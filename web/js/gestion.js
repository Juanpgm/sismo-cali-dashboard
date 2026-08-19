// Gestión view: cross-reference (cruce) of the F3 assignment roster against
// the "Gestor de Zonas — PMU Cali" Apps Script API — which critical points
// already have a field visit done, and a per-zone rollup of what's left.
// Static data comes from cruce_gestor.py: data/cruce_gestor.json +
// data/zonas_asignacion.geojson. The "Actualizar desde gestor" button fetches
// the gestor API directly from the browser to refresh only the live
// zone/dispatch fields (estado, líder, despacho hoy, pendientes) — it never
// recomputes the F3 point matching client-side, that stays from the pipeline.
import { COLORS, escapeHtml, themeColor, basemapTileUrl, formatTs } from './utils.js';

const DATA_URL = 'data/cruce_gestor.json';
const ZONES_URL = 'data/zonas_asignacion.geojson';
const GESTOR_URL = 'https://script.google.com/macros/s/AKfycbw4uRh_HkiZtKgvb1ZkGYt_m5wydNFWw1SkN6D2ttGEaLm_V4cHOJCqg5I581hqevuP/exec';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
const PENDING_STATES = new Set(['PENDIENTE', 'EN_ATENCION']);
const MUTED = '#64748b'; // zones with no puntos críticos asignados

let map = null;
let baseTile = null;
let zonesLayer = null;
let pointsLayer = null;
let legendEl = null;
let data = null;      // cruce_gestor.json (mutated in place by live refresh)
let zonesGeo = null;  // zonas_asignacion.geojson
let initialized = false;

const el = (sel) => document.querySelector(sel);

function ratioColor(ratio) {
  if (ratio === null) return MUTED;
  if (ratio >= 0.8) return themeColor('--status-h', COLORS.status.h);
  if (ratio >= 0.4) return themeColor('--status-r2', '#f97316');
  return themeColor('--status-i3', '#dc2626');
}

function zonaLookup() {
  return new Map(data.zonas.map((z) => [z.zona_id, z]));
}

function zoneRatio(z) {
  return z && z.n_criticos > 0 ? z.n_f3_hechas / z.n_criticos : null;
}

function zoneStyle(z) {
  return {
    color: 'rgba(255,255,255,0.35)',
    weight: 1,
    fillColor: ratioColor(zoneRatio(z)),
    fillOpacity: z && z.n_criticos > 0 ? 0.45 : 0.12,
  };
}

function zoneTooltip(z) {
  if (!z) return '';
  const rows = [
    ['Zona', z.zona_id],
    ['Líder', z.lider || 'sin asignar'],
    ['Estado', z.estado_zona || '—'],
    ['Despacho hoy', z.despacho_hoy ? 'Sí' : 'No'],
    ['F3 hechas / faltantes', `${z.n_f3_hechas} / ${z.n_faltantes}`],
    ['Pendientes gestor', z.gestor_pendientes],
  ];
  return rows.map(([k, v]) => `<strong>${escapeHtml(k)}:</strong> ${escapeHtml(String(v))}`).join('<br>');
}

function renderZonesLayer() {
  if (!zonesLayer || !data) return;
  zonesLayer.clearLayers();
  if (!zonesGeo) return;
  const byId = zonaLookup();
  L.geoJSON(zonesGeo, {
    style: (feature) => zoneStyle(byId.get(feature.properties.zone_id)),
    onEachFeature: (feature, lyr) => {
      const z = byId.get(feature.properties.zone_id);
      lyr.bindTooltip(zoneTooltip(z), { sticky: true });
      lyr.on({
        mouseover: () => lyr.setStyle({ weight: 2, color: COLORS.accent }),
        mouseout: () => lyr.setStyle(zoneStyle(z)),
      });
    },
  }).addTo(zonesLayer);
}

function pointPopup(r) {
  const estado = r.f3_hecha
    ? '<span class="asig-badge asig-badge-done">F3 hecha</span>'
    : '<span class="asig-badge asig-badge-pending">F3 pendiente</span>';
  return `
    <div class="map-popup">
      <h4>${escapeHtml(r.direccion || r.registro_id || 'Sin dirección')}</h4>
      <div class="asig-popup-badge">${estado}</div>
      <dl>
        <dt>Score</dt><dd class="tabular">${escapeHtml(String(r.score))}</dd>
        <dt>Estado visita</dt><dd>${escapeHtml(r.estado_visita || '—')}</dd>
        <dt>Zona</dt><dd>${escapeHtml(r.zona_id || 'fuera de zona')}</dd>
        <dt>Punto gestor</dt><dd>${escapeHtml(r.punto_estado || '—')}</dd>
      </dl>
    </div>
  `;
}

function renderPointsLayer() {
  if (!pointsLayer || !data) return;
  pointsLayer.clearLayers();
  const records = data.records.filter((r) => r.lat != null && r.lon != null
    && !Number.isNaN(Number(r.lat)) && !Number.isNaN(Number(r.lon)));
  for (const r of records) {
    L.circleMarker([Number(r.lat), Number(r.lon)], {
      radius: 6,
      color: '#0B1D33',
      weight: 1,
      fillColor: r.f3_hecha ? themeColor('--status-h', COLORS.status.h) : themeColor('--status-i3', '#dc2626'),
      fillOpacity: 0.9,
    }).bindPopup(pointPopup(r), { maxWidth: 280 }).addTo(pointsLayer);
  }
}

