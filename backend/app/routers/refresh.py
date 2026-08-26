"""POST /refresh — admin-triggered redeploy of the `dashboard-refresh`
Railway cron service (design.md ADR-6; backend-platform spec
"Admin-gated route rejects non-admin" (`/refresh` row); "Route Parity
Across Consolidated Endpoints" (`/refresh` row)).

Ports `api/refresh.js:134-181`'s dual-header Railway GraphQL auth fallback
(`railway()`, `api/refresh.js:107-132`) to trigger a `serviceInstanceRedeploy`
mutation — this is the server hop the dashboard's "Actualizar datos" button
calls to kick off `scripts/refresh_data.py` on Railway. Absorbing
`dashboard-refresh`'s job code into `backend/app/jobs/` is a SEPARATE
concern (slice 7, not started) — this router only triggers the redeploy.

The "Actualizar datos" button force-runs the WHOLE 15-min cron fleet, not
just `dashboard-refresh`: this route redeploys all three 15-minute crons —
`dashboard-refresh` (primary), `cruce-sticker`, and `cruce-gestion`. The
primary redeploy IS the data refresh; a primary failure is fatal (502). The
two cross jobs are best-effort adjuncts, redeployed after the primary with a
per-service fail-soft branch (a `cruce-sticker`/`cruce-gestion` hiccup is
surfaced in `errors` but never blocks the response). This revives the legacy
handler's fail-soft `cruce-gestion` second redeploy (`api/refresh.js:166-174`,
previously cut per proposal.md Scope Exclusion Addendum Extension 2 item 5)
and adds `cruce-sticker` alongside it. The response keeps `{ok, deploymentId}`
(deploymentId = the primary's, for frontend backward-compat) and adds a
`deployments` (name → deployment id | null) and `errors` (name → message)
map.

Railway service/environment id (tasks.md 6.2: "confirm exact id before
hardcoding"): `dashboard-refresh`'s job code has NOT been absorbed into
`backend/app/jobs/` yet (slice 7, not started), so there is no NEW
consolidated Railway service for it yet. This router therefore targets the
SAME service/environment `api/refresh.js` itself currently references (the
already-provisioned `dashboard-refresh` service in the `normalizador-sismo-
cali` Railway project — `deploy/refresh.sh` already runs there today) rather
than fabricate a different id. Values are read from env vars at REQUEST
time (not import time, so a redeploy/test can change them without an app
restart), mirroring `api/refresh.js`'s own env var names verbatim
(`RAILWAY_API_TOKEN`, `RAILWAY_SERVICE_ID`, `RAILWAY_ENVIRONMENT_ID`), each
defaulting to the exact literal `api/refresh.js:96,100` already hardcodes —
this is an env-var-with-fallback approach, not a bare hardcoded id, so slice
7 can repoint it to a new consolidated service by simply setting
`RAILWAY_SERVICE_ID` on the Railway "web" service, no code change required.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import require_role

# Never touches Firestore/S3 — RAILWAY_API_TOKEN is a "plain secret" read
# directly by this router (design.md ADR-4 table), not part of the
# named-client union; it fails lazily at request time (matching
# api/refresh.js:157-162), not at web startup — same pattern
# `routers/source_status.py` uses for VISITADOS_API_PASS.
REQUIRED_CLIENTS: tuple[str, ...] = ()

RAILWAY_API = "https://backboard.railway.com/graphql/v2"

# The three 15-min cron services in the `normalizador-sismo-cali` Railway
# project (confirmed live 2026-08-26). `dashboard-refresh` verbatim from
# api/refresh.js:96; the other two resolved from the Railway project.
DEFAULT_SERVICE_ID = "156e97a2-596b-4861-95f4-4060dab408e2"          # dashboard-refresh
DEFAULT_STICKER_SERVICE_ID = "b18c74c8-0b7a-459c-ada5-5e5df6db8050"  # cruce-sticker
DEFAULT_CRUCE_SERVICE_ID = "b4c8fd15-aa3b-4157-b787-2034c89a108b"    # cruce-gestion
DEFAULT_ENVIRONMENT_ID = "4418f451-bd97-4d96-ba6e-b5ecbbd49c9b"

# serviceInstanceRedeploy redeploys the service's latest deployment (i.e.
# runs the cron container now) and returns the new deployment id. Verbatim
# from api/refresh.js:104-105.
REDEPLOY_MUTATION = """
mutation($s: String!, $e: String!) {
  serviceInstanceRedeploy(serviceId: $s, environmentId: $e)
}
"""

router = APIRouter()


async def _railway_graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Dual-header auth fallback, ported verbatim from
    `api/refresh.js:107-132`'s `railway()` helper: Railway authenticates
    account/team tokens via `Authorization: Bearer` and project tokens via
    the `Project-Access-Token` header. Try Bearer first, fall back to the
    project header so either token type works transparently — same
    convention `integracion_F1/scripts/railway_setup.py`'s `gql()` uses
    (Project-Access-Token + a descriptive User-Agent, since Cloudflare
    answers 403 to requests without one)."""
    auth_headers = (
        {"Authorization": f"Bearer {token}"},
        {"Project-Access-Token": token},
    )
    last_error: str | None = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        for auth in auth_headers:
            resp = await client.post(
                RAILWAY_API,
                json={"query": query, "variables": variables},
                headers={
                    "Content-Type": "application/json",
                    # Cloudflare answers 403 to requests without a User-Agent.
                    "User-Agent": "sismo-cali-dashboard/1.0",
                    **auth,
                },
            )
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.status_code < 300 and not body.get("errors"):
                return body.get("data") or {}
            last_error = f"Railway API {resp.status_code}: {body.get('errors') or body}"
    raise RuntimeError(last_error or "Railway API request failed")


