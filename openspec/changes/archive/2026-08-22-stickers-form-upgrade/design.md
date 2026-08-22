# Design: stickers-form-upgrade

## Technical Approach

All new behavior lands as pure functions in `formulario/js/logic.js` (node --test first, strict TDD), wired by thin imperative glue in `form.js` / `auth.js`. No bundler, no framework, no new dependency. `auth.js` becomes the single Firebase seam: it owns the CDN imports and re-exports the Firestore/Auth primitives `form.js` needs. Three autonomous slices per the proposal.

## Architecture Decisions

### Decision: consecutive derivation

| Option | Tradeoff | Decision |
|---|---|---|
| `orderBy('codigo_edificacion').limit(1)` | Wrong: codes are area-prefixed, do not sort by consecutive | Rejected |
| `count()` of evaluaciones | Collides on gaps (`{1,3}` → 3) | Rejected |
| Equality query + client-side max | One round trip per session, no composite index | **Chosen** |

Query shape (equality only — served by the automatic single-field index):

```js
getDocs(query(collection(db, 'evaluaciones'), where('inspector.uid', '==', uid)))
```

Consecutive is parsed client-side from the doc id (`codigo_edificacion` as fallback). `parseConsecutivo` is width-agnostic: it strips the 3-digit inspector code from the third `-` segment and parses the remainder, so `76001-1-00410000` → `10000` (the `>9999` widening asserted in `logic.test.mjs:78-82`). `buildCodigo` is NOT changed, so that test stays green; the 4-digit ceiling is enforced by the new validator instead.

Cache: `state.maxConsecutivo` (number | null). `null` → query. Invalidated (set to `null`) only on a `codigo-duplicado` transaction failure. Bumped locally to `max(cache, used)` after a successful create and after each generate.

### Decision: `generarCodigo` replacement, zero rules change

The `inspectores/{uid}` read-increment-write transaction (`form.js:194-201`) is deleted outright. The client simply stops writing `consecutivo`, so the deployed `+1`-only update rule is never exercised — it cannot break in either rollout direction. Reverting the slice restores the old counter exactly (the field is left in place, never deleted).

**Firestore schema/rules call-out**: new evaluaciones docs gain a numeric `consecutivo` field (parsed from the final, possibly edited code). The deployed `create` rule only constrains `inspector.uid`, so this is allowed with **no rule deployment**. Reads of `evaluaciones` are already permitted by `allow read: if isInspector()`. Optional post-rollout tightening (`allow update: if false` on `inspectores`) is documented in `SETUP.md` only.

### Decision: editable last-4 segment

`#codigo-display` becomes a wrapper: `<span id="codigo-prefijo">76001-1-004</span>` + `<input id="codigo-consecutivo" inputmode="numeric" pattern="[0-9]*" maxlength="4">`. Validation is `validarSegmento(raw)` → `{ ok, value, code }` with codes `vacio | no-numerico | longitud | cero`; `0001`-`9999` only (rejected: floor-at-next-available — it blocks the gap-filling correction that motivates the feature). A non-blocking Spanish hint appears when the value is below the derived next. Guardrails in order: input validation → `getDoc` existence pre-check on blur/submit (early, friendly) → the existing create-only transaction (`form.js:326-332`) as the fail-closed backstop.

Collision UX change: on `codigo-duplicado` the form no longer wipes the code and re-enables the area. It invalidates the cache, re-derives the next value, prefills the input with it, and shows a Spanish message. Entered data and photos survive.

### Decision: photo model

`state.fotos` becomes a **dense array** of `{ file, previewUrl }` (max 10), not fixed slots; `renderFotos()` rebuilds `.fotos-grid` from state (one tile per photo plus a trailing add-tile while `length < 10`). The three hardcoded `.foto-slot` divs are removed from `index.html`. Two shared hidden inputs replace the per-slot inputs:

| Affordance | Input | Attributes |
|---|---|---|
| "Agregar fotos" | `#foto-galeria` | `accept="image/*" multiple` — **no** `capture` (restores the gallery picker) |
| "Tomar foto" | `#foto-camara` | `accept="image/*" capture="environment"` |

Upload cache key drops the slot: `` `${codigo}:${name}:${size}:${lastModified}` ``. Rationale: removing a photo shifts every later index, and a slot-keyed cache would force re-upload of every surviving photo on retry — the exact failure the cache exists to prevent. The S3 slot number may therefore not match final display order; order is preserved in the `fotos` array written to Firestore.

Upload becomes a worker pool with concurrency 3 (`subirFotos(files, limit)`), results collected index-ordered. Failure semantics unchanged (`foto-upload`); successful uploads stay cached.

**Signer probe (D3)**: `MAX_FOTOS` is a module constant. Apply MUST start with a manual `slot: 10` probe against the signer and set the constant to 10 or 3 accordingly, recording the result in `SETUP.md`. Defensive runtime fallback: a sign failure on `slot > 3` surfaces a specific Spanish message ("Este dispositivo solo admite 3 fotos por registro") rather than the generic upload error.

### Decision: auth transient-vs-fatal

