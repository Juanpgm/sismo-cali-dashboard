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

from datetime import datetime
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
STICKER_MATCHES = "sticker_matches"  # `grupos-inspectores` change: eliminarGrupo's cross-campaign orphan check
GRUPOS_INSPECTORES = "grupos_inspectores"  # `grupos-inspectores` change
VEHICULOS = "vehiculos"  # `grupos-inspectores` follow-up (2026-08-26): vehicles
CONDUCTORES = "conductores"  # feature H (2026-08-26): drivers
PLANEACION_AUDITORIA = "planeacion_auditoria"  # `planeacion-auditoria` change (2026-08-26)


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
        select_fields: list[str] | None = None,
    ) -> None:
        self._collection = collection
        self._store = store
        self._ids = docs if docs is not None else list(store.keys())
        self._order_field = order_field
        self._order_desc = order_desc
        self._limit_n = limit_n
        self._select_fields = select_fields

    def where(self, field: str, op: str, value: Any) -> "_FakeQuery":
        if op == "==":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) == value]
        elif op == "!=":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) != value]
        elif op == ">=":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) is not None
                       and self._store[i][field] >= value]
        elif op == "<":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) is not None
                       and self._store[i][field] < value]
        else:
            raise AssertionError(f"unsupported op {op!r} in fake Firestore")
        return _FakeQuery(
            self._collection, self._store, matched, self._order_field, self._order_desc,
            self._limit_n, self._select_fields,
        )

    def order_by(self, field: str, direction: str | None = None) -> "_FakeQuery":
        desc = direction == "DESCENDING"
        return _FakeQuery(self._collection, self._store, self._ids, field, desc, self._limit_n, self._select_fields)

    def limit(self, n: int) -> "_FakeQuery":
        return _FakeQuery(
            self._collection, self._store, self._ids, self._order_field, self._order_desc, n, self._select_fields
        )

    def select(self, field_paths: list[str]) -> "_FakeQuery":
        """Firestore projection: only these fields come back in `to_dict()`
        (`auto-agrupar-comuna-barrio` follow-up, item 2B — same precedent as
        `get_all(field_paths=...)` below)."""
        return _FakeQuery(
            self._collection, self._store, self._ids, self._order_field, self._order_desc,
            self._limit_n, list(field_paths),
        )

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

    def _projected(self, data: dict[str, Any] | None) -> dict[str, Any] | None:
        if data is None or self._select_fields is None:
            return data
        return {k: v for k, v in data.items() if k in self._select_fields}

    def get(self) -> list[_FakeSnapshot]:
        return self.stream()

    def stream(self) -> list[_FakeSnapshot]:
        snaps = []
        for doc_id in self._ordered_ids():
            snap = _FakeSnapshot(self._collection, doc_id, self._projected(self._store.get(doc_id)))
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
    return {PLANEACION_PUNTOS: {}, PLANEACION_CUADRILLAS: {}, PLANEACION_AUDITORIA: {}}


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
        "listGrupos",
        "crearGrupo",
        "editarGrupo",
        "eliminarGrupo",
        "asignarGrupoAPuntos",
        "desasignarGrupo",
        "listVehiculos",
        "crearVehiculo",
        "editarVehiculo",
        "eliminarVehiculo",
        "asignarVehiculoAGrupo",
        "desasignarVehiculo",
        "metricasProgreso",
        "listAuditoria",
    ],
)
def test_non_admin_is_rejected_no_mutation(monkeypatch, action):
    stores = _stores()
    stores[PLANEACION_PUNTOS]["p1"] = {"estado_asignacion": "pendiente", "cuadrilla_id": None}
    client = _viewer_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={
            "action": action,
            "cuadrilla_id": "c1",
            "punto_id": "p1",
            "puntos": ["p1"],
            "grupo_id": "g1",
            "nombre": "Grupo Norte",
            "miembros": ["uid-1"],
            "vehiculo_id": "v1",
            "placa": "ABC123",
        },
    )

    assert resp.status_code == 403
    assert stores[PLANEACION_PUNTOS]["p1"] == {"estado_asignacion": "pendiente", "cuadrilla_id": None}
    assert stores[PLANEACION_CUADRILLAS] == {}
    assert stores.get(GRUPOS_INSPECTORES, {}) == {}
    assert stores.get(VEHICULOS, {}) == {}


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


def _capture_fake_query_limits(monkeypatch) -> list[int]:
    """Instruments `_FakeQuery.limit()` for this test only (monkeypatch
    auto-restores) so `list_puntos`'s dynamic over-fetch (2026-08-27 speed
    follow-up) can be asserted on directly, instead of inferring it from doc
    counts."""
    captured: list[int] = []
    original_limit = _FakeQuery.limit

    def _tracking_limit(self, n):
        captured.append(n)
        return original_limit(self, n)

    monkeypatch.setattr(_FakeQuery, "limit", _tracking_limit)
    return captured


def test_list_puntos_default_call_does_not_overfetch_the_hard_max(monkeypatch):
    """2026-08-27 speed follow-up: the default call (no estado/prioridad/
    comuna/soloPendientes) has no in-code narrowing filter left besides the
    rare `no_aplica` exclusion, so it must NOT pay the full LIMIT_MAX+1
    (5001-doc) read every time."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": _punto()}
    client = _admin_client(monkeypatch, stores)
    captured = _capture_fake_query_limits(monkeypatch)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})

    assert resp.status_code == 200
    assert captured, "expected .limit() to be called"
    assert captured[-1] <= pa.LIMIT_DEFAULT * 2 + 1
    assert captured[-1] < pa.LIMIT_MAX + 1


def test_list_puntos_with_prioridad_filter_keeps_the_large_overfetch(monkeypatch):
    """A narrowing in-code filter (prioridad here) can drop an arbitrary
    fraction of the over-fetched page, so it must keep the original
    LIMIT_MAX+1 over-fetch for correctness."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": _punto(prioridad="alta")}
    client = _admin_client(monkeypatch, stores)
    captured = _capture_fake_query_limits(monkeypatch)

    resp = client.post(
        "/planeacion-asignaciones", json={"action": "listPuntos", "prioridad": "alta"}
    )

    assert resp.status_code == 200
    assert captured[-1] == pa.LIMIT_MAX + 1


