# Delta for Stickers — cruce y asignación

Change: `stickers-tab-asignacion-removed` · Base: `openspec/specs/stickers-asignacion/spec.md`

## REMOVED Requirements

### Requirement: CRUD affordances in the frontend
**Reason**: The Stickers-tab Asignación segment that hosted the "Auto-agrupar"/"Crear cuadrilla"/
assign-inspector controls was removed from `web/js/stickers.js` — the tab now shows Evaluaciones
ATC-20 (Atención Sismo) directly, with no sub-sections.
**Migration**: None. `api/sticker-asignaciones.js` (the endpoint these controls called) stays
deployed but is now uncalled by any frontend — a separate, later decision if it should be
decommissioned too.

### Requirement: Mounted as a sub-section of the existing Stickers tab
**Reason**: Same removal — there is no longer a 2-way (or any-way) segmented control in the
Stickers tab. `web/js/stickers-asignacion.js` and its test were deleted; nothing imports or mounts
an Asignación view anywhere.
**Migration**: None.

### Requirement: Table view — sortable, filterable by `estado_asignacion`
**Reason**: Described the table rendered "inside the Asignación sub-section" — that sub-section is
gone, so is the table.
**Migration**: None.

### Requirement: Map view — 3-color legend
**Reason**: Same removal — the blue/red/amber Leaflet map lived in the same removed Asignación
sub-section.
**Migration**: None.

## MODIFIED Requirements

### Requirement: Scope boundaries
The system MUST NOT write to the `evaluaciones` collection from any part of this change (read-only
access). The system MUST NOT open a public Firestore read rule for `sticker_matches` or
`cuadrillas` — both collections are reachable only through `api/sticker-asignaciones.js`
(admin-SDK), never via a client-direct Firestore read. No frontend currently calls this endpoint —
the Stickers tab's Asignación UI was removed entirely (see REMOVED Requirements above), and
Planeación's own auto-agrupar/crearCuadrilla/asignarInspector affordances (`web/js/planeacion.js`)
are a structurally similar but functionally separate CRUD surface: they call the
`planeacionAsignaciones` endpoint against the Survey Cali/EDAN domain, not `sticker_matches`/
`cuadrillas`.

#### Scenario: Evaluaciones collection is never written
- GIVEN any action in `api/sticker-asignaciones.js` or `cruce_sticker.py`
- WHEN that action executes
- THEN no write operation targets the `evaluaciones` collection

#### Scenario: No Asignación CRUD surface remains anywhere in the frontend
- GIVEN the Stickers tab
- WHEN an admin looks for any cuadrilla/assignment control (auto-agrupar, crear cuadrilla,
  assign/reassign inspector)
- THEN none exists anywhere in the app's frontend; `api/sticker-asignaciones.js` remains deployed
  but has no caller

#### Scenario: Direct client Firestore read is rejected
- GIVEN a browser client attempts to read `sticker_matches` or `cuadrillas` directly via the
  Firestore client SDK (bypassing `api/sticker-asignaciones.js`)
- WHEN the read is attempted
- THEN Firestore security rules deny it (admin-SDK-only access, no public read rule)
