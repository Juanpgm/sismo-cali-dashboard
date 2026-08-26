```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:47ff9c8e9096ab968d3087420aa93b8af1a1aa46
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 9/9
scenarios: 35/35
test_command: python -m pytest backend/tests/ -q
test_exit_code: 0
test_output_hash: sha256:0126c0a826e9f54c455d74e0d89be8a16aecbdf9cb15f8391e4f70431204c1a2
build_command: node --test "js/**/*.test.mjs" (from web/)
build_exit_code: 1
build_output_hash: sha256:48a16a40861d958776467412f0cf29340905bccc43a00162a807c178c91a8f2d
```

## Verification Report

Change: usuarios-personas-unificadas
Version: N/A (delta specs, not yet archived)
Mode: Standard (strict_tdd: true declared in tasks.md; RED/GREEN honored for pure helpers only, DOM/fan-out explicitly out of automated scope per design.md)

Commits verified: 5df7278 (Slice A), 7ac81ea (hide-individual), 69f2396 (roster move), 47ff9c8 (Usuarios fan-out), 6fce259 (SDD paperwork). Parent commit: 575efdd.

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 27 |
| Tasks complete (checkbox) | 27 |
| Automated tasks DONE and independently re-verified | 22 |
| Manual-smoke tasks checked as done but flagged PENDING verification | 5 (1.4, 2.4, 3.5, 4.7, plus the aggregate checklist in apply-progress.md) |

No task is unchecked. No task is BLOCKED. The 5 manual-smoke tasks are honestly self-flagged as not yet executed by a human - this is a known category (no headless-DOM harness in this repo, confirmed true: no jsdom/puppeteer/playwright dependency in web/package.json or node_modules), not a hidden gap. This is called out explicitly in both tasks.md per-task STATUS lines and apply-progress.md dedicated MANUAL SMOKE section, which itemizes checks per phase matching the spec scenarios below one to one.

### Build & Tests Execution

Backend tests: PASSED

    $ python -m pytest backend/tests/ -q
    555 passed, 1 warning in 9.69s

Matches apply-progress.md claim exactly (555, unchanged baseline - this change touches zero backend files, confirmed by empty diffs below).

Frontend tests: 7 passed / 1 failed (pre-existing, unrelated)

    $ node --test "js/**/*.test.mjs"   (from web/)
    OK   js/analista.test.mjs
    OK   js/charts.test.mjs
    OK   js/data.test.mjs
    FAIL js/evaluaciones.test.mjs   ERR_UNSUPPORTED_ESM_URL_SCHEME (CDN import via israel-source.js)
    OK   js/planeacion.test.mjs     (includes new rowHtml/filterRosterInspectores assertions)
    OK   js/stickers-asignacion.test.mjs
    OK   js/usuarios.test.mjs       (new file - payloadForTipo routing, 1/1 pass)
    OK   js/utils.test.mjs
    pass 7 / fail 1

Independently confirmed the evaluaciones.test.mjs failure is pre-existing and unrelated: checked out the parent commit 575efdd into a throwaway git worktree and re-ran node --test js/evaluaciones.test.mjs there - it fails with the identical ERR_UNSUPPORTED_ESM_URL_SCHEME stack (CDN import chain via israel-source.js, nothing to do with this change). Worktree removed after the check; repo left clean.

Syntax check: PASSED

    $ node --check js/planeacion.js js/stickers.js js/stickers-asignacion.js js/usuarios.js
    (no output - all 4 edited files parse clean)

Coverage: Not tracked in this repo (no coverage tool configured)

### Untouched-file guarantees (independently re-verified, not trusted from apply claim)

    $ git diff 575efdd..47ff9c8 -- web/js/firebase-config.js
    (empty)
    $ git diff 575efdd..47ff9c8 -- api/usuarios.js api/stickers.js backend/app/routers/usuarios.py
    (empty)

Both confirmed empty. The dynamic import() of firebase-config.js/gstatic lives only in usuarios.js (loadFirebaseAuth()), and firebase-config.js itself is byte-identical to the pre-change commit.

