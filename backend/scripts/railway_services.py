"""Provision and configure the two migrated Railway cron services
(`dashboard-refresh`, `cruce-sticker`) on the consolidated backend image
(design.md ADR-6, task 7.12).

**Replaces `integracion_F1/scripts/railway_setup.py` as the source of
truth for these two jobs only** — that script keeps owning
`normalizador`/`integracion-f3`/`asignaciones`/`cruce-gestion` (excluded
from migration, see tasks.md 7.7/7.10/7.11) until their slice 9
decommission. Port of the exact same drift-only pattern
(LIST/INSTANCE/UPDATE GraphQL, `desired()` diff, dry-run), scoped to
`SERVICES`'s two rows instead of the legacy fleet of six.

Unlike `railway_setup.py`, every service here is GIT-CONNECTED (design.md
ADR-1: same pinned `dockerfilePath = backend/Dockerfile`, root = repo root)
— there is no `railway up --path-as-root .` step for these two jobs; code
ships via git push, this script only owns `cronSchedule`/`startCommand`.

    python backend/scripts/railway_services.py            # apply the two rows
    python backend/scripts/railway_services.py --show     # report only, change nothing
    python backend/scripts/railway_services.py --dry      # print the plan, create/update nothing
    python backend/scripts/railway_services.py --only cruce-sticker   # one service

Auth: `RAILWAY_API_TOKEN` env var (project-scoped token, same convention
`railway_setup.py`'s `_token()` used).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

API = "https://backboard.railway.com/graphql/v2"

# Same Railway project/environment `dashboard-refresh` already lives in
# today (`normalizador-sismo-cali`) — task 6.2 already defaults
# RAILWAY_SERVICE_ID/RAILWAY_ENVIRONMENT_ID to these exact literals for the
# /refresh route's redeploy trigger; this script reuses the same ids rather
# than fabricating a new project, so no re-provisioning is needed.
PROJECT_ID = "f32efdbf-a8d5-4a43-9369-cb7b7623c4f6"
ENVIRONMENT_ID = "4418f451-bd97-4d96-ba6e-b5ecbbd49c9b"

# Schedules per job-scheduling spec's "Per-Job Schedule Parity" table
# (ground truth over any module docstring) — identical to
# integracion_F1/scripts/railway_setup.py's EVERY_15/STICKER_EVERY_15
# constants before this migration.
EVERY_15 = "*/15 13-23,0 * * *"
STICKER_EVERY_15 = "7,22,37,52 13-23,0 * * *"

# Desired fleet: EXACTLY the two migrated jobs (tasks.md 7.12). No rows for
# integracion-f3/asignaciones/cruce-gestion — none of them ever move to this
# script (see 7.7/7.10/7.11); they stay in the legacy railway_setup.py.
SERVICES = [
    # Already provisioned (task 6.2's redeploy trigger already targets this
    # exact service_id) — this script repoints it from the legacy
    # dashboard-repo-clone image to `python -m app.jobs.dashboard_refresh`
    # on the consolidated image (git-connected, backend/Dockerfile).
    {"name": "dashboard-refresh", "start_command": "python -m app.jobs.dashboard_refresh",
     "cron": EVERY_15, "service_id": "156e97a2-596b-4861-95f4-4060dab408e2"},
    # service_id: None — created fresh by the MANUAL operator step (tasks.md
    # 7.13); the legacy cruce-sticker shell in railway_setup.py never
    # deployed successfully (Node-upload bug, 2026-08-25) and stays deleted
    # there, not reused here.
    {"name": "cruce-sticker", "start_command": "python -m app.jobs.cruce_sticker",
     "cron": STICKER_EVERY_15, "service_id": None},
]

# builder: None (unset) lets Railway auto-detect the Dockerfile — same
# no-op guard integracion_F1/scripts/railway_setup.py kept (its own comment
# explains why this write is inert but harmless).
COMMON = {"restartPolicyType": "NEVER", "numReplicas": 1, "builder": None}


def _token() -> str:
    token = os.environ.get("RAILWAY_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("No Railway credentials. Set RAILWAY_API_TOKEN.")
    return token


def gql(query: str, variables: dict | None = None) -> dict:
    request = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Content-Type": "application/json",
            # Token de PROYECTO (creado en el dashboard del proyecto): Railway lo
            # autoriza con el header Project-Access-Token, no con Authorization Bearer.
            "Project-Access-Token": _token(),
            # Cloudflare answers 403 to requests without a User-Agent.
            "User-Agent": "sismo-cali-backend/1.0",
        },
    )
    try:
        payload = json.load(urllib.request.urlopen(request))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Railway API {exc.code}: {exc.read().decode()[:400]}")
    if "errors" in payload:
        raise SystemExit(f"Railway API error: {payload['errors']}")
    return payload["data"]


LIST_SERVICES = """query($p:String!){
  project(id:$p){ services{ edges{ node{ id name } } } } }"""

INSTANCE = """query($s:String!,$e:String!){
  serviceInstance(serviceId:$s, environmentId:$e){
    cronSchedule startCommand restartPolicyType numReplicas builder dockerfilePath } }"""

CREATE = "mutation($in:ServiceCreateInput!){ serviceCreate(input:$in){ id } }"

UPDATE = """mutation($s:String!,$e:String,$in:ServiceInstanceUpdateInput!){
  serviceInstanceUpdate(serviceId:$s, environmentId:$e, input:$in) }"""


def project_services() -> dict[str, str]:
    """name -> service_id for every service currently in the project."""
    data = gql(LIST_SERVICES, {"p": PROJECT_ID})
    return {e["node"]["name"]: e["node"]["id"]
            for e in data["project"]["services"]["edges"]}


def instance(service_id: str) -> dict:
    return gql(INSTANCE, {"s": service_id, "e": ENVIRONMENT_ID})["serviceInstance"]


def create_service(name: str) -> str:
    """Create an empty service shell. Code ships via git push (design.md
    ADR-1) — no `railway up` step, unlike the legacy CLI-upload fleet."""
    return gql(CREATE, {"in": {"projectId": PROJECT_ID, "name": name}})["serviceCreate"]["id"]


def desired(spec: dict) -> dict:
    d = {"cronSchedule": spec["cron"], **COMMON}
    if spec.get("start_command"):
        d["startCommand"] = spec["start_command"]
    return d


def apply_service(spec: dict, by_name: dict[str, str], dry: bool) -> bool:
    name = spec["name"]
    service_id = spec["service_id"] or by_name.get(name)
    newly_created = False

    if not service_id:
        print(f"[{name}] no existe → crear")
        if dry:
            print("  (dry) serviceCreate (shell vacío; conectar al repo git en el "
                  "dashboard de Railway antes del primer deploy)")
            return True
        service_id = create_service(name)
        newly_created = True
        print(f"  creado service_id={service_id} → conectar al repo git en el "
              f"dashboard de Railway, dockerfilePath=backend/Dockerfile")
    else:
        print(f"[{name}] service_id={service_id}")

    want = desired(spec)
    # A just-created service has no instance settings yet, so apply everything.
    before = {} if newly_created else instance(service_id)
    drift = {k: v for k, v in want.items() if before.get(k) != v}
    if not drift:
        print("  ya aplicado; nada que hacer")
        return True

    print(f"  aplicando: {drift}")
    if dry:
        print("  (dry) sin escribir")
        return True
    try:
        gql(UPDATE, {"s": service_id, "e": ENVIRONMENT_ID, "in": drift})
    except SystemExit as exc:
        if newly_created:
            print(f"  ⚠️  '{name}' fue CREADO pero NO configurado ({exc}).\n"
                  f"     Queda sin cronSchedule (no se agenda). Re-corré este "
                  f"script para terminar de configurarlo antes de desplegarlo.")
        else:
            print(f"  ⚠️  no se pudo aplicar a '{name}': {exc}")
        return False

    after = instance(service_id)
    ok = all(after.get(k) == v for k, v in want.items())
    for k, v in want.items():
        got = after.get(k)
        print(f"    {k:20s} {got!r}  [{'ok' if got == v else 'NO APLICÓ'}]")
    if spec["service_id"] is None:
        print(f"  → fijá service_id de '{name}' en SERVICES: {service_id!r}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", action="store_true",
                    help="report current settings of the fleet, change nothing")
    ap.add_argument("--dry", action="store_true",
                    help="print the plan without creating or updating anything")
    ap.add_argument("--only", metavar="NAME", help="target a single service by name")
    args = ap.parse_args()

    by_name = project_services()
    specs = [s for s in SERVICES if not args.only or s["name"] == args.only]
    if not specs:
        raise SystemExit(f"--only {args.only!r} no coincide con ningún servicio")

    if args.show:
        for spec in specs:
            sid = spec["service_id"] or by_name.get(spec["name"])
            if not sid:
                print(f"[{spec['name']}] aún no existe")
                continue
            cur = instance(sid)
            print(f"[{spec['name']}] {sid}")
            for k in ("cronSchedule", "startCommand", "restartPolicyType", "numReplicas"):
                print(f"  {k:20s} {cur.get(k)!r}")
        return 0

    # Evaluate every service — never short-circuit, or one early failure would
    # leave the rest of the fleet unprovisioned.
    results = [apply_service(spec, by_name, args.dry) for spec in specs]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
