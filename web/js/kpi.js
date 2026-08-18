// KPI tile row — recomputed from the currently filtered record set.
// Habitability uses the granular criterio_habitabilidad scale (H · R1 · R2 ·
// I1 · I2 · I3): green for H, yellow shades for R, red shades for I.
import { COLORS, isNoHabitableBinary, habCode, labelForCode, normalize } from './utils.js';

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

// Granular habitability codes in severity order (drives tiles + distribution bar).
const HAB_CODES = ['h', 'r1', 'r2', 'i1', 'i2', 'i3'];

// Headline tiles: Unidades residenciales · Habitable (H) · Uso restringido
// (R1+R2) · No habitable (I1+I2+I3) · Colapso total · Colapso parcial.
// Human-cost figures (victims, occupants) and unit/structure counts are
// de-emphasized into a collapsed `secondary` group so the panel doesn't lead
// with the death/casualty numbers.
const TILE_DEFS = [
  { key: 'u_residenciales', label: 'Unidades residenciales', accent: null, group: 'headline' },
  { key: 'hab_h', label: 'Habitable (H)', accent: COLORS.status.h, group: 'headline' },
  { key: 'hab_r', label: 'Uso restringido (R1 + R2)', accent: COLORS.status.r2, group: 'headline' },
  { key: 'hab_i', label: 'No habitable (I1 + I2 + I3)', accent: COLORS.status.i2, group: 'headline' },
  { key: 'colapso_total', label: 'Colapso total', accent: COLORS.status.i3, group: 'headline' },
  { key: 'colapso_parcial', label: 'Colapso parcial', accent: COLORS.status.r2, group: 'headline' },
  // Human cost + counts — hidden by default, smaller, low visual weight.
  { key: 'muertos', label: 'Muertos', accent: COLORS.status.i2, group: 'secondary' },
  { key: 'heridos', label: 'Heridos', accent: COLORS.status.r2, group: 'secondary' },
  { key: 'ocupantes_riesgo', label: 'Ocupantes en no habitables', accent: COLORS.status.i1, group: 'secondary' },
  { key: 'ocupantes_total', label: 'Ocupantes totales', accent: null, group: 'secondary' },
  { key: 'u_comerciales', label: 'Unidades comerciales', accent: null, group: 'secondary' },
  { key: 'u_no_habitadas', label: 'Unidades no habitadas', accent: null, group: 'secondary' },
  { key: 'pisos_prom', label: 'Pisos (promedio)', accent: null, group: 'secondary' },
  { key: 'sotanos_total', label: 'Sótanos (total)', accent: null, group: 'secondary' },
];


/** Guarded percentage: never NaN/Infinity, always 0 when total is 0. */
function pct(part, total) {
  if (!total) return 0;
  return Math.round((part / total) * 1000) / 10; // 1 decimal
}

export function computeKpis(records) {
  const noHab = records.filter(isNoHabitableBinary);
  const habCounts = habDistribution(records);
  // Sum of residential units per habitability code (records + units are the
  // two differentiated readings shown on each habitability tile).
  const resByCode = Object.fromEntries(HAB_CODES.map((c) => [c, 0]));
  for (const r of records) {
    const c = habCode(r);
    if (resByCode[c] === undefined) continue;
    const v = Number(r.n_residenciales);
    if (!Number.isNaN(v)) resByCode[c] += v;
  }
  return {
    total: records.length,
    muertos: sumField(records, 'n_muertos'),
    heridos: sumField(records, 'n_heridos'),
    hab_h: habCounts.h,
    hab_r: habCounts.r1 + habCounts.r2,
    hab_i: habCounts.i1 + habCounts.i2 + habCounts.i3,
    hab_h_res: resByCode.h,
    hab_r_res: resByCode.r1 + resByCode.r2,
    hab_i_res: resByCode.i1 + resByCode.i2 + resByCode.i3,
    colapso_total: records.filter((r) => isYes(r.colapso_total)).length,
    colapso_parcial: records.filter((r) => isYes(r.colapso_parcial)).length,
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
  const counts = Object.fromEntries(HAB_CODES.map((c) => [c, 0]));
  for (const r of records) {
    const code = habCode(r);
    if (counts[code] !== undefined) counts[code]++;
  }
  return counts;
}

function subLine(html) {
  return html ? `<div class="kpi-sub-row">${html}</div>` : '';
}

/** @param {HTMLElement} container @param {object[]} filteredRecords @param {object[]} allRecords */
export function renderKpis(container, filteredRecords, allRecords) {
  const values = computeKpis(filteredRecords);
  const total = filteredRecords.length;

  const ocupantesTotalFiltrados = sumField(filteredRecords, 'n_ocupantes');

  const tileHtml = (def) => {
    let sub = '';
    if (def.key.startsWith('hab_')) {
      sub = subLine(
        `<span class="kpi-sub">${pct(values[def.key], total)}% del filtrado</span>`
        + `<span class="kpi-sub">${values[`${def.key}_res`]} unid. residenciales</span>`,
      );
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
  };

  const headlineHtml = TILE_DEFS.filter((d) => d.group === 'headline').map(tileHtml).join('');
  const secondaryHtml = TILE_DEFS.filter((d) => d.group === 'secondary').map(tileHtml).join('');

  const dist = habDistribution(filteredRecords);
  const segsHtml = HAB_CODES.map((code) => {
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

  // Victims / occupancy / counts stay collapsed by default (de-emphasized).
  const secondaryDetailsHtml = `
    <details class="kpi-secondary">
      <summary>Víctimas, ocupación y conteos detallados</summary>
      <div class="kpi-secondary-grid">${secondaryHtml}</div>
    </details>
  `;

  container.innerHTML = headlineHtml + habBarHtml + secondaryDetailsHtml;
}
