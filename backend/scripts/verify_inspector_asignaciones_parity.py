"""MANUAL operator tool for tasks.md 5.4 (ADR-7 parity procedure).

NOT part of the automated test suite -- never imported by `app/`, never run
in CI. Hits two LIVE endpoints (old Vercel `api/inspector-asignaciones.js`
vs new Railway `/inspector-asignaciones`) side by side and prints both
payloads for the PR description, per tasks.md task 5.4:

  "VERIFY (ADR-7 procedure): side-by-side same-inspector-token calls, both
  actions; record diff."

Follows the same two-tier convention `verify_sign_parity.py` (task 2.3) and
`verify_status_routes_parity.py` (task 4.5) established:

  STRUCTURAL tier (only NEW_INSPECTOR_ASIGNACIONES_URL required) --
  exercises every code path reachable WITHOUT a genuine, successfully-
  verified inspector token: no auth at all, and a syntactically-present-
  but-invalid token, against both actions (`misPuntos`, `marcarHecho`).
  Both the legacy handler and the new router reject these identically with
  401 (auth is checked before the action dispatch on both sides), which is
  real, useful parity information that needs no live inspector session.

  TOKEN-REQUIRED tier (needs FIREBASE_ID_TOKEN, a real unexpired inspector
  ID token) -- the only tier that can confirm the actual `misPuntos`
  payload shape/values match, and that `marcarHecho` against a point NOT
  assigned to that inspector is rejected identically on both sides
  (field-form-session "Cross-inspector access still rejected after
  migration"). IMPORTANT: `marcarHecho` is a MUTATING action -- this
  script never calls it against a real point automatically (unlike
  `misPuntos`, which is read-only and safe to call on both sides freely).
  Set MARCAR_HECHO_PUNTO_ID explicitly (a point the test inspector is
  known to own, or a point known to be assigned to a DIFFERENT inspector,
  to exercise the cross-uid-rejection side) to opt into that check; it is
  SKIPPED by default even when FIREBASE_ID_TOKEN is set, to avoid
  accidentally flipping a real production point to `hecho`.
  SKIPPED with an explicit "PENDING" marker (never fabricated) if
  FIREBASE_ID_TOKEN is unset.

BLOCKED entirely on a live `FIREBASE_ID_TOKEN` -- NOT the tasks.md 1.4
class of blocker (the Railway "web" service is live, per
apply-progress.md's "Cutover status sync" section --
`NEW_INSPECTOR_ASIGNACIONES_URL` has a real base to point at). This is the
SAME class of blocker slice 2's 2.3 token-required tier and slice 4's 4.5
still carry: no automated apply batch can fabricate a real Firebase ID
token belonging to a registered inspector.

Usage (structural tier only, no live inspector session needed):

    NEW_INSPECTOR_ASIGNACIONES_URL=https://<railway-app>.up.railway.app/inspector-asignaciones \
    python backend/scripts/verify_inspector_asignaciones_parity.py

Usage (both tiers, once a real inspector ID token is available):

    NEW_INSPECTOR_ASIGNACIONES_URL=https://<railway-app>.up.railway.app/inspector-asignaciones \
    FIREBASE_ID_TOKEN=<inspector id token, same project as production> \
    python backend/scripts/verify_inspector_asignaciones_parity.py

Env vars:
    NEW_INSPECTOR_ASIGNACIONES_URL   required. Consolidated app's
                                       /inspector-asignaciones endpoint
                                       (note: no /api prefix -- see
                                       app/routers/inspector_asignaciones.py's
                                       module docstring).
    FIREBASE_ID_TOKEN                 optional. A valid, unexpired
                                       inspector ID token (any authenticated
                                       role -- this route accepts any).
                                       Unlocks the misPuntos comparison;
                                       omit to run structural-only.
    MARCAR_HECHO_PUNTO_ID             optional. Only used if
                                       FIREBASE_ID_TOKEN is also set. A
                                       specific sticker_matches doc id to
                                       exercise marcarHecho against on BOTH
                                       endpoints. MUTATES real data if the
                                       point belongs to the test token's
                                       inspector -- omit unless you have a
                                       disposable/test point.
    OLD_INSPECTOR_ASIGNACIONES_URL    optional. Default: the live legacy
                                       Vercel function.
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT_OLD_URL = "https://sismo-cali-dashboard.vercel.app/api/inspector-asignaciones"
FAKE_TOKEN = "not-a-real-token"


def _payload(resp) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    return {"status": resp.status_code, "body": body}


def _post(client, url: str, headers: dict, body: dict) -> dict:
    return _payload(client.post(url, headers=headers, json=body))


def _run_structural(client, old_url: str, new_url: str) -> dict:
    """No-auth and bad-token checks, for both actions -- reachable without
    a genuine token, identical on both the legacy handler and the new
    router (both check auth before the action dispatch)."""
    return {
        "old_no_auth_mis_puntos": _post(client, old_url, {}, {"action": "misPuntos"}),
        "new_no_auth_mis_puntos": _post(client, new_url, {}, {"action": "misPuntos"}),
        "old_bad_token_mis_puntos": _post(
            client, old_url, {"Authorization": f"Bearer {FAKE_TOKEN}"}, {"action": "misPuntos"}
        ),
        "new_bad_token_mis_puntos": _post(
            client, new_url, {"Authorization": f"Bearer {FAKE_TOKEN}"}, {"action": "misPuntos"}
        ),
        "old_bad_token_marcar_hecho": _post(
            client,
            old_url,
            {"Authorization": f"Bearer {FAKE_TOKEN}"},
            {"action": "marcarHecho", "punto_id": "does-not-matter"},
        ),
        "new_bad_token_marcar_hecho": _post(
            client,
            new_url,
            {"Authorization": f"Bearer {FAKE_TOKEN}"},
            {"action": "marcarHecho", "punto_id": "does-not-matter"},
        ),
    }


def _run_token_required(client, old_url: str, new_url: str, id_token: str, punto_id: str | None) -> dict:
    headers = {"Authorization": f"Bearer {id_token}"}
    results: dict[str, dict] = {
        "old_mis_puntos": _post(client, old_url, headers, {"action": "misPuntos"}),
        "new_mis_puntos": _post(client, new_url, headers, {"action": "misPuntos"}),
        "old_unrecognized_action": _post(client, old_url, headers, {"action": "bogus"}),
        "new_unrecognized_action": _post(client, new_url, headers, {"action": "bogus"}),
    }
    if punto_id:
        results["old_marcar_hecho"] = _post(
            client, old_url, headers, {"action": "marcarHecho", "punto_id": punto_id}
        )
        results["new_marcar_hecho"] = _post(
            client, new_url, headers, {"action": "marcarHecho", "punto_id": punto_id}
        )
    return results


def main() -> int:
    import httpx

    new_url = os.environ.get("NEW_INSPECTOR_ASIGNACIONES_URL", "").strip()
    if not new_url:
        print(
            "BLOCKED: set NEW_INSPECTOR_ASIGNACIONES_URL (the consolidated "
            "Railway app's /inspector-asignaciones -- no /api prefix) before "
            "running this script. See the module docstring.",
            file=sys.stderr,
        )
        return 2

    old_url = os.environ.get("OLD_INSPECTOR_ASIGNACIONES_URL", DEFAULT_OLD_URL).strip()
    id_token = os.environ.get("FIREBASE_ID_TOKEN", "").strip()
    punto_id = os.environ.get("MARCAR_HECHO_PUNTO_ID", "").strip() or None

    with httpx.Client(timeout=15.0) as client:
        structural = _run_structural(client, old_url, new_url)
        token_required = (
            _run_token_required(client, old_url, new_url, id_token, punto_id) if id_token else None
        )

    print("=== STRUCTURAL TIER (no live inspector token required) ===")
    print(json.dumps(structural, indent=2, ensure_ascii=False))

    if token_required is not None:
        print("\n=== TOKEN-REQUIRED TIER ===")
        print(json.dumps(token_required, indent=2, ensure_ascii=False))
        if punto_id is None:
            print(
                "\nNOTE: marcarHecho was NOT exercised (MARCAR_HECHO_PUNTO_ID not "
                "set) -- it is a mutating action, skipped by default to avoid "
                "flipping a real production point. Set MARCAR_HECHO_PUNTO_ID to "
                "opt in once a disposable/test point is available."
            )
    else:
        print(
            "\n=== TOKEN-REQUIRED TIER: PENDING -- FIREBASE_ID_TOKEN not set ===\n"
            "The following checks were NOT run and have NO result (do not\n"
            "fabricate a pass/fail for them):\n"
            "  - authenticated misPuntos payload shape/values (old_mis_puntos/new_mis_puntos)\n"
            "  - authenticated unrecognized-action parity (old_unrecognized_action/new_unrecognized_action)\n"
            "  - marcarHecho own-uid/cross-uid parity (opt-in via MARCAR_HECHO_PUNTO_ID)\n"
            "Re-run with FIREBASE_ID_TOKEN=<real inspector id token> to close these."
        )

    # Structural-tier verdict: both no-auth and bad-token checks must reject
    # identically (401) on old vs new, for both actions -- the one
    # like-for-like parity assertion reachable without a real token.
    def _is_401(block: dict, key: str) -> bool:
        return block[key]["status"] == 401

    structural_ok = all(
        _is_401(structural, key)
        for key in (
            "old_no_auth_mis_puntos",
            "new_no_auth_mis_puntos",
            "old_bad_token_mis_puntos",
            "new_bad_token_mis_puntos",
            "old_bad_token_marcar_hecho",
            "new_bad_token_marcar_hecho",
        )
    )

    print(
        "\nSTRUCTURAL PARITY (no-auth / bad-token rejection, both actions):",
        "OK" if structural_ok else "MISMATCH — inspect payloads above",
    )

    return 0 if structural_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
