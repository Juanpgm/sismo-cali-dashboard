// Self-check for the pure logic behind the "Puntos Solicitados" tab: estado
// classification, counts, filters and sort. Run: node web/js/puntos_solicitados.test.mjs
import assert from 'node:assert';
import {
  ESTADOS, estadoDe, contarPorEstado, applyFilters, sortPuntos,
} from './puntos_solicitados.js';

// The derived lifecycle states (ADR-4 map's output values), in the order they
// progress through the assignment machinery.
assert.deepStrictEqual(ESTADOS.map((e) => e.key), ['pendiente', 'asignado', 'en_proceso', 'visitado', 'excluido']);

// estadoDe: exact stored values (what the router's GET already derives server-side).
assert.strictEqual(estadoDe({ estado_seguimiento: 'pendiente' }).key, 'pendiente');
assert.strictEqual(estadoDe({ estado_seguimiento: 'asignado' }).key, 'asignado');
assert.strictEqual(estadoDe({ estado_seguimiento: 'en_proceso' }).key, 'en_proceso');
assert.strictEqual(estadoDe({ estado_seguimiento: 'visitado' }).key, 'visitado');
assert.strictEqual(estadoDe({ estado_seguimiento: 'excluido' }).key, 'excluido');

// Case drift and blank/unknown values fall back to 'pendiente' — never crash,
// never silently invent a 6th state.
assert.strictEqual(estadoDe({ estado_seguimiento: ' ASIGNADO ' }).key, 'asignado');
assert.strictEqual(estadoDe({ estado_seguimiento: '' }).key, 'pendiente');
assert.strictEqual(estadoDe({}).key, 'pendiente');
assert.strictEqual(estadoDe({ estado_seguimiento: 'otra_cosa' }).key, 'pendiente');

// contarPorEstado: counts every state, including zero-count ones — real
// production code walking a mixed, non-trivial dataset.
const counts = contarPorEstado([
  { estado_seguimiento: 'pendiente' },
  { estado_seguimiento: 'pendiente' },
  { estado_seguimiento: 'asignado' },
  { estado_seguimiento: 'visitado' },
]);
assert.deepStrictEqual(counts, {
  pendiente: 2, asignado: 1, en_proceso: 0, visitado: 1, excluido: 0,
});
assert.deepStrictEqual(contarPorEstado([]), {
  pendiente: 0, asignado: 0, en_proceso: 0, visitado: 0, excluido: 0,
});

// applyFilters — a realistic mixed dataset, filtered by each dimension in turn.
const dataset = [
  {
    id: '1', nombre: 'Casa esquinera', direccion: 'Calle 5 # 10-20', nombre_solicitante: 'María Pérez',
    comuna_corregimiento: 'Comuna 3', barrio_vereda: 'San Antonio', clave_integracion: 'PLN-AAA-11111111',
    estado_seguimiento: 'pendiente',
  },
  {
    id: '2', nombre: 'Edificio Torre Azul', direccion: 'Carrera 8 # 4-30', nombre_solicitante: 'Jorge Ruiz',
    comuna_corregimiento: 'Comuna 3', barrio_vereda: 'El Peñón', clave_integracion: 'PLN-BBB-22222222',
    estado_seguimiento: 'asignado',
  },
  {
    id: '3', nombre: 'Local comercial', direccion: 'Avenida 6N', nombre_solicitante: 'Ana Gómez',
    comuna_corregimiento: 'Comuna 19', barrio_vereda: 'Tequendama', clave_integracion: 'PLN-CCC-33333333',
    estado_seguimiento: 'visitado',
  },
];

assert.strictEqual(applyFilters(dataset, {}).length, 3);
assert.deepStrictEqual(applyFilters(dataset, { estado: 'asignado' }).map((p) => p.id), ['2']);
assert.deepStrictEqual(applyFilters(dataset, { comuna: 'Comuna 3' }).map((p) => p.id), ['1', '2']);
assert.deepStrictEqual(applyFilters(dataset, { comuna: 'Comuna 3', barrio: 'El Peñón' }).map((p) => p.id), ['2']);
// Search is case/accent-insensitive across nombre/direccion/solicitante.
assert.deepStrictEqual(applyFilters(dataset, { search: 'peñon' }).map((p) => p.id), ['2']);
assert.deepStrictEqual(applyFilters(dataset, { search: 'MARIA' }).map((p) => p.id), ['1']);
assert.deepStrictEqual(applyFilters(dataset, { search: 'no-existe-nada' }), []);

// sortPuntos — newest creado_en first.
const unsorted = [
  { id: 'old', creado_en: '2026-08-01T10:00:00Z' },
  { id: 'newest', creado_en: '2026-08-27T09:00:00Z' },
  { id: 'mid', creado_en: '2026-08-15T12:00:00Z' },
];
assert.deepStrictEqual(sortPuntos(unsorted).map((p) => p.id), ['newest', 'mid', 'old']);
// Missing creado_en sorts last, never crashes.
assert.deepStrictEqual(
  sortPuntos([{ id: 'sin-fecha' }, { id: 'con-fecha', creado_en: '2026-08-01T00:00:00Z' }]).map((p) => p.id),
  ['con-fecha', 'sin-fecha'],
);

console.log('ok — puntos_solicitados.js estado classification, filters, sort');
