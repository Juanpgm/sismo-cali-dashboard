"""Sole writer AND sole reader of `planeacion_auditoria` — an append-only
audit log ("bitácora") of every mutating change made through the
`/planeacion-asignaciones` dispatcher (change `planeacion-auditoria`;
design.md ADR-1/ADR-2/ADR-3/ADR-4; spec `Append-only write on successful
mutation`, `A logging failure never alters a completed mutation`,
`listAuditoria read action`, `Audit entries are immutable`, `Sole-writer
invariant`).

The literal `"planeacion_auditoria"` MUST appear ONLY in this file under
`backend/app/` (enforced by `tests/invariants/test_sole_writer.py`) — the
router calls `registrar_best_effort`/`list_auditoria`, never the collection
directly.

No `_rev`, no history subcollection, no diff, no revert — the LIGHTER
variant of the survey campaign's own versioned-history service module
(proposal.md "Altitude decision"). The returned `resultado` (the mutation's
own new state) IS the audit record's payload; no before/after diff is kept.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, NamedTuple

PLANEACION_AUDITORIA_COLLECTION = "planeacion_auditoria"

# Mirrors `routers/planeacion_asignaciones.py`'s own private `_UNSET =
# "__unset__"` sentinel value (not imported — the router imports THIS module
# at module level, so the reverse import would be circular). Same literal,
# independently defined; `editarAsignacion`'s partial-write fields use it to
# distinguish "omitted from the request" from "explicitly set to null".
_UNSET_SENTINEL = "__unset__"


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Drops `action` (redundant with `accion`), `_UNSET`-sentinel values
    (fields the caller never touched — `payload` is a full `model_dump()`
    with every field defaulted), and `None` values, leaving only what the
    caller actually set."""
    return {
        k: v
        for k, v in params.items()
        if k != "action" and v is not None and v != _UNSET_SENTINEL
    }


class _ActionMeta(NamedTuple):
    entidad: str
    id_extractor: Callable[[dict[str, Any], dict[str, Any]], str | None]
    resumen: Callable[[dict[str, Any], dict[str, Any]], str]


def _punto_id(params: dict[str, Any], resultado: dict[str, Any]) -> str | None:
    punto = resultado.get("punto")
    if isinstance(punto, dict):
        return punto.get("id")
    return resultado.get("id")


