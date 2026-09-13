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
import { COLORS, escapeHtml, normalize, loadXlsx, downloadStamp, showToast, faseKeyDe, debounce } from './utils.js';
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

// ── Zona horaria: America/Bogota (UTC-5, sin horario de verano) ────────────
// Regla única del plan (Zona horaria): TODO lo que el usuario ve o se agrupa
// por día/hora (KPIs, timeline, filtros Desde/Hasta, columnas de hora, "hoy"
// para días-desde-primera-actividad y la ventana de barrios activos) usa un
// offset FIJO -05:00 sobre el instante UTC-aware que trae la API (`fecha`,
// siempre "+00:00"), nunca la zona del navegador/máquina que ejecuta el
// código -- por eso cada función de esta sección trabaja con aritmética
// entera sobre milisegundos (Date.UTC / getUTC*) o con regex puro sobre
// texto, y JAMÁS con `new Date(unaTemplateString)` ni con los getters
// LOCALES de Date (getFullYear/getMonth/getDate/getHours/getMinutes) -- esos
// SÍ dependen de la zona del proceso (TZ), que es exactamente lo que este
// módulo no puede permitirse (ver seguimiento-fechas.test.mjs, que corre
// bajo TZ=UTC, TZ=America/Bogota y TZ=Europe/Madrid y exige el mismo
// resultado en los tres).
//
// D6 (decisión tomada, plan §Decisiones): dateOnly usaba antes la zona LOCAL
// de la máquina (`new Date(raw)` + getFullYear/getMonth/getDate) -- un admin
// en una laptop con otro huso horario veía días distintos para el MISMO
// sticker que uno en Cali. Ahora delega en bogotaParts(), offset fijo.

/** Offset fijo de Bogotá respecto a UTC, en minutos (America/Bogota no tiene
 *  horario de verano). Negativo: Bogotá va 5 horas DETRÁS de UTC. */
export const BOGOTA_UTC_OFFSET_MIN = -300;

const BARE_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const ISO_OFFSET_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/;
const NAIVE_DATETIME_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;

/** Whether y-mo-d is a real calendar date (rejects e.g. 2026-02-30, month
 *  13, day 0) — pure integer arithmetic, no Date involved at all. */
function isValidYMD(y, mo, d) {
  if (!Number.isInteger(y) || mo < 1 || mo > 12 || d < 1) return false;
  const leap = (y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0));
  const daysInMonth = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return d <= daysInMonth[mo - 1];
}

/** `dateStr` (YYYY-MM-DD) shifted by `deltaDays` (may be negative), via
 *  Date.UTC + getUTC* only — never a local getter, so the result is
 *  identical regardless of the running process' own timezone. Returns
 *  `dateStr` unchanged if it doesn't parse (defensive; callers only ever
 *  pass an already-validated YYYY-MM-DD here). */
function shiftDateStr(dateStr, deltaDays) {
  const m = BARE_DATE_RE.exec(dateStr);
  if (!m) return dateStr;
  const ms = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) + deltaDays * 86400000;
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
}

/** Whole days between two YYYY-MM-DD strings (`to` minus `from`), or null
 *  when either is malformed. Pure Date.UTC arithmetic, timezone-independent. */
function daysBetween(fromStr, toStr) {
  const a = BARE_DATE_RE.exec(fromStr || '');
  const b = BARE_DATE_RE.exec(toStr || '');
  if (!a || !b) return null;
  const msA = Date.UTC(Number(a[1]), Number(a[2]) - 1, Number(a[3]));
  const msB = Date.UTC(Number(b[1]), Number(b[2]) - 1, Number(b[3]));
  return Math.round((msB - msA) / 86400000);
}

/** The Bogotá calendar day (+ time-of-day, in minutes since midnight, when
 *  the value carries one) of a value that may be:
 *   - a tz-aware ISO timestamp (`Z` or an explicit `+HH:MM`/`-HH:MM` offset)
 *     — the atencionsismo API's/Firestore's own `fecha`, always "+00:00" per
 *     the data contract, but any explicit offset is honored;
 *   - a bare `YYYY-MM-DD` (Survey's `fecha_inspeccion`) — taken literally,
 *     `minutes: null`, since it never carried a time/offset to convert;
 *   - a naive `YYYY-MM-DDTHH:MM[:SS]` with NO offset (Survey's `fecha_hora`,
 *     already Bogotá local per the data contract) — parsed as TEXT via
 *     regex, never `new Date()` on it (handing a naive string to `new
 *     Date()` parses it in the RUNNING PROCESS' OWN timezone by spec — the
 *     exact bug this function exists to avoid).
 *  Anything else (malformed, empty, non-string-coercible, an invalid
 *  calendar date/time, an out-of-range hour/minute) returns `null`. Never
 *  throws. */
