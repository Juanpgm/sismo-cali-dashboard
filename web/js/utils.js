// Shared helpers, label maps and color tokens for the dashboard.
// No framework, no build step — plain ES module consumed by main.js and friends.

/* ------------------------------------------------------------------ */
/* Color tokens (mirrors the CSS custom properties in styles.css)      */
/* ------------------------------------------------------------------ */

export const COLORS = {
  bg: '#0B1D33',
  surface: '#12294a',
  accent: '#FFC400',
  // Habitability (criterio_habitabilidad / habitabilidad_calc)
  status: {
    h: '#22c55e',
    r1: '#facc15',
    r2: '#eab308',
    i1: '#f87171',
    i2: '#ef4444',
    i3: '#b91c1c',
  },
  // Text color to use ON TOP of each status color (badges/legend chips).
  statusInk: {
    h: '#0B1D33',
    r1: '#0B1D33',
    r2: '#0B1D33',
    i1: '#0B1D33',
    i2: '#0B1D33',
    i3: '#ffffff',
  },
  // Damage level (nivel_dano) — sequential green -> yellow -> orange -> red
  damage: {
    sin_dano: '#22c55e',
    bajo: '#eab308',
    medio: '#f97316',
    alto: '#ef4444',
  },
  // Nominal categorical (uso_edificacion, severidad_danos fallback, etc.)
  // First 3 slots are validated all-pairs CVD-safe on the navy dark surface;
  // anything past the 3rd real category folds into "Otros" (muted gray).
  categorical: ['#3987e5', '#d95926', '#199e70'],
  categoricalOther: '#64748b',
  // Sequential ramp for choropleth (dark navy -> yellow), 5 discrete classes.
  choropleth: ['#12294a', '#2c4468', '#6b6142', '#c99a1a', '#FFC400'],
  unknown: '#475569',
};

/* ------------------------------------------------------------------ */
/* Category -> friendly Spanish label maps (known EDE codes)           */
/* ------------------------------------------------------------------ */

const KNOWN_LABELS = {
  // habitability (granular)
  h: 'Habitable',
  r1: 'Uso restringido R1',
  r2: 'Uso restringido R2',
  i1: 'No habitable I1',
  i2: 'No habitable I2',
  i3: 'No habitable I3 (insegura)',
  // habitability (binary — Momento 3 framing)
  habitable: 'Habitable',
  no_habitable: 'No habitable',
  // damage level
  sin_dano: 'Sin daño',
  bajo: 'Daño bajo',
  medio: 'Daño medio',
  alto: 'Daño alto',
  // generic damage-detail codes
  leve: 'Leve',
  moderado: 'Moderado',
  fuerte: 'Fuerte',
  severo: 'Severo',
  // yes/no
  si: 'Sí',
  'sí': 'Sí',
  no: 'No',
};

/** Strip accents + lowercase, for case/accent-insensitive matching. */
export function normalize(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim();
}

/** Colombian address way-type abbreviations -> single canonical short form.
 *  Longest variants first so e.g. "crra"/"carrera" both hit before "cr". */
const WAY_TYPE_MAP = [
  [/\b(calle|cll)\b/g, 'cl'],
  [/\b(carrera|carrea|crra|cra|krra|kra)\b/g, 'cr'],
  [/\b(avenida|aven)\b/g, 'av'],
  [/\b(diagonal|diag)\b/g, 'dg'],
  [/\b(transversal|trans)\b/g, 'tv'],
  [/\bcircular\b/g, 'cir'],
  [/\bautopista\b/g, 'au'],
  [/\bmanzana\b/g, 'mz'],
];

/** normalize() + address-aware fuzz: punctuation/separators -> spaces, way-type
 *  abbreviations collapsed to one canonical form, digit<->letter boundaries
 *  split ("44a" / "5B2" -> "44 a" / "5 b 2"), noise words dropped. Used to
 *  build the search index AND the query, so "Cra 44a #10-25" and
 *  "carrera 44 10-25" land on the same tokens. */
