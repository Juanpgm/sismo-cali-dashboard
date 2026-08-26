# Verify Report: Stickers -- cruce y asignacion

Change: stickers-asignacion - Project: seismic_disaster_data_analisys_cali - Phase: sdd-verify

## Scope verified

Checked out feat/stickers-asignacion-3-frontend (stacks on feat/stickers-asignacion-2-api, which
stacks on main) to see the full accumulated Phase 2 + Phase 3 diff. Phase 1
(integracion_F1/cruce_sticker.py, integracion_F1/job_sticker.py) verified directly in the
separate normalizador_data_sismo_cali repo checked out at integracion_F1/, confirmed committed
to that repo own main at commit 551a73a.

## Test/check evidence (all real, executed, not assumed)

| Command | Result |
|---|---|
| node --test "js/**/*.test.mjs" (from web/) | tests 6, pass 6, fail 0 -- includes stickers-asignacion.test.mjs (colorForPunto/buildRows/sortRows/filterRows) |
| node api/sticker-asignaciones.test.js | OK, exit 0 |
| node api/stickers.test.js | OK, exit 0 (regression, untouched by this change) |
| node api/usuarios.test.js | OK, exit 0 (regression, untouched by this change) |
| python cruce_sticker.py --check (from integracion_F1/) | OK, exit 0 |
| python job_sticker.py --check (from integracion_F1/) | OK, exit 0, delegates into the same self-check |

All 6 commands pass with exit code 0. No CRITICAL from test execution.

## Task completion cross-check

tasks.md: 26 checked, 4 unchecked. The 4 unchecked are exactly the pre-flagged known-open items,
confirmed still open and correctly unchecked:

- Task 0.2 (maxRadiusM/maxSize operator confirmation) -- open. Verified shipped as named constants
  DEFAULT_MAX_RADIUS_M = 800, DEFAULT_MAX_SIZE = 8 in api/sticker-asignaciones.js (not magic
  numbers), with a per-call override (body.maxRadiusM/body.maxSize) and a matching UI hint in
  stickers-asignacion.js. Not a bug -- a real pending operator decision.
- Task 1.7 (Railway cron service creation) -- open, manual/operator action, no repo diff expected or
  found. Confirmed integracion_F1/railway.json has no per-service startCommand/cronSchedule
  fields, consistent with the task own self-correction of design.md ADR-2.
- Task 3.9 (frontend manual browser smoke test) -- open, no live browser available to the apply agent.
  Confirmed real: node --test and node -e import(...) syntax/parse checks were run in place of
  actual DOM/browser execution; the code-read trace of the lazy-init guard (asignacionHandle) is
  accurate (see below). This is a genuine open item, not a code defect -- flagged plainly, not treated
  as blocking or silently passed.
- Task 4.1 (Firestore console rules) -- open by design, explicitly out of scope for any apply agent
  (console-only, no file in this repo governs sismo-agosto-sgred deployed ruleset). Confirmed no
  .rules file exists anywhere in this repo diff or working tree for this change.

Spot-checked several completed tasks against actual code -- no hallucination found:
- Task 1.2/1.5 (doc_id, PIPELINE_FIELDS/ADMIN_DEFAULT_FIELDS, build_write_ops) -- all present
  in integracion_F1/cruce_sticker.py, matching the claimed line-level behavior.
- Task 2.4/2.5 (autoAgrupar, haversineM, runAutoAgrupar) -- present in
  api/sticker-asignaciones.js with the exact ponytail: comment tasks.md specifies.
- Task 3.2/3.6/3.7 (segmented control, zero-diff index.html, lazy-init guard) -- all confirmed by
  direct source read (see Spec conformance below).

## Spec conformance (source-verified, not trust-the-report)

Merge safety (sticker_matches document ownership) -- cruce_sticker.py build_write_ops()
only ever emits PIPELINE_FIELDS, adding ADMIN_DEFAULT_FIELDS exclusively when the doc id is
absent from the pre-read existing_ids set; writes go through db.batch().set(doc_ref, fields,
merge=True), never a full-document set(). Confirmed by reading the function body and its own
self-check assertions.

autoAgrupar determinism -- api/sticker-asignaciones.js autoAgrupar() sorts points by
stable [lat, lon] comparator before any clustering, uses no RNG, and does a plain greedy
nearest-neighbor pass respecting maxRadiusM/maxSize. Confirmed by reading the function body; the
4-scenario self-check in api/sticker-asignaciones.test.js (determinism, maxSize cap,
maxRadiusM cap, empty input) passed at runtime.

