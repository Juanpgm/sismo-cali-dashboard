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
  COLORS, escapeHtml, basemapTileUrl, normalize, loadXlsx, downloadStamp, showToast, faseInspector,
} from './utils.js';
import { buildMiniMap, resolveBarrioComuna } from './mapview.js';
import { openLightbox } from './table.js';
import { store } from './data.js';
import { coverageGaugeHtml } from './coverage-gauge.js';
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
// Atención Sismo stickers of origin "sistema" carry no inspector at all, so
// their Fase is unknowable — surfaced as its own state instead of the
// Firestore default (Fase I), which would be a lie for that source.
export const FASE_SIN_DATO = { key: 'SIN_DATO', label: 'sin dato', color: '#9AA5B1' };
const FASE_BY_KEY = new Map(FASES.map((f) => [f.key, f]));

/** Fase I/II of one evaluation's inspector, derived from inspector.np. For
 *  the atencionsismo source an empty np means "sin dato" (design D1). */
export function faseDe(evaluacion) {
  const np = String((evaluacion && evaluacion.inspector && evaluacion.inspector.np) || '').trim();
  if (evaluacion && evaluacion.fuente === 'atencionsismo' && !np) return FASE_SIN_DATO;
  return FASE_BY_KEY.get(faseInspector(np));
}

