"""POST /sticker-asignaciones (RED first, task 8.3) — design.md ADR-3/ADR-9;
backend-platform spec "sticker_matches And cuadrillas Sole-Writer Invariant"
(route side), "Admin-gated route rejects non-admin".

Ports the action matrix from `api/sticker-asignaciones.test.js`
(`autoAgrupar` determinism/maxSize/maxRadius/empty-input; `listPuntos`/
`listCuadrillas`; `crearCuadrilla`; `editarCuadrilla`; `asignarInspector`;
`reasignarPunto`; `eliminarCuadrilla` clears membership before delete) as
pytest cases against a fake in-memory Firestore (no real service-account
JSON, no network) — same call-count-instrumented `credentials.sismo()`
override convention `test_inspector_asignaciones.py`/`test_stickers.py`
established.

**Deviation flagged for verify** (see apply-progress.md's Batch 8a entry
for the full writeup): `api/sticker-asignaciones.js`'s dispatcher actually
exposes 10 actions, not 8 — `desasignarInspector` and `reiniciarAgrupacion`
exist in the source but are not named in task 8.3/8.4's enumerated list
("the 8-action matrix"). Since 8.4 says "port ... verbatim (all 8 actions
...)" and "verbatim" of the WHOLE FILE necessarily includes every dispatch
branch, this file covers all 10 — the 8 explicitly named by the task get
the fuller scenario coverage (mirroring the JS test file's own emphasis),
the 2 extras get one success-path + one admin-gate case each so the
verbatim claim is actually exercised, not just implemented.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app
from app.routers import sticker_asignaciones as sa

UID_ADMIN = "uid-admin"
FAKE_CLAIMS_ADMIN = {"sub": UID_ADMIN, "email": "admin@example.com", "role": "admin"}
FAKE_CLAIMS_VIEWER = {"sub": "uid-viewer", "email": "someone@gmail.com"}

STICKER_MATCHES = "sticker_matches"
CUADRILLAS = "cuadrillas"


# ── Fake Firestore: path-keyed by (collection, id); supports .where()
# chaining, batch(), get_all(), and auto-id document() creation — the full
# surface sticker_asignaciones.py's 10 actions need. ────────────────────────


class _FakeSnapshot:
    def __init__(self, collection: str, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None
        self.reference = _FakeDocRef.__new__(_FakeDocRef)  # patched below

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self, store: dict[str, dict[str, Any]], collection: str, doc_id: str) -> None:
        self._store = store
        self._collection = collection
        self.id = doc_id

    def get(self) -> _FakeSnapshot:
        snap = _FakeSnapshot(self._collection, self.id, self._store.get(self.id))
        snap.reference = self
        return snap

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        current = dict(self._store.get(self.id, {})) if merge else {}
        current.update(data)
        self._store[self.id] = current

    def delete(self) -> None:
        self._store.pop(self.id, None)


class _FakeQuery:
    def __init__(self, collection: str, store: dict[str, dict[str, Any]], docs: list[str] | None = None) -> None:
        self._collection = collection
        self._store = store
        self._ids = docs if docs is not None else list(store.keys())

    def where(self, field: str, op: str, value: Any) -> "_FakeQuery":
        assert op == "=="
        matched = [i for i in self._ids if self._store.get(i, {}).get(field) == value]
        return _FakeQuery(self._collection, self._store, matched)

    def get(self) -> list[_FakeSnapshot]:
        snaps = []
        for doc_id in self._ids:
            snap = _FakeSnapshot(self._collection, doc_id, self._store.get(doc_id))
            snap.reference = _FakeDocRef(self._store, self._collection, doc_id)
            snaps.append(snap)
        return snaps


class _FakeCollection(_FakeQuery):
    def __init__(self, collection: str, store: dict[str, dict[str, Any]]) -> None:
        super().__init__(collection, store)
        self._auto_seq = 0

    def document(self, doc_id: str | None = None) -> _FakeDocRef:
        if doc_id is None:
            self._auto_seq += 1
            doc_id = f"auto-{self._collection}-{self._auto_seq}"
        return _FakeDocRef(self._store, self._collection, doc_id)


class _FakeBatch:
    def __init__(self) -> None:
        self._ops: list[tuple[str, _FakeDocRef, dict[str, Any] | None, bool]] = []

    def set(self, ref: _FakeDocRef, data: dict[str, Any], merge: bool = False) -> None:
        self._ops.append(("set", ref, data, merge))

    def delete(self, ref: _FakeDocRef) -> None:
        self._ops.append(("delete", ref, None, False))

    def commit(self) -> None:
        for kind, ref, data, merge in self._ops:
            if kind == "set":
                ref.set(data, merge=merge)
            else:
                ref.delete()
        self._ops = []


class _FakeFirestore:
    def __init__(self, stores: dict[str, dict[str, dict[str, Any]]]) -> None:
        self._stores = stores

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(name, self._stores.setdefault(name, {}))

    def batch(self) -> _FakeBatch:
        return _FakeBatch()

    def get_all(self, refs: list[_FakeDocRef]) -> list[_FakeSnapshot]:
        return [ref.get() for ref in refs]


class _FakeSismoClients:
    def __init__(self, stores: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.firestore = _FakeFirestore(stores)
        self.app = object()


def _stores() -> dict[str, dict[str, dict[str, Any]]]:
    return {STICKER_MATCHES: {}, CUADRILLAS: {}}


def _app(monkeypatch, stores: dict[str, dict[str, dict[str, Any]]]) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    credentials.s3.cache_clear()
    monkeypatch.setattr(credentials, "sismo", lambda: _FakeSismoClients(stores))
    return create_app()


def _admin_client(monkeypatch, stores) -> TestClient:
    app = _app(monkeypatch, stores)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN
    return TestClient(app)


def _viewer_client(monkeypatch, stores) -> TestClient:
    app = _app(monkeypatch, stores)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER
    return TestClient(app)


# ── Pure autoAgrupar/haversineM determinism (api/sticker-asignaciones.test.js) ─


def _pt(id_: str, lat: float, lon: float) -> dict[str, Any]:
    return {"id": id_, "coords": {"lat": lat, "lon": lon}}


def _group_ids(groups: list[list[dict[str, Any]]]) -> list[list[str]]:
    return [sorted(p["id"] for p in g) for g in groups]


def test_auto_agrupar_is_deterministic_same_input_twice():
    fixture = [_pt("a", 3.40, -76.50), _pt("b", 3.4001, -76.5001), _pt("c", 3.50, -76.60)]
    run1 = _group_ids(sa.auto_agrupar(fixture, max_radius_m=800, max_size=8))
    run2 = _group_ids(sa.auto_agrupar(fixture, max_radius_m=800, max_size=8))
    assert run1 == run2
    assert len(run1) == 2
    assert next(g for g in run1 if "a" in g) == ["a", "b"]


def test_auto_agrupar_respects_max_size_cap():
    dense = [_pt(f"p{i}", 3.40 + i * 0.00001, -76.50) for i in range(10)]
    capped = sa.auto_agrupar(dense, max_radius_m=800, max_size=3)
    assert all(len(g) <= 3 for g in capped)
    assert sum(len(g) for g in capped) == len(dense)


def test_auto_agrupar_respects_max_radius_cap():
    fixture = [_pt("seed", 3.40, -76.50), _pt("far", 3.50, -76.60)]
    groups = _group_ids(sa.auto_agrupar(fixture, max_radius_m=100, max_size=8))
    assert groups == [["far"], ["seed"]] or groups == [["seed"], ["far"]]
    assert len(groups) == 2


def test_auto_agrupar_empty_input_returns_empty_list():
    assert sa.auto_agrupar([], max_radius_m=800, max_size=8) == []


# ── Router: admin-gate rejection, no mutation ───────────────────────────────


@pytest.mark.parametrize(
    "action",
    [
        "listPuntos",
        "listCuadrillas",
        "autoAgrupar",
        "crearCuadrilla",
        "editarCuadrilla",
        "asignarInspector",
        "desasignarInspector",
        "reasignarPunto",
        "eliminarCuadrilla",
        "reiniciarAgrupacion",
        "asignarGrupoAPuntos",
        "desasignarGrupo",
    ],
)
def test_non_admin_is_rejected_no_mutation(monkeypatch, action):
    stores = _stores()
    stores[STICKER_MATCHES]["p1"] = {"estado_asignacion": "pendiente", "cuadrilla_id": None}
    client = _viewer_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": action, "cuadrilla_id": "c1", "punto_id": "p1", "puntos": ["p1"], "grupo_id": "g1"},
    )

    assert resp.status_code == 403
    assert stores[STICKER_MATCHES]["p1"] == {"estado_asignacion": "pendiente", "cuadrilla_id": None}
    assert stores[CUADRILLAS] == {}


def test_unauthenticated_is_rejected(monkeypatch):
    stores = _stores()
    app = _app(monkeypatch, stores)
    client = TestClient(app)

    resp = client.post("/sticker-asignaciones", json={"action": "listPuntos"})

    assert resp.status_code == 401


# ── listPuntos / listCuadrillas ──────────────────────────────────────────────


def test_list_puntos_returns_every_sticker_match(monkeypatch):
    stores = _stores()
    stores[STICKER_MATCHES] = {"p1": {"estado_asignacion": "pendiente"}, "p2": {"estado_asignacion": "hecho"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "listPuntos"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert {p["id"] for p in body["puntos"]} == {"p1", "p2"}


def test_list_cuadrillas_returns_every_cuadrilla(monkeypatch):
    stores = _stores()
    stores[CUADRILLAS] = {"c1": {"nombre": "Zona 1", "puntos": []}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "listCuadrillas"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["cuadrillas"] == [{"id": "c1", "nombre": "Zona 1", "puntos": []}]


# ── crearCuadrilla ────────────────────────────────────────────────────────


def test_crear_cuadrilla_succeeds_and_sets_cuadrilla_id_on_points(monkeypatch):
    stores = _stores()
    stores[STICKER_MATCHES] = {
        "p1": {"cuadrilla_id": None, "tiene_sticker": False, "colapso": "no"},
        "p2": {"cuadrilla_id": None, "tiene_sticker": False, "colapso": "parcial"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": "crearCuadrilla", "nombre": "Zona 1", "puntos": ["p1", "p2"]},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["ok"] is True
    cuadrilla_id = body["id"]
    assert stores[CUADRILLAS][cuadrilla_id]["puntos"] == ["p1", "p2"]
    assert stores[STICKER_MATCHES]["p1"]["cuadrilla_id"] == cuadrilla_id
    assert stores[STICKER_MATCHES]["p2"]["cuadrilla_id"] == cuadrilla_id


def test_crear_cuadrilla_rejects_already_stickered_points(monkeypatch):
    stores = _stores()
    stores[STICKER_MATCHES] = {"p1": {"cuadrilla_id": None, "tiene_sticker": True, "colapso": "no"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "crearCuadrilla", "puntos": ["p1"]})

    assert resp.status_code == 400
    assert stores[CUADRILLAS] == {}


def test_crear_cuadrilla_rejects_points_already_in_another_cuadrilla(monkeypatch):
    stores = _stores()
    stores[STICKER_MATCHES] = {"p1": {"cuadrilla_id": "c-existing", "tiene_sticker": False, "colapso": "no"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "crearCuadrilla", "puntos": ["p1"]})

    assert resp.status_code == 400
    assert stores[CUADRILLAS] == {}


def test_crear_cuadrilla_requires_at_least_one_punto(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "crearCuadrilla", "puntos": []})

    assert resp.status_code == 400


# ── editarCuadrilla ───────────────────────────────────────────────────────


def test_editar_cuadrilla_adds_and_removes_points(monkeypatch):
    stores = _stores()
    stores[CUADRILLAS] = {"c1": {"puntos": ["p1"], "inspector_uid": None, "origen": "manual"}}
    stores[STICKER_MATCHES] = {
        "p1": {"cuadrilla_id": "c1"},
        "p2": {"cuadrilla_id": None},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": "editarCuadrilla", "cuadrilla_id": "c1", "add": ["p2"], "remove": ["p1"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["puntos"]) == {"p2"}
    assert stores[STICKER_MATCHES]["p2"]["cuadrilla_id"] == "c1"
    assert stores[STICKER_MATCHES]["p1"]["cuadrilla_id"] is None
    assert set(stores[CUADRILLAS]["c1"]["puntos"]) == {"p2"}


def test_editar_cuadrilla_rejects_adding_point_from_another_cuadrilla(monkeypatch):
    stores = _stores()
    stores[CUADRILLAS] = {"c1": {"puntos": [], "inspector_uid": None, "origen": "manual"}}
    stores[STICKER_MATCHES] = {"p1": {"cuadrilla_id": "c2"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": "editarCuadrilla", "cuadrilla_id": "c1", "add": ["p1"], "remove": []},
    )

    assert resp.status_code == 400
    assert stores[STICKER_MATCHES]["p1"]["cuadrilla_id"] == "c2"


def test_editar_cuadrilla_nonexistent_is_rejected(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": "editarCuadrilla", "cuadrilla_id": "missing", "add": [], "remove": []},
    )

    assert resp.status_code == 400


# ── asignarInspector ──────────────────────────────────────────────────────


def test_asignar_inspector_propagates_to_every_member_point(monkeypatch):
    stores = _stores()
    stores[CUADRILLAS] = {"c1": {"puntos": ["p1", "p2"], "inspector_uid": None, "origen": "manual"}}
    stores[STICKER_MATCHES] = {"p1": {}, "p2": {}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": "asignarInspector", "cuadrilla_id": "c1", "inspector_uid": "insp-1"},
    )

    assert resp.status_code == 200
    assert stores[CUADRILLAS]["c1"]["inspector_uid"] == "insp-1"
    assert stores[STICKER_MATCHES]["p1"]["inspector_uid"] == "insp-1"
    assert stores[STICKER_MATCHES]["p1"]["estado_asignacion"] == "asignado"
    assert stores[STICKER_MATCHES]["p2"]["estado_asignacion"] == "asignado"


def test_asignar_inspector_missing_fields_rejected(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "asignarInspector", "cuadrilla_id": ""})

    assert resp.status_code == 400


# ── desasignarInspector (extra action beyond the task's named 8, see module docstring) ─


def test_desasignar_inspector_clears_assignment_keeps_cuadrilla(monkeypatch):
    stores = _stores()
    stores[CUADRILLAS] = {"c1": {"puntos": ["p1"], "inspector_uid": "insp-1", "origen": "manual"}}
    stores[STICKER_MATCHES] = {"p1": {"inspector_uid": "insp-1", "estado_asignacion": "asignado"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "desasignarInspector", "cuadrilla_id": "c1"})

    assert resp.status_code == 200
    assert stores[CUADRILLAS]["c1"]["inspector_uid"] is None
    assert stores[STICKER_MATCHES]["p1"]["inspector_uid"] is None
    assert stores[STICKER_MATCHES]["p1"]["estado_asignacion"] == "pendiente"
    assert "c1" in stores[CUADRILLAS]  # cuadrilla itself is kept, unlike eliminarCuadrilla


# ── reasignarPunto ────────────────────────────────────────────────────────


def test_reasignar_punto_records_previous_inspector(monkeypatch):
    stores = _stores()
    stores[STICKER_MATCHES] = {"p1": {"inspector_uid": "insp-old"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": "reasignarPunto", "punto_id": "p1", "nuevo_inspector_uid": "insp-new"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "id": "p1", "inspector_uid": "insp-new", "reasignado_de": "insp-old"}
    assert stores[STICKER_MATCHES]["p1"]["inspector_uid"] == "insp-new"
    assert stores[STICKER_MATCHES]["p1"]["reasignado_de"] == "insp-old"


def test_reasignar_punto_nonexistent_is_rejected(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": "reasignarPunto", "punto_id": "missing", "nuevo_inspector_uid": "insp-new"},
    )

    assert resp.status_code == 400


# ── eliminarCuadrilla: clears membership BEFORE deleting the doc ────────────


def test_eliminar_cuadrilla_clears_membership_before_delete(monkeypatch):
    stores = _stores()
    stores[CUADRILLAS] = {"c1": {"puntos": ["p1", "p2"], "inspector_uid": "insp-1", "origen": "manual"}}
    stores[STICKER_MATCHES] = {
        "p1": {"cuadrilla_id": "c1", "inspector_uid": "insp-1"},
        "p2": {"cuadrilla_id": "c1", "inspector_uid": "insp-1"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "eliminarCuadrilla", "cuadrilla_id": "c1"})

    assert resp.status_code == 200
    assert "c1" not in stores[CUADRILLAS]
    assert stores[STICKER_MATCHES]["p1"]["cuadrilla_id"] is None
    assert stores[STICKER_MATCHES]["p1"]["inspector_uid"] is None
    assert stores[STICKER_MATCHES]["p2"]["cuadrilla_id"] is None


def test_eliminar_cuadrilla_nonexistent_is_rejected(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "eliminarCuadrilla", "cuadrilla_id": "missing"})

    assert resp.status_code == 400


# ── autoAgrupar (router path — excludes stickered/total-collapse points) ────


def test_auto_agrupar_router_creates_cuadrillas_from_pending_points(monkeypatch):
    stores = _stores()
    stores[STICKER_MATCHES] = {
        "p1": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "coords": {"lat": 3.40, "lon": -76.50}, "tiene_sticker": False, "colapso": "no"},
        "p2": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "coords": {"lat": 3.4001, "lon": -76.5001}, "tiene_sticker": False, "colapso": "no"},
        "p3": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "coords": {"lat": 3.40, "lon": -76.50}, "tiene_sticker": True, "colapso": "no"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "autoAgrupar"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["cuadrillas"]) == 1  # p1+p2 grouped; p3 excluded (tiene_sticker)
    grupo = body["cuadrillas"][0]
    assert set(grupo["puntos"]) == {"p1", "p2"}
    assert stores[STICKER_MATCHES]["p1"]["cuadrilla_id"] == grupo["id"]
    assert stores[STICKER_MATCHES]["p3"]["cuadrilla_id"] is None  # untouched


def test_auto_agrupar_router_empty_when_no_pending_points(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "autoAgrupar"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "cuadrillas": []}


# ── reiniciarAgrupacion (extra action beyond the task's named 8, see module docstring) ─


def test_reiniciar_agrupacion_releases_only_auto_cuadrillas(monkeypatch):
    stores = _stores()
    stores[CUADRILLAS] = {
        "c-auto": {"puntos": ["p1"], "inspector_uid": "insp-1", "origen": "auto"},
        "c-manual": {"puntos": ["p2"], "inspector_uid": "insp-2", "origen": "manual"},
    }
    stores[STICKER_MATCHES] = {
        "p1": {"cuadrilla_id": "c-auto", "inspector_uid": "insp-1", "estado_asignacion": "asignado"},
        "p2": {"cuadrilla_id": "c-manual", "inspector_uid": "insp-2", "estado_asignacion": "asignado"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "reiniciarAgrupacion"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "eliminadas": 1, "puntosLiberados": 1}
    assert "c-auto" not in stores[CUADRILLAS]
    assert "c-manual" in stores[CUADRILLAS]  # manual cuadrillas untouched
    assert stores[STICKER_MATCHES]["p1"]["cuadrilla_id"] is None
    assert stores[STICKER_MATCHES]["p1"]["estado_asignacion"] == "pendiente"
    assert stores[STICKER_MATCHES]["p2"]["cuadrilla_id"] == "c-manual"  # untouched


# ── Unknown action ────────────────────────────────────────────────────────


def test_unrecognized_action_is_rejected(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "bogus"})

    assert resp.status_code == 400


# ── `grupos-inspectores` change (2026-08-26): sticker-campaign counterpart of
# planeacion_asignaciones.py's own asignarGrupoAPuntos/desasignarGrupo —
# writes/clears `grupo_id` on `sticker_matches` docs ONLY. Group CRUD itself
# (creating/editing/deleting a `grupos_inspectores` doc) is NOT here — it is
# exclusively owned by `routers/planeacion_asignaciones.py` (campaign-
# agnostic single owner); this router only VALIDATES a `grupo_id` exists
# before writing it, same read-before-write discipline `crear_cuadrilla`
# already uses. ──────────────────────────────────────────────────────────


GRUPOS_INSPECTORES = "grupos_inspectores"


def test_asignar_grupo_a_puntos_sets_grupo_id_on_sticker_points(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[STICKER_MATCHES] = {"s1": {}, "s2": {}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["s1", "s2"]},
    )

    assert resp.status_code == 200
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] == "g1"
    assert stores[STICKER_MATCHES]["s2"]["grupo_id"] == "g1"
    # Individual assignment (inspector_uid) untouched — coexistence.
    assert "inspector_uid" not in stores[STICKER_MATCHES]["s1"]


def test_asignar_grupo_a_puntos_rejects_nonexistent_grupo(monkeypatch):
    stores = _stores()
    stores[STICKER_MATCHES] = {"s1": {}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/sticker-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "missing", "puntos": ["s1"]},
    )

    assert resp.status_code == 400
    assert "grupo_id" not in stores[STICKER_MATCHES]["s1"]


def test_desasignar_grupo_clears_field_on_sticker_points(monkeypatch):
    stores = _stores()
    stores[STICKER_MATCHES] = {"s1": {"grupo_id": "g1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/sticker-asignaciones", json={"action": "desasignarGrupo", "puntos": ["s1"]})

    assert resp.status_code == 200
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] is None
