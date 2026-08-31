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

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.cloud import firestore as _fs_real

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app
from app.routers import planeacion_asignaciones as pa

# `actualizado_en` speed follow-up (stage 1): the fake store keeps raw
# values, so a literal `firestore.SERVER_TIMESTAMP` sentinel would be stored
# as-is instead of a real timestamp. Resolve it to a real, strictly
# increasing UTC datetime on write, mimicking a real Firestore server
# timestamp closely enough for a later stage's delta query to sort on it.
_FAKE_TS_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_fake_ts_counter = 0


def _resolve_server_timestamps(data: dict[str, Any]) -> dict[str, Any]:
    global _fake_ts_counter
    out = {}
    for k, v in data.items():
        if v is _fs_real.SERVER_TIMESTAMP:
            _fake_ts_counter += 1
            out[k] = _FAKE_TS_BASE + timedelta(microseconds=_fake_ts_counter)
        else:
            out[k] = v
    return out

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
        data = _resolve_server_timestamps(data)
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
        calls: dict[str, int] | None = None,
    ) -> None:
        self._collection = collection
        self._store = store
        self._ids = docs if docs is not None else list(store.keys())
        self._order_field = order_field
        self._order_desc = order_desc
        self._limit_n = limit_n
        self._select_fields = select_fields
        # Call-count instrumentation (perf-cache tests): incremented once per
        # `.get()`/`.stream()`, keyed by collection name. `None` when a test
        # doesn't care (most of the suite) — see `_FakeFirestore.__init__`.
        self._calls = calls

    def where(self, field: str, op: str, value: Any) -> "_FakeQuery":
        if op == "==":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) == value]
        elif op == "!=":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) != value]
        elif op == ">=":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) is not None
                       and self._store[i][field] >= value]
        elif op == ">":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) is not None
                       and self._store[i][field] > value]
        elif op == "<":
            matched = [i for i in self._ids if self._store.get(i, {}).get(field) is not None
                       and self._store[i][field] < value]
        else:
            raise AssertionError(f"unsupported op {op!r} in fake Firestore")
        return _FakeQuery(
            self._collection, self._store, matched, self._order_field, self._order_desc,
            self._limit_n, self._select_fields, self._calls,
        )

    def order_by(self, field: str, direction: str | None = None) -> "_FakeQuery":
        desc = direction == "DESCENDING"
        return _FakeQuery(
            self._collection, self._store, self._ids, field, desc, self._limit_n, self._select_fields, self._calls
        )

    def limit(self, n: int) -> "_FakeQuery":
        return _FakeQuery(
            self._collection, self._store, self._ids, self._order_field, self._order_desc, n,
            self._select_fields, self._calls,
        )

    def select(self, field_paths: list[str]) -> "_FakeQuery":
        """Firestore projection: only these fields come back in `to_dict()`
        (`auto-agrupar-comuna-barrio` follow-up, item 2B — same precedent as
        `get_all(field_paths=...)` below)."""
        return _FakeQuery(
            self._collection, self._store, self._ids, self._order_field, self._order_desc,
            self._limit_n, list(field_paths), self._calls,
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
        if self._calls is not None:
            self._calls[self._collection] = self._calls.get(self._collection, 0) + 1
        snaps = []
        for doc_id in self._ordered_ids():
            snap = _FakeSnapshot(self._collection, doc_id, self._projected(self._store.get(doc_id)))
            snap.reference = _FakeDocRef(self._store, self._collection, doc_id)
            snaps.append(snap)
        return snaps


class _FakeCollection(_FakeQuery):
    def __init__(
        self, collection: str, store: dict[str, dict[str, Any]], calls: dict[str, int] | None = None
    ) -> None:
        super().__init__(collection, store, calls=calls)
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
    def __init__(
        self, stores: dict[str, dict[str, dict[str, Any]]], calls: dict[str, int] | None = None
    ) -> None:
        self._stores = stores
        self._calls = calls

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(name, self._stores.setdefault(name, {}), calls=self._calls)

    def batch(self) -> _FakeBatch:
        return _FakeBatch()

    def get_all(self, refs: list[_FakeDocRef], field_paths: list[str] | None = None) -> list[_FakeSnapshot]:
        return [ref.get() for ref in refs]


class _FakeSismoClients:
    def __init__(
        self, stores: dict[str, dict[str, dict[str, Any]]], calls: dict[str, int] | None = None
    ) -> None:
        self.firestore = _FakeFirestore(stores, calls=calls)
        self.app = object()


def _stores() -> dict[str, dict[str, dict[str, Any]]]:
    return {PLANEACION_PUNTOS: {}, PLANEACION_CUADRILLAS: {}, PLANEACION_AUDITORIA: {}}


def _app(
    monkeypatch, stores: dict[str, dict[str, dict[str, Any]]], calls: dict[str, int] | None = None
) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    monkeypatch.setenv("SURVEY123_FORM_URL", "https://survey123.arcgis.com/share/abc123")
    monkeypatch.setenv("SURVEY123_FIELD_APP_ITEM_ID", "itemid123")
    credentials.s3.cache_clear()
    monkeypatch.setattr(credentials, "sismo", lambda: _FakeSismoClients(stores, calls=calls))
    return create_app()


def _admin_client(monkeypatch, stores) -> TestClient:
    app = _app(monkeypatch, stores)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN
    return TestClient(app)


def _viewer_client(monkeypatch, stores) -> TestClient:
    app = _app(monkeypatch, stores)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER
    return TestClient(app)


def _admin_client_with_calls(monkeypatch, stores) -> tuple[TestClient, dict[str, int]]:
    """Same as `_admin_client`, plus a `collection(name).get()` call-count
    dict for the aggregate-cache tests below."""
    calls: dict[str, int] = {}
    app = _app(monkeypatch, stores, calls=calls)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN
    return TestClient(app), calls


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


# ── Pure cluster_mas_denso ("Agrupar" builds ONE cuadrilla: the densest
# cluster, not a partition of the whole working set) ───────────────────────


def test_cluster_mas_denso_picks_the_seed_with_the_most_neighbors():
    # A tight trio plus a lone far-away point: the trio's seed has density 3
    # (itself + 2 neighbors within radius), the solo point's density is 1.
    dense = [_pt("d1", 3.4000, -76.5000), _pt("d2", 3.4001, -76.5001), _pt("d3", 3.4002, -76.5002)]
    solo = [_pt("solo", 3.4600, -76.5600)]
    grupo = pa.cluster_mas_denso(dense + solo, max_radius_m=800, max_size=3)
    assert {p["id"] for p in grupo} == {"d1", "d2", "d3"}


def test_cluster_mas_denso_caps_at_max_size_even_with_more_pending_points():
    # 5 points within radius of each other; max_size=2 must still cap the
    # group at 2 — max_size takes priority over how many points qualify.
    puntos = [_pt(f"p{i}", 3.40 + i * 0.00001, -76.50) for i in range(5)]
    grupo = pa.cluster_mas_denso(puntos, max_radius_m=800, max_size=2)
    assert len(grupo) == 2


def test_cluster_mas_denso_pulls_in_points_beyond_the_radius_to_reach_max_size():
    # max_size takes PRIORITY over the radius: with only 2 points and one
    # of them 5000m away (far outside max_radius_m), the group must still
    # reach max_size=2 by pulling the distant point in.
    near = _pt("near", 3.4000, -76.5000)
    far = _pt("far", 3.4600, -76.5600)  # ~9.4 km away, well outside max_radius_m
    grupo = pa.cluster_mas_denso([near, far], max_radius_m=100, max_size=2)
    assert {p["id"] for p in grupo} == {"near", "far"}


def test_cluster_mas_denso_tie_break_is_deterministic_by_score_then_id():
    # Two seeds tie on density (1 neighbor each, far apart) — the one with
    # the higher prioridad_score wins the seed, so its own neighborhood
    # (itself only, here) is what gets returned.
    a = {**_pt("a", 3.40, -76.50), "prioridad_score": 10}
    b = {**_pt("b", 3.90, -76.90), "prioridad_score": 90}
    grupo = pa.cluster_mas_denso([a, b], max_radius_m=10, max_size=1)
    assert [p["id"] for p in grupo] == ["b"]


def test_cluster_mas_denso_empty_input_returns_empty_list():
    assert pa.cluster_mas_denso([], max_radius_m=800, max_size=10) == []


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


def test_list_puntos_always_includes_assigned_points_beyond_top_n(monkeypatch):
    """"Always include assigned" (user decision 2026-08-27): a point with a
    grupo/inspector/cuadrilla assignment appears even when it ranks BELOW the
    top-N priority page — otherwise a low-priority assigned point is invisible
    and unmanageable (can't desasignar it, it blocks its group's deletion)."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "hi1": _punto(prioridad_score=90),
        "hi2": _punto(prioridad_score=80),
        # Low priority (score 1) so it falls outside the top-2 page, but it is
        # assigned to a group → must still be returned.
        "assigned_lo": {**_punto(estado_asignacion="asignado", prioridad_score=1), "grupo_id": "g1"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos", "limit": 2})

    body = resp.json()
    ids = {p["id"] for p in body["puntos"]}
    assert ids == {"hi1", "hi2", "assigned_lo"}
    # No duplicate even though the assigned point is also in the raw score query.
    assert len(body["puntos"]) == 3


def test_list_puntos_assigned_point_still_honors_active_comuna_filter(monkeypatch):
    """The always-included assigned set is still narrowed by the active
    filters — an assigned point in another comuna does not leak into a
    comuna-filtered view."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "hi1": _punto(prioridad_score=90, comuna="Comuna 1"),
        "assigned_other": {**_punto(estado_asignacion="asignado", prioridad_score=1, comuna="Comuna 2"), "grupo_id": "g1"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "listPuntos", "comuna": "Comuna 1"})

    ids = {p["id"] for p in resp.json()["puntos"]}
    assert ids == {"hi1"}


