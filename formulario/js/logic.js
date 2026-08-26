// Pure form logic, DOM- and Firebase-free so it is testable in Node.

export const MUNICIPIO = '76001'; // DIVIPOLA code for Cali

// Inspectors sign in with their cédula only; Firebase Auth needs an email, so
// we synthesize one. A value that already contains "@" is used verbatim (lets a
// real email still work and avoids a double-@).
export const LOGIN_EMAIL_DOMAIN = 'sismocali.gov.co';
export function cedulaToEmail(raw) {
  const v = String(raw == null ? '' : raw).trim().toLowerCase();
  if (!v) return '';
  return v.includes('@') ? v : `${v}@${LOGIN_EMAIL_DOMAIN}`;
}

// ATC-20: the worst observed condition governs the suggested classification.
export function sugerirClasificacion(criterios) {
  const vals = Object.values(criterios);
  if (vals.includes('severo')) return 'INSEGURO';
  if (vals.includes('moderado')) return 'USO_RESTRINGIDO';
  return 'INSPECCIONADA';
}

// e.g. buildCodigo('1', '004', 1) -> '76001-1-0040001'
export function buildCodigo(area, codigoInspector, consecutivo) {
  return `${MUNICIPIO}-${area}-${codigoInspector}${String(consecutivo).padStart(4, '0')}`;
}

// Inverse of buildCodigo's third segment: strips the fixed 3-digit inspector
// code and parses the remainder as the consecutive. Width-agnostic so codes
// that already widened past 9999 (see the ceiling test above) still parse:
// '76001-1-00410000' with codigoInspector '004' -> 10000. Returns null when
// the code does not belong to this inspector (wrong area segment, wrong
// inspector prefix, or malformed input).
export function parseConsecutivo(codigo, codigoInspector) {
  if (typeof codigo !== 'string' || !codigoInspector) return null;
  const parts = codigo.split('-');
  if (parts.length !== 3) return null;
  const [, , tail] = parts;
  if (!tail.startsWith(codigoInspector)) return null;
  const rest = tail.slice(codigoInspector.length);
  if (!/^\d+$/.test(rest)) return null;
  return Number(rest);
}

// Max-based (not count-based) so gaps in the sequence never collide with an
// existing code: {1, 3} -> 4, not 3. Codes that do not belong to this
// inspector (parseConsecutivo -> null) are ignored.
export function siguienteConsecutivo(codigos, codigoInspector) {
  let max = 0;
  for (const codigo of codigos) {
    const n = parseConsecutivo(codigo, codigoInspector);
    if (n != null && n > max) max = n;
  }
  return max + 1;
}

// Pure "next from a known max" arithmetic — the session-cache counterpart to
// siguienteConsecutivo, for when the caller already holds the max (no codes
// array to re-scan). Same max-based semantics: null/undefined means "no
// known max yet" and is treated as 0.
export function siguienteDesdeMax(maxConocido) {
  return (maxConocido == null ? 0 : maxConocido) + 1;
}

// Folds a consecutive that was just actually saved into the session's known
// max, via max() — never a blind assignment. This is what lets the session
// cache learn from a manually-edited code (the saved value can be higher OR
// lower than what was derived before the edit) without a gap-filling
// correction (saving a value BELOW the current max) ever dragging the known
// max backwards.
export function plegarConsecutivoGuardado(maxConocido, consecutivoGuardado) {
  const max = maxConocido == null ? 0 : maxConocido;
  return Math.max(max, consecutivoGuardado);
}

// Consecutivos already saved by THIS inspector, extracted from record ids.
// Backs the duplicate guard on the editable segment: an edited value that is
// already in this set can never be submitted (it would collide at the
// create-only transaction anyway — this catches it at edit time instead of
// after photo uploads). Codes of other inspectors parse to null and drop out.
export function consecutivosExistentes(codigos, codigoInspector) {
  const set = new Set();
  for (const codigo of codigos) {
    const n = parseConsecutivo(codigo, codigoInspector);
    if (n != null) set.add(n);
  }
  return set;
}

// Validates the editable 4-digit consecutive segment the inspector types.
// Deliberately no "floor at next available" rule: a value below the derived
// next is a legitimate gap-filling correction, not an error (caller shows a
// non-blocking hint instead). '0000' is rejected — zero is not a valid
// consecutive.
export function validarSegmento(raw) {
  const v = String(raw == null ? '' : raw).trim();
  if (!v) return { ok: false, value: null, code: 'vacio' };
  if (v.length !== 4) return { ok: false, value: null, code: 'longitud' };
  if (!/^\d{4}$/.test(v)) return { ok: false, value: null, code: 'no-numerico' };
  const n = Number(v);
  if (n === 0) return { ok: false, value: null, code: 'cero' };
  return { ok: true, value: n, code: null };
}

// ---- Photos -----------------------------------------------------------------

