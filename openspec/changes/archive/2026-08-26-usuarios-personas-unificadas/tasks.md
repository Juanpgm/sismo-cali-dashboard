# Tasks: Usuarios/Personas unificadas

Change: `usuarios-personas-unificadas` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-tasks

Reads `proposal.md` (6 decisions, Q1-Q4 proposal question round), `design.md` (ADR-1..4), and the
three delta specs (`user-management`, `planeacion-asignaciones`, `stickers-asignacion`). Ordered,
hierarchical, grouped by phase per `openspec/config.yaml` (`group by phase`, `hierarchical
numbering`, `completable in one session`). Phases follow `design.md`'s locked slice order: Slice A
→ hide-individual-assignment → Slice B → Slice C (C lands last — see proposal/design rationale).
`strict_tdd: true`. Test runner: **`node --test "js/**/*.test.mjs"` run from `web/`**.

**Honesty note on test coverage (per design.md's Testing Strategy):** RED→GREEN only applies to the
PURE helpers (`payloadForTipo`, the ported `rowHtml`/`filterRosterInspectores`). Everything DOM
(segment toggles, modal wiring, the two-backend fan-out) gets an explicit **MANUAL SMOKE** task —
this repo has no headless-DOM/browser harness, so faking a unit test around `document`/`fetch`
would be theater, not coverage.

**CRITICAL constraint carried into every task below**: target only `web/js/planeacion.js`,
`web/js/stickers.js`, `web/js/stickers-asignacion.js`, `web/js/usuarios.js`. `api/usuarios.js` and
`api/stickers.js` are read, never edited. `backend/app/routers/usuarios.py` is OUT OF SCOPE — no
task here may touch it.

**Delivery**: `ask-on-risk`. Four chained PRs, one per phase below — this matches `design.md`'s own
slice boundaries and lets each land/rollback independently. See the Review Workload Forecast at the
end for the size call on each.

## Dependency graph

    Phase 1 (Slice A)  ─┐
    Phase 2 (hide indiv)─┼─ independent of each other, independent of Phase 3
    Phase 3 (Slice B)   ─┘
                          Phase 4 (Slice C) depends on Phase 3 landing first
                          (conductor/inspector success copy names Planeación
                          as the roster's home — design.md "Migration/Rollout")

---

## Phase 1 — Slice A: Vehiculo modal cleanup (`web/js/planeacion.js`)

Commit: `fix(planeacion): remove inline conductor creation from vehiculo modal`

Depends on: none.

- [x] **1.1** STATUS: DONE (commit `5df7278`). Delete the inline "Crear conductor" fieldset markup:
      `#planeacion-vehiculo-conductor-nuevo` (`planeacion.js:526-540`). The `empresa` input
      (520-522) and the existing-conductor `<select>` (523-525) stay untouched.
      — Satisfies: spec `planeacion-asignaciones` Requirement "Vehiculo modal assigns only an
      existing conductor" (no-inline-creation-UI scenario).

- [x] **1.2** STATUS: DONE (commit `5df7278`). Delete the JS side of the inline-create flow: `conductorNuevoBox` ref (1452),
      `NUEVO_CONDUCTOR` sentinel + its `<option>` push (1453, 1461), `syncConductorNuevo` function +
      its `change` listener + its call in `openVehiculoModal` (1464-1467, 1479), the
      conductor-field reset loop (1476-1478), and the two-step save branch (1504, 1507-1519)
      **including** the stale `// ponytail: a vehiculo-create failure after crearConductor leaves an
      orphan driver…` comment (1520-1521) — that comment documents a risk of the branch being
      deleted here, so it must go with it. Collapse the save handler to
      `const conductorId = conductorSelect.value;` (no `nuevoConductor` branch). **KEEP**
      `buildConductorPayload` (240-248, exported) — Slice C's conductor branch (Phase 4) reuses it.
      — Satisfies: spec `planeacion-asignaciones` Requirement "Vehiculo modal assigns only an
      existing conductor" (existing-conductor-save scenario).

- [x] **1.3** STATUS: DONE. `node --test "js/planeacion.test.mjs"` green (1 pass); `buildConductorPayload`/`buildVehiculoPayload` bodies untouched. Regression check (pure deletion, no new logic → no RED/GREEN needed): run
      `node --test "js/**/*.test.mjs"` from `web/` and confirm `planeacion.test.mjs`'s existing
      `buildConductorPayload`/`buildVehiculoPayload` assertions still pass unchanged — neither
      function's body is touched by 1.1/1.2, only their (now sole) caller shape.
      — Satisfies: `design.md` ADR-3.

- [x] **1.4** STATUS: MANUAL SMOKE PENDING — user must verify (see apply-progress.md). MANUAL SMOKE (DOM, no harness): open the vehículo modal for create and for edit;
      confirm no "Crear nuevo conductor" option or fieldset is reachable; save with an existing
      conductor selected → `conductor_id`/`empresa` persist and no `crearConductor` network call
      fires; save with "— Sin conductor —" selected still works.
      — Satisfies: spec `planeacion-asignaciones` Requirement "Vehiculo modal assigns only an
      existing conductor" (both scenarios, end to end).

---

## Phase 2 — Hide individual inspector-assignment controls (`web/js/planeacion.js`)

Commit: `feat(planeacion): hide individual inspector-assignment controls (group-only)`

Depends on: none (independent of Phase 1 and Phase 3; backend branches stay callable, this is
markup-only).

- [x] **2.1** STATUS: DONE (commit `7ac81ea`). Delete the cuadrilla-level assign combobox: the `.asignacion-combo` block in
      `cuadrillasHtml` (636-642) and its lazy-mount wiring in `renderCuadrillasSection`
      (1317-1332, the `querySelectorAll('[data-combo-cuadrilla]')` loop). `querySelectorAll` on the
      now-absent selector returns an empty NodeList, so no dangling reference is left.
      — Satisfies: spec `planeacion-asignaciones` Requirement "Planeación UI — priority table, map,
      and correction affordances" (no-individual-control scenario, cuadrilla-combobox part).

- [x] **2.2** STATUS: DONE (commit `7ac81ea`). Delete the individual desasignar button: the `data-desasignar` button markup in
      `cuadrillasHtml` (644) and its wiring block in `renderCuadrillasSection` (1334-1339).
      — Satisfies: same requirement, desasignar-button part.

- [x] **2.3** STATUS: DONE (commit `7ac81ea`). `mountCombobox`/`inspectorLabelFor` left in place as harmless dead code (not listed for removal in this task; no other task references them). Delete the map per-point reassign control: the "Reasignar a" `<label>`/`<select>`
      block in `popupHtml` (867-870); the `reasignar()` function (1673-1684); and its use as
      `renderMap`'s 3rd argument at `renderMap(currentRows(), inspectores, reasignar)` (1688) — drop
      the argument (call `renderMap(currentRows(), inspectores)`) and drop the now-unused
      `onReasignar` parameter from `renderMap`'s own signature too (don't leave a parameter that is
      always `undefined`). Confirm the `popupopen` handler's own
      `if (!sel) return` guard (~line 916, `querySelector('[data-reasignar-select]')` now always
      null) makes the missing markup a safe no-op — do not add a second guard, the existing one
      already covers it.
      — Satisfies: same requirement, map-reassign-select part.

- [x] **2.4** STATUS: MANUAL SMOKE PENDING — user must verify (see apply-progress.md). MANUAL SMOKE: open a cuadrilla row in the Grupos/Cuadrillas view — confirm no inspector
      combobox or "Quitar asignación" button renders; open a map point popup — confirm no
      "Reasignar a" select renders; confirm the separate "Grupos" (inspector+vehículo) section's
      `asignarGrupoAPuntos`/`desasignarGrupo` buttons (`runGrupoAction`, 1656-1671, untouched) still
      assign/unassign a selection of points end to end — this is the one remaining assignment path.
      — Satisfies: spec `planeacion-asignaciones` Requirement "Planeación UI — priority table, map,
      and correction affordances" (no-individual-control and group-assignment-still-works
      scenarios, end to end).

