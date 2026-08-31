"""POST /inspector-asignaciones — an INSPECTOR's own sticker assignments
(design.md ADR-3/ADR-9; backend-platform spec "Own-uid-scoped route rejects
cross-uid access", "sticker_matches And cuadrillas Sole-Writer Invariant";
field-form-session spec "Inspector Own-UID Scoping Preserved").

Ports `api/inspector-asignaciones.js`'s `misPuntos`/`marcarHecho` dispatch
verbatim. Unlike `sticker-asignaciones` (admin-only, ported in slice 8),
this endpoint authorizes ANY authenticated user (`Depends(require_auth)`)
and scopes EVERY `sticker_matches` read/write to the caller's OWN uid
(`inspector_uid == token.sub`) — an inspector can only ever see or touch
the points assigned to them. `sticker_matches` has no Firestore security
rules (ADR-9), so this scoping is the ONLY thing standing between one
inspector's data and another's; there is no rules-layer backstop.

FIRST of the three modules allowlisted for the `sticker_matches`/
`cuadrillas` literal under `tests/invariants/test_sole_writer.py` (ADR-9).
The admin `sticker-asignaciones` router (slice 8) and the `cruce_sticker`
job (slice 7) are the other two — do not anticipate their allowlist entries
here.

Route path deliberately has NO `/api` prefix (unlike `/api/sign`), matching
the field-form-session/backend-platform spec deltas' own scenario text
(`/inspector-asignaciones`, not `/api/inspector-asignaciones`) and the
`reportados`/`sticker-status`/`source-status` precedent — see task 5.5's
BLOCKED note in apply-progress.md for what this means for the eventual
`formulario/js/form.js` repoint (a path change, not just a host flip).

## `planeacion-asignaciones` follow-up batch (2026-08-26)

The Planeación feature let an ADMIN assign EDAN-survey points to an
inspector (`routers/planeacion_asignaciones.py`, `Depends(require_role(
"admin"))`), but shipped with no way for the ASSIGNEE to ever see them —
`formulario/` had zero references to planeación and the admin router is
403 to anyone who isn't an admin. This module is the correct place to close
that gap: it is ALREADY the own-uid-scoped, any-authenticated surface an
inspector talks to, so adding `misPuntosPlaneacion`/`marcarHechoPlaneacion`
here means no new auth path to get wrong — same `Depends(require_auth)`,
same `inspector_uid == token.sub` scoping the two sticker actions above
already enforce, applied to `planeacion_puntos` instead of
`sticker_matches`. `misPuntosPlaneacion` also builds each point's prefilled
Survey123 URL via the SAME `app/services/survey_link.py:build_survey_urls()`
the admin router's `getEnlaceSurvey` already uses — no duplicated URL logic.

This module is the THIRD allowlisted reader/writer of the `planeacion_puntos`
literal under `tests/invariants/test_sole_writer.py`'s CLOSED
`ALLOWED_MODULES_PLANEACION_PUNTOS` set (the other two are
`app/jobs/planeacion_cruce.py`, pipeline-owned fields, and
`app/routers/planeacion_asignaciones.py`, admin-owned fields). The honest
entry is annotated there rather than obfuscating the literal to dodge the
scan — this module's own access is a THIRD, genuinely different case from
either of those: it is neither the pipeline nor the admin dashboard, it is
the assignee reading/closing only their OWN points, gated the same way
`sticker_matches` already is above.

## `grupos-inspectores` change (2026-08-26) — groups of PEOPLE, not points

A group of INSPECTORS is a NEW concept, distinct from `planeacion_cuadrillas`
(a group of POINTS assigned to exactly one `inspector_uid`). A point in
EITHER campaign (`sticker_matches` or `planeacion_puntos`) can now carry an
optional `grupo_id` naming a `grupos_inspectores/{id}` doc
(`{nombre, miembros: [uid,...], activo, creado_en, creado_por}`, CRUD lives
in `routers/planeacion_asignaciones.py`, admin-gated). Group assignment
COEXISTS with individual `inspector_uid` assignment — neither this module's
own-uid queries/guard nor the admin dashboards' existing `inspector_uid`
behavior change at all; a point simply gains a second, independent way to
be visible/completable.

Because Firestore cannot OR across two fields in one query, `_mis_puntos`/
`_mis_puntos_planeacion` now run TWO queries per campaign — the existing
own-uid `==` query, plus a `grupo_id in [...]` query over every ACTIVE
group the caller belongs to (found via one `miembros array_contains uid`
query against `grupos_inspectores`) — and merge the results, de-duplicated
by doc id, in Python. The `in` operator caps at 30 values per Firestore
query; `_grupo_ids_for_uid`'s caller chunks into <=30-id batches so an
inspector in more than 30 groups degrades safely instead of silently
truncating (see `_puntos_por_grupo`).

The own-uid write guard in `_marcar_hecho`/`_marcar_hecho_planeacion`
widens symmetrically: a write is allowed when `inspector_uid == uid` **OR**
`uid` is an active member of the point's `grupo_id` group; every other
caller still gets a 403 with NO write, unchanged. Because "anyone in the
group can act" would otherwise destroy the per-write accountability the
own-uid guard used to give for free, every successful `marcarHecho`/
`marcarHechoPlaneacion` (own-uid OR group path alike, for consistency) now
also stamps `completado_por` (the ACTING uid) and `completado_en`.

This module is the READER of `grupos_inspectores` (own-uid membership
lookup only — it never writes that collection; group CRUD is exclusively
admin-owned by `routers/planeacion_asignaciones.py`), allowlisted under
`tests/invariants/test_sole_writer.py`'s
`ALLOWED_MODULES_GRUPOS_INSPECTORES`.

## `puntos-disponibles` change (2026-08-26) — claim a nearby UNassigned point

Two NEW actions, both own-uid-scoped exactly like the four above:
`puntosCercanosDisponibles {lat, lng}` (read-only) and `tomarPunto
{punto_id, campana}` (the caller self-assigns). User decisions locked in:

1. **Radius = `NEARBY_RADIUS_M` = 300 m** — "only what an inspector can see
   on foot", one named constant so it is retunable in one place.
2. **Claiming a point assigns BOTH campaigns.** The inspector is standing
   at the building, so claiming ANY campaign's point ALSO claims the SAME
   building's point in the OTHER campaign, IFF a pending, unassigned,
   not-yet-covered record for it exists there — never fabricated.

### Scale (same bounding-box discipline as the pipelines' own reads)

`planeacion_puntos` has ~14.8k docs; `sticker_matches` is smaller but not
tiny. Firestore allows only ONE inequality field per query, so
`_fetch_bbox` queries a bounding box on `coords.lat` (`>=`/`<=` — the SAME
field twice, which real Firestore does NOT require a composite index for)
and filters longitude, exact haversine distance, assignment state, AND
coverage (see below) in Python. No composite index is needed anywhere in
this module: every other predicate runs in Python after the bbox fetch.

### "Available" — corrected 2026-08-26: coverage, not just assignment

A point is available when it is UNASSIGNED (no `inspector_uid`, no
`grupo_id`), still PENDING (not `hecho`, not `no_aplica` for survey), AND
NOT ALREADY COVERED — `sticker_matches.tiene_sticker` / `planeacion_puntos.
tiene_survey` computed by the cruce jobs INDEPENDENTLY of assignment
(`app/jobs/cruce_sticker.py`/`app/jobs/planeacion_cruce.py`). A point can be
unassigned yet already covered — someone did it without an assignment, or
the cruce matched it after the fact — and offering it would send an
inspector to a building that is already done. `_razon_no_disponible` is the
SINGLE source of truth for this rule, shared by `puntosCercanosDisponibles`
(the read filter) and `_tomar_punto` (the transactional re-check below) —
one definition, never duplicated.

### The race: `tomarPunto` MUST use a Firestore transaction

Two inspectors standing on the same corner can tap "tomar" at the same
moment; the cruce job can ALSO flip `tiene_sticker`/`tiene_survey` true
between the list being rendered and the tap. Read-then-write is a race in
BOTH cases. `_tomar_punto` wraps the read-check-write (primary point AND,
when found, the sibling "gemelo" point in the other campaign) in a REAL
`google.cloud.firestore` transaction (`@transactional`, imported verbatim —
not reimplemented), so a second/stale claim loses cleanly with a 409 and NO
write, never a silent overwrite. The candidate SEARCH for the sibling
(`_buscar_gemelo`, a plain query) runs OUTSIDE the transaction — it is only
discovery, not the source of truth — but the actual claim decision for
EVERY doc touched (primary and sibling alike) is a transactional
`ref.get(transaction=transaction)` re-check right before the transactional
`transaction.set(...)`, so a coverage flip or a competing claim on either
doc is caught inside the SAME transaction that decides what to write.

### Building identity across campaigns — no new matching rule invented

`sticker_matches` and `planeacion_puntos` do NOT share a `registro_id`
namespace (different upstream systems: EDE Panel vs atencionsismo
`informe/json`), so there is no doc-id-level identity to compare. Both
`app/jobs/cruce_sticker.py` and `app/jobs/planeacion_cruce.py` already
cross-reference a point against ITS OWN campaign's field-reported source
using the SAME imported primitives from `app.integracion.cruce_gestor`:
geo proximity (`nearest`, <= `MAX_MATCH_M` = 40 m haversine) then address
fuzzy match (`addr_key`, `SequenceMatcher` ratio >= `ADDR_MATCH_RATIO` =
0.90). `_buscar_gemelo` reuses those exact same imported primitives and
thresholds, applied directly BETWEEN the two campaigns' collections
instead of between a campaign and its own source — the same tuned signal,
not a new one.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from google.cloud.firestore import transactional
from pydantic import BaseModel

from app.auth.deps import require_auth
from app.config import Settings
from app.credentials import clients as credentials
from app.integracion.coords import haversine_m
from app.integracion.cruce_gestor import ADDR_MATCH_RATIO, MAX_MATCH_M, addr_key, nearest
from app.services.survey_link import build_survey_urls

# sismo() is already unconditionally in credentials.WEB_STARTUP_CLIENTS, but
# this router still declares it per ADR-4's declaration mechanism — the
# invariant a router only reaches clients it names.
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

STICKER_MATCHES_COLLECTION = "sticker_matches"
# `planeacion-asignaciones` follow-up batch. Own-uid-scoped ONLY — this
# module never reads/writes another inspector's `planeacion_puntos` doc,
# and never touches the pipeline-owned or admin-owned fields
# (`clave_integracion`, `tiene_survey`, `match_via`, `cuadrilla_id`, ...);
# it only ever reads a point and, on marcarHechoPlaneacion, writes exactly
# one field (`estado_asignacion`), mirroring `_marcar_hecho` above.
PLANEACION_PUNTOS_COLLECTION = "planeacion_puntos"
DONE_ESTADO = "hecho"
NO_APLICA_ESTADO = "no_aplica"

# `grupos-inspectores` change. Own-uid-scoped READ ONLY here — this module
# only resolves "which active groups is the caller a member of", never
# writes this collection (CRUD is exclusively admin-owned,
# `routers/planeacion_asignaciones.py`).
GRUPOS_INSPECTORES_COLLECTION = "grupos_inspectores"
# Real Firestore caps an `in` query at 30 values.
_IN_QUERY_CHUNK = 30

# `planeacion-flujo-confiable` change (design.md ADR-1/ADR-3). READ-ONLY —
# this module is the sole reader of the sibling restricted-contact channel
# `app/jobs/dashboard_refresh.py` writes, keyed by the SAME doc id as its
# `planeacion_puntos` counterpart (`atencionsismo_{registro_id}`), never
# merged onto that doc itself (leak-impossible-by-construction, see that
# job's own module comment). Allowlisted under
# `tests/invariants/test_sole_writer.py`'s `ALLOWED_MODULES_PUNTOS_CONTACTO`
# (read side).
PUNTOS_CONTACTO_COLLECTION = "puntos_contacto"
# Firestore `get_all` chunk cap, house style (the survey-ingest service's
# own `_batched_read_source_state` precedent) — not the 30-value `in`-query
# cap above, a separate, larger batching concern.
_GET_ALL_CHUNK = 500

# `puntos-disponibles` change. "Only what an inspector can see on foot" —
# binding user decision, one named constant so it is retunable in one place.
NEARBY_RADIUS_M = 300.0

# Standard great-circle approximation (WGS84 mean), used ONLY to size the
# bounding-box prefilter — the ACTUAL distance test is always the exact
# `haversine_m` below, never this approximation.
_METERS_PER_DEG_LAT = 111_320.0

CAMPANA_SURVEY = "survey"
CAMPANA_STICKER = "sticker"
CAMPANA_COLLECTIONS: dict[str, str] = {
    CAMPANA_STICKER: STICKER_MATCHES_COLLECTION,
    CAMPANA_SURVEY: PLANEACION_PUNTOS_COLLECTION,
}

router = APIRouter()


class AsignacionesRequest(BaseModel):
    action: str
    punto_id: str | None = None
    # `puntos-disponibles` change: tomarPunto {punto_id, campana} and
    # puntosCercanosDisponibles {lat, lng}.
    campana: str | None = None
    lat: float | None = None
    lng: float | None = None


def _pendiente(data: dict[str, Any]) -> bool:
    """An assignment is still "pending" (should show in the picker) when
    it is not yet marked done. Verbatim port of
    `api/inspector-asignaciones.js`'s `pendiente()`."""
    return data.get("estado_asignacion") != DONE_ESTADO


