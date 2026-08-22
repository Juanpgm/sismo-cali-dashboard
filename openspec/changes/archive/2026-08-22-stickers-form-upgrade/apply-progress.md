# Apply Progress: stickers-form-upgrade

Continuity contract for slice 2/3 apply batches. MERGE, do not overwrite.

## Mode

Strict TDD Mode: ACTIVE. Test runners: `cd formulario && node --test` (unit) and `cd formulario && npx playwright test` (e2e, config `formulario/playwright.config.js`).

## Slice 1: Code Assignment (PR 1) — COMPLETE

All 13 tasks (1.1–1.13) done. Commit: `7659e82` — `feat(formulario): derive sticker consecutive from records and allow segment edit` (6 files changed, 298 insertions(+), 47 deletions(-) — within the ~330-line slice-1 forecast).

### TDD Cycle Evidence

| Task | Behavior | RED | GREEN | REFACTOR |
|------|----------|-----|-------|----------|
| 1.1 | `firebase-mock.js`: collection/query/where/getDocs (dotted-path equality) | N/A (infra, exercised by 1.8 e2e RED) | Added; exercised and passing via e2e suite | — |
| 1.2–1.3 | `parseConsecutivo(codigo, codigoInspector)` | 3 unit tests added; confirmed failing with `SyntaxError: does not provide an export named 'parseConsecutivo'` (module-load failure since named import binds all three new symbols) | Implemented in `logic.js`; `node --test` → 25/25 pass | — |
| 1.4–1.5 | `siguienteConsecutivo(codigos, codigoInspector)` | 3 unit tests added; confirmed failing (same module-load mechanism, missing `siguienteConsecutivo` export) | Implemented; full suite green | — |
| 1.6–1.7 | `validarSegmento(raw)` | 5 unit tests added; confirmed failing (missing `validarSegmento` export) | Implemented; full suite green (25/25) | — |
| 1.8–1.10 | e2e: split `#codigo-display` assertions (lines 69, 97 in the pre-change file) into `#codigo-prefijo`/`#codigo-consecutivo`; new scenarios (gap→0004, abandon-no-consume, edited-segment-persists, duplicate-edit-preserves-data); markup + `generarCodigo` rewrite | Ran `npx playwright test` against old markup/form.js with new spec assertions: **8 failed / 6 passed** (confirms RED — all 8 failures are the new/changed assertions expecting `#codigo-prefijo`/`#codigo-consecutivo`, which did not exist yet) | Applied `index.html` (1.9) + `form.js` (1.10) together (atomic markup+behavior change per design finding #3); re-ran → **14/14 passed** | — |

Note on RED granularity: for 1.2–1.7, RED was verified once per named-export group rather than per individual assertion, because Node's ESM loader fails the entire test file at import time when a named export is missing (a `SyntaxError`, not a per-test assertion failure) — this is the earliest observable failure mode for a not-yet-implemented named export in this test file's import style. Each implementation task (1.3, 1.5, 1.7) was confirmed GREEN by running the full suite immediately after.

### Final Verification (this batch)

- `cd formulario && node --test test/logic.test.mjs` → **25 pass, 0 fail** (includes the pre-existing width-widening test `consecutivo > 9999 desborda el ancho fijo` unchanged/green, and all new `parseConsecutivo`/`siguienteConsecutivo`/`validarSegmento` cases).
- `cd formulario && npx playwright test` → **14 passed** (11 pre-existing scenarios adapted + 3 new describe blocks: "Consecutivo derivado de registros" ×2, "Segmento editable del código" ×1; "Recuperación ante código duplicado" rewritten for the new non-destructive collision UX).
- Playwright chromium browser and `@playwright/test` devDependency were not present in the environment; installed via `npm install` (formulario/package.json devDependency, gitignored `node_modules/`/`package-lock.json`) and `npx playwright install chromium` before running e2e, per apply-phase instructions.

### Files Changed (commit `7659e82`)

| File | Action | What Was Done |
|------|--------|----------------|
| `formulario/js/logic.js` | Modified | Added `parseConsecutivo`, `siguienteConsecutivo`, `validarSegmento` pure functions. `buildCodigo` untouched. |
| `formulario/test/logic.test.mjs` | Modified | 11 new unit tests for the three new functions (RED written before each implementation). |
| `formulario/e2e/firebase-mock.js` | Modified | Added `collection`, `query`, `where`, `getDocs` (dotted-path equality filter) to the in-memory Firestore mock. |
| `formulario/e2e/atc20.spec.js` | Modified | Split `#codigo-display` text assertions into `#codigo-prefijo` + `#codigo-consecutivo` checks everywhere the markup change affects them (not only the two lines called out in the task, since the element no longer carries the full code as text anywhere); added 3 new describe blocks; rewrote the duplicate-collision test for the new non-destructive UX; added `written.consecutivo` assertions to the full-flow tests. |
| `formulario/index.html` | Modified | `#codigo-display` is now a wrapper `<p>` containing `<span id="codigo-prefijo">` + `<input id="codigo-consecutivo" inputmode="numeric" pattern="[0-9]*" maxlength="4">`. |
| `formulario/js/form.js` | Modified | Rewrote `generarCodigo()`: session-cached `state.maxConsecutivo`, `getDocs(query(collection('evaluaciones'), where('inspector.uid','==',uid)))` run at most once per session, next value derived via `siguienteConsecutivo`. Deleted the `inspectores/{uid}` read-increment-write transaction. Added `renderCodigo()`, `validarSegmentoInput()` (wired on blur and pre-submit). Added pre-submit `getDoc` existence check ahead of photo upload. `data.consecutivo` (numeric) now persisted on create via `parseConsecutivo`. Collision (`codigo-duplicado`) handling rewritten: invalidates cache, re-derives, re-renders the code in place, keeps all entered data/photos, area/button stay locked (previously wiped the code and re-enabled the area). `nuevoRegistro()` updated for the new markup (resets `#codigo-prefijo`/`#codigo-consecutivo`) but intentionally does NOT reset `state.maxConsecutivo` (session-scoped, not per-record). |

### Deviations from Design

- **None functionally.** One naming note: `specs/sticker-code-assignment/spec.md`'s "Testability Notes" section names the pure helpers `parseConsecutive`, `computeNextConsecutive`, `validateCodeSegment`, `rebuildCodeWithSegment` (English), while `design.md`'s "Interfaces / Contracts" section and `tasks.md` both consistently specify `parseConsecutivo`, `siguienteConsecutivo`, `validarSegmento` (Spanish, matching the rest of the codebase's function-naming convention — e.g. `sugerirClasificacion`, `buildCodigo`). Implemented per `design.md`/`tasks.md` (the more detailed, mutually-consistent, and codebase-convention-matching source). **Flag for verify phase**: spec.md's Testability Notes section has stale/inconsistent naming versus design.md and should be corrected in a spec follow-up, not a code change.
- Did not implement the "below-next edit is permitted with a non-blocking hint" scenario from `specs/sticker-code-assignment/spec.md` (Editable Last-4-Digits Segment requirement). This behavior has no corresponding task in `tasks.md` Slice 1 (1.1–1.13 cover validation/rejection paths only, not the non-blocking-hint UX), so implementing it would have meant adding an untasked behavior without a preceding RED test in this batch — out of strict-TDD and out-of-scope-tasks bounds. **Flag for verify phase**: this is a spec-scenario vs. tasks-breakdown gap, not an implementation defect.
- Duplicate-collision Spanish message changed from "El código ya existe. Genere un nuevo código e intente de nuevo." (old, since removed) to "El código ya existe. Se generó uno nuevo automáticamente; revise y envíe de nuevo." — required by the new non-destructive UX (design: "invalidates the cache, re-derives the next value, prefills the input with it, and shows a Spanish message"); the old message no longer matches the new behavior (nothing to "generate" manually, it's automatic).
- Extended CSS was NOT touched in slice 1 (no CSS task assigned this slice — `.codigo-display`/`input[type="text"]` existing rules apply adequately to the new wrapper+input combo; a dedicated grid/tile CSS task is scheduled for slice 2 per `tasks.md`).

### Issues Found

None blocking. `firebase-mock.js`'s `flags` object already existed (`window.__fb.flags`) but was unused by any current mock function except the pre-existing `failUpload` flag on the storage mock (irrelevant here, storage mock is unused by this app which uses the S3 signer, not Firebase Storage) — left untouched, no new flags added in this slice.

### Remaining Tasks (Slice 3 — NOT started)

- [ ] 3.1–3.12 (Slice 3: Session Resilience and Perf, PR 3) — untouched.

### Workload / PR Boundary

- Mode: chained PR slice (`auto-chain` / `stacked-to-main`, per orchestrator delivery decision).
- Current work unit: Slice 1 — Code Assignment (PR 1).
- Boundary: starts from `main` (branch `feature/stickers-form-upgrade`), ends at commit `7659e82`. Clean rollback via `git revert 7659e82` (per design: "Per-slice `git revert` is a clean rollback" — the deleted `inspectores/{uid}` counter field is never removed from Firestore, only stopped being written, so reverting restores old behavior exactly).
- Estimated review budget impact: 298 insertions + 47 deletions = 345 changed lines, within the ~330-line slice-1 forecast and under the 400-line reviewer budget on its own.

### Status

13/13 slice-1 tasks complete (36 total tasks across all 3 slices; slice 1 = tasks 1.1–1.13). Ready for `sdd-apply` slice 2, or `sdd-verify` if the orchestrator wants to verify slice 1 alone before continuing.

---

## Slice 2: Photo Capture (PR 2) — COMPLETE

All 12 tasks (2.1–2.12) done. Commit: `8e8a885` — `feat(formulario): dynamic photo capture via gallery/camera with parallel upload` (8 files changed, 353 insertions(+), 115 deletions(-)).

### Task 2.1 — Signer Probe (BLOCKING gate)

Probed `https://sismo-fotos-signer.vercel.app/api/sign` with `curl` before any UI work, per the apply-phase gate. Sign-only requests, no PUT, no real file uploaded (a dummy 1x1-style workload was never even needed — the signer rejected before reaching the token/PUT stage for high slots):

| Request (`idToken` deliberately invalid) | `slot` | Response |
|---|---|---|
| `POST /api/sign` | `1` | `401 {"error":"invalid-token"}` |
| `POST /api/sign` | `3` | `401 {"error":"invalid-token"}` |
| `POST /api/sign` | `4` | `400 {"error":"bad-request"}` |
| `POST /api/sign` | `10` | `400 {"error":"bad-request"}` |
| `POST /api/sign` (no `idToken`, `slot:10`) | `10` | `400 {"error":"bad-request"}` |
| `POST /api/sign` (no `codigo`, `slot:10`, dummy token) | `10` | `400 {"error":"bad-request"}` |

**Interpretation**: `slot` 1 and 3 pass request-schema validation and fail only at the (expected, deliberately-broken) token check → `401`. `slot` 4 and 10 are rejected **before** the token is even checked → `400 bad-request`, i.e. the signer validates `slot` against a server-side `1..3` range as part of request-schema validation, independent of auth. **Conclusion: the signer does not accept `slot > 3`.**

**Decision**: `MAX_FOTOS = 3` (cap-at-3 fallback, per design decision "Slot-Generic Design With Capped Fallback" and spec requirement "Slot-Generic Design With Capped Fallback"). Recorded in `formulario/SETUP.md` (new "§7 Límite de fotos por registro" section) and in `formulario/js/logic.js` as a module constant with an explanatory comment. The rest of slice 2 (gallery/camera sourcing, dynamic grid, parallel upload with concurrency cap) shipped unaffected — only the visible slot ceiling is 3 instead of 10. If the signer is later fixed to accept `slot` up to 10, only the `MAX_FOTOS` constant needs to change (no other file assumes the value 3; `canAddSlot`, `renderFotos`, and the worker pool are all written against the constant, not a hardcoded number).

### TDD Cycle Evidence

| Task | Behavior | RED | GREEN | REFACTOR |
|------|----------|-----|-------|----------|
| 2.1 | Signer probe (manual, not TDD — a research/gate task) | N/A | N/A | N/A |
| 2.2–2.3 | `canAddSlot(current, max)` | 3 unit tests added; confirmed failing with `SyntaxError: does not provide an export named 'canAddSlot'` | Implemented `canAddSlot` + `MAX_FOTOS` in `logic.js`; `node --test` → 28/28 pass | — |
| 2.4–2.7 | Dynamic photo grid: gallery/camera sourcing, multi-select ordering, removal reordering, hard cap at `MAX_FOTOS` | New "Fotos: galería y cámara" describe block (4 scenarios) run against the OLD markup (`.foto-slot` × 3, no `#foto-galeria`/`#foto-camara`) → **8 failed / 10 passed** (all 8 failures are the new/changed photo assertions — the new selectors did not exist yet, and `addFoto`'s helper rewrite to use `#foto-galeria` also broke every other test that attaches a photo, confirming the shared-helper coupling is real) | Applied `index.html` (2.5) + `form.js` (2.6) + `form.css` (2.7) together (same atomic-markup-and-behavior rationale as slice 1's finding #3 — a dynamic grid can't be demoed/tested piecemeal); re-ran → **18/18 passed**. One extra fix needed mid-GREEN: `.btn-secondary` sets `display:block`, which overrides the UA `[hidden]` stylesheet rule (author CSS always wins), so the new `#btn-foto-galeria`/`#btn-foto-camara` buttons stayed visually visible despite the `hidden` attribute being set correctly by JS — added an explicit `.foto-action-btn[hidden] { display: none; }` override. | — |
| 2.8–2.9 | Upload concurrency cap (≤3 in flight, parallel not sequential) + cache-reuse-on-retry | Added a "Subida de fotos: concurrencia y caché" describe block with a Node-side in-flight counter in the mocked signer route (real network delay via `setTimeout` to make overlap observable) and a one-shot `failTransactionOnce` mock flag (added to `firebase-mock.js`'s `runTransaction`) to force a generic post-upload write failure for the cache-reuse scenario. Ran against the sequential `for`-loop upload (task 2.6's interim implementation) → **concurrency test failed** (`maxInFlight` was 1, expected `>1`); the cache-reuse test already passed (task 2.6's cache-key change alone was sufficient for that one). | Replaced the sequential loop with a `limit`-worker pool (`Promise.all` over `Math.min(limit, fotos.length)` workers pulling from a shared `next` index, `limit` default 3) in `subirFotos`; re-ran → both pass, `maxInFlight` reaches 3. | — |
| 2.10–2.11 | Full regression | — | `node --test`: 28/28 pass. `npx playwright test`: 20/20 pass (18 pre-existing/adapted + 2 new: concurrency, cache-reuse). | — |
| 2.12 | Conventional commit | — | `8e8a885` | — |

Note on RED granularity (mirrors slice 1's note): 2.2's RED was verified once at the named-export level (`SyntaxError` on missing `canAddSlot` export fails the whole test file at import time), not per-assertion — same earliest-observable-failure reasoning as slice 1.

### Final Verification (this batch)

- `cd formulario && node --test test/logic.test.mjs` → **28 pass, 0 fail** (25 from slice 1 + 3 new `canAddSlot` cases).
- `cd formulario && npx playwright test` → **20 passed** (18 adapted/pre-existing + 2 new: "sube hasta 3 fotos en paralelo, no de a una", "una foto ya subida no se vuelve a subir en un reintento").
- Playwright chromium and `@playwright/test` were already installed from slice 1 (gitignored `formulario/node_modules/`); no reinstall needed this batch.

### Files Changed (commit `8e8a885`)

| File | Action | What Was Done |
|------|--------|----------------|
| `formulario/js/logic.js` | Modified | Added `MAX_FOTOS = 3` constant (value from the 2.1 probe) and pure `canAddSlot(current, max = MAX_FOTOS)`. |
| `formulario/test/logic.test.mjs` | Modified | 3 new unit tests for `canAddSlot` (RED written before implementation). |
| `formulario/e2e/firebase-mock.js` | Modified | Added a one-shot `window.__fb.flags.failTransactionOnce` check inside `runTransaction` — throws a generic (non-`codigo-duplicado`) error once, then clears itself, so a spec can exercise "photos already uploaded and cached, but the doc write still failed for an unrelated reason." |
| `formulario/e2e/atc20.spec.js` | Modified | Rewrote `addFoto`/added `addFotosGaleria` helpers to use `#foto-galeria` multi-select instead of `.foto-slot` per-slot inputs; updated the one direct `.foto-slot` usage in "Flujo completo" to use the helper; added "Fotos: galería y cámara" (4 scenarios, adapted from the spec's 10-slot scenarios to the shipped 3-slot cap) and "Subida de fotos: concurrencia y caché" (2 scenarios: parallel-upload timing via a Node-side in-flight counter with artificial delay, and cache-reuse-on-retry via the new `failTransactionOnce` flag) describe blocks. |
| `formulario/index.html` | Modified | Removed the 3 hardcoded `.foto-slot` divs. Added `#fotos-grid` (empty container, rebuilt by JS), a `.foto-actions` row with `#btn-foto-galeria` ("Agregar fotos") / `#btn-foto-camara` ("Tomar foto"), `#foto-max-msg` (hidden, Spanish max-reached message), and the two shared hidden inputs `#foto-galeria` (`accept="image/*" multiple`, no `capture`) / `#foto-camara` (`accept="image/*" capture="environment"`). |
| `formulario/js/form.js` | Modified | `state.fotos` is now a dense `{file, previewUrl}[]` (was `[null,null,null]` fixed-slot). New `wirePhotos()` wires the two buttons + two hidden inputs to a shared `addFotos(fileList)` (drops files beyond remaining capacity, per spec "up to the remaining slot capacity"); `renderFotos()` rebuilds `#fotos-grid` from state and toggles the add-buttons/`#foto-max-msg` via `canAddSlot`; `removeFoto(index)` splices + revokes the object URL, closing any gap. `validate()`'s photo check changed from `.some(Boolean)` to `.length === 0`. New `subirFotos(fotos, limit=3)` worker-pool upload (replaces the old per-slot sequential loop) with index-ordered results; cache key dropped the slot (`` `${codigo}:${name}:${size}:${lastModified}` ``, was `` `${codigo}:${slot}:${name}:${size}:${lastModified}` ``); on a signer `400` for `slot > 3` (defensive — unreachable in normal use since `MAX_FOTOS=3`, but guards against a future regression), throws a distinguishable `signer-slot-limit` error surfaced as "Este dispositivo solo admite 3 fotos por registro" instead of the generic upload-failure message. |
| `formulario/css/form.css` | Modified | `.fotos-grid` changed from a fixed `repeat(3, 1fr)` to `repeat(auto-fill, minmax(88px, 1fr))`; renamed slot-tile styles from `.foto-slot`/`.foto-add` to `.foto-tile`/`.foto-actions`/`.foto-action-btn`; added `.foto-action-btn[hidden] { display: none; }` to fix the `.btn-secondary`-overrides-`[hidden]` bug found during 2.5–2.7 GREEN. |
| `formulario/SETUP.md` | Modified | New "§7 Límite de fotos por registro" section with the raw 2.1 probe table and conclusion (see above). |

### Deviations from Design

- **`MAX_FOTOS` resolved to 3, not 10.** This is not a deviation from design — design.md explicitly anticipates both outcomes ("Apply MUST start with a manual `slot: 10` probe... and set the constant to 10 or 3 accordingly") — but it cascades into several task-wording adaptations, flagged here for the verify phase:
  - **Task 2.4 / spec.md's "Up To 10 Dynamic Photo Slots" scenarios**: the literal scenarios ("3 photos attached, add a 4th → 4th slot renders", "10 attached, 11th prevented") are unreachable under `MAX_FOTOS=3` (the 2nd scenario *is* the 1st scenario once the cap is 3). Adapted to: multi-select 2/3 in one action → correct count and order; remove one → remaining reorder with no gap; reaching the cap (3) hides the add buttons and shows the Spanish max message; selecting more than remaining capacity in one action only adds up to the cap. All four are still real Playwright e2e coverage of the same underlying requirement ("slot-generic design, capped fallback"), just at the actual shipped ceiling instead of a now-hypothetical one.
  - **Task 2.8 / spec.md's "Concurrency Respected" scenario** ("GIVEN 9 photos... at most 3 uploads in flight"): 9 photos can never be attached through the real UI once `MAX_FOTOS=3` (`canAddSlot` blocks the 4th add attempt in the browser). Adapted to 3 photos — the maximum reachable — which still proves (a) uploads run in parallel, not sequentially (`maxInFlight > 1`, using an artificial delay in the mocked signer route to make overlap observable), and (b) the pool never exceeds the cap (`maxInFlight <= 3`). This is a weaker upper-bound proof than a true 9-vs-3 test would be (it can't demonstrate queuing behavop beyond the cap, since there's nothing to queue), but the worker-pool implementation itself (`Math.min(limit, fotos.length)` workers pulling from a shared index) is limit-generic — it does not special-case the input length — so this is judged an acceptable adaptation rather than a real coverage gap. Flagged for verify phase to review.
  - **Task 2.12's commit message**: tasks.md specifies `feat(formulario): support up to 10 photos with gallery/camera and parallel upload`. Committed as `feat(formulario): dynamic photo capture via gallery/camera with parallel upload` instead — "support up to 10 photos" would misrepresent the shipped behavior (visible cap is 3). The commit body documents both the design capability (architecture is 10-capable via one constant) and the actual shipped cap, so the "why" is fully preserved; only the literal number in the subject line was corrected.
- **Defensive `signer-slot-limit` fallback (design D3) is currently dead code in normal operation**: since `MAX_FOTOS=3` is enforced client-side by `canAddSlot`, the client can never legitimately construct a `slot > 3` request. The fallback (distinguishing a `400` on `slot > 3` from other sign failures, per design: "a sign failure on `slot > 3` surfaces a specific Spanish message") is implemented anyway as defense-in-depth against a future regression (e.g. if `MAX_FOTOS` is bumped without re-probing the signer). Not exercised by any e2e test in this slice (would require deliberately desyncing `MAX_FOTOS` from the enforced cap, which felt like testing a hypothetical rather than current behavior) — noted as an untested-but-implemented branch for verify/future review.
- **Extended CSS beyond the task's literal list**: task 2.7 said "`auto-fill minmax(88px, 1fr)` responsive grid, tile styles, action-button row" — implemented plus one unplanned fix (`.foto-action-btn[hidden] { display: none; }`) discovered during GREEN verification (the `.btn-secondary` class's `display: block` was silently defeating the native `[hidden]` attribute behavior on the new action buttons). Documented as a "REFACTOR" note in the TDD evidence table above rather than a separate task since it was required to make 2.4's own RED tests pass GREEN, not a new behavior.

### Issues Found

- The `.btn-secondary[hidden]`/`display:block` interaction above is a latent gotcha worth remembering for any *other* future `hidden`-toggled element that also carries a `display`-setting class in this codebase (`.btn-primary`, `.auth-submit` have the same `display: block` pattern) — none of them are currently toggled via the `hidden` attribute elsewhere in the app, so no other instance needed fixing, but a future feature that does would hit the same bug.
- None blocking.

### Workload / PR Boundary

- Mode: chained PR slice (`auto-chain` / `stacked-to-main`, per orchestrator delivery decision).
- Current work unit: Slice 2 — Photo Capture (PR 2).
- Boundary: starts from `main` (branch `feature/stickers-form-upgrade`, stacked on top of slice 1's `7659e82`), ends at commit `8e8a885`. Clean rollback via `git revert 8e8a885` — no Firestore schema/rules change in this slice (the `fotos` array shape/content is unchanged: still an ordered array of public URLs), so a revert is a pure client-code rollback with no backend cleanup needed.
- Estimated review budget impact: 353 insertions + 115 deletions = 468 changed lines — **above** the 400-line reviewer budget and above the ~280-line slice-2 forecast in tasks.md's Review Workload Forecast. The overage is almost entirely `formulario/js/form.js` (203 changed lines: full photo subsystem rewrite is inherently one cohesive unit — state model, wiring, rendering, and upload pool all change together) and `formulario/e2e/atc20.spec.js` (128 changed lines: 6 new scenarios plus the shared-helper rewrite that a fair number of *other* existing tests depend on). Per `delivery_strategy: auto-chain` / `chain_strategy: stacked-to-main`, this is already the designated per-slice PR boundary (slice 1 / slice 2 / slice 3 = PR 1 / PR 2 / PR 3); no further sub-slicing was attempted within slice 2 because task 2.12 specifies a single commit and the photo subsystem does not have an obviously smaller independently-shippable/revertible sub-unit (markup, state, rendering, and upload are interdependent — see the "atomic markup+behavior" TDD note above). **Flagged for the orchestrator/reviewer**: this slice exceeds the reviewer budget; if strict enforcement is required, splitting `subirFotos`'s worker-pool (2.8–2.9) into its own follow-up commit/PR on top of the grid-and-sourcing commit (2.2–2.7) is the most natural cut point, but was not done here since task 2.12 called for one commit and no explicit exception was requested before this batch started.

### Status

25/36 tasks complete (13 slice 1 + 12 slice 2). Ready for `sdd-apply` slice 3 (tasks 3.1–3.12), or `sdd-verify` if the orchestrator wants to verify slices 1+2 before continuing. **Note the workload/PR-boundary flag above** — slice 2 landed at 468 changed lines, over the 400-line budget and over its own ~280-line forecast; recommend the orchestrator/user confirm whether this is accepted as-is (`size:exception` after the fact) or whether a follow-up split is wanted before slice 3 starts.

---

## Slice 3: Session Resilience and Perf (PR 3) — COMPLETE

All 12 tasks (3.1–3.12) done. Commit: `f70907c` — `feat(formulario): retry transient profile reads and dedupe Firebase imports` (8 files changed, 224 insertions(+), 20 deletions(-) — well under the 400-line budget; no `size:exception` needed).

### TDD Cycle Evidence

| Task | Behavior | RED | GREEN | REFACTOR |
|------|----------|-----|-------|----------|
| 3.1–3.2 | `clasificarErrorFirestore(err)` | 6 unit tests added; confirmed failing with `SyntaxError: does not provide an export named 'backoffDelay'` (module-load failure — same named-export-group mechanism as slices 1/2, both new exports were added to the same import statement) | Implemented in `logic.js`; `node --test` → 36/36 pass | — |
| 3.3–3.4 | `backoffDelay(attempt, base=600)` | 2 unit tests added; confirmed failing via the same module-load `SyntaxError` as above (verified jointly with 3.1–3.2's RED run, since both missing exports were imported in one statement) | Implemented; full suite green | — |
| 3.5–3.6 | Auth session resilience: transient retry-then-boot vs. fatal immediate sign-out | Added a "Sesión: reintento del perfil ante fallas transitorias" describe block (2 scenarios) and a `firebase-mock.js` `getDoc` failure-injection queue (`window.__fb.flags.getDocFailQueue`, scoped to the `inspectores` collection only). Ran against the pre-change `auth.js` → **1 failed / 1 passed**: the transient scenario failed as expected (old code signs out unconditionally on any `getDoc` throw, `#app` stayed hidden after the 10s timeout); the fatal (`permission-denied`) scenario already passed by coincidence (old code already signs out unconditionally on any read error, which happens to match the *fatal* branch's required behavior) — this one is a regression guard confirming the pre-existing "sign out on fatal" behavior survives the retry rewrite, not a new-behavior RED/GREEN pair. | Rewrote `auth.js`'s profile-check into `checkProfile(user)` + `readProfileWithRetry(db, uid)` (up to 3 attempts, `backoffDelay` between retries, immediate re-throw on `clasificarErrorFirestore(err) === 'fatal'`); added the `#auth-retry` "Reintentar" button (hidden by default) wired to re-run `checkProfile` for the last-seen user; re-ran both scenarios → **2/2 passed**. | — |
| 3.7 | Import dedupe (form.js ← auth.js only) | No new test — this is a pure import-source change with no behavior difference (module URL resolution already deduped the fetch per design's "corrected rationale"); full regression suite is the verification. | `form.js` now imports `initAuth, getApp, getDb, getAuth, collection, doc, getDoc, getDocs, query, runTransaction, serverTimestamp, where` from `./auth.js` only; the two direct gstatic CDN import statements were removed. `node --test` unaffected (pure-logic layer untouched); full e2e suite re-run to confirm no wiring regression. | — |
| 3.8 | `index.html` preconnect/modulepreload | No test — markup-only `<head>` addition, not exercised by Playwright assertions (no e2e scenario asserts on `<link>` presence in this slice, consistent with slice 1's precedent of not testing markup that has no behavior to assert against). Verified by full e2e suite still passing (mocked GSTATIC routes still intercept the same three module URLs; `modulepreload` does not change which script tag actually executes the module — `<script type="module" src="js/form.js">` still owns the real import graph). | Added `<link rel="preconnect">` + 3 `<link rel="modulepreload">` tags in `<head>`. | — |
| 3.9 | `SETUP.md` documentation | N/A (docs) | Added §8 (rollout order across the three slices, optional post-rollout `inspectores` rule tightening) and §9 (session-resilience behavior summary for field-inspector-facing operators). | — |
| 3.10–3.11 | Full regression | — | `node --test`: 36/36 pass. `npx playwright test`: 22/22 pass (20 pre-existing/adapted + 2 new: transient-retry-boots, fatal-signs-out-immediately). | — |
| 3.12 | Conventional commit | — | `f70907c` | — |

Note on RED granularity (mirrors slices 1–2's note): 3.1–3.4's RED was verified once at the named-export-group level (a single `SyntaxError` on the missing `backoffDelay` export fails the whole test file at import time, since both new names were added to one `import { ... } from '../js/logic.js'` statement) rather than per-assertion — same earliest-observable-failure reasoning as prior slices.

### Final Verification (this batch)

- `cd formulario && node --test test/logic.test.mjs` → **36 pass, 0 fail** (28 from slices 1–2 + 6 `clasificarErrorFirestore` cases + 2 `backoffDelay` cases).
- `cd formulario && npx playwright test` → **22 passed** (20 pre-existing/adapted from slices 1–2, unchanged and still green + 2 new: "una falla transitoria en la lectura del perfil no cierra la sesión (reintenta y arranca)", "una falla permission-denied en la lectura del perfil cierra la sesión de inmediato").
- Playwright chromium and `@playwright/test` were already installed from slice 1 (gitignored `formulario/node_modules/`); no reinstall needed this batch.
- Both commands re-run fresh immediately before the commit (not reused from the RED/GREEN runs) per verification-before-completion — output above is from that final run.

### Files Changed (commit `f70907c`)

| File | Action | What Was Done |
|------|--------|----------------|
| `formulario/js/logic.js` | Modified | Added `clasificarErrorFirestore(err)` (`Set`-based fatal/transient classification; fatal = `permission-denied`/`not-found`; everything else, including unknown/missing codes, is transient — fails open) and `backoffDelay(attempt, base=600)` (`base * 3^(attempt-1)`). |
| `formulario/test/logic.test.mjs` | Modified | 8 new unit tests (6 `clasificarErrorFirestore`, 2 `backoffDelay`), RED written before implementation. |
| `formulario/js/auth.js` | Modified | Added `readProfileWithRetry(db, uid)` (up to 3 attempts, `sleep(backoffDelay(attempt))` between retries, immediate re-throw on fatal classification). Extracted the profile-check block into `checkProfile(user)` so the new `#auth-retry` button can re-run the exact same check for the last-seen user (`retryUser` closure variable, cleared on success/fatal/sign-out). Removed the unconditional `signOut` on any `getDoc` throw; transient exhaustion now shows a Spanish message + the retry button instead. Re-exports `getAuth, doc, getDoc, getDocs, collection, query, where, runTransaction, serverTimestamp` so `form.js` has a single Firebase import boundary. Added a `#auth-retry` button (hidden by default) to the login overlay markup. |
| `formulario/js/form.js` | Modified | Imports `initAuth, getApp, getDb, getAuth, collection, doc, getDoc, getDocs, query, runTransaction, serverTimestamp, where` from `./auth.js` only; removed the two direct gstatic CDN import statements (`firebase-firestore.js`, `firebase-auth.js`). No behavior change — same bindings, single import source. |
| `formulario/index.html` | Modified | Added `<link rel="preconnect" href="https://www.gstatic.com">` and 3 `<link rel="modulepreload">` tags (app/auth/firestore) in `<head>`. |
| `formulario/e2e/firebase-mock.js` | Modified | `getDoc` now checks `window.__fb.flags.getDocFailQueue` (an array of error codes) when `ref._coll === 'inspectores'`; shifts and throws the next queued code as `{ code }`, otherwise resolves normally. Scoped to `inspectores` only so it does not interfere with the unrelated `evaluaciones` pre-submit existence check in `form.js`. |
| `formulario/e2e/atc20.spec.js` | Modified | Added a "Sesión: reintento del perfil ante fallas transitorias" describe block (2 scenarios) placed after "Autenticación" and before "Código de la edificación". |
| `formulario/SETUP.md` | Modified | Added §8 (three-slice rollout order, each independently revertible; optional post-rollout `inspectores.consecutivo` rule tightening, not required by this change) and §9 (plain-language summary of the retry-vs-sign-out behavior for whoever operates the deployed form). |

### Deviations from Design

- **Naming**: `spec.md`'s "Testability Notes" section names the pure helper `classifyAuthError` (English), while `design.md`'s "Interfaces / Contracts" section and `tasks.md` both consistently specify `clasificarErrorFirestore` (Spanish, matching `parseConsecutivo`/`siguienteConsecutivo`/`validarSegmento`/`canAddSlot` from slices 1–2). Implemented per `design.md`/`tasks.md`, same precedent as slice 1's `parseConsecutivo` naming note. **Flag for verify phase**: `spec.md`'s Testability Notes section has stale/inconsistent naming versus `design.md` for a second helper now (first was the slice-1 code-assignment helpers); both should be corrected together in a spec follow-up, not a code change.
- **E2E scope narrower than `spec.md`'s full requirement set**: `tasks.md`'s task 3.5 lists exactly 2 e2e scenarios (transient-retries-then-boots, fatal-signs-out-immediately). `spec.md`'s "Transient-Error Retry Instead Of Sign-Out" requirement also describes a third scenario — "Retries exhausted still avoids forced logout" (the `#auth-retry` "Reintentar" affordance actually appearing and being usable) — which has no corresponding task in `tasks.md`. Per the same reasoning as slice 1's "non-blocking hint" gap (implementing untasked behavior without a preceding RED test would be out-of-scope-tasks and out of strict-TDD bounds for this batch): the `#auth-retry` button **is implemented** (task 3.6 explicitly requires it, independent of e2e coverage), but its exhaustion path is **not exercised by an e2e test** in this slice. Manually reasoned-through instead: `readProfileWithRetry` re-throws `lastErr` after 3 failed attempts when none are fatal; `checkProfile`'s catch branch classifies that error, and since it is not fatal, sets `retryUser`, shows the Spanish message, and calls `showRetry(overlay, true)` — the same code path already covered indirectly by the 2-fails-then-succeed test (which exercises 2 of the 3 attempts and the same retry loop, just not the terminal un-recovered branch). **Flagged for verify phase**: this is a spec-scenario vs. tasks-breakdown gap, not an implementation defect — consistent with the slice-1 precedent for handling this exact situation.
- **Fatal e2e scenario was already-passing before the change ("accidental GREEN")**: noted explicitly in the TDD Cycle Evidence table above. The pre-change `auth.js` already called `signOut` unconditionally on any `getDoc` throw, which happens to satisfy "fatal errors sign out immediately" without needing the new classification logic. This scenario functions as a **regression guard** proving the retry rewrite didn't accidentally start retrying fatal errors too, not as a RED-to-GREEN demonstration of new behavior. Documented here rather than silently treated as if it were a normal RED cycle.
- **`no-await-in-loop` inline disable comments** in `readProfileWithRetry`: the retry loop is a sequential-by-design `for` loop (each attempt must complete, including its backoff wait, before the next begins) — a `Promise.all`/parallel rewrite would change the behavior (all 3 attempts firing at once defeats the purpose of backoff). No project ESLint config was found to confirm this project actually runs that specific rule, so the comments are a defensive/documentary no-op if the rule isn't active, and a correct suppression if it is. Not a design deviation, just implementation hygiene worth flagging.

### Issues Found

None blocking. The `.btn-secondary[hidden]`/`display:block` gotcha from slice 2 did not resurface here — no new `hidden`-toggled element in this slice carries a `display`-setting class (`#auth-retry` has no CSS class at all yet, so it inherits default `<button>` display; if a future pass styles it with `.btn-secondary` or similar, re-check the slice-2 gotcha).

### Workload / PR Boundary

- Mode: chained PR slice (`auto-chain` / `stacked-to-main`, per orchestrator delivery decision).
- Current work unit: Slice 3 — Session Resilience and Perf (PR 3).
- Boundary: starts from `main` (branch `feature/stickers-form-upgrade`, stacked on top of slice 1's `7659e82` and slice 2's `8e8a885`), ends at commit `f70907c`. Clean rollback via `git revert f70907c` — no Firestore schema/rules change in this slice (no new fields, no rule deployment; the optional `inspectores` rule tightening documented in `SETUP.md` §8 is explicitly deferred/manual, not part of this commit), so a revert is a pure client-code rollback with no backend cleanup needed.
- Estimated review budget impact: 224 insertions + 20 deletions = **244 changed lines** — well under the 400-line reviewer budget and under the ~120-line slice-3 forecast in `tasks.md`'s Review Workload Forecast (the forecast undercounted the e2e-scenario + mock-infra cost, similar to how slice 2 exceeded its forecast, but this slice still landed comfortably inside budget). No `size:exception` needed.
- Hard constraints respected: `formulario/js/firebase-config.js`, `web/js/firebase-config.js`, `api/refresh.js`, `api/stickers.js` were NOT touched by this batch (pre-existing unstaged changes to those files from before this session were left untouched and unstaged in the commit — verified via `git diff --stat` before `git add`). `openspec/` was left uncommitted per instructions. No local dev server was started; Playwright ran against its own configured static server only.

### Status

36/36 tasks complete (13 slice 1 + 12 slice 2 + 12 slice 3). All three slices of `stickers-form-upgrade` are implemented, committed, and independently revertible on `feature/stickers-form-upgrade` (`7659e82` → `8e8a885` → `f70907c`). Ready for `sdd-verify`.

---

## Remediation Batch — verify-report.md WARNINGs 1-5 (PASS WITH WARNINGS follow-up)

Fixes all 5 WARNING findings from `verify-report.md` (verdict was PASS WITH WARNINGS, no CRITICAL). Commit: `cb2693e` — `fix(formulario): implement below-next code hint and derive photo-cap literals from MAX_FOTOS` (3 files changed, 101 insertions(+), 8 deletions(-)).

### Findings Addressed

| # | Verify-report finding | Fix | Status |
|---|---|---|---|
| 1 | "Below-next edit is permitted with a hint" scenario (`sticker-code-assignment/spec.md`) was unimplemented — no floor enforced, but no hint UI existed | Added `#codigo-hint` element (`index.html`); `renderCodigo` records `state.derivedConsecutivo` and hides the hint; `validarSegmentoInput` shows a non-blocking Spanish hint when the valid edited value is below `state.derivedConsecutivo`, without blocking submit or affecting duplicate-protection paths; `nuevoRegistro` resets both | FIXED |
| 2 | Auth retry-exhaustion path (`#auth-retry` / `Reintentar`) implemented but not e2e-covered | New e2e test: 3 queued `unavailable` `getDoc` failures exhaust `readProfileWithRetry`'s 3 attempts → `#auth-retry` visible, `#app` hidden → click → `checkProfile` re-runs on the empty queue → `#app` visible, `#auth-overlay` hidden | FIXED (coverage only — code already correct, test passed on first run) |
| 3 | Literal `3`s at `form.js:340` (`slot > 3` signer fallback) and `form.js:477` (hardcoded "solo admite 3 fotos" message) did not derive from `MAX_FOTOS`, undermining the one-constant-changes-everything claim in `SETUP.md` §7 | Both literals now read `MAX_FOTOS`; message is template-interpolated (`` `Este dispositivo solo admite ${MAX_FOTOS} fotos por registro.` ``); `subirFotos`'s default `limit` param also switched from a literal `3` to `MAX_FOTOS` for the same reason. Behavior identical at `MAX_FOTOS=3` (confirmed by full regression, all pre-existing assertions of "3" text unchanged since `MAX_FOTOS` is still 3) | FIXED |
| 4 | Non-4-digit segment rejection UI (`validarSegmentoInput` blur handler) had unit coverage only, no e2e | New e2e test: fills `#codigo-consecutivo` with `'12'`, blurs, asserts `#codigo-error` shows the length message, then confirms submit is blocked with `'Corrija el consecutivo del código antes de enviar.'` | FIXED (coverage only — code already correct, test passed on first run) |
| 5 | `spec.md` Testability Notes sections used stale English helper names (`parseConsecutive`, `computeNextConsecutive`, `validateCodeSegment`, `rebuildCodeWithSegment`, `classifyAuthError`) vs. the Spanish names actually implemented and used in `design.md`/`tasks.md`/code | Doc-only edit: `specs/sticker-code-assignment/spec.md` now lists `parseConsecutivo`, `siguienteConsecutivo`, `validarSegmento`, `buildCodigo`; `specs/field-form-session/spec.md` now lists `clasificarErrorFirestore` and `backoffDelay`. `specs/inspection-photo-capture/spec.md` already correctly said `canAddSlot` — no change needed there | FIXED |

### TDD Cycle Evidence

| Finding | Behavior | RED | GREEN | REFACTOR |
|---|---|---|---|---|
| 1 | Below-next edit hint | Added the e2e scenario (fill `0002` after a derived `0006`, expect `#codigo-hint` visible) against the pre-fix code → **1 failed / 24 passed** (`#codigo-hint` element did not exist; `Error: element(s) not found`) — full-suite run confirms this is the only regression from the 3 new tests added this batch | Added `#codigo-hint` markup + `state.derivedConsecutivo` + hint logic in `validarSegmentoInput`/`renderCodigo`/`nuevoRegistro`; re-ran → **25/25 passed**. One test-design bug caught and fixed mid-GREEN: the first seed used 5 pre-existing codes (0001-0005) to force a derived-next of 0006, which made the test's own edit target (`0002`) collide with an already-seeded record — rewrote the seed to a single record (`0005`) so the edit target is unoccupied | — |
| 2, 4 | Auth retry-exhaustion; non-4-digit rejection UI | Both scenarios were added in the same RED run as finding 1's test (all 3 new tests added together, suite run once) — both passed immediately (0 code changes needed), confirming the verify-report's assessment that these were coverage gaps only, not implementation defects | N/A — no implementation change | — |
| 3 | MAX_FOTOS-derived literals | Not a TDD RED/GREEN cycle — a refactor with no observable behavior change at the current `MAX_FOTOS=3` value (pure constant substitution). Verified by full regression (36 unit + 25 e2e) passing unchanged before and after | Applied the 3 literal→`MAX_FOTOS` substitutions in `form.js` | Full suite re-run green (regression guard) |
| 5 | Stale Testability Notes naming | Docs-only, not applicable to TDD | Edited `specs/sticker-code-assignment/spec.md` and `specs/field-form-session/spec.md` | — |

### Final Verification (this batch)

- `cd formulario && node --test test/logic.test.mjs` → **36 pass, 0 fail** (unchanged from before this batch — finding 3's refactor and finding 1's new hint state do not touch any pure-logic function under unit test).
- `cd formulario && npx playwright test` → **25 passed, 0 failed** (22 pre-existing + 3 new: below-next hint, auth-retry-exhaustion-recovers, non-4-digit-segment-rejection-UI).
- Both commands re-run fresh immediately before the commit per verification-before-completion.

### Files Changed (commit `cb2693e`)

| File | Action | What Was Done |
|---|---|---|
| `formulario/index.html` | Modified | Added `<p id="codigo-hint" class="hint" hidden></p>` after `#codigo-error`. |
| `formulario/js/form.js` | Modified | Added `state.derivedConsecutivo`; `renderCodigo` records it and hides the hint; `validarSegmentoInput` shows/hides the non-blocking hint by comparing the valid edited value against it; `nuevoRegistro` resets both. Replaced the two literal `3`s (`slot > 3` fallback check, hardcoded photo-limit message) and `subirFotos`'s default `limit` param with `MAX_FOTOS` (already imported from `logic.js`). |
| `formulario/e2e/atc20.spec.js` | Modified | Added 3 scenarios: "agotados los 3 intentos... el botón Reintentar permite recuperar la sesión" (Sesión describe block), "un segmento que no tiene 4 dígitos muestra el error inline y bloquea el envío" and "editar el segmento por debajo del siguiente sugerido se acepta y muestra una sugerencia no bloqueante" (Segmento editable describe block). |
| `openspec/changes/stickers-form-upgrade/specs/sticker-code-assignment/spec.md` | Modified (doc-only, uncommitted per instructions) | Testability Notes helper names corrected to Spanish (`parseConsecutivo`, `siguienteConsecutivo`, `validarSegmento`, `buildCodigo`). |
| `openspec/changes/stickers-form-upgrade/specs/field-form-session/spec.md` | Modified (doc-only, uncommitted per instructions) | Testability Notes helper names corrected to Spanish (`clasificarErrorFirestore`, `backoffDelay`). |

### Deviations from Design

None. The hint threshold semantics (compare edited value against the consecutive value that was last rendered/derived, not a re-query) match design.md's "Decision: editable last-4 segment" section verbatim: "A non-blocking Spanish hint appears when the value is below the derived next."

### Issues Found

- The first draft of finding 1's e2e test had a test-authoring bug (seed data collided with the test's own edit target) — caught during the GREEN run, not a product defect. See TDD Cycle Evidence above.
- None blocking otherwise.

### Hard Constraints Respected

- `formulario/js/firebase-config.js`, `web/js/firebase-config.js`, `api/refresh.js`, `api/stickers.js` were not touched by this batch (confirmed via `git status`/`git show --stat` before and after commit — their pre-existing uncommitted changes remain exactly as they were).
- Only `formulario/e2e/atc20.spec.js`, `formulario/index.html`, `formulario/js/form.js` were staged and committed. `openspec/` (including this file and the two edited spec.md files) was left uncommitted per instructions.
- One conventional commit, no AI attribution, author `juanpgm <juanp.gzmz@gmail.com>` (confirmed via `git log -1`).
- Not pushed, no PR opened.

### Workload / PR Boundary

- Mode: remediation batch on top of the existing 3-slice stacked chain (`auto-chain` / `stacked-to-main`), not a new numbered slice — this is a fix-forward commit addressing verify-phase WARNINGs on the already-applied `feature/stickers-form-upgrade` branch.
- Boundary: starts from `f70907c` (end of slice 3), ends at `cb2693e`. Clean rollback via `git revert cb2693e` — no Firestore schema/rules change, no new external dependency; a revert removes the hint UI/e2e coverage and restores the two literal `3`s with zero other side effects.
- Estimated review budget impact: 101 insertions + 8 deletions = 109 changed lines — well under the 400-line budget.

### Status (Remediation)

5/5 WARNING findings from `verify-report.md` fixed. 0 CRITICAL, 0 WARNING remaining (the 2 pre-existing SUGGESTIONs — concurrent two-device race untested, root-level untracked `node_modules`/`package-lock.json` noise — were explicitly not archive-blocking and are unchanged by this batch). Full regression: 36/36 unit, 25/25 e2e. Ready for `sdd-verify` (re-verification of the remediation) or `sdd-archive`.
