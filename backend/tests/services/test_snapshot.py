"""In-process reportados snapshot cache (RED first) — design.md ADR-5;
backend-platform spec "In-Process Caching Preserves Or Improves Response
Behavior" (reportados scenarios).

Covers: Blob-seed cold start serves immediately with an age; Blob-seed
failure + no completed refresh -> `SnapshotUnavailableError` (router maps
this to 503 + `Retry-After: 60`); snapshot older than 86400s ->
`SnapshotStaleError` (503); a completed refresh stores a servable snapshot
with a fresh (~0s) age. No real network: `seed_from_blob` is exercised via
an injected `httpx.AsyncClient(transport=httpx.MockTransport(...))`.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services import snapshot as snapshot_service


def _run(coro):
    return asyncio.run(coro)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- ReportadosSnapshot: store/get, staleness bound -------------------------


def test_get_raises_unavailable_when_nothing_seeded_yet():
    snap = snapshot_service.ReportadosSnapshot()

    with pytest.raises(snapshot_service.SnapshotUnavailableError) as exc_info:
        snap.get()

    assert exc_info.value.retry_after == 60


def test_get_returns_payload_and_fresh_age_after_store():
    snap = snapshot_service.ReportadosSnapshot()
    payload = {"ok": True, "total": 5, "inmuebles": 3, "por_estadoVerificacion": {"Reportado": 5}}

    snap.store(payload)
    got_payload, age = snap.get()

    assert got_payload == payload
    assert 0 <= age < 1


def test_get_raises_stale_when_entry_exceeds_86400s_bound():
    import time

    snap = snapshot_service.ReportadosSnapshot()
    snap.store({"ok": True}, fetched_at=time.monotonic() - snapshot_service.STALE_AFTER_S - 1)

    with pytest.raises(snapshot_service.SnapshotStaleError) as exc_info:
        snap.get()

    assert exc_info.value.retry_after == 60


def test_get_serves_snapshot_within_86400s_even_if_older_than_900s():
    import time

    snap = snapshot_service.ReportadosSnapshot()
    # 4000s old: past the 900s refresh cadence but well under the 86400s
    # hard outer bound — still servable (design.md ADR-5).
    snap.store({"ok": True}, fetched_at=time.monotonic() - 4000)

    payload, age = snap.get()

    assert payload == {"ok": True}
    assert age >= 4000


def test_has_entry_reflects_store_state():
    snap = snapshot_service.ReportadosSnapshot()
    assert snap.has_entry is False

    snap.store({"ok": True})

    assert snap.has_entry is True


# --- seed_from_blob: best-effort cold start ---------------------------------


def test_seed_from_blob_returns_false_when_url_env_unset(monkeypatch):
    monkeypatch.delenv(snapshot_service.BLOB_URL_ENV, raising=False)
    snap = snapshot_service.ReportadosSnapshot()

    ok = _run(snapshot_service.seed_from_blob(snap))

    assert ok is False
    assert snap.has_entry is False


def test_seed_from_blob_populates_snapshot_on_success(monkeypatch):
    monkeypatch.setenv(snapshot_service.BLOB_URL_ENV, "https://blob.example.com/data/reportes.json")
    records = [
        {"id": "a", "estadoVerificacion": "Reportado", "lat": 3.1, "lng": -76.1},
        {"id": "b", "estadoVerificacion": "Verificado", "lat": 3.2, "lng": -76.2},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://blob.example.com/data/reportes.json"
        return httpx.Response(200, json=records)

    async def go():
        snap = snapshot_service.ReportadosSnapshot()
        async with _client(handler) as client:
            ok = await snapshot_service.seed_from_blob(snap, client=client)
        return snap, ok

    snap, ok = _run(go())

    assert ok is True
    assert snap.has_entry is True
    payload, age = snap.get()
    assert payload["total"] == 2
    assert payload["inmuebles"] == 2
    assert payload["fuente"] == "blob-seed"
    assert 0 <= age < 1  # "serves immediately with age" — age tracks from seed time


def test_seed_from_blob_returns_false_on_http_error(monkeypatch):
    monkeypatch.setenv(snapshot_service.BLOB_URL_ENV, "https://blob.example.com/data/reportes.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def go():
        snap = snapshot_service.ReportadosSnapshot()
        async with _client(handler) as client:
            ok = await snapshot_service.seed_from_blob(snap, client=client)
        return snap, ok

    snap, ok = _run(go())

    assert ok is False
    assert snap.has_entry is False


def test_seed_from_blob_returns_false_on_empty_records(monkeypatch):
    monkeypatch.setenv(snapshot_service.BLOB_URL_ENV, "https://blob.example.com/data/reportes.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async def go():
        snap = snapshot_service.ReportadosSnapshot()
        async with _client(handler) as client:
            ok = await snapshot_service.seed_from_blob(snap, client=client)
        return snap, ok

    snap, ok = _run(go())

    assert ok is False
    assert snap.has_entry is False


def test_seed_from_blob_returns_false_on_non_list_json(monkeypatch):
    monkeypatch.setenv(snapshot_service.BLOB_URL_ENV, "https://blob.example.com/data/reportes.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    async def go():
        snap = snapshot_service.ReportadosSnapshot()
        async with _client(handler) as client:
            ok = await snapshot_service.seed_from_blob(snap, client=client)
        return snap, ok

    snap, ok = _run(go())

    assert ok is False
    assert snap.has_entry is False


# --- refresh_loop: one iteration store + broad-except resilience -----------


def test_refresh_loop_stores_a_completed_refresh_then_stops(monkeypatch):
    monkeypatch.setenv("VISITADOS_API_PASS", "s3cr3t")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"reportes": [{"id": "r1", "estadoVerificacion": "Reportado", "latitud": "3.1", "longitud": "-76.1"}]},
        )

    async def go():
        snap = snapshot_service.ReportadosSnapshot()
        stop_event = asyncio.Event()
        stop_event.set()  # run exactly one iteration, then exit the loop
        await snapshot_service.refresh_loop(
            snap, stop_event=stop_event, transport=httpx.MockTransport(handler)
        )
        return snap

    snap = _run(go())

    assert snap.has_entry is True
    payload, _age = snap.get()
    assert payload["total"] == 1


def test_refresh_loop_survives_a_failed_refresh_without_raising(monkeypatch):
    monkeypatch.delenv("VISITADOS_API_PASS", raising=False)  # forces ApiCredentialsError

    async def go():
        snap = snapshot_service.ReportadosSnapshot()
        stop_event = asyncio.Event()
        stop_event.set()
        await snapshot_service.refresh_loop(snap, stop_event=stop_event)
        return snap

    snap = _run(go())  # must not raise

    assert snap.has_entry is False