def test_list_puntos_large_limit_caps_overfetch_at_the_hard_max(monkeypatch):
    """Item 5 (2026-08-27): the frontend now requests `limit: 4500` (top-4500
    critical working set). `effective_limit * 2 + 1` for 4500 would be 9001 --
    over LIMIT_MAX+1 (5001). The dynamic over-fetch must be CAPPED at
    LIMIT_MAX+1, never exceed it, regardless of how large `limit` is."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": _punto()}
    client = _admin_client(monkeypatch, stores)
    captured = _capture_fake_query_limits(monkeypatch)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos", "limit": 4500})

    assert resp.status_code == 200
    assert captured[-1] == pa.LIMIT_MAX + 1


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


def test_resumen_includes_barrios_por_comuna_sorted_distinct_pending_only(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {**_punto(comuna="COMUNA 19"), "barrio": "Tequendama"},
        "p2": {**_punto(comuna="COMUNA 19"), "barrio": "San Fernando"},
        "p3": {**_punto(comuna="COMUNA 19"), "barrio": "San Fernando"},  # duplicate barrio
        "p4": {**_punto(comuna="COMUNA 19"), "barrio": None},  # no barrio, excluded
        "p5": {**_punto(comuna="COMUNA 2"), "barrio": "Otro"},
        # not pending -> excluded from the tally even though it has a barrio
        "p6": {**_punto(comuna="COMUNA 19", tiene_survey=True), "barrio": "Ya Levantado"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "resumen"})

    assert resp.json()["resumen"]["barrios_por_comuna"] == {
        "COMUNA 19": ["San Fernando", "Tequendama"],
        "COMUNA 2": ["Otro"],
    }


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


def test_auto_agrupar_router_caps_working_set_to_top_n_by_score(monkeypatch):
    # `limite` bounds the fetch to the top-N pending/ungrouped by prioridad_score:
    # only the 2 highest-score points are clustered; the low-score one is left
    # ungrouped for a later run (fluidity fix, coverage preserved across runs).
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "hi1": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                "prioridad_score": 90, "coords": {"lat": 3.40, "lon": -76.50}},
        "hi2": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                "prioridad_score": 80, "coords": {"lat": 3.4001, "lon": -76.5001}},
        "lo": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
               "prioridad_score": 10, "coords": {"lat": 3.40, "lon": -76.50}},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar", "limite": 2})

    assert resp.status_code == 200
    assigned = {pid for c in resp.json()["cuadrillas"] for pid in c["puntos"]}
    assert assigned == {"hi1", "hi2"}
    assert stores[PLANEACION_PUNTOS]["lo"]["cuadrilla_id"] is None


def test_auto_agrupar_router_excludes_surveyed_at_the_query_not_just_in_code(monkeypatch):
    """Item 3a (2026-08-27) root cause: fuzzy-matched top-scored pendientes
    are often ALREADY surveyed (tiene_survey True, but the pipeline keeps
    them 'pendiente'). If the query only excludes them AFTER the fetch (the
    old in-code-only filter), a small `limite` batch can come back 100%
    already-surveyed -> 0 groups, even though real pending points exist
    further down. The `tiene_survey == False` filter belongs at the QUERY
    so a small `limite` still finds real candidates."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "surveyed_hi": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": True,
                        "prioridad_score": 99, "coords": {"lat": 3.40, "lon": -76.50}},
        "surveyed_mid": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": True,
                         "prioridad_score": 90, "coords": {"lat": 3.40, "lon": -76.50}},
        "real_pendiente": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                           "prioridad_score": 10, "coords": {"lat": 3.41, "lon": -76.51}},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar", "limite": 2})

    assert resp.status_code == 200
    assigned = {pid for c in resp.json()["cuadrillas"] for pid in c["puntos"]}
    assert assigned == {"real_pendiente"}


def test_auto_agrupar_router_orders_cuadrillas_by_density_desc(monkeypatch):
    # Densest cluster first — teams get the fullest route. A tight trio + a lone
    # far-away point must come back as [3-point cuadrilla, 1-point cuadrilla].
    stores = _stores()
    dense = {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
             "prioridad_score": 50}
    stores[PLANEACION_PUNTOS] = {
        "d1": {**dense, "coords": {"lat": 3.4000, "lon": -76.5000}},
        "d2": {**dense, "coords": {"lat": 3.4001, "lon": -76.5001}},
        "d3": {**dense, "coords": {"lat": 3.4002, "lon": -76.5002}},
        "solo": {**dense, "coords": {"lat": 3.4600, "lon": -76.5600}},  # >800m away
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar"})

    cuadrillas = resp.json()["cuadrillas"]
    assert [len(c["puntos"]) for c in cuadrillas] == [3, 1]


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


# ── autoAgrupar scoped by comuna/barrio ─────────────────────────────────


def _scopeable(comuna, barrio, lat, lon, score=50):
    return {
        "estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
        "prioridad_score": score, "coords": {"lat": lat, "lon": lon},
        "comuna": comuna, "barrio": barrio,
    }


def test_auto_agrupar_router_with_comuna_only_clusters_that_comuna(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "c19a": _scopeable("COMUNA 19", "San Fernando", 3.40, -76.50),
        "c19b": _scopeable("COMUNA 19", "Tequendama", 3.4001, -76.5001),
        "c2": _scopeable("COMUNA 2", "Otro Barrio", 3.40, -76.50),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar", "comuna": "COMUNA 19"})

    assert resp.status_code == 200
    assigned = {pid for c in resp.json()["cuadrillas"] for pid in c["puntos"]}
    assert assigned == {"c19a", "c19b"}
    assert stores[PLANEACION_PUNTOS]["c2"]["cuadrilla_id"] is None


def test_auto_agrupar_router_with_comuna_and_barrio_clusters_only_that_barrio(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "sf1": _scopeable("COMUNA 19", "San Fernando", 3.40, -76.50),
        "sf2": _scopeable("COMUNA 19", "San Fernando", 3.4001, -76.5001),
        "tq1": _scopeable("COMUNA 19", "Tequendama", 3.40, -76.50),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "autoAgrupar", "comuna": "COMUNA 19", "barrio": "San Fernando"},
    )

    assert resp.status_code == 200
    assigned = {pid for c in resp.json()["cuadrillas"] for pid in c["puntos"]}
    assert assigned == {"sf1", "sf2"}
    assert stores[PLANEACION_PUNTOS]["tq1"]["cuadrilla_id"] is None


def test_auto_agrupar_router_without_comuna_or_barrio_is_unscoped(monkeypatch):
    """Empty/absent comuna/barrio params = current (unscoped) behavior."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "c19": _scopeable("COMUNA 19", "San Fernando", 3.40, -76.50),
        "c2": _scopeable("COMUNA 2", "Otro Barrio", 3.4600, -76.5600),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar", "comuna": "  "})

    assert resp.status_code == 200
    assigned = {pid for c in resp.json()["cuadrillas"] for pid in c["puntos"]}
    assert assigned == {"c19", "c2"}


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


def test_crear_cuadrilla_rejects_a_hecho_point_without_a_survey(monkeypatch):
    # Bug found during review: a point manually marked 'hecho' (e.g. an
    # inspector completed it in the field without a survey ever arriving)
    # has tiene_survey=False, so the `surveyed` guard alone never catches
    # it. Every OTHER assignment path (editarCuadrilla add,
    # asignarGrupoAPuntos, asignarInspector, reasignarPunto) already rejects
    # this via points_locked — crearCuadrilla was the one path that let it
    # slip into a brand-new cuadrilla. This proves the fix closes that gap.
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"cuadrilla_id": None, "tiene_survey": False, "estado_asignacion": "hecho"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "crearCuadrilla", "puntos": ["p1"]})

    assert resp.status_code == 400
    assert stores[PLANEACION_CUADRILLAS] == {}
    assert stores[PLANEACION_PUNTOS]["p1"].get("cuadrilla_id") is None


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


# ── feature F: levantado/hecho points are NOT re-assignable ───────────────


def test_reasignar_punto_rejects_a_levantado_point(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"inspector_uid": "insp-old", "tiene_survey": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "reasignarPunto", "punto_id": "p1", "nuevo_inspector_uid": "insp-new"},
    )

    assert resp.status_code == 400
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] == "insp-old"


def test_reasignar_punto_rejects_a_hecho_point(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"inspector_uid": "insp-old", "estado_asignacion": "hecho"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "reasignarPunto", "punto_id": "p1", "nuevo_inspector_uid": "insp-new"},
    )

    assert resp.status_code == 400
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] == "insp-old"


def test_editar_cuadrilla_add_rejects_levantado_points(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": [], "inspector_uid": None, "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {"p1": {"cuadrilla_id": None, "tiene_survey": True, "estado_asignacion": "pendiente"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarCuadrilla", "cuadrilla_id": "c1", "add": ["p1"]},
    )

    assert resp.status_code == 400
    assert stores[PLANEACION_PUNTOS]["p1"].get("cuadrilla_id") is None
    assert "p1" not in stores[PLANEACION_CUADRILLAS]["c1"]["puntos"]


def test_editar_cuadrilla_add_rejects_hecho_points_without_a_survey(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": [], "inspector_uid": None, "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {"p1": {"cuadrilla_id": None, "tiene_survey": False, "estado_asignacion": "hecho"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarCuadrilla", "cuadrilla_id": "c1", "add": ["p1"]},
    )

    assert resp.status_code == 400
    assert stores[PLANEACION_PUNTOS]["p1"].get("cuadrilla_id") is None
    assert "p1" not in stores[PLANEACION_CUADRILLAS]["c1"]["puntos"]


def test_asignar_grupo_rejects_levantado_points(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "G1", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"estado_asignacion": "pendiente", "tiene_survey": False},
        "p2": {"estado_asignacion": "pendiente", "tiene_survey": True},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1", "p2"]},
    )

    assert resp.status_code == 400
    # whole op rejected — neither point got grupo_id
    assert "grupo_id" not in stores[PLANEACION_PUNTOS]["p1"]
    assert "grupo_id" not in stores[PLANEACION_PUNTOS]["p2"]


def test_asignar_grupo_rejects_hecho_points_without_a_survey(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "G1", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"estado_asignacion": "pendiente", "tiene_survey": False},
        "p2": {"estado_asignacion": "hecho", "tiene_survey": False},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1", "p2"]},
    )

    assert resp.status_code == 400
    assert "grupo_id" not in stores[PLANEACION_PUNTOS]["p1"]
    assert "grupo_id" not in stores[PLANEACION_PUNTOS]["p2"]


def test_asignar_inspector_skips_levantado_points_and_assigns_the_rest(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1", "p2"], "inspector_uid": None, "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"estado_asignacion": "pendiente", "tiene_survey": False},
        "p2": {"estado_asignacion": "pendiente", "tiene_survey": True},  # levantado
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarInspector", "cuadrilla_id": "c1", "inspector_uid": "insp-1"},
    )

    assert resp.status_code == 200
    # non-locked p1 assigned
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] == "insp-1"
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "asignado"
    # levantado p2 skipped, untouched
    assert stores[PLANEACION_PUNTOS]["p2"].get("inspector_uid") is None
    assert stores[PLANEACION_PUNTOS]["p2"]["estado_asignacion"] == "pendiente"
    # cuadrilla still records the inspector
    assert stores[PLANEACION_CUADRILLAS]["c1"]["inspector_uid"] == "insp-1"


def test_asignar_inspector_skips_hecho_points_without_a_survey(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1", "p2"], "inspector_uid": None, "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"estado_asignacion": "pendiente", "tiene_survey": False},
        "p2": {"estado_asignacion": "hecho", "tiene_survey": False},  # completed manually, no survey
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarInspector", "cuadrilla_id": "c1", "inspector_uid": "insp-1"},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] == "insp-1"
    assert stores[PLANEACION_PUNTOS]["p2"].get("inspector_uid") is None
    assert stores[PLANEACION_PUNTOS]["p2"]["estado_asignacion"] == "hecho"  # untouched


# ── points_locked: pure-function guarantee, generalizes to ANY point ────────
# Every assignment write path (crearCuadrilla, editarCuadrilla add,
# asignarGrupoAPuntos, asignarInspector, reasignarPunto) routes its
# "already done, don't touch" decision through this ONE function. Proving it
# correct here — independent of any route/fixture — is what makes the
# guarantee hold for any point, not just the ones exercised above.


def test_points_locked_true_for_surveyed_or_hecho_false_otherwise():
    puntos = [
        {"id": "surveyed", "tiene_survey": True, "estado_asignacion": "pendiente"},
        {"id": "hecho_sin_survey", "tiene_survey": False, "estado_asignacion": "hecho"},
        {"id": "surveyed_and_hecho", "tiene_survey": True, "estado_asignacion": "hecho"},
        {"id": "pendiente", "tiene_survey": False, "estado_asignacion": "pendiente"},
        {"id": "asignado", "tiene_survey": False, "estado_asignacion": "asignado"},
        {"id": "en_proceso", "tiene_survey": False, "estado_asignacion": "en_proceso"},
        {"id": "no_aplica", "tiene_survey": False, "estado_asignacion": "no_aplica"},  # excluded elsewhere, not "locked"
        {"id": "sin_estado", "tiene_survey": False, "estado_asignacion": None},
    ]

    locked = set(pa.points_locked(puntos))

    assert locked == {"surveyed", "hecho_sin_survey", "surveyed_and_hecho"}


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
    """Item 2B (2026-08-27) follow-up: `listPuntos` now projects to
    `pa.PUNTOS_LIST_FIELDS` (`.select()`), and `matched_at` is not in that
    set — it never reaches `to_dict()` at all now, real Firestore included,
    so the field is simply absent rather than raising. The `_jsonable`
    funnel itself stays covered generically by
    `test_list_cuadrillas_serializes_datetime_fields` below, whose action
    (`listCuadrillas`) reads full, unprojected docs."""
    from datetime import datetime, timezone

    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {**_punto(),
               "matched_at": datetime(2026, 8, 26, 9, 4, 37, tzinfo=timezone.utc)},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})

    assert resp.status_code == 200, resp.text
    assert "matched_at" not in resp.json()["puntos"][0]


def test_list_puntos_response_contains_only_projected_fields(monkeypatch):
    """Item 2B (2026-08-27): `listPuntos` must not ship the full document —
    only `pa.PUNTOS_LIST_FIELDS` plus `id`, matching what
    `web/js/planeacion.js`'s `buildRows`/table/popup/modals actually read
    off a punto."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {**_punto(), "direccion": "Cra 1 # 2-3",
               "heavy_leftover_field": "should not be shipped"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})

    punto = resp.json()["puntos"][0]
    assert set(punto.keys()) <= {"id", *pa.PUNTOS_LIST_FIELDS}
    assert "heavy_leftover_field" not in punto


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


