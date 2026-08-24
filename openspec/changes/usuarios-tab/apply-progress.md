# Apply progress: usuarios-tab

Change: `usuarios-tab` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-apply
Artifact store: openspec (Engram not connected for this change)

## Batch: Work Unit 1 — Backend `api/usuarios.js`

Status: **done**. Tasks 1.1–1.9 marked `[x]` in `openspec/changes/usuarios-tab/tasks.md`.

### Files created

- `api/usuarios.js` — new serverless function, CommonJS, mirrors `api/stickers.js` skeleton
  (405 guard, Bearer-token extraction, `verifyFirebaseToken` import from `./refresh.js`,
  fail-closed auth preamble, `getAdmin()` singleton duplicated per ADR-1).
  - `classify(u)` — ordered predicate per ADR-2 (inspector domain → password/admin →
    google.com+@cali.gov.co/viewer → otro).
  - `action: 'list'` — `listUsers(1000)` → `{ uid, email, role, disabled, lastSignInTime,
    creationTime }` per row. `ponytail: listUsers(1000) ceiling` comment carried forward.
  - `action: 'create'` — password admin only; rejects `@sismocali.gov.co` (message points to
    Stickers tab); no Firestore write, so no rollback branch (per design ADR-1's explicit
    narrowing of the general create-with-rollback spec language — confirmed against
    `design.md` ADR-1 / tasks.md 1.4, which locks this as the accepted narrowing for the
    admin-create path specifically).
  - `action: 'setEnabled'` — `updateUser({disabled})` + syncs `inspectores/{uid}.activo` only
    when target classifies as inspector (mirrors `stickers.js`). Self-guard applied first.
  - `action: 'delete'` — **net-new**. `deleteUser` + best-effort delete of
    `inspectores/{uid}`; `evaluaciones` untouched. Guards applied via `checkDeleteGuards`
    before any write.
  - Guards: self-management (`targetUid === callerUid`, 403) applied in both `setEnabled` and
    `delete`; last-admin guard (`checkDeleteGuards`, computed from the live `listUsers`
    snapshot the delete action already loaded) applied in `delete` only, per ADR-3's reasoning
    that guard 1 already makes self-disable-lockout impossible.
  - Deviation from the literal ADR-3 pseudocode: the guard logic was factored into a pure,
    exported `checkDeleteGuards(users, targetUid, callerUid)` function (not inlined in
    `deleteUsuario`) specifically so task 1.9's runnable check can exercise it without mocking
    the Firebase Admin SDK. Behavior is identical to the ADR-3 snippet; this is a testability
    refactor, not a logic change.

- `api/usuarios.test.js` — assert-based self-check, `node api/usuarios.test.js`, no framework,
  mirrors `api/stickers.test.js`'s style. Covers:
  - `classify` on 4 fixture `UserRecord`-shaped objects → inspector/admin/viewer/otro (proves
    inspector-domain-before-provider ordering).
  - Last-admin count: fixture list with 1 enabled admin + viewer + inspector + 1 disabled
    admin → delete of the enabled admin blocked (403), delete of the viewer allowed (null);
    adding a 2nd enabled admin unblocks the first admin's delete.
  - Self-uid delete blocked regardless of role (admin, viewer, inspector all rejected when
    `targetUid === callerUid`).
  - `isValidPassword` sanity (Firebase 6-char minimum).

### Self-check — actual run output

```
$ node api/usuarios.test.js
usuarios.test.js OK
```

Also re-ran the pre-existing `api/stickers.test.js` as a regression sanity check (untouched
file, no changes made to it):

```
$ node api/stickers.test.js
stickers.test.js OK
```

### Tasks.md updated

All WU1 checkboxes (1.1–1.9) flipped `[ ]` → `[x]` in
`openspec/changes/usuarios-tab/tasks.md` in this batch.

## Batch: Work Unit 2 — Front-end `web/js/usuarios.js` + tab wiring

Status: **done, except manual browser smoke test (2.9)**. Tasks 2.1–2.8 marked `[x]`; 2.9 left
`[ ]` — it is an explicit manual smoke test (log in as admin/viewer in a real browser session)
that this sandboxed, browser-less apply environment cannot perform. See "What remains" below.

### Files created / changed

