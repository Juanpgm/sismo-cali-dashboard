// Runnable self-check (assert-based, no framework): `node api/inspector-asignaciones.test.js`.
const assert = require('assert');
const sa = require('./inspector-asignaciones.js');

// pendiente: everything not yet 'hecho' still shows in the inspector's picker.
assert.strictEqual(sa.pendiente({ estado_asignacion: 'asignado' }), true);
assert.strictEqual(sa.pendiente({ estado_asignacion: 'pendiente' }), true);
assert.strictEqual(sa.pendiente({ estado_asignacion: 'en_proceso' }), true);
assert.strictEqual(sa.pendiente({ estado_asignacion: 'hecho' }), false, 'done points drop off the list');
assert.strictEqual(sa.pendiente(null), false);

console.log('inspector-asignaciones.test.js OK');