# ── `grupos-inspectores` change (2026-08-26): admin group-of-INSPECTORS CRUD
# — NOT to be confused with `planeacion_cuadrillas` (groups of POINTS under
# ONE inspector, tested above). `grupos_inspectores` is campaign-agnostic
# (shared by BOTH stickers and survey); CRUD is exclusively owned here. ────


def test_crear_grupo_succeeds_and_defaults_activo_true(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearGrupo", "nombre": "Grupo Norte", "miembros": ["uid-1", "uid-2"]},
    )

    assert resp.status_code == 201
    body = resp.json()
    grupo_id = body["id"]
    doc = stores[GRUPOS_INSPECTORES][grupo_id]
    assert doc["nombre"] == "Grupo Norte"
    assert doc["miembros"] == ["uid-1", "uid-2"]
    assert doc["activo"] is True
    assert doc["creado_por"] == UID_ADMIN
    assert "creado_en" in doc


def test_crear_grupo_requires_nombre(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearGrupo", "nombre": "", "miembros": ["uid-1"]},
    )

    assert resp.status_code == 400
    assert stores.get(GRUPOS_INSPECTORES, {}) == {}


def test_crear_grupo_requires_at_least_one_miembro(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearGrupo", "nombre": "Grupo Norte", "miembros": []},
    )

    assert resp.status_code == 400
    assert stores.get(GRUPOS_INSPECTORES, {}) == {}


# ── member cap (2026-08-26 follow-up): a group has AT MOST
# MAX_MIEMBROS_GRUPO (4) members, enforced server-side. ─────────────────────


def test_crear_grupo_rejects_more_than_max_miembros(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearGrupo", "nombre": "Grupo Grande", "miembros": ["u1", "u2", "u3", "u4", "u5"]},
    )

    assert resp.status_code == 400
    assert "4" in resp.json()["detail"]
    assert stores.get(GRUPOS_INSPECTORES, {}) == {}


def test_crear_grupo_allows_exactly_max_miembros(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearGrupo", "nombre": "Grupo Completo", "miembros": ["u1", "u2", "u3", "u4"]},
    )

    assert resp.status_code == 201


def test_crear_grupo_dedupes_miembros_before_counting_cap(monkeypatch):
    """A duplicate uid in the raw request must not count twice toward the
    cap — 6 raw entries, only 4 DISTINCT uids, must succeed."""
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearGrupo", "nombre": "Grupo Dedup", "miembros": ["u1", "u1", "u2", "u3", "u4", "u4"]},
    )

    assert resp.status_code == 201
    grupo_id = resp.json()["id"]
    assert sorted(stores[GRUPOS_INSPECTORES][grupo_id]["miembros"]) == ["u1", "u2", "u3", "u4"]


