// Results table: sortable columns, column visibility control, detail modal.
import {
  labelForField, labelForCode, formatValue, escapeHtml, DETAIL_GROUPS, BADGE_FIELDS, normalize,
  barrioVeredaDisplay,
} from './utils.js';
import { buildMiniMap, highlightRecord } from './mapview.js';

const CANONICAL_ORDER = [
  'ObjectID', 'GlobalID', 'fecha_inspeccion', 'hora', 'fecha_hora', 'nombre_evaluador', 'id_grupo',
  'entidad', 'tipo_evento', 'nombre_edificacion', 'municipio', 'barrio_vereda_resuelto', 'barrio_vereda',
  'direccion',
  'tipo_propiedad', 'relacion_edificacion', 'otro', 'epoca_construccion', 'n_pisos', 'n_sotanos',
  'n_ocupantes', 'frente', 'fondo', 'n_residenciales', 'n_comerciales', 'n_no_habitadas',
  'n_muertos', 'n_heridos', 'acceso_edificacion', 'uso_edificacion', 'uso_cual', 'sistema_estructural',
  'sistema_estructural_cual', 'material_estructura', 'material_entrepiso', 'sistema_entrepiso',
  'sistema_entrepiso_cual', 'existen_sistemas_combinados', 'observaciones', 'sistema_cubierta',
  'sistema_cubierta_cual', 'revestimiento_cubierta', 'revestimiento_cubierta_cual',
  'sistema_muros_divisorios', 'sistema_muros_divisorios_cual', 'fachadas', 'fachadas_cual',
  'escaleras', 'escaleras_cual', 'calidad_construccion', 'estado_edificacion',
  '46_especifique_cual', 'colapso_total', 'colapso_parcial', 'asentamiento_severo',
  'inclinacion_importante', 'suelo_inestable', 'riesgo_caida', 'danos_estructura',
  'danos_contrapiso_entrepiso_muroscont', 'danos_muro_div', 'danos_cubierta', 'cielos_instalaciones',
  'alc_exterior', 'alc_interior', 'matriz_ref', 'afectacion_planta', 'afectacion_planta_calc',
  'severidad_danos', 'severidad_danos_calc', 'nivel_dano', 'riesgo_ab', 'riesgo_ac',
  'habitabilidad_calc', 'criterio_habitabilidad', 'suspension_servicios', 'sticker', 'justificacion_criterio',
  'requiere_evaluacion_adicional', 'eval_estructural', 'eval_geotecnica', 'eval_otra',
  'recomendaciones', 'aislamiento', 'intervencion_entades', 'observaciones_generales',
  'evento_id', 'gps_precision_m', 'CreationDate', 'Creator', 'EditDate', 'Editor', 'x', 'y',
  'comuna', 'barrio_geo', 'barrio_vereda_fuente',
];

const DEFAULT_VISIBLE = [
  'fecha_inspeccion', 'nombre_edificacion', 'barrio_vereda_resuelto', 'direccion', 'uso_edificacion',
  'nivel_dano', 'criterio_habitabilidad', 'suspension_servicios', 'sticker',
  'justificacion_criterio', 'observaciones_generales', 'recomendaciones',
];

const LONG_TEXT_FIELDS = new Set([
  'observaciones', 'observaciones_generales', 'recomendaciones', 'justificacion_criterio',
  'direccion', 'nombre_edificacion',
]);

let allFields = [...CANONICAL_ORDER];
let visibleColumns = [...DEFAULT_VISIBLE];
let sortField = 'fecha_inspeccion';
let sortDir = 'desc';
let els = null;
let onRowClick = null;
let lastRecords = [];
let lastSorted = []; // current render's sorted rows, read by the delegated row handler
let totalRecords = null;

function discoverFields(records) {
  const found = new Set();
  for (const r of records) {
    for (const k in r) {
      if (k === '_search') continue;
      found.add(k);
    }
  }
  const rest = [...found].filter((f) => !CANONICAL_ORDER.includes(f));
  allFields = [...CANONICAL_ORDER.filter((f) => found.has(f)), ...rest];
}

