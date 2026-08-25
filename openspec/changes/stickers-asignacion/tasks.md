# Tasks: Stickers — cruce y asignación

Change: `stickers-asignacion` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-tasks

Ordered, hierarchical, grouped by phase per `openspec/config.yaml` (`group by phase`,
`hierarchical numbering`, `completable in one session`). Follows the 4-work-unit split locked in
`design.md`'s "Size and commit/PR split": (1) pipeline job, (2) API, (3) frontend, (4) Firestore
console rules (non-code). `strict_tdd: true` — every non-trivial logic task has a RED task (write
the failing/offline self-check) before its GREEN task (make it pass), per `design.md`'s "Runnable
check (locked)" section.

---

## Phase 0 — Pre-work: confirm open decisions before locking code

Carries `design.md`'s "Risks / open decisions carried to tasks" into concrete gate tasks. Both
block Phase 2/3 work that depends on their answer; do them first so no code has to be revisited.

- [x] **0.1** Read the current `web/js/stickers.js` markup/lifecycle (already done for this task
      list — confirmed: today's `shellHtml()` renders the evaluaciones section and the roster
      section back-to-back inside `#view-stickers` with **no segmented control / section-switch
      pattern** at all, `web/js/stickers.js:122-130`). Record this finding as the basis for **3.2**:
      a three-way segmented control (Roster · Evaluaciones · Asignación) must be introduced for the
      first time by this change, not extended from an existing one.
      — Satisfies: `design.md` risk 2.

- [ ] **0.2** Confirm `maxRadiusM` (design.md placeholder: 800m) and `maxSize` (placeholder: 8
      points) defaults for `autoAgrupar` with the operator before **2.4** hard-codes them. If no
      answer is available before implementation, ship the placeholders as named constants (not
      magic numbers) at the top of `api/sticker-asignaciones.js` so a later tune is a one-line
      change, and note the placeholder status in the PR description.
      — Satisfies: `design.md` risk 1; `proposal.md` risk 4.

---

## Phase 1 — Pipeline: `integracion_F1/cruce_sticker.py`

Commit: `feat(pipeline): cruce_sticker job`

Depends on: Phase 0 (no blocking dependency — can start in parallel with 0.2, since defaults don't
affect this phase).

