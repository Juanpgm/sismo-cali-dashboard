"""Manual pin of which inspection represents a duplicated building.

The same building gets inspected more than once (re-visits, accidental
re-submits). `scripts/refresh_data.py`'s `add_dup_group()` groups those
submissions per building and marks ONE as `es_representante`, so the Panel's
figures count BUILDINGS instead of submissions — 1091 records describe 941
buildings, and counting submissions inflated every figure ~13.7%.

The automatic rule is "most recent inspection wins". This module is the
escape hatch for the cases it gets wrong: an operator pins a specific record
for a group and the Panel counts that one instead.

## Why pinning is admin-only but reading is not

A pin changes the headline numbers everybody reads, so it is an admin write.
Reading is open to any authenticated role, because every role can see the
Panel and must therefore see the same figures — a viewer whose KPIs silently
disagreed with an admin's would be worse than no override at all.

## Why the pin stores who and when

An override is a human overruling the data. `fijado_por`/`fijado_en` keep
that attributable — without them the figures could be changed by anyone with
no trace, which is exactly the property the raw data already had and that
this whole de-duplication effort exists to protect.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth.deps import current_claims, require_role
from app.credentials import clients as credentials

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

# Doc id = `dup_grupo_id` (the building), so a group can only ever carry ONE
# pin: re-pinning replaces, it never accumulates a pile of stale choices.
PANEL_REPRESENTANTE_COLLECTION = "panel_representante"

router = APIRouter()


class PinRequest(BaseModel):
    dup_grupo_id: str
    global_id: str


class UnpinRequest(BaseModel):
    dup_grupo_id: str


@router.get("/panel-representante")
def listar_representantes(
    claims: dict[str, Any] = Depends(current_claims),
) -> JSONResponse:
    """`{dup_grupo_id: GlobalID}` for every pinned group.

    Returned as a flat map because that is exactly the shape both consumers
    want: `add_dup_group(df, overrides=...)` in the pipeline, and the Panel's
    own client-side re-application (which lets a pin take effect immediately
    instead of waiting for the next 15-minute pipeline run).
    """
    db = credentials.sismo().firestore
    docs = db.collection(PANEL_REPRESENTANTE_COLLECTION).stream()
    representantes = {
        d.id: (d.to_dict() or {}).get("global_id")
        for d in docs
        if (d.to_dict() or {}).get("global_id")
    }
    return JSONResponse({"ok": True, "representantes": representantes})


@router.post("/panel-representante")
def fijar_representante(
    body: PinRequest,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    grupo = (body.dup_grupo_id or "").strip()
    global_id = (body.global_id or "").strip()
    if not grupo or not global_id:
        raise HTTPException(status_code=400, detail="dup_grupo_id y global_id son obligatorios.")

    db = credentials.sismo().firestore
    db.collection(PANEL_REPRESENTANTE_COLLECTION).document(grupo).set({
        "global_id": global_id,
        "fijado_por": claims.get("sub"),
        "fijado_en": datetime.now(timezone.utc).isoformat(),
    })
    return JSONResponse({"ok": True, "dup_grupo_id": grupo, "global_id": global_id})


@router.delete("/panel-representante")
def quitar_representante(
    body: UnpinRequest,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    """Remove a pin so the group falls back to the automatic rule.

    Deleting a pin can never leave a group unrepresented: `add_dup_group()`
    always elects one by recency when no pin applies.
    """
    grupo = (body.dup_grupo_id or "").strip()
    if not grupo:
        raise HTTPException(status_code=400, detail="dup_grupo_id es obligatorio.")

    db = credentials.sismo().firestore
    db.collection(PANEL_REPRESENTANTE_COLLECTION).document(grupo).delete()
    return JSONResponse({"ok": True, "dup_grupo_id": grupo})
