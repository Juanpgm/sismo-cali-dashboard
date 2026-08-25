// Asignación sub-section of the Stickers tab: which Panel points already
// have a field sticker vs which are still pending, grouped into cuadrillas
// and handed to inspectors — table + map, both reading the already-fetched
// `sticker_matches`/`cuadrillas` data from /api/sticker-asignaciones. Never
// reads inspections.json/puntos_israel_cali.json (design.md ADR-4).
//
// Lazy-initialized by web/js/stickers.js the first time this segment opens
// (spec.md "Mounted as a sub-section of the existing Stickers tab").
import { COLORS, escapeHtml, basemapTileUrl } from './utils.js';

const ENDPOINT = '/api/sticker-asignaciones';
const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
// A few point coords land outside Cali and would drag fitBounds north (we saw
// it framing Cartago). Fit only to points inside this box; fall back to the
// fixed city view if none qualify.
const CALI_BBOX = { latMin: 3.30, latMax: 3.55, lngMin: -76.60, lngMax: -76.40 };

// Mirrors api/sticker-asignaciones.js's own placeholders (task 0.2, still
// unconfirmed) — display-only hints for the override inputs, not enforced
// here; the backend applies its own default when the field is left blank.
const DEFAULT_MAX_RADIUS_M = 800;
const DEFAULT_MAX_SIZE = 8;

const ESTADOS = ['pendiente', 'asignado', 'en_proceso', 'hecho'];
const ESTADO_LABELS = { pendiente: 'Pendiente', asignado: 'Asignado', en_proceso: 'En proceso', hecho: 'Hecho' };
// Same red -> orange -> yellow -> green ramp already used for habitability
// (COLORS.status), reused here instead of inventing a fourth palette.
const ESTADO_COLOR = {
  pendiente: COLORS.status.i2, asignado: COLORS.status.r2, en_proceso: COLORS.status.r1, hecho: COLORS.status.h,
};
// spec.md "Map view — 3-color legend": blue (tiene_sticker) / red (pendiente)
// / amber (asignado|en_proceso). categorical[0] is the repo's existing blue.
const MARKER_HEX = { blue: COLORS.categorical[0], red: COLORS.status.i2, amber: COLORS.status.r2 };

// cloned verbatim from web/js/stickers.js:19-30 (ENDPOINT swapped).
async function callApi(getToken, body) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volver a iniciar sesión.');
  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

// ---- pure logic (exported for the self-check, stickers-asignacion.test.mjs) ----

/** Map marker colour per spec.md's 3-color legend. `tiene_sticker` wins over
 *  estado (a matched-and-assigned point still reads as "done", not amber). */
export function colorForPunto(punto) {
  if (punto && punto.tiene_sticker === true) return 'blue';
  if (punto && (punto.estado_asignacion === 'asignado' || punto.estado_asignacion === 'en_proceso')) return 'amber';
  return 'red';
}

/** Joins raw sticker_matches points with their cuadrilla/inspector for the
 *  table — computed once per load so client-side sort/filter never re-look-up. */
export function buildRows(puntos, cuadrillas, inspectores) {
  const cuadrillaById = new Map((cuadrillas || []).map((c) => [c.id, c]));
  const inspectorById = new Map((inspectores || []).map((i) => [i.uid, i]));
  return (puntos || []).map((p) => {
    const cuadrilla = p.cuadrilla_id ? cuadrillaById.get(p.cuadrilla_id) : null;
    const inspector = p.inspector_uid ? inspectorById.get(p.inspector_uid) : null;
    return {
      id: p.id,
      direccion: p.direccion || '',
      zona: p.zona_id || '',
      estado_asignacion: p.estado_asignacion || 'pendiente',
      cuadrilla_id: p.cuadrilla_id || null,
      cuadrillaLabel: cuadrilla ? (cuadrilla.nombre || cuadrilla.id) : '—',
      inspector_uid: p.inspector_uid || null,
      inspectorLabel: inspector ? (inspector.nombre_completo || inspector.codigo || inspector.uid) : '—',
      tier: p.tier || null,
      tiene_sticker: !!p.tiene_sticker,
      coords: p.coords || null,
      color: colorForPunto(p),
    };
  });
}

