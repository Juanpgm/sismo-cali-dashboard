// Self-check for the pure predicates in api/usuarios.js. Run: node api/usuarios.test.js
const assert = require('assert');
const u = require('./usuarios.js');

// ---- classify: fixture UserRecord-shaped objects ---------------------------
// Role model (see refresh.js roleFrom): superadmin email > custom claim >
// @sismocali (inspector) > password (usuario) > google@cali (viewer) > otro.
const inspector = { email: 'cedula@sismocali.gov.co', providerData: [{ providerId: 'password' }] };
const usuario = { email: 'someone@example.com', providerData: [{ providerId: 'password' }] };
const admin = { email: 'boss@example.com', providerData: [{ providerId: 'password' }], customClaims: { role: 'admin' } };
const superadmin = { email: 'juanp.gzmz@gmail.com', providerData: [{ providerId: 'password' }] };
const viewer = { email: 'viewer@cali.gov.co', providerData: [{ providerId: 'google.com' }] };
const otro = { email: 'stray@gmail.com', providerData: [{ providerId: 'google.com' }] };

assert.strictEqual(u.classify(inspector), 'inspector'); // @sismocali wins over password
assert.strictEqual(u.classify(usuario), 'usuario');     // password default is usuario, NOT admin
assert.strictEqual(u.classify(admin), 'admin');         // explicit custom claim
assert.strictEqual(u.classify(superadmin), 'admin');    // superadmin email, no claim needed
assert.strictEqual(u.classify(viewer), 'viewer');
assert.strictEqual(u.classify(otro), 'otro');
// A claim overrides the derived default (a password user promoted to admin).
assert.strictEqual(u.classify({ ...usuario, customClaims: { role: 'admin' } }), 'admin');

// ---- last-admin count: fixture list, 1 enabled admin + N others -----------
const fixtureUsers = [
  { uid: 'admin-1', disabled: false, ...admin },
  { uid: 'viewer-1', disabled: false, ...viewer },
  { uid: 'usuario-1', disabled: false, ...usuario },
  { uid: 'inspector-1', disabled: false, ...inspector },
  { uid: 'admin-disabled', disabled: true, email: 'old@example.com', providerData: [{ providerId: 'password' }], customClaims: { role: 'admin' } },
];

// Delete of the sole enabled admin is blocked.
const blocked = u.checkDeleteGuards(fixtureUsers, 'admin-1', 'admin-1-not-caller');
assert.ok(blocked && blocked.status === 403, 'last enabled admin delete must be blocked');

// Delete of a plain usuario/viewer is allowed (guards pass -> null).
assert.strictEqual(u.checkDeleteGuards(fixtureUsers, 'viewer-1', 'admin-1-not-caller'), null, 'non-admin delete must be allowed');
assert.strictEqual(u.checkDeleteGuards(fixtureUsers, 'usuario-1', 'admin-1-not-caller'), null, 'usuario delete must be allowed');

// A second enabled admin unblocks the delete of the first.
const fixtureTwoAdmins = [...fixtureUsers, { uid: 'admin-2', disabled: false, email: 'a2@example.com', providerData: [{ providerId: 'password' }], customClaims: { role: 'admin' } }];
assert.strictEqual(u.checkDeleteGuards(fixtureTwoAdmins, 'admin-1', 'admin-2'), null, 'delete allowed when another enabled admin remains');

// ---- self-uid delete blocked regardless of role ----------------------------
assert.strictEqual(u.checkDeleteGuards(fixtureUsers, 'viewer-1', 'viewer-1').status, 403);
assert.strictEqual(u.checkDeleteGuards(fixtureUsers, 'admin-1', 'admin-1').status, 403);
assert.strictEqual(u.checkDeleteGuards(fixtureUsers, 'inspector-1', 'inspector-1').status, 403);

// ---- isValidPassword sanity (Firebase minimum) -----------------------------
assert.strictEqual(u.isValidPassword('Cali2026+-'), true);
assert.strictEqual(u.isValidPassword('12345'), false);

console.log('usuarios.test.js OK');
