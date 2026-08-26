"""backend/tests/services/test_survey_cali.py (RED first, task 7.3) —
`survey_cali` mutation core (`apply_mutation`) + incremental ingestion
(`ingest_records`, `canonical_hash`, `diff_upstream_fields`). design.md
ADR-10/11/12; survey-cali-collection spec: "Unchanged record is skipped",
"Changed record is upserted by GlobalID", "A run never rewrites the full
collection", "Ingest update writes a pipeline-authored revision", "History
is never destroyed", "Manual edit survives an unrelated ingest run",
"Source move overwrites a manually-edited field, visibly".

MUTATION CORE ONLY — the CRUD/history/revert ROUTER (`routers/survey_cali.py`)
does NOT exist yet and is explicitly out of scope (slice 8b); nothing here
goes through FastAPI/`TestClient`, every case calls `app.services.survey_cali`
directly against an injected fake Firestore `db=`.

Fake Firestore (`_FakeDB`/`_FakeDocRef`/`_FakeCollection`/`_FakeTransaction`)
is a tiny in-memory, path-keyed store supporting exactly the shapes this
module's real-Firestore call sites use: `.collection(name).document(id)`,
`.document("a/b")` (the `_meta/...` singleton-doc idiom), doc refs'
`.get(transaction=)`/`.set(data, merge=)`/`.collection(subname)`
(subcollections), `db.get_all(refs, field_paths=)`, and `db.transaction()`
returning a `_is_test_double`-marked fake — `apply_mutation` detects that
marker and calls the mutation function directly instead of wrapping it in
the real SDK's `@firestore.transactional` decorator (which requires a live
gRPC-backed `Transaction`, confirmed by reading the installed
`google-cloud-firestore` SDK's `_Transactional._pre_commit`/`__call__` —
not fakeable without a real project, and no test in this repo does that for
ANY Firestore-touching module).
"""
from __future__ import annotations

from typing import Any

from app.services import survey_cali


# ── Fake Firestore (path-keyed, supports subcollections + transactions) ────


class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self, db: "_FakeDB", path: tuple[str, ...]) -> None:
        self._db = db
        self._path = path

    def get(self, transaction=None, field_paths=None) -> _FakeSnapshot:
        data = self._db.store.get(self._path)
        if field_paths is not None and data is not None:
            data = {k: data.get(k) for k in field_paths}
        return _FakeSnapshot(self._path[-1], data)

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        current = self._db.store.get(self._path)
        if merge and current is not None:
            merged = dict(current)
            merged.update(data)
            self._db.store[self._path] = merged
        else:
            self._db.store[self._path] = dict(data)

    def collection(self, name: str) -> "_FakeCollection":
        return _FakeCollection(self._db, self._path + (name,))


class _FakeCollection:
    def __init__(self, db: "_FakeDB", path: tuple[str, ...]) -> None:
        self._db = db
        self._path = path

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._db, self._path + (doc_id,))


class _FakeTransaction:
    _is_test_double = True

    def set(self, ref: _FakeDocRef, data: dict[str, Any], merge: bool = False) -> None:
        ref.set(data, merge=merge)


class _FakeDB:
    def __init__(self) -> None:
        self.store: dict[tuple[str, ...], dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self, (name,))

    def document(self, path_str: str) -> _FakeDocRef:
        return _FakeDocRef(self, tuple(path_str.split("/")))

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    def get_all(self, refs: list[_FakeDocRef], field_paths=None) -> list[_FakeSnapshot]:
        return [ref.get(field_paths=field_paths) for ref in refs]


def _history(db: _FakeDB, gid: str) -> dict[str, dict]:
    prefix = (survey_cali.SURVEY_CALI_COLLECTION, gid, survey_cali.HISTORY_SUBCOLLECTION)
    return {path[-1]: data for path, data in db.store.items() if path[:-1] == prefix}


def _doc(db: _FakeDB, gid: str) -> dict[str, Any] | None:
    return db.store.get((survey_cali.SURVEY_CALI_COLLECTION, gid))


# ── apply_mutation: first-run create ────────────────────────────────────────


def test_apply_mutation_first_run_writes_create_revision_with_full_record():
    db = _FakeDB()

    result = survey_cali.apply_mutation(
        "gid-1", {"direccion": "Calle 1", "notas": "primera"}, "pipeline", "ingest", db=db
    )

    assert result == {
        "rev": 1,
        "created": True,
        "changes": {
            "direccion": {"before": None, "after": "Calle 1"},
            "notas": {"before": None, "after": "primera"},
        },
        "written": True,
    }
    doc = _doc(db, "gid-1")
    assert doc["direccion"] == "Calle 1" and doc["notas"] == "primera"
    assert doc["_rev"] == 1 and doc["_updated_by"] == "pipeline"

    history = _history(db, "gid-1")
    assert set(history) == {"rev_000001"}
    rev1 = history["rev_000001"]
    assert rev1["kind"] == "create"  # forced, regardless of the 'ingest' kind passed in
    assert rev1["author"] == "pipeline"
    assert rev1["revert_of"] is None
    assert rev1["changes"] == {
        "direccion": {"before": None, "after": "Calle 1"},
        "notas": {"before": None, "after": "primera"},
    }


