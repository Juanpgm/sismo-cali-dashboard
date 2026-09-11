// Seguimiento tab: cross-source view of who is doing the fieldwork, joining
// stickers (Atención Sismo, via fetchEvaluacionesOnce(getToken,
// 'stickersAtencionsismo') — same lifecycle as Stickers) with the Survey123/
// EDE inspections already loaded into store.records (data.js). The two
// sources have no shared id, only a free-text professional name — stickers
// carry `inspector.nombre_completo`, Survey carries `nombre_evaluador` — so
// every row here is an APPROXIMATE cross-source match by normalized name,
// never a verified join (see the caveat text rendered under the toolbar).
//
// Same overall shape as reportes-ciudadanos.js: pure aggregation/sort
// functions exported for the Node self-check (seguimiento.test.mjs), then
// the DOM section below. The pure functions never import data.js (it pulls
// the Firebase SDK via a bare https:// specifier, which breaks Node's ESM
// loader on plain import).
import { COLORS, escapeHtml, normalize, loadXlsx, downloadStamp, showToast, faseKeyDe } from './utils.js';
import { upsertChart, baseOptions, totalDataLabelPlugin } from './charts.js';
import { fetchEvaluacionesOnce } from './stickers.js';
import { loadPdfmake } from './report.js';

const STICKERS_ENDPOINT = 'stickersAtencionsismo';

// Spanish display labels for faseKeyDe()'s three return values — used by
// professionalRecords/buildProfessionalReportDocDefinition below, kept in
// sync with the table's own COLUMNS labels ("Stickers F-I"/"F-II"/"sin fase").
const FASE_LABELS = { FASE_I: 'Fase I', FASE_II: 'Fase II', SIN_DATO: 'Sin dato' };

// ── Pure aggregation helpers ────────────────────────────────────────────────

/** Strip accents (NFD, diacritics removed), lowercase, trim, collapse
 *  internal whitespace. The join key between a sticker's
 *  inspector.nombre_completo and a Survey record's nombre_evaluador — both
 *  are free-text fields typed by different people in different apps, so
 *  accent/case/spacing drift is the norm, not the exception. */
export function normalizeName(raw) {
  if (raw === null || raw === undefined) return '';
  return String(raw)
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, ' ');
}

/** Date-only (YYYY-MM-DD) part of a value that may be a plain date string, a
 *  full (possibly UTC-offset-aware) ISO timestamp, malformed text, or
 *  missing entirely. Returns null for anything that doesn't resolve to a
 *  real calendar date — a malformed value must never silently masquerade as
 *  a valid date (e.g. "2026-02-30" rolling over to March in a naive
 *  `new Date()` parse).
 *
 *  A bare "YYYY-MM-DD" string (Survey's own fecha_inspeccion shape) is taken
 *  literally — anchored to the full string, not just its prefix — since it
 *  never carried a time/offset component to convert. Anything else (a full
 *  ISO timestamp, e.g. the atencionsismo API's `fecha`, which is UTC-aware:
 *  "...T02:00:00+00:00", backend/app/routers/stickers.py) resolves to the
 *  LOCAL calendar day (same convention as evaluaciones.js's formatFecha) —
 *  Cali is UTC-5, so reading the UTC date straight off the string would
 *  silently shift an evening record to the wrong day for every KPI/filter/
 *  timeline bucket keyed off it. This was the bug: the old prefix-only regex
 *  matched the leading 10 characters of ANY timestamp, so a full ISO string
 *  was treated exactly like a bare date and never actually converted. */
