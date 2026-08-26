"""POST /sticker-asignaciones — admin sticker matching/assignment CRUD
("Asignación" sub-section of the dashboard's Stickers tab); design.md
ADR-3/ADR-9; backend-platform spec "Admin-gated route rejects non-admin",
"sticker_matches And cuadrillas Sole-Writer Invariant" (route side, final
closure).

Reads/writes two lean Firestore collections this endpoint and
`app/jobs/cruce_sticker.py` (pipeline) jointly own, split by field group:
`sticker_matches/{fuente}_{registro_id}` (pipeline-owned cruce result +
admin-owned assignment state) and `cuadrillas/{id}` (groups of pending
points, optionally linked to one inspector). Never reads
inspections.json/puntos_israel_cali.json (the full Panel) — only ever
touches these two lean collections.

FOURTH and FINAL module allowlisted for the `sticker_matches`/`cuadrillas`
literal under `tests/invariants/test_sole_writer.py` (ADR-9) — the other
three are `routers/inspector_asignaciones.py` (own-uid, slice 5),
`routers/sticker_status.py` (read-only, slice 4), and
`app/jobs/cruce_sticker.py` (pipeline fields, slice 7).

Ports `api/sticker-asignaciones.js` verbatim.

**Design interpretation flagged for verify** (see apply-progress.md's
Batch 8a entry): the legacy file's dispatcher exposes 10 actions, not the
8 explicitly enumerated by tasks.md 8.3/8.4's "8-action matrix" text
(`listPuntos`, `listCuadrillas`, `autoAgrupar`, `crearCuadrilla`,
`editarCuadrilla`, `asignarInspector`, `reasignarPunto`,
`eliminarCuadrilla`). `desasignarInspector` and `reiniciarAgrupacion` exist
in `api/sticker-asignaciones.js` but are not named in that list. Since
8.4's own instruction is to port the file "verbatim ... (all 8 actions
...)", and a verbatim port of the WHOLE FILE necessarily includes every
dispatch branch, both extra actions are ported here too rather than
silently dropped — omitting them would leave a real production capability
unported and would not actually be "verbatim".
"""
from __future__ import annotations

import math
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth.deps import require_role
from app.credentials import clients as credentials

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

STICKER_MATCHES_COLLECTION = "sticker_matches"
CUADRILLAS_COLLECTION = "cuadrillas"

# task 0.2 placeholders, verbatim from api/sticker-asignaciones.js:32-33 —
# not yet confirmed with the operator. Named constants so a later tune is a
# one-line change.
DEFAULT_MAX_RADIUS_M = 800
DEFAULT_MAX_SIZE = 8

router = APIRouter()


# ---- Pure helpers (exported for the self-check) ----------------------------

EARTH_RADIUS_M = 6371000


