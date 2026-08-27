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

`cruce-sticker`/`cruce-gestion` are NOT absorbed into this backend yet (see
`app/jobs/dashboard_refresh.py`'s own docstring) — they're still triggered
the old Railway-redeploy way, best-effort and fail-soft: a hiccup there is
surfaced in `errors` but never blocks the 202, because the in-process
primary run is the thing the "Actualizar datos" button is actually waiting
on (it polls `meta.json`, which only the primary run publishes).

Frontend wiring: `web/js/api-config.js`'s `refresh` entry points here
(`${RAILWAY_BASE_URL}/refresh`) per the per-endpoint parity-flip pattern
(design.md ADR-7) every other consolidated route already used. The legacy
`api/refresh.js` Vercel function is left in place, untouched, as the
one-line rollback (flip `api-config.js` back to `/api/refresh`) — same
convention ADR-7 documents for `reportados`/`sticker-status`/etc.
"""
from __future__ import annotations

import os
import threading
import traceback
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.auth.deps import require_role
from app.jobs.dashboard_refresh import run_refresh

RAILWAY_API = "https://backboard.railway.com/graphql/v2"

# The two NOT-YET-absorbed 15-min cron adjuncts in the `normalizador-sismo-
# cali` Railway project (confirmed live 2026-08-26). `dashboard-refresh`
# itself is no longer redeployed here — it runs in-process (see module
# docstring).
DEFAULT_STICKER_SERVICE_ID = "b18c74c8-0b7a-459c-ada5-5e5df6db8050"  # cruce-sticker
DEFAULT_CRUCE_SERVICE_ID = "b4c8fd15-aa3b-4157-b787-2034c89a108b"    # cruce-gestion
DEFAULT_ENVIRONMENT_ID = "4418f451-bd97-4d96-ba6e-b5ecbbd49c9b"

REDEPLOY_MUTATION = """
mutation($s: String!, $e: String!) {
  serviceInstanceRedeploy(serviceId: $s, environmentId: $e)
}
"""

router = APIRouter()

# Non-blocking check-and-set: acquired in the request handler (so two rapid
# clicks can't both slip past the 409 guard before either background task
# starts), released by the background task once run_refresh() returns.
_refresh_lock = threading.Lock()


async def _railway_graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Dual-header auth fallback, ported verbatim from `api/refresh.js`'s
    `railway()` helper: Railway authenticates account/team tokens via
    `Authorization: Bearer` and project tokens via the `Project-Access-Token`
    header. Try Bearer first, fall back to the project header so either
    token type works transparently — same convention
    `integracion_F1/scripts/railway_setup.py`'s `gql()` uses."""
    auth_headers = (
        {"Authorization": f"Bearer {token}"},
        {"Project-Access-Token": token},
    )
    last_error: str | None = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        for auth in auth_headers:
            resp = await client.post(
                RAILWAY_API,
                json={"query": query, "variables": variables},
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "sismo-cali-dashboard/1.0",
                    **auth,
                },
            )
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.status_code < 300 and not body.get("errors"):
                return body.get("data") or {}
            last_error = f"Railway API {resp.status_code}: {body.get('errors') or body}"
    raise RuntimeError(last_error or "Railway API request failed")


def _cron_adjuncts() -> list[tuple[str, str]]:
    """(name, service_id) for the two 15-min crons NOT yet absorbed into this
    backend. Each id is env-overridable, same fallback pattern the legacy
    RAILWAY_*_SERVICE_ID vars used."""
    return [
        (
            "cruce-sticker",
            os.environ.get("RAILWAY_STICKER_SERVICE_ID", "").strip()
            or DEFAULT_STICKER_SERVICE_ID,
        ),
        (
            "cruce-gestion",
            os.environ.get("RAILWAY_CRUCE_SERVICE_ID", "").strip()
            or DEFAULT_CRUCE_SERVICE_ID,
        ),
    ]


async def _redeploy_adjuncts() -> dict[str, str]:
    """Best-effort redeploy of the two not-yet-absorbed crons. Returns a
    name -> error-message map (empty when everything succeeded); never
    raises — a Railway hiccup here must not affect the in-process primary
    run this endpoint already dispatched."""
    token = os.environ.get("RAILWAY_API_TOKEN", "").strip()
    if not token:
        return {"railway": "RAILWAY_API_TOKEN no configurado; cruce-sticker/cruce-gestion no se dispararon."}
    environment_id = (
        os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip() or DEFAULT_ENVIRONMENT_ID
    )
    errors: dict[str, str] = {}
    for name, service_id in _cron_adjuncts():
        try:
            await _railway_graphql(token, REDEPLOY_MUTATION, {"s": service_id, "e": environment_id})
        except Exception as exc:  # noqa: BLE001 - fail-soft adjunct
            errors[name] = str(exc)
    return errors


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

    errors = await _redeploy_adjuncts()
    return {"ok": True, "errors": errors}
