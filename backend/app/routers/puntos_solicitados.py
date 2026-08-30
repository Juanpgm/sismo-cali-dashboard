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
admin-only). Delegates to the pure `app.services.geocode.geocode`, backed by
Nominatim (OpenStreetMap, no API key — see that module's docstring for why
this superseded the original Google Geocoding API design). A
`GeocodeTransportError` (timeout/connection failure/bad HTTP status/
malformed response) maps to a 502.

**ADR-6 — sole-writer allowlist**: this module is added to
`tests/invariants/test_sole_writer.py`'s existing
`ALLOWED_MODULES_PLANEACION_PUNTOS` (it writes the mirror) AND a NEW,
independent `puntos_solicitados` collection literal check names this file
(+ `app/main.py`, import/mount only) as its sole allowlisted module.

**`puntos-solicitados-busqueda-asignacion` change (follow-up), ADR-1/ADR-2 —
`GET /puntos-solicitados/buscar`.** Admin-only, read-only search over
`load_reportes()` (imported from `app.jobs.planeacion_cruce`, the same
PII-free `reportes.json` reader the pipeline uses). `_build_rows`/
`_filter_rows` are pure (no Firestore, no clock) — join and
filter+top-20-cap are unit-tested directly. `_build_rows` dedupes by `id`
(first occurrence wins, mirrors `planeacion_cruce.load_puntos`'s own
dedup-by-first-seen on the same source). The (unfiltered) address-only list
is cached with a 5-minute TTL via `BuscarCache`, one instance per
`create_app()` call attached to `app.state.puntos_solicitados_buscar_cache`
— same process-lifetime, test-isolated pattern as `sticker_status.py`'s
`StickerStatusCache`, not a bare module-level dict — so a debounced keystroke
storm doesn't re-read `reportes.json` per character; `q` is applied AFTER
the cached build, never part of the cache key.

**Follow-up perf fix — contact join moved OUT of the cached build, INTO
the route, scoped to the page.** `_joined_rows` used to also do a FULL
`puntos_contacto` collection scan (~14.8k docs) to enrich every cached row;
that full scan is gone. `_joined_rows` now returns address-only rows
(`_build_rows(reportes, {})`, contact fields always `None`). The route
handler `buscar_puntos_solicitados` joins `puntos_contacto` AFTER
`_filter_rows`'s top-20 cap, via ONE batched `db.get_all` keyed by
`atencionsismo_{registro_id}` — never more than 20 doc reads per search.
`load_reportes()` (the primary source) and the per-page `puntos_contacto`
read remain INDEPENDENT failure domains: a `puntos_contacto` read failure
is logged and degrades that page to address-only rows (contact fields
`None`), only a `load_reportes()` failure still 502s the request. Tradeoff:
searching BY requester name (`nombre_solicitante`) no longer matches
anything, since the cached rows the filter runs over never carry contact
data — see the comment at the enrichment site. This still reads (never
writes) `puntos_contacto`, flagged in `tests/invariants/
test_sole_writer.py`'s `ALLOWED_MODULES_PUNTOS_CONTACTO`.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.auth.deps import require_auth, require_role
from app.credentials import clients as credentials
from app.jobs.planeacion_cruce import clave_integracion, doc_id, load_reportes
from app.services.geocode import GeocodeTransportError
from app.services.geocode import geocode as geocode_service

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

PUNTOS_SOLICITADOS_COLLECTION = "puntos_solicitados"
# Imported, never re-literaled, from planeacion_asignaciones.py's own module
# constant would create a circular-ish coupling for one string; this mirrors
# the collection name planeacion_cruce.py/planeacion_asignaciones.py already
# both hardcode independently (same literal, no shared owner module today).
PLANEACION_PUNTOS_COLLECTION = "planeacion_puntos"
# Same re-literal convention `inspector_asignaciones.py` already uses for
# this collection — READ-ONLY here (see module docstring, ADR-1/ADR-2 of the
# `puntos-solicitados-busqueda-asignacion` follow-up).
PUNTOS_CONTACTO_COLLECTION = "puntos_contacto"

FUENTE = "solicitado"

