// Self-check: fixture-based aggregation parity for the chart.update() migration
// (ADR-5's safety net — run before AND after touching upsertChart/renderStatistics).
// Run: node web/js/charts.test.mjs
import assert from 'node:assert/strict';
import { tipologiaDe, tipologiaCounts, colapsoHabCounts } from './charts.js';

// --- tipologiaDe edge cases -------------------------------------------------
assert.equal(tipologiaDe({ n_pisos: null }), 'sin_dato');
assert.equal(tipologiaDe({ n_pisos: '' }), 'sin_dato');
assert.equal(tipologiaDe({ n_pisos: 0 }), 'erroneo'); // below 1
assert.equal(tipologiaDe({ n_pisos: 1 }), 'casa');
assert.equal(tipologiaDe({ n_pisos: 3 }), 'casa');
assert.equal(tipologiaDe({ n_pisos: 4 }), 'edificacion');
assert.equal(tipologiaDe({ n_pisos: 60 }), 'edificacion');
assert.equal(tipologiaDe({ n_pisos: 61 }), 'erroneo');
assert.equal(tipologiaDe({ n_pisos: 91980 }), 'erroneo');
assert.equal(tipologiaDe({ n_pisos: 'no-numeric' }), 'erroneo');

// --- fixed record fixture ---------------------------------------------------
const fixture = [
  // casa, habitable, no colapso, 1 unidad
  { n_pisos: 2, criterio_habitabilidad: 'H', colapso_total: 'no', colapso_parcial: 'no', n_residenciales: 1 },
  // casa, no habitable (I2), colapso parcial, 2 unidades
  { n_pisos: 3, criterio_habitabilidad: 'I2', colapso_total: 'no', colapso_parcial: 'si', n_residenciales: 2 },
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

console.log('ok — charts.js aggregation parity');