export function initTable(root, allRecords, callbacks) {
  onRowClick = callbacks.onRowClick;
  discoverFields(allRecords);
  els = {
    root,
    columnsBtn: root.querySelector('[data-columns-btn]'),
    columnsPopover: root.querySelector('[data-columns-popover]'),
    theadRow: root.querySelector('thead tr'),
    tbody: root.querySelector('tbody'),
    countEl: root.querySelector('[data-row-count]'),
    scrollWrap: root.querySelector('.table-scroll'),
  };
  buildColumnsPopover();
  wireColumnsToggle();
  wireRowDelegation();
}

// One delegated click+keydown listener on the tbody instead of two listeners
// per row (854 records x 2 = ~1700 attaches). Behavior-identical: same
// modal-open / [data-expandable] toggle / Enter-Space dispatch as before,
// just resolved via event.target.closest() against the row that's actually
// still in the DOM after the current render (see lastSorted).
function wireRowDelegation() {
  els.tbody.addEventListener('click', (e) => {
    const expandable = e.target.closest('[data-expandable]');
    if (expandable && els.tbody.contains(expandable)) {
      expandable.classList.toggle('is-expanded');
      return;
    }
    const tr = e.target.closest('tr[data-object-id]');
    if (!tr) return;
    const id = tr.dataset.objectId;
    const record = lastSorted.find((r) => String(r.ObjectID) === id);
    if (record && onRowClick) onRowClick(record);
  });
  els.tbody.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const tr = e.target.closest('tr[data-object-id]');
    if (!tr) return;
    e.preventDefault();
    tr.click(); // re-dispatches a click event that bubbles to the handler above
  });
}

function buildColumnsPopover() {
  els.columnsPopover.innerHTML = allFields.map((f) => `
    <label class="columns-item">
      <input type="checkbox" data-field="${escapeHtml(f)}" ${visibleColumns.includes(f) ? 'checked' : ''}>
      <span>${escapeHtml(labelForField(f))}</span>
    </label>
  `).join('');
  els.columnsPopover.addEventListener('change', (e) => {
    const input = e.target.closest('input[data-field]');
    if (!input) return;
    const field = input.dataset.field;
    if (input.checked) {
      if (!visibleColumns.includes(field)) visibleColumns.push(field);
    } else {
      visibleColumns = visibleColumns.filter((f) => f !== field);
    }
    renderTable(lastRecords);
  });
}

function wireColumnsToggle() {
  els.columnsBtn.addEventListener('click', () => {
    els.columnsPopover.classList.toggle('is-open');
  });
  document.addEventListener('click', (e) => {
    if (!els.columnsPopover.classList.contains('is-open')) return;
    if (els.columnsPopover.contains(e.target) || els.columnsBtn.contains(e.target)) return;
    els.columnsPopover.classList.remove('is-open');
  });
}

const isEmptyVal = (v) => v === null || v === undefined || String(v).trim() === '';

function compareValues(a, b, field) {
  const va = a[field];
  const vb = b[field];
  const na = Number(va);
  const nb = Number(vb);
  if (!Number.isNaN(na) && !Number.isNaN(nb) && String(va).trim() !== '' && String(vb).trim() !== '') {
    return na - nb;
  }
  return String(va).localeCompare(String(vb), 'es');
}

function renderHeader() {
  els.theadRow.innerHTML = visibleColumns.map((f) => {
    const active = f === sortField;
    const arrow = active ? (sortDir === 'asc' ? '&uarr;' : '&darr;') : '';
    return `<th data-sort-field="${escapeHtml(f)}" class="${active ? 'is-sorted' : ''}">
      <button type="button" class="th-sort-btn">${escapeHtml(labelForField(f))} <span class="sort-arrow">${arrow}</span></button>
    </th>`;
  }).join('');
  els.theadRow.querySelectorAll('[data-sort-field]').forEach((th) => {
    th.addEventListener('click', () => {
      const field = th.dataset.sortField;
      if (sortField === field) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
      else { sortField = field; sortDir = 'asc'; }
      renderTable(lastRecords);
    });
  });
}

function renderBadge(field, value) {
  const code = normalize(value);
  return `<span class="badge badge-${escapeHtml(field)}-${escapeHtml(code || 'none')}">${escapeHtml(labelForCode(value))}</span>`;
}

