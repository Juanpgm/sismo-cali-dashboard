// Self-check for the Panel reconciliation model (pure logic, no DOM/Firebase).
// Run: node web/js/panel-model.test.mjs
import assert from 'node:assert/strict';
import {
  buildingKey, applyBuildingFilter, inDateRange, reconcile,
  inspeccionesEdificiosText, flagYesCount, countByFuente,
} from './panel-model.js';

// Fixture builders -----------------------------------------------------------
let seq = 0;
const rec = (o = {}) => ({
  GlobalID: `g${++seq}`, ObjectID: seq, fuente: 'cali',
  criterio_habitabilidad: 'H', fecha_inspeccion: '2026-08-10',
  dup_grupo_id: '', es_representante: true, ...o,
});

// --- buildingKey ------------------------------------------------------------
assert.equal(buildingKey({ dup_grupo_id: 'A1', GlobalID: 'x' }), 'g:A1');
assert.equal(buildingKey({ dup_grupo_id: '  A1  ' }), 'g:A1', 'whitespace is trimmed');
assert.notEqual(buildingKey({ dup_grupo_id: '', GlobalID: 'a' }), buildingKey({ dup_grupo_id: '', GlobalID: 'b' }),
  'no group id -> each record is its own building');
assert.notEqual(buildingKey({ dup_grupo_id: null, GlobalID: 'a' }), buildingKey({ dup_grupo_id: undefined, GlobalID: 'b' }));
assert.equal(buildingKey({}, 7), buildingKey({}, 7), 'deterministic for the same index');
assert.notEqual(buildingKey({}, 1), buildingKey({}, 2), 'no id at all falls back to the index, never merges');
assert.notEqual(buildingKey({ dup_grupo_id: 5 }), buildingKey({ dup_grupo_id: 6 }), 'numeric ids are supported');

// --- applyBuildingFilter: empty / single ---------------------------------------
assert.deepEqual(applyBuildingFilter([], () => true), { inspecciones: [], edificios: [] });
assert.deepEqual(applyBuildingFilter(null, () => true), { inspecciones: [], edificios: [] });
{
  const a = rec();
  const out = applyBuildingFilter([a], () => true);
  assert.deepEqual(out.inspecciones, [a]);
  assert.deepEqual(out.edificios, [a]);
  const none = applyBuildingFilter([a], () => false);
  assert.deepEqual(none, { inspecciones: [], edificios: [] }, 'filter matching zero buildings');
}

// --- filter AFTER grouping ------------------------------------------------------
// Building B: the representative (most critical) is I2, a re-inspection is H.
const b1 = rec({ dup_grupo_id: 'B', criterio_habitabilidad: 'I2', es_representante: true, fecha_inspeccion: '2026-08-10' });
const b2 = rec({ dup_grupo_id: 'B', criterio_habitabilidad: 'H', es_representante: false, fecha_inspeccion: '2026-08-20' });
const b3 = rec({ dup_grupo_id: 'B', criterio_habitabilidad: 'H', es_representante: false, fecha_inspeccion: '2026-08-21' });
// Building C: single inspection, R1.
const c1 = rec({ dup_grupo_id: 'C', criterio_habitabilidad: 'R1' });
const all = [b1, b2, b3, c1];
const isH = (r) => r.criterio_habitabilidad === 'H';
{
  const out = applyBuildingFilter(all, isH);
  assert.deepEqual(out.inspecciones, [b2, b3], 'table keeps only inspections that match');
  assert.deepEqual(out.edificios, [b1], 'building passes because ANY inspection matches; shown via its representative; counted once');
}
{
  const out = applyBuildingFilter(all, (r) => r.criterio_habitabilidad === 'I2');
  assert.deepEqual(out.edificios, [b1]);
  assert.equal(out.inspecciones.length, 1);
}
{
  const out = applyBuildingFilter(all, (r) => r.criterio_habitabilidad === 'I3');
  assert.deepEqual(out, { inspecciones: [], edificios: [] }, 'not counted when no inspection matches');
}
{
  // Different criteria across inspections: matching either one keeps the building, once.
  const out = applyBuildingFilter(all, (r) => r.criterio_habitabilidad === 'H' || r.criterio_habitabilidad === 'I2');
  assert.equal(out.edificios.length, 1);
  assert.equal(out.inspecciones.length, 3);
}
{
  // Date filter after grouping: only the re-inspection falls inside the range.
  const out = applyBuildingFilter(all, (r) => inDateRange(r.fecha_inspeccion, '2026-08-19', '2026-08-30'));
  assert.deepEqual(out.edificios, [b1]);
  assert.deepEqual(out.inspecciones, [b2, b3]);
}
{
  // Representative missing from the matched set AND a group with no flagged representative.
  const g1 = rec({ dup_grupo_id: 'Z', es_representante: false, criterio_habitabilidad: 'R2' });
  const g2 = rec({ dup_grupo_id: 'Z', es_representante: false, criterio_habitabilidad: 'H' });
  const out = applyBuildingFilter([g1, g2], isH);
  assert.equal(out.edificios.length, 1, 'group without a representative still counts once');
  assert.equal(out.edificios[0], g2, 'falls back to the first matching inspection');
}
{
  // Records without es_representante at all (legacy data / Israel) count as their own building.
  const l1 = { GlobalID: 'l1', criterio_habitabilidad: 'H' };
  const l2 = { GlobalID: 'l2', criterio_habitabilidad: 'H' };
  const out = applyBuildingFilter([l1, l2], isH);
  assert.equal(out.edificios.length, 2);
}

