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
import {
  upsertChart, baseOptions, totalDataLabelPlugin, setChartEmpty, clearChartEmpty, hasChart,
} from './charts.js';
import { fetchEvaluacionesOnce } from './stickers.js';
import { loadPdfmake } from './report.js';

const STICKERS_ENDPOINT = 'stickersAtencionsismo';

// Spanish display labels for faseKeyDe()'s three return values — used by
// professionalRecords/buildProfessionalReportDocDefinition below, kept in
// sync with the table's own COLUMNS_TOTALES labels ("Sticker F1"/"F2"/"sin dato").
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

/** Digits-only join key for a cédula — mirrors the backend's
 *  `cedula_utils.solo_digitos` (used by `inspectores_depuracion._cedula_key`):
 *  the digit extraction and the float-artifact rule are the same (known,
 *  accepted divergence: exotic whitespace around a ".0" tail — BOM, NEL,
 *  control chars — where JS `\s` and Python differ; design D-CEDDEC). A
 *  float-artifact tail is dropped first — ONLY a lone ".0" at the end
 *  of an otherwise digit-only string ("1234567.0" -> "1234567", what a float
 *  cell stringifies to) — then every non-digit (dots, spaces, dashes, …) is
 *  stripped, so "1.234.567", 1234567 (number) and " 1234567 " all resolve to
 *  the same key. "166.000" and "12.000" are thousands-separated cédulas, NOT
 *  float artifacts: they become "166000" / "12000" (design D-CEDDEC). The regex
 *  below is embedded verbatim by a backend test that compares it with
 *  `COLA_FLOTANTE_PATRON`. Decision (leading zeros): kept VERBATIM, never
 *  stripped; a cédula that legitimately starts with "0" must not collide with
 *  one that doesn't. Returns "" when nothing digit-like remains (blank/
 *  non-numeric input, e.g. "CC" typed into the cédula field by mistake) —
 *  callers treat "" as "no cédula". */
export function cedulaKey(raw) {
  const text = String(raw === null || raw === undefined ? '' : raw);
  const artifact = /^\s*(\d+)\.0\s*$/.exec(text);
  return (artifact ? artifact[1] : text).replace(/\D/g, '');
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
  if (ownCedula && idx.eligibleCedulas.has(ownCedula)) {
    // seguimiento-inspectores-depurado (CRITICAL fix): a cédula that lost a
    // backend exact-name merge (Perfil.cedulas_unificadas) is eligible but
    // must resolve into the SURVIVING identity's row, never its own —
    // see buildIdentityIndexFromDepuracion's cedulaFusionadaACedulaSurvivor.
    const survivorCedula = (idx.cedulaFusionadaACedulaSurvivor
      && idx.cedulaFusionadaACedulaSurvivor.get(ownCedula)) || ownCedula;
    return `ced:${survivorCedula}`;
  }
  const nameKey = normalizeName(insp.nombre_completo || '');
  if (nameKey && idx.nameToCedula.has(nameKey)) return `ced:${idx.nameToCedula.get(nameKey)}`;
  return nameKey ? `nom:${nameKey}` : '';
}

/** seguimiento-inspectores-depurado, Fase 4 (design "Frontend Changes
 *  (minimal)"): builds the depuracion-derived branch of buildIdentityIndex
 *  below — profiles come DIRECTLY from `depuracion.inspectores`, never from
 *  the raw sticker/Survey "primer no vacío gana" merge (spec: "Frontend
 *  Consumes Backend-Resolved NP"). `eligibleCedulas`/`nameToCedula` still
 *  exist in the SAME shape as the non-depurado branch (built from
 *  `depuracion.inspectores`' own `identidad_key` and `depuracion.
 *  alias_nombres` respectively) so professionalKeyOf keeps routing raw
 *  sticker/Survey records into the exact SAME `ced:<cedula>` keys these
 *  profiles are stored under — buildProfessionalRows' per-record loop is
 *  untouched, only WHERE the profile's own fields come from changes. */
function buildIdentityIndexFromDepuracion(depuracion, depMeta) {
  const eligibleCedulas = new Set();
  const nameToCedula = new Map();
  const profiles = new Map();
  // CRITICAL fix: a merged-away identity's own cédula (backend's
  // Perfil.cedulas_unificadas, now serialized by _perfil_a_dict) must route
  // a raw sticker into the SAME row as the survivor instead of falling into
  // its own orphan `nom:` bucket. Keyed by the LOSING cédula, valued by the
  // survivor's own cédula.
  const cedulaFusionadaACedulaSurvivor = new Map();
  // normalizedName -> Set<cédula> over the SEEDED profiles (W3, see below).
  const seededNameCedulas = new Map();

  const inspectores = Array.isArray(depuracion.inspectores) ? depuracion.inspectores : [];
  for (const insp of inspectores) {
    if (!insp) continue;
    const ced = cedulaKey(insp.identidad_key || insp.identificacion);
    if (!ced) continue;
    eligibleCedulas.add(ced);
    const seededNameKey = normalizeName(insp.nombre_completo || '');
    if (seededNameKey) {
      if (!seededNameCedulas.has(seededNameKey)) seededNameCedulas.set(seededNameKey, new Set());
      seededNameCedulas.get(seededNameKey).add(ced);
    }
    const fusionadas = Array.isArray(insp.cedulas_unificadas) ? insp.cedulas_unificadas : [];
    for (const fusionadaRaw of fusionadas) {
      const cedFusionada = cedulaKey(fusionadaRaw);
      if (!cedFusionada || cedFusionada === ced) continue;
      eligibleCedulas.add(cedFusionada);
      cedulaFusionadaACedulaSurvivor.set(cedFusionada, ced);
    }
    profiles.set(`ced:${ced}`, {
      nameCounts: new Map(insp.nombre_completo ? [[insp.nombre_completo, 1]] : []),
      name: insp.nombre_completo || '',
      cedula: insp.identificacion || '',
      codigo: insp.codigo || '',
      entidad: insp.entidad || '',
      np: insp.np || '',
      npFuente: insp.np_fuente || 'ninguno',
      fase: insp.fase || '',
      faseNpFaltante: Boolean(insp.fase_np_faltante),
      estadoSugerido: insp.estado_sugerido || '',
      fuenteDato: insp.fuente_dato || '',
      tarjetaProfesional: insp.tarjeta_profesional || '',
      // D-ENFASIS: the registry's free text (admin-only depuracion block), kept as is. Only depurado
      // profiles carry the key; the legacy identity profiles never do.
      enfasis: typeof insp.enfasis === 'string' ? insp.enfasis : '',
      celular: insp.num_telefono || '',
      correo: insp.correo_contacto || '',
      noPersona: Boolean(insp.no_persona),
      ambiguous: false,
    });
  }

  // depuracion.alias_nombres: normalizedName -> identidad_key (backend's own
  // survey-name dedupe, spec: "a survey name that maps to a real person's
  // key never lands in GRUPO-EXTERNOS") — the exact same role nameToCedula
  // plays in the non-depurado branch, just sourced from the backend instead
  // of re-derived from raw stickers.
  for (const [nombreNorm, identidadKey] of Object.entries(depuracion.alias_nombres || {})) {
    const ced = cedulaKey(identidadKey);
    if (nombreNorm && ced) nameToCedula.set(nombreNorm, ced);
  }
  // Judgment-day W3: the seeded profiles feed the name index too, so a record
  // with a name but a blank/unlisted cédula unifies to the padrón person instead
  // of opening a second `nom:` row for the same human. ONLY a normalized name
  // that belongs to exactly ONE seeded cédula is indexed (homonyms never
  // unify), and the backend's own alias_nombres above always wins.
  for (const [nameKey, cedulas] of seededNameCedulas) {
    if (cedulas.size === 1 && !nameToCedula.has(nameKey)) nameToCedula.set(nameKey, [...cedulas][0]);
  }

  const resolverCore = { eligibleCedulas, nameToCedula, cedulaFusionadaACedulaSurvivor };
  const keyForSticker = (record) => professionalKeyOf(record, resolverCore);
  const keyForSurvey = (record) => professionalKeyOf(record, resolverCore);

  return {
    eligibleCedulas, nameToCedula, ambiguousNames: new Set(), profiles, keyForSticker, keyForSurvey,
    cedulaFusionadaACedulaSurvivor, ...depMeta,
  };
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
 *  their records count toward a KPI).
 *
 *  `depuracion` (seguimiento-inspectores-depurado, Fase 4, optional): the
 *  backend's `GET /stickers-atencionsismo` `depuracion` block. `depMeta`
 *  (activa/motivo/referenciaGeneradaEn/grupoExternos/revisionManual) is
 *  ALWAYS present on the returned index regardless of branch, so the
 *  badge/manual-review UI never needs to null-check which path ran — its
 *  defaults (false/''/''/null/[]) match the "no depuracion at all"
 *  cold-start/flag-off case exactly. `depuracion` absent or `activa:false`
 *  falls through to the pre-existing code path below, BYTE-IDENTICAL (this
 *  is also the Blob-restored cold-start path and the feature-flag-off
 *  path — design D5/"Frontend Changes (minimal)"). */
export function buildIdentityIndex({ stickers = [], surveys = [], depuracion = null } = {}) {
  // A usable block is an object carrying a BOOLEAN `activa` (judgment-day S1):
  // `{}`, `{ motivo }` or a non-boolean `activa` is malformed/absent, never a
  // degradation announcement nor an active block.
  const hasBlock = Boolean(depuracion) && typeof depuracion === 'object' && !Array.isArray(depuracion)
    && typeof depuracion.activa === 'boolean';
  const block = hasBlock ? depuracion : null;
  const depMeta = {
    depuracionActiva: Boolean(block && block.activa === true),
    // true when the payload carried NO usable depuracion block at all (flag
    // off, not requested, viewer, old backend, cold start) — distinct from a
    // block that says activa:false. Only the latter is announced (banner); an
    // absent block is the normal state and stays silent (depuracionBadgeHtml).
    depuracionAusente: !hasBlock,
    depuracionMotivo: (block && block.motivo) || '',
    referenciaGeneradaEn: (block && block.referencia_generada_en) || '',
    grupoExternos: (block && block.grupo_externos) || null,
    revisionManual: Array.isArray(block && block.revision_manual) ? block.revision_manual : [],
  };
  if (depMeta.depuracionActiva) return buildIdentityIndexFromDepuracion(depuracion, depMeta);

  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];

  const eligibleCedulas = new Set();
  const nameCedulaCandidates = new Map(); // normalizedName -> Set<cedula>
  for (const s of stickerList) {
    if (!s) continue;
    // H2: a record whose Fase never resolves (faseKeyDe -> 'SIN_DATO') is
    // dropped from every downstream KPI/report (#29) -- it must never drive
    // identity either (a discarded record steering a display name, a
    // cédula->name mapping, or an ambiguity flag would silently contradict
    // that exclusion). Checked FIRST, before this loop touches anything else.
    if (faseKeyDe(s) === 'SIN_DATO') continue;
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
    // Same SIN_DATO guard as the eligibility loop above (H2) -- this second
    // loop builds the per-key PROFILE (display name/cedula/codigo/entidad/…),
    // so a discarded record must not reach it either, even though
    // professionalKeyOf could still resolve it (via name->cédula unification)
    // to a key that DOES have real, eligible records.
    if (faseKeyDe(s) === 'SIN_DATO') continue;
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

  return {
    eligibleCedulas, nameToCedula, ambiguousNames, profiles, keyForSticker, keyForSurvey, ...depMeta,
  };
}

/** D17 seeding step: with an ACTIVE depuracion, one row per profile built
 *  from `depuracion.inspectores`; the legacy path (depuracion absent or
 *  `activa:false`) is this single early return — rows come only from records,
 *  byte-identical to before. Returns whether the table was seeded. */
function seedRowsFromProfiles(idx, ensureRow) {
  if (!idx.depuracionActiva) return false;
  for (const key of idx.profiles.keys()) ensureRow(key);
  return true;
}

/** The seeding + enrichment passes behind buildProfessionalRows (task 11.19):
 *  seed the rows (seedRowsFromProfiles), then let every counted sticker/Survey
 *  enrich its row — or create the row nobody seeded. Returns the raw
 *  per-key accumulators plus the counters of the records that resolved to no
 *  row at all. This is the ONE place rows come from, so a later change (a
 *  retained snapshot, a skipped re-render) hooks around one call. */
function accumulateRowActivity({
  stickerList, surveyList, idx, from, to,
}) {
  const rowsByKey = new Map();
  let unassignedStickers = 0;
  let unassignedSurveys = 0;
  let stickersWithoutDate = 0;
  // Earliest Bogotá date among the COUNTED stickers (attributed or not): the
  // anchor of the "stickers/día por inspector activo" range when no `from` is set.
  let earliestStickerDate = null;

  function ensureRow(key) {
    let row = rowsByKey.get(key);
    if (!row) {
      row = {
        key,
        stickersFase1: 0, stickersFase2: 0, stickersTotal: 0,
        surveyTotal: 0, rosterSourced: 0,
        dates: [],
        // M6: dates from STICKERS ONLY, kept separate from the pooled
        // `dates` above (stickers + Survey) -- "stickers/día por profesional"
        // must divide by days that actually have a sticker, never diluted by
        // a Survey-only day that happens to fall on the same professional.
        stickerDates: [],
      };
      rowsByKey.set(key, row);
    }
    return row;
  }

  // Phase 11 / D17: with an active depuracion every seeded person gets a row
  // BEFORE the sticker/survey pass, so a zero-activity person still shows up
  // (with zero counters); the loops below only enrich existing rows or add
  // the ones nobody seeded — no record is ever dropped.
  const seeded = seedRowsFromProfiles(idx, ensureRow);
  // An unseeded row has no profile: borrow the first name/cédula its own
  // records carry so it is identifiable instead of a blank "Sin dato" row.
  function adoptFallbackIdentity(row, name, cedula) {
    if (!seeded || idx.profiles.has(row.key)) return;
    if (!row.fallbackName && name) row.fallbackName = name;
    if (!row.fallbackCedula && cedula) row.fallbackCedula = cedula;
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
    else if (earliestStickerDate === null || dateVal < earliestStickerDate) earliestStickerDate = dateVal;
    if (!key) { unassignedStickers += 1; continue; }
    const row = ensureRow(key);
    adoptFallbackIdentity(row, s.inspector && s.inspector.nombre_completo, s.inspector && s.inspector.identificacion);
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
    if (dateVal) { row.dates.push(dateVal); row.stickerDates.push(dateVal); }
  }

  for (const sv of surveyList) {
    if (!sv) continue;
    if (!surveyIncluded(sv, from, to)) continue;
    const key = professionalKeyOf(sv, idx);
    if (!key) { unassignedSurveys += 1; continue; }
    const row = ensureRow(key);
    adoptFallbackIdentity(row, sv.nombre_evaluador, '');
    row.surveyTotal += 1;
    const dateVal = dateOnly(sv.fecha_inspeccion);
    if (dateVal) row.dates.push(dateVal);
  }

  return {
    rowsByKey, unassignedStickers, unassignedSurveys, stickersWithoutDate, seeded, earliestStickerDate,
  };
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
 *  buildBarriosActivos for the per-row "barrios activos (7 d)" derivation.
 *
 *  Phase 11 / D17: when `identity` carries an ACTIVE depuracion, every
 *  seeded person has a row even with zero activity in the range
 *  (accumulateRowActivity); `totals.professionals` and every average count
 *  only rows WITH activity, and `totals.padron` (present only then) is the
 *  number of seeded profiles (range-independent). The legacy path is
 *  byte-identical. */
export function buildProfessionalRows({
  stickers, surveys, from = null, to = null, identity, today,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: surveyList });
  const todayStr = today || bogotaToday();

  const {
    rowsByKey, unassignedStickers, unassignedSurveys, stickersWithoutDate, seeded, earliestStickerDate,
  } = accumulateRowActivity({
    stickerList, surveyList, idx, from, to,
  });

  // B1: ONE pass over the whole sticker list for "barrios activos" across
  // EVERY professional, instead of buildBarriosActivos rescanning it once
  // PER ROW below (110 rows x ~3,037 stickers each rescanned -- measured
  // 168 ms vs 8.8 ms before this change).
  const barriosByKey = buildBarriosActivosByKey({ stickers: stickerList, today: todayStr, identity: idx });
  // W9: same batch-instead-of-per-row pattern (B1/M5) for the "Análisis
  // temporales" sub-tab's hour-of-day columns -- computed ONCE here (reused
  // by the table, the XLSX temporales sheet and the PDF report), never
  // recomputed per sub-tab switch/export.
  const temporalByKey = buildTemporalMetricsByKey({
    stickers: stickerList, surveys: surveyList, identity: idx, today: todayStr, from, to,
  });

  // "stickers/día por inspector activo": END of the range is `to`, else TODAY
  // (Bogotá) — never the latest sticker, so a stray future-dated one cannot
  // stretch the range. Stickers dated after END are outside it.
  const rangeEnd = to || todayStr;
  let stickersInspectoresActivos = 0;

  const rows = [...rowsByKey.values()].map((row) => {
    const profile = idx.profiles.get(row.key)
      || (seeded ? { name: row.fallbackName, cedula: row.fallbackCedula } : {});
    // Numerator: F1+F2 stickers of the seeded profiles whose estado is exactly
    // 'activo' (orphan rows carry no estado), dated inside the range. Undated
    // stickers cannot be placed in it, so they never count.
    if (seeded && profile.estadoSugerido === 'activo') {
      for (const d of row.stickerDates) if (d <= rangeEnd) stickersInspectoresActivos += 1;
    }
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
      // D-ENFASIS: only a row seeded from the depurado base carries the key (legacy rows stay identical).
      ...(typeof profile.enfasis === 'string' ? { enfasis: profile.enfasis } : {}),
      celular: profile.celular || '',
      correo: profile.correo || '',
      ambiguous: Boolean(profile.ambiguous),
      // Task 4.1/4.9 (seguimiento-inspectores-depurado, Fase 4): only ever
      // populated when `depuracion` was active for THIS identity — '' in
      // every other case, same "no data" convention as the fields above.
      // xlsxRowsFor/matchesSearch/cellHtml read these straight off `row`,
      // never re-deriving them (spec: "Table And Export Reflect Depurado
      // Fields Consistently").
      npFuente: profile.npFuente || '',
      fase: profile.fase || '',
      estadoSugerido: profile.estadoSugerido || '',
      fuenteDato: profile.fuenteDato || '',
      noPersona: Boolean(profile.noPersona),
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
    // most recent real-world week, not the selected range. B1: read from the
    // single batch pass above instead of rescanning per row.
    builtRow.barriosActivos = barriosByKey.get(row.key) || [];
    // W9 ("Análisis temporales" sub-tab + XLSX): daysSinceFirst mirrors
    // buildTemporalMetrics' own per-row wrapper (today minus firstDate, null
    // when there is no firstDate at all); the rest comes straight from the
    // batch pass above (null defaults match buildTemporalMetricsByKey's own
    // "key never appears" case -- a professional with zero TIMED records).
    const temporal = temporalByKey.get(row.key);
    builtRow.daysSinceFirst = builtRow.firstDate ? daysBetween(builtRow.firstDate, todayStr) : null;
    builtRow.prevDay = temporal ? temporal.prevDay : null;
    builtRow.prevDayFirstMinutes = temporal ? temporal.prevDayFirstMinutes : null;
    builtRow.prevDayLastMinutes = temporal ? temporal.prevDayLastMinutes : null;
    builtRow.avgFirstMinutes = temporal ? temporal.avgFirstMinutes : null;
    builtRow.avgLastMinutes = temporal ? temporal.avgLastMinutes : null;
    // "Stickers prom. diario" (W9/M6): stickers-only pace, distinct from the
    // existing avgPerActiveDay (which pools stickers+surveys) -- see the
    // module note above buildProfessionalRows. M6: divides by
    // `stickerActiveDays` (distinct days with >=1 counted sticker), NOT the
    // pooled `activeDays` -- a professional with survey-only days mixed in
    // would otherwise have their sticker pace diluted by days that have no
    // sticker at all. null (never 0/NaN) when there are no STICKER-active
    // days: a genuine "no data" case, not a confirmed zero pace. DASH-masked
    // for display at the table/XLSX layer, same convention as every other
    // sticker-derived figure in this file.
    const stickerActiveDays = new Set(row.stickerDates).size;
    builtRow.stickerActiveDays = stickerActiveDays;
    builtRow.avgStickersPerDay = stickerActiveDays
      ? Math.round((row.stickersTotal / stickerActiveDays) * 100) / 100
      : null;
    return builtRow;
  });

  const stickersAssigned = rows.reduce((n, r) => n + r.stickersTotal, 0);
  const surveysAssigned = rows.reduce((n, r) => n + r.surveyTotal, 0);
  // D17: "professionals" (and every average) counts rows WITH activity in the
  // range; seeded zero-activity rows only add to the separate `padron` figure
  // (present ONLY when seeded, so the legacy totals shape is untouched). On
  // the legacy path a row exists only because it has activity, so
  // `professionals === rows.length` exactly as before.
  const activeRows = rows.filter(rowHasActivity).length;

  const totals = {
    professionals: activeRows,
    stickers: stickersAssigned + unassignedStickers,
    surveys: surveysAssigned + unassignedSurveys,
    avgPerProfessional: activeRows ? Math.round(((stickersAssigned + surveysAssigned) / activeRows) * 100) / 100 : 0,
    unassigned: unassignedStickers + unassignedSurveys,
    stickersWithoutDate,
  };
  // The padrón is the depurado total: the number of SEEDED profiles, never
  // `rows.length` (which also counts the orphan `nom:`/`ced:` rows that records
  // resolving to nobody create, and so drifts with the date range).
  if (seeded) {
    totals.padron = idx.profiles.size;
    // The depuración's own ACTIVE classification, over the whole padrón: like
    // `padron` it ignores the date range, the search box and the estado filter.
    // Exact match only — a missing/unexpected estado is never counted.
    let inspectoresActivos = 0;
    for (const profile of idx.profiles.values()) {
      if (profile.estadoSugerido === 'activo') inspectoresActivos += 1;
    }
    totals.inspectoresActivos = inspectoresActivos;
    totals.stickersInspectoresActivos = stickersInspectoresActivos;
    // Inclusive days of the range START..END; START = `from`, else the earliest
    // sticker date of the data. null (never 0/negative) when there is no START
    // (no `from` and no dated sticker) or when the range is inverted/malformed.
    const rangeStart = from || earliestStickerDate;
    const span = rangeStart ? daysBetween(rangeStart, rangeEnd) : null;
    totals.rangoDias = span !== null && span >= 0 ? span + 1 : null;
  }

  return {
    rows,
    unassigned: { stickers: unassignedStickers, surveys: unassignedSurveys },
    stickersWithoutDate,
    totals,
  };
}

/** Whether a table row has any sticker/Survey activity in the selected range
 *  (`total > 0`). Rows seeded from `depuracion.inspectores` without activity
 *  are excluded from the KPIs and from the mass PDF export scope (D17). A
 *  row without a finite `total` (malformed) counts as no activity. */