`clasificarErrorFirestore(err)` (pure) → `'fatal' | 'transient'`. Fatal: `permission-denied`, `unauthenticated`. Transient: everything else, including unknown codes — the Firestore rules are the durable gate (already stated in `auth.js:177-178`), so failing open on the session is safe and failing closed is the field-logout bug. Missing doc and `activo === false` keep their unconditional `signOut`.

The `getDoc` call is wrapped in 3 attempts with `backoffDelay(attempt)` (600ms → 1800ms, pure and testable). On exhaustion: **no `signOut`** — the overlay shows a Spanish message plus a "Reintentar" button that re-runs the profile check.

### Decision: import dedupe — corrected rationale

Investigated the proposal's "fetched twice" claim: **it is not a duplicate network fetch**. The ES module loader keys by resolved URL, so `firebase-auth.js` imported from both `auth.js` and `form.js` is fetched once. The dedupe is still worth doing, but as a **coupling/seam win** (one Firebase boundary, one interception point for the e2e mock), not a load-time win. Real low-risk load wins, both in `index.html` `<head>`: `<link rel="preconnect" href="https://www.gstatic.com">` and `<link rel="modulepreload">` for the three gstatic modules, so `firebase-firestore.js` starts fetching in parallel instead of waiting for `auth.js` to be parsed.

## Data Flow

    [Generar código]
      generarCodigo() ─→ cache hit? ──yes──→ next = max + 1
            │ no                                   │
            ↓                                      ↓
      getDocs(where inspector.uid == uid)     buildCodigo(area, cod, next)
            │                                      ↓
      parseConsecutivo() per doc ─→ max ──→  render prefijo + <input value=NNNN>
                                                   │
    [Submit]  validarSegmento ─→ getDoc pre-check ─→ subirFotos(limit 3)
                                                   ↓
                     runTransaction create-only (fail closed) ─→ on dup: cache = null

## File Changes

| File | Action | Description |
|---|---|---|
| `formulario/js/logic.js` | Modify | `parseConsecutivo`, `siguienteConsecutivo`, `validarSegmento`, `clasificarErrorFirestore`, `backoffDelay`, `MAX_FOTOS` |
| `formulario/test/logic.test.mjs` | Modify | Unit tests for each new helper (written first) |
| `formulario/js/auth.js` | Modify | Retry+backoff profile read, no `signOut` on transient, "Reintentar" affordance, re-export Firestore/Auth primitives |
| `formulario/js/form.js` | Modify | `generarCodigo` rewrite, editable segment wiring, dynamic `renderFotos`, `subirFotos` pool, `consecutivo` field on write, imports only from `./auth.js` |
| `formulario/index.html` | Modify | Code input markup, remove 3 hardcoded slots + add gallery/camera buttons, preconnect/modulepreload |
| `formulario/css/form.css` | Modify | `auto-fill minmax(88px, 1fr)` grid, tile styles, action-button row |
| `formulario/e2e/firebase-mock.js` | Modify | `collection`/`query`/`where`/`getDocs`, dotted-path equality, read-failure flags |
| `formulario/e2e/atc20.spec.js` | Modify | New scenarios; update `#codigo-display` text assertions to prefix + input value |
| `formulario/SETUP.md` | Modify | Signer probe result, optional rule tightening, rollout order |

## Interfaces / Contracts

```js
// logic.js (pure)
parseConsecutivo(codigo, codigoInspector) -> number | null   // width-agnostic, >9999 safe
siguienteConsecutivo(codigos, codigoInspector) -> number     // max + 1, gaps tolerated, empty -> 1
validarSegmento(raw) -> { ok, value, code }                  // '0001'..'9999'
clasificarErrorFirestore(err) -> 'fatal' | 'transient'
backoffDelay(attempt, base = 600) -> number
```

Firestore `evaluaciones/{codigo}` gains: `consecutivo: number` (additive, ignored by all current readers).

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | All five pure helpers | `node --test` in `formulario/test/logic.test.mjs`; gaps `{1,3}`→4, empty→1, `00410000`→10000, wrong prefix→null, `0000`/`abc`/5-digit rejected, `permission-denied`→fatal, unknown→transient |
| E2E prerequisite | Query support | Extend `firebase-mock.js` with `collection`/`query`/`where`/`getDocs` **before** any dependent spec is written |
| E2E | Gaps → next code `0004`; abandoning the form does not consume a number; edited segment persists as doc id + `consecutivo`; duplicate edit → Spanish error, data preserved; 5 photos via multi-select, remove one, 4 URLs in order; 10 photos hides the add tile; transient `getDoc` failure (2 fails then success) boots without logout; `permission-denied` still signs out | Playwright against the extended mock |

## Migration / Rollout

No data migration, no rules deployment, no backfill. Order: **(1) code-assignment → (2) photo-capture → (3) session-and-perf**. Slice 2 is gated on the signer probe; if it fails, ship slice 2 with `MAX_FOTOS = 3` (gallery/camera affordances and parallel upload still land) and track the signer fix externally. Per-slice `git revert` is a clean rollback.

## Open Questions

- [ ] Signer `slot > 3` support — must be probed at the start of slice 2 (D3).
- [ ] Confirm inspectors hold at most a few hundred records each; if not, the indexed `consecutivo` `orderBy` must move into slice 1.
