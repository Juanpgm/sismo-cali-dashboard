# Verify report: usuarios-tab

Change: `usuarios-tab` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-verify
Artifact store: openspec (Engram not connected for this change)

## Runnable checks — actual output

```
$ node api/usuarios.test.js
usuarios.test.js OK

$ node api/stickers.test.js
stickers.test.js OK

$ node --check web/js/usuarios.js
(exit 0, no output)
```

`node api/refresh.test.js` does not exist in the repo (`api/` only contains
`stickers.test.js` and the new `usuarios.test.js` — confirmed via glob
`api/*.test.js`). This was requested in the verify instructions but is not a
real file; treated as a non-issue rather than a failure — there is no
regression to check because there was never a test at that path. Regression
sanity is instead `api/stickers.test.js`, which passes unchanged.

## Per-requirement verdicts

### Tab visibility and server-side authorization — MET
- Server: `api/usuarios.js:162-178` — fail-closed preamble: valid Bearer
  token via `verifyFirebaseToken`, `sign_in_provider === 'password'`, caller
  email NOT `@sismocali.gov.co`. Runs before the action router, independent
  of any client state. Rejects with 401/403.
- Client: `web/index.html:75` tab button + `:273` `#view-usuarios` panel;
  `web/styles.css:1559-1560` hides `.view-tab[data-view="usuarios"]` for
  `body[data-role="viewer"]`. `main.js:201-204` lazily calls `initUsuarios`
  only when the tab is switched to.
- Evidence for "viewer token rejected server-side" is by code-path
  inspection only (no live browser call was made in this environment) — see
  Task 2.9 caveat below.

### Superset user listing — MET
- `api/usuarios.js:63-73` `listUsuarios` — `listUsers(1000)` (ceiling
  comment carried forward at `:61-62`, matches `stickers.js:65`'s pattern),
  maps every `UserRecord` to `{ uid, email, role, disabled, lastSignInTime,
  creationTime }`. No `evaluaciones`/`events` join — matches the "keep list
  lean" design decision and the spec's "MUST NOT claim login/download counts
  in v1" line.
- `classify(u)` order (`api/usuarios.js:33-39`) is: inspector-domain check
  first (`email.endsWith(INSPECTOR_DOMAIN)`) → generic `password` provider
  → `google.com` + `@cali.gov.co` → `otro`. This is the critical ordering
  the spec calls out ("inspectors are also password-provider"); verified
  correct both by reading the code and by `usuarios.test.js:8-16`, which
  fixtures an inspector email with `providerId: 'password'` and asserts
  `classify` returns `'inspector'`, not `'admin'`.

### Role, domain, and status filtering — MET
- `web/js/usuarios.js:96-107` role select (admin/viewer/inspector/otro)
  plus status select (activo/inhabilitado), client-side filter over the
  already-fetched array (`currentFiltered()` at `:165-167`), re-render on
  change (`:193-200`). No `multiselect.js` — matches ADR-5's explicit
  rejection.

### In-tab stat chips — MET
- `web/js/usuarios.js:66-78` `chipsHtml(usuarios)` is called with the raw
  (unfiltered) `usuarios` array (`rosterHtml(usuarios, filtered, ...)` at
  `:170`, `chipsHtml(usuarios)` inside at `:89` receives the first,
  unfiltered arg, not `filtered`). Total/activos/inhabilitados plus per-role
  (admin/viewer/inspector) counts. `otro` has no dedicated chip — a
  documented, intentional narrowing (matches ADR-5's locked chip shape;
  `otro` rows stay visible/filterable, just not counted as a chip) —
  SUGGESTION, not a spec violation, since the spec only requires "total,
  active, disabled, plus per-role counts" without naming `otro`.

### Create user — MET
- `api/usuarios.js:81-91` `createUsuario` — validates email format, rejects
  `@sismocali.gov.co` (`:85-87`, message points to Stickers), validates
  password ≥6 chars, `createUser({email,password})`. No Firestore write, so
  no rollback branch — this is a documented narrowing of the spec's
  "rolling back the Auth user if the Firestore write fails" line, locked in
  `design.md` ADR-1 and `tasks.md` 1.4 as accepted for the admin-create path
  specifically (admins have no Firestore profile). This narrowing was an
  explicit "decision needed before apply" item in tasks.md's forecast and
  was carried through apply without contradiction — treated as correctly
  implemented against the locked design, not a gap.

### Disable / enable user — MET
- `api/usuarios.js:93-106` `setEnabled` — self-guard first (`:97`, rejects
  before any read/write), `updateUser({disabled: !enabled})`, then syncs
  `inspectores/{uid}.activo = enabled` only when `classify(target) ===
  'inspector'` (`:102-104`) — correctly scoped (admins/viewers have no
  `activo` doc, matches ADR-3's reasoning).

### Delete user with anti-lockout guards — MET
- `api/usuarios.js:127-142` `deleteUsuario` — loads a fresh `listUsers(1000)`
  snapshot, runs `checkDeleteGuards` before any write, then `deleteUser` +
  best-effort `inspectores/{uid}` delete (`.catch(() => {})` — deletion
  failure of the Firestore doc does not roll back or block the Auth delete;
  reasonable for a non-inspector target, and for an inspector target it is a
  soft-fail rather than leaving a half-deleted transaction pending —
  WARNING-level, not blocking: if the Firestore delete fails for an
  inspector, the Auth account is gone but `inspectores/{uid}` survives as an
  orphan, silently. Not called out explicitly in spec/design, low risk).
- `evaluaciones` are never touched anywhere in `usuarios.js` — MET, matches
  "leave evaluaciones records intact".
- Guard logic (`checkDeleteGuards`, `:113-122`): self-check first
  (`targetUid === callerUid` → 403), then last-admin check only for
  `isEnabledAdmin(target)` targets, `enabledAdmins = users.filter
  (isEnabledAdmin).length`, blocks if `<= 1`. `isEnabledAdmin` (`:41`) equals
  `!u.disabled && classify(u) === 'admin'` — exactly matches the spec's
  "last admin = enabled password non-@sismocali count" definition (since
  `classify` returns `'admin'` only for password-provider, non-inspector,
  and viewer/otro never reach `'admin'`).
- `usuarios.test.js:26-41` exercises: sole-enabled-admin delete blocked
  (403), viewer delete allowed (null), delete unblocked when a 2nd enabled
  admin exists, and self-uid delete blocked regardless of role
  (admin/viewer/inspector all rejected). All assertions pass per the actual
  test run above.
- Client courtesy hide of the caller's own disable/delete buttons
  (`web/js/usuarios.js:40,47-50`) is explicitly commented as
  non-authoritative, correctly deferring to the server guard.

### Send password reset — MET
- `web/js/usuarios.js:10,278-296` — `sendPasswordResetEmail(getAuth
  (getFirebaseApp()), email)` from the Firebase Auth client SDK, no
  `/api/usuarios` call. Matches the spec and design's locked decision
  exactly.

## Tab wiring — MET
- `web/index.html:75,273` — button + panel present, next to Stickers'.
- `web/js/main.js:13,201-204` — import + lazy init inside `switchView()`,
  same pattern as Stickers (`:198-200`), no early-return guard (matches
  ADR-6, unlike suspended Acciones at `:185`).
- No DOM-duplication risk on reopen: `initUsuarios` always starts with
  `root.innerHTML = shellHtml()` (`usuarios.js:162`), replacing the whole
  subtree each time the tab is switched to — same pattern as `initStickers`
  (`stickers.js:140`). Confirmed by reading both files; this is the same
  lifecycle already proven safe by Stickers in production.
- `web/styles.css:1559-1560` viewer-hide rule includes `usuarios`;
  `:1699-1704` adds only `.usuario-filters`/`.usuario-row`/`.usuario-actions`,
  reusing `.sticker-*` for everything else — matches ADR-6's "no
  `#view-usuarios`-specific display override" instruction (confirmed no such
  rule exists).

## Known, documented gaps (non-blocking)

1. Mobile CSS collapse (`styles.css:1818-1820`) — the `.sticker-row` mobile
   media query collapses to a 3-column grid and gives `.sticker-action`
   full-width treatment; `.usuario-row`'s `.usuario-actions` (a
   flex-wrapped group, not individual grid children) does not get an
   equivalent override, so it will likely render cramped on narrow screens.
   Confirmed present: the media query at `:1818-1820` only targets
   `.sticker-row .sticker-action`, not `.usuario-actions`. Documented in
   `apply-progress.md` with a `ponytail:` comment pointer, though the
   comment itself was placed in the progress doc, not inline in
   `styles.css` — a minor process gap, not a code gap. SUGGESTION: add the
   inline `ponytail:` comment in `styles.css` itself for future readers.