export function rowHasActivity(row) {
  return Boolean(row) && Number.isFinite(row.total) && row.total > 0;
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
  // N10: an inverted range (`to` < `from`) has no valid window at all -- the
  // per-record loop below would otherwise count every date <= `to` (all of
  // which are also < `from`) toward the offset, producing a non-zero
  // offset alongside the (already correctly) empty labels/series. Bail out
  // to the same all-zero shape empty inputs return, before touching either.
  if (from && to && to < from) {
    return {
      labels: [], stickers: [], surveys: [], stickersCumulative: [], surveysCumulative: [],
      offsets: { stickers: 0, surveys: 0 },
    };
  }

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

// Shared by professionalRecords AND its M6 batch counterpart
// (professionalRecordsByKey) below — a single comparator each, so the two
// can never quietly drift into a different sort order for the same points.
function compareStickerPoint(a, b) {
  if (a.fecha === b.fecha) return a.codigo < b.codigo ? -1 : a.codigo > b.codigo ? 1 : 0;
  if (a.fecha === null) return 1;
  if (b.fecha === null) return -1;
  return a.fecha < b.fecha ? -1 : 1;
}
function compareSurveyPoint(a, b) {
  if (a.fecha === b.fecha) return a.direccion < b.direccion ? -1 : a.direccion > b.direccion ? 1 : 0;
  if (a.fecha === null) return 1;
  if (b.fecha === null) return -1;
  return a.fecha < b.fecha ? -1 : 1;
}
function toStickerPoint(s) {
  return {
    codigo: s.codigo_edificacion || '',
    direccion: (s.descripcion && s.descripcion.direccion) || '',
    municipio: s.municipio || '',
    fecha: dateOnly(s.fecha),
    faseLabel: FASE_LABELS[faseKeyDe(s)] || FASE_LABELS.SIN_DATO,
    // W11: additive — the raw ATC-20 classification fields (same fields
    // evaluaciones.js already reads: color_etiqueta/clasificacion, see
    // stickers_atencionsismo.py's own output shape), carried through so the
    // per-professional PDF report's "Listado de stickers" table can render a
    // colored classification badge (badgeStyleFor/badgeCell below) instead
    // of losing this signal. Never renamed/removed an existing field.
    colorEtiqueta: s.color_etiqueta || '',
    clasificacion: s.clasificacion || '',
  };
}
function toSurveyPoint(sv) {
  return {
    direccion: sv.direccion || '',
    nombreEdificacion: sv.nombre_edificacion || '',
    fecha: dateOnly(sv.fecha_inspeccion),
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
 *  only still applies when NO period is given at all.
 *
 *  M6: see professionalRecordsByKey below for the ONE-PASS-over-every-
 *  professional version the mass export loop uses instead of calling this
 *  once per row (both share the exact same filter/map/sort logic, so they
 *  can never disagree). */
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
    .map(toStickerPoint);
  stickerPoints.sort(compareStickerPoint);

  const surveyPoints = surveyList
    .filter((sv) => sv && professionalKeyOf(sv, idx) === key && surveyIncluded(sv, from, to))
    .map(toSurveyPoint);
  surveyPoints.sort(compareSurveyPoint);

  return { stickerPoints, surveyPoints };
}

/** M6: batch version of professionalRecords — ONE pass over the WHOLE
 *  stickers/surveys arrays, grouping "puntos recogidos" per professional KEY
 *  for the given period, instead of the mass export loop calling
 *  professionalRecords once PER professional (each call re-scanning the
 *  full arrays — O(rows × records), the same class of quadratic blowup
 *  B1/M5 already fixed for buildBarriosActivos/buildTemporalMetrics).
 *  Returns `Map<key, { stickerPoints, surveyPoints }>`; a key with no
 *  records in the period simply has no entry — callers fall back to
 *  `{ stickerPoints: [], surveyPoints: [] }` (mirroring professionalRecords'
 *  own "zero records" shape) via `.get(key) || …`. Shares the exact same
 *  filter/map/sort logic (toStickerPoint/toSurveyPoint/compareStickerPoint/
 *  compareSurveyPoint) as professionalRecords, so the two are guaranteed to
 *  agree for every key — see seguimiento.test.mjs's own batch-vs-per-row
 *  equality check. */
export function professionalRecordsByKey({
  stickers, surveys, identity, from = null, to = null,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: surveyList });

  const stickerByKey = new Map();
  for (const s of stickerList) {
    if (!s || faseKeyDe(s) === 'SIN_DATO' || !stickerIncluded(s, from, to)) continue;
    const key = professionalKeyOf(s, idx);
    if (!key) continue;
    let list = stickerByKey.get(key);
    if (!list) { list = []; stickerByKey.set(key, list); }
    list.push(toStickerPoint(s));
  }
  const surveyByKey = new Map();
  for (const sv of surveyList) {
    if (!sv || !surveyIncluded(sv, from, to)) continue;
    const key = professionalKeyOf(sv, idx);
    if (!key) continue;
    let list = surveyByKey.get(key);
    if (!list) { list = []; surveyByKey.set(key, list); }
    list.push(toSurveyPoint(sv));
  }
  for (const list of stickerByKey.values()) list.sort(compareStickerPoint);
  for (const list of surveyByKey.values()) list.sort(compareSurveyPoint);

  const keys = new Set([...stickerByKey.keys(), ...surveyByKey.keys()]);
  const result = new Map();
  for (const key of keys) {
    result.set(key, {
      stickerPoints: stickerByKey.get(key) || [],
      surveyPoints: surveyByKey.get(key) || [],
    });
  }
  return result;
}

/** M6: batch version of visitasUltimos7Dias — ONE pass (via
 *  professionalRecordsByKey over the rolling today-6..today window),
 *  instead of the mass export loop calling visitasUltimos7Dias (itself a
 *  professionalRecords call, i.e. a full rescan) once PER professional.
 *  Returns `Map<key, count>`; a key with zero records in the window has no
 *  entry — callers fall back to `.get(key) || 0`, same convention as
 *  professionalRecordsByKey's own "no entry" case. */
export function visitasUltimos7DiasByKey({
  stickers, surveys, identity, today,
} = {}) {
  const todayStr = today || bogotaToday();
  const from = shiftDateStr(todayStr, -6);
  const byKey = professionalRecordsByKey({
    stickers, surveys, identity, from, to: todayStr,
  });
  const result = new Map();
  for (const [key, points] of byKey) {
    result.set(key, points.stickerPoints.length + points.surveyPoints.length);
  }
  return result;
}

/** M5/H3: batch version of buildTemporalMetrics — ONE pass over
 *  stickers+surveys grouping timed records per professional KEY per Bogotá
 *  day (instead of buildProfessionalRows/callers re-scanning the full
 *  stickers+surveys arrays once PER ROW — measured 278 ms for 110 rows
 *  before this change). Returns `Map<key, metrics>` where `metrics` is
 *  everything buildTemporalMetrics computes EXCEPT `daysSinceFirst` (that
 *  one depends on `row.firstDate`, a per-row value the batch pass has no
 *  business knowing about — the thin per-row wrapper below adds it back).
 *
 *  H3 (renamed from firstRecordMinutes/lastRecordMinutes): the old fields
 *  came from the EARLIEST day's min and the LATEST day's max — two
 *  DIFFERENT days — so "last" could end up earlier than "first" (e.g.
 *  09-10 09:00, 09-10 17:00, 09-12 08:00 -> old first=540, old last=480).
 *  `prevDayFirstMinutes`/`prevDayLastMinutes` both come from `prevDay` (the
 *  latest day STRICTLY BEFORE `today` with >=1 timed record — never
 *  "ayer"), i.e. the SAME day, so last is never earlier than first. Both
 *  are null when there is no such day (never today itself). `avgFirst/
 *  LastMinutes` are unchanged: the arithmetic-mean minute-of-day across ALL
 *  days that have a timed record (never circular — a 23:50 and a 00:10
 *  average to ~12:00, not to midnight; documented, not "fixed", since
 *  averaging times-of-day has no single correct convention).
 *
 *  A dated-but-untimed record (Survey missing `fecha_hora`, or one whose
 *  value fails to parse) still counts toward buildProfessionalRows'
 *  `activeDays` (computed there, from `dates`) but contributes NOTHING here
 *  — "día sin hora cuenta en activeDays, no en promedios" (plan edge case):
 *  this function's own day-set only ever contains days with a real time.
 *
 *  L10 (bug fix): `from`/`to` (YYYY-MM-DD, either may be null) now filter
 *  BOTH sources through the SAME stickerIncluded/surveyIncluded rules
 *  buildProfessionalRows/buildTimeline already apply — sibling columns
 *  (stickersTotal, the timeline) already dropped out-of-range records, but
 *  this function silently ignored the active Desde/Hasta filter, so an
 *  out-of-range record could still set `prevDay`/skew the hour-of-day
 *  averages the "Análisis temporales" sub-tab shows for the CURRENTLY
 *  filtered period. No active range (both null, the default) -> unchanged
 *  behavior, every timed record counts, same as before this fix. */
export function buildTemporalMetricsByKey({
  stickers, surveys, identity, today, from = null, to = null,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: surveyList });
  const todayStr = today || bogotaToday();

  const byKeyDay = new Map(); // key -> Map<date, { min, max }>
  function feed(key, dateStr, minutes) {
    if (!key || dateStr === null || dateStr === undefined || minutes === null || minutes === undefined) return;
    let byDay = byKeyDay.get(key);
    if (!byDay) { byDay = new Map(); byKeyDay.set(key, byDay); }
    const bucket = byDay.get(dateStr);
    if (!bucket) { byDay.set(dateStr, { min: minutes, max: minutes }); return; }
    if (minutes < bucket.min) bucket.min = minutes;
    if (minutes > bucket.max) bucket.max = minutes;
  }

  for (const s of stickerList) {
    if (!s) continue;
    if (faseKeyDe(s) === 'SIN_DATO') continue;
    if (!stickerIncluded(s, from, to)) continue;
    const key = professionalKeyOf(s, idx);
    if (!key) continue;
    const parts = bogotaParts(s.fecha);
    if (!parts || parts.minutes === null) continue;
    feed(key, parts.date, parts.minutes);
  }
  for (const sv of surveyList) {
    if (!sv) continue;
    if (!surveyIncluded(sv, from, to)) continue;
    const key = professionalKeyOf(sv, idx);
    if (!key) continue;
    const parts = bogotaParts(sv.fecha_hora);
    if (!parts || parts.minutes === null) continue;
    feed(key, parts.date, parts.minutes);
  }

  const result = new Map();
  for (const [key, byDay] of byKeyDay) {
    const days = [...byDay.keys()].sort();
    const sumFirst = days.reduce((acc, d) => acc + byDay.get(d).min, 0);
    const sumLast = days.reduce((acc, d) => acc + byDay.get(d).max, 0);
    // Last day STRICTLY before today with >=1 timed record -- "no ayer": if
    // the only timed day IS today, there is no such day (null), not "today".
    let prevDay = null;
    for (let i = days.length - 1; i >= 0; i--) {
      if (days[i] < todayStr) { prevDay = days[i]; break; }
    }
    const prevDayBucket = prevDay ? byDay.get(prevDay) : null;
    result.set(key, {
      prevDayFirstMinutes: prevDayBucket ? prevDayBucket.min : null,
      prevDayLastMinutes: prevDayBucket ? prevDayBucket.max : null,
      avgFirstMinutes: Math.round(sumFirst / days.length),
      avgLastMinutes: Math.round(sumLast / days.length),
      prevDay,
    });
  }
  return result;
}

/** Hour-of-day / activity-recency metrics for ONE professional row's
 *  "Análisis temporales" columns — thin wrapper over
 *  buildTemporalMetricsByKey (M5) for the single-row callers (W10 per-row
 *  report), plus `daysSinceFirst` (today minus row.firstDate, in days),
 *  which the batch pass above never computes since it has no `row` to read
 *  `firstDate` from. See buildTemporalMetricsByKey's own doc comment for the
 *  full field contract (H3: prevDayFirst/LastMinutes, not firstRecordMinutes/
 *  lastRecordMinutes; L10: from/to, passed straight through to
 *  buildTemporalMetricsByKey). */
export function buildTemporalMetrics(row, {
  stickers, surveys, today, identity, from = null, to = null,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const surveyList = Array.isArray(surveys) ? surveys : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: surveyList });
  const todayStr = today || bogotaToday();
  const key = row && row.key;

  const daysSinceFirst = (row && row.firstDate) ? daysBetween(row.firstDate, todayStr) : null;

  const map = buildTemporalMetricsByKey({
    stickers: stickerList, surveys: surveyList, identity: idx, today: todayStr, from, to,
  });
  const metrics = map.get(key);
  if (!metrics) {
    return {
      prevDayFirstMinutes: null, prevDayLastMinutes: null,
      avgFirstMinutes: null, avgLastMinutes: null,
      prevDay: null, daysSinceFirst,
    };
  }
  return { ...metrics, daysSinceFirst };
}

/** B1: batch version of buildBarriosActivos — ONE pass over ALL stickers,
 *  grouping "barrios activos" per professional KEY (instead of
 *  buildProfessionalRows calling buildBarriosActivos, a full sticker
 *  rescan, once PER ROW — 110 rows x ~3,037 stickers each rescanned via
 *  faseKeyDe + professionalKeyOf + bogotaParts ~ 334k identity resolutions,
 *  measured 168 ms vs 8.8 ms before this change). Returns `Map<key,
 *  string[]>`; buildBarriosActivos(row, …) below is now a thin single-key
 *  lookup over this same batch pass, so both stay identical by
 *  construction. See buildBarriosActivos' own doc comment (still accurate)
 *  for the full window/exclusion/dedupe contract this shares — repeated
 *  briefly here: window INCLUSIVE of `today` and `today - (days - 1)`; a
 *  blank `barrio_reportado` or one reading as "Sin identificar" (case/
 *  accent/whitespace-insensitive) never counts; SIN_DATO stickers excluded.
 *
 *  L6: the dedupe key uses normalizeName (not utils.js's bare `normalize`)
 *  because it ALSO collapses internal whitespace — "San  Antonio" (double
 *  space) and "San Antonio" must dedupe to ONE entry, not two; a barrio
 *  name is free text typed by different people, same drift as a
 *  professional's name. */
export function buildBarriosActivosByKey({
  stickers, today, days = 7, identity,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: [] });
  const todayStr = today || bogotaToday();
  const cutoff = shiftDateStr(todayStr, -(days - 1));

  const seenByKey = new Map(); // key -> Map<normalized barrio, first-seen display spelling>
  for (const s of stickerList) {
    if (!s) continue;
    if (faseKeyDe(s) === 'SIN_DATO') continue;
    const key = professionalKeyOf(s, idx);
    if (!key) continue;
    const parts = bogotaParts(s.fecha);
    if (!parts) continue;
    if (parts.date < cutoff || parts.date > todayStr) continue;
    const raw = (s.barrio_reportado || '').trim();
    if (!raw) continue;
    const normKey = normalizeName(raw);
    if (normKey === 'sin identificar') continue;
    let seen = seenByKey.get(key);
    if (!seen) { seen = new Map(); seenByKey.set(key, seen); }
    if (!seen.has(normKey)) seen.set(normKey, raw);
  }

  const result = new Map();
  for (const [key, seen] of seenByKey) {
    result.set(key, [...seen.values()].sort((a, b) => a.localeCompare(b, 'es')));
  }
  return result;
}

/** "Barrios activos (7 d)" for ONE professional row (D1, plan
 *  §Decisiones) — thin wrapper over buildBarriosActivosByKey (B1) for the
 *  single-row callers (W10 per-row report); see that function's doc
 *  comment for the full contract. Returns a plain array of distinct
 *  display strings, sorted (es collation) for a deterministic render
 *  order, or `[]` when this professional has none in the window. */
export function buildBarriosActivos(row, {
  stickers, today, days = 7, identity,
} = {}) {
  const stickerList = Array.isArray(stickers) ? stickers : [];
  const idx = identity || buildIdentityIndex({ stickers: stickerList, surveys: [] });
  const key = row && row.key;
  const map = buildBarriosActivosByKey({
    stickers: stickerList, today, days, identity: idx,
  });
  return map.get(key) || [];
}

// ── W10: reporting helpers (objetivo diario, visitas 7d, filename slug) ────

/** Daily visit target needed to clear a backlog by `deadline` (D5: fixed
 *  project deadline, 2026-09-30), spread evenly across `profesionalesActivos`
 *  and the remaining days. `null` (never a fabricated number) when:
 *   - `pendientes` isn't a finite number — `reportes_agg.json` may not carry
 *     `kpis` at all (see kpisOficialesFrom's own contract in
 *     reportes-ciudadanos.js): a missing/non-finite value must NEVER be
 *     silently treated as 0 pending buildings;
 *   - `profesionalesActivos` isn't a finite number, or is <= 0 — dividing by
 *     zero (or a meaningless negative headcount) has no sane answer;
 *   - `today` is missing, or already past `deadline` (string compare works
 *     because both are zero-padded YYYY-MM-DD) — there is no daily PACE that
 *     still "clears the backlog by then" once the deadline has passed.
 *  `days` is `max(1, daysBetween(today, deadline))` — `today === deadline`
 *  still yields exactly 1 day (never a division by zero), and any malformed
 *  date daysBetween can't parse also resolves to `null` here (defensive;
 *  today/deadline are both expected to already be validated YYYY-MM-DD).
 *
 *  M3 (bug fix): `pendientes < 0` (a backlog gone negative — an upstream
 *  data/sign bug) now returns `null` too, same as any other non-finite
 *  value, rather than silently producing a NEGATIVE daily target (which
 *  would read as "you may do fewer visits than zero", the opposite of what
 *  a target means). Rounding is `Math.ceil`, never `Math.round`: a target
 *  meant to clear a backlog by a FIXED deadline must never be UNDER-stated
 *  — rounding 3.33 down to 3.3 (or, worse, `{pendientes:1,
 *  profesionalesActivos:110}`'s 0.009… down to a fabricated 0) would let the
 *  real backlog slip past the deadline while every professional still hits
 *  "their" rounded number every day. `Math.ceil` on a positive `pendientes`
 *  can never round down to 0 (the smallest non-zero result is 0.1). */
export function objetivoDiario({
  pendientes, profesionalesActivos, today, deadline = '2026-09-30',
} = {}) {
  if (!Number.isFinite(pendientes) || pendientes < 0) return null;
  if (!Number.isFinite(profesionalesActivos) || profesionalesActivos <= 0) return null;
  if (!today || today > deadline) return null;
  const rawDays = daysBetween(today, deadline);
  if (rawDays === null) return null;
  const days = Math.max(1, rawDays);
  const value = pendientes / profesionalesActivos / days;
  return Math.ceil(value * 10) / 10;
}

/** Rolling count of this professional's stickers + Survey records in the
 *  last 7 days INCLUDING today (`today-6` .. `today`, D3 — "a hoy", never
 *  "hasta ayer"). Reuses professionalRecords' own from/to narrowing (same
 *  stickerIncluded/surveyIncluded rules, same SIN_DATO exclusion via
 *  faseKeyDe applied FIRST) instead of a second, independent window/filter
 *  implementation that could silently drift from it. */
export function visitasUltimos7Dias(row, {
  stickers, surveys, identity, today,
} = {}) {
  const todayStr = today || bogotaToday();
  const from = shiftDateStr(todayStr, -6);
  const points = professionalRecords(row, {
    stickers, surveys, identity, from, to: todayStr,
  });
  return points.stickerPoints.length + points.surveyPoints.length;
}

/** A professional's display name -> a safe PDF filename fragment: unsafe
 *  filename characters collapse to `_` (never dropped silently, which could
 *  make two DIFFERENT names collide into the same slug); a blank/whitespace-
 *  only/missing name falls back to the literal 'profesional' rather than
 *  producing an empty filename segment. Extracted from the existing
 *  per-row/per-selection report download logic (unchanged behavior) so the
 *  mass export's per-professional filenames — and a self-check — can reuse
 *  the exact same rule instead of a second, copy-pasted regex. */
export function reportFilenameSlug(name) {
  const slug = String(name || 'profesional').trim().replace(/[^\w-]+/g, '_');
  return slug || 'profesional';
}

// ── Per-professional PDF report ─────────────────────────────────────────────
// Same recipe as report.js: a PURE doc-definition builder (no fetch/DOM/
// pdfmake dependency, so it is Node-testable) plus a thin async orchestrator
// in the DOM section below that lazy-loads pdfmake and triggers the download.

const REPORT_DISCLAIMER = 'Informe generado automáticamente a partir del cruce aproximado por nombre '
  + 'entre Stickers y Survey — ver la nota de la pestaña Seguimiento. No constituye un documento oficial certificado.';

// W10: the individual/mass report now prints celular/correo/cédula — a
// dedicated confidentiality line alongside the pre-existing cross-source
// caveat, since the mass export in particular ships this for every
// professional in one file.
const REPORT_CONFIDENTIALITY_NOTICE = 'Este informe contiene datos personales del profesional (cédula, celular, correo) — '
  + 'tratar conforme a la política de protección de datos; no distribuir fuera de los canales autorizados.';

// M7: the confidentiality notice repeats on EVERY page as a pdfmake `footer:`
// function — a ~110-person mass export otherwise only shows it once
// (wherever it happened to sit in the flattened content array), unreachable
// once a reader has scrolled past it on a multi-page-per-professional
// export. A SINGLE module-scope function (never a fresh arrow function
// PER CALL) for the same reason REPORT_CARD_LAYOUT above is a shared
// object: buildMassReportDocDefinition's per-professional equality guarantee
// is assert.deepStrictEqual, which compares functions by REFERENCE only —
// two separately-allocated but behaviorally-identical closures would never
// be deepStrictEqual. Sharing one function reference sidesteps this, and
// lets buildMassReportDocDefinition attach the EXACT SAME reference as its
// own single top-level footer (one PDF document = one footer, not one per
// merged-in professional).
function reportFooter() {
  return { text: REPORT_CONFIDENTIALITY_NOTICE, style: 'disclaimer', margin: [40, 0, 40, 0] };
}

const REPORT_STICKERS_CAP = 50;

// Proyección fija (D5/plan): visita objetivo diario se calcula "al 30 de
// septiembre de 2026" -- el mismo deadline que objetivoDiario() usa por
// defecto, y el mismo texto que este builder muestra junto al valor.
const OBJETIVO_DEADLINE_LABEL = '30 de septiembre de 2026';

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
 *  whether that means zero work or a rendering bug.
 *
 *  W11: a cell may now ALSO be a pre-built pdfmake cell object (e.g.
 *  badgeCell(...), for the "Listado de stickers" classification column)
 *  instead of a plain string/number — passed through untouched rather than
 *  being coerced via String(cell) (which would otherwise flatten it into the
 *  useless literal "[object Object]"). Every EXISTING caller only ever
 *  passes strings/numbers, so this is purely additive — their rendering is
 *  byte-identical to before. */
function pointsTable(headers, rows, emptyText) {
  if (!rows.length) return [{ text: emptyText, style: 'fieldValue', margin: [0, 0, 0, 10] }];
  return [{
    table: {
      headerRows: 1,
      widths: headers.map(() => '*'),
      body: [
        headers.map((h) => ({ text: h, style: 'tableHeader' })),
        ...rows.map((r) => r.map((cell) => {
          if (cell && typeof cell === 'object') return cell;
          return { text: cell === '' || cell === null ? 'Sin dato' : String(cell), style: 'fieldValue' };
        })),
      ],
    },
    layout: 'lightHorizontalLines',
    margin: [0, 0, 0, 10],
  }];
}

/** Same idea as pointsTable, capped at `cap` rows with a "…y N más" note when
 *  the real count exceeds it — a manager reading the printed listing needs a
 *  bounded page count regardless of how many stickers a very active
 *  professional collected, while still being told the true total exists
 *  (never silently truncated with no indication). */
function cappedPointsTable(headers, rows, emptyText, cap = REPORT_STICKERS_CAP) {
  const shown = rows.slice(0, cap);
  const table = pointsTable(headers, shown, emptyText);
  const overflow = rows.length - shown.length;
  if (overflow > 0) table.push({ text: `…y ${overflow} más`, style: 'disclaimer', margin: [0, -6, 0, 10] });
  return table;
}

// ── W11: report visual redesign — colored badges, KPI stat cards, section
// rule, activity sparkline ──────────────────────────────────────────────────
// Merges the best of both Claude Design mockups ("Reporte Profesional" —
// card-style KPI grid, colored header rule; "Reporte Borrador" — same PLUS a
// colored sticker classification badge + "Total stickers en período" line)
// into the EXISTING report, without dropping anything it already showed.
// Every function here is a PURE pdfmake-node builder (no fetch/DOM/pdfmake
// dependency), same recipe as kvTable/pointsTable above.

// A4 content width in pdfmake points: 595.28 (A4 width) - 40 - 40 (default
// pageMargins, unchanged by this report) ≈ 515. Used by the header/section
// rule canvases so they always span the full text column, mirroring the
// mockups' `border-bottom` (which spans its container's full width).
const REPORT_CONTENT_WIDTH = 515;

