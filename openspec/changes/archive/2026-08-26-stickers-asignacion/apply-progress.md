# Apply Progress: Stickers — cruce y asignación

Change: `stickers-asignacion` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-apply
(batch 1 of N — chained PRs, `stacked-to-main`)

Branch: `feat/stickers-asignacion-1-pipeline` (branched from `main`)

## ⚠️ CRITICAL DISCOVERY — `integracion_F1/` is a separate git repository

**Not anticipated by `proposal.md`/`design.md`/`tasks.md`.** `integracion_F1/` is excluded from
this repo's git tracking entirely (`.gitignore:38`, with its own comment: `# subproyecto con repo
git propio (normalizador_data_sismo_cali)`). Verified at apply time:

- `git log --oneline --all -- integracion_F1/` → **zero commits, ever**, on any branch of THIS
  repo (`sismo-cali-dashboard`). Every reference file this task read as precedent
  (`asignar_f3.py`, `cruce_gestor.py`, `subir_cruce_firebase.py`, `job_asignaciones.py`) — none of
  them are tracked here either. They only exist on disk because `integracion_F1/` is itself a
  **separate, fully independent git repository** checked out at that path, remote
  `https://github.com/Juanpgm/normalizador_data_sismo_cali.git`, currently on its own `main`
  branch (not related to `feat/stickers-asignacion-1-pipeline` in any way — different repo, no
  submodule/subtree relationship, no `.gitmodules`).
- That subrepo's working tree already has pre-existing uncommitted state unrelated to this change
  (`README_integracion.md` modified +58 lines, plus several untracked stray files —
  `experimento_catastro.py`, `stickers_analysis.ipynb`, `catastro_asignaciones.json`, etc.) — left
  untouched, not mine.

**Consequence for this PR chain.** `integracion_F1/cruce_sticker.py` and
`integracion_F1/job_sticker.py` were written correctly on disk (verified working, RED→GREEN,
runtime harness passed — see below), but **cannot be committed to
`feat/stickers-asignacion-1-pipeline`** — git refuses (`git add` errors: "The following paths are
ignored by one of your .gitignore files"), and forcing it (`git add -f`) would be semantically
wrong: these files do not belong to this repository's deploy surface at all. The "PR #1 of 3"
framing in the orchestrator's brief (pipeline → API → frontend, all as sequential PRs against
*this* repo's `main`) does not hold for Phase 1 specifically — **there is no diff for this repo to
review for Phase 1.** The actual deliverable for Phase 1 is a commit to the *separate*
`normalizador_data_sismo_cali` repository, which is outside this SDD change's tracked artifact
store and outside the scope this apply agent was authorized to push to (that repo's `main` is
live-deployed to a Railway cron service reading/writing production Firestore — pushing there is a
real deploy action, not a reviewable PR, and was correctly NOT done autonomously here).

**What I did instead**: left both files in place, verified and tested, in `integracion_F1/`'s
working tree (uncommitted in that subrepo, same status as the other pre-existing untracked files
already sitting there). Committed only the OpenSpec bookkeeping (`tasks.md`, this file) to
`feat/stickers-asignacion-1-pipeline`, since `openspec/changes/` IS tracked by this repo.

**Recommended next step (needs a human/orchestrator decision, not a silent agent choice)**: either
(a) the operator reviews and commits `cruce_sticker.py`/`job_sticker.py` directly to
`normalizador_data_sismo_cali`'s `main` (matching that subrepo's own established workflow — its
`git log` shows commits go straight to `main`, no feature branches), independently of this SDD
change's PR chain, or (b) the SDD chain strategy is revised so Phase 1 is understood as "code
delivered, no PR" and the outer-repo PR chain starts at Phase 2 (API) instead.

## Scope of this batch

Phase 1 — Pipeline (`integracion_F1/cruce_sticker.py` + `integracion_F1/job_sticker.py`) only.
Tasks 0.1, 1.1–1.6. Task 1.7 is a manual operator step, out of scope for any apply agent (see
below). Phases 2–4 are separate PRs/batches, not started.

## Completed tasks

- [x] **0.1** — Finding already recorded in `tasks.md` itself at planning time; no additional
  action needed. Confirmed at apply time by re-reading `web/js/stickers.js`.
- [x] **1.1** (RED) — Wrote `_selfcheck_cruce_sticker` FIRST as a placeholder module (only the
  test function, referencing not-yet-defined `doc_id`/`build_write_ops`); ran `python
  cruce_sticker.py --check`, confirmed it fails with `NameError: name 'doc_id' is not defined`
  (exit code 1) before any production code existed.
- [x] **1.2** (GREEN) — Scaffolded `cruce_sticker.py`: `main()`, `--check`/`--dry`/`--top N`
  flags, module docstring, `doc_id(fuente, registro_id) -> f"{fuente}_{registro_id}"`.
