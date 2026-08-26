// Self-check for the pure `tipo` -> {endpoint, body} routing behind the
// unified Usuarios creation modal (`usuarios-personas-unificadas`, Phase 4).
// Run: node --test "js/**/*.test.mjs" (from web/)
import assert from 'node:assert/strict';
import { payloadForTipo } from './usuarios.js';

// ---- admin/viewer/usuario -> apiUrl('usuarios') create ---------------------
for (const tipo of ['admin', 'viewer', 'usuario']) {
  assert.deepEqual(
    payloadForTipo(tipo, { email: 'ana@x.com', password: 'secret6' }),
    { endpoint: 'usuarios', body: { action: 'create', email: 'ana@x.com', password: 'secret6' } },
    `tipo=${tipo}`,
  );
}

// ---- inspector -> apiUrl('stickers') create --------------------------------
assert.deepEqual(
  payloadForTipo('inspector', { cedula: '123', nombre_completo: 'Ana Torres', entidad: 'SGRED', password: 'secret6' }),
  { endpoint: 'stickers', body: { action: 'create', cedula: '123', nombre_completo: 'Ana Torres', entidad: 'SGRED', password: 'secret6' } },
);

// ---- conductor -> apiUrl('planeacionAsignaciones') crearConductor ---------
// Reuses planeacion.js's own buildConductorPayload (design.md ADR-1) — same
// trimming/shape, no duplicated logic.
assert.deepEqual(
  payloadForTipo('conductor', { nombre_completo: ' Ana ', cedula: ' 123 ', email: ' a@x.com ', telefono: ' 555 ' }),
  { endpoint: 'planeacionAsignaciones', body: { action: 'crearConductor', nombre_completo: 'Ana', cedula: '123', email: 'a@x.com', telefono: '555' } },
);

// ---- @sismocali.gov.co under a non-inspector tipo -> rejected, names inspector
for (const tipo of ['admin', 'viewer', 'usuario']) {
  assert.throws(
    () => payloadForTipo(tipo, { email: 'x@sismocali.gov.co', password: 'secret6' }),
    /inspector/i,
    `tipo=${tipo} with @sismocali.gov.co must throw and name the inspector tipo`,
  );
}
// Inspector tipo itself is obviously unaffected by that guard.
assert.doesNotThrow(() => payloadForTipo('inspector', { cedula: '1', nombre_completo: 'X', entidad: 'Y', password: 'secret6' }));

// ---- unknown tipo -> throws -------------------------------------------------
assert.throws(() => payloadForTipo('bogus', {}));
assert.throws(() => payloadForTipo(undefined, {}));

console.log('ok — usuarios.js payloadForTipo routing');
