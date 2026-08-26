// Self-check for the pure ATC-20 form logic. Run: node --test formulario/test/logic.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  sugerirClasificacion, buildCodigo, MUNICIPIO, cedulaToEmail, LOGIN_EMAIL_DOMAIN,
  parseConsecutivo, siguienteConsecutivo, validarSegmento, canAddSlot,
  clasificarErrorFirestore, backoffDelay,
  plegarConsecutivoGuardado, siguienteDesdeMax, consecutivosExistentes,
  filtrarPendientes, habitabilidadColor, colapsoLabel, mapsDirUrl,
  elegirEnlaceEncuesta, prioridadColor,
} from '../js/logic.js';

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

// ---- parseConsecutivo -------------------------------------------------------

test('parseConsecutivo extrae el consecutivo cuando el prefijo del inspector coincide', () => {
  assert.equal(parseConsecutivo('76001-1-0040007', '004'), 7);
});

test('parseConsecutivo devuelve null si el código no pertenece a este inspector', () => {
  assert.equal(parseConsecutivo('76001-1-0050007', '004'), null);
});

test('parseConsecutivo es agnóstico al ancho (desborde > 9999)', () => {
  assert.equal(parseConsecutivo('76001-1-00410000', '004'), 10000);
});

// ---- siguienteConsecutivo ---------------------------------------------------

test('siguienteConsecutivo con registros contiguos da el siguiente número', () => {
  assert.equal(siguienteConsecutivo(['76001-1-0040001', '76001-1-0040002', '76001-1-0040003'], '004'), 4);
});

test('siguienteConsecutivo con un salto usa el máximo, no el conteo', () => {
  assert.equal(siguienteConsecutivo(['76001-1-0040001', '76001-1-0040003'], '004'), 4);
});

test('siguienteConsecutivo sin registros previos empieza en 1', () => {
  assert.equal(siguienteConsecutivo([], '004'), 1);
});

// ---- plegarConsecutivoGuardado -----------------------------------------------

test('plegarConsecutivoGuardado adopta un valor guardado mayor que el máximo conocido', () => {
  assert.equal(plegarConsecutivoGuardado(2, 9), 9);
});

test('plegarConsecutivoGuardado no retrocede si el valor guardado es menor (corrección de hueco)', () => {
  assert.equal(plegarConsecutivoGuardado(9, 2), 9);
});

test('plegarConsecutivoGuardado con máximo conocido null adopta el valor guardado', () => {
  assert.equal(plegarConsecutivoGuardado(null, 5), 5);
});

test('plegarConsecutivoGuardado sostiene el ancho por encima de 9999', () => {
  assert.equal(plegarConsecutivoGuardado(9999, 10000), 10000);
});

// ---- siguienteDesdeMax -------------------------------------------------------

test('siguienteDesdeMax suma uno al máximo conocido', () => {
  assert.equal(siguienteDesdeMax(2), 3);
});

test('siguienteDesdeMax sin máximo conocido (null) empieza en 1', () => {
  assert.equal(siguienteDesdeMax(null), 1);
});

test('siguienteDesdeMax por encima de 9999 sigue sumando uno (ancho libre)', () => {
  assert.equal(siguienteDesdeMax(9999), 10000);
});

// ---- validarSegmento --------------------------------------------------------

test('validarSegmento acepta un segmento de 4 dígitos', () => {
  assert.deepEqual(validarSegmento('0005'), { ok: true, value: 5, code: null });
});

test('validarSegmento rechaza longitud distinta de 4', () => {
  assert.deepEqual(validarSegmento('12'), { ok: false, value: null, code: 'longitud' });
});

test('validarSegmento rechaza texto no numérico', () => {
  assert.deepEqual(validarSegmento('abcd'), { ok: false, value: null, code: 'no-numerico' });
});

test('validarSegmento rechaza el segmento cero', () => {
  assert.deepEqual(validarSegmento('0000'), { ok: false, value: null, code: 'cero' });
});

test('validarSegmento rechaza el segmento vacío', () => {
  assert.deepEqual(validarSegmento(''), { ok: false, value: null, code: 'vacio' });
});

// ---- canAddSlot --------------------------------------------------------------

test('canAddSlot permite agregar por debajo del tope', () => {
  assert.equal(canAddSlot(2, 3), true);
});

test('canAddSlot no permite agregar en el tope', () => {
  assert.equal(canAddSlot(3, 3), false);
});

test('canAddSlot no permite agregar por encima del tope', () => {
  assert.equal(canAddSlot(4, 3), false);
});

// ---- clasificarErrorFirestore -------------------------------------------------

test('clasificarErrorFirestore clasifica unavailable como transient', () => {
  assert.equal(clasificarErrorFirestore({ code: 'unavailable' }), 'transient');
});

test('clasificarErrorFirestore clasifica deadline-exceeded como transient', () => {
  assert.equal(clasificarErrorFirestore({ code: 'deadline-exceeded' }), 'transient');
});

test('clasificarErrorFirestore clasifica network-request-failed como transient', () => {
  assert.equal(clasificarErrorFirestore({ code: 'network-request-failed' }), 'transient');
});

test('clasificarErrorFirestore clasifica permission-denied como fatal', () => {
  assert.equal(clasificarErrorFirestore({ code: 'permission-denied' }), 'fatal');
});

test('clasificarErrorFirestore clasifica not-found como fatal', () => {
  assert.equal(clasificarErrorFirestore({ code: 'not-found' }), 'fatal');
});

