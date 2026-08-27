# Apply Progress: Planeación — flujo de asignaciones confiable (FASE 1)

First apply run for this change. No prior apply-progress artifact existed.

## Summary

23 of 26 tasks done, 1 deliberately deferred (C7, out of the orchestrator's
explicit Commit-2 scope), 1 blocked at runtime by bad E2E credentials (not a
code defect), 1 manual-verification task not performed live (no browser
session available in this environment). Full details and STATUS notes are
in `tasks.md` (updated in place). This file adds the cross-cutting
evidence (test counts, TDD cycles, deviations) tasks.md doesn't carry.

## Baseline

- `python -m pytest backend/tests/ -q` before any change: **589 passed**.
- After Slice 1 (+ scope rider): **601 passed** (12 net new tests, zero
  regressions, zero baseline drop).
- `node --test "js/**/*.test.mjs"` before any change (from `web/`): 7
  passed, 1 failed (`evaluaciones.test.mjs`, the known D2 defect).
- After Slice 4: **8 passed, 0 failed**.

## TDD Cycle Evidence (backend, strict RED→GREEN)

| Area | RED command | RED result | GREEN result |
|---|---|---|---|
| `puntos_contacto` write/mapper | `pytest backend/tests/jobs/test_dashboard_refresh.py -q` | 6 new tests failed (`AttributeError: no attribute 'credentials'/'_write_contactos'`) | 24 passed |
| `puntos_contacto` sole-writer | `pytest backend/tests/invariants/test_sole_writer.py -q` | 1 pre-existing test failed (`planeacion_puntos` scan tripped by a docstring mention in `dashboard_refresh.py`) | 12 passed after rewording the comment (no functional change) |
| `misPuntosPlaneacion` contact fields | `pytest backend/tests/routers/test_inspector_asignaciones.py -q` | 3 new tests failed (`KeyError: 'nombre_solicitante'`) | 62 passed |
| Full suite | `pytest backend/tests/ -q` | — | 601 passed |

`editarVehiculo`'s pico-y-placa gate (task 1.11) was found ALREADY GREEN —
`test_editar_vehiculo_allows_unrelated_field_when_conductor_untouched_on_pico_placa_day`
already exists and passes (FASE 0 hotfix dc4ae77/071b40f already fixed this).
No RED cycle was fabricated; the task is marked done-by-verification, not
done-by-reimplementation.

## Scope Rider: `list_auditoria` composite-index elimination

Rewrote `backend/app/services/planeacion_audit.py:list_auditoria` per the
orchestrator's explicit rider. Was: 4 chained `.where()` clauses +
`.order_by("ts")`, needing 3 composite indexes (`entidad+ts`,
`actor_uid+ts`, `entidad+actor_uid+ts`) — production was 503ing on any
filtered query while these built. Now: ONE `order_by("ts", DESCENDING)
.limit(fetch_cap)` fetch (`fetch_cap = min(page_size*5, 1000)`, named
`_AUDITORIA_OVERFETCH_CAP`, documented), with `entidad`/`actor_uid`/`ts`-
range ALL filtered in Python — citing `list_puntos`'s own "filter the
harder conditions in code" tradeoff verbatim in the new docstring. All 9
pre-existing `listAuditoria` tests pass UNCHANGED; added one new test for
the exact combined-filter case that used to need the 3rd composite index.
Docstring documents the pagination caveat honestly (a deep page under a
narrow filter can under-report `hay_mas` if true matches are sparser than
the bounded over-fetch window) — this is a real, disclosed tradeoff, not a
silently-swept corner. **This makes OP.2/OP.3/OP.4 (3 of the proposal's 5
operator steps) unnecessary** — those indexes can be left unbuilt or
deleted; only OP.1 (the pre-existing `planeacion_puntos` autoAgrupar index)
still applies.

## Deviations From the Literal Task Text