export function bogotaParts(value) {
  if (value === null || value === undefined) return null;
  const raw = String(value).trim();
  if (!raw) return null;

  const bare = BARE_DATE_RE.exec(raw);
  if (bare) {
    const y = Number(bare[1]); const mo = Number(bare[2]); const d = Number(bare[3]);
    if (!isValidYMD(y, mo, d)) return null;
    return { date: raw, minutes: null };
  }

  const offsetMatch = ISO_OFFSET_RE.exec(raw);
  if (offsetMatch) {
    const [, yStr, moStr, dStr, hStr, minStr, secStr, offStr] = offsetMatch;
    const y = Number(yStr); const mo = Number(moStr); const d = Number(dStr);
    const h = Number(hStr); const mi = Number(minStr); const sec = secStr ? Number(secStr) : 0;
    if (!isValidYMD(y, mo, d) || h > 23 || mi > 59 || sec > 59) return null;
    let offsetMin = 0;
    if (offStr !== 'Z') {
      const sign = offStr[0] === '-' ? -1 : 1;
      const digits = offStr.slice(1).replace(':', '');
      offsetMin = sign * (Number(digits.slice(0, 2)) * 60 + Number(digits.slice(2, 4)));
    }
    // Instante UTC real (ms) del valor, sin depender de la zona del proceso.
    const utcMs = Date.UTC(y, mo - 1, d, h, mi, sec) - offsetMin * 60000;
    const bogotaMs = utcMs + BOGOTA_UTC_OFFSET_MIN * 60000;
    const bd = new Date(bogotaMs);
    return {
      date: `${bd.getUTCFullYear()}-${String(bd.getUTCMonth() + 1).padStart(2, '0')}-${String(bd.getUTCDate()).padStart(2, '0')}`,
      minutes: bd.getUTCHours() * 60 + bd.getUTCMinutes(),
    };
  }

  const naive = NAIVE_DATETIME_RE.exec(raw);
  if (naive) {
    const [, yStr, moStr, dStr, hStr, minStr] = naive;
    const y = Number(yStr); const mo = Number(moStr); const d = Number(dStr);
    const h = Number(hStr); const mi = Number(minStr);
    if (!isValidYMD(y, mo, d) || h > 23 || mi > 59) return null;
    return { date: `${yStr}-${moStr}-${dStr}`, minutes: h * 60 + mi };
  }

  return null;
}

/** Today's date (YYYY-MM-DD) in America/Bogota, from `now` (epoch ms,
 *  default the real clock). Date.UTC-safe arithmetic only, so the RUNNING
 *  PROCESS' timezone never enters the computation — only `now` and the
 *  fixed Bogotá offset do. */
export function bogotaToday(now = Date.now()) {
  const d = new Date(now + BOGOTA_UTC_OFFSET_MIN * 60000);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
}

/** Date-only (YYYY-MM-DD) part of a value, in America/Bogota (D6: fixed
 *  -05:00 offset — see the "Zona horaria" block comment above for why this
 *  replaced the old browser/machine-local-timezone conversion). Thin
 *  wrapper over bogotaParts() for the many callers below that only need the
 *  day, not the time-of-day. */
