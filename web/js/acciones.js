// Acciones view: demolition triage over partial-collapse records.
// Rule: colapso_parcial = si, then count point-4 external risks (4.1a–4.4a).
// All 4 = "Demoler"; 2–3 ("varios") = "Verificar"; fewer are excluded.
import { escapeHtml, sourceLabel, barrioVeredaDisplay } from './utils.js';

const P4_FIELDS = ['41_a', '42_a', '43_a', '44_a'];
const P4_LABELS = ['4.1', '4.2', '4.3', '4.4'];

const isSi = (v) => String(v ?? '').trim().toLowerCase() === 'si';

export function classifyAccion(record) {
  if (!isSi(record.colapso_parcial)) return null;
  const count = P4_FIELDS.filter((f) => isSi(record[f])).length;
  if (count === 4) return 'Demoler';
  if (count >= 2) return 'Verificar';
  return null;
}

export function renderAcciones(root, records, { onRowClick } = {}) {
  const rows = records
    .map((r) => ({ record: r, accion: classifyAccion(r) }))
    .filter((r) => r.accion)
    .sort((a, b) => (a.accion === b.accion ? 0 : a.accion === 'Demoler' ? -1 : 1));

  const demoler = rows.filter((r) => r.accion === 'Demoler').length;
  const verificar = rows.length - demoler;

  root.innerHTML = `
    <section class="card asig-summary-card">
      <p class="accion-note">
        Edificaciones con <strong>colapso parcial</strong> clasificadas por riesgos externos
        del punto 4 (4.1–4.4): los 4 en «Si» → <strong>Demoler</strong>; 2 o 3 → <strong>Verificar</strong>.
      </p>
      <div class="asig-summary">
        <div class="asig-stat"><span class="asig-stat-value">${demoler}</span><span class="asig-stat-label">Demoler</span></div>
        <div class="asig-stat"><span class="asig-stat-value">${verificar}</span><span class="asig-stat-label">Verificar</span></div>
        <div class="asig-stat"><span class="asig-stat-value">${rows.length}</span><span class="asig-stat-label">Total</span></div>
      </div>
    </section>
    <section class="card">
      <div class="table-scroll">
        <table id="acciones-table">
          <thead><tr>
            <th>Acción</th><th>Dirección</th><th>Barrio / vereda</th><th>Comuna / corregimiento</th>
            <th>${escapeHtml(sourceLabel('criterio_habitabilidad', 'Habitabilidad'))}</th>
            ${P4_LABELS.map((l) => `<th>${l}</th>`).join('')}
          </tr></thead>
          <tbody>
            ${rows.map(({ record: r, accion }, i) => `
              <tr data-row="${i}">
                <td><span class="accion-badge accion-badge-${accion.toLowerCase()}">${accion}</span></td>
                <td>${escapeHtml(r.direccion ?? '—')}</td>
                <td>${escapeHtml(barrioVeredaDisplay(r))}</td>
                <td>${escapeHtml(r.comuna ?? '—')}</td>
                <td>${escapeHtml((r.criterio_habitabilidad ?? '—').toUpperCase())}</td>
                ${P4_FIELDS.map((f) => `<td>${isSi(r[f]) ? 'Si' : 'No'}</td>`).join('')}
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </section>`;

  if (onRowClick) {
    root.querySelectorAll('tbody tr').forEach((tr) => {
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', () => onRowClick(rows[Number(tr.dataset.row)].record));
    });
  }
}
