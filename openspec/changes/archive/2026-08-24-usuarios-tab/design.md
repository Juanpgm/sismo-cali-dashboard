# Design: Usuarios tab

Change: `usuarios-tab` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-design

Reads: `proposal.md`, `exploration.md`. Mirrors real code: `api/stickers.js`, `api/refresh.js`
(`verifyFirebaseToken`, `getAdmin`), `web/js/stickers.js` (`callApi`/`rosterHtml`/`initStickers`),
`web/js/auth.js` (`roleForUser`, login paths), `web/js/main.js` (`switchView`, the two downloads),
`web/js/firebase-config.js` (`getFirebaseApp`, `ALLOWED_DOMAIN`), `web/index.html`, `web/styles.css:1556`.

## Architecture at a glance

```
Browser (admin session)                       Vercel serverless            Firebase (sismo-agosto-sgred)
──────────────────────                        ──────────────────           ─────────────────────────────
web/js/usuarios.js  ──POST /api/usuarios──►   api/usuarios.js  ──Admin SDK──► Auth (listUsers/createUser/
  list/create/setEnabled/delete                 getAdmin()                     updateUser/deleteUser)
  (Bearer ID token)                             verifyFirebaseToken()          Firestore inspectores/{uid}
                                                anti-lockout guards
  sendPasswordResetEmail ──client SDK──────────────────────────────────────► Auth (hosted email)

(Phase 2, server-side: usage-event capture → Firestore events/{uid}. Not in v1 — see ADR-4.)
```

The tab reuses the **exact** `api/stickers.js` shape (auth preamble, action routing, `getAdmin`
singleton) and the **exact** `web/js/stickers.js` shape (`callApi`, `rosterHtml` chips, lazy
`init*` on first tab open). Net-new logic is only: the `delete` action, the anti-lockout guards,
and role classification across three populations. (Event-logging capture is descoped to Phase 2 —
ADR-4.)

---

## ADR-1 — `api/usuarios.js` endpoint shape and action routing

**Decision.** One new serverless function `api/usuarios.js`, CommonJS, default Node runtime, POST-only,
`{ action, ...args }` body — a byte-for-byte copy of the `api/stickers.js` skeleton (lines 225-263):
same 405 guard, same Bearer-token extraction, same fail-closed auth preamble, same `try` router that
dispatches on `body.action` and surfaces `err.status || 502`.

Actions:

| action | verb result | Admin SDK calls | net-new? |
|---|---|---|---|
| `list` | 200 `{ ok, usuarios }` | `listUsers(1000)` → map | reuse shape |
| `create` | 201 `{ ok, uid, email }` | `createUser({email,password})` | simplified (no brigade code) |
| `setEnabled` | 200 `{ ok, uid, disabled }` | `updateUser({disabled})` + sync `inspectores/{uid}.activo` | reuse + guard |
| `delete` | 200 `{ ok, uid }` | `deleteUser` + delete `inspectores/{uid}` | **net-new** |

**Auth preamble — reuse verbatim.** Import `verifyFirebaseToken` from `./refresh.js` (already the
shared verifier; `stickers.js` imports it the same way). Reuse the stickers guard exactly: valid ID
token + `sign_in_provider === 'password'` + caller email NOT `@sismocali.gov.co`. Capture the caller
identity from the decoded claims — `claims.sub` (uid) and `claims.email` — for the anti-lockout guards.

**`getAdmin()` — duplicate, do not export-and-share.** The 10-line singleton (`stickers.js:50-61`)
is guarded by `admin.apps.length`, and `firebase-admin` is itself a process singleton, so a second
copy in `usuarios.js` re-uses the same initialized app — zero double-init risk. Copying keeps each
serverless file self-contained, which is the existing repo convention (`refresh.js` and `stickers.js`
each carry their own copy of the auth machinery).
- *Rejected:* exporting `getAdmin` from `stickers.js` and importing it. Creates a cross-function
  coupling for no runtime benefit; breaks the "each function stands alone" pattern.

**`create` is admin-only, not a general user factory.** Google viewers are auto-provisioned at first
sign-in (no create step), and inspectors need a brigade-code transaction that already lives in
Stickers. So `usuarios.create` creates a **password admin**: validate `email` + `password` (≥6),
`createUser`, done — no Firestore profile, no code allocation. Reject `@sismocali.gov.co` emails with a
message pointing to the Stickers tab (an inspector created here would have no brigade code and no
profile doc). This is strictly *less* code than `createInspector`.
- *Rejected:* one polymorphic create that branches on domain to also mint inspectors. Duplicates the
  Stickers transaction; the two create-paths have different invariants. Keep them separate.