// The mockups' fixed color palette (see context/mejoras_seguimiento/*.dc.html):
// header/section rule + card values #151F55, stat card bg #f0f2f7 (border
// #e0e3ea), the highlighted "objetivo diario" card #e8f0fc bg / #2186E0 text.
// pdfmake ships only Roboto by default (no Montserrat font file) — headers
// approximate the mockups' bold look with `bold:true` + this same color
// instead of a font-family change (never add a new font asset for this).
const REPORT_HEADER_COLOR = '#151F55';
const REPORT_CARD_BG = '#f0f2f7';
const REPORT_CARD_BORDER = '#e0e3ea';
const REPORT_ACCENT_BG = '#e8f0fc';
const REPORT_ACCENT_COLOR = '#2186E0';

// A SINGLE shared layout object (module-scope, built once) for every
// statCard() — never a fresh `{ hLineWidth: () => 1, ... }` object literal
// PER CALL. buildMassReportDocDefinition's per-professional equality
// guarantee is `assert.deepStrictEqual` (via 'node:assert/strict'), which
// compares functions by REFERENCE, never by behavior — two behaviorally
// identical but separately-allocated arrow functions (one from the solo
// build, one from the mass build) would never be deepStrictEqual, breaking
// that invariant for a reason that has nothing to do with the actual
// content. Sharing one object/one set of function references sidesteps this
// entirely, the same way a bug like it must be FIXED here, never worked
// around by loosening the test (per this file's own W10 doc comment on the
// mass/solo guarantee).
const REPORT_CARD_LAYOUT = {
  hLineWidth: () => 1,
  vLineWidth: () => 1,
  hLineColor: () => REPORT_CARD_BORDER,
  vLineColor: () => REPORT_CARD_BORDER,
};

// Sticker classification badge styles — reuses the app's OWN ATC-20/
// colorEtiqueta vocabulary (utils.js KNOWN_LABELS/habitabilityColor,
// evaluaciones.js's color_etiqueta/clasificacion usage) rather than
// inventing a parallel one. Badge LABEL TEXT follows the Borrador mockup's
// own Spanish wording (Inspeccionado / Con restricciones / Inseguro), which
// does not conflict with the app's vocabulary — only the mockup's exact
// COLOR HEX values are novel here (the rest of the dashboard has no
// pre-existing "badge" treatment to stay consistent with).
const BADGE_STYLES = {
  inspeccionado: { bg: '#e6f4ea', color: '#1a7d3a', label: 'Inspeccionado' },
  restringido: { bg: '#fff3e0', color: '#b8730a', label: 'Con restricciones' },
  inseguro: { bg: '#fce4ec', color: '#c62828', label: 'Inseguro' },
  neutral: { bg: '#eeeeee', color: '#555555', label: 'Sin clasificar' },
};

/** Sticker classification (clasificacion primary, colorEtiqueta fallback)
 *  -> `{ bg, color, label }` badge style.
 *
 *  H1 (fixed): `clasificacion` is the AUTHORITATIVE signal, not
 *  `colorEtiqueta` — the backend (stickers_atencionsismo.py) sets
 *  `clasificacion` from a matched Firestore evaluación when one exists,
 *  which can OVERRIDE a stale `colorEtiqueta` from the raw atencionsismo API;
 *  evaluaciones.js's claseDe (what the Stickers tab actually renders) reads
 *  `clasificacion` ONLY. Prioritizing colorEtiqueta could show the SAFER of
 *  two contradictory classifications for the same sticker, disagreeing with
 *  the Stickers tab — dangerous for a disaster-response tool. `clasificacion`
 *  (ATC-20: inspeccionado/uso_restringido/peligro_colapso, OR the backend's
 *  own INSPECCIONADA/USO_RESTRINGIDO/INSEGURO clase-fallback vocabulary —
 *  see stickers_atencionsismo.py's COLOR_TO_CLASE) is checked first; only
 *  when it's blank/unrecognized does `colorEtiqueta` (the atencionsismo
 *  API's own human label — "Habitable" / "Acceso restringido" / "No
 *  habitable" / "Sin clasificación", per docs/api-informe-json.md) act as
 *  the fallback signal.
 *
 *  M4 (fixed): `clasificacion` is normalized with `.replace(/[\s-]+/g, '_')`
 *  AFTER normalize(), same as the two EXISTING readers of this field
 *  (evaluaciones.js's claseDe, report.js's evalClaseLabel) — this codebase
 *  already treats space/hyphen drift on this field as real drift, not
 *  noise, so "Uso restringido"/"uso-restringido"/"uso_restringido" must all
 *  match the same code.
 *
 *  Case/accent-insensitive (normalize(), same helper the rest of this module
 *  already uses). Never throws: blank/null/unrecognized input always falls
 *  through to the neutral badge, same "fail-soft, never fabricate" invariant
 *  as DASH. */
export function badgeStyleFor(colorEtiqueta, clasificacion) {
  const clase = normalize(clasificacion).replace(/[\s-]+/g, '_');
  if (clase === 'inspeccionado' || clase === 'inspeccionada') {
    return { ...BADGE_STYLES.inspeccionado };
  }
  if (clase === 'uso_restringido') {
    return { ...BADGE_STYLES.restringido };
  }
  if (clase === 'inseguro' || clase === 'peligro_colapso') {
    return { ...BADGE_STYLES.inseguro };
  }
  // clasificacion was blank/unrecognized -> fall back to colorEtiqueta.
  const label = normalize(colorEtiqueta);
  if (label === 'habitable') {
    return { ...BADGE_STYLES.inspeccionado };
  }
  if (label === 'acceso restringido') {
    return { ...BADGE_STYLES.restringido };
  }
  if (label === 'no habitable') {
    return { ...BADGE_STYLES.inseguro };
  }
  return { ...BADGE_STYLES.neutral };
}

/** One colored pdfmake table cell for a sticker classification badge —
 *  suitable as a `pointsTable`/`cappedPointsTable` cell (they now pass
 *  object cells through untouched, see pointsTable above). */
export function badgeCell(text, style) {
  return {
    text: String(text),
    color: style.color,
    fillColor: style.bg,
    alignment: 'center',
    bold: true,
    fontSize: 8,
    margin: [2, 2, 2, 2],
  };
}

/** One KPI "stat card": a colored, bordered single-cell table containing a
 *  stacked label + value (+ optional caption below, e.g. the "objetivo
 *  diario" card's "Proyectado al …" methodology note). `accent: true` is the
 *  Borrador/Profesional mockups' highlighted blue "objetivo diario" card
 *  (`#e8f0fc` bg / `#2186E0` value text); otherwise the plain gray card
 *  (`#f0f2f7` bg / `#e0e3ea` border / `#151F55` value text) every other
 *  metric uses. Pure pdfmake node construction — no pdfmake dependency, no
 *  randomness/identity quirks, so buildMassReportDocDefinition's per-
 *  professional equality guarantee (same inputs -> deepEqual output) holds. */
export function statCard(label, value, { accent = false, caption } = {}) {
  const stack = [
    { text: String(label), fontSize: 8, color: '#555555', margin: [0, 0, 0, 2] },
    { text: String(value), fontSize: 14, bold: true, color: accent ? REPORT_ACCENT_COLOR : REPORT_HEADER_COLOR },
  ];
  if (caption) stack.push({ text: String(caption), fontSize: 7, color: '#888888', margin: [0, 2, 0, 0] });
  return {
    table: {
      widths: ['*'],
      body: [[{ stack, fillColor: accent ? REPORT_ACCENT_BG : REPORT_CARD_BG, margin: [8, 8, 8, 8] }]],
    },
    layout: REPORT_CARD_LAYOUT,
    margin: [0, 0, 8, 8],
  };
}

/** Chunks `cards` (statCard(...) nodes) into pdfmake `columns` rows of
 *  `perRow` — so N cards always render as a proper grid regardless of count
 *  (the last, partial row is never padded with empty cells).
 *
 *  L9 (fixed): every card, in every row, now carries an explicit
 *  `width: '<100/perRow>%'` — a `columns` entry with no explicit width
 *  defaults to pdfmake's `'*'` (share of remaining space), so a trailing
 *  partial row (e.g. 4 cards at perRow=3 -> a 2-card final row) used to
 *  render those cards WIDER than the full rows above it, visually
 *  inconsistent. The explicit width keeps every card the same size
 *  regardless of how many share its row. */
export function statCardsRow(cards, perRow = 3) {
  const width = `${Math.floor(100 / perRow)}%`;
  const rows = [];
  for (let i = 0; i < cards.length; i += perRow) {
    const row = cards.slice(i, i + perRow).map((card) => ({ ...card, width }));
    rows.push({ columns: row, columnGap: 8, margin: [0, 0, 0, 0] });
  }
  return rows;
}

/** A section title + a thin colored bottom rule (canvas line), mirroring
 *  both mockups' `border-bottom: 2px solid #151F55` section-header
 *  treatment — replaces the plain bold-text-only `sectionHeader` style used
 *  everywhere in this report. Returns a stack-like array, spread into
 *  `content` the same way kvTable/pointsTable already are. */
export function sectionHeaderNode(text) {
  return [
    { text: String(text), style: 'sectionHeader' },
    {
      canvas: [{
        type: 'line', x1: 0, y1: 0, x2: REPORT_CONTENT_WIDTH, y2: 0, lineWidth: 1.5, lineColor: REPORT_HEADER_COLOR,
      }],
      margin: [0, -2, 0, 6],
    },
  ];
}

/** The new graphical element (present in NEITHER mockup): a bounded,
 *  deterministic bar chart of sticker activity per day, built straight from
 *  `stickerPoints`' own `fecha` (already a Bogotá calendar-day string —
 *  dateOnly()/bogotaParts() already ran upstream in professionalRecords/
 *  toStickerPoint, so this never re-derives timezone logic, just buckets an
 *  already-resolved day string).
 *
 *  Chronological daily counts are built for EVERY day between the first and
 *  last dated point (via the same shiftDateStr helper buildTimeline already
 *  uses) — a day with zero stickers in the middle of the range still gets
 *  its own (1px baseline tick) bar, so "no data that day" (impossible once
 *  there's a range) is never visually confused with "confirmed zero that
 *  day" (this project's standing fail-soft invariant, same reasoning as
 *  DASH).
 *
 *  L10: a day-span guard runs BEFORE the daily-array walk — a single
 *  malformed `fecha` far outside any sane range (e.g. a stray "9999-12-31")
 *  would otherwise make that walk allocate a multi-million-entry array; this
 *  builder now runs ONCE PER PROFESSIONAL in the mass export (up to ~110x),
 *  unlike the pre-existing single global buildTimeline call, so a >4000-day
 *  (~11 year) span is treated as degenerate/unparseable input and falls back
 *  to the same "no data to graph" safe path as blank dates, rather than
 *  hanging or exhausting memory.
 *
 *  L8: when the range spans more days than `maxBars`, days are grouped into
 *  exactly `min(daily.length, maxBars)` buckets by INDEX (`i * L / n`), never
 *  a fixed Math.ceil(L / maxBars) bucket SIZE — the old size-based approach
 *  wasted up to ~47% of available bar resolution right at the maxBars+1
 *  boundary (30 days -> 30 bars, but 31 days -> only 16 bars, since
 *  ceil(31/30)=2 forced every bucket to hold 2 days). Index-based boundaries
 *  give a smooth bar count that only drops below maxBars once the range
 *  itself is shorter than maxBars days, and — being a complete, non-
 *  overlapping partition of `daily` — never drops or double-counts a day.
 *
 *  H2: each bar's height is driven by a per-day RATE (bucket sum / bucket
 *  day count), never a raw SUM — summing a variable-length bucket (in
 *  particular the old size-based approach's shorter final remainder) drew a
 *  systematically shorter final bar even when daily output was perfectly
 *  constant, misreading as "activity collapsed" when nothing changed.
 *  Scaling against the max RATE (not the max sum) keeps constant-rate
 *  buckets visually equal regardless of how many days each spans.
 *
 *  M5: the return value also carries `bucketSize` (the effective, rounded
 *  days-per-bar) so the caller's caption can describe a multi-day bucket
 *  accurately instead of unconditionally claiming one bar = one day.
 *
 *  0 stickers with a resolvable date -> a `{ canvas: [...], bucketSize }`
 *  node (bars bottom-aligned, height proportional to rate, color
 *  `COLORS.accent` — the app's own accent, not an invented one). 0 stickers
 *  AT ALL (or every point's fecha unresolvable/degenerate) -> an explanatory
 *  `{ text }` node instead of an empty/misleading canvas — NEVER throws. */
export function buildActivitySparkline(stickerPoints, { maxBars = 30, width = REPORT_CONTENT_WIDTH, height = 40 } = {}) {
  const dates = (Array.isArray(stickerPoints) ? stickerPoints : [])
    .map((p) => p && p.fecha)
    .filter(Boolean)
    .sort();
  if (!dates.length) return { text: 'Sin actividad para graficar.', style: 'fieldValue', italics: true };

  const first = dates[0];
  const last = dates[dates.length - 1];

  // L10: guard BEFORE walking the (potentially huge) day range.
  const MAX_SPARKLINE_SPAN_DAYS = 4000;
  const span = daysBetween(first, last);
  if (span === null || span > MAX_SPARKLINE_SPAN_DAYS) {
    return { text: 'Sin actividad para graficar.', style: 'fieldValue', italics: true };
  }

  const counts = new Map();
  for (const d of dates) counts.set(d, (counts.get(d) || 0) + 1);

  const daily = [];
  let cursor = first;
  while (cursor <= last) {
    daily.push(counts.get(cursor) || 0);
    cursor = shiftDateStr(cursor, 1);
  }

  // L8: index-based bucket boundaries — a complete, non-overlapping
  // partition of `daily` into exactly `n` buckets.
  const n = Math.max(1, Math.min(daily.length, maxBars));
  const bucketSums = [];
  const bucketDayCounts = [];
  for (let i = 0; i < n; i += 1) {
    const startIdx = Math.floor((i * daily.length) / n);
    const endIdx = Math.floor(((i + 1) * daily.length) / n);
    const slice = daily.slice(startIdx, endIdx);
    bucketDayCounts.push(slice.length);
    bucketSums.push(slice.reduce((a, b) => a + b, 0));
  }

  // H2: rate (sum / day count) per bucket, scaled against the max RATE.
  const rates = bucketSums.map((sum, i) => sum / bucketDayCounts[i]);
  const maxRate = Math.max(1e-9, ...rates);
  const gap = 2;
  const barW = Math.max(1, width / n - gap);
  const usableH = Math.max(height - 4, 4);
  const canvas = rates.map((rate, i) => {
    // A zero-rate bucket still renders a 1px baseline tick (never omitted);
    // a real (non-zero) rate is always visibly taller (>= 2px) so the two
    // cases stay distinguishable at a glance, never both collapsing to the
    // same 1px sliver.
    const h = rate > 0 ? Math.max(2, Math.round((rate / maxRate) * usableH)) : 1;
    return {
      type: 'rect', x: i * (barW + gap), y: height - h, w: barW, h, color: COLORS.accent,
    };
  });

  // M5: effective, rounded days-per-bar for the caller's caption.
  const bucketSize = Math.max(1, Math.round(daily.length / n));
  return { canvas, bucketSize };
}

/** Pure builder: a professional's row (buildProfessionalRows output, already
 *  carrying the W9 avgStickersPerDay/barriosActivos/temporal fields) + their
 *  raw points (professionalRecords output, already period-filtered by the
 *  caller's `from`/`to`) + `ctx` -> pdfmake document definition. Same style
 *  tokens as report.js's builders, so this report reads as part of the same
 *  family of PDFs the app already generates.
 *
 *  `ctx = { from, to, generatedAt, objetivoDiario, degraded, today, last7 }`
 *  — every field optional/defaulted:
 *   - `degraded: true` REFUSES to build (throws) rather than ship a report
 *     built from redacted/incomplete identities (D5/plan fail-soft matrix)
 *     — the caller (DOM section) never even reaches pdfMake.createPdf() in
 *     that case, so no partial/misleading file is ever produced;
 *   - `objetivoDiario` (objetivoDiario()'s own return value, precomputed by
 *     the caller — this builder stays pure, no fetch) renders "sin dato"
 *     when null/non-finite, never a fabricated number;
 *   - `last7` (visitasUltimos7Dias()'s own return value, same reasoning —
 *     precomputed by the caller) defaults to 0 when not a finite number.
 *  `from`/`to`/`generatedAt`/`today` are accepted for forward-compat/caller
 *  bookkeeping (mass export's per-professional loop threads the same ctx to
 *  every builder call) but this function reads dates straight off `row`/
 *  `points`, which the caller already filtered to the right period. */
export function buildProfessionalReportDocDefinition(row, { stickerPoints, surveyPoints }, ctx = {}) {
  if (ctx.degraded) {
    throw new Error('No se puede generar el informe: origen de datos degradado (identidades no disponibles).');
  }
  const caveat = row.rosterSourced > 0
    ? [{ text: `⚠ ${row.rosterSourced} sticker(s) con identidad completada por roster (aproximada, no verificada contra la evaluación).`, style: 'caveat', margin: [0, 0, 0, 8] }]
    : [];
  // W11: the accent "objetivo diario" stat card shows the NUMBER alone as its
  // value (or 'sin dato' when not finite — never a fabricated number) and the
  // "Proyectado al …" phrasing as its own caption underneath — same two
  // pieces of information the old single concatenated string carried, now
  // split to match the mockups' card layout (value + small gray caption).
  const objetivoValue = Number.isFinite(ctx.objetivoDiario) ? String(ctx.objetivoDiario) : 'sin dato';
  const objetivoCaption = `Proyectado al ${OBJETIVO_DEADLINE_LABEL}`;
  const last7 = Number.isFinite(ctx.last7) ? ctx.last7 : 0;
  // M5: buildActivitySparkline now aggregates multiple days per bar once the
  // period exceeds its default maxBars — the OLD caption ("Stickers
  // registrados por día") unconditionally implied one bar = one day, which
  // is false for any multi-month range (e.g. a 120-day range -> 4-day
  // buckets). The caption now reads off the sparkline's own `bucketSize`
  // (undefined when there's no data to graph, in which case no caption is
  // rendered at all — see the `sparkline.canvas` guard in `content` below).
  const sparkline = buildActivitySparkline(stickerPoints);
  const sparklineCaption = sparkline.bucketSize > 1
    ? `Promedio diario de stickers (agrupado cada ${sparkline.bucketSize} días).`
    : 'Promedio diario de stickers.';
  const visitasPeriodo = stickerPoints.length + surveyPoints.length;
  const stickersNumbered = stickerPoints.map((p, i) => {
    const style = badgeStyleFor(p.colorEtiqueta, p.clasificacion);
    return [i + 1, p.codigo, p.faseLabel, badgeCell(style.label, style), p.fecha || 'Sin fecha'];
  });
  return {
    content: [
      { text: `Informe de seguimiento — ${row.name || 'Sin dato'}`, style: 'title' },
      {
        canvas: [{
          type: 'line', x1: 0, y1: 0, x2: REPORT_CONTENT_WIDTH, y2: 0, lineWidth: 1.5, lineColor: REPORT_HEADER_COLOR,
        }],
        margin: [0, 2, 0, 6],
      },
      // W11: Tarjeta profesional (TP) is now ALSO shown here, inline, so it's
      // visible at a glance without opening "Datos del profesional" further
      // down — the field itself already existed (row.tarjetaProfesional);
      // this only makes it more prominent, it stays in the kvTable below too.
      { text: `Cédula: ${row.cedula || 'Sin dato'} · TP: ${row.tarjetaProfesional || DASH} · Código: ${row.codigo || 'Sin dato'} · Entidad: ${row.entidad || 'Sin dato'}`, style: 'subtitle' },
      // M4: prefer ctx.generatedAt (set once per render pass by the caller,
      // buildReportCtx) over calling downloadStamp() again here — the mass
      // export shares ONE ctx across every professional (buildMassReport
      // DocDefinition), so without this every professional's own solo-vs-
      // mass comparison depended on two downloadStamp() calls landing in
      // the same minute; ctx.generatedAt makes it byte-identical by
      // construction instead of by (usual) luck. Falls back to downloadStamp()
      // only when no ctx.generatedAt was given (e.g. calling this directly
      // in a test, or a future caller that hasn't been updated).
      { text: `Fecha de generación: ${ctx.generatedAt || downloadStamp().legible}`, style: 'subtitle' },
      ...caveat,
      ...sectionHeaderNode('Datos del profesional'),
      ...kvTable([
        ['Nombre', row.name || DASH],
        ['Cédula', row.cedula || DASH],
        ['Tarjeta profesional', row.tarjetaProfesional || DASH],
        // D-ENFASIS: only a row seeded from the depurado base has the key; a legacy report is unchanged.
        ...(typeof row.enfasis === 'string' ? [['Énfasis', row.enfasis || DASH]] : []),
        ['Clase (P) / Código vigente', `${row.np || DASH} / ${row.codigo || DASH}`],
        ['Celular', row.celular || DASH],
        ['Correo', row.correo || DASH],
        ['Barrios activos (7 d)', (row.barriosActivos && row.barriosActivos.length) ? row.barriosActivos.join(', ') : DASH],
      ]),
      ...sectionHeaderNode('Métricas de seguimiento'),
      ...statCardsRow([
        statCard('Visitas totales en el período', visitasPeriodo),
        statCard('Visitas últimos 7 días (a hoy)', last7),
        statCard('Promedio diario de stickers', Number.isFinite(row.avgStickersPerDay) ? row.avgStickersPerDay : DASH),
        statCard('Día inicio', row.firstDate || DASH),
        statCard('Fecha última visita', row.lastDate || DASH),
        // M6: the deadline caption is only meaningful alongside a REAL
        // projected value — attaching "Proyectado al …" when objetivoDiario
        // is null/non-finite (the card shows "sin dato") would be fabricated
        // confidence about a projection that doesn't exist.
        statCard('Visita objetivo diario', objetivoValue, {
          accent: true,
          caption: Number.isFinite(ctx.objetivoDiario) ? objetivoCaption : undefined,
        }),
      ]),
      // W11: the new graphical element — present in NEITHER mockup — a
      // bounded daily-activity bar chart built from the same stickerPoints
      // already listed below, so the two sections never disagree.
      ...sectionHeaderNode('Actividad en el período'),
      ...(sparkline.canvas
        ? [{ text: sparklineCaption, style: 'fieldValue', margin: [0, 0, 0, 4] }, sparkline]
        : [sparkline]),
      ...sectionHeaderNode(`Listado de stickers del período (${stickerPoints.length})`),
      ...cappedPointsTable(
        ['#', 'Número', 'Fase', 'Clasificación', 'Fecha'],
        stickersNumbered,
        'Sin registros de stickers.',
      ),
      // Borrador mockup's footer line under the sticker table — the
      // UNCAPPED total (never the capped/shown-at-most-50 count), so a very
      // active professional's true volume is never understated.
      { text: `Total stickers en período: ${stickerPoints.length}`, style: 'disclaimer', alignment: 'right', margin: [0, -6, 0, 10] },
      ...sectionHeaderNode(`Puntos recogidos — Survey (${surveyPoints.length})`),
      ...pointsTable(
        ['Dirección', 'Edificación', 'Fecha'],
        surveyPoints.map((p) => [p.direccion, p.nombreEdificacion, p.fecha || 'Sin fecha']),
        'Sin registros de Survey.',
      ),
      // Footer (both mockups have one): the pre-existing disclaimer, now
      // visually separated as a real footer (thin light-gray top rule)
      // instead of sitting at the top of the page — content unchanged, only
      // its position/presentation moved. M7: the confidentiality notice is
      // NO LONGER here — it moved to the top-level `footer:` function below
      // so it repeats on EVERY page, not just wherever this content block
      // happened to end.
      {
        canvas: [{
          type: 'line', x1: 0, y1: 0, x2: REPORT_CONTENT_WIDTH, y2: 0, lineWidth: 1, lineColor: '#dddddd',
        }],
        margin: [0, 14, 0, 6],
      },
      { text: REPORT_DISCLAIMER, style: 'disclaimer', margin: [0, 0, 0, 4] },
    ],
    // M7: repeats REPORT_CONFIDENTIALITY_NOTICE on EVERY page — a stable,
    // module-scope function reference (see reportFooter's own comment for
    // why this must never be a fresh closure per call).
    footer: reportFooter,
    styles: {
      title: { fontSize: 16, bold: true, color: REPORT_HEADER_COLOR },
      subtitle: { fontSize: 9, color: '#555' },
      disclaimer: { fontSize: 8, italics: true, color: '#777' },
      caveat: { fontSize: 9, italics: true, color: '#a15c00' },
      sectionHeader: { fontSize: 12, bold: true, color: REPORT_HEADER_COLOR, margin: [0, 10, 0, 2] },
      fieldLabel: { fontSize: 9, bold: true },
      fieldValue: { fontSize: 9 },
      tableHeader: { fontSize: 9, bold: true, fillColor: '#eeeeee' },
    },
    defaultStyle: { fontSize: 9 },
  };
}

