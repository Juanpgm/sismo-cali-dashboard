// KPI tile row — recomputed from the currently filtered record set.
// Habitability uses the granular criterio_habitabilidad scale (H · R1 · R2 ·
// I1 · I2 · I3): green for H, yellow shades for R, red shades for I.
import { COLORS, isNoHabitableBinary, habCode, labelForCode, normalize, splitMultiValue, escapeHtml } from './utils.js';

function sumField(records, field) {
  let total = 0;
  for (const r of records) {
    const v = Number(r[field]);
    if (!Number.isNaN(v)) total += v;
  }
  return total;
}

/** Mean over records with a numeric value, rounded to 1 decimal (0 if none).
 *  `max` descarta outliers de captura (p.ej. n_pisos=91980) que reventaban el
 *  promedio; mismo criterio que NPISOS_MAX en charts.js. */
function avgField(records, field, max = Infinity) {
  let total = 0;
  let n = 0;
  for (const r of records) {
    const v = Number(r[field]);
    if (!Number.isNaN(v) && v >= 0 && v <= max) { total += v; n += 1; }
  }
  return n ? Math.round((total / n) * 10) / 10 : 0;
}

function isYes(v) {
  return normalize(v) === 'si' || normalize(v) === 'sí';
}

// Granular habitability codes in severity order (drives tiles + distribution bar).
const HAB_CODES = ['h', 'r1', 'r2', 'i1', 'i2', 'i3'];

