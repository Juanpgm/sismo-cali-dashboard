// Vuelos UAS tab: the "relacion_vuelos_uas_sismo_cali" drone-flight layer from
// ArcGIS, rendered client-side — KPIs, one chart per variable, a Leaflet map
// with its flight list, links to each flight's Google Drive folder, and the
// official ArcGIS Dashboard embedded below.
//
// The layer is the Survey123 capture view (`service_..._form`): it is queried
// READ-ONLY here — never open anonymous editing on it (Create/Update are
// exposed publicly, an edit UI would let anyone alter field records).
import { COLORS, escapeHtml, basemapTileUrl, themeColor, showToast } from './utils.js';

/* global L, Chart */

const CAPA_URL = 'https://services8.arcgis.com/ljfiJpg35HWgdtaC/arcgis/rest/services/service_b39faeeb8f6a4222b67ca2c500eee2b8_form/FeatureServer/0';
const DASHBOARD_URL = 'https://www.arcgis.com/apps/dashboards/fef40bd53d964e2496d60d5b8d903e13';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;

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
/* Shell                                                               */
/* ------------------------------------------------------------------ */

let colorVar = 'estado_edificacion';
let map = null;
let baseTile = null;
const charts = new Map();
let rootRef = null;
// Set when a themechange re-render was skipped because the tab was hidden —
// Leaflet mis-fits a display:none 0×0 container. Consumed on the next
// initVuelosUasTab call with the tab visible.
let vuelosDirty = false;

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
}