function setLegend() {
  if (!legendEl) return;
  const entries = [
    { label: 'Zona ≥80% F3 hechas', color: ratioColor(1) },
    { label: 'Zona 40–79%', color: ratioColor(0.5) },
    { label: 'Zona <40%', color: ratioColor(0) },
    { label: 'Punto: F3 hecha', color: themeColor('--status-h', COLORS.status.h) },
    { label: 'Punto: F3 pendiente', color: themeColor('--status-i3', '#dc2626') },
  ];
  legendEl.style.display = 'block';
  legendEl.innerHTML = `
    <div class="legend-title">Avance F3 por zona</div>
    ${entries.map((e) => `
      <div class="legend-row">
        <span class="legend-swatch legend-circle" style="background:${e.color}"></span>
        <span>${escapeHtml(e.label)}</span>
      </div>`).join('')}
  `;
}

function renderSummary() {
  if (!data) return;
  const r = data.resumen;
  const pct = r.total_criticos > 0 ? Math.round((r.f3_hechas / r.total_criticos) * 100) : 0;
  el('#gestion-summary').innerHTML = `
    <div class="asig-stat">
      <span class="asig-stat-value tabular">${r.total_criticos}</span>
      <span class="asig-stat-label">Críticos totales</span>
    </div>
    <div class="asig-stat">
      <span class="asig-stat-value tabular" style="color:${themeColor('--status-h', COLORS.status.h)}">${r.f3_hechas}</span>
      <span class="asig-stat-label">Visitas F3 hechas</span>
    </div>
    <div class="asig-stat">
      <span class="asig-stat-value tabular" style="color:${themeColor('--status-i3', '#dc2626')}">${r.f3_faltantes}</span>
      <span class="asig-stat-label">Faltantes</span>
    </div>
    <div class="asig-stat">
      <span class="asig-stat-value tabular">${r.gestor_puntos_pendientes}</span>
      <span class="asig-stat-label">Puntos gestor pendientes</span>
    </div>
    <div class="asig-stat">
      <span class="asig-stat-value tabular">${r.zonas_con_despacho_hoy}</span>
      <span class="asig-stat-label">Zonas con despacho hoy</span>
    </div>
    <div class="asig-progress-wrap">
      <div class="asig-progress-head"><span>Avance F3</span><span class="tabular">${pct}%</span></div>
      <div class="asig-progress"><div class="asig-progress-fill" style="width:${pct}%"></div></div>
      <p class="asig-progress-note">${data.zonas.length} zonas · corte ${escapeHtml(formatTs(data.generated_at))}</p>
    </div>
  `;
}

