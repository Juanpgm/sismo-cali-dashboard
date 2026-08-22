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
// constant changes if the external signer starts accepting slot > 3.
// Set to 3 after the apply-time signer probe rejected slot 4 and 10 with
// `400 bad-request` while slot 1 and 3 passed schema validation (see
// formulario/SETUP.md section 7 for the raw probe evidence).
export const MAX_FOTOS = 3;

// Pure predicate for the dynamic add-tile: true while there is room for one
// more photo. `current` is the number of photos already attached.
export function canAddSlot(current, max = MAX_FOTOS) {
  return current < max;
}