- [x] **1.3** (GREEN) — `load_panel()` reads `web/data/inspections.json` +
  `puntos_israel_cali.json` (repo root), same EXIF-corrected `x`/`y` coords as the notebook;
  `_firestore_client()` uses the same 3-tier resolution as `subir_cruce_firebase.py`
  (`STICKERS_FIREBASE_SA` path → `FIREBASE_SERVICE_ACCOUNT_JSON` env → ADC), explicitly targeting
  `sismo-agosto-sgred` (not `integracion_F1`'s `dagma-85aad` default).
- [x] **1.4** (GREEN) — `cruce_sticker_punto()` imports and calls `nearest`, `match_by_direccion`,
  `build_addr_index`, `addr_key`, `_eval_latlon` directly from `cruce_gestor.py` — no fork/copy of
  the matching logic.
- [x] **1.5** (GREEN) — `build_write_ops()` (pure) splits fields per ADR-1: only ever emits
  `PIPELINE_FIELDS`, plus `ADMIN_DEFAULT_FIELDS` (`estado_asignacion:'pendiente'`,
  `cuadrilla_id:null`, `inspector_uid:null`) when the doc id is not in the pre-read `existing_ids`
  set. `write_sticker_matches()` pre-reads existence via `db.get_all()` (Python Firestore client's
  equivalent of the JS `getAll()` join pattern in `api/stickers.js:70-73`), then batches
  `db.batch().set(doc_ref, fields, merge=True)` in groups of ≤500 (`BATCH_SIZE`). Ran `--check`
  again after implementing — passes (see Work Unit Evidence below).
- [x] **1.6** — `integracion_F1/job_sticker.py`, `job_asignaciones.py`'s structure copied verbatim
  (durable-logging harness: `runlog.resolve_log_dir()` / `start_tee()` / `append_run()`, tees to
  `runs_sticker.jsonl`, non-zero exit on failure), wrapping `cruce_sticker.main()` instead of
  `asignar_f3.main()`.

## Not completed (manual step, correctly out of apply scope)

- [ ] **1.7** — Cron wiring is a **manual Railway action**, not a repo diff. `tasks.md` already
  self-corrects `design.md` ADR-2's claim that `integracion_F1/railway.json` gets a new
  `startCommand`/`cronSchedule` pair — the real file is shared Dockerfile-build config only, with
  no per-service fields; per-service settings live on each Railway service instance
  (CLI/dashboard), not in this repo. **Operator action required, next session or manual step
  outside this PR**:
  1. `railway up --service sticker-cruce` (name TBD) from `integracion_F1/`, same Docker image as
     `job.py`/`job_asignaciones.py`.
  2. Set `startCommand: python job_sticker.py`.
  3. Set a daily `cronSchedule` matching `job_asignaciones.py`'s slot family (currently 16:00
     Bogotá — confirm exact cron string against the live `job_asignaciones` service config).
  4. Confirm env vars are present on the new service: `STICKERS_FIREBASE_SA` (preferred, SA key
     file path) or `FIREBASE_SERVICE_ACCOUNT_JSON` (whole-JSON env, Railway/CI fallback).
  5. No PR captures this step — it is a console/CLI action against Railway's own project
     dashboard, tracked here so the next apply batch (or a human) knows it is still open.

## Deviations from design / risks discovered

- **Confirmed correction already flagged by tasks.md (task 1.7)**: `design.md` ADR-2 assumed
  `integracion_F1/railway.json` would get a `startCommand`/`cronSchedule` pair added by this PR.
  The actual file's contents rule that out — verified again at apply time, matches the
  orchestrator's brief. No new deviation here, just re-confirming the known one.