def test_list_puntos_reflects_a_new_assignment_after_the_widen_cache_was_warm(monkeypatch):
    """Root-cause repro for "las asignaciones no se están persistiendo",
    still valid under the stage 2 `PlaneacionPuntosSnapshot`: the UI's own
    `reload()` warms the snapshot on every tab open/reload BEFORE an admin
    makes a change. If a low-priority point (outside the top-N page) is
    then assigned via `asignarGrupoAPuntos`, the very next `listPuntos`
    call — the reload the UI fires right after the write — MUST see it, not
    the pre-assignment snapshot. `snapshot.mark_dirty()` in the dispatcher's
    post-mutation block is what is supposed to guarantee this (via one
    bounded delta query on the next `snapshot.docs()` call)."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "hi1": _punto(prioridad_score=90),
        # Low priority so it starts outside a limit=1 page.
        "lo1": _punto(prioridad_score=1),
    }
    client = _admin_client(monkeypatch, stores)

    # Tab open / reload() warms the "assignedPuntos" cache while lo1 is still
    # unassigned — the exact ordering the real UI performs.
    warm = client.post("/planeacion-asignaciones", json={"action": "listPuntos", "limit": 1})
    assert {p["id"] for p in warm.json()["puntos"]} == {"hi1"}

    # Admin assigns the low-priority point to a grupo.
    assign = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["lo1"]},
    )
    assert assign.status_code == 200

    # The UI's own post-write reload() — must reflect the assignment, not the
    # pre-assignment cached widen set.
    after = client.post("/planeacion-asignaciones", json={"action": "listPuntos", "limit": 1})
    ids = {p["id"] for p in after.json()["puntos"]}
    assert "lo1" in ids, (
        f"lo1 was just assigned to grupo g1 but is missing from listPuntos "
        f"({ids}) — the assignedPuntos widen cache was not busted by the "
        f"mutation."
    )


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


def test_resumen_dup_aware_building_counts_collapse_same_building_reports(monkeypatch):
    # p1/p2 share a dup_grupo_id (tagged by planeacion_cruce.tag_duplicados as
    # the same building) -- report-based `total`/`levantados` count them
    # separately, but the dup-aware building counts must collapse them to one.
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {**_punto(), "dup_grupo_id": "dup-1", "registro_id": "1"},
        "p2": {**_punto(tiene_survey=True), "dup_grupo_id": "dup-1", "registro_id": "2"},
        "p3": {**_punto(), "dup_grupo_id": "dup-3", "registro_id": "3"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "resumen"})

    resumen_body = resp.json()["resumen"]
    assert resumen_body["total"] == 3  # unchanged: report-based count
    assert resumen_body["total_edificios"] == 2  # dup-1 group + dup-3 group
    # dup-1's group is "levantado" because p2 (a member) has a survey.
    assert resumen_body["edificios_levantados"] == 1
    assert resumen_body["edificios_pendientes"] == 1


def test_resumen_dup_aware_falls_back_to_registro_id_for_untagged_docs(monkeypatch):
    # A legacy doc with no dup_grupo_id (written before dedup tagging shipped)
    # must still be tallied -- one building per report, same as today.
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {**_punto(), "registro_id": "10"},
        "p2": {**_punto(tiene_survey=True), "registro_id": "20"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "resumen"})

    resumen_body = resp.json()["resumen"]
    assert resumen_body["total_edificios"] == 2
    assert resumen_body["edificios_levantados"] == 1
    assert resumen_body["edificios_pendientes"] == 1


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


def test_auto_agrupar_scoped_run_stays_within_global_top_n(monkeypatch):
    """"Auto-agrupar solo dentro del 2500" (user decision 2026-08-27): a
    SCOPED run must only cluster points that also belong to the CITY-WIDE
    top-`limite` set the Puntos table shows. Without the intersection a
    low-priority barrio yields clusters whose points fall outside the
    focalized working set and are invisible in the table."""
    stores = _stores()
    close_a = {"lat": 3.4000, "lon": -76.5000}
    close_b = {"lat": 3.4001, "lon": -76.5001}
    stores[PLANEACION_PUNTOS] = {
        # In the target zone AND in the global top-3 (scores 100/95): clustered.
        "zone_hi1": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                     "prioridad_score": 100, "comuna": "Comuna 12", "barrio": "Asturias", "coords": close_a},
        "zone_hi2": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                     "prioridad_score": 95, "comuna": "Comuna 12", "barrio": "Asturias", "coords": close_b},
        # In the target zone but BELOW the global top-3 (scores 10/5): must be
        # excluded — before the fix these would cluster (top-3 within the zone).
        "zone_lo1": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                     "prioridad_score": 10, "comuna": "Comuna 12", "barrio": "Asturias", "coords": close_a},
        "zone_lo2": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                     "prioridad_score": 5, "comuna": "Comuna 12", "barrio": "Asturias", "coords": close_b},
        # Higher-priority points elsewhere that occupy the global top-N but are
        # out of scope, pushing the low-score zone points below the cutoff.
        "other_hi": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                     "prioridad_score": 90, "comuna": "Comuna 19", "barrio": "Cuarto de Legua",
                     "coords": {"lat": 3.42, "lon": -76.52}},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones",
                       json={"action": "autoAgrupar", "comuna": "Comuna 12", "barrio": "Asturias", "limite": 3})

    assert resp.status_code == 200
    assigned = {pid for c in resp.json()["cuadrillas"] for pid in c["puntos"]}
    assert assigned == {"zone_hi1", "zone_hi2"}
    assert stores[PLANEACION_PUNTOS]["zone_lo1"]["cuadrilla_id"] is None
    assert stores[PLANEACION_PUNTOS]["zone_lo2"]["cuadrilla_id"] is None


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


def test_auto_agrupar_router_excludes_dano_estructural_points(monkeypatch):
    # User decision 2026-08-27: a point whose atencionsismo report already
    # carries afectacion=DAÑO ESTRUCTURAL is assumed to have a sufficient
    # specialized evaluation and must never enter the auto-agrupar pool —
    # same in-code exclusion shape `points_excluded`/`points_with_survey`
    # already apply post-fetch.
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "dano_estructural": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                             "prioridad_score": 99, "coords": {"lat": 3.40, "lon": -76.50},
                             "afectacion": "DAÑO ESTRUCTURAL"},
        "real_pendiente": {"estado_asignacion": "pendiente", "cuadrilla_id": None, "tiene_survey": False,
                           "prioridad_score": 10, "coords": {"lat": 3.41, "lon": -76.51},
                           "afectacion": "RIESGO COLAPSO"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar"})

    assert resp.status_code == 200
    assigned = {pid for c in resp.json()["cuadrillas"] for pid in c["puntos"]}
    assert assigned == {"real_pendiente"}


def test_auto_agrupar_router_creates_only_the_densest_cluster(monkeypatch):
    # "Agrupar" now creates exactly ONE cuadrilla per click — the densest
    # cluster — not one cuadrilla per cluster. A tight trio + a lone
    # far-away point, capped with maxSize=3, must come back as a single
    # 3-point cuadrilla; "solo" is left ungrouped for a later run.
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

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar", "maxSize": 3})

    cuadrillas = resp.json()["cuadrillas"]
    assert len(cuadrillas) == 1
    assert set(cuadrillas[0]["puntos"]) == {"d1", "d2", "d3"}
    assert stores[PLANEACION_PUNTOS]["solo"]["cuadrilla_id"] is None


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


def test_auto_agrupar_router_with_comuna_only_names_cuadrillas_with_zone(monkeypatch):
    """Zone-naming follow-up (2026-08-27): a comuna-scoped run stamps each
    created cuadrilla with `nombre: "COMUNA i"` (1-based, per this run)."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "c19a": _scopeable("COMUNA 19", "San Fernando", 3.40, -76.50),
        "c19b": _scopeable("COMUNA 19", "Tequendama", 3.4001, -76.5001),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar", "comuna": "COMUNA 19"})

    cuadrillas = resp.json()["cuadrillas"]
    assert cuadrillas
    assert [c["nombre"] for c in cuadrillas] == [f"COMUNA 19 {i}" for i in range(1, len(cuadrillas) + 1)]


