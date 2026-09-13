// Self-check for the pure aggregation helpers behind the Seguimiento tab.
// Run: node web/js/seguimiento.test.mjs
import assert from 'node:assert/strict';
import {
  normalizeName, cedulaKey, professionalKeyOf, buildIdentityIndex,
  buildProfessionalRows, buildTimeline, sortRows,
  professionalRecords, buildTemporalMetrics, buildTemporalMetricsByKey,
  buildBarriosActivos, buildBarriosActivosByKey,
  buildProfessionalReportDocDefinition, hasActiveSegFilters,
  createSegCache, createIdentityCache, makeSearchController,
  DASH, DEGRADED_STICKERS_NOTE, reportSelectedButtonState,
  kpiTotals, matchesSearch, visibleRowsFor, degradedStickerNote, unassignedNote, kpisHtml,
  timelineChartConfig, timelineDataKey, shouldSkipChartRender,
  COLUMNS_TOTALES, COLUMNS_TEMPORALES, columnsFor, defaultSortFor, formatMinutes, xlsxRowsFor, xlsxFiltersSummary,
  objetivoDiario, visitasUltimos7Dias, reportFilenameSlug, buildMassReportDocDefinition,
} from './seguimiento.js';

// Small test-local helper: the "no identity/no cédula at all" key shape a
// name-only record resolves to — every existing single-professional fixture
// below (no `identificacion`/`cedula`) uses this instead of the bare
// normalizeName() output the OLD row.key/professionalKey used to be (D7,
// plan §Decisiones: join by cédula first, name key is now PREFIXED so it can
// never collide with a 'ced:...' key).
const nomKey = (name) => `nom:${normalizeName(name)}`;

// ── normalizeName ──────────────────────────────────────────────────────────

assert.equal(normalizeName('Juan Pérez'), 'juan perez');
assert.equal(normalizeName('  Juan   Pérez  '), 'juan perez');
assert.equal(normalizeName('JUAN PEREZ'), 'juan perez');
assert.equal(normalizeName('juan perez'), normalizeName('JUAN   PÉREZ'));
assert.equal(normalizeName(null), '');
assert.equal(normalizeName(undefined), '');
assert.equal(normalizeName(''), '');
assert.equal(normalizeName('   '), '');
console.log('normalizeName OK');

// ── cedulaKey ───────────────────────────────────────────────────────────────
// Mirrors backend's cedula_key (stickers_atencionsismo.py:220) EXACTLY:
// digits-only, no leading-zero stripping (decision: kept verbatim, same as
// the backend, which only strips non-digits and does nothing else).

assert.equal(cedulaKey('1.234.567'), '1234567');
assert.equal(cedulaKey(' 1234567 '), '1234567');
assert.equal(cedulaKey('1234567'), '1234567');
assert.equal(cedulaKey(1234567), '1234567');
assert.equal(cedulaKey('CC'), '');
assert.equal(cedulaKey(''), '');
assert.equal(cedulaKey(null), '');
assert.equal(cedulaKey(undefined), '');
// Leading-zero policy: digits kept verbatim, never stripped -- "0123" and
// "123" are DIFFERENT keys (a cédula legitimately starting with 0 must not
// collide with one that doesn't).
assert.equal(cedulaKey('0123'), '0123');
assert.notEqual(cedulaKey('0123'), cedulaKey('123'));
console.log('cedulaKey OK');

// ── buildIdentityIndex / professionalKeyOf: the single identity resolver ──
// D7 (plan §Decisiones): join by cédula first; unify name -> cédula ONLY
// when the name maps to exactly one eligible cédula across the whole
// sticker set; a cédula seen ONLY via inspector_fuente === 'roster' is never
// an eligible merge key (misattribution risk, stickers_atencionsismo.py:20-27).

{
  // No identity at all (default) -> every record resolves to its own
  // 'nom:normalizedName' bucket, exactly the old normalizeName-based
  // behavior except for the 'nom:' prefix (never collides with 'ced:').
  const sticker = { inspector: { nombre_completo: 'Gil Soto' } };
  assert.equal(professionalKeyOf(sticker), nomKey('Gil Soto'));
  assert.equal(professionalKeyOf(sticker, undefined), nomKey('Gil Soto'));
  const survey = { nombre_evaluador: 'Gil Soto' };
  assert.equal(professionalKeyOf(survey), nomKey('Gil Soto'));
  assert.equal(professionalKeyOf(null), '');
  assert.equal(professionalKeyOf({ inspector: {} }), '');
  assert.equal(professionalKeyOf({ nombre_evaluador: '' }), '');
}
console.log('professionalKeyOf: no-identity default (nom: prefix) OK');

{
  // A sticker's own eligible cédula (>=1 non-roster occurrence) always wins,
  // regardless of name.
  const stickers = [
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '1.234.567' }, inspector_fuente: 'evaluacion' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:1234567');
}
console.log('professionalKeyOf: own eligible cédula wins OK');

{
  // A cédula that ONLY ever appears via inspector_fuente === 'roster' is
  // NEVER eligible as a merge key -- even a record carrying it resolves via
  // name instead (falling back to nom: since the name maps to nothing else).
  const stickers = [
    { inspector: { nombre_completo: 'Beto Ríos', identificacion: '999' }, inspector_fuente: 'roster' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  assert.equal(professionalKeyOf(stickers[0], identity), nomKey('Beto Ríos'), 'roster-only cédula must never become ced:999');
}
console.log('professionalKeyOf: roster-only cédula never eligible OK');

{
  // Name -> cédula unification: a name-only record (Survey, no cédula at
  // all) attaches to the SAME cédula-based row when the name maps to
  // exactly ONE eligible cédula among the stickers.
  const stickers = [
    { inspector: { nombre_completo: 'Carla Nova', identificacion: '555' }, inspector_fuente: 'evaluacion' },
  ];
  const survey = { nombre_evaluador: 'CARLA   nova' }; // case/spacing drift, same normalized name
  const identity = buildIdentityIndex({ stickers, surveys: [survey] });
  assert.equal(professionalKeyOf(survey, identity), 'ced:555');
}
console.log('professionalKeyOf: unique name->cédula unifies a name-only Survey record OK');

{
  // Homonyms with DIFFERENT cédulas: the name maps to >=2 eligible cédulas
  // -> each sticker keeps its OWN cédula key (never merged under the shared
  // name), and a name-only record (no cédula) falls into its own separate
  // 'nom:' bucket instead of guessing which of the two it belongs to --
  // "nombre con dos cédulas -> 3 filas" (plan W5 edge case).
  const stickers = [
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '111' }, inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '222' }, inspector_fuente: 'evaluacion' },
  ];
  const survey = { nombre_evaluador: 'Luis Gil' };
  const identity = buildIdentityIndex({ stickers, surveys: [survey] });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:111');
  assert.equal(professionalKeyOf(stickers[1], identity), 'ced:222');
  assert.equal(professionalKeyOf(survey, identity), nomKey('Luis Gil'), 'ambiguous name-only record must not guess a cédula');
  assert.equal(identity.ambiguousNames.has(normalizeName('Luis Gil')), true);
  // Distinct keys -> distinct rows once buildProfessionalRows runs (checked below).
  assert.notEqual(professionalKeyOf(stickers[0], identity), professionalKeyOf(stickers[1], identity));
}
console.log('professionalKeyOf: homonyms with different cédulas separate into 3 buckets OK');

{
  // profiles: merged display fields per resolved key -- "most frequent raw
  // spelling wins" for the name (same rule as before), "first non-blank
  // wins" for cedula/codigo/entidad/np/tarjetaProfesional/celular/correo.
  const stickers = [
    { inspector: {
      nombre_completo: 'María José López', identificacion: '123', codigo: 'B1', entidad: 'DAGRD',
      np: 'P2', tarjeta_profesional: 'TP-1', num_telefono: '3001234567', correo_contacto: 'mj@example.com',
    }, inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: '  MARIA JOSE LOPEZ  ', identificacion: '123' }, inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'maria   jose lopez', identificacion: '123' }, inspector_fuente: 'evaluacion' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const key = professionalKeyOf(stickers[0], identity);
  const profile = identity.profiles.get(key);
  assert.equal(profile.name, 'María José López', 'most common raw spelling wins');
  assert.equal(profile.cedula, '123');
  assert.equal(profile.codigo, 'B1');
  assert.equal(profile.entidad, 'DAGRD');
  assert.equal(profile.np, 'P2');
  assert.equal(profile.tarjetaProfesional, 'TP-1');
  assert.equal(profile.celular, '3001234567');
  assert.equal(profile.correo, 'mj@example.com');
  assert.equal(profile.ambiguous, false);
}
console.log('buildIdentityIndex: profiles merge display fields (most-frequent name, first-non-blank contact) OK');

// ── buildIdentityIndex: SIN_DATO stickers must never drive identity (H2) ───
// A record whose Fase never resolves (faseKeyDe -> 'SIN_DATO') is dropped
// from buildProfessionalRows entirely (#29) -- but buildIdentityIndex used to
// iterate ALL stickers regardless, so a discarded record could still steer a
// profile's display name, cédula mapping or ambiguity flag. Both sticker
// loops must skip SIN_DATO as their FIRST statement.

