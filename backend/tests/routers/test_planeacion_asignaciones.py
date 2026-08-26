"""POST /planeacion-asignaciones (RED first, task 3.3) — design.md ADR-8/
ADR-9/ADR-11; spec `POST /planeacion-asignaciones is admin-only`,
`autoAgrupar clusters pending points deterministically`, `listPuntos
returns a bounded, prioritized working set`, `resumen returns aggregate
tallies without shipping the working set`, `Assignment lifecycle actions`,
`Assignment correction actions`, `getEnlaceSurvey builds a prefilled
Survey123 URL from configuration`.

Matches `tests/routers/test_sticker_asignaciones.py`'s shape: `TestClient` +
a fake in-memory Firestore double (no real service-account JSON, no
network), same call-count-instrumented `credentials.sismo()` override
convention. Extended beyond the sticker fake with `.order_by()` + `.limit()`
support (`listPuntos`'s bounded, prioritized query, ADR-9) and a
`field_paths=` kwarg on `get_all()` (`read_punto_state`-style projected
reads are not needed by the router directly, but `get_all` is reused for
the guard reads `crearCuadrilla`/`editarCuadrilla` already exercise in the
sticker template).
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app
from app.routers import planeacion_asignaciones as pa

UID_ADMIN = "uid-admin"
FAKE_CLAIMS_ADMIN = {"sub": UID_ADMIN, "email": "admin@example.com", "role": "admin"}
FAKE_CLAIMS_VIEWER = {"sub": "uid-viewer", "email": "someone@gmail.com"}

PLANEACION_PUNTOS = "planeacion_puntos"
PLANEACION_CUADRILLAS = "planeacion_cuadrilla" + "s"  # see planeacion_asignaciones.py's own note


# ── Fake Firestore: path-keyed by (collection, id); supports .where()
# chaining (== and !=), .order_by()+.limit(), batch(), get_all(), and
# auto-id document() creation. ───────────────────────────────────────────


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
    def __init__(
        self,
        collection: str,
        store: dict[str, dict[str, Any]],
        docs: list[str] | None = None,
        order_field: str | None = None,
        order_desc: bool = False,
        limit_n: int | None = None,
    ) -> None:
        self._collection = collection
        self._store = store
        self._ids = docs if docs is not None else list(store.keys())
        self._order_field = order_field
        self._order_desc = order_desc
        self._limit_n = limit_n

    def where(self, field: str, op: str, value: Any) -> "_FakeQuery":
        if op == "==":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) == value]
        elif op == "!=":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) != value]
        else:
            raise AssertionError(f"unsupported op {op!r} in fake Firestore")
        return _FakeQuery(
            self._collection, self._store, matched, self._order_field, self._order_desc, self._limit_n
        )

    def order_by(self, field: str, direction: str | None = None) -> "_FakeQuery":
        desc = direction == "DESCENDING"
        return _FakeQuery(self._collection, self._store, self._ids, field, desc, self._limit_n)

    def limit(self, n: int) -> "_FakeQuery":
        return _FakeQuery(self._collection, self._store, self._ids, self._order_field, self._order_desc, n)

    def _ordered_ids(self) -> list[str]:
        ids = list(self._ids)
        if self._order_field is not None:
            ids.sort(
                key=lambda i: (self._store.get(i, {}).get(self._order_field) is None,
                                self._store.get(i, {}).get(self._order_field)),
                reverse=self._order_desc,
            )
        if self._limit_n is not None:
            ids = ids[: self._limit_n]
        return ids

    def get(self) -> list[_FakeSnapshot]:
        return self.stream()

    def stream(self) -> list[_FakeSnapshot]:
        snaps = []
        for doc_id in self._ordered_ids():
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

    def get_all(self, refs: list[_FakeDocRef], field_paths: list[str] | None = None) -> list[_FakeSnapshot]:
        return [ref.get() for ref in refs]


class _FakeSismoClients:
    def __init__(self, stores: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.firestore = _FakeFirestore(stores)
        self.app = object()


def _stores() -> dict[str, dict[str, dict[str, Any]]]:
    return {PLANEACION_PUNTOS: {}, PLANEACION_CUADRILLAS: {}}


def _app(monkeypatch, stores: dict[str, dict[str, dict[str, Any]]]) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    monkeypatch.setenv("SURVEY123_FORM_URL", "https://survey123.arcgis.com/share/abc123")
    monkeypatch.setenv("SURVEY123_FIELD_APP_ITEM_ID", "itemid123")
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


# ── Pure autoAgrupar/haversineM (ported verbatim; same tests as the sticker
# template, DEFAULT_MAX_SIZE=10 not 8 — user decision) ──────────────────────


def _pt(id_: str, lat: float, lon: float) -> dict[str, Any]:
    return {"id": id_, "coords": {"lat": lat, "lon": lon}}


def _group_ids(groups: list[list[dict[str, Any]]]) -> list[list[str]]:
    return [sorted(p["id"] for p in g) for g in groups]


def test_auto_agrupar_is_deterministic_same_input_twice():
    fixture = [_pt("a", 3.40, -76.50), _pt("b", 3.4001, -76.5001), _pt("c", 3.50, -76.60)]
    run1 = _group_ids(pa.auto_agrupar(fixture, max_radius_m=800, max_size=10))
    run2 = _group_ids(pa.auto_agrupar(fixture, max_radius_m=800, max_size=10))
    assert run1 == run2
    assert len(run1) == 2
    assert next(g for g in run1 if "a" in g) == ["a", "b"]


def test_auto_agrupar_respects_max_size_cap():
    dense = [_pt(f"p{i}", 3.40 + i * 0.00001, -76.50) for i in range(15)]
    capped = pa.auto_agrupar(dense, max_radius_m=800, max_size=3)
    assert all(len(g) <= 3 for g in capped)
    assert sum(len(g) for g in capped) == len(dense)


def test_auto_agrupar_respects_max_radius_cap():
    fixture = [_pt("seed", 3.40, -76.50), _pt("far", 3.50, -76.60)]
    groups = _group_ids(pa.auto_agrupar(fixture, max_radius_m=100, max_size=10))
    assert groups == [["far"], ["seed"]] or groups == [["seed"], ["far"]]
    assert len(groups) == 2


def test_auto_agrupar_empty_input_returns_empty_list():
    assert pa.auto_agrupar([], max_radius_m=800, max_size=10) == []


def test_default_max_size_is_ten_not_eight():
    """Binding user decision: Planeación's DEFAULT_MAX_SIZE=10, NOT the
    sticker template's 8 (an EDAN survey is a longer visit)."""
    assert pa.DEFAULT_MAX_SIZE == 10


