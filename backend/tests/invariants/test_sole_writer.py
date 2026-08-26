"""tests/invariants/test_sole_writer.py — design.md ADR-9: `sticker_matches`
and `cuadrillas` have NO Firestore security rules, so "the backend is sole
writer, and only from these modules" must hold by construction, not by
policy. Slice 5 (`routers/inspector_asignaciones.py`) introduced this
literal under the invariant. Slice 7 (task 7.9) added the THIRD WRITE
module, `app/jobs/cruce_sticker.py` (+ its copied pipeline module,
`app/integracion/cruce_gestor.py` — see below). Slice 8 (task 8.4) adds the
FOURTH and FINAL WRITE module, `routers/sticker_asignaciones.py` — the
`sticker_matches`/`cuadrillas` allowlist is now CLOSED; no further module is
named by ADR-9 to join it. `cuadrillas` gets its first real hit this slice
(`inspector-asignaciones.js` never touches `cuadrillas` — only
`sticker-asignaciones.js` does), so `test_cuadrillas_literal_appears_only_in_allowlisted_modules`
now also asserts a non-empty hit set, mirroring the `sticker_matches` test.

**Slice 7b (task 7.6)** adds a SECOND, INDEPENDENT literal check for
`survey_cali` — ADR-9's "same treatment" extension for that collection.
Its declared writer set was `services/survey_cali.py` (the sole module that
touches Firestore for this collection) and `app/jobs/dashboard_refresh.py`
(calls INTO `services/survey_cali.py`'s `ingest_records`/`apply_mutation`,
never Firestore directly — but its source text still contains the literal,
via `from app.services import survey_cali` + `survey_cali.ingest_records(...)`,
which is exactly what this scan is built to catch either way).

**Slice 8c (task 8.11)** closes this set: `routers/survey_cali.py` (task
8.10's CRUD/history/revert router) joins as the THIRD and FINAL write
module — every mutation it performs (create/patch/delete/revert) funnels
through `services/survey_cali.apply_mutation`, never a direct Firestore
write, same "funnel through the single mutation core" discipline
`app/jobs/dashboard_refresh.py` already follows. The `survey_cali`
allowlist is now CLOSED — no further module is named by ADR-9 to join it.

**Slice 7 finding**: `app/integracion/cruce_gestor.py` (the copied pipeline
module `cruce_sticker.py` imports its matching cascade from) does NOT
itself reference the `sticker_matches`/`cuadrillas` literals anywhere — its
own Firestore-shaped I/O is the unrelated "Gestor de Zonas" Apps Script API
(`fetch_gestor`/`build_cruce`, dead code in this context, see its module
docstring), not Firestore at all. So only `app/jobs/cruce_sticker.py` itself
needs an allowlist entry, not `cruce_gestor.py` too — verified by scanning
(this test's own `_files_containing()` over `backend/app/` would have
caught a `cruce_gestor.py` hit and failed otherwise).

**Merge-reconciliation finding (2026-08-26)**: `routers/sticker_status.py`
(slice 4, branched independently and merged separately from slice 5 — this
invariant test did not exist yet on slice 4's branch, so its own apply
batch could not have caught this) reads the whole `sticker_matches`
collection (`db.collection("sticker_matches").get()`) to compute a
per-status tally, mirroring `api/sticker-status.js`'s legacy behavior
exactly. This is READ-ONLY — `sticker_status.py` never calls
`.set()`/`.update()`/`.document(...).set()` on this collection — so it does
not violate the "sole WRITER" property ADR-9 protects (no Firestore rules
means an uncontrolled WRITE could corrupt data with no server-side guard;
an uncontrolled READ cannot). Added as a fourth, read-only-flagged entry in
`ALLOWED_MODULES` rather than silently failing the invariant or refactoring
`sticker_status.py` to route through one of the three write-allowlisted
modules for a read it doesn't need write access for.

Scans every `.py` file under `backend/app/` for the literal collection
names and asserts they appear ONLY inside the allowlisted modules. A new
write (or read) path referencing either collection cannot merge without
someone deliberately editing ALLOWED_MODULES here — that edit is the
review tripwire ADR-9 describes.

Sequencing note (honesty, matching the pattern batches 1b/3 used for
1.13/3.5): this file was written and confirmed RED *before*
`routers/inspector_asignaciones.py` (task 5.2) existed — at that point zero
`.py` files under `backend/app/` contained the literal `sticker_matches` at
all, so `test_sticker_matches_literal_is_used_by_an_allowlisted_module`
failed (0 hits, assertion requires >=1). This is a genuine RED-before-GREEN
cycle, not the no-implementation-gap-left situation 1.13/3.5 hit — see
apply-progress.md's TDD Cycle Evidence table for the exact RED/GREEN
command+output pair.
"""
from __future__ import annotations

from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

