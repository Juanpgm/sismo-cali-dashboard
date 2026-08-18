// Entry point: wires data store, filters, KPIs, map and table together.
import { store } from './data.js';
import { initFilters } from './filters.js';
import { renderKpis } from './kpi.js';
import { renderStatistics } from './charts.js';
import {
  initMap, render as renderMap, setMode, setColorBy, setSizeBy, setHeatWeight,
  setChoroplethLevel, setChoroplethMetric, invalidateSize, highlightRecord, applyMapTheme,
} from './mapview.js';
import { initTable, renderTable, setTotalRecords, openDetailModal } from './table.js';
import { initAsignaciones, invalidateAsigSize, applyAsigTheme } from './asignaciones.js';
import { initTheme } from './theme.js';
import { debounce, setSourceLabels, sourceLabel } from './utils.js';

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

// "Colorear por" options that map to a real EDAN-F3 variable get the original
// (pre-normalization) source name, like the filter titles. Viz-only options
// (binary habitability) keep their descriptive label.
const COLOR_BY_SOURCE_FIELDS = {
  criterio_habitabilidad: 'criterio_habitabilidad',
  nivel_dano: 'nivel_dano',
  severidad_danos: 'severidad_danos',
  uso_edificacion: 'uso_edificacion',
};
function applySourceLabelsToSelect() {
  const select = el('#color-by-select');
  if (!select) return;
  select.querySelectorAll('option').forEach((opt) => {
    const field = COLOR_BY_SOURCE_FIELDS[opt.value];
    if (field) opt.textContent = sourceLabel(field, opt.textContent);
  });
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
    // Selectable options display the original EDAN-F3 excel names (display only;
    // internal field keys and values are unchanged).
    setSourceLabels(store.meta?.source_labels || {});
    applySourceLabelsToSelect();

    if (!mapInitialized) {
      initMap('map', { onDetail: (id) => {
        const record = store.records.find((r) => String(r.ObjectID) === String(id));
        if (record) openDetailModal(record);
      } });
      initTable(tableCard, store.records, { onRowClick });
      wireMapControls();
      wireViewTabs();
      initTheme();
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

// Manual refresh: POST /api/refresh triggers the Railway pipeline (regenerate
// JSON from the F3 Sheet + push → Vercel redeploy). The fresh data lands minutes
// later, so we keep the button busy and poll the deployed meta.json until its
// generated_at advances, then re-render. Falls back to a plain reload when the
// endpoint is unreachable (e.g. local dev without the serverless function).
const REFRESH_ENDPOINT = '/api/refresh';
const POLL_INTERVAL_MS = 15000;
const POLL_MAX_TRIES = 20; // ~5 min opportunistic background watch

function setRefreshChrome(on) {
  refreshBtn.classList.toggle('is-loading', on);
  refreshBtn.disabled = on;
  if (on) refreshBtn.setAttribute('aria-busy', 'true');
  else refreshBtn.removeAttribute('aria-busy');
  refreshBtn.querySelector('span').textContent = on ? 'Actualizando…' : 'Actualizar datos';
  refreshProgress.hidden = !on;
}

// Fire-and-forget after a successful trigger: the button is already reset, so
// this quietly watches the published meta.json and re-renders IF fresh data
// lands. A timeout is NOT a failure — the pipeline skips publishing when the
// source data hasn't changed, so we stay silent rather than report an error.
async function pollForFreshData(baseline) {
  for (let i = 0; i < POLL_MAX_TRIES; i += 1) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    try {
      const res = await fetch(`data/meta.json?t=${Date.now()}`, { cache: 'no-store' });
      if (res.ok) {
        const meta = await res.json();
        if (meta.generated_at && meta.generated_at !== baseline) {
          await loadAndRender();
          showToast('Datos actualizados');
          return;
        }
      }
    } catch {
      // transient hiccup during the deploy window; keep watching.
    }
  }
}

async function triggerRefresh() {
  const baseline = store.meta?.generated_at ?? null;
  setRefreshChrome(true);
  try {
    let res;
    try {
      res = await fetch(REFRESH_ENDPOINT, { method: 'POST' });
    } catch {
      // No backend reachable: degrade to re-reading the published JSON.
      await loadAndRender();
      showToast('No se pudo contactar el servicio de actualización; recargué los datos publicados.', 'error');
      return;
    }

    let body = {};
    try { body = await res.json(); } catch { /* body optional */ }

    if (res.ok || res.status === 409) {
      // The trigger fired — that's the success the user asked for. The new data
      // lands minutes later (pipeline + redeploy) or not at all when nothing
      // changed, so we report success now and watch in the background instead
      // of freezing the button for minutes.
      showToast(res.status === 409
        ? 'Ya hay una actualización en curso; el dashboard se refrescará en unos minutos.'
        : 'Actualización iniciada. El dashboard se refrescará solo en unos minutos.');
      pollForFreshData(baseline).catch(() => {});
    } else {
      // Trigger unavailable (endpoint missing, token not set, Railway error):
      // never leave the user worse off — re-read the published JSON.
      await loadAndRender();
      showToast(`No se pudo disparar la actualización (${body.error || res.status}); recargué los datos publicados.`, 'error');
    }
  } finally {
    setRefreshChrome(false);
  }
}

searchInput.addEventListener('input', debounce((e) => store.setSearch(e.target.value), 250));
refreshBtn.addEventListener('click', () => triggerRefresh());
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
// Theme toggle: swap map tiles (both maps) and rebuild charts so Chart.js picks
// up the new CSS-variable colors it bakes in at construction time.
document.addEventListener('themechange', () => {
  applyMapTheme();
  applyAsigTheme();
  if (store.records.length) renderStatistics(store.filtered);
});

loadAndRender();