{
  // (a) Two SIN_DATO stickers with a misspelled name outnumber a single
  // fase-1 sticker with the CORRECT spelling, same cédula -- "most frequent
  // raw spelling wins" must never even see the SIN_DATO records, so the
  // fase-1 spelling wins despite its lower raw count.
  const stickers = [
    { inspector: { nombre_completo: 'NOMBRE MAL ESCRITO', identificacion: '700' }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'NOMBRE MAL ESCRITO', identificacion: '700' }, fecha: '2026-01-02', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Nombre Bien Escrito', identificacion: '700' }, fecha: '2026-01-03', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const key = professionalKeyOf(stickers[2], identity);
  assert.equal(key, 'ced:700');
  const profile = identity.profiles.get(key);
  assert.equal(profile.name, 'Nombre Bien Escrito', 'SIN_DATO records must never outvote the real fase-1 spelling');
}
console.log('buildIdentityIndex: SIN_DATO stickers never drive the display name (a) OK');

{
  // (b) A fase-1 sticker {Ana Lopez, 888} + a SIN_DATO sticker {Ana Lopez,
  // 889} + a Survey record for "Ana Lopez" -- the SIN_DATO cédula must never
  // become an eligible merge key, so the name maps to exactly ONE eligible
  // cédula (888) and everything (sticker + Survey) merges into ONE row,
  // never split into an ambiguous pair + a detached nom: row.
  const stickers = [
    { inspector: { nombre_completo: 'Ana Lopez', identificacion: '888' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Ana Lopez', identificacion: '889' }, fecha: '2026-01-02', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const surveys = [{ nombre_evaluador: 'Ana Lopez', fecha_inspeccion: '2026-01-03' }];
  const result = buildProfessionalRows({ stickers, surveys });
  assert.equal(result.rows.length, 1, 'the SIN_DATO cédula must never fragment this into an ambiguous pair + detached nom: row');
  assert.equal(result.rows[0].key, 'ced:888');
  assert.equal(result.rows[0].stickersTotal, 1, 'only the fase-1 sticker counts (SIN_DATO excluded)');
  assert.equal(result.rows[0].surveyTotal, 1);
  assert.equal(result.rows[0].ambiguous, false);
}
console.log('buildIdentityIndex: SIN_DATO cédula never fragments a single-professional merge (b) OK');

// ── buildProfessionalRows: empty inputs ────────────────────────────────────

{
  const result = buildProfessionalRows({ stickers: [], surveys: [] });
  assert.deepEqual(result.rows, []);
  assert.deepEqual(result.unassigned, { stickers: 0, surveys: 0 });
  assert.equal(result.stickersWithoutDate, 0);
  assert.deepEqual(result.totals, {
    professionals: 0, stickers: 0, surveys: 0, avgPerProfessional: 0, unassigned: 0, stickersWithoutDate: 0,
  });
}

// Also tolerant of missing/undefined stickers/surveys arrays entirely.
{
  const result = buildProfessionalRows({});
  assert.deepEqual(result.rows, []);
  assert.equal(result.totals.professionals, 0);
}
console.log('buildProfessionalRows: empty inputs OK');

// ── buildProfessionalRows: null/undefined/empty name fields -> unassigned ──

{
  const stickers = [
    { inspector: { nombre_completo: '' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' },
    { inspector: {}, fecha: '2026-01-02', fase: 2, fuente: 'atencionsismo' },
    { fecha: '2026-01-03', fase: 1, fuente: 'atencionsismo' },
  ];
  const surveys = [
    { nombre_evaluador: '', fecha_inspeccion: '2026-01-01' },
    { fecha_inspeccion: '2026-01-02' },
  ];
  const result = buildProfessionalRows({ stickers, surveys });
  assert.deepEqual(result.rows, []);
  assert.deepEqual(result.unassigned, { stickers: 3, surveys: 2 });
  assert.equal(result.totals.stickers, 3);
  assert.equal(result.totals.surveys, 2);
  assert.equal(result.totals.unassigned, 5);
}
console.log('buildProfessionalRows: unassigned bucket OK');

// ── buildProfessionalRows: accent/case/whitespace merge into one row ───────

{
  const stickers = [
    { inspector: { nombre_completo: 'María José López', identificacion: '123', codigo: 'B1', entidad: 'DAGRD' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: '  MARIA JOSE LOPEZ  ' }, fecha: '2026-01-02', fase: 2, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'maria   jose lopez' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(result.rows.length, 1);
  const row = result.rows[0];
  assert.equal(row.name, 'María José López'); // most common raw spelling seen
  assert.equal(row.cedula, '123');
  assert.equal(row.codigo, 'B1');
  assert.equal(row.entidad, 'DAGRD');
  assert.equal(row.stickersFase1, 2);
  assert.equal(row.stickersFase2, 1);
  assert.equal(row.stickersTotal, 3);
}
console.log('buildProfessionalRows: name normalization merge OK');

// ── buildProfessionalRows: sticker with null fecha -> counted but no date ──

{
  const stickers = [
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: null, fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: '2026-02-01', fase: 1, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(result.rows.length, 1);
  const row = result.rows[0];
  assert.equal(row.stickersTotal, 2); // both counted in totals
  assert.equal(row.firstDate, '2026-02-01'); // only the dated one contributes
  assert.equal(row.lastDate, '2026-02-01');
  assert.equal(row.activeDays, 1);
  assert.equal(result.stickersWithoutDate, 1);
  assert.equal(result.totals.stickers, 2);
}
console.log('buildProfessionalRows: null-fecha sticker counted OK');

// ── buildProfessionalRows: date filter excludes rows + drops null-fecha stickers ──

{
  const stickers = [
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: null, fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: '2026-02-01', fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: '2026-03-15', fase: 1, fuente: 'atencionsismo' },
  ];
  // No filter: all 3 counted (1 without date).
  const noFilter = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(noFilter.rows[0].stickersTotal, 3);
  assert.equal(noFilter.stickersWithoutDate, 1);

  // Filtered to February only: the null-fecha sticker must drop out entirely
  // (never assumed to be "in range"), and the March sticker is excluded too.
  const filtered = buildProfessionalRows({ stickers, surveys: [], from: '2026-02-01', to: '2026-02-28' });
  assert.equal(filtered.rows[0].stickersTotal, 1);
  assert.equal(filtered.stickersWithoutDate, 0);
  assert.equal(filtered.totals.stickers, 1);

  // `to` is inclusive: a record dated exactly on the boundary still counts.
  const inclusiveTo = buildProfessionalRows({ stickers, surveys: [], from: '2026-02-01', to: '2026-02-01' });
  assert.equal(inclusiveTo.rows[0].stickersTotal, 1);

  // `to`-only filter (no `from`): everything on or before `to` counts, the
  // null-fecha record still drops (a filter is active) and the March one is excluded.
  const toOnly = buildProfessionalRows({ stickers, surveys: [], to: '2026-02-01' });
  assert.equal(toOnly.rows[0].stickersTotal, 1);
  assert.equal(toOnly.stickersWithoutDate, 0);

  // A from-only filter that excludes everything collapses the professional
  // entirely (no assigned records at all -> no row).
  const excludesAll = buildProfessionalRows({ stickers, surveys: [], from: '2027-01-01' });
  assert.deepEqual(excludesAll.rows, []);
}
console.log('buildProfessionalRows: date filter OK (incl. inclusive `to` and `to`-only) ');

// ── buildProfessionalRows: SIN_DATO stickers excluded entirely (#29, same
// rule as evaluaciones.js's Stickers tab) ──────────────────────────────────

{
  const stickers = [
    // SIN_DATO (atencionsismo, fase null, no inspector.np) -- must not count
    // anywhere: not in stickersFase1/2, not in stickersTotal, not in
    // stickersWithoutDate (it HAS a valid fecha) and not in unassigned.
    { inspector: { nombre_completo: 'Luis Gil' }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Luis Gil' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Luis Gil' }, fecha: '2026-01-03', fase: 2, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  const row = result.rows[0];
  assert.equal(row.stickersFase1, 1);
  assert.equal(row.stickersFase2, 1);
  assert.equal(row.stickersTotal, 2, 'the SIN_DATO record must never count toward the total');
  assert.equal(result.stickersWithoutDate, 0, 'a SIN_DATO record with a real fecha still must not count here');
  assert.equal(result.totals.stickers, 2);
}
console.log('buildProfessionalRows: SIN_DATO stickers excluded from Fase buckets/totals OK');

{
  // A SIN_DATO sticker with no resolvable name must not even fall into the
  // "sin profesional identificado" bucket -- it's dropped before that check.
  const stickers = [
    { inspector: {}, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.deepEqual(result.rows, []);
  assert.equal(result.unassigned.stickers, 0);
  assert.equal(result.totals.stickers, 0);
}
console.log('buildProfessionalRows: unnamed SIN_DATO sticker never counted as unassigned OK');

// ── buildProfessionalRows: fase rule shared with utils.js's faseKeyDe ──────
// (contrato v3: fase null/unrecognized on an atencionsismo sticker falls
// back to the inspector's NP category — P3+ is Fase II — same shared rule
// evaluaciones.js uses, instead of a second independent switch here.)

{
  const stickers = [
    { inspector: { nombre_completo: 'Nora Vidal', np: 'P4' }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  const row = result.rows[0];
  assert.equal(row.stickersFase2, 1);
  assert.equal(row.stickersFase1, 0);
}
console.log('buildProfessionalRows: fase fallback via inspector.np (faseKeyDe) OK');

// ── buildProfessionalRows: invalid fecha_inspeccion (ignored for dates but counted) ──

{
  const surveys = [
    { nombre_evaluador: 'Rita Paz', fecha_inspeccion: 'no-es-una-fecha' },
    { nombre_evaluador: 'Rita Paz', fecha_inspeccion: '2026-04-01' },
  ];
  const result = buildProfessionalRows({ stickers: [], surveys });
  const row = result.rows[0];
  assert.equal(row.surveyTotal, 2); // both counted
  assert.equal(row.firstDate, '2026-04-01'); // invalid one contributes no date
  assert.equal(row.lastDate, '2026-04-01');
  assert.equal(row.activeDays, 1);
}
console.log('buildProfessionalRows: invalid fecha_inspeccion OK');

// ── buildProfessionalRows: date-overflow guard ("2026-02-30" rejected) ─────
// Must never silently roll over to a real day (e.g. March 2) — treated the
// same as any other unparseable date: counted, but contributes no date.

{
  const surveys = [{ nombre_evaluador: 'Rita Paz', fecha_inspeccion: '2026-02-30' }];
  const result = buildProfessionalRows({ stickers: [], surveys });
  const row = result.rows[0];
  assert.equal(row.surveyTotal, 1);
  assert.equal(row.firstDate, null);
  assert.equal(row.lastDate, null);
  assert.equal(row.activeDays, 0);
}
console.log('buildProfessionalRows: date-overflow guard rejects 2026-02-30 OK');

// ── buildProfessionalRows: rosterSourced counting ──────────────────────────

{
  const stickers = [
    { inspector: { nombre_completo: 'Beto Ríos' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'roster' },
    { inspector: { nombre_completo: 'Beto Ríos' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Beto Ríos' }, fecha: '2026-01-03', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'roster' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(result.rows[0].rosterSourced, 2);
}
console.log('buildProfessionalRows: rosterSourced OK');

// ── buildProfessionalRows: activeDays/avgPerActiveDay across multiple days ─
// Bare dates on purpose (no time component): this test's intent is the
// activeDays/avg arithmetic, not timezone bucketing (that has its own
// dedicated buildTimeline test below) — using a machine-local-timezone-
// sensitive timestamp here would make this test flaky on CI.

{
  const stickers = [
    { inspector: { nombre_completo: 'Dana Vela' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Dana Vela' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Dana Vela' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  const row = result.rows[0];
  assert.equal(row.activeDays, 2);
  assert.equal(row.avgPerActiveDay, 1.5); // 3 dated records / 2 active days
}
console.log('buildProfessionalRows: activeDays/avgPerActiveDay OK');

// ── buildProfessionalRows: identity layer end-to-end (D7, cédula-first) ────

{
  // "nombre con dos cédulas -> 3 filas": two stickers share a name but carry
  // different eligible cédulas, plus a Survey-only record with that same
  // name (no cédula at all) -- must produce THREE separate rows, never
  // merged under the shared name.
  const stickers = [
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '111' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '222' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const surveys = [{ nombre_evaluador: 'Luis Gil', fecha_inspeccion: '2026-01-03' }];
  const result = buildProfessionalRows({ stickers, surveys });
  assert.equal(result.rows.length, 3);
  const keys = result.rows.map((r) => r.key).sort();
  assert.deepEqual(keys, ['ced:111', 'ced:222', nomKey('Luis Gil')].sort());
  const cedRow111 = result.rows.find((r) => r.key === 'ced:111');
  const cedRow222 = result.rows.find((r) => r.key === 'ced:222');
  const nomRow = result.rows.find((r) => r.key === nomKey('Luis Gil'));
  assert.equal(cedRow111.stickersTotal, 1);
  assert.equal(cedRow222.stickersTotal, 1);
  assert.equal(nomRow.surveyTotal, 1, 'the ambiguous-name Survey record must attach to its OWN nom: bucket');
  assert.equal(nomRow.stickersTotal, 0);
  // All three rows carry the ambiguous flag -- a viewer must be warned that
  // this name is shared by more than one identity.
  assert.equal(cedRow111.ambiguous, true);
  assert.equal(cedRow222.ambiguous, true);
  assert.equal(nomRow.ambiguous, true);
}
console.log('buildProfessionalRows: homonyms with different cédulas -> 3 rows OK');

{
  // A Survey-only record (no cédula at all) whose name maps to EXACTLY ONE
  // eligible cédula attaches to that professional's ced: row -- the row's
  // surveyTotal/stickersTotal both come from the SAME key, and the sum of
  // buildTimeline's per-source counts for that key matches the row totals.
  const stickers = [
    { inspector: { nombre_completo: 'Carla Nova', identificacion: '555' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const surveys = [{ nombre_evaluador: 'CARLA nova', fecha_inspeccion: '2026-01-02' }];
  const result = buildProfessionalRows({ stickers, surveys });
  assert.equal(result.rows.length, 1);
  const row = result.rows[0];
  assert.equal(row.key, 'ced:555');
  assert.equal(row.stickersTotal, 1);
  assert.equal(row.surveyTotal, 1);
  const identity = buildIdentityIndex({ stickers, surveys });
  const timeline = buildTimeline({
    stickers, surveys, identity, professionalKey: row.key,
  });
  const timelineStickers = timeline.stickers.reduce((a, b) => a + b, 0);
  const timelineSurveys = timeline.surveys.reduce((a, b) => a + b, 0);
  assert.equal(timelineStickers, row.stickersTotal);
  assert.equal(timelineSurveys, row.surveyTotal);
}
console.log('buildProfessionalRows: survey-only name unifies into the ced: row (sum(timeline)===row totals) OK');

{
  // A cédula sourced ONLY via roster must NEVER absorb a Survey record with
  // the same name -- the roster-only sticker and the Survey record land in
  // TWO different rows (the roster-only one keeps its own nom: bucket).
  const stickers = [
    { inspector: { nombre_completo: 'Beto Ríos', identificacion: '999' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'roster' },
  ];
  const surveys = [{ nombre_evaluador: 'Beto Ríos', fecha_inspeccion: '2026-01-02' }];
  const result = buildProfessionalRows({ stickers, surveys });
  // Both the sticker and the Survey record resolve via name (no eligible
  // cédula exists for "Beto Ríos" at all) -- so they DO share the same
  // nom: bucket, but it must never be 'ced:999' (the roster-only cédula).
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].key, nomKey('Beto Ríos'));
  assert.notEqual(result.rows[0].key, 'ced:999');
}
console.log('buildProfessionalRows: roster-only cédula never absorbs a Survey record under ced: OK');

{
  // New identity/contact fields (W5): first non-blank wins, exposed on the row.
  const stickers = [
    { inspector: {
      nombre_completo: 'Nora Vidal', identificacion: '777', np: 'P4',
      tarjeta_profesional: 'TP-9', num_telefono: '3009998888', correo_contacto: 'nora@example.com',
    }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  const row = result.rows[0];
  assert.equal(row.np, 'P4');
  assert.equal(row.tarjetaProfesional, 'TP-9');
  assert.equal(row.celular, '3009998888');
  assert.equal(row.correo, 'nora@example.com');
  assert.equal(row.ambiguous, false);
  assert.deepEqual(row.barriosActivos, [], 'no barrio_reportado on this fixture -> empty array, never undefined/throw');
}
console.log('buildProfessionalRows: np/tarjetaProfesional/celular/correo/barriosActivos fields present OK');

{
  // Totals must reconcile regardless of how many rows the identity split
  // produces: totals.stickers === Σ rows.stickersTotal + unassigned.stickers.
  const stickers = [
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '111' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '222' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: {}, fecha: '2026-01-03', fase: 1, fuente: 'atencionsismo' }, // unassigned (no name)
  ];
  const surveys = [
    { nombre_evaluador: 'Luis Gil', fecha_inspeccion: '2026-01-04' },
    { nombre_evaluador: '', fecha_inspeccion: '2026-01-04' }, // unassigned
  ];
  const result = buildProfessionalRows({ stickers, surveys });
  const rowsStickerSum = result.rows.reduce((n, r) => n + r.stickersTotal, 0);
  const rowsSurveySum = result.rows.reduce((n, r) => n + r.surveyTotal, 0);
  assert.equal(result.totals.stickers, rowsStickerSum + result.unassigned.stickers);
  assert.equal(result.totals.surveys, rowsSurveySum + result.unassigned.surveys);
}
console.log('buildProfessionalRows: totals reconcile across a multi-row identity split OK');

// ── buildBarriosActivos ─────────────────────────────────────────────────────

{
  // Window is inclusive of today-6, exclusive of today-7 (default days=7).
  const row = { key: 'ced:1' };
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'Barrio Adentro' }, // today (2026-01-09 Bogotá, see below)
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-03T12:00:00+00:00', barrio_reportado: 'Barrio Limite' }, // today-6, IN
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-02T12:00:00+00:00', barrio_reportado: 'Barrio Fuera' }, // today-7, OUT
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const barrios = buildBarriosActivos(row, { stickers, today: '2026-01-09', identity });
  assert.deepEqual(barrios, ['Barrio Adentro', 'Barrio Limite']);
}
console.log('buildBarriosActivos: window inclusive today-6 / exclusive today-7 OK');

{
  // Blank barrio_reportado and "Sin identificar" (case/accent-insensitive)
  // never count; accent/case variants of the SAME real barrio count once.
  const row = { key: 'ced:1' };
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'San Antonio' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T13:00:00+00:00', barrio_reportado: 'SAN ANTONIO' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T14:00:00+00:00', barrio_reportado: '' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T15:00:00+00:00', barrio_reportado: 'Sin identificar' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const barrios = buildBarriosActivos(row, { stickers, today: '2026-01-09', identity });
  assert.deepEqual(barrios, ['San Antonio']);
}
console.log('buildBarriosActivos: blank/"Sin identificar" excluded, accent/case variants dedupe OK');

{
  // Only this professional's OWN stickers count, and SIN_DATO is excluded.
  const row = { key: 'ced:1' };
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'Mío' },
    { inspector: { nombre_completo: 'Y', identificacion: '2' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'Ajeno' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: null, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'SinDato' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const barrios = buildBarriosActivos(row, { stickers, today: '2026-01-09', identity });
  assert.deepEqual(barrios, ['Mío']);
}
console.log('buildBarriosActivos: only own (non-SIN_DATO) stickers count OK');

{
  // L6: the dedupe key must strip accents, lowercase, AND collapse internal
  // whitespace -- "San  Antonio" (double space) and "San Antonio" must
  // dedupe to ONE entry, not two.
  const row = { key: 'ced:1' };
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'San  Antonio' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T13:00:00+00:00', barrio_reportado: 'San Antonio' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const barrios = buildBarriosActivos(row, { stickers, today: '2026-01-09', identity });
  assert.deepEqual(barrios, ['San  Antonio'], 'double-internal-space and single-space variants must dedupe to ONE entry (first-seen spelling kept)');
}
console.log('buildBarriosActivos: internal-whitespace variants dedupe to one entry (L6) OK');

// ── buildBarriosActivosByKey (B1): ONE pass over all stickers, keyed by ────
// professional -- buildProfessionalRows used to call buildBarriosActivos
// (a full sticker rescan) once PER ROW: 110 rows x ~3,037 stickers each
// rescanned ~ 334k identity resolutions (measured 168 ms vs 8.8 ms before
// this change). buildBarriosActivos(row, ...) above is now a thin wrapper
// over this batch builder -- same output, single pass.

{
  // Batch vs per-row equality on a mixed fixture (several professionals,
  // SIN_DATO mixed in, blank/"Sin identificar" barrios, accent/case variants,
  // out-of-window dates).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'San Antonio' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-08T12:00:00+00:00', barrio_reportado: 'SAN ANTONIO' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: null, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'Descartado' }, // SIN_DATO
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2025-12-01T12:00:00+00:00', barrio_reportado: 'Fuera De Ventana' }, // out of window
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '2' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: '' },
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '2' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T13:00:00+00:00', barrio_reportado: 'Sin identificar' },
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '2' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T14:00:00+00:00', barrio_reportado: 'Barrio Ana' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const today = '2026-01-09';
  const keys = ['ced:1', 'ced:2'];
  const byKey = buildBarriosActivosByKey({
    stickers, identity, today,
  });
  for (const key of keys) {
    const perRow = buildBarriosActivos({ key }, { stickers, today, identity });
    assert.deepEqual(byKey.get(key) || [], perRow, `batch and per-row must agree for ${key}`);
  }
  assert.deepEqual(byKey.get('ced:1'), ['San Antonio'], 'SIN_DATO excluded, out-of-window excluded, accent/case variant deduped');
  assert.deepEqual(byKey.get('ced:2'), ['Barrio Ana'], 'blank and Sin identificar excluded');
}
console.log('buildBarriosActivosByKey: batch vs per-row equality on a mixed fixture (B1) OK');

// ── buildTemporalMetrics ─────────────────────────────────────────────────────
// CONTRATO CAMBIADO (H3): firstRecordMinutes/lastRecordMinutes used to come
// from the EARLIEST day's min and the LATEST day's max respectively -- so
// "last" could end up EARLIER than "first" (e.g. 09-10 09:00, 09-10 17:00,
// 09-12 08:00 -> old first=540, old last=480). The UI's columns are "Hora
// 1er/últ. registro (día ant.)": both must come from the SAME day, `prevDay`
// (the latest day strictly before `today`) -- renamed prevDayFirstMinutes/
// prevDayLastMinutes accordingly, null when prevDay is null or that day has
// no timed record (defensive; prevDay is only ever set when it does).

{
  // Empty (no timed records at all) -> everything null, daysSinceFirst null
  // when the row has no firstDate either.
  const row = { key: nomKey('Gil Soto'), firstDate: null };
  const result = buildTemporalMetrics(row, { stickers: [], surveys: [], today: '2026-01-09' });
  assert.deepEqual(result, {
    prevDayFirstMinutes: null, prevDayLastMinutes: null,
    avgFirstMinutes: null, avgLastMinutes: null,
    prevDay: null, daysSinceFirst: null,
  });
}
console.log('buildTemporalMetrics: empty inputs OK');

{
  // Survey fecha_hora minute-of-day parsing: T00:05 -> 5, T12:05 -> 725,
  // both on the SAME (only) day, which resolves as prevDay.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-01' };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-01T00:05' },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-01T12:05' },
  ];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.prevDay, '2026-01-01');
  assert.equal(result.prevDayFirstMinutes, 5);
  assert.equal(result.prevDayLastMinutes, 725);
  assert.equal(result.avgFirstMinutes, 5);
  assert.equal(result.avgLastMinutes, 725);
}
console.log('buildTemporalMetrics: Survey fecha_hora minute-of-day (00:05->5, 12:05->725) OK');

{
  // Missing/malformed fecha_hora contributes no hour, never throws:
  // T25:00 (out-of-range hour) and T7:5 (single-digit minute, no match).
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-01' };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_hora: undefined },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-01T25:00' },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-01T7:5' },
  ];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.prevDayFirstMinutes, null);
  assert.equal(result.avgFirstMinutes, null);
}
console.log('buildTemporalMetrics: missing/malformed fecha_hora -> no hour, never throws OK');

{
  // prevDay: only a record dated TODAY -> prevDay null (never "yesterday"
  // unless there really is a timed record on a day strictly before today) --
  // and prevDayFirstMinutes/prevDayLastMinutes must be null right along with it.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-09' };
  const surveys = [{ nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-09T08:00' }];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.prevDay, null);
  assert.equal(result.prevDayFirstMinutes, null);
  assert.equal(result.prevDayLastMinutes, null);
  assert.equal(result.daysSinceFirst, 0);
}
console.log('buildTemporalMetrics: only-today record -> prevDay null, daysSinceFirst 0 OK');

{
  // prevDay resolves to the last timed day strictly before today; a
  // dated-but-UNtimed day (no fecha_hora) counts toward buildProfessionalRows'
  // activeDays but contributes NOTHING to buildTemporalMetrics' own day set.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-05' };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-08', fecha_hora: undefined }, // dated, no hour
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-07', fecha_hora: '2026-01-07T09:30' },
  ];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.prevDay, '2026-01-07', 'the undated-hour 2026-01-08 record must not count as a timed day');
  assert.equal(result.prevDayFirstMinutes, 570); // 09:30
  assert.equal(result.prevDayLastMinutes, 570);
  assert.equal(result.daysSinceFirst, 4); // 2026-01-09 - 2026-01-05
}
console.log('buildTemporalMetrics: daysSinceFirst (yesterday=1 case covered via prevDay path) + untimed day excluded OK');

{
  // daysSinceFirst: yesterday -> 1.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-08' };
  const result = buildTemporalMetrics(row, { stickers: [], surveys: [], today: '2026-01-09' });
  assert.equal(result.daysSinceFirst, 1);
}
console.log('buildTemporalMetrics: daysSinceFirst yesterday -> 1 OK');

{
  // H3 (Blocker regression, reproduced exactly): 09-10 09:00 (540), 09-10
  // 17:00 (1020), 09-12 08:00 (480) -- the OLD firstRecordMinutes/
  // lastRecordMinutes (earliest-day-min / latest-day-max) gave first=540,
  // last=480, i.e. "last" EARLIER than "first". Both prevDayFirstMinutes and
  // prevDayLastMinutes must come from THE SAME day (prevDay = 09-12, the
  // latest day strictly before today), so last >= first always holds.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-09-10' };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-09-10T09:00' },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-09-10T17:00' },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-09-12T08:00' },
  ];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-09-13' });
  assert.equal(result.prevDay, '2026-09-12');
  assert.equal(result.prevDayFirstMinutes, 480);
  assert.equal(result.prevDayLastMinutes, 480);
  assert.ok(result.prevDayLastMinutes >= result.prevDayFirstMinutes, 'last must never be earlier than first (H3)');
}
console.log('buildTemporalMetrics: prevDayFirst/LastMinutes come from the SAME day, last>=first (H3) OK');

{
  // M5: batch vs per-row equality on a mixed fixture (several professionals,
  // stickers + surveys, SIN_DATO mixed in, untimed records mixed in) --
  // buildTemporalMetricsByKey's single-pass result must match calling
  // buildTemporalMetrics() once per professional exactly.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-05T09:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-06T14:30:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-05T20:00:00+00:00', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' }, // SIN_DATO
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '2' }, fecha: '2026-01-07T12:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-08T08:15' },
    { nombre_evaluador: 'Ana Ruiz', fecha_hora: '2026-01-08T09:45' },
    { nombre_evaluador: 'Ana Ruiz', fecha_inspeccion: '2026-01-08', fecha_hora: undefined }, // dated, untimed
  ];
  const identity = buildIdentityIndex({ stickers, surveys });
  const today = '2026-01-09';
  const keys = ['ced:1', 'ced:2'];
  const byKey = buildTemporalMetricsByKey({
    stickers, surveys, identity, today,
  });
  for (const key of keys) {
    const perRow = buildTemporalMetrics({ key, firstDate: null }, {
      stickers, surveys, identity, today,
    });
    const { daysSinceFirst, ...perRowMetrics } = perRow;
    assert.deepEqual(byKey.get(key), perRowMetrics, `batch and per-row must agree for ${key}`);
  }
}
console.log('buildTemporalMetricsByKey: batch vs per-row equality on a mixed fixture (M5) OK');

// ── sortRows ────────────────────────────────────────────────────────────

{
  const rows = [
    { key: 'a', name: 'Ana', total: 5 },
    { key: 'b', name: 'Beto', total: 10 },
    { key: 'c', name: 'Caro', total: 10 }, // tie with Beto on `total`
  ];
  const byTotalDesc = sortRows(rows, 'total', 'desc');
  assert.deepEqual(byTotalDesc.map((r) => r.key), ['b', 'c', 'a']); // ties broken by key, stable
  const byTotalAsc = sortRows(rows, 'total', 'asc');
  assert.deepEqual(byTotalAsc.map((r) => r.key), ['a', 'b', 'c']);
  const byNameAsc = sortRows(rows, 'name', 'asc');
  assert.deepEqual(byNameAsc.map((r) => r.key), ['a', 'b', 'c']);
  const byNameDesc = sortRows(rows, 'name', 'desc');
  assert.deepEqual(byNameDesc.map((r) => r.key), ['c', 'b', 'a']);
  // Original array untouched (returns a new array).
  assert.deepEqual(rows.map((r) => r.key), ['a', 'b', 'c']);
}
console.log('sortRows: string vs numeric columns + ties OK');

{
  // Numeric column with a null value must not crash and sorts as lowest.
  // `activeDays` (an actual number|null column), not `lastDate`: a string
  // column like lastDate makes `typeof av === 'string' || typeof bv ===
  // 'string'` true whenever the OTHER row holds a real date, so it was
  // silently exercising the localeCompare/string branch instead of the
  // numeric -Infinity one this test claims to cover.
  //
  // A NEGATIVE real value (-5), not a positive one: a placeholder of, say,
  // 0 instead of -Infinity would still coincidentally sort null "lowest"
  // against any non-negative real value, passing this test for the WRONG
  // reason. Only a genuine -Infinity placeholder correctly sorts null below
  // a negative number too — asserted both ascending and descending so the
  // ordering is pinned in both directions, not just the one direction that
  // happens to match array insertion order.
  const rows = [
    { key: 'a', name: 'Ana', activeDays: null },
    { key: 'b', name: 'Beto', activeDays: -5 },
  ];
  const asc = sortRows(rows, 'activeDays', 'asc');
  assert.deepEqual(asc.map((r) => r.key), ['a', 'b']); // null (-Infinity) sorts before -5
  const desc = sortRows(rows, 'activeDays', 'desc');
  assert.deepEqual(desc.map((r) => r.key), ['b', 'a']); // -5 sorts before null (-Infinity) descending
}
console.log('sortRows: null-value numeric column (negative pin, both directions) OK');

// ── buildTimeline ──────────────────────────────────────────────────────────

{
  // Empty inputs -> empty timeline, never throws.
  // CONTRATO CAMBIADO (W5/W8, plan): `offsets` es nuevo -- pre-range counts
  // que W8 usará para arrancar el acumulado en un valor > 0 cuando `from`
  // deja historia fuera de la ventana visible; con inputs vacíos ambos son 0.
  const result = buildTimeline({ stickers: [], surveys: [] });
  assert.deepEqual(result, {
    labels: [], stickers: [], surveys: [], stickersCumulative: [], surveysCumulative: [],
    offsets: { stickers: 0, surveys: 0 },
  });
}
console.log('buildTimeline: empty inputs OK');

{
  // Single day: exactly one label.
  const stickers = [{ inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-05', fase: 1 }];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-05']);
  assert.deepEqual(result.stickers, [1]);
  assert.deepEqual(result.surveys, [0]);
  assert.deepEqual(result.stickersCumulative, [1]);
  assert.deepEqual(result.surveysCumulative, [0]);
}
console.log('buildTimeline: single day OK');

{
  // Gap filling: records on day 1 and day 4 must yield days 1-4 with zeros between.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-04', fase: 1 },
  ];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04']);
  assert.deepEqual(result.stickers, [1, 0, 0, 1]);
  // Running total never resets on a zero-count day, and never double-counts.
  assert.deepEqual(result.stickersCumulative, [1, 1, 1, 2]);
  assert.deepEqual(result.surveysCumulative, [0, 0, 0, 0]);
}
console.log('buildTimeline: gap filling OK');

{
  // Cumulative totals combine BOTH sources independently and monotonically,
  // even when one source is quiet on a day the other is active.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 2 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-03', fase: 1 },
  ];
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-02' },
  ];
  const result = buildTimeline({ stickers, surveys });
  assert.deepEqual(result.labels, ['2026-01-01', '2026-01-02', '2026-01-03']);
  assert.deepEqual(result.stickers, [2, 0, 1]);
  assert.deepEqual(result.surveys, [0, 1, 0]);
  assert.deepEqual(result.stickersCumulative, [2, 2, 3]);
  assert.deepEqual(result.surveysCumulative, [0, 1, 1]);
}
console.log('buildTimeline: cumulative totals per source OK');

{
  // Sticker with null fecha never enters the timeline (only totals/KPIs count it).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: null, fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
  ];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-01']);
  assert.deepEqual(result.stickers, [1]);
}
console.log('buildTimeline: null-fecha sticker excluded from timeline OK');

{
  // SIN_DATO stickers (Fase never resolved) never enter the timeline either
  // -- same exclusion rule as buildProfessionalRows (#29).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' },
  ];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.stickers, [1]);
}
console.log('buildTimeline: SIN_DATO sticker excluded OK');

{
  // professionalKey filter: only that professional's dated records count.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: '2026-01-01', fase: 1 },
  ];
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-01' },
  ];
  const all = buildTimeline({ stickers, surveys, professionalKey: null });
  assert.deepEqual(all.stickers, [2]);
  assert.deepEqual(all.surveys, [1]);
  // CONTRATO CAMBIADO (D7): professionalKey ahora es un professionalKeyOf()
  // ('nom:'/'ced:' prefijado), nunca un normalizeName() a secas.
  const onlyGil = buildTimeline({ stickers, surveys, professionalKey: nomKey('Gil Soto') });
  assert.deepEqual(onlyGil.stickers, [1]);
  assert.deepEqual(onlyGil.surveys, [1]);
}
console.log('buildTimeline: professionalKey filter OK');

// ── buildTimeline: offsets (pre-range counts, W8 will consume them) ────────

{
  // `from` null -> offset is always 0 (no "before" an unbounded window).
  const stickers = [{ inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 }];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.offsets, { stickers: 0, surveys: 0 });
}
console.log('buildTimeline: offsets — from null OK');

{
  // Records strictly before `from` count toward the offset, not the series;
  // the cumulative running total starts FROM that offset, not from zero.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2025-12-30', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2025-12-31', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
  ];
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2025-12-31' },
  ];
  const result = buildTimeline({
    stickers, surveys, from: '2026-01-01', to: '2026-01-31',
  });
  assert.deepEqual(result.offsets, { stickers: 2, surveys: 1 });
  assert.deepEqual(result.labels, ['2026-01-01']);
  assert.deepEqual(result.stickers, [1]);
  assert.deepEqual(result.stickersCumulative, [3], 'offset (2) + day-1 daily (1) = 3');
  assert.deepEqual(result.surveysCumulative, [1], 'offset (1) + day-1 daily (0) = 1');
}
console.log('buildTimeline: offsets — pre-range records accumulate, cumulative starts from offset OK');

{
  // A record with no resolvable date contributes 0 to the offset (never
  // assumed to be "before" the range) even though a date filter is active.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: null, fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-05', fase: 1 },
  ];
  const result = buildTimeline({ stickers, surveys: [], from: '2026-01-01' });
  assert.deepEqual(result.offsets, { stickers: 0, surveys: 0 });
}
console.log('buildTimeline: offsets — undated record contributes 0 OK');

{
  // A record strictly AFTER `to` is excluded entirely -- not counted in the
  // offset either (it isn't "before" the window, it's beyond it).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2025-01-01', fase: 1 }, // before `from`
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-03-01', fase: 1 }, // after `to`
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-15', fase: 1 }, // in range
  ];
  const result = buildTimeline({
    stickers, surveys: [], from: '2026-01-01', to: '2026-01-31',
  });
  assert.deepEqual(result.offsets, { stickers: 1, surveys: 0 });
  assert.deepEqual(result.labels, ['2026-01-15']);
  assert.deepEqual(result.stickers, [1]);
}
console.log('buildTimeline: offsets — record after `to` excluded entirely OK');

{
  // Date range filter applied the same way as buildProfessionalRows.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-02-01', fase: 1 },
  ];
  const result = buildTimeline({ stickers, surveys: [], from: '2026-02-01', to: '2026-02-28' });
  assert.deepEqual(result.labels, ['2026-02-01']);
  assert.deepEqual(result.stickers, [1]);
}
console.log('buildTimeline: date range filter OK');

