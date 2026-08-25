/* global Chart */
// Chart.js 4 (UMD global via CDN, same pattern as Leaflet's `L`) — "Estadísticas" charts.
import {
  COLORS, themeColor, labelForCode, labelForField, splitMultiValue, habCode, habBinary, normalize, formatDate,
  buildCategoricalScale, interpolateRamp,
} from './utils.js';

/** Colores de un set de valores por INTENSIDAD de un mismo hue (el acento):
 *  mayor valor = más intenso. Reusa interpolateRamp con una rampa de 2 paradas. */
function intensityColors(values) {
  const max = Math.max(1, ...values);
  const ramp = ['#4a3f14', themeColor('--accent', '#FFC400')]; // gold apagado → acento
  return values.map((v) => interpolateRamp(ramp, v / max));
}

const registry = new Map();

// Ayuda orientativa por gráfico: un ⓘ discreto junto al título que despliega una
// explicación breve de cómo interpretar el elemento. Se inyecta una sola vez.
const CHART_HELP = {
  'chart-timeseries': 'Ritmo de inspecciones en el tiempo. "Diarias" = inspecciones por día · "Acumuladas" = total corrido · "Momento 2 · Reportados" = universo de reportes ciudadanos, como referencia. El eje Y es logarítmico para que convivan magnitudes muy distintas; los totales van rotulados sobre cada línea.',
  'chart-tipologia': 'Cuántas casas (3 pisos o menos y uso residencial) y edificaciones (más de 3 pisos, o de uso no residencial aunque tengan pocos pisos) hay en cada color del semáforo: verde = habitable (H) · amarillo = uso restringido (R1/R2) · rojo = no habitable (I1/I2/I3).',
  'chart-hab-tipologia': 'Registros habitables vs. no habitables, separando casas de edificaciones. Habitable = H · No habitable = R1/R2/I1/I2/I3.',
  'chart-colapso-tipologia': 'Registros con colapso total, colapso parcial y sin colapso ("No colapsadas"), por casa y edificación.',
  'chart-severidad': 'Cómo se reparten las inspecciones según la severidad de los daños observados.',
  'chart-hab-doughnut': 'Distribución de la habitabilidad (H · R1 · R2 · I1 · I2 · I3) sobre las inspecciones filtradas.',
  'chart-uso-doughnut': 'Cuántas inspecciones por uso de la edificación. Usa los mismos colores que el mapa coloreado por uso.',
  'chart-suspension': 'Cuántas edificaciones requieren suspensión de servicios (colapso parcial cruzado con habitabilidad) frente al total visitado. La cifra central es requieren / total.',
  'chart-flags': 'Cuántas inspecciones marcaron "Sí" en cada riesgo o afectación, ordenadas de mayor a menor.',
  'chart-hab-comuna': 'Habitabilidad por comuna: cada barra es una comuna, segmentada por criterio de habitabilidad.',
  'chart-epoca': 'Inspecciones agrupadas por la época de construcción declarada de la edificación.',
};

function injectChartHelp() {
  for (const [id, text] of Object.entries(CHART_HELP)) {
    const canvas = document.getElementById(id);
    const tile = canvas && canvas.closest('.chart-tile');
    const title = tile && tile.querySelector('.chart-tile-title');
    if (!title || title.querySelector('.tile-help-btn')) continue;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tile-help-btn';
    btn.setAttribute('aria-label', 'Cómo interpretar este gráfico');
    btn.textContent = 'i';
    title.appendChild(btn);
    const body = document.createElement('div');
    body.className = 'tile-help-body';
    body.hidden = true;
    body.textContent = text;
    title.after(body);
    btn.addEventListener('click', () => { body.hidden = !body.hidden; });
  }
}

/** Filter-change render: mutate the existing Chart instance in place via
 *  update() when one of the same `type` already exists on this canvas — avoids
 *  a full destroy+recreate teardown/rebuild per filter change. Falls back to
 *  destroy+recreate when there's no existing chart, its type differs, or the
 *  caller passes `recreate: true` (needed by chart-suspension: its centerText
 *  plugin closes over requieren/total/pct baked in at construction, which
 *  update() would leave stale — see renderSuspension). Theme changes go
 *  through resetCharts() first instead, so they always take the recreate path. */
