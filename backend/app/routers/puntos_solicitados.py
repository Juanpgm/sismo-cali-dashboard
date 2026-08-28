"""POST/GET /puntos-solicitados, PATCH/DELETE /puntos-solicitados/{id},
POST /geocode — admin-only registration, listing, editing, and deletion of
special-case ("solicitado") points, plus the live geocoding proxy
(`puntos-solicitados` change, design.md ADR-1 through ADR-6; spec
`puntos-solicitados/*`).

Structurally close to the existing survey-CRUD router (REST-shaped CRUD, no
`/api` prefix, `require_role("admin")` per route) but with its OWN
two-collection write shape instead of that router's single-collection
mutation core.

**ADR-1 — atomic dual-write, one `db.batch()`.** `crear_punto_solicitado`
pre-generates the `puntos_solicitados` doc id (`.document()` with no path —
no round trip), mints `clave_integracion('solicitado', sid)`, then writes
BOTH `puntos_solicitados/{sid}` and `planeacion_puntos/solicitado_{sid}` in
ONE `db.batch()` committed once. A `WriteBatch` is already atomic across
documents/collections: if `commit()` raises, NOTHING was written — no
orphan is possible by construction, no compensation logic needed here.

**ADR-2 — `es_solicitado` is a flat field on the mirror**, written directly
in the same batch, never resolved by a join at read time (the formulario's
existing `misPuntosPlaneacion` reads only `planeacion_puntos`). Exactly one
non-standard field crosses onto the mirror; `justificacion`/contact/photos
stay on `puntos_solicitados` only.

**ADR-3 — `clave_integracion`/`doc_id` are IMPORTED from
`app.jobs.planeacion_cruce`, never re-implemented.** Both are pure (no
Firestore access); `doc_id('solicitado', sid)` yields the exact
`solicitado_{sid}` mirror id this module's `_mirror_doc_id` wraps.

**ADR-4 — `estado_seguimiento` is DERIVED, never a synced second
lifecycle.** `listar_puntos_solicitados` reads the mirror's
`estado_asignacion` (one batched `get_all`) and maps it through
`ESTADO_SEGUIMIENTO_MAP`. `PATCH /{id}` never writes `estado_seguimiento`
or lifecycle fields (`estado_asignacion`/`cuadrilla_id`/`inspector_uid`) —
every lifecycle transition is driven exclusively by the EXISTING
`planeacion_asignaciones.py`/`inspector_asignaciones.py` endpoints. It DOES
re-sync the ADR-2 mirrored display subset (`nombre`/`direccion`/`barrio`/
`comuna`/`coords`) onto the mirror in the same atomic batch, so a corrected
name/address/location never goes stale on the assignment board;
`justificacion`/contact/photos stay solicitado-only, never copied.

**ADR-5 — `POST /geocode`: live proxy, `Depends(require_auth)`** (any
authenticated caller, not admin-only — creating a point still IS
admin-only). Delegates to the pure `app.services.geocode.geocode`; a
`GeocodeKeyError` (bad key/quota) or `GeocodeTransportError` (timeout/
connection failure/malformed response) both map to the same 502. The API
key never appears in any response — `geocode()` reads it from the
environment itself.

**ADR-6 — sole-writer allowlist**: this module is added to
`tests/invariants/test_sole_writer.py`'s existing
`ALLOWED_MODULES_PLANEACION_PUNTOS` (it writes the mirror) AND a NEW,
independent `puntos_solicitados` collection literal check names this file
(+ `app/main.py`, import/mount only) as its sole allowlisted module.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.auth.deps import require_auth, require_role
from app.credentials import clients as credentials
from app.jobs.planeacion_cruce import clave_integracion, doc_id
from app.services.geocode import GeocodeKeyError, GeocodeTransportError
from app.services.geocode import geocode as geocode_service

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

PUNTOS_SOLICITADOS_COLLECTION = "puntos_solicitados"
# Imported, never re-literaled, from planeacion_asignaciones.py's own module
# constant would create a circular-ish coupling for one string; this mirrors
# the collection name planeacion_cruce.py/planeacion_asignaciones.py already
# both hardcode independently (same literal, no shared owner module today).
PLANEACION_PUNTOS_COLLECTION = "planeacion_puntos"

FUENTE = "solicitado"

# `puntos_solicitados` required-field set (spec: "Admin-only creation with
# required-field validation"). Enforced by CrearPuntoSolicitadoBody's plain
# (non-Optional) fields below — FastAPI/Pydantic 422s a missing key before
# this router's own code ever runs, so "zero writes" on a missing field is
# automatic, not a manual check.
MAX_FOTOS = 10

# Solicited points are always top priority (proposal.md: "visually flagged
# as priority"; design.md ADR-2's mirror interface table: `prioridad:'alta'`,
# `prioridad_score:<high>`). 100 is the ceiling of `planeacion_cruce.py`'s
# own 0-100 scoring range (ALTA_THRESHOLD=60), so a solicited point always
# outranks every pipeline point in `planeacion_asignaciones.list_puntos`'s
# `prioridad_score`-ordered query.
PRIORIDAD_SCORE_SOLICITADO = 100

# ADR-4: derived `estado_seguimiento`, never a second stored lifecycle.
ESTADO_SEGUIMIENTO_MAP: dict[str, str] = {
    "pendiente": "pendiente",
    "asignado": "asignado",
    "en_proceso": "en_proceso",
    "hecho": "visitado",
    "no_aplica": "excluido",
}

router = APIRouter()


class CrearPuntoSolicitadoBody(BaseModel):
    nombre: str
    comuna_corregimiento: str
    barrio_vereda: str
    nombre_solicitante: str
    telefono_solicitante: str
    justificacion: str
    lat: float
    lng: float
    # Typed address used for /geocode (client-side) and carried through onto
    # the mirror's `direccion` field; not itself in the required set (a
    # manual lat/lng submit — spec scenario "Manual coordinate entry" — may
    # never call /geocode or type an address at all).
    direccion: str = ""
    fotos: list[str] = Field(default_factory=list)

    # Blank/whitespace-only required strings pass Pydantic's plain `str` type
    # check (a `disabled` form field or an all-spaces typed value both submit
    # as `""`/`"   "`) — reject them the same way a missing key already 422s,
    # so "zero writes" holds for both "field absent" and "field blank".
    @field_validator(
        "nombre", "comuna_corregimiento", "barrio_vereda",
        "nombre_solicitante", "telefono_solicitante", "justificacion",
    )
    @classmethod
    def _reject_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("no puede estar vacío")
        return v


class EditarPuntoSolicitadoBody(BaseModel):
    """Partial-write PATCH body — every field optional, `None` means
    "leave unchanged" (mirrors `exclude_unset` semantics, not a nullable
    write). Never carries `estado_seguimiento` — ADR-4."""

    nombre: str | None = None
    comuna_corregimiento: str | None = None
    barrio_vereda: str | None = None
    nombre_solicitante: str | None = None
    telefono_solicitante: str | None = None
    justificacion: str | None = None
    direccion: str | None = None
    lat: float | None = None
    lng: float | None = None
    fotos: list[str] | None = None


class GeocodeBody(BaseModel):
    direccion: str


def _mirror_doc_id(sid: str) -> str:
    """`solicitado_{sid}` — via the SAME `doc_id()` the pipeline uses for
    its own `atencionsismo_{registro_id}` mirror ids (ADR-3), never
    re-literaled here."""
    return doc_id(FUENTE, sid)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(value: Any) -> Any:
    """Firestore Timestamps -> ISO-8601 strings; same minimal walker every
    other router in this backend re-derives locally (`planeacion_
    asignaciones.py`'s own `_jsonable`) rather than sharing one helper
    module for a handful of lines."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Punto solicitado no encontrado.")