MUTATING_ACTIONS: dict[str, _ActionMeta] = {
    # grupo
    "crearGrupo": _ActionMeta(
        "grupo",
        lambda p, r: r.get("id"),
        lambda p, r: f"Crear grupo «{p.get('nombre')}»",
    ),
    "editarGrupo": _ActionMeta(
        "grupo",
        lambda p, r: r.get("id") or p.get("grupo_id"),
        lambda p, r: f"Editar grupo «{r.get('nombre') or p.get('grupo_id')}»",
    ),
    "eliminarGrupo": _ActionMeta(
        "grupo",
        lambda p, r: r.get("id") or p.get("grupo_id"),
        lambda p, r: f"Eliminar grupo {p.get('grupo_id')}",
    ),
    "asignarGrupoAPuntos": _ActionMeta(
        "grupo",
        lambda p, r: p.get("grupo_id"),
        lambda p, r: f"Asignar grupo {p.get('grupo_id')} a {len(r.get('puntos') or [])} puntos",
    ),
    "desasignarGrupo": _ActionMeta(
        "grupo",
        lambda p, r: None,
        lambda p, r: f"Desasignar grupo de {len(r.get('puntos') or [])} puntos",
    ),
    # vehiculo
    "crearVehiculo": _ActionMeta(
        "vehiculo",
        lambda p, r: r.get("id"),
        lambda p, r: f"Crear vehículo {p.get('placa')}",
    ),
    "editarVehiculo": _ActionMeta(
        "vehiculo",
        lambda p, r: r.get("id") or p.get("vehiculo_id"),
        lambda p, r: f"Editar vehículo {r.get('placa') or p.get('placa') or p.get('vehiculo_id')}",
    ),
    "eliminarVehiculo": _ActionMeta(
        "vehiculo",
        lambda p, r: r.get("id") or p.get("vehiculo_id"),
        lambda p, r: f"Eliminar vehículo {p.get('vehiculo_id')}",
    ),
    "asignarVehiculoAGrupo": _ActionMeta(
        "vehiculo",
        lambda p, r: p.get("vehiculo_id"),
        lambda p, r: f"Asignar vehículo {p.get('vehiculo_id')} a grupo {p.get('grupo_id')}",
    ),
    "desasignarVehiculo": _ActionMeta(
        "vehiculo",
        lambda p, r: p.get("grupo_id"),
        lambda p, r: f"Desasignar vehículo de grupo {p.get('grupo_id')}",
    ),
    # conductor
    "crearConductor": _ActionMeta(
        "conductor",
        lambda p, r: r.get("id"),
        lambda p, r: f"Crear conductor {p.get('nombre_completo')}",
    ),
    "editarConductor": _ActionMeta(
        "conductor",
        lambda p, r: r.get("id") or p.get("conductor_id"),
        lambda p, r: f"Editar conductor {r.get('nombre_completo') or p.get('conductor_id')}",
    ),
    "eliminarConductor": _ActionMeta(
        "conductor",
        lambda p, r: r.get("id") or p.get("conductor_id"),
        lambda p, r: f"Eliminar conductor {p.get('conductor_id')}",
    ),
    # cuadrilla
    "crearCuadrilla": _ActionMeta(
        "cuadrilla",
        lambda p, r: r.get("id"),
        lambda p, r: f"Crear cuadrilla «{p.get('nombre')}»",
    ),
    "editarCuadrilla": _ActionMeta(
        "cuadrilla",
        lambda p, r: r.get("id") or p.get("cuadrilla_id"),
        lambda p, r: f"Editar cuadrilla {p.get('cuadrilla_id')}",
    ),
    "eliminarCuadrilla": _ActionMeta(
        "cuadrilla",
        lambda p, r: r.get("id") or p.get("cuadrilla_id"),
        lambda p, r: f"Eliminar cuadrilla {p.get('cuadrilla_id')}",
    ),
    "autoAgrupar": _ActionMeta(
        "cuadrilla",
        lambda p, r: None,
        lambda p, r: f"Agrupar automáticamente {len(r.get('cuadrillas') or [])} cuadrillas",
    ),
    "reiniciarAgrupacion": _ActionMeta(
        "cuadrilla",
        lambda p, r: None,
        lambda p, r: f"Reiniciar agrupación automática ({r.get('eliminadas', 0)} cuadrillas eliminadas)",
    ),
    "asignarInspector": _ActionMeta(
        "cuadrilla",
        lambda p, r: p.get("cuadrilla_id"),
        lambda p, r: f"Asignar inspector a cuadrilla {p.get('cuadrilla_id')}",
    ),
    "desasignarInspector": _ActionMeta(
        "cuadrilla",
        lambda p, r: p.get("cuadrilla_id"),
        lambda p, r: f"Desasignar inspector de cuadrilla {p.get('cuadrilla_id')}",
    ),
    # asignacion
    "reasignarPunto": _ActionMeta(
        "asignacion",
        lambda p, r: r.get("id"),
        lambda p, r: f"Reasignar punto {r.get('id')} a otro inspector",
    ),
    "editarAsignacion": _ActionMeta(
        "asignacion",
        _punto_id,
        lambda p, r: f"Editar asignación del punto {_punto_id(p, r)}",
    ),
    "marcarNoAplica": _ActionMeta(
        "asignacion",
        _punto_id,
        lambda p, r: (
            f"Revertir «no aplica» del punto {_punto_id(p, r)}"
            if p.get("revertir")
            else f"Marcar punto {_punto_id(p, r)} como no aplica"
        ),
    ),
    "reopen": _ActionMeta(
        "asignacion",
        _punto_id,
        lambda p, r: "Reabrir punto a pendiente",
    ),
}


def registrar(
    db: Any,
    *,
    actor_uid: str | None,
    actor_email: str | None,
    accion: str,
    params: dict[str, Any],
    resultado: dict[str, Any],
) -> None:
    """Appends exactly one `planeacion_auditoria` doc. Raises (a plain
    `KeyError` via the `MUTATING_ACTIONS[accion]` lookup) if `accion` is not
    a known mutating action — by contract the caller only ever passes a
    `MUTATING_ACTIONS` key (the router's `body.action in MUTATING_ACTIONS`
    gate)."""
    meta = MUTATING_ACTIONS[accion]
    doc = {
        "actor_uid": actor_uid,
        "actor_email": actor_email,
        "accion": accion,
        "entidad": meta.entidad,
        "entidad_id": meta.id_extractor(params, resultado),
        "params": _sanitize_params(params),
        "resultado": resultado,
        "resumen": meta.resumen(params, resultado),
        "ts": _server_timestamp(),
    }
    db.collection(PLANEACION_AUDITORIA_COLLECTION).document().set(doc)


