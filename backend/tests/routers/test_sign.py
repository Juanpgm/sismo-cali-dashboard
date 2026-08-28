"""POST /api/sign (RED first) — design.md ADR-3/ADR-4; backend-platform spec
"Route Parity Across Consolidated Endpoints" (`/sign` row); inspection-photo-
capture spec "Unified Token Verification For Signer", "Presign Acceptance
Semantics Unchanged".

Ports `services/photo-signer/api/sign.js`'s acceptance semantics (codigo
regex, slot range, presigned-URL shape) with ONE change: token verification
moves from the legacy signer's independent `accounts:lookup` REST call onto
`Depends(require_auth)` — the same RS256 verifier every other route uses —
and the request stops carrying `idToken` in the JSON body, using the
`Authorization: Bearer` header instead.

No real AWS network call: `generate_presigned_url` is a pure local HMAC
computation (confirmed empirically — boto3 never contacts AWS for this
call), so these tests exercise the REAL `credentials.s3()` accessor with
fake/dummy credentials rather than a hand-rolled fake S3 client, per the
apply-agent's scope instructions.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.jobs.planeacion_cruce import clave_integracion
from app.main import create_app

VALID_CODIGO = "76001-1-0040001"
# Real shape minted by puntos_solicitados.crear_punto_solicitado (ADR-3):
# clave_integracion('solicitado', sid) — same function, not hand-typed.
VALID_SOLICITADO_CODIGO = clave_integracion("solicitado", "abc123XYZ0")
FAKE_CLAIMS = {"sub": "uid-inspector", "email": "inspector@sismocali.gov.co"}


def _app(monkeypatch) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    monkeypatch.setenv("SIGNER_S3_REGION", "us-east-1")
    credentials.s3.cache_clear()
    return create_app()


def _client(monkeypatch) -> TestClient:
    return TestClient(_app(monkeypatch))


def _authed_client(monkeypatch) -> TestClient:
    app = _app(monkeypatch)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS
    return TestClient(app)


def test_valid_request_returns_presigned_upload_and_public_urls(monkeypatch):
    client = _authed_client(monkeypatch)

    resp = client.post("/api/sign", json={"codigo": VALID_CODIGO, "slot": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["uploadUrl"].startswith(
        "https://test-sismo-fotos.s3.amazonaws.com/evaluaciones/"
        f"{VALID_CODIGO}/foto_1.jpg"
    )
    assert body["publicUrl"] == (
        f"https://test-sismo-fotos.s3.amazonaws.com/evaluaciones/{VALID_CODIGO}/foto_1.jpg"
    )


def test_missing_bearer_is_rejected(monkeypatch):
    client = _client(monkeypatch)

    resp = client.post("/api/sign", json={"codigo": VALID_CODIGO, "slot": 1})

    assert resp.status_code == 401


def test_invalid_bearer_is_rejected(monkeypatch):
    client = _client(monkeypatch)

    resp = client.post(
        "/api/sign",
        json={"codigo": VALID_CODIGO, "slot": 1},
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert resp.status_code == 401


def test_bad_codigo_is_rejected(monkeypatch):
    client = _authed_client(monkeypatch)

    resp = client.post("/api/sign", json={"codigo": "not-a-valid-codigo", "slot": 1})

    assert resp.status_code == 400


def test_out_of_range_slot_is_rejected(monkeypatch):
    client = _authed_client(monkeypatch)

    resp = client.post("/api/sign", json={"codigo": VALID_CODIGO, "slot": 999})

    assert resp.status_code == 400


def test_zero_slot_is_rejected(monkeypatch):
    client = _authed_client(monkeypatch)

    resp = client.post("/api/sign", json={"codigo": VALID_CODIGO, "slot": 0})

    assert resp.status_code == 400


def test_puntos_solicitados_codigo_is_accepted_and_keyed_under_solicitados(monkeypatch):
    """puntos-solicitados gap-fix: web/js/puntos_solicitados.js sends the
    point's `clave_integracion` (PLN-<slug>-<digest>) as `codigo`."""
    client = _authed_client(monkeypatch)

    resp = client.post("/api/sign", json={"codigo": VALID_SOLICITADO_CODIGO, "slot": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["uploadUrl"].startswith(
        "https://test-sismo-fotos.s3.amazonaws.com/solicitados/"
        f"{VALID_SOLICITADO_CODIGO}/foto_1.jpg"
    )
    assert body["publicUrl"] == (
        f"https://test-sismo-fotos.s3.amazonaws.com/solicitados/{VALID_SOLICITADO_CODIGO}/foto_1.jpg"
    )


def test_evaluaciones_codigo_still_keyed_under_evaluaciones(monkeypatch):
    """Regression: adding the puntos_solicitados shape must not touch the
    existing evaluaciones key path."""
    client = _authed_client(monkeypatch)

    resp = client.post("/api/sign", json={"codigo": VALID_CODIGO, "slot": 1})

    assert resp.status_code == 200
    public_url = resp.json()["publicUrl"]
    assert f"/evaluaciones/{VALID_CODIGO}/foto_1.jpg" in public_url
    assert "/solicitados/" not in public_url


def test_malformed_solicitado_shaped_codigo_is_still_rejected(monkeypatch):
    """Right prefix, wrong digest length — fails both CODIGO_RE and the
    puntos_solicitados structural check, so it must still 400."""
    client = _authed_client(monkeypatch)

    resp = client.post("/api/sign", json={"codigo": "PLN-ABC123-XYZ", "slot": 1})

    assert resp.status_code == 400
