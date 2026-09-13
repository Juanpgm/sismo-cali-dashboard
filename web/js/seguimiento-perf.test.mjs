// Node perf tripwire (L8, plan performance budget: buildProfessionalRows
// <=25 ms browser / <250 ms Node tripwire on a 3k/2k fixture). This is a
// looser Node-side backstop, not the browser budget itself — it exists to
// catch an accidental O(rows * stickers) regression (like B1/M5 before this
// change) long before it ever reaches a browser profiler.
// Run: node web/js/seguimiento-perf.test.mjs
import assert from 'node:assert/strict';
import {
  buildIdentityIndex, buildProfessionalRows, buildTimeline, buildTemporalMetricsByKey,
} from './seguimiento.js';

// Deterministic seeded PRNG (mulberry32) — NEVER Math.random(): a perf test
// that occasionally trips over an unlucky random fixture shape (or, worse,
// occasionally passes for the wrong reason) is worse than no perf test.
function mulberry32(seed) {
  let a = seed;
  return function rng() {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rng = mulberry32(20260913);

const N_PROFESSIONALS = 110;
const NAMES = Array.from({ length: N_PROFESSIONALS }, (_, i) => `Profesional ${String(i + 1).padStart(3, '0')}`);
const BARRIOS = [
  'San Antonio', 'El Peñón', 'Champagnat', 'Tequendama', 'Ciudad Jardín',
  'El Ingenio', 'Meléndez', 'San Fernando', 'Versalles', 'La Flora',
  'Granada', 'Aguablanca', 'El Poblado', 'Siloé', 'Terrón Colorado',
];
const DAYS = 30;
const BASE_DAY_MS = Date.UTC(2026, 0, 1); // 2026-01-01

function pad2(n) { return String(n).padStart(2, '0'); }

function ymdOnDay(dayOffset) {
  const d = new Date(BASE_DAY_MS + dayOffset * 86400000);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
}
function randomIsoOnDay(dayOffset) {
  const h = Math.floor(rng() * 24);
  const mi = Math.floor(rng() * 60);
  return `${ymdOnDay(dayOffset)}T${pad2(h)}:${pad2(mi)}:00+00:00`;
}
function naiveDatetimeOnDay(dayOffset) {
  const h = Math.floor(rng() * 24);
  const mi = Math.floor(rng() * 60);
  return `${ymdOnDay(dayOffset)}T${pad2(h)}:${pad2(mi)}`;
}

const N_STICKERS = 3000;
const N_SURVEYS = 1900;

const stickers = [];
for (let i = 0; i < N_STICKERS; i++) {
  const profIdx = Math.floor(rng() * N_PROFESSIONALS);
  const name = NAMES[profIdx];
  const hasCedula = rng() >= 0.35; // ~35% blank cédula
  const isSinDato = rng() < 0.25; // ~25% SIN_DATO (fase null, no NP fallback)
  const fase = isSinDato ? null : (rng() < 0.5 ? 1 : 2);
  const dayOffset = Math.floor(rng() * DAYS);
  const barrio = BARRIOS[Math.floor(rng() * BARRIOS.length)];
  stickers.push({
    inspector: {
      nombre_completo: name,
      identificacion: hasCedula ? String(100000 + profIdx) : '',
    },
    inspector_fuente: 'evaluacion',
    fuente: 'atencionsismo',
    fase,
    fecha: randomIsoOnDay(dayOffset),
    barrio_reportado: barrio,
  });
}

const surveys = [];
for (let i = 0; i < N_SURVEYS; i++) {
  const profIdx = Math.floor(rng() * N_PROFESSIONALS);
  const dayOffset = Math.floor(rng() * DAYS);
  surveys.push({
    nombre_evaluador: NAMES[profIdx],
    fecha_inspeccion: ymdOnDay(dayOffset),
    fecha_hora: naiveDatetimeOnDay(dayOffset),
  });
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function timeIt(fn) {
  const start = process.hrtime.bigint();
  const result = fn();
  const ms = Number(process.hrtime.bigint() - start) / 1e6;
  return { ms, result };
}

// buildIdentityIndex + buildProfessionalRows (barrios batch folded in, B1) —
// run 3x, take the median so a single GC pause never flakes the assertion.
const rowsRuns = [];
let lastRows;
for (let i = 0; i < 3; i++) {
  const { ms, result } = timeIt(() => {
    const identity = buildIdentityIndex({ stickers, surveys });
    return buildProfessionalRows({
      stickers, surveys, identity, today: '2026-02-05',
    });
  });
  rowsRuns.push(ms);
  lastRows = result;
}
const rowsMedian = median(rowsRuns);
console.log(`buildIdentityIndex + buildProfessionalRows (${N_STICKERS} stickers / ${N_SURVEYS} surveys, ${lastRows.rows.length} rows): [${rowsRuns.map((n) => n.toFixed(1)).join(', ')}] ms -- median ${rowsMedian.toFixed(1)} ms`);
assert.ok(rowsMedian < 250, `buildIdentityIndex + buildProfessionalRows median ${rowsMedian.toFixed(1)} ms must stay under the 250 ms Node tripwire`);

// buildTemporalMetricsByKey (M5's batch pass) on the same fixture — a real
// render() will eventually pay for this alongside buildProfessionalRows once
// the "Análisis temporales" sub-tab (W10) wires it in, so it gets its own
// tripwire against the same 250 ms Node budget.
const temporalRuns = [];
for (let i = 0; i < 3; i++) {
  const { ms } = timeIt(() => {
    const identity = buildIdentityIndex({ stickers, surveys });
    return buildTemporalMetricsByKey({
      stickers, surveys, identity, today: '2026-02-05',
    });
  });
  temporalRuns.push(ms);
}
const temporalMedian = median(temporalRuns);
console.log(`buildIdentityIndex + buildTemporalMetricsByKey: [${temporalRuns.map((n) => n.toFixed(1)).join(', ')}] ms -- median ${temporalMedian.toFixed(1)} ms`);
assert.ok(temporalMedian < 250, `buildTemporalMetricsByKey median ${temporalMedian.toFixed(1)} ms must stay under the 250 ms Node tripwire`);

// buildTimeline — its own, tighter 100 ms budget (plan).
const timelineRuns = [];
for (let i = 0; i < 3; i++) {
  const { ms } = timeIt(() => buildTimeline({ stickers, surveys }));
  timelineRuns.push(ms);
}
const timelineMedian = median(timelineRuns);
console.log(`buildTimeline: [${timelineRuns.map((n) => n.toFixed(1)).join(', ')}] ms -- median ${timelineMedian.toFixed(1)} ms`);
assert.ok(timelineMedian < 100, `buildTimeline median ${timelineMedian.toFixed(1)} ms must stay under the 100 ms Node tripwire`);

console.log('seguimiento-perf.test.mjs: all assertions passed');
