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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