export function normalizeAddressText(str) {
  let s = normalize(str)
    .replace(/[#.,\-]/g, ' ')
    .replace(/\b(no|num)\b/g, ' ');
  for (const [re, canon] of WAY_TYPE_MAP) s = s.replace(re, canon);
  s = s
    .replace(/(\d)([a-z])/g, '$1 $2')
    .replace(/([a-z])(\d)/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim();
  return s;
}

/** Capitalize-and-space a raw snake_case field/code as a last-resort label. */
export function prettify(code) {
  if (code === null || code === undefined || code === '') return 'Sin dato';
  const str = String(code).replace(/^\d+_/, '').replace(/_/g, ' ').trim();
  return str.charAt(0).toUpperCase() + str.slice(1);
}

/** Map a known category code to its Spanish label, else prettify it. */
export function labelForCode(code) {
  if (code === null || code === undefined || code === '') return 'Sin dato';
  const key = normalize(code);
  if (Object.prototype.hasOwnProperty.call(KNOWN_LABELS, key)) return KNOWN_LABELS[key];
  return prettify(code);
}

/* ------------------------------------------------------------------ */
/* Field labels (record keys -> Spanish column/detail labels)          */
/* ------------------------------------------------------------------ */

const FIELD_LABELS = {
  ObjectID: 'ID de objeto',
  GlobalID: 'ID global',
  fecha_inspeccion: 'Fecha de inspección',
  hora: 'Hora',
  fecha_hora: 'Fecha y hora',
  nombre_evaluador: 'Evaluador',
  id_grupo: 'Grupo',
  entidad: 'Entidad',
  tipo_evento: 'Tipo de evento',
  nombre_edificacion: 'Edificación',
  municipio: 'Municipio',
  barrio_vereda: 'Barrio / vereda',
  direccion: 'Dirección',
  comuna: 'Comuna',
  barrio_geo: 'Barrio (geo)',
  tipo_propiedad: 'Tipo de propiedad',
  relacion_edificacion: 'Relación con la edificación',
  otro: 'Otro',
  epoca_construccion: 'Época de construcción',
  n_pisos: 'N.º de pisos',
  n_sotanos: 'N.º de sótanos',
  n_ocupantes: 'N.º de ocupantes',
  frente: 'Frente (m)',
  fondo: 'Fondo (m)',
  n_residenciales: 'Unidades residenciales',
  n_comerciales: 'Unidades comerciales',
  n_no_habitadas: 'Unidades no habitadas',
  n_muertos: 'Muertos',
  n_heridos: 'Heridos',
  acceso_edificacion: 'Acceso a la edificación',
  uso_edificacion: 'Uso de la edificación',
  uso_cual: 'Uso (cuál)',
  sistema_estructural: 'Sistema estructural',
  sistema_estructural_cual: 'Sistema estructural (cuál)',
  material_estructura: 'Material de la estructura',
  material_entrepiso: 'Material de entrepiso',
  sistema_entrepiso: 'Sistema de entrepiso',
  sistema_entrepiso_cual: 'Sistema de entrepiso (cuál)',
  existen_sistemas_combinados: 'Sistemas combinados',
  observaciones: 'Observaciones',
  sistema_cubierta: 'Sistema de cubierta',
  sistema_cubierta_cual: 'Sistema de cubierta (cuál)',
  revestimiento_cubierta: 'Revestimiento de cubierta',
  revestimiento_cubierta_cual: 'Revestimiento de cubierta (cuál)',
  sistema_muros_divisorios: 'Muros divisorios',
  sistema_muros_divisorios_cual: 'Muros divisorios (cuál)',
  fachadas: 'Fachadas',
  fachadas_cual: 'Fachadas (cuál)',
  escaleras: 'Escaleras',
  escaleras_cual: 'Escaleras (cuál)',
  calidad_construccion: 'Calidad de construcción',
  estado_edificacion: 'Estado de la edificación',
  '41_a': 'Caída de objetos de edificios adyacentes',
  '42_a': 'Colapso de edificios adyacentes',
  '46_especifique_cual': 'Otro riesgo (especifique)',
  colapso_total: 'Colapso total',
  colapso_parcial: 'Colapso parcial',
  asentamiento_severo: 'Asentamiento severo',
  inclinacion_importante: 'Inclinación importante',
  suelo_inestable: 'Suelo inestable',
  riesgo_caida: 'Riesgo de caída de elementos',
  danos_estructura: 'Daños en la estructura',
  danos_contrapiso_entrepiso_muroscont: 'Daños en contrapiso / entrepiso / muros de contención',
  danos_muro_div: 'Daños en muros divisorios',
  danos_cubierta: 'Daños en cubierta',
  cielos_instalaciones: 'Daños en cielos e instalaciones',
  alc_exterior: 'Alcantarillado exterior',
  alc_interior: 'Alcantarillado interior',
  matriz_ref: 'Matriz de referencia',
  afectacion_planta: 'Afectación en planta',
  afectacion_planta_calc: 'Afectación en planta (calculada)',
  severidad_danos: 'Severidad de daños',
  severidad_danos_calc: 'Severidad de daños (calculada)',
  nivel_dano: 'Nivel de daño',
  riesgo_ab: 'Riesgo A-B',
  riesgo_ac: 'Riesgo A-C',
  habitabilidad_calc: 'Habitabilidad (calculada)',
  criterio_habitabilidad: 'Criterio de habitabilidad',
  justificacion_criterio: 'Justificación del criterio',
  requiere_evaluacion_adicional: 'Requiere evaluación adicional',
  eval_estructural: 'Evaluación estructural',
  eval_geotecnica: 'Evaluación geotécnica',
  eval_otra: 'Otra evaluación',
  recomendaciones: 'Recomendaciones',
  aislamiento: 'Aislamiento',
  intervencion_entades: 'Intervención de entidades',
  observaciones_generales: 'Observaciones generales',
  evento_id: 'ID de evento',
  gps_precision_m: 'Precisión GPS (m)',
  CreationDate: 'Fecha de creación',
  Creator: 'Creado por',
  EditDate: 'Fecha de edición',
  Editor: 'Editado por',
  x: 'Longitud',
  y: 'Latitud',
};

export function labelForField(field) {
  if (Object.prototype.hasOwnProperty.call(FIELD_LABELS, field)) return FIELD_LABELS[field];
  return prettify(field);
}

/* ------------------------------------------------------------------ */
/* Detail modal grouping                                               */
/* ------------------------------------------------------------------ */

export const DETAIL_GROUPS = {
  'Identificación': [
    'ObjectID', 'GlobalID', 'evento_id', 'tipo_evento', 'entidad', 'nombre_evaluador', 'id_grupo',
    'fecha_inspeccion', 'hora', 'fecha_hora', 'nombre_edificacion', 'municipio', 'comuna',
    'barrio_vereda', 'barrio_geo', 'direccion', 'tipo_propiedad', 'relacion_edificacion', 'otro',
    'x', 'y', 'gps_precision_m', 'CreationDate', 'Creator', 'EditDate', 'Editor',
  ],
  'Estructura': [
    'epoca_construccion', 'n_pisos', 'n_sotanos', 'n_ocupantes', 'frente', 'fondo',
    'n_residenciales', 'n_comerciales', 'n_no_habitadas', 'acceso_edificacion', 'uso_edificacion',
    'uso_cual', 'sistema_estructural', 'sistema_estructural_cual', 'material_estructura',
    'material_entrepiso', 'sistema_entrepiso', 'sistema_entrepiso_cual', 'existen_sistemas_combinados',
    'sistema_cubierta', 'sistema_cubierta_cual', 'revestimiento_cubierta', 'revestimiento_cubierta_cual',
    'sistema_muros_divisorios', 'sistema_muros_divisorios_cual', 'fachadas', 'fachadas_cual',
    'escaleras', 'escaleras_cual', 'calidad_construccion', 'estado_edificacion', 'aislamiento',
  ],
  'Riesgos externos': [
    'colapso_total', 'colapso_parcial', 'asentamiento_severo', 'inclinacion_importante',
    'suelo_inestable', 'riesgo_caida', '46_especifique_cual',
  ],
  'Daños': [
    'danos_estructura', 'danos_contrapiso_entrepiso_muroscont', 'danos_muro_div', 'danos_cubierta',
    'cielos_instalaciones', 'alc_exterior', 'alc_interior', 'matriz_ref', 'afectacion_planta',
    'afectacion_planta_calc', 'severidad_danos', 'severidad_danos_calc', 'nivel_dano',
    'n_muertos', 'n_heridos',
  ],
  'Evaluación': [
    'riesgo_ab', 'riesgo_ac', 'habitabilidad_calc', 'criterio_habitabilidad', 'justificacion_criterio',
    'requiere_evaluacion_adicional', 'eval_estructural', 'eval_geotecnica', 'eval_otra',
    'intervencion_entades',
  ],
  'Observaciones': [
    'observaciones', 'recomendaciones', 'observaciones_generales',
  ],
};

/** Fields we render as badges (colored by known category maps). */
export const BADGE_FIELDS = new Set(['criterio_habitabilidad', 'habitabilidad_calc', 'nivel_dano']);

/* ------------------------------------------------------------------ */
/* Formatting helpers                                                  */
/* ------------------------------------------------------------------ */

export function formatDate(isoDate) {
  if (!isoDate) return 'Sin dato';
  const d = new Date(isoDate.length <= 10 ? `${isoDate}T00:00:00` : isoDate);
  if (Number.isNaN(d.getTime())) return String(isoDate);
  return d.toLocaleDateString('es-CO', { year: 'numeric', month: 'short', day: '2-digit' });
}

export function formatDateTime(iso) {
  if (!iso) return 'Sin dato';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('es-CO', {
    year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

export function formatValue(field, value) {
  if (value === null || value === undefined || value === '') return 'Sin dato';
  if (field === 'fecha_inspeccion') return formatDate(value);
  if (field === 'fecha_hora' || field === 'CreationDate' || field === 'EditDate') return formatDateTime(value);
  if (['criterio_habitabilidad', 'habitabilidad_calc', 'nivel_dano', 'severidad_danos', 'severidad_danos_calc']
    .includes(field)) return labelForCode(value);
  if (typeof value === 'string' && /^(si|sí|no)$/i.test(value.trim())) return labelForCode(value);
  return String(value);
}

export function debounce(fn, wait = 250) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

/** All string-ish fields of a record concatenated + normalized, for search. */
export function buildSearchIndex(record) {
  const parts = [];
  for (const key in record) {
    const v = record[key];
    if (v === null || v === undefined) continue;
    if (typeof v === 'string' || typeof v === 'number') parts.push(String(v));
  }
  return normalizeAddressText(parts.join(' | '));
}

/** Color for a habitability code. */
export function habitabilityColor(code) {
  const key = normalize(code);
  return COLORS.status[key] || COLORS.unknown;
}

/** Color for a damage-level code. */
export function damageColor(code) {
  const key = normalize(code);
  return COLORS.damage[key] || COLORS.unknown;
}

/**
 * Assign colors to a dynamic list of category values (nominal categorical).
 * First N (=slots.length) most-frequent values get the validated hues,
 * the rest fold into "Otros". Returns a Map value -> hex plus the ordered
 * legend entries [{ value, label, color }].
 */
export function buildCategoricalScale(values, slots = COLORS.categorical) {
  const counts = new Map();
  for (const v of values) {
    const key = v === null || v === undefined || v === '' ? null : String(v);
    if (key === null) continue;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  const map = new Map();
  const legend = [];
  sorted.forEach((val, i) => {
    if (i < slots.length) {
      map.set(val, slots[i]);
      legend.push({ value: val, label: labelForCode(val), color: slots[i] });
    } else {
      map.set(val, COLORS.categoricalOther);
    }
  });
  if (sorted.length > slots.length) {
    legend.push({ value: '__other__', label: 'Otros', color: COLORS.categoricalOther });
  }
  return { colorOf: (v) => map.get(String(v)) || COLORS.unknown, legend };
}

/** Linear interpolation across a hex ramp for continuous choropleth values. */
export function interpolateRamp(ramp, t) {
  const clamped = Math.max(0, Math.min(1, t));
  const scaled = clamped * (ramp.length - 1);
  const i = Math.floor(scaled);
  const frac = scaled - i;
  if (i >= ramp.length - 1) return ramp[ramp.length - 1];
  const c1 = hexToRgb(ramp[i]);
  const c2 = hexToRgb(ramp[i + 1]);
  const r = Math.round(c1.r + (c2.r - c1.r) * frac);
  const g = Math.round(c1.g + (c2.g - c1.g) * frac);
  const b = Math.round(c1.b + (c2.b - c1.b) * frac);
  return `rgb(${r}, ${g}, ${b})`;
}

function hexToRgb(hex) {
  const clean = hex.replace('#', '');
  return {
    r: parseInt(clean.substring(0, 2), 16),
    g: parseInt(clean.substring(2, 4), 16),
    b: parseInt(clean.substring(4, 6), 16),
  };
}

export function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ------------------------------------------------------------------ */
/* Habitability / multi-value / theme helpers                         */
/* ------------------------------------------------------------------ */

export const NO_HABITABLE_CODES = ['i1', 'i2', 'i3'];
export const RESTRINGIDO_CODES = ['r1', 'r2'];

/** Normalized habitability code for a record (criterio_habitabilidad || habitabilidad_calc). */
export function habCode(record) {
  return normalize(record.criterio_habitabilidad || record.habitabilidad_calc);
}

export function isNoHabitable(record) {
  return NO_HABITABLE_CODES.includes(habCode(record));
}

export function isRestringido(record) {
  return RESTRINGIDO_CODES.includes(habCode(record));
}

/* ---- Binary habitability (Momento 3 / PMU framing) --------------------- */
// Habitable = 'h'; No habitable = anything else that has a habitability code
// (r1/r2/i1/i2/i3). Blank code -> '' (sin dato), counted in neither bucket.
export function habBinary(record) {
  const c = habCode(record);
  if (c === 'h') return 'habitable';
  if (c === '') return '';
  return 'no_habitable';
}
export function isNoHabitableBinary(record) { return habBinary(record) === 'no_habitable'; }

/* ---- Source (pre-normalization) field labels from the EDAN-F3 excel ----- */
// Populated once from meta.json's `source_labels` on load; used ONLY for the
// display of selectable options (filters, "Colorear por"). Internal field keys
// and values are never touched.
let SOURCE_LABELS = {};
export function setSourceLabels(map) { SOURCE_LABELS = map || {}; }
export function sourceLabel(field, fallback) {
  return SOURCE_LABELS[field] || fallback || labelForField(field);
}

/** Split a comma-joined multi-value field (e.g. "residencial,comercial") into trimmed parts. */
export function splitMultiValue(value) {
  if (value === null || value === undefined || value === '') return [];
  return String(value).split(',').map((s) => s.trim()).filter(Boolean);
}

/** CARTO basemap tiles matching the active theme: Positron (light_all) when the
 *  page is in light mode, Dark Matter (dark_all) otherwise. */
export function basemapTileUrl() {
  const style = document.documentElement.dataset.theme === 'light' ? 'light_all' : 'dark_all';
  return `https://{s}.basemaps.cartocdn.com/${style}/{z}/{x}/{y}{r}.png`;
}

/** Read a CSS custom property off :root, with a fallback for non-DOM contexts. */
export function themeColor(varName, fallback = '') {
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return fallback;
  const val = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  return val || fallback;
}