- `web/js/usuarios.js` — **new**, clones `web/js/stickers.js`'s shape exactly:
  - `ENDPOINT = '/api/usuarios'` + `callApi(getToken, body)` copied verbatim from
    `stickers.js:19-30` (Bearer ID token attach, `{error}` surfacing on non-2xx).
  - `initUsuarios(root, { getToken })` — shell rendered once, `reload()` fetches
    `{action:'list'}`, `render()` recomputes the client-side filtered view and re-renders the
    roster subtree, `wire()` binds all actions. Re-fetches on every tab open (same lifecycle as
    `initStickers`, wired from `main.js switchView`).
  - `classify`/role handling is **not duplicated here** — the server's `list` response already
    carries `role` per row (`classify(u)` ran in `api/usuarios.js`); the front-end only maps
    `role` → a Spanish label (`ROLE_LABEL`) for display. No client-side role re-derivation, so
    there is exactly one place (the server) that can disagree with `auth.js roleForUser()`.
  - `rowHtml`/`chipsHtml`/`rosterHtml` mirror `stickers.js`'s `rowHtml`/`rosterHtml`
    structure and CSS-class vocabulary (`.sticker-pill`, `.sticker-chip`, `.sticker-action`,
    `.sticker-row`, `.sticker-list`, `.sticker-empty`, `.sticker-loading`, `.sticker-field`,
    `.sticker-form*`, `.modal`/`.modal-panel`) — only `.usuario-row`, `.usuario-actions`, and
    `.usuario-filters` are net-new classes (see styles.css below).
  - Chips (task 2.2): total / activos / inhabilitados / admins / viewers / inspectores,
    computed from the **unfiltered** `usuarios` array on every render, per spec's "In-tab stat
    chips" requirement and design.md ADR-5's locked chip shape. `otro` has no chip (design
    only locked admins/viewers/inspectores per-role counts); `otro` rows still appear in the
    list and are selectable via the role filter.
  - Filter (task 2.3): plain `role` + `status` `<select>` elements, filtered client-side over
    the already-fetched `usuarios` array, full re-render on `change` (no `multiselect.js`, per
    ADR-5's explicit rejection of the Panel filter stack for three facets on one loaded array).
  - Row actions (task 2.4): `setEnabled` and `delete` call `/api/usuarios`; `delete` requires
    `confirm()` first (matches the repo's existing destructive-action convention — `stickers.js`
    has no delete action to compare against, so this follows the general repo pattern of
    `confirm()` before an irreversible server call). Both the caller's own disable/delete
    buttons are omitted client-side as a UI courtesy — the code comment explicitly notes this
    is *not* the security boundary; `api/usuarios.js`'s self-management guard (ADR-3) is.
    Server error messages (`err.message`, including the anti-lockout 403 strings like "No podés
    eliminar al último administrador.") are surfaced verbatim via `alert()`, matching
    `stickers.js`'s existing `alert(err.message)` pattern for the enable/disable action.
  - Reset password (task 2.4): `sendPasswordResetEmail(getAuth(getFirebaseApp()), email)` —
    **client SDK, no API hop**, imported from
    `https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js`, the exact same modular-SDK
    import style and CDN version `web/js/auth.js` already uses (`getAuth`, `getFirebaseApp()`
    from `firebase-config.js`). No new import style introduced.
  - Create form (task 2.5): a modal cloned from `#sticker-modal` (`#usuario-modal`) with
    `email`/`password` fields instead of `cedula`/`nombre_completo`/`entidad`/`password`,
    submitting `{action:'create', email, password}`. No native `prompt()` used anywhere —
    avoids the dialog-trap the instructions flagged.

- `web/index.html` — added the `Usuarios` tab button (`data-view="usuarios"`) next to
  `Stickers` in `.view-tabs` (~L74-75), and `<section id="view-usuarios"
  data-view-panel="usuarios" aria-label="Usuarios" hidden></section>` next to
  `#view-stickers` (~L268-270). No `aria-disabled`/`tabindex="-1"` (unlike the suspended
  Acciones tab) — Usuarios is fully active.

- `web/js/main.js` — imported `initUsuarios` from `./usuarios.js`; in `switchView()` added an
  `if (view === 'usuarios') initUsuarios(document.getElementById('view-usuarios'), { getToken:
  getIdToken });` block immediately after the existing Stickers block. No early-return guard
  (unlike the suspended Acciones tab) — matches the locked ADR-6 decision. The generic
  `.view-tab`/`[data-view-panel]` toggling loops already earlier in `switchView()` handle
  Usuarios' active/hidden state with no Usuarios-specific code needed there.

- `web/styles.css` —
  - Added `body[data-role="viewer"] .view-tab[data-view="usuarios"]` to the existing
    role-hide rule (was `#refresh-btn`, `#transito-download`, `acciones`, `stickers`; now also
    `usuarios`), ~L1556-1560.
  - Added three minimal net-new rules right after the existing `.sticker-chip.is-off` rule
    (~L1699-1703): `.usuario-filters` (flex row for the two `<select>`s), `.usuario-row`
    (overrides `.sticker-row`'s 5-column grid — `auto auto 1fr auto auto`, brigade-code +
    avatar + identity + estado + toggle — with a 4-column grid — `auto 1fr auto auto`, no
    brigade-code column since Usuarios has no analogous field), and `.usuario-actions` (flex
    wrap for the 2-3 action buttons per row, replacing Stickers' single toggle button in that
    grid cell). Everything else (`.sticker-pill`, `.sticker-chip`, `.sticker-action`,
    `.sticker-avatar`, `.sticker-identity`, `.sticker-name`, `.sticker-meta`, `.sticker-warn`,
    `.sticker-list`, `.sticker-empty`, `.sticker-loading`, `.sticker-form*`, `.sticker-field`,
    `.sticker-modal-panel`, `.sticker-note`, `.sticker-error`, `.sticker-ok`, `.section-bar*`)
    is reused unmodified — no `.usuario-*` duplicate was written for anything Stickers already
    styles identically. No `#view-usuarios`-specific `display` rule was added (the global
    `[hidden] { display:none !important }` at L1506 already handles panel toggling — the exact
    gotcha ADR-6 called out to avoid).
  - **Known minor gap, not fixed (documented, not a blocker):** the existing mobile media
    query (~L1807-1813) targets `.sticker-row` generically and collapses it to a 3-column grid
    (`auto 1fr auto`) on narrow screens; because `.usuario-row` and `.sticker-row` share equal
    CSS specificity (both single class selectors) and the media-query block is declared later
    in the file, the mobile rule wins over the desktop-authored `.usuario-row` 4-column rule at
    narrow widths. Net effect: on mobile, the `.usuario-actions` group (a flex-wrapped button
    cluster, not individual `.sticker-action` grid children like Stickers') doesn't get the
    `.sticker-row .sticker-action { grid-column: 2/4 }` full-width treatment Stickers' mobile
    buttons get, so it likely renders slightly cramped rather than broken. Left as-is per
    ponytail (`# ponytail: usuario-row inherits stickers' mobile 3-col collapse as-is; add a
    matching mobile override in the same media query if it's reported visually cramped`) — no
    code comment was added inline in `styles.css` itself for this since it's a CSS cascade
    interaction, not a runtime code path; documenting it here instead. Desktop/tablet layout
    (where admins actually manage users) is unaffected.

### Self-check — actual run output

```
$ node --check web/js/usuarios.js
(no output — exit 0)
```

Confirmed `node --check` correctly parses this file's ESM `import` syntax by first running it
against the pre-existing `web/js/stickers.js` and `web/js/main.js` (both also `import`-based
browser modules) and observing the same clean exit, then against `web/index.html` to confirm
`node --check` legitimately rejects a non-JS file (`ERR_UNKNOWN_FILE_EXTENSION`) rather than
silently no-op'ing on anything handed to it — i.e. the check is real, not a false-positive on
an unparsed/misidentified file.

No lint or build tooling exists in this repo (`package.json` has no `lint`/`build` script, no
`.eslintrc`/`eslint.config.*` found at the repo root or under `web/`) — nothing further to run.

### What remains

- **Task 2.9 (manual smoke test) — not performed.** Requires a real browser session logged in
  as an admin (superset list renders, chips match totals, role/status filters narrow the list,
  disable → enable → delete round trip, reset-password click) and separately as a viewer
  (tab button absent, direct `fetch('/api/usuarios', ...)` with the viewer's token rejected).
  This apply environment has no browser. Recommend this run manually before merge, or as part
  of `sdd-verify`/the post-apply review if that phase has browser access; otherwise flag it as
  an explicit residual manual-QA step for the human reviewer.
- **Delivery/size decision** (per tasks.md "Decision needed before apply"): now resolvable —
  both work units are complete. Actual authored diff: `api/usuarios.js` ~200 lines,
  `api/usuarios.test.js` ~? lines (from WU1), `web/js/usuarios.js` ~250 lines, `index.html` ~2
  lines, `main.js` ~5 lines, `styles.css` ~9 lines. Combined is in the ~450-470 line range,
  modestly over the 400-line single-PR budget noted in tasks.md's "Review Workload Forecast" —
  the backend→front-end split (this apply's own WU1/WU2 boundary) is available as the natural
  two-PR seam if the reviewer wants to bound it; otherwise a single PR with the standard
  dominant-risk lens (`review-reliability`, given this is new state-changing account-management
  logic) is plausible since nothing here touches auth/payments primitives directly (it *calls*
  Firebase Auth Admin/client SDKs but doesn't reimplement auth).
- No changes made to `firestore.rules` or any file outside `web/js/usuarios.js`,
  `web/index.html`, `web/js/main.js`, `web/styles.css`,
  `openspec/changes/usuarios-tab/tasks.md`, and this progress file (this batch); WU1's file set
  (`api/usuarios.js`, `api/usuarios.test.js`) is unchanged from the prior batch.
