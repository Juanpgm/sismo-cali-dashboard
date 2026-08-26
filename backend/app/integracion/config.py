"""Trimmed config for the absorbed `cruce-sticker` pipeline (design.md ADR-2).

Ported from `integracion_F1/integracion/config.py`, keeping ONLY the two
constants the copied modules actually import (`coords.py`'s `CALI_BBOX`,
`runlog.py`'s `BOGOTA_TZ`). Every other constant from the source module is
CUT, not just its Sheets branches — the source file also carries the dagma
Firestore project/collection defaults (`FIRESTORE_PROJECT`,
`FIRESTORE_COLLECTION = "cruce_criticos_survey"`), EDAN/VISITAS spreadsheet
ids, and Sheets write scopes, all of which belong to jobs excluded from this
migration (`normalizador`, `cruce-gestion` — proposal.md Scope Exclusion
Addendum Extension 2: "no usar nada relacionado con el dagma", no dagma
credential/project id/collection name anywhere in `backend/`). No `.env`
auto-load either (`envfile.load_env_file`, source config.py's own import) —
Railway env vars are provisioned on the service directly, matching every
other module in this backend.
"""
from __future__ import annotations

from datetime import timedelta, timezone

# Colombia is UTC-5 year-round (no DST) — runlog.py's human-facing timestamps.
BOGOTA_TZ = timezone(timedelta(hours=-5), "America/Bogota")

# Cali bounding box (WGS84) — coords.py's parse_latlon() sanity filter.
CALI_BBOX = {"lat_min": 2.9, "lat_max": 4.1, "lon_min": -77.0, "lon_max": -76.0}
