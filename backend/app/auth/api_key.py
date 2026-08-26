"""Service API-key auth for the read-only `/integracion/*` interop endpoints.

Separate from the Firebase Bearer flow in `deps.py`: interop callers are
OTHER systems (not dashboard users), so they present a static shared secret
in an `X-API-Key` header instead of a Firebase ID token.

`APIKeyHeader` (not a raw `Header`) so an OpenAPI `apiKeyAuth` security
scheme gets registered — it shows up in Swagger `/docs`' Authorize dialog
alongside the Bearer scheme. `auto_error=False`: WE raise the 401 so the
status/detail are ours, never the library's default 403.

FAIL CLOSED: when `INTEROP_API_KEY` is unset (empty), EVERY request is
rejected — an unconfigured service never serves interop data openly. The
comparison is constant-time (`hmac.compare_digest`) to avoid leaking the
key through timing.
"""
from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from app.config import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_UNAUTHORIZED = HTTPException(status_code=401, detail="API key inválida o ausente.")


async def require_api_key(provided: str | None = Depends(api_key_header)) -> None:
    expected = Settings().interop_api_key or ""
    if not expected:  # unconfigured -> fail closed, reject everything
        raise _UNAUTHORIZED
    if not provided or not hmac.compare_digest(provided, expected):
        raise _UNAUTHORIZED
