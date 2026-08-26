"""FastAPI application factory.

`create_app()` is a factory (not a module-level singleton) precisely so tests
can inject fakes — no network, no real credentials in CI (design.md ADR-8).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.credentials import clients as credentials
from app.routers import health

# Every router mounted by create_app(). Extended one module per migration
# slice (tasks.md phases 2-8).
_ROUTERS = (health,)


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    Fail-fast (design.md ADR-4, backend-platform "Missing web-route
    credential fails startup"): validates the union of WEB_STARTUP_CLIENTS
    and every mounted router's REQUIRED_CLIENTS BEFORE the app object is
    constructed — startup fails before any request can be served.
    """
    required = credentials.required_clients_for(_ROUTERS)
    credentials.require(*required)

    app = FastAPI(title=config.Settings().app_name)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.CORS_ALLOW_ORIGINS),
        allow_origin_regex=config.CORS_ALLOW_ORIGIN_REGEX,
        allow_credentials=config.CORS_ALLOW_CREDENTIALS,
        allow_methods=list(config.CORS_ALLOW_METHODS),
        allow_headers=list(config.CORS_ALLOW_HEADERS),
    )

    for router_module in _ROUTERS:
        app.include_router(router_module.router)

    return app