- [x] **1.1** (RED) Write the offline `--check` self-test fixture and assertions FIRST, in
      `integracion_F1/cruce_sticker.py` (idiom: `_selfcheck_cruce_sticker`, mirrors the notebook's
      existing self-check and `cruce_gestor.py --check`). Assert, against a fixture
      Firestore-shaped dict (no network):
      (a) re-writing pipeline-owned fields on an existing doc leaves `estado_asignacion`,
      `cuadrilla_id`, `inspector_uid`, `asignado_en`, `reasignado_de` unchanged;
      (b) a first-write (no prior doc) seeds `estado_asignacion:'pendiente'`,
      `cuadrilla_id:null`, `inspector_uid:null`.
      This MUST fail (module doesn't exist yet) before 1.2-1.5 are written.
      — Satisfies: *Requirement: `sticker_matches` document ownership and merge safety* (all 3
      scenarios), *Requirement: `cruce_sticker.py` reuses the existing matching cascade* (`--check`
      passes offline scenario).

- [x] **1.2** (GREEN) Scaffold `integracion_F1/cruce_sticker.py` structured like `asignar_f3.py`:
      `main()`, `--check`, `--dry`, `--top N` flags, module docstring. Doc id function
      `doc_id(fuente, registro_id) -> f"{fuente}_{registro_id}"` (ADR-1) — pure, exported for 1.1's
      fixture to call directly.
      — Satisfies: *Requirement: `sticker_matches` document ownership and merge safety* (doc id
      stability scenario).

- [x] **1.3** (GREEN) Load Panel points the same way the notebook does: `inspections.json` +
      `puntos_israel_cali.json`, EXIF-corrected `x`/`y` coords (per `exploration.md` §1). Read
      `evaluaciones` from Firestore using the same 3-tier credential resolution as
      `subir_cruce_firebase.py` (`STICKERS_FIREBASE_SA` path → `FIREBASE_SERVICE_ACCOUNT_JSON` env
      → ADC), targeting the `sismo-agosto-sgred` project (NOT the `integracion_F1` subproject's
      default `dagma-85aad` — this is a different Firebase project than `subir_cruce_firebase.py`'s
      own target, confirm the project id is passed explicitly).
      — Satisfies: *Requirement: `cruce_sticker.py` reuses the existing matching cascade* (input
      side).

- [x] **1.4** (GREEN) Run the matching cascade by importing `nearest`, `match_by_direccion`,
      `build_addr_index`, `addr_key` from `integracion_F1/cruce_gestor.py` directly — do not copy
      or fork their bodies into `cruce_sticker.py`.
      — Satisfies: *Requirement: `cruce_sticker.py` reuses the existing matching cascade* (matching
      logic lives in one place scenario).

- [x] **1.5** (GREEN) Implement the write path: for each point, split fields into the
      pipeline-owned subset (ADR-1) and pre-read existence via `db.getAll()` batch (mirrors the
      `inspectores` join pattern in `api/stickers.js:70-73`) so a first-write can seed
      `estado_asignacion:'pendiente'` alongside the pipeline fields in the same `merge:true` set —
      never a full-document `set()`. Batch writes in groups of ≤500 (`subir_cruce_firebase.py`'s
      `upload()` pattern, `subir_cruce_firebase.py:59-71`). Run 1.1 and confirm it now passes.
      — Satisfies: *Requirement: `sticker_matches` document ownership and merge safety* (all 3
      scenarios, now against real code), *Requirement: `cruce_sticker.py` reuses the existing
      matching cascade* (batched writes scenario).

- [x] **1.6** New file `integracion_F1/job_sticker.py`, wrapping `cruce_sticker.main()` with the
      same durable-logging harness as `job_asignaciones.py` (tee stdout/stderr to the mounted
      volume, `runs_sticker.jsonl`, non-zero exit on failure) — copy `job_asignaciones.py`'s
      structure (`integracion_F1/job_asignaciones.py:1-53`) verbatim, swap the wrapped module.
      — Satisfies: *Requirement: `cruce_sticker.py` reuses the existing matching cascade* (job
      runnability, indirect).

- [ ] **1.7** — NOT COMPLETABLE BY sdd-apply (manual operator action, no repo diff — see
      `apply-progress.md`). Cron wiring — **correcting `design.md` ADR-2's claim that
      `integracion_F1/railway.json`
      gets a new `startCommand`/`cronSchedule` pair**: the actual `railway.json` in this repo is
      shared build config only (`"builder": "DOCKERFILE"`, no per-service fields) and its own
      comment states per-service `startCommand`/`cronSchedule` are set on each Railway service
      instance directly (CLI/dashboard), not in this file — `scripts/railway_setup.py` referenced
      by that comment does not exist in this repo. This task is therefore a **manual step, not a
      repo diff**: create a new Railway cron service in the `integracion_F1` project (same Docker
      image, `railway up --service <name>` from `integracion_F1/`), `startCommand: python
      job_sticker.py`, daily `cronSchedule` matching `job_asignaciones.py`'s slot family. Confirm
      env vars (`STICKERS_FIREBASE_SA` or `FIREBASE_SERVICE_ACCOUNT_JSON`) are set on the new
      service.
      — Satisfies: `design.md` ADR-2 "Cron wiring"; `proposal.md` risk 3 (cadence confirmed daily,
      no on-demand trigger in v1).

---

## Phase 2 — API: `api/sticker-asignaciones.js`

Commit: `feat(api): sticker-asignaciones endpoint`

Depends on: none at the code level (reads/writes Firestore directly, not `cruce_sticker.py`'s
output at build time) — but exercising it end-to-end needs Phase 1's job to have run at least once
against a real/emulated Firestore. Can be written in parallel with Phase 1.

- [x] **2.1** Scaffold `api/sticker-asignaciones.js` as a byte-for-byte copy of the
      `api/stickers.js` auth preamble and skeleton (`api/stickers.js:225-262`): 405 guard, Bearer
      token extraction, `verifyFirebaseToken` + `roleFromClaims` from `./refresh.js`, fail-closed
      `roleFromClaims(claims) !== 'admin'` → 403, `try` router dispatching on `body.action`,
      `err.status || 502`. Copy the `getAdmin()` singleton (`api/stickers.js:50-61`) rather than
      importing it.
      — Satisfies: *Requirement: `api/sticker-asignaciones.js` is admin-only* (non-admin call
      rejected scenario).

- [x] **2.2** (RED) Write `api/sticker-asignaciones.test.js` FIRST, `assert`-based `demo()`
      self-check (no framework, mirrors the self-check idiom already exported from
      `api/stickers.js:264-270` for its own pure helpers), covering the `autoAgrupar` clustering
      function in isolation:
      - same fixture point set called twice with identical params → identical group membership
        (determinism).
      - a cluster of points closer than `maxRadiusM` but exceeding `maxSize` → no resulting group
        larger than `maxSize`.
      - a point farther than `maxRadiusM` from every seed → not added to that seed's group.
      - empty input → returns `[]`, no error.
      This MUST fail (function doesn't exist yet) before 2.4 is written.
      — Satisfies: *Requirement: `autoAgrupar` clusters pending points deterministically* (all 4
      scenarios); locked "Runnable check" in `design.md`.

- [x] **2.3** Implement `listPuntos` (`{ok, puntos}`, full `sticker_matches` read, no
      `inspections.json`/`puntos_israel_cali.json` read anywhere in the handler) and
      `listCuadrillas` (`{ok, cuadrillas}`, full `cuadrillas` read).
      — Satisfies: *Requirement: `listPuntos` returns lean point data without loading full Panel*,
      *Requirement: `listCuadrillas` returns current groups*.

- [x] **2.4** (GREEN) Implement the pure `autoAgrupar(puntos, {maxRadiusM, maxSize})` clustering
      function per ADR-3's greedy nearest-neighbor pseudocode (stable `[lat, lon]` sort order, no
      RNG, no k-means), plus a `haversineM` helper (five-line port if the repo has no existing JS
      haversine — check `web/js/evaluaciones.js` and `web/js/*.js` for one first before adding a
      new copy). Export the pure function for 2.2's test file. Run 2.2 and confirm it now passes.
      Mark the O(n²) scan: `// ponytail: O(n²) greedy grouping, fine to a few thousand pending
      points; switch to a spatial grid pre-bucket if it ever gets slow.`
      — Satisfies: *Requirement: `autoAgrupar` clusters pending points deterministically* (now
      against real code).

- [x] **2.5** Wire the `autoAgrupar` action handler: read `pendiente` points with no
      `cuadrilla_id`, call 2.4's pure function with `maxRadiusM`/`maxSize` (from **0.2**'s
      confirmed or placeholder constants), create `cuadrillas` docs with `origen:'auto'`,
      `inspector_uid:null`, set `cuadrilla_id` on member points — MUST NOT touch
      `estado_asignacion`. Empty pending set → `{ok, cuadrillas:[]}`, no error.
      — Satisfies: *Requirement: `autoAgrupar` clusters pending points deterministically* (does not
      assign an inspector scenario, empty-set no-op scenario), *Requirement: `cuadrillas` document
      shape* (cuadrilla creation sets membership scenario).