# ---- Routes -----------------------------------------------------------


@router.post("/puntos-solicitados", status_code=201)
def crear_punto_solicitado(
    body: CrearPuntoSolicitadoBody,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    if len(body.fotos) > MAX_FOTOS:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FOTOS} fotos.")

    db = credentials.sismo().firestore

    # ADR-1: allocate the id first (no write yet) so both the doc and the
    # mirror's `registro_id`/doc-id can reference it before either `.set()`.
    ref = db.collection(PUNTOS_SOLICITADOS_COLLECTION).document()
    sid = ref.id
    clave = clave_integracion(FUENTE, sid)
    now = _now()
    uid = str(claims.get("sub") or "")
    coords = {"lat": body.lat, "lon": body.lng}

    solicitado_fields: dict[str, Any] = {
        "nombre": body.nombre,
        "comuna_corregimiento": body.comuna_corregimiento,
        "barrio_vereda": body.barrio_vereda,
        "nombre_solicitante": body.nombre_solicitante,
        "telefono_solicitante": body.telefono_solicitante,
        "justificacion": body.justificacion,
        "direccion": body.direccion,
        "coords": coords,
        "fotos": body.fotos,
        "clave_integracion": clave,
        "estado_seguimiento": "pendiente",
        "creado_por": uid,
        "creado_en": now,
    }
    # ADR-2's Interfaces table: the ordinary planeacion_puntos field set any
    # point carries, plus the ONE non-standard field, es_solicitado.
    mirror_fields: dict[str, Any] = {
        "fuente": FUENTE,
        "registro_id": sid,
        "clave_integracion": clave,
        "es_solicitado": True,
        "nombre": body.nombre,
        "direccion": body.direccion,
        "barrio": body.barrio_vereda,
        "comuna": body.comuna_corregimiento,
        "coords": coords,
        "prioridad": "alta",
        "prioridad_score": PRIORIDAD_SCORE_SOLICITADO,
        "prioridad_override": None,
        "tiene_survey": False,
        "estado_asignacion": "pendiente",
        "cuadrilla_id": None,
        "inspector_uid": None,
        "matched_at": now,
    }

    try:
        batch = db.batch()
        batch.set(ref, solicitado_fields)
        batch.set(db.collection(PLANEACION_PUNTOS_COLLECTION).document(_mirror_doc_id(sid)), mirror_fields)
        batch.commit()
    except Exception as exc:  # noqa: BLE001 — batch is atomic; surface as a clean 502, never a partial write
        logging.exception("Fallo creando punto solicitado")
        raise HTTPException(status_code=502, detail=f"No se pudo crear el punto solicitado: {exc}") from exc

    return JSONResponse({"ok": True, "id": sid, "clave_integracion": clave}, status_code=201)


