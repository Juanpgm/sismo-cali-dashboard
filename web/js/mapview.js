// Map centerpiece: Puntos / Calor / Coroplético modes over Leaflet + leaflet.heat.
import {
  COLORS, habitabilityColor, damageColor, severidadColor, buildCategoricalScale,
  interpolateRamp, labelForCode, labelForField, formatValue, escapeHtml, normalize,
  isNoHabitableBinary, basemapTileUrl, afectacionColor, afectacionLevel, AFECTACION_ORDER,
  barrioVeredaDisplay, danoGradoColor, DANO_GRADO_ORDER,
} from './utils.js';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;

const DAMAGE_ORDER = { sin_dano: 0, bajo: 1, medio: 2, alto: 3 };

// Per-building numeric fields shared across map modes (size / intensity / metric).
// Values are the short noun used in legends and labels.
const NUM_FIELDS = {
  n_ocupantes: 'habitantes',
  n_pisos: 'pisos',
  n_sotanos: 'sótanos',
};

// Adjacent-building external-risk fields, moved here from the sidebar: they are
// yes/no flags, so points get a semantic 2-color scale (present = hazard, absent
// = clear) rather than an arbitrary categorical one.
const RISK_FIELDS = new Set(['41_a', '42_a', 'riesgo_caida']);
function riskColor(value) {
  const v = normalize(value);
  if (v === 'si') return COLORS.status.i2; // present → hazard (red)
  if (v === 'no') return COLORS.status.h;  // absent → clear (green)
  return COLORS.unknown;                   // blank → sin dato (gray)
}

let map = null;
let baseTile = null;
let pointsLayer = null;
let heatLayer = null;
let choroplethLayer = null;
let legendControl = null;
let legendEl = null;
let comunasGeo = null;
let barriosGeo = null;
let onDetailRequest = null;
let highlightMarker = null;

// Zonas de interés: optional overlay layer (checkbox in the toolbar), NOT a
// map mode. Independent of state.mode/clearLayers()/render() — added straight
// to `map` (never into pointsLayer/heatLayer/choroplethLayer), so mode
// switches never touch it. See setZonasInteresVisible.
let zonasInteresLayer = null; // L.geoJSON, built once on first enable
let zonasInteresGeo = null;
let zonasInteresOn = false;

// GlobalIDs of Panel points that already have a field sticker, from the cruce
// (api/sticker-status). Set by main.js when the store refreshes. Used by the
// 'sticker' colorBy mode. Empty until the coverage lookup resolves.
let stickerSet = new Set();
/** @param {string[]} ids GlobalIDs con sticker (registro_id del cruce). */
export function setStickerStatus(ids) {
  stickerSet = new Set((ids || []).map(String));
}
// Azul = con sticker · Rojo = sin sticker (mismo código que Stickers→Asignación).
const STICKER_CON = COLORS.categorical[0];
const STICKER_SIN = COLORS.status.i2;

const state = {
  mode: 'points', // points | heat | choropleth
  colorBy: 'criterio_habitabilidad',
  sizeBy: 'n_ocupantes', // none | n_ocupantes | n_pisos | n_sotanos
  heatWeight: 'count', // count | victims | damage | n_ocupantes | n_pisos | n_sotanos
  choroplethLevel: 'comuna', // comuna | barrio
  choroplethMetric: 'count', // count | no_habitables | victims | n_ocupantes | n_pisos | n_sotanos
};

export function getState() {
  return state;
}

export function initMap(containerId, { onDetail } = {}) {
  onDetailRequest = onDetail || null;
  map = L.map(containerId, {
    zoomControl: true,
    minZoom: 10,
    maxZoom: 18,
  }).setView(CALI_CENTER, CALI_ZOOM);

  baseTile = L.tileLayer(basemapTileUrl(), {
    attribution: TILE_ATTRIBUTION,
    subdomains: 'abcd',
    maxZoom: 20,
  }).addTo(map);

  pointsLayer = L.layerGroup();
  choroplethLayer = L.layerGroup();

  legendControl = L.control({ position: 'bottomright' });
  legendControl.onAdd = () => {
    legendEl = L.DomUtil.create('div', 'map-legend');
    L.DomEvent.disableClickPropagation(legendEl);
    return legendEl;
  };
  legendControl.addTo(map);

  return map;
}

