// Role-change event hub (seguimiento-inspectores-depurado PR 10 part 2, task
// 11.28 / design D28): auth.js announces the effective session (role + uid)
// after every auth state change, and modules that hold role-scoped data in
// memory or in the DOM (Seguimiento's retained admin snapshot and its table)
// subscribe to drop it. A separate, dependency-free module because auth.js
// needs the Firebase CDN and cannot be imported by Node tests, and because
// auth.js must not depend on the feature modules.
//
// A "change" is any transition of the session key `uid|role` (`null` role =
// signed out): admin -> viewer, admin -> sign-out, sign-out -> admin, and —
// judgment-day W2 — admin A -> admin B (same role, different uid: A's data must
// never be painted to B). Re-announcing the same uid + role (a token refresh
// re-fires onAuthStateChanged) is NOT a change, so nothing is refetched.

let lastRole = null;
let lastUid = null;
const listeners = new Set();

/** Subscribes `listener(nextRole, previousRole, { uid, previousUid })`;
 *  returns the unsubscribe fn. */
export function onRoleChange(listener) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Records the current session; returns true (and notifies) only when the
 *  role or the uid changed. A signed-out session (`null` role) has no
 *  identity: its uid is always null. A throwing listener never breaks the
 *  auth flow or the other listeners. */
export function announceRole(role, uid = null) {
  const next = role || null;
  const nextUid = next ? (uid || null) : null;
  const previous = lastRole;
  const previousUid = lastUid;
  if (next === previous && nextUid === previousUid) return false;
  lastRole = next;
  lastUid = nextUid;
  for (const listener of [...listeners]) {
    try {
      listener(next, previous, { uid: nextUid, previousUid });
    } catch {
      // Intentionally silent: an observer bug must not affect sign-in/out.
    }
  }
  return true;
}
