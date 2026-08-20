// Results table: sortable columns, column visibility control, detail modal.
import {
  labelForField, labelForCode, formatValue, escapeHtml, DETAIL_GROUPS, BADGE_FIELDS, normalize,
} from './utils.js';
import { buildMiniMap, highlightRecord } from './mapview.js';

const CANONICAL_ORDER = [
  'ObjectID', 'GlobalID', 'fecha_inspeccion', 'hora', 'fecha_hora', 'nombre_evaluador', 'id_grupo',
  'entidad', 'tipo_evento', 'nombre_edificacion', 'municipio', 'barrio_vereda', 'direccion',
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
  'habitabilidad_calc', 'criterio_habitabilidad', 'suspension_servicios', 'justificacion_criterio',
  'requiere_evaluacion_adicional', 'eval_estructural', 'eval_geotecnica', 'eval_otra',
  'recomendaciones', 'aislamiento', 'intervencion_entades', 'observaciones_generales',
  'evento_id', 'gps_precision_m', 'CreationDate', 'Creator', 'EditDate', 'Editor', 'x', 'y',
  'comuna', 'barrio_geo',
];

const DEFAULT_VISIBLE = [
  'fecha_inspeccion', 'nombre_edificacion', 'barrio_vereda', 'direccion', 'uso_edificacion',
  'nivel_dano', 'criterio_habitabilidad', 'suspension_servicios', 'n_muertos', 'n_heridos',
  'observaciones_generales', 'recomendaciones',
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

  els.tbody.querySelectorAll('tr[data-object-id]').forEach((tr) => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('[data-expandable]')) {
        e.target.closest('[data-expandable]').classList.toggle('is-expanded');
        return;
      }
      const id = tr.dataset.objectId;
      const record = sorted.find((r) => String(r.ObjectID) === id);
      if (record && onRowClick) onRowClick(record);
    });
    tr.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        tr.click();
      }
    });
  });
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
    <div class="detail-minimap" data-minimap></div>
    ${orderedGroupNames.map((group) => `
      <section class="detail-group">
        <h3>${escapeHtml(group)}</h3>
        <dl class="detail-fields">
          ${groups[group].map((f) => `
            <div class="detail-field">
              <dt>${escapeHtml(labelForField(f))}</dt>
              <dd>${BADGE_FIELDS.has(f) ? renderBadge(f, record[f]) : escapeHtml(formatValue(f, record[f]))}</dd>
            </div>
          `).join('')}
        </dl>
      </section>
    `).join('')}
  `;

  const miniMapEl = body.querySelector('[data-minimap]');
  buildMiniMap(miniMapEl, record);

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