export function invalidateSize() {
  if (map) map.invalidateSize();
}

/** Swap the base tiles to match the active theme (light/dark). */
export function applyMapTheme() {
  if (!map) return;
  if (baseTile) map.removeLayer(baseTile);
  baseTile = L.tileLayer(basemapTileUrl(), {
    attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20,
  }).addTo(map);
  baseTile.bringToBack();
}

// Casa = 3 pisos o menos · Edificación = más de 3 · sin dato de pisos = sin_dato.
function tipologiaDe(r) {
  const n = Number(r.n_pisos);
  if (!Number.isFinite(n) || n < 1) return 'sin_dato';
  return n <= 3 ? 'casa' : 'edificacion';
}
const TIPOLOGIA_COLORS = {
  casa: COLORS.categoricalWide[0],        // azul
  edificacion: COLORS.categoricalWide[1], // naranja
  sin_dato: COLORS.unknown,
};
const TIPOLOGIA_LABELS = { casa: 'Casa (≤3 pisos)', edificacion: 'Edificación (>3 pisos)', sin_dato: 'Sin dato de pisos' };
function tipologiaColor(r) { return TIPOLOGIA_COLORS[tipologiaDe(r)]; }

// Fuente de datos: distingue el levantamiento inicial de Cali del survey del
// equipo de Israel (colección Firestore aparte). 2 colores categóricos CVD-safe.
const FUENTE_COLORS = { cali: COLORS.categorical[0], israel: COLORS.categorical[1] };
const FUENTE_LABELS = { cali: 'Levantamiento Cali', israel: 'Inspectores de Israel' };
function fuenteColor(r) { return FUENTE_COLORS[normalize(r.fuente)] || COLORS.unknown; }

function pointColor(record) {
  switch (state.colorBy) {
    case 'nivel_dano':
      return damageColor(record.nivel_dano);
    case 'severidad_danos':
      return severidadColor(record.severidad_danos);
    case 'afectacion_planta':
      return afectacionColor(record.afectacion_planta);
    case 'danos_estructura':
      return danoGradoColor(record.danos_estructura);
    case 'tipologia':
      return tipologiaColor(record);
    case 'uso_edificacion':
      return dynamicColor('uso_edificacion', record.uso_edificacion);
    case 'fuente':
      return fuenteColor(record);
    case 'sticker':
      return stickerSet.has(String(record.GlobalID)) ? STICKER_CON : STICKER_SIN;
    case '41_a':
    case '42_a':
    case 'riesgo_caida':
      return riskColor(record[state.colorBy]);
    case 'criterio_habitabilidad':
    default:
      return habitabilityColor(record.criterio_habitabilidad || record.habitabilidad_calc);
  }
}

let dynamicScaleCache = { field: null, scale: null };
function dynamicColor(field, value) {
  return dynamicScaleCache.field === field
    ? dynamicScaleCache.scale.colorOf(value)
    : COLORS.unknown;
}

const MIN_RADIUS = 5;
const MAX_RADIUS = 18;
const BASE_RADIUS = 7;

/** Min/max of a numeric field over records (ignores blank/non-numeric). */
function numericBounds(records, field) {
  let min = null;
  let max = null;
  for (const r of records) {
    const v = Number(r[field]);
    if (Number.isNaN(v)) continue;
    if (min === null || v < min) min = v;
    if (max === null || v > max) max = v;
  }
  return { min: min ?? 0, max: max ?? 0 };
}

/** Marker radius scaled so circle AREA is proportional to the sizeBy value
 *  (sqrt of the normalized value). Uniform BASE_RADIUS when sizing is off. */
function radiusFor(record, bounds) {
  const field = state.sizeBy;
  if (field === 'none' || !bounds) return BASE_RADIUS;
  const v = Number(record[field]);
  if (Number.isNaN(v)) return MIN_RADIUS;
  const { min, max } = bounds;
  if (max <= min) return (MIN_RADIUS + MAX_RADIUS) / 2;
  const t = Math.sqrt((v - min) / (max - min));
  return MIN_RADIUS + t * (MAX_RADIUS - MIN_RADIUS);
}

