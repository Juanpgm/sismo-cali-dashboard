// Self-check for the pure aggregation helpers behind the Seguimiento tab.
// Run: node web/js/seguimiento.test.mjs
import assert from 'node:assert/strict';
import {
  normalizeName, buildProfessionalRows, buildTimeline, sortRows,
  professionalRecords, buildProfessionalReportDocDefinition, hasActiveSegFilters,
} from './seguimiento.js';

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
  const result = buildTimeline({ stickers: [], surveys: [] });
  assert.deepEqual(result, {
    labels: [], stickers: [], surveys: [], stickersCumulative: [], surveysCumulative: [],
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
  const onlyGil = buildTimeline({ stickers, surveys, professionalKey: normalizeName('Gil Soto') });
  assert.deepEqual(onlyGil.stickers, [1]);
  assert.deepEqual(onlyGil.surveys, [1]);
}
console.log('buildTimeline: professionalKey filter OK');

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

// ── buildTimeline: UTC-aware timestamp buckets to the LOCAL calendar day ──
// The atencionsismo API's `fecha` is a UTC-aware ISO timestamp
// ("...T02:00:00+00:00" — backend/app/routers/stickers.py); Cali is UTC-5,
// so an evening record can carry a NEXT-DAY UTC date. Reading the UTC date
// straight off the string (treating its leading YYYY-MM-DD digits as truth)
// would silently shift such a record to the wrong day for every KPI/filter/
// timeline bucket keyed off it. The expected day is computed here from the
// SAME local-timezone conversion the code under test performs (never
// hardcoded), so this assertion holds regardless of which machine runs it.

{
  const raw = '2026-01-02T02:00:00+00:00';
  const d = new Date(raw);
  const expectedLocalDay = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const stickers = [{ inspector: { nombre_completo: 'Gil Soto' }, fecha: raw, fase: 1, fuente: 'atencionsismo' }];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, [expectedLocalDay]);
}
console.log('buildTimeline: UTC-aware fecha buckets to the local calendar day OK');

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
  const row = { key: normalizeName('Gil Soto') };
  const result = professionalRecords(row, { stickers: [], surveys: [] });
  assert.deepEqual(result, { stickerPoints: [], surveyPoints: [] });
}
console.log('professionalRecords: empty inputs OK');

{
  // Only records matching the row's normalized name key are included —
  // same join rule as buildProfessionalRows/buildTimeline, case/accent-
  // insensitive.
  const row = { key: normalizeName('Gil Soto') };
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
  const row = { key: normalizeName('Gil Soto') };
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
  const row = { key: normalizeName('Gil Soto') };
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
  const row = { key: normalizeName('Gil Soto') };
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
  const row = { key: normalizeName('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: '76001-1-0010001', fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
  ];
  const result = professionalRecords(row, { stickers, surveys: [] });
  assert.deepEqual(result.stickerPoints, []);
}
console.log('professionalRecords: SIN_DATO sticker excluded entirely OK');

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