def _cron_services() -> list[tuple[str, str]]:
    """(name, service_id) for the three 15-min crons the "Actualizar datos"
    button force-runs. Order matters: `dashboard-refresh` is the PRIMARY (its
    redeploy is the data refresh itself); the two cross jobs are best-effort
    adjuncts. Each id is env-overridable, same fallback pattern as the legacy
    RAILWAY_SERVICE_ID."""
    return [
        (
            "dashboard-refresh",
            os.environ.get("RAILWAY_SERVICE_ID", "").strip() or DEFAULT_SERVICE_ID,
        ),
        (
            "cruce-sticker",
            os.environ.get("RAILWAY_STICKER_SERVICE_ID", "").strip()
            or DEFAULT_STICKER_SERVICE_ID,
        ),
        (
            "cruce-gestion",
            os.environ.get("RAILWAY_CRUCE_SERVICE_ID", "").strip()
            or DEFAULT_CRUCE_SERVICE_ID,
        ),
    ]


async def _redeploy_one(token: str, service_id: str, environment_id: str) -> str:
    data = await _railway_graphql(
        token, REDEPLOY_MUTATION, {"s": service_id, "e": environment_id}
    )
    return data["serviceInstanceRedeploy"]


async def _redeploy_all() -> dict[str, Any]:
    """Force-run all three 15-min crons. The primary (`dashboard-refresh`)
    failure propagates (→ 502); secondary failures are caught and reported in
    ``errors`` (fail-soft). Env vars read at REQUEST time, mirroring
    api/refresh.js's RAILWAY_API_TOKEN/RAILWAY_SERVICE_ID/RAILWAY_ENVIRONMENT_ID
    names."""
    token = os.environ.get("RAILWAY_API_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=500, detail="RAILWAY_API_TOKEN no está configurado."
        )
    environment_id = (
        os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip() or DEFAULT_ENVIRONMENT_ID
    )
    services = _cron_services()
    deployments: dict[str, str | None] = {}
    errors: dict[str, str] = {}

    # Primary: its failure is fatal (the data refresh itself) — let it
    # propagate to trigger_refresh's 502, matching the legacy catch branch.
    primary_name, primary_id = services[0]
    deployments[primary_name] = await _redeploy_one(token, primary_id, environment_id)

    # Secondary crosses: best-effort. A stickers/gestion hiccup is surfaced in
    # `errors` but must not tumble the primary data refresh's response.
    for name, service_id in services[1:]:
        try:
            deployments[name] = await _redeploy_one(token, service_id, environment_id)
        except Exception as exc:  # noqa: BLE001 — fail-soft adjunct
            deployments[name] = None
            errors[name] = str(exc)

    return {
        "deploymentId": deployments[primary_name],
        "deployments": deployments,
        "errors": errors,
    }


@router.post("/refresh", status_code=202)
async def trigger_refresh(
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        result = await _redeploy_all()
    except HTTPException:
        raise
    except RuntimeError as exc:
        # Verbatim status from api/refresh.js:178-179's catch branch.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, **result}
