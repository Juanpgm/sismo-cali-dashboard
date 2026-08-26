// Analista view: a read-only inventory of every data source feeding the
// dashboard (name, description, last read, row count, semáforo status),
// including a real live-connectivity probe for the atencionsismo API.
//
// Mirrors web/js/usuarios.js's shape (callApi-style GET, lazy init on tab
// open, defense-in-depth isAdmin() guard) but is READ-ONLY — no create/edit
// actions, just a reload() + render(). See design.md ADR-1.
import { isAdmin } from './auth.js';
import { fetchData } from './data.js';
import { fetchIsraelRecords } from './israel-source.js';
import { COLORS, formatTs, escapeHtml } from './utils.js';
import { apiUrl } from './api-config.js';

const SOURCE_STATUS_ENDPOINT = apiUrl('sourceStatus');

// A snapshot is 'desactualizado' (amarillo) past this age. Derived from the
// Railway cron cadence (*/15 min) — three missed publishes is a real stall,
// not ordinary timing jitter. See design.md ADR-5. rojo is NEVER inferred
// from age; only from an explicit received error signal.
const STALE_MS = 45 * 60 * 1000;

const DOT_COLOR = { verde: COLORS.status.h, amarillo: COLORS.status.r2, rojo: COLORS.status.i2 };

// ---- shared read helper: distinguishes "missing/unreadable" (a resolved,
// non-ok or throwing-on-parse response) from "transport failure" (fetchData()
// itself rejected — network down, Blob unreachable). ADR-6: only the second
// case ever renders as 'sin datos'; the first is source-specific ('sin
// metadata', 'ausente', etc). Never throws.
async function readJson(name) {
  try {
    const res = await fetchData(name);
    if (!res.ok) return { state: 'missing', res };
    return { state: 'ok', json: await res.json(), res };
  } catch (err) {
    return { state: 'error', err };
  }
}

function freshnessColor(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) <= STALE_MS ? 'verde' : 'amarillo';
}

// A transport failure (fetch rejected) always renders as amarillo 'sin
// datos', regardless of the source's normal category rules (ADR-6).
function sinDatosRow(base) {
  return { ...base, ultima_lectura: null, registros: null, estado_color: 'amarillo', estado_label: 'sin datos', detalle: 'no se pudo leer la fuente' };
}

// ---- per-source SourceRow builders ------------------------------------------

function rowFromMeta(base, result, { staleLabel = 'desactualizado' } = {}) {
  if (result.state === 'error') return sinDatosRow(base);
  if (result.state === 'missing') {
    return { ...base, ultima_lectura: null, registros: null, estado_color: 'amarillo', estado_label: 'sin metadata', detalle: 'meta.json no disponible' };
  }
  const { generated_at = null, row_count = null } = result.json || {};
  const color = freshnessColor(generated_at);
  const label = color === 'verde' ? 'conectado' : (color === 'amarillo' ? staleLabel : 'sin metadata');
  return { ...base, ultima_lectura: generated_at, registros: row_count, estado_color: color || 'amarillo', estado_label: label, detalle: base.detalle || null };
}

function rowGeocoding() {
  // No freshness metadata exists for this cache by design — always amarillo
  // 'sin metadata', regardless of the underlying file's age (spec requirement).
  return {
    id: 'geocoding', nombre: 'Google Maps Geocoding API', descripcion: 'Caché interna de geocodificación (geocode_cache.json); sin metadata de frescura.',
    ultima_lectura: null, registros: null, estado_color: 'amarillo', estado_label: 'sin metadata', detalle: 'caché interna, sin metadata',
  };
}