function dateOnly(value) {
  const parts = bogotaParts(value);
  return parts ? parts.date : null;
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

// ── Identidad: cédula primero, nombre como respaldo (D7) ────────────────────
// Único resolver de identidad para TODO el módulo (invariante "un solo
// resolver de identidad" del plan): buildProfessionalRows, buildTimeline,
// professionalRecords, buildTemporalMetrics y buildBarriosActivos llaman
// TODOS a professionalKeyOf — ninguno vuelve a derivar normalizeName por su
// cuenta. `row.key` conserva el nombre de campo pero ahora es el valor de
// professionalKeyOf ('ced:...' | 'nom:...'), nunca un normalizeName() a
// secas — cambio de contrato deliberado, ver los tests actualizados.

/** Digits-only join key for a cédula — mirrors the backend's `cedula_key`
 *  (stickers_atencionsismo.py:220) EXACTLY: strips every non-digit
 *  character (dots, spaces, dashes, …) so "1.234.567", 1234567 (number) and
 *  " 1234567 " all resolve to the same key. Decision (leading zeros): kept
 *  VERBATIM, never stripped — same as the backend, which only does
 *  `re.sub(r"\D", "", ...)` and nothing else; a cédula that legitimately
 *  starts with "0" must not collide with one that doesn't. Returns "" when
 *  nothing digit-like remains (blank/non-numeric input, e.g. "CC" typed into
 *  the cédula field by mistake) — callers treat "" as "no cédula". */
export function cedulaKey(raw) {
  return String(raw === null || raw === undefined ? '' : raw).replace(/\D/g, '');
}

const EMPTY_IDENTITY = Object.freeze({ eligibleCedulas: new Set(), nameToCedula: new Map() });

/** Whether `record` is a Survey record (has `nombre_evaluador`, even if
 *  blank) rather than a sticker (has `inspector`/`inspector_fuente`) — the
 *  two source shapes never carry a cédula the same way, so every caller
 *  needs to tell them apart before resolving a key. */
function isSurveyRecord(record) {
  return Object.prototype.hasOwnProperty.call(record, 'nombre_evaluador');
}

/** THE single identity resolver (see the module note above): a sticker or
 *  Survey record -> its professional key, `'ced:<digits>'` when a cédula
 *  resolves it, `'nom:<normalizedName>'` when only a name does, or `''` when
 *  neither resolves (no name at all — the "sin profesional identificado"
 *  case, unchanged from before).
 *
 *  Resolution order: (1) the record's OWN cédula, but ONLY when that cédula
 *  is "eligible" (`identity.eligibleCedulas` — has appeared at least once
 *  with `inspector_fuente !== 'roster'` ANYWHERE in the sticker set; a
 *  cédula seen ONLY via the roster is never a merge key — misattribution
 *  risk, see stickers_atencionsismo.py:20-27); (2) failing that, the
 *  record's normalized name, unified to a cédula ONLY when that name maps to
 *  EXACTLY ONE eligible cédula across the whole sticker set
 *  (`identity.nameToCedula`, built by buildIdentityIndex) — this is what
 *  lets a Survey record (which never carries a cédula at all) attach to the
 *  same row as a sticker professional; (3) otherwise a plain name key. A
 *  name that maps to >=2 eligible cédulas (homonyms with different
 *  identities) is NEVER unified — each cédula keeps its own row, and a
 *  name-only record for that name falls into its own separate `nom:` bucket
 *  rather than guessing which person it belongs to ("nombre con dos
 *  cédulas -> 3 filas"). `identity` defaults to "no cédula ever eligible",
 *  i.e. every record resolves via name alone — a safe, pure default that
 *  needs no prior buildIdentityIndex() call for a single-source test. */
export function professionalKeyOf(record, identity = EMPTY_IDENTITY) {
  if (!record) return '';
  const idx = identity || EMPTY_IDENTITY;
  if (isSurveyRecord(record)) {
    const nameKey = normalizeName(record.nombre_evaluador || '');
    if (!nameKey) return '';
    if (idx.nameToCedula.has(nameKey)) return `ced:${idx.nameToCedula.get(nameKey)}`;
    return `nom:${nameKey}`;
  }
  const insp = record.inspector || {};
  const ownCedula = cedulaKey(insp.identificacion);
  if (ownCedula && idx.eligibleCedulas.has(ownCedula)) return `ced:${ownCedula}`;
  const nameKey = normalizeName(insp.nombre_completo || '');
  if (nameKey && idx.nameToCedula.has(nameKey)) return `ced:${idx.nameToCedula.get(nameKey)}`;
  return nameKey ? `nom:${nameKey}` : '';
}

/** Builds the identity index every other pure function in this module
 *  resolves through (via professionalKeyOf): which cédulas are eligible
 *  merge keys, which normalized names unify to exactly one of them, AND the
 *  per-key `profiles` (display name via "most frequent raw spelling wins",
 *  same as before; cédula/código/entidad/np/tarjetaProfesional/celular/
 *  correo via "first non-blank wins"; `ambiguous` for a homonym split) that
 *  buildProfessionalRows reads instead of re-deriving them inline.
 *
 *  Built from the FULL, unfiltered {stickers, surveys} — identity is a
 *  property of the whole dataset, never of a date-filtered slice (a `from`/
 *  `to` range must not change WHO a cédula/name resolves to, only which of
 *  their records count toward a KPI). */
export function buildIdentityIndex({ stickers = [], surveys = [] } = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];

  const eligibleCedulas = new Set();
  const nameCedulaCandidates = new Map(); // normalizedName -> Set<cedula>
  for (const s of stickerList) {
    if (!s) continue;
    const insp = s.inspector || {};
    const ced = cedulaKey(insp.identificacion);
    if (!ced) continue;
    if (s.inspector_fuente !== 'roster') eligibleCedulas.add(ced);
    const nameKey = normalizeName(insp.nombre_completo || '');
    if (!nameKey) continue;
    if (!nameCedulaCandidates.has(nameKey)) nameCedulaCandidates.set(nameKey, new Set());
    nameCedulaCandidates.get(nameKey).add(ced);
  }

  const nameToCedula = new Map();
  const ambiguousNames = new Set();
  for (const [nameKey, cedSet] of nameCedulaCandidates) {
    const eligible = [...cedSet].filter((c) => eligibleCedulas.has(c));
    if (eligible.length === 1) nameToCedula.set(nameKey, eligible[0]);
    else if (eligible.length >= 2) ambiguousNames.add(nameKey);
  }

  const resolverCore = { eligibleCedulas, nameToCedula };
  const keyForSticker = (record) => professionalKeyOf(record, resolverCore);
  const keyForSurvey = (record) => professionalKeyOf(record, resolverCore);

  const profiles = new Map();
  function ensureProfile(key) {
    let p = profiles.get(key);
    if (!p) {
      p = {
        nameCounts: new Map(), cedula: '', codigo: '', entidad: '',
        np: '', tarjetaProfesional: '', celular: '', correo: '',
        ambiguous: false,
      };
      profiles.set(key, p);
    }
    return p;
  }

  for (const s of stickerList) {
    if (!s) continue;
    const insp = s.inspector || {};
    const rawName = insp.nombre_completo || '';
    const key = keyForSticker(s);
    if (!key) continue;
    const p = ensureProfile(key);
    if (rawName) p.nameCounts.set(rawName, (p.nameCounts.get(rawName) || 0) + 1);
    if (!p.cedula && insp.identificacion) p.cedula = insp.identificacion;
    if (!p.codigo && insp.codigo) p.codigo = insp.codigo;
    if (!p.entidad && insp.entidad) p.entidad = insp.entidad;
    if (!p.np && insp.np) p.np = insp.np;
    if (!p.tarjetaProfesional && insp.tarjeta_profesional) p.tarjetaProfesional = insp.tarjeta_profesional;
    if (!p.celular && insp.num_telefono) p.celular = insp.num_telefono;
    if (!p.correo && insp.correo_contacto) p.correo = insp.correo_contacto;
    const nameKey = normalizeName(rawName);
    if (nameKey && ambiguousNames.has(nameKey)) p.ambiguous = true;
  }
  for (const sv of surveyList) {
    if (!sv) continue;
    const rawName = sv.nombre_evaluador || '';
    const key = keyForSurvey(sv);
    if (!key) continue;
    const p = ensureProfile(key);
    if (rawName) p.nameCounts.set(rawName, (p.nameCounts.get(rawName) || 0) + 1);
    if (!p.entidad && sv.entidad) p.entidad = sv.entidad;
    const nameKey = normalizeName(rawName);
    if (nameKey && ambiguousNames.has(nameKey)) p.ambiguous = true;
  }

  for (const p of profiles.values()) {
    let bestName = '';
    let bestCount = -1;
    for (const [name, count] of p.nameCounts) {
      if (count > bestCount) { bestCount = count; bestName = name; }
    }
    p.name = bestName;
  }

  return { eligibleCedulas, nameToCedula, ambiguousNames, profiles, keyForSticker, keyForSurvey };
}

