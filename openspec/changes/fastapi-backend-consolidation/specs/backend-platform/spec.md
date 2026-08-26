# Backend Platform Specification

Change: `fastapi-backend-consolidation` · New capability (no prior spec exists — this is the target-state contract for the consolidated FastAPI app).

## Purpose

One FastAPI application, git-connected on Railway, serving every legacy `api/*.js` route plus `/api/sign`, with the exact auth matrix, caching behavior, and sole-writer invariants preserved from today's Vercel functions.

## Requirements

### Requirement: Route Parity Across Consolidated Endpoints

The system MUST expose every legacy endpoint at method/auth-parity, preserving each route's response contract exactly:

| Route | Method | Auth level |
|---|---|---|
| `/refresh` | POST | Bearer + `admin` (triggers only `dashboard-refresh`; the legacy fail-soft `cruce-gestion` redeploy is NOT ported — dagma is out of scope) |
| `/reportados` | GET | none (public) |
| `/stickers` | POST | Bearer + `admin` |
| `/sticker-status` | GET | Bearer, any authenticated role |
| `/sticker-asignaciones` | POST | Bearer + `admin` |
| `/inspector-asignaciones` | POST | Bearer, any authenticated role, scoped to `inspector_uid == token.sub` |
| `/usuarios` | POST | Bearer + `admin`, plus the acting admin's provider MUST be `password` and email MUST NOT be under `@sismocali.gov.co` |
| `/source-status` | GET | Bearer + `admin` |
| `/sign` | POST | Bearer, any authenticated role (see `inspection-photo-capture` delta) |

#### Scenario: Public route requires no token

- GIVEN no `Authorization` header is present
- WHEN `GET /reportados` is called
- THEN the request succeeds (200), matching today's unauthenticated `api/reportados.js`

#### Scenario: Admin-gated route rejects non-admin

- GIVEN a valid Bearer token whose `roleFrom` resolution is not `admin`
- WHEN any of `/refresh`, `/stickers`, `/sticker-asignaciones`, `/source-status` is called
- THEN the request is rejected and no Firestore mutation occurs

#### Scenario: Any-authenticated role-wide route accepts every valid role

- GIVEN a valid Bearer token of any resolved role
- WHEN `GET /sticker-status` is called
- THEN the request succeeds regardless of the specific role

#### Scenario: Own-uid-scoped route rejects cross-uid access

- GIVEN a valid Bearer token for inspector A (`token.sub == uidA`)
- WHEN `POST /inspector-asignaciones` targets a `sticker_matches` doc whose `inspector_uid` is `uidB`
- THEN the request is rejected and no write occurs

#### Scenario: usuarios endpoint enforces its extra provider/domain gate

- GIVEN an `admin`-role token whose provider is `password` and email is under `@sismocali.gov.co`
- WHEN `POST /usuarios` is called
- THEN the request is rejected, matching `api/usuarios.js`'s extra gate verbatim

#### Scenario: /refresh triggers only dashboard-refresh

- GIVEN an admin calls `POST /refresh`
- WHEN the request is processed
- THEN only the `dashboard-refresh` Railway service is redeployed/triggered; no `cruce-gestion` redeploy is attempted (`cruce-gestion` is excluded from migration and dagma is out of scope)

### Requirement: Ported Auth Verifier And Role Resolution