# ── apply_mutation: edit writes a new revision, prior history untouched ────


def test_apply_mutation_edit_writes_new_revision_history_never_destroyed():
    db = _FakeDB()
    survey_cali.apply_mutation("gid-1", {"notas": "orig"}, "pipeline", "ingest", db=db)

    result = survey_cali.apply_mutation("gid-1", {"notas": "editado"}, "uid-admin", "edit", db=db)

    assert result["rev"] == 2 and result["created"] is False
    assert result["changes"] == {"notas": {"before": "orig", "after": "editado"}}
    assert _doc(db, "gid-1")["notas"] == "editado"
    assert _doc(db, "gid-1")["_rev"] == 2 and _doc(db, "gid-1")["_updated_by"] == "uid-admin"

    history = _history(db, "gid-1")
    assert set(history) == {"rev_000001", "rev_000002"}  # rev 1 still present, unaltered
    assert history["rev_000001"]["changes"] == {"notas": {"before": None, "after": "orig"}}
    assert history["rev_000002"]["kind"] == "edit"
    assert history["rev_000002"]["author"] == "uid-admin"


# ── apply_mutation: no-op idempotency — zero writes, zero new revisions ────


def test_apply_mutation_same_changes_twice_is_a_pure_noop_second_time():
    db = _FakeDB()
    survey_cali.apply_mutation("gid-1", {"notas": "orig"}, "pipeline", "ingest", db=db)

    result = survey_cali.apply_mutation("gid-1", {"notas": "orig"}, "pipeline", "ingest", db=db)

    assert result == {"rev": 1, "created": False, "changes": {}, "written": False}
    assert _doc(db, "gid-1")["_rev"] == 1  # unchanged
    assert set(_history(db, "gid-1")) == {"rev_000001"}  # no second revision minted


def test_apply_mutation_metadata_only_change_does_not_touch_visible_changes_map():
    db = _FakeDB()
    survey_cali.apply_mutation(
        "gid-1", {"direccion": "Calle 1", "_source_hash": "h1"}, "pipeline", "ingest", db=db
    )

    result = survey_cali.apply_mutation(
        "gid-1", {"direccion": "Calle 2", "_source_hash": "h2"}, "pipeline", "ingest", db=db
    )

    assert result["changes"] == {"direccion": {"before": "Calle 1", "after": "Calle 2"}}
    assert _doc(db, "gid-1")["_source_hash"] == "h2"


# ── canonical_hash / diff_upstream_fields (pure, RAW-field scoped hash) ────


def test_canonical_hash_ignores_derived_fields():
    base = {"GlobalID": "g1", "direccion": "Calle 1", "comuna": "Comuna 3", "x": -76.5}
    only_derived_changed = {"GlobalID": "g1", "direccion": "Calle 1", "comuna": "Comuna 9", "x": -76.9}

    assert survey_cali.canonical_hash(base) == survey_cali.canonical_hash(only_derived_changed)


def test_canonical_hash_changes_when_a_raw_field_changes():
    base = {"GlobalID": "g1", "direccion": "Calle 1"}
    changed = {"GlobalID": "g1", "direccion": "Calle 2"}

    assert survey_cali.canonical_hash(base) != survey_cali.canonical_hash(changed)


def test_diff_upstream_fields_returns_only_fields_that_moved_since_last_ingest():
    record = {"GlobalID": "g1", "direccion": "Calle 2", "notas": "sin cambios"}
    source_shadow = {"direccion": "Calle 1", "notas": "sin cambios"}

    assert survey_cali.diff_upstream_fields(record, source_shadow) == {"direccion": "Calle 2"}


# ── ingest_records: skip / upsert / no full-collection rewrite ─────────────


def test_ingest_records_first_run_creates_every_record():
    db = _FakeDB()
    records = [
        {"GlobalID": "g1", "EditDate": "2026-08-01T00:00:00", "direccion": "Calle 1"},
        {"GlobalID": "g2", "EditDate": "2026-08-01T00:00:00", "direccion": "Calle 2"},
    ]

    summary = survey_cali.ingest_records(records, db=db)

    assert summary == {"created": 2, "updated": 0, "skipped": 0}
    assert _doc(db, "g1")["_source_hash"] == survey_cali.canonical_hash(records[0])
    assert _doc(db, "g2")["direccion"] == "Calle 2"


def test_ingest_records_unchanged_record_is_skipped_zero_writes():
    db = _FakeDB()
    record = {"GlobalID": "g1", "EditDate": "2026-08-01T00:00:00", "direccion": "Calle 1"}
    survey_cali.ingest_records([record], db=db)
    rev_after_first_run = _doc(db, "g1")["_rev"]

    summary = survey_cali.ingest_records([dict(record)], db=db)

    assert summary == {"created": 0, "updated": 0, "skipped": 1}
    assert _doc(db, "g1")["_rev"] == rev_after_first_run  # no new write at all
    assert set(_history(db, "g1")) == {"rev_000001"}  # no new revision


