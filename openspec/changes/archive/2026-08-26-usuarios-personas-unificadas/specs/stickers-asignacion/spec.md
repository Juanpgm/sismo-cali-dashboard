# Delta for Stickers — cruce y asignación

Change: `usuarios-personas-unificadas` · Base: `openspec/changes/stickers-asignacion/specs/stickers-asignacion/spec.md`
(that change's spec is the current documented behavior for `web/js/stickers.js`; not yet archived
into `openspec/specs/`, but the shipped code already matches it.)

## MODIFIED Requirements

### Requirement: Mounted as a sub-section of the existing Stickers tab
The system MUST mount the Asignación view as a sub-section inside `#view-stickers` (a 2-way
segmented control: Evaluaciones and Asignación — the Roster segment has moved to Planeación), and
MUST NOT add a new top-level `.view-tabs` entry. The sub-section's frontend module MUST
lazy-initialize on the first time the Asignación segment is opened, not on Stickers-tab open.
(Previously: the segmented control was 3-way — Roster, Evaluaciones, Asignación; Roster has
relocated to Planeación in `usuarios-personas-unificadas`.)

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

### Requirement: CRUD affordances in the frontend
The system MUST provide an "Auto-agrupar" button that calls `autoAgrupar`, a manual multi-select
(table or map) with a "Crear cuadrilla" action that calls `crearCuadrilla`, and an
assign/reassign inspector control — populated by fetching the `inspectores` roster itself via the
existing `callStickersApi`/`getInspectores` client, since the Roster segment that used to preload
it no longer lives in the Stickers tab — that calls `asignarInspector` or `reasignarPunto`.
(Previously: reused a roster already loaded by the Stickers tab's own Roster segment, with no new
fetch; that segment has moved to Planeación, so Asignación now fetches independently.)

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

### Requirement: Scope boundaries
The system MUST NOT write to the `evaluaciones` collection from any part of this change (read-only
access). The system MUST NOT add inspector-roster CRUD inside the Stickers tab — the Roster
segment has moved to Planeación (see `usuarios-personas-unificadas`), and the Asignación
sub-section only reads the existing `inspectores` roster (fetched itself, per CRUD affordances) to
populate assignment controls. The system MUST NOT open a public Firestore read rule for
`sticker_matches` or `cuadrillas` — both collections are reachable only through
`api/sticker-asignaciones.js` (admin-SDK), never via a client-direct Firestore read.
(Previously: stated inspector CRUD "remains exclusively in the existing Roster segment" of the
Stickers tab; that segment has relocated to Planeación.)

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
