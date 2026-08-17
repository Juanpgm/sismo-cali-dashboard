// Entry point: wires data store, filters, KPIs, map and table together.
import { store } from './data.js';
import { initFilters } from './filters.js';
import { renderKpis } from './kpi.js';
import { renderStatistics } from './charts.js';
import {
  initMap, render as renderMap, setMode, setColorBy, setSizeBy, setHeatWeight,
  setChoroplethLevel, setChoroplethMetric, invalidateSize, highlightRecord,
} from './mapview.js';
import { initTable, renderTable, setTotalRecords, openDetailModal } from './table.js';
import { initAsignaciones, invalidateAsigSize } from './asignaciones.js';
import { debounce } from './utils.js';

const el = (sel) => document.querySelector(sel);

const kpiRow = el('#kpi-row');
const activeChipsEl = el('#active-chips');
const searchInput = el('#search-input');
const refreshBtn = el('#refresh-btn');
const refreshProgress = el('#refresh-progress');
const retryBtn = el('#retry-btn');
const errorOverlay = el('#error-overlay');
const errorMessage = el('[data-error-message]');
const toastStack = el('#toast-stack');
const lastUpdateEl = el('[data-last-update]');
const eventBadgeEl = el('[data-event-badge]');
const filtersPanel = el('#filters-panel');
const filtersOpenBtn = el('#filters-open-btn');
const drawerBackdrop = el('#drawer-backdrop');
const tableCard = el('#table-card');

let mapInitialized = false;

function showToast(message, variant = 'success') {
  const toast = document.createElement('div');
  toast.className = `toast toast-${variant}`;
  toast.textContent = message;
  toastStack.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('is-visible'));
  setTimeout(() => {
    toast.classList.remove('is-visible');
    setTimeout(() => toast.remove(), 300);
  }, 3800);
}

function formatGeneratedAt(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('es-CO', {
    year: 'numeric', month: 'long', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function renderHeaderMeta() {
  if (!store.meta) return;
  lastUpdateEl.textContent = `Última actualización: ${formatGeneratedAt(store.meta.generated_at)}`;
  eventBadgeEl.textContent = `Evento ${store.meta.event_id ?? '—'}`;
}

function onStoreChange() {
  if (searchInput.value !== (store.filters.searchRaw || '')) {
    searchInput.value = store.filters.searchRaw || '';
  }
  renderKpis(kpiRow, store.filtered, store.records);
  setTotalRecords(store.records.length);
  renderTable(store.filtered);
  renderMap(store.filtered).catch((err) => {
    console.error(err);
    showToast('No se pudo cargar la capa geográfica.', 'error');
  });
  renderStatistics(store.filtered);
}

function openFiltersDrawer() {
  filtersPanel.classList.add('is-open');
  drawerBackdrop.classList.add('is-open');
}
function closeFiltersDrawer() {
  filtersPanel.classList.remove('is-open');
  drawerBackdrop.classList.remove('is-open');
}

function wireMapControls() {
  document.querySelectorAll('[data-map-mode]').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-map-mode]').forEach((b) => {
        b.classList.remove('is-active');
        b.setAttribute('aria-selected', 'false');
      });
      btn.classList.add('is-active');
      btn.setAttribute('aria-selected', 'true');
      const mode = btn.dataset.mapMode;
      setMode(mode);
      document.querySelectorAll('[data-mode-controls]').forEach((panel) => {
        panel.classList.toggle('is-hidden', panel.dataset.modeControls !== mode);
      });
      renderMap(store.filtered);
      setTimeout(invalidateSize, 60);
    });
  });

  el('#color-by-select').addEventListener('change', (e) => {
    setColorBy(e.target.value);
    renderMap(store.filtered);
  });
  el('#size-by-select').addEventListener('change', (e) => {
    setSizeBy(e.target.value);
    renderMap(store.filtered);
  });
  el('#heat-weight-select').addEventListener('change', (e) => {
    setHeatWeight(e.target.value);
    renderMap(store.filtered);
  });
  el('#choropleth-level-select').addEventListener('change', (e) => {
    setChoroplethLevel(e.target.value);
    renderMap(store.filtered).catch(() => showToast('No se pudo cargar el nivel geográfico.', 'error'));
  });
  el('#choropleth-metric-select').addEventListener('change', (e) => {
    setChoroplethMetric(e.target.value);
    renderMap(store.filtered);
  });
}

function onRowClick(record) {
  openDetailModal(record);
}

function switchView(view) {
  document.querySelectorAll('.view-tab').forEach((btn) => {
    const active = btn.dataset.view === view;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('[data-view-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== view;
  });
  // The filters sidebar only applies to the Panel view; collapse it otherwise.
  document.querySelector('.app-shell').classList.toggle('asig-active', view === 'asignaciones');
  if (view === 'asignaciones') {
    closeFiltersDrawer();
    initAsignaciones().catch((err) => {
      console.error(err);
      showToast('No se pudo cargar la vista de asignaciones.', 'error');
    });
    invalidateAsigSize();
  }
}

function wireViewTabs() {
  document.querySelectorAll('.view-tab').forEach((btn) => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });
}

async function loadAndRender({ isRefresh = false } = {}) {
  try {
    if (isRefresh) {
      refreshBtn.classList.add('is-loading');
      refreshBtn.setAttribute('aria-busy', 'true');
      refreshBtn.querySelector('span').textContent = 'Actualizando…';
      refreshProgress.hidden = false;
      // Floor the perceived duration so the progress feedback is visible even
      // when the JSON comes back from cache in a few milliseconds.
      await Promise.all([store.load(), new Promise((r) => setTimeout(r, 600))]);
    } else {
      await store.load();
    }
    errorOverlay.hidden = true;
    renderHeaderMeta();

    if (!mapInitialized) {
      initMap('map', { onDetail: (id) => {
        const record = store.records.find((r) => String(r.ObjectID) === String(id));
        if (record) openDetailModal(record);
      } });
      initTable(tableCard, store.records, { onRowClick });
      wireMapControls();
      wireViewTabs();
      store.subscribe(onStoreChange);
      mapInitialized = true;
    }

    // Filter option lists are rebuilt from fresh data on every load/refresh;
    // initFilters is idempotent (it replaces the panel's innerHTML each call).
    initFilters(document.getElementById('filters-root'), store, activeChipsEl);

    onStoreChange();

    if (isRefresh) showToast('Datos actualizados');
  } catch (err) {
    console.error(err);
    errorMessage.textContent = 'No se pudieron cargar los datos de inspecciones. Verifica tu conexión e inténtalo de nuevo.';
    errorOverlay.hidden = false;
    if (isRefresh) showToast('Error al actualizar los datos.', 'error');
  } finally {
    refreshBtn.classList.remove('is-loading');
    refreshBtn.removeAttribute('aria-busy');
    refreshBtn.querySelector('span').textContent = 'Actualizar datos';
    refreshProgress.hidden = true;
  }
}

searchInput.addEventListener('input', debounce((e) => store.setSearch(e.target.value), 250));
refreshBtn.addEventListener('click', () => loadAndRender({ isRefresh: true }));
retryBtn.addEventListener('click', () => loadAndRender({ isRefresh: true }));
filtersOpenBtn.addEventListener('click', openFiltersDrawer);
drawerBackdrop.addEventListener('click', closeFiltersDrawer);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeFiltersDrawer();
    document.getElementById('detail-modal').classList.remove('is-open');
    document.getElementById('detail-modal').setAttribute('aria-hidden', 'true');
  }
});
window.addEventListener('resize', debounce(() => invalidateSize(), 200));

loadAndRender();
