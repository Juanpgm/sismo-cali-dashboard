// Self-check for the pure table/map logic behind the Asignación sub-section.
// Run: node web/js/stickers-asignacion.test.mjs
import assert from 'node:assert/strict';
import {
  colorForPunto, buildRows, sortRows, filterRows,
  activeCountsByInspector, wouldExceedCap, filterInspectores, inspectorOptionLabel,
  gaugeCounts, MAX_ACTIVE_PER_INSPECTOR,
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

// ---- sortRows — blank values always sink to the bottom, both directions ----
// (the ~101 israel points have no direccion; they must never front the table).
const withBlank = [
  { id: 'has_addr', direccion: 'Cra 5' },
  { id: 'blank', direccion: '' },
  { id: 'null_addr', direccion: null },
];
assert.deepEqual(sortRows(withBlank, 'direccion', 'asc').map((r) => r.id)[0], 'has_addr', 'asc: non-empty first');
assert.deepEqual(sortRows(withBlank, 'direccion', 'desc').map((r) => r.id)[0], 'has_addr', 'desc: non-empty still first, blanks last');
const ascBlanks = sortRows(withBlank, 'direccion', 'asc');
assert.ok(ascBlanks[ascBlanks.length - 1].direccion === '' || ascBlanks[ascBlanks.length - 1].direccion == null, 'asc: a blank is last');
const descBlanks = sortRows(withBlank, 'direccion', 'desc');
assert.ok(descBlanks[descBlanks.length - 1].direccion === '' || descBlanks[descBlanks.length - 1].direccion == null, 'desc: a blank is last');

// ---- filterRows — spec.md "Filtering to a single estado" scenario ----------
assert.equal(filterRows(rows, 'todos').length, 2);
assert.equal(filterRows(rows, undefined).length, 2);
const onlyPendiente = filterRows(rows, 'pendiente');
assert.deepEqual(onlyPendiente.map((r) => r.id), ['ede_2']);

// ---- activeCountsByInspector — N/20 load from the already-fetched rows ------
const loadRows = [
  { id: 'a', inspector_uid: 'u1', estado_asignacion: 'asignado' },
  { id: 'b', inspector_uid: 'u1', estado_asignacion: 'en_proceso' },
  { id: 'c', inspector_uid: 'u1', estado_asignacion: 'hecho' },    // done -> not counted
  { id: 'd', inspector_uid: 'u2', estado_asignacion: 'asignado' },
  { id: 'e', inspector_uid: null, estado_asignacion: 'pendiente' },
];
const loadCounts = activeCountsByInspector(loadRows);
assert.equal(loadCounts.get('u1'), 2, 'u1 has 2 active (hecho excluded)');
assert.equal(loadCounts.get('u2'), 1);
assert.equal(loadCounts.get('u3'), undefined);
assert.equal(MAX_ACTIVE_PER_INSPECTOR, 20);

// ---- wouldExceedCap — mirrors the backend distinct-count enforcement --------
// u1 has 2 active (a,b). Assigning a 3-point cuadrilla of brand-new points -> 5, ok.
assert.equal(wouldExceedCap(loadRows, ['x', 'y', 'z'], 'u1'), false);
// A cuadrilla whose points reach the cap: 19 existing active + 2 new = 21 > 20.
const heavy = Array.from({ length: 19 }, (_, i) => ({ id: `h${i}`, inspector_uid: 'busy', estado_asignacion: 'asignado' }));
assert.equal(wouldExceedCap(heavy, ['n1', 'n2'], 'busy'), true, '19 + 2 new -> over cap');
assert.equal(wouldExceedCap(heavy, ['n1'], 'busy'), false, '19 + 1 new = 20, exactly at cap');
// Points already in the cuadrilla are not double-counted: 19 active where 18 of
// them are the cuadrilla being (re)assigned -> distinct total stays 19.
const alreadyMine = heavy.map((r) => r.id).slice(0, 18);
assert.equal(wouldExceedCap(heavy, alreadyMine, 'busy'), false, 'reassigning own points does not double-count');

// ---- filterInspectores — free-text over nombre/código/cédula ---------------
const roster = [
  { uid: 'u1', nombre_completo: 'Ana Torres', codigo: '001', cedula: '123' },
  { uid: 'u2', nombre_completo: 'Beto Ruiz', codigo: '045', cedula: '999' },
];
assert.deepEqual(filterInspectores(roster, 'ana').map((i) => i.uid), ['u1']);
assert.deepEqual(filterInspectores(roster, '045').map((i) => i.uid), ['u2']);
assert.deepEqual(filterInspectores(roster, '999').map((i) => i.uid), ['u2']);
assert.equal(filterInspectores(roster, '').length, 2, 'empty query -> full roster');
assert.equal(filterInspectores(roster, 'zzz').length, 0);

// ---- inspectorOptionLabel — `Nombre — codigo (N/20)` -----------------------
assert.equal(inspectorOptionLabel(roster[0], 3), 'Ana Torres — 001 (3/20)');
assert.equal(inspectorOptionLabel({ codigo: null }, 0), 'Brigada — (0/20)');

// ---- gaugeCounts — barrido/asignado/pendiente split, colorForPunto precedence
const gaugeRows = [
  { tiene_sticker: true, estado_asignacion: 'pendiente' },  // barrido (sticker wins)
  { tiene_sticker: true, estado_asignacion: 'asignado' },   // barrido (sticker wins)
  { tiene_sticker: false, estado_asignacion: 'asignado' },  // asignado
  { tiene_sticker: false, estado_asignacion: 'en_proceso' },// asignado
  { tiene_sticker: false, estado_asignacion: 'pendiente' }, // pendiente
  { tiene_sticker: false, estado_asignacion: 'hecho' },     // pendiente (not barrido, not assigned-active)
];
assert.deepEqual(gaugeCounts(gaugeRows), { barrido: 2, asignado: 2, pendiente: 2, total: 6 });
assert.deepEqual(gaugeCounts([]), { barrido: 0, asignado: 0, pendiente: 0, total: 0 });
assert.deepEqual(gaugeCounts(undefined), { barrido: 0, asignado: 0, pendiente: 0, total: 0 });

console.log('ok — stickers-asignacion.js pure table/map logic');