/** Per-professional rows joining stickers + Survey by identity
 *  (professionalKeyOf — cédula first, name as fallback; see the module note
 *  above), plus the "Sin profesional identificado" bucket and the
 *  sticker-without-fecha count the UI surfaces as its own KPI/note.
 *  `from`/`to` (YYYY-MM-DD, either may be null) filter both sources — Survey
 *  via fecha_inspeccion, stickers via fecha's Bogotá date part; see
 *  stickerIncluded/surveyIncluded above for the null-date edge case.
 *  `identity` defaults to a fresh buildIdentityIndex() over the SAME
 *  {stickers, surveys} (correct — identity must come from the whole
 *  dataset, which these already are); pass an explicitly pre-built one when
 *  calling this repeatedly so identity isn't recomputed every time (W6).
 *  `today` (YYYY-MM-DD, Bogotá) defaults to bogotaToday() and flows into
 *  buildBarriosActivos for the per-row "barrios activos (7 d)" derivation. */
export function buildProfessionalRows({
  stickers, surveys, from = null, to = null, identity, today,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: surveyList });
  const todayStr = today || bogotaToday();

  const rowsByKey = new Map();
  let unassignedStickers = 0;
  let unassignedSurveys = 0;
  let stickersWithoutDate = 0;

  function ensureRow(key) {
    let row = rowsByKey.get(key);
    if (!row) {
      row = {
        key,
        stickersFase1: 0, stickersFase2: 0, stickersTotal: 0,
        surveyTotal: 0, rosterSourced: 0,
        dates: [],
      };
      rowsByKey.set(key, row);
    }
    return row;
  }

  for (const s of stickerList) {
    if (!s) continue;
    // Same rule as evaluaciones.js's Stickers tab (#29): a record whose Fase
    // never resolves (faseKeyDe -> 'SIN_DATO') is dropped before it can enter
    // any KPI, the "sin profesional identificado"/"sin fecha" buckets, the
    // timeline, or a per-professional report — not kept in its own "sin
    // fase" bucket the way it used to be, which let it silently inflate
    // stickersTotal even though it can't be attributed to a real Fase I/II
    // inspection. Checked first, before the date/identity filters below, so
    // it never counts toward those either.
    if (faseKeyDe(s) === 'SIN_DATO') continue;
    if (!stickerIncluded(s, from, to)) continue;
    const key = professionalKeyOf(s, idx);
    const dateVal = dateOnly(s.fecha);
    if (dateVal === null) stickersWithoutDate += 1;
    if (!key) { unassignedStickers += 1; continue; }
    const row = ensureRow(key);
    // Shared Fase I/II rule (utils.js's faseKeyDe) instead of a second,
    // independent `fase === 1/2` switch: for an atencionsismo sticker whose
    // `fase` isn't 1 or 2, this falls back to the inspector's NP category
    // (P3+ = Fase II) exactly like evaluaciones.js does — a second inline
    // rule here would silently drift from that one over time. SIN_DATO is
    // filtered above, so this only ever resolves FASE_I/FASE_II here.
    const faseKey = faseKeyDe(s);
    if (faseKey === 'FASE_I') row.stickersFase1 += 1;
    else row.stickersFase2 += 1;
    row.stickersTotal += 1;
    if (s.inspector_fuente === 'roster') row.rosterSourced += 1;
    if (dateVal) row.dates.push(dateVal);
  }

  for (const sv of surveyList) {
    if (!sv) continue;
    if (!surveyIncluded(sv, from, to)) continue;
    const key = professionalKeyOf(sv, idx);
    if (!key) { unassignedSurveys += 1; continue; }
    const row = ensureRow(key);
    row.surveyTotal += 1;
    const dateVal = dateOnly(sv.fecha_inspeccion);
    if (dateVal) row.dates.push(dateVal);
  }

  const rows = [...rowsByKey.values()].map((row) => {
    const profile = idx.profiles.get(row.key) || {};
    const sortedDates = [...row.dates].sort();
    const activeDays = new Set(sortedDates).size;
    const datedRecords = sortedDates.length;
    const builtRow = {
      key: row.key,
      name: profile.name || '',
      cedula: profile.cedula || '',
      codigo: profile.codigo || '',
      entidad: profile.entidad || '',
      // W5: contact/identity fields for the future report/table columns
      // (W7-W10) -- "first non-blank wins" across every sticker attributed
      // to this professional, computed once by buildIdentityIndex.
      np: profile.np || '',
      tarjetaProfesional: profile.tarjetaProfesional || '',
      celular: profile.celular || '',
      correo: profile.correo || '',
      ambiguous: Boolean(profile.ambiguous),
      stickersFase1: row.stickersFase1,
      stickersFase2: row.stickersFase2,
      stickersTotal: row.stickersTotal,
      surveyTotal: row.surveyTotal,
      total: row.stickersTotal + row.surveyTotal,
      firstDate: sortedDates.length ? sortedDates[0] : null,
      lastDate: sortedDates.length ? sortedDates[sortedDates.length - 1] : null,
      activeDays,
      avgPerActiveDay: activeDays ? Math.round((datedRecords / activeDays) * 100) / 100 : 0,
      rosterSourced: row.rosterSourced,
    };
    // D1 (plan §Decisiones): "barrios activos (7 d)" derived from THIS
    // professional's OWN stickers in the last 7 days (Bogotá), regardless
    // of the from/to filter currently narrowing the table -- always the
    // most recent real-world week, not the selected range.
    builtRow.barriosActivos = buildBarriosActivos(builtRow, { stickers: stickerList, today: todayStr, identity: idx });
    return builtRow;
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
 *  `professionalKey` restricts to one professional's key (professionalKeyOf
 *  — see the module note above); null (or any other falsy value) means
 *  every professional.
 *
 *  Also computes the PRE-RANGE `offsets` in this SAME loop (pure, so it
 *  belongs here even though only W8's chart consumes it): for each source,
 *  how many of the (identity/SIN_DATO-filtered) records fall STRICTLY
 *  BEFORE `from` — these never enter the daily/cumulative series (they are
 *  outside the visible window) but W8's running total starts counting from
 *  this offset instead of zero, so a mid-range `from` doesn't misrepresent
 *  the cumulative total as if history began at the window's left edge. A
 *  record without a date contributes 0 to the offset (never assumed to be
 *  "before" anything); one AFTER `to` is excluded entirely (neither the
 *  series nor the offset — it's simply outside scope); `from` null -> the
 *  offset is always 0 (there is no "before" an unbounded window). */
export function buildTimeline({
  stickers, surveys, from = null, to = null, professionalKey = null, identity,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: surveyList });

  const stickerCounts = new Map();
  const surveyCounts = new Map();
  let stickerOffset = 0;
  let surveyOffset = 0;

  for (const s of stickerList) {
    if (!s) continue;
    // Same SIN_DATO exclusion as buildProfessionalRows (#29) — a record whose
    // Fase never resolves never contributes a point to the timeline either.
    if (faseKeyDe(s) === 'SIN_DATO') continue;
    if (professionalKey && professionalKeyOf(s, idx) !== professionalKey) continue;
    const d = dateOnly(s.fecha);
    if (!d) continue; // sin fecha aporta 0 -- ni a la serie ni al offset
    if (to && d > to) continue; // fuera del rango visible (futuro) -- nunca cuenta
    if (from && d < from) { stickerOffset += 1; continue; } // pre-range -> offset, no serie
    stickerCounts.set(d, (stickerCounts.get(d) || 0) + 1);
  }

  for (const sv of surveyList) {
    if (!sv) continue;
    if (professionalKey && professionalKeyOf(sv, idx) !== professionalKey) continue;
    const d = dateOnly(sv.fecha_inspeccion);
    if (!d) continue;
    if (to && d > to) continue;
    if (from && d < from) { surveyOffset += 1; continue; }
    surveyCounts.set(d, (surveyCounts.get(d) || 0) + 1);
  }

  const allDates = [...new Set([...stickerCounts.keys(), ...surveyCounts.keys()])].sort();
  const offsets = { stickers: stickerOffset, surveys: surveyOffset };
  if (!allDates.length) {
    return {
      labels: [], stickers: [], surveys: [], stickersCumulative: [], surveysCumulative: [], offsets,
    };
  }

  const labels = [];
  let cursor = allDates[0];
  const endLabel = allDates[allDates.length - 1];
  while (cursor <= endLabel) {
    labels.push(cursor);
    cursor = shiftDateStr(cursor, 1);
  }

  const stickersDaily = labels.map((d) => stickerCounts.get(d) || 0);
  const surveysDaily = labels.map((d) => surveyCounts.get(d) || 0);
  // Running totals, one per source — each accumulates independently across
  // every label (including zero-count days, which never reset it), starting
  // from the pre-range offset rather than zero.
  let stickersRunning = stickerOffset;
  let surveysRunning = surveyOffset;
  const stickersCumulative = stickersDaily.map((n) => (stickersRunning += n));
  const surveysCumulative = surveysDaily.map((n) => (surveysRunning += n));

  return {
    labels, stickers: stickersDaily, surveys: surveysDaily, stickersCumulative, surveysCumulative, offsets,
  };
}

/** The raw stickers/surveys attributed to ONE professional (`row.key`, the
 *  same professionalKeyOf identity buildProfessionalRows/buildTimeline
 *  already use) — "los puntos recogidos", for the per-professional PDF
 *  report. `from`/`to` (both default null, meaning "whole career" — the
 *  ORIGINAL, unfiltered behavior, for the existing per-row report button):
 *  when either is set, records are narrowed to that period via the SAME
 *  stickerIncluded/surveyIncluded rules used everywhere else (an undated
 *  record then drops out, same as buildProfessionalRows/buildTimeline)
 *  instead of the old "always include undated, sorted last" behavior, which
 *  only still applies when NO period is given at all. */
export function professionalRecords(row, {
  stickers, surveys, identity, from = null, to = null,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: surveyList });
  const key = row && row.key;

  const stickerPoints = stickerList
    // Same SIN_DATO exclusion as buildProfessionalRows/buildTimeline (#29) —
    // a record whose Fase never resolves never shows up as one of this
    // professional's "puntos recogidos" either.
    .filter((s) => s && faseKeyDe(s) !== 'SIN_DATO' && professionalKeyOf(s, idx) === key && stickerIncluded(s, from, to))
    .map((s) => ({
      codigo: s.codigo_edificacion || '',
      direccion: (s.descripcion && s.descripcion.direccion) || '',
      municipio: s.municipio || '',
      fecha: dateOnly(s.fecha),
      faseLabel: FASE_LABELS[faseKeyDe(s)] || FASE_LABELS.SIN_DATO,
    }));
  stickerPoints.sort((a, b) => {
    if (a.fecha === b.fecha) return a.codigo < b.codigo ? -1 : a.codigo > b.codigo ? 1 : 0;
    if (a.fecha === null) return 1;
    if (b.fecha === null) return -1;
    return a.fecha < b.fecha ? -1 : 1;
  });

  const surveyPoints = surveyList
    .filter((sv) => sv && professionalKeyOf(sv, idx) === key && surveyIncluded(sv, from, to))
    .map((sv) => ({
      direccion: sv.direccion || '',
      nombreEdificacion: sv.nombre_edificacion || '',
      fecha: dateOnly(sv.fecha_inspeccion),
    }));
  surveyPoints.sort((a, b) => {
    if (a.fecha === b.fecha) return a.direccion < b.direccion ? -1 : a.direccion > b.direccion ? 1 : 0;
    if (a.fecha === null) return 1;
    if (b.fecha === null) return -1;
    return a.fecha < b.fecha ? -1 : 1;
  });

  return { stickerPoints, surveyPoints };
}

/** Hour-of-day / activity-recency metrics for ONE professional row's
 *  "Análisis temporales" columns: first/last record time-of-day (Bogotá
 *  minutes since midnight), the arithmetic-mean minute-of-day across the
 *  DAYS that have at least one timed record (never circular — a 23:50 and a
 *  00:10 average to ~12:00, not to midnight; documented, not "fixed", since
 *  averaging times-of-day has no single correct convention), `prevDay` (the
 *  last day STRICTLY BEFORE `today` with >=1 timed record — never "ayer"),
 *  and `daysSinceFirst` (today minus row.firstDate, in days).
 *
 *  A dated-but-untimed record (Survey missing `fecha_hora`, or one whose
 *  value fails to parse) still counts toward buildProfessionalRows'
 *  `activeDays` (computed there, from `dates`) but contributes NOTHING here
 *  — "día sin hora cuenta en activeDays, no en promedios" (plan edge case):
 *  this function's own day-set only ever contains days with a real time. */
export function buildTemporalMetrics(row, {
  stickers, surveys, today, identity,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: surveyList });
  const todayStr = today || bogotaToday();
  const key = row && row.key;

  const byDay = new Map(); // date -> { min, max } minutes-of-day
  function feed(dateStr, minutes) {
    if (dateStr === null || dateStr === undefined || minutes === null || minutes === undefined) return;
    const bucket = byDay.get(dateStr);
    if (!bucket) { byDay.set(dateStr, { min: minutes, max: minutes }); return; }
    if (minutes < bucket.min) bucket.min = minutes;
    if (minutes > bucket.max) bucket.max = minutes;
  }

  for (const s of stickerList) {
    if (!s) continue;
    if (faseKeyDe(s) === 'SIN_DATO') continue;
    if (professionalKeyOf(s, idx) !== key) continue;
    const parts = bogotaParts(s.fecha);
    if (!parts || parts.minutes === null) continue;
    feed(parts.date, parts.minutes);
  }
  for (const sv of surveyList) {
    if (!sv) continue;
    if (professionalKeyOf(sv, idx) !== key) continue;
    const parts = bogotaParts(sv.fecha_hora);
    if (!parts || parts.minutes === null) continue;
    feed(parts.date, parts.minutes);
  }

  const daysSinceFirst = (row && row.firstDate) ? daysBetween(row.firstDate, todayStr) : null;

  const days = [...byDay.keys()].sort();
  if (!days.length) {
    return {
      firstRecordMinutes: null, lastRecordMinutes: null,
      avgFirstMinutes: null, avgLastMinutes: null,
      prevDay: null, daysSinceFirst,
    };
  }

  const firstDay = days[0];
  const lastDay = days[days.length - 1];
  const sumFirst = days.reduce((acc, d) => acc + byDay.get(d).min, 0);
  const sumLast = days.reduce((acc, d) => acc + byDay.get(d).max, 0);
  // Last day STRICTLY before today with >=1 timed record -- "no ayer": if
  // the only timed day IS today, there is no such day (null), not "today".
  let prevDay = null;
  for (let i = days.length - 1; i >= 0; i--) {
    if (days[i] < todayStr) { prevDay = days[i]; break; }
  }

  return {
    firstRecordMinutes: byDay.get(firstDay).min,
    lastRecordMinutes: byDay.get(lastDay).max,
    avgFirstMinutes: Math.round(sumFirst / days.length),
    avgLastMinutes: Math.round(sumLast / days.length),
    prevDay,
    daysSinceFirst,
  };
}

