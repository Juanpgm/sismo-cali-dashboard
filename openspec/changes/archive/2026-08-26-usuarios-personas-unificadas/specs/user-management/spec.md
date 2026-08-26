# Delta for User Management (Usuarios tab)

Change: `usuarios-personas-unificadas` · Base: `openspec/specs/user-management/spec.md`

## MODIFIED Requirements

### Requirement: Create user
The system MUST render a `tipo` selector (`admin` | `viewer` | `usuario` | `inspector` |
`conductor`) in the Usuarios creation modal that swaps the field set and dispatches to the
endpoint owning that person kind. For `tipo` in {`admin`,`viewer`,`usuario`} the system MUST
create a password Auth account (Auth `createUser` + Firestore profile) via `apiUrl('usuarios')`,
rolling back the Auth user if the Firestore write fails. For `tipo='inspector'` the system MUST
submit cedula/nombre/entidad/password to `apiUrl('stickers')`'s existing `createInspector` action
(identical brigade-code allocation and Auth rollback as today's Stickers-tab flow). For
`tipo='conductor'` the system MUST submit cedula/nombre/email/telefono to
`apiUrl('planeacionAsignaciones')`'s `crearConductor` action, creating NO Auth account. Each tipo
MUST issue exactly one write call; a failure on one tipo's endpoint MUST NOT leave state on any
other endpoint.
(Previously: only handled password-admin creation via `apiUrl('usuarios')`, with no `tipo`
selector and no routing to other endpoints.)

#### Scenario: Successful create (admin/viewer/usuario)
- GIVEN valid email/password input and `tipo='admin'`
- WHEN the admin submits create
- THEN an Auth user and a matching Firestore profile exist and the row appears in the list

#### Scenario: Firestore write fails after Auth create
- GIVEN Auth `createUser` succeeds for `tipo='admin'`
- WHEN the Firestore profile write fails
- THEN the system deletes the just-created Auth user (rollback) and returns an error

#### Scenario: Inspector tipo creates via Stickers, with brigade code
- GIVEN `tipo='inspector'` with valid cedula/nombre/entidad/password
- WHEN the admin submits create
- THEN `apiUrl('stickers')` `createInspector` runs, an Auth account at
  `${cedula}@sismocali.gov.co` and a brigade code are allocated, and no `apiUrl('usuarios')` call
  is made

#### Scenario: Conductor tipo creates a data record, no login
- GIVEN `tipo='conductor'` with valid cedula/nombre/email/telefono
- WHEN the admin submits create
- THEN `apiUrl('planeacionAsignaciones')` `crearConductor` runs, a `conductores` document is
  created, and no Firebase Auth account is created

#### Scenario: A conductor-create failure does not touch other endpoints
- GIVEN `tipo='conductor'` and a duplicate cedula
- WHEN `crearConductor` fails
- THEN the modal shows that error inline, stays open on `tipo='conductor'`, and no call is made to
  `apiUrl('stickers')` or `apiUrl('usuarios')`

#### Scenario: @sismocali.gov.co is still rejected outside the inspector tipo
- GIVEN `tipo` is `admin`, `viewer`, or `usuario` and the typed email ends in
  `@sismocali.gov.co`
- WHEN the admin submits create
- THEN the request is rejected with a message naming the inspector tipo as the correct path

## ADDED Requirements

### Requirement: Per-tipo error isolation in the unified creation modal
The system MUST surface each tipo's create failure using that endpoint's own error payload, MUST
keep the modal open with the failing tipo still selected, and MUST NOT retry against or fall back
to a different endpoint.

#### Scenario: An inspector-create failure is scoped to that branch
- GIVEN `tipo='inspector'` and `createInspector` returns a duplicate-cedula error
- WHEN the error is shown
- THEN the modal displays that Stickers error text and takes no `apiUrl('usuarios')` or
  `apiUrl('planeacionAsignaciones')` action
