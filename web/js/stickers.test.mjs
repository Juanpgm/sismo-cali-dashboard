// Self-check for the pure helpers behind the Stickers tab's fetch layer.
// Run: node web/js/stickers.test.mjs
import assert from 'node:assert/strict';
import { tagFuente, errorMessageFor } from './stickers.js';

// --- tagFuente: a record's own `fuente` wins over the response's top-level one
assert.deepStrictEqual(
  tagFuente({ fuente: 'atencionsismo', evaluaciones: [{ id: '1', fuente: 'firestore' }], degraded: false }),
  { evaluaciones: [{ id: '1', fuente: 'firestore' }], degraded: false },
  'a record carrying its own fuente must not be overwritten by the response fuente',
);

// --- tagFuente: missing top-level `fuente` -> defaults to 'firestore' (GET
// /evaluaciones's legacy shape, which predates the field).
assert.deepStrictEqual(
  tagFuente({ evaluaciones: [{ id: '1' }], degraded: false }),
  { evaluaciones: [{ id: '1', fuente: 'firestore' }], degraded: false },
);

// --- tagFuente: non-array `evaluaciones` -> [] (never throws on a malformed
// payload)
assert.deepStrictEqual(tagFuente({ fuente: 'atencionsismo', evaluaciones: null, degraded: false }).evaluaciones, []);
assert.deepStrictEqual(tagFuente({ fuente: 'atencionsismo', degraded: false }).evaluaciones, []);
assert.deepStrictEqual(tagFuente({ fuente: 'atencionsismo', evaluaciones: 'oops', degraded: false }).evaluaciones, []);

// --- tagFuente: degraded coerced to a real boolean
assert.strictEqual(tagFuente({ evaluaciones: [] }).degraded, false);
assert.strictEqual(tagFuente({ evaluaciones: [], degraded: 1 }).degraded, true);

// --- tagFuente: multiple records, mixed own-fuente / fallback
assert.deepStrictEqual(
  tagFuente({
    fuente: 'atencionsismo',
    evaluaciones: [{ id: 'a', fuente: 'firestore' }, { id: 'b' }],
    degraded: false,
  }).evaluaciones,
  [{ id: 'a', fuente: 'firestore' }, { id: 'b', fuente: 'atencionsismo' }],
);

console.log('ok — tagFuente');

// --- errorMessageFor: structured `error` wins ------------------------------
assert.strictEqual(errorMessageFor({ error: 'boom' }, 500), 'boom');

// --- errorMessageFor: string `detail` used when there is no `error` --------
assert.strictEqual(errorMessageFor({ detail: 'Sesión expirada' }, 401), 'Sesión expirada');

// --- errorMessageFor: non-string `detail` (e.g. a FastAPI validation-error
// array/object) must NOT stringify to "[object Object]" — fall back to the
// generic status message instead.
assert.strictEqual(errorMessageFor({ detail: [{ msg: 'invalid' }] }, 422), 'Error 422');
assert.strictEqual(errorMessageFor({ detail: { msg: 'invalid' } }, 500), 'Error 500');
assert.strictEqual(errorMessageFor({}, 503), 'Error 503');
assert.strictEqual(errorMessageFor(null, 500), 'Error 500');

console.log('ok — errorMessageFor');
console.log('stickers.test.mjs: all assertions passed');
