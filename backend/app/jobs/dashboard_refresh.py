"""Cron entrypoint: `python -m app.jobs.dashboard_refresh` (design.md ADR-6).

Absorbs `deploy/refresh.sh`'s pipeline:

1. best-effort seed the geocode cache from Blob;
2. run `scripts/refresh_data.py` (Survey123 → `web/data/{meta,inspections}.json`);
3. fetch `reportes.json`/`reportes_meta.json`/`reportes_agg.json` — formerly
   `scripts/fetch_reportes_api.py`'s OWN day-walk, now calling
   `app.services.atencionsismo`'s day-walk/split-retry (`day_walk`, with a
   full-field `mapper`) instead of duplicating it (design.md ADR-5, task 7.2);
4. meta-guard: never publish over empty/broken `refresh_data.py` output;
5. publish every file to Vercel Blob.

Preserves `deploy/refresh.sh`'s timeout/meta-guard/trap structure (each step
tracked in `STEP`, a best-effort `_status.json` Blob write on the way out —
mirroring the bash `trap report_status EXIT`) WITHOUT the clone-at-start
`entrypoint.sh`/`DASHBOARD_REPO_TOKEN` machinery: the code is already IN the
image (`backend/Dockerfile` COPYs `scripts/` + `deploy/` alongside `backend/`,
design.md ADR-1), so there is nothing left to clone.

Durable logging follows the same harness every other absorbed job uses
(`app.integracion.runlog`: `resolve_log_dir` -> `start_tee` -> run ->
`append_run`, `integracion_F1/job_sticker.py`'s pattern) — a SEPARATE
destination (the Railway volume's `runs.jsonl`) from step 5's public Blob
`_status.json`; both are preserved, matching `deploy/refresh.sh` + this job
family's own convention side by side.

    python -m app.jobs.dashboard_refresh          # real run (network + I/O)
    python -m app.jobs.dashboard_refresh --check  # offline self-check, no network
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.integracion import runlog
from app.services import atencionsismo

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEPLOY_DIR = REPO_ROOT / "deploy"
WEB_DATA_DIR = REPO_ROOT / "web" / "data"

# deploy/blob_sync.py is a plain script, not a package (COPYd verbatim into
# the image per ADR-1) — imported directly rather than re-implemented, same
# "call it, don't duplicate it" principle task 7.2 applies to atencionsismo.
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))
import blob_sync  # noqa: E402  (path must be set up first)

RUNS_FILE = "runs_dashboard_refresh.jsonl"

REFRESH_DATA_TIMEOUT_S = 300  # deploy/refresh.sh: timeout 300 python refresh_data.py
FETCH_REPORTES_TIMEOUT_S = 240  # deploy/refresh.sh: timeout 240 python fetch_reportes_api.py
SEED_GEOCODE_TIMEOUT_S = 30

# Never publish these to a static, world-readable file — verbatim from
# scripts/fetch_reportes_api.py's PII_FIELDS/HEAVY_FIELDS (task 7.2: the
# "keep every analytic field" contract for reportes.json is unchanged, only
# the day-walk mechanics moved to app.services.atencionsismo).
PII_FIELDS = {"nombre", "telefono", "cedula", "correo", "matriculaProfesional"}
HEAVY_FIELDS = {"fotografiasEvaluacion", "mensajes"}

# (local file under web/data/, blob pathname, max-age) — verbatim from
# deploy/refresh.sh's up() calls, uniform --max-age 60 for every file.
_PUBLISH_FILES: tuple[tuple[str, str], ...] = (
    ("meta.json", "data/meta.json"),
    ("inspections.json", "data/inspections.json"),
    ("inspections.xlsx", "data/inspections.xlsx"),
    ("reportes.json", "data/reportes.json"),
    ("reportes_meta.json", "data/reportes_meta.json"),
    ("reportes_agg.json", "data/reportes_agg.json"),
    (os.path.join("geocode", "geocode_cache.json"), "data/geocode/geocode_cache.json"),
)
_PUBLISH_MAX_AGE_S = 60


# ── Raw record mapper (reportes.json needs every analytic field) ───────────


def _parse_coords(latitud, longitud) -> tuple[float | None, float | None]:
    try:
        lat, lng = float(latitud), float(longitud)
    except (TypeError, ValueError):
        return None, None
    if lat == 0 and lng == 0:
        return None, None
    return lat, lng


def _raw_record_mapper(rep: dict) -> dict:
    """Keep every analytic field (PII/heavy stripped) — same contract as the
    legacy `fetch_reportes_api.py`'s `strip_report()`, passed as
    `atencionsismo.day_walk`'s `mapper` hook so `reportes.json` keeps the
    FULL field set the dashboard's map/analytics need (unlike the day-walk's
    default AGG-only mapper, which only the `/reportados` snapshot needs)."""
    out = {k: v for k, v in rep.items() if k not in PII_FIELDS and k not in HEAVY_FIELDS}
    lat, lng = _parse_coords(rep.get("latitud"), rep.get("longitud"))
    out["lat"] = lat
    out["lng"] = lng
    return out


def _dedupe_sorted(records: list[dict]) -> list[dict]:
    """First-occurrence-wins dedup by `id`, then sorted by id — the same
    idempotency contract `fetch_reportes_api.py`'s `run()` had: an unchanged
    dataset (even fetched via slightly different day-walk window ordering)
    yields a byte-identical `reportes.json`."""
    seen: dict[object, dict] = {}
    for rec in records:
        rid = rec.get("id")
        if rid and rid not in seen:
            seen[rid] = rec
    return [seen[k] for k in sorted(seen, key=str)]


def _atomic_write_json(path: Path, obj, *, compact: bool = False) -> None:
    kwargs = {"separators": (",", ":")} if compact else {"indent": 2}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, **kwargs), encoding="utf-8")
    os.replace(tmp, path)


async def fetch_reportes() -> int:
    """Absorbs `fetch_reportes_api.py`'s `run()`: day-walk via
    `app.services.atencionsismo.day_walk` (instead of duplicating the
    split/retry logic), then write `reportes.json` + `reportes_meta.json` +
    `reportes_agg.json`. Fail-soft (returns 0, logs, main refresh continues)
    when `VISITADOS_API_PASS` is unset — verbatim behavior from the legacy
    script's own fail-soft credentials guard."""
    try:
        user, password = atencionsismo.credentials_from_env()
    except atencionsismo.ApiCredentialsError as exc:
        print(f"  {exc}; sigo sin actualizar reportes (refresh principal continúa).")
        return 0

    desde = os.environ.get("REPORTES_DESDE", atencionsismo.DEFAULT_DESDE)
    async with httpx.AsyncClient() as client:
        raw_records = await atencionsismo.day_walk(client, user, password, desde, mapper=_raw_record_mapper)

    records = _dedupe_sorted(raw_records)
    if not records:
        print("  API devolvió 0 reportes; conservo los archivos previos, nada escrito.")
        return 0

    generated_at = atencionsismo.now_iso()
    WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(WEB_DATA_DIR / "reportes.json", records, compact=True)
    _atomic_write_json(
        WEB_DATA_DIR / "reportes_meta.json",
        {
            "generated_at": generated_at,
            "row_count": len(records),
            "source": "api:informe/json",
            "date_range": {"generated_at": generated_at, "desde": desde},
        },
    )
    # Reuse atencionsismo.summarize() for the aggregation — same "single
    # implementation" principle as the day-walk itself (design.md ADR-5;
    # app/services/snapshot.py's Blob-seed path already established this
    # precedent for reportes.json's own field shape).
    _atomic_write_json(
        WEB_DATA_DIR / "reportes_agg.json",
        {"generated_at": generated_at, **atencionsismo.summarize(records)},
    )
    print(f"  {len(records)} reportes -> web/data/reportes.json (+ meta, +agg).")
    return len(records)