/** "Barrios activos (7 d)" for ONE professional row (D1, plan
 *  §Decisiones): derived from the barrios of THIS professional's OWN
 *  stickers dated within the last `days` days up to and including `today`
 *  (Bogotá) — there is no real "active assignment" data (assignments are
 *  per-point, not per-barrio/per-professional), so this is an explicitly
 *  DERIVED signal, never a claim of formal assignment (the UI's job, W7+,
 *  is to label it as such). Window is INCLUSIVE of `today` and
 *  `today - (days - 1)`, i.e. `today - 6` is IN for the default 7-day
 *  window and `today - 7` is OUT. A blank `barrio_reportado` or one that
 *  reads as "Sin identificar" (case/accent-insensitive) never counts;
 *  accent/case variants of the same real barrio count once (deduped via
 *  utils.js's `normalize`). Returns a plain array of distinct display
 *  strings, sorted (es collation) for a deterministic render order. */
export function buildBarriosActivos(row, {
  stickers, today, days = 7, identity,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: [] });
  const todayStr = today || bogotaToday();
  const cutoff = shiftDateStr(todayStr, -(days - 1));
  const key = row && row.key;

  const seen = new Map(); // normalized barrio -> first-seen display spelling
  for (const s of stickerList) {
    if (!s) continue;
    if (faseKeyDe(s) === 'SIN_DATO') continue;
    if (professionalKeyOf(s, idx) !== key) continue;
    const parts = bogotaParts(s.fecha);
    if (!parts) continue;
    if (parts.date < cutoff || parts.date > todayStr) continue;
    const raw = (s.barrio_reportado || '').trim();
    if (!raw) continue;
    const normKey = normalize(raw);
    if (normKey === 'sin identificar') continue;
    if (!seen.has(normKey)) seen.set(normKey, raw);
  }
  return [...seen.values()].sort((a, b) => a.localeCompare(b, 'es'));
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

