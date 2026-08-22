# Exploration: stickers-form-upgrade

Status: complete. Produced by sdd-explore phase. This is the exploration input for sdd-propose.

## Current State

The "stickers form" is `formulario/` — the ATC-20 field inspection form, a standalone static site (separate Vercel deployment, root dir `formulario`), NOT the `web/` dashboard. The dashboard's "Stickers" tab (`web/js/stickers.js` + `api/stickers.js`) is the admin-side inspector roster manager that creates/enables the inspector accounts who use `formulario/`. Both are relevant.

### Entry point & module map

- `formulario/index.html` — DOM shell: 3 hardcoded photo slots (`.foto-slot`), login overlay injected by JS, single `<script type="module" src="js/form.js">`.
- `formulario/js/auth.js` — Firebase Auth gate. Email/password (cedula -> email synthesized). Loads Firebase App/Auth/Firestore from gstatic.com CDN (ESM, not bundled).
- `formulario/js/form.js` — geolocation, photo state, code generation, submit. Also imports `getAuth`/`firebase-auth.js` a second time (separate CDN fetch from auth.js's import).
- `formulario/js/logic.js` — pure, Node-testable logic: `buildCodigo`, `cedulaToEmail`, `sugerirClasificacion`.
- `formulario/css/form.css` — plain CSS, `.fotos-grid { grid-template-columns: repeat(3, 1fr) }` (fixed 3-column).
- `formulario/e2e/` — Playwright + hand-rolled in-memory Firebase mock (`firebase-mock.js`) intercepting the 4 gstatic module URLs.
- `formulario/test/logic.test.mjs` — Node `--test` unit tests for `logic.js`.
- `api/stickers.js` — Vercel serverless fn, admin-only, manages inspector Auth users + `inspectores/{uid}` Firestore docs (create, list, enable/disable).
- `api/refresh.js` — separate concern (Railway redeploy trigger for dashboard data), shares `verifyFirebaseToken` with `stickers.js`.

## Code generation & consecutive numbering (exact mechanism)

`formulario/js/logic.js:24-26`:

```js
export function buildCodigo(area, codigoInspector, consecutivo) {
  return `${MUNICIPIO}-${area}-${codigoInspector}${String(consecutivo).padStart(4, '0')}`;
}
```

e.g. `76001-1-0040001` (municipio-area-inspectorCode+4digitConsecutivo).

`formulario/js/form.js:172-216` (`generarCodigo`): reads `inspectores/{uid}.consecutivo`, increments by 1 inside a Firestore transaction, writes it back, then builds the code. This happens on "Generar codigo" click, before the record is ever submitted.

**Confirmed existing bug** (already documented in the codebase): `api/stickers.js:56-59` comment: "Real submitted evaluaciones per inspector. NOT `consecutivo` — that counter increments every time a code is generated (form started), not when a record is saved, so it overcounts. Count the source of truth instead."

The admin roster already works around this by counting actual `evaluaciones` docs (`api/stickers.js:61-64`, Firestore `.count()` query filtered by `inspector.uid`) instead of trusting `consecutivo`. This is exactly the gap to close in the field form: `generarCodigo()` should derive the next consecutive from real records, not an independently-incrementing counter field.

**Hard constraint — Firestore security rules** (`formulario/SETUP.md:73-83`):

```
allow update: if request.auth.uid == uid
  && request.resource.data.diff(resource.data).affectedKeys().hasOnly(['consecutivo'])
  && request.resource.data.consecutivo == resource.data.consecutivo + 1;
```

The rule only allows `consecutivo` to move forward by exactly 1 per write. If the field is abandoned in favor of a query-derived value, this rule becomes dead weight (or must be relaxed/removed) — a design-phase decision, not just a client change.

**Duplicate-code overflow note**: `formulario/test/logic.test.mjs:78-82` asserts that consecutivo > 9999 widens the code width (`00410000` instead of a fixed 4-digit field) — pre-existing, documented "current behavior"; will interact with editable-last-4-digits UX.

## Editing the code / last-4-digits requirement

Currently `state.codigo` is fully generated and read-only (`$('#codigo-display').textContent = codigo`), no input field. Implementing "edit last 4 digits" requires: (1) making the consecutive segment an editable input once generated, (2) validating it's numeric/4-digit, (3) re-running/adjusting the create-only duplicate check in `onSubmit` (`formulario/js/form.js:326-332`, `doc(db, 'evaluaciones', state.codigo)` transaction that already fails closed on collision — this part can stay), (4) deciding whether an edited value still needs a floor tied to "next available for this inspector" — open product question for sdd-propose.

## Photo capture flow

- `state.fotos = [null, null, null]` — fixed-size array matching the 3 hardcoded `.foto-slot` divs in `index.html:112-136`.
- Each slot: `<input type="file" accept="image/*" capture="environment">` — no `multiple` attribute, one file per slot; `capture="environment"` makes many mobile browsers jump straight to the camera app rather than offering gallery choice — likely root cause of complaint #4 (wants device/gallery picker).
- `wirePhotos()` (`formulario/js/form.js:131-156`) wires per-slot `change`/remove handlers 1:1 with `.foto-slot` index.
- Upload happens in `onSubmit`, not at photo-selection time: photos are uploaded to S3 via an EXTERNAL serverless signer (`FOTO_SIGNER_URL = 'https://sismo-fotos-signer.vercel.app/api/sign'`, not in this repo) sequentially in a `for` loop (`formulario/js/form.js:263-290`), one signed PUT per photo, awaited serially — a real perf bottleneck once photo count goes from 3 to 10.
- Upload results are cached in `state.fotosSubidas` keyed by `codigo:slot:name:size:lastModified` to survive retry-after-failed-submit — this pattern generalizes to 10 slots fine.
- SETUP.md's Storage rule caps each file at 15 MB — relevant to a "10 photos" upload-time budget.
- The signer service is external and not in this repo — its `slot` parameter handling for values > 3 is unverified; must be confirmed before promising 10-slot support (open question).
- `formulario/e2e/firebase-mock.js` only mocks `firebase-storage.js` even though the real code uses the S3 signer, not Firebase Storage — that mock module appears stale/dead; the S3 signer + PUT calls are mocked separately via Playwright `page.route` (mock file lines 129-137). Existing tech debt; fix only if it blocks new e2e tests.

## Auth / session flow and suspected logout causes

`formulario/js/auth.js:113-194` (`initAuth`):

- `setPersistence(auth, browserLocalPersistence)` — correct, persistent session.
- `onAuthStateChanged` handler, on a signed-in user, calls `getDoc(doc(db, 'inspectores', uid))` to re-verify the inspector profile every time auth state fires.
- **Concrete bug**: `auth.js:160-169` — if that `getDoc` throws for ANY reason (including transient network blips), the code does `signOut(auth)` and shows "No se pudo verificar el perfil de inspector." This force-signs-out the inspector on any Firestore read failure (flaky field connectivity, cold-start latency) — the strong candidate for "unwanted logouts". A transient error should retry/backoff, not nuke the session.
- Two other `signOut` paths are intentional/correct: profile doc missing (`auth.js:170-174`), and `activo === false` (`auth.js:179-184`).
- No `onIdTokenChanged` listener, no explicit token-refresh handling — relies on the SDK's silent refresh. `api/refresh.js`/`api/stickers.js` server-side token verification is for dashboard-admin calls, not the field form's session — not a factor in field-form logouts.
- No PWA/service worker/visibilitychange handling in `formulario/` (grep: zero matches).

## Performance observations

- Firebase SDK loaded as 4+ separate unbundled ESM CDN fetches per page load (`firebase-auth.js` and `firebase-firestore.js` each imported twice across auth.js/form.js); cold loads on poor mobile connections pay this serially via the module graph.
- Photo uploads inside `onSubmit` are strictly sequential — with up to 10 photos this could take 10x today's worst case; parallelize with a concurrency cap.
- `generarCodigo()` currently does a single-doc transaction (cheap); a "from records" model adds a Firestore aggregation query round trip (generally fast, metered).
- 10000ms CDN-load watchdog in `index.html:12-15` is a reasonable resilience pattern already in place.

## UI language / conventions

- All UI copy is Spanish (Colombia-specific: "Cedula", "Area (DIVIPOLA)", ATC-20 terms). Vanilla JS, ES modules, no framework/bundler, CDN imports. Code comments in English (project convention), consistent across form.js/auth.js/api/stickers.js. New code follows the same split: English comments, Spanish user-facing strings.
- Testing convention: pure logic in `logic.js` for `node --test`; DOM/Firebase behavior via Playwright e2e against hand-rolled Firebase mock. Strict TDD active: new logic (consecutive calc, code-edit validation) goes into `logic.js`-style pure functions first, tested via `node --test`, before wiring into `form.js`.

## Affected Areas

- `formulario/js/form.js` — `generarCodigo()` (consecutive source + editable last-4), `wirePhotos()`/`clearPhotos()`/`state.fotos` (3 -> 10 slots, file-picker vs camera), `onSubmit()` photo upload loop (parallelize).
- `formulario/js/logic.js` — home for new pure helpers (consecutive calc, code-segment validation).
- `formulario/index.html` — photo slot markup (dynamic structure for up to 10), editable code input.
- `formulario/css/form.css` — `.fotos-grid`/`.foto-slot`/`.foto-add` rework for a simpler 10-photo UI.
- `formulario/js/auth.js` — the `getDoc` failure branch (lines 160-169) causing forced logout on transient errors.
- `formulario/e2e/atc20.spec.js` + `formulario/e2e/firebase-mock.js` — extend to mock Firestore `count()`/query reads (currently only `getDoc`/`runTransaction`) and cover >3 photo slots.
- `formulario/test/logic.test.mjs` — new unit tests for consecutive-from-records logic and code-edit validation.
- `formulario/SETUP.md` (Firestore/Storage rules) — the `consecutivo` +1-only rule is incompatible with records-derived consecutive; needs a rule-strategy decision.
- External: `sismo-fotos-signer.vercel.app` (S3 photo signer) — verify it accepts slot values 1-10 before committing to that scope.
- Reference (not modified): `api/stickers.js` already computes real record counts — reference implementation for the new consecutive logic.

## Approaches (consecutive-numbering fix, highest-risk item)

1. **Query-derived consecutive (drop `consecutivo` field as source of truth)** — on `generarCodigo()`, run a Firestore query on `evaluaciones` where `inspector.uid == uid`, derive next. Pros: matches the ask; reuses api/stickers.js pattern; eliminates drift. Cons: requires relaxing/removing the +1-only rule; adds a round trip; needs e2e mock support. Effort: Medium.
2. **Hybrid — keep `consecutivo` but reconcile against actual records before use** (`max(consecutivo, derived) + 1`, persist). Pros: less rule disruption; self-heals drift. Cons: pays most of Option 1's cost without removing the stale-field problem; more complex. Effort: Medium-High.
3. **Max-scan of existing codes' consecutive segment** — next = max(existing consecutive for this inspector) + 1. Pros: collision-safe with gaps and robust to manual edits/deletions. Cons: needs fetching codes (or max via ordered query limit(1)) rather than cheap count().

**Orchestrator note on semantics**: the user's requirement "consecutivo incremental basado en los registros, independiente si tienen o no continuidad" implies gap tolerance. Count-based next can COLLIDE when gaps exist (records {1,3} -> count 2 -> next 3 -> collision). Max-based (max+1) never collides. Recommend max-based next (query evaluaciones for this inspector ordered by consecutive desc, limit 1 — or parse max from codes), with the create-transaction duplicate check kept as the fail-closed backstop.

## Recommendation

- Consecutive: records-derived next value (prefer max+1 semantics, see note above), mirroring api/stickers.js's proven query pattern; explicit design decision on the Firestore `consecutivo` rule (relax, repurpose audit-only, or drop) and e2e mock extension for queries.
- Photos: dynamic `state.fotos` (drop hardcoded 3), JS-generated slot markup, remove/soften `capture="environment"` and offer explicit camera + gallery affordances, parallelize uploads with a small concurrency cap (~3).
- Logout: in `auth.js` distinguish permission-denied/doc-missing from transient errors — retry with backoff or a manual "retry" affordance instead of unconditional signOut.

## Risks

- Firestore rule for `inspectores/{uid}.consecutivo` is +1-only; records-derived approach requires a coordinated rule change (deploy risk if rules and client are out of sync).
- External photo signer (`sismo-fotos-signer.vercel.app`) behavior for slots 4-10 unverified; "10 photos" scope may be blocked there.
- e2e firebase-mock lacks Firestore query/count support — must be extended before failing e2e tests can be written (strict TDD).
- Editable last-4-digits data-integrity policy unresolved: free edit vs floor at next-available. Needs product decision in sdd-propose.
- Sequential upload already risks partial-orphan uploads on abandoned submissions (existing comment form.js:259); 10 photos + parallel uploads increases blast radius.
- Uncommitted local diffs in `formulario/js/firebase-config.js` and `web/js/firebase-config.js` (unknown content — do not clobber; content on disk looks production-ready).

## Open decisions for sdd-propose

(a) count-based vs max-based consecutive semantics (orchestrator recommends max-based, collision-safe with gaps);
(b) editable code digits: unconstrained (with duplicate fail-closed check) vs floor at next-available;
(c) external photo-signer 10-slot support: verify during apply, design slot-generic with runtime fallback;
(d) Firestore rule change strategy for `consecutivo` (documented in SETUP.md; rule deployment is manual by the project owner).
