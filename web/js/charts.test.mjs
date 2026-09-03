// Self-check: fixture-based aggregation parity for the chart.update() migration
// (ADR-5's safety net — run before AND after touching upsertChart/renderStatistics).
// Run: node web/js/charts.test.mjs
import assert from 'node:assert/strict';
import { tipologiaDe, tipologiaCounts, colapsoHabCounts, danosEstructuraCounts } from './charts.js';

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
