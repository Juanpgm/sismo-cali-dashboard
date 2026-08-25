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

// ---- pointsAlreadyAssigned — uniqueness guard (a point -> one cuadrilla) ---
const guardFixture = [
  { id: 'free', cuadrilla_id: null },
  { id: 'in_c1', cuadrilla_id: 'c1' },
  { id: 'in_c2', cuadrilla_id: 'c2' },
];
// crearCuadrilla case (target null): any already-grouped point conflicts.
assert.deepStrictEqual(sa.pointsAlreadyAssigned(guardFixture, null), ['in_c1', 'in_c2']);
// editarCuadrilla case (adding to c1): points already in c1 are fine, others conflict.
assert.deepStrictEqual(sa.pointsAlreadyAssigned(guardFixture, 'c1'), ['in_c2']);
// all free -> no conflicts; empty/undefined input -> [].
assert.deepStrictEqual(sa.pointsAlreadyAssigned([{ id: 'a', cuadrilla_id: null }], null), []);
assert.deepStrictEqual(sa.pointsAlreadyAssigned([], null), []);
assert.deepStrictEqual(sa.pointsAlreadyAssigned(undefined, null), []);

// ---- commitInChunks — never exceeds 500 ops/batch, covers every item -------
async function checkChunks(n) {
  const commits = [];
  let current = [];
  const fakeDb = {
    batch: () => ({
      set: (...a) => current.push(a),
      delete: (...a) => current.push(a),
      commit: async () => { commits.push(current); current = []; },
    }),
  };
  const items = Array.from({ length: n }, (_, i) => i);
  await sa.commitInChunks(fakeDb, items, (batch, item) => batch.set(item));
  const total = commits.reduce((s, c) => s + c.length, 0);
  assert.strictEqual(total, n, `all ${n} items must be written`);
  for (const c of commits) assert.ok(c.length <= 500, `batch of ${c.length} exceeds 500`);
  return commits.length;
}
(async () => {
  assert.strictEqual(await checkChunks(0), 0, 'empty list -> no commits');
  assert.strictEqual(await checkChunks(500), 1, '500 items -> exactly one batch');
  assert.strictEqual(await checkChunks(501), 2, '501 items -> two batches');
  assert.strictEqual(await checkChunks(1101), 3, '1101 items -> three batches');
  console.log('sticker-asignaciones.test.js OK');
})();