def test_auto_agrupar_router_with_comuna_and_barrio_names_cuadrillas_with_zone(monkeypatch):
    """Comuna+barrio scoped run: `nombre: "COMUNA · BARRIO i"`."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "sf1": _scopeable("COMUNA 19", "San Fernando", 3.40, -76.50),
        "sf2": _scopeable("COMUNA 19", "San Fernando", 3.4001, -76.5001),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "autoAgrupar", "comuna": "COMUNA 19", "barrio": "San Fernando"},
    )

    cuadrillas = resp.json()["cuadrillas"]
    assert cuadrillas
    assert [c["nombre"] for c in cuadrillas] == [
        f"COMUNA 19 · San Fernando {i}" for i in range(1, len(cuadrillas) + 1)
    ]


def test_auto_agrupar_router_unscoped_run_has_no_nombre(monkeypatch):
    """An UNSCOPED (city-wide) run keeps the prior behavior: no `nombre` —
    there is no single zone to name."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "c19": _scopeable("COMUNA 19", "San Fernando", 3.40, -76.50),
        "c2": _scopeable("COMUNA 2", "Otro Barrio", 3.4600, -76.5600),
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "autoAgrupar"})

    cuadrillas = resp.json()["cuadrillas"]
    assert cuadrillas
    assert all("nombre" not in c for c in cuadrillas)


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