def test_list_grupos_returns_every_grupo(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {
        "g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True},
        "g2": {"nombre": "Sur", "miembros": ["u2"], "activo": False},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listGrupos"})

    assert resp.status_code == 200
    ids = {g["id"] for g in resp.json()["grupos"]}
    assert ids == {"g1", "g2"}


def test_list_grupos_vehiculo_is_null_when_unassigned(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listGrupos"})

    grupo = resp.json()["grupos"][0]
    assert grupo["vehiculo"] is None


def test_list_grupos_includes_assigned_vehiculo_without_second_round_trip(monkeypatch):
    """The group's resolved vehicle (placa/tipo) comes back embedded in
    listGrupos itself — the admin UI must not need a second round trip."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True, "vehiculo_id": "v1"}}
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listGrupos"})

    grupo = resp.json()["grupos"][0]
    assert grupo["vehiculo"]["id"] == "v1"
    assert grupo["vehiculo"]["placa"] == "ABC123"
    assert grupo["vehiculo"]["tipo"] == "camioneta"


def test_editar_grupo_adds_removes_members_and_renames(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Viejo", "miembros": ["u1"], "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarGrupo", "grupo_id": "g1", "nombre": "Nuevo", "add": ["u2"], "remove": ["u1"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["miembros"]) == {"u2"}
    assert stores[GRUPOS_INSPECTORES]["g1"]["nombre"] == "Nuevo"
    assert set(stores[GRUPOS_INSPECTORES]["g1"]["miembros"]) == {"u2"}


def test_editar_grupo_rejects_add_that_would_exceed_cap_leaves_unchanged(monkeypatch):
    """The check runs against the RESULTING membership (current - remove +
    add), not just len(add). Rejecting must leave the group completely
    unchanged — no partial add."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1", "u2", "u3"], "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarGrupo", "grupo_id": "g1", "add": ["u4", "u5"], "remove": []},
    )

    assert resp.status_code == 400
    assert "4" in resp.json()["detail"]
    assert sorted(stores[GRUPOS_INSPECTORES]["g1"]["miembros"]) == ["u1", "u2", "u3"]
    assert stores[GRUPOS_INSPECTORES]["g1"]["nombre"] == "Norte"


def test_editar_grupo_add_already_member_does_not_double_count_toward_cap(monkeypatch):
    """A group already AT the cap (4) re-adding one of its own current
    members must succeed — de-duplication before measuring."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1", "u2", "u3", "u4"], "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarGrupo", "grupo_id": "g1", "add": ["u1"], "remove": []},
    )

    assert resp.status_code == 200
    assert sorted(stores[GRUPOS_INSPECTORES]["g1"]["miembros"]) == ["u1", "u2", "u3", "u4"]


def test_editar_grupo_allows_add_when_remove_offsets_to_stay_within_cap(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1", "u2", "u3", "u4"], "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarGrupo", "grupo_id": "g1", "add": ["u5"], "remove": ["u1"]},
    )

    assert resp.status_code == 200
    assert sorted(resp.json()["miembros"]) == ["u2", "u3", "u4", "u5"]


def test_editar_grupo_nonexistent_fails(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarGrupo", "grupo_id": "missing", "add": ["u1"], "remove": []},
    )

    assert resp.status_code == 400


def test_eliminar_grupo_succeeds_when_no_points_assigned(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "eliminarGrupo", "grupo_id": "g1"})

    assert resp.status_code == 200
    assert "g1" not in stores[GRUPOS_INSPECTORES]


def test_eliminar_grupo_refuses_when_planeacion_points_still_assigned(monkeypatch):
    """Orphan-prevention decision: REFUSE deletion while points still
    reference the group, naming the count — never a silent grupo_id clear.
    See planeacion_asignaciones.py's own module docstring for why."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {"p1": {"grupo_id": "g1"}, "p2": {"grupo_id": "g1"}, "p3": {"grupo_id": None}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "eliminarGrupo", "grupo_id": "g1"})

    assert resp.status_code == 400
    assert "2" in resp.json()["detail"]
    assert "g1" in stores[GRUPOS_INSPECTORES]


def test_eliminar_grupo_refuses_when_sticker_points_still_assigned(monkeypatch):
    """Cross-campaign: the SAME shared group can also be orphaning
    `sticker_matches` points — the check spans both collections."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[STICKER_MATCHES] = {"s1": {"grupo_id": "g1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "eliminarGrupo", "grupo_id": "g1"})

    assert resp.status_code == 400
    assert "1" in resp.json()["detail"]
    assert "g1" in stores[GRUPOS_INSPECTORES]


def test_eliminar_grupo_nonexistent_fails(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "eliminarGrupo", "grupo_id": "missing"})

    assert resp.status_code == 400


def test_asignar_grupo_a_puntos_sets_grupo_id_default_planeacion(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {"p1": {}, "p2": {}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1", "p2"]},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["grupo_id"] == "g1"
    assert stores[PLANEACION_PUNTOS]["p2"]["grupo_id"] == "g1"
    # Individual assignment (inspector_uid) untouched — coexistence, not replacement.
    assert "inspector_uid" not in stores[PLANEACION_PUNTOS]["p1"]


def test_asignar_grupo_a_puntos_rejects_nonexistent_grupo(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "missing", "puntos": ["p1"]},
    )

    assert resp.status_code == 400
    assert "grupo_id" not in stores[PLANEACION_PUNTOS]["p1"]


def test_desasignar_grupo_clears_field(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"grupo_id": "g1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "desasignarGrupo", "puntos": ["p1"]})

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["grupo_id"] is None


def test_asignar_grupo_a_puntos_coleccion_and_desasignar_are_planeacion_only(monkeypatch):
    """This router's own asignarGrupoAPuntos/desasignarGrupo touch ONLY
    `planeacion_puntos` — never `sticker_matches`. The sticker-campaign
    counterpart of these two actions lives in `sticker_asignaciones.py`
    (own collection, own router), keeping the existing per-campaign
    collection ownership discipline intact instead of granting this
    router write access to a collection it does not own."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[STICKER_MATCHES] = {"s1": {}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["s1"]},
    )

    # s1 only exists in sticker_matches, not planeacion_puntos -- this
    # router must refuse it (400, unknown point) rather than silently
    # writing into a collection it does not own.
    assert resp.status_code == 400
    assert stores[STICKER_MATCHES]["s1"] == {}


# ── Item 6 (2026-08-26, reversed 2026-08-27): grupo assignment propagates
# to the matching `sticker_matches` TWIN, best-effort. See the module
# docstring's dated reversal note of its own earlier "never reach into
# sticker_matches" decision. ────────────────────────────────────────────


def test_asignar_grupo_propagates_to_free_sticker_twin_and_persists_linkage(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "clave_integracion": "PLN-1-ABC"},
    }
    stores[STICKER_MATCHES] = {"s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 1
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] == "g1"
    assert stores[STICKER_MATCHES]["s1"]["clave_integracion"] == "PLN-1-ABC"
    assert stores[STICKER_MATCHES]["s1"]["planeacion_punto_id"] == "p1"


def test_asignar_grupo_does_not_steal_a_twin_already_in_another_grupo(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {"p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"}}
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "grupo_id": "g-otro"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 0
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] == "g-otro"


def test_asignar_grupo_skips_a_completed_twin(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {"p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"}}
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "estado_asignacion": "hecho"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 0
    assert "grupo_id" not in stores[STICKER_MATCHES]["s1"]


