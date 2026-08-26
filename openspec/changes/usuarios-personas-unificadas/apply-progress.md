# Apply progress: usuarios-personas-unificadas

Change: `usuarios-personas-unificadas` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-apply
First apply run (no prior apply-progress existed). All 27 tasks across 4 phases are `[x]` in
`tasks.md`. Automated tasks are DONE and verified; MANUAL SMOKE tasks are marked PENDING — the
user must perform them (list below). No task is BLOCKED.

## Commits (in order, all on `main`, not pushed)

1. `5df7278` — `fix(planeacion): remove inline conductor creation from vehiculo modal` (Phase 1 / Slice A)
2. `7ac81ea` — `feat(planeacion): hide individual inspector-assignment controls (group-only)` (Phase 2)
3. `69f2396` — `feat(planeacion): move Inspectores roster from Stickers` (Phase 3 / Slice B)
4. *(next)* — `feat(usuarios): unified tipo selector fans out to inspector/conductor/usuario endpoints` (Phase 4 / Slice C)

A 5th commit carries this SDD paperwork (tasks.md checkboxes + this file + the previously-uncommitted
proposal/design/specs/exploration for this change) — separate from the four code commits above so
each code commit stays a clean, atomic diff for review.

## TDD honesty per phase

- **Phase 1/2**: pure deletion + one call-site collapse, no new logic → no RED/GREEN needed (per
  tasks.md's own note). Verified via `node --test "js/planeacion.test.mjs"` staying green
  (`buildConductorPayload`/`buildVehiculoPayload` bodies untouched).
- **Phase 3**: RED confirmed for real — `filterRosterInspectores`/`rowHtml` not yet exported caused
  `SyntaxError: The requested module './planeacion.js' does not provide an export named
  'filterRosterInspectores'` before the port; GREEN after. DOM/segment-toggle/two-backend-fanout work
  is MANUAL SMOKE only (no headless-DOM harness in this repo — see design.md's Testing Strategy).
- **Phase 4**: RED confirmed by *actually* temporarily renaming `export function payloadForTipo` to a
  non-exported name, re-running `node --test "js/usuarios.test.mjs"`, observing the real
  `SyntaxError: does not provide an export named 'payloadForTipo'`, then restoring and confirming
  GREEN — not just inferred from the Phase 3 precedent.
  - **Unplanned infra fix required to make this RED/GREEN cycle possible at all**: `usuarios.js`
    statically imported `getAuth`/`sendPasswordResetEmail` from
    `https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js`, and transitively via
    `firebase-config.js`'s own CDN import of `initializeApp`/`getApps`/`getApp`. Node's ESM loader
    cannot resolve an `https:` specifier (`ERR_UNSUPPORTED_ESM_URL_SCHEME`) — the same failure mode
    already visible in the pre-existing `evaluaciones.test.mjs` (via `israel-source.js`'s own CDN
    import, unrelated to this change). Importing `usuarios.js` at all — even just for the pure
    `payloadForTipo` — would have failed with this error, not a clean "missing export", making an
    honest RED impossible. Fix: both CDN-touching imports are now loaded lazily via a
    `loadFirebaseAuth()` dynamic `import()`, called only at the two runtime call sites that actually
    need them (`reload()`'s `ownUid` lookup; the "Resetear contraseña" click handler). Zero behavior
    change in the browser — a dynamic `import()` of the same specifier resolves from the module
    registry cache after the first call, so the two Firebase SDK fetches still effectively happen
    once per page load, just deferred a few milliseconds from parse-time to `initUsuarios()`-time.

## Deviations from design.md / tasks.md (documented, not silent)

1. **Task 4.4's literal instruction was to parameterize `callApi` itself** (not fork a second fetch
   function) — implemented exactly that: `callApi(getToken, body, endpointName = 'usuarios')`, with
   `apiUrl(endpointName)` replacing the old hardcoded `ENDPOINT` constant. (My first draft had
   accidentally forked a `callEndpoint` helper before I caught the mismatch against the task text and
   corrected it back to a single parameterized `callApi` — worth flagging in review as the diff shows
   only the final, correct shape.)
2. **The CDN-import lazy-load refactor in `usuarios.js`** (see above) was not in any task text — it
   was a necessary enabler discovered while trying to honor the STRICT TDD requirement for
   `payloadForTipo`. Scoped narrowly (two import statements → one `loadFirebaseAuth()` helper, two
   call sites updated), no behavior change.
3. **Reload vs. no-reload after create, by tipo**: `admin`/`viewer`/`usuario`/`inspector` all call
   `reload()` after a successful create (all four are Firebase Auth-backed, and `api/usuarios.js`'s
   own `listUsuarios()` enumerates ALL Auth users via `admin.auth().listUsers(1000)` — a newly
   created inspector genuinely appears in the Usuarios list on the next fetch, same as it already did
   before this change via the `inspector` role chip). Only `conductor` skips `reload()` (no Auth
   account is ever created, so it can never appear in this list per design.md's v1 scope) and instead
   calls the cheaper local `render()` to just show the confirmation notice. This wasn't spelled out
   explicitly in tasks.md but follows directly from `api/usuarios.js`'s actual `listUsuarios`
   implementation (verified by reading it, not assumed).
4. **Modal header ("Nuevo usuario") and title copy were left as-is** — not updated to a
   tipo-neutral phrase. Functional correctness (selector, field-swap, fan-out, error isolation) was
   prioritized; this is a cosmetic nit, flagged here rather than silently left. Trivial to rename
   later if the user wants a more neutral header.

## Files changed

- `web/js/planeacion.js` — Slice A deletions (Phase 1); hid 3 individual-assignment controls
  (Phase 2); ported `rowHtml`/`filterRosterInspectores`/roster markup/`wire()` as a new
  "Inspectores" segment, reusing `callStickersApi`/`inspectoresCache`/`ensureInspectores` (Phase 3).
- `web/js/planeacion.test.mjs` — added RED→GREEN assertions for `rowHtml`/`filterRosterInspectores`
  (Phase 3).
- `web/js/stickers.js` — removed the Roster segment entirely; shrunk to a 2-way segmented control
  (Evaluaciones default + Asignación); dropped the now-unused `escapeHtml` import (Phase 3).
- `web/js/stickers-asignacion.js` — added a local `callStickersApi` + `inspectoresCache`/
  `getInspectores()` getter so the Asignación sub-section fetches its own roster copy each
  `reload()`, instead of depending on the (now-gone) `getInspectores` callback from `stickers.js`
  (Phase 3).
- `web/js/usuarios.js` — added `tipo` selector + field-swap to the create modal; exported pure
  `payloadForTipo(tipo, fields)`; parameterized `callApi` to fan out to `usuarios`/`stickers`/
  `planeacionAsignaciones` per tipo; per-tipo inline error isolation; lazy-loaded the two
  CDN-touching Firebase imports (Phase 4).
- `web/js/usuarios.test.mjs` — new file, RED→GREEN assertions for `payloadForTipo` (Phase 4).
- `api/usuarios.js`, `api/stickers.js`, `backend/app/routers/usuarios.py` — untouched, confirmed via
  empty `git diff --stat`.

## Test results (final)

- `python -m pytest backend/tests/ -q` → **555 passed** (unchanged from baseline; Phase 4 touched no
  backend files, Phases 1-3 never touched `backend/`).
- `node --test "js/**/*.test.mjs"` (from `web/`) → **7 pass / 1 fail**. The 1 fail is
  `evaluaciones.test.mjs`, pre-existing and unrelated (CDN import via `israel-source.js`, confirmed
  via `git log` that this file predates and was never touched by this change).

## MANUAL SMOKE — user must perform (no headless-DOM harness in this repo)

**Phase 1 (vehículo modal)**
- Open the vehículo modal for create and for edit: confirm no "Crear nuevo conductor" option/fieldset
  is reachable.
- Save with an existing conductor selected → `conductor_id`/`empresa` persist, no `crearConductor`
  network call fires.
- Save with "— Sin conductor —" selected still works.

**Phase 2 (hide individual assignment)**
- Open a cuadrilla row in Grupos/Cuadrillas: confirm no inspector combobox or "Quitar asignación"
  button renders.
- Open a map point popup: confirm no "Reasignar a" select renders.
- Confirm the separate "Grupos" (inspector+vehículo) section's `asignarGrupoAPuntos`/
  `desasignarGrupo` buttons still assign/unassign a selection of points end to end.

**Phase 3 (roster move)**
- Planeación tab, new "Inspectores" segment: roster lists; search filters by nombre/cédula/código/
  entidad; create-inspector modal creates and shows the assigned brigade code; enable/disable toggles
  and does NOT get stuck on `busy` after two rapid clicks (the exact regression the carried-over
  `finally` guards against).
- Stickers tab: segmented control shows only Evaluaciones/Asignación, opens on Evaluaciones by
  default, no Roster segment reachable anywhere.
- Stickers → Asignación segment, opened in a session where Planeación was never opened: its inspector
  `<select>`/combobox still populates (proves the self-fetch works, not a leftover shared cache).

**Phase 4 (Usuarios fan-out, two live backends)**
- Create an `admin`/`viewer`/`usuario` account — unchanged behavior, row appears in the Usuarios list.
- Create an `inspector` — hits `api/stickers.js`, brigade code shown in the success message, the new
  inspector appears in Planeación's roster segment the next time that tab is opened.
- Create a `conductor` — hits `planeacion_asignaciones.py` (Railway/FastAPI), no Firebase Auth account
  is created, "Conductor creado. Visible en Planeación" message shown, the conductor is selectable
  from the vehículo modal's existing-conductor `<select>`.
- Force a duplicate-cédula conductor failure — modal stays open on `tipo='conductor'`, the FastAPI
  error text is shown inline, and the browser network tab shows no call to `api/usuarios.js` or
  `api/stickers.js` for that submit.
- Type an `@sismocali.gov.co` email under `tipo='admin'` — rejected client-side (no network call),
  message names the inspector tipo.
