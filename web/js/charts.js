/* global Chart */
// Chart.js 4 (UMD global via CDN, same pattern as Leaflet's `L`) — "Estadísticas" charts.
import {
  COLORS, themeColor, labelForCode, splitMultiValue, habCode, normalize, formatDate,
} from './utils.js';

const registry = new Map();

/** Destroy-and-recreate per render (fine at 91 rows; revisit with chart.update() if data grows). */
export function upsertChart(canvasId, config) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  const existing = registry.get(canvasId);
  if (existing) existing.destroy();
  const chart = new Chart(canvas, config);
  registry.set(canvasId, chart);
  return chart;
}

function baseOptions(overrides = {}) {
  const textPrimary = themeColor('--text-primary', '#f4f7fb');
  const textSecondary = themeColor('--text-secondary', '#b9c4d4');
  const textMuted = themeColor('--text-muted', '#7c8ca3');
  const border = themeColor('--border', 'rgba(255,255,255,0.10)');
  const surface2 = themeColor('--surface-2', '#1a3a63');

  const defaultScales = {
    x: {
      grid: { color: border },
      ticks: { color: textMuted, font: { size: 11 } },
    },
    y: {
      beginAtZero: true,
      grid: { color: border },
      ticks: { color: textMuted, font: { size: 11 } },
    },
  };
  const defaultPlugins = {
    legend: {
      display: false,
      position: 'bottom',
      labels: { color: textSecondary, usePointStyle: true, boxWidth: 8, boxHeight: 8, font: { size: 11 } },
    },
    tooltip: {
      backgroundColor: surface2,
      titleColor: textPrimary,
      bodyColor: textPrimary,
      borderColor: border,
      borderWidth: 1,
      padding: 10,
      cornerRadius: 6,
      displayColors: true,
    },
  };

  // Deep-merge one level: a caller passing `scales: { y: {...} }` or
  // `plugins: { legend: {...} }` must not wipe out the themed defaults for the
  // sibling keys it didn't mention (this previously dropped the dark-theme
  // tooltip/legend styling and axis tick colors wholesale).
  const { scales: scalesOverride, plugins: pluginsOverride, ...rest } = overrides;
  const mergedScales = {
    ...scalesOverride,
    x: { ...defaultScales.x, ...(scalesOverride && scalesOverride.x) },
    y: { ...defaultScales.y, ...(scalesOverride && scalesOverride.y) },
  };
  const mergedPlugins = {
    ...defaultPlugins,
    ...pluginsOverride,
    legend: { ...defaultPlugins.legend, ...(pluginsOverride && pluginsOverride.legend) },
    tooltip: { ...defaultPlugins.tooltip, ...(pluginsOverride && pluginsOverride.tooltip) },
  };

  return {
    maintainAspectRatio: false,
    responsive: true,
    animation: { duration: 200 },
    color: textPrimary,
    scales: mergedScales,
    plugins: mergedPlugins,
    ...rest,
  };
}

