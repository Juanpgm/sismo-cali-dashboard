"""GET/POST /survey-cali, GET/PATCH/DELETE /survey-cali/{id},
GET /survey-cali/{id}/history, POST /survey-cali/{id}/revert (RED first,
task 8.9) — design.md ADR-9 (sole-writer extension, final closure)/ADR-10
(doc+history model)/ADR-12 (this router); survey-cali-collection spec: "Non-
admin call is rejected", "Update is a merge, not a replace", "Admin update
writes a uid-authored revision", "Listing history returns all revisions in
order", "Viewing a revision shows its changed fields", "Revert creates a new
revision instead of mutating history", "Default list omits history",
"History is available on explicit request".

Unlike `tests/services/test_survey_cali.py` (mutation-CORE only, no
FastAPI/TestClient), THIS file drives the actual HTTP routes end-to-end via
`TestClient`, with a router-owned Fake Firestore that extends
`tests/services/test_survey_cali.py`'s path-keyed `_FakeDB` with one thing
that mutation-core test didn't need: `_FakeCollection.get()` — listing every
direct child doc under a collection path (used by `GET /survey-cali`'s list
and `GET /survey-cali/{id}/history`'s revision list; NOT by
`apply_mutation`, which never lists a whole collection).

Seeding for scenarios that need pre-existing state calls
`app.services.survey_cali.apply_mutation(...)` DIRECTLY against the same
fake `db` the app will be wired to (same precedent
`tests/services/test_survey_cali.py` established) rather than routing every
setup step through the HTTP layer — keeps each test focused on the ONE
router behavior it actually exercises.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app
from app.services import survey_cali

# ── Fake Firestore (path-keyed; extends the mutation-core test's fake with
# whole-collection listing, which apply_mutation itself never needs) ───────


class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self, db: "_FakeDB", path: tuple[str, ...]) -> None:
        self._db = db
        self._path = path

    def get(self, transaction=None, field_paths=None) -> _FakeSnapshot:
        data = self._db.store.get(self._path)
        if field_paths is not None and data is not None:
            data = {k: data.get(k) for k in field_paths}
        return _FakeSnapshot(self._path[-1], data)

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        current = self._db.store.get(self._path)
        if merge and current is not None:
            merged = dict(current)
            merged.update(data)
            self._db.store[self._path] = merged
        else:
            self._db.store[self._path] = dict(data)

    def collection(self, name: str) -> "_FakeCollection":
        return _FakeCollection(self._db, self._path + (name,))


class _FakeCollection:
    def __init__(self, db: "_FakeDB", path: tuple[str, ...]) -> None:
        self._db = db
        self._path = path

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._db, self._path + (doc_id,))

    def get(self) -> list[_FakeSnapshot]:
        """Direct children ONLY (one path segment deeper) — mirrors a real
        `CollectionReference.get()`, never descending into subcollections
        (e.g. listing `survey_cali` must not return `survey_cali/{id}/
        history/{rev}` docs)."""
        depth = len(self._path) + 1
        out = []
        for path, data in self._db.store.items():
            if len(path) == depth and path[:-1] == self._path:
                out.append(_FakeSnapshot(path[-1], data))
        return out


class _FakeTransaction:
    _is_test_double = True

    def set(self, ref: _FakeDocRef, data: dict[str, Any], merge: bool = False) -> None:
        ref.set(data, merge=merge)


class _FakeDB:
    def __init__(self) -> None:
        self.store: dict[tuple[str, ...], dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self, (name,))

    def document(self, path_str: str) -> _FakeDocRef:
        return _FakeDocRef(self, tuple(path_str.split("/")))

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    def get_all(self, refs: list[_FakeDocRef], field_paths=None) -> list[_FakeSnapshot]:
        return [ref.get(field_paths=field_paths) for ref in refs]

    def snapshot(self) -> dict[tuple[str, ...], dict[str, Any]]:
        """Deep-enough copy for before/after "no state change" comparisons."""
        return {path: dict(data) for path, data in self.store.items()}


class _FakeSismoClients:
    def __init__(self, db: _FakeDB) -> None:
        self.firestore = db
        self.app = object()


# ── App / client wiring ─────────────────────────────────────────────────

UID_ADMIN = "uid-admin"
FAKE_CLAIMS_ADMIN = {"sub": UID_ADMIN, "email": "admin@example.com", "role": "admin"}
FAKE_CLAIMS_VIEWER = {"sub": "uid-viewer", "email": "viewer@example.com", "role": "viewer"}


def _app(monkeypatch, db: _FakeDB) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    credentials.s3.cache_clear()
    monkeypatch.setattr(credentials, "sismo", lambda: _FakeSismoClients(db))
    return create_app()


def _client_as(monkeypatch, claims: dict[str, Any], db: _FakeDB) -> TestClient:
    app = _app(monkeypatch, db)
    app.dependency_overrides[current_claims] = lambda: claims
    return TestClient(app)


def _admin_client(monkeypatch, db: _FakeDB) -> TestClient:
    return _client_as(monkeypatch, FAKE_CLAIMS_ADMIN, db)


def _seed(db: _FakeDB, gid: str, fields: dict[str, Any], author: str = "pipeline", kind: str = "create") -> None:
    survey_cali.apply_mutation(gid, fields, author, kind, db=db)


# ── Non-admin rejection, no state change — all 7 routes ────────────────────

_ROUTE_CASES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/survey-cali", None),
    ("POST", "/survey-cali", {"id": "gid-new", "direccion": "Calle 1"}),
    ("GET", "/survey-cali/gid-1", None),
    ("PATCH", "/survey-cali/gid-1", {"direccion": "Calle 2"}),
    ("DELETE", "/survey-cali/gid-1", None),
    ("GET", "/survey-cali/gid-1/history", None),
    ("POST", "/survey-cali/gid-1/revert", {"rev": 1}),
]


@pytest.mark.parametrize("method,path,body", _ROUTE_CASES, ids=[c[0] + " " + c[1] for c in _ROUTE_CASES])
def test_non_admin_rejected_no_state_change(monkeypatch, method, path, body):
    db = _FakeDB()
    _seed(db, "gid-1", {"direccion": "Calle 1"})
    before = db.snapshot()
    client = _client_as(monkeypatch, FAKE_CLAIMS_VIEWER, db)

    resp = client.request(method, path, json=body)

    assert resp.status_code == 403
    assert db.snapshot() == before


def test_unauthenticated_is_rejected(monkeypatch):
    db = _FakeDB()
    app = _app(monkeypatch, db)
    client = TestClient(app)

    resp = client.get("/survey-cali")

    assert resp.status_code == 401


# ── PATCH is merge-only, not a replace ──────────────────────────────────────


def test_patch_merges_does_not_drop_untouched_fields(monkeypatch):
    db = _FakeDB()
    _seed(db, "gid-1", {"a": 1, "b": 2})
    client = _admin_client(monkeypatch, db)

    resp = client.patch("/survey-cali/gid-1", json={"b": 3})

    assert resp.status_code == 200
    body = resp.json()
    assert body["record"]["a"] == 1
    assert body["record"]["b"] == 3


def test_patch_noop_returns_200_zero_new_revision(monkeypatch):
    db = _FakeDB()
    _seed(db, "gid-1", {"a": 1, "b": 2})
    rev_before = db.store[("survey_cali", "gid-1")]["_rev"]
    client = _admin_client(monkeypatch, db)

    resp = client.patch("/survey-cali/gid-1", json={"b": 2})

    assert resp.status_code == 200
    assert resp.json()["rev"] == rev_before
    history_docs = [p for p in db.store if p[:2] == ("survey_cali", "gid-1") and p[2:3] == ("history",)]
    assert len(history_docs) == 1  # only the original create revision


# ── Underscore-prefixed metadata is rejected by the schema ─────────────────


def test_patch_rejects_underscore_metadata_field(monkeypatch):
    db = _FakeDB()
    _seed(db, "gid-1", {"a": 1})
    client = _admin_client(monkeypatch, db)

    resp = client.patch("/survey-cali/gid-1", json={"_rev": 999})

    assert resp.status_code == 422
    assert db.store[("survey_cali", "gid-1")]["_rev"] == 1


def test_create_rejects_underscore_metadata_field(monkeypatch):
    db = _FakeDB()
    client = _admin_client(monkeypatch, db)

    resp = client.post("/survey-cali", json={"id": "gid-new", "_deleted": True})

    assert resp.status_code == 422
    assert ("survey_cali", "gid-new") not in db.store


# ── GET /survey-cali (list): excludes _deleted, never embeds history ───────


def test_list_excludes_deleted_and_never_embeds_history(monkeypatch):
    db = _FakeDB()
    _seed(db, "gid-1", {"direccion": "Calle 1"})
    _seed(db, "gid-2", {"direccion": "Calle 2"})
    survey_cali.apply_mutation("gid-2", {"_deleted": True}, "uid-admin", "delete", db=db)
    client = _admin_client(monkeypatch, db)

    resp = client.get("/survey-cali")

    assert resp.status_code == 200
    body = resp.json()
    ids = {r["id"] for r in body["records"]}
    assert ids == {"gid-1"}
    for record in body["records"]:
        assert "history" not in record


# ── GET /survey-cali/{id}/history: all revisions, newest first ─────────────


def test_history_returns_all_revisions_newest_first(monkeypatch):
    db = _FakeDB()
    _seed(db, "gid-1", {"a": 1})
    survey_cali.apply_mutation("gid-1", {"a": 2}, "uid-admin", "edit", db=db)
    survey_cali.apply_mutation("gid-1", {"a": 3}, "uid-admin", "edit", db=db)
    client = _admin_client(monkeypatch, db)

    resp = client.get("/survey-cali/gid-1/history")

    assert resp.status_code == 200
    revisions = resp.json()["revisions"]
    assert [r["rev"] for r in revisions] == [3, 2, 1]


def test_history_shows_changed_fields_for_a_revision(monkeypatch):
    db = _FakeDB()
    _seed(db, "gid-1", {"estado": "pendiente"})
    survey_cali.apply_mutation("gid-1", {"estado": "revisado"}, "uid-admin", "edit", db=db)
    client = _admin_client(monkeypatch, db)

    resp = client.get("/survey-cali/gid-1/history")

    revisions = {r["rev"]: r for r in resp.json()["revisions"]}
    assert revisions[2]["changes"] == {"estado": {"before": "pendiente", "after": "revisado"}}


def test_admin_update_writes_uid_authored_revision(monkeypatch):
    db = _FakeDB()
    _seed(db, "gid-1", {"a": 1})
    client = _admin_client(monkeypatch, db)

    resp = client.patch("/survey-cali/gid-1", json={"a": 2})

    assert resp.status_code == 200
    history_docs = {
        path[-1]: data for path, data in db.store.items()
        if path[:2] == ("survey_cali", "gid-1") and path[2:3] == ("history",)
    }
    assert history_docs["rev_000002"]["author"] == UID_ADMIN


# ── POST /survey-cali/{id}/revert ───────────────────────────────────────────


def test_revert_creates_new_revision_current_matches_target_prior_unchanged(monkeypatch):
    db = _FakeDB()
    _seed(db, "gid-1", {"a": 1})  # rev 1
    survey_cali.apply_mutation("gid-1", {"a": 2}, "uid-admin", "edit", db=db)  # rev 2
    survey_cali.apply_mutation("gid-1", {"a": 3}, "uid-admin", "edit", db=db)  # rev 3
    history_before = dict(
        (p, dict(d)) for p, d in db.store.items()
        if p[:2] == ("survey_cali", "gid-1") and p[2:3] == ("history",)
    )
    client = _admin_client(monkeypatch, db)

    resp = client.post("/survey-cali/gid-1/revert", json={"rev": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["record"]["a"] == 1  # current state matches target rev's values
    assert body["rev"] == 4  # a NEW revision, not a rewrite of rev 1

    history_after = {
        p: d for p, d in db.store.items()
        if p[:2] == ("survey_cali", "gid-1") and p[2:3] == ("history",)
    }
    for path, data in history_before.items():
        assert history_after[path] == data  # rev 1-3 unchanged
    new_rev = history_after[("survey_cali", "gid-1", "history", "rev_000004")]
    assert new_rev["kind"] == "revert"
    assert new_rev["revert_of"] == 1