function rowAtencionsismo(metaResult, probeResult) {
  const base = { id: 'atencionsismo', nombre: 'API atención sismo (reportes)', descripcion: 'Reportes ciudadanos vía atencionsismo.cali.gov.co; alimenta el KPI "Reportados".' };
  let ultima_lectura = null;
  let registros = null;
  if (metaResult.state === 'ok') {
    ultima_lectura = metaResult.json?.generated_at ?? null;
    registros = metaResult.json?.row_count ?? null;
  }
  // The live probe result overrides the snapshot-freshness color entirely
  // (spec: "Live probe succeeds independent of snapshot staleness").
  if (probeResult.state === 'transport-error') {
    return { ...base, ultima_lectura, registros, estado_color: 'amarillo', estado_label: 'sin datos', detalle: 'no se pudo contactar /api/source-status' };
  }
  if (probeResult.ok) {
    return { ...base, ultima_lectura, registros, estado_color: 'verde', estado_label: 'conectado', detalle: probeResult.detail || null };
  }
  return { ...base, ultima_lectura, registros, estado_color: 'rojo', estado_label: 'con errores', detalle: probeResult.detail || 'la API no respondió' };
}

function rowGlobalRun(result) {
  const base = { id: 'pipeline-run', nombre: 'Corrida global del pipeline', descripcion: 'Última corrida de deploy/refresh.sh; señal de todo-o-nada, no por sub-fuente.' };
  if (result.state === 'error') return sinDatosRow(base);
  if (result.state === 'missing') {
    return { ...base, ultima_lectura: null, registros: null, estado_color: 'amarillo', estado_label: 'sin metadata', detalle: 'sin dato de corrida global' };
  }
  const { ok, step } = result.json || {};
  const lastModified = result.res?.headers?.get?.('last-modified') || null;
  const detalle = `corrida completa (no por sub-fuente); paso: ${step || '—'}`;
  if (ok === false) return { ...base, ultima_lectura: lastModified, registros: null, estado_color: 'rojo', estado_label: 'con errores', detalle };
  const color = freshnessColor(lastModified) || 'verde';
  return { ...base, ultima_lectura: lastModified, registros: null, estado_color: color, estado_label: color === 'verde' ? 'conectado' : 'desactualizado', detalle };
}

// Orphaned outputs: produced by the untraced integracion_F1 cron, consumed by
// no live tab. Always amarillo 'sin consumidor' when present; 'ausente' when
// the file itself is missing (still amarillo — that's a category fact, not a
// transport failure). registros derivation differs per file's own shape.
function rowOrphan(base, result, deriveRegistros) {
  if (result.state === 'error') return sinDatosRow(base);
  if (result.state === 'missing') {
    return { ...base, ultima_lectura: null, registros: null, estado_color: 'amarillo', estado_label: 'sin consumidor', detalle: 'ausente' };
  }
  const { ultima_lectura, registros } = deriveRegistros(result);
  return { ...base, ultima_lectura, registros, estado_color: 'amarillo', estado_label: 'sin consumidor', detalle: null };
}

function rowIsrael(records) {
  const base = { id: 'israel', nombre: 'FeatureServer Israel + Firestore inspecciones_israel', descripcion: 'Survey del equipo de Israel remapeado al esquema EDE; se combina con el Panel.' };
  const registros = records.length;
  if (registros > 0) return { ...base, ultima_lectura: null, registros, estado_color: 'verde', estado_label: 'conectado', detalle: null };
  // Empty and "unreachable" look identical here (fetchIsraelRecords() never
  // throws) — labeled honestly rather than guessed as an error.
  return { ...base, ultima_lectura: null, registros: 0, estado_color: 'amarillo', estado_label: 'sin metadata', detalle: 'sin registros o fuente inaccesible' };
}

// ---- live probe call ---------------------------------------------------------

async function callSourceStatus(getToken, bust) {
  const token = await getToken();
  if (!token) return { state: 'transport-error' };
  try {
    const url = bust ? `${SOURCE_STATUS_ENDPOINT}?t=${Date.now()}` : SOURCE_STATUS_ENDPOINT;
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) return { state: 'transport-error' };
    const body = await res.json();
    return { state: 'ok', ok: !!body.ok, detail: body.detail };
  } catch {
    return { state: 'transport-error' };
  }
}

