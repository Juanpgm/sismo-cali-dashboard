"""GET /sticker-status — read-only sticker-coverage lookup for the Panel +
Evaluaciones views (design.md ADR-4; backend-platform spec
"Any-authenticated role-wide route accepts every valid role", "sticker-status
cache hit within TTL").

Ports `api/sticker-status.js`'s Firestore read (`sticker_matches` collection
tally: `con_sticker`/`con`/`total`) with ONE deliberate fix to the legacy
caching: `api/sticker-status.js` held its 5-minute cache in a bare
module-level variable that only behaved like a shared cache when Vercel
happened to reuse a warm Lambda instance between invocations — a cold start,
or two concurrent cold invocations, got no caching guarantee at all. This
backend is one always-on process (design.md ADR-1, proposal answer 8), so
the cache below lives on `app.state` for the process's whole lifetime: the
guarantee the legacy code only had by accident on a warm Lambda now actually
holds, always.

Unlike `sticker-asignaciones`/`inspector-asignaciones` this is READ-ONLY and
open to ANY authenticated role, not admin-only (backend-platform spec table:
"`/sticker-status` | GET | Bearer, any authenticated role").
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.deps import require_auth
from app.credentials import clients as credentials
from app.services import blob_lkg

# sismo() is already unconditionally in credentials.WEB_STARTUP_CLIENTS, but
# this router still declares it per ADR-4's declaration mechanism — the
# invariant a router only reaches clients it names.
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

CACHE_TTL_SECONDS = 5 * 60
STICKER_STATUS_LKG_BLOB = "data/sticker_status_last_good.json"  # last-known-good payload, see services/blob_lkg.py


class StickerStatusCache:
    """Process-lifetime TTL cache — see module docstring for why this
    replaces the legacy module-level variable's warm-lambda-only guarantee.
    One instance per `create_app()` call, attached to `app.state` (matches
    `app/services/snapshot.py`'s `ReportadosSnapshot` convention), so tests
    get a fresh cache per app instance instead of leaking state across
    tests via a module-level global.

    Each successful fetch is also persisted to Vercel Blob (fire-and-forget,
    hash-gated) so a COLD start during a sustained Firestore outage — every
    Railway deploy restarts the process, wiping the in-process payload — can
    fall back to the last-known-good copy instead of a 502 (31-ago-2026, see
    `app.services.blob_lkg`)."""

    def __init__(self) -> None:
        self._at: float | None = None
        self._payload: dict[str, Any] | None = None
        self._blob_hash: str | None = None  # hash of the last payload persisted to Blob
        self._persist_thread: threading.Thread | None = None  # last persist worker (tests join it)

    def get_or_fetch(self, fetch: Any) -> dict[str, Any]:
        now = time.monotonic()
        stale = self._payload is None or self._at is None or (now - self._at) > CACHE_TTL_SECONDS
        if stale:
            try:
                self._payload = fetch()
                self._at = now
                self._persist_last_good()
            except Exception:
                # Serve-stale-on-error (30-ago-2026): a Firestore 429/
                # ResourceExhausted degrading this route to a raw 502 is
                # worse than showing slightly-old coverage numbers — the
                # dashboard has NOTHING to show otherwise.
                if self._payload is None:
                    # Cold start (fresh process) during the outage: try the
                    # Blob-persisted last-known-good copy before giving up.
                    # `load_json` returns None on ANY failure — including a
                    # malformed/wrong-shaped payload — so only a validated
                    # dict is ever served. Re-raise only when Firestore
                    # failed AND Blob has nothing usable.
                    restored = blob_lkg.load_json(STICKER_STATUS_LKG_BLOB, dict)
                    if restored is None:
                        raise
                    logging.exception(
                        "sticker-status: fetch fallo sin payload previo, sirviendo el ultimo bueno desde Blob"
                    )
                    self._payload = restored
                    self._at = now  # behaves as a normal (stale-able) payload from here on
                else:
                    logging.exception(
                        "sticker-status: fetch fallo, sirviendo el ultimo payload en cache (stale)"
                    )
        assert self._payload is not None
        return self._payload

    def _persist_last_good(self) -> None:
        """Blob write of the fresh payload, gated by content hash so an
        unchanged payload doesn't re-upload every TTL window — same
        hash-gate idea as `dashboard_refresh._write_contactos`. The actual
        PUT (a synchronous urllib call) runs on a daemon thread, OFF the
        request path; the hash only advances inside the thread on a
        confirmed upload, so a failed write is retried on the next fetch.

        Public Blob exposure ceiling: this payload is only registro ids +
        counts, equivalent to the already-public web/data — do NOT add
        fields to this payload without re-reviewing the exposure boundary
        (the store is public; see stickers.py's `_redact_for_blob`
        allowlist for the precedent)."""
        payload = self._payload
        h = blob_lkg.payload_hash(payload)
        if h == self._blob_hash:
            return

        def _upload() -> None:
            if blob_lkg.save_json(STICKER_STATUS_LKG_BLOB, payload):
                self._blob_hash = h

        # ponytail: no locking — two overlapping refreshes may double-upload
        # the same pathname; harmless (idempotent overwrite, hash gate
        # converges). Add a lock only if duplicate PUTs ever matter.
        self._persist_thread = threading.Thread(target=_upload, daemon=True)
        self._persist_thread.start()


def _read_coverage(db: Any) -> dict[str, Any]:
    docs = db.collection("sticker_matches").get()
    con_sticker: list[str] = []
    total = 0
    for doc in docs:
        data = doc.to_dict() or {}
        rid = data.get("registro_id")
        if rid is None:
            continue
        total += 1
        if data.get("tiene_sticker") is True:
            con_sticker.append(str(rid))
    return {"con_sticker": con_sticker, "total": total, "con": len(con_sticker)}


router = APIRouter()


@router.get("/sticker-status")
def get_sticker_status(
    request: Request,
    claims: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    cache: StickerStatusCache = request.app.state.sticker_status_cache
    try:
        payload = cache.get_or_fetch(lambda: _read_coverage(credentials.sismo().firestore))
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - fail-open surface, mirrors
        # planeacion_asignaciones.py's own catch-all: an uncaught Firestore
        # exception here (e.g. a 429 quota error) was previously reaching
        # Starlette's default error handler as a bare 500 with NO CORS
        # headers attached, which the browser then reports as a misleading
        # "blocked by CORS policy" / "Failed to fetch" instead of the real
        # cause. A normal HTTPException always carries CORS headers.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"ok": True, **payload})