# ADR-2: TTL snapshot of the JOINED (unfiltered) rows — `q` is applied after
# the cached build, never part of the cache key.
_BUSCAR_TTL_S = 300  # 5 min
_BUSCAR_TOP_N = 20

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


# ---- GET /buscar helpers (ADR-1/ADR-2) ---------------------------------


def _build_rows(reportes: list[dict], contacto_by_id: dict[str, dict]) -> list[dict]:
    """Pure join: `reportes.json` records ⋈ `puntos_contacto` on
    `id (str) == registro_id`. Only the fixed field set below ever crosses
    into the output — a raw `reportes` record carrying stray/unstripped
    fields (e.g. a malformed `nombre`/`telefono`) can never leak, by
    construction, since those keys are simply never read here.
    `nombre_solicitante`/`telefono_solicitante` are `None` when no
    `puntos_contacto` doc matches (fail-soft write, not every reporte has
    one). Records without an `id` are skipped — nothing to key a row on.
    Duplicate `id`s are deduped, first occurrence wins — same
    first-seen convention `planeacion_cruce.load_puntos` already uses for
    this exact source (registro_id uniqueness is the one unenforced
    assumption there too); this function stays pure/silent, the
    found/dropped count is logged once by the caller (`_joined_rows`), not
    per-row here."""
    rows: list[dict] = []
    seen_ids: set[str] = set()
    for rep in reportes:
        registro_id = rep.get("id")
        if not registro_id:
            continue
        rid = str(registro_id)
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        contacto = contacto_by_id.get(rid) or {}
        rows.append({
            "registro_id": rid,
            "direccion": rep.get("direccion"),
            "barrio": rep.get("barrio"),
            "comuna": rep.get("comuna"),
            "lat": rep.get("lat"),
            "lng": rep.get("lng"),
            "nombre_solicitante": contacto.get("nombre_solicitante"),
            "telefono_solicitante": contacto.get("telefono_solicitante"),
        })
    return rows


def _filter_rows(rows: list[dict], q: str) -> list[dict]:
    """Case-insensitive substring filter over
    `direccion|barrio|comuna|nombre_solicitante`, top-`_BUSCAR_TOP_N` cap.
    Blank `q` (already the route's empty-fast-path too) yields no rows —
    this is never a full-dump."""
    needle = q.strip().lower()
    if not needle:
        return []
    fields = ("direccion", "barrio", "comuna", "nombre_solicitante")
    matched = [
        row for row in rows
        if any(needle in str(row.get(field) or "").lower() for field in fields)
    ]
    return matched[:_BUSCAR_TOP_N]


class BuscarCache:
    """Process-lifetime TTL cache for `_joined_rows`' joined (unfiltered)
    rows. One instance per `create_app()` call, attached to
    `app.state.puntos_solicitados_buscar_cache` (matches
    `sticker_status.py`'s `StickerStatusCache` convention) instead of a bare
    module-level dict, so tests get a fresh cache per app instance rather
    than leaking a joined-rows snapshot across tests via a module global."""

    def __init__(self) -> None:
        self._at: float | None = None
        self._rows: list[dict] | None = None

    def get_or_fetch(self, fetch: Any) -> list[dict]:
        now = time.monotonic()
        stale = self._rows is None or self._at is None or (now - self._at) > _BUSCAR_TTL_S
        if stale:
            self._rows = fetch()
            self._at = now
        assert self._rows is not None
        return self._rows


def _joined_rows(cache: BuscarCache) -> list[dict]:
    """TTL-cached (via `cache`) ADDRESS-ONLY rows — one `load_reportes()`
    call per TTL window, never per keystroke. No Firestore read happens
    here anymore: `puntos_contacto` used to be scanned in full (~14.8k
    docs) just to enrich a 20-row page, so that join was moved OUT of the
    cached/unfiltered build and INTO the route handler, where it can be
    done with one batched `db.get_all` scoped to the current result page
    only (see `buscar_puntos_solicitados`). `_build_rows(reportes, {})`
    means `nombre_solicitante`/`telefono_solicitante` are always `None` on
    these cached rows; contact enrichment happens per-page, after
    filtering, in the route handler."""

    def _fetch() -> list[dict]:
        reportes = load_reportes()
        ids = [str(rep["id"]) for rep in reportes if rep.get("id")]
        n_dupes = len(ids) - len(set(ids))
        if n_dupes:
            logging.warning(
                "puntos_solicitados.buscar: %d registro_id duplicado(s) en "
                "reportes.json, se conserva solo la primera ocurrencia",
                n_dupes,
            )
        return _build_rows(reportes, {})

    return cache.get_or_fetch(_fetch)


