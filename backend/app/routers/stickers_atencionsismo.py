"""GET /stickers-atencionsismo — the Stickers tab's Evaluaciones list sourced
from atencionsismo `GET /api/informe/stickers` instead of Firestore
`evaluaciones` (design D1..D4). Same response shape as GET /evaluaciones
plus `fuente`/`origen`/`color_etiqueta` per record, so web/js/evaluaciones.js
renders both sources with one code path.

Cache: `stickers.EvaluacionesCache` parametrized with its own Blob
last-known-good pathname and an allowlist redaction that blanks the
affected person's name and the inspector NP (same sensitivity class as the
evaluaciones copy). A Firestore failure (roster or evaluaciones) fails the
whole fetch on purpose: NP is still the FALLBACK Fase source (contrato v3,
2026-09-08 — the API's own `fase` is primary, see
stickers_atencionsismo.py) for rows where `fase` is `None`, and it also
carries inspector identity (nombre_completo, uid, entidad) — a silently
empty Fase/identity for those rows is worse than a stale payload.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.deps import require_role
from app.credentials import clients as credentials
from app.routers import stickers
from app.services import atencionsismo
from app.services.stickers_atencionsismo import build_evaluaciones

router = APIRouter()
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

STICKERS_LKG_BLOB = "data/stickers_atencionsismo_last_good.json"

# Dedicated executor for the two Firestore reads in `build_payload`'s
# concurrent gather (fail-fast fix, adversarial review 2026-09-10) — this
# must NOT be `asyncio.to_thread`'s default (loop) executor. `build_payload`
# runs its own fresh event loop per call via `asyncio.run(...)`, and
# `asyncio.run`'s own teardown calls `loop.shutdown_default_executor()`,
# which SYNCHRONOUSLY JOINS every thread ever submitted to the loop's
# default executor (up to a 300s cap) before `asyncio.run` returns — even
# for a task whose asyncio-level wrapper we already cancelled. Cancelling a
# `Task` wrapping a default-executor call only stops OUR await of it; it
# does not detach the underlying thread from the executor `asyncio.run`
# joins on exit, so using the default executor here would silently defeat
# the whole fail-fast fix on a slow Firestore call. A private executor
# sidesteps that entirely (`shutdown_default_executor` only touches the
# loop's OWN default executor, which this never becomes).
_FIRESTORE_READ_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    thread_name_prefix="stickers-atencionsismo-firestore"
)

# Hard ceiling on the whole informe/stickers pull: fetch_stickers can walk
# hundreds of pages (MAX_PAGES), each individually retried — without an
# outer deadline a sustained slow-but-alive upstream could hang this sync
# route (running in FastAPI's threadpool) far longer than any caller would
# wait. 45s comfortably covers a normal full walk while still failing fast
# into the cache's serve-stale/Blob chain instead of hanging the worker.
STICKERS_FETCH_DEADLINE_S = 45.0

# inspector_fuente ("evaluacion"|"roster"|"api"|"") is a bare enum, not PII,
# so it is allowlisted alongside the other atencionsismo-only fields —
# dropping it on the public Blob copy would silently undo the
# misattribution-risk caveat callers key off of
# (stickers_atencionsismo.normalize_sticker). fase (contrato v3, 2026-09-08)
# is likewise a bare 1|2|None value, not PII — it is the Stickers tab's own
# Fase I/II signal (API developer confirmation), not a raw storage artifact.
_BLOB_ALLOWED_FIELDS = stickers._BLOB_ALLOWED_FIELDS + (
    "fuente", "origen", "color_etiqueta", "inspector_fuente", "fase",
)


def redact_for_blob(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allowlist projection for the PUBLIC Blob copy — `descripcion.nombre`
    is the affected person's name here (personaAfectada), so it is blanked
    along with `inspector.np`; everything else mirrors
    `stickers._redact_for_blob`. Never mutates the input."""
    out: list[dict[str, Any]] = []
    for e in payload:
        insp = e.get("inspector") or {}
        desc = e.get("descripcion") or {}
        out.append({
            **{k: e.get(k) for k in _BLOB_ALLOWED_FIELDS},
            "descripcion": {"nombre": "", "direccion": desc.get("direccion") or ""},
            "inspector": {**{k: insp.get(k) or "" for k in stickers._BLOB_ALLOWED_INSPECTOR},
                          "nombre_completo": "", "identificacion": "", "np": ""},
            "comentarios": "",
            "fotos": [],
        })
    return out