---

## Phase 3 — Slice B: move Inspectores roster Stickers → Planeación

Commit: `feat(planeacion): move Inspectores roster from Stickers`

Depends on: none at the code level (independent of Phase 1/2); lands before Phase 4 per
`design.md`'s ordering.

- [x] **3.1** STATUS: DONE (RED confirmed — `SyntaxError: does not provide an export named 'filterRosterInspectores'` before 3.2, commit `69f2396` includes both RED+GREEN). (RED) Add to `web/js/planeacion.test.mjs` assertions for two pure helpers NOT YET
      exported from `planeacion.js` — MUST fail (undefined import) until 3.2:
      - `rowHtml(i)` — mirrors `stickers.js:35-57`'s cases: active vs. `disabled`/`!activo` renders
        the right pill + toggle button `data-enable` value; missing `i.registrado` renders the "sin
        perfil" warning; missing `i.codigo` falls back to `—`.
      - `filterRosterInspectores(inspectores, query)` — accent/case-insensitive match across
        nombre/cédula/código/entidad, ported from `stickers.js:64-76`'s `filterInspectores` +
        `normalizeSearch`. **Named differently on purpose**: `planeacion.js` already exports its
        OWN `filterInspectores` (line 250, a narrower nombre/código/cédula-only match used by the
        assign-inspector combobox) — porting the roster's richer search under the same name would
        silently shadow or collide with it. Keep both functions; do not merge them.
      — Satisfies: spec `planeacion-asignaciones` Requirement "Inspector roster CRUD lives in
      Planeación" (pure-logic side, indirectly backs the roster-usable scenario).

