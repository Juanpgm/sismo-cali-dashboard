"""FastAPI `Depends` wrappers around `verify.py`/`roles.py` — design.md
ADR-3's per-route auth matrix foundation:

- `current_claims`  — verifies the Bearer ID token, returns its claims.
  401 fail-closed on any missing/invalid token, matching every legacy
  `api/*.js` handler's `Autenticación requerida.` / `Token inválido: ...`
  responses.
- `require_auth`    — any authenticated role (`/sticker-status`,
  `/inspector-asignaciones`, `/sign`).
- `require_role(role)` — dependency FACTORY; 403 unless the resolved role
  matches exactly (`/refresh`, `/stickers`, `/sticker-asignaciones`,
  `/source-status`, `/usuarios` — the latter layers its own extra
  provider/domain gate at the route, per ADR-3's table).
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.roles import role_from_claims
from app.auth.verify import TokenVerificationError, verify_firebase_token

FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "sismo-agosto-sgred")

# HTTPBearer (not a raw `Header`) so an OpenAPI `bearerAuth` security scheme
# gets registered — that is what puts the Authorize button in Swagger `/docs`.
# `auto_error=False`: WE raise the 401 below (same status/detail every legacy
# `api/*.js` handler and `test_deps.py` assert), never HTTPBearer's own 403.
bearer = HTTPBearer(auto_error=False)


async def current_claims(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    token = cred.credentials if cred else ""
    if not token:
        raise HTTPException(status_code=401, detail="Autenticación requerida.")
    try:
        return await verify_firebase_token(token, FIREBASE_PROJECT_ID)
    except TokenVerificationError as exc:
        raise HTTPException(status_code=401, detail=f"Token inválido: {exc}") from exc


async def require_auth(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, Any]:
    """Any authenticated role — no additional role check."""
    return claims


def require_role(role: str):
    """Dependency factory: 403 unless `role_from_claims(claims) == role`."""

    async def _dependency(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, Any]:
        if role_from_claims(claims) != role:
            raise HTTPException(status_code=403, detail="No autorizado.")
        return claims

    return _dependency