def build_payload(db: Any, evaluaciones_cache: stickers.EvaluacionesCache) -> list[dict[str, Any]]:
    user, password = atencionsismo.credentials_from_env()

    async def _pull() -> list[dict]:
        async with httpx.AsyncClient() as client:
            return await atencionsismo.fetch_stickers(client, user, password)

    async def _pull_with_deadline() -> list[dict]:
        try:
            return await asyncio.wait_for(_pull(), timeout=STICKERS_FETCH_DEADLINE_S)
        except asyncio.TimeoutError as exc:
            raise atencionsismo.ApiUnavailableError(
                f"informe/stickers: no respondio en {STICKERS_FETCH_DEADLINE_S}s", status=503
            ) from exc

    # Perf (2026-09-10): the upstream page walk and the two Firestore reads
    # below are mutually independent — nothing about `roster_map` or
    # `firestore_evals` depends on `rows` or vice versa — so they still run
    # concurrently instead of one after another. The blocking
    # (google-cloud-firestore) calls go through `_FIRESTORE_READ_EXECUTOR`
    # (see its own comment for why NOT `asyncio.to_thread`'s default
    # executor) so their I/O overlaps the async HTTP walk rather than
    # waiting behind it.
    #
    # Fail-fast fix (adversarial review, 2026-09-10): this is NOT
    # byte-identical to the old sequential code — a plain
    # `asyncio.gather(..., return_exceptions=True)` over all three would
    # always wait for the two Firestore reads even when the upstream walk
    # fails, which the OLD sequential code never did (it never even reached
    # them). `evaluaciones_cache` is shared with `GET /evaluaciones`, so an
    # upstream-only failure incorrectly running its own degraded/TTL/
    # Blob-restore chain would be a data-quality regression, not just a
    # perf one. So `pull` is awaited FIRST and, on failure, the other two
    # tasks are cancelled instead of awaited-through:
    #   - response body/status/headers, the error PRIORITY (pull error >
    #     roster error > firestore error), and the success-path result
    #     usage are all preserved exactly as before.
    #   - the ONE residual difference from byte-identical sequential
    #     behavior: on a pull failure, the two Firestore calls may already
    #     be running in their worker threads by the time we cancel (see the
    #     comment below) — the old code never started them at all in that
    #     case.
    async def _gather() -> tuple[list[dict], Any, Any]:
        loop = asyncio.get_running_loop()
        pull_task = asyncio.create_task(_pull_with_deadline())
        # Started AFTER the pull task so the failure path below never
        # depends on their timing to stay correct — they still run
        # concurrently with the upstream walk once the loop schedules them.
        # `ensure_future` because `run_in_executor` already returns a
        # Future, not a coroutine (`create_task` requires the latter).
        roster_task = asyncio.ensure_future(
            # F6: ONE `inspectores` collection scan for both roster shapes —
            # see inspector_profiles' own doc comment.
            loop.run_in_executor(_FIRESTORE_READ_EXECUTOR, stickers.inspector_profiles, db)
        )
        firestore_task = asyncio.ensure_future(
            loop.run_in_executor(
                _FIRESTORE_READ_EXECUTOR, evaluaciones_cache.get_or_fetch,
                lambda: stickers.list_evaluaciones(db),
            )
        )

        try:
            rows = await pull_task
        except BaseException:
            # Never consume or wait through the Firestore reads once the
            # upstream walk has failed.
            roster_task.cancel()
            firestore_task.cancel()
            # NOTE: cancelling the Task wrapping a `run_in_executor(...)`
            # call cancels OUR await of it right away (the task settles as
            # CancelledError without needing the executor thread to
            # finish), but it does NOT interrupt the underlying thread
            # mid-call: once a concurrent.futures.Future is actually
            # running, it cannot be aborted, so the google-cloud-firestore
            # call may keep running to completion in a background thread
            # after this function has already re-raised. That is harmless —
            # `stickers.EvaluacionesCache.get_or_fetch`'s own lock keeps the
            # call concurrency-safe either way, and nothing awaits its
            # result — but it is the residual behavioral difference noted
            # above. (This is also exactly why these two reads use
            # `_FIRESTORE_READ_EXECUTOR` instead of the default executor:
            # see that constant's comment — the default executor would make
            # `asyncio.run` below block on that background thread anyway,
            # defeating this whole fail-fast path.)
            await asyncio.gather(roster_task, firestore_task, return_exceptions=True)
            raise

        roster_result, firestore_result = await asyncio.gather(
            roster_task, firestore_task, return_exceptions=True
        )
        return rows, roster_result, firestore_result

    rows, roster_result, firestore_result = asyncio.run(_gather())
    # sync route runs in the threadpool: no running loop here, same as the
    # old single `asyncio.run(_pull_with_deadline())` call. A pull failure
    # propagates straight out of `_gather()` above (same exception/priority
    # as the old sequential code raised in that case).

    if isinstance(roster_result, BaseException):
        raise roster_result
    if isinstance(firestore_result, BaseException):
        raise firestore_result

    roster_map, roster_by_cedula = roster_result
    firestore_evals = firestore_result

    if evaluaciones_cache.degraded:
        # design D4: "si Firestore falla, el fetch falla completo" — a
        # Blob-restored evaluaciones payload has `inspector.np`,
        # `nombre_completo` AND `identificacion` all blanked
        # (`stickers._redact_for_blob`), so silently joining it here would
        # poison BOTH the Fase fallback and the identity of a matched record
        # (not just serve a stale one). Fail this fetch too so THIS cache
        # (`stickers_atencionsismo_cache`) runs its own serve-stale /
        # Blob-restore chain instead of serving a payload with a poisoned
        # identity/Fase.
        raise RuntimeError("evaluaciones degradado: sin match de Firestore no hay identidad ni NP de respaldo")
    return build_evaluaciones(
        rows, roster_by_codigo=roster_map, evaluaciones_firestore=firestore_evals,
        roster_by_cedula=roster_by_cedula,
    )


