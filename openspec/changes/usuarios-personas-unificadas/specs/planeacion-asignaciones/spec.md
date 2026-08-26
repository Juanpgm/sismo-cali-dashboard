# Delta for Planeación — cruce Survey Cali ↔ API y asignación de levantamientos

Change: `usuarios-personas-unificadas` · Base: `openspec/changes/planeacion-asignaciones/specs/planeacion-asignaciones/spec.md`
(that change's spec is the current documented behavior for `web/js/planeacion.js`; not yet archived
into `openspec/specs/`, but the shipped code already matches it — see exploration §0, §5.)

## ADDED Requirements

### Requirement: Vehiculo modal assigns only an existing conductor
The vehiculo create/edit modal MUST offer an `empresa` free-text input and a `<select>` of existing
conductores to assign, and MUST NOT offer an inline "crear conductor" path. Creating a new
conductor MUST happen only through the Usuarios modal's `tipo='conductor'` fan-out.

#### Scenario: Vehiculo save with an existing conductor
- GIVEN an admin opens the vehiculo modal and selects an existing conductor plus an `empresa`
  value
- WHEN the vehiculo is saved
- THEN the vehiculo document stores `conductor_id` and `empresa`, and no `crearConductor` call is
  made

#### Scenario: No inline conductor-creation UI is reachable
- GIVEN the vehiculo modal is open
- WHEN the admin looks for a way to create a conductor without leaving the modal
- THEN no such control exists; only the existing-conductor selector is present

### Requirement: Inspector roster CRUD lives in Planeación
The system MUST render the inspector roster (list, create-inspector, enable/disable) as a segment
inside the Planeación tab, calling the same `api/stickers.js` actions via the existing
`callStickersApi` client, and MUST NOT fork or duplicate that client. The roster segment MUST NOT
exist inside the Stickers tab.

#### Scenario: Roster is usable from Planeación
- GIVEN an admin opens the Planeación tab's roster segment
- WHEN they create, list, or disable an inspector
- THEN the call goes through `callStickersApi` to `api/stickers.js`, and the change is reflected
  in the Planeación roster list

#### Scenario: Roster is absent from Stickers
- GIVEN an admin opens the Stickers tab
- WHEN the segmented control renders
- THEN it offers only Evaluaciones and Asignación; no Roster segment exists

## MODIFIED Requirements

### Requirement: Planeación UI — priority table, map, and correction affordances
The system MUST render the working set as a sortable, filterable table ordered by effective
priority and a Leaflet map with a legend distinguishing surveyed, high-priority pending, other
pending, assigned/in-progress, and excluded points. The system MUST provide controls to
auto-group, create a cuadrilla from a manual selection, assign an inspector to a cuadrilla
(group-only), correct a point's assignment, mark a point as not applicable with a reason, open or
copy the point's prefilled survey link, and manage the inspector roster (list/create/enable/
disable) in its own segment. The system MUST NOT render the individual cuadrilla-inspector
combobox (`asignarInspector` at the point level), the map per-point reassign select
(`reasignarPunto`), or the individual desasignar button (`desasignarInspector`) — group assignment
(`asignarGrupoAPuntos`) remains the only assignment path in the UI; their backend actions MUST
remain callable. When the working set is truncated the UI MUST say so.
(Previously: offered "assign and reassign an inspector" without distinguishing group vs.
individual controls, and did not mention roster management.)

#### Scenario: Table is ordered by priority by default
- GIVEN the table renders a mixed working set
- WHEN it first appears
- THEN rows are ordered by effective priority, highest first

#### Scenario: Filtering narrows the working set
- GIVEN the table contains points across several comunas and priorities
- WHEN the admin selects a `prioridad` and a `comuna` filter
- THEN only matching rows remain visible

#### Scenario: Map legend distinguishes the five states
- GIVEN points exist in each of the five legend states
- WHEN the map renders
- THEN each state's markers use its own legend colour and the legend identifies all five

