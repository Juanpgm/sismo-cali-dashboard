"""Firebase ID token verification and role resolution.

Ported from `api/refresh.js` (design.md ADR-3): `verify.py` (RS256 against
Google's rotating x509 certs), `roles.py` (`role_from`/`role_from_claims`,
pure functions), `deps.py` (FastAPI `Depends` wrappers: `require_auth`,
`require_role`, `current_claims`).
"""
