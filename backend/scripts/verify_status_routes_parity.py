"""MANUAL operator tool for tasks.md 4.5 (ADR-7 parity procedure).

NOT part of the automated test suite -- never imported by `app/`, never run
in CI. Hits FOUR LIVE endpoints (old Vercel `sticker-status`/`source-status`
vs new Railway `/sticker-status`/`/source-status`) side by side and prints
every payload for the PR description, per tasks.md task 4.5:

  "VERIFY (ADR-7 procedure): side-by-side same-token calls for both routes;
  record diff."

Follows the same two-tier convention `verify_sign_parity.py` (task 2.3)
established:

  STRUCTURAL tier (only NEW_*_URL required) -- exercises every code path
  reachable WITHOUT a genuine, successfully-verified token: no auth at all,
  and a syntactically-present-but-invalid token, against both routes. Both
  legacy handlers and both new routers reject these identically with 401
  (auth is checked before anything else on all four handlers), which is
  real, useful parity information that needs no live session.

  TOKEN-REQUIRED tier (needs FIREBASE_ID_TOKEN) -- the only tier that can
  confirm the actual 200 payload shape/values match. IMPORTANT: `/source-
  status` is admin-gated (backend-platform spec: "Admin-gated route rejects
  non-admin") -- FIREBASE_ID_TOKEN must resolve to the `admin` role for its
  200-vs-200 comparison to mean anything; a non-admin token will correctly
  get 403 on both sides instead (still valid parity information, printed as
  such, not treated as a mismatch). `/sticker-status` accepts ANY
  authenticated role, so the same token works for both routes regardless of
  which role it resolves to. SKIPPED with an explicit "PENDING" marker
  (never fabricated) if FIREBASE_ID_TOKEN is unset.

BLOCKED entirely until tasks.md 1.4 (done, per apply-progress.md's "Cutover
status sync") -- NEW_STICKER_STATUS_URL/NEW_SOURCE_STATUS_URL now have a
real base to point at (`sismo-cali-dashboard-production.up.railway.app`),
but this script still needs a live FIREBASE_ID_TOKEN, which no automated
apply batch can fabricate (same class of blocker task 2.3 hit before its
operator step).

Usage (structural tier only, no live token needed):

    NEW_STICKER_STATUS_URL=https://<railway-app>.up.railway.app/sticker-status \
    NEW_SOURCE_STATUS_URL=https://<railway-app>.up.railway.app/source-status \
    python backend/scripts/verify_status_routes_parity.py

Usage (both tiers, once a real token is available -- admin role required for
a meaningful /source-status 200 comparison):

    NEW_STICKER_STATUS_URL=https://<railway-app>.up.railway.app/sticker-status \
    NEW_SOURCE_STATUS_URL=https://<railway-app>.up.railway.app/source-status \
    FIREBASE_ID_TOKEN=<admin id token, same project as production> \
    python backend/scripts/verify_status_routes_parity.py

Env vars:
    NEW_STICKER_STATUS_URL   required. Consolidated app's /sticker-status.
    NEW_SOURCE_STATUS_URL    required. Consolidated app's /source-status.
    FIREBASE_ID_TOKEN        optional. A valid, unexpired ID token (admin
                              role recommended -- see TOKEN-REQUIRED tier
                              note above). Unlocks that tier; omit to run
                              structural-only.
    OLD_STICKER_STATUS_URL   optional. Default: the live legacy function.
    OLD_SOURCE_STATUS_URL    optional. Default: the live legacy function.
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT_OLD_STICKER_STATUS_URL = "https://sismo-cali-dashboard.vercel.app/api/sticker-status"
DEFAULT_OLD_SOURCE_STATUS_URL = "https://sismo-cali-dashboard.vercel.app/api/source-status"
FAKE_TOKEN = "not-a-real-token"


def _payload(resp) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    return {"status": resp.status_code, "body": body}


def _get(client, url: str, headers: dict) -> dict:
    return _payload(client.get(url, headers=headers))


def _run_structural(client, old_url: str, new_url: str) -> dict:
    """No-auth and bad-token checks -- reachable without a genuine token,
    identical on both the legacy handler and the new router (both check
    auth before anything else)."""
    return {
        "old_no_auth": _get(client, old_url, {}),
        "new_no_auth": _get(client, new_url, {}),
        "old_bad_token": _get(client, old_url, {"Authorization": f"Bearer {FAKE_TOKEN}"}),
        "new_bad_token": _get(client, new_url, {"Authorization": f"Bearer {FAKE_TOKEN}"}),
    }


def _run_token_required(client, old_url: str, new_url: str, id_token: str) -> dict:
    headers = {"Authorization": f"Bearer {id_token}"}
    return {
        "old_valid": _get(client, old_url, headers),
        "new_valid": _get(client, new_url, headers),
    }


def _print_route_block(name: str, structural: dict, token_required: dict | None) -> None:
    print(f"\n=== {name}: STRUCTURAL TIER (no live token required) ===")
    print(json.dumps(structural, indent=2, ensure_ascii=False))
    if token_required is not None:
        print(f"\n=== {name}: TOKEN-REQUIRED TIER ===")
        print(json.dumps(token_required, indent=2, ensure_ascii=False))
    else:
        print(
            f"\n=== {name}: TOKEN-REQUIRED TIER: PENDING -- FIREBASE_ID_TOKEN not set ===\n"
            "The 200-vs-200 payload comparison was NOT run and has NO result\n"
            "(do not fabricate a pass/fail for it). Re-run with\n"
            "FIREBASE_ID_TOKEN=<real id token> to close this."
        )


def main() -> int:
    import httpx

    new_sticker_url = os.environ.get("NEW_STICKER_STATUS_URL", "").strip()
    new_source_url = os.environ.get("NEW_SOURCE_STATUS_URL", "").strip()
    if not new_sticker_url or not new_source_url:
        print(
            "BLOCKED: set both NEW_STICKER_STATUS_URL and NEW_SOURCE_STATUS_URL "
            "(the consolidated Railway app's /sticker-status and /source-status) "
            "before running this script. See the module docstring.",
            file=sys.stderr,
        )
        return 2

    old_sticker_url = os.environ.get("OLD_STICKER_STATUS_URL", DEFAULT_OLD_STICKER_STATUS_URL).strip()
    old_source_url = os.environ.get("OLD_SOURCE_STATUS_URL", DEFAULT_OLD_SOURCE_STATUS_URL).strip()
    id_token = os.environ.get("FIREBASE_ID_TOKEN", "").strip()

    with httpx.Client(timeout=15.0) as client:
        sticker_structural = _run_structural(client, old_sticker_url, new_sticker_url)
        source_structural = _run_structural(client, old_source_url, new_source_url)
        sticker_token_required = (
            _run_token_required(client, old_sticker_url, new_sticker_url, id_token) if id_token else None
        )
        source_token_required = (
            _run_token_required(client, old_source_url, new_source_url, id_token) if id_token else None
        )

    _print_route_block("sticker-status", sticker_structural, sticker_token_required)
    _print_route_block("source-status", source_structural, source_token_required)

    # Structural-tier verdict: both no-auth and bad-token checks must reject
    # identically (401) on old vs new, for both routes -- the one
    # like-for-like parity assertion reachable without a real token.
    def _both_401(block: dict, no_auth_key: str, bad_token_key: str) -> bool:
        return block[no_auth_key]["status"] == 401 and block[bad_token_key]["status"] == 401

    sticker_ok = _both_401(sticker_structural, "old_no_auth", "old_bad_token") and _both_401(
        sticker_structural, "new_no_auth", "new_bad_token"
    )
    source_ok = _both_401(source_structural, "old_no_auth", "old_bad_token") and _both_401(
        source_structural, "new_no_auth", "new_bad_token"
    )

    print(
        "\nSTRUCTURAL PARITY (no-auth / bad-token rejection):",
        "sticker-status OK" if sticker_ok else "sticker-status MISMATCH — inspect payloads above",
        "|",
        "source-status OK" if source_ok else "source-status MISMATCH — inspect payloads above",
    )

    return 0 if (sticker_ok and source_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
