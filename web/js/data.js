// Data loading + global filter state (tiny pub/sub store, no framework).
import {
  normalizeAddressText, buildSearchIndex, splitMultiValue, labelForField,
  bucketNpisos, suspensionServicios, bustParams, AFECTACION_ORDER,
} from './utils.js';
import { fetchIsraelRecords } from './israel-source.js';

// Re-exported so existing/potential external import sites (`import { bucketNpisos } from './data.js'`)
// keep working; the actual implementation lives in utils.js (see comment there
// for why — data.js can't be loaded standalone by Node's ESM loader for testing).
export { bucketNpisos, suspensionServicios, bustParams };

// The refresh-generated data (meta/inspections/reportes) lives in Vercel Blob,
// updated by the pipeline every cron run WITHOUT a Vercel deploy. Reads are
// public + CDN-cached. The repo's data/ copies are a frozen fallback: if Blob
// is unreachable we serve the last-deployed data (stale) instead of a blank
// dashboard. Static boundaries (comunas/barrios geojson) are NOT here — they
// never change, so they stay served from the deploy. See deploy/refresh.sh +
// deploy/blob_sync.py.
const BLOB_DATA_BASE = 'https://xsr0euqif1ryb8id.public.blob.vercel-storage.com/data';

export async function fetchData(name, { q = '', opts = {} } = {}) {
  try {
    const res = await fetch(`${BLOB_DATA_BASE}/${name}${q}`, opts);
    if (res.ok) return res;
  } catch { /* Blob unreachable — fall back to the deployed copy */ }
  return fetch(`data/${name}${q}`, opts);
}

// Sidebar section order/labels for FILTER_FIELDS' `group` key.
// Ordered so the severity-determining fields the assessor needs first come first.
export const FILTER_GROUPS = [
  { key: 'severidad', label: 'Severidad y daño' },
  { key: 'edificacion', label: 'Edificación' },
  { key: 'ubicacion', label: 'Ubicación' },
  { key: 'contexto', label: 'Contexto' },
];

// Single source of truth for filterable (checkbox/dropdown) fields — filters.js imports this directly.
export const FILTER_FIELDS = [
  // Severity / damage grade — the primary triage decision fields.
  { field: 'severidad_danos', label: labelForField('severidad_danos'), group: 'severidad' },
  // Porcentaje de afectación en planta: rangos ordinales en string; `order` los
  // muestra por severidad creciente en vez de alfabéticamente (ver computeOptions).
  { field: 'afectacion_planta', label: labelForField('afectacion_planta'), group: 'severidad', order: AFECTACION_ORDER },
  { field: 'nivel_dano', label: 'Nivel de daño', group: 'severidad' },
  { field: 'criterio_habitabilidad', label: 'Habitabilidad', group: 'severidad' },
  // Derived field (see suspensionServicios): not in inspections.json.
  { field: 'suspension_servicios', label: 'Suspensión de servicios', group: 'severidad' },
  { field: 'colapso_total', label: 'Colapso total', group: 'severidad' },
  { field: 'colapso_parcial', label: 'Colapso parcial', group: 'severidad' },
  // NOTE: adjacent-building external risk (41_a / 42_a / riesgo_caida) is no
  // longer a filter group — it's now colorable directly on the map points
  // ("Colorear por" → Riesgo externo). See mapview.js RISK_FIELDS.
  // Building type / condition.
  { field: 'uso_edificacion', label: 'Uso de la edificación', multiValue: true, group: 'edificacion' },
  { field: 'sistema_estructural', label: 'Sistema estructural', group: 'edificacion' },
  { field: 'material_estructura', label: labelForField('material_estructura'), group: 'edificacion' },
  { field: 'calidad_construccion', label: 'Calidad de construcción', group: 'edificacion' },
  { field: 'estado_edificacion', label: labelForField('estado_edificacion'), group: 'edificacion' },
  { field: 'tipo_propiedad', label: 'Tipo de propiedad', group: 'edificacion' },
  { field: 'epoca_construccion', label: 'Época de construcción', group: 'edificacion' },
  // N.º de pisos como rangos de 3 (campo derivado n_pisos_rango, ver bucketNpisos).
  { field: 'n_pisos_rango', label: 'N.º de pisos', emptyLabel: 'Sin dato', group: 'edificacion' },
  { field: 'comuna', label: 'Comuna', group: 'ubicacion' },
  { field: 'barrio_geo', label: 'Barrio', emptyLabel: 'Sin barrio asignado', group: 'ubicacion' },
  { field: 'entidad', label: labelForField('entidad'), group: 'contexto' },
];