function dateOnly(value) {
  if (value === null || value === undefined) return null;
  const raw = String(value).trim();
  if (!raw) return null;
  const bare = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
  if (bare) {
    const [, y, mo, d] = bare;
    const parsed = new Date(`${raw}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime())) return null;
    // Reject overflow (e.g. "2026-02-30" -> rolls to March 2): the date
    // actually parsed must match the digits typed, not a rolled-over one.
    if (parsed.getUTCFullYear() !== Number(y) || parsed.getUTCMonth() + 1 !== Number(mo) || parsed.getUTCDate() !== Number(d)) return null;
    return raw;
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return null;
  const y = parsed.getFullYear();
  const mo = String(parsed.getMonth() + 1).padStart(2, '0');
  const d = String(parsed.getDate()).padStart(2, '0');
  return `${y}-${mo}-${d}`;
}

/** Whether `dateStr` (YYYY-MM-DD or null) falls within [from, to] inclusive.
 *  A null/missing `from` or `to` leaves that side unbounded. A null
 *  `dateStr` is never "in range" — callers decide separately whether a
 *  missing date should still be included when no filter is active at all. */
function inDateRange(dateStr, from, to) {
  if (!dateStr) return false;
  if (from && dateStr < from) return false;
  if (to && dateStr > to) return false;
  return true;
}

/** Whether one sticker belongs in the currently filtered set. A sticker
 *  with no resolvable `fecha` (the atencionsismo API's own gap — see the
 *  module doc in stickers.js) is kept ONLY while no date filter is active:
 *  once a range is picked, a record with an unknown date can no longer be
 *  proven to fall inside it, so it drops out entirely (not just from the
 *  timeline — see buildProfessionalRows' stickersWithoutDate for the
 *  unfiltered count callers show as a KPI/note). */
function stickerIncluded(sticker, from, to) {
  const dateFilterActive = Boolean(from || to);
  const d = dateOnly(sticker && sticker.fecha);
  if (d === null) return !dateFilterActive;
  return inDateRange(d, from, to);
}

/** Same rule as stickerIncluded, over a Survey record's fecha_inspeccion —
 *  including an invalid/malformed value (never assumed to be "in range"). */
function surveyIncluded(survey, from, to) {
  const dateFilterActive = Boolean(from || to);
  const d = dateOnly(survey && survey.fecha_inspeccion);
  if (d === null) return !dateFilterActive;
  return inDateRange(d, from, to);
}

/** Per-professional rows joining stickers + Survey by normalized name, plus
 *  the "Sin profesional identificado" bucket and the sticker-without-fecha
 *  count the UI surfaces as its own KPI/note. `from`/`to` (YYYY-MM-DD,
 *  either may be null) filter both sources — Survey via fecha_inspeccion,
 *  stickers via fecha's date part; see stickerIncluded/surveyIncluded above
 *  for the null-date edge case. */
export function buildProfessionalRows({ stickers, surveys, from = null, to = null } = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];

  const rowsByKey = new Map();
  let unassignedStickers = 0;
  let unassignedSurveys = 0;
  let stickersWithoutDate = 0;

  function ensureRow(key, rawName) {
    let row = rowsByKey.get(key);
    if (!row) {
      row = {
        key,
        nameCounts: new Map(),
        cedula: '', codigo: '', entidad: '',
        stickersFase1: 0, stickersFase2: 0, stickersSinFase: 0, stickersTotal: 0,
        surveyTotal: 0, rosterSourced: 0,
        dates: [],
      };
      rowsByKey.set(key, row);
    }
    row.nameCounts.set(rawName, (row.nameCounts.get(rawName) || 0) + 1);
    return row;
  }

  for (const s of stickerList) {
    if (!s) continue;
    if (!stickerIncluded(s, from, to)) continue;
    const rawName = (s.inspector && s.inspector.nombre_completo) || '';
    const key = normalizeName(rawName);
    const dateVal = dateOnly(s.fecha);
    if (dateVal === null) stickersWithoutDate += 1;
    if (!key) { unassignedStickers += 1; continue; }
    const row = ensureRow(key, rawName);
    if (!row.cedula && s.inspector && s.inspector.identificacion) row.cedula = s.inspector.identificacion;
    if (!row.codigo && s.inspector && s.inspector.codigo) row.codigo = s.inspector.codigo;
    if (!row.entidad && s.inspector && s.inspector.entidad) row.entidad = s.inspector.entidad;
    // Shared Fase I/II rule (utils.js's faseKeyDe) instead of a second,
    // independent `fase === 1/2` switch: for an atencionsismo sticker whose
    // `fase` isn't 1 or 2, this falls back to the inspector's NP category
    // (P3+ = Fase II) exactly like evaluaciones.js does — a second inline
    // rule here would silently drift from that one over time.
    const faseKey = faseKeyDe(s);
    if (faseKey === 'FASE_I') row.stickersFase1 += 1;
    else if (faseKey === 'FASE_II') row.stickersFase2 += 1;
    else row.stickersSinFase += 1;
    row.stickersTotal += 1;
    if (s.inspector_fuente === 'roster') row.rosterSourced += 1;
    if (dateVal) row.dates.push(dateVal);
  }

  for (const sv of surveyList) {
    if (!sv) continue;
    if (!surveyIncluded(sv, from, to)) continue;
    const rawName = sv.nombre_evaluador || '';
    const key = normalizeName(rawName);
    if (!key) { unassignedSurveys += 1; continue; }
    const row = ensureRow(key, rawName);
    if (!row.entidad && sv.entidad) row.entidad = sv.entidad;
    row.surveyTotal += 1;
    const dateVal = dateOnly(sv.fecha_inspeccion);
    if (dateVal) row.dates.push(dateVal);
  }

  const rows = [...rowsByKey.values()].map((row) => {
    let bestName = '';
    let bestCount = -1;
    for (const [name, count] of row.nameCounts) {
      if (count > bestCount) { bestCount = count; bestName = name; }
    }
    const sortedDates = [...row.dates].sort();
    const activeDays = new Set(sortedDates).size;
    const datedRecords = sortedDates.length;
    return {
      key: row.key,
      name: bestName,
      cedula: row.cedula,
      codigo: row.codigo,
      entidad: row.entidad,
      stickersFase1: row.stickersFase1,
      stickersFase2: row.stickersFase2,
      stickersSinFase: row.stickersSinFase,
      stickersTotal: row.stickersTotal,
      surveyTotal: row.surveyTotal,
      total: row.stickersTotal + row.surveyTotal,
      firstDate: sortedDates.length ? sortedDates[0] : null,
      lastDate: sortedDates.length ? sortedDates[sortedDates.length - 1] : null,
      activeDays,
      avgPerActiveDay: activeDays ? Math.round((datedRecords / activeDays) * 100) / 100 : 0,
      rosterSourced: row.rosterSourced,
    };
  });

  const stickersAssigned = rows.reduce((n, r) => n + r.stickersTotal, 0);
  const surveysAssigned = rows.reduce((n, r) => n + r.surveyTotal, 0);

  return {
    rows,
    unassigned: { stickers: unassignedStickers, surveys: unassignedSurveys },
    stickersWithoutDate,
    totals: {
      professionals: rows.length,
      stickers: stickersAssigned + unassignedStickers,
      surveys: surveysAssigned + unassignedSurveys,
      avgPerProfessional: rows.length ? Math.round(((stickersAssigned + surveysAssigned) / rows.length) * 100) / 100 : 0,
      unassigned: unassignedStickers + unassignedSurveys,
      stickersWithoutDate,
    },
  };
}

/** Daily timeline of dated stickers + Survey records, gap-filled with zeros
 *  from the earliest to the latest date in the (already filtered) set — a
 *  chart with missing days would misread as "no activity that day" the same
 *  way as "no data yet". Records with no resolvable date (see dateOnly)
 *  never enter the timeline, only the KPI/totals in buildProfessionalRows.
 *  `professionalKey` restricts to one professional's normalized name; null
 *  (or any other falsy value) means every professional. */
export function buildTimeline({ stickers, surveys, from = null, to = null, professionalKey = null } = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];

  const stickerCounts = new Map();
  const surveyCounts = new Map();

  for (const s of stickerList) {
    if (!s) continue;
    if (!stickerIncluded(s, from, to)) continue;
    if (professionalKey && normalizeName((s.inspector && s.inspector.nombre_completo) || '') !== professionalKey) continue;
    const d = dateOnly(s.fecha);
    if (!d) continue;
    stickerCounts.set(d, (stickerCounts.get(d) || 0) + 1);
  }

  for (const sv of surveyList) {
    if (!sv) continue;
    if (!surveyIncluded(sv, from, to)) continue;
    if (professionalKey && normalizeName(sv.nombre_evaluador || '') !== professionalKey) continue;
    const d = dateOnly(sv.fecha_inspeccion);
    if (!d) continue;
    surveyCounts.set(d, (surveyCounts.get(d) || 0) + 1);
  }

  const allDates = [...new Set([...stickerCounts.keys(), ...surveyCounts.keys()])].sort();
  if (!allDates.length) {
    return { labels: [], stickers: [], surveys: [], stickersCumulative: [], surveysCumulative: [] };
  }

  const labels = [];
  const cursor = new Date(`${allDates[0]}T00:00:00Z`);
  const end = new Date(`${allDates[allDates.length - 1]}T00:00:00Z`);
  while (cursor.getTime() <= end.getTime()) {
    labels.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }

  const stickersDaily = labels.map((d) => stickerCounts.get(d) || 0);
  const surveysDaily = labels.map((d) => surveyCounts.get(d) || 0);
  // Running totals, one per source — each accumulates independently across
  // every label (including zero-count days, which never reset it).
  let stickersRunning = 0;
  let surveysRunning = 0;
  const stickersCumulative = stickersDaily.map((n) => (stickersRunning += n));
  const surveysCumulative = surveysDaily.map((n) => (surveysRunning += n));

  return { labels, stickers: stickersDaily, surveys: surveysDaily, stickersCumulative, surveysCumulative };
}

/** The raw stickers/surveys attributed to ONE professional (`row.key`, the
 *  same normalized-name join buildProfessionalRows/buildTimeline already
 *  use) — "los puntos recogidos", for the per-professional PDF report.
 *  Unlike buildTimeline/buildProfessionalRows this never applies a date
 *  filter: the report is a full career summary for that person, not a
 *  filtered view. An undated sticker still appears (it IS a real collected
 *  point — same reasoning as the "sin fecha" KPI), sorted after every dated
 *  one; an invalid/garbage fecha_inspeccion is treated the same as missing. */
export function professionalRecords(row, { stickers, surveys }) {
  const key = row && row.key;
  const stickerList = (Array.isArray(stickers) ? stickers : [])
    .filter((s) => s && normalizeName((s.inspector && s.inspector.nombre_completo) || '') === key)
    .map((s) => ({
      codigo: s.codigo_edificacion || '',
      direccion: (s.descripcion && s.descripcion.direccion) || '',
      municipio: s.municipio || '',
      fecha: dateOnly(s.fecha),
      faseLabel: FASE_LABELS[faseKeyDe(s)] || FASE_LABELS.SIN_DATO,
    }));
  stickerList.sort((a, b) => {
    if (a.fecha === b.fecha) return a.codigo < b.codigo ? -1 : a.codigo > b.codigo ? 1 : 0;
    if (a.fecha === null) return 1;
    if (b.fecha === null) return -1;
    return a.fecha < b.fecha ? -1 : 1;
  });

  const surveyList = (Array.isArray(surveys) ? surveys : [])
    .filter((sv) => sv && normalizeName(sv.nombre_evaluador || '') === key)
    .map((sv) => ({
      direccion: sv.direccion || '',
      nombreEdificacion: sv.nombre_edificacion || '',
      fecha: dateOnly(sv.fecha_inspeccion),
    }));
  surveyList.sort((a, b) => {
    if (a.fecha === b.fecha) return a.direccion < b.direccion ? -1 : a.direccion > b.direccion ? 1 : 0;
    if (a.fecha === null) return 1;
    if (b.fecha === null) return -1;
    return a.fecha < b.fecha ? -1 : 1;
  });

  return { stickerPoints: stickerList, surveyPoints: surveyList };
}

// ── Per-professional PDF report ─────────────────────────────────────────────
// Same recipe as report.js: a PURE doc-definition builder (no fetch/DOM/
// pdfmake dependency, so it is Node-testable) plus a thin async orchestrator
// in the DOM section below that lazy-loads pdfmake and triggers the download.

const REPORT_DISCLAIMER = 'Informe generado automáticamente a partir del cruce aproximado por nombre '
  + 'entre Stickers y Survey — ver la nota de la pestaña Seguimiento. No constituye un documento oficial certificado.';

/** 2-column key/value table, filtering out blank values — same idea as
 *  report.js's own (private) fieldTable, kept local here since this report's
 *  "puntos recogidos" tables are multi-column and do not fit that helper. */
function kvTable(rows) {
  const body = rows.filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (!body.length) return [];
  return [{
    table: {
      widths: ['50%', '50%'],
      body: body.map(([k, v]) => [
        { text: k, style: 'fieldLabel' },
        { text: String(v), style: 'fieldValue' },
      ]),
    },
    layout: 'lightHorizontalLines',
    margin: [0, 0, 0, 10],
  }];
}

/** Multi-column table for a list of points, or a plain "sin registros"
 *  message when the list is empty — a report a professional's manager reads
 *  should never render a silently-empty table and leave them guessing
 *  whether that means zero work or a rendering bug. */
function pointsTable(headers, rows, emptyText) {
  if (!rows.length) return [{ text: emptyText, style: 'fieldValue', margin: [0, 0, 0, 10] }];
  return [{
    table: {
      headerRows: 1,
      widths: headers.map(() => '*'),
      body: [
        headers.map((h) => ({ text: h, style: 'tableHeader' })),
        ...rows.map((r) => r.map((cell) => ({ text: cell === '' || cell === null ? 'Sin dato' : String(cell), style: 'fieldValue' }))),
      ],
    },
    layout: 'lightHorizontalLines',
    margin: [0, 0, 0, 10],
  }];
}

/** Pure builder: a professional's row (buildProfessionalRows output) + their
 *  raw points (professionalRecords output) -> pdfmake document definition.
 *  Same style tokens as report.js's builders, so this report reads as part
 *  of the same family of PDFs the app already generates. */
export function buildProfessionalReportDocDefinition(row, { stickerPoints, surveyPoints }) {
  const caveat = row.rosterSourced > 0
    ? [{ text: `⚠ ${row.rosterSourced} sticker(s) con identidad completada por roster (aproximada, no verificada contra la evaluación).`, style: 'caveat', margin: [0, 0, 0, 8] }]
    : [];
  return {
    content: [
      { text: `Informe de seguimiento — ${row.name || 'Sin dato'}`, style: 'title' },
      { text: `Cédula: ${row.cedula || 'Sin dato'} · Código: ${row.codigo || 'Sin dato'} · Entidad: ${row.entidad || 'Sin dato'}`, style: 'subtitle' },
      { text: `Fecha de generación: ${downloadStamp().legible}`, style: 'subtitle' },
      { text: REPORT_DISCLAIMER, style: 'disclaimer', margin: [0, 4, 0, 12] },
      ...caveat,
      { text: 'Resumen', style: 'sectionHeader' },
      ...kvTable([
        ['Stickers Fase I', row.stickersFase1],
        ['Stickers Fase II', row.stickersFase2],
        ['Stickers sin fase', row.stickersSinFase],
        ['Evaluaciones Survey', row.surveyTotal],
        ['Total', row.total],
        ['Primer registro', row.firstDate || 'Sin dato'],
        ['Último registro', row.lastDate || 'Sin dato'],
        ['Días activos', row.activeDays],
        ['Promedio por día activo', row.avgPerActiveDay],
      ]),
      { text: `Puntos recogidos — Stickers (${stickerPoints.length})`, style: 'sectionHeader' },
      ...pointsTable(
        ['Código', 'Dirección', 'Municipio', 'Fecha', 'Fase'],
        stickerPoints.map((p) => [p.codigo, p.direccion, p.municipio, p.fecha || 'Sin fecha', p.faseLabel]),
        'Sin registros de stickers.',
      ),
      { text: `Puntos recogidos — Survey (${surveyPoints.length})`, style: 'sectionHeader' },
      ...pointsTable(
        ['Dirección', 'Edificación', 'Fecha'],
        surveyPoints.map((p) => [p.direccion, p.nombreEdificacion, p.fecha || 'Sin fecha']),
        'Sin registros de Survey.',
      ),
    ],
    styles: {
      title: { fontSize: 16, bold: true },
      subtitle: { fontSize: 9, color: '#555' },
      disclaimer: { fontSize: 8, italics: true, color: '#777' },
      caveat: { fontSize: 9, italics: true, color: '#a15c00' },
      sectionHeader: { fontSize: 12, bold: true, margin: [0, 10, 0, 4] },
      fieldLabel: { fontSize: 9, bold: true },
      fieldValue: { fontSize: 9 },
      tableHeader: { fontSize: 9, bold: true, fillColor: '#eeeeee' },
    },
    defaultStyle: { fontSize: 9 },
  };
}

/** Sorts a copy of `rows` by `column`, ascending or descending. String
 *  columns compare with localeCompare (es); numeric/null columns compare
 *  numerically, with a null/undefined value sorting as the lowest ("-∞",
 *  never crashing on a missing firstDate/lastDate). Ties break by `key` so
 *  the result is deterministic regardless of the input's original order. */
/** Whether the professional search box or the Desde/Hasta date range is
 *  currently narrowing the table — drives "Reiniciar filtros"' enabled/
 *  disabled + soft-orange state. Deliberately excludes `seg-chart-professional`
 *  (never narrows the table, only which line the Ritmo diario chart
 *  highlights) and sort order — neither is a data-narrowing filter. Exported
 *  so a self-check can cover the transitions without the DOM. */
export function hasActiveSegFilters({ search = '', from = null, to = null } = {}) {
  return Boolean(search || from || to);
}

export function sortRows(rows, column, dir = 'asc') {
  const sign = dir === 'desc' ? -1 : 1;
  const copy = [...rows];
  copy.sort((a, b) => {
    const av = a[column];
    const bv = b[column];
    let cmp;
    if (typeof av === 'string' || typeof bv === 'string') {
      cmp = String(av ?? '').localeCompare(String(bv ?? ''), 'es');
    } else {
      const an = av === null || av === undefined ? -Infinity : av;
      const bn = bv === null || bv === undefined ? -Infinity : bv;
      cmp = an === bn ? 0 : (an < bn ? -1 : 1);
    }
    // Tie-break is always ascending by `key`, regardless of `dir` — a
    // deterministic order for equal values matters more than it matching
    // the sort direction of the column that just tied.
    if (cmp !== 0) return cmp * sign;
    return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
  });
  return copy;
}

// ── DOM section ─────────────────────────────────────────────────────────────
// Same overall recipe as reportes-ciudadanos.js: initSeguimiento(root, ...)
// re-renders root.innerHTML on every open (the tab is admin-only and
// re-fetches every time, same lifecycle as Stickers), with module-level
// guards so a re-open never leaks a pending fetch/timer into a fresh init.

const DASH = '—';
const DEGRADED_TITLE = 'No disponible: mostrando una copia de respaldo con datos incompletos.';
const DEGRADED_STICKERS_NOTE = 'Mostrando una copia de respaldo de los stickers: los nombres e identificaciones de los inspectores no están disponibles hasta reconectar la fuente en vivo, así que la mayoría de los stickers aparecerán como "Sin profesional identificado".';

const COLUMNS = [
  { key: 'name', label: 'Profesional' },
  { key: 'cedula', label: 'Cédula' },
  { key: 'codigo', label: 'Código' },
  { key: 'stickersFase1', label: 'Stickers F-I' },
  { key: 'stickersFase2', label: 'Stickers F-II' },
  { key: 'stickersSinFase', label: 'Stickers sin fase' },
  { key: 'surveyTotal', label: 'Evaluaciones Survey' },
  { key: 'total', label: 'Total' },
  { key: 'firstDate', label: 'Primer registro' },
  { key: 'lastDate', label: 'Último registro' },
  { key: 'activeDays', label: 'Días activos' },
  { key: 'avgPerActiveDay', label: 'Prom./día' },
];

let loadSeq = 0;
let searchDebounceTimer = null;

function formatDateCell(d) {
  return d || 'Sin dato';
}

function sectionHtml() {
  return `
    <section class="eval-section seg-section" aria-label="Seguimiento">
      <div class="section-bar">
        <h3 class="section-bar-title">Seguimiento</h3>
        <button type="button" class="btn-clear" id="seg-reset-filters" disabled>Reiniciar filtros</button>
      </div>

      <p class="sticker-note" id="seg-status" role="status" hidden></p>
      <p class="sticker-note">Cruce aproximado por nombre: Stickers usa inspector.nombre_completo, Survey usa nombre_evaluador.</p>
      <p class="sticker-note" id="seg-sinfecha-note" hidden></p>

      <div class="eval-filters" id="seg-filters">
        <div class="asignacion-search">
          <input type="search" id="seg-search" class="sticker-search-input"
            placeholder="Buscar profesional…" aria-label="Buscar profesional">
        </div>
        <div class="card-toolbar asignacion-filters">
          <label class="sticker-field asignacion-inline-field">
            <span>Desde</span>
            <input type="date" id="seg-from" aria-label="Fecha desde">
          </label>
          <label class="sticker-field asignacion-inline-field">
            <span>Hasta</span>
            <input type="date" id="seg-to" aria-label="Fecha hasta">
          </label>
          <label class="sticker-field asignacion-inline-field">
            <span>Profesional (gráfico)</span>
            <select id="seg-chart-professional" aria-label="Profesional para el gráfico"><option value="">Todos</option></select>
          </label>
          <button type="button" class="sticker-action" id="seg-download">Descargar XLSX</button>
        </div>
      </div>

      <div class="kpi-row eval-kpis" id="seg-kpis"></div>

      <div class="card eval-workspace-card">
        <div class="card-toolbar">
          <span class="eval-toolbar-title">Ritmo diario</span>
        </div>
        <div class="chart-tile" style="height:320px">
          <canvas id="seguimiento-timeline"></canvas>
        </div>
        <p class="chart-note">Eje Y en <strong>escala logarítmica</strong>: permite comparar en el mismo gráfico el ritmo diario (unidades/decenas) con el acumulado corrido (cientos), como en el gráfico "Inspecciones por día" del Panel. Las líneas punteadas son el acumulado de cada fuente, con el total rotulado sobre el último punto.</p>
      </div>

      <div class="card eval-workspace-card">
        <div class="card-toolbar">
          <span class="eval-toolbar-title">Profesionales</span>
        </div>
        <div class="table-scroll">
          <table class="tipologia-table" id="seg-table">
            <thead><tr></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </section>`;
}

/** `stickersLoaded` masks the two sticker-derived KPI tiles behind DASH
 *  while stickers haven't resolved yet (in flight, or failed after the
 *  retry) — a real 0 here would silently claim "confirmed zero stickers"
 *  when the true state is "unknown", which is a materially different and
 *  misleading message to show an admin. */
function kpisHtml(totals, stickersLoaded) {
  const fmt = (v) => (v === DASH ? DASH : Number(v || 0).toLocaleString('es-CO'));
  const tile = (label, value) => `
    <div class="kpi-tile is-neutral">
      <span class="kpi-label kpi-label-lower">${escapeHtml(label)}</span>
      <span class="kpi-value">${fmt(value)}</span>
    </div>`;
  return [
    tile('profesionales activos', totals.professionals),
    tile('stickers', stickersLoaded ? totals.stickers : DASH),
    tile('evaluaciones survey', totals.surveys),
    tile('promedio por profesional', totals.avgPerProfessional),
    tile('sin profesional identificado', totals.unassigned),
    tile('stickers sin fecha', stickersLoaded ? totals.stickersWithoutDate : DASH),
  ].join('');
}

function headerRowHtml(sortState) {
  const sortable = COLUMNS.map((c) => {
    const active = sortState.column === c.key;
    const arrow = active ? (sortState.dir === 'asc' ? ' ▲' : ' ▼') : '';
    return `<th scope="col"><button type="button" class="seg-sort-btn${active ? ' is-active' : ''}" data-seg-sort="${c.key}">${escapeHtml(c.label)}${arrow}</button></th>`;
  }).join('');
  // Not part of COLUMNS/sortRows — it's a row action, not sortable data.
  return `${sortable}<th scope="col">Acciones</th>`;
}

/** `stickersLoaded` masks the sticker-derived columns (and Total, which
 *  mixes sticker + Survey counts) behind DASH for the same reason as
 *  kpisHtml above — these fields are literally 0 whenever `stickers` is
 *  still `[]` (in flight or failed), and showing that as a real zero would
 *  misreport "no stickers" as fact instead of "unknown". The same flag (plus
 *  `isDegraded`) disables the per-row PDF report button: its "puntos
 *  recogidos — Stickers" section would otherwise ship as an empty/false
 *  list while stickers haven't resolved, or a degraded/redacted one. */
function rowHtml(r, stickersLoaded, isDegraded) {
  const caveat = r.rosterSourced > 0
    ? ` <span class="seg-caveat" title="Identidad por roster, aproximada — ${r.rosterSourced} sticker(s) sin verificar contra esta evaluación.">⚠</span>`
    : '';
  const stk = (v) => (stickersLoaded ? v : DASH);
  const reportBlocked = isDegraded || !stickersLoaded;
  const reportTitle = isDegraded ? DEGRADED_TITLE
    : !stickersLoaded ? 'Esperando a que carguen los stickers…' : 'Descargar informe PDF de este profesional';
  return `<tr>
    <td>${escapeHtml(r.name || 'Sin dato')}${caveat}</td>
    <td>${escapeHtml(r.cedula || 'Sin dato')}</td>
    <td>${escapeHtml(r.codigo || 'Sin dato')}</td>
    <td>${stk(r.stickersFase1)}</td>
    <td>${stk(r.stickersFase2)}</td>
    <td>${stk(r.stickersSinFase)}</td>
    <td>${r.surveyTotal}</td>
    <td>${stk(r.total)}</td>
    <td>${escapeHtml(formatDateCell(r.firstDate))}</td>
    <td>${escapeHtml(formatDateCell(r.lastDate))}</td>
    <td>${r.activeDays}</td>
    <td>${r.avgPerActiveDay}</td>
    <td><button type="button" class="sticker-action seg-report-btn" data-seg-report="${escapeHtml(r.key)}"${reportBlocked ? ' disabled' : ''} title="${escapeHtml(reportTitle)}">📄 Informe</button></td>
  </tr>`;
}

/** Shows a small note in the chart tile instead of a blank/broken canvas —
 *  same idea as charts.js's own (private) setChartEmpty, duplicated here
 *  rather than exported since this is the only caller outside charts.js. */
function renderChartUnavailable(message) {
  const canvas = document.getElementById('seguimiento-timeline');
  if (!canvas) return;
  canvas.style.display = 'none';
  const tile = canvas.closest('.chart-tile');
  if (!tile) return;
  let note = tile.querySelector('.chart-empty');
  if (!note) {
    note = document.createElement('p');
    note.className = 'chart-empty';
    tile.appendChild(note);
  }
  note.textContent = message;
}

function clearChartUnavailable() {
  const canvas = document.getElementById('seguimiento-timeline');
  if (!canvas) return;
  canvas.style.display = '';
  const note = canvas.closest('.chart-tile') && canvas.closest('.chart-tile').querySelector('.chart-empty');
  if (note) note.remove();
}

const fmtCount = (n) => Math.round(n || 0).toLocaleString('es-CO');

/** Built on charts.js's own baseOptions() so this chart's ticks/grid/legend/
 *  tooltip colors follow the same theme tokens as every other chart in the
 *  dashboard instead of a second, hand-rolled (and un-themed) copy.
 *
 *  Mirrors the Panel's "Inspecciones por día" chart (charts.js renderTimeSeries):
 *  daily counts + cumulative running totals on ONE logarithmic Y axis, so
 *  a handful of stickers on a given day and a running total in the hundreds
 *  can share the same chart without the daily line flattening to zero.
 *  Cumulative lines reuse the daily line's own color (dashed, no points) so
 *  a source stays visually one color across both its daily and cumulative
 *  series; the running total is labeled on the last point via the same
 *  totalDataLabelPlugin the Panel chart uses. */
function timelineChartConfig(timeline) {
  return {
    type: 'line',
    data: {
      labels: timeline.labels,
      datasets: [
        {
          label: 'Stickers', data: timeline.stickers, borderColor: COLORS.accent,
          backgroundColor: 'transparent', tension: 0.15, pointRadius: 3, borderWidth: 2,
        },
        {
          label: 'Survey', data: timeline.surveys, borderColor: COLORS.categorical[0],
          backgroundColor: 'transparent', tension: 0.15, pointRadius: 3, borderWidth: 2,
        },
        {
          label: 'Stickers (acumulado)', data: timeline.stickersCumulative, borderColor: COLORS.accent,
          backgroundColor: 'transparent', tension: 0.15, pointRadius: 0, borderWidth: 2, borderDash: [6, 4],
          _totalLabel: fmtCount(timeline.stickersCumulative[timeline.stickersCumulative.length - 1]),
        },
        {
          label: 'Survey (acumulado)', data: timeline.surveysCumulative, borderColor: COLORS.categorical[0],
          backgroundColor: 'transparent', tension: 0.15, pointRadius: 0, borderWidth: 2, borderDash: [6, 4],
          _totalLabel: fmtCount(timeline.surveysCumulative[timeline.surveysCumulative.length - 1]),
        },
      ],
    },
    plugins: [totalDataLabelPlugin],
    options: baseOptions({
      interaction: { mode: 'index', intersect: false },
      // Logarithmic axis, same reasoning as the Panel chart: lets a daily
      // count of a handful and a cumulative total in the hundreds share one
      // Y axis legibly. Chart.js's log scale can't plot a literal 0 (log(0)
      // is undefined), so a day with zero stickers/survey records simply
      // has no point for that series on that day — the line resumes on the
      // next non-zero day, same behavior the Panel chart already has.
      scales: { y: { type: 'logarithmic' } },
      plugins: { legend: { display: true }, tooltip: { mode: 'index', intersect: false } },
    }),
  };
}

// The timeline chart lives in charts.js's shared Chart.js registry, keyed
// only by canvas id. main.js's own 'themechange' listener calls charts.js's
// resetCharts() (destroy every registered chart) so Chart.js can re-bake the
// new theme's CSS-variable colors at construction time — but that leaves
// 'seguimiento-timeline' destroyed with nothing left to rebuild it unless
// this module reacts to the same event. `activeRenderChart` always points at
// the MOST RECENT initSeguimiento() call's own renderChart closure (reset on
// every open, same idea as loadSeq/searchDebounceTimer above) so a stale
// closure from a previous open never fires after a fresh one has taken over.
//
// Deferred via setTimeout(...,0): this listener is registered at module load
// (when main.js imports this file), which happens BEFORE main.js's own
// document.addEventListener('themechange', ...) call executes further down
// its file — so without the deferral, THIS listener would run first, rebuild
// the chart, and then main.js's resetCharts() would immediately destroy it
// again a moment later, leaving nothing on screen. Pushing the rebuild to a
// macrotask guarantees it runs after every synchronous 'themechange'
// listener (main.js's resetCharts() included) regardless of registration
// order. Guarded (like reportes-ciudadanos.js:404 / evaluaciones.js:477) so
// the pure-logic self-check can import this module under Node.
//
// `activeRenderChart` is set (inside initSeguimiento, below) to a wrapper
// that first checks `root.hidden` — the tab may have been opened once, then
// switched away from (root stays in the DOM, just hidden, never rebuilt)
// while a theme change fires later; rebuilding a chart nobody can see would
// be wasted work at best, and there is no guarantee the hidden container's
// layout is still meaningful for Chart.js to measure.
let activeRenderChart = null;
if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => {
    if (!activeRenderChart) return;
    setTimeout(() => {
      try {
        activeRenderChart();
      } catch (err) {
        console.warn('seguimiento: fallo al re-renderizar el gráfico tras cambio de tema', err);
      }
    }, 0);
  });
}

// Survives across tab re-opens/store refreshes so main.js's onStoreChange
// can push fresh Survey records into the CURRENTLY ACTIVE init without
// tearing it down (see updateSeguimientoRecords below) — re-running
// initSeguimiento on every store notify (~2 times per 15-min auto-refresh
// cycle: setStickerIds, applyFilters) would wipe the
// user's filters/sort/search AND re-fetch stickers from the network every
// time, for no reason (the sticker data itself didn't change).
let activeUpdateRecords = null;

/** Pushes a fresh Survey records array into the currently open Seguimiento
 *  tab (if any) without touching its DOM structure, filters, sort state, or
 *  in-flight/already-loaded sticker data — a state-preserving update, not a
 *  re-init. No-op when the tab has never been opened this session. */
export function updateSeguimientoRecords(records) {
  if (activeUpdateRecords) activeUpdateRecords(records);
}

/** One-shot retry over a transient network blip — same recipe as stickers.js's
 *  own fetchEvaluaciones wrapper (used for the Evaluaciones section's fetch):
 *  a cold serverless connection or a dropped request shouldn't surface as a
 *  hard error when a second attempt half a second later would have worked. */
async function fetchStickersWithRetry(getToken) {
  try {
    return await fetchEvaluacionesOnce(getToken, STICKERS_ENDPOINT);
  } catch (err) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    return await fetchEvaluacionesOnce(getToken, STICKERS_ENDPOINT);
  }
}

/** Orchestrator for the per-row "📄 Informe" button: gathers this
 *  professional's raw points (professionalRecords, pure), builds the doc
 *  definition (buildProfessionalReportDocDefinition, pure) + lazy-loads
 *  pdfmake (report.js — the same instance every other PDF report in the app
 *  uses, cached after the first call) and triggers the download. Mirrors
 *  report.js's own generarInformePdf/generarInformeCandidato shape. */
async function descargarInformeProfesional(row, { stickers, surveys }) {
  const points = professionalRecords(row, { stickers, surveys });
  const def = buildProfessionalReportDocDefinition(row, points);
  const pdfMake = await loadPdfmake();
  const nameSlug = String(row.name || 'profesional').trim().replace(/[^\w-]+/g, '_') || 'profesional';
  const filename = `informe_seguimiento_${nameSlug}_${downloadStamp().slug}.pdf`;
  pdfMake.createPdf(def).download(filename);
}

/** initSeguimiento(root, { getToken, records }) — renders the tab and wires
 *  its actions. `records` is store.records (Survey), passed in by main.js so
 *  this module never imports data.js directly (would pull in the Firebase
 *  chain and break the Node self-check). Stickers are fetched fresh on every
 *  open via fetchEvaluacionesOnce, same lifecycle as the Stickers tab. */
export function initSeguimiento(root, { getToken, records }) {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = null;
  root.innerHTML = sectionHtml();

  const $ = (id) => root.querySelector(`#${id}`);
  const kpisEl = $('seg-kpis');
  const statusEl = $('seg-status');
  const sinFechaNoteEl = $('seg-sinfecha-note');
  const searchEl = $('seg-search');
  const fromEl = $('seg-from');
  const toEl = $('seg-to');
  const chartSelectEl = $('seg-chart-professional');
  const resetFiltersBtn = $('seg-reset-filters');
  const downloadBtn = $('seg-download');
  const tableEl = $('seg-table');
  const theadRow = tableEl.querySelector('thead tr');
  const tbody = tableEl.querySelector('tbody');

  let stickers = [];
  // `let`, not `const`: updateSeguimientoRecords() (module-level export,
  // called by main.js's onStoreChange) reassigns this in place on a store
  // refresh instead of tearing down and re-initializing the whole tab — see
  // activeUpdateRecords below.
  let surveys = Array.isArray(records) ? records : [];
  let sortState = { column: 'total', dir: 'desc' };
  let currentRows = [];
  // Search-filtered rows, hoisted so the XLSX export and the empty-table
  // guard both read the SAME set the user is actually looking at — the
  // export used to silently ignore the search box and always dump every
  // row (same convention as reportes-ciudadanos.js's `visibles`).
  let visibleRows = [];
  // Distinguishes "confirmed zero stickers" from "stickers haven't
  // resolved yet / failed" — see kpisHtml/rowHtml's DASH masking above.
  // `stickers` itself stays `[]` in both the "loading" and "failed" cases,
  // so this flag (not the array) is what the UI reads to tell them apart.
  let stickersLoaded = false;
  let isDegraded = false;
  let stickerFetchErrorMessage = '';

  function currentFilters() {
    return { from: fromEl.value || null, to: toEl.value || null };
  }

  function updateDownloadAvailability() {
    // Blocked while degraded (identities are redacted, see fetchStickers
    // below) OR before stickers have resolved at all — exporting mid-flight
    // would silently ship a file whose sticker columns are all "unknown".
    const blocked = isDegraded || !stickersLoaded;
    downloadBtn.disabled = blocked;
    downloadBtn.title = isDegraded ? DEGRADED_TITLE : (!stickersLoaded ? 'Esperando a que carguen los stickers…' : '');
  }

  function renderStatusBanner() {
    // A single short-lived status line (loading / degraded / fetch error),
    // its own dedicated <p role="status"> below the header — NOT the old
    // eval-toolbar-meta span, which is sized for a one-line "actualizado…"
    // caption and overflows the DEGRADED_STICKERS_NOTE's ~250 characters.
    const text = isDegraded ? DEGRADED_STICKERS_NOTE
      : stickerFetchErrorMessage ? stickerFetchErrorMessage
        : !stickersLoaded ? 'Cargando stickers…'
          : '';
    statusEl.hidden = !text;
    statusEl.textContent = text;
  }

  function renderChartOptions(rows) {
    const prev = chartSelectEl.value;
    chartSelectEl.innerHTML = '<option value="">Todos</option>'
      + rows.map((r) => `<option value="${escapeHtml(r.key)}">${escapeHtml(r.name || 'Sin dato')}</option>`).join('');
    chartSelectEl.value = rows.some((r) => r.key === prev) ? prev : '';
  }

  function renderTable(rows) {
    const q = searchEl.value ? normalize(searchEl.value) : '';
    visibleRows = q ? rows.filter((r) => normalize(r.name || '').includes(q)) : rows;
    const sorted = sortRows(visibleRows, sortState.column, sortState.dir);
    theadRow.innerHTML = headerRowHtml(sortState);
    tbody.innerHTML = sorted.length
      ? sorted.map((r) => rowHtml(r, stickersLoaded, isDegraded)).join('')
      : `<tr><td colspan="${COLUMNS.length + 1}" class="eval-empty">Ningún profesional coincide con los filtros aplicados.</td></tr>`;

    // Every filter control (search input, Desde/Hasta) re-renders through
    // render() -> renderTable() (search's own debounce calls renderTable
    // directly) — updating the reset button's state in this one shared spot
    // keeps it in sync without a parallel check that could drift.
    const active = hasActiveSegFilters({ search: searchEl.value, from: fromEl.value || null, to: toEl.value || null });
    resetFiltersBtn.disabled = !active;
    resetFiltersBtn.classList.toggle('is-filter-active', active);
  }

  function renderChart() {
    // Chart.js loads from a CDN (see index.html) — if it failed to load (or
    // hasn't yet), `Chart` is simply undefined here; upsertChart() would
    // throw a ReferenceError trying to `new Chart(...)`. render() calls this
    // synchronously (both from initSeguimiento's own first paint and from
    // every filter/search/sort interaction afterward), and NOTHING between
    // here and switchView()/onStoreChange() catches that throw — main.js has
    // no try/catch around either call site, so an uncaught error here would
    // abort the whole init (freshness/status banner, sticker fetch, filter
    // wiring) or, worse, escape into data.js's notify() loop and take other
    // subscribers down with it.
    if (typeof Chart === 'undefined') {
      renderChartUnavailable('Gráfico no disponible (no se pudo cargar Chart.js).');
      return;
    }
    const timeline = buildTimeline({
      stickers, surveys, ...currentFilters(), professionalKey: chartSelectEl.value || null,
    });
    try {
      // recreate: true — root.innerHTML is replaced on every open (see the
      // top of this function), which orphans the PREVIOUS open's <canvas>
      // even though charts.js's registry still holds a Chart instance bound
      // to it. Without recreate, upsertChart() would just call that stale
      // instance's update() — which repaints the detached old canvas, not
      // the new one actually on screen, so the chart stayed blank from the
      // second open on.
      upsertChart('seguimiento-timeline', timelineChartConfig(timeline), { recreate: true });
      clearChartUnavailable();
    } catch (err) {
      console.warn('seguimiento: fallo al renderizar el gráfico de ritmo diario', err);
      renderChartUnavailable('Gráfico no disponible (error al renderizar).');
    }
  }
  // Wrapped, not the bare closure: `root` stays in the DOM (just hidden)
  // after the user switches away from this tab, and a later 'themechange'
  // must not rebuild a chart nobody can see (see the module-level listener
  // above) — this is the SAME root main.js's switchView() toggles `.hidden`
  // on, so checking it here needs no separate visibility bookkeeping.
  activeRenderChart = () => {
    if (root.hidden) return;
    renderChart();
  };
  // Reassigned on every initSeguimiento() call, same idea as
  // activeRenderChart above — main.js's onStoreChange always targets
  // whichever init is CURRENTLY open.
  activeUpdateRecords = (newRecords) => {
    surveys = Array.isArray(newRecords) ? newRecords : [];
    render();
  };

  function render() {
    // `unassigned` (the Sin-profesional bucket, broken down by source) is
    // not destructured here — the UI only ever surfaces it as one combined
    // KPI tile (totals.unassigned), rendered by kpisHtml below.
    const { rows, stickersWithoutDate, totals } = buildProfessionalRows({
      stickers, surveys, ...currentFilters(),
    });
    currentRows = rows;
    kpisEl.innerHTML = kpisHtml(totals, stickersLoaded);
    sinFechaNoteEl.hidden = !stickersLoaded || stickersWithoutDate === 0;
    sinFechaNoteEl.textContent = (stickersLoaded && stickersWithoutDate)
      ? `${stickersWithoutDate.toLocaleString('es-CO')} stickers sin fecha (la API de atencionsismo aún no expone la fecha por registro); se cuentan en los totales pero no en la curva temporal.`
      : '';
    renderChartOptions(rows);
    renderTable(rows);
    renderChart();
  }

  theadRow.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-seg-sort]');
    if (!btn) return;
    const col = btn.dataset.segSort;
    sortState = sortState.column === col
      ? { column: col, dir: sortState.dir === 'asc' ? 'desc' : 'asc' }
      : { column: col, dir: 'desc' };
    renderTable(currentRows);
  });

  // Delegated on tbody (rebuilt on every renderTable call) rather than one
  // listener per row button — same reasoning as table.js's own row clicks.
  tbody.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-seg-report]');
    if (!btn || btn.disabled) return;
    const row = currentRows.find((r) => r.key === btn.dataset.segReport);
    if (!row) return;
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Generando…';
    try {
      await descargarInformeProfesional(row, { stickers, surveys });
    } catch (err) {
      console.error('seguimiento: fallo al generar el informe PDF', err);
      showToast('No se pudo generar el informe PDF.', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  });

  searchEl.addEventListener('input', () => {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => renderTable(currentRows), 250);
  });

  fromEl.addEventListener('change', render);
  toEl.addEventListener('change', render);
  chartSelectEl.addEventListener('change', renderChart);

  // "Reiniciar filtros": this tab's only real data-narrowing filters are the
  // professional search box and the Desde/Hasta date range — both read
  // straight off the DOM (there's no in-memory `filters` object to reset,
  // unlike evaluaciones.js). `seg-chart-professional` is deliberately left
  // untouched: it never narrows which professionals appear in the table
  // (renderTable filters only by search), it only picks which one line the
  // Ritmo diario chart highlights — a display-mode selection, the same
  // category as "colorear por", not a filter. Sort order is left alone too.
  resetFiltersBtn.addEventListener('click', () => {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = null;
    searchEl.value = '';
    fromEl.value = '';
    toEl.value = '';
    render();
  });

  downloadBtn.addEventListener('click', async () => {
    // Belt-and-suspenders alongside the disabled attribute (updateDownload
    // Availability) — same defense-in-depth evaluaciones.js uses for its own
    // degraded-copy export block.
    if (isDegraded) { showToast('No se puede exportar: mostrando una copia de respaldo con datos incompletos.', 'error'); return; }
    if (!stickersLoaded) { showToast('Esperá a que carguen los stickers antes de exportar.', 'error'); return; }
    if (!visibleRows.length) { showToast('No hay profesionales para exportar.', 'error'); return; }
    downloadBtn.disabled = true;
    try {
      let XLSX;
      try { XLSX = await loadXlsx(); } catch { showToast('No se pudo cargar el generador de Excel.', 'error'); return; }
      const rows = sortRows(visibleRows, sortState.column, sortState.dir).map((r) => ({
        profesional: r.name, cedula: r.cedula, codigo: r.codigo, entidad: r.entidad,
        stickers_fase1: r.stickersFase1, stickers_fase2: r.stickersFase2, stickers_sin_fase: r.stickersSinFase,
        stickers_total: r.stickersTotal, evaluaciones_survey: r.surveyTotal, total: r.total,
        primer_registro: r.firstDate || '', ultimo_registro: r.lastDate || '',
        dias_activos: r.activeDays, promedio_por_dia: r.avgPerActiveDay,
        stickers_por_roster: r.rosterSourced,
      }));
      const { legible, slug } = downloadStamp();
      const ws = XLSX.utils.aoa_to_sheet([
        ['Seguimiento — profesionales'],
        ['Fecha de generación:', legible],
        ['Registros:', rows.length],
        [],
      ]);
      XLSX.utils.sheet_add_json(ws, rows, { origin: 'A5' });
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, 'seguimiento');
      XLSX.writeFile(wb, `seguimiento_${slug}.xlsx`);
      showToast('Archivo generado.');
    } finally {
      updateDownloadAvailability();
    }
  });

  // Survey renders immediately and synchronously (records are already in
  // memory, no fetch needed) — the table header, KPIs and chart are never
  // headerless/blank while stickers are still in flight; sticker-derived
  // figures simply start out DASH-masked (see kpisHtml/rowHtml) until
  // loadStickers() below settles.
  updateDownloadAvailability();
  render();
  renderStatusBanner();

  (async () => {
    const seq = ++loadSeq;
    try {
      const { evaluaciones, degraded } = await fetchStickersWithRetry(getToken);
      if (seq !== loadSeq) return;
      stickers = evaluaciones;
      stickersLoaded = true;
      isDegraded = degraded;
      stickerFetchErrorMessage = '';
    } catch (err) {
      if (seq !== loadSeq) return;
      // Survey half stays fully rendered (see the synchronous render() call
      // above) — a sticker fetch failure only degrades the sticker-derived
      // figures to DASH via renderStatusBanner/render() below, it never
      // blanks the whole tab.
      stickersLoaded = false;
      isDegraded = false;
      stickerFetchErrorMessage = `Stickers no disponibles: ${err && err.message ? err.message : String(err)}`;
    }
    renderStatusBanner();
    updateDownloadAvailability();
    render();
  })();
}