### Spec Compliance Matrix

#### user-management spec

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Create user | Successful create admin/viewer/usuario | usuarios.test.mjs payload shape + inherited untouched api/usuarios.js | COMPLIANT (routing); e2e MANUAL SMOKE PENDING |
| Create user | Firestore write fails after Auth create | none, pre-existing rollback logic in untouched api/usuarios.js | INHERITED, unaffected by this diff |
| Create user | Inspector tipo creates via Stickers | usuarios.test.mjs routing to stickers endpoint, correct body | COMPLIANT (routing); e2e MANUAL SMOKE PENDING (brigade code, Auth rollback) |
| Create user | Conductor tipo creates a data record, no login | usuarios.test.mjs routing to planeacionAsignaciones, buildConductorPayload reuse | COMPLIANT (routing); e2e MANUAL SMOKE PENDING |
| Create user | Conductor-create failure does not touch other endpoints | Static: payloadForTipo is a pure router with no fallthrough; catch block makes exactly one callApi call per submit, no retry | COMPLIANT (code-path proof); live-network confirmation MANUAL SMOKE PENDING |
| Create user | sismocali.gov.co still rejected outside inspector tipo | usuarios.test.mjs throws, message names inspector | COMPLIANT |
| Per-tipo error isolation | Inspector-create failure is scoped to that branch | Static: showFormError prefixes by tipoLabel, modal stays open, tipoSelect value never reset on error | COMPLIANT (code-path proof); live error text MANUAL SMOKE PENDING |

Compliance summary: 6/7 scenarios COMPLIANT on the routing/logic side (unit-tested or statically provable from the diff); 5/7 additionally require a live two-backend MANUAL SMOKE pass not yet performed. 1/7 (Firestore-rollback) is pre-existing/untouched, inherited unchanged.

#### planeacion-asignaciones spec

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Vehiculo modal existing conductor only | Vehiculo save with existing conductor | planeacion.test.mjs (buildConductorPayload/buildVehiculoPayload untouched, still pass) + diff proof (conductorId = conductorSelect.value, no two-step branch) | COMPLIANT (code-path proof); e2e save MANUAL SMOKE PENDING |
| Vehiculo modal existing conductor only | No inline conductor-creation UI reachable | Diff proof: fieldset, NUEVO_CONDUCTOR sentinel, syncConductorNuevo, and the two-step save branch are all deleted, verified via git show 5df7278 | COMPLIANT |
| Inspector roster CRUD lives in Planeacion | Roster is usable from Planeacion | planeacion.test.mjs (rowHtml/filterRosterInspectores pure assertions) + diff proof (reuses callStickersApi/inspectoresCache, no forked client) | COMPLIANT (pure logic); live CRUD e2e MANUAL SMOKE PENDING |
| Inspector roster CRUD lives in Planeacion | Roster is absent from Stickers | Diff proof: stickers.js roster segment button, section, and all roster helpers (rowHtml/rosterListHtml/rosterHtml/filterInspectores/normalizeSearch) fully removed; segmented control is 2-way | COMPLIANT |
| Planeacion UI priority table/map/correction | Table ordered by priority / filtering / map legend / truncation / survey link / correction re-render | Unchanged code paths, not touched by this diff | INHERITED, unaffected |
| Planeacion UI | Roster available in a top-level tab, self-loaded | Diff proof: reload() calls renderInspectorRoster() unconditionally on every Planeacion tab open, independent of Stickers ever being opened | COMPLIANT |
| Planeacion UI | No individual-assignment control is reachable | Diff proof (git show 7ac81ea): cuadrilla combobox, data-desasignar button, and map Reasignar-a select markup all deleted; renderMap onReasignar param dropped; popupopen existing guard (if not sel return) confirmed present at line 1024, makes the missing markup a safe no-op | COMPLIANT |
| Planeacion UI | Group assignment still works after hiding individual controls | Diff proof: asignarGrupoAPuntos/desasignarGrupo (runGrupoAction) untouched by the diff | COMPLIANT (code-path proof); e2e MANUAL SMOKE PENDING |
| Scope boundaries | survey_cali / ArcGIS / dagma / Firestore-rule / pipeline-owned-field boundaries | Not touched by this diff (frontend-only change) | INHERITED, unaffected |
| Scope boundaries | Inspector-roster CRUD calls the existing Stickers endpoint, not a new one | Diff proof: roster segment create/setEnabled calls go through callStickersApi to api/stickers.js; confirmed backend/app/routers/planeacion_asignaciones.py has zero diff for this change | COMPLIANT |

