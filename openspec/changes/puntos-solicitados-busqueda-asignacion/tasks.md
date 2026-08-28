# Tasks: Puntos Solicitados — buscar existing reports + assign from the card

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~800-950 (backend `buscar`+cache+tests ~250-290; frontend search/prefill+card-assign+polish+css+node tests ~550-620) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 backend (`GET /buscar`+`_build_rows`+TTL cache+pytest+sole-writer conditional) → PR2 frontend F1 (search modal+prefill+css+node tests) → PR3 frontend F2/F3 (card-level assign+xlsx+badges+spinners+css+node tests) |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main (user-confirmed) |

Decision needed before apply: No — resolved, stacked-to-main
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | `GET /puntos-solicitados/buscar` + `_build_rows` + TTL cache (ADR-1/2), admin gate, sole-writer conditional | PR1 | `python -m pytest backend/tests/routers/test_puntos_solicitados.py backend/tests/invariants/test_sole_writer.py` | N/A — fake Firestore double + `TestClient`, same convention as base feature | revert the route/helper/cache block + test additions from `puntos_solicitados.py`/`test_puntos_solicitados.py`; no write path, no data touched |
| 2 | "Buscar punto" modal + debounced search + prefill into `#ps-crear-modal` (F1) | PR2 | `node --test "web/js/puntos_solicitados.test.mjs"` | Manual: admin session, search by address/name, select result, confirm prefilled create modal | revert the search-modal markup/wiring/prefill block + its css + its node tests from `puntos_solicitados.js`/`styles.css`/`*.test.mjs`; card-assign (unit 3) is a separate code region |
| 3 | Card-level "Asignar" panel (F2) + xlsx export + count badges + spinners (F3) | PR3 | `node --test "web/js/puntos_solicitados.test.mjs"` | Manual: admin session, assign from a card without opening the modal, download xlsx, confirm badge counts + spinner busy states | revert the row-restructure/assign-panel/xlsx/badge/spinner block + its css + its node tests; search feature (unit 2) unaffected |

## Phase 1: Backend — `GET /buscar` (ADR-1, ADR-2)

