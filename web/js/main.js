// Entry point: wires data store, filters, KPIs, map and table together.
import { store, fetchData } from './data.js';
import { initFilters } from './filters.js';
import { renderKpis } from './kpi.js';
import { renderStatistics, resetCharts } from './charts.js';
import {
  initMap, render as renderMap, setMode, setColorBy, setSizeBy, setHeatWeight,
  setChoroplethLevel, setChoroplethMetric, invalidateSize, highlightRecord, applyMapTheme,
  setStickerStatus,
} from './mapview.js';
import { coverageGaugeHtml } from './coverage-gauge.js';
import { initTable, renderTable, setTotalRecords, openDetailModal } from './table.js';
import { renderAcciones } from './acciones.js';
import { initStickers } from './stickers.js';
import { initUsuarios } from './usuarios.js';
import { initAnalista } from './analista.js';
import { initTheme } from './theme.js';
import { initAuth, getIdToken, isAdmin } from './auth.js';
import { debounce, setSourceLabels, sourceLabel, habBinary, labelForCode } from './utils.js';

const el = (sel) => document.querySelector(sel);

const kpiRow = el('#kpi-row');
const activeChipsEl = el('#active-chips');
const searchInput = el('#search-input');
const refreshBtn = el('#refresh-btn');
const refreshProgress = el('#refresh-progress');
const refreshProgressFill = refreshProgress.querySelector('.progress-bar-fill');
const refreshStatus = el('#refresh-status');
const refreshStatusText = refreshStatus.querySelector('.refresh-status-text');
const refreshStatusPct = refreshStatus.querySelector('.pct');
const retryBtn = el('#retry-btn');
const errorOverlay = el('#error-overlay');
const errorMessage = el('[data-error-message]');
const toastStack = el('#toast-stack');
const lastUpdateEl = el('[data-last-update]');
const filtersPanel = el('#filters-panel');
const filtersOpenBtn = el('#filters-open-btn');
const drawerBackdrop = el('#drawer-backdrop');
const tableCard = el('#table-card');

let mapInitialized = false;
let currentView = 'panel';

// Kick off the data fetch RIGHT NOW, in parallel with the Firebase Auth SDK
// boot/login check below — the static JSON is publicly fetchable regardless
// of login (see auth.js's "ceiling" comment), so there's no reason to
// serialize a ~3.5MB fetch behind an auth round-trip. loadAndRender() awaits
// this instead of calling store.load() again for the very first render.
let initialLoadPromise = store.load();
// Real handling happens in loadAndRender()'s try/catch; this just keeps the
// browser from logging a spurious "unhandled rejection" if auth is slow and
// the fetch fails before loadAndRender gets a chance to await it.
initialLoadPromise.catch(() => {});

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
}

