// "Puntos Solicitados" tab — admin-only registration of special-case points
// (citizen requests, follow-ups, priority cases) that flow through the SAME
// grupo/cuadrilla/inspector assignment machinery as any pipeline point
// (`puntos-solicitados` change: proposal.md, design.md ADR-1..ADR-6).
//
// Structurally cloned from evaluaciones.js (KPIs + filters + Leaflet map +
// card list + detail modal), plus a "Crear punto solicitado" modal — this
// tab is the only place that WRITES (`backend/app/routers/puntos_solicitados.py`:
// POST/PATCH/DELETE/geocode), unlike evaluaciones.js which is read-only.
//
// Data shape (GET /puntos-solicitados, design.md "Interfaces / Contracts"):
// { id, nombre, comuna_corregimiento, barrio_vereda, nombre_solicitante,
//   telefono_solicitante, justificacion, direccion, coords:{lat,lon}, fotos[],
//   clave_integracion, estado_seguimiento, creado_por, creado_en }
// `estado_seguimiento` in the response is ALREADY the ADR-4-derived value —
// this tab never re-derives it from a mirror, it just classifies/sorts what
// the router returns.
//
// No geo-resolution needed for comuna/barrio filters (unlike evaluaciones.js):
// comuna_corregimiento/barrio_vereda are typed admin fields on this record,
// not resolved from a basemap polygon.
import {
  COLORS, escapeHtml, basemapTileUrl, normalize, showToast, mountCombobox,
} from './utils.js';
import { buildMiniMap } from './mapview.js';
import { openLightbox } from './table.js';
import { apiUrl } from './api-config.js';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
const CALI_BBOX = { latMin: 3.30, latMax: 3.55, lngMin: -76.60, lngMax: -76.40 };

const MAX_FOTOS = 10;

// ---- Estado classification (ADR-4's derived lifecycle) --------------------
//
// Own palette on purpose (design.md/tasks.md: "distinct from existing pill
// scheme") — NOT evaluaciones.js's ATC-20 ramp, NOT planeacion.js's 5-color
// marker scheme; a punto solicitado's estado is its own small state machine.
export const ESTADOS = [
  { key: 'pendiente', label: 'pendiente', color: COLORS.unknown },
  { key: 'asignado', label: 'asignado', color: COLORS.categorical[0] },
  { key: 'en_proceso', label: 'en proceso', color: COLORS.status.r2 },
  { key: 'visitado', label: 'visitado', color: COLORS.status.h },
  { key: 'excluido', label: 'excluido', color: COLORS.categoricalOther },
];
const ESTADO_BY_KEY = new Map(ESTADOS.map((e) => [e.key, e]));
const ESTADO_DEFAULT = ESTADOS[0];

/** Estado tile of a punto solicitado, tolerant of spacing/case drift and
 *  falling back to 'pendiente' for anything unrecognised — never a crash,
 *  never a silent 6th state. */
export function estadoDe(punto) {
  const raw = String((punto && punto.estado_seguimiento) || '').trim().toLowerCase();
  return ESTADO_BY_KEY.get(raw) || ESTADO_DEFAULT;
}

/** Count per estado, keyed by ESTADOS[].key — always all 5 keys present. */
export function contarPorEstado(puntos) {
  const counts = Object.fromEntries(ESTADOS.map((e) => [e.key, 0]));
  for (const p of puntos) counts[estadoDe(p).key] += 1;
  return counts;
}

/** Filtered view of `list`: estado, comuna/barrio (flat typed fields, no geo
 *  resolution needed) and a free-text search across the fields an admin
 *  would actually search by. Exported so the pure self-check can exercise it
 *  without the DOM. */