# ── meta-guard: never publish empty/broken refresh_data.py output ──────────


def _meta_guard() -> int:
    meta_path = WEB_DATA_DIR / "meta.json"
    if not meta_path.exists():
        raise RuntimeError("meta.json no se generó; aborto sin publicar")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    row_count = int(data.get("row_count", 0))
    if row_count <= 0:
        raise RuntimeError(f"row_count={row_count} inválido; aborto sin publicar")
    return row_count


# ── Blob publish ─────────────────────────────────────────────────────────


def seed_geocode() -> None:
    """Best-effort: seed the geocode cache from Blob before refresh_data.py
    runs (its cache used to ride in git; now lives in Blob). First run/miss
    just starts empty — matches deploy/refresh.sh's `|| true`."""
    cache_dir = WEB_DATA_DIR / "geocode"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blob_sync.download("data/geocode/geocode_cache.json", str(cache_dir / "geocode_cache.json"))


def _publish_all() -> None:
    for local_name, pathname in _PUBLISH_FILES:
        local = WEB_DATA_DIR / local_name
        if not local.exists():
            print(f"  (omito {local_name}: no existe)")
            continue
        url = blob_sync.upload(str(local), pathname, _PUBLISH_MAX_AGE_S, None)
        print(f"  -> {pathname}: {url}")