// Fecha de generación del Excel = momento del clic (fecha de descarga). Devuelve
// dos formas: `legible` para una celda dentro del archivo y `slug` para el nombre.
function downloadStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return {
    legible: d.toLocaleString('es-CO', { dateStyle: 'long', timeStyle: 'short' }),
    slug: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}`,
  };
}

// "Colorear por" options that map to a real EDAN-F3 variable get the original
// (pre-normalization) source name, like the filter titles.
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

const stickerCoverageSection = el('#panel-sticker-coverage');
const stickerGaugeEl = el('#panel-sticker-gauge');

// Paints the Panel coverage gauge from store.stickerCoverage; hides the card
// while there is nothing to show (endpoint not yet resolved / failed).
function renderStickerGauge() {
  const html = store.stickerCoverage ? coverageGaugeHtml(store.stickerCoverage) : '';
  if (stickerGaugeEl) stickerGaugeEl.innerHTML = html;
  if (stickerCoverageSection) stickerCoverageSection.hidden = !html;
}

// Sticker coverage from the cruce (api/sticker-status). Authenticated (any
// logged-in role), so it runs only after startApp. Fire-and-forget: feeds the
// map's 'sticker' colorBy mode and the coverage gauge; on any failure both
// degrade to empty rather than showing stale data.
async function refreshStickerStatus() {
  try {
    const token = await getIdToken();
    if (!token) { setStickerStatus([]); store.setStickerCoverage(null); return; }
    const res = await fetch('/api/sticker-status', { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    setStickerStatus(Array.isArray(body.con_sticker) ? body.con_sticker : []);
    store.setStickerCoverage({ total: body.total, con: body.con });
  } catch (err) {
    console.error('sticker-status falló (se reintenta en 15 min):', err);
    setStickerStatus([]);
    store.setStickerCoverage(null);
  }
}

function onStoreChange() {
  if (searchInput.value !== (store.filters.searchRaw || '')) {
    searchInput.value = store.filters.searchRaw || '';
  }
  renderKpis(kpiRow, store.filtered, store.records, store.reportados, store.reportesTotal, store.inmuebles);
  setTotalRecords(store.records.length);
  renderTable(store.filtered);
  renderMap(store.filtered).catch((err) => {
    console.error(err);
    showToast('No se pudo cargar la capa geográfica.', 'error');
  });
  renderStatistics(store.filtered, store.records, store.reportados);
  renderStickerGauge();
  // Acciones works over ALL records: the filters sidebar only applies to Panel.
  // Solo admin, and only while that tab is actually visible — skip the full
  // rebuild on every Panel filter keystroke otherwise (see switchView()).
  if (isAdmin() && currentView === 'acciones') {
    renderAcciones(document.getElementById('view-acciones'), store.records, { onRowClick: openDetailModal });
  }
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
  // TEMPORARY: Acciones is suspended (see the matching CSS rule that greys
  // out its tab). pointer-events already blocks clicks; this closes keyboard
  // activation and programmatic calls.
  if (view === 'acciones') return;
  document.querySelectorAll('.view-tab').forEach((btn) => {
    const active = btn.dataset.view === view;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('[data-view-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== view;
  });
  // The filters sidebar only applies to the Panel view; collapse it otherwise.
  document.querySelector('.app-shell').classList.toggle('asig-active', view !== 'panel');
  if (view !== 'panel') closeFiltersDrawer();
  // Stickers pulls live data from /api/stickers — (re)load it each time it opens.
  if (view === 'stickers') {
    initStickers(document.getElementById('view-stickers'), { getToken: getIdToken });
  }
  // Usuarios pulls live data from /api/usuarios — (re)load it each time it opens.
  if (view === 'usuarios') {
    initUsuarios(document.getElementById('view-usuarios'), { getToken: getIdToken });
  }
  // Analista pulls live data (Blob snapshots + /api/source-status probe) —
  // (re)load it each time it opens, same lifecycle as Stickers/Usuarios.
  if (view === 'analista') {
    initAnalista(document.getElementById('view-analista'), { getToken: getIdToken });
  }
  currentView = view;
  // Acciones works over ALL records and doesn't depend on the Panel filters —
  // render it lazily (on load and on filter change) only while it's the
  // visible tab, same idea as Stickers above; see onStoreChange().
  if (view === 'acciones' && isAdmin() && store.records.length) {
    renderAcciones(document.getElementById('view-acciones'), store.records, { onRowClick: openDetailModal });
  }
}

function wireViewTabs() {
  document.querySelectorAll('.view-tab').forEach((btn) => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });
}

async function loadAndRender({ isRefresh = false, bust = false } = {}) {
  try {
    if (isRefresh) {
      refreshBtn.classList.add('is-loading');
      refreshBtn.setAttribute('aria-busy', 'true');
      refreshBtn.querySelector('span').textContent = 'Actualizando…';
      refreshProgress.classList.add('is-indeterminate');
      refreshProgress.hidden = false;
      // Floor the perceived duration so the progress feedback is visible even
      // when the JSON comes back from cache in a few milliseconds.
      await Promise.all([store.load({ bust }), new Promise((r) => setTimeout(r, 600))]);
    } else if (initialLoadPromise) {
      // First call after boot: reuse the fetch kicked off at module load
      // instead of firing a second, redundant one.
      await initialLoadPromise;
      initialLoadPromise = null;
    } else {
      await store.load({ bust });
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
    // Only the retry/legacy refresh path owns the button + indeterminate bar.
    // When triggerRefresh calls this with isRefresh=false, it must not clobber
    // the phased progress bar it is driving.
    if (isRefresh) {
      refreshBtn.classList.remove('is-loading');
      refreshBtn.removeAttribute('aria-busy');
      refreshBtn.querySelector('span').textContent = 'Actualizar datos';
      refreshProgress.classList.remove('is-indeterminate');
      refreshProgress.hidden = true;
    }
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
}

// --- Phased progress bar for the manual refresh -------------------------
let progressAnim = null;

function setProgress(pct, label, state) {
  if (progressAnim) { cancelAnimationFrame(progressAnim); progressAnim = null; }
  refreshProgress.hidden = false;
  refreshProgress.classList.remove('is-indeterminate');
  refreshProgressFill.style.width = `${pct}%`;
  refreshProgress.setAttribute('aria-valuenow', String(Math.round(pct)));
  refreshStatus.hidden = false;
  refreshStatus.classList.toggle('is-done', state === 'done');
  refreshStatus.classList.toggle('is-error', state === 'error');
  if (label != null) refreshStatusText.textContent = label;
  refreshStatusPct.textContent = `${Math.round(pct)}%`;
}

// Ease the bar from its current width toward `target` over `duration`, so the
// user sees steady movement while the server-side job is being queued/processed.
function animateProgress(target, label, duration) {
  return new Promise((resolve) => {
    if (label != null) refreshStatusText.textContent = label;
    const start = parseFloat(refreshProgressFill.style.width) || 0;
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - t0) / duration);
      const pct = start + (target - start) * p;
      refreshProgressFill.style.width = `${pct}%`;
      refreshStatusPct.textContent = `${Math.round(pct)}%`;
      if (p < 1) { progressAnim = requestAnimationFrame(step); }
      else { progressAnim = null; resolve(); }
    };
    progressAnim = requestAnimationFrame(step);
  });
}

function clearProgress() {
  if (progressAnim) { cancelAnimationFrame(progressAnim); progressAnim = null; }
  refreshProgress.hidden = true;
  refreshProgress.classList.remove('is-indeterminate');
  refreshProgressFill.style.width = '0%';
  refreshStatus.hidden = true;
  refreshStatus.classList.remove('is-done', 'is-error');
}

// Background watch: when the published data actually changes, finish the bar and
// re-render. A timeout is NOT a failure (the hourly job may publish later, or
// have nothing new), so it just fades quietly.
async function pollForFreshData(baseline) {
  for (let i = 0; i < POLL_MAX_TRIES; i += 1) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    try {
      const res = await fetchData('meta.json', { q: `?t=${Date.now()}`, opts: { cache: 'no-store' } });
      if (res.ok) {
        const meta = await res.json();
        if (meta.generated_at && meta.generated_at !== baseline) {
          setProgress(100, 'Datos actualizados', 'done');
          await loadAndRender({ bust: true });
          showToast('Datos actualizados');
          setTimeout(clearProgress, 1800);
          return;
        }
      }
    } catch {
      // transient hiccup during the deploy window; keep watching.
    }
  }
}

// Guard: mientras hay una actualización en vuelo, más clicks NO deben disparar
// otro redeploy — cada redeploy MATA la corrida en curso en Railway y la fecha
// nunca avanza. El botón queda ocupado hasta que el dato fresco aterriza (o el
// poll agota su ventana).
let refreshInFlight = false;

async function triggerRefresh() {
  // Solo los administradores pueden disparar el refresh. El botón ya está oculto
  // para viewers, pero esto cierra el llamado directo desde la consola. El
  // endpoint también verifica el token en el servidor (defensa en profundidad).
  if (!isAdmin()) {
    showToast('No tenés permisos para actualizar los datos.', 'error');
    return;
  }
  if (refreshInFlight) {
    showToast('Ya hay una actualización en curso; los datos llegan en unos minutos.');
    return;
  }
  refreshInFlight = true;
  let heldByPoll = false; // el camino exitoso transfiere la liberación al poll
  const baseline = store.meta?.generated_at ?? null;
  const idToken = await getIdToken();
  const authHeaders = idToken ? { Authorization: `Bearer ${idToken}` } : {};
  setRefreshChrome(true);
  setProgress(12, 'Enviando solicitud…');
  try {
    // Un deploy de Vercel en curso (publicamos cada 15 min) puede responder
    // 5xx/501 por un instante: reintentamos una vez tras una pausa corta.
    let res;
    try {
      res = await fetch(REFRESH_ENDPOINT, { method: 'POST', headers: authHeaders });
      if (!res.ok && res.status !== 409) {
        setProgress(25, 'Reintentando…');
        await new Promise((r) => setTimeout(r, 3000));
        res = await fetch(REFRESH_ENDPOINT, { method: 'POST', headers: authHeaders });
      }
    } catch {
      // No backend reachable: degrade to re-reading the published JSON.
      setProgress(100, 'Sin conexión — recargando datos', 'error');
      await loadAndRender({ bust: true });
      showToast('No se pudo contactar el servicio de actualización; recargué los datos publicados.', 'error');
      return;
    }

    let body = {};
    try { body = await res.json(); } catch { /* body optional */ }

    if (res.ok || res.status === 409) {
      // The trigger fired — that's the success the user asked for. The real
      // data lands later (the hourly job syncs + publishes), so we advance the
      // bar through the server-side phases and keep watching in the background
      // instead of freezing the button.
      // El KPI "Reportados" viene de la API atencionsismo detrás de una caché
      // CDN de 15 min: el botón también la saltea para releer la API YA. Es
      // fire-and-forget (la lectura en vivo puede tardar ~2 min en frío); al
      // aterrizar, store.notify() re-renderiza el KPI solo.
      store.refreshReportados({ bust: true }).catch(() => {});
      setProgress(45, res.status === 409 ? 'Ya había una actualización en curso…' : 'Encolado en el servidor…');
      await animateProgress(88, 'Procesando datos…', 7000);
      setProgress(92, 'Esperando datos frescos…');
      showToast('Actualización encolada. El dashboard se refrescará en unos minutos.');
      // El botón sigue ocupado hasta que el dato aterrice (o el poll expire):
      // así un segundo click no mata la corrida en el servidor.
      heldByPoll = true;
      pollForFreshData(baseline)
        .catch(() => {})
        .finally(() => {
          refreshInFlight = false;
          setRefreshChrome(false);
          setTimeout(clearProgress, 2200);
        });
      return;
    }
    // Trigger unavailable (endpoint missing, token not set, Railway error):
    // never leave the user worse off — re-read the published JSON.
    setProgress(100, 'No se pudo disparar — recargando', 'error');
    await loadAndRender({ bust: true });
    showToast(`No se pudo disparar la actualización (${body.error || res.status}); recargué los datos publicados.`, 'error');
  } finally {
    // Los caminos fallidos liberan el botón ya; el camino exitoso lo libera el
    // poll (heldByPoll) para bloquear clicks que matarían la corrida.
    if (!heldByPoll) {
      refreshInFlight = false;
      setRefreshChrome(false);
      setTimeout(clearProgress, 2200);
    }
  }
}

// xlsx (SheetJS, ~1MB) is only needed by the two download buttons below —
// load it on first click instead of blocking every page load with it.
let xlsxPromise = null;
function loadXlsx() {
  if (!xlsxPromise) {
    xlsxPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js';
      s.onload = () => resolve(window.XLSX);
      s.onerror = () => reject(new Error('No se pudo cargar xlsx'));
      document.head.appendChild(s);
    });
  }
  return xlsxPromise;
}

/** Build the .xlsx client-side from store.filtered so active filters apply.
 *  The JSON records carry the same columns as the static export; the internal
 *  derived fields (_search, n_pisos_rango) are stripped, but the derived
 *  suspension_servicios column ships in the export on purpose. */
el('#datos-download').addEventListener('click', async () => {
  let XLSX;
  try { XLSX = await loadXlsx(); } catch { showToast('No se pudo cargar el generador de Excel.', 'error'); return; }
  const rows = store.filtered.map(({ _search, n_pisos_rango, ...r }) => {
    const bin = habBinary(r); // 'habitable' | 'no_habitable' | '' (sin dato)
    return { ...r, habitable_no_habitable: bin ? labelForCode(bin) : 'Sin dato' };
  });
  if (!rows.length) {
    showToast('No hay registros con los filtros aplicados.', 'error');
    return;
  }
  // TODAS las columnas disponibles: sheet_add_json toma el encabezado de la
  // PRIMERA fila, así que con registros de esquemas distintos (Cali completo vs
  // Israel sparse) se perderían columnas. Construimos la unión de claves de todas
  // las filas y la pasamos como `header` para que ninguna columna quede afuera.
  const header = [];
  const seen = new Set();
  for (const row of rows) {
    for (const k of Object.keys(row)) {
      if (!seen.has(k)) { seen.add(k); header.push(k); }
    }
  }
  // Fecha de descarga (momento del clic) como metadato SOBRE los datos + en el
  // nombre del archivo, para dejar claro cuándo se bajó este export.
  const { legible, slug } = downloadStamp();
  const ws = XLSX.utils.aoa_to_sheet([
    ['Descargado el:', legible],
    [],
  ]);
  XLSX.utils.sheet_add_json(ws, rows, { origin: 'A3', header });
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'inspecciones');
  XLSX.writeFile(wb, `inspecciones_${slug}.xlsx`);
});

/** Reporte para la Secretaría de Tránsito (solo admin): edificaciones con
 *  colapso (parcial o total) Y nivel de daño alto — las que pueden comprometer
 *  la vía. El filtro es FIJO (no usa los filtros del Panel) porque es un
 *  documento con criterio propio que se entrega a otra entidad. */
el('#transito-download').addEventListener('click', async () => {
  if (!isAdmin()) return; // el botón está oculto para viewers; guardia por si acaso
  let XLSX;
  try { XLSX = await loadXlsx(); } catch { showToast('No se pudo cargar el generador de Excel.', 'error'); return; }
  const yes = (v) => String(v).toLowerCase() === 'si';
  const rows = store.records
    .filter((r) => (yes(r.colapso_parcial) || yes(r.colapso_total))
      && String(r.nivel_dano).toLowerCase() === 'alto')
    .map(({ _search, n_pisos_rango, ...r }) => r);
  if (!rows.length) {
    showToast('No hay edificaciones que cumplan el filtro de Tránsito.', 'error');
    return;
  }
  const { legible, slug } = downloadStamp();
  const ws = XLSX.utils.aoa_to_sheet([
    ['Reporte para la Secretaría de Tránsito'],
    ['Filtro aplicado:', 'Colapso (parcial o total) y nivel de daño alto'],
    ['Fecha de generación:', legible],
    ['Registros:', rows.length],
    ['Aviso:', 'Los cierres o intervenciones viales son a criterio de la Secretaría de Tránsito.'],
    [],
  ]);
  XLSX.utils.sheet_add_json(ws, rows, { origin: 'A7' });
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'transito');
  XLSX.writeFile(wb, `reporte_transito_${slug}.xlsx`);
});

searchInput.addEventListener('input', debounce((e) => store.setSearch(e.target.value), 250));
refreshBtn.addEventListener('click', () => triggerRefresh());
retryBtn.addEventListener('click', () => loadAndRender({ isRefresh: true, bust: true }));
filtersOpenBtn.addEventListener('click', openFiltersDrawer);
drawerBackdrop.addEventListener('click', closeFiltersDrawer);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeFiltersDrawer();
    document.getElementById('detail-modal').classList.remove('is-open');
    document.getElementById('detail-modal').setAttribute('aria-hidden', 'true');
  }
});
window.addEventListener('resize', debounce(() => {
  invalidateSize();
}, 200));
// Theme toggle: swap map tiles and rebuild charts so Chart.js picks
// up the new CSS-variable colors it bakes in at construction time.
document.addEventListener('themechange', () => {
  applyMapTheme();
  if (store.records.length) {
    resetCharts(); // force destroy+recreate so Chart.js re-bakes the new theme's CSS-var colors
    renderStatistics(store.filtered, store.records, store.reportados);
  }
});

// Auto-refresh cada 15 min, alineado con el cron de Railway (*/15) que publica
// los datos. Silencioso: recarga el store (dispara el re-render del Panel vía la
// suscripción) + actualiza la fecha del header, sin overlay de error ni
// reconstruir el panel de filtros.
const AUTO_REFRESH_MS = 15 * 60 * 1000;

// El dashboard NO carga datos hasta que haya una sesión válida. initAuth muestra
// el login y llama startApp una sola vez cuando el usuario queda autorizado; el
// rol (admin/viewer) ya lo aplicó auth.js sobre document.body para ocultar el
// chrome de admin (tab Acciones + botón Actualizar).
function startApp() {
  loadAndRender();
  refreshStickerStatus();
  setInterval(async () => {
    try {
      await store.load();
      renderHeaderMeta();
      refreshStickerStatus();
    } catch (err) {
      console.error('auto-refresh falló (se reintenta en 15 min):', err);
    }
  }, AUTO_REFRESH_MS);
}

initAuth(startApp);