@router.get("/puntos-solicitados")
def listar_puntos_solicitados(claims: dict[str, Any] = Depends(require_role("admin"))) -> JSONResponse:
    """ADR-4: `estado_seguimiento` in the response is DERIVED from the
    mirror's `estado_asignacion` via one batched `get_all` — never the
    stored seed value once a mirror exists."""
    db = credentials.sismo().firestore
    try:
        docs = list(db.collection(PUNTOS_SOLICITADOS_COLLECTION).get())
        if not docs:
            return JSONResponse({"ok": True, "puntos": []})

        mirror_refs = [
            db.collection(PLANEACION_PUNTOS_COLLECTION).document(_mirror_doc_id(d.id)) for d in docs
        ]
        mirror_by_id = {s.id: (s.to_dict() or {}) for s in db.get_all(mirror_refs) if s.exists}
    except Exception as exc:  # noqa: BLE001 — clean 502, never an unhandled 500
        logging.exception("Fallo listando puntos solicitados")
        raise HTTPException(status_code=502, detail=f"No se pudo listar los puntos solicitados: {exc}") from exc

    puntos = []
    for d in docs:
        data = _jsonable(d.to_dict() or {})
        mirror = mirror_by_id.get(_mirror_doc_id(d.id))
        estado_asignacion = (mirror or {}).get("estado_asignacion") or "pendiente"
        puntos.append({
            "id": d.id,
            **data,
            "estado_seguimiento": ESTADO_SEGUIMIENTO_MAP.get(estado_asignacion, "pendiente"),
        })
    return JSONResponse({"ok": True, "puntos": puntos})


