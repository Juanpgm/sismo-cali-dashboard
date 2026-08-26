// Self-check for the pure table/map/filter logic behind the Planeación tab.
// Run: node --test "js/**/*.test.mjs" (from web/)
import assert from 'node:assert/strict';
import {
  colorForPunto, buildRows, sortRows, filterRows, formatTruncacion, metricasHtml,
} from './planeacion.js';

// ---- colorForPunto — design.md ADR-10 map legend, 5 states -----------------
// green: tiene_survey (levantado) — wins over everything else.
assert.equal(colorForPunto({ tiene_survey: true, estado_asignacion: 'pendiente', prioridad: 'alta' }), 'green');
assert.equal(colorForPunto({ tiene_survey: true, estado_asignacion: 'no_aplica' }), 'green');
// grey: no_aplica — excluded from the pool, must never read as "pending".
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'no_aplica', prioridad: 'alta' }), 'grey');
// blue: asignado / en_proceso — work already under way.
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'asignado', prioridad: 'alta' }), 'blue');
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'en_proceso', prioridad: 'baja' }), 'blue');
// red: pendiente, effective priority alta.
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'pendiente', prioridad: 'alta' }), 'red');
// amber: pendiente, effective priority media or baja.
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'pendiente', prioridad: 'media' }), 'amber');
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'pendiente', prioridad: 'baja' }), 'amber');
// prioridad_override wins over the computed prioridad for the red/amber split too.
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'pendiente', prioridad: 'baja', prioridad_override: 'alta' }), 'red');
assert.equal(colorForPunto(null), 'amber');
assert.equal(colorForPunto(undefined), 'amber');

// ---- buildRows — joins puntos with cuadrilla/inspector for the table -------
const puntos = [
  {
    id: 'atencionsismo_1', direccion: 'Cra 1', comuna: 'COMUNA 1', barrio: 'Barrio 1',
    afectacion: 'COLAPSO PARCIAL', estado_verificacion: 'Visitado', tipo_inmueble: 'Casa',
    habitabilidad: 'I', prioridad_score: 72, prioridad: 'alta', prioridad_override: null,
    estado_asignacion: 'asignado', cuadrilla_id: 'c1', inspector_uid: 'u1', tier: 'alta',
    match_via: null, tiene_survey: false, coords: { lat: 3.4, lon: -76.5 },
    notas: null, motivo_exclusion: null, clave_integracion: 'PLN-1-ABCDEF01',
  },
  {
    id: 'atencionsismo_2', direccion: 'Cra 2', comuna: 'COMUNA 2', barrio: 'Barrio 2',
    afectacion: 'DAÑO ESTRUCTURAL', estado_verificacion: 'Reportado', tipo_inmueble: 'Edificio',
    habitabilidad: 'R', prioridad_score: 40, prioridad: 'media', prioridad_override: null,
    estado_asignacion: 'pendiente', cuadrilla_id: null, inspector_uid: null, tier: null,
    match_via: null, tiene_survey: false, coords: { lat: 3.5, lon: -76.4 },
    notas: null, motivo_exclusion: null, clave_integracion: 'PLN-2-ABCDEF02',
  },
];
const cuadrillas = [{ id: 'c1', nombre: 'Cuadrilla 1', puntos: ['atencionsismo_1'], inspector_uid: 'u1', origen: 'manual' }];
const inspectores = [{ uid: 'u1', nombre_completo: 'Ana Torres', codigo: '001' }];

const rows = buildRows(puntos, cuadrillas, inspectores);
assert.equal(rows.length, 2);
assert.equal(rows[0].cuadrillaLabel, 'Cuadrilla 1');
assert.equal(rows[0].inspectorLabel, 'Ana Torres');
assert.equal(rows[0].color, 'blue');
assert.equal(rows[0].prioridadEfectiva, 'alta');
assert.equal(rows[1].cuadrillaLabel, '—');
assert.equal(rows[1].inspectorLabel, '—');
assert.equal(rows[1].color, 'amber');
assert.equal(rows[1].prioridadEfectiva, 'media');

// ---- sortRows — effective priority DESC, prioridad_override wins -----------
const unordered = [
  { id: 'low', prioridad: 'baja', prioridad_override: null, prioridad_score: 10, prioridadEfectiva: 'baja' },
  { id: 'high', prioridad: 'alta', prioridad_override: null, prioridad_score: 90, prioridadEfectiva: 'alta' },
  { id: 'mid', prioridad: 'media', prioridad_override: null, prioridad_score: 50, prioridadEfectiva: 'media' },
];
assert.deepEqual(sortRows(unordered).map((r) => r.id), ['high', 'mid', 'low']);

