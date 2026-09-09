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
import logging

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


def test_fetch_window_follows_next_offset_across_multiple_pages():
    seen_offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        seen_offsets.append(offset)
        if offset == 0:
            return httpx.Response(
                200,
                json={
                    "reportes": [
                        {"id": "r1", "estadoVerificacion": "Reportado", "latitud": "3.1", "longitud": "-76.1"},
                        {"id": "r2", "estadoVerificacion": "Reportado", "latitud": "3.2", "longitud": "-76.2"},
                    ],
                    "done": False,
                    "nextOffset": 2,
                },
            )
        return httpx.Response(
            200,
            json={
                "reportes": [
                    {"id": "r3", "estadoVerificacion": "Reportado", "latitud": "3.3", "longitud": "-76.3"},
                ],
                "done": True,
                "nextOffset": 3,
            },
        )

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_window(client, FAKE_USER, FAKE_PASS, 0, 59_999)

    records = _run(go())

    assert [r["id"] for r in records] == ["r1", "r2", "r3"]
    assert seen_offsets == [0, 2]


def test_fetch_window_stops_when_done_true_even_with_nextoffset_present():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "reportes": [
                    {"id": "r1", "estadoVerificacion": "Reportado", "latitud": "3.1", "longitud": "-76.1"},
                ],
                "done": True,
                "nextOffset": 200,
            },
        )

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_window(client, FAKE_USER, FAKE_PASS, 0, 59_999)

    records = _run(go())

    assert [r["id"] for r in records] == ["r1"]
    assert calls["n"] == 1


@pytest.mark.parametrize("bad_next_offset", [None, "missing", 0, -1])
def test_fetch_window_stops_on_non_advancing_or_missing_cursor(bad_next_offset):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = {
            "reportes": [
                {"id": "r1", "estadoVerificacion": "Reportado", "latitud": "3.1", "longitud": "-76.1"},
            ],
            "done": False,
        }
        if bad_next_offset != "missing":
            body["nextOffset"] = bad_next_offset
        return httpx.Response(200, json=body)

    failed: list[tuple[int, int]] = []

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_window(
                client, FAKE_USER, FAKE_PASS, 0, 59_999, failed_windows=failed
            )

    records = _run(go())

    assert [r["id"] for r in records] == ["r1"]
    assert calls["n"] == 1
    assert failed == []  # malformed-but-200 response is not a transport/status failure


def test_fetch_window_safety_cap_stops_never_ending_done_false_response(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "reportes": [
                    {"id": f"r{offset}", "estadoVerificacion": "Reportado", "latitud": "3.1", "longitud": "-76.1"},
                ],
                "done": False,
                "nextOffset": offset + atencionsismo.PAGE_LIMIT,
            },
        )

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_window(client, FAKE_USER, FAKE_PASS, 0, 59_999)

    records = _run(go())

    assert calls["n"] <= atencionsismo.MAX_PAGES_PER_WINDOW
    assert len(records) == calls["n"]


def test_fetch_window_splittable_status_on_a_later_page_still_splits_the_whole_window(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    seen_windows: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        d0 = int(request.url.params["desde_utc"])
        d1 = int(request.url.params["hasta_utc"])
        offset = int(request.url.params["offset"])
        width = d1 - d0
        if width > atencionsismo.MIN_WINDOW_MS and offset == 0:
            # First page of the full window succeeds but signals more pages.
            return httpx.Response(
                200,
                json={
                    "reportes": [
                        {"id": "abandoned", "estadoVerificacion": "Reportado", "latitud": "3.1", "longitud": "-76.1"},
                    ],
                    "done": False,
                    "nextOffset": 1,
                },
            )
        if width > atencionsismo.MIN_WINDOW_MS and offset == 1:
            # Second page of the full (still-wide) window chokes -> split.
            return httpx.Response(500)
        # Leaf windows (<= MIN_WINDOW_MS) always succeed on their first page.
        seen_windows.append((d0, d1))
        return httpx.Response(
            200,
            json={
                "reportes": [
                    {"id": f"r{d0}", "estadoVerificacion": "Reportado", "latitud": "3.2", "longitud": "-76.2"},
                ],
                "done": True,
            },
        )

    async def go():
        async with _client(handler) as client:
            return await atencionsismo.fetch_window(
                client, FAKE_USER, FAKE_PASS, 0, 4 * atencionsismo.MIN_WINDOW_MS - 1
            )

    records = _run(go())

    assert len(seen_windows) >= 2
    ids = {r["id"] for r in records}
    assert "abandoned" not in ids  # page 1 of the superseded whole-window attempt must not leak
    assert ids == {f"r{d0}" for d0, _d1 in seen_windows}
    assert len(records) == len(seen_windows)  # no duplicate/wrong data from the abandoned attempt


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


# ── fetch_stickers: offset pagination over GET /api/informe/stickers ──────


def _sticker(i: int, origen: str = "firebase") -> dict:
    return {"id": f"ev-{i}", "direccion": f"Calle {i}", "latitud": "3.45", "longitud": "-76.53",
            "numero": f"76001-1-004{i:04d}", "personaAfectada": "X", "origen": origen,
            "color": "verde", "colorEtiqueta": "Habitable"}


def _pages_handler(pages: dict[int, dict], seen: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/informe/stickers"
        assert request.headers["Authorization"].startswith("Basic ")
        params = dict(request.url.params)
        if seen is not None:
            seen.append(params)
        offset = int(params.get("offset", "0"))
        return httpx.Response(200, json=pages[offset])
    return handler


def test_fetch_stickers_follows_next_offset_until_done(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    seen: list[dict] = []
    pages = {
        0: {"ok": True, "stickers": [_sticker(1), _sticker(2)], "nextOffset": 200, "done": False},
        200: {"ok": True, "stickers": [_sticker(3)], "nextOffset": 400, "done": True},
    }
    rows = _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages, seen)), FAKE_USER, FAKE_PASS))
    assert [r["id"] for r in rows] == ["ev-1", "ev-2", "ev-3"]
    assert [p["offset"] for p in seen] == ["0", "200"]
    assert all(p["limit"] == "200" for p in seen)