@router.patch("/puntos-solicitados/{id}")
def editar_punto_solicitado(
    id: str,
    body: EditarPuntoSolicitadoBody,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    """ADR-4: edits `puntos_solicitados` request-metadata fields — never
    `estado_seguimiento`. `lat`/`lng` are collapsed into the same
    `coords:{lat,lon}` shape the create path writes. The subset of fields
    ADR-2 mirrors (`nombre`/`direccion`/`barrio`/`comuna`/`coords`) is
    re-synced onto the `planeacion_puntos` mirror in the SAME atomic batch
    (ADR-1 precedent) so the assignment board never renders stale
    location/name; `justificacion`/contact/photos/lifecycle stay untouched
    on the mirror, per ADR-2/ADR-4."""
    if body.fotos is not None and len(body.fotos) > MAX_FOTOS:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FOTOS} fotos.")

    db = credentials.sismo().firestore
    ref = db.collection(PUNTOS_SOLICITADOS_COLLECTION).document(id)
    snap = ref.get()
    if not snap.exists:
        raise _not_found()

    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    lat = changes.pop("lat", None)
    lng = changes.pop("lng", None)
    if lat is not None or lng is not None:
        current_coords = (snap.to_dict() or {}).get("coords") or {}
        changes["coords"] = {
            "lat": lat if lat is not None else current_coords.get("lat"),
            "lon": lng if lng is not None else current_coords.get("lon"),
        }

    # ADR-2's mirrored subset only — request-only fields (justificacion,
    # contact, photos) never cross onto planeacion_puntos.
    mirror_field_map = {"nombre": "nombre", "direccion": "direccion",
                         "barrio_vereda": "barrio", "comuna_corregimiento": "comuna"}
    mirror_changes = {mirror_field_map[k]: v for k, v in changes.items() if k in mirror_field_map}
    if "coords" in changes:
        mirror_changes["coords"] = changes["coords"]

    try:
        if mirror_changes:
            mirror_ref = db.collection(PLANEACION_PUNTOS_COLLECTION).document(_mirror_doc_id(id))
            batch = db.batch()
            batch.set(ref, changes, merge=True)
            batch.set(mirror_ref, mirror_changes, merge=True)
            batch.commit()
        elif changes:
            ref.set(changes, merge=True)
    except Exception as exc:  # noqa: BLE001 — clean 502, never an unhandled 500
        logging.exception("Fallo actualizando punto solicitado %s", id)
        raise HTTPException(status_code=502, detail=f"No se pudo actualizar el punto solicitado: {exc}") from exc
    return JSONResponse({"ok": True, "id": id})


@router.delete("/puntos-solicitados/{id}")
def eliminar_punto_solicitado(
    id: str,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    """Deletes both the request doc AND its mirror — the inverse of the
    ADR-1 dual-write, so a deleted solicited point does not linger in the
    assignment queue as an orphan mirror."""
    db = credentials.sismo().firestore
    ref = db.collection(PUNTOS_SOLICITADOS_COLLECTION).document(id)
    if not ref.get().exists:
        raise _not_found()

    try:
        batch = db.batch()
        batch.delete(ref)
        batch.delete(db.collection(PLANEACION_PUNTOS_COLLECTION).document(_mirror_doc_id(id)))
        batch.commit()
    except Exception as exc:  # noqa: BLE001 — clean 502, never an unhandled 500
        logging.exception("Fallo eliminando punto solicitado %s", id)
        raise HTTPException(status_code=502, detail=f"No se pudo eliminar el punto solicitado: {exc}") from exc
    return JSONResponse({"ok": True, "id": id})


@router.post("/geocode")
def geocode_route(
    body: GeocodeBody,
    claims: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """ADR-5: live proxy, any authenticated caller. Google
    REQUEST_DENIED/OVER_QUERY_LIMIT/INVALID_REQUEST, transport failures
    (timeout/connection error), and malformed responses all map to the
    SAME clean 502 (key/quota problem or upstream failure, never an
    address rejection); everything else comes back as `geocode()`'s own
    `{ok, accepted, ...}` shape unchanged."""
    try:
        result = geocode_service(body.direccion)
    except (GeocodeKeyError, GeocodeTransportError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(result)