// ── createSegCache: single-entry memo (W6) ──────────────────────────────────
// A render() pass recomputes buildProfessionalRows over the whole
// stickers/surveys arrays (now including the per-row buildBarriosActivos
// pass, and eventually — a later work unit — buildTemporalMetrics' hour
// columns for the "Análisis temporales" sub-tab) on every keystroke-settle/
// filter change, even when NOTHING that would change the result actually
// changed (e.g. a sort-only click, W9). A single-entry memo — never a Map —
// skips that recomputation whenever the exact same inputs come back, while
// staying trivially bounded (never more than 1 entry, so no cache
// invalidation/eviction policy is needed at all).
//
// Keyed on STRICT identity (===) of the stickers/surveys arrays — never a
// deep/content comparison, which would be both slower than just recomputing
// and wrong for data.js's `data.js:263-265`-style in-place mutation (a
// caller that mutates an array in place and expects a cache miss would
// never get one under a content comparison) — plus plain string equality of
// from/to/professionalKey/today. The whole tuple is stored as the key
// alongside the RESULT of the last call, and any single field's mismatch
// (a new array reference, or ANY string field a caller changed) invalidates
// the one entry.
export function createSegCache() {
  let entry = null;

  function sameKey(a, b) {
    return a.stickers === b.stickers
      && a.surveys === b.surveys
      && a.from === b.from
      && a.to === b.to
      && a.professionalKey === b.professionalKey
      && a.today === b.today;
  }

  return {
    /** Returns the cached result when `key` matches the last call's key
     *  (see sameKey above); otherwise calls `compute()`, stores its result
     *  as the (only) entry, and returns it. */
    get(key, compute) {
      if (entry && sameKey(entry.key, key)) return entry.result;
      const result = compute();
      entry = { key, result };
      return result;
    },
    /** Drops the cached entry — the next get() (even with an identical key)
     *  recomputes. Called at the top of initSeguimiento() so a fresh open
     *  never reuses a stale entry from a previous session's stickers/
     *  surveys arrays (which, being garbage-collectable, could in principle
     *  share a memory address with a brand-new array — vanishingly unlikely
     *  in practice, but a stale entry surviving a full re-init would be a
     *  much more mundane bug: e.g. a `from`/`to` reset should never keep
     *  yesterday's result just because a value happened to match by luck). */
    clear() {
      entry = null;
    },
  };
}

