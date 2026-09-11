// Vuelos UAS tab: the "relacion_vuelos_uas_sismo_cali" drone-flight layer from
// ArcGIS, rendered client-side — KPIs, one chart per variable, a Leaflet map
// with its flight list, links to each flight's Google Drive folder, and the
// official ArcGIS Dashboard embedded below.
//
// The layer is the Survey123 capture view (`service_..._form`): it is queried
// READ-ONLY here — never open anonymous editing on it (Create/Update are
// exposed publicly, an edit UI would let anyone alter field records).
import {
  COLORS, escapeHtml, basemapTileUrl, themeColor, showToast, createBasemapToggle,
  normalize, normalizeAddressText, debounce,
} from './utils.js';
import { mountDriveCarousel } from './drive-viewer.js';
import { resolveBarrioComuna } from './mapview.js';
import { renderMultiSelect } from './multiselect.js';

/* global L, Chart */

const CAPA_URL = 'https://services8.arcgis.com/ljfiJpg35HWgdtaC/arcgis/rest/services/service_b39faeeb8f6a4222b67ca2c500eee2b8_form/FeatureServer/0';
const DASHBOARD_URL = 'https://www.arcgis.com/apps/dashboards/fef40bd53d964e2496d60d5b8d903e13';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
// Sanity box for the fitBounds computation (F6), mirroring evaluaciones.js's
// own CALI_BBOX (~766-772): one bad/corrupt coordinate must not blow the
// zoom out to show the whole world. Markers still render on the map
// regardless of this box — only the fitBounds computation excludes
// out-of-bbox points from what it fits to.
const CALI_BBOX = { latMin: 3.30, latMax: 3.55, lngMin: -76.60, lngMax: -76.40 };
const inCaliBbox = (lat, lng) => (
  lat >= CALI_BBOX.latMin && lat <= CALI_BBOX.latMax && lng >= CALI_BBOX.lngMin && lng <= CALI_BBOX.lngMax
);

// Layer-specific codes -> Spanish labels (domain aliases of the Survey123
// form; utils.js's labelForCode doesn't know these and would drop accents).
const LABELS = {
  colapso_parcial: 'Colapso parcial',
  colapso_total: 'Colapso total',
  riesgo_caida: 'Riesgo de caída de elementos',
  inclinacion_desviacion: 'Inclinación o desviación importante',
  sin_afectacion: 'Sin afectación aparente',
  verificar: 'Verificar',
  verificar_colapso_total: 'Verificar colapso total',
  verificar_torre_pie: 'Verificar torre en pie — misma unidad',
  repetido: 'Repetido',
  demolicion_confirmada: 'Demolición confirmada',
  se_debe_confirmar: 'Se debe confirmar',
  otro: 'Otro',
  foto_panoramica: 'Foto panorámica',
  video_panoramico: 'Video panorámico',
  confinado: 'Confinado',
  sgred: 'SGRED',
  siata: 'SIATA',
  acre: 'ACRE',
  univalle: 'Univalle',
  securitas: 'Securitas',
  servigpoder: 'Servigpoder',
  voluntario: 'Voluntario',
};

const code = (v) => String(v ?? '').trim().toLowerCase();