export function upsertChart(canvasId, config, { recreate = false } = {}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  const existing = registry.get(canvasId);
  if (existing && !recreate && existing.config.type === config.type) {
    existing.data = config.data;
    existing.options = config.options;
    existing.update('none');
    return existing;
  }
  if (existing) existing.destroy();
  const chart = new Chart(canvas, config);
  registry.set(canvasId, chart);
  return chart;
}

/** Destroy every registered chart and clear the registry. Used before a theme
 *  change re-render: Chart.js bakes CSS-variable colors in at construction, so
 *  update() alone won't repaint an existing instance with the new theme's
 *  colors — a full recreate is required. */
export function resetCharts() {
  for (const chart of registry.values()) chart.destroy();
  registry.clear();
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
        backgroundColor: intensityColors(sorted.map(([, v]) => v)),
        borderRadius: 4,
        maxBarThickness: 40,
        categoryPercentage: 0.8,
        barPercentage: 0.85,
      }],
    },
    options: baseOptions({
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, grid: { color: themeColor('--border', 'rgba(255,255,255,0.10)') } },
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
        backgroundColor: intensityColors(sorted.map(([, v]) => v)),
        borderRadius: 4,
        maxBarThickness: 32,
        categoryPercentage: 0.7,
        barPercentage: 0.8,
      }],
    },
    options: baseOptions(),
  });
}

// Chronological order + explicit labels: prettify() would mangle the range codes
// (its `^\d+_` strip turns "1984_1997" into "1997"), and none are in KNOWN_LABELS.
const EPOCA_ORDER = ['antes_1984', '1984_1997', '1998_2010', 'despues_2010', 'desconocida'];
const EPOCA_LABELS = {
  antes_1984: 'Antes de 1984',
  '1984_1997': '1984–1997',
  '1998_2010': '1998–2010',
  despues_2010: 'Después de 2010',
  desconocida: 'Desconocida',
};