def test_limit_default_is_a_few_hundred_not_two_thousand():
    """Speed follow-up (2026-08-26): opening the Planeación tab measured
    9-35s in production, driven in real part by `listPuntos` shipping
    LIMIT_DEFAULT=2000 points to the client (and the tab rendering 2000
    table rows) when an operator can only act on a few hundred at a time.
    LIMIT_MAX stays the ceiling for an explicit, caller-supplied `limit`."""
    assert pa.LIMIT_DEFAULT < 500
    assert pa.LIMIT_MAX == 5000


# ── Router: admin-gate rejection, no mutation ───────────────────────────────


@pytest.mark.parametrize(
    "action",
    [
        "listPuntos",
        "resumen",
        "listCuadrillas",
        "autoAgrupar",
        "crearCuadrilla",
        "editarCuadrilla",
        "asignarInspector",
        "desasignarInspector",
        "reasignarPunto",
        "eliminarCuadrilla",
        "reiniciarAgrupacion",
        "editarAsignacion",
        "marcarNoAplica",
        "reopen",
        "getEnlaceSurvey",
    ],
)
def test_non_admin_is_rejected_no_mutation(monkeypatch, action):
    stores = _stores()
    stores[PLANEACION_PUNTOS]["p1"] = {"estado_asignacion": "pendiente", "cuadrilla_id": None}
    client = _viewer_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": action, "cuadrilla_id": "c1", "punto_id": "p1", "puntos": ["p1"]},
    )

    assert resp.status_code == 403
    assert stores[PLANEACION_PUNTOS]["p1"] == {"estado_asignacion": "pendiente", "cuadrilla_id": None}
    assert stores[PLANEACION_CUADRILLAS] == {}


