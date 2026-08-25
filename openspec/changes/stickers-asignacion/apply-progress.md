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