{
  // N10: an inverted range (`to` < `from`) has no valid window at all --
  // labels must be empty (already true before this fix) AND offsets must be
  // {stickers: 0, surveys: 0} (the bug: `to < from` made every date <= `to`
  // satisfy both "not > to" and "< from", so it fell into the offset
  // instead of being excluded, producing a non-zero offset alongside empty
  // labels).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-05', fase: 1 },
  ];
  const surveys = [{ nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-03' }];
  const result = buildTimeline({
    stickers, surveys, from: '2026-02-01', to: '2026-01-01',
  });
  assert.deepEqual(result.labels, []);
  assert.deepEqual(result.offsets, { stickers: 0, surveys: 0 }, 'an inverted range must never produce a non-zero offset');
}
console.log('buildTimeline: inverted range (to < from) -> empty labels AND zero offsets (N10) OK');

// ── buildTimeline: UTC-aware timestamp buckets to the BOGOTÁ calendar day ──
// CONTRATO CAMBIADO DELIBERADAMENTE (D6, plan §Zona horaria): antes este test
// comparaba contra la zona LOCAL de la máquina que corre el test (via `new
// Date(raw).getFullYear()/getMonth()/getDate()`); ahora dateOnly/bogotaParts
// usan el offset FIJO -05:00 de Bogotá, nunca la zona del proceso -- un admin
// con laptop en otra zona debe ver el MISMO día que uno en Cali. El valor
// esperado está escrito a mano (no recalculado con la misma aritmética que
// el código bajo prueba, para no ser una aserción tautológica): 02:00 UTC
// menos 5 horas = 21:00 del día ANTERIOR en Bogotá.
{
  const raw = '2026-01-02T02:00:00+00:00';
  const stickers = [{ inspector: { nombre_completo: 'Gil Soto' }, fecha: raw, fase: 1, fuente: 'atencionsismo' }];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-01'], 'debe ser el día anterior (Bogotá, UTC-5), no el día UTC');
}
console.log('buildTimeline: UTC-aware fecha buckets to the Bogotá calendar day OK');

{
  // An offset-less, time-less date string ("YYYY-MM-DD" only, Survey's own
  // fecha_inspeccion shape) must stay EXACTLY as written — no timezone
  // conversion applies to a value that never carried a time component.
  const stickers = [{ inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-05', fase: 1, fuente: 'atencionsismo' }];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-05']);
}
console.log('buildTimeline: offset-less date-only string stays unchanged OK');

// ── professionalRecords: the raw points behind a professional's summary ────
// Per-row PDF report needs the actual stickers/surveys attributed to that
// professional ("los puntos recogidos"), not just the aggregate counts
// buildProfessionalRows already computes.

{
  const row = { key: nomKey('Gil Soto') };
  const result = professionalRecords(row, { stickers: [], surveys: [] });
  assert.deepEqual(result, { stickerPoints: [], surveyPoints: [] });
}
console.log('professionalRecords: empty inputs OK');

{
  // Only records matching the row's normalized name key are included —
  // same join rule as buildProfessionalRows/buildTimeline, case/accent-
  // insensitive.
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'GIL   sotó' }, codigo_edificacion: '76001-1-0010001', fecha: '2026-01-02', fase: 1 },
    { inspector: { nombre_completo: 'Ana Ruiz' }, codigo_edificacion: '76001-1-0010002', fecha: '2026-01-01', fase: 2 },
  ];
  const surveys = [
    { nombre_evaluador: 'gil soto', direccion: 'Cl 5 # 1-2', fecha_inspeccion: '2026-01-03' },
    { nombre_evaluador: 'Ana Ruiz', direccion: 'Cl 9 # 1-2', fecha_inspeccion: '2026-01-01' },
  ];
  const result = professionalRecords(row, { stickers, surveys });
  assert.equal(result.stickerPoints.length, 1);
  assert.equal(result.stickerPoints[0].codigo, '76001-1-0010001');
  assert.equal(result.surveyPoints.length, 1);
  assert.equal(result.surveyPoints[0].direccion, 'Cl 5 # 1-2');
}
console.log('professionalRecords: filters by normalized name (case/accent-insensitive) OK');

