"""FastAPI auth dependencies (design.md ADR-3): `require_auth`,
`require_role`, `current_claims` — the foundation every later slice's
`Depends(...)` matrix attaches to (backend-platform spec: "Route Parity
Across Consolidated Endpoints", auth-level column).

Per ADR-8's routers-testing convention, this suite builds a stub app and
overrides `current_claims` via FastAPI's `dependency_overrides` to inject
fake verified claims — no real Bearer tokens, no cert fetch here (that is
`test_verify.py`'s job).
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims, require_auth, require_role


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/stub-auth")
    def stub_auth(claims: dict = Depends(require_auth)):
        return {"sub": claims.get("sub")}

    @app.get("/stub-admin")
    def stub_admin(claims: dict = Depends(require_role("admin"))):
        return {"sub": claims.get("sub")}

    return app


def test_require_auth_rejects_missing_bearer_header():
    client = TestClient(_make_app())

    resp = client.get("/stub-auth")

    assert resp.status_code == 401


def test_require_auth_rejects_invalid_token():
    client = TestClient(_make_app())

    resp = client.get("/stub-auth", headers={"Authorization": "Bearer not-a-real-token"})

    assert resp.status_code == 401


def test_require_auth_accepts_valid_claims_via_dependency_override():
    app = _make_app()
    app.dependency_overrides[current_claims] = lambda: {"sub": "uid-abc", "email": "x@example.com"}
    client = TestClient(app)

    resp = client.get("/stub-auth")

    assert resp.status_code == 200
    assert resp.json() == {"sub": "uid-abc"}


def test_require_role_admin_accepts_admin_claims():
    app = _make_app()
    app.dependency_overrides[current_claims] = lambda: {
        "sub": "uid-admin",
        "email": "boss@example.com",
        "role": "admin",
    }
    client = TestClient(app)

    resp = client.get("/stub-admin")

    assert resp.status_code == 200


def test_require_role_admin_rejects_non_admin_claims():
    app = _make_app()
    app.dependency_overrides[current_claims] = lambda: {
        "sub": "uid-usuario",
        "email": "someone@example.com",
        "firebase": {"sign_in_provider": "password"},
    }
    client = TestClient(app)

    resp = client.get("/stub-admin")

    assert resp.status_code == 403