/** Mass export (D5, plan §Decisiones): ONE pdfmake document spanning every
 *  professional, `pageBreak: 'before'` on each professional's FIRST content
 *  node except the very first professional — built incrementally (list.
 *  forEach, one buildProfessionalReportDocDefinition() call per entry) so
 *  the individual and mass reports are GUARANTEED to render identical
 *  per-professional content (they share the exact same builder, never a
 *  second/parallel "mass" layout that could drift). `rowsWithPoints` is
 *  `[{ row, points, last7 }, …]` (points = professionalRecords() output for
 *  that row/period, last7 = visitasUltimos7Dias() for that row — both
 *  precomputed by the caller, same reasoning as ctx.objetivoDiario above).
 *  `ctx` is the SAME ctx buildProfessionalReportDocDefinition takes, applied
 *  to every professional (only `last7` varies per entry — everything else,
 *  incl. `degraded`/`objetivoDiario`, is a property of the whole export, not
 *  of one professional). 0 professionals -> one informational page, never an
 *  empty `content` array (pdfmake would otherwise render nothing at all,
 *  indistinguishable from a silent failure). */
export function buildMassReportDocDefinition(rowsWithPoints, ctx = {}) {
  if (ctx.degraded) {
    throw new Error('No se puede generar el informe: origen de datos degradado (identidades no disponibles).');
  }
  const list = Array.isArray(rowsWithPoints) ? rowsWithPoints : [];
  if (!list.length) {
    return {
      content: [{ text: 'Sin profesionales para los filtros seleccionados', style: 'emptyMessage' }],
      styles: { emptyMessage: { fontSize: 14, bold: true, alignment: 'center', margin: [0, 60, 0, 0] } },
      defaultStyle: { fontSize: 9 },
    };
  }
  let styles = null;
  let defaultStyle = null;
  // M7: the mass export is ONE pdfmake docDefinition spanning every
  // professional — each buildProfessionalReportDocDefinition() call carries
  // its own `footer` (the confidentiality notice), but only `content` gets
  // merged node-by-node above; `footer`/`styles`/`defaultStyle` are
  // properties of the WHOLE document, so they're captured once (from the
  // first professional — every professional shares the same `footer`
  // reference, see reportFooter's own comment) and attached at the top
  // level, the same way styles/defaultStyle already were, so the
  // confidentiality footer repeats on every physical page of the mass PDF,
  // not just page 1.
  let footer = null;
  const content = [];
  list.forEach((entry, i) => {
    const single = buildProfessionalReportDocDefinition(entry.row, entry.points, { ...ctx, last7: entry.last7 });
    if (!styles) { styles = single.styles; defaultStyle = single.defaultStyle; footer = single.footer; }
    single.content.forEach((node, idx) => {
      content.push(i > 0 && idx === 0 ? { ...node, pageBreak: 'before' } : node);
    });
  });
  return {
    content, styles, defaultStyle, footer,
  };
}

/** Sorts a copy of `rows` by `column`, ascending or descending. String
 *  columns compare with localeCompare (es); numeric/null columns compare
 *  numerically, with a null/undefined value sorting as the lowest ("-∞",
 *  never crashing on a missing firstDate/lastDate). Ties break by `key` so
 *  the result is deterministic regardless of the input's original order. */
/** Whether the professional search box, the Desde/Hasta date range, OR the
 *  `seg-chart-professional` select is currently narrowing the table — drives
 *  "Reiniciar filtros"' enabled/disabled + soft-orange state.
 *
 *  CONTRATO CAMBIADO (W7): `seg-chart-professional` used to be excluded here
 *  because it only picked which line the Ritmo diario chart highlighted,
 *  never the table. W7 makes it ALSO narrow the table (label renamed to
 *  "Profesional (gráfico y tabla)"), so it is now a genuine data-narrowing
 *  filter and must count toward this — "Reiniciar filtros" must both clear
 *  it AND re-enable/disable based on it, same as search/from/to. Sort order
 *  is still excluded (never a data-narrowing filter). Exported so a
 *  self-check can cover the transitions without the DOM. */
export function hasActiveSegFilters({
  search = '', from = null, to = null, professional = null, estado = ESTADO_ALL,
} = {}) {
  // Phase 11: the estado_sugerido filter narrows the table too ("all" — or
  // an empty value — is the no-op default).
  return Boolean(search || from || to || professional || (estado && estado !== ESTADO_ALL));
}

/** The estado_sugerido filter's "no narrowing" value (its default). */
export const ESTADO_ALL = 'all';

/** Options of the estado_sugerido filter (Phase 11, spec "Estado Sugerido
 *  Filter And Column"): "all" first (the default), then the four values the
 *  backend emits. A value outside this list is still LISTED under "all" — the
 *  table never hides a row because of an estado the frontend has no label for. */
export const ESTADO_FILTER_OPTIONS = Object.freeze([
  { value: ESTADO_ALL, label: 'Todos' },
  { value: 'activo', label: 'Activo' },
  { value: 'revisar', label: 'Revisar' },
  { value: 'candidato_desactivacion', label: 'Candidato a desactivación' },
  { value: 'no_persona', label: 'No es persona' },
]);

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

// ── W7: KPI/search/filter pure helpers ──────────────────────────────────────
// DASH masks a value that is genuinely UNKNOWN (still loading, or a failed
// fetch) — never a real 0. Moved up from the DOM section (still the same
// sentinel value) and exported so the pure helpers below, and a self-check,
// can reference/assert it directly instead of duplicating the literal '—'.
export const DASH = '—';

// ── W9: two-sub-tab table (Totales / Análisis temporales) + XLSX ───────────
// "Acciones" is deliberately NOT part of either array (same convention as the
// pre-W9 COLUMNS): it is a row action, never sortable/exportable data — the
// DOM section appends its own "Acciones" header/cell after whichever of
// these two columnsFor() returns.
export const COLUMNS_TOTALES = [
  { key: 'name', label: 'Nombre' },
  { key: 'cedula', label: 'Cédula' },
  // W-TP (user request, 2026-09-15): tarjeta profesional is now shown
  // inline in the Seguimiento tab itself, not just in the per-professional
  // PDF report — see xlsxRowsFor's totales mapping and cellHtml below.
  { key: 'tarjetaProfesional', label: 'Tarjeta profesional' },
  { key: 'np', label: 'Clase (P)' },
  { key: 'codigo', label: 'Código vigente' },
  { key: 'stickersFase1', label: 'Sticker F1' },
  { key: 'stickersFase2', label: 'Sticker F2' },
  { key: 'surveyTotal', label: 'Ev. Survey' },
  { key: 'activeDays', label: 'Días activos' },
  { key: 'avgStickersPerDay', label: 'Stickers prom. diario' },
  { key: 'barriosActivos', label: 'Barrios activos (7 d)' },
];

// L10: the four hour-of-day columns (and daysSinceFirst) below now respect
// the active Desde/Hasta filter (buildTemporalMetricsByKey), same as every
// OTHER column in this sub-tab — `title` documents that on the header itself
// (rendered as the <button>'s title attribute by headerRowHtml) so it's
// visible on hover, not just in this source comment.
const RANGE_AWARE_HOUR_TITLE = 'Calculado sobre el rango Desde–Hasta activo (si hay uno seleccionado).';
export const COLUMNS_TEMPORALES = [
  { key: 'name', label: 'Nombre' },
  { key: 'cedula', label: 'Cédula' },
  { key: 'np', label: 'Clase (P)' },
  { key: 'codigo', label: 'Código' },
  { key: 'firstDate', label: 'Fecha primer registro' },
  { key: 'lastDate', label: 'Fecha último registro' },
  { key: 'activeDays', label: 'Días activo' },
  { key: 'daysSinceFirst', label: 'Días desde 1ª actividad' },
  { key: 'prevDayFirstMinutes', label: 'Hora 1er registro (día ant.)', title: RANGE_AWARE_HOUR_TITLE },
  { key: 'prevDayLastMinutes', label: 'Hora últ. registro (día ant.)', title: RANGE_AWARE_HOUR_TITLE },
  { key: 'avgFirstMinutes', label: 'Hora prom. 1er registro', title: RANGE_AWARE_HOUR_TITLE },
  { key: 'avgLastMinutes', label: 'Hora prom. últ. registro', title: RANGE_AWARE_HOUR_TITLE },
];

/** Which column set a sub-tab shows — an unrecognized/missing `subTab` falls
 *  back to 'totales' (never throws, never renders a headerless table). */
export function columnsFor(subTab, { withEstado = false, withEnfasis = false } = {}) {
  if (subTab === 'temporales') return COLUMNS_TEMPORALES;
  if (!withEstado && !withEnfasis) return COLUMNS_TOTALES;
  // Phase 11: the estado_sugerido column exists only when the table is fed by
  // an active depuracion (on the legacy path every estado is '' — a column of
  // "Sin dato" would be noise). Right after "Clase (P)". D-ENFASIS: same gate
  // for the registry's "Énfasis" (a separate flag, same caller value), right
  // after "Tarjeta profesional".
  const columns = [];
  for (const column of COLUMNS_TOTALES) {
    columns.push(column);
    if (withEnfasis && column.key === 'tarjetaProfesional') columns.push(COLUMN_ENFASIS);
    if (withEstado && column.key === 'np') columns.push(COLUMN_ESTADO);
  }
  return columns;
}
const COLUMN_ESTADO = { key: 'estadoSugerido', label: 'Estado sugerido' };
const COLUMN_ENFASIS = { key: 'enfasis', label: 'Énfasis' };

/** The sort state a sub-tab opens with (D4: header-click sorting is
 *  preserved when the CURRENT sort column still exists in the new sub-tab's
 *  columnsFor() — this is only the FALLBACK the DOM layer uses otherwise, or
 *  on first render).
 *
 *  M5 (bug fix): the default for 'totales' USED to be `{ column: 'total' }`
 *  — but 'total' is NOT one of COLUMNS_TOTALES' own keys (it's a real field
 *  on every row, just never a visible header in this sub-tab). headerRowHtml
 *  only shows the ▲/▼ sort indicator on a `<th>` whose OWN key matches
 *  `sortState.column`, and the header-click handler only ever toggles by
 *  clicking a `data-seg-sort` button that carries a COLUMNS_TOTALES key — so
 *  the default sort silently had NO visible indicator and could never be
 *  toggled back to via a header click. 'stickersFase1' (most active
 *  professionals by Fase I count first) IS a real COLUMNS_TOTALES header.
 *  'firstDate' desc (most recently STARTED professionals first) for
 *  temporales, unchanged. An unrecognized subTab defaults like 'totales',
 *  same fallback as columnsFor. */
export function defaultSortFor(subTab) {
  return subTab === 'temporales'
    ? { column: 'firstDate', dir: 'desc' }
    : { column: 'stickersFase1', dir: 'desc' };
}

/** A minute-of-day value (0-1439) as 'HH:MM', or DASH for anything that isn't
 *  a genuine minute of a day (null/undefined/NaN, or out of the [0, 1439]
 *  range) — never a bogus/negative time silently rendered. Shared by the
 *  table's temporal-column cells AND the temporales XLSX sheet, so both
 *  present hour-of-day figures identically. */
export function formatMinutes(min) {
  if (!Number.isFinite(min) || min < 0 || min > 1439) return DASH;
  // L9: round ONCE, up front, to a whole minute, THEN split into hour/minute
  // — flooring the hour and separately rounding the leftover minute (the old
  // order) could round the MINUTE PART past 59 without ever carrying into
  // the hour (e.g. 719.6 -> floor(719.6/60)=11, round(719.6%60)=round(59.6)
  // =60 -> the bogus "11:60" instead of rolling over to "12:00").
  const t = Math.round(min);
  const h = Math.floor(t / 60);
  const m = t % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** Pure mapper: professional rows (buildProfessionalRows output, already
 *  carrying the W9 avgStickersPerDay/temporal-metrics fields) -> plain
 *  objects ready for XLSX.utils.sheet_add_json, one shape per sub-tab sheet.
 *  EVERY returned row carries the EXACT SAME key set regardless of how much
 *  data that particular professional has (`?? ''` on every optional field) —
 *  a sparse professional (no contact info, no timed record at all) must
 *  never produce a row with fewer/different columns than a fully-populated
 *  one, which would silently shift every OTHER column in a spreadsheet
 *  viewer. The 'totales' sheet keeps today's existing field names (see the
 *  XLSX download handler below) and only ADDS the new W5/W9 fields —
 *  nothing existing silently renames/disappears. */
export function xlsxRowsFor(rows, { subTab = 'totales', withEnfasis = false } = {}) {
  const list = Array.isArray(rows) ? rows : [];
  if (subTab === 'temporales') {
    return list.map((r) => ({
      profesional: r.name ?? '',
      cedula: r.cedula ?? '',
      clase_p: r.np ?? '',
      codigo: r.codigo ?? '',
      fecha_primer_registro: r.firstDate ?? '',
      fecha_ultimo_registro: r.lastDate ?? '',
      dias_activo: r.activeDays ?? 0,
      dias_desde_primera_actividad: r.daysSinceFirst ?? '',
      hora_1er_registro_dia_ant: formatMinutes(r.prevDayFirstMinutes),
      hora_ult_registro_dia_ant: formatMinutes(r.prevDayLastMinutes),
      hora_prom_1er_registro: formatMinutes(r.avgFirstMinutes),
      hora_prom_ult_registro: formatMinutes(r.avgLastMinutes),
    }));
  }
  // H1 (superseded for TP, W-TP, 2026-09-15): the plan's W9 originally never
  // listed ANY contact field (tarjeta_profesional/celular/correo) for the
  // XLSX -- only W10's per-professional PDF carried them, alongside its own
  // REPORT_CONFIDENTIALITY_NOTICE. The user has since explicitly asked for
  // tarjeta profesional to be visible in the Seguimiento tab itself (see
  // COLUMNS_TOTALES above), so it is now included here too. `celular`/
  // `correo` stay PDF-only: H1's original reasoning still applies to those
  // two — a spreadsheet is far more likely to be forwarded/copied around
  // unattended than a single PDF click.
  return list.map((r) => ({
    profesional: r.name ?? '',
    cedula: r.cedula ?? '',
    tarjeta_profesional: r.tarjetaProfesional ?? '',
    // D-ENFASIS: only in a sheet exported from the depurado base (same gate as the table column).
    ...(withEnfasis ? { enfasis: r.enfasis ?? '' } : {}),
    clase_p: r.np ?? '',
    codigo_vigente: r.codigo ?? '',
    entidad: r.entidad ?? '',
    stickers_fase1: r.stickersFase1 ?? 0,
    stickers_fase2: r.stickersFase2 ?? 0,
    stickers_total: r.stickersTotal ?? 0,
    evaluaciones_survey: r.surveyTotal ?? 0,
    total: r.total ?? 0,
    primer_registro: r.firstDate ?? '',
    ultimo_registro: r.lastDate ?? '',
    // Null policy (nit, documented once here): `dias_activos` and
    // `promedio_por_dia` are never null in buildProfessionalRows' own output
    // (a row that exists always has a real, if degenerate, 0 for both when
    // it has zero dated records) -- `?? 0` is a type-safety fallback, not a
    // "missing data" sentinel. `stickers_promedio_diario` (and, in the
    // temporales sheet, `dias_desde_primera_actividad`/every hour-of-day
    // field) DOES use `null` as a genuine "no sticker-active day at all"
    // sentinel, so those use `?? ''` instead -- the SAME '' every other
    // "sin dato" cell in this sheet uses, never a bare 0 that would read as
    // a confirmed zero pace.
    dias_activos: r.activeDays ?? 0,
    promedio_por_dia: r.avgPerActiveDay ?? 0,
    stickers_promedio_diario: r.avgStickersPerDay ?? '',
    // Nit: ', ' — same separator the table cell (cellHtml's 'barriosActivos'
    // case) already uses, so the table and the XLSX never read differently.
    barrios_activos_7d: Array.isArray(r.barriosActivos) && r.barriosActivos.length ? r.barriosActivos.join(', ') : '',
    stickers_por_roster: r.rosterSourced ?? 0,
    // Task 4.9 (seguimiento-inspectores-depurado, Fase 4; spec: "Table And
    // Export Reflect Depurado Fields Consistently") -- read straight off
    // `r`, the SAME backend-resolved fields the table/matchesSearch use;
    // '' (never a client re-derivation) whenever depuracion wasn't active
    // for this row, same "no data" convention as clase_p/codigo_vigente.
    fase: r.fase ?? '',
    estado_sugerido: r.estadoSugerido ?? '',
    fuente_dato: r.fuenteDato ?? '',
  }));
}

/** N13: one-line summary of the active filters at export time, for the XLSX
 *  header's "Filtros:" row — without it, an export narrowed by e.g. a
 *  professional selection or a date range looked identical to a full export
 *  (only "Registros: N" differed), which could be misread as the WHOLE
 *  dataset. `professionalName` is the resolved display name (never the raw
 *  `ced:…`/`nom:…` key), so the cell reads meaningfully in a spreadsheet. */
export function xlsxFiltersSummary({
  search = '', from = null, to = null, professionalName = '', estado = ESTADO_ALL,
} = {}) {
  const parts = [];
  const q = String(search || '').trim();
  if (q) parts.push(`Búsqueda: "${q}"`);
  if (from) parts.push(`Desde: ${from}`);
  if (to) parts.push(`Hasta: ${to}`);
  const prof = String(professionalName || '').trim();
  if (prof) parts.push(`Profesional: ${prof}`);
  // The estado_sugerido filter narrows the exported rows too (same test as
  // hasActiveSegFilters/visibleRowsFor); its label, or the raw value when the
  // frontend has no label for it.
  if (estado && estado !== ESTADO_ALL) {
    const option = ESTADO_FILTER_OPTIONS.find((o) => o.value === estado);
    parts.push(`Estado sugerido: ${option ? option.label : estado}`);
  }
  return parts.length ? parts.join('; ') : 'ninguno';
}

/** Shown while `isDegraded` (the sticker fetch fell back to the redacted LKG
 *  Blob copy). CONTRATO CAMBIADO (W7): the old text promised a "sin
 *  profesional identificado" KPI bucket that W7 removes as its own tile —
 *  rewritten to state the concrete degraded-count fact instead ("0 stickers
 *  atribuibles"), which stays true regardless of which tiles exist. */
export const DEGRADED_STICKERS_NOTE = 'Mostrando una copia de respaldo de los stickers: los nombres e identificaciones de los '
  + 'inspectores no están disponibles hasta reconectar la fuente en vivo — 0 stickers atribuibles (copia de respaldo sin identidades).';

/** The 5-tile KPI values (W7: "sin profesional identificado" and "stickers
 *  sin fecha" are no longer tiles — see DEGRADED_STICKERS_NOTE/unassignedNote/
 *  the sinfecha note for where those counts still surface, as disclosures
 *  instead of tiles) from a buildProfessionalRows() result.
 *
 *  `avgStickersPerDayPerProfessional` (displayed as "stickers/día por
 *  profesional", M6) is deliberately NOT `totals.avgPerProfessional` (which
 *  pools stickers+surveys across ALL professionals into one ratio) — it is
 *  the MEAN, across professionals WITH AT LEAST ONE STICKER-ACTIVE DAY, of
 *  each row's OWN `stickersTotal/stickerActiveDays` (M6: NOT the pooled
 *  `activeDays`, which also counts survey-only days and would dilute the
 *  pace). A professional with zero sticker-active days is EXCLUDED from the
 *  mean entirely (never folded in as a 0, never NaN/Infinity) — "stickers/día
 *  por profesional" reads as a per-professional STICKER pace, not a pooled
 *  stickers+surveys ratio one very active professional could dominate, and
 *  not diluted by colleagues who only ever did surveys. DASH when there ARE
 *  professionals but none has any sticker-active day at all.
 *
 *  `barriosActivos` is the count of DISTINCT barrios across EVERY
 *  professional's own `row.barriosActivos` (buildBarriosActivosByKey/D1) —
 *  deduped the same way that per-row list already is (accent/case/whitespace
 *  -insensitive), but now ACROSS professionals too (two professionals both
 *  reporting "San Antonio"/"SAN ANTONIO" must still count as one barrio).
 *
 *  `stickersLoaded` masks EVERY sticker-derived tile behind DASH — same
 *  reasoning as the old kpisHtml/rowHtml masking: a real 0/count would claim
 *  "confirmed" when the true state is "unknown, still loading or failed".
 *  That now includes (H3/L7, fixing a prior gap):
 *   - `stickers` / `avgStickersPerDayPerProfessional` (as before);
 *   - `barriosActivos` — entirely derived from sticker `barrio`s (D1), so a
 *     real 0 while stickers haven't resolved would read as "confirmed zero
 *     active barrios";
 *   - `professionals` (= rows.length from buildProfessionalRows) — a MIX of
 *     professionals resolved via stickers AND via Survey-only records, so
 *     while stickers are still loading/failed it is only a PARTIAL count
 *     (the Survey-only subset), never the real total.
 *  `surveys` is the only tile that is genuinely never sticker-derived, so it
 *  alone stays unmasked. */
export function kpiTotals(rowsResult, { stickersLoaded = true } = {}) {
  const allRows = (rowsResult && Array.isArray(rowsResult.rows)) ? rowsResult.rows : [];
  const totals = (rowsResult && rowsResult.totals) || {};
  // Phase 11 / D17: when the table is seeded (`totals.padron` is present) the
  // averages and the barrios count run over rows WITH activity only — a
  // seeded person with nothing in the range must not dilute them (barrios
  // are last-7-days, NOT range-bound, so a zero-activity row could still
  // carry some). Unseeded (legacy) rows all have activity: untouched.
  const seeded = Number.isFinite(totals.padron);
  const rows = seeded ? allRows.filter(rowHasActivity) : allRows;
  const professionals = totals.professionals || 0;
  const surveys = totals.surveys || 0;
  const stickersRaw = totals.stickers || 0;

  // M6: mean, ACROSS PROFESSIONALS WITH AT LEAST ONE STICKER-ACTIVE DAY, of
  // each row's own stickersTotal/stickerActiveDays -- a professional with
  // zero sticker-active days (survey-only, or literally no stickers) is
  // EXCLUDED from the mean entirely rather than folded in as a 0, which
  // would understate the pace of everyone who IS placing stickers. When
  // there ARE professionals but NONE has any sticker activity, there is no
  // pace to report at all -- DASH, not a lying 0. An empty row set (zero
  // professionals, period) is the one case that stays a real 0.
  let avgStickersPerDayPerProfessional = 0;
  if (rows.length) {
    const paced = rows.filter((r) => (r.stickerActiveDays || 0) > 0);
    if (!paced.length) {
      avgStickersPerDayPerProfessional = DASH;
    } else {
      const sum = paced.reduce((acc, r) => acc + r.stickersTotal / r.stickerActiveDays, 0);
      avgStickersPerDayPerProfessional = Math.round((sum / paced.length) * 100) / 100;
    }
  }

  const seenBarrios = new Map(); // normalized -> first-seen spelling (unused, only the count matters)
  for (const r of rows) {
    for (const b of (r.barriosActivos || [])) {
      const key = normalizeName(b);
      if (key && !seenBarrios.has(key)) seenBarrios.set(key, b);
    }
  }

  const result = {
    professionals: stickersLoaded ? professionals : DASH,
    stickers: stickersLoaded ? stickersRaw : DASH,
    surveys,
    avgStickersPerDayPerProfessional: stickersLoaded ? avgStickersPerDayPerProfessional : DASH,
    barriosActivos: stickersLoaded ? seenBarrios.size : DASH,
  };
  // The seeded total ("padrón") is its own figure, never conflated with
  // "profesionales activos"; absent (no key at all) on the legacy path.
  if (seeded) {
    result.padron = stickersLoaded ? totals.padron : DASH;
    // Only with a non-empty padrón (nothing to classify otherwise) and a real
    // figure (older/hand-built results may not carry it); masked like padrón.
    if (totals.padron > 0 && Number.isFinite(totals.inspectoresActivos)) {
      result.inspectoresActivos = stickersLoaded ? totals.inspectoresActivos : DASH;
      // Daily pace of the ACTIVE padrón: stickers of the active inspectors in
      // the range / days of the range / number of active inspectors. DASH for
      // anything that is not a real ratio (no active inspector, no valid range,
      // stickers still loading) — never NaN/Infinity/negative.
      const days = totals.rangoDias;
      const stickersActivos = totals.stickersInspectoresActivos;
      result.rangoDias = Number.isFinite(days) && days >= 1 ? days : DASH;
      result.stickersPorInspectorActivo = stickersLoaded
        && totals.inspectoresActivos > 0
        && Number.isFinite(days) && days >= 1
        && Number.isFinite(stickersActivos) && stickersActivos >= 0
        ? Math.round((stickersActivos / days / totals.inspectoresActivos) * 100) / 100
        : DASH;
    }
  }
  return result;
}

