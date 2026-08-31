"""GET /sticker-status (RED first) — design.md ADR-4; backend-platform spec
"Any-authenticated role-wide route accepts every valid role", "sticker-status
cache hit within TTL".

Ports `api/sticker-status.js`'s Firestore read (`sticker_matches` collection
tally: `con_sticker`/`con`/`total`) but FIXES the legacy cache's warm-lambda-
only correctness: the legacy handler held its cache in a bare module-level
variable, which only behaved as a shared 5-minute cache when Vercel happened
to reuse a warm Lambda instance between invocations — a cold start (or two
concurrent cold invocations) got NO caching guarantee at all. This backend
is one always-on process (ADR-1 proposal answer 8), so the cache below is
attached to `app.state` and actually holds for the process lifetime — the
guarantee the legacy code only had by accident on a warm Lambda.

Uses a call-count-instrumented fake `credentials.sismo()` override (no real
service-account JSON, no network) to prove the TTL cache skips Firestore on
a repeat request within the window.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.main import create_app

FAKE_CLAIMS_VIEWER = {"sub": "uid-viewer", "email": "someone@gmail.com"}
FAKE_CLAIMS_ADMIN = {
    "sub": "uid-admin",
    "email": "admin@example.com",
    "role": "admin",
}


@pytest.fixture(autouse=True)
def _no_blob_token(monkeypatch):
    """Hermetic guard: a real BLOB_READ_WRITE_TOKEN in the dev environment
    must never let the cache's fire-and-forget Blob persistence attempt a
    real upload during tests (tests that exercise the Blob path set the env
    var and fake `blob_sync` themselves)."""
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

_FAKE_DOCS: list[dict[str, Any]] = [
    {"registro_id": "1", "tiene_sticker": True},
    {"registro_id": "2", "tiene_sticker": False},
    {"registro_id": None, "tiene_sticker": True},  # dropped — no registro_id
]


class _FakeDoc:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]], calls: list[int]) -> None:
        self._docs = docs
        self._calls = calls

    def get(self) -> list[_FakeDoc]:
        self._calls.append(1)
        return [_FakeDoc(d) for d in self._docs]


class _FakeFirestore:
    def __init__(self, docs: list[dict[str, Any]], calls: list[int]) -> None:
        self._docs = docs
        self._calls = calls

    def collection(self, name: str) -> _FakeCollection:
        assert name == "sticker_matches"
        return _FakeCollection(self._docs, self._calls)


class _FakeSismoClients:
    def __init__(self, docs: list[dict[str, Any]], calls: list[int]) -> None:
        self.firestore = _FakeFirestore(docs, calls)
        self.app = None


def _app(monkeypatch, calls: list[int]) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    credentials.s3.cache_clear()
    monkeypatch.setattr(credentials, "sismo", lambda: _FakeSismoClients(_FAKE_DOCS, calls))
    return create_app()


def _client(monkeypatch) -> TestClient:
    return TestClient(_app(monkeypatch, []))


def _authed_client(monkeypatch, claims: dict[str, Any], calls: list[int]) -> TestClient:
    app = _app(monkeypatch, calls)
    app.dependency_overrides[current_claims] = lambda: claims
    return TestClient(app)


def test_any_authenticated_role_gets_200(monkeypatch):
    for claims in (FAKE_CLAIMS_VIEWER, FAKE_CLAIMS_ADMIN):
        calls: list[int] = []
        client = _authed_client(monkeypatch, claims, calls)

        resp = client.get("/sticker-status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["total"] == 2
        assert body["con"] == 1
        assert body["con_sticker"] == ["1"]


def test_unauthenticated_is_rejected(monkeypatch):
    client = _client(monkeypatch)

    resp = client.get("/sticker-status")

    assert resp.status_code == 401


def test_firestore_exception_becomes_502_not_a_bare_crash(monkeypatch):
    """A 429/ResourceExhausted (or any other) Firestore exception must
    surface as a normal HTTPException (502, CORS headers intact) — not
    propagate unhandled into a bare 500 that Starlette's default error
    handler serves with NO CORS headers, which the browser then
    misreports as "blocked by CORS policy" instead of the real cause."""
    calls: list[int] = []
    app = _app(monkeypatch, calls)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    from app.routers import sticker_status as sticker_status_module

    monkeypatch.setattr(sticker_status_module, "_read_coverage", lambda db: boom())
    client = TestClient(app)

    resp = client.get("/sticker-status")

    assert resp.status_code == 502
    assert "Quota exceeded" in resp.json()["detail"]


def test_get_or_fetch_serves_stale_payload_when_a_later_fetch_fails(monkeypatch):
    """Once at least one fetch has ever succeeded, a Firestore outage on a
    later refresh (429/ResourceExhausted, sustained) must degrade to the
    last known-good payload — not a 502 — so the dashboard always has
    SOMETHING to show. 30-ago-2026: staggering the colliding crons did not
    clear an already-tripped rate limit fast enough; this is the fix that
    actually keeps the UI usable while Firestore recovers."""
    from app.routers import sticker_status as mod

    cache = mod.StickerStatusCache()
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    good_payload = {"ok": True, "total": 5, "con": 2, "con_sticker": ["1", "2"]}
    result = cache.get_or_fetch(lambda: good_payload)
    assert result == good_payload

    clock["t"] += mod.CACHE_TTL_SECONDS + 1  # force the next call to see it as stale

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    result_during_outage = cache.get_or_fetch(boom)

    assert result_during_outage == good_payload  # served stale, no exception raised


def test_get_or_fetch_still_raises_on_a_cold_cache_with_no_prior_success(monkeypatch):
    """The very first fetch in a fresh process, with nothing in Blob either
    (no last-known-good ever persisted), must still surface the real error —
    there is nothing to serve, and silently returning a fake empty result
    would be more misleading than a clear failure."""
    from app.routers import sticker_status as mod

    monkeypatch.setattr(mod.blob_lkg, "load_json", lambda pathname, expected_type: None)
    cache = mod.StickerStatusCache()

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    with pytest.raises(RuntimeError, match="Quota exceeded"):
        cache.get_or_fetch(boom)


def test_get_or_fetch_cold_start_falls_back_to_blob_last_good(monkeypatch):
    """A COLD-start fetch failure (fresh process after a deploy, Firestore
    still 429ing) must serve the Blob-persisted last-known-good payload
    instead of raising — and adopt it as the in-process payload so later
    behavior is normal serve-stale."""
    from app.routers import sticker_status as mod

    blob_payload = {"con_sticker": ["1"], "total": 3, "con": 1}
    monkeypatch.setattr(mod.blob_lkg, "load_json",
                        lambda pathname, expected_type: blob_payload)
    cache = mod.StickerStatusCache()

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    assert cache.get_or_fetch(boom) == blob_payload
    # Adopted in-process: served again within TTL without touching Blob.
    monkeypatch.setattr(mod.blob_lkg, "load_json",
                        lambda pathname, expected_type: None)
    assert cache.get_or_fetch(boom) == blob_payload


def test_get_or_fetch_cold_start_rejects_malformed_blob_payload(monkeypatch):
    """Malformed Blob JSON must fail to the raise path, never be served as
    data (the `_load_contacto_hashes` malformed-Blob precedent). Exercises
    the real `blob_lkg.load_json` with only `blob_sync.download` faked."""
    from pathlib import Path

    from app.routers import sticker_status as mod

    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_STORE_secret")

    def fake_download(pathname, local, **kw):
        Path(local).write_text("{not valid json", encoding="utf-8")
        return True

    monkeypatch.setattr(mod.blob_lkg.blob_sync, "download", fake_download)
    cache = mod.StickerStatusCache()

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    with pytest.raises(RuntimeError, match="Quota exceeded"):
        cache.get_or_fetch(boom)


def test_get_or_fetch_persists_to_blob_only_when_payload_changed(monkeypatch):
    """A successful fetch persists the payload to Blob, hash-gated: an
    unchanged payload is NOT re-uploaded every TTL window; a changed one
    is."""
    from app.routers import sticker_status as mod

    cache = mod.StickerStatusCache()
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    saves: list[Any] = []
    monkeypatch.setattr(mod.blob_lkg, "save_json",
                        lambda pathname, payload: saves.append((pathname, payload)) or True)

    def _join():
        # Persist runs on a daemon thread (off the request path); join it so
        # the upload-count assertions are deterministic.
        if cache._persist_thread is not None:
            cache._persist_thread.join()

    cache.get_or_fetch(lambda: {"con_sticker": ["1"], "total": 3, "con": 1})
    _join()
    assert len(saves) == 1
    assert saves[0][0] == mod.STICKER_STATUS_LKG_BLOB

    clock["t"] += mod.CACHE_TTL_SECONDS + 1
    cache.get_or_fetch(lambda: {"con_sticker": ["1"], "total": 3, "con": 1})  # unchanged
    _join()
    assert len(saves) == 1  # hash-gated: unchanged payload not re-uploaded

    clock["t"] += mod.CACHE_TTL_SECONDS + 1
    cache.get_or_fetch(lambda: {"con_sticker": ["1", "2"], "total": 3, "con": 2})
    _join()
    assert len(saves) == 2


def test_get_or_fetch_blob_write_failure_never_breaks_the_request(monkeypatch):
    """A Blob upload failure (e.g. `blob_sync`'s own `sys.exit` on an API
    error) on the persist path must never break a request whose Firestore
    fetch SUCCEEDED — persistence is strictly fire-and-forget."""
    from app.routers import sticker_status as mod

    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_STORE_secret")

    def exploding_upload(local, pathname, *a, **kw):
        raise SystemExit("Blob upload 500 para data/sticker_status_last_good.json")

    monkeypatch.setattr(mod.blob_lkg.blob_sync, "upload", exploding_upload)
    cache = mod.StickerStatusCache()

    payload = {"con_sticker": [], "total": 0, "con": 0}
    assert cache.get_or_fetch(lambda: payload) == payload
    if cache._persist_thread is not None:  # let the failing upload finish inside the test
        cache._persist_thread.join()
    assert cache._blob_hash is None  # failed upload never advances the hash


def test_get_or_fetch_failure_cooldown_serves_stale_without_refetching(monkeypatch):
    """31-ago-2026 cooldown: after a failed fetch, the ~20s frontend polls
    within the cooldown window must serve the stale payload WITHOUT
    re-attempting the (slow, rate-limiter-hammering) Firestore fetch."""
    from app.routers import sticker_status as mod

    cache = mod.StickerStatusCache()
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    good_payload = {"con_sticker": ["1"], "total": 3, "con": 1}
    assert cache.get_or_fetch(lambda: good_payload) == good_payload

    clock["t"] += mod.CACHE_TTL_SECONDS + 1
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise RuntimeError("429 Quota exceeded.")

    assert cache.get_or_fetch(boom) == good_payload  # failure recorded, stale served
    clock["t"] += 20  # a frontend poll, still inside the cooldown
    assert cache.get_or_fetch(boom) == good_payload
    assert len(calls) == 1  # the poll never touched Firestore


def test_get_or_fetch_reattempts_a_live_fetch_after_the_cooldown_expires(monkeypatch):
    """Recovery is automatic: the first request AFTER the cooldown window
    attempts a live fetch again, no manual intervention needed."""
    from app.routers import sticker_status as mod

    cache = mod.StickerStatusCache()
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    assert cache.get_or_fetch(lambda: {"con_sticker": [], "total": 0, "con": 0})["total"] == 0
    clock["t"] += mod.CACHE_TTL_SECONDS + 1

    def boom():
        raise RuntimeError("429 Quota exceeded.")

    cache.get_or_fetch(boom)  # trips the cooldown
    clock["t"] += mod.FETCH_FAIL_COOLDOWN_SECONDS + 1

    recovered = {"con_sticker": ["1"], "total": 1, "con": 1}
    assert cache.get_or_fetch(lambda: recovered) == recovered  # live fetch attempted again


def test_cold_cache_failure_cooldown_fails_fast_without_refetching(monkeypatch):
    """Cold start (no payload, nothing in Blob) within the cooldown: fail
    fast with a 503 HTTPException — which the route re-raises as-is —
    instead of re-running the doomed multi-second Firestore fetch."""
    from app.routers import sticker_status as mod

    monkeypatch.setattr(mod.blob_lkg, "load_json", lambda pathname, expected_type: None)
    cache = mod.StickerStatusCache()
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise RuntimeError("429 Quota exceeded.")

    with pytest.raises(RuntimeError, match="Quota exceeded"):
        cache.get_or_fetch(boom)

    clock["t"] += 20  # a frontend poll, still inside the cooldown
    with pytest.raises(mod.HTTPException) as excinfo:
        cache.get_or_fetch(boom)
    assert excinfo.value.status_code == 503
    assert len(calls) == 1  # the poll never touched Firestore


def test_cached_response_served_without_new_firestore_read(monkeypatch):
    calls: list[int] = []
    app = _app(monkeypatch, calls)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER
    client = TestClient(app)

    first = client.get("/sticker-status")
    second = client.get("/sticker-status")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1