def test_ingest_records_never_rewrites_the_full_collection():
    """N records, fewer than N changed on the second run -> writes ==
    changed count, not N (survey-cali-collection spec)."""
    db = _FakeDB()
    records = [
        {"GlobalID": f"g{i}", "EditDate": "2026-08-01T00:00:00", "direccion": f"Calle {i}"}
        for i in range(5)
    ]
    survey_cali.ingest_records(records, db=db)

    records[2]["direccion"] = "Calle 2 (moved)"
    records[2]["EditDate"] = "2026-08-02T00:00:00"
    summary = survey_cali.ingest_records(records, db=db)

    assert summary == {"created": 0, "updated": 1, "skipped": 4}


def test_ingest_records_changed_record_is_upserted_not_duplicated():
    db = _FakeDB()
    record = {"GlobalID": "g1", "EditDate": "2026-08-01T00:00:00", "direccion": "Calle 1"}
    survey_cali.ingest_records([record], db=db)

    moved = {"GlobalID": "g1", "EditDate": "2026-08-02T00:00:00", "direccion": "Calle 1-moved"}
    survey_cali.ingest_records([moved], db=db)

    # Still exactly one doc at survey_cali/g1 -- no duplicate id.
    assert len([p for p in db.store if p[:1] == (survey_cali.SURVEY_CALI_COLLECTION,) and len(p) == 2]) == 1
    assert _doc(db, "g1")["direccion"] == "Calle 1-moved"
    assert _doc(db, "g1")["_rev"] == 2


# ── ingest update -> pipeline-authored revision ─────────────────────────────


def test_ingest_update_writes_pipeline_authored_revision():
    db = _FakeDB()
    record = {"GlobalID": "g1", "EditDate": "2026-08-01T00:00:00", "direccion": "Calle 1"}
    survey_cali.ingest_records([record], db=db)

    moved = {"GlobalID": "g1", "EditDate": "2026-08-02T00:00:00", "direccion": "Calle 1-moved"}
    survey_cali.ingest_records([moved], db=db)

    history = _history(db, "g1")
    assert history["rev_000002"]["author"] == "pipeline"
    assert history["rev_000002"]["kind"] == "ingest"
    assert history["rev_000002"]["changes"]["direccion"] == {"before": "Calle 1", "after": "Calle 1-moved"}


# ── per-field ingest-vs-manual-edit conflict rule (ADR-11) ──────────────────


def test_manual_edit_survives_an_unrelated_ingest_run():
    db = _FakeDB()
    record = {"GlobalID": "g1", "EditDate": "2026-08-01T00:00:00",
              "direccion": "Calle 1", "notas": "sin observaciones"}
    survey_cali.ingest_records([record], db=db)

    # Admin manually edits `notas` — NOT via ingest_records (this is the CRUD
    # path, out of scope, so we call apply_mutation directly like the future
    # router will).
    survey_cali.apply_mutation("g1", {"notas": "revisado en sitio"}, "uid-admin", "edit", db=db)

    # Upstream re-ingests the SAME record (notas untouched upstream, only
    # EditDate nominally advances) -- notas must survive.
    unrelated_reingest = {"GlobalID": "g1", "EditDate": "2026-08-03T00:00:00",
                          "direccion": "Calle 1", "notas": "sin observaciones"}
    summary = survey_cali.ingest_records([unrelated_reingest], db=db)

    assert summary == {"created": 0, "updated": 0, "skipped": 1}  # hash unchanged (raw fields same)
    assert _doc(db, "g1")["notas"] == "revisado en sitio"  # manual edit intact


def test_source_move_overwrites_manually_edited_field_visibly_and_revertibly():
    db = _FakeDB()
    record = {"GlobalID": "g1", "EditDate": "2026-08-01T00:00:00", "direccion": "Calle 1"}
    survey_cali.ingest_records([record], db=db)

    survey_cali.apply_mutation("g1", {"direccion": "Calle 1 (corregida a mano)"}, "uid-admin", "edit", db=db)
    assert _doc(db, "g1")["direccion"] == "Calle 1 (corregida a mano)"

    moved = {"GlobalID": "g1", "EditDate": "2026-08-05T00:00:00", "direccion": "Calle 99 (nueva fuente)"}
    summary = survey_cali.ingest_records([moved], db=db)

    assert summary == {"created": 0, "updated": 1, "skipped": 0}
    assert _doc(db, "g1")["direccion"] == "Calle 99 (nueva fuente)"  # source wins

    history = _history(db, "g1")
    overwrite_rev = history["rev_000003"]  # rev1=create, rev2=manual edit, rev3=ingest overwrite
    assert overwrite_rev["kind"] == "ingest" and overwrite_rev["author"] == "pipeline"
    # `before` is the MANUAL value, not the original upstream one -- visible + revertible.
    assert overwrite_rev["changes"]["direccion"] == {
        "before": "Calle 1 (corregida a mano)", "after": "Calle 99 (nueva fuente)"
    }
    # Nothing prior was deleted or altered -- revert-as-new-revision is still possible.
    assert set(history) == {"rev_000001", "rev_000002", "rev_000003"}
