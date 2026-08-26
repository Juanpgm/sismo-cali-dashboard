# Planeación — bitácora de auditoría append-only Specification

Change: `planeacion-auditoria` · New capability (no prior spec exists for this domain).

## Purpose

An append-only audit log of every mutating change made through the `/planeacion-asignaciones`
dispatcher — grupos, vehículos, conductores, and asignaciones/cuadrillas — captured at the single
dispatch site, read back through an admin-only `listAuditoria` action, and shown as a chronological
feed in a new "Historial" sub-tab.

## Requirements

### Requirement: Append-only write on successful mutation
The system MUST write exactly one `planeacion_auditoria` document, after a mutation already
succeeded, for every action in the `MUTATING_ACTIONS` set (`crearGrupo`, `editarGrupo`,
`eliminarGrupo`, `asignarGrupoAPuntos`, `desasignarGrupo`, `crearVehiculo`, `editarVehiculo`,
`eliminarVehiculo`, `asignarVehiculoAGrupo`, `desasignarVehiculo`, `crearConductor`,
`editarConductor`, `eliminarConductor`, `crearCuadrilla`, `editarCuadrilla`, `eliminarCuadrilla`,
`autoAgrupar`, `reiniciarAgrupacion`, `asignarInspector`, `desasignarInspector`, `reasignarPunto`,
`editarAsignacion`, `marcarNoAplica`, `reopen`). The document MUST carry `actor_uid` (from
`claims.get("sub")`), `actor_email` (from `claims.get("email")`), `accion`, `entidad` (one of
`grupo|vehiculo|conductor|asignacion|cuadrilla`, derived per action), `entidad_id`, `params` (the
request body minus `action`), `resultado` (the returned new state), a human-readable `resumen`
(neutral Spanish, infinitive), and `ts`. Any action outside `MUTATING_ACTIONS` MUST write nothing.

#### Scenario: A mutating action writes one correct document
- GIVEN an admin calls `crearGrupo` with `{nombre: "Norte"}` and it succeeds
- WHEN the dispatcher returns the response
- THEN exactly one `planeacion_auditoria` document exists with `entidad:'grupo'`, the acting admin's
  `actor_uid`/`actor_email`, `accion:'crearGrupo'`, and `resumen:'Crear grupo Norte'`

#### Scenario: A read-only action writes nothing
- GIVEN an admin calls `listGrupos`
- WHEN the call completes
- THEN zero `planeacion_auditoria` documents are written

### Requirement: A logging failure never alters a completed mutation
The audit append MUST run strictly after the mutation's own writes have already succeeded. If the
audit write raises, the system MUST NOT roll back the mutation, MUST NOT return an error status for
it, and MUST return the mutation's success response unchanged.

#### Scenario: Audit write fails but the mutation still reports success
- GIVEN `asignarVehiculoAGrupo` has already committed its Firestore write
- WHEN the subsequent `planeacion_auditoria` append raises an exception
- THEN the endpoint still returns its normal 2xx success response for `asignarVehiculoAGrupo`,
  unchanged, and the exception is swallowed/logged, not surfaced to the caller

### Requirement: `listAuditoria` read action
The system MUST expose `listAuditoria` on the same admin-gated `/planeacion-asignaciones`
dispatcher (no new route), returning entries newest-first, filterable by `tipo` (entidad), `usuario`
(actor), and a date range, and paginated. A non-admin caller MUST be rejected with 403 and receive
no data.

#### Scenario: Results are ordered newest-first
- GIVEN entries created at different times
- WHEN `listAuditoria` is called with no filters
- THEN the returned entries are ordered by `ts` descending

#### Scenario: Filtering by tipo
- GIVEN entries across multiple `entidad` values
- WHEN `listAuditoria` is called with `tipo:'vehiculo'`
- THEN only entries with `entidad:'vehiculo'` are returned

#### Scenario: Filtering by usuario
- GIVEN entries from multiple actors
- WHEN `listAuditoria` is called with `usuario:'u9'`
- THEN only entries whose `actor_uid` is `u9` are returned

#### Scenario: Filtering by date range
- GIVEN entries spanning several days
- WHEN `listAuditoria` is called with a `desde`/`hasta` range
- THEN only entries with `ts` inside that range are returned

#### Scenario: Pagination bounds the result
- GIVEN more entries exist than one page
- WHEN `listAuditoria` is called with a page size
- THEN at most that many entries are returned, with a way to fetch the next page

#### Scenario: Non-admin call is rejected
- GIVEN a valid token belonging to a non-admin role
- WHEN `listAuditoria` is called
- THEN the request is rejected with 403 and no audit data is returned

### Requirement: Audit entries are immutable
The system MUST NOT expose any action that edits or deletes an existing `planeacion_auditoria`
document. `registrar(...)` only appends.

#### Scenario: No update or delete action exists
- GIVEN the full set of dispatcher actions
- WHEN it is inspected for `planeacion_auditoria`
- THEN no action updates or deletes an existing audit document

### Requirement: Sole-writer invariant
The system MUST enforce, by an automated repository scan, that the literal `planeacion_auditoria`
appears under `backend/app/` only inside `backend/app/services/planeacion_audit.py`.

#### Scenario: An unallowlisted write path fails the invariant
- GIVEN a module other than `planeacion_audit.py` references `planeacion_auditoria`
- WHEN the invariant test runs
- THEN it fails, naming the unexpected module

### Requirement: "Historial" sub-tab renders the feed and its filters
The system MUST add a "Historial" sub-tab in `web/js/planeacion.js`, sibling to Grupos / Vehículos /
Asignaciones, rendering the feed via `listAuditoria` with filter selects for tipo, usuario, and fecha.

#### Scenario: Feed renders and a filter narrows it
- GIVEN the admin opens the Historial sub-tab
- WHEN it loads and the admin selects a `tipo` filter
- THEN the feed re-fetches via `listAuditoria` and shows only matching entries
