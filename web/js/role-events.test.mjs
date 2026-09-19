// Self-check for the tiny role-change event hub (PR 10 part 2, task 11.28):
// auth.js announces the effective role, main.js clears Seguimiento's retained
// admin data when it changes (sign-out included).
// Run: node web/js/role-events.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { onRoleChange, announceRole } from './role-events.js';

const seen = [];
const off = onRoleChange((next, prev) => seen.push([prev, next]));

// A first announcement is a change from "no role" (null).
assert.equal(announceRole('admin'), true);
assert.deepStrictEqual(seen, [[null, 'admin']]);

// The same role again (a Firebase token refresh re-fires onAuthStateChanged) is NOT a change.
assert.equal(announceRole('admin'), false);
assert.equal(seen.length, 1, 'a same-role announcement must not clear anything');

// admin -> viewer is a change; so is sign-out (null) and coming back.
assert.equal(announceRole('viewer'), true);
assert.equal(announceRole(null), true);
assert.equal(announceRole(undefined), false, 'undefined normalises to null: still signed out, no change');
assert.equal(announceRole('admin'), true);
assert.deepStrictEqual(seen, [
  [null, 'admin'], ['admin', 'viewer'], ['viewer', null], [null, 'admin'],
]);

// Judgment-day W2: the session key is `uid|role`, not the role alone. Admin A ->
// admin B is a change (A's data must never be painted to B); the SAME user's
// token refresh (same uid + role) is not.
{
  announceRole(null);
  const events = [];
  const offUid = onRoleChange((next, prev, detail) => events.push([prev, next, detail.previousUid, detail.uid]));
  assert.equal(announceRole('admin', 'uid-A'), true);
  assert.equal(announceRole('admin', 'uid-A'), false, 'same uid + same role (token refresh): NOT a change');
  assert.equal(announceRole('admin', 'uid-B'), true, 'same role, different uid: a change');
  assert.equal(announceRole('admin', 'uid-B'), false);
  assert.equal(announceRole('viewer', 'uid-B'), true, 'same uid, different role: a change');
  assert.equal(announceRole(null), true, 'sign-out');
  assert.equal(announceRole(null, 'uid-stale'), false, 'a signed-out session has no identity: a stray uid never counts');
  assert.equal(announceRole('admin', 'uid-B'), true, 'the same user coming back after a sign-out is a NEW session');
  assert.equal(announceRole('admin'), true, 'a missing uid is a different session than uid-B (fail closed)');
  assert.equal(announceRole('admin', ''), false, 'an empty uid normalises to "no uid"');
  assert.equal(announceRole('admin', undefined), false);
  assert.deepStrictEqual(events, [
    [null, 'admin', null, 'uid-A'],
    ['admin', 'admin', 'uid-A', 'uid-B'],
    ['admin', 'viewer', 'uid-B', 'uid-B'],
    ['viewer', null, 'uid-B', null],
    [null, 'admin', null, 'uid-B'],
    ['admin', 'admin', 'uid-B', null],
  ]);
  offUid();
  announceRole(null);
}

// A throwing listener never breaks the announcer nor the other listeners, and
// writes nothing to the console (an auth flow must not depend on a listener).
const consoleCalls = [];
const savedError = console.error;
const savedWarn = console.warn;
console.error = (...a) => consoleCalls.push(a);
console.warn = (...a) => consoleCalls.push(a);
const offBad = onRoleChange(() => { throw new Error('listener bug'); });
const seenAfterBad = [];
const offGood = onRoleChange((next) => seenAfterBad.push(next));
assert.doesNotThrow(() => announceRole('viewer'));
console.error = savedError;
console.warn = savedWarn;
assert.deepStrictEqual(seenAfterBad, ['viewer'], 'listeners after a throwing one still run');
assert.deepStrictEqual(consoleCalls, []);

// Unsubscribing works.
off(); offBad(); offGood();
const before = seen.length;
announceRole('admin');
assert.equal(seen.length, before, 'an unsubscribed listener is never called again');

// Wiring guard (auth.js needs the Firebase CDN, so it cannot be imported under
// Node): auth.js announces every session transition (role + uid), main.js
// subscribes the Seguimiento session wiring (teardown + re-open/leave).
const auth = readFileSync(new URL('./auth.js', import.meta.url), 'utf8');
const main = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
assert.match(auth, /import \{ announceRole \} from '\.\/role-events\.js'/);
assert.match(auth, /announceRole\(null\);/, 'sign-out announces a null role');
assert.match(auth, /announceRole\(role, user\.uid\);/, 'a resolved role is announced WITH the uid (admin A -> admin B is a change)');
assert.ok(
  auth.indexOf('currentRole = role;') < auth.indexOf('announceRole(role, user.uid);'),
  'the role is updated BEFORE listeners run (they may read getRole())',
);
// Judgment-day S2: the body's data-role is applied before listeners run, on
// both transitions (they may read it, and the re-open gating relies on it).
assert.ok(
  auth.indexOf('document.body.dataset.role = role;') < auth.indexOf('announceRole(role, user.uid);'),
  'data-role is set BEFORE the role is announced',
);
assert.ok(
  auth.indexOf('delete document.body.dataset.role;') < auth.indexOf('announceRole(null);'),
  'data-role is removed BEFORE the sign-out is announced',
);
assert.match(main, /import \{ wireSeguimientoSession \} from '\.\/seguimiento-session\.js'/);
assert.match(main, /wireSeguimientoSession\(\{[\s\S]*?getRoot: \(\) => document\.getElementById\('view-seguimiento'\)[\s\S]*?isViewActive: \(\) => currentView === 'seguimiento'[\s\S]*?reopen: \(\) => switchView\('seguimiento'\)[\s\S]*?leave: \(\) => switchView\('panel'\)[\s\S]*?\}\);/, 'main.js wires the session teardown to the view state');
assert.doesNotMatch(main, /onRoleChange\(\(\) => clearSeguimientoSnapshot\(\)\)/, 'clearing only the in-memory snapshot (the PII leak) is gone');

// Judgment-day S7: the hub and the session wiring keep no data anywhere but memory.
for (const file of ['./role-events.js', './seguimiento-session.js']) {
  assert.doesNotMatch(readFileSync(new URL(file, import.meta.url), 'utf8'), /localStorage|sessionStorage|indexedDB|caches\.open/, `${file} references no persistence API`);
}

console.log('role-events.test.mjs: all assertions passed');