@router.get("/stickers-atencionsismo")
def get_stickers_atencionsismo(
    request: Request,
    claims: dict[str, Any] = Depends(require_role("admin", "viewer")),
) -> JSONResponse:
    cache: stickers.EvaluacionesCache = request.app.state.stickers_atencionsismo_cache
    evaluaciones_cache: stickers.EvaluacionesCache = request.app.state.stickers_evaluaciones_cache
    db = credentials.sismo().firestore
    try:
        payload = cache.get_or_fetch(lambda: build_payload(db, evaluaciones_cache))
    except HTTPException:
        raise
    except (atencionsismo.ApiUnavailableError, atencionsismo.ApiCredentialsError,
            atencionsismo.ApiEmptyResultError, RuntimeError) as exc:
        # RuntimeError: `build_payload`'s own "evaluaciones cache is
        # degraded, sin match de Firestore no hay identidad ni NP de
        # respaldo" guard above, re-raised verbatim by
        # `EvaluacionesCache.get_or_fetch` when this (stickers) cache is
        # ALSO cold and has nothing in Blob to restore. Maps to 503 like
        # every other upstream-unavailable case — this is the "nothing
        # trustworthy to serve" branch, not an unclassified bug, so it does
        # not deserve the generic 502 below.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - same CORS-preserving catch-all as GET /evaluaciones
        logging.exception("stickers-atencionsismo: fallo no clasificado")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "fuente": "atencionsismo", "evaluaciones": payload, "degraded": cache.degraded})
