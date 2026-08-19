/* global Chart */
// Chart.js 4 (UMD global via CDN, same pattern as Leaflet's `L`) — "Estadísticas" charts.
import {
  COLORS, themeColor, labelForCode, labelForField, splitMultiValue, habCode, isNoHabitable, normalize, formatDate,
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
        backgroundColor: themeColor('--accent', '#FFC400'),
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

function renderTimeSeries(records) {
  const byDay = new Map();
  for (const r of records) {
    const d = (r.fecha_inspeccion || '').trim();
    if (!d) continue;
    byDay.set(d, (byDay.get(d) || 0) + 1);
  }
  const days = [...byDay.keys()].sort();
  // The source EDAN sheet ships every inspection date as "##########" (a stale
  // Sheets display artifact), so there is nothing to plot. Degrade to a clear
  // note rather than an empty axis until the date column is fixed at the source.
  if (!days.length) {
    setChartEmpty('chart-timeseries', 'Sin fechas de inspección en el origen: la columna llega vacía o como «##########».');
    return;
  }
  clearChartEmpty('chart-timeseries');
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
        backgroundColor: COLORS.status.i2,
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
  const counts = new Map();
  for (const r of records) {
    for (const part of splitMultiValue(r.uso_edificacion)) {
      counts.set(part, (counts.get(part) || 0) + 1);
    }
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const top = sorted.slice(0, COLORS.categorical.length);
  const otherCount = sorted.slice(COLORS.categorical.length).reduce((sum, [, v]) => sum + v, 0);
  const labels = top.map(([k]) => labelForCode(k));
  const data = top.map(([, v]) => v);
  const colors = top.map((_, i) => COLORS.categorical[i]);
  if (otherCount > 0) {
    labels.push('Otros');
    data.push(otherCount);
    colors.push(COLORS.categoricalOther);
  }
  upsertChart('chart-uso-doughnut', {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: colors,
        borderColor: themeColor('--surface', '#12294a'),
        borderWidth: 2,
      }],
    },
    options: baseOptions({ cutout: '55%', plugins: { legend: { display: true } } }),
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

function tipologiaDe(r) {
  const raw = r.n_pisos;
  if (raw == null || String(raw).trim() === '') return 'sin_dato';
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 1 || n > NPISOS_MAX) return 'erroneo';
  return n <= 3 ? 'casa' : 'edificacion';
}

/** Conteos edificaciones + suma de unidades residenciales por tipología×semáforo.
 *  Solo registros con semáforo conocido (H/R/I), así todas las filas cuadran. */
function tipologiaCounts(records) {
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
  const box = document.getElementById('tipologia-table');
  if (box) {
    const fmt = (n) => Math.round(n).toLocaleString('es-CO');
    const dot = (color) => `<span class="sem-dot" style="background:${color}"></span>`;
    const head = SEMAFORO_COLS.map(([, label, color]) => `<th>${dot(color)}${label} edif.</th>`).join('')
      + '<th>Total edif.</th>'
      + SEMAFORO_COLS.map(([, label, color]) => `<th>${dot(color)}${label} unid.</th>`).join('')
      + '<th>Total unid.</th>';
    const totals = { verde: { edif: 0, unid: 0 }, amarillo: { edif: 0, unid: 0 }, rojo: { edif: 0, unid: 0 } };
    const bodyRows = TIPOLOGIAS.map(([t, label]) => {
      const c = acc[t];
      let te = 0; let tu = 0;
      const cells = SEMAFORO_COLS.map(([k]) => {
        te += c[k].edif; tu += c[k].unid;
        totals[k].edif += c[k].edif; totals[k].unid += c[k].unid;
        return c[k];
      });
      return `<tr><th scope="row">${label}</th>`
        + cells.map((cell) => `<td>${fmt(cell.edif)}</td>`).join('')
        + `<td class="tip-total">${fmt(te)}</td>`
        + cells.map((cell) => `<td>${fmt(cell.unid)}</td>`).join('')
        + `<td class="tip-total">${fmt(tu)}</td></tr>`;
    }).join('');
    const te = totals.verde.edif + totals.amarillo.edif + totals.rojo.edif;
    const tu = totals.verde.unid + totals.amarillo.unid + totals.rojo.unid;
    const footRow = '<tr class="tip-total-row"><th scope="row">TOTAL</th>'
      + SEMAFORO_COLS.map(([k]) => `<td>${fmt(totals[k].edif)}</td>`).join('')
      + `<td class="tip-total">${fmt(te)}</td>`
      + SEMAFORO_COLS.map(([k]) => `<td>${fmt(totals[k].unid)}</td>`).join('')
      + `<td class="tip-total">${fmt(tu)}</td></tr>`;
    box.innerHTML = `<table class="tipologia-table">
      <thead><tr><th scope="col">Tipología (pisos sobre el terreno)</th>${head}</tr></thead>
      <tbody>${bodyRows}${footRow}</tbody>
    </table>
    <p class="chart-note">Semáforo: verde = H · amarillo = R1+R2 · rojo = I1+I2+I3. Solo registros con habitabilidad conocida.</p>`;
  }

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

// ── Colapso total y evacuación por tipología (Casa/Edificio) ────────────────
// Reusa tipologiaDe() (n_pisos <= 3 casa, > 3 edificación). Tres métricas por
// tipología, cada una en registros y unidades habitacionales (n_residenciales):
//   · Colapso total       = colapso_total == 'sí'
//   · Evacuar (no habit.) = no habitable (I1/I2/I3)
//   · Evacuar (parcial)   = colapso_parcial == 'sí' Y no habitable, sin colapso total
const CE_METRICS = ['colapso', 'evac', 'evacParcial'];
function colapsoEvacCounts(records) {
  const acc = Object.fromEntries(TIPOLOGIAS.map(([t]) => [
    t, Object.fromEntries(CE_METRICS.map((m) => [m, { reg: 0, unid: 0 }])),
  ]));
  for (const r of records) {
    const cell = acc[tipologiaDe(r)];
    const u = Number(r.n_residenciales);
    const unid = Number.isNaN(u) ? 0 : u;
    const total = normalize(r.colapso_total) === 'si';
    const parcial = normalize(r.colapso_parcial) === 'si';
    const noHab = isNoHabitable(r);
    if (total) { cell.colapso.reg += 1; cell.colapso.unid += unid; }
    if (noHab) { cell.evac.reg += 1; cell.evac.unid += unid; }
    if (parcial && noHab && !total) { cell.evacParcial.reg += 1; cell.evacParcial.unid += unid; }
  }
  return acc;
}

function renderColapsoEvac(records) {
  const box = document.getElementById('colapso-evac-table');
  if (!box) return;
  const acc = colapsoEvacCounts(records);
  const fmt = (n) => Math.round(n).toLocaleString('es-CO');
  const totals = Object.fromEntries(CE_METRICS.map((m) => [m, { reg: 0, unid: 0 }]));
  const cells = (c) => CE_METRICS.map((m) => `<td>${fmt(c[m].reg)}</td><td>${fmt(c[m].unid)}</td>`).join('');
  const bodyRows = TIPOLOGIAS.map(([t, label]) => {
    const c = acc[t];
    CE_METRICS.forEach((m) => { totals[m].reg += c[m].reg; totals[m].unid += c[m].unid; });
    return `<tr><th scope="row">${label}</th>${cells(c)}</tr>`;
  }).join('');
  const footRow = `<tr class="tip-total-row"><th scope="row">TOTAL</th>${cells(totals)}</tr>`;
  box.innerHTML = `<table class="tipologia-table">
    <thead>
      <tr><th rowspan="2" scope="col">Tipología (pisos sobre el terreno)</th>
          <th colspan="2" scope="colgroup">Colapso total</th>
          <th colspan="2" scope="colgroup">Evacuar · no habitable</th>
          <th colspan="2" scope="colgroup">Evacuar · colapso parcial + no habitable</th></tr>
      <tr><th scope="col">Reg.</th><th scope="col">Unid. hab.</th>
          <th scope="col">Reg.</th><th scope="col">Unid. hab.</th>
          <th scope="col">Reg.</th><th scope="col">Unid. hab.</th></tr>
    </thead>
    <tbody>${bodyRows}${footRow}</tbody>
  </table>
  <p class="chart-note">Casa = 3 pisos o menos · Edificación = más de 3 pisos. Evacuar · no habitable = I1+I2+I3. Evacuar · colapso parcial + no habitable = colapso parcial Y no habitable, excluyendo colapso total. Unid. hab. = unidades residenciales.</p>`;
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
  renderByEpoca(records);
  renderHabByComuna(records);
  renderTipologia(records);
  renderColapsoEvac(records);
  renderHabDoughnut(records);
  renderSeveridad(records);
  renderUsoDoughnut(records);
  renderFlags(records);
  renderTimeSeries(records);
}
