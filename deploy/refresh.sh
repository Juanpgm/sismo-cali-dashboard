#!/usr/bin/env bash
# The real refresh+publish logic, RUN FROM THE CLONED REPO (/repo/deploy/refresh.sh).
# entrypoint.sh (baked into the Railway image) clones the repo and execs THIS file,
# so it is picked up fresh on every cron run — edit it freely and the next run uses
# it, NO image rebuild needed. Rebuild (`railway up`) only if entrypoint.sh changes.
#
# Regenerates the dashboard's static JSON directly from the public Survey123 layer
# (no Google Sheets) and publishes by pushing to the connected repo (Vercel
# auto-redeploys).
#
# Required env (set in Railway, secret):
#   DASHBOARD_REPO_TOKEN  GitHub token with contents:write on the dashboard repo
# Optional:
#   GOOGLE_MAPS_API_KEY   habilita el arbitraje por geocodificación (fail-soft si falta)
#   VISITADOS_API_PASS    habilita el pull de reportes de la API (fail-soft si falta)
#   DASHBOARD_BRANCH (default main)
set -euo pipefail

BRANCH="${DASHBOARD_BRANCH:-main}"
_scrub() { sed "s/${DASHBOARD_REPO_TOKEN}/***/g"; }   # never echo the token

cd /repo/scripts
# Fuente única: layer público de Survey123 (sin Google Sheets).
echo "Corriendo refresh_data.py (fuente: Survey123)…"
python refresh_data.py

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