Admin-only auth -- module.exports handler in api/sticker-asignaciones.js verifies the Bearer
ID token and checks roleFromClaims(claims) !== admin -> 403 BEFORE calling getAdmin() or
dispatching to any action handler, so no Firestore state can change on a rejected call. Byte-for-byte
mirrors api/stickers.js preamble as claimed.

Scope boundaries:
- Grepped api/sticker-asignaciones.js for collection(evaluaciones) -- zero matches.
- Grepped integracion_F1/cruce_sticker.py for evaluaciones -- all occurrences are reads
  (fetch_evaluaciones, build_addr_index, docstring/comments); no write call. Confirms the
  evaluaciones-collection-is-never-written scenario.
- No .rules file exists anywhere in this repo (git diff main...feat/stickers-asignacion-3-frontend
  --stat shows only api/, web/js/, web/styles.css, and openspec/ files touched -- no rules
  file, confirming Phase 4 stayed console-only as required).
- Grepped web/index.html for sticker -- only the pre-existing top-level .view-tab button and the
  empty #view-stickers section remain; index.html does not appear at all in the diff stat (true
  zero-diff, matching apply-progress claim exactly, not just a small diff).

Frontend CRUD scope (item 4 from the brief) -- re-read spec.md Requirement: CRUD affordances
in the frontend verbatim: it lists exactly 4 controls (Auto-agrupar button, manual multi-select to
Crear cuadrilla, assign/reassign inspector control calling asignarInspector/reasignarPunto).
Neither editarCuadrilla nor eliminarCuadrilla is mentioned by that requirement or by any of its
3 scenarios. The apply agent scope call was correct: shipping no frontend UI for those two
actions matches the locked spec text precisely -- this is not a gap, just an unused (but
already-implemented and tested) API surface for a later, undirected UI addition.

Segmented control / lazy init -- read web/js/stickers.js directly: shellHtml() renders the
3-way segmented control and three data-sticker-section wrappers; showSegment() only calls
initStickersAsignacion when asignacionHandle is still null, calling .reload() on every
subsequent open -- confirmed this exactly matches spec.md Lazy init on first Asignacion open and
Init runs once scenarios at the granularity the apply agent claimed (once per Stickers-tab-open
session, not once per browser session -- a reasonable, explicitly-documented reading since
roster/evaluaciones already re-init on every Stickers-tab open, pre-existing unrelated behavior).

Inspector roster reuse -- getInspectores callback reads inspectoresCache, which is set inside
stickers.js own roster reload() (the pre-existing action:list call). Grepped
stickers-asignacion.js -- no action:list or new roster fetch anywhere in the file. Confirms
the no-new-roster-fetch claim.

## Design coherence

5 ADRs referenced in design.md (field-group split, pipeline write path/cron wiring, API/clustering
algorithm shape, frontend CRUD/popup placement, sub-nav pattern) all traced against the actual code
and hold, with two known, already-documented corrections: ADR-2 railway.json claim (task 1.7,
self-corrected in tasks.md) and the request field-name choice for editarCuadrilla add/remove
(not specified verbatim by design.md, implementation detail only, no spec conflict).

## Issues

CRITICAL: None found.

WARNING:
- Task 3.9 (frontend manual smoke test) genuinely not performed -- real browser/DOM behavior (segment
  click-through, Leaflet map render/fitBounds/legend colors, network-tab single-call assertion, live
  CRUD round-trips) is unverified by any agent so far, apply or verify. Static analysis and the
  passing self-check cover the pure logic underneath it, not runtime DOM behavior.
- Task 1.7 (Railway cron creation) still open -- Phase 1 pipeline has never run against a real or
  emulated Firestore in any batch (no credentials in this environment, consistent through all 3
  apply batches and this verify pass).
- Task 0.2 (maxRadiusM/maxSize defaults) still open -- shipped as named placeholders, functionally
  fine, but represents an unconfirmed product decision.

SUGGESTION:
- editarCuadrilla/eliminarCuadrilla have working, tested API handlers with no frontend caller --
  fine per spec today, but worth a follow-up ticket if an admin ever needs to un-group or delete a
  cuadrilla from the UI instead of directly via API.

## Final verdict

PASS WITH WARNINGS. All 6 test/check suites pass for real, code inspection confirms every spec
requirement sampled (merge-safety, determinism, admin-only auth, scope boundaries, CRUD scope, lazy
init, roster reuse) is satisfied by the actual implementation, and all 26 checked tasks correspond to
real, verifiable code. The 4 unchecked tasks are exactly the pre-flagged, genuinely-open manual items
-- nothing new was discovered. This change is implementation-complete but not fully closed-out: it is
not ready for sdd-archive until 0.2/1.7/3.9/4.1 are resolved (3.9 needs a human/browser QA pass in
particular, since it is the only item with zero runtime coverage against real DOM/Firestore).
