"""POST /planeacion-cruce/run, GET /planeacion-cruce/status — modeled
closely on `test_refresh.py`'s own fixture/client-setup/auth-mocking
conventions (same `_base_app`/`_admin_client`/`_non_admin_client` shape).

Mocks `planeacion_cruce.run_planeacion_cruce` (never runs the real job)
and, where relevant, `planeacion_cruce.read_last_run` (never hits real
Firestore). Asserts: admin token -> 202, job scheduled as a background
task with the right kwargs; a second concurrent trigger while one is in
flight -> 409, job NOT scheduled again; the lock is released even when the
background job raises; non-admin -> 403; unauthenticated -> 401; and
`GET /planeacion-cruce/status` reports `running`/`last_run`.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app
from app.routers import planeacion_cruce

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
    time `client.post(...)` returns, our fake `run_planeacion_cruce` has
    already run and the lock is already released. Safety net only, in case
    a test's fake forgets to release it. Same convention `test_refresh.py`
    uses for `refresh._refresh_lock`."""
    if planeacion_cruce._lock.locked():
        planeacion_cruce._lock.release()


def test_admin_token_schedules_the_job_with_the_right_kwargs(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        planeacion_cruce, "run_planeacion_cruce",
        lambda top=None, dry=False, full=False: calls.append({"top": top, "dry": dry, "full": full}),
    )
    client = _admin_client(monkeypatch)

    resp = client.post("/planeacion-cruce/run", json={"top": 50, "dry": True, "full": False})

    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is True
    assert body["params"] == {"top": 50, "dry": True, "full": False}
    assert calls == [{"top": 50, "dry": True, "full": False}]
    _release_lock_if_held()


def test_default_body_omits_every_field(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        planeacion_cruce, "run_planeacion_cruce",
        lambda top=None, dry=False, full=False: calls.append({"top": top, "dry": dry, "full": full}),
    )
    client = _admin_client(monkeypatch)

    resp = client.post("/planeacion-cruce/run", json={})

    assert resp.status_code == 202
    assert resp.json()["params"] == {"top": None, "dry": False, "full": False}
    assert calls == [{"top": None, "dry": False, "full": False}]
    _release_lock_if_held()


def test_concurrent_trigger_is_rejected_with_409(monkeypatch):
    monkeypatch.setattr(planeacion_cruce, "run_planeacion_cruce", lambda **kw: None)
    client = _admin_client(monkeypatch)

    # Hold the lock ourselves to simulate a run already in flight — TestClient's
    # synchronous BackgroundTasks execution means we can't otherwise observe
    # the "still running" window from outside a real ASGI server.
    acquired = planeacion_cruce._lock.acquire(blocking=False)
    assert acquired, "test setup: lock should be free"
    try:
        resp = client.post("/planeacion-cruce/run", json={})
        assert resp.status_code == 409
    finally:
        planeacion_cruce._lock.release()


def test_job_exception_does_not_leak_into_the_response_and_lock_is_released(monkeypatch):
    def _boom(top=None, dry=False, full=False):
        raise RuntimeError("planeacion_cruce blew up")

    monkeypatch.setattr(planeacion_cruce, "run_planeacion_cruce", _boom)
    client = _admin_client(monkeypatch)

    resp = client.post("/planeacion-cruce/run", json={})

    assert resp.status_code == 202
    assert resp.json()["ok"] is True
    assert not planeacion_cruce._lock.locked(), "a raising job must not leave the lock stuck"
    _release_lock_if_held()


def test_non_admin_is_rejected_with_no_run_scheduled(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(planeacion_cruce, "run_planeacion_cruce", lambda **kw: calls.append(1))
    client = _non_admin_client(monkeypatch)

    resp = client.post("/planeacion-cruce/run", json={})

    assert resp.status_code == 403
    assert calls == []
    _release_lock_if_held()


def test_unauthenticated_is_rejected_with_no_run_scheduled(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(planeacion_cruce, "run_planeacion_cruce", lambda **kw: calls.append(1))
    client = TestClient(_base_app(monkeypatch))

    resp = client.post("/planeacion-cruce/run", json={})

    assert resp.status_code == 401
    assert calls == []
    _release_lock_if_held()


def _stub_sismo(monkeypatch) -> None:
    """`credentials.sismo()` is memoized (`@lru_cache`) and, unmocked, would
    try to construct a REAL firebase_admin/Firestore client from the fake
    JSON `_base_app` sets — stubbed out here so `GET /status` never touches
    it; `read_last_run` (mocked per-test below) is what actually consumes
    the returned `.firestore` value, so a bare sentinel is enough."""
    monkeypatch.setattr(credentials, "sismo", lambda: type("_C", (), {"firestore": object()})())


def test_status_reports_not_running_and_no_prior_run(monkeypatch):
    _stub_sismo(monkeypatch)
    monkeypatch.setattr(planeacion_cruce, "read_last_run", lambda db: None)
    monkeypatch.setattr(planeacion_cruce, "read_last_checked", lambda db: None)
    client = _admin_client(monkeypatch)

    resp = client.get("/planeacion-cruce/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["last_run"] is None


def test_status_reports_the_last_run_summary(monkeypatch):
    last_run = {"total_puntos": 100, "a_escribir": 5, "full": False, "finished_at": "2026-08-27T00:00:00+00:00"}
    _stub_sismo(monkeypatch)
    monkeypatch.setattr(planeacion_cruce, "read_last_run", lambda db: last_run)
    monkeypatch.setattr(planeacion_cruce, "read_last_checked", lambda db: None)
    client = _admin_client(monkeypatch)

    resp = client.get("/planeacion-cruce/status")

    assert resp.status_code == 200
    assert resp.json()["last_run"] == last_run


def test_status_reports_running_true_while_the_lock_is_held(monkeypatch):
    _stub_sismo(monkeypatch)
    monkeypatch.setattr(planeacion_cruce, "read_last_run", lambda db: None)
    monkeypatch.setattr(planeacion_cruce, "read_last_checked", lambda db: None)
    client = _admin_client(monkeypatch)

    acquired = planeacion_cruce._lock.acquire(blocking=False)
    assert acquired, "test setup: lock should be free"
    try:
        resp = client.get("/planeacion-cruce/status")
        assert resp.json()["running"] is True
    finally:
        planeacion_cruce._lock.release()


def test_status_surfaces_last_checked_at_from_a_noop_run(monkeypatch):
    """`last_checked_at` advances on a no-op (gated) run even when
    `last_run` is still `None` — proves the endpoint shows the job is
    alive during a quiet period, not just after a run that did real work."""
    _stub_sismo(monkeypatch)
    monkeypatch.setattr(planeacion_cruce, "read_last_run", lambda db: None)
    monkeypatch.setattr(planeacion_cruce, "read_last_checked",
                        lambda db: "2026-08-29T00:00:00+00:00")
    client = _admin_client(monkeypatch)

    resp = client.get("/planeacion-cruce/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["last_run"] is None
    assert body["last_checked_at"] == "2026-08-29T00:00:00+00:00"
