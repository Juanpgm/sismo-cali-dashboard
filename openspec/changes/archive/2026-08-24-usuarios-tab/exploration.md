# Exploration: Usuarios tab

Change: `usuarios-tab` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-explore

## Feature request (user, Spanish)
"Implementa una pestaña de 'Usuarios' para identificar a cada persona que tiene acceso de
autenticación o que se haya conectado a la app y cuántas veces se ha conectado, descargado
archivos, etc. — métricas de usuario, con estadísticas en la misma tab, y que me permita crear
nuevos usuarios, inhabilitar, borrar, mandar request de cambio de contraseña, y filtrarlos."

Decoded: a "Usuarios" tab listing everyone with auth access or who has connected; per-user
metrics (login count, downloads, etc.); in-tab statistics; admin actions create / disable /
delete / send password-reset; filtering.

## 1. Front-end tab mechanism (where a new tab plugs in)
- Tabs in `web/index.html`: `<nav class="view-tabs">` (~L70-75) with `<button data-view="...">`;
  content is `<section data-view-panel="..." hidden>` (`#view-acciones` L266, `#view-stickers` L269).
- Switching in `web/js/main.js` `switchView(view)` (L180-207): toggles `.is-active`/`aria-selected`
  and `panel.hidden = panel.dataset.viewPanel !== view`. `wireViewTabs()` (L209-213) wires clicks.
  Stickers/Acciones render lazily on first open (e.g. `initStickers(...)` inside `switchView`).
- CSS: `web/styles.css:1506` has a global `[hidden] { display:none !important; }` — the historical
  `[hidden]` vs `display:flex` gotcha is ALREADY fixed globally. A new `#view-usuarios` needs no
  special override; just don't add an ID-specific `display:flex/inline-flex` rule.
- Admin-only tab hiding: `styles.css:1556-1559` hides `acciones`/`stickers` tabs for
  `body[data-role="viewer"]`. Add the Usuarios selector to that rule (client-side hiding only —
  NOT the auth boundary).
- Correction to memory: "Acciones" is NOT empty — it's a fully-built demolition-triage table
  (`web/js/acciones.js`) that is deliberately soft-suspended (`aria-disabled`, opacity .4,
  `switchView()` early-returns on `acciones`). Memory "Gestión eliminada, tab Acciones vacía" is stale.

## 2. Existing user/auth management — the direct template
- `api/stickers.js` is the template. `getAdmin()` (L50-61) lazily inits `firebase-admin` from
  `FIREBASE_SERVICE_ACCOUNT_JSON` (Vercel env), singleton across warm invocations.
- Actions today: `list` (Auth users + Firestore `inspectores/{uid}` profile + live `evaluaciones`
  count), `create` (Auth `createUser` + Firestore profile in a txn allocating a brigade code, with
  Auth-user rollback on failure), `setEnabled` (Auth `updateUser({disabled})` AND Firestore `activo`
  flag — the security rules gate on `activo`, because an issued ID token stays valid ~1h regardless
  of the Auth `disabled` flag).
- NO "delete user" action is exposed anywhere (`deleteUser` used only as create-rollback, L149).
  Net-new. Same token-lifetime caveat applies (delete doesn't revoke a live ID token).
- NO password-reset action anywhere in the repo. Net-new.
- Authz predicate (duplicated in `api/refresh.js` and `api/stickers.js`; `stickers.js` imports
  `verifyFirebaseToken` from `refresh.js`): valid Firebase ID token + `sign_in_provider === 'password'`
  + (stickers only) caller email NOT ending `@sismocali.gov.co`. NO tiered role system — every
  non-inspector password account is an equally-privileged "admin".

## 3. Login/connection & download metrics — CRITICAL GAP
- Firebase Auth Admin `listUsers()` returns per-user `metadata.creationTime`, `.lastSignInTime`,
  `.lastRefreshTime` for free — stickers.js currently ignores them. Gives "account created" +
  "last login", NOT a login count (Firebase overwrites the timestamp each sign-in; no history).
- NO login-count, download-count, or usage-event logging exists anywhere. No analytics/track/audit
  code in `web/` or `api/`.
- Both downloads (`#datos-download`, `#transito-download`, `web/js/main.js` L474-539) are built
  entirely client-side with SheetJS — the server never sees the download; nothing to count from.
- Photo download URLs (`services/photo-signer/api/sign.js`) are signed URLs usable directly once
  issued; can't be counted without instrumenting the signer.
- Bottom line: literal "cuántas veces se ha conectado / descargado archivos" is NOT achievable from
  data that exists today. Feasible now: last sign-in + account-created per user. Real counts require
  NEW server-side event logging (e.g. a Firestore collection incremented on login/download).

## 4. API layer
- `api/*.js` at repo root, Vercel serverless functions, CommonJS, default Node runtime (no
  `functions` override in `vercel.json`).
- `firebase-admin` credential via `FIREBASE_SERVICE_ACCOUNT_JSON`, project **`sismo-agosto-sgred`**.
- Correction to memory/docs: the dashboard auth project is `sismo-agosto-sgred`
  (`web/js/firebase-config.js` L20-27, `api/refresh.js` L22, `api/stickers.js` L22), NOT
  `dagma-85aad`. `dagma-85aad` belongs to the independent `formulario/` subproject. `docs/ADR.md`
  is stale (also wrongly says Google login is disabled; `web/js/auth.js` has it fully wired).

## 5. Filtering/stats patterns to reuse
- `web/js/filters.js` + `web/js/multiselect.js`: Panel tab multi-select column filters + active-filter
  chips — reusable for "filtrarlos".
- `web/js/stickers.js` `rosterHtml()` (L63-84): lightweight "3 chips in the section bar"
  stat pattern (total/activos/inhabilitados) — closest precedent for "estadísticas en la misma tab",
  lighter than Panel's `kpi.js` + Chart.js.

## 6. Constraints, risks, open questions
1. Metrics feasibility gap (§3): only `lastSignInTime`/`creationTime` are free; login/download
   COUNTS require new event-logging infra. Must be scoped explicitly, not assumed from a UI change.
2. Authz has no tiers: any admin can manage any other admin/itself. Need explicit self-management
   rules (can an admin disable/delete itself? the last admin?).
3. Password reset: no email infra. Simplest = Firebase CLIENT SDK `sendPasswordResetEmail` (hosted
   templates, SDK already loaded) vs Admin SDK `generateLink` + custom SMTP (no SMTP in repo).
4. Population overlap with Stickers: `listUsers()` spans three populations on this one project —
   (a) `@sismocali.gov.co` inspectors (already managed in Stickers), (b) password admins,
   (c) `google.com` `@cali.gov.co` viewers (auto-provisioned, no "create" step). Decide: does
   Usuarios REPLACE Stickers' roster as a superset with role filters, or COEXIST (duplicating the
   inspector list)? Resolve in sdd-propose.
5. `listUsers(1000)` single-page cap (`ponytail:` comment in stickers.js) — reuse inherits the ceiling.
6. Delete must be built new, including whether it cleans up related Firestore data
   (`inspectores/{uid}`, `evaluaciones` FKs) like the create-rollback path does.

## Next recommended
sdd-propose — but first resolve the population-overlap and metrics-scope decisions, which
materially change the proposal shape.
