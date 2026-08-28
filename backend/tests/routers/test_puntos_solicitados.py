"""POST/GET/PATCH/DELETE /puntos-solicitados, POST /geocode (task 3.1-3.9) —
design.md ADR-1 through ADR-6 of the `puntos-solicitados` change; spec
puntos-solicitados/"Admin-only creation with required-field validation",
"Atomic dual-write to puntos_solicitados and the planeacion_puntos mirror",
"Live geocoding with manual fallback", "estado_seguimiento tracks the
mirror's assignment lifecycle".

Fake in-memory Firestore double, same shape/convention as
`tests/routers/test_planeacion_asignaciones.py`'s own fake (path-keyed by
(collection, id), `.where()`/`.order_by()`/`.limit()`/`batch()`/`get_all()`/
auto-id `.document()`), extended with an injectable batch-commit failure
(`_FakeBatch.commit()` can be made to raise BEFORE applying any op — proves
ADR-1's "neither document exists on failure" by construction, mirroring how
a real Firestore `WriteBatch` behaves) AND an injectable read failure
(`fail_flag["fail_read"]` makes `_FakeCollection.get()`/`_FakeFirestore.
get_all()` raise, for GET's Firestore-hiccup coverage).
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app

PUNTOS_SOLICITADOS = "puntos_solicitados"
PLANEACION_PUNTOS = "planeacion_puntos"

UID_ADMIN = "uid-admin"
FAKE_CLAIMS_ADMIN = {"sub": UID_ADMIN, "email": "admin@example.com", "role": "admin"}
FAKE_CLAIMS_VIEWER = {"sub": "uid-viewer", "email": "viewer@example.com"}

VALID_BODY: dict[str, Any] = {
    "nombre": "Casa esquinera",
    "comuna_corregimiento": "Comuna 3",
    "barrio_vereda": "San Antonio",
    "nombre_solicitante": "María Pérez",
    "telefono_solicitante": "3001234567",
    "justificacion": "Grieta visible reportada por el vecino",
    "lat": 3.45,
    "lng": -76.53,
    "direccion": "Calle 5 # 10-20",
    "fotos": [],
}


# ── Fake Firestore (same shape as test_planeacion_asignaciones.py's own) ───


class _FakeSnapshot:
    def __init__(self, collection: str, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self, store: dict[str, dict[str, Any]], collection: str, doc_id: str) -> None:
        self._store = store
        self._collection = collection
        self.id = doc_id

    def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self._collection, self.id, self._store.get(self.id))

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        current = dict(self._store.get(self.id, {})) if merge else {}
        current.update(data)
        self._store[self.id] = current

    def delete(self) -> None:
        self._store.pop(self.id, None)


class _FakeCollection:
    def __init__(self, collection: str, store: dict[str, dict[str, Any]], fail_flag: dict[str, bool]) -> None:
        self._collection = collection
        self._store = store
        self._auto_seq = 0
        self._fail_flag = fail_flag

    def document(self, doc_id: str | None = None) -> _FakeDocRef:
        if doc_id is None:
            self._auto_seq += 1
            doc_id = f"auto-{self._collection}-{self._auto_seq}"
        return _FakeDocRef(self._store, self._collection, doc_id)

    def get(self) -> list[_FakeSnapshot]:
        if self._fail_flag.get("fail_read"):
            raise RuntimeError("simulated Firestore read failure")
        return [_FakeSnapshot(self._collection, doc_id, data) for doc_id, data in self._store.items()]


class _FakeBatch:
    """`fail_on_commit`: when set, `commit()` raises immediately, BEFORE
    applying any queued op — proves ADR-1's "neither document exists after
    a failed batch" the same way a real Firestore WriteBatch guarantees it
    (all-or-nothing, never partially applied)."""

    def __init__(self, fail_flag: dict[str, bool]) -> None:
        self._ops: list[tuple[str, _FakeDocRef, dict[str, Any] | None, bool]] = []
        self._fail_flag = fail_flag

    def set(self, ref: _FakeDocRef, data: dict[str, Any], merge: bool = False) -> None:
        self._ops.append(("set", ref, data, merge))

    def delete(self, ref: _FakeDocRef) -> None:
        self._ops.append(("delete", ref, None, False))

    def commit(self) -> None:
        if self._fail_flag.get("fail"):
            raise RuntimeError("simulated batch-commit failure")
        for kind, ref, data, merge in self._ops:
            if kind == "set":
                ref.set(data, merge=merge)
            else:
                ref.delete()
        self._ops = []


class _FakeFirestore:
    def __init__(self, stores: dict[str, dict[str, dict[str, Any]]], fail_flag: dict[str, bool]) -> None:
        self._stores = stores
        self._fail_flag = fail_flag

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(name, self._stores.setdefault(name, {}), self._fail_flag)

    def batch(self) -> _FakeBatch:
        return _FakeBatch(self._fail_flag)

    def get_all(self, refs: list[_FakeDocRef]) -> list[_FakeSnapshot]:
        if self._fail_flag.get("fail_read"):
            raise RuntimeError("simulated Firestore read failure")
        return [ref.get() for ref in refs]


class _FakeSismoClients:
    def __init__(self, stores: dict[str, dict[str, dict[str, Any]]], fail_flag: dict[str, bool]) -> None:
        self.firestore = _FakeFirestore(stores, fail_flag)
        self.app = object()


def _stores() -> dict[str, dict[str, dict[str, Any]]]:
    return {PUNTOS_SOLICITADOS: {}, PLANEACION_PUNTOS: {}}


def _app(monkeypatch, stores: dict[str, dict[str, dict[str, Any]]], fail_flag: dict[str, bool] | None = None) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    monkeypatch.setenv("SURVEY123_FORM_URL", "https://survey123.arcgis.com/share/abc123")
    monkeypatch.setenv("SURVEY123_FIELD_APP_ITEM_ID", "itemid123")
    credentials.s3.cache_clear()
    monkeypatch.setattr(credentials, "sismo", lambda: _FakeSismoClients(stores, fail_flag or {}))
    return create_app()


def _admin_client(monkeypatch, stores, fail_flag: dict[str, bool] | None = None) -> TestClient:
    app = _app(monkeypatch, stores, fail_flag)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN
    return TestClient(app)


def _viewer_client(monkeypatch, stores) -> TestClient:
    app = _app(monkeypatch, stores)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER
    return TestClient(app)


# ── Successful create writes both documents (3.1) ──────────────────────────


def test_successful_create_writes_both_documents_with_minted_codigoapp(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.post("/puntos-solicitados", json=VALID_BODY)
    assert resp.status_code == 201
    body = resp.json()
    assert body["ok"] is True
    sid = body["id"]
    clave = body["clave_integracion"]
    assert clave.startswith("PLN-")

    assert sid in stores[PUNTOS_SOLICITADOS]
    solicitado = stores[PUNTOS_SOLICITADOS][sid]
    assert solicitado["justificacion"] == VALID_BODY["justificacion"]
    assert solicitado["clave_integracion"] == clave

    mirror_id = f"solicitado_{sid}"
    assert mirror_id in stores[PLANEACION_PUNTOS]
    mirror = stores[PLANEACION_PUNTOS][mirror_id]
    assert mirror["fuente"] == "solicitado"
    assert mirror["registro_id"] == sid
    assert mirror["es_solicitado"] is True
    assert mirror["clave_integracion"] == clave
    assert mirror["estado_asignacion"] == "pendiente"
    assert mirror["coords"] == {"lat": 3.45, "lon": -76.53}


# ── Simulated write failure leaves no orphan (3.2) ──────────────────────────


def test_simulated_batch_failure_leaves_neither_document(monkeypatch):
    stores = _stores()
    fail_flag = {"fail": True}
    client = _admin_client(monkeypatch, stores, fail_flag)

    resp = client.post("/puntos-solicitados", json=VALID_BODY)
    assert resp.status_code == 502
    assert stores[PUNTOS_SOLICITADOS] == {}
    assert stores[PLANEACION_PUNTOS] == {}


# ── Missing required field is rejected, zero writes (3.3) ──────────────────


@pytest.mark.parametrize(
    "missing_field",
    ["nombre", "comuna_corregimiento", "barrio_vereda", "nombre_solicitante",
     "telefono_solicitante", "justificacion", "lat", "lng"],
)
def test_missing_required_field_is_rejected_with_zero_writes(monkeypatch, missing_field):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    body = {k: v for k, v in VALID_BODY.items() if k != missing_field}

    resp = client.post("/puntos-solicitados", json=body)
    assert resp.status_code == 422
    assert stores[PUNTOS_SOLICITADOS] == {}
    assert stores[PLANEACION_PUNTOS] == {}


# ── Blank (present but empty/whitespace) required field is rejected, zero
#    writes — the disabled-combobox/all-spaces gap FIX 1 closes ────────────


@pytest.mark.parametrize(
    "blank_field",
    ["nombre", "comuna_corregimiento", "barrio_vereda", "nombre_solicitante",
     "telefono_solicitante", "justificacion"],
)
@pytest.mark.parametrize("blank_value", ["", "   "])
def test_blank_required_field_is_rejected_with_zero_writes(monkeypatch, blank_field, blank_value):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    body = {**VALID_BODY, blank_field: blank_value}

    resp = client.post("/puntos-solicitados", json=body)
    assert resp.status_code == 422
    assert stores[PUNTOS_SOLICITADOS] == {}
    assert stores[PLANEACION_PUNTOS] == {}


# ── All required fields, zero photos accepted (3.4) ─────────────────────────


def test_all_required_fields_zero_photos_is_accepted(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    body = {**VALID_BODY, "fotos": []}

    resp = client.post("/puntos-solicitados", json=body)
    assert resp.status_code == 201


# ── Non-admin create/edit/delete rejected, zero writes (3.5) ───────────────


def test_non_admin_create_is_rejected_with_zero_writes(monkeypatch):
    stores = _stores()
    client = _viewer_client(monkeypatch, stores)

    resp = client.post("/puntos-solicitados", json=VALID_BODY)
    assert resp.status_code == 403
    assert stores[PUNTOS_SOLICITADOS] == {}
    assert stores[PLANEACION_PUNTOS] == {}


def test_non_admin_edit_is_rejected(monkeypatch):
    stores = _stores()
    stores[PUNTOS_SOLICITADOS]["p1"] = {"nombre": "Original"}
    client = _viewer_client(monkeypatch, stores)

    resp = client.patch("/puntos-solicitados/p1", json={"nombre": "Cambiado"})
    assert resp.status_code == 403
    assert stores[PUNTOS_SOLICITADOS]["p1"]["nombre"] == "Original"


def test_non_admin_delete_is_rejected(monkeypatch):
    stores = _stores()
    stores[PUNTOS_SOLICITADOS]["p1"] = {"nombre": "Original"}
    client = _viewer_client(monkeypatch, stores)

    resp = client.delete("/puntos-solicitados/p1")
    assert resp.status_code == 403
    assert "p1" in stores[PUNTOS_SOLICITADOS]


# ── estado_seguimiento derives from the mirror's estado_asignacion (3.6) ───


def test_estado_seguimiento_tracks_mirror_estado_asignacion_transitions(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]
    mirror_id = f"solicitado_{sid}"

    def _estado_seguimiento() -> str:
        listing = client.get("/puntos-solicitados").json()["puntos"]
        punto = next(p for p in listing if p["id"] == sid)
        return punto["estado_seguimiento"]

    assert _estado_seguimiento() == "pendiente"

    stores[PLANEACION_PUNTOS][mirror_id]["estado_asignacion"] = "asignado"
    assert _estado_seguimiento() == "asignado"

    stores[PLANEACION_PUNTOS][mirror_id]["estado_asignacion"] = "en_proceso"
    assert _estado_seguimiento() == "en_proceso"

    stores[PLANEACION_PUNTOS][mirror_id]["estado_asignacion"] = "hecho"
    assert _estado_seguimiento() == "visitado"

    # No direct write to estado_seguimiento on the puntos_solicitados doc —
    # the stored seed value stays the offline-display fallback only.
    assert stores[PUNTOS_SOLICITADOS][sid]["estado_seguimiento"] == "pendiente"


# ── GET exposes inspector_uid/mirror_id read from the mirror ───────────────


def test_list_exposes_inspector_uid_and_mirror_id_from_mirror(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]
    mirror_id = f"solicitado_{sid}"

    def _punto() -> dict:
        listing = client.get("/puntos-solicitados").json()["puntos"]
        return next(p for p in listing if p["id"] == sid)

    # No assignment yet: inspector_uid is None, mirror_id is always present.
    punto = _punto()
    assert punto["inspector_uid"] is None
    assert punto["mirror_id"] == mirror_id

    stores[PLANEACION_PUNTOS][mirror_id]["inspector_uid"] = "uid-inspector-1"
    punto = _punto()
    assert punto["inspector_uid"] == "uid-inspector-1"
    assert punto["mirror_id"] == mirror_id


# ── Manual coordinate entry without calling /geocode (3.7) ─────────────────


def test_manual_lat_lng_submit_creates_point_with_those_coordinates(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    body = {**VALID_BODY, "lat": 3.401234, "lng": -76.512345, "direccion": ""}

    resp = client.post("/puntos-solicitados", json=body)
    assert resp.status_code == 201
    sid = resp.json()["id"]
    assert stores[PUNTOS_SOLICITADOS][sid]["coords"] == {"lat": 3.401234, "lon": -76.512345}
    assert stores[PLANEACION_PUNTOS][f"solicitado_{sid}"]["coords"] == {"lat": 3.401234, "lon": -76.512345}


# ── PATCH re-syncs mirrored display fields, never solicitado-only/lifecycle ─


def test_patch_mirrored_field_updates_the_mirror(monkeypatch):
    """A mirrored field (ADR-2: nombre/direccion/barrio/comuna/coords) must
    reach `planeacion_puntos/solicitado_{id}` too, atomically."""
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]

    resp = client.patch(f"/puntos-solicitados/{sid}", json={
        "nombre": "Casa renombrada",
        "lat": 3.999,
        "lng": -76.111,
    })
    assert resp.status_code == 200
    assert stores[PUNTOS_SOLICITADOS][sid]["nombre"] == "Casa renombrada"
    assert stores[PUNTOS_SOLICITADOS][sid]["coords"] == {"lat": 3.999, "lon": -76.111}

    mirror = stores[PLANEACION_PUNTOS][f"solicitado_{sid}"]
    assert mirror["nombre"] == "Casa renombrada"
    assert mirror["coords"] == {"lat": 3.999, "lon": -76.111}


def test_patch_edits_request_fields_never_touches_mirror_or_lifecycle(monkeypatch):
    """`justificacion` is solicitado-only (ADR-2) — it must never reach the
    mirror; lifecycle fields on the mirror stay untouched by PATCH (ADR-4)."""
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]
    mirror_before = dict(stores[PLANEACION_PUNTOS][f"solicitado_{sid}"])

    resp = client.patch(f"/puntos-solicitados/{sid}", json={"justificacion": "Actualizada"})
    assert resp.status_code == 200
    assert stores[PUNTOS_SOLICITADOS][sid]["justificacion"] == "Actualizada"
    assert "justificacion" not in stores[PLANEACION_PUNTOS][f"solicitado_{sid}"]
    assert stores[PLANEACION_PUNTOS][f"solicitado_{sid}"] == mirror_before
    assert stores[PLANEACION_PUNTOS][f"solicitado_{sid}"]["estado_asignacion"] == "pendiente"
    assert stores[PLANEACION_PUNTOS][f"solicitado_{sid}"]["cuadrilla_id"] is None


def test_patch_unknown_id_is_404(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    resp = client.patch("/puntos-solicitados/does-not-exist", json={"justificacion": "x"})
    assert resp.status_code == 404


def test_patch_with_more_than_max_fotos_is_rejected(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]

    resp = client.patch(f"/puntos-solicitados/{sid}", json={"fotos": [f"f{i}" for i in range(11)]})
    assert resp.status_code == 400
    assert stores[PUNTOS_SOLICITADOS][sid]["fotos"] == []


def test_patch_with_exactly_max_fotos_is_accepted(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]

    resp = client.patch(f"/puntos-solicitados/{sid}", json={"fotos": [f"f{i}" for i in range(10)]})
    assert resp.status_code == 200
    assert len(stores[PUNTOS_SOLICITADOS][sid]["fotos"]) == 10


def test_patch_firestore_failure_is_a_clean_502(monkeypatch):
    stores = _stores()
    fail_flag: dict[str, bool] = {}
    client = _admin_client(monkeypatch, stores, fail_flag)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]

    fail_flag["fail"] = True
    resp = client.patch(f"/puntos-solicitados/{sid}", json={"nombre": "x"})
    assert resp.status_code == 502
    assert stores[PUNTOS_SOLICITADOS][sid]["nombre"] != "x"


# ── DELETE removes both the request doc and its mirror ─────────────────────


def test_delete_removes_both_documents(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]

    resp = client.delete(f"/puntos-solicitados/{sid}")
    assert resp.status_code == 200
    assert sid not in stores[PUNTOS_SOLICITADOS]
    assert f"solicitado_{sid}" not in stores[PLANEACION_PUNTOS]


def test_delete_unknown_id_is_404(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    resp = client.delete("/puntos-solicitados/does-not-exist")
    assert resp.status_code == 404


def test_delete_firestore_failure_is_a_clean_502(monkeypatch):
    stores = _stores()
    fail_flag: dict[str, bool] = {}
    client = _admin_client(monkeypatch, stores, fail_flag)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]

    fail_flag["fail"] = True
    resp = client.delete(f"/puntos-solicitados/{sid}")
    assert resp.status_code == 502
    assert sid in stores[PUNTOS_SOLICITADOS]


# ── GET Firestore failure is a clean 502 ────────────────────────────────────


def test_list_firestore_read_failure_is_a_clean_502(monkeypatch):
    stores = _stores()
    fail_flag: dict[str, bool] = {}
    client = _admin_client(monkeypatch, stores, fail_flag)
    client.post("/puntos-solicitados", json=VALID_BODY)

    fail_flag["fail_read"] = True
    resp = client.get("/puntos-solicitados")
    assert resp.status_code == 502


# ── POST /geocode: any authenticated caller (Nominatim, no API key) ────────


def test_geocode_route_requires_authentication_not_admin(monkeypatch):
    stores = _stores()

    def _fake_geocode(direccion, **kwargs):
        return {"ok": True, "accepted": True, "lat": 3.42, "lng": -76.53,
                "formatted": "x", "location_type": "yes"}

    import app.routers.puntos_solicitados as router_mod
    monkeypatch.setattr(router_mod, "geocode_service", _fake_geocode)

    client = _viewer_client(monkeypatch, stores)  # authenticated, non-admin
    resp = client.post("/geocode", json={"direccion": "Calle 1 # 2-3"})
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True


def test_geocode_route_maps_real_transport_failure_to_502(monkeypatch):
    """Full path through the REAL geocode_service (not a mocked
    GeocodeTransportError) — a `requests.get` connection failure must come
    back as a clean 502."""
    import requests

    stores = _stores()

    def _raise_connection_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", _raise_connection_error)

    client = _viewer_client(monkeypatch, stores)
    resp = client.post("/geocode", json={"direccion": "Calle 1 # 2-3"})
    assert resp.status_code == 502


def test_geocode_route_maps_transport_error_to_502(monkeypatch):
    """Timeout/connection-error/malformed-response failures inside
    `geocode()` must also come back as a clean 502, not an unhandled 500."""
    stores = _stores()

    def _raise_transport_error(direccion, **kwargs):
        from app.services.geocode import GeocodeTransportError
        raise GeocodeTransportError("Geocoding API request failed: timeout")

    import app.routers.puntos_solicitados as router_mod
    monkeypatch.setattr(router_mod, "geocode_service", _raise_transport_error)

    client = _viewer_client(monkeypatch, stores)
    resp = client.post("/geocode", json={"direccion": "Calle 1 # 2-3"})
    assert resp.status_code == 502
