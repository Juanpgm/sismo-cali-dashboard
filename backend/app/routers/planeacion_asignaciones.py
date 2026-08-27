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

## A note on a name this module shares with the sticker campaign

This module's collections are `planeacion_puntos` and
`planeacion_cuadrillas`, and its `listCuadrillas`/`autoAgrupar` actions
answer `{"ok": true, "cuadrillas": [...]}` — deliberately the same payload
shape the sticker endpoint uses, so the Planeación tab can reuse the same
frontend reading pattern. It has ZERO functional relationship to the
sticker campaign's own `cuadrillas` collection: it never reads or writes
it. Its own two collections are guarded by their own two independent
sole-writer allowlists in `tests/invariants/test_sole_writer.py`.

That scan is deliberately COARSE — it flags the bare word anywhere under
`backend/app/`, on the principle "if the word appears, prove it is fine" —
so this file is listed in the sticker `ALLOWED_MODULES` set with an
explicit annotation saying the only hit is the JSON response key. That
annotation, not a code change, is the resolution.

Recorded because the first implementation did the opposite: it wrote the
constant as `"planeacion_cuadrilla" + "s"` so the word never appeared
contiguously, and adopted a file-wide convention of avoiding the plural in
prose. That passes the scan while defeating its purpose, and teaches the
next author that an inconvenient tripwire is something to slip past.
Reverted 2026-08-26 along with a real fix to the scan itself, which now
matches whole identifiers — so `planeacion_cuadrillas` no longer
false-positives against the sticker campaign's closed list at all.

## `grupos-inspectores` change (2026-08-26) — groups of INSPECTORS

A NEW concept, distinct from `planeacion_cuadrillas` above: a group of
PEOPLE (`grupos_inspectores/{id}`, `{nombre, miembros: [uid,...], activo,
creado_en, creado_por}`), not a group of points. Shared by BOTH campaigns
(binding decision 1) — CRUD lives exclusively here (`listGrupos`/
`crearGrupo`/`editarGrupo`/`eliminarGrupo`) because group-of-people
membership is campaign-agnostic, and one canonical owner avoids a split
CRUD surface. `routers/inspector_asignaciones.py` READS this collection
(own-uid group-membership lookup, never writes it); `routers/
sticker_asignaciones.py` also READS it (validates a `grupo_id` before its
OWN `asignarGrupoAPuntos` writes it onto a `sticker_matches` doc). All
three modules are allowlisted under `tests/invariants/test_sole_writer.py`'s
`ALLOWED_MODULES_GRUPOS_INSPECTORES`.

`asignarGrupoAPuntos`/`desasignarGrupo` HERE write `grupo_id` ONLY onto
`planeacion_puntos` docs — this router's own, already-owned collection.
They deliberately do NOT also reach into `sticker_matches`: that would
mean writing a field this router doesn't otherwise own into a DIFFERENT
campaign's core collection, undermining the same per-campaign collection
ownership discipline this file's own "A note on a name this module shares"
section above describes ("ZERO functional relationship" to the sticker
campaign's own collections). Instead, `sticker_asignaciones.py` — already
the FOURTH and FINAL allowlisted WRITER of `sticker_matches` — gets its
OWN mirrored `asignarGrupoAPuntos`/`desasignarGrupo` pair for that
collection. Group ASSIGNMENT is therefore per-campaign (two small, focused
actions, one per router, each writing only the collection it already
owns), while group MEMBERSHIP (people) stays single-owned here. Net
effect for the end user is identical to a single cross-campaign action:
"assign this group to these points" always resolves to the right
collection, because the ADMIN UI (Planeación tab, `web/js/planeacion.js`)
always calls the same-router action for the points it is already looking
at.

`eliminarGrupo`'s orphan-prevention guard (decision: REFUSE deletion while
ANY point still references the group, rather than silently clearing
`grupo_id` — see the function's own docstring for why) must check BOTH
collections, since the group itself is shared. Reading (never writing)
`sticker_matches` for that count is why this module is ALSO already listed
in the sticker campaign's own `ALLOWED_MODULES` set above (originally for
the unrelated `cuadrillas` JSON-key false positive) — a second, genuinely
different, honestly-flagged reason to be there, same "legitimate new
reader, flagged rather than hidden" precedent `routers/sticker_status.py`
and `app/jobs/planeacion_cruce.py` already established for their own
collections.

## `grupos-inspectores` change, follow-up batch (2026-08-26) — member cap
## + vehicles

Two ADDITIONAL binding requirements folded into the SAME collection this
change already owns, not a separate slice:

1. **`MAX_MIEMBROS_GRUPO = 4`** — a group has AT MOST 4 members. Enforced
   SERVER-SIDE ONLY, in both `crear_grupo` and `editar_grupo` — a
   client-side check is not a boundary. `editar_grupo` checks the
   RESULTING membership (current − remove + add, de-duplicated) BEFORE
   writing anything, so a rejection leaves the group completely
   unchanged — no partial `add`. De-duplication also means re-adding an
   already-current member never counts twice toward the cap.

