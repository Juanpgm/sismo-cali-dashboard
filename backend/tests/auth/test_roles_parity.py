"""Table-driven port of the JS `roleFrom`/`classify` parity fixture matrix
(design.md ADR-3; backend-platform spec: "Parity suite passes identically to
the JS test matrix", "Precedence order resolves to the earliest matching
rule").

Source of truth for the fixtures: `api/usuarios.test.js:8-22` (NOT a dedicated
`api/refresh.test.js`, which does not exist despite `api/refresh.js:183`'s
stale comment pointing at it). `api/usuarios.test.js` exercises `roleFrom` via
its own `classify()` wrapper, which forwards `email`/`customClaims.role`/an
inferred `password`|`google.com`|`''` provider straight into
`refresh.js#roleFrom` — so the (email, claim_role, provider) triples below are
copied verbatim from that fixture file's `inspector`/`usuario`/`admin`/
`superadmin`/`viewer`/`otro` objects and the claim-override case at line 22.

Precedence (design.md ADR-3, `api/refresh.js:63-76`), verbatim:
    SUPERADMIN_EMAIL -> claim `role` -> `@sismocali.gov.co` -> `password`
    provider -> `google.com` + `@cali.gov.co` -> `otro`.
"""
from __future__ import annotations

import pytest

from app.auth.roles import role_from, role_from_claims

# (case name, email, claim_role, provider, expected role) — ported verbatim
# from api/usuarios.test.js:8-22.
ROLE_FROM_CASES = [
    ("inspector: @sismocali wins over password", "cedula@sismocali.gov.co", None, "password", "inspector"),
    ("usuario: password default is usuario, NOT admin", "someone@example.com", None, "password", "usuario"),
    ("admin: explicit custom claim", "boss@example.com", "admin", "password", "admin"),
    ("superadmin: superadmin email, no claim needed", "juanp.gzmz@gmail.com", None, "password", "admin"),
    ("viewer: google.com + @cali.gov.co", "viewer@cali.gov.co", None, "google.com", "viewer"),
    ("otro: google.com, no @cali.gov.co", "stray@gmail.com", None, "google.com", "otro"),
    ("claim overrides the derived default (usuario -> admin)", "someone@example.com", "admin", "password", "admin"),
]


@pytest.mark.parametrize("case_name, email, claim_role, provider, expected", ROLE_FROM_CASES)
def test_role_from_matches_js_fixture_matrix(case_name, email, claim_role, provider, expected):
    assert role_from(email=email, claim_role=claim_role, provider=provider) == expected, case_name


def test_role_from_superadmin_email_wins_over_claim_role():
    """backend-platform spec: "Precedence order resolves to the earliest
    matching rule" — SUPERADMIN_EMAIL + claim role:'viewer' -> 'admin', the
    email match wins over the claim (api/refresh.js:79-80 order)."""
    assert role_from(email="juanp.gzmz@gmail.com", claim_role="viewer", provider="password") == "admin"


# ---- role_from_claims: verified-token-shaped payloads ----------------------
# `roleFromClaims` (api/refresh.js:88-94) destructures a decoded Firebase
# claims object: top-level `email`, top-level `role` custom claim,
# `claims.firebase.sign_in_provider`.
ROLE_FROM_CLAIMS_CASES = [
    (
        "inspector claims payload",
        {"email": "cedula@sismocali.gov.co", "firebase": {"sign_in_provider": "password"}},
        "inspector",
    ),
    (
        "usuario claims payload",
        {"email": "someone@example.com", "firebase": {"sign_in_provider": "password"}},
        "usuario",
    ),
    (
        "admin claim role in claims payload",
        {"email": "boss@example.com", "role": "admin", "firebase": {"sign_in_provider": "password"}},
        "admin",
    ),
    (
        "superadmin claims payload, no claim needed",
        {"email": "juanp.gzmz@gmail.com", "firebase": {"sign_in_provider": "password"}},
        "admin",
    ),
    (
        "viewer claims payload",
        {"email": "viewer@cali.gov.co", "firebase": {"sign_in_provider": "google.com"}},
        "viewer",
    ),
    (
        "otro claims payload",
        {"email": "stray@gmail.com", "firebase": {"sign_in_provider": "google.com"}},
        "otro",
    ),
    (
        "superadmin email wins over claim role in claims payload",
        {"email": "juanp.gzmz@gmail.com", "role": "viewer", "firebase": {"sign_in_provider": "password"}},
        "admin",
    ),
]


@pytest.mark.parametrize("case_name, claims, expected", ROLE_FROM_CLAIMS_CASES)
def test_role_from_claims_matches_js_fixture_matrix(case_name, claims, expected):
    assert role_from_claims(claims) == expected, case_name