function popupHtml(r) {
  const title = escapeHtml(r.nombre_edificacion || r.direccion || 'Sin nombre');
  const hab = r.criterio_habitabilidad || r.habitabilidad_calc;
  return `
    <div class="map-popup">
      <h4>${title}</h4>
      <dl>
        <dt>Barrio / vereda</dt><dd>${escapeHtml(barrioVeredaDisplay(r))}</dd>
        <dt>Fecha</dt><dd>${escapeHtml(formatValue('fecha_inspeccion', r.fecha_inspeccion))}</dd>
        <dt>Habitabilidad</dt><dd>${escapeHtml(labelForCode(hab))}</dd>
        <dt>Nivel de daño</dt><dd>${escapeHtml(labelForCode(r.nivel_dano))}</dd>
        <dt>Muertos / heridos</dt><dd>${Number(r.n_muertos) || 0} / ${Number(r.n_heridos) || 0}</dd>
        <dt>Habitantes / pisos / sótanos</dt><dd>${Number(r.n_ocupantes) || 0} / ${Number(r.n_pisos) || 0} / ${Number(r.n_sotanos) || 0}</dd>
      </dl>
      <button type="button" class="btn-link" data-detail-id="${escapeHtml(String(r.ObjectID))}">Ver detalle &rarr;</button>
    </div>
  `;
}

function renderPoints(records) {
  pointsLayer.clearLayers();
  const withCoords = records.filter((r) => r.x != null && r.y != null && !Number.isNaN(Number(r.x)) && !Number.isNaN(Number(r.y)));

  if (state.colorBy === 'uso_edificacion') {
    dynamicScaleCache = {
      field: state.colorBy,
      // Paleta amplia (8 hues CVD-safe) en vez de las 3 categóricas por defecto:
      // el uso tiene muchas categorías y la paleta corta apelmazaba casi todo en gris.
      scale: buildCategoricalScale(withCoords.map((r) => r[state.colorBy]), COLORS.categoricalWide),
    };
  }

  const sizeBounds = state.sizeBy === 'none' ? null : numericBounds(withCoords, state.sizeBy);

  for (const r of withCoords) {
    const marker = L.circleMarker([Number(r.y), Number(r.x)], {
      radius: radiusFor(r, sizeBounds),
      color: '#0B1D33',
      weight: 1,
      fillColor: pointColor(r),
      fillOpacity: 0.9,
      className: `point-marker point-${r.ObjectID}`,
    });
    marker.bindPopup(popupHtml(r), { maxWidth: 280 });
    marker.on('popupopen', (e) => {
      const btn = e.popup.getElement().querySelector('[data-detail-id]');
      if (btn && onDetailRequest) {
        btn.addEventListener('click', () => onDetailRequest(r.ObjectID));
      }
    });
    marker.addTo(pointsLayer);
  }

  renderPointsLegend(withCoords);
}

