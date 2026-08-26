# Stickers — cruce y asignación Specification

## Purpose

A recurring, persisted process that determines which Panel points already carry a field sticker
(ATC-20 `evaluaciones`) versus which are still pending, surfaced as a sortable table and a
blue/red/amber Leaflet map inside the existing Stickers tab, with admin CRUD to group pending
points into `cuadrillas` (auto-by-proximity or manual) and assign/reassign them to inspectors —
without ever loading the full Panel dataset in the browser.

## Requirements

### Requirement: `sticker_matches` document ownership and merge safety
The system MUST store one `sticker_matches/{fuente}_{registro_id}` document per Panel point, split
into a pipeline-owned field group (`fuente`, `registro_id`, `tiene_sticker`, `tier`,
`sticker_dist_m`, `direccion`, `coords`, `zona_id`, `matched_at`) and an admin-owned field group
(`estado_asignacion`, `cuadrilla_id`, `inspector_uid`, `asignado_en`, `reasignado_de`). The doc id
MUST be derived deterministically from `fuente` + `registro_id` so re-running the pipeline updates
the same document instead of creating a duplicate. The pipeline MUST only ever write the
pipeline-owned field subset via a `merge:true` set, never a full-document `set()`, and MUST NOT
touch any admin-owned field on an existing document.

#### Scenario: Doc id is stable across re-runs
- GIVEN a Panel point with `fuente='ede'`, `registro_id='1234'` already has a `sticker_matches`
  document
- WHEN `cruce_sticker.py` runs again for the same point
- THEN the write targets doc id `ede_1234` (the same document), not a new document

#### Scenario: Pipeline re-run never clobbers admin-owned fields
- GIVEN a `sticker_matches` document has `estado_asignacion:'asignado'`, `cuadrilla_id:'c1'`,
  `inspector_uid:'u1'` set by the admin API
- WHEN `cruce_sticker.py` re-runs and re-writes the pipeline-owned fields for that same point
- THEN `estado_asignacion`, `cuadrilla_id`, `inspector_uid`, `asignado_en`, and `reasignado_de`
  remain unchanged after the write

#### Scenario: First write seeds a pending assignment state
- GIVEN no `sticker_matches` document exists yet for a given `fuente`/`registro_id`
- WHEN `cruce_sticker.py` writes that point for the first time
- THEN the resulting document has `estado_asignacion:'pendiente'`, `cuadrilla_id:null`,
  `inspector_uid:null`

