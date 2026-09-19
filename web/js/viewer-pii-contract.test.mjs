// D-VIEWER-PII frontend contract. Run: node web/js/viewer-pii-contract.test.mjs
//
// The backend serves a viewer `evaluaciones[].inspector.{tarjeta_profesional,
// num_telefono, correo_contacto}` as "" (keys kept) and an admin with values.
// Only the admin-only Seguimiento tab reads them; the viewer-facing Stickers
// tab (evaluaciones.js / stickers.js / report.js) must neither read them nor
// break when they are empty, absent or null.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildIdentityIndex, buildProfessionalRows, cellHtml, columnsFor, tableBodyHtml } from './seguimiento.js';
import {
  quienDe, inspectorFuenteLabel, faseDe, applyFilters, defaultEvalFilters, fingerprintEvaluaciones,
  evaluacionRenderKey,
} from './evaluaciones.js';

const CONTACT = ['tarjeta_profesional', 'num_telefono', 'correo_contacto'];
const here = (name) => new URL(name, import.meta.url);

// ── 1. Viewer-facing code never reads the contact fields ────────────────────
// A read here would silently render "Sin dato" for every viewer once the
// backend redacts, so it is caught at the source instead.

// The guard is TOTAL: every web/js/*.js module is scanned and ONLY the allowlist below may name a
// contact field, so a new viewer-facing module (or a new read in an old one) fails here with its
// file name instead of depending on someone remembering to extend a hardcoded list. The allowlist
// is the admin-only Seguimiento tab (seguimiento*.js): main.js mounts it only behind `isAdmin()`
// (`view === 'seguimiento' && isAdmin()`), and the backend serves it the values only to admins.
const ADMIN_ONLY_MODULE = /^seguimiento[\w-]*\.js$/;
const scanned = readdirSync(fileURLToPath(new URL('.', import.meta.url))).filter((name) => name.endsWith('.js'));
assert.ok(scanned.includes('evaluaciones.js') && scanned.includes('stickers.js') && scanned.includes('main.js'),
  'the scan sees the viewer-facing modules');
export function contactFieldReaders(files, read) {
  return files.filter((file) => !ADMIN_ONLY_MODULE.test(file))
    .filter((file) => CONTACT.some((field) => read(file).includes(field)));
}
const offenders = contactFieldReaders(scanned, (file) => readFileSync(here(file), 'utf8'));
assert.deepEqual(offenders, [], `modules outside the admin-only allowlist must not read the contact fields (D-VIEWER-PII): ${offenders.join(', ')}`);
// The guard bites: a scratch viewer-facing module that names a field is reported by file name.
assert.deepEqual(
  contactFieldReaders(['evaluaciones.js', 'scratch-viewer.js'], (file) => (file === 'scratch-viewer.js' ? 'x.num_telefono' : '')),
  ['scratch-viewer.js'],
);
// Non-vacuous: the admin-only tab does read them (the scan is looking at the right spelling).
const seguimientoSource = readFileSync(here('seguimiento.js'), 'utf8');
for (const field of CONTACT) assert.ok(seguimientoSource.includes(field), `seguimiento.js reads ${field}`);
console.log('viewer-facing modules never read the contact fields OK');

// ── 2. Seguimiento tolerates empty / absent / null contact fields ───────────

const identity = (extra) => ({
  nombre_completo: 'Ana Ruiz', identificacion: '1020304050', codigo: '004', entidad: 'DAGRD', np: 'P2', ...extra,
});
const sticker = (inspector, id = 's1') => ({
  id, fuente: 'atencionsismo', origen: 'sistema', fase: 1, fecha: '2026-09-10T10:00:00Z',
  inspector_fuente: 'api', inspector, coords: null, descripcion: { nombre: '', direccion: '' },
});

function rowFor(stickers) {
  const idx = buildIdentityIndex({ stickers, surveys: [] });
  const { rows } = buildProfessionalRows({ stickers, surveys: [], identity: idx, today: '2026-09-19' });
  assert.equal(rows.length, 1, 'one professional');
  return rows[0];
}

const VIEWER_SHAPES = {
  'keys kept with empty strings (the backend viewer shape)': { tarjeta_profesional: '', num_telefono: '', correo_contacto: '' },
  'keys absent': {},
  'keys null': { tarjeta_profesional: null, num_telefono: null, correo_contacto: null },
};

