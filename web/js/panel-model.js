// Pure model behind the Panel's reconciliation (no DOM, no Firebase, so it can
// be loaded by `node` self-checks; data.js cannot).
//
// Vocabulary used across the Panel:
//   inspección / registro = one submission (raw, ungrouped)
//   edificio              = one physical building; may have several inspections
//   reinspección          = inspections beyond the first of each building
//                           (registros - edificios)
import { habCode, colapsoResuelto, normalize } from './utils.js';

/** Stable building identity. Survey records carry `dup_grupo_id` (assigned by
 *  the pipeline, scripts/refresh_data.py add_dup_group). Anything without one
 *  — legacy data, and every Israel (Firestore) record — is its own building:
 *  we never merge on a guess. `idx` is the last-resort fallback for records
 *  with no id at all so they can never collapse into each other. */
export function buildingKey(r, idx = 0) {
  const g = r && r.dup_grupo_id;
  if (g !== null && g !== undefined && String(g).trim() !== '') return `g:${String(g).trim()}`;
  const id = r && (r.GlobalID || r.ObjectID);
  if (id !== null && id !== undefined && String(id).trim() !== '') return `r:${r.fuente || ''}:${String(id).trim()}`;
  return `i:${idx}`;
}

/** Filter AFTER grouping.
 *
 *  A building passes the filter when ANY of its inspections satisfies
 *  `predicate` (a building re-inspected on another date, or later reclassified,
 *  is still that building), and it is counted exactly once.
 *
 *  Returns:
 *   - `inspecciones`: the inspections that individually match (table, raw total,
 *     xlsx export, time series — the ungrouped view);
 *   - `edificios`: one record per passing building, in source order. The record
 *     that REPRESENTS the building is the pipeline's `es_representante` (most
 *     critical assessment), even when that record is not the one that matched:
 *     the building's attributes (habitability, collapse, units, location) are
 *     always those of its representative, so the same building never changes
 *     category depending on which filter let it in. If a group has no flagged
 *     representative in the loaded set, its first matching inspection stands in. */
export function applyBuildingFilter(records, predicate) {
  if (!Array.isArray(records) || records.length === 0) return { inspecciones: [], edificios: [] };
  const keys = records.map((r, i) => buildingKey(r, i));
  const inspecciones = [];
  const passing = new Map(); // key -> first matching inspection (fallback)
  records.forEach((r, i) => {
    if (!predicate(r)) return;
    inspecciones.push(r);
    if (!passing.has(keys[i])) passing.set(keys[i], r);
  });
  const edificios = [];
  const placed = new Set();
  records.forEach((r, i) => {
    const k = keys[i];
    if (!passing.has(k) || placed.has(k) || r.es_representante === false) return;
    placed.add(k);
    edificios.push(r);
  });
  // Groups whose representative is absent from the loaded set.
  for (const [k, first] of passing) if (!placed.has(k)) edificios.push(first);
  return { inspecciones, edificios };
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})/;

/** Inclusive date-range test on `fecha_inspeccion`. With no active bound
 *  everything passes (even undated records). With a bound, a blank or
 *  malformed date fails: it cannot be compared. Only the ISO date part
 *  (YYYY-MM-DD) is used, so datetimes work and impossible dates are rejected. */
export function inDateRange(value, from, to) {
  if (!from && !to) return true;
  if (value === null || value === undefined) return false;
  const m = ISO_DATE.exec(String(value).trim());
  if (!m) return false;
  const [mo, d] = [Number(m[2]), Number(m[3])];
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return false;
  const day = `${m[1]}-${m[2]}-${m[3]}`;
  if (from && day < from) return false;
  if (to && day > to) return false;
  return true;
}

/** Counts by source. Israel (Firestore) has no address and no dup group, and
 *  proximity alone is not reliable enough to merge it into Survey123
 *  buildings, so its records are reported as their own labeled block. */
export function countByFuente(records) {
  const out = { cali: 0, israel: 0 };
  if (!Array.isArray(records)) return out;
  for (const r of records) out[r && r.fuente === 'israel' ? 'israel' : 'cali'] += 1;
  return out;
}

const H_CODES = new Set(['h']);
const R_CODES = new Set(['r1', 'r2']);
const I_CODES = new Set(['i1', 'i2', 'i3']);

/** Reconciliation of the Panel figures.
 *  Invariants: h + r + i + sinDato === edificios ; registros - edificios === reinspecciones.
 *  Anything that is not a valid H/R1/R2/I1/I2/I3 code (blank, whitespace,
 *  malformed) is "Sin dato", so no building is ever dropped from the sum. */
export function reconcile(registros, edificios) {
  const list = Array.isArray(edificios) ? edificios : [];
  const out = { registros: Number.isFinite(registros) ? registros : 0, edificios: list.length, h: 0, r: 0, i: 0, sinDato: 0 };
  for (const b of list) {
    const c = habCode(b);
    if (H_CODES.has(c)) out.h += 1;
    else if (R_CODES.has(c)) out.r += 1;
    else if (I_CODES.has(c)) out.i += 1;
    else out.sinDato += 1;
  }
  return {
    registros: out.registros,
    edificios: out.edificios,
    reinspecciones: Math.max(0, out.registros - out.edificios),
    h: out.h, r: out.r, i: out.i, sinDato: out.sinDato,
  };
}

/** "2090 inspecciones (1731 edificios)" — the table's row counter. */
export function inspeccionesEdificiosText(inspecciones, edificios) {
  const n = Number.isFinite(inspecciones) ? inspecciones : 0;
  const e = Number.isFinite(edificios) ? edificios : 0;
  return `${n} ${n === 1 ? 'inspección' : 'inspecciones'} (${e} ${e === 1 ? 'edificio' : 'edificios'})`;
}

/** Count of "Sí" for a Riesgos-chart flag. colapso_total / colapso_parcial use
 *  the depurated value (colapso_resuelto: both-si resolves to parcial), the
 *  same rule the KPI cards use, so card and chart cannot disagree. */
export function flagYesCount(records, field) {
  if (!Array.isArray(records)) return 0;
  const resolved = field === 'colapso_total' ? 'total' : field === 'colapso_parcial' ? 'parcial' : null;
  let n = 0;
  for (const r of records) {
    if (resolved) {
      if ((r.colapso_resuelto || colapsoResuelto(r)) === resolved) n += 1;
    } else if (normalize(r[field]) === 'si') n += 1;
  }
  return n;
}
