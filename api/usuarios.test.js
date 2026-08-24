// Self-check for the pure predicates in api/usuarios.js. Run: node api/usuarios.test.js
const assert = require('assert');
const u = require('./usuarios.js');

// ---- classify: fixture UserRecord-shaped objects ---------------------------
// Order matters — inspector domain must win over generic "password" provider
// even though inspectors ARE password-provider accounts (ADR-2's core claim).
const inspector = { email: 'cedula@sismocali.gov.co', providerData: [{ providerId: 'password' }] };
const admin = { email: 'admin@example.com', providerData: [{ providerId: 'password' }] };
const viewer = { email: 'viewer@cali.gov.co', providerData: [{ providerId: 'google.com' }] };
const otro = { email: 'stray@gmail.com', providerData: [{ providerId: 'google.com' }] };

assert.strictEqual(u.classify(inspector), 'inspector');
assert.strictEqual(u.classify(admin), 'admin');
assert.strictEqual(u.classify(viewer), 'viewer');
assert.strictEqual(u.classify(otro), 'otro');

// ---- last-admin count: fixture list, 1 enabled admin + N others -----------
const fixtureUsers = [
  { uid: 'admin-1', disabled: false, ...admin },
  { uid: 'viewer-1', disabled: false, ...viewer },
  { uid: 'inspector-1', disabled: false, ...inspector },
  { uid: 'admin-disabled', disabled: true, email: 'old-admin@example.com', providerData: [{ providerId: 'password' }] },
];

// Delete of the sole enabled admin is blocked.
const blocked = u.checkDeleteGuards(fixtureUsers, 'admin-1', 'admin-1-not-caller');
assert.ok(blocked && blocked.status === 403, 'last enabled admin delete must be blocked');

// Delete of a viewer is allowed (guards pass -> null).
const allowed = u.checkDeleteGuards(fixtureUsers, 'viewer-1', 'admin-1-not-caller');
assert.strictEqual(allowed, null, 'non-admin delete must be allowed');

// A second enabled admin unblocks the delete of the first.
const fixtureTwoAdmins = [...fixtureUsers, { uid: 'admin-2', disabled: false, email: 'admin2@example.com', providerData: [{ providerId: 'password' }] }];
assert.strictEqual(u.checkDeleteGuards(fixtureTwoAdmins, 'admin-1', 'admin-2'), null, 'delete allowed when another enabled admin remains');

// ---- self-uid delete blocked regardless of role ----------------------------
assert.strictEqual(u.checkDeleteGuards(fixtureUsers, 'viewer-1', 'viewer-1').status, 403);
assert.strictEqual(u.checkDeleteGuards(fixtureUsers, 'admin-1', 'admin-1').status, 403);
assert.strictEqual(u.checkDeleteGuards(fixtureUsers, 'inspector-1', 'inspector-1').status, 403);

// ---- isValidPassword sanity (Firebase minimum) -----------------------------
assert.strictEqual(u.isValidPassword('Cali2026+-'), true);
assert.strictEqual(u.isValidPassword('12345'), false);

console.log('usuarios.test.js OK');