def haversine_m(a: dict[str, float], b: dict[str, float]) -> float:
    """Great-circle distance in meters. Verbatim port of
    `api/sticker-asignaciones.js`'s `haversineM`."""
    d_lat = math.radians(b["lat"] - a["lat"])
    d_lon = math.radians(b["lon"] - a["lon"])
    la1 = math.radians(a["lat"])
    la2 = math.radians(b["lat"])
    h = math.sin(d_lat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def auto_agrupar(
    puntos: list[dict[str, Any]], *, max_radius_m: float, max_size: int
) -> list[list[dict[str, Any]]]:
    """Deterministic greedy nearest-neighbor clustering — design.md ADR-3's
    locked pseudocode. Stable [lat, lon] sort order, no RNG, no k-means, so
    re-running on an unchanged point set produces identical groups.
    Verbatim port of `api/sticker-asignaciones.js`'s `autoAgrupar`."""
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
    """Uniqueness guard (one point -> at most one cuadrilla). Verbatim port
    of `api/sticker-asignaciones.js`'s `pointsAlreadyAssigned`."""
    return [
        p["id"]
        for p in (points or [])
        if p and p.get("cuadrilla_id") and p.get("cuadrilla_id") != target_cuadrilla_id
    ]


def points_with_sticker(points: list[dict[str, Any]]) -> list[str]:
    """No-sticker guard. Verbatim port of `pointsWithSticker`."""
    return [p["id"] for p in (points or []) if p and p.get("tiene_sticker") is True]


def points_with_colapso_total(points: list[dict[str, Any]]) -> list[str]:
    """Total-collapse guard. Verbatim port of `pointsWithColapsoTotal`."""
    return [p["id"] for p in (points or []) if p and p.get("colapso") == "total"]


def commit_in_chunks(db: Any, items: list[Any], apply_fn: Callable[[Any, Any], None]) -> None:
    """Firestore caps a write batch at 500 ops; split a flat list of writes
    into <=500-op commits. Verbatim port of `commitInChunks`."""
    for i in range(0, len(items), 500):
        batch = db.batch()
        for item in items[i : i + 500]:
            apply_fn(batch, item)
        batch.commit()


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


# ---- Firestore-backed actions ----------------------------------------------


def list_puntos(db: Any) -> list[dict[str, Any]]:
    docs = db.collection(STICKER_MATCHES_COLLECTION).get()
    return [{"id": d.id, **(d.to_dict() or {})} for d in docs]


def list_cuadrillas(db: Any) -> list[dict[str, Any]]:
    docs = db.collection(CUADRILLAS_COLLECTION).get()
    return [{"id": d.id, **(d.to_dict() or {})} for d in docs]


def _positive_number(value: Any, default: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def run_auto_agrupar(db: Any, body: dict[str, Any]) -> list[dict[str, Any]]:
    """Groups current `pendiente` points with no `cuadrilla_id` and creates
    new `cuadrillas` docs. MUST NOT touch `estado_asignacion` — grouping and
    assigning are separate actions. Verbatim port of `runAutoAgrupar`."""
    max_radius_m = _positive_number(body.get("maxRadiusM"), DEFAULT_MAX_RADIUS_M)
    max_size = _positive_number(body.get("maxSize"), DEFAULT_MAX_SIZE)

    docs = (
        db.collection(STICKER_MATCHES_COLLECTION)
        .where("estado_asignacion", "==", "pendiente")
        .where("cuadrilla_id", "==", None)
        .get()
    )
    # Exclude already-stickered and totally-collapsed points in code rather
    # than adding more equality where()s (that would need new composite
    # indexes) — same tradeoff api/sticker-asignaciones.js documents.
    all_puntos = [{"id": d.id, **(d.to_dict() or {})} for d in docs]
    puntos = [p for p in all_puntos if p.get("tiene_sticker") is not True and p.get("colapso") != "total"]
    if not puntos:
        return []

    grupos = auto_agrupar(puntos, max_radius_m=max_radius_m, max_size=int(max_size))
    cuadrillas: list[dict[str, Any]] = []
    batch = db.batch()
    for grupo in grupos:
        ref = db.collection(CUADRILLAS_COLLECTION).document()
        punto_ids = [p["id"] for p in grupo]
        data = {"puntos": punto_ids, "inspector_uid": None, "origen": "auto"}
        batch.set(ref, data)
        for punto_id in punto_ids:
            batch.set(db.collection(STICKER_MATCHES_COLLECTION).document(punto_id), {"cuadrilla_id": ref.id}, merge=True)
        cuadrillas.append({"id": ref.id, **data})
    batch.commit()
    return cuadrillas


def crear_cuadrilla(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Read-before-write uniqueness guard: refuse to build a cuadrilla out
    of points that already belong to another one. Verbatim port of
    `crearCuadrilla`."""
    nombre = str(body.get("nombre") or "").strip()
    raw_puntos = body.get("puntos")
    puntos = [str(p) for p in raw_puntos] if isinstance(raw_puntos, list) else []
    if not puntos:
        raise bad_request("crearCuadrilla necesita al menos un punto.")

    punto_refs = [db.collection(STICKER_MATCHES_COLLECTION).document(pid) for pid in puntos]
    punto_snaps = db.get_all(punto_refs)
    current = []
    for s in punto_snaps:
        data = s.to_dict() if s.exists else None
        current.append(
            {
                "id": s.id,
                "cuadrilla_id": (data or {}).get("cuadrilla_id"),
                "tiene_sticker": (data or {}).get("tiene_sticker") is True,
                "colapso": (data or {}).get("colapso"),
            }
        )

    # No-sticker / total-collapse guards checked before the
    # already-in-a-cuadrilla guard so the operator gets the more specific
    # reason first.
    stickered = points_with_sticker(current)
    if stickered:
        raise bad_request(
            f"{len(stickered)} punto(s) ya tienen sticker y no requieren visita; quitar esos puntos de la selección."
        )
    colapsados = points_with_colapso_total(current)
    if colapsados:
        raise bad_request(
            f"{len(colapsados)} punto(s) tienen colapso total y no requieren visita; quitar esos puntos de la selección."
        )
    conflicts = points_already_assigned(current, None)
    if conflicts:
        raise bad_request(
            f"{len(conflicts)} punto(s) ya pertenecen a una cuadrilla; quitar esos puntos de su cuadrilla actual antes de reasignar."
        )

    ref = db.collection(CUADRILLAS_COLLECTION).document()
    data = {"nombre": nombre, "puntos": puntos, "inspector_uid": None, "origen": "manual"}
    batch = db.batch()
    batch.set(ref, data)
    for punto_id in puntos:
        batch.set(db.collection(STICKER_MATCHES_COLLECTION).document(punto_id), {"cuadrilla_id": ref.id}, merge=True)
    batch.commit()
    return {"id": ref.id}


def editar_cuadrilla(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Add/remove points from an existing cuadrilla, keeping each point's
    `cuadrilla_id` consistent with membership. Verbatim port of
    `editarCuadrilla`."""
    cuadrilla_id = str(body.get("cuadrilla_id") or "").strip()
    add = [str(p) for p in body.get("add") or []] if isinstance(body.get("add"), list) else []
    remove = [str(p) for p in body.get("remove") or []] if isinstance(body.get("remove"), list) else []
    if not cuadrilla_id:
        raise bad_request("Falta cuadrilla_id.")

    ref = db.collection(CUADRILLAS_COLLECTION).document(cuadrilla_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe la cuadrilla {cuadrilla_id}.")

    # Adding a point that already belongs to a *different* cuadrilla would
    # silently move it. Reject instead. Removing points is always fine, so
    # only `add` is checked.
    if add:
        add_refs = [db.collection(STICKER_MATCHES_COLLECTION).document(pid) for pid in add]
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
        batch.set(db.collection(STICKER_MATCHES_COLLECTION).document(punto_id), {"cuadrilla_id": cuadrilla_id}, merge=True)
    for punto_id in remove:
        batch.set(db.collection(STICKER_MATCHES_COLLECTION).document(punto_id), {"cuadrilla_id": None}, merge=True)
    batch.commit()
    return {"id": cuadrilla_id, "puntos": next_puntos}


def asignar_inspector(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Propagates inspector_uid/asignado_en/estado_asignacion:'asignado' to
    every point currently in the cuadrilla. Verbatim port of
    `asignarInspector` (no per-inspector cap, product decision)."""
    cuadrilla_id = str(body.get("cuadrilla_id") or "").strip()
    inspector_uid = str(body.get("inspector_uid") or "").strip()
    if not cuadrilla_id:
        raise bad_request("Falta cuadrilla_id.")
    if not inspector_uid:
        raise bad_request("Falta inspector_uid.")

    ref = db.collection(CUADRILLAS_COLLECTION).document(cuadrilla_id)
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
            db.collection(STICKER_MATCHES_COLLECTION).document(punto_id),
            {"inspector_uid": inspector_uid, "asignado_en": now, "estado_asignacion": "asignado"},
            merge=True,
        )
    batch.commit()
    return {"id": cuadrilla_id}


def desasignar_inspector(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Removes the inspector from a cuadrilla: clears inspector_uid/
    asignado_en on every member point and resets estado_asignacion to
    'pendiente', but KEEPS the cuadrilla and its membership (unlike
    eliminarCuadrilla). Verbatim port of `desasignarInspector`."""
    cuadrilla_id = str(body.get("cuadrilla_id") or "").strip()
    if not cuadrilla_id:
        raise bad_request("Falta cuadrilla_id.")

    ref = db.collection(CUADRILLAS_COLLECTION).document(cuadrilla_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe la cuadrilla {cuadrilla_id}.")

    puntos = [str(p) for p in (snap.to_dict() or {}).get("puntos") or []]
    batch = db.batch()
    batch.set(ref, {"inspector_uid": None}, merge=True)
    for punto_id in puntos:
        batch.set(
            db.collection(STICKER_MATCHES_COLLECTION).document(punto_id),
            {"inspector_uid": None, "asignado_en": None, "estado_asignacion": "pendiente"},
            merge=True,
        )
    batch.commit()
    return {"puntos": len(puntos)}


def reasignar_punto(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Reassigns a single point to a different inspector, recording the
    previous inspector uid as a one-hop breadcrumb — independent of
    cuadrilla membership. Verbatim port of `reasignarPunto`."""
    punto_id = str(body.get("punto_id") or "").strip()
    nuevo_inspector_uid = str(body.get("nuevo_inspector_uid") or "").strip()
    if not punto_id:
        raise bad_request("Falta punto_id.")
    if not nuevo_inspector_uid:
        raise bad_request("Falta nuevo_inspector_uid.")

    ref = db.collection(STICKER_MATCHES_COLLECTION).document(punto_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe el punto {punto_id}.")

    prev_inspector_uid = (snap.to_dict() or {}).get("inspector_uid")
    ref.set({"inspector_uid": nuevo_inspector_uid, "reasignado_de": prev_inspector_uid}, merge=True)
    return {"id": punto_id, "inspector_uid": nuevo_inspector_uid, "reasignado_de": prev_inspector_uid}


def eliminar_cuadrilla(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Clears cuadrilla_id/inspector_uid on every member point BEFORE
    deleting the cuadrillas doc, so no point is left referencing a
    nonexistent cuadrilla even if the delete step fails partway. Verbatim
    port of `eliminarCuadrilla`."""
    cuadrilla_id = str(body.get("cuadrilla_id") or "").strip()
    if not cuadrilla_id:
        raise bad_request("Falta cuadrilla_id.")

    ref = db.collection(CUADRILLAS_COLLECTION).document(cuadrilla_id)
    snap = ref.get()
    if not snap.exists:
        raise bad_request(f"No existe la cuadrilla {cuadrilla_id}.")

    puntos = [str(p) for p in (snap.to_dict() or {}).get("puntos") or []]
    clear_batch = db.batch()
    for punto_id in puntos:
        clear_batch.set(
            db.collection(STICKER_MATCHES_COLLECTION).document(punto_id),
            {"cuadrilla_id": None, "inspector_uid": None},
            merge=True,
        )
    clear_batch.commit()
    ref.delete()
    return {"id": cuadrilla_id}


def reiniciar_agrupacion(db: Any) -> dict[str, Any]:
    """Undoes every AUTO grouping: releases the member points of all
    `origen:'auto'` cuadrillas back to `pendiente`, then deletes those
    cuadrilla docs. MANUAL cuadrillas are left untouched. Same
    "clear points before deleting the doc" order as eliminarCuadrilla.
    Verbatim port of `reiniciarAgrupacion`."""
    docs = list(db.collection(CUADRILLAS_COLLECTION).where("origen", "==", "auto").get())
    if not docs:
        return {"eliminadas": 0, "puntosLiberados": 0}

    punto_ids: set[str] = set()
    for d in docs:
        for p in (d.to_dict() or {}).get("puntos") or []:
            punto_ids.add(str(p))

    def _clear_point(batch: Any, punto_id: str) -> None:
        batch.set(
            db.collection(STICKER_MATCHES_COLLECTION).document(punto_id),
            {"cuadrilla_id": None, "inspector_uid": None, "asignado_en": None, "estado_asignacion": "pendiente"},
            merge=True,
        )

    commit_in_chunks(db, list(punto_ids), _clear_point)

    def _delete_doc(batch: Any, doc: Any) -> None:
        batch.delete(doc.reference)

    commit_in_chunks(db, docs, _delete_doc)

    return {"eliminadas": len(docs), "puntosLiberados": len(punto_ids)}


class StickerAsignacionesRequest(BaseModel):
    action: str
    maxRadiusM: Any = None
    maxSize: Any = None
    nombre: str | None = None
    puntos: list[str] | None = None
    cuadrilla_id: str | None = None
    add: list[str] | None = None
    remove: list[str] | None = None
    inspector_uid: str | None = None
    punto_id: str | None = None
    nuevo_inspector_uid: str | None = None


@router.post("/sticker-asignaciones")
def sticker_asignaciones(
    body: StickerAsignacionesRequest,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    db = credentials.sismo().firestore
    payload = body.model_dump()

    try:
        if body.action == "listPuntos":
            return JSONResponse({"ok": True, "puntos": list_puntos(db)})
        if body.action == "listCuadrillas":
            return JSONResponse({"ok": True, "cuadrillas": list_cuadrillas(db)})
        if body.action == "autoAgrupar":
            return JSONResponse({"ok": True, "cuadrillas": run_auto_agrupar(db, payload)})
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
        raise bad_request(f"Acción desconocida: {body.action}")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - legacy fail-open surface
        raise HTTPException(status_code=502, detail=str(exc)) from exc
