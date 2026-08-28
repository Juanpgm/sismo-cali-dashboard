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
  // Severidad de daños (severidad_danos) — ordinal, mismo esquema cálido que
  // damage pero con 5 niveles (agrega medio_alto): verde -> amarillo -> naranja
  // -> rojo -> rojo oscuro.
  severidad: {
    sin_dano: '#22c55e',
    bajo: '#eab308',
    medio: '#f97316',
    medio_alto: '#ef4444',
    alto: '#b91c1c',
  },
  // Nominal categorical (uso_edificacion, severidad_danos fallback, etc.)
  // First 3 slots are validated all-pairs CVD-safe on the navy dark surface;
  // anything past the 3rd real category folds into "Otros" (muted gray).
  categorical: ['#3987e5', '#d95926', '#199e70'],
  // Paleta cualitativa amplia (Okabe-Ito, CVD-safe) para variables nominales con
  // muchas categorías, p.ej. uso_edificacion en el mapa de puntos.
  categoricalWide: ['#56b4e9', '#e69f00', '#009e73', '#cc79a7', '#0072b2', '#d55e00', '#f0e442', '#994f00'],
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
  // sticker (cruce con evaluaciones, ver mapview.js colorBy 'sticker')
  con: 'Con sticker',
  sin: 'Sin sticker',
  // fuente de datos (origen del levantamiento)
  cali: 'Levantamiento Cali',
  israel: 'Inspectores de Israel',
  // porcentaje de afectación en planta (afectacion_planta) — rangos ordinales
  ninguno: 'Sin afectación',
  menor_10: 'Menor al 10%',
  de_10_a_40: 'Del 10% al 40%',
  de_40_a_70: 'Del 40% al 70%',
  mayor_70: 'Mayor al 70%',
  // barrio_vereda_fuente — provenance of the resolved "Barrio / vereda" value.
  geo: 'Geográfico (basemap)',
  reportado: 'Reportado (inspector)',
  sin_dato: 'Sin dato',
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
  // "Barrio / vereda" — geo-first per the spatial intersection with the
  // barrios_veredas basemap (see resolve_barrio_vereda in refresh_data.py).
  // barrio_vereda_resuelto is the resolved value shown as THE column; the
  // two source fields keep their own (reportado)/(geo) labels for provenance.
  barrio_vereda_resuelto: 'Barrio / vereda',
  barrio_vereda: 'Barrio / vereda (reportado)',
  barrio_vereda_fuente: 'Barrio / vereda (fuente)',
  direccion: 'Dirección',
  comuna: 'Comuna / corregimiento',
  barrio_geo: 'Barrio / vereda (geo)',
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
  afectacion_planta: 'Porcentaje de afectación en planta',
  afectacion_planta_calc: 'Afectación en planta (calculada)',
  severidad_danos: 'Severidad de daños',
  severidad_danos_calc: 'Severidad de daños (calculada)',
  nivel_dano: 'Nivel de daño',
  riesgo_ab: 'Riesgo A-B',
  riesgo_ac: 'Riesgo A-C',
  habitabilidad_calc: 'Habitabilidad (calculada)',
  criterio_habitabilidad: 'Criterio de habitabilidad',
  suspension_servicios: 'Suspensión de servicios',
  sticker: 'Sticker',
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
    'barrio_vereda_resuelto', 'barrio_vereda', 'barrio_geo', 'barrio_vereda_fuente',
    'direccion', 'tipo_propiedad', 'relacion_edificacion', 'otro',
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
    'riesgo_ab', 'riesgo_ac', 'habitabilidad_calc', 'criterio_habitabilidad', 'suspension_servicios',
    'sticker', 'justificacion_criterio',
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