export function applyFilters(list, filters) {
  const q = filters.search ? normalize(filters.search) : '';
  return list.filter((p) => {
    if (filters.estado && estadoDe(p).key !== filters.estado) return false;
    if (filters.comuna && p.comuna_corregimiento !== filters.comuna) return false;
    if (filters.barrio && p.barrio_vereda !== filters.barrio) return false;
    if (q) {
      const hay = normalize([
        p.nombre, p.direccion, p.nombre_solicitante, p.barrio_vereda,
        p.comuna_corregimiento, p.clave_integracion,
      ].filter(Boolean).join(' '));
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

/** Pure removal: returns a NEW array with the file at `index` dropped,
 *  never mutating `files`. Extracted so the create modal's "quitar" handler
 *  stays a one-liner and this specific behaviour (order preservation, first/
 *  last/only-item removal, non-mutation) is unit-testable without the DOM. */
export function removeFotoAt(files, index) {
  const copy = files.slice();
  copy.splice(index, 1);
  return copy;
}

/** Newest creado_en first — same "most recent first" convention as
 *  evaluaciones.js's list. Missing/unparseable dates sort last, never throw. */
export function sortPuntos(list) {
  const time = (p) => {
    const t = p && p.creado_en ? new Date(p.creado_en).getTime() : NaN;
    return Number.isNaN(t) ? -Infinity : t;
  };
  return [...list].sort((a, b) => time(b) - time(a));
}

// ---- Filter chips / selects (same shape as evaluaciones.js) ---------------

function estadoChipsHtml(activeKey) {
  const chip = (value, label, active) => `<button type="button" class="asignacion-chip${active ? ' is-active' : ''}" data-filter-group="estado" data-filter-value="${value}">${escapeHtml(label)}</button>`;
  return [chip('', 'Todos', !activeKey), ...ESTADOS.map((e) => chip(e.key, e.label, activeKey === e.key))].join('');
}

function comunaBarrioMap(list) {
  const map = new Map();
  for (const p of list) {
    if (!p.comuna_corregimiento) continue;
    if (!map.has(p.comuna_corregimiento)) map.set(p.comuna_corregimiento, new Set());
    if (p.barrio_vereda) map.get(p.comuna_corregimiento).add(p.barrio_vereda);
  }
  return map;
}

function renderComunaSelect(selectEl, comunaMap) {
  const comunas = [...comunaMap.keys()].sort();
  const prev = selectEl.value;
  selectEl.innerHTML = '<option value="">— Todas las comunas —</option>'
    + comunas.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  selectEl.value = comunas.includes(prev) ? prev : '';
}

function renderBarrioSelect(selectEl, comunaMap, comuna) {
  const barrios = comuna ? [...(comunaMap.get(comuna) || [])].sort() : [];
  const prev = selectEl.value;
  selectEl.innerHTML = '<option value="">— Todos los barrios —</option>'
    + barrios.map((b) => `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`).join('');
  selectEl.disabled = !comuna;
  selectEl.value = barrios.includes(prev) ? prev : '';
}

// ---- KPIs -------------------------------------------------------------------
// Same shape as evaluaciones.js's own kpisHtml/barHtml (% sub-row, neutral
// total tile with a subtitle, one-line distribution strip) — tab-level parity
// item from the puntos-solicitados-ux-polish change.

const pct = (part, total) => (total ? Math.round((part / total) * 1000) / 10 : 0);

function kpisHtml(puntos) {
  const total = puntos.length;
  const counts = contarPorEstado(puntos);
  const tiles = ESTADOS.map((e) => `
    <div class="kpi-tile" style="--kpi-accent:${e.color}">
      <span class="kpi-label kpi-label-lower">${e.label}</span>
      <span class="kpi-value">${counts[e.key]}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">${pct(counts[e.key], total)}% del total</span></div>
    </div>`).join('');
  return `
    <div class="kpi-tile is-neutral">
      <span class="kpi-label kpi-label-lower">puntos solicitados</span>
      <span class="kpi-value">${total}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">registros creados manualmente por el equipo administrador</span></div>
    </div>
    ${tiles}`;
}

/** One-line proportion strip under the tiles — same `.eval-bar`/`.hab-bar`
 *  markup evaluaciones.js's own barHtml builds, segments in the same order
 *  and colour as the tiles right above. */
function barHtml(puntos) {
  const total = puntos.length;
  if (!total) return '';
  const counts = contarPorEstado(puntos);
  const segs = ESTADOS.map((e) => {
    const share = pct(counts[e.key], total);
    if (share <= 0) return '';
    return `<div class="hab-bar-seg" style="width:${share}%;background:${e.color}" title="${escapeHtml(e.label)}: ${counts[e.key]}"></div>`;
  }).join('');
  return `
    <span class="eval-bar-label">distribución</span>
    <div class="hab-bar">${segs}</div>`;
}

// ---- Static markup ------------------------------------------------------------

export function sectionHtml() {
  return `
    <section class="eval-section" aria-label="Puntos Solicitados">
      <div class="section-bar">
        <h3 class="section-bar-title">Puntos Solicitados</h3>
        <button type="button" class="sticker-action" id="ps-reload">Actualizar</button>
        <button type="button" class="btn-primary" id="ps-crear">Crear punto solicitado</button>
      </div>

      <div class="kpi-row eval-kpis" id="ps-kpis"></div>
      <div class="eval-bar" id="ps-bar"></div>

      <div class="eval-filters" id="ps-filters">
        <div class="asignacion-search">
          <input type="search" id="ps-search" class="sticker-search-input"
            placeholder="Buscar por nombre, dirección o solicitante…" aria-label="Buscar puntos solicitados">
        </div>
        <div class="card-toolbar asignacion-filters">
          <div class="asignacion-filters-group" id="ps-estado-chips">${estadoChipsHtml('')}</div>
          <label class="sticker-field asignacion-inline-field">
            <span>Comuna</span>
            <select id="ps-comuna-select" aria-label="Filtrar por comuna"><option value="">— Todas las comunas —</option></select>
          </label>
          <label class="sticker-field asignacion-inline-field">
            <span>Barrio</span>
            <select id="ps-barrio-select" aria-label="Filtrar por barrio" disabled><option value="">— Todos los barrios —</option></select>
          </label>
        </div>
      </div>

      <div class="card eval-workspace-card">
        <div class="card-toolbar">
          <span class="eval-toolbar-title">Puntos solicitados</span>
          <span class="eval-toolbar-meta" id="ps-map-meta"></span>
        </div>
        <div class="eval-workspace">
          <div class="eval-map" id="ps-map"></div>
          <div class="eval-aside">
            <div class="eval-aside-head">
              <h4>Registros</h4>
              <span class="eval-toolbar-meta" id="ps-list-meta"></span>
            </div>
            <ul class="eval-list" id="ps-list"></ul>
          </div>
        </div>
      </div>

      <!-- Detail modal -->
      <div class="modal" id="ps-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="ps-modal-title">
        <div class="modal-backdrop" data-ps-close></div>
        <div class="modal-panel">
          <div class="modal-header">
            <h2 id="ps-modal-title">Detalle del punto solicitado</h2>
            <button type="button" class="btn-icon" data-ps-close aria-label="Cerrar">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
            </button>
          </div>
          <div class="modal-body" id="ps-modal-body"></div>
        </div>
      </div>

      <!-- Create modal -->
      <div class="modal" id="ps-crear-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="ps-crear-title">
        <div class="modal-backdrop" data-ps-crear-close></div>
        <div class="modal-panel sticker-modal-panel">
          <div class="modal-header">
            <h2 id="ps-crear-title">Crear punto solicitado</h2>
            <button type="button" class="btn-icon" data-ps-crear-close aria-label="Cerrar">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
            </button>
          </div>
          <div class="modal-body">
            <form id="ps-crear-form" class="sticker-form">
              <div class="sticker-form-section">
                <h4 class="sticker-form-section-title">Punto</h4>
                <label class="sticker-field"><span>Nombre *</span>
                  <input name="nombre" required placeholder="Casa esquinera" autocomplete="off">
                </label>
                <!-- Full-width, one per row (not inside .sticker-form-grid): the
                     comuna/barrio combos share .asignacion-combo's CSS with the
                     wide inspector comboboxes (stickers-asignacion.js/
                     planeacion.js) — squeezing them into a 233px 2-col grid
                     cell let the open dropdown bleed onto the "Nombre del
                     solicitante" field directly below in the same column. -->
                <label class="sticker-field"><span>Comuna / corregimiento *</span>
                  <div class="asignacion-combo">
                    <input type="text" name="comuna_corregimiento" id="ps-comuna-input" class="asignacion-combo-input"
                      role="combobox" aria-expanded="false" aria-autocomplete="list" autocomplete="off" spellcheck="false"
                      required placeholder="Buscar comuna o corregimiento…" aria-label="Buscar comuna o corregimiento">
                    <ul class="asignacion-combo-list" id="ps-comuna-list" role="listbox" hidden></ul>
                  </div>
                </label>
                <label class="sticker-field"><span>Barrio / vereda *</span>
                  <div class="asignacion-combo">
                    <input type="text" name="barrio_vereda" id="ps-barrio-input" class="asignacion-combo-input"
                      role="combobox" aria-expanded="false" aria-autocomplete="list" autocomplete="off" spellcheck="false"
                      required disabled placeholder="Elegí una comuna primero…" aria-label="Buscar barrio o vereda">
                    <ul class="asignacion-combo-list" id="ps-barrio-list" role="listbox" hidden></ul>
                  </div>
                </label>
              </div>

              <div class="sticker-form-section">
                <h4 class="sticker-form-section-title">Solicitante</h4>
                <div class="sticker-form-grid">
                  <label class="sticker-field"><span>Nombre del solicitante *</span>
                    <input name="nombre_solicitante" required placeholder="María Pérez" autocomplete="off">
                  </label>
                  <label class="sticker-field"><span>Teléfono del solicitante *</span>
                    <input name="telefono_solicitante" required placeholder="3001234567" autocomplete="off">
                  </label>
                </div>
                <label class="sticker-field"><span>Justificación *</span>
                  <textarea name="justificacion" required rows="3" placeholder="Motivo de la solicitud"></textarea>
                </label>
              </div>

              <div class="sticker-form-section">
                <h4 class="sticker-form-section-title">Ubicación</h4>
                <div class="sticker-field">
                  <span>Dirección</span>
                  <div class="ps-geocode-row">
                    <input name="direccion" id="ps-direccion" placeholder="Calle 5 # 10-20" autocomplete="off">
                    <button type="button" class="btn-secondary" id="ps-geocode-btn">Ubicar</button>
                  </div>
                  <p class="sticker-note" id="ps-geocode-note">Ubicá por dirección o arrastrá el marcador / ingresá lat/lng manualmente.</p>
                </div>

                <div class="ps-coords-map" id="ps-coords-map"></div>
                <div class="ps-coords-inline">
                  <label class="sticker-field ps-coords-field"><span>Latitud *</span>
                    <input name="lat" id="ps-lat" type="number" step="any" required>
                  </label>
                  <label class="sticker-field ps-coords-field"><span>Longitud *</span>
                    <input name="lng" id="ps-lng" type="number" step="any" required>
                  </label>
                </div>
              </div>

              <div class="sticker-form-section">
                <h4 class="sticker-form-section-title">Fotos</h4>
                <div class="sticker-field">
                  <span>Fotos (hasta ${MAX_FOTOS})</span>
                  <div class="ps-fotos-picker">
                    <input type="file" id="ps-fotos-input" class="ps-fotos-input-native" accept="image/*" multiple>
                    <label for="ps-fotos-input" class="btn-secondary">Elegir fotos</label>
                    <span class="ps-fotos-caption" id="ps-fotos-caption">Ningún archivo seleccionado</span>
                  </div>
                </div>
                <div class="ps-fotos-preview" id="ps-fotos-preview"></div>
              </div>

              <p class="sticker-error" id="ps-crear-error" role="alert" hidden></p>
              <div class="sticker-form-actions">
                <button type="button" class="btn-secondary" data-ps-crear-close>Cancelar</button>
                <button type="submit" class="btn-primary" id="ps-crear-submit">Crear punto</button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </section>`;
}

// ---- Main workspace map (list + markers) -------------------------------------

let map = null;
let baseTile = null;
let pointsLayer = null;
let legendEl = null;
let lastFitBounds = null;
let markerById = new Map();

function teardownMap() {
  if (map) { map.remove(); map = null; }
  baseTile = null;
  pointsLayer = null;
  legendEl = null;
  markerById = new Map();
}

if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => {
    if (!map || !baseTile) return;
    map.removeLayer(baseTile);
    baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
    baseTile.bringToBack();
  });
}

function popupHtml(p) {
  const e = estadoDe(p);
  return `
    <div class="map-popup">
      <h4>${escapeHtml(p.nombre || 'Sin nombre')}</h4>
      <dl>
        <dt>Estado</dt><dd>${escapeHtml(e.label)}</dd>
        <dt>Dirección</dt><dd>${escapeHtml(p.direccion || 'Sin dato')}</dd>
        <dt>Comuna</dt><dd>${escapeHtml(p.comuna_corregimiento || 'Sin dato')}</dd>
        <dt>Barrio</dt><dd>${escapeHtml(p.barrio_vereda || 'Sin dato')}</dd>
      </dl>
      <button type="button" class="btn-link" data-ps-detail="${escapeHtml(p.id)}">Ver detalle &rarr;</button>
    </div>`;
}

function renderMap(puntos, onDetail) {
  teardownMap();
  const conCoords = puntos.filter((p) => p.coords && Number.isFinite(p.coords.lat) && Number.isFinite(p.coords.lon));

  map = L.map('ps-map', { zoomControl: true, minZoom: 10, maxZoom: 18 }).setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
  pointsLayer = L.layerGroup().addTo(map);

  for (const p of conCoords) {
    const marker = L.circleMarker([p.coords.lat, p.coords.lon], {
      radius: 8, color: '#0B1D33', weight: 1, fillColor: estadoDe(p).color, fillOpacity: 0.9,
    });
    marker.bindPopup(popupHtml(p), { maxWidth: 280 });
    marker.on('popupopen', (ev) => {
      const btn = ev.popup.getElement().querySelector('[data-ps-detail]');
      if (btn) btn.addEventListener('click', () => onDetail(p.id));
    });
    marker.addTo(pointsLayer);
    markerById.set(p.id, marker);
  }

  const legend = L.control({ position: 'bottomright' });
  legend.onAdd = () => {
    legendEl = L.DomUtil.create('div', 'map-legend');
    L.DomEvent.disableClickPropagation(legendEl);
    legendEl.innerHTML = `
      <div class="legend-title">Estado</div>
      ${ESTADOS.map((e) => `
        <div class="legend-row">
          <span class="legend-swatch legend-circle" style="background:${e.color}"></span>
          <span>${escapeHtml(e.label)}</span>
        </div>`).join('')}`;
    return legendEl;
  };
  legend.addTo(map);

  const inBox = conCoords.filter((p) =>
    p.coords.lat >= CALI_BBOX.latMin && p.coords.lat <= CALI_BBOX.latMax
    && p.coords.lon >= CALI_BBOX.lngMin && p.coords.lon <= CALI_BBOX.lngMax);
  lastFitBounds = inBox.length ? L.latLngBounds(inBox.map((p) => [p.coords.lat, p.coords.lon])) : null;
  if (lastFitBounds) map.fitBounds(lastFitBounds, { padding: [40, 40], maxZoom: 16 });
  else map.setView(CALI_CENTER, CALI_ZOOM);
  setTimeout(() => { if (map) map.invalidateSize(); }, 80);

  return conCoords.length;
}

// ---- Record list -------------------------------------------------------------

function listItemHtml(p) {
  const e = estadoDe(p);
  const fotos = (p.fotos || []).length ? `${p.fotos.length} foto${p.fotos.length === 1 ? '' : 's'}` : 'sin fotos';
  return `<li>
    <button type="button" class="eval-row" data-ps-detail="${escapeHtml(p.id)}">
      <span class="eval-dot" style="background:${e.color}" aria-hidden="true"></span>
      <span class="eval-name">${escapeHtml(p.nombre || 'Sin nombre')}</span>
      <span class="eval-pill" style="--eval-pill:${e.color}">${escapeHtml(e.label)}</span>
      <span class="eval-meta">${escapeHtml(p.comuna_corregimiento || 'Sin comuna')} · ${escapeHtml(p.barrio_vereda || 'Sin barrio')}</span>
      <span class="eval-meta">${escapeHtml(p.nombre_solicitante || 'Sin solicitante')} · ${fotos}</span>
      <span class="eval-cta">Ver detalle &rsaquo;</span>
    </button>
  </li>`;
}

// ---- Detail modal --------------------------------------------------------------

function detailHtml(p) {
  const e = estadoDe(p);
  const group = (titulo, filas) => {
    const body = filas
      .filter(([, v]) => v !== '' && v != null)
      .map(([k, v]) => `<div class="detail-field"><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`)
      .join('');
    return body ? `<div class="detail-group"><h3>${escapeHtml(titulo)}</h3><dl class="detail-fields">${body}</dl></div>` : '';
  };
  const fotos = (p.fotos || []).length
    ? p.fotos.map((url, i) => `<button type="button" class="detail-photo" data-foto-idx="${i}" aria-label="Ampliar foto ${i + 1}"><img src="${escapeHtml(url)}" alt="Foto ${i + 1}" loading="lazy"></button>`).join('')
    : '<span class="detail-photos-empty">Este punto no tiene fotos.</span>';

  return `
    <div class="eval-detail-banner" style="--eval-pill:${e.color}">
      <span class="eval-pill" style="--eval-pill:${e.color}">${escapeHtml(e.label)}</span>
      <span class="eval-detail-code">${escapeHtml(p.clave_integracion || '')}</span>
    </div>
    <div class="detail-media">
      <div class="detail-minimap" id="ps-detail-map"></div>
      <div class="detail-photos" id="ps-detail-photos">${fotos}</div>
    </div>
    ${group('Punto', [
      ['Nombre', p.nombre || 'Sin dato'],
      ['Dirección', p.direccion || 'Sin dato'],
      ['Comuna / corregimiento', p.comuna_corregimiento || 'Sin dato'],
      ['Barrio / vereda', p.barrio_vereda || 'Sin dato'],
      ['Latitud', p.coords ? Number(p.coords.lat).toFixed(6) : 'Sin dato'],
      ['Longitud', p.coords ? Number(p.coords.lon).toFixed(6) : 'Sin dato'],
    ])}
    ${group('Justificación', [['Motivo de la solicitud', p.justificacion || 'Sin dato']])}
    ${group('Contacto', [
      ['Solicitante', p.nombre_solicitante || 'Sin dato'],
      ['Teléfono', p.telefono_solicitante || 'Sin dato'],
    ])}
    <div class="sticker-form-actions">
      <button type="button" class="btn-secondary" id="ps-detail-eliminar" data-ps-id="${escapeHtml(p.id)}">Eliminar punto</button>
    </div>`;
}

// ---- Create modal: geocode + draggable marker ----------------------------------

let createMap = null;
let createMarker = null;

function teardownCreateMap() {
  if (createMap) { createMap.remove(); createMap = null; }
  createMarker = null;
}

/** Small draggable-marker map for the create modal (design.md: "after
 *  geocoding a small Leaflet map shows a draggable marker"). Kept inline in
 *  this file, separate from mapview.js's buildMiniMap (that one is
 *  non-draggable, built for the read-only detail views). `onMove(lat,lng)`
 *  fires on drag so the lat/lng inputs stay in sync. */
function renderCreateMap(lat, lng, onMove) {
  teardownCreateMap();
  const el = document.getElementById('ps-coords-map');
  if (!el) return;
  createMap = L.map(el, { zoomControl: true, minZoom: 10, maxZoom: 19 }).setView([lat, lng], 17);
  L.tileLayer(basemapTileUrl(), { subdomains: 'abcd', maxZoom: 20 }).addTo(createMap);
  createMarker = L.marker([lat, lng], { draggable: true }).addTo(createMap);
  createMarker.on('dragend', () => {
    const pos = createMarker.getLatLng();
    onMove(pos.lat, pos.lng);
  });
  setTimeout(() => { if (createMap) createMap.invalidateSize(); }, 50);
}

function moveCreateMarker(lat, lng) {
  if (!createMarker || !createMap) return;
  createMarker.setLatLng([lat, lng]);
  createMap.setView([lat, lng]);
}

// ---- Photo upload (presigned S3, same contract as backend/app/routers/sign.py) --
//
// GAP CLOSED (puntos-solicitados gap-fix): sign.py now also accepts a
// `clave_integracion` (`PLN-...`) code — validated via
// `app.jobs.planeacion_cruce.verify_clave_integracion` — and keys matching
// uploads under `solicitados/{codigo}/foto_{slot}.jpg`. The evaluaciones
// path/regex are unchanged.
async function subirFoto(file, slot, codigo, getToken) {
  const token = await getToken();
  const res = await fetch(apiUrl('sign'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ codigo, slot }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
  const up = await fetch(data.uploadUrl, { method: 'PUT', headers: { 'Content-Type': 'image/jpeg' }, body: file });
  if (!up.ok) throw new Error(`put-${up.status}`);
  return data.publicUrl;
}

// ---- API calls (REST — no `action` dispatch, unlike planeacionAsignaciones) -----

async function authHeaders(getToken) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volvé a iniciar sesión.');
  return { Authorization: `Bearer ${token}` };
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
  return data;
}

async function apiList(getToken) {
  const headers = await authHeaders(getToken);
  const data = await fetchJson(apiUrl('puntosSolicitados'), { headers });
  return data.puntos || [];
}

async function apiCreate(getToken, body) {
  const headers = { 'Content-Type': 'application/json', ...(await authHeaders(getToken)) };
  return fetchJson(apiUrl('puntosSolicitados'), { method: 'POST', headers, body: JSON.stringify(body) });
}

async function apiPatch(getToken, id, body) {
  const headers = { 'Content-Type': 'application/json', ...(await authHeaders(getToken)) };
  return fetchJson(`${apiUrl('puntosSolicitados')}/${encodeURIComponent(id)}`, { method: 'PATCH', headers, body: JSON.stringify(body) });
}

async function apiDelete(getToken, id) {
  const headers = await authHeaders(getToken);
  return fetchJson(`${apiUrl('puntosSolicitados')}/${encodeURIComponent(id)}`, { method: 'DELETE', headers });
}

async function apiGeocode(getToken, direccion) {
  const headers = { 'Content-Type': 'application/json', ...(await authHeaders(getToken)) };
  return fetchJson(apiUrl('geocode'), { method: 'POST', headers, body: JSON.stringify({ direccion }) });
}

// ---- Entry point -------------------------------------------------------------

const AUTO_REFRESH_MS = 5 * 60 * 1000;
let autoRefreshTimer = null;

/** Renders the tab into `section` (already in the DOM) and wires create/list/
 *  detail/delete + geocode. Mirrors evaluaciones.js's init shape, extended
 *  with the create flow this tab owns and evaluaciones.js does not. */
export function initPuntosSolicitados(section, { getToken }) {
  section.innerHTML = sectionHtml();
  const kpis = section.querySelector('#ps-kpis');
  const barEl = section.querySelector('#ps-bar');
  const listEl = section.querySelector('#ps-list');
  const listMeta = section.querySelector('#ps-list-meta');
  const mapMeta = section.querySelector('#ps-map-meta');
  const modal = section.querySelector('#ps-modal');
  const modalBody = section.querySelector('#ps-modal-body');
  const modalTitle = section.querySelector('#ps-modal-title');
  const reloadBtn = section.querySelector('#ps-reload');
  const searchEl = section.querySelector('#ps-search');
  const chipsEl = section.querySelector('#ps-estado-chips');
  const comunaSelect = section.querySelector('#ps-comuna-select');
  const barrioSelect = section.querySelector('#ps-barrio-select');

  const crearBtn = section.querySelector('#ps-crear');
  const crearModal = section.querySelector('#ps-crear-modal');
  const crearForm = section.querySelector('#ps-crear-form');
  const crearError = section.querySelector('#ps-crear-error');
  const crearSubmit = section.querySelector('#ps-crear-submit');
  const direccionInput = section.querySelector('#ps-direccion');
  const geocodeBtn = section.querySelector('#ps-geocode-btn');
  const geocodeNote = section.querySelector('#ps-geocode-note');
  const latInput = section.querySelector('#ps-lat');
  const lngInput = section.querySelector('#ps-lng');
  const fotosInput = section.querySelector('#ps-fotos-input');
  const fotosPreview = section.querySelector('#ps-fotos-preview');
  const fotosCaption = section.querySelector('#ps-fotos-caption');
  const comunaComboInput = section.querySelector('#ps-comuna-input');
  const comunaComboList = section.querySelector('#ps-comuna-list');
  const barrioComboInput = section.querySelector('#ps-barrio-input');
  const barrioComboList = section.querySelector('#ps-barrio-list');

  let byId = new Map();
  let allPuntos = [];
  let comunaMap = new Map();
  let filters = { search: '', estado: '', comuna: '', barrio: '' };
  let fotosSeleccionadas = []; // File[]
  // Fingerprint of the last rendered dataset — same technique evaluaciones.js's
  // load() uses: a silent auto-refresh poll that returned exactly what is
  // already on screen skips the whole re-render (which would otherwise reset
  // the map's pan/zoom mid-use).
  let lastFingerprint = null;

  // ---- detail modal ----
  const closeModal = () => { modal.classList.remove('is-open'); modal.setAttribute('aria-hidden', 'true'); };
  modal.querySelectorAll('[data-ps-close]').forEach((el) => el.addEventListener('click', closeModal));

  function openDetail(id) {
    const p = byId.get(id);
    if (!p) return;
    modalTitle.textContent = p.nombre || 'Punto solicitado';
    modalBody.innerHTML = detailHtml(p);
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    // buildMiniMap speaks the Panel's record shape (x = lng, y = lat) — same
    // non-draggable mini-map evaluaciones.js's detail modal uses.
    buildMiniMap(modalBody.querySelector('#ps-detail-map'), p.coords ? { x: p.coords.lon, y: p.coords.lat } : {});
    modalBody.querySelectorAll('[data-foto-idx]').forEach((btn) => {
      btn.addEventListener('click', () => openLightbox(p.fotos, Number(btn.dataset.fotoIdx)));
    });
    const delBtn = modalBody.querySelector('#ps-detail-eliminar');
    if (delBtn) delBtn.addEventListener('click', () => eliminarPunto(id));
  }

  section.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-ps-detail]');
    if (btn && listEl.contains(btn)) openDetail(btn.dataset.psDetail);
  });

  const setHighlight = (id, on) => {
    const marker = markerById.get(id);
    if (marker) marker.setStyle(on ? { radius: 12, color: COLORS.accent, weight: 3 } : { radius: 8, color: '#0B1D33', weight: 1 });
  };
  listEl.addEventListener('pointerover', (ev) => { const row = ev.target.closest('[data-ps-detail]'); if (row) setHighlight(row.dataset.psDetail, true); });
  listEl.addEventListener('pointerout', (ev) => { const row = ev.target.closest('[data-ps-detail]'); if (row) setHighlight(row.dataset.psDetail, false); });
  listEl.addEventListener('focusin', (ev) => { const row = ev.target.closest('[data-ps-detail]'); if (row) setHighlight(row.dataset.psDetail, true); });
  listEl.addEventListener('focusout', (ev) => { const row = ev.target.closest('[data-ps-detail]'); if (row) setHighlight(row.dataset.psDetail, false); });

  function renderFiltered() {
    const filtered = sortPuntos(applyFilters(allPuntos, filters));
    chipsEl.innerHTML = estadoChipsHtml(filters.estado);
    kpis.innerHTML = kpisHtml(filtered);
    barEl.innerHTML = barHtml(filtered);

    if (!filtered.length) {
      listEl.innerHTML = allPuntos.length
        ? '<li class="eval-empty">Ningún punto coincide con los filtros aplicados.</li>'
        : '<li class="eval-empty">Todavía no hay puntos solicitados registrados.</li>';
      listMeta.textContent = '';
    } else {
      listEl.innerHTML = filtered.map(listItemHtml).join('');
      listMeta.textContent = `${filtered.length} · más reciente primero`;
    }

    const conCoords = renderMap(filtered, openDetail);
    const sinCoords = filtered.length - conCoords;
    mapMeta.textContent = sinCoords ? `${conCoords} en el mapa · ${sinCoords} sin coordenadas` : `${conCoords} en el mapa`;
  }

  async function load({ silent = false } = {}) {
    if (!silent) {
      kpis.innerHTML = '<p class="sticker-loading">Cargando puntos solicitados…</p>';
      barEl.innerHTML = '';
      listEl.innerHTML = '';
      listMeta.textContent = '';
      mapMeta.textContent = '';
    }
    try {
      const puntos = await apiList(getToken);
      // Fingerprint from the raw fetch: an unchanged silent poll short-circuits
      // here, before touching filters/selects/map — same shape as
      // evaluaciones.js's load({ silent }).
      const fingerprint = JSON.stringify(puntos.map((p) => [p.id, p.estado_seguimiento, (p.fotos || []).length]));
      if (silent && fingerprint === lastFingerprint) return;
      lastFingerprint = fingerprint;

      allPuntos = puntos;
      byId = new Map(allPuntos.map((p) => [p.id, p]));
      comunaMap = comunaBarrioMap(allPuntos);
      renderComunaSelect(comunaSelect, comunaMap);
      renderBarrioSelect(barrioSelect, comunaMap, comunaSelect.value);
      filters.comuna = comunaSelect.value;
      filters.barrio = barrioSelect.value;
      renderFiltered();
    } catch (err) {
      // A failed silent poll keeps the last good render on screen; the next
      // tick (or the manual button) retries.
      if (silent) return;
      teardownMap();
      barEl.innerHTML = '';
      kpis.innerHTML = `<p class="sticker-error" role="alert">No se pudieron cargar los puntos solicitados: ${escapeHtml(err.message)}</p>`;
    }
  }

  async function eliminarPunto(id) {
    // eslint-disable-next-line no-alert -- same confirm() convention as planeacion.js's delete actions
    if (!window.confirm('¿Eliminar este punto solicitado? Esta acción no se puede deshacer.')) return;
    try {
      await apiDelete(getToken, id);
      closeModal();
      showToast('Punto solicitado eliminado.');
      await load();
    } catch (err) {
      showToast(`No se pudo eliminar el punto: ${err.message}`, 'error');
    }
  }

  reloadBtn.addEventListener('click', () => load());

  searchEl.addEventListener('input', () => { filters = { ...filters, search: searchEl.value }; renderFiltered(); });
  chipsEl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-filter-group="estado"]');
    if (!btn) return;
    filters = { ...filters, estado: btn.dataset.filterValue };
    renderFiltered();
  });
  comunaSelect.addEventListener('change', () => {
    filters = { ...filters, comuna: comunaSelect.value, barrio: '' };
    renderBarrioSelect(barrioSelect, comunaMap, comunaSelect.value);
    renderFiltered();
  });
  barrioSelect.addEventListener('change', () => { filters = { ...filters, barrio: barrioSelect.value }; renderFiltered(); });

  // ---- create modal: comuna/barrio comboboxes ----
  // comuna_barrios.json (built by scripts/prepare_basemaps.py from the same
  // barrios_veredas basemap the choropleth join uses) fetched once and cached
  // at module scope — every tab open/modal open after the first reuses it.
  let comunaBarriosCache = null;
  function fetchComunaBarrios() {
    if (!comunaBarriosCache) {
      comunaBarriosCache = fetch('data/comuna_barrios.json')
        .then((res) => {
          if (!res.ok) throw new Error('No se pudo cargar comuna_barrios.json');
          return res.json();
        })
        .catch((err) => {
          // Soft-fail: the combobox input is still a plain text field, so an
          // admin can type a comuna/barrio manually even with zero suggestions
          // — same fallback the free-text inputs gave before this change.
          console.error(err);
          return {};
        });
    }
    return comunaBarriosCache;
  }

  let comunaBarrios = {};
  const barrioCombo = mountCombobox(barrioComboInput, barrioComboList, { options: [] });
  // Shared by the explicit list-pick (onSelect) and the typed-exact-match
  // fallback below — both must unlock/populate barrio identically.
  function selectComuna(comuna) {
    const barrios = comunaBarrios[comuna] || [];
    barrioComboInput.value = '';
    barrioComboInput.disabled = false;
    barrioComboInput.placeholder = 'Buscar barrio o vereda…';
    barrioCombo.setOptions(barrios.map((b) => ({ id: b, label: b })));
  }
  const comunaCombo = mountCombobox(comunaComboInput, comunaComboList, {
    options: [],
    onSelect: selectComuna,
  });
  fetchComunaBarrios().then((catalog) => {
    comunaBarrios = catalog;
    comunaCombo.setOptions(Object.keys(catalog).sort().map((c) => ({ id: c, label: c })));
  });
  // FIX 1 (barrio required-field bypass): onSelect only fires on an explicit
  // list click/Enter. An admin who types a valid comuna name and tabs/clicks
  // away without picking from the dropdown must still unlock barrio instead
  // of silently leaving it `disabled` (which drops it from both native
  // `required` validation and FormData, submitting an empty barrio_vereda).
  comunaComboInput.addEventListener('blur', () => {
    const typed = comunaComboInput.value.trim();
    if (!typed) return;
    const match = Object.keys(comunaBarrios).find((c) => normalize(c) === normalize(typed));
    if (match) { comunaComboInput.value = match; selectComuna(match); }
  });

  // ---- create modal ----
  function resetCrearForm() {
    crearForm.reset();
    crearError.hidden = true;
    latInput.value = '';
    lngInput.value = '';
    fotosSeleccionadas = [];
    fotosPreview.innerHTML = '';
    fotosCaption.textContent = 'Ningún archivo seleccionado';
    teardownCreateMap();
    barrioComboInput.disabled = true;
    barrioComboInput.placeholder = 'Elegí una comuna primero…';
    barrioCombo.setOptions([]);
  }

  function openCrear() {
    resetCrearForm();
    crearModal.classList.add('is-open');
    crearModal.setAttribute('aria-hidden', 'false');
  }
  function closeCrear() {
    crearModal.classList.remove('is-open');
    crearModal.setAttribute('aria-hidden', 'true');
    teardownCreateMap();
  }
  crearBtn.addEventListener('click', openCrear);
  crearModal.querySelectorAll('[data-ps-crear-close]').forEach((el) => el.addEventListener('click', closeCrear));

  function syncCoordsFromInputs() {
    const lat = Number(latInput.value);
    const lng = Number(lngInput.value);
    if (Number.isFinite(lat) && Number.isFinite(lng)) {
      if (createMap) moveCreateMarker(lat, lng);
      else renderCreateMap(lat, lng, (nlat, nlng) => { latInput.value = nlat.toFixed(6); lngInput.value = nlng.toFixed(6); });
    }
  }
  latInput.addEventListener('change', syncCoordsFromInputs);
  lngInput.addEventListener('change', syncCoordsFromInputs);

  geocodeBtn.addEventListener('click', async () => {
    const direccion = direccionInput.value.trim();
    if (!direccion) { showToast('Escribí una dirección para ubicar.', 'error'); return; }
    geocodeBtn.disabled = true;
    geocodeBtn.textContent = 'Ubicando…';
    try {
      const result = await apiGeocode(getToken, direccion);
      if (result.accepted) {
        latInput.value = result.lat.toFixed(6);
        lngInput.value = result.lng.toFixed(6);
        renderCreateMap(result.lat, result.lng, (nlat, nlng) => { latInput.value = nlat.toFixed(6); lngInput.value = nlng.toFixed(6); });
        geocodeNote.textContent = `Ubicado: ${result.formatted || direccion}. Arrastrá el marcador para ajustar.`;
      } else {
        geocodeNote.textContent = 'No se pudo ubicar la dirección con precisión suficiente — arrastrá el marcador o ingresá lat/lng manualmente.';
      }
    } catch (err) {
      // Raw backend error (502, missing API key, Google quota…) stays out of
      // user-facing text — logged for debugging, replaced with a clean,
      // persistent message in the SAME note element the "accepted:false" case
      // uses (no auto-dismissing toast for something the admin needs to act on).
      console.error('No se pudo geocodificar:', err);
      geocodeNote.textContent = 'No se pudo conectar con el servicio de geocodificación — ingresá lat/lng manualmente o arrastrá el marcador.';
    } finally {
      geocodeBtn.disabled = false;
      geocodeBtn.textContent = 'Ubicar';
    }
  });

  // Renders the caption/chips from `fotosSeleccionadas` (the source of
  // truth once editing starts) — NOT from `fotosInput.files` (the native
  // FileList), which can't have a single entry removed from it. The old
  // code re-derived from `fotosInput.files` on every change, including the
  // one it synthetically dispatched after a splice() — silently undoing
  // the removal, so "quitar" never actually shrank the upload set.
  function renderFotosPreview() {
    const files = fotosSeleccionadas;
    fotosCaption.textContent = files.length ? `${files.length}/${MAX_FOTOS} fotos seleccionadas` : 'Ningún archivo seleccionado';
    fotosPreview.innerHTML = files.map((f, i) => `<span class="ps-foto-chip">${escapeHtml(f.name)} <button type="button" data-remove-foto="${i}" aria-label="Quitar">&times;</button></span>`).join('');
    fotosPreview.querySelectorAll('[data-remove-foto]').forEach((btn) => {
      btn.addEventListener('click', () => {
        fotosSeleccionadas = removeFotoAt(fotosSeleccionadas, Number(btn.dataset.removeFoto));
        renderFotosPreview();
      });
    });
  }

  // FIX (mid-upload removal doesn't cancel the in-flight upload): once the
  // submit/upload flow starts, the already-captured File references in
  // `fotosSeleccionadas` are what gets uploaded regardless of what the admin
  // clicks — so lock both the picker and every "quitar" button for the
  // duration, instead of letting the UI lie about what's still queued.
  function setFotosLocked(locked) {
    fotosInput.disabled = locked;
    fotosPreview.querySelectorAll('[data-remove-foto]').forEach((btn) => { btn.disabled = locked; });
  }

  fotosInput.addEventListener('change', () => {
    const files = Array.from(fotosInput.files || []).slice(0, MAX_FOTOS);
    if ((fotosInput.files || []).length > MAX_FOTOS) showToast(`Máximo ${MAX_FOTOS} fotos — se tomaron las primeras ${MAX_FOTOS}.`, 'error');
    fotosSeleccionadas = files;
    renderFotosPreview();
  });

  crearForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    crearError.hidden = true;
    const fd = new FormData(crearForm);
    const lat = Number(latInput.value);
    const lng = Number(lngInput.value);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      crearError.textContent = 'Ubicá el punto por dirección, arrastrando el marcador, o ingresando lat/lng manualmente.';
      crearError.hidden = false;
      return;
    }
    const body = {
      nombre: fd.get('nombre') || '',
      comuna_corregimiento: fd.get('comuna_corregimiento') || '',
      barrio_vereda: fd.get('barrio_vereda') || '',
      nombre_solicitante: fd.get('nombre_solicitante') || '',
      telefono_solicitante: fd.get('telefono_solicitante') || '',
      justificacion: fd.get('justificacion') || '',
      direccion: fd.get('direccion') || '',
      lat, lng,
      fotos: [],
    };
    crearSubmit.disabled = true;
    crearSubmit.textContent = 'Creando…';
    setFotosLocked(true);
    let created;
    try {
      created = await apiCreate(getToken, body);
    } catch (err) {
      // Nothing exists yet — same "form looks untouched" failure as before,
      // and the admin can still edit the fotos set before retrying.
      crearError.textContent = err.message;
      crearError.hidden = false;
      crearSubmit.disabled = false;
      crearSubmit.textContent = 'Crear punto';
      setFotosLocked(false);
      return;
    }
    // The point now exists server-side. From here on, a failure must look
    // DIFFERENT from "nothing was created" — otherwise an admin retries and
    // creates a duplicate. Always closeCrear()+load() so they can see the
    // point already in the list.
    try {
      // Photos upload AFTER create (design.md ADR-1: id is minted server-side;
      // the presigned flow needs an identifier that only exists once the point
      // does). Best-effort: a failed photo never rolls back the point itself —
      // photos are optional per spec.
      if (fotosSeleccionadas.length) {
        const urls = [];
        for (let i = 0; i < fotosSeleccionadas.length; i += 1) {
          try {
            // eslint-disable-next-line no-await-in-loop -- sequential slots, same as formulario/js/form.js's own upload loop
            urls.push(await subirFoto(fotosSeleccionadas[i], i + 1, created.clave_integracion, getToken));
          } catch (err) {
            showToast(`No se pudo subir una foto: ${err.message}`, 'error');
          }
        }
        if (urls.length) await apiPatch(getToken, created.id, { fotos: urls });
      }
      showToast('Punto solicitado creado.');
    } catch (err) {
      showToast(`El punto se creó (código ${created.clave_integracion}) pero hubo un problema guardando las fotos: ${err.message}. Abrilo desde la lista para reintentar.`, 'error');
    } finally {
      closeCrear();
      await load();
      crearSubmit.disabled = false;
      crearSubmit.textContent = 'Crear punto';
      setFotosLocked(false);
    }
  });

  load();

  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(() => {
    if (!section.isConnected) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; return; }
    if (document.visibilityState === 'hidden' || section.closest('[hidden]')) return;
    load({ silent: true }).catch(() => {});
  }, AUTO_REFRESH_MS);

  return {
    invalidate: () => {
      if (!map) return;
      map.invalidateSize();
      if (lastFitBounds) map.fitBounds(lastFitBounds, { padding: [40, 40], maxZoom: 16 });
    },
  };
}
