"""Consolidated FastAPI backend for the Cali seismic disaster data platform.

One application, one Railway image, serving every legacy `api/*.js` Vercel
function plus `/sign`, and every `integracion_F1` cron job (`normalizador`
excluded — see `openspec/changes/fastapi-backend-consolidation/design.md`
ADR-1/ADR-2).
"""