**Ceiling — `listUsers(1000)`.** Inherited single-page cap (same `ponytail:` comment as
`stickers.js:65`). The three combined populations are well under 1000 today. If ever exceeded, the
list silently truncates; upgrade path is `pageToken` cursor paging. Carry the comment forward.

---

## ADR-2 — Role classification across three populations

**Decision.** Classify each `UserRecord` from `listUsers` with a single ordered predicate. Order
matters because inspectors are also `password`-provider, so the inspector domain must be tested first.

```js
const INSPECTOR_DOMAIN = '@sismocali.gov.co';
const VIEWER_DOMAIN = '@cali.gov.co';               // ALLOWED_DOMAIN in firebase-config.js
const hasProvider = (u, id) => (u.providerData || []).some((p) => p.providerId === id);

function classify(u) {
  const email = (u.email || '').toLowerCase();
  if (email.endsWith(INSPECTOR_DOMAIN)) return 'inspector';        // password, but a field account
  if (hasProvider(u, 'password'))       return 'admin';            // password, non-sismocali
  if (hasProvider(u, 'google.com') && email.endsWith(VIEWER_DOMAIN)) return 'viewer';
  return 'otro';                                                   // exists in Auth but not a live role
}
```

This mirrors `auth.js roleForUser()` (which is `providerData`-based) so the server's view of "who is
an admin" cannot drift from the client's login gate. The `otro` bucket surfaces stray/rejected
accounts instead of hiding them (an operator wants to see and delete them).

Each `list` row: `{ uid, email, role, disabled, lastSignInTime, creationTime }`, straight from
`u.metadata.lastSignInTime` / `.creationTime` (free from the Admin SDK; `stickers.js` currently
ignores them). No `evaluaciones` count and no `events` join in v1 — the counter UI is Phase 2, keep
`list` lean.

- *Rejected:* deriving role from Firestore profile docs. Viewers and admins have no profile doc;
  `providerData` + email is the only source that spans all three populations.

---

## ADR-3 — Anti-lockout guards (server-side, fail-closed)

**Decision.** Two independent guards, computed from the same `listUsers` snapshot the action already
loads, enforced before any `deleteUser`/`updateUser` write:

```js
const isEnabledAdmin = (u) => !u.disabled && classify(u) === 'admin';

// 1. No self-management: caller cannot disable or delete their own account.
if (targetUid === callerUid) throw forbidden('No podés inhabilitar ni eliminar tu propia cuenta.');

// 2. No deleting the last admin: block a delete whose target is the only enabled admin.
if (action === 'delete') {
  const enabledAdmins = users.filter(isEnabledAdmin).length;   // "last admin" = this count
  const targetIsAdmin = isEnabledAdmin(usersById.get(targetUid));
  if (targetIsAdmin && enabledAdmins <= 1) throw forbidden('No podés eliminar al último administrador.');
}
```

**"Last admin" is defined as `count(enabled password non-@sismocali accounts)`** — exactly the locked
decision. Computed live from the request's `listUsers` result (no separate query, no cache).

**Why the last-admin guard only needs to cover `delete`.** Given guard 1, an admin can never disable
*themselves*, so they can only disable *other* admins — which always leaves at least the caller
enabled. Full lockout-by-disable is therefore already impossible. Delete is the only path that could
in principle remove the final admin (e.g. a token/uid edge or a future relaxation of guard 1), so it
carries the explicit count check. Both guards are kept even where they overlap: defense in depth, and
each reads independently at the call site.

`forbidden(msg)` mirrors `badRequest` (`stickers.js:219`) with `err.status = 403`.

**Live-token caveat (documented, not fixed).** Disable/delete do not revoke an already-issued ID
token (~1h). The durable gate is the Firestore `activo` flag that the security rules read — so
`setEnabled` syncs `inspectores/{uid}.activo` when the target is an inspector, exactly as
`stickers.js setEnabled` does. Admins/viewers have no `activo` doc and no rule-gated writes, so their
disable is Auth-flag-only; acceptable and consistent with today's behavior.