/** Debounced search-input controller (W6): wraps utils.js's own debounce()
 *  instead of the hand-rolled clearTimeout/setTimeout pair this module used
 *  to carry — one fewer bespoke timer implementation to keep in sync with
 *  the shared one. `trigger()` schedules `callback` after `wait` ms,
 *  resetting the timer on every call (standard debounce semantics); a
 *  caller that fires `trigger()` again before it settles gets only the LAST
 *  call's effect. `cancel()` drops any pending call without scheduling a
 *  new one — the real reason this needs its own name instead of being
 *  inlined at each call site: a filter reset or a fresh initSeguimiento()
 *  must be able to kill a stale pending render from the OLD state before it
 *  fires against a tbody/currentRows that no longer exists (same class of
 *  bug `debounce()`'s own doc comment describes). */
export function makeSearchController(callback, wait = 250) {
  const debounced = debounce(callback, wait);
  return {
    trigger: debounced,
    cancel: debounced.cancel,
  };
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
  { key: 'surveyTotal', label: 'Evaluaciones Survey' },
  { key: 'total', label: 'Total' },
  { key: 'firstDate', label: 'Primer registro' },
  { key: 'lastDate', label: 'Último registro' },
  { key: 'activeDays', label: 'Días activos' },
  { key: 'avgPerActiveDay', label: 'Prom./día' },
];

