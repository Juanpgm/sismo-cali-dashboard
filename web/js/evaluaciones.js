// Evaluaciones ATC-20 panel, rendered inside the Stickers tab.
//
// Three pieces over the same dataset: KPI tiles + a distribution bar, a
// Leaflet map in the Panel's visual language, and a detail modal with every
// field and photo of one evaluation.
//
// The colour code is the ATC-20 placard itself — green INSPECCIONADA, yellow
// USO RESTRINGIDO, red INSEGURO — which is why it reuses the Panel's
// habitability ramp instead of inventing a palette: on the map both views
// then read as the same language.
//
// Data comes from /api/stickers { action: 'evaluaciones' }, NOT straight from
// Firestore: the rules open the `evaluaciones` collection only to inspectores
// (integracion_F1/firestore.rules) and a dashboard admin is deliberately not
// one. The serverless function reads it with the admin SDK behind the same
// admin gate the rest of this tab already uses.
import {
  COLORS, escapeHtml, basemapTileUrl, normalize, loadXlsx, downloadStamp, showToast, faseKeyDe,
  createBasemapToggle, debounce, stableStringify,
} from './utils.js';
import { buildMiniMap, resolveBarrioComuna, prefetchGeo } from './mapview.js';
import { openLightbox } from './table.js';
import { renderMultiSelect } from './multiselect.js';
import { generarInformeEvaluacion } from './report.js';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
// A few evaluation coords land outside Cali and would drag fitBounds north
// (framing Cartago). Fit only to points inside this box; fall back to the
// fixed city view if none qualify.
const CALI_BBOX = { latMin: 3.30, latMax: 3.55, lngMin: -76.60, lngMax: -76.40 };

// Placard classes in escalating severity. Labels are lowercase on purpose:
// the KPI row reads as a sentence instead of shouting three states at once.
export const CLASES = [
  { key: 'INSPECCIONADA', label: 'inspeccionada', color: COLORS.status.h },
  { key: 'USO_RESTRINGIDO', label: 'uso restringido', color: COLORS.status.r2 },
  { key: 'INSEGURO', label: 'inseguro', color: COLORS.status.i2 },
];
const SIN_CLASE = { key: 'SIN_DATO', label: 'sin dato', color: COLORS.unknown };
const CLASE_BY_KEY = new Map(CLASES.map((c) => [c.key, c]));

/** Placard class of one evaluation, tolerant of spacing/case drift in the
 *  stored value ("Uso restringido" -> USO_RESTRINGIDO). */
export function claseDe(evaluacion) {
  const raw = String((evaluacion && evaluacion.clasificacion) || '')
    .trim().toUpperCase().replace(/[\s-]+/g, '_');
  return CLASE_BY_KEY.get(raw) || SIN_CLASE;
}

// Inspector Fase I/II — see utils.js's faseInspector for the business rule
// (NP category P3+ = Fase II). FASE_II first: same escalating-severity
// convention CLASES uses (worse/rarer state first).
export const FASES = [
  { key: 'FASE_II', label: 'fase II', color: COLORS.accent },
  { key: 'FASE_I', label: 'fase I', color: COLORS.unknown },
];
// Some Atención Sismo stickers carry no NP at all, so their Fase is
// unknowable — surfaced as its own state instead of the Firestore default
// (Fase I), which would be a lie for that source.
// '#9AA5B1' stays a literal on purpose: utils.js's COLORS has no neutral/
// unknown token distinct from COLORS.unknown ('#475569'), which SIN_CLASE
// above already uses — reusing it here would make the Clase and Fase
// "sin dato" pills indistinguishable on screen.
export const FASE_SIN_DATO = { key: 'SIN_DATO', label: 'sin dato', color: '#9AA5B1' };
const FASE_BY_KEY = new Map(FASES.map((f) => [f.key, f]));

/** Fase I/II of one evaluation. The Fase KEY itself ('FASE_I' | 'FASE_II' |
 *  'SIN_DATO') is derived by utils.js's `faseKeyDe` — the single shared
 *  rule report.js's `evalFaseLabelDe` also maps through its own label
 *  table, so the contrato v3 logic (fase field vs inspector.np fallback)
 *  lives in exactly one place. This wrapper only resolves that key to the
 *  {label, color} shape the rest of this module (chips, KPI colouring,
 *  filters) already expects. */
export function faseDe(evaluacion) {
  const key = faseKeyDe(evaluacion);
  return key === 'SIN_DATO' ? FASE_SIN_DATO : FASE_BY_KEY.get(key);
}

// "Colorear por" — same segmented control acciones-capa.js's own
// COLOR_VARS/colorSegmentedHtml pattern uses, two variables instead of
// three: which one drives the map marker + list dot colour. Both pills in
// listItemHtml stay visible regardless of the active mode — this only
// changes which dimension the DOT (map + row) emphasises.
// Exported: pure data, so a self-check can assert both entries lists carry
// their SIN_DATO state without the DOM.
export const COLOR_MODES = {
  clase: { label: 'Clasificación', colorOf: (e) => claseDe(e).color, entries: [...CLASES, SIN_CLASE] },
  fase: { label: 'Fase', colorOf: (e) => faseDe(e).color, entries: [...FASES, FASE_SIN_DATO] },
};
let colorMode = 'clase';

function colorSegmentedHtml() {
  return Object.entries(COLOR_MODES).map(([key, def]) => `
    <button type="button" class="segmented-btn${key === colorMode ? ' is-active' : ''}"
      data-eval-color="${key}" role="tab" aria-selected="${key === colorMode}">${escapeHtml(def.label)}</button>`).join('');
}

/** Count per placard class, keyed by CLASES[].key (plus SIN_DATO). */
export function contarPorClase(evaluaciones) {
  const counts = Object.fromEntries([...CLASES, SIN_CLASE].map((c) => [c.key, 0]));
  for (const e of evaluaciones) counts[claseDe(e).key] += 1;
  return counts;
}

const pct = (part, total) => (total ? Math.round((part / total) * 1000) / 10 : 0);

function formatFecha(iso) {
  if (!iso) return 'Sin fecha';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('es-CO', { dateStyle: 'medium', timeStyle: 'short' });
}

// Exported: pure, so a self-check can assert the 'Sin dirección' fallback
// without the DOM. The row title and modal heading must never render blank.
export const tituloDe = (e) => e.descripcion.nombre || e.descripcion.direccion || e.codigo_edificacion || 'Sin dirección';

// "Who did this" label for the list row (misattribution-risk fix
// 2026-09-08, see backend/app/services/stickers_atencionsismo.py's
// inspector_fuente doc comment): brigade codes are reused after an
// inspector is deleted, so a roster-fallback identity (`inspector_fuente
// === 'roster'`) names whoever CURRENTLY holds the code, not necessarily
// who did this evaluation — it must never read as a confirmed name. A
// record with no `inspector_fuente` field at all (Firestore-sourced, or a
// matched atencionsismo evaluación) keeps today's plain behaviour.
// `inspector_fuente === 'api'` (contrato v3, 2026-09-08) is the API's own
// `profesional`, joined by the unique cédula — no code-reuse risk — so it
// behaves exactly like a verified 'evaluacion' match, no caveat.
// Exported: pure, so a self-check can assert this without the DOM.
export function quienDe(e) {
  const nombre = (e.inspector && e.inspector.nombre_completo) || '';
  const codigo = (e.inspector && e.inspector.codigo) || '';
  if (e.inspector_fuente === 'roster') {
    return nombre
      ? `Código ${codigo || '—'} · titular actual: ${nombre}`
      : `Código ${codigo || '—'} (sin identidad verificada)`;
  }
  if (nombre) return nombre;
  // 'Sin identificar' also covers a plain Firestore-sourced record (no
  // inspector_fuente field at all) with no name and no código — not just
  // the atencionsismo sources above.
  return codigo ? `Brigada ${codigo}` : 'Sin identificar';
}

// xlsx export's "inspector_verificado" column (misattribution-risk fix
// 2026-09-08): flags whether the exported name/identificación came from a
// verified Firestore match, an unverified roster-by-code fallback, or the
// API's own `profesional` (contrato v3, 2026-09-08), alongside the raw
// values (never mutates them — a spreadsheet consumer needs both).
// 'api' reads "Según Atención Sismo", not "Sí": this column asks whether
// WE verified the identity locally, and an 'api'-sourced identity is the
// upstream API's own unverified report, not a local match.
// Exported: pure, so a self-check can assert it without xlsx.
export function inspectorFuenteLabel(e) {
  if (e.inspector_fuente === 'evaluacion') return 'Sí';
  if (e.inspector_fuente === 'roster') return 'No (código de brigada)';
  if (e.inspector_fuente === 'api') return 'Según Atención Sismo';
  return '';
}

// ---- Silent-poll fingerprint -------------------------------------------------

