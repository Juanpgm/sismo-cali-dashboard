"""POST /refresh — design.md ADR-6; backend-platform spec
"Admin-gated route rejects non-admin" (`/refresh` row).

Mocks the Railway GraphQL client (`app.routers.refresh._railway_graphql`) —
no real network in tests. Asserts: admin token → 202 redeploying ALL THREE
15-min cron services (dashboard-refresh + cruce-sticker + cruce-gestion —
the "Actualizar datos" button force-runs the whole 15-min fleet); a
secondary cron failure is fail-soft (still 202, error surfaced); the
primary (dashboard-refresh) failure → 502; non-admin → 403, no Railway
call; unauthenticated → 401, no Railway call.
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


def test_admin_token_redeploys_all_three_crons(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_railway_graphql(token, query, variables):
        calls.append((token, variables))
        return {"serviceInstanceRedeploy": f"deploy-{variables['s'][:8]}"}

    monkeypatch.setattr(refresh, "_railway_graphql", _fake_railway_graphql)
    client = _admin_client(monkeypatch)

    resp = client.post("/refresh")

    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is True
    # Primary deploymentId kept for frontend backward-compat.
    assert body["deploymentId"] == f"deploy-{refresh.DEFAULT_SERVICE_ID[:8]}"
    # All three 15-min crons redeployed, once each.
    assert len(calls) == 3
    assert all(token == "fake-railway-token" for token, _ in calls)
    service_ids = {variables["s"] for _, variables in calls}
    assert service_ids == {
        refresh.DEFAULT_SERVICE_ID,
        refresh.DEFAULT_STICKER_SERVICE_ID,
        refresh.DEFAULT_CRUCE_SERVICE_ID,
    }
    assert set(body["deployments"]) == {
        "dashboard-refresh",
        "cruce-sticker",
        "cruce-gestion",
    }
    assert body["errors"] == {}


def test_secondary_cron_failure_is_fail_soft(monkeypatch):
    async def _fake_railway_graphql(token, query, variables):
        if variables["s"] == refresh.DEFAULT_STICKER_SERVICE_ID:
            raise RuntimeError("Railway API 500: sticker boom")
        return {"serviceInstanceRedeploy": f"deploy-{variables['s'][:8]}"}

    monkeypatch.setattr(refresh, "_railway_graphql", _fake_railway_graphql)
    client = _admin_client(monkeypatch)

    resp = client.post("/refresh")

    # Primary (dashboard-refresh) succeeded → still 202, cross failure surfaced.
    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is True
    assert body["deploymentId"] == f"deploy-{refresh.DEFAULT_SERVICE_ID[:8]}"
    assert body["deployments"]["dashboard-refresh"] == f"deploy-{refresh.DEFAULT_SERVICE_ID[:8]}"
    assert body["deployments"]["cruce-sticker"] is None
    assert "cruce-sticker" in body["errors"]


def test_primary_cron_failure_returns_502(monkeypatch):
    async def _fake_railway_graphql(token, query, variables):
        if variables["s"] == refresh.DEFAULT_SERVICE_ID:
            raise RuntimeError("Railway API 500: primary boom")
        return {"serviceInstanceRedeploy": "deploy-secondary"}

    monkeypatch.setattr(refresh, "_railway_graphql", _fake_railway_graphql)
    client = _admin_client(monkeypatch)

    resp = client.post("/refresh")

    assert resp.status_code == 502


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