{
  // Chronological order, ascending; an undated sticker still appears (it IS
  // a real collected point, same reasoning as the KPI totals) but sorts
  // after every dated one.
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: '76001-1-0010003', fecha: '2026-01-03', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: '76001-1-0010001', fecha: null, fase: 2 },
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: '76001-1-0010002', fecha: '2026-01-01', fase: 2 },
  ];
  const result = professionalRecords(row, { stickers, surveys: [] });
  assert.deepEqual(result.stickerPoints.map((p) => p.codigo),
    ['76001-1-0010002', '76001-1-0010003', '76001-1-0010001']);
  assert.equal(result.stickerPoints[2].fecha, null);
}
console.log('professionalRecords: sticker points sorted chronologically, undated last OK');

{
  // Same chronological-ascending, undated-last rule for Survey points, keyed
  // off fecha_inspeccion (an invalid/garbage value counts as undated too).
  const row = { key: nomKey('Gil Soto') };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', direccion: 'C', fecha_inspeccion: '2026-01-05' },
    { nombre_evaluador: 'Gil Soto', direccion: 'A', fecha_inspeccion: 'no-es-una-fecha' },
    { nombre_evaluador: 'Gil Soto', direccion: 'B', fecha_inspeccion: '2026-01-01' },
  ];
  const result = professionalRecords(row, { stickers: [], surveys });
  assert.deepEqual(result.surveyPoints.map((p) => p.direccion), ['B', 'C', 'A']);
}
console.log('professionalRecords: survey points sorted chronologically, invalid date last OK');

{
  // Fase label uses the SAME shared rule as buildProfessionalRows (faseKeyDe)
  // -- an atencionsismo sticker with fase:null still resolves via
  // inspector.np, never falls into a separate "sin fase" bucket here that
  // would disagree with the aggregate table above it in the same tab.
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', np: 'P4' }, codigo_edificacion: '76001-1-0010001', fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
  ];
  const result = professionalRecords(row, { stickers, surveys: [] });
  assert.equal(result.stickerPoints[0].faseLabel, 'Fase II');
}
console.log('professionalRecords: fase label matches faseKeyDe (inspector.np fallback) OK');

{
  // SIN_DATO stickers never show up as one of this professional's "puntos
  // recogidos" either -- same exclusion rule as buildProfessionalRows/
  // buildTimeline (#29).
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: '76001-1-0010001', fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
  ];
  const result = professionalRecords(row, { stickers, surveys: [] });
  assert.deepEqual(result.stickerPoints, []);
}
console.log('professionalRecords: SIN_DATO sticker excluded entirely OK');

{
  // New optional from/to (W5, for the future per-period report, W10):
  // default null/null keeps the OLD "whole career, undated last" behavior
  // unchanged (already covered by every test above, none of which pass
  // from/to). When a period IS given, records are narrowed via the SAME
  // stickerIncluded/surveyIncluded rule used elsewhere -- an undated record
  // then drops out instead of being kept-and-sorted-last.
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: 'A', fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: 'B', fecha: '2026-02-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: 'C', fecha: null, fase: 1 },
  ];
  const noFilter = professionalRecords(row, { stickers, surveys: [] });
  assert.equal(noFilter.stickerPoints.length, 3, 'default (no period) keeps the undated point too');

  const filtered = professionalRecords(row, {
    stickers, surveys: [], from: '2026-01-01', to: '2026-01-31',
  });
  assert.deepEqual(filtered.stickerPoints.map((p) => p.codigo), ['A'], 'a period filter drops the out-of-range AND the undated point');
}
console.log('professionalRecords: optional from/to period filter (default = whole career) OK');

// ── buildProfessionalReportDocDefinition ────────────────────────────────────

{
  const row = {
    name: 'Gil Soto', cedula: '123', codigo: '004', entidad: 'DAGMA',
    stickersFase1: 2, stickersFase2: 1, surveyTotal: 3, total: 6,
    firstDate: '2026-01-01', lastDate: '2026-01-05', activeDays: 3, avgPerActiveDay: 2, rosterSourced: 0,
  };
  const points = {
    stickerPoints: [
      { codigo: '76001-1-0010001', direccion: 'Cl 5 # 1-2', municipio: 'Cali', fecha: '2026-01-01', faseLabel: 'Fase I' },
    ],
    surveyPoints: [
      { direccion: 'Cl 9 # 1-2', nombreEdificacion: 'Casa', fecha: '2026-01-05' },
    ],
  };
  const doc = buildProfessionalReportDocDefinition(row, points);
  const flatText = JSON.stringify(doc.content);
  assert.ok(flatText.includes('Gil Soto'), 'header should show the professional name');
  assert.ok(flatText.includes('76001-1-0010001'), 'should list the sticker code');
  assert.ok(flatText.includes('Cl 9 # 1-2'), 'should list the survey address');
  assert.ok(/Fecha de generaci/i.test(flatText), 'should include a generation-date label');
}
console.log('buildProfessionalReportDocDefinition: includes header, stats and points OK');

{
  // A professional with zero points in one or both sources must not crash
  // the builder -- it should say so in plain text, not render an empty table.
  const row = {
    name: 'Sin Puntos', cedula: '', codigo: '', entidad: '',
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0, rosterSourced: 0,
  };
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] });
  const flatText = JSON.stringify(doc.content);
  assert.ok(/sin registros/i.test(flatText), 'should say there are no points for an empty source');
}
console.log('buildProfessionalReportDocDefinition: empty point lists render a plain message OK');

{
  // A roster-sourced identity (approximate, per the cross-source caveat) is
  // flagged in the report the same way the table row flags it.
  const row = {
    name: 'Gil Soto', cedula: '', codigo: '', entidad: '',
    stickersFase1: 1, stickersFase2: 0, surveyTotal: 0, total: 1,
    firstDate: '2026-01-01', lastDate: '2026-01-01', activeDays: 1, avgPerActiveDay: 1, rosterSourced: 1,
  };
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] });
  const flatText = JSON.stringify(doc.content);
  assert.ok(/roster/i.test(flatText), 'should carry the roster-sourced caveat');
}
console.log('buildProfessionalReportDocDefinition: roster-sourced caveat included OK');

// ── hasActiveSegFilters: drives "Reiniciar filtros"' enabled/disabled state.
// Search, Desde/Hasta AND seg-chart-professional all narrow the table (W7) —
// only sort order stays excluded (never a data-narrowing filter). ──────────
assert.equal(hasActiveSegFilters(), false, 'no args at all -> nothing active');
assert.equal(hasActiveSegFilters({}), false, 'an empty object -> nothing active');
assert.equal(hasActiveSegFilters({ search: '', from: null, to: null }), false, 'the default all-empty/null shape has nothing active');
assert.equal(hasActiveSegFilters({ search: 'ana', from: null, to: null }), true, 'a non-empty search is active');
assert.equal(hasActiveSegFilters({ search: '', from: '2026-01-01', to: null }), true, 'a Desde date alone is active');
assert.equal(hasActiveSegFilters({ search: '', from: null, to: '2026-01-31' }), true, 'a Hasta date alone is active');
assert.equal(hasActiveSegFilters({ search: '', from: '2026-01-01', to: '2026-01-31' }), true, 'both dates active');
// Clearing the LAST active filter by hand must flip back to false — the real
// transition the reset button's disabled state relies on.
{
  const f = { search: '', from: '2026-01-01', to: null };
  assert.equal(hasActiveSegFilters(f), true);
  f.from = null;
  assert.equal(hasActiveSegFilters(f), false, 'clearing the only active filter (from) flips back to inactive');
}

console.log('seguimiento.test.mjs: hasActiveSegFilters OK');

// ── createSegCache: single-entry memo (W6) ──────────────────────────────────
// Key: strict identity (===) of the stickers/surveys arrays + string
// equality of from/to/professionalKey/today. A single entry -- never a Map
// -- so there is structurally never more than 1 cached result no matter how
// many distinct ranges are requested.

