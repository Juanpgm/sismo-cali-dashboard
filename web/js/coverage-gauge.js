// Reusable semicircle "sticker coverage" gauge, shared by the Panel view and
// the Stickers→Evaluaciones section. Same geometry and CSS classes as the
// Asignación gauge (stickers-asignacion.js:renderGauge) so it reads identically,
// but two segments only: blue = Panel points WITH a sticker, red = without.
//
// Data comes from /api/sticker-status (the cruce_sticker.py result), so both
// places show the same Panel-wide coverage.

import { COLORS } from './utils.js';

const CON = COLORS.categorical[0]; // azul = con sticker (igual que el mapa)
const SIN = COLORS.status.i2;      // rojo = sin sticker

// Returns the gauge as an HTML string. `total` = matched Panel points, `con` =
// those that already have a sticker. Returns '' when there is nothing to show.
export function coverageGaugeHtml({ total, con } = {}) {
  if (!Number.isFinite(total) || total <= 0) return '';
  const conN = Math.max(0, Math.min(Number(con) || 0, total));
  const sinN = total - conN;
  const pct = Math.round((conN / total) * 100);

  const cx = 110;
  const cy = 104;
  const r = 84;
  const sw = 16;
  const pointAt = (f) => {
    const a = Math.PI * (1 - f); // f=0 → izquierda (π), f=1 → derecha (0), sobre el arco superior
    return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
  };
  const seg = (f0, f1, color) => {
    if (f1 - f0 <= 0.0001) return '';
    const [x0, y0] = pointAt(f0);
    const [x1, y1] = pointAt(f1);
    return `<path d="M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" fill="none" stroke="${color}" stroke-width="${sw}"/>`;
  };
  const [tx0, ty0] = pointAt(0);
  const [tx1, ty1] = pointAt(1);
  const track = `<path d="M ${tx0.toFixed(1)} ${ty0.toFixed(1)} A ${r} ${r} 0 0 1 ${tx1.toFixed(1)} ${ty1.toFixed(1)}" fill="none" stroke="var(--surface-3)" stroke-width="${sw}" stroke-linecap="round"/>`;
  const fCon = conN / total;
  const arcs = seg(0, fCon, CON) + seg(fCon, 1, SIN);

  return `
    <svg class="asignacion-gauge-svg" viewBox="0 0 220 128" role="img" aria-label="Cobertura de stickers: ${pct}%">
      ${track}${arcs}
      <text x="${cx}" y="${cy - 12}" class="asignacion-gauge-pct" text-anchor="middle">${pct}%</text>
      <text x="${cx}" y="${cy + 8}" class="asignacion-gauge-cap" text-anchor="middle">${conN} de ${total} con sticker</text>
    </svg>
    <div class="asignacion-gauge-legend">
      <span class="asignacion-gauge-item"><span class="legend-swatch legend-circle" style="background:${CON}"></span>Con sticker ${conN}</span>
      <span class="asignacion-gauge-item"><span class="legend-swatch legend-circle" style="background:${SIN}"></span>Sin sticker ${sinN}</span>
    </div>`;
}
