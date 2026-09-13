// Self-check: fixture-based aggregation parity for the chart.update() migration
// (ADR-5's safety net — run before AND after touching upsertChart/renderStatistics).
// Run: node web/js/charts.test.mjs
import assert from 'node:assert/strict';
import {
  tipologiaDe, tipologiaCounts, colapsoHabCounts, danosEstructuraCounts, baseOptions, hasChart,
} from './charts.js';
import { themeColor } from './utils.js';

// --- tipologiaDe edge cases -------------------------------------------------
assert.equal(tipologiaDe({ n_pisos: null }), 'sin_dato');
assert.equal(tipologiaDe({ n_pisos: '' }), 'sin_dato');
assert.equal(tipologiaDe({ n_pisos: 0 }), 'erroneo'); // below 1
assert.equal(tipologiaDe({ n_pisos: 1, uso_edificacion: 'residencial' }), 'casa');
assert.equal(tipologiaDe({ n_pisos: 3, uso_edificacion: 'residencial' }), 'casa');
assert.equal(tipologiaDe({ n_pisos: 4, uso_edificacion: 'residencial' }), 'edificacion');
assert.equal(tipologiaDe({ n_pisos: 60, uso_edificacion: 'residencial' }), 'edificacion');
assert.equal(tipologiaDe({ n_pisos: 61, uso_edificacion: 'residencial' }), 'erroneo');
assert.equal(tipologiaDe({ n_pisos: 91980, uso_edificacion: 'residencial' }), 'erroneo');
assert.equal(tipologiaDe({ n_pisos: 'no-numeric', uso_edificacion: 'residencial' }), 'erroneo');

// --- tipologiaDe: a casa MUST be residencial use — a low-rise commercial/
// institutional/etc. building is an edificación regardless of floor count -----
assert.equal(tipologiaDe({ n_pisos: 2, uso_edificacion: 'comercial' }), 'edificacion');
assert.equal(tipologiaDe({ n_pisos: 1, uso_edificacion: 'educativo' }), 'edificacion');
assert.equal(tipologiaDe({ n_pisos: 2 }), 'edificacion'); // no uso_edificacion at all -> not residencial
// Mixed use (comma-joined multi-value): 'residencial' present anywhere still counts.
assert.equal(tipologiaDe({ n_pisos: 2, uso_edificacion: 'comercial,residencial' }), 'casa');

// --- fixed record fixture ---------------------------------------------------
const fixture = [
  // casa, habitable, no colapso, 1 unidad
  { n_pisos: 2, uso_edificacion: 'residencial', criterio_habitabilidad: 'H', colapso_total: 'no', colapso_parcial: 'no', n_residenciales: 1 },
  // casa, no habitable (I2), colapso parcial, 2 unidades
  { n_pisos: 3, uso_edificacion: 'residencial', criterio_habitabilidad: 'I2', colapso_total: 'no', colapso_parcial: 'si', n_residenciales: 2 },
  // edificacion, no habitable (I3), colapso total, 10 unidades
  { n_pisos: 5, criterio_habitabilidad: 'I3', colapso_total: 'si', colapso_parcial: 'no', n_residenciales: 10 },
  // edificacion, habitable, no colapso, 4 unidades
  { n_pisos: 10, criterio_habitabilidad: 'H', colapso_total: 'no', colapso_parcial: 'no', n_residenciales: 4 },
  // sin dato de pisos, restringido (R1 -> no_habitable per habBinary), no colapso
  { n_pisos: null, criterio_habitabilidad: 'R1', colapso_total: 'no', colapso_parcial: 'no', n_residenciales: 0 },
  // dato de pisos erróneo, habitable, no colapso, 1 unidad
  { n_pisos: 91, criterio_habitabilidad: 'H', colapso_total: 'no', colapso_parcial: 'no', n_residenciales: 1 },
];