{
  // Same key (identical references + equal strings) -> compute() runs ONCE;
  // a second .get() with the SAME key returns the cached result.
  const cache = createSegCache();
  const stickers = [];
  const surveys = [];
  let calls = 0;
  const key = {
    stickers, surveys, from: '2026-01-01', to: '2026-01-31', professionalKey: null, today: '2026-01-15',
  };
  const r1 = cache.get(key, () => { calls += 1; return { n: calls }; });
  const r2 = cache.get({ ...key }, () => { calls += 1; return { n: calls }; });
  assert.equal(calls, 1, 'compute() must run exactly once for two .get() calls with the same key');
  assert.equal(r1, r2, 'the SECOND call must return the exact cached object, not a new one');
}
console.log('createSegCache: same key -> single execution, cached result returned OK');

{
  // A NEW array reference with EQUAL contents must still recompute --
  // identity (===), never a deep-equality/content comparison.
  const cache = createSegCache();
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  const stickersA = [{ a: 1 }];
  const stickersB = [{ a: 1 }]; // same shape, different reference
  cache.get({
    stickers: stickersA, surveys: [], from: null, to: null, professionalKey: null, today: '2026-01-15',
  }, compute);
  cache.get({
    stickers: stickersB, surveys: [], from: null, to: null, professionalKey: null, today: '2026-01-15',
  }, compute);
  assert.equal(calls, 2, 'a new array reference (even with identical contents) must recompute');
}
console.log('createSegCache: new array identity (equal contents) recomputes OK');

{
  // Changing `to` (a string field of the key) must recompute too.
  const cache = createSegCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  cache.get({
    stickers, surveys, from: '2026-01-01', to: '2026-01-31', professionalKey: null, today: '2026-01-15',
  }, compute);
  cache.get({
    stickers, surveys, from: '2026-01-01', to: '2026-02-28', professionalKey: null, today: '2026-01-15',
  }, compute);
  assert.equal(calls, 2, 'changing `to` must invalidate the single cached entry');
}
console.log('createSegCache: changing `to` recomputes OK');

{
  // <=1 entry after 50 DIFFERENT ranges -- a single-entry cache structurally
  // cannot grow, unlike a Map keyed by range that would leak one entry per
  // distinct range ever requested.
  const cache = createSegCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  for (let i = 0; i < 50; i++) {
    cache.get({
      stickers, surveys, from: `2026-01-${String(i + 1).padStart(2, '0')}`, to: null, professionalKey: null, today: '2026-01-15',
    }, () => { calls += 1; return { n: calls }; });
  }
  assert.equal(calls, 50, 'every distinct range really did recompute (not silently cached under the wrong key)');
  // L7: a REAL size() (0 or 1), not a vacuous `cache.size ? cache.size() : 1`
  // fallback that always passes regardless of what the cache actually holds.
  assert.equal(typeof cache.size, 'function', 'createSegCache must expose a real size() method');
  assert.equal(cache.size(), 1, 'a single-entry cache holds exactly 1 entry after 50 distinct ranges, never more');
}
console.log('createSegCache: <=1 entry after 50 ranges OK');

{
  // clear() drops the cached entry -- the next .get() (even with the SAME
  // key) must recompute, same as initSeguimiento()'s guard against a stale
  // cache surviving a fresh open.
  const cache = createSegCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  const key = {
    stickers, surveys, from: null, to: null, professionalKey: null, today: '2026-01-15',
  };
  cache.get(key, () => { calls += 1; return { n: calls }; });
  cache.clear();
  cache.get(key, () => { calls += 1; return { n: calls }; });
  assert.equal(calls, 2, 'clear() must force the next .get() (even with an identical key) to recompute');
}
console.log('createSegCache: clear() forces recompute OK');

// ── createIdentityCache (M4): single-entry memo keyed on stickers/surveys ──
// array identity, independent of segCache's own (from/to/professionalKey/
// today) key -- render() used to call buildIdentityIndex() UNCONDITIONALLY
// before the segCache memo check even ran (7.6 ms every render, regardless
// of whether segCache itself would hit), even though its only real inputs
// are the exact same stickers/surveys array references segCache already
// keys on.

{
  // Same stickers/surveys refs -> compute() runs ONCE across two .get() calls.
  const cache = createIdentityCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  const r1 = cache.get(stickers, surveys, compute);
  const r2 = cache.get(stickers, surveys, compute);
  assert.equal(calls, 1, 'compute() must run once for two .get() calls with identical stickers/surveys refs');
  assert.equal(r1, r2, 'the second call must return the exact cached object, not a new one');
}
console.log('createIdentityCache: same stickers/surveys refs -> single execution OK');

{
  // A NEW array reference (even with identical/empty contents) must recompute.
  const cache = createIdentityCache();
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  const surveys = [];
  cache.get([], surveys, compute);
  cache.get([], surveys, compute);
  assert.equal(calls, 2, 'a new stickers array reference must recompute, even with identical (empty) contents');
}
console.log('createIdentityCache: new array reference recomputes OK');

{
  // clear() forces the next .get() (even with the SAME refs) to recompute.
  const cache = createIdentityCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  cache.get(stickers, surveys, compute);
  cache.clear();
  cache.get(stickers, surveys, compute);
  assert.equal(calls, 2, 'clear() must force the next .get() to recompute');
}
console.log('createIdentityCache: clear() forces recompute OK');

// ── makeSearchController: debounce() wrapper with a working cancel() ────────

{
  // trigger() schedules the callback; a second trigger() before it fires
  // resets the timer (debounce semantics) -- only the LAST scheduled call
  // actually runs.
  const calls = [];
  const controller = makeSearchController((label) => calls.push(label), 20);
  controller.trigger('a');
  controller.trigger('b');
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.deepEqual(calls, ['b'], 'only the last trigger() before the wait must fire');
}
console.log('makeSearchController: trigger() debounces, only the last call fires OK');

{
  // cancel() prevents a PENDING callback from ever firing -- the real
  // scenario this exists for: a filter reset or a fresh init must be able to
  // kill a stale debounced render before it stomps new state.
  const calls = [];
  const controller = makeSearchController(() => calls.push('fired'), 20);
  controller.trigger();
  controller.cancel();
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.deepEqual(calls, [], 'cancel() must prevent the pending callback from firing at all');
}
console.log('makeSearchController: cancel() prevents a pending callback OK');

{
  // cancel() with nothing pending is a harmless no-op, and a NEW trigger()
  // after a cancel() schedules normally (cancel does not permanently disable
  // the controller).
  const calls = [];
  const controller = makeSearchController(() => calls.push('fired'), 15);
  controller.cancel(); // nothing pending -- must not throw
  controller.trigger();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.deepEqual(calls, ['fired'], 'a trigger() after a no-op cancel() still fires normally');
}
console.log('makeSearchController: cancel() with nothing pending is a no-op; later trigger() still fires OK');

// ── W7: kpiTotals ────────────────────────────────────────────────────────
// The 5 KPI tiles' values, derived from a buildProfessionalRows() result —
// "sin profesional identificado" and "stickers sin fecha" are no longer
// tiles (W7, plan), replaced by barrios activos (7 d) and the promedio
// tile now being STICKER-pace-specific (see doc comment on kpiTotals itself).

{
  // Empty rows -> everything zero, never NaN/throw.
  const result = { rows: [], totals: { professionals: 0, stickers: 0, surveys: 0 } };
  const t = kpiTotals(result, { stickersLoaded: true });
  assert.deepEqual(t, {
    professionals: 0, stickers: 0, surveys: 0, avgStickersPerDayPerProfessional: 0, barriosActivos: 0,
  });
}
console.log('kpiTotals: empty rows OK');

{
  // H3/L7: stickersLoaded=false masks EVERY sticker-derived tile behind DASH
  // — stickers total, the per-professional daily-pace average, AND (unlike
  // the old behavior) `professionals`/`barriosActivos` too:
  //  - `barriosActivos` is derived entirely from stickers (buildBarriosActivos
  //    reads `row.barriosActivos`, itself built from sticker `barrio`s) — a
  //    real 0 while stickers are still loading/failed would read as
  //    "confirmed zero active barrios", not "unknown".
  //  - `professionals` (= rows.length from buildProfessionalRows) is a MIX of
  //    professionals identified via stickers AND via Survey-only records — a
  //    row built while stickers haven't resolved yet only reflects the
  //    Survey-only subset, a partial count masquerading as the real total.
  // Only `surveys` is genuinely never sticker-derived and stays unmasked.
  const result = {
    rows: [{ stickersTotal: 4, activeDays: 2, barriosActivos: ['San Antonio'] }],
    totals: { professionals: 1, stickers: 4, surveys: 0 },
  };
  const t = kpiTotals(result, { stickersLoaded: false });
  assert.equal(t.stickers, DASH);
  assert.equal(t.avgStickersPerDayPerProfessional, DASH);
  assert.equal(t.barriosActivos, DASH, 'H3: barriosActivos is sticker-derived, must be DASH-masked too');
  assert.equal(t.professionals, DASH, 'L7: professionals is a partial (sticker-loading-dependent) count while !stickersLoaded, must be DASH-masked too');
  // Survey count is NOT sticker-derived — never masked.
  assert.equal(t.surveys, 0);
}
console.log('kpiTotals: stickersLoaded=false masks every sticker-derived tile (stickers/pace/barriosActivos/professionals) behind DASH (H3/L7) OK');

{
  // M6: avgStickersPerDayPerProfessional = mean, ACROSS PROFESSIONALS WITH AT
  // LEAST ONE STICKER-ACTIVE DAY, of each row's own stickersTotal/
  // stickerActiveDays — NOT stickersAssigned / Σ stickerActiveDays (a single
  // pooled average would let one very active professional dominate the
  // tile), and a professional with ZERO sticker-active days is EXCLUDED from
  // the mean entirely (never folded in as a 0 that drags the average down —
  // that would understate the pace of everyone who IS actually placing
  // stickers just because a survey-only colleague exists).
  const result = {
    rows: [
      { stickersTotal: 10, stickerActiveDays: 2, barriosActivos: [] }, // 5/day
      { stickersTotal: 3, stickerActiveDays: 3, barriosActivos: [] }, // 1/day
      { stickersTotal: 0, stickerActiveDays: 0, barriosActivos: [] }, // no sticker-active day -> excluded, not a 0
    ],
    totals: { professionals: 3, stickers: 13, surveys: 0 },
  };
  const t = kpiTotals(result, { stickersLoaded: true });
  // (5 + 1) / 2 = 3 -- the zero-pace professional never enters the mean.
  assert.equal(t.avgStickersPerDayPerProfessional, 3);
}
console.log('kpiTotals: avgStickersPerDayPerProfessional is the mean over professionals WITH sticker activity only (M6) OK');

{
  // M6: every professional has zero sticker-active days (survey-only, or
  // truly zero stickers) -> DASH, never a fake 0 (there IS no sticker pace to
  // report, distinct from "confirmed zero pace").
  const result = {
    rows: [
      { stickersTotal: 0, stickerActiveDays: 0, barriosActivos: [] },
      { stickersTotal: 0, stickerActiveDays: 0, barriosActivos: [] },
    ],
    totals: { professionals: 2, stickers: 0, surveys: 5 },
  };
  const t = kpiTotals(result, { stickersLoaded: true });
  assert.equal(t.avgStickersPerDayPerProfessional, DASH, 'M6: no professional with any sticker-active day -> DASH, never 0');
}
console.log('kpiTotals: avgStickersPerDayPerProfessional is DASH when NO professional has sticker activity (M6) OK');

{
  // barriosActivos (7 d): count of DISTINCT barrios across ALL professionals'
  // own barriosActivos arrays — accent/case variants across DIFFERENT
  // professionals must still dedupe to one (same normalizeName rule
  // buildBarriosActivosByKey uses internally for a single professional).
  const result = {
    rows: [
      { stickersTotal: 1, activeDays: 1, barriosActivos: ['San Antonio', 'El Ingenio'] },
      { stickersTotal: 1, activeDays: 1, barriosActivos: ['SAN ANTONIO', 'Otro Barrio'] },
    ],
    totals: { professionals: 2, stickers: 2, surveys: 0 },
  };
  const t = kpiTotals(result, { stickersLoaded: true });
  // Distinct: San Antonio (deduped), El Ingenio, Otro Barrio = 3.
  assert.equal(t.barriosActivos, 3);
}
console.log('kpiTotals: barriosActivos dedupes accent/case variants ACROSS professionals OK');

{
  // Missing/malformed rowsResult tolerated -- never throws.
  const t = kpiTotals(undefined, {});
  assert.deepEqual(t, {
    professionals: 0, stickers: 0, surveys: 0, avgStickersPerDayPerProfessional: 0, barriosActivos: 0,
  });
}
console.log('kpiTotals: missing/malformed rowsResult tolerated OK');

// ── W7: matchesSearch ────────────────────────────────────────────────────
// Cédula path when the query has >=3 digits (after stripping non-digits);
// name path otherwise. A short numeric query ('123') must NOT match a name
// like "Gil" via the name path (it never reaches it -- goes cedula path,
// which correctly fails against a blank/non-matching cedula).

assert.equal(matchesSearch({ name: 'Gil Soto', cedula: '' }, ''), true, 'empty query matches everything');
assert.equal(matchesSearch({ name: 'Gil Soto', cedula: '' }, '   '), true, 'whitespace-only query matches everything');
assert.equal(matchesSearch({ name: 'Gil Soto', cedula: '1234567' }, 'gil'), true, 'name substring, case-insensitive');
assert.equal(matchesSearch({ name: 'María José López', cedula: '' }, 'jose lopez'), true, 'accent-insensitive name substring');
assert.equal(matchesSearch({ name: 'Gil Soto', cedula: '' }, 'xyz'), false, 'no match on name');
assert.equal(matchesSearch({ name: 'Gil', cedula: '' }, '123'), false, '"123" (>=3 digits) must NOT fall back to matching the name "Gil"');
assert.equal(matchesSearch({ name: 'Ana', cedula: '1234567' }, '234'), true, '3-digit query matches a cedula substring');
assert.equal(matchesSearch({ name: 'Ana', cedula: '1.234.567' }, '234'), true, 'cedula substring match ignores dots in the STORED cedula');
assert.equal(matchesSearch({ name: 'Ana', cedula: '1234567' }, '99'), false, '2-digit query (below the >=3 threshold) falls back to the NAME path, "99" not in "Ana"');
assert.equal(matchesSearch({ name: '99', cedula: '1234567' }, '99'), true, '2-digit query matches via the NAME path when the name itself is "99"');
assert.equal(matchesSearch({ name: 'Ana', cedula: '' }, '234'), false, '3-digit query with a blank cedula never falls back to the name path');
console.log('matchesSearch: cedula path (>=3 digits) vs name path OK');

