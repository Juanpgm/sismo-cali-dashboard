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

@pytest.fixture(autouse=True)
def _no_blob_token(monkeypatch):
    """Hermetic guard (same as test_sticker_status.py's own fixture): a real
    BLOB_READ_WRITE_TOKEN in the dev environment must never let
    `PuntosSolicitadosCache`'s fire-and-forget Blob persistence attempt a
    real upload during tests (tests that exercise the Blob path set the env
    var and fake `blob_sync` themselves)."""
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)


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
    """GET is now TTL-cached (`PuntosSolicitadosCache`) — this test mutates
    the fake store directly (bypassing the router's own write endpoints, so
    `invalidate()` never fires), so the clock is advanced past the TTL
    before each re-read to force a fresh fetch, same convention
    `test_sticker_status.py`'s own TTL tests use."""
    import app.routers.puntos_solicitados as router_mod

    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    clock = {"t": 0.0}
    monkeypatch.setattr(router_mod.time, "monotonic", lambda: clock["t"])

    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]
    mirror_id = f"solicitado_{sid}"

    def _estado_seguimiento() -> str:
        listing = client.get("/puntos-solicitados").json()["puntos"]
        punto = next(p for p in listing if p["id"] == sid)
        return punto["estado_seguimiento"]

    assert _estado_seguimiento() == "pendiente"

    clock["t"] += router_mod.PUNTOS_SOLICITADOS_CACHE_TTL_SECONDS + 1
    stores[PLANEACION_PUNTOS][mirror_id]["estado_asignacion"] = "asignado"
    assert _estado_seguimiento() == "asignado"

    clock["t"] += router_mod.PUNTOS_SOLICITADOS_CACHE_TTL_SECONDS + 1
    stores[PLANEACION_PUNTOS][mirror_id]["estado_asignacion"] = "en_proceso"
    assert _estado_seguimiento() == "en_proceso"

    clock["t"] += router_mod.PUNTOS_SOLICITADOS_CACHE_TTL_SECONDS + 1
    stores[PLANEACION_PUNTOS][mirror_id]["estado_asignacion"] = "hecho"
    assert _estado_seguimiento() == "visitado"

    # No direct write to estado_seguimiento on the puntos_solicitados doc —
    # the stored seed value stays the offline-display fallback only.
    assert stores[PUNTOS_SOLICITADOS][sid]["estado_seguimiento"] == "pendiente"


# ── GET exposes inspector_uid/mirror_id read from the mirror ───────────────


def test_list_exposes_inspector_uid_and_mirror_id_from_mirror(monkeypatch):
    """Same TTL-cache clock-advance note as
    `test_estado_seguimiento_tracks_mirror_estado_asignacion_transitions`
    above — the direct store mutation bypasses `invalidate()`."""
    import app.routers.puntos_solicitados as router_mod

    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    clock = {"t": 0.0}
    monkeypatch.setattr(router_mod.time, "monotonic", lambda: clock["t"])

    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]
    mirror_id = f"solicitado_{sid}"

    def _punto() -> dict:
        listing = client.get("/puntos-solicitados").json()["puntos"]
        return next(p for p in listing if p["id"] == sid)

    # No assignment yet: inspector_uid is None, mirror_id is always present.
    punto = _punto()
    assert punto["inspector_uid"] is None
    assert punto["mirror_id"] == mirror_id

    clock["t"] += router_mod.PUNTOS_SOLICITADOS_CACHE_TTL_SECONDS + 1
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


# ── PuntosSolicitadosCache: TTL + serve-stale + Blob last-known-good ───────
# (Firestore-quota-outage fix, 31-ago-2026). Same battery of tests as
# `test_sticker_status.py`'s own `StickerStatusCache` coverage, exercised
# directly against the cache class (no Firestore/HTTP plumbing needed for
# the pure caching behavior) plus a few route-level tests for the parts that
# depend on the actual GET route (cache hit skips Firestore, writes bust it
# immediately, a live-plus-Blob double failure still 502s).


def test_cached_response_served_without_new_firestore_read(monkeypatch):
    stores = _stores()
    fail_flag: dict[str, bool] = {}
    client = _admin_client(monkeypatch, stores, fail_flag)
    client.post("/puntos-solicitados", json=VALID_BODY)

    first = client.get("/puntos-solicitados")
    fail_flag["fail_read"] = True  # a second Firestore read would now blow up
    second = client.get("/puntos-solicitados")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()  # served from cache, Firestore never touched again