// --- tipologiaCounts (semáforo × tipología) ---------------------------------
const zero3 = () => ({ verde: { edif: 0, unid: 0 }, amarillo: { edif: 0, unid: 0 }, rojo: { edif: 0, unid: 0 } });
const expectedTipologia = {
  edificacion: zero3(), casa: zero3(), sin_dato: zero3(), erroneo: zero3(),
};
expectedTipologia.casa.verde = { edif: 1, unid: 1 };
expectedTipologia.casa.rojo = { edif: 1, unid: 2 };
expectedTipologia.edificacion.rojo = { edif: 1, unid: 10 };
expectedTipologia.edificacion.verde = { edif: 1, unid: 4 };
expectedTipologia.sin_dato.amarillo = { edif: 1, unid: 0 };
expectedTipologia.erroneo.verde = { edif: 1, unid: 1 };
assert.deepEqual(tipologiaCounts(fixture), expectedTipologia);

// --- colapsoHabCounts (habitabilidad/colapso × tipología) -------------------
const zeroCH = () => ({
  habitable: { reg: 0, unid: 0 },
  no_habitable: { reg: 0, unid: 0 },
  colapso_total: { reg: 0, unid: 0 },
  colapso_parcial: { reg: 0, unid: 0 },
  no_colapso: { reg: 0, unid: 0 },
});
const expectedCH = {
  edificacion: zeroCH(), casa: zeroCH(), sin_dato: zeroCH(), erroneo: zeroCH(),
};
expectedCH.casa.habitable = { reg: 1, unid: 1 };
expectedCH.casa.no_colapso = { reg: 1, unid: 1 };
expectedCH.casa.no_habitable = { reg: 1, unid: 2 };
expectedCH.casa.colapso_parcial = { reg: 1, unid: 2 };
expectedCH.edificacion.no_habitable = { reg: 1, unid: 10 };
expectedCH.edificacion.colapso_total = { reg: 1, unid: 10 };
expectedCH.edificacion.habitable = { reg: 1, unid: 4 };
expectedCH.edificacion.no_colapso = { reg: 1, unid: 4 };
expectedCH.sin_dato.no_habitable = { reg: 1, unid: 0 };
expectedCH.sin_dato.no_colapso = { reg: 1, unid: 0 };
expectedCH.erroneo.habitable = { reg: 1, unid: 1 };
expectedCH.erroneo.no_colapso = { reg: 1, unid: 1 };
assert.deepEqual(colapsoHabCounts(fixture), expectedCH);

// --- danosEstructuraCounts: the "Sin dato" bucket, which is the only branching
// this chart has. Blank AND unrecognized codes fold into it together (see the
// doc comment in charts.js for why that is deliberate, not renderByEpoca's rule).
{
  const counts = (recs) => {
    const { counts: c, sinDato } = danosEstructuraCounts(recs);
    return { ...Object.fromEntries(c), sinDato };
  };

  // Empty input: every grade at zero, no phantom "Sin dato".
  assert.deepEqual(
    counts([]),
    { sin_dano: 0, leve: 0, moderado: 0, severo: 0, sinDato: 0 },
  );

  // The four canonical grades, one each.
  assert.deepEqual(
    counts([
      { danos_estructura: 'sin_dano' }, { danos_estructura: 'leve' },
      { danos_estructura: 'moderado' }, { danos_estructura: 'severo' },
    ]),
    { sin_dano: 1, leve: 1, moderado: 1, severo: 1, sinDato: 0 },
  );

  // Case + accent variants normalize onto the canonical grade, NOT into sinDato.
  // 'sin_daño' with ñ is the case that actually exercises normalize()'s NFD strip.
  assert.deepEqual(
    counts([
      { danos_estructura: 'SEVERO' }, { danos_estructura: '  Moderado  ' },
      { danos_estructura: 'sin_daño' },
    ]),
    { sin_dano: 1, leve: 0, moderado: 1, severo: 1, sinDato: 0 },
  );

  // Blank shapes: null, undefined, '', and a missing key all land in sinDato.
  assert.deepEqual(
    counts([
      { danos_estructura: null }, { danos_estructura: undefined },
      { danos_estructura: '' }, {},
    ]),
    { sin_dano: 0, leve: 0, moderado: 0, severo: 0, sinDato: 4 },
  );

  // Unrecognized non-blank code ('fuerte' is a live KNOWN_LABELS damage code
  // that no source emits for THIS field) folds into sinDato too, matching the
  // map's COLORS.unknown / "Sin dato" legend entry for the same record.
  assert.deepEqual(
    counts([{ danos_estructura: 'fuerte' }, { danos_estructura: 'no_evaluado' }]),
    { sin_dano: 0, leve: 0, moderado: 0, severo: 0, sinDato: 2 },
  );

  // Every record is counted exactly once — grades + sinDato must equal the input.
  const mixed = [
    { danos_estructura: 'leve' }, { danos_estructura: 'leve' },
    { danos_estructura: 'severo' }, { danos_estructura: null },
    { danos_estructura: 'fuerte' }, {},
  ];
  const m = counts(mixed);
  assert.equal(m.sin_dano + m.leve + m.moderado + m.severo + m.sinDato, mixed.length);
  assert.deepEqual(m, { sin_dano: 0, leve: 2, moderado: 0, severo: 1, sinDato: 3 });
}

