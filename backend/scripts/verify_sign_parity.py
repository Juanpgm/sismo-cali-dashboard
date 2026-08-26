"""MANUAL operator tool for tasks.md 2.3 (ADR-7 parity procedure).

NOT part of the automated test suite — never imported by `app/`, never run
in CI. Hits two LIVE endpoints (old Vercel signer, new Railway `/api/sign`)
side by side with the SAME Firebase ID token and prints both payloads for
the PR description, per tasks.md task 2.3:

  "VERIFY (ADR-7 parity procedure): side-by-side same-token calls, old
  (sismo-fotos-signer.vercel.app, body-idToken) vs new (Bearer header) —
  equivalent presigned URL for the same codigo/slot; both reject the same
  invalid cases."

BLOCKED until tasks.md 1.4 (manual Railway "web" service creation) exists —
`NEW_SIGN_URL` has no real value to point at yet. This script is written
and ready so 2.3 can be run the moment 1.4 lands, with zero further code
changes needed.

Usage (once 1.4 is live):

    NEW_SIGN_URL=https://<railway-app>.up.railway.app/api/sign \
    FIREBASE_ID_TOKEN=<inspector id token, same project as production> \
    CODIGO=76001-1-0040001 \
    SLOT=1 \
    python backend/scripts/verify_sign_parity.py

Env vars:
    NEW_SIGN_URL       required. Consolidated app's /api/sign endpoint.
    FIREBASE_ID_TOKEN   required. A valid, unexpired inspector ID token
                         (same Firebase project both endpoints verify
                         against: sismo-agosto-sgred).
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


def _call_old(client, url: str, id_token: str, codigo: str, slot: int):
    """Legacy shape: idToken carried in the JSON body (services/photo-signer/api/sign.js:54)."""
    return client.post(url, json={"idToken": id_token, "codigo": codigo, "slot": slot})


def _call_new(client, url: str, id_token: str, codigo: str, slot: int):
    """Consolidated shape: Authorization Bearer header, body is {codigo, slot} only."""
    return client.post(
        url,
        headers={"Authorization": f"Bearer {id_token}"},
        json={"codigo": codigo, "slot": slot},
    )


def _payload(resp) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    return {"status": resp.status_code, "body": body}


def main() -> int:
    import httpx

    new_url = os.environ.get("NEW_SIGN_URL", "").strip()
    id_token = os.environ.get("FIREBASE_ID_TOKEN", "").strip()
    if not new_url or not id_token:
        print(
            "BLOCKED: set NEW_SIGN_URL (requires tasks.md 1.4 — the Railway "
            "web service — to exist) and FIREBASE_ID_TOKEN before running "
            "this script. See the module docstring.",
            file=sys.stderr,
        )
        return 2

    old_url = os.environ.get("OLD_SIGN_URL", DEFAULT_OLD_SIGN_URL).strip()
    codigo = os.environ.get("CODIGO", DEFAULT_CODIGO).strip()
    slot = int(os.environ.get("SLOT", str(DEFAULT_SLOT)))

    results: dict[str, dict] = {}
    with httpx.Client(timeout=15.0) as client:
        results["old_valid"] = _payload(_call_old(client, old_url, id_token, codigo, slot))
        results["new_valid"] = _payload(_call_new(client, new_url, id_token, codigo, slot))
        results["old_bad_codigo"] = _payload(
            _call_old(client, old_url, id_token, "not-a-valid-codigo", slot)
        )
        results["new_bad_codigo"] = _payload(
            _call_new(client, new_url, id_token, "not-a-valid-codigo", slot)
        )
        results["old_bad_token"] = _payload(
            _call_old(client, old_url, "not-a-real-token", codigo, slot)
        )
        results["new_bad_token"] = _payload(
            _call_new(client, new_url, "not-a-real-token", codigo, slot)
        )

    print(json.dumps(results, indent=2, ensure_ascii=False))

    valid_parity = (
        results["old_valid"]["status"] == 200 and results["new_valid"]["status"] == 200
    )
    reject_parity = (
        results["old_bad_codigo"]["status"] == 400
        and results["new_bad_codigo"]["status"] == 400
        and results["old_bad_token"]["status"] in (401, 403)
        and results["new_bad_token"]["status"] in (401, 403)
    )
    print(
        "\nPARITY:",
        "OK" if valid_parity and reject_parity else "MISMATCH — inspect payloads above",
    )
    return 0 if valid_parity and reject_parity else 1


if __name__ == "__main__":
    raise SystemExit(main())
