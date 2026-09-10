// Self-check for the pure classification logic behind the Stickers tab's
// evaluaciones panel. Run: node web/js/evaluaciones.test.mjs
import assert from 'node:assert';
import {
  claseDe, contarPorClase, CLASES, faseDe, FASES, applyFilters, FASE_SIN_DATO,
  describeFilters, COLOR_MODES, tituloDe, quienDe, inspectorFuenteLabel, fuenteFaseNotaDe,
  comunaOptionsFrom, barrioOptionsFrom, pruneToValid, pruneInvalidBarrios, toggleSetValue,
  barrioDisabledFor,
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
// comuna/barrio are Sets (multiselect conversion, 2026-09) — a single
// selected value still reads exactly like the old bare-string filter did.
assert.ok(describeFilters({ clase: 'INSEGURO', fase: 'FASE_I', comuna: new Set(['Comuna 5']) })
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

// ── comuna/barrio filters: converted from single-select <select> elements to
// Set-based multiselects (user needed to pick MORE THAN ONE comuna/barrio at
// once) — applyFilters/describeFilters below take Sets; comunaOptionsFrom/
// barrioOptionsFrom/pruneToValid are the pure helpers behind the DOM wiring
// in initEvaluaciones (option lists, and dropping stale selections). ───────

const evalsCB = [
  {
    id: 'a', _comuna: 'Comuna 1', _barrio: 'Barrio A', inspector: { np: 'P3' },
    descripcion: { direccion: 'Calle 1' }, clasificacion: 'INSEGURO',
  },
  {
    id: 'b', _comuna: 'Comuna 1', _barrio: 'Barrio B', inspector: { np: 'P1' },
    descripcion: { direccion: 'Calle 2' }, clasificacion: 'INSPECCIONADA',
  },
  {
    id: 'c', _comuna: 'Comuna 2', _barrio: 'Barrio B', inspector: { np: 'P3' },
    descripcion: { direccion: 'Calle 1' }, clasificacion: 'INSEGURO',
  },
  {
    id: 'd', _comuna: 'Comuna 3', _barrio: 'Barrio C', inspector: {},
    descripcion: {}, clasificacion: '',
  },
  // Unresolved geo (no coords, or fell outside every polygon) — must never
  // match a non-empty comuna/barrio filter.
  {
    id: 'e', _comuna: null, _barrio: null, inspector: {},
    descripcion: {}, clasificacion: '',
  },
];

// Boundary: zero comunas selected -> no filter, everything passes (same as
// the old '' single-select value).
assert.deepStrictEqual(
  applyFilters(evalsCB, { comuna: new Set() }).map((e) => e.id),
  ['a', 'b', 'c', 'd', 'e'],
);
// Boundary: exactly one comuna selected -> old single-select behaviour preserved.
assert.deepStrictEqual(
  applyFilters(evalsCB, { comuna: new Set(['Comuna 1']) }).map((e) => e.id),
  ['a', 'b'],
);
// Boundary: multiple comunas selected simultaneously -> union, not intersection.
assert.deepStrictEqual(
  applyFilters(evalsCB, { comuna: new Set(['Comuna 1', 'Comuna 3']) }).map((e) => e.id),
  ['a', 'b', 'd'],
);
// Boundary: every available comuna selected -> everything with a resolved
// comuna passes (record 'e', unresolved, still never matches).
assert.deepStrictEqual(
  applyFilters(evalsCB, { comuna: new Set(['Comuna 1', 'Comuna 2', 'Comuna 3']) }).map((e) => e.id),
  ['a', 'b', 'c', 'd'],
);
// Malformed/missing input: filters.comuna/filters.barrio undefined must not
// throw — the existing partial-filter-object tests above already rely on this.
assert.doesNotThrow(() => applyFilters(evalsCB, { fase: 'FASE_II' }));
assert.deepStrictEqual(applyFilters(evalsCB, {}).map((e) => e.id), ['a', 'b', 'c', 'd', 'e']);
// Empty Set behaves identically to the old '' for barrio too.
assert.deepStrictEqual(
  applyFilters(evalsCB, { barrio: new Set() }).map((e) => e.id),
  ['a', 'b', 'c', 'd', 'e'],
);
// A null _comuna/_barrio (unresolved geo) never matches a non-empty set.
assert.strictEqual(
  applyFilters(evalsCB, { comuna: new Set(['Comuna 1']) }).some((e) => e.id === 'e'), false,
);
assert.strictEqual(
  applyFilters(evalsCB, { barrio: new Set(['Barrio A']) }).some((e) => e.id === 'e'), false,
);
// comuna + barrio combined narrows further (AND between the two fields, OR
// within each field's own Set).
assert.deepStrictEqual(
  applyFilters(evalsCB, { comuna: new Set(['Comuna 1', 'Comuna 2']), barrio: new Set(['Barrio B']) }).map((e) => e.id),
  ['b', 'c'],
);
// Combined with clase/fase/search — extends the existing applyFilters test
// pattern above rather than inventing a new style.
assert.deepStrictEqual(
  applyFilters(evalsCB, { comuna: new Set(['Comuna 1']), clase: 'INSEGURO' }).map((e) => e.id),
  ['a'],
);
assert.deepStrictEqual(
  applyFilters(evalsCB, { comuna: new Set(['Comuna 1', 'Comuna 2']), fase: 'FASE_II' }).map((e) => e.id),
  ['a', 'c'],
);
assert.deepStrictEqual(
  applyFilters(evalsCB, { barrio: new Set(['Barrio A']), search: 'calle 1' }).map((e) => e.id),
  ['a'],
);

console.log('evaluaciones.test.mjs: applyFilters comuna/barrio Sets OK');

// ── describeFilters: multi-value comuna/barrio summary ──────────────────────
assert.strictEqual(describeFilters({ comuna: new Set() }), 'Todos los registros', 'zero selected comunas omit the field entirely');
assert.strictEqual(describeFilters({ comuna: new Set(['Comuna 5']) }), 'Comuna: Comuna 5', 'one selected value reads exactly like the old bare string');
assert.strictEqual(describeFilters({ comuna: new Set(['Comuna 5', 'Comuna 8']) }), 'Comuna: Comuna 5, Comuna 8', 'multiple values join with ", "');
assert.strictEqual(describeFilters({ barrio: new Set(['Barrio A', 'Barrio B']) }), 'Barrio: Barrio A, Barrio B');
assert.strictEqual(
  describeFilters({ clase: 'INSEGURO', comuna: new Set(['Comuna 5', 'Comuna 8']), barrio: new Set(['Barrio A']) }),
  'Clasificación: inseguro · Comuna: Comuna 5, Comuna 8 · Barrio: Barrio A',
  'combined with other active filters, all parts still appear joined',
);

console.log('evaluaciones.test.mjs: describeFilters comuna/barrio Sets OK');

// ── comunaOptionsFrom / barrioOptionsFrom / pruneToValid: pure helpers
// behind the multiselect DOM wiring in initEvaluaciones ─────────────────────
const comunaMap = new Map([
  ['Comuna 2', new Set(['Barrio C', 'Barrio B'])],
  ['Comuna 1', new Set(['Barrio A', 'Barrio B'])],
  ['Comuna 3', new Set()], // resolved comuna, no barrio resolved for any record
]);

assert.deepStrictEqual(
  comunaOptionsFrom(comunaMap).map((o) => o.value),
  ['Comuna 1', 'Comuna 2', 'Comuna 3'],
  'options are sorted, independent of Map insertion order',
);
assert.deepStrictEqual(comunaOptionsFrom(new Map()), [], 'no comuna resolved yet -> no options');

// Barrio options = union across EVERY selected comuna, not just one.
assert.deepStrictEqual(
  barrioOptionsFrom(comunaMap, new Set(['Comuna 1'])).map((o) => o.value),
  ['Barrio A', 'Barrio B'],
);
assert.deepStrictEqual(
  barrioOptionsFrom(comunaMap, new Set(['Comuna 1', 'Comuna 2'])).map((o) => o.value),
  ['Barrio A', 'Barrio B', 'Barrio C'],
  'union across two selected comunas, deduped and sorted',
);
assert.deepStrictEqual(
  barrioOptionsFrom(comunaMap, new Set()), [],
  'zero comunas selected -> barrio control has no options (reads as disabled)',
);
assert.deepStrictEqual(barrioOptionsFrom(comunaMap, new Set(['Comuna 3'])), [], 'a comuna with no resolved barrios offers none');
assert.deepStrictEqual(barrioOptionsFrom(comunaMap, undefined), [], 'must not throw when comunaSet is undefined');
assert.deepStrictEqual(barrioOptionsFrom(comunaMap, null), [], 'must not throw when comunaSet is null');

// pruneToValid: what survives a comuna/barrio no longer offered.
assert.deepStrictEqual(
  pruneToValid(new Set(['Barrio A', 'Barrio Z']), ['Barrio A', 'Barrio B']),
  new Set(['Barrio A']),
  'drops a selection no longer present, keeps the one that is still valid',
);
assert.deepStrictEqual(pruneToValid(new Set(), ['Barrio A']), new Set(), 'nothing selected stays empty');
assert.deepStrictEqual(pruneToValid(new Set(['Barrio A']), []), new Set(), 'no valid values left -> everything dropped');

console.log('evaluaciones.test.mjs: comunaOptionsFrom / barrioOptionsFrom / pruneToValid OK');

// ── State-machine transitions: comuna toggles driving the barrio Set, the
// same composition (barrioOptionsFrom + pruneToValid) initEvaluaciones's
// mountComuna()/load() use. ─────────────────────────────────────────────────

// Selecting a SECOND comuna after a barrio from the first is already
// selected keeps that barrio selected if it's still valid.
{
  const selectedComunas = new Set(['Comuna 1']);
  let selectedBarrios = new Set(['Barrio A']);
  selectedComunas.add('Comuna 2');
  selectedBarrios = pruneToValid(selectedBarrios, barrioOptionsFrom(comunaMap, selectedComunas).map((o) => o.value));
  assert.deepStrictEqual(selectedBarrios, new Set(['Barrio A']), 'Barrio A still belongs to Comuna 1, stays selected');
}

// Deselecting the comuna that OWNS a currently-selected barrio removes just
// that barrio, not unrelated ones from other still-selected comunas.
{
  const selectedComunas = new Set(['Comuna 1', 'Comuna 2']);
  let selectedBarrios = new Set(['Barrio A', 'Barrio C']); // A only under Comuna 1, C only under Comuna 2
  selectedComunas.delete('Comuna 1');
  selectedBarrios = pruneToValid(selectedBarrios, barrioOptionsFrom(comunaMap, selectedComunas).map((o) => o.value));
  assert.deepStrictEqual(selectedBarrios, new Set(['Barrio C']), 'Barrio A dropped with Comuna 1; Barrio C (still under Comuna 2) kept');
}

// Deselecting ALL comunas clears every barrio selection, and the option list
// itself becomes empty.
{
  const selectedComunas = new Set(['Comuna 1', 'Comuna 2']);
  let selectedBarrios = new Set(['Barrio A', 'Barrio C']);
  selectedComunas.clear();
  const options = barrioOptionsFrom(comunaMap, selectedComunas);
  selectedBarrios = pruneToValid(selectedBarrios, options.map((o) => o.value));
  assert.deepStrictEqual(options, [], 'no comunas selected -> no barrio options left to offer');
  assert.deepStrictEqual(selectedBarrios, new Set(), 'every barrio selection cleared');
}

// A load() refresh that no longer offers a previously-selected comuna drops
// it (and, transitively, any barrio that depended on it) — same composition,
// this time pruning the comuna Set itself against the freshly loaded map's keys.
{
  const freshComunaMap = new Map([['Comuna 2', new Set(['Barrio B', 'Barrio C'])]]); // 'Comuna 1' no longer present
  let selectedComunas = new Set(['Comuna 1', 'Comuna 2']);
  let selectedBarrios = new Set(['Barrio A', 'Barrio B']); // A only under the now-gone Comuna 1
  selectedComunas = pruneToValid(selectedComunas, freshComunaMap.keys());
  selectedBarrios = pruneToValid(selectedBarrios, barrioOptionsFrom(freshComunaMap, selectedComunas).map((o) => o.value));
  assert.deepStrictEqual(selectedComunas, new Set(['Comuna 2']), 'stale comuna dropped after refresh');
  assert.deepStrictEqual(selectedBarrios, new Set(['Barrio B']), 'barrio tied to the dropped comuna is dropped too');
}

console.log('evaluaciones.test.mjs: comuna/barrio multiselect state transitions OK');

// ── pruneInvalidBarrios: the extracted pure function initEvaluaciones's
// mountComuna() onToggle and post-load() both call instead of duplicating
// the barrioOptionsFrom + pruneToValid composition inline. ─────────────────
{
  const cbMap = new Map([
    ['Comuna 1', new Set(['Barrio A', 'Barrio B'])],
    ['Comuna 2', new Set(['Barrio B', 'Barrio C'])],
  ]);

  // A barrio that still belongs to SOME comuna in the new selection is kept.
  assert.deepStrictEqual(
    pruneInvalidBarrios(new Set(['Barrio A']), new Set(['Comuna 1']), cbMap),
    new Set(['Barrio A']),
    'a barrio still valid under the selected comunas is kept',
  );

  // A barrio that no longer belongs to ANY selected comuna is dropped.
  assert.deepStrictEqual(
    pruneInvalidBarrios(new Set(['Barrio A']), new Set(['Comuna 2']), cbMap),
    new Set(),
    'a barrio that stopped belonging to every selected comuna is dropped',
  );

  // Union semantics: a barrio belonging to a comuna that is STILL selected
  // must survive even though it also belonged to a comuna that was just
  // deselected — must not be over-pruned by the deselection.
  assert.deepStrictEqual(
    pruneInvalidBarrios(new Set(['Barrio B']), new Set(['Comuna 2']), cbMap),
    new Set(['Barrio B']),
    'Barrio B (under both Comuna 1 and Comuna 2) survives when only Comuna 1 is deselected',
  );

  // Boundary: empty comuna Set -> empty barrio Set (barrioOptionsFrom itself
  // returns no options for zero comunas selected).
  assert.deepStrictEqual(
    pruneInvalidBarrios(new Set(['Barrio A', 'Barrio B']), new Set(), cbMap),
    new Set(),
    'zero selected comunas -> every barrio dropped',
  );

  // Boundary: an already-empty barrio Set must not throw and stays empty.
  assert.deepStrictEqual(
    pruneInvalidBarrios(new Set(), new Set(['Comuna 1']), cbMap),
    new Set(),
    'an already-empty barrio Set is a no-op',
  );
}

console.log('evaluaciones.test.mjs: pruneInvalidBarrios OK');

// ── toggleSetValue: has/delete/add dance behind a multiselect's onToggle —
// mutates the Set IN PLACE (the real contract: onToggle callbacks in
// mountComuna/mountBarrio call it and then read the SAME `filters.comuna`/
// `filters.barrio` Set reference back), and returns undefined. ─────────────
{
  // Adds a value not yet present.
  const s1 = new Set(['a']);
  const ret1 = toggleSetValue(s1, 'b');
  assert.deepStrictEqual(s1, new Set(['a', 'b']), 'toggling an absent value adds it');
  assert.strictEqual(ret1, undefined, 'toggleSetValue does not return a value — callers read the mutated Set back');

  // Removes a value already present.
  const s2 = new Set(['a', 'b']);
  toggleSetValue(s2, 'b');
  assert.deepStrictEqual(s2, new Set(['a']), 'toggling a present value removes it');

  // Rapid on/off/on returns to the original state.
  const s3 = new Set(['a']);
  toggleSetValue(s3, 'x');
  toggleSetValue(s3, 'x');
  toggleSetValue(s3, 'x');
  assert.deepStrictEqual(s3, new Set(['a', 'x']), 'on/off/on lands back on "on" (odd number of toggles)');

  // Mutates in place: the same Set reference passed in is the one changed —
  // this is the real contract onToggle relies on (filters.comuna/filters.barrio
  // are never reassigned by toggleSetValue itself, only their contents).
  const original = new Set(['keep']);
  const sameRef = original;
  toggleSetValue(original, 'added');
  assert.strictEqual(sameRef, original, 'no new Set is created — the caller\'s reference is mutated directly');
  assert.ok(sameRef.has('added'), 'the mutation is visible through the original reference');
}

console.log('evaluaciones.test.mjs: toggleSetValue OK');

// ── barrioDisabledFor: the Barrio toggle button's disabled condition,
// extracted from mountBarrio()'s DOM wiring so it can be asserted without
// the DOM (renderMultiSelect/Leaflet make mountBarrio itself untestable
// under plain Node assert without jsdom). ───────────────────────────────────
assert.strictEqual(barrioDisabledFor(new Set()), true, 'zero comunas selected -> Barrio stays disabled');
assert.strictEqual(barrioDisabledFor(new Set(['Comuna 1'])), false, 'at least one comuna selected -> Barrio is enabled');
assert.strictEqual(barrioDisabledFor(new Set(['Comuna 1', 'Comuna 2'])), false, 'multiple comunas selected -> still enabled');
assert.strictEqual(barrioDisabledFor(null), true, 'must not throw on a null comunaSet — reads as disabled');
assert.strictEqual(barrioDisabledFor(undefined), true, 'must not throw on an undefined comunaSet — reads as disabled');

console.log('evaluaciones.test.mjs: barrioDisabledFor OK');