Compliance summary: 8/8 in-scope scenarios COMPLIANT via diff/static proof or unit test (3 additionally pending live MANUAL SMOKE); 10/18 total scenarios for this delta are pre-existing/inherited and unaffected by this diff (they belong to the base planeacion-asignaciones change, not this one).

#### stickers-asignacion spec

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Mounted as sub-section | No new top-level tab / lazy init on first open / init runs once | Not touched by this diff (pre-existing showSegment/lazy-init logic unchanged) | INHERITED, unaffected |
| Mounted as sub-section | Segmented control is 2-way, not 3-way | Diff proof: shellHtml() now renders exactly Evaluaciones/Asignacion buttons; evaluaciones is the is-active default | COMPLIANT |
| CRUD affordances | Auto-agrupar / manual multi-select | Not touched by this diff | INHERITED, unaffected |
| CRUD affordances | Inspector dropdown fetches its own roster copy | Diff proof: stickers-asignacion.js gained a local callStickersApi + inspectoresCache/getInspectores getter; reload() now does Promise.all with callStickersApi list action; initStickersAsignacion in stickers.js dropped the getInspectores callback | COMPLIANT (code-path proof); live populate-without-Planeacion-first check MANUAL SMOKE PENDING |
| Scope boundaries | Evaluaciones collection never written / no public Firestore read rule | Not touched by this diff | INHERITED, unaffected |
| Scope boundaries | No inspector CRUD surface remains in Stickers | Diff proof: all roster CRUD helpers/markup removed from stickers.js; confirmed the escapeHtml import was correctly dropped alongside its only consumer (no dead import) | COMPLIANT |

Compliance summary: 3/3 in-scope scenarios COMPLIANT (1 additionally pending live MANUAL SMOKE); 7/10 total scenarios inherited/unaffected.

### Correctness (Static Evidence)

| Requirement | Status | Notes |
|---|---|---|
| Slice A vehiculo modal cleanup | Implemented | Exact deletions per ADR-3; buildConductorPayload correctly retained (used by Phase 4 conductor branch) |
| Hide individual assignment | Implemented | Exact deletions per ADR-4; mountCombobox/inspectorLabelFor correctly left as harmless dead code, not referenced elsewhere |
| Roster move Slice B | Implemented | finally block resetting busy and btn.disabled carried over verbatim (confirmed by direct read of the ported handler); filterRosterInspectores correctly does NOT collide with the module pre-existing filterInspectores; refreshInspectoresAfterWrite forces inspectoresLoaded false plus ensureInspectores so other Planeacion selects see new inspectors without a full reload |
| Usuarios fan-out Slice C | Implemented | payloadForTipo pure router matches design.md ADR-1 table exactly (endpoint/body/success-copy per tipo); callApi parameterized in place (endpointName defaults to usuarios) rather than forked, matching tasks.md 4.4 literal instruction; per-tipo error isolation confirmed by code inspection (no fallthrough, tipo-prefixed error, modal stays on same tipo) |
| sismocali.gov.co guard untouched | Implemented | git diff of api/usuarios.js across the whole change is empty; payloadForTipo client-side check is explicitly documented as additive UX, not a replacement |
| Two-backend fan-out wiring | Implemented | apiUrl stickers maps to /api/stickers, apiUrl planeacionAsignaciones maps to Railway FastAPI base, apiUrl usuarios maps to /api/usuarios, all confirmed against web/js/api-config.js actual map |
| firebase-config.js isolation | Implemented | Confirmed empty diff; lazy loadFirebaseAuth dynamic import scoped correctly to the two call sites that need it |

