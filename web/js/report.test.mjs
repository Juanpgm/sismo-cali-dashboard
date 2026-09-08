// Self-check for the pure PDF-report builder. Run: node web/js/report.test.mjs
import assert from 'node:assert/strict';
import {
  buildReportDocDefinition, buildEvaluacionDocDefinition, MAX_PHOTOS, evalFaseLabelDe,
} from './report.js';
import { DETAIL_GROUPS } from './utils.js';

const fullRecord = {
  ObjectID: 42,
  codigo: 'EDE-0042',
  fecha_inspeccion: '2026-08-13',
  nombre_evaluador: 'Ana',
  municipio: 'Cali',
  epoca_construccion: '1990-2000',
  n_pisos: 3,
  colapso_total: 'No',
  danos_estructura: 'Leve',
  riesgo_ab: 'Bajo',
  observaciones: 'Sin novedad',
};

// --- header ------------------------------------------------------------
const doc = buildReportDocDefinition(fullRecord, { photos: [], signatures: [], mapImage: null });
const flatText = JSON.stringify(doc.content);
assert.ok(flatText.includes('EDE-0042'), 'header should show the record codigo');
assert.ok(/Fecha de generaci/i.test(flatText), 'header should show a generation date label');
assert.ok(/no sustituye/i.test(flatText), 'header should include the disclaimer');

// --- one section per populated DETAIL_GROUPS group ----------------------
const groupNames = Object.keys(DETAIL_GROUPS);
const populatedGroups = groupNames.filter((g) => DETAIL_GROUPS[g].some((f) => fullRecord[f] !== undefined));
for (const g of populatedGroups) assert.ok(flatText.includes(g), `expected section "${g}" in doc content`);
const emptyGroups = groupNames.filter((g) => !populatedGroups.includes(g));
for (const g of emptyGroups) assert.ok(!flatText.includes(`"text":"${g}"`), `unpopulated group "${g}" must be omitted`);

// --- null image -> placeholder node with sourceUrl ----------------------
const withMissingMap = buildReportDocDefinition(fullRecord, {
  photos: [], signatures: [], mapImage: { dataURL: null, sourceUrl: 'https://maps.example/42' },
});
const mapSection = JSON.stringify(withMissingMap.content);
assert.ok(/Imagen no disponible/.test(mapSection), 'missing map image should render a placeholder');
assert.ok(mapSection.includes('https://maps.example/42'), 'placeholder should carry the source link');

// --- >MAX_PHOTOS photos -> capped + overflow note ------------------------
const manyPhotos = Array.from({ length: MAX_PHOTOS + 3 }, (_, i) => ({ dataURL: `data:image/png;base64,${i}`, sourceUrl: `https://x/${i}` }));
const withOverflow = buildReportDocDefinition(fullRecord, { photos: manyPhotos, signatures: [], mapImage: null });
const photosSection = withOverflow.content.find((n) => n.columns);
assert.equal(photosSection.columns.length, MAX_PHOTOS, 'photos should be capped at MAX_PHOTOS');
assert.ok(JSON.stringify(withOverflow.content).includes('3 fotos adicionales no incluidas'), 'overflow note should report the excess count');

// --- empty photos/signatures -> section present but marked empty, no throw
const sparse = buildReportDocDefinition(fullRecord, { photos: [], signatures: [], mapImage: null });
const sparseText = JSON.stringify(sparse.content);
assert.ok(/Sin fotos en el survey/.test(sparseText), 'empty photos should be marked, not silently dropped');
assert.ok(/Sin firmas en el survey/.test(sparseText), 'empty signatures should be marked, not silently dropped');

// --- sparse record (no populated groups) must not throw ------------------
assert.doesNotThrow(() => buildReportDocDefinition({}, { photos: [], signatures: [], mapImage: null }));

// --- buildEvaluacionDocDefinition ------------------------------------------
const fullEvaluacion = {
  codigo_edificacion: '76001-1-0040001',
  consecutivo: 3,
  municipio: '76001',
  area: 'Area 1',
  area_nombre: 'Comuna 5',
  clasificacion: 'USO_RESTRINGIDO',
  alcance: 'Exterior',
  coords: { lat: 3.45, lng: -76.53, accuracy: 8 },
  descripcion: { nombre: 'Edificio Test', direccion: 'Calle 5 # 10-20' },
  restricciones: 'No ingresar al segundo piso',
  acciones_posteriores: { barricadas: true, evaluacion_detallada: false },
  comentarios: 'Grietas visibles',
  inspector: {
    nombre_completo: 'Ana Perez', codigo: '004', identificacion: '123456',
    entidad: 'DAGMA', np: 'P3',
  },
  fecha: '2026-08-13T10:00:00Z',
  fotos: [],
};

const evalDoc = buildEvaluacionDocDefinition(fullEvaluacion, { photos: [], mapImage: null });
const evalText = JSON.stringify(evalDoc.content);
assert.ok(/Informe de evaluaci.n ATC-20/.test(evalText), 'title should identify the ATC-20 report');
assert.ok(evalText.includes('76001-1-0040001'), 'header should show codigo_edificacion');
assert.ok(evalText.includes('Edificio Test'), 'Edificación group should show the nombre');
assert.ok(evalText.includes('Calle 5 # 10-20'), 'Edificación group should show the direccion');
assert.ok(evalText.includes('No ingresar al segundo piso'), 'Evaluación group should show restricciones');
assert.ok(evalText.includes('Grietas visibles'), 'Evaluación group should show comentarios');
assert.ok(evalText.includes('Ana Perez'), 'Inspector group should show nombre_completo');
assert.ok(evalText.includes('fase II'), 'Inspector group should show the derived Fase label');
assert.ok(evalText.includes('P3'), 'Inspector group should show the raw NP value');
assert.ok(!/Firmas/.test(evalText), 'evaluaciones have no firma concept: no Firmas section');