function renderCell(record, field) {
  const value = record[field];
  if (field === 'barrio_vereda_resuelto') return escapeHtml(barrioVeredaDisplay(record));
  if (BADGE_FIELDS.has(field) && value) return renderBadge(field, value);
  const text = escapeHtml(formatValue(field, value));
  if (LONG_TEXT_FIELDS.has(field)) {
    return `<div class="cell-clamp" data-expandable>${text}</div>`;
  }
  return text;
}

export function renderTable(records) {
  lastRecords = records;
  renderHeader();
  const sorted = [...records].sort((a, b) => {
    // Empties always sink to the bottom, regardless of sort direction, so the
    // ~123 registros sin fecha_inspeccion no tapan las fechas reales al ordenar desc.
    const ea = isEmptyVal(a[sortField]);
    const eb = isEmptyVal(b[sortField]);
    if (ea && eb) return 0;
    if (ea) return 1;
    if (eb) return -1;
    const cmp = compareValues(a, b, sortField);
    return sortDir === 'asc' ? cmp : -cmp;
  });

  if (sorted.length === 0) {
    els.tbody.innerHTML = `<tr class="empty-row"><td colspan="${visibleColumns.length || 1}">
      <div class="empty-state">
        <strong>No hay registros para estos filtros.</strong>
        <span>Ajusta los filtros o la búsqueda para ver resultados.</span>
      </div>
    </td></tr>`;
  } else {
    els.tbody.innerHTML = sorted.map((r) => `
      <tr data-object-id="${escapeHtml(String(r.ObjectID))}" tabindex="0">
        ${visibleColumns.map((f) => `<td>${renderCell(r, f)}</td>`).join('')}
      </tr>
    `).join('');
  }

  els.countEl.textContent = `Mostrando ${sorted.length} de ${totalRecords ?? sorted.length}`;
  lastSorted = sorted; // read by the delegated row handler (see wireRowDelegation)
}

export function setTotalRecords(n) {
  totalRecords = n;
}

/* ------------------------------------------------------------------ */
/* Detail modal                                                        */
/* ------------------------------------------------------------------ */

function groupForField(field) {
  for (const [group, fields] of Object.entries(DETAIL_GROUPS)) {
    if (fields.includes(field)) return group;
  }
  if (/^\d+_[a-z]+$/i.test(field)) return 'Riesgos externos';
  return 'Otros';
}

// Layer público de Survey123: sus adjuntos (fotos) son accesibles sin token en
// {LAYER}/{objectid}/attachments. Se muestran en el modal, junto al minimapa.
const SURVEY_LAYER_URL = 'https://services8.arcgis.com/ljfiJpg35HWgdtaC/arcgis/rest/services/service_16fa1d2000ea4fa68304bc030a95e8d1/FeatureServer/0';

async function loadPhotos(objectId, container) {
  if (!container || objectId == null) return;
  try {
    const res = await fetch(`${SURVEY_LAYER_URL}/${objectId}/attachments?f=json`);
    const json = await res.json();
    const infos = (json.attachmentInfos || []).filter((a) => (a.contentType || '').startsWith('image'));
    // Las fotos del formulario se llaman foto_*; la firma del evaluador es firma-*
    // (pequeña). Mostramos las fotos primero y grandes, la firma al final y chica.
    const isFirma = (a) => /^firma/i.test(a.name || '');
    const ordered = [...infos.filter((a) => !isFirma(a)), ...infos.filter(isFirma)];
    if (!ordered.length) {
      container.innerHTML = '<span class="detail-photos-empty">Sin fotos en el survey.</span>';
      return;
    }
    const urls = ordered.map((a) => `${SURVEY_LAYER_URL}/${objectId}/attachments/${a.id}`);
    container.innerHTML = ordered.map((a, i) => {
      const firma = isFirma(a);
      return `<button type="button" class="detail-photo${firma ? ' detail-photo-firma' : ''}" data-idx="${i}" title="${firma ? 'Firma del evaluador' : 'Foto de la inspección'}">
        <img src="${escapeHtml(urls[i])}" alt="${firma ? 'Firma del evaluador' : 'Foto de la inspección'}" loading="lazy">
      </button>`;
    }).join('');
    container.querySelectorAll('.detail-photo').forEach((btn) => {
      btn.addEventListener('click', () => openLightbox(urls, Number(btn.dataset.idx)));
    });
  } catch {
    container.innerHTML = '<span class="detail-photos-empty">No se pudieron cargar las fotos.</span>';
  }
}