/** Ascending/descending string sort on one row field — client-side, over the
 *  already-fetched rows array (spec.md "Sorting by column header"). */
export function sortRows(rows, key, dir = 'asc') {
  const sign = dir === 'desc' ? -1 : 1;
  return [...rows].sort((a, b) => {
    const av = String(a[key] ?? '').toLowerCase();
    const bv = String(b[key] ?? '').toLowerCase();
    // Empty/missing values always sink to the bottom, both directions, so the
    // ~101 blank-address points never float to the top of the table.
    if (!av && !bv) return 0;
    if (!av) return 1;
    if (!bv) return -1;
    if (av < bv) return -1 * sign;
    if (av > bv) return 1 * sign;
    return 0;
  });
}

/** Filter chip logic (spec.md "Filtering to a single estado"). 'todos' (or
 *  falsy) short-circuits to the full set. */
export function filterRows(rows, estado) {
  if (!estado || estado === 'todos') return rows;
  return rows.filter((r) => r.estado_asignacion === estado);
}

// ---- markup ---------------------------------------------------------------

function cardHead(title, subtitle, extra = '') {
  return `<div class="card-toolbar asignacion-card-head">
    <div class="asignacion-card-titles">
      <span class="eval-toolbar-title">${title}</span>
      <span class="asignacion-subtitle">${subtitle}</span>
    </div>
    ${extra}
  </div>`;
}

function shellHtml() {
  return `
    <div class="section-bar">
      <h3 class="section-bar-title">Asignación</h3>
      <span class="eval-toolbar-meta" id="asignacion-map-meta"></span>
    </div>
    <p class="sticker-ok" id="asignacion-ok" role="status" hidden></p>

    <p class="asignacion-intro">Puntos del Panel y su estado de sticker. Agrupar los pendientes en cuadrillas y asignar cada una a un inspector.</p>
    <ol class="asignacion-steps">
      <li><span class="asignacion-step-n">1</span> Agrupar los puntos pendientes.</li>
      <li><span class="asignacion-step-n">2</span> Asignar un inspector a cada cuadrilla.</li>
      <li><span class="asignacion-step-n">3</span> Ajustar desde la tabla o el mapa.</li>
    </ol>

    <div class="asignacion-workspace">
      <div class="asignacion-main">
        <div class="card">
          ${cardHead('Paso 1 · Agrupar', 'Automáticamente por cercanía, o marcar filas en la tabla para crear una cuadrilla manual.')}
          <div class="card-toolbar asignacion-actions-bar" id="asignacion-toolbar">
            <button type="button" class="btn-primary" id="asignacion-auto">Auto-agrupar</button>
            <label class="sticker-field asignacion-inline-field">
              <span>Radio (m)</span>
              <input type="number" id="asignacion-max-radius" min="50" step="50" placeholder="${DEFAULT_MAX_RADIUS_M}">
            </label>
            <label class="sticker-field asignacion-inline-field">
              <span>Tamaño máx.</span>
              <input type="number" id="asignacion-max-size" min="2" step="1" placeholder="${DEFAULT_MAX_SIZE}">
            </label>
            <button type="button" class="sticker-action" id="asignacion-crear" disabled>Crear cuadrilla de la selección</button>
          </div>
        </div>

        <div class="card">
          ${cardHead('Paso 2 · Cuadrillas e inspectores', 'Asignar un inspector a cada cuadrilla. «Reiniciar agrupación» borra solo las automáticas.', '<button type="button" class="sticker-action sticker-action-off" id="asignacion-reiniciar">Reiniciar agrupación</button>')}
          <div class="asignacion-cuadrillas-scroll" id="asignacion-cuadrillas"></div>
        </div>

        <div class="card">
          ${cardHead('Puntos del Panel', 'Filtrar y ordenar. Marcar filas para crear una cuadrilla manual en el Paso 1.')}
          <div class="card-toolbar asignacion-filters" id="asignacion-filters"></div>
          <div class="table-scroll asignacion-table-scroll" id="asignacion-table-wrap"></div>
        </div>
      </div>

      <aside class="asignacion-aside">
        <div class="card eval-workspace-card asignacion-map-card">
          ${cardHead('Mapa de puntos', 'Reasignar un punto desde su globo.')}
          <div class="eval-map asignacion-map" id="asignacion-map"></div>
        </div>
      </aside>
    </div>`;
}

