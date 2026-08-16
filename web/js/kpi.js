// KPI tile row — recomputed from the currently filtered record set.
import { COLORS, isNoHabitable, isRestringido, habCode, labelForCode, normalize } from './utils.js';

function sumField(records, field) {
  let total = 0;
  for (const r of records) {
    const v = Number(r[field]);
    if (!Number.isNaN(v)) total += v;
  }
  return total;
}

/** Mean over records with a numeric value, rounded to 1 decimal (0 if none). */
function avgField(records, field) {
  let total = 0;
  let n = 0;
  for (const r of records) {
    const v = Number(r[field]);
    if (!Number.isNaN(v)) { total += v; n += 1; }
  }
  return n ? Math.round((total / n) * 10) / 10 : 0;
}

function isYes(v) {
  return normalize(v) === 'si' || normalize(v) === 'sí';
}

const TILE_DEFS = [
  { key: 'total', label: 'Total inspecciones', accent: null },
  { key: 'muertos', label: 'Muertos', accent: COLORS.status.i2 },
  { key: 'heridos', label: 'Heridos', accent: COLORS.status.r2 },
  { key: 'no_habitables', label: 'No habitables', accent: COLORS.status.i2 },
  { key: 'restringido', label: 'Uso restringido', accent: COLORS.status.r1 },
  { key: 'habitables', label: 'Habitables', accent: COLORS.status.h },
  { key: 'colapsos', label: 'Colapsos (Total / Parcial)', accent: COLORS.status.i3 },
  { key: 'ocupantes_riesgo', label: 'Ocupantes en no habitables', accent: COLORS.status.i1 },
  // Numeric-variable aggregates.
  { key: 'ocupantes_total', label: 'Ocupantes totales', accent: null },
  { key: 'u_residenciales', label: 'Unidades residenciales', accent: null },
  { key: 'u_comerciales', label: 'Unidades comerciales', accent: null },
  { key: 'u_no_habitadas', label: 'Unidades no habitadas', accent: null },
  { key: 'pisos_prom', label: 'Pisos (promedio)', accent: null },
  { key: 'sotanos_total', label: 'Sótanos (total)', accent: null },
];

const HAB_ORDER = ['h', 'r1', 'r2', 'i1', 'i2', 'i3'];

/** Guarded percentage: never NaN/Infinity, always 0 when total is 0. */
function pct(part, total) {
  if (!total) return 0;
  return Math.round((part / total) * 1000) / 10; // 1 decimal
}

export function computeKpis(records) {
  const noHab = records.filter(isNoHabitable);
  const restringido = records.filter(isRestringido);
  const habitables = records.filter((r) => habCode(r) === 'h');
  const colapsoTotal = records.filter((r) => isYes(r.colapso_total)).length;
  const colapsoParcial = records.filter((r) => isYes(r.colapso_parcial)).length;
  return {
    total: records.length,
    muertos: sumField(records, 'n_muertos'),
    heridos: sumField(records, 'n_heridos'),
    no_habitables: noHab.length,
    restringido: restringido.length,
    habitables: habitables.length,
    colapsos: `${colapsoTotal} / ${colapsoParcial}`,
    ocupantes_riesgo: sumField(noHab, 'n_ocupantes'),
    ocupantes_total: sumField(records, 'n_ocupantes'),
    u_residenciales: sumField(records, 'n_residenciales'),
    u_comerciales: sumField(records, 'n_comerciales'),
    u_no_habitadas: sumField(records, 'n_no_habitadas'),
    pisos_prom: avgField(records, 'n_pisos'),
    sotanos_total: sumField(records, 'n_sotanos'),
  };
}

function habDistribution(records) {
  const counts = Object.fromEntries(HAB_ORDER.map((c) => [c, 0]));
  for (const r of records) {
    const code = habCode(r);
    if (HAB_ORDER.includes(code)) counts[code]++;
  }
  return counts;
}

function subLine(html) {
  return html ? `<div class="kpi-sub-row">${html}</div>` : '';
}

/** @param {HTMLElement} container @param {object[]} filteredRecords @param {object[]} allRecords */
export function renderKpis(container, filteredRecords, allRecords) {
  const values = computeKpis(filteredRecords);
  const baseline = computeKpis(allRecords);
  const total = filteredRecords.length;

  const noHabFilteredRate = pct(values.no_habitables, total);
  const noHabBaselineRate = pct(baseline.no_habitables, allRecords.length);
  const deltaPts = Math.round((noHabFilteredRate - noHabBaselineRate) * 10) / 10;
  const deltaArrow = deltaPts > 0 ? '▲' : deltaPts < 0 ? '▼' : '■';
  const deltaColor = deltaPts > 0 ? COLORS.status.i2 : deltaPts < 0 ? COLORS.status.h : COLORS.unknown;

  const ocupantesTotalFiltrados = sumField(filteredRecords, 'n_ocupantes');

  const tilesHtml = TILE_DEFS.map((def) => {
    let sub = '';
    if (def.key === 'no_habitables') {
      sub = subLine(
        `<span class="kpi-sub">${noHabFilteredRate}% del filtrado</span>`
        + `<span class="kpi-delta" style="color:${deltaColor}">${deltaArrow} ${Math.abs(deltaPts)} pts vs. total</span>`,
      );
    } else if (def.key === 'restringido') {
      sub = subLine(`<span class="kpi-sub">${pct(values.restringido, total)}% del filtrado</span>`);
    } else if (def.key === 'habitables') {
      sub = subLine(`<span class="kpi-sub">${pct(values.habitables, total)}% del filtrado</span>`);
    } else if (def.key === 'ocupantes_riesgo') {
      sub = subLine(`<span class="kpi-sub">${pct(values.ocupantes_riesgo, ocupantesTotalFiltrados)}% de ocupantes totales</span>`);
    }
    return `
      <div class="kpi-tile" style="${def.accent ? `--kpi-accent:${def.accent}` : ''}">
        <span class="kpi-label">${def.label}</span>
        <span class="kpi-value">${values[def.key]}</span>
        ${sub}
      </div>
    `;
  }).join('');

  const dist = habDistribution(filteredRecords);
  const segsHtml = HAB_ORDER.map((code) => {
    const count = dist[code];
    const share = total ? (count / total) * 100 : 0;
    if (share <= 0) return '';
    return `<div class="hab-bar-seg" style="width:${share}%;background:${COLORS.status[code]}" title="${labelForCode(code)}: ${count}"></div>`;
  }).join('');

  const habBarHtml = `
    <div class="kpi-tile kpi-tile-wide">
      <span class="kpi-label">Distribución de habitabilidad</span>
      <div class="hab-bar">${segsHtml}</div>
    </div>
  `;

  container.innerHTML = tilesHtml + habBarHtml;
}
