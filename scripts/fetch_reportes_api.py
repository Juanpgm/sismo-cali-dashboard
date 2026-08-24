"""Full-pull idempotent fetch of the atencionsismo `informe/json` API.

The dashboard's map/analytics need every registered report with coordinates,
not just the critical ones. The API times out (504) when asked for the whole
history at once and rejects the densest single days with 413, so this script
walks the date range in day windows and splits any window that still 413/504s
down to the minute. Results are deduped by report `id` and written to three
static files under `web/data/`, following the same publish-static-JSON pattern
as `refresh_data.py`:

  reportes.json       flat records (PII stripped) for pandas / dashboard reads
  reportes_meta.json  {generated_at, row_count, ...} — freshness + publish guard
  reportes_agg.json   pre-computed headline aggregations (instant analytics)

Idempotent: records are sorted by `id`, so an unchanged dataset yields a
byte-identical reportes.json (git sees no diff). Only meta advances every run.

Auth is HTTP Basic with the same operator/viewer credentials used elsewhere:
env VISITADOS_API_USER / VISITADOS_API_PASS (falls back to the default user).
Locally it also best-effort loads integracion_F1/.env. If the password is
absent it logs and exits 0 so it never blocks the main dashboard refresh.

Run:  python scripts/fetch_reportes_api.py [--desde 2026-08-01] [--check]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_reportes_api")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "web" / "data"

API_URL = "https://atencionsismo.cali.gov.co/api/informe/json"
USER_ENV = "VISITADOS_API_USER"
PASS_ENV = "VISITADOS_API_PASS"
DEFAULT_USER = "juanp.gzmz@gmail.com"

# Event floor: reports never predate this. Overridable via --desde / env.
DEFAULT_DESDE = os.environ.get("REPORTES_DESDE", "2026-08-01")

# Never publish these to a static, world-readable file.
PII_FIELDS = {"nombre", "telefono", "cedula", "correo", "matriculaProfesional"}
# Heavy / free-text lists dropped from the analytics export (photos + chat can
# carry PII in their text and bloat the file). Coordinates live at the root.
HEAVY_FIELDS = {"fotografiasEvaluacion", "mensajes"}

# Smallest window we bother splitting to before giving up on a 413/504 (1 min).
MIN_WINDOW_MS = 60_000


def _load_local_env() -> None:
    """Best-effort: make `python scripts/fetch_reportes_api.py` work locally."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for env_path in (REPO_ROOT / ".env", REPO_ROOT / "integracion_F1" / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)


def _credentials() -> tuple[str, str] | None:
    user = os.environ.get(USER_ENV, "").strip() or DEFAULT_USER
    password = os.environ.get(PASS_ENV, "").strip()
    if not password:
        return None
    return user, password


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class AuthError(Exception):
    """API rejected the credentials (401/403). This is a global auth failure, not
    a per-window hiccup, so it aborts the whole pull instead of being retried and
    swallowed once per day-window."""


def fetch_window(session: requests.Session, auth: tuple[str, str],
                 d0: int, d1: int) -> list[dict]:
    """Fetch [d0, d1] (ms UTC), recursively halving on 413/504 payload limits.
    Raises AuthError immediately on 401/403 (bad credentials) — no point
    retrying or splitting when the whole pull will fail the same way."""
    for attempt in range(3):
        try:
            resp = session.get(API_URL, params={"desde_utc": d0, "hasta_utc": d1},
                               auth=auth, timeout=90)
            if resp.status_code in (401, 403):
                raise AuthError(f"HTTP {resp.status_code}: {resp.text[:120]}")
            if resp.status_code in (413, 504):
                raise requests.HTTPError(str(resp.status_code), response=resp)
            resp.raise_for_status()
            return resp.json().get("reportes", [])
        except requests.HTTPError as exc:
            too_big = exc.response is not None and exc.response.status_code in (413, 504)
            if too_big and (d1 - d0) > MIN_WINDOW_MS:
                mid = (d0 + d1) // 2
                return fetch_window(session, auth, d0, mid) + \
                       fetch_window(session, auth, mid + 1, d1)
            if attempt == 2:
                start = datetime.fromtimestamp(d0 / 1000, tz=timezone.utc)
                log.warning("giving up on window %s (%s)", start.isoformat(), exc)
                return []
            time.sleep(2)
        except requests.RequestException as exc:
            if attempt == 2:
                log.warning("request failed for window (%s)", exc)
                return []
            time.sleep(2)
    return []


def strip_report(rep: dict) -> dict:
    """Keep every analytic field; drop PII and heavy lists. Add numeric coords."""
    out = {k: v for k, v in rep.items() if k not in PII_FIELDS and k not in HEAVY_FIELDS}
    lat, lng = _parse_coords(rep.get("latitud"), rep.get("longitud"))
    out["lat"] = lat
    out["lng"] = lng
    return out


