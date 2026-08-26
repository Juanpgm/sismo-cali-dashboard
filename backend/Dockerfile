# Consolidated FastAPI backend — ONE image, N Railway services (web + 2 crons).
# Build context is the REPO ROOT (design.md ADR-1): the dashboard-refresh job
# needs scripts/ and deploy/ alongside the backend/ package, so the Railway
# service points root directory = repo root, dockerfilePath = backend/Dockerfile
# (see repo-root railway.json). No service is ever `railway up`'d.
FROM python:3.12-slim

WORKDIR /app

COPY backend/ backend/
COPY scripts/ scripts/
COPY deploy/ deploy/

WORKDIR /app/backend

RUN pip install --no-cache-dir -r requirements.txt

# Web service default; cron services override with
# `startCommand: python -m app.jobs.<job>` (design.md ADR-6).
# --factory: app.main deliberately exposes only create_app() (no module-level
# singleton, so importing it in tests never triggers the fail-fast credential
# check). Shell form so Railway's injected $PORT is honored (exec form cannot
# expand env vars); 8000 is the local-run fallback.
CMD uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}
