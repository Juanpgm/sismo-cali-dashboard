"""POST /refresh — admin-triggered manual data refresh.

Runs `app.jobs.dashboard_refresh.run_refresh()` IN-PROCESS as a background
task, instead of the old approach this router used to port from
`api/refresh.js` (a Railway `serviceInstanceRedeploy` of the
`dashboard-refresh` cron container). The pipeline code already lives in
THIS image (`app/jobs/dashboard_refresh.py`, absorbed per design.md ADR-6 /
task 7.1-7.9), so re-running it here needs no Railway round-trip and no
dependency on that cron service's OWN deploy config being healthy — which
has twice broken `dashboard-refresh`'s SCHEDULED runs in production (a
half-finished Railway git-cutover repeatedly overwriting a manual fix with
a build that can't find `app.jobs.dashboard_refresh`, 2026-08-27). The
manual-trigger path no longer shares that failure mode.

A module-level lock keeps two overlapping runs from racing on the same
`web/data/` files: a second click while one is in flight gets 409 — exactly
what the frontend (`main.js`'s `triggerRefresh()`) already treats as
"already running, keep polling the published data" (that branch existed
before this change but the old endpoint never actually returned 409; it
does now, no frontend change needed for this part).

This endpoint used to ALSO best-effort-redeploy the `cruce-sticker`/
`cruce-gestion` Railway cron adjuncts on every click. That machinery is
gone (2026-08-31): both services were deleted from Railway (decision:
cross-reference jobs run on-demand only, in-process — see
`routers/cruce_sticker.py`), so the redeploys could only ever log errors.

Frontend wiring: `web/js/api-config.js`'s `refresh` entry points here
(`${RAILWAY_BASE_URL}/refresh`) per the per-endpoint parity-flip pattern
(design.md ADR-7) every other consolidated route already used. The legacy
`api/refresh.js` Vercel function is left in place, untouched, as the
one-line rollback (flip `api-config.js` back to `/api/refresh`) — same
convention ADR-7 documents for `reportados`/`sticker-status`/etc.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.auth.deps import require_role
from app.jobs.dashboard_refresh import run_refresh

router = APIRouter()

# Non-blocking check-and-set: acquired in the request handler (so two rapid
# clicks can't both slip past the 409 guard before either background task
# starts), released by the background task once run_refresh() returns.
_refresh_lock = threading.Lock()


def _run_refresh_and_release() -> None:
    """Background-task body. Starlette runs sync `BackgroundTasks.add_task`
    callables in a threadpool (`run_in_threadpool`), so `run_refresh()`'s
    blocking subprocess/network calls never block this service's event loop
    — other requests (map data, KPIs, other routes) keep being served while
    a refresh runs. `run_refresh()` already reports its own outcome to Blob
    (`_status.json`, its own `finally`); the try/except here is only a
    last-resort net so a bug in that reporting itself can't leak silently
    into an unreleased lock."""
    try:
        run_refresh()
    except Exception:  # noqa: BLE001 - see docstring
        traceback.print_exc()
    finally:
        _refresh_lock.release()


@router.post("/refresh", status_code=202)
async def trigger_refresh(
    background_tasks: BackgroundTasks,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    if not _refresh_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una actualización en curso.")
    background_tasks.add_task(_run_refresh_and_release)
    return {"ok": True, "errors": {}}