// A low raw prioridad_score but an admin override to 'alta' must sort as alta —
// spec.md "An admin priority override is respected in ordering".
const withOverride = [
  { id: 'raw-high', prioridad: 'alta', prioridad_override: null, prioridad_score: 95, prioridadEfectiva: 'alta' },
  { id: 'overridden', prioridad: 'baja', prioridad_override: 'alta', prioridad_score: 5, prioridadEfectiva: 'alta' },
];
const sortedOverride = sortRows(withOverride);
assert.equal(sortedOverride[0].prioridadEfectiva, 'alta');
assert.equal(sortedOverride[1].prioridadEfectiva, 'alta');
// Tie on effective priority breaks by raw prioridad_score, descending.
assert.deepEqual(sortedOverride.map((r) => r.id), ['raw-high', 'overridden']);

// ---- filterRows — narrows by prioridad and comuna --------------------------
assert.equal(filterRows(rows, {}).length, 2);
assert.equal(filterRows(rows, { prioridad: 'alta' }).length, 1);
assert.deepEqual(filterRows(rows, { prioridad: 'alta' }).map((r) => r.id), ['atencionsismo_1']);
assert.deepEqual(filterRows(rows, { comuna: 'COMUNA 2' }).map((r) => r.id), ['atencionsismo_2']);
assert.equal(filterRows(rows, { prioridad: 'alta', comuna: 'COMUNA 2' }).length, 0);
assert.equal(filterRows(rows, { afectacion: 'DAÑO ESTRUCTURAL' }).length, 1);

// ---- formatTruncacion — spec.md "Truncation is surfaced to the operator" ---
assert.equal(
  formatTruncacion(2000, 13713),
  'Mostrando los 2000 puntos de mayor prioridad de 13713 pendientes.',
);
assert.equal(formatTruncacion(50, 50), null, 'not truncated -> no message');
assert.equal(formatTruncacion(0, 0), null);

// ---- metricasHtml — `metricasProgreso` change (`puntos-disponibles`, 2026-08-26)

assert.equal(metricasHtml(null, new Map()), '<p class="sticker-empty">Sin datos de progreso todavía.</p>');

const metricasFixture = {
  grupos: {
    g1: {
      nombre: 'Norte', miembros: 2, activo: true,
      combinado: { asignados: 4, hechos: 2, pendientes: 1, no_aplica: 1, completado_pct: 50.0 },
      stickers: { asignados: 1, hechos: 1, pendientes: 0, no_aplica: 0, completado_pct: 100.0 },
      survey: { asignados: 3, hechos: 1, pendientes: 1, no_aplica: 1, completado_pct: 33.3 },
    },
  },
  inspectores: {
    'uid-a': {
      grupos: ['Norte'],
      combinado: { asignados: 2, hechos: 1, pendientes: 1, no_aplica: 0, completado_pct: 50.0 },
      stickers: { asignados: 0, hechos: 0, pendientes: 0, no_aplica: 0, completado_pct: 0.0 },
      survey: { asignados: 2, hechos: 1, pendientes: 1, no_aplica: 0, completado_pct: 50.0 },
    },
  },
  combinado: { asignados: 4, hechos: 2, pendientes: 1, no_aplica: 1, completado_pct: 50.0 },
  stickers: { asignados: 1, hechos: 1, pendientes: 0, no_aplica: 0, completado_pct: 100.0 },
  survey: { asignados: 3, hechos: 1, pendientes: 1, no_aplica: 1, completado_pct: 33.3 },
};

const inspectorById = new Map([['uid-a', { uid: 'uid-a', nombre_completo: 'Ana Pérez' }]]);
const html = metricasHtml(metricasFixture, inspectorById);
assert.match(html, /Norte/);
assert.match(html, /50%/); // group's combined completion
assert.match(html, /100%/); // group's sticker completion
// Inspector NAME is resolved via inspectorById, never a bare raw uid.
assert.match(html, /Ana Pérez/);
assert.ok(!html.includes('uid-a'), 'raw uid must not leak into the rendered markup once resolved');
assert.match(html, /Norte/); // the inspector's own group membership list

const htmlSinRoster = metricasHtml(metricasFixture, new Map());
// Fail open: an unresolved uid (roster not loaded yet) falls back to the
// raw uid itself, never a blank/broken cell.
assert.match(htmlSinRoster, /uid-a/);

const vacio = metricasHtml({ grupos: {}, inspectores: {}, combinado: { asignados: 0, hechos: 0, pendientes: 0, no_aplica: 0, completado_pct: 0 }, stickers: { asignados: 0, hechos: 0, pendientes: 0, no_aplica: 0, completado_pct: 0 }, survey: { asignados: 0, hechos: 0, pendientes: 0, no_aplica: 0, completado_pct: 0 } }, new Map());
assert.match(vacio, /Todavía no hay grupos/);
assert.match(vacio, /Todavía no hay inspectores/);

console.log('ok — planeacion.js pure table/map/filter logic');
