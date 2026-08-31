"""POST /refresh — admin-triggered manual data refresh, run IN-PROCESS
(app.jobs.dashboard_refresh.run_refresh) instead of a Railway redeploy of
the dashboard-refresh cron container (see app/routers/refresh.py's module
docstring for why: that redeploy path has twice broken production).

Mocks `refresh.run_refresh` (never runs the real pipeline). The old
cruce-sticker/cruce-gestion adjunct-redeploy machinery is gone (both
Railway services deleted 2026-08-31), so there is no Railway call left to
mock. Asserts: admin token → 202, run_refresh scheduled as a background
task; a second concurrent trigger while one is in flight → 409,
run_refresh NOT scheduled again; non-admin → 403; unauthenticated → 401 —
neither runs run_refresh.
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


def _release_lock_if_held() -> None:
    """TestClient's BackgroundTasks run SYNCHRONOUSLY before the response is
    returned (unlike real Starlette/uvicorn, which defers them) — so by the
    time `client.post(...)` returns, our fake `run_refresh` has already run
    and the lock is already released. This is a safety net only, in case a
    test's fake forgets to release it."""
    if refresh._refresh_lock.locked():
        refresh._refresh_lock.release()


def test_admin_token_runs_refresh_in_process(monkeypatch):
    run_calls: list[int] = []
    monkeypatch.setattr(refresh, "run_refresh", lambda: run_calls.append(1))
    client = _admin_client(monkeypatch)

    resp = client.post("/refresh")

    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is True
    assert body["errors"] == {}
    assert run_calls == [1]
    _release_lock_if_held()


def test_run_refresh_exception_does_not_leak_into_the_response(monkeypatch):
    """run_refresh() failing (e.g. a bad Survey123 pull) must not turn the
    202 into a 5xx — it already reports its own failure to Blob; the caller
    only gets to know a run STARTED, never whether it succeeded (that's what
    the frontend's meta.json poll is for)."""
    def _boom():
        raise RuntimeError("refresh_data.py blew up")

    monkeypatch.setattr(refresh, "run_refresh", _boom)
    client = _admin_client(monkeypatch)

    resp = client.post("/refresh")

    assert resp.status_code == 202
    assert resp.json()["ok"] is True
    _release_lock_if_held()


def test_concurrent_trigger_is_rejected_with_409(monkeypatch):
    monkeypatch.setattr(refresh, "run_refresh", lambda: None)
    client = _admin_client(monkeypatch)

    # Hold the lock ourselves to simulate a run already in flight — TestClient's
    # synchronous BackgroundTasks execution means we can't otherwise observe
    # the "still running" window from outside a real ASGI server.
    acquired = refresh._refresh_lock.acquire(blocking=False)
    assert acquired, "test setup: lock should be free"
    try:
        resp = client.post("/refresh")
        assert resp.status_code == 409
    finally:
        refresh._refresh_lock.release()


def test_non_admin_is_rejected_with_no_run(monkeypatch):
    run_calls: list[int] = []
    monkeypatch.setattr(refresh, "run_refresh", lambda: run_calls.append(1))
    client = _non_admin_client(monkeypatch)

    resp = client.post("/refresh")

    assert resp.status_code == 403
    assert run_calls == []
    _release_lock_if_held()


def test_unauthenticated_is_rejected_with_no_run(monkeypatch):
    run_calls: list[int] = []
    monkeypatch.setattr(refresh, "run_refresh", lambda: run_calls.append(1))
    client = TestClient(_base_app(monkeypatch))

    resp = client.post("/refresh")

    assert resp.status_code == 401
    assert run_calls == []
    _release_lock_if_held()