- [x] **2.6** Implement `crearCuadrilla` (`{nombre, puntos}` → new doc, `origen:'manual'`, sets
      `cuadrilla_id` on every listed point).
      — Satisfies: *Requirement: `crearCuadrilla` supports manual grouping*, *Requirement:
      `cuadrillas` document shape* (membership scenario, manual case).

- [x] **2.7** Implement `editarCuadrilla` (add/remove points from an existing cuadrilla, keeping
      `cuadrilla_id` on member points consistent with `puntos[]`; error + no writes if the
      `cuadrilla_id` doesn't exist).
      — Satisfies: *Requirement: `editarCuadrilla` supports adding/removing points* (both
      scenarios).

- [x] **2.8** Implement `asignarInspector` (`{cuadrilla_id, inspector_uid}` → propagate
      `inspector_uid`, `asignado_en` (server timestamp), `estado_asignacion:'asignado'` to every
      member point of that cuadrilla).
      — Satisfies: *Requirement: `asignarInspector` propagates to every point in a cuadrilla*.

- [x] **2.9** Implement `reasignarPunto` (`{punto_id, nuevo_inspector_uid}` → set
      `reasignado_de` = the point's current `inspector_uid` (or `null` if it had none),
      `inspector_uid` = new value; `cuadrilla_id` untouched).
      — Satisfies: *Requirement: `reasignarPunto` reassigns a single point with a breadcrumb* (both
      scenarios).

- [x] **2.10** Implement `eliminarCuadrilla` (clear `cuadrilla_id`/`inspector_uid` on every member
      point BEFORE deleting the `cuadrillas` doc, so no point is left referencing a nonexistent
      cuadrilla even if the delete step fails partway).
      — Satisfies: *Requirement: `eliminarCuadrilla` clears membership before deleting*.

- [x] **2.11** Runnable check pass: `node api/sticker-asignaciones.test.js` (or
      `node --test` if the repo's `api/` self-checks run that way — match whatever
      `api/stickers.test.js`/`api/usuarios.test.js` actually use) green end to end; manually confirm
      *Requirement: Scope boundaries*' "evaluaciones collection is never written" by grepping this
      file for any `.collection('evaluaciones')` write call (`set`/`update`/`delete`/`add`) —
      expect zero matches.
      — Satisfies: *Requirement: Scope boundaries* (evaluaciones never written scenario).

---

## Phase 3 — Frontend: Asignación sub-section

Commit: `feat(web): asignación sub-section`

Depends on: Phase 2 (calls `/api/sticker-asignaciones`), Phase 0.1 (sub-nav decision).

- [ ] **3.1** Create `web/js/stickers-asignacion.js` cloning the `callApi(getToken, body)` helper
      verbatim from `web/js/stickers.js:19-30` (swap `ENDPOINT` to
      `/api/sticker-asignaciones`), and `initStickersAsignacion(root, {getToken})` with a
      render-shell-once / `reload()` fetches `listPuntos` + `listCuadrillas` / re-render lifecycle
      matching `initStickers`'s shape (`web/js/stickers.js:134-162`).
      — Satisfies: *Requirement: Mounted as a sub-section of the existing Stickers tab* (init runs
      once scenario).

- [ ] **3.2** Introduce the three-way segmented control (Roster · Evaluaciones · Asignación) inside
      `#view-stickers` for the first time (per **0.1**'s finding — no existing sub-nav to extend).
      Add it to `shellHtml()` in `web/js/stickers.js`, toggling which of the three section
      containers is visible; the existing roster/evaluaciones sections keep rendering as before,
      just gated behind the new control instead of always both showing.
      — Satisfies: *Requirement: Mounted as a sub-section of the existing Stickers tab* (no new
      top-level tab scenario).

- [ ] **3.3** Add the table: columns dirección, zona, estado_asignacion, cuadrilla, inspector,
      tier; client-side sort on column-header click (over the already-fetched `puntos` array, same
      weight class as the `usuarios-tab` inline-sort decision — no new dependency); filter chips
      for `estado_asignacion`.
      — Satisfies: *Requirement: Table view — sortable, filterable by `estado_asignacion`* (both
      scenarios).

- [ ] **3.4** Add the Leaflet map: clone `evaluaciones.js`'s map setup, `L.circleMarker` per point
      colored blue (`tiene_sticker === true`) / red (`estado_asignacion === 'pendiente'`) / amber
      (`asignado` or `en_proceso`), 3-color legend, `fitBounds()` on load, popup with
      "Ver detalle / Reasignar".
      — Satisfies: *Requirement: Map view — 3-color legend* (all 3 scenarios).

- [ ] **3.5** Add CRUD controls: "Auto-agrupar" button (calls `autoAgrupar` with the
      `maxRadiusM`/`maxSize` defaults from **0.2**, small settings affordance to override); manual
      multi-select (checkbox column) → "Crear cuadrilla" from selection (calls `crearCuadrilla`);
      per-cuadrilla inspector `<select>` populated from the `inspectores` roster the Stickers tab
      already loads (`initStickers`'s existing `list` call — no new roster fetch) →
      `asignarInspector`; per-point "Reasignar" action in the popup/detail →
      `reasignarPunto`.
      — Satisfies: *Requirement: CRUD affordances in the frontend* (all 3 scenarios).

- [ ] **3.6** Wire `web/index.html`: no new `.view-tabs` entry (confirm none added). Add the
      "Asignación" segment/button inside `#view-stickers`'s new sub-nav (3.2) and a
      `<div data-sticker-section="asignacion" hidden>` container next to the roster/evaluaciones
      ones.
      — Satisfies: *Requirement: Mounted as a sub-section of the existing Stickers tab* (no new
      top-level tab scenario).

- [ ] **3.7** Wire `web/js/stickers.js` (owner of the Stickers section-switch after 3.2): import
      and lazy-call `initStickersAsignacion(container, {getToken})` the first time the "Asignación"
      segment is opened; subsequent opens in the same session call `reload()` instead of
      re-initializing (mirror how `initEvaluaciones` is already wired at
      `web/js/stickers.js:143-145`, but gated on first-open rather than unconditional).
      — Satisfies: *Requirement: Mounted as a sub-section of the existing Stickers tab* (lazy init
      + init-runs-once scenarios).

- [ ] **3.8** Wire `web/styles.css`: reuse `.sticker-*` table/chip styles; add `.asignacion-*` only
      for the segmented control and the 3-color legend (matching the `usuarios-tab` precedent of
      styling only what genuinely differs, `openspec/changes/archive/2026-08-24-usuarios-tab/tasks.md`
      task 2.8's approach).
      — Satisfies: no single spec requirement directly; supports 3.2-3.5's rendering.

- [ ] **3.9** Runnable check: manual smoke test — log in as admin, open Stickers tab, confirm
      Roster/Evaluaciones still render as before with the new segmented control added (no
      regression); open "Asignación" for the first time, confirm exactly one `listPuntos` +
      `listCuadrillas` call fires (network tab), table renders with sort/filter working, map shows
      3 colors, auto-agrupar creates cuadrillas without touching `estado_asignacion`, manual
      multi-select creates a cuadrilla, assign/reassign inspector round-trips. Re-open the segment
      and confirm no duplicate init call, only `reload()`. No automated UI test — same proportion
      call as `usuarios-tab` task 2.9 (DOM wiring plus an already-tested API).
      — Satisfies: *Requirement: Mounted as a sub-section of the existing Stickers tab* (all
      scenarios, end to end).

---

## Phase 4 — Firestore console rules (manual, NOT a repo diff)

**Not code — do not attempt to implement this as a file edit at apply time.** No file in this repo
governs the deployed ruleset for the `sismo-agosto-sgred` project; `integracion_F1/firestore.rules`
belongs to the separate `dagma-85aad` project (same caveat already on record from `usuarios-tab`).

- [ ] **4.1** In the `sismo-agosto-sgred` Firebase console → Firestore → Rules, add rules for the
      two new collections `sticker_matches` and `cuadrillas` denying all client reads/writes
      (admin-SDK/server-only access via `api/sticker-asignaciones.js` and
      `integracion_F1/cruce_sticker.py`), mirroring the existing posture for `evaluaciones` /
      `inspectores`. Apply directly in the console; there is no PR for this step.
      — Satisfies: *Requirement: Scope boundaries* (direct client Firestore read is rejected
      scenario).

---

## Review Workload Forecast

- **Estimated changed lines (rough, per phase):**
  - Phase 1 (`cruce_sticker.py` ~180-220, `job_sticker.py` ~55 copy-of-`job_asignaciones.py`,
    `--check` fixture ~50-70): **~285-345 lines**.
  - Phase 2 (`api/sticker-asignaciones.js` ~260-320 incl. 8 actions + `autoAgrupar`,
    `api/sticker-asignaciones.test.js` ~70-90): **~330-410 lines**.
  - Phase 3 (`web/js/stickers-asignacion.js` ~280-350 table+map+CRUD, `web/js/stickers.js` sub-nav
    edit ~40-60, `index.html` ~10-15, `styles.css` ~40-70): **~370-495 lines**.
  - Phase 4: 0 repo lines (console-only).
  - **Total: roughly 985-1250 authored lines.**
- **400-line budget risk: High.** Every one of the three code phases individually approaches or
  exceeds 400 lines on its own (Phase 3 alone likely crosses it), and the total is 2.5-3x the
  budget. This matches `proposal.md`'s own "Rough size" call — do not undersell it.
- **Chained PRs recommended: Yes.** Ship as 3 sequential PRs matching the phase boundaries
  (Phase 1 → Phase 2 → Phase 3), each independently reviewable and each comfortably at or under the
  400-line single-lens-review threshold on its own; Phase 4 is a console note attached to whichever
  PR description mentions the new collections first (no PR of its own).
- **Decision needed before apply: Yes.** Two items must resolve before/at the apply gate: (a)
  **0.2** — `maxRadiusM`/`maxSize` defaults confirmed with the operator, or explicitly shipped as
  named placeholder constants; (b) the Phase 1 cron-wiring correction (**1.7**) — confirm the
  Railway service creation happens as a manual step outside this repo's diff, since
  `design.md` ADR-2 assumed a `railway.json` edit that the actual file's own contents rule out.