def _grupo_ids_for_uid(db: Any, uid: str) -> list[str]:
    """Every ACTIVE `grupos_inspectores` doc id whose `miembros` contains
    `uid` — a single `array_contains` query, shared by both campaigns
    (`grupos_inspectores` is campaign-agnostic, `grupos-inspectores`
    change decision 1)."""
    docs = db.collection(GRUPOS_INSPECTORES_COLLECTION).where(
        "miembros", "array_contains", uid
    ).get()
    ids: list[str] = []
    for doc in docs:
        data = doc.to_dict() or {}
        if data.get("activo") is False:
            continue
        ids.append(doc.id)
    return ids


def _uid_en_grupo_activo(db: Any, uid: str, grupo_id: str) -> bool:
    """True iff `grupo_id` names an ACTIVE `grupos_inspectores` doc whose
    `miembros` contains `uid`. Used by the write guard — a point's
    `grupo_id` may be stale/deleted, which must fail closed (403), not
    raise."""
    if not grupo_id:
        return False
    snap = db.collection(GRUPOS_INSPECTORES_COLLECTION).document(grupo_id).get()
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    if data.get("activo") is False:
        return False
    return uid in (data.get("miembros") or [])


def _docs_por_grupo(db: Any, collection_name: str, grupo_ids: list[str]) -> dict[str, Any]:
    """Every doc in `collection_name` whose `grupo_id` is one of
    `grupo_ids`, keyed by doc id. Chunks the `in` query at
    `_IN_QUERY_CHUNK` (Firestore's own 30-value cap) so a caller belonging
    to more than 30 groups degrades safely instead of silently truncating
    (`grupos-inspectores` change, binding requirement)."""
    by_id: dict[str, Any] = {}
    for i in range(0, len(grupo_ids), _IN_QUERY_CHUNK):
        chunk = grupo_ids[i : i + _IN_QUERY_CHUNK]
        docs = db.collection(collection_name).where("grupo_id", "in", chunk).get()
        for doc in docs:
            by_id[doc.id] = doc
    return by_id


