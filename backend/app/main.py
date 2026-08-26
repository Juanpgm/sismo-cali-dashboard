"""FastAPI application factory.

`create_app()` is a factory (not a module-level singleton) precisely so tests
can inject fakes — no network, no real credentials in CI (design.md ADR-8).
"""
from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    Slice 1 stub: real wiring (CORS, credential validation, routers) lands in
    task 1.12 (backend-platform "Named-Client Credential Matrix...",
    "Universal Explicit CORS Allowlist").
    """
    return FastAPI(title="sismo-cali-backend")


app = create_app()