function renderPointsLegend(records) {
  let entries;
  let title;
  if (state.colorBy === 'nivel_dano') {
    title = 'Nivel de daño';
    entries = Object.entries(COLORS.damage).map(([code, color]) => ({ label: labelForCode(code), color }));
  } else if (state.colorBy === 'severidad_danos') {
    title = labelForField('severidad_danos');
    entries = Object.entries(COLORS.severidad).map(([code, color]) => ({ label: labelForCode(code), color }));
  } else if (state.colorBy === 'afectacion_planta') {
    title = labelForField('afectacion_planta');
    entries = AFECTACION_ORDER.map((code) => ({ label: labelForCode(code), color: afectacionColor(code) }));
    entries.push({ label: 'Sin dato', color: COLORS.unknown });
  } else if (state.colorBy === 'danos_estructura') {
    title = labelForField('danos_estructura');
    entries = DANO_GRADO_ORDER.map((code) => ({ label: labelForCode(code), color: danoGradoColor(code) }));
    entries.push({ label: 'Sin dato', color: COLORS.unknown });
  } else if (state.colorBy === 'tipologia') {
    title = 'Tipología (Casa / Edificación)';
    entries = Object.entries(TIPOLOGIA_COLORS).map(([code, color]) => ({ label: TIPOLOGIA_LABELS[code], color }));
  } else if (state.colorBy === 'uso_edificacion') {
    title = labelForField(state.colorBy);
    entries = dynamicScaleCache.field === state.colorBy
      ? dynamicScaleCache.scale.legend.map((e) => ({ label: e.label, color: e.color }))
      : [];
  } else if (state.colorBy === 'fuente') {
    title = 'Fuente de datos';
    entries = Object.entries(FUENTE_COLORS).map(([code, color]) => ({ label: FUENTE_LABELS[code], color }));
  } else if (state.colorBy === 'sticker') {
    title = 'Sticker (cruce con evaluaciones)';
    entries = [
      { label: 'Con sticker', color: STICKER_CON },
      { label: 'Sin sticker', color: STICKER_SIN },
    ];
  } else if (RISK_FIELDS.has(state.colorBy)) {
    title = labelForField(state.colorBy);
    entries = [
      { label: 'Presente (Sí)', color: COLORS.status.i2 },
      { label: 'Ausente (No)', color: COLORS.status.h },
      { label: 'Sin dato', color: COLORS.unknown },
    ];
  } else {
    title = 'Criterio de habitabilidad';
    entries = Object.entries(COLORS.status).map(([code, color]) => ({ label: labelForCode(code), color }));
    entries.push({ label: 'Sin dato', color: COLORS.unknown });
  }
  if (state.sizeBy !== 'none') title += ` · tamaño ∝ ${NUM_FIELDS[state.sizeBy]}`;
  // Total por categoría: mismo pointColor() que pinta cada punto, así que el
  // conteo siempre cuadra 1:1 con lo que se ve en el mapa (mismo `records` ya
  // filtrado a los que tienen coordenadas). Si dos categorías compartieran
  // color se sumarían juntas, pero cada escala de color de este archivo ya
  // asigna un color único por categoría — eso es lo que hace la leyenda legible.
  const countByColor = new Map();
  for (const r of records) {
    const c = pointColor(r);
    countByColor.set(c, (countByColor.get(c) || 0) + 1);
  }
  entries = entries.map((e) => ({ ...e, label: `${e.label} · ${countByColor.get(e.color) || 0}` }));
  setLegend(title, entries.map((e) => ({ ...e, shape: 'circle' })));
}

function weightFor(record) {
  switch (state.heatWeight) {
    case 'victims':
      return (Number(record.n_muertos) || 0) + (Number(record.n_heridos) || 0) + 0.15;
    case 'damage':
      return (DAMAGE_ORDER[normalize(record.nivel_dano)] ?? -1) + 1 || 0.2;
    case 'afectacion':
      // Ordinal 0..4 → weight 1..5; sin dato (null) keeps a faint 0.2, same
      // shape as 'damage' so "ninguno" points still register on the map.
      return (afectacionLevel(record.afectacion_planta) ?? -1) + 1 || 0.2;
    case 'n_ocupantes':
    case 'n_pisos':
    case 'n_sotanos':
      return Number(record[state.heatWeight]) || 0;
    case 'count':
    default:
      return 1;
  }
}

function renderHeat(records) {
  if (heatLayer) {
    map.removeLayer(heatLayer);
    heatLayer = null;
  }
  const points = records
    .filter((r) => r.x != null && r.y != null && !Number.isNaN(Number(r.x)) && !Number.isNaN(Number(r.y)))
    .map((r) => [Number(r.y), Number(r.x), weightFor(r)]);
  // Scale the ramp to the actual weight range; otherwise leaflet.heat's default
  // max of 1.0 saturates every point once weights exceed 1 (e.g. occupants).
  const maxWeight = points.reduce((m, p) => Math.max(m, p[2]), 0) || 1;
  heatLayer = L.heatLayer(points, { radius: 26, blur: 20, maxZoom: 17, minOpacity: 0.35, max: maxWeight });
  heatLayer.addTo(map);

  const label = state.heatWeight === 'victims' ? 'Intensidad: muertos + heridos'
    : state.heatWeight === 'damage' ? 'Intensidad: nivel de daño'
      : state.heatWeight === 'afectacion' ? 'Intensidad: afectación en planta'
        : NUM_FIELDS[state.heatWeight] ? `Intensidad: ${NUM_FIELDS[state.heatWeight]}`
          : 'Intensidad: número de inspecciones';
  setLegend(label, [
    { label: 'Baja', color: interpolateRamp(COLORS.choropleth, 0.15), shape: 'square' },
    { label: 'Media', color: interpolateRamp(COLORS.choropleth, 0.5), shape: 'square' },
    { label: 'Alta', color: interpolateRamp(COLORS.choropleth, 0.9), shape: 'square' },
  ]);
}

