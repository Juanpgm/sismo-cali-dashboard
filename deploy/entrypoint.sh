#!/usr/bin/env bash
# Thin, STABLE bootstrap baked into the Railway image. It ONLY clones the repo and
# hands off to the repo-tracked deploy/refresh.sh, so ALL refresh logic lives in
# the repo and updates on every push — NO image rebuild needed for logic changes.
#
# Rebuild the image (`railway up --service dashboard-refresh` from this dir) ONLY
# when THIS file changes. Everything else lives in deploy/refresh.sh.
#
# Required env (set in Railway, secret):
#   DASHBOARD_REPO_TOKEN  GitHub token with contents:write on the dashboard repo
# Optional:
#   DASHBOARD_REPO   (default Juanpgm/sismo-cali-dashboard)
#   DASHBOARD_BRANCH (default main)
set -euo pipefail

: "${DASHBOARD_REPO_TOKEN:?falta el env DASHBOARD_REPO_TOKEN (token GitHub con contents:write)}"
REPO="${DASHBOARD_REPO:-Juanpgm/sismo-cali-dashboard}"
BRANCH="${DASHBOARD_BRANCH:-main}"

echo "Clonando ${REPO}@${BRANCH}…"
# Railway re-ejecuta el MISMO contenedor en cada tick del cron y el filesystem
# PERSISTE entre corridas, así que /repo del run anterior queda ahí y `git clone`
# falla con "destination path already exists". Limpiar siempre = clone idempotente
# (no-op si el contenedor es fresco, salva si persiste).
rm -rf /repo
git clone --depth 1 --branch "$BRANCH" \
  "https://x-access-token:${DASHBOARD_REPO_TOKEN}@github.com/${REPO}.git" /repo \
  2>&1 | sed "s/${DASHBOARD_REPO_TOKEN}/***/g"

# Hand off to the repo-tracked logic (invoked via bash → no exec-bit needed).
exec bash /repo/deploy/refresh.sh
