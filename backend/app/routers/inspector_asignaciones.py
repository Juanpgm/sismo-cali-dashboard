"""POST /inspector-asignaciones — an INSPECTOR's own sticker assignments
(design.md ADR-3/ADR-9; backend-platform spec "Own-uid-scoped route rejects
cross-uid access", "sticker_matches And cuadrillas Sole-Writer Invariant";
field-form-session spec "Inspector Own-UID Scoping Preserved").

Ports `api/inspector-asignaciones.js`'s `misPuntos`/`marcarHecho` dispatch
verbatim. Unlike `sticker-asignaciones` (admin-only, ported in slice 8),
this endpoint authorizes ANY authenticated user (`Depends(require_auth)`)
and scopes EVERY `sticker_matches` read/write to the caller's OWN uid
(`inspector_uid == token.sub`) — an inspector can only ever see or touch
the points assigned to them. `sticker_matches` has no Firestore security
rules (ADR-9), so this scoping is the ONLY thing standing between one
inspector's data and another's; there is no rules-layer backstop.

FIRST of the three modules allowlisted for the `sticker_matches`/
`cuadrillas` literal under `tests/invariants/test_sole_writer.py` (ADR-9).
The admin `sticker-asignaciones` router (slice 8) and the `cruce_sticker`
job (slice 7) are the other two — do not anticipate their allowlist entries
here.

Route path deliberately has NO `/api` prefix (unlike `/api/sign`), matching
the field-form-session/backend-platform spec deltas' own scenario text
(`/inspector-asignaciones`, not `/api/inspector-asignaciones`) and the
`reportados`/`sticker-status`/`source-status` precedent — see task 5.5's
BLOCKED note in apply-progress.md for what this means for the eventual
`formulario/js/form.js` repoint (a path change, not just a host flip).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.deps import require_auth
from app.credentials import clients as credentials

# sismo() is already unconditionally in credentials.WEB_STARTUP_CLIENTS, but
# this router still declares it per ADR-4's declaration mechanism — the
# invariant a router only reaches clients it names.
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

STICKER_MATCHES_COLLECTION = "sticker_matches"
DONE_ESTADO = "hecho"

router = APIRouter()


class AsignacionesRequest(BaseModel):
    action: str
    punto_id: str | None = None


def _pendiente(data: dict[str, Any]) -> bool:
    """An assignment is still "pending" (should show in the picker) when
    it is not yet marked done. Verbatim port of
    `api/inspector-asignaciones.js`'s `pendiente()`."""
    return data.get("estado_asignacion") != DONE_ESTADO


def _mis_puntos(db: Any, uid: str) -> list[dict[str, Any]]:
    """Every `sticker_matches` doc whose `inspector_uid == uid`, filtered to
    still-pending ones. Verbatim port of `api/inspector-asignaciones.js`'s
    `misPuntos()`."""
    docs = db.collection(STICKER_MATCHES_COLLECTION).where(
        "inspector_uid", "==", uid
    ).get()
    puntos: list[dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict() or {}
        if not _pendiente(data):
            continue
        puntos.append(
            {
                "id": doc.id,
                "direccion": data.get("direccion") or "",
                "zona_id": data.get("zona_id") or "",
                "coords": data.get("coords"),
                "criterio_habitabilidad": data.get("criterio_habitabilidad"),
                "colapso": data.get("colapso") or "no",
                "estado_asignacion": data.get("estado_asignacion") or "pendiente",
            }
        )
    return puntos


def _marcar_hecho(db: Any, uid: str, punto_id: str) -> dict[str, Any]:
    """Flip one `sticker_matches` doc to `hecho`, IFF it belongs to `uid`.
    Verbatim port of `api/inspector-asignaciones.js`'s `marcarHecho()` —
    the own-uid check (`snap.data().inspector_uid !== uid` -> 403) is the
    entire cross-inspector rejection boundary this router exists for."""
    if not punto_id:
        raise HTTPException(status_code=400, detail="Falta el id del punto.")
    ref = db.collection(STICKER_MATCHES_COLLECTION).document(punto_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="El punto no existe.")
    data = snap.to_dict() or {}
    if data.get("inspector_uid") != uid:
        raise HTTPException(
            status_code=403, detail="Ese punto no está asignado a este inspector."
        )
    ref.set({"estado_asignacion": DONE_ESTADO}, merge=True)
    return {"id": punto_id, "estado_asignacion": DONE_ESTADO}


@router.post("/inspector-asignaciones")
def inspector_asignaciones(
    body: AsignacionesRequest,
    claims: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    uid = claims.get("sub") or claims.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Token sin identificador de usuario.")

    db = credentials.sismo().firestore
    if body.action == "misPuntos":
        return {"ok": True, "puntos": _mis_puntos(db, uid)}
    if body.action == "marcarHecho":
        result = _marcar_hecho(db, uid, str(body.punto_id or ""))
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail="Acción no reconocida.")
