"""GET/POST /survey-cali, GET/PATCH/DELETE /survey-cali/{id},
GET /survey-cali/{id}/history, POST /survey-cali/{id}/revert — admin-gated
CRUD + append-only revision history + revert (slice 8b/8c). design.md
ADR-9 (sole-writer extension, final closure)/ADR-10 (document + history
model)/ADR-12 (this router); survey-cali-collection spec (all requirements,
route layer).

Third and FINAL module allowlisted for the `survey_cali` literal under
`tests/invariants/test_sole_writer.py`'s independent `survey_cali` check
(ADR-9), closing the set opened by `services/survey_cali.py` (task 7.4) and
`app/jobs/dashboard_refresh.py` (task 7.5): `services/survey_cali.py`,
`routers/survey_cali.py` (this file), `app/jobs/dashboard_refresh.py`
(task 8.11 finalizes the allowlist).

**Single mutation core (ADR-12)**: every WRITE below — create, patch,
delete, revert — funnels through `services.survey_cali.apply_mutation`.
This router never calls `.set()`/`.update()` on this collection directly;
it only performs direct Firestore READS (list, get-by-id, history) — same
"reads are fine, only writes are sole-writer-gated" precedent
`routers/sticker_status.py` established for its own read-only collection
access.

## Route shape — design interpretation (no `/api` prefix), flagged for verify

design.md's ADR-12 table writes these routes as `/api/survey-cali...`, but
tasks.md's own task 8.10 text — the actual scoped instruction for this
router — lists them WITHOUT an `/api` prefix: `GET/POST /survey-cali`,
`.../{id}`, `.../{id}/history`, `.../{id}/revert`. This matches every other
NEW-shape admin router this change has mounted so far (`/stickers`,
`/sticker-asignaciones`, `/usuarios`, `/refresh`, `/sticker-status`,
`/source-status`) — only `/api/sign` keeps an `/api` prefix, and that is a
literal parity requirement for an existing consumer. `survey_cali` has NO
legacy consumer to stay parity with (ADR-12 itself: "new capability, no
legacy parity constraint"), so the established no-`/api`-prefix convention
for every other new-shape router in this backend is followed here; ADR-12's
table is treated as slightly stale on this one cosmetic point.

## Validation boundary: underscore-prefixed metadata is rejected BY THE
SCHEMA (ADR-12), not by a manual post-parse check — clients can never write
metadata (`_rev`, `_updated_at`, `_updated_by`, `_source`, `_source_hash`,
`_deleted`, ...) through any of these routes. `_reject_metadata_keys` runs
as a Pydantic validator on the raw JSON body (a `BeforeValidator` for
PATCH's plain dict body, a `model_validator(mode="before")` for POST's
`id`-plus-content body) — a metadata key produces a genuine 422
(`RequestValidationError`), not a hand-rolled 400.

## No-op mutations are idempotent, not errors (ADR-12/ADR-10)

A PATCH whose values already match the current doc, or a DELETE on an
already-soft-deleted doc, is a successful no-op: 200, zero Firestore
writes, zero new revisions — `apply_mutation`'s own no-op guarantee
(task 7.4) surfaces here unchanged, not re-implemented.

## History page size — design open question 5, resolved here

Neither design.md nor tasks.md specifies a default beyond "pick one and
document it." Chosen: **50** revisions per page (`limit` query param,
default 50, capped at 200), newest-first by the revision's own `rev`
integer (ADR-10: `rev_NNNNNN` zero-padded ids already sort lexically in
revision order — sorting by the `rev` field directly avoids depending on a
Firestore `order_by` capability the offline test fakes don't implement).
Most records will carry single-digit-to-low-tens of revisions; 50
comfortably covers the overwhelming majority in a single page.

## GET-by-id / list treat soft-deleted docs as not-found — design
interpretation, flagged for verify

Spec/ADR-10 explicitly require the LIST endpoint to exclude `_deleted`
docs; they don't say what the single-doc GET should do for one. Treating a
soft-deleted doc as 404 on `GET /survey-cali/{id}` too (not just the list)
was chosen for consistency with "the dashboard read model never surfaces a
deleted record" — the full doc (incl. `_deleted:true`) and its history stay
reachable only via `GET .../{id}/history`, never via the current-state
read path, matching ADR-10's "Default Read Path Returns Current State
Only" requirement's spirit for BOTH read routes, not just the list one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

from app.auth.deps import require_role
from app.credentials import clients as credentials
from app.services import survey_cali

# `sismo` is already unconditionally in credentials.WEB_STARTUP_CLIENTS, but
# this router still declares it per ADR-4's declaration mechanism (same
# precedent every other admin router sets).
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

HISTORY_PAGE_SIZE_DEFAULT = 50
HISTORY_PAGE_SIZE_MAX = 200

router = APIRouter()


# ---- Validation boundary: reject client-supplied metadata keys ------------


def _reject_metadata_keys(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("El cuerpo debe ser un objeto JSON.")
    bad = sorted(k for k in value if isinstance(k, str) and k.startswith("_"))
    if bad:
        raise ValueError(f"No se pueden escribir campos de metadata desde el cliente: {bad}")
    return value


RecordChanges = Annotated[dict[str, Any], BeforeValidator(_reject_metadata_keys), Body(...)]


class SurveyCaliCreateBody(BaseModel):
    """POST body: `id` (the GlobalID / Firestore doc id) plus arbitrary
    content fields — `survey_cali` records have no fixed field set
    (Survey123-sourced columns), so extra fields are allowed wholesale
    rather than declared one by one. Underscore-prefixed metadata is
    rejected the same way PATCH's plain-dict body is."""

    model_config = ConfigDict(extra="allow")

    id: str

    @model_validator(mode="before")
    @classmethod
    def _reject_metadata(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _reject_metadata_keys({k: v for k, v in data.items() if k != "id"})
        return data

    def content_fields(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump(exclude_unset=True).items() if k != "id"}


class RevertBody(BaseModel):
    rev: int


# ---- Serialization helpers --------------------------------------------


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    """Firestore Timestamps auto-convert to Python `datetime` on `to_dict()`
    (no `.toDate()` to duck-type against, unlike the legacy JS SDK) — same
    manual `.isoformat()` convention `routers/stickers.py` established for
    `evaluaciones`' timestamp field."""
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in data.items()}


