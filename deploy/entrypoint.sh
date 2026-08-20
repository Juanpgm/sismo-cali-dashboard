#!/usr/bin/env bash
# Cron entrypoint: regenerate the dashboard's static JSON from the F3 Google
# Sheet and publish by pushing to the connected repo (Vercel auto-redeploys).
#
# Required env (set in Railway, both secret):
#   GOOGLE_SERVICE_ACCOUNT_JSON  full service-account key JSON (reader+editor on the Sheet)
#   DASHBOARD_REPO_TOKEN         GitHub token with contents:write on the dashboard repo
# Optional:
#   DASHBOARD_REPO   (default Juanpgm/sismo-cali-dashboard)
#   DASHBOARD_BRANCH (default main)
set -euo pipefail

: "${GOOGLE_SERVICE_ACCOUNT_JSON:?falta el env GOOGLE_SERVICE_ACCOUNT_JSON (JSON de la service account)}"
: "${DASHBOARD_REPO_TOKEN:?falta el env DASHBOARD_REPO_TOKEN (token GitHub con contents:write)}"
REPO="${DASHBOARD_REPO:-Juanpgm/sismo-cali-dashboard}"
BRANCH="${DASHBOARD_BRANCH:-main}"

# refresh_data.py reads credentials from a file path, not inline JSON.
printf '%s' "$GOOGLE_SERVICE_ACCOUNT_JSON" > /tmp/sa.json
export GOOGLE_APPLICATION_CREDENTIALS=/tmp/sa.json

_scrub() { sed "s/${DASHBOARD_REPO_TOKEN}/***/g"; }   # never echo the token

echo "Clonando ${REPO}@${BRANCH}…"
git clone --depth 1 --branch "$BRANCH" \
  "https://x-access-token:${DASHBOARD_REPO_TOKEN}@github.com/${REPO}.git" /repo 2>&1 | _scrub

cd /repo/scripts
# Sync new/edited form responses (raw_data) into the curated tabla_normalizada
# BEFORE reading it. Non-fatal: if the upsert fails, still publish whatever the
# table currently holds rather than blocking the dashboard refresh.
echo "Sincronizando raw_data → tabla_normalizada (upsert)…"
python normalize_sync.py 2>&1 | _scrub || echo "normalize_sync falló; sigo con la tabla actual."

echo "Corriendo refresh_data.py --source drive…"
python refresh_data.py --source drive

# Trae TODOS los reportes de la API atencionsismo (informe/json) → reportes.json
# + agregaciones, para el mapa/analítica del dashboard. Non-fatal: si faltan las
# credenciales o la API falla, publicamos el resto igual. Necesita en Railway:
#   VISITADOS_API_PASS  (y opcional VISITADOS_API_USER, default juanp.gzmz@gmail.com)
echo "Trayendo reportes de la API (informe/json)…"
python fetch_reportes_api.py 2>&1 | _scrub || echo "fetch_reportes_api falló; sigo sin actualizar reportes."

cd /repo
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

# Publish EVERY run so the dashboard's "última actualización" refleja la fecha de
# corrida del script aunque los datos no cambien. meta.json.generated_at avanza
# siempre, así que siempre hay algo que commitear (asumimos deploy cada corrida).
git config user.email "sismo-refresh-bot@users.noreply.github.com"
git config user.name "sismo-refresh-bot"
# inspections.xlsx is fail-soft in refresh_data.py, so it may be absent — add it
# only if it exists, or `git add` on a missing path aborts the publish (set -e).
git add web/data/inspections.json web/data/meta.json
[ -f web/data/inspections.xlsx ] && git add web/data/inspections.xlsx
# API reportes (fail-soft above): add only if the fetch actually wrote them.
for f in reportes.json reportes_meta.json reportes_agg.json; do
  [ -f "web/data/$f" ] && git add "web/data/$f"
done
# Commit the geocode cache so future runs pay 0 API calls for known addresses.
[ -f web/data/geocode/geocode_cache.json ] && git add web/data/geocode/geocode_cache.json
if git diff --cached --quiet; then
  echo "nada que publicar (ni meta.json cambió); salgo limpio."
  exit 0
fi
git commit -m "chore: refresh dashboard data (auto)"

# El botón "Actualizar datos" redespliega dashboard-refresh Y cruce-gestion a la
# vez (api/refresh.js), y ambos pushean a ${BRANCH}. El que llega segundo veía su
# push rechazado (non-fast-forward) y, con `set -e`, el job de Railway moría.
# Cada servicio toca archivos distintos en web/data/, así que un rebase sobre el
# remoto es limpio: reintegramos y reintentamos en vez de fallar.
push_ok=0
for intento in 1 2 3 4 5; do
  if git push origin "HEAD:${BRANCH}" 2>&1 | _scrub; then
    push_ok=1; break
  fi
  echo "push rechazado (intento ${intento}); reintegro remoto y reintento…"
  git fetch --depth=50 origin "$BRANCH" 2>&1 | _scrub || true
  if ! git rebase "origin/${BRANCH}" 2>&1 | _scrub; then
    git rebase --abort 2>/dev/null || true
    echo "rebase con conflicto inesperado; reintento limpio."
  fi
  sleep $(( (RANDOM % 5) + 2 ))
done
[ "$push_ok" = 1 ] || { echo "no se pudo publicar tras 5 reintentos"; exit 1; }
echo "Publicado: Vercel redesplegará desde ${BRANCH}."
