# Verify Report: stickers-form-upgrade

Branch: feature/stickers-form-upgrade (off main)
Commits: 7659e82 (slice 1) -> 8e8a885 (slice 2) -> f70907c (slice 3) -> cb2693e (remediation)
Verdict: PASS

## Re-Verification Scope

This is a FOCUSED re-verification of remediation commit `cb2693e`, which claims
to fix the 5 WARNING findings from the previous verify report (same file,
prior verdict PASS WITH WARNINGS). Full spec/task/design verification was not
redone — see the prior report body below for that evidence, which still
stands unchanged for everything commit cb2693e did not touch.

## Test Evidence (re-run by verifier)

| Suite | Command | Result |
|---|---|---|
| Unit | cd formulario && node --test test/logic.test.mjs | 36 pass, 0 fail |
| E2E | cd formulario && npx playwright test | 25 passed, 0 failed |

E2E count rose from 22 to 25 (+3), matching the 3 new Playwright tests added
in cb2693e (retry-exhaustion recovery, non-4-digit segment rejection,
below-next hint). Unit count unchanged at 36 (no new pure-logic branches
introduced by this remediation).

## Diff / Commit Hygiene (re-checked)

- `git diff main...HEAD --stat`: 9 files changed, 966 insertions(+), 180 deletions(-), all inside `formulario/`.
- Forbidden files confirmed still absent from the branch diff: `git diff main...HEAD --stat -- formulario/js/firebase-config.js web/js/firebase-config.js api/refresh.js api/stickers.js` returns empty. Their pre-existing uncommitted working-tree modifications remain untouched and unstaged.
- `cb2693e` commit message is conventional (`fix(formulario): ...`), authored by juanpgm <juanp.gzmz@gmail.com>, no AI attribution / co-author trailer.
- `cb2693e` touches only `formulario/e2e/atc20.spec.js`, `formulario/index.html`, `formulario/js/form.js` (72/+1/+36-8 lines) — scoped exactly to the claimed fix, no unrelated changes.

## Per-Finding Resolution

### WARNING 1 — Below-next edit hint scenario unimplemented → RESOLVED

