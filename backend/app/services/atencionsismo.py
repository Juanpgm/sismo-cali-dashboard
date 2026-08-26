"""Unified atencionsismo `informe/json` day-walk client (design.md ADR-5).

**Single implementation** replacing BOTH `scripts/fetch_reportes_api.py`
(Python day-walk, kept as a thin caller once slice 7 lands) AND
`api/reportados.js` (the JS twin — retired, not ported). Consumed by the
web snapshot refresher (`app/services/snapshot.py`) and, in a later slice,
`app/jobs/dashboard_refresh.py`.

The API 504s on the full history in one request and 413/500/502/503s on
dense days, so this walks the requested range in day windows and
recursively halves any window that still errors, down to a 1-minute floor
— exactly `api/reportados.js`'s (the CURRENTLY LIVE implementation) split
set and probe/dedup semantics, ported to async httpx so it can run inside
FastAPI's background refresh loop. `scripts/fetch_reportes_api.py`'s
narrower {413, 504} split set and lack of a probe/failed-window retry pass
are the two behavioral gaps this extraction closes — the live JS behavior
wins per task 3.1.

Basic-auth username constant (design open question 3): confirmed identical
in both legacy sources before extraction — `api/reportados.js:27` and
`scripts/fetch_reportes_api.py:48` both hardcode ``"juanp.gzmz@gmail.com"``.
No conflict, so no "JS wins" substitution was needed for this constant.
"""
from __future__ import annotations

import base64
import math
import os
import time
from datetime import datetime, timezone
from typing import Iterable, Mapping

import httpx

API_URL = "https://atencionsismo.cali.gov.co/api/informe/json"

USER_ENV = "VISITADOS_API_USER"
PASS_ENV = "VISITADOS_API_PASS"
DEFAULT_USER = "juanp.gzmz@gmail.com"

DESDE_ENV = "REPORTES_DESDE"
DEFAULT_DESDE = os.environ.get(DESDE_ENV, "2026-08-01")

DAY_MS = 86_400_000
MIN_WINDOW_MS = 60_000  # smallest window before giving up on a split
CONCURRENCY = 4  # parallel day windows (api/reportados.js CONCURRENCY)
MAX_ATTEMPTS = 3
RETRY_SLEEP_S = 2.0
REQUEST_TIMEOUT_S = 90.0
PROBE_TIMEOUT_S = 15.0

# 413 payload-too-big, 500/502/503/504 upstream chokes on volume — all mean
# "too much for one window", so split instead of dropping the day
# (api/reportados.js:72, the live behavior; scripts/fetch_reportes_api.py
# only split on {413, 504} — the narrower Python set undercounted dense
# days that 500/502/503'd, per api/reportados.js's own comment history).
SPLITTABLE_STATUSES = frozenset({413, 500, 502, 503, 504})

_USER_AGENT = "sismo-cali-backend/1.0"  # Cloudflare 403s requests with none


class ApiCredentialsError(RuntimeError):
    """VISITADOS_API_PASS is not configured."""


class ApiUnavailableError(RuntimeError):
    """The atencionsismo API is unreachable or answered a non-alive status
    to the cheap probe request. Carries an HTTP status to surface (503 by
    default, matching api/reportados.js's probeApi)."""

    def __init__(self, message: str, *, status: int = 503) -> None:
        super().__init__(message)
        self.status = status


class ApiEmptyResultError(RuntimeError):
    """The day-walk completed but returned zero reports — treated as a
    transient upstream failure, matching api/reportados.js: never
    cache/serve an empty count over a transient failure."""


def credentials_from_env() -> tuple[str, str]:
    """Read (user, password) for HTTP Basic auth. Raises
    ``ApiCredentialsError`` if the password is unset — this is a "plain
    secret" (design.md ADR-4 table), validated here rather than through
    `credentials/clients.py`'s named-client mechanism."""
    user = os.environ.get(USER_ENV, "").strip() or DEFAULT_USER
    password = os.environ.get(PASS_ENV, "").strip()
    if not password:
        raise ApiCredentialsError(f"{PASS_ENV} is not set")
    return user, password


