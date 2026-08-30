"""GET /sticker-status (RED first) — design.md ADR-4; backend-platform spec
"Any-authenticated role-wide route accepts every valid role", "sticker-status
cache hit within TTL".

Ports `api/sticker-status.js`'s Firestore read (`sticker_matches` collection
tally: `con_sticker`/`con`/`total`) but FIXES the legacy cache's warm-lambda-
only correctness: the legacy handler held its cache in a bare module-level
variable, which only behaved as a shared 5-minute cache when Vercel happened
to reuse a warm Lambda instance between invocations — a cold start (or two
concurrent cold invocations) got NO caching guarantee at all. This backend
is one always-on process (ADR-1 proposal answer 8), so the cache below is
attached to `app.state` and actually holds for the process lifetime — the
guarantee the legacy code only had by accident on a warm Lambda.

Uses a call-count-instrumented fake `credentials.sismo()` override (no real
service-account JSON, no network) to prove the TTL cache skips Firestore on
a repeat request within the window.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app

FAKE_CLAIMS_VIEWER = {"sub": "uid-viewer", "email": "someone@gmail.com"}
FAKE_CLAIMS_ADMIN = {
    "sub": "uid-admin",
    "email": "admin@example.com",
    "role": "admin",
}

_FAKE_DOCS: list[dict[str, Any]] = [
    {"registro_id": "1", "tiene_sticker": True},
    {"registro_id": "2", "tiene_sticker": False},
    {"registro_id": None, "tiene_sticker": True},  # dropped — no registro_id
]


class _FakeDoc:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]], calls: list[int]) -> None:
        self._docs = docs
        self._calls = calls

    def get(self) -> list[_FakeDoc]:
        self._calls.append(1)
        return [_FakeDoc(d) for d in self._docs]


class _FakeFirestore:
    def __init__(self, docs: list[dict[str, Any]], calls: list[int]) -> None:
        self._docs = docs
        self._calls = calls

    def collection(self, name: str) -> _FakeCollection:
        assert name == "sticker_matches"
        return _FakeCollection(self._docs, self._calls)


class _FakeSismoClients:
    def __init__(self, docs: list[dict[str, Any]], calls: list[int]) -> None:
        self.firestore = _FakeFirestore(docs, calls)
        self.app = None


def _app(monkeypatch, calls: list[int]) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    credentials.s3.cache_clear()
    monkeypatch.setattr(credentials, "sismo", lambda: _FakeSismoClients(_FAKE_DOCS, calls))
    return create_app()


def _client(monkeypatch) -> TestClient:
    return TestClient(_app(monkeypatch, []))


def _authed_client(monkeypatch, claims: dict[str, Any], calls: list[int]) -> TestClient:
    app = _app(monkeypatch, calls)
    app.dependency_overrides[current_claims] = lambda: claims
    return TestClient(app)


def test_any_authenticated_role_gets_200(monkeypatch):
    for claims in (FAKE_CLAIMS_VIEWER, FAKE_CLAIMS_ADMIN):
        calls: list[int] = []
        client = _authed_client(monkeypatch, claims, calls)

        resp = client.get("/sticker-status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["total"] == 2
        assert body["con"] == 1
        assert body["con_sticker"] == ["1"]


def test_unauthenticated_is_rejected(monkeypatch):
    client = _client(monkeypatch)

    resp = client.get("/sticker-status")

    assert resp.status_code == 401


def test_firestore_exception_becomes_502_not_a_bare_crash(monkeypatch):
    """A 429/ResourceExhausted (or any other) Firestore exception must
    surface as a normal HTTPException (502, CORS headers intact) — not
    propagate unhandled into a bare 500 that Starlette's default error
    handler serves with NO CORS headers, which the browser then
    misreports as "blocked by CORS policy" instead of the real cause."""
    calls: list[int] = []
    app = _app(monkeypatch, calls)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    from app.routers import sticker_status as sticker_status_module

    monkeypatch.setattr(sticker_status_module, "_read_coverage", lambda db: boom())
    client = TestClient(app)

    resp = client.get("/sticker-status")

    assert resp.status_code == 502
    assert "Quota exceeded" in resp.json()["detail"]


def test_cached_response_served_without_new_firestore_read(monkeypatch):
    calls: list[int] = []
    app = _app(monkeypatch, calls)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER
    client = TestClient(app)

    first = client.get("/sticker-status")
    second = client.get("/sticker-status")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1
