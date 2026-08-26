"""In-process reportados snapshot cache (design.md ADR-5).

Started from FastAPI's `lifespan` (`app/main.py`): an asyncio background
task repeatedly calls `atencionsismo.fetch_reportados()`, stores
`{payload, fetched_at}` in process memory, sleeps `REFRESH_INTERVAL_S`
(900s — parity with the retired CDN `s-maxage=900`). `web/js/data.js`
calls `/reportados` fire-and-forget with no fallback, so the route NEVER
does an inline ~150s day-walk inside a request — it only ever serves from
this in-memory snapshot (ADR-5's explicit rejection of "refresh inside the
request").

Cold start: before the first live refresh completes, best-effort seed from
the cron's Blob-published `reportes.json` (`REPORTES_BLOB_URL` — same
"full public URL as a plain env var" pattern `INSPECTIONS_URL` already
uses for `cruce_sticker`/`cruce_criticos_survey`; the Railway image has no
`web/data/` filesystem to read locally). Reuses `atencionsismo.summarize()`
so the seeded shape is identical to a live refresh's. If the seed also
fails/is unset, `/reportados` responds 503 + `Retry-After: 60` until the
first live refresh completes.

Design interpretation (flagged for verify): the seeded snapshot's age is
measured from the MOMENT this process downloaded the Blob file (like a
live refresh), not from the underlying data's `reportes_meta.json`
`generated_at` timestamp. Deriving the latter would need a second Blob
fetch (`REPORTES_META_BLOB_URL`) for a value none of the three spec
scenarios in backend-platform's "In-Process Caching..." requirement
actually need (they need `X-Snapshot-Age` PRESENT and the 86400s hard
bound enforced — not that the seeded age reflect the cron's original
publish time). Simpler and still fully spec-compliant; open to revisiting
if verify disagrees.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

import httpx

from app.services import atencionsismo

log = logging.getLogger(__name__)

REFRESH_INTERVAL_S = 900  # parity with the retired CDN s-maxage=900
STALE_AFTER_S = 86_400  # outer bound the CDN had (stale-while-revalidate)
RETRY_AFTER_S = 60

# Full public Blob URL, plain env var — same pattern as INSPECTIONS_URL
# (integracion_F1/cruce_sticker.py, cruce_criticos_survey.py). Points at
# deploy/refresh.sh's published `data/reportes.json` (raw stripped
# records — see scripts/fetch_reportes_api.py's strip_report()), NOT
# `reportes_agg.json` (which lacks the `inmuebles` coord-dedup count).
BLOB_URL_ENV = "REPORTES_BLOB_URL"
BLOB_TIMEOUT_S = 30.0


class SnapshotUnavailableError(RuntimeError):
    """No snapshot exists yet: no completed live refresh AND no successful
    Blob seed. Router maps this to 503 + Retry-After."""

    def __init__(self, message: str, *, retry_after: int = RETRY_AFTER_S) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SnapshotStaleError(RuntimeError):
    """The only available snapshot exceeds the 86400s outer staleness
    bound. Router maps this to 503 + Retry-After."""

    def __init__(self, message: str, *, retry_after: int = RETRY_AFTER_S) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class _Entry:
    payload: dict
    fetched_at: float  # time.monotonic() — immune to wall-clock adjustments


class ReportadosSnapshot:
    """Process-wide in-memory snapshot store. One instance lives on
    `app.state.reportados_snapshot`: the background loop (and the
    cold-start seed) write to it, the router reads from it. No locking —
    asyncio is single-threaded and a store() is a single attribute
    assignment, so readers never observe a partially-written entry."""

    def __init__(self) -> None:
        self._entry: _Entry | None = None

    def store(self, payload: dict, *, fetched_at: float | None = None) -> None:
        self._entry = _Entry(
            payload=payload,
            fetched_at=fetched_at if fetched_at is not None else time.monotonic(),
        )

    def get(self) -> tuple[dict, float]:
        """Return `(payload, age_seconds)`.

        Raises `SnapshotUnavailableError` if nothing has been seeded/
        refreshed yet, or `SnapshotStaleError` if the only entry exceeds
        `STALE_AFTER_S` (design.md ADR-5's hard outer bound)."""
        if self._entry is None:
            raise SnapshotUnavailableError("no reportados snapshot available yet")
        age = time.monotonic() - self._entry.fetched_at
        if age > STALE_AFTER_S:
            raise SnapshotStaleError(f"snapshot is {age:.0f}s old, exceeds {STALE_AFTER_S}s bound")
        return self._entry.payload, age

    @property
    def has_entry(self) -> bool:
        return self._entry is not None


async def seed_from_blob(
    snapshot: ReportadosSnapshot, *, client: httpx.AsyncClient | None = None
) -> bool:
    """Best-effort cold-start seed from the cron's Blob-published
    `reportes.json`. Returns True iff `snapshot` now has an entry; returns
    False (NEVER raises) on any failure — unset env var, network error,
    non-2xx, non-JSON, non-list JSON, or zero usable records — so callers
    can always fall through to the 503 path."""
    url = os.environ.get(BLOB_URL_ENV, "").strip()
    if not url:
        return False

    owns_client = client is None
    active_client = client or httpx.AsyncClient()
    try:
        resp = await active_client.get(url, timeout=BLOB_TIMEOUT_S)
        resp.raise_for_status()
        records = resp.json()
        if not isinstance(records, list):
            return False
        counts = atencionsismo.summarize(records)
        if counts["total"] == 0:
            return False
        snapshot.store(
            {
                "ok": True,
                "generado": atencionsismo.now_iso(),
                "fuente": "blob-seed",
                **counts,
            }
        )
        return True
    except (httpx.HTTPError, ValueError):
        return False
    finally:
        if owns_client:
            await active_client.aclose()


async def refresh_loop(
    snapshot: ReportadosSnapshot,
    *,
    stop_event: asyncio.Event | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """The lifespan-owned background task: refresh -> store -> sleep
    `REFRESH_INTERVAL_S`, forever. A refresh failure (missing credentials,
    API down, empty result) is logged and NEVER kills the loop — the next
    cycle tries again. `stop_event`/`transport` are test-only seams: a set
    `stop_event` makes the loop run exactly one iteration then return
    instead of sleeping/looping forever; `transport` lets tests inject an
    `httpx.MockTransport` without any real network access."""
    async with httpx.AsyncClient(transport=transport) as client:
        while True:
            try:
                payload = await atencionsismo.fetch_reportados(client)
                snapshot.store(payload)
            except Exception:  # noqa: BLE001 — a bad refresh must not kill the loop
                log.warning("reportados background refresh failed", exc_info=True)

            if stop_event is not None and stop_event.is_set():
                return

            if stop_event is not None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=REFRESH_INTERVAL_S)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(REFRESH_INTERVAL_S)
