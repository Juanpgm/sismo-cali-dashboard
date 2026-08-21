// Pure form logic, DOM- and Firebase-free so it is testable in Node.

export const MUNICIPIO = '76001'; // DIVIPOLA code for Cali

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