**Delete cleanup scope (locked).** `deleteUser(uid)` + delete `inspectores/{uid}` (if present).
(There is no `events/{uid}` to clean up in v1 — event capture is Phase 2, ADR-4; Phase 2's
server-side delete path will remove it.) **`evaluaciones` are left intact** — they are historical inspection
records keyed by `inspector.uid`; nulling or reassigning FKs would corrupt the audit trail. The count
join in Stickers (`where('inspector.uid','==',uid)`) simply returns rows for a now-absent account,
which is correct history.

---

## ADR-4 — `events` collection: schema, write hooks, rules — SUPERSEDED / DESCOPED TO PHASE 2

> **STATUS: SUPERSEDED — descoped from v1, deferred to a named Phase 2 (server-side).** Nothing in
> this ADR is implemented in v1: no `web/js/events.js`, no login/download write hooks in
> `auth.js`/`main.js`, no `events/{uid}` rules edit, no Firestore client SDK added to the web bundle.
>
> **Why descoped.** The original plan wrote `events/{uid}` **client-side** with `increment()`. Two
> problems make that wrong for v1:
> 1. **The `sismo-agosto-sgred` Firestore rules are console-managed and are NOT version-controlled in
>    this repo.** The only `firestore.rules` present (`integracion_F1/firestore.rules`) belongs to a
>    **different** project, `dagma-85aad` — editing it does nothing for `sismo-agosto-sgred`. Shipping
>    the rules block below as a repo edit would be a no-op against the real project and would falsely
>    imply the write surface is governed.
> 2. **Client-side counter writes are inflatable.** A client can increment its own `events/{uid}`
>    doc directly; the counter is a soft usage metric with no integrity guarantee (see the "acceptable
>    tamper ceiling" note that was already in this ADR). Opening a new client-write surface on a
>    project that today writes only server-side is a net-new attack surface for a metric that does not
>    warrant it.
>
> **Phase-2 direction.** Do the capture **server-side** (e.g. increment `events/{uid}` from the same
> Vercel serverless layer that already holds the Admin SDK, on a server-observed login/download
> signal), so no client-write surface or console rule change is needed, and the counter cannot be
> self-inflated. The schema and read-cost reasoning below are **retained for Phase 2 design** — the
> per-uid counter-doc shape is still the cheapest Phase-2 read; only the *write path* moves from
> client to server.
>
> The remainder of this ADR is the original (client-side) reasoning, preserved for Phase 2 and **not
> a v1 commitment**.

**Original decision (Phase 2 reference) — per-uid rolling counter doc (`events/{uid}`).**

```
events/{uid}  {
  logins:      <number>          // FieldValue.increment(1) on each login
  downloads:   <number>          // FieldValue.increment(1) on each download
  lastLoginAt:    <Timestamp>    // serverTimestamp() on login
  lastDownloadAt: <Timestamp>    // serverTimestamp() on download
  email:       <string>          // denormalized for a readable Phase-2 join
}
```

**Why the counter doc over an append-only event stream.**
- *Cheapest Phase-2 read.* The counts the counter UI needs (`logins`, `downloads` per user) are the
  document itself. Phase-2 `list` adds one `getAll(events/{uid}...)` — the same join Stickers already
  does for `inspectores/{uid}` — and the numbers arrive with zero aggregation compute. An append
  stream would need a `count()` query per uid or a scheduled rollup.
- *Cheapest, race-free write.* `setDoc({...}, {merge:true})` with `FieldValue.increment(1)` is atomic
  server-side — no read-modify-write, no transaction, one write per event.
- *Acceptable tamper ceiling.* A client can only touch its **own** doc (rule below), so the worst case
  is a user inflating *their own* usage metric. This is a usage counter, not a security boundary.

- *Rejected — append-only stream `events/{autoId}` `{uid,type,ts}`.* More granular (enables
  time-series like "logins last 7 days") but more docs, and Phase-2 aggregation is a per-uid query +
  count or a scheduled rollup — more read cost and more code for a counter UI that only needs totals.
  **Ponytail ceiling + upgrade path:** the counter doc loses time-series. If Phase 2 wants trends over
  time, either switch to the append stream or add a scheduled function that snapshots the counters
  into a daily rollup. `lastLoginAt`/`lastDownloadAt` give cheap recency without a stream in the
  meantime. `// ponytail: counter doc, no time-series; switch to event stream if trends are needed`.

**Write hooks (data capture only, no read/UI in v1):**
- **Login** — `web/js/auth.js`, after each *explicit* successful sign-in: right after
  `signInWithPopup(...)` resolves and right after `signInWithEmailAndPassword(...)` resolves (the two
  handlers at `auth.js:178` and `:194`). Hooking the explicit success paths — not
  `onAuthStateChanged` — means a persisted-session page reload does **not** inflate the login count;
  only a real sign-in does.
- **Download** — `web/js/main.js`, at the end of the two existing click handlers (`#datos-download`
  `:474`, `#transito-download` `:513`), after `XLSX.writeFile(...)` succeeds.

**New tiny module `web/js/events.js`** exports `logEvent(type)`:

```js
// web/js/events.js — usage capture (v1: write-only, no reads/UI).
import { getFirestore, doc, setDoc, increment, serverTimestamp }
  from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js';
import { getAuth } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js';
import { getFirebaseApp } from './firebase-config.js';

// type: 'login' | 'download'. Fire-and-forget: a metrics write must never block
// or break the action that triggered it.
export async function logEvent(type) {
  try {
    const user = getAuth(getFirebaseApp()).currentUser;
    if (!user) return;
    const field = type === 'login' ? 'logins' : 'downloads';
    const stamp = type === 'login' ? 'lastLoginAt' : 'lastDownloadAt';
    await setDoc(doc(getFirestore(getFirebaseApp()), 'events', user.uid),
      { [field]: increment(1), [stamp]: serverTimestamp(), email: user.email || '' },
      { merge: true });
  } catch { /* metrics are best-effort */ }
}
```

This adds the Firestore client SDK to the web bundle (currently only the Auth SDK is imported).
Contained to this one module.

**Firestore security rules addition** (Phase 2, applied to the **console-managed** `sismo-agosto-sgred`
ruleset — which is NOT in this repo; `integracion_F1/firestore.rules` belongs to the different
`dagma-85aad` project and is not the deployed ruleset for `sismo-agosto-sgred`). A server-side Phase-2
capture path may not even need a client-write rule; shown here as the original client-side plan only:

```
match /events/{uid} {
  allow read: if false;                                  // only the Admin SDK (server) reads
  allow create, update: if request.auth != null && request.auth.uid == uid;
  allow delete: if false;                                // deletion happens via Admin SDK on user-delete
}
```

`read: false` is correct because the client never reads events; the server uses the Admin SDK which
bypasses rules. `create/update` scoped to `request.auth.uid == uid` is the tamper boundary (own doc
only). Rules cannot cheaply prove a write is exactly `+1`, which is the accepted ceiling above.

---

## ADR-5 — Front-end module `web/js/usuarios.js`

**Decision.** Clone the `web/js/stickers.js` structure. Reused verbatim:
- `ENDPOINT = '/api/usuarios'` + the identical `callApi(getToken, body)` helper (`stickers.js:19-30`).
- `initUsuarios(root, { getToken })` — renders a shell once, `reload()` fetches `list`, renders
  table + chips, `wire()` binds actions; re-fetches on each open. Same lifecycle as `initStickers`.
- The **`rosterHtml` chip pattern** (`stickers.js:63-84`): section-bar inline chips for the in-tab
  stats — `total`, `activos`, `inhabilitados`, plus per-role counts (`admins`, `viewers`,
  `inspectores`). This is the locked "lightweight chips, not the Panel KPI/Chart stack" choice.
- The **create modal** mirrors `#sticker-modal` (email + password fields instead of cédula + code).

**Filtering — light inline filter, not the Panel `multiselect`.** The Panel `filters.js` /
`multiselect.js` are built for many-column faceted filtering over the records store with active-chip
sync — heavier than this tab needs. A role `<select>` + status `<select>` + a text `<input>`, filtered
client-side over the fetched `usuarios` array with a re-render, matches the `rosterHtml` weight and
the "filtrarlos" requirement.
- *Rejected:* wiring `multiselect.js`. Over-built for three facets on one already-loaded array; adds
  coupling to the Panel store's filter model.

**Row actions:**
- **Disable / enable** → `callApi({action:'setEnabled', uid, enabled})`, same as Stickers.
- **Delete** → `confirm()` then `callApi({action:'delete', uid})`. Server enforces the guards; the UI
  additionally hides delete/disable on the caller's own row as a courtesy (not a security boundary).
- **Reset password** → **client SDK**, no API hop: `sendPasswordResetEmail(getAuth(getFirebaseApp()),
  email)` imported from the Firebase Auth SDK. Uses Firebase's hosted email + action URL (locked
  decision). No change to `auth.js` needed — `usuarios.js` builds its own `getAuth(getFirebaseApp())`
  handle; `getFirebaseApp` is already exported from `firebase-config.js`.

