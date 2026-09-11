// Self-check for the pure logic behind the Reportes ciudadanos tab.
// Run: node web/js/reportes-ciudadanos.test.mjs
import assert from 'node:assert';
import {
  ESTADOS, estadoDe, contarPor, opcionesDe, applyFiltrosReportes, colorSticker, COLOR_MODES_REPORTES, freshnessText,
  hasActiveRepFilters, kpisOficialesFrom, kpisCaption,
} from './reportes-ciudadanos.js';
import { COLORS } from './utils.js';

const r = (over = {}) => ({
  id: 'r1', direccion: 'Calle 1', barrio: 'San Antonio', comuna: 'Comuna 3', estado: 'Reportado',
  afectacion: 'DAÑO ESTRUCTURAL', tipo_inmueble: 'Casa', nombre_edificio: '', lat: 3.4, lng: -76.5,
  creado: '2026-08-18T18:33:00-05:00', creado_texto: '', habitabilidad: '', visitado: false, pudo_evaluar: '',
  alcance: '', descripcion: 'Grieta', sticker: { numero: '', color: '', etiqueta: '', origen: '', clasificacion: '' },
  ...over,
});

// Six API states, worst-known first for the KPI row.
assert.deepStrictEqual(ESTADOS.map((e) => e.key), [
  'Visitado crítico', 'Evaluación especializada', 'Visitado', 'Visita fallida', 'Asignado', 'Reportado']);
assert.strictEqual(estadoDe(r()).key, 'Reportado');
assert.strictEqual(estadoDe(r({ estado: 'visitado crítico' })).key, 'Visitado crítico');
assert.strictEqual(estadoDe(r({ estado: '' })).key, 'SIN_DATO');
assert.strictEqual(estadoDe(r({ estado: 'Otro' })).key, 'SIN_DATO');

// Counting by any key function; unknown buckets are kept.
const counts = contarPor([r(), r({ estado: 'Asignado' }), r({ estado: '' })], (x) => estadoDe(x).key);
assert.deepStrictEqual(counts, { Reportado: 1, Asignado: 1, SIN_DATO: 1 });

// Option lists are sorted, deduped, and skip blanks.
assert.deepStrictEqual(opcionesDe([r({ comuna: 'Comuna 3' }), r({ comuna: 'Comuna 19' }), r({ comuna: '' }), r({ comuna: 'Comuna 3' })], 'comuna'),
  ['Comuna 19', 'Comuna 3']);

// Filters: every dimension, AND-combined, free text over address/name/id.
const list = [r(), r({ id: 'r2', estado: 'Visitado', afectacion: 'COLAPSO TOTAL', tipo_inmueble: 'Edificio', comuna: 'Comuna 19',
  barrio: 'Granada', nombre_edificio: 'Torre Sol', sticker: { numero: 'x', color: 'rojo', etiqueta: 'No habitable', origen: 'sistema', clasificacion: '' } })];
