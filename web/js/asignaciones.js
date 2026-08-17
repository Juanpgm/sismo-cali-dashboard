// Assignments view: a simpler operational map of the points crews must visit.
// Shows the KML assignment zones, the prioritized pending points and the ones
// already visited (F3 done), a progress summary, and a CSV download of the
// full roster. Data comes from the static artifacts asignar_f3.py exports:
//   data/asignaciones.json  · data/zonas_asignacion.geojson
import { COLORS, escapeHtml, interpolateRamp, basemapTileUrl } from './utils.js';

const ASIG_URL = 'data/asignaciones.json';
const ZONES_URL = 'data/zonas_asignacion.geojson';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;

// Pending points ramp by priority score: yellow (lower) -> red (higher/urgent).
const PRIO_RAMP = ['#eab308', '#f97316', '#dc2626'];
const VISITED_COLOR = COLORS.status.h; // green — already visited

let map = null;
let baseTile = null;
let zonesLayer = null;
let pointsLayer = null;
let legendEl = null;
let data = null;
let initialized = false;

const el = (sel) => document.querySelector(sel);

/** Min/max pending score, to normalize the priority color ramp. */
function pendingScoreBounds(records) {
  let min = Infinity;
  let max = -Infinity;
  for (const r of records) {
    if (r.estado_visita !== 'pendiente') continue;
    const s = Number(r.score);
    if (Number.isNaN(s)) continue;
    if (s < min) min = s;
    if (s > max) max = s;
  }
  if (min === Infinity) return { min: 0, max: 0 };
  return { min, max };
}

function pointColor(r, bounds) {
  if (r.estado_visita === 'visitado') return VISITED_COLOR;
  const span = bounds.max - bounds.min;
  const t = span > 0 ? (Number(r.score) - bounds.min) / span : 0.5;
  return interpolateRamp(PRIO_RAMP, t);
}

function zoneStyle(feature) {
  const ola = String(feature.properties.ola || '');
  return {
    color: 'rgba(255,255,255,0.30)',
    weight: 1,
    fillColor: ola === '1' ? COLORS.accent : COLORS.status.r2,
    fillOpacity: 0.06,
  };
}

function zoneTooltip(feature) {
  const p = feature.properties;
  const rows = [
    ['Zona', p.zone_id],
    ['Ola', p.ola],
    ['Despacho', p.despacho],
    ['Comuna', p.comuna],
    ['Barrios', p.barrios],
  ].filter(([, v]) => v);
  return rows.map(([k, v]) => `<strong>${escapeHtml(k)}:</strong> ${escapeHtml(String(v))}`).join('<br>');
}

function pointPopup(r) {
  const estado = r.estado_visita === 'visitado'
    ? '<span class="asig-badge asig-badge-done">Visitado</span>'
    : `<span class="asig-badge asig-badge-pending">Pendiente · prioridad ${escapeHtml(String(r.prioridad))}</span>`;
  const victimas = (Number(r.n_fallecidos) || 0) + (Number(r.n_atrapamientos) || 0) + (Number(r.n_rescatados) || 0);
  return `
    <div class="map-popup">
      <h4>${escapeHtml(r.direccion || r.id_asignacion || 'Sin dirección')}</h4>
      <div class="asig-popup-badge">${estado}</div>
      <dl>
        <dt>Código</dt><dd class="tabular">${escapeHtml(r.id_asignacion)}</dd>
        <dt>Score</dt><dd class="tabular">${escapeHtml(String(r.score))}</dd>
        <dt>Barrio / comuna</dt><dd>${escapeHtml(r.barrio_vereda || '—')} · ${escapeHtml(r.comuna_corregimiento || '—')}</dd>
        <dt>Zona · ola</dt><dd>${escapeHtml(r.zona_id || 'fuera de zona')} · ${escapeHtml(r.ola || '—')}</dd>
        <dt>Severidad (grafo)</dt><dd class="tabular">${escapeHtml(String(r.grafo_severidad))}</dd>
        <dt>Fallecidos / atrap. / rescat.</dt><dd class="tabular">${Number(r.n_fallecidos) || 0} / ${Number(r.n_atrapamientos) || 0} / ${Number(r.n_rescatados) || 0}</dd>
        <dt>Nivel de riesgo</dt><dd>${escapeHtml(r.nivel_riesgo || '—')}</dd>
      </dl>
    </div>
  `;
}

function renderMap() {
  const records = data.records.filter((r) => r.lat != null && r.lon != null
    && !Number.isNaN(Number(r.lat)) && !Number.isNaN(Number(r.lon)));
  const bounds = pendingScoreBounds(records);

  zonesLayer.clearLayers();
  if (data.zones) {
    L.geoJSON(data.zones, {
      style: zoneStyle,
      onEachFeature: (feature, lyr) => {
        lyr.bindTooltip(zoneTooltip(feature), { sticky: true });
        lyr.on({
          mouseover: () => lyr.setStyle({ weight: 2, color: COLORS.accent, fillOpacity: 0.14 }),
          mouseout: () => lyr.setStyle(zoneStyle(feature)),
        });
      },
    }).addTo(zonesLayer);
  }

  pointsLayer.clearLayers();
  // Draw pending last so urgent points sit on top of visited ones.
  const ordered = [...records].sort((a, b) => (a.estado_visita === 'visitado' ? -1 : 1) - (b.estado_visita === 'visitado' ? -1 : 1));
  for (const r of ordered) {
    const visited = r.estado_visita === 'visitado';
    L.circleMarker([Number(r.lat), Number(r.lon)], {
      radius: visited ? 5 : 7,
      color: '#0B1D33',
      weight: 1,
      fillColor: pointColor(r, bounds),
      fillOpacity: visited ? 0.7 : 0.95,
    }).bindPopup(pointPopup(r), { maxWidth: 300 }).addTo(pointsLayer);
  }

  setLegend(bounds);
}