def registrar_best_effort(
    db: Any,
    *,
    actor_uid: str | None,
    actor_email: str | None,
    accion: str,
    params: dict[str, Any],
    resultado: dict[str, Any],
) -> None:
    """ADR-1: the mutation this wraps has ALREADY committed and returned its
    own response by the time this runs. Any exception here (a bad action
    key, a Firestore write failure, ...) is caught and logged, NEVER
    propagated — a dropped audit row is an acceptable, logged loss; it must
    never roll back or fail a completed mutation."""
    try:
        registrar(
            db, actor_uid=actor_uid, actor_email=actor_email, accion=accion,
            params=params, resultado=resultado,
        )
    except Exception:
        logging.exception("planeacion_auditoria append failed for %s", accion)


def _server_timestamp() -> Any:
    from google.cloud import firestore as _fs  # deferred import, credentials/clients.py's own convention
    return _fs.SERVER_TIMESTAMP


PAGE_SIZE_DEFAULT = 50

# planeacion-flujo-confiable (2026-08-27 scope rider): the ORIGINAL
# multi-`.where()` + `.order_by("ts")` query needed THREE composite indexes
# (`entidad+ts`, `actor_uid+ts`, `entidad+actor_uid+ts`) — one per filter
# combination an operator might click — and production was 503ing on any
# filtered query while those indexes built. Bounded over-fetch cap for the
# single `order_by("ts", DESCENDING)` fetch below; `page_size * 5` gives
# comfortable headroom for a filtered page without re-scanning the whole
# collection, capped at 1000 so a huge `page_size` can't demand an
# unbounded read.
_AUDITORIA_OVERFETCH_CAP = 1000


def list_auditoria(
    db: Any,
    *,
    tipo: str | None = None,
    usuario: str | None = None,
    desde: Any = None,
    antes_de: Any = None,
    page_size: int = PAGE_SIZE_DEFAULT,
) -> dict[str, Any]:
    """ADR-4: `ts`-cursor pagination — never `offset`/`start_after`. Needs
    NO composite index (2026-08-27 scope rider): a SINGLE
    `order_by("ts", DESCENDING).limit(_AUDITORIA_OVERFETCH_CAP)` fetch (only
    a single-field index, which Firestore always auto-creates), then
    `entidad`/`actor_uid`/`ts`-range ALL filtered in code — the exact
    "filter the harder conditions in code" tradeoff
    `routers/planeacion_asignaciones.py:list_puntos` already documents
    (Firestore needs one composite index per distinct filter COMBINATION;
    moving every combination into Python needs none, ever, no matter how
    many filters this endpoint later grows).

    Pagination caveat (honest, not silently swept under the over-fetch):
    each call re-fetches the newest `_AUDITORIA_OVERFETCH_CAP` docs by `ts`
    and filters/pages WITHIN that window. If a narrow filter (e.g. one
    `actor_uid`) has more than `_AUDITORIA_OVERFETCH_CAP` non-matching rows
    interleaved ahead of it in `ts` order, a deep page's `hay_mas`/cursor
    can under-report matches older than the window — the same class of
    bound the prior `LIMIT_MAX + 1` over-fetch already accepted elsewhere in
    this codebase, just against a fixed cap instead of the full collection.
    Strictly better than the prior behavior either way: an unfiltered or
    lightly-filtered query (the common case) sees every row within the cap
    with no index dependency at all, where before ANY filtered query 503'd
    outright without the 3 composite indexes built.
    """
    from google.cloud import firestore as _fs  # deferred import, credentials/clients.py's own convention

    fetch_cap = min(page_size * 5, _AUDITORIA_OVERFETCH_CAP)
    query = (
        db.collection(PLANEACION_AUDITORIA_COLLECTION)
        .order_by("ts", direction=_fs.Query.DESCENDING)
        .limit(fetch_cap)
    )
    raw = [_doc_to_dict(d) for d in query.get()]

    def _matches(row: dict[str, Any]) -> bool:
        if tipo and row.get("entidad") != tipo:
            return False
        if usuario and row.get("actor_uid") != usuario:
            return False
        ts = row.get("ts")
        if desde is not None and not (ts is not None and ts >= desde):
            return False
        if antes_de is not None and not (ts is not None and ts < antes_de):
            return False
        return True

    filtered = [r for r in raw if _matches(r)]
    hay_mas = len(filtered) > page_size
    rows = filtered[:page_size]
    return {
        "entradas": rows,
        "hay_mas": hay_mas,
        "antes_de": rows[-1]["ts"] if rows else None,
    }


def _jsonable(value: Any) -> Any:
    """Same normalization `routers/planeacion_asignaciones.py`'s own
    `_jsonable` performs (real Firestore timestamps -> ISO strings so
    `JSONResponse` can encode them) — duplicated here rather than imported,
    since the router already imports THIS module at module level."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _doc_to_dict(doc: Any) -> dict[str, Any]:
    data = _jsonable(doc.to_dict() or {})
    return {"id": doc.id, **data}
