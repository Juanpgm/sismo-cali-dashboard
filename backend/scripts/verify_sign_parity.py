"""MANUAL operator tool for tasks.md 2.3 (ADR-7 parity procedure).

NOT part of the automated test suite -- never imported by `app/`, never run
in CI. Hits two LIVE endpoints (old Vercel signer, new Railway `/api/sign`)
side by side and prints both payloads for the PR description, per tasks.md
task 2.3:

  "VERIFY (ADR-7 parity procedure): side-by-side same-token calls, old
  (sismo-fotos-signer.vercel.app, body-idToken) vs new (Bearer header) --
  equivalent presigned URL for the same codigo/slot; both reject the same
  invalid cases."

Runs in two tiers:

  STRUCTURAL tier (only NEW_SIGN_URL required) -- exercises every code path
  reachable WITHOUT a genuine, successfully-verified inspector token: no
  auth at all, a syntactically-present-but-invalid token, each combined
  with a valid body, a bad `codigo`, and an entirely missing field. These
  compare the auth-vs-body-validation ORDERING between the two signers
  (legacy checks body fields before verifying the token; the new router's
  `Depends(require_auth)` verifies the token before the route body -- or
  Pydantic's own body model -- ever runs), which is real, useful parity
  information that needs no live inspector session.

  TOKEN-REQUIRED tier (needs FIREBASE_ID_TOKEN, a real unexpired inspector
  ID token) -- the only tier that can confirm (a) an equivalent presigned
  URL for a genuinely accepted request, and (b) whether an authenticated
  request with a bad `codigo` or a missing field produces the same
  400/422 on both sides once auth itself is not the reason for rejection.
  SKIPPED with an explicit "PENDING" marker (never fabricated) if
  FIREBASE_ID_TOKEN is unset.

BLOCKED entirely until tasks.md 1.4 (manual Railway "web" service creation)
exists -- `NEW_SIGN_URL` has no real value to point at until then.

Usage (structural tier only, no live inspector session needed):

    NEW_SIGN_URL=https://<railway-app>.up.railway.app/api/sign \
    python backend/scripts/verify_sign_parity.py

Usage (both tiers, once a real inspector ID token is available):

    NEW_SIGN_URL=https://<railway-app>.up.railway.app/api/sign \
    FIREBASE_ID_TOKEN=<inspector id token, same project as production> \
    CODIGO=76001-1-0040001 \
    SLOT=1 \
    python backend/scripts/verify_sign_parity.py

Env vars:
    NEW_SIGN_URL       required. Consolidated app's /api/sign endpoint.
    FIREBASE_ID_TOKEN   optional. A valid, unexpired inspector ID token
                         (same Firebase project both endpoints verify
                         against: sismo-agosto-sgred). Unlocks the
                         TOKEN-REQUIRED tier; omit to run structural-only.
    OLD_SIGN_URL        optional. Default: the live legacy signer.
    CODIGO              optional. Default: a syntactically valid test code.
    SLOT                optional. Default: 1.
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT_OLD_SIGN_URL = "https://sismo-fotos-signer.vercel.app/api/sign"
DEFAULT_CODIGO = "76001-1-0040001"
DEFAULT_SLOT = 1
FAKE_TOKEN = "not-a-real-token"


def _call_old(client, url: str, body: dict):
    """Legacy shape: idToken (if any) carried in the JSON body
    (services/photo-signer/api/sign.js:54)."""
    return client.post(url, json=body)


def _call_new(client, url: str, headers: dict, body: dict):
    """Consolidated shape: Authorization Bearer header (if any); body never
    carries idToken."""
    return client.post(url, headers=headers, json=body)


def _payload(resp) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    return {"status": resp.status_code, "body": body}


def _run_structural(client, old_url: str, new_url: str, codigo: str, slot: int) -> dict:
    """Every check reachable without a genuine, verified inspector token."""
    results: dict[str, dict] = {}

    # 1. No auth at all + otherwise-valid body.
    results["old_no_auth"] = _payload(_call_old(client, old_url, {"codigo": codigo, "slot": slot}))
    results["new_no_auth"] = _payload(_call_new(client, new_url, {}, {"codigo": codigo, "slot": slot}))

    # 2. Syntactically-present but invalid token + otherwise-valid body.
    results["old_bad_token"] = _payload(
        _call_old(client, old_url, {"idToken": FAKE_TOKEN, "codigo": codigo, "slot": slot})
    )
    results["new_bad_token"] = _payload(
        _call_new(client, new_url, {"Authorization": f"Bearer {FAKE_TOKEN}"}, {"codigo": codigo, "slot": slot})
    )

    # 3. Invalid token + bad codigo (probes auth-vs-body-validation ordering).
    results["old_bad_token_bad_codigo"] = _payload(
        _call_old(client, old_url, {"idToken": FAKE_TOKEN, "codigo": "not-a-valid-codigo", "slot": slot})
    )
    results["new_bad_token_bad_codigo"] = _payload(
        _call_new(
            client, new_url,
            {"Authorization": f"Bearer {FAKE_TOKEN}"},
            {"codigo": "not-a-valid-codigo", "slot": slot},
        )
    )

    # 4. Invalid token + entirely missing field (WARNING-3 from the slice-2
    #    verify report: legacy computes Number(slot) from a possibly-absent
    #    field -> falls into its own 400 branch; the new router's Pydantic
    #    body model would raise 422 IF it ever got to run -- but ordering
    #    check 3 already establishes whether auth wins first).
    results["old_bad_token_missing_slot"] = _payload(
        _call_old(client, old_url, {"idToken": FAKE_TOKEN, "codigo": codigo})
    )
    results["new_bad_token_missing_slot"] = _payload(
        _call_new(client, new_url, {"Authorization": f"Bearer {FAKE_TOKEN}"}, {"codigo": codigo})
    )

    return results


def _run_token_required(client, old_url: str, new_url: str, id_token: str, codigo: str, slot: int) -> dict:
    """Checks that only mean something with a genuinely verified token."""
    results: dict[str, dict] = {}

    results["old_valid"] = _payload(
        _call_old(client, old_url, {"idToken": id_token, "codigo": codigo, "slot": slot})
    )
    results["new_valid"] = _payload(
        _call_new(client, new_url, {"Authorization": f"Bearer {id_token}"}, {"codigo": codigo, "slot": slot})
    )

    results["old_bad_codigo"] = _payload(
        _call_old(client, old_url, {"idToken": id_token, "codigo": "not-a-valid-codigo", "slot": slot})
    )
    results["new_bad_codigo"] = _payload(
        _call_new(client, new_url, {"Authorization": f"Bearer {id_token}"}, {"codigo": "not-a-valid-codigo", "slot": slot})
    )

    # WARNING-3 (slice-2 verify report): entirely-missing field with a REAL,
    # successfully-verified token -- the only way to observe whether the new
    # router's Pydantic 422 actually differs from legacy's 400 once auth is
    # not the reason for rejection.
    results["old_missing_slot"] = _payload(
        _call_old(client, old_url, {"idToken": id_token, "codigo": codigo})
    )
    results["new_missing_slot"] = _payload(
        _call_new(client, new_url, {"Authorization": f"Bearer {id_token}"}, {"codigo": codigo})
    )

    return results


def main() -> int:
    import httpx

    new_url = os.environ.get("NEW_SIGN_URL", "").strip()
    if not new_url:
        print(
            "BLOCKED: set NEW_SIGN_URL (requires tasks.md 1.4 -- the Railway "
            "web service -- to exist) before running this script. See the "
            "module docstring.",
            file=sys.stderr,
        )
        return 2

    old_url = os.environ.get("OLD_SIGN_URL", DEFAULT_OLD_SIGN_URL).strip()
    id_token = os.environ.get("FIREBASE_ID_TOKEN", "").strip()
    codigo = os.environ.get("CODIGO", DEFAULT_CODIGO).strip()
    slot = int(os.environ.get("SLOT", str(DEFAULT_SLOT)))

    with httpx.Client(timeout=15.0) as client:
        structural = _run_structural(client, old_url, new_url, codigo, slot)
        token_required = (
            _run_token_required(client, old_url, new_url, id_token, codigo, slot)
            if id_token
            else None
        )

    print("=== STRUCTURAL TIER (no live inspector token required) ===")
    print(json.dumps(structural, indent=2, ensure_ascii=False))

    if token_required is not None:
        print("\n=== TOKEN-REQUIRED TIER ===")
        print(json.dumps(token_required, indent=2, ensure_ascii=False))
    else:
        print(
            "\n=== TOKEN-REQUIRED TIER: PENDING -- FIREBASE_ID_TOKEN not set ===\n"
            "The following checks were NOT run and have NO result (do not\n"
            "fabricate a pass/fail for them):\n"
            "  - valid request -> equivalent presigned URL (old_valid/new_valid)\n"
            "  - authenticated bad-codigo -> 400/400 parity (old_bad_codigo/new_bad_codigo)\n"
            "  - authenticated missing-field -> 400-vs-422 (WARNING-3, old_missing_slot/new_missing_slot)\n"
            "Re-run with FIREBASE_ID_TOKEN=<real inspector id token> to close these."
        )

    # Structural-tier verdict. `bad_token` (check 2) is the one genuine
    # like-for-like parity assertion reachable without a real token -- both
    # signers reject a syntactically-present-but-invalid token with the
    # same class of error. Checks 1/3/4 are EXPECTED to diverge (legacy
    # validates body fields before verifying the token; the new router's
    # `Depends(require_auth)` verifies the token first) -- documented as
    # KNOWN DIVERGENCE, not treated as failures.
    bad_token_parity = (
        structural["old_bad_token"]["status"] in (400, 401, 403)
        and structural["new_bad_token"]["status"] in (401, 403)
    )
    print(
        "\nSTRUCTURAL PARITY (invalid-token rejection):",
        "OK" if bad_token_parity else "MISMATCH — inspect payloads above",
    )
    print(
        "KNOWN DIVERGENCES (auth-before-body-validation ordering, expected, "
        "not failures): no-auth status (old 400 body-check-first vs new 401 "
        "auth-dependency-first), bad-token+bad-codigo status, "
        "bad-token+missing-field status. See module docstring."
    )

    return 0 if bad_token_parity else 1


if __name__ == "__main__":
    raise SystemExit(main())