function renderByEpoca(records) {
  const counts = new Map(EPOCA_ORDER.map((k) => [k, 0]));
  let sinDato = 0;
  for (const r of records) {
    const k = normalize(r.epoca_construccion);
    if (counts.has(k)) counts.set(k, counts.get(k) + 1);
    else if (!r.epoca_construccion) sinDato += 1;
  }
  const cats = EPOCA_ORDER.filter((k) => counts.get(k) > 0);
  const labels = cats.map((k) => EPOCA_LABELS[k]);
  const data = cats.map((k) => counts.get(k));
  if (sinDato > 0) { labels.push('Sin dato'); data.push(sinDato); }
  upsertChart('chart-epoca', {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Inspecciones',
        data,
        backgroundColor: intensityColors(data),
        borderRadius: 4,
        maxBarThickness: 40,
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

/** Show a message inside a chart tile instead of an (empty/broken) canvas. */
function setChartEmpty(canvasId, message) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const existing = registry.get(canvasId);
  if (existing) { existing.destroy(); registry.delete(canvasId); }
  canvas.style.display = 'none';
  const tile = canvas.closest('.chart-tile');
  let note = tile && tile.querySelector('.chart-empty');
  if (tile && !note) {
    note = document.createElement('p');
    note.className = 'chart-empty';
    tile.appendChild(note);
  }
  if (note) note.textContent = message;
}

/** Re-show a tile's canvas and drop any empty-state message. */
function clearChartEmpty(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  canvas.style.display = '';
  const note = canvas.closest('.chart-tile') && canvas.closest('.chart-tile').querySelector('.chart-empty');
  if (note) note.remove();
}

// Plugin inline: dibuja el valor total (ds._totalLabel) como etiqueta de dato
// sobre el último punto de la serie, en vez de meterlo en la leyenda.
const totalDataLabelPlugin = {
  id: 'totalDataLabel',
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    chart.data.datasets.forEach((ds, i) => {
      if (ds._totalLabel == null) return;
      const meta = chart.getDatasetMeta(i);
      const pt = meta.data[meta.data.length - 1];
      if (!pt) return;
      ctx.save();
      ctx.font = '700 12px system-ui, -apple-system, sans-serif';
      ctx.fillStyle = ds.borderColor;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'bottom';
      ctx.fillText(ds._totalLabel, pt.x - 4, pt.y - 5);
      ctx.restore();
    });
  },
};

function renderTimeSeries(records, reportados) {
  const byDay = new Map();
  let undated = 0; // registros del survey sin fecha_inspeccion (aún cuentan al total)
  for (const r of records) {
    const d = (r.fecha_inspeccion || '').trim();
    if (!d) { undated += 1; continue; }
    byDay.set(d, (byDay.get(d) || 0) + 1);
  }
  const days = [...byDay.keys()].sort();
  if (!days.length && !undated) {
    setChartEmpty('chart-timeseries', 'Sin registros en el survey para estos filtros.');
    return;
  }
  clearChartEmpty('chart-timeseries');
  // Etiquetas + conteo diario. Se agrega un bucket final "Sin fecha" con los
  // registros del survey sin fecha_inspeccion, para que el ACUMULADO llegue al
  // total real (= records.length, el mismo del KPI "Total registros"), no solo
  // a los que traen fecha. Todo se recomputa en cada render (se actualiza).
  const labels = days.map(formatDate);
  const daily = days.map((d) => byDay.get(d));
  if (undated > 0) { labels.push('Sin fecha'); daily.push(undated); }
  let running = 0;
  const cumulative = daily.map((n) => (running += n));
  const totalApp = running;
  const surface = themeColor('--surface', '#12294a');
  const accent = themeColor('--accent', '#FFC400');
  const secondary = COLORS.categorical[0];
  const fmt = (n) => Math.round(n).toLocaleString('es-CO');
  const datasets = [
    {
      label: 'Diarias',
      data: daily,
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
      _totalLabel: fmt(totalApp), // etiqueta de dato sobre el último punto
    },
  ];
  // Momento 2 (histórico): total de reportes de la API atencionsismo como línea de
  // referencia plana, para comparar el avance de la app contra el universo de
  // Momento 2 en escala logarítmica. Se actualiza con store.reportados (refresh).
  if (reportados != null && reportados > 0) {
    datasets.push({
      label: 'Momento 2 · Reportados',
      data: labels.map(() => reportados),
      borderColor: COLORS.status.i2,
      backgroundColor: 'transparent',
      borderWidth: 2,
      borderDash: [6, 4],
      pointRadius: 0,
      tension: 0,
      _totalLabel: fmt(reportados), // etiqueta de dato sobre la línea de referencia
    });
  }
  upsertChart('chart-timeseries', {
    type: 'line',
    data: { labels, datasets },
    plugins: [totalDataLabelPlugin],
    // Escala logarítmica: deja convivir el conteo diario (decenas), el acumulado
    // de la app (cientos) y el total Momento 2 (miles) en el mismo eje.
    // interaction mode 'index': al pasar/tocar cualquier punto del día se ven los
    // valores de las tres series a la vez (no hay que apuntar al punto exacto).
    options: baseOptions({
      interaction: { mode: 'index', intersect: false },
      scales: { y: { type: 'logarithmic' } },
      plugins: { legend: { display: true }, tooltip: { mode: 'index', intersect: false } },
    }),
  });
}

function isYes(v) {
  return normalize(v) === 'si';
}

// Meaningful, human-labeled Sí/No hazard/affectation flags. The remaining binary
// fields are opaque EDE damage-matrix sub-cells (41_b, 43_c, 45_a…) without a
// codebook, so they're left out of this summary rather than shown as "B"/"C".
const FLAG_FIELDS = [
  'colapso_total', 'colapso_parcial', '42_a', '41_a', 'riesgo_caida',
  'asentamiento_severo', 'inclinacion_importante', 'suelo_inestable',
  'existen_sistemas_combinados',
];

// Paleta propia del chart de flags, tomada de los tokens del front (severidad
// roja→amarilla + categóricos + gris) para que cada barra tenga color distinto
// pero coherente con el resto del tablero. Las barras van ordenadas por conteo,
// así que la más frecuente toma el rojo más intenso.
const FLAG_PALETTE = [
  COLORS.status.i3, COLORS.status.i2, COLORS.status.i1,
  COLORS.status.r2, COLORS.status.r1,
  COLORS.categorical[0], COLORS.categorical[1], COLORS.categorical[2],
  COLORS.categoricalOther,
];

/** Horizontal bar: count of "Sí" for every meaningful binary flag. */
function renderFlags(records) {
  const rows = FLAG_FIELDS
    .map((f) => ({ label: labelForField(f), count: records.reduce((n, r) => n + (isYes(r[f]) ? 1 : 0), 0) }))
    .sort((a, b) => b.count - a.count);
  const total = records.length;
  upsertChart('chart-flags', {
    type: 'bar',
    data: {
      labels: rows.map((r) => r.label),
      datasets: [{
        label: 'Casos con "Sí"',
        data: rows.map((r) => r.count),
        backgroundColor: rows.map((_, i) => FLAG_PALETTE[i % FLAG_PALETTE.length]),
        borderRadius: 4,
        maxBarThickness: 22,
      }],
    },
    options: baseOptions({
      indexAxis: 'y',
      scales: {
        x: { beginAtZero: true },
        y: { grid: { display: false } },
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.x;
              const pct = total ? Math.round((v / total) * 1000) / 10 : 0;
              return `${v} de ${total} (${pct}%)`;
            },
          },
        },
      },
    }),
  });
}

