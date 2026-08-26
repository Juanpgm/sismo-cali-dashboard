# User Management (Usuarios tab) Specification

## Purpose

A single, superset view of everyone with authentication access to the dashboard (password
admins, Google `@cali.gov.co` viewers, `@sismocali.gov.co` inspectors), with the last-known
activity metrics available today, filtering, and admin lifecycle actions — all authorized
server-side, independent of client-side tab visibility.

## Requirements

### Requirement: Tab visibility and server-side authorization
The system MUST render `#view-usuarios` for admin sessions and hide it via CSS for
`body[data-role="viewer"]`. The system MUST independently authorize every `api/usuarios.js`
action server-side (valid Firebase ID token + `sign_in_provider === 'password'`), regardless
of client-side tab visibility.

#### Scenario: Admin opens tab
- GIVEN an authenticated password-admin session
- WHEN the user opens the Usuarios tab
- THEN the tab renders the superset list

#### Scenario: Viewer cannot reach the tab or its API
- GIVEN a Google-authenticated viewer session
- WHEN the viewer's UI loads
- THEN the Usuarios tab is hidden by CSS
- AND a direct call to any `api/usuarios.js` action with the viewer's token is rejected

### Requirement: Superset user listing
The system MUST list every Firebase Auth user on `sismo-agosto-sgred` (password admins,
`google.com` `@cali.gov.co` viewers, `@sismocali.gov.co` inspectors) via `listUsers()`, each
row showing identity (email, uid, provider), `metadata.lastSignInTime`, and
`metadata.creationTime`. The system MUST NOT claim login/download counts in v1.

#### Scenario: List spans all three populations
- GIVEN admins, viewers, and inspectors exist in Firebase Auth
- WHEN the list action runs
- THEN the result includes rows from all three populations with last-sign-in and created dates

#### Scenario: listUsers(1000) ceiling
- GIVEN the combined user population exceeds 1000 accounts
- WHEN the list action runs
- THEN only the first page (up to 1000) is returned and the list is silently truncated (known
  ceiling; no pagination in v1)

### Requirement: Role, domain, and status filtering
The system MUST let an admin filter the superset by role/domain (admin / `@cali.gov.co`
viewer / `@sismocali.gov.co` inspector) and by status (enabled / disabled).

#### Scenario: Filter to disabled inspectors
- GIVEN the superset list is rendered
- WHEN the admin selects domain=inspector and status=disabled
- THEN only disabled `@sismocali.gov.co` rows remain visible

### Requirement: In-tab stat chips
The system MUST show total, active, and disabled counts, plus per-role counts, as chips in the
tab's section bar, computed from the current (unfiltered) superset.

#### Scenario: Chips reflect current data
- GIVEN the superset contains N total users with D disabled
- WHEN the tab renders
- THEN chips display N total and D disabled without requiring a filter to be applied

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

### Requirement: Per-tipo error isolation in the unified creation modal
The system MUST surface each tipo's create failure using that endpoint's own error payload, MUST
keep the modal open with the failing tipo still selected, and MUST NOT retry against or fall back
to a different endpoint.

#### Scenario: An inspector-create failure is scoped to that branch
- GIVEN `tipo='inspector'` and `createInspector` returns a duplicate-cedula error
- WHEN the error is shown
- THEN the modal displays that Stickers error text and takes no `apiUrl('usuarios')` or
  `apiUrl('planeacionAsignaciones')` action

### Requirement: Disable / enable user
The system MUST toggle both Auth `updateUser({disabled})` and the Firestore `activo` flag
together, since security rules gate on `activo`, not the Auth flag.

#### Scenario: Disable mirrors both stores
- GIVEN an enabled user
- WHEN an admin disables that user
- THEN Auth `disabled=true` AND Firestore `activo=false` are both set

#### Scenario: Disabled user's live token still works briefly
- GIVEN a user is disabled while holding a valid ID token
- WHEN that user calls a rules-gated action within ~1h of disable
- THEN the action may still succeed until the token expires or is refreshed (known latency,
  not a defect)

### Requirement: Delete user with anti-lockout guards
The system MUST delete the Auth user and the `inspectores/{uid}` Firestore profile, and MUST
leave `evaluaciones` records intact (historical inspection data, not account data). The system
MUST reject an admin's attempt to delete or disable their own account, and MUST reject deleting
the last remaining password-admin account.

#### Scenario: Successful delete
- GIVEN an admin deletes another non-last admin or a viewer/inspector
- WHEN the delete action runs
- THEN the Auth user and `inspectores/{uid}` are removed
- AND any `evaluaciones` records referencing that uid remain unchanged

#### Scenario: Self-delete blocked
- GIVEN an admin is authenticated as uid X
- WHEN that admin requests delete (or disable) of uid X
- THEN the request is rejected server-side and no state changes

#### Scenario: Last-admin delete blocked
- GIVEN exactly one password-admin account remains
- WHEN any caller requests deletion of that account
- THEN the request is rejected server-side and no state changes

### Requirement: Send password reset
The system MUST trigger Firebase client SDK `sendPasswordResetEmail` for a target user's email
from an authenticated admin session.

#### Scenario: Reset email requested
- GIVEN a valid target email in the superset
- WHEN the admin clicks "send reset"
- THEN `sendPasswordResetEmail` is invoked for that email and the admin sees a confirmation
