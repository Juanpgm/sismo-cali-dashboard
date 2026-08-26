"""GET /health — no auth, no credentials. Railway health-check target."""
from __future__ import annotations

from fastapi import APIRouter

# No named client is ever reached from this router (backend-platform spec:
# "A route cannot reach an undeclared client").
REQUIRED_CLIENTS: tuple[str, ...] = ()

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
