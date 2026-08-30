"""POST /stickers (RED first, task 8.1) — design.md ADR-4/ADR-9;
backend-platform spec "Admin-gated route rejects non-admin" (`/stickers`
row), "Route Parity Across Consolidated Endpoints".

Ports `api/stickers.js`'s `list`/`evaluaciones`/`create`/`setEnabled`
dispatch. Admin-only (`Depends(require_role("admin"))`) — unlike
`inspector-asignaciones` (any-authenticated), this whole endpoint manages
OTHER people's Auth accounts, so a non-admin caller must be rejected with
NO mutation for every action, not just a subset.

Two Firebase surfaces are faked, matching this router's two real
dependencies:

- `credentials.sismo()` — same call-count-instrumented fake pattern
  `test_inspector_asignaciones.py`/`test_source_status.py` established
  (`_FakeSismoClients` with a `.firestore` in-memory store + a plain
  `.app` sentinel object, no real service-account JSON, no network).
- `app.routers.stickers.fb_auth` — the imported `firebase_admin.auth`
  module reference, monkeypatched wholesale to a small in-memory fake
  (`list_users`/`create_user`/`update_user`/`delete_user`) so Auth-account
  management never touches a real Firebase project either. Importing
  `app.routers.stickers` directly (to reach `stickers.fb_auth` for the
  patch) is what makes this file's RED genuine: it fails at COLLECTION
  time with `ImportError: cannot import name 'stickers' from
  'app.routers'` until 8.2 lands — the same ImportError-before-any-code
  pattern 7.1/7.3/7.8 established, not a runtime 404.

Also ports the pure validators from `api/stickers.test.js` verbatim
(cedula/codigo/password regex bounds, email round-trip,
`nextAvailableCodigo`'s gap-filling allocation).
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app
from app.routers import stickers

UID_ADMIN = "uid-admin"
FAKE_CLAIMS_ADMIN = {"sub": UID_ADMIN, "email": "admin@example.com", "role": "admin"}
FAKE_CLAIMS_VIEWER = {"sub": "uid-viewer", "email": "someone@gmail.com"}


# ── Fake Firestore (in-memory dict, keyed by doc id within one collection
# namespace) — same shape convention test_inspector_asignaciones.py uses,
# extended with get_all() + transaction() (stickers.py's codigo-allocation
# read-before-write needs both). ────────────────────────────────────────


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
        current = dict(self._store.get(self._id, {})) if merge else {}
        current.update(data)
        self._store[self._id] = current


class _FakeQuery:
    def __init__(self, docs: list[_FakeSnapshot]) -> None:
        self._docs = docs

    def where(self, field: str, op: str, value: Any) -> "_FakeQuery":
        assert op == "=="
        return _FakeQuery([d for d in self._docs if (d.to_dict() or {}).get(field) == value])

    def get(self) -> list[_FakeSnapshot]:
        return list(self._docs)


class _FakeTransaction:
    _is_test_double = True

    def __init__(self, collection: "_FakeCollection") -> None:
        self._collection = collection

    def get(self, ref_or_collection: Any) -> list[_FakeSnapshot]:
        # stickers.py only ever transaction.get()s the whole 'inspectores'
        # collection (the codigo-allocation read), never a single doc ref.
        return self._collection.get()

    def set(self, ref: _FakeDocRef, data: dict[str, Any], merge: bool = False) -> None:
        ref.set(data, merge=merge)


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id)

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        docs = [_FakeSnapshot(doc_id, data) for doc_id, data in self._store.items()]
        return _FakeQuery(docs).where(field, op, value)

    def get(self) -> list[_FakeSnapshot]:
        return [_FakeSnapshot(doc_id, data) for doc_id, data in self._store.items()]

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)


class _FakeFirestore:
    def __init__(self, stores: dict[str, dict[str, dict[str, Any]]]) -> None:
        self._stores = stores

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._stores.setdefault(name, {}))

    def get_all(self, refs: list[_FakeDocRef]) -> list[_FakeSnapshot]:
        return [ref.get() for ref in refs]

    def transaction(self) -> _FakeTransaction:
        # Only reached via db.transaction() directly in stickers.py's
        # _allocate_codigo() — delegate to the 'inspectores' collection's
        # own transaction() so transaction.get() still returns its docs.
        return self.collection("inspectores").transaction()


class _FakeSismoClients:
    def __init__(self, stores: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.firestore = _FakeFirestore(stores)
        self.app = object()


# ── Fake firebase_admin.auth (list_users/create_user/update_user/delete_user) ─


class _FakeUserRecord:
    def __init__(self, uid: str, email: str, disabled: bool = False) -> None:
        self.uid = uid
        self.email = email
        self.disabled = disabled


class _FakeListUsersPage:
    def __init__(self, users: list[_FakeUserRecord]) -> None:
        self.users = users


class _FakeAuth:
    def __init__(self, users: list[_FakeUserRecord] | None = None) -> None:
        self._users: dict[str, _FakeUserRecord] = {u.uid: u for u in (users or [])}
        self._next_uid = len(self._users) + 1
        self.create_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []
        self.update_calls: list[tuple[str, bool]] = []
        self.fail_create: Exception | None = None

    def list_users(self, max_results: int = 1000, app: Any = None) -> _FakeListUsersPage:
        return _FakeListUsersPage(list(self._users.values()))

    def create_user(self, *, email: str, password: str, app: Any = None) -> _FakeUserRecord:
        if self.fail_create is not None:
            raise self.fail_create
        self.create_calls.append((email, password))
        uid = f"uid-{self._next_uid}"
        self._next_uid += 1
        record = _FakeUserRecord(uid, email)
        self._users[uid] = record
        return record

    def delete_user(self, uid: str, app: Any = None) -> None:
        self.delete_calls.append(uid)
        self._users.pop(uid, None)

    def update_user(self, uid: str, *, disabled: bool, app: Any = None) -> None:
        self.update_calls.append((uid, disabled))
        if uid in self._users:
            self._users[uid].disabled = disabled


def _app(monkeypatch, fake_auth: _FakeAuth, stores: dict[str, dict[str, dict[str, Any]]] | None = None) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    credentials.s3.cache_clear()
    stores = stores if stores is not None else {"inspectores": {}, "evaluaciones": {}}
    monkeypatch.setattr(credentials, "sismo", lambda: _FakeSismoClients(stores))
    monkeypatch.setattr(stickers, "fb_auth", fake_auth)
    return create_app()


def _admin_client(monkeypatch, fake_auth: _FakeAuth, stores=None) -> TestClient:
    app = _app(monkeypatch, fake_auth, stores)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN
    return TestClient(app)


def _viewer_client(monkeypatch, fake_auth: _FakeAuth, stores=None) -> TestClient:
    app = _app(monkeypatch, fake_auth, stores)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER
    return TestClient(app)


# ── Pure validator ports (api/stickers.test.js) ─────────────────────────────


def test_is_valid_cedula():
    assert stickers.is_valid_cedula("1020735324") is True
    assert stickers.is_valid_cedula("12345") is True
    assert stickers.is_valid_cedula("1234") is False
    assert stickers.is_valid_cedula("abc123") is False
    assert stickers.is_valid_cedula("") is False


def test_is_valid_codigo():
    assert stickers.is_valid_codigo("004") is True
    assert stickers.is_valid_codigo("4") is False
    assert stickers.is_valid_codigo("0040") is False


def test_is_valid_password():
    assert stickers.is_valid_password("Cali2026+-") is True
    assert stickers.is_valid_password("12345") is False
    assert stickers.is_valid_password(None) is False


def test_email_round_trip():
    assert stickers.cedula_to_email(" 1020735324 ") == "1020735324@sismocali.gov.co"
    assert stickers.email_to_cedula("1020735324@sismocali.gov.co") == "1020735324"


def test_next_available_codigo():
    next_ = stickers.next_available_codigo
    assert next_([]) == "001"
    assert next_(["001", "002", "003"]) == "004"
    assert next_(["001", "003"]) == "002"  # fills the gap
    assert next_(["002", "003"]) == "001"  # starts at 001
    assert next_(["1", " 2 "]) == "003"  # unpadded/whitespace input
    assert next_(["001", "999"]) == "002"  # a high code doesn't push the next one up
    assert next_([str(i + 1).zfill(3) for i in range(999)]) is None  # exhausted


# ── Router: admin-gate rejection, no mutation ───────────────────────────────


@pytest.mark.parametrize("action", ["list", "evaluaciones", "create", "setEnabled"])
def test_non_admin_is_rejected_no_mutation(monkeypatch, action):
    fake_auth = _FakeAuth()
    client = _viewer_client(monkeypatch, fake_auth)

    resp = client.post("/stickers", json={"action": action, "cedula": "1020735324", "password": "Cali2026+"})

    assert resp.status_code == 403
    assert fake_auth.create_calls == []
    assert fake_auth.delete_calls == []
    assert fake_auth.update_calls == []


def test_unauthenticated_is_rejected(monkeypatch):
    fake_auth = _FakeAuth()
    app = _app(monkeypatch, fake_auth)
    client = TestClient(app)

    resp = client.post("/stickers", json={"action": "list"})

    assert resp.status_code == 401


# ── Router: admin CRUD actions succeed ──────────────────────────────────────


def test_admin_list_returns_inspectores_sorted_by_cedula(monkeypatch):
    fake_auth = _FakeAuth(
        users=[
            _FakeUserRecord("uid-2", "1020735324@sismocali.gov.co"),
            _FakeUserRecord("uid-1", "1000000000@sismocali.gov.co", disabled=True),
            _FakeUserRecord("uid-3", "admin@gmail.com"),  # not an inspector — filtered out
        ]
    )
    stores = {
        "inspectores": {
            "uid-2": {"nombre_completo": "Ana", "codigo": "002", "activo": True},
            # uid-1 has no profile doc -> registrado:false, activo defaults true
        },
        "evaluaciones": {},
    }
    client = _admin_client(monkeypatch, fake_auth, stores)

    resp = client.post("/stickers", json={"action": "list"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    cedulas = [i["cedula"] for i in body["inspectores"]]
    assert cedulas == ["1000000000", "1020735324"]  # sorted, gmail admin excluded
    by_cedula = {i["cedula"]: i for i in body["inspectores"]}
    assert by_cedula["1020735324"]["codigo"] == "002"
    assert by_cedula["1020735324"]["registrado"] is True
    assert by_cedula["1000000000"]["registrado"] is False
    assert by_cedula["1000000000"]["disabled"] is True
    assert by_cedula["1000000000"]["activo"] is True  # missing doc -> counts active


def test_admin_evaluaciones_returns_flattened_list(monkeypatch):
    fake_auth = _FakeAuth()
    stores = {
        "inspectores": {},
        "evaluaciones": {
            "ev-1": {
                "codigo_edificacion": "76001-1-0010001",
                "coords": {"lat": 3.4, "lng": -76.5, "accuracy": 5},
                "inspector": {"uid": "uid-x", "codigo": "004"},
                "descripcion": {"nombre": "Casa", "direccion": "Calle 1"},
                "acciones_posteriores": {"barricadas": True},
                "fotos": ["a.jpg", None],
                "fecha_hora_dispositivo": "2026-01-01T00:00:00Z",
            }
        },
    }
    client = _admin_client(monkeypatch, fake_auth, stores)

    resp = client.post("/stickers", json={"action": "evaluaciones"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["evaluaciones"]) == 1
    ev = body["evaluaciones"][0]
    assert ev["id"] == "ev-1"
    assert ev["coords"] == {"lat": 3.4, "lng": -76.5, "accuracy": 5}
    assert ev["inspector"]["codigo"] == "004"
    assert ev["acciones_posteriores"] == {"barricadas": True, "evaluacion_detallada": False}
    assert ev["fotos"] == ["a.jpg"]  # falsy entries filtered
    assert ev["fecha"] == "2026-01-01T00:00:00Z"


def test_admin_create_allocates_next_free_codigo(monkeypatch):
    fake_auth = _FakeAuth()
    stores = {"inspectores": {"uid-1": {"codigo": "001"}}, "evaluaciones": {}}
    client = _admin_client(monkeypatch, fake_auth, stores)

    resp = client.post(
        "/stickers",
        json={
            "action": "create",
            "cedula": "1020735324",
            "password": "Cali2026+",
            "nombre_completo": "Ana Perez",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["ok"] is True
    assert body["email"] == "1020735324@sismocali.gov.co"
    assert body["codigo"] == "002"  # 001 taken, next free
    assert fake_auth.create_calls == [("1020735324@sismocali.gov.co", "Cali2026+")]
    assert stores["inspectores"][body["uid"]]["codigo"] == "002"


def test_admin_create_invalid_cedula_is_rejected_no_auth_call(monkeypatch):
    fake_auth = _FakeAuth()
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/stickers", json={"action": "create", "cedula": "abc", "password": "Cali2026+"})

    assert resp.status_code == 400
    assert fake_auth.create_calls == []


def test_admin_create_rolls_back_auth_user_on_firestore_failure(monkeypatch):
    fake_auth = _FakeAuth()
    stores = {
        "inspectores": {str(i + 1).zfill(3): {"codigo": str(i + 1).zfill(3)} for i in range(999)},
        "evaluaciones": {},
    }
    client = _admin_client(monkeypatch, fake_auth, stores)

    resp = client.post(
        "/stickers",
        json={"action": "create", "cedula": "1020735324", "password": "Cali2026+"},
    )

    assert resp.status_code == 400  # brigade codes exhausted (001-999)
    assert fake_auth.create_calls == [("1020735324@sismocali.gov.co", "Cali2026+")]
    assert len(fake_auth.delete_calls) == 1  # orphan Auth account cleaned up


def test_admin_set_enabled_flips_auth_and_firestore(monkeypatch):
    fake_auth = _FakeAuth(users=[_FakeUserRecord("uid-1", "1020735324@sismocali.gov.co")])
    stores = {"inspectores": {"uid-1": {"activo": True}}, "evaluaciones": {}}
    client = _admin_client(monkeypatch, fake_auth, stores)

    resp = client.post("/stickers", json={"action": "setEnabled", "uid": "uid-1", "enabled": False})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "uid": "uid-1", "activo": False, "disabled": True}
    assert fake_auth.update_calls == [("uid-1", True)]
    assert stores["inspectores"]["uid-1"]["activo"] is False


def test_get_evaluaciones_is_cached_across_consecutive_calls(monkeypatch):
    fake_auth = _FakeAuth()
    stores = {
        "inspectores": {},
        "evaluaciones": {"ev-1": {"codigo_edificacion": "76001-1-0010001"}},
    }
    client = _admin_client(monkeypatch, fake_auth, stores)

    calls = {"n": 0}
    original = stickers.list_evaluaciones

    def counting_stub(db):
        calls["n"] += 1
        return original(db)

    monkeypatch.setattr(stickers, "list_evaluaciones", counting_stub)

    resp1 = client.get("/evaluaciones")
    resp2 = client.get("/evaluaciones")

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json() == resp2.json()
    assert calls["n"] == 1  # TTL cache serves the second call without refetching


def test_get_evaluaciones_firestore_exception_becomes_502_not_a_bare_crash(monkeypatch):
    """A 429/ResourceExhausted (or any other) Firestore exception must
    surface as a normal HTTPException (502, CORS headers intact) — not
    propagate unhandled into a bare 500 that Starlette's default error
    handler serves with NO CORS headers, which the browser then
    misreports as "blocked by CORS policy" instead of the real cause."""
    fake_auth = _FakeAuth()
    client = _admin_client(monkeypatch, fake_auth)

    def boom(db):
        raise RuntimeError("429 Quota exceeded.")

    monkeypatch.setattr(stickers, "list_evaluaciones", boom)

    resp = client.get("/evaluaciones")

    assert resp.status_code == 502
    assert "Quota exceeded" in resp.json()["detail"]


def test_get_evaluaciones_non_admin_is_403(monkeypatch):
    fake_auth = _FakeAuth()
    client = _viewer_client(monkeypatch, fake_auth)

    resp = client.get("/evaluaciones")

    assert resp.status_code == 403


def test_unrecognized_action_is_rejected(monkeypatch):
    fake_auth = _FakeAuth()
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/stickers", json={"action": "bogus"})

    assert resp.status_code == 400
