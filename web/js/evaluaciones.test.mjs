// Self-check for the pure classification logic behind the Stickers tab's
// evaluaciones panel. Run: node web/js/evaluaciones.test.mjs
import assert from 'node:assert';
import {
  claseDe, contarPorClase, CLASES, faseDe, FASES, applyFilters, FASE_SIN_DATO,
  describeFilters, COLOR_MODES, tituloDe, quienDe, inspectorFuenteLabel, fuenteFaseNotaDe,
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

// ── faseDe: contrato v3 (2026-09-08) — for `fuente === 'atencionsismo'`,
// the API's own `fase` (1 = Fase I, 2 = Fase II, API developer confirmation
// 2026-09-08) drives the pill; `inspector.np` is only the FALLBACK when
// `fase` is not exactly the number 1 or 2. Firestore-sourced records
// ignore `fase` entirely, even when present. ────────────────────────────
assert.strictEqual(faseDe({ fuente: 'atencionsismo', fase: 1, inspector: { np: 'P4' } }).key, 'FASE_I',
  'fase 1 wins over a np that would otherwise read as Fase II');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', fase: 2, inspector: { np: 'P1' } }).key, 'FASE_II',
  'fase 2 wins over a np that would otherwise read as Fase I');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', fase: null, inspector: { np: 'P4' } }).key, 'FASE_II',
  'fase null falls back to np-derived Fase');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', fase: null, inspector: { np: 'P1' } }).key, 'FASE_I',
  'fase null falls back to np-derived Fase');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', fase: null, inspector: { np: '' } }).key, 'SIN_DATO',
  'fase null with no np at all falls back to SIN_DATO');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', fase: '2', inspector: { np: 'P1' } }).key, 'FASE_I',
  'fase as the STRING "2" must not match — only the numbers 1 and 2 — falls back to np');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', fase: 3, inspector: { np: 'P1' } }).key, 'FASE_I',
  'an out-of-range fase falls back to np-derived Fase, not a crash');
// A Firestore-sourced record ignores `fase` even when present — its Fase
// still comes from inspector.np alone.
assert.strictEqual(faseDe({ fuente: 'firestore', fase: 2, inspector: { np: 'P1' } }).key, 'FASE_I',
  'a Firestore record with fase 2 and np P1 stays FASE_I: fase is atencionsismo-only');

console.log('evaluaciones.test.mjs: faseDe contrato v3 (fase field) OK');

// ── describeFilters: SIN_DATO fallback for clase/fase (xlsx header block) ───
assert.strictEqual(describeFilters({}), 'Todos los registros');
assert.ok(describeFilters({ clase: 'SIN_DATO' }).includes('sin dato'), 'clase SIN_DATO should describe as "sin dato"');
assert.ok(describeFilters({ fase: 'SIN_DATO' }).includes('sin dato'), 'fase SIN_DATO should describe as "sin dato"');
assert.ok(describeFilters({ fase: 'FASE_II' }).includes('fase II'), 'a known fase key should describe by its own label');
assert.ok(describeFilters({ clase: 'INSEGURO', fase: 'FASE_I', comuna: 'Comuna 5' })
  .includes('Comuna: Comuna 5'), 'multiple filters should all appear, joined');

// ── COLOR_MODES.fase/.clase entries must include their SIN_DATO state ───────
assert.ok(COLOR_MODES.fase.entries.some((e) => e.key === 'SIN_DATO'), 'Fase color mode should include the SIN_DATO entry');
assert.ok(COLOR_MODES.clase.entries.some((e) => e.key === 'SIN_DATO'), 'Clase color mode should include the SIN_DATO entry');

console.log('evaluaciones.test.mjs: describeFilters + COLOR_MODES SIN_DATO OK');

// ── tituloDe: fallback to 'Sin dirección' when nombre/direccion/codigo are
// all empty — the row title and modal heading must never render blank. ─────
assert.strictEqual(
  tituloDe({ descripcion: { nombre: '', direccion: '' }, codigo_edificacion: '' }),
  'Sin dirección',
);
assert.strictEqual(
  tituloDe({ descripcion: { nombre: 'Torre A', direccion: '' }, codigo_edificacion: '' }),
  'Torre A',
);
assert.strictEqual(
  tituloDe({ descripcion: { nombre: '', direccion: 'Calle 9' }, codigo_edificacion: '' }),
  'Calle 9',
);
assert.strictEqual(
  tituloDe({ descripcion: { nombre: '', direccion: '' }, codigo_edificacion: '76001-1-0040001' }),
  '76001-1-0040001',
);

console.log('evaluaciones.test.mjs: tituloDe "Sin dirección" fallback OK');