// Numeric range fields — the "Rangos" sidebar section. Empty on purpose: n_pisos
// is now a bucketed dropdown (n_pisos_rango, ranges of 3) in the Edificación group,
// and the other numeric variables are explored on the map (size / heat / choropleth).
// The store, chips and filters.js all guard on RANGE_FIELDS.length, so an empty
// list simply drops the whole section.
export const RANGE_FIELDS = [];

const NONE = '__none__';

function cleanDate(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  return s === '' ? null : s;
}

/** Does `record` match the selected-value set for one FILTER_FIELDS entry? */
function matchesField(record, def, set) {
  if (set.size === 0) return true;
  const raw = record[def.field];
  if (def.multiValue) {
    const parts = splitMultiValue(raw);
    if (parts.length === 0) return set.has(NONE);
    return parts.some((p) => set.has(p));
  }
  if (raw === null || raw === undefined || raw === '') return set.has(NONE);
  return set.has(raw);
}

/** Does `record` match a { min, max } range for one RANGE_FIELDS entry?
 *  Inactive (both null) matches everyone; once a bound is set, blank/non-numeric
 *  record values are excluded (can't be compared against a numeric bound). */
function matchesRange(record, def, range) {
  if (range.min === null && range.max === null) return true;
  const raw = record[def.field];
  if (raw === null || raw === undefined || raw === '') return false;
  const n = Number(raw);
  if (Number.isNaN(n)) return false;
  if (range.min !== null && n < range.min) return false;
  if (range.max !== null && n > range.max) return false;
  return true;
}

class Store {
  constructor() {
    this.meta = null;
    this.records = []; // raw records + _search index
    this.filtered = [];
    // Reportes ciudadanos en estado "Reportado", leído EN VIVO de la API
    // atencionsismo vía /api/reportados (proxy serverless, caché CDN de 15 min).
    // Fallback: el agregado estático del pipeline. Global, no depende de los
    // filtros del tablero. null si ninguna fuente está disponible.
    this.reportados = null;
    this.filters = {
      dateFrom: null,
      dateTo: null,
      search: [], // tokens, address-normalized (see setSearch)
      searchRaw: '', // original (non-normalized) text, for chip display
    };
    for (const def of FILTER_FIELDS) this.filters[def.field] = new Set();
    // Range filters live in their OWN namespace, not in this.filters: a field can
    // be BOTH a dropdown and a range (e.g. n_pisos), and sharing the key would let
    // the {min,max} object clobber the Set. Mutated in place (never reassigned) so
    // a held reference (a rendered control's closure) always sees the current value.
    this.ranges = {};
    for (const def of RANGE_FIELDS) this.ranges[def.field] = { min: null, max: null };
    this.options = {}; // field -> sorted unique values present in data (+ '__none__' when applicable)
    this.dateBounds = { min: null, max: null };
    this.rangeBounds = {}; // field -> { min, max } observed in data, for input placeholders
    this.listeners = new Set();
    this.selectedId = null; // ObjectID highlighted from table -> map
  }

  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  notify() {
    for (const fn of this.listeners) fn(this);
  }