/** Whether `row` matches the free-text search box: a query with >=3 digits
 *  (after stripping every non-digit character) is treated as a CÉDULA search
 *  — `cedulaKey(row.cedula)` OR `cedulaKey(row.tarjetaProfesional)` must
 *  CONTAIN that digit run — else it's a NAME search — `normalize(row.name)`
 *  OR `normalize(row.tarjetaProfesional)` OR `normalize(row.np)` must
 *  contain `normalize(query)` (W-TP, 2026-09-15: tarjeta profesional is also
 *  searchable, same path as name; task 4.9/4.10, seguimiento-inspectores-
 *  depurado: `np` too — spec "Search matches the resolved np, not a stale
 *  client value" — `row.np` is already the backend-resolved value
 *  buildProfessionalRows carries, never re-derived here; D-ENFASIS: the
 *  registry's free-text `row.enfasis` is searched the same way, accent- and
 *  case-insensitively, on the name path only — a digit run never searches it). A short numeric
 *  query like "123" therefore never falls back to matching a name (it stays
 *  on the cédula path, which correctly fails against a blank/non-matching
 *  cedula) — the query is either "clearly a cédula fragment" or "clearly a
 *  name/np fragment", never ambiguously both. Blank/whitespace-only query
 *  matches every row (no filter active). */
export function matchesSearch(row, query) {
  const q = String(query === null || query === undefined ? '' : query).trim();
  if (!q) return true;
  const digits = cedulaKey(q);
  if (digits.length >= 3) {
    return cedulaKey(row && row.cedula).includes(digits)
      || cedulaKey(row && row.tarjetaProfesional).includes(digits);
  }
  return normalize((row && row.name) || '').includes(normalize(q))
    || normalize((row && row.tarjetaProfesional) || '').includes(normalize(q))
    || normalize((row && row.np) || '').includes(normalize(q))
    || normalize((row && row.enfasis) || '').includes(normalize(q));
}

/** The professional rows currently shown in the table: `rows` narrowed by
 *  the search box (matchesSearch) AND, since W7, by the SAME
 *  `seg-chart-professional` select the Ritmo diario chart uses (`row.key`
 *  strict equality — the identity resolver's own key, never a re-derived
 *  name) — both filters apply together (AND), not either/or. An empty/
 *  falsy `professionalKey` leaves every professional in. */
export function visibleRowsFor(rows, { query = '', professionalKey = '', estado = ESTADO_ALL } = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const key = professionalKey || '';
  // Phase 11: the estado_sugerido filter is one more AND term; "all" (or an
  // empty value) leaves every row in — including one whose estado is an
  // unexpected string. Exact string match otherwise.
  const narrowEstado = Boolean(estado) && estado !== ESTADO_ALL;
  return list.filter((r) => (!key || r.key === key)
    && (!narrowEstado || (r && r.estadoSugerido === estado))
    && matchesSearch(r, query));
}

/** The degraded-copy disclosure text (or `null`) — a dedicated pure function
 *  instead of inlining the ternary in renderStatusBanner, so its 4-state
 *  truth table (isDegraded × stickersLoaded) is directly assertable. Only
 *  BOTH true produces the note: `isDegraded` is only ever set once the
 *  sticker fetch has actually resolved (alongside `stickersLoaded = true`)
 *  in the real init flow, so `isDegraded && !stickersLoaded` is defensively
 *  null rather than showing a note about a fetch that hasn't settled yet. */
export function degradedStickerNote(isDegraded, stickersLoaded) {
  if (!isDegraded || !stickersLoaded) return null;
  return DEGRADED_STICKERS_NOTE;
}

/** Disclosure text reconciling the (no-longer-a-tile) "sin profesional
 *  identificado" sticker count with the visible stickers KPI/table — W7
 *  dropped that dedicated tile, so this note is how an admin still learns
 *  the stickers KPI includes N stickers no row in the table accounts for.
 *  Only the STICKER half of `unassigned` (buildProfessionalRows' own
 *  `{stickers, surveys}` bucket) is surfaced — the note's whole purpose is
 *  reconciling the sticker-count KPI specifically. `null` when there is
 *  nothing to disclose (0 unassigned stickers, or a missing/malformed
 *  input). */
export function unassignedNote(unassigned) {
  const n = (unassigned && unassigned.stickers) || 0;
  if (!n) return null;
  return `${n.toLocaleString('es-CO')} stickers sin profesional atribuible (sin nombre de inspector resolvible en el sticker); `
    + 'se cuentan en el KPI de stickers pero no aparecen en ninguna fila de la tabla.';
}

/** Task 4.8 (seguimiento-inspectores-depurado, Fase 4): freshness/degraded
 *  indicator for the identity source — `identity` is a buildIdentityIndex()
 *  result (reads its `depuracionActiva`/`depuracionMotivo`/
 *  `referenciaGeneradaEn` fields, always present regardless of branch — see
 *  buildIdentityIndex's own doc comment). Plain text (assigned via
 *  `.textContent`, same convention as degradedStickerNote/unassignedNote
 *  above — never HTML, never escaped here). `null` when there is nothing to
 *  disclose: no identity at all, or `loaded === false` (the sticker fetch is
 *  still in flight or failed: the depuracion block cannot be judged absent
 *  yet).
 *
 *  Phase 11 (spec "Degraded Or Absent Depuración Is Announced", corrected in
 *  PR 10 part 2): the degraded banner is driven ONLY by a depuracion block
 *  the SERVER sent with `activa:false` (its `motivo` is named; a block with no
 *  motivo names `sin_motivo`). A payload with NO block at all is SILENT — no
 *  banner, legacy render — because "no block" is the NORMAL state (backend
 *  flag off, request did not opt in, viewer role, older backend) and the
 *  frontend cannot tell those apart; an error-style banner there would appear
 *  for every admin the moment this ships, before anyone enabled anything.
 *  `loaded` (default true) is passed as `stickersLoaded` by render(). */
export function depuracionBadgeHtml(identity, { loaded = true } = {}) {
  if (!identity || !loaded) return null;
  if (identity.depuracionActiva) {
    return `Identidad depurada · referencia generada el ${identity.referenciaGeneradaEn || 'fecha desconocida'}.`;
  }
  if (identity.depuracionMotivo) {
    return `Identidad sin depurar (${identity.depuracionMotivo}) — mostrando datos crudos de la API.`;
  }
  if (identity.depuracionAusente === false) {
    return 'Identidad sin depurar (sin_motivo) — mostrando datos crudos de la API.';
  }
  return null;
}

/** Whether the depuracion badge is the DEGRADED banner (styled as a warning)
 *  rather than the neutral freshness line of an active depuracion. An absent
 *  block is neither: it renders no badge at all (see depuracionBadgeHtml). */
export function depuracionBadgeIsDegraded(identity) {
  return Boolean(identity) && !identity.depuracionActiva && identity.depuracionAusente !== true;
}

/** Task 4.4/4.5 (seguimiento-inspectores-depurado, Fase 4; spec: "Non-Person
 *  Group Row Is Expandable"): the GRUPO-EXTERNOS aggregate as ONE extra
 *  `<tr>`, appended after the sortable professional rows (never one of
 *  them — `depuracion.grupo_externos` carries no per-professional sticker/
 *  Survey stats to sort/export/PDF-report against, only the aggregate count
 *  + the collapsed detail). Collapsed by default: the detail `<ul>` ships
 *  with the `hidden` attribute; initSeguimiento's delegated tbody click
 *  handler removes it on `#seg-externos-toggle` (this function never
 *  toggles anything itself — it only has to make sure the content EXISTS to
 *  reveal, which is what "expanding shows the individual entries" tests
 *  against). `colspan` must match the CURRENT sub-tab's column count + 1
 *  (Acciones), same as the "no rows match" placeholder row in renderTable.
 *  `null`/missing `grupoExternos` (no non-person accounts were collapsed
 *  this pass, or depuracion inactive) -> `''`, no stray row. */
export function grupoExternosRowHtml(grupoExternos, colspan = 1) {
  if (!grupoExternos) return '';
  const detalle = Array.isArray(grupoExternos.detalle) ? grupoExternos.detalle : [];
  const items = detalle.map((d) => {
    const nombre = escapeHtml((d && d.nombre_completo) || 'Sin dato');
    const identificacion = escapeHtml((d && d.identificacion) || 'Sin dato');
    const motivo = escapeHtml((d && d.motivo) || 'sin motivo');
    const ultimo = d && d.ultimo_sticker ? `, últ. sticker ${escapeHtml(d.ultimo_sticker)}` : '';
    return `<li>${nombre} — cédula ${identificacion} (${motivo}${ultimo})</li>`;
  }).join('');
  const n = grupoExternos.n_colapsados ?? detalle.length;
  const fuente = escapeHtml(grupoExternos.fuente_dato || '');
  return `<tr class="seg-grupo-externos-row"><td colspan="${colspan}">`
    + `<button type="button" class="seg-sort-btn" id="seg-externos-toggle" data-seg-externos-toggle aria-expanded="false">`
    + `▸ Externos agrupados (${n})${fuente ? ` — ${fuente}` : ''}</button>`
    + `<ul class="seg-externos-detail" id="seg-externos-detail" hidden>${items}</ul>`
    + '</td></tr>';
}

/** Task 4.6/4.7 (seguimiento-inspectores-depurado, Fase 4; spec: "Manual
 *  Review Section Surfaces Unresolved Depuration Cases"): renders
 *  `depuracion.revision_manual` — código remaps in conflict
 *  (`codigo_remap_candidato`), Vercel-internal duplicate códigos
 *  (`codigo_vercel_duplicado`), or any other case
 *  `inspectores_depuracion.py` could not resolve automatically. An empty
 *  list renders an EXPLICIT "nothing pending" state — this section must
 *  never look hidden/broken just because there is currently nothing to
 *  review (the spec scenario this literally guards).
 *
 *  Phase 11 (tasks 11.15/11.15b/11.16; spec MODIFIED requirement): every
 *  motivo the engine emits is listed WITH the affected people's name and
 *  cédula — resolved from the item's own fields (`nombre_completo`,
 *  `cedula_key`, …) or, for the `identidad_key*` fields, from `identity`
 *  (`profiles`, then `grupoExternos.detalle`); a key that resolves to no one
 *  still shows its cédula. Generic on the FIELDS, not on the motivo, so an
 *  unrecognized motivo prints its raw string plus whatever fields it carries.
 *  `n_ocurrencias` >= 2 renders "×N". `isAdmin: false` drops every name,
 *  cédula and identidad key (they are PII; only motivo, código and the
 *  counters remain). `isAdmin` defaults to true for the pre-Phase-11 callers:
 *  initSeguimiento passes the real role. `null` entries are skipped. Every
 *  interpolated value is escaped. */
export function revisionManualHtml(list, { identity = null, isAdmin = true } = {}) {
  const items = (Array.isArray(list) ? list : []).filter((it) => it !== null && it !== undefined);
  if (!items.length) {
    return '<p class="sticker-note" id="seg-revision-manual-empty">Sin pendientes de revisión manual.</p>';
  }
  const label = isAdmin ? revisionPersonLabeler(identity) : null;
  const rows = items.map((it) => `<li>${revisionItemText(it, label)}</li>`).join('');
  return `<ul class="seg-revision-manual-list">${rows}</ul>`;
}

/** Most keys/holders a single review entry lists before "+N más". */
const REVISION_LIST_CAP = 25;

/** `label(rawKey)` -> escaped "Name (cédula 123)" for a `revision_manual`
 *  identity key, from the identity's profiles, then the GRUPO-EXTERNOS detail
 *  (INV-2: a review key may be a collapsed member); an unresolvable key shows
 *  just "cédula <key>". Empty key -> ''. */
function revisionPersonLabeler(identity) {
  const profiles = identity && identity.profiles instanceof Map ? identity.profiles : null;
  const detalle = identity && identity.grupoExternos && Array.isArray(identity.grupoExternos.detalle)
    ? identity.grupoExternos.detalle : [];
  let externos = null;
  return (rawKey) => {
    const raw = String(rawKey === null || rawKey === undefined ? '' : rawKey);
    const key = cedulaKey(raw);
    let name = '';
    let cedula = raw;
    const profile = profiles && key ? profiles.get(`ced:${key}`) : null;
    if (profile) {
      name = profile.name || '';
      cedula = profile.cedula || raw;
    } else if (key && detalle.length) {
      if (!externos) externos = new Map(detalle.filter(Boolean).map((d) => [cedulaKey(d.identificacion), d]));
      const externo = externos.get(key);
      if (externo) { name = externo.nombre_completo || ''; cedula = externo.identificacion || raw; }
    }
    if (!name && !cedula) return '';
    return name ? `${escapeHtml(name)} (cédula ${escapeHtml(cedula)})` : `cédula ${escapeHtml(cedula)}`;
  };
}

function revisionListText(rawValues, label) {
  // A scalar where the backend should send a list (a string/number key) is one
  // element, never silently dropped: `label` resolves it or prints the escaped
  // raw text as "cédula <raw>".
  const isScalar = (typeof rawValues === 'string' && rawValues.trim() !== '')
    || (typeof rawValues === 'number' && Number.isFinite(rawValues));
  const values = isScalar ? [rawValues] : rawValues;
  if (!Array.isArray(values) || !values.length) return '';
  const shown = values.slice(0, REVISION_LIST_CAP).map(label).filter(Boolean).join(', ');
  const rest = values.length - REVISION_LIST_CAP;
  return rest > 0 ? `${shown}, +${rest.toLocaleString('es-CO')} más` : shown;
}

/** One entry's text (already escaped): motivo, ×N, código(s), then — only when
 *  `label` (admin) — the people involved. Fields keep the order of the
 *  pre-Phase-11 render (motivo · código · candidato · score). */
function revisionItemText(it, label) {
  const src = (it && typeof it === 'object') ? it : {};
  const n = typeof src.n_ocurrencias === 'number' && Number.isFinite(src.n_ocurrencias) && src.n_ocurrencias >= 2
    ? ` ×${Math.trunc(src.n_ocurrencias)}` : '';
  const parts = [`${escapeHtml(src.motivo || 'sin motivo')}${n}`];
  if (src.codigo) parts.push(`código ${escapeHtml(src.codigo)}`);
  if (src.codigo_anterior || src.codigo_nuevo) {
    parts.push(`código ${escapeHtml(src.codigo_anterior || '—')} → ${escapeHtml(src.codigo_nuevo || '—')}`);
  }
  if (label) {
    const one = (text, key) => { const who = key ? label(key) : ''; if (who) parts.push(`${text}: ${who}`); };
    const many = (text, keys) => { const who = revisionListText(keys, label); if (who) parts.push(`${text}: ${who}`); };
    one('persona', src.identidad_key);
    one('conserva el código', src.identidad_key_conservado);
    one('absorbido', src.identidad_key_absorbido);
    one('ya existente', src.identidad_key_existente);
    many('personas', src.identidad_keys);
    many('titulares que conservan el código', src.identidad_keys_titulares);
    if (src.cedula_key) parts.push(`cédula ${escapeHtml(src.cedula_key)}`);
    if (src.nombre_completo) parts.push(`nombre: ${escapeHtml(src.nombre_completo)}`);
    if (src.nombre_completo_duplicado) parts.push(`duplicado: ${escapeHtml(src.nombre_completo_duplicado)}`);
    one('candidato', src.identidad_key_candidato);
  }
  if (Number.isFinite(src.score)) parts.push(`score ${escapeHtml(src.score)}`);
  return parts.join(' · ');
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
    /** L7: 0 or 1, never anything else — a real reflection of whether the
     *  single entry currently holds a cached result, for a self-check to
     *  assert against instead of a vacuous "size if it exists" fallback. */
    size() {
      return entry ? 1 : 0;
    },
  };
}

