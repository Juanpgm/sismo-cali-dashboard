"""GET /reportados — public route, serves from the in-process snapshot
(design.md ADR-5; backend-platform spec "Public route requires no token",
"reportados responds fast from snapshot", "Cache-Control headers
preserved").

No auth dependency: `web/js/data.js` fetches this fire-and-forget, exactly
like today's unauthenticated `api/reportados.js`. Response body is the
`atencionsismo.fetch_reportados()`/Blob-seed payload shape as-is —
byte-identical field names to the retired JS endpoint
(`por_estadoVerificacion`, `inmuebles`), since that is what `data.js`
already reads.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services import snapshot as snapshot_service

router = APIRouter()

# No named SA/S3 client dependency — reportados never touches
# Firestore/S3 (backend-platform spec: "A route cannot reach an
# undeclared client"). VISITADOS_API_PASS is a "plain secret" read
# directly by app/services/atencionsismo.py, not part of this union
# (design.md ADR-4 table).
REQUIRED_CLIENTS: tuple[str, ...] = ()

# Verbatim from api/reportados.js's `res.setHeader('Cache-Control', ...)`.
CACHE_CONTROL = "public, s-maxage=900, stale-while-revalidate=86400"


@router.get("/reportados")
async def get_reportados(request: Request) -> JSONResponse:
    snap: snapshot_service.ReportadosSnapshot = request.app.state.reportados_snapshot
    try:
        payload, age = snap.get()
    except (snapshot_service.SnapshotUnavailableError, snapshot_service.SnapshotStaleError) as exc:
        return JSONResponse(
            {"error": str(exc)},
            status_code=503,
            headers={"Retry-After": str(exc.retry_after)},
        )
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": CACHE_CONTROL,
            "X-Snapshot-Age": str(int(age)),
        },
    )
