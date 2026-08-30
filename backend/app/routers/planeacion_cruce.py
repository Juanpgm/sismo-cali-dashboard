"""POST /planeacion-cruce/run, GET /planeacion-cruce/status — admin-triggered
manual run of `app.jobs.planeacion_cruce.run_planeacion_cruce` (the
atencionsismo <-> EDAN-survey cross-reference cron), run IN-PROCESS instead
of a Railway redeploy of that cron's own container — the SAME pattern
`routers/refresh.py` established for the dashboard-refresh cron (see that
module's own docstring for why: a Railway-redeploy trigger has twice broken
production, and the pipeline code already lives in this image).

A module-level lock keeps two overlapping runs from racing on the same
Firestore docs the job writes: a second call while one is in flight gets
409 — exactly `refresh.py`'s own `_refresh_lock` shape, acquired
non-blocking in the request handler (so two rapid clicks can't both slip
past the guard) and released by the background task in a `finally`, so a
raising job never leaves it stuck.

`GET /planeacion-cruce/status` surfaces whether a run is currently in
flight (`lock.locked()`) plus the LAST completed run's summary
(`read_last_run` — the job's own `write_state` persists it on every
non-dry run) AND `last_checked_at` (`read_last_checked` — stamped on
EVERY run, no-op or real), so an operator can tell "healthy quiet period"
(the early-exit gate keeps firing, `last_checked_at` keeps advancing) from
"cron died" (neither field advances) — the same visibility an operator
would otherwise only get by tailing Railway cron logs.

## Encapsulation note

This router deliberately never spells out either of the job's own two
Firestore collection names, in code, comments, or docstrings — they stay
encapsulated inside `app.jobs.planeacion_cruce` and the read-only service
module it reads surveys from. That is what keeps this file OUT of
`tests/invariants/test_sole_writer.py`'s independent allowlists for those
two collections: it never references them directly, it only calls the
job's own already-allowlisted functions.

## Cache invalidation is best-effort, ADJACENT to the job's own outcome

After a successful (non-dry) run, this router clears
`app.state.planeacion_aggregates_cache` (`routers/planeacion_asignaciones.py`'s
`resumen`/`metricasProgreso` TTL cache) so an admin doesn't see stale tallies
for up to 5 minutes after a manual run. That clear is wrapped in its own
try/except — a cache-clear failure must never be reported as if the cross-
reference run itself failed, mirroring how `refresh.py`'s own background
task treats its `run_refresh()` outcome as authoritative over any
adjacent bookkeeping step.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.deps import require_role
from app.credentials import clients as credentials
from app.jobs.planeacion_cruce import read_last_checked, read_last_run, run_planeacion_cruce

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

router = APIRouter()

# Non-blocking check-and-set, acquired in the request handler, released by
# the background task once the job returns or raises — same convention
# routers/refresh.py's own `_refresh_lock` uses.
_lock = threading.Lock()


class RunBody(BaseModel):
    top: int | None = None
    dry: bool = False
    full: bool = False


def _run_and_release(top: int | None, dry: bool, full: bool, cache: Any) -> None:
    """Background-task body. Starlette runs sync `BackgroundTasks.add_task`
    callables in a threadpool, so the job's blocking Firestore calls never
    block this service's event loop — same reasoning routers/refresh.py's
    own `_run_refresh_and_release` documents. The lock is released in
    `finally` BEFORE the cache clear, so a job failure frees the lock
    immediately and never attempts the (unnecessary, on failure) cache
    clear at all."""
    try:
        run_planeacion_cruce(top=top, dry=dry, full=full)
    except Exception:  # noqa: BLE001 - a job failure must not leak into an unreleased lock
        traceback.print_exc()
        return
    finally:
        _lock.release()

    if not dry and cache is not None:
        try:
            cache.clear()
        except Exception:  # noqa: BLE001 - best-effort; must never mask the job's own outcome
            traceback.print_exc()


@router.post("/planeacion-cruce/run", status_code=202)
async def trigger_planeacion_cruce(
    body: RunBody,
    background_tasks: BackgroundTasks,
    request: Request,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    if not _lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una corrida en curso.")
    cache = getattr(request.app.state, "planeacion_aggregates_cache", None)
    background_tasks.add_task(_run_and_release, body.top, body.dry, body.full, cache)
    return {"ok": True, "params": {"top": body.top, "dry": body.dry, "full": body.full}}


@router.get("/planeacion-cruce/status")
async def planeacion_cruce_status(
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    db = credentials.sismo().firestore
    return {"running": _lock.locked(), "last_run": read_last_run(db),
            "last_checked_at": read_last_checked(db)}