// Slot-generic cap (design decision "Slot-Generic Design With Capped
// Fallback"): the client never hardcodes below-10 UI logic — only this
// constant changes if the external signer's accepted slot range changes.
// Set to 10 after a signer re-probe confirmed slot 1..10 pass schema
// validation while slot 11 is rejected with `400 bad-request` (see
// formulario/SETUP.md section 7 for the raw probe evidence).
export const MAX_FOTOS = 10;

// Pure predicate for the dynamic add-tile: true while there is room for one
// more photo. `current` is the number of photos already attached.
export function canAddSlot(current, max = MAX_FOTOS) {
  return current < max;
}

// ---- Assigned points (sticker_matches) ---------------------------------------

// Assigned points still to visit: everything except those already completed.
// The dashboard/cruce marks a point 'hecho' once its evaluación lands, so those
// drop off the inspector's list. Firestore `!=` needs a composite index, so the
// estado filter is done here in code over the inspector's own docs.
export function filtrarPendientes(asignaciones) {
  return (asignaciones || []).filter((a) => a && a.estado_asignacion !== 'hecho');
}

// Habitability criterion → severity color token, reusing the form's palette.
// h = habitable (green), r1/r2 = restricted use (amber), i1/i2/i3 = unsafe
// (red). Unknown/empty → muted grey. Value is a CSS custom-property reference
// so the caller can drop it straight into an inline style.
export function habitabilidadColor(criterio) {
  const c = String(criterio == null ? '' : criterio).trim().toLowerCase();
  if (c === 'h') return 'var(--verde)';
  if (c === 'r1' || c === 'r2') return 'var(--ambar)';
  if (c === 'i1' || c === 'i2' || c === 'i3') return 'var(--rojo)';
  return 'var(--muted)';
}

// Colapso pill label: total/parcial get a (red) pill; anything else — 'no',
// empty, unknown — means no pill (return '').
export function colapsoLabel(colapso) {
  const c = String(colapso == null ? '' : colapso).trim().toLowerCase();
  if (c === 'total') return 'Total';
  if (c === 'parcial') return 'Parcial';
  return '';
}

// Planeación priority → color token, same palette habitabilidadColor uses
// (alta = red, media = amber, baja/unknown = muted) so a planeación card's
// left border reads consistently with a sticker card's own severity color.
export function prioridadColor(prioridad) {
  const p = String(prioridad == null ? '' : prioridad).trim().toLowerCase();
  if (p === 'alta') return 'var(--rojo)';
  if (p === 'media') return 'var(--ambar)';
  return 'var(--muted)';
}

// Google Maps directions deep link to a point's coordinates. sticker_matches
// coords are { lat, lon } (note: lon, not lng). Returns '' when coords are
// missing so the caller can hide the "Cómo llegar" button.
export function mapsDirUrl(coords) {
  if (!coords || coords.lat == null || coords.lon == null) return '';
  return `https://www.google.com/maps/dir/?api=1&destination=${coords.lat},${coords.lon}`;
}

// Which prefilled Survey123 link a "planeación" (EDAN) card's "Abrir
// encuesta" button should open. `punto` carries `survey_web`/`survey_app`
// as returned by misPuntosPlaneacion (app is null when
// SURVEY123_FIELD_APP_ITEM_ID is not configured). On a mobile device the
// field-app deep link is preferred when present — it opens Survey123's own
// installed app rather than a mobile browser tab — falling back to the web
// URL otherwise. Returns '' when neither link is available (missing
// SURVEY123_FORM_URL), so the caller can disable/hide the button instead of
// wiring up a dead link.
export function elegirEnlaceEncuesta(punto, isMobile) {
  if (!punto) return '';
  if (isMobile && punto.survey_app) return punto.survey_app;
  return punto.survey_web || punto.survey_app || '';
}

// ---- Session resilience ------------------------------------------------------

const TRANSIENT_FIRESTORE_CODES = new Set(['unavailable', 'deadline-exceeded', 'network-request-failed']);
const FATAL_FIRESTORE_CODES = new Set(['permission-denied', 'not-found']);

// Classifies a Firebase/Firestore error by its `.code`. Fatal errors are
// authoritative rejections (retrying can never succeed); everything else,
// including unknown/missing codes, is transient — the Firestore rules are
// the durable gate (a live ID token can't create evaluaciones on its own),
// so failing OPEN on the session here is safe, and failing CLOSED is the
// field-logout bug this classification exists to prevent.
export function clasificarErrorFirestore(err) {
  const code = err && err.code ? err.code : '';
  if (FATAL_FIRESTORE_CODES.has(code)) return 'fatal';
  return 'transient';
}

// Exponential backoff for the profile-read retry loop: attempt 1 -> 600ms,
// attempt 2 -> 1800ms, attempt 3 -> 5400ms (base * 3^(attempt-1)).
export function backoffDelay(attempt, base = 600) {
  return base * 3 ** (attempt - 1);
}