### Requirement: `cruce_sticker.py` reuses the existing matching cascade
The system MUST implement the Panel↔`evaluaciones` matching in `integracion_F1/cruce_sticker.py`
by importing the cascade functions already proven in `integracion_F1/cruce_gestor.py` (`nearest`,
`match_by_direccion`, `build_addr_index`, `addr_key`, `_eval_latlon`), and MUST NOT reimplement or
fork that matching logic. Writes to `sticker_matches` MUST be batched (≤500 operations per Firestore
batch, following `subir_cruce_firebase.py`'s pattern). The job MUST provide an offline `--check`
self-test that runs without live Firestore/network access.

#### Scenario: Matching logic lives in one place
- GIVEN `cruce_sticker.py` needs to decide `tiene_sticker` for a Panel point
- WHEN the matching cascade runs
- THEN it calls into `cruce_gestor.py`'s existing functions rather than a separate reimplementation

#### Scenario: Batched writes respect the Firestore limit
- GIVEN a job run produces more than 500 point updates
- WHEN the job writes to `sticker_matches`
- THEN the writes are split into batches of at most 500 operations each

#### Scenario: `--check` passes offline
- GIVEN no network or live Firestore credentials are available
- WHEN `cruce_sticker.py --check` runs
- THEN it completes successfully using fixture data, asserting merge-safety (admin fields untouched)
  and first-write seeding (`estado_asignacion:'pendiente'`)

### Requirement: `cuadrillas` document shape
The system MUST store one `cuadrillas/{id}` document per group of pending points, carrying
`puntos` (array of point ids), `inspector_uid` (nullable), and `origen` (`'auto'` or `'manual'`).
Membership of a point in a cuadrilla MUST be reflected on the point's `sticker_matches` document
via `cuadrilla_id`.

#### Scenario: Cuadrilla creation sets membership on member points
- GIVEN a new cuadrilla is created (auto or manual) containing points P1, P2, P3
- WHEN the `cuadrillas` document is written
- THEN `sticker_matches/P1`, `P2`, `P3` each have `cuadrilla_id` set to the new cuadrilla's id

### Requirement: `api/sticker-asignaciones.js` is admin-only
The system MUST expose every action (`listPuntos`, `listCuadrillas`, `autoAgrupar`,
`crearCuadrilla`, `editarCuadrilla`, `asignarInspector`, `reasignarPunto`, `eliminarCuadrilla`)
behind a single POST-only endpoint that verifies a Firebase ID token and rejects any caller whose
role is not admin, mirroring `api/stickers.js`'s auth preamble.

#### Scenario: Non-admin call is rejected
- GIVEN a valid Firebase ID token belonging to a viewer or inspector (non-admin) account
- WHEN that token is used to call any `api/sticker-asignaciones.js` action
- THEN the request is rejected server-side and no Firestore state changes

### Requirement: `listPuntos` returns lean point data without loading full Panel
The system MUST let an admin list all `sticker_matches` documents (both pipeline-owned and
admin-owned fields) without reading `inspections.json` or `puntos_israel_cali.json`.

#### Scenario: List reflects current match and assignment state
- GIVEN `sticker_matches` contains matched and pending points, some already assigned
- WHEN `listPuntos` is called
- THEN the response includes every point's `tiene_sticker`, `estado_asignacion`, `cuadrilla_id`,
  and `inspector_uid`, without any read of the full Panel dataset

### Requirement: `listCuadrillas` returns current groups
The system MUST let an admin list all `cuadrillas` documents.

#### Scenario: List reflects auto and manual groups
- GIVEN `cuadrillas` contains both `origen:'auto'` and `origen:'manual'` documents
- WHEN `listCuadrillas` is called
- THEN the response includes all cuadrillas with their `puntos`, `inspector_uid`, and `origen`

### Requirement: `autoAgrupar` clusters pending points deterministically
The system MUST cluster `pendiente` points that have no `cuadrilla_id` using a deterministic
greedy nearest-neighbor pass (stable sort order, no RNG, no k-means), respecting a `maxRadiusM`
and `maxSize` cap per group. `autoAgrupar` MUST create new `cuadrillas` documents with
`origen:'auto'` and `inspector_uid:null`, and MUST NOT change any point's `estado_asignacion`
(grouping and assigning are separate actions).

#### Scenario: Same input twice produces the same groups
- GIVEN an unchanged set of pending, ungrouped points
- WHEN `autoAgrupar` is called twice in succession with the same parameters
- THEN both calls produce groups with identical membership

#### Scenario: Group size respects the cap
- GIVEN more than `maxSize` pending points fall within `maxRadiusM` of a seed point
- WHEN `autoAgrupar` runs
- THEN no resulting group contains more than `maxSize` points

#### Scenario: Group radius respects the cap
- GIVEN a candidate point lies farther than `maxRadiusM` from every group seed
- WHEN `autoAgrupar` runs
- THEN that point is not added to any group formed from those seeds (it becomes its own seed or
  remains ungrouped if no other point is within range)

#### Scenario: Auto-agrupar on an empty pending set is a no-op
- GIVEN no `pendiente` points without a `cuadrilla_id` exist
- WHEN `autoAgrupar` is called
- THEN it returns successfully with zero new `cuadrillas` created and no error

#### Scenario: Auto-agrupar does not assign an inspector
- GIVEN `autoAgrupar` creates a new cuadrilla from three pending points
- WHEN the resulting `cuadrillas` document and member points are inspected
- THEN `inspector_uid` is `null` on the cuadrilla and `estado_asignacion` remains `'pendiente'` on
  every member point

### Requirement: `crearCuadrilla` supports manual grouping
The system MUST let an admin create a cuadrilla manually from an explicit list of point ids, with
`origen:'manual'`.

#### Scenario: Manual creation from selected points
- GIVEN an admin selects a set of pending point ids via the UI
- WHEN `crearCuadrilla` is called with `{nombre, puntos}`
- THEN a new `cuadrillas` document is created with `origen:'manual'` and every listed point's
  `cuadrilla_id` is set to the new id

### Requirement: `editarCuadrilla` supports adding/removing points
The system MUST let an admin add or remove points from an existing cuadrilla, keeping each point's
`cuadrilla_id` consistent with membership.

#### Scenario: Removing a point clears its membership
- GIVEN a point P is a member of cuadrilla C
- WHEN `editarCuadrilla` removes P from C
- THEN P's `sticker_matches` document has `cuadrilla_id:null` and C's `puntos` no longer includes P

#### Scenario: Editing a cuadrilla that does not exist
- GIVEN a `cuadrilla_id` that has no matching `cuadrillas` document
- WHEN `editarCuadrilla` is called with that id
- THEN the call fails with an error and no point documents are modified

### Requirement: `asignarInspector` propagates to every point in a cuadrilla
The system MUST let an admin assign an inspector to a cuadrilla, propagating `inspector_uid`,
`asignado_en`, and `estado_asignacion:'asignado'` to every point currently in that cuadrilla.

#### Scenario: Assigning an inspector updates every member point
- GIVEN a cuadrilla with three member points, none yet assigned
- WHEN `asignarInspector` is called with `{cuadrilla_id, inspector_uid}`
- THEN all three points have `inspector_uid` set to the given uid, `estado_asignacion:'asignado'`,
  and a non-null `asignado_en`

### Requirement: `reasignarPunto` reassigns a single point with a breadcrumb
The system MUST let an admin reassign a single point to a different inspector, recording the
previous inspector uid in `reasignado_de` and updating `inspector_uid` to the new value —
independent of the point's current cuadrilla membership.

#### Scenario: Reassigning a point mid-cuadrilla
- GIVEN a point P is a member of cuadrilla C and currently assigned to inspector A
- WHEN `reasignarPunto` is called with `{punto_id: P, nuevo_inspector_uid: B}`
- THEN P's `inspector_uid` becomes B, `reasignado_de` becomes A, and P's `cuadrilla_id` is
  unchanged (P remains a member of C)

#### Scenario: Reassigning an unassigned point
- GIVEN a point P has `inspector_uid:null`
- WHEN `reasignarPunto` is called with `{punto_id: P, nuevo_inspector_uid: B}`
- THEN P's `inspector_uid` becomes B and `reasignado_de` becomes `null` (there was no prior
  inspector)

