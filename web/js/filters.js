// Global filter panel: date range + searchable multi-select dropdowns + numeric
// range controls + active chips. Single DOM instance reused for desktop sidebar
// and mobile bottom sheet (CSS only).
import { FILTER_FIELDS, FILTER_GROUPS, RANGE_FIELDS } from './data.js';
import { renderMultiSelect } from './multiselect.js';
import { labelForCode, escapeHtml, sourceLabel } from './utils.js';

const NONE = '__none__';

let subscribed = false;
let msHandles = {};
let rangeHandles = {};

export function initFilters(root, store, chipsRoot) {
  render(root, store, chipsRoot);
  if (!subscribed) {
    store.subscribe(() => syncUi(root, store, chipsRoot));
    subscribed = true;
  }
}

function optionLabel(def, value) {
  return value === NONE ? (def.emptyLabel || 'Sin dato') : labelForCode(value);
}

function buildOptions(def, store) {
  return (store.options[def.field] || []).map((val) => ({ value: val, label: optionLabel(def, val) }));
}

/** Two-input (Mín/Máx) collapsible range control. Reuses `.filter-toggle` /
 *  `.filter-options` so multiselect.js's delegated outside-click handler
 *  (which targets any `.filter-options.is-open`) closes this panel too. */
function renderRangeFilter(root, { label, bounds, value, onChange }) {
  root.innerHTML = `
    <button type="button" class="filter-toggle" data-toggle>
      <span>${escapeHtml(label)}</span>
      <span class="filter-count" data-count></span>
    </button>
    <div class="filter-options range-options" data-panel>
      <div class="range-inputs">
        <label class="range-field">
          <span>Mín</span>
          <input type="number" data-range-min placeholder="${bounds.min ?? ''}" value="${value.min ?? ''}">
        </label>
        <label class="range-field">
          <span>Máx</span>
          <input type="number" data-range-max placeholder="${bounds.max ?? ''}" value="${value.max ?? ''}">
        </label>
      </div>
    </div>
  `;

  const toggleBtn = root.querySelector('[data-toggle]');
  const panel = root.querySelector('[data-panel]');
  const countEl = root.querySelector('[data-count]');
  const minInput = root.querySelector('[data-range-min]');
  const maxInput = root.querySelector('[data-range-max]');

  toggleBtn.addEventListener('click', () => panel.classList.toggle('is-open'));
  minInput.addEventListener('change', () => onChange('min', minInput.value));
  maxInput.addEventListener('change', () => onChange('max', maxInput.value));
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      panel.classList.remove('is-open');
      toggleBtn.focus();
    }
  });

  function refresh() {
    const activeBounds = (value.min !== null ? 1 : 0) + (value.max !== null ? 1 : 0);
    countEl.textContent = activeBounds > 0 ? String(activeBounds) : '';
    const minStr = value.min ?? '';
    const maxStr = value.max ?? '';
    if (minInput.value !== String(minStr)) minInput.value = minStr;
    if (maxInput.value !== String(maxStr)) maxInput.value = maxStr;
  }

  refresh();
  return { refresh };
}

