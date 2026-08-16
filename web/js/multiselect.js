// Generic searchable multi-select dropdown: trigger button + floating panel.
// Reused by filters.js, one instance per filterable field.
import { escapeHtml, normalize } from './utils.js';

let installed = false;

/** One delegated document click listener for every multiselect instance, ever —
 *  installed once. Re-rendering a field (e.g. "Limpiar filtros") would otherwise
 *  add a fresh document-level listener per render, accumulating unboundedly and
 *  keeping detached DOM subtrees alive via closure. */
function installOutsideClickHandler() {
  if (installed) return;
  installed = true;
  document.addEventListener('click', (e) => {
    document.querySelectorAll('.filter-options.is-open').forEach((panel) => {
      const container = panel.closest('.filter-block') || panel.parentElement;
      if (container && !container.contains(e.target)) panel.classList.remove('is-open');
    });
  });
}

/**
 * @param {HTMLElement} root - mount point (becomes the `.filter-block` container)
 * @param {{
 *   field: string,
 *   label: string,
 *   options: Array<{ value: string, label: string }>,
 *   selectedSet: Set<string>,
 *   onToggle: (value: string) => void,
 * }} config
 * @returns {{ refresh: () => void }} call refresh() after the store mutates externally
 *   (e.g. a chip removed this field's value) so the checkbox states and count badge
 *   stay in sync even while the panel is open.
 */
export function renderMultiSelect(root, { field, label, options, selectedSet, onToggle }) {
  root.innerHTML = `
    <button type="button" class="filter-toggle" data-toggle="${escapeHtml(field)}">
      <span>${escapeHtml(label)}</span>
      <span class="filter-count" data-count></span>
    </button>
    <div class="filter-options" data-panel>
      <input type="search" class="ms-search" placeholder="Buscar…" aria-label="Buscar ${escapeHtml(label)}" data-ms-search>
      <div class="ms-options" data-ms-list></div>
    </div>
  `;

  const toggleBtn = root.querySelector('[data-toggle]');
  const panel = root.querySelector('[data-panel]');
  const countEl = root.querySelector('[data-count]');
  const searchInput = root.querySelector('[data-ms-search]');
  const listEl = root.querySelector('[data-ms-list]');

  function renderList(filterText) {
    const q = normalize(filterText || '');
    const visible = q ? options.filter((o) => normalize(o.label).includes(q)) : options;
    listEl.innerHTML = visible.map((o) => `
      <label class="filter-option">
        <input type="checkbox" value="${escapeHtml(o.value)}" ${selectedSet.has(o.value) ? 'checked' : ''}>
        <span>${escapeHtml(o.label)}</span>
      </label>
    `).join('') || '<p class="filter-empty">Sin resultados</p>';
    listEl.querySelectorAll('input[type=checkbox]').forEach((cb) => {
      cb.addEventListener('change', () => onToggle(cb.value));
    });
  }

  function refresh() {
    countEl.textContent = selectedSet.size > 0 ? String(selectedSet.size) : '';
    renderList(searchInput.value);
  }

  toggleBtn.addEventListener('click', () => {
    const isOpen = panel.classList.toggle('is-open');
    if (isOpen) {
      searchInput.value = '';
      renderList('');
      searchInput.focus();
    }
  });
  searchInput.addEventListener('input', () => renderList(searchInput.value));
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      panel.classList.remove('is-open');
      toggleBtn.focus();
    }
  });
  installOutsideClickHandler();

  refresh();
  return { refresh };
}
