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

- A follower never waits forever behind a hung fetch: the wait on the fetch lock
  is bounded (`fetch_wait_s`, REAL seconds, default `DEFAULT_FETCH_WAIT_S`). On
  expiry it serves last-good, or raises `FetchWaitTimeout` when there is none.
- Last-good is not served forever: past `max_stale_s` since the last SUCCESSFUL
  fetch (default `DEFAULT_MAX_STALE_S`) a refresh that fails, is in backoff or
  hangs raises `StaleBeyondCeiling` instead of silently serving old data (one
  warning per TTL, exception type only). Both errors are `RuntimeError`, which
  the routes already map to 503 / an omitted advisory block.

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

# Same figure as `stickers_atencionsismo._FLIGHT_WAIT_S`: a sync route parks one
# Starlette worker (40 by default) for the whole wait, so it must stay far below
# what would exhaust the pool yet far above a normal Firestore scan.
DEFAULT_FETCH_WAIT_S = 10.0
# Longest a failing refresh may keep serving the last-good value.
DEFAULT_MAX_STALE_S = 6 * 60 * 60.0


class FetchWaitTimeout(RuntimeError):
    """A follower's bounded wait on another caller's fetch expired and there is
    no last-good value to serve. Carries the component name only."""


class StaleBeyondCeiling(RuntimeError):
    """The last-good value is older than `max_stale_s` and the refresh cannot
    replace it. Carries the component name and the TYPE of the failed refresh,
    never its message, and does not chain the upstream exception."""


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
        fetch_wait_s: float = DEFAULT_FETCH_WAIT_S,
        max_stale_s: float = DEFAULT_MAX_STALE_S,
    ) -> None:
        self._name = name
        self._ttl_s = ttl_s
        self._failure_backoff_s = ttl_s if failure_backoff_s is None else failure_backoff_s
        self._clock = clock
        self._version_of = version_of
        self._fetch_wait_s = fetch_wait_s
        self._max_stale_s = max_stale_s
        self._fetch_lock = threading.Lock()
        self._state_lock = threading.Lock()
        # (last-good value, monotonic time it was read; None = not trusted as fresh)
        self._published: tuple[Versioned[T] | None, float | None] = (None, None)
        self._good_at: float | None = None  # last SUCCESSFUL fetch; survives invalidate()
        self._ceiling_logged_at: float | None = None
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

    def _last_good(self, cur: Versioned[T], now: float, exc: BaseException | None = None) -> Versioned[T]:
        """`cur` if it is still inside the max-stale ceiling, else raise
        `StaleBeyondCeiling` (logged once per TTL, type only). Age is measured
        from the last successful fetch, so `invalidate()` cannot hide it; a
        backwards clock (negative age) is never "too old"."""
        with self._state_lock:
            good_at = self._good_at
            if good_at is None or not now - good_at > self._max_stale_s:
                return cur
            log = self._ceiling_logged_at is None or not 0 <= now - self._ceiling_logged_at < self._ttl_s
            if log:
                self._ceiling_logged_at = now
        if log:
            logging.warning(
                "%s: last-good value is beyond the max-stale ceiling and the refresh cannot replace it (%s)",
                self._name, type(exc).__name__ if exc is not None else "no refresh",
            )
        # `from None`, on purpose: a chained upstream exception is printed by
        # `logging.exception` in the callers and its message can echo PII. The
        # TYPE is kept in the message, like the warning above.
        cause = type(exc).__name__ if exc is not None else "no refresh"
        raise StaleBeyondCeiling(
            f"{self._name}: last-good value is beyond the max-stale ceiling ({cause})"
        ) from None

    def _fetch_wait_expired(self) -> Versioned[T]:
        """The bounded wait on the fetch lock ran out: another caller's fetch is hung."""
        cur = self._published[0]
        if cur is None:
            raise FetchWaitTimeout(f"{self._name}: fetch still running and nothing to serve")
        logging.warning("%s: fetch still running; serving the last-good value", self._name)
        return self._last_good(cur, self._now())

    def get(self, fetch: Callable[[], T]) -> Versioned[T]:
        cur, at = self._published  # one atomic read: value and timestamp of the SAME refresh
        if cur is not None and not self._is_stale(cur, at, self._now()):
            return cur

        if not self._fetch_lock.acquire(timeout=self._fetch_wait_s):
            return self._fetch_wait_expired()
        try:
            return self._refresh(fetch)
        finally:
            self._fetch_lock.release()

    def _refresh(self, fetch: Callable[[], T]) -> Versioned[T]:
        """Runs under `_fetch_lock` (single-flight)."""
        now = self._now()
        with self._state_lock:
            cur, at = self._published
            if cur is not None and not self._is_stale(cur, at, now):
                return cur  # a queued caller reuses the leader's result

            failed_at = self._failed_at
            in_backoff = cur is not None and failed_at is not None and 0 <= now - failed_at < self._failure_backoff_s
            generation = self._generation

        if in_backoff:
            assert cur is not None
            return self._last_good(cur, now)  # a recent refresh failed: no hot retry

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
            served = self._last_good(cur, now, exc)  # raises past the max-stale ceiling
            logging.warning(
                "%s: refresh failed (%s); serving the last-good value", self._name, type(exc).__name__
            )
            return served

        with self._state_lock:
            cur = self._published[0]
            # Equal content keeps the previous object: no churn for consumers.
            if cur is None or cur.version != version:
                cur = Versioned(value, version)
            self._good_at = now
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
