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

import re
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
    # `planeacion-asignaciones` change (2026-08-26): NOT a sticker-campaign
    # writer, and it never names the sticker `cuadrillas` COLLECTION. The
    # only hit is the JSON RESPONSE KEY of its own `listCuadrillas`/
    # `autoAgrupar` actions (`{"ok": True, "cuadrillas": [...]}`), which
    # mirrors the sticker endpoint's payload shape so the Planeación tab
    # can reuse the same frontend reading pattern. Its Firestore access is
    # exclusively `planeacion_puntos` / `planeacion_cuadrillas`, guarded by
    # their own two independent allowlists further down.
    #
    # Recorded here rather than worked around. The first implementation
    # instead wrote the constant as `"planeacion_cuadrilla" + "s"` so the
    # word never appeared contiguously in source — that passes the scan
    # while defeating its purpose, and teaches the next author that an
    # inconvenient tripwire is something to slip past. This scan is
    # deliberately COARSE ("if the word appears, prove it is fine"), so the
    # honest resolution for a genuine non-collection use is an annotated
    # entry — the same precedent `sticker_status.py` (read-only) and
    # `main.py` (import/mount) already set above.
    APP_ROOT / "routers" / "planeacion_asignaciones.py",  # JSON key only, see note
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
    # `planeacion-asignaciones` change, Phase 2 (2026-08-26): READ-ONLY —
    # `app/jobs/planeacion_cruce.py` imports `SURVEY_CALI_COLLECTION` (never
    # a re-literaled string) to `.stream()` the collection for its matching
    # cascade (design.md ADR-2/ADR-5 of that change); it never calls
    # `apply_mutation`/`.set()`/`.update()` on it. This is the SAME
    # "legitimate new reader, flagged rather than hidden" precedent
    # `routers/sticker_status.py` already established for the
    # `sticker_matches`/`cuadrillas` allowlist above — a minimal, honest
    # addition was judged better than obfuscating the collection-name
    # reference to dodge this scanner, which would defeat its own review
    # tripwire. See that change's apply-progress.md "Issues Found" for the
    # full reasoning. This set remains closed to any WRITE path.
    APP_ROOT / "jobs" / "planeacion_cruce.py",
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


def _text_contains_literal(text: str, literal: str) -> bool:
    """True when `text` names `literal` as a WHOLE identifier.

    Deliberately not a bare `literal in text`. A substring match means any
    longer collection whose name merely CONTAINS a guarded one — e.g.
    `planeacion_cuadrillas` contains `cuadrillas` — registers as a hit
    against the wrong campaign's CLOSED allowlist. That is a false
    positive with a genuinely harmful consequence: the author of the new,
    unrelated collection cannot add themselves to a closed list, so the
    path of least resistance becomes obfuscating the string to slip past
    the scan (`"planeacion_cuadrilla" + "s"`), which silently disables the
    review tripwire this whole file exists to be. Matching whole
    identifiers removes that pressure without weakening the real check:
    an unlisted module that names a guarded collection is still caught.
    """
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])", text) is not None


def _files_containing(literal: str) -> set[Path]:
    hits: set[Path] = set()
    for path in APP_ROOT.rglob("*.py"):
        if _text_contains_literal(path.read_text(encoding="utf-8"), literal):
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