// ── createIdentityCache: single-entry memo for buildIdentityIndex (M4) ─────
// render() used to call buildIdentityIndex({ stickers, surveys })
// UNCONDITIONALLY, before segCache.get()'s own memo check even ran (7.6 ms
// every render, regardless of whether segCache itself would have hit) —
// even though buildIdentityIndex's only real inputs are the EXACT SAME
// stickers/surveys array references segCache already keys on. A dedicated
// (simpler) single-entry memo, keyed on just those two references — never
// the full segCache tuple, since identity must NOT be invalidated by a mere
// from/to/today change the way the per-filter row computation is.
export function createIdentityCache() {
  let entry = null;

  return {
    /** Returns the cached result when `stickers`/`surveys` are the exact
     *  same references as the last call's (see the module note above);
     *  otherwise calls `compute()`, stores its result, and returns it. */
    get(stickers, surveys, compute) {
      if (entry && entry.stickers === stickers && entry.surveys === surveys) return entry.result;
      const result = compute();
      entry = { stickers, surveys, result };
      return result;
    },
    /** Drops the cached entry — same reasoning as createSegCache's own
     *  clear(), called at the top of initSeguimiento() so a fresh open
     *  never reuses a stale identity index from a previous session. */
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

// DASH/DEGRADED_STICKERS_NOTE now live in the pure section above (exported —
// W7), used here as-is; only DEGRADED_TITLE (a control `title` attribute
// string, not a KPI/note value) stays local to the DOM section.
const DEGRADED_TITLE = 'No disponible: mostrando una copia de respaldo con datos incompletos.';

/** Pure decision behind "Reporte PDF individual"'s disabled/title state (M5)
 *  — extracted out of updateDownloadAvailability so the "no selection" case
 *  is directly assertable without a DOM. This is the exact state that used to
 *  go stale: renderChartOptions can silently reset seg-chart-professional
 *  back to "" (Todos) when a Desde/Hasta change narrows the previously
 *  selected professional out of `rows`, but nothing re-derived this state
 *  until some OTHER interaction happened to call updateDownloadAvailability —
 *  the button stayed enabled, pointing at a selection that no longer existed.
 *  Mirrors the XLSX/mass-export buttons' own isDegraded||!stickersLoaded||
 *  busy block, plus the extra "a professional must be selected" requirement
 *  unique to this button. */
export function reportSelectedButtonState({
  isDegraded = false, stickersLoaded = true, busy = false, hasSelection = false,
} = {}) {
  const disabled = Boolean(isDegraded || !stickersLoaded || busy || !hasSelection);
  const title = isDegraded ? DEGRADED_TITLE
    : !stickersLoaded ? 'Esperando a que carguen los stickers…'
      : busy ? 'Generando exportación masiva…'
        : !hasSelection ? 'Seleccioná un profesional en el filtro "Profesional (gráfico y tabla)" para descargar su informe.'
          : 'Descargar informe PDF de este profesional';
  return { disabled, title };
}

/** L7: whether every per-row "📄 Reporte" button must be disabled — the SAME
 *  block condition the mass/XLSX/per-selection buttons already share
 *  (isDegraded||!stickersLoaded), PLUS `busy` (a mass export in flight):
 *  without this, a second per-row report could start concurrently while the
 *  mass export loop is mid-build against its own snapshotted stickers/
 *  surveys/identity, racing the SAME pdfmake instance (loadPdfmake's shared
 *  memoized promise) and the (already-degrading) main thread. Exported so a
 *  self-check can assert the truth table without a DOM. */
export function rowReportButtonsBlocked({
  isDegraded = false, stickersLoaded = true, busy = false,
} = {}) {
  return Boolean(isDegraded || !stickersLoaded || busy);
}

/** L8: text for the blocking "generating…" overlay shown from the mass
 *  export's click until its download() call returns — the pdfmake layout
 *  phase is effectively synchronous and can run for seconds on 100+
 *  professionals, so an admin needs to see SOMETHING is happening rather
 *  than a frozen page. `total` is the professional count about to be
 *  built. Exported so a self-check can assert the exact copy. */
export function massExportOverlayText(total) {
  const n = Number.isFinite(total) ? total : 0;
  return `Generando ${n} reportes… esto puede tardar unos segundos.`;
}

/** Hard cap of the mass PDF export (plan D5/W10): beyond it the document
 *  risks the performance budget (< 20 s / < 700 MB for ~110 professionals). */
export const MASS_EXPORT_CAP = 200;

/** Phase 11 / D17 (spec "Table And Export Reflect Depurado Fields
 *  Consistently"): the ONE decision behind the mass PDF export. `visibleRows`
 *  are the rows currently on screen (search + range + estado + professional
 *  narrowing); the export defaults to those WITH activity in the range —
 *  seeding puts up to ~373 rows on screen and most of them have nothing to
 *  report. When that scope still exceeds `cap` the export is REFUSED (no
 *  partial batch, `rows: []`) with a message naming the cap and the count —
 *  it never truncates. `status` is `'ok'` | `'refused'` | `'empty'` (nothing
 *  with activity to export). Pure: the DOM layer only reads it. */
export function massExportScope(visibleRows, { cap = MASS_EXPORT_CAP } = {}) {
  const list = Array.isArray(visibleRows) ? visibleRows : [];
  const withActivityRows = list.filter(rowHasActivity);
  const base = { visible: list.length, withActivity: withActivityRows.length, cap };
  if (!withActivityRows.length) return { ...base, status: 'empty', rows: [], message: '' };
  if (withActivityRows.length > cap) {
    return {
      ...base,
      status: 'refused',
      rows: [],
      message: `No se puede exportar: ${withActivityRows.length.toLocaleString('es-CO')} profesionales con actividad superan el máximo de ${cap.toLocaleString('es-CO')} por exportación. Acotá los filtros (búsqueda/estado/rango/profesional) antes de exportar.`,
    };
  }
  return { ...base, status: 'ok', rows: withActivityRows, message: '' };
}

/** The scope statement shown in the UI BEFORE the mass export runs (and used
 *  as the button's caption note): what will be exported, out of how many
 *  visible rows, or the refusal message. Plain text (`.textContent`). */
export function massExportScopeText(scope) {
  if (!scope) return '';
  // The persistent notice under the buttons is calm guidance; the explicit
  // "No se puede exportar: ..." wording (scope.message) is reserved for the
  // moment the user actually clicks the mass export over the cap.
  const count = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0).toLocaleString('es-CO');
  if (scope.status === 'refused') {
    return `Exportación masiva: hay ${count(scope.withActivity)} profesionales con actividad y el máximo por exportación es ${count(scope.cap)}. Para exportar, acotá los filtros (búsqueda, estado, rango o profesional).`;
  }
  if (scope.status === 'empty') return 'Exportación masiva: ningún profesional visible con actividad en el rango.';
  const n = count(scope.withActivity);
  if (scope.withActivity === scope.visible) {
    return `Exportación masiva: ${n} profesionales visibles, todos con actividad en el rango.`;
  }
  return `Exportación masiva: ${n} de ${count(scope.visible)} profesionales visibles (solo los profesionales con actividad en el rango).`;
}

/** L8: whether a NEW mass export may start — shared, module-level state
 *  (`exportInFlight`, read by the DOM section below) rather than the
 *  per-init `busy` closure variable it augments: re-opening the Seguimiento
 *  tab mid-export used to reset `busy` to `false` in the FRESH init, so a
 *  second export could start (and a second pdfMake.createPdf(...).download()
 *  fire) while the OLD (now-orphaned) export was still mid-build against
 *  its own stale snapshot. Exported so a self-check can assert the guard
 *  without a DOM/without touching real module state. */
export function canStartMassExport({ exportInFlight = false } = {}) {
  return !exportInFlight;
}

/** L8: whether an already-built mass export should actually be delivered
 *  (pdfMake.createPdf(...).download(...)) once its (possibly slow) build
 *  finishes. `seq` is the module-level `loadSeq` snapshotted at click time;
 *  `loadSeq` is its CURRENT value, read again right before delivery. A
 *  mismatch means the tab was re-opened (a fresh sticker fetch bumped
 *  loadSeq) WHILE this export's build was still running against a snapshot
 *  of the OLD stickers/surveys/DOM — delivering it would ship a file built
 *  from stale data and, worse, the export's own `finally` would go on to
 *  replay a deferred store update against an init that no longer exists.
 *  Never deliver in that case; the caller shows a cancellation toast
 *  instead and skips both the download AND the deferred-records replay.
 *  Exported so a self-check can assert the decision without a DOM. */
export function shouldDeliverExport({ seq, loadSeq: currentLoadSeq } = {}) {
  return seq === currentLoadSeq;
}

let loadSeq = 0;
// L8: shared across EVERY initSeguimiento() call (never reset per-init, the
// way the old `busy` closure variable was) — see canStartMassExport's own
// doc comment for why a per-init flag alone let a re-opened tab start a
// second, concurrent mass export while an old one was still mid-build.
let exportInFlight = false;
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
// M4: identity index memo, alongside segCache — see createIdentityCache's
// own doc comment for why it is a SEPARATE cache (identity must not be
// invalidated by a mere from/to/today change).
const identityCache = createIdentityCache();

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
      <p class="sticker-note" id="seg-unassigned-note" hidden></p>
      <p class="sticker-note" id="seg-depuracion-badge" hidden></p>

      <div class="eval-filters" id="seg-filters">
        <div class="asignacion-search">
          <input type="search" id="seg-search" class="sticker-search-input"
            placeholder="Buscar profesional por nombre o cédula…" aria-label="Buscar profesional por nombre o cédula">
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
            <span>Profesional (gráfico y tabla)</span>
            <select id="seg-chart-professional" aria-label="Profesional (gráfico y tabla)"><option value="">Todos</option></select>
          </label>
          <label class="sticker-field asignacion-inline-field" id="seg-estado-field" hidden>
            <span>Estado sugerido</span>
            <select id="seg-estado" aria-label="Estado sugerido">${ESTADO_FILTER_OPTIONS.map((o) => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`).join('')}</select>
          </label>
          <button type="button" class="sticker-action" id="seg-download">Exportar XLSX</button>
          <button type="button" class="sticker-action" id="seg-report-selected" disabled>Reporte PDF individual</button>
          <button type="button" class="sticker-action" id="seg-report-mass" disabled>Exportación masiva reportes</button>
        </div>
        <p class="sticker-note" id="seg-report-scope" hidden></p>
      </div>

      <div class="kpi-row eval-kpis" id="seg-kpis"></div>

      <div class="card eval-workspace-card">
        <div class="card-toolbar">
          <span class="eval-toolbar-title">Ritmo diario</span>
        </div>
        <div class="chart-tile" style="height:320px">
          <canvas id="seguimiento-timeline"></canvas>
        </div>
        <p class="chart-note">Eje izquierdo: acumulado corrido (línea punteada), iniciado en el total previo al rango. Eje derecho: registros del día (línea sólida). Rango: Desde–Hasta. El cuadro de búsqueda solo acota la tabla; el filtro "Profesional (gráfico y tabla)" acota el gráfico y la tabla a la vez.</p>
      </div>

      <div class="card eval-workspace-card">
        <div class="card-toolbar">
          <span class="eval-toolbar-title">Profesionales</span>
        </div>
        <div class="asignacion-segmented" id="seg-subtabs" role="tablist" aria-label="Vista de la tabla de profesionales">
          <button type="button" class="asignacion-segment is-active" data-seg-subtab="totales" role="tab" aria-selected="true" tabindex="0">Totales</button>
          <button type="button" class="asignacion-segment" data-seg-subtab="temporales" role="tab" aria-selected="false" tabindex="-1">Análisis temporales</button>
        </div>
        <div class="table-scroll">
          <table class="tipologia-table" id="seg-table">
            <thead><tr></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>

      <div class="card eval-workspace-card">
        <div class="card-toolbar">
          <span class="eval-toolbar-title">Revisión manual</span>
        </div>
        <div id="seg-revision-manual"></div>
      </div>
    </section>
    <div class="seg-export-overlay" id="seg-export-overlay" role="status" aria-live="polite"
      style="display:none;position:fixed;inset:0;z-index:1100;align-items:center;justify-content:center;background:rgba(2,8,18,0.75);padding:20px;">
      <p class="seg-export-overlay-text" id="seg-export-overlay-text"
        style="background:var(--surface,#12294a);color:var(--text-primary,#fff);border-radius:var(--radius-lg,10px);padding:20px 28px;max-width:360px;text-align:center;font-size:0.95rem;margin:0;"></p>
    </div>`;
}

/** 5 KPI tiles (W7 — "sin profesional identificado" and "stickers sin fecha"
 *  are no longer tiles; see unassignedNote/the sinfecha note for where those
 *  counts still surface, as disclosures). `rowsResult` is the (subset of)
 *  buildProfessionalRows() output kpiTotals itself needs (`rows` + `totals`);
 *  `stickersLoaded` masks the two sticker-derived tiles behind DASH — see
 *  kpiTotals' own doc comment for why. */
export function kpisHtml(rowsResult, stickersLoaded) {
  const t = kpiTotals(rowsResult, { stickersLoaded });
  const fmt = (v) => (v === DASH ? DASH : Number(v || 0).toLocaleString('es-CO'));
  const tile = (label, value, title = '') => `
    <div class="kpi-tile is-neutral"${title ? ` title="${escapeHtml(title)}"` : ''}>
      <span class="kpi-label kpi-label-lower">${escapeHtml(label)}</span>
      <span class="kpi-value">${fmt(value)}</span>
    </div>`;
  const seeded = t.padron !== undefined;
  return [
    // The depuración's own ACTIVE count leads the row: it is the figure the owner
    // asked to see, and it does not depend on the date range.
    ...(t.inspectoresActivos === undefined ? [] : [tile(
      'inspectores activos',
      t.inspectoresActivos,
      `Inspectores que la depuración clasifica como activos (con código vigente o sticker válido), sobre el total del padrón (${fmt(t.inspectoresActivos)} de ${fmt(t.padron)}). No depende del rango de fechas.`,
    )]),
    // Second tile: the daily sticker pace of that same active padrón (owner
    // definition 2026-09-19). Follows the range but not the search/estado filters.
    ...(t.stickersPorInspectorActivo === undefined ? [] : [tile(
      'stickers/día por inspector activo',
      t.stickersPorInspectorActivo,
      `Stickers de los inspectores activos en el rango ÷ días del rango (${fmt(t.rangoDias)}) ÷ inspectores activos (${fmt(t.inspectoresActivos)}). Es el rendimiento del padrón activo completo; no depende del buscador ni del filtro de estado.`,
    )]),
    // Seeded: "activos" alone would be ambiguous next to "inspectores activos"
    // (the depuración's classification), so this one says what it measures.
    seeded
      ? tile('profesionales con actividad', t.professionals, 'Profesionales con actividad en el rango de fechas seleccionado.')
      : tile('profesionales activos', t.professionals),
    tile('stickers (F1+F2)', t.stickers),
    tile('evaluaciones survey', t.surveys),
    // M6: renamed from "promedio diario por profesional" — the OLD label
    // read as a generic pace, but the tile is (and always was meant to be) a
    // STICKER-only pace with a specific denominator (days the professional
    // actually placed a sticker on, averaged across professionals who placed
    // at least one) — the title spells that out so it doesn't get confused
    // with a pooled stickers+surveys average.
    tile(
      'stickers/día por profesional',
      t.avgStickersPerDayPerProfessional,
      'Stickers por día con actividad de stickers, promedio entre profesionales (excluye a quien no tiene ningún día con stickers).',
    ),
    tile('barrios activos (7 d)', t.barriosActivos),
    // Phase 11 / D17: only when the table is seeded from depuracion.inspectores.
    ...(t.padron === undefined ? [] : [tile(
      'profesionales en padrón',
      t.padron,
      'Total de profesionales del padrón depurado, con o sin actividad en el rango. No entra en los promedios.',
    )]),
  ].join('');
}

/** W9: header row for WHICHEVER sub-tab's columns (columnsFor) are passed in
 *  — no more hardcoded module-level COLUMNS; "Acciones" stays appended here,
 *  never part of either COLUMNS_TOTALES/COLUMNS_TEMPORALES array (see their
 *  own doc comment). */
function headerRowHtml(sortState, columns) {
  const sortable = columns.map((c) => {
    const active = sortState.column === c.key;
    const arrow = active ? (sortState.dir === 'asc' ? ' ▲' : ' ▼') : '';
    const title = c.title ? ` title="${escapeHtml(c.title)}"` : '';
    return `<th scope="col"><button type="button" class="seg-sort-btn${active ? ' is-active' : ''}" data-seg-sort="${c.key}"${title}>${escapeHtml(c.label)}${arrow}</button></th>`;
  }).join('');
  return `${sortable}<th scope="col">Acciones</th>`;
}

/** One row's cell text/HTML for a single column key — the DOM-layer
 *  counterpart to xlsxRowsFor's per-sheet field mapping (same fields, same
 *  DASH-when-unknown-vs-"Sin dato"-when-genuinely-blank distinction), kept as
 *  its own small function so headerRowHtml/rowHtml never hardcode a column
 *  list themselves. `stickersLoaded` masks every sticker/temporal-derived
 *  cell behind DASH (same reasoning as kpisHtml above: these are literally
 *  0/null whenever `stickers` is still `[]` — in flight or failed — and
 *  showing that as a real value would misreport "unknown" as "confirmed").
 *
 *  H2 (bug fix): `np`/`codigo`/`cedula` (only ever populated from a
 *  STICKER's own `inspector` fields, see buildIdentityIndex's profile-
 *  building loop — the Survey loop never sets them), `barriosActivos` and
 *  `activeDays`/`firstDate`/`lastDate` (both pooled from stickers+Survey
 *  dates, so while stickers haven't resolved they can only ever reflect a
 *  PARTIAL, Survey-only picture) used to render their real content —
 *  "Sin dato" or a real value — even while `!stickersLoaded`, inconsistent
 *  with `daysSinceFirst` right below (which WAS already masked) and with the
 *  "Degradado/cargando ≠ cero" invariant every other sticker-derived figure
 *  in this file already follows. All seven now route through `stk()` too.
 *  Exported so a self-check can assert the masking truth table directly. */
export function cellHtml(r, key, stickersLoaded) {
  const stk = (v) => (stickersLoaded ? v : DASH);
  switch (key) {
    case 'name': return escapeHtml(r.name || 'Sin dato');
    case 'cedula': return stk(escapeHtml(r.cedula || 'Sin dato'));
    case 'tarjetaProfesional': return stk(escapeHtml(r.tarjetaProfesional || 'Sin dato'));
    case 'enfasis': {
      // Free text of any length: the cell shows an ellipsis-truncated line (`.seg-enfasis`) and keeps the
      // whole escaped text in the tooltip; nothing is sliced here, so copy/search/export keep the full value.
      if (!stickersLoaded) return DASH;
      if (!r.enfasis) return 'Sin dato';
      const texto = escapeHtml(r.enfasis);
      return `<span class="seg-enfasis" title="${texto}">${texto}</span>`;
    }
    case 'np': return stk(escapeHtml(r.np || 'Sin dato'));
    case 'estadoSugerido': return stk(escapeHtml(r.estadoSugerido || 'Sin dato'));
    case 'codigo': return stk(escapeHtml(r.codigo || 'Sin dato'));
    case 'stickersFase1': return stk(r.stickersFase1);
    case 'stickersFase2': return stk(r.stickersFase2);
    case 'surveyTotal': return r.surveyTotal;
    case 'activeDays': return stk(r.activeDays);
    case 'avgStickersPerDay':
      return stickersLoaded ? (Number.isFinite(r.avgStickersPerDay) ? r.avgStickersPerDay : DASH) : DASH;
    case 'barriosActivos':
      return stk(escapeHtml((r.barriosActivos && r.barriosActivos.length) ? r.barriosActivos.join(', ') : 'Sin dato'));
    case 'firstDate': return stk(escapeHtml(formatDateCell(r.firstDate)));
    case 'lastDate': return stk(escapeHtml(formatDateCell(r.lastDate)));
    case 'daysSinceFirst':
      // Mixes stickers+Survey the same way "Total" already does -- masked
      // behind the same flag for consistency rather than a third rule.
      return stickersLoaded ? (Number.isFinite(r.daysSinceFirst) ? r.daysSinceFirst : DASH) : DASH;
    case 'prevDayFirstMinutes': return stk(formatMinutes(r.prevDayFirstMinutes));
    case 'prevDayLastMinutes': return stk(formatMinutes(r.prevDayLastMinutes));
    case 'avgFirstMinutes': return stk(formatMinutes(r.avgFirstMinutes));
    case 'avgLastMinutes': return stk(formatMinutes(r.avgLastMinutes));
    default: return '';
  }
}

/** The `<tbody>` markup of the professionals table for already filtered and
 *  sorted `sortedRows` (Phase 11: extracted from renderTable so the 400-row
 *  perf test measures the real render path, and so a future caller — e.g. a
 *  skipped re-render — has one pure function to reuse). An empty list renders
 *  the "no match" placeholder row spanning `columns.length + 1` (Acciones). */
export function tableBodyHtml(sortedRows, stickersLoaded, isDegraded, columns, busy) {
  return sortedRows.length
    ? sortedRows.map((r) => rowHtml(r, stickersLoaded, isDegraded, columns, busy)).join('')
    : `<tr><td colspan="${columns.length + 1}" class="eval-empty">Ningún profesional coincide con los filtros aplicados.</td></tr>`;
}

/** `columns` (columnsFor(subTab)'s current result) drives which cells render
 *  — see cellHtml's own doc comment for the per-column DASH-masking rule.
 *  The roster-sourced caveat badge always rides on the FIRST visible column
 *  (always "Nombre" in both sub-tabs) regardless of which sub-tab is active.
 *  The same flags (isDegraded/stickersLoaded) disable the per-row PDF report
 *  button: its "puntos recogidos" section would otherwise ship as an empty/
 *  false list while stickers haven't resolved, or a degraded/redacted one. */
function rowHtml(r, stickersLoaded, isDegraded, columns, busy) {
  const caveat = r.rosterSourced > 0
    ? ` <span class="seg-caveat" title="Identidad por roster, aproximada — ${r.rosterSourced} sticker(s) sin verificar contra esta evaluación.">⚠</span>`
    : '';
  // L7: rowReportButtonsBlocked is the single source of truth for this
  // condition — updateDownloadAvailability re-applies the SAME rule directly
  // on the live buttons (via querySelectorAll('[data-seg-report]')) whenever
  // `busy` changes WITHOUT a full renderTable pass, so the two must never
  // independently drift.
  const reportBlocked = rowReportButtonsBlocked({ isDegraded, stickersLoaded, busy });
  const reportTitle = isDegraded ? DEGRADED_TITLE
    : !stickersLoaded ? 'Esperando a que carguen los stickers…'
      : busy ? 'Generando exportación masiva…' : 'Descargar informe PDF de este profesional';
  const cells = columns.map((c, i) => `<td>${cellHtml(r, c.key, stickersLoaded)}${i === 0 ? caveat : ''}</td>`).join('');
  return `<tr>${cells}<td><button type="button" class="sticker-action seg-report-btn" data-seg-report="${escapeHtml(r.key)}"${reportBlocked ? ' disabled' : ''} title="${escapeHtml(reportTitle)}">📄 Reporte</button></td></tr>`;
}

// The empty-chart-tile note (used when Chart.js failed to load, or the
// current filter/range has zero activity — W8) reuses charts.js's own
// setChartEmpty/clearChartEmpty (exported for exactly this — W8's "reutilizar
// sin duplicar" rule) instead of a second, drifting copy of the same
// hide-canvas + `.chart-empty` note + registry-cleanup logic.

const fmtCount = (n) => Math.round(n || 0).toLocaleString('es-CO');

/** Built on charts.js's own baseOptions() so this chart's ticks/grid/legend/
 *  tooltip colors follow the same theme tokens as every other chart in the
 *  dashboard instead of a second, hand-rolled (and un-themed) copy.
 *
 *  CONTRATO CAMBIADO (W8): the shared logarithmic axis is GONE — a single
 *  log axis could never plot a literal 0 (log(0) is undefined), so a
 *  zero-count day silently dropped its point instead of showing the real
 *  zero. Replaced with a genuine DUAL LINEAR axis: daily counts (solid,
 *  thicker lines) on the RIGHT axis (`y1`), cumulative running totals
 *  (dashed, no points) on the LEFT axis (`y`) — each series lives on the
 *  scale suited to its own magnitude, and a zero-count day plots a real 0.
 *  `timeline.stickersCumulative`/`surveysCumulative` already start from
 *  `timeline.offsets` (buildTimeline's own pre-range count folded into the
 *  running total), so the left axis' first point reflects the true running
 *  total even when `from` narrows the visible window mid-history — nothing
 *  here recomputes that. A single-label timeline gives the (normally
 *  pointRadius 0) cumulative lines pointRadius 3 instead, since a 1-point
 *  dashed line with radius 0 renders NOTHING at all. The running total is
 *  labeled on the last point via the same totalDataLabelPlugin the Panel
 *  chart uses, with a per-dataset vertical nudge (`_labelOffsetY`) so the two
 *  cumulative labels don't overlap when their values are close. */
export function timelineChartConfig(timeline) {
  const singleLabel = timeline.labels.length === 1;
  return {
    type: 'line',
    data: {
      labels: timeline.labels,
      datasets: [
        {
          label: 'Sticker diario', data: timeline.stickers, borderColor: COLORS.accent,
          backgroundColor: 'transparent', tension: 0.15, pointRadius: 3, borderWidth: 3, yAxisID: 'y1',
        },
        {
          label: 'Survey diario', data: timeline.surveys, borderColor: COLORS.categorical[0],
          backgroundColor: 'transparent', tension: 0.15, pointRadius: 3, borderWidth: 3, yAxisID: 'y1',
        },
        {
          label: 'Sticker acum.', data: timeline.stickersCumulative, borderColor: COLORS.accent,
          backgroundColor: 'transparent', tension: 0.15, pointRadius: singleLabel ? 3 : 0, borderWidth: 2, borderDash: [6, 4],
          yAxisID: 'y',
          _totalLabel: fmtCount(timeline.stickersCumulative[timeline.stickersCumulative.length - 1]),
          _labelOffsetY: -5,
        },
        {
          label: 'Survey acum.', data: timeline.surveysCumulative, borderColor: COLORS.categorical[0],
          backgroundColor: 'transparent', tension: 0.15, pointRadius: singleLabel ? 3 : 0, borderWidth: 2, borderDash: [6, 4],
          yAxisID: 'y',
          _totalLabel: fmtCount(timeline.surveysCumulative[timeline.surveysCumulative.length - 1]),
          _labelOffsetY: -18,
        },
      ],
    },
    plugins: [totalDataLabelPlugin],
    options: baseOptions({
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: {
          type: 'linear', position: 'left', beginAtZero: true, title: { display: true, text: 'Acumulado' },
        },
        y1: {
          type: 'linear', position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'Diario' },
        },
      },
      plugins: { legend: { display: true }, tooltip: { mode: 'index', intersect: false } },
    }),
  };
}

/** Cache key for renderChart()'s upsertChart-skip (W8): identical inputs (the
 *  visible labels, the date range, which professional is selected, the
 *  pre-range offsets, and each series' own sum) -> identical key, so a
 *  re-render that would produce the EXACT SAME chart (e.g. a sort-only
 *  interaction, which still routes through render()) can skip upsertChart
 *  entirely instead of re-diffing/repainting Chart.js for no visual change.
 *  Deliberately a plain joined string, not a content hash — cheap to compute
 *  and trivially different whenever any input field differs.
 *
 *  H2: the per-series fields are the FULL day-by-day array (`.join(',')`, at
 *  most ~365 points), not a sum — two timelines with the same labels and the
 *  same total (e.g. `[1,2]` vs `[2,1]`, which can genuinely happen switching
 *  seg-chart-professional between two professionals whose daily counts swap)
 *  used to hash to the identical key when only the sums were compared, so a
 *  real change in what's being plotted silently skipped the repaint. */
export function timelineDataKey(timeline, { from = null, to = null, professionalKey = null } = {}) {
  const series = (arr) => (Array.isArray(arr) ? arr.join(',') : '');
  const offsets = (timeline && timeline.offsets) || {};
  return [
    (timeline && timeline.labels ? timeline.labels.join(',') : ''),
    from || '', to || '', professionalKey || '',
    offsets.stickers || 0, offsets.surveys || 0,
    series(timeline && timeline.stickers), series(timeline && timeline.surveys),
  ].join('|');
}

/** Whether renderChart() may skip re-running upsertChart() for the timeline
 *  chart (B1). Gating on `dataKey === lastKey` ALONE is unsafe: main.js's
 *  'themechange' listener destroys EVERY registered Chart.js instance
 *  (charts.js's resetCharts()) so every chart can re-bake the new theme's
 *  CSS-variable colors, then this module's own deferred 'themechange'
 *  listener calls renderChart() again with the exact same stickers/surveys/
 *  from/to/professionalKey it had before — producing the SAME dataKey even
 *  though the underlying Chart.js instance no longer exists. The old
 *  key-only check treated that as "nothing to repaint" and left the canvas
 *  permanently blank. `hasChart` (charts.js's registry.has(canvasId)) is the
 *  second, independent signal: only skip when the key is unchanged AND the
 *  chart is still actually registered. */
