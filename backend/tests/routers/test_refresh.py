"""POST /refresh (RED first) — design.md ADR-6; backend-platform spec
"Admin-gated route rejects non-admin" (`/refresh` row).

Mocks the Railway GraphQL client (`app.routers.refresh._railway_graphql`) —
no real network in tests. Asserts: admin token → 202 with `deploymentId`,
exactly ONE Railway call (the `dashboard-refresh` redeploy — the legacy
fail-soft `cruce-gestion` second redeploy, api/refresh.js:169-174, is NOT
ported per proposal.md Scope Exclusion Addendum Extension 2 item 5);
non-admin → 403, no Railway call; unauthenticated → 401, no Railway call.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app
from app.routers import refresh

FAKE_CLAIMS_ADMIN = {"sub": "uid-admin", "email": "admin@example.com", "role": "admin"}
FAKE_CLAIMS_VIEWER = {"sub": "uid-viewer", "email": "someone@gmail.com"}


def _base_app(monkeypatch) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    monkeypatch.setenv("RAILWAY_API_TOKEN", "fake-railway-token")
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


def test_admin_token_gets_202_with_deployment_id(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_railway_graphql(token, query, variables):
        calls.append((token, variables))
        return {"serviceInstanceRedeploy": "deploy-123"}

    monkeypatch.setattr(refresh, "_railway_graphql", _fake_railway_graphql)
    client = _admin_client(monkeypatch)

    resp = client.post("/refresh")

    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is True
    assert body["deploymentId"] == "deploy-123"
    # Exactly ONE Railway call — dashboard-refresh only, no cruce-gestion.
    assert len(calls) == 1
    assert calls[0][0] == "fake-railway-token"


def test_non_admin_is_rejected_with_no_railway_call(monkeypatch):
    calls: list[int] = []

    async def _counting_railway_graphql(token, query, variables):
        calls.append(1)
        return {"serviceInstanceRedeploy": "deploy-should-not-happen"}

    monkeypatch.setattr(refresh, "_railway_graphql", _counting_railway_graphql)
    client = _non_admin_client(monkeypatch)

    resp = client.post("/refresh")

    assert resp.status_code == 403
    assert calls == []


def test_unauthenticated_is_rejected_with_no_railway_call(monkeypatch):
    calls: list[int] = []

    async def _counting_railway_graphql(token, query, variables):
        calls.append(1)
        return {"serviceInstanceRedeploy": "deploy-should-not-happen"}

    monkeypatch.setattr(refresh, "_railway_graphql", _counting_railway_graphql)
    client = TestClient(_base_app(monkeypatch))

    resp = client.post("/refresh")

    assert resp.status_code == 401
    assert calls == []
