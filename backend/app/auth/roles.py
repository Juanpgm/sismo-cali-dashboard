"""Effective dashboard role resolution — ported verbatim from
`api/refresh.js:63-94` (design.md ADR-3).

Roles: 'admin' (Administrador — full access), 'usuario' (password default),
'viewer' (Google @cali.gov.co), 'inspector' (@sismocali.gov.co field
account), 'otro'. Enforcement is server-side, via `auth/deps.py`'s
`Depends`; any front-end gating is cosmetic.

Precedence, and WHY (verbatim from the JS source comment):
  1. SUPERADMIN_EMAIL  -> always 'admin', even without a claim. This is the
     bootstrap + anti-lockout: the one account that can never be demoted, so
     there is always at least one Administrador to hand out roles.
  2. explicit custom claim `role` -> assigned via the "Cambiar rol" action.
  3. @sismocali.gov.co -> 'inspector' (they are password-provider too, so
     the domain must be tested before the generic password branch).
  4. password (no claim) -> 'usuario' (password no longer implies admin;
     admin is now an explicit, assignable claim).
  5. google.com + @cali.gov.co -> 'viewer'. Else 'otro'.
"""
from __future__ import annotations

import os
from typing import Any

SUPERADMIN_EMAIL = os.environ.get("SUPERADMIN_EMAIL", "juanp.gzmz@gmail.com").lower()
INSPECTOR_DOMAIN = "@sismocali.gov.co"
VIEWER_DOMAIN = "@cali.gov.co"


def role_from(
    email: str | None = None,
    claim_role: str | None = None,
    provider: str | None = None,
) -> str:
    """Port of `api/refresh.js#roleFrom({email, claimRole, provider})`."""
    e = str(email or "").lower()
    if e == SUPERADMIN_EMAIL:
        return "admin"
    if claim_role:
        return claim_role
    if e.endswith(INSPECTOR_DOMAIN):
        return "inspector"
    if provider == "password":
        return "usuario"
    if provider == "google.com" and e.endswith(VIEWER_DOMAIN):
        return "viewer"
    return "otro"


def role_from_claims(claims: dict[str, Any] | None) -> str:
    """Port of `api/refresh.js#roleFromClaims(claims)` — from a verified
    Firebase ID-token payload (custom claims sit at the top level)."""
    claims = claims or {}
    firebase = claims.get("firebase") or {}
    return role_from(
        email=claims.get("email"),
        claim_role=claims.get("role"),
        provider=firebase.get("sign_in_provider"),
    )
