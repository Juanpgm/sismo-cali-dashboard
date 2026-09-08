"""GET /stickers-atencionsismo — the Stickers tab's Evaluaciones list sourced
from atencionsismo `GET /api/informe/stickers` instead of Firestore
`evaluaciones` (design D1..D4). Same response shape as GET /evaluaciones
plus `fuente`/`origen`/`color_etiqueta` per record, so web/js/evaluaciones.js
renders both sources with one code path.

Cache: `stickers.EvaluacionesCache` parametrized with its own Blob
last-known-good pathname and an allowlist redaction that blanks the
affected person's name and the inspector NP (same sensitivity class as the
evaluaciones copy). A Firestore failure (roster or evaluaciones) fails the
whole fetch on purpose: without NP there is no Fase, and a silently empty
Fase is worse than a stale payload.
"""
from __future__ import annotations

import asyncio
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

# Hard ceiling on the whole informe/stickers pull: fetch_stickers can walk
# hundreds of pages (MAX_PAGES), each individually retried — without an
# outer deadline a sustained slow-but-alive upstream could hang this sync
# route (running in FastAPI's threadpool) far longer than any caller would
# wait. 45s comfortably covers a normal full walk while still failing fast
# into the cache's serve-stale/Blob chain instead of hanging the worker.
STICKERS_FETCH_DEADLINE_S = 45.0

_BLOB_ALLOWED_FIELDS = stickers._BLOB_ALLOWED_FIELDS + ("fuente", "origen", "color_etiqueta")


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

    rows = asyncio.run(_pull_with_deadline())  # sync route runs in the threadpool: no running loop here
    roster_map = stickers.inspector_profile_by_codigo(db)
    firestore_evals = evaluaciones_cache.get_or_fetch(lambda: stickers.list_evaluaciones(db))
    if evaluaciones_cache.degraded:
        # design D4: "si Firestore falla, el fetch falla completo" — a
        # Blob-restored evaluaciones payload has `inspector.np` blanked
        # (`stickers._redact_for_blob`), so silently joining it here would
        # produce a WRONG Fase (not just a stale one). Fail this fetch too
        # so THIS cache (`stickers_atencionsismo_cache`) runs its own
        # serve-stale / Blob-restore chain instead of serving a payload with
        # a poisoned Fase.
        raise RuntimeError("evaluaciones degradado: sin NP no hay Fase")
    return build_evaluaciones(rows, roster_by_codigo=roster_map, evaluaciones_firestore=firestore_evals)


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
        # degraded, sin NP no hay Fase" guard above, re-raised verbatim by
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