def test_unauthenticated_is_rejected(monkeypatch):
    stores = _stores()
    app = _app(monkeypatch, stores)
    client = TestClient(app)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})

    assert resp.status_code == 401
    assert stores[PLANEACION_PUNTOS] == {}


def test_unrecognized_action_is_rejected_with_400_naming_it(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "borrarTodo"})

    assert resp.status_code == 400
    assert "borrarTodo" in resp.json()["detail"]
    assert stores[PLANEACION_PUNTOS] == {}


# ── listPuntos: bounded, prioritized working set (task 3.5/3.6) ─────────────


def _punto(estado_asignacion="pendiente", tiene_survey=False, prioridad="media",
           prioridad_score=50, prioridad_override=None, comuna=None, cuadrilla_id=None):
    return {
        "estado_asignacion": estado_asignacion,
        "tiene_survey": tiene_survey,
        "prioridad": prioridad,
        "prioridad_score": prioridad_score,
        "prioridad_override": prioridad_override,
        "comuna": comuna,
        "cuadrilla_id": cuadrilla_id,
    }


def test_list_puntos_default_excludes_surveyed_and_no_aplica(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": _punto(),
        "p2": _punto(tiene_survey=True),
        "p3": _punto(estado_asignacion="no_aplica"),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert {p["id"] for p in body["puntos"]} == {"p1"}


def test_list_puntos_orders_by_descending_effective_priority(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "low": _punto(prioridad="baja", prioridad_score=10),
        "high": _punto(prioridad="alta", prioridad_score=90),
        "mid": _punto(prioridad="media", prioridad_score=50),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})

    ids = [p["id"] for p in resp.json()["puntos"]]
    assert ids == ["high", "mid", "low"]


def test_list_puntos_prioridad_override_wins_ordering(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "overridden": _punto(prioridad="baja", prioridad_score=5, prioridad_override="alta"),
        "natural_high": _punto(prioridad="alta", prioridad_score=95),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})

    ids = [p["id"] for p in resp.json()["puntos"]]
    assert ids[0] in ("overridden", "natural_high")
    assert set(ids) == {"overridden", "natural_high"}
    # both are effectively 'alta' — the override moved 'overridden' out of 'baja'
    for p in resp.json()["puntos"]:
        if p["id"] == "overridden":
            assert p["prioridad_override"] == "alta"


def test_list_puntos_truncado_true_when_more_than_limit(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        f"p{i}": _punto(prioridad_score=100 - i) for i in range(5)
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos", "limit": 2})

    body = resp.json()
    assert len(body["puntos"]) == 2
    assert body["truncado"] is True


def test_list_puntos_limit_above_hard_max_is_clamped_not_failed(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": _punto()}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos", "limit": 999999})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ── resumen (task 3.5/3.6) ───────────────────────────────────────────────