Evidence:
- `formulario/index.html`: new `<p id="codigo-hint" class="hint" hidden></p>` element added next to `#codigo-error`.
- `formulario/js/form.js`: `state.derivedConsecutivo` tracks the last rendered/derived consecutive; `validarSegmentoInput` compares the edited value against it and shows/hides `#codigo-hint` with the Spanish non-blocking text `"El consecutivo ingresado es menor al siguiente sugerido. Se acepta si es una corrección intencional."`; no floor/block is added (edit still accepted), matching spec.md scenario "Below-next edit is permitted with a hint" verbatim.
- Hint is correctly reset (`hidden = true`, `derivedConsecutivo = null`) on `renderCodigo` and `nuevoRegistro`.
- E2E coverage added: `atc20.spec.js` test "editar el segmento por debajo del siguiente sugerido se acepta y muestra una sugerencia no bloqueante" — asserts hint hidden initially, visible and non-empty after a below-next edit, and that submission still succeeds with the below-next value persisted (`consecutivo === 2`).
- Test run confirms this test PASSES (test #12, `atc20.spec.js:189:3`).

### WARNING 2 — Retries-exhausted e2e scenario missing → RESOLVED

Evidence:
- New e2e test "agotados los 3 intentos por fallas transitorias, el botón Reintentar permite recuperar la sesión" in `atc20.spec.js` (under "Sesión: reintento del perfil ante fallas transitorias").
- Drives all 3 transient `getDocFailQueue` failures to exhaustion, asserts `#auth-retry` becomes visible with the correct error text and `#app` stays hidden, then clicks `#auth-retry` and asserts `#app` becomes visible / `#auth-overlay` hides — i.e. session recovery, not a forced logout.
- No code change was needed here (the retry affordance already existed); this closes the "implemented but not runtime-proven" gap.
- Test run confirms PASS (test #5, `atc20.spec.js:76:3`).

### WARNING 3 — MAX_FOTOS-independent literals (slot > 3, retry message) → RESOLVED

Evidence (`formulario/js/form.js`, re-inspected post-remediation):
- `subirFotos(fotos, limit = MAX_FOTOS)` — was `limit = 3`.
- `if (sr.status === 400 && slot > MAX_FOTOS) throw new Error('signer-slot-limit')` — was `slot > 3`.
- `` showSubmitError(`Este dispositivo solo admite ${MAX_FOTOS} fotos por registro.`) `` — was the hardcoded string `"...solo admite 3 fotos..."`.
- All three previously-literal `3`s now derive from the single `MAX_FOTOS` constant (`formulario/js/logic.js`). The "single-constant-changes-everything" claim in SETUP.md is now accurate as written.
- Confirmed via direct grep of `formulario/js/form.js` post-fix: no remaining `slot > 3` or hardcoded `3 fotos` string.

### WARNING 4 — Non-4-digit segment rejection UI untested at e2e level → RESOLVED

Evidence:
- New e2e test "un segmento que no tiene 4 dígitos muestra el error inline y bloquea el envío" in `atc20.spec.js`.
- Types `12` into `#codigo-consecutivo`, blurs, asserts `#codigo-error` shows the exact Spanish message `"El consecutivo debe tener exactamente 4 dígitos."`, then fills the rest of the form and asserts submission is blocked with `#submit-error` = `"Corrija el consecutivo del código antes de enviar."` and `#confirm` stays hidden.
- This directly exercises the previously-untested `validarSegmentoInput` blur-handler wiring end to end, not just the pure `validarSegmento` unit function.
- Test run confirms PASS (test #16, `atc20.spec.js:170:3`).

### WARNING 5 — spec.md Testability Notes stale naming (cosmetic, doc-only) → RESOLVED

Evidence:
- `openspec/changes/stickers-form-upgrade/specs/sticker-code-assignment/spec.md:97` now reads: "Pure functions (`parseConsecutivo`, `siguienteConsecutivo`, `validarSegmento`, `buildCodigo`) MUST have `node --test` coverage..." — Spanish names matching the actual code, no more `parseConsecutive`.
- `openspec/changes/stickers-form-upgrade/specs/field-form-session/spec.md:75` now reads: "`clasificarErrorFirestore` and the pure retry/backoff scheduling helper `backoffDelay` MUST have `node --test` coverage." — Spanish name matching the actual code, no more `classifyAuthError`.
- Note: `openspec/` is an untracked directory on this branch (confirmed via `git status`), so this doc fix is not visible in `git show cb2693e`'s tracked-file diff, but the current file content on disk is verified correct and consistent with design.md/tasks.md/code. This does not affect shipped functionality — it is a planning-artifact-only fix, and its correctness was verified directly against the file content rather than a commit diff.

## Findings Summary (post-remediation)

- CRITICAL: 0
- WARNING: 0 (all 5 prior WARNINGs RESOLVED with runtime/inspection evidence above)
- SUGGESTION: 2 unchanged, carried forward from the prior report (neither was in scope for this remediation batch, neither blocks archive):
  1. Concurrent two-device race untested (pre-existing transaction pattern, unmodified by this change).
  2. Root-level untracked `node_modules/`/`package-lock.json` noise (unrelated to this change, not committed).

All 36 tasks remain complete (unchanged by this remediation — no task-list edits were needed, the WARNINGs were spec/test-coverage gaps, not unchecked tasks). Both suites pass in full (36/36 unit, 25/25 e2e, e2e count correctly increased by exactly the 3 new tests). No forbidden files touched. No AI attribution in the remediation commit.

## Recommendation

Archive-eligible, no follow-up items remain from the WARNING list. The 2 pre-existing SUGGESTIONs are optional/low-risk and do not block archive.

## Verdict

PASS
