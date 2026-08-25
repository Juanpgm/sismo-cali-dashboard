// Self-check for the pure `autoAgrupar` clustering function in
// api/sticker-asignaciones.js. Run: node api/sticker-asignaciones.test.js
const assert = require('assert');
const sa = require('./sticker-asignaciones.js');

const pt = (id, lat, lon) => ({ id, coords: { lat, lon } });

// ---- determinism: same input twice -> identical group membership ----------
const detFixture = [pt('a', 3.40, -76.50), pt('b', 3.4001, -76.5001), pt('c', 3.50, -76.60)];
const groupsIds = (groups) => groups.map((g) => g.map((p) => p.id).sort());

const run1 = groupsIds(sa.autoAgrupar(detFixture, { maxRadiusM: 800, maxSize: 8 }));
const run2 = groupsIds(sa.autoAgrupar(detFixture, { maxRadiusM: 800, maxSize: 8 }));
assert.deepStrictEqual(run1, run2, 'same input twice must produce identical group membership');
// a and b are ~15m apart (within 800m), c is >10km away -> 2 groups, a+b together
assert.strictEqual(run1.length, 2, 'expected 2 groups: [a,b] close, [c] far');
assert.deepStrictEqual(run1.find((g) => g.includes('a')), ['a', 'b']);

// ---- maxSize cap: a cluster of points closer than maxRadiusM but exceeding
// maxSize -> no resulting group larger than maxSize -----------------------
const denseFixture = Array.from({ length: 10 }, (_, i) => pt(`p${i}`, 3.40 + i * 0.00001, -76.50));
const capped = sa.autoAgrupar(denseFixture, { maxRadiusM: 800, maxSize: 3 });
for (const g of capped) {
  assert.ok(g.length <= 3, `group size ${g.length} exceeds maxSize 3`);
}
assert.strictEqual(
  capped.reduce((n, g) => n + g.length, 0),
  denseFixture.length,
  'every point must end up in exactly one group',
);

// ---- radius cap: a point farther than maxRadiusM from every seed is never
// added to that seed's group --------------------------------------------
const radiusFixture = [pt('seed', 3.40, -76.50), pt('far', 3.50, -76.60)];
const radiusGroups = groupsIds(sa.autoAgrupar(radiusFixture, { maxRadiusM: 100, maxSize: 8 }));
assert.strictEqual(radiusGroups.length, 2, 'far point must not join the seed group');
assert.deepStrictEqual(radiusGroups, [['seed'], ['far']]);

// ---- empty input -> [] , no error ------------------------------------------
assert.deepStrictEqual(sa.autoAgrupar([], { maxRadiusM: 800, maxSize: 8 }), []);

console.log('sticker-asignaciones.test.js OK');