def test_list_serves_stale_via_route_when_firestore_fails_after_a_success(monkeypatch):
    """30-ago-2026 serve-stale-on-error, at the route/HTTP level: once one
    GET has succeeded, a later Firestore 429 must degrade to the cached
    (stale) list — 200, not 502 — so the admin board always has SOMETHING
    to show."""
    import app.routers.puntos_solicitados as router_mod

    stores = _stores()
    fail_flag: dict[str, bool] = {}
    client = _admin_client(monkeypatch, stores, fail_flag)
    client.post("/puntos-solicitados", json=VALID_BODY)

    clock = {"t": 0.0}
    monkeypatch.setattr(router_mod.time, "monotonic", lambda: clock["t"])
    first = client.get("/puntos-solicitados")
    assert first.status_code == 200

    clock["t"] += router_mod.PUNTOS_SOLICITADOS_CACHE_TTL_SECONDS + 1  # force stale
    fail_flag["fail_read"] = True
    second = client.get("/puntos-solicitados")

    assert second.status_code == 200
    assert second.json() == first.json()  # stale but served, not a 502


def test_create_invalidates_the_list_cache_immediately(monkeypatch):
    """Cache-busting on write: an admin's own `POST /puntos-solicitados`
    must be visible on the VERY NEXT `GET`, not stuck behind the 60 s TTL —
    no clock advance in this test."""
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    before = client.get("/puntos-solicitados").json()["puntos"]
    assert before == []

    client.post("/puntos-solicitados", json=VALID_BODY)
    after = client.get("/puntos-solicitados").json()["puntos"]

    assert len(after) == 1


def test_edit_invalidates_the_list_cache_immediately(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]
    client.get("/puntos-solicitados")  # populate the cache with the pre-edit name

    client.patch(f"/puntos-solicitados/{sid}", json={"nombre": "Renombrada"})
    listing = client.get("/puntos-solicitados").json()["puntos"]

    assert next(p for p in listing if p["id"] == sid)["nombre"] == "Renombrada"


def test_delete_invalidates_the_list_cache_immediately(monkeypatch):
    stores = _stores()
    client = _admin_client(monkeypatch, stores)
    sid = client.post("/puntos-solicitados", json=VALID_BODY).json()["id"]
    client.get("/puntos-solicitados")  # populate the cache while the point still exists

    client.delete(f"/puntos-solicitados/{sid}")
    listing = client.get("/puntos-solicitados").json()["puntos"]

    assert listing == []


def test_get_or_fetch_serves_stale_payload_when_a_later_fetch_fails(monkeypatch):
    from app.routers import puntos_solicitados as mod

    cache = mod.PuntosSolicitadosCache()
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    good_payload = {"puntos": [{"id": "p1"}]}
    assert cache.get_or_fetch(lambda: good_payload) == good_payload

    clock["t"] += mod.PUNTOS_SOLICITADOS_CACHE_TTL_SECONDS + 1

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    assert cache.get_or_fetch(boom) == good_payload  # served stale, no exception


def test_get_or_fetch_still_raises_on_a_cold_cache_with_no_prior_success(monkeypatch):
    from app.routers import puntos_solicitados as mod

    monkeypatch.setattr(mod.blob_lkg, "load_json", lambda pathname, expected_type: None)
    cache = mod.PuntosSolicitadosCache()

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    with pytest.raises(RuntimeError, match="Quota exceeded"):
        cache.get_or_fetch(boom)


def test_get_or_fetch_cold_start_falls_back_to_blob_last_good(monkeypatch):
    from app.routers import puntos_solicitados as mod

    blob_payload = {"puntos": [{"id": "p1"}]}
    monkeypatch.setattr(mod.blob_lkg, "load_json", lambda pathname, expected_type: blob_payload)
    cache = mod.PuntosSolicitadosCache()

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    assert cache.get_or_fetch(boom) == blob_payload
    # Adopted in-process: served again within TTL without touching Blob.
    monkeypatch.setattr(mod.blob_lkg, "load_json", lambda pathname, expected_type: None)
    assert cache.get_or_fetch(boom) == blob_payload