for (const [label, contact] of Object.entries(VIEWER_SHAPES)) {
  const row = rowFor([sticker(identity(contact))]);
  assert.equal(row.tarjetaProfesional, '', `${label}: tarjetaProfesional`);
  assert.equal(row.celular, '', `${label}: celular`);
  assert.equal(row.correo, '', `${label}: correo`);
  assert.equal(row.name, 'Ana Ruiz', `${label}: identity untouched`);
  assert.equal(row.cedula, '1020304050', `${label}: identity untouched`);

  const html = tableBodyHtml([row], true, false, columnsFor('totales'), false);
  assert.ok(html.includes('Ana Ruiz'), `${label}: the row renders`);
  assert.equal(cellHtml(row, 'tarjetaProfesional', true), 'Sin dato', `${label}: the empty tarjeta renders the placeholder`);
  assert.ok(!/undefined|null|NaN/.test(html), `${label}: no "undefined"/"null"/"NaN" text in the table`);
}
console.log('seguimiento renders empty/absent/null contact fields without crashing or "undefined" OK');

// Triangulation: the same rows WITH values (the admin shape) do carry them.
{
  const row = rowFor([sticker(identity({
    tarjeta_profesional: 'TP-1', num_telefono: '3001234567', correo_contacto: 'ana@example.com',
  }))]);
  assert.equal(row.tarjetaProfesional, 'TP-1');
  assert.equal(row.celular, '3001234567');
  assert.equal(row.correo, 'ana@example.com');
  assert.ok(tableBodyHtml([row], true, false, columnsFor('totales'), false).includes('TP-1'));
}

// "First non-blank wins" across rows: blanked viewer rows never invent a value.
{
  const row = rowFor([
    sticker(identity({ tarjeta_profesional: '', num_telefono: '', correo_contacto: '' }), 's1'),
    sticker(identity({}), 's2'),
    sticker(identity({ tarjeta_profesional: null }), 's3'),
  ]);
  assert.deepEqual([row.tarjetaProfesional, row.celular, row.correo], ['', '', '']);
}
console.log('seguimiento first-non-blank merge over redacted rows OK');

// ── 3. The Stickers tab helpers ignore the contact fields entirely ──────────

const viewerRow = (insp, fuente = 'api') => ({
  id: 'e1', fuente: 'atencionsismo', origen: 'sistema', fase: 1, clasificacion: 'INSEGURO',
  codigo_edificacion: '76001-1-0040001', inspector_fuente: fuente,
  inspector: { uid: 'u', codigo: '004', nombre_completo: 'Ana Ruiz', identificacion: '123', entidad: 'DAGRD', np: 'P2', ...insp },
  descripcion: { nombre: 'Casa', direccion: 'Calle 1' },
});
const REDACTED = { tarjeta_profesional: '', num_telefono: '', correo_contacto: '' };
const FULL = { tarjeta_profesional: 'TP-1', num_telefono: '3001234567', correo_contacto: 'ana@example.com' };

for (const fuente of ['api', 'evaluacion', 'roster', '']) {
  for (const contact of [REDACTED, {}, { tarjeta_profesional: null }, FULL]) {
    const e = viewerRow(contact, fuente);
    const quien = quienDe(e);
    assert.equal(typeof quien, 'string');
    assert.ok(quien.includes('Ana Ruiz') || fuente === '', `quienDe names the inspector (${fuente})`);
    assert.ok(!/undefined|null/.test(quien + inspectorFuenteLabel(e)), `no "undefined"/"null" (${fuente})`);
    assert.equal(faseDe(e).key, 'FASE_I');
    assert.equal(applyFilters([e], { ...defaultEvalFilters(), search: 'ana' }).length, 1, 'search by inspector name still finds it');
  }
}
// The contact fields are not searchable, so a viewer loses nothing by their absence.
assert.equal(applyFilters([viewerRow(FULL)], { ...defaultEvalFilters(), search: '3001234567' }).length, 0);
assert.equal(applyFilters([viewerRow(REDACTED)], { ...defaultEvalFilters(), search: '3001234567' }).length, 0);

// The whole-record change fingerprint stays deterministic over redacted rows.
assert.equal(evaluacionRenderKey(viewerRow(REDACTED)), evaluacionRenderKey(viewerRow(REDACTED)));
assert.equal(fingerprintEvaluaciones([viewerRow(REDACTED)]), fingerprintEvaluaciones([viewerRow(REDACTED)]));
console.log('Stickers tab helpers unaffected by empty/absent contact fields OK');