def _parse_coords(latitud, longitud) -> tuple[float | None, float | None]:
    try:
        lat, lng = float(latitud), float(longitud)
    except (TypeError, ValueError):
        return None, None
    if lat == 0 and lng == 0:
        return None, None
    return lat, lng


def build_aggregations(records: list[dict], date_range: dict) -> dict:
    def counts(field: str) -> dict:
        c = Counter((r.get(field) or "—") for r in records)
        return dict(sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0]))))

    con_coords = sum(1 for r in records if r.get("lat") is not None)
    agg = {
        "generated_at": date_range["generated_at"],
        "total": len(records),
        "con_coordenadas": con_coords,
        "sin_coordenadas": len(records) - con_coords,
        "por_estadoVerificacion": counts("estadoVerificacion"),
        "por_afectacion": counts("afectacion"),
        "por_comuna": counts("comuna"),
        "por_habitabilidad": counts("habitabilidad"),
        "por_tipoInmueble": counts("tipoInmueble"),
    }
    # Self-check: a per-category breakdown must reconcile with the total.
    assert sum(agg["por_estadoVerificacion"].values()) == len(records), \
        "aggregation lost records"
    assert con_coords + agg["sin_coordenadas"] == len(records)
    return agg


def _atomic_write_json(path: Path, obj, compact: bool = False) -> None:
    # reportes.json is ~10k records committed every refresh; minify it so git
    # history and Railway clones stay light. meta/agg are tiny → keep readable.
    kwargs = {"separators": (",", ":")} if compact else {"indent": 2}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, **kwargs), encoding="utf-8")
    os.replace(tmp, path)


def run(desde: str) -> int:
    creds = _credentials()
    if creds is None:
        log.warning("%s not set; skipping reportes fetch (main refresh continues).",
                    PASS_ENV)
        return 0

    start = datetime.strptime(desde, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    log.info("Fetching reportes %s → %s (day windows, adaptive split)…",
             start.date(), end.date())

    session = requests.Session()
    seen: dict[str, dict] = {}
    day = start
    try:
        while day < end:
            d0 = _ms(day)
            d1 = _ms(day + timedelta(days=1)) - 1
            reps = fetch_window(session, creds, d0, d1)
            new = 0
            for rep in reps:
                rid = rep.get("id")
                if rid and rid not in seen:
                    seen[rid] = strip_report(rep)
                    new += 1
            if reps:
                log.info("%s: %d reportes (%d nuevos)", day.date(), len(reps), new)
            day += timedelta(days=1)
    except AuthError as exc:
        # Wrong/expired credentials fail every window identically — stop on the
        # first one. Fail-soft for the overall refresh: keep previous files.
        log.error(
            "Reportes API rechazó las credenciales de %s (%s). Actualizá %s en "
            "Railway con la contraseña autorizada. Detalle: %s. "
            "El refresh principal (survey123) continúa; reportes no se actualizan.",
            creds[0], USER_ENV, PASS_ENV, exc,
        )
        return 0

    records = [seen[k] for k in sorted(seen)]  # stable order → idempotent file
    if not records:
        # Never wipe good published data over a transient empty API response;
        # keep the previous reportes.json (like entrypoint's "never publish empty").
        log.warning("API returned 0 reportes; keeping previous files, nothing written.")
        return 0
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_range = {"generated_at": generated_at, "desde": desde,
                  "hasta": end.date().isoformat()}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(OUT_DIR / "reportes.json", records, compact=True)
    _atomic_write_json(OUT_DIR / "reportes_meta.json", {
        "generated_at": generated_at,
        "row_count": len(records),
        "source": "api:informe/json",
        "date_range": date_range,
    })
    _atomic_write_json(OUT_DIR / "reportes_agg.json",
                       build_aggregations(records, date_range))
    log.info("Wrote %d reportes → web/data/reportes.json (+ meta, +agg).",
             len(records))
    return len(records)


def check() -> None:
    """Validate the published files: no PII leaked, counts reconcile."""
    records = json.loads((OUT_DIR / "reportes.json").read_text(encoding="utf-8"))
    meta = json.loads((OUT_DIR / "reportes_meta.json").read_text(encoding="utf-8"))
    agg = json.loads((OUT_DIR / "reportes_agg.json").read_text(encoding="utf-8"))
    assert meta["row_count"] == len(records) == agg["total"], "count mismatch"
    leaked = PII_FIELDS.intersection(records[0]) if records else set()
    assert not leaked, f"PII leaked into reportes.json: {leaked}"
    assert sum(agg["por_comuna"].values()) == len(records), "agg mismatch"
    log.info("check OK: %d records, no PII, aggregations reconcile.", len(records))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desde", default=DEFAULT_DESDE,
                        help="Floor date YYYY-MM-DD (default %(default)s).")
    parser.add_argument("--check", action="store_true",
                        help="Validate already-written files instead of fetching.")
    args = parser.parse_args()

    _load_local_env()
    if args.check:
        check()
    else:
        run(args.desde)


if __name__ == "__main__":
    main()