def test_fetch_stickers_forwards_date_params(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    seen: list[dict] = []
    pages = {0: {"ok": True, "stickers": [_sticker(1)], "nextOffset": 200, "done": True}}
    _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages, seen)), FAKE_USER, FAKE_PASS,
                                      desde_utc=1000, hasta_utc=2000))
    assert seen[0]["desde_utc"] == "1000" and seen[0]["hasta_utc"] == "2000"


def test_fetch_stickers_401_raises_unavailable_without_retry(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "no"})

    with pytest.raises(atencionsismo.ApiUnavailableError) as exc:
        _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))
    assert exc.value.status == 503
    assert calls == 1


def test_fetch_stickers_retries_504_then_succeeds(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(504)
        return httpx.Response(200, json={"ok": True, "stickers": [_sticker(1)], "done": True})

    rows = _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))
    assert len(rows) == 1 and attempts["n"] == 2


def test_fetch_stickers_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500)

    with pytest.raises(atencionsismo.ApiUnavailableError):
        _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))
    assert attempts["n"] == atencionsismo.MAX_ATTEMPTS


def test_fetch_stickers_malformed_json_retries(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(200, text="<html>maintenance</html>")
        return httpx.Response(200, json={"ok": True, "stickers": [_sticker(1)], "done": True})

    rows = _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))
    assert len(rows) == 1 and attempts["n"] == 2


def test_fetch_stickers_empty_universe_raises(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    pages = {0: {"ok": True, "stickers": [], "nextOffset": 0, "done": True}}
    with pytest.raises(atencionsismo.ApiEmptyResultError):
        _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages)), FAKE_USER, FAKE_PASS))


# B1: non-advancing/malformed/exhausted pagination no longer returns a
# silent partial universe — it raises, so the cache's serve-stale/Blob
# chain kicks in instead of quietly under-counting stickers.


def test_fetch_stickers_non_advancing_cursor_raises(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    seen: list[dict] = []
    pages = {0: {"ok": True, "stickers": [_sticker(1)], "nextOffset": 0, "done": False}}
    with pytest.raises(atencionsismo.ApiUnavailableError):
        _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages, seen)), FAKE_USER, FAKE_PASS))
    assert len(seen) == 1  # stopped after the one non-advancing page, not looped forever


def test_fetch_stickers_coerces_numeric_string_next_offset(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    seen: list[dict] = []
    pages = {
        0: {"ok": True, "stickers": [_sticker(1)], "nextOffset": "200", "done": False},
        200: {"ok": True, "stickers": [_sticker(2)], "nextOffset": 400, "done": True},
    }
    rows = _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages, seen)), FAKE_USER, FAKE_PASS))
    assert [r["id"] for r in rows] == ["ev-1", "ev-2"]
    assert [p["offset"] for p in seen] == ["0", "200"]


def test_fetch_stickers_non_numeric_cursor_raises(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    pages = {0: {"ok": True, "stickers": [_sticker(1)], "nextOffset": "abc", "done": False}}
    with pytest.raises(atencionsismo.ApiUnavailableError):
        _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages)), FAKE_USER, FAKE_PASS))


def test_fetch_stickers_max_pages_exhausted_raises(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    monkeypatch.setattr(atencionsismo, "MAX_PAGES", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params).get("offset", "0"))
        return httpx.Response(
            200,
            json={"ok": True, "stickers": [_sticker(offset)], "nextOffset": offset + 200, "done": False},
        )

    with pytest.raises(atencionsismo.ApiUnavailableError):
        _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))


def test_fetch_stickers_missing_done_field_warns_and_completes(monkeypatch, caplog):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    pages = {0: {"ok": True, "stickers": [_sticker(1)]}}  # no `done`, no `nextOffset`
    with caplog.at_level(logging.WARNING):
        rows = _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages)), FAKE_USER, FAKE_PASS))
    assert len(rows) == 1
    assert any("done" in r.message for r in caplog.records)


# B2: any 400..499 status is a client-error, non-retriable failure — no
# point burning MAX_ATTEMPTS retries on a request that will never succeed.


def test_fetch_stickers_400_raises_without_retry(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    with pytest.raises(atencionsismo.ApiUnavailableError):
        _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))
    assert calls["n"] == 1