let lightboxEl = null;
const lbState = { urls: [], idx: 0 };

function lbShow() {
  if (!lightboxEl) return;
  lightboxEl.querySelector('img').src = lbState.urls[lbState.idx] || '';
  const multi = lbState.urls.length > 1;
  lightboxEl.querySelectorAll('[data-lb-prev], [data-lb-next], .photo-lightbox-count')
    .forEach((el) => { el.style.display = multi ? '' : 'none'; });
  const count = lightboxEl.querySelector('.photo-lightbox-count');
  if (count) count.textContent = `${lbState.idx + 1} / ${lbState.urls.length}`;
}
function lbStep(delta) {
  if (!lbState.urls.length) return;
  lbState.idx = (lbState.idx + delta + lbState.urls.length) % lbState.urls.length;
  lbShow();
}

/** Full-screen photo viewer. Shared with the Stickers tab's evaluation
 *  detail modal so both photo galleries behave identically. */
export function openLightbox(urls, startIndex) {
  lbState.urls = Array.isArray(urls) ? urls : [urls];
  lbState.idx = startIndex || 0;
  if (!lightboxEl) {
    lightboxEl = document.createElement('div');
    lightboxEl.className = 'photo-lightbox';
    lightboxEl.innerHTML = `
      <button type="button" class="photo-lightbox-nav" data-lb-prev aria-label="Anterior" title="Anterior">‹</button>
      <img alt="Foto ampliada">
      <button type="button" class="photo-lightbox-nav" data-lb-next aria-label="Siguiente" title="Siguiente">›</button>
      <div class="photo-lightbox-actions">
        <span class="photo-lightbox-count"></span>
        <button type="button" data-lb-full aria-label="Pantalla completa" title="Pantalla completa">⛶</button>
        <button type="button" data-lb-close aria-label="Cerrar" title="Cerrar">✕</button>
      </div>`;
    document.body.appendChild(lightboxEl);
    const close = () => lightboxEl.classList.remove('is-open');
    lightboxEl.addEventListener('click', (e) => {
      if (e.target === lightboxEl || e.target.closest('[data-lb-close]')) close();
      else if (e.target.closest('[data-lb-prev]')) lbStep(-1);
      else if (e.target.closest('[data-lb-next]')) lbStep(1);
    });
    lightboxEl.querySelector('[data-lb-full]').addEventListener('click', () => {
      const img = lightboxEl.querySelector('img');
      if (lightboxEl.requestFullscreen) lightboxEl.requestFullscreen().catch(() => {});
      else if (img.requestFullscreen) img.requestFullscreen().catch(() => {});
    });
    document.addEventListener('keydown', (e) => {
      if (!lightboxEl.classList.contains('is-open')) return;
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowLeft') lbStep(-1);
      else if (e.key === 'ArrowRight') lbStep(1);
    });
  }
  lbShow();
  lightboxEl.classList.add('is-open');
}

// Un edificio puede tener varias inspecciones (re-visitas, reenvíos). El
// pipeline agrupa por edificio y elige UNA para las cifras: por defecto la
// MÁS RECIENTE. Ninguna regla automática acierta siempre, así que desde acá
// se puede fijar a mano cuál cuenta.
//
// El bloque aparece SOLO en registros que pertenecen a un grupo con más de
// una inspección — en los otros 941 no habría nada que decidir y sería ruido.
function duplicadoHtml(record) {
  const n = Number(record.dup_n) || 1;
  if (n <= 1) return '';

  const esRep = record.es_representante !== false;
  const grupo = record.dup_grupo_id || '';
  const gid = record.GlobalID || '';

  return `
    <section class="detail-group detail-duplicado">
      <h3>Edificio con ${n} inspecciones</h3>
      <p class="detail-duplicado-nota">
        ${esRep
          ? 'Esta es la inspección que cuenta en las cifras del Panel.'
          : 'Las cifras del Panel usan otra inspección de este mismo edificio.'}
        Ninguna se borra: todas siguen en la tabla.
      </p>
      <div class="detail-duplicado-acciones">
        ${esRep
          ? `<button type="button" class="btn-secondary" data-quitar-representante
                     data-grupo="${escapeHtml(grupo)}">Volver a la regla automática</button>`
          : `<button type="button" class="btn-primary" data-fijar-representante
                     data-grupo="${escapeHtml(grupo)}" data-gid="${escapeHtml(gid)}">
               Usar esta inspección para las cifras
             </button>`}
        <span class="detail-duplicado-estado" data-duplicado-estado></span>
      </div>
    </section>
  `;
}