// ── W7: visibleRowsFor ──────────────────────────────────────────────────

{
  const rows = [
    { key: 'ced:1', name: 'Ana Ruiz', cedula: '111' },
    { key: 'ced:2', name: 'Beto Ríos', cedula: '222' },
    { key: nomKey('Gil Soto'), name: 'Gil Soto', cedula: '' },
  ];
  assert.deepEqual(visibleRowsFor(rows, {}).map((r) => r.key), rows.map((r) => r.key), 'no filters -> everything visible');
  assert.deepEqual(visibleRowsFor(rows, { query: 'ana' }).map((r) => r.key), ['ced:1']);
  assert.deepEqual(visibleRowsFor(rows, { professionalKey: 'ced:2' }).map((r) => r.key), ['ced:2']);
  assert.deepEqual(
    visibleRowsFor(rows, { query: 'gil', professionalKey: 'ced:2' }).map((r) => r.key),
    [],
    'both filters apply together (AND), not either/or',
  );
  assert.deepEqual(visibleRowsFor([], { query: 'ana' }), [], 'empty rows -> empty, never throws');
  assert.deepEqual(visibleRowsFor(undefined, {}), [], 'missing rows tolerated');
}
console.log('visibleRowsFor: search + professionalKey filters (AND) OK');

// ── W7: degradedStickerNote ──────────────────────────────────────────────
// 4 states (isDegraded x stickersLoaded): only BOTH true produces a note --
// matches the real production flow (isDegraded is only ever set once the
// sticker fetch has resolved, i.e. alongside stickersLoaded=true).

assert.equal(degradedStickerNote(false, false), null);
assert.equal(degradedStickerNote(false, true), null);
assert.equal(degradedStickerNote(true, false), null, 'degraded-but-not-yet-loaded is defensively null, not a premature note');
assert.equal(degradedStickerNote(true, true), DEGRADED_STICKERS_NOTE);
assert.ok(!/sin profesional identificado/i.test(DEGRADED_STICKERS_NOTE), 'must not promise the removed "sin profesional identificado" KPI bucket');
console.log('degradedStickerNote: 4-state truth table OK');

// ── W7: unassignedNote ───────────────────────────────────────────────────
// Reconciles the (now-hidden) "sin profesional identificado" count with the
// visible stickers KPI/table, as a disclosure note instead of its own tile.

assert.equal(unassignedNote({ stickers: 0, surveys: 5 }), null, 'zero unassigned stickers -> no note (surveys not this note\'s concern)');
assert.equal(unassignedNote({ stickers: 0 }), null);
assert.equal(unassignedNote(undefined), null, 'missing input tolerated');
{
  const note = unassignedNote({ stickers: 7, surveys: 2 });
  assert.ok(/7/.test(note), 'must mention the unassigned STICKER count');
  assert.ok(/sin profesional atribuible/i.test(note));
}
console.log('unassignedNote: null when zero, mentions the sticker count otherwise OK');

// ── W7: hasActiveSegFilters gains `professional` ─────────────────────────
// seg-chart-professional now ALSO narrows the table (W7) — so it must count
// as an active filter for "Reiniciar filtros"' enabled/disabled state, unlike
// before (W5/W6, when it only picked the chart's highlighted line).

assert.equal(hasActiveSegFilters({ professional: 'ced:1' }), true, 'a professional selection alone is active');
assert.equal(hasActiveSegFilters({ professional: '' }), false, 'an empty-string professional is not active');
assert.equal(hasActiveSegFilters({ professional: null }), false);
assert.equal(
  hasActiveSegFilters({ search: '', from: null, to: null, professional: 'ced:1' }),
  true,
  'professional active even with every other filter empty',
);
console.log('hasActiveSegFilters: professional selection counts as an active filter (W7) OK');

// ── M5: reportSelectedButtonState ─────────────────────────────────────────
// The pure decision behind "Reporte PDF individual"'s disabled/title state.
// The regression: renderChartOptions can reset seg-chart-professional back
// to "" when a Desde/Hasta change narrows the previous selection out of
// `rows` — the button must read as disabled (no selection) in that state,
// which requires render() to re-derive it every time, not just on the
// select's own 'change' handler.
{
  const noSelection = reportSelectedButtonState({
    isDegraded: false, stickersLoaded: true, busy: false, hasSelection: false,
  });
  assert.equal(noSelection.disabled, true, 'no selection -> disabled');
  assert.match(noSelection.title, /seleccion/i, 'no selection -> title explains a selection is required');
}
{
  const selected = reportSelectedButtonState({
    isDegraded: false, stickersLoaded: true, busy: false, hasSelection: true,
  });
  assert.equal(selected.disabled, false, 'selection present + nothing else blocking -> enabled');
  assert.match(selected.title, /descargar informe/i);
}
assert.equal(reportSelectedButtonState({ hasSelection: true, isDegraded: true }).disabled, true, 'degraded blocks even with a selection');
assert.equal(reportSelectedButtonState({ hasSelection: true, stickersLoaded: false }).disabled, true, 'stickers not loaded yet blocks even with a selection');
assert.equal(reportSelectedButtonState({ hasSelection: true, busy: true }).disabled, true, 'a mass export in flight blocks even with a selection');
assert.equal(reportSelectedButtonState().disabled, true, 'no args at all -> disabled (defaults to no selection)');
console.log('reportSelectedButtonState: disabled/title truth table, "no selection" is the M5 regression case OK');

// ── W7: kpisHtml (5 tiles, HTML string) ──────────────────────────────────

{
  const rowsResult = {
    rows: [{
      stickersTotal: 4, activeDays: 2, stickerActiveDays: 2, barriosActivos: ['San Antonio'],
    }],
    totals: { professionals: 1, stickers: 4, surveys: 2 },
  };
  const html = kpisHtml(rowsResult, true);
  assert.ok(!/sin profesional identificado/i.test(html), 'the removed "sin profesional identificado" tile must not render');
  assert.ok(!/stickers sin fecha/i.test(html), 'the removed "stickers sin fecha" tile must not render');
  assert.ok(/barrios activos/i.test(html), 'the new "barrios activos (7 d)" tile must render');
  // M6: the tile's product semantics changed (mean over sticker-active
  // professionals only, denominator = stickerActiveDays) — renamed to match,
  // with a `title` explaining the denominator so it doesn't read as a pooled
  // stickers+surveys pace.
  assert.ok(/stickers\/d.a por profesional/i.test(html), 'M6: tile is renamed "stickers/día por profesional"');
  assert.ok(!/promedio diario por profesional/i.test(html), 'M6: the old, more ambiguous label must be gone');
  assert.match(html, /title="[^"]*stickers por d.a con actividad de stickers[^"]*"/i, 'M6: a title tooltip explains the denominator (sticker-active days), averaged across professionals');
  const tileCount = (html.match(/kpi-tile/g) || []).length;
  assert.equal(tileCount, 5, 'exactly 5 KPI tiles');
}
console.log('kpisHtml: 5 tiles, old ones dropped, new one present, M6 rename + caption OK');

{
  // stickersLoaded=false masks the sticker-derived tiles with DASH in the
  // rendered HTML too (not just the pure kpiTotals value).
  const rowsResult = { rows: [], totals: { professionals: 0, stickers: 0, surveys: 0 } };
  const html = kpisHtml(rowsResult, false);
  assert.ok(html.includes(DASH), 'DASH must appear in the rendered HTML while stickers have not loaded');
}
console.log('kpisHtml: stickersLoaded=false renders DASH OK');

// ── W8: timelineChartConfig ───────────────────────────────────────────────
// Dual linear axis (replaces the old shared logarithmic axis): daily counts
// on the right ('y1'), cumulative running totals on the left ('y').

{
  const empty = { labels: [], stickers: [], surveys: [], stickersCumulative: [], surveysCumulative: [], offsets: { stickers: 0, surveys: 0 } };
  const config = timelineChartConfig(empty);
  assert.deepEqual(config.data.labels, []);
  for (const ds of config.data.datasets) assert.deepEqual(ds.data, []);
}
console.log('timelineChartConfig: empty timeline -> empty dataset arrays, never throws OK');

{
  // Single label: the cumulative (dashed, normally pointRadius 0) datasets
  // get pointRadius 3 instead -- a 1-point dashed line with radius 0 would
  // otherwise render NOTHING at all.
  const single = {
    labels: ['2026-01-01'], stickers: [2], surveys: [1], stickersCumulative: [5], surveysCumulative: [3],
    offsets: { stickers: 3, surveys: 2 },
  };
  const config = timelineChartConfig(single);
  const cumulativeDatasets = config.data.datasets.filter((d) => /acum/i.test(d.label));
  assert.equal(cumulativeDatasets.length, 2);
  for (const ds of cumulativeDatasets) assert.equal(ds.pointRadius, 3);
  const dailyDatasets = config.data.datasets.filter((d) => /diario/i.test(d.label));
  assert.equal(dailyDatasets.length, 2);
  for (const ds of dailyDatasets) { assert.equal(ds.pointRadius, 3); assert.equal(ds.borderWidth, 3); }
}
console.log('timelineChartConfig: single label -> cumulative pointRadius 3 OK');

{
  // A day with 0 daily records still plots 0 (not null/undefined) -- Chart.js
  // would otherwise render a gap instead of a real zero point.
  const timeline = {
    labels: ['2026-01-01', '2026-01-02'], stickers: [1, 0], surveys: [0, 0],
    stickersCumulative: [1, 1], surveysCumulative: [0, 0], offsets: { stickers: 0, surveys: 0 },
  };
  const config = timelineChartConfig(timeline);
  const stickerDaily = config.data.datasets.find((d) => d.label === 'Sticker diario');
  assert.equal(stickerDaily.data[1], 0);
  assert.notEqual(stickerDaily.data[1], null);
}
console.log('timelineChartConfig: a zero-count day plots 0, not null OK');

{
  // yAxisID assignment: daily on y1 (right), cumulative on y (left) --
  // scales.y is titled "Acumulado", scales.y1 "Diario", y1 never draws its
  // own gridlines over y's (drawOnChartArea:false).
  const timeline = { labels: ['2026-01-01'], stickers: [1], surveys: [1], stickersCumulative: [1], surveysCumulative: [1], offsets: { stickers: 0, surveys: 0 } };
  const config = timelineChartConfig(timeline);
  const byLabel = Object.fromEntries(config.data.datasets.map((d) => [d.label, d]));
  assert.equal(byLabel['Sticker diario'].yAxisID, 'y1');
  assert.equal(byLabel['Survey diario'].yAxisID, 'y1');
  assert.equal(byLabel['Sticker acum.'].yAxisID, 'y');
  assert.equal(byLabel['Survey acum.'].yAxisID, 'y');
  assert.equal(config.options.scales.y.title.text, 'Acumulado');
  assert.equal(config.options.scales.y1.title.text, 'Diario');
  assert.equal(config.options.scales.y1.grid.drawOnChartArea, false);
  // Node-safe theming (baseOptions/themeColor return fallbacks without `document`).
  assert.ok(config.options.scales.y1.ticks.color, 'y1 must be themed (W8 baseOptions fix)');
  assert.equal(config.options.scales.y.type, 'linear');
  assert.equal(config.options.scales.y1.type, 'linear');
}
console.log('timelineChartConfig: daily on y1 (right), cumulative on y (left), both linear + themed OK');

{
  // Last-point label (_totalLabel) equals offset + Σ daily -- exactly
  // timeline.*Cumulative's own last entry (buildTimeline already folds the
  // offset into the running total), never recomputed a second, divergent way.
  const timeline = {
    labels: ['2026-01-01', '2026-01-02'], stickers: [2, 3], surveys: [1, 1],
    stickersCumulative: [7, 10], surveysCumulative: [4, 5], offsets: { stickers: 5, surveys: 3 },
  };
  const config = timelineChartConfig(timeline);
  const byLabel = Object.fromEntries(config.data.datasets.map((d) => [d.label, d]));
  assert.equal(byLabel['Sticker acum.']._totalLabel, '10');
  assert.equal(byLabel['Survey acum.']._totalLabel, '5');
}
console.log('timelineChartConfig: last-point label equals offset + Σ daily OK');

// ── W8: timelineDataKey ───────────────────────────────────────────────────
// Drives renderChart()'s upsertChart-skip: identical inputs -> identical key;
// any of labels/from/to/professionalKey/offsets/series-sums changing ->
// a different key.

{
  const t1 = { labels: ['2026-01-01'], stickers: [1], surveys: [0], stickersCumulative: [1], surveysCumulative: [0], offsets: { stickers: 0, surveys: 0 } };
  const t2 = { labels: ['2026-01-01'], stickers: [1], surveys: [0], stickersCumulative: [1], surveysCumulative: [0], offsets: { stickers: 0, surveys: 0 } };
  assert.equal(
    timelineDataKey(t1, { from: '2026-01-01', to: '2026-01-31', professionalKey: null }),
    timelineDataKey(t2, { from: '2026-01-01', to: '2026-01-31', professionalKey: null }),
    'identical inputs -> identical key',
  );
  assert.notEqual(
    timelineDataKey(t1, { from: '2026-01-01', to: '2026-01-31', professionalKey: null }),
    timelineDataKey(t1, { from: '2026-02-01', to: '2026-01-31', professionalKey: null }),
    '`from` changing must change the key',
  );
  assert.notEqual(
    timelineDataKey(t1, { professionalKey: null }),
    timelineDataKey(t1, { professionalKey: 'ced:1' }),
    '`professionalKey` changing must change the key',
  );
  const t3 = { ...t1, offsets: { stickers: 5, surveys: 0 } };
  assert.notEqual(
    timelineDataKey(t1, {}),
    timelineDataKey(t3, {}),
    'offsets changing must change the key even when labels/series are identical',
  );
  const t4 = { ...t1, labels: ['2026-01-01', '2026-01-02'], stickers: [1, 0] };
  assert.notEqual(timelineDataKey(t1, {}), timelineDataKey(t4, {}), 'labels changing must change the key');
}
console.log('timelineDataKey: identical inputs -> identical key, any relevant field changing -> different key OK');