---

## ADR-6 — Tab wiring

**Decision.** Mirror the Stickers wiring exactly.
- **`web/index.html`** — add `<button class="view-tab" data-view="usuarios" role="tab"
  aria-selected="false">Usuarios</button>` to `.view-tabs` (~L74), and
  `<section id="view-usuarios" data-view-panel="usuarios" aria-label="Usuarios" hidden></section>`
  next to `#view-stickers` (~L269).
- **`web/js/main.js`** — in `switchView()` (~L197) add
  `if (view === 'usuarios') initUsuarios(document.getElementById('view-usuarios'), { getToken: getIdToken });`
  and import `initUsuarios`. No early-return guard (unlike suspended Acciones).
- **`web/styles.css`** — add `body[data-role="viewer"] .view-tab[data-view="usuarios"]` to the
  role-hide rule at L1556-1559 (client-side presentation only — every server action authorizes
  independently). The global `[hidden] { display:none !important }` (L1506) already handles panel
  toggling, so **no** `#view-usuarios`-specific `display` rule (avoids the historical `[hidden]` vs
  `display:flex` gotcha). Reuse `.sticker-*` styles where possible; add `.usuario-*` only where the
  row layout differs (email/role/last-login columns vs the brigade-code token).

---

## Size and commit/PR split