function renderTable() {
  if (!data) return;
  const rows = [...data.zonas].sort((a, b) => b.n_faltantes - a.n_faltantes).map((z) => {
    const pct = z.n_criticos > 0 ? Math.round((z.n_f3_hechas / z.n_criticos) * 100) : 0;
    return `
      <tr>
        <td>${escapeHtml(z.zona_id)}</td>
        <td>${escapeHtml(z.comuna || '—')}</td>
        <td>${escapeHtml(z.lider || 'sin asignar')}</td>
        <td>${escapeHtml(z.estado_zona || '—')}</td>
        <td>${z.despacho_hoy ? 'Sí' : 'No'}</td>
        <td class="tabular">${z.n_criticos}</td>
        <td class="tabular">${z.n_f3_hechas}</td>
        <td class="tabular">${z.n_faltantes}</td>
        <td class="tabular">${z.gestor_pendientes}</td>
        <td>
          <div class="gestion-progress-cell">
            <div class="asig-progress"><div class="asig-progress-fill" style="width:${pct}%"></div></div>
            <span class="tabular gestion-progress-pct">${pct}%</span>
          </div>
        </td>
      </tr>`;
  }).join('');
  el('#gestion-table').innerHTML = `
    <table>
      <thead><tr>
        <th>Zona</th><th>Comuna</th><th>Líder</th><th>Estado zona</th><th>Despacho hoy</th>
        <th>Críticos</th><th>F3 hechas</th><th>Faltantes</th><th>Pend. gestor</th><th>Avance</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function setGestionError(message) {
  const bar = el('#gestion-error');
  if (!bar) return;
  bar.textContent = message;
  bar.hidden = !message;
}

/** GET url as JSON, retrying on a non-OK response or a JSON parse failure
 *  (the Apps Script occasionally answers HTTP 200 with an HTML wrapper). */
async function fetchJsonRetry(url, tries = 3) {
  let lastErr;
  for (let attempt = 0; attempt < tries; attempt += 1) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      lastErr = err;
      if (attempt < tries - 1) await new Promise((r) => setTimeout(r, 1000));
    }
  }
  throw lastErr;
}

/** Merge live gestor fields (estado, líder, despacho hoy, pendientes) into the
 *  static rollup, without touching the F3 point matching. Rows for records
 *  with no matching gestor zone (zona_id "(fuera de zona)") are synthetic —
 *  skip them, the gestor has nothing to report for them. */
function applyLiveUpdate(zonasLive, despachosLive, puntosLive) {
  const despachoZonas = new Set(despachosLive.map((d) => d.ZONA_ID).filter(Boolean));
  const puntosPendByZona = new Map();
  let gestorPendTotal = 0;
  for (const p of puntosLive) {
    if (!PENDING_STATES.has(String(p.ESTADO || '').toUpperCase())) continue;
    gestorPendTotal += 1;
    if (p.ZONA_ID) puntosPendByZona.set(p.ZONA_ID, (puntosPendByZona.get(p.ZONA_ID) || 0) + 1);
  }
  const zonasLiveById = new Map(zonasLive.map((z) => [z.ZONA_ID, z]));

  for (const z of data.zonas) {
    if (z.zona_id.startsWith('(')) continue; // synthetic "fuera de zona" row
    const live = zonasLiveById.get(z.zona_id);
    if (live) {
      z.estado_zona = live.ESTADO_ACTUAL;
      z.lider = live.LIDER_ACTUAL;
    }
    z.despacho_hoy = despachoZonas.has(z.zona_id);
    z.gestor_pendientes = puntosPendByZona.get(z.zona_id) || 0;
  }
  data.resumen.zonas_con_despacho_hoy = despachoZonas.size;
  data.resumen.gestor_puntos_pendientes = gestorPendTotal; // same definition as cruce_gestor.py: all pending puntos

  renderSummary();
  renderTable();
  renderZonesLayer();
}

async function refreshFromGestor() {
  const btn = el('#gestion-refresh');
  btn.disabled = true;
  btn.classList.add('is-loading');
  try {
    const bust = `&t=${Date.now()}`;
    const [zonasLive, despachosLive, puntosLive] = await Promise.all([
      fetchJsonRetry(`${GESTOR_URL}?api=zonas${bust}`),
      fetchJsonRetry(`${GESTOR_URL}?api=despachosHoy${bust}`),
      fetchJsonRetry(`${GESTOR_URL}?api=puntos${bust}`),
    ]);
    applyLiveUpdate(zonasLive, despachosLive, puntosLive);
    el('#gestion-updated').textContent = `Actualizado desde gestor: ${new Date().toLocaleTimeString('es-CO')}`;
    setGestionError('');
  } catch (err) {
    console.error(err);
    setGestionError('No se pudo consultar el gestor.');
  } finally {
    btn.disabled = false;
    btn.classList.remove('is-loading');
  }
}

export function invalidateGestionSize() {
  if (map) setTimeout(() => map.invalidateSize(), 60);
}

/** Swap the gestión-map base tiles to match the active theme. No-op until the
 *  view has been opened (map built lazily). Layers/legend need `data` to
 *  render — skip them (tiles still swap) when a load failed. */
export function applyGestionTheme() {
  if (!map) return;
  if (baseTile) map.removeLayer(baseTile);
  baseTile = L.tileLayer(basemapTileUrl(), {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd', maxZoom: 20,
  }).addTo(map);
  baseTile.bringToBack();
  if (!data) return;
  renderZonesLayer();
  renderPointsLayer();
  setLegend();
}

/** Build the map/layers/listener once; safe to call again (no-op past the
 *  first time), independent of whether the data load ever succeeds. */
function ensureMap() {
  if (map) return;
  map = L.map('gestion-map', { zoomControl: true, minZoom: 10, maxZoom: 18 }).setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd', maxZoom: 20,
  }).addTo(map);
  zonesLayer = L.layerGroup().addTo(map);
  pointsLayer = L.layerGroup().addTo(map);

  const legendControl = L.control({ position: 'bottomright' });
  legendControl.onAdd = () => {
    legendEl = L.DomUtil.create('div', 'map-legend');
    L.DomEvent.disableClickPropagation(legendEl);
    return legendEl;
  };
  legendControl.addTo(map);

  el('#gestion-refresh').addEventListener('click', refreshFromGestor);
}

/** Lazy init: build the map once, (re)load data on every entry until it
 *  succeeds. `initialized` only flips on a successful load, so a transient
 *  fetch failure doesn't permanently lock the tab in the error state. */
export async function initGestion() {
  if (initialized) { invalidateGestionSize(); return; }
  ensureMap();

  try {
    const bust = `?t=${Date.now()}`;
    const [cruce, zones] = await Promise.all([
      fetch(DATA_URL + bust, { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('cruce_gestor.json'); return r.json(); }),
      fetch(ZONES_URL + bust, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)),
    ]);
    data = cruce;
    zonesGeo = zones;
    initialized = true;
    renderSummary();
    renderZonesLayer();
    renderPointsLayer();
    setLegend();
    renderTable();
    invalidateGestionSize();
  } catch (err) {
    console.error(err);
    el('#gestion-summary').innerHTML = '<p class="asig-error">No se pudo cargar la gestión. Corré <code>cruce_gestor.py</code> para generar los datos.</p>';
  }
}
