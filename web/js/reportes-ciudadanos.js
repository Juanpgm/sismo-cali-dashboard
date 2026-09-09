// Reportes ciudadanos tab: every citizen report from atencionsismo
// informe/json, read from the public Blob snapshot reportes_ciudadanos.json
// (backend/app/services/reportes_ciudadanos.py, design D5/D6). Same shape
// of module as evaluaciones.js: pure classification/filter helpers exported
// for the Node self-check, then the DOM/Leaflet section below.
import { COLORS, escapeHtml, basemapTileUrl, normalize, loadXlsx, downloadStamp, showToast } from './utils.js';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
const CALI_BBOX = { latMin: 3.30, latMax: 3.55, lngMin: -76.60, lngMax: -76.40 };
const PAGE_SIZE = 100;

// API `estadoVerificacion` labels, worst-known first (KPI row order).
export const ESTADOS = [
  { key: 'Visitado crítico', label: 'visitado crítico', color: COLORS.status.i2 },
  { key: 'Evaluación especializada', label: 'evaluación especializada', color: COLORS.accent },
  { key: 'Visitado', label: 'visitado', color: COLORS.status.h },
  { key: 'Visita fallida', label: 'visita fallida', color: '#9AA5B1' },
  { key: 'Asignado', label: 'asignado', color: '#5B8DEF' },
  { key: 'Reportado', label: 'reportado', color: COLORS.unknown },
];
const SIN_ESTADO = { key: 'SIN_DATO', label: 'sin dato', color: COLORS.unknown };
const ESTADO_BY_NORM = new Map(ESTADOS.map((e) => [normalize(e.key), e]));

export function estadoDe(reporte) {
  return ESTADO_BY_NORM.get(normalize(String((reporte && reporte.estado) || ''))) || SIN_ESTADO;
}

// Self-declared damage at intake, worst first. Used for the distribution bar
// and the "afectación" colour mode; NOT a technical severity (see the
// exploratory report: use habitabilidad / sticker for that).
export const AFECTACIONES = [
  { key: 'COLAPSO TOTAL', color: COLORS.status.i2 },
  { key: 'COLAPSO PARCIAL', color: '#E0603C' },
  { key: 'RIESGO COLAPSO', color: COLORS.status.r2 },
  { key: 'DAÑO ESTRUCTURAL', color: '#E8B04B' },
  { key: 'DAÑO MAMPOSTERÍA', color: '#B8C56B' },
  { key: 'NO SE EVIDENCIA NINGÚN DAÑO', color: COLORS.status.h },
];
const AFECTACION_BY_NORM = new Map(AFECTACIONES.map((a) => [normalize(a.key), a]));
const colorAfectacion = (r) => (AFECTACION_BY_NORM.get(normalize(String((r && r.afectacion) || ''))) || SIN_ESTADO).color;

const STICKER_COLORS = { verde: COLORS.status.h, amarillo: COLORS.status.r2, rojo: COLORS.status.i2 };
export function colorSticker(reporte) {
  const c = String((reporte && reporte.sticker && reporte.sticker.color) || '').trim().toLowerCase();
  return STICKER_COLORS[c] || COLORS.unknown;
}

export const COLOR_MODES_REPORTES = {
  estado: { label: 'Estado', colorOf: (r) => estadoDe(r).color },
  afectacion: { label: 'Afectación', colorOf: colorAfectacion },
  sticker: { label: 'Sticker', colorOf: colorSticker },
  // Panel = Survey123/EDE field inspection (a DIFFERENT system from this
  // report, matched server-side by proximity — app/services/
  // reportes_panel_state.py). Green: visitado en Panel + sticker confirmado.
  // Amber: visitado en Panel, sticker aún sin confirmar. Gray: sin match en
  // Panel todavía (r.panel absent/null covers "not matched yet" the same
  // way as a malformed value — never crashes, never misclassifies).
  panel: {
    label: 'Panel',
    colorOf: (r) => (r.panel && r.panel.visitado && r.panel.sticker) ? COLORS.status.h
      : (r.panel && r.panel.visitado) ? COLORS.status.r2
      : COLORS.unknown,
  },
};

export function contarPor(list, keyFn) {
  const out = {};
  for (const item of list) {
    const k = keyFn(item);
    out[k] = (out[k] || 0) + 1;
  }
  return out;
}

