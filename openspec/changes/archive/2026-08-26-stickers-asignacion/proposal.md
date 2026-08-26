# Proposal: Stickers — cruce y asignación

Change: `stickers-asignacion` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-propose

## Why

`integracion_F1/stickers_analysis.ipynb` already knows, in principle, which Panel points still
lack a field sticker — but only as a one-shot notebook run against live Firestore, with no
persisted result and no way to act on it. Today, finding out "who's missing a sticker" means
re-running the notebook and reading a dataframe; there is no recurring answer, no way to see it in
the dashboard, and no way to turn a "missing" point into a work assignment for an inspector. As the
sticker campaign scales, an operator needs a standing, low-cost view of matched-vs-pending points
and a way to organize the pending ones into cuadrillas and hand them to specific inspectors —
without re-loading the full, heavy Panel dataset every time.

## What Changes (v1, in scope)

- **New Firestore collection `sticker_matches`** — one lightweight doc per Panel point, refreshed
  recurringly by a new Python job (not read live from the notebook, not re-derived by the browser).
  Carries the cruce result (`tiene_sticker`, `tier`, `sticker_dist_m`, `direccion`, `coords`,
  `zona_id`) plus an admin-owned assignment sub-state (`estado_asignacion`, `cuadrilla_id`,
  `inspector_uid`) that the job never overwrites.
- **New job `integracion_F1/cruce_sticker.py`** — extracts the cascade already proven in the
  notebook (via `cruce_gestor.py`, not reimplemented) into a script, runs on a new Railway cron
  service (same image as the other `integracion_F1` jobs), batch-writes to `sticker_matches` with
  `merge:true`.
- **New Firestore collection `cuadrillas`** — groups of pending points, each optionally linked to
  one inspector (`origen: auto|manual`).
- **New admin-only endpoint `api/sticker-asignaciones.js`** — list points/cuadrillas, auto-group
  pending points by proximity (deterministic greedy clustering, no external service), create/edit
  cuadrillas manually, assign/reassign an inspector, delete a cuadrilla.
- **New sub-section inside the existing Stickers tab** (`web/js/stickers-asignacion.js`, lazy-init
  like `evaluaciones.js`): a sortable table (matched/pending, estado, zona, cuadrilla, inspector)
  and a Leaflet map tab with blue circles (tiene sticker) / red circles (pendiente), plus the CRUD
  controls (auto-agrupar, seleccionar puntos, asignar/reasignar inspector).

## Explicitly Out of Scope

- **No changes to the sticker-capture flow itself** (the ATC-20 field form, `evaluaciones` writes)
  — this change only reads `evaluaciones`, never writes to it.
- **No new inspector-roster CRUD.** Inspectors already exist in `inspectores/{uid}` via the
  Stickers tab; this change only reads that roster to populate assignment dropdowns.
- **No time-series / history UI for reassignments** beyond a single `reasignado_de` breadcrumb
  field on the point doc — a full audit trail (who reassigned what, when) is a possible Phase 2 if
  the operator asks for it.
- **No public Firestore read rule.** `sticker_matches`/`cuadrillas` are read only through the new
  admin-only API, mirroring how `evaluaciones`/`inspectores` are already gated — not a new
  client-readable collection.
- **No `web/data/*.json` output.** Unlike `asignar_f3.py`, this pipeline writes straight to
  Firestore, sidestepping the known `integracion_F1` image `web/`-omission bug that silently
  no-ops `asignaciones.json` in production today.

## Impact

New / touched surfaces:

- **New `integracion_F1/cruce_sticker.py`** + a new Railway cron service entry (same Docker image
  as `job.py`/`job_asignaciones.py`).
- **New `api/sticker-asignaciones.js`** — admin-SDK, same auth preamble shape as
  `api/stickers.js`/`api/usuarios.js`.
- **New `web/js/stickers-asignacion.js`** — table + map + CRUD, mounted inside `#view-stickers`.
- **`web/index.html`** — a sub-tab/segmented-control inside the Stickers panel (roster /
  evaluaciones / asignación), no new top-level `.view-tabs` entry (locked decision, see brainstorm
  conversation).
- **Firestore console (`sismo-agosto-sgred`)** — two new collections, `sticker_matches` and
  `cuadrillas`, with rules limiting reads/writes to the Admin SDK only (no client-direct access,
  same posture as `evaluaciones`/`inspectores`).

## Risks & Open Questions

1. **`sticker_matches` doc-id stability.** The id must be derived from a stable Panel key
   (`fuente` + `registro_id`/GlobalID) so re-running the job updates the same doc instead of
   duplicating it — needs to be locked in design (ADR-1).
2. **Merge semantics.** The job must never clobber `estado_asignacion`/`cuadrilla_id`/
   `inspector_uid` — enforced by only ever writing the pipeline-owned field subset with
   `merge:true`, never a full document `set()`. A test/self-check should assert this.
3. **Cron cadence vs. staleness.** Daily (matching `job_asignaciones.py`) may be too slow right
   after a sticker is applied in the field; confirm acceptable staleness with the operator, or add
   a manual "recalcular" trigger later if needed (kept out of v1 per the brainstorm decision to
   keep this a scheduled job, not on-demand).
4. **Auto-agrupar determinism.** Clustering must be deterministic (no RNG) so re-running
   "auto-agrupar" on an unchanged point set doesn't need explanation for producing different
   groups — a plain greedy nearest-neighbor pass, not k-means with random init (ADR-3).
5. **Rules for the two new collections are console-managed**, same caveat already on record from
   `usuarios-tab`: `integracion_F1/firestore.rules` in this repo belongs to a *different* project
   (`dagma-85aad`) and is not the deployed ruleset for `sismo-agosto-sgred`. Any rule change for
   `sticker_matches`/`cuadrillas` must be applied in the `sismo-agosto-sgred` console directly.

## Rough size

Four work units map cleanly to a backend→frontend split: (1) `cruce_sticker.py` + cron wiring, (2)
`api/sticker-asignaciones.js`, (3) `web/js/stickers-asignacion.js` + tab wiring, (4) Firestore
console rules for the two new collections (manual, not a repo diff). Likely lands over the 400-line
budget once table+map+CRUD UI is counted — plan for a chained PR (data/job → API → frontend) rather
than a single PR, confirmed at the tasks/apply gate.