def test_crear_cuadrilla_rejects_dano_estructural_points_naming_them(monkeypatch):
    # User decision 2026-08-27: a point whose atencionsismo report already
    # carries afectacion=DAÑO ESTRUCTURAL is assumed to have a sufficient
    # specialized evaluation and must not be assignable — same "not
    # assignable" shape as no_aplica, just data-driven from the cruce.
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"cuadrilla_id": None, "tiene_survey": False, "estado_asignacion": "pendiente",
               "afectacion": "DAÑO ESTRUCTURAL"},
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


def test_eliminar_cuadrilla_clears_grupo_id_and_sticker_twin(monkeypatch):
    # Regression: deleting a cuadrilla used to leave grupo_id on its points,
    # orphaning them -- invisible in every chip (no cuadrilla left to show
    # them) but still summed by grupo-wide readers (formulario, "Por grupo").
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1"], "inspector_uid": "insp-1", "origen": "auto"}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {
            "cuadrilla_id": "c1",
            "inspector_uid": "insp-1",
            "grupo_id": "g1",
            "coords": {"lat": 3.40, "lon": -76.50},
            "direccion": "Calle 1",
        },
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "grupo_id": "g1"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "eliminarCuadrilla", "cuadrilla_id": "c1"})

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["grupo_id"] is None
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] is None


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
    assert body == {"ok": True, "eliminadas": 1, "puntosLiberados": 1, "conservadas": 0}
    assert "c-auto" not in stores[PLANEACION_CUADRILLAS]
    assert "c-manual" in stores[PLANEACION_CUADRILLAS]
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "pendiente"
    assert stores[PLANEACION_PUNTOS]["p2"]["cuadrilla_id"] == "c-manual"


