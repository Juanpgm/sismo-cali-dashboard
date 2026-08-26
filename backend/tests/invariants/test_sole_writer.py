"""tests/invariants/test_sole_writer.py — design.md ADR-9: `sticker_matches`
and `cuadrillas` have NO Firestore security rules, so "the backend is sole
writer, and only from these modules" must hold by construction, not by
policy. Slice 5 (`routers/inspector_asignaciones.py`) introduced this
literal under the invariant. Slice 7 (task 7.9) adds the THIRD WRITE module,
`app/jobs/cruce_sticker.py` (+ its copied pipeline module,
`app/integracion/cruce_gestor.py` — see below). ADR-9 names one more WRITE
module joining later: `routers/sticker_asignaciones.py` (slice 8) — do NOT
anticipate it here; extend `ALLOWED_MODULES` in its own slice's RED task
(8.4 per tasks.md).

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
ALLOWED_MODULES = {
    APP_ROOT / "routers" / "inspector_asignaciones.py",
    APP_ROOT / "routers" / "sticker_status.py",  # read-only, see docstring
    APP_ROOT / "jobs" / "cruce_sticker.py",
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
    """`inspector-asignaciones.js` never touches `cuadrillas` at all (only
    `sticker-asignaciones.js`, admin-only, ported in slice 8, does) — so
    this collection has ZERO hits under backend/app/ at this slice, which
    is a legitimate empty-subset pass, not a gap. Slice 8's 8.4 extends
    ALLOWED_MODULES and this test will then also assert a non-empty hit
    set for `cuadrillas`, mirroring the sticker_matches test above."""
    hits = _files_containing("cuadrillas")
    unexpected = hits - ALLOWED_MODULES
    assert not unexpected, f"unexpected cuadrillas reference(s): {sorted(unexpected)}"
