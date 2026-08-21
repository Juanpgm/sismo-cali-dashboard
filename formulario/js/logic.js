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
