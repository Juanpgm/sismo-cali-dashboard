# Tasks: Usuarios tab

Change: `usuarios-tab` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-tasks

Ordered, independently reviewable/commit-able in the 2-work-unit split locked in `design.md`
(event-logging descoped to Phase 2). Each task cites the concrete file it touches and the spec
requirement it satisfies.

---

## Work unit 1 — Backend: `api/usuarios.js`

Commit: `feat(api): usuarios endpoint`

Depends on: none.

- [x] **1.1** Scaffold `api/usuarios.js` as a byte-for-byte copy of the `api/stickers.js`
      skeleton (`:225-263`): 405 guard, Bearer-token extraction, `verifyFirebaseToken` import
      from `./refresh.js`, fail-closed auth preamble (valid token + `sign_in_provider ===
      'password'` + caller email not `@sismocali.gov.co`), `try` router dispatching on
      `body.action`, `err.status || 502`. Copy the `getAdmin()` singleton (10 lines,
      `admin.apps.length` guard) rather than importing it from `stickers.js`.
      — Satisfies: *Requirement: Tab visibility and server-side authorization*.

- [x] **1.2** Implement the `classify(u)` predicate exactly as ADR-2: inspector domain tested
      before generic `password` provider, then `google.com` + `@cali.gov.co` → viewer, else
      `otro`. Export it (or keep local + export via `module.exports` for the test file).
      — Satisfies: *Requirement: Superset user listing* (role field per row).

- [x] **1.3** Implement `action: 'list'` — `listUsers(1000)` (carry forward the
      `// ponytail: listUsers(1000) ceiling, page with pageToken if ever exceeded` comment from
      `stickers.js:65`), map each `UserRecord` to `{ uid, email, role: classify(u), disabled,
      lastSignInTime, creationTime }`. No `evaluaciones`/`events` join in v1.
      — Satisfies: *Requirement: Superset user listing*, *listUsers(1000) ceiling* scenario.

- [x] **1.4** Implement `action: 'create'` — validate `email` + `password` (≥6 chars), reject
      `@sismocali.gov.co` emails (message pointing to Stickers tab), `createUser({email,
      password})`, return 201 `{ ok, uid, email }`. No Firestore profile write (admins have none).
      — Satisfies: *Requirement: Create user* (successful-create scenario only; admin creation
      has no Firestore profile to roll back — confirm against spec's "rolling back the Auth user
      if the Firestore write fails" language, which applies to the general create requirement;
      admin `create` here has no Firestore write step, so no rollback branch exists for this
      action — document this narrowing inline in the code comment).

- [x] **1.5** Implement `action: 'setEnabled'` — `updateUser({disabled})`, and when
      `classify(target) === 'inspector'`, sync `inspectores/{uid}.activo = !disabled` (mirrors
      `stickers.js setEnabled`). Apply the self-management guard (1.7) before the write.
      — Satisfies: *Requirement: Disable / enable user*.

- [x] **1.6** Implement `action: 'delete'` — `deleteUser(uid)`, then delete `inspectores/{uid}`
      if present. Do NOT touch `evaluaciones`. (No `events/{uid}` cleanup in v1 — event capture is
      Phase 2, ADR-4.) Apply both guards (1.7, 1.8) before any write.
      — Satisfies: *Requirement: Delete user with anti-lockout guards* (successful-delete
      scenario).

- [x] **1.7** Implement guard 1 (self-management): reject `setEnabled`/`delete` when
      `targetUid === callerUid` (`callerUid` from `claims.sub`), `403` via a `forbidden(msg)`
      helper mirroring `badRequest` (`stickers.js:219`).
      — Satisfies: *Scenario: Self-delete blocked*.

- [x] **1.8** Implement guard 2 (last-admin): for `action === 'delete'` only, compute
      `enabledAdmins = users.filter(isEnabledAdmin).length` from the same `listUsers` snapshot
      the action already loaded; if target is an enabled admin and `enabledAdmins <= 1`, reject
      with `403`.
      — Satisfies: *Scenario: Last-admin delete blocked*.