// ── H2: timelineDataKey must not collapse a re-ordered day-by-day series ───
// The old key only summed each series (Σstickers, Σsurveys) — two timelines
// with the SAME labels and the SAME totals but a DIFFERENT per-day order
// ([1,2] vs [2,1], e.g. after switching the professional-select filter to
// someone whose daily counts happen to sum the same) hashed to the identical
// key, so renderChart() silently skipped the repaint even though the actual
// data being plotted had changed.
{
  const perm1 = {
    labels: ['2026-01-01', '2026-01-02'], stickers: [1, 2], surveys: [0, 0],
    stickersCumulative: [1, 3], surveysCumulative: [0, 0], offsets: { stickers: 0, surveys: 0 },
  };
  const perm2 = {
    labels: ['2026-01-01', '2026-01-02'], stickers: [2, 1], surveys: [0, 0],
    stickersCumulative: [2, 3], surveysCumulative: [0, 0], offsets: { stickers: 0, surveys: 0 },
  };
  assert.notEqual(
    timelineDataKey(perm1, { from: null, to: null, professionalKey: null }),
    timelineDataKey(perm2, { from: null, to: null, professionalKey: null }),
    'H2: same labels + same per-series total but a different day-by-day order must still produce a different key',
  );
  // Same reasoning on the surveys series.
  const survPerm1 = { ...perm1, stickers: [0, 0], surveys: [1, 2] };
  const survPerm2 = { ...perm1, stickers: [0, 0], surveys: [2, 1] };
  assert.notEqual(
    timelineDataKey(survPerm1, {}),
    timelineDataKey(survPerm2, {}),
    'H2: same reasoning applies to the surveys series',
  );
}
console.log('timelineDataKey: a re-ordered per-day series (same labels/totals) still changes the key (H2) OK');

// ── B1: shouldSkipChartRender — the actual gate renderChart() uses to decide
// whether upsertChart() is worth calling again. Gating on the dataKey ALONE
// broke after a theme change: main.js's 'themechange' listener destroys every
// registered chart (charts.js's resetCharts()) BEFORE this module's own
// deferred re-render fires, but the dataKey computed from the SAME
// stickers/surveys/from/to/professionalKey is unchanged -> the old
// `dataKey === lastChartDataKey` check alone returned true and skipped the
// rebuild, leaving the canvas permanently blank. The fix threads whether the
// chart is STILL actually registered (charts.js's hasChart()) into the
// decision. ───────────────────────────────────────────────────────────────
assert.equal(
  shouldSkipChartRender({ dataKey: 'k1', lastKey: 'k1', hasChart: true }),
  true,
  'same key, chart still registered -> safe to skip',
);
assert.equal(
  shouldSkipChartRender({ dataKey: 'k1', lastKey: 'k1', hasChart: false }),
  false,
  'B1: same key but the chart was destroyed (e.g. a theme change) -> must NOT skip, must rebuild',
);
assert.equal(
  shouldSkipChartRender({ dataKey: 'k1', lastKey: 'k2', hasChart: true }),
  false,
  'different key -> never skip regardless of registration',
);
assert.equal(
  shouldSkipChartRender({ dataKey: 'k1', lastKey: null, hasChart: true }),
  false,
  'no previous key (first render / just torn down) -> never skip',
);
assert.equal(shouldSkipChartRender(), false, 'no args at all -> never skip');
console.log('shouldSkipChartRender: gates the upsertChart skip on BOTH key match AND the chart still being registered (B1) OK');

// ── W9: formatMinutes ───────────────────────────────────────────────────────
assert.equal(formatMinutes(5), '00:05');
assert.equal(formatMinutes(725), '12:05');
assert.equal(formatMinutes(0), '00:00');
assert.equal(formatMinutes(1439), '23:59');
assert.equal(formatMinutes(null), DASH);
assert.equal(formatMinutes(undefined), DASH);
assert.equal(formatMinutes(NaN), DASH);
assert.equal(formatMinutes(-5), DASH, 'out-of-range minute must never throw/produce a bogus time');
assert.equal(formatMinutes(1440), DASH, 'out-of-range minute (>= 24h) must never throw/produce a bogus time');
console.log('formatMinutes: HH:MM formatting + DASH for null/invalid OK');

// ── W9: columnsFor / defaultSortFor / COLUMNS_TOTALES / COLUMNS_TEMPORALES ──
assert.ok(Array.isArray(COLUMNS_TOTALES) && COLUMNS_TOTALES.length > 0);
assert.ok(Array.isArray(COLUMNS_TEMPORALES) && COLUMNS_TEMPORALES.length > 0);
assert.deepEqual(columnsFor('totales'), COLUMNS_TOTALES);
assert.deepEqual(columnsFor('temporales'), COLUMNS_TEMPORALES);
assert.deepEqual(columnsFor(undefined), COLUMNS_TOTALES, 'default subTab is totales');
assert.deepEqual(columnsFor('unknown-sub-tab'), COLUMNS_TOTALES, 'an unknown subTab falls back to totales, never throws');
assert.deepEqual(defaultSortFor('totales'), { column: 'total', dir: 'desc' });
assert.deepEqual(defaultSortFor('temporales'), { column: 'firstDate', dir: 'desc' });
assert.deepEqual(defaultSortFor('unknown'), { column: 'total', dir: 'desc' }, 'unknown subTab defaults like totales');
assert.ok(COLUMNS_TOTALES.some((c) => c.key === 'np' && c.label === 'Clase (P)'));
assert.ok(COLUMNS_TOTALES.some((c) => c.key === 'codigo' && c.label === 'Código vigente'));
assert.ok(COLUMNS_TEMPORALES.some((c) => c.key === 'codigo' && c.label === 'Código'));
console.log('columnsFor/defaultSortFor OK');

// ── W9: buildProfessionalRows now also carries avgStickersPerDay + the batch
// temporal-metrics fields (reusing buildTemporalMetricsByKey — B1-style single
// pass instead of a per-subtab recompute) ───────────────────────────────────
{
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-01T09:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-02T10:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [], today: '2026-01-09' });
  const row = result.rows[0];
  assert.equal(row.avgStickersPerDay, 1, '2 stickers / 2 active days');
  assert.equal(row.daysSinceFirst, 8, 'today (01-09) minus firstDate (01-01)');
  assert.equal(typeof row.prevDayFirstMinutes, 'number');
}
console.log('buildProfessionalRows: avgStickersPerDay + temporal fields merged in OK');

{
  // activeDays 0 (no dated records at all) -> avgStickersPerDay is null (never
  // NaN/Infinity, never a silent 0 masquerading as "confirmed zero pace").
  const stickers = [
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: null, fase: 1, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(result.rows[0].avgStickersPerDay, null);
}
console.log('buildProfessionalRows: avgStickersPerDay null when activeDays is 0 OK');

// ── M6: avgStickersPerDay is driven by STICKER-only active days, never the
// pooled `activeDays` (which also counts survey-only days) — "stickers/día
// por profesional" must reflect only days the professional actually placed a
// sticker on. ────────────────────────────────────────────────────────────
{
  // 10 stickers on ONE day, plus Survey activity on 4 OTHER (sticker-less)
  // days: activeDays pools all 5 days, but the sticker pace must stay 10 (not
  // 10/5 = 2, diluted by days that have no sticker at all).
  const stickerFixture = { inspector: { nombre_completo: 'Mono Pace' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' };
  const tenStickers = Array.from({ length: 10 }, () => ({ ...stickerFixture }));
  const surveys = ['2026-01-02', '2026-01-03', '2026-01-04', '2026-01-05'].map((d) => ({
    nombre_evaluador: 'Mono Pace', fecha_inspeccion: d,
  }));
  const result = buildProfessionalRows({ stickers: tenStickers, surveys });
  const row = result.rows[0];
  assert.equal(row.activeDays, 5, 'sticker day + 4 survey-only days = 5 active days total (the OLD, wrong denominator)');
  assert.equal(row.stickerActiveDays, 1, 'only 1 distinct day actually has a counted sticker');
  assert.equal(row.avgStickersPerDay, 10, '10 stickers / 1 sticker-active day, never diluted by survey-only days');
}
{
  // A survey-only professional (zero stickers ever) -> stickerActiveDays 0,
  // avgStickersPerDay null (not 0/NaN) even though activeDays is nonzero.
  const surveys = [{ nombre_evaluador: 'Solo Survey', fecha_inspeccion: '2026-01-01' }];
  const result = buildProfessionalRows({ stickers: [], surveys });
  const row = result.rows[0];
  assert.equal(row.activeDays, 1);
  assert.equal(row.stickerActiveDays, 0);
  assert.equal(row.avgStickersPerDay, null);
}
console.log('buildProfessionalRows: avgStickersPerDay uses sticker-only active days, not the pooled activeDays (M6) OK');

// ── W9: xlsxRowsFor ──────────────────────────────────────────────────────────
{
  const rowFull = {
    key: 'ced:1', name: 'Gil Soto', cedula: '123', codigo: 'B1', entidad: 'DAGRD',
    np: 'P2', tarjetaProfesional: 'TP-1', celular: '300', correo: 'g@x.com',
    stickersFase1: 2, stickersFase2: 1, stickersTotal: 3, surveyTotal: 1, total: 4,
    firstDate: '2026-01-01', lastDate: '2026-01-05', activeDays: 3, avgPerActiveDay: 1.33,
    avgStickersPerDay: 1, rosterSourced: 0, barriosActivos: ['San Antonio', 'El Poblado'],
    daysSinceFirst: 8, prevDay: '2026-01-08', prevDayFirstMinutes: 480, prevDayLastMinutes: 600,
    avgFirstMinutes: 500, avgLastMinutes: 620,
  };
  // Row missing contact/temporal data entirely (only the bare minimum a
  // buildProfessionalRows result can produce for an untimed professional) --
  // every key must still be present, defaulted via `?? ''`, never a missing
  // key/undefined value.
  const rowSparse = {
    key: 'nom:sin datos', name: 'Sin Datos', cedula: '', codigo: '', entidad: '',
    np: '', tarjetaProfesional: '', celular: '', correo: '',
    stickersFase1: 0, stickersFase2: 0, stickersTotal: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0,
    avgStickersPerDay: null, rosterSourced: 0, barriosActivos: [],
    daysSinceFirst: null, prevDay: null, prevDayFirstMinutes: null, prevDayLastMinutes: null,
    avgFirstMinutes: null, avgLastMinutes: null,
  };

  const totalesRows = xlsxRowsFor([rowFull, rowSparse], { subTab: 'totales' });
  assert.equal(totalesRows.length, 2);
  const keysFull = Object.keys(totalesRows[0]).sort();
  const keysSparse = Object.keys(totalesRows[1]).sort();
  assert.deepEqual(keysFull, keysSparse, 'every row must carry the identical key set');
  assert.equal(totalesRows[0].profesional, 'Gil Soto');
  assert.equal(totalesRows[0].clase_p, 'P2');
  assert.equal(totalesRows[0].codigo_vigente, 'B1');
  assert.equal(totalesRows[0].tarjeta_profesional, 'TP-1');
  assert.equal(totalesRows[0].barrios_activos_7d, 'San Antonio; El Poblado');
  assert.equal(totalesRows[0].stickers_promedio_diario, 1);
  assert.equal(totalesRows[1].tarjeta_profesional, '', 'missing contact data defaults to empty string, never undefined');
  assert.equal(totalesRows[1].barrios_activos_7d, '');
  assert.equal(totalesRows[1].stickers_promedio_diario, '', 'null avgStickersPerDay -> empty string, never the literal null');

  const temporalesRows = xlsxRowsFor([rowFull, rowSparse], { subTab: 'temporales' });
  assert.equal(temporalesRows.length, 2);
  const tKeysFull = Object.keys(temporalesRows[0]).sort();
  const tKeysSparse = Object.keys(temporalesRows[1]).sort();
  assert.deepEqual(tKeysFull, tKeysSparse, 'every temporales row must carry the identical key set too');
  assert.equal(temporalesRows[0].hora_1er_registro_dia_ant, '08:00');
  assert.equal(temporalesRows[0].hora_prom_ult_registro, '10:20');
  assert.equal(temporalesRows[1].hora_1er_registro_dia_ant, DASH, 'a professional with no timed record shows DASH, not a crash/blank');
  assert.equal(temporalesRows[1].fecha_primer_registro, '');
  assert.equal(temporalesRows[1].dias_desde_primera_actividad, '');
}
console.log('xlsxRowsFor: totales/temporales sheets carry the identical key set, missing data defaults via ?? "" OK');

{
  // Empty/undefined input -> empty array, never throws.
  assert.deepEqual(xlsxRowsFor([], { subTab: 'totales' }), []);
  assert.deepEqual(xlsxRowsFor(undefined, { subTab: 'totales' }), []);
  assert.deepEqual(xlsxRowsFor([], {}), [], 'default subTab (totales) with no rows');
}
console.log('xlsxRowsFor: empty/undefined input OK');

// ── N13: xlsxFiltersSummary — the XLSX header's "Filtros:" row, so an export
// carries which search/range/professional narrowed it, not just a bare
// "Registros: N" that could otherwise be misread as the WHOLE dataset. ────
assert.equal(
  xlsxFiltersSummary({
    search: '', from: null, to: null, professionalName: '',
  }),
  'ninguno',
  'no active filter at all -> "ninguno", never a blank/misleading cell',
);
assert.equal(xlsxFiltersSummary(), 'ninguno', 'no args at all -> "ninguno"');
assert.equal(
  xlsxFiltersSummary({ search: 'ana' }),
  'Búsqueda: "ana"',
);
assert.equal(
  xlsxFiltersSummary({ search: '  ana  ' }),
  'Búsqueda: "ana"',
  'search is trimmed before it renders',
);
assert.equal(
  xlsxFiltersSummary({ from: '2026-01-01', to: '2026-01-31' }),
  'Desde: 2026-01-01; Hasta: 2026-01-31',
);
assert.equal(
  xlsxFiltersSummary({ professionalName: 'Gil Soto' }),
  'Profesional: Gil Soto',
  'the professional NAME renders, not the raw ced:/nom: key',
);
assert.equal(
  xlsxFiltersSummary({
    search: 'ana', from: '2026-01-01', to: null, professionalName: 'Gil Soto',
  }),
  'Búsqueda: "ana"; Desde: 2026-01-01; Profesional: Gil Soto',
  'every active filter joins in a stable order, absent ones simply omitted',
);
console.log('xlsxFiltersSummary: "ninguno" when nothing active, otherwise a stable summary of the active filters (N13) OK');

// ── sortRows: temporal (nullable numeric minute) column, per W9 ────────────
{
  const rows = [
    { key: 'a', prevDayFirstMinutes: null },
    { key: 'b', prevDayFirstMinutes: 300 },
    { key: 'c', prevDayFirstMinutes: 60 },
  ];
  const asc = sortRows(rows, 'prevDayFirstMinutes', 'asc');
  assert.deepEqual(asc.map((r) => r.key), ['a', 'c', 'b'], 'null (no previous timed day) sorts lowest, ascending');
  const desc = sortRows(rows, 'prevDayFirstMinutes', 'desc');
  assert.deepEqual(desc.map((r) => r.key), ['b', 'c', 'a'], 'null still sorts lowest, i.e. LAST descending');
}
console.log('sortRows: nullable temporal-minutes column (prevDayFirstMinutes) OK');

// ── W10: objetivoDiario ──────────────────────────────────────────────────────
assert.equal(objetivoDiario({ pendientes: null, profesionalesActivos: 10, today: '2026-09-13' }), null, 'non-finite pendientes -> null, never substituted with 0');
assert.equal(objetivoDiario({ pendientes: NaN, profesionalesActivos: 10, today: '2026-09-13' }), null);
assert.equal(objetivoDiario({ pendientes: undefined, profesionalesActivos: 10, today: '2026-09-13' }), null, 'missing pendientes (agg without kpis) -> null, never 0');
assert.equal(objetivoDiario({ pendientes: 100, profesionalesActivos: 0, today: '2026-09-13' }), null, 'zero active professionals -> null');
assert.equal(objetivoDiario({ pendientes: 100, profesionalesActivos: -1, today: '2026-09-13' }), null, 'negative active professionals -> null');
assert.equal(objetivoDiario({ pendientes: 100, profesionalesActivos: 10, today: '2026-10-01' }), null, 'today past the deadline -> null');
assert.equal(objetivoDiario({ pendientes: 100, profesionalesActivos: 10, today: null }), null, 'missing today -> null');
{
  // today === deadline -> days = max(1, 0) = 1, never a division by zero.
  const v = objetivoDiario({
    pendientes: 50, profesionalesActivos: 5, today: '2026-09-30', deadline: '2026-09-30',
  });
  assert.equal(v, 10, '50/5/1 día = 10.0');
}
{
  // Normal rounding to 1 decimal.
  const v = objetivoDiario({
    pendientes: 333, profesionalesActivos: 10, today: '2026-09-20', deadline: '2026-09-30',
  });
  assert.equal(v, 3.3, '333 / 10 profesionales / 10 días = 3.33 -> 3.3');
}
console.log('objetivoDiario: null-cases + normal rounding OK');

// ── W10: visitasUltimos7Dias — rolling today-6..today (inclusive), stickers +
// surveys, SIN_DATO excluded first (D3) ──────────────────────────────────────
{
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00' }, // today
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-03T12:00:00+00:00' }, // today-6, IN
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-02T12:00:00+00:00' }, // today-7, OUT
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: null, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00' }, // SIN_DATO, excluded
  ];
  const surveys = [
    { nombre_evaluador: 'X', fecha_inspeccion: '2026-01-08' }, // IN
  ];
  const identity = buildIdentityIndex({ stickers, surveys });
  const row = { key: 'ced:1' };
  const n = visitasUltimos7Dias(row, {
    stickers, surveys, identity, today: '2026-01-09',
  });
  assert.equal(n, 3, '2 in-window stickers (SIN_DATO excluded) + 1 survey');
}
console.log('visitasUltimos7Dias: window inclusive today-6/exclusive today-7, SIN_DATO excluded first OK');

{
  // Zero records in the window -> 0, never throws.
  const row = { key: 'ced:99' };
  const n = visitasUltimos7Dias(row, { stickers: [], surveys: [], today: '2026-01-09' });
  assert.equal(n, 0);
}
console.log('visitasUltimos7Dias: zero records -> 0 OK');

// ── W10: reportFilenameSlug ──────────────────────────────────────────────────
assert.equal(reportFilenameSlug('Gil Soto'), 'Gil_Soto');
assert.equal(reportFilenameSlug(''), 'profesional', 'blank name falls back to a stable slug, never an empty filename');
assert.equal(reportFilenameSlug('   '), 'profesional', 'whitespace-only name falls back too');
assert.equal(reportFilenameSlug(null), 'profesional');
assert.equal(reportFilenameSlug(undefined), 'profesional');
assert.equal(reportFilenameSlug('Gil/Soto?'), 'Gil_Soto_', 'unsafe filename characters collapse to _, never dropped silently in a way that could collide');
console.log('reportFilenameSlug OK');

// ── W10: buildProfessionalReportDocDefinition — ctx-driven extensions ───────
{
  const row = {
    name: 'Gil Soto', cedula: '123', codigo: '004', entidad: 'DAGMA', np: 'P2',
    tarjetaProfesional: '', celular: '', correo: '', barriosActivos: [],
    stickersFase1: 2, stickersFase2: 1, surveyTotal: 3, total: 6,
    firstDate: '2026-01-01', lastDate: '2026-01-05', activeDays: 3, avgPerActiveDay: 2,
    avgStickersPerDay: 1, rosterSourced: 0,
  };
  const points = { stickerPoints: [], surveyPoints: [] };
  const ctx = {
    from: '2026-01-01', to: '2026-01-05', generatedAt: '2026-01-06', today: '2026-01-06',
    objetivoDiario: null, degraded: false,
  };
  const doc = buildProfessionalReportDocDefinition(row, points, { ...ctx, last7: 2 });
  const flatText = JSON.stringify(doc.content);
  assert.ok(/sin dato/i.test(flatText), 'objetivoDiario null must render "sin dato", never a lying number');
  assert.ok(flatText.includes('—'), 'blank tarjeta/celular/correo/barrios must render the forced DASH');
  assert.ok(/personal/i.test(flatText), 'a confidentiality line about personal data must be present');

  const docOther = buildProfessionalReportDocDefinition(row, points, { ...ctx, last7: 9 });
  assert.notEqual(JSON.stringify(docOther.content), flatText, 'ctx.last7 must actually reach the rendered report');
  assert.ok(JSON.stringify(docOther.content).includes('"9"'), 'the last7 value must render as its own cell text');
}
console.log('buildProfessionalReportDocDefinition: ctx-driven fields (objetivoDiario sin dato, DASH-forced blanks, confidentiality line, last7) OK');

{
  const row = {
    name: 'X', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0,
    avgStickersPerDay: null, rosterSourced: 0,
  };
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] }, { objetivoDiario: 3.3, last7: 0 });
  const flatText = JSON.stringify(doc.content);
  assert.ok(flatText.includes('3.3'), 'a real objetivoDiario value must render');
  assert.ok(/30 de septiembre de 2026/.test(flatText), 'must show the "Proyectado al 30 de septiembre de 2026" phrasing');
}
console.log('buildProfessionalReportDocDefinition: objetivoDiario real value renders with the "Proyectado…" phrasing OK');