export function shouldSkipChartRender({ dataKey, lastKey, hasChart: chartStillRegistered } = {}) {
  return Boolean(dataKey != null && lastKey != null && dataKey === lastKey && chartStillRegistered);
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

// ── Snapshot retention + background revalidation (PR 10 part 2, design D25/D28) ──
//
// The last `{ snapshotId, stickers, degraded, depuracion }` an ADMIN received is
// kept in a module-level variable, MEMORY ONLY (it carries names, cédulas and
// contact data: never written to any browser storage, asserted by a test).
// `snapshotId` is the opaque response ETag (design D26/C-E3: the body has no
// such key). On open the tab paints from it at once and revalidates in the
// background with `If-None-Match`; a 304 or an equal ETag changes nothing (zero
// extra render()), a different ETag replaces the retained value and re-renders
// once. The retained value is dropped on 401/403, sign-out and role change
// (clearSeguimientoSnapshot, wired from main.js), and never retained without a
// usable ETag (nothing to validate against => a plain full render every time).
//
// Late answers cannot resurrect cleared data: every clear bumps `retainEpoch`
// and aborts the in-flight request, and a flight only commits (and only reports
// a result) while its epoch is still current.

const SNAPSHOT_RETRY_DELAY_MS = 500;
let retained = null; // { snapshotId, stickers, degraded, depuracion } | null
let retainEpoch = 0;
let inflightFlight = null; // { epoch, isAdmin, controller, promise, cancelWait }
// The container the current init rendered into, so teardownSeguimiento() can
// empty it. Reset to null there: nothing keeps the (PII-carrying) DOM reachable.
let activeRoot = null;

/** Drops the retained snapshot and cancels any in-flight revalidation. Called
 *  on sign-out and on any role change (main.js), and on 401/403. */
export function clearSeguimientoSnapshot() {
  retained = null;
  retainEpoch += 1;
  const flight = inflightFlight;
  inflightFlight = null;
  if (flight && flight.controller) flight.controller.abort();
  // A flight sleeping in its retry back-off wakes up now (its timer is cleared)
  // and finds its epoch stale, so it ends as 'discarded' without a second request.
  if (flight && flight.cancelWait) flight.cancelWait();
}

/** Ends the Seguimiento session's footprint (judgment-day C1): the retained
 *  snapshot and any in-flight revalidation/retry timer are dropped, the epoch
 *  and `loadSeq` are bumped (a late answer or a mid-flight export is never
 *  delivered), every module-level handle onto the open init (search debounce,
 *  chart redraw, store-update hook, memo caches) is released, and the rendered
 *  view is emptied — the container this module last rendered into, plus `root`
 *  when given. Idempotent, a no-op when nothing was ever opened, and each step
 *  is contained so one failure never skips the rest. The module can
 *  `initSeguimiento` again afterwards. */
export function teardownSeguimiento(root) {
  const attempt = (step) => { try { step(); } catch { /* keep tearing down */ } };
  attempt(clearSeguimientoSnapshot);
  loadSeq += 1;
  attempt(() => { if (activeSearchDebounced) activeSearchDebounced.cancel(); });
  activeSearchDebounced = null;
  activeRenderChart = null;
  activeUpdateRecords = null;
  attempt(() => segCache.clear());
  attempt(() => identityCache.clear());
  const mounted = activeRoot;
  activeRoot = null;
  for (const container of new Set([mounted, root])) {
    if (container) attempt(() => { container.innerHTML = ''; });
  }
}

/** The retained snapshot (or null). Exposed for the open path and for tests. */
export function peekSeguimientoSnapshot() {
  return retained;
}

function isAuthError(err) {
  return Boolean(err) && (err.status === 401 || err.status === 403);
}

/** Revalidates the retained snapshot (or does the first full fetch) and
 *  resolves — NEVER rejects — to one of:
 *   { outcome: 'unchanged', snapshot }  304 / equal ETag: nothing to redraw
 *   { outcome: 'fresh', snapshot }      new data: the caller renders once
 *   { outcome: 'denied', error }        401/403: retention cleared
 *   { outcome: 'failed', error }        network/5xx/malformed: retention kept
 *   { outcome: 'discarded' }            the session/role changed meanwhile
 *  At most ONE flight is in progress: concurrent callers share its promise.
 *  A non-admin never sends a validator and never retains. `fetchOnce` and the
 *  retry delay are injectable for tests. */
export function revalidateSnapshot({
  getToken, isAdmin, fetchOnce = fetchEvaluacionesOnce, retryDelayMs = SNAPSHOT_RETRY_DELAY_MS,
}) {
  if (!isAdmin && retained) clearSeguimientoSnapshot(); // fail closed: admin data never serves a non-admin
  if (inflightFlight && inflightFlight.epoch === retainEpoch && inflightFlight.isAdmin === Boolean(isAdmin)) {
    return inflightFlight.promise;
  }
  const flight = {
    epoch: retainEpoch,
    isAdmin: Boolean(isAdmin),
    controller: typeof AbortController === 'function' ? new AbortController() : null,
    promise: null,
    cancelWait: null,
  };
  flight.promise = runRevalidation(flight, { getToken, fetchOnce, retryDelayMs })
    .finally(() => { if (inflightFlight === flight) inflightFlight = null; });
  inflightFlight = flight;
  return flight.promise;
}

async function runRevalidation(flight, { getToken, fetchOnce, retryDelayMs }) {
  const current = () => flight.epoch === retainEpoch;
  const held = flight.isAdmin ? retained : null;
  const attempt = (extra) => fetchOnce(getToken, STICKERS_ENDPOINT, {
    depuracion: true,
    strictJson: true,
    conditional: true,
    signal: flight.controller ? flight.controller.signal : undefined,
    ...extra,
  });
  // One retry over a transient blip (same recipe as the Stickers tab's read),
  // but never for an authorization failure or a cancelled flight.
  const withRetry = async (extra) => {
    try {
      return await attempt(extra);
    } catch (err) {
      if (isAuthError(err) || !current()) throw err;
      if (retryDelayMs > 0) {
        await new Promise((resolve) => {
          const timer = setTimeout(() => { flight.cancelWait = null; resolve(); }, retryDelayMs);
          flight.cancelWait = () => { clearTimeout(timer); flight.cancelWait = null; resolve(); };
        });
      }
      if (!current()) throw err;
      return await attempt(extra);
    }
  };

  try {
    let result = await withRetry({ ifNoneMatch: held ? held.snapshotId : null });
    if (result.notModified && !(held && retained === held && current())) {
      if (!current()) return { outcome: 'discarded' };
      // A 304 with nothing to validate against: never a blank table — fetch in
      // full, without a validator and bypassing any HTTP cache validator.
      result = await withRetry({ ifNoneMatch: null, bypassCache: true });
      if (result.notModified) throw new Error('El servidor respondió 304 sin datos que validar.');
    }
    if (!current()) return { outcome: 'discarded' };
    if (result.notModified) return { outcome: 'unchanged', snapshot: held };

    const snapshotId = result.etag || null;
    if (held && snapshotId && snapshotId === held.snapshotId && retained === held) {
      return { outcome: 'unchanged', snapshot: held };
    }
    const snapshot = {
      snapshotId, stickers: result.evaluaciones, degraded: result.degraded, depuracion: result.depuracion,
    };
    // No usable ETag => no retention (and the older value's validator is stale).
    retained = flight.isAdmin && snapshotId ? snapshot : null;
    return { outcome: 'fresh', snapshot };
  } catch (err) {
    if (!current()) return { outcome: 'discarded' };
    if (isAuthError(err)) {
      clearSeguimientoSnapshot();
      return { outcome: 'denied', error: err };
    }
    return { outcome: 'failed', error: err };
  }
}

/** Orchestrator for the per-row "📄 Informe"/"Reporte PDF individual" button:
 *  gathers this professional's raw points for `ctx.from`/`ctx.to` (W10: the
 *  CURRENT date range, not the professional's whole career — see
 *  professionalRecords' own doc comment), the last-7-days count, builds the
 *  doc definition (buildProfessionalReportDocDefinition, pure — throws when
 *  `ctx.degraded`) + lazy-loads pdfmake (report.js — the same instance every
 *  other PDF report in the app uses, cached after the first call, retried on
 *  failure via memoizeLoader) and triggers the download. Mirrors report.js's
 *  own generarInformePdf/generarInformeCandidato shape. */
async function descargarInformeProfesional(row, { stickers, surveys, identity }, ctx) {
  const points = professionalRecords(row, {
    stickers, surveys, identity, from: ctx.from, to: ctx.to,
  });
  const last7 = visitasUltimos7Dias(row, {
    stickers, surveys, identity, today: ctx.today,
  });
  const def = buildProfessionalReportDocDefinition(row, points, { ...ctx, last7 });
  const pdfMake = await loadPdfmake();
  const filename = `informe_seguimiento_${reportFilenameSlug(row.name)}_${downloadStamp().slug}.pdf`;
  pdfMake.createPdf(def).download(filename);
}

/** `reportes_agg.json`'s `kpis.pendientes` — fail-soft (`.catch(() => null)`,
 *  and `null` on any other shape mismatch): objetivoDiario() already treats
 *  a non-finite value as "sin dato", never a fabricated 0, exactly the same
 *  contract kpisOficialesFrom (reportes-ciudadanos.js) already applies to
 *  this same JSON file's `kpis` object. Lazy `import('./data.js')` — this
 *  module must stay importable under plain Node (data.js pulls in the
 *  Firebase SDK via a bare https:// specifier, which breaks Node's ESM
 *  loader on a static import), same pattern W10's own plan calls for. */
async function fetchPendientes() {
  try {
    const { fetchData } = await import('./data.js');
    const res = await fetchData('reportes_agg.json');
    if (!res.ok) return null;
    const agg = await res.json();
    const pendientes = agg && agg.kpis && agg.kpis.pendientes;
    return Number.isFinite(pendientes) ? pendientes : null;
  } catch {
    return null;
  }
}

/** initSeguimiento(root, { getToken, records }) — renders the tab and wires
 *  its actions. `records` is store.records (Survey), passed in by main.js so
 *  this module never imports data.js directly (would pull in the Firebase
 *  chain and break the Node self-check). Stickers come from
 *  fetchEvaluacionesOnce with the `depuracion=1` opt-in (this tab only): on a
 *  re-open the admin's retained snapshot is painted first and revalidated in
 *  the background (see revalidateSnapshot), so a re-open costs no re-render
 *  unless the snapshot changed.
 *  `isAdmin` (Phase 11, fail-closed default false) gates the names/cédulas of
 *  the "Revisión manual" section AND the snapshot retention: main.js passes the
 *  real role. `snapshotRetryDelayMs` only exists so tests need not wait 500 ms. */
export function initSeguimiento(root, {
  getToken, records, isAdmin = false, snapshotRetryDelayMs = SNAPSHOT_RETRY_DELAY_MS,
}) {
  if (activeSearchDebounced) activeSearchDebounced.cancel();
  segCache.clear();
  identityCache.clear();
  // The retained admin snapshot to paint from (D28); a non-admin never sees it.
  // (The non-admin drop of a retained value lives in revalidateSnapshot, which
  // this init calls synchronously below: a second copy of it here was dead code.)
  const held = isAdmin ? retained : null;
  root.innerHTML = sectionHtml();
  activeRoot = root;

  const $ = (id) => root.querySelector(`#${id}`);
  const kpisEl = $('seg-kpis');
  const statusEl = $('seg-status');
  const sinFechaNoteEl = $('seg-sinfecha-note');
  const unassignedNoteEl = $('seg-unassigned-note');
  const depuracionBadgeEl = $('seg-depuracion-badge');
  const revisionManualEl = $('seg-revision-manual');
  const searchEl = $('seg-search');
  const fromEl = $('seg-from');
  const toEl = $('seg-to');
  const chartSelectEl = $('seg-chart-professional');
  const estadoEl = $('seg-estado');
  const estadoFieldEl = $('seg-estado-field');
  const reportScopeEl = $('seg-report-scope');
  const resetFiltersBtn = $('seg-reset-filters');
  const downloadBtn = $('seg-download');
  const reportSelectedBtn = $('seg-report-selected');
  const reportMassBtn = $('seg-report-mass');
  const subTabsEl = $('seg-subtabs');
  const tableEl = $('seg-table');
  const theadRow = tableEl.querySelector('thead tr');
  const tbody = tableEl.querySelector('tbody');
  // M6: blocking "generating…" overlay for the mass export — see
  // generarReportesMasivos below for why it must be shown/painted BEFORE the
  // (effectively synchronous) pdfmake build starts.
  const exportOverlayEl = $('seg-export-overlay');
  const exportOverlayTextEl = $('seg-export-overlay-text');

  let stickers = held ? held.stickers : [];
  // seguimiento-inspectores-depurado, Fase 4: the backend's `depuracion`
  // block (stickers.js's tagFuente now passes it through, defaulting to
  // `null`) — always reassigned together with `stickers` (same fetch, same
  // synchronous block below), so identityCache's own stickers/surveys-only
  // key (M4) still invalidates correctly whenever this changes; see
  // buildIdentityIndex's own doc comment for the `null`/`activa:false`
  // fallback contract.
  let depuracion = held ? held.depuracion : null;
  // `let`, not `const`: updateSeguimientoRecords() (module-level export,
  // called by main.js's onStoreChange) reassigns this in place on a store
  // refresh instead of tearing down and re-initializing the whole tab — see
  // activeUpdateRecords below.
  let surveys = Array.isArray(records) ? records : [];
  // W9: which of the two sub-tabs (Totales / Análisis temporales) is
  // showing — reset to 'totales' on every fresh init (a plain closure
  // variable, never persisted across opens); preserved across
  // updateSeguimientoRecords/search/sort WITHIN the same open.
  let subTab = 'totales';
  let sortState = defaultSortFor(subTab);
  let currentRows = [];
  // W10: true while a mass-export build is in flight — makes
  // updateSeguimientoRecords() defer its re-render until the export
  // finishes (a store refresh mid-export must never swap `surveys`/`stickers`
  // out from under an already-snapshotted export loop), and blocks a second
  // click from starting a concurrent export.
  let busy = false;
  let pendingRecordsDuringExport = null;
  // Search-filtered rows, hoisted so the XLSX export and the empty-table
  // guard both read the SAME set the user is actually looking at — the
  // export used to silently ignore the search box and always dump every
  // row (same convention as reportes-ciudadanos.js's `visibles`).
  let visibleRows = [];
  // Distinguishes "confirmed zero stickers" from "stickers haven't
  // resolved yet / failed" — see kpisHtml/rowHtml's DASH masking above.
  // `stickers` itself stays `[]` in both the "loading" and "failed" cases,
  // so this flag (not the array) is what the UI reads to tell them apart.
  let stickersLoaded = Boolean(held);
  let isDegraded = held ? held.degraded : false;
  let stickerFetchErrorMessage = '';
  // Identity index (W5/D7: cédula-first join) for the CURRENT stickers/
  // surveys — recomputed once per render() (not once per pure-function
  // call) so buildProfessionalRows/buildTimeline/the per-row PDF report all
  // resolve identity through the exact SAME index for a given render pass.
  let currentIdentity = buildIdentityIndex({ stickers, surveys, depuracion });
  // W10: reportes_agg.json's kpis.pendientes, fetched once per init
  // (fetchPendientes — fail-soft, null on any failure/shape mismatch) and
  // reused by every report/export click during this session; never blocks
  // the tab (fetched in the background, alongside the sticker fetch below).
  let pendientesValue = null;

  function currentFilters() {
    return { from: fromEl.value || null, to: toEl.value || null };
  }

  /** The `ctx` every report builder call shares this render pass: the
   *  current Desde/Hasta filters, "now" (both as a Bogotá date and as a
   *  ready-to-print `legible` timestamp), the degraded flag (the builder
   *  itself refuses when true), and objetivoDiario() computed from the
   *  CURRENTLY known professional count + pendientesValue — recomputed on
   *  every call (cheap: pure arithmetic) rather than cached, so it always
   *  reflects the latest filters/row count. `extra` lets a caller override/
   *  add fields (mass export sets `last7` per professional, individual
   *  reports leave it to descargarInformeProfesional). */
  function buildReportCtx(extra = {}) {
    const { from, to } = currentFilters();
    const today = bogotaToday();
    return {
      from,
      to,
      today,
      generatedAt: downloadStamp().legible,
      degraded: isDegraded,
      objetivoDiario: objetivoDiario({
        // D17: the per-professional daily target divides by professionals
        // WITH activity, never by the seeded padrón (373 vs ~116).
        pendientes: pendientesValue, profesionalesActivos: currentRows.filter(rowHasActivity).length, today,
      }),
      ...extra,
    };
  }

  // W7/W10: the three download/report actions (XLSX, per-selection PDF,
  // mass export) all share the isDegraded||!stickersLoaded block, PLUS
  // `busy`/`exportInFlight` (L8: a mass export in flight, from THIS init or
  // an orphaned one) — a second export/report must never start while
  // stickers/surveys could be swapped out from under the first one's
  // already-snapshotted loop. The PDF button additionally needs a
  // professional SELECTED (seg-chart-professional).
  function updateDownloadAvailability() {
    // Blocked while degraded (identities are redacted, see fetchStickers
    // below) OR before stickers have resolved at all — exporting mid-flight
    // would silently ship a file whose sticker columns are all "unknown".
    const blocked = isDegraded || !stickersLoaded;
    const loadingTitle = 'Esperando a que carguen los stickers…';
    const busyTitle = 'Generando exportación masiva…';
    // L8: an export in flight from an OLD (now-orphaned) init after a
    // mid-export tab re-open must ALSO block this (fresh) init's own
    // buttons — `busy` alone (per-init) would miss that case.
    const exportBusy = busy || exportInFlight;
    const allBlocked = blocked || exportBusy;
    downloadBtn.disabled = allBlocked;
    downloadBtn.title = isDegraded ? DEGRADED_TITLE : (!stickersLoaded ? loadingTitle : exportBusy ? busyTitle : '');

    const hasSelection = Boolean(chartSelectEl.value);
    const reportState = reportSelectedButtonState({
      isDegraded, stickersLoaded, busy: exportBusy, hasSelection,
    });
    reportSelectedBtn.disabled = reportState.disabled;
    reportSelectedBtn.title = reportState.title;

    reportMassBtn.disabled = allBlocked;
    reportMassBtn.title = isDegraded ? DEGRADED_TITLE
      : !stickersLoaded ? loadingTitle
        : exportBusy ? busyTitle
          : 'Generar un PDF con el informe de cada profesional visible con actividad en el rango (según los filtros aplicados)';

    // L7: the per-row "📄 Reporte" buttons must reflect the SAME block
    // condition (rowReportButtonsBlocked) WITHOUT waiting for the next full
    // renderTable() pass — updateDownloadAvailability runs on its own (e.g.
    // right when `busy` flips at the start/end of a mass export), and a
    // stale, still-enabled row button would let a second export/report start
    // concurrently against the very data the mass loop already snapshotted.
    const rowsBlocked = rowReportButtonsBlocked({ isDegraded, stickersLoaded, busy: exportBusy });
    for (const btn of tbody.querySelectorAll('[data-seg-report]')) {
      btn.disabled = rowsBlocked;
    }
  }

  function renderStatusBanner() {
    // A single short-lived status line (loading / degraded / fetch error),
    // its own dedicated <p role="status"> below the header — NOT the old
    // eval-toolbar-meta span, which is sized for a one-line "actualizado…"
    // caption and overflows the DEGRADED_STICKERS_NOTE's ~250 characters.
    const text = degradedStickerNote(isDegraded, stickersLoaded)
      || stickerFetchErrorMessage
      || (!stickersLoaded ? 'Cargando stickers…' : '');
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
    // W7: seg-chart-professional now ALSO narrows the table (visibleRowsFor's
    // `professionalKey`), alongside the search box (visibleRowsFor's `query`,
    // matchesSearch — a >=3-digit query matches cédula OR tarjeta profesional
    // digits, otherwise name OR tarjeta profesional text, W-TP) — see
    // hasActiveSegFilters' own doc comment for why this is a genuine
    // contract change from before.
    // Phase 11: estado_sugerido is one more AND term (composes with search,
    // the professional select and — upstream, in buildProfessionalRows — the
    // Desde/Hasta range); it only exists while a depuracion is active.
    visibleRows = visibleRowsFor(rows, {
      query: searchEl.value, professionalKey: chartSelectEl.value || '', estado: estadoEl.value,
    });
    const sorted = sortRows(visibleRows, sortState.column, sortState.dir);
    const columns = columnsFor(subTab, {
      withEstado: currentIdentity.depuracionActiva, withEnfasis: currentIdentity.depuracionActiva,
    });
    theadRow.innerHTML = headerRowHtml(sortState, columns);
    // L8: `busy || exportInFlight` — `busy` covers an export THIS init
    // started; `exportInFlight` (module-level) covers one still running
    // from an OLD, now-orphaned init after a mid-export tab re-open (see
    // canStartMassExport's own doc comment).
    const rowsBusy = busy || exportInFlight;
    tbody.innerHTML = tableBodyHtml(sorted, stickersLoaded, isDegraded, columns, rowsBusy);
    // Task 4.4/4.5: GRUPO-EXTERNOS is NEVER one of `rows` (see
    // grupoExternosRowHtml's own doc comment) — appended as its own trailing
    // row, in EVERY sub-tab, regardless of search/professional filters (it
    // is a whole-dataset aggregate, not a per-professional record those
    // filters narrow).
    tbody.insertAdjacentHTML('beforeend', grupoExternosRowHtml(currentIdentity.grupoExternos, columns.length + 1));

    // Every filter control (search input, Desde/Hasta, seg-chart-professional)
    // re-renders through render() -> renderTable() (search's own debounce
    // calls renderTable directly) — updating the reset button's state in
    // this one shared spot keeps it in sync without a parallel check that
    // could drift.
    const active = hasActiveSegFilters({
      search: searchEl.value,
      from: fromEl.value || null,
      to: toEl.value || null,
      professional: chartSelectEl.value || null,
      estado: estadoEl.value,
    });
    resetFiltersBtn.disabled = !active;
    resetFiltersBtn.classList.toggle('is-filter-active', active);
    renderMassExportScope();
  }

  // Phase 11 (spec "Table And Export Reflect Depurado Fields Consistently"):
  // the mass PDF export's scope is stated BEFORE it runs — rows on screen
  // WITH activity in the range — and an over-cap scope is announced up
  // front. Only shown for a seeded (depuracion active) table: on the legacy
  // path every visible row has activity, so the scope is just "what you see".
  function renderMassExportScope() {
    const text = currentIdentity.depuracionActiva && stickersLoaded
      ? massExportScopeText(massExportScope(visibleRows))
      : '';
    reportScopeEl.hidden = !text;
    reportScopeEl.textContent = text;
  }

  // W8: skips upsertChart entirely when the last successfully rendered
  // chart's own dataKey (timelineDataKey) is unchanged — a re-render that
  // would produce the EXACT SAME chart (e.g. a sort-only interaction, which
  // still routes through render()) no longer re-diffs/repaints Chart.js for
  // no visual change. Reset to null whenever the chart is torn down (empty
  // range, Chart.js missing, a render error) so the NEXT successful render
  // always re-creates rather than silently staying blank.
  let lastChartDataKey = null;

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
      setChartEmpty('seguimiento-timeline', 'Gráfico no disponible (no se pudo cargar Chart.js).');
      lastChartDataKey = null;
      return;
    }
    const { from, to } = currentFilters();
    const professionalKey = chartSelectEl.value || null;
    const timeline = buildTimeline({
      stickers, surveys, from, to, professionalKey, identity: currentIdentity,
    });
    // W8: an empty timeline (no dated records in range at all, INCLUDING an
    // inverted `to < from`, which buildTimeline already returns as empty
    // labels) has nothing to plot — same empty-state convention (and
    // registry cleanup) as charts.js's own setChartEmpty, reused here rather
    // than duplicated.
    if (!timeline.labels.length) {
      setChartEmpty('seguimiento-timeline', 'Sin actividad en el rango seleccionado.');
      lastChartDataKey = null;
      return;
    }
    const dataKey = timelineDataKey(timeline, { from, to, professionalKey });
    // B1: gate the skip on BOTH the key match AND the chart still actually
    // being registered — a theme change destroys it (charts.js's
    // resetCharts()) without touching lastChartDataKey, so the key-only
    // check used to skip forever after a theme toggle, leaving the canvas
    // permanently blank. See shouldSkipChartRender's own doc comment.
    if (shouldSkipChartRender({ dataKey, lastKey: lastChartDataKey, hasChart: hasChart('seguimiento-timeline') })) return;
    try {
      // recreate: true — root.innerHTML is replaced on every open (see the
      // top of this function), which orphans the PREVIOUS open's <canvas>
      // even though charts.js's registry still holds a Chart instance bound
      // to it. Without recreate, upsertChart() would just call that stale
      // instance's update() — which repaints the detached old canvas, not
      // the new one actually on screen, so the chart stayed blank from the
      // second open on.
      // M4: clearChartEmpty BEFORE upsertChart — the canvas is left
      // `display:none` by a previous empty-range render (setChartEmpty), and
      // Chart.js measures the canvas's layout box at construction time; if
      // upsertChart ran first, `new Chart(...)` would initialize against a
      // hidden (0×0) canvas, right before it gets shown again a line later.
      clearChartEmpty('seguimiento-timeline');
      upsertChart('seguimiento-timeline', timelineChartConfig(timeline), { recreate: true });
      lastChartDataKey = dataKey;
    } catch (err) {
      console.warn('seguimiento: fallo al renderizar el gráfico de ritmo diario', err);
      setChartEmpty('seguimiento-timeline', 'Gráfico no disponible (error al renderizar).');
      lastChartDataKey = null;
    }
  }
  // Wrapped, not the bare closure: `root` stays in the DOM (just hidden)
  // after the user switches away from this tab, and a later 'themechange'
  // must not rebuild a chart nobody can see (see the module-level listener
  // above) — this is the SAME root main.js's switchView() toggles `.hidden`
  // on, so checking it here needs no separate visibility bookkeeping.
  activeRenderChart = () => {
    if (root.hidden) return;
    // B1: this wrapper is exactly what the module-level 'themechange'
    // listener calls, AFTER main.js's own listener has already destroyed
    // every registered chart via charts.js's resetCharts() — belt-and-
    // suspenders alongside the hasChart() gate inside renderChart itself:
    // force the next call to treat this as a fresh render regardless of
    // whether dataKey happens to still match.
    lastChartDataKey = null;
    renderChart();
  };
  // Reassigned on every initSeguimiento() call, same idea as
  // activeRenderChart above — main.js's onStoreChange always targets
  // whichever init is CURRENTLY open.
  //
  // W10: while a mass export is `busy`, the update is DEFERRED (stashed in
  // `pendingRecordsDuringExport`) instead of swapping `surveys`/re-rendering
  // immediately — the export loop already snapshotted its OWN `surveys`
  // reference at click time, so an in-place store refresh mid-export must
  // never race it; the deferred update is applied once, right after the
  // export's own `finally` clears `busy` (see generarReportesMasivos below).
  activeUpdateRecords = (newRecords) => {
    if (busy) { pendingRecordsDuringExport = newRecords; return; }
    surveys = Array.isArray(newRecords) ? newRecords : [];
    render();
  };

  function render() {
    // Identity is memoized (M4) on the stickers/surveys array REFERENCES —
    // a store refresh (updateSeguimientoRecords) swaps `surveys` in place
    // (a new reference), which still correctly invalidates this cache, so
    // identity never goes stale; but a re-render with the SAME references
    // (e.g. a sort-only interaction) now skips buildIdentityIndex entirely,
    // instead of rebuilding it unconditionally before segCache's own memo
    // check even ran (7.6 ms every render, regardless of whether segCache
    // itself would hit).
    currentIdentity = identityCache.get(stickers, surveys, () => buildIdentityIndex({ stickers, surveys, depuracion }));
    const today = bogotaToday();
    const { from, to } = currentFilters();
    // W6: memoized via segCache — a re-render with the SAME stickers/
    // surveys array references and the SAME from/to/today (e.g. a sort-only
    // interaction that still routes through render(), or two consecutive
    // opens with an unchanged store) skips recomputing buildProfessionalRows
    // (and its nested buildBarriosActivos pass per row) entirely.
    // `professionalKey` is reserved for a future unit that folds
    // buildTimeline's chart-selection filter into this same cached call; it
    // plays no role in buildProfessionalRows itself, so a fixed `null` here
    // never causes a spurious cache miss. seg-chart-professional's own
    // narrowing (W7) is applied AFTER this — by visibleRowsFor (table) and
    // buildTimeline's own `professionalKey` param (chart) — so it plays no
    // role in this cached aggregation pass either.
    const {
      rows, stickersWithoutDate, totals, unassigned,
    } = segCache.get(
      {
        stickers, surveys, from, to, professionalKey: null, today,
      },
      () => buildProfessionalRows({
        stickers, surveys, from, to, identity: currentIdentity, today,
      }),
    );
    currentRows = rows;
    // KPIs stay GLOBAL (every row, regardless of the search box or the
    // seg-chart-professional selection) — only stickersLoaded masks them.
    // Documented here since it's the one place that could look like an
    // oversight: renderTable below DOES narrow by both.
    kpisEl.innerHTML = kpisHtml({ rows, totals }, stickersLoaded);
    sinFechaNoteEl.hidden = !stickersLoaded || stickersWithoutDate === 0;
    sinFechaNoteEl.textContent = (stickersLoaded && stickersWithoutDate)
      ? `${stickersWithoutDate.toLocaleString('es-CO')} stickers sin fecha resoluble (importados sin evaluación asociada); se cuentan en totales, no en la curva.`
      : '';
    const unassignedText = stickersLoaded ? unassignedNote(unassigned) : null;
    unassignedNoteEl.hidden = !unassignedText;
    unassignedNoteEl.textContent = unassignedText || '';
    // seguimiento-inspectores-depurado, Fase 4 (tasks 4.6/4.7/4.8): freshness/
    // degraded badge + the "Revisión manual" section, both driven straight
    // off currentIdentity (always present, see buildIdentityIndex's own doc
    // comment) — recomputed every render() pass, same as every other
    // identity-derived note above.
    const depuracionBadgeText = depuracionBadgeHtml(currentIdentity, { loaded: stickersLoaded });
    depuracionBadgeEl.hidden = !depuracionBadgeText;
    depuracionBadgeEl.textContent = depuracionBadgeText || '';
    // Phase 11: a degraded/absent depuracion is a banner (role=alert), the
    // freshness line of an active one stays a neutral note.
    depuracionBadgeEl.classList.toggle('seg-depuracion-banner', Boolean(depuracionBadgeText) && depuracionBadgeIsDegraded(currentIdentity));
    if (depuracionBadgeText && depuracionBadgeIsDegraded(currentIdentity)) depuracionBadgeEl.setAttribute('role', 'alert');
    else depuracionBadgeEl.removeAttribute('role');
    // The estado filter only exists on top of an active depuracion; when it
    // goes away (a re-open, a degraded refresh) a leftover selection must not
    // keep silently narrowing the table.
    estadoFieldEl.hidden = !currentIdentity.depuracionActiva;
    if (!currentIdentity.depuracionActiva) estadoEl.value = ESTADO_ALL;
    revisionManualEl.innerHTML = revisionManualHtml(currentIdentity.revisionManual, { identity: currentIdentity, isAdmin });
    renderChartOptions(rows);
    renderTable(rows);
    renderChart();
    // M5: renderChartOptions can silently reset seg-chart-professional back
    // to '' when the previously selected professional no longer appears in
    // `rows` (e.g. a Desde/Hasta change narrows them out) — without this, the
    // per-selection "Reporte PDF individual" button stayed enabled/pointing
    // at a selection that no longer exists until some OTHER interaction
    // happened to call updateDownloadAvailability. Every render() (including
    // the one "Reiniciar filtros" triggers) must refresh it too.
    updateDownloadAvailability();
  }

  // W9: sub-tab segmented control — preserves sortState across the switch
  // ONLY when the current sort column still exists in the NEW sub-tab's
  // columnsFor() (e.g. 'activeDays'/'name'/'cedula'/'np'/'codigo', shared by
  // both); otherwise falls back to that sub-tab's own defaultSortFor. Search/
  // Desde-Hasta/professional-select filters are untouched by a sub-tab
  // switch (they narrow WHICH professionals show, not which columns do).
  function activateSubTab(btn) {
    if (!btn || btn.classList.contains('is-active')) return;
    subTab = btn.dataset.segSubtab;
    for (const b of subTabsEl.querySelectorAll('[data-seg-subtab]')) {
      const active = b === btn;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-selected', String(active));
      // Nit: roving tabindex — only the active tab is Tab-reachable; the
      // other is reached via ArrowLeft/ArrowRight (below), the standard
      // keyboard pattern for an ARIA tablist.
      b.tabIndex = active ? 0 : -1;
    }
    const stillSortable = columnsFor(subTab, { withEstado: currentIdentity.depuracionActiva, withEnfasis: currentIdentity.depuracionActiva })
      .some((c) => c.key === sortState.column);
    sortState = stillSortable ? sortState : defaultSortFor(subTab);
    renderTable(currentRows);
  }

  subTabsEl.addEventListener('click', (ev) => {
    activateSubTab(ev.target.closest('[data-seg-subtab]'));
  });

  // Nit: ArrowLeft/ArrowRight moves focus AND activates the adjacent tab
  // (wrapping at either end) — the standard keyboard contract for an ARIA
  // tablist, alongside the roving tabindex set in activateSubTab above.
  subTabsEl.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
    const tabs = [...subTabsEl.querySelectorAll('[data-seg-subtab]')];
    const currentIndex = tabs.findIndex((b) => b.classList.contains('is-active'));
    if (currentIndex < 0) return;
    ev.preventDefault();
    const delta = ev.key === 'ArrowRight' ? 1 : -1;
    const next = tabs[(currentIndex + delta + tabs.length) % tabs.length];
    activateSubTab(next);
    next.focus();
  });

  theadRow.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-seg-sort]');
    if (!btn) return;
    const col = btn.dataset.segSort;
    sortState = sortState.column === col
      ? { column: col, dir: sortState.dir === 'asc' ? 'desc' : 'asc' }
      : { column: col, dir: 'desc' };
    renderTable(currentRows);
  });

  // Task 4.4/4.5: GRUPO-EXTERNOS expand/collapse toggle — same delegated-on-
  // tbody pattern as the per-row report buttons below (tbody is rebuilt on
  // every renderTable call, so this listener must be delegated, never
  // attached to the button itself). Pure DOM state (the `hidden` attribute
  // + aria-expanded) — grupoExternosRowHtml itself never re-renders on
  // toggle, it only has to make sure the detail content EXISTS to reveal.
  tbody.addEventListener('click', (ev) => {
    const toggleBtn = ev.target.closest('[data-seg-externos-toggle]');
    if (!toggleBtn) return;
    const detail = tbody.querySelector('#seg-externos-detail');
    if (!detail) return;
    const nextExpanded = detail.hidden;
    detail.hidden = !nextExpanded;
    toggleBtn.setAttribute('aria-expanded', String(nextExpanded));
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
      await descargarInformeProfesional(row, { stickers, surveys, identity: currentIdentity }, buildReportCtx());
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
    if (activeSearchDebounced) activeSearchDebounced.trigger(); // null after teardownSeguimiento()
  });

  fromEl.addEventListener('change', render);
  toEl.addEventListener('change', render);
  // Phase 11: the estado filter narrows the TABLE only (rows/KPIs/chart are
  // computed upstream of it), so a change re-runs renderTable, not render().
  estadoEl.addEventListener('change', () => {
    renderTable(currentRows);
    updateDownloadAvailability();
  });
  // W7: seg-chart-professional now narrows the TABLE too (visibleRowsFor),
  // not just which line the chart highlights — so its 'change' handler must
  // re-run renderTable (not just renderChart), and refresh the per-selection
  // PDF button's enabled state (it depends on a selection existing).
  chartSelectEl.addEventListener('change', () => {
    renderTable(currentRows);
    renderChart();
    updateDownloadAvailability();
  });

  // "Reiniciar filtros": this tab's data-narrowing filters are the
  // professional search box, the Desde/Hasta date range, AND (W7)
  // seg-chart-professional — all read straight off the DOM (there's no
  // in-memory `filters` object to reset, unlike evaluaciones.js).
  //
  // CONTRATO CAMBIADO (W7): seg-chart-professional used to be deliberately
  // left untouched here (it only picked which line the chart highlighted,
  // never a table filter — a display-mode selection, like "colorear por").
  // Since it now ALSO narrows the table, "Reiniciar filtros" must clear it
  // too, or a professional selection would silently survive a reset that
  // claims to show "every professional" again. Sort order is still left
  // alone (never a data-narrowing filter).
  resetFiltersBtn.addEventListener('click', () => {
    if (activeSearchDebounced) activeSearchDebounced.cancel();
    searchEl.value = '';
    fromEl.value = '';
    toEl.value = '';
    chartSelectEl.value = '';
    estadoEl.value = ESTADO_ALL;
    render();
  });

  // Per-selection PDF report (W7): same orchestrator as the per-row button
  // (descargarInformeProfesional), just sourced from seg-chart-professional's
  // current value instead of a table row's data-seg-report attribute.
  // Disabled state (no selection / degraded / loading) is enforced by
  // updateDownloadAvailability — this handler is belt-and-suspenders, same
  // convention as the XLSX download button below.
  reportSelectedBtn.addEventListener('click', async () => {
    const key = chartSelectEl.value;
    if (!key) return;
    const row = currentRows.find((r) => r.key === key);
    if (!row) return;
    const originalLabel = reportSelectedBtn.textContent;
    reportSelectedBtn.disabled = true;
    reportSelectedBtn.textContent = 'Generando…';
    try {
      await descargarInformeProfesional(row, { stickers, surveys, identity: currentIdentity }, buildReportCtx());
    } catch (err) {
      console.error('seguimiento: fallo al generar el informe PDF (selección)', err);
      showToast('No se pudo generar el informe PDF.', 'error');
    } finally {
      reportSelectedBtn.textContent = originalLabel;
      updateDownloadAvailability();
    }
  });

  // W10: mass export — one PDF spanning every professional currently ON
  // SCREEN (visibleRows: search + Desde/Hasta + professional-select
  // narrowing, same set the XLSX export uses), one pageBreak per
  // professional (buildMassReportDocDefinition). Snapshots `loadSeq`/
  // `stickers`/`surveys`/`visibleRows` at click time so a store refresh
  // mid-build (deferred via `busy`, see activeUpdateRecords above) can never
  // shift the data out from under an already-running loop.
  //
  // L8: `canStartMassExport`/`shouldDeliverExport` are the module-level
  // guard — see their own doc comments for why `busy` alone (per-init)
  // isn't enough once a tab re-open mid-export is possible. A stale build
  // (tab reloaded before this finished) is never delivered at all, not just
  // toasted about after already downloading.
  async function generarReportesMasivos() {
    // Double-click guard (belt-and-suspenders alongside .disabled) — now
    // module-level (L8), so it also blocks a second export launched from a
    // freshly re-opened tab while an OLD one is still mid-build.
    if (!canStartMassExport({ exportInFlight })) return;
    if (isDegraded) { showToast('No se puede exportar: mostrando una copia de respaldo con datos incompletos.', 'error'); return; }
    if (!stickersLoaded) { showToast('Esperá a que carguen los stickers antes de exportar.', 'error'); return; }
    // Phase 11 / D17: the scope is the visible rows WITH activity in the range
    // (seeded zero-activity people have nothing to report); over the cap
    // (MASS_EXPORT_CAP, plan D5/W10 — a bigger document risks the performance
    // budget, < 20 s / < 700 MB for ~110 profesionales) the export is REFUSED
    // with the cap and the count, never truncated to a partial batch.
    const scope = massExportScope(visibleRows);
    if (scope.status === 'empty') {
      showToast(scope.visible ? 'Ningún profesional visible tiene actividad en el rango: no hay nada que exportar.' : 'No hay profesionales para exportar.', 'error');
      return;
    }
    if (scope.status === 'refused') { showToast(scope.message, 'error'); return; }
    const rowsToExport = scope.rows;
    if (rowsToExport.length > 60) {
      const proceed = confirm(`Vas a generar el informe de ${rowsToExport.length} profesionales en un solo PDF. Esto puede tardar. ¿Continuar?`);
      if (!proceed) return;
    }

    const seq = loadSeq;
    const stk = stickers;
    const svy = surveys;
    const identitySnapshot = currentIdentity;
    const ctx = buildReportCtx();
    exportInFlight = true;
    busy = true;
    updateDownloadAvailability();
    const originalLabel = reportMassBtn.textContent;

    // M6: show the blocking overlay and let the browser actually PAINT it
    // (one rAF, or a setTimeout(0) fallback when rAF isn't available — e.g.
    // this Node self-check has no DOM at all) BEFORE the loop/pdfmake build
    // below, which is effectively synchronous work on the main thread and
    // would otherwise freeze the page with no visible feedback at all.
    // display (not .hidden) — the element's own inline style already sets
    // `display:none` as its default; toggling `hidden` instead would fight
    // that SAME inline style's `align-items`/`justify-content` (an inline
    // style always wins over the `[hidden]` UA rule), leaving it visible
    // even while "hidden".
    exportOverlayTextEl.textContent = massExportOverlayText(rowsToExport.length);
    exportOverlayEl.style.display = 'flex';
    await new Promise((resolve) => {
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve());
      else setTimeout(resolve, 0);
    });

    try {
      // M6: ONE batch pass per source (professionalRecordsByKey/
      // visitasUltimos7DiasByKey) instead of calling professionalRecords/
      // visitasUltimos7Dias once PER professional — each per-row call used to
      // rescan the FULL stickers/surveys arrays on its own (O(rows ×
      // records)), the same class of quadratic blowup B1/M5 already fixed
      // for buildBarriosActivos/buildTemporalMetrics.
      const recordsByKey = professionalRecordsByKey({
        stickers: stk, surveys: svy, identity: identitySnapshot, from: ctx.from, to: ctx.to,
      });
      const last7ByKey = visitasUltimos7DiasByKey({
        stickers: stk, surveys: svy, identity: identitySnapshot, today: ctx.today,
      });
      const rowsWithPoints = [];
      for (let i = 0; i < rowsToExport.length; i += 1) {
        const row = rowsToExport[i];
        const points = recordsByKey.get(row.key) || { stickerPoints: [], surveyPoints: [] };
        const last7 = last7ByKey.get(row.key) || 0;
        rowsWithPoints.push({ row, points, last7 });
        // Yield to the event loop every ~10 professionals (plan's own
        // performance note: no task in the build phase should exceed
        // 50 ms) — also refreshes the progress label an admin sees during
        // what can be a multi-second synchronous-ish build.
        if ((i + 1) % 10 === 0 || i === rowsToExport.length - 1) {
          reportMassBtn.textContent = `Generando… ${i + 1}/${rowsToExport.length}`;
          await new Promise((resolve) => { setTimeout(resolve, 0); });
        }
      }
      const def = buildMassReportDocDefinition(rowsWithPoints, ctx);
      const pdfMake = await loadPdfmake();
      // L8: never deliver a build whose snapshot has gone stale (the tab was
      // re-opened mid-build, bumping loadSeq) — downloading it would ship a
      // file built from data that may no longer be live, and the pending-
      // records replay below must also be skipped (see the finally block).
      if (shouldDeliverExport({ seq, loadSeq })) {
        pdfMake.createPdf(def).download(`informes_seguimiento_masivo_${downloadStamp().slug}.pdf`);
        showToast('Reportes generados.');
      } else {
        showToast('La exportación se canceló porque la pestaña se recargó; vuelva a intentarlo.', 'error');
      }
    } catch (err) {
      console.error('seguimiento: fallo la exportación masiva de reportes', err);
      showToast('No se pudo generar la exportación masiva.', 'error');
    } finally {
      exportInFlight = false;
      busy = false;
      exportOverlayEl.style.display = 'none';
      reportMassBtn.textContent = originalLabel;
      updateDownloadAvailability();
      // A store refresh that arrived mid-export was deferred (see
      // activeUpdateRecords) — apply it now that this export is done,
      // exactly once, instead of dropping it silently. L8: skipped when the
      // tab was re-opened mid-build (seq !== loadSeq) — this closure's own
      // `render()`/tbody/etc. belong to an init that is no longer the live
      // one (a NEW init's own activeUpdateRecords already took over), so
      // replaying against them would touch a detached DOM for no benefit.
      if (seq === loadSeq && pendingRecordsDuringExport !== null) {
        const next = pendingRecordsDuringExport;
        pendingRecordsDuringExport = null;
        surveys = Array.isArray(next) ? next : [];
        render();
      }
    }
  }
  reportMassBtn.addEventListener('click', () => { generarReportesMasivos(); });

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
      // W9: two sheets (totales, temporales), each from the SAME visibleRows
      // (what is on screen right now — search + Desde/Hasta + professional-
      // select narrowing already applied), sorted by that sheet's OWN
      // default order regardless of which sub-tab happens to be showing on
      // screen at click time — an export always carries both views in full.
      const { legible, slug } = downloadStamp();
      // N13: "Filtros:" row — without it, an export narrowed by e.g. a
      // professional selection or a date range read identical to a full
      // export (only "Registros: N" differed), which could be misread as
      // the whole dataset. Uses the SAME search/Desde/Hasta/professional
      // state visibleRows itself was narrowed by (renderTable's own
      // visibleRowsFor call), plus the selected professional's resolved
      // NAME (never the raw ced:/nom: key) via currentRows.
      const selectedProfessionalKey = chartSelectEl.value || '';
      const selectedProfessionalRow = selectedProfessionalKey
        ? currentRows.find((r) => r.key === selectedProfessionalKey)
        : null;
      const filtrosSummary = xlsxFiltersSummary({
        search: searchEl.value,
        from: fromEl.value || null,
        to: toEl.value || null,
        professionalName: selectedProfessionalRow ? selectedProfessionalRow.name : '',
        estado: estadoEl.value,
      });
      // Formula injection (verified against SheetJS 0.20.3, the build loadXlsx
      // loads): aoa_to_sheet/sheet_add_json store every JS string as a typed
      // TEXT cell and the writer emits no formula element, so backend-supplied
      // names/entidad/motivos starting with = + - @ are shown, never evaluated.
      // Nothing to neutralize; a test guards against building formula cells.
      const wb = XLSX.utils.book_new();
      for (const sheetSubTab of ['totales', 'temporales']) {
        const sortSpec = defaultSortFor(sheetSubTab);
        const sorted = sortRows(visibleRows, sortSpec.column, sortSpec.dir);
        const rows = xlsxRowsFor(sorted, { subTab: sheetSubTab, withEnfasis: currentIdentity.depuracionActiva });
        const ws = XLSX.utils.aoa_to_sheet([
          [`Seguimiento — profesionales (${sheetSubTab})`],
          ['Fecha de generación:', legible],
          ['Registros:', rows.length],
          ['Filtros:', filtrosSummary],
          [],
        ]);
        XLSX.utils.sheet_add_json(ws, rows, { origin: 'A6' });
        XLSX.utils.book_append_sheet(wb, ws, sheetSubTab);
      }
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

  // W10: fetch reportes_agg.json's kpis.pendientes in the background — never
  // blocks the tab, never surfaces an error (fetchPendientes is fail-soft by
  // construction); objetivoDiario() already treats a still-null value the
  // same as a genuinely missing one ("sin dato" in the report, never 0).
  fetchPendientes().then((value) => { pendientesValue = value; });

  // PR 10 part 2 (D28): the sticker read is a revalidation of whatever was
  // painted above (`held`), or the first full fetch. Only a CHANGED snapshot,
  // a failure with nothing on screen, or a 401/403 touches the view.
  (async () => {
    const seq = ++loadSeq;
    const result = await revalidateSnapshot({ getToken, isAdmin, retryDelayMs: snapshotRetryDelayMs });
    if (seq !== loadSeq) return;
    if (result.outcome === 'discarded') return;
    if (result.outcome === 'unchanged') {
      // Nothing changed: no state assignment, no render() (skip-render). Only
      // a view that is NOT already showing this snapshot still needs it.
      if (stickersLoaded && stickers === result.snapshot.stickers) return;
    }
    if (result.outcome === 'unchanged' || result.outcome === 'fresh') {
      const { snapshot } = result;
      stickers = snapshot.stickers;
      stickersLoaded = true;
      isDegraded = snapshot.degraded;
      stickerFetchErrorMessage = '';
      // seguimiento-inspectores-depurado, Fase 4: reassigned in the SAME
      // synchronous block as `stickers` above -- identityCache's own
      // stickers/surveys-only key (M4) still invalidates correctly (see
      // the `let depuracion` declaration's own doc comment).
      depuracion = snapshot.depuracion;
    } else if (result.outcome === 'failed' && held) {
      // A failed revalidation keeps the retained render: never a blank table,
      // never an error banner over data that is still valid.
      return;
    } else {
      // First-open failure, or a 401/403 (the retained PII must leave the
      // view too): the Survey half stays fully rendered — the failure only
      // degrades the sticker-derived figures to DASH via renderStatusBanner/
      // render() below, it never blanks the whole tab.
      stickers = [];
      depuracion = null;
      stickersLoaded = false;
      isDegraded = false;
      const err = result.error;
      stickerFetchErrorMessage = `Stickers no disponibles: ${err && err.message ? err.message : String(err)}`;
    }
    renderStatusBanner();
    updateDownloadAvailability();
    render();
  })();
}
