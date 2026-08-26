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