function render(root, store, chipsRoot) {
  const groupBlocksHtml = FILTER_GROUPS.map((g) => {
    const fields = FILTER_FIELDS.filter((def) => def.group === g.key);
    if (!fields.length) return '';
    return `
      <div class="filter-group">
        <h3 class="filter-group-title">${escapeHtml(g.label)}</h3>
        ${fields.map((def) => `<div class="filter-block" data-filter-field="${escapeHtml(def.field)}"></div>`).join('')}
      </div>
    `;
  }).join('');

  const rangeGroupHtml = RANGE_FIELDS.length ? `
    <div class="filter-group">
      <h3 class="filter-group-title">Rangos</h3>
      ${RANGE_FIELDS.map((def) => `<div class="filter-block" data-range-field="${escapeHtml(def.field)}"></div>`).join('')}
    </div>
  ` : '';

  root.innerHTML = `
    <div class="filters-header">
      <h2>Filtros</h2>
      <button type="button" class="btn-clear" data-clear-filters>Limpiar filtros</button>
    </div>
    <div class="filter-block">
      <label class="filter-label" for="date-from">Desde</label>
      <input type="date" id="date-from" data-date-from
        min="${store.dateBounds.min || ''}" max="${store.dateBounds.max || ''}"
        value="${store.filters.dateFrom || ''}">
      <label class="filter-label" for="date-to">Hasta</label>
      <input type="date" id="date-to" data-date-to
        min="${store.dateBounds.min || ''}" max="${store.dateBounds.max || ''}"
        value="${store.filters.dateTo || ''}">
    </div>
    ${groupBlocksHtml}
    ${rangeGroupHtml}
  `;

  root.querySelector('[data-date-from]').addEventListener('change', (e) => {
    store.setFilter('dateFrom', e.target.value || null);
  });
  root.querySelector('[data-date-to]').addEventListener('change', (e) => {
    store.setFilter('dateTo', e.target.value || null);
  });
  root.querySelector('[data-clear-filters]').addEventListener('click', () => {
    store.clearFilters();
    // Full re-render resets every dropdown panel, date/range inputs and chip row
    // at once — avoids the desync the old manual-checkbox-reset approach was prone to.
    render(root, store, chipsRoot);
  });

  msHandles = {};
  FILTER_FIELDS.forEach((def) => {
    const fieldRoot = root.querySelector(`[data-filter-field="${def.field}"]`);
    msHandles[def.field] = renderMultiSelect(fieldRoot, {
      field: def.field,
      label: sourceLabel(def.field, def.label),
      options: buildOptions(def, store),
      selectedSet: store.filters[def.field],
      onToggle: (value) => store.toggleMultiFilter(def.field, value),
    });
  });

  rangeHandles = {};
  RANGE_FIELDS.forEach((def) => {
    const fieldRoot = root.querySelector(`[data-range-field="${def.field}"]`);
    rangeHandles[def.field] = renderRangeFilter(fieldRoot, {
      label: def.label,
      bounds: store.rangeBounds[def.field] || { min: null, max: null },
      value: store.filters[def.field],
      onChange: (part, val) => store.setRangeFilter(def.field, part, val),
    });
  });

  renderChips(chipsRoot, store);
}

/** Keep every derived UI surface in sync after any store mutation (chip removal,
 *  a checkbox toggled in one panel while another is open, date/range auto-swap,
 *  etc.) — without tearing down open dropdown panels. */
function syncUi(root, store, chipsRoot) {
  Object.values(msHandles).forEach((h) => h.refresh());
  Object.values(rangeHandles).forEach((h) => h.refresh());

  const dateFromEl = root.querySelector('[data-date-from]');
  const dateToEl = root.querySelector('[data-date-to]');
  if (dateFromEl && dateFromEl.value !== (store.filters.dateFrom || '')) dateFromEl.value = store.filters.dateFrom || '';
  if (dateToEl && dateToEl.value !== (store.filters.dateTo || '')) dateToEl.value = store.filters.dateTo || '';

  const badge = document.querySelector('[data-active-filter-count]');
  if (badge) {
    const n = store.activeFilterCount();
    badge.textContent = n > 0 ? String(n) : '';
    badge.classList.toggle('is-visible', n > 0);
  }

  renderChips(chipsRoot, store);
}

function rangeChipText(def, range) {
  if (range.min !== null && range.max !== null) return `${def.label}: ${range.min}–${range.max}`;
  if (range.min !== null) return `${def.label}: ≥${range.min}`;
  return `${def.label}: ≤${range.max}`;
}

function renderChips(chipsRoot, store) {
  if (!chipsRoot) return;
  const chips = [];

  for (const def of FILTER_FIELDS) {
    for (const value of store.filters[def.field]) {
      chips.push({
        text: `${sourceLabel(def.field, def.label)}: ${optionLabel(def, value)}`,
        onRemove: () => store.toggleMultiFilter(def.field, value),
      });
    }
  }
  for (const def of RANGE_FIELDS) {
    const range = store.filters[def.field];
    if (range.min === null && range.max === null) continue;
    chips.push({
      text: rangeChipText(def, range),
      onRemove: () => store.clearRangeFilter(def.field),
    });
  }
  if (store.filters.dateFrom || store.filters.dateTo) {
    const from = store.filters.dateFrom || '…';
    const to = store.filters.dateTo || '…';
    chips.push({
      text: `Fecha: ${from} – ${to}`,
      onRemove: () => { store.setFilter('dateFrom', null); store.setFilter('dateTo', null); },
    });
  }
  if (store.filters.search) {
    chips.push({
      text: `Búsqueda: "${store.filters.searchRaw}"`,
      onRemove: () => store.setSearch(''),
    });
  }

  chipsRoot.innerHTML = chips.map((c, i) => `
    <span class="chip" data-chip-index="${i}">
      ${escapeHtml(c.text)}
      <button type="button" class="chip-remove" data-chip-remove="${i}" aria-label="Quitar filtro">×</button>
    </span>
  `).join('');

  chipsRoot.querySelectorAll('[data-chip-remove]').forEach((btn) => {
    const idx = Number(btn.dataset.chipRemove);
    btn.addEventListener('click', () => chips[idx].onRemove());
  });
}