/** Short day/month/hour:minute timestamp (roster "corte" / gestión "corte" notes). */
export function formatTs(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso)
    : d.toLocaleString('es-CO', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function formatValue(field, value) {
  if (value === null || value === undefined || value === '') return 'Sin dato';
  if (field === 'fecha_inspeccion') return formatDate(value);
  if (field === 'fecha_hora' || field === 'CreationDate' || field === 'EditDate') return formatDateTime(value);
  if (['criterio_habitabilidad', 'habitabilidad_calc', 'nivel_dano', 'severidad_danos', 'severidad_danos_calc', 'afectacion_planta', 'afectacion_planta_calc', 'sticker', 'barrio_vereda_fuente']
    .includes(field)) return labelForCode(value);
  if (typeof value === 'string' && /^(si|sí|no)$/i.test(value.trim())) return labelForCode(value);
  return String(value);
}

/** "Barrio / vereda" as it should read on screen: the resolved value
 *  (geo-first product of the spatial join against barrios_veredas, falling
 *  back to the inspector's typed value — see resolve_barrio_vereda in
 *  refresh_data.py), with a small "(reportado)" marker whenever the value
 *  came from the fallback so it's never mistaken for a geographic value.
 *  Never returns blank for a record that has SOME value — 'Sin dato' only
 *  when barrio_vereda_fuente is 'sin_dato' (or the field predates this). */
function cleanBarrioValue(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  return s === '' ? null : s;
}

/**
 * Client-side port of `resolve_barrio_vereda()` in scripts/refresh_data.py:
 * the geographic intersection against the barrios_veredas basemap wins over
 * the inspector's free-typed value, falling back to it (tagged `reportado`)
 * when the point lands outside every polygon.
 *
 * Applied at load time to EVERY record because data.js filters through raw
 * `record[def.field]` access: a display-time fallback alone would leave the
 * "Barrio / vereda" dropdown empty for every record the pipeline has not
 * rewritten yet — i.e. all of web/data/inspections.json until the next run,
 * plus the israel source, which never passes through the pipeline at all.
 *
 * Values the pipeline already resolved are returned untouched: it is the
 * authority, this is only the catch-up path.
 */
export function resolveBarrioVereda(record) {
  const yaResuelto = cleanBarrioValue(record?.barrio_vereda_resuelto);
  if (yaResuelto !== null) {
    return {
      barrio_vereda_resuelto: yaResuelto,
      barrio_vereda_fuente: record?.barrio_vereda_fuente || 'geo',
    };
  }

  const geo = cleanBarrioValue(record?.barrio_geo);
  if (geo !== null) {
    return { barrio_vereda_resuelto: geo, barrio_vereda_fuente: 'geo' };
  }

  const tipeado = cleanBarrioValue(record?.barrio_vereda);
  if (tipeado !== null) {
    return { barrio_vereda_resuelto: tipeado, barrio_vereda_fuente: 'reportado' };
  }

  return { barrio_vereda_resuelto: null, barrio_vereda_fuente: 'sin_dato' };
}

export function barrioVeredaDisplay(record) {
  const clean = cleanBarrioValue;

  const resuelto = clean(record?.barrio_vereda_resuelto);
  if (resuelto !== null) {
    return record?.barrio_vereda_fuente === 'reportado' ? `${resuelto} (reportado)` : resuelto;
  }

  // No resolved column: the record never went through resolve_barrio_vereda().
  // That is NOT an edge case — it is every record already published in
  // web/data/inspections.json until the next pipeline run, plus the whole
  // israel source, which never passes through the pipeline at all. Re-apply
  // the same geo-first precedence here so shipping the UI ahead of the data
  // cannot blank out the dashboard.
  const geo = clean(record?.barrio_geo);
  if (geo !== null) return geo;

  const tipeado = clean(record?.barrio_vereda);
  if (tipeado !== null) return `${tipeado} (reportado)`;

  return 'Sin dato';
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

/** Color for a severidad_danos code (ordinal, warm ramp). */
export function severidadColor(code) {
  const key = normalize(code);
  return COLORS.severidad[key] || COLORS.unknown;
}

// Porcentaje de afectación en planta (afectacion_planta): string ranges stored
// in ascending severity order. Mapped onto the SAME 5-level severidad ramp
// (verde → rojo oscuro) so points/heat read like the other damage variables.
export const AFECTACION_ORDER = ['ninguno', 'menor_10', 'de_10_a_40', 'de_40_a_70', 'mayor_70'];
const AFECTACION_COLORS = {
  ninguno: COLORS.severidad.sin_dano,
  menor_10: COLORS.severidad.bajo,
  de_10_a_40: COLORS.severidad.medio,
  de_40_a_70: COLORS.severidad.medio_alto,
  mayor_70: COLORS.severidad.alto,
};

/** Color for an afectacion_planta range (ordinal, same warm ramp as severidad). */
export function afectacionColor(code) {
  return AFECTACION_COLORS[normalize(code)] || COLORS.unknown;
}

/** Ordinal level 0..4 of an afectacion_planta range; null when blank/unknown.
 *  Backs the heat weight and the choropleth average. */
export function afectacionLevel(code) {
  const i = AFECTACION_ORDER.indexOf(normalize(code));
  return i < 0 ? null : i;
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

/* ---- N.º de pisos buckets / suspensión de servicios / fetch cache-busting */
// Kept here (not in data.js) because data.js transitively imports the Firebase
// SDK (via israel-source.js -> firebase-config.js -> a bare https:// specifier).
// D2 (planeacion-flujo-confiable) made that chain lazy (`await import()`), so
// data.js itself no longer crashes Node's ESM loader on plain import — but
// utils.js still has zero imports of its own, so pure logic that needs a
// `node:assert` self-check (data.test.mjs) stays here rather than moving back;
// data.js re-exports these three so its own call sites/import surface don't change.

// Rangos de "N.º de pisos" en buckets de 3 (1–3, 4–6, 7–9, …). El dato de origen
// trae errores de captura (500, 91980…): valores fuera de un rango físico plausible
// (>60 pisos) se ignoran como outliers → "sin dato". Las etiquetas empiezan por su
// número menor para que el orden numérico (localeCompare numeric) las deje en orden.
const NPISOS_OUTLIER_MAX = 60;
export function bucketNpisos(v) {
  const n = Number(v);
  if (v === null || v === undefined || v === '' || Number.isNaN(n)
      || n < 1 || n > NPISOS_OUTLIER_MAX) return null;
  const b = Math.floor((n - 1) / 3);
  return `${b * 3 + 1}–${b * 3 + 3} pisos`;
}

// Suspensión de servicios: colapso (parcial O total) declarado Y criterio de
// habitabilidad no habitable (I1–I3). Un edificio colapsado y no habitable
// amerita corte. Misma regla que el pipeline (refresh_data.add_suspension_servicios);
// derivado en carga y viaja en el xlsx.
export function suspensionServicios(r) {
  const colapso = normalize(r.colapso_parcial) === 'si' || normalize(r.colapso_total) === 'si';
  return colapso && isNoHabitable(r) ? 'si' : 'no';
}

// Resuelve la contradicción "colapso_total Y colapso_parcial = sí" (22/1142
// registros en vivo, revisar_casos del pipeline la marca como "Colapso total y
// parcial simultáneos"). Regla acordada con el producto: ambos sí -> parcial.
// Misma regla que el pipeline (refresh_data.add_colapso_resuelto); los campos
// crudos colapso_total/colapso_parcial no se tocan, esto solo da a las tarjetas
// KPI/mapa un valor único para no contar un registro dos veces.
export function colapsoResuelto(r) {
  if (normalize(r.colapso_parcial) === 'si') return 'parcial';
  if (normalize(r.colapso_total) === 'si') return 'total';
  return 'ninguno';
}

// colapso_resuelto + its two filter-only si/no mirrors (see FILTER_FIELDS in
// data.js), computed together so callers never pay for colapsoResuelto(r)
// three times over the same record.
export function colapsoResueltoFields(r) {
  const resuelto = colapsoResuelto(r);
  return {
    colapso_resuelto: resuelto,
    colapso_total_resuelto: resuelto === 'total' ? 'si' : 'no',
    colapso_parcial_resuelto: resuelto === 'parcial' ? 'si' : 'no',
  };
}

// Cache-busting fetch params. `bust` MUST stay false on normal startup loads
// (lets vercel.json's Cache-Control do its job); true only for retry/refresh/poll
// paths that need a guaranteed-fresh fetch. See data.js load()'s comment for the why.
export function bustParams(bust) {
  const q = bust ? `?t=${Date.now()}` : '';
  const opts = bust ? { cache: 'no-store' } : {};
  return { q, opts };
}

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

/* ------------------------------------------------------------------ */
/* Toast + xlsx export helpers — shared by main.js (Panel) and          */
/* evaluaciones.js (Stickers), moved here to stop the duplication.      */
/* ------------------------------------------------------------------ */

/** Fire a transient toast. #toast-stack is static markup outside any tab
 *  (present in index.html regardless of which tab is active), so it is
 *  looked up per-call instead of cached at module load — any caller can
 *  toast without holding its own reference to the element. */
export function showToast(message, variant = 'success') {
  const toastStack = document.querySelector('#toast-stack');
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

// Fecha de generación del Excel = momento del clic (fecha de descarga). Devuelve
// dos formas: `legible` para una celda dentro del archivo y `slug` para el nombre.
export function downloadStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return {
    legible: d.toLocaleString('es-CO', { dateStyle: 'long', timeStyle: 'short' }),
    slug: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}`,
  };
}

/* ------------------------------------------------------------------ */
/* Shared searchable single-select combobox                            */
/* ------------------------------------------------------------------ */
// Generalized from stickers-asignacion.js's and planeacion.js's own (near-
// identical) `mountCombobox(comboEl, { inspectores, onSelect })` — those two
// stay as-is (inspector-roster-specific: load counts/over-cap styling), this
// is the reusable version for any {id, label} option list (e.g.
// puntos_solicitados.js's comuna/barrio pickers). Same markup contract as
// the two originals: an `<input role="combobox">` + a sibling
// `<ul role="listbox">`, styled by the existing `.asignacion-combo*` rules
// in styles.css (already generic, not inspector-specific).

/** Collapse leading zeros in digit runs ("03" -> "3") so an admin typing
 *  "comuna 3" matches comuna_barrios.json's zero-padded "COMUNA 03". Local to
 *  filterOptionsByLabel only — does NOT touch the shared normalize() used
 *  for address/other search elsewhere. */
function collapseLeadingZeros(str) {
  return str.replace(/\d+/g, (run) => run.replace(/^0+(?=\d)/, ''));
}

/** Default filter: case/accent-insensitive substring match on `label`,
 *  tolerant of missing leading zeros in numbers (e.g. "comuna 3" ⇄ "comuna 03"). */
export function filterOptionsByLabel(options, query) {
  const q = collapseLeadingZeros(normalize(query || ''));
  if (!q) return options;
  return options.filter((o) => collapseLeadingZeros(normalize(o.label)).includes(q));
}

/**
 * Mount a searchable single-select combobox on `input`/`listEl`.
 * `options`: [{id, label}]. `onSelect(id, option)` fires on pick — the input
 * is set to the picked option's label; the caller does NOT need to re-render
 * the input itself (unlike the two per-row originals, which re-render from
 * external state on every change). `filterFn(options, query)` overrides the
 * default substring match. Returns `{ setOptions(newOptions) }` so a
 * dependent combobox (e.g. barrio, scoped to a chosen comuna) can be
 * repopulated without re-mounting/double-binding listeners.
 */
// Before the user types anything, showing every option (e.g. 37 comunas,
// 400+ barrios for a big comuna) makes the dropdown pop up disproportionately
// tall right on focus. Cap that empty-query view; typing narrows it via the
// normal filter, which is uncapped (a real search shouldn't hide matches).
const EMPTY_QUERY_VISIBLE_LIMIT = 8;

export function mountCombobox(input, listEl, { options = [], onSelect = () => {}, filterFn = filterOptionsByLabel } = {}) {
  let allOptions = options;
  let visible = []; // [{id, label}] in current render order — matches what's actually on screen
  let active = -1;

  const close = () => { listEl.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };

  function render(query) {
    const q = query === undefined ? input.value : query;
    const matches = filterFn(allOptions, q);
    const isEmptyQuery = !normalize(q);
    visible = isEmptyQuery ? matches.slice(0, EMPTY_QUERY_VISIBLE_LIMIT) : matches;
    const rows = visible.map((o) => `<li role="option" class="asignacion-combo-option" data-id="${escapeHtml(o.id)}">
        <span class="asignacion-combo-name">${escapeHtml(o.label)}</span></li>`).join('')
      || '<li class="asignacion-combo-empty" aria-disabled="true">Sin coincidencias</li>';
    const hiddenCount = matches.length - visible.length;
    const hint = isEmptyQuery && hiddenCount > 0
      ? `<li class="asignacion-combo-hint" aria-disabled="true">Escribí para ver ${hiddenCount} más…</li>` : '';
    listEl.innerHTML = rows + hint;
    active = -1;
    listEl.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function highlight(i) {
    const items = [...listEl.querySelectorAll('.asignacion-combo-option')];
    if (!items.length || i < 0 || i >= visible.length) return;
    active = i;
    items.forEach((el, idx) => el.classList.toggle('is-active', idx === active));
    items[active].scrollIntoView({ block: 'nearest' });
  }

  function choose(id) {
    close();
    const opt = allOptions.find((o) => o.id === id);
    input.value = opt ? opt.label : '';
    onSelect(id, opt);
  }

  input.addEventListener('focus', () => { input.select(); render(''); });
  input.addEventListener('input', () => render());
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); if (listEl.hidden) render(''); highlight(active < 0 ? 0 : active + 1); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); highlight(active < 0 ? visible.length - 1 : active - 1); }
    else if (ev.key === 'Enter') { ev.preventDefault(); if (active >= 0 && visible[active]) choose(visible[active].id); }
    else if (ev.key === 'Escape') { close(); }
  });
  listEl.addEventListener('mousedown', (ev) => {
    // mousedown (not click) so it fires before the input's blur closes the list.
    const li = ev.target.closest('.asignacion-combo-option');
    if (!li) return;
    ev.preventDefault();
    choose(li.dataset.id);
  });
  input.addEventListener('blur', () => { setTimeout(close, 120); });

  return {
    setOptions(newOptions) { allOptions = newOptions; close(); },
  };
}

// xlsx (SheetJS, ~1MB) is only needed by the download buttons — load it on
// first click instead of blocking every page load with it.
let xlsxPromise = null;
export function loadXlsx() {
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