# ---- Routes -----------------------------------------------------------


def _mark_planeacion_snapshot_dirty(request: Request) -> None:
    """Best-effort: tells `PlaneacionPuntosSnapshot` a mirror write just
    happened so the admin board's next read picks it up promptly instead of
    waiting for the snapshot's own TTL. Never raises — a cache-freshness
    miss must never surface as if the actual (already-committed) write had
    failed."""
    snapshot = getattr(request.app.state, "planeacion_puntos_snapshot", None)
    if snapshot is None:
        return
    try:
        snapshot.mark_dirty()
    except Exception:  # noqa: BLE001 - best-effort
        pass


@router.post("/puntos-solicitados", status_code=201)
def crear_punto_solicitado(
    request: Request,
    body: CrearPuntoSolicitadoBody,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    if len(body.fotos) > MAX_FOTOS:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FOTOS} fotos.")

    from google.cloud import firestore as _fs

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
        # Speed follow-up: stamp actualizado_en so the in-process
        # planeacion_puntos snapshot's delta query can find this write.
        "actualizado_en": _fs.SERVER_TIMESTAMP,
    }

    try:
        batch = db.batch()
        batch.set(ref, solicitado_fields)
        batch.set(db.collection(PLANEACION_PUNTOS_COLLECTION).document(_mirror_doc_id(sid)), mirror_fields)
        batch.commit()
    except Exception as exc:  # noqa: BLE001 — batch is atomic; surface as a clean 502, never a partial write
        logging.exception("Fallo creando punto solicitado")
        raise HTTPException(status_code=502, detail=f"No se pudo crear el punto solicitado: {exc}") from exc

    _mark_planeacion_snapshot_dirty(request)
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
        mirror = mirror_by_id.get(_mirror_doc_id(d.id)) or {}
        estado_asignacion = mirror.get("estado_asignacion") or "pendiente"
        # Both flags are set on the mirror by app/jobs/planeacion_cruce.py —
        # tiene_survey by the existing Survey123 cascade, tiene_evaluacion by
        # its formulario ATC-20/stickers cross-reference (module docstring's
        # "Camino formulario ATC-20 / stickers"). datos_capturados spares the
        # frontend from repeating the `or` itself.
        tiene_survey = bool(mirror.get("tiene_survey"))
        tiene_evaluacion = bool(mirror.get("tiene_evaluacion"))
        puntos.append({
            "id": d.id,
            **data,
            "estado_seguimiento": ESTADO_SEGUIMIENTO_MAP.get(estado_asignacion, "pendiente"),
            # Read-only passthrough of the mirror's assignment (never written
            # by this router — ADR-4); mirror_id spares the frontend from
            # re-deriving the `solicitado_{id}` convention itself.
            "inspector_uid": mirror.get("inspector_uid"),
            "mirror_id": _mirror_doc_id(d.id),
            "tiene_survey": tiene_survey,
            "tiene_evaluacion": tiene_evaluacion,
            "datos_capturados": tiene_survey or tiene_evaluacion,
        })
    return JSONResponse({"ok": True, "puntos": puntos})