# Slice 5: inspector_asignaciones.py (own-uid WRITE, per ADR-9's original
# three-module list). Slice 4's sticker_status.py joined READ-ONLY at merge
# reconciliation (see module docstring above) — it must never gain a write
# call to this collection without a corresponding ADR-9 amendment. Slice 7
# (task 7.9): app/jobs/cruce_sticker.py joins as the third WRITE module
# (pipeline-owned fields, merge:true only — see its module docstring).
# Slice 8 (task 8.4): routers/sticker_asignaciones.py joins as the FOURTH
# and FINAL WRITE module (admin fields) — this set is now CLOSED per ADR-9.
ALLOWED_MODULES = {
    APP_ROOT / "routers" / "inspector_asignaciones.py",
    APP_ROOT / "routers" / "sticker_status.py",  # read-only, see docstring
    APP_ROOT / "jobs" / "cruce_sticker.py",
    APP_ROOT / "routers" / "sticker_asignaciones.py",
}

# Slice 7b (task 7.6) opened `survey_cali`'s OWN allowlist — INDEPENDENT of
# ALLOWED_MODULES above (different collection, different ADR-9 clause).
# Slice 8c (task 8.11) adds `routers/survey_cali.py` as the THIRD and FINAL
# write module, per ADR-9's own naming and task 7.6's "do not anticipate it
# here" note (now resolved — the router exists). This set is now CLOSED.
ALLOWED_MODULES_SURVEY_CALI = {
    APP_ROOT / "services" / "survey_cali.py",
    APP_ROOT / "jobs" / "dashboard_refresh.py",
    APP_ROOT / "routers" / "survey_cali.py",
    # Plain docstring mention ("... survey_cali.py -- land in their own
    # migration slices") in the package's module docstring -- no Firestore
    # access, verified by reading the file in full. Allowlisted rather than
    # scrubbed: the scan's job is to catch WRITE paths, and a doc comment
    # naming a sibling module isn't one (same "verified harmless" precedent
    # 7.9's docstring note used for app/integracion/cruce_gestor.py, which
    # needed no entry at all because it had zero hits; this one needs an
    # entry because it genuinely contains the substring).
    APP_ROOT / "services" / "__init__.py",
    # Slice 8c finding: `app/main.py` imports the `survey_cali` router
    # module by name (`from app.routers import (..., survey_cali, ...)`)
    # and mounts it in `_ROUTERS`, so its source text genuinely contains
    # the literal too -- an inherent consequence of the router module
    # sharing its name with the Firestore collection, unlike every other
    # router (`stickers.py`, `usuarios.py`, ...) whose module name never
    # happens to match a scanned collection literal. Verified harmless by
    # reading `main.py` in full: import + `app.include_router(...)` only,
    # zero Firestore access -- same "import/mount reference, not a write
    # path" reasoning as the `services/__init__.py` entry above.
    APP_ROOT / "main.py",
}


def _files_containing(literal: str) -> set[Path]:
    hits: set[Path] = set()
    for path in APP_ROOT.rglob("*.py"):
        if literal in path.read_text(encoding="utf-8"):
            hits.add(path)
    return hits


def test_sticker_matches_literal_is_used_by_an_allowlisted_module():
    hits = _files_containing("sticker_matches")
    unexpected = hits - ALLOWED_MODULES
    assert not unexpected, f"unexpected sticker_matches reference(s): {sorted(unexpected)}"
    assert hits, "expected sticker_matches to be referenced by an allowlisted module by now"


def test_cuadrillas_literal_appears_only_in_allowlisted_modules():
    """`inspector-asignaciones.js` never touches `cuadrillas` at all — only
    `sticker-asignaciones.js` (admin-only, ported in slice 8, task 8.4)
    does. Prior to slice 8 this collection had ZERO hits under backend/app/
    (a legitimate empty-subset pass). Slice 8's 8.4 extends ALLOWED_MODULES
    with routers/sticker_asignaciones.py, giving this collection its FIRST
    real hit — so this test now also asserts a non-empty hit set, mirroring
    the sticker_matches test above."""
    hits = _files_containing("cuadrillas")
    unexpected = hits - ALLOWED_MODULES
    assert not unexpected, f"unexpected cuadrillas reference(s): {sorted(unexpected)}"
    assert hits, "expected cuadrillas to be referenced by an allowlisted module by now"


def test_survey_cali_literal_is_used_by_an_allowlisted_module():
    """Slice 7b (task 7.6) opened `survey_cali`'s OWN sole-writer check,
    independent of `sticker_matches`/`cuadrillas` above (design.md ADR-9's
    extension for this collection). Slice 8c (task 8.11) finalizes the
    allowlist to its CLOSED set: `services/survey_cali.py`,
    `app/jobs/dashboard_refresh.py`, `routers/survey_cali.py` — confirmed
    by this scan that no other module under `backend/app/` references the
    literal."""
    hits = _files_containing("survey_cali")
    unexpected = hits - ALLOWED_MODULES_SURVEY_CALI
    assert not unexpected, f"unexpected survey_cali reference(s): {sorted(unexpected)}"
    assert hits, "expected survey_cali to be referenced by an allowlisted module by now"