// ---- orchestration: fetch everything, fault-isolated -------------------------

// Promise.allSettled only guards against fail-fast on REJECTION — a promise
// that never settles (network hang: stalled TCP/DNS, unresponsive function)
// still blocks the whole allSettled() forever. Wrap each source so a hang
// rejects (settles) after `ms` instead, landing in the same "transport
// failure -> amarillo 'sin datos'" path a real rejection would (ADR-6).
export function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`timeout: ${label}`)), ms)),
  ]);
}

const SOURCE_TIMEOUT_MS = 15000;

async function loadSourceRows(getToken, { bust = false } = {}) {
  const results = await Promise.allSettled([
    withTimeout(readJson('meta.json'), SOURCE_TIMEOUT_MS, 'meta.json'),
    withTimeout(readJson('reportes_meta.json'), SOURCE_TIMEOUT_MS, 'reportes_meta.json'),
    withTimeout(readJson('_status.json'), SOURCE_TIMEOUT_MS, '_status.json'),
    withTimeout(readJson('asignaciones.json'), SOURCE_TIMEOUT_MS, 'asignaciones.json'),
    withTimeout(readJson('cruce_gestor.json'), SOURCE_TIMEOUT_MS, 'cruce_gestor.json'),
    withTimeout(readJson('cruce_criticos_survey.json'), SOURCE_TIMEOUT_MS, 'cruce_criticos_survey.json'),
    withTimeout(readJson('criticos_api.json'), SOURCE_TIMEOUT_MS, 'criticos_api.json'),
    withTimeout(fetchIsraelRecords(), SOURCE_TIMEOUT_MS, 'israel'),
    withTimeout(callSourceStatus(getToken, bust), SOURCE_TIMEOUT_MS, 'atencionsismo-probe'),
    // Geocoding cache has no freshness signal to extract (spec: always 'sin
    // metadata' regardless of age) — still fetched for fault-isolation parity
    // with the other static reads, result intentionally unused.
    withTimeout(readJson('geocode/geocode_cache.json'), SOURCE_TIMEOUT_MS, 'geocode_cache.json'),
  ]);

  const val = (i, fallback) => (results[i].status === 'fulfilled' ? results[i].value : fallback);
  const metaR = val(0, { state: 'error' });
  const reportesMetaR = val(1, { state: 'error' });
  const statusR = val(2, { state: 'error' });
  const asignacionesR = val(3, { state: 'error' });
  const cruceGestorR = val(4, { state: 'error' });
  const cruceCriticosR = val(5, { state: 'error' });
  const criticosApiR = val(6, { state: 'error' });
  const israelRecords = val(7, []);
  const probeR = val(8, { state: 'transport-error' });

  return [
    rowFromMeta({ id: 'edan-f3', nombre: 'Google Sheet EDAN-F3', descripcion: 'Hoja fuente de tabla_normalizada; alimenta el Panel principal.' }, metaR),
    rowFromMeta({ id: 'survey123', nombre: 'Survey123 ArcGIS FeatureServer', descripcion: 'Fotos/geometría cruzadas por EXIF; plegado en el mismo meta.json (sin error de sub-fuente propio).', detalle: 'sin error de sub-fuente: solo hay meta de la corrida completa' }, metaR),
    rowGeocoding(),
    rowAtencionsismo(reportesMetaR, probeR),
    rowGlobalRun(statusR),
    rowOrphan({ id: 'asignaciones', nombre: 'asignaciones.json', descripcion: 'Roster de asignación priorizada (pestaña Asignaciones); sin consumidor propio de salud.' }, asignacionesR, (r) => ({
      ultima_lectura: r.json.generated_at ?? null,
      registros: (Number(r.json.pendientes) || 0) + (Number(r.json.visitados) || 0),
    })),
    rowOrphan({ id: 'cruce-gestor', nombre: 'cruce_gestor.json', descripcion: 'Cruce del roster de gestión contra la API del PMU; sin tab que lo consuma hoy.' }, cruceGestorR, (r) => ({
      ultima_lectura: r.json.generated_at ?? null,
      registros: Array.isArray(r.json.records) ? r.json.records.length : null,
    })),
    rowOrphan({ id: 'cruce-criticos-survey', nombre: 'cruce_criticos_survey.json', descripcion: 'Cruce de críticos contra Survey123; sin tab que lo consuma hoy.' }, cruceCriticosR, (r) => ({
      ultima_lectura: r.json.generated_at ?? null,
      registros: Array.isArray(r.json.records) ? r.json.records.length : null,
    })),
    rowOrphan({ id: 'criticos-api', nombre: 'criticos_api.json', descripcion: 'Salida ad-hoc del cruce críticos↔API; sin generated_at ni tab consumidor.' }, criticosApiR, (r) => ({
      ultima_lectura: r.res?.headers?.get?.('last-modified') || null,
      registros: Array.isArray(r.json) ? r.json.length : null,
    })),
    rowIsrael(israelRecords),
  ];
}

