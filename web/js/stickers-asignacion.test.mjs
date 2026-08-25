// Self-check for the pure table/map logic behind the Asignación sub-section.
// Run: node web/js/stickers-asignacion.test.mjs
import assert from 'node:assert/strict';
import {
  colorForPunto, buildRows, sortRows, filterRows,
  activeCountsByInspector, filterInspectores, inspectorOptionLabel,
  gaugeCounts, isHabilitado,
} from './stickers-asignacion.js';

// ---- colorForPunto — spec.md "Map view" legend scenarios -------------------
assert.equal(colorForPunto({ tiene_sticker: true, estado_asignacion: 'pendiente' }), 'blue');
assert.equal(colorForPunto({ tiene_sticker: false, estado_asignacion: 'pendiente' }), 'red');
assert.equal(colorForPunto({ tiene_sticker: false, estado_asignacion: 'asignado' }), 'amber');
// en_proceso gets its OWN colour — a cuadrilla actively working the point,
// distinct from just-assigned-not-started.
assert.equal(colorForPunto({ tiene_sticker: false, estado_asignacion: 'en_proceso' }), 'yellow');
// tiene_sticker wins even when also assigned/en_proceso — matched still reads as "done".
assert.equal(colorForPunto({ tiene_sticker: true, estado_asignacion: 'asignado' }), 'blue');
assert.equal(colorForPunto({ tiene_sticker: true, estado_asignacion: 'en_proceso' }), 'blue');

// ---- buildRows — derived estado: 'hecho' SOLO si tiene_sticker; un punto
// asignado (o marcado 'hecho' por el inspector, aún sin confirmar por el cruce
// diario) nunca retrocede a 'pendiente'; en_proceso pasa tal cual si el doc ya
// lo trae (nadie lo escribe todavía, pero no debe aplastarse a 'asignado') ---
assert.equal(buildRows([{ id: 'p1', tiene_sticker: true, estado_asignacion: 'pendiente' }], [], [])[0].estado_asignacion, 'hecho');
assert.equal(buildRows([{ id: 'p2', tiene_sticker: false, cuadrilla_id: 'c1' }], [], [])[0].estado_asignacion, 'asignado');
assert.equal(buildRows([{ id: 'p3', tiene_sticker: false, inspector_uid: 'u1' }], [], [])[0].estado_asignacion, 'asignado');
// El inspector marcó 'hecho' en campo, pero cruce_sticker.py (diario) todavía
// no lo confirmó: sigue leyendo 'asignado', no 'pendiente'.
assert.equal(buildRows([{ id: 'p4', tiene_sticker: false, inspector_uid: 'u1', estado_asignacion: 'hecho' }], [], [])[0].estado_asignacion, 'asignado');
assert.equal(buildRows([{ id: 'p5', tiene_sticker: false }], [], [])[0].estado_asignacion, 'pendiente');
assert.equal(buildRows([{ id: 'p6', tiene_sticker: false, cuadrilla_id: 'c1', estado_asignacion: 'en_proceso' }], [], [])[0].estado_asignacion, 'en_proceso');
// tiene_sticker todavía gana sobre en_proceso.
assert.equal(buildRows([{ id: 'p7', tiene_sticker: true, estado_asignacion: 'en_proceso' }], [], [])[0].estado_asignacion, 'hecho');

// ---- isHabilitado — mismo criterio que stickers.js:rowHtml -----------------
assert.equal(isHabilitado({ disabled: false, activo: true }), true);
assert.equal(isHabilitado({ disabled: true, activo: true }), false);
assert.equal(isHabilitado({ disabled: false, activo: false }), false);
assert.equal(isHabilitado(null), false);

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
  { id: 'b', inspector_uid: 'u1', estado_asignacion: 'asignado' },
  { id: 'c', inspector_uid: 'u1', estado_asignacion: 'hecho' },    // done -> not counted
  { id: 'd', inspector_uid: 'u2', estado_asignacion: 'asignado' },
  { id: 'e', inspector_uid: null, estado_asignacion: 'pendiente' },
];
const loadCounts = activeCountsByInspector(loadRows);
assert.equal(loadCounts.get('u1'), 2, 'u1 has 2 active (hecho excluded)');
assert.equal(loadCounts.get('u2'), 1);
assert.equal(loadCounts.get('u3'), undefined);

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

// ---- inspectorOptionLabel — `Nombre — codigo (N)`, no cap ------------------
assert.equal(inspectorOptionLabel(roster[0], 3), 'Ana Torres — 001 (3)');
assert.equal(inspectorOptionLabel({ codigo: null }, 0), 'Brigada — (0)');

// ---- gaugeCounts — barrido/asignado/pendiente split, colorForPunto precedence
const gaugeRows = [
  { tiene_sticker: true, estado_asignacion: 'pendiente' },  // barrido (sticker wins)
  { tiene_sticker: true, estado_asignacion: 'asignado' },   // barrido (sticker wins)
  { tiene_sticker: false, estado_asignacion: 'asignado' },  // asignado
  { tiene_sticker: false, estado_asignacion: 'asignado' },  // asignado
  { tiene_sticker: false, estado_asignacion: 'pendiente' }, // pendiente
  { tiene_sticker: false, estado_asignacion: 'hecho' },     // pendiente (raw 'hecho' but sticker unconfirmed — buildRows never emits this combo; gaugeCounts alone has no way to know)
];
assert.deepEqual(gaugeCounts(gaugeRows), { barrido: 2, asignado: 2, pendiente: 2, total: 6 });
assert.deepEqual(gaugeCounts([]), { barrido: 0, asignado: 0, pendiente: 0, total: 0 });
assert.deepEqual(gaugeCounts(undefined), { barrido: 0, asignado: 0, pendiente: 0, total: 0 });

console.log('ok — stickers-asignacion.js pure table/map logic');