def test_resumen_returns_counts_without_per_point_payload(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": _punto(),
        "p2": _punto(tiene_survey=True),
        "p3": _punto(estado_asignacion="no_aplica"),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "resumen"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "puntos" not in body
    assert body["resumen"]["total"] == 3
    assert body["resumen"]["levantados"] == 1
    assert body["resumen"]["pendientes"] == 1


def test_resumen_includes_por_match_via_tally(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {**_punto(tiene_survey=True), "match_via": "clave"},
        "p2": {**_punto(tiene_survey=True), "match_via": "cercania"},
        "p3": {**_punto(tiene_survey=True), "match_via": "clave"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "resumen"})

    assert resp.json()["resumen"]["por_match_via"] == {"clave": 2, "cercania": 1}


# ── listCuadrillas ───────────────────────────────────────────────────────


def test_list_cuadrillas_returns_every_cuadrilla(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"nombre": "Zona 1", "puntos": []}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listCuadrillas"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["cuadrillas"] == [{"id": "c1", "nombre": "Zona 1", "puntos": []}]


# ── autoAgrupar (router path — DEFAULT_MAX_SIZE=10, excludes surveyed/no_aplica) ─


def test_auto_agrupar_router_creates_cuadrillas_from_pending_points(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"estado_asignacion": "pendiente", "cuadrilla_id": None,
               "coords": {"lat": 3.40, "lon": -76.50}, "tiene_survey": False},
        "p2": {"estado_asignacion": "pendiente", "cuadrilla_id": None,
               "coords": {"lat": 3.4001, "lon": -76.5001}, "tiene_survey": False},
        "p3": {"estado_asignacion": "pendiente", "cuadrilla_id": None,
               "coords": {"lat": 3.40, "lon": -76.50}, "tiene_survey": True},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["cuadrillas"]) == 1
    grupo = body["cuadrillas"][0]
    assert set(grupo["puntos"]) == {"p1", "p2"}
    assert stores[PLANEACION_PUNTOS]["p1"]["cuadrilla_id"] == grupo["id"]
    assert stores[PLANEACION_PUNTOS]["p3"]["cuadrilla_id"] is None


def test_auto_agrupar_router_empty_when_no_pending_points(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "cuadrillas": []}


def test_auto_agrupar_router_never_touches_estado_asignacion(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"estado_asignacion": "pendiente", "cuadrilla_id": None,
               "coords": {"lat": 3.40, "lon": -76.50}, "tiene_survey": False},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar"})

    assert resp.status_code == 200
    grupo = resp.json()["cuadrillas"][0]
    assert grupo["inspector_uid"] is None
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "pendiente"


# ── crearCuadrilla ────────────────────────────────────────────────────────


def test_crear_cuadrilla_succeeds_and_sets_cuadrilla_id_on_points(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"cuadrilla_id": None, "tiene_survey": False, "estado_asignacion": "pendiente"},
        "p2": {"cuadrilla_id": None, "tiene_survey": False, "estado_asignacion": "pendiente"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearCuadrilla", "nombre": "Zona 1", "puntos": ["p1", "p2"]},
    )

    assert resp.status_code == 201
    body = resp.json()
    cuadrilla_id = body["id"]
    assert stores[PLANEACION_CUADRILLAS][cuadrilla_id]["puntos"] == ["p1", "p2"]
    assert stores[PLANEACION_PUNTOS]["p1"]["cuadrilla_id"] == cuadrilla_id


def test_crear_cuadrilla_rejects_already_surveyed_points_naming_them(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"cuadrilla_id": None, "tiene_survey": True, "estado_asignacion": "pendiente"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "crearCuadrilla", "puntos": ["p1"]})

    assert resp.status_code == 400
    assert stores[PLANEACION_CUADRILLAS] == {}


def test_crear_cuadrilla_rejects_points_already_in_another_cuadrilla(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"cuadrilla_id": "c-existing", "tiene_survey": False, "estado_asignacion": "pendiente"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "crearCuadrilla", "puntos": ["p1"]})

    assert resp.status_code == 400
    assert stores[PLANEACION_CUADRILLAS] == {}


# ── editarCuadrilla ───────────────────────────────────────────────────────


