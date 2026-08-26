# Delta for Field Form Session

Change: `fastapi-backend-consolidation` · Modified capability. Existing spec: `openspec/specs/field-form-session/spec.md`. This delta adds backend-repoint/CORS requirements without changing any existing session-retry/dedup-import requirement.

## ADDED Requirements

### Requirement: DASHBOARD_API Repoint

`formulario/js/form.js`'s `DASHBOARD_API` constant MUST repoint from `https://sismo-cali-dashboard.vercel.app` to the consolidated app's base URL only after the `inspector-asignaciones` parity slice is verified, following `backend-platform`'s cutover safety model (old endpoint stays live until verified).

#### Scenario: DASHBOARD_API repoint after parity verification

- GIVEN the consolidated `/inspector-asignaciones` route has passed parity checks
- WHEN `DASHBOARD_API` is updated
- THEN subsequent `inspector-asignaciones` calls target the consolidated app

#### Scenario: Old dashboard API stays live during transition

- GIVEN the `DASHBOARD_API` repoint has not yet shipped
- WHEN formulario calls `sismo-cali-dashboard.vercel.app`
- THEN it continues to serve `inspector-asignaciones` exactly as before

### Requirement: CORS Enabled For The formulario Origin

The consolidated app's CORS allowlist MUST include the `formulario-atc20-cali.vercel.app` origin (and its local dev origin), replacing the current per-route CORS-reflection hack.

#### Scenario: formulario origin receives CORS headers

- GIVEN a request `Origin` of `https://formulario-atc20-cali.vercel.app`
- WHEN `/inspector-asignaciones` or `/api/sign` is called cross-origin
- THEN the response includes `Access-Control-Allow-Origin` permitting that origin

### Requirement: Inspector Own-UID Scoping Preserved

`inspector-asignaciones` MUST continue to scope every read/write to `sticker_matches` by `inspector_uid == token.sub`, rejecting any request where the authenticated inspector targets a point assigned to a different `inspector_uid`.

#### Scenario: Cross-inspector access still rejected after migration

- GIVEN inspector A's Bearer token (`sub == uidA`)
- WHEN a `POST /inspector-asignaciones` request targets a point whose `inspector_uid` is `uidB`
- THEN the request is rejected and no `sticker_matches` write occurs

#### Scenario: Own-uid access still succeeds after migration

- GIVEN inspector A's Bearer token (`sub == uidA`) and a point with `inspector_uid == uidA`
- WHEN A calls `POST /inspector-asignaciones` to mark that point `hecho`
- THEN the write succeeds
