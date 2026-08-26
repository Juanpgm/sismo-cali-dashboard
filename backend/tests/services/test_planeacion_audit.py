"""`backend/app/services/planeacion_audit.py` — sole writer of the
`planeacion_auditoria` append-only bitácora (change `planeacion-auditoria`,
Phase 1). design.md ADR-1/ADR-2/ADR-3; spec `Append-only write on successful
mutation`, `A logging failure never alters a completed mutation`.

Minimal fake Firestore double: only `.collection(name).document().set(doc)`
is needed here (no query support — that's Phase 3's job, on the router's own
richer fake double).
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from app.services import planeacion_audit as audit


class _FakeDocRef:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store = store
        self.id = doc_id

    def set(self, data: dict[str, Any]) -> None:
        self._store[self.id] = data


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store
        self._seq = 0

    def document(self) -> _FakeDocRef:
        self._seq += 1
        return _FakeDocRef(self._store, f"auto-{self._seq}")


class _FakeFirestore:
    def __init__(self) -> None:
        self.stores: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self.stores.setdefault(name, {}))


UID = "uid-admin"
EMAIL = "admin@example.com"


def _one_doc(db: _FakeFirestore) -> dict[str, Any]:
    docs = db.stores.get(audit.PLANEACION_AUDITORIA_COLLECTION, {})
    assert len(docs) == 1, f"expected exactly one doc, found {len(docs)}"
    return next(iter(docs.values()))


# ── registrar: one correct doc per entidad shape ────────────────────────────


@pytest.mark.parametrize(
    "accion,entidad,params,resultado",
    [
        ("crearGrupo", "grupo", {"nombre": "Norte", "miembros": ["u1"]}, {"ok": True, "id": "g1"}),
        ("crearVehiculo", "vehiculo", {"placa": "ABC123"}, {"ok": True, "id": "v1"}),
        ("crearConductor", "conductor", {"cedula": "123", "nombre_completo": "Juan Perez"}, {"ok": True, "id": "c1"}),
        (
            "editarAsignacion",
            "asignacion",
            {"punto_id": "p1", "estado_asignacion": "asignado"},
            {"ok": True, "punto": {"id": "p1", "estado_asignacion": "asignado"}},
        ),
        ("crearCuadrilla", "cuadrilla", {"nombre": "Cuadrilla A", "puntos": ["p1"]}, {"ok": True, "id": "cu1"}),
    ],
)
def test_registrar_writes_one_doc_with_required_fields(accion, entidad, params, resultado):
    db = _FakeFirestore()
    audit.registrar(
        db, actor_uid=UID, actor_email=EMAIL, accion=accion, params=params, resultado=resultado
    )
    doc = _one_doc(db)
    for key in ("actor_uid", "actor_email", "accion", "entidad", "entidad_id", "params", "resultado", "resumen", "ts"):
        assert key in doc, f"missing {key!r} in {doc!r}"
    assert doc["actor_uid"] == UID
    assert doc["actor_email"] == EMAIL
    assert doc["accion"] == accion
    assert doc["entidad"] == entidad


@pytest.mark.parametrize("accion", ["autoAgrupar", "reiniciarAgrupacion"])
def test_bulk_actions_have_no_natural_entidad_id(accion):
    db = _FakeFirestore()
    resultado = {"ok": True, "cuadrillas": [{"id": "c1"}, {"id": "c2"}]} if accion == "autoAgrupar" \
        else {"ok": True, "eliminadas": 2, "puntosLiberados": 5}
    audit.registrar(db, actor_uid=UID, actor_email=EMAIL, accion=accion, params={}, resultado=resultado)
    doc = _one_doc(db)
    assert doc["entidad_id"] is None


# ── resumen: exact neutral-Spanish-infinitive strings (design.md examples) ──


def test_resumen_crear_grupo_single_id_shape():
    db = _FakeFirestore()
    audit.registrar(
        db, actor_uid=UID, actor_email=EMAIL, accion="crearGrupo",
        params={"nombre": "Norte"}, resultado={"ok": True, "id": "g1"},
    )
    assert _one_doc(db)["resumen"] == "Crear grupo «Norte»"


def test_resumen_editar_vehiculo_rename_edit_shape():
    db = _FakeFirestore()
    audit.registrar(
        db, actor_uid=UID, actor_email=EMAIL, accion="editarVehiculo",
        params={"vehiculo_id": "v1", "placa": "ABC123"},
        resultado={"ok": True, "id": "v1", "placa": "ABC123"},
    )
    assert _one_doc(db)["resumen"] == "Editar vehículo ABC123"


def test_resumen_auto_agrupar_bulk_count_shape():
    db = _FakeFirestore()
    resultado = {"ok": True, "cuadrillas": [{"id": f"c{i}"} for i in range(12)]}
    audit.registrar(
        db, actor_uid=UID, actor_email=EMAIL, accion="autoAgrupar", params={}, resultado=resultado,
    )
    assert _one_doc(db)["resumen"] == "Agrupar automáticamente 12 cuadrillas"


# ── _sanitize_params ─────────────────────────────────────────────────────────


def test_sanitize_params_drops_action_unset_and_none():
    params = {
        "action": "editarAsignacion",
        "punto_id": "p1",
        "estado_asignacion": "__unset__",
        "notas": None,
        "prioridad_override": "alta",
    }
    sanitized = audit._sanitize_params(params)
    assert sanitized == {"punto_id": "p1", "prioridad_override": "alta"}


# ── unknown action contract ──────────────────────────────────────────────────


def test_registrar_with_unknown_action_raises():
    db = _FakeFirestore()
    with pytest.raises(Exception):
        audit.registrar(
            db, actor_uid=UID, actor_email=EMAIL, accion="borrarTodo", params={}, resultado={},
        )
    assert db.stores.get(audit.PLANEACION_AUDITORIA_COLLECTION, {}) == {}


# ── registrar_best_effort: never propagates, always logs (task 1.3) ────────


def test_registrar_best_effort_swallows_and_logs_exception(monkeypatch, caplog):
    db = _FakeFirestore()

    def _boom(*args, **kwargs):
        raise RuntimeError("firestore is down")

    monkeypatch.setattr(audit, "registrar", _boom)
    with caplog.at_level(logging.ERROR):
        audit.registrar_best_effort(
            db, actor_uid=UID, actor_email=EMAIL, accion="crearGrupo", params={}, resultado={},
        )
    assert any("planeacion_auditoria append failed" in rec.message for rec in caplog.records)


def test_registrar_best_effort_calls_through_on_success():
    db = _FakeFirestore()
    audit.registrar_best_effort(
        db, actor_uid=UID, actor_email=EMAIL, accion="crearGrupo",
        params={"nombre": "Norte"}, resultado={"ok": True, "id": "g1"},
    )
    assert _one_doc(db)["accion"] == "crearGrupo"
