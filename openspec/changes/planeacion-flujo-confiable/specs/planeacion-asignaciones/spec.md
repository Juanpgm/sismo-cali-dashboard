# Delta for planeacion-asignaciones

## ADDED Requirements

### Requirement: Reporter contact captured to a restricted channel, fail-soft

`dashboard_refresh` MUST capture each raw atencionsismo record's `{nombre_solicitante,
telefono_solicitante}` (read before the `PII_FIELDS` strip) and write it to a restricted,
non-public Firestore channel keyed by `registro_id`. A write failure on this channel MUST NOT
raise, retry-block, or fail the refresh job (fail-soft, mirroring `registrar_best_effort`).

#### Scenario: Contact is captured alongside a normal refresh
- GIVEN a raw record with a non-empty reporter name and phone
- WHEN `dashboard_refresh` runs
- THEN the restricted channel document for that `registro_id` holds both fields

#### Scenario: A contact-write failure never breaks the refresh
- GIVEN the restricted-channel write raises an exception
- WHEN `dashboard_refresh` runs
- THEN the refresh job still completes successfully and its other outputs are unaffected

### Requirement: Reporter contact reaches only the assigned group, never a public surface

`misPuntosPlaneacion` MUST return `nombre_solicitante`/`telefono_solicitante` for a point when the
caller is its `inspector_uid` OR a member of the group (cuadrilla) the point belongs to, and MUST
omit both fields otherwise. These fields MUST NOT appear in `web/data/reportes.json`, in the
admin-facing `listPuntos` response, or in any `/integracion/*` response.

#### Scenario: The assigned inspector and groupmates see the contact
- GIVEN point P is assigned to inspector A inside cuadrilla C, and B is another member of C
- WHEN A or B calls `misPuntosPlaneacion`
- THEN P's entry includes `nombre_solicitante` and `telefono_solicitante` for both callers

#### Scenario: The contact never leaks to a public or admin-bulk surface
- GIVEN a refresh run that captured reporter contact for point P
- WHEN `web/data/reportes.json`, any `/integracion/*` response, or the admin `listPuntos` response
  is inspected
- THEN none of them contains `nombre_solicitante` or `telefono_solicitante`

### Requirement: Vehicle list and selector surface pico y placa restriction

The vehicle list MUST show `dia_pico_placa` as a chip; the per-grupo selector MUST mark/disable a
vehicle restricted TODAY. Backend stays the real barrier: `asignarVehiculoAGrupo` MUST still 400 a
restricted vehicle even if the UI is bypassed; `editarVehiculo` MUST gate on `conductor_id` change
only, never on an edit that leaves the driver unchanged.

#### Scenario: The selector reflects today's restriction
- GIVEN one vehicle restricted today and one that is not
- WHEN the per-grupo vehicle selector renders
- THEN the restricted vehicle is marked and disabled, and the other remains selectable

#### Scenario: Backend still rejects a restricted assignment
- GIVEN a restricted-today vehicle
- WHEN `asignarVehiculoAGrupo` is called for it anyway
- THEN the call fails with 400 and no assignment is made

#### Scenario: Editing a vehiculo without changing its driver is not gated
- GIVEN a restricted-today vehicle
- WHEN `editarVehiculo` updates its `empresa` without changing `conductor_id`
- THEN the call succeeds

### Requirement: Auto-agrupar returns actionable created-count feedback

`autoAgrupar`'s response MUST report the count of cuadrillas created, and the UI MUST show a
success message with that count plus a note that a re-run takes the next batch. Existing
top-N-by-`prioridad_score`, densest-first ordering MUST be unchanged.

#### Scenario: Success feedback states the created count
- GIVEN `autoAgrupar` creates 4 cuadrillas
- WHEN the call returns
- THEN the UI shows a message naming 4 cuadrillas created and that re-running takes the next batch

#### Scenario: A re-run takes the next batch
- GIVEN a first run already grouped the top-N pending points, densest-first
- WHEN `autoAgrupar` is called again with ungrouped points remaining
- THEN the next call groups the next top-N batch, not points already grouped

### Requirement: Node test suite passes completely

`node --test "js/**/*.test.mjs"` MUST complete with zero failures, including
`evaluaciones.test.mjs`, by lazy-loading its CDN-dependent import so module load does not fail
under the Node test runner.

#### Scenario: Full suite is green
- GIVEN the current `web/js/**/*.test.mjs` files
- WHEN `node --test "js/**/*.test.mjs"` runs
- THEN every test passes and the process exits 0

### Requirement: Playwright E2E scaffold covers the assignment flow

An unauthenticated smoke test MUST always run headless. An authenticated admin-flow test (crear
grupo → refresh sin F5 → vehiculo+conductor → auto-agrupar feedback) MUST run when
`E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD` are present and MUST skip with a clear message when absent. A
Survey123 connectivity test MUST assert HTTP 200 with `field:codigoapp` intact, with NO submission.

#### Scenario: Unauthenticated smoke always runs
- GIVEN no credentials are configured
- WHEN `npx playwright test` runs
- THEN the smoke test (dashboard loads, login renders) executes and reports a result

#### Scenario: Authenticated flow runs with credentials, skips without them
- GIVEN `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD`
- WHEN `npx playwright test` runs with them set, then again with them unset
- THEN the first run exercises crear grupo, refresh sin F5, vehiculo+conductor, and auto-agrupar
  feedback and passes; the second run reports the test skipped, naming the missing env vars

#### Scenario: Survey123 connectivity check never submits
- GIVEN the configured prefilled Survey123 URL
- WHEN the connectivity test runs
- THEN it asserts HTTP 200 and an intact `field:codigoapp` parameter, and issues no POST/submission

### Requirement: Consistent Planeación UI states and controls

Every `.sticker-field` `select`/`textarea` MUST be styled like its sibling inputs. Every Planeación
subtab MUST share the same loading/empty/success visual pattern. The three `run*Action` helpers
(`runCuadrillaAction`, `runGrupoAction`, `runVehiculoAction`) MUST become one consolidated helper
with unchanged externally observable behavior.

#### Scenario: Selects and empty states match their siblings
- GIVEN a modal `.sticker-field select` and a Planeación subtab with zero results
- WHEN each renders
- THEN the select matches sibling `.sticker-field` styling and the subtab shows the same
  empty-state pattern used elsewhere in Planeación

#### Scenario: The consolidated action helper preserves prior behavior
- GIVEN any action previously routed through `runCuadrillaAction`, `runGrupoAction`, or
  `runVehiculoAction`
- WHEN it is triggered through the consolidated helper
- THEN loading, error, and success handling behave exactly as before