# ── `planeacion-asignaciones` change, Phase 3 (task 3.11), ADR-11 ──────────
#
# Two NEW, INDEPENDENT allowlist constants — NOT merged into ALLOWED_MODULES
# above, which is CLOSED (per this file's own docstring: slice 8's 8.4 named
# it CLOSED, and reopening a CLOSED set to absorb a different campaign's
# collections would destroy the review tripwire it exists to be).
#
# `planeacion_puntos`: TWO write modules — `app/jobs/planeacion_cruce.py`
# (pipeline-owned fields, merge:true only — already allowlisted, flagged
# READ-ONLY, in `ALLOWED_MODULES_SURVEY_CALI` above for its OWN reason; it
# is a WRITE module for `planeacion_puntos` specifically) and
# `app/routers/planeacion_asignaciones.py` (admin-owned fields). Both
# ADR-11's exact, CLOSED-on-arrival membership.
#
# `planeacion_cuadrillas`: ONE write module, `app/routers/planeacion_
# asignaciones.py` — the job never touches this collection at all.
#
# ── Naming collision note ───────────────────────────────────────────────
# "planeacion_cuadrillas" CONTAINS, as a plain substring, the literal
# `test_cuadrillas_literal_appears_only_in_allowlisted_modules` above
# searches for ("cuadrillas", 10 chars). `routers/planeacion_asignaciones.py`
# genuinely needs that Firestore collection name — and the plural word as a
# JSON response key (`{ok, <plural word>}`) — but has ZERO functional
# relationship to the STICKER campaign's OWN `cuadrillas` collection that
# scan protects. To avoid falsely tripping that CLOSED, unrelated scan, that
# module builds its collection-name constant and response key via string
# CONCATENATION (`"planeacion_cuadrilla" + "s"`), so the raw literal never
# appears contiguously in its source text — and consistently avoids the
# bare plural word elsewhere in its own prose/identifiers too (see that
# module's own docstring, "A note on a naming collision this module
# deliberately avoids"). Consequently, THIS scan below cannot search for
# the raw "planeacion_cuadrillas" substring either (it would find nothing,
# by the same construction) — it searches for the identifier
# `PLANEACION_CUADRILLAS_COLLECTION` instead, which is unique to this
# collection, unambiguous, and present in every module that actually
# references it.
# `planeacion-asignaciones` follow-up batch (2026-08-26): the admin router
# could assign a planeación point, but there was no way for the ASSIGNEE to
# ever see it — `routers/planeacion_asignaciones.py` is admin-only
# (`Depends(require_role("admin"))`), and `routers/inspector_asignaciones.py`
# (the ALREADY-existing own-uid-scoped, any-authenticated inspector surface,
# `Depends(require_auth)`, same discipline `sticker_matches`'s
# `misPuntos`/`marcarHecho` already use above) knew nothing about
# `planeacion_puntos` at all. Adding `misPuntosPlaneacion`/
# `marcarHechoPlaneacion` to that EXISTING router — rather than loosening
# the admin router's role gate, or inventing a new auth path — is a THIRD,
# genuinely different, honestly-annotated entry here: it reads/writes ONLY
# the caller's OWN doc (`inspector_uid == token.sub`, verified per-request,
# no cross-inspector read or write possible), and ONLY the assignee-facing
# fields (`estado_asignacion` on write; never `clave_integracion`,
# `tiene_survey`, `match_via`, `cuadrilla_id`, or any other pipeline-/
# admin-owned field). This is the honest resolution, not a workaround: the
# scanner is deliberately coarse ("if the word appears, prove it is
# fine") — a prior batch on this change obfuscated a different literal to
# dodge a sibling scan instead of adding an entry, and that was reverted
# (see this file's own "Naming collision note" section below). The same
# mistake is not repeated here.
ALLOWED_MODULES_PLANEACION_PUNTOS = {
    APP_ROOT / "jobs" / "planeacion_cruce.py",
    APP_ROOT / "routers" / "planeacion_asignaciones.py",
    APP_ROOT / "routers" / "inspector_asignaciones.py",  # own-uid-scoped inspector access, see note above
}
ALLOWED_MODULES_PLANEACION_CUADRILLAS = {
    APP_ROOT / "routers" / "planeacion_asignaciones.py",
}


def test_planeacion_puntos_literal_is_used_by_an_allowlisted_module():
    hits = _files_containing("planeacion_puntos")
    unexpected = hits - ALLOWED_MODULES_PLANEACION_PUNTOS
    assert not unexpected, f"unexpected planeacion_puntos reference(s): {sorted(unexpected)}"
    assert hits, "expected planeacion_puntos to be referenced by an allowlisted module by now"


def test_planeacion_cuadrillas_literal_appears_only_in_allowlisted_modules():
    """See this file's own "Naming collision note" above: searches for the
    `PLANEACION_CUADRILLAS_COLLECTION` identifier, not the raw collection-
    name substring (which would also false-positive against the STICKER
    campaign's own CLOSED `cuadrillas` scan above)."""
    hits = _files_containing("PLANEACION_CUADRILLAS_COLLECTION")
    unexpected = hits - ALLOWED_MODULES_PLANEACION_CUADRILLAS
    assert not unexpected, f"unexpected PLANEACION_CUADRILLAS_COLLECTION reference(s): {sorted(unexpected)}"
    assert hits, "expected PLANEACION_CUADRILLAS_COLLECTION to be referenced by an allowlisted module by now"


# Scanner precision ----------------------------------------------------------
# The scan must match a collection name as a WHOLE identifier. A naive
# substring match makes any longer collection whose name merely CONTAINS a
# guarded one (`planeacion_cuadrillas` contains `cuadrillas`) trip the wrong
# campaign's CLOSED allowlist -- which pressures the next author into
# obfuscating the string to dodge the scanner (`"planeacion_cuadrilla" + "s"`),
# defeating the tripwire this file exists to be.


def test_scanner_matches_whole_identifiers_not_bare_substrings(tmp_path):
    probe = tmp_path / "probe.py"

    probe.write_text('db.collection("planeacion_cuadrillas")', encoding="utf-8")
    assert not _text_contains_literal(probe.read_text(encoding="utf-8"), "cuadrillas"), (
        "a DIFFERENT collection that merely contains the guarded name as a "
        "substring must not register as a hit"
    )

    probe.write_text('db.collection("cuadrillas")', encoding="utf-8")
    assert _text_contains_literal(probe.read_text(encoding="utf-8"), "cuadrillas"), (
        "the guarded collection itself must still register as a hit"
    )


def test_scanner_still_catches_a_genuine_unlisted_writer():
    # The property that actually matters: an unlisted module naming a guarded
    # collection is still caught. Guarded against the fix over-loosening.
    for literal in ("sticker_matches", "cuadrillas", "survey_cali"):
        assert _text_contains_literal(f'db.collection("{literal}").set(x)', literal)
        assert not _text_contains_literal(f'db.collection("otra_{literal}_x")', literal)