def test_get_or_fetch_cold_start_rejects_malformed_blob_payload(monkeypatch):
    from pathlib import Path

    from app.routers import puntos_solicitados as mod

    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_STORE_secret")

    def fake_download(pathname, local, **kw):
        Path(local).write_text("{not valid json", encoding="utf-8")
        return True

    monkeypatch.setattr(mod.blob_lkg.blob_sync, "download", fake_download)
    cache = mod.PuntosSolicitadosCache()

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    with pytest.raises(RuntimeError, match="Quota exceeded"):
        cache.get_or_fetch(boom)


def test_get_or_fetch_persists_to_blob_only_when_payload_changed(monkeypatch):
    from app.routers import puntos_solicitados as mod

    cache = mod.PuntosSolicitadosCache()
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    saves: list[Any] = []
    monkeypatch.setattr(mod.blob_lkg, "save_json",
                        lambda pathname, payload: saves.append((pathname, payload)) or True)

    def _join():
        if cache._persist_thread is not None:
            cache._persist_thread.join()

    cache.get_or_fetch(lambda: {"puntos": [{"id": "p1"}]})
    _join()
    assert len(saves) == 1
    assert saves[0][0] == mod.PUNTOS_SOLICITADOS_LKG_BLOB

    clock["t"] += mod.PUNTOS_SOLICITADOS_CACHE_TTL_SECONDS + 1
    cache.get_or_fetch(lambda: {"puntos": [{"id": "p1"}]})  # unchanged
    _join()
    assert len(saves) == 1  # hash-gated: unchanged payload not re-uploaded

    clock["t"] += mod.PUNTOS_SOLICITADOS_CACHE_TTL_SECONDS + 1
    cache.get_or_fetch(lambda: {"puntos": [{"id": "p1"}, {"id": "p2"}]})
    _join()
    assert len(saves) == 2


def test_get_or_fetch_blob_write_failure_never_breaks_the_request(monkeypatch):
    from app.routers import puntos_solicitados as mod

    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_STORE_secret")

    def exploding_upload(local, pathname, *a, **kw):
        raise SystemExit("Blob upload 500 para data/puntos_solicitados_last_good.json")

    monkeypatch.setattr(mod.blob_lkg.blob_sync, "upload", exploding_upload)
    cache = mod.PuntosSolicitadosCache()

    payload = {"puntos": []}
    assert cache.get_or_fetch(lambda: payload) == payload
    if cache._persist_thread is not None:
        cache._persist_thread.join()
    assert cache._blob_hash is None  # failed upload never advances the hash


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


# ── GET /puntos-solicitados/buscar (puntos-solicitados-busqueda-asignacion
#    change, design.md ADR-1/ADR-2) ─────────────────────────────────────────
#
# Reads `reportes.json` via `load_reportes()` (imported, never re-literaled)
# joined to the private `puntos_contacto` collection on
# `id (str) == registro_id`. The joined-rows cache (`BuscarCache`) lives on
# `app.state`, one instance per `create_app()` call — same as
# `test_sticker_status.py`'s `StickerStatusCache` tests, each test's fresh
# `_admin_client`/`_viewer_client` call already gives natural per-test
# isolation, no reset fixture needed.


# ── 2.1: `_build_rows` join attaches name when present / None when missing ─


def test_build_rows_joins_and_attaches_name_or_none():
    import app.routers.puntos_solicitados as router_mod

    reportes = [
        {"id": "1", "direccion": "Calle 1", "barrio": "San Antonio",
         "comuna": "Comuna 3", "lat": 3.1, "lng": -76.1},
        {"id": "2", "direccion": "Calle 2", "barrio": "Otro",
         "comuna": "Comuna 5", "lat": 3.2, "lng": -76.2},
    ]
    contacto_by_id = {
        "1": {"registro_id": "1", "nombre_solicitante": "María Pérez",
              "telefono_solicitante": "3001234567"},
    }

    rows = router_mod._build_rows(reportes, contacto_by_id)
    assert len(rows) == 2

    matched = next(r for r in rows if r["registro_id"] == "1")
    assert matched["nombre_solicitante"] == "María Pérez"
    assert matched["telefono_solicitante"] == "3001234567"
    assert matched["direccion"] == "Calle 1"

    unmatched = next(r for r in rows if r["registro_id"] == "2")
    assert unmatched["nombre_solicitante"] is None
    assert unmatched["telefono_solicitante"] is None


