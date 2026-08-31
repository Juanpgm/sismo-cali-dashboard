// Node self-check for mapview.js's ensureGeo promise memoization (via
// resolveBarrioComuna). Run: node web/js/mapview.test.mjs
import assert from 'node:assert/strict';

let fetchCalls = 0;
let failNext = false;
globalThis.fetch = async () => {
  fetchCalls += 1;
  if (failNext) return { ok: false };
  return { ok: true, json: async () => ({ features: [] }) };
};

const { resolveBarrioComuna } = await import('./mapview.js');

// 1. A failed load rejects every concurrent caller but does NOT poison the
//    cache: the in-flight slot clears so a later call can retry.
failNext = true;
await assert.rejects(() => resolveBarrioComuna(3.42, -76.53), /No se pudo cargar/);

// 2. Concurrent callers share ONE fetch per file instead of a thundering herd
//    (Evaluaciones fires hundreds of these at once).
failNext = false;
fetchCalls = 0;
const results = await Promise.all(
  Array.from({ length: 50 }, () => resolveBarrioComuna(3.42, -76.53)),
);
assert.equal(fetchCalls, 2, `expected 2 fetches (comunas+barrios), got ${fetchCalls}`);
assert.deepEqual(results[0], { comuna: null, barrio: null });

// 3. Once parsed, later calls fetch nothing at all.
fetchCalls = 0;
await resolveBarrioComuna(3.42, -76.53);
assert.equal(fetchCalls, 0);

console.log('ok — mapview.js ensureGeo single-flight geo load');