def _report_status(step: str, ok: bool, exit_code: int) -> None:
    """Best-effort `_status.json` Blob write — the trap report_status()
    equivalent from deploy/refresh.sh, fired on the way out regardless of
    success/failure. Never raises further (matches the bash `|| true`)."""
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            json.dump({"ok": ok, "step": step, "exit_code": exit_code}, fh)
            tmp_path = fh.name
        blob_sync.upload(tmp_path, "data/_status.json", 0, "application/json")
    except (SystemExit, Exception) as exc:  # noqa: BLE001 - best-effort, never propagate
        print(f"  (status Blob write falló, sigo: {exc})")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _require_blob_token() -> None:
    if not os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip():
        raise RuntimeError(
            "falta BLOB_READ_WRITE_TOKEN (token rw del store Blob sismo-dashboard-data)"
        )


# ── Real pipeline (network + filesystem + subprocess) ──────────────────────


def run_refresh() -> dict:
    """The real pipeline. Mirrors deploy/refresh.sh's step order and
    STEP-tracked status trap, WITHOUT the git-clone-at-start
    entrypoint.sh/DASHBOARD_REPO_TOKEN machinery (task 7.2)."""
    step = "init"
    ok = False
    row_count = 0
    try:
        _require_blob_token()

        step = "seed_geocode"
        print(f"[{step}] Sembrando cache de geocode desde Blob…")
        try:
            seed_geocode()
        except (SystemExit, Exception) as exc:  # noqa: BLE001 - best-effort, fail-soft
            print(f"  seed_geocode falló, sigo sin cache previa: {exc}")

        step = "refresh_data"
        print(f"[{step}] Corriendo refresh_data.py (fuente: Survey123)…")
        subprocess.run(
            [sys.executable, "refresh_data.py"],
            cwd=str(SCRIPTS_DIR),
            timeout=REFRESH_DATA_TIMEOUT_S,
            check=True,
        )

        step = "fetch_reportes"
        print(f"[{step}] Trayendo reportes de la API (informe/json, vía services.atencionsismo)…")
        try:
            asyncio.run(asyncio.wait_for(fetch_reportes(), timeout=FETCH_REPORTES_TIMEOUT_S))
        except Exception as exc:  # noqa: BLE001 - fail-soft, main refresh continues
            print(f"  fetch_reportes falló o superó el timeout; sigo sin actualizar reportes: {exc}")

        step = "meta_guard"
        row_count = _meta_guard()
        print(f"[{step}] meta OK: {row_count} filas")

        step = "publish_blob"
        print(f"[{step}] Publicando datos a Vercel Blob…")
        _publish_all()

        step = "done"
        ok = True
        print(f"[{step}] Publicado en Blob: el dashboard lo verá en segundos (sin deploy).")
        return {"step": step, "row_count": row_count}
    finally:
        _report_status(step, ok, 0 if ok else 1)


# ── Offline self-check (--check, no network, cruce_sticker.py's idiom) ─────


def _selfcheck() -> None:
    out = _raw_record_mapper(
        {"id": "1", "estadoVerificacion": "Reportado", "nombre": "x", "latitud": "3.1", "longitud": "-76.1"}
    )
    assert "nombre" not in out and out["lat"] == 3.1 and out["lng"] == -76.1

    deduped = _dedupe_sorted([{"id": "b"}, {"id": "a"}, {"id": "b"}])
    assert deduped == [{"id": "a"}, {"id": "b"}], deduped

    print("dashboard_refresh self-check OK")


# ── Entrypoint (integracion_F1/job_sticker.py's runlog-wrapped pattern) ────


def main() -> int:
    if "--check" in sys.argv:
        _selfcheck()
        return 0

    started_at = datetime.now(timezone.utc)
    log_dir = runlog.resolve_log_dir()
    restore = runlog.start_tee(log_dir)

    print("=" * 60)
    print(f"Corrida dashboard-refresh · inicio {started_at:%Y-%m-%d %H:%M:%S} UTC")
    print(f"Logs: {log_dir or 'solo stdout (sin volumen escribible)'}")
    try:
        summary = run_refresh()
        duracion = round((datetime.now(timezone.utc) - started_at).total_seconds(), 1)
        runlog.append_run(
            log_dir, {"estado": "ok", "duracion_seg": duracion, "archivo": RUNS_FILE, **summary}
        )
        print("Corrida OK")
        return 0
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - job entrypoint boundary
        traceback.print_exc()
        runlog.append_run(
            log_dir,
            {
                "estado": "error",
                "archivo": RUNS_FILE,
                "duracion_seg": round((datetime.now(timezone.utc) - started_at).total_seconds(), 1),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        print("Corrida FALLIDA")
        return 1
    finally:
        restore()


if __name__ == "__main__":
    sys.exit(main())
