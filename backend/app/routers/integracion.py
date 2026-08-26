"""GET /integracion/* — READ-ONLY interop feed of cross-system integration
keys, API-key gated (`auth/api_key.py`'s `require_api_key`, every route).

Purpose: let OTHER systems resolve the shared keys that tie an atencionsismo
report, its EDAN/israel survey, and (best-effort) its field sticker together,
without granting them the dashboard's admin write surface. This module NEVER
writes any collection — it only reads `planeacion_puntos` (via
`credentials.sismo()`), reusing `planeacion_asignaciones.py`'s
`_doc_to_dict`/`_jsonable` JSON-safe projection idiom (imported, not
duplicated) and its `LIMIT_MAX`/`_clamp_limit` bounds.

Per point it projects ONLY the interop keys:
`{registro_id, clave_integracion, codigoapp, tiene_survey, survey_globalid,
match_via, sticker_globalid}`.

  * `codigoapp` == `clave_integracion` by construction — the Survey123
    `codigoapp` a field crew types is exactly the point's minted
    `clave_integracion` (see `app/jobs/planeacion_cruce.py`'s minting rule
    and `build_key_index`, which pairs a survey to a point by
    `codigoapp == clave_integracion`). It is surfaced under both names so an
    interop caller that knows the survey side by "codigoapp" and one that
    knows the point side by "clave_integracion" both find it.

  * UNLIKE `planeacion_asignaciones.list_puntos`, this does NOT default-hide
    `tiene_survey == true` rows — interop consumers specifically want the
    MATCHED ones. `tiene_survey` is an OPTIONAL query filter instead.

## `sticker_globalid` — an INFERRED tie, no stored cross-key

There is no persisted field linking a `planeacion_puntos` point to a
`sticker_matches` doc. This link is INFERRED from a namespace identity
verified across the pipeline (2026-08-26):

  * `survey_cali` docs are keyed by the Survey123 `GlobalID`
    (`services/survey_cali.ingest_records`, doc id = `record["GlobalID"]`),
    and `inspections.json`'s `GlobalID` IS that Survey123 UUID
    (`scripts/refresh_data.py` lowercases/strips it, never re-mints it).
  * A point's `survey_globalid` is that same `survey_cali` doc id
    (`planeacion_cruce.fetch_surveys`, `GlobalID = doc.id`).
  * A `sticker_matches` doc for an EDE point is keyed
    `ede_{registro_id}` where `registro_id` is `inspections.json`'s
    `GlobalID` (`cruce_sticker.load_panel`).

So `ede_{survey_globalid}` addresses the SAME building's sticker doc. For a
point WITH a `survey_globalid`, `sticker_globalid = survey_globalid` iff a
`sticker_matches/ede_{survey_globalid}` doc exists, else `null`. Existence
is checked in ONE batched `get_all` (chunked at `BATCH_SIZE`), not one read
per point. It is `null` whenever the namespaces don't line up — e.g. an
israel-sourced `survey_globalid` (prefixed `isr-`, matched against israel
surveys, never an `ede_` sticker) — so no wrong link is ever emitted.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth.api_key import require_api_key
from app.credentials import clients as credentials
from app.routers.planeacion_asignaciones import (
    LIMIT_MAX,  # noqa: F401 — re-exported bound; _clamp_limit caps at it
    PLANEACION_PUNTOS_COLLECTION,
    _clamp_limit,
    _doc_to_dict,
)

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

STICKER_MATCHES_COLLECTION = "sticker_matches"
FUENTE = "atencionsismo"  # planeacion_puntos doc id is f"{FUENTE}_{registro_id}"
BATCH_SIZE = 500  # Firestore get_all chunk cap

router = APIRouter()


def _sticker_globalids(db: Any, survey_globalids: list[str | None]) -> dict[str, str]:
    """{survey_globalid: survey_globalid} for those whose
    `sticker_matches/ede_{survey_globalid}` doc EXISTS — the inferred tie
    (see module docstring). Batched `get_all` over the distinct, non-null
    ids present in the page, chunked at BATCH_SIZE."""
    uniq = sorted({g for g in survey_globalids if g})
    if not uniq:
        return {}
    col = db.collection(STICKER_MATCHES_COLLECTION)
    present: dict[str, str] = {}
    for start in range(0, len(uniq), BATCH_SIZE):
        chunk = uniq[start:start + BATCH_SIZE]
        refs = [col.document(f"ede_{g}") for g in chunk]
        for snap in db.get_all(refs):
            if snap.exists:
                gid = snap.id[len("ede_"):]  # strip the "ede_" prefix
                present[gid] = gid
    return present


def _project(point: dict[str, Any], sticker_map: dict[str, str]) -> dict[str, Any]:
    """Interop-key subset ONLY — no other `planeacion_puntos` field leaks."""
    clave = point.get("clave_integracion")
    sg = point.get("survey_globalid")
    return {
        "registro_id": point.get("registro_id"),
        "clave_integracion": clave,
        "codigoapp": clave,  # identical by construction (module docstring)
        "tiene_survey": point.get("tiene_survey"),
        "survey_globalid": sg,
        "match_via": point.get("match_via"),
        "sticker_globalid": sticker_map.get(sg) if sg else None,
    }


def _first_where(db: Any, field: str, value: str) -> dict[str, Any] | None:
    docs = list(
        db.collection(PLANEACION_PUNTOS_COLLECTION).where(field, "==", value).limit(1).get()
    )
    return _doc_to_dict(docs[0]) if docs else None


def _one(db: Any, point: dict[str, Any] | None, detail: str) -> JSONResponse:
    if point is None:
        raise HTTPException(status_code=404, detail=detail)
    sticker_map = _sticker_globalids(db, [point.get("survey_globalid")])
    return JSONResponse({"llave": _project(point, sticker_map)})


@router.get("/integracion/llaves")
def llaves(
    limit: Any = None,
    cursor: str | None = None,
    tiene_survey: bool | None = None,
    _: None = Depends(require_api_key),
) -> JSONResponse:
    """Paginated interop-key feed. `_clamp_limit` bounds the page at
    `LIMIT_MAX` (mirroring `list_puntos`); `cursor` is the last
    `registro_id` of the previous page (Firestore `start_after`). Does NOT
    hide matched rows; `tiene_survey` filters optionally."""
    db = credentials.sismo().firestore
    n = _clamp_limit(limit)

    query = db.collection(PLANEACION_PUNTOS_COLLECTION)
    if tiene_survey is not None:
        query = query.where("tiene_survey", "==", tiene_survey)
    query = query.order_by("registro_id")
    if cursor:
        query = query.start_after({"registro_id": cursor})
    query = query.limit(n + 1)  # over-fetch one to detect a next page

    docs = [_doc_to_dict(d) for d in query.get()]
    has_more = len(docs) > n
    page = docs[:n]
    sticker_map = _sticker_globalids(db, [p.get("survey_globalid") for p in page])
    llaves_out = [_project(p, sticker_map) for p in page]
    next_cursor = page[-1].get("registro_id") if has_more and page else None
    return JSONResponse({"llaves": llaves_out, "next_cursor": next_cursor})


@router.get("/integracion/por-atencionsismo/{id}")
def por_atencionsismo(id: str, _: None = Depends(require_api_key)) -> JSONResponse:
    db = credentials.sismo().firestore
    snap = db.collection(PLANEACION_PUNTOS_COLLECTION).document(f"{FUENTE}_{id}").get()
    point = _doc_to_dict(snap) if snap.exists else None
    return _one(db, point, f"No existe el punto {FUENTE}_{id}.")


@router.get("/integracion/por-clave/{clave}")
def por_clave(clave: str, _: None = Depends(require_api_key)) -> JSONResponse:
    db = credentials.sismo().firestore
    return _one(db, _first_where(db, "clave_integracion", clave),
                f"Ningún punto con clave_integracion {clave}.")


@router.get("/integracion/por-survey/{globalid}")
def por_survey(globalid: str, _: None = Depends(require_api_key)) -> JSONResponse:
    db = credentials.sismo().firestore
    return _one(db, _first_where(db, "survey_globalid", globalid),
                f"Ningún punto con survey_globalid {globalid}.")
