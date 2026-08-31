"""POST /cruce-sticker/run, GET /cruce-sticker/status — admin-triggered
manual run of `app.jobs.cruce_sticker.run_cruce_sticker` (the Panel <->
`evaluaciones` sticker cross-reference), run IN-PROCESS. Replaces the
deleted `cruce-sticker` Railway cron service (2026-08-31 decision: the
automatic runs' Firestore reads alone consumed the daily quota and
key-driven native state covers in-app flows in real time, so cross-
reference runs are ON-DEMAND ONLY now) — the SAME pattern
`routers/planeacion_cruce.py` and `routers/refresh.py` established for
their own absorbed crons (see those modules' docstrings).

A module-level lock keeps two overlapping runs from racing on the same
Firestore docs the job writes: a second call while one is in flight gets
409 — exactly `refresh.py`'s own `_refresh_lock` shape, acquired
non-blocking in the request handler (so two rapid clicks can't both slip
past the guard) and released by the background task in a `finally`, so a
raising job never leaves it stuck.

`GET /cruce-sticker/status` surfaces whether a run is currently in flight
(`lock.locked()`) plus the job's own persisted state (`read_state` — the
job stamps `last_run_at` on every real run and `last_checked_at` on EVERY
run, no-op or real), so an operator can tell "healthy quiet period" (the
early-exit gate keeps firing, `last_checked_at` keeps advancing) from
"never ran" — the same visibility `planeacion_cruce.py`'s status endpoint
provides for its sibling job.

## Encapsulation note

This router deliberately never spells out any of the job's own Firestore
collection names, in code, comments, or docstrings — they stay
encapsulated inside `app.jobs.cruce_sticker`. That is what keeps this
file OUT of `tests/invariants/test_sole_writer.py`'s allowlists: it never
references those collections directly, it only calls the job's own
already-allowlisted functions.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.auth.deps import require_role
from app.credentials import clients as credentials
from app.jobs.cruce_sticker import read_state, run_cruce_sticker

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

router = APIRouter()

# Non-blocking check-and-set, acquired in the request handler, released by
# the background task once the job returns or raises — same convention
# routers/refresh.py's own `_refresh_lock` uses.
_lock = threading.Lock()


class RunBody(BaseModel):
    dry: bool = False
    full: bool = False


def _run_and_release(dry: bool, full: bool) -> None:
    """Background-task body. Starlette runs sync `BackgroundTasks.add_task`
    callables in a threadpool, so the job's blocking Firestore calls never
    block this service's event loop — same reasoning routers/refresh.py's
    own `_run_refresh_and_release` documents. The lock is released in
    `finally`, so a job failure frees it immediately."""
    try:
        run_cruce_sticker(dry=dry, full=full)
    except Exception:  # noqa: BLE001 - a job failure must not leak into an unreleased lock
        traceback.print_exc()
    finally:
        _lock.release()


@router.post("/cruce-sticker/run", status_code=202)
async def trigger_cruce_sticker(
    body: RunBody,
    background_tasks: BackgroundTasks,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    if not _lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una corrida en curso.")
    background_tasks.add_task(_run_and_release, body.dry, body.full)
    return {"ok": True, "params": {"dry": body.dry, "full": body.full}}


@router.get("/cruce-sticker/status")
async def cruce_sticker_status(
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    db = credentials.sismo().firestore
    state = read_state(db)
    return {"running": _lock.locked(),
            "last_run_at": state.get("last_run_at"),
            "last_checked_at": state.get("last_checked_at")}
