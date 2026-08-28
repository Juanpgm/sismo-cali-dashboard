# Tasks: Puntos Solicitados

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~950-1150 across backend (router+service+tests), frontend (new tab+wiring+tests), formulario, planeacion.js rename |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 backend (geocode.py + router + main.py + sole-writer + pytest) → PR2 frontend tab (puntos_solicitados.js + index.html/main.js/styles.css + node tests) → PR3 formulario badge/sort + planeacion.js rename |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | `geocode.py` + `puntos_solicitados.py` router (CRUD+`/geocode`) + `main.py` registration + sole-writer allowlist | PR1 | `python -m pytest backend/tests/services/test_geocode.py backend/tests/routers/test_puntos_solicitados.py backend/tests/invariants/test_sole_writer.py` | N/A — fake Firestore double + `TestClient` are the proof (no live Firestore in this repo's test suite) | unregister router from `_ROUTERS`, revert 4 backend files; `puntos_solicitados`/`solicitado_*` docs stay additive, read by nothing else |
| 2 | New tab `web/js/puntos_solicitados.js` + nav/routing/admin-gate wiring | PR2 | `node --test "web/js/puntos_solicitados.test.mjs"` | Manual: admin session, "Puntos Solicitados" tab create+list+edit | revert `web/js/puntos_solicitados.js` + the 3 wiring diffs; assignment endpoints unaffected |
| 3 | Formulario PRIORIDAD badge/sort + Planeación button rename | PR3 | `node --check formulario/js/form.js` / manual | Manual: mixed assignment list in formulario; Planeación tab button label | revert `formulario/js/form.js` block and the one-line `planeacion.js` rename independently |

## Phase 1: Backend foundation — geocode service

- [x] 1.1 Create `backend/app/services/geocode.py`: port `ACCEPTED` thresholds (`ROOFTOP`, `RANGE_INTERPOLATED`), `CALI_BBOX`, `to_google_address` from `scripts/geocode_validate.py` as a pure function `geocode(direccion, http_get=...)` returning `{ok, accepted, lat, lng, formatted, location_type}` or `{ok, accepted:false, reason}`; add `__main__` self-check against fixture responses.
- [x] 1.2 RED `backend/tests/services/test_geocode.py`: `ROOFTOP` inside bbox → accepted; `APPROXIMATE`/outside-bbox/no-result → `accepted:false` with correct `reason` — Satisfies: puntos-solicitados/"Live geocoding with manual fallback" (both scenarios).
- [x] 1.3 GREEN: confirm `test_geocode.py` passes against 1.1.

## Phase 2: Backend router — CRUD + `/geocode` + wiring

- [x] 2.1 Create `backend/app/routers/puntos_solicitados.py`: `REQUIRED_CLIENTS=("sismo",)`; import `clave_integracion`, `doc_id` from `app.jobs.planeacion_cruce`; `POST` create (admin-only via existing claims dep, ADR-1 pre-generate id + single `db.batch()` writing both `puntos_solicitados/{id}` and `planeacion_puntos/solicitado_{id}` per ADR-2/ADR-3 field shape).
- [x] 2.2 Add `GET` list to the same router: batched `get_all` over known `solicitado_{sid}` mirror ids, derive `estado_seguimiento` from `estado_asignacion` per ADR-4 map (`pendiente→pendiente`, `asignado→asignado`, `en_proceso→en_proceso`, `hecho→visitado`, `no_aplica→excluido`).
- [x] 2.3 Add `PATCH /{id}` (admin-only, edits `puntos_solicitados` request fields — never lifecycle) and `DELETE /{id}` (admin-only) to the router. Corrective fix round: `PATCH` now also re-syncs the ADR-2 mirrored subset (`nombre`/`direccion`/`barrio`/`comuna`/`coords`) onto the mirror in the same atomic batch (was previously stale after edit), enforces `MAX_FOTOS` same as create, and all four CRUD routes now catch Firestore failures → clean 502 with `logging.exception`.
- [x] 2.4 Add `POST /geocode` to the router: `Depends(require_auth)`, calls `app.services.geocode.geocode()`, maps Google `REQUEST_DENIED`/`OVER_QUERY_LIMIT`/`INVALID_REQUEST` → 502; key never in response (ADR-5). Corrective fix round: `_default_http_get`/`geocode()` now also catch transport failures (timeout/connection error) and malformed/non-JSON responses, mapped to the same 502 via a new `GeocodeTransportError`.
- [x] 2.5 Modify `backend/app/main.py`: import `puntos_solicitados` router, add to `_ROUTERS` tuple.

## Phase 3: Backend tests

- [x] 3.1 RED `backend/tests/routers/test_puntos_solicitados.py` (fake Firestore double + `TestClient`, mirror `test_planeacion_asignaciones.py`): successful create writes both docs with minted `codigoapp` — Satisfies: puntos-solicitados/"Atomic dual-write..." (Scenario: successful create writes both documents).
- [x] 3.2 RED same file: simulated batch-commit failure leaves neither document — Satisfies: same requirement (Scenario: simulated write failure leaves no orphan).
- [x] 3.3 RED same file: missing `justificacion` (or any required field) rejected, zero writes — Satisfies: "Admin-only creation with required-field validation" (Scenario: missing required field).
- [x] 3.4 RED same file: all required fields + zero photos accepted — Satisfies: same requirement (Scenario: all required fields present, no photos).
- [x] 3.5 RED same file: non-admin `POST`/`PATCH`/`DELETE` → 403, zero writes/modifications/deletions — Satisfies: same requirement (Scenario: non-admin create/edit/delete rejected).
- [x] 3.6 RED same file: `GET` list maps mirror `estado_asignacion` transitions `pendiente→asignado→en_proceso→hecho` to `estado_seguimiento` `pendiente→asignado→en_proceso→visitado` with no direct write from assignment endpoints — Satisfies: "`estado_seguimiento` tracks the mirror's assignment lifecycle" (Scenario: status advances as the mirror advances).
- [x] 3.7 RED same file: manual lat/lng submit without calling `/geocode` creates the point with those coordinates — Satisfies: "Live geocoding with manual fallback" (Scenario: manual coordinate entry).
- [x] 3.8 GREEN: implement/adjust Phase 2 code until 3.1-3.7 pass.
- [x] 3.9 RED `backend/tests/invariants/test_sole_writer.py`: add `routers/puntos_solicitados.py` to `ALLOWED_MODULES_PLANEACION_PUNTOS`; add `ALLOWED_MODULES_PUNTOS_SOLICITADOS` (`puntos_solicitados.py` + `main.py`) and `test_puntos_solicitados_literal_is_used_by_an_allowlisted_module` per ADR-6.
- [x] 3.10 GREEN: run full `python -m pytest backend/tests/`, zero failures; reword any accidental literal collisions the same way prior sole-writer additions did (see `planeacion-flujo-confiable` precedent).

## Phase 4: Frontend — new tab

- [ ] 4.1 Create `web/js/puntos_solicitados.js` cloned from `web/js/evaluaciones.js`: KPIs + filters + Leaflet map colored by `estado_seguimiento` + card list + detail modal (photos/justificación/contacto); export pure helpers (color-by-estado, sort, filter) for testing.
- [ ] 4.2 In the same file, add "Crear punto solicitado" modal: required-field form, live `/geocode` call on typed address showing a draggable-marker Leaflet map, manual lat/lng fallback, up to 10 photos via existing presigned S3 flow (`solicitados/{id}/foto_{slot}.jpg`).
- [ ] 4.3 Modify `web/index.html`: add nav button + `data-view-panel` section for "Puntos Solicitados", admin-gated same pattern as Stickers/Usuarios.
- [ ] 4.4 Modify `web/js/main.js`: add `switchView()` branch wiring the new panel, gated to admin/superadmin custom claims.
- [ ] 4.5 Modify `web/styles.css`: admin-only visibility gate + PRIORIDAD/estado color rules for the new tab (distinct from existing pill scheme, consistent with formulario badge in Phase 6).

## Phase 5: Frontend tests

- [ ] 5.1 RED `web/js/puntos_solicitados.test.mjs` (mirror `evaluaciones.test.mjs`): color-by-`estado_seguimiento`, sort, and filter helpers — Satisfies: puntos-solicitados requirements (list/detail rendering, not spec-scenario-mapped, matches Testing Strategy table's "node pure" row).
- [ ] 5.2 GREEN: run `node --test "web/js/puntos_solicitados.test.mjs"`, zero failures.

## Phase 6: Formulario — PRIORIDAD badge + sort

- [ ] 6.1 Modify `formulario/js/form.js` `buildPlaneacionCard` (~379-421): read `es_solicitado` from the assigned-point record, sort solicited points first, render a distinct "PRIORIDAD" badge (own styling, not the alta/media/baja pill) — Satisfies: field-form-session/"Assigned-point card surfaces solicited points as PRIORIDAD, sorted first" (both scenarios).
- [ ] 6.2 Manual/`node --check formulario/js/form.js`: mixed list sorts solicited-first with badge; solicited-only list unaffected; pipeline-only list keeps unchanged ordering/pill.

## Phase 7: Planeación rename (copy-only)

- [ ] 7.1 Modify `web/js/planeacion.js:497`: button label "Auto-agrupar" → "Crear Cluster".
- [ ] 7.2 Confirm handler at `web/js/planeacion.js:2569` still dispatches unchanged `action:'autoAgrupar'` — Satisfies: puntos-solicitados/"Planeación cluster-creation rename is copy-only" (Scenario: renamed button still dispatches the unchanged action).

## Phase 8: Verification

- [ ] 8.1 Full `python -m pytest backend/tests/`, zero failures.
- [ ] 8.2 Full `node --test "web/js/**/*.test.mjs"`, zero failures.
- [ ] 8.3 Manual: admin sees "Puntos Solicitados" tab, non-admin does not; create→assign via existing grupo/cuadrilla/inspector flow→formulario shows PRIORIDAD badge sorted first.

## Operator Tasks (no repo diff)

- [ ] OP.1 Confirm `GOOGLE_MAPS_API_KEY` is set live on the Railway FastAPI service (currently consumed only by the offline `scripts/` container) — required for `/geocode` to function in production.
