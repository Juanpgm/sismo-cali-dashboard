// Session wiring for the Seguimiento tab (judgment-day C1/W2, design D28).
//
// Seguimiento renders ~370 names/cédulas (PII) into #view-seguimiento, and the
// panel is only ever HIDDEN by switchView: auth.js boots the app once per tab
// (`booted`), so after a sign-out or a role/user change the previous admin's
// table would still sit in the DOM for the next user. This module subscribes to
// the role-event hub (uid + role, see role-events.js) and, on EVERY session
// change, empties the view and cancels everything in flight
// (teardownSeguimiento). Then:
//   - the next session is an admin and Seguimiento is the current tab
//     -> `reopen()` (main.js: switchView('seguimiento'), i.e. a fresh init);
//   - the next session is a non-admin and Seguimiento is the current tab
//     -> `leave()` (main.js: switchView('panel')): the panel stays blank;
//   - signed out, or another tab is current -> nothing more to do (the login
//     overlay covers the app; the tab initializes itself when opened).
//
// Kept apart from main.js (which pulls in the whole app and cannot be imported
// by Node tests) so the real logic is exercised by the Node suite.
import { onRoleChange } from './role-events.js';
import { teardownSeguimiento } from './seguimiento.js';

/** Subscribes the wiring; returns the unsubscribe fn.
 *  - `getRoot()`: the #view-seguimiento element;
 *  - `isViewActive()`: whether Seguimiento is the current view;
 *  - `reopen()` / `leave()`: see above. */
export function wireSeguimientoSession({
  getRoot, isViewActive, reopen, leave,
}) {
  return onRoleChange((role) => {
    // A failing step must never skip the view gating below, and nothing here
    // may break the auth flow (announceRole also contains a throwing listener).
    let root = null;
    try { root = getRoot(); } catch { root = null; }
    try { teardownSeguimiento(root); } catch { /* the module's own steps are contained; nothing more to do */ }
    let active = false;
    try { active = Boolean(isViewActive()); } catch { active = false; }
    if (!active || !role) return;
    if (role === 'admin') reopen();
    else leave();
  });
}