// In-flight loads for ensureGeo. Guarding only on the parsed result let every
// concurrent caller race its own fetch+parse of these multi-MB files before
// the first one landed — Evaluaciones resolves hundreds of points at once and
// produced a ~1000-request thundering herd that froze the tab. A failed load
// clears its slot so the next call can retry.
let comunasGeoLoad = null;
let barriosGeoLoad = null;

async function ensureGeo(level) {
  // Static boundaries (comunas/barrios), never regenerated by the pipeline —
  // safe to let the browser/CDN cache them normally (no bust needed).
  if (level === 'comuna' && !comunasGeo) {
    if (!comunasGeoLoad) {
      comunasGeoLoad = (async () => {
        const res = await fetch('data/comunas.geojson');
        if (!res.ok) throw new Error('No se pudo cargar comunas.geojson');
        comunasGeo = await res.json();
      })().catch((err) => { comunasGeoLoad = null; throw err; });
    }
    await comunasGeoLoad;
  }
  if (level === 'barrio' && !barriosGeo) {
    if (!barriosGeoLoad) {
      barriosGeoLoad = (async () => {
        const res = await fetch('data/barrios.geojson');
        if (!res.ok) throw new Error('No se pudo cargar barrios.geojson');
        barriosGeo = await res.json();
      })().catch((err) => { barriosGeoLoad = null; throw err; });
    }
    await barriosGeoLoad;
  }
}

// In-flight load for the zonas_interes overlay, same single-flight +
// clear-on-failure pattern as comunasGeoLoad/barriosGeoLoad above — kept
// separate from ensureGeo() on purpose: that function is choropleth wiring
// (comuna/barrio levels), while this is an independent optional overlay, and
// mixing the two would tangle unrelated concerns.
let zonasInteresGeoLoad = null;

async function ensureZonasInteresGeo() {
  if (!zonasInteresGeo) {
    if (!zonasInteresGeoLoad) {
      zonasInteresGeoLoad = (async () => {
        const res = await fetch('data/zonas_interes.geojson');
        if (!res.ok) throw new Error('No se pudo cargar zonas_interes.geojson');
        zonasInteresGeo = await res.json();
      })().catch((err) => { zonasInteresGeoLoad = null; throw err; });
    }
    await zonasInteresGeoLoad;
  }
  return zonasInteresGeo;
}

/** Builds the zonas_interes overlay layer. Defensive on malformed input (0
 *  features, missing/non-array `features`) — never throws. Exported only so
 *  the test harness can exercise this defensiveness directly, independent
 *  of the fetch/cache state machine in setZonasInteresVisible. */
export function buildZonasInteresLayer(geo) {
  const safeGeo = geo && Array.isArray(geo.features)
    ? geo
    : { type: 'FeatureCollection', features: [] };
  return L.geoJSON(safeGeo, {
    style: () => ({
      color: COLORS.accent,
      weight: 2,
      fillColor: COLORS.accent,
      fillOpacity: 0.12,
    }),
    onEachFeature: (feature, lyr) => {
      const name = (feature.properties && feature.properties.name) || 'Zona de interés';
      lyr.bindTooltip(escapeHtml(name), { sticky: true });
    },
  });
}

// Single-flight guard for the "turn on" flow itself (not just the fetch):
// without it, two concurrent setZonasInteresVisible(true) calls would both
// pass the `!zonasInteresOn` check before the first one finishes and each
// build/add its own layer. Cleared on both success and failure so a later
// toggle-on always starts fresh.
let zonasInteresEnabling = null;