# ── 2.2: case-insensitive substring filter over all 4 fields, top-20 cap ───


def test_filter_rows_case_insensitive_substring_over_four_fields_and_top20_cap():
    import app.routers.puntos_solicitados as router_mod

    rows = [
        {"registro_id": str(i), "direccion": f"Calle {i}", "barrio": "San Antonio",
         "comuna": "Comuna 3", "lat": 0, "lng": 0,
         "nombre_solicitante": None, "telefono_solicitante": None}
        for i in range(25)
    ]

    matched = router_mod._filter_rows(rows, "SAN ANTONIO")
    assert len(matched) == 20  # top-20 cap even though 25 rows match
    assert all("san antonio" in (r["barrio"] or "").lower() for r in matched)

    name_row = {"registro_id": "name-match", "direccion": "Otra dir", "barrio": "Otro",
                "comuna": "Otra comuna", "lat": 0, "lng": 0,
                "nombre_solicitante": "Pedro Gómez", "telefono_solicitante": "300"}
    matched_by_name = router_mod._filter_rows(rows + [name_row], "pedro")
    assert [r["registro_id"] for r in matched_by_name] == ["name-match"]

    assert router_mod._filter_rows(rows, "") == []
    assert router_mod._filter_rows(rows, "zzz-no-match-anywhere") == []


# ── 2.3: TTL cache builds once, serves cached within TTL, rebuilds after ───


def test_joined_rows_ttl_cache_builds_once_serves_cached_then_rebuilds_after_ttl(monkeypatch):
    import app.routers.puntos_solicitados as router_mod

    calls: list[int] = []

    def _counting_load_reportes():
        calls.append(1)
        return [{"id": "1", "direccion": "Calle 1", "barrio": "B",
                  "comuna": "C", "lat": 1.0, "lng": 2.0}]

    monkeypatch.setattr(router_mod, "load_reportes", _counting_load_reportes)
    stores = _stores()
    monkeypatch.setattr(router_mod.credentials, "sismo", lambda: _FakeSismoClients(stores, {}))

    clock = {"t": 0.0}
    monkeypatch.setattr(router_mod.time, "monotonic", lambda: clock["t"])

    cache = router_mod.BuscarCache()
    router_mod._joined_rows(cache)
    assert len(calls) == 1

    clock["t"] = router_mod._BUSCAR_TTL_S - 1  # still within TTL
    router_mod._joined_rows(cache)
    assert len(calls) == 1  # served from cache, no rebuild

    clock["t"] = router_mod._BUSCAR_TTL_S + 1  # past TTL
    router_mod._joined_rows(cache)
    assert len(calls) == 2  # rebuilt


# ── 2.4: non-admin `GET /buscar` → 403, zero source reads ──────────────────


def test_buscar_non_admin_is_403_with_zero_source_reads(monkeypatch):
    import app.routers.puntos_solicitados as router_mod

    calls: list[int] = []
    monkeypatch.setattr(router_mod, "load_reportes", lambda: calls.append(1) or [])
    stores = _stores()
    client = _viewer_client(monkeypatch, stores)

    resp = client.get("/puntos-solicitados/buscar", params={"q": "san antonio"})
    assert resp.status_code == 403
    assert calls == []


# ── 2.5: admin response never leaks puntos_contacto/raw-reportes PII fields ─
#    when unmatched; the response only ever carries None for those fields,
#    never a stray raw-reportes key even if the record were malformed/
#    unstripped — proving PII cannot cross into the response by construction
#    (the property "Public artifacts remain PII-free" is meant to protect).