  // `bust` forces a network re-fetch bypassing HTTP cache — only needed right
  // after we KNOW the published JSON changed (manual "Actualizar datos",
  // poll detecting a new generated_at). Otherwise we let vercel.json's
  // Cache-Control (max-age=120, stale-while-revalidate) do its job: repeat
  // navigations/reloads and the 15-min auto-refresh don't re-download the
  // full 3.5MB file when nothing changed server-side.
  async load({ bust = false } = {}) {
    const { q, opts } = bustParams(bust);
    // israelRecords: colección Firestore SEPARADA (Inspectores de Israel). Se trae
    // en paralelo y nunca lanza (devuelve [] ante cualquier fallo), así el tablero
    // de Cali carga aunque Firestore no responda.
    // Reportados: fire-and-forget A PROPÓSITO. /api/reportados puede tardar
    // ~1-2 min cuando la caché CDN está fría (camina toda la API) y esperarlo
    // acá dejaba el tablero en blanco. Cuando llega, notify() pinta el KPI.
    this.refreshReportados().catch(() => {});
    const [metaRes, dataRes, israelRecords] = await Promise.all([
      fetchData('meta.json', { q, opts }),
      fetchData('inspections.json', { q, opts }),
      fetchIsraelRecords(),
    ]);
    if (!metaRes.ok || !dataRes.ok) {
      throw new Error(`HTTP ${metaRes.status}/${dataRes.status}`);
    }
    const meta = await metaRes.json();
    const caliRecords = await dataRes.json();
    this.meta = meta;
    // Cali = 'cali', Israel ya viene con fuente:'israel'. Ambos se procesan igual
    // (índice de búsqueda, buckets, suspensión) para que se comporten idéntico.
    const records = [
      ...caliRecords.map((r) => ({ ...r, fuente: 'cali' })),
      ...israelRecords,
    ];
    this.records = records.map((r) => ({
      ...r,
      _search: buildSearchIndex(r),
      n_pisos_rango: bucketNpisos(r.n_pisos),
      suspension_servicios: suspensionServicios(r),
    }));
    this.computeOptions();
    this.computeDateBounds();
    this.computeRangeBounds();
    this.applyFilters();
  }

  // KPI "Reportados" = solo los reportes en estado "Reportado" (decisión del
  // usuario 2026-08-20), leídos EN VIVO de /api/reportados. SIN fallback: si la
  // API no responde, no trae el campo, o el valor no es un número, el KPI se
  // pone en null y se OCULTA (kpi.js) — nunca se muestra un dato viejo o de otra
  // fuente que pueda contradecir a la API. Con bust=true agrega un query param
  // único que saltea la caché CDN de 15 min (botón "Actualizar datos").
  async refreshReportados({ bust = false } = {}) {
    let val = null;
    try {
      const res = await fetch(bust ? `/api/reportados?refresh=${Date.now()}` : '/api/reportados');
      if (res.ok) {
        const n = (await res.json())?.por_estadoVerificacion?.Reportado;
        if (typeof n === 'number' && Number.isFinite(n)) val = n;
      }
    } catch { /* sin red / respuesta malformada: val queda null → KPI oculto */ }
    // Propagar incluso null: si la lectura falla, el KPI debe ocultarse, no
    // conservar el último valor conocido.
    if (val !== this.reportados) {
      this.reportados = val;
      this.notify();
    }
    return val;
  }

  computeOptions() {
    const opts = {};
    for (const def of FILTER_FIELDS) {
      const set = new Set();
      let hasEmpty = false;
      for (const r of this.records) {
        const raw = r[def.field];
        if (def.multiValue) {
          const parts = splitMultiValue(raw);
          if (parts.length === 0) hasEmpty = true;
          else parts.forEach((p) => set.add(p));
        } else if (raw === null || raw === undefined || raw === '') {
          hasEmpty = true;
        } else {
          set.add(raw);
        }
      }
      // Ordinal fields declare an explicit `order`; everything else sorts
      // alphanumerically. Values outside the declared order fall to the end.
      let values;
      if (def.order) {
        const rank = (v) => { const i = def.order.indexOf(v); return i < 0 ? def.order.length : i; };
        values = [...set].sort((a, b) => rank(a) - rank(b) || String(a).localeCompare(String(b), 'es', { numeric: true }));
      } else {
        values = [...set].sort((a, b) => String(a).localeCompare(String(b), 'es', { numeric: true }));
      }
      if (def.emptyLabel && hasEmpty) values = [...values, NONE];
      opts[def.field] = values;
    }
    this.options = opts;
  }

