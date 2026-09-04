// Self-check for the pure PDF-report builder. Run: node web/js/report.test.mjs
import assert from 'node:assert/strict';
import { buildReportDocDefinition, MAX_PHOTOS } from './report.js';
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

console.log('report.test.mjs: all assertions passed');
