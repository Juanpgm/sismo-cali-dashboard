"""`survey_cali` mutation core + incremental ingestion — design.md ADR-9
(sole-writer treatment, extended to this collection), ADR-10 (document +
history model), ADR-11 (incremental ingestion, slice 7b), ADR-12 (the
`apply_mutation` transaction semantics; the CRUD/history/revert ROUTER
itself, `routers/survey_cali.py`, is explicitly OUT OF SCOPE here — it
lands in slice 8b, task 8.x, per tasks.md's own instruction for 7.3-7.6).

One of the (eventually three) modules allowlisted for the `survey_cali`
literal under `tests/invariants/test_sole_writer.py` (ADR-9): this module,
`app/jobs/dashboard_refresh.py` (calls `ingest_records` after the
Survey123 fetch), and — once it exists in slice 8b — `routers/survey_cali.py`.

## `apply_mutation` — the single write path (ADR-12)

`apply_mutation(id, changes, author, kind, revert_of=None, *, db=None)` is
the ONLY function that ever writes to `survey_cali/{id}` or its
`history/{rev_NNNNNN}` subcollection. CRUD (slice 8b), revert (slice 8b),
and ingest (this slice, via `ingest_records`) all funnel through it — one
code path, one history shape, per ADR-12's explicit rejection of letting
the ingest job write Firestore directly.

Read-diff-write runs inside a Firestore transaction (read current doc,
compute the diff, write the effective fields + `_rev+1` + the history doc
atomically) using the SDK's documented `db.transaction()` +
`@firestore.transactional` pattern. Unit tests inject a lightweight fake
transaction (marked `_is_test_double = True`) instead of a live Firestore
project — real transactional retry/atomicity is trusted to the SDK
(exercised nowhere in this offline test suite, consistent with how
`credentials.sismo()` itself is never called against a real project in any
test in this repo), while the read-diff-write LOGIC (the actual behavior
every spec scenario cares about) is fully covered against the fake.

Metadata fields (any key starting with `_` — `_source`, `_source_hash`,
`_deleted`, ...) are written directly but never appear in a revision's
visible `changes` map — that map is reserved for record content, matching
every spec scenario's own language ("the changed field(s)").

## `ingest_records` — incremental ingestion (ADR-11)

Per-record: skip via the `_source_hash` content-hash gate (an `EditDate`
pre-filter short-circuits hashing entirely for records that plainly
haven't moved, per ADR-11 — CPU-only, never the sole write trigger); else
diff every field against `_source[field]` (NOT the effective field — this
is what lets a manual edit survive an unrelated ingest run while a real
upstream move still overwrites it, visibly and revertibly) and write only
the changed fields via `apply_mutation(..., author='pipeline',
kind='ingest')`.

### Design Interpretation — RAW-vs-computed hashing (open question 4)

ADR-11 recommends hashing RAW upstream fields only, "so pipeline-derived
enrichment never masks or fakes an upstream change" — confirmed as the
default here, via `DERIVED_FIELDS` below. One constraint changed HOW that
recommendation is satisfied: this batch's scope forbids touching anything
outside `backend/` (no edits to `scripts/refresh_data.py`) and forbids a
second Survey123 upstream call, while `scripts/refresh_data.py` itself
runs as an opaque `subprocess.run` from `app/jobs/dashboard_refresh.py`
(task 7.2) — its in-memory pre-normalize DataFrame is not reachable across
that process boundary without either edit. The only artifact economically
available, without violating either constraint, is
`web/data/inspections.json` — `refresh_data.py`'s ALREADY-NORMALIZED
output (comuna/barrio_geo spatial join, EXIF/geocode-corrected `x`/`y`,
`id_edan`, `direccion_norm`, `*_calc` fields, etc. all already applied).

So the canonical form hashed by `canonical_hash()` is `inspections.json`'s
per-record dict MINUS `DERIVED_FIELDS` (every field name confirmed, by
reading `scripts/refresh_data.py`'s `normalize()` pipeline, to be added by
`spatial_join`/`add_id_edan`/`add_address_norm`/`apply_photo_coords`/
`validate_photo_coords`/`resolve_barrio_vereda`/`add_suspension_servicios`/`add_date_fields` —
i.e. genuinely pipeline-computed, not passed through from the Survey123
layer). This is the closest achievable approximation to "RAW fields only"
within this batch's file-scope constraint: it still satisfies the
rationale (a re-geocode or a comuna-polygon update alone can never trip
the record-level hash gate), it just derives the RAW/derived split from
`inspections.json`'s field names rather than from a true pre-normalize
attribute dict. `diff_upstream_fields()` (the per-field ingest-vs-manual
write decision) is intentionally NOT restricted to this subset — every
field (including the derived ones) is still tracked in `_source` and can
still be written on ingest, so the dashboard's derived enrichment keeps
refreshing once a REAL change fires the hash gate; only the record-level
"should I even look at this record" decision is RAW-scoped.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Mapping

from app.credentials import clients as credentials

SURVEY_CALI_COLLECTION = "survey_cali"
HISTORY_SUBCOLLECTION = "history"
INGEST_STATE_DOC = "_meta/survey_cali_ingest_state"

BATCH_SIZE = 500  # Firestore batch-write/get_all chunk limit, cruce_sticker.py's house style

# Fields `scripts/refresh_data.py`'s `normalize()` pipeline COMPUTES (spatial
# join, EXIF/geocode coordinate correction, address normalization, derived
# id/date/triage fields) rather than passes through from the Survey123
# layer verbatim — excluded from `canonical_hash()`'s RAW-only gate (open
# question 4). See the module docstring's "Design Interpretation" above for
# how this set was derived and why it does NOT also restrict
# `diff_upstream_fields()`.
DERIVED_FIELDS = frozenset({
    "comuna", "barrio_geo",                      # spatial_join
    "barrio_vereda_resuelto", "barrio_vereda_fuente",  # resolve_barrio_vereda (geo-first "Barrio / vereda")
    "id_edan",                                    # add_id_edan
    "direccion_norm",                              # add_address_norm
    "x", "y", "coords_fuente", "coords_validacion", # apply_photo_coords / validate_photo_coords
    "dist_geocode_m", "geocode_lat", "geocode_lon", "gps_error_m", "n_fotos_gps",
    "suspension_servicios",                        # add_suspension_servicios
    "fecha_hora",                                   # add_date_fields
    "afectacion_planta_calc", "severidad_danos_calc", "habitabilidad_calc",  # derived triage calc
})

# Survey123 SYSTEM/audit fields — NOT pipeline-derived (they come straight
# off the layer), but also NOT content: `EditDate` in particular updates on
# ANY edit to the source record, including edits to fields this pipeline
# never syncs downstream. Folding it into the canonical hash would make
# "content hash primary, EditDate as pre-filter only" (ADR-11) self-
# defeating — an EditDate bump alone would always look like a content
# change. Excluded from `canonical_form()` for the same reason
# `DERIVED_FIELDS` is, kept as a separate constant because the REASON is
# different (source audit metadata, not pipeline enrichment).
SOURCE_SYSTEM_FIELDS = frozenset({"EditDate", "CreationDate", "Creator", "Editor", "ObjectID"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── apply_mutation — the sole write path (ADR-12) ───────────────────────────


def apply_mutation(id: str, changes: dict, author: str, kind: str,
                   revert_of: int | None = None, *, db=None) -> dict:
    """Read current doc -> diff -> write effective fields + `_rev+1` + a
    history doc, atomically. `changes` may mix record content fields
    (diffed against the current effective value, recorded in the
    revision's `changes` map) with underscore-prefixed metadata (`_source`,
    `_source_hash`, `_deleted`, ...) — written directly, diffed against the
    current doc too (so a genuinely no-op metadata write mints no
    revision), but never surfaced in `changes`.

    First-run (`id` doc doesn't exist yet): ALWAYS writes `kind:'create'`
    with `_rev:1`, regardless of the `kind` argument passed in — ADR-11's
    "missing doc -> kind:'create' revision with the full record" rule,
    enforced here so `ingest_records` doesn't need to know upfront whether
    a doc exists.

    No-op (every field in `changes` already matches the current doc):
    writes NOTHING — zero Firestore ops, no new revision. This is the
    idempotency guarantee: re-applying the same `changes` twice is a
    single write.

    Returns ``{"rev": int, "created": bool, "changes": {field: {before,
    after}}, "written": bool}``.
    """
    db = db or credentials.sismo().firestore
    doc_ref = db.collection(SURVEY_CALI_COLLECTION).document(id)

    def _mutate(transaction) -> dict:
        snapshot = doc_ref.get(transaction=transaction)
        current = snapshot.to_dict() if snapshot.exists else {}
        is_create = not snapshot.exists

        record_changes: dict = {}
        doc_write: dict = {}
        for field, new_value in changes.items():
            before = current.get(field)
            if not is_create and before == new_value:
                continue  # unchanged — not even written
            doc_write[field] = new_value
            if not field.startswith("_"):
                record_changes[field] = {"before": None if is_create else before, "after": new_value}

        if not is_create and not doc_write:
            return {"rev": int(current.get("_rev", 0)), "created": False, "changes": {}, "written": False}

        new_rev = 1 if is_create else int(current.get("_rev", 0)) + 1
        now = _now()
        doc_write["_rev"] = new_rev
        doc_write["_updated_at"] = now
        doc_write["_updated_by"] = author

        rev_id = f"rev_{new_rev:06d}"
        history_ref = doc_ref.collection(HISTORY_SUBCOLLECTION).document(rev_id)
        history_doc = {
            "rev": new_rev,
            "author": author,
            "at": now,
            "kind": "create" if is_create else kind,
            "changes": record_changes,
            "revert_of": revert_of,
        }

        transaction.set(doc_ref, doc_write, merge=True)
        transaction.set(history_ref, history_doc)

        return {"rev": new_rev, "created": is_create, "changes": record_changes, "written": True}

    transaction = db.transaction()
    if getattr(transaction, "_is_test_double", False):
        # Test fakes apply writes immediately (no begin/commit machinery to
        # emulate) — see module docstring: real atomicity is the SDK's job.
        return _mutate(transaction)
    from google.cloud import firestore as _fs  # deferred import, credentials/clients.py's own convention
    return _fs.transactional(_mutate)(transaction)


# ── canonical form / hash / per-field diff (ADR-11) ─────────────────────────


def canonical_form(record: Mapping[str, object]) -> dict:
    """RAW-only canonical form (open question 4 / DERIVED_FIELDS above),
    `GlobalID` excluded (it's the doc id, not a content field). String
    values are stripped so incidental whitespace differences across
    re-fetches don't churn the hash."""
    out: dict = {}
    for field, value in record.items():
        if field == "GlobalID" or field in DERIVED_FIELDS or field in SOURCE_SYSTEM_FIELDS:
            continue
        if isinstance(value, str):
            value = value.strip()
        out[field] = value
    return out


def canonical_hash(record: Mapping[str, object]) -> str:
    """SHA-256 of `canonical_form(record)`'s deterministic (sorted-key)
    JSON encoding — the record-level ingest short-circuit (ADR-11: "content
    hash primary")."""
    blob = json.dumps(canonical_form(record), sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def diff_upstream_fields(record: Mapping[str, object], source_shadow: Mapping[str, object]) -> dict:
    """Per-field ingest-vs-manual-edit rule (ADR-11): diff EVERY field of
    `record` (not just the RAW/hash subset — derived enrichment stays
    fresh too) against `source_shadow[field]` — the last-ingested upstream
    value, NOT the record's current effective value. This is what lets a
    manual edit survive an unrelated ingest run while a real upstream move
    still overwrites it. Returns only the fields whose upstream value
    actually changed."""
    changed: dict = {}
    for field, value in record.items():
        if field == "GlobalID":
            continue
        if isinstance(value, str):
            value = value.strip()
        if source_shadow.get(field) != value:
            changed[field] = value
    return changed


# ── ingest_records — orchestrates the per-run upsert (ADR-11) ──────────────


def _batched_read_source_state(db, ids: list[str]) -> dict:
    """{GlobalID: {'exists', '_source', '_source_hash'}} via one batched
    `get_all` per BATCH_SIZE-sized chunk, projected to just the two ingest
    metadata fields — cheap, mirrors `cruce_sticker.py`'s
    `read_tiene_sticker_state` precedent."""
    col = db.collection(SURVEY_CALI_COLLECTION)
    out: dict = {}
    for start in range(0, len(ids), BATCH_SIZE):
        chunk = ids[start:start + BATCH_SIZE]
        refs = [col.document(doc_id) for doc_id in chunk]
        for snap in db.get_all(refs, field_paths=["_source", "_source_hash"]):
            data = snap.to_dict() or {}
            out[snap.id] = {
                "exists": snap.exists,
                "_source": data.get("_source") or {},
                "_source_hash": data.get("_source_hash"),
            }
    return out


def _read_ingest_state(db) -> dict:
    doc = db.document(INGEST_STATE_DOC).get()
    return (doc.to_dict() or {}) if doc.exists else {}


def _write_ingest_state(db, state: dict) -> None:
    collection_name, doc_name = INGEST_STATE_DOC.split("/")
    db.collection(collection_name).document(doc_name).set(state, merge=True)


def ingest_records(records: list[dict], *, db=None, author: str = "pipeline") -> dict:
    """Per-record upsert into `survey_cali`, keyed by `GlobalID`
    (backend-platform/survey-cali-collection spec: "Changed record is
    upserted by GlobalID"; "A run never rewrites the full collection" — the
    number of `apply_mutation` calls below equals the number of changed
    records, never the input size). Batches the current-doc read via
    `_batched_read_source_state`; every write goes through
    `apply_mutation` (never a direct Firestore call) per ADR-12. Updates
    `_meta/survey_cali_ingest_state` (watermark/counts, observability only
    — never the correctness source; see ADR-11).

    Returns ``{"created": int, "updated": int, "skipped": int}``.
    """
    db = db or credentials.sismo().firestore
    ids = [r["GlobalID"] for r in records if r.get("GlobalID")]
    state_by_id = _batched_read_source_state(db, ids)
    max_edit_date = _read_ingest_state(db).get("max_edit_date")

    created = updated = skipped = 0
    new_max_edit_date = max_edit_date
    for record in records:
        gid = record.get("GlobalID")
        if not gid:
            continue
        edit_date = record.get("EditDate")
        state = state_by_id.get(gid, {"exists": False, "_source": {}, "_source_hash": None})

        # EditDate pre-filter: CPU-only shortcut (ADR-11 — EditDate is
        # unreliable at this source, so it may only SKIP work, never be the
        # sole trigger to write). Only ever applies to already-ingested
        # records — a brand-new record always falls through to the hash
        # gate (and from there, always writes: create).
        if state["exists"] and max_edit_date and edit_date and edit_date <= max_edit_date:
            skipped += 1
        else:
            new_hash = canonical_hash(record)
            if state["exists"] and new_hash == state["_source_hash"]:
                skipped += 1  # content hash unchanged — no write, no revision
            else:
                changed = diff_upstream_fields(record, state["_source"])
                if changed or not state["exists"]:
                    source_shadow = {k: v for k, v in record.items() if k != "GlobalID"}
                    result = apply_mutation(
                        gid,
                        {**changed, "_source": source_shadow, "_source_hash": new_hash},
                        author=author,
                        kind="ingest",
                        db=db,
                    )
                    if result["created"]:
                        created += 1
                    else:
                        updated += 1
                else:
                    skipped += 1

        if edit_date and (new_max_edit_date is None or edit_date > new_max_edit_date):
            new_max_edit_date = edit_date

    _write_ingest_state(db, {
        "last_run_at": _now(),
        "max_edit_date": new_max_edit_date,
        "created": created, "updated": updated, "skipped": skipped,
    })
    return {"created": created, "updated": updated, "skipped": skipped}
