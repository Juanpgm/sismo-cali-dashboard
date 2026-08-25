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
# Required env (set in Railway, secret):
#   DASHBOARD_REPO_TOKEN  GitHub token to clone the repo (used in entrypoint.sh)
#   BLOB_READ_WRITE_TOKEN rw token for the sismo-dashboard-data Blob store
# Optional:
#   GOOGLE_MAPS_API_KEY   habilita el arbitraje por geocodificación (fail-soft si falta)
#   VISITADOS_API_PASS    habilita el pull de reportes de la API (fail-soft si falta)
set -euo pipefail

_scrub() { sed "s/${DASHBOARD_REPO_TOKEN}/***/g"; }   # never echo the token

# Publicación por Vercel Blob (NO git commit → NO deploy por refresh). El token
# rw del store vive en Railway (secreto). Fail-closed: sin él no publicamos.
: "${BLOB_READ_WRITE_TOKEN:?falta BLOB_READ_WRITE_TOKEN (token rw del store Blob sismo-dashboard-data)}"
export BLOB_READ_WRITE_TOKEN

# Status heartbeat to Blob: `railway logs` is not reliably readable after a
# short-lived cron container exits, so the run's own outcome is reported here
# (data/_status.json, publicly curl-able) instead of relying on log capture.
# Best-effort — never abort the real pipeline over a monitoring write.
STATUS_STEP="init"
update_status() { STATUS_STEP="$1"; }
report_status() {
  local code=$? tmp
  tmp="$(mktemp)"
  if [ "$code" -eq 0 ]; then
    printf '{"ok":true,"step":"%s","exit_code":0}' "$STATUS_STEP" > "$tmp"
  else
    printf '{"ok":false,"step":"%s","exit_code":%s,"failed_command":%s}' \
      "$STATUS_STEP" "$code" "$(printf '%s' "$BASH_COMMAND" | python -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" > "$tmp"
  fi
  python /repo/deploy/blob_sync.py upload "$tmp" data/_status.json --max-age 0 --content-type application/json >/dev/null 2>&1 || true
  # Mirror via a git side-branch push too (uses DASHBOARD_REPO_TOKEN, not
  # BLOB_READ_WRITE_TOKEN) — if the Blob upload above is what's silently
  # failing, this independent channel still tells us the run happened and how
  # far it got. main is the only branch Vercel builds, so this never deploys.
  ( cd /repo && cp "$tmp" _status_debug.json \
    && git checkout -q -B _status_debug \
    && git add _status_debug.json \
    && git -c user.email=bot@x -c user.name=bot commit -q -m status \
    && git push -q -f origin _status_debug:_status_debug ) 2>&1 | _scrub || true
  rm -f "$tmp"
}
trap report_status EXIT

# Sembrar la cache de geocode desde Blob antes del refresh: antes venía por git,
# ahora vive en Blob. Best-effort — en la primera corrida / miss arranca vacía.
update_status "seed_geocode"
mkdir -p /repo/web/data/geocode
echo "Sembrando cache de geocode desde Blob…"
python /repo/deploy/blob_sync.py download data/geocode/geocode_cache.json /repo/web/data/geocode/geocode_cache.json || true

cd /repo/scripts
# Fuente única: layer público de Survey123 (sin Google Sheets).
update_status "refresh_data"
echo "Corriendo refresh_data.py (fuente: Survey123)…"
python refresh_data.py

# Trae TODOS los reportes de la API atencionsismo (informe/json) → reportes.json
# + agregaciones, para el mapa/analítica del dashboard. Non-fatal: si faltan las
# credenciales o la API falla, publicamos el resto igual. Necesita en Railway:
#   VISITADOS_API_PASS  (y opcional VISITADOS_API_USER, default juanp.gzmz@gmail.com)
update_status "fetch_reportes"
echo "Trayendo reportes de la API (informe/json)…"
python fetch_reportes_api.py 2>&1 | _scrub || echo "fetch_reportes_api falló; sigo sin actualizar reportes."

cd /repo
update_status "meta_guard"
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
  python /repo/deploy/blob_sync.py upload "$1" "$2" --max-age 60 >/dev/null
}

update_status "publish_blob"
echo "Publicando datos a Vercel Blob…"
up web/data/meta.json          data/meta.json
up web/data/inspections.json   data/inspections.json
up web/data/inspections.xlsx   data/inspections.xlsx
up web/data/reportes.json      data/reportes.json
up web/data/reportes_meta.json data/reportes_meta.json
up web/data/reportes_agg.json  data/reportes_agg.json
# Geocode cache = pipeline-internal state; persist in Blob (used to ride in git)
# so future runs pay 0 geocoding API calls for known addresses.
up web/data/geocode/geocode_cache.json data/geocode/geocode_cache.json
update_status "done"
echo "Publicado en Blob: el dashboard lo verá en segundos (sin deploy)."
