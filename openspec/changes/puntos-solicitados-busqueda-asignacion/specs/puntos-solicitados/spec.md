# Delta for Puntos Solicitados

## ADDED Requirements

### Requirement: Admin-only search over existing atencionsismo reports

The system MUST expose an admin-only search that matches existing atencionsismo-reported incidents
by `dirección`, `barrio`, `comuna`, OR `nombre del solicitante`, and MUST reject callers without
admin/superadmin claims with 403 and no results. Name search MUST be performed by filtering the
existing private `puntos_contacto` collection server-side and in-memory (no new index, no new
storage artifact).

#### Scenario: Search by address, barrio, or comuna
- GIVEN an admin types a partial address, barrio, or comuna in the searchbar
- WHEN the search runs
- THEN matching atencionsismo reports are returned with the matched field(s) indicated

#### Scenario: Search by solicitante name
- GIVEN an admin types a requester's name in the searchbar
- WHEN the search runs
- THEN the system filters the existing `puntos_contacto` collection in memory and returns matching
  reports, without creating or querying any new stored index

#### Scenario: Non-admin search is rejected
- GIVEN a caller whose token lacks admin/superadmin claims
- WHEN they call the search endpoint
- THEN the request is rejected with 403 and no results are returned

### Requirement: Search result selection prefills the create form

Selecting a search result MUST open the existing "crear punto" form prefilled with that result's
known fields (address, barrio, comuna, requester name, coordinates when available). If no result is
selected or none match, the admin MUST still be able to open the create form prefilled with whatever
free text was typed in the searchbar.

#### Scenario: Selecting a result prefills the create form
- GIVEN a list of search results is shown
- WHEN the admin selects one
- THEN the create modal opens with that result's known fields pre-populated

#### Scenario: No match still allows manual creation
- GIVEN a search returns zero results
- WHEN the admin proceeds to create anyway
- THEN the create modal opens prefilled with the typed search text and no other data

### Requirement: Search results and contact data MUST NOT leak to non-admins or public artifacts

Search results and the underlying `puntos_contacto` data they draw from MUST never be exposed to
non-admin roles and MUST NOT be written to any public or client-fetchable artifact (git, Blob,
`reportes.json`, or equivalent).

#### Scenario: Public artifacts remain PII-free
- GIVEN the search feature is in use
- WHEN any public/client-fetchable artifact is inspected
- THEN it contains no requester name, phone, or other `puntos_contacto` field

#### Scenario: A non-admin session cannot retrieve contact data via search
- GIVEN a non-admin/superadmin authenticated session
- WHEN it attempts to call the search endpoint directly
- THEN it receives 403 and no contact data is returned

### Requirement: Card-level assignment action

Every point in the puntos solicitados list view MUST show a visible "Asignar" action that assigns
without requiring the detail modal to be opened, and MUST reuse the existing assignment mechanism
(`asignarInspector`/`planeacion_asignaciones`) with no new backend assignment endpoint.

#### Scenario: Assigning from the list view
- GIVEN a pendiente point in the list view
- WHEN the admin uses its card-level "Asignar" action to pick grupo/cuadrilla/inspector
- THEN the assignment is applied through the existing assignment endpoint, identical to assigning
  from the detail modal, without the modal ever opening

### Requirement: xlsx export

The tab MUST provide a working xlsx export button with parity to the evaluaciones tab's download.

#### Scenario: Exporting the list
- GIVEN the puntos solicitados list is loaded
- WHEN the admin clicks the export button
- THEN an xlsx file downloads containing the currently listed points

### Requirement: Inspector selection shows active-assignment load

The inspector picker used for assignment MUST display each inspector's current count of active
assignments alongside their name.

#### Scenario: Inspector option shows load count
- GIVEN the inspector picker is opened for an assignment
- WHEN options are rendered
- THEN each inspector's option shows their current active-assignment count

### Requirement: Busy state feedback on create/geocode actions

The "crear punto" and geocode actions MUST show a spinner while the request is in flight, in addition
to (not instead of) any text change, and MUST disable the action to prevent duplicate submission.

#### Scenario: Geocode button shows a spinner while busy
- GIVEN the admin clicks the geocode action
- WHEN the request is in flight
- THEN the button shows a spinner and is disabled until the response arrives

#### Scenario: Create button shows a spinner while busy
- GIVEN the admin submits the create form
- WHEN the request is in flight
- THEN the button shows a spinner and is disabled until the response arrives