function filtersHtml(active) {
  const chip = (value, label) => `<button type="button" class="asignacion-chip${active === value ? ' is-active' : ''}" data-estado-filter="${value}">${escapeHtml(label)}</button>`;
  return [chip('todos', 'Todos'), ...ESTADOS.map((e) => chip(e, ESTADO_LABELS[e]))].join('');
}

const SORTABLE = [
  ['direccion', 'Dirección'],
  ['zona', 'Zona'],
  ['estado_asignacion', 'Estado'],
  ['cuadrillaLabel', 'Cuadrilla'],
  ['inspectorLabel', 'Inspector'],
  ['tier', 'Tier'],
];

function tableHtml(rows, sort, selected) {
  const head = SORTABLE.map(([key, label]) => {
    const active = sort.key === key;
    const arrow = active ? (sort.dir === 'desc' ? '&darr;' : '&uarr;') : '';
    return `<th data-sort-field="${key}" class="${active ? 'is-sorted' : ''}">
      <button type="button" class="th-sort-btn">${escapeHtml(label)} <span class="sort-arrow">${arrow}</span></button>
    </th>`;
  }).join('');
  const body = rows.length
    ? rows.map((r) => `<tr>
        <td><input type="checkbox" class="asignacion-check" data-punto-check="${escapeHtml(r.id)}" ${selected.has(r.id) ? 'checked' : ''} ${r.cuadrilla_id ? 'disabled title="Ya pertenece a una cuadrilla"' : ''}></td>
        <td>${escapeHtml(r.direccion || 'Sin dato')}</td>
        <td>${escapeHtml(r.zona || 'Sin dato')}</td>
        <td><span class="eval-pill" style="--eval-pill:${ESTADO_COLOR[r.estado_asignacion] || COLORS.unknown}">${escapeHtml(ESTADO_LABELS[r.estado_asignacion] || r.estado_asignacion)}</span></td>
        <td>${escapeHtml(r.cuadrillaLabel)}</td>
        <td>${escapeHtml(r.inspectorLabel)}</td>
        <td>${escapeHtml(r.tier || '—')}</td>
      </tr>`).join('')
    : `<tr><td colspan="7" class="sticker-empty">Sin puntos para este filtro.</td></tr>`;
  return `<table><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

/** Human-readable cuadrilla label (spec: keep the id as the underlying key, but
 *  never show the raw hash to the operator). A user-typed `nombre` wins; else
 *  the dominant zona of its member points + a per-zona counter; else "Grupo N".
 *  `zonaByPunto` maps punto id -> zona so we can name auto cuadrillas by sector. */
export function cuadrillaLabel(cuadrilla, index, zonaByPunto, zonaSeq) {
  if (cuadrilla.nombre && cuadrilla.nombre.trim()) return cuadrilla.nombre.trim();
  const zonas = (cuadrilla.puntos || []).map((id) => zonaByPunto.get(id)).filter(Boolean);
  if (zonas.length) {
    const counts = {};
    for (const z of zonas) counts[z] = (counts[z] || 0) + 1;
    const dominant = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0];
    const seq = (zonaSeq[dominant] = (zonaSeq[dominant] || 0) + 1);
    return `${dominant} · grupo ${seq}`;
  }
  return `Grupo ${index + 1}`;
}

function cuadrillasHtml(cuadrillas, inspectores, zonaByPunto = new Map()) {
  if (!cuadrillas.length) {
    return '<p class="sticker-empty">Todavía no hay cuadrillas. Usar «Auto-agrupar» o crear una manualmente desde la tabla.</p>';
  }
  const optionsHtml = (selectedUid) => `<option value="">Sin asignar</option>${inspectores.map((i) => `<option value="${escapeHtml(i.uid)}" ${i.uid === selectedUid ? 'selected' : ''}>${escapeHtml(i.nombre_completo || `Brigada ${i.codigo || '—'}`)}</option>`).join('')}`;
  const zonaSeq = {};
  return `<ul class="sticker-list">
    ${cuadrillas.map((c, i) => {
      const n = (c.puntos || []).length;
      const label = cuadrillaLabel(c, i, zonaByPunto, zonaSeq);
      return `<li class="sticker-row">
        <span class="sticker-code" title="Origen">${c.origen === 'auto' ? 'AUTO' : 'MAN'}</span>
        <div class="sticker-identity">
          <span class="sticker-name" title="ID: ${escapeHtml(c.id)}">${escapeHtml(label)}</span>
          <span class="sticker-meta">${n} punto${n === 1 ? '' : 's'}</span>
        </div>
        <select class="asignacion-inspector-select" data-cuadrilla-id="${escapeHtml(c.id)}">${optionsHtml(c.inspector_uid)}</select>
      </li>`;
    }).join('')}
  </ul>`;
}

function popupHtml(row) {
  return `<div class="map-popup">
    <h4>${escapeHtml(row.direccion || 'Sin dirección')}</h4>
    <dl>
      <dt>Estado</dt><dd>${escapeHtml(ESTADO_LABELS[row.estado_asignacion] || row.estado_asignacion)}</dd>
      <dt>Zona</dt><dd>${escapeHtml(row.zona || 'Sin dato')}</dd>
      <dt>Tier</dt><dd>${escapeHtml(row.tier || 'Sin dato')}</dd>
      <dt>Cuadrilla</dt><dd>${escapeHtml(row.cuadrillaLabel)}</dd>
    </dl>
    <label class="sticker-field">
      <span>Reasignar a</span>
      <select data-reasignar-select="${escapeHtml(row.id)}"><option value="">— Elegir inspector —</option></select>
    </label>
  </div>`;
}

// ---- map --------------------------------------------------------------------

// One Leaflet instance for the whole sub-section (same rationale as
// evaluaciones.js: the Stickers view re-renders its root on every open).
let map = null;
let baseTile = null;
let pointsLayer = null;
let legendEl = null;

function teardownMap() {
  if (map) { map.remove(); map = null; }
  baseTile = null;
  pointsLayer = null;
  legendEl = null;
}

// Guarded so the pure-logic self-check can import this module under Node.
if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => {
    if (!map || !baseTile) return;
    map.removeLayer(baseTile);
    baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
    baseTile.bringToBack();
  });
}

function renderMap(rows, inspectores, onReasignar) {
  teardownMap();
  const conCoords = rows.filter((r) => r.coords && Number.isFinite(r.coords.lat) && Number.isFinite(r.coords.lon));

  map = L.map('asignacion-map', { zoomControl: true, minZoom: 10, maxZoom: 18 }).setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
  pointsLayer = L.layerGroup().addTo(map);

  for (const r of conCoords) {
    const marker = L.circleMarker([r.coords.lat, r.coords.lon], {
      radius: 7, color: '#0B1D33', weight: 1, fillColor: MARKER_HEX[r.color], fillOpacity: 0.9,
    });
    marker.bindPopup(popupHtml(r), { maxWidth: 280 });
    marker.on('popupopen', (ev) => {
      const sel = ev.popup.getElement().querySelector('[data-reasignar-select]');
      if (!sel) return;
      inspectores.forEach((i) => {
        const opt = document.createElement('option');
        opt.value = i.uid;
        opt.textContent = i.nombre_completo || `Brigada ${i.codigo || '—'}`;
        if (i.uid === r.inspector_uid) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', async () => {
        if (!sel.value) return;
        sel.disabled = true;
        try {
          await onReasignar(r.id, sel.value);
        } finally {
          sel.disabled = false;
        }
      });
    });
    marker.addTo(pointsLayer);
  }

  const legend = L.control({ position: 'bottomright' });
  legend.onAdd = () => {
    legendEl = L.DomUtil.create('div', 'map-legend');
    L.DomEvent.disableClickPropagation(legendEl);
    legendEl.innerHTML = `
      <div class="legend-title">Estado del sticker</div>
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.blue}"></span><span>Tiene sticker</span></div>
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.red}"></span><span>Pendiente</span></div>
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.amber}"></span><span>Asignado / en proceso</span></div>`;
    return legendEl;
  };
  legend.addTo(map);

  const inBox = conCoords.filter((r) =>
    r.coords.lat >= CALI_BBOX.latMin && r.coords.lat <= CALI_BBOX.latMax
    && r.coords.lon >= CALI_BBOX.lngMin && r.coords.lon <= CALI_BBOX.lngMax);
  if (inBox.length) {
    map.fitBounds(L.latLngBounds(inBox.map((r) => [r.coords.lat, r.coords.lon])), { padding: [40, 40], maxZoom: 16 });
  } else {
    map.setView(CALI_CENTER, CALI_ZOOM);
  }
  // The segment is hidden until stickers.js shows it, so Leaflet measures a
  // zero-height container on first mount.
  setTimeout(() => { if (map) map.invalidateSize(); }, 80);
  return conCoords.length;
}

