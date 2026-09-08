// Reportes ciudadanos tab: every citizen report from atencionsismo
// informe/json, read from the public Blob snapshot reportes_ciudadanos.json
// (backend/app/services/reportes_ciudadanos.py, design D5/D6). Same shape
// of module as evaluaciones.js: pure classification/filter helpers exported
// for the Node self-check, then the DOM/Leaflet section below.
import { COLORS, escapeHtml, basemapTileUrl, normalize, loadXlsx, downloadStamp, showToast } from './utils.js';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
const CALI_BBOX = { latMin: 3.30, latMax: 3.55, lngMin: -76.60, lngMax: -76.40 };
const PAGE_SIZE = 100;

// API `estadoVerificacion` labels, worst-known first (KPI row order).
export const ESTADOS = [
  { key: 'Visitado crítico', label: 'visitado crítico', color: COLORS.status.i2 },
  { key: 'Evaluación especializada', label: 'evaluación especializada', color: COLORS.accent },
  { key: 'Visitado', label: 'visitado', color: COLORS.status.h },
  { key: 'Visita fallida', label: 'visita fallida', color: '#9AA5B1' },
  { key: 'Asignado', label: 'asignado', color: '#5B8DEF' },
  { key: 'Reportado', label: 'reportado', color: COLORS.unknown },
];
const SIN_ESTADO = { key: 'SIN_DATO', label: 'sin dato', color: COLORS.unknown };
const ESTADO_BY_NORM = new Map(ESTADOS.map((e) => [normalize(e.key), e]));

export function estadoDe(reporte) {
  return ESTADO_BY_NORM.get(normalize(String((reporte && reporte.estado) || ''))) || SIN_ESTADO;
}

// Self-declared damage at intake, worst first. Used for the distribution bar
// and the "afectación" colour mode; NOT a technical severity (see the
// exploratory report: use habitabilidad / sticker for that).
export const AFECTACIONES = [
  { key: 'COLAPSO TOTAL', color: COLORS.status.i2 },
  { key: 'COLAPSO PARCIAL', color: '#E0603C' },
  { key: 'RIESGO COLAPSO', color: COLORS.status.r2 },
  { key: 'DAÑO ESTRUCTURAL', color: '#E8B04B' },
  { key: 'DAÑO MAMPOSTERÍA', color: '#B8C56B' },
  { key: 'NO SE EVIDENCIA NINGÚN DAÑO', color: COLORS.status.h },
];
const AFECTACION_BY_NORM = new Map(AFECTACIONES.map((a) => [normalize(a.key), a]));
const colorAfectacion = (r) => (AFECTACION_BY_NORM.get(normalize(String((r && r.afectacion) || ''))) || SIN_ESTADO).color;

const STICKER_COLORS = { verde: COLORS.status.h, amarillo: COLORS.status.r2, rojo: COLORS.status.i2 };
export function colorSticker(reporte) {
  const c = String((reporte && reporte.sticker && reporte.sticker.color) || '').trim().toLowerCase();
  return STICKER_COLORS[c] || COLORS.unknown;
}

export const COLOR_MODES_REPORTES = {
  estado: { label: 'Estado', colorOf: (r) => estadoDe(r).color },
  afectacion: { label: 'Afectación', colorOf: colorAfectacion },
  sticker: { label: 'Sticker', colorOf: colorSticker },
};

export function contarPor(list, keyFn) {
  const out = {};
  for (const item of list) {
    const k = keyFn(item);
    out[k] = (out[k] || 0) + 1;
  }
  return out;
}

export function opcionesDe(list, campo) {
  const set = new Set();
  for (const r of list) {
    const v = String((r && r[campo]) || '').trim();
    if (v) set.add(v);
  }
  return [...set].sort((a, b) => a.localeCompare(b, 'es'));
}

export function applyFiltrosReportes(list, filtros) {
  const f = filtros || {};
  const q = f.search ? normalize(f.search) : '';
  return list.filter((r) => {
    if (f.estado && estadoDe(r).key !== f.estado) return false;
    if (f.afectacion && normalize(r.afectacion || '') !== normalize(f.afectacion)) return false;
    if (f.tipo && r.tipo_inmueble !== f.tipo) return false;
    if (f.comuna && r.comuna !== f.comuna) return false;
    if (f.barrio && r.barrio !== f.barrio) return false;
    if (f.sticker) {
      const color = String((r.sticker && r.sticker.color) || '').trim().toLowerCase();
      if (f.sticker === 'SIN_STICKER' ? color !== '' : color !== f.sticker) return false;
    }
    if (q) {
      const hay = normalize([r.direccion, r.nombre_edificio, r.barrio, r.id, r.sticker && r.sticker.numero]
        .filter(Boolean).join(' '));
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}