- **New minor deviation (not a correction, an explicit scope call)**: `zona_id` in the
  pipeline-owned field group is populated from the Panel's `comuna` field (e.g. `"COMUNA 19"`),
  NOT from a KML/polygon zone lookup. Neither `design.md` nor `tasks.md` 1.1–1.5 specify a zone
  source for this field, and no spec scenario asserts a specific `zona_id` value — `comuna` is the
  only zone-shaped field already present on every Panel record, so this is the lazy/correct choice
  for Phase 1. If a future phase needs KML-polygon zones (`asignar_f3.py`'s pattern) instead, that
  is a one-line swap inside `load_panel()`, not a schema change.
- **New minor deviation**: `tier` (`'alta'|'media'|'sospechoso'|null`) is computed with a
  simplified two-signal rule (geo distance ≤ 40 m AND/OR address-ratio ≥ 0.90) — the notebook's own
  `_tier()` additionally uses a calle/carrera transposition detector
  (`_es_transposicion_calle_carrera`) as a third corroborating signal. That detector is not
  referenced by any Phase 1 task (1.1–1.5) or spec scenario; including it would have pulled in
  `integracion.normalization.address_to_vector` for a field with no test coverage in the accepted
  scope. Documented here rather than silently matching the notebook 1:1. Trivial follow-up if a
  reviewer wants full parity.
- **No other deviations.** `PIPELINE_FIELDS`/`ADMIN_DEFAULT_FIELDS`, `doc_id`, the 3-tier
  credential resolution, and the ≤500-batch write path all match `design.md` ADR-1/ADR-2 and
  `specs/stickers-asignacion/spec.md` exactly.

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1–1.5 | `integracion_F1/cruce_sticker.py` (`_selfcheck_cruce_sticker`, run via `--check`) | Unit (pure fixture, offline) | N/A (new file) | ✅ Written first; ran against a placeholder module and confirmed `NameError: name 'doc_id' is not defined` (exit 1) | ✅ Full implementation written, `--check` passes | ✅ 5 cases: doc-id stability (2), matching cascade geo/address/miss (3), merge-safety on existing doc + first-write seeding (2) | ✅ `python -m py_compile` clean; re-ran `--check` after the compile pass, still green |
| 1.6 | N/A (thin wrapper, no independent test — mirrors `job_asignaciones.py`, which also has none) | N/A | N/A (new file) | N/A | ✅ Ran `python job_sticker.py --check`, confirmed it wraps and forwards to `cruce_sticker.main()`'s self-check, exits 0 | ➖ Single (harness has one code path) | ➖ None needed (verbatim copy of a proven pattern) |

### Test Summary
- **Total tests written**: 1 self-check function (`_selfcheck_cruce_sticker`), 10 assertions
  across doc-id stability, matching-cascade reuse, and merge-safety/first-write seeding.
- **Total tests passing**: 10/10 (see Work Unit Evidence below for exact command output).
- **Layers used**: Unit (1), Integration (0 — no live Firestore in this batch), E2E (0).
- **Approval tests**: None — no refactoring of existing files in this batch.
- **Pure functions created**: `doc_id`, `build_write_ops`, `_tier`, `cruce_sticker_punto` (Firestore
  I/O is isolated in `_firestore_client`, `fetch_evaluaciones`, `write_sticker_matches`).

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `python cruce_sticker.py --check` → RED: `NameError: name 'doc_id' is not defined` (exit 1, against a placeholder-only module). Restored full implementation → GREEN: `cruce_sticker self-check OK` (exit 0). Re-ran after `python -m py_compile cruce_sticker.py` → still `cruce_sticker self-check OK`. `python job_sticker.py --check` → `Corrida OK` (exit 0), delegates into the same self-check. |
| Runtime harness command/scenario and exact result | `python -c "import cruce_sticker as cs; panel = cs.load_panel(); print(len(panel))"` against the REAL repo data files (`web/data/inspections.json` + `puntos_israel_cali.json`, no network) → `1101` points (`{'ede': 1000, 'israel': 101}`), matching the expected panel size from `exploration.md`. Live-Firestore write path (`write_sticker_matches`) was NOT run against a real project in this batch — no credentials available in this environment; its pure counterpart `build_write_ops` is fully covered by the offline self-check instead, and the write/batch/get_all wiring was code-reviewed against `subir_cruce_firebase.py`'s proven `upload()` shape. |
| Rollback boundary | Two new, independent files: `integracion_F1/cruce_sticker.py`, `integracion_F1/job_sticker.py`. Neither is imported by any other module in the repo yet (Phase 2's `api/sticker-asignaciones.js` doesn't exist yet and doesn't call into this job at runtime — it reads Firestore directly). `git rm integracion_F1/cruce_sticker.py integracion_F1/job_sticker.py` (or `git revert` this commit) fully reverts this batch with zero impact on any other file. |

## Next batch (Phase 2 — separate PR/branch)

- New branch (per `chain_strategy: stacked-to-main`) targeting `main` after this PR merges,
  implementing `api/sticker-asignaciones.js` + `api/sticker-asignaciones.test.js` (tasks 2.1–2.11).
- Phase 2 does not depend on Phase 1's job having run at build time (it reads/writes Firestore
  directly), but exercising it end-to-end needs `cruce_sticker.py`/`job_sticker.py` to have
  populated `sticker_matches` at least once against a real/emulated Firestore — which in turn needs
  **1.7** (the Railway cron service) to exist, or a manual one-off `python cruce_sticker.py` run
  with real credentials.
- Carry task 1.7's open item forward: confirm with the operator whether the Railway cron service
  was created before or independently of Phase 2's apply session.

---

# Batch 2 — Phase 2: API (`feat/stickers-asignacion-2-api`)

Branch: `feat/stickers-asignacion-2-api` (branched from `main`, Phase 1's openspec bookkeeping
already merged). This PR lives entirely in `api/`, inside THIS repo (unlike Phase 1 — no subrepo
topology issue here, `api/` is directly tracked by `sismo-cali-dashboard`).

## Scope of this batch

Phase 2 — API (`api/sticker-asignaciones.js` + `api/sticker-asignaciones.test.js`). Tasks 2.1–2.11.
Task 0.2 (`maxRadiusM`/`maxSize` defaults) is still open — shipped as named placeholder constants
per its own instruction, not blocking.

## Completed tasks

- [x] **2.1** — `api/sticker-asignaciones.js` scaffolded: 405 guard, Bearer token extraction,
  `verifyFirebaseToken` + `roleFromClaims` from `./refresh.js`, fail-closed
  `roleFromClaims(claims) !== 'admin'` → 403, try/router on `body.action`, `err.status || 502`.
  `getAdmin()` singleton copied (not imported) from `api/stickers.js:50-61`, same self-contained
  convention `api/usuarios.js` already documents for itself.
- [x] **2.2** (RED) — `api/sticker-asignaciones.test.js` written first: determinism (same fixture
  twice → identical group membership), `maxSize` cap (10 dense points, cap 3 → no group >3, every
  point placed exactly once), `maxRadiusM` cap (far point never joins the near seed's group), empty
  input → `[]`. Ran before `sticker-asignaciones.js` existed — confirmed `Cannot find module
  './sticker-asignaciones.js'` (exit 1).
- [x] **2.3** — `listPuntos` (`{ok, puntos}`, full `sticker_matches` read) and `listCuadrillas`
  (`{ok, cuadrillas}`, full `cuadrillas` read) implemented; neither reads
  `inspections.json`/`puntos_israel_cali.json` anywhere in the file (grep-confirmed, see Work Unit
  Evidence).
- [x] **2.4** (GREEN) — Pure `autoAgrupar(puntos, {maxRadiusM, maxSize})` per ADR-3's locked
  pseudocode (stable `[lat, lon]` sort, no RNG, no k-means) + `haversineM` (no existing JS
  haversine found in `web/js/*.js` — checked `evaluaciones.js` and the rest, none exists; ported the
  same formula `scripts/refresh_data.py`'s `_haversine_m` and `scripts/geocode_validate.py`'s
  `haversine_m` already use). Exported for the test file. Ran 2.2 again → GREEN (see Work Unit
  Evidence). O(n²) scan marked with the exact `ponytail:` comment tasks.md specifies.
- [x] **2.5** — `autoAgrupar` action handler (`runAutoAgrupar`) reads `pendiente` points with
  `cuadrilla_id == null` via a Firestore compound query, calls the pure function with
  `maxRadiusM`/`maxSize` (request override or the task-0.2 placeholder constants), batch-creates
  `cuadrillas` docs with `origen:'auto'`, `inspector_uid:null`, sets `cuadrilla_id` on every member
  point in the same batch. Does not touch `estado_asignacion` anywhere in this function. Empty
  pending set short-circuits to `[]` before any Firestore write.
- [x] **2.6** — `crearCuadrilla({nombre, puntos})` → new `cuadrillas` doc, `origen:'manual'`, sets
  `cuadrilla_id` on every listed point in the same batch. Rejects an empty `puntos` array
  (`badRequest`).
- [x] **2.7** — `editarCuadrilla({cuadrilla_id, add, remove})` merges add/remove into the existing
  `puntos` set (dedup via `Set`), writes the new membership plus `cuadrilla_id`/`null` on affected
  points in one batch; throws (no writes) if the `cuadrillas` doc doesn't exist.
- [x] **2.8** — `asignarInspector({cuadrilla_id, inspector_uid})` reads the cuadrilla's current
  `puntos`, batch-sets `inspector_uid`, `asignado_en` (`admin.firestore.FieldValue.serverTimestamp()`),
  `estado_asignacion:'asignado'` on every member point plus `inspector_uid` on the cuadrilla doc
  itself.
- [x] **2.9** — `reasignarPunto({punto_id, nuevo_inspector_uid})` reads the point's current
  `inspector_uid` (defaults to `null` if unset), sets `reasignado_de` to that previous value and
  `inspector_uid` to the new one via `merge:true` — `cuadrilla_id` is never touched by this
  function.
- [x] **2.10** — `eliminarCuadrilla({cuadrilla_id})` clears `cuadrilla_id`/`inspector_uid` on every
  member point in one batch **committed before** the `cuadrillas` doc delete call (two separate
  Firestore operations, ordered — matches the spec's "no point left referencing a nonexistent
  cuadrilla even if the delete step fails partway").
- [x] **2.11** — `node api/sticker-asignaciones.test.js` → `sticker-asignaciones.test.js OK` (exit
  0). `node --check api/sticker-asignaciones.js` → syntax clean. Sibling self-checks re-run as a
  regression safety net: `node api/stickers.test.js` → OK, `node api/usuarios.test.js` → OK (neither
  touched by this batch). Grep for any `evaluaciones` write call
  (`\.collection\('evaluaciones'\)\.(set|update|delete|add)`) in `api/sticker-asignaciones.js` →
  zero matches (the only `evaluaciones` occurrence in the file is a code comment referencing
  `web/js/evaluaciones.js` by name, not a Firestore call).

## Deviations from design / risks discovered

- **None that require correction.** Implementation matches `design.md` ADR-1 (field-group
  ownership respected — this endpoint only ever writes admin-owned fields, never the pipeline-owned
  subset), ADR-3 (endpoint shape, action table, greedy-clustering algorithm verbatim from the
  pseudocode, including the exact `ponytail:` comment), and every Phase-2 spec requirement/scenario
  in `specs/stickers-asignacion/spec.md`.
- **Task 0.2 still open, as expected.** `maxRadiusM`/`maxSize` (800m/8 points) shipped as named
  constants (`DEFAULT_MAX_RADIUS_M`, `DEFAULT_MAX_SIZE`) at the top of `api/sticker-asignaciones.js`,
  not magic numbers — `runAutoAgrupar` also accepts a per-call override (`body.maxRadiusM`/
  `body.maxSize`) so Phase 3's frontend settings affordance (task 3.5) can override without another
  backend change. No operator confirmation obtained this batch; flagged again here per instruction.
- **`editarCuadrilla`'s `add`/`remove` field names are not spelled out verbatim in `design.md`**
  (design.md's ADR-3 table just says "add/remove points from an existing cuadrilla" without naming
  the request fields). Chose `{cuadrilla_id, add: [...], remove: [...]}` as the natural shape given
  spec.md's two scenarios (add and remove are independent operations) — no spec scenario asserts a
  specific field name, so this is a request-shape implementation detail, not a deviation from a
  locked contract. Documented here for Phase 3's frontend to consume as-is.
- **No use of a live/emulated Firestore in this batch** — same constraint as Phase 1 (no credentials
  in this environment). All 8 action handlers are code-reviewed against the proven `api/stickers.js`/
  `api/usuarios.js` batch-write shapes (`db.batch()`, `db.getAll()`-equivalent reads,
  `merge:true` sets); the pure `autoAgrupar`/`haversineM` core (the only actually-testable-offline
  logic per the locked "Runnable check" in design.md) has full self-check coverage instead.

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 2.1–2.11 | `api/sticker-asignaciones.test.js` (`node api/sticker-asignaciones.test.js`) | Unit (pure fixture, offline) | ✅ `node api/stickers.test.js` + `node api/usuarios.test.js` both OK before and after (neither file touched) | ✅ Written first; ran against a nonexistent module, confirmed `Cannot find module './sticker-asignaciones.js'` (exit 1) | ✅ Full `autoAgrupar`/`haversineM` implementation written, self-check passes (exit 0) | ✅ 4 cases: determinism (same input twice), `maxSize` cap (10 dense points → cap 3, every point placed once), `maxRadiusM` cap (far point excluded), empty input → `[]` | ✅ `node --check` clean; re-ran self-check after the syntax pass, still green |

### Test Summary
- **Total tests written**: 1 self-check file, 8 assertions (2 determinism, 1 `deepStrictEqual`
  group-count, 1 membership check, 2 `maxSize`-cap loop/sum assertions, 2 `maxRadiusM`-cap
  assertions, 1 empty-input assertion — plus the `assert.ok`/`assert.strictEqual`/
  `assert.deepStrictEqual` calls inside those blocks).
- **Total tests passing**: 8/8 (see Work Unit Evidence below for exact command output).
- **Layers used**: Unit (1 — the pure `autoAgrupar`/`haversineM` core), Integration (0 — no
  live/emulated Firestore in this batch, same constraint as Phase 1), E2E (0).
- **Approval tests**: None — no refactoring of existing files in this batch (`api/stickers.js`,
  `api/usuarios.js`, `api/refresh.js` were read as reference only, never modified).
- **Pure functions created**: `autoAgrupar`, `haversineM`. Firestore I/O (`listPuntos`,
  `listCuadrillas`, `runAutoAgrupar`, `crearCuadrilla`, `editarCuadrilla`, `asignarInspector`,
  `reasignarPunto`, `eliminarCuadrilla`) is isolated in its own async functions, all calling the
  pure core for any clustering decision.

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `node api/sticker-asignaciones.test.js` → RED (before implementation): `Error: Cannot find module './sticker-asignaciones.js'` (exit 1). After implementation → GREEN: `sticker-asignaciones.test.js OK` (exit 0). `node --check api/sticker-asignaciones.js` → clean (no output, exit 0). |
| Runtime harness command/scenario and exact result | No live/emulated Firestore available in this environment (same constraint recorded in Phase 1's batch). All 8 Firestore-backed action handlers were exercised via `node --check` (syntax) plus manual code review against `api/stickers.js`/`api/usuarios.js`'s proven `db.batch()`/`merge:true`/`db.getAll()`-equivalent patterns — no handler diverges from those shapes. The one piece of logic with a real runtime decision boundary (`autoAgrupar`'s clustering) is fully covered by the offline self-check instead (4 scenarios, all passing). Grep-based scope check: `rg "collection\('evaluaciones'\)\.(set\|update\|delete\|add)" api/sticker-asignaciones.js` → zero matches, satisfying spec.md's "Scope boundaries" scenario without needing a live collection. |
| Rollback boundary | Two new, independent files: `api/sticker-asignaciones.js`, `api/sticker-asignaciones.test.js`. Neither is imported by any other module in the repo yet (Phase 3's `web/js/stickers-asignacion.js` doesn't exist yet and isn't wired into `web/index.html`/`stickers.js` — no runtime caller). `git rm api/sticker-asignaciones.js api/sticker-asignaciones.test.js` (or `git revert` this commit) fully reverts this batch with zero impact on any other file, including Phase 1's untouched `integracion_F1/` files. |

## Next batch (Phase 3 — separate PR/branch)

- New branch (per `chain_strategy: stacked-to-main`) targeting `main` after this PR merges,
  implementing `web/js/stickers-asignacion.js` + `web/index.html`/`web/js/stickers.js`/
  `web/styles.css` wiring (tasks 3.1–3.9).
- Phase 3 calls `/api/sticker-asignaciones` (this batch's endpoint) directly from the browser via
  the existing `callApi(getToken, body)` pattern — no further backend work needed for Phase 3 to
  start.
- Carry task 0.2 forward again: Phase 3's task 3.5 (CRUD controls) explicitly plans a "small settings
  affordance to override" `maxRadiusM`/`maxSize` — the per-call override this batch's
  `runAutoAgrupar` already accepts (`body.maxRadiusM`/`body.maxSize`) is what that affordance should
  call; still no operator-confirmed default.
- Carry Phase 1's task 1.7 forward unchanged (Railway cron service creation, manual operator step,
  independent of Phase 2/3's PR chain).

---

# Batch 3 — Phase 3: Frontend (`feat/stickers-asignacion-3-frontend`)

Branch: `feat/stickers-asignacion-3-frontend` (branched from `feat/stickers-asignacion-2-api`, per
`chain_strategy: stacked-to-main` — Phase 2's `api/sticker-asignaciones.js` is present in the
working tree but not yet merged to `main`, that is expected).

## Scope of this batch

Phase 3 — Frontend Asignación sub-section. Tasks 3.1–3.8 complete; 3.9 (manual browser smoke test)
explicitly NOT performed — no live browser session available to this apply agent — see its own note
in `tasks.md` for exactly what was verified by static analysis instead.

## Completed tasks

- [x] **3.1** `web/js/stickers-asignacion.js` created: `callApi(getToken, body)` cloned verbatim
  from `stickers.js:19-30` (`ENDPOINT` swapped to `/api/sticker-asignaciones`);
  `initStickersAsignacion(root, {getToken, getInspectores})` renders `shellHtml()` once, `reload()`
  runs `Promise.all([listPuntos, listCuadrillas])`, returns `{ reload }` so the caller can re-fetch
  on subsequent opens without re-initializing.
- [x] **3.2** Three-way segmented control (`Roster · Evaluaciones · Asignación`) added to
  `shellHtml()` in `stickers.js` — the first sub-nav pattern inside `#view-stickers` (0.1's finding
  confirmed: nothing existed to extend). Each of the three sections now lives behind its own
  `<div data-sticker-section="...">` wrapper, toggled via `hidden`.
- [x] **3.3** Table: checkbox + 6 spec.md columns (dirección, zona, estado_asignacion, cuadrilla,
  inspector, tier). Client-side sort reuses the Panel's own existing `data-sort-field`/`.is-sorted`/
  `.th-sort-btn`/`.sort-arrow` convention from `table.js:151-166` (zero new CSS for sort UI — more
  reuse than task 3.8 anticipated). Filter chips are the one genuinely new interactive piece
  (`.asignacion-chip`/`.asignacion-filters`), since `.sticker-chip` is a display-only two-state
  (`is-on`/`is-off`) chip, not built for a 5-way single-select toggle group.
- [x] **3.4** Leaflet map cloned from `evaluaciones.js`'s setup (same `L.map`/`L.tileLayer`/
  `L.circleMarker`/`L.layerGroup`/`themechange` re-tile listener, guarded behind
  `typeof document !== 'undefined'` so the pure-logic self-check can still import the module under
  Node). 3-color legend reuses `.map-legend`/`.legend-row`/`.legend-swatch.legend-circle` **verbatim
  — zero new CSS**, since that component was already fully generic (hex passed via inline `style`),
  contrary to task 3.8's expectation that the legend would need `.asignacion-*` additions.
  `colorForPunto()` implements the exact blue/red/amber priority spec.md locks (tiene_sticker wins
  over estado_asignacion).
- [x] **3.5** CRUD controls, scoped to exactly the 4 controls spec.md's "CRUD affordances in the
  frontend" requirement and design.md ADR-4 both list (no more, no less — see Deviations below for
  the two explicitly NOT wired): "Auto-agrupar" button + two small number-input overrides
  (`maxRadiusM`/`maxSize`, sent only when non-empty — task 0.2's per-call override contract from
  Phase 2 consumed exactly as documented); checkbox column + "Crear cuadrilla" button
  (`window.prompt()` for the nombre — native dialog, no new modal component, per-point checkboxes
  disable themselves once a point already has a `cuadrilla_id`); per-cuadrilla inspector `<select>`
  in a new "Cuadrillas" list card (`getInspectores()` closure — reads `stickers.js`'s
  `inspectoresCache`, set from its existing roster `list` call, confirmed **zero new roster fetch**
  by inspection: no `action: 'list'` call anywhere in `stickers-asignacion.js`); per-point
  "Reasignar" `<select>` inline inside the Leaflet popup (design.md's literal "in the popup/detail"
  placement — populated on `popupopen`, same lazy-population pattern `evaluaciones.js` uses for its
  own popup button wiring).
- [x] **3.6** `web/index.html` — CORRECTION recorded in `tasks.md` itself: `#view-stickers` is an
  empty `<section ... hidden></section>`, fully populated by `stickers.js`'s `initStickers()` on
  every open (confirmed via `main.js`'s `switchView()`). There is no static markup in `index.html`
  to edit; 3.2's `shellHtml()` change already produces the sub-nav and all three
  `data-sticker-section` containers. Grepped `index.html` for "sticker" (case-insensitive) — only
  the pre-existing `.view-tab` button and the empty `#view-stickers` section remain, both untouched.
  Zero-line diff to `index.html` for this batch, by design not by omission.
- [x] **3.7** `stickers.js` wiring: `showSegment(name)` toggles the three section wrappers' `hidden`
  + the segment buttons' `.is-active`/`aria-selected`; on `asignacion`, lazy-inits
  `initStickersAsignacion` exactly once (`asignacionHandle === null` guard) and calls
  `.reload()` on every subsequent open. `inspectoresCache` is set inside the roster `reload()`
  (already-existing function, one new assignment line) and read by the `getInspectores` closure
  passed into `initStickersAsignacion`.
- [x] **3.8** `web/styles.css` additions, all under one new `.asignacion-*` block (30 lines):
  `.asignacion-segmented`/`.asignacion-segment` (segmented control, genuinely new — no prior
  sub-nav pattern existed per 0.1), `.asignacion-filters`/`.asignacion-chip` (interactive filter
  toggle group, `.sticker-chip` wasn't built for this), `.asignacion-inline-field`/`.asignacion-check`/
  `.asignacion-inspector-select` (small layout tweaks for the toolbar's number inputs and the
  cuadrilla list's select). Everything else (table sort UI, estado pill via `.eval-pill`, map
  legend/popup via `.map-legend`/`.map-popup`, cards via `.card`/`.card-toolbar`/`.eval-map`, roster
  list shape via `.sticker-list`/`.sticker-row`/`.sticker-code`/`.sticker-identity`) is reused
  verbatim, zero new CSS — a stricter reading of "reuse `.sticker-*`, add `.asignacion-*` only where
  genuinely different" than task 3.8's own text anticipated (it expected the legend to need new
  CSS too; it didn't).
- [ ] **3.9** NOT independently verified in a real browser — see the note left directly in
  `tasks.md` under this task for the exact split of what static analysis covered vs. what still
  needs a live/emulated session.

## Deviations from design / risks discovered

- **`editarCuadrilla` and `eliminarCuadrilla` have NO frontend UI in this batch.** Re-read
  spec.md's "Requirement: CRUD affordances in the frontend" and design.md ADR-4's own CRUD-controls
  paragraph closely at apply time: both list exactly 4 controls (auto-agrupar, manual
  create-from-selection, assign/reassign inspector, per-point reasignar) — neither mentions an
  "editar cuadrilla" (add/remove points from an existing group) or "eliminar cuadrilla" affordance.
  Phase 2's API supports both actions (`editarCuadrilla`/`eliminarCuadrilla` in
  `api/sticker-asignaciones.js`), so wiring them later is additive, not a schema change — just two
  more buttons/selects calling an endpoint that already exists. Flagging explicitly since this is a
  scope reduction versus what a skim of the API surface alone would suggest, but it matches the
  locked spec/design text precisely.
- **"Ver detalle" (half of design.md's "Ver detalle / Reasignar" popup pattern) was not built as a
  separate action.** Unlike `evaluaciones.js` (where the popup is a short summary linking to a full
  ATC-20 detail modal with photos), every field available on a `sticker_matches` point
  (dirección/estado/zona/tier/cuadrilla) is already shown directly in the popup body — there is no
  additional data behind a "ver detalle" click to justify a second modal. Lazy call: building an
  empty-content modal just to match the literal two-word phrase would be scaffolding with no
  payload behind it.
- **`initStickersAsignacion`'s "once per session" is once per Stickers-tab open, not once per
  browser session.** `main.js`'s `switchView('stickers')` unconditionally re-calls `initStickers()`
  (full `root.innerHTML = shellHtml()` replace) every time the Stickers top-level tab is opened —
  this is pre-existing behavior for the roster/evaluaciones sections too (the file's own top comment
  says "Refetches from the API on each open so both sections are always current"), not something
  this batch introduced. `asignacionHandle` is a local `let` inside `initStickers`'s closure, so it
  resets on every fresh Stickers-tab open; re-opening the Asignación segment within the SAME
  Stickers-tab-open session correctly calls `.reload()`, not a second `initStickersAsignacion()`, an
  exact match for spec.md's "Init runs once on first Asignación open" scenario read at that
  granularity. Documented here in case "session" was meant more broadly — no code change made
  without operator confirmation, since it would mean changing the pre-existing (unrelated to this
  change) roster/evaluaciones refetch-on-every-open behavior too.
- **No live/emulated Firestore or browser used in this batch** — same constraint recorded in Phases
  1 and 2 (no credentials/browser in this environment). All CRUD wiring is code-reviewed against
  Phase 2's own `api/sticker-asignaciones.js` request/response shapes (confirmed field names
  `{cuadrilla_id, add, remove}` etc. match exactly what Phase 2's apply-progress documented as "the
  real contract").
- **No other deviations.** Table columns, map colors, segmented-control placement, and the
  lazy-init contract all match `spec.md` and `design.md` ADR-4/ADR-5 exactly.

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 3.1, 3.3, 3.4 (pure logic only) | `web/js/stickers-asignacion.test.mjs` (`node js/stickers-asignacion.test.mjs`, from `web/`) | Unit (pure fixture, offline) | ✅ `node --test "js/**/*.test.mjs"` — all 5 pre-existing self-checks (`analista`, `charts`, `data`, `evaluaciones`, `utils`) still pass before and after, none touched | ✅ Written first; ran against a nonexistent module, confirmed `ERR_MODULE_NOT_FOUND` (exit 1) | ✅ `colorForPunto`/`buildRows`/`sortRows`/`filterRows` implemented, self-check passes (exit 0) | ✅ 5 `colorForPunto` cases (blue precedence over amber/red, both amber sub-states), a 2-point `buildRows` fixture (one grouped+assigned, one bare pending) covering label fallback (`—`) and label hit, `sortRows` asc+desc, `filterRows` `'todos'`/`undefined`/a real filter | ✅ Both `node --check`-equivalent (`node -e "import(...)"`) syntax passes clean for `stickers.js` and `stickers-asignacion.js`; re-ran the self-check after, still green |
| 3.2, 3.5–3.8 (DOM/CSS wiring, no pure logic) | N/A — DOM wiring has no offline self-check surface, same call as `usuarios-tab` task 2.9's precedent, explicitly endorsed for this batch's task 3.9 | N/A | N/A | N/A | ✅ Both files import/parse cleanly under Node (catches syntax errors); traced call graph by reading, not executing, in a browser | ➖ N/A | ➖ N/A |

### Test Summary
- **Total tests written**: 1 self-check file (`stickers-asignacion.test.mjs`), 16 assertions across
  `colorForPunto` (5), `buildRows` (6), `sortRows` (2 — asc/desc order arrays via `deepEqual`),
  `filterRows` (3 — `'todos'`, `undefined`, a real filter value).
- **Total tests passing**: 16/16 (`node --test "js/**/*.test.mjs"` → `tests 6, pass 6, fail 0` across
  the whole `web/js/` suite, including this new file).
- **Layers used**: Unit (1 — the pure table/map-color logic), Integration (0), E2E (0 — no browser
  available in this environment, see task 3.9's note).
- **Approval tests**: None — `evaluaciones.js`/`table.js`/`utils.js` were read as pattern reference
  only, never modified.
- **Pure functions created**: `colorForPunto`, `buildRows`, `sortRows`, `filterRows` (all exported
  from `stickers-asignacion.js` for the self-check). DOM rendering (`shellHtml`, `tableHtml`,
  `cuadrillasHtml`, `popupHtml`) and Firestore-via-API calls (`callApi`, `reload`, the four CRUD
  handlers) are not pure and have no offline self-check surface, matching the same proportion call
  `usuarios-tab` task 2.9 already established for this repo's DOM-wiring tasks.

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `node js/stickers-asignacion.test.mjs` (from `web/`) → RED (before implementation): `Error [ERR_MODULE_NOT_FOUND]: Cannot find module '.../stickers-asignacion.js'` (exit 1). After implementation → GREEN: `ok — stickers-asignacion.js pure table/map logic` (exit 0). `node --test "js/**/*.test.mjs"` (from `web/`) → `tests 6, pass 6, fail 0` (all pre-existing self-checks plus the new one). `node -e "import('./js/stickers.js')"` and the same for `stickers-asignacion.js` → both resolve/parse cleanly (`ok`), no syntax errors introduced into either file. |
| Runtime harness command/scenario and exact result | No live browser or Firestore available in this environment (same constraint recorded in Phases 1 and 2). Runtime behavior (segmented-control clicks, Leaflet map mount/fitBounds/legend, the single-`listPuntos`+`listCuadrillas`-call-on-first-open assertion, and all 4 CRUD round-trips) was verified only by reading the code's call graph, not by executing it in a DOM — explicitly NOT claimed as a passed manual smoke test. Task 3.9 in `tasks.md` is left unchecked with the exact split of what was/wasn't verified, per instruction. The orchestrator should arrange a real browser session (e.g. via a `run` skill or Chrome DevTools) before or alongside `sdd-verify` for this specific gap. |
| Rollback boundary | Four files touched, all independent of Phase 1/2's already-committed work: new `web/js/stickers-asignacion.js` + `web/js/stickers-asignacion.test.mjs`; modified `web/js/stickers.js` (3 additive edits: one new import, `shellHtml()`'s new sub-nav markup, `initStickers()`'s new segment-switching/lazy-init block — the pre-existing roster/evaluaciones logic inside `initStickers` is untouched code, only re-indented by the new wrapper divs); modified `web/styles.css` (one new 30-line `.asignacion-*` block, inserted between two pre-existing sections, nothing else in the file touched). `git checkout -- web/js/stickers.js web/styles.css && git rm web/js/stickers-asignacion.js web/js/stickers-asignacion.test.mjs` (or `git revert` this batch's commit(s)) fully reverts Phase 3 with zero impact on Phase 1 (`integracion_F1/`, separate repo, untouched) or Phase 2 (`api/sticker-asignaciones.js`, untouched by this batch). |

## Next steps (not this agent's scope)

- Task 3.9's real browser smoke test — flagged above, needed before/alongside `sdd-verify`.
- Task 0.2 (`maxRadiusM`/`maxSize` defaults) still open across all 3 phases — no operator
  confirmation obtained in any apply batch; shipped as named placeholder constants everywhere
  (API + this batch's UI hint values), one-line change if/when confirmed.
- Task 1.7 (Railway cron service) still open — manual operator step, independent of this PR chain.
- Phase 4 (Firestore console rules for `sticker_matches`/`cuadrillas`) is a manual console step, not
  a repo diff, tracked in `tasks.md` §4.1 — should happen before or shortly after this PR merges so
  the two new collections aren't left open to client-direct reads in the interim.
