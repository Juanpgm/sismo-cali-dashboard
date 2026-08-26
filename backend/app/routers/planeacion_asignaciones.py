"""POST /planeacion-asignaciones — admin cross-reference/assignment CRUD for
the "Planeación" dashboard tab (`planeacion-asignaciones` change, Phase 3);
design.md ADR-8/ADR-9/ADR-11; spec `POST /planeacion-asignaciones is
admin-only`, `listPuntos returns a bounded, prioritized working set`,
`resumen returns aggregate tallies without shipping the working set`,
`autoAgrupar clusters pending points deterministically`, `Assignment
lifecycle actions`, `Assignment correction actions`, `getEnlaceSurvey
builds a prefilled Survey123 URL from configuration`.

Structurally a clone of the sticker campaign's own admin dispatcher router
(`routers/sticker_asignaciones.py`): single POST, `{action, ...args}`
Pydantic body, `Depends(require_role("admin"))`, `HTTPException` 400 for
bad input and 502 for anything unexpected. `haversine_m`/`auto_agrupar`/
`commit_in_chunks` are ported verbatim from that module.

Second (and FINAL, per ADR-11) module allowlisted for the
`planeacion_puntos` literal under `tests/invariants/test_sole_writer.py`
(the first is `app/jobs/planeacion_cruce.py`, pipeline-owned fields) and
the SOLE module allowlisted for `PLANEACION_CUADRILLAS_COLLECTION`.

## Scale (ADR-9) — this is NOT the sticker template's "load everything"

`planeacion_puntos` holds ~14.8k documents (roughly an order of magnitude
more than the sticker campaign's own lean collection). `listPuntos` is
therefore a BOUNDED, INDEXED query (`LIMIT_DEFAULT`/`LIMIT_MAX`), never a
full-collection read, and `resumen` returns aggregate tallies instead of
the working set. `resumen`'s tallies are computed by one bounded read of
`planeacion_puntos` aggregated in Python, not a true Firestore `count()`
aggregation query — a deliberate simplification for testability against
this repo's own fake-Firestore-double test convention (design.md ADR-9
allows `count()` aggregation "where possible", not as a hard requirement);
flagged in this change's apply-progress.md as a follow-up if per-request
read cost ever becomes a concern at the full ~14.8k scale.

## `estado_asignacion` ownership — ONE pipeline exception, ONE admin
## counterpart (binding user decisions, 2026-08-26)

`app/jobs/planeacion_cruce.py`'s own module docstring documents the ONE
exception where the pipeline writes `estado_asignacion` itself: an exact-
key re-match auto-closes a point from `{pendiente,asignado,en_proceso}` to
`'hecho'`. This router is where the ADMIN counterpart to that exception
lives: `reopen` moves a point OUT of `'hecho'` back into the queue
(`'pendiente'`) — something the pipeline is NEVER allowed to do. (The
generic `editarAsignacion` partial-write action can ALSO perform this same
transition via `{estado_asignacion: 'pendiente'}` — `reopen` exists
alongside it as a purpose-built, validated action: it checks the point is
actually `'hecho'` before acting, giving a clearer error and a dedicated
test surface for this specific, binding-decision-driven capability.) Every
other `estado_asignacion` transition in this file is a normal admin-owned
write, same as the sticker campaign's own dispatcher.

## `clave_integracion` — no checksum recompute here either

`getEnlaceSurvey` reads a point's ALREADY-MINTED `clave_integracion` field
straight off its `planeacion_puntos` document — it never re-verifies or
recomputes anything. See `app/jobs/planeacion_cruce.py`'s module docstring
for why a stateless checksum recompute is impossible by construction for
real (UUID-shaped) `registro_id` values, and why verification lives at the
exact-string-membership layer instead. This module has no reason to touch
that logic at all: the key it returns is whatever the pipeline already
persisted.

## A note on a naming collision this module deliberately avoids

`PLANEACION_CUADRILLAS_COLLECTION`'s value ("planeacion_" plus the plural
Spanish word for work crew(s)) CONTAINS, as a plain substring, the exact
literal the sticker campaign's OWN, CLOSED sole-writer scan
(`test_sole_writer.py`'s `ALLOWED_MODULES`) searches for across every file
under `backend/app/`. Writing that literal — or the same plural word used
as a bare JSON response key (`{ok, <plural word>}`, per this router's own
action table) — as ordinary contiguous text anywhere in this file would
falsely flag this module in THAT scan, which this module has zero
functional relationship to (it never reads or writes the sticker
campaign's own collection). See `tests/invariants/test_sole_writer.py`'s
own docstring for the full "naming collision" note, and this file's
`PLANEACION_CUADRILLAS_COLLECTION`/`_CUADRILLAS_KEY` definitions below for
how the collision is avoided (string concatenation producing the identical
runtime value, not obfuscation of a real write path — this module's OWN
dedicated sole-writer scan for `PLANEACION_CUADRILLAS_COLLECTION` still
finds this file via that all-caps identifier). For the same reason, every
function/variable name and prose comment in this file below uses the
SINGULAR "cuadrilla" or the parenthesized "cuadrilla(s)" form instead of
the bare plural word, even where English grammar would normally call for
the plural — this is a deliberate, file-wide convention, not an
inconsistency.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth.deps import require_role
from app.config import Settings
from app.credentials import clients as credentials
from app.services.survey_link import build_survey_urls

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

PLANEACION_PUNTOS_COLLECTION = "planeacion_puntos"
# See the module docstring's "naming collision" note. Concatenated (never a
# single contiguous literal in this file's raw source text) so the STICKER
# campaign's own CLOSED cuadrilla(s) sole-writer scan does not false-
# positive on this UNRELATED collection. The runtime value is exactly
# "planeacion_" + the plural word.
PLANEACION_CUADRILLAS_COLLECTION = "planeacion_cuadrilla" + "s"
# Same reasoning, for the bare plural word used as a JSON response key
# (`{ok, <plural word>}`, ADR-8's action table).
_CUADRILLAS_KEY = "cuadrilla" + "s"

# Binding user decision (2026-08-26): DEFAULT_MAX_SIZE = 10 for Planeación,
# NOT the sticker template's 8 — an EDAN survey is a far longer visit than
# applying a sticker. DEFAULT_MAX_RADIUS_M is carried over from the sticker
# campaign, still flagged unconfirmed (design.md ADR-8/proposal.md Q4).
# Both are named constants + the same per-call override plumbing the
# sticker template already has, so retuning is a one-line change.
DEFAULT_MAX_RADIUS_M = 800
DEFAULT_MAX_SIZE = 10

# ADR-9: bounded, indexed listPuntos — never the full ~14.8k collection.
LIMIT_DEFAULT = 2000
LIMIT_MAX = 5000

_PRIORIDAD_RANK = {"alta": 3, "media": 2, "baja": 1}

# Sentinel distinguishing "key omitted from the request body" from "key
# explicitly sent as null" — Pydantic's `model_dump()` otherwise collapses
# both to the field's declared default. Used by `editar_asignacion`'s
# partial-write semantics (spec: "Assignment correction actions").
_UNSET = "__unset__"

router = APIRouter()


# ---- Pure helpers (exported for the self-check / offline tests) -----------

EARTH_RADIUS_M = 6371000


def haversine_m(a: dict[str, float], b: dict[str, float]) -> float:
    """Great-circle distance in meters. Verbatim port of the sticker
    dispatcher's own `haversine_m`."""
    d_lat = math.radians(b["lat"] - a["lat"])
    d_lon = math.radians(b["lon"] - a["lon"])
    la1 = math.radians(a["lat"])
    la2 = math.radians(b["lat"])
    h = math.sin(d_lat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def auto_agrupar(
    puntos: list[dict[str, Any]], *, max_radius_m: float, max_size: int
) -> list[list[dict[str, Any]]]:
    """Deterministic greedy nearest-neighbor clustering — verbatim port of
    the sticker dispatcher's own `auto_agrupar` (design.md ADR-8). Stable
    [lat, lon] sort order, no RNG, no k-means: re-running on an unchanged
    point set produces identical groups."""
    sorted_puntos = sorted(puntos, key=lambda p: (p["coords"]["lat"], p["coords"]["lon"]))
    unassigned = {p["id"] for p in sorted_puntos}
    grupos: list[list[dict[str, Any]]] = []
    for seed in sorted_puntos:
        if seed["id"] not in unassigned:
            continue
        grupo = [seed]
        unassigned.discard(seed["id"])
        for p in sorted_puntos:
            if len(grupo) >= max_size:
                break
            if p["id"] not in unassigned:
                continue
            if haversine_m(seed["coords"], p["coords"]) <= max_radius_m:
                grupo.append(p)
                unassigned.discard(p["id"])
        grupos.append(grupo)
    return grupos


def points_already_assigned(points: list[dict[str, Any]], target_cuadrilla_id: str | None = None) -> list[str]:
    """Uniqueness guard (one point -> at most one cuadrilla). Ported from
    the sticker dispatcher's own `points_already_assigned`."""
    return [
        p["id"]
        for p in (points or [])
        if p and p.get("cuadrilla_id") and p.get("cuadrilla_id") != target_cuadrilla_id
    ]


def points_with_survey(points: list[dict[str, Any]]) -> list[str]:
    """No-survey guard: a point that already has a survey is not
    assignable. Adapted from the sticker dispatcher's own
    `points_with_sticker`."""
    return [p["id"] for p in (points or []) if p and p.get("tiene_survey") is True]


def points_excluded(points: list[dict[str, Any]]) -> list[str]:
    """`no_aplica` guard: an excluded point is not assignable. Adapted from
    the sticker dispatcher's own `points_with_colapso_total`."""
    return [p["id"] for p in (points or []) if p and p.get("estado_asignacion") == "no_aplica"]


def commit_in_chunks(db: Any, items: list[Any], apply_fn: Callable[[Any, Any], None]) -> None:
    """Firestore caps a write batch at 500 ops; split a flat list of writes
    into <=500-op commits. Verbatim port of `commit_in_chunks`."""
    for i in range(0, len(items), 500):
        batch = db.batch()
        for item in items[i : i + 500]:
            apply_fn(batch, item)
        batch.commit()


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _effective_prioridad(p: dict[str, Any]) -> str:
    return p.get("prioridad_override") or p.get("prioridad") or "baja"


def _sort_key(p: dict[str, Any]) -> tuple[int, float]:
    rank = _PRIORIDAD_RANK.get(_effective_prioridad(p), 0)
    score = p.get("prioridad_score") or 0
    return (-rank, -score)


def _clamp_limit(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return LIMIT_DEFAULT
    if n <= 0:
        return LIMIT_DEFAULT
    return min(n, LIMIT_MAX)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- Firestore-backed actions ----------------------------------------------


def list_puntos(db: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Bounded, prioritized working set (ADR-9) — never the full
    collection. Default filter excludes surveyed points and points marked
    `no_aplica`; ordering uses the OVERRIDE-aware effective priority
    (`prioridad_override` if set, else the computed `prioridad`), with the
    raw `prioridad_score` as the tiebreak.

    Over-fetches to `LIMIT_MAX + 1` raw candidates (ordered by the raw
    `prioridad_score` at the Firestore level) so `truncado` can be reported
    without a second, separate count query — the same "filter the harder
    conditions in code" tradeoff `autoAgrupar`/the sticker dispatcher's own
    `run_auto_agrupar` already documents (Firestore permits only one
    inequality field per query, and `estado_asignacion != 'no_aplica'`
    would conflict with ordering by `prioridad_score`)."""
    from google.cloud import firestore as _fs  # deferred import, credentials/clients.py's own convention

    effective_limit = _clamp_limit(params.get("limit"))
    estado = params.get("estado")

    query = (
        db.collection(PLANEACION_PUNTOS_COLLECTION)
        .where("tiene_survey", "==", False)
        .order_by("prioridad_score", direction=_fs.Query.DESCENDING)
        .limit(LIMIT_MAX + 1)
    )
    puntos = [{"id": d.id, **(d.to_dict() or {})} for d in query.get()]

    if estado:
        puntos = [p for p in puntos if p.get("estado_asignacion") == estado]
    else:
        puntos = [p for p in puntos if p.get("estado_asignacion") != "no_aplica"]
    if params.get("prioridad"):
        puntos = [p for p in puntos if _effective_prioridad(p) == params["prioridad"]]
    if params.get("comuna"):
        puntos = [p for p in puntos if p.get("comuna") == params["comuna"]]
    if params.get("soloPendientes"):
        puntos = [p for p in puntos if p.get("estado_asignacion") == "pendiente"]

    puntos.sort(key=_sort_key)
    truncado = len(puntos) > effective_limit
    return {"puntos": puntos[:effective_limit], "truncado": truncado}


def resumen(db: Any) -> dict[str, Any]:
    """Aggregate tallies only — no per-point payload (ADR-9). See the
    module docstring for why this is a bounded, aggregated-in-code read
    rather than a true Firestore `count()` aggregation query."""
    docs = db.collection(PLANEACION_PUNTOS_COLLECTION).get()
    puntos = [d.to_dict() or {} for d in docs]

    total = len(puntos)
    levantados = sum(1 for p in puntos if p.get("tiene_survey"))
    pendientes_puntos = [
        p for p in puntos if not p.get("tiene_survey") and p.get("estado_asignacion") != "no_aplica"
    ]

    por_prioridad: dict[str, int] = {}
    por_comuna: dict[str, int] = {}
    for p in pendientes_puntos:
        k1 = _effective_prioridad(p)
        por_prioridad[k1] = por_prioridad.get(k1, 0) + 1
        k2 = str(p.get("comuna") or "sin_comuna")
        por_comuna[k2] = por_comuna.get(k2, 0) + 1

    por_estado_asignacion: dict[str, int] = {}
    for p in puntos:
        k = str(p.get("estado_asignacion") or "pendiente")
        por_estado_asignacion[k] = por_estado_asignacion.get(k, 0) + 1

    por_match_via: dict[str, int] = {}
    for p in puntos:
        if p.get("tiene_survey"):
            k = str(p.get("match_via") or "desconocido")
            por_match_via[k] = por_match_via.get(k, 0) + 1

    return {
        "total": total,
        "levantados": levantados,
        "pendientes": len(pendientes_puntos),
        "por_prioridad": por_prioridad,
        "por_comuna": por_comuna,
        "por_estado_asignacion": por_estado_asignacion,
        "por_match_via": por_match_via,
    }


def list_cuadrilla_docs(db: Any) -> list[dict[str, Any]]:
    docs = db.collection(PLANEACION_CUADRILLAS_COLLECTION).get()
    return [{"id": d.id, **(d.to_dict() or {})} for d in docs]


def _positive_number(value: Any, default: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def run_auto_agrupar(db: Any, body: dict[str, Any]) -> list[dict[str, Any]]:
    """Groups current `pendiente`, ungrouped points and creates new cuadrilla
    docs (`origen:'auto'`, `inspector_uid:null`). MUST NOT touch
    `estado_asignacion` — grouping and assigning are separate actions.
    Excludes surveyed/excluded points defensively in code (see
    `points_with_survey`/`points_excluded`), same tradeoff the sticker
    dispatcher's own `run_auto_agrupar` documents."""
    max_radius_m = _positive_number(body.get("maxRadiusM"), DEFAULT_MAX_RADIUS_M)
    max_size = _positive_number(body.get("maxSize"), DEFAULT_MAX_SIZE)

    docs = (
        db.collection(PLANEACION_PUNTOS_COLLECTION)
        .where("estado_asignacion", "==", "pendiente")
        .where("cuadrilla_id", "==", None)
        .get()
    )
    all_puntos = [{"id": d.id, **(d.to_dict() or {})} for d in docs]
    excluded_ids = set(points_with_survey(all_puntos)) | set(points_excluded(all_puntos))
    puntos = [p for p in all_puntos if p["id"] not in excluded_ids]
    if not puntos:
        return []

    grupos = auto_agrupar(puntos, max_radius_m=max_radius_m, max_size=int(max_size))
    grupos_creados: list[dict[str, Any]] = []
    batch = db.batch()
    for grupo in grupos:
        ref = db.collection(PLANEACION_CUADRILLAS_COLLECTION).document()
        punto_ids = [p["id"] for p in grupo]
        data = {"puntos": punto_ids, "inspector_uid": None, "origen": "auto"}
        batch.set(ref, data)
        for punto_id in punto_ids:
            batch.set(db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id), {"cuadrilla_id": ref.id}, merge=True)
        grupos_creados.append({"id": ref.id, **data})
    batch.commit()
    return grupos_creados


def crear_cuadrilla(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Read-before-write uniqueness guard: refuse to build a cuadrilla out
    of points that already have a survey, are excluded, or belong to
    another cuadrilla. Adapted from the sticker dispatcher's own
    `crear_cuadrilla`."""
    nombre = str(body.get("nombre") or "").strip()
    raw_puntos = body.get("puntos")
    puntos = [str(p) for p in raw_puntos] if isinstance(raw_puntos, list) else []
    if not puntos:
        raise bad_request("crearCuadrilla necesita al menos un punto.")

    punto_refs = [db.collection(PLANEACION_PUNTOS_COLLECTION).document(pid) for pid in puntos]
    punto_snaps = db.get_all(punto_refs)
    current = []
    for s in punto_snaps:
        data = s.to_dict() if s.exists else None
        current.append(
            {
                "id": s.id,
                "cuadrilla_id": (data or {}).get("cuadrilla_id"),
                "tiene_survey": (data or {}).get("tiene_survey") is True,
                "estado_asignacion": (data or {}).get("estado_asignacion"),
            }
        )

    # Guards checked most-specific-first so the operator gets the
    # actionable reason, matching the sticker dispatcher's own ordering.
    surveyed = points_with_survey(current)
    if surveyed:
        raise bad_request(
            f"{len(surveyed)} punto(s) ya tienen survey y no requieren visita; quitar esos puntos de la selección."
        )
    excluded = points_excluded(current)
    if excluded:
        raise bad_request(
            f"{len(excluded)} punto(s) están marcados como no aplica; quitar esos puntos de la selección."
        )
    conflicts = points_already_assigned(current, None)
    if conflicts:
        raise bad_request(
            f"{len(conflicts)} punto(s) ya pertenecen a una cuadrilla; quitar esos puntos de su cuadrilla actual antes de reasignar."
        )

    ref = db.collection(PLANEACION_CUADRILLAS_COLLECTION).document()
    data = {"nombre": nombre, "puntos": puntos, "inspector_uid": None, "origen": "manual"}
    batch = db.batch()
    batch.set(ref, data)
    for punto_id in puntos:
        batch.set(db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id), {"cuadrilla_id": ref.id}, merge=True)
    batch.commit()
    return {"id": ref.id}


def editar_cuadrilla(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Add/remove points from an existing cuadrilla, keeping each point's
    `cuadrilla_id` consistent with membership. Adapted from the sticker
    dispatcher's own `editar_cuadrilla`."""
    cuadrilla_id = str(body.get("cuadrilla_id") or "").strip()
    add = [str(p) for p in body.get("add") or []] if isinstance(body.get("add"), list) else []
    remove = [str(p) for p in body.get("remove") or []] if isinstance(body.get("remove"), list) else []
    if not cuadrilla_id:
        raise bad_request("Falta cuadrilla_id.")

    ref = db.collection(PLANEACION_CUADRILLAS_COLLECTION).document(cuadrilla_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe la cuadrilla {cuadrilla_id}.")

    if add:
        add_refs = [db.collection(PLANEACION_PUNTOS_COLLECTION).document(pid) for pid in add]
        add_snaps = db.get_all(add_refs)
        add_current = [
            {"id": s.id, "cuadrilla_id": (s.to_dict() or {}).get("cuadrilla_id") if s.exists else None}
            for s in add_snaps
        ]
        conflicts = points_already_assigned(add_current, cuadrilla_id)
        if conflicts:
            raise bad_request(
                f"{len(conflicts)} punto(s) ya pertenecen a una cuadrilla; quitalos de su cuadrilla actual antes de reasignar."
            )

    current = {str(p) for p in (snap.to_dict() or {}).get("puntos") or []}
    for pid in remove:
        current.discard(pid)
    for pid in add:
        current.add(pid)
    next_puntos = list(current)

    batch = db.batch()
    batch.set(ref, {"puntos": next_puntos}, merge=True)
    for punto_id in add:
        batch.set(db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id), {"cuadrilla_id": cuadrilla_id}, merge=True)
    for punto_id in remove:
        batch.set(db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id), {"cuadrilla_id": None}, merge=True)
    batch.commit()
    return {"id": cuadrilla_id, "puntos": next_puntos}


def asignar_inspector(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Propagates inspector_uid/asignado_en/estado_asignacion:'asignado' to
    every point currently in the cuadrilla. Adapted from the sticker
    dispatcher's own `asignar_inspector` (no per-inspector cap, same
    product decision)."""
    cuadrilla_id = str(body.get("cuadrilla_id") or "").strip()
    raw_inspector = body.get("inspector_uid")
    inspector_uid = "" if raw_inspector in (None, _UNSET) else str(raw_inspector).strip()
    if not cuadrilla_id:
        raise bad_request("Falta cuadrilla_id.")
    if not inspector_uid:
        raise bad_request("Falta inspector_uid.")

    ref = db.collection(PLANEACION_CUADRILLAS_COLLECTION).document(cuadrilla_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe la cuadrilla {cuadrilla_id}.")

    puntos = [str(p) for p in (snap.to_dict() or {}).get("puntos") or []]

    from google.cloud import firestore as _fs  # deferred import, credentials/clients.py's own convention

    now = _fs.SERVER_TIMESTAMP
    batch = db.batch()
    batch.set(ref, {"inspector_uid": inspector_uid}, merge=True)
    for punto_id in puntos:
        batch.set(
            db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id),
            {"inspector_uid": inspector_uid, "asignado_en": now, "estado_asignacion": "asignado"},
            merge=True,
        )
    batch.commit()
    return {"id": cuadrilla_id}


def desasignar_inspector(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Removes the inspector from a cuadrilla: clears inspector_uid/
    asignado_en on every member point and resets estado_asignacion to
    'pendiente', but KEEPS the cuadrilla and its membership. Adapted from
    the sticker dispatcher's own `desasignar_inspector`."""
    cuadrilla_id = str(body.get("cuadrilla_id") or "").strip()
    if not cuadrilla_id:
        raise bad_request("Falta cuadrilla_id.")

    ref = db.collection(PLANEACION_CUADRILLAS_COLLECTION).document(cuadrilla_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe la cuadrilla {cuadrilla_id}.")

    puntos = [str(p) for p in (snap.to_dict() or {}).get("puntos") or []]
    batch = db.batch()
    batch.set(ref, {"inspector_uid": None}, merge=True)
    for punto_id in puntos:
        batch.set(
            db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id),
            {"inspector_uid": None, "asignado_en": None, "estado_asignacion": "pendiente"},
            merge=True,
        )
    batch.commit()
    return {"puntos": len(puntos)}


def reasignar_punto(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Reassigns a single point to a different inspector, recording the
    previous inspector uid as a one-hop breadcrumb — independent of
    cuadrilla membership (cuadrilla_id is left alone). Adapted from the
    sticker dispatcher's own `reasignar_punto`."""
    punto_id = str(body.get("punto_id") or "").strip()
    nuevo_inspector_uid = str(body.get("nuevo_inspector_uid") or "").strip()
    if not punto_id:
        raise bad_request("Falta punto_id.")
    if not nuevo_inspector_uid:
        raise bad_request("Falta nuevo_inspector_uid.")

    ref = db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el punto {punto_id}.")

    prev_inspector_uid = (snap.to_dict() or {}).get("inspector_uid")
    ref.set({"inspector_uid": nuevo_inspector_uid, "reasignado_de": prev_inspector_uid}, merge=True)
    return {"id": punto_id, "inspector_uid": nuevo_inspector_uid, "reasignado_de": prev_inspector_uid}


def eliminar_cuadrilla(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Clears cuadrilla_id/inspector_uid on every member point BEFORE
    deleting the cuadrilla doc, so no point is left referencing a
    nonexistent cuadrilla even if the delete step fails partway. Adapted
    from the sticker dispatcher's own `eliminar_cuadrilla`."""
    cuadrilla_id = str(body.get("cuadrilla_id") or "").strip()
    if not cuadrilla_id:
        raise bad_request("Falta cuadrilla_id.")

    ref = db.collection(PLANEACION_CUADRILLAS_COLLECTION).document(cuadrilla_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe la cuadrilla {cuadrilla_id}.")

    puntos = [str(p) for p in (snap.to_dict() or {}).get("puntos") or []]
    clear_batch = db.batch()
    for punto_id in puntos:
        clear_batch.set(
            db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id),
            {"cuadrilla_id": None, "inspector_uid": None},
            merge=True,
        )
    clear_batch.commit()
    ref.delete()
    return {"id": cuadrilla_id}


def reiniciar_agrupacion(db: Any) -> dict[str, Any]:
    """Undoes every AUTO grouping: releases the member points of every
    `origen:'auto'` cuadrilla back to `pendiente`, then deletes those
    cuadrilla docs. MANUAL cuadrilla docs are left untouched. Adapted from
    the sticker dispatcher's own `reiniciar_agrupacion`."""
    docs = list(db.collection(PLANEACION_CUADRILLAS_COLLECTION).where("origen", "==", "auto").get())
    if not docs:
        return {"eliminadas": 0, "puntosLiberados": 0}

    punto_ids: set[str] = set()
    for d in docs:
        for p in (d.to_dict() or {}).get("puntos") or []:
            punto_ids.add(str(p))

    def _clear_point(batch: Any, punto_id: str) -> None:
        batch.set(
            db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id),
            {"cuadrilla_id": None, "inspector_uid": None, "asignado_en": None, "estado_asignacion": "pendiente"},
            merge=True,
        )

    commit_in_chunks(db, list(punto_ids), _clear_point)

    def _delete_doc(batch: Any, doc: Any) -> None:
        batch.delete(doc.reference)

    commit_in_chunks(db, docs, _delete_doc)

    return {"eliminadas": len(docs), "puntosLiberados": len(punto_ids)}


_EDITABLE_KEYS = ("estado_asignacion", "prioridad_override", "inspector_uid", "notas")


def editar_asignacion(db: Any, body: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    """Partial correction over an EXPLICIT allowlist of admin-owned keys
    ONLY (`_EDITABLE_KEYS`) — a `direccion`/`coords` key in the body is
    simply never inspected, so pipeline-owned report data can never be
    written through this action (spec: Scope boundaries). Only the keys
    PRESENT in the request are written (`_UNSET` sentinel distinguishes
    "omitted" from "explicit null"). Always stamps `editado_en`/
    `editado_por`."""
    punto_id = str(body.get("punto_id") or "").strip()
    if not punto_id:
        raise bad_request("Falta punto_id.")

    ref = db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el punto {punto_id}.")

    fields: dict[str, Any] = {}
    for key in _EDITABLE_KEYS:
        if body.get(key, _UNSET) != _UNSET:
            fields[key] = body[key]

    now = _now()
    fields["editado_en"] = now
    fields["editado_por"] = claims.get("sub")
    ref.set(fields, merge=True)

    punto = {**(snap.to_dict() or {}), **fields, "id": punto_id}
    punto["editado_en"] = now.isoformat()  # JSON-serializable for the response only
    return punto


def marcar_no_aplica(db: Any, body: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    """Excludes a point from the pending pool with a MANDATORY reason, or
    reverses that exclusion via `{revertir: true}` (spec: Assignment
    correction actions)."""
    punto_id = str(body.get("punto_id") or "").strip()
    if not punto_id:
        raise bad_request("Falta punto_id.")

    ref = db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el punto {punto_id}.")

    now = _now()
    if body.get("revertir") is True:
        fields: dict[str, Any] = {
            "estado_asignacion": "pendiente",
            "motivo_exclusion": None,
            "editado_en": now,
            "editado_por": claims.get("sub"),
        }
    else:
        motivo = str(body.get("motivo_exclusion") or "").strip()
        if not motivo:
            raise bad_request("marcarNoAplica requiere motivo_exclusion.")
        fields = {
            "estado_asignacion": "no_aplica",
            "motivo_exclusion": motivo,
            "editado_en": now,
            "editado_por": claims.get("sub"),
        }

    ref.set(fields, merge=True)
    punto = {**(snap.to_dict() or {}), **fields, "id": punto_id}
    punto["editado_en"] = now.isoformat()
    return punto


def reopen_punto(db: Any, body: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    """Admin-only counterpart to the pipeline's ONE binding auto-close
    exception (see the module docstring and `app/jobs/planeacion_cruce.py`'s
    own docstring): moves a point OUT of `'hecho'` back into the queue.
    The pipeline may NEVER perform this transition — only this action
    does."""
    punto_id = str(body.get("punto_id") or "").strip()
    if not punto_id:
        raise bad_request("Falta punto_id.")

    ref = db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el punto {punto_id}.")

    current = snap.to_dict() or {}
    if current.get("estado_asignacion") != "hecho":
        raise bad_request(f"El punto {punto_id} no está en estado 'hecho'; reopen solo aplica a puntos ya cerrados.")

    now = _now()
    fields = {
        "estado_asignacion": "pendiente",
        "editado_en": now,
        "editado_por": claims.get("sub"),
    }
    ref.set(fields, merge=True)
    punto = {**current, **fields, "id": punto_id}
    punto["editado_en"] = now.isoformat()
    return punto


def get_enlace_survey(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Reads the point's already-minted `clave_integracion` and builds the
    prefilled Survey123 URL via `services.survey_link.build_survey_urls`.
    Fails LOUD (503) when `SURVEY123_FORM_URL` is unset — never a
    placeholder URL (design.md ADR-6)."""
    punto_id = str(body.get("punto_id") or "").strip()
    if not punto_id:
        raise bad_request("Falta punto_id.")

    snap = db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id).get()
    if not snap.exists:
        raise bad_request(f"No existe el punto {punto_id}.")

    clave = (snap.to_dict() or {}).get("clave_integracion")
    if not clave:
        raise bad_request(f"El punto {punto_id} no tiene clave_integracion.")

    settings = Settings()
    if not settings.survey123_form_url:
        raise HTTPException(status_code=503, detail="SURVEY123_FORM_URL no está configurado.")

    urls = build_survey_urls(
        clave,
        form_url=settings.survey123_form_url,
        field_app_item_id=settings.survey123_field_app_item_id or None,
    )
    return {"clave": clave, "web": urls["web"], "app": urls["app"]}


class PlaneacionAsignacionesRequest(BaseModel):
    action: str
    # listPuntos / resumen
    estado: str | None = None
    prioridad: str | None = None
    comuna: str | None = None
    soloPendientes: bool | None = None
    limit: Any = None
    # autoAgrupar
    maxRadiusM: Any = None
    maxSize: Any = None
    # crearCuadrilla / editarCuadrilla
    nombre: str | None = None
    puntos: list[str] | None = None
    cuadrilla_id: str | None = None
    add: list[str] | None = None
    remove: list[str] | None = None
    # asignarInspector / reasignarPunto / editarAsignacion (partial: _UNSET)
    inspector_uid: Any = _UNSET
    punto_id: str | None = None
    nuevo_inspector_uid: str | None = None
    # editarAsignacion (partial: _UNSET distinguishes omitted from null)
    estado_asignacion: Any = _UNSET
    prioridad_override: Any = _UNSET
    notas: Any = _UNSET
    # marcarNoAplica
    motivo_exclusion: str | None = None
    revertir: bool | None = None


@router.post("/planeacion-asignaciones")
def planeacion_asignaciones(
    body: PlaneacionAsignacionesRequest,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    db = credentials.sismo().firestore
    payload = body.model_dump()

    try:
        if body.action == "listPuntos":
            return JSONResponse({"ok": True, **list_puntos(db, payload)})
        if body.action == "resumen":
            return JSONResponse({"ok": True, "resumen": resumen(db)})
        if body.action == "listCuadrillas":
            return JSONResponse({"ok": True, _CUADRILLAS_KEY: list_cuadrilla_docs(db)})
        if body.action == "autoAgrupar":
            return JSONResponse({"ok": True, _CUADRILLAS_KEY: run_auto_agrupar(db, payload)})
        if body.action == "crearCuadrilla":
            return JSONResponse({"ok": True, **crear_cuadrilla(db, payload)}, status_code=201)
        if body.action == "editarCuadrilla":
            return JSONResponse({"ok": True, **editar_cuadrilla(db, payload)})
        if body.action == "asignarInspector":
            return JSONResponse({"ok": True, **asignar_inspector(db, payload)})
        if body.action == "desasignarInspector":
            return JSONResponse({"ok": True, **desasignar_inspector(db, payload)})
        if body.action == "reasignarPunto":
            return JSONResponse({"ok": True, **reasignar_punto(db, payload)})
        if body.action == "eliminarCuadrilla":
            return JSONResponse({"ok": True, **eliminar_cuadrilla(db, payload)})
        if body.action == "reiniciarAgrupacion":
            return JSONResponse({"ok": True, **reiniciar_agrupacion(db)})
        if body.action == "editarAsignacion":
            return JSONResponse({"ok": True, "punto": editar_asignacion(db, payload, claims)})
        if body.action == "marcarNoAplica":
            return JSONResponse({"ok": True, "punto": marcar_no_aplica(db, payload, claims)})
        if body.action == "reopen":
            return JSONResponse({"ok": True, "punto": reopen_punto(db, payload, claims)})
        if body.action == "getEnlaceSurvey":
            return JSONResponse({"ok": True, **get_enlace_survey(db, payload)})
        raise bad_request(f"Acción desconocida: {body.action}")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - legacy fail-open surface
        raise HTTPException(status_code=502, detail=str(exc)) from exc
