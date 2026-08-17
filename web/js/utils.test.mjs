// Self-check for address-aware search normalization. Run: node web/js/utils.test.mjs
import assert from 'node:assert/strict';
import { normalizeAddressText, buildSearchIndex } from './utils.js';

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

console.log('ok — address search normalization');