With event-logging descoped to Phase 2, the change is smaller (no `events.js`, no auth/main hooks, no
rules edit): new API function + new JS module + HTML/CSS/main wiring. This lands **near or under 400
authored lines** and can plausibly ship as a **single PR**. Two work units, split backend→front-end
if the diff still needs bounding:

1. **`feat(api): usuarios endpoint`** — `api/usuarios.js` (list/create/setEnabled/delete + guards) +
   `api/usuarios.test.js` self-check (pure `classify` + last-admin-count assertions; `assert`-based,
   no framework, mirrors `stickers.test.js`).
2. **`feat(web): usuarios tab`** — `web/js/usuarios.js` + `index.html` + `main.js` wiring +
   `styles.css`.

## Runnable check (locked, for the API commit)

`classify` and the last-admin count are the money paths — one `assert`-based `demo()` in
`api/usuarios.test.js`:
- `classify` on four fixture UserRecords → `inspector`/`admin`/`viewer`/`otro` (proves domain-before-
  provider ordering).
- last-admin count on a fixture list with 1 enabled admin + N others → delete of that admin blocked,
  delete of a viewer allowed; self-uid delete blocked.

## Risks / open decisions carried to tasks

1. **Metrics expectation gap.** v1 ships last-sign-in + account-created only; login/download
   *counts* (both capture and UI) are Phase 2, server-side (ADR-4). Communicate the boundary so the
   deliverable is not read as incomplete.
2. **`listUsers(1000)` ceiling.** Inherited; silent truncation past 1000 combined accounts. Cursor
   paging only if hit.
3. **Live-token latency.** Disable/delete do not revoke an issued ID token (~1h); `activo` is the real
   gate for inspectors; admins/viewers have no rule-gated writes. State it to operators.
4. **Client reset = Firebase templates.** `sendPasswordResetEmail` uses Firebase hosted email + action
   URL; confirm the project's authorized domains and template branding are acceptable.
5. **`create` domain policy.** Decided: `usuarios.create` mints password admins and rejects
   `@sismocali.gov.co` (those belong in Stickers). Confirm no operator expects to create inspectors here.
6. **`sismo-agosto-sgred` rules are console-managed, not in repo (Phase-2 concern).** The repo's only
   `firestore.rules` (`integracion_F1/firestore.rules`) belongs to `dagma-85aad`, a different project —
   it is NOT the deployed ruleset for `sismo-agosto-sgred`. v1 edits no rules. Phase 2's server-side
   event capture avoids needing a client-write rule at all; if any rule change is ever required it must
   be made in the `sismo-agosto-sgred` console, not this repo.