let loadSeq = 0;
// W6: the hand-rolled clearTimeout/setTimeout pair this used to be is now
// utils.js's shared debounce() via makeSearchController() — reassigned on
// every initSeguimiento() call (same pattern as activeRenderChart/
// activeUpdateRecords below), so a stale controller from a previous open
// never fires against a torn-down tbody/currentRows.
let activeSearchDebounced = null;
// W6: one single-entry memo for the whole module lifetime — cleared (not
// replaced) at the top of every initSeguimiento() so a fresh open never
// reuses a stale entry, but a re-render WITHIN the same open (e.g. a filter
// change that ends up producing the exact same stickers/surveys/from/to) can
// still hit the cache.
const segCache = createSegCache();

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
// every open, same idea as loadSeq/activeSearchDebounced above) so a stale
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
async function descargarInformeProfesional(row, { stickers, surveys, identity }) {
  const points = professionalRecords(row, { stickers, surveys, identity });
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
  if (activeSearchDebounced) activeSearchDebounced.cancel();
  segCache.clear();
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
  // Identity index (W5/D7: cédula-first join) for the CURRENT stickers/
  // surveys — recomputed once per render() (not once per pure-function
  // call) so buildProfessionalRows/buildTimeline/the per-row PDF report all
  // resolve identity through the exact SAME index for a given render pass.
  let currentIdentity = buildIdentityIndex({ stickers, surveys });

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
      stickers, surveys, ...currentFilters(), professionalKey: chartSelectEl.value || null, identity: currentIdentity,
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
    // Identity + "hoy" (Bogotá) are recomputed on every render() — a store
    // refresh (updateSeguimientoRecords) swaps `surveys` in place, and a
    // stale identity/today would silently keep resolving keys or
    // "días desde 1ª actividad"/barrios-activos-7d against yesterday's data.
    currentIdentity = buildIdentityIndex({ stickers, surveys });
    const today = bogotaToday();
    const { from, to } = currentFilters();
    // `unassigned` (the Sin-profesional bucket, broken down by source) is
    // not destructured here — the UI only ever surfaces it as one combined
    // KPI tile (totals.unassigned), rendered by kpisHtml below.
    //
    // W6: memoized via segCache — a re-render with the SAME stickers/
    // surveys array references and the SAME from/to/today (e.g. a sort-only
    // interaction that still routes through render(), or two consecutive
    // opens with an unchanged store) skips recomputing buildProfessionalRows
    // (and its nested buildBarriosActivos pass per row) entirely.
    // `professionalKey` is reserved for a future unit that folds
    // buildTimeline's chart-selection filter into this same cached call; it
    // plays no role in buildProfessionalRows itself, so a fixed `null` here
    // never causes a spurious cache miss.
    const { rows, stickersWithoutDate, totals } = segCache.get(
      {
        stickers, surveys, from, to, professionalKey: null, today,
      },
      () => buildProfessionalRows({
        stickers, surveys, from, to, identity: currentIdentity, today,
      }),
    );
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
      await descargarInformeProfesional(row, { stickers, surveys, identity: currentIdentity });
    } catch (err) {
      console.error('seguimiento: fallo al generar el informe PDF', err);
      showToast('No se pudo generar el informe PDF.', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  });

  // Reassigned on every initSeguimiento() call (this init's OWN renderTable/
  // currentRows closure), same pattern as activeRenderChart/
  // activeUpdateRecords — the shared module-level handle above always
  // points at the CURRENTLY open init's controller.
  activeSearchDebounced = makeSearchController(() => renderTable(currentRows), 250);
  searchEl.addEventListener('input', () => {
    activeSearchDebounced.trigger();
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
    activeSearchDebounced.cancel();
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
        stickers_fase1: r.stickersFase1, stickers_fase2: r.stickersFase2,
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
