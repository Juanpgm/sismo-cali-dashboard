"""GET /source-status (RED first) — design.md ADR-4; backend-platform spec
"Admin-gated route rejects non-admin" (`/source-status` row).

Ports `api/source-status.js` verbatim: a live connectivity check for the
atencionsismo API, backing the "Analista" tab's atencionsismo status row. It
re-runs `app/services/atencionsismo.py`'s cheap one-minute `probe_api()` —
the SAME probe the day-walk already runs before its full range fetch — so a
snapshot proves the pipeline ran at some point, and this endpoint answers
whether the source is reachable RIGHT NOW. A down/misconfigured upstream is
a successfully-determined FACT (`ok: false`, still HTTP 200), never a 5xx —
matching the legacy handler's `res.status(200).json({ok: false, ...})`.

Fakes `atencionsismo.probe_api`/`credentials_from_env` (no real network, no
VISITADOS_API_PASS needed) so both the success and failure paths are
deterministic.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app
from app.services import atencionsismo

FAKE_CLAIMS_ADMIN = {
    "sub": "uid-admin",
    "email": "admin@example.com",
    "role": "admin",
}
FAKE_CLAIMS_VIEWER = {"sub": "uid-viewer", "email": "someone@gmail.com"}


def _base_app(monkeypatch) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    credentials.s3.cache_clear()
    return create_app()


def _admin_client(monkeypatch) -> TestClient:
    app = _base_app(monkeypatch)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN
    return TestClient(app)


def _non_admin_client(monkeypatch) -> TestClient:
    app = _base_app(monkeypatch)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER
    return TestClient(app)


def test_admin_token_gets_200_ok_true_when_source_reachable(monkeypatch):
    async def _fake_probe(client: Any, user: str, password: str) -> None:
        return None

    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("user", "pass"))
    monkeypatch.setattr(atencionsismo, "probe_api", _fake_probe)
    client = _admin_client(monkeypatch)

    resp = client.get("/source-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "conectado"
    assert body["detail"] is None
    assert body["checked_at"]


def test_admin_token_gets_200_ok_false_when_source_unreachable(monkeypatch):
    async def _fake_probe(client: Any, user: str, password: str) -> None:
        raise atencionsismo.ApiUnavailableError("API no disponible (HTTP 503)", status=503)

    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("user", "pass"))
    monkeypatch.setattr(atencionsismo, "probe_api", _fake_probe)
    client = _admin_client(monkeypatch)

    resp = client.get("/source-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "con errores"
    assert "503" in body["detail"]


def test_non_admin_is_rejected_with_no_probe_call(monkeypatch):
    calls: list[int] = []

    async def _counting_probe(client: Any, user: str, password: str) -> None:
        calls.append(1)

    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("user", "pass"))
    monkeypatch.setattr(atencionsismo, "probe_api", _counting_probe)
    client = _non_admin_client(monkeypatch)

    resp = client.get("/source-status")

    assert resp.status_code == 403
    assert calls == []


def test_unauthenticated_is_rejected(monkeypatch):
    client = TestClient(_base_app(monkeypatch))

    resp = client.get("/source-status")

    assert resp.status_code == 401