// Headline tiles split into two labeled sections: counts by record and sums
// by residential unit (n_residenciales). Human-cost figures (victims,
// occupants) and unit/structure counts are de-emphasized into a collapsed
// `secondary` group so the panel doesn't lead with the death/casualty numbers.
const TILE_DEFS = [
  { key: 'total', label: 'Total registros', accent: null, group: 'headline', section: 'registros' },
  { key: 'hab_h', label: 'Habitable (H)', accent: COLORS.status.h, group: 'headline', section: 'registros' },
  { key: 'hab_r', label: 'Uso restringido (R1 + R2)', accent: COLORS.status.r2, group: 'headline', section: 'registros' },
  { key: 'hab_i', label: 'No habitable (I1 + I2 + I3)', accent: COLORS.status.i2, group: 'headline', section: 'registros' },
  { key: 'colapso_total', label: 'Colapso total', accent: COLORS.status.i3, group: 'headline', section: 'registros' },
  { key: 'colapso_parcial', label: 'Colapso parcial', accent: COLORS.status.r2, group: 'headline', section: 'registros' },
  { key: 'u_residenciales', label: 'Total unidades habitacionales', accent: null, group: 'headline', section: 'residenciales' },
  { key: 'hab_h_res', label: 'Habitable (H)', accent: COLORS.status.h, group: 'headline', section: 'residenciales' },
  { key: 'hab_r_res', label: 'Uso restringido (R1 + R2)', accent: COLORS.status.r2, group: 'headline', section: 'residenciales' },
  { key: 'hab_i_res', label: 'No habitable (I1 + I2 + I3)', accent: COLORS.status.i2, group: 'headline', section: 'residenciales' },
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
    pisos_prom: avgField(records, 'n_pisos', 60),
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

/** Cards for the "Por uso de la edificación" section, sorted by count.
 *  uso_edificacion is multi-value: a record counts in every use it lists,
 *  so these cards can sum to more than the filtered total. */
function usoTilesHtml(records, total) {
  const counts = new Map();
  const resSums = new Map();
  for (const r of records) {
    const nRes = Number(r.n_residenciales);
    for (const part of splitMultiValue(r.uso_edificacion)) {
      counts.set(part, (counts.get(part) || 0) + 1);
      if (!Number.isNaN(nRes)) resSums.set(part, (resSums.get(part) || 0) + nRes);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([uso, count]) => `
    <div class="kpi-tile">
      <span class="kpi-label">${escapeHtml(labelForCode(uso))}</span>
      <span class="kpi-value">${count}</span>
      <div class="kpi-sub-row">
        <span class="kpi-sub">${pct(count, total)}% del filtrado</span>
        <span class="kpi-sub">${Math.round(resSums.get(uso) || 0)} unid. habitacionales</span>
      </div>
    </div>
  `).join('');
}

/** @param {HTMLElement} container @param {object[]} filteredRecords @param {object[]} allRecords
 *  @param {number|null} reportados Total "Reportado" de la API de reportes (global, no filtrable) */
export function renderKpis(container, filteredRecords, allRecords, reportados = null) {
  const values = computeKpis(filteredRecords);
  const total = filteredRecords.length;

  // Encabeza la sección: total de reportes ciudadanos "Reportado" (dataset de
  // reportes, independiente de los registros EDAN-F3 y de los filtros).
  const reportadosTile = reportados == null ? '' : `
    <div class="kpi-tile">
      <span class="kpi-label">Reportados</span>
      <span class="kpi-value">${reportados}</span>
      <div class="kpi-sub-row"><span class="kpi-sub">reportes ciudadanos en el sistema</span></div>
    </div>
  `;

  const ocupantesTotalFiltrados = sumField(filteredRecords, 'n_ocupantes');

  const tileHtml = (def) => {
    let sub = '';
    if (def.key.startsWith('hab_') && def.key.endsWith('_res')) {
      sub = subLine(`<span class="kpi-sub">${pct(values[def.key], values.u_residenciales)}% de unid. habitacionales</span>`);
    } else if (def.key.startsWith('hab_')) {
      sub = subLine(`<span class="kpi-sub">${pct(values[def.key], total)}% del filtrado</span>`);
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

  const kpiHelpBtn = (text) => (text
    ? `<button type="button" class="tile-help-btn" aria-label="Cómo interpretar">i</button><div class="tile-help-body kpi-help-body" hidden>${text}</div>`
    : '');
  const sectionTitle = (t, help) => `<h3 class="kpi-section-title">${t}${kpiHelpBtn(help)}</h3>`;
  const tilesFor = (section) => TILE_DEFS
    .filter((d) => d.group === 'headline' && d.section === section)
    .map(tileHtml).join('');
  const headlineHtml = sectionTitle('Por número de registros', 'Cada tarjeta cuenta INSPECCIONES (un registro por edificación evaluada). "Reportados" es el universo de reportes ciudadanos (Momento 2), independiente de las inspecciones. Todo se recalcula con los filtros.')
    + reportadosTile
    + tilesFor('registros')
    + sectionTitle('Por unidades habitacionales (viviendas)', 'Aquí se suman VIVIENDAS (n_residenciales), un aproximado según cada inspección — no inspecciones. Sirve para dimensionar cuántos hogares hay detrás de cada estado de habitabilidad.')
    + tilesFor('residenciales')
    + sectionTitle('Por uso de la edificación', 'Cuántas inspecciones por uso (residencial, comercial, etc.). Un registro con varios usos cuenta en cada uno, así que pueden sumar más que el total.')
    + usoTilesHtml(filteredRecords, total);
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
      <p class="chart-note">Muertos y heridos son la SUMA de lo declarado en cada inspección (n_muertos, n_heridos); es una cifra estimada por los evaluadores y se recalcula con los filtros. Ocupantes y unidades siguen el mismo criterio.</p>
      <div class="kpi-secondary-grid">${secondaryHtml}</div>
    </details>
  `;

  container.innerHTML = headlineHtml + habBarHtml + secondaryDetailsHtml;

  // Toggle de los ⓘ (delegado, una sola vez): muestra/oculta el popover de ayuda.
  if (!container._helpWired) {
    container.addEventListener('click', (e) => {
      const btn = e.target.closest('.tile-help-btn');
      if (!btn) return;
      const body = btn.parentElement.querySelector('.tile-help-body');
      if (body) body.hidden = !body.hidden;
    });
    container._helpWired = true;
  }
}