// --- buildEvaluacionDocDefinition: fotos: [] -> placeholder, no crash ------
const sparseFotos = buildEvaluacionDocDefinition(fullEvaluacion, { photos: [], mapImage: null });
assert.ok(/Sin fotos/i.test(JSON.stringify(sparseFotos.content)), 'empty fotos should render a placeholder, not silently vanish');

// --- buildEvaluacionDocDefinition: coords: null -> no crash, "Sin coordenadas"
const noCoords = { ...fullEvaluacion, coords: null };
assert.doesNotThrow(() => buildEvaluacionDocDefinition(noCoords, { photos: [], mapImage: null }));
const noCoordsText = JSON.stringify(buildEvaluacionDocDefinition(noCoords, { photos: [], mapImage: null }).content);
assert.ok(noCoordsText.includes('Sin coordenadas'), 'missing coords should read "Sin coordenadas", matching the on-screen modal');

// --- buildEvaluacionDocDefinition: inspector.np === '' -> Fase I fallback --
const npBlank = { ...fullEvaluacion, inspector: { ...fullEvaluacion.inspector, np: '' } };
const npBlankText = JSON.stringify(buildEvaluacionDocDefinition(npBlank, { photos: [], mapImage: null }).content);
assert.ok(npBlankText.includes('fase I'), 'blank NP should default to the Fase I label, never Fase II');
assert.ok(!npBlankText.includes('fase II'), 'blank NP must not read as Fase II');
assert.ok(/"NP"[\s\S]{0,40}"Sin dato"/.test(npBlankText) || npBlankText.includes('Sin dato'), 'blank NP row should show "Sin dato", not blank');

// --- buildEvaluacionDocDefinition: sparse record must not throw ------------
assert.doesNotThrow(() => buildEvaluacionDocDefinition({}, { photos: [], mapImage: null }));

// --- evalFaseLabelDe: atencionsismo + blank NP -> "sin dato" (design D1) ---
// Mirrors evaluaciones.js's faseDe()/FASE_SIN_DATO without importing that
// module (circular import: evaluaciones.js already imports report.js).
assert.strictEqual(evalFaseLabelDe({ fuente: 'atencionsismo', inspector: { np: '' } }), 'sin dato');
assert.strictEqual(evalFaseLabelDe({ fuente: 'atencionsismo', inspector: { np: '  ' } }), 'sin dato');
assert.strictEqual(evalFaseLabelDe({ fuente: 'atencionsismo', inspector: { np: 'P4' } }), 'fase II');
assert.strictEqual(evalFaseLabelDe({ fuente: 'atencionsismo', inspector: { np: 'P1' } }), 'fase I');
assert.strictEqual(evalFaseLabelDe({ fuente: 'firestore', inspector: { np: '' } }), 'fase I');
assert.strictEqual(evalFaseLabelDe({ inspector: { np: '' } }), 'fase I');
// Missing inspector object entirely, per source.
assert.strictEqual(evalFaseLabelDe({ fuente: 'atencionsismo' }), 'sin dato');
assert.strictEqual(evalFaseLabelDe({ fuente: 'firestore' }), 'fase I');
assert.strictEqual(evalFaseLabelDe({}), 'fase I');

console.log('report.test.mjs: evalFaseLabelDe OK');

// --- buildEvaluacionDocDefinition: atencionsismo + blank NP shows "sin dato"
// in the PDF's Inspector/Fase row, not a lying "fase I" (design D1 step 3).
const atencionsismoBlankNp = {
  ...fullEvaluacion, fuente: 'atencionsismo', inspector: { ...fullEvaluacion.inspector, np: '' },
};
const atencionsismoBlankNpText = JSON.stringify(
  buildEvaluacionDocDefinition(atencionsismoBlankNp, { photos: [], mapImage: null }).content,
);
assert.ok(atencionsismoBlankNpText.includes('sin dato'), 'atencionsismo + blank NP should show "sin dato" in the PDF, not a Fase I/II lie');
assert.ok(!atencionsismoBlankNpText.includes('"fase I"'), 'atencionsismo + blank NP must not fall back to the "fase I" value');

console.log('report.test.mjs: all assertions passed');

// --- buildEvaluacionDocDefinition: roster-fallback identity gets a
// misattribution-risk caveat in the printed Inspector section (2026-09-08) --
const CAVEAT_TEXT = 'no verificado contra esta evaluación';

const rosterFallback = { ...fullEvaluacion, inspector_fuente: 'roster' };
const rosterFallbackText = JSON.stringify(buildEvaluacionDocDefinition(rosterFallback, { photos: [], mapImage: null }).content);
assert.ok(rosterFallbackText.includes(CAVEAT_TEXT), 'inspector_fuente "roster" should print the unverified-identity caveat');

const verifiedMatch = { ...fullEvaluacion, inspector_fuente: 'evaluacion' };
const verifiedMatchText = JSON.stringify(buildEvaluacionDocDefinition(verifiedMatch, { photos: [], mapImage: null }).content);
assert.ok(!verifiedMatchText.includes(CAVEAT_TEXT), 'inspector_fuente "evaluacion" must not print the caveat');

const noFuenteField = { ...fullEvaluacion };
delete noFuenteField.inspector_fuente;
const noFuenteFieldText = JSON.stringify(buildEvaluacionDocDefinition(noFuenteField, { photos: [], mapImage: null }).content);
assert.ok(!noFuenteFieldText.includes(CAVEAT_TEXT), 'Firestore-sourced records with no inspector_fuente field must not print the caveat');

console.log('report.test.mjs: roster-fallback inspector caveat OK');
