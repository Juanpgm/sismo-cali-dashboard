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
# scripts/refresh_data.py reads the PREPARED basemaps (comunas.geojson,
# barrios.geojson — see its PREPARED_BASEMAPS_DIR) for the spatial join, and
# writes its own outputs (meta.json, inspections.json, ...) back into this
# same directory before publish_blob() ships them to Vercel Blob. Found
# missing live (2026-08-26): the dashboard-refresh job crashed with
# FileNotFoundError on comunas.geojson on its first real Railway run — no
# backend/tests/ case builds the actual Docker image or runs refresh_data.py
# for real, so this gap was invisible to the test suite.
COPY web/data/ web/data/

WORKDIR /app/backend

RUN pip install --no-cache-dir -r requirements.txt

# Web service default; cron services override with
# `startCommand: python -m app.jobs.<job>` (design.md ADR-6).
# --factory: app.main deliberately exposes only create_app() (no module-level
# singleton, so importing it in tests never triggers the fail-fast credential
# check). Shell form so Railway's injected $PORT is honored (exec form cannot
# expand env vars); 8000 is the local-run fallback.
CMD uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}
