"""Generic TTL + content-version + single-flight cache (design D22).

One instance per Firestore-derived input of the depuración path (roster,
survey names, evaluaciones). Same behaviours as `routers/stickers.py`'s
`InspectoresCache`/`EvaluacionesCache` — own lock, serve-stale on error,
`invalidate()` — plus a content `version` so an expired TTL alone never looks
like a change downstream:

- `get(fetch)` returns a `Versioned(value, version)`. `version` is
  `blob_lkg.payload_hash` of the fetched content; a refetch with identical
  content keeps the PREVIOUS `Versioned` object (same value identity, same
  version).
- The fast path (fresh) takes no lock. A stale caller takes the cache's OWN
  lock, re-checks, and fetches once; the callers queued behind it reuse that
  result (single-flight). Locks are per instance: a slow fetch of one
  component never blocks another component's fast path.
- A failed refresh serves the last-good value and arms a backoff
  (`failure_backoff_s`, default one TTL, so at most one retry per TTL). A cold
  start with nothing to serve propagates the error and retries next call.
- `invalidate()` marks the value stale (keeping it as last-good) and lifts any
  armed backoff. An `invalidate()` that lands while a fetch is in flight is
  honoured: the in-flight result is kept but not trusted as fresh, and if that
  refresh then FAILS it cannot re-arm the backoff. `invalidate()` is atomic
  (own state lock) and never waits for an in-flight fetch.
- The clock is injectable; None resolves `time.monotonic` at CALL time so a
  test that patches it globally still applies. A negative age (clock moved
  backwards) counts as stale: one safe refetch, never an unbounded stale
  window.

Logs carry the component name and the exception TYPE only — upstream messages
can echo PII (cédulas, names) and never reach the log.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from app.services import blob_lkg

T = TypeVar("T")


@dataclass(frozen=True)
class Versioned(Generic[T]):
    value: T
    version: str


class VersionedCache(Generic[T]):
    """Two locks, on purpose:

    - `_fetch_lock` serializes REFRESHES (single-flight) and is held across the
      upstream call. Nothing else ever waits on it, and `invalidate()` never
      takes it: an admin write must not wait behind a slow Firestore scan.
    - `_state_lock` guards the published state and is held only for O(1)
      bookkeeping, never across `fetch()`.

    The published `(value, timestamp)` pair is ONE tuple replaced atomically, so
    the lock-free fast path can never pair one refresh's value with another's
    timestamp. `_generation` bumps on every `invalidate()`; a refresh that began
    under an older generation may return what it read to its own caller but can
    neither re-arm the failure backoff nor mark its value fresh.
    """

    def __init__(
        self,
        *,
        name: str,
        ttl_s: float,
        failure_backoff_s: float | None = None,
        clock: Callable[[], float] | None = None,
        version_of: Callable[[Any], str] = blob_lkg.payload_hash,
    ) -> None:
        self._name = name
        self._ttl_s = ttl_s
        self._failure_backoff_s = ttl_s if failure_backoff_s is None else failure_backoff_s
        self._clock = clock
        self._version_of = version_of
        self._fetch_lock = threading.Lock()
        self._state_lock = threading.Lock()
        # (last-good value, monotonic time it was read; None = not trusted as fresh)
        self._published: tuple[Versioned[T] | None, float | None] = (None, None)
        self._failed_at: float | None = None
        self._generation = 0

    def _now(self) -> float:
        return (self._clock or time.monotonic)()

    def _is_stale(self, cur: Versioned[T] | None, at: float | None, now: float) -> bool:
        if cur is None or at is None:
            return True
        age = now - at
        return age < 0 or age > self._ttl_s

    @property
    def current(self) -> Versioned[T] | None:
        """Last-good value without any freshness check (None before the first fetch)."""
        return self._published[0]

    def get(self, fetch: Callable[[], T]) -> Versioned[T]:
        cur, at = self._published  # one atomic read: value and timestamp of the SAME refresh
        if cur is not None and not self._is_stale(cur, at, self._now()):
            return cur

        with self._fetch_lock:
            now = self._now()
            with self._state_lock:
                cur, at = self._published
                if cur is not None and not self._is_stale(cur, at, now):
                    return cur  # a queued caller reuses the leader's result

                failed_at = self._failed_at
                if cur is not None and failed_at is not None and 0 <= now - failed_at < self._failure_backoff_s:
                    return cur  # a recent refresh failed: no hot retry

                generation = self._generation

            try:
                value = fetch()
                version = self._version_of(value)
            except Exception as exc:  # noqa: BLE001 - serve-stale contract
                with self._state_lock:
                    cur = self._published[0]
                    if cur is not None and generation == self._generation:
                        self._failed_at = now
                    # else: cold start (nothing to serve, re-raised below and retried on
                    # the next call) or an invalidate() landed mid-refresh, which already
                    # lifted the backoff and must not be undone by this older attempt.
                if cur is None:
                    raise
                logging.warning(
                    "%s: refresh failed (%s); serving the last-good value", self._name, type(exc).__name__
                )
                return cur

            with self._state_lock:
                cur = self._published[0]
                # Equal content keeps the previous object: no churn for consumers.
                if cur is None or cur.version != version:
                    cur = Versioned(value, version)
                if generation == self._generation:
                    self._published = (cur, now)
                    self._failed_at = None
                else:
                    # An invalidate() raced this fetch (it may predate the admin
                    # write): kept as last-good, never trusted as fresh.
                    self._published = (cur, None)
                return cur

    def invalidate(self) -> None:
        """Marks the value stale (kept as last-good) and lifts any armed
        backoff. Never waits for an in-flight fetch."""
        with self._state_lock:
            self._generation += 1
            self._published = (self._published[0], None)
            self._failed_at = None