console.log('ok — charts.js aggregation parity');
console.log('ok — danosEstructuraCounts "Sin dato" bucket');

// --- baseOptions: themes EVERY x*/y*-prefixed scale key, not just the ------
// literal 'x'/'y' pair (W8) — a caller adding a second axis (e.g. 'y1' for a
// right-hand linear scale, seguimiento.js's dual-axis timeline chart) used to
// get NO theming at all: the old merge only ever touched the literal keys
// 'x' and 'y', so any other key from the caller's own `scales` passed through
// untouched, silently unthemed in light mode.
{
  const mutedFallback = themeColor('--text-muted', '#7c8ca3');
  const borderFallback = themeColor('--border', 'rgba(255,255,255,0.10)');

  const opts = baseOptions({ scales: { y1: { position: 'right' } } });
  assert.equal(opts.scales.y1.ticks.color, mutedFallback, 'a new y*-prefixed key must get the themed tick color');
  assert.equal(opts.scales.y1.grid.color, borderFallback, 'a new y*-prefixed key must get the themed grid color');
  assert.equal(opts.scales.y1.position, 'right', 'the caller override itself must survive the merge');
  assert.equal(opts.scales.y1.beginAtZero, true, 'a y-like axis gets the same beginAtZero default as the literal y axis');

  // Existing x/y behavior is unchanged by the generalization.
  assert.equal(opts.scales.x.ticks.color, mutedFallback);
  assert.equal(opts.scales.y.beginAtZero, true);
  assert.equal(opts.scales.y.ticks.color, mutedFallback);

  // An override on the literal 'y' key still survives alongside the themed
  // defaults (same one-level merge contract as before).
  const opts2 = baseOptions({ scales: { y: { type: 'logarithmic' } } });
  assert.equal(opts2.scales.y.type, 'logarithmic');
  assert.equal(opts2.scales.y.ticks.color, mutedFallback);

  // A non-x/non-y scale key (unusual, but must not crash) passes through
  // VERBATIM — no themed grid/ticks synthesized at all (L8: the merge used to
  // inject empty `grid:{}`/`ticks:{}` objects into a key like 'r' even though
  // the axis-like defaults never applied to it and the caller never asked for
  // them).
  const opts3 = baseOptions({ scales: { r: { min: 0 } } });
  assert.equal(opts3.scales.r.min, 0);
  assert.deepEqual(opts3.scales.r, { min: 0 }, 'non-axis-like key must pass through verbatim, no injected grid/ticks keys');
}

// --- hasChart (B1): registry.has() by canvas id, no DOM lookup needed ------
// (upsertChart itself is DOM-bound via document.getElementById and can't be
// exercised from this Node-only self-check — this only covers the "nothing
// registered" branch; the "registered" branch is covered by the pure
// shouldSkipChartRender() decision helper in seguimiento.test.mjs, which is
// what actually gates the B1 regression).
assert.equal(hasChart('a-canvas-id-nothing-ever-registers'), false, 'an id nothing has registered must read as absent');
console.log('hasChart: registry.has() passthrough, no DOM required OK');
console.log('baseOptions: themes every x*/y*-prefixed scale key (W8) OK');