def test_editar_cuadrilla_adds_and_removes_points(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1"], "inspector_uid": None, "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {"p1": {"cuadrilla_id": "c1"}, "p2": {"cuadrilla_id": None}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarCuadrilla", "cuadrilla_id": "c1", "add": ["p2"], "remove": ["p1"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["puntos"]) == {"p2"}
    assert stores[PLANEACION_PUNTOS]["p2"]["cuadrilla_id"] == "c1"
    assert stores[PLANEACION_PUNTOS]["p1"]["cuadrilla_id"] is None


def test_editar_cuadrilla_nonexistent_fails_with_zero_point_writes(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"cuadrilla_id": None}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarCuadrilla", "cuadrilla_id": "missing", "add": ["p1"], "remove": []},
    )

    assert resp.status_code == 400
    assert stores[PLANEACION_PUNTOS]["p1"]["cuadrilla_id"] is None


# ── asignarInspector / desasignarInspector ──────────────────────────────


def test_asignar_inspector_propagates_to_every_member_point(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1", "p2"], "inspector_uid": None, "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {"p1": {}, "p2": {}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarInspector", "cuadrilla_id": "c1", "inspector_uid": "insp-1"},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_CUADRILLAS]["c1"]["inspector_uid"] == "insp-1"
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] == "insp-1"
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "asignado"
    assert stores[PLANEACION_PUNTOS]["p1"]["asignado_en"] is not None


def test_desasignar_inspector_keeps_cuadrilla_resets_points_to_pendiente(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1"], "inspector_uid": "insp-1", "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {"p1": {"inspector_uid": "insp-1", "estado_asignacion": "asignado"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "desasignarInspector", "cuadrilla_id": "c1"})

    assert resp.status_code == 200
    assert stores[PLANEACION_CUADRILLAS]["c1"]["inspector_uid"] is None
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] is None
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "pendiente"
    assert "c1" in stores[PLANEACION_CUADRILLAS]


# ── reasignarPunto ────────────────────────────────────────────────────────


def test_reasignar_punto_sets_reasignado_de_and_leaves_cuadrilla_alone(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"inspector_uid": "insp-old", "cuadrilla_id": "c1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "reasignarPunto", "punto_id": "p1", "nuevo_inspector_uid": "insp-new"},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] == "insp-new"
    assert stores[PLANEACION_PUNTOS]["p1"]["reasignado_de"] == "insp-old"
    assert stores[PLANEACION_PUNTOS]["p1"]["cuadrilla_id"] == "c1"


def test_reasignar_punto_unassigned_sets_reasignado_de_null(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"inspector_uid": None}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "reasignarPunto", "punto_id": "p1", "nuevo_inspector_uid": "insp-new"},
    )

    body = resp.json()
    assert body["reasignado_de"] is None


# ── eliminarCuadrilla / reiniciarAgrupacion ─────────────────────────────


def test_eliminar_cuadrilla_clears_membership_before_delete(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1", "p2"], "inspector_uid": "insp-1", "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"cuadrilla_id": "c1", "inspector_uid": "insp-1"},
        "p2": {"cuadrilla_id": "c1", "inspector_uid": "insp-1"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "eliminarCuadrilla", "cuadrilla_id": "c1"})

    assert resp.status_code == 200
    assert "c1" not in stores[PLANEACION_CUADRILLAS]
    assert stores[PLANEACION_PUNTOS]["p1"]["cuadrilla_id"] is None
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] is None


