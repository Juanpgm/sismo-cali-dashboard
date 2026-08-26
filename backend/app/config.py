"""Application settings.

Slice 1 stub: the CORS allowlist and env-var-name constants land in task 1.12
(design.md ADR-7).
"""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Process-wide configuration, sourced from environment variables."""

    app_name: str = "sismo-cali-backend"
