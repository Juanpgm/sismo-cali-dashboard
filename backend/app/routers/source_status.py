"""GET /source-status — admin-only, live connectivity check for the
atencionsismo API (design.md ADR-4; backend-platform spec "Admin-gated
route rejects non-admin" (`/source-status` row)).

Ports `api/source-status.js` verbatim. Backs the "Analista" tab's
atencionsismo status row: a snapshot (`reportes_meta.json`) proves the
pipeline ran at some point in the past, not that the API is reachable NOW —
this route answers that second question by re-running the SAME cheap
one-minute probe `app/services/atencionsismo.py`'s day-walk already runs
before its full range fetch (`atencionsismo.probe_api`, extracted in slice
3).

Deliberately never a 5xx for an upstream failure: a down/misconfigured
atencionsismo API (or a missing `VISITADOS_API_PASS`) is a successfully-
determined FACT (`ok: false`), not a backend error — the legacy handler
always answers `200` on both branches (`api/source-status.js:67,70`), which
this route preserves exactly.

No `Cache-Control` header value is invented: the legacy handler explicitly
sets `private, no-store` on both branches (`api/source-status.js:66,69`),
carried over verbatim below. (NOTE for verify: this is UNLIKE
`api/sticker-status.js`, which sets no `Cache-Control` header at all —
confirmed by reading both files; do not conflate the two.)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.deps import require_role
from app.services import atencionsismo

# Never touches Firestore/S3 — VISITADOS_API_PASS is a "plain secret" read
# directly by app/services/atencionsismo.py (design.md ADR-4 table), not
# part of the named-client union.
REQUIRED_CLIENTS: tuple[str, ...] = ()

# Verbatim from api/source-status.js:66,69.
CACHE_CONTROL = "private, no-store"

router = APIRouter()


def _checked_at() -> str:
    """`new Date().toISOString()` shape (millisecond precision, trailing
    `Z`) — Python's default `isoformat()` uses microseconds and a `+00:00`
    offset instead, so this is formatted by hand to match the legacy
    payload byte-for-byte."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


async def _probe() -> None:
    user, password = atencionsismo.credentials_from_env()
    async with httpx.AsyncClient() as client:
        await atencionsismo.probe_api(client, user, password)


@router.get("/source-status")
async def get_source_status(
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    checked_at = _checked_at()
    try:
        await _probe()
        return JSONResponse(
            {"ok": True, "status": "conectado", "detail": None, "checked_at": checked_at},
            headers={"Cache-Control": CACHE_CONTROL},
        )
    except (
        atencionsismo.ApiCredentialsError,
        atencionsismo.ApiUnavailableError,
        httpx.HTTPError,
    ) as exc:
        return JSONResponse(
            {
                "ok": False,
                "status": "con errores",
                "detail": str(exc),
                "checked_at": checked_at,
            },
            headers={"Cache-Control": CACHE_CONTROL},
        )
