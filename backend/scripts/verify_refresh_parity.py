"""MANUAL operator tool for tasks.md 6.3 (ADR-7 parity procedure,
mutating-action carve-out).

NOT part of the automated test suite -- never imported by `app/`, never run
in CI. Hits two LIVE endpoints (old Vercel `api/refresh.js` vs new Railway
`POST /refresh`) side by side and prints both payloads for the PR
description, per tasks.md task 6.3:

  "VERIFY (ADR-7 procedure, mutating-action carve-out -- redeploy trigger
  is idempotent enough to exercise live): admin-token POST old vs new; both
  202, deploymentId present (old response's cruceDeploymentId field has no
  new-side equivalent -- expected, documented difference, not a parity
  failure)."

Follows the same two-tier convention `verify_sign_parity.py` (task 2.3),
`verify_status_routes_parity.py` (task 4.5) and
`verify_inspector_asignaciones_parity.py` (task 5.4) established, WITH ONE
EXTRA SAFETY GUARD this endpoint needs and those did not:

  STRUCTURAL tier (only NEW_REFRESH_URL required) -- no-auth and
  syntactically-present-but-invalid-token checks against both endpoints.
  These are SAFE and non-mutating: both the legacy handler and the new
  router reject before ever reaching the Railway redeploy call, so this
  tier never triggers a real deployment. Runnable with no other env var
  set.

  TOKEN-REQUIRED tier (needs FIREBASE_ID_TOKEN, a real unexpired ADMIN ID
  token) -- the only tier that can confirm both sides actually return 202
  with a `deploymentId`. UNLIKE every prior parity script's token-required
  tier (which only ever READS data), a successful call here is a REAL
  Railway `serviceInstanceRedeploy` mutation -- it redeploys the live
  `dashboard-refresh` cron container on BOTH the old and the new path (two
  real redeploys, since this compares old vs new side by side). That is a
  genuine production side effect beyond anything `verify_sign_parity.py`/
  `verify_status_routes_parity.py`/`verify_inspector_asignaciones_parity.py`
  ever do, so it needs a SEPARATE, EXPLICIT opt-in beyond just having a
  token: set `CONFIRM_REDEPLOY=yes`. Having FIREBASE_ID_TOKEN alone is NOT
  enough -- without `CONFIRM_REDEPLOY=yes` this tier is SKIPPED with an
  explicit "PENDING, confirmation required" marker, never fabricated and
  never fired by accident.

BLOCKED entirely this batch -- not run against a live Railway URL. Needs
BOTH a live admin `FIREBASE_ID_TOKEN` AND explicit human confirmation
(`CONFIRM_REDEPLOY=yes`) before it would ever fire a real redeploy; neither
is available to an automated apply batch, and firing a real redeploy is
exactly the kind of production action that must not be triggered
automatically. See tasks.md task 6.3's STATUS note and apply-progress.md's
"Batch 6" section for the concrete BLOCKED write-up.

Usage (structural tier only, no live admin session and no redeploy):

    NEW_REFRESH_URL=https://<railway-app>.up.railway.app/refresh \
    python backend/scripts/verify_refresh_parity.py

Usage (both tiers, once a real admin ID token exists AND a human has
explicitly confirmed firing two real redeploys is acceptable right now):

    NEW_REFRESH_URL=https://<railway-app>.up.railway.app/refresh \
    FIREBASE_ID_TOKEN=<admin id token, same project as production> \
    CONFIRM_REDEPLOY=yes \
    python backend/scripts/verify_refresh_parity.py

Env vars:
    NEW_REFRESH_URL     required. Consolidated app's POST /refresh endpoint.
    FIREBASE_ID_TOKEN    optional. A valid, unexpired ADMIN ID token (same
                          Firebase project both endpoints verify against:
                          sismo-agosto-sgred). Necessary but NOT sufficient
                          to unlock the TOKEN-REQUIRED tier -- also needs
                          CONFIRM_REDEPLOY=yes.
    CONFIRM_REDEPLOY     optional. Must be exactly "yes" to fire the real
                          mutating redeploy calls even when FIREBASE_ID_TOKEN
                          is set. Any other value (including unset) keeps
                          the TOKEN-REQUIRED tier PENDING/skipped.
    OLD_REFRESH_URL      optional. Default: the live legacy Vercel function.
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT_OLD_URL = "https://sismo-cali-dashboard.vercel.app/api/refresh"
FAKE_TOKEN = "not-a-real-token"


def _payload(resp) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    return {"status": resp.status_code, "body": body}


def _post(client, url: str, headers: dict) -> dict:
    return _payload(client.post(url, headers=headers))


def _run_structural(client, old_url: str, new_url: str) -> dict:
    """No-auth and bad-token checks -- reachable without a genuine token,
    non-mutating: both endpoints reject before ever calling Railway."""
    return {
        "old_no_auth": _post(client, old_url, {}),
        "new_no_auth": _post(client, new_url, {}),
        "old_bad_token": _post(client, old_url, {"Authorization": f"Bearer {FAKE_TOKEN}"}),
        "new_bad_token": _post(client, new_url, {"Authorization": f"Bearer {FAKE_TOKEN}"}),
    }


def _run_token_required(client, old_url: str, new_url: str, id_token: str) -> dict:
    """REAL Railway redeploys on BOTH sides -- only called once
    CONFIRM_REDEPLOY=yes has been explicitly set (see module docstring)."""
    headers = {"Authorization": f"Bearer {id_token}"}
    return {
        "old_admin_post": _post(client, old_url, headers),
        "new_admin_post": _post(client, new_url, headers),
    }


def main() -> int:
    import httpx

    new_url = os.environ.get("NEW_REFRESH_URL", "").strip()
    if not new_url:
        print(
            "BLOCKED: set NEW_REFRESH_URL (the consolidated Railway app's "
            "POST /refresh) before running this script. See the module "
            "docstring.",
            file=sys.stderr,
        )
        return 2

    old_url = os.environ.get("OLD_REFRESH_URL", DEFAULT_OLD_URL).strip()
    id_token = os.environ.get("FIREBASE_ID_TOKEN", "").strip()
    confirm_redeploy = os.environ.get("CONFIRM_REDEPLOY", "").strip() == "yes"

    with httpx.Client(timeout=30.0) as client:
        structural = _run_structural(client, old_url, new_url)

        token_required = None
        if id_token and confirm_redeploy:
            token_required = _run_token_required(client, old_url, new_url, id_token)

    print("=== STRUCTURAL TIER (no live admin token required, non-mutating) ===")
    print(json.dumps(structural, indent=2, ensure_ascii=False))

    if token_required is not None:
        print("\n=== TOKEN-REQUIRED TIER (REAL redeploys fired on BOTH sides) ===")
        print(json.dumps(token_required, indent=2, ensure_ascii=False))
        print(
            "\nNOTE: the old endpoint's response may include a "
            "`cruceDeploymentId` field -- the new router has no equivalent "
            "(the cruce-gestion fail-soft redeploy is deliberately not "
            "ported, proposal.md Scope Exclusion Addendum Extension 2 item "
            "5). This is an EXPECTED, documented difference, not a parity "
            "failure -- only compare `ok`/`deploymentId` presence and the "
            "202 status."
        )
    elif id_token and not confirm_redeploy:
        print(
            "\n=== TOKEN-REQUIRED TIER: PENDING -- CONFIRM_REDEPLOY not set to "
            '"yes" ===\n'
            "A FIREBASE_ID_TOKEN was provided, but this tier fires a REAL "
            "Railway redeploy on BOTH endpoints -- a token alone is not "
            "enough consent. Set CONFIRM_REDEPLOY=yes only when a human has "
            "explicitly decided firing two real production redeploys right "
            "now is acceptable."
        )
    else:
        print(
            "\n=== TOKEN-REQUIRED TIER: PENDING -- FIREBASE_ID_TOKEN not set ===\n"
            "The following check was NOT run and has NO result (do not\n"
            "fabricate a pass/fail for it):\n"
            "  - admin POST -> 202 + deploymentId parity (old_admin_post/new_admin_post)\n"
            "Re-run with FIREBASE_ID_TOKEN=<real admin id token> AND "
            "CONFIRM_REDEPLOY=yes to close this -- both are required."
        )

    # Structural-tier verdict: no-auth and bad-token must both reject (401)
    # on old vs new -- the one like-for-like parity assertion reachable
    # without firing a real redeploy.
    structural_ok = all(
        structural[key]["status"] in (401, 403)
        for key in ("old_no_auth", "new_no_auth", "old_bad_token", "new_bad_token")
    )
    print(
        "\nSTRUCTURAL PARITY (no-auth / bad-token rejection):",
        "OK" if structural_ok else "MISMATCH — inspect payloads above",
    )

    return 0 if structural_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