def test_asignar_grupo_no_twin_in_range_is_a_no_op_not_an_error(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {"p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"}}
    stores[STICKER_MATCHES] = {"s1": {"coords": {"lat": 3.60, "lon": -76.70}, "direccion": "Otra calle lejana"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 0
    assert stores[PLANEACION_PUNTOS]["p1"]["grupo_id"] == "g1"


def test_asignar_grupo_survey_assignment_succeeds_even_if_sticker_propagation_raises(monkeypatch):
    """FAIL-SOFT: a sticker-side failure must NEVER fail the survey
    assignment that already committed."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {"p1": {"coords": {"lat": 3.40, "lon": -76.50}}}
    stores[STICKER_MATCHES] = {"s1": {"coords": {"lat": 3.40, "lon": -76.50}}}
    client = _admin_client(monkeypatch, stores)

    def _boom(*args, **kwargs):
        raise RuntimeError("sticker store unavailable")

    monkeypatch.setattr(pa, "_doc_to_dict", _boom)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["grupo_id"] == "g1"
    assert resp.json()["stickers_asignados"] == 0


def test_asignar_grupo_does_not_overwrite_a_twin_linked_to_a_different_clave(monkeypatch):
    """First-link-wins: a twin already carrying a DIFFERENT clave_integracion
    is a different planeacion point's pairing -- never overwritten."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "clave_integracion": "PLN-NEW"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "clave_integracion": "PLN-OLD"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 0
    assert stores[STICKER_MATCHES]["s1"].get("grupo_id") is None
    assert stores[STICKER_MATCHES]["s1"]["clave_integracion"] == "PLN-OLD"


def test_desasignar_grupo_clears_only_grupo_id_keeps_linkage_on_twin(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "grupo_id": "g1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1",
               "grupo_id": "g1", "clave_integracion": "PLN-1-ABC", "planeacion_punto_id": "p1"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "desasignarGrupo", "puntos": ["p1"]})

    assert resp.status_code == 200
    assert resp.json()["stickers_desasignados"] == 1
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] is None
    # linkage keys STAY -- the physical pairing remains true regardless of assignment
    assert stores[STICKER_MATCHES]["s1"]["clave_integracion"] == "PLN-1-ABC"
    assert stores[STICKER_MATCHES]["s1"]["planeacion_punto_id"] == "p1"


def test_desasignar_grupo_does_not_clear_a_twin_from_a_different_grupo(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "grupo_id": "g1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "grupo_id": "g-otro"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "desasignarGrupo", "puntos": ["p1"]})

    assert resp.status_code == 200
    assert resp.json()["stickers_desasignados"] == 0
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] == "g-otro"


# ── `grupos-inspectores` follow-up (2026-08-26): vehículos — "cada grupo
# sale en un vehículo". CRUD lives here (same single-owner reasoning as
# `grupos_inspectores` itself); the load-bearing invariant is "one vehicle
# -> at most one group at a time". ──────────────────────────────────────────


def test_crear_vehiculo_succeeds(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearVehiculo", "placa": "abc123", "dia_pico_placa": "lunes"},
    )

    assert resp.status_code == 201
    vehiculo_id = resp.json()["id"]
    doc = stores[VEHICULOS][vehiculo_id]
    assert doc["placa"] == "ABC123"  # normalized uppercase
    assert "tipo" not in doc  # tipo removed from the model entirely (2026-08-26)
    assert doc["dia_pico_placa"] == "lunes"
    assert doc["activo"] is True
    assert doc["creado_por"] == UID_ADMIN
    assert "creado_en" in doc


def test_crear_vehiculo_rejects_invalid_dia_pico_placa(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearVehiculo", "placa": "abc123", "dia_pico_placa": "sabado"},
    )

    assert resp.status_code == 400
    assert stores.get(VEHICULOS, {}) == {}


def test_crear_vehiculo_requires_placa(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "crearVehiculo", "placa": ""})

    assert resp.status_code == 400
    assert stores.get(VEHICULOS, {}) == {}


def test_crear_vehiculo_rejects_duplicate_placa(monkeypatch):
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "crearVehiculo", "placa": "abc123"})

    assert resp.status_code == 400
    assert "ABC123" in resp.json()["detail"]
    assert len(stores[VEHICULOS]) == 1


def test_list_vehiculos_returns_every_vehiculo(monkeypatch):
    stores = _stores()
    stores[VEHICULOS] = {
        "v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True},
        "v2": {"placa": "XYZ789", "tipo": "moto", "activo": True},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listVehiculos"})

    assert resp.status_code == 200
    ids = {v["id"] for v in resp.json()["vehiculos"]}
    assert ids == {"v1", "v2"}


def test_editar_vehiculo_updates_fields(monkeypatch):
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarVehiculo", "vehiculo_id": "v1", "dia_pico_placa": "martes", "activo": False},
    )

    assert resp.status_code == 200
    assert stores[VEHICULOS]["v1"]["dia_pico_placa"] == "martes"
    assert stores[VEHICULOS]["v1"]["activo"] is False
    assert stores[VEHICULOS]["v1"]["placa"] == "ABC123"  # untouched


# ── Pico y placa: blocks conductor-assignment and grupo-assignment on the
# vehicle's restricted weekday (binding user decision 2026-08-26) ───────────


def _hoy_bogota_es(dia: str) -> bool:
    """True iff `dia` (e.g. 'lunes') is Bogota-local today — used to build a
    deterministic test around whatever day the suite actually runs on."""
    from app.integracion.config import BOGOTA_TZ
    from app.routers.planeacion_asignaciones import _WEEKDAY_A_DIA
    return _WEEKDAY_A_DIA.get(datetime.now(BOGOTA_TZ).weekday()) == dia


def _dia_pico_placa_de_hoy() -> str:
    from app.integracion.config import BOGOTA_TZ
    from app.routers.planeacion_asignaciones import _WEEKDAY_A_DIA
    return _WEEKDAY_A_DIA[datetime.now(BOGOTA_TZ).weekday()]


def test_crear_vehiculo_rejects_conductor_when_today_is_pico_placa(monkeypatch):
    stores = _stores()
    stores[CONDUCTORES] = {"c1": {"cedula": "123", "nombre_completo": "Pedro"}}
    client = _admin_client(monkeypatch, stores)
    hoy = _dia_pico_placa_de_hoy()

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearVehiculo", "placa": "abc123", "dia_pico_placa": hoy, "conductor_id": "c1"},
    )

    assert resp.status_code == 400
    assert stores.get(VEHICULOS, {}) == {}


def test_crear_vehiculo_allows_conductor_on_a_non_restricted_day(monkeypatch):
    stores = _stores()
    stores[CONDUCTORES] = {"c1": {"cedula": "123", "nombre_completo": "Pedro"}}
    client = _admin_client(monkeypatch, stores)
    hoy = _dia_pico_placa_de_hoy()
    otro_dia = next(d for d in ["lunes", "martes", "miercoles", "jueves", "viernes"] if d != hoy)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearVehiculo", "placa": "abc123", "dia_pico_placa": otro_dia, "conductor_id": "c1"},
    )

    assert resp.status_code == 201
    vehiculo_id = resp.json()["id"]
    assert stores[VEHICULOS][vehiculo_id]["conductor_id"] == "c1"


def test_editar_vehiculo_rejects_conductor_change_when_todays_own_pico_placa(monkeypatch):
    hoy = _dia_pico_placa_de_hoy()
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "activo": True, "dia_pico_placa": hoy, "conductor_id": None}}
    stores[CONDUCTORES] = {"c1": {"cedula": "123", "nombre_completo": "Pedro"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarVehiculo", "vehiculo_id": "v1", "conductor_id": "c1"},
    )

    assert resp.status_code == 400
    assert stores[VEHICULOS]["v1"]["conductor_id"] is None


def test_editar_vehiculo_rejects_conductor_when_setting_pico_placa_in_same_call(monkeypatch):
    hoy = _dia_pico_placa_de_hoy()
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "activo": True, "conductor_id": None}}
    stores[CONDUCTORES] = {"c1": {"cedula": "123", "nombre_completo": "Pedro"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarVehiculo", "vehiculo_id": "v1", "dia_pico_placa": hoy, "conductor_id": "c1"},
    )

    assert resp.status_code == 400
    assert stores[VEHICULOS]["v1"].get("dia_pico_placa") is None
    assert stores[VEHICULOS]["v1"]["conductor_id"] is None


def test_editar_vehiculo_resending_same_conductor_on_pico_placa_day_is_allowed(monkeypatch):
    # Hotfix A2 (2026-08-26): the frontend re-sends the CURRENT conductor_id on
    # EVERY save (buildVehiculoPayload always includes it), so an unrelated
    # edit (empresa/activo) of a vehicle restricted TODAY used to 400 all day.
    # An unchanged driver must never trip the gate.
    hoy = _dia_pico_placa_de_hoy()
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "activo": True, "dia_pico_placa": hoy, "conductor_id": "c1"}}
    stores[CONDUCTORES] = {"c1": {"cedula": "123", "nombre_completo": "Pedro"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarVehiculo", "vehiculo_id": "v1",
              "empresa": "Acme", "conductor_id": "c1", "dia_pico_placa": hoy},
    )

    assert resp.status_code == 200
    assert stores[VEHICULOS]["v1"]["empresa"] == "Acme"
    assert stores[VEHICULOS]["v1"]["conductor_id"] == "c1"  # unchanged, not blocked


