# Proposal: Usuarios tab

Change: `usuarios-tab` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-propose

## Why

The dashboard has no single place to see who can authenticate or who has connected. Today user
management is split and partial: the Stickers tab manages only `@sismocali.gov.co` inspectors,
Google `@cali.gov.co` viewers are auto-provisioned invisibly, and password admins exist only in the
Firebase console. There is no way for an operator to answer "who has access, who is this person,
when did they last sign in, and disable/reset/remove them" without leaving the app and opening the
Firebase console. As the operation scales past the emergency phase, access hygiene (stale accounts,
last-login visibility, controlled offboarding) becomes an operational and security need, not a
convenience.

The user also wants usage metrics (logins, downloads). Those counts do not exist in any data source
today (see exploration §3): Firebase only keeps a last-sign-in timestamp, and downloads are built
entirely client-side so the server never sees them. Waiting to design a "perfect" analytics feature
would block the access-management value that is deliverable now — and would keep accumulating zero
history. So this change ships the access-management tab now and starts writing usage events now, so
the counter UI can land later on real history instead of an empty table.

## What Changes (v1, in scope)

- **New "Usuarios" tab** (`#view-usuarios`) wired into `view-tabs` / `switchView()`, admin-only via
  the existing client-side role-hide CSS rule (presentation only — every server action authorizes
  independently).
- **Superset user list** from `listUsers()` on project `sismo-agosto-sgred` — a single source of
  truth spanning all three populations: password admins, Google `@cali.gov.co` viewers, and
  `@sismocali.gov.co` inspectors. Each row surfaces identity (email/uid/provider) and the metrics
  that exist today only: **last sign-in** (`metadata.lastSignInTime`) and **account created**
  (`metadata.creationTime`) from the Admin SDK.
- **Role / domain / status filter** so the superset can be narrowed (admins vs viewers vs inspectors,
  enabled vs disabled), reusing the existing filter/chip patterns.
- **In-tab stat chips** (total / active / disabled, and per-role counts) — the lightweight
  section-bar chip pattern from `stickers.js` `rosterHtml()`, not the heavier Panel KPI/Chart stack.
- **Admin actions**, each authorized server-side (valid Firebase ID token + `sign_in_provider ===
  'password'`):
  - **Create** — Auth `createUser` + Firestore profile, mirroring the `stickers.js` create txn.
  - **Disable / enable** — Auth `updateUser({disabled})` **and** the Firestore `activo` flag,
    mirroring the `stickers.js` `setEnabled` pattern (the `activo` flag is what security rules gate;
    the Auth flag alone does not revoke a live ID token, ~1h).
  - **Delete** — net-new action. Removes the Auth user and cleans up related Firestore data
    (`inspectores/{uid}` profile and related records), reusing the cleanup shape of the existing
    create-rollback path. Same live-token caveat as disable.
  - **Send password-reset** — Firebase **client SDK** `sendPasswordResetEmail` from the browser
    (SDK already loaded, reuses Firebase hosted email templates). No Admin SDK link generation, no
    SMTP.
- **Anti-lockout authz guards** (enforced server-side): any password admin may manage others, but an
  admin may **not** delete or disable **their own** account, and may **not** delete the **last
  remaining** admin. No superadmin tier is introduced.

## Explicitly Out of Scope

- **Event-logging / usage counters = entirely Phase 2 (capture AND UI).** Both halves — writing
  login/download events AND surfacing login-count / download-count aggregates in the Usuarios tab —
  are deferred to a named Phase 2. v1 writes NO events and adds NO counter UI. Reason: the
  `sismo-agosto-sgred` Firestore rules are console-managed and are **not** version-controlled in this
  repo (the only `firestore.rules` present, `integracion_F1/firestore.rules`, belongs to a different
  project, `dagma-85aad`). Opening a client-side write surface (`events/{uid}`) on a project that
  today writes only server-side is both a new attack surface and inflatable by the user (a client can
  increment its own counter). Phase 2 will do the capture **server-side** instead.
- **No superadmin / role tiers.** The flat "any password account is an equal admin" model stays.
- **No Stickers rewrite or deletion.** Stickers may remain for inspector brigade-code management;
  Usuarios is the superset view. The two coexist; reconciling/removing Stickers is not in this change.
- **No custom email infrastructure** (no SMTP, no Admin SDK `generateLink`).
- **No pagination beyond the inherited `listUsers(1000)` single-page cap** (see Risks).
- **No instrumentation of the photo-signer** signed-URL path for download counts.

## Impact

New / touched surfaces:

- **New `api/usuarios.js`** (or similarly named serverless function) — `list` / `create` /
  `setEnabled` / `delete`, reusing `getAdmin()` and `verifyFirebaseToken` from the `stickers.js` /
  `refresh.js` pattern; adds the delete action and the anti-lockout guards (self-management +
  last-admin checks) server-side.
- **New `web/js/usuarios.js`** — renders the tab: superset table, filter, stat chips, action
  buttons; calls the client SDK `sendPasswordResetEmail` for reset.
- **`web/index.html`** — new `data-view="usuarios"` tab button + `#view-usuarios` panel section.
- **`web/js/main.js`** — `switchView()` case + lazy `initUsuarios()` on first open (mirrors Stickers).
- **`web/styles.css`** — add `usuarios` to the `body[data-role="viewer"]` hide rule; tab/table styling.

## Risks & Open Questions

1. **Metrics expectation gap.** The user asked for connection/download *counts*; v1 delivers only
   last-sign-in + account-created. Event capture and the counter UI are both Phase 2 (server-side).
   The v1/Phase-2 boundary must be communicated so the deliverable is not perceived as incomplete.
2. **Delete cleanup scope.** Exactly which Firestore records a delete must remove needs confirming —
   `inspectores/{uid}` is clear; whether `evaluaciones` FKs should be nulled, reassigned, or left
   (they are historical inspection records, likely keep) needs a decision in spec/design.
3. **`listUsers(1000)` cap** is inherited. Fine at current scale; if the combined population ever
   exceeds 1000 the list silently truncates. Flag as a known ceiling, add cursor paging only if hit.
4. **Live-token latency.** Disable/delete do not revoke an issued ID token (~1h); the `activo` flag
   in security rules is the real gate. Acceptable and consistent with current Stickers behavior, but
   worth stating so operators do not expect instant lockout.
5. **Client-side reset means Firebase templates.** `sendPasswordResetEmail` uses Firebase's hosted
   email + action URL; confirm the project's authorized domains / template branding are acceptable.

## Rough size

With event-logging descoped, the change is smaller: new API function + new JS module + HTML/CSS/main
wiring, no event hooks and no `events.js`/rules edit. This likely lands **near or under the 400-line
budget** and can plausibly ship as a **single PR**. The two remaining work units (backend
`api/usuarios.js`, then front-end) map to a clean backend→front-end commit split if the diff still
needs bounding; confirm the final line count against the 400 threshold at the apply/review gate.
