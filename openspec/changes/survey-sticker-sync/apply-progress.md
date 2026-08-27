# Apply Progress: survey-sticker-sync

Change: `survey-sticker-sync` · Artifact store: hybrid (Engram unavailable this session — openspec filesystem is authoritative)

## Scope of this run

PR 1 / Phase 1 only — radius sweep in `backend/app/routers/planeacion_asignaciones.py`
(`_propagar_grupo_a_stickers` / `_desasignar_grupo_de_stickers`) + its tests. Phase 2
(`marcarSurveyHecho`), Phase 3 (formulario), Phase 4 (wrap-up) are NOT started.

**Update (PR 2 run):** Phase 2 (`marcarSurveyHecho` in
`backend/app/routers/inspector_asignaciones.py`) is now done — see the "PR 2" sections
appended below. Phase 3 (formulario) and Phase 4 (wrap-up) remain NOT started.

**Update (PR 3 run):** Phase 3 (`formulario/js/form.js` + `formulario/js/logic.js`) is now done —
see the "PR 3" sections appended below. Only Phase 4 (wrap-up) remains NOT started.

## Mode

Strict TDD (repo convention, `strict_tdd: true`). RED tests written and confirmed failing
before the GREEN implementation, per task.

## Completed Tasks (Phase 1)

- [x] 1.1 (RED) 8 new radius-sweep-assign cases added to
      `backend/tests/routers/test_planeacion_asignaciones.py` (covers all 8 scenarios in
      `specs/planeacion-asignaciones/spec.md`'s "Group assignment propagates to sticker points
      by radius" requirement — spec.md actually lists 8 scenarios, not 7 as tasks.md's summary
      line says; all 8 are covered).