  computeDateBounds() {
    let min = null;
    let max = null;
    for (const r of this.records) {
      const d = cleanDate(r.fecha_inspeccion);
      if (!d) continue;
      if (min === null || d < min) min = d;
      if (max === null || d > max) max = d;
    }
    this.dateBounds = { min, max };
  }

  computeRangeBounds() {
    const bounds = {};
    for (const def of RANGE_FIELDS) {
      let min = null;
      let max = null;
      for (const r of this.records) {
        const raw = r[def.field];
        if (raw === null || raw === undefined || raw === '') continue;
        const n = Number(raw);
        if (Number.isNaN(n)) continue;
        if (min === null || n < min) min = n;
        if (max === null || n > max) max = n;
      }
      bounds[def.field] = { min, max };
    }
    this.rangeBounds = bounds;
  }

  setFilter(field, value) {
    this.filters[field] = value;
    this.applyFilters();
  }

  toggleMultiFilter(field, value) {
    const set = this.filters[field];
    if (set.has(value)) set.delete(value);
    else set.add(value);
    this.applyFilters();
  }

  /** @param {string} field @param {'min'|'max'} part @param {string|number|null} value raw input value */
  setRangeFilter(field, part, value) {
    const num = value === null || value === '' ? null : Number(value);
    this.ranges[field][part] = (num === null || Number.isNaN(num)) ? null : num;
    this.applyFilters();
  }

  clearRangeFilter(field) {
    this.ranges[field].min = null;
    this.ranges[field].max = null;
    this.applyFilters();
  }

  setSearch(text) {
    // Address-aware normalization (abbreviations, separators, digit/letter
    // splits) + token list so "Cra 44a 10-25" and "carrera 44 10-25" match.
    this.filters.search = normalizeAddressText(text).split(' ').filter(Boolean);
    this.filters.searchRaw = text || '';
    this.applyFilters();
  }

  clearFilters() {
    this.filters.dateFrom = null;
    this.filters.dateTo = null;
    this.filters.search = [];
    this.filters.searchRaw = '';
    for (const def of FILTER_FIELDS) this.filters[def.field].clear();
    for (const def of RANGE_FIELDS) {
      this.ranges[def.field].min = null;
      this.ranges[def.field].max = null;
    }
    this.applyFilters();
  }

  activeFilterCount() {
    let n = 0;
    if (this.filters.dateFrom) n++;
    if (this.filters.dateTo) n++;
    if (this.filters.search.length) n++;
    for (const def of FILTER_FIELDS) n += this.filters[def.field].size > 0 ? 1 : 0;
    for (const def of RANGE_FIELDS) {
      const r = this.ranges[def.field];
      if (r.min !== null || r.max !== null) n++;
    }
    return n;
  }

  applyFilters() {
    // Auto-swap an inverted date range instead of silently returning zero rows.
    // Mutates this.filters directly so the UI (date inputs) can re-sync from it.
    if (this.filters.dateFrom && this.filters.dateTo && this.filters.dateFrom > this.filters.dateTo) {
      const tmp = this.filters.dateFrom;
      this.filters.dateFrom = this.filters.dateTo;
      this.filters.dateTo = tmp;
    }
    // Same auto-swap for numeric ranges.
    for (const def of RANGE_FIELDS) {
      const r = this.ranges[def.field];
      if (r.min !== null && r.max !== null && r.min > r.max) {
        const tmp = r.min;
        r.min = r.max;
        r.max = tmp;
      }
    }
    const { dateFrom, dateTo, search } = this.filters;
    this.filtered = this.records.filter((r) => {
      const recordDate = cleanDate(r.fecha_inspeccion);
      if (dateFrom && (!recordDate || recordDate < dateFrom)) return false;
      if (dateTo && (!recordDate || recordDate > dateTo)) return false;
      for (const def of FILTER_FIELDS) {
        if (!matchesField(r, def, this.filters[def.field])) return false;
      }
      for (const def of RANGE_FIELDS) {
        if (!matchesRange(r, def, this.ranges[def.field])) return false;
      }
      if (search.length && !search.every((tok) => r._search.includes(tok))) return false;
      return true;
    });
    this.notify();
  }
}

export const store = new Store();