function label(value) {
  const key = code(value);
  if (!key) return 'Sin dato';
  if (LABELS[key]) return LABELS[key];
  const s = key.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Selectable "color by" variables for the map, in the semaphore language the
// rest of the dashboard speaks (red = act, amber = pending, green = clear).
const COLOR_VARS = {
  estado_edificacion: {
    label: 'Estado',
    entries: [
      { value: 'colapso_total', color: COLORS.status.i3 },
      { value: 'colapso_parcial', color: COLORS.status.i2 },
      { value: 'riesgo_caida', color: COLORS.damage.medio },
      { value: 'inclinacion_desviacion', color: COLORS.status.r2 },
      { value: 'sin_afectacion', color: COLORS.status.h },
    ],
  },
  confirmacion: {
    label: 'Confirmación',
    entries: [
      { value: 'colapso_total', color: COLORS.status.i3 },
      { value: 'colapso_parcial', color: COLORS.status.i2 },
      { value: 'verificar_colapso_total', color: COLORS.damage.medio },
      { value: 'verificar', color: COLORS.status.r2 },
      { value: 'verificar_torre_pie', color: COLORS.status.r1 },
      { value: 'repetido', color: COLORS.categorical[0] },
      { value: 'otro', color: COLORS.categoricalOther },
    ],
  },
  definicion: {
    label: 'Definición',
    entries: [
      { value: 'demolicion_confirmada', color: COLORS.status.i2 },
      { value: 'se_debe_confirmar', color: COLORS.status.r2 },
      { value: 'otro', color: COLORS.categoricalOther },
    ],
  },
};

function colorFor(varName, value) {
  const entry = (COLOR_VARS[varName]?.entries || []).find((e) => e.value === code(value));
  return entry ? entry.color : COLORS.unknown;
}

// Chips need readable ink on top of the fill; only the darkest red needs white.
const inkFor = (color) => (color === COLORS.status.i3 ? '#ffffff' : '#0B1D33');

const chip = (varName, value) => {
  const c = colorFor(varName, value);
  return `<span class="badge" style="background:${c};color:${inkFor(c)}">${escapeHtml(label(value))}</span>`;
};

// dia_captura is an esriFieldTypeDateOnly — the REST API returns "YYYY-MM-DD"
// strings (epoch ms tolerated defensively).
function diaKey(v) {
  if (v == null || v === '') return null;
  if (typeof v === 'number') {
    const d = new Date(v);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  const m = /^\d{4}-\d{2}-\d{2}/.exec(String(v));
  return m ? m[0] : null;
}

function formatDia(v) {
  const key = diaKey(v);
  if (!key) return 'Sin fecha';
  return new Date(`${key}T12:00:00`).toLocaleDateString('es-CO', { dateStyle: 'medium' });
}

// Multi-select fields (productos, origen) arrive space- OR comma-separated.
const tokens = (v) => code(v).split(/[\s,]+/).filter(Boolean);

const driveLink = (v) => {
  const url = String(v ?? '').trim();
  return /^https?:\/\//i.test(url) ? url : null;
};

// Folder id out of a drive.google.com/drive/folders/<id> link; feeds the
// embeddedfolderview iframe (photo grid without leaving the app, no API key).
const driveFolderId = (v) => (/\/folders\/([\w-]+)/.exec(String(v ?? '')) || [])[1] || null;

/* ------------------------------------------------------------------ */
/* Grouping by edificación                                             */
/* ------------------------------------------------------------------ */

// Flights over the same edificación share a normalized `lugar` (the layer's
// address text); rows without lugar stay solo instead of pooling together.
const grupoKey = (row) => code(row.lugar).replace(/\s+/g, ' ') || `__vuelo_${row.objectid}`;

// Worst-first severity, so a building with any colapso_total flight counts
// (and paints) as colapso_total no matter what later flights observed.
const ESTADO_ORDEN = ['colapso_total', 'colapso_parcial', 'riesgo_caida', 'inclinacion_desviacion', 'sin_afectacion'];

function worstEstado(members) {
  for (const e of ESTADO_ORDEN) if (members.some((r) => code(r.estado_edificacion) === e)) return e;
  return members.find((r) => code(r.estado_edificacion))?.estado_edificacion ?? '';
}

/** Arrays of ROW OBJECTS (not indices), one array per edificación, in `rows`
 *  order (rows come sorted date-desc, so each group sits where its most
 *  recent flight does). Each row carries its own `_idx` (stable position in
 *  the full, unfiltered `allRows` — see renderVuelos) so a filtered subset
 *  can still be grouped without losing the marker/list-row identity. */
function buildGrupos(rows) {
  const map = new Map();
  rows.forEach((row) => {
    const key = grupoKey(row);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(row);
  });
  return [...map.values()];
}

/* ------------------------------------------------------------------ */
/* Fetch                                                               */
/* ------------------------------------------------------------------ */

// Refetched only on explicit action (Actualizar / Reintentar) — ~130 rows
// that move slowly. `fetchPromise` de-dupes concurrent loads; `loadErrored`
// keeps a failed load from silently refetching (same recipe as acciones-capa).
let featuresCache = null;
let fetchPromise = null;
let loadErrored = false;

async function fetchCapa() {
  // Page through resultOffset: ArcGIS silently truncates at maxRecordCount
  // and flags it with exceededTransferLimit instead of erroring.
  const all = [];
  let more = true;
  while (more) {
    const res = await fetch(`${CAPA_URL}/query?where=1=1&outFields=*&returnGeometry=true&f=json&resultOffset=${all.length}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (json.error) throw new Error(json.error.message || 'Error de la capa');
    const feats = json.features || [];
    all.push(...feats.map((f) => ({
      ...f.attributes,
      _lat: Number(f.geometry?.y ?? f.attributes.y),
      _lng: Number(f.geometry?.x ?? f.attributes.x),
    })));
    more = Boolean(json.exceededTransferLimit) && feats.length > 0;
  }
  return all;
}

/* ------------------------------------------------------------------ */
/* Comuna/barrio resolution — same recipe as evaluaciones.js's own        */
/* geoCache/resolveGeoFor (this layer has no native comuna/barrio field,  */
/* so it is resolved client-side per point against the same cached       */
/* comunas/barrios boundaries mapview.js already lazily fetches once).   */
/* ------------------------------------------------------------------ */

// Memoized by rounded-coordinate key: the cache holds the in-flight PROMISE
// (not just its eventual result), so two overlapping lookups for the exact
// same point share one resolution instead of racing separate ones. A failed
// lookup (rejecting resolver, e.g. the comunas/barrios geojson fetch itself
// failing) degrades to { _comuna: null, _barrio: null } for that coordinate
// rather than throwing — same "degrade toward keeping data usable"
// philosophy utils.js's isInsideCali/resolveZonaInteres already document,
// applied here to a resolution that runs over the network instead of a
// pure point-in-polygon check.
const geoCache = new Map();

// F3: one geojson/resolution failure used to leave a permanently-cached
// REJECTED-then-degraded promise for that coordinate — every later render
// (or a retry once the network/geojson fetch recovers) would keep reading
// the stale null/null forever instead of ever trying again. Also drives the
// one-shot error toast below: a load with many failing rows must toast once,
// not once per row — reset at the START of every resolveRowsGeo (one load
// call), never per-row.
let geoErrorToastShown = false;

/** Comuna/barrio for one (lat, lng), memoized — but ONLY a SUCCESSFUL
 *  resolution is memoized (F3): on rejection the cache entry is evicted
 *  before returning the null/null fallback, so a later call for the exact
 *  same coordinate re-invokes `resolveFn` instead of being served a stale
 *  failure forever. `resolveFn` defaults to mapview.resolveBarrioComuna and
 *  is only ever overridden by tests (a real caller never passes it) — kept
 *  as a plain parameter rather than a separate seam so the function stays a
 *  single, directly testable unit. Rows without finite coordinates never
 *  call the resolver at all: there is nothing to look up. Exported so a
 *  self-check can assert the memoization/invalid-coordinate/failure-
 *  degradation/recovery-on-retry contracts without the DOM. */
export function resolveGeoFor(lat, lng, resolveFn = resolveBarrioComuna) {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return Promise.resolve({ _comuna: null, _barrio: null });
  }
  const key = `${Number(lat).toFixed(5)},${Number(lng).toFixed(5)}`;
  if (!geoCache.has(key)) {
    geoCache.set(key, resolveFn(lat, lng)
      .then(({ comuna, barrio }) => ({ _comuna: comuna, _barrio: barrio }))
      .catch(() => {
        geoCache.delete(key); // never memoize a failure — a later call must retry, not stay degraded forever
        if (!geoErrorToastShown) {
          geoErrorToastShown = true;
          // Guarded: this module's pure functions run under plain Node in
          // the self-check (no `document`), same convention the rest of the
          // file already uses for DOM-touching side effects.
          if (typeof document !== 'undefined') {
            showToast('No se pudo resolver comuna/barrio de los vuelos; se muestran como "Sin dato".', 'error');
          }
        }
        return { _comuna: null, _barrio: null };
      }));
  }
  return geoCache.get(key);
}

/** Resolves _comuna/_barrio for every row in one batch. Each row's lookup is
 *  independent (resolveGeoFor already isolates a failure to null/null for
 *  that coordinate), so one bad/failing lookup never blanks the rest of the
 *  batch. Resets the one-shot error-toast flag at the START of the batch (F3)
 *  so many failing rows within this one load only toast once, while a LATER
 *  load call (Actualizar) can toast again if it also hits failures. Exported
 *  so a self-check can assert the batch/isolation contract without the DOM. */
export async function resolveRowsGeo(rows, resolveFn = resolveBarrioComuna) {
  geoErrorToastShown = false;
  return Promise.all(rows.map(async (row) => ({
    ...row,
    ...(await resolveGeoFor(row._lat, row._lng, resolveFn)),
  })));
}

/* ------------------------------------------------------------------ */
/* Free-text search + comuna/barrio multiselect filters — same shape      */
/* evaluaciones.js's applyFilters/comuna/barrio helpers use, ported and   */
/* adapted to this file's flat row shape (row._comuna/row._barrio instead */
/* of a nested `descripcion`).                                             */
/* ------------------------------------------------------------------ */

/** Whether `row` matches a free-text `query`, across every variable shown in
 *  the row card/popup: lugar, formatted dia_captura, the estado/confirmacion/
 *  definicion labels, origen tokens + origen_otro, productos tokens,
 *  observacion, objectid_survey, and the resolved _comuna/_barrio. Case/
 *  accent-insensitive (normalize()), same recipe as evaluaciones.js's
 *  applyFilters search branch. Empty or whitespace-only query matches
 *  everything (no filtering). `lugar` is ALSO matched through
 *  normalizeAddressText() on both the haystack and the query (in addition
 *  to the plain-normalize() haystack above), so "Cra 44a #10-25" and
 *  "carrera 44 10-25" land on the same row — normalizeAddressText's own
 *  contract (utils.js) is to build BOTH the index and the query this way.
 *  This never changes what is DISPLAYED (row.lugar stays as-is), only what
 *  matches. Exported: pure, so a self-check can exercise it without the
 *  DOM. */
export function matchesSearch(row, query) {
  if (!query || !String(query).trim()) return true;
  const q = normalize(query);
  const haystack = normalize([
    row.lugar,
    formatDia(row.dia_captura),
    label(row.estado_edificacion),
    label(row.confirmacion),
    label(row.definicion),
    tokens(row.origen).map(label).join(' '),
    row.origen_otro,
    tokens(row.productos).map(label).join(' '),
    row.observacion,
    row.objectid_survey,
    row._comuna,
    row._barrio,
  ].filter((v) => v !== null && v !== undefined && v !== '').join(' '));
  if (haystack.includes(q)) return true;
  const qAddr = normalizeAddressText(query);
  if (!qAddr) return false;
  return normalizeAddressText(row.lugar).includes(qAddr);
}

/** Filtered view of `rows`: comuna/barrio (resolved client-side, see
 *  resolveGeoFor above) and the free-text search. comuna/barrio are Sets
 *  (multiselect) — an empty or missing Set means "todas". Exported so a
 *  self-check can exercise it without the DOM. */
export function applyUasFilters(rows, filters) {
  return rows.filter((row) => {
    if (filters.comuna && filters.comuna.size && !filters.comuna.has(row._comuna)) return false;
    if (filters.barrio && filters.barrio.size && !filters.barrio.has(row._barrio)) return false;
    if (filters.search && !matchesSearch(row, filters.search)) return false;
    return true;
  });
}

/** comuna -> Set(barrio) across rows with a resolved comuna. Rows without
 *  coords (or that fell outside every polygon) don't offer a comuna/barrio
 *  to filter by. Exported: pure, so a self-check can exercise it without the
 *  DOM. */
export function comunaBarrioMap(rows) {
  const map = new Map();
  for (const row of rows) {
    if (!row._comuna) continue;
    if (!map.has(row._comuna)) map.set(row._comuna, new Set());
    if (row._barrio) map.get(row._comuna).add(row._barrio);
  }
  return map;
}

/** Sorted {value,label} options for the comuna multiselect. Exported: pure,
 *  so a self-check can assert the sort/empty-map cases without the DOM. */
export function comunaOptionsFrom(comunaMap) {
  return [...comunaMap.keys()].sort().map((c) => ({ value: c, label: c }));
}

/** Barrio options = union of barrios across EVERY currently selected comuna.
 *  Zero selected comunas -> zero options, read by the caller as "disable the
 *  barrio control" (see barrioDisabledFor). Exported: pure, so a self-check
 *  can assert the union/empty rules without the DOM. */
export function barrioOptionsFrom(comunaMap, comunaSet) {
  if (!comunaSet || !comunaSet.size) return [];
  const barrios = new Set();
  for (const comuna of comunaSet) {
    for (const b of comunaMap.get(comuna) || []) barrios.add(b);
  }
  return [...barrios].sort().map((b) => ({ value: b, label: b }));
}

/** New Set holding only the members of `set` still present in `validValues`
 *  (an array or Set). Exported: pure, so a self-check can assert what
 *  survives/what's dropped without the DOM. */
export function pruneToValid(set, validValues) {
  const valid = validValues instanceof Set ? validValues : new Set(validValues);
  return new Set([...set].filter((v) => valid.has(v)));
}

/** Drops any member of `barrioSet` that no longer belongs to ANY comuna in
 *  `comunaSet`, per `comunaMap`. Composes barrioOptionsFrom + pruneToValid —
 *  the one rule behind both a comuna toggle narrowing the selection and a
 *  reload() rebuilding comunaMap from fresh data. Exported: pure, so a
 *  self-check can assert every case without the DOM. */
export function pruneInvalidBarrios(barrioSet, comunaSet, comunaMap) {
  return pruneToValid(barrioSet, barrioOptionsFrom(comunaMap, comunaSet).map((o) => o.value));
}

/** has/delete/add dance for a multiselect's onToggle callback. Mutates `set`
 *  in place and returns nothing — callers read the same Set reference back.
 *  Exported: pure/DOM-free, so a self-check can assert the mutate-in-place
 *  contract directly. */
export function toggleSetValue(set, value) {
  if (set.has(value)) set.delete(value); else set.add(value);
}

/** Guarded toggle for the barrio Set specifically: `validOptions` is the
 *  barrio option list CURRENTLY on offer (barrioOptionsFrom's own output,
 *  reshaped to bare values, or an equivalent Set). Toggling a value that is
 *  not among those valid options is never silently accepted — it is pruned
 *  from `barrioSet` (a no-op if it was never there) instead of being toggled
 *  on. This guards against an invalid state transition (a stale/unknown
 *  barrio value reaching the filter) even though renderMultiSelect's own
 *  rendered checkboxes never offer one in practice. Exported: pure, so a
 *  self-check can assert the no-op/prune behaviour without the DOM. */
export function toggleBarrioValue(barrioSet, value, validOptions) {
  const valid = validOptions instanceof Set ? validOptions : new Set(validOptions);
  if (!valid.has(value)) {
    barrioSet.delete(value);
    return;
  }
  toggleSetValue(barrioSet, value);
}

/** Whether the Barrio toggle button should be disabled: true only when no
 *  comuna is selected. Exported: pure, so a self-check can assert both
 *  states without the DOM. */
export function barrioDisabledFor(comunaSet) {
  return !comunaSet || comunaSet.size === 0;
}

/** Default (empty) filters shape — a fresh Set instance for comuna/barrio on
 *  every call, never a shared reference, so a reset/reload can't hand back a
 *  Set some other closure still holds and mutates later. Exported so
 *  data-uas-reload and the initial `filters` declaration share exactly one
 *  definition, and so a self-check can assert the fresh-Set contract without
 *  the DOM. */
export function defaultUasFilters() {
  return { search: '', comuna: new Set(), barrio: new Set() };
}

/** Whether any filter is currently narrowing the view — drives "Reiniciar
 *  filtros"' enabled/disabled + soft-orange state, same contract as
 *  evaluaciones.js's hasActiveEvalFilters. Tolerant of a partial/malformed
 *  filters object so it can never throw mid-render. Exported so a self-check
 *  can cover the no-filters/one-filter/cleared-back-to-none transitions
 *  without the DOM. */
export function hasActiveUasFilters(filters) {
  if (!filters) return false;
  // F9: a whitespace-only search string must not read as active — it never
  // actually narrows anything (matchesSearch treats it as "no filtering"
  // too), so the reset button must agree or it sits enabled with nothing
  // real for it to reset.
  return Boolean(
    (filters.search && filters.search.trim())
    || (filters.comuna && filters.comuna.size)
    || (filters.barrio && filters.barrio.size),
  );
}

/** Counts of the CURRENTLY FILTERED rows grouped by resolved _comuna,
 *  alphabetical with "Sin dato" last — backs the "Vuelos por comuna" chart.
 *  Exported: pure, so a self-check can assert the sort/Sin dato-last/empty
 *  cases without the DOM. */
export function vuelosPorComunaData(rows) {
  const counts = new Map();
  for (const row of rows) {
    const key = row._comuna || '__sin_dato__';
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const known = [...counts.keys()].filter((k) => k !== '__sin_dato__').sort();
  const keys = counts.has('__sin_dato__') ? [...known, '__sin_dato__'] : known;
  return {
    labels: keys.map((k) => (k === '__sin_dato__' ? 'Sin dato' : k)),
    data: keys.map((k) => counts.get(k)),
  };
}

/** Stable key for a SET of `_idx` values: deduped, sorted numerically, then
 *  joined — so two idx collections with the same MEMBERS produce the exact
 *  same key regardless of iteration order or duplicate entries. Tolerant of
 *  a null/undefined iterable (reads as the empty set). Exported: pure, so a
 *  self-check can assert it directly. */
export function visibleSetKey(idxIterable) {
  return [...new Set(idxIterable || [])].sort((a, b) => a - b).join(',');
}

/** Whether the SET of visible `_idx` values actually changed between two
 *  renders (F6) — compares by visibleSetKey, so order and duplicate
 *  membership never count as a change, only real membership does. Backs
 *  updateMapVisibility's fitBounds dedupe: a filter change that doesn't
 *  actually change WHICH markers are visible (or a re-render with the exact
 *  same visible set, e.g. the initial-load double-fit) skips the redundant
 *  recompute entirely. Exported: pure, so a self-check can assert every
 *  transition without Leaflet or the DOM. */
export function visibleSetChanged(prevIdx, nextIdx) {
  return visibleSetKey(prevIdx) !== visibleSetKey(nextIdx);
}

/* ------------------------------------------------------------------ */
/* Shell                                                               */
/* ------------------------------------------------------------------ */

let colorVar = 'estado_edificacion';
let map = null;
let baseTile = null;
let basemapToggle = null;
const charts = new Map();
let rootRef = null;
// Set when a themechange re-render was skipped because the tab was hidden —
// Leaflet mis-fits a display:none 0×0 container. Consumed on the next
// initVuelosUasTab call with the tab visible.
let vuelosDirty = false;

// Full, geo-resolved, unfiltered row set from the last renderVuelos() —
// each row carries its own `_idx` (stable position here) so a FILTERED
// subset can still address its marker/list-row identity. comunaMap is
// rebuilt alongside it. `filters` persists across a plain re-render (theme
// change, tab reopen) and is only reset back to defaultUasFilters() by the
// "Actualizar" reload handler — see requirement 8 in the task/spec.
let allRows = [];
let comunaMap = new Map();
let filters = defaultUasFilters();
// Multiselect handles, so a comuna toggle can refresh its own control and
// re-mount the barrio one (whose OPTIONS list itself changes) — same recipe
// as evaluaciones.js's comunaHandle/barrioHandle.
let comunaHandle = null;
let barrioHandle = null;

function shellHtml() {
  return `
    <header class="sticker-page-head">
      <h2 class="sticker-h1">Vuelos UAS</h2>
      <p class="sticker-lead">Relación de vuelos de dron sobre edificaciones afectadas, con sus productos en Google Drive.</p>
    </header>
    <div data-uas-section></div>`;
}

function destroyCharts() {
  for (const c of charts.values()) c.destroy();
  charts.clear();
}

function teardownMap() {
  if (map) { map.remove(); map = null; }
  baseTile = null;
  basemapToggle = null;
}

// Basemap + chart colors are baked at construction; rebuild on theme change,
// but never into a hidden container (same guard as acciones-capa.js).
if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => {
    if (map && baseTile) {
      map.removeLayer(baseTile);
      baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
      baseTile.bringToBack();
      if (basemapToggle) basemapToggle.notifyStreetLayerRecreated();
    }
    if (!rootRef || !featuresCache) return;
    const sec = rootRef.querySelector('[data-uas-section]');
    if (!sec) return;
    if (document.visibilityState === 'hidden' || sec.closest('[hidden]')) {
      vuelosDirty = true;
      return;
    }
    renderVuelos(sec);
  });
}

/** Entry point, called by main.js each time the tab opens. The layer is
 *  independent of the Panel store, so there is no records input. */
export function initVuelosUasTab(root) {
  rootRef = root;
  if (!root.querySelector('[data-uas-section]')) root.innerHTML = shellHtml();
  const sectionEl = root.querySelector('[data-uas-section]');

  if (featuresCache && !vuelosDirty) {
    // Same data as the last render — just re-measure the map (the tab may
    // have been hidden when it was built).
    if (map) setTimeout(() => map.invalidateSize(), 60);
    return;
  }
  if (sectionEl.closest('[hidden]')) {
    vuelosDirty = true;
    return;
  }
  vuelosDirty = false;
  if (featuresCache) renderVuelos(sectionEl);
  else if (!loadErrored && !fetchPromise) loadVuelos(sectionEl);
}

async function loadVuelos(sectionEl) {
  sectionEl.innerHTML = '<p class="sticker-loading">Cargando vuelos UAS…</p>';
  try {
    // Comuna/barrio resolved once per fetch, before caching — every render
    // after this reads row._comuna/row._barrio straight off featuresCache,
    // never re-resolving. A failed geojson fetch degrades to null/null per
    // row (resolveRowsGeo/resolveGeoFor above) rather than failing the load.
    fetchPromise = fetchPromise || fetchCapa().then((rows) => resolveRowsGeo(rows));
    featuresCache = await fetchPromise;
    loadErrored = false;
    renderVuelos(sectionEl);
  } catch (err) {
    loadErrored = true;
    sectionEl.innerHTML = `
      <section class="card accion-error-card">
        <p class="sticker-error" role="alert">No se pudo cargar la capa de vuelos UAS: ${escapeHtml(err.message)}</p>
        <button type="button" class="btn-primary" data-uas-retry>Reintentar</button>
      </section>`;
    sectionEl.querySelector('[data-uas-retry]').addEventListener('click', () => {
      loadErrored = false;
      loadVuelos(sectionEl);
    });
  } finally {
    fetchPromise = null;
  }
}

/* ------------------------------------------------------------------ */
/* Render                                                              */
/* ------------------------------------------------------------------ */

function kpisHtml(rows, grupos) {
  const total = rows.length;
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);
  // Colapsos count edificaciones (grouped by dirección), not flights — the
  // same building flown 3 times is one collapse, at its worst observed state.
  // `grupos` is already an array of ROW arrays (buildGrupos), so worstEstado
  // takes each group directly — no index->row mapping needed.
  const porEstado = (estado) => grupos.filter((members) => worstEstado(members) === estado).length;
  const colTotal = porEstado('colapso_total');
  const colParcial = porEstado('colapso_parcial');
  const riesgo = rows.filter((r) => code(r.estado_edificacion) === 'riesgo_caida').length;
  const conDrive = rows.filter((r) => driveLink(r.enlace_drive)).length;
  const dias = new Set(rows.map((r) => diaKey(r.dia_captura)).filter(Boolean)).size;
  return `
    <div class="kpi-tile is-neutral">
      <span class="kpi-label">Vuelos registrados</span>
      <span class="kpi-value">${total}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">sobre ${grupos.length} edificaciones en ${dias} días</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.status.i3}">
      <span class="kpi-label">Colapso total / parcial</span>
      <span class="kpi-value">${colTotal} / ${colParcial}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">edificaciones, agrupadas por dirección</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.damage.medio}">
      <span class="kpi-label">Riesgo de caída</span>
      <span class="kpi-value">${riesgo}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">${pct(riesgo)}% de los vuelos</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.accent}">
      <span class="kpi-label">Con carpeta Drive</span>
      <span class="kpi-value">${conDrive} / ${total}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">productos accesibles en línea</span></div>
    </div>`;
}

function colorSegmentedHtml() {
  return Object.entries(COLOR_VARS).map(([key, def]) => `
    <button type="button" class="segmented-btn${key === colorVar ? ' is-active' : ''}"
      data-uas-color="${key}" role="tab" aria-selected="${key === colorVar}">${escapeHtml(def.label)}</button>`).join('');
}

const DRIVE_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
const FOTOS_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>';

/** Row card, same recipe as Acciones' accionRowHtml: dot + place first line,
 *  meta lines below, and the Drive link as a SIBLING of .eval-row inside
 *  .ps-row-wrap (link-in-button is invalid HTML). `row._idx` (assigned once
 *  in renderVuelos, over the FULL unfiltered row set) addresses this row's
 *  marker directly, so a filtered/regrouped render still points at the
 *  right marker without renumbering. */
function vueloRowHtml(row) {
  const color = colorFor('estado_edificacion', row.estado_edificacion);
  const drive = driveLink(row.enlace_drive);
  const folder = driveFolderId(drive);
  return `
    <li>
      <div class="ps-row-wrap">
        <button type="button" class="eval-row" data-uas-row="${row._idx}">
          <span class="eval-dot" style="background:${color}" aria-hidden="true" title="${escapeHtml(label(row.estado_edificacion))}"></span>
          <span class="eval-name">${escapeHtml(row.lugar || `Vuelo ${row.objectid}`)}</span>
          <span class="eval-meta">${escapeHtml(formatDia(row.dia_captura))} · ${escapeHtml(label(row.estado_edificacion))}</span>
          <span class="eval-meta">${escapeHtml(tokens(row.origen).map(label).join(' · ') || 'Sin origen')}</span>
          <span class="eval-meta">Comuna: ${escapeHtml(row._comuna || 'Sin dato')} · Barrio: ${escapeHtml(row._barrio || 'Sin dato')}</span>
        </button>
        ${folder ? `<button type="button" class="btn-icon" data-uas-fotos="${folder}" data-uas-lugar="${escapeHtml(row.lugar || `Vuelo ${row.objectid}`)}" title="Ver fotos del vuelo" aria-label="Ver fotos del vuelo">${FOTOS_ICON}</button>` : ''}
        ${drive ? `<a class="btn-icon" href="${escapeHtml(drive)}" target="_blank" rel="noopener" title="Abrir carpeta de Google Drive" aria-label="Abrir carpeta de Google Drive">${DRIVE_ICON}</a>` : ''}
      </div>
    </li>`;
}

/** One <li> per edificación: a single flight reuses vueloRowHtml as-is; a
 *  multi-flight building renders a stacked <details> card whose summary
 *  carries the worst estado and expands to the individual flight rows.
 *  `members` is an array of ROW OBJECTS (buildGrupos' own output), each
 *  already carrying its `_idx`. */
function grupoHtml(members) {
  if (members.length === 1) return vueloRowHtml(members[0]);
  const estado = worstEstado(members);
  const color = colorFor('estado_edificacion', estado);
  const diasGrupo = [...new Set(members.map((r) => diaKey(r.dia_captura)).filter(Boolean))].sort();
  const rango = diasGrupo.length > 1
    ? `${formatDia(diasGrupo[0])} — ${formatDia(diasGrupo[diasGrupo.length - 1])}`
    : formatDia(diasGrupo[0]);
  return `
    <li>
      <details class="uas-grupo">
        <summary class="eval-row uas-grupo-head">
          <span class="eval-dot" style="background:${color}" aria-hidden="true" title="${escapeHtml(label(estado))}"></span>
          <span class="eval-name">${escapeHtml(members[0].lugar)}</span>
          <span class="uas-grupo-count" title="${members.length} vuelos sobre esta edificación">×${members.length}</span>
          <span class="eval-meta">${escapeHtml(rango)} · ${escapeHtml(label(estado))}</span>
          <span class="eval-meta">${members.length} vuelos — clic para explorar</span>
        </summary>
        <ul class="eval-list uas-grupo-list">${members.map((row) => vueloRowHtml(row)).join('')}</ul>
      </details>
    </li>`;
}

function popupHtml(row) {
  const drive = driveLink(row.enlace_drive);
  const folder = driveFolderId(drive);
  const productos = tokens(row.productos).map(label).join(', ');
  const origen = tokens(row.origen).map(label).join(', ');
  const line = (lbl, html) => (html ? `<div class="uas-popup-line"><strong>${lbl}:</strong> ${html}</div>` : '');
  return `
    <div class="uas-popup">
      <div class="uas-popup-title">${escapeHtml(row.lugar || `Vuelo ${row.objectid}`)}</div>
      <div class="uas-popup-line">${escapeHtml(formatDia(row.dia_captura))}</div>
      ${line('Estado', chip('estado_edificacion', row.estado_edificacion))}
      ${line('Comuna', escapeHtml(row._comuna || 'Sin dato'))}
      ${line('Barrio', escapeHtml(row._barrio || 'Sin dato'))}
      ${row.confirmacion ? line('Confirmación', chip('confirmacion', row.confirmacion)) : ''}
      ${row.definicion ? line('Definición', chip('definicion', row.definicion)) : ''}
      ${line('Origen', escapeHtml([origen, row.origen_otro].filter(Boolean).join(' — ')) || '')}
      ${line('Productos', escapeHtml(productos))}
      ${row.observacion ? line('Observación', escapeHtml(row.observacion)) : ''}
      ${row.objectid_survey ? line('Código survey', escapeHtml(String(row.objectid_survey))) : ''}
      ${folder ? `<button type="button" class="uas-popup-drive uas-popup-fotos" data-uas-fotos="${folder}" data-uas-lugar="${escapeHtml(row.lugar || `Vuelo ${row.objectid}`)}">${FOTOS_ICON}<span>Ver fotos</span></button>` : ''}
      ${drive ? `<a class="uas-popup-drive" href="${escapeHtml(drive)}" target="_blank" rel="noopener">${DRIVE_ICON}<span>Abrir carpeta de Drive</span></a>` : ''}
    </div>`;
}

/** The section's static shell: search bar, comuna/barrio filter containers,
 *  chart tiles, map + list workspace, ArcGIS embed, fotos modal. Built once
 *  per renderVuelos() call (load/reload/themechange) — a filter change never
 *  touches this again (see renderFilteredVuelos), so an open comuna/barrio
 *  dropdown or a mid-typed search value is never yanked out from under the
 *  user. KPI row and list start empty; renderFilteredVuelos fills them. */
function sectionShellHtml() {
  return `
    <div class="section-bar">
      <h3 class="section-bar-title">Capa «relacion_vuelos_uas_sismo_cali»</h3>
      <button type="button" class="sticker-action" data-uas-reload>Actualizar</button>
      <button type="button" class="btn-clear" data-uas-reset-filters disabled>Reiniciar filtros</button>
    </div>
    <div class="kpi-row" data-uas-kpis></div>

    <div class="eval-filters">
      <div class="asignacion-search">
        <input type="search" data-uas-search class="sticker-search-input"
          placeholder="Buscar…" aria-label="Buscar vuelos">
      </div>
      <div class="card-toolbar asignacion-filters">
        <div class="filter-block" data-uas-comuna-filter></div>
        <div class="filter-block" data-uas-barrio-filter></div>
      </div>
    </div>

    <div class="stats-grid">
      <div class="chart-tile"><h3 class="chart-tile-title">Estado de la edificación</h3><canvas id="uas-chart-estado"></canvas></div>
      <div class="chart-tile"><h3 class="chart-tile-title">Definición de demolición</h3><canvas id="uas-chart-definicion"></canvas></div>
      <div class="chart-tile"><h3 class="chart-tile-title">Productos capturados</h3><canvas id="uas-chart-productos"></canvas></div>
      <div class="chart-tile chart-tile-wide"><h3 class="chart-tile-title">Confirmación en campo</h3><canvas id="uas-chart-confirmacion"></canvas></div>
      <div class="chart-tile chart-tile-wide"><h3 class="chart-tile-title">Origen del vuelo</h3><canvas id="uas-chart-origen"></canvas></div>
      <div class="chart-tile chart-tile-wide"><h3 class="chart-tile-title">Vuelos por comuna</h3><canvas id="uas-chart-comuna"></canvas></div>
      <div class="chart-tile chart-tile-wide"><h3 class="chart-tile-title">Vuelos por día de captura (acumulado)</h3><canvas id="uas-chart-dia"></canvas></div>
    </div>

    <section class="card eval-workspace-card" aria-label="Mapa y relación de vuelos">
      <div class="card-toolbar">
        <span class="eval-toolbar-title">Puntos sobrevolados</span>
        <div class="segmented" role="tablist" aria-label="Colorear por" data-uas-color-group>${colorSegmentedHtml()}</div>
      </div>
      <div class="eval-workspace">
        <div class="eval-map" id="uas-capa-map"></div>
        <div class="eval-aside">
          <div class="eval-aside-head">
            <h4>Relación de vuelos</h4>
            <span class="eval-toolbar-meta" data-uas-list-meta></span>
          </div>
          <ul class="eval-list" data-uas-list></ul>
        </div>
      </div>
    </section>

    <section class="card uas-embed-card" aria-label="Dashboard ArcGIS de vuelos UAS">
      <div class="card-toolbar">
        <span class="eval-toolbar-title">Dashboard ArcGIS</span>
        <a class="sticker-action" href="${DASHBOARD_URL}" target="_blank" rel="noopener">Abrir en ArcGIS</a>
      </div>
      <iframe class="uas-embed-frame" src="${DASHBOARD_URL}" title="Dashboard ArcGIS de vuelos UAS" loading="lazy" allowfullscreen></iframe>
    </section>

    <div class="modal" id="uas-fotos-modal" aria-hidden="true" role="dialog" aria-label="Fotos del vuelo">
      <div class="modal-backdrop"></div>
      <div class="modal-panel uas-fotos-panel">
        <div class="modal-header">
          <h2 data-uas-fotos-title>Fotos del vuelo</h2>
          <div class="uas-fotos-actions">
            <a class="sticker-action" data-uas-fotos-drive href="#" target="_blank" rel="noopener">Abrir en Drive</a>
            <button type="button" class="btn-icon" data-uas-fotos-close aria-label="Cerrar"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg></button>
          </div>
        </div>
        <div class="modal-body uas-fotos-body" data-uas-fotos-body></div>
      </div>
    </div>`;
}

/** Re-mounted (not just re-populated) whenever comunaMap itself is rebuilt
 *  (a load/reload) — so this is only called from renderVuelos. Toggling a
 *  comuna just mutates the Set and refreshes in place; barrio depends on
 *  WHICH comunas are selected, so every toggle here re-mounts IT (mountBarrio
 *  below), same split evaluaciones.js's mountComuna/mountBarrio use. */
function mountComuna(sectionEl) {
  const root = sectionEl.querySelector('[data-uas-comuna-filter]');
  comunaHandle = renderMultiSelect(root, {
    field: 'comuna',
    label: 'Comuna',
    options: comunaOptionsFrom(comunaMap),
    selectedSet: filters.comuna,
    onToggle: (value) => {
      toggleSetValue(filters.comuna, value);
      filters.barrio = pruneInvalidBarrios(filters.barrio, filters.comuna, comunaMap);
      comunaHandle.refresh();
      mountBarrio(sectionEl);
      renderFilteredVuelos(sectionEl);
    },
  });
}

/** Barrio's own OPTIONS list changes on every comuna toggle (union across
 *  selected comunas), so this is re-mounted there too — never the whole
 *  filter row/search box, only this one `.filter-block`. renderMultiSelect
 *  has no disabled state of its own, so the toggle button's `.disabled` is
 *  set directly, same idea evaluaciones.js's mountBarrio uses. Guarded
 *  against toggling a value that fell out of the current options
 *  (toggleBarrioValue), even though the rendered checkboxes never actually
 *  offer one. */
function mountBarrio(sectionEl) {
  const root = sectionEl.querySelector('[data-uas-barrio-filter]');
  const options = barrioOptionsFrom(comunaMap, filters.comuna);
  barrioHandle = renderMultiSelect(root, {
    field: 'barrio',
    label: 'Barrio',
    options,
    selectedSet: filters.barrio,
    onToggle: (value) => {
      toggleBarrioValue(filters.barrio, value, options.map((o) => o.value));
      barrioHandle.refresh();
      renderFilteredVuelos(sectionEl);
    },
  });
  const toggleBtn = root.querySelector('.filter-toggle');
  if (toggleBtn) toggleBtn.disabled = barrioDisabledFor(filters.comuna);
}

/** Mounts the static shell ONCE per load/reload/theme-change: search input,
 *  comuna/barrio dropdown containers, chart canvases, and the Leaflet map
 *  (built from the FULL unfiltered `allRows`, every marker mounted once —
 *  see renderCapaMap). A filter change afterwards only calls
 *  renderFilteredVuelos(), never this function again. */
function renderVuelos(sectionEl) {
  destroyCharts();
  teardownMap();

  allRows = [...featuresCache]
    .sort((a, b) => String(diaKey(b.dia_captura) || '').localeCompare(String(diaKey(a.dia_captura) || '')))
    .map((row, i) => ({ ...row, _idx: i }));
  comunaMap = comunaBarrioMap(allRows);
  // Same "keep filters in sync with what's actually offered" rule
  // evaluaciones.js's load() applies: a comuna/barrio no longer present in
  // the freshly (re)loaded data is dropped, not silently kept as a stale
  // filter that can never match anything again.
  filters.comuna = pruneToValid(filters.comuna, comunaMap.keys());
  filters.barrio = pruneInvalidBarrios(filters.barrio, filters.comuna, comunaMap);

  sectionEl.innerHTML = sectionShellHtml();

  // Search input — mounted once here, never rebuilt by a filter change.
  // Declared up here (F10, pure reordering) so it's available to the reload/
  // reset handlers below without relying on their callbacks running after
  // this line has executed (they always do — this is just for readability).
  const searchInput = sectionEl.querySelector('[data-uas-search]');
  // F6: renderFilteredVuelos is the expensive part on 2046+ rows, not the
  // filter-state update — filters.search is updated SYNCHRONOUSLY on every
  // keystroke below (no perceived input lag, and any other control reading
  // filters.search always sees the latest typed text), only the re-render
  // itself is debounced by 180ms.
  const debouncedRenderFilteredVuelos = debounce(() => renderFilteredVuelos(sectionEl), 180);

  sectionEl.querySelector('[data-uas-reload]').addEventListener('click', () => {
    // A stale pending debounced render (from a search keystroke right
    // before clicking Actualizar) must never stomp the freshly (re)loaded
    // data once it lands — same rationale as F4's debouncedRenderFiltered
    // cancellation in evaluaciones.js.
    debouncedRenderFilteredVuelos.cancel();
    featuresCache = null;
    loadErrored = false;
    filters = defaultUasFilters();
    // F3: a reload must retry geo resolution from scratch, not keep serving
    // whatever this session's cache (successes AND any not-yet-evicted
    // failures) already holds.
    geoCache.clear();
    loadVuelos(sectionEl);
  });

  // "Reiniciar filtros": back to defaultUasFilters() and re-render through
  // the exact same paths every individual control already uses — mountComuna/
  // mountBarrio (not a manual DOM sync) so any open dropdown panel is closed
  // and rebuilt against the now-empty Sets, then renderFilteredVuelos() for
  // KPIs/list/map/charts. Never touches colorVar (display mode, out of scope).
  sectionEl.querySelector('[data-uas-reset-filters]').addEventListener('click', () => {
    // Same stale-render guard as the reload handler above: a pending
    // debounced search render must never re-apply old search text after the
    // reset already cleared it.
    debouncedRenderFilteredVuelos.cancel();
    filters = defaultUasFilters();
    searchInput.value = '';
    mountComuna(sectionEl);
    mountBarrio(sectionEl);
    renderFilteredVuelos(sectionEl);
  });

  sectionEl.querySelector('[data-uas-color-group]').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-uas-color]');
    if (!btn) return;
    colorVar = btn.dataset.uasColor;
    sectionEl.querySelectorAll('[data-uas-color]').forEach((b) => {
      const active = b === btn;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-selected', String(active));
    });
    paintMarkers();
  });

  // Row click -> fly to the marker and open its popup. The Drive link is a
  // sibling <a>, so it navigates on its own without touching the map.
  // `[data-uas-list]` itself is only ever innerHTML-replaced (renderFiltered
  // Vuelos), never re-created, so this delegated listener keeps working.
  sectionEl.querySelector('[data-uas-list]').addEventListener('click', (e) => {
    const rowBtn = e.target.closest('[data-uas-row]');
    if (!rowBtn) return;
    const entry = markers[Number(rowBtn.dataset.uasRow)];
    if (!entry) { showToast('Este vuelo no tiene coordenadas.', 'error'); return; }
    map.flyTo(entry.marker.getLatLng(), Math.max(map.getZoom(), 16));
    entry.marker.openPopup();
  });

  searchInput.value = filters.search;
  searchInput.addEventListener('input', () => {
    filters = { ...filters, search: searchInput.value };
    debouncedRenderFilteredVuelos();
  });

  // "Ver fotos" -> in-app carousel (or the Drive folder grid fallback) in a
  // modal. Delegated with .onclick (not addEventListener) so re-renders
  // replace instead of stack.
  const fotosModal = sectionEl.querySelector('#uas-fotos-modal');
  const closeFotos = () => {
    fotosModal.classList.remove('is-open');
    fotosModal.setAttribute('aria-hidden', 'true');
    fotosGen++;
    carouselHandle?.destroy();
    carouselHandle = null;
    fotosModal.querySelector('[data-uas-fotos-body]').innerHTML = '';
  };
  fotosModal.querySelector('[data-uas-fotos-close]').onclick = closeFotos;
  fotosModal.querySelector('.modal-backdrop').onclick = closeFotos;
  sectionEl.onclick = (e) => {
    const btn = e.target.closest('[data-uas-fotos]');
    if (btn) openFotos(btn.dataset.uasFotos, btn.dataset.uasLugar);
  };

  // Comuna/barrio dropdowns: mounted once here (load/reload/theme-change).
  // A comuna toggle re-mounts ONLY these two `.filter-block`s (see
  // mountComuna/mountBarrio above), never the search input or anything else
  // in the shell — so an open panel is never yanked shut mid-interaction.
  mountComuna(sectionEl);
  mountBarrio(sectionEl);

  // Map built ONCE from the FULL, unfiltered, geo-resolved row set — every
  // marker mounted once. A filter change never calls renderCapaMap again
  // (no teardownMap/re-init of Leaflet, no re-fetch of tiles); it only
  // shows/hides the already-built markers — see updateMapVisibility.
  renderCapaMap(allRows);

  renderFilteredVuelos(sectionEl);
}

/** Recomputed from applyUasFilters(allRows, filters) on every filter change
 *  (search input, comuna/barrio toggle) — the ONLY thing a filter change
 *  calls. Never touches the map instance, the search input, or the
 *  comuna/barrio dropdown containers themselves: only this function's own
 *  targets (KPI row innerHTML, list innerHTML, chart canvases via upsert(),
 *  and marker visibility) are replaced. */
function renderFilteredVuelos(sectionEl) {
  const filtered = applyUasFilters(allRows, filters);
  const grupos = buildGrupos(filtered);
  const conDrive = filtered.filter((r) => driveLink(r.enlace_drive)).length;

  // Every filter control (search, comuna/barrio toggles, and the reset
  // button itself) funnels through this one function — updating the reset
  // button's enabled/disabled + soft-orange state here (rather than
  // duplicating the check in each handler) means it can never drift out of
  // sync, e.g. after deselecting the last comuna chip by hand.
  const resetBtn = sectionEl.querySelector('[data-uas-reset-filters]');
  if (resetBtn) {
    const active = hasActiveUasFilters(filters);
    resetBtn.disabled = !active;
    resetBtn.classList.toggle('is-filter-active', active);
  }

  const kpisEl = sectionEl.querySelector('[data-uas-kpis]');
  if (kpisEl) kpisEl.innerHTML = kpisHtml(filtered, grupos);

  const listMetaEl = sectionEl.querySelector('[data-uas-list-meta]');
  if (listMetaEl) listMetaEl.textContent = `${filtered.length} vuelos · ${grupos.length} edificaciones · ${conDrive} con Drive`;

  const listEl = sectionEl.querySelector('[data-uas-list]');
  if (listEl) {
    if (!filtered.length) {
      listEl.innerHTML = allRows.length
        ? '<li class="eval-empty">Ningún vuelo coincide con los filtros aplicados.</li>'
        : '<li class="eval-empty">Todavía no hay vuelos registrados.</li>';
    } else {
      listEl.innerHTML = grupos.map((members) => grupoHtml(members)).join('');
    }
  }

  updateMapVisibility(filtered);
  renderCharts(filtered);
}

// Visible marker SET (by `_idx`) as of the last updateMapVisibility/
// renderCapaMap call (F6) — lets a filter change that doesn't actually
// change WHICH markers are visible (or the initial renderCapaMap fit
// immediately followed by this same function, the old double-fit-on-load
// bug) skip the redundant fitBounds recompute. Seeded by renderCapaMap.
let lastVisibleKey = null;

/** Shows/hides the already-built markers (see renderCapaMap) to match the
 *  currently filtered rows, then re-fits bounds to whichever are visible —
 *  never re-creates the map or re-adds tiles. `entry.row._idx` is the same
 *  stable id vueloRowHtml/[data-uas-row] address, so this stays correct
 *  across filter changes and regrouping.
 *
 *  F6: the fitBounds recompute is skipped entirely when the visible marker
 *  SET (compared via visibleSetKey, order/duplicate-insensitive — see
 *  visibleSetChanged's own doc comment) is the same as last time — a
 *  comuna/barrio toggle that doesn't change which rows are visible, or two
 *  renders back-to-back on the same data, no longer re-fit for zero visible
 *  change. Also applies the CALI_BBOX sanity
 *  filter to what fitBounds computes FROM: markers still show/hide exactly
 *  as before regardless of the box, only the bounds computation excludes a
 *  bad/corrupt out-of-bbox coordinate so it can't blow the zoom out to show
 *  the whole world. */
function updateMapVisibility(filtered) {
  if (!map) return;
  const visibleIdx = new Set(filtered.map((r) => r._idx));
  const visibleMarkerIdx = [];
  const inBboxMarkers = [];
  for (const entry of markers) {
    if (!entry) continue;
    if (visibleIdx.has(entry.row._idx)) {
      if (!map.hasLayer(entry.marker)) entry.marker.addTo(map);
      visibleMarkerIdx.push(entry.row._idx);
      if (inCaliBbox(entry.row._lat, entry.row._lng)) inBboxMarkers.push(entry.marker);
    } else if (map.hasLayer(entry.marker)) {
      map.removeLayer(entry.marker);
    }
  }
  const nextKey = visibleSetKey(visibleMarkerIdx);
  if (nextKey === lastVisibleKey) return; // same visible SET as last time -> skip the redundant fitBounds recompute
  lastVisibleKey = nextKey;
  // No in-bbox visible marker (every visible point corrupt/far away, or
  // nothing visible at all) — leave the current pan/zoom rather than
  // fitting to garbage coordinates or the whole world.
  if (inBboxMarkers.length) {
    map.fitBounds(L.latLngBounds(inBboxMarkers.map((m) => m.getLatLng())), { padding: [40, 40], maxZoom: 16 });
  }
}

// Destroy handle for the currently-mounted carousel (its keydown listener
// and click delegate) — torn down on close and before mounting the next one.
// `fotosGen` guards the async mount against the same stale-response race
// puntos_solicitados.js's runGuardedBuscar documents: bumped on every
// open/close, a mount only takes effect if no newer open/close happened
// while it was awaiting the Drive API (otherwise its handle is discarded
// instead of clobbering whatever opened after it).
let carouselHandle = null;
let fotosGen = 0;

function openFotos(folderId, lugar) {
  const modal = document.getElementById('uas-fotos-modal');
  if (!modal || !folderId) return;
  modal.querySelector('[data-uas-fotos-title]').textContent = lugar || 'Fotos del vuelo';
  modal.querySelector('[data-uas-fotos-drive]').href = `https://drive.google.com/drive/folders/${folderId}`;
  modal.classList.add('is-open');
  modal.setAttribute('aria-hidden', 'false');
  carouselHandle?.destroy();
  carouselHandle = null;
  const gen = ++fotosGen;
  mountDriveCarousel(modal.querySelector('[data-uas-fotos-body]'), folderId).then((handle) => {
    if (gen === fotosGen) carouselHandle = handle;
    else handle.destroy();
  });
}

/* ------------------------------------------------------------------ */
/* Map                                                                 */
/* ------------------------------------------------------------------ */

// Sparse array indexed by row._idx (stable across filter changes) so list
// rows address their marker directly.
let markers = [];

/** Builds the map and every marker ONCE, from the FULL unfiltered `rows`
 *  (allRows) — called only from renderVuelos (load/reload/theme-change),
 *  never on a filter change. updateMapVisibility() is what shows/hides
 *  markers afterwards without touching the map instance or re-adding tiles. */
function renderCapaMap(rows) {
  map = L.map('uas-capa-map', { zoomControl: true, minZoom: 10, maxZoom: 18 }).setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
  basemapToggle = createBasemapToggle(map, { getStreetLayer: () => baseTile });

  // Leaflet popups stop click propagation, so the section-level delegation
  // never sees popup buttons — bind directly each time a popup opens.
  map.on('popupopen', (e) => {
    const btn = e.popup.getElement()?.querySelector('[data-uas-fotos]');
    if (btn) btn.onclick = () => openFotos(btn.dataset.uasFotos, btn.dataset.uasLugar);
  });

  markers = [];
  rows.forEach((row) => {
    if (!Number.isFinite(row._lat) || !Number.isFinite(row._lng)) return;
    const marker = L.circleMarker([row._lat, row._lng], {
      radius: 8, color: '#0B1D33', weight: 1, fillOpacity: 0.9,
      fillColor: colorFor(colorVar, row[colorVar]),
    });
    marker.bindTooltip(escapeHtml(row.lugar || `Vuelo ${row.objectid}`));
    marker.bindPopup(popupHtml(row), { maxWidth: 300 });
    marker.addTo(map);
    markers[row._idx] = { marker, row };
  });

  const legend = L.control({ position: 'bottomright' });
  legend.onAdd = () => {
    const el = L.DomUtil.create('div', 'map-legend');
    L.DomEvent.disableClickPropagation(el);
    el.dataset.uasLegend = '';
    el.innerHTML = legendHtml();
    return el;
  };
  legend.addTo(map);

  const placed = markers.filter(Boolean);
  // CALI_BBOX sanity filter (F6), same as updateMapVisibility's own: a bad/
  // corrupt coordinate must not blow this initial fit out to the whole
  // world. Falls back to every placed marker only when NONE of them fall
  // inside the box (nothing better to fit to); otherwise the hardcoded
  // Cali view from setView() above stays as the last resort.
  const inBbox = placed.filter(({ row }) => inCaliBbox(row._lat, row._lng));
  const fitTo = inBbox.length ? inBbox : placed;
  if (fitTo.length) {
    map.fitBounds(L.latLngBounds(fitTo.map(({ marker }) => marker.getLatLng())), { padding: [40, 40], maxZoom: 16 });
  }
  // Seed lastVisibleKey to this full placed set: renderVuelos always calls
  // renderFilteredVuelos -> updateMapVisibility right after this with the
  // SAME set (no filters active on a fresh load/reload), so that call's own
  // fitBounds is skipped as redundant instead of double-fitting on load.
  lastVisibleKey = visibleSetKey(placed.map(({ row }) => row._idx));
  // The tab may be hidden while this builds; re-measure once painted.
  setTimeout(() => { if (map) map.invalidateSize(); }, 80);
}

function legendHtml() {
  return `
    <div class="legend-title">${escapeHtml(COLOR_VARS[colorVar].label)}</div>
    ${COLOR_VARS[colorVar].entries.map((e) => `
      <div class="legend-row">
        <span class="legend-swatch legend-circle" style="background:${e.color}"></span>
        <span>${escapeHtml(label(e.value))}</span>
      </div>`).join('')}
    <div class="legend-row">
      <span class="legend-swatch legend-circle" style="background:${COLORS.unknown}"></span>
      <span>Sin dato</span>
    </div>`;
}

function paintMarkers() {
  for (const entry of markers) {
    if (!entry) continue;
    entry.marker.setStyle({ fillColor: colorFor(colorVar, entry.row[colorVar]) });
  }
  const legendEl = document.querySelector('[data-uas-legend]');
  if (legendEl) legendEl.innerHTML = legendHtml();
}

/* ------------------------------------------------------------------ */
/* Charts                                                              */
/* ------------------------------------------------------------------ */

function upsert(id, config) {
  const canvas = document.getElementById(id);
  if (!canvas) return;
  charts.get(id)?.destroy();
  charts.set(id, new Chart(canvas, config));
}

function chartOpts(overrides = {}) {
  const textMuted = themeColor('--text-muted', '#7c8ca3');
  const textSecondary = themeColor('--text-secondary', '#b9c4d4');
  const border = themeColor('--border', 'rgba(255,255,255,0.10)');
  return {
    maintainAspectRatio: false,
    responsive: true,
    animation: { duration: 200 },
    plugins: {
      legend: { display: false, position: 'bottom', labels: { color: textSecondary, usePointStyle: true, boxWidth: 8, boxHeight: 8, font: { size: 11 } } },
      ...overrides.plugins,
    },
    scales: overrides.scales === null ? undefined : {
      x: { grid: { color: border }, ticks: { color: textMuted, font: { size: 11 } } },
      y: { beginAtZero: true, grid: { color: border }, ticks: { color: textMuted, font: { size: 11 }, precision: 0 } },
    },
  };
}

/** Counts of every value of `varName` (including missing as "Sin dato"),
 *  colored via COLOR_VARS so charts and map speak the same palette. */
function categoryData(rows, varName) {
  const counts = new Map();
  for (const r of rows) {
    const key = code(r[varName]) || '__missing__';
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  // Known codes first (in their semaphore order), then surprises, then Sin dato.
  const known = COLOR_VARS[varName].entries.map((e) => e.value).filter((v) => counts.has(v));
  const extra = [...counts.keys()].filter((k) => k !== '__missing__' && !known.includes(k));
  const keys = [...known, ...extra, ...(counts.has('__missing__') ? ['__missing__'] : [])];
  return {
    labels: keys.map((k) => (k === '__missing__' ? 'Sin dato' : label(k))),
    data: keys.map((k) => counts.get(k)),
    colors: keys.map((k) => (k === '__missing__' ? COLORS.unknown : colorFor(varName, k))),
  };
}

function doughnut(id, varName, rows) {
  const { labels, data, colors } = categoryData(rows, varName);
  upsert(id, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data, backgroundColor: colors, borderColor: themeColor('--surface', '#12294a'), borderWidth: 2 }],
    },
    options: { ...chartOpts({ scales: null, plugins: { legend: { display: true, position: 'bottom', labels: { color: themeColor('--text-secondary', '#b9c4d4'), usePointStyle: true, boxWidth: 8, boxHeight: 8, font: { size: 11 } } } } }), cutout: '55%' },
  });
}

/** Bar of individual tokens of a multi-select field (productos / origen). */
function tokenBar(id, rows, varName, palette) {
  const counts = new Map();
  for (const r of rows) {
    for (const t of tokens(r[varName])) counts.set(t, (counts.get(t) || 0) + 1);
  }
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  upsert(id, {
    type: 'bar',
    data: {
      labels: entries.map(([t]) => label(t)),
      datasets: [{ data: entries.map(([, n]) => n), backgroundColor: entries.map((_, i) => palette[i % palette.length]) }],
    },
    options: chartOpts(),
  });
}

function renderCharts(rows) {
  doughnut('uas-chart-estado', 'estado_edificacion', rows);
  doughnut('uas-chart-definicion', 'definicion', rows);

  const conf = categoryData(rows, 'confirmacion');
  upsert('uas-chart-confirmacion', {
    type: 'bar',
    data: { labels: conf.labels, datasets: [{ data: conf.data, backgroundColor: conf.colors }] },
    options: chartOpts(),
  });

  tokenBar('uas-chart-productos', rows, 'productos', COLORS.categorical);
  tokenBar('uas-chart-origen', rows, 'origen', COLORS.categoricalWide);

  // Vuelos por comuna: counts of the CURRENTLY FILTERED rows (`rows` here
  // is always applyUasFilters(allRows, filters)'s own output — see
  // renderFilteredVuelos), grouped by resolved _comuna. "Sin dato" gets the
  // neutral unknown color, same convention categoryData/tokenBar already use.
  const comunaChart = vuelosPorComunaData(rows);
  upsert('uas-chart-comuna', {
    type: 'bar',
    data: {
      labels: comunaChart.labels,
      datasets: [{
        data: comunaChart.data,
        backgroundColor: comunaChart.labels.map((lbl, i) => (
          lbl === 'Sin dato' ? COLORS.unknown : COLORS.categorical[i % COLORS.categorical.length]
        )),
      }],
    },
    options: chartOpts(),
  });

  const dayCounts = new Map();
  for (const r of rows) {
    const key = diaKey(r.dia_captura);
    if (key) dayCounts.set(key, (dayCounts.get(key) || 0) + 1);
  }
  const days = [...dayCounts.keys()].sort();
  // Cumulative running total, not daily counts — the campaign's overall pace
  // matters more here than any single day's volume (same as Acciones).
  let runningTotal = 0;
  const cumulative = days.map((d) => (runningTotal += dayCounts.get(d)));
  upsert('uas-chart-dia', {
    type: 'line',
    data: {
      labels: days.map((d) => new Date(`${d}T12:00:00`).toLocaleDateString('es-CO', { day: '2-digit', month: 'short' })),
      datasets: [{
        data: cumulative,
        borderColor: themeColor('--accent', '#FFC400'),
        backgroundColor: themeColor('--accent-dim', 'rgba(255,196,0,0.16)'),
        pointBackgroundColor: themeColor('--accent', '#FFC400'),
        pointRadius: 3,
        tension: 0.25,
        fill: true,
      }],
    },
    options: chartOpts(),
  });
}