def test_editar_vehiculo_clearing_conductor_on_pico_placa_day_is_allowed(monkeypatch):
    # Removing the driver ("" -> None) is never "putting into service" — allowed
    # even on the restricted day.
    hoy = _dia_pico_placa_de_hoy()
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "activo": True, "dia_pico_placa": hoy, "conductor_id": "c1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarVehiculo", "vehiculo_id": "v1", "conductor_id": ""},
    )

    assert resp.status_code == 200
    assert stores[VEHICULOS]["v1"]["conductor_id"] is None


def test_editar_vehiculo_allows_unrelated_field_when_conductor_untouched_on_pico_placa_day(monkeypatch):
    # Only a conductor CHANGE is blocked — editing another field (e.g. activo)
    # on a vehicle that already has a conductor + is pico-y-placa today must
    # still succeed (the gate only fires when conductor_id is IN the body).
    hoy = _dia_pico_placa_de_hoy()
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "activo": True, "dia_pico_placa": hoy, "conductor_id": "c1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarVehiculo", "vehiculo_id": "v1", "activo": False},
    )

    assert resp.status_code == 200
    assert stores[VEHICULOS]["v1"]["activo"] is False
    assert stores[VEHICULOS]["v1"]["conductor_id"] == "c1"  # untouched


def test_asignar_vehiculo_a_grupo_rejects_on_pico_placa_day(monkeypatch):
    hoy = _dia_pico_placa_de_hoy()
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "G1", "miembros": ["u1"], "activo": True}}
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "activo": True, "dia_pico_placa": hoy}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarVehiculoAGrupo", "grupo_id": "g1", "vehiculo_id": "v1"},
    )

    assert resp.status_code == 400
    assert stores[GRUPOS_INSPECTORES]["g1"].get("vehiculo_id") is None


def test_asignar_vehiculo_a_grupo_succeeds_on_a_non_restricted_day(monkeypatch):
    hoy = _dia_pico_placa_de_hoy()
    otro_dia = next(d for d in ["lunes", "martes", "miercoles", "jueves", "viernes"] if d != hoy)
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "G1", "miembros": ["u1"], "activo": True}}
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "activo": True, "dia_pico_placa": otro_dia}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarVehiculoAGrupo", "grupo_id": "g1", "vehiculo_id": "v1"},
    )

    assert resp.status_code == 200
    assert stores[GRUPOS_INSPECTORES]["g1"]["vehiculo_id"] == "v1"


def test_editar_vehiculo_rejects_duplicate_placa(monkeypatch):
    stores = _stores()
    stores[VEHICULOS] = {
        "v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True},
        "v2": {"placa": "XYZ789", "tipo": "moto", "activo": True},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarVehiculo", "vehiculo_id": "v2", "placa": "abc123"},
    )

    assert resp.status_code == 400
    assert stores[VEHICULOS]["v2"]["placa"] == "XYZ789"  # unchanged


def test_editar_vehiculo_nonexistent_fails(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "editarVehiculo", "vehiculo_id": "missing", "tipo": "moto"})

    assert resp.status_code == 400


def test_eliminar_vehiculo_succeeds_when_not_assigned(monkeypatch):
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "eliminarVehiculo", "vehiculo_id": "v1"})

    assert resp.status_code == 200
    assert "v1" not in stores[VEHICULOS]


def test_eliminar_vehiculo_refuses_when_assigned_to_a_grupo(monkeypatch):
    """Same orphan-prevention discipline already chosen for eliminarGrupo:
    refuse, naming the group, rather than silently clearing the vehicle."""
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True}}
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True, "vehiculo_id": "v1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "eliminarVehiculo", "vehiculo_id": "v1"})

    assert resp.status_code == 400
    assert "Norte" in resp.json()["detail"]
    assert "v1" in stores[VEHICULOS]


def test_asignar_vehiculo_a_grupo_succeeds_and_sets_field(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarVehiculoAGrupo", "grupo_id": "g1", "vehiculo_id": "v1"},
    )

    assert resp.status_code == 200
    assert stores[GRUPOS_INSPECTORES]["g1"]["vehiculo_id"] == "v1"


def test_asignar_vehiculo_a_grupo_rejects_nonexistent_grupo_or_vehiculo(monkeypatch):
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarVehiculoAGrupo", "grupo_id": "missing", "vehiculo_id": "v1"},
    )

    assert resp.status_code == 400


def test_asignar_vehiculo_a_grupo_rejects_double_booking_names_other_grupo(monkeypatch):
    """THE load-bearing invariant: one vehicle -> at most one group at a
    time. Decision: REJECT (400, naming the other group), never silently
    move the vehicle."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {
        "g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True, "vehiculo_id": "v1"},
        "g2": {"nombre": "Sur", "miembros": ["u2"], "activo": True},
    }
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarVehiculoAGrupo", "grupo_id": "g2", "vehiculo_id": "v1"},
    )

    assert resp.status_code == 400
    assert "Norte" in resp.json()["detail"]
    # NOT silently moved -- g1 still holds it, g2 still unassigned.
    assert stores[GRUPOS_INSPECTORES]["g1"]["vehiculo_id"] == "v1"
    assert stores[GRUPOS_INSPECTORES]["g2"].get("vehiculo_id") is None


def test_asignar_vehiculo_a_grupo_reassigning_same_grupo_is_idempotent(monkeypatch):
    """Assigning a vehicle to the group that ALREADY holds it must not
    conflict with itself."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True, "vehiculo_id": "v1"}}
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "tipo": "camioneta", "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarVehiculoAGrupo", "grupo_id": "g1", "vehiculo_id": "v1"},
    )

    assert resp.status_code == 200
    assert stores[GRUPOS_INSPECTORES]["g1"]["vehiculo_id"] == "v1"