{
  const row = { name: 'X' };
  assert.throws(
    () => buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] }, { degraded: true }),
    /degradad/i,
    'a degraded data source must refuse to build the report, not silently ship redacted/wrong data',
  );
}
console.log('buildProfessionalReportDocDefinition: degraded ctx refuses to build (throws) OK');

{
  const row = {
    name: 'Gil Soto', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 60, stickersFase2: 0, surveyTotal: 0, total: 60,
    firstDate: '2026-01-01', lastDate: '2026-03-01', activeDays: 60, avgPerActiveDay: 1,
    avgStickersPerDay: 1, rosterSourced: 0,
  };
  const stickerPoints = Array.from({ length: 60 }, (_, i) => ({
    codigo: `COD-${i}`, direccion: 'x', municipio: 'Cali', fecha: '2026-01-01', faseLabel: 'Fase I',
  }));
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints, surveyPoints: [] }, {});
  const flatText = JSON.stringify(doc.content);
  assert.ok(flatText.includes('COD-49'), 'the 50th row (index 49) must still be included');
  assert.ok(!flatText.includes('COD-50'), 'row 51 (index 50) must be cut off — cap is 50');
  assert.ok(/y 10 m.s/i.test(flatText), 'overflow note must report the exact excess count (60-50=10)');
}
console.log('buildProfessionalReportDocDefinition: sticker listing capped at 50 rows + overflow note OK');

{
  const row = {
    name: '', cedula: '', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0,
    avgStickersPerDay: null, rosterSourced: 0,
  };
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] }, {});
  const flatText = JSON.stringify(doc.content);
  assert.ok(flatText.includes('Sin dato'), 'a blank name must render as "Sin dato" in the title, never an empty header');
}
console.log('buildProfessionalReportDocDefinition: row.name === "" renders "Sin dato" OK');

{
  // A professional with zero stickers in the requested period still renders
  // "Sin registros" instead of an empty/blank table (existing convention,
  // still exercised here explicitly for the period-filtered W10 flow).
  const row = {
    name: 'Sin Stickers', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 2, total: 2,
    firstDate: '2026-01-01', lastDate: '2026-01-02', activeDays: 2, avgPerActiveDay: 1,
    avgStickersPerDay: null, rosterSourced: 0,
  };
  const doc = buildProfessionalReportDocDefinition(row, {
    stickerPoints: [], surveyPoints: [{ direccion: 'X', nombreEdificacion: 'Y', fecha: '2026-01-01' }],
  }, {});
  assert.ok(/sin registros/i.test(JSON.stringify(doc.content)));
}
console.log('buildProfessionalReportDocDefinition: zero stickers in range -> "Sin registros" OK');

// ── W10: buildMassReportDocDefinition ────────────────────────────────────────
{
  const rowA = {
    name: 'Ana Ruiz', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 1, stickersFase2: 0, surveyTotal: 0, total: 1,
    firstDate: '2026-01-01', lastDate: '2026-01-01', activeDays: 1, avgPerActiveDay: 1,
    avgStickersPerDay: 1, rosterSourced: 0,
  };
  const rowB = {
    name: 'Beto Ríos', cedula: '2', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 1, surveyTotal: 0, total: 1,
    firstDate: '2026-01-02', lastDate: '2026-01-02', activeDays: 1, avgPerActiveDay: 1,
    avgStickersPerDay: 1, rosterSourced: 0,
  };
  const pointsA = { stickerPoints: [{ codigo: 'A1', direccion: '', municipio: '', fecha: '2026-01-01', faseLabel: 'Fase I' }], surveyPoints: [] };
  const pointsB = { stickerPoints: [{ codigo: 'B1', direccion: '', municipio: '', fecha: '2026-01-02', faseLabel: 'Fase II' }], surveyPoints: [] };
  const ctx = {
    from: '2026-01-01', to: '2026-01-02', generatedAt: '2026-01-03', today: '2026-01-03', objetivoDiario: null, degraded: false,
  };

  const mass = buildMassReportDocDefinition([
    { row: rowA, points: pointsA, last7: 1 },
    { row: rowB, points: pointsB, last7: 1 },
  ], ctx);

  const soloA = buildProfessionalReportDocDefinition(rowA, pointsA, { ...ctx, last7: 1 });
  const soloB = buildProfessionalReportDocDefinition(rowB, pointsB, { ...ctx, last7: 1 });

  const breakIndex = mass.content.findIndex((n) => n && n.pageBreak === 'before');
  assert.ok(breakIndex > 0, 'a page break must exist before the second professional');
  const firstBlock = mass.content.slice(0, breakIndex);
  const secondBlock = mass.content.slice(breakIndex).map((n, i) => {
    if (i !== 0) return n;
    const { pageBreak, ...rest } = n;
    return rest;
  });
  assert.deepEqual(firstBlock, soloA.content, 'first professional block must match the solo builder exactly');
  assert.deepEqual(secondBlock, soloB.content, 'second professional block (pageBreak stripped) must match the solo builder exactly');
}
console.log('buildMassReportDocDefinition: individual and mass builders produce identical per-professional content OK');

{
  // 0 professionals -> a single informational page, never an empty content array.
  const doc = buildMassReportDocDefinition([], {});
  assert.ok(doc.content.length > 0, 'must never ship an empty content array');
  assert.ok(JSON.stringify(doc.content).includes('Sin profesionales para los filtros seleccionados'));
}
console.log('buildMassReportDocDefinition: 0 professionals -> single "sin profesionales" page OK');

{
  // Degraded ctx refuses the whole batch too, even with 0 rows.
  assert.throws(() => buildMassReportDocDefinition([], { degraded: true }), /degradad/i);
}
console.log('buildMassReportDocDefinition: degraded ctx refuses (throws) OK');

{
  // W10 perf sanity (Node-side only -- pdfmake's actual PAGE LAYOUT cannot run
  // outside a browser; the real timing/heap gate from the plan's performance
  // budget (< 20 s and < 700 MB for 110 professionals) must be measured
  // manually in DevTools against the real dataset.
  // TODO perf gate: manual measurement required (browser: getBlob() timing +
  // heap snapshot around the mass-export click, per the plan's own
  // "Presupuesto de rendimiento" table, row "Export masivo build/total").
  const rowsWithPoints = Array.from({ length: 110 }, (_, i) => {
    const row = {
      key: `ced:${i}`, name: `Profesional ${i}`, cedula: String(1000 + i), codigo: 'B1', entidad: 'DAGRD',
      np: 'P2', tarjetaProfesional: 'TP', celular: '300', correo: 'x@x.com',
      stickersFase1: 15, stickersFase2: 15, surveyTotal: 0, total: 30,
      firstDate: '2026-08-01', lastDate: '2026-09-01', activeDays: 20, avgPerActiveDay: 1.5,
      avgStickersPerDay: 1.5, rosterSourced: 0, barriosActivos: ['Barrio A'],
    };
    const stickerPoints = Array.from({ length: 30 }, (_, j) => ({
      codigo: `COD-${i}-${j}`, direccion: `Calle ${j}`, municipio: 'Cali', fecha: '2026-08-15', faseLabel: j % 2 ? 'Fase I' : 'Fase II',
    }));
    return { row, points: { stickerPoints, surveyPoints: [] }, last7: 5 };
  });
  const def = buildMassReportDocDefinition(rowsWithPoints, {
    from: '2026-08-01', to: '2026-09-01', generatedAt: '2026-09-13', objetivoDiario: 2.5, degraded: false, today: '2026-09-13',
  });
  const pageBreaks = def.content.filter((n) => n && n.pageBreak === 'before').length;
  assert.equal(pageBreaks, 109, '110 professionals -> 109 page breaks (none before the first)');
  const size = JSON.stringify(def).length;
  assert.ok(size < 3 * 1024 * 1024, `doc-definition JSON should stay under 3MB (was ${size} bytes)`);
  console.log(`buildMassReportDocDefinition: 110-professional doc-definition size = ${(size / 1024).toFixed(1)} KB`);
}
console.log('buildMassReportDocDefinition: 110-professional perf sanity (page breaks + size) — TODO perf gate: manual browser measurement per plan OK');

console.log('seguimiento.test.mjs: all assertions passed');