def test_reiniciar_agrupacion_preserves_auto_cuadrilla_with_a_grupo_assigned(monkeypatch):
    """Grupo-protection (2026-08-27, user decision): an auto cuadrilla with
    real assignment work on top (a grupo de inspectores on at least one of
    its points) must survive `reiniciarAgrupacion` untouched — releasing it
    would silently discard that work. A sibling auto cuadrilla with no grupo
    assigned is still reset normally."""
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {
        "c-protegida": {"puntos": ["p1", "p2"], "inspector_uid": "insp-1", "origen": "auto"},
        "c-libre": {"puntos": ["p3"], "inspector_uid": "insp-2", "origen": "auto"},
    }
    stores[PLANEACION_PUNTOS] = {
        "p1": {"cuadrilla_id": "c-protegida", "inspector_uid": "insp-1", "estado_asignacion": "asignado", "grupo_id": "g1"},
        "p2": {"cuadrilla_id": "c-protegida", "inspector_uid": "insp-1", "estado_asignacion": "asignado"},
        "p3": {"cuadrilla_id": "c-libre", "inspector_uid": "insp-2", "estado_asignacion": "asignado"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "reiniciarAgrupacion"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "eliminadas": 1, "puntosLiberados": 1, "conservadas": 1}
    assert "c-protegida" in stores[PLANEACION_CUADRILLAS]
    assert "c-libre" not in stores[PLANEACION_CUADRILLAS]
    # Untouched: still points to its cuadrilla, still "asignado".
    assert stores[PLANEACION_PUNTOS]["p1"]["cuadrilla_id"] == "c-protegida"
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "asignado"
    assert stores[PLANEACION_PUNTOS]["p1"]["grupo_id"] == "g1"
    assert stores[PLANEACION_PUNTOS]["p2"]["cuadrilla_id"] == "c-protegida"
    # Released as usual: no grupo on any of its points.
    assert stores[PLANEACION_PUNTOS]["p3"]["estado_asignacion"] == "pendiente"
    assert stores[PLANEACION_PUNTOS]["p3"]["cuadrilla_id"] is None


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


def test_editar_asignacion_serializes_stray_timestamp_fields(monkeypatch):
    """Regression: the point doc carries OTHER timestamp fields (e.g.
    `asignado_en`, stamped by asignarInspector) besides `editado_en`. The
    hand-built response only isoformat-converted `editado_en`, so a live
    point with an `asignado_en` datetime 502'd the JSONResponse ("Object of
    type DatetimeWithNanoseconds is not JSON serializable"). The whole
    response must go through `_jsonable`."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {
            "estado_asignacion": "asignado",
            "inspector_uid": "insp-a",
            "asignado_en": datetime(2026, 8, 27, 12, 0, 0),  # stray timestamp
        }
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "editarAsignacion", "punto_id": "p1", "estado_asignacion": "pendiente", "inspector_uid": None},
    )

    assert resp.status_code == 200, resp.text
    punto = resp.json()["punto"]
    assert isinstance(punto["asignado_en"], str)  # serialized, not a raw datetime
    assert punto["inspector_uid"] is None
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] is None
    assert stores[PLANEACION_PUNTOS]["p1"]["estado_asignacion"] == "pendiente"


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


# ── `survey-sticker-sync` (2026-08-27): radius sweep on top of the exact
# twin — every ELIGIBLE `sticker_matches` doc within `DEFAULT_MAX_RADIUS_M`
# of an assigned point gets `grupo_id` only (never the twin linkage
# fields), no capacity cap. ─────────────────────────────────────────────


def test_asignar_grupo_radius_sweep_assigns_unassigned_sticker_within_800m(monkeypatch):
    """A sticker that is NOT the exact twin (>40m, no address match) but
    within DEFAULT_MAX_RADIUS_M still gets grupo_id, best-effort."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.4045, "lon": -76.50}, "direccion": "Otra calle sin relacion"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 1
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] == "g1"


def test_asignar_grupo_radius_sweep_leaves_a_sticker_beyond_800m_untouched(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.4081, "lon": -76.50}, "direccion": "Otra calle sin relacion"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 0
    assert "grupo_id" not in stores[STICKER_MATCHES]["s1"]


def test_asignar_grupo_radius_sweep_skips_a_sticker_already_in_another_grupo(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.4045, "lon": -76.50}, "direccion": "Otra calle sin relacion", "grupo_id": "g0"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 0
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] == "g0"


def test_asignar_grupo_radius_sweep_skips_a_hecho_sticker(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.4045, "lon": -76.50}, "direccion": "Otra calle sin relacion",
               "estado_asignacion": "hecho"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 0
    assert "grupo_id" not in stores[STICKER_MATCHES]["s1"]


def test_asignar_grupo_radius_sweep_skips_a_tiene_sticker_true_document(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.4045, "lon": -76.50}, "direccion": "Otra calle sin relacion",
               "tiene_sticker": True},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["stickers_asignados"] == 0
    assert "grupo_id" not in stores[STICKER_MATCHES]["s1"]


def test_asignar_grupo_radius_sweep_never_writes_twin_linkage_fields(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "clave_integracion": "PLN-1-ABC"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.4045, "lon": -76.50}, "direccion": "Otra calle sin relacion"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] == "g1"
    assert "clave_integracion" not in stores[STICKER_MATCHES]["s1"]
    assert "planeacion_punto_id" not in stores[STICKER_MATCHES]["s1"]


def test_asignar_grupo_radius_sweep_first_link_wins_across_two_points_in_one_batch(monkeypatch):
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"},
        "p2": {"coords": {"lat": 3.4001, "lon": -76.50}, "direccion": "Calle 2"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.4045, "lon": -76.50}, "direccion": "Otra calle sin relacion"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1", "p2"]},
    )

    assert resp.status_code == 200
    # S is within radius of BOTH points but must be claimed only once.
    assert resp.json()["stickers_asignados"] == 1
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] == "g1"


