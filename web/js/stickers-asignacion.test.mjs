// Self-check for the pure table/map logic behind the Asignación sub-section.
// Run: node web/js/stickers-asignacion.test.mjs
import assert from 'node:assert/strict';
import {
  colorForPunto, buildRows, sortRows, filterRows,
} from './stickers-asignacion.js';

// ---- colorForPunto — spec.md "Map view — 3-color legend" scenarios --------
assert.equal(colorForPunto({ tiene_sticker: true, estado_asignacion: 'pendiente' }), 'blue');
assert.equal(colorForPunto({ tiene_sticker: false, estado_asignacion: 'pendiente' }), 'red');
assert.equal(colorForPunto({ tiene_sticker: false, estado_asignacion: 'asignado' }), 'amber');
assert.equal(colorForPunto({ tiene_sticker: false, estado_asignacion: 'en_proceso' }), 'amber');
// tiene_sticker wins even when also assigned — matched-and-assigned still reads as "done".
assert.equal(colorForPunto({ tiene_sticker: true, estado_asignacion: 'asignado' }), 'blue');

// ---- buildRows — joins puntos with cuadrilla/inspector for the table -------
const puntos = [
  {
    id: 'ede_1', direccion: 'Cra 1', zona_id: 'COMUNA 1', estado_asignacion: 'asignado',
    cuadrilla_id: 'c1', inspector_uid: 'u1', tier: 'alta', tiene_sticker: false,
    coords: { lat: 3.4, lon: -76.5 },
  },
  {
    id: 'ede_2', direccion: 'Cra 2', zona_id: 'COMUNA 2', estado_asignacion: 'pendiente',
    cuadrilla_id: null, inspector_uid: null, tier: null, tiene_sticker: false,
    coords: { lat: 3.5, lon: -76.4 },
  },
];
const cuadrillas = [{ id: 'c1', nombre: 'Cuadrilla 1', puntos: ['ede_1'], inspector_uid: 'u1', origen: 'manual' }];
const inspectores = [{ uid: 'u1', nombre_completo: 'Ana Torres', codigo: '001' }];

const rows = buildRows(puntos, cuadrillas, inspectores);
assert.equal(rows.length, 2);
assert.equal(rows[0].cuadrillaLabel, 'Cuadrilla 1');
assert.equal(rows[0].inspectorLabel, 'Ana Torres');
assert.equal(rows[0].color, 'amber');
assert.equal(rows[1].cuadrillaLabel, '—');
assert.equal(rows[1].inspectorLabel, '—');
assert.equal(rows[1].color, 'red');

// ---- sortRows — ascending/descending, spec.md "Sorting by column header" ---
const byDireccionAsc = sortRows(rows, 'direccion', 'asc');
assert.deepEqual(byDireccionAsc.map((r) => r.id), ['ede_1', 'ede_2']);
const byDireccionDesc = sortRows(rows, 'direccion', 'desc');
assert.deepEqual(byDireccionDesc.map((r) => r.id), ['ede_2', 'ede_1']);

// ---- filterRows — spec.md "Filtering to a single estado" scenario ----------
assert.equal(filterRows(rows, 'todos').length, 2);
assert.equal(filterRows(rows, undefined).length, 2);
const onlyPendiente = filterRows(rows, 'pendiente');
assert.deepEqual(onlyPendiente.map((r) => r.id), ['ede_2']);

console.log('ok — stickers-asignacion.js pure table/map logic');
