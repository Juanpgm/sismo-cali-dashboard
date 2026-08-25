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
import { COLORS, escapeHtml, basemapTileUrl } from './utils.js';
import { buildMiniMap } from './mapview.js';
import { openLightbox } from './table.js';

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

      <div class="kpi-row eval-kpis" id="eval-kpis"></div>
      <div class="eval-bar" id="eval-bar"></div>

      <div class="card eval-workspace-card">
        <div class="card-toolbar">
          <span class="eval-toolbar-title">Puntos de evaluación</span>
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
  });
}

function popupHtml(e) {
  const c = claseDe(e);
  return `
    <div class="map-popup">
      <h4>${escapeHtml(tituloDe(e))}</h4>
      <dl>
        <dt>Código</dt><dd>${escapeHtml(e.codigo_edificacion)}</dd>
        <dt>Clasificación</dt><dd>${escapeHtml(c.label)}</dd>
        <dt>Dirección</dt><dd>${escapeHtml(e.descripcion.direccion || 'Sin dato')}</dd>
        <dt>Inspector</dt><dd>${escapeHtml(e.inspector.nombre_completo || e.inspector.codigo || 'Sin dato')}</dd>
        <dt>Fecha</dt><dd>${escapeHtml(formatFecha(e.fecha))}</dd>
        <dt>Fotos</dt><dd>${e.fotos.length}</dd>
      </dl>
      <button type="button" class="btn-link" data-eval-detail="${escapeHtml(e.id)}">Ver detalle &rarr;</button>
    </div>`;
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
      fillColor: claseDe(e).color,
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
    legendEl.innerHTML = `
      <div class="legend-title">Clasificación ATC-20</div>
      ${[...CLASES, SIN_CLASE].map((c) => `
        <div class="legend-row">
          <span class="legend-swatch legend-circle" style="background:${c.color}"></span>
          <span>${escapeHtml(c.label)}</span>
        </div>`).join('')}`;
    return legendEl;
  };
  legend.addTo(map);

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
function listItemHtml(e) {
  const c = claseDe(e);
  const fotos = e.fotos.length ? `${e.fotos.length} foto${e.fotos.length === 1 ? '' : 's'}` : 'sin fotos';
  const quien = e.inspector.nombre_completo || `Brigada ${e.inspector.codigo || '—'}`;
  return `<li>
    <button type="button" class="eval-row" data-eval-detail="${escapeHtml(e.id)}">
      <span class="eval-dot" style="background:${c.color}" aria-hidden="true"></span>
      <span class="eval-name">${escapeHtml(tituloDe(e))}</span>
      <span class="eval-pill" style="--eval-pill:${c.color}">${escapeHtml(c.label)}</span>
      <span class="eval-meta">${escapeHtml(e.codigo_edificacion)} · ${escapeHtml(quien)}</span>
      <span class="eval-meta">${escapeHtml(formatFecha(e.fecha))} · ${fotos}</span>
      <span class="eval-cta">Ver detalle &rsaquo;</span>
    </button>
  </li>`;
}

// ---- Detail modal ------------------------------------------------------------

const siNo = (v) => (v ? 'Sí' : 'No');

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
  const kpis = section.querySelector('#eval-kpis');
  const barEl = section.querySelector('#eval-bar');
  const listEl = section.querySelector('#eval-list');
  const listMeta = section.querySelector('#eval-list-meta');
  const mapMeta = section.querySelector('#eval-map-meta');
  const modal = section.querySelector('#eval-modal');
  const modalBody = section.querySelector('#eval-modal-body');
  const modalTitle = section.querySelector('#eval-modal-title');
  const reloadBtn = section.querySelector('#eval-reload');
  let byId = new Map();

  const closeModal = () => {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
  };
  modal.querySelectorAll('[data-eval-close]').forEach((el) => el.addEventListener('click', closeModal));

  function openDetail(id) {
    const e = byId.get(id);
    if (!e) return;
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

  async function load({ silent = false } = {}) {
    if (!silent) {
      kpis.innerHTML = '<p class="sticker-loading">Cargando evaluaciones…</p>';
      barEl.innerHTML = '';
      listEl.innerHTML = '';
      listMeta.textContent = '';
      mapMeta.textContent = '';
    }
    try {
      const evaluaciones = await fetchEvaluaciones();
      const fingerprint = JSON.stringify(evaluaciones.map((e) => [e.id, e.clasificacion, e.fotos.length]));
      if (silent && fingerprint === lastFingerprint) return;
      lastFingerprint = fingerprint;
      byId = new Map(evaluaciones.map((e) => [e.id, e]));
      kpis.innerHTML = kpisHtml(evaluaciones);
      barEl.innerHTML = barHtml(evaluaciones);

      if (!evaluaciones.length) {
        listEl.innerHTML = '<li class="eval-empty">Todavía no hay evaluaciones registradas desde el formulario.</li>';
      } else {
        listEl.innerHTML = evaluaciones.map(listItemHtml).join('');
        listMeta.textContent = `${evaluaciones.length} · más reciente primero`;
      }

      const conCoords = renderMap('eval-map', evaluaciones, openDetail);
      const sinCoords = evaluaciones.length - conCoords;
      mapMeta.textContent = sinCoords
        ? `${conCoords} en el mapa · ${sinCoords} sin coordenadas`
        : `${conCoords} en el mapa`;
    } catch (err) {
      // A failed silent poll keeps the last good render on screen; the next
      // tick (or the manual button) retries.
      if (silent) return;
      teardownMap();
      barEl.innerHTML = '';
      kpis.innerHTML = `<p class="sticker-error" role="alert">No se pudieron cargar las evaluaciones: ${escapeHtml(err.message)}</p>`;
    }
  }

  reloadBtn.addEventListener('click', () => load());
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
  };
}
