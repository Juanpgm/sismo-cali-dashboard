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

import asyncio
import base64
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping

import httpx

API_URL = "https://atencionsismo.cali.gov.co/api/informe/json"
STICKERS_URL = "https://atencionsismo.cali.gov.co/api/informe/stickers"
PAGE_LIMIT = 200  # v2 contract: 1..200 rows per page
MAX_PAGES = 500  # hard stop (100k rows) against a server that never reports done

USER_ENV = "VISITADOS_API_USER"
PASS_ENV = "VISITADOS_API_PASS"
DEFAULT_USER = "juanp.gzmz@gmail.com"

DESDE_ENV = "REPORTES_DESDE"
DEFAULT_DESDE = os.environ.get(DESDE_ENV, "2026-08-01")

DAY_MS = 86_400_000
MIN_WINDOW_MS = 60_000  # smallest window before giving up on a split
MAX_PAGES_PER_WINDOW = 50  # hard stop (50*PAGE_LIMIT=10k rows/day) against a
# server that never reports done=true within one fetch_window call — a
# SEPARATE constant from MAX_PAGES (fetch_stickers' own cap), same naming
# convention, different scope.
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


# Analytic fields aggregated per record (user directive 2026-08-25: metrics
# over ALL records, not just the estadoVerificacion tally the legacy JS kept).
# Same field set scripts/fetch_reportes_api.py's build_aggregations() uses for
# the static reportes_agg.json, so both readings agree on category names.
AGG_FIELDS = ("estadoVerificacion", "afectacion", "comuna", "habitabilidad", "tipoInmueble")