function setLegend(bounds) {
  if (!legendEl) return;
  const entries = [
    { label: 'Visitado', color: VISITED_COLOR },
    { label: `Pendiente · menor prioridad (${bounds.min})`, color: interpolateRamp(PRIO_RAMP, 0) },
    { label: 'Pendiente · media', color: interpolateRamp(PRIO_RAMP, 0.5) },
    { label: `Pendiente · mayor prioridad (${bounds.max})`, color: interpolateRamp(PRIO_RAMP, 1) },
  ];
  legendEl.style.display = 'block';
  legendEl.innerHTML = `
    <div class="legend-title">Estado de la visita</div>
    ${entries.map((e) => `
      <div class="legend-row">
        <span class="legend-swatch legend-circle" style="background:${e.color}"></span>
        <span>${escapeHtml(e.label)}</span>
      </div>`).join('')}
  `;
}

function renderSummary() {
  const total = data.records.length;
  const visitados = data.visitados ?? data.records.filter((r) => r.estado_visita === 'visitado').length;
  const pendientes = data.pendientes ?? (total - visitados);
  const pct = total > 0 ? Math.round((visitados / total) * 100) : 0;
  el('#asig-summary').innerHTML = `
    <div class="asig-stat">
      <span class="asig-stat-value tabular">${total}</span>
      <span class="asig-stat-label">Puntos asignados</span>
    </div>
    <div class="asig-stat">
      <span class="asig-stat-value tabular" style="color:${COLORS.status.r2}">${pendientes}</span>
      <span class="asig-stat-label">Pendientes</span>
    </div>
    <div class="asig-stat">
      <span class="asig-stat-value tabular" style="color:${VISITED_COLOR}">${visitados}</span>
      <span class="asig-stat-label">Visitados</span>
    </div>
    <div class="asig-progress-wrap">
      <div class="asig-progress-head"><span>Avance</span><span class="tabular">${pct}%</span></div>
      <div class="asig-progress"><div class="asig-progress-fill" style="width:${pct}%"></div></div>
      <p class="asig-progress-note">${data.zonas ?? '—'} zonas de asignación · corte ${escapeHtml(formatTs(data.generated_at))}</p>
    </div>
  `;
}

function formatTs(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso)
    : d.toLocaleString('es-CO', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

const TABLE_COLS = [
  ['prioridad', 'Prio'], ['estado_visita', 'Estado'], ['id_asignacion', 'Código'],
  ['score', 'Score'], ['direccion', 'Dirección'], ['barrio_vereda', 'Barrio'],
  ['comuna_corregimiento', 'Comuna'], ['zona_id', 'Zona'], ['nivel_riesgo', 'Riesgo'],
  ['grafo_severidad', 'Sev.'], ['n_fallecidos', 'Fall.'], ['n_atrapamientos', 'Atrap.'],
];

function renderTable() {
  const head = TABLE_COLS.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join('');
  const rows = data.records.map((r) => {
    const cells = TABLE_COLS.map(([key]) => {
      const v = r[key] == null ? '' : String(r[key]);
      const cls = ['prioridad', 'score', 'id_asignacion', 'grafo_severidad', 'n_fallecidos', 'n_atrapamientos'].includes(key) ? ' class="tabular"' : '';
      return `<td${cls}>${escapeHtml(v)}</td>`;
    }).join('');
    const stateClass = r.estado_visita === 'visitado' ? 'asig-row-done' : 'asig-row-pending';
    return `<tr class="${stateClass}">${cells}</tr>`;
  }).join('');
  el('#asig-table').innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}

/** Download the static .xlsx that asignar_f3.py exports alongside the JSON. */
function downloadXlsx() {
  const a = document.createElement('a');
  a.href = `data/asignaciones.xlsx?t=${Date.now()}`;
  a.download = `asignaciones_${(data?.generated_at || '').slice(0, 10) || 'export'}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function invalidateAsigSize() {
  if (map) setTimeout(() => map.invalidateSize(), 60);
}

/** Swap the assignments-map base tiles to match the active theme. No-op until
 *  the view has been opened (map built lazily). */
export function applyAsigTheme() {
  if (!map) return;
  if (baseTile) map.removeLayer(baseTile);
  baseTile = L.tileLayer(basemapTileUrl(), {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd', maxZoom: 20,
  }).addTo(map);
  baseTile.bringToBack();
}

/** Lazy one-time init: build the map, load data, render everything. */
export async function initAsignaciones() {
  if (initialized) { invalidateAsigSize(); return; }
  initialized = true;

  map = L.map('asig-map', { zoomControl: true, minZoom: 10, maxZoom: 18 }).setView(CALI_CENTER, CALI_ZOOM);
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

  el('#asig-download').addEventListener('click', downloadXlsx);

  try {
    const bust = `?t=${Date.now()}`;
    const [asig, zones] = await Promise.all([
      fetch(ASIG_URL + bust, { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('asignaciones.json'); return r.json(); }),
      fetch(ZONES_URL + bust, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)),
    ]);
    data = asig;
    data.zones = zones;
    renderSummary();
    renderMap();
    renderTable();
    invalidateAsigSize();
  } catch (err) {
    console.error(err);
    el('#asig-summary').innerHTML = '<p class="asig-error">No se pudieron cargar las asignaciones. Corré <code>asignar_f3.py</code> para generar los datos.</p>';
  }
}