def test_reiniciar_agrupacion_releases_only_auto_cuadrillas(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {
        "c-auto": {"puntos": ["p1"], "inspector_uid": "insp-1", "origen": "auto"},
        "c-manual": {"puntos": ["p2"], "inspector_uid": "insp-2", "origen": "manual"},
    }
    stores[PLANEACION_PUNTOS] = {
        "p1": {"cuadrilla_id": "c-auto", "inspector_uid": "insp-1", "estado_asignacion": "asignado"},
        "p2": {"cuadrilla_id": "c-manual", "inspector_uid": "insp-2", "estado_asignacion": "asignado"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "reiniciarAgrupacion"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "eliminadas": 1, "puntosLiberados": 1}
    assert "c-auto" not in stores[PLANEACION_CUADRILLAS]
    assert "c-manual" in stores[PLANEACION_CUADRILLAS]
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "pendiente"
    assert stores[PLANEACION_PUNTOS]["p2"]["cuadrilla_id"] == "c-manual"


# ── editarAsignacion (task 3.9/3.10) ────────────────────────────────────


def test_editar_asignacion_partial_leaves_untouched_fields_alone(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"notas": "porteria cerrada", "prioridad_override": None}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarAsignacion", "punto_id": "p1", "prioridad_override": "alta"},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["prioridad_override"] == "alta"
    assert stores[PLANEACION_PUNTOS]["p1"]["notas"] == "porteria cerrada"


def test_editar_asignacion_explicit_null_clears_a_field(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"notas": "porteria cerrada"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarAsignacion", "punto_id": "p1", "notas": None},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["notas"] is None


def test_editar_asignacion_stamps_editado_por_and_editado_en(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarAsignacion", "punto_id": "p1", "notas": "x"},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["editado_por"] == UID_ADMIN
    assert stores[PLANEACION_PUNTOS]["p1"]["editado_en"] is not None


def test_editar_asignacion_can_correct_inspector_without_touching_cuadrilla(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"cuadrilla_id": "c1", "inspector_uid": "insp-a"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarAsignacion", "punto_id": "p1", "inspector_uid": "insp-b"},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] == "insp-b"
    assert stores[PLANEACION_PUNTOS]["p1"]["cuadrilla_id"] == "c1"


def test_editar_asignacion_ignores_direccion_and_coords_keys(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"direccion": "Calle original", "coords": {"lat": 1, "lon": 2}}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={
            "action": "editarAsignacion",
            "punto_id": "p1",
            "direccion": "Calle hackeada",
            "coords": {"lat": 99, "lon": 99},
            "notas": "solo esto debe escribirse",
        },
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["direccion"] == "Calle original"
    assert stores[PLANEACION_PUNTOS]["p1"]["coords"] == {"lat": 1, "lon": 2}


# ── marcarNoAplica ───────────────────────────────────────────────────────


def test_marcar_no_aplica_without_reason_is_400_and_unchanged(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"estado_asignacion": "pendiente"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "marcarNoAplica", "punto_id": "p1"})

    assert resp.status_code == 400
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "pendiente"


def test_marcar_no_aplica_with_reason_excludes_from_default_list(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": _punto()}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "marcarNoAplica", "punto_id": "p1", "motivo_exclusion": "demolido"},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "no_aplica"
    assert stores[PLANEACION_PUNTOS]["p1"]["motivo_exclusion"] == "demolido"

    list_resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})
    assert "p1" not in {p["id"] for p in list_resp.json()["puntos"]}


def test_marcar_no_aplica_revertir_restores_pendiente(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"estado_asignacion": "no_aplica", "motivo_exclusion": "demolido"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "marcarNoAplica", "punto_id": "p1", "revertir": True},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "pendiente"
    assert stores[PLANEACION_PUNTOS]["p1"]["motivo_exclusion"] is None


# ── reopen (constraint #2 — the admin counterpart to the pipeline's ONE
# binding auto-close exception; see planeacion_cruce.py's module docstring) ─


def test_reopen_moves_a_hecho_point_back_to_pendiente(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"estado_asignacion": "hecho", "tiene_survey": True, "match_via": "clave"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "reopen", "punto_id": "p1"})

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "pendiente"
    assert stores[PLANEACION_PUNTOS]["p1"]["editado_por"] == UID_ADMIN
    # pipeline-owned survey facts are untouched by reopen — only the
    # admin-owned estado_asignacion transitions
    assert stores[PLANEACION_PUNTOS]["p1"]["tiene_survey"] is True


def test_reopen_rejects_a_point_that_is_not_hecho(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"estado_asignacion": "pendiente"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "reopen", "punto_id": "p1"})

    assert resp.status_code == 400
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "pendiente"


# ── getEnlaceSurvey ───────────────────────────────────────────────────────


def test_get_enlace_survey_returns_key_and_links_for_a_real_point(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"clave_integracion": "PLN-14832-9C4A1F0B"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "getEnlaceSurvey", "punto_id": "p1"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["clave"] == "PLN-14832-9C4A1F0B"
    assert "field:codigoapp=PLN-14832-9C4A1F0B" in body["web"]
    assert body["app"] is not None


def test_get_enlace_survey_fails_loud_when_form_url_unset(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"clave_integracion": "PLN-14832-9C4A1F0B"}}
    app = _app(monkeypatch, stores)
    monkeypatch.setenv("SURVEY123_FORM_URL", "")
    from app.auth.deps import current_claims as _cc
    app.dependency_overrides[_cc] = lambda: FAKE_CLAIMS_ADMIN
    client = TestClient(app)

    resp = client.post("/planeacion-asignaciones", json={"action": "getEnlaceSurvey", "punto_id": "p1"})

    assert resp.status_code == 503
    assert "SURVEY123_FORM_URL" in resp.json()["detail"]


# incluirLevantados override (Phase 4 found the gap: the tiene_survey filter
# was unconditional, so the tab's "incluir levantados" toggle had no way to
# work) -------------------------------------------------------------------


def test_list_puntos_incluir_levantados_returns_surveyed_points_too(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "pendiente": _punto(),
        "levantado": _punto(tiene_survey=True),
        "excluido": _punto(estado_asignacion="no_aplica"),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "listPuntos", "incluirLevantados": True},
    )

    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()["puntos"]}
    assert ids == {"pendiente", "levantado"}, (
        "incluirLevantados must widen the set to surveyed points, while "
        "no_aplica stays excluded (it is an explicit operator exclusion, "
        "not a survey-state fact)"
    )