def test_desasignar_vehiculo_clears_field(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True, "vehiculo_id": "v1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "desasignarVehiculo", "grupo_id": "g1"})

    assert resp.status_code == 200
    assert stores[GRUPOS_INSPECTORES]["g1"]["vehiculo_id"] is None


# ── metricasProgreso (`puntos-disponibles` change, 2026-08-26) ─────────────
# Appended at the END of the dispatcher, deliberately not touching the
# group/vehicle CRUD region above (owned by a concurrent batch). Per-group
# and per-inspector progress, both campaigns combined AND broken out.


def test_metricas_progreso_por_grupo(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {
        "g1": {"nombre": "Norte", "miembros": ["u1", "u2"], "activo": True},
    }
    stores[PLANEACION_PUNTOS] = {
        "p1": {"grupo_id": "g1", "estado_asignacion": "hecho"},
        "p2": {"grupo_id": "g1", "estado_asignacion": "pendiente"},
        "p3": {"grupo_id": "g1", "estado_asignacion": "no_aplica"},
        "p4": {"grupo_id": None, "estado_asignacion": "pendiente"},  # not this group
    }
    stores[STICKER_MATCHES] = {
        "s1": {"grupo_id": "g1", "estado_asignacion": "hecho"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"})

    assert resp.status_code == 200
    g1 = resp.json()["metricas"]["grupos"]["g1"]
    assert g1["nombre"] == "Norte"
    assert g1["miembros"] == 2
    assert g1["survey"] == {"asignados": 3, "hechos": 1, "pendientes": 1, "no_aplica": 1, "completado_pct": 33.3}
    assert g1["stickers"] == {"asignados": 1, "hechos": 1, "pendientes": 0, "no_aplica": 0, "completado_pct": 100.0}
    assert g1["combinado"]["asignados"] == 4
    assert g1["combinado"]["hechos"] == 2


def test_metricas_progreso_por_inspector_incluye_grupos(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {
        "g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True},
    }
    stores[PLANEACION_PUNTOS] = {
        "p1": {"inspector_uid": "u1", "estado_asignacion": "hecho"},
        "p2": {"inspector_uid": "u1", "estado_asignacion": "pendiente"},
    }
    stores[STICKER_MATCHES] = {}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"})

    assert resp.status_code == 200
    u1 = resp.json()["metricas"]["inspectores"]["u1"]
    assert u1["grupos"] == ["Norte"]
    assert u1["survey"] == {"asignados": 2, "hechos": 1, "pendientes": 1, "no_aplica": 0, "completado_pct": 50.0}
    assert u1["combinado"]["asignados"] == 2


def test_metricas_progreso_inspector_sin_puntos_pero_con_grupo_aparece(monkeypatch):
    """A group member with zero individually-assigned points still shows up
    (0/0/0, 0%) — the roster is driven by group membership too, not only by
    who happens to have inspector_uid points."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u9"], "activo": True}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"})

    u9 = resp.json()["metricas"]["inspectores"]["u9"]
    assert u9["combinado"] == {"asignados": 0, "hechos": 0, "pendientes": 0, "no_aplica": 0, "completado_pct": 0.0}


def test_metricas_progreso_totales_combinados(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"estado_asignacion": "hecho"}, "p2": {"estado_asignacion": "pendiente"}}
    stores[STICKER_MATCHES] = {"s1": {"estado_asignacion": "hecho"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"})

    metricas = resp.json()["metricas"]
    assert metricas["combinado"] == {"asignados": 3, "hechos": 2, "pendientes": 1, "no_aplica": 0, "completado_pct": 66.7}
    assert metricas["stickers"]["asignados"] == 1
    assert metricas["survey"]["asignados"] == 2


# ── feature H: conductores (drivers) CRUD + link vehiculo->conductor ─────────


def test_crear_conductor_succeeds_with_all_fields(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={
        "action": "crearConductor", "cedula": "123", "nombre_completo": "Ana Pérez",
        "email": "ana@example.com", "telefono": "3001112222",
    })

    assert resp.status_code == 201
    cid = resp.json()["id"]
    doc = stores[CONDUCTORES][cid]
    assert doc["cedula"] == "123"
    assert doc["nombre_completo"] == "Ana Pérez"
    assert doc["email"] == "ana@example.com"
    assert doc["telefono"] == "3001112222"
    assert doc["activo"] is True
    assert doc["creado_por"] == UID_ADMIN


def test_crear_conductor_requires_cedula(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "crearConductor", "cedula": "", "nombre_completo": "Ana"})
    assert resp.status_code == 400


def test_crear_conductor_requires_nombre(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "crearConductor", "cedula": "123", "nombre_completo": ""})
    assert resp.status_code == 400


def test_crear_conductor_rejects_duplicate_cedula(monkeypatch):
    stores = _stores()
    stores[CONDUCTORES] = {"c1": {"cedula": "123", "nombre_completo": "Ana"}}
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "crearConductor", "cedula": "123", "nombre_completo": "Otro"})
    assert resp.status_code == 400
    assert len(stores[CONDUCTORES]) == 1


def test_list_conductores_returns_all(monkeypatch):
    stores = _stores()
    stores[CONDUCTORES] = {
        "c1": {"cedula": "1", "nombre_completo": "A"},
        "c2": {"cedula": "2", "nombre_completo": "B"},
    }
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={"action": "listConductores"})
    assert resp.status_code == 200
    assert {c["id"] for c in resp.json()["conductores"]} == {"c1", "c2"}


def test_editar_conductor_updates_fields(monkeypatch):
    stores = _stores()
    stores[CONDUCTORES] = {"c1": {"cedula": "1", "nombre_completo": "A", "email": None}}
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "editarConductor", "conductor_id": "c1",
        "nombre_completo": "Ana María", "email": "ana@x.com"})
    assert resp.status_code == 200
    assert stores[CONDUCTORES]["c1"]["nombre_completo"] == "Ana María"
    assert stores[CONDUCTORES]["c1"]["email"] == "ana@x.com"


def test_eliminar_conductor_refuses_while_a_vehiculo_references_it(monkeypatch):
    stores = _stores()
    stores[CONDUCTORES] = {"c1": {"cedula": "1", "nombre_completo": "A"}}
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "conductor_id": "c1"}}
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "eliminarConductor", "conductor_id": "c1"})
    assert resp.status_code == 400
    assert "ABC123" in resp.json()["detail"]
    assert "c1" in stores[CONDUCTORES]


def test_eliminar_conductor_succeeds_when_unreferenced(monkeypatch):
    stores = _stores()
    stores[CONDUCTORES] = {"c1": {"cedula": "1", "nombre_completo": "A"}}
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "eliminarConductor", "conductor_id": "c1"})
    assert resp.status_code == 200
    assert "c1" not in stores[CONDUCTORES]


def test_crear_vehiculo_with_conductor_stores_the_link(monkeypatch):
    stores = _stores()
    stores[CONDUCTORES] = {"c1": {"cedula": "1", "nombre_completo": "A"}}
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "crearVehiculo", "placa": "abc123", "conductor_id": "c1"})
    assert resp.status_code == 201
    vid = resp.json()["id"]
    assert stores[VEHICULOS][vid]["conductor_id"] == "c1"


def test_crear_vehiculo_rejects_nonexistent_conductor(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "crearVehiculo", "placa": "abc123", "conductor_id": "ghost"})
    assert resp.status_code == 400
    assert stores.get(VEHICULOS, {}) == {}


def test_editar_vehiculo_can_clear_conductor(monkeypatch):
    stores = _stores()
    stores[CONDUCTORES] = {"c1": {"cedula": "1", "nombre_completo": "A"}}
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "conductor_id": "c1"}}
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "editarVehiculo", "vehiculo_id": "v1", "conductor_id": ""})
    assert resp.status_code == 200
    assert stores[VEHICULOS]["v1"]["conductor_id"] is None


def test_crear_vehiculo_persists_empresa(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "crearVehiculo", "placa": "abc123", "empresa": "  Acme S.A.  "})
    assert resp.status_code == 201
    vid = resp.json()["id"]
    assert stores[VEHICULOS][vid]["empresa"] == "Acme S.A."


def test_editar_vehiculo_updates_empresa(monkeypatch):
    stores = _stores()
    stores[VEHICULOS] = {"v1": {"placa": "ABC123", "empresa": "Old"}}
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "editarVehiculo", "vehiculo_id": "v1", "empresa": "New Co"})
    assert resp.status_code == 200
    assert stores[VEHICULOS]["v1"]["empresa"] == "New Co"


# ── `planeacion-auditoria` change, Phase 2 (2026-08-26): the dispatch-site
# hook. design.md ADR-1/ADR-2; spec `Append-only write on successful
# mutation`, `A logging failure never alters a completed mutation`. ────────


def test_mutating_action_leaves_one_auditoria_doc_with_actor(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "crearGrupo", "nombre": "Norte", "miembros": ["u1"]})
    assert resp.status_code == 201
    docs = list(stores[PLANEACION_AUDITORIA].values())
    assert len(docs) == 1
    assert docs[0]["actor_uid"] == UID_ADMIN
    assert docs[0]["actor_email"] == FAKE_CLAIMS_ADMIN["email"]
    assert docs[0]["accion"] == "crearGrupo"


def test_read_only_action_leaves_zero_auditoria_docs(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={"action": "listGrupos"})
    assert resp.status_code == 200
    assert stores[PLANEACION_AUDITORIA] == {}


def _auditoria_doc(*, entidad, actor_uid, ts, accion="crearGrupo", entidad_id="x", resumen="r"):
    return {
        "actor_uid": actor_uid, "actor_email": f"{actor_uid}@example.com", "accion": accion,
        "entidad": entidad, "entidad_id": entidad_id, "params": {}, "resultado": {},
        "resumen": resumen, "ts": ts,
    }


# ── `planeacion-auditoria` change, Phase 3 (2026-08-26): `listAuditoria`.
# design.md ADR-4; spec `listAuditoria read action`. ─────────────────────────


def test_list_auditoria_no_filters_orders_newest_first(monkeypatch):
    stores = _stores()
    stores[PLANEACION_AUDITORIA] = {
        "a": _auditoria_doc(entidad="grupo", actor_uid="u1", ts=100),
        "b": _auditoria_doc(entidad="vehiculo", actor_uid="u2", ts=300),
        "c": _auditoria_doc(entidad="conductor", actor_uid="u3", ts=200),
    }
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={"action": "listAuditoria"})
    assert resp.status_code == 200
    entradas = resp.json()["entradas"]
    assert [e["ts"] for e in entradas] == [300, 200, 100]


def test_list_auditoria_filters_by_tipo(monkeypatch):
    stores = _stores()
    stores[PLANEACION_AUDITORIA] = {
        "a": _auditoria_doc(entidad="grupo", actor_uid="u1", ts=100),
        "b": _auditoria_doc(entidad="vehiculo", actor_uid="u2", ts=300),
    }
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={"action": "listAuditoria", "tipo": "vehiculo"})
    assert resp.status_code == 200
    entradas = resp.json()["entradas"]
    assert len(entradas) == 1
    assert entradas[0]["entidad"] == "vehiculo"


def test_list_auditoria_filters_by_usuario(monkeypatch):
    stores = _stores()
    stores[PLANEACION_AUDITORIA] = {
        "a": _auditoria_doc(entidad="grupo", actor_uid="u1", ts=100),
        "b": _auditoria_doc(entidad="vehiculo", actor_uid="u9", ts=300),
    }
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={"action": "listAuditoria", "usuario": "u9"})
    assert resp.status_code == 200
    entradas = resp.json()["entradas"]
    assert len(entradas) == 1
    assert entradas[0]["actor_uid"] == "u9"


def test_list_auditoria_filters_by_date_range(monkeypatch):
    stores = _stores()
    stores[PLANEACION_AUDITORIA] = {
        "a": _auditoria_doc(entidad="grupo", actor_uid="u1", ts=100),
        "b": _auditoria_doc(entidad="grupo", actor_uid="u1", ts=200),
        "c": _auditoria_doc(entidad="grupo", actor_uid="u1", ts=300),
    }
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "listAuditoria", "desde": 150, "antes_de": 300})
    assert resp.status_code == 200
    entradas = resp.json()["entradas"]
    assert [e["ts"] for e in entradas] == [200]


def test_list_auditoria_pagination_bounds_result(monkeypatch):
    stores = _stores()
    stores[PLANEACION_AUDITORIA] = {
        str(i): _auditoria_doc(entidad="grupo", actor_uid="u1", ts=i) for i in range(5)
    }
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={"action": "listAuditoria", "limit": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entradas"]) == 2
    assert data["hay_mas"] is True

    # second page, via the cursor
    cursor = data["entradas"][-1]["ts"]
    resp2 = client.post("/planeacion-asignaciones", json={
        "action": "listAuditoria", "limit": 2, "antes_de": cursor})
    assert resp2.status_code == 200
    assert len(resp2.json()["entradas"]) == 2


def test_list_auditoria_combined_tipo_and_usuario_filter_needs_no_composite_index(monkeypatch):
    """planeacion-flujo-confiable scope rider: `tipo` + `usuario` together
    used to need the `entidad+actor_uid+ts` composite index (one of the 3
    the router previously depended on). `list_auditoria` now filters both
    IN CODE over a single `order_by("ts")` fetch — no composite index
    involved at all, real or faked."""
    stores = _stores()
    stores[PLANEACION_AUDITORIA] = {
        "a": _auditoria_doc(entidad="vehiculo", actor_uid="u9", ts=300),
        "b": _auditoria_doc(entidad="grupo", actor_uid="u9", ts=200),
        "c": _auditoria_doc(entidad="vehiculo", actor_uid="u1", ts=100),
    }
    client = _admin_client(monkeypatch, stores)
    resp = client.post("/planeacion-asignaciones", json={
        "action": "listAuditoria", "tipo": "vehiculo", "usuario": "u9"})
    assert resp.status_code == 200
    entradas = resp.json()["entradas"]
    assert [e["ts"] for e in entradas] == [300]


def test_audit_write_failure_does_not_alter_the_mutation_response(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    def _boom(*args, **kwargs):
        raise RuntimeError("firestore is down")

    from app.services import planeacion_audit
    monkeypatch.setattr(planeacion_audit, "registrar", _boom)

    resp = client.post("/planeacion-asignaciones", json={
        "action": "crearGrupo", "nombre": "Norte", "miembros": ["u1"]})
    assert resp.status_code == 201
    assert resp.json()["ok"] is True
    assert "id" in resp.json()
    # no exception surfaced; the audit write is best-effort and swallowed
    assert stores[PLANEACION_AUDITORIA] == {}


# ── Dispatcher error mapping: missing-index vs generic (hotfix B1) ──────────


def test_dispatch_maps_missing_index_error_to_actionable_503(monkeypatch, caplog):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    def _boom(*args, **kwargs):
        raise Exception(
            "400 The query requires an index. You can create it here: "
            "https://console.firebase.google.com/project/x/firestore/indexes?create_composite=abc"
        )

    monkeypatch.setattr(pa, "list_grupos", _boom)

    with caplog.at_level("ERROR"):
        resp = client.post("/planeacion-asignaciones", json={"action": "listGrupos"})

    assert resp.status_code == 503
    # Item 3b (2026-08-27): this is an admin-only endpoint, so surfacing the
    # index-creation URL directly in the 503 detail is correct and saves the
    # admin a hop to the server logs.
    detail = resp.json()["detail"]
    assert "https://console.firebase.google.com/project/x/firestore/indexes?create_composite=abc" in detail
    assert "Falta un índice de la base de datos" in detail
    # the original error (with the console creation link) must still be logged
    assert "console.firebase.google.com" in caplog.text


def test_dispatch_missing_index_error_without_a_link_falls_back_to_generic_message(monkeypatch):
    """Item 3b: the link is surfaced WHEN PRESENT in the error text -- a
    missing-index error without one (unlikely in practice, but the regex
    must not crash) still gets the actionable 503, just without a link."""
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    def _boom(*args, **kwargs):
        raise Exception("400 The query requires an index, but no url this time.")

    monkeypatch.setattr(pa, "list_grupos", _boom)

    resp = client.post("/planeacion-asignaciones", json={"action": "listGrupos"})

    assert resp.status_code == 503
    assert resp.json()["detail"] == (
        "Falta un índice de la base de datos para esta consulta. Avisar al "
        "administrador (el enlace de creación está en los logs del servidor)."
    )


def test_dispatch_keeps_generic_502_for_other_errors(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    def _boom(*args, **kwargs):
        raise Exception("boom: something unrelated broke")

    monkeypatch.setattr(pa, "list_grupos", _boom)

    resp = client.post("/planeacion-asignaciones", json={"action": "listGrupos"})

    assert resp.status_code == 502
    assert resp.json()["detail"] == "boom: something unrelated broke"