// "Colorear por" — same segmented control acciones-capa.js's own
// COLOR_VARS/colorSegmentedHtml pattern uses, two variables instead of
// three: which one drives the map marker + list dot colour. Both pills in
// listItemHtml stay visible regardless of the active mode — this only
// changes which dimension the DOT (map + row) emphasises.
const COLOR_MODES = {
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

const tituloDe = (e) => e.descripcion.nombre || e.descripcion.direccion || e.codigo_edificacion;

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
    if (filters.comuna && e._comuna !== filters.comuna) return false;
    if (filters.barrio && e._barrio !== filters.barrio) return false;
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

/** Human-readable summary of the active filters, for the xlsx header block. */
function describeFilters(f) {
  const parts = [];
  if (f.clase) parts.push(`Clasificación: ${(CLASE_BY_KEY.get(f.clase) || SIN_CLASE).label}`);
  if (f.fase) parts.push(`Fase: ${(FASE_BY_KEY.get(f.fase) || FASE_SIN_DATO).label}`);
  if (f.comuna) parts.push(`Comuna: ${f.comuna}`);
  if (f.barrio) parts.push(`Barrio: ${f.barrio}`);
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
function resolveGeoFor(coords) {
  if (!coords) return Promise.resolve({ _comuna: null, _barrio: null });
  const key = `${coords.lat.toFixed(5)},${coords.lng.toFixed(5)}`;
  if (!geoCache.has(key)) {
    geoCache.set(key, resolveBarrioComuna(coords.lat, coords.lng)
      .then(({ comuna, barrio }) => ({ _comuna: comuna, _barrio: barrio })));
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

/** Repopulate the comuna <select>, preserving the current selection when it
 *  is still a valid option — same pattern as planeacion.js's renderAutoScopeSelects. */
function renderComunaSelect(selectEl, comunaMap) {
  const comunas = [...comunaMap.keys()].sort();
  const prev = selectEl.value;
  selectEl.innerHTML = '<option value="">— Todas las comunas —</option>'
    + comunas.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  selectEl.value = comunas.includes(prev) ? prev : '';
}

/** Repopulate the barrio <select>, dependent on the chosen comuna — disabled
 *  until one is picked, same pattern as planeacion.js's renderAutoBarrioSelect. */
function renderBarrioSelect(selectEl, comunaMap, comuna) {
  const barrios = comuna ? [...(comunaMap.get(comuna) || [])].sort() : [];
  const prev = selectEl.value;
  selectEl.innerHTML = '<option value="">— Todos los barrios —</option>'
    + barrios.map((b) => `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`).join('');
  selectEl.disabled = !comuna;
  selectEl.value = barrios.includes(prev) ? prev : '';
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
          <label class="sticker-field asignacion-inline-field">
            <span>Comuna</span>
            <select id="eval-comuna-select" aria-label="Filtrar por comuna"><option value="">— Todas las comunas —</option></select>
          </label>
          <label class="sticker-field asignacion-inline-field">
            <span>Barrio</span>
            <select id="eval-barrio-select" aria-label="Filtrar por barrio" disabled><option value="">— Todos los barrios —</option></select>
          </label>
          <button type="button" class="sticker-action" id="eval-download">Descargar .xlsx</button>
        </div>
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
      <div class="kpi-sub-row"><span class="kpi-sub">evaluaciones enviadas desde el formulario</span></div>
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

// One Leaflet instance for the whole tab. The Stickers view re-renders its
// root on every open, which detaches the old container — remove() first or
// each visit leaks a map plus its tile requests.
let map = null;
let baseTile = null;
let pointsLayer = null;
let legendEl = null;
let stickerGaugeEl = null;
// Bounds fitted at build time. The map is built while its section is hidden
// (0×0), so the build-time fit computes a wrong zoom; re-applied on invalidate
// once the section is visible and correctly sized.
let lastFitBounds = null;
// id -> circleMarker, so hovering a row in the aside can point at the map.
let markerById = new Map();

function teardownMap() {
  if (map) { map.remove(); map = null; }
  baseTile = null;
  pointsLayer = null;
  legendEl = null;
  stickerGaugeEl = null;
  markerById = new Map();
}

// Cobertura de stickers en Panel (cruce con evaluaciones), misma cifra que el
// gauge del Panel. Vive como control de Leaflet — esquina superior derecha,
// libre (zoom en topleft, leyenda ATC-20 en bottomright) — en vez de un bloque
// propio en el flujo: la cifra es contexto secundario, no merece su propia fila.
// Oculto (display:none) hasta que /api/sticker-status resuelva.
function setStickerGauge(coverage) {
  if (!stickerGaugeEl) return;
  const html = coverage ? coverageGaugeHtml(coverage) : '';
  stickerGaugeEl.innerHTML = html;
  stickerGaugeEl.style.display = html ? 'block' : 'none';
}

// Reacts to the SAME store notify the Panel gauge does: main.js's 15-min
// refreshStickerStatus() calls store.setStickerCoverage(), which notifies
// every subscriber. Without this, the map gauge only refreshed on the
// section's OWN load()/poll cycle — which the fingerprint short-circuit in
// load() below skips whenever the evaluaciones themselves haven't changed,
// so the gauge could sit stale well past 15 minutes. Module scope like
// themechange below: a listener registered inside initEvaluaciones() would
// stack on every tab reopen.
store.subscribe(() => setStickerGauge(store.stickerCoverage));

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
        <dt>Fotos</dt><dd>${e.fotos.length}</dd>
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

function renderMap(containerId, evaluaciones, onDetail) {
  teardownMap();
  const conCoords = evaluaciones.filter((e) => e.coords);

  map = L.map(containerId, { zoomControl: true, minZoom: 10, maxZoom: 18 })
    .setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);

  pointsLayer = L.layerGroup().addTo(map);
  for (const e of conCoords) {
    const marker = L.circleMarker([e.coords.lat, e.coords.lng], {
      radius: 8,
      color: '#0B1D33',
      weight: 1,
      fillColor: COLOR_MODES[colorMode].colorOf(e),
      fillOpacity: 0.9,
    });
    marker.bindPopup(popupHtml(e), { maxWidth: 280 });
    marker.on('popupopen', (ev) => {
      const btn = ev.popup.getElement().querySelector('[data-eval-detail]');
      if (btn) btn.addEventListener('click', () => onDetail(e.id));
    });
    marker.addTo(pointsLayer);
    markerById.set(e.id, marker);
  }

  const legend = L.control({ position: 'bottomright' });
  legend.onAdd = () => {
    legendEl = L.DomUtil.create('div', 'map-legend');
    L.DomEvent.disableClickPropagation(legendEl);
    legendEl.innerHTML = colorLegendHtml();
    return legendEl;
  };
  legend.addTo(map);

  const stickerGauge = L.control({ position: 'topright' });
  stickerGauge.onAdd = () => {
    stickerGaugeEl = L.DomUtil.create('div', 'map-legend map-sticker-gauge');
    L.DomEvent.disableClickPropagation(stickerGaugeEl);
    stickerGaugeEl.style.display = 'none';
    return stickerGaugeEl;
  };
  stickerGauge.addTo(map);

  // Frame the actual data instead of a hardcoded city view — a single
  // evaluation in one corner of Cali is otherwise invisible at zoom 12.
  const inBox = conCoords.filter((e) =>
    e.coords.lat >= CALI_BBOX.latMin && e.coords.lat <= CALI_BBOX.latMax
    && e.coords.lng >= CALI_BBOX.lngMin && e.coords.lng <= CALI_BBOX.lngMax);
  lastFitBounds = inBox.length ? L.latLngBounds(inBox.map((e) => [e.coords.lat, e.coords.lng])) : null;
  if (lastFitBounds) {
    map.fitBounds(lastFitBounds, { padding: [40, 40], maxZoom: 16 });
  } else {
    map.setView(CALI_CENTER, CALI_ZOOM);
  }
  // The tab is hidden until switchView() shows it, so Leaflet measures a
  // zero-height container on first mount.
  setTimeout(() => { if (map) map.invalidateSize(); }, 80);

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
  const fotos = e.fotos.length ? `${e.fotos.length} foto${e.fotos.length === 1 ? '' : 's'}` : 'sin fotos';
  const quien = e.inspector.nombre_completo || `Brigada ${e.inspector.codigo || '—'}`;
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

  const fotos = e.fotos.length
    ? e.fotos.map((url, i) => `<button type="button" class="detail-photo" data-foto-idx="${i}" aria-label="Ampliar foto ${i + 1}"><img src="${escapeHtml(url)}" alt="Foto ${i + 1} de la edificación" loading="lazy"></button>`).join('')
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

/** Renders the evaluaciones section into `section` (already in the DOM).
 *  `fetchEvaluaciones` returns the array; failures render inline so a broken
 *  evaluations read never takes the inspector roster down with it. */
export function initEvaluaciones(section, { fetchEvaluaciones }) {
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
  const colorGroupEl = section.querySelector('[data-eval-color-group]');
  const comunaSelect = section.querySelector('#eval-comuna-select');
  const barrioSelect = section.querySelector('#eval-barrio-select');
  const downloadBtn = section.querySelector('#eval-download');
  const modalPdfBtn = section.querySelector('#eval-modal-pdf');
  let byId = new Map();
  // Full, geo-resolved dataset from the last successful fetch — filters below
  // read/write these without ever re-fetching or re-resolving geo.
  let allEvaluaciones = [];
  let comunaMap = new Map(); // comuna -> Set(barrio), rebuilt alongside allEvaluaciones
  let filters = { search: '', clase: '', fase: '', comuna: '', barrio: '' };
  // Which evaluación the modal is currently showing — the modal's own PDF
  // button (unlike the row ones) has no per-row index to read from.
  let modalEvaluacion = null;
  // Mirrors the last `degraded` flag load() saw (see there): a PDF/xlsx
  // generated from the redacted backup copy would carry a wrong Fase I/II
  // reading with nothing on the exported file to say so, so every export
  // path is blocked outright while this is true, not just banner-warned.
  let isDegraded = false;

  // Panel-wide sticker coverage (same figure as the Panel gauge), from the store
  // that main.js populates via /api/sticker-status. Lives on the map itself
  // (setStickerGauge, a Leaflet control) — must run AFTER renderMap() below,
  // since that call rebuilds the control from scratch each time.
  const renderCoverage = () => setStickerGauge(store.stickerCoverage);

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
    const filtered = applyFilters(allEvaluaciones, filters);
    chipsEl.innerHTML = claseChipsHtml(filters.clase);
    faseChipsEl.innerHTML = faseChipsHtml(filters.fase);
    kpis.innerHTML = kpisHtml(filtered);
    barEl.innerHTML = barHtml(filtered);

    if (!filtered.length) {
      listEl.innerHTML = allEvaluaciones.length
        ? '<li class="eval-empty">Ningún registro coincide con los filtros aplicados.</li>'
        : '<li class="eval-empty">Todavía no hay evaluaciones registradas desde el formulario.</li>';
      listMeta.textContent = '';
    } else {
      listEl.innerHTML = filtered.map((e) => listItemHtml(e, isDegraded)).join('');
      listMeta.textContent = `${filtered.length} · más reciente primero`;
    }

    const conCoords = renderMap('eval-map', filtered, openDetail);
    renderCoverage(); // after renderMap: it rebuilds the control this writes into
    const sinCoords = filtered.length - conCoords;
    mapMeta.textContent = sinCoords
      ? `${conCoords} en el mapa · ${sinCoords} sin coordenadas`
      : `${conCoords} en el mapa`;
  }

  async function load({ silent = false } = {}) {
    if (!silent) {
      kpis.innerHTML = '<p class="sticker-loading">Cargando evaluaciones…</p>';
      barEl.innerHTML = '';
      listEl.innerHTML = '';
      listMeta.textContent = '';
      mapMeta.textContent = '';
    }
    try {
      const { evaluaciones, degraded } = await fetchEvaluaciones();
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
      const fingerprint = JSON.stringify(evaluaciones.map((e) => [e.id, e.clasificacion, (e.fotos || []).length]));
      if (silent && fingerprint === lastFingerprint) return;
      lastFingerprint = fingerprint;

      allEvaluaciones = await Promise.all(
        evaluaciones.map(async (e) => ({ ...e, ...(await resolveGeoFor(e.coords)) })),
      );
      byId = new Map(allEvaluaciones.map((e) => [e.id, e]));

      comunaMap = comunaBarrioMap(allEvaluaciones);
      renderComunaSelect(comunaSelect, comunaMap);
      renderBarrioSelect(barrioSelect, comunaMap, comunaSelect.value);
      // The selects may have dropped the previous value (comuna/barrio no
      // longer present in the refreshed data) — keep `filters` in sync with
      // what's actually selected instead of filtering by a stale value.
      filters.comuna = comunaSelect.value;
      filters.barrio = barrioSelect.value;

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
      kpis.innerHTML = `<p class="sticker-error" role="alert">No se pudieron cargar las evaluaciones: ${escapeHtml(err.message)}</p>`;
    }
  }

  reloadBtn.addEventListener('click', () => load());

  // ---- filter wiring: each control only updates `filters` and re-renders
  // from the already-loaded, already-geo-resolved allEvaluaciones. ----------
  searchEl.addEventListener('input', () => {
    filters = { ...filters, search: searchEl.value };
    renderFiltered();
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

  comunaSelect.addEventListener('change', () => {
    filters = { ...filters, comuna: comunaSelect.value, barrio: '' };
    renderBarrioSelect(barrioSelect, comunaMap, comunaSelect.value);
    renderFiltered();
  });

  barrioSelect.addEventListener('change', () => {
    filters = { ...filters, barrio: barrioSelect.value };
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
      num_fotos: e.fotos.length,
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