/** Doughnut: H · R1 · R2 · I1 · I2 · I3 distribution over the filtered set. */
function renderHabDoughnut(records) {
  const counts = Object.fromEntries(HAB_ORDER.map((c) => [c, 0]));
  for (const r of records) {
    const code = habCode(r);
    if (counts[code] !== undefined) counts[code] += 1;
  }
  const present = HAB_ORDER.filter((c) => counts[c] > 0);
  upsertChart('chart-hab-doughnut', {
    type: 'doughnut',
    data: {
      labels: present.map(labelForCode),
      datasets: [{
        data: present.map((c) => counts[c]),
        backgroundColor: present.map((c) => COLORS.status[c]),
        borderColor: themeColor('--surface', '#12294a'),
        borderWidth: 2,
      }],
    },
    options: baseOptions({ cutout: '55%', plugins: { legend: { display: true } } }),
  });
}

const SEV_ORDER = ['sin_dano', 'bajo', 'medio', 'medio_alto', 'alto'];
const SEV_COLORS = {
  sin_dano: COLORS.damage.sin_dano,
  bajo: COLORS.damage.bajo,
  medio: COLORS.damage.medio,
  medio_alto: '#fb7185',
  alto: COLORS.damage.alto,
};

/** Pie: damage-severity distribution over the filtered set. */
function renderSeveridad(records) {
  const counts = new Map(SEV_ORDER.map((k) => [k, 0]));
  for (const r of records) {
    const k = normalize(r.severidad_danos);
    if (counts.has(k)) counts.set(k, counts.get(k) + 1);
  }
  const present = SEV_ORDER.filter((k) => counts.get(k) > 0);
  upsertChart('chart-severidad', {
    type: 'pie',
    data: {
      labels: present.map(labelForCode),
      datasets: [{
        data: present.map((k) => counts.get(k)),
        backgroundColor: present.map((k) => SEV_COLORS[k]),
        borderColor: themeColor('--surface', '#12294a'),
        borderWidth: 2,
      }],
    },
    options: baseOptions({ plugins: { legend: { display: true } } }),
  });
}

/** Doughnut: building-use distribution over the filtered set (uso_edificacion
 *  is multi-value — a record counts toward every use it lists). Top 3 by
 *  count get the validated categorical palette; the rest fold into "Otros",
 *  same convention as the map's categorical color-by for this field. */
function renderUsoDoughnut(records) {
  // Mismo criterio y colores que el mapa de puntos por "Uso de la edificación":
  // categoría = valor crudo de uso_edificacion (no se parte), escala categórica
  // amplia (buildCategoricalScale con categoricalWide), top-N + "Otros".
  const scale = buildCategoricalScale(records.map((r) => r.uso_edificacion), COLORS.categoricalWide);
  const counts = new Map();
  for (const r of records) {
    const v = r.uso_edificacion;
    if (v == null || String(v).trim() === '') continue;
    counts.set(String(v), (counts.get(String(v)) || 0) + 1);
  }
  // buildCategoricalScale ya mete su propia entrada "Otros" (__other__); la
  // filtramos y recomputamos el conteo agregado de los overflow acá.
  const realLegend = scale.legend.filter((e) => e.value !== '__other__');
  const topValues = new Set(realLegend.map((e) => e.value));
  const labels = realLegend.map((e) => e.label);
  const data = realLegend.map((e) => counts.get(e.value) || 0);
  const colors = realLegend.map((e) => e.color);
  let otherCount = 0;
  for (const [v, c] of counts) if (!topValues.has(v)) otherCount += c;
  if (otherCount > 0) {
    labels.push('Otros');
    data.push(otherCount);
    colors.push(COLORS.categoricalOther);
  }
  const total = data.reduce((s, v) => s + v, 0);
  upsertChart('chart-uso-doughnut', {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Registros',
        data,
        backgroundColor: colors,
        borderRadius: 4,
        maxBarThickness: 22,
      }],
    },
    options: baseOptions({
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true } },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.y;
              const pct = total ? Math.round((v / total) * 1000) / 10 : 0;
              return `${v} (${pct}%)`;
            },
          },
        },
      },
    }),
  });
}

