// Self-check for the pure classification logic behind the Stickers tab's
// evaluaciones panel. Run: node web/js/evaluaciones.test.mjs
import assert from 'node:assert';
import {
  claseDe, contarPorClase, CLASES, faseDe, FASES, applyFilters, FASE_SIN_DATO,
} from './evaluaciones.js';

// The three ATC-20 placard states, in escalating severity.
assert.deepStrictEqual(CLASES.map((c) => c.key), ['INSPECCIONADA', 'USO_RESTRINGIDO', 'INSEGURO']);
assert.deepStrictEqual(CLASES.map((c) => c.label), ['inspeccionada', 'uso restringido', 'inseguro']);

// Exact stored values (what formulario/js/form.js writes).
assert.strictEqual(claseDe({ clasificacion: 'INSPECCIONADA' }).label, 'inspeccionada');
assert.strictEqual(claseDe({ clasificacion: 'USO_RESTRINGIDO' }).label, 'uso restringido');
assert.strictEqual(claseDe({ clasificacion: 'INSEGURO' }).label, 'inseguro');

// Case / spacing / hyphen drift still lands on the right placard.
assert.strictEqual(claseDe({ clasificacion: ' uso restringido ' }).key, 'USO_RESTRINGIDO');
assert.strictEqual(claseDe({ clasificacion: 'Uso-Restringido' }).key, 'USO_RESTRINGIDO');

// Anything unrecognised is "sin dato", never silently a placard.
assert.strictEqual(claseDe({ clasificacion: '' }).key, 'SIN_DATO');
assert.strictEqual(claseDe({}).key, 'SIN_DATO');
assert.strictEqual(claseDe({ clasificacion: 'OTRA_COSA' }).key, 'SIN_DATO');

// Counts cover every class, including the ones with no records.
const counts = contarPorClase([
  { clasificacion: 'INSEGURO' },
  { clasificacion: 'INSEGURO' },
  { clasificacion: 'inspeccionada' },
  { clasificacion: '' },
]);
assert.deepStrictEqual(counts, {
  INSPECCIONADA: 1, USO_RESTRINGIDO: 0, INSEGURO: 2, SIN_DATO: 1,
});
assert.deepStrictEqual(contarPorClase([]), {
  INSPECCIONADA: 0, USO_RESTRINGIDO: 0, INSEGURO: 0, SIN_DATO: 0,
});

console.log('ok — evaluaciones.js placard classification');

// --- faseDe: Fase I/II derived from inspector.np -----------------------------
assert.deepStrictEqual(FASES.map((f) => f.key), ['FASE_II', 'FASE_I']);
assert.strictEqual(faseDe({ inspector: { np: 'P3' } }).key, 'FASE_II');
assert.strictEqual(faseDe({ inspector: { np: 'P3' } }).label, 'fase II');
assert.strictEqual(faseDe({ inspector: { np: 'P1' } }).key, 'FASE_I');
assert.strictEqual(faseDe({ inspector: { np: 'P1' } }).label, 'fase I');
assert.strictEqual(faseDe({ inspector: { np: '' } }).key, 'FASE_I');
assert.strictEqual(faseDe({ inspector: {} }).key, 'FASE_I');

// --- applyFilters: fase filter clause ----------------------------------------
const evaluacionesFase = [
  { id: '1', inspector: { np: 'P3' }, descripcion: {}, clasificacion: '' },
  { id: '2', inspector: { np: 'P1' }, descripcion: {}, clasificacion: '' },
  { id: '3', inspector: {}, descripcion: {}, clasificacion: '' }, // no NP -> Fase I default
];
assert.deepStrictEqual(
  applyFilters(evaluacionesFase, { fase: 'FASE_II' }).map((e) => e.id),
  ['1'],
);
assert.deepStrictEqual(
  applyFilters(evaluacionesFase, { fase: 'FASE_I' }).map((e) => e.id),
  ['2', '3'],
);
assert.deepStrictEqual(
  applyFilters(evaluacionesFase, { fase: '' }).map((e) => e.id),
  ['1', '2', '3'],
);

console.log('ok — faseDe + applyFilters fase filter');

// ── Fase "sin dato" for the atencionsismo source (design D1 step 3) ─────
assert.strictEqual(FASE_SIN_DATO.key, 'SIN_DATO');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', inspector: { np: '' } }).key, 'SIN_DATO');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', inspector: { np: '  ' } }).key, 'SIN_DATO');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', inspector: { np: 'P4' } }).key, 'FASE_II');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', inspector: { np: 'P1' } }).key, 'FASE_I');
// Firestore source keeps today's rule: unknown NP defaults to Fase I.
assert.strictEqual(faseDe({ fuente: 'firestore', inspector: { np: '' } }).key, 'FASE_I');
assert.strictEqual(faseDe({ inspector: { np: '' } }).key, 'FASE_I');
assert.strictEqual(faseDe({}).key, 'FASE_I');
// Edge cases beyond the plan: null input, and an atencionsismo record with
// no inspector object at all (not just an empty np).
assert.strictEqual(faseDe(null).key, 'FASE_I');
assert.strictEqual(faseDe({ fuente: 'atencionsismo' }).key, 'SIN_DATO');

const sinDato = { fuente: 'atencionsismo', inspector: { np: '', nombre_completo: '', codigo: '' },
  descripcion: { nombre: '', direccion: 'Calle 9' }, codigo_edificacion: '', clasificacion: 'INSEGURO' };
assert.strictEqual(applyFilters([sinDato], { fase: 'SIN_DATO' }).length, 1);
assert.strictEqual(applyFilters([sinDato], { fase: 'FASE_I' }).length, 0);
assert.strictEqual(applyFilters([sinDato], { search: 'calle 9' }).length, 1);

// Edge case beyond the plan: a Firestore record must NOT match the
// 'SIN_DATO' fase filter, even with an empty np (it defaults to Fase I).
const firestoreVacio = { fuente: 'firestore', inspector: { np: '' }, descripcion: {}, clasificacion: '' };
assert.strictEqual(applyFilters([firestoreVacio], { fase: 'SIN_DATO' }).length, 0);
assert.strictEqual(applyFilters([firestoreVacio], { fase: 'FASE_I' }).length, 1);

console.log('evaluaciones.test.mjs: fase sin dato OK');