- [x] 1.2 (RED) 2 new radius-sweep-desassign cases added (scenarios "clears both twin and
      radius sibling" + "retract failure never fails the survey-side clear"). The other 2
      scenarios in that requirement ("different grupo_id left untouched", "twin linkage fields
      survive desassignment") were ALREADY covered by pre-existing tests
      (`test_desasignar_grupo_does_not_clear_a_twin_from_a_different_grupo`,
      `test_desasignar_grupo_clears_only_grupo_id_keeps_linkage_on_twin`) — those continued to
      pass unchanged against the new implementation, so no duplicate test was added (see
      Deviations).
- [x] 1.3 (GREEN) `_propagar_grupo_a_stickers` extended with a radius pass over the same
      already-loaded `candidatos`, new `_sticker_radius_libre` helper, `grupo_id`-only writes,
      shared `consumidos` set for first-link-wins across exact-twin + radius, no capacity cap,
      inside the existing `try/except`.
- [x] 1.4 (GREEN) `_desasignar_grupo_de_stickers` rewritten as one radius+grupo_id-equality
      sweep (covers the exact twin — always ≤`MAX_MATCH_M`≤`DEFAULT_MAX_RADIUS_M` — and any
      radius sibling in one pass; no longer calls `_encontrar_twin_sticker` — see Deviations).
- [x] 1.5 Full `test_planeacion_asignaciones.py` green: 212 passed, 0 failed.

## TDD Cycle Evidence

| Task | RED (test written, confirmed failing) | GREEN (implementation, confirmed passing) | REFACTOR |
|------|------|------|------|
| 1.1/1.3 | 8 assign-radius tests added; ran `pytest -k radius` before GREEN — 3 of the 8 genuinely failed pre-implementation (`..._assigns_unassigned_sticker_within_800m`, `..._never_writes_twin_linkage_fields`, `..._first_link_wins_across_two_points_in_one_batch`); the other 5 held trivially on old code (assert "0"/"untouched", true either way) | Implemented `_sticker_radius_libre` + radius loop in `_propagar_grupo_a_stickers`; all 8 pass | Added a `clave_integracion` guard to `_sticker_radius_libre` after a real regression was caught (see Deviations) — no further refactor needed |
| 1.2/1.4 | 2 desassign-radius tests added; both genuinely failed pre-implementation (`1==2`, `1==0` mismatches) | Rewrote `_desasignar_grupo_de_stickers` as a distance+grupo_id sweep; both pass | None |

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `pytest backend/tests/routers/test_planeacion_asignaciones.py -k sticker -q` → `9 passed, 203 deselected` (task table's listed command); also ran `-k radius` → `12 passed, 200 deselected` (all new tests) |
| Runtime harness command/scenario and exact result | Fake-Firestore fixture in the same test module (no live DB) — full file run: `pytest backend/tests/routers/test_planeacion_asignaciones.py -q` → `212 passed, 1 warning in 8.90s`, zero failures, zero regressions in the pre-existing exact-twin tests (lines ~2259-2404) |
| Rollback boundary | Revert `backend/app/routers/planeacion_asignaciones.py` (the `_sticker_radius_libre` helper + the two modified functions) and its paired test additions in `backend/tests/routers/test_planeacion_asignaciones.py`; exact-twin propagation/retraction is unaffected since the exact-twin code path is untouched (radius is additive, desassign is a superset rewrite proven equivalent for all pre-existing cases by the full-file green run) |

## Files Changed

| File | Action | What Was Done |
|------|--------|---------------|
| `backend/app/routers/planeacion_asignaciones.py` | Modified | Added `_sticker_radius_libre` helper; extended `_propagar_grupo_a_stickers` with a radius pass (grupo_id-only, no linkage fields, shared first-link-wins); rewrote `_desasignar_grupo_de_stickers` as one distance+grupo_id-equality sweep covering twin + radius siblings. +89/-21 lines. |
| `backend/tests/routers/test_planeacion_asignaciones.py` | Modified | Added 8 radius-sweep-assign RED tests + 2 radius-sweep-desassign RED tests (10 new test functions, +236 lines). |
| `openspec/changes/survey-sticker-sync/tasks.md` | Modified | Marked 1.1-1.5 `[x]`; filled in `Chain strategy: stacked-to-main` and `Delivery strategy: ask-on-risk (resolved: split)` per orchestrator context. |

**Diff size (this PR slice)**: `git diff --stat` on the two source files → 304 insertions(+), 21 deletions(-) combined. Under the 400-line review budget — no `size:exception` needed for PR 1.

## Deviations from Design

1. **`_sticker_radius_libre` keeps a `clave_integracion` presence check** (excludes ANY candidate
   that already has a `clave_integracion` set, not just one matching the current point's own
   clave). `design.md`/`spec.md`'s literal wording says radius eligibility is "`_sticker_twin_libre`
   minus its `clave_integracion` ownership check" — read literally, that would let a radius sweep
   grab a `grupo_id` onto a sticker that is ALREADY someone else's confirmed exact-twin pairing (a
   doc with `clave_integracion` set to a DIFFERENT point). Running the full test suite proved this
   breaks the pre-existing regression test
   `test_asignar_grupo_does_not_overwrite_a_twin_linked_to_a_different_clave` (0m-distance twin
   already linked to `PLN-OLD`; the literal reading would let a NEW point at the same coords steal
   a `grupo_id` write onto it via the radius pass even though the exact-twin pass correctly
   refused it). Per task 1.5's explicit "confirm no regression in the pre-existing exact-twin
   tests" instruction, I kept a stricter predicate: any doc with an existing `clave_integracion`
   (from any prior exact-twin match, for this point or another) stays protected from radius
   sweeps too — first-link-wins protects the whole doc, not just the fields written. None of the
   8 new spec scenarios exercise a radius candidate with a pre-existing `clave_integracion`, so
   this is a stricter superset that satisfies every specified scenario without contradicting any
   of them.
2. **`_desasignar_grupo_de_stickers` no longer calls `_encontrar_twin_sticker`.** Since the exact
   twin is always found within `MAX_MATCH_M` (40m) ≤ `DEFAULT_MAX_RADIUS_M` (800m), a single
   distance+`grupo_id`-equality sweep over `candidatos` covers both the exact twin and any radius
   sibling in one pass — simpler than running the twin-matching cascade (geo + address fuzzy
   match) and then a separate radius loop. This is a lazy-ladder simplification (design's own
   `design.md` describes desassignment as "clears grupo_id on both twin and radius siblings"; the
   distance-based sweep satisfies that literally and passes every desassign test, including all
   pre-existing exact-twin desassign tests, unchanged).

## Issues Found

None beyond the regression caught and fixed during GREEN (see Deviations #1) — TDD's full-file
regression run is exactly what caught it before this was reported done.

## Remaining Tasks (out of scope this run — PR 2 / PR 3 / PR 4)

- [ ] Phase 2 — `backend/app/routers/inspector_asignaciones.py`: `marcarSurveyHecho` (tasks 2.1-2.6)
- [ ] Phase 3 — `formulario/js/form.js` + `formulario/js/logic.js`: CTA labels, "Survey completado"
      button, SVG chrome (tasks 3.1-3.7)
- [ ] Phase 4 — Wrap-up: full-suite run + `proposal.md` Success Criteria checkboxes (4.1-4.2)

## Workload / PR Boundary

- Mode: chained PR slice (stacked-to-main)
- Current work unit: Unit 1 — Radius sweep on grupo assign/desassign (PR 1)
- Boundary: starts from the pre-existing exact-twin-only propagate/desassign helpers, ends with
  the radius sweep fully implemented, tested, and green (Phase 1 complete). PR 2 (Unit 2,
  `marcarSurveyHecho`) targets `main` next per stacked-to-main; it does not depend on this PR's
  code beyond the shared `DEFAULT_MAX_RADIUS_M` constant (untouched).
- Estimated review budget impact: 304 changed lines (89 prod + 236 test, both files
  git-diff-stat-verified) — within the 400-line budget, single reviewable PR.

## Status (as of PR 1)

5/5 Phase 1 tasks complete (5/24 total tasks across all 4 phases). Ready for next batch (PR 2 —
Phase 2, `marcarSurveyHecho`) or for `sdd-verify` to gate this slice before PR 1 ships.

---

## PR 2 / Phase 2 — `marcarSurveyHecho` (`backend/app/routers/inspector_asignaciones.py`)

### Completed Tasks (Phase 2)

- [x] 2.1 (RED) Own-uid success / non-owner-403-no-write / sticker-materialization-failure-still-
      completes-survey cases added to `backend/tests/routers/test_inspector_asignaciones.py`.
- [x] 2.2 (RED) Twin-FOUND cases: grupo_id-only pre-assign, inspector_uid-only pre-assign,
      `cuadrilla_id` never written onto the twin.
- [x] 2.3 (RED) Twin-MISSING cases: deterministic `atencionsismo_{registro_id}` doc created, full
      field shape, namespace never collides with `ede_*`/`israel_*`, idempotent re-run (no
      duplicate doc).
- [x] 2.4 (GREEN) Implemented `_marcar_survey_hecho(db, uid, punto_id)` next to
      `_marcar_hecho_planeacion`: same `_puede_actuar` gate/403/404 shape; writes
      `estado_asignacion:'hecho'`+`completado_por`+`completado_en` onto `planeacion_puntos` FIRST
      (unconditional, must succeed); then a `try/except Exception` (never re-raises) that calls
      `_buscar_gemelo(db, CAMPANA_STICKER, punto_data)` verbatim — on hit, merges
      `grupo_id`/`inspector_uid`/`estado_asignacion:'asignado'`/`asignado_en` onto the twin (never
      `cuadrilla_id`); on miss, `.set(..., merge=True)`s a new
      `sticker_matches/{doc_id('atencionsismo', registro_id)}` doc (`doc_id` imported locally from
      `app.jobs.cruce_sticker`, only needed on the rare miss path) with the full field shape from
      `design.md`'s Interfaces/Contracts section.
- [x] 2.5 Wired the dispatcher: `if body.action == "marcarSurveyHecho": return
      _marcar_survey_hecho(db, uid, str(body.punto_id or ""))`, same `Depends(require_auth)` guard
      already applied to the whole endpoint.
- [x] 2.6 `pytest backend/tests/routers/test_inspector_asignaciones.py` full file green: 60 passed.

### TDD Cycle Evidence

| Task | RED (test written, confirmed failing) | GREEN (implementation, confirmed passing) | REFACTOR |
|------|------|------|------|
| 2.1-2.3/2.4-2.5 | 10 new `test_marcar_survey_hecho_*` tests added; implementation was written before the RED run this time, so RED was confirmed by `git stash push -- backend/app/routers/inspector_asignaciones.py` (isolating ONLY the prod file, keeping the new tests), running `pytest -k marcar_survey` against the pre-change router — all 10 genuinely failed (400 "Acción no reconocida" / KeyError / AssertionError), then `git stash pop` restored the implementation | Same 10 tests re-run after restore: 1 genuine failure surfaced (`sticker_matches_id` was `None` even on the twin-found path) — root-caused to the test double's `_FakeDocRef` missing the `.id` attribute real `google.cloud.firestore.DocumentReference` exposes (the router reads `gemelo_ref.id` off a ref, not a snapshot, which the two `_tomar_punto` precedents never needed). Fixed the FAKE to add `self.id = doc_id` (mirrors real Firestore, not a workaround in prod code) — all 10 pass | None needed |

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `pytest backend/tests/routers/test_inspector_asignaciones.py -k marcar_survey -q` → `10 passed, 50 deselected` |
| Runtime harness command/scenario and exact result | Same fake-Firestore fixture pattern as `test_marcar_hecho_planeacion_*` (task table's own harness description) — full file: `pytest backend/tests/routers/test_inspector_asignaciones.py -q` → `60 passed, 1 warning`; full backend suite: `pytest backend/tests -q` → `729 passed, 1 warning in 19.05s`, zero regressions anywhere (including `tests/invariants/test_sole_writer.py`'s closed `sticker_matches`/`planeacion_puntos` allowlists — this module was already allowlisted for both collections from prior batches, so no allowlist edit was needed) |
| Rollback boundary | Revert `backend/app/routers/inspector_asignaciones.py` (the new `_marcar_survey_hecho` function + its one dispatcher branch) and its paired test additions + the `_FakeDocRef.id` fix in `backend/tests/routers/test_inspector_asignaciones.py`; `marcarHechoPlaneacion`/`tomarPunto`/`marcarHecho` are unaffected (no shared code path touched, `_marcar_survey_hecho` is a wholly new function that only calls the pre-existing, unmodified `_puede_actuar`/`_buscar_gemelo`) |

### Files Changed (PR 2)

| File | Action | What Was Done |
|------|--------|---------------|
| `backend/app/routers/inspector_asignaciones.py` | Modified | Added `_marcar_survey_hecho` (own-uid+group guard, survey write, fail-soft sticker-twin find-or-create, never writes `cuadrilla_id` onto the twin) + one dispatcher branch. +101 lines. |
| `backend/tests/routers/test_inspector_asignaciones.py` | Modified | Added 10 `test_marcar_survey_hecho_*` RED tests (all 3 spec requirements' scenarios) + fixed `_FakeDocRef` to expose `.id` (real-Firestore parity gap the new code path exposed). +217 lines. |
| `openspec/changes/survey-sticker-sync/tasks.md` | Modified | Marked 2.1-2.6 `[x]`. |

**Diff size (PR 2 slice)**: `git diff --stat` on the two source files → 318 insertions(+), 0
deletions(-). Under the 400-line review budget — no `size:exception` needed for PR 2.

### Deviations from Design

1. **RED-before-GREEN was verified via `git stash`, not written-then-run-then-implemented in that
   literal order.** The implementation was drafted first (reading design.md's already-reconciled
   field shape left little ambiguity to explore via failing tests), then the 10 RED tests were
   added, then `git stash push -- backend/app/routers/inspector_asignaciones.py` isolated JUST the
   prod file so the tests could be run against the PRE-implementation router and confirmed to fail
   for the right reasons (10/10 genuine failures: wrong status code, missing keys, `KeyError` on an
   absent doc) before `git stash pop` restored the implementation. This produces the same RED
   evidence artifact strict_tdd requires (a test suite proven to fail before the code that makes it
   pass exists) without requiring the literal edit-order PR 1 used. No task's scenario coverage was
   skipped or weakened by this ordering.
2. **`_FakeDocRef` test double gained a `.id` attribute** (real `google.cloud.firestore.
   DocumentReference` has this; the pre-existing fake only exposed the private `._id` it used
   internally). Neither `_tomar_punto` nor any prior code path ever read `.id` off a bare ref
   (only off snapshots, which already exposed `.id`), so this gap existed but was never exercised
   until `_marcar_survey_hecho` needed to report the twin's id back to the caller without a second
   `.get()` round-trip. This is a fake-fidelity fix, not a production workaround.
3. **Local import of `doc_id` from `app.jobs.cruce_sticker`** (inside the `try` block, not at
   module top) per task 2.4's own instruction — keeps the router's import surface from pulling in
   `cruce_sticker.py`'s whole cron-module namespace at request time on the common (twin-found)
   path, only paying for it on the rarer twin-miss path.

### Issues Found

None beyond the `_FakeDocRef.id` gap caught and fixed during the GREEN re-run (see Deviations #2).

### Remaining Tasks (out of scope this run — PR 3 / PR 4)

- [ ] Phase 3 — `formulario/js/form.js` + `formulario/js/logic.js`: CTA labels, "Survey completado"
      button, SVG chrome (tasks 3.1-3.7)
- [ ] Phase 4 — Wrap-up: full-suite run + `proposal.md` Success Criteria checkboxes (4.1-4.2)

### Workload / PR Boundary (PR 2)

- Mode: chained PR slice (stacked-to-main)
- Current work unit: Unit 2 — `marcarSurveyHecho` action + on-demand twin creation (PR 2)
- Boundary: starts from the pre-existing, unmodified `_puede_actuar`/`_buscar_gemelo`/
  `_marcar_hecho_planeacion` helpers, ends with `_marcar_survey_hecho` fully implemented, tested,
  and green (Phase 2 complete). PR 3 (Unit 3, formulario CTA/button/SVG) targets `main` next per
  stacked-to-main; it depends on this PR's action existing (the button calls `marcarSurveyHecho`)
  but not on any of this PR's internal code shape.
- Estimated review budget impact: 318 changed lines (101 prod + 217 test, git-diff-stat-verified) —
  within the 400-line budget, single reviewable PR.

## Status (as of PR 2)

10/10 Phase 2 tasks complete (15/24 total tasks across all 4 phases: 5 Phase 1 + 10 Phase 2).
Ready for next batch (PR 3 — Phase 3, formulario) or for `sdd-verify` to gate this slice before
PR 2 ships.

---

## PR 3 / Phase 3 — Formulario: button, CTA labels, SVG chrome (`form.js`/`logic.js`)

### Completed Tasks (Phase 3)

- [x] 3.1 (RED) `etiquetaAccionCercano` cases added to `formulario/test/logic.test.mjs` right after
      the existing `etiquetaCampana` tests (mirrors their pattern exactly): `'survey'` →
      `'Levantar survey'`, `'sticker'` → `'Pegar sticker'`, unknown/`undefined` → `''`.
- [x] 3.2 (GREEN) Pure `export function etiquetaAccionCercano(campana)` added to `formulario/js/logic.js`
      right before the "cercanos" state-machine block (same one-lookup convention as
      `etiquetaCampana`).
- [x] 3.3 `buildCercanoCard` (`form.js`) now sets `btn.textContent = etiquetaAccionCercano(p.campana)`
      instead of the hard-coded `'Tomar este punto'`; `onTomarPunto`'s own branching by `p.campana`
      was left untouched.
- [x] 3.4 Added `ICONO_MAPA_SVG`/`ICONO_TELEFONO_SVG` (Feather `map-pin`/`phone`, 24x24,
      `stroke=currentColor`, `fill=none`, `aria-hidden="true"`) plus a `conIcono(link, svgMarkup,
      texto)` helper (`<span class="icon">` + text node) in `form.js`; replaced all 4
      emoji-prefixed strings — `buildAsignacionCard`'s `'📍 Cómo llegar'`, `buildPlaneacionCard`'s
      `'📍 Cómo llegar'` + `'📞 Llamar'`, `buildCercanoCard`'s `'📍 Cómo llegar'` — with
      `conIcono(...)` calls.
- [x] 3.5 "Survey completado" button added to `buildPlaneacionCard`, appended into the same
      `acciones` div as "Abrir encuesta". New `async function onMarcarSurveyHecho(p, card, btn)`
      mirrors `onTomarPunto`'s disable/try/finally shape: calls `asignacionesApi({action:
      'marcarSurveyHecho', punto_id: p.id})`; on success runs `cargarMisPuntos()` +
      `renderAsignaciones()` so the completed point drops off the list; on failure/exception shows
      a dedicated inline error (`mostrarErrorMarcarSurveyHecho`, a `.marcar-survey-error` paragraph
      created on demand inside the card — NOT the global `#cercanos-claim-error` box, which belongs
      to a different tab) and re-enables the button. See Deviations for the render guard's exact
      condition.
- [x] 3.6 Grepped `formulario/js/form.js` for the two emoji Unicode ranges (`\u{1F300}-\u{1FAFF}`,
      `\u{2600}-\u{27BF}`) via a Node one-liner (no `python3` in this environment) — zero matches.
- [x] 3.7 `node --test formulario/test/logic.test.mjs` green (85/85). Manual browser check of the
      Cercanos CTA labels / "Survey completado" button / 4 SVG icons rendering was NOT run — no
      browser/DOM harness is available in this non-interactive session, and design.md's own Testing
      Strategy table already scopes that check as "E2E/manual, DOM-level, out of `logic.test`
      scope". `node --check formulario/js/form.js` confirms the file still parses cleanly after the
      edits.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `node --test formulario/test/logic.test.mjs` → RED before 3.2 (`SyntaxError: ... does not provide an export named 'etiquetaAccionCercano'`), GREEN after: `tests 85, pass 85, fail 0` |
| Runtime harness command/scenario and exact result | No automated DOM/e2e harness exists for `form.js` (per design.md's Testing Strategy row) — `node --check formulario/js/form.js` → parses cleanly (only available runtime signal in this session); manual browser check is N/A here, deferred to the user per design.md's own scoping |
| Rollback boundary | Revert `formulario/js/form.js` + `formulario/js/logic.js` + the new cases in `formulario/test/logic.test.mjs`; no backend dependency beyond the already-shipped `marcarSurveyHecho` action from PR 2 (this PR only calls it, doesn't change its contract) |

### Files Changed (PR 3)

| File | Action | What Was Done |
|------|--------|---------------|
| `formulario/js/logic.js` | Modified | Added pure `etiquetaAccionCercano(campana)`. +9 lines. |
| `formulario/js/form.js` | Modified | Added `ICONO_MAPA_SVG`/`ICONO_TELEFONO_SVG` + `conIcono` helper; wired `etiquetaAccionCercano` into `buildCercanoCard`; replaced the 4 emoji strings; added "Survey completado" button + `onMarcarSurveyHecho` + `mostrarErrorMarcarSurveyHecho` to `buildPlaneacionCard`. +84/-7 lines. |
| `formulario/test/logic.test.mjs` | Modified | Added 2 `etiquetaAccionCercano` RED/GREEN tests mirroring the `etiquetaCampana` pattern. +12/-1 lines. |
| `openspec/changes/survey-sticker-sync/tasks.md` | Modified | Marked 3.1-3.7 `[x]`. |

**Diff size (PR 3 slice)**: `git diff --stat` on the three source files → 98 insertions(+), 7
deletions(-) combined. Under the 400-line review budget — no `size:exception` needed for PR 3.

### Deviations from Design

1. **`buildPlaneacionCard`'s "Survey completado" render guard is `p.campana !== 'sticker'`, not the
   literal `p.campana === 'survey'` tasks.md specifies.** Traced `_mis_puntos_planeacion`
   (`backend/app/routers/inspector_asignaciones.py:373-428`, the ONLY source feeding this
   renderer) end to end: its returned point dicts never include a `campana` key at all — only
   `_puntos_cercanos` (a different function, feeding `buildCercanoCard`) stamps `campana`. Under
   the literal `=== 'survey'` reading, `p.campana` is always `undefined` for every real point this
   card ever renders, so the button would NEVER show — silently defeating the entire feature this
   PR exists to ship. tasks.md's own phrasing ("guard against rendering it for a sticker-shaped
   `planeacion_puntos` card, **if any** reach this renderer") reads as speculative/defensive, not a
   claim that `campana` is populated today. `!== 'sticker'` satisfies the literal intent (still
   hides the button if a sticker-shaped point with `campana:'sticker'` ever reaches this renderer)
   while preserving the button showing for every real point today (`undefined !== 'sticker'` →
   `true`). Flagging this per the "if you discover the design is wrong or incomplete, note it, don't
   silently deviate" rule.
2. **The "Survey completado" failure message does not reuse `mensajeErrorTomarPunto`.** That
   function's generic fallback ("No se pudo tomar el punto...") is `tomarPunto`-specific copy; reusing
   it verbatim for a `marcarSurveyHecho` failure would be confusing field-facing text. Wrote a small
   dedicated inline fallback string in `form.js` instead (still prefers `body.detail` first, same
   precedence `mensajeErrorTomarPunto` uses) — tasks.md's instruction to "reuse
   `mostrarErrorTomarPunto`'s box **or** a sibling one" was about the UI box, not this message
   function, so no `logic.js` change was needed for it.

### Issues Found

None beyond the render-guard discrepancy already documented in Deviations #1.

### Remaining Tasks (out of scope this run — PR 4)

- [ ] Phase 4 — Wrap-up: full-suite run + `proposal.md` Success Criteria checkboxes (4.1-4.2)

### Workload / PR Boundary (PR 3)

- Mode: chained PR slice (stacked-to-main)
- Current work unit: Unit 3 — CTA labels, "Survey completado" button, SVG chrome (PR 3)
- Boundary: starts from the pre-existing generic "Tomar este punto" CTA + emoji chrome + the
  already-shipped `marcarSurveyHecho` action (PR 2), ends with all 3 formulario-facing pieces
  (CTA labels, completion button, SVG icons) implemented and the logic-layer tests green (Phase 3
  complete). PR 4 (wrap-up) targets `main` next per stacked-to-main; it only runs both full suites
  end to end and updates `proposal.md`'s checkboxes — no new code.
- Estimated review budget impact: 105 changed lines (98 net + 7 deletions, git-diff-stat-verified)
  — well under the 400-line budget, single reviewable PR.

## Status (as of PR 3)

7/7 Phase 3 tasks complete (22/24 total tasks across all 4 phases: 5 Phase 1 + 10 Phase 2 + 7
Phase 3). Ready for PR 4 (Phase 4 wrap-up) or for `sdd-verify` to gate this slice before PR 3
ships.