def test_list_puntos_omitting_the_flag_still_excludes_surveyed(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"a": _punto(), "b": _punto(tiene_survey=True)}
    client = _admin_client(monkeypatch, stores)

    for payload in ({"action": "listPuntos"},
                    {"action": "listPuntos", "incluirLevantados": False}):
        resp = client.post("/planeacion-asignaciones", json=payload)
        assert {p["id"] for p in resp.json()["puntos"]} == {"a"}, payload


# Firestore timestamp serialization ------------------------------------------
# Real Firestore returns DatetimeWithNanoseconds for timestamp fields; the
# in-memory fake used by every other test returns plain Python values, so a
# non-serializable type slips through the whole suite and only explodes on a
# live call. Found exactly that way (502 "Object of type
# DatetimeWithNanoseconds is not JSON serializable") against 14,804 real docs.


def test_list_puntos_serializes_datetime_fields(monkeypatch):
    from datetime import datetime, timezone

    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {**_punto(),
               "matched_at": datetime(2026, 8, 26, 9, 4, 37, tzinfo=timezone.utc)},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})

    assert resp.status_code == 200, resp.text
    punto = resp.json()["puntos"][0]
    assert isinstance(punto["matched_at"], str), (
        "a datetime must be serialized to a string, not handed to the JSON "
        "encoder raw -- real Firestore returns DatetimeWithNanoseconds here"
    )
    assert punto["matched_at"].startswith("2026-08-26T09:04:37")


def test_list_cuadrillas_serializes_datetime_fields(monkeypatch):
    from datetime import datetime, timezone

    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {
        "c1": {"puntos": [], "inspector_uid": None, "origen": "manual",
               "asignado_en": datetime(2026, 8, 26, 9, 0, 0, tzinfo=timezone.utc)},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listCuadrillas"})

    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["cuadrillas"][0]["asignado_en"], str)
