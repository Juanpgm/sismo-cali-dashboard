# Tasks: stickers-form-upgrade

Constraint: do NOT modify `formulario/js/firebase-config.js`, `web/js/firebase-config.js`, `api/refresh.js`, `api/stickers.js` (user-owned uncommitted migration). Branch: `feature/stickers-form-upgrade` off `main`. Strict TDD: for every behavior, the failing-test task precedes its implementation task.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~700-750 (logic+tests ~200, form.js ~180, HTML/CSS ~100, auth.js ~40, e2e ~150, docs ~30) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 (one per slice) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Records-derived consecutive + editable segment (Slice 1, ~330 lines) | PR 1 | Base: `main`. Includes e2e mock query extension, unit + e2e tests. |
| 2 | Up to 10 photo slots, gallery/camera, parallel upload (Slice 2, ~280 lines) | PR 2 | Base: `main` (stacked). Gated on D3 signer probe (task 2.1). |
| 3 | Auth retry, import dedupe, perf preconnect, SETUP.md (Slice 3, ~120 lines) | PR 3 | Base: `main` (stacked). Independent of PR 1/2 content, sequenced last per rollout order. |

Each unit lands as its own stacked commit set on `feature/stickers-form-upgrade`; PRs to `main` may be opened later per unit.

## Slice 1: Code Assignment (PR 1)

- [x] 1.1 [BLOCKING] Extend `formulario/e2e/firebase-mock.js`: add `collection`, `query`, `where`, `getDocs` with dotted-path equality matching (e.g. `inspector.uid`). Verify: mock is exercised and passes via task 1.8's spec run.
- [x] 1.2 RED: add failing `node --test` cases in `formulario/test/logic.test.mjs` for `parseConsecutivo` (matching prefix → parsed number, wrong prefix → null, width-agnostic `00410000` → `10000`).
- [x] 1.3 GREEN: implement `parseConsecutivo(codigo, codigoInspector)` in `formulario/js/logic.js`. Verify: 1.2 tests pass.
- [x] 1.4 RED: add failing tests for `siguienteConsecutivo` (contiguous `{1,2,3}` → 4, gap `{1,3}` → 4, empty → 1).
- [x] 1.5 GREEN: implement `siguienteConsecutivo(codigos, codigoInspector)` in `formulario/js/logic.js`. Verify: 1.4 tests pass.
- [x] 1.6 RED: add failing tests for `validarSegmento` (`0005` ok, `12` → `longitud`, `abcd` → `no-numerico`, `0000` → `cero`, empty → `vacio`).
- [x] 1.7 GREEN: implement `validarSegmento(raw)` in `formulario/js/logic.js`. Verify: 1.6 tests pass.
- [x] 1.8 RED (e2e): in `formulario/e2e/atc20.spec.js`, split the `#codigo-display` text assertions at line 69 and line 97 into a prefix check (`#codigo-prefijo` text) and an input-value check (`#codigo-consecutivo`); add scenarios: gap records → next code `0004`; abandoning the form does not consume a number; edited segment persists as doc id + `consecutivo`; duplicate edit → Spanish error with data preserved.
- [x] 1.9 GREEN: `formulario/index.html` — replace `#codigo-display` with `#codigo-prefijo` (span) + `#codigo-consecutivo` (`inputmode="numeric"` `maxlength="4"`).
- [x] 1.10 GREEN: `formulario/js/form.js` — rewrite `generarCodigo()` to use `state.maxConsecutivo` (session cache), query `where('inspector.uid','==',uid)` once per session, derive via `siguienteConsecutivo`; delete the `inspectores/{uid}` counter transaction (`form.js:194-201`); wire `validarSegmento` on blur/submit; add pre-submit `getDoc` existence check; persist numeric `consecutivo` on create; on `codigo-duplicado` invalidate cache, re-derive, prefill input, show Spanish message, keep entered data/photos.
- [x] 1.11 Run `cd formulario && node --test`. Verify: all `logic.test.mjs` cases pass, including existing width-widening test at `logic.test.mjs:78-82`.
- [x] 1.12 Run Playwright e2e (`cd formulario && npx playwright test`, or configured script). Verify: `atc20.spec.js` passes with the new mock and scenarios.
- [x] 1.13 Conventional commit: `feat(formulario): derive sticker consecutive from records and allow segment edit`.

## Slice 2: Photo Capture (PR 2)