// Fija/quita el representante y RECARGA, porque cambiar cuál inspección
// cuenta cambia los KPIs, el mapa y los gráficos a la vez — reconstruirlos a
// mano desde acá sería fácil de desincronizar.
function wireDuplicadoAcciones(body) {
  const estado = body.querySelector('[data-duplicado-estado]');
  const fijar = body.querySelector('[data-fijar-representante]');
  const quitar = body.querySelector('[data-quitar-representante]');
  if (!fijar && !quitar) return;

  const llamar = async (btn, method, payload) => {
    btn.disabled = true;
    if (estado) estado.textContent = 'Guardando…';
    try {
      const token = await duplicadoDeps.getToken?.();
      if (!token) throw new Error('Sesión no disponible.');
      const res = await fetch(duplicadoDeps.endpoint, {
        method,
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (estado) estado.textContent = 'Listo. Actualizando cifras…';
      await duplicadoDeps.onChange?.();
    } catch (err) {
      // Se muestra el error en vez de tragárselo: si no se guardó, el usuario
      // debe saberlo — creer que fijó un dato y que no haya quedado es peor
      // que no tener el botón.
      if (estado) estado.textContent = `No se pudo guardar: ${err.message}`;
      btn.disabled = false;
    }
  };

  fijar?.addEventListener('click', () => llamar(fijar, 'POST', {
    dup_grupo_id: fijar.dataset.grupo, global_id: fijar.dataset.gid,
  }));
  quitar?.addEventListener('click', () => llamar(quitar, 'DELETE', {
    dup_grupo_id: quitar.dataset.grupo,
  }));
}

// Inyectado por main.js (initTable): el token y el recargador viven allá.
let duplicadoDeps = { endpoint: '/panel-representante' };
export function configurarRepresentante(deps) {
  duplicadoDeps = { ...duplicadoDeps, ...deps };
}

export function openDetailModal(record) {
  const modal = document.getElementById('detail-modal');
  const body = modal.querySelector('[data-modal-body]');
  const title = modal.querySelector('[data-modal-title]');
  title.textContent = record.nombre_edificacion || record.direccion || `Registro ${record.ObjectID}`;

  const groups = {};
  for (const field of allFields) {
    const value = record[field];
    if (value === null || value === undefined || value === '') continue;
    const group = groupForField(field);
    if (!groups[group]) groups[group] = [];
    groups[group].push(field);
  }

  const orderedGroupNames = [...Object.keys(DETAIL_GROUPS), 'Otros'].filter((g) => groups[g] && groups[g].length);

  body.innerHTML = `
    ${duplicadoHtml(record)}
    <div class="detail-media">
      <div class="detail-minimap" data-minimap></div>
      <div class="detail-photos" data-photos><span class="detail-photos-empty">Cargando fotos…</span></div>
    </div>
    ${orderedGroupNames.map((group) => `
      <section class="detail-group">
        <h3>${escapeHtml(group)}</h3>
        <dl class="detail-fields">
          ${groups[group].map((f) => `
            <div class="detail-field">
              <dt>${escapeHtml(labelForField(f))}</dt>
              <dd>${f === 'barrio_vereda_resuelto' ? escapeHtml(barrioVeredaDisplay(record))
                  : BADGE_FIELDS.has(f) ? renderBadge(f, record[f]) : escapeHtml(formatValue(f, record[f]))}</dd>
            </div>
          `).join('')}
        </dl>
      </section>
    `).join('')}
  `;

  const miniMapEl = body.querySelector('[data-minimap]');
  buildMiniMap(miniMapEl, record);
  loadPhotos(record.ObjectID, body.querySelector('[data-photos]'));

  wireDuplicadoAcciones(body);

  modal.classList.add('is-open');
  modal.setAttribute('aria-hidden', 'false');
  highlightRecord(record);

  const closeBtn = modal.querySelector('[data-modal-close]');
  const onClose = () => {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
  };
  closeBtn.onclick = onClose;
  modal.querySelector('.modal-backdrop').onclick = onClose;
}