def _sorted_counts(counter: Mapping[str, int]) -> dict:
    """Count-desc then name, matching build_aggregations()'s presentation."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0]))))


def summarize(records: Iterable[Mapping[str, object]]) -> dict:
    """Dedupe `records` by `id` and aggregate over ALL of them: the legacy
    fields (`total`, `inmuebles`, `por_estadoVerificacion` — consumer parity
    with api/reportados.js) PLUS the full metric set the atencionsismo API
    carries per record (`por_afectacion`, `por_comuna`, `por_habitabilidad`,
    `por_tipoInmueble`, coordinate coverage, and `sin_id` for records the
    dedupe had to drop for lacking an id — nothing is silently ignored).
    Accepts BOTH the live day-walk's normalized shape and the Blob-published
    `reportes.json` shape so `app/services/snapshot.py`'s cold-start Blob
    seed reuses this exact logic."""
    seen: dict[object, Mapping[str, object]] = {}
    coords: set[str] = set()
    sin_id = 0
    con_coordenadas = 0
    for rec in records:
        rid = rec.get("id")
        if not rid:
            sin_id += 1
            continue
        if rid in seen:
            continue
        seen[rid] = rec
        lat = rec.get("lat")
        if lat is None:
            lat = rec.get("latitud")
        lng = rec.get("lng")
        if lng is None:
            lng = rec.get("longitud")
        key = coord_key(lat, lng)
        if key:
            coords.add(key)
            con_coordenadas += 1

    tallies: dict[str, dict[str, int]] = {f: {} for f in AGG_FIELDS}
    for rec in seen.values():
        for field in AGG_FIELDS:
            value = rec.get(field)
            # Live day-walk normalizes estadoVerificacion into `estado`.
            if value in (None, "") and field == "estadoVerificacion":
                value = rec.get("estado")
            value = value if value not in (None, "") else "—"
            bucket = tallies[field]
            bucket[str(value)] = bucket.get(str(value), 0) + 1

    return {
        "total": len(seen),
        "inmuebles": len(coords),
        "con_coordenadas": con_coordenadas,
        "sin_coordenadas": len(seen) - con_coordenadas,
        "sin_id": sin_id,
        "por_estadoVerificacion": _sorted_counts(tallies["estadoVerificacion"]),
        "por_afectacion": _sorted_counts(tallies["afectacion"]),
        "por_comuna": _sorted_counts(tallies["comuna"]),
        "por_habitabilidad": _sorted_counts(tallies["habitabilidad"]),
        "por_tipoInmueble": _sorted_counts(tallies["tipoInmueble"]),
    }


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


RecordMapper = Callable[[dict], dict]


def _map_summary_record(r: dict) -> dict:
    """Default per-record shape: the ANALYTIC fields only (user directive:
    metrics over all records) — id/coords for dedupe/inmuebles plus every
    AGG_FIELDS category. Deliberately nothing else: no PII, no heavy nested
    lists, ever. This is what the `/reportados` snapshot needs; the
    `dashboard_refresh` job's fuller `reportes.json` export passes its own
    `mapper` (see `app/jobs/dashboard_refresh.py`) instead of duplicating
    this day-walk (design.md ADR-5, task 7.2)."""
    return {
        "id": r.get("id"),
        "estado": r.get("estadoVerificacion") or "—",
        "lat": r.get("latitud"),
        "lng": r.get("longitud"),
        "afectacion": r.get("afectacion"),
        "comuna": r.get("comuna"),
        "habitabilidad": r.get("habitabilidad"),
        "tipoInmueble": r.get("tipoInmueble"),
    }


async def _fetch_window_page(
    client: httpx.AsyncClient,
    user: str,
    password: str,
    headers: dict[str, str],
    d0: int,
    d1: int,
    offset: int,
    *,
    failed_windows: list[tuple[int, int]] | None,
    mapper: RecordMapper | None,
) -> tuple[str, object]:
    """Fetch ONE page (`offset`/`limit=PAGE_LIMIT`) of `[d0, d1]`, with
    `MAX_ATTEMPTS` retries on transport errors / non-2xx (unchanged
    semantics from before pagination — just scoped to a single page now).

    Returns a `(status, payload)` tag:
    - `("ok", body)` — the page's raw decoded JSON body.
    - `("split", records)` — a `SPLITTABLE_STATUSES` response was hit on
      THIS page (first or later) while the window is still wider than
      `MIN_WINDOW_MS`; the window was recursively halved right here (same
      `SPLITTABLE_STATUSES` behavior as before, now reachable from any
      page) and `records` is the two halves' own independently-paginated
      result — the caller must return it AS-IS, discarding any records
      already accumulated from earlier pages of this now-abandoned
      whole-window attempt.
    - `("gave_up", None)` — this page's retries were exhausted (not
      splittable, or already at `MIN_WINDOW_MS`); the caller must treat the
      WHOLE window as failed, not just this page."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = await client.get(
                API_URL,
                params={"desde_utc": d0, "hasta_utc": d1, "offset": offset, "limit": PAGE_LIMIT},
                headers=headers,
                timeout=REQUEST_TIMEOUT_S,
            )
            splittable = resp.status_code in SPLITTABLE_STATUSES
            if splittable and (d1 - d0) > MIN_WINDOW_MS:
                mid = (d0 + d1) // 2
                first = await fetch_window(
                    client, user, password, d0, mid, failed_windows=failed_windows, mapper=mapper
                )
                second = await fetch_window(
                    client, user, password, mid + 1, d1, failed_windows=failed_windows, mapper=mapper
                )
                return "split", first + second
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            return "ok", resp.json()
        except httpx.HTTPError:
            if attempt == MAX_ATTEMPTS - 1:
                break
            if RETRY_SLEEP_S:
                await asyncio.sleep(RETRY_SLEEP_S)
    return "gave_up", None


