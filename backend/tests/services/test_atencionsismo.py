"""Unified atencionsismo day-walk client (RED first) — design.md ADR-5;
backend-platform spec (foundation for "In-Process Caching Preserves Or
Improves Response Behavior").

Single implementation, extracted from `scripts/fetch_reportes_api.py`'s
day-walk/split-retry skeleton (already Python), but following
`api/reportados.js` — the CURRENTLY LIVE implementation — for exact
split-status-code set, probe, dedup, and coordKey semantics, since
`web/js/data.js` is the actual consumer and it reads the JS response shape
(`por_estadoVerificacion.Reportado`, `inmuebles`). Both source files were
checked before extraction (design open question 3): `DEFAULT_USER` is the
literal string ``"juanp.gzmz@gmail.com"`` in BOTH `api/reportados.js:27`
and `scripts/fetch_reportes_api.py:48` — no conflict, so no "JS wins" call
was needed.

No real network calls: every HTTP-touching test injects an
`httpx.AsyncClient(transport=httpx.MockTransport(handler))`. Async functions
are exercised via `asyncio.run(...)` inside plain `def test_*` functions —
no pytest-asyncio marker/config needed.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services import atencionsismo

FAKE_USER = "user@example.com"
FAKE_PASS = "s3cr3t"


def _run(coro):
    return asyncio.run(coro)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Basic-auth username constant parity (design open question 3) ---------


def test_default_user_matches_both_legacy_sources():
    # Verbatim from api/reportados.js:27 and scripts/fetch_reportes_api.py:48
    # — confirmed identical before extraction; if they ever diverge, the JS
    # value (currently live) wins per task 3.1.
    assert atencionsismo.DEFAULT_USER == "juanp.gzmz@gmail.com"


# --- credentials_from_env ---------------------------------------------------


def test_credentials_from_env_reads_user_and_password(monkeypatch):
    monkeypatch.setenv("VISITADOS_API_USER", FAKE_USER)
    monkeypatch.setenv("VISITADOS_API_PASS", FAKE_PASS)

    user, password = atencionsismo.credentials_from_env()

    assert user == FAKE_USER
    assert password == FAKE_PASS


def test_credentials_from_env_defaults_user_when_unset(monkeypatch):
    monkeypatch.delenv("VISITADOS_API_USER", raising=False)
    monkeypatch.setenv("VISITADOS_API_PASS", FAKE_PASS)

    user, _password = atencionsismo.credentials_from_env()

    assert user == atencionsismo.DEFAULT_USER


def test_credentials_from_env_raises_when_password_missing(monkeypatch):
    monkeypatch.delenv("VISITADOS_API_PASS", raising=False)

    with pytest.raises(atencionsismo.ApiCredentialsError):
        atencionsismo.credentials_from_env()


# --- coord_key ---------------------------------------------------------------


def test_coord_key_builds_stable_string_for_valid_coords():
    assert atencionsismo.coord_key("3.45", "-76.53") == "3.45,-76.53"


@pytest.mark.parametrize(
    "lat,lng",
    [(None, None), ("", ""), ("nan", "nan"), (0, 0), ("0", "0")],
)
def test_coord_key_returns_none_for_invalid_or_zero_zero_coords(lat, lng):
    assert atencionsismo.coord_key(lat, lng) is None


# --- summarize (dedup + inmuebles + por_estadoVerificacion) -----------------


def test_summarize_dedupes_by_id():
    records = [
        {"id": "a", "estado": "Reportado", "lat": "3.1", "lng": "-76.1"},
        {"id": "a", "estado": "Reportado", "lat": "3.1", "lng": "-76.1"},
        {"id": "b", "estado": "Verificado", "lat": "3.2", "lng": "-76.2"},
    ]

    result = atencionsismo.summarize(records)

    assert result["total"] == 2


def test_summarize_excludes_null_and_zero_zero_coords_from_inmuebles():
    records = [
        {"id": "a", "estado": "Reportado", "lat": None, "lng": None},
        {"id": "b", "estado": "Reportado", "lat": 0, "lng": 0},
        {"id": "c", "estado": "Reportado", "lat": "3.1", "lng": "-76.1"},
    ]

    result = atencionsismo.summarize(records)

    assert result["total"] == 3
    assert result["inmuebles"] == 1


def test_summarize_tallies_por_estado_with_dash_default():
    records = [
        {"id": "a", "estado": "Reportado", "lat": "3.1", "lng": "-76.1"},
        {"id": "b", "estado": "Reportado", "lat": "3.2", "lng": "-76.2"},
        {"id": "c", "estado": None, "lat": "3.3", "lng": "-76.3"},
    ]

    result = atencionsismo.summarize(records)

    assert result["por_estadoVerificacion"] == {"Reportado": 2, "—": 1}


def test_summarize_accepts_reportes_json_field_names():
    # The Blob-published reportes.json shape (strip_report()) uses
    # `estadoVerificacion`/`lat`/`lng` directly at the record root — same
    # keys the live day-walk already normalizes to `estado`/`lat`/`lng`.
    records = [{"id": "a", "estadoVerificacion": "Reportado", "lat": 3.1, "lng": -76.1}]

    result = atencionsismo.summarize(records)

    assert result["total"] == 1
    assert result["por_estadoVerificacion"] == {"Reportado": 1}
    assert result["inmuebles"] == 1


def test_summarize_aggregates_all_analytic_fields():
    # User directive: metrics over ALL records — the full field set the
    # atencionsismo API carries (same categories reportes_agg.json uses),
    # not just the estadoVerificacion tally the legacy JS kept.
    records = [
        {"id": "a", "estado": "Reportado", "lat": "3.1", "lng": "-76.1",
         "afectacion": "DAÑO ESTRUCTURAL", "comuna": "Comuna 19",
         "habitabilidad": "No habitable", "tipoInmueble": "Casa"},
        {"id": "b", "estado": "Asignado", "lat": "3.2", "lng": "-76.2",
         "afectacion": "DAÑO ESTRUCTURAL", "comuna": "Comuna 2",
         "habitabilidad": None, "tipoInmueble": "Edificio"},
        {"id": "c", "estado": "Reportado", "lat": None, "lng": None,
         "afectacion": None, "comuna": "Comuna 19",
         "habitabilidad": "Habitable", "tipoInmueble": "Casa"},
    ]

    result = atencionsismo.summarize(records)

    assert result["por_afectacion"] == {"DAÑO ESTRUCTURAL": 2, "—": 1}
    assert result["por_comuna"] == {"Comuna 19": 2, "Comuna 2": 1}
    assert result["por_habitabilidad"] == {"—": 1, "Habitable": 1, "No habitable": 1}
    assert result["por_tipoInmueble"] == {"Casa": 2, "Edificio": 1}
    assert result["con_coordenadas"] == 2
    assert result["sin_coordenadas"] == 1
    # Every per-category breakdown reconciles with the total — no record is
    # ever silently ignored (same invariant fetch_reportes_api.py asserts).
    for field in ("por_estadoVerificacion", "por_afectacion", "por_comuna",
                  "por_habitabilidad", "por_tipoInmueble"):
        assert sum(result[field].values()) == result["total"], field


def test_summarize_counts_dropped_idless_records_as_sin_id():
    records = [
        {"id": "a", "estado": "Reportado", "lat": "3.1", "lng": "-76.1"},
        {"id": None, "estado": "Reportado", "lat": "3.2", "lng": "-76.2"},
        {"estado": "Asignado", "lat": "3.3", "lng": "-76.3"},
    ]

    result = atencionsismo.summarize(records)

    assert result["total"] == 1
    assert result["sin_id"] == 2  # dropped records are COUNTED, never silent


# --- probe_api ----------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 413, 504])
def test_probe_api_accepts_alive_statuses(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"reportes": []})

    async def go():
        async with _client(handler) as client:
            await atencionsismo.probe_api(client, FAKE_USER, FAKE_PASS)

    _run(go())  # must not raise


@pytest.mark.parametrize("status", [401, 500, 503])
def test_probe_api_raises_on_down_statuses(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async def go():
        async with _client(handler) as client:
            await atencionsismo.probe_api(client, FAKE_USER, FAKE_PASS)

    with pytest.raises(atencionsismo.ApiUnavailableError):
        _run(go())


def test_probe_api_raises_on_network_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async def go():
        async with _client(handler) as client:
            await atencionsismo.probe_api(client, FAKE_USER, FAKE_PASS)

    with pytest.raises(atencionsismo.ApiUnavailableError):
        _run(go())


# --- fetch_window: split-on-413/500/502/503/504 down to 1-min windows -------


def test_fetch_window_returns_mapped_records_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "reportes": [
                    {"id": "r1", "estadoVerificacion": "Reportado", "latitud": "3.1", "longitud": "-76.1"},
                ]
            },
        )

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_window(client, FAKE_USER, FAKE_PASS, 0, 59_999)

    records = _run(go())

    assert records == [{
        "id": "r1", "estado": "Reportado", "lat": "3.1", "lng": "-76.1",
        # Analytic fields ride along even when the API omits them (None) so
        # summarize() can aggregate over ALL records (user directive).
        "afectacion": None, "comuna": None, "habitabilidad": None, "tipoInmueble": None,
    }]


@pytest.mark.parametrize("status", [413, 500, 502, 503, 504])
def test_fetch_window_splits_on_dense_status_codes_down_to_min_window(monkeypatch, status):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    seen_windows: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        d0 = int(request.url.params["desde_utc"])
        d1 = int(request.url.params["hasta_utc"])
        if d1 - d0 > atencionsismo.MIN_WINDOW_MS:
            return httpx.Response(status)
        seen_windows.append((d0, d1))
        return httpx.Response(
            200,
            json={
                "reportes": [
                    {"id": f"r{d0}", "estadoVerificacion": "Reportado", "latitud": "3.1", "longitud": "-76.1"},
                ]
            },
        )

    async def go():
        async with _client(handler) as client:
            # 4x MIN_WINDOW_MS: recursive halving lands on several
            # <= MIN_WINDOW_MS leaf windows that all succeed at 200.
            return await atencionsismo.fetch_window(
                client, FAKE_USER, FAKE_PASS, 0, 4 * atencionsismo.MIN_WINDOW_MS - 1
            )

    records = _run(go())

    # Splitting happened (more than one leaf window was reached) and every
    # leaf that actually served 200 is <= MIN_WINDOW_MS wide.
    assert len(seen_windows) >= 2
    assert len(records) == len(seen_windows)
    assert all(d1 - d0 <= atencionsismo.MIN_WINDOW_MS for d0, d1 in seen_windows)
    assert {r["id"] for r in records} == {f"r{d0}" for d0, _d1 in seen_windows}


def test_fetch_window_retries_then_gives_up_at_min_window(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(500)

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_window(client, FAKE_USER, FAKE_PASS, 0, 59_999)

    records = _run(go())

    assert records == []
    assert call_count["n"] == atencionsismo.MAX_ATTEMPTS


def test_fetch_window_records_failed_window_after_giving_up(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    failed: list[tuple[int, int]] = []

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_window(
                client, FAKE_USER, FAKE_PASS, 0, 59_999, failed_windows=failed
            )

    _run(go())

    assert failed == [(0, 59_999)]


# --- count_reportes: concurrency batching + dedup across windows + retry ---


def test_count_reportes_dedupes_across_windows_and_recovers_failed_window(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    attempts: dict[int, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        d0 = int(request.url.params["desde_utc"])
        day_index = d0 // atencionsismo.DAY_MS
        attempts[day_index] = attempts.get(day_index, 0) + 1
        # Day 1 fails on its first pass (within the concurrency batch) so it
        # lands in failed_windows, then succeeds on the sequential retry pass.
        if day_index == 1 and attempts[day_index] == 1:
            return httpx.Response(500)
        return httpx.Response(
            200,
            json={
                "reportes": [
                    {
                        "id": f"day{day_index}",
                        "estadoVerificacion": "Reportado",
                        "latitud": str(3.0 + day_index),
                        "longitud": str(-76.0 - day_index),
                    }
                ]
            },
        )

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.count_reportes(
                client,
                FAKE_USER,
                FAKE_PASS,
                "2024-01-01",
                until_ms=atencionsismo._parse_desde("2024-01-04"),
            )

    result = _run(go())

    assert result["total"] == 3  # day0, day1 (recovered), day2
    assert result["inmuebles"] == 3
    assert result["por_estadoVerificacion"] == {"Reportado": 3}


# --- fetch_reportados: probe + count_reportes, empty-result guard ----------


def test_fetch_reportados_returns_ok_payload_shape(monkeypatch):
    monkeypatch.setenv("VISITADOS_API_PASS", FAKE_PASS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "reportes": [
                    {"id": "r1", "estadoVerificacion": "Reportado", "latitud": "3.1", "longitud": "-76.1"},
                ]
            },
        )

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_reportados(
                client, desde="2024-01-01", until_ms=atencionsismo._parse_desde("2024-01-02")
            )

    payload = _run(go())

    assert payload["ok"] is True
    assert payload["fuente"] == "api:informe/json"
    assert payload["total"] == 1
    assert payload["inmuebles"] == 1
    assert payload["por_estadoVerificacion"] == {"Reportado": 1}
    assert "generado" in payload


def test_fetch_reportados_raises_on_zero_total(monkeypatch):
    monkeypatch.setenv("VISITADOS_API_PASS", FAKE_PASS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"reportes": []})

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_reportados(
                client, desde="2024-01-01", until_ms=atencionsismo._parse_desde("2024-01-02")
            )

    with pytest.raises(atencionsismo.ApiEmptyResultError):
        _run(go())