def test_buscar_response_never_leaks_raw_pii_fields_and_nulls_when_unmatched(monkeypatch):
    import app.routers.puntos_solicitados as router_mod

    reportes = [
        {
            "id": "9",
            "direccion": "Calle 9",
            "barrio": "San Antonio",
            "comuna": "Comuna 3",
            "lat": 3.1,
            "lng": -76.1,
            # Simulates a malformed/unstripped record — _build_rows must
            # never forward these raw fields into the response even if
            # present on the input, regardless of contacto match.
            "nombre": "NO DEBERIA APARECER",
            "telefono": "000",
        },
    ]
    monkeypatch.setattr(router_mod, "load_reportes", lambda: reportes)
    stores = _stores()  # puntos_contacto stays empty: no match for id "9"
    client = _admin_client(monkeypatch, stores)

    resp = client.get("/puntos-solicitados/buscar", params={"q": "san antonio"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["resultados"]) == 1
    row = body["resultados"][0]
    assert row["nombre_solicitante"] is None
    assert row["telefono_solicitante"] is None
    assert "nombre" not in row
    assert "telefono" not in row


# ── empty/whitespace q → {ok:true, resultados:[]}, no source read (ADR-1) ──


def test_buscar_empty_or_whitespace_q_returns_empty_resultados_no_source_read(monkeypatch):
    import app.routers.puntos_solicitados as router_mod

    calls: list[int] = []
    monkeypatch.setattr(router_mod, "load_reportes", lambda: calls.append(1) or [])
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.get("/puntos-solicitados/buscar", params={"q": "   "})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "resultados": []}
    assert calls == []


# ── clean 502 on a load_reportes() failure (primary source), same
#    convention as sibling routes ───────────────────────────────────────────


def test_buscar_source_failure_is_a_clean_502(monkeypatch):
    import app.routers.puntos_solicitados as router_mod

    def _raise():
        raise RuntimeError("simulated reportes read failure")

    monkeypatch.setattr(router_mod, "load_reportes", _raise)
    stores = _stores()
    client = _admin_client(monkeypatch, stores)

    resp = client.get("/puntos-solicitados/buscar", params={"q": "san antonio"})
    assert resp.status_code == 502


# ── a puntos_contacto-only failure degrades to address-only rows, NOT a 502 ─
#    (Fix 3: load_reportes() and the puntos_contacto read are independent
#    failure domains — only load_reportes() failing is fatal).


def test_buscar_contacto_read_failure_degrades_to_address_only_rows_not_502(monkeypatch):
    import app.routers.puntos_solicitados as router_mod

    reportes = [
        {"id": "9", "direccion": "Calle 9", "barrio": "San Antonio",
         "comuna": "Comuna 3", "lat": 3.1, "lng": -76.1},
    ]
    monkeypatch.setattr(router_mod, "load_reportes", lambda: reportes)
    stores = _stores()
    client = _admin_client(monkeypatch, stores, fail_flag={"fail_read": True})

    resp = client.get("/puntos-solicitados/buscar", params={"q": "san antonio"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["resultados"]) == 1
    row = body["resultados"][0]
    assert row["direccion"] == "Calle 9"
    assert row["nombre_solicitante"] is None
    assert row["telefono_solicitante"] is None


# ── happy-path: only the current page's rows get contact-enriched, via one
#    batched get_all (not a full puntos_contacto scan) ─────────────────────


def test_buscar_enriches_only_page_rows_with_contact_via_get_all(monkeypatch):
    import app.routers.puntos_solicitados as router_mod

    reportes = [
        {"id": "9", "direccion": "Calle 9 San Antonio", "barrio": "San Antonio",
         "comuna": "Comuna 3", "lat": 3.1, "lng": -76.1},
    ]
    monkeypatch.setattr(router_mod, "load_reportes", lambda: reportes)
    stores = _stores()
    stores["puntos_contacto"] = {
        "atencionsismo_9": {"nombre_solicitante": "María Pérez", "telefono_solicitante": "3001234567"},
    }
    client = _admin_client(monkeypatch, stores)

    resp = client.get("/puntos-solicitados/buscar", params={"q": "san antonio"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["resultados"]) == 1
    row = body["resultados"][0]
    assert row["nombre_solicitante"] == "María Pérez"
    assert row["telefono_solicitante"] == "3001234567"


# ── 2.6: duplicate `id` in reportes.json collapses to one row, not two ─────


def test_build_rows_dedupes_duplicate_id_keeping_first_occurrence():
    import app.routers.puntos_solicitados as router_mod

    reportes = [
        {"id": "1", "direccion": "Calle 1 primera", "barrio": "San Antonio",
         "comuna": "Comuna 3", "lat": 3.1, "lng": -76.1},
        {"id": "1", "direccion": "Calle 1 duplicada", "barrio": "Otro",
         "comuna": "Comuna 5", "lat": 3.2, "lng": -76.2},
    ]

    rows = router_mod._build_rows(reportes, {})
    assert len(rows) == 1
    assert rows[0]["direccion"] == "Calle 1 primera"