export function opcionesDe(list, campo) {
  const set = new Set();
  for (const r of list) {
    const v = String((r && r[campo]) || '').trim();
    if (v) set.add(v);
  }
  return [...set].sort((a, b) => a.localeCompare(b, 'es'));
}

export function applyFiltrosReportes(list, filtros) {
  const f = filtros || {};
  const q = f.search ? normalize(f.search) : '';
  return list.filter((r) => {
    if (!r) return false;
    if (f.estado && estadoDe(r).key !== f.estado) return false;
    if (f.afectacion && normalize(r.afectacion || '') !== normalize(f.afectacion)) return false;
    if (f.tipo && r.tipo_inmueble !== f.tipo) return false;
    if (f.comuna && r.comuna !== f.comuna) return false;
    if (f.barrio && r.barrio !== f.barrio) return false;
    if (f.sticker) {
      const color = String((r.sticker && r.sticker.color) || '').trim().toLowerCase();
      if (f.sticker === 'SIN_STICKER' ? color !== '' : color !== f.sticker) return false;
    }
    if (q) {
      const hay = normalize([r.direccion, r.nombre_edificio, r.barrio, r.id, r.sticker && r.sticker.numero]
        .filter(Boolean).join(' '));
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

// ── DOM / Leaflet section ─────────────────────────────────────────────────
// Same overall shape as evaluaciones.js: one module-level Leaflet instance
// (the tab's root is re-rendered on every open, so the old container is
// detached and must be torn down first or each visit leaks a map), KPI
// tiles/bar reusing the Panel's .kpi-row/.kpi-tile/.hab-bar classes, and a
// paginated record list sharing evaluaciones.js's .eval-row/-dot/-pill/-meta
// markup so both tabs read as the same visual language.

let map = null;
let baseTile = null;
let pointsLayer = null;
let lastFitBounds = null;
let colorMode = 'estado';
// Guards an out-of-order fetchReportes() response: initReportesCiudadanos
// runs on every tab open, and nothing stops a slow init#1 request from
// resolving after a faster init#2 already rendered — see the seq check in
// initReportesCiudadanos below (same recipe as evaluaciones.js's loadSeq).
let loadSeq = 0;
// The search box's debounce timer, also module-level: a pending timer from
// a previous init must be cancelled when the tab is reopened, or its
// eventual fire calls the OLD init's render() over stale closures.
let searchDebounceTimer = null;

function teardownMap() {
  if (map) { map.remove(); map = null; }
  baseTile = null;
  pointsLayer = null;
}

function formatFecha(iso, fallback) {
  if (!iso) return fallback || 'Sin fecha';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? (fallback || String(iso)) : d.toLocaleString('es-CO', { dateStyle: 'medium', timeStyle: 'short' });
}

const FRESHNESS_BASE = 'Reportes de ingreso vía atencionsismo.cali.gov.co';

/** Freshness line for the KPI header. `count` is always the caller's live
 *  row count (todos.length), never meta.row_count — the snapshot's row_count
 *  can drift from what actually rendered (client-side id/shape filtering).
 *  meta only ever supplies the date; missing meta OR a meta without
 *  generated_at both fall back to the static line with no dynamic part.
 *  Exported: pure, so a self-check can assert all three shapes without the
 *  DOM (design D6 finding 7/9). */
export function freshnessText(meta, count) {
  const generatedAt = meta && meta.generated_at;
  if (!generatedAt) return `${FRESHNESS_BASE}.`;
  const generado = formatFecha(generatedAt);
  const n = Number(count || 0).toLocaleString('es-CO');
  return `${FRESHNESS_BASE} · actualizado ${generado} · ${n} registros`;
}

const pct = (part, total) => (total ? Math.round((part / total) * 1000) / 10 : 0);

function selectFieldHtml(id, label, opciones, todos) {
  return `<label class="sticker-field asignacion-inline-field">
    <span>${escapeHtml(label)}</span>
    <select id="${id}" aria-label="Filtrar por ${escapeHtml(label.toLowerCase())}">
      <option value="">${escapeHtml(todos)}</option>
      ${opciones.map((o) => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`).join('')}
    </select></label>`;
}

function colorSegmentedHtml() {
  return Object.entries(COLOR_MODES_REPORTES).map(([key, def]) => `
    <button type="button" class="segmented-btn${key === colorMode ? ' is-active' : ''}"
      data-rep-color="${key}" role="tab" aria-selected="${key === colorMode}">${escapeHtml(def.label)}</button>`).join('');
}

export function sectionHtml() {
  return `
    <section class="eval-section rep-section" aria-label="Reportes ciudadanos">
      <div class="section-bar">
        <h3 class="section-bar-title">Reportes ciudadanos</h3>
        <span class="eval-toolbar-meta" id="rep-freshness">Reportes de ingreso vía atencionsismo.cali.gov.co.</span>
      </div>

      <div class="kpi-row eval-kpis" id="rep-kpis"></div>
      <div class="eval-bar" id="rep-bar"></div>

      <div class="eval-filters" id="rep-filters">
        <div class="asignacion-search">
          <input type="search" id="rep-search" class="sticker-search-input"
            placeholder="Buscar por dirección, edificio, barrio o sticker…" aria-label="Buscar reportes">
        </div>
        <div class="card-toolbar asignacion-filters" id="rep-filter-selects"></div>
      </div>

      <div class="card eval-workspace-card">
        <div class="card-toolbar">
          <span class="eval-toolbar-title">Reportes</span>
          <div class="segmented" role="tablist" aria-label="Colorear por" data-rep-color-group>${colorSegmentedHtml()}</div>
          <button type="button" class="sticker-action" id="rep-download">Descargar .xlsx</button>
        </div>
        <div class="eval-workspace">
          <div class="eval-map" id="rep-map" role="region" aria-label="Mapa de reportes"></div>
          <div class="eval-aside">
            <div class="eval-aside-head">
              <h4>Registros</h4>
              <span class="eval-toolbar-meta" id="rep-count"></span>
            </div>
            <ul class="eval-list" id="rep-list"></ul>
            <button type="button" class="sticker-action" id="rep-more" hidden>Mostrar más</button>
          </div>
        </div>
      </div>

      <div class="modal" id="rep-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="rep-modal-title">
        <div class="modal-backdrop" data-rep-close></div>
        <div class="modal-panel">
          <div class="modal-header">
            <h2 id="rep-modal-title">Detalle del reporte</h2>
            <button type="button" class="btn-icon" data-rep-close aria-label="Cerrar">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
            </button>
          </div>
          <div class="modal-body" id="rep-modal-body"></div>
        </div>
      </div>
    </section>`;
}

/** Which estados get a KPI tile: all six always, "sin dato" only when it
 *  actually happened (same rule as evaluaciones.js's clasesVisibles). */
function estadosVisibles(counts) {
  return counts[SIN_ESTADO.key] ? [...ESTADOS, SIN_ESTADO] : ESTADOS;
}

function kpisHtml(list) {
  const total = list.length;
  const counts = contarPor(list, (r) => estadoDe(r).key);
  const tiles = estadosVisibles(counts).map((e) => `
    <div class="kpi-tile" style="--kpi-accent:${e.color}">
      <span class="kpi-label kpi-label-lower">${escapeHtml(e.label)}</span>
      <span class="kpi-value">${(counts[e.key] || 0).toLocaleString('es-CO')}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">${pct(counts[e.key] || 0, total)}% del total</span></div>
    </div>`).join('');
  return `
    <div class="kpi-tile is-neutral">
      <span class="kpi-label kpi-label-lower">reportes</span>
      <span class="kpi-value">${total.toLocaleString('es-CO')}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">con los filtros aplicados</span></div>
    </div>
    ${tiles}`;
}

function barHtml(list) {
  const total = list.length;
  if (!total) return '';
  const counts = contarPor(list, (r) => normalize(r.afectacion || ''));
  const segs = AFECTACIONES.map((a) => {
    const n = counts[normalize(a.key)] || 0;
    const share = pct(n, total);
    if (share <= 0) return '';
    return `<div class="hab-bar-seg" style="width:${share}%;background:${a.color}" title="${escapeHtml(a.key.toLowerCase())}: ${n}"></div>`;
  }).join('');
  return `
    <span class="eval-bar-label">afectación declarada</span>
    <div class="hab-bar">${segs}</div>`;
}

function listItemHtml(r) {
  const e = estadoDe(r);
  const stickerColor = r.sticker && r.sticker.color ? colorSticker(r) : null;
  return `<li>
    <button type="button" class="eval-row" data-rep-detail="${escapeHtml(r.id)}">
      <span class="eval-dot" style="background:${COLOR_MODES_REPORTES[colorMode].colorOf(r)}" aria-hidden="true"></span>
      <span class="eval-name">${escapeHtml(r.direccion || 'Sin dirección')}</span>
      <span class="eval-pill-group">
        <span class="eval-pill" style="--eval-pill:${e.color}">${escapeHtml(e.label)}</span>
        ${stickerColor ? `<span class="eval-pill" style="--eval-pill:${stickerColor}">sticker ${escapeHtml(r.sticker.etiqueta || r.sticker.color)}</span>` : ''}
        ${r.panel && r.panel.visitado ? `<span class="eval-pill" style="--eval-pill:${r.panel.sticker ? COLORS.status.h : COLORS.status.r2}">panel ${r.panel.sticker ? 'con sticker' : 'sin sticker'}</span>` : ''}
      </span>
      <span class="eval-meta">${escapeHtml(r.barrio || 'Sin barrio')} · ${escapeHtml(r.comuna || 'Sin comuna')} · ${escapeHtml(r.tipo_inmueble || 'Sin dato')}</span>
      <span class="eval-meta">${escapeHtml(r.afectacion || 'sin afectación')} · ${escapeHtml(formatFecha(r.creado, r.creado_texto))}</span>
      <span class="eval-cta">Ver detalle &rsaquo;</span>
    </button>
  </li>`;
}

function popupHtml(r) {
  const e = estadoDe(r);
  return `
    <div class="map-popup">
      <h4>${escapeHtml(r.direccion || 'Sin dirección')}</h4>
      <dl>
        <dt>Estado</dt><dd>${escapeHtml(e.label)}</dd>
        <dt>Afectación</dt><dd>${escapeHtml(r.afectacion || 'Sin dato')}</dd>
        <dt>Barrio</dt><dd>${escapeHtml(r.barrio || 'Sin dato')}</dd>
      </dl>
      <button type="button" class="btn-link" data-rep-detail="${escapeHtml(r.id)}">Ver detalle &rarr;</button>
    </div>`;
}

function detailHtml(r) {
  const group = (titulo, filas) => {
    const body = filas
      .filter(([, v]) => v !== '' && v != null)
      .map(([k, v]) => `<div class="detail-field"><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`)
      .join('');
    return body ? `<div class="detail-group"><h3>${escapeHtml(titulo)}</h3><dl class="detail-fields">${body}</dl></div>` : '';
  };
  const stickerTexto = r.sticker && r.sticker.numero
    ? `${r.sticker.numero} · ${r.sticker.etiqueta || r.sticker.color || 'sin etiqueta'} · ${r.sticker.origen || 'sin origen'}`
    : 'Sin sticker';
  return `
    ${group('Reporte', [
      ['Estado', estadoDe(r).label],
      ['Afectación declarada', r.afectacion || 'Sin dato'],
      ['Tipo de inmueble', r.tipo_inmueble || 'Sin dato'],
      ['Edificio', r.nombre_edificio || 'Sin dato'],
      ['Barrio', r.barrio || 'Sin dato'],
      ['Comuna', r.comuna || 'Sin dato'],
      ['Creado', formatFecha(r.creado, r.creado_texto)],
    ])}
    ${group('Visita', [
      ['Visitado', r.visitado ? 'Sí' : 'No'],
      ['Pudo evaluar', r.pudo_evaluar || 'Sin dato'],
      ['Alcance', r.alcance || 'Sin dato'],
      ['Habitabilidad', r.habitabilidad || 'Sin dato'],
      ['Sticker', stickerTexto],
    ])}
    ${group('Ubicación', [
      ['Dirección', r.direccion || 'Sin dato'],
      ['Coordenadas', r.lat != null && r.lng != null ? `${r.lat}, ${r.lng}` : 'Sin coordenadas'],
    ])}
    ${group('Descripción', [
      ['Descripción', r.descripcion || 'Sin dato'],
    ])}`;
}

/** Builds the Leaflet map + its base tile + the (empty) points layer group,
 *  but only the first time it's called per init — teardownMap() (called at
 *  the top of initReportesCiudadanos) is what makes `map` null again for a
 *  fresh init. A no-op on every render after the first within the same init,
 *  which is the point: rebuilding the whole map on every filter/search
 *  keystroke was resetting the user's pan/zoom mid-use (finding 2). */
function ensureMap(containerId) {
  if (map) return;
  // preferCanvas: true — up to ~14 800 points (design D6/D5 risk table),
  // a canvas renderer keeps pan/zoom smooth where per-marker SVG DOM nodes
  // would not.
  map = L.map(containerId, { zoomControl: true, minZoom: 10, maxZoom: 18, preferCanvas: true })
    .setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
  pointsLayer = L.layerGroup().addTo(map);
}

/** Re-renders the point markers over the (already built, see ensureMap)
 *  map. `fit` is true only for the first render of an init — see design D6
 *  finding 2: fitBounds on every re-render fights the user's own pan/zoom
 *  every time a filter or the search box changes. */
function renderMap(containerId, list, onDetail, { fit } = { fit: true }) {
  ensureMap(containerId);
  pointsLayer.clearLayers();
  const inCali = [];
  for (const r of list) {
    // Records without coordinates are still listed (listItemHtml doesn't
    // require them) but simply skip the map — no marker to place.
    if (r.lat == null || r.lng == null) continue;
    const marker = L.circleMarker([r.lat, r.lng], {
      radius: 5, color: '#0B1D33', weight: 0.5, fillColor: COLOR_MODES_REPORTES[colorMode].colorOf(r), fillOpacity: 0.85,
    });
    // Function form: popupHtml(r) built eagerly for every one of up to
    // ~14 800 points was wasted work for popups nobody opens — Leaflet only
    // calls this when a marker's popup actually opens.
    marker.bindPopup(() => popupHtml(r), { maxWidth: 260 });
    marker.on('popupopen', (ev) => {
      const btn = ev.popup.getElement().querySelector('[data-rep-detail]');
      if (btn) btn.addEventListener('click', () => onDetail(r.id));
    });
    marker.addTo(pointsLayer);
    if (r.lat >= CALI_BBOX.latMin && r.lat <= CALI_BBOX.latMax && r.lng >= CALI_BBOX.lngMin && r.lng <= CALI_BBOX.lngMax) {
      inCali.push([r.lat, r.lng]);
    }
  }
  lastFitBounds = inCali.length ? L.latLngBounds(inCali) : null;
  if (fit) {
    if (lastFitBounds) {
      map.fitBounds(lastFitBounds, { padding: [30, 30], maxZoom: 15 });
    } else {
      map.setView(CALI_CENTER, CALI_ZOOM);
    }
  }
  // The tab is hidden until switchView() shows it, so Leaflet measures a
  // zero-height container on first mount (same trap as evaluaciones.js).
  setTimeout(() => { if (map) map.invalidateSize(); }, 80);
}

// One module-level theme listener (not per render): the tab owns its own
// Leaflet instance, so mapview.applyMapTheme() never reaches this tile layer.
// Guarded so the Node self-check can import the module.
if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => {
    if (!map || !baseTile) return;
    map.removeLayer(baseTile);
    baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
    baseTile.bringToBack();
  });
}

export function initReportesCiudadanos(root, { fetchReportes }) {
  // Both FIRST statements, before anything else touches the DOM or the
  // network (findings 5/11): a previous init's pending debounce timer must
  // never fire into this call's fresh closures, and the previous init's map
  // (if any) must not survive to be torn down mid-render by ensureMap()'s
  // "only build if null" check below.
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = null;
  teardownMap();
  root.innerHTML = sectionHtml();
  const $ = (id) => root.querySelector(`#${id}`);
  const kpisEl = $('rep-kpis');
  const barEl = $('rep-bar');
  const listEl = $('rep-list');
  const moreBtn = $('rep-more');
  const countEl = $('rep-count');
  const freshnessEl = $('rep-freshness');
  const modal = $('rep-modal');
  const modalBody = $('rep-modal-body');
  const downloadBtn = $('rep-download');
  const colorGroupEl = root.querySelector('[data-rep-color-group]');
  const filtros = { estado: '', afectacion: '', tipo: '', comuna: '', barrio: '', sticker: '', search: '' };
  let todos = [];
  let visibles = [];
  let shown = 0;
  const byId = new Map();

  const closeModal = () => {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
  };
  modal.querySelectorAll('[data-rep-close]').forEach((el) => el.addEventListener('click', closeModal));

  function openDetail(id) {
    const r = byId.get(id);
    if (!r) return;
    modalBody.innerHTML = detailHtml(r);
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
  }

  // Attached to `listEl` (fresh every init, torn down with the rest of
  // sectionHtml() on the next open) rather than the persistent `root` —
  // finding 1: a listener on `root` stacks one more copy on every tab open
  // and is never cleaned up (root is reused across inits, only its
  // innerHTML gets replaced). Map popups live outside `listEl` (Leaflet
  // reparents them), so those buttons are wired on popupopen instead; this
  // covers the list rows only.
  listEl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-rep-detail]');
    if (btn) openDetail(btn.dataset.repDetail);
  });

  function renderList(reset) {
    if (reset) { listEl.innerHTML = ''; shown = 0; }
    if (!visibles.length) {
      listEl.innerHTML = todos.length
        ? '<li class="eval-empty">Ningún reporte coincide con los filtros aplicados.</li>'
        : '<li class="eval-empty">Todavía no hay reportes en esta fuente.</li>';
      moreBtn.hidden = true;
      countEl.textContent = `${visibles.length.toLocaleString('es-CO')} de ${todos.length.toLocaleString('es-CO')} reportes`;
      return;
    }
    const next = visibles.slice(shown, shown + PAGE_SIZE);
    listEl.insertAdjacentHTML('beforeend', next.map(listItemHtml).join(''));
    shown += next.length;
    moreBtn.hidden = shown >= visibles.length;
    countEl.textContent = `${visibles.length.toLocaleString('es-CO')} de ${todos.length.toLocaleString('es-CO')} reportes`;
  }

  // fitBounds only on this init's first render — finding 2: re-fitting on
  // every filter/search re-render fights the user's own pan/zoom.
  let firstMapRender = true;
  function render() {
    visibles = applyFiltrosReportes(todos, filtros);
    kpisEl.innerHTML = kpisHtml(visibles);
    barEl.innerHTML = barHtml(visibles);
    renderList(true);
    renderMap('rep-map', visibles, openDetail, { fit: firstMapRender });
    firstMapRender = false;
  }

  function renderFilters() {
    const opt = (values) => values.map((v) => ({ value: v, label: v }));
    $('rep-filter-selects').innerHTML = [
      selectFieldHtml('rep-estado', 'Estado', ESTADOS.map((e) => ({ value: e.key, label: e.label })), 'Todos'),
      selectFieldHtml('rep-afectacion', 'Afectación', AFECTACIONES.map((a) => ({ value: a.key, label: a.key.toLowerCase() })), 'Todas'),
      selectFieldHtml('rep-tipo', 'Inmueble', opt(opcionesDe(todos, 'tipo_inmueble')), 'Todos'),
      selectFieldHtml('rep-comuna', 'Comuna', opt(opcionesDe(todos, 'comuna')), 'Todas'),
      selectFieldHtml('rep-barrio', 'Barrio', opt(opcionesDe(todos, 'barrio')), 'Todos'),
      selectFieldHtml('rep-sticker', 'Sticker', [
        { value: 'verde', label: 'verde' }, { value: 'amarillo', label: 'amarillo' },
        { value: 'rojo', label: 'rojo' }, { value: 'SIN_STICKER', label: 'sin sticker' },
      ], 'Todos'),
    ].join('');
    const bind = (id, key) => $(id).addEventListener('change', (ev) => { filtros[key] = ev.target.value; render(); });
    bind('rep-estado', 'estado');
    bind('rep-afectacion', 'afectacion');
    bind('rep-tipo', 'tipo');
    bind('rep-comuna', 'comuna');
    bind('rep-barrio', 'barrio');
    bind('rep-sticker', 'sticker');
    // Module-level timer (searchDebounceTimer, not a local closure var): a
    // pending timer must be reachable and cancellable from the NEXT init's
    // own first statements (finding 5) — a local `let t` here would be
    // invisible to that later call and could still fire into this closure.
    $('rep-search').addEventListener('input', (ev) => {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => { filtros.search = ev.target.value; render(); }, 250);
    });
  }

  function renderColorMode() {
    colorGroupEl.innerHTML = colorSegmentedHtml();
    colorGroupEl.querySelectorAll('[data-rep-color]').forEach((btn) => btn.addEventListener('click', () => {
      colorMode = btn.dataset.repColor;
      renderColorMode();
      render();
    }));
  }

  moreBtn.addEventListener('click', () => renderList(false));

  downloadBtn.addEventListener('click', async () => {
    // Never write an empty workbook: nothing to export when every row is
    // filtered out (mandatory edge case — same guard as evaluaciones.js's
    // downloadBtn handler, added here since the plan's draft omitted it).
    if (!visibles.length) { showToast('No hay reportes para exportar.', 'error'); return; }
    downloadBtn.disabled = true;
    try {
      let XLSX;
      try { XLSX = await loadXlsx(); } catch { showToast('No se pudo cargar el generador de Excel.', 'error'); return; }
      const rows = visibles.map((r) => ({
        id: r.id, estado: r.estado, afectacion: r.afectacion, tipo_inmueble: r.tipo_inmueble, direccion: r.direccion,
        nombre_edificio: r.nombre_edificio, barrio: r.barrio, comuna: r.comuna, lat: r.lat, lng: r.lng, creado: r.creado,
        habitabilidad: r.habitabilidad, visitado: r.visitado ? 'Sí' : 'No', pudo_evaluar: r.pudo_evaluar, alcance: r.alcance,
        sticker_numero: r.sticker ? r.sticker.numero : '', sticker_color: r.sticker ? r.sticker.color : '',
        sticker_etiqueta: r.sticker ? r.sticker.etiqueta : '', sticker_origen: r.sticker ? r.sticker.origen : '',
        descripcion: r.descripcion,
      }));
      const { legible, slug } = downloadStamp();
      const ws = XLSX.utils.aoa_to_sheet([
        ['Reportes ciudadanos — atencionsismo.cali.gov.co'],
        ['Fecha de generación:', legible],
        ['Registros:', rows.length],
        [],
      ]);
      XLSX.utils.sheet_add_json(ws, rows, { origin: 'A5' });
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, 'reportes');
      XLSX.writeFile(wb, `reportes_ciudadanos_${slug}.xlsx`);
      showToast('Archivo generado.');
    } finally {
      downloadBtn.disabled = false;
    }
  });

  (async () => {
    // Claims this call's slot in the stale-response race (finding 3): a
    // tab reopened before this fetch resolves bumps loadSeq again, and the
    // checks below drop this response instead of rendering over whatever
    // the newer init already put on screen.
    const seq = ++loadSeq;
    kpisEl.innerHTML = '<p class="sticker-loading">Cargando reportes…</p>';
    barEl.innerHTML = '';
    listEl.innerHTML = '';
    countEl.textContent = '';
    try {
      const { reportes, meta } = await fetchReportes();
      if (seq !== loadSeq) return;
      if (reportes === null) {
        // Distinguishes "the snapshot isn't published yet" (fetchReportes
        // resolves { reportes: null, ... } when neither the Blob copy nor
        // the deploy fallback answered ok) from a genuinely empty snapshot
        // ([], handled by renderList's existing empty-state copy below) —
        // finding 6.
        todos = [];
        visibles = [];
        byId.clear();
        kpisEl.innerHTML = '<p class="sticker-error" role="alert">El snapshot de reportes ciudadanos todavía no está publicado. Se genera en la próxima corrida del refresh.</p>';
        barEl.innerHTML = '';
        listEl.innerHTML = '';
        countEl.textContent = '';
        freshnessEl.hidden = true;
        return;
      }
      todos = reportes.filter((r) => r && r.id);
      byId.clear();
      for (const r of todos) byId.set(r.id, r);
      freshnessEl.hidden = false;
      freshnessEl.textContent = freshnessText(meta, todos.length);
      renderFilters();
      renderColorMode();
      render();
    } catch (err) {
      if (seq !== loadSeq) return;
      kpisEl.innerHTML = `<p class="sticker-error" role="alert">No se pudieron cargar los reportes: ${escapeHtml(err && err.message ? err.message : String(err))}</p>`;
      barEl.innerHTML = '';
      listEl.innerHTML = '';
      countEl.textContent = '';
    }
  })();

  return {
    invalidate: () => {
      if (!map) return;
      map.invalidateSize();
      if (lastFitBounds) map.fitBounds(lastFitBounds, { padding: [30, 30], maxZoom: 15 });
    },
  };
}