def _mis_puntos(db: Any, uid: str) -> list[dict[str, Any]]:
    """Every `sticker_matches` doc the caller can see — own-uid points
    UNION every point assigned to a group the caller actively belongs to
    (de-duplicated by doc id) — filtered to still-pending ones. Own-uid
    behavior is a verbatim port of `api/inspector-asignaciones.js`'s
    `misPuntos()`; the group union is the `grupos-inspectores` change."""
    own_docs = db.collection(STICKER_MATCHES_COLLECTION).where(
        "inspector_uid", "==", uid
    ).get()
    merged: dict[str, Any] = {doc.id: doc for doc in own_docs}
    grupo_ids = _grupo_ids_for_uid(db, uid)
    if grupo_ids:
        merged.update(_docs_por_grupo(db, STICKER_MATCHES_COLLECTION, grupo_ids))

    puntos: list[dict[str, Any]] = []
    for doc in merged.values():
        data = doc.to_dict() or {}
        if not _pendiente(data):
            continue
        puntos.append(
            {
                "id": doc.id,
                "direccion": data.get("direccion") or "",
                "zona_id": data.get("zona_id") or "",
                "coords": data.get("coords"),
                "criterio_habitabilidad": data.get("criterio_habitabilidad"),
                "colapso": data.get("colapso") or "no",
                "estado_asignacion": data.get("estado_asignacion") or "pendiente",
                # Item 6 follow-up (2026-08-27): rides along for free once
                # `planeacion_asignaciones.py`'s twin propagation persists it
                # onto this same doc — null on a twin never linked yet.
                "clave_integracion": data.get("clave_integracion"),
            }
        )
    return puntos