- [x] 1.1 `backend/app/routers/puntos_solicitados.py`: add pure `_build_rows(reportes, contacto_by_id)` — join on `id == registro_id`, attach `nombre_solicitante`/`telefono_solicitante` or `None`, case-insensitive substring filter over `direccion|barrio|comuna|nombre_solicitante`, top-20 cap. *(Implemented as two pure functions per design.md ADR-2's own pseudocode/rationale: `_build_rows` does the join only — filter is NOT part of the cached build, since `q` is applied after the cache, never part of the cache key; `_filter_rows` does the case-insensitive substring filter + top-20 cap. Both are unit-tested directly.)*
- [x] 1.2 Same file: import `_load_reportes` from `app.jobs.planeacion_cruce`; add module-level `_BUSCAR_CACHE`/`_BUSCAR_TTL_S=300`/`_joined_rows()` TTL wrapper per ADR-2 (build reportes + `puntos_contacto` collection read once per 5 min).
- [x] 1.3 Same file: add `GET /puntos-solicitados/buscar?q=`, `Depends(require_role("admin"))`; empty/whitespace `q` → `{ok:true, resultados:[]}`; wrap source fetches in the same clean-502 `try/except` as sibling routes.

## Phase 2: Backend tests

- [x] 2.1 RED `backend/tests/routers/test_puntos_solicitados.py`: `_build_rows` join attaches name when present / `None` when missing — Satisfies: "Search by address, barrio, or comuna" / "Search by solicitante name".
- [x] 2.2 RED same file: case-insensitive substring filter over all 4 fields, top-20 cap.
- [x] 2.3 RED same file: TTL cache builds once, serves cached within TTL, rebuilds after TTL (monkeypatch `time.monotonic` + counting fake `_load_reportes`).
- [x] 2.4 RED same file: non-admin `GET /buscar` → 403, zero source reads — Satisfies: "Non-admin search is rejected" / "A non-admin session cannot retrieve contact data via search".
- [x] 2.5 RED same file: admin response never leaks `puntos_contacto` fields when unmatched (`null`), and asserts `reportes.json` itself stays PII-free — Satisfies: "Public artifacts remain PII-free". *(Plus two extra guard tests added: empty/whitespace `q` zero-source-read fast-path, and a clean-502 source-failure test mirroring sibling routes.)*
- [x] 2.6 GREEN: implement until 2.1-2.5 pass.
- [x] 2.7 `backend/tests/invariants/test_sole_writer.py` (ADR-4): confirmed — the invariant scans for `puntos_contacto` *references*, not just writers, so `routers/puntos_solicitados.py` was added to `ALLOWED_MODULES_PUNTOS_CONTACTO` as a flagged READ-ONLY entry. No writer-allowlist change.
- [x] 2.8 GREEN: full `python -m pytest backend/tests/`, zero failures (798 passed).

## Phase 3: Frontend — search modal + prefill (F1, ADR-1 consumer)

- [ ] 3.1 `web/js/puntos_solicitados.js` `sectionHtml()`: add "Buscar punto" button + `#ps-buscar-modal` (debounced input + results list container).
- [ ] 3.2 Wire debounced fetch to `GET /puntos-solicitados/buscar?q=`; render results (`direccion`/`barrio`/`comuna`/`nombre_solicitante`) each with "Usar este punto".
- [ ] 3.3 Prefill wiring into existing `#ps-crear-modal` per design's field mapping: `direccion`→`#ps-direccion`+`name="nombre"`, `comuna`→`#ps-comuna-input` (fire select to load barrios) **before** `barrio`→`#ps-barrio-input`, `lat`/`lng`→`#ps-lat`/`#ps-lng`+marker, `nombre_solicitante`/`telefono_solicitante` — Satisfies: "Search result selection prefills the create form" (Scenario: Selecting a result prefills the create form).
- [ ] 3.4 "Crear punto nuevo" fallback: prefill only `direccion`+`nombre` from typed `q`, everything else blank — Satisfies: same requirement (Scenario: No match still allows manual creation).
- [ ] 3.5 `web/styles.css`: `.ps-buscar-modal`/result-list styles.

## Phase 4: Frontend tests — search

- [ ] 4.1 RED `web/js/puntos_solicitados.test.mjs`: exported prefill-mapping helper (result → field values).
- [ ] 4.2 RED same file: comuna-before-barrio sequencing helper (barrio value applied only after comuna is set).
- [ ] 4.3 GREEN: `node --test "web/js/puntos_solicitados.test.mjs"`.

## Phase 5: Frontend — card-level assign (F2, ADR-3)

- [ ] 5.1 `web/js/puntos_solicitados.js` `listItemHtml()`: split the single `eval-row` button into sibling detail button + `.ps-asignar-btn`, plus sibling `.ps-asignar-panel[hidden]`.
- [ ] 5.2 `init()`: delegate click on `.ps-asignar-btn` to toggle its panel (only one open at a time), mount `mountCombobox` over `inspectoresCache`, call existing `asignarInspector(id, uid)` on selection — Satisfies: "Card-level assignment action" (Scenario: Assigning from the list view).
- [ ] 5.3 `web/styles.css`: `.ps-asignar-btn`/`.ps-asignar-panel` inline-panel styles, reusing `.asignacion-combo`.

## Phase 6: Frontend — polish (F3: xlsx, badges, spinners)

- [ ] 6.1 `web/js/puntos_solicitados.js`: xlsx export button mirroring `evaluaciones.js` `downloadStamp`/`loadXlsx` (`710-756`) — Satisfies: "xlsx export".
- [ ] 6.2 Same file: client-side `count[uid]` one-pass tally over the loaded list, passed to `inspectorOptionLabel` (adapted from `stickers-asignacion.js:351`) for both the detail-modal and card-level comboboxes — Satisfies: "Inspector selection shows active-assignment load".
- [ ] 6.3 Same file: `.asignacion-spinner` + disabled state on `#ps-crear-submit`/`#ps-geocode-btn` while in flight, in addition to existing text change — Satisfies: "Busy state feedback on create/geocode actions" (both scenarios).
- [ ] 6.4 `web/styles.css`: confirm `.asignacion-combo-count`/`.asignacion-spinner` reused unmodified; add only if layout breaks in this context.

## Phase 7: Frontend tests — polish

- [ ] 7.1 RED `web/js/puntos_solicitados.test.mjs`: client-side count-tally helper (pure function, list → `{uid: count}`).
- [ ] 7.2 GREEN: `node --test "web/js/puntos_solicitados.test.mjs"`.

## Phase 1b: Backend — 4R polish pass (post-review, non-blocking WARNINGs)

- [x] 1b.1 `_BUSCAR_CACHE` module dict → `BuscarCache` class on `app.state.puntos_solicitados_buscar_cache` (same pattern as `sticker_status.StickerStatusCache`); `_reset_buscar_cache` test fixture removed (natural per-test isolation via fresh `create_app()`).
- [x] 1b.2 `_load_reportes` renamed to public `load_reportes` in `app/jobs/planeacion_cruce.py`; import updated.
- [x] 1b.3 `_joined_rows` degrades to address-only rows (logged) on a `puntos_contacto`-only read failure; only `load_reportes()` failure still 502s. New test added alongside `test_buscar_source_failure_is_a_clean_502`.
- [x] 1b.4 `_build_rows` dedupes duplicate `id`s (first occurrence wins), logged once in `_joined_rows`, mirroring `planeacion_cruce.load_puntos`'s own convention. New test added.

## Phase 8: Verification

- [ ] 8.1 Full `python -m pytest backend/tests/`, zero failures.
- [ ] 8.2 Full `node --test "web/js/**/*.test.mjs"` (per `openspec/config.yaml` `verify.test_command`), zero failures.
- [ ] 8.3 Manual (flag for operator, needs live admin session): search selects+prefills correctly; card "Asignar" assigns without opening the modal; xlsx downloads; inspector options show count badges; create/geocode buttons show spinner.