// ── quienDe: the list row's "who did this" label must never overstate an
// unverified roster fallback as a confirmed identity (misattribution-risk
// fix 2026-09-08) ─────────────────────────────────────────────────────────
assert.strictEqual(
  quienDe({ inspector: { nombre_completo: 'Ana', codigo: '004' }, inspector_fuente: 'evaluacion' }),
  'Ana',
  'a verified (evaluacion) match shows the bare name, unchanged',
);
assert.strictEqual(
  quienDe({ inspector: { nombre_completo: '', codigo: '004' }, inspector_fuente: 'evaluacion' }),
  'Brigada 004',
  'a verified match with no name falls back to "Brigada {codigo}", unchanged',
);
assert.strictEqual(
  quienDe({ inspector: { nombre_completo: 'Ana Gomez', codigo: '004' }, inspector_fuente: 'roster' }),
  'Código 004 · titular actual: Ana Gomez',
  'a roster-fallback identity is labeled as the CURRENT holder, not asserted as who did the work',
);
assert.strictEqual(
  quienDe({ inspector: { nombre_completo: '', codigo: '004' }, inspector_fuente: 'roster' }),
  'Código 004 (sin identidad verificada)',
  'roster fallback with no name at all reads as unverified, not blank',
);
assert.strictEqual(
  quienDe({ inspector: { nombre_completo: 'Ana', codigo: '004' } }),
  'Ana',
  'no inspector_fuente field at all (Firestore-sourced records) behaves exactly like today',
);

// ── quienDe: contrato v3 (2026-09-08) 'api' source — the API's own
// `profesional` names a person, a unique-cédula join, no code-reuse risk —
// so it behaves exactly like a verified 'evaluacion' match, no caveat. ─────
assert.strictEqual(
  quienDe({ inspector: { nombre_completo: 'Juan Perez', codigo: '004' }, inspector_fuente: 'api' }),
  'Juan Perez',
  'an api-sourced identity shows the bare name, same as a verified match',
);
assert.strictEqual(
  quienDe({ inspector: { nombre_completo: '', codigo: '004' }, inspector_fuente: 'api' }),
  'Brigada 004',
  'api-sourced with no name falls back to "Brigada {codigo}", same as a verified match',
);
assert.strictEqual(
  quienDe({ inspector: { nombre_completo: '', codigo: '' }, inspector_fuente: 'api' }),
  'Sin identificar',
  'api-sourced with no name and no codigo reads as unidentified',
);

console.log('evaluaciones.test.mjs: quienDe OK');

// ── inspectorFuenteLabel: xlsx export's "inspector_verificado" column ──────
assert.strictEqual(inspectorFuenteLabel({ inspector_fuente: 'evaluacion' }), 'Sí');
assert.strictEqual(inspectorFuenteLabel({ inspector_fuente: 'roster' }), 'No (código de brigada)');
// F5: 'api' is the API's own report, not a LOCAL verification — this column
// is named inspector_verificado, so it must not read as "Sí".
assert.strictEqual(inspectorFuenteLabel({ inspector_fuente: 'api' }), 'Según Atención Sismo');
assert.strictEqual(inspectorFuenteLabel({ inspector_fuente: '' }), '');
assert.strictEqual(inspectorFuenteLabel({}), '', 'Firestore-sourced records with no inspector_fuente field are also blank');

console.log('evaluaciones.test.mjs: inspectorFuenteLabel OK');

// ── F7: fuenteFaseNotaDe — divergence caveat, atencionsismo only ───────────
// The loaded list's FIRST record's fuente speaks for the whole set (one
// Fuente switch reloads the whole list from one endpoint, design D3 — the
// two sources are never merged).
assert.strictEqual(
  fuenteFaseNotaDe([{ fuente: 'atencionsismo' }]),
  'Fase según el proceso de Atención Sismo (paso 1 = Fase I, paso 2 = Fase II); '
    + 'puede diferir de la Fase por NP del Formulario.',
);
assert.strictEqual(fuenteFaseNotaDe([{ fuente: 'atencionsismo' }, { fuente: 'atencionsismo' }]) !== null, true);
assert.strictEqual(fuenteFaseNotaDe([{ fuente: 'firestore' }]), null);
assert.strictEqual(fuenteFaseNotaDe([{}]), null, 'a record with no fuente field is treated as Firestore, not atencionsismo');
assert.strictEqual(fuenteFaseNotaDe([]), null, 'no rows loaded -> nothing to caveat');
assert.strictEqual(fuenteFaseNotaDe(null), null, 'must not throw on a null list');
assert.strictEqual(fuenteFaseNotaDe(undefined), null, 'must not throw on an undefined list');

console.log('evaluaciones.test.mjs: fuenteFaseNotaDe OK');
