// Self-check for the pure aggregation helpers behind the Seguimiento tab.
// Run: node web/js/seguimiento.test.mjs
import assert from 'node:assert/strict';
import {
  normalizeName, cedulaKey, professionalKeyOf, buildIdentityIndex,
  buildProfessionalRows, buildTimeline, sortRows,
  professionalRecords, buildTemporalMetrics, buildBarriosActivos,
  buildProfessionalReportDocDefinition, hasActiveSegFilters,
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

// ── buildTemporalMetrics ─────────────────────────────────────────────────────

{
  // Empty (no timed records at all) -> everything null, daysSinceFirst null
  // when the row has no firstDate either.
  const row = { key: nomKey('Gil Soto'), firstDate: null };
  const result = buildTemporalMetrics(row, { stickers: [], surveys: [], today: '2026-01-09' });
  assert.deepEqual(result, {
    firstRecordMinutes: null, lastRecordMinutes: null,
    avgFirstMinutes: null, avgLastMinutes: null,
    prevDay: null, daysSinceFirst: null,
  });
}
console.log('buildTemporalMetrics: empty inputs OK');

{
  // Survey fecha_hora minute-of-day parsing: T00:05 -> 5, T12:05 -> 725.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-01' };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-01T00:05' },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-02T12:05' },
  ];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.firstRecordMinutes, 5);
  assert.equal(result.lastRecordMinutes, 725);
  assert.equal(result.avgFirstMinutes, Math.round((5 + 725) / 2));
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
  assert.equal(result.firstRecordMinutes, null);
  assert.equal(result.avgFirstMinutes, null);
}
console.log('buildTemporalMetrics: missing/malformed fecha_hora -> no hour, never throws OK');

{
  // prevDay: only a record dated TODAY -> prevDay null (never "yesterday"
  // unless there really is a timed record on a day strictly before today).
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-09' };
  const surveys = [{ nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-09T08:00' }];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.prevDay, null);
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
// Only search + Desde/Hasta narrow the table — seg-chart-professional and
// sort order are deliberately excluded (see the function's own doc comment). ─
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

console.log('seguimiento.test.mjs: all assertions passed');
