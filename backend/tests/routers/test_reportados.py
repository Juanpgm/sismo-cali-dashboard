"""GET /reportados (test-first addition beyond tasks.md's literal 3.5 GREEN
— no dedicated RED task is listed for this router in tasks.md, same as
batch-1b's 1.9/1.13; Strict TDD Mode is active for this batch, so this file
is written FIRST and confirmed failing before `app/routers/reportados.py`
exists) — design.md ADR-5; backend-platform spec "Public route requires no
token", "reportados responds fast from snapshot", "Cache-Control headers
preserved".

`create_app()` always attaches a fresh, empty `app.state.reportados_snapshot`
synchronously (NOT deferred to the `lifespan` background task) precisely so
router tests can populate/replace it directly via `TestClient(app)` without
needing `with TestClient(app) as client:` to trigger lifespan startup — the
same pattern every other router test file in this suite already uses.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.snapshot import ReportadosSnapshot, SnapshotStaleError, SnapshotUnavailableError

SAMPLE_PAYLOAD = {
    "ok": True,
    "generado": "2026-08-25T12:00:00Z",
    "fuente": "api:informe/json",
    "total": 42,
    "inmuebles": 30,
    "por_estadoVerificacion": {"Reportado": 20, "Verificado": 22},
}


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    return TestClient(create_app())


def test_no_authorization_header_still_succeeds_when_snapshot_ready(monkeypatch):
    client = _client(monkeypatch)
    client.app.state.reportados_snapshot.store(SAMPLE_PAYLOAD)

    resp = client.get("/reportados")

    assert resp.status_code == 200
    assert resp.json() == SAMPLE_PAYLOAD


def test_response_carries_x_snapshot_age_header(monkeypatch):
    client = _client(monkeypatch)
    client.app.state.reportados_snapshot.store(SAMPLE_PAYLOAD)

    resp = client.get("/reportados")

    assert "x-snapshot-age" in {k.lower() for k in resp.headers}
    assert int(resp.headers["x-snapshot-age"]) >= 0


def test_response_preserves_legacy_cache_control_header(monkeypatch):
    client = _client(monkeypatch)
    client.app.state.reportados_snapshot.store(SAMPLE_PAYLOAD)

    resp = client.get("/reportados")

    assert resp.headers["cache-control"] == "public, s-maxage=900, stale-while-revalidate=86400"


def test_no_snapshot_yet_returns_503_with_retry_after(monkeypatch):
    client = _client(monkeypatch)
    # Fresh app.state.reportados_snapshot from create_app() — nothing stored.

    resp = client.get("/reportados")

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "60"


def test_stale_snapshot_returns_503_with_retry_after(monkeypatch):
    import time

    client = _client(monkeypatch)
    client.app.state.reportados_snapshot.store(
        SAMPLE_PAYLOAD, fetched_at=time.monotonic() - 90_000
    )

    resp = client.get("/reportados")

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "60"


def test_reportados_router_declares_no_credential_client(monkeypatch):
    # backend-platform spec "A route cannot reach an undeclared client":
    # reportados never touches Firestore/S3, so it must not appear in
    # create_app()'s startup credential union via its own declaration.
    from app.routers import reportados

    assert reportados.REQUIRED_CLIENTS == ()