// ---- render -------------------------------------------------------------------

function rowHtml(row) {
  const meta = [
    `Última lectura: ${row.ultima_lectura ? formatTs(row.ultima_lectura) : 'sin metadata'}`,
    row.registros !== null && row.registros !== undefined ? `${row.registros} registro(s)` : null,
    row.detalle || null,
  ].filter(Boolean).join(' · ');
  return `<li class="sticker-row">
    <div class="sticker-identity">
      <span class="sticker-name">${escapeHtml(row.nombre)}</span>
      <span class="sticker-meta">${escapeHtml(row.descripcion)}</span>
    </div>
    <span class="sticker-meta">${escapeHtml(meta)}</span>
    <span class="analista-estado">
      <span class="analista-dot" style="background:${DOT_COLOR[row.estado_color] || COLORS.status.r2}" aria-hidden="true"></span>
      ${escapeHtml(row.estado_label)}
    </span>
  </li>`;
}

function shellHtml() {
  return `
    <header class="sticker-page-head">
      <h2 class="sticker-h1">Analista</h2>
      <p class="sticker-lead">Salud de las fuentes de datos que alimentan el tablero.</p>
    </header>
    <div class="section-bar">
      <h3 class="section-bar-title">Fuentes</h3>
      <button type="button" class="btn-primary" id="analista-refresh">Actualizar</button>
    </div>
    <section id="analista-roster"></section>`;
}

// initAnalista(root, { getToken }) — renders the tab and (re)loads its data.
// Re-fetches on every tab open (main.js calls this every time), same
// lifecycle as initUsuarios/initStickers. Read-only: no timers, no polling.
export function initAnalista(root, { getToken }) {
  if (!isAdmin()) {
    root.innerHTML = '<p class="sticker-empty">Acceso restringido a administradores.</p>';
    return;
  }

  root.innerHTML = shellHtml();
  const rosterRoot = root.querySelector('#analista-roster');
  const refreshBtn = root.querySelector('#analista-refresh');

  async function reload({ bust = false } = {}) {
    rosterRoot.innerHTML = '<p class="sticker-loading">Cargando fuentes…</p>';
    refreshBtn.disabled = true;
    try {
      const rows = await loadSourceRows(getToken, { bust });
      rosterRoot.innerHTML = `<ul class="sticker-list">${rows.map(rowHtml).join('')}</ul>`;
    } catch (err) {
      rosterRoot.innerHTML = `<p class="sticker-error" role="alert">${escapeHtml(err.message || String(err))}</p>`;
    } finally {
      refreshBtn.disabled = false;
    }
  }

  refreshBtn.addEventListener('click', () => reload({ bust: true }));
  reload();
}