async def fetch_window(
    client: httpx.AsyncClient,
    user: str,
    password: str,
    d0: int,
    d1: int,
    *,
    failed_windows: list[tuple[int, int]] | None = None,
    mapper: RecordMapper | None = None,
) -> list[dict]:
    """Fetch [d0, d1] (ms UTC epoch), fully paginating within the window via
    `offset`/`nextOffset` (v2 contract, `PAGE_LIMIT` rows/page) before
    returning — a day can hold far more than one page's worth of reports,
    and returning only page 1 silently truncates dense days.

    A missing `done` key is treated as `True` (single page, stop) — every
    existing mock fixture omits `done`/`nextOffset` entirely and must keep
    behaving as a single-page fetch. A `False` `done` follows `nextOffset`;
    a missing, non-int, or non-advancing `nextOffset` stops pagination for
    this window (malformed-but-200 response, NOT a transport/status
    failure, so `failed_windows` is left untouched) rather than looping
    forever — the same "stop rather than loop forever" discipline
    `fetch_stickers` already uses for its own offset cursor. A hard
    `MAX_PAGES_PER_WINDOW` cap guards against a server that never reports
    `done` at all.

    Recursively halves SEQUENTIALLY (not concurrently — a concurrent split
    would fan a dense window out into an exponential request burst) on any
    `SPLITTABLE_STATUSES` response wider than `MIN_WINDOW_MS`, whether it
    hits on the first page or a later one — the two halves each
    independently paginate the range they cover, superseding (not
    appending to) whatever partial pages this window had already
    accumulated. Otherwise a page's `MAX_ATTEMPTS` retries (with
    `RETRY_SLEEP_S` backoff) exhausting means the WHOLE window gives up and
    returns `[]` — appending `(d0, d1)` to `failed_windows` if the caller
    passed one, for a later sequential recovery pass (api/reportados.js's
    fetchWindow, ported).

    `mapper` shapes each raw API record into the caller's desired dict;
    defaults to `_map_summary_record` (the `/reportados` snapshot's slim
    shape) so every existing caller/test is unaffected. `dashboard_refresh`
    passes a fuller mapper to reuse this exact split/retry mechanics for
    `reportes.json` instead of duplicating it (task 7.2)."""
    active_mapper = mapper or _map_summary_record
    headers = _headers(user, password)
    offset = 0
    records: list[dict] = []
    for _page in range(MAX_PAGES_PER_WINDOW):
        status, payload = await _fetch_window_page(
            client, user, password, headers, d0, d1, offset, failed_windows=failed_windows, mapper=mapper
        )
        if status == "split":
            return payload  # supersedes anything accumulated so far — do not merge
        if status == "gave_up":
            if failed_windows is not None:
                failed_windows.append((d0, d1))
            return []
        body = payload
        records.extend(active_mapper(r) for r in body.get("reportes", []))
        if "done" not in body or body.get("done"):
            return records
        next_offset = _coerce_offset(body.get("nextOffset"))
        if next_offset is None or next_offset <= offset:
            return records  # malformed/non-advancing cursor: stop, not a failure
        offset = next_offset
    # MAX_PAGES_PER_WINDOW safety cap reached WITHOUT the API ever reporting
    # done: True — reintroducing the exact silent-truncation shape this fix
    # closes (just at 10,000 rows/day instead of 200). Never expected in
    # practice (see the module's own docstring for the real observed
    # ceiling), but loud on the way out rather than a quiet return.
    logging.warning(
        "fetch_window: tope de %d paginas alcanzado para [%d, %d] sin done=true; "
        "puede haber reportes sin traer",
        MAX_PAGES_PER_WINDOW, d0, d1,
    )
    return records


async def day_walk(
    client: httpx.AsyncClient,
    user: str,
    password: str,
    desde: str,
    *,
    until_ms: int | None = None,
    mapper: RecordMapper | None = None,
) -> list[dict]:
    """Walk `desde` (YYYY-MM-DD) through `until_ms` (default: now + 1 day)
    in day windows, `CONCURRENCY` at a time, then run one sequential retry
    pass over windows that gave up — anything that fails twice stays
    dropped (api/reportados.js's countReportes, ported). Returns the raw
    (mapped, NOT deduped/aggregated) record list — `count_reportes()` is
    `summarize(day_walk(...))`; `dashboard_refresh` calls this directly with
    a fuller `mapper` for `reportes.json` (task 7.2)."""
    start = _parse_desde(desde)
    end = until_ms if until_ms is not None else int(time.time() * 1000) + DAY_MS
    windows = [(d0, d0 + DAY_MS - 1) for d0 in range(start, end, DAY_MS)]

    all_records: list[dict] = []
    failed_windows: list[tuple[int, int]] = []

    for i in range(0, len(windows), CONCURRENCY):
        batch = windows[i : i + CONCURRENCY]
        results = await asyncio.gather(
            *[
                fetch_window(client, user, password, d0, d1, failed_windows=failed_windows, mapper=mapper)
                for d0, d1 in batch
            ]
        )
        for records in results:
            all_records.extend(records)

    retry = failed_windows[:]
    failed_windows.clear()
    for d0, d1 in retry:
        all_records.extend(
            await fetch_window(client, user, password, d0, d1, failed_windows=failed_windows, mapper=mapper)
        )

    return all_records


