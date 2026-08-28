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
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
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


# Regression: CORS_ALLOW_METHODS omitted PATCH/DELETE despite real routes
# using them (survey_cali.py, puntos_solicitados.py, panel_representante.py)
# — every such request failed silently at the browser's OWN preflight
# (OPTIONS 400) before ever reaching the route, so curl/pytest-against-the-
# route-directly always looked fine while the actual browser UI (e.g.
# puntos-solicitados' "Eliminar punto"/edit) was broken in production.
# The real client-facing symptom is exactly this preflight, so assert THAT,
# not just that config.py's tuple happens to contain the string.
def test_preflight_allows_patch_and_delete(monkeypatch):
    client = _client(monkeypatch)
    for method in ("PATCH", "DELETE"):
        resp = client.options(
            "/puntos-solicitados/some-id",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert resp.status_code == 200, (method, resp.status_code, resp.text)
        allowed = resp.headers.get("access-control-allow-methods", "")
        assert method in allowed, (method, allowed)


def test_cookie_only_request_is_rejected_on_authenticated_route(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    app = create_app()

    @app.get("/stub-auth")
    def stub_auth(claims: dict = Depends(require_auth)):
        return {"sub": claims.get("sub")}

    client = TestClient(app)

    resp = client.get("/stub-auth", headers={"Cookie": "session=whatever-a-cookie-carries"})

    assert resp.status_code == 401
