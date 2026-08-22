// Self-check for pure logic in data.js. Run: node web/js/data.test.mjs
import assert from 'node:assert/strict';
// Imported from utils.js (not data.js): data.js transitively imports the Firebase
// SDK via israel-source.js -> firebase-config.js -> a bare https:// specifier,
// which Node's ESM loader can't resolve, so data.js can't be loaded standalone
// here. The logic itself lives in utils.js; data.js re-exports it unchanged.
import { bustParams, suspensionServicios, bucketNpisos } from './utils.js';

// --- bustParams ------------------------------------------------------------
// A.1: normal startup load MUST NOT cache-bust; retry/refresh/poll paths MUST.
assert.deepEqual(bustParams(false), { q: '', opts: {} });
{
  const { q, opts } = bustParams(true);
  assert.ok(/^\?t=\d+$/.test(q), `expected ?t=<digits>, got "${q}"`);
  assert.deepEqual(opts, { cache: 'no-store' });
}

// --- suspensionServicios ----------------------------------------------------
// Truth table: colapso (parcial O total) = 'si' Y no-habitable (I1-I3) => 'si'.
const habitable = { criterio_habitabilidad: 'H' };
const restringido = { criterio_habitabilidad: 'R1' };
const noHabitableI1 = { criterio_habitabilidad: 'I1' };
const noHabitableI3 = { criterio_habitabilidad: 'I3' };

assert.equal(
  suspensionServicios({ colapso_parcial: 'si', colapso_total: 'no', ...noHabitableI1 }),
  'si',
  'colapso_parcial=si + no-habitable => si',
);
assert.equal(
  suspensionServicios({ colapso_parcial: 'no', colapso_total: 'si', ...noHabitableI3 }),
  'si',
  'colapso_total=si + no-habitable => si',
);
assert.equal(
  suspensionServicios({ colapso_parcial: 'no', colapso_total: 'no', ...noHabitableI1 }),
  'no',
  'no colapso => no, regardless of habitability',
);
assert.equal(
  suspensionServicios({ colapso_parcial: 'si', colapso_total: 'no', ...habitable }),
  'no',
  'colapso=si + habitable (H) => no',
);
assert.equal(
  suspensionServicios({ colapso_parcial: 'si', colapso_total: 'no', ...restringido }),
  'no',
  'colapso=si + restringido (R1/R2, not I1-I3) => no',
);

// --- bucketNpisos ------------------------------------------------------------
// Edge cases: missing/invalid/out-of-range -> null; in-range -> bucket of 3, starting at 1.
assert.equal(bucketNpisos(null), null);
assert.equal(bucketNpisos(''), null);
assert.equal(bucketNpisos(undefined), null);
assert.equal(bucketNpisos(NaN), null);
assert.equal(bucketNpisos(0), null); // below range (min 1)
assert.equal(bucketNpisos(1), '1–3 pisos');
assert.equal(bucketNpisos(4), '4–6 pisos');
assert.equal(bucketNpisos(60), '58–60 pisos'); // last valid bucket at the outlier ceiling
assert.equal(bucketNpisos(61), null); // just past the ceiling
assert.equal(bucketNpisos(91980), null); // real outlier seen in prod data

console.log('ok — data.js pure-logic self-check');