// ---- entry point --------------------------------------------------------------

/** initStickersAsignacion(root, { getToken, getInspectores }) — renders the
 *  sub-section once, fetches listPuntos+listCuadrillas, wires CRUD. Returns
 *  { reload } so web/js/stickers.js can re-fetch on subsequent opens instead
 *  of calling this twice (spec.md "Init runs once on first Asignación open"). */
export function initStickersAsignacion(root, { getToken, getInspectores }) {
  let rows = [];
  let cuadrillas = [];
  // Default to an always-present column so the first view is meaningful and
  // never fronted by the blank-address points.
  let sortKey = 'estado_asignacion';
  let sortDir = 'asc';
  let estadoFilter = 'todos';
  const selected = new Set();
  let busy = false;

  root.innerHTML = shellHtml();
  const mapMeta = root.querySelector('#asignacion-map-meta');
  const okBox = root.querySelector('#asignacion-ok');
  const filtersEl = root.querySelector('#asignacion-filters');
  const tableWrap = root.querySelector('#asignacion-table-wrap');
  const cuadrillasWrap = root.querySelector('#asignacion-cuadrillas');
  const autoBtn = root.querySelector('#asignacion-auto');
  const radiusInput = root.querySelector('#asignacion-max-radius');
  const sizeInput = root.querySelector('#asignacion-max-size');
  const crearBtn = root.querySelector('#asignacion-crear');
  const reiniciarBtn = root.querySelector('#asignacion-reiniciar');

  const showOk = (msg) => { okBox.textContent = msg; okBox.hidden = !msg; };

  function currentRows() {
    return sortRows(filterRows(rows, estadoFilter), sortKey, sortDir);
  }

  function renderTable() {
    filtersEl.innerHTML = filtersHtml(estadoFilter);
    tableWrap.innerHTML = tableHtml(currentRows(), { key: sortKey, dir: sortDir }, selected);
    wireTable();
  }

  function wireTable() {
    tableWrap.querySelectorAll('[data-sort-field]').forEach((th) => {
      th.addEventListener('click', () => {
        const key = th.dataset.sortField;
        if (sortKey === key) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
        else { sortKey = key; sortDir = 'asc'; }
        renderTable();
      });
    });
    tableWrap.querySelectorAll('[data-punto-check]').forEach((cb) => {
      cb.addEventListener('change', () => {
        const id = cb.dataset.puntoCheck;
        if (cb.checked) selected.add(id); else selected.delete(id);
        crearBtn.disabled = selected.size === 0;
      });
    });
    filtersEl.querySelectorAll('[data-estado-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        estadoFilter = btn.dataset.estadoFilter;
        renderTable();
      });
    });
  }

  async function reasignar(puntoId, nuevoInspectorUid) {
    try {
      await callApi(getToken, { action: 'reasignarPunto', punto_id: puntoId, nuevo_inspector_uid: nuevoInspectorUid });
      showOk('Punto reasignado.');
      await reload();
    } catch (err) {
      alert(err.message); // rare path (network/permission); same idiom as stickers.js
    }
  }

  function renderCuadrillasSection() {
    const inspectores = getInspectores() || [];
    const zonaByPunto = new Map(rows.map((r) => [r.id, r.zona]).filter(([, z]) => z));
    cuadrillasWrap.innerHTML = cuadrillasHtml(cuadrillas, inspectores, zonaByPunto);
    cuadrillasWrap.querySelectorAll('[data-cuadrilla-id]').forEach((sel) => {
      sel.addEventListener('change', async () => {
        if (busy || !sel.value) return;
        busy = true;
        sel.disabled = true;
        try {
          await callApi(getToken, { action: 'asignarInspector', cuadrilla_id: sel.dataset.cuadrillaId, inspector_uid: sel.value });
          showOk('Inspector asignado.');
          await reload();
        } catch (err) {
          alert(err.message);
        } finally {
          busy = false;
        }
      });
    });
  }

  function renderMapSection() {
    const inspectores = getInspectores() || [];
    const n = renderMap(currentRows(), inspectores, reasignar);
    const sinCoords = rows.length - n;
    mapMeta.textContent = sinCoords ? `${n} en el mapa · ${sinCoords} sin coordenadas` : `${n} en el mapa`;
  }

  async function reload() {
    showOk('');
    tableWrap.innerHTML = '<p class="sticker-loading">Cargando asignación…</p>';
    try {
      const [{ puntos }, { cuadrillas: cuadrillasResp }] = await Promise.all([
        callApi(getToken, { action: 'listPuntos' }),
        callApi(getToken, { action: 'listCuadrillas' }),
      ]);
      cuadrillas = cuadrillasResp;
      rows = buildRows(puntos, cuadrillas, getInspectores() || []);
      selected.clear();
      crearBtn.disabled = true;
      renderTable();
      renderCuadrillasSection();
      renderMapSection();
    } catch (err) {
      teardownMap();
      tableWrap.innerHTML = `<p class="sticker-error" role="alert">${escapeHtml(err.message)}</p>`;
    }
  }

  autoBtn.addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    autoBtn.disabled = true;
    try {
      const body = { action: 'autoAgrupar' };
      if (radiusInput.value) body.maxRadiusM = Number(radiusInput.value);
      if (sizeInput.value) body.maxSize = Number(sizeInput.value);
      const { cuadrillas: nuevas } = await callApi(getToken, body);
      showOk(nuevas.length ? `${nuevas.length} cuadrilla${nuevas.length === 1 ? '' : 's'} nueva${nuevas.length === 1 ? '' : 's'} creada${nuevas.length === 1 ? '' : 's'}.` : 'No había puntos pendientes para agrupar.');
      await reload();
    } catch (err) {
      alert(err.message);
    } finally {
      busy = false;
      autoBtn.disabled = false;
    }
  });

  reiniciarBtn.addEventListener('click', async () => {
    if (busy) return;
    if (!window.confirm('Esto borra las cuadrillas automáticas y libera sus puntos a pendiente. Las cuadrillas manuales se conservan. ¿Continuar?')) return;
    busy = true;
    reiniciarBtn.disabled = true;
    try {
      const { eliminadas, puntosLiberados } = await callApi(getToken, { action: 'reiniciarAgrupacion' });
      showOk(eliminadas
        ? `${eliminadas} cuadrilla${eliminadas === 1 ? '' : 's'} automática${eliminadas === 1 ? '' : 's'} eliminada${eliminadas === 1 ? '' : 's'}; ${puntosLiberados} punto${puntosLiberados === 1 ? '' : 's'} liberado${puntosLiberados === 1 ? '' : 's'} a pendiente.`
        : 'No había cuadrillas automáticas para reiniciar.');
      await reload();
    } catch (err) {
      alert(err.message);
    } finally {
      busy = false;
      reiniciarBtn.disabled = false;
    }
  });

  crearBtn.addEventListener('click', async () => {
    if (busy || selected.size === 0) return;
    const nombre = window.prompt('Nombre de la cuadrilla:', '');
    if (nombre === null) return; // cancelled
    busy = true;
    crearBtn.disabled = true;
    try {
      await callApi(getToken, { action: 'crearCuadrilla', nombre: nombre.trim(), puntos: [...selected] });
      showOk('Cuadrilla creada.');
      selected.clear();
      await reload();
    } catch (err) {
      alert(err.message);
    } finally {
      busy = false;
    }
  });

  reload();
  return { reload };
}