/** Stable serialization of one evaluación — the input to the silent-poll
 *  fingerprint below. Bug fixed 2026-09-10 (v1): the fingerprint covered
 *  only id/clasificacion/fotos-length, so a change to fase, inspector name/
 *  código, coords or descripción never triggered a silent poll's re-render
 *  — the tab would keep showing stale data indefinitely until the next
 *  manual "Actualizar" or tab reopen. Bug fixed 2026-09-10 (v2, this fix):
 *  v1's replacement was still a HAND-PICKED subset of fields, so a change to
 *  restricciones, alcance, acciones_posteriores.*, area, area_nombre,
 *  municipio, origen, color_etiqueta, consecutivo,
 *  inspector.identificacion/entidad, coords.accuracy, or a fotos URL (same
 *  count, different content) — every one of them read by the detail modal
 *  and/or the PDF/xlsx export — was STILL silently invisible to the poll: a
 *  direct violation of the "performance must never trade away data quality/
 *  freshness" guarantee, not just a missed edge case.
 *
 *  Fix: fingerprint the WHOLE record via stableStringify() (utils.js)
 *  instead of hand-picking fields. stableStringify sorts object keys
 *  recursively, so a source field REORDERING still can never change the
 *  fingerprint — only an actual VALUE change can — but every field, present
 *  or future, is automatically covered: a view starting to read a field
 *  nobody picked before can no longer reopen this exact bug class. Measured
 *  cost on the full 2046-record atencionsismo dataset stays well within the
 *  "keep it cheap" budget the fingerprint has always had (see load()'s own
 *  comment). Exported: pure, so a self-check can assert it without the DOM. */
export function evaluacionRenderKey(e) {
  return stableStringify(e);
}

/** Whole-list fingerprint the silent auto-refresh compares against the
 *  previous one: identical string -> nothing rendered changed -> skip the
 *  re-render entirely (see load()'s own comment on why: it would reset the
 *  map's pan/zoom mid-use for zero visible change). Exported: pure, so a
 *  self-check can assert the differs-only-in-fase / identical / field-order
 *  cases without the DOM. */
export function fingerprintEvaluaciones(list) {
  return JSON.stringify((list || []).map(evaluacionRenderKey));
}

// ---- Filters -------------------------------------------------------------

/** Chip group for the ATC-20 classification filter — same shape as
 *  planeacion.js's filtersHtml() chips (data-filter-group/-value, delegated
 *  click), one group instead of two. */
function claseChipsHtml(activeKey) {
  const chip = (value, label, active) => `<button type="button" class="asignacion-chip${active ? ' is-active' : ''}" data-filter-group="clase" data-filter-value="${value}">${escapeHtml(label)}</button>`;
  return [
    chip('', 'Todas', !activeKey),
    ...CLASES.map((c) => chip(c.key, c.label, activeKey === c.key)),
    chip(SIN_CLASE.key, SIN_CLASE.label, activeKey === SIN_CLASE.key),
  ].join('');
}

/** Chip group for the inspector Fase I/II filter — same shape as
 *  claseChipsHtml above, one group ('fase') instead of 'clase'. Each fase
 *  chip carries its own --chip-accent (see #eval-fase-chips in styles.css)
 *  so Fase I/II read as two different colours, not just two labels —
 *  "Todas" stays neutral, same as the clase group's own "Todas". */
function faseChipsHtml(activeKey) {
  const chip = (value, label, active, color) => `<button type="button" class="asignacion-chip${active ? ' is-active' : ''}" data-filter-group="fase" data-filter-value="${value}"${color ? ` style="--chip-accent:${color}"` : ''}>${escapeHtml(label)}</button>`;
  return [
    chip('', 'Todas', !activeKey),
    ...FASES.map((f) => chip(f.key, f.label, activeKey === f.key, f.color)),
    chip(FASE_SIN_DATO.key, FASE_SIN_DATO.label, activeKey === FASE_SIN_DATO.key, FASE_SIN_DATO.color),
  ].join('');
}

// F7: the Stickers tab shows two Fase signals depending on the active
// source (module docstring: for atencionsismo, `fase` per the API's own
// process; for Formulario/Firestore, the inspector's NP category) — the
// same evaluación can land on a different Fase in each. Every record in
// `evaluaciones` shares one `fuente` (a Fuente switch re-fetches the whole
// list, design D3 — sources are never merged), so the FIRST record's
// `fuente` speaks for the whole loaded set. Returns `null` (not '') when
// the note should not show — the caller drives the element's `.hidden`
// directly off truthiness, no separate empty-string check needed.
// Exported: pure, so a self-check can assert it without the DOM.
export function fuenteFaseNotaDe(evaluaciones) {
  const activeFuente = Array.isArray(evaluaciones) && evaluaciones.length ? evaluaciones[0].fuente : null;
  if (activeFuente !== 'atencionsismo') return null;
  return 'Fase según el proceso de Atención Sismo (paso 1 = Fase I, paso 2 = Fase II); '
    + 'puede diferir de la Fase por NP del Formulario.';
}

/** Filtered view of `list`: clasificación ATC-20, inspector Fase I/II,
 *  comuna/barrio (resolved client-side, see resolveBarrioComuna) and a
 *  free-text search across inspector + edificación fields. Case/accent-
 *  insensitive (normalize() strips both), same as the rest of the
 *  dashboard's search boxes. Exported so a pure self-check can exercise it
 *  without the DOM. */