- [x] **1.9** Runnable check: `api/usuarios.test.js`, `assert`-based `demo()`/self-check (no
      framework, mirrors `stickers.test.js` if one exists, else a plain Node script), covering:
      - `classify` on four fixture `UserRecord`-shaped objects → `inspector`/`admin`/`viewer`/
        `otro` (proves inspector-domain-before-provider ordering, ADR-2's core claim).
      - Last-admin count on a fixture list with 1 enabled admin + N others → delete of that admin
        is blocked, delete of a viewer is allowed.
      - Self-uid delete is blocked regardless of role.
      — Satisfies: locked "Runnable check" in `design.md`; covers guards 1.7/1.8 and classify 1.2.

---

## Work unit 2 — Front-end: Usuarios tab UI and wiring

Commit: `feat(web): usuarios tab`

Depends on: unit 1 (calls `/api/usuarios`).

- [x] **2.1** Create `web/js/usuarios.js` cloning `web/js/stickers.js` structure: `ENDPOINT =
      '/api/usuarios'`, the `callApi(getToken, body)` helper copied verbatim (`stickers.js:19-30`),
      `initUsuarios(root, { getToken })` with the render-shell-once / `reload()` fetches `list` /
      re-render-on-open lifecycle matching `initStickers`.
      — Satisfies: *Requirement: Superset user listing*.

- [x] **2.2** Add the `rosterHtml`-pattern stat chips (`stickers.js:63-84` shape): total, activos,
      inhabilitados, plus per-role counts (admins, viewers, inspectores), computed from the
      current unfiltered superset on every render.
      — Satisfies: *Requirement: In-tab stat chips*.

- [x] **2.3** Add role `<select>` + status `<select>` inline filter (client-side over the fetched
      `usuarios` array, re-render on change) — no `multiselect.js`.
      — Satisfies: *Requirement: Role, domain, and status filtering*.

- [x] **2.4** Add row action buttons: disable/enable (`callApi({action:'setEnabled', uid,
      enabled})`), delete (`confirm()` then `callApi({action:'delete', uid})`), reset password
      (`sendPasswordResetEmail(getAuth(getFirebaseApp()), email)`, client SDK, no API hop — import
      `getFirebaseApp` from `./firebase-config.js`). Hide delete/disable on the caller's own row
      as a UI courtesy only (server guard 1.7 is the real boundary).
      — Satisfies: *Requirement: Disable / enable user*, *Delete user with anti-lockout guards*,
      *Send password reset*.

- [x] **2.5** Add the create-user modal mirroring `#sticker-modal` (email + password fields
      instead of cédula + code), wired to `callApi({action:'create', email, password})`.
      — Satisfies: *Requirement: Create user*.

- [x] **2.6** Wire `web/index.html`: add `<button class="view-tab" data-view="usuarios"
      role="tab" aria-selected="false">Usuarios</button>` to `.view-tabs` (next to the Stickers
      button, `:74`), and `<section id="view-usuarios" data-view-panel="usuarios"
      aria-label="Usuarios" hidden></section>` next to `#view-stickers` (`:269`).
      — Satisfies: *Requirement: Tab visibility and server-side authorization* (admin opens tab
      scenario).

- [x] **2.7** Wire `web/js/main.js`: import `initUsuarios` from `./usuarios.js`; in `switchView()`
      (`:180`, alongside the existing `initStickers` call at `:198`) add
      `if (view === 'usuarios') initUsuarios(document.getElementById('view-usuarios'), { getToken:
      getIdToken });`. No early-return guard (unlike suspended Acciones).
      — Satisfies: *Requirement: Tab visibility and server-side authorization*.

- [x] **2.8** Wire `web/styles.css`: add `.view-tab[data-view="usuarios"]` to the
      `body[data-role="viewer"]` hide rule (`:1556-1559`, alongside `stickers`/`acciones`). No
      `#view-usuarios`-specific `display` override — the global `[hidden] { display:none
      !important }` (`:1506`) already handles panel toggling. Add `.usuario-*` styles only where
      the row layout differs from `.sticker-*` (email/role/last-login columns vs brigade-code
      token); reuse `.sticker-*` classes otherwise.
      — Satisfies: *Scenario: Viewer cannot reach the tab or its API* (CSS-hide half).

- [ ] **2.9** Runnable check: manual smoke test — log in as an admin, open Usuarios, confirm the
      superset list renders with rows from all three populations, chips match totals, role/status
      filters narrow the list, and (in a disposable dev account) disable → enable → delete round
      trip and reset-password click all succeed. Log in as a viewer, confirm the tab button is
      absent and a direct `fetch('/api/usuarios', ...)` with the viewer's token returns a
      rejection. No automated UI test — this is DOM wiring plus a server round trip already
      covered by unit 1's `assert` checks; a framework-based UI test is out of proportion to a
      tab clone of an existing pattern.
      — Satisfies: *Scenario: Viewer cannot reach the tab or its API*, *Scenario: Admin opens
      tab*.

---

## Review Workload Forecast

- **Estimated changed lines:** ~380-460 (new `api/usuarios.js` ~200 lines incl. guards +
  `api/usuarios.test.js` ~60 lines + new `web/js/usuarios.js` ~180-220 lines + `index.html` ~2 lines +
  `main.js` wiring ~3 lines + `styles.css` ~15-30 lines). Event-logging is descoped to Phase 2, so
  `web/js/events.js` (~25), the `auth.js`/`main.js` hooks (~10), and the `firestore.rules` block (~6)
  are gone — roughly 40 fewer authored lines than the pre-descope estimate, and the whole
  event-capture work unit is removed.
- **400-line budget risk:** Borderline — the change now sits **near the 400-line threshold** rather
  than clearly over it. It lands under 400 if `web/js/usuarios.js` stays lean and `.usuario-*` styles
  reuse `.sticker-*`; it may edge slightly over if the UI module hits the top of its range. Not the
  clear >400 high-risk case it was before.
- **Chained PRs recommended:** Optional — a single PR is plausible now. Keep the backend→front-end
  split (WU1 `api/usuarios.js` → WU2 UI) available as the fallback if the actual diff lands over 400
  at apply time; otherwise ship as one reviewable PR.
- **Decision needed before apply:** Light — confirm at the apply/review gate: (a) whether the final
  diff crosses 400 (if so, split into the two work units for review; standard single-lens review
  otherwise), and (b) the `create`-action Firestore-rollback narrowing in task 1.4 (admin create has
  no Firestore write, so no rollback branch) is acceptable against the spec's rollback language. The
  former `firestore.rules` authority question is moot — v1 edits no rules (event capture is Phase 2,
  server-side; `sismo-agosto-sgred` rules are console-managed and not in this repo).
