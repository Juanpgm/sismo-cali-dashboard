// Self-check for the Panel KPI tiles: reconciliation text, "Sin dato" tile and
// the explicit "No habitable" definitions. Run: node web/js/kpi.test.mjs
import assert from 'node:assert/strict';
import { computeKpis, renderKpis } from './kpi.js';

const fakeContainer = () => ({ innerHTML: '', _helpWired: true, addEventListener() {} });
const b = (code, extra = {}) => ({ criterio_habitabilidad: code, n_ocupantes: 1, n_residenciales: 1, ...extra });

// --- computeKpis: H + R + I + Sin dato == buildings ----------------------------
{
  const edificios = [b('H'), b('R1'), b('R2'), b('I1'), b('I3'), b(null), b(''), b('   '), b('XX'), b('h')];
  const k = computeKpis(edificios);
  assert.equal(k.total, 10);
  assert.equal(k.hab_h, 2);
  assert.equal(k.hab_r, 2);
  assert.equal(k.hab_i, 2);
  assert.equal(k.hab_sin_dato, 4, 'blank, whitespace and malformed codes all land in Sin dato');
  assert.equal(k.hab_h + k.hab_r + k.hab_i + k.hab_sin_dato, k.total);
}
{
  const k = computeKpis([]);
  assert.equal(k.total, 0);
  assert.equal(k.hab_sin_dato, 0);
  const one = computeKpis([b(undefined)]);
  assert.equal(one.hab_sin_dato, 1);
}

// --- renderKpis: Total registros keeps the RAW number, adds building/reinspection text ---
{
  const edificios = [b('H'), b('R1'), b('I2'), b(null)];
  const c = fakeContainer();
  renderKpis(c, edificios, edificios, { recolectados: 7 });
  const html = c.innerHTML;
  assert.match(html, /Total registros[\s\S]*?<span class="kpi-value">7<\/span>/, 'the big number stays the raw submissions');
  assert.match(html, /4 edificios · 3 reinspecciones/, 'secondary text: buildings and re-inspections');
  assert.match(html, /Sin criterio de habitabilidad[\s\S]*?<span class="kpi-value">1<\/span>/, 'Sin dato tile');
  assert.match(html, /cuentan edificios/i, 'helper text states H/R/I count buildings');
}
{
  // Equal raw and buildings: still explicit, 0 reinspecciones (singular handled).
  const c = fakeContainer();
  renderKpis(c, [b('H')], [b('H')], { recolectados: 1 });
  assert.match(c.innerHTML, /1 edificio · 0 reinspecciones/);
}
{
  // Israel block is labeled, never merged silently.
  const c = fakeContainer();
  renderKpis(c, [b('H'), b('R1')], [b('H'), b('R1')], { recolectados: 2, israel: 2 });
  assert.match(c.innerHTML, /2 de Israel, sin agrupar/);
}
{
  // No raw figure available: falls back to the building count, no reconciliation text.
  const c = fakeContainer();
  renderKpis(c, [b('H')], [b('H')], {});
  assert.doesNotMatch(c.innerHTML, /reinspeccion/);
}
{
  // Empty dataset / filter matching zero buildings: renders without NaN/undefined.
  const c = fakeContainer();
  renderKpis(c, [], [b('H')], { recolectados: 0 });
  assert.doesNotMatch(c.innerHTML, /NaN|undefined|Infinity/);
  assert.match(c.innerHTML, /0 edificios · 0 reinspecciones/);
}

// --- the two "no habitable" definitions never share a label -----------------------
{
  const c = fakeContainer();
  renderKpis(c, [b('H'), b('R1'), b('I1')], [b('H'), b('R1'), b('I1')], { recolectados: 3 });
  assert.match(c.innerHTML, /No habitable \(I1 \+ I2 \+ I3\)/);
  assert.match(c.innerHTML, /Ocupantes en no habitables o restringidos \(R \+ I\)/);
  assert.doesNotMatch(c.innerHTML, /Ocupantes en no habitables</, 'the old ambiguous label is gone');
}

console.log('ok — kpi self-check');