#### Scenario: Truncation is surfaced to the operator
- GIVEN `listPuntos` returned `truncado:true`
- WHEN the view renders
- THEN it displays how many points are shown out of how many pending

#### Scenario: The survey link is reachable per point
- GIVEN an admin opens a point's row actions
- WHEN they choose the survey-link action
- THEN the prefilled link is fetched from the endpoint and offered to open or copy

#### Scenario: A correction updates the view without a full reload
- GIVEN an admin corrects a point's priority override from the table
- WHEN the call succeeds
- THEN the row re-renders with the new value without re-fetching the whole working set

#### Scenario: The inspector roster is available in a top-level tab
- GIVEN the admin opens Planeación without having opened the Stickers tab in this session
- WHEN an inspector selection control renders
- THEN it is populated with the enabled inspectors, because the module loads the roster itself

#### Scenario: No individual-assignment control is reachable
- GIVEN an admin views a cuadrilla or a map point
- WHEN they look for a per-point inspector combobox, a per-point reassign select, or an individual
  desasignar button
- THEN none of those controls exist; only group assignment on the cuadrilla is available

#### Scenario: Group assignment still works after hiding individual controls
- GIVEN a cuadrilla with member points
- WHEN an admin calls `asignarGrupoAPuntos` for that cuadrilla
- THEN every member point receives the assigned inspector, exactly as before this change

### Requirement: Scope boundaries
The system MUST NOT write to the `survey_cali` collection from any module this change adds — it
MUST NOT call the survey mutation core, and MUST NOT create a `survey_cali` history revision. The
system MUST NOT write to ArcGIS or Survey123 — no feature create, update, or `applyEdits` call.
The system MUST NOT reference the `dagma-85aad` project id, the `cruce_criticos_survey`
collection, the legacy `integracion_F1` credential resolution, or any dagma-related constant
anywhere under `backend/`. The system MUST NOT open a client-readable Firestore rule for either
new collection. The system MUST NOT allow an admin to edit the report's own pipeline-owned data
(address, coordinates, severity) through the assignment endpoint. Inspector-roster CRUD
(create/enable/disable) now legitimately lives in the Planeación tab's roster segment, calling
`api/stickers.js` — this is the one exception to the prior no-roster-CRUD boundary and MUST NOT be
duplicated into any new backend endpoint.
(Previously: additionally stated "MUST NOT add inspector-roster CRUD" with a scenario asserting no
such control exists in Planeación; superseded by the roster's relocation in this change.)

#### Scenario: `survey_cali` is never written
- GIVEN any action of the endpoint or any step of the cruce job
- WHEN it executes
- THEN no write targets `survey_cali` and no history revision is created

#### Scenario: ArcGIS is never written
- GIVEN any module this change adds
- WHEN it is inspected
- THEN it contains no ArcGIS feature-editing call; the only ArcGIS interaction is URL construction

#### Scenario: No dagma reference exists in the backend
- GIVEN the `backend/` tree after this change
- WHEN it is scanned for the dagma project id, the `cruce_criticos_survey` collection name, and
  the legacy credential env vars
- THEN there are zero matches

#### Scenario: Pipeline-owned report data is not editable by an admin
- GIVEN an admin sends `editarAsignacion` with a `direccion` or `coords` key
- WHEN the request is processed
- THEN the pipeline-owned field is not written

#### Scenario: Direct client Firestore read is denied
- GIVEN a browser client attempts to read `planeacion_puntos` or `planeacion_cuadrillas` directly
  via the Firestore client SDK
- WHEN the read is attempted
- THEN Firestore security rules deny it

#### Scenario: Inspector-roster CRUD calls the existing Stickers endpoint, not a new one
- GIVEN the Planeación roster segment creates or disables an inspector
- WHEN the request is inspected
- THEN it targets `api/stickers.js`, and no new FastAPI inspector-CRUD endpoint exists under
  `backend/app/routers/planeacion_asignaciones.py`
