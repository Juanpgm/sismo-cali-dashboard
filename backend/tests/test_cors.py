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


# ── Conditional GET across origins (design D27, tasks 10b.10-10b.12) ─────────
#
# The frontend (Vercel) and the backend (Railway) are cross-origin: without
# `If-None-Match` in the allowlist the preflight of a conditional GET fails and
# the fetch is blocked; without `ETag` exposed JS cannot read the validator.

STICKERS_URL = "/stickers-atencionsismo"


def _preflight(client, *, origin=ALLOWED_ORIGIN, headers="if-none-match", method="GET", path=STICKERS_URL):
    return client.options(path, headers={
        "Origin": origin, "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    })


def _allowed_headers(resp) -> set[str]:
    return {h.strip().lower() for h in resp.headers.get("access-control-allow-headers", "").split(",") if h.strip()}


def test_cors_preflight_allows_if_none_match_and_exposes_etag(monkeypatch):
    client = _client(monkeypatch)
    pre = _preflight(client)
    assert pre.status_code == 200, pre.text
    assert "if-none-match" in _allowed_headers(pre)

    actual = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert actual.headers.get("access-control-expose-headers") == "ETag"


def test_preflight_for_a_conditional_get_carrying_all_three_headers_passes(monkeypatch):
    pre = _preflight(_client(monkeypatch), headers="authorization,content-type,if-none-match")
    assert pre.status_code == 200, pre.text
    assert {"authorization", "content-type", "if-none-match"} <= _allowed_headers(pre)


def test_authorization_and_content_type_remain_allowed_and_no_wildcard_crept_in(monkeypatch):
    pre = _preflight(_client(monkeypatch), headers="authorization,content-type")
    assert pre.status_code == 200
    allowed = _allowed_headers(pre)
    assert {"authorization", "content-type"} <= allowed and "*" not in allowed


def test_a_header_that_was_never_allowed_is_still_rejected_at_the_preflight(monkeypatch):
    client = _client(monkeypatch)
    for header in ("x-evil", "if-match", "authorization,x-evil"):
        pre = _preflight(client, headers=header)
        assert pre.status_code == 400 and "headers" in pre.text.lower(), (header, pre.status_code)


def test_a_disallowed_origin_is_still_rejected_and_never_gets_an_allow_origin(monkeypatch):
    """Starlette's `simple_headers` (incl. Expose-Headers) ride on every response
    that carries an Origin; without `Access-Control-Allow-Origin` a browser
    ignores them, so the guarantee that matters is the absent allow-origin."""
    client = _client(monkeypatch)
    pre = _preflight(client, origin=UNLISTED_ORIGIN)
    assert pre.status_code == 400 and "access-control-allow-origin" not in pre.headers
    actual = client.get("/health", headers={"Origin": UNLISTED_ORIGIN})
    assert "access-control-allow-origin" not in {k.lower() for k in actual.headers}


def test_only_etag_is_exposed_and_the_localhost_dev_origin_gets_it_too(monkeypatch):
    client = _client(monkeypatch)
    for origin in (ALLOWED_ORIGIN, LOCALHOST_DEV_ORIGIN):
        exposed = client.get("/health", headers={"Origin": origin}).headers.get("access-control-expose-headers", "")
        assert [h.strip() for h in exposed.split(",")] == ["ETag"], (origin, exposed)


def test_the_config_allowlist_keeps_its_original_entries_and_gains_only_if_none_match():
    from app import config

    assert config.CORS_ALLOW_HEADERS == ("Authorization", "Content-Type", "If-None-Match")
