// Asignación sub-section of the Stickers tab: which Panel points already
// have a field sticker vs which are still pending, grouped into cuadrillas
// and handed to inspectors — table + map, both reading the already-fetched
// `sticker_matches`/`cuadrillas` data from /api/sticker-asignaciones. Never
// reads inspections.json/puntos_israel_cali.json (design.md ADR-4).
//
// Lazy-initialized by web/js/stickers.js the first time this segment opens
// (spec.md "Mounted as a sub-section of the existing Stickers tab").
import { COLORS, escapeHtml, basemapTileUrl } from './utils.js';
import { apiUrl } from './api-config.js';

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

// 4 estados. 'en_proceso' = una cuadrilla ya está trabajando el punto en
// campo (a diferencia de 'asignado', que solo significa que se le asignó un
// inspector pero todavía no arrancó la visita) — ningún flujo de ESTA app lo
// escribe todavía (ni el CRUD de admin ni el formulario del inspector), pero
// sí puede llegar por edición manual en Firestore o por un flujo futuro, así
// que displayEstado() lo respeta si lo encuentra en vez de aplastarlo contra
// 'asignado'.
const ESTADOS = ['pendiente', 'asignado', 'en_proceso', 'hecho'];
const ESTADO_LABELS = { pendiente: 'Pendiente', asignado: 'Asignado', en_proceso: 'En proceso', hecho: 'Hecho' };
// Red -> amber (asignado) -> yellow (en_proceso) -> green (hecho); mismo
// esquema de COLORS.status que habitabilidad, con r1 como el escalón propio
// de en_proceso — ya lo usaba la tabla antes, ahora también el mapa.
const ESTADO_COLOR = {
  pendiente: COLORS.status.i2, asignado: COLORS.status.r2, en_proceso: COLORS.status.r1, hecho: COLORS.status.h,
};
// spec.md "Map view — legend": blue (tiene_sticker) / red (pendiente) / amber
// (asignado) / yellow (en_proceso). categorical[0] is the repo's existing blue.
const MARKER_HEX = {
  blue: COLORS.categorical[0], red: COLORS.status.i2,
  amber: COLORS.status.r2, yellow: COLORS.status.r1,
};

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

