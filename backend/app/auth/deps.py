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

from fastapi import Depends, Header, HTTPException

from app.auth.roles import role_from_claims
from app.auth.verify import TokenVerificationError, verify_firebase_token

FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "sismo-agosto-sgred")


async def current_claims(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
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