test('clasificarErrorFirestore trata un código desconocido como transient (falla abierto)', () => {
  assert.equal(clasificarErrorFirestore({ code: 'internal' }), 'transient');
  assert.equal(clasificarErrorFirestore({}), 'transient');
});

// ---- backoffDelay ---------------------------------------------------------

test('backoffDelay en el primer intento usa la base', () => {
  assert.equal(backoffDelay(1), 600);
});

test('backoffDelay en el segundo intento triplica la base', () => {
  assert.equal(backoffDelay(2), 1800);
});

// ---- consecutivosExistentes ------------------------------------------------

test('consecutivosExistentes extrae los consecutivos propios de los ids', () => {
  const set = consecutivosExistentes(['76001-1-0040001', '76001-1-0040003'], '004');
  assert.equal(set.has(1), true);
  assert.equal(set.has(3), true);
  assert.equal(set.has(2), false);
});

test('consecutivosExistentes ignora codigos de otros inspectores y basura', () => {
  const set = consecutivosExistentes(['76001-1-0050001', 'malformado', ''], '004');
  assert.equal(set.size, 0);
});

test('consecutivosExistentes con lista vacia devuelve set vacio', () => {
  assert.equal(consecutivosExistentes([], '004').size, 0);
});

// ---- filtrarPendientes ------------------------------------------------------

test('filtrarPendientes descarta los puntos ya hechos', () => {
  const puntos = [
    { id: 'a', estado_asignacion: 'pendiente' },
    { id: 'b', estado_asignacion: 'hecho' },
    { id: 'c', estado_asignacion: 'en_proceso' },
    { id: 'd' }, // sin estado = sigue pendiente
  ];
  assert.deepEqual(filtrarPendientes(puntos).map((p) => p.id), ['a', 'c', 'd']);
});

test('filtrarPendientes con entrada nula devuelve lista vacia', () => {
  assert.deepEqual(filtrarPendientes(null), []);
});

// ---- habitabilidadColor -----------------------------------------------------

test('habitabilidadColor mapea H a verde, R a ambar, I a rojo', () => {
  assert.equal(habitabilidadColor('h'), 'var(--verde)');
  assert.equal(habitabilidadColor('R1'), 'var(--ambar)');
  assert.equal(habitabilidadColor('r2'), 'var(--ambar)');
  assert.equal(habitabilidadColor('I2'), 'var(--rojo)');
  assert.equal(habitabilidadColor('i3'), 'var(--rojo)');
});

test('habitabilidadColor con valor desconocido o vacio usa muted', () => {
  assert.equal(habitabilidadColor(''), 'var(--muted)');
  assert.equal(habitabilidadColor(null), 'var(--muted)');
  assert.equal(habitabilidadColor('x'), 'var(--muted)');
});

// ---- colapsoLabel -----------------------------------------------------------

test('colapsoLabel etiqueta total y parcial, ignora no/vacio', () => {
  assert.equal(colapsoLabel('total'), 'Total');
  assert.equal(colapsoLabel('Parcial'), 'Parcial');
  assert.equal(colapsoLabel('no'), '');
  assert.equal(colapsoLabel(''), '');
  assert.equal(colapsoLabel(null), '');
});

// ---- mapsDirUrl -------------------------------------------------------------

test('mapsDirUrl arma el enlace de direcciones desde coords {lat, lon}', () => {
  assert.equal(
    mapsDirUrl({ lat: 3.4516, lon: -76.532 }),
    'https://www.google.com/maps/dir/?api=1&destination=3.4516,-76.532',
  );
});

test('mapsDirUrl devuelve cadena vacia si faltan coords', () => {
  assert.equal(mapsDirUrl(null), '');
  assert.equal(mapsDirUrl({ lat: 3.4 }), '');
  assert.equal(mapsDirUrl({ lon: -76.5 }), '');
});

// ---- prioridadColor -----------------------------------------------------------

test('prioridadColor mapea alta a rojo, media a ambar, baja/desconocido a muted', () => {
  assert.equal(prioridadColor('alta'), 'var(--rojo)');
  assert.equal(prioridadColor('MEDIA'), 'var(--ambar)');
  assert.equal(prioridadColor('baja'), 'var(--muted)');
  assert.equal(prioridadColor(''), 'var(--muted)');
  assert.equal(prioridadColor(null), 'var(--muted)');
});

// ---- elegirEnlaceEncuesta ----------------------------------------------------

test('elegirEnlaceEncuesta prefiere el deep link de la app en movil cuando existe', () => {
  const punto = { survey_web: 'https://web.example/x', survey_app: 'arcgis-survey123:///x' };
  assert.equal(elegirEnlaceEncuesta(punto, true), 'arcgis-survey123:///x');
});

test('elegirEnlaceEncuesta usa el enlace web en escritorio aunque haya app', () => {
  const punto = { survey_web: 'https://web.example/x', survey_app: 'arcgis-survey123:///x' };
  assert.equal(elegirEnlaceEncuesta(punto, false), 'https://web.example/x');
});

test('elegirEnlaceEncuesta cae al enlace web en movil si no hay deep link de app', () => {
  const punto = { survey_web: 'https://web.example/x', survey_app: null };
  assert.equal(elegirEnlaceEncuesta(punto, true), 'https://web.example/x');
});

test('elegirEnlaceEncuesta devuelve cadena vacia sin ningun enlace configurado', () => {
  assert.equal(elegirEnlaceEncuesta({ survey_web: null, survey_app: null }, true), '');
  assert.equal(elegirEnlaceEncuesta(null, false), '');
});