- [x] 2.1 [BLOCKING] Manually probe the external signer (`sismo-fotos-signer.vercel.app`) with `slot: 10` per design D3. Record accept/reject result in `formulario/SETUP.md`; result sets `MAX_FOTOS` (10 if accepted, 3 fallback if rejected) for tasks below.
- [x] 2.2 RED: add failing `node --test` cases for `canAddSlot(current, max)` in `formulario/test/logic.test.mjs` (below cap → true, at cap → false).
- [x] 2.3 GREEN: implement `canAddSlot` and the `MAX_FOTOS` constant in `formulario/js/logic.js` (value from 2.1). Verify: 2.2 tests pass.
- [x] 2.4 RED (e2e): extend `formulario/e2e/atc20.spec.js` — multi-select photos then remove one → preview URLs in order; reaching `MAX_FOTOS` hides the add tile and shows the Spanish max-reached message. (Adapted to `MAX_FOTOS=3` per the 2.1 probe result: multi-select 3 then remove one → 2 remain in order; hitting the cap at 3 hides the add buttons; selecting more than remaining capacity in one action only adds up to the cap.)
- [x] 2.5 GREEN: `formulario/index.html` — remove the 3 hardcoded `.foto-slot` divs; add `#foto-galeria` (`accept="image/*" multiple`, no `capture`) and `#foto-camara` (`accept="image/*" capture="environment"`) hidden inputs, "Agregar fotos"/"Tomar foto" buttons, and a `.fotos-grid` container.
- [x] 2.6 GREEN: `formulario/js/form.js` — `state.fotos` as a dense array of `{file, previewUrl}` (max `MAX_FOTOS`); `renderFotos()` rebuilds `.fotos-grid`; `wirePhotos`/`clearPhotos`; upload cache key drops the slot (`` `${codigo}:${name}:${size}:${lastModified}` ``).
- [x] 2.7 GREEN: `formulario/css/form.css` — `auto-fill minmax(88px, 1fr)` responsive grid, tile styles, action-button row.
- [x] 2.8 RED (e2e): extend `formulario/e2e/atc20.spec.js` and the mocked signer route — assert at most 3 concurrent uploads in flight; assert an already-uploaded photo (cached) is not re-uploaded on retry. (Adapted from "9 photos" to 3 — the reachable maximum under `MAX_FOTOS=3` — which still proves parallel-not-sequential execution and that the cap is never exceeded. Also added a one-shot `failTransactionOnce` mock flag to `firebase-mock.js` so the cache-reuse scenario can fail the write *after* photos are already uploaded, distinct from the pre-upload `codigo-duplicado` short-circuit.)
- [x] 2.9 GREEN: `formulario/js/form.js` — `subirFotos(files, limit=3)` worker-pool upload, index-ordered results, reusing the `fotosSubidas` cache; on a `slot > 3` signer rejection, show "Este dispositivo solo admite 3 fotos por registro" instead of the generic upload error.
- [x] 2.10 Run `cd formulario && node --test`. Verify: `canAddSlot` and existing suites pass.
- [x] 2.11 Run Playwright e2e. Verify: `atc20.spec.js` passes, including the 2.4/2.8 scenarios.
- [x] 2.12 Conventional commit: `feat(formulario): dynamic photo capture via gallery/camera with parallel upload` (commit `8e8a885`; message adapted from the tasks.md wording — "support up to 10 photos" would be inaccurate given the 2.1 probe forced `MAX_FOTOS=3`; the commit body documents both the design capability and the shipped cap).

## Slice 3: Session Resilience and Perf (PR 3)

- [x] 3.1 RED: add failing `node --test` cases for `clasificarErrorFirestore` in `formulario/test/logic.test.mjs` (`unavailable`/`deadline-exceeded`/`network-request-failed` → `transient`; `permission-denied`/`not-found` → `fatal`; unknown code → `transient`).
- [x] 3.2 GREEN: implement `clasificarErrorFirestore(err)` in `formulario/js/logic.js`. Verify: 3.1 tests pass.
- [x] 3.3 RED: add failing tests for `backoffDelay(attempt, base = 600)` (attempt 1 → 600, attempt 2 → 1800).
- [x] 3.4 GREEN: implement `backoffDelay` in `formulario/js/logic.js`. Verify: 3.3 tests pass.
- [x] 3.5 RED (e2e): extend `formulario/e2e/atc20.spec.js` — mocked transient `getDoc` failure (2 fails then success) boots the form without sign-out; mocked `permission-denied` still signs out immediately.
- [x] 3.6 GREEN: `formulario/js/auth.js` — wrap the profile `getDoc` in up to 3 attempts using `backoffDelay`; classify failures with `clasificarErrorFirestore`; drop the unconditional `signOut` on transient errors (`auth.js:161-169`); keep `signOut` for missing profile, `activo === false`, and fatal codes; add a "Reintentar" affordance on exhaustion; re-export the Firestore/Auth primitives `form.js` needs.
- [x] 3.7 GREEN: `formulario/js/form.js` — import Firestore/Auth primitives only from `./auth.js` (remove direct duplicate CDN imports).
- [x] 3.8 GREEN: `formulario/index.html` — add `<link rel="preconnect" href="https://www.gstatic.com">` and `<link rel="modulepreload">` for the three gstatic Firebase modules in `<head>`.
- [x] 3.9 GREEN: `formulario/SETUP.md` — document rollout order (code-assignment → photo-capture → session-and-perf), the D3 signer probe result (from 2.1), and the optional post-rollout Firestore rule tightening (`allow update: if false` on `inspectores`).
- [x] 3.10 Run `cd formulario && node --test`. Verify: all unit suites pass.
- [x] 3.11 Run Playwright e2e (full regression). Verify: `atc20.spec.js` passes end to end across all three slices.
- [x] 3.12 Conventional commit: `feat(formulario): retry transient profile reads and dedupe Firebase imports`.