### Coherence (Design)

| Decision | Followed? | Notes |
|---|---|---|
| ADR-1 fan-out routing | Yes | Table matches implementation exactly |
| ADR-2 roster is a UI port, backend untouched | Yes | api/stickers.js diff confirmed empty |
| ADR-3 Slice A targeted deletions | Yes | |
| ADR-4 hide-only, guards cover the rest | Yes | popupopen existing guard verified in place at line 1024 |
| Phase ordering, 3 before 4 | Yes | Commit order matches: 69f2396 before 47ff9c8 |
| Testing Strategy: pure-helper unit tests plus explicit manual smoke for DOM/fan-out | Yes, honestly | No fake DOM tests were added to inflate coverage; the gap is documented, not hidden |
| Review Workload Forecast: Phase 3 over 400 lines needs full 4R | Not evidenced | No openspec/changes/usuarios-personas-unificadas/reviews directory exists; tasks.md itself calls for a full 4R sweep specifically on the Phase-3 PR given its forecast, but no review transaction/ledger/receipt was found for this change |

### Issues Found

CRITICAL: None.

WARNING:
1. Manual smoke not yet executed. Five tasks (1.4, 2.4, 3.5, 4.7, and the aggregate checklist) are checked complete in tasks.md but their own STATUS notes say manual smoke is pending user verification, and apply-progress.md restates the same as an explicit to-do list. This is honestly disclosed, not hidden, and matches this change own documented testing strategy (no headless-DOM harness exists) - but it means the DOM-level and live two-backend behavior (brigade-code display, Auth rollback, F5-toggle-busy regression, conductor duplicate-cedula error path, sismocali reject copy) has not been confirmed against a running browser/backend yet. Recommend the user perform the apply-progress.md checklist before this change is considered production-verified end to end.
2. No review-lens artifact found for Phase 3. tasks.md own Review Workload Forecast calls for a full 4R sweep (review-risk, review-resilience, review-readability, review-reliability) on the roster-move commit specifically, since it forecasts over 400 authored lines (actual: 606 changed lines in 69f2396, crossing the 400-line high-risk threshold from sdd-phase-common.md Review Workload Guard). No review artifacts exist under this change directory. Per the standing orchestrator rule, this review should run, or be explicitly validated as not-yet-required, before this change reaches a commit/push/PR/archive gate.

SUGGESTION:
1. apply-progress.md deviation note 4 flags the Usuarios modal header still saying "Nuevo usuario" instead of a tipo-neutral phrase - cosmetic only, already self-disclosed, no action required for this verify pass.
2. Consider adding the pre-existing evaluaciones.test.mjs CDN-import failure to a tracked known-issues note so node --test returns exit 0 for future CI gating - out of scope for this change (confirmed pre-existing at 575efdd), but its non-zero exit code currently masks whether a future regression in evaluaciones.test.mjs would be noticed.

### Verdict
PASS WITH WARNINGS

All 27 tasks are complete, all in-scope spec requirements are implemented correctly (verified by direct diff/code inspection, not by trusting apply claims), both untouched-file guarantees hold, the F5-toggle-busy fix was carried over verbatim, and both test suites (555 backend, 7-of-8 frontend with the 1 frontend failure independently confirmed pre-existing at the parent commit) pass as claimed. The two WARNINGs - manual smoke not yet executed, and no recorded review-lens pass for the over-400-line Phase 3 commit - are process gates for the orchestrator to close before archive, not defects in the shipped code.

---

## Archive-time addendum (2026-08-26)

Per the orchestrator: apply is complete (commits `5df7278`/`7ac81ea`/`69f2396`/`47ff9c8`), the
Phase-3 WARNING above (no review-lens artifact) has since been closed — reviewed with
reliability+readability lenses, fixes landed in `9208748` — and the change has shipped to
production. The manual-smoke WARNING remains a user-facing follow-up, not a blocker: no CRITICAL
findings exist at any point in this change's lifecycle, so the archive proceeds per the
Strict-vs-OpenSpec Archive Policy (CRITICAL-only hard block).
