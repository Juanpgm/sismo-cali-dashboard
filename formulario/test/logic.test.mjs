// Self-check for the pure ATC-20 form logic. Run: node --test formulario/test/logic.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { sugerirClasificacion, buildCodigo, MUNICIPIO, cedulaToEmail, LOGIN_EMAIL_DOMAIN } from '../js/logic.js';

const base = {
  dano_estructural: 'menor',
  peligro_geotecnico: 'menor',
  peligro_no_estructural: 'menor',
  condiciones_salida: 'menor',
  servicios_esenciales: 'menor',
};

test('severo wins over moderado and menor', () => {
  assert.equal(
    sugerirClasificacion({ ...base, condiciones_salida: 'moderado', peligro_geotecnico: 'severo' }),
    'INSEGURO',
  );
});

test('moderado wins over menor', () => {
  assert.equal(
    sugerirClasificacion({ ...base, peligro_no_estructural: 'moderado' }),
    'USO_RESTRINGIDO',
  );
});

test('all menor suggests INSPECCIONADA', () => {
  assert.equal(sugerirClasificacion(base), 'INSPECCIONADA');
});

test('first consecutivo builds the full code', () => {
  assert.equal(buildCodigo('1', '004', 1), '76001-1-0040001');
});

test('consecutivo pads to 4 digits', () => {
  assert.equal(buildCodigo('1', '004', 23), '76001-1-0040023');
});

test('severo gana aunque haya moderados presentes', () => {
  assert.equal(
    sugerirClasificacion({ ...base, dano_estructural: 'moderado', servicios_esenciales: 'severo' }),
    'INSEGURO',
  );
});

test('un valor nulo o faltante no fuerza severo/moderado', () => {
  assert.equal(sugerirClasificacion({ ...base, dano_estructural: null }), 'INSPECCIONADA');
});

test('el área de centro poblado y rural entran en el código', () => {
  assert.equal(buildCodigo('2', '004', 1), '76001-2-0040001');
  assert.equal(buildCodigo('3', '012', 7), '76001-3-0120007');
});

test('MUNICIPIO es el código DANE de Cali', () => {
  assert.equal(MUNICIPIO, '76001');
});

test('la cédula sola se convierte en el correo de login', () => {
  assert.equal(cedulaToEmail('1110547406'), `1110547406@${LOGIN_EMAIL_DOMAIN}`);
});

test('un correo completo se usa tal cual (sin doble @)', () => {
  assert.equal(cedulaToEmail('inspector@cali.gov.co'), 'inspector@cali.gov.co');
});

test('cédula con espacios y mayúsculas se normaliza', () => {
  assert.equal(cedulaToEmail('  1110547406  '), `1110547406@${LOGIN_EMAIL_DOMAIN}`);
  assert.equal(cedulaToEmail('Inspector@Cali.Gov.Co'), 'inspector@cali.gov.co');
});

test('cédula vacía o nula devuelve cadena vacía', () => {
  assert.equal(cedulaToEmail(''), '');
  assert.equal(cedulaToEmail(null), '');
});

// Documented ceiling (see SETUP.md): past 9999 the consecutivo widens the code.
// This asserts the CURRENT behavior so a future fix trips the test on purpose.
test('consecutivo > 9999 desborda el ancho fijo (comportamiento actual)', () => {
  assert.equal(buildCodigo('1', '004', 10000), '76001-1-00410000');
});
