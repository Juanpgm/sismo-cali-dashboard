"""MANUAL operator tool for tasks.md 3.6 (ADR-5's parity-diff plan).

NOT part of the automated test suite — never imported by `app/`, never run
in CI. Hits two LIVE endpoints (old Vercel `api/reportados.js`, new Railway
`/reportados`) within the same 15-min window and compares the fields
`web/js/data.js` actually consumes, per tasks.md task 3.6:

  "VERIFY (ADR-5 parity-diff plan): within the same 15-min window, fetch
  live sismo-cali-dashboard.vercel.app/api/reportados and the Railway
  route; compare JSON shape and consumed fields (reportados total,
  inmuebles) with tolerance for in-flight drift; confirm <2s response;
  record both payloads in the PR description."

BLOCKED until tasks.md 1.4 (manual Railway "web" service creation) exists —
`NEW_REPORTADOS_URL` has no real value to point at yet. This script is
written and ready so 3.6 can be run the moment 1.4 lands (and the
background snapshot refresh has completed at least once), with zero
further code changes needed. No Authorization header is sent to either
endpoint — `/reportados` is public (backend-platform spec "Public route
requires no token").

Usage (once 1.4 is live and the new route has served at least once):

    NEW_REPORTADOS_URL=https://<railway-app>.up.railway.app/reportados \
    python backend/scripts/verify_reportados_parity.py

Env vars:
    NEW_REPORTADOS_URL  required. Consolidated app's /reportados endpoint.
    OLD_REPORTADOS_URL  optional. Default: the live dashboard's Vercel function.
    DRIFT_TOLERANCE     optional. Default: 50 — max acceptable absolute
                         difference in `total`/`inmuebles` between old and
                         new, allowing for a live report landing mid-window
                         (both endpoints re-read the same upstream API, at
                         slightly different moments).
"""
from __future__ import annotations

import json
import os
import sys
import time

DEFAULT_OLD_REPORTADOS_URL = "https://sismo-cali-dashboard.vercel.app/api/reportados"
DEFAULT_DRIFT_TOLERANCE = 50
MAX_RESPONSE_SECONDS = 2.0  # backend-platform spec: "reportados responds fast from snapshot"


def _payload(resp, elapsed: float) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    return {"status": resp.status_code, "elapsed_s": round(elapsed, 3), "body": body}


def main() -> int:
    import httpx

    new_url = os.environ.get("NEW_REPORTADOS_URL", "").strip()
    if not new_url:
        print(
            "BLOCKED: set NEW_REPORTADOS_URL (requires tasks.md 1.4 — the "
            "Railway web service — to exist, AND at least one completed "
            "background snapshot refresh) before running this script. See "
            "the module docstring.",
            file=sys.stderr,
        )
        return 2

    old_url = os.environ.get("OLD_REPORTADOS_URL", DEFAULT_OLD_REPORTADOS_URL).strip()
    tolerance = int(os.environ.get("DRIFT_TOLERANCE", str(DEFAULT_DRIFT_TOLERANCE)))

    results: dict[str, dict] = {}
    with httpx.Client(timeout=180.0) as client:  # old endpoint can cold-fetch ~150s
        t0 = time.monotonic()
        old_resp = client.get(old_url)
        results["old"] = _payload(old_resp, time.monotonic() - t0)

        t0 = time.monotonic()
        new_resp = client.get(new_url)
        results["new"] = _payload(new_resp, time.monotonic() - t0)

    print(json.dumps(results, indent=2, ensure_ascii=False))

    old_body = results["old"]["body"] if isinstance(results["old"]["body"], dict) else {}
    new_body = results["new"]["body"] if isinstance(results["new"]["body"], dict) else {}

    fast_enough = results["new"]["elapsed_s"] < MAX_RESPONSE_SECONDS
    both_ok = results["old"]["status"] == 200 and results["new"]["status"] == 200

    old_total = (old_body.get("por_estadoVerificacion") or {}).get("Reportado")
    new_total = (new_body.get("por_estadoVerificacion") or {}).get("Reportado")
    old_inmuebles = old_body.get("inmuebles")
    new_inmuebles = new_body.get("inmuebles")

    def _within_tolerance(a, b) -> bool:
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return False
        return abs(a - b) <= tolerance

    fields_match = _within_tolerance(old_total, new_total) and _within_tolerance(
        old_inmuebles, new_inmuebles
    )

    print(
        "\nold por_estadoVerificacion.Reportado =", old_total,
        "| new =", new_total,
    )
    print("old inmuebles =", old_inmuebles, "| new inmuebles =", new_inmuebles)
    print(f"new response time: {results['new']['elapsed_s']}s (budget: <{MAX_RESPONSE_SECONDS}s)")

    ok = both_ok and fast_enough and fields_match
    print("\nPARITY:", "OK" if ok else "MISMATCH — inspect payloads above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
