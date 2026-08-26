// Self-check for address-aware search normalization. Run: node web/js/utils.test.mjs
import assert from 'node:assert/strict';
import {
  normalizeAddressText, buildSearchIndex, barrioVeredaDisplay, resolveBarrioVereda, labelForField,
} from './utils.js';

// Real variants seen in the dataset for the same building should normalize
// to the same token set, and the AND-per-token match used by data.js should
// find them regardless of abbreviation/separator/order differences.
const variants = [
  'Cra 46 # 10-04',
  'Carrera 46 10 04',
  'Cr 46 10-04',
];
const normalized = variants.map(normalizeAddressText);
for (const n of normalized) assert.equal(n, normalized[0], `mismatch: "${n}" vs "${normalized[0]}"`);

// Digit/letter-suffix units match with or without a space.
assert.equal(normalizeAddressText('Cra 44a 10-30'), normalizeAddressText('Carrera 44 a 10 30'));
assert.equal(normalizeAddressText('Cr 36 B5B2 47'), normalizeAddressText('Carrera 36 b 5 b 2 47'));

// Full pipeline: index a record, confirm a differently-abbreviated query
// matches via AND-per-token (same logic as data.js applyFilters).
const index = buildSearchIndex({ direccion: 'Crra 44a #10-25', barrio_geo: 'San Fernando' });
const query = normalizeAddressText('carrera 44 10-25').split(' ').filter(Boolean);
assert.ok(query.every((tok) => index.includes(tok)), 'query tokens should all be found in the index');

// Precision: an address that only shares one token should NOT match.
const otherQuery = normalizeAddressText('calle 44 10-25').split(' ').filter(Boolean);
assert.ok(!otherQuery.every((tok) => index.includes(tok)), 'different way-type should not match');

// --- barrioVeredaDisplay ----------------------------------------------------
// "Barrio / vereda" = product of the spatial intersection against the
// barrios_veredas/comunas_corregimientos basemaps (barrio_vereda_resuelto),
// geo-first with a visible fallback to the inspector's typed value.

// Geo source: plain value, no marker.
assert.equal(
  barrioVeredaDisplay({ barrio_vereda_resuelto: 'San Fernando Nuevo', barrio_vereda_fuente: 'geo' }),
  'San Fernando Nuevo',
);

// Reported source (the ~12-record case: point outside every polygon) — the
// typed value MUST still show (never blank) and MUST carry a visible marker
// so the operator knows it is not geographic.
assert.equal(
  barrioVeredaDisplay({ barrio_vereda_resuelto: 'Vereda El Saladito', barrio_vereda_fuente: 'reportado' }),
  'Vereda El Saladito (reportado)',
);

// Neither present: sin_dato -> 'Sin dato', no crash.
assert.equal(
  barrioVeredaDisplay({ barrio_vereda_resuelto: null, barrio_vereda_fuente: 'sin_dato' }),
  'Sin dato',
);

// Records that never went through resolve_barrio_vereda() must still show a
// barrio. Two real populations carry NO resolved column:
//   1. every record already in web/data/inspections.json until the next
//      pipeline run — deploying the UI ahead of the data would otherwise
//      blank out the whole dashboard, which is worse than before the change;
//   2. the israel source, which never passes through the pipeline at all.
// The client re-applies the SAME geo-first precedence for them.
assert.equal(
  barrioVeredaDisplay({ barrio_geo: 'Alto Napoles', barrio_vereda: 'Napoles' }),
  'Alto Napoles',
);
assert.equal(
  barrioVeredaDisplay({ barrio_geo: null, barrio_vereda: 'Suba 1' }),
  'Suba 1 (reportado)',
);
// A blank string is not a value — treat it like a missing one.
assert.equal(
  barrioVeredaDisplay({ barrio_geo: '   ', barrio_vereda: 'Centro' }),
  'Centro (reportado)',
);

// Defensive: nothing at all must not throw.
assert.equal(barrioVeredaDisplay({}), 'Sin dato');
assert.equal(barrioVeredaDisplay(null), 'Sin dato');

// --- resolveBarrioVereda: the field the FILTER reads ------------------------
// data.js filters via raw `record[def.field]`, so a display-time fallback is
// not enough — the record itself must carry barrio_vereda_resuelto or the
// "Barrio / vereda" dropdown collapses to "Sin barrio asignado" for every
// record until the next pipeline run. Derived at load time for both sources.

// Pipeline-provided values are authoritative: never recomputed.
assert.deepEqual(
  resolveBarrioVereda({
    barrio_vereda_resuelto: 'Alto Napoles', barrio_vereda_fuente: 'geo',
    barrio_geo: 'IGNORAR', barrio_vereda: 'IGNORAR',
  }),
  { barrio_vereda_resuelto: 'Alto Napoles', barrio_vereda_fuente: 'geo' },
);

// Legacy record: geo wins over the inspector's typed value.
assert.deepEqual(
  resolveBarrioVereda({ barrio_geo: 'San Fernando Nuevo', barrio_vereda: 'San Fernando' }),
  { barrio_vereda_resuelto: 'San Fernando Nuevo', barrio_vereda_fuente: 'geo' },
);

// Outside every polygon: the typed value survives, tagged as reported.
assert.deepEqual(
  resolveBarrioVereda({ barrio_geo: null, barrio_vereda: 'Centro' }),
  { barrio_vereda_resuelto: 'Centro', barrio_vereda_fuente: 'reportado' },
);

// Neither: sin_dato, no crash.
assert.deepEqual(
  resolveBarrioVereda({}),
  { barrio_vereda_resuelto: null, barrio_vereda_fuente: 'sin_dato' },
);

// --- Field labels: geo-first precedence made visible in the UI -------------
assert.equal(labelForField('barrio_vereda_resuelto'), 'Barrio / vereda');
assert.equal(labelForField('barrio_vereda'), 'Barrio / vereda (reportado)');
assert.equal(labelForField('barrio_geo'), 'Barrio / vereda (geo)');
assert.equal(labelForField('comuna'), 'Comuna / corregimiento');

console.log('ok — address search normalization');
console.log('ok — barrioVeredaDisplay + geo-first field labels');
