"""Application settings — CORS allowlist (design.md ADR-7)."""
from __future__ import annotations

from pydantic_settings import BaseSettings

# Explicit origin allowlist — no wildcard. Bearer-token auth only
# (allow_credentials=False: no cookies).
CORS_ALLOW_ORIGINS: tuple[str, ...] = (
    "https://sismo-cali-dashboard.vercel.app",
    "https://formulario-atc20-cali.vercel.app",
)

# Local dev origins on any port.
CORS_ALLOW_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1):\d+$"

CORS_ALLOW_METHODS: tuple[str, ...] = ("GET", "POST", "OPTIONS")
CORS_ALLOW_HEADERS: tuple[str, ...] = ("Authorization", "Content-Type")
CORS_ALLOW_CREDENTIALS = False


class Settings(BaseSettings):
    """Process-wide configuration, sourced from environment variables."""

    app_name: str = "sismo-cali-backend"

    # `planeacion-asignaciones` change, Phase 3, design.md ADR-6:
    # `getEnlaceSurvey` config. Both default to "" (not configured) so a
    # missing env var fails loud (503) rather than emitting a broken link —
    # see `routers/planeacion_asignaciones.py`'s `get_enlace_survey`.
    survey123_form_url: str = ""
    survey123_field_app_item_id: str = ""
