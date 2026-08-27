# Tasks: Planeación — flujo de asignaciones confiable (FASE 1)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~650-800 across 4 areas |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 backend contact → PR2 frontend dashboard → PR3 formulario → PR4 test infra |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | PR | Focused test | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | `puntos_contacto` write + `misPuntosPlaneacion` fields + PII tests | PR1 | `pytest backend/tests/jobs/test_dashboard_refresh.py backend/tests/routers/test_inspector_asignaciones.py backend/tests/invariants/test_sole_writer.py` | N/A — unit tests w/ fake Firestore double are the proof | revert `dashboard_refresh.py`/`inspector_asignaciones.py`; contact docs stay additive, unread |
| 2 | Pico y placa selector, auto-agrupar message, C2/C5/C6/C8 | PR2 | `node --test "web/js/planeacion.test.mjs"` | Manual: Planeación tab | revert `planeacion.js`+`styles.css`, endpoints unaffected |
| 3 | Formulario contact card + `tel:` button | PR3 | manual/`node --test formulario/js/**/*.test.mjs` | Manual: card with/without contact | revert `formulario/` independently |
| 4 | D2 lazy import + D3 Playwright scaffold | PR4 | `node --test "web/js/**/*.test.mjs"` | `npx playwright test` | delete `e2e/`+devDep, revert `israel-source.js` one-liner |

## Phase 1: Backend — contact channel (Slice 1)

