"""tests/invariants/test_sole_writer.py — design.md ADR-9: `sticker_matches`
and `cuadrillas` have NO Firestore security rules, so "the backend is sole
writer, and only from these modules" must hold by construction, not by
policy. This is the FIRST literal introduced under the invariant (slice 5,
`routers/inspector_asignaciones.py`) — ADR-9 names two more modules that
join the allowlist later: `app/jobs/cruce_sticker.py` (slice 7) and
`routers/sticker_asignaciones.py` (slice 8). Do NOT anticipate those here;
extend `ALLOWED_MODULES` in their own slices' RED tasks (7.6/8.4 per
tasks.md), per this task's own "do not try to anticipate them" instruction.

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

# Slice 5 (this task): only inspector_asignaciones.py exists yet.
ALLOWED_MODULES = {
    APP_ROOT / "routers" / "inspector_asignaciones.py",
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