function renderByComuna(records) {
  const counts = new Map();
  for (const r of records) {
    const key = r.comuna || 'Sin comuna';
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  upsertChart('chart-comuna', {
    type: 'bar',
    data: {
      labels: sorted.map(([k]) => k),
      datasets: [{
        label: 'Inspecciones',
        data: sorted.map(([, v]) => v),
        backgroundColor: themeColor('--accent', '#FFC400'),
        borderRadius: 4,
        maxBarThickness: 22,
        categoryPercentage: 0.7,
        barPercentage: 0.8,
      }],
    },
    options: baseOptions({
      indexAxis: 'y',
      scales: {
        x: { beginAtZero: true, grid: { color: themeColor('--border', 'rgba(255,255,255,0.10)') } },
        y: { grid: { display: false } },
      },
    }),
  });
}

const DANO_ORDER = ['sin_dano', 'bajo', 'medio', 'alto'];

function renderByNivelDano(records) {
  const counts = new Map(DANO_ORDER.map((k) => [k, 0]));
  for (const r of records) {
    const key = normalize(r.nivel_dano);
    if (counts.has(key)) counts.set(key, counts.get(key) + 1);
  }
  upsertChart('chart-dano', {
    type: 'bar',
    data: {
      labels: DANO_ORDER.map(labelForCode),
      datasets: [{
        label: 'Inspecciones',
        data: DANO_ORDER.map((k) => counts.get(k)),
        backgroundColor: DANO_ORDER.map((k) => COLORS.damage[k]),
        borderRadius: 4,
        maxBarThickness: 40,
      }],
    },
    options: baseOptions(),
  });
}

function renderByUso(records) {
  const counts = new Map();
  for (const r of records) {
    for (const part of splitMultiValue(r.uso_edificacion)) {
      counts.set(part, (counts.get(part) || 0) + 1);
    }
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  upsertChart('chart-uso', {
    type: 'bar',
    data: {
      labels: sorted.map(([k]) => labelForCode(k)),
      datasets: [{
        label: 'Inspecciones',
        data: sorted.map(([, v]) => v),
        backgroundColor: themeColor('--accent', '#FFC400'),
        borderRadius: 4,
        maxBarThickness: 32,
        categoryPercentage: 0.7,
        barPercentage: 0.8,
      }],
    },
    options: baseOptions(),
  });
}

const HAB_ORDER = ['h', 'r1', 'r2', 'i1', 'i2', 'i3'];

function renderHabByComuna(records) {
  const comunas = [...new Set(records.map((r) => r.comuna).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, 'es', { numeric: true }));
  const grid = new Map(comunas.map((c) => [c, Object.fromEntries(HAB_ORDER.map((h) => [h, 0]))]));
  for (const r of records) {
    if (!r.comuna || !grid.has(r.comuna)) continue;
    const code = habCode(r);
    if (HAB_ORDER.includes(code)) grid.get(r.comuna)[code] += 1;
  }
  const surface = themeColor('--surface', '#12294a');
  const datasets = HAB_ORDER.map((code) => ({
    label: labelForCode(code),
    data: comunas.map((c) => grid.get(c)[code]),
    backgroundColor: COLORS.status[code],
    borderColor: surface,
    borderWidth: 2,
    stack: 'hab',
  }));
  upsertChart('chart-hab-comuna', {
    type: 'bar',
    data: { labels: comunas, datasets },
    options: baseOptions({
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, beginAtZero: true, grid: { color: themeColor('--border', 'rgba(255,255,255,0.10)') } },
      },
      plugins: { legend: { display: true } },
    }),
  });
}

function renderTimeSeries(records) {
  const byDay = new Map();
  for (const r of records) {
    const d = (r.fecha_inspeccion || '').trim();
    if (!d) continue;
    byDay.set(d, (byDay.get(d) || 0) + 1);
  }
  const days = [...byDay.keys()].sort();
  let running = 0;
  const cumulative = days.map((d) => (running += byDay.get(d)));
  const surface = themeColor('--surface', '#12294a');
  const accent = themeColor('--accent', '#FFC400');
  const secondary = COLORS.categorical[0];
  upsertChart('chart-timeseries', {
    type: 'line',
    data: {
      labels: days.map(formatDate),
      datasets: [
        {
          label: 'Diarias',
          data: days.map((d) => byDay.get(d)),
          borderColor: accent,
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 4,
          pointBackgroundColor: accent,
          pointBorderColor: surface,
          pointBorderWidth: 2,
          tension: 0.15,
        },
        {
          label: 'Acumuladas',
          data: cumulative,
          borderColor: secondary,
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 4,
          pointBackgroundColor: secondary,
          pointBorderColor: surface,
          pointBorderWidth: 2,
          tension: 0.15,
        },
      ],
    },
    options: baseOptions({ plugins: { legend: { display: true } } }),
  });
}

let warnedMissingChart = false;

/** Render all 5 "Estadísticas" charts from the current filtered record set. */
export function renderStatistics(records) {
  if (typeof Chart === 'undefined') {
    if (!warnedMissingChart) {
      console.warn('Chart.js no está disponible (falló la carga del CDN) — se omite "Estadísticas".');
      warnedMissingChart = true;
    }
    return;
  }
  renderByComuna(records);
  renderByNivelDano(records);
  renderByUso(records);
  renderHabByComuna(records);
  renderTimeSeries(records);
}