assert.strictEqual(applyFiltrosReportes(list, {}).length, 2);
assert.deepStrictEqual(applyFiltrosReportes(list, { estado: 'Visitado' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { afectacion: 'COLAPSO TOTAL' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { tipo: 'Casa' }).map((x) => x.id), ['r1']);
assert.deepStrictEqual(applyFiltrosReportes(list, { comuna: 'Comuna 19' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { barrio: 'Granada' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { sticker: 'rojo' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { sticker: 'SIN_STICKER' }).map((x) => x.id), ['r1']);
assert.deepStrictEqual(applyFiltrosReportes(list, { search: 'torre sol' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { search: 'CALLE' }).map((x) => x.id), ['r1', 'r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { estado: 'Visitado', comuna: 'Comuna 3' }), []);
assert.deepStrictEqual(applyFiltrosReportes([], { estado: 'Visitado' }), []);

// Sticker colour mode: API colour names to the Panel ramp; blank is "unknown".
assert.notStrictEqual(colorSticker(r({ sticker: { color: 'rojo' } })), colorSticker(r()));
assert.strictEqual(colorSticker(r({ sticker: { color: 'ROJO' } })), colorSticker(r({ sticker: { color: 'rojo' } })));
assert.strictEqual(colorSticker(r({ sticker: null })), colorSticker(r()));
assert.deepStrictEqual(Object.keys(COLOR_MODES_REPORTES), ['estado', 'afectacion', 'sticker', 'panel']);
console.log('reportes-ciudadanos.test.mjs OK');

// ── panel colour mode (visita especializada / sticker confirmados en Panel) ─
// green = visitado en Panel + sticker confirmado; amber = visitado sin sticker
// aún; gray = sin match en Panel todavía. Proximity match against Survey123/
// EDE Panel points, computed server-side (app/services/reportes_panel_state.py)
// — this module only ever reads the resulting r.panel.{visitado,sticker} flags.
const panelColorOf = COLOR_MODES_REPORTES.panel.colorOf;
assert.strictEqual(panelColorOf(r({ panel: { visitado: true, sticker: true } })), COLORS.status.h);
assert.strictEqual(panelColorOf(r({ panel: { visitado: true, sticker: false } })), COLORS.status.r2);
assert.strictEqual(panelColorOf(r({ panel: { visitado: false, sticker: false } })), COLORS.unknown);
assert.strictEqual(panelColorOf(r({})), COLORS.unknown); // no panel key at all -> not yet matched
assert.strictEqual(panelColorOf(r({ panel: null })), COLORS.unknown); // malformed/null panel -> not yet matched
assert.strictEqual(panelColorOf(r({ panel: { visitado: false, sticker: true } })), COLORS.unknown); // sticker without visitado never happens server-side, but must not crash/misclassify
console.log('reportes-ciudadanos.test.mjs panel colour mode OK');

// ── Mandatory edge cases beyond the plan's tests ─────────────────────────

// estadoDe: accent/case drift and a null record must not throw.
assert.strictEqual(estadoDe(r({ estado: 'evaluacion especializada' })).key, 'Evaluación especializada');
assert.strictEqual(estadoDe(r({ estado: 'EVALUACIÓN ESPECIALIZADA' })).key, 'Evaluación especializada');
assert.strictEqual(estadoDe(null).key, 'SIN_DATO');
assert.strictEqual(estadoDe(undefined).key, 'SIN_DATO');

// applyFiltrosReportes: sticker missing entirely, filtros undefined, search of only spaces.
const sinSticker = r({ id: 'r3' });
delete sinSticker.sticker;
assert.deepStrictEqual(applyFiltrosReportes([sinSticker], { sticker: 'SIN_STICKER' }).map((x) => x.id), ['r3']);
assert.deepStrictEqual(applyFiltrosReportes([sinSticker], { sticker: 'rojo' }), []);
assert.strictEqual(applyFiltrosReportes(list, undefined).length, 2);
assert.strictEqual(applyFiltrosReportes(list, { search: '   ' }).length, 2);

// opcionesDe: non-string (numeric) values and null records in the list.
assert.deepStrictEqual(opcionesDe([r({ comuna: 3 }), r({ comuna: 19 }), null, r({ comuna: 3 })], 'comuna'),
  ['19', '3']);
assert.deepStrictEqual(opcionesDe([null, undefined], 'comuna'), []);

// contarPor on an empty list returns an empty bucket map, never throws.
assert.deepStrictEqual(contarPor([], (x) => estadoDe(x).key), {});

// applyFiltrosReportes: a falsy record in the list must be skipped, not thrown on
// (fix(reportes) finding 5 — r.afectacion on a null r used to throw TypeError).
assert.doesNotThrow(() => applyFiltrosReportes([null, r()], { afectacion: 'DAÑO ESTRUCTURAL' }));
assert.deepStrictEqual(applyFiltrosReportes([null, r(), undefined], { afectacion: 'DAÑO ESTRUCTURAL' }).map((x) => x.id), ['r1']);
assert.deepStrictEqual(applyFiltrosReportes([null, undefined], {}), []);

console.log('reportes-ciudadanos.test.mjs edge cases OK');

// ── freshnessText (fix(reportes) finding 6/7/9) ──────────────────────────
// Count always comes from todos.length (the caller's live count), never
// from meta.row_count — meta only ever supplies the date.
assert.ok(freshnessText(null, 42).includes('42') === false, 'freshnessText: meta null has no dynamic (count/date) part');
assert.strictEqual(freshnessText(null, 42), freshnessText(undefined, 0), 'freshnessText: meta null/undefined render identically regardless of count');

const metaSinFecha = { row_count: 999 };
assert.strictEqual(freshnessText(metaSinFecha, 42), freshnessText(null, 42), 'freshnessText: meta without generated_at omits the dynamic part, same as meta null');

const metaCompleto = { generated_at: '2026-09-08T12:00:00-05:00', row_count: 999 };
const full = freshnessText(metaCompleto, 42);
assert.ok(full.includes('actualizado'), 'freshnessText: full meta includes the "actualizado" date phrase');
assert.ok(full.includes('42'), 'freshnessText: full meta uses the passed-in count (42)');
assert.ok(!full.includes('999'), 'freshnessText: full meta ignores meta.row_count for the count (uses todos.length instead)');

console.log('reportes-ciudadanos.test.mjs freshnessText OK');

// ── hasActiveRepFilters: drives "Reiniciar filtros"' enabled/disabled state ──
const emptyFiltros = { estado: '', afectacion: '', tipo: '', comuna: '', barrio: '', sticker: '', search: '' };
assert.strictEqual(hasActiveRepFilters(emptyFiltros), false, 'the default all-empty shape has nothing active');
assert.strictEqual(hasActiveRepFilters({}), false, 'must not throw on a filtros object missing every field');
assert.strictEqual(hasActiveRepFilters(null), false, 'must not throw on a null filtros object');
assert.strictEqual(hasActiveRepFilters(undefined), false, 'must not throw on an undefined filtros object');
assert.strictEqual(hasActiveRepFilters({ ...emptyFiltros, estado: 'Reportado' }), true, 'a chosen estado select is active');
assert.strictEqual(hasActiveRepFilters({ ...emptyFiltros, afectacion: 'COLAPSO TOTAL' }), true, 'a chosen afectacion select is active');
assert.strictEqual(hasActiveRepFilters({ ...emptyFiltros, tipo: 'Casa' }), true, 'a chosen tipo select is active');
assert.strictEqual(hasActiveRepFilters({ ...emptyFiltros, comuna: 'Comuna 3' }), true, 'a chosen comuna select is active');
assert.strictEqual(hasActiveRepFilters({ ...emptyFiltros, barrio: 'San Antonio' }), true, 'a chosen barrio select is active');
assert.strictEqual(hasActiveRepFilters({ ...emptyFiltros, sticker: 'verde' }), true, 'a chosen sticker select is active');
assert.strictEqual(hasActiveRepFilters({ ...emptyFiltros, search: 'calle' }), true, 'a non-empty search is active');
// Clearing the LAST active filter by hand (picking the select's own "Todos"/
// "Todas" option) must flip back to false — the real transition the reset
// button's disabled state relies on.
{
  const f = { ...emptyFiltros, comuna: 'Comuna 3' };
  assert.strictEqual(hasActiveRepFilters(f), true);
  f.comuna = '';
  assert.strictEqual(hasActiveRepFilters(f), false, 'clearing the only active filter (comuna) flips back to inactive');
}

console.log('reportes-ciudadanos.test.mjs hasActiveRepFilters OK');

// ── kpisOficialesFrom: official KPIs come ONLY from agg.kpis (backend fetch),
// never inferred/counted from the reportes list — see fix(reportes-ciudadanos)
// design note. null means "nothing usable"; the array never invents a zero
// for a field the API didn't return. ────────────────────────────────────────
const kpisCompletos = {
  inmueblesReportados: 1200, reportes: 1500, inmueblesVerificados: 800, pendientes: 400,
  asignaciones: 300, asignacionesEspecializadas: 50, inmueblesVisitadosNoCriticos: 700,
  avancePct: 33.333, cuadrillasEnCampo: 12, cuadrillasEspecializadas: 3, zonaRural: 20, fueraDeCali: 5,
};

// Boundary: every "not a usable kpis object" shape must return null, not throw.
assert.strictEqual(kpisOficialesFrom(undefined), null, 'agg undefined -> null');
assert.strictEqual(kpisOficialesFrom(null), null, 'agg null -> null');
assert.strictEqual(kpisOficialesFrom({}), null, 'agg.kpis undefined -> null');
assert.strictEqual(kpisOficialesFrom({ kpis: undefined }), null, 'agg.kpis explicitly undefined -> null');
assert.strictEqual(kpisOficialesFrom({ kpis: null }), null, 'agg.kpis null -> null');
assert.strictEqual(kpisOficialesFrom({ kpis: 'no-data' }), null, 'agg.kpis a string -> null');
assert.strictEqual(kpisOficialesFrom({ kpis: 42 }), null, 'agg.kpis a number -> null');
assert.strictEqual(kpisOficialesFrom({ kpis: [1, 2, 3] }), null, 'agg.kpis an array -> null');
// CRITICAL fix 1: a usable object where EVERY field is missing/non-numeric
// must also return null, never an empty array — kpisCaption/kpisHtml both
// test the result for truthiness, and [] is truthy, which used to render a
// confident-looking caption with zero KPI tiles and no "unavailable" state.
assert.strictEqual(kpisOficialesFrom({ kpis: {} }), null, 'agg.kpis an empty object -> null, not []');
assert.strictEqual(kpisOficialesFrom({ kpis: { inmueblesReportados: 'x', reportes: null } }), null,
  'agg.kpis with only non-numeric fields -> null, not []');

// Full valid kpis: exact order, labels, and es-CO formatting (independent
// worked values, not recomputed the way the code does).
{
  const out = kpisOficialesFrom({ kpis: kpisCompletos });
  assert.deepStrictEqual(out.map((k) => k.key), [
    'inmueblesReportados', 'reportes', 'inmueblesVerificados', 'pendientes', 'asignaciones',
    'asignacionesEspecializadas', 'inmueblesVisitadosNoCriticos', 'avancePct', 'cuadrillasEnCampo',
    'cuadrillasEspecializadas', 'zonaRural', 'fueraDeCali',
  ], 'exact field order, 12/12 present');
  assert.deepStrictEqual(out.map((k) => k.label), [
    'inmuebles reportados', 'reportes (según API)', 'inmuebles verificados', 'pendientes', 'asignaciones',
    'asignaciones especializadas', 'visitados no críticos', 'avance', 'cuadrillas en campo',
    'cuadrillas especializadas', 'zona rural', 'fuera de Cali',
  ], 'exact labels in order');
  const formatted = Object.fromEntries(out.map((k) => [k.key, k.formatted]));
  // F8: assert against the SAME toLocaleString('es-CO', …) call the code
  // itself makes, rather than a hard-coded '.'/',' string — a sandbox
  // without full-ICU data can render es-CO grouping/decimal marks
  // differently, and this keeps the assertion meaningful either way.
  assert.strictEqual(formatted.inmueblesReportados, (1200).toLocaleString('es-CO'), 'es-CO thousands separator');
  assert.strictEqual(formatted.reportes, (1500).toLocaleString('es-CO'));
  assert.strictEqual(formatted.pendientes, (400).toLocaleString('es-CO'));
  assert.strictEqual(
    formatted.avancePct,
    `${(33.333).toLocaleString('es-CO', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`,
    'avancePct: one decimal place, trailing % ',
  );
}

// Partial kpis: a missing field is ABSENT from the result, never present as 0.
{
  const parcial = { inmueblesReportados: 900, reportes: 950, avancePct: 0 };
  const out = kpisOficialesFrom({ kpis: parcial });
  assert.strictEqual(out.length, 3, 'only the 3 present numeric fields are returned');
  assert.strictEqual(out.find((k) => k.key === 'inmueblesReportados').formatted, (900).toLocaleString('es-CO'));
  assert.strictEqual(
    out.find((k) => k.key === 'avancePct').formatted,
    `${(0).toLocaleString('es-CO', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`,
  );
  assert.strictEqual(out.find((k) => k.key === 'pendientes'), undefined, 'missing field is absent, not 0');
  assert.strictEqual(out.find((k) => k.key === 'cuadrillasEnCampo'), undefined, 'missing field is absent, not 0');
}

// Non-numeric values mixed with valid ones: string/null/boolean are omitted,
// the numeric siblings survive.
{
  const mixto = {
    inmueblesReportados: 'sin-dato', reportes: 1500, inmueblesVerificados: null,
    pendientes: 400, asignaciones: true, avancePct: 100,
  };
  const out = kpisOficialesFrom({ kpis: mixto });
  assert.deepStrictEqual(out.map((k) => k.key), ['reportes', 'pendientes', 'avancePct'],
    'string/null/boolean fields dropped, numeric fields kept in defined order');
  assert.strictEqual(
    out.find((k) => k.key === 'avancePct').formatted,
    `${(100).toLocaleString('es-CO', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`,
  );
}

// avancePct formatting edge cases, asserted against the same toLocaleString
// call the code makes (F8: locale-safe, doesn't assume full-ICU '.'/',' marks).
{
  const expected = (v) => `${v.toLocaleString('es-CO', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
  const at = (v) => kpisOficialesFrom({ kpis: { avancePct: v } })[0].formatted;
  assert.strictEqual(at(0), expected(0));
  assert.strictEqual(at(100), expected(100));
  assert.strictEqual(at(33.333), expected(33.333));
}

// SUGGESTION fix 6: Number.isFinite rejects Infinity/-Infinity (typeof
// Infinity === 'number' is true and it is not NaN, so the old typeof+NaN
// check let it through and it would have rendered as "∞ %").
assert.strictEqual(kpisOficialesFrom({ kpis: { avancePct: Infinity, reportes: 10 } }).find((k) => k.key === 'avancePct'), undefined,
  'Infinity avancePct is omitted, never rendered as an infinite value');
assert.strictEqual(kpisOficialesFrom({ kpis: { avancePct: Infinity, reportes: 10 } }).find((k) => k.key === 'reportes').formatted, '10',
  'a sibling finite field still survives alongside the omitted Infinity one');
assert.strictEqual(kpisOficialesFrom({ kpis: { pendientes: -Infinity } }), null,
  '-Infinity as the only field present -> omitted -> zero fields survive -> null (fix 1 + fix 6 together)');

console.log('reportes-ciudadanos.test.mjs kpisOficialesFrom OK');

// ── kpisCaption: date/stale/error states shown above the official KPI row ──
{
  const fresco = kpisCaption({ kpis: kpisCompletos, kpis_generated_at: '2026-09-11T19:49:00-05:00', kpis_stale: false, kpis_error: null });
  assert.ok(fresco.includes('según Atención Sismo'), 'fresh caption is date-based');
  assert.ok(!fresco.includes('⚠'), 'fresh caption has no warning prefix');
  // F9: the "universo completo, no filtrado" note belongs WITH an actual
  // (available or stale) official-KPIs reading, never appended separately
  // downstream to whatever kpisCaption returns.
  assert.ok(fresco.includes('universo completo, no filtrado'), 'fresh caption includes the universo-completo note');
}
{
  const stale = kpisCaption({ kpis: kpisCompletos, kpis_generated_at: '2026-09-11T19:49:00-05:00', kpis_stale: true, kpis_error: 'timeout' });
  assert.ok(stale.includes('⚠'), 'stale caption is warning-prefixed');
  assert.ok(stale.toLowerCase().includes('no respondió'), 'stale caption explains the carried-forward snapshot');
  assert.ok(stale.includes('universo completo, no filtrado'), 'stale caption ALSO includes the universo-completo note');
}
{
  const sinKpis = kpisCaption({ kpis: null, kpis_generated_at: null, kpis_stale: false, kpis_error: 'HTTP 503' });
  assert.strictEqual(sinKpis, 'KPIs oficiales no disponibles', 'error with no usable kpis short-circuits the date-based caption');
  // F9 (CONFIRMED bug): "KPIs oficiales no disponibles" must NEVER be
  // followed by "universo completo, no filtrado" — there is nothing
  // "complete" to claim when there are zero usable official KPIs to show.
  assert.ok(!sinKpis.includes('universo completo'), 'unavailable caption never claims universo completo');
}
// WARNING fix 2: "no usable kpis" must short-circuit to the unavailable
// message regardless of whether kpis_error happens to be set — agg === null
// or agg.kpis === null with NO kpis_error field at all used to fall through
// to a misleading date-based caption ("fecha desconocida").
assert.strictEqual(kpisCaption(null), 'KPIs oficiales no disponibles', 'kpisCaption(null): no usable kpis, no kpis_error field at all -> unavailable message, not a date fallback');
assert.strictEqual(kpisCaption({ kpis: null }), 'KPIs oficiales no disponibles', 'kpisCaption({kpis:null}) with no kpis_error field at all -> unavailable message');
{
  const fechaInvalida = kpisCaption({ kpis: kpisCompletos, kpis_generated_at: 'not-a-real-date', kpis_stale: false, kpis_error: null });
  assert.ok(fechaInvalida.includes('fecha desconocida'), 'unparsable date falls back to "fecha desconocida", never throws');
}
{
  const sinFecha = kpisCaption({ kpis: kpisCompletos, kpis_generated_at: undefined, kpis_stale: false, kpis_error: null });
  assert.ok(sinFecha.includes('fecha desconocida'), 'missing kpis_generated_at falls back to "fecha desconocida"');
}
assert.doesNotThrow(() => kpisCaption(undefined), 'kpisCaption(undefined) must not throw');
assert.doesNotThrow(() => kpisCaption(null), 'kpisCaption(null) must not throw');

console.log('reportes-ciudadanos.test.mjs kpisCaption OK');