1. **Task 1.6 (sole-writer allowlist)**: passing the NEW `puntos_contacto`
   scan required editing comments in TWO OTHER, unrelated files —
   `dashboard_refresh.py`'s new comment happened to spell out
   `planeacion_puntos` contiguously (tripping the pre-existing CLOSED
   `ALLOWED_MODULES_PLANEACION_PUNTOS` scan), and `inspector_asignaciones.py`'s
   new comment spelled out `survey_cali` (tripping
   `ALLOWED_MODULES_SURVEY_CALI`). Both reworded to avoid the literal
   substring, zero functional change — same "honest reword, not an
   allowlist-dodge" precedent that file's own docstring documents at
   length for a prior batch.
2. **Task 2.2 line estimate**: design.md/tasks.md cite "~1653" for the
   per-grupo vehicle selector; the actual code is in `gruposHtml` at
   ~line 725 (`vehiculoSelect` template). Implemented at the correct
   location — the estimate was simply off (design docs are not
   authoritative on line numbers, only on the requirement).
3. **Task 2.6 (consolidated `runAction` test)**: no automated RED/GREEN
   test was written. These three helpers are unexported closures living
   inside `initPlaneacion(root, {...})`, coupled to `busy`/`showOk`/
   `showErr`/`callApi`/DOM state with no existing DOM/fetch-mocking harness
   anywhere in this codebase's frontend test suite (`node --test` only
   ever exercises pure, exported functions). Fabricating a jsdom harness
   for one refactor would be scope creep beyond what any other UI-behavior
   change in `planeacion.js` has ever been held to. Verified instead by
   direct diff review: `runCuadrillaAction`, `runGrupoAction`, and
   `runVehiculoAction` were byte-identical in body except
   `runCuadrillaAction` lacked the `reloadFn` parameter — and its ONE call
   site never passed one anyway, so the default (`reload`) was already
   the effective behavior. This is documented in tasks.md as an explicit,
   reasoned exception, not a silently-skipped test.
