// Acciones tab: the "Formulario demolición" data layer — the "EDE candidato
// demolición" field review layer from ArcGIS, joined against the Panel's EDE
// records.
//
// The join is the core: objectivo_id_r is free text ("ID 25", "473"...) whose
// first integer is the EDE ObjectID. The joined EDE record supplies address,
// coords, barrio, evaluator and habitability; the form supplies the demolition
// review verdict. Records without a match still render, flagged "Sin cruce EDE".
import {
  COLORS, escapeHtml, labelForCode, addressDisplay, barrioVeredaDisplay, basemapTileUrl,
  themeColor, SURVEY_LAYER_URL, isFirmaAttachment, attachmentUrl, showToast,
  loadXlsx, downloadStamp,
} from './utils.js';
import { buildMiniMap } from './mapview.js';
import { openLightbox } from './table.js';
import { generarInformeCandidato } from './report.js';

/* global L, Chart */

const CAPA_URL = 'https://services8.arcgis.com/ljfiJpg35HWgdtaC/arcgis/rest/services/service_795f84b10eaa4719a46849b415a3e713/FeatureServer/0';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;

// Selectable "color by" variables for the map, with the semaphore language the
// rest of the dashboard speaks (red = act, green = clear, amber = pending).
const COLOR_VARS = {
  candidato_demolicion: {
    label: 'Candidato',
    entries: [
      { value: 'si', label: 'Candidato: sí', color: COLORS.status.i2 },
      { value: 'no', label: 'Candidato: no', color: COLORS.status.h },
    ],
  },
  colapso: {
    label: 'Colapso',
    entries: [
      { value: 'total', label: 'Colapso total', color: COLORS.status.i3 },
      { value: 'parcial', label: 'Colapso parcial', color: COLORS.damage.medio },
    ],
  },
  visita: {
    label: 'Visita',
    entries: [
      { value: 'si', label: 'Requiere visita', color: COLORS.status.r2 },
      { value: 'no', label: 'Sin visita adicional', color: COLORS.status.h },
    ],
  },
};

const code = (v) => String(v ?? '').trim().toLowerCase();

function colorFor(varName, value) {
  const entry = (COLOR_VARS[varName]?.entries || []).find((e) => e.value === code(value));
  return entry ? entry.color : COLORS.unknown;
}

function formatFecha(ms, { withTime = false } = {}) {
  if (!ms) return 'Sin fecha';
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return 'Sin fecha';
  return withTime
    ? d.toLocaleString('es-CO', { dateStyle: 'medium', timeStyle: 'short' })
    : d.toLocaleDateString('es-CO', { dateStyle: 'medium' });
}

const badge = (field, value) => (value
  ? `<span class="badge badge-${escapeHtml(field)}-${escapeHtml(code(value))}">${escapeHtml(labelForCode(value))}</span>`
  : '—');

/* ------------------------------------------------------------------ */
/* Fetch + join                                                        */
/* ------------------------------------------------------------------ */

// Raw form attributes from the last successful /query. Refetched only on
// explicit action (Actualizar / Reintentar) — the layer has ~78 rows and the
// field review moves slowly. `fetchPromise` de-dupes concurrent loads (store
// notify while the first fetch is pending); `loadErrored` keeps a failed load
// from silently refetching on the next notify — only Reintentar/Actualizar
// clear it.
let featuresCache = null;
let fetchPromise = null;
let loadErrored = false;