/**
 * Shows/hides the zonas_interes overlay. Loaded on demand the first time it
 * is enabled (hidden by default); idempotent — calling it twice with the same
 * value is a no-op. Independent of state.mode: added straight to `map`, never
 * touched by clearLayers()/render(), so it survives every Puntos/Calor/
 * Coroplético switch.
 *
 * The layer is added with bringToBack() so it never sits above the
 * inspection markers/choropleth in z-order: Leaflet's SVG renderer paints
 * later-added layers on top, and pointsLayer/choroplethLayer are re-added on
 * every render() after this call, but bringToBack() also covers the case
 * where the overlay is switched on while points are already on the map (its
 * paths would otherwise land after — i.e. above — the existing marker
 * paths). Clicks on markers therefore keep hitting the marker (topmost),
 * never the polygon underneath.
 */
export async function setZonasInteresVisible(visible) {
  const wantOn = !!visible;
  if (!wantOn) {
    zonasInteresOn = false;
    if (zonasInteresLayer && map && map.hasLayer(zonasInteresLayer)) {
      map.removeLayer(zonasInteresLayer);
    }
    return;
  }
  if (zonasInteresOn) return; // already on: no-op, no duplicate layer/fetch
  if (!zonasInteresEnabling) {
    zonasInteresEnabling = (async () => {
      const geo = await ensureZonasInteresGeo();
      if (!zonasInteresLayer) {
        zonasInteresLayer = buildZonasInteresLayer(geo);
      }
      zonasInteresOn = true;
      if (map) {
        zonasInteresLayer.addTo(map);
        zonasInteresLayer.bringToBack();
      }
    })().catch((err) => {
      console.warn('No se pudo cargar zonas_interes.geojson:', err);
      zonasInteresOn = false;
      throw err;
    }).finally(() => { zonasInteresEnabling = null; });
  }
  return zonasInteresEnabling;
}

