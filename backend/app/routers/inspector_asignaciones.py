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

## `planeacion-asignaciones` follow-up batch (2026-08-26)

The Planeación feature let an ADMIN assign EDAN-survey points to an
inspector (`routers/planeacion_asignaciones.py`, `Depends(require_role(
"admin"))`), but shipped with no way for the ASSIGNEE to ever see them —
`formulario/` had zero references to planeación and the admin router is
403 to anyone who isn't an admin. This module is the correct place to close
that gap: it is ALREADY the own-uid-scoped, any-authenticated surface an
inspector talks to, so adding `misPuntosPlaneacion`/`marcarHechoPlaneacion`
here means no new auth path to get wrong — same `Depends(require_auth)`,
same `inspector_uid == token.sub` scoping the two sticker actions above
already enforce, applied to `planeacion_puntos` instead of
`sticker_matches`. `misPuntosPlaneacion` also builds each point's prefilled
Survey123 URL via the SAME `app/services/survey_link.py:build_survey_urls()`
the admin router's `getEnlaceSurvey` already uses — no duplicated URL logic.

This module is the THIRD allowlisted reader/writer of the `planeacion_puntos`
literal under `tests/invariants/test_sole_writer.py`'s CLOSED
`ALLOWED_MODULES_PLANEACION_PUNTOS` set (the other two are
`app/jobs/planeacion_cruce.py`, pipeline-owned fields, and
`app/routers/planeacion_asignaciones.py`, admin-owned fields). The honest
entry is annotated there rather than obfuscating the literal to dodge the
scan — this module's own access is a THIRD, genuinely different case from
either of those: it is neither the pipeline nor the admin dashboard, it is
the assignee reading/closing only their OWN points, gated the same way
`sticker_matches` already is above.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.deps import require_auth
from app.config import Settings
from app.credentials import clients as credentials
from app.services.survey_link import build_survey_urls

# sismo() is already unconditionally in credentials.WEB_STARTUP_CLIENTS, but
# this router still declares it per ADR-4's declaration mechanism — the
# invariant a router only reaches clients it names.
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

STICKER_MATCHES_COLLECTION = "sticker_matches"
# `planeacion-asignaciones` follow-up batch. Own-uid-scoped ONLY — this
# module never reads/writes another inspector's `planeacion_puntos` doc,
# and never touches the pipeline-owned or admin-owned fields
# (`clave_integracion`, `tiene_survey`, `match_via`, `cuadrilla_id`, ...);
# it only ever reads a point and, on marcarHechoPlaneacion, writes exactly
# one field (`estado_asignacion`), mirroring `_marcar_hecho` above.
PLANEACION_PUNTOS_COLLECTION = "planeacion_puntos"
DONE_ESTADO = "hecho"
NO_APLICA_ESTADO = "no_aplica"

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


def _pendiente_planeacion(data: dict[str, Any]) -> bool:
    """A planeación point is still "pending" (should show in the picker)
    when it is neither `hecho` NOR `no_aplica` — mirrors `_pendiente`'s
    single-terminal-state shape, extended by one state because
    `planeacion_puntos` (unlike `sticker_matches`) has an explicit operator
    exclusion an inspector must never be sent to survey."""
    estado = data.get("estado_asignacion")
    return estado != DONE_ESTADO and estado != NO_APLICA_ESTADO


def _mis_puntos_planeacion(db: Any, uid: str) -> list[dict[str, Any]]:
    """Every `planeacion_puntos` doc whose `inspector_uid == uid`, filtered
    to still-pending ones, each carrying its prefilled Survey123 links.
    Structural port of `_mis_puntos` above: single equality-field Firestore
    query (no composite index needed), remaining filters applied in code."""
    docs = db.collection(PLANEACION_PUNTOS_COLLECTION).where(
        "inspector_uid", "==", uid
    ).get()

    settings = Settings()
    form_url = settings.survey123_form_url
    field_app_item_id = settings.survey123_field_app_item_id or None

    puntos: list[dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict() or {}
        if not _pendiente_planeacion(data):
            continue
        clave = data.get("clave_integracion")
        # Fail OPEN, not loud: unlike getEnlaceSurvey's single-item 503,
        # this is a LIST action — one missing env var or one point without
        # a minted key yet must never blank the whole picker, only that
        # point's own link fields.
        if clave and form_url:
            urls = build_survey_urls(
                clave, form_url=form_url, field_app_item_id=field_app_item_id
            )
        else:
            urls = {"web": None, "app": None}
        puntos.append(
            {
                "id": doc.id,
                "clave_integracion": clave,
                "direccion": data.get("direccion") or "",
                "coords": data.get("coords"),
                "comuna": data.get("comuna"),
                "afectacion": data.get("afectacion"),
                "prioridad": data.get("prioridad"),
                "estado_asignacion": data.get("estado_asignacion") or "pendiente",
                "survey_web": urls["web"],
                "survey_app": urls["app"],
            }
        )
    return puntos


def _marcar_hecho_planeacion(db: Any, uid: str, punto_id: str) -> dict[str, Any]:
    """Flip one `planeacion_puntos` doc to `hecho`, IFF it belongs to `uid`.
    Own-uid guard copied verbatim (shape, not text) from `_marcar_hecho`
    above — reject with NO write on a cross-inspector attempt."""
    if not punto_id:
        raise HTTPException(status_code=400, detail="Falta el id del punto.")
    ref = db.collection(PLANEACION_PUNTOS_COLLECTION).document(punto_id)
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
    if body.action == "misPuntosPlaneacion":
        return {"ok": True, "puntos": _mis_puntos_planeacion(db, uid)}
    if body.action == "marcarHechoPlaneacion":
        result = _marcar_hecho_planeacion(db, uid, str(body.punto_id or ""))
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail="Acción no reconocida.")
