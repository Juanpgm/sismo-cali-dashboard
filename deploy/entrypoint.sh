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

# Only publish when the actual data changed. meta.json's generated_at bumps
# every run, so gating on inspections.json avoids hourly no-op deploys.
if git diff --quiet -- web/data/inspections.json; then
  echo "inspections.json sin cambios; no publico (evito churn de deploy)."
  exit 0
fi

git config user.email "sismo-refresh-bot@users.noreply.github.com"
git config user.name "sismo-refresh-bot"
# inspections.xlsx is fail-soft in refresh_data.py, so it may be absent — add it
# only if it exists, or `git add` on a missing path aborts the publish (set -e).
git add web/data/inspections.json web/data/meta.json
[ -f web/data/inspections.xlsx ] && git add web/data/inspections.xlsx
# Commit the geocode cache so future runs pay 0 API calls for known addresses.
[ -f web/data/geocode/geocode_cache.json ] && git add web/data/geocode/geocode_cache.json
git commit -m "chore: refresh dashboard data (auto)"
git push origin "HEAD:${BRANCH}" 2>&1 | _scrub
echo "Publicado: Vercel redesplegará desde ${BRANCH}."
