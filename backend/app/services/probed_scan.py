"""Probe-gated collection scan (design D34).

A component that mirrors a whole Firestore collection (survey names, the
evaluaciones list) used to pay a FULL scan every time its TTL expired. Firestore
bills per document, so that is ~1.9k / ~1.5k reads for a value that almost never
changes. `ProbedScan.fetch` replaces the timed scan by a cheap probe:

    count()                         ~ceil(n / 1000) reads (2 at ~1.9k documents)
    newest by `order_field`, DESC   1 read (skipped when the count is 0)

If `(count, newest doc id, newest timestamp, extra)` is unchanged since the last
full scan, the previously scanned value is returned (the SAME object, so the
`VersionedCache` above keeps its version). Otherwise the scan runs once. One
forced full reconcile every `reconcile_s` (default 6 h) bounds the blind spot.

Blind spots, stated honestly:
- an in-place edit that does not bump `order_field` (a console edit, a script
  that bypasses the sole writer) leaves `(count, newest)` unchanged; it is seen
  by the forced reconcile, at most `reconcile_s` later;
- documents that lack `order_field` are invisible to the newest query (Firestore
  drops them from an ordered query) but still move the count;
- deletions change the count; a delete + create pair changes the newest.

Failure contract: the probe is the only thing a warm refresh does, so ONE
transient probe failure PROPAGATES and the calling cache serves its last-good
value (a cheap, short-lived staleness). But a probe that cannot run must never
silently lengthen the staleness window: a caller that swallows the error and
keeps the old payload (`EvaluacionesCache`) would otherwise freeze the body until
the forced reconcile with no ceiling firing. So after `max_probe_failures`
CONSECUTIVE warm-path probe failures (default 2), or when the time since the
last SUCCESSFUL probe or scan reaches `probe_failure_grace_s` (default: the
component TTL of the source), the warm path FALLS THROUGH to the full scan (the
source of truth) instead of re-raising. If that scan fails too, its error
propagates as before (serve-stale paths and ceilings apply) and the next refresh
tries again. A probe success or a successful scan resets the counter. A COLD
start, or a due reconcile, never depends on the probe: the probe is best-effort
there and the scan proceeds (the signature is taken BEFORE the scan, so a write
landing in between only costs one extra scan later). Logs carry the component
name and the exception TYPE only; the fall-through is logged once per grace
window.

Concurrency: `fetch` is meant to run inside the `VersionedCache` single-flight
refresh (one caller at a time); the source keeps no lock of its own.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")

# Forced full reconcile: longest an edit that does not bump `order_field` can go
# unseen. Named and injectable (`ProbedScan(reconcile_s=...)`).
DEFAULT_RECONCILE_S = 6 * 60 * 60.0

# Warm-path probe failure policy (see the module docstring): consecutive failures
# tolerated before the scan runs anyway, and the longest time without a success
# (probe or scan) a failing probe may hide. Named and injectable; the production
# sources pass their component TTL as the grace.
DEFAULT_MAX_PROBE_FAILURES = 2
DEFAULT_PROBE_FAILURE_GRACE_S = 15 * 60.0

Signature = tuple[int, str | None, str | None, str]


class ProbeError(RuntimeError):
    """The probe answered something unusable (no count row, a non-numeric count)."""


class ProbedScan(Generic[T]):
    def __init__(
        self,
        *,
        name: str,
        collection: str,
        order_field: str,
        reconcile_s: float = DEFAULT_RECONCILE_S,
        max_probe_failures: int = DEFAULT_MAX_PROBE_FAILURES,
        probe_failure_grace_s: float = DEFAULT_PROBE_FAILURE_GRACE_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_probe_failures < 1:
            raise ValueError("max_probe_failures must be >= 1")
        if probe_failure_grace_s < 0:
            raise ValueError("probe_failure_grace_s must be >= 0")
        self._name = name
        self._collection = collection
        self._order_field = order_field
        self._reconcile_s = reconcile_s
        self._max_probe_failures = max_probe_failures
        self._probe_failure_grace_s = probe_failure_grace_s
        self._clock = clock
        self._value: T | None = None
        self._has_value = False
        self._signature: Signature | None = None
        self._scanned_at: float | None = None
        self._probe_failures = 0  # consecutive warm-path probe failures since the last success or scan
        self._last_ok_at: float | None = None  # last SUCCESSFUL probe or scan
        self._fallback_logged_at: float | None = None

    def _now(self) -> float:
        return (self._clock or time.monotonic)()

    def _probe(self, db: Any, extra: str) -> Signature:
        from google.cloud import firestore  # deferred, `credentials/clients.py`'s convention

        col = db.collection(self._collection)
        count: int | None = None
        for row in col.count().get():
            for result in row:
                count = int(result.value)
                break
            if count is not None:
                break
        if count is None or count < 0:
            raise ProbeError(f"{self._name}: count() returned no usable result")
        if count == 0:
            return (0, None, None, extra)
        newest_id: str | None = None
        newest_at: str | None = None
        for snap in col.order_by(self._order_field, direction=firestore.Query.DESCENDING).limit(1).get():
            stamp = (snap.to_dict() or {}).get(self._order_field)
            newest_id = str(snap.id)
            newest_at = stamp.isoformat() if hasattr(stamp, "isoformat") else (None if stamp is None else str(stamp))
        return (count, newest_id, newest_at, extra)

    def _scan(self, scan: Callable[[], T], signature: Signature | None, now: float) -> T:
        value = scan()  # raises -> the previous value/signature/anchor stay as they were
        self._value, self._has_value = value, True
        self._signature, self._scanned_at = signature, now
        self._probe_failures, self._last_ok_at = 0, now
        return value

    def _probe_ok(self, now: float) -> None:
        self._probe_failures, self._last_ok_at = 0, now

    def _must_scan_despite_probe_failure(self, now: float) -> bool:
        """True when a failing warm-path probe may no longer stand in for the scan."""
        if self._probe_failures >= self._max_probe_failures:
            return True
        if self._last_ok_at is None:
            return True
        since = now - self._last_ok_at
        # A negative interval (the clock moved backwards) cannot be measured: scan.
        return since < 0 or since >= self._probe_failure_grace_s

    def _log_fallback(self, exc: Exception, now: float) -> None:
        last = self._fallback_logged_at
        if last is not None and 0 <= now - last < self._probe_failure_grace_s:
            return  # once per grace window: a dead probe must not flood the log
        self._fallback_logged_at = now
        logging.warning(
            "%s: probe failing (%s); falling back to the full scan", self._name, type(exc).__name__
        )

    def fetch(self, db: Any, scan: Callable[[], T], *, extra: str = "") -> T:
        now = self._now()
        age = None if self._scanned_at is None else now - self._scanned_at
        # A negative age (the clock moved backwards) cannot be measured: one safe
        # rescan re-anchors the interval, never a rescan loop.
        due = not self._has_value or age is None or age < 0 or age >= self._reconcile_s
        if due:
            try:
                signature: Signature | None = self._probe(db, extra)
                self._probe_ok(now)
            except Exception as exc:  # noqa: BLE001 - best effort: the scan does not need the probe
                logging.warning("%s: probe failed before a full scan (%s)", self._name, type(exc).__name__)
                signature = None
            return self._scan(scan, signature, now)

        try:
            signature = self._probe(db, extra)
        except Exception as exc:  # noqa: BLE001 - the policy below decides: serve stale or scan
            self._probe_failures += 1
            if not self._must_scan_despite_probe_failure(now):
                raise  # a transient failure: the caller serves its last-good value
            self._log_fallback(exc, now)
            # The signature is unknown (None): the first working probe after this costs
            # one more scan, exactly like a cold start with a dead probe.
            return self._scan(scan, None, now)
        self._probe_ok(now)
        if signature == self._signature:
            return self._value  # type: ignore[return-value]  # `_has_value` is true on the non-due path
        return self._scan(scan, signature, now)
