# Proposal: stickers-form-upgrade

Field ATC-20 form (`formulario/`): correctable building codes, records-derived consecutive numbering, up to 10 photos with gallery support, and session/UX resilience.

## Intent

| # | Problem today | Why it matters |
|---|---|---|
| 1 | The generated code is read-only (`#codigo-display` text). A mis-generated or mis-pasted sticker number cannot be corrected in the form. | Inspectors must abandon the record or file a wrong code; physical sticker and database drift apart. |
| 2 | The consecutive comes from `inspectores/{uid}.consecutivo`, incremented on every "Generar código" click even if the record is never submitted. `api/stickers.js:56-59` already documents this as overcounting and works around it by counting real `evaluaciones`. | Code sequence drifts away from real records; the roster and the form disagree. |
| 3 | Exactly 3 hardcoded photo slots (`state.fotos = [null,null,null]`, 3 `.foto-slot` divs, fixed 3-column grid). | Inspections needing more evidence are truncated. |
| 4 | Each slot uses `capture="environment"` with no `multiple`, so most mobile browsers jump straight to the camera and never offer the gallery. | Photos taken before opening the form cannot be attached. |
| 5 | `auth.js:161-169` calls `signOut()` whenever the profile `getDoc` throws — including transient network failures. Photo uploads in `onSubmit` are strictly sequential. Firebase auth/firestore modules are fetched twice (auth.js + form.js). | Involuntary logouts in the field; slow submits that get worse at 10 photos. |

## Scope

### In Scope
- Records-derived next consecutive (max + 1) computed from the inspector's own `evaluaciones`.
- Editable last 4 digits of the generated code, validated and collision-protected.
- Up to 10 photo slots with a simplified, dynamic UI.
- Explicit gallery ("Agregar fotos", multi-select) and camera ("Tomar foto") affordances.
- Parallel photo upload with a small concurrency cap.
- Transient-vs-fatal auth error classification (no more logout on network blips).
- Deduplicated Firebase SDK imports; `SETUP.md` operator notes.

### Out of Scope
- Editing area/municipio/inspector segments of the code.
- Editing or deleting submitted `evaluaciones` (rules forbid it; re-inspection is a new doc).
- Bundler, framework, or PWA/offline queue introduction.
- Backfilling legacy `evaluaciones` docs.
- Changing the external photo signer service (not in this repo).
- Deploying Firestore rules (manual, owner-operated).

## Capabilities

### New Capabilities
- `sticker-code-assignment`: derive the next consecutive from real records, render the code with an editable 4-digit segment, validate and fail closed on duplicates.
- `inspection-photo-capture`: up to 10 photos, gallery and camera entry points, resilient parallel upload.
- `field-form-session`: keep an authenticated inspector signed in through transient backend failures; sign out only on authoritative rejections.

### Modified Capabilities
- None (`openspec/specs/` is empty; this change seeds the first specs).

## Decisions

**D1 — Consecutive semantics: max-based, session-cached. RESOLVED.**
Next = `max(consecutive parsed from this inspector's evaluaciones codes) + 1`. The requirement explicitly tolerates gaps ("independiente de si tienen continuidad"). Count-based collides when gaps exist (records `{1,3}` → count 2 → next 3 → duplicate). Rejected: count-based (`api/stickers.js` pattern) for correctness; hybrid `max(counter, derived)+1` because it keeps the stale field as a source of truth without removing its failure mode.
Derivation detail: `evaluaciones` docs store no numeric consecutive, and codes across areas (`76001-{area}-{cod3}{NNNN}`) do NOT sort lexicographically by consecutive, so an `orderBy(codigo).limit(1)` shortcut is wrong. First slice queries `where('inspector.uid','==',uid)` (equality only — no composite index needed) and parses the max client-side. The query runs once per session and is cached in memory, then incremented locally; it is re-run on a duplicate collision. New docs also persist a numeric `consecutivo` field so a cheap indexed `orderBy` becomes available later without changing behavior now.

**D2 — Editable digits: permissive within `0001`-`9999`, fail closed on duplicates. RESOLVED.**
Correcting an already-pasted sticker is the whole point, and gap filling is a legitimate correction, so a floor at "next available" would block the primary use case. Guardrails instead: exactly 4 digits required; a non-blocking Spanish hint when the value is below the derived next; an existence pre-check before submit for an early error; and the existing create-only transaction (`form.js:326-332`) as the hard, fail-closed backstop. Rejected: floor-at-next-available (blocks corrections), free-text full-code edit (breaks the municipio/area/inspector invariants).

**D3 — Photo signer slot range: slot-generic design, verified during apply. RESOLVED.**
`sismo-fotos-signer.vercel.app` is external and its handling of `slot > 3` is unverified. Nothing in the client hardcodes 3, so the design stays slot-generic (`slot: index + 1`, 1-10). Apply MUST start with a live probe of `slot: 10` before enabling more than 3 slots. If the signer rejects high slots, the visible cap falls back to 3 and the photo slice ships blocked pending an external fix; the rest of the change is unaffected.

**D4 — Firestore rules: zero-change rollout, optional cleanup afterwards. RESOLVED.**
The currently deployed rules already grant `allow read: if isInspector()` on `/evaluaciones/{id}`, so the records-derived query works under the OLD rules with no deployment. The client simply STOPS writing `inspectores/{uid}.consecutivo`; the `+1`-only update rule is never exercised again, so it cannot break mid-rollout in either direction. Adding a numeric `consecutivo` field to new evaluaciones is allowed because the `create` rule only constrains `inspector.uid`. `SETUP.md` documents an OPTIONAL post-rollout operator step (tighten to `allow update: if false`); the field remains as a legacy hint, never as a source of truth. Rejected: relaxing the rule before rollout (a window where any client could write arbitrary consecutives).

