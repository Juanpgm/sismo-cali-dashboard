"""FastAPI application factory.

`create_app()` is a factory (not a module-level singleton) precisely so tests
can inject fakes — no network, no real credentials in CI (design.md ADR-8).
"""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.credentials import clients as credentials
from app.routers import health, inspector_asignaciones, reportados, sign
from app.services.snapshot import ReportadosSnapshot, refresh_loop, seed_from_blob

# Every router mounted by create_app(). Extended one module per migration
# slice (tasks.md phases 2-8).
_ROUTERS = (health, sign, reportados, inspector_asignaciones)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Owns the `reportados` background refresh task (design.md ADR-5).

    `app.state.reportados_snapshot` itself is attached synchronously in
    `create_app()` below, NOT here — tests build routers against
    `TestClient(app)` without entering the `with TestClient(app) as
    client:` context manager that triggers `lifespan`, matching every
    other router test file in this suite. Only the actually-async
    work (best-effort Blob seed, the forever-refresh task) belongs here.
    """
    snapshot = app.state.reportados_snapshot
    await seed_from_blob(snapshot)  # best-effort, never raises
    task = asyncio.create_task(refresh_loop(snapshot))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    Fail-fast (design.md ADR-4, backend-platform "Missing web-route
    credential fails startup"): validates the union of WEB_STARTUP_CLIENTS
    and every mounted router's REQUIRED_CLIENTS BEFORE the app object is
    constructed — startup fails before any request can be served.
    """
    required = credentials.required_clients_for(_ROUTERS)
    credentials.require(*required)

    app = FastAPI(title=config.Settings().app_name, lifespan=_lifespan)

    # Attached synchronously (not inside `_lifespan`) so router tests can
    # populate/replace it via a plain `TestClient(app)` — no need to enter
    # `lifespan` just to get a snapshot store to write to.
    app.state.reportados_snapshot = ReportadosSnapshot()

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