// Standard ray-casting point-in-polygon over one ring (array of [lng, lat]
// vertices). Odd number of edge crossings to the right of the point = inside.
function pointInRing(lat, lng, ring) {
  let inside = false;
  // j must trail i by one vertex (wrapping from the last back to 0) so every
  // edge gets tested exactly once. `j = i++` reads i BEFORE incrementing it;
  // `j = i += 1` (the previous bug here) reads i AFTER, so j and i landed on
  // the same vertex from the second iteration on — a zero-length "edge" that
  // never crosses anything, which silently broke every ring except the one
  // real edge tested on iteration 0. That's why every point resolved to null.
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const crosses = (yi > lat) !== (yj > lat)
      && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

// coordinates = [outerRing, ...holeRings]; inside the outer ring AND outside
// every hole, same convention as GeoJSON Polygon.coordinates.
function pointInPolygon(lat, lng, coordinates) {
  if (!pointInRing(lat, lng, coordinates[0])) return false;
  return coordinates.slice(1).every((hole) => !pointInRing(lat, lng, hole));
}

function pointInGeometry(lat, lng, geometry) {
  if (!geometry) return false;
  if (geometry.type === 'Polygon') return pointInPolygon(lat, lng, geometry.coordinates);
  if (geometry.type === 'MultiPolygon') return geometry.coordinates.some((poly) => pointInPolygon(lat, lng, poly));
  return false;
}

/** Name of the first feature of a FeatureCollection containing (lat, lng), or
 *  null when the point lands outside every polygon. */
function nameAt(lat, lng, geo) {
  const feature = geo && geo.features.find((f) => pointInGeometry(lat, lng, f.geometry));
  return feature ? feature.properties.name : null;
}

/**
 * Comuna/barrio containing (lat, lng), resolved against the SAME cached
 * boundaries the choropleth mode uses (ensureGeo) — a name lookup for
 * Evaluaciones (Stickers tab) to filter/export by comuna/barrio, not a
 * rendered layer, so it costs no extra fetch beyond what the choropleth
 * already lazily loads once.
 */
export async function resolveBarrioComuna(lat, lng) {
  await Promise.all([ensureGeo('comuna'), ensureGeo('barrio')]);
  return { comuna: nameAt(lat, lng, comunasGeo), barrio: nameAt(lat, lng, barriosGeo) };
}

function metricValue(records, metric) {
  if (metric === 'no_habitables') {
    return records.filter(isNoHabitableBinary).length;
  }
  if (metric === 'casas') {
    return records.filter((r) => tipologiaDe(r) === 'casa').length;
  }
  if (metric === 'edificaciones') {
    return records.filter((r) => tipologiaDe(r) === 'edificacion').length;
  }
  if (metric === 'victims') {
    return records.reduce((sum, r) => sum + (Number(r.n_muertos) || 0) + (Number(r.n_heridos) || 0), 0);
  }
  if (metric === 'afectacion_prom') {
    // Average ordinal level (0 sin afectación … 4 mayor al 70%) over records
    // that actually reported a value; blanks are ignored, not counted as 0.
    let sum = 0;
    let n = 0;
    for (const r of records) {
      const l = afectacionLevel(r.afectacion_planta);
      if (l != null) { sum += l; n += 1; }
    }
    return n ? Math.round((sum / n) * 10) / 10 : 0;
  }
  if (NUM_FIELDS[metric]) {
    return records.reduce((sum, r) => sum + (Number(r[metric]) || 0), 0);
  }
  return records.length;
}

async function renderChoropleth(records) {
  await ensureGeo(state.choroplethLevel);
  const geo = state.choroplethLevel === 'comuna' ? comunasGeo : barriosGeo;
  choroplethLayer.clearLayers();
  if (!geo) return;

  const keyField = state.choroplethLevel === 'comuna' ? 'comuna' : 'barrio_geo';
  const byName = new Map();
  for (const r of records) {
    const name = r[keyField];
    if (!name) continue;
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push(r);
  }

  const values = new Map();
  let maxVal = 0;
  for (const [name, recs] of byName.entries()) {
    const v = metricValue(recs, state.choroplethMetric);
    values.set(name, v);
    if (v > maxVal) maxVal = v;
  }

  // Mapeo por RANGO (rank) en vez de lineal v/maxVal: reparte la rampa por la
  // posición relativa del área entre las que tienen dato, así áreas con conteos
  // parecidos quedan visualmente diferenciables (el lineal apelmazaba casi todo
  // en el extremo oscuro con distribución sesgada). Piso 0.2 para que hasta la
  // más baja se despegue del fondo. Misma rampa de colores, solo cambia el mapeo.
  const nonzero = [...values.values()].filter((x) => x > 0).sort((a, b) => a - b);
  const rankT = (v) => {
    if (v <= 0 || nonzero.length === 0) return 0;
    if (nonzero.length === 1) return 1;
    return 0.2 + 0.8 * (nonzero.indexOf(v) / (nonzero.length - 1));
  };

  const layer = L.geoJSON(geo, {
    style: (feature) => {
      const name = feature.properties && feature.properties.name;
      const v = values.get(name) || 0;
      const t = rankT(v);
      return {
        color: 'rgba(255,255,255,0.25)',
        weight: 1,
        fillColor: v > 0 ? interpolateRamp(COLORS.choropleth, t) : COLORS.choropleth[0],
        fillOpacity: v > 0 ? 0.75 : 0.25,
      };
    },
    onEachFeature: (feature, lyr) => {
      const name = (feature.properties && feature.properties.name) || 'Sin nombre';
      const v = values.get(name) || 0;
      lyr.bindTooltip(`<strong>${escapeHtml(name)}</strong><br>${metricLabel(state.choroplethMetric)}: ${v}`, { sticky: true });
      lyr.on({
        mouseover: () => lyr.setStyle({ weight: 2, color: COLORS.accent }),
        mouseout: () => lyr.setStyle({ weight: 1, color: 'rgba(255,255,255,0.25)' }),
      });
    },
  });
  layer.addTo(choroplethLayer);

  // Leyenda coherente con el mapeo por rango: valor bajo / medio / alto entre las
  // áreas con dato, en su color de rango.
  const lo = nonzero[0] ?? 0;
  const mid = nonzero[Math.floor(nonzero.length / 2)] ?? 0;
  const hi = nonzero[nonzero.length - 1] ?? maxVal;
  setLegend(`${metricLabel(state.choroplethMetric)} por ${state.choroplethLevel === 'comuna' ? 'comuna' : 'barrio'}`, [
    { label: '0', color: COLORS.choropleth[0], shape: 'square' },
    { label: `${lo}`, color: interpolateRamp(COLORS.choropleth, 0.2), shape: 'square' },
    { label: `${mid}`, color: interpolateRamp(COLORS.choropleth, 0.6), shape: 'square' },
    { label: `${hi}`, color: interpolateRamp(COLORS.choropleth, 1), shape: 'square' },
  ]);
}

function metricLabel(metric) {
  if (metric === 'no_habitables') return 'No habitables';
  if (metric === 'casas') return 'Casas (≤3 pisos)';
  if (metric === 'edificaciones') return 'Edificaciones (>3 pisos)';
  if (metric === 'victims') return 'Muertos + heridos';
  if (metric === 'afectacion_prom') return 'Afectación en planta (nivel prom. 0–4)';
  if (NUM_FIELDS[metric]) return `Total ${NUM_FIELDS[metric]}`;
  return 'N° inspecciones';
}

function setLegend(title, entries) {
  if (!legendEl) return;
  if (!entries.length) {
    legendEl.innerHTML = '';
    legendEl.style.display = 'none';
    return;
  }
  legendEl.style.display = 'block';
  legendEl.innerHTML = `
    <div class="legend-title">${escapeHtml(title)}</div>
    ${entries.map((e) => `
      <div class="legend-row">
        <span class="legend-swatch legend-${e.shape}" style="background:${e.color}"></span>
        <span>${escapeHtml(e.label)}</span>
      </div>
    `).join('')}
  `;
}

function clearLayers() {
  map.removeLayer(pointsLayer);
  map.removeLayer(choroplethLayer);
  if (heatLayer) map.removeLayer(heatLayer);
}

export async function render(records) {
  if (!map) return;
  clearLayers();
  if (state.mode === 'points') {
    renderPoints(records);
    pointsLayer.addTo(map);
  } else if (state.mode === 'heat') {
    renderHeat(records);
  } else if (state.mode === 'choropleth') {
    await renderChoropleth(records);
    choroplethLayer.addTo(map);
  }
}

export function setMode(mode) { state.mode = mode; }
export function setColorBy(field) { state.colorBy = field; }
export function setSizeBy(field) { state.sizeBy = field; }
export function setHeatWeight(mode) { state.heatWeight = mode; }
export function setChoroplethLevel(level) { state.choroplethLevel = level; }
export function setChoroplethMetric(metric) { state.choroplethMetric = metric; }

export function highlightRecord(record) {
  if (!map || record.x == null || record.y == null) return;
  const latlng = [Number(record.y), Number(record.x)];
  if (highlightMarker) map.removeLayer(highlightMarker);
  highlightMarker = L.circleMarker(latlng, {
    radius: 12,
    color: COLORS.accent,
    weight: 3,
    fillOpacity: 0,
    className: 'highlight-marker',
  }).addTo(map);
  map.flyTo(latlng, Math.max(map.getZoom(), 15), { duration: 0.6 });
  setTimeout(() => {
    if (highlightMarker) { map.removeLayer(highlightMarker); highlightMarker = null; }
  }, 3500);
}

/** Build a tiny locator map inside the detail modal. */
export function buildMiniMap(containerEl, record) {
  if (record.x == null || record.y == null) {
    containerEl.innerHTML = '<div class="mini-map-empty">Sin coordenadas registradas</div>';
    return;
  }
  containerEl.innerHTML = '';
  const mini = L.map(containerEl, { zoomControl: false, attributionControl: false, dragging: false, scrollWheelZoom: false });
  L.tileLayer(basemapTileUrl(), { subdomains: 'abcd', maxZoom: 20 }).addTo(mini);
  const latlng = [Number(record.y), Number(record.x)];
  mini.setView(latlng, 16);
  L.circleMarker(latlng, { radius: 8, color: COLORS.accent, weight: 2, fillColor: COLORS.accent, fillOpacity: 0.6 }).addTo(mini);
  setTimeout(() => mini.invalidateSize(), 50);
}