// --- Israel: never merged with Survey123 buildings, never silently double counted ---
{
  const survey = rec({ dup_grupo_id: 'S1', x: -76.5, y: 3.4 });
  const israelSameSpot = { GlobalID: 'i1', ObjectID: 'isr-1', fuente: 'israel', x: -76.5, y: 3.4, criterio_habitabilidad: 'H' };
  const israelNoCoords = { GlobalID: null, fuente: 'israel', x: null, y: null, direccion: null, criterio_habitabilidad: 'R1' };
  const israelNoId = { fuente: 'israel', criterio_habitabilidad: 'I1' };
  const out = applyBuildingFilter([survey, israelSameSpot, israelNoCoords, israelNoId], () => true);
  assert.equal(out.edificios.length, 4, 'each Israel record is its own building, incl. missing coords/address/ids');
  const counts = countByFuente(out.inspecciones);
  assert.deepEqual(counts, { cali: 1, israel: 3 }, 'the Israel block is counted separately and explicitly');
  assert.deepEqual(countByFuente([]), { cali: 0, israel: 0 });
  assert.deepEqual(countByFuente(null), { cali: 0, israel: 0 });
  assert.deepEqual(countByFuente([{}, { fuente: undefined }]), { cali: 2, israel: 0 }, 'unknown source counts as cali');
}

// --- inDateRange: boundaries, invalid ----------------------------------------
assert.equal(inDateRange('2026-08-10', '2026-08-10', '2026-08-10'), true, 'start and end are inclusive');
assert.equal(inDateRange('2026-08-09', '2026-08-10', null), false);
assert.equal(inDateRange('2026-08-11', null, '2026-08-10'), false);
assert.equal(inDateRange('2026-08-10', null, null), true, 'no bounds -> everyone passes, even undated');
assert.equal(inDateRange(null, null, null), true);
assert.equal(inDateRange(null, '2026-08-01', null), false, 'undated fails an active bound');
assert.equal(inDateRange('', '2026-08-01', null), false);
assert.equal(inDateRange('   ', null, '2026-08-30'), false);
assert.equal(inDateRange(undefined, null, '2026-08-30'), false);
assert.equal(inDateRange('not-a-date', '2026-08-01', null), false, 'malformed date fails an active bound');
assert.equal(inDateRange('not-a-date', null, '2026-08-30'), false);
assert.equal(inDateRange('2026-13-45', '2026-08-01', null), false, 'impossible calendar date is rejected');
assert.equal(inDateRange(' 2026-08-10 ', '2026-08-10', '2026-08-10'), true, 'surrounding whitespace tolerated');
assert.equal(inDateRange('2026-08-10T13:00:00', '2026-08-10', '2026-08-10'), true, 'ISO datetime uses its date part');

