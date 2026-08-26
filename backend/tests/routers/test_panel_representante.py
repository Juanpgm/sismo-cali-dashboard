"""Manual pin of which inspection represents a duplicated building.

The pipeline picks the most RECENT inspection automatically. No automatic
rule is right every time, so an operator can pin a specific record for a
group; the Panel then counts that one. Reading is open to any authenticated
role (the Panel is visible to every role); pinning is admin-only, because it
changes the headline figures everyone reads.
"""
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import current_claims, require_role
from app.main import create_app
from app.routers import panel_representante as pr


class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)

    @property
    def exists(self):
        return self._data is not None


class _FakeRef:
    def __init__(self, store, doc_id):
        self._store, self._id = store, doc_id

    def get(self):
        return _FakeDoc(self._id, self._store.get(self._id))

    def set(self, data, merge=False):
        if merge and self._id in self._store:
            self._store[self._id].update(data)
        else:
            self._store[self._id] = dict(data)

    def delete(self):
        self._store.pop(self._id, None)


class _FakeCollection:
    def __init__(self, store):
        self._store = store

    def document(self, doc_id):
        return _FakeRef(self._store, doc_id)

    def stream(self):
        return [_FakeDoc(k, v) for k, v in self._store.items()]

    def get(self):
        return self.stream()


class _FakeDb:
    def __init__(self, store):
        self._store = store

    def collection(self, name):
        assert name == pr.PANEL_REPRESENTANTE_COLLECTION, name
        return _FakeCollection(self._store)


@pytest.fixture
def store():
    return {}


def _client(monkeypatch, store, *, role="admin"):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "x")

    class _C:
        firestore = _FakeDb(store)

    monkeypatch.setattr(pr.credentials, "sismo", lambda: _C())
    app = create_app()
    claims = {"sub": "u-1", "role": role, "email": "a@b.co"}
    app.dependency_overrides[current_claims] = lambda: claims
    app.dependency_overrides[require_role("admin")] = lambda: claims
    return TestClient(app)


def test_listing_pins_is_open_to_any_authenticated_role(monkeypatch, store):
    store["dir:CL 5"] = {"global_id": "abc", "fijado_por": "u-9"}
    resp = _client(monkeypatch, store, role="inspector").get("/panel-representante")

    assert resp.status_code == 200
    assert resp.json()["representantes"] == {"dir:CL 5": "abc"}


def test_pinning_records_who_did_it(monkeypatch, store):
    client = _client(monkeypatch, store)

    resp = client.post("/panel-representante",
                       json={"dup_grupo_id": "dir:CL 5", "global_id": "abc"})

    assert resp.status_code == 200, resp.text
    assert store["dir:CL 5"]["global_id"] == "abc"
    assert store["dir:CL 5"]["fijado_por"] == "u-1", "an override of a public figure must be attributable"
    assert store["dir:CL 5"].get("fijado_en")


def test_pinning_again_replaces_the_previous_pin(monkeypatch, store):
    client = _client(monkeypatch, store)
    client.post("/panel-representante", json={"dup_grupo_id": "g", "global_id": "uno"})
    client.post("/panel-representante", json={"dup_grupo_id": "g", "global_id": "dos"})

    assert store["g"]["global_id"] == "dos"
    assert len(store) == 1, "one pin per group, not an append-only pile"


def test_clearing_a_pin_returns_the_group_to_the_automatic_rule(monkeypatch, store):
    store["g"] = {"global_id": "abc"}
    client = _client(monkeypatch, store)

    resp = client.request("DELETE", "/panel-representante", json={"dup_grupo_id": "g"})

    assert resp.status_code == 200, resp.text
    assert "g" not in store


def test_missing_fields_are_rejected_without_writing(monkeypatch, store):
    client = _client(monkeypatch, store)

    for body in ({"dup_grupo_id": "g"}, {"global_id": "abc"}, {}):
        resp = client.post("/panel-representante", json=body)
        assert resp.status_code in (400, 422), body
    assert store == {}, "a rejected request must never leave a partial write"
