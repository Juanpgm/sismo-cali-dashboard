"""POST /inspector-asignaciones (RED first) — design.md ADR-3/ADR-9;
backend-platform spec "Own-uid-scoped route rejects cross-uid access",
"sticker_matches And cuadrillas Sole-Writer Invariant"; field-form-session
spec "Inspector Own-UID Scoping Preserved" ("Cross-inspector access still
rejected after migration", "Own-uid access still succeeds after
migration").

Ports `api/inspector-asignaciones.js`'s misPuntos/marcarHecho dispatch.
Unlike sticker-asignaciones (admin-only, not yet ported), this endpoint is
ANY-authenticated (`Depends(require_auth)`) but scopes every sticker_matches
read/write to the caller's OWN uid (`inspector_uid == token.sub`) — no
Firestore rules back this collection, so this scoping is the only thing
standing between one inspector's data and another's.

`planeacion-asignaciones` follow-up batch (2026-08-26): the same router
gains `misPuntosPlaneacion`/`marcarHechoPlaneacion`, the inspector-facing
counterpart to the admin-only `planeacion_puntos` collection
(`routers/planeacion_asignaciones.py`). Same own-uid-scoping discipline,
same collection, different literal — see the new tests below.

Uses a call-count-instrumented fake `credentials.sismo()` override (no real
service-account JSON, no network), same convention `test_sticker_status.py`
established. The fake Firestore is a tiny in-memory dict keyed by doc id,
supporting `.collection(name).where(field, op, value).get()` and
`.collection(name).document(id).get()/.set(data, merge=True)` — the two
Firestore call shapes `inspector_asignaciones.py` needs. It now backs TWO
independent stores (`sticker_matches` and `planeacion_puntos`) keyed by
collection name, so a query against one can never see the other's docs.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app

UID_A = "uid-inspector-a"
UID_B = "uid-inspector-b"
FAKE_CLAIMS_A = {"sub": UID_A, "email": "a@sismocali.gov.co"}


class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store = store
        self._id = doc_id

    def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self._id, self._store.get(self._id))

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        current = self._store.get(self._id, {}) if merge else {}
        current = dict(current)
        current.update(data)
        self._store[self._id] = current


class _FakeQuery:
    def __init__(self, docs: list[_FakeSnapshot]) -> None:
        self._docs = docs

    def get(self) -> list[_FakeSnapshot]:
        return list(self._docs)


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        assert op == "=="
        matched = [
            _FakeSnapshot(doc_id, data)
            for doc_id, data in self._store.items()
            if data.get(field) == value
        ]
        return _FakeQuery(matched)

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id)


class _FakeFirestore:
    """Backs TWO independent collections by name so a query against one can
    never see the other's docs — `sticker_matches` (misPuntos/marcarHecho)
    and `planeacion_puntos` (misPuntosPlaneacion/marcarHechoPlaneacion,
    `planeacion-asignaciones` follow-up batch)."""

    def __init__(
        self,
        sticker_store: dict[str, dict[str, Any]],
        planeacion_store: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._stores = {
            "sticker_matches": sticker_store,
            "planeacion_puntos": planeacion_store if planeacion_store is not None else {},
        }

    def collection(self, name: str) -> _FakeCollection:
        assert name in self._stores, f"unexpected collection: {name}"
        return _FakeCollection(self._stores[name])


class _FakeSismoClients:
    def __init__(
        self,
        sticker_store: dict[str, dict[str, Any]],
        planeacion_store: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.firestore = _FakeFirestore(sticker_store, planeacion_store)
        self.app = None


def _store() -> dict[str, dict[str, Any]]:
    return {
        "point-a": {
            "inspector_uid": UID_A,
            "estado_asignacion": "pendiente",
            "direccion": "Calle 1 #2-3",
            "zona_id": "1",
        },
        "point-b": {
            "inspector_uid": UID_B,
            "estado_asignacion": "pendiente",
            "direccion": "Calle 9 #8-7",
        },
        "point-a-done": {
            "inspector_uid": UID_A,
            "estado_asignacion": "hecho",
        },
    }


def _planeacion_store() -> dict[str, dict[str, Any]]:
    return {
        "pln-a": {
            "inspector_uid": UID_A,
            "estado_asignacion": "pendiente",
            "clave_integracion": "PLN-AAAAAA-11111111",
            "direccion": "Calle 1 #2-3",
            "coords": {"lat": 3.4, "lon": -76.5},
            "comuna": "1",
            "afectacion": "DAÑO ESTRUCTURAL",
            "prioridad": "alta",
        },
        "pln-b": {
            "inspector_uid": UID_B,
            "estado_asignacion": "pendiente",
            "clave_integracion": "PLN-BBBBBB-22222222",
        },
        "pln-a-hecho": {
            "inspector_uid": UID_A,
            "estado_asignacion": "hecho",
            "clave_integracion": "PLN-CCCCCC-33333333",
        },
        "pln-a-no-aplica": {
            "inspector_uid": UID_A,
            "estado_asignacion": "no_aplica",
            "clave_integracion": "PLN-DDDDDD-44444444",
        },
    }


def _app(
    monkeypatch,
    store: dict[str, dict[str, Any]],
    planeacion_store: dict[str, dict[str, Any]] | None = None,
) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    monkeypatch.setenv("SURVEY123_FORM_URL", "https://survey123.arcgis.com/share/abc123")
    credentials.s3.cache_clear()
    monkeypatch.setattr(
        credentials, "sismo", lambda: _FakeSismoClients(store, planeacion_store)
    )
    return create_app()


def _client(monkeypatch, store: dict[str, dict[str, Any]]) -> TestClient:
    return TestClient(_app(monkeypatch, store))


def _authed_client(
    monkeypatch,
    store: dict[str, dict[str, Any]],
    claims: dict[str, Any],
    planeacion_store: dict[str, dict[str, Any]] | None = None,
) -> TestClient:
    app = _app(monkeypatch, store, planeacion_store)
    app.dependency_overrides[current_claims] = lambda: claims
    return TestClient(app)


def test_unauthenticated_is_rejected(monkeypatch):
    store = _store()
    client = _client(monkeypatch, store)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntos"})

    assert resp.status_code == 401


def test_mis_puntos_returns_only_own_pending_points(monkeypatch):
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntos"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    ids = {p["id"] for p in body["puntos"]}
    # Only inspector A's PENDING point — not B's (cross-uid) and not A's
    # already-`hecho` point (`pendiente()` filter, ported verbatim).
    assert ids == {"point-a"}


def test_cross_inspector_marcar_hecho_is_rejected_no_write(monkeypatch):
    """field-form-session 'Cross-inspector access still rejected after
    migration' / backend-platform 'Own-uid-scoped route rejects cross-uid
    access': inspector A (sub==uidA) targeting a point whose
    inspector_uid is uidB must be rejected, with NO sticker_matches write."""
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "point-b"},
    )

    assert resp.status_code == 403
    # No write occurred — point-b's state is exactly as it started.
    assert store["point-b"]["estado_asignacion"] == "pendiente"


def test_own_uid_marcar_hecho_succeeds(monkeypatch):
    """field-form-session 'Own-uid access still succeeds after migration':
    inspector A targeting their OWN point succeeds and flips
    estado_asignacion to 'hecho'."""
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "point-a"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["id"] == "point-a"
    assert body["estado_asignacion"] == "hecho"
    assert store["point-a"]["estado_asignacion"] == "hecho"


def test_marcar_hecho_missing_punto_id_is_rejected(monkeypatch):
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post("/inspector-asignaciones", json={"action": "marcarHecho"})

    assert resp.status_code == 400


def test_marcar_hecho_nonexistent_point_is_rejected(monkeypatch):
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "does-not-exist"},
    )

    assert resp.status_code == 404


def test_unrecognized_action_is_rejected(monkeypatch):
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post("/inspector-asignaciones", json={"action": "bogus"})

    assert resp.status_code == 400


# ---- misPuntosPlaneacion / marcarHechoPlaneacion --------------------------
# `planeacion-asignaciones` follow-up batch (2026-08-26): the inspector-
# facing counterpart to `planeacion_puntos`, own-uid-scoped exactly like
# misPuntos/marcarHecho above — same auth surface, different collection.


def test_mis_puntos_planeacion_returns_only_own_pending_points(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntosPlaneacion"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    ids = {p["id"] for p in body["puntos"]}
    # Only inspector A's still-pending point — not B's (cross-uid), not A's
    # already-`hecho` point, and not A's `no_aplica` point (excluded from
    # the pool, same as the admin dashboard treats it).
    assert ids == {"pln-a"}


def test_mis_puntos_planeacion_includes_survey_link_and_point_fields(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntosPlaneacion"})

    punto = resp.json()["puntos"][0]
    assert punto["clave_integracion"] == "PLN-AAAAAA-11111111"
    assert punto["direccion"] == "Calle 1 #2-3"
    assert punto["coords"] == {"lat": 3.4, "lon": -76.5}
    assert punto["comuna"] == "1"
    assert punto["afectacion"] == "DAÑO ESTRUCTURAL"
    assert punto["prioridad"] == "alta"
    # Built via services.survey_link.build_survey_urls with the SAME
    # SURVEY123_FORM_URL the admin router reads — no duplicated URL logic.
    assert punto["survey_web"].startswith("https://survey123.arcgis.com/share/abc123")
    assert "field:codigoapp=PLN-AAAAAA-11111111" in punto["survey_web"]


def test_mis_puntos_planeacion_omits_survey_links_when_form_url_unset(monkeypatch):
    """Fail-open, not fail-loud: unlike getEnlaceSurvey's 503, a LIST action
    must never blank-page an inspector's whole picker over one missing env
    var — the point still shows, just without a clickable link yet."""
    store = _store()
    planeacion = _planeacion_store()
    app = _app(monkeypatch, store, planeacion)
    monkeypatch.setenv("SURVEY123_FORM_URL", "")
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_A
    client = TestClient(app)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntosPlaneacion"})

    assert resp.status_code == 200
    punto = resp.json()["puntos"][0]
    assert punto["survey_web"] is None
    assert punto["survey_app"] is None


def test_cross_inspector_marcar_hecho_planeacion_is_rejected_no_write(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "pln-b"},
    )

    assert resp.status_code == 403
    assert planeacion["pln-b"]["estado_asignacion"] == "pendiente"


def test_own_uid_marcar_hecho_planeacion_succeeds(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "pln-a"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["id"] == "pln-a"
    assert body["estado_asignacion"] == "hecho"
    assert planeacion["pln-a"]["estado_asignacion"] == "hecho"


def test_marcar_hecho_planeacion_missing_punto_id_is_rejected(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post("/inspector-asignaciones", json={"action": "marcarHechoPlaneacion"})

    assert resp.status_code == 400


def test_marcar_hecho_planeacion_nonexistent_point_is_rejected(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "does-not-exist"},
    )

    assert resp.status_code == 404


def test_marcar_hecho_planeacion_never_touches_sticker_matches(monkeypatch):
    """Cross-collection safety: marcarHechoPlaneacion must write ONLY to
    planeacion_puntos, never sticker_matches — same doc-id space collision
    risk a shared literal would otherwise create."""
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "pln-a"},
    )

    assert resp.status_code == 200
    assert "pln-a" not in store  # sticker_matches store untouched