async def count_reportes(
    client: httpx.AsyncClient,
    user: str,
    password: str,
    desde: str,
    *,
    until_ms: int | None = None,
) -> dict:
    """`summarize(day_walk(...))` — kept as its own function for the
    existing `/reportados` snapshot call sites; see `day_walk` for the
    underlying walk/retry mechanics."""
    records = await day_walk(client, user, password, desde, until_ms=until_ms)
    return summarize(records)


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


async def _get_stickers_page(
    client: httpx.AsyncClient, headers: dict[str, str], params: dict[str, int]
) -> dict:
    """One page of GET /api/informe/stickers with MAX_ATTEMPTS retries on
    transport/5xx/malformed-body errors. Any 4xx (400..499) is a client-error
    problem (bad params, unset password, account without `api: read`,
    password not yet created at /ingresar) — retrying an identical request
    cannot fix it, so it raises immediately without burning MAX_ATTEMPTS."""
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = await client.get(STICKERS_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_S)
            if resp.status_code in (401, 403):
                raise ApiUnavailableError(
                    f"informe/stickers HTTP {resp.status_code}: credenciales rechazadas", status=503
                )
            if 400 <= resp.status_code < 500:
                raise ApiUnavailableError(
                    f"informe/stickers HTTP {resp.status_code}: error de cliente, no reintentable",
                    status=503,
                )
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("el cuerpo no es un objeto JSON")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt < MAX_ATTEMPTS - 1 and RETRY_SLEEP_S:
                await asyncio.sleep(RETRY_SLEEP_S)
    raise ApiUnavailableError(f"informe/stickers sin respuesta valida: {last_exc}", status=503)


def _coerce_offset(value: object) -> int | None:
    """`nextOffset` as an actual int cursor. Accepts a plain int and a
    numeric string (some pagination responses have been observed sending
    the cursor as text); anything else (missing, bool, non-numeric string,
    float) is not a usable cursor."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


async def fetch_stickers(
    client: httpx.AsyncClient,
    user: str,
    password: str,
    *,
    desde_utc: int | None = None,
    hasta_utc: int | None = None,
) -> list[dict]:
    """Full offset-paginated read of GET /api/informe/stickers (v2 contract:
    `offset`/`limit` <= 200, follow `nextOffset` until `done`). Unlike the
    `informe/json` day-walk there is no date-window failure mode to split
    on, so each page is simply retried. Returns the raw `stickers[]` rows.

    Pagination must reach an explicit `done` page to be considered
    complete: a non-int/non-advancing cursor, or exhausting `MAX_PAGES`
    without ever seeing `done`, raises `ApiUnavailableError` instead of
    silently returning a partial universe — a truncated sticker list is
    worse than a 503 (the cache's serve-stale/Blob chain handles that
    better than a caller ever could). A page missing the `done` key
    entirely is treated as the end of pagination (`done=True`), but logged
    as a warning since the contract doesn't document that shape.

    Zero rows over a COMPLETE walk raises ApiEmptyResultError — never cache
    an empty universe over a transient failure (same rule as
    `fetch_reportados`)."""
    headers = _headers(user, password)
    rows: list[dict] = []
    offset = 0
    complete = False
    for _ in range(MAX_PAGES):
        params: dict[str, int] = {"offset": offset, "limit": PAGE_LIMIT}
        if desde_utc is not None:
            params["desde_utc"] = desde_utc
        if hasta_utc is not None:
            params["hasta_utc"] = hasta_utc
        body = await _get_stickers_page(client, headers, params)
        rows.extend(r for r in (body.get("stickers") or []) if isinstance(r, dict))
        if "done" not in body:
            logging.warning(
                "informe/stickers: pagina en offset=%s sin campo 'done'; se asume fin de paginacion", offset
            )
            complete = True
            break
        if body.get("done"):
            complete = True
            break
        next_offset = _coerce_offset(body.get("nextOffset"))
        if next_offset is None or next_offset <= offset:
            break  # malformed/non-advancing cursor: `complete` stays False
        offset = next_offset
    if not complete:
        raise ApiUnavailableError(
            "informe/stickers: paginacion incompleta (cursor invalido, no avanza, o MAX_PAGES agotado)",
            status=503,
        )
    if not rows:
        raise ApiEmptyResultError("informe/stickers devolvio 0 filas")
    return rows