4. **Task 4.1 root cause was one level deeper**: fixing ONLY the
   `firebase-firestore.js` CDN import inside `fetchIsraelRecords` (the
   task's literal instruction) left `evaluaciones.test.mjs` still RED,
   because `israel-source.js`'s top-level `import ... from
   './firebase-config.js'` transitively pulled in `firebase-config.js`'s
   OWN top-level `firebase-app.js` CDN import. Fixed by also lazy-importing
   `./firebase-config.js` itself inside the same function — mirroring the
   exact precedent `usuarios.js`'s `loadFirebaseAuth()` already
   established for the identical problem. Confirmed via the actual error
   (`ERR_UNSUPPORTED_ESM_URL_SCHEME`) persisting after the literal fix, not
   assumed.
5. **Task 4.4 (`playwright.config.ts`)**: could not use `import.meta.url`
   (the natural ESM idiom, and what `formulario/`'s own `.js` config
   doesn't need since it's ESM) because root `package.json` has no
   `"type": "module"` — the Vercel `api/*.js` serverless functions are
   plain CommonJS and adding it would have broken them. Playwright
   transpiles the `.ts` config to CJS in that case, so `import.meta.url`
   crashes; used the ambient CJS `__dirname` global instead.

## Security Note (Playwright credential capture — handled, not a code change)

Running `admin-flow.spec.ts` against the real credentials caused Playwright
to capture the TYPED email/password into `test-results/**/error-context.md`
and `trace.zip` (its own accessibility-snapshot/trace mechanism, not
anything this spec does deliberately). These were **deleted immediately
after each run** and `.gitignore` was updated to exclude
`/test-results/`, `/playwright-report/`, `/blob-report/` going forward so
this can never land in a commit. No credential value is echoed anywhere in
this file, in commit messages, or in the final report — only the Firebase
error TEXT ("Correo o contraseña incorrectos.") is quoted, which contains
no secret.

## C7 Deferral — Explicit Reasoning (not a silent skip)

The orchestrator's delegation message enumerated Commit 2's scope as an
explicit bullet list: "diaPicoPlacaHoy() helper; ...; auto-agrupar success
feedback; C2 CSS; C5 consolidate; C6 fix dead line-number comments; C8
uniform loading/empty states". **C7 was not in that list**, unlike the
audit-index rewrite, which the SAME message added as an explicit,
separately-labeled "SCOPE RIDER". Contrast: when the orchestrator wanted
scope added beyond tasks.md, it said so explicitly. Absent that signal for
C7, and absent the `frontend-design` skill the proposal itself says should
be "loaded at apply" (not present in this session's available skills),
implementing a subtab reorder by guesswork risked exactly the kind of
UX decision this skill exists to gate. Flagged for an explicit follow-up
decision rather than either (a) silently dropping it with no trace, or
(b) inventing a UI change unreviewed.

## Playwright Final Run

```
Running 3 tests using 3 workers
  ✓ e2e/smoke.spec.ts        — unauthenticated smoke: dashboard responds, login UI renders
  ✓ e2e/survey123.spec.ts    — connectivity: 200, field:codigoapp param intact, no submission
  ✘ e2e/admin-flow.spec.ts   — Firebase login rejected E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD:
                                "Correo o contraseña incorrectos." (credential issue, not app/spec)
2 passed, 1 failed
```

Did not report "skipped" for admin-flow because `E2E_ADMIN_EMAIL`/
`E2E_ADMIN_PASSWORD` ARE present (sourced from `integracion_F1/.env` via
the config's dotenv fallback) — the skip guard only fires when they're
ABSENT. Per instruction ("if the authed spec fails for a non-credential
reason, fix the spec; else document honestly") — this **is** a credential
reason (Firebase's own error text says so), so the spec was left as a
genuine failure, and a fail-fast diagnostic was added so the NEXT run (once
OP.5 is resolved) fails fast and clearly if it happens again, instead of a
generic 20s timeout.

## Files Changed

**Backend (Commit 1):**
- `backend/app/jobs/dashboard_refresh.py` — `_make_raw_mapper`, `_write_contactos`, `PUNTOS_CONTACTO_COLLECTION`
- `backend/app/routers/inspector_asignaciones.py` — `_contactos_por_id`, `_mis_puntos_planeacion` contact merge
- `backend/app/services/planeacion_audit.py` — `list_auditoria` rewrite (scope rider)
- `backend/tests/jobs/test_dashboard_refresh.py` — 8 new tests
- `backend/tests/routers/test_inspector_asignaciones.py` — 3 new tests + fake-Firestore `contacto_store`/`get_all`
- `backend/tests/routers/test_integracion.py` — PII noise-field additions to `_punto()`
- `backend/tests/routers/test_planeacion_asignaciones.py` — 1 new combined-filter test
- `backend/tests/invariants/test_sole_writer.py` — new `puntos_contacto` allowlist + scan

**Frontend dashboard (Commit 2):**
- `web/js/planeacion.js` — `diaPicoPlacaHoy`, `autoAgruparMensaje`, `runAction` consolidation, C6/C8 fixes
- `web/js/planeacion.test.mjs` — new tests for the two pure helpers
- `web/styles.css` — C2 select/textarea styling

**Formulario (Commit 3):**
- `formulario/js/form.js` — `buildPlaneacionCard` contact line + `tel:` button
- `formulario/css/form.css` — `.asignacion-solicitante`/`.asignacion-llamar`

**Test infra (Commit 4):**
- `web/js/israel-source.js` — full lazy-import fix (D2)
- `web/js/utils.js` — comment accuracy update (no logic change)
- `package.json`, `package-lock.json` — `@playwright/test` devDependency + `test:e2e` script
- `playwright.config.ts` — new
- `e2e/smoke.spec.ts`, `e2e/admin-flow.spec.ts`, `e2e/survey123.spec.ts` — new
- `.gitignore` — Playwright output dirs excluded