### Requirement: `eliminarCuadrilla` clears membership before deleting
The system MUST clear `cuadrilla_id` and `inspector_uid` on every member point before deleting a
`cuadrillas` document, so no point is left referencing a nonexistent cuadrilla.

#### Scenario: Deleting a cuadrilla releases its points
- GIVEN a cuadrilla C has member points P1, P2 with `inspector_uid` set
- WHEN `eliminarCuadrilla` is called with C's id
- THEN P1 and P2 have `cuadrilla_id:null` and `inspector_uid:null`, and the `cuadrillas/{C}`
  document no longer exists

### Requirement: Table view — sortable, filterable by `estado_asignacion`
The system MUST render a table of `sticker_matches` points (columns: dirección, zona,
estado_asignacion, cuadrilla, inspector, tier) inside the Asignación sub-section, sortable by
clicking a column header, and filterable by `estado_asignacion` via filter chips.

#### Scenario: Sorting by column header
- GIVEN the table is rendered with unsorted rows
- WHEN the admin clicks the "dirección" column header
- THEN rows re-render sorted by `direccion`, ascending

#### Scenario: Filtering to a single estado
- GIVEN the table contains points in every `estado_asignacion` value
- WHEN the admin selects the `pendiente` filter chip
- THEN only rows with `estado_asignacion:'pendiente'` remain visible

### Requirement: Map view — 3-color legend
The system MUST render a Leaflet map with one circle marker per point, colored blue when
`tiene_sticker === true`, red when `estado_asignacion === 'pendiente'`, and amber when
`estado_asignacion` is `'asignado'` or `'en_proceso'`, with a legend identifying the three colors.

#### Scenario: Matched point renders blue
- GIVEN a point has `tiene_sticker:true`
- WHEN the map renders
- THEN that point's marker is blue

#### Scenario: Pending, unassigned point renders red
- GIVEN a point has `tiene_sticker:false` and `estado_asignacion:'pendiente'`
- WHEN the map renders
- THEN that point's marker is red

#### Scenario: Assigned-but-unvisited point renders amber, distinct from pending
- GIVEN a point has `estado_asignacion:'asignado'`
- WHEN the map renders
- THEN that point's marker is amber, not red, so it is visually distinguishable from untouched
  pending points

