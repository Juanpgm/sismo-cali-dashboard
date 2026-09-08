// Self-check for the pure logic behind the Reportes ciudadanos tab.
// Run: node web/js/reportes-ciudadanos.test.mjs
import assert from 'node:assert';
import {
  ESTADOS, estadoDe, contarPor, opcionesDe, applyFiltrosReportes, colorSticker, COLOR_MODES_REPORTES,
} from './reportes-ciudadanos.js';

const r = (over = {}) => ({
  id: 'r1', direccion: 'Calle 1', barrio: 'San Antonio', comuna: 'Comuna 3', estado: 'Reportado',
  afectacion: 'DAÑO ESTRUCTURAL', tipo_inmueble: 'Casa', nombre_edificio: '', lat: 3.4, lng: -76.5,
  creado: '2026-08-18T18:33:00-05:00', creado_texto: '', habitabilidad: '', visitado: false, pudo_evaluar: '',
  alcance: '', descripcion: 'Grieta', sticker: { numero: '', color: '', etiqueta: '', origen: '', clasificacion: '' },
  ...over,
});

// Six API states, worst-known first for the KPI row.
assert.deepStrictEqual(ESTADOS.map((e) => e.key), [
  'Visitado crítico', 'Evaluación especializada', 'Visitado', 'Visita fallida', 'Asignado', 'Reportado']);
assert.strictEqual(estadoDe(r()).key, 'Reportado');
assert.strictEqual(estadoDe(r({ estado: 'visitado crítico' })).key, 'Visitado crítico');
assert.strictEqual(estadoDe(r({ estado: '' })).key, 'SIN_DATO');
assert.strictEqual(estadoDe(r({ estado: 'Otro' })).key, 'SIN_DATO');

// Counting by any key function; unknown buckets are kept.
const counts = contarPor([r(), r({ estado: 'Asignado' }), r({ estado: '' })], (x) => estadoDe(x).key);
assert.deepStrictEqual(counts, { Reportado: 1, Asignado: 1, SIN_DATO: 1 });

// Option lists are sorted, deduped, and skip blanks.
assert.deepStrictEqual(opcionesDe([r({ comuna: 'Comuna 3' }), r({ comuna: 'Comuna 19' }), r({ comuna: '' }), r({ comuna: 'Comuna 3' })], 'comuna'),
  ['Comuna 19', 'Comuna 3']);

// Filters: every dimension, AND-combined, free text over address/name/id.
const list = [r(), r({ id: 'r2', estado: 'Visitado', afectacion: 'COLAPSO TOTAL', tipo_inmueble: 'Edificio', comuna: 'Comuna 19',
  barrio: 'Granada', nombre_edificio: 'Torre Sol', sticker: { numero: 'x', color: 'rojo', etiqueta: 'No habitable', origen: 'sistema', clasificacion: '' } })];
assert.strictEqual(applyFiltrosReportes(list, {}).length, 2);
assert.deepStrictEqual(applyFiltrosReportes(list, { estado: 'Visitado' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { afectacion: 'COLAPSO TOTAL' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { tipo: 'Casa' }).map((x) => x.id), ['r1']);
assert.deepStrictEqual(applyFiltrosReportes(list, { comuna: 'Comuna 19' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { barrio: 'Granada' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { sticker: 'rojo' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { sticker: 'SIN_STICKER' }).map((x) => x.id), ['r1']);
assert.deepStrictEqual(applyFiltrosReportes(list, { search: 'torre sol' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { search: 'CALLE' }).map((x) => x.id), ['r1', 'r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { estado: 'Visitado', comuna: 'Comuna 3' }), []);
assert.deepStrictEqual(applyFiltrosReportes([], { estado: 'Visitado' }), []);

// Sticker colour mode: API colour names to the Panel ramp; blank is "unknown".
assert.notStrictEqual(colorSticker(r({ sticker: { color: 'rojo' } })), colorSticker(r()));
assert.strictEqual(colorSticker(r({ sticker: { color: 'ROJO' } })), colorSticker(r({ sticker: { color: 'rojo' } })));
assert.strictEqual(colorSticker(r({ sticker: null })), colorSticker(r()));
assert.deepStrictEqual(Object.keys(COLOR_MODES_REPORTES), ['estado', 'afectacion', 'sticker']);
console.log('reportes-ciudadanos.test.mjs OK');

// ── Mandatory edge cases beyond the plan's tests ─────────────────────────

// estadoDe: accent/case drift and a null record must not throw.
assert.strictEqual(estadoDe(r({ estado: 'evaluacion especializada' })).key, 'Evaluación especializada');
assert.strictEqual(estadoDe(r({ estado: 'EVALUACIÓN ESPECIALIZADA' })).key, 'Evaluación especializada');
assert.strictEqual(estadoDe(null).key, 'SIN_DATO');
assert.strictEqual(estadoDe(undefined).key, 'SIN_DATO');

// applyFiltrosReportes: sticker missing entirely, filtros undefined, search of only spaces.
const sinSticker = r({ id: 'r3' });
delete sinSticker.sticker;
assert.deepStrictEqual(applyFiltrosReportes([sinSticker], { sticker: 'SIN_STICKER' }).map((x) => x.id), ['r3']);
assert.deepStrictEqual(applyFiltrosReportes([sinSticker], { sticker: 'rojo' }), []);
assert.strictEqual(applyFiltrosReportes(list, undefined).length, 2);
assert.strictEqual(applyFiltrosReportes(list, { search: '   ' }).length, 2);

// opcionesDe: non-string (numeric) values and null records in the list.
assert.deepStrictEqual(opcionesDe([r({ comuna: 3 }), r({ comuna: 19 }), null, r({ comuna: 3 })], 'comuna'),
  ['19', '3']);
assert.deepStrictEqual(opcionesDe([null, undefined], 'comuna'), []);

// contarPor on an empty list returns an empty bucket map, never throws.
assert.deepStrictEqual(contarPor([], (x) => estadoDe(x).key), {});

// applyFiltrosReportes: a falsy record in the list must be skipped, not thrown on
// (fix(reportes) finding 5 — r.afectacion on a null r used to throw TypeError).
assert.doesNotThrow(() => applyFiltrosReportes([null, r()], { afectacion: 'DAÑO ESTRUCTURAL' }));
assert.deepStrictEqual(applyFiltrosReportes([null, r(), undefined], { afectacion: 'DAÑO ESTRUCTURAL' }).map((x) => x.id), ['r1']);
assert.deepStrictEqual(applyFiltrosReportes([null, undefined], {}), []);

console.log('reportes-ciudadanos.test.mjs edge cases OK');