// Roster read-only reuse of Stickers' own endpoint. The Roster segment that
// used to preload this for the whole Stickers tab moved to Planeación
// (`usuarios-personas-unificadas` Phase 3), so this sub-section now fetches
// its own copy — same 8-line client `planeacion.js`/`stickers.js` already
// carry (a 3rd near-identical copy is fine here, not worth a shared module
// for 8 lines per this repo's own precedent).
async function callStickersApi(getToken, body) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volver a iniciar sesión.');
  const res = await fetch(apiUrl('stickers'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

// ---- pure logic (exported for the self-check, stickers-asignacion.test.mjs) ----

/** Map marker colour per spec.md's map legend. `tiene_sticker` wins over
 *  estado (a matched-and-assigned point still reads as "done", not amber). */
export function colorForPunto(punto) {
  if (punto && punto.tiene_sticker === true) return 'blue';
  if (punto && punto.estado_asignacion === 'en_proceso') return 'yellow';
  if (punto && punto.estado_asignacion === 'asignado') return 'amber';
  return 'red';
}

/** Derived estado a row/punto displays: 'hecho' ONLY once the daily
 *  cruce_sticker.py confirms the sticker (tiene_sticker) — an inspector's own
 *  "marcarHecho" submission (api/inspector-asignaciones.js) flips the RAW
 *  admin-owned estado_asignacion to 'hecho' first, but that's still just a
 *  pending confirmation from the pipeline's point of view, so it reads here as
 *  'asignado' (never regresses to 'pendiente' — see cuadrilla_id/inspector_uid
 *  below) until tiene_sticker catches up. 'en_proceso' passes through as-is
 *  when the raw doc already carries it (no write path in THIS app sets it yet,
 *  but a manual edit or a future flow might, and it shouldn't get silently
 *  collapsed into 'asignado' when it does). */
function displayEstado(p) {
  if (p && p.tiene_sticker === true) return 'hecho';
  if (p && p.estado_asignacion === 'en_proceso') return 'en_proceso';
  if (p && (p.cuadrilla_id || p.inspector_uid)) return 'asignado';
  return 'pendiente';
}

/** Joins raw sticker_matches points with their cuadrilla/inspector for the
 *  table — computed once per load so client-side sort/filter never re-look-up. */
export function buildRows(puntos, cuadrillas, inspectores) {
  const cuadrillaById = new Map((cuadrillas || []).map((c) => [c.id, c]));
  const inspectorById = new Map((inspectores || []).map((i) => [i.uid, i]));
  return (puntos || []).map((p) => {
    const cuadrilla = p.cuadrilla_id ? cuadrillaById.get(p.cuadrilla_id) : null;
    const inspector = p.inspector_uid ? inspectorById.get(p.inspector_uid) : null;
    const estado = displayEstado(p);
    return {
      id: p.id,
      direccion: p.direccion || '',
      zona: p.zona_id || '',
      estado_asignacion: estado,
      habitabilidad: (p.criterio_habitabilidad || '').toUpperCase(),
      colapso: p.colapso || 'no',
      cuadrilla_id: p.cuadrilla_id || null,
      cuadrillaLabel: cuadrilla ? (cuadrilla.nombre || cuadrilla.id) : '—',
      inspector_uid: p.inspector_uid || null,
      inspectorLabel: inspector ? (inspector.nombre_completo || inspector.codigo || inspector.uid) : '—',
      tier: p.tier || null,
      tiene_sticker: !!p.tiene_sticker,
      coords: p.coords || null,
      color: colorForPunto({ tiene_sticker: p.tiene_sticker, estado_asignacion: estado }),
    };
  });
}

// Habilitado = ni Firebase Auth `disabled` ni el perfil `activo` en false —
// mismo criterio que stickers.js:rowHtml (el toggle Habilitar/Inhabilitar del
// roster). Un inspector inhabilitado no puede recibir NUEVAS asignaciones; uno
// ya asignado sigue mostrando su nombre igual (inspectorLabelFor no filtra).
export function isHabilitado(i) {
  return !!i && !i.disabled && !!i.activo;
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

/** Active-assigned count per inspector uid, computed from the already-fetched
 *  rows (no extra API call). "Active" = assigned to them AND not yet 'hecho'.
 *  Shown next to each inspector for balancing — there is no per-inspector cap. */
export function activeCountsByInspector(rows) {
  const counts = new Map();
  for (const r of rows || []) {
    if (r.inspector_uid && r.estado_asignacion !== 'hecho') {
      counts.set(r.inspector_uid, (counts.get(r.inspector_uid) || 0) + 1);
    }
  }
  return counts;
}

/** Field-sweep progress tallies for the gauge, computed from the UNFILTERED
 *  rows: `barrido` = already has a field sticker (blue), else `asignado` when
 *  assigned/in-progress (amber), else `pendiente` (red). Same precedence as
 *  colorForPunto so gauge and map agree. */
export function gaugeCounts(rows) {
  let barrido = 0;
  let asignado = 0;
  let pendiente = 0;
  for (const r of rows || []) {
    if (r.tiene_sticker === true) barrido += 1;
    // El gauge sigue siendo de 3 segmentos: en_proceso cuenta como "en marcha",
    // igual que asignado — el color propio de en_proceso vive en la tabla y el
    // mapa, no acá.
    else if (r.estado_asignacion === 'asignado' || r.estado_asignacion === 'en_proceso') asignado += 1;
    else pendiente += 1;
  }
  return { barrido, asignado, pendiente, total: (rows || []).length };
}

/** Filter the roster by a free-text query over nombre/código/cédula. */
export function filterInspectores(inspectores, query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return inspectores || [];
  return (inspectores || []).filter((i) => {
    const hay = `${i.nombre_completo || ''} ${i.codigo || ''} ${i.cedula || ''}`.toLowerCase();
    return hay.includes(q);
  });
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
        <div class="card asignacion-gauge-card">
          ${cardHead('Avance del barrido', 'Puntos con sticker en campo sobre el total.')}
          <div class="asignacion-gauge" id="asignacion-gauge"></div>
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
  ['habitabilidad', 'Habitabilidad'],
  ['colapso', 'Colapso'],
  ['estado_asignacion', 'Estado'],
  ['inspectorLabel', 'Inspector'],
  ['tier', 'Tier'],
];

// Habitability criterion color (H green · R amber · I red) reusing the habitability
// palette; collapse gets a red pill when total/parcial, muted when 'no'.
const HABIT_COLOR = (h) => {
  const v = String(h || '').toLowerCase();
  if (v.startsWith('h')) return COLORS.status.h;
  if (v.startsWith('r')) return COLORS.status.r2;
  if (v.startsWith('i')) return COLORS.status.i2;
  return COLORS.unknown;
};
const COLAPSO_LABEL = { total: 'Total', parcial: 'Parcial', no: '—' };

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
        <td><input type="checkbox" class="asignacion-check" data-punto-check="${escapeHtml(r.id)}" ${selected.has(r.id) ? 'checked' : ''} ${r.tiene_sticker ? 'disabled title="Ya tiene sticker; no requiere visita"' : (r.colapso === 'total' ? 'disabled title="Colapso total; no requiere visita"' : (r.cuadrilla_id ? 'disabled title="Ya pertenece a una cuadrilla"' : ''))}></td>
        <td>${escapeHtml(r.direccion || 'Sin dato')}</td>
        <td>${escapeHtml(r.zona || 'Sin dato')}</td>
        <td>${r.habitabilidad ? `<span class="eval-pill" style="--eval-pill:${HABIT_COLOR(r.habitabilidad)}">${escapeHtml(r.habitabilidad)}</span>` : '—'}</td>
        <td>${r.colapso && r.colapso !== 'no' ? `<span class="eval-pill" style="--eval-pill:${COLORS.status.i2}">${escapeHtml(COLAPSO_LABEL[r.colapso] || r.colapso)}</span>` : '—'}</td>
        <td><span class="eval-pill" style="--eval-pill:${ESTADO_COLOR[r.estado_asignacion] || COLORS.unknown}">${escapeHtml(ESTADO_LABELS[r.estado_asignacion] || r.estado_asignacion)}</span></td>
        <td>${escapeHtml(r.inspectorLabel)}</td>
        <td>${escapeHtml(r.tier || '—')}</td>
      </tr>`).join('')
    : `<tr><td colspan="8" class="sticker-empty">Sin puntos para este filtro.</td></tr>`;
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

/** Roster display label for one inspector, with their current active load.
 *  `Nombre — codigo (N/cap)` (spec §3). Cap is the editable per-inspector max. */
export function inspectorOptionLabel(insp, count) {
  const name = insp.nombre_completo || `Brigada ${insp.codigo || '—'}`;
  const code = insp.codigo ? ` — ${insp.codigo}` : '';
  // Informational count only — there is no per-inspector cap.
  return `${name}${code} (${count})`;
}

function cuadrillasHtml(cuadrillas, inspectores, zonaByPunto = new Map(), inspectorById = new Map()) {
  if (!cuadrillas.length) {
    return '<p class="sticker-empty">Todavía no hay cuadrillas. Usar «Auto-agrupar» o crear una manualmente desde la tabla.</p>';
  }
  const zonaSeq = {};
  return `<ul class="sticker-list">
    ${cuadrillas.map((c, i) => {
      const n = (c.puntos || []).length;
      const label = cuadrillaLabel(c, i, zonaByPunto, zonaSeq);
      const insp = c.inspector_uid ? inspectorById.get(c.inspector_uid) : null;
      const inspName = insp ? (insp.nombre_completo || `Brigada ${insp.codigo || '—'}`) : '';
      const metaInsp = insp ? `Inspector: ${escapeHtml(inspName)}` : 'Sin asignar';
      return `<li class="sticker-row asignacion-cuadrilla-row" data-cuadrilla-row="${escapeHtml(c.id)}">
        <span class="sticker-code" title="Origen">${c.origen === 'auto' ? 'AUTO' : 'MAN'}</span>
        <div class="sticker-identity">
          <span class="sticker-name" title="ID: ${escapeHtml(c.id)}">${escapeHtml(label)}</span>
          <span class="sticker-meta">${n} punto${n === 1 ? '' : 's'} · ${metaInsp}</span>
        </div>
        <div class="asignacion-combo" data-combo-cuadrilla="${escapeHtml(c.id)}">
          <input type="text" class="asignacion-combo-input" role="combobox" aria-expanded="false"
            aria-autocomplete="list" autocomplete="off" spellcheck="false"
            placeholder="${insp ? 'Cambiar inspector…' : 'Asignar inspector…'}" aria-label="Buscar inspector para asignar"
            value="${escapeHtml(inspName)}">
          <ul class="asignacion-combo-list" role="listbox" hidden></ul>
        </div>
        <div class="asignacion-cuadrilla-actions">
          ${insp ? `<button type="button" class="sticker-action asignacion-desasignar" data-desasignar="${escapeHtml(c.id)}">Quitar asignación</button>` : ''}
          <button type="button" class="sticker-action sticker-action-off asignacion-eliminar" data-eliminar="${escapeHtml(c.id)}">Eliminar</button>
        </div>
      </li>`;
    }).join('')}
  </ul>`;
}

// Vanilla searchable combobox for one cuadrilla's inspector selector: a text
// <input role="combobox"> + a filtered <ul role="listbox">. No library. Shows
// each inspector's current load (informational — no cap). Keyboard: ArrowUp/Down
// move the active option, Enter selects, Escape closes. `onSelect(uid)` fires the
// assignment.
function mountCombobox(comboEl, { inspectores, counts, onSelect }) {
  const input = comboEl.querySelector('.asignacion-combo-input');
  const list = comboEl.querySelector('.asignacion-combo-list');
  let options = []; // [{ uid, disabled }] in current render order
  let active = -1;

  const close = () => {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    active = -1;
  };

  // `query` overrides the input value — on focus we pass '' so the FULL roster
  // shows even when the input still holds the current inspector's name (so
  // changing an already-assigned cuadrilla to another inspector is one click).
  function render(query) {
    const matches = filterInspectores(inspectores, query === undefined ? input.value : query);
    options = [];
    list.innerHTML = matches.map((insp) => {
      const count = counts.get(insp.uid) || 0;
      // No cap: every inspector is selectable; the count is shown for balancing.
      options.push({ uid: insp.uid, disabled: false });
      const name = insp.nombre_completo || `Brigada ${insp.codigo || '—'}`;
      const code = insp.codigo ? ` — ${insp.codigo}` : '';
      return `<li role="option" class="asignacion-combo-option"
        data-uid="${escapeHtml(insp.uid)}"
        title="${escapeHtml(inspectorOptionLabel(insp, count))}">
        <span class="asignacion-combo-name">${escapeHtml(name + code)}</span>
        <span class="asignacion-combo-count">${count}</span></li>`;
    }).join('') || '<li class="asignacion-combo-empty" aria-disabled="true">Sin coincidencias</li>';
    active = -1;
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function highlight(from, step) {
    const items = [...list.querySelectorAll('.asignacion-combo-option')];
    if (!items.length) return;
    // Walk in `step` direction, skipping disabled options.
    let i = from;
    while (i >= 0 && i < options.length && options[i].disabled) i += step;
    if (i < 0 || i >= options.length) return;
    active = i;
    items.forEach((el, idx) => el.classList.toggle('is-active', idx === active));
    items[active].scrollIntoView({ block: 'nearest' });
  }

  function choose(uid, disabled) {
    if (disabled) return;
    close();
    onSelect(uid);
  }

  // On focus: select the text so typing replaces the current name, and show the
  // whole roster (query '') so another inspector is immediately pickable.
  input.addEventListener('focus', () => { input.select(); render(''); });
  input.addEventListener('input', () => render());
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); if (list.hidden) render(''); highlight(active < 0 ? 0 : active + 1, 1); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); highlight(active < 0 ? options.length - 1 : active - 1, -1); }
    else if (ev.key === 'Enter') { ev.preventDefault(); if (active >= 0 && options[active]) choose(options[active].uid, options[active].disabled); }
    else if (ev.key === 'Escape') { close(); }
  });
  list.addEventListener('mousedown', (ev) => {
    // mousedown (not click) so it fires before the input's blur closes the list.
    const li = ev.target.closest('.asignacion-combo-option');
    if (!li) return;
    ev.preventDefault();
    const opt = options.find((o) => o.uid === li.dataset.uid);
    choose(li.dataset.uid, opt ? opt.disabled : false);
  });
  input.addEventListener('blur', () => { setTimeout(close, 120); });
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
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.amber}"></span><span>Asignado</span></div>
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.yellow}"></span><span>En proceso</span></div>`;
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

/** initStickersAsignacion(root, { getToken }) — renders the sub-section once,
 *  fetches its own inspector roster + listPuntos+listCuadrillas, wires CRUD.
 *  Returns { reload } so web/js/stickers.js can re-fetch on subsequent opens
 *  instead of calling this twice (spec.md "Init runs once on first Asignación
 *  open"). */
export function initStickersAsignacion(root, { getToken }) {
  let rows = [];
  let cuadrillas = [];
  // Own copy of the roster (Phase 3: the Roster segment that used to preload
  // this for the whole Stickers tab moved to Planeación) — fetched once on
  // init and again on reload(), same lifecycle as `rows`/`cuadrillas`.
  let inspectoresCache = [];
  function getInspectores() { return inspectoresCache; }
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
  const gaugeEl = root.querySelector('#asignacion-gauge');

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
        // Isolate the filtered estado on the map too (the gauge keeps full totals).
        renderMapSection();
      });
    });
  }

  // Re-render every panel from the in-memory `rows`/`cuadrillas` — the same four
  // renders reload() runs after fetching, minus the ~6s listPuntos+listCuadrillas
  // round-trip. Used by the optimistic per-point/per-cuadrilla handlers below.
  function renderAll() {
    renderTable();
    renderCuadrillasSection();
    renderMapSection();
    renderGauge();
  }

  // Resolve a roster label the same way buildRows does (spec §3), from the live
  // getInspectores() roster; null uid -> em dash.
  function inspectorLabelFor(uid) {
    if (!uid) return '—';
    const insp = (getInspectores() || []).find((i) => i.uid === uid);
    return insp ? (insp.nombre_completo || insp.codigo || insp.uid) : '—';
  }

  // ---- optimistic local mutations (applied only after the API 200) ----
  // Each mutates the same row objects the renders read, then callers renderAll().
  function applyAsignar(cuadrillaId, uid) {
    const label = inspectorLabelFor(uid);
    for (const row of rows) {
      if (row.cuadrilla_id === cuadrillaId) {
        row.inspector_uid = uid;
        row.estado_asignacion = 'asignado';
        row.inspectorLabel = label;
        row.color = colorForPunto(row);
      }
    }
    const c = cuadrillas.find((x) => x.id === cuadrillaId);
    if (c) c.inspector_uid = uid;
  }

  function applyDesasignar(cuadrillaId) {
    for (const row of rows) {
      if (row.cuadrilla_id === cuadrillaId) {
        row.inspector_uid = null;
        row.estado_asignacion = 'pendiente';
        row.inspectorLabel = '—';
        row.color = colorForPunto(row);
      }
    }
    const c = cuadrillas.find((x) => x.id === cuadrillaId);
    if (c) c.inspector_uid = null;
  }

  function applyEliminar(cuadrillaId) {
    for (const row of rows) {
      if (row.cuadrilla_id === cuadrillaId) {
        row.cuadrilla_id = null;
        row.inspector_uid = null;
        row.estado_asignacion = 'pendiente';
        row.cuadrillaLabel = '—';
        row.inspectorLabel = '—';
        row.color = colorForPunto(row);
      }
    }
    cuadrillas = cuadrillas.filter((c) => c.id !== cuadrillaId);
  }

  async function reasignar(puntoId, nuevoInspectorUid) {
    try {
      await callApi(getToken, { action: 'reasignarPunto', punto_id: puntoId, nuevo_inspector_uid: nuevoInspectorUid });
      showOk('Punto reasignado.');
      const row = rows.find((r) => r.id === puntoId);
      if (row) {
        row.inspector_uid = nuevoInspectorUid;
        row.inspectorLabel = inspectorLabelFor(nuevoInspectorUid);
        row.color = colorForPunto(row);
      }
      renderAll();
    } catch (err) {
      alert(err.message); // rare path (network/permission); same idiom as stickers.js
    }
  }

  // Shared runner for the per-cuadrilla CRUD buttons/combobox: busy guard, then
  // apply the change locally + renderAll() on success (no full refetch). Same
  // idiom as the toolbar handlers, minus the ~6s reload.
  async function runCuadrillaAction(body, okMsg, applyLocal) {
    if (busy) return;
    busy = true;
    try {
      await callApi(getToken, body);
      showOk(okMsg);
      applyLocal();
      renderAll();
    } catch (err) {
      alert(err.message);
    } finally {
      busy = false;
    }
  }

  function renderCuadrillasSection() {
    // Roster completo para mostrar (nombre de un inspector ya asignado, aunque
    // hoy esté inhabilitado); solo los habilitados entran al combobox de
    // asignación — un inhabilitado no puede recibir cuadrillas nuevas.
    const inspectores = getInspectores() || [];
    const seleccionables = inspectores.filter(isHabilitado);
    const inspectorById = new Map(inspectores.map((i) => [i.uid, i]));
    const counts = activeCountsByInspector(rows);
    const cuadrillaById = new Map(cuadrillas.map((c) => [c.id, c]));
    const zonaByPunto = new Map(rows.map((r) => [r.id, r.zona]).filter(([, z]) => z));
    cuadrillasWrap.innerHTML = cuadrillasHtml(cuadrillas, inspectores, zonaByPunto, inspectorById);

    cuadrillasWrap.querySelectorAll('[data-combo-cuadrilla]').forEach((comboEl) => {
      const cuadrillaId = comboEl.dataset.comboCuadrilla;
      const cuadrilla = cuadrillaById.get(cuadrillaId);
      mountCombobox(comboEl, {
        inspectores: seleccionables,
        counts,
        cuadrillaPuntoIds: (cuadrilla && cuadrilla.puntos) || [],
        rows,
        onSelect: (uid) => runCuadrillaAction(
          { action: 'asignarInspector', cuadrilla_id: cuadrillaId, inspector_uid: uid },
          'Inspector asignado.',
          () => applyAsignar(cuadrillaId, uid),
        ),
      });
    });

    cuadrillasWrap.querySelectorAll('[data-desasignar]').forEach((btn) => {
      btn.addEventListener('click', () => runCuadrillaAction(
        { action: 'desasignarInspector', cuadrilla_id: btn.dataset.desasignar },
        'Asignación retirada; los puntos vuelven a pendiente.',
        () => applyDesasignar(btn.dataset.desasignar),
      ));
    });

    cuadrillasWrap.querySelectorAll('[data-eliminar]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (!window.confirm('Eliminar esta cuadrilla y liberar sus puntos a pendiente. ¿Continuar?')) return;
        runCuadrillaAction(
          { action: 'eliminarCuadrilla', cuadrilla_id: btn.dataset.eliminar },
          'Cuadrilla eliminada.',
          () => applyEliminar(btn.dataset.eliminar),
        );
      });
    });
  }

  function renderMapSection() {
    // Solo habilitados en el <select> "Reasignar a" del popup — mismo criterio
    // que el combobox de cuadrillas.
    const inspectores = (getInspectores() || []).filter(isHabilitado);
    const n = renderMap(currentRows(), inspectores, reasignar);
    const sinCoords = rows.length - n;
    mapMeta.textContent = sinCoords ? `${n} en el mapa · ${sinCoords} sin coordenadas` : `${n} en el mapa`;
  }

  // Semicircle SVG gauge of field-sweep progress, always over the UNFILTERED
  // rows (the estado filter narrows the map, not the overall progress). Three
  // stacked arcs (blue barrido / amber asignado / red pendiente) along a 180°
  // top semicircle; the barrido share fills from the left. No chart library.
  function renderGauge() {
    if (!gaugeEl) return;
    const { barrido, asignado, pendiente, total } = gaugeCounts(rows);
    const pct = total ? Math.round((barrido / total) * 100) : 0;
    const cx = 110;
    const cy = 104;
    const r = 84;
    const sw = 16;
    const pointAt = (f) => {
      const a = Math.PI * (1 - f); // f=0 -> left (π), f=1 -> right (0), sweeping over the top
      return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
    };
    const seg = (f0, f1, color) => {
      if (f1 - f0 <= 0.0001) return '';
      const [x0, y0] = pointAt(f0);
      const [x1, y1] = pointAt(f1);
      return `<path d="M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" fill="none" stroke="${color}" stroke-width="${sw}"/>`;
    };
    const [tx0, ty0] = pointAt(0);
    const [tx1, ty1] = pointAt(1);
    const track = `<path d="M ${tx0.toFixed(1)} ${ty0.toFixed(1)} A ${r} ${r} 0 0 1 ${tx1.toFixed(1)} ${ty1.toFixed(1)}" fill="none" stroke="var(--surface-3)" stroke-width="${sw}" stroke-linecap="round"/>`;
    let arcs = '';
    if (total) {
      const fB = barrido / total;
      const fA = fB + asignado / total;
      const fP = fA + pendiente / total;
      arcs = seg(0, fB, MARKER_HEX.blue) + seg(fB, fA, MARKER_HEX.amber) + seg(fA, fP, MARKER_HEX.red);
    }
    gaugeEl.innerHTML = `
      <svg class="asignacion-gauge-svg" viewBox="0 0 220 128" role="img" aria-label="Avance del barrido: ${pct}%">
        ${track}${arcs}
        <text x="${cx}" y="${cy - 12}" class="asignacion-gauge-pct" text-anchor="middle">${pct}%</text>
        <text x="${cx}" y="${cy + 8}" class="asignacion-gauge-cap" text-anchor="middle">${barrido} de ${total} barridos</text>
      </svg>
      <div class="asignacion-gauge-legend">
        <span class="asignacion-gauge-item"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.blue}"></span>Barrido ${barrido}</span>
        <span class="asignacion-gauge-item"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.amber}"></span>Asignado ${asignado}</span>
        <span class="asignacion-gauge-item"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.red}"></span>Pendiente ${pendiente}</span>
      </div>`;
  }

  async function reload() {
    showOk('');
    tableWrap.innerHTML = '<p class="sticker-loading">Cargando asignación…</p>';
    try {
      const [{ inspectores }, { puntos }, { cuadrillas: cuadrillasResp }] = await Promise.all([
        callStickersApi(getToken, { action: 'list' }),
        callApi(getToken, { action: 'listPuntos' }),
        callApi(getToken, { action: 'listCuadrillas' }),
      ]);
      inspectoresCache = inspectores || [];
      cuadrillas = cuadrillasResp;
      rows = buildRows(puntos, cuadrillas, getInspectores() || []);
      selected.clear();
      crearBtn.disabled = true;
      renderTable();
      renderCuadrillasSection();
      renderMapSection();
      renderGauge();
    } catch (err) {
      teardownMap();
      tableWrap.innerHTML = `<p class="sticker-error" role="alert">${escapeHtml(err.message)}</p>`;
    }
  }

  autoBtn.addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    autoBtn.disabled = true;
    const originalLabel = autoBtn.innerHTML;
    autoBtn.innerHTML = '<span class="asignacion-spinner" aria-hidden="true"></span>Agrupando…';
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
      autoBtn.innerHTML = originalLabel;
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