2. Task 2.9 (manual browser smoke test) — NOT run, correctly left unchecked
   in `tasks.md`. This verify pass is also non-browser and cannot execute
   it. Per-requirement code-path verification above stands in for it, but
   the actual browser round trip (login as admin, open tab,
   disable→enable→delete round trip, reset-password click, login as viewer
   and confirm tab absence + direct-fetch rejection) remains an outstanding
   manual-QA item before merge/production use. Reported honestly, not
   fabricated.
3. Best-effort Firestore delete (`api/usuarios.js:140`) — if
   `inspectores/{uid}` delete fails after `deleteUser` succeeds, the Auth
   account is gone but the Firestore doc survives silently (no retry, no
   surfaced warning). Low-probability, low-impact (an orphaned profile doc
   for a non-existent Auth user), not spec-mandated to handle differently.
4. `otro` bucket has no stat chip — intentional per ADR-5's locked chip
   shape; `otro` rows remain visible and filterable. Not a spec gap.

## Scope check: event-logging correctly absent

Confirmed `web/js/events.js` does not exist, no Firestore client SDK write
hooks were added to `auth.js`/`main.js`, and no `firestore.rules` file was
touched. `design.md` ADR-4 explicitly supersedes/descopes this to Phase 2
with a documented rationale (console-managed rules not in this repo;
client-write tamper surface). Implementation matches the v1 scope exactly —
no drift.

## Overall verdict: PASS-WITH-CAVEATS

All eight spec requirements are MET with concrete code evidence. Both
runnable self-checks (`api/usuarios.test.js`, `api/stickers.test.js`
regression) pass, and `web/js/usuarios.js` is syntactically valid. The
caveats: (a) task 2.9's manual browser smoke test is a genuinely outstanding
manual-QA item — not run here or in apply, and it should be run before this
ships to real users; (b) one minor CSS mobile-layout gap and one
best-effort-delete edge case, both low-severity and already documented by
the implementer. No CRITICAL findings block archive on their own — the
manual smoke test is a pre-merge/pre-production gate, not a code defect —
but it should not be silently skipped.

- CRITICAL: 0
- WARNING: 2 (best-effort Firestore-delete orphan risk; mobile CSS cramped
  layout for `.usuario-actions`)
- SUGGESTION: 2 (no chip for `otro`; move the `ponytail:` mobile-gap comment
  inline into `styles.css`)
- Outstanding manual step: Task 2.9 browser smoke test (not a code defect,
  but not yet performed by anyone — flag before production sign-off)
