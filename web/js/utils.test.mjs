// Self-check for address-aware search normalization. Run: node web/js/utils.test.mjs
import assert from 'node:assert/strict';
import {
  normalizeAddressText, buildSearchIndex, barrioVeredaDisplay, resolveBarrioVereda, labelForField,
  filterOptionsByLabel, mountCombobox, isTypedAddress, addressDisplay,
  danoGradoColor, DANO_GRADO_ORDER, formatValue, COLORS, sourceLabel, setSourceLabels,
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

// --- normalizeAddressText: abbreviation glued to the number (bug fix) ------
// `\b` does not separate a letter from a digit it's glued to, so WAY_TYPE_MAP
// used to skip "Carrera77" entirely -- and running the collapse BEFORE the
// digit/letter split meant it never got a second chance either. Both issues
// together meant "carrera 77" (own index term "cr 77...") could never match
// a record indexed from the glued form. Digit-glued and space-separated
// forms of the same address must now produce the exact same token stream.
assert.equal(normalizeAddressText('Carrera77 #1c-140'), normalizeAddressText('Carrera 77 #1c-140'));
assert.equal(normalizeAddressText('CARRERA77'), normalizeAddressText('Carrera 77'));
assert.equal(normalizeAddressText('Calle3D # 45-23'), normalizeAddressText('Calle 3D # 45-23'));

// A search for "carrera 77" must find a record indexed from the glued form.
const gluedIndex = buildSearchIndex({ direccion: 'Carrera77 #1c-140' });
const spacedQuery = normalizeAddressText('carrera 77').split(' ').filter(Boolean);
assert.ok(spacedQuery.every((tok) => gluedIndex.includes(tok)), 'glued-form index should match a spaced query');

// --- normalizeAddressText: "kr"/"k" as carrera aliases ---------------------
// "kr" (no vowels) was missing from WAY_TYPE_MAP's carrera group even though
// direccion_norm (backend) emits "KR" as the canonical IGAC code -- so
// searching "kr 96" would not find data whose search index came from
// "carrera 96" (and vice versa) despite both meaning the same road.
assert.equal(normalizeAddressText('kr 96'), normalizeAddressText('carrera 96'));
assert.equal(normalizeAddressText('kr96'), normalizeAddressText('carrera 96'));

// Lone "k" glued to a digit is ALSO the short form of "kilometro" in the raw
// dataset ("k18", "k10.5", "k14, sector...") -- roughly 5x more often than
// it's a real carrera -- so digit-adjacency alone is not enough evidence
// (see LONE_K_RE's doc comment above). Only convert when a "#" (the
// cadastral number-sign) shows up shortly after the road number, same
// criterion as address_norm.py's backend fix -- kept in sync with it.
assert.equal(normalizeAddressText('K 67#3C-15'), normalizeAddressText('KR 67 # 3C-15'));
assert.equal(normalizeAddressText('K 58 #3 - 136 4 G'), normalizeAddressText('KR 58 # 3-136 4 G'));

// False-positive guards, all pulled from the real dataset -- none of these
// have a "#" anywhere near the "k", so none must collapse to "carrera".
assert.equal(normalizeAddressText('K18 vial al mar sector la vaca'), normalizeAddressText('k18 vial al mar sector la vaca'));
assert.ok(!normalizeAddressText('K18 vial al mar sector la vaca').includes('cr '), 'kilometer marker must not become "cr"');
assert.equal(normalizeAddressText('K10.5 Casa 6'), normalizeAddressText('k10 5 casa 6'));
assert.ok(!normalizeAddressText('K10.5 Casa 6').includes('cr'), 'kilometer with decimal must not become "cr"');
assert.ok(!normalizeAddressText('Sector Altos Los Pinos, K14').includes('cr'), 'kilometer at end of string, no "#", must not become "cr"');
assert.equal(normalizeAddressText('Km 18 via cali'), 'km 18 via cali');
assert.notEqual(normalizeAddressText('Torre K - 5').split(' ')[1], 'cr');
assert.ok(normalizeAddressText('Torre K - 5').includes(' k '), 'a dash-separated "K" must stay a bare token, not become "cr"');
assert.equal(normalizeAddressText('Bloque K'), 'bloque k');
assert.equal(normalizeAddressText('Torre K 5'), 'torre k 5');
assert.equal(normalizeAddressText('Bloque K 3'), 'bloque k 3');
assert.equal(normalizeAddressText('Manzana K 12'), normalizeAddressText('Mz K 12'));
assert.ok(!normalizeAddressText('Manzana K 12').includes('cr'), 'a bare "K" after MANZANA/MZ must not become "cr"');
assert.equal(normalizeAddressText('Mz K 5 Cs 3'), 'mz k 5 cs 3');
// A real "KR" earlier in the string must not make a later, unrelated "K"
// collapse too -- each occurrence is judged on its own local evidence.
assert.ok(!normalizeAddressText('Cra 1 K 5').endsWith('cr 5'), '"K 5" after an unrelated real carrera must stay a bare "k", not double up as another "cr"');

// --- normalizeAddressText: non-address free text is untouched --------------
assert.equal(normalizeAddressText('Clinica colombia'), 'clinica colombia');
assert.equal(normalizeAddressText('Finca El Refujio'), 'finca el refujio');

// --- normalizeAddressText: blank / whitespace-only input -------------------
assert.equal(normalizeAddressText(''), '');
assert.equal(normalizeAddressText(null), '');
assert.equal(normalizeAddressText(undefined), '');
assert.equal(normalizeAddressText('   '), '');

// --- normalizeAddressText: irregular spacing --------------------------------
assert.equal(normalizeAddressText('CL 72 W # 28 D  -  11'), normalizeAddressText('Calle 72 W 28 D 11'));
assert.equal(normalizeAddressText('Avenida 5 ta norte # 23 74 '), normalizeAddressText('Av 5 ta norte 23 74'));

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

// --- filterOptionsByLabel: default filter behind the shared mountCombobox --
// Pure logic only (this repo's web/js/*.test.mjs convention tests exported
// pure functions, not DOM — mountCombobox's keyboard/mousedown wiring is
// exercised by hand in the browser, same as the two pre-existing comboboxes
// this one generalizes, stickers-asignacion.js/planeacion.js's own).
const options = [
  { id: 'a', label: 'San Antonio' },
  { id: 'b', label: 'El Peñón' },
  { id: 'c', label: 'Santa Mónica' },
];
assert.deepEqual(filterOptionsByLabel(options, ''), options);
assert.deepEqual(filterOptionsByLabel(options, undefined), options);
assert.deepEqual(filterOptionsByLabel(options, 'san').map((o) => o.id), ['a', 'c']); // Santa -> normalize 'santa' includes 'san'
assert.deepEqual(filterOptionsByLabel(options, 'penon').map((o) => o.id), ['b']); // accent-insensitive
assert.deepEqual(filterOptionsByLabel(options, 'PEÑÓN').map((o) => o.id), ['b']); // case-insensitive
assert.deepEqual(filterOptionsByLabel(options, 'no-existe'), []);

// Zero-padded numeric labels (e.g. comuna_barrios.json's "COMUNA 03") should
// match a query typed without the leading zero, and vice versa — an admin
// naturally types "comuna 3", not "comuna 03".
const comunas = [
  { id: '1', label: 'COMUNA 01' },
  { id: '2', label: 'COMUNA 02' },
  { id: '3', label: 'COMUNA 03' },
  { id: 'x', label: 'CORREGIMIENTO EL SALADITO' },
];
assert.deepEqual(filterOptionsByLabel(comunas, 'comuna 3').map((o) => o.id), ['3']);
assert.deepEqual(filterOptionsByLabel(comunas, 'comuna 03').map((o) => o.id), ['3']);
assert.deepEqual(filterOptionsByLabel(comunas, 'comuna 3a'), []); // no false match on a suffixed query
assert.deepEqual(filterOptionsByLabel(comunas, 'saladito').map((o) => o.id), ['x']); // non-numeric label unaffected

assert.equal(typeof mountCombobox, 'function');

// --- isTypedAddress / addressDisplay: table+modal "direccion" column -------
// direccion_norm shows as the primary value ONLY when it's a real IGAC-typed
// address (contains a road-type code as a bounded token, anywhere in the
// string -- NOT only at the start, see below); free text normalize_address
// could not typify falls back to the raw value, unlabeled as "normalized".

assert.equal(isTypedAddress('KR 96 # 48-53 BLQ 1 AP 502'), true);
assert.equal(isTypedAddress('CL 3 C # 66B-03'), true);
assert.equal(isTypedAddress('CLINICA COLOMBIA'), false);
assert.equal(isTypedAddress('FINCA EL REFUJIO'), false);
assert.equal(isTypedAddress(''), false);
assert.equal(isTypedAddress(null), false);
assert.equal(isTypedAddress(undefined), false);

// `\b` does not separate a letter from a digit it's glued to (both count as
// "word" characters to `\b`), so the road-type code is invisible to a
// `\bCODE\b`-anchored pattern when a number is glued right after it --
// exactly the shape address_norm.py's own glued-abbreviation fix now
// produces ("KR77", "CL1D", "CL12A"). Without the same fix here, the UI
// would fail to recognize the very addresses the backend fix just typified.
assert.equal(isTypedAddress('KR77 # 1C-140'), true);
assert.equal(isTypedAddress('CL1D OESTE # 12-30'), true);
assert.equal(isTypedAddress('CL12A # 56-04'), true);

// --- isTypedAddress: NOT anchored to the start -- real production bug -----
// normalize_direccion often prepends free-text context ahead of the actual
// typed address (a building/urbanización name, a rural "Km N vía" prefix).
// Requiring the code at position 0 missed every one of these even though
// normalize_address DID recognize the road type mid-string. The reported
// bug case: raw "io carrera 39 # 12c-57 Barrio Olimpico" normalizes to
// direccion_norm "IO KR 39 # 12C-57 BARRIO OLIMPICO" -- the "IO" prefix
// (evaluator noise, not a road-type code) meant the old `^`-anchored check
// classified this as untyped and showed the ugly raw string instead of the
// already-correctly-normalized one.
assert.equal(isTypedAddress('IO KR 39 # 12C-57 BARRIO OLIMPICO'), true);
assert.deepEqual(
  addressDisplay({
    direccion: 'io carrera 39 # 12c-57 Barrio Olimpico',
    direccion_norm: 'IO KR 39 # 12C-57 BARRIO OLIMPICO',
  }),
  { primary: 'IO KR 39 # 12C-57 BARRIO OLIMPICO', secondary: 'io carrera 39 # 12c-57 Barrio Olimpico' },
);

// A few more real non-anchored cases pulled from the local/production
// datasets during validation (10 local / 16 production records total carry
// a real road code that is not string-initial; see the module comment above
// IGAC_ROAD_CODE_RE for the full audit -- zero were coincidental embedded
// matches once the boundary check applies).
assert.equal(isTypedAddress('DANUBIO KR 77- # 1C-140 URBANIZACION DANUBIO'), true);
assert.equal(isTypedAddress('AGRUPACION 1 SECTOR 5 CL 62B # 1A9-75'), true);
// Rural nomenclature ("Km N vía [nombre]") -- "VI" (vía) recognized mid-string.
assert.equal(isTypedAddress('KM 2 VI CRISTO REY'), true);

// --- isTypedAddress: boundary check rejects a code glued inside another ---
// --- word, even with the anchor removed -----------------------------------
// A 2-letter code embedded inside an unrelated word (letters on both sides)
// must NOT be treated as a road-type token just because "contains" replaced
// "starts with". "CLAVEL" ("carnation", a plant name used in street/park
// naming) contains "AV" glued between "CL" and "EL" -- neither boundary is
// a non-letter, so it must stay blocked, same as it always was.
assert.equal(isTypedAddress('JARDINES CLAVEL 45'), false);
// "APTO" contains "PT" glued between "A" and "O" -- must stay blocked too.
assert.equal(isTypedAddress('MANZANA K 12 APTO 301'), false);

// Typed norm, different from raw -> norm is primary, raw rides as secondary.
assert.deepEqual(
  addressDisplay({ direccion: 'Carrera 96 # 48 - 53', direccion_norm: 'KR 96 # 48-53' }),
  { primary: 'KR 96 # 48-53', secondary: 'Carrera 96 # 48 - 53' },
);

// Blank direccion_norm -> raw alone, no secondary (nothing extra to show).
assert.deepEqual(
  addressDisplay({ direccion: 'Carrera 96 # 48 - 53', direccion_norm: '' }),
  { primary: 'Carrera 96 # 48 - 53', secondary: null },
);
assert.deepEqual(
  addressDisplay({ direccion: 'Carrera 96 # 48 - 53' }),
  { primary: 'Carrera 96 # 48 - 53', secondary: null },
);

// Untyped free text norm (normalize_address's passthrough) -> raw alone, no
// secondary duplicate of the same text under a misleading "IGAC" framing.
assert.deepEqual(
  addressDisplay({ direccion: 'Clinica colombia', direccion_norm: 'CLINICA COLOMBIA' }),
  { primary: 'Clinica colombia', secondary: null },
);

// Neither field present -> both null, no crash.
assert.deepEqual(addressDisplay({}), { primary: undefined, secondary: null });
assert.deepEqual(addressDisplay(null), { primary: undefined, secondary: null });

console.log('ok — address search normalization');
console.log('ok — barrioVeredaDisplay + geo-first field labels');
console.log('ok — filterOptionsByLabel (shared combobox default filter)');

// --- DANO_GRADO_ORDER: exact contents/length ---------------------------------
// The filter sort (data.js FILTER_FIELDS `order`) and the chart both depend on
// this exact ascending-severity order -- guard against silent reordering.
assert.deepEqual(DANO_GRADO_ORDER, ['sin_dano', 'leve', 'moderado', 'severo']);

// --- danoGradoColor: 4 known grades, each a distinct hex ---------------------
const sinDanoColor = danoGradoColor('sin_dano');
const leveColor = danoGradoColor('leve');
const moderadoColor = danoGradoColor('moderado');
const severoColor = danoGradoColor('severo');
const danoColors = [sinDanoColor, leveColor, moderadoColor, severoColor];
assert.equal(new Set(danoColors).size, 4, 'each of the 4 grades must map to a distinct color');
for (const c of danoColors) assert.match(c, /^#[0-9a-f]{6}$/i);

// The ramp must run BEST -> WORST over COLORS.severidad. Asserting only
// "4 distinct hexes" would pass just as happily with sin_dano painted dark red
// and severo painted green, which is the one failure a reader would never
// question on screen -- pin the actual mapping.
assert.equal(sinDanoColor, COLORS.severidad.sin_dano);
assert.equal(leveColor, COLORS.severidad.bajo);
assert.equal(moderadoColor, COLORS.severidad.medio);
assert.equal(severoColor, COLORS.severidad.alto);

// No grade may collide with COLORS.unknown either: the map legend renders the
// 4 grades AND "Sin dato" together, and renderPointsLegend's countByColor
// buckets by hex -- a collision would silently merge two categories' counts.
assert.equal(new Set([...danoColors, COLORS.unknown]).size, 5);

// Accent/uppercase/whitespace input normalizes the same as the canonical code.
// 'sin_daño' (ñ) is the one that actually exercises normalize()'s NFD strip --
// 'SEVERO'/'Sin_Dano' only cover case folding.
assert.equal(danoGradoColor('SEVERO'), severoColor);
assert.equal(danoGradoColor('Sin_Dano'), sinDanoColor);
assert.equal(danoGradoColor('sin_daño'), sinDanoColor);
assert.equal(danoGradoColor('  Moderado  '), moderadoColor);

// Blank / unknown codes fall back to COLORS.unknown.
assert.equal(danoGradoColor(null), COLORS.unknown);
assert.equal(danoGradoColor(undefined), COLORS.unknown);
assert.equal(danoGradoColor(''), COLORS.unknown);
assert.equal(danoGradoColor('fuerte_xyz'), COLORS.unknown);

// --- formatValue: danos_estructura + sibling danos_*/cielos_instalaciones ---
// fields route through labelForCode instead of rendering the raw snake_case code.
assert.equal(formatValue('danos_estructura', 'sin_dano'), 'Sin daño');
assert.equal(formatValue('danos_estructura', 'severo'), 'Severo');
assert.equal(formatValue('danos_contrapiso_entrepiso_muroscont', 'moderado'), 'Moderado');
assert.equal(formatValue('danos_muro_div', 'leve'), 'Leve');
assert.equal(formatValue('danos_cubierta', 'severo'), 'Severo');
assert.equal(formatValue('cielos_instalaciones', 'sin_dano'), 'Sin daño');

// Blank/null value -> 'Sin dato', regardless of field.
assert.equal(formatValue('danos_estructura', null), 'Sin dato');
assert.equal(formatValue('danos_estructura', undefined), 'Sin dato');
assert.equal(formatValue('danos_estructura', ''), 'Sin dato');

// Unknown code falls back to prettify() (labelForCode's own fallback), not to
// the raw untouched string -- e.g. underscores turned to spaces + capitalized.
assert.equal(formatValue('danos_estructura', 'fuerte_xyz'), 'Fuerte xyz');

console.log('ok — danoGradoColor + DANO_GRADO_ORDER (Daños en la estructura)');
console.log('ok — formatValue routes danos_*/cielos_instalaciones through labelForCode');

// --- sourceLabel: LABEL_OVERRIDES beats the EDAN-F3 question text -----------
// The sidebar speaks the survey's numbered wording for every other field, so
// this override is the exception, not the rule -- assert BOTH halves.
{
  setSourceLabels({
    danos_estructura: '5.7 Daño en muros de carga, columnas y otros elementos',
    severidad_danos: '6.2 Severidad de daños:',
  });

  // Overridden field: the source label loses, no matter what fallback is passed.
  assert.equal(sourceLabel('danos_estructura', 'Daños en la estructura'), 'Daños en la estructura');
  assert.equal(sourceLabel('danos_estructura', 'cualquier otro fallback'), 'Daños en la estructura');
  assert.equal(sourceLabel('danos_estructura'), 'Daños en la estructura');

  // Non-overridden field: the source label still wins over the fallback, which
  // is the whole point of the sidebar's shared language.
  assert.equal(sourceLabel('severidad_danos', 'Severidad de daños'), '6.2 Severidad de daños:');

  // Unknown field: fallback, then labelForField -- unchanged precedence.
  assert.equal(sourceLabel('nivel_dano', 'Nivel de daño'), 'Nivel de daño');
  assert.equal(sourceLabel('comuna'), labelForField('comuna'));

  // The override must survive meta.json arriving empty/absent (source_labels
  // missing is a real state: setSourceLabels(undefined) on a cold/failed load).
  setSourceLabels(undefined);
  assert.equal(sourceLabel('danos_estructura', 'x'), 'Daños en la estructura');
  assert.equal(sourceLabel('severidad_danos', 'Severidad de daños'), 'Severidad de daños');

  setSourceLabels({}); // leave the module in a clean state for later assertions
}

console.log('ok — sourceLabel override for danos_estructura');