export function applyFilters(list, filters) {
  const q = filters.search ? normalize(filters.search) : '';
  return list.filter((e) => {
    if (filters.clase && claseDe(e).key !== filters.clase) return false;
    if (filters.fase && faseDe(e).key !== filters.fase) return false;
    // comuna/barrio are Sets (multiselect, 2026-09 conversion): an empty or
    // missing Set means "todas" — only a non-empty Set actually narrows, and
    // a record with no resolved comuna/barrio (null) can never satisfy one.
    if (filters.comuna && filters.comuna.size && !filters.comuna.has(e._comuna)) return false;
    if (filters.barrio && filters.barrio.size && !filters.barrio.has(e._barrio)) return false;
    if (q) {
      const hay = normalize([
        e.inspector.nombre_completo, e.inspector.codigo,
        e.descripcion.nombre, e.descripcion.direccion, e.codigo_edificacion,
      ].filter(Boolean).join(' '));
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

/** Human-readable summary of the active filters, for the xlsx header block.
 *  Exported: pure, so a self-check can exercise the SIN_DATO fallbacks
 *  without the DOM. */
export function describeFilters(f) {
  const parts = [];
  if (f.clase) parts.push(`Clasificación: ${(CLASE_BY_KEY.get(f.clase) || SIN_CLASE).label}`);
  if (f.fase) parts.push(`Fase: ${(FASE_BY_KEY.get(f.fase) || FASE_SIN_DATO).label}`);
  // Multi-select: every chosen value joins the line, not just one.
  if (f.comuna && f.comuna.size) parts.push(`Comuna: ${[...f.comuna].join(', ')}`);
  if (f.barrio && f.barrio.size) parts.push(`Barrio: ${[...f.barrio].join(', ')}`);
  if (f.search) parts.push(`Búsqueda: "${f.search}"`);
  return parts.length ? parts.join(' · ') : 'Todos los registros';
}

// No barrio/comuna field exists on evaluaciones — resolved client-side per
// point against the same comunas/barrios boundaries the Panel's choropleth
// uses (mapview.resolveBarrioComuna). Memoized by rounded coordinate: several
// evaluaciones of the same building share the exact same point, and caching
// the in-flight PROMISE (not just its result) means duplicate coords issued
// in the same load() share one point-in-polygon resolution instead of racing
// separate ones.
const geoCache = new Map();

/** Comuna/barrio for one (lat, lng), memoized. F3 bug fix (2026-09-10): the
 *  cached promise had no `.catch` at all, so ONE geojson-fetch failure left
 *  a permanently REJECTED promise cached under that coordinate's key —
 *  every later load()/silent poll that happened to touch the same
 *  coordinate hit the error branch forever, even once the underlying fetch
 *  would have succeeded again. Fix: evict the key on rejection before
 *  re-throwing, so a later call for the same coordinate actually retries
 *  instead of reading a stale cached failure — the rejection itself still
 *  propagates to the caller exactly as before (load()'s existing catch
 *  handling is unchanged), only the MEMOIZATION of the failure is removed.
 *  `resolveFn` defaults to mapview.resolveBarrioComuna and is only ever
 *  overridden by tests (mirrors vuelos-uas.js's own resolveGeoFor(lat, lng,
 *  resolveFn) shape so both are testable in isolation the same way).
 *  Exported so a self-check can assert the memoization/failure-eviction/
 *  recovery-on-retry contracts without the DOM. */
export function resolveGeoFor(lat, lng, resolveFn = resolveBarrioComuna) {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return Promise.resolve({ _comuna: null, _barrio: null });
  }
  const key = `${Number(lat).toFixed(5)},${Number(lng).toFixed(5)}`;
  if (!geoCache.has(key)) {
    geoCache.set(key, resolveFn(lat, lng)
      .then(({ comuna, barrio }) => ({ _comuna: comuna, _barrio: barrio }))
      .catch((err) => {
        geoCache.delete(key); // never memoize a failure — a later call must retry, not stay degraded forever
        throw err; // propagate exactly as before this fix — load()'s catch still handles it
      }));
  }
  return geoCache.get(key);
}

/** comuna -> Set(barrio) across evaluaciones with a resolved comuna. Records
 *  without coords (or that fell outside every polygon) don't offer a comuna/
 *  barrio to filter by — they still export under "Sin dato". */
function comunaBarrioMap(list) {
  const map = new Map();
  for (const e of list) {
    if (!e._comuna) continue;
    if (!map.has(e._comuna)) map.set(e._comuna, new Set());
    if (e._barrio) map.get(e._comuna).add(e._barrio);
  }
  return map;
}

/** Sorted {value,label} options for the comuna multiselect — same source
 *  comunaBarrioMap already builds, reshaped for renderMultiSelect's own
 *  `options` contract. Exported: pure, so a self-check can assert the sort/
 *  empty-map cases without the DOM. */
export function comunaOptionsFrom(comunaMap) {
  return [...comunaMap.keys()].sort().map((c) => ({ value: c, label: c }));
}

/** Barrio options = union of barrios across EVERY currently selected comuna,
 *  not just one — a "Comuna 5 + Comuna 8" selection offers both comunas'
 *  barrios together, the same way the map/list below show both comunas'
 *  records together. Zero selected comunas -> zero options, which the
 *  caller (initEvaluaciones) reads as "disable the barrio control" —
 *  renderMultiSelect has no disabled state of its own, so the mounted
 *  toggle button's `.disabled` is set directly, same idea as the old
 *  `selectEl.disabled = !comuna`. Exported: pure, so a self-check can
 *  assert the union/empty rules without the DOM. */
export function barrioOptionsFrom(comunaMap, comunaSet) {
  if (!comunaSet || !comunaSet.size) return [];
  const barrios = new Set();
  for (const comuna of comunaSet) {
    for (const b of comunaMap.get(comuna) || []) barrios.add(b);
  }
  return [...barrios].sort().map((b) => ({ value: b, label: b }));
}

/** New Set holding only the members of `set` still present in `validValues`
 *  (an array or Set) — the one prune rule behind two different moments: a
 *  load() refresh dropping a comuna/barrio no longer offered by the fresh
 *  data, and a comuna toggle dropping a barrio that no longer belongs to
 *  ANY currently selected comuna. Exported: pure, so a self-check can assert
 *  what survives/what's dropped without the DOM. */
export function pruneToValid(set, validValues) {
  const valid = validValues instanceof Set ? validValues : new Set(validValues);
  return new Set([...set].filter((v) => valid.has(v)));
}

/** Drops any member of `barrioSet` that no longer belongs to ANY comuna in
 *  `comunaSet`, per `comunaMap` (comuna -> Set(barrio)) — composes
 *  barrioOptionsFrom + pruneToValid, the one rule behind two different
 *  moments: a comuna toggle narrowing the selection, and a load() refresh
 *  rebuilding comunaMap from fresh data. A barrio belonging to a comuna that
 *  is STILL selected is kept even if it also belonged to a comuna that was
 *  just deselected (union semantics, same as barrioOptionsFrom itself) —
 *  never over-pruned. Exported: pure, so a self-check can assert every case
 *  (still valid / no longer under any selected comuna / empty comunaSet /
 *  already-empty barrioSet) without the DOM. */
export function pruneInvalidBarrios(barrioSet, comunaSet, comunaMap) {
  return pruneToValid(barrioSet, barrioOptionsFrom(comunaMap, comunaSet).map((o) => o.value));
}

/** Default (empty) filters shape for the evaluaciones panel — a fresh Set
 *  instance for comuna/barrio on every call, never a shared reference, so a
 *  reset can't accidentally hand back a Set some other closure still holds
 *  and mutates later. Exported so "Reiniciar filtros" and the initial
 *  `filters` declaration in initEvaluaciones share exactly one definition,
 *  and so a self-check can assert the fresh-Set contract without the DOM. */
export function defaultEvalFilters() {
  return { search: '', clase: '', fase: '', comuna: new Set(), barrio: new Set() };
}

/** Whether any of this panel's filters is currently narrowing the view —
 *  drives "Reiniciar filtros"' enabled/disabled + soft-orange state. Tolerant
 *  of a partial/malformed filters object (comuna/barrio missing, not yet a
 *  Set) so it can never throw mid-render. Exported so a self-check can cover
 *  the no-filters/one-filter/cleared-back-to-none transitions without DOM. */
export function hasActiveEvalFilters(filters) {
  if (!filters) return false;
  // F9: a whitespace-only search must not read as active — it never
  // actually narrows applyFilters' own search branch either (normalize()
  // trims), so the reset button must agree.
  return Boolean(
    (filters.search && filters.search.trim())
    || filters.clase
    || filters.fase
    || (filters.comuna && filters.comuna.size)
    || (filters.barrio && filters.barrio.size),
  );
}

/** has/delete/add dance for a multiselect's onToggle callback — one place
 *  instead of duplicating it for comuna and for barrio. Mutates `set`
 *  in place (matches Set.prototype.add/delete's own contract) and returns
 *  nothing; callers that need the new state read the same Set reference
 *  back, same as today's onToggle callbacks in mountComuna/mountBarrio do.
 *  Exported: pure/DOM-free, so a self-check can assert the mutate-in-place
 *  contract directly. */
export function toggleSetValue(set, value) {
  if (set.has(value)) set.delete(value); else set.add(value);
}

/** Whether the Barrio toggle button should be disabled: true only when no
 *  comuna is selected (an empty comunaSet, mirroring pruneInvalidBarrios /
 *  barrioOptionsFrom's own "zero comunas -> zero options" rule) — barrio has
 *  nothing to filter by until at least one comuna narrows the option list.
 *  Exported: pure, so a self-check can assert both states without the DOM. */
export function barrioDisabledFor(comunaSet) {
  return !comunaSet || comunaSet.size === 0;
}

// ---- Static markup -----------------------------------------------------------

/** The section's skeleton. Contents are filled in by render() once the data
 *  lands, so the map container exists before Leaflet is asked to mount.
 *
 *  Map and record list share one card side by side: they are two readings of
 *  the same rows, the tab has the width for it, and stacking them buried the
 *  inspector roster under a screen and a half of scroll. */
export function sectionHtml() {
  return `
    <section class="eval-section" aria-label="Evaluaciones ATC-20">
      <div class="section-bar">
        <h3 class="section-bar-title">Evaluaciones ATC-20</h3>
        <button type="button" class="sticker-action" id="eval-reload">Actualizar</button>
        <button type="button" class="btn-clear" id="eval-reset-filters" disabled>Reiniciar filtros</button>
      </div>

      <div class="eval-degraded-banner" id="eval-degraded-banner" hidden role="status">
        Mostrando una copia de respaldo porque no hay conexión con la fuente en vivo:
        la clasificación Fase I/Fase II puede no ser correcta, y los nombres, fotos y
        comentarios no están disponibles hasta que se reconecte.
      </div>

      <div class="kpi-row eval-kpis" id="eval-kpis"></div>
      <div class="eval-bar" id="eval-bar"></div>

      <div class="eval-filters" id="eval-filters">
        <div class="asignacion-search">
          <input type="search" id="eval-search" class="sticker-search-input"
            placeholder="Buscar por inspector, dirección, nombre o código…" aria-label="Buscar evaluaciones">
        </div>
        <div class="card-toolbar asignacion-filters">
          <div class="asignacion-filters-group" id="eval-clase-chips">${claseChipsHtml('')}</div>
          <div class="asignacion-filters-group" id="eval-fase-chips">${faseChipsHtml('')}</div>
          <div class="filter-block" id="eval-comuna-filter"></div>
          <div class="filter-block" id="eval-barrio-filter"></div>
          <button type="button" class="sticker-action" id="eval-download">Descargar .xlsx</button>
        </div>
        <p class="sticker-note" id="eval-fase-fuente-note" hidden></p>
      </div>

      <div class="card eval-workspace-card">
        <div class="card-toolbar">
          <span class="eval-toolbar-title">Puntos de evaluación</span>
          <div class="segmented" role="tablist" aria-label="Colorear por" data-eval-color-group>${colorSegmentedHtml()}</div>
          <span class="eval-toolbar-meta" id="eval-map-meta"></span>
        </div>
        <div class="eval-workspace">
          <div class="eval-map" id="eval-map"></div>
          <div class="eval-aside">
            <div class="eval-aside-head">
              <h4>Registros</h4>
              <span class="eval-toolbar-meta" id="eval-list-meta"></span>
            </div>
            <ul class="eval-list" id="eval-list"></ul>
          </div>
        </div>
      </div>

      <div class="modal" id="eval-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="eval-modal-title">
        <div class="modal-backdrop" data-eval-close></div>
        <div class="modal-panel">
          <div class="modal-header">
            <h2 id="eval-modal-title">Detalle de la evaluación</h2>
            <button type="button" class="btn-icon accion-pdf-btn" id="eval-modal-pdf" title="Descargar informe PDF" aria-label="Descargar informe PDF">${PDF_ICON}</button>
            <button type="button" class="btn-icon" data-eval-close aria-label="Cerrar">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
            </button>
          </div>
          <div class="modal-body" id="eval-modal-body"></div>
        </div>
      </div>
    </section>`;
}

// ---- KPIs --------------------------------------------------------------------

/** Which classes get a tile: the three placards always, "sin dato" only when
 *  it actually happened — an always-zero tile beside three real ones dilutes
 *  the row. */
function clasesVisibles(counts) {
  return counts[SIN_CLASE.key] ? [...CLASES, SIN_CLASE] : CLASES;
}

function kpisHtml(evaluaciones) {
  const total = evaluaciones.length;
  const counts = contarPorClase(evaluaciones);

  const tiles = clasesVisibles(counts).map((c) => `
    <div class="kpi-tile" style="--kpi-accent:${c.color}">
      <span class="kpi-label kpi-label-lower">${c.label}</span>
      <span class="kpi-value">${counts[c.key]}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">${pct(counts[c.key], total)}% del total</span></div>
    </div>`).join('');

  // The total tile stays deliberately colourless: green/yellow/red mean a
  // placard here, and a gold bar over "registros" reads as a fourth state.
  return `
    <div class="kpi-tile is-neutral">
      <span class="kpi-label kpi-label-lower">registros</span>
      <span class="kpi-value">${total}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">evaluaciones registradas en la fuente seleccionada</span></div>
    </div>
    ${tiles}`;
}

/** One-line proportion strip under the tiles. No legend of its own: the
 *  segments run in the same order and colour as the tiles right above it. */
function barHtml(evaluaciones) {
  const total = evaluaciones.length;
  if (!total) return '';
  const counts = contarPorClase(evaluaciones);
  const segs = clasesVisibles(counts).map((c) => {
    const share = pct(counts[c.key], total);
    if (share <= 0) return '';
    return `<div class="hab-bar-seg" style="width:${share}%;background:${c.color}" title="${escapeHtml(c.label)}: ${counts[c.key]}"></div>`;
  }).join('');
  return `
    <span class="eval-bar-label">distribución</span>
    <div class="hab-bar">${segs}</div>`;
}

// ---- Map ---------------------------------------------------------------------

// One Leaflet instance PER initEvaluaciones session. The Stickers view
// re-renders its root on every open (a NEW #eval-map container each time),
// which detaches the old one — remove() first or each visit leaks a map
// plus its tile requests. WITHIN one session, though, the map is built once
// (buildMapOnce, called lazily by the first renderMap) and stays alive
// across every renderFiltered() that session sees (initial load, every
// filter/search change, every silent poll) — only its markers are diffed
// (see diffMarkerIds/renderMap below), not the whole map/tile/legend
// scaffold. Perf (2026-09-10): tearing down and rebuilding L.map() + the
// tile layer + the legend control on every keystroke was ~42% of
// renderFiltered()'s cost on real data (2046 records) for work that, once
// the map already exists, only needs to happen when the SET of points on
// screen actually changes.
let map = null;
let baseTile = null;
let basemapToggle = null;
let pointsLayer = null;
let legendEl = null;
// Bounds fitted at build time. The map is built while its section is hidden
// (0×0), so the build-time fit computes a wrong zoom; re-applied on invalidate
// once the section is visible and correctly sized.
let lastFitBounds = null;
// id -> circleMarker, so hovering a row in the aside can point at the map.
let markerById = new Map();

function teardownMap() {
  if (map) { map.remove(); map = null; }
  baseTile = null;
  basemapToggle = null;
  pointsLayer = null;
  legendEl = null;
  markerById = new Map();
}

// The Panel swaps its own tiles on theme change (mapview.applyMapTheme); this
// map has to do the same or a light-mode dashboard keeps a dark basemap here.
// Registered once at module scope so reopening the tab can't stack listeners;
// guarded so the pure-logic self-check can import this module under Node.
if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => {
    if (!map || !baseTile) return;
    map.removeLayer(baseTile);
    baseTile = L.tileLayer(basemapTileUrl(), {
      attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20,
    }).addTo(map);
    baseTile.bringToBack();
    // Recreating the street layer just re-added it on top of a satellite
    // layer if the toggle was in satellite mode — undo that, see
    // createBasemapToggle's own doc comment (utils.js).
    if (basemapToggle) basemapToggle.notifyStreetLayerRecreated();
  });
}

function popupHtml(e) {
  const c = claseDe(e);
  const f = faseDe(e);
  return `
    <div class="map-popup">
      <h4>${escapeHtml(tituloDe(e))}</h4>
      <dl>
        <dt>Código</dt><dd>${escapeHtml(e.codigo_edificacion)}</dd>
        <dt>Clasificación</dt><dd>${escapeHtml(c.label)}</dd>
        <dt>Fase</dt><dd>${escapeHtml(f.label)}</dd>
        <dt>Dirección</dt><dd>${escapeHtml(e.descripcion.direccion || 'Sin dato')}</dd>
        <dt>Inspector</dt><dd>${escapeHtml(e.inspector.nombre_completo || e.inspector.codigo || 'Sin dato')}</dd>
        <dt>Fecha</dt><dd>${escapeHtml(formatFecha(e.fecha))}</dd>
        <dt>Fotos</dt><dd>${(e.fotos || []).length}</dd>
      </dl>
      <button type="button" class="btn-link" data-eval-detail="${escapeHtml(e.id)}">Ver detalle &rarr;</button>
    </div>`;
}

/** Legend content for whichever variable "Colorear por" currently drives —
 *  same shape claseDe/faseDe already share ({label, color}), so both
 *  COLOR_MODES entries render through one function. */
function colorLegendHtml() {
  const def = COLOR_MODES[colorMode];
  return `
    <div class="legend-title">${escapeHtml(def.label)}</div>
    ${def.entries.map((c) => `
      <div class="legend-row">
        <span class="legend-swatch legend-circle" style="background:${c.color}"></span>
        <span>${escapeHtml(c.label)}</span>
      </div>`).join('')}`;
}

/** Which marker ids to add/restyle/remove to bring the map from its CURRENT
 *  set of markers (`prevIds`) to the next filtered render (`nextEvaluaciones`,
 *  the UNFILTERED-by-coords list — rows without coords are skipped here the
 *  same way renderMap's own old `conCoords` filter did). Pure/DOM-free: the
 *  actual Leaflet add/remove/restyle calls live in renderMap below, this
 *  only decides which ids fall in which bucket. Exported so a self-check can
 *  assert every transition without Leaflet or the DOM.
 *
 *  `toRestyle` covers every id present in BOTH sets, unconditionally — not
 *  just the ones whose visible fields actually changed. Two reasons this is
 *  deliberately generous rather than a deep-equality diff: (1) a silent poll
 *  can refresh a record's fase/clasificacion/coords while its id stays the
 *  same, and a kept marker's fillColor/popup/position must track the NEW
 *  record, never keep serving the old one; (2) "Colorear por" can flip
 *  colorMode with the exact same id set on screen, which likewise needs
 *  every marker restyled even though no record itself changed. Cheap either
 *  way: renderMap only calls .setStyle()/.setLatLng()/re-binds the popup on
 *  these, never recreates the marker object.
 *
 *  A duplicate id inside `nextEvaluaciones` keeps only its FIRST occurrence
 *  — mirrors markerById (a Map, one entry per id) never having room for a
 *  second marker under the same key anyway. */
export function diffMarkerIds(prevIds, nextEvaluaciones) {
  const prevSet = prevIds instanceof Set ? prevIds : new Set(prevIds);
  const nextIds = new Set();
  const toAdd = [];
  const toRestyle = [];
  for (const e of nextEvaluaciones || []) {
    if (!e || !e.coords) continue; // no coords -> never gets a marker, same as the old conCoords filter
    if (nextIds.has(e.id)) continue; // duplicate id this render: first occurrence wins
    nextIds.add(e.id);
    (prevSet.has(e.id) ? toRestyle : toAdd).push(e);
  }
  const toRemove = [...prevSet].filter((id) => !nextIds.has(id));
  return { toAdd, toRestyle, toRemove };
}

/** (Re)binds a marker's popup + its popupopen "Ver detalle" wiring to
 *  EVALUACIÓN `e`. Popup content is a function (Leaflet calls it only when
 *  the popup is actually opened), not a pre-built string — F2 (perf,
 *  2026-09-10): building `popupHtml(e)` eagerly for every one of N markers
 *  on every render was pure waste for the ~99% of markers whose popup is
 *  never opened. `.off('popupopen')` before re-binding so a marker that
 *  gets restyled across many renders (see diffMarkerIds's `toRestyle`
 *  above) never accumulates duplicate listeners each firing onDetail
 *  once more than the last. */
function bindMarkerPopup(marker, e, onDetail) {
  marker.unbindPopup();
  marker.bindPopup(() => popupHtml(e), { maxWidth: 280 });
  marker.off('popupopen');
  marker.on('popupopen', (ev) => {
    const btn = ev.popup.getElement().querySelector('[data-eval-detail]');
    if (btn) btn.addEventListener('click', () => onDetail(e.id));
  });
}

/** Builds the map/tile/legend/basemap-toggle scaffold exactly once per
 *  session (initEvaluaciones calls teardownMap() up front, so `map` is null
 *  at the start of a fresh session and only ever rebuilt there — see the
 *  module doc comment above). A no-op on every later call within the same
 *  session, which is what lets renderMap below skip straight to diffing
 *  markers instead of rebuilding this scaffold on every render. */
function buildMapOnce(containerId) {
  if (map) return;
  // F8: build every piece into LOCAL variables first, and only assign them
  // to the module-level refs together, at the very end, once everything
  // succeeded. Without this, a throw partway through construction (e.g.
  // L.map() succeeds but createBasemapToggle or L.layerGroup throws) could
  // leave `map` truthy while baseTile/pointsLayer/legendEl are still
  // null/undefined — every later call site that guards on `if (map)` (theme
  // change, renderMap, invalidate()) would then read the module as "built"
  // and crash on one of its still-missing siblings instead of failing
  // cleanly. Wrapped in try/catch that tears down whatever DID get created
  // and rethrows, so a failed build always leaves the module in the same
  // clean not-built state teardownMap() itself produces.
  try {
    const localMap = L.map(containerId, { zoomControl: true, minZoom: 10, maxZoom: 18 })
      .setView(CALI_CENTER, CALI_ZOOM);
    const localBaseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(localMap);
    const localBasemapToggle = createBasemapToggle(localMap, { getStreetLayer: () => localBaseTile });
    const localPointsLayer = L.layerGroup().addTo(localMap);

    let localLegendEl = null;
    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = () => {
      localLegendEl = L.DomUtil.create('div', 'map-legend');
      L.DomEvent.disableClickPropagation(localLegendEl);
      return localLegendEl;
    };
    legend.addTo(localMap); // invokes onAdd synchronously, so localLegendEl is set before the assignment below

    map = localMap;
    baseTile = localBaseTile;
    basemapToggle = localBasemapToggle;
    pointsLayer = localPointsLayer;
    legendEl = localLegendEl;
  } catch (err) {
    teardownMap();
    throw err;
  }

  // The tab is hidden until switchView() shows it, so Leaflet measures a
  // zero-height container on first mount. Only needed once, at build time —
  // later renders never resize the container out from under an already-
  // correctly-measured map (that's what the exposed `invalidate()` handles,
  // for when the TAB itself is hidden/reshown).
  setTimeout(() => { if (map) map.invalidateSize(); }, 80);
}

function renderMap(containerId, evaluaciones, onDetail) {
  buildMapOnce(containerId);
  const conCoords = evaluaciones.filter((e) => e.coords);

  const { toAdd, toRestyle, toRemove } = diffMarkerIds(markerById.keys(), conCoords);
  for (const id of toRemove) {
    const marker = markerById.get(id);
    if (marker) pointsLayer.removeLayer(marker);
    markerById.delete(id);
  }
  for (const e of toAdd) {
    const marker = L.circleMarker([e.coords.lat, e.coords.lng], {
      radius: 8,
      color: '#0B1D33',
      weight: 1,
      fillColor: COLOR_MODES[colorMode].colorOf(e),
      fillOpacity: 0.9,
    });
    bindMarkerPopup(marker, e, onDetail);
    marker.addTo(pointsLayer);
    markerById.set(e.id, marker);
  }
  for (const e of toRestyle) {
    const marker = markerById.get(e.id);
    if (!marker) continue; // defensive: diffMarkerIds only puts ids from prevSet here
    marker.setLatLng([e.coords.lat, e.coords.lng]);
    // F7: must reset the BASE style (radius/color/weight), not just
    // fillColor — a marker restyled here after being hover-highlighted
    // (setHighlight below enlarges/accents it) would otherwise stay
    // enlarged/accented forever, since this was the only place a kept
    // marker's style is touched again after the highlight.
    marker.setStyle({
      radius: 8, color: '#0B1D33', weight: 1, fillColor: COLOR_MODES[colorMode].colorOf(e),
    });
    bindMarkerPopup(marker, e, onDetail); // rebind: a poll refresh must never leave a stale record/detail-id bound
  }

  // Legend content depends on colorMode + the SIN_DATO entries, neither of
  // which is worth diffing — cheap fixed-size markup either way, and it has
  // to refresh even when the marker set itself didn't change (a colorMode
  // toggle alone doesn't touch toAdd/toRestyle/toRemove). legendEl is set by
  // buildMapOnce's legend.onAdd, which has already fired by the time this
  // line runs (L.control.addTo() invokes onAdd synchronously).
  legendEl.innerHTML = colorLegendHtml();

  // Frame the actual data instead of a hardcoded city view — a single
  // evaluation in one corner of Cali is otherwise invisible at zoom 12.
  // Unchanged from before: still recomputed and re-applied on EVERY render
  // (not just when the map is first built) — this already ran on every
  // renderFiltered() pre-refactor too (renderMap was rebuilt from scratch
  // each time), so keeping it here preserves the exact fit-vs-keep-pan
  // behaviour rather than changing it as a side effect of this perf pass.
  const inBox = conCoords.filter((e) =>
    e.coords.lat >= CALI_BBOX.latMin && e.coords.lat <= CALI_BBOX.latMax
    && e.coords.lng >= CALI_BBOX.lngMin && e.coords.lng <= CALI_BBOX.lngMax);
  lastFitBounds = inBox.length ? L.latLngBounds(inBox.map((e) => [e.coords.lat, e.coords.lng])) : null;
  if (lastFitBounds) {
    map.fitBounds(lastFitBounds, { padding: [40, 40], maxZoom: 16 });
  } else {
    map.setView(CALI_CENTER, CALI_ZOOM);
  }

  return conCoords.length;
}

// ---- Record list -------------------------------------------------------------

// The aside is a narrow column, so the row stacks instead of laying its parts
// out in a strip, and the whole row is the button — a separate "Ver detalle"
// control would eat a third of the width. The label stays visible so the
// affordance is not hidden behind a hover.
function listItemHtml(e, degraded) {
  const c = claseDe(e);
  const f = faseDe(e);
  const numFotos = (e.fotos || []).length;
  const fotos = numFotos ? `${numFotos} foto${numFotos === 1 ? '' : 's'}` : 'sin fotos';
  const quien = quienDe(e);
  // .ps-row-wrap + sibling PDF button — same recipe acciones-capa.js's
  // accionRowHtml uses (button-in-button is invalid HTML, so the PDF
  // control lives next to .eval-row, not nested inside it). Disabled while
  // serving the degraded backup copy — see isDegraded above.
  //
  // Two stacked pills in the grid's 3rd column: clasificación (ATC-20
  // placard colour, existing) on top, Fase I/II (inspector NP colour)
  // below — .eval-pill-group is new, .eval-pill itself is untouched so
  // Acciones' single-pill-less rows (its own accionRowHtml, its own
  // 2-column grid override) are unaffected.
  return `<li>
    <div class="ps-row-wrap">
      <button type="button" class="eval-row" data-eval-detail="${escapeHtml(e.id)}">
        <span class="eval-dot" style="background:${COLOR_MODES[colorMode].colorOf(e)}" aria-hidden="true"></span>
        <span class="eval-name">${escapeHtml(tituloDe(e))}</span>
        <span class="eval-pill-group">
          <span class="eval-pill" style="--eval-pill:${c.color}">${escapeHtml(c.label)}</span>
          <span class="eval-pill" style="--eval-pill:${f.color}">${escapeHtml(f.label)}</span>
        </span>
        <span class="eval-meta">${escapeHtml(e.codigo_edificacion)} · ${escapeHtml(quien)}</span>
        <span class="eval-meta">${escapeHtml(formatFecha(e.fecha))} · ${fotos}</span>
        <span class="eval-cta">Ver detalle &rsaquo;</span>
      </button>
      <button type="button" class="btn-icon accion-pdf-btn" data-eval-pdf="${escapeHtml(e.id)}" ${degraded ? 'disabled' : ''} title="${degraded ? DEGRADED_TITLE : 'Descargar informe PDF'}" aria-label="Descargar informe PDF">${PDF_ICON}</button>
    </div>
  </li>`;
}

// ---- Detail modal ------------------------------------------------------------

const siNo = (v) => (v ? 'Sí' : 'No');

// PDF icon, swapped for a spinner while generarInformeEvaluacion runs — same
// pattern acciones-capa.js's accionRowHtml uses (PDF_ICON/SPINNER_ICON are
// not exported there, so duplicated here rather than adding a cross-module
// export just for two SVG strings).
const PDF_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><polyline points="9 15 12 18 15 15"/></svg>';
const SPINNER_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9" stroke-opacity=".25"/><path d="M21 12a9 9 0 0 0-9-9"/></svg>';
// Shared by listItemHtml (module scope) and initEvaluaciones's own
// download/modal-PDF wiring — one string, one place to reword.
const DEGRADED_TITLE = 'No disponible: mostrando una copia de respaldo con datos incompletos.';

function detailHtml(e) {
  const c = claseDe(e);
  const group = (titulo, filas) => {
    const body = filas
      .filter(([, v]) => v !== '' && v != null)
      .map(([k, v]) => `<div class="detail-field"><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`)
      .join('');
    return body ? `<div class="detail-group"><h3>${escapeHtml(titulo)}</h3><dl class="detail-fields">${body}</dl></div>` : '';
  };

  const fotosList = e.fotos || [];
  const fotos = fotosList.length
    ? fotosList.map((url, i) => `<button type="button" class="detail-photo" data-foto-idx="${i}" aria-label="Ampliar foto ${i + 1}"><img src="${escapeHtml(url)}" alt="Foto ${i + 1} de la edificación" loading="lazy"></button>`).join('')
    : '<span class="detail-photos-empty">Este registro no tiene fotos.</span>';

  return `
    <div class="eval-detail-banner" style="--eval-pill:${c.color}">
      <span class="eval-pill" style="--eval-pill:${c.color}">${escapeHtml(c.label)}</span>
      <span class="eval-detail-code">${escapeHtml(e.codigo_edificacion)}</span>
    </div>
    <div class="detail-media">
      <div class="detail-minimap" id="eval-detail-map"></div>
      <div class="detail-photos" id="eval-detail-photos">${fotos}</div>
    </div>
    ${group('Edificación', [
      ['Nombre', e.descripcion.nombre || 'Sin dato'],
      ['Dirección', e.descripcion.direccion || 'Sin dato'],
      ['Área', e.area_nombre || e.area || 'Sin dato'],
      ['Municipio (DIVIPOLA)', e.municipio || 'Sin dato'],
      ['Fuente', e.fuente === 'atencionsismo' ? 'Atención Sismo' : 'Formulario'],
      ['Origen del sticker', e.origen === 'firebase' ? 'Importado de Firebase' : (e.origen === 'sistema' ? 'App Atención Sismo' : 'Sin dato')],
      ['Etiqueta', e.color_etiqueta || 'Sin dato'],
      ['Consecutivo', e.consecutivo],
    ])}
    ${group('Evaluación', [
      ['Clasificación', c.label],
      ['Alcance', e.alcance || 'Sin dato'],
      ['Restricciones', e.restricciones || 'Ninguna registrada'],
      ['Barricadas', siNo(e.acciones_posteriores.barricadas)],
      ['Evaluación detallada', siNo(e.acciones_posteriores.evaluacion_detallada)],
      ['Comentarios', e.comentarios || 'Sin comentarios'],
    ])}
    ${group('Inspector', [
      ['Nombre', e.inspector.nombre_completo || 'Sin dato'],
      ['Código de brigada', e.inspector.codigo || 'Sin dato'],
      ['Identificación', e.inspector.identificacion || 'Sin dato'],
      ['Entidad', e.inspector.entidad || 'Sin dato'],
      ['Fase', faseDe(e).label],
      ['NP', e.inspector.np || 'Sin dato'],
      ['Fecha de registro', formatFecha(e.fecha)],
    ])}
    ${e.inspector_fuente === 'roster'
      ? '<p class="detail-inspector-caveat">Dato del código de brigada, no verificado contra esta evaluación — puede pertenecer a otro inspector si el código fue reasignado.</p>'
      : ''}
    ${group('Ubicación', [
      ['Latitud', e.coords ? e.coords.lat.toFixed(6) : 'Sin coordenadas'],
      ['Longitud', e.coords ? e.coords.lng.toFixed(6) : 'Sin coordenadas'],
      ['Precisión', e.coords && e.coords.accuracy ? `±${Math.round(e.coords.accuracy)} m` : 'Sin dato'],
    ])}`;
}

// ---- Entry point -------------------------------------------------------------

// Auto-refresh cadence for the section while the tab is open. Module-level
// handle: initEvaluaciones runs on every tab open, and stacking intervals
// would multiply the polling.
const AUTO_REFRESH_MS = 5 * 60 * 1000;
let autoRefreshTimer = null;
// F4: handle to the CURRENT session's debounced renderFiltered (declared
// inside initEvaluaciones, below) — held at module scope so a FRESH session
// can cancel a still-pending debounce from a PREVIOUS session (stickers.js
// re-renders the whole tab shell on every open; a leftover 180ms timer from
// a search keystroke right before switching tabs must never fire into the
// new session's fresh DOM/state).
let debouncedRenderFilteredRef = null;

/** Renders the evaluaciones section into `section` (already in the DOM).
 *  `fetchEvaluaciones` returns the array; failures render inline so a broken
 *  evaluations read never takes the inspector roster down with it. */
export function initEvaluaciones(section, { fetchEvaluaciones }) {
  // F4: cancel any PREVIOUS session's still-pending debounced render before
  // anything else — see debouncedRenderFilteredRef's own doc comment above.
  if (debouncedRenderFilteredRef) debouncedRenderFilteredRef.cancel();
  // A fresh session: dispose of any PREVIOUS session's Leaflet instance up
  // front. stickers.js re-renders the whole tab shell on every open (a NEW
  // #eval-map container each time), so a map left over from an earlier
  // session would be bound to an already-detached DOM node. From here on
  // THIS session's map is built once, lazily, by the first renderMap call
  // (buildMapOnce) and kept alive across every renderFiltered() this
  // session sees — see the module doc comment above `let map = null`.
  teardownMap();
  const bannerEl = section.querySelector('#eval-degraded-banner');
  const kpis = section.querySelector('#eval-kpis');
  const barEl = section.querySelector('#eval-bar');
  const listEl = section.querySelector('#eval-list');
  const listMeta = section.querySelector('#eval-list-meta');
  const mapMeta = section.querySelector('#eval-map-meta');
  const modal = section.querySelector('#eval-modal');
  const modalBody = section.querySelector('#eval-modal-body');
  const modalTitle = section.querySelector('#eval-modal-title');
  const reloadBtn = section.querySelector('#eval-reload');
  const searchEl = section.querySelector('#eval-search');
  const chipsEl = section.querySelector('#eval-clase-chips');
  const faseChipsEl = section.querySelector('#eval-fase-chips');
  const faseFuenteNoteEl = section.querySelector('#eval-fase-fuente-note');
  const colorGroupEl = section.querySelector('[data-eval-color-group]');
  const comunaFilterRoot = section.querySelector('#eval-comuna-filter');
  const barrioFilterRoot = section.querySelector('#eval-barrio-filter');
  const resetFiltersBtn = section.querySelector('#eval-reset-filters');
  const downloadBtn = section.querySelector('#eval-download');
  const modalPdfBtn = section.querySelector('#eval-modal-pdf');
  let byId = new Map();
  // Full, geo-resolved dataset from the last successful fetch — filters below
  // read/write these without ever re-fetching or re-resolving geo.
  let allEvaluaciones = [];
  let comunaMap = new Map(); // comuna -> Set(barrio), rebuilt alongside allEvaluaciones
  // comuna/barrio are Sets (multiselect, 2026-09 conversion) — an empty Set
  // is "todas", same meaning the old '' string carried.
  let filters = defaultEvalFilters();
  // Multiselect handles, so a comuna toggle can .refresh() its own control
  // and re-mount the barrio one (whose OPTIONS list itself changes).
  let comunaHandle = null;
  let barrioHandle = null;
  // Which evaluación the modal is currently showing — the modal's own PDF
  // button (unlike the row ones) has no per-row index to read from.
  let modalEvaluacion = null;
  // Mirrors the last `degraded` flag load() saw (see there): a PDF/xlsx
  // generated from the redacted backup copy would carry a wrong Fase I/II
  // reading with nothing on the exported file to say so, so every export
  // path is blocked outright while this is true, not just banner-warned.
  let isDegraded = false;
  // Guards against an out-of-order response: switching Fuente triggers
  // reload() while a previous load() for the OLD source may still be
  // in-flight, and network timing gives no guarantee the old request
  // resolves first. Each load() call claims the next seq; a response whose
  // seq no longer matches the latest is a stale race loser and is dropped
  // without touching any UI state the newer call already owns.
  let loadSeq = 0;

  const closeModal = () => {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
  };
  modal.querySelectorAll('[data-eval-close]').forEach((el) => el.addEventListener('click', closeModal));

  function openDetail(id) {
    const e = byId.get(id);
    if (!e) return;
    modalEvaluacion = e;
    modalTitle.textContent = tituloDe(e);
    modalBody.innerHTML = detailHtml(e);
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    // buildMiniMap speaks the Panel's record shape (x = lng, y = lat).
    buildMiniMap(modalBody.querySelector('#eval-detail-map'),
      e.coords ? { x: e.coords.lng, y: e.coords.lat } : {});
    modalBody.querySelectorAll('[data-foto-idx]').forEach((btn) => {
      btn.addEventListener('click', () => openLightbox(e.fotos, Number(btn.dataset.fotoIdx)));
    });
  }

  section.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-eval-detail]');
    // Map popups live outside `section` (Leaflet reparents them), so those
    // buttons are wired on popupopen instead; this covers the list rows.
    if (btn && listEl.contains(btn)) openDetail(btn.dataset.evalDetail);
  });

  // Per-row PDF button — same disable/spinner/restore + error-toast contract
  // as acciones-capa.js's own [data-accion-pdf] handler.
  listEl.addEventListener('click', async (ev) => {
    const pdfBtn = ev.target.closest('[data-eval-pdf]');
    if (!pdfBtn) return;
    const e = byId.get(pdfBtn.dataset.evalPdf);
    if (!e) return;
    pdfBtn.disabled = true;
    pdfBtn.classList.add('is-loading');
    pdfBtn.innerHTML = SPINNER_ICON;
    try {
      await generarInformeEvaluacion(e);
    } catch {
      showToast('No se pudo generar el informe PDF.', 'error');
    } finally {
      // Not unconditionally false: if a poll flipped isDegraded to true
      // WHILE this generation was in flight, the button must land back on
      // disabled, not re-enable itself into a now-blocked state.
      pdfBtn.disabled = isDegraded;
      pdfBtn.classList.remove('is-loading');
      pdfBtn.innerHTML = PDF_ICON;
    }
  });

  // Same PDF action inside the detail modal (Acciones has one there too),
  // over whichever evaluación openDetail() last set.
  modalPdfBtn.addEventListener('click', async () => {
    if (!modalEvaluacion) return;
    modalPdfBtn.disabled = true;
    modalPdfBtn.classList.add('is-loading');
    modalPdfBtn.innerHTML = SPINNER_ICON;
    try {
      await generarInformeEvaluacion(modalEvaluacion);
    } catch {
      showToast('No se pudo generar el informe PDF.', 'error');
    } finally {
      // Same isDegraded-aware restore as the per-row handler above.
      modalPdfBtn.disabled = isDegraded;
      modalPdfBtn.classList.remove('is-loading');
      modalPdfBtn.innerHTML = PDF_ICON;
    }
  });

  // Pointing at a row lights up its point. This is what earns the side-by-side
  // layout: the list stops being a second copy of the data and becomes a way
  // to read the map. Delegated, so re-rendering the list keeps working.
  const setHighlight = (id, on) => {
    const marker = markerById.get(id);
    if (marker) marker.setStyle(on
      ? { radius: 12, color: COLORS.accent, weight: 3 }
      : { radius: 8, color: '#0B1D33', weight: 1 });
  };
  listEl.addEventListener('pointerover', (ev) => {
    const row = ev.target.closest('[data-eval-detail]');
    if (row) setHighlight(row.dataset.evalDetail, true);
  });
  listEl.addEventListener('pointerout', (ev) => {
    const row = ev.target.closest('[data-eval-detail]');
    if (row) setHighlight(row.dataset.evalDetail, false);
  });
  // Keyboard parity: tabbing through the list highlights too.
  listEl.addEventListener('focusin', (ev) => {
    const row = ev.target.closest('[data-eval-detail]');
    if (row) setHighlight(row.dataset.evalDetail, true);
  });
  listEl.addEventListener('focusout', (ev) => {
    const row = ev.target.closest('[data-eval-detail]');
    if (row) setHighlight(row.dataset.evalDetail, false);
  });

  // Fingerprint of the last rendered dataset. The silent auto-refresh skips
  // the whole re-render (which would reset the map's pan/zoom mid-use) when
  // the server returned exactly what is already on screen.
  let lastFingerprint = null;

  // Renders KPIs/bar/list/map from applyFilters(allEvaluaciones, filters).
  // Never fetches, never touches geo — every filter control below calls only
  // this, so changing a filter is a synchronous in-memory re-render.
  function renderFiltered() {
    // Every filter control (search, clase/fase chips, comuna/barrio toggles)
    // funnels through this one function — updating the reset button's
    // enabled/disabled + soft-orange state here (rather than duplicating the
    // check in each handler) means it can never drift out of sync with the
    // real filter state, including when the user clears the LAST active
    // filter by hand (e.g. deselecting the last comuna chip).
    const active = hasActiveEvalFilters(filters);
    resetFiltersBtn.disabled = !active;
    resetFiltersBtn.classList.toggle('is-filter-active', active);

    const filtered = applyFilters(allEvaluaciones, filters);
    chipsEl.innerHTML = claseChipsHtml(filters.clase);
    faseChipsEl.innerHTML = faseChipsHtml(filters.fase);
    const fuenteFaseNota = fuenteFaseNotaDe(allEvaluaciones);
    faseFuenteNoteEl.hidden = !fuenteFaseNota;
    faseFuenteNoteEl.textContent = fuenteFaseNota || '';
    kpis.innerHTML = kpisHtml(filtered);
    barEl.innerHTML = barHtml(filtered);

    if (!filtered.length) {
      listEl.innerHTML = allEvaluaciones.length
        ? '<li class="eval-empty">Ningún registro coincide con los filtros aplicados.</li>'
        : '<li class="eval-empty">Todavía no hay evaluaciones en esta fuente.</li>';
      listMeta.textContent = '';
    } else {
      listEl.innerHTML = filtered.map((e) => listItemHtml(e, isDegraded)).join('');
      listMeta.textContent = `${filtered.length} · más reciente primero`;
    }

    const conCoords = renderMap('eval-map', filtered, openDetail);
    const sinCoords = filtered.length - conCoords;
    mapMeta.textContent = sinCoords
      ? `${conCoords} en el mapa · ${sinCoords} sin coordenadas`
      : `${conCoords} en el mapa`;
  }

  // Drops any selected barrio that no longer belongs to ANY currently
  // selected comuna — called after every comuna toggle and after each
  // load() rebuilds comunaMap, so filters.barrio never points at a barrio
  // the active comuna selection (or the freshly loaded data) no longer
  // offers. Thin DOM-state wrapper over the exported pure
  // pruneInvalidBarrios(barrioSet, comunaSet, comunaMap) above.
  function syncBarrioFilter() {
    filters.barrio = pruneInvalidBarrios(filters.barrio, filters.comuna, comunaMap);
  }

  // Re-mounted (not just .refresh()'d) on every comuna change: its OPTIONS
  // list itself changes (union across selected comunas), and
  // renderMultiSelect's refresh() only re-renders the CURRENT options
  // against a changed selectedSet — it has no way to pick up a new options
  // array. renderMultiSelect has no disabled state of its own, so the
  // toggle button's `.disabled` is set directly here, same idea as the old
  // `selectEl.disabled = !comuna`.
  function mountBarrio() {
    barrioHandle = renderMultiSelect(barrioFilterRoot, {
      field: 'barrio',
      label: 'Barrio',
      options: barrioOptionsFrom(comunaMap, filters.comuna),
      selectedSet: filters.barrio,
      onToggle: (value) => {
        toggleSetValue(filters.barrio, value);
        barrioHandle.refresh();
        renderFiltered();
      },
    });
    const toggleBtn = barrioFilterRoot.querySelector('.filter-toggle');
    if (toggleBtn) toggleBtn.disabled = barrioDisabledFor(filters.comuna);
  }

  // Comuna's own options only change when comunaMap itself is rebuilt (a
  // fresh load()), so this is only re-mounted there — toggling a comuna
  // just mutates the Set and refreshes in place. Barrio depends on WHICH
  // comunas are selected, so every toggle here re-mounts it too.
  function mountComuna() {
    comunaHandle = renderMultiSelect(comunaFilterRoot, {
      field: 'comuna',
      label: 'Comuna',
      options: comunaOptionsFrom(comunaMap),
      selectedSet: filters.comuna,
      onToggle: (value) => {
        toggleSetValue(filters.comuna, value);
        syncBarrioFilter();
        comunaHandle.refresh();
        mountBarrio();
        renderFiltered();
      },
    });
  }

  async function load({ silent = false } = {}) {
    // F4: a non-silent load (manual "Actualizar", or the initial load()
    // below) is about to replace allEvaluaciones/filters state wholesale —
    // a stale pending debounced render from a search keystroke just before
    // this must never fire afterwards and re-render against data this call
    // is in the middle of superseding. A SILENT poll does NOT cancel it:
    // the user may be actively typing, and a silent poll must never disturb
    // that in-flight debounce.
    if (!silent) debouncedRenderFiltered.cancel();
    const seq = ++loadSeq;
    // Perf (2026-09-10): kick off the comunas/barrios geojson download NOW,
    // in parallel with the API round-trip below, instead of only starting
    // it once resolveGeoFor's first call discovers the need for it (after
    // fetchEvaluaciones already resolved) — same single-flight ensureGeo
    // cache either way, so this just overlaps two independent downloads
    // instead of serializing them. The `.catch(() => {})` lives ONLY on
    // this prefetch handle, never on the real resolution path: if the
    // geojson fetch fails, resolveGeoFor -> resolveBarrioComuna -> ensureGeo
    // still runs (and still throws, same as before this prefetch existed)
    // when a record's coords are actually resolved further down — this
    // call only silences the duplicate "unhandled rejection" that would
    // otherwise fire here for a promise nothing else is awaiting.
    prefetchGeo().catch(() => {});
    if (!silent) {
      kpis.innerHTML = '<p class="sticker-loading">Cargando evaluaciones…</p>';
      barEl.innerHTML = '';
      listEl.innerHTML = '';
      listMeta.textContent = '';
      mapMeta.textContent = '';
    }
    try {
      const { evaluaciones, degraded } = await fetchEvaluaciones();
      // A newer load() (e.g. switching Fuente again before this fetch
      // resolved) has already claimed loadSeq — drop this stale response
      // rather than let it overwrite the newer call's UI state below.
      if (seq !== loadSeq) return;
      // Toggled BEFORE the fingerprint short-circuit below, on purpose: a
      // silent poll can flip `degraded` (Blob-restore recovering, or a fresh
      // outage starting) even when the evaluaciones themselves are byte-for-
      // byte identical, and the banner (and the disabled export controls
      // below) must track that regardless of whether the rest of the render
      // below actually runs.
      isDegraded = degraded;
      bannerEl.hidden = !degraded;
      // A PDF/xlsx built from the redacted backup copy would carry a wrong
      // Fase I/II reading with nothing on the exported file to flag it (the
      // on-screen banner doesn't travel with a downloaded document) — so
      // every export path is blocked outright, not just banner-warned. The
      // per-row buttons pick this up next render via listItemHtml(e,
      // isDegraded); these two don't get re-rendered from scratch, so they're
      // set directly here.
      downloadBtn.disabled = degraded;
      downloadBtn.title = degraded ? DEGRADED_TITLE : '';
      modalPdfBtn.disabled = degraded;
      modalPdfBtn.title = degraded ? DEGRADED_TITLE : 'Descargar informe PDF';

      // Fingerprint from the RAW fetch, before geo resolution: an unchanged
      // silent poll must short-circuit here, before paying for a single
      // point-in-polygon lookup — and without touching the user's filters.
      // Covers every field that affects the render (see
      // evaluacionRenderKey's own doc comment for the 2026-09-10 fix this
      // closed) — measured at 2046 records (full atencionsismo dataset):
      // ~0.7-1.5 ms, well inside the "keep it cheap" budget.
      const fingerprint = fingerprintEvaluaciones(evaluaciones);
      if (silent && fingerprint === lastFingerprint) return;
      lastFingerprint = fingerprint;

      allEvaluaciones = await Promise.all(
        evaluaciones.map(async (e) => ({ ...e, ...(await resolveGeoFor(e.coords?.lat, e.coords?.lng)) })),
      );
      // Same stale-response guard, re-checked after the async geo resolution
      // above (its own await gives an even newer load() more time to win).
      if (seq !== loadSeq) return;
      byId = new Map(allEvaluaciones.map((e) => [e.id, e]));

      comunaMap = comunaBarrioMap(allEvaluaciones);
      // Same "keep filters in sync with what's actually offered" rule the
      // old single-select had (a stale value silently dropped after a
      // refresh) — generalized to Sets: any selected comuna/barrio no
      // longer present in the freshly loaded data is dropped, not just
      // reset wholesale to "todas".
      filters.comuna = pruneToValid(filters.comuna, comunaMap.keys());
      syncBarrioFilter();
      mountComuna();
      mountBarrio();

      renderFiltered();
    } catch (err) {
      // A failed silent poll keeps the last good render on screen; the next
      // tick (or the manual button) retries.
      if (silent) return;
      teardownMap();
      barEl.innerHTML = '';
      // A total load failure is its own error state (nothing to show at
      // all) — don't leave a stale "backup copy" banner contradicting it.
      isDegraded = false;
      bannerEl.hidden = true;
      // Same reasoning: nothing loaded, so nothing to export — the
      // degraded-copy block above must not survive into this error state
      // and leave the export controls stuck disabled.
      downloadBtn.disabled = false;
      downloadBtn.title = '';
      modalPdfBtn.disabled = false;
      modalPdfBtn.title = 'Descargar informe PDF';
      kpis.innerHTML = `<p class="sticker-error" role="alert">No se pudieron cargar las evaluaciones: ${escapeHtml(err.message)}</p>`;
    }
  }

  reloadBtn.addEventListener('click', () => load());

  // ---- filter wiring: each control only updates `filters` and re-renders
  // from the already-loaded, already-geo-resolved allEvaluaciones. ----------
  // Perf (2026-09-10): renderFiltered() is the expensive part (~270 ms on
  // 2046 records), not the filter-state update — so `filters.search` is
  // still updated SYNCHRONOUSLY on every keystroke (any other control, e.g.
  // a clase chip clicked mid-typing, must always see the latest typed text,
  // same as before this change), and only the re-render itself is debounced.
  //
  // Reset-race guard: "Reiniciar filtros" below sets `filters.search = ''`
  // and calls renderFiltered() SYNCHRONOUSLY (not through this debounced
  // fn), so a reset always renders immediately regardless of any pending
  // debounce. A debounced render still in flight when that happens is
  // harmless, not stale: by the time it fires, `filters` (read fresh via
  // the closure below, never a snapshot captured at keystroke time) already
  // reflects the post-reset state, so it just redundantly re-renders that
  // SAME already-correct state — it can never re-apply the old search text,
  // because that text was never stored anywhere the debounced fn reads from
  // except `filters` itself, which the reset already overwrote.
  const debouncedRenderFiltered = debounce(() => renderFiltered(), 180);
  // F4: publish this session's instance so the NEXT session's
  // initEvaluaciones (or a stale one still pending right now) can cancel it
  // — see debouncedRenderFilteredRef's own module-level doc comment.
  debouncedRenderFilteredRef = debouncedRenderFiltered;
  searchEl.addEventListener('input', () => {
    filters = { ...filters, search: searchEl.value };
    debouncedRenderFiltered();
  });

  // Delegated click on the chip group — same shape as planeacion.js's filter
  // chip wiring, one group ('clase') instead of two.
  chipsEl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-filter-group="clase"]');
    if (!btn) return;
    filters = { ...filters, clase: btn.dataset.filterValue };
    renderFiltered();
  });

  // Same delegated-click shape as the clase chip group above, one group
  // ('fase') instead of 'clase'.
  faseChipsEl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-filter-group="fase"]');
    if (!btn) return;
    filters = { ...filters, fase: btn.dataset.filterValue };
    renderFiltered();
  });

  // "Colorear por" — same wiring shape as acciones-capa.js's own
  // [data-accion-color-group] handler, over the two-entry COLOR_MODES
  // above instead of touching data or filters at all.
  colorGroupEl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-eval-color]');
    if (!btn) return;
    colorMode = btn.dataset.evalColor;
    colorGroupEl.querySelectorAll('[data-eval-color]').forEach((b) => {
      const active = b === btn;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-selected', String(active));
    });
    renderFiltered();
  });

  // comuna/barrio filter wiring lives in mountComuna()/mountBarrio() above
  // (their onToggle callbacks) — mounted once after the first load() and
  // re-mounted whenever comunaMap or the comuna selection itself changes.

  // "Reiniciar filtros": back to defaultEvalFilters() and re-render through
  // the exact same paths every individual control already uses — mountComuna/
  // mountBarrio (not a manual DOM sync) so any open dropdown panel is closed
  // and rebuilt against the now-empty Sets, then renderFiltered() for the
  // chips/KPIs/list/map. Never touches colorMode (display mode, out of scope).
  resetFiltersBtn.addEventListener('click', () => {
    // F4: cancel a still-pending debounced render explicitly — filters
    // being read fresh at fire time (see the old comment this replaces)
    // already made a stale re-render harmless in practice, but cancelling
    // it outright removes the redundant call entirely instead of relying on
    // that argument.
    debouncedRenderFiltered.cancel();
    filters = defaultEvalFilters();
    searchEl.value = '';
    mountComuna();
    mountBarrio();
    // Synchronous, not debouncedRenderFiltered — reset must render
    // immediately, never wait out the search debounce above.
    renderFiltered();
  });

  // Same xlsx header-block convention used elsewhere in the dashboard (title,
  // filtro aplicado, fecha de generación, registros, blank row) but built
  // from Stickers data and the currently active filters.
  downloadBtn.addEventListener('click', async () => {
    let XLSX;
    try { XLSX = await loadXlsx(); } catch { showToast('No se pudo cargar el generador de Excel.', 'error'); return; }
    const rows = applyFilters(allEvaluaciones, filters).map((e) => ({
      id: e.id,
      fuente: e.fuente || 'firestore',
      origen_sticker: e.origen || '',
      etiqueta_sticker: e.color_etiqueta || '',
      codigo_edificacion: e.codigo_edificacion,
      consecutivo: e.consecutivo,
      municipio: e.municipio,
      area: e.area,
      area_nombre: e.area_nombre,
      clasificacion: e.clasificacion,
      alcance: e.alcance,
      inspector_nombre_completo: e.inspector.nombre_completo,
      inspector_codigo: e.inspector.codigo,
      inspector_identificacion: e.inspector.identificacion,
      inspector_entidad: e.inspector.entidad,
      inspector_np: e.inspector.np,
      inspector_verificado: inspectorFuenteLabel(e),
      fase: faseDe(e).label,
      nombre: e.descripcion.nombre,
      direccion: e.descripcion.direccion,
      comuna: e._comuna || 'Sin dato',
      barrio: e._barrio || 'Sin dato',
      barricadas: siNo(e.acciones_posteriores.barricadas),
      evaluacion_detallada: siNo(e.acciones_posteriores.evaluacion_detallada),
      restricciones: e.restricciones,
      comentarios: e.comentarios,
      lat: e.coords ? e.coords.lat : '',
      lng: e.coords ? e.coords.lng : '',
      accuracy: e.coords ? e.coords.accuracy : '',
      fecha: e.fecha,
      num_fotos: (e.fotos || []).length,
    }));
    if (!rows.length) {
      showToast('No hay evaluaciones con los filtros aplicados.', 'error');
      return;
    }
    const { legible, slug } = downloadStamp();
    const ws = XLSX.utils.aoa_to_sheet([
      ['Evaluaciones ATC-20 — Stickers'],
      ['Filtro aplicado:', describeFilters(filters)],
      ['Fecha de generación:', legible],
      ['Registros:', rows.length],
      [],
    ]);
    XLSX.utils.sheet_add_json(ws, rows, { origin: 'A6' });
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'stickers');
    XLSX.writeFile(wb, `stickers_${slug}.xlsx`);
  });

  load();

  // Keep the tab fresh on its own while it stays open: silent poll every 5
  // minutes, skipped while the browser tab is hidden. Re-initialization
  // replaces the previous interval; a detached section stops its own timer.
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(() => {
    if (!section.isConnected) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; return; }
    if (document.visibilityState === 'hidden' || section.closest('[hidden]')) return;
    load({ silent: true }).catch(() => {});
  }, AUTO_REFRESH_MS);

  // The map is built while this section is hidden (display:none → 0 size), so
  // Leaflet renders broken tiles AND fits to a wrong zoom until it re-measures
  // once visible. stickers.js calls this each time the Evaluaciones segment is
  // opened: re-measure, then re-fit so the zoom matches the real container size.
  return {
    invalidate: () => {
      if (!map) return;
      map.invalidateSize();
      if (lastFitBounds) map.fitBounds(lastFitBounds, { padding: [40, 40], maxZoom: 16 });
    },
    // Full (non-silent) re-fetch, exposed so a caller (stickers.js switching
    // the Fuente selector) can force a fresh load instead of waiting for the
    // next auto-refresh tick. `load` is this closure's own internal loader
    // (async function load({ silent = false } = {}) {...}, line 800) — same
    // no-arg call the "Actualizar" button already uses, so silent defaults
    // to false here too.
    reload: () => load(),
  };
}
