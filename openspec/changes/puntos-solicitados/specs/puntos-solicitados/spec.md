# Puntos Solicitados Specification

Change: `puntos-solicitados` · New capability (no prior spec exists for this domain).

## Purpose

Admin-only registration of special-case points (citizen requests, follow-ups, priority cases) that
flow through the existing grupo/cuadrilla/inspector assignment machinery, codigoapp minting, and
Survey123/formulario rendering with zero new assignment code.

## Requirements

### Requirement: Admin-only creation with required-field validation

The system MUST require `nombre`, `comuna_corregimiento`, `barrio_vereda`, coordinates,
`nombre_solicitante`, `telefono_solicitante`, and `justificacion` to create a punto solicitado. Up to
10 photos MAY be attached; none are required. A non-admin/superadmin caller MUST be rejected with 403
for create, edit, and delete, with no document written.

#### Scenario: Missing a required field is rejected
- GIVEN an admin submits the create form without `justificacion`
- WHEN validated
- THEN it is rejected and no document is written in either collection

#### Scenario: All required fields present, no photos
- GIVEN an admin submits all required fields with zero photos
- WHEN validated
- THEN it is accepted

#### Scenario: Non-admin create/edit/delete is rejected
- GIVEN a caller whose token lacks admin/superadmin claims
- WHEN they call create, edit, or delete on `puntos_solicitados`
- THEN it is rejected with 403 and nothing is written, modified, or deleted

### Requirement: Atomic dual-write to `puntos_solicitados` and the `planeacion_puntos` mirror

The system MUST create the `puntos_solicitados` document and its `planeacion_puntos` mirror
(`fuente='solicitado'`, `registro_id`) in a single atomic operation: both documents MUST exist after
a successful create, and neither MUST exist after a failed one.

#### Scenario: Successful create writes both documents
- GIVEN a valid create request
- WHEN the write completes successfully
- THEN both the `puntos_solicitados` document and the `planeacion_puntos` mirror exist, and the point
  carries a minted `codigoapp`

#### Scenario: A simulated write failure leaves no orphan
- GIVEN the underlying batched write raises before committing
- WHEN the create operation fails
- THEN neither the `puntos_solicitados` document nor the `planeacion_puntos` mirror exists

### Requirement: Live geocoding with manual fallback

The system MUST expose a geocoding action that, given a typed address, returns coordinates when the
result is high-confidence (`ROOFTOP` or `RANGE_INTERPOLATED`) and located within the Cali bounding
box, and otherwise returns a no-coordinates response so the caller falls back to a draggable-marker
or manually entered lat/lng. The Google Maps API key MUST NOT appear in any client-facing response.

#### Scenario: High-confidence address is accepted
- GIVEN a typed address that geocodes to `ROOFTOP` inside the Cali bbox
- WHEN geocoding is requested
- THEN coordinates are returned and accepted

#### Scenario: Low-confidence result falls back to manual placement
- GIVEN a typed address that geocodes to `APPROXIMATE`, or outside the Cali bbox, or with no result
- WHEN geocoding is requested
- THEN no coordinates are returned, and the caller MUST be able to place/drag a marker or enter
  lat/lng manually to complete the point

#### Scenario: Manual coordinate entry as an alternative to geocoding
- GIVEN an admin who never calls geocoding
- WHEN they enter lat/lng directly and submit
- THEN the point is created with those manually entered coordinates

### Requirement: Mirrored point is assignable through existing machinery

A punto solicitado's mirror MUST be assignable to grupo, cuadrilla, and inspector, and MUST mint
`codigoapp`, through the existing `planeacion_asignaciones` endpoints only. No new assignment code
path MUST exist for solicited points.

#### Scenario: A solicited point is assigned exactly like a pipeline point
- GIVEN a punto solicitado mirror and a pipeline-sourced point, both `pendiente`
- WHEN both are assigned to the same grupo/cuadrilla/inspector via `planeacion_asignaciones`
- THEN both transition to `asignado` through the identical endpoint and code path, and both carry a
  `codigoapp`

### Requirement: `estado_seguimiento` tracks the mirror's assignment lifecycle

The tab MUST display `estado_seguimiento` as derived from the mirror's `estado_asignacion`
(`pendiente→pendiente`, `asignado→asignado`, `en_proceso→en_proceso`, `hecho→visitado`), driven only
by the existing assignment/completion endpoints, never by a separately stored, independently updated
field.

#### Scenario: Status advances as the mirror advances
- GIVEN a punto solicitado whose mirror starts at `estado_asignacion:'pendiente'`
- WHEN the mirror is assigned, then marked in-progress, then marked done via the existing endpoints
- THEN the tab shows `estado_seguimiento` as `asignado`, then `en_proceso`, then `visitado`, at each
  step, with no direct write to `estado_seguimiento` from any of those endpoints

### Requirement: Planeación cluster-creation rename is copy-only

Renaming the "Auto-agrupar" button to "Crear Cluster" MUST NOT change the underlying `autoAgrupar`
action name or its request/response contract.

#### Scenario: Renamed button still dispatches the unchanged action
- GIVEN the button now reads "Crear Cluster"
- WHEN it is clicked
- THEN it dispatches `action:'autoAgrupar'` with the same contract as before the rename, and grouping
  behavior is unchanged