// Basemap + chart colors are baked at construction; rebuild on theme change,
// but never into a hidden container (same guard as acciones-capa.js).
if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => {
    if (map && baseTile) {
      map.removeLayer(baseTile);
      baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
      baseTile.bringToBack();
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
    fetchPromise = fetchPromise || fetchCapa();
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

function kpisHtml(rows) {
  const total = rows.length;
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);
  const colTotal = rows.filter((r) => code(r.estado_edificacion) === 'colapso_total').length;
  const colParcial = rows.filter((r) => code(r.estado_edificacion) === 'colapso_parcial').length;
  const riesgo = rows.filter((r) => code(r.estado_edificacion) === 'riesgo_caida').length;
  const demolicion = rows.filter((r) => code(r.definicion) === 'demolicion_confirmada').length;
  const porConfirmar = rows.filter((r) => code(r.definicion) === 'se_debe_confirmar').length;
  const conDrive = rows.filter((r) => driveLink(r.enlace_drive)).length;
  const dias = new Set(rows.map((r) => diaKey(r.dia_captura)).filter(Boolean)).size;
  return `
    <div class="kpi-tile is-neutral">
      <span class="kpi-label">Vuelos registrados</span>
      <span class="kpi-value">${total}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">en ${dias} días de captura</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.status.i3}">
      <span class="kpi-label">Colapso total / parcial</span>
      <span class="kpi-value">${colTotal} / ${colParcial}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">estado observado desde el aire</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.damage.medio}">
      <span class="kpi-label">Riesgo de caída</span>
      <span class="kpi-value">${riesgo}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">${pct(riesgo)}% de los vuelos</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.status.i2}">
      <span class="kpi-label">Demolición confirmada</span>
      <span class="kpi-value">${demolicion}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">${porConfirmar} por confirmar</span></div>
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

/** Row card, same recipe as Acciones' accionRowHtml: dot + place first line,
 *  meta lines below, and the Drive link as a SIBLING of .eval-row inside
 *  .ps-row-wrap (link-in-button is invalid HTML). */
function vueloRowHtml(row, i) {
  const color = colorFor('estado_edificacion', row.estado_edificacion);
  const drive = driveLink(row.enlace_drive);
  return `
    <li>
      <div class="ps-row-wrap">
        <button type="button" class="eval-row" data-uas-row="${i}">
          <span class="eval-dot" style="background:${color}" aria-hidden="true" title="${escapeHtml(label(row.estado_edificacion))}"></span>
          <span class="eval-name">${escapeHtml(row.lugar || `Vuelo ${row.objectid}`)}</span>
          <span class="eval-meta">${escapeHtml(formatDia(row.dia_captura))} · ${escapeHtml(label(row.estado_edificacion))}</span>
          <span class="eval-meta">${escapeHtml(tokens(row.origen).map(label).join(' · ') || 'Sin origen')}</span>
        </button>
        ${drive ? `<a class="btn-icon" href="${escapeHtml(drive)}" target="_blank" rel="noopener" title="Abrir carpeta de Google Drive" aria-label="Abrir carpeta de Google Drive">${DRIVE_ICON}</a>` : ''}
      </div>
    </li>`;
}

function popupHtml(row) {
  const drive = driveLink(row.enlace_drive);
  const productos = tokens(row.productos).map(label).join(', ');
  const origen = tokens(row.origen).map(label).join(', ');
  const line = (lbl, html) => (html ? `<div class="uas-popup-line"><strong>${lbl}:</strong> ${html}</div>` : '');
  return `
    <div class="uas-popup">
      <div class="uas-popup-title">${escapeHtml(row.lugar || `Vuelo ${row.objectid}`)}</div>
      <div class="uas-popup-line">${escapeHtml(formatDia(row.dia_captura))}</div>
      ${line('Estado', chip('estado_edificacion', row.estado_edificacion))}
      ${row.confirmacion ? line('Confirmación', chip('confirmacion', row.confirmacion)) : ''}
      ${row.definicion ? line('Definición', chip('definicion', row.definicion)) : ''}
      ${line('Origen', escapeHtml([origen, row.origen_otro].filter(Boolean).join(' — ')) || '')}
      ${line('Productos', escapeHtml(productos))}
      ${row.observacion ? line('Observación', escapeHtml(row.observacion)) : ''}
      ${row.objectid_survey ? line('Código survey', escapeHtml(String(row.objectid_survey))) : ''}
      ${drive ? `<a class="uas-popup-drive" href="${escapeHtml(drive)}" target="_blank" rel="noopener">${DRIVE_ICON}<span>Abrir carpeta de Drive</span></a>` : ''}
    </div>`;
}

function renderVuelos(sectionEl) {
  destroyCharts();
  teardownMap();

  const rows = [...featuresCache].sort((a, b) => String(diaKey(b.dia_captura) || '').localeCompare(String(diaKey(a.dia_captura) || '')));
  const conDrive = rows.filter((r) => driveLink(r.enlace_drive)).length;

  sectionEl.innerHTML = `
    <div class="section-bar">
      <h3 class="section-bar-title">Capa «relacion_vuelos_uas_sismo_cali»</h3>
      <button type="button" class="sticker-action" data-uas-reload>Actualizar</button>
    </div>
    <div class="kpi-row">${kpisHtml(rows)}</div>

    <div class="stats-grid">
      <div class="chart-tile"><h3 class="chart-tile-title">Estado de la edificación</h3><canvas id="uas-chart-estado"></canvas></div>
      <div class="chart-tile"><h3 class="chart-tile-title">Definición de demolición</h3><canvas id="uas-chart-definicion"></canvas></div>
      <div class="chart-tile"><h3 class="chart-tile-title">Productos capturados</h3><canvas id="uas-chart-productos"></canvas></div>
      <div class="chart-tile chart-tile-wide"><h3 class="chart-tile-title">Confirmación en campo</h3><canvas id="uas-chart-confirmacion"></canvas></div>
      <div class="chart-tile chart-tile-wide"><h3 class="chart-tile-title">Origen del vuelo</h3><canvas id="uas-chart-origen"></canvas></div>
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
            <span class="eval-toolbar-meta">${rows.length} vuelos · ${conDrive} con carpeta Drive</span>
          </div>
          <ul class="eval-list" data-uas-list>${rows.map((r, i) => vueloRowHtml(r, i)).join('')}</ul>
        </div>
      </div>
    </section>

    <section class="card uas-embed-card" aria-label="Dashboard ArcGIS de vuelos UAS">
      <div class="card-toolbar">
        <span class="eval-toolbar-title">Dashboard ArcGIS</span>
        <a class="sticker-action" href="${DASHBOARD_URL}" target="_blank" rel="noopener">Abrir en ArcGIS</a>
      </div>
      <iframe class="uas-embed-frame" src="${DASHBOARD_URL}" title="Dashboard ArcGIS de vuelos UAS" loading="lazy" allowfullscreen></iframe>
    </section>`;

  sectionEl.querySelector('[data-uas-reload]').addEventListener('click', () => {
    featuresCache = null;
    loadErrored = false;
    loadVuelos(sectionEl);
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
  sectionEl.querySelector('[data-uas-list]').addEventListener('click', (e) => {
    const rowBtn = e.target.closest('[data-uas-row]');
    if (!rowBtn) return;
    const entry = markers[Number(rowBtn.dataset.uasRow)];
    if (!entry) { showToast('Este vuelo no tiene coordenadas.', 'error'); return; }
    map.flyTo(entry.marker.getLatLng(), Math.max(map.getZoom(), 16));
    entry.marker.openPopup();
  });

  renderCapaMap(rows);
  renderCharts(rows);
}

/* ------------------------------------------------------------------ */
/* Map                                                                 */
/* ------------------------------------------------------------------ */

// Sparse array indexed like `rows` so list rows address their marker directly.
let markers = [];

function renderCapaMap(rows) {
  map = L.map('uas-capa-map', { zoomControl: true, minZoom: 10, maxZoom: 18 }).setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);

  markers = [];
  rows.forEach((row, i) => {
    if (!Number.isFinite(row._lat) || !Number.isFinite(row._lng)) return;
    const marker = L.circleMarker([row._lat, row._lng], {
      radius: 8, color: '#0B1D33', weight: 1, fillOpacity: 0.9,
      fillColor: colorFor(colorVar, row[colorVar]),
    });
    marker.bindTooltip(escapeHtml(row.lugar || `Vuelo ${row.objectid}`));
    marker.bindPopup(popupHtml(row), { maxWidth: 300 });
    marker.addTo(map);
    markers[i] = { marker, row };
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
  if (placed.length) {
    map.fitBounds(L.latLngBounds(placed.map(({ marker }) => marker.getLatLng())), { padding: [40, 40], maxZoom: 16 });
  }
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