- [x] **3.2** STATUS: DONE (commit `69f2396`); F5-toggle `finally` reset carried over verbatim; `refreshInspectoresAfterWrite()` forces `inspectoresLoaded=false` + `ensureInspectores()` after create/toggle. (GREEN) Port `rowHtml`, `rosterListHtml`, `rosterHtml`, the create-inspector modal
      markup, and `wire()` from `stickers.js:35-149,249-330` into `planeacion.js` as a new
      "Inspectores" segment: add a 5th `data-subtab-btn="inspectores"` nav entry to `shellHtml`
      (270-285) and a matching `<section class="planeacion-subpanel" data-subtab="inspectores">`.
      Reuse the existing `callStickersApi`/`inspectoresCache`/`ensureInspectores`
      (`planeacion.js:79-90`, `~1706-1711`) for the ported `list`/`create`/`setEnabled` calls — do
      not add a second Stickers HTTP client. Rename the ported search helper to
      `filterRosterInspectores` per 3.1. Run 3.1, confirm green.
      - **CRITICAL**: the ported row-toggle handler MUST carry the
        `finally { busy = false; btn.disabled = false; }` reset from `stickers.js:308-313` (the
        F5-toggle fix, commit `7977fb7`) **verbatim** — copy the whole try/catch/finally, do not
        paraphrase it into something that drops the `finally` and reintroduces the stuck-`busy` bug.
      - After a successful create, force a re-fetch of `inspectoresCache`/`ensureInspectores` (not
        just the roster segment's own local list) so every OTHER Planeación section's inspector
        `<select>`/combobox (cuadrillas, grupos, map popup) sees the new person without a full tab
        reload.
      — Satisfies: spec `planeacion-asignaciones` Requirement "Inspector roster CRUD lives in
      Planeación" (both scenarios).

- [x] **3.3** STATUS: DONE (commit `69f2396`); `evaluaciones` is now default active segment; unused `escapeHtml` import dropped. Shrink Stickers: in `web/js/stickers.js`, delete the roster segment button + its
      `data-sticker-section="roster"` wrapper (167, 172-174), the `roster:` entry from `sections`
      (195), the `rosterRoot`/roster `reload()`/`wire()` path (192, 231-247, 249-330), and the
      now-dead `rowHtml`/`rosterListHtml`/`rosterHtml`/`filterInspectores`/`normalizeSearch`
      helpers (35-149) — grep the rest of the file first to confirm `field()`/`initials()`
      (33, 59-62) have no other caller before deleting them too (they were roster-only). Change the
      segmented control to 2-way (Evaluaciones · Asignación) in `shellHtml` (159-177): drop the
      "Inspectores" button, and make `evaluaciones` the default active segment (`is-active`/
      `aria-selected="true"` swap + the corresponding `sections.evaluaciones.hidden = false` /
      others hidden at init).
      — Satisfies: spec `stickers-asignacion` Requirement "Mounted as a sub-section of the existing
      Stickers tab" (2-way-segmented-control scenario).

- [x] **3.4** STATUS: DONE (commit `69f2396`); local `getInspectores()` getter kept so the rest of the file's call sites needed zero changes. Make the Asignación sub-section fetch its own inspector roster: in
      `web/js/stickers-asignacion.js`, add a small `callStickersApi(getToken, body)` hitting
      `apiUrl('stickers')` (import `apiUrl` from `./api-config.js`; this is the SAME 8-line copy
      `planeacion.js:79-90` already carries — a 3rd near-identical copy is fine, not worth
      extracting a shared module for 8 lines per this repo's own precedent). Call `{action:'list'}`
      once during `initStickersAsignacion`'s init (and again on `reload()`), cache the result
      locally, and stop depending on the `getInspectores` callback `stickers.js` used to pass in
      (that cache no longer exists after 3.3). Update the `initStickersAsignacion(sections.asignacion,
      { getToken, getInspectores: () => inspectoresCache })` call in `stickers.js` (~223-226) to
      drop the now-removed `getInspectores` argument.
      — Satisfies: spec `stickers-asignacion` Requirement "CRUD affordances in the frontend"
      (inspector-dropdown-fetches-its-own-roster scenario).

- [x] **3.5** STATUS: MANUAL SMOKE PENDING — user must verify (see apply-progress.md). MANUAL SMOKE (DOM-heavy, no harness):
      - Planeación tab, new "Inspectores" segment: roster lists, search filters by nombre/cédula/
        código/entidad, create-inspector modal creates and shows the assigned brigade code,
        enable/disable toggles and does **not** get stuck on `busy` after two rapid clicks (the
        exact regression 3.2's carried-over `finally` guards against).
      - Stickers tab: segmented control shows only Evaluaciones/Asignación, opens on Evaluaciones by
        default, no Roster segment reachable anywhere.
      - Stickers → Asignación segment, opened in a session where Planeación was never opened: its
        inspector `<select>`/combobox still populates (proves 3.4's self-fetch works, not a leftover
        shared cache).
      — Satisfies: spec `stickers-asignacion` Requirement "Mounted as a sub-section of the existing
      Stickers tab" (segmented-control-2-way, inspector-dropdown scenarios, end to end).

- [x] **3.6** STATUS: DONE. 6/7 pass; the 1 fail is the pre-existing unrelated `evaluaciones.test.mjs` ESM/https-import failure noted in the apply brief (exists on `main` before this change too). Run `node --test "js/**/*.test.mjs"` from `web/`, confirm all green (existing suites
      untouched + 3.1's new assertions).
      — Satisfies: `openspec/config.yaml` tasks rule ("keep tasks completable in one session").

---

## Phase 4 — Slice C: Usuarios `tipo` selector fan-out (`web/js/usuarios.js`)

Commit: `feat(usuarios): unified tipo selector fans out to inspector/conductor/usuario endpoints`

Depends on: Phase 3 (the conductor/inspector success copy names Planeación as the roster's home;
`design.md` "Migration/Rollout" orders C last for this reason).

- [x] **4.1** STATUS: DONE. RED confirmed by temporarily renaming the export and re-running the
      exact same test file — real `SyntaxError: does not provide an export named 'payloadForTipo'`
      before restoring. Also required an unplanned infra fix: `usuarios.js` (and transitively
      `firebase-config.js`) statically imported `getAuth`/`sendPasswordResetEmail` from a
      `https://www.gstatic.com/...` CDN URL, which Node's ESM loader cannot resolve
      (`ERR_UNSUPPORTED_ESM_URL_SCHEME`) — the exact same failure mode as the pre-existing
      `evaluaciones.test.mjs`. Converted both Firebase imports to a lazy `loadFirebaseAuth()`
      dynamic `import()` scoped to the two call sites that actually need them (`reload()`'s
      `ownUid` lookup, the "Resetear contraseña" click) so importing `usuarios.js` for its pure
      `payloadForTipo` never touches the CDN import path. Zero behavior change in the browser
      (dynamic import of the same specifier is cached after first use). (RED) Create `web/js/usuarios.test.mjs` (new file) asserting a pure
      `payloadForTipo(tipo, fields)` NOT YET exported from `usuarios.js` — MUST fail until 4.2:
      - `tipo` in `{admin,viewer,usuario}` → `{endpoint:'usuarios', body:{action:'create', email,
        password}}`.
      - `tipo:'inspector'` → `{endpoint:'stickers', body:{action:'create', cedula,
        nombre_completo, entidad, password}}`.
      - `tipo:'conductor'` → `{endpoint:'planeacionAsignaciones', body:{action:'crearConductor',
        nombre_completo, cedula, email, telefono}}`.
      - `tipo` in `{admin,viewer,usuario}` with an email ending `@sismocali.gov.co` → throws, message
        names `inspector` as the correct tipo (client-side fast-fail mirroring the spec scenario;
        `api/usuarios.js`'s own server-side guard, untouched, remains the authoritative check —
        this is a UX nicety, not a replacement).
      - unknown `tipo` → throws.
      — Satisfies: spec `user-management` Requirement "Create user" (all 5 scenarios, routing side);
      Requirement "Per-tipo error isolation in the unified creation modal" (routing side).

- [x] **4.2** STATUS: DONE; GREEN confirmed (1/1 pass). (GREEN) Implement and export `payloadForTipo` in `usuarios.js` per 4.1. Run 4.1,
      confirm green.
      — Satisfies: same as 4.1.

- [x] **4.3** STATUS: DONE. Add the `tipo` selector + field-swap to the create modal (`rosterHtml`'s form,
      145-169): a `<select id="usuario-form-tipo">` with options
      `admin|viewer|usuario|inspector|conductor` (labels: Administrador/Viewer/Usuario/Inspector/
      Conductor, neutral Spanish infinitive elsewhere in the copy). On `change`, show exactly one
      field group via the existing `field()` helper (33-36): email+password (default —
      admin/viewer/usuario), cedula+nombre_completo+entidad+password (inspector), nombre+cedula+
      email+telefono (conductor). Toggle both `hidden` AND each hidden group's inputs' `disabled`
      attribute, so `FormData` never picks up a stale value left over from a previously-selected
      tipo.
      — Satisfies: spec `user-management` Requirement "Create user" (field-swap side, all create
      scenarios).

- [x] **4.4** STATUS: DONE — `callApi` itself parameterized (`endpointName = 'usuarios'`), no separate fetch function forked. Wire the submit handler (491-510) to call 4.2's `payloadForTipo`, then dispatch to the
      returned `endpoint`. `usuarios.js` currently hardcodes `ENDPOINT = '/api/usuarios'` (line 14)
      and `callApi` always targets it (16-27) — replace the hardcoded literal with
      `apiUrl('usuarios')` (import `apiUrl` from `./api-config.js`) and parameterize
      `callApi(getToken, body, endpointName = 'usuarios')` so the SAME helper (same headers/
      error-unwrap shape) also serves the `stickers` and `planeacionAsignaciones` branches — do not
      fork three near-identical fetch functions. Prefix success copy per tipo per the spec's table:
      `Usuario creado: {email}` / `Inspector creado. Código: {codigo}` / `Conductor creado. Visible
      en Planeación`.
      — Satisfies: spec `user-management` Requirement "Create user" (dispatch side, all create
      scenarios); Requirement "Per-tipo error isolation in the unified creation modal" (both
      scenarios, dispatch side).

- [x] **4.5** STATUS: DONE. Per-tipo inline error surfacing: on a failed create, keep the modal open with the SAME
      `tipo` still selected (do not reset the `<select>` on error), show `usuario-form-error`
      prefixed by that tipo's own label (e.g. `Conductor: cédula duplicada.`), and make no follow-up
      call to either of the other two endpoints from the same submit — each tipo is exactly one
      write, the catch block must not fall through to a second attempt.
      — Satisfies: spec `user-management` Requirement "Per-tipo error isolation in the unified
      creation modal" (both scenarios).

- [x] **4.6** STATUS: DONE — `git diff --stat api/usuarios.js` empty throughout; confirmed byte-identical. Confirm `api/usuarios.js`'s `@sismocali.gov.co` server-side rejection is untouched by
      this slice (no edit to that file at all — grep it before/after and diff to confirm byte-
      identical). 4.1's client-side pre-check is additive UX only, never a replacement for it.
      — Satisfies: spec `user-management` Requirement "Create user" (`@sismocali`-still-rejected
      scenario).

- [x] **4.7** STATUS: MANUAL SMOKE PENDING — user must verify (see apply-progress.md). MANUAL SMOKE (DOM-heavy, two live backends — Vercel + Railway/FastAPI, per design.md's
      honesty note):
      - Create an `admin`/`viewer`/`usuario` account — unchanged behavior, row appears in the
        Usuarios list.
      - Create an `inspector` — hits `api/stickers.js`, brigade code shown in the success message,
        the new inspector appears in Planeación's roster segment (Phase 3) without reloading that
        tab.
      - Create a `conductor` — hits `planeacion_asignaciones.py` (Railway/FastAPI), no Firebase Auth
        account is created, "Conductor creado. Visible en Planeación" message shown, the conductor
        is selectable from the vehículo modal's existing-conductor `<select>` (Phase 1).
      - Force a duplicate-cédula conductor failure — modal stays open on `tipo='conductor'`, the
        FastAPI error text is shown inline, and the browser network tab shows no call to
        `api/usuarios.js` or `api/stickers.js` for that submit.
      - Type an `@sismocali.gov.co` email under `tipo='admin'` — rejected, message names the
        inspector tipo.
      — Satisfies: spec `user-management` Requirement "Create user" (all 6 scenarios, end to end);
      Requirement "Per-tipo error isolation in the unified creation modal" (both scenarios, end to
      end).

- [x] **4.8** STATUS: DONE. 7/8 pass; the 1 fail is the same pre-existing unrelated `evaluaciones.test.mjs`
      failure as Phase 3 (CDN import, exists on `main` before this change). `usuarios.test.mjs`
      itself: 1/1 pass. Run `node --test "js/**/*.test.mjs"` from `web/`, confirm all green (existing suites
      untouched + 4.1's new `usuarios.test.mjs`).
      — Satisfies: `openspec/config.yaml` tasks rule ("keep tasks completable in one session").

---

## Review Workload Forecast

- **Estimated changed lines (rough, per phase — deletions count toward the diff same as
  additions):**
  - Phase 1 (Slice A, `planeacion.js` deletions only): **~55-70 lines**.
  - Phase 2 (hide individual assignment, `planeacion.js` deletions + one call-site edit):
    **~55-70 lines**.
  - Phase 3 (Slice B — roster move): `stickers.js` net removal (~220 lines: `rowHtml`/
    `rosterListHtml`/`rosterHtml`/roster `wire()`/segment plumbing) + `planeacion.js` net addition
    (~200 lines: new segment nav/subpanel, ported roster markup+`wire()`, cache-refresh tweak) +
    `stickers-asignacion.js` (~25 lines: local `callStickersApi` + self-fetch wiring) + test
    additions (~20 lines): **~465-520 lines**.
  - Phase 4 (Slice C — Usuarios fan-out): `usuarios.js` (~110-140 lines: selector, field-swap,
    `payloadForTipo`, dispatch, per-branch error copy) + new `usuarios.test.mjs` (~60-80 lines):
    **~170-220 lines**.
  - **Total: roughly 745-880 authored lines across the four phases.**
- **400-line budget risk: High for Phase 3 specifically, Low for Phases 1/2/4.** Phase 3 (the
  roster move) crosses 400 lines on its own — it touches three files across a UI relocation, not a
  simple deletion, and is the one phase that genuinely earns the size. Phases 1, 2, and 4 each sit
  comfortably under 400 lines individually.
- **Chained PRs recommended: Yes — already the natural shape.** Ship as the four phases above, each
  its own PR/commit, in the dependency order stated (1 and 2 can also swap order or land as one
  combined "cleanup" PR since both are small `planeacion.js`-only deletions with no shared lines
  touched; 3 must land before 4).
- **Review lens recommendation, per the standing risk table:** Phases 1/2/4 are Medium risk
  (behavior/state/UI, no security/auth path) → single dominant-risk lens each (`review-reliability`
  for 1/2's deletion-correctness and 4's two-backend fan-out; `review-readability` would also be
  defensible for 1/2's pure deletions). **Phase 3 exceeds the 400-line High-risk threshold** — per
  the standing orchestrator rule, run the full 4R sweep (`review-risk`, `review-resilience`,
  `review-readability`, `review-reliability`) on that PR specifically, not a single lens.
- **Decision needed before apply: No.** `proposal.md`'s Q1-Q4 all ship their stated default answers
  (yes / reject-naming-inspector / toast-only / group-only-is-sufficient) since none was overridden
  at the tasks gate — no task above is blocked on a pending product answer.