def test_asignar_grupo_radius_sweep_failure_never_fails_the_survey_side_write(monkeypatch):
    """FAIL-SOFT: the radius sweep itself failing (distinct from a total
    sticker-store read failure, already covered above) must not fail the
    survey-side grupo_id write either."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.4045, "lon": -76.50}, "direccion": "Otra calle sin relacion"},
    }
    client = _admin_client(monkeypatch, stores)

    def _boom(*args, **kwargs):
        raise RuntimeError("radius math unavailable")

    monkeypatch.setattr(pa, "haversine_m", _boom)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarGrupoAPuntos", "grupo_id": "g1", "puntos": ["p1"]},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["grupo_id"] == "g1"
    assert resp.json()["stickers_asignados"] == 0


# ── Pairing-key propagation (2026-08-31): asignarInspector / reasignarPunto
# persist the SAME `clave_integracion`/`planeacion_punto_id` linkage the
# grupo path already writes — linkage-only (never `grupo_id`, no radius
# sweep), same fail-soft/first-link-wins helper. ─────────────────────────


def test_asignar_inspector_propagates_pairing_keys_never_grupo_id(monkeypatch):
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1"], "inspector_uid": None, "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "clave_integracion": "PLN-1-ABC"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"},
        # Within DEFAULT_MAX_RADIUS_M but NOT the exact twin: linkage-only
        # propagation must skip the radius sweep entirely.
        "s2": {"coords": {"lat": 3.4045, "lon": -76.50}, "direccion": "Otra calle sin relacion"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarInspector", "cuadrilla_id": "c1", "inspector_uid": "insp-1"},
    )

    assert resp.status_code == 200
    assert stores[STICKER_MATCHES]["s1"]["clave_integracion"] == "PLN-1-ABC"
    assert stores[STICKER_MATCHES]["s1"]["planeacion_punto_id"] == "p1"
    assert "grupo_id" not in stores[STICKER_MATCHES]["s1"]
    assert stores[STICKER_MATCHES]["s2"] == {"coords": {"lat": 3.4045, "lon": -76.50},
                                             "direccion": "Otra calle sin relacion"}


def test_asignar_inspector_does_not_overwrite_a_twin_linked_to_a_different_clave(monkeypatch):
    """First-link-wins: a twin already carrying a DIFFERENT clave_integracion
    is a different planeacion point's pairing -- never overwritten."""
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1"], "inspector_uid": None, "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "clave_integracion": "PLN-NEW"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "clave_integracion": "PLN-OLD"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarInspector", "cuadrilla_id": "c1", "inspector_uid": "insp-1"},
    )

    assert resp.status_code == 200
    assert stores[STICKER_MATCHES]["s1"]["clave_integracion"] == "PLN-OLD"
    assert "planeacion_punto_id" not in stores[STICKER_MATCHES]["s1"]


def test_asignar_inspector_succeeds_even_if_sticker_propagation_raises(monkeypatch):
    """FAIL-SOFT: a sticker-side failure must NEVER fail the inspector
    assignment that already committed."""
    stores = _stores()
    stores[PLANEACION_CUADRILLAS] = {"c1": {"puntos": ["p1"], "inspector_uid": None, "origen": "manual"}}
    stores[PLANEACION_PUNTOS] = {"p1": {"coords": {"lat": 3.40, "lon": -76.50}}}
    stores[STICKER_MATCHES] = {"s1": {"coords": {"lat": 3.40, "lon": -76.50}}}
    client = _admin_client(monkeypatch, stores)

    def _boom(*args, **kwargs):
        raise RuntimeError("sticker store unavailable")

    monkeypatch.setattr(pa, "_doc_to_dict", _boom)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "asignarInspector", "cuadrilla_id": "c1", "inspector_uid": "insp-1"},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] == "insp-1"


def test_reasignar_punto_propagates_pairing_keys_never_grupo_id(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"inspector_uid": "insp-old", "coords": {"lat": 3.40, "lon": -76.50},
               "direccion": "Calle 1", "clave_integracion": "PLN-1-ABC"},
    }
    stores[STICKER_MATCHES] = {"s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1"}}
    client = _admin_client(monkeypatch, stores)

    resp = client.post(
        "/planeacion-asignaciones",
        json={"action": "reasignarPunto", "punto_id": "p1", "nuevo_inspector_uid": "insp-new"},
    )

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["inspector_uid"] == "insp-new"
    assert stores[STICKER_MATCHES]["s1"]["clave_integracion"] == "PLN-1-ABC"
    assert stores[STICKER_MATCHES]["s1"]["planeacion_punto_id"] == "p1"
    assert "grupo_id" not in stores[STICKER_MATCHES]["s1"]


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


# ── `survey-sticker-sync` (2026-08-27): symmetric radius retract — clears
# grupo_id on the exact twin AND any radius-swept sibling in one sweep. ───