@router.patch("/puntos-solicitados/{id}")
def editar_punto_solicitado(
    id: str,
    request: Request,
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
            from google.cloud import firestore as _fs

            # Speed follow-up: stamp actualizado_en on the mirror write only
            # (never on `changes`, which is puntos_solicitados' own payload)
            # so the in-process planeacion_puntos snapshot's delta query can
            # find this admin edit.
            mirror_changes["actualizado_en"] = _fs.SERVER_TIMESTAMP
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
    if mirror_changes:
        # Only a mirror write bumps `actualizado_en` — nothing for the
        # snapshot's delta query to see otherwise.
        _mark_planeacion_snapshot_dirty(request)
    return JSONResponse({"ok": True, "id": id})


@router.delete("/puntos-solicitados/{id}")
def eliminar_punto_solicitado(
    id: str,
    request: Request,
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

    # The delta query can only ever find CHANGED docs, never GONE ones — the
    # mirror must be explicitly removed from the snapshot here.
    snapshot = getattr(request.app.state, "planeacion_puntos_snapshot", None)
    if snapshot is not None:
        try:
            snapshot.remove(_mirror_doc_id(id))
        except Exception:  # noqa: BLE001 - best-effort
            pass
    return JSONResponse({"ok": True, "id": id})


@router.get("/puntos-solicitados/buscar")
def buscar_puntos_solicitados(
    request: Request,
    q: str = "",
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    """ADR-1/ADR-2: admin-only search over `reportes.json`, then a
    per-page `puntos_contacto` join. Empty/whitespace `q` short-circuits to
    `resultados: []` BEFORE touching either source — never a full-dump,
    never an unneeded cache build. A `load_reportes()` failure (the
    primary source) maps to the same clean 502 every sibling route in this
    file uses; a `puntos_contacto` failure degrades to address-only rows
    for the page instead (see the enrichment block below)."""
    query = q.strip()
    if not query:
        return JSONResponse({"ok": True, "resultados": []})

    cache: BuscarCache = request.app.state.puntos_solicitados_buscar_cache
    try:
        rows = _joined_rows(cache)
    except Exception as exc:  # noqa: BLE001 — clean 502, never an unhandled 500
        logging.exception("Fallo buscando puntos solicitados")
        raise HTTPException(status_code=502, detail=f"No se pudo buscar puntos solicitados: {exc}") from exc

    resultados = _filter_rows(rows, query)

    # Contact is now joined AFTER the search filter (only for the top-20
    # page), never against the full reportes/cache set — this is what
    # replaces the old full-collection `puntos_contacto` scan. Tradeoff:
    # `_filter_rows` still lists `nombre_solicitante` as a searchable
    # field, but the cached rows always carry `None` there (see
    # `_joined_rows`), so searching BY requester name no longer matches
    # anything. Accepted cost of dropping the full-collection scan.
    if resultados:
        db = credentials.sismo().firestore
        try:
            refs = [
                db.collection(PUNTOS_CONTACTO_COLLECTION).document(f"atencionsismo_{row['registro_id']}")
                for row in resultados
            ]
            contacto_by_id = {
                snap.id.removeprefix("atencionsismo_"): (snap.to_dict() or {})
                for snap in db.get_all(refs)
                if snap.exists
            }
        except Exception:  # noqa: BLE001 — secondary source, degrade not 502
            logging.exception(
                "puntos_solicitados.buscar: fallo leyendo puntos_contacto "
                "para la página de resultados, degradando a resultados sin "
                "datos de contacto"
            )
            contacto_by_id = {}

        # Copy each row before mutating — `resultados` items came from the
        # SAME cached `rows` list (reused across requests via `BuscarCache`),
        # so writing enrichment in place would bleed one request's contact
        # data into every later request's cached rows.
        enriched = []
        for row in resultados:
            row = dict(row)
            contacto = contacto_by_id.get(row["registro_id"])
            if contacto:
                row["nombre_solicitante"] = contacto.get("nombre_solicitante")
                row["telefono_solicitante"] = contacto.get("telefono_solicitante")
            enriched.append(row)
        resultados = enriched

    return JSONResponse({"ok": True, "resultados": resultados})


@router.post("/geocode")
def geocode_route(
    body: GeocodeBody,
    claims: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """ADR-5: live proxy, any authenticated caller, backed by Nominatim (no
    API key). Transport failures (timeout/connection error/bad HTTP status)
    and malformed responses map to a clean 502 (upstream failure, never an
    address rejection); everything else comes back as `geocode()`'s own
    `{ok, accepted, ...}` shape unchanged."""
    try:
        result = geocode_service(body.direccion)
    except GeocodeTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(result)