def _pendiente_planeacion(data: dict[str, Any]) -> bool:
    """A planeación point is still "pending" (should show in the picker)
    when it is neither `hecho` NOR `no_aplica` — mirrors `_pendiente`'s
    single-terminal-state shape, extended by one state because
    `planeacion_puntos` (unlike `sticker_matches`) has an explicit operator
    exclusion an inspector must never be sent to survey."""
    estado = data.get("estado_asignacion")
    return estado != DONE_ESTADO and estado != NO_APLICA_ESTADO


def _contactos_por_id(db: Any, doc_ids: list[str]) -> dict[str, dict[str, Any]]:
    """`{doc_id: {'nombre_solicitante', 'telefono_solicitante'}}` via one
    batched `get_all` over `puntos_contacto`, keyed by the SAME doc ids the
    caller already holds (`planeacion_puntos` doc id ==
    `puntos_contacto` doc id, design.md ADR-1). Missing/errored docs are
    simply absent from the result — the caller merges null-safe."""
    if not doc_ids:
        return {}
    col = db.collection(PUNTOS_CONTACTO_COLLECTION)
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(doc_ids), _GET_ALL_CHUNK):
        chunk = doc_ids[start:start + _GET_ALL_CHUNK]
        refs = [col.document(doc_id) for doc_id in chunk]
        for snap in db.get_all(refs):
            if not snap.exists:
                continue
            data = snap.to_dict() or {}
            out[snap.id] = {
                "nombre_solicitante": data.get("nombre_solicitante"),
                "telefono_solicitante": data.get("telefono_solicitante"),
            }
    return out


def _mis_puntos_planeacion(db: Any, uid: str) -> list[dict[str, Any]]:
    """Every `planeacion_puntos` doc the caller can see — own-uid points
    UNION every point assigned to a group the caller actively belongs to
    (de-duplicated by doc id) — filtered to still-pending ones, each
    carrying its prefilled Survey123 links AND, when present, the
    reporter's contact (design.md ADR-3 — one extra batched `get_all`
    against `puntos_contacto`, keyed by doc ids already held here).
    Structural port of `_mis_puntos` above: single-field Firestore queries
    only (no composite index needed), remaining filters applied in code."""
    own_docs = db.collection(PLANEACION_PUNTOS_COLLECTION).where(
        "inspector_uid", "==", uid
    ).get()
    merged: dict[str, Any] = {doc.id: doc for doc in own_docs}
    grupo_ids = _grupo_ids_for_uid(db, uid)
    if grupo_ids:
        merged.update(_docs_por_grupo(db, PLANEACION_PUNTOS_COLLECTION, grupo_ids))

    settings = Settings()
    form_url = settings.survey123_form_url
    field_app_item_id = settings.survey123_field_app_item_id or None
    contactos = _contactos_por_id(db, list(merged.keys()))

    puntos: list[dict[str, Any]] = []
    for doc in merged.values():
        data = doc.to_dict() or {}
        if not _pendiente_planeacion(data):
            continue
        clave = data.get("clave_integracion")
        # Fail OPEN, not loud: unlike getEnlaceSurvey's single-item 503,
        # this is a LIST action — one missing env var or one point without
        # a minted key yet must never blank the whole picker, only that
        # point's own link fields.
        if clave and form_url:
            urls = build_survey_urls(
                clave, form_url=form_url, field_app_item_id=field_app_item_id
            )
        else:
            urls = {"web": None, "app": None}
        contacto = contactos.get(doc.id, {})
        puntos.append(
            {
                "id": doc.id,
                "clave_integracion": clave,
                "direccion": data.get("direccion") or "",
                "coords": data.get("coords"),
                "comuna": data.get("comuna"),
                "afectacion": data.get("afectacion"),
                "prioridad": data.get("prioridad"),
                "estado_asignacion": data.get("estado_asignacion") or "pendiente",
                "survey_web": urls["web"],
                "survey_app": urls["app"],
                "nombre_solicitante": contacto.get("nombre_solicitante"),
                "telefono_solicitante": contacto.get("telefono_solicitante"),
            }
        )
    return puntos