def test_desasignar_grupo_clears_both_exact_twin_and_radius_sibling(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "grupo_id": "g1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1",
               "grupo_id": "g1", "clave_integracion": "PLN-1-ABC", "planeacion_punto_id": "p1"},
        "s2": {"coords": {"lat": 3.4045, "lon": -76.50}, "direccion": "Otra calle sin relacion",
               "grupo_id": "g1"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/planeacion-asignaciones", json={"action": "desasignarGrupo", "puntos": ["p1"]})

    assert resp.status_code == 200
    assert resp.json()["stickers_desasignados"] == 2
    assert stores[STICKER_MATCHES]["s1"]["grupo_id"] is None
    assert stores[STICKER_MATCHES]["s2"]["grupo_id"] is None


def test_desasignar_grupo_radius_retract_failure_never_fails_the_survey_side_clear(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {
        "p1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "grupo_id": "g1"},
    }
    stores[STICKER_MATCHES] = {
        "s1": {"coords": {"lat": 3.40, "lon": -76.50}, "direccion": "Calle 1", "grupo_id": "g1"},
    }
    client = _admin_client(monkeypatch, stores)

    def _boom(*args, **kwargs):
        raise RuntimeError("radius math unavailable")

    monkeypatch.setattr(pa, "haversine_m", _boom)

    resp = client.post("/planeacion-asignaciones", json={"action": "desasignarGrupo", "puntos": ["p1"]})

    assert resp.status_code == 200
    assert stores[PLANEACION_PUNTOS]["p1"]["grupo_id"] is None
    assert resp.json()["stickers_desasignados"] == 0


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


# ── in-process snapshot: resumen/metricasProgreso/listPuntos perf fix ──────
# Stage 1 stamped `actualizado_en` on every write; stage 2 spends it:
# `PlaneacionPuntosSnapshot` pays one full projected read per process
# lifetime, then bounded delta queries on `actualizado_en`. `resumen`/
# `metricas_progreso` no longer have their own dispatch-level TTL cache —
# the snapshot IS the cost/freshness authority now (see that class's own
# docstring in planeacion_asignaciones.py).


def _capture_fake_query_where(monkeypatch) -> list[tuple[str, str, Any]]:
    """Instruments `_FakeQuery.where()` for this test only (monkeypatch
    auto-restores) so a snapshot delta refresh (`actualizado_en > cutoff`)
    can be asserted on directly, distinguishing it from a full rescan."""
    captured: list[tuple[str, str, Any]] = []
    original_where = _FakeQuery.where

    def _tracking_where(self, field, op, value):
        captured.append((field, op, value))
        return original_where(self, field, op, value)

    monkeypatch.setattr(_FakeQuery, "where", _tracking_where)
    return captured


def test_resumen_is_cached_across_consecutive_calls(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": _punto()}
    client, calls = _admin_client_with_calls(monkeypatch, stores)

    first = client.post("/planeacion-asignaciones", json={"action": "resumen"})
    second = client.post("/planeacion-asignaciones", json={"action": "resumen"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert calls[PLANEACION_PUNTOS] == 1


def test_metricas_progreso_is_cached_across_consecutive_calls(monkeypatch):
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": {"estado_asignacion": "pendiente"}}
    stores[STICKER_MATCHES] = {}
    stores[GRUPOS_INSPECTORES] = {}
    client, calls = _admin_client_with_calls(monkeypatch, stores)

    first = client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"})
    second = client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # planeacion_puntos comes from the snapshot (one full load, no reason to
    # refresh again seconds later) and sticker_matches from its own 5-min
    # TTL cache — both stay at 1. grupos_inspectores is intentionally NOT
    # cached (small collection, task decision) so it is read fresh every call.
    assert calls[PLANEACION_PUNTOS] == 1
    assert calls[STICKER_MATCHES] == 1
    assert calls[GRUPOS_INSPECTORES] == 2


def test_mutation_marks_snapshot_dirty_for_exactly_one_bounded_delta(monkeypatch):
    """crearGrupo (a mutation that doesn't even touch `planeacion_puntos`)
    still marks the snapshot dirty unconditionally, so the NEXT read pays
    exactly one MORE `planeacion_puntos` query — and that query is a bounded
    delta (`actualizado_en > cutoff`), never a second full rescan."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": _punto()}
    stores[STICKER_MATCHES] = {}
    stores[GRUPOS_INSPECTORES] = {}
    client, calls = _admin_client_with_calls(monkeypatch, stores)
    where_calls = _capture_fake_query_where(monkeypatch)

    client.post("/planeacion-asignaciones", json={"action": "resumen"})
    client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"})
    crear = client.post(
        "/planeacion-asignaciones",
        json={"action": "crearGrupo", "nombre": "Grupo X", "miembros": ["u1"]},
    )
    client.post("/planeacion-asignaciones", json={"action": "resumen"})
    client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"})

    assert crear.status_code == 201
    # Round 1: resumen triggers the snapshot's one full load (1 read).
    # metricasProgreso reuses the warm snapshot (0 more) + sticker_matches
    # (1) + grupos_inspectores (1, live). crearGrupo marks the snapshot
    # dirty. Round 2: resumen's snapshot.docs() sees dirty=True and runs
    # exactly ONE bounded delta query (+1 == 2 total) instead of a full
    # rescan; metricasProgreso then finds the snapshot already fresh again
    # (0 more). sticker_matches stays cached; grupos_inspectores is read
    # again (uncached).
    assert calls[PLANEACION_PUNTOS] == 2
    assert calls[STICKER_MATCHES] == 1
    assert calls[GRUPOS_INSPECTORES] == 2
    # Prove the second planeacion_puntos read was a bounded delta, not a
    # full rescan: an inequality filter on actualizado_en was applied.
    assert any(field == "actualizado_en" and op == ">" for field, op, _ in where_calls), (
        f"expected a delta query filtering on actualizado_en > cutoff, got {where_calls}"
    )


def test_resumen_cache_expires_after_ttl(monkeypatch):
    """Monkeypatches the named `pa.CACHE_TTL_SECONDS` constant to force the
    SNAPSHOT (not a dispatch-level cache — that no longer exists for
    resumen/metricasProgreso, stage 2) into treating itself as stale on
    every subsequent call, so each `resumen` call after the first pays one
    more (bounded delta) `planeacion_puntos` read. NOT `time.monotonic`
    itself — that stdlib function is shared process-wide with httpx/anyio's
    own transport-timeout clock, and faking it out from under `TestClient`
    hangs the request instead of the intended cache-miss behavior."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {"p1": _punto()}
    client, calls = _admin_client_with_calls(monkeypatch, stores)
    monkeypatch.setattr(pa, "CACHE_TTL_SECONDS", -1)

    client.post("/planeacion-asignaciones", json={"action": "resumen"})
    client.post("/planeacion-asignaciones", json={"action": "resumen"})

    assert calls[PLANEACION_PUNTOS] == 2


def test_snapshot_cold_start_reads_planeacion_puntos_exactly_once(monkeypatch):
    """Cold start: the very first read (whichever action hits it first)
    triggers exactly ONE full `planeacion_puntos` read; further reads of any
    action, within the TTL and with no mutation in between, add ZERO more."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {f"p{i}": _punto(prioridad_score=i) for i in range(5)}
    stores[STICKER_MATCHES] = {}
    stores[GRUPOS_INSPECTORES] = {}
    client, calls = _admin_client_with_calls(monkeypatch, stores)

    first = client.post("/planeacion-asignaciones", json={"action": "listPuntos"})
    assert first.status_code == 200
    assert calls[PLANEACION_PUNTOS] == 1

    client.post("/planeacion-asignaciones", json={"action": "resumen"})
    client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"})

    # metricasProgreso reads grupos_inspectores/sticker_matches too, but
    # neither is what this test is about — only planeacion_puntos matters.
    assert calls[PLANEACION_PUNTOS] == 1


def test_mutation_is_reflected_in_resumen_and_metricas_on_next_call(monkeypatch):
    """`marcarNoAplica` excludes a point from resumen's `pendientes` and
    from metricasProgreso's per-group tally on the VERY NEXT call — proof
    `snapshot.mark_dirty()` actually busts the snapshot; resumen/
    metricasProgreso have no TTL cache of their own left to hide behind."""
    stores = _stores()
    stores[GRUPOS_INSPECTORES] = {"g1": {"nombre": "Norte", "miembros": ["u1"], "activo": True}}
    stores[PLANEACION_PUNTOS] = {
        "p1": {**_punto(), "grupo_id": "g1"},
        "p2": {**_punto(), "grupo_id": "g1"},
    }
    stores[STICKER_MATCHES] = {}
    client = _admin_client(monkeypatch, stores)

    before = client.post("/planeacion-asignaciones", json={"action": "resumen"}).json()["resumen"]
    assert before["pendientes"] == 2

    marcar = client.post(
        "/planeacion-asignaciones",
        json={"action": "marcarNoAplica", "punto_id": "p1", "motivo_exclusion": "duplicado"},
    )
    assert marcar.status_code == 200

    after = client.post("/planeacion-asignaciones", json={"action": "resumen"}).json()["resumen"]
    assert after["pendientes"] == 1

    metricas = client.post("/planeacion-asignaciones", json={"action": "metricasProgreso"}).json()["metricas"]
    assert metricas["grupos"]["g1"]["survey"]["no_aplica"] == 1


def test_puntos_solicitados_delete_removes_mirror_without_a_full_rescan(monkeypatch):
    """Deleting a punto solicitado (a DIFFERENT router, same app/snapshot
    instance) removes its `planeacion_puntos` mirror via `snapshot.remove()`
    — a delta query can only ever find CHANGED docs, never GONE ones. The
    next planeacion read must not show the mirror, and must NOT pay a
    second full collection read to notice its absence (`remove()`, unlike
    `mark_dirty()`, does not force a refresh)."""
    stores = _stores()
    sid = "sid1"
    mirror_id = f"solicitado_{sid}"
    stores["puntos_solicitados"] = {sid: {"nombre": "Casa X", "estado_seguimiento": "pendiente"}}
    stores[PLANEACION_PUNTOS] = {
        mirror_id: {**_punto(), "fuente": "solicitado", "registro_id": sid, "es_solicitado": True},
    }
    client, calls = _admin_client_with_calls(monkeypatch, stores)

    warm = client.post("/planeacion-asignaciones", json={"action": "resumen"})
    assert warm.json()["resumen"]["total"] == 1
    assert calls[PLANEACION_PUNTOS] == 1

    delete_resp = client.delete(f"/puntos-solicitados/{sid}")
    assert delete_resp.status_code == 200

    after = client.post("/planeacion-asignaciones", json={"action": "resumen"})
    assert after.json()["resumen"]["total"] == 0
    assert calls[PLANEACION_PUNTOS] == 1, "expected the removal to be served from the in-memory snapshot, not a rescan"


def test_snapshot_sequential_reads_never_see_a_partial_set(monkeypatch):
    """No new concurrency harness (the suite has none): a plain sequential
    check that the full load always completes before `docs()` returns
    anything — a cold read never sees an empty/partial dict, and a repeat
    read still sees the complete seeded set."""
    stores = _stores()
    stores[PLANEACION_PUNTOS] = {f"p{i}": _punto(prioridad_score=i) for i in range(10)}
    client = _admin_client(monkeypatch, stores)

    first = client.post("/planeacion-asignaciones", json={"action": "resumen"}).json()["resumen"]
    assert first["total"] == 10

    second = client.post("/planeacion-asignaciones", json={"action": "resumen"}).json()["resumen"]
    assert second["total"] == 10


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
