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
from app.routers import (
    health,
    integracion,
    inspector_asignaciones,
    panel_representante,
    planeacion_asignaciones,
    planeacion_cruce,
    refresh,
    reportados,
    sign,
    source_status,
    sticker_asignaciones,
    stickers,
    sticker_status,
    survey_cali,
    usuarios,
)
from app.routers.planeacion_asignaciones import PlaneacionAggregatesCache
from app.routers.sticker_status import StickerStatusCache
from app.services.snapshot import ReportadosSnapshot, refresh_loop, seed_from_blob

# Every router mounted by create_app(). Extended one module per migration
# slice (tasks.md phases 2-8).
_ROUTERS = (
    health,
    sign,
    reportados,
    sticker_status,
    source_status,
    inspector_asignaciones,
    refresh,
    stickers,
    sticker_asignaciones,
    planeacion_asignaciones,
    planeacion_cruce,
    survey_cali,
    usuarios,
    panel_representante,
    integracion,
)


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

    app = FastAPI(
        title=config.Settings().app_name,
        lifespan=_lifespan,
        # Keep the Authorize-button token across /docs reloads (the bearerAuth
        # scheme comes from auth/deps.py's HTTPBearer).
        swagger_ui_parameters={"persistAuthorization": True},
    )

    # Attached synchronously (not inside `_lifespan`) so router tests can
    # populate/replace it via a plain `TestClient(app)` — no need to enter
    # `lifespan` just to get a snapshot store to write to.
    app.state.reportados_snapshot = ReportadosSnapshot()

    # One TTL-cache instance per app, same synchronous-attach convention as
    # `reportados_snapshot` above (design.md ADR-4; see
    # `routers/sticker_status.py`'s module docstring for why this replaces
    # the legacy warm-lambda-only cache).
    app.state.sticker_status_cache = StickerStatusCache()

    # Same convention, `planeacion_asignaciones.py`'s own `resumen`/
    # `metricasProgreso` aggregate cache (speed follow-up, 2026-08-27).
    app.state.planeacion_aggregates_cache = PlaneacionAggregatesCache()

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