// --- reconcile: invariants ---------------------------------------------------
{
  const edificios = [
    rec({ criterio_habitabilidad: 'H' }), rec({ criterio_habitabilidad: 'h ' }), // case/whitespace variants
    rec({ criterio_habitabilidad: 'R1' }), rec({ criterio_habitabilidad: 'r2' }),
    rec({ criterio_habitabilidad: 'I1' }), rec({ criterio_habitabilidad: 'I2' }), rec({ criterio_habitabilidad: 'i3' }),
    rec({ criterio_habitabilidad: null }), rec({ criterio_habitabilidad: '' }), rec({ criterio_habitabilidad: '   ' }),
    rec({ criterio_habitabilidad: 'ZZ' }), // malformed category -> Sin dato, never dropped
    rec({ criterio_habitabilidad: undefined, habitabilidad_calc: 'H' }), // fallback field
  ];
  const r = reconcile(edificios.length + 5, edificios);
  assert.equal(r.edificios, 12);
  assert.equal(r.registros, 17);
  assert.equal(r.reinspecciones, 5, 'registros - edificios');
  assert.equal(r.h, 3);
  assert.equal(r.r, 2);
  assert.equal(r.i, 3);
  assert.equal(r.sinDato, 4);
  assert.equal(r.h + r.r + r.i + r.sinDato, r.edificios, 'H + R + I + Sin dato == edificios');
}
{
  const empty = reconcile(0, []);
  assert.deepEqual(empty, { registros: 0, edificios: 0, reinspecciones: 0, h: 0, r: 0, i: 0, sinDato: 0 });
  const single = reconcile(1, [rec({ criterio_habitabilidad: 'I3' })]);
  assert.equal(single.i, 1);
  assert.equal(single.sinDato, 0);
  // Never negative even if the caller passes inconsistent numbers.
  assert.equal(reconcile(1, [rec(), rec()]).reinspecciones, 0);
  assert.equal(reconcile(undefined, null).registros, 0);
}

// --- inspeccionesEdificiosText -----------------------------------------------
assert.equal(inspeccionesEdificiosText(2090, 1731), '2090 inspecciones (1731 edificios)');
assert.equal(inspeccionesEdificiosText(1, 1), '1 inspección (1 edificio)');
assert.equal(inspeccionesEdificiosText(0, 0), '0 inspecciones (0 edificios)');
assert.equal(inspeccionesEdificiosText(2, 1), '2 inspecciones (1 edificio)');
assert.equal(inspeccionesEdificiosText(undefined, null), '0 inspecciones (0 edificios)');

// --- flagYesCount: the Riesgos chart must agree with the depurated cards -------
{
  const rows = [
    { colapso_total: 'si', colapso_parcial: 'no' },
    { colapso_total: 'si', colapso_parcial: 'si' }, // contradiction -> parcial
    { colapso_total: 'SÍ ', colapso_parcial: 'no' }, // accent/case/whitespace variant
    { colapso_total: null, colapso_parcial: 'si' },
    { colapso_total: undefined, colapso_parcial: '' },
    { colapso_total: 'talvez', colapso_parcial: 'x' }, // malformed
    { '42_a': 'si' },
  ];
  assert.equal(flagYesCount(rows, 'colapso_total'), 2, 'both-si moves to parcial');
  assert.equal(flagYesCount(rows, 'colapso_parcial'), 2);
  // Already-resolved records (loaded through the store) give the same answer.
  const resolved = rows.map((r) => ({ ...r, colapso_resuelto: r.colapso_parcial === 'si' ? 'parcial' : (String(r.colapso_total).trim().toLowerCase().startsWith('s') ? 'total' : 'ninguno') }));
  assert.equal(flagYesCount(resolved, 'colapso_total'), 2);
  assert.equal(flagYesCount(rows, '42_a'), 1, 'other flags keep the plain Sí count');
  assert.equal(flagYesCount([], 'colapso_total'), 0);
  assert.equal(flagYesCount(null, 'colapso_total'), 0);
}

console.log('ok — panel-model self-check');