async function fetchCapa() {
  // Page through resultOffset: ArcGIS silently truncates at maxRecordCount
  // and flags it with exceededTransferLimit instead of erroring.
  const all = [];
  let more = true;
  while (more) {
    const res = await fetch(`${CAPA_URL}/query?where=1=1&outFields=*&f=json&resultOffset=${all.length}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (json.error) throw new Error(json.error.message || 'Error de la capa');
    const feats = json.features || [];
    all.push(...feats.map((f) => f.attributes));
    more = Boolean(json.exceededTransferLimit) && feats.length > 0;
  }
  return all;
}

/** First integer in objectivo_id_r -> EDE record by ObjectID. Parsed key is
 *  normalized numerically so zero-padded free text ("025") still matches.
 *  Geometry of the layer is all (0,0), so coords ALWAYS come from the joined
 *  EDE record. */
export function joinCapa(features, records) {
  const byId = new Map(records.map((r) => [String(r.ObjectID), r]));
  return features.map((form) => {
    const m = /\d+/.exec(String(form.objectivo_id_r ?? ''));
    return { form, ede: (m && byId.get(String(parseInt(m[0], 10)))) || null };
  });
}

/* ------------------------------------------------------------------ */
/* Shell                                                               */
/* ------------------------------------------------------------------ */

let colorVar = 'candidato_demolicion';
let map = null;
let baseTile = null;
const charts = new Map();
// Refs of the last formulario render, to skip rebuilding (and resetting the
// map) when a store notify didn't actually change the inputs.
let lastRecordsRef = null;
let lastFeaturesRef = null;
let rootRef = null;
// Latest records from main.js, so deferred renders (segment switch, fetch
// resolve) never paint with a stale array.
let currentRecords = [];
// Set when a render was skipped because the section was hidden (tab hidden
// during a theme change) — Leaflet mis-fits a display:none 0×0 container.
// Consumed on the next initAccionesTab call with the tab visible.
let formularioDirty = false;

function shellHtml() {
  return `
    <header class="sticker-page-head">
      <h2 class="sticker-h1">Acciones</h2>
      <p class="sticker-lead">Revisión de campo de candidatos a demolición.</p>
    </header>
    <div data-accion-section="formulario"></div>`;
}

function destroyCharts() {
  for (const c of charts.values()) c.destroy();
  charts.clear();
}

function teardownMap() {
  if (map) { map.remove(); map = null; }
  baseTile = null;
}

// The Panel swaps basemap tiles on theme change; without this a light-mode
// dashboard keeps a dark basemap (and stale chart colors) here. Module scope
// so tab reopens can't stack listeners.
if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => {
    if (map && baseTile) {
      map.removeLayer(baseTile);
      baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
      baseTile.bringToBack();
    }
    // Chart.js bakes CSS-var colors at construction — force a rebuild. Never
    // rebuild into a hidden container (Leaflet would mis-fit a 0×0 box, same
    // guard as evaluaciones.js's poll): mark dirty and let the visibility
    // change consume it.
    if (!rootRef || !featuresCache || !lastRecordsRef) return;
    const sec = rootRef.querySelector('[data-accion-section="formulario"]');
    if (!sec) return;
    if (document.visibilityState === 'hidden' || sec.closest('[hidden]')) {
      formularioDirty = true;
      return;
    }
    renderFormulario(sec, currentRecords);
  });
}

/** Entry point, called by main.js on tab open and on store change while the
 *  tab is visible. Builds the shell once; the content re-renders only when
 *  its inputs actually changed so the map keeps its pan/zoom. */
export function initAccionesTab(root, { records }) {
  rootRef = root;
  currentRecords = records;
  if (!root.querySelector('[data-accion-section]')) root.innerHTML = shellHtml();

  const formularioEl = root.querySelector('[data-accion-section="formulario"]');
  if (!records.length) {
    // Admin opened the tab before the first store load — show a neutral
    // placeholder; the next store notify re-enters with real records.
    formularioEl.innerHTML = '<p class="sticker-loading">Cargando datos del panel…</p>';
    return;
  }
  const upToDate = featuresCache && records === lastRecordsRef && featuresCache === lastFeaturesRef;
  if (upToDate && !formularioDirty) {
    // Same data as the last render — just re-measure the map (tab may have
    // been hidden when it was built).
    if (map) setTimeout(() => map.invalidateSize(), 60);
    return;
  }
  if (formularioEl.closest('[hidden]')) {
    // Rendering into a display:none 0×0 container mis-fits the Leaflet map.
    // Defer to the next call with the tab visible.
    formularioDirty = true;
    return;
  }
  formularioDirty = false;
  renderOrLoad(formularioEl);
}

/** Render from cache, or fetch if there is none. A pending fetch renders on
 *  its own resolve; a failed one leaves its error card (only Reintentar /
 *  Actualizar refetch). */
function renderOrLoad(sectionEl) {
  if (featuresCache) renderFormulario(sectionEl, currentRecords);
  else if (!loadErrored && !fetchPromise) loadFormulario(sectionEl);
}

async function loadFormulario(sectionEl) {
  sectionEl.innerHTML = '<p class="sticker-loading">Cargando formulario de demolición…</p>';
  try {
    // Concurrent callers (store notify while pending) share one request.
    fetchPromise = fetchPromise || fetchCapa();
    featuresCache = await fetchPromise;
    loadErrored = false;
    renderFormulario(sectionEl, currentRecords);
  } catch (err) {
    loadErrored = true;
    sectionEl.innerHTML = `
      <section class="card accion-error-card">
        <p class="sticker-error" role="alert">No se pudo cargar la capa del formulario: ${escapeHtml(err.message)}</p>
        <button type="button" class="btn-primary" data-accion-retry>Reintentar</button>
      </section>`;
    sectionEl.querySelector('[data-accion-retry]').addEventListener('click', () => {
      loadErrored = false;
      loadFormulario(sectionEl);
    });
  } finally {
    fetchPromise = null;
  }
}

/* ------------------------------------------------------------------ */
/* Formulario section render                                           */
/* ------------------------------------------------------------------ */

function kpisHtml(rows) {
  const total = rows.length;
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);
  const candidatos = rows.filter((r) => code(r.form.candidato_demolicion) === 'si').length;
  const visitas = rows.filter((r) => code(r.form.visita) === 'si').length;
  const colTotal = rows.filter((r) => code(r.form.colapso) === 'total').length;
  const colParcial = rows.filter((r) => code(r.form.colapso) === 'parcial').length;
  const cruzados = rows.filter((r) => r.ede).length;
  return `
    <div class="kpi-tile is-neutral">
      <span class="kpi-label">Registros</span>
      <span class="kpi-value">${total}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">revisiones del formulario</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.status.i2}">
      <span class="kpi-label">Candidato a demolición</span>
      <span class="kpi-value">${candidatos}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">${pct(candidatos)}% del total</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.status.r2}">
      <span class="kpi-label">Requiere visita adicional</span>
      <span class="kpi-value">${visitas}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">${pct(visitas)}% del total</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.status.i3}">
      <span class="kpi-label">Colapso total / parcial</span>
      <span class="kpi-value">${colTotal} / ${colParcial}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">según la revisión de campo</span></div>
    </div>
    <div class="kpi-tile" style="--kpi-accent:${COLORS.status.h}">
      <span class="kpi-label">Cruzados con EDE</span>
      <span class="kpi-value">${cruzados} / ${total}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">con registro EDE vinculado</span></div>
    </div>`;
}

function colorSegmentedHtml() {
  return Object.entries(COLOR_VARS).map(([key, def]) => `
    <button type="button" class="segmented-btn${key === colorVar ? ' is-active' : ''}"
      data-accion-color="${key}" role="tab" aria-selected="${key === colorVar}">${escapeHtml(def.label)}</button>`).join('');
}

// PDF icon, swapped for a spinner while generarInformeCandidato runs — the
// button used to just go `disabled` with no other feedback, which reads as
// unresponsive on a slower connection.
const PDF_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><polyline points="9 15 12 18 15 15"/></svg>';
const SPINNER_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9" stroke-opacity=".25"/><path d="M21 12a9 9 0 0 0-9-9"/></svg>';

/** Row card, same recipe as Stickers' evaluaciones.js buildEvalRow: a dot +
 *  name first line, then full-width meta lines below — no per-column
 *  headers to truncate, so the aside can carry as much text as it needs.
 *  The candidato "Sí/No" pill is gone: the dot already carries that same
 *  color (same legend the map uses), so the pill was the same information
 *  twice, printed as its own oddly-placed right-aligned label — dropping it
 *  is also what lets .eval-name use the row's full width instead of leaving
 *  the big gap in front of it (see the #view-acciones .eval-row override in
 *  styles.css). The PDF button is a SIBLING of .eval-row (button-in-button
 *  is invalid HTML), same .ps-row-wrap fix puntos_solicitados.js already
 *  uses for its "Asignar" button next to .eval-row. */
function accionRowHtml(row, i) {
  const { form, ede } = row;
  const color = ede ? colorFor('candidato_demolicion', form.candidato_demolicion) : COLORS.unknown;
  const dotTitle = ede ? `Candidato a demolición: ${labelForCode(form.candidato_demolicion)}` : 'Sin cruce EDE';
  const name = ede
    ? escapeHtml(addressDisplay(ede).primary || `Registro ${ede.ObjectID}`)
    : `${escapeHtml(form.objectivo_id_r ?? '—')} <span class="badge badge-sin-cruce">Sin cruce EDE</span>`;
  const ubicacion = ede ? [barrioVeredaDisplay(ede), ede.comuna].filter(Boolean).map(escapeHtml).join(' · ') : '';
  return `
    <li>
      <div class="ps-row-wrap">
        <button type="button" class="eval-row" data-accion-row="${i}">
          <span class="eval-dot" style="background:${color}" aria-hidden="true" title="${escapeHtml(dotTitle)}"></span>
          <span class="eval-name">${name}</span>
          ${ubicacion ? `<span class="eval-meta">${ubicacion}</span>` : ''}
          <span class="eval-meta">${escapeHtml(formatFecha(form.fecha_registro))} · ${badge('colapso', form.colapso)}</span>
          <span class="eval-meta">${escapeHtml(ede?.nombre_evaluador || 'Sin profesional')}</span>
        </button>
        <button type="button" class="btn-icon accion-pdf-btn" data-accion-pdf="${i}" title="Descargar informe PDF" aria-label="Descargar informe PDF">${PDF_ICON}</button>
      </div>
    </li>`;
}

function accionRowsHtml(rows) {
  return rows.map((row, i) => accionRowHtml(row, i)).join('');
}

/** Flat, spreadsheet-friendly shape of the joined rows — mirrors the visible
 *  table columns (plus the EDE ObjectID and the cruce flag) rather than the
 *  raw form/ede field dump: the join is the point of this view, so a flat
 *  "no cruce" record with the ArcGIS field names would be less useful than
 *  what the table already shows. */
function buildExportRows(rows) {
  return rows.map(({ form, ede }) => ({
    direccion: ede ? (addressDisplay(ede).primary || `Registro ${ede.ObjectID}`) : (form.objectivo_id_r ?? ''),
    barrio: ede ? barrioVeredaDisplay(ede) : '',
    fecha_registro: formatFecha(form.fecha_registro, { withTime: true }),
    candidato_demolicion: form.candidato_demolicion ? labelForCode(form.candidato_demolicion) : '',
    colapso: form.colapso ? labelForCode(form.colapso) : '',
    visita: form.visita ? labelForCode(form.visita) : '',
    profesional: ede?.nombre_evaluador || '',
    ede_object_id: ede?.ObjectID ?? '',
    sin_cruce_ede: ede ? 'No' : 'Sí',
  }));
}

function renderFormulario(sectionEl, records) {
  destroyCharts();
  teardownMap();
  lastRecordsRef = records;
  lastFeaturesRef = featuresCache;

  const rows = joinCapa(featuresCache, records)
    .sort((a, b) => (b.form.fecha_registro || 0) - (a.form.fecha_registro || 0));
  const cruzados = rows.filter((r) => r.ede).length;

  sectionEl.innerHTML = `
    <div class="section-bar">
      <h3 class="section-bar-title">Formulario «EDE candidato demolición»</h3>
      <button type="button" class="sticker-action" data-accion-reload>Actualizar</button>
    </div>
    <div class="kpi-row" data-accion-kpis>${kpisHtml(rows)}</div>

    <div class="stats-grid">
      <div class="chart-tile"><h3 class="chart-tile-title">Candidato a demolición</h3><canvas id="accion-chart-candidato"></canvas></div>
      <div class="chart-tile"><h3 class="chart-tile-title">Tipo de colapso</h3><canvas id="accion-chart-colapso"></canvas></div>
      <div class="chart-tile"><h3 class="chart-tile-title">Requiere visita adicional</h3><canvas id="accion-chart-visita"></canvas></div>
      <div class="chart-tile chart-tile-wide"><h3 class="chart-tile-title">Registros por fecha (acumulado)</h3><canvas id="accion-chart-fecha"></canvas></div>
      <div class="chart-tile chart-tile-wide"><h3 class="chart-tile-title">Registros por barrio (top 10)</h3><canvas id="accion-chart-barrio"></canvas></div>
    </div>

    <!-- Map + table side by side, same recipe as Stickers' .eval-workspace
         (evaluaciones.js): the list stops being a second copy of the map and
         becomes a way to read it, and the tab has the width for it. Reuses
         .eval-workspace/-map/-aside/-aside-head as-is (Puntos Solicitados
         already set this precedent for a non-Stickers tab) — only the
         download-icon placement (.accion-aside-actions) is genuinely new. -->
    <section class="card eval-workspace-card" aria-label="Mapa y registros del formulario">
      <div class="card-toolbar">
        <span class="eval-toolbar-title">Puntos revisados (coordenadas del EDE)</span>
        <div class="segmented" role="tablist" aria-label="Colorear por" data-accion-color-group>${colorSegmentedHtml()}</div>
        <!-- Reference-only boundaries, off by default (design.md-style ADR:
             pure orientation context, independent of "Colorear por" and of
             Panel's own choropleth — this map has its own Leaflet instance). -->
        <div class="map-sub-controls">
          <label class="inline-check"><input type="checkbox" data-accion-ref="comuna">Comunas / corregimientos</label>
          <label class="inline-check"><input type="checkbox" data-accion-ref="barrio">Barrios / veredas</label>
        </div>
      </div>
      <div class="eval-workspace">
        <div class="eval-map" id="accion-capa-map"></div>
        <div class="eval-aside">
          <div class="eval-aside-head">
            <h4>Registros del formulario</h4>
            <div class="accion-aside-actions">
              <span class="eval-toolbar-meta">${rows.length} registros · ${cruzados} cruzados con EDE</span>
              <button type="button" class="btn-icon" data-accion-download title="Descargar datos (xlsx)" aria-label="Descargar datos (xlsx)">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12"/><polyline points="7 10 12 15 17 10"/><line x1="5" y1="21" x2="19" y2="21"/></svg>
              </button>
            </div>
          </div>
          <ul class="eval-list" data-accion-list>${accionRowsHtml(rows)}</ul>
        </div>
      </div>
    </section>`;

  sectionEl.querySelector('[data-accion-reload]').addEventListener('click', () => {
    featuresCache = null;
    loadErrored = false;
    loadFormulario(sectionEl);
  });

  // Same mechanism as the Panel's "Descargar datos (xlsx)" (main.js):
  // lazy-loaded SheetJS, a "Descargado el:" stamp row, then the flat export
  // rows below it.
  sectionEl.querySelector('[data-accion-download]').addEventListener('click', async () => {
    let XLSX;
    try { XLSX = await loadXlsx(); } catch { showToast('No se pudo cargar el generador de Excel.', 'error'); return; }
    const exportRows = buildExportRows(rows);
    if (!exportRows.length) {
      showToast('No hay registros para exportar.', 'error');
      return;
    }
    const { legible, slug } = downloadStamp();
    const ws = XLSX.utils.aoa_to_sheet([
      ['Descargado el:', legible],
      [],
    ]);
    XLSX.utils.sheet_add_json(ws, exportRows, { origin: 'A3' });
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'acciones');
    XLSX.writeFile(wb, `acciones_candidatos_demolicion_${slug}.xlsx`);
  });

  sectionEl.querySelector('[data-accion-color-group]').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-accion-color]');
    if (!btn) return;
    colorVar = btn.dataset.accionColor;
    sectionEl.querySelectorAll('[data-accion-color]').forEach((b) => {
      const active = b === btn;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-selected', String(active));
    });
    paintMarkers();
  });

  sectionEl.querySelectorAll('[data-accion-ref]').forEach((input) => {
    input.checked = (input.dataset.accionRef === 'comuna') ? comunaRefOn : barrioRefOn;
    input.addEventListener('change', () => {
      setRefLayerVisible(input.dataset.accionRef, input.checked).catch(() => {
        input.checked = false;
        showToast('No se pudo cargar la capa de referencia.', 'error');
      });
    });
  });

  // Row click -> detail; the PDF button is a sibling (.ps-row-wrap), not a
  // nested button, so no stopPropagation/keydown dance is needed — both are
  // real <button>s and get native keyboard activation for free.
  const list = sectionEl.querySelector('[data-accion-list]');
  list.addEventListener('click', async (e) => {
    const pdfBtn = e.target.closest('[data-accion-pdf]');
    if (pdfBtn) {
      const row = rows[Number(pdfBtn.dataset.accionPdf)];
      pdfBtn.disabled = true;
      pdfBtn.classList.add('is-loading');
      pdfBtn.innerHTML = SPINNER_ICON;
      try {
        await generarInformeCandidato(row.form, row.ede);
      } catch {
        showToast('No se pudo generar el informe PDF.', 'error');
      } finally {
        pdfBtn.disabled = false;
        pdfBtn.classList.remove('is-loading');
        pdfBtn.innerHTML = PDF_ICON;
      }
      return;
    }
    const rowBtn = e.target.closest('[data-accion-row]');
    if (rowBtn) openCandidatoModal(rows[Number(rowBtn.dataset.accionRow)]);
  });

  renderCapaMap(rows);
  renderCharts(rows);
}

/* ------------------------------------------------------------------ */
/* Optional reference basemaps: comuna/corregimiento and barrio/vereda   */
/* boundary outlines — pure orientation context, hidden by default and   */
/* independent of the candidato/colapso/visita "Colorear por" marker     */
/* coloring. Own fetch/cache here rather than reusing mapview.js's        */
/* ensureGeo/setZonasInteresVisible: those are wired to Panel's single    */
/* map singleton (module-scoped `map` in mapview.js), not this tab's own  */
/* Leaflet instance — same static files (data/comunas.geojson,           */
/* data/barrios.geojson), independent cache (the browser's HTTP cache    */
/* still de-dupes the actual network fetch either way).                  */
/* ------------------------------------------------------------------ */

let comunaGeoCache = null;
let barrioGeoCache = null;
let comunaGeoLoad = null;
let barrioGeoLoad = null;
let comunaRefLayer = null;
let barrioRefLayer = null;
let comunaRefOn = false;
let barrioRefOn = false;

async function ensureRefGeo(level) {
  const isComuna = level === 'comuna';
  if (isComuna ? comunaGeoCache : barrioGeoCache) return isComuna ? comunaGeoCache : barrioGeoCache;
  const load = (isComuna ? comunaGeoLoad : barrioGeoLoad) || (async () => {
    const url = isComuna ? 'data/comunas.geojson' : 'data/barrios.geojson';
    const res = await fetch(url);
    if (!res.ok) throw new Error(`No se pudo cargar ${url}`);
    const geo = await res.json();
    if (isComuna) comunaGeoCache = geo; else barrioGeoCache = geo;
  })().catch((err) => {
    if (isComuna) comunaGeoLoad = null; else barrioGeoLoad = null;
    throw err;
  });
  if (isComuna) comunaGeoLoad = load; else barrioGeoLoad = load;
  await load;
  return isComuna ? comunaGeoCache : barrioGeoCache;
}

function buildRefLayer(geo, style) {
  const safeGeo = geo && Array.isArray(geo.features) ? geo : { type: 'FeatureCollection', features: [] };
  return L.geoJSON(safeGeo, {
    style: () => style,
    onEachFeature: (feature, lyr) => {
      const name = feature.properties && feature.properties.name;
      if (name) lyr.bindTooltip(escapeHtml(name), { sticky: true });
    },
  });
}

// Comuna: solid, a touch heavier — the coarser boundary. Barrio: thin
// dashed — the finer one, so both can be on at once without merging
// visually. No fill: this is a reference outline, not a choropleth — it
// must not compete with the candidato_demolicion/colapso marker colors.
const REF_STYLES = {
  comuna: { color: '#38bdf8', weight: 2, fill: false },
  barrio: { color: '#94a3b8', weight: 1, dashArray: '4,3', fill: false },
};

/** Shows/hides a reference layer on THIS tab's map. Idempotent; loaded on
 *  demand the first time it's enabled. bringToBack() keeps it under the
 *  candidato markers/legend regardless of add order. */
async function setRefLayerVisible(level, visible) {
  const isComuna = level === 'comuna';
  if (isComuna) comunaRefOn = !!visible; else barrioRefOn = !!visible;
  if (!visible) {
    const layer = isComuna ? comunaRefLayer : barrioRefLayer;
    if (layer && map && map.hasLayer(layer)) map.removeLayer(layer);
    return;
  }
  const geo = await ensureRefGeo(level);
  let layer = isComuna ? comunaRefLayer : barrioRefLayer;
  if (!layer) {
    layer = buildRefLayer(geo, REF_STYLES[level]);
    if (isComuna) comunaRefLayer = layer; else barrioRefLayer = layer;
  }
  // The checkbox may have been unchecked again while the fetch was in
  // flight — respect whichever state is current, not the one requested.
  if (map && (isComuna ? comunaRefOn : barrioRefOn)) {
    layer.addTo(map);
    layer.bringToBack();
  }
}

/* ------------------------------------------------------------------ */
/* Map                                                                 */
/* ------------------------------------------------------------------ */

let markers = []; // [{ marker, row }] for repainting on color-var change

function renderCapaMap(rows) {
  map = L.map('accion-capa-map', { zoomControl: true, minZoom: 10, maxZoom: 18 }).setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
  // teardownMap() nulls `map` (and drops its layers) on every rebuild — an
  // already-fetched reference layer just needs re-adding, no refetch.
  if (comunaRefOn && comunaRefLayer) { comunaRefLayer.addTo(map); comunaRefLayer.bringToBack(); }
  if (barrioRefOn && barrioRefLayer) { barrioRefLayer.addTo(map); barrioRefLayer.bringToBack(); }

  markers = [];
  const conCoords = rows.filter((r) => r.ede && Number.isFinite(Number(r.ede.y)) && Number.isFinite(Number(r.ede.x)));
  for (const row of conCoords) {
    const marker = L.circleMarker([Number(row.ede.y), Number(row.ede.x)], {
      radius: 8, color: '#0B1D33', weight: 1, fillOpacity: 0.9,
      fillColor: colorFor(colorVar, row.form[colorVar]),
    });
    marker.on('click', () => openCandidatoModal(row));
    marker.bindTooltip(escapeHtml(addressDisplay(row.ede).primary || `Registro ${row.ede.ObjectID}`));
    marker.addTo(map);
    markers.push({ marker, row });
  }

  const legend = L.control({ position: 'bottomright' });
  legend.onAdd = () => {
    const el = L.DomUtil.create('div', 'map-legend');
    L.DomEvent.disableClickPropagation(el);
    el.dataset.accionLegend = '';
    el.innerHTML = legendHtml();
    return el;
  };
  legend.addTo(map);

  if (markers.length) {
    map.fitBounds(L.latLngBounds(markers.map(({ marker }) => marker.getLatLng())), { padding: [40, 40], maxZoom: 16 });
  }
  // The tab/segment may be hidden while this builds; re-measure once painted.
  setTimeout(() => { if (map) map.invalidateSize(); }, 80);
}

function legendHtml() {
  return `
    <div class="legend-title">${escapeHtml(COLOR_VARS[colorVar].label)}</div>
    ${COLOR_VARS[colorVar].entries.map((e) => `
      <div class="legend-row">
        <span class="legend-swatch legend-circle" style="background:${e.color}"></span>
        <span>${escapeHtml(e.label)}</span>
      </div>`).join('')}`;
}

function paintMarkers() {
  for (const { marker, row } of markers) {
    marker.setStyle({ fillColor: colorFor(colorVar, row.form[colorVar]) });
  }
  const legendEl = document.querySelector('[data-accion-legend]');
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

function countBy(rows, fn) {
  const counts = new Map();
  for (const r of rows) {
    const key = fn(r);
    if (key == null) continue;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return counts;
}

function doughnut(id, varName, rows) {
  const counts = countBy(rows, (r) => code(r.form[varName]) || null);
  const entries = COLOR_VARS[varName].entries.filter((e) => counts.get(e.value));
  upsert(id, {
    type: 'doughnut',
    data: {
      labels: entries.map((e) => labelForCode(e.value)),
      datasets: [{
        data: entries.map((e) => counts.get(e.value)),
        backgroundColor: entries.map((e) => e.color),
        borderColor: themeColor('--surface', '#12294a'),
        borderWidth: 2,
      }],
    },
    options: { ...chartOpts({ scales: null, plugins: { legend: { display: true, position: 'bottom', labels: { color: themeColor('--text-secondary', '#b9c4d4'), usePointStyle: true, boxWidth: 8, boxHeight: 8, font: { size: 11 } } } } }), cutout: '55%' },
  });
}

function renderCharts(rows) {
  doughnut('accion-chart-candidato', 'candidato_demolicion', rows);
  doughnut('accion-chart-colapso', 'colapso', rows);

  // Visita as a two-bar chart (spec): same colors as the map variable.
  const visitaCounts = countBy(rows, (r) => code(r.form.visita) || null);
  const visitaEntries = COLOR_VARS.visita.entries;
  upsert('accion-chart-visita', {
    type: 'bar',
    data: {
      labels: visitaEntries.map((e) => labelForCode(e.value)),
      datasets: [{ data: visitaEntries.map((e) => visitaCounts.get(e.value) || 0), backgroundColor: visitaEntries.map((e) => e.color) }],
    },
    options: chartOpts(),
  });

  const barrioCounts = countBy(rows, (r) => (r.ede ? barrioVeredaDisplay(r.ede) : null));
  const topBarrios = [...barrioCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
  upsert('accion-chart-barrio', {
    type: 'bar',
    data: {
      // Distinct blue (not the gold --accent "Registros por fecha" already
      // uses right above it) so the two full-width charts read as separate
      // at a glance. Vertical bars (default indexAxis) — a horizontal layout
      // made sense narrow, but the tile is now full width.
      labels: topBarrios.map(([b]) => b),
      datasets: [{ data: topBarrios.map(([, n]) => n), backgroundColor: '#3b82f6' }],
    },
    options: chartOpts(),
  });

  const dayCounts = countBy(rows, (r) => {
    const ms = r.form.fecha_registro;
    if (!ms) return null;
    const d = new Date(ms);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  });
  const days = [...dayCounts.keys()].sort();
  // Cumulative running total, not daily counts — the field review's overall
  // pace matters more here than any single day's volume.
  let runningTotal = 0;
  const cumulative = days.map((d) => (runningTotal += dayCounts.get(d)));
  upsert('accion-chart-fecha', {
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

/* ------------------------------------------------------------------ */
/* Detail modal (shared #detail-modal shell, own body — table.js's      */
/* openDetailModal for Panel records is untouched)                      */
/* ------------------------------------------------------------------ */

// Same fetch as table.js's private loadPhotos (attachments of the EDE survey
// layer for the JOINED ObjectID), reusing the shared lightbox.
async function loadCapaPhotos(objectId, container) {
  if (!container || objectId == null) return;
  try {
    const res = await fetch(`${SURVEY_LAYER_URL}/${objectId}/attachments?f=json`);
    const json = await res.json();
    const infos = (json.attachmentInfos || []).filter((a) => (a.contentType || '').startsWith('image'));
    const ordered = [...infos.filter((a) => !isFirmaAttachment(a.name)), ...infos.filter((a) => isFirmaAttachment(a.name))];
    if (!ordered.length) {
      container.innerHTML = '<span class="detail-photos-empty">Sin fotos en el survey.</span>';
      return;
    }
    const urls = ordered.map((a) => attachmentUrl(objectId, a.id));
    container.innerHTML = ordered.map((a, i) => `
      <button type="button" class="detail-photo${isFirmaAttachment(a.name) ? ' detail-photo-firma' : ''}" data-idx="${i}" title="Foto de la inspección EDE">
        <img src="${escapeHtml(urls[i])}" alt="Foto de la inspección EDE" loading="lazy">
      </button>`).join('');
    container.querySelectorAll('.detail-photo').forEach((btn) => {
      btn.addEventListener('click', () => openLightbox(urls, Number(btn.dataset.idx)));
    });
  } catch {
    container.innerHTML = '<span class="detail-photos-empty">No se pudieron cargar las fotos.</span>';
  }
}

const field = (label, valueHtml) => `
  <div class="detail-field"><dt>${escapeHtml(label)}</dt><dd>${valueHtml}</dd></div>`;

function openCandidatoModal(row) {
  const { form, ede } = row;
  const modal = document.getElementById('detail-modal');
  const body = modal.querySelector('[data-modal-body]');
  const title = modal.querySelector('[data-modal-title]');
  title.textContent = ede
    ? (addressDisplay(ede).primary || ede.nombre_edificacion || `Registro ${ede.ObjectID}`)
    : `Objetivo ${form.objectivo_id_r ?? '—'}`;

  const edeGroup = ede ? `
    <section class="detail-group">
      <h3>Contexto EDE</h3>
      <dl class="detail-fields">
        ${field('Dirección', escapeHtml(addressDisplay(ede).primary || '—'))}
        ${field('Barrio / vereda', escapeHtml(barrioVeredaDisplay(ede)))}
        ${ede.comuna ? field('Comuna / corregimiento', escapeHtml(ede.comuna)) : ''}
        ${ede.nombre_edificacion ? field('Edificación', escapeHtml(ede.nombre_edificacion)) : ''}
        ${field('Profesional que realizó la evaluación', escapeHtml(ede.nombre_evaluador || 'Sin dato'))}
        ${field('Criterio de habitabilidad', badge('criterio_habitabilidad', ede.criterio_habitabilidad))}
        ${field('Nivel de daño', badge('nivel_dano', ede.nivel_dano))}
        ${field('ObjectID EDE', escapeHtml(String(ede.ObjectID)))}
      </dl>
    </section>` : `
    <section class="detail-group">
      <p class="accion-sin-cruce">Sin cruce EDE: el objetivo «${escapeHtml(form.objectivo_id_r ?? '—')}» no coincide con ningún registro del Panel. Sin mapa ni fotos disponibles.</p>
    </section>`;

  const justificacion = (label, text) => (text ? `
    <section class="detail-group">
      <h3>${escapeHtml(label)}</h3>
      <p class="accion-justificacion">${escapeHtml(text)}</p>
    </section>` : '');

  body.innerHTML = `
    ${ede ? `
    <div class="detail-media">
      <div class="detail-minimap" data-minimap></div>
      <div class="detail-photos" data-photos><span class="detail-photos-empty">Cargando fotos…</span></div>
    </div>` : ''}
    <section class="detail-group">
      <h3>Revisión de campo</h3>
      <dl class="detail-fields">
        ${field('Candidato a demolición', badge('candidato_demolicion', form.candidato_demolicion))}
        ${field('Colapso', badge('colapso', form.colapso))}
        ${field('Requiere visita adicional', badge('visita', form.visita))}
        ${field('Fecha de registro', escapeHtml(formatFecha(form.fecha_registro, { withTime: true })))}
        ${field('Objetivo (referencia)', escapeHtml(form.objectivo_id_r ?? '—'))}
      </dl>
    </section>
    ${edeGroup}
    ${justificacion('Justificación de patología', form.justificacion_patologia)}
    ${justificacion('Justificación de reconfirmación', form.justificacion_reconfirmacion)}
  `;

  if (ede) {
    buildMiniMap(body.querySelector('[data-minimap]'), ede);
    loadCapaPhotos(ede.ObjectID, body.querySelector('[data-photos]'));
  }

  modal.classList.add('is-open');
  modal.setAttribute('aria-hidden', 'false');

  const onClose = () => {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
  };
  modal.querySelector('[data-modal-close]').onclick = onClose;
  modal.querySelector('.modal-backdrop').onclick = onClose;

  // Same .onclick handoff table.js uses, so reopening a Panel record restores
  // its own handler without stacking listeners.
  const reportBtn = modal.querySelector('#detail-report-btn');
  const reportBtnLabel = reportBtn.textContent;
  reportBtn.onclick = async () => {
    reportBtn.disabled = true;
    reportBtn.textContent = 'Generando…';
    try {
      await generarInformeCandidato(form, ede);
    } catch {
      showToast('No se pudo generar el informe PDF.', 'error');
    } finally {
      reportBtn.disabled = false;
      reportBtn.textContent = reportBtnLabel;
    }
  };
}