// ── Semáforo × tipología por número de pisos ────────────────────────────────
// Verde = H, amarillo = R1+R2, rojo = I1+I2+I3 (criterio_habitabilidad).
// Tipología: n_pisos <= 3 -> casa; > 3 -> edificación; vacío -> sin dato;
// numérico fuera de rango (< 1 o > 60, mismo outlier max que data.js) -> erróneo.
const SEMAFORO_DE = { h: 'verde', r1: 'amarillo', r2: 'amarillo', i1: 'rojo', i2: 'rojo', i3: 'rojo' };
const SEMAFORO_COLS = [
  ['verde', 'Verde', COLORS.status.h],
  ['amarillo', 'Amarillo', COLORS.status.r2],
  ['rojo', 'Rojo', COLORS.status.i2],
];
const TIPOLOGIAS = [
  ['edificacion', 'EDIFICACIÓN (más de 3 pisos)'],
  ['casa', 'CASA (3 pisos o menos)'],
  ['sin_dato', 'Sin dato de pisos'],
  ['erroneo', 'Dato de pisos erróneo'],
];
const NPISOS_MAX = 60;

/** Whether `uso_edificacion` (comma-joined, multi-value) includes 'residencial'. */
function esUsoResidencial(r) {
  return splitMultiValue(r.uso_edificacion).some((v) => normalize(v) === 'residencial');
}

export function tipologiaDe(r) {
  const raw = r.n_pisos;
  if (raw == null || String(raw).trim() === '') return 'sin_dato';
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 1 || n > NPISOS_MAX) return 'erroneo';
  // Una casa es, por definición, de uso residencial — un local de 2 pisos sin
  // uso residencial es una edificación aunque tenga pocos pisos.
  if (n <= 3) return esUsoResidencial(r) ? 'casa' : 'edificacion';
  return 'edificacion';
}

/** Conteos edificaciones + suma de unidades residenciales por tipología×semáforo.
 *  Solo registros con semáforo conocido (H/R/I), así todas las filas cuadran. */
export function tipologiaCounts(records) {
  const acc = Object.fromEntries(TIPOLOGIAS.map(([t]) => [
    t, { verde: { edif: 0, unid: 0 }, amarillo: { edif: 0, unid: 0 }, rojo: { edif: 0, unid: 0 } },
  ]));
  for (const r of records) {
    const sem = SEMAFORO_DE[habCode(r)];
    if (!sem) continue;
    const cell = acc[tipologiaDe(r)][sem];
    cell.edif += 1;
    const u = Number(r.n_residenciales);
    if (!Number.isNaN(u)) cell.unid += u;
  }
  return acc;
}

function renderTipologia(records) {
  const acc = tipologiaCounts(records);
  const surface = themeColor('--surface', '#12294a');
  upsertChart('chart-tipologia', {
    type: 'bar',
    data: {
      labels: TIPOLOGIAS.map(([, label]) => label.replace(/ \(.*\)/, '')),
      datasets: SEMAFORO_COLS.map(([k, label, color]) => ({
        label,
        data: TIPOLOGIAS.map(([t]) => acc[t][k].edif),
        backgroundColor: color,
        borderColor: surface,
        borderWidth: 2,
        stack: 'sem',
        maxBarThickness: 48,
      })),
    },
    options: baseOptions({
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, beginAtZero: true },
      },
      plugins: {
        legend: { display: true },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const t = TIPOLOGIAS[ctx.dataIndex][0];
              const k = SEMAFORO_COLS[ctx.datasetIndex][0];
              return `${ctx.dataset.label}: ${ctx.parsed.y} edif. · ${Math.round(acc[t][k].unid)} unid.`;
            },
          },
        },
      },
    }),
  });
}