## Approach

1. Pure functions first in `formulario/js/logic.js` (`node --test`, strict TDD): parse the consecutive segment from a code, compute next from a code list, validate a 4-digit segment, rebuild a code with an overridden segment, classify an auth/Firestore error as transient vs fatal.
2. Wire `form.js`: `generarCodigo()` reads from the cached records-derived value instead of the counter transaction; the code renders as prefix text plus a 4-digit `inputmode="numeric"` input.
3. Photos: JS-generated slots (max 10), dynamic `state.fotos`, one `multiple` gallery input plus one `capture="environment"` camera input, responsive grid; upload with concurrency 3, keeping the existing `fotosSubidas` cache key.
4. `auth.js`: retry with backoff on transient profile-read failures and offer a retry affordance; keep `signOut` only for missing profile and `activo === false`.
5. Extend `formulario/e2e/firebase-mock.js` with Firestore query support BEFORE writing the e2e coverage that depends on it.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `formulario/js/logic.js` | Modified | New pure helpers (consecutive, code-segment validation, error classification) |
| `formulario/test/logic.test.mjs` | Modified | Unit tests for the new helpers (written first) |
| `formulario/js/form.js` | Modified | `generarCodigo`, editable segment, `wirePhotos`/`clearPhotos`, parallel upload |
| `formulario/index.html` | Modified | Editable code input; photo slots become JS-generated |
| `formulario/css/form.css` | Modified | Responsive grid for up to 10 slots |
| `formulario/js/auth.js` | Modified | Transient-error retry instead of unconditional `signOut` |
| `formulario/e2e/*` | Modified | Query mock support; specs for code edit and >3 photos |
| `formulario/SETUP.md` | Modified | Optional rule-tightening operator step; documented rollout order |
| `formulario/js/firebase-config.js`, `web/js/firebase-config.js` | Untouched | Uncommitted local changes — MUST NOT be modified |
| `api/stickers.js` | Reference only | Existing per-inspector count pattern |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Signer rejects `slot > 3` | Med | D3 probe before implementing; fall back to 3 slots, ship the rest |
| Per-session query cost / payload on many records | Med | One query per session, cached; re-query only on collision |
| Two devices generating codes concurrently | Low | Create-only transaction fails closed; re-derive and retry |
| Consecutive exceeds 9999 (code widening, `logic.test.mjs:78-82`) | Low | Clamp the editable field to 4 digits; explicit Spanish error above 9999 |
| Parallel uploads widen the orphaned-photo window (`form.js:259`) | Med | Cap concurrency at 3; keep the upload cache; document manual cleanup |
| Retry-instead-of-signOut masks a real revocation | Low | Bounded retries; `permission-denied`/`not-found` still sign out immediately |
| e2e mock drift (stale `firebase-storage.js` mock) | Low | Extend only what new tests need; leave dead mock untouched |

## Rollback Plan

Per-slice `git revert` — no data migration, no rules deployment, no schema removal. The extra `consecutivo` field on new evaluaciones is additive and ignored by all readers; the legacy `inspectores/{uid}.consecutivo` field is left in place and untouched, so reverting the client restores the old counter behavior exactly. If only photos regress, revert the photo slice and the visible cap returns to 3.

## Dependencies

- External photo signer `sismo-fotos-signer.vercel.app` slot 1-10 support (probe in apply).
- Firestore rules are owner-deployed; this change requires NO rule deployment to ship.

## Success Criteria

- [ ] The next consecutive equals `max(existing for this inspector) + 1`, correct in the presence of gaps.
- [ ] Abandoning the form no longer consumes a consecutive number.
- [ ] An inspector can correct the last 4 digits; a duplicate is rejected with a clear Spanish message.
- [ ] Up to 10 photos attach from gallery (multi-select) or camera; submit uploads them in parallel.
- [ ] A transient profile-read failure no longer signs the inspector out.
- [ ] `node --test` unit tests exist for every new pure function; e2e covers code edit and >3 photos.

## Review Workload Forecast

- Estimated changed lines: **~700-750** (logic+tests ~200, form.js ~180, HTML/CSS ~100, auth.js ~40, e2e ~150, docs ~30).
- **400-line budget risk: High**
- **Chained PRs recommended: Yes**
- **Decision needed before apply: Yes**

Suggested slices (each autonomous, testable, revertible):
1. `code-assignment` — logic helpers + tests, e2e query mock, `generarCodigo` rewrite, editable 4-digit segment (~330 lines).
2. `photo-capture` — 10 dynamic slots, gallery/camera inputs, CSS, parallel upload, e2e (~280 lines).
3. `session-and-perf` — auth transient-error handling, duplicate SDK import cleanup, SETUP.md operator notes (~120 lines).

## Proposal question round

Resolved autonomously (user unavailable). Flag for review if any assumption is wrong:
1. Corrections may fill gaps below the current maximum — assumed YES (D2 permissive). If corrections must only move forward, D2 becomes floor-at-next-available.
2. The consecutive is per inspector and shared across areas — assumed YES (current `buildCodigo` behavior preserved). Per-area sequences would change the derivation and the code format contract.
3. 10 photos is a hard product cap, not "at least 10" — assumed YES.
4. Inspectors have at most a few hundred records each, so one per-session query is acceptable — assumed YES. If volumes are much larger, the numeric `consecutivo` field plus an indexed query must ship in slice 1 rather than later.