The system MUST implement Firebase ID token verification equivalent to `verifyFirebaseToken` (RS256 against Google's rotating certs, no `firebase-admin` needed) and `roleFrom` with precedence, in order: `SUPERADMIN_EMAIL` → claim `role` → `@sismocali.gov.co` domain → `password` provider → `google.com` + `@cali.gov.co`.

#### Scenario: Precedence order resolves to the earliest matching rule

- GIVEN a token whose email matches `SUPERADMIN_EMAIL` and also carries claim `role:'viewer'`
- WHEN `roleFrom` evaluates the token
- THEN the resolved role is `admin` (the email match wins over the claim)

#### Scenario: Parity suite passes identically to the JS test matrix

- GIVEN the ported JS `roleFrom`/`verifyFirebaseToken` precedence test matrix as fixture inputs
- WHEN the Python parity suite runs against the same fixtures
- THEN every case resolves to the identical role as the JS implementation

### Requirement: Single-Service-Account Credential Client

The system MUST expose credentials only through a credentials module bound to exactly one named client: the sismo SA (`FIREBASE_SERVICE_ACCOUNT_JSON`). Nothing dagma-related is used by the new backend (per Extension 2): no dagma client, credential, project id, collection name, or API constant MAY appear anywhere in `backend/`. Google Sheets is likewise out of scope (per Scope Exclusion Addendum). Web-serving routes MUST validate this credential at process startup (fail fast). A route or job MUST only reach this named client if it declares it.

#### Scenario: Missing web-route credential fails startup

- GIVEN `FIREBASE_SERVICE_ACCOUNT_JSON` is unset
- WHEN the FastAPI app starts
- THEN startup fails before serving any request

#### Scenario: A route cannot reach an undeclared client

- GIVEN `reportados` declares no Firestore client dependency
- WHEN its route code executes
- THEN it has no access to any named Firestore client instance

#### Scenario: No dagma reference exists in backend/

- GIVEN the full `backend/` source tree of the consolidated app
- WHEN it is searched for any dagma credential, project id (`dagma-85aad`), collection name, or API constant
- THEN no match is found

### Requirement: Universal Explicit CORS Allowlist

The system MUST apply CORS middleware with an explicit origin allowlist (dashboard, formulario, localhost dev) across every route, authenticating via Bearer token only — no cookie-based credentials.

#### Scenario: Allowed origin receives CORS headers

- GIVEN a request `Origin` matching the formulario or dashboard origin
- WHEN any route is called cross-origin
- THEN the response includes a matching `Access-Control-Allow-Origin`

#### Scenario: Unlisted origin is rejected

- GIVEN a request `Origin` not in the allowlist
- WHEN any route is called cross-origin
- THEN no CORS headers permitting that origin are returned

#### Scenario: No cookie credentials accepted

- GIVEN a request carries only a cookie and no `Authorization` Bearer header
- WHEN any authenticated route is called
- THEN the request is rejected as unauthenticated

### Requirement: In-Process Caching Preserves Or Improves Response Behavior

`reportados` MUST serve from a background-refreshed in-memory snapshot, respond in under 2 seconds, and bound snapshot staleness to 15 minutes (matching today's `s-maxage=900`). `sticker-status` MUST use a 5-minute TTL cache. Both MUST preserve their existing `Cache-Control` response headers.

#### Scenario: reportados responds fast from snapshot

- GIVEN the background refresh has completed at least once
- WHEN `GET /reportados` is called
- THEN the response returns in under 2 seconds

#### Scenario: reportados snapshot staleness bound

- GIVEN the last successful background refresh completed at time T
- WHEN a request arrives before `T + 15min`
- THEN the served snapshot is no older than 15 minutes

#### Scenario: sticker-status cache hit within TTL

- GIVEN a `sticker-status` response was cached less than 5 minutes ago
- WHEN a new request arrives
- THEN it is served from cache without a new Firestore read

#### Scenario: Cache-Control headers preserved

- GIVEN the legacy `s-maxage=900, stale-while-revalidate=86400` header on `reportados`
- WHEN the consolidated route responds
- THEN the same `Cache-Control` header value is present

### Requirement: sticker_matches And cuadrillas Sole-Writer Invariant

The system MUST restrict all writes to `sticker_matches` and `cuadrillas` to exactly two write paths: the admin-gated `sticker-asignaciones`/`inspector-asignaciones` routes, and the `cruce_sticker` job. No other route, job, or future addition MAY write to these collections, since neither has Firestore security rules.

#### Scenario: No write path exists outside the designated two

- GIVEN the full set of routes and jobs in the consolidated app
- WHEN every write operation targeting `sticker_matches` or `cuadrillas` is enumerated
- THEN each one originates only from `sticker-asignaciones`, `inspector-asignaciones`, or the `cruce_sticker` job

### Requirement: Old Endpoint Remains Live Until Consumer Verified

For each migrated endpoint, the legacy Vercel function MUST remain deployed and behaviorally identical until its consumer repoint slice is verified in production. Rollback MUST be a consumer config revert, never a code redeploy.

#### Scenario: Old endpoint still serves after the new one deploys

- GIVEN the consolidated `/reportados` is deployed but `web/js/data.js` still points at the old Vercel URL
- WHEN a client calls the old URL
- THEN it responds identically to before the migration

#### Scenario: Rollback is a config revert

- GIVEN a consumer has been repointed and a regression is found
- WHEN rollback is executed
- THEN it consists of reverting the consumer's endpoint config constant, not a redeploy of removed code

### Requirement: Reproducible Git-Connected Deploy

The system MUST deploy via Railway's GitHub integration from this repository, using an in-repo Dockerfile and a pinned root directory. No CLI-upload deploy (`railway up`) MAY be used for the web service.

#### Scenario: Deploy triggers from a git push

- GIVEN a commit is pushed to the tracked branch
- WHEN Railway's GitHub integration observes it
- THEN a new deploy is triggered automatically from the pinned root/Dockerfile

#### Scenario: No CLI-upload path exists for the web service

- GIVEN the Railway service configuration for the consolidated app
- WHEN its deploy source is inspected
- THEN it is git-connected, not a manually-uploaded CLI deploy