// ── Habitabilidad y colapso por tipología (Casa/Edificio) ───────────────────
// Reusa tipologiaDe() (n_pisos <= 3 Y uso residencial -> casa; si no, edificación)
// y habBinary()
// (habitable = H · no habitable = R1/R2/I1/I2/I3). Cuatro métricas por tipología,
// en número de registros:
//   · Habitable      = habBinary == 'habitable'
//   · No habitable   = habBinary == 'no_habitable'
//   · Colapso total  = colapso_total == 'sí'
//   · Colapso parcial= colapso_parcial == 'sí'
const CH_METRICS = [
  ['habitable', 'Habitable', COLORS.status.h],
  ['no_habitable', 'No habitable', COLORS.status.i2],
  ['colapso_total', 'Colapso total', COLORS.status.i3],
  ['colapso_parcial', 'Colapso parcial', COLORS.status.r2],
];
// Métricas de las BARRAS: agrega "No colapsadas" (ni total ni parcial). No va en
// la tabla (que se queda en las 4 de CH_METRICS), solo en el gráfico de colapso
// para dar el contraste contra las colapsadas.
const CH_BAR_METRICS = [...CH_METRICS, ['no_colapso', 'No colapsadas', COLORS.status.h]];
export function colapsoHabCounts(records) {
  const acc = Object.fromEntries(TIPOLOGIAS.map(([t]) => [
    t, Object.fromEntries(CH_BAR_METRICS.map(([m]) => [m, { reg: 0, unid: 0 }])),
  ]));
  for (const r of records) {
    const cell = acc[tipologiaDe(r)];
    const u = Number(r.n_residenciales);
    const unid = Number.isNaN(u) ? 0 : u;
    const bin = habBinary(r); // 'habitable' | 'no_habitable' | '' (sin dato)
    if (bin) { cell[bin].reg += 1; cell[bin].unid += unid; }
    const ct = normalize(r.colapso_total) === 'si';
    const cp = normalize(r.colapso_parcial) === 'si';
    if (ct) { cell.colapso_total.reg += 1; cell.colapso_total.unid += unid; }
    if (cp) { cell.colapso_parcial.reg += 1; cell.colapso_parcial.unid += unid; }
    if (!ct && !cp) { cell.no_colapso.reg += 1; cell.no_colapso.unid += unid; }
  }
  return acc;
}

// Filas que no son Casa/Edificación se atenúan para no distraer la lectura.
const CH_MUTED = new Set(['sin_dato', 'erroneo']);

function renderColapsoHab(records) {
  const box = document.getElementById('colapso-hab-table');
  if (box) {
    const acc = colapsoHabCounts(records);
    const fmt = (n) => Math.round(n).toLocaleString('es-CO');
    const totals = Object.fromEntries(CH_METRICS.map(([m]) => [m, { reg: 0, unid: 0 }]));
    // Cada celda: registros (valor principal) + unidades habitacionales (secundario).
    const cells = (c) => CH_METRICS.map(([m]) =>
      `<td><span class="ce-reg">${fmt(c[m].reg)}</span><span class="ce-unid">${fmt(c[m].unid)} unid. hab.</span></td>`).join('');
    const bodyRows = TIPOLOGIAS.map(([t, label]) => {
      const c = acc[t];
      CH_METRICS.forEach(([m]) => { totals[m].reg += c[m].reg; totals[m].unid += c[m].unid; });
      const rowCls = CH_MUTED.has(t) ? ' class="ce-muted"' : '';
      return `<tr${rowCls}><th scope="row">${label}</th>${cells(c)}</tr>`;
    }).join('');
    const footRow = `<tr class="tip-total-row"><th scope="row">TOTAL</th>${cells(totals)}</tr>`;
    const head = CH_METRICS.map(([, label]) => `<th scope="col">${label}</th>`).join('');
    box.innerHTML = `<table class="tipologia-table ch-table">
      <thead><tr><th scope="col">Tipología (pisos sobre el terreno)</th>${head}</tr></thead>
      <tbody>${bodyRows}${footRow}</tbody>
    </table>
    <p class="chart-note">Valor principal = registros · valor secundario = unidades habitacionales (viviendas), un <strong>aproximado según los datos de la inspección</strong> (n_residenciales). Habitable = H · No habitable = R1/R2/I1/I2/I3. Casa = 3 pisos o menos y uso residencial · Edificación = más de 3 pisos, o cualquier uso no residencial. Un registro puede sumar en colapso y en habitabilidad a la vez.</p>`;
  }

  renderTipologiaBar('chart-hab-tipologia', records, ['habitable', 'no_habitable']);
  renderTipologiaBar('chart-colapso-tipologia', records, ['colapso_total', 'colapso_parcial', 'no_colapso']);
}