2. **`vehiculos/{id}`** (`{placa, tipo, activo, creado_en, creado_por}`,
   `placa` unique) — "cada grupo sale en un vehículo". CRUD
   (`listVehiculos`/`crearVehiculo`/`editarVehiculo`/`eliminarVehiculo`)
   plus `asignarVehiculoAGrupo`/`desasignarVehiculo` live HERE, same
   single-owner reasoning as `grupos_inspectores` itself (campaign-
   agnostic, one canonical CRUD surface). `eliminarVehiculo` follows the
   SAME orphan-prevention discipline already chosen for `eliminarGrupo`:
   REFUSE deletion while any `grupos_inspectores` doc still references the
   vehicle, naming the group(s), rather than silently clearing it —
   consistency with the decision already made for the sibling collection,
   not a new one.

   **Double-booking decision**: "one vehicle -> at most one group at a
   time" is enforced by `asignar_vehiculo_a_grupo` REJECTING (400, naming
   the OTHER group that already holds it) rather than silently moving the
   vehicle. Chosen for the same reason `eliminarGrupo`/`crear_cuadrilla`
   already reject instead of silently mutating state elsewhere in this
   file: an admin should see exactly what is already using a resource and
   act deliberately (`desasignarVehiculo` first), not have it silently
   unbooked from a group nobody told them about. Re-assigning a vehicle to
   the SAME group it is already on is idempotent (no conflict with
   itself).

   `list_grupos` embeds each group's resolved `vehiculo` (`{id, placa,
   tipo}` or `null`) directly in its response — a `get_all` batch read
   over the distinct `vehiculo_id`s already present in the page, not a
   second round trip the admin UI has to make.

`vehiculos` is a THIRD, INDEPENDENT collection under this router (own
sole-writer allowlist, `ALLOWED_MODULES_VEHICULOS` in
`tests/invariants/test_sole_writer.py`) — `routers/inspector_asignaciones.py`
and `routers/sticker_asignaciones.py` have no reason to read or write it;
only the admin dispatcher here ever touches it.

## `puntos-disponibles` change (2026-08-26) — `metricasProgreso`

ONE new, READ-ONLY action, appended at the very END of the dispatcher (a
concurrent batch owns the group/vehicle CRUD region above — this action
touches none of it). Returns per-GROUP and per-INSPECTOR progress —
assigned/hecho/pendiente counts and completion % — for BOTH campaigns
(`sticker_matches`, `planeacion_puntos`) combined AND broken out per
campaign. Reads the SAME two collections `resumen()` above already reads in
full (no new scale concern: this is the identical "bounded, aggregated in
Python" tradeoff that function's own docstring note already documents,
applied to two collections instead of one).

Inspector NAMES are NOT resolved here — `metrics_progreso` returns raw
uids. The admin tab already caches the inspector roster client-side
(`web/js/planeacion.js`'s `inspectoresCache`/`inspectorById`, fetched once
from `/api/stickers {action:'list'}`) and already resolves uid -> display
name that way for `cuadrillasHtml`/`gruposHtml` — `metricasProgreso`
reuses that SAME cache in the UI layer rather than adding a second,
duplicated roster fetch on the backend.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth.deps import require_role
from app.config import Settings
from app.credentials import clients as credentials
from app.services import planeacion_audit
from app.services.survey_link import build_survey_urls

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

PLANEACION_PUNTOS_COLLECTION = "planeacion_puntos"
PLANEACION_CUADRILLAS_COLLECTION = "planeacion_cuadrillas"
_CUADRILLAS_KEY = "cuadrillas"

# `grupos-inspectores` change. Shared, campaign-agnostic — CRUD lives here;
# `routers/inspector_asignaciones.py` and `routers/sticker_asignaciones.py`
# READ it (see module docstring). Read-only cross-campaign reference to
# STICKER_MATCHES_COLLECTION below is for eliminarGrupo's orphan check ONLY
# (never a write) — this router owns `planeacion_puntos`/
# `planeacion_cuadrillas`/`grupos_inspectores`, not `sticker_matches`.
GRUPOS_INSPECTORES_COLLECTION = "grupos_inspectores"
STICKER_MATCHES_COLLECTION = "sticker_matches"

# Binding requirement (2026-08-26, folded into this change): a group has AT
# MOST this many members. Named constant, not a magic number, so it can be
# retuned in one place. Enforced server-side in crear_grupo/editar_grupo.
MAX_MIEMBROS_GRUPO = 4

# `grupos-inspectores` follow-up: vehicles, one per group at a time ("cada
# grupo sale en un vehículo"). Own, independent collection/allowlist — see
# module docstring's own "member cap + vehicles" section.
VEHICULOS_COLLECTION = "vehiculos"

# Feature H: drivers. Own, independent collection/allowlist. A vehiculo may
# reference one conductor (its `conductor_id`); a conductor cannot be deleted
# while any vehiculo still points at it (orphan-prevention, same discipline as
# vehiculos vs grupos).
CONDUCTORES_COLLECTION = "conductores"

# Binding user decision (2026-08-26): DEFAULT_MAX_SIZE = 10 for Planeación,
# NOT the sticker template's 8 — an EDAN survey is a far longer visit than
# applying a sticker. DEFAULT_MAX_RADIUS_M is carried over from the sticker
# campaign, still flagged unconfirmed (design.md ADR-8/proposal.md Q4).
# Both are named constants + the same per-call override plumbing the
# sticker template already has, so retuning is a one-line change.
DEFAULT_MAX_RADIUS_M = 800
DEFAULT_MAX_SIZE = 10

# Auto-agrupar working-set cap (user decision 2026-08-26): cluster the top-N
# highest-`prioridad_score` pending/ungrouped points per run instead of the
# full ~11k pending set — keeps each run fluid. Critical damage concentrates
# geographically, so the top-N by score still captures dense hard-hit zones;
# points beyond N are picked up on the NEXT run (once this batch is grouped),
# so coverage stays complete across runs. Overridable per call via `limite`.
AUTOAGRUPAR_LIMIT = 2000

# ADR-9: bounded, indexed listPuntos — never the full ~14.8k collection.
#
# Speed follow-up (2026-08-26): LIMIT_DEFAULT was 2000, and opening the
# Planeación tab measured 9-35s in production — the tab shipped 2000 points
# to the client and rendered 2000 table rows, far more than an operator can
# act on in one sitting. 300 is a "few hundred of the highest-priority
# points" working set, matching the truncation banner's own honest
# "showing N of M pending" message (formatTruncacion, web/js/planeacion.js)
# — a smaller default does not hide work, it is just a smaller page of the
# SAME prioritized queue. LIMIT_MAX is unchanged: still the ceiling for
# anyone who explicitly passes a larger `limit`.
LIMIT_DEFAULT = 300
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


def points_locked(points: list[dict[str, Any]]) -> list[str]:
    """Assignment lock (feature F): a point already **levantado**
    (`tiene_survey` — EDAN survey or israel) or **done**
    (`estado_asignacion == 'hecho'`) must NOT be re-assigned; only pending
    points get assigned. Superset of `points_with_survey`, adding the
    already-completed case."""
    return [
        p["id"]
        for p in (points or [])
        if p and (p.get("tiene_survey") is True or p.get("estado_asignacion") == "hecho")
    ]


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


def _positive_int(raw: Any, default: int) -> int:
    """`listAuditoria`'s own page-size parsing — deliberately separate from
    `_clamp_limit` above, which defaults to `listPuntos`'s LIMIT_DEFAULT
    (300)/LIMIT_MAX (5000), not the bitácora's own 50/unbounded shape."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _clamp_limit(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return LIMIT_DEFAULT
    if n <= 0:
        return LIMIT_DEFAULT
    return min(n, LIMIT_MAX)


def _jsonable(value: Any) -> Any:
    """Make a Firestore document value JSON-encodable.

    Timestamp fields (`matched_at`, `asignado_en`, ...) come back from REAL
    Firestore as `DatetimeWithNanoseconds`, which `JSONResponse` cannot
    encode — the request dies with a 502 before any payload is written. The
    in-memory fake every router test uses returns plain Python values, so
    nothing in the suite exercises this; it surfaced only on the first live
    call against the 14,804 populated documents. Anything with `isoformat`
    is normalised to an ISO-8601 string (which is what the frontend parses
    anyway); lists/dicts are walked so a nested timestamp cannot slip past.
    """
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _doc_to_dict(doc: Any, *, with_id: bool = True) -> dict[str, Any]:
    """Firestore snapshot -> JSON-safe dict. Single funnel so a new read
    site cannot forget `_jsonable` and reintroduce the 502 above."""
    data = _jsonable(doc.to_dict() or {})
    return {"id": doc.id, **data} if with_id else data


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
    # `incluirLevantados` widens the set to points that ALREADY have a survey,
    # for reviewing/correcting what the cruce matched (a wrong auto-close is
    # only findable if you can see closed points). Default stays False: the
    # working set is "what still needs a survey". `no_aplica` is NOT widened
    # by this flag — that is an explicit operator exclusion, not a survey-state
    # fact, so it needs the `estado` filter to surface.
    incluir_levantados = bool(params.get("incluirLevantados"))

    query = db.collection(PLANEACION_PUNTOS_COLLECTION)
    if not incluir_levantados:
        query = query.where("tiene_survey", "==", False)
    query = (
        query.order_by("prioridad_score", direction=_fs.Query.DESCENDING)
        .limit(LIMIT_MAX + 1)
    )
    puntos = [_doc_to_dict(d) for d in query.get()]

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
    puntos = [_doc_to_dict(d, with_id=False) for d in docs]

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
    return [_doc_to_dict(d) for d in docs]


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

    from google.cloud import firestore as _fs  # deferred import, credentials/clients.py's own convention

    # Cap the working set to the top-N most-critical pending/ungrouped points
    # (by prioridad_score) instead of fetching the full ~11k pending set — this
    # is the fluidity fix. Excludes surveyed/`no_aplica` points defensively in
    # code below (Firestore allows only one inequality field, already spent on
    # ordering by prioridad_score). Needs a composite index on
    # (estado_asignacion, cuadrilla_id, prioridad_score DESC) — operator step.
    limite = int(_positive_number(body.get("limite"), AUTOAGRUPAR_LIMIT))
    docs = (
        db.collection(PLANEACION_PUNTOS_COLLECTION)
        .where("estado_asignacion", "==", "pendiente")
        .where("cuadrilla_id", "==", None)
        .order_by("prioridad_score", direction=_fs.Query.DESCENDING)
        .limit(limite)
        .get()
    )
    all_puntos = [_doc_to_dict(d) for d in docs]
    excluded_ids = set(points_with_survey(all_puntos)) | set(points_excluded(all_puntos))
    puntos = [p for p in all_puntos if p["id"] not in excluded_ids]
    if not puntos:
        return []

    grupos = auto_agrupar(puntos, max_radius_m=max_radius_m, max_size=int(max_size))
    # Densest clusters first: teams get the fullest routes (most points per
    # field position) — proximity criterion, "aprovechar el terreno".
    grupos.sort(key=len, reverse=True)
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
    # feature F parity gap fix: a point manually marked 'hecho' WITHOUT a
    # survey (tiene_survey stays False, so the `surveyed` guard above never
    # catches it) must still be refused here — every OTHER assignment path
    # (editarCuadrilla add, asignarGrupoAPuntos, asignarInspector,
    # reasignarPunto) already rejects it via `points_locked`; crearCuadrilla
    # was the one write path missing this check.
    hecho_sin_survey = [p["id"] for p in current if p.get("estado_asignacion") == "hecho"]
    if hecho_sin_survey:
        raise bad_request(
            f"{len(hecho_sin_survey)} punto(s) ya están marcados como hechos; quitar esos puntos de la selección."
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
            {
                "id": s.id,
                "cuadrilla_id": (s.to_dict() or {}).get("cuadrilla_id") if s.exists else None,
                "tiene_survey": (s.to_dict() or {}).get("tiene_survey") is True if s.exists else False,
                "estado_asignacion": (s.to_dict() or {}).get("estado_asignacion") if s.exists else None,
            }
            for s in add_snaps
        ]
        conflicts = points_already_assigned(add_current, cuadrilla_id)
        if conflicts:
            raise bad_request(
                f"{len(conflicts)} punto(s) ya pertenecen a una cuadrilla; quitalos de su cuadrilla actual antes de reasignar."
            )
        locked = points_locked(add_current)
        if locked:
            raise bad_request(
                f"{len(locked)} punto(s) ya están levantados o hechos; no se pueden agregar a una cuadrilla."
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

    # feature F: never re-assign a levantado/hecho member. Read each point's
    # state and propagate the inspector only to the still-assignable ones —
    # the cuadrilla always records the inspector, but a point whose survey
    # arrived after the cuadrilla was formed is skipped, not reset.
    punto_snaps = db.get_all(
        [db.collection(PLANEACION_PUNTOS_COLLECTION).document(pid) for pid in puntos]
    ) if puntos else []
    locked = set(
        points_locked(
            [
                {
                    "id": s.id,
                    "tiene_survey": (s.to_dict() or {}).get("tiene_survey") is True,
                    "estado_asignacion": (s.to_dict() or {}).get("estado_asignacion"),
                }
                for s in punto_snaps
            ]
        )
    )
    asignables = [pid for pid in puntos if pid not in locked]

    from google.cloud import firestore as _fs  # deferred import, credentials/clients.py's own convention

    now = _fs.SERVER_TIMESTAMP
    batch = db.batch()
    batch.set(ref, {"inspector_uid": inspector_uid}, merge=True)
    for punto_id in asignables:
        batch.set(
            db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id),
            {"inspector_uid": inspector_uid, "asignado_en": now, "estado_asignacion": "asignado"},
            merge=True,
        )
    batch.commit()
    return {"id": cuadrilla_id, "asignados": len(asignables), "omitidos": len(locked)}


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

    data = snap.to_dict() or {}
    if points_locked([{"id": punto_id, **data}]):
        raise bad_request(
            "El punto ya está levantado o hecho; no se puede re-asignar (solo se asignan pendientes)."
        )

    prev_inspector_uid = data.get("inspector_uid")
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


# ---- grupos_inspectores actions (`grupos-inspectores` change) --------------


def list_grupos(db: Any) -> list[dict[str, Any]]:
    """Every `grupos_inspectores` doc, raw (`miembros` as uids — the admin
    UI resolves uid -> display name via the SAME inspector roster it
    already caches for cuadrillas' own `inspectorLabel`, `/api/stickers`
    `{action:'list'}`, no duplicated roster fetch here), PLUS each group's
    resolved `vehiculo` (`{id, placa, tipo}` or `null`) embedded directly —
    one `get_all` batch read over the distinct `vehiculo_id`s already
    present in this page, not a second round trip the admin UI has to make
    (2026-08-26 follow-up requirement)."""
    docs = db.collection(GRUPOS_INSPECTORES_COLLECTION).get()
    grupos = [_doc_to_dict(d) for d in docs]

    vehiculo_ids = sorted({g["vehiculo_id"] for g in grupos if g.get("vehiculo_id")})
    vehiculo_by_id: dict[str, dict[str, Any]] = {}
    if vehiculo_ids:
        refs = [db.collection(VEHICULOS_COLLECTION).document(vid) for vid in vehiculo_ids]
        for snap in db.get_all(refs):
            if snap.exists:
                vehiculo_by_id[snap.id] = _doc_to_dict(snap)

    for g in grupos:
        vid = g.get("vehiculo_id")
        g["vehiculo"] = vehiculo_by_id.get(vid) if vid else None
    return grupos


def crear_grupo(db: Any, body: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    nombre = str(body.get("nombre") or "").strip()
    raw_miembros = body.get("miembros")
    # De-duplicate BEFORE measuring against the cap — a repeated uid in the
    # raw request must not count twice (2026-08-26 follow-up requirement).
    miembros = list(dict.fromkeys(str(m) for m in raw_miembros)) if isinstance(raw_miembros, list) else []
    if not nombre:
        raise bad_request("crearGrupo necesita un nombre.")
    if not miembros:
        raise bad_request("crearGrupo necesita al menos un miembro.")
    if len(miembros) > MAX_MIEMBROS_GRUPO:
        raise bad_request(
            f"Un grupo admite máximo {MAX_MIEMBROS_GRUPO} miembros; se enviaron {len(miembros)}."
        )

    ref = db.collection(GRUPOS_INSPECTORES_COLLECTION).document()
    data = {
        "nombre": nombre,
        "miembros": miembros,
        "activo": True,
        "creado_en": _now(),
        "creado_por": claims.get("sub"),
    }
    ref.set(data)
    return {"id": ref.id}


def editar_grupo(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Rename and/or add/remove members, keeping `miembros` de-duplicated.
    Adapted from `editar_cuadrilla`'s own add/remove shape. The
    `MAX_MIEMBROS_GRUPO` cap is checked against the RESULTING membership
    (current − remove + add, de-duplicated) BEFORE any write happens — a
    rejection leaves the group completely unchanged, no partial add
    (2026-08-26 follow-up requirement)."""
    grupo_id = str(body.get("grupo_id") or "").strip()
    add = [str(u) for u in body.get("add") or []] if isinstance(body.get("add"), list) else []
    remove = [str(u) for u in body.get("remove") or []] if isinstance(body.get("remove"), list) else []
    if not grupo_id:
        raise bad_request("Falta grupo_id.")

    ref = db.collection(GRUPOS_INSPECTORES_COLLECTION).document(grupo_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el grupo {grupo_id}.")

    current_data = snap.to_dict() or {}
    current = {str(u) for u in current_data.get("miembros") or []}
    next_set = set(current)
    for uid in remove:
        next_set.discard(uid)
    for uid in add:
        next_set.add(uid)
    next_miembros = list(next_set)

    if len(next_miembros) > MAX_MIEMBROS_GRUPO:
        raise bad_request(
            f"Un grupo admite máximo {MAX_MIEMBROS_GRUPO} miembros; la edición resultaría en {len(next_miembros)}."
        )

    fields: dict[str, Any] = {"miembros": next_miembros}
    nombre = body.get("nombre")
    if nombre:
        fields["nombre"] = str(nombre).strip()

    ref.set(fields, merge=True)
    return {"id": grupo_id, "nombre": fields.get("nombre", current_data.get("nombre")), "miembros": next_miembros}


def eliminar_grupo(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Orphan-prevention decision: REFUSE deletion while ANY point (in
    EITHER campaign — the group is shared) still references this
    `grupo_id`, naming the count(s), rather than silently clearing
    `grupo_id` back to unassigned on every affected point. Chosen over the
    silent-clear alternative for the same reason `crear_cuadrilla`'s own
    guards are read-before-write and actionable rather than silent: an
    admin deleting a group that still has active field-work assigned
    through it should see exactly what would be orphaned and act
    deliberately (reassign or `desasignarGrupo` first), not discover it
    later as points with a dangling `grupo_id` nobody remembers creating."""
    grupo_id = str(body.get("grupo_id") or "").strip()
    if not grupo_id:
        raise bad_request("Falta grupo_id.")

    ref = db.collection(GRUPOS_INSPECTORES_COLLECTION).document(grupo_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el grupo {grupo_id}.")

    planeacion_n = len(
        list(db.collection(PLANEACION_PUNTOS_COLLECTION).where("grupo_id", "==", grupo_id).get())
    )
    sticker_n = len(
        list(db.collection(STICKER_MATCHES_COLLECTION).where("grupo_id", "==", grupo_id).get())
    )
    if planeacion_n or sticker_n:
        partes = []
        if planeacion_n:
            partes.append(f"{planeacion_n} punto(s) de planeación")
        if sticker_n:
            partes.append(f"{sticker_n} punto(s) de stickers")
        raise bad_request(
            f"No se puede eliminar el grupo {grupo_id}: todavía tiene {' y '.join(partes)} "
            "asignado(s). Reasignar o desasignar esos puntos primero."
        )

    ref.delete()
    return {"id": grupo_id}


def asignar_grupo_a_puntos(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Sets `grupo_id` on the given `planeacion_puntos` docs — COEXISTS with
    any existing `inspector_uid` (never touched here, binding decision 2).
    Read-before-write existence guards on BOTH the grupo and every point,
    same discipline `crear_cuadrilla` already uses; batched at 500 ops via
    `commit_in_chunks`."""
    grupo_id = str(body.get("grupo_id") or "").strip()
    raw_puntos = body.get("puntos")
    puntos = [str(p) for p in raw_puntos] if isinstance(raw_puntos, list) else []
    if not grupo_id:
        raise bad_request("Falta grupo_id.")
    if not puntos:
        raise bad_request("asignarGrupoAPuntos necesita al menos un punto.")

    grupo_snap = db.collection(GRUPOS_INSPECTORES_COLLECTION).document(grupo_id).get()
    if not grupo_snap.exists:
        raise bad_request(f"No existe el grupo {grupo_id}.")

    punto_refs = [db.collection(PLANEACION_PUNTOS_COLLECTION).document(pid) for pid in puntos]
    punto_snaps = db.get_all(punto_refs)
    missing = [s.id for s in punto_snaps if not s.exists]
    if missing:
        raise bad_request(f"{len(missing)} punto(s) no existen en planeacion_puntos: {sorted(missing)}.")

    # feature F: a levantado/hecho point is not re-assignable — reject the whole
    # op (like crearCuadrilla) so the operator drops those points and retries.
    current = [
        {
            "id": s.id,
            "tiene_survey": (s.to_dict() or {}).get("tiene_survey") is True,
            "estado_asignacion": (s.to_dict() or {}).get("estado_asignacion"),
        }
        for s in punto_snaps
    ]
    locked = points_locked(current)
    if locked:
        raise bad_request(
            f"{len(locked)} punto(s) ya están levantados o hechos; quitar esos puntos de la selección."
        )

    def _apply(batch: Any, punto_id: str) -> None:
        batch.set(db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id), {"grupo_id": grupo_id}, merge=True)

    commit_in_chunks(db, puntos, _apply)
    return {"grupo_id": grupo_id, "puntos": puntos}


def desasignar_grupo(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Clears `grupo_id` (back to unassigned) on the given `planeacion_puntos`
    docs. Does not touch `inspector_uid` — the two assignment mechanisms
    are independent (binding decision 2)."""
    raw_puntos = body.get("puntos")
    puntos = [str(p) for p in raw_puntos] if isinstance(raw_puntos, list) else []
    if not puntos:
        raise bad_request("desasignarGrupo necesita al menos un punto.")

    def _apply(batch: Any, punto_id: str) -> None:
        batch.set(db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id), {"grupo_id": None}, merge=True)

    commit_in_chunks(db, puntos, _apply)
    return {"puntos": puntos}


# ---- vehiculos actions (`grupos-inspectores` follow-up, 2026-08-26) --------
# "cada grupo sale en un vehículo" — CRUD + grupo assignment for the
# `vehiculos` collection. Own, independent collection (see module
# docstring's own "member cap + vehicles" section).


# ── conductores (feature H) — driver CRUD, mirrors the vehiculos block ──────
def _cedula_conflict(db: Any, cedula: str, exclude_id: str | None = None) -> bool:
    existentes = db.collection(CONDUCTORES_COLLECTION).where("cedula", "==", cedula).get()
    return any(s.id != exclude_id for s in existentes)


def list_conductores(db: Any) -> list[dict[str, Any]]:
    return [_doc_to_dict(d) for d in db.collection(CONDUCTORES_COLLECTION).get()]


def crear_conductor(db: Any, body: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    cedula = str(body.get("cedula") or "").strip()
    nombre_completo = str(body.get("nombre_completo") or "").strip()
    email = str(body.get("email") or "").strip()
    telefono = str(body.get("telefono") or "").strip()
    if not cedula:
        raise bad_request("crearConductor necesita una cédula.")
    if not nombre_completo:
        raise bad_request("crearConductor necesita el nombre completo.")
    if _cedula_conflict(db, cedula):
        raise bad_request(f"Ya existe un conductor con la cédula {cedula}.")

    ref = db.collection(CONDUCTORES_COLLECTION).document()
    data = {
        "cedula": cedula,
        "nombre_completo": nombre_completo,
        "email": email or None,
        "telefono": telefono or None,
        "activo": True,
        "creado_en": _now(),
        "creado_por": claims.get("sub"),
    }
    ref.set(data)
    return {"id": ref.id}


def editar_conductor(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    conductor_id = str(body.get("conductor_id") or "").strip()
    if not conductor_id:
        raise bad_request("Falta conductor_id.")

    ref = db.collection(CONDUCTORES_COLLECTION).document(conductor_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el conductor {conductor_id}.")

    fields: dict[str, Any] = {}
    raw_cedula = body.get("cedula")
    if raw_cedula:
        nueva = str(raw_cedula).strip()
        if _cedula_conflict(db, nueva, exclude_id=conductor_id):
            raise bad_request(f"Ya existe un conductor con la cédula {nueva}.")
        fields["cedula"] = nueva
    for key in ("nombre_completo", "email", "telefono"):
        raw = body.get(key)
        if raw is not None:
            fields[key] = str(raw).strip() or None
    raw_activo = body.get("activo")
    if raw_activo is not None:
        fields["activo"] = bool(raw_activo)

    ref.set(fields, merge=True)
    return {"id": conductor_id, **fields}


def eliminar_conductor(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Orphan-prevention: REFUSE deletion while any `vehiculos` doc still
    references this conductor, naming the plate(s) — same discipline as
    `eliminar_vehiculo`/`eliminar_grupo`."""
    conductor_id = str(body.get("conductor_id") or "").strip()
    if not conductor_id:
        raise bad_request("Falta conductor_id.")

    ref = db.collection(CONDUCTORES_COLLECTION).document(conductor_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el conductor {conductor_id}.")

    asignados = list(
        db.collection(VEHICULOS_COLLECTION).where("conductor_id", "==", conductor_id).get()
    )
    if asignados:
        placas = [(d.to_dict() or {}).get("placa") or d.id for d in asignados]
        raise bad_request(
            f"El conductor está asignado a {len(placas)} vehículo(s): {', '.join(placas)}. "
            "Quitar el conductor de esos vehículos antes de eliminarlo."
        )
    ref.delete()
    return {"id": conductor_id, "eliminado": True}


def _validate_conductor(db: Any, raw_conductor_id: Any) -> str | None:
    """Normalize + existence-check a vehicle's optional `conductor_id`. Empty
    → None (no driver); a non-existent id → 400. Feature H."""
    conductor_id = str(raw_conductor_id or "").strip()
    if not conductor_id:
        return None
    if not db.collection(CONDUCTORES_COLLECTION).document(conductor_id).get().exists:
        raise bad_request(f"No existe el conductor {conductor_id}.")
    return conductor_id


def _placa_conflict(db: Any, placa: str, exclude_id: str | None = None) -> bool:
    existentes = db.collection(VEHICULOS_COLLECTION).where("placa", "==", placa).get()
    return any(s.id != exclude_id for s in existentes)


def list_vehiculos(db: Any) -> list[dict[str, Any]]:
    docs = db.collection(VEHICULOS_COLLECTION).get()
    return [_doc_to_dict(d) for d in docs]


def crear_vehiculo(db: Any, body: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    placa = str(body.get("placa") or "").strip().upper()
    tipo = body.get("tipo")
    empresa = str(body.get("empresa") or "").strip()
    if not placa:
        raise bad_request("crearVehiculo necesita una placa.")
    if _placa_conflict(db, placa):
        raise bad_request(f"Ya existe un vehículo con la placa {placa}.")
    conductor_id = _validate_conductor(db, body.get("conductor_id"))

    ref = db.collection(VEHICULOS_COLLECTION).document()
    data = {
        "placa": placa,
        "tipo": str(tipo).strip() if tipo else None,
        "empresa": empresa or None,
        "conductor_id": conductor_id,
        "activo": True,
        "creado_en": _now(),
        "creado_por": claims.get("sub"),
    }
    ref.set(data)
    return {"id": ref.id}


def editar_vehiculo(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    vehiculo_id = str(body.get("vehiculo_id") or "").strip()
    if not vehiculo_id:
        raise bad_request("Falta vehiculo_id.")

    ref = db.collection(VEHICULOS_COLLECTION).document(vehiculo_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el vehículo {vehiculo_id}.")

    fields: dict[str, Any] = {}
    raw_placa = body.get("placa")
    if raw_placa:
        nueva_placa = str(raw_placa).strip().upper()
        if _placa_conflict(db, nueva_placa, exclude_id=vehiculo_id):
            raise bad_request(f"Ya existe un vehículo con la placa {nueva_placa}.")
        fields["placa"] = nueva_placa
    raw_tipo = body.get("tipo")
    if raw_tipo is not None:
        fields["tipo"] = str(raw_tipo).strip()
    raw_empresa = body.get("empresa")
    if raw_empresa is not None:
        fields["empresa"] = str(raw_empresa).strip() or None
    raw_activo = body.get("activo")
    if raw_activo is not None:
        fields["activo"] = bool(raw_activo)
    raw_conductor = body.get("conductor_id")
    if raw_conductor is not None:
        # explicit "" clears the driver; a non-existent id is a 400
        fields["conductor_id"] = _validate_conductor(db, raw_conductor)

    ref.set(fields, merge=True)
    return {"id": vehiculo_id, **fields}


def eliminar_vehiculo(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Orphan-prevention decision: SAME discipline already chosen for
    `eliminar_grupo` — REFUSE deletion while any `grupos_inspectores` doc
    still references this vehicle, naming the group(s), rather than
    silently clearing it. Consistency with the earlier decision on the
    sibling collection, not a new one."""
    vehiculo_id = str(body.get("vehiculo_id") or "").strip()
    if not vehiculo_id:
        raise bad_request("Falta vehiculo_id.")

    ref = db.collection(VEHICULOS_COLLECTION).document(vehiculo_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el vehículo {vehiculo_id}.")

    asignados = list(
        db.collection(GRUPOS_INSPECTORES_COLLECTION).where("vehiculo_id", "==", vehiculo_id).get()
    )
    if asignados:
        nombres = [(d.to_dict() or {}).get("nombre") or d.id for d in asignados]
        raise bad_request(
            f"No se puede eliminar el vehículo {vehiculo_id}: todavía está asignado al grupo "
            f"«{'», «'.join(nombres)}». Desasignarlo primero (desasignarVehiculo)."
        )

    ref.delete()
    return {"id": vehiculo_id}


def asignar_vehiculo_a_grupo(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Sets `vehiculo_id` on a `grupos_inspectores` doc. Double-booking
    decision: "one vehicle -> at most one group at a time" is enforced by
    REJECTING (400, naming the OTHER group that already holds it) rather
    than silently moving the vehicle — same "guard-then-reject with an
    actionable message" discipline `eliminar_grupo`/`crear_cuadrilla`
    already use elsewhere in this file. Re-assigning to the SAME group it
    is already on is idempotent (no conflict with itself)."""
    grupo_id = str(body.get("grupo_id") or "").strip()
    vehiculo_id = str(body.get("vehiculo_id") or "").strip()
    if not grupo_id:
        raise bad_request("Falta grupo_id.")
    if not vehiculo_id:
        raise bad_request("Falta vehiculo_id.")

    grupo_ref = db.collection(GRUPOS_INSPECTORES_COLLECTION).document(grupo_id)
    grupo_snap = grupo_ref.get()
    if not grupo_snap.exists:
        raise bad_request(f"No existe el grupo {grupo_id}.")

    vehiculo_snap = db.collection(VEHICULOS_COLLECTION).document(vehiculo_id).get()
    if not vehiculo_snap.exists:
        raise bad_request(f"No existe el vehículo {vehiculo_id}.")

    otros = db.collection(GRUPOS_INSPECTORES_COLLECTION).where("vehiculo_id", "==", vehiculo_id).get()
    conflicto = [d for d in otros if d.id != grupo_id]
    if conflicto:
        otro = conflicto[0]
        otro_nombre = (otro.to_dict() or {}).get("nombre") or otro.id
        raise bad_request(
            f"El vehículo {vehiculo_id} ya está asignado al grupo «{otro_nombre}». Desasignarlo primero."
        )

    grupo_ref.set({"vehiculo_id": vehiculo_id}, merge=True)
    return {"grupo_id": grupo_id, "vehiculo_id": vehiculo_id}


def desasignar_vehiculo(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    grupo_id = str(body.get("grupo_id") or "").strip()
    if not grupo_id:
        raise bad_request("Falta grupo_id.")

    ref = db.collection(GRUPOS_INSPECTORES_COLLECTION).document(grupo_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el grupo {grupo_id}.")

    ref.set({"vehiculo_id": None}, merge=True)
    return {"grupo_id": grupo_id}


# ---- metricasProgreso (`puntos-disponibles` change, 2026-08-26) -----------


def _tally(puntos: list[dict[str, Any]]) -> dict[str, Any]:
    """assigned/hecho/pendiente/no_aplica + completion % for a flat list of
    raw point dicts (either collection's shape — both use the SAME
    `estado_asignacion` values). `no_aplica` never appears on a
    `sticker_matches` doc, so it is naturally always 0 there — one shared
    function for both campaigns, no special-casing needed."""
    total = len(puntos)
    hechos = sum(1 for p in puntos if p.get("estado_asignacion") == "hecho")
    no_aplica = sum(1 for p in puntos if p.get("estado_asignacion") == "no_aplica")
    pendientes = total - hechos - no_aplica
    pct = round((hechos / total) * 100, 1) if total else 0.0
    return {
        "asignados": total,
        "hechos": hechos,
        "pendientes": pendientes,
        "no_aplica": no_aplica,
        "completado_pct": pct,
    }


def metricas_progreso(db: Any) -> dict[str, Any]:
    """Per-group and per-inspector progress, BOTH campaigns combined AND
    broken out per campaign (spec: `metricasProgreso`). See this module's
    own docstring ("puntos-disponibles change") for the scale/roster
    reasoning."""
    sticker_puntos = [_doc_to_dict(d, with_id=False) for d in db.collection(STICKER_MATCHES_COLLECTION).get()]
    planeacion_puntos = [_doc_to_dict(d, with_id=False) for d in db.collection(PLANEACION_PUNTOS_COLLECTION).get()]
    grupos = [_doc_to_dict(d) for d in db.collection(GRUPOS_INSPECTORES_COLLECTION).get()]

    por_campana = {"stickers": sticker_puntos, "survey": planeacion_puntos}
    todos_los_puntos = sticker_puntos + planeacion_puntos

    grupos_metricas: dict[str, Any] = {}
    for g in grupos:
        gid = g["id"]
        entry: dict[str, Any] = {
            "nombre": g.get("nombre") or gid,
            "miembros": len(g.get("miembros") or []),
            "activo": g.get("activo", True),
        }
        combinado: list[dict[str, Any]] = []
        for campana, puntos in por_campana.items():
            propios = [p for p in puntos if p.get("grupo_id") == gid]
            entry[campana] = _tally(propios)
            combinado.extend(propios)
        entry["combinado"] = _tally(combinado)
        grupos_metricas[gid] = entry

    uids: set[str] = set()
    for puntos in por_campana.values():
        uids.update(p["inspector_uid"] for p in puntos if p.get("inspector_uid"))
    grupos_por_uid: dict[str, list[str]] = {}
    for g in grupos:
        for uid in g.get("miembros") or []:
            uids.add(uid)
            grupos_por_uid.setdefault(uid, []).append(g.get("nombre") or g["id"])

    inspectores_metricas: dict[str, Any] = {}
    for uid in sorted(uids):
        entry = {"grupos": grupos_por_uid.get(uid, [])}
        combinado = []
        for campana, puntos in por_campana.items():
            propios = [p for p in puntos if p.get("inspector_uid") == uid]
            entry[campana] = _tally(propios)
            combinado.extend(propios)
        entry["combinado"] = _tally(combinado)
        inspectores_metricas[uid] = entry

    return {
        "grupos": grupos_metricas,
        "inspectores": inspectores_metricas,
        "combinado": _tally(todos_los_puntos),
        "stickers": _tally(sticker_puntos),
        "survey": _tally(planeacion_puntos),
    }


class PlaneacionAsignacionesRequest(BaseModel):
    action: str
    # listPuntos / resumen
    estado: str | None = None
    prioridad: str | None = None
    comuna: str | None = None
    soloPendientes: bool | None = None
    # Widens listPuntos to points that already have a survey, so a wrong
    # auto-close is reviewable (a closed point is only correctable if it can
    # be seen). Default False keeps the working set at "still needs a survey".
    incluirLevantados: bool | None = None
    limit: Any = None
    # autoAgrupar
    maxRadiusM: Any = None
    maxSize: Any = None
    limite: Any = None
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
    # grupos-inspectores: crearGrupo/editarGrupo/eliminarGrupo/
    # asignarGrupoAPuntos/desasignarGrupo
    grupo_id: str | None = None
    miembros: list[str] | None = None
    # grupos-inspectores follow-up: vehiculos CRUD + asignarVehiculoAGrupo/
    # desasignarVehiculo. `activo` here is bool-only (editarVehiculo), no
    # _UNSET needed — unlike editarAsignacion's nullable string fields.
    vehiculo_id: str | None = None
    placa: str | None = None
    tipo: str | None = None
    empresa: str | None = None
    activo: bool | None = None

    # Feature H (conductores): driver CRUD + link vehiculo->conductor.
    conductor_id: str | None = None
    cedula: str | None = None
    telefono: str | None = None
    email: str | None = None
    nombre_completo: str | None = None

    # `planeacion-auditoria` change: listAuditoria filters. `tipo` is already
    # declared above (reused verbatim — vehiculo's own type field and this
    # action's entidad filter never collide, since they belong to different
    # actions on the same dispatcher).
    usuario: str | None = None
    desde: Any = None
    antes_de: Any = None


def _dispatch(
    body: PlaneacionAsignacionesRequest,
    payload: dict[str, Any],
    claims: dict[str, Any],
    db: Any,
) -> JSONResponse:
    """The dispatcher's own `if body.action == ...` chain — extracted
    verbatim (mechanical move, no branch body changes) so
    `planeacion_asignaciones()` can capture its `JSONResponse` and run the
    post-mutation audit hook at ONE call site (design.md ADR-2)."""
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
        if body.action == "listGrupos":
            return JSONResponse({"ok": True, "grupos": list_grupos(db)})
        if body.action == "crearGrupo":
            return JSONResponse({"ok": True, **crear_grupo(db, payload, claims)}, status_code=201)
        if body.action == "editarGrupo":
            return JSONResponse({"ok": True, **editar_grupo(db, payload)})
        if body.action == "eliminarGrupo":
            return JSONResponse({"ok": True, **eliminar_grupo(db, payload)})
        if body.action == "asignarGrupoAPuntos":
            return JSONResponse({"ok": True, **asignar_grupo_a_puntos(db, payload)})
        if body.action == "desasignarGrupo":
            return JSONResponse({"ok": True, **desasignar_grupo(db, payload)})
        if body.action == "listVehiculos":
            return JSONResponse({"ok": True, "vehiculos": list_vehiculos(db)})
        if body.action == "crearVehiculo":
            return JSONResponse({"ok": True, **crear_vehiculo(db, payload, claims)}, status_code=201)
        if body.action == "editarVehiculo":
            return JSONResponse({"ok": True, **editar_vehiculo(db, payload)})
        if body.action == "eliminarVehiculo":
            return JSONResponse({"ok": True, **eliminar_vehiculo(db, payload)})
        if body.action == "asignarVehiculoAGrupo":
            return JSONResponse({"ok": True, **asignar_vehiculo_a_grupo(db, payload)})
        if body.action == "desasignarVehiculo":
            return JSONResponse({"ok": True, **desasignar_vehiculo(db, payload)})
        if body.action == "listConductores":
            return JSONResponse({"ok": True, "conductores": list_conductores(db)})
        if body.action == "crearConductor":
            return JSONResponse({"ok": True, **crear_conductor(db, payload, claims)}, status_code=201)
        if body.action == "editarConductor":
            return JSONResponse({"ok": True, **editar_conductor(db, payload)})
        if body.action == "eliminarConductor":
            return JSONResponse({"ok": True, **eliminar_conductor(db, payload)})
        if body.action == "metricasProgreso":
            return JSONResponse({"ok": True, "metricas": metricas_progreso(db)})
        if body.action == "listAuditoria":
            page_size = _positive_int(payload.get("limit"), planeacion_audit.PAGE_SIZE_DEFAULT)
            return JSONResponse({
                "ok": True,
                **planeacion_audit.list_auditoria(
                    db,
                    tipo=payload.get("tipo"),
                    usuario=payload.get("usuario"),
                    desde=payload.get("desde"),
                    antes_de=payload.get("antes_de"),
                    page_size=page_size,
                ),
            })
        raise bad_request(f"Acción desconocida: {body.action}")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - legacy fail-open surface
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/planeacion-asignaciones")
def planeacion_asignaciones(
    body: PlaneacionAsignacionesRequest,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    db = credentials.sismo().firestore
    payload = body.model_dump()

    resp = _dispatch(body, payload, claims, db)

    if body.action in planeacion_audit.MUTATING_ACTIONS:
        # ADR-1: best-effort, strictly AFTER the mutation already committed
        # and built its own response — a logging failure never alters it.
        resultado = json.loads(resp.body)
        planeacion_audit.registrar_best_effort(
            db,
            actor_uid=claims.get("sub"),
            actor_email=claims.get("email"),
            accion=body.action,
            params=payload,
            resultado=resultado,
        )
    return resp
