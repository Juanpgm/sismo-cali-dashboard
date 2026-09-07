// Self-check for the objectivo_id_r -> EDE ObjectID join and the candidato
// PDF builder. Run: node web/js/acciones-capa.test.mjs
import assert from 'node:assert/strict';
import { joinCapa } from './acciones-capa.js';
import { buildCandidatoDocDefinition } from './report.js';

// --- join: first integer of the free-text key wins ----------------------
const features = [
  { objectivo_id_r: 'ID 25' },       // prefixed
  { objectivo_id_r: '473' },         // bare number
  { objectivo_id_r: 'OBJ ID2' },     // glued prefix
  { objectivo_id_r: '16758424' },    // no EDE match -> ede: null
  { objectivo_id_r: null },          // nothing to parse -> ede: null
  { objectivo_id_r: 'ID 0473' },     // zero-padded -> numeric normalization
];
const records = [{ ObjectID: 25 }, { ObjectID: 473 }, { ObjectID: 2 }];
const rows = joinCapa(features, records);
assert.equal(rows.length, 6, 'every feature renders, matched or not');
assert.equal(rows.filter((r) => r.ede).length, 4);
assert.equal(rows[0].ede.ObjectID, 25);
assert.equal(rows[2].ede.ObjectID, 2);
assert.equal(rows[3].ede, null, 'unmatched key still yields a row');
assert.equal(rows[4].ede, null);
assert.equal(rows[5].ede.ObjectID, 473, '"0473" must match ObjectID 473');

// --- candidato doc builder: with and without EDE context ----------------
const form = {
  objectivo_id_r: 'ID 25', candidato_demolicion: 'si', colapso: 'parcial',
  visita: 'no', fecha_registro: 1787666580000,
  justificacion_patologia: 'Falla en elementos no estructurales.',
};
const ede = {
  ObjectID: 25, direccion: 'Cra 44# 44-11', direccion_norm: 'KR 44 # 44-11',
  barrio_vereda: 'Los Cambulos', comuna: 'COMUNA 19',
  nombre_evaluador: 'Ana', criterio_habitabilidad: 'i2', nivel_dano: 'alto',
};
const withEde = JSON.stringify(buildCandidatoDocDefinition(form, ede, { photos: [], signatures: [], mapImage: null }).content);
assert.ok(withEde.includes('Revisión candidato a demolición'));
assert.ok(withEde.includes('Contexto EDE'));
assert.ok(withEde.includes('KR 44 # 44-11'), 'address comes from addressDisplay (normalized)');
assert.ok(withEde.includes('Ana'));

const sinEde = JSON.stringify(buildCandidatoDocDefinition(form, null, { photos: [], signatures: [], mapImage: null }).content);
assert.ok(sinEde.includes('Sin cruce EDE'));
assert.ok(!sinEde.includes('Contexto EDE'), 'no EDE section without a join');

console.log('acciones-capa.test.mjs: all assertions passed');
