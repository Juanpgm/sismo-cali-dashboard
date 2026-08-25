#!/usr/bin/env bash
# The real refresh+publish logic, RUN FROM THE CLONED REPO (/repo/deploy/refresh.sh).
# entrypoint.sh (baked into the Railway image) clones the repo and execs THIS file,
# so it is picked up fresh on every cron run — edit it freely and the next run uses
# it, NO image rebuild needed. Rebuild (`railway up`) only if entrypoint.sh changes.
#
# Regenerates the dashboard's JSON directly from the public Survey123 layer (no
# Google Sheets) and publishes to Vercel Blob — NO git commit, so the ~15-min
# refresh no longer spends a Vercel deploy (100/day free-tier cap). Data lands
# live in seconds; deploys are reserved for real code changes.
#
# Every network-bound step is wrapped in `timeout` so a stuck upstream call
# fails LOUD within minutes instead of hanging the container forever (a hung
# run previously looked identical to "still running" with zero observability —
# see data/_status.json below for how we know which one happened).
#
# Required env (set in Railway, secret):
#   DASHBOARD_REPO_TOKEN  GitHub token to clone the repo (used in entrypoint.sh)
#   BLOB_READ_WRITE_TOKEN rw token for the sismo-dashboard-data Blob store
# Optional:
#   GOOGLE_MAPS_API_KEY   habilita el arbitraje por geocodificación (fail-soft si falta)
#   VISITADOS_API_PASS    habilita el pull de reportes de la API (fail-soft si falta)
set -euo pipefail

_scrub() { sed "s/${DASHBOARD_REPO_TOKEN}/***/g"; }   # never echo the token

: "${BLOB_READ_WRITE_TOKEN:?falta BLOB_READ_WRITE_TOKEN (token rw del store Blob sismo-dashboard-data)}"
export BLOB_READ_WRITE_TOKEN
echo "BLOB_READ_WRITE_TOKEN presente (${#BLOB_READ_WRITE_TOKEN} chars)."

# One best-effort status write on exit — success or failure, whatever step we
# got to. Single Blob call, no side channels: if this fails, the run itself
# already logged its own progress via the `echo` lines above each step.
STEP="init"
report_status() {
  local code=$?
  local tmp; tmp="$(mktemp)"
  printf '{"ok":%s,"step":"%s","exit_code":%s}' \
    "$([ "$code" -eq 0 ] && echo true || echo false)" "$STEP" "$code" > "$tmp"
  timeout 20 python /repo/deploy/blob_sync.py upload "$tmp" data/_status.json --max-age 0 --content-type application/json >/dev/null 2>&1 || true
  rm -f "$tmp"
}
trap report_status EXIT

# Sembrar la cache de geocode desde Blob antes del refresh: antes venía por git,
# ahora vive en Blob. Best-effort — en la primera corrida / miss arranca vacía.
STEP="seed_geocode"
mkdir -p /repo/web/data/geocode
echo "[$STEP] Sembrando cache de geocode desde Blob…"
timeout 30 python /repo/deploy/blob_sync.py download data/geocode/geocode_cache.json /repo/web/data/geocode/geocode_cache.json || true

cd /repo/scripts
STEP="refresh_data"
echo "[$STEP] Corriendo refresh_data.py (fuente: Survey123)…"
timeout 300 python refresh_data.py

STEP="fetch_reportes"
echo "[$STEP] Trayendo reportes de la API (informe/json)…"
timeout 240 python fetch_reportes_api.py 2>&1 | _scrub || echo "fetch_reportes_api falló o superó el timeout; sigo sin actualizar reportes."

cd /repo
STEP="meta_guard"
# Guard: never publish empty/broken data.
python - <<'PY'
import json, sys, pathlib
meta = pathlib.Path("web/data/meta.json")
if not meta.exists():
    sys.exit("meta.json no se generó; aborto sin publicar")
d = json.loads(meta.read_text(encoding="utf-8"))
if int(d.get("row_count", 0)) <= 0:
    sys.exit(f"row_count={d.get('row_count')} inválido; aborto sin publicar")
print(f"meta OK: {d['row_count']} filas, source={d.get('source')}")
PY

# Publish to Vercel Blob EVERY run — no git commit, so NO Vercel deploy is
# spent on data. meta.json.generated_at advances each run, so the dashboard's
# "última actualización" moves even when the data is unchanged; the update lands
# live in seconds (Blob is CDN-served), not minutes (a deploy). data.js reads
# these; the repo's web/data/*.json stay frozen as an offline fallback.
up() {  # up <localFile> <blobPathname> — fail-soft on missing files
  if [ ! -f "$1" ]; then echo "  (omito $1: no existe)"; return 0; fi
  echo "  → $2"
  timeout 60 python /repo/deploy/blob_sync.py upload "$1" "$2" --max-age 60 >/dev/null
}

STEP="publish_blob"
echo "[$STEP] Publicando datos a Vercel Blob…"
up web/data/meta.json          data/meta.json
up web/data/inspections.json   data/inspections.json
up web/data/inspections.xlsx   data/inspections.xlsx
up web/data/reportes.json      data/reportes.json
up web/data/reportes_meta.json data/reportes_meta.json
up web/data/reportes_agg.json  data/reportes_agg.json
# Geocode cache = pipeline-internal state; persist in Blob (used to ride in git)
# so future runs pay 0 geocoding API calls for known addresses.
up web/data/geocode/geocode_cache.json data/geocode/geocode_cache.json
STEP="done"
echo "[$STEP] Publicado en Blob: el dashboard lo verá en segundos (sin deploy)."