### Requirement: CRUD affordances in the frontend
The system MUST provide an "Auto-agrupar" button that calls `autoAgrupar`, a manual multi-select
(table or map) with a "Crear cuadrilla" action that calls `crearCuadrilla`, and an
assign/reassign inspector control — populated by fetching the `inspectores` roster itself via the
existing `callStickersApi`/`getInspectores` client, since the Roster segment that used to preload
it no longer lives in the Stickers tab — that calls `asignarInspector` or `reasignarPunto`.

#### Scenario: Auto-agrupar button triggers clustering
- GIVEN the admin is viewing the Asignación sub-section
- WHEN the admin clicks "Auto-agrupar"
- THEN `autoAgrupar` is called and the table/map refresh to show the new cuadrillas

#### Scenario: Manual multi-select creates a cuadrilla
- GIVEN the admin selects several pending points via checkboxes
- WHEN the admin triggers "Crear cuadrilla" from the selection
- THEN `crearCuadrilla` is called with the selected point ids

#### Scenario: Inspector dropdown fetches its own roster copy
- GIVEN the Stickers tab no longer has a Roster segment
- WHEN the Asignación sub-section renders an inspector `<select>`
- THEN it fetches the `inspectores` roster itself via `callStickersApi` and populates the select
  from that response

### Requirement: Mounted as a sub-section of the existing Stickers tab
The system MUST mount the Asignación view as a sub-section inside `#view-stickers` (a 2-way
segmented control: Evaluaciones and Asignación — the Roster segment has moved to Planeación), and
MUST NOT add a new top-level `.view-tabs` entry. The sub-section's frontend module MUST
lazy-initialize on the first time the Asignación segment is opened, not on Stickers-tab open.

#### Scenario: No new top-level tab appears
- GIVEN the dashboard's top-level view tabs
- WHEN the Asignación feature ships
- THEN the top-level tab list is unchanged; Asignación is reachable only via a segment inside the
  Stickers tab

#### Scenario: Lazy init on first Asignación open
- GIVEN an admin opens the Stickers tab and stays on the Evaluaciones segment
- WHEN the admin has not yet opened the Asignación segment
- THEN `initStickersAsignacion` has not run and no `listPuntos`/`listCuadrillas` calls have been
  made

#### Scenario: Init runs once on first Asignación open
- GIVEN an admin opens the Asignación segment for the first time in a session
- WHEN the segment becomes visible
- THEN `initStickersAsignacion` runs exactly once, fetching `listPuntos` and `listCuadrillas`;
  subsequent segment re-opens in the same session call `reload()` instead of re-initializing

#### Scenario: Segmented control is 2-way, not 3-way
- GIVEN an admin opens the Stickers tab
- WHEN the segmented control renders
- THEN it offers exactly Evaluaciones and Asignación, with no Roster option

### Requirement: Scope boundaries
The system MUST NOT write to the `evaluaciones` collection from any part of this change (read-only
access). The system MUST NOT add inspector-roster CRUD inside the Stickers tab — the Roster
segment has moved to Planeación (see `usuarios-personas-unificadas`), and the Asignación
sub-section only reads the existing `inspectores` roster (fetched itself, per CRUD affordances) to
populate assignment controls. The system MUST NOT open a public Firestore read rule for
`sticker_matches` or `cuadrillas` — both collections are reachable only through
`api/sticker-asignaciones.js` (admin-SDK), never via a client-direct Firestore read.

#### Scenario: Evaluaciones collection is never written
- GIVEN any action in `api/sticker-asignaciones.js` or `cruce_sticker.py`
- WHEN that action executes
- THEN no write operation targets the `evaluaciones` collection

#### Scenario: No inspector CRUD surface remains in Stickers
- GIVEN the Stickers tab's Asignación sub-section
- WHEN the admin looks for a way to create, edit, or delete an inspector account
- THEN no such control exists anywhere in the Stickers tab; inspector CRUD now lives exclusively
  in Planeación's roster segment

#### Scenario: Direct client Firestore read is rejected
- GIVEN a browser client attempts to read `sticker_matches` or `cuadrillas` directly via the
  Firestore client SDK (bypassing `api/sticker-asignaciones.js`)
- WHEN the read is attempted
- THEN Firestore security rules deny it (admin-SDK-only access, no public read rule)