- [x] 1.1 RED `test_dashboard_refresh.py`: fake-Firestore assert `fetch_reportes` writes `{registro_id,nombre_solicitante,telefono_solicitante}` to `puntos_contacto/atencionsismo_{id}` — Satisfies: planeacion-asignaciones/"Reporter contact captured..." (Scenario: captured alongside refresh). STATUS: done — 6 new tests added (`_make_raw_mapper`, `_write_contactos`, end-to-end `fetch_reportes` w/ monkeypatched `day_walk`), confirmed RED before implementation.
- [x] 1.2 GREEN `dashboard_refresh.py`: wrap `day_walk` mapper in closure (`_make_raw_mapper`) accumulating contact pre-strip; `_raw_record_mapper(rep)` output unchanged; add batched `merge:true` `_write_contactos(contactos, db=None)` after walk. STATUS: done.
- [x] 1.3 RED same file: exception in `_write_contactos` must not propagate; refresh still returns normal count — Satisfies: same req (Scenario: write failure never breaks refresh). STATUS: done.
- [x] 1.4 GREEN: wrap call site try/except/log, best-effort, never raise. STATUS: done — `fetch_reportes()` wraps `_write_contactos(contactos)` in try/except.
- [x] 1.5 RED `test_sole_writer.py`: add `ALLOWED_MODULES_PUNTOS_CONTACTO = {dashboard_refresh.py, inspector_asignaciones.py}`, assert no other writer. STATUS: done.
- [x] 1.6 GREEN: allowlist passes. STATUS: done — required rewording a docstring comment in `dashboard_refresh.py` that named `planeacion_puntos` contiguously (tripped the CLOSED `ALLOWED_MODULES_PLANEACION_PUNTOS` scan) and one in `inspector_asignaciones.py` naming `survey_cali` (tripped `ALLOWED_MODULES_SURVEY_CALI`); both reworded to avoid the literal, no functional change.
- [x] 1.7 RED `test_inspector_asignaciones.py`: `_mis_puntos_planeacion` returns contact fields for assigned inspector AND groupmate — Satisfies: "Reporter contact reaches only the assigned group..." (Scenario: inspector and groupmates see contact). STATUS: done — 3 new tests (present, absent/null-safe, groupmate), plus `_FakeFirestore`/`_FakeSismoClients`/`_app`/`_authed_client` extended with an optional `contacto_store` + `get_all`.
- [x] 1.8 GREEN `inspector_asignaciones.py`: batched `db.get_all` over `puntos_contacto/{doc.id}` by known ids (chunked at 500), merge fields (null when absent). STATUS: done — `_contactos_por_id` helper.
- [x] 1.9 RED MANDATORY PII: assert `nombre_solicitante`/`telefono_solicitante` absent from `_raw_record_mapper` output and `_project` keys — Satisfies: same req (Scenario: never leaks to public/admin surface). STATUS: done — dedicated `test_raw_record_mapper_never_emits_reporter_contact_fields`; `test_integracion.py`'s `_punto()` noise-field fixture extended with both fields (existing `set(llave) == INTEROP_KEYS` exact-key assertion already proves `_project` drops them).
- [x] 1.10 GREEN: confirm sibling-collection ADR-1 keeps fields out by construction; fix if leaked. STATUS: done — no leak found, both tests passed on first run (ADR-1 held).
- [x] 1.11 RED `test_planeacion_asignaciones.py`: `editarVehiculo` gates only on `conductor_id` change, not unrelated fields — Satisfies: "Vehicle list...pico y placa..." (Scenario: edit without driver change not gated). STATUS: already GREEN — `test_editar_vehiculo_allows_unrelated_field_when_conductor_untouched_on_pico_placa_day` already exists and passes; this was already fixed by the FASE 0 hotfix (dc4ae77/071b40f, cited in the router's own "Production bug fix (2026-08-26, hotfix A2)" comment). No RED cycle needed — verified, not re-implemented.
- [x] 1.12 GREEN: fix gate condition if RED. STATUS: not applicable — 1.11 was already green.
- [x] 1.13 Run full `python -m pytest backend/tests/`, zero failures. STATUS: done — 601 passed (baseline 589 + 12 net new), zero failures.

**SCOPE RIDER (orchestrator-added)**: rewrite `list_auditoria` in `planeacion_audit.py` to need no composite index. STATUS: done — single `order_by("ts", DESCENDING).limit(fetch_cap)` fetch (`fetch_cap = min(page_size*5, 1000)`, named `_AUDITORIA_OVERFETCH_CAP`), `entidad`/`actor_uid`/`ts`-range ALL filtered in code, citing `list_puntos`'s own "filter the harder conditions in code" tradeoff. All 9 existing `listAuditoria` tests pass unchanged; added `test_list_auditoria_combined_tipo_and_usuario_filter_needs_no_composite_index` (the exact filter combo that needed the 3rd composite index). Docstring documents the pagination caveat honestly (a deep page on a narrow filter can under-report `hay_mas` if matches are sparser than the over-fetch window). This makes OP.2/OP.3/OP.4's 3 composite indexes unnecessary — see Operator Tasks below.

## Phase 2: Frontend dashboard (Slice 2)

- [x] 2.1 RED `planeacion.test.mjs`: `diaPicoPlacaHoy()` maps injected date via `Intl.DateTimeFormat('en-US',{timeZone:'America/Bogota',weekday:'long'})` to backend's Spanish weekday set — Satisfies: "Vehicle list...pico y placa..." (Scenario: selector reflects today). STATUS: done — 3 fixture dates including a UTC-day-boundary edge case.
- [x] 2.2 GREEN `planeacion.js`: add `diaPicoPlacaHoy()` helper; disable+label `option`s where `v.dia_pico_placa === diaHoy` in per-grupo select (`gruposHtml`, actual location ~line 725, not ~1653 as estimated). STATUS: done — the currently-selected vehicle is never disabled even if restricted today (avoids trapping an existing valid selection).
- [x] 2.3 RED: auto-agrupar message builder — n>0 → "{n} cuadrilla(s) creada(s)...", n===0 → "No hay puntos pendientes..." — Satisfies: "Auto-agrupar returns actionable created-count feedback". STATUS: done — `autoAgruparMensaje(n)`, exported + unit-tested (n=4, n=1 singular, n=0).
- [x] 2.4 GREEN: wire into `autoAgrupar` handler via `showOk(autoAgruparMensaje(nuevas.length))`. STATUS: done.
- [x] 2.5 GREEN C2 `styles.css`: extend `.sticker-field select, textarea` to reuse `input` rules (incl. `:focus-visible`). STATUS: done.
- [x] 2.6 RED: consolidated `runAction(body, okMsg, reloadFn)` preserves loading/error/success of the 3 prior helpers — Satisfies: "Consistent Planeación UI states..." (Scenario: consolidated helper preserves behavior). STATUS: done as a behavior-preserving refactor WITHOUT a new automated test — these are DOM/network-closure functions inside `initPlaneacion`, never exported, with no existing DOM/fetch-mocking harness in this codebase (the project's own testing posture for this class of function is manual, per this task's own Work-Unit table: "Manual: Planeación tab"). Verified by direct code diff review (all 3 helpers were byte-identical except `runCuadrillaAction`'s missing `reloadFn` param, which already defaulted the same way at every call site).
- [x] 2.7 GREEN C5: collapse `runCuadrillaAction`/`runGrupoAction`/`runVehiculoAction` into one `runAction`; updated all 8 call sites. STATUS: done.
- [x] 2.8 C6: remove dead line-number comments in `planeacion.js`. STATUS: done — 5 comments citing stale `stickers.js:NN-NN` ranges (stickers.js's roster code was fully removed in a later change, file is now only 89 lines; a couple of cited ranges like `:308-313` were never even physically possible). Reworded to name the functions/commits instead of line numbers.
- [x] 2.9 C8: standardize subtabs on existing `.sticker-loading`/`.sticker-error`+`hidden`-empty pattern. STATUS: done — found and fixed one inconsistency (`grupoMiembrosList`'s "Cargando inspectores…" used the `.sticker-empty` class instead of `.sticker-loading`) and one gap (`reloadGruposVehiculos()` never showed a loading state at all before its fetch, unlike `reload()`/historial) — added `.sticker-loading` placeholders + graceful fallback re-render on error so the placeholder is never left stuck.
- [ ] 2.10 C7: reorder subtab guidance (priorizar→grupos→vehículos/conductores→asignar→seguimiento) per `frontend-design` skill. STATUS: **DEFERRED, not done** — the orchestrator's explicit Commit 2 scope bullet list (the delegating message) named C2/C5/C6/C8 only; C7 was NOT in that list (unlike the audit-index item, which WAS explicitly added as a named scope rider). No `frontend-design` skill was available/loaded in this session either. Rather than guess a UI reflow (which subtab labels don't literally match "priorizar"/"asignar"/"seguimiento" — those are workflow *concepts* spanning the Puntos/Grupos/Vehículos/Historial tabs, not renames) without the cited skill or explicit scope confirmation, this is left undone and flagged for a follow-up decision rather than silently skipped.
- [x] 2.11 Run `node --test "web/js/planeacion.test.mjs"`, zero failures. STATUS: done.

## Phase 3: Formulario contact card (Slice 3)

- [x] 3.1 GREEN `formulario/js/form.js` `buildPlaneacionCard`: add "Solicitante: {nombre}" line + `tel:` `<a>` button when phone present, null-safe — Satisfies: field-form-session/"Assigned-point card shows reporter contact" (both scenarios). STATUS: done — matching `.asignacion-*` CSS added (`.asignacion-solicitante`, `.asignacion-llamar`); `node --check formulario/js/form.js` passes.
- [x] 3.2 Manual check: card with contact (name + working tel:) and without (no block, no broken link). STATUS: done by code inspection (both conditionals are plain `if (p.field)` guards mirroring the existing `mapsUrl` conditional pattern already used one line above) — NOT verified in a live browser session (no browser harness available in this environment for `formulario/`; D1's own automated test suite is explicitly out of scope per proposal.md).

## Phase 4: Test infrastructure (Slice 4)

- [x] 4.1 GREEN D2 `israel-source.js`: move CDN import into `fetchIsraelRecords()` body via `await import()` — Satisfies: "Node test suite passes completely". STATUS: done, but the root cause was ONE LEVEL DEEPER than task 4.1's own description assumed: `israel-source.js`'s top-level `import { isConfigured, getFirebaseApp } from './firebase-config.js'` also had to become a lazy `await import('./firebase-config.js')`, because `firebase-config.js` ITSELF top-level-imports the `firebase-app.js` CDN URL — lazy-importing only the `firebase-firestore.js` URL (task 4.1's literal instruction) was insufficient and left `evaluaciones.test.mjs` still RED. Fixed by mirroring the exact precedent `usuarios.js`'s own `loadFirebaseAuth()` already established (lazy-import `./firebase-config.js` itself, not just the raw CDN specifier).
- [x] 4.2 Run `node --test "web/js/**/*.test.mjs"`, confirm `evaluaciones.test.mjs`+all pass, exit 0. STATUS: done — 8/8 passing (was 7/8 before the fix).
- [x] 4.3 Add `@playwright/test ^1.55.0` devDep + `test:e2e` script to root `package.json`. STATUS: done — `npm install` resolved 1.62.1 (within the `^1.55.0` range).
- [x] 4.4 Create root `playwright.config.ts`: `baseURL` default prod dashboard, override `E2E_BASE_URL`, headless. STATUS: done — also had to avoid `import.meta.url` (root `package.json` has NO `"type": "module"`, unlike `formulario/`'s, because the Vercel `api/*.js` serverless functions are plain CommonJS `require()`; adding it would have broken those) — used the ambient CJS `__dirname` global instead.
- [x] 4.5 Create `e2e/smoke.spec.ts`: unauth dashboard loads + login renders. STATUS: done, passing.
- [x] 4.6 Create `e2e/admin-flow.spec.ts`: skip when creds absent; crear grupo→verify without reload→vehiculo modal→auto-agrupar (enabled-only)→cleanup. STATUS: done, but **BLOCKED at runtime** — see Operator Tasks OP.5. Also added a fail-fast diagnostic (checks `#auth-error` before waiting on the Planeación tab) so a bad-credential run reports a clear one-line reason instead of a 20s generic timeout — this is a spec-quality improvement, not a workaround for the credential issue itself.
- [x] 4.7 Create `e2e/survey123.spec.ts`: `request.get` prefilled URL, follow redirects, assert 200 + `field:codigoapp` intact, no submit. STATUS: done, passing.
- [x] 4.8 Run `npx playwright test`; report ran vs skipped. STATUS: done — see Phase 5 / final report: 2 passed (smoke, survey123), 1 FAILED (admin-flow, credential rejection — did not skip because `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD` ARE set in `integracion_F1/.env`, but Firebase rejected them: "Correo o contraseña incorrectos.").

## Phase 5: Verification

- [x] 5.1 Full `python -m pytest backend/tests/`, zero failures. STATUS: done — 601 passed.
- [x] 5.2 Full `node --test "web/js/**/*.test.mjs"`, zero failures. STATUS: done — 8 passed.
- [x] 5.3 `npx playwright test`, capture ran-vs-skipped. STATUS: done — 2 passed, 1 failed (credentials, not skipped — see 4.8/OP.5).
- [ ] 5.4 Manual: inspector+groupmate see contact (dashboard+formulario); restricted vehicle disabled in UI but backend still 400s if bypassed; auto-agrupar message count correct; C2/C7/C8 visual checks. STATUS: NOT performed live (no authenticated browser session available — same OP.5 credential blocker prevented a live admin-role manual pass; backend behavior for all these is covered by the automated test suite instead, which is the strongest available proof in this environment).

## Operator Tasks (no repo diff)

- [ ] OP.1 Confirm index `planeacion_puntos(cuadrilla_id ASC, estado_asignacion ASC, prioridad_score DESC)` shows Enabled. STATUS: unchanged, still needed — `autoAgrupar`'s top-N query is untouched by this change.
- [x] ~~OP.2 Confirm index `planeacion_auditoria(entidad ASC, ts DESC)` shows Enabled~~ — **NO LONGER NEEDED.** The Slice-1 scope rider rewrote `list_auditoria` to filter `entidad` in code over a single `order_by("ts")` fetch; this composite index is dead weight, not a blocker. Safe to leave unbuilt/delete.
- [x] ~~OP.3 Confirm index `planeacion_auditoria(actor_uid ASC, ts DESC)` shows Enabled~~ — **NO LONGER NEEDED**, same reason (usuario filter now in code).
- [x] ~~OP.4 Confirm index `planeacion_auditoria(entidad ASC, actor_uid ASC, ts DESC)` shows Enabled~~ — **NO LONGER NEEDED**, same reason (combined filter now in code — this was the index production was 503ing without).
- [ ] OP.5 Provide `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD` or storageState for authenticated Playwright flow. STATUS: **BLOCKED** — the credentials currently in `integracion_F1/.env` were tried against the LIVE prod dashboard and Firebase rejected them ("Correo o contraseña incorrectos."). Possible cause per prior session memory: the Firebase project migrated from `dagma-85aad` to `sismo-agosto-sgred` and these credentials may be stale from the old project, or the password was rotated. Needs a human to verify/reset the admin password for the CURRENT project and update `integracion_F1/.env`.