/** Barras agrupadas Casa vs. Edificación para un subconjunto de CH_METRICS. */
function renderTipologiaBar(canvasId, records, metricKeys) {
  const acc = colapsoHabCounts(records);
  const tips = [['casa', 'Casa'], ['edificacion', 'Edificación']];
  const surface = themeColor('--surface', '#12294a');
  const metrics = CH_BAR_METRICS.filter(([m]) => metricKeys.includes(m));
  upsertChart(canvasId, {
    type: 'bar',
    data: {
      labels: tips.map(([, label]) => label),
      datasets: metrics.map(([m, label, color]) => ({
        label,
        data: tips.map(([t]) => acc[t][m].reg),
        backgroundColor: color,
        borderColor: surface,
        borderWidth: 2,
        maxBarThickness: 64,
      })),
    },
    options: baseOptions({
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true } },
      plugins: { legend: { display: true } },
    }),
  });
}

/** Doughnut: edificaciones que requieren suspensión de servicios vs. el total
 *  visitado. suspension_servicios es un flag derivado (colapso parcial ×
 *  habitabilidad). Cifra central = requieren / total (%). */
function renderSuspension(records) {
  const total = records.length;
  const requieren = records.filter((r) => isYes(r.suspension_servicios)).length;
  const noReq = Math.max(0, total - requieren);
  const pct = total ? Math.round((requieren / total) * 1000) / 10 : 0;
  const centerText = {
    id: 'suspCenter',
    afterDraw(chart) {
      const { ctx, chartArea } = chart;
      const cx = (chartArea.left + chartArea.right) / 2;
      const cy = (chartArea.top + chartArea.bottom) / 2;
      ctx.save();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = themeColor('--text-primary', '#f4f7fb');
      ctx.font = '700 22px system-ui, -apple-system, sans-serif';
      ctx.fillText(`${requieren}/${total}`, cx, cy - 7);
      ctx.font = '600 12px system-ui, -apple-system, sans-serif';
      ctx.fillStyle = themeColor('--text-muted', '#7c8ca3');
      ctx.fillText(`${pct}% requiere`, cx, cy + 14);
      ctx.restore();
    },
  };
  upsertChart('chart-suspension', {
    type: 'doughnut',
    data: {
      labels: ['Requieren suspensión', 'No requieren'],
      datasets: [{
        data: [requieren, noReq],
        backgroundColor: [COLORS.status.i3, COLORS.status.h],
        borderColor: themeColor('--surface', '#12294a'),
        borderWidth: 2,
      }],
    },
    plugins: [centerText],
    options: baseOptions({
      cutout: '64%',
      plugins: {
        legend: { display: true },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.label}: ${ctx.parsed} (${total ? Math.round((ctx.parsed / total) * 1000) / 10 : 0}%)`,
          },
        },
      },
    }),
  }, { recreate: true }); // centerText closes over requieren/total/pct — must always rebuild.
}

let warnedMissingChart = false;

/** Render all "Estadísticas" charts from the current filtered record set.
 *  allRecords (sin filtrar) alimenta el reporte alcalde, que es un resumen fijo
 *  sobre el total y NO debe moverse con los filtros del tablero. */
export function renderStatistics(records, allRecords, reportados = null) {
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
  renderByEpoca(records);
  renderHabByComuna(records);
  renderTipologia(records);
  renderColapsoHab(records);
  renderHabDoughnut(records);
  renderSeveridad(records);
  renderSuspension(records);
  renderFlags(records);
  renderTimeSeries(records, reportados);
  injectChartHelp();
}
