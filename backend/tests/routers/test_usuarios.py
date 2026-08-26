"""POST /usuarios (RED first, task 8.5) — design.md ADR-3/ADR-4;
backend-platform spec "Route Parity Across Consolidated Endpoints"
(`/usuarios` row), "usuarios endpoint enforces its extra provider/domain
gate".

Scope for THIS batch (8b) is deliberately narrower than `api/usuarios.js`'s
full action set — tasks.md 8.5/8.6 name exactly FOUR actions: `create`,
`list`, `setPassword`, `delete`. `setEnabled`/`setRole` are NOT in scope
(see `routers/usuarios.py`'s module docstring for the flagged gap).

Ports `api/usuarios.test.js`'s full fixture matrix verbatim:
- `classify` precedence incl. claim-override (inspector/usuario/admin/
  superadmin/viewer/otro, plus a password user promoted to admin by claim).
- `checkDeleteGuards`: last-enabled-admin block, non-admin delete allowed,
  a second enabled admin unblocks the delete of the first, self-uid delete
  always blocked regardless of role.
- `isValidPassword` bounds.

PLUS the extra gate this router layers on top of `require_role("admin")`
(spec.md "usuarios endpoint enforces its extra provider/domain gate"; design
open question 2; `api/usuarios.js:200-201`, byte-for-byte): the ACTING
admin's own token must have `sign_in_provider == 'password'` AND an email
NOT under `@sismocali.gov.co` — see `routers/usuarios.py`'s module
docstring for the full discussion of why this is an explicit additive
check, not just a restatement of `require_role("admin")`.

Auth fixture shape: unlike every prior router test file's flat
`FAKE_CLAIMS_ADMIN` (`{sub, email, role}`), THIS router's extra gate reads
`claims["firebase"]["sign_in_provider"]`, so the admin fixtures here always
carry that nested key — the realistic shape a verified Firebase ID token
actually has (`role_from_claims`'s own `firebase.sign_in_provider` read,
`app/auth/roles.py:58`).

Only `firebase_admin.auth` is faked (module-reference monkeypatch, same
"patch the imported module reference" convention `stickers.py`/
`source_status.py` established) — `delete` also touches Firestore
(`inspectores/{uid}` best-effort cleanup), faked with the same minimal
in-memory doc store shape `test_stickers.py` uses.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app
from app.routers import usuarios

# ── Acting-admin token fixtures — carry the nested `firebase.sign_in_provider`
# claim shape the extra gate reads (unlike other routers' flat fixtures). ────

UID_ADMIN = "uid-admin"
FAKE_CLAIMS_ADMIN = {
    "sub": UID_ADMIN,
    "email": "admin@example.com",
    "role": "admin",
    "firebase": {"sign_in_provider": "password"},
}
# Same admin custom claim, but the CALLER's own provider/domain fails the
# extra gate — an inspector (@sismocali.gov.co, password-provider) somehow
# holding an admin claim, or a google.com-provider admin claim holder.
FAKE_CLAIMS_ADMIN_SISMOCALI_DOMAIN = {
    "sub": "uid-admin-inspector",
    "email": "004@sismocali.gov.co",
    "role": "admin",
    "firebase": {"sign_in_provider": "password"},
}
FAKE_CLAIMS_ADMIN_GOOGLE_PROVIDER = {
    "sub": "uid-admin-google",
    "email": "admin@example.com",
    "role": "admin",
    "firebase": {"sign_in_provider": "google.com"},
}
FAKE_CLAIMS_VIEWER = {
    "sub": "uid-viewer",
    "email": "someone@gmail.com",
    "firebase": {"sign_in_provider": "google.com"},
}


# ── Fake firebase_admin.auth (list_users/create_user/update_user/delete_user) ─


class _FakeUserRecord:
    def __init__(
        self,
        uid: str,
        email: str,
        disabled: bool = False,
        custom_claims: dict[str, Any] | None = None,
        provider_ids: list[str] | None = None,
    ) -> None:
        self.uid = uid
        self.email = email
        self.disabled = disabled
        self.custom_claims = custom_claims
        self.provider_data = [SimpleNamespace(provider_id=p) for p in (provider_ids or ["password"])]


class _FakeListUsersPage:
    def __init__(self, users: list[_FakeUserRecord]) -> None:
        self.users = users


class _FakeAuth:
    def __init__(self, users: list[_FakeUserRecord] | None = None) -> None:
        self._users: dict[str, _FakeUserRecord] = {u.uid: u for u in (users or [])}
        self._next_uid = len(self._users) + 1
        self.create_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []
        self.update_calls: list[tuple[str, str]] = []

    def list_users(self, max_results: int = 1000, app: Any = None) -> _FakeListUsersPage:
        return _FakeListUsersPage(list(self._users.values()))

    def create_user(self, *, email: str, password: str, app: Any = None) -> _FakeUserRecord:
        uid = f"uid-{self._next_uid}"
        self._next_uid += 1
        record = _FakeUserRecord(uid, email)
        self._users[uid] = record
        self.create_calls.append((email, password))
        return record

    def delete_user(self, uid: str, app: Any = None) -> None:
        self.delete_calls.append(uid)
        self._users.pop(uid, None)

    def update_user(self, uid: str, *, password: str | None = None, app: Any = None) -> None:
        self.update_calls.append((uid, password or ""))


# ── Minimal fake Firestore (only `inspectores/{uid}` doc delete is touched) ──


class _FakeDocRef:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store = store
        self._id = doc_id

    def delete(self) -> None:
        self._store.pop(self._id, None)


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id)


class _FakeFirestore:
    def __init__(self, stores: dict[str, dict[str, dict[str, Any]]]) -> None:
        self._stores = stores

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._stores.setdefault(name, {}))


class _FakeSismoClients:
    def __init__(self, stores: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.firestore = _FakeFirestore(stores)
        self.app = object()


def _app(monkeypatch, fake_auth: _FakeAuth, stores: dict[str, dict[str, dict[str, Any]]] | None = None) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    credentials.s3.cache_clear()
    stores = stores if stores is not None else {"inspectores": {}}
    monkeypatch.setattr(credentials, "sismo", lambda: _FakeSismoClients(stores))
    monkeypatch.setattr(usuarios, "fb_auth", fake_auth)
    return create_app()


def _client_as(monkeypatch, claims: dict[str, Any], fake_auth: _FakeAuth, stores=None) -> TestClient:
    app = _app(monkeypatch, fake_auth, stores)
    app.dependency_overrides[current_claims] = lambda: claims
    return TestClient(app)


def _admin_client(monkeypatch, fake_auth: _FakeAuth, stores=None) -> TestClient:
    return _client_as(monkeypatch, FAKE_CLAIMS_ADMIN, fake_auth, stores)


# ── Pure: classify (api/usuarios.test.js fixture matrix, verbatim) ─────────

_INSPECTOR = _FakeUserRecord("i", "cedula@sismocali.gov.co", provider_ids=["password"])
_USUARIO = _FakeUserRecord("u", "someone@example.com", provider_ids=["password"])
_ADMIN = _FakeUserRecord("a", "boss@example.com", provider_ids=["password"], custom_claims={"role": "admin"})
_SUPERADMIN = _FakeUserRecord("s", "juanp.gzmz@gmail.com", provider_ids=["password"])
_VIEWER = _FakeUserRecord("v", "viewer@cali.gov.co", provider_ids=["google.com"])
_OTRO = _FakeUserRecord("o", "stray@gmail.com", provider_ids=["google.com"])


def test_classify_inspector_sismocali_wins_over_password():
    assert usuarios.classify(_INSPECTOR) == "inspector"


def test_classify_usuario_password_default_is_not_admin():
    assert usuarios.classify(_USUARIO) == "usuario"


def test_classify_admin_explicit_custom_claim():
    assert usuarios.classify(_ADMIN) == "admin"


def test_classify_superadmin_email_no_claim_needed():
    assert usuarios.classify(_SUPERADMIN) == "admin"


def test_classify_viewer():
    assert usuarios.classify(_VIEWER) == "viewer"


def test_classify_otro():
    assert usuarios.classify(_OTRO) == "otro"


def test_classify_claim_overrides_derived_default():
    promoted = _FakeUserRecord("u2", "someone@example.com", provider_ids=["password"], custom_claims={"role": "admin"})
    assert usuarios.classify(promoted) == "admin"


# ── Pure: checkDeleteGuards (api/usuarios.test.js fixture matrix, verbatim) ─

_FIXTURE_USERS = [
    _FakeUserRecord("admin-1", "boss@example.com", disabled=False, provider_ids=["password"], custom_claims={"role": "admin"}),
    _FakeUserRecord("viewer-1", "viewer@cali.gov.co", disabled=False, provider_ids=["google.com"]),
    _FakeUserRecord("usuario-1", "someone@example.com", disabled=False, provider_ids=["password"]),
    _FakeUserRecord("inspector-1", "cedula@sismocali.gov.co", disabled=False, provider_ids=["password"]),
    _FakeUserRecord(
        "admin-disabled", "old@example.com", disabled=True, provider_ids=["password"], custom_claims={"role": "admin"}
    ),
]


def test_delete_guard_blocks_last_enabled_admin():
    rejection = usuarios.check_delete_guards(_FIXTURE_USERS, "admin-1", "admin-1-not-caller")
    assert rejection is not None
    assert rejection["status"] == 403


def test_delete_guard_allows_non_admin_delete():
    assert usuarios.check_delete_guards(_FIXTURE_USERS, "viewer-1", "admin-1-not-caller") is None
    assert usuarios.check_delete_guards(_FIXTURE_USERS, "usuario-1", "admin-1-not-caller") is None


def test_delete_guard_second_admin_unblocks_delete():
    two_admins = _FIXTURE_USERS + [
        _FakeUserRecord("admin-2", "a2@example.com", disabled=False, provider_ids=["password"], custom_claims={"role": "admin"})
    ]
    assert usuarios.check_delete_guards(two_admins, "admin-1", "admin-2") is None


@pytest.mark.parametrize("target_uid", ["viewer-1", "admin-1", "inspector-1"])
def test_delete_guard_self_uid_delete_always_blocked(target_uid):
    rejection = usuarios.check_delete_guards(_FIXTURE_USERS, target_uid, target_uid)
    assert rejection is not None
    assert rejection["status"] == 403


# ── Pure: isValidPassword bounds ────────────────────────────────────────────


def test_is_valid_password_bounds():
    assert usuarios.is_valid_password("Cali2026+-") is True
    assert usuarios.is_valid_password("12345") is False
    assert usuarios.is_valid_password(None) is False


# ── Router: the extra provider/domain gate (design open question 2, spec
# "usuarios endpoint enforces its extra provider/domain gate") ─────────────


def test_extra_gate_rejects_acting_admin_under_sismocali_domain(monkeypatch):
    fake_auth = _FakeAuth()
    client = _client_as(monkeypatch, FAKE_CLAIMS_ADMIN_SISMOCALI_DOMAIN, fake_auth)

    resp = client.post("/usuarios", json={"action": "list"})

    assert resp.status_code == 403
    assert fake_auth.create_calls == []


def test_extra_gate_rejects_acting_admin_with_non_password_provider(monkeypatch):
    fake_auth = _FakeAuth()
    client = _client_as(monkeypatch, FAKE_CLAIMS_ADMIN_GOOGLE_PROVIDER, fake_auth)

    resp = client.post("/usuarios", json={"action": "list"})

    assert resp.status_code == 403
    assert fake_auth.create_calls == []


def test_extra_gate_accepts_password_provider_non_sismocali_admin(monkeypatch):
    fake_auth = _FakeAuth()
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/usuarios", json={"action": "list"})

    assert resp.status_code == 200


# ── Router: admin-gate rejection, no mutation ───────────────────────────────


@pytest.mark.parametrize("action", ["list", "create", "setPassword", "delete"])
def test_non_admin_is_rejected_no_mutation(monkeypatch, action):
    fake_auth = _FakeAuth()
    client = _client_as(monkeypatch, FAKE_CLAIMS_VIEWER, fake_auth)

    resp = client.post(
        "/usuarios",
        json={"action": action, "email": "new@example.com", "password": "Cali2026+", "uid": "some-uid"},
    )

    assert resp.status_code == 403
    assert fake_auth.create_calls == []
    assert fake_auth.delete_calls == []
    assert fake_auth.update_calls == []


def test_unauthenticated_is_rejected(monkeypatch):
    fake_auth = _FakeAuth()
    app = _app(monkeypatch, fake_auth)
    client = TestClient(app)

    resp = client.post("/usuarios", json={"action": "list"})

    assert resp.status_code == 401


# ── Router: admin success/failure per action ────────────────────────────────


def test_admin_list_returns_classified_users(monkeypatch):
    fake_auth = _FakeAuth(
        users=[
            _FakeUserRecord("uid-1", "boss@example.com", provider_ids=["password"], custom_claims={"role": "admin"}),
            _FakeUserRecord("uid-2", "viewer@cali.gov.co", provider_ids=["google.com"]),
            _FakeUserRecord("uid-3", "cedula@sismocali.gov.co", provider_ids=["password"]),
        ]
    )
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/usuarios", json={"action": "list"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    by_uid = {u["uid"]: u for u in body["usuarios"]}
    assert by_uid["uid-1"]["role"] == "admin"
    assert by_uid["uid-2"]["role"] == "viewer"
    assert by_uid["uid-3"]["role"] == "inspector"


def test_admin_create_mints_password_admin(monkeypatch):
    fake_auth = _FakeAuth()
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post(
        "/usuarios", json={"action": "create", "email": "New.Admin@Example.com", "password": "Cali2026+"}
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["ok"] is True
    assert body["email"] == "new.admin@example.com"
    assert body["uid"]
    assert fake_auth.create_calls == [("new.admin@example.com", "Cali2026+")]


def test_admin_create_rejects_sismocali_domain_no_auth_call(monkeypatch):
    fake_auth = _FakeAuth()
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post(
        "/usuarios", json={"action": "create", "email": "004@sismocali.gov.co", "password": "Cali2026+"}
    )

    assert resp.status_code == 400
    assert fake_auth.create_calls == []


def test_admin_create_rejects_invalid_password_no_auth_call(monkeypatch):
    fake_auth = _FakeAuth()
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/usuarios", json={"action": "create", "email": "x@example.com", "password": "12345"})

    assert resp.status_code == 400
    assert fake_auth.create_calls == []


def test_admin_set_password_updates_target(monkeypatch):
    fake_auth = _FakeAuth(users=[_FakeUserRecord("uid-1", "x@example.com")])
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/usuarios", json={"action": "setPassword", "uid": "uid-1", "password": "NuevaClave1"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "uid": "uid-1"}
    assert fake_auth.update_calls == [("uid-1", "NuevaClave1")]


def test_admin_set_password_rejects_short_password(monkeypatch):
    fake_auth = _FakeAuth(users=[_FakeUserRecord("uid-1", "x@example.com")])
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/usuarios", json={"action": "setPassword", "uid": "uid-1", "password": "abc"})

    assert resp.status_code == 400
    assert fake_auth.update_calls == []


def test_admin_delete_removes_auth_user_and_profile(monkeypatch):
    fake_auth = _FakeAuth(
        users=[
            _FakeUserRecord(UID_ADMIN, "admin@example.com", provider_ids=["password"], custom_claims={"role": "admin"}),
            _FakeUserRecord("uid-target", "viewer@cali.gov.co", provider_ids=["google.com"]),
        ]
    )
    stores = {"inspectores": {"uid-target": {"activo": True}}}
    client = _admin_client(monkeypatch, fake_auth, stores)

    resp = client.post("/usuarios", json={"action": "delete", "uid": "uid-target"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "uid": "uid-target"}
    assert fake_auth.delete_calls == ["uid-target"]
    assert "uid-target" not in stores["inspectores"]


def test_admin_delete_blocks_self_uid(monkeypatch):
    fake_auth = _FakeAuth(
        users=[_FakeUserRecord(UID_ADMIN, "admin@example.com", provider_ids=["password"], custom_claims={"role": "admin"})]
    )
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/usuarios", json={"action": "delete", "uid": UID_ADMIN})

    assert resp.status_code == 403
    assert fake_auth.delete_calls == []


def test_admin_delete_second_admin_unblocks(monkeypatch):
    fake_auth = _FakeAuth(
        users=[
            _FakeUserRecord(UID_ADMIN, "admin@example.com", provider_ids=["password"], custom_claims={"role": "admin"}),
            _FakeUserRecord("uid-other-admin", "other@example.com", provider_ids=["password"], custom_claims={"role": "admin"}),
        ]
    )
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/usuarios", json={"action": "delete", "uid": "uid-other-admin"})

    assert resp.status_code == 200
    assert fake_auth.delete_calls == ["uid-other-admin"]


def test_unrecognized_action_is_rejected(monkeypatch):
    fake_auth = _FakeAuth()
    client = _admin_client(monkeypatch, fake_auth)

    resp = client.post("/usuarios", json={"action": "bogus"})

    assert resp.status_code == 400
