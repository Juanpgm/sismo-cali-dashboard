"""CORS allowlist + cookie-only rejection (design.md ADR-7; backend-platform
spec: "Universal Explicit CORS Allowlist" — all 3 scenarios).

Runs against 1.12's real `create_app()` (the actual `CORSMiddleware` wiring
from `app/config.py`), NOT a separate stub app. A stub authenticated route
(`Depends(require_auth)`, task 1.9) is attached directly to the app
instance returned by `create_app()` purely as a test fixture for the
cookie-vs-Bearer scenario — no permanent router lands under `app/routers/`
(endpoint ports start at slice 2, out of this slice's scope).
"""
from __future__ import annotations

from fastapi import Depends
from fastapi.testclient import TestClient

from app.auth.deps import require_auth
from app.main import create_app

ALLOWED_ORIGIN = "https://sismo-cali-dashboard.vercel.app"
UNLISTED_ORIGIN = "https://evil.example.com"
LOCALHOST_DEV_ORIGIN = "http://localhost:5173"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    return TestClient(create_app())


def test_allowed_origin_receives_cors_header(monkeypatch):
    resp = _client(monkeypatch).get("/health", headers={"Origin": ALLOWED_ORIGIN})

    assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN


def test_unlisted_origin_gets_no_permitting_cors_header(monkeypatch):
    resp = _client(monkeypatch).get("/health", headers={"Origin": UNLISTED_ORIGIN})

    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_localhost_dev_origin_is_allowed_via_regex(monkeypatch):
    resp = _client(monkeypatch).get("/health", headers={"Origin": LOCALHOST_DEV_ORIGIN})

    assert resp.headers.get("access-control-allow-origin") == LOCALHOST_DEV_ORIGIN


def test_cookie_only_request_is_rejected_on_authenticated_route(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    app = create_app()

    @app.get("/stub-auth")
    def stub_auth(claims: dict = Depends(require_auth)):
        return {"sub": claims.get("sub")}

    client = TestClient(app)

    resp = client.get("/stub-auth", headers={"Cookie": "session=whatever-a-cookie-carries"})

    assert resp.status_code == 401