def _serialize_doc(doc_id: str, data: dict[str, Any]) -> dict[str, Any]:
    out = _jsonable(data)
    out["id"] = doc_id
    return out


def _serialize_revision(rev_id: str, data: dict[str, Any]) -> dict[str, Any]:
    out = _jsonable(data)
    out["id"] = rev_id
    return out


# ---- Direct Firestore reads (never writes — ADR-9/ADR-12) -----------------


def _doc_ref(db: Any, doc_id: str):
    return db.collection(survey_cali.SURVEY_CALI_COLLECTION).document(doc_id)


def _get_current(db: Any, doc_id: str) -> dict[str, Any] | None:
    snap = _doc_ref(db, doc_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    if data.get("_deleted"):
        return None
    return data


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Registro no encontrado.")


def _read_history(db: Any, doc_id: str) -> list[dict[str, Any]]:
    snaps = _doc_ref(db, doc_id).collection(survey_cali.HISTORY_SUBCOLLECTION).get()
    return [_serialize_revision(snap.id, snap.to_dict() or {}) for snap in snaps]


def _reconstruct_state_at(revisions: list[dict[str, Any]], target_rev: int) -> dict[str, Any]:
    """ADR-10: "value of field at rev N is derivable (the `after` of the
    last revision <= N touching it; `create` seeds every field)." Replays
    every revision up to and including `target_rev`, ascending, applying
    each `changes[field].after` — reconstructs the effective record state
    AT that revision. `apply_mutation` then diffs this against the CURRENT
    doc to compute the revert's own `changes` map, so reverting to a state
    identical to the current doc is correctly a no-op (zero-diff), never a
    spurious revision."""
    state: dict[str, Any] = {}
    for rev in sorted((r for r in revisions if r.get("rev", 0) <= target_rev), key=lambda r: r.get("rev", 0)):
        for field, delta in (rev.get("changes") or {}).items():
            state[field] = delta.get("after")
    return state


# ---- Routes -----------------------------------------------------------


@router.get("/survey-cali")
def list_survey_cali(claims: dict[str, Any] = Depends(require_role("admin"))) -> JSONResponse:
    """Dashboard read model: current docs only, `_deleted` excluded, no
    embedded history array (ADR-10 "Default Read Path Returns Current
    State Only")."""
    db = credentials.sismo().firestore
    records = []
    for snap in db.collection(survey_cali.SURVEY_CALI_COLLECTION).get():
        data = snap.to_dict() or {}
        if data.get("_deleted"):
            continue
        records.append(_serialize_doc(snap.id, data))
    return JSONResponse({"ok": True, "records": records})


@router.post("/survey-cali", status_code=201)
def create_survey_cali(
    body: SurveyCaliCreateBody,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    """Fails if `id` already exists (ADR-12) — checked BEFORE calling
    `apply_mutation`, since that function's own first-run detection would
    otherwise silently treat a pre-existing id as a `create` too."""
    db = credentials.sismo().firestore
    if _doc_ref(db, body.id).get().exists:
        raise HTTPException(status_code=409, detail="Ya existe un registro con este id.")
    uid = str(claims.get("sub") or "")
    result = survey_cali.apply_mutation(body.id, body.content_fields(), uid, "create", db=db)
    doc = _get_current(db, body.id) or {}
    return JSONResponse(
        {"ok": True, "rev": result["rev"], "record": _serialize_doc(body.id, doc)}, status_code=201
    )


@router.get("/survey-cali/{id}")
def get_survey_cali(id: str, claims: dict[str, Any] = Depends(require_role("admin"))) -> JSONResponse:
    db = credentials.sismo().firestore
    data = _get_current(db, id)
    if data is None:
        raise _not_found()
    return JSONResponse({"ok": True, "record": _serialize_doc(id, data)})


@router.patch("/survey-cali/{id}")
def patch_survey_cali(
    id: str,
    changes: RecordChanges,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    """Merge-only: `changes` carries ONLY the provided fields; `apply_mutation`
    writes them with `merge=True`, so every untouched field survives
    (survey-cali-collection spec: "Update is a merge, not a replace")."""
    db = credentials.sismo().firestore
    if _get_current(db, id) is None:
        raise _not_found()
    uid = str(claims.get("sub") or "")
    result = survey_cali.apply_mutation(id, changes, uid, "edit", db=db)
    doc = _get_current(db, id) or {}
    return JSONResponse({"ok": True, "rev": result["rev"], "record": _serialize_doc(id, doc)})


@router.delete("/survey-cali/{id}")
def delete_survey_cali(id: str, claims: dict[str, Any] = Depends(require_role("admin"))) -> JSONResponse:
    """Soft delete (`_deleted:true`) via `apply_mutation` — ADR-10: hard
    deletes are incompatible with append-only history."""
    db = credentials.sismo().firestore
    if _get_current(db, id) is None:
        raise _not_found()
    uid = str(claims.get("sub") or "")
    result = survey_cali.apply_mutation(id, {"_deleted": True}, uid, "delete", db=db)
    return JSONResponse({"ok": True, "rev": result["rev"], "id": id})


@router.get("/survey-cali/{id}/history")
def get_survey_cali_history(
    id: str,
    limit: int = Query(default=HISTORY_PAGE_SIZE_DEFAULT, ge=1, le=HISTORY_PAGE_SIZE_MAX),
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    """All revisions, newest-first (survey-cali-collection spec: "Listing
    history returns all revisions in order"). `_get_current`'s _deleted
    filter does NOT apply here — history for a soft-deleted record must
    stay reachable (ADR-10: "nothing is ever destroyed")."""
    db = credentials.sismo().firestore
    revisions = _read_history(db, id)
    revisions.sort(key=lambda r: r.get("rev", 0), reverse=True)
    return JSONResponse({"ok": True, "revisions": revisions[:limit]})


@router.post("/survey-cali/{id}/revert")
def revert_survey_cali(
    id: str,
    body: RevertBody,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    """Revert-as-new-revision (ADR-10): reconstructs the record's state AT
    `body.rev`, then writes it via `apply_mutation(..., kind='revert',
    revert_of=body.rev)` — a NEW revision is appended, R1..R(current) stay
    untouched."""
    db = credentials.sismo().firestore
    history = _read_history(db, id)
    if not any(r.get("rev") == body.rev for r in history):
        raise HTTPException(status_code=404, detail="Revisión no encontrada.")
    target_state = _reconstruct_state_at(history, body.rev)
    uid = str(claims.get("sub") or "")
    result = survey_cali.apply_mutation(id, target_state, uid, "revert", revert_of=body.rev, db=db)
    doc = _get_current(db, id) or {}
    return JSONResponse({"ok": True, "rev": result["rev"], "record": _serialize_doc(id, doc)})