def _puede_actuar(db: Any, uid: str, data: dict[str, Any]) -> bool:
    """The widened write guard (`grupos-inspectores` change, binding
    decision 5): allowed when `inspector_uid == uid` OR `uid` is an active
    member of the point's `grupo_id` group. Everything else is a 403 with
    no write, unchanged from before this change."""
    if data.get("inspector_uid") == uid:
        return True
    return _uid_en_grupo_activo(db, uid, data.get("grupo_id"))


def _marcar_hecho_planeacion(db: Any, uid: str, punto_id: str) -> dict[str, Any]:
    """Flip one `planeacion_puntos` doc to `hecho`, IFF `uid` may act on it
    (own-uid OR active group member, `_puede_actuar`) — reject with NO
    write otherwise. Records WHO acted (`completado_por`/`completado_en`)
    so group assignment does not lose the per-write accountability the
    own-uid guard used to give for free."""
    if not punto_id:
        raise HTTPException(status_code=400, detail="Falta el id del punto.")
    ref = db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="El punto no existe.")
    data = snap.to_dict() or {}
    if not _puede_actuar(db, uid, data):
        raise HTTPException(
            status_code=403, detail="Ese punto no está asignado a este inspector."
        )
    from google.cloud import firestore as _fs

    ref.set(
        {
            "estado_asignacion": DONE_ESTADO,
            "completado_por": uid,
            "completado_en": datetime.now(timezone.utc),
            "actualizado_en": _fs.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    return {"id": punto_id, "estado_asignacion": DONE_ESTADO}


def _marcar_survey_hecho(db: Any, uid: str, punto_id: str) -> dict[str, Any]:
    """`survey-sticker-realtime-sync` change: close a `planeacion_puntos`
    (survey) point AND, best-effort, materialize/pre-assign its sticker
    twin so it appears in the same inspector's/group's sticker tab
    immediately — without waiting for `app/jobs/cruce_sticker.py`'s next
    cron run. Same `_puede_actuar` gate and `estado_asignacion:'hecho'` +
    `completado_por`/`completado_en` write shape as
    `_marcar_hecho_planeacion` (that write MUST succeed and is not gated by
    the sticker step at all).

    The sticker step is wrapped in one `try/except` that NEVER re-raises
    (spec: "sticker-twin materialization/pre-assignment... NEVER blocks or
    fails the survey-completion write on error") — twin lookup reuses
    `_buscar_gemelo` VERBATIM (no new matching rule); on a miss, a NEW
    `sticker_matches/atencionsismo_{registro_id}` doc is created via the
    SAME deterministic `doc_id(fuente, registro_id)` shape
    `cruce_sticker.py`/`planeacion_cruce.py` already use — `fuente` is
    `'atencionsismo'`, a namespace `cruce_sticker.py` never mints
    (`ede_*`/`israel_*` only), so this can never collide with a future cron
    write (design.md's "Deterministic on-demand doc id" ADR). `cuadrilla_id`
    is NEVER copied onto the twin: it names a `cuadrillas` doc in the
    STICKER campaign's OWN id-space, distinct from `planeacion_puntos`' own
    `cuadrilla_id` (`planeacion_cuadrillas`) — copying it would silently
    create a cross-collection id collision."""
    if not punto_id:
        raise HTTPException(status_code=400, detail="Falta el id del punto.")
    ref = db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="El punto no existe.")
    data = snap.to_dict() or {}
    if not _puede_actuar(db, uid, data):
        raise HTTPException(
            status_code=403, detail="Ese punto no está asignado a este inspector."
        )

    from google.cloud import firestore as _fs

    now = datetime.now(timezone.utc)
    ref.set(
        {
            "estado_asignacion": DONE_ESTADO,
            "completado_por": uid,
            "completado_en": now,
            "actualizado_en": _fs.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    sticker_matches_id: str | None = None
    sticker_creado = False
    try:
        # Local import: this router only ever needs `cruce_sticker`'s pure
        # `doc_id` helper on the (rare) twin-miss path, not its whole cron
        # module surface at request time every call.
        from app.jobs.cruce_sticker import doc_id as _sticker_doc_id

        gemelo_ref = _buscar_gemelo(db, CAMPANA_STICKER, data)
        asignacion = {
            "grupo_id": data.get("grupo_id"),
            "inspector_uid": data.get("inspector_uid"),
            "estado_asignacion": "asignado",
            "asignado_en": now,
        }
        if gemelo_ref is not None:
            gemelo_ref.set(asignacion, merge=True)
            sticker_matches_id = gemelo_ref.id
        else:
            registro_id = data.get("registro_id")
            new_id = _sticker_doc_id("atencionsismo", str(registro_id))
            nuevo = {
                "fuente": "atencionsismo",
                "registro_id": str(registro_id) if registro_id is not None else None,
                "tiene_sticker": False,
                "tier": None,
                "sticker_dist_m": None,
                "direccion": data.get("direccion"),
                "coords": data.get("coords"),
                "zona_id": data.get("comuna"),
                "matched_at": now,
                "clave_integracion": data.get("clave_integracion"),
                "planeacion_punto_id": punto_id,
                "cuadrilla_id": None,
                "reasignado_de": None,
                **asignacion,
            }
            db.collection(STICKER_MATCHES_COLLECTION).document(new_id).set(nuevo, merge=True)
            sticker_matches_id = new_id
            sticker_creado = True
    except Exception:
        sticker_matches_id = None
        sticker_creado = False

    return {
        "id": punto_id,
        "estado_asignacion": DONE_ESTADO,
        "sticker_matches_id": sticker_matches_id,
        "sticker_creado": sticker_creado,
    }


def _marcar_hecho(db: Any, uid: str, punto_id: str) -> dict[str, Any]:
    """Flip one `sticker_matches` doc to `hecho`, IFF `uid` may act on it
    (own-uid OR active group member, `_puede_actuar`). Own-uid behavior is
    a verbatim port of `api/inspector-asignaciones.js`'s `marcarHecho()`;
    the group-member path and the `completado_por`/`completado_en`
    accountability stamp are the `grupos-inspectores` change."""
    if not punto_id:
        raise HTTPException(status_code=400, detail="Falta el id del punto.")
    ref = db.collection(STICKER_MATCHES_COLLECTION).document(punto_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="El punto no existe.")
    data = snap.to_dict() or {}
    if not _puede_actuar(db, uid, data):
        raise HTTPException(
            status_code=403, detail="Ese punto no está asignado a este inspector."
        )
    ref.set(
        {
            "estado_asignacion": DONE_ESTADO,
            "completado_por": uid,
            "completado_en": datetime.now(timezone.utc),
        },
        merge=True,
    )
    return {"id": punto_id, "estado_asignacion": DONE_ESTADO}


# ── `puntos-disponibles` change (2026-08-26) ────────────────────────────────
# Nearby UNASSIGNED points (any campaign) an inspector can claim on the spot,
# plus the transactional claim itself. See the module docstring's own
# "puntos-disponibles change" section for the scale/race/identity reasoning.


def _bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lon_min, lon_max) — a generous rectangular
    prefilter, never the final distance test. Longitude degrees shrink with
    latitude (`cos(lat)`); clamped away from 0 so a point exactly on the
    equator (never true for Cali, but keeps this pure function total) can't
    divide by zero."""
    lat_delta = radius_m / _METERS_PER_DEG_LAT
    lon_delta = radius_m / (_METERS_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))
    return lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta


def _fetch_bbox(db: Any, collection_name: str, lat: float, lon: float, radius_m: float) -> list[Any]:
    """Every doc in `collection_name` whose `coords.lat` falls in the bbox —
    ONE Firestore inequality field (`coords.lat`, `>=` then `<=` — the SAME
    field twice never needs a composite index in real Firestore), longitude
    and everything else filtered in Python by the caller. This is the ONLY
    Firestore-level filter in this module's nearby/claim path — see the
    module docstring's "Scale" section."""
    lat_min, lat_max, _lon_min, _lon_max = _bbox(lat, lon, radius_m)
    return (
        db.collection(collection_name)
        .where("coords.lat", ">=", lat_min)
        .where("coords.lat", "<=", lat_max)
        .get()
    )


def _razon_no_disponible(data: dict[str, Any], campana: str) -> str | None:
    """None iff `data` (a raw sticker_matches/planeacion_puntos doc dict) is
    available to be claimed for `campana` — otherwise the reason it is not,
    used both to FILTER `puntosCercanosDisponibles` and as the exact 409
    detail `_tomar_punto` returns on a lost race. SINGLE source of truth for
    "available" (module docstring's corrected "Available" section,
    2026-08-26): unassigned (no inspector_uid, no grupo_id), still pending
    (not hecho; not no_aplica for survey), AND not already covered by field
    data independently of assignment (tiene_sticker/tiene_survey)."""
    if data.get("inspector_uid") or data.get("grupo_id"):
        return "otro inspector ya tomó este punto"
    estado = data.get("estado_asignacion")
    if estado == DONE_ESTADO:
        return "otro inspector ya tomó este punto"
    if campana == CAMPANA_SURVEY and estado == NO_APLICA_ESTADO:
        return "este punto ya no aplica para encuesta"
    cubierto_key = "tiene_sticker" if campana == CAMPANA_STICKER else "tiene_survey"
    if data.get(cubierto_key):
        return "este punto ya fue cubierto (ya tiene sticker o encuesta registrada)"
    return None


def _disponible(data: dict[str, Any], campana: str) -> bool:
    return _razon_no_disponible(data, campana) is None


def _puntos_cercanos_disponibles(db: Any, lat: float, lng: float) -> list[dict[str, Any]]:
    """Every still-pending, UNASSIGNED, NOT-YET-COVERED point within
    `NEARBY_RADIUS_M` of (lat, lng), from BOTH campaigns, each tagged with
    its own `campana` so the UI can label it, sorted nearest-first."""
    resultados: list[dict[str, Any]] = []
    for campana, collection_name in CAMPANA_COLLECTIONS.items():
        for doc in _fetch_bbox(db, collection_name, lat, lng, NEARBY_RADIUS_M):
            data = doc.to_dict() or {}
            if not _disponible(data, campana):
                continue
            coords = data.get("coords") or {}
            dlat, dlon = coords.get("lat"), coords.get("lon")
            if dlat is None or dlon is None:
                continue
            dist_m = haversine_m((lat, lng), (dlat, dlon))
            if dist_m > NEARBY_RADIUS_M:
                continue
            resultados.append(
                {
                    "id": doc.id,
                    "campana": campana,
                    "direccion": data.get("direccion") or "",
                    "coords": coords,
                    "dist_m": round(dist_m, 1),
                    "criterio_habitabilidad": data.get("criterio_habitabilidad"),
                    "colapso": data.get("colapso"),
                    "afectacion": data.get("afectacion"),
                    "prioridad": data.get("prioridad"),
                }
            )
    resultados.sort(key=lambda p: p["dist_m"])
    return resultados


def _gemelo_latlon(item: dict[str, Any]):
    coords = item.get("coords") or {}
    lat, lon = coords.get("lat"), coords.get("lon")
    return (lat, lon) if lat is not None and lon is not None else None


def _buscar_gemelo(db: Any, otra_campana: str, punto_data: dict[str, Any]) -> Any | None:
    """The pending, unassigned, not-yet-covered record for the SAME
    building in the OTHER campaign's collection, or None. See the module
    docstring's "Building identity across campaigns" section — reuses
    `cruce_gestor`'s own geo-then-address cascade verbatim, never a new
    matching rule. Candidate discovery only (not transactional); the caller
    re-validates freshness inside the transaction before writing."""
    coords = punto_data.get("coords") or {}
    lat, lon = coords.get("lat"), coords.get("lon")
    if lat is None or lon is None:
        return None
    otra_collection = CAMPANA_COLLECTIONS[otra_campana]
    candidatos = [
        (doc.id, doc.to_dict() or {})
        for doc in _fetch_bbox(db, otra_collection, lat, lon, NEARBY_RADIUS_M)
    ]
    disponibles = [(doc_id, data) for doc_id, data in candidatos if _disponible(data, otra_campana)]

    # Rung 1: geo, nearest within MAX_MATCH_M (cruce_gestor's own tuned
    # proximity threshold, reused — not retuned).
    best, _dist = nearest(lat, lon, [data for _id, data in disponibles], _gemelo_latlon, max_m=MAX_MATCH_M)
    if best is not None:
        for doc_id, data in disponibles:
            if data is best:
                return db.collection(otra_collection).document(doc_id)

    # Rung 2: address, exact-or-fuzzy normalized key (cruce_gestor's own
    # addr_key + ADDR_MATCH_RATIO, reused — not retuned).
    key_p = addr_key(punto_data.get("direccion"))
    if key_p:
        for doc_id, data in disponibles:
            key_c = addr_key(data.get("direccion"))
            if key_c and (key_c == key_p or SequenceMatcher(None, key_p, key_c).ratio() >= ADDR_MATCH_RATIO):
                return db.collection(otra_collection).document(doc_id)
    return None


def _tomar_punto(db: Any, uid: str, punto_id: str, campana: str) -> dict[str, Any]:
    """Self-claim `punto_id` in `campana` — AND, per binding user decision 1,
    the SAME building's record in the OTHER campaign when one exists,
    pending and unassigned. Wrapped in a REAL Firestore transaction so a
    second/stale claim (race OR a coverage flip mid-flight) is rejected
    with NO write, never a silent overwrite — see the module docstring's
    "The race" section."""
    if not punto_id:
        raise HTTPException(status_code=400, detail="Falta el id del punto.")
    if campana not in CAMPANA_COLLECTIONS:
        raise HTTPException(status_code=400, detail="Campaña no reconocida.")

    ref = db.collection(CAMPANA_COLLECTIONS[campana]).document(punto_id)
    otra_campana = CAMPANA_SURVEY if campana == CAMPANA_STICKER else CAMPANA_STICKER

    transaction = db.transaction()

    @transactional
    def _run(transaction: Any) -> dict[str, Any]:
        snap = ref.get(transaction=transaction)
        if not snap.exists:
            raise HTTPException(status_code=404, detail="El punto no existe.")
        data = snap.to_dict() or {}
        razon = _razon_no_disponible(data, campana)
        if razon is not None:
            raise HTTPException(status_code=409, detail=razon)

        gemelo_ref = _buscar_gemelo(db, otra_campana, data)
        gemelo_id: str | None = None
        gemelo_data: dict[str, Any] = {}
        if gemelo_ref is not None:
            gemelo_snap = gemelo_ref.get(transaction=transaction)
            if gemelo_snap.exists and _disponible(gemelo_snap.to_dict() or {}, otra_campana):
                gemelo_id = gemelo_snap.id
                gemelo_data = gemelo_snap.to_dict() or {}

        from google.cloud import firestore as _fs

        now = datetime.now(timezone.utc)
        campos = {
            "inspector_uid": uid,
            "asignado_en": now,
            "estado_asignacion": "asignado",
            # Speed follow-up: stamped unconditionally even though `ref`/
            # `gemelo_ref` may land on a sticker_matches doc in this
            # cross-campaign claim — an extra unused field there is
            # harmless, and this is the only write shared by both
            # collections in this transaction.
            "actualizado_en": _fs.SERVER_TIMESTAMP,
        }
        # Pairing-key propagation (2026-08-31): copy `clave_integracion`/
        # `planeacion_punto_id` across the twin so the formulario can stamp
        # the evaluación — same two fields `_marcar_survey_hecho` already
        # writes from this router. First-link-wins: only when the
        # destination's existing clave is empty or equal.
        campos_ref = dict(campos)
        campos_gemelo = dict(campos)
        if gemelo_id is not None:
            if campana == CAMPANA_SURVEY:
                # Planeación-claim: stamp the sticker gemelo with this
                # point's own pairing keys.
                existente = gemelo_data.get("clave_integracion")
                clave = data.get("clave_integracion")
                if not existente or existente == clave:
                    campos_gemelo.update(
                        {"clave_integracion": clave, "planeacion_punto_id": punto_id}
                    )
            else:
                # Sticker-claim: pull the keys from the planeación gemelo
                # into this sticker doc's own write.
                existente = data.get("clave_integracion")
                clave = gemelo_data.get("clave_integracion")
                if not existente or existente == clave:
                    campos_ref.update(
                        {"clave_integracion": clave, "planeacion_punto_id": gemelo_id}
                    )
        transaction.set(ref, campos_ref, merge=True)
        asignados = {campana: punto_id}
        if gemelo_id is not None:
            transaction.set(gemelo_ref, campos_gemelo, merge=True)
            asignados[otra_campana] = gemelo_id
        return asignados

    asignados = _run(transaction)
    return {"asignados": asignados, "tambien_asignado": otra_campana in asignados}


def _clear_planeacion_cache(request: Request) -> None:
    """Bust `planeacion_asignaciones.py`'s `list_puntos` widening cache
    (`app.state.planeacion_aggregates_cache`, 5-min TTL) after a write that
    touches `planeacion_puntos` from THIS router. Without this, a point an
    inspector self-claims or completes in the field is invisible in the
    Planeación table/map (both driven by the same cached `assignedPuntos`
    set) for up to 5 minutes — this router used to never invalidate it at
    all, since only `routers/planeacion_asignaciones.py` and
    `routers/planeacion_cruce.py` knew the cache existed. Best-effort, same
    try/except-swallow shape `planeacion_cruce.py`'s own post-run clear
    uses: a cache-clear failure must never surface as if the actual write
    (already committed) had failed."""
    # Stage 2: also best-effort mark the in-process planeacion_puntos
    # snapshot dirty, so an inspector's own claim/completion shows up in the
    # admin board on its next read instead of waiting for the snapshot's TTL.
    snapshot = getattr(request.app.state, "planeacion_puntos_snapshot", None)
    if snapshot is not None:
        try:
            snapshot.mark_dirty()
        except Exception:  # noqa: BLE001 - best-effort
            pass

    cache = getattr(request.app.state, "planeacion_aggregates_cache", None)
    if cache is None:
        return
    try:
        cache.clear()
    except Exception:  # noqa: BLE001 - best-effort, mirrors planeacion_cruce.py
        pass


@router.post("/inspector-asignaciones")
def inspector_asignaciones(
    body: AsignacionesRequest,
    request: Request,
    claims: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    uid = claims.get("sub") or claims.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Token sin identificador de usuario.")

    db = credentials.sismo().firestore
    if body.action == "misPuntos":
        return {"ok": True, "puntos": _mis_puntos(db, uid)}
    if body.action == "marcarHecho":
        result = _marcar_hecho(db, uid, str(body.punto_id or ""))
        return {"ok": True, **result}
    if body.action == "misPuntosPlaneacion":
        return {"ok": True, "puntos": _mis_puntos_planeacion(db, uid)}
    if body.action == "marcarHechoPlaneacion":
        result = _marcar_hecho_planeacion(db, uid, str(body.punto_id or ""))
        _clear_planeacion_cache(request)
        return {"ok": True, **result}
    if body.action == "marcarSurveyHecho":
        result = _marcar_survey_hecho(db, uid, str(body.punto_id or ""))
        _clear_planeacion_cache(request)
        return {"ok": True, **result}
    if body.action == "puntosCercanosDisponibles":
        if body.lat is None or body.lng is None:
            raise HTTPException(status_code=400, detail="Faltan lat/lng.")
        return {"ok": True, "puntos": _puntos_cercanos_disponibles(db, body.lat, body.lng)}
    if body.action == "tomarPunto":
        result = _tomar_punto(db, uid, str(body.punto_id or ""), str(body.campana or ""))
        _clear_planeacion_cache(request)
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail="Acción no reconocida.")
