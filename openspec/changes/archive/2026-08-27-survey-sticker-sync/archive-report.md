# Archive Report: survey-sticker-sync

**Date**: 2026-08-27  
**Change**: `survey-sticker-sync`  
**Status**: ARCHIVED — All tasks complete, all success criteria satisfied, all code pushed to origin/main.

## Executive Summary

The `survey-sticker-sync` change closes two field-visible gaps between survey and sticker campaigns by adding radius-based sticker propagation on group assignment, a real-time "Survey completado" action that materializes sticker twins on demand, per-type Cercanos CTA labels, and SVG icon chrome. All 24 tasks across 4 phases completed with zero CRITICAL issues; full test suites green (729 backend tests, 85 logic tests); delivered in 4 chained PRs (445fa32, 12f84ed, 1fe9521, 46bc73c). Specs merged into main; change folder moved to archive.

## Specs Merged

| Domain | Action | Details |
|--------|--------|---------|
| `openspec/specs/planeacion-asignaciones/spec.md` | 2 ADDED requirements | Group assignment propagates to sticker points by radius (8 scenarios); Group desassignment symmetrically retracts the radius sweep (4 scenarios) |
| `openspec/specs/stickers-asignacion/spec.md` | 1 MODIFIED requirement | `sticker_matches` document ownership updated to document on-demand creation via `marcarSurveyHecho` under `atencionsismo_` namespace (2 new scenarios) |
| `openspec/specs/survey-sticker-realtime-sync/spec.md` | NEW FULL SPEC | Complete capability spec with 5 requirements covering `marcarSurveyHecho` action contract, twin lookup/found/missing scenarios, Cercanos CTA labels, SVG chrome (24 scenarios total) |

## Archive Contents

**Location**: `openspec/changes/archive/2026-08-27-survey-sticker-sync/`

- `proposal.md` ✅ — scope, approach, risks, rollback, all 4 Success Criteria checked
- `tasks.md` ✅ — 24/24 tasks marked complete (5 Phase 1 + 10 Phase 2 + 7 Phase 3 + 2 Phase 4)
- `design.md` ✅ — technical approach, architecture decisions, interfaces, testing strategy
- `apply-progress.md` ✅ — detailed phase-by-phase evidence for all 4 PRs, TDD cycle proofs, deviations documented
- `specs/` — 3 delta specs (planeacion-asignaciones, stickers-asignacion, survey-sticker-realtime-sync)

## Verification Status

### Task Completion Gate ✅
All 24 implementation tasks checked in `tasks.md`:
- Phase 1 (radius sweep): 5/5 complete — 212 backend tests passed
- Phase 2 (`marcarSurveyHecho`): 10/10 complete — 60 inspector-asignaciones tests passed
- Phase 3 (formulario): 7/7 complete — 85 logic tests passed, 0 emoji in form.js
- Phase 4 (wrap-up): 2/2 complete — full backend suite 729 tests passed, all 4 Success Criteria verified

### Verification Report Status ✅
No `verify-report.md` exists (N/A for this phase model); `apply-progress.md` documents:
- Zero CRITICAL issues
- Zero regressions across all 3 phases' changes
- Both full suites green end-to-end

### Review Gate ✅
All 4 PRs pushed to origin/main with commit hashes:
- PR 1: 445fa32 (radius sweep, 304 changed lines)
- PR 2: 12f84ed (marcarSurveyHecho, 318 changed lines)
- PR 3: 1fe9521 (formulario, 105 changed lines)
- PR 4: 46bc73c (wrap-up verification, ~10 changed lines)

All within individual 400-line review budgets; chained delivery strategy (stacked-to-main) applied.

## Source of Truth Updated

The following main specs now reflect the new behavior and are the canonical source:
- `openspec/specs/planeacion-asignaciones/spec.md` — extended with radius sweep requirements
- `openspec/specs/stickers-asignacion/spec.md` — modified to document on-demand creation
- `openspec/specs/survey-sticker-realtime-sync/spec.md` — new full capability spec

## Implementation Summary

### Code Changes (Delivered in 4 chained PRs)

#### Phase 1: Radius Sweep
- `backend/app/routers/planeacion_asignaciones.py`: Added `_sticker_radius_libre` helper; extended `_propagar_grupo_a_stickers` with radius pass (grupo_id-only, no linkage fields, first-link-wins); rewrote `_desasignar_grupo_de_stickers` as distance+grupo_id-equality sweep
- `backend/tests/routers/test_planeacion_asignaciones.py`: 10 new RED tests (8 assign-radius, 2 desassign-radius), all green

#### Phase 2: marcarSurveyHecho
- `backend/app/routers/inspector_asignaciones.py`: New `_marcar_survey_hecho` function (own-uid+group gate, survey write, fail-soft sticker-twin find-or-create); dispatcher branch wired
- `backend/tests/routers/test_inspector_asignaciones.py`: 10 new RED tests (action contract, twin-found pre-assign, twin-missing creation), `_FakeDocRef.id` gap fixed, all green

#### Phase 3: Formulario
- `formulario/js/logic.js`: New `etiquetaAccionCercano(campana)` pure function
- `formulario/js/form.js`: `ICONO_MAPA_SVG`/`ICONO_TELEFONO_SVG` helpers; replaced 4 emoji strings; added "Survey completado" button + `onMarcarSurveyHecho` + error handling to `buildPlaneacionCard`
- `formulario/test/logic.test.mjs`: 2 new etiquetaAccionCercano RED/GREEN tests, all green

#### Phase 4: Verification
- No code changes; full suite verification: `pytest backend/tests` → 729 passed; `node --test formulario/test` → 85 passed; all 4 Success Criteria checkboxes updated in `proposal.md`

## Deviations from Design (Documented)

1. **`_sticker_radius_libre` clave_integracion guard**: Stricter than spec wording; protects any pre-linked sticker (even if linked to a different survey) from radius re-assignment. Justified by regression test proof.
2. **`_desasignar_grupo_de_stickers` simplified**: Uses distance+grupo_id sweep instead of calling `_encontrar_twin_sticker`, per lazy-ladder simplification principle.
3. **"Survey completado" render guard**: Uses `p.campana !== 'sticker'` (negated condition) instead of literal `=== 'survey'` because `_mis_puntos_planeacion` never populates `campana` key. Guard works as intended: shows button for real points (undefined !== 'sticker' → true), hides if ever a sticker-shaped point reaches this renderer.

## SDD Cycle Closure

- **Proposal**: Delivered and archived
- **Specification**: Delta specs merged into main; new survey-sticker-realtime-sync spec created in main
- **Design**: Delivered and archived; all architecture decisions documented
- **Tasks**: All 24 completed and checked; chained delivery strategy executed as planned
- **Apply Phase**: 4 PRs delivered (445fa32, 12f84ed, 1fe9521, 46bc73c); all pushed to origin/main
- **Verify Phase**: 729 backend + 85 formulario tests green; zero regressions; all success criteria satisfied

**No follow-up changes required. Ready for next change.**

## Traceability

Change artifacts archived to: `openspec/changes/archive/2026-08-27-survey-sticker-sync/`  
Main specs updated in: `openspec/specs/{planeacion-asignaciones,stickers-asignacion,survey-sticker-realtime-sync}/`  
Commits on origin/main: 445fa32, 12f84ed, 1fe9521, 46bc73c