def _basic_auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _headers(user: str, password: str) -> dict[str, str]:
    return {"Authorization": _basic_auth_header(user, password), "User-Agent": _USER_AGENT}


def coord_key(lat: object, lng: object) -> str | None:
    """Dedup key for "Inmuebles reportados": the exact lat,lng pair. Null
    coords / (0,0) can't be grouped by location, so they are excluded
    (matching api/reportados.js's coordKey)."""
    try:
        a = float(lat)  # type: ignore[arg-type]
        b = float(lng)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    if a == 0 and b == 0:
        return None
    return f"{a},{b}"


def _parse_desde(desde: str) -> int:
    dt = datetime.strptime(desde, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def now_iso() -> str:
    """UTC timestamp for the `generado` response field. Public (not
    `_now_iso`) — `app/services/snapshot.py`'s Blob-seed path reuses it so
    a seeded snapshot's `generado` field is stamped the same way a live
    refresh's is."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def summarize(records: Iterable[Mapping[str, object]]) -> dict:
    """Dedupe `records` by `id`, tally `por_estadoVerificacion`, and count
    unique "inmuebles" by coordinate pair. Accepts BOTH the live day-walk's
    normalized shape (`id`/`estado`/`lat`/`lng`) and the Blob-published
    `reportes.json` shape (`id`/`estadoVerificacion`/`lat`/`lng`) so
    `app/services/snapshot.py`'s cold-start Blob seed can reuse this exact
    counting logic instead of a second parallel implementation."""
    seen: dict[object, str] = {}
    coords: set[str] = set()
    for rec in records:
        rid = rec.get("id")
        if not rid or rid in seen:
            continue
        estado = rec.get("estado")
        if not estado:
            estado = rec.get("estadoVerificacion") or "—"
        seen[rid] = estado
        lat = rec.get("lat")
        if lat is None:
            lat = rec.get("latitud")
        lng = rec.get("lng")
        if lng is None:
            lng = rec.get("longitud")
        key = coord_key(lat, lng)
        if key:
            coords.add(key)

    por_estado: dict[str, int] = {}
    for estado in seen.values():
        por_estado[estado] = por_estado.get(estado, 0) + 1

    return {"total": len(seen), "inmuebles": len(coords), "por_estadoVerificacion": por_estado}


async def probe_api(client: httpx.AsyncClient, user: str, password: str) -> None:
    """One cheap 1-minute-window probe before the full day walk: while the
    API is down for maintenance it answers non-2xx to everything, and
    without this the walk would retry every window 3 times before giving
    up. 413/504 = alive but window too dense (fine); anything else
    non-2xx, or a network failure, raises ``ApiUnavailableError``
    (api/reportados.js's probeApi, ported verbatim)."""
    now = int(time.time() * 1000)
    try:
        resp = await client.get(
            API_URL,
            params={"desde_utc": now - MIN_WINDOW_MS, "hasta_utc": now},
            headers=_headers(user, password),
            timeout=PROBE_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise ApiUnavailableError(f"API no disponible (sin respuesta: {exc})", status=503) from exc
    if resp.status_code >= 400 and resp.status_code not in (413, 504):
        raise ApiUnavailableError(f"API no disponible (HTTP {resp.status_code})", status=503)


async def fetch_window(
    client: httpx.AsyncClient,
    user: str,
    password: str,
    d0: int,
    d1: int,
    *,
    failed_windows: list[tuple[int, int]] | None = None,
) -> list[dict]:
    """Fetch [d0, d1] (ms UTC epoch). Recursively halves SEQUENTIALLY (not
    concurrently — a concurrent split would fan a dense window out into an
    exponential request burst) on any `SPLITTABLE_STATUSES` response wider
    than `MIN_WINDOW_MS`. Otherwise retries up to `MAX_ATTEMPTS` times with
    a `RETRY_SLEEP_S` backoff, then gives up and returns `[]` — appending
    `(d0, d1)` to `failed_windows` if the caller passed one, for a later
    sequential recovery pass (api/reportados.js's fetchWindow, ported)."""
    headers = _headers(user, password)
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = await client.get(
                API_URL,
                params={"desde_utc": d0, "hasta_utc": d1},
                headers=headers,
                timeout=REQUEST_TIMEOUT_S,
            )
            splittable = resp.status_code in SPLITTABLE_STATUSES
            if splittable and (d1 - d0) > MIN_WINDOW_MS:
                mid = (d0 + d1) // 2
                first = await fetch_window(client, user, password, d0, mid, failed_windows=failed_windows)
                second = await fetch_window(client, user, password, mid + 1, d1, failed_windows=failed_windows)
                return first + second
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            data = resp.json()
            return [
                {
                    "id": r.get("id"),
                    "estado": r.get("estadoVerificacion") or "—",
                    "lat": r.get("latitud"),
                    "lng": r.get("longitud"),
                }
                for r in data.get("reportes", [])
            ]
        except httpx.HTTPError:
            if attempt == MAX_ATTEMPTS - 1:
                break
            if RETRY_SLEEP_S:
                import asyncio

                await asyncio.sleep(RETRY_SLEEP_S)
    if failed_windows is not None:
        failed_windows.append((d0, d1))
    return []


async def count_reportes(
    client: httpx.AsyncClient,
    user: str,
    password: str,
    desde: str,
    *,
    until_ms: int | None = None,
) -> dict:
    """Walk `desde` (YYYY-MM-DD) through `until_ms` (default: now + 1 day)
    in day windows, `CONCURRENCY` at a time, then run one sequential retry
    pass over windows that gave up — anything that fails twice stays
    dropped (api/reportados.js's countReportes, ported). Returns the
    `summarize()` shape."""
    import asyncio

    start = _parse_desde(desde)
    end = until_ms if until_ms is not None else int(time.time() * 1000) + DAY_MS
    windows = [(d0, d0 + DAY_MS - 1) for d0 in range(start, end, DAY_MS)]

    all_records: list[dict] = []
    failed_windows: list[tuple[int, int]] = []

    for i in range(0, len(windows), CONCURRENCY):
        batch = windows[i : i + CONCURRENCY]
        results = await asyncio.gather(
            *[
                fetch_window(client, user, password, d0, d1, failed_windows=failed_windows)
                for d0, d1 in batch
            ]
        )
        for records in results:
            all_records.extend(records)

    retry = failed_windows[:]
    failed_windows.clear()
    for d0, d1 in retry:
        all_records.extend(
            await fetch_window(client, user, password, d0, d1, failed_windows=failed_windows)
        )

    return summarize(all_records)


async def fetch_reportados(
    client: httpx.AsyncClient, *, desde: str | None = None, until_ms: int | None = None
) -> dict:
    """Full refresh cycle: read credentials, probe, day-walk, and wrap into
    the exact response shape `web/js/data.js` consumes
    (`por_estadoVerificacion.Reportado`, `inmuebles`) — matching
    `api/reportados.js`'s response body. Raises ``ApiEmptyResultError`` if
    the walk returns zero reports (never serve/cache an empty count over a
    transient upstream failure)."""
    user, password = credentials_from_env()
    await probe_api(client, user, password)
    counts = await count_reportes(client, user, password, desde or DEFAULT_DESDE, until_ms=until_ms)
    if counts["total"] == 0:
        raise ApiEmptyResultError("la API devolvió 0 reportes")
    return {
        "ok": True,
        "generado": now_iso(),
        "fuente": "api:informe/json",
        **counts,
    }
