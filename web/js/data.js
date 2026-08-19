// Data loading + global filter state (tiny pub/sub store, no framework).
import {
  normalizeAddressText, buildSearchIndex, splitMultiValue, labelForField,
  normalize, habBinary,
} from './utils.js';

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

// Rangos de "N.º de pisos" en buckets de 3 (1–3, 4–6, 7–9, …). El dato de origen
// trae errores de captura (500, 91980…): valores fuera de un rango físico plausible
// (>60 pisos) se ignoran como outliers → "sin dato". Las etiquetas empiezan por su
// número menor para que el orden numérico (localeCompare numeric) las deje en orden.
const NPISOS_OUTLIER_MAX = 60;
function bucketNpisos(v) {
  const n = Number(v);
  if (v === null || v === undefined || v === '' || Number.isNaN(n)
      || n < 1 || n > NPISOS_OUTLIER_MAX) return null;
  const b = Math.floor((n - 1) / 3);
  return `${b * 3 + 1}–${b * 3 + 3} pisos`;
}

// Suspensión de servicios: cruce colapso parcial × habitabilidad. Un registro
// con colapso parcial y clasificación de habitabilidad (habitable H o no
// habitable R1/R2/I1/I2/I3) amerita corte. Derivado en carga; viaja en el xlsx.
function suspensionServicios(r) {
  return normalize(r.colapso_parcial) === 'si' && habBinary(r) !== ''
    ? 'si' : 'no';
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

  async load() {
    const bust = `?t=${Date.now()}`;
    const [metaRes, dataRes] = await Promise.all([
      fetch(`data/meta.json${bust}`, { cache: 'no-store' }),
      fetch(`data/inspections.json${bust}`, { cache: 'no-store' }),
    ]);
    if (!metaRes.ok || !dataRes.ok) {
      throw new Error(`HTTP ${metaRes.status}/${dataRes.status}`);
    }
    const meta = await metaRes.json();
    const records = await dataRes.json();
    this.meta = meta;
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
      let values = [...set].sort((a, b) => String(a).localeCompare(String(b), 'es', { numeric: true }));
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
