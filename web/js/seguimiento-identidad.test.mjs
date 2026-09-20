// Identity unification + display-name casing for the DEPURADO branch of
// seguimiento.js. Run: node --test web/js/seguimiento-identidad.test.mjs
//
// Three contracts live here, all of them presentation/join rules that run
// ONCE where the depurado profile is built (buildIdentityIndexFromDepuracion),
// never by mutating the backend payload:
//
//   D-NOPERSONA  a `no_persona` registry stub never gets a table row; its
//                activity is re-attributed to the certified person when the
//                stub is that person's name variant, and otherwise falls to
//                the ordinary "not in the padrón" orphan path.
//   D-VARIANTE   the stub -> certified match is a strict name-prefix match
//                with exactly one certified owner; an ambiguous prefix is
//                NEVER merged (two different humans must not collapse).
//   D-NOMCASE    every displayed professional name is Title Case, accents
//                preserved, Spanish particles lowercase.
//
// The legacy (no depuracion / activa:false) path must stay byte-identical —
// pinned at the bottom of this file.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildIdentityIndex, buildProfessionalRows, professionalKeyOf, revisionManualHtml, titleCaseName,
} from './seguimiento.js';

// ── fixtures ────────────────────────────────────────────────────────────────

/** A sticker as `/stickers-atencionsismo` serves it. */
function sticker({
  cedula = '', nombre = '', fecha = '2026-09-10T14:00:00+00:00', fase = 1,
  fuente = 'api', tarjeta = '', barrio = 'BARRIO',
} = {}) {
  return {
    // `fuente` is what stickers.js's tagFuente stamps on every record; faseKeyDe
    // only honours the explicit `fase` for an atencionsismo one.
    fuente: 'atencionsismo',
    fecha,
    fase,
    inspector_fuente: fuente,
    barrio,
    inspector: {
      identificacion: cedula, nombre_completo: nombre, tarjeta_profesional: tarjeta, np: 'P1',
    },
  };
}

/** A Survey (`survey_cali`) record — never carries a cédula. */
function survey({ nombre = '', fecha = '2026-09-10' } = {}) {
  return { nombre_evaluador: nombre, fecha_inspeccion: fecha, entidad: '' };
}

/** One `depuracion.inspectores` record. */
function inspector({
  cedula, nombre, noPersona = false, estado = 'activo', codigo = '048',
  alias = [], profesion = '', tarjeta = '', enfasis = '',
} = {}) {
  return {
    identidad_key: cedula,
    identificacion: cedula,
    nombre_completo: nombre,
    cedulas_unificadas: alias,
    codigo,
    entidad: '',
    np: 'P2',
    np_fuente: 'vercel',
    fase: 'Fase I',
    fase_np_faltante: false,
    estado_sugerido: estado,
    fuente_dato: 'main',
    tarjeta_profesional: tarjeta,
    enfasis,
    profesion,
    num_telefono: '',
    correo_contacto: '',
    no_persona: noPersona,
    cedula_sospechosa: false,
    tiene_sticker_valido: true,
    dias_inactivo: null,
    ultimo_sticker: null,
  };
}

function depuracionBlock(inspectores, aliasNombres = {}) {
  return {
    activa: true,
    motivo: '',
    referencia_generada_en: '2026-09-19T00:00:00+00:00',
    inspectores,
    grupo_externos: null,
    alias_nombres: aliasNombres,
    revision_manual: [],
  };
}

// The production case (masked): one certified person and one `no_persona`
// registry stub whose name is the same person without the last surname and
// whose cédula is a different, implausible number.
const CERT = { cedula: '1144205597', nombre: 'JUAN DAVID HERNANDEZ BUENO' };
const STUB = { cedula: '1488438729', nombre: 'Juan David Hernández' };

function padronDelCasoReal() {
  return depuracionBlock([
    inspector({
      cedula: CERT.cedula, nombre: CERT.nombre, codigo: '048',
      profesion: 'ingeniero civil', tarjeta: '171037-0598569VLL', enfasis: 'Gestion del riesgo',
    }),
    inspector({
      cedula: STUB.cedula, nombre: STUB.nombre, noPersona: true, estado: 'no_persona',
      codigo: '', profesion: 'ingeniero', tarjeta: 'Vll', alias: ['1839622460'],
    }),
  // The backend's own survey-name dedupe points the SHORT spelling at the stub.
  ], { 'juan david hernandez': STUB.cedula, 'juan david hernandez bueno': CERT.cedula });
}

// ── D-NOMCASE: titleCaseName ────────────────────────────────────────────────

test('titleCaseName: upper/lower/mixed input all render the same Title Case', () => {
  for (const raw of ['JUAN DAVID HERNANDEZ BUENO', 'juan david hernandez bueno', 'JuAn DaViD hErNaNdEz BuEnO']) {
    assert.equal(titleCaseName(raw), 'Juan David Hernandez Bueno');
  }
});

test('titleCaseName: accents are preserved, never stripped nor re-decomposed', () => {
  assert.equal(titleCaseName('JUAN DAVID HERNÁNDEZ'), 'Juan David Hernández');
  assert.equal(titleCaseName('maría josé ñuñez'), 'María José Ñuñez');
  // NFD input (accent as a combining mark) normalizes to the same NFC string.
  assert.equal(titleCaseName('MARÍA'), 'María');
  assert.equal(titleCaseName('MARÍA'), titleCaseName('MARÍA'));
});

test('titleCaseName: Spanish particles stay lowercase except as the first token', () => {
  assert.equal(titleCaseName('JOSE DE LA CRUZ Y LOS SANTOS'), 'Jose de la Cruz y los Santos');
  assert.equal(titleCaseName('DEL VALLE PEREZ'), 'Del Valle Perez');
  assert.equal(titleCaseName('LA ROSA DEL CAMPO'), 'La Rosa del Campo');
});

test('titleCaseName: apostrophes, hyphens, initials and roman numerals', () => {
  assert.equal(titleCaseName("o'neil MCDONALD"), "O'Neil Mcdonald");
  assert.equal(titleCaseName('PEREZ-GOMEZ ANA'), 'Perez-Gomez Ana');
  assert.equal(titleCaseName('juan d. perez'), 'Juan D. Perez');
  assert.equal(titleCaseName('JUAN PEREZ III'), 'Juan Perez III');
  // A lowercase look-alike is a name, not a numeral: never forced to uppercase.
  assert.equal(titleCaseName('juan perez iii'), 'Juan Perez Iii');
});

test('titleCaseName: empty, blank and non-string inputs collapse to ""', () => {
  for (const raw of ['', '   ', null, undefined, 42, {}, [], true, NaN]) {
    assert.equal(titleCaseName(raw), '');
  }
});

test('titleCaseName: whitespace is trimmed and collapsed; digits survive', () => {
  assert.equal(titleCaseName('  JUAN   DAVID  '), 'Juan David');
  assert.equal(titleCaseName('JUAN\tDAVID\nPEREZ'), 'Juan David Perez');
  assert.equal(titleCaseName('inspector 2 perez'), 'Inspector 2 Perez');
});

test('titleCaseName: idempotent over every fixture, including hostile input', () => {
  const casos = [
    'JUAN DAVID HERNANDEZ BUENO', 'jose de la cruz', "o'neil", 'PEREZ-GOMEZ', 'JUAN PEREZ III',
    'MARÍA', '<b>juan</b>', '   ', 'ANA 2 DE LOS RIOS', 'ß STRASSE',
    `${'a'.repeat(2048)} perez`,
  ];
  for (const raw of casos) {
    const una = titleCaseName(raw);
    assert.equal(titleCaseName(una), una, `no idempotente: ${JSON.stringify(raw.slice(0, 40))}`);
  }
});

test('titleCaseName: a 2000+ char name is handled without truncation', () => {
  const largo = `${'JUAN '.repeat(500)}PEREZ`.trim();
  const out = titleCaseName(largo);
  assert.equal(out.length, largo.length);
  assert.ok(out.startsWith('Juan Juan '));
  assert.ok(out.endsWith('Perez'));
});

test('titleCaseName: never introduces HTML — hostile text is passed through verbatim', () => {
  const out = titleCaseName('<img src=x onerror=alert(1)> perez');
  assert.ok(!out.includes('&'), 'no debe escapar aquí (eso es del render)');
  assert.ok(out.toLowerCase().includes('<img'), 'no debe reordenar ni borrar el texto');
  // Only the CASING may change: nothing is added, removed nor reordered — escaping is the render's job.
  assert.equal(out.toLowerCase(), '<img src=x onerror=alert(1)> perez');
});

// ── D-NOPERSONA + D-VARIANTE: the real duplicate becomes ONE row ────────────

test('the no_persona name variant and the certified person are ONE row', () => {
  const depuracion = padronDelCasoReal();
  const stickers = [
    sticker({ cedula: CERT.cedula, nombre: CERT.nombre, fase: 1 }),
    sticker({ cedula: CERT.cedula, nombre: CERT.nombre, fase: 2 }),
    // A sticker still carrying the STUB's cédula must land on the certified row.
    sticker({ cedula: STUB.cedula, nombre: STUB.nombre, fase: 1 }),
    // …and one carrying the stub's OWN alias cédula too.
    sticker({ cedula: '1839622460', nombre: STUB.nombre, fase: 1 }),
  ];
  // A Survey record typed with the short spelling: alias_nombres points it at
  // the stub, so only the unification can bring it home.
  const surveys = [survey({ nombre: 'Juan David Hernández' })];
  const identity = buildIdentityIndex({ stickers, surveys, depuracion });
  const { rows, totals } = buildProfessionalRows({
    stickers, surveys, identity, today: '2026-09-19',
  });

  assert.equal(rows.length, 1, 'la persona duplicada debe ser UNA sola fila');
  const [row] = rows;
  assert.equal(row.key, `ced:${CERT.cedula}`);
  assert.equal(row.cedula, CERT.cedula);
  assert.equal(row.estadoSugerido, 'activo');
  assert.equal(row.noPersona, false);
  assert.equal(row.codigo, '048');
  assert.equal(row.profesion, 'Ingeniero civil');
  assert.equal(row.stickersFase1, 3);
  assert.equal(row.stickersFase2, 1);
  assert.equal(row.stickersTotal, 4);
  assert.equal(row.surveyTotal, 1);
  assert.equal(row.total, 5);
  // Totals stay exact: nothing is dropped by the merge.
  assert.equal(totals.stickers, 4);
  assert.equal(totals.surveys, 1);
  assert.equal(totals.unassigned, 0);
  assert.equal(totals.professionals, 1);
  // The padrón counts CERTIFIED people only — the stub is not one.
  assert.equal(totals.padron, 1);
});

test('the certified name is displayed Title Case in the row', () => {
  const depuracion = padronDelCasoReal();
  const stickers = [sticker({ cedula: CERT.cedula, nombre: CERT.nombre })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  const { rows } = buildProfessionalRows({ stickers, surveys: [], identity, today: '2026-09-19' });
  assert.equal(rows[0].name, 'Juan David Hernandez Bueno');
});

test('the raw depuracion payload is never mutated by the profile build', () => {
  const depuracion = padronDelCasoReal();
  const antes = JSON.stringify(depuracion);
  buildIdentityIndex({ stickers: [], surveys: [], depuracion });
  assert.equal(JSON.stringify(depuracion), antes);
});

test('a no_persona stub is never seeded as its own zero-activity row', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA PEREZ' }),
    inspector({
      cedula: '222', nombre: 'CUENTA MIGRADA SISTEMA', noPersona: true,
      estado: 'no_persona', codigo: '',
    }),
  ]);
  const identity = buildIdentityIndex({ stickers: [], surveys: [], depuracion });
  assert.equal(identity.profiles.size, 1);
  assert.ok(identity.profiles.has('ced:111'));
  assert.ok(!identity.profiles.has('ced:222'));
  const { rows, totals } = buildProfessionalRows({
    stickers: [], surveys: [], identity, today: '2026-09-19',
  });
  assert.equal(rows.length, 1);
  assert.equal(totals.padron, 1);
});

test('a no_persona stub WITH stickers and no certified variant keeps its activity', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA PEREZ' }),
    inspector({
      cedula: '222', nombre: 'SOPORTE TECNICO SISTEMAS', noPersona: true,
      estado: 'no_persona', codigo: '', profesion: 'ingeniero',
    }),
  ]);
  const stickers = [
    sticker({ cedula: '111', nombre: 'ANA PEREZ' }),
    sticker({ cedula: '222', nombre: 'SOPORTE TECNICO SISTEMAS' }),
    sticker({ cedula: '222', nombre: 'SOPORTE TECNICO SISTEMAS' }),
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  const { rows, totals } = buildProfessionalRows({
    stickers, surveys: [], identity, today: '2026-09-19',
  });
  // Nothing is dropped: every sticker is still counted somewhere.
  assert.equal(totals.stickers, 3);
  assert.equal(totals.unassigned, 0);
  const stub = rows.find((r) => r.key !== 'ced:111');
  assert.ok(stub, 'la actividad del stub sigue visible como fila sin padrón');
  assert.equal(stub.stickersTotal, 2);
  // …but it carries NO certified data and is not flagged as a padrón person.
  assert.equal(stub.estadoSugerido, '');
  assert.equal(stub.noPersona, false);
  assert.equal(stub.profesion, undefined);
  assert.equal(stub.name, 'Soporte Tecnico Sistemas', 'nombre prestado en Title Case');
  assert.equal(totals.padron, 1, 'el padrón sigue contando sólo certificados');
});

test('an ambiguous name variant is NEVER merged into either candidate', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'JUAN PEREZ GOMEZ TORRES' }),
    inspector({ cedula: '333', nombre: 'JUAN PEREZ GOMEZ RUIZ' }),
    inspector({
      cedula: '222', nombre: 'JUAN PEREZ GOMEZ', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const stickers = [sticker({ cedula: '222', nombre: 'JUAN PEREZ GOMEZ' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:222',
    'un prefijo reclamado por dos personas se queda en su propia fila, nunca en la de un homónimo');
  const { rows } = buildProfessionalRows({ stickers, surveys: [], identity, today: '2026-09-19' });
  const conActividad = rows.filter((r) => r.total > 0);
  assert.equal(conActividad.length, 1);
  assert.equal(conActividad[0].key, 'ced:222');
});

test('D-VARIANTE is a TRUE prefix rule: merely sharing a prefix never merges', () => {
  // Every pair below shares its first two tokens and NOTHING else: they are
  // different humans, and the registry gives no evidence that they are not.
  const casos = [
    ['JUAN CARLOS RAMIREZ TORRES', 'JUAN CARLOS GOMEZ'],
    ['MARIA FERNANDA TORRES SANCHEZ', 'MARIA FERNANDA LOPEZ ARIAS'],
    ['JUAN PEREZ GOMEZ', 'JUAN PEREZ RUIZ'],
    // …and the shorter side must carry at least 3 tokens to identify anyone.
    ['JUAN DAVID HERNANDEZ BUENO', 'JUAN DAVID'],
  ];
  for (const [certificado, stub] of casos) {
    const depuracion = depuracionBlock([
      inspector({ cedula: '111', nombre: certificado }),
      inspector({ cedula: '222', nombre: stub, noPersona: true, estado: 'no_persona', codigo: '' }),
    ]);
    const stickers = [sticker({ cedula: '222', nombre: stub })];
    const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
    assert.equal(professionalKeyOf(stickers[0], identity), 'ced:222', `${stub} !-> ${certificado}`);
    assert.equal(identity.cedulaFusionadaACedulaSurvivor.has('222'), false, `${stub} !-> ${certificado}`);
  }
});

test('D-VARIANTE: a stub that DIVERGES after a shared prefix is never absorbed', () => {
  // "Ana Maria Perez Torres" and "Ana Maria Perez Gomez" share three tokens and
  // then name two different humans: neither WHOLE name is the other's beginning.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA MARIA PEREZ TORRES' }),
    inspector({
      cedula: '222', nombre: 'ANA MARIA PEREZ GOMEZ', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const stickers = [sticker({ cedula: '222', nombre: 'ANA MARIA PEREZ GOMEZ' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:222');
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.has('222'), false);
});

test('D-VARIANTE: a stub that fits TWO different certified people is never absorbed', () => {
  // The stub's whole name is the beginning of 111's name, AND 333's whole name is
  // the beginning of the stub's. Two candidates, two humans: never guess.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA MARIA PEREZ TORRES GOMEZ' }),
    inspector({ cedula: '333', nombre: 'ANA MARIA PEREZ' }),
    inspector({
      cedula: '222', nombre: 'ANA MARIA PEREZ TORRES', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const stickers = [sticker({ cedula: '222', nombre: 'ANA MARIA PEREZ TORRES' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:222');
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.has('222'), false);
});

test('D-VARIANTE merges in both directions when one WHOLE name is the prefix of the other', () => {
  const pares = [
    // stub shorter than the certified person (the production case)
    ['JUAN DAVID HERNANDEZ BUENO', 'JUAN DAVID HERNANDEZ'],
    // stub longer
    ['ANA MARIA PEREZ', 'ANA MARIA PEREZ TORRES'],
  ];
  for (const [certificado, stub] of pares) {
    const depuracion = depuracionBlock([
      inspector({ cedula: '111', nombre: certificado }),
      inspector({ cedula: '222', nombre: stub, noPersona: true, estado: 'no_persona', codigo: '' }),
    ]);
    const stickers = [sticker({ cedula: '222', nombre: stub })];
    const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
    assert.equal(professionalKeyOf(stickers[0], identity), 'ced:111', `${stub} -> ${certificado}`);
  }
});

test('a shared SHORT prefix never vetoes the unique LONGER one', () => {
  // "juan david" belongs to two different certified people, but
  // "juan david hernandez" belongs to exactly one — the longest match wins.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'JUAN DAVID HERNANDEZ BUENO' }),
    inspector({ cedula: '333', nombre: 'JUAN DAVID TORRES' }),
    inspector({
      cedula: '222', nombre: 'JUAN DAVID HERNANDEZ', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const stickers = [sticker({ cedula: '222', nombre: 'JUAN DAVID HERNANDEZ' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:111');
});

test('a single-token stub name is never merged (too weak to identify a person)', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'JUAN PEREZ GOMEZ' }),
    inspector({ cedula: '222', nombre: 'JUAN', noPersona: true, estado: 'no_persona', codigo: '' }),
  ]);
  const stickers = [sticker({ cedula: '222', nombre: 'JUAN' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:222');
});

test('a stub holding a código is evidence, not a stub: it is never absorbed', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'JUAN PEREZ GOMEZ TORRES' }),
    inspector({
      cedula: '222', nombre: 'JUAN PEREZ GOMEZ', noPersona: true, estado: 'no_persona', codigo: '099',
    }),
  ]);
  const stickers = [sticker({ cedula: '222', nombre: 'JUAN PEREZ GOMEZ' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:222');
  // Without the código the SAME shape IS absorbed: the código is the only difference.
  const sinCodigo = depuracionBlock([
    inspector({ cedula: '111', nombre: 'JUAN PEREZ GOMEZ TORRES' }),
    inspector({
      cedula: '222', nombre: 'JUAN PEREZ GOMEZ', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  assert.equal(professionalKeyOf(stickers[0], buildIdentityIndex({
    stickers, surveys: [], depuracion: sinCodigo,
  })), 'ced:111');
});

test('two no_persona stubs never merge into each other', () => {
  const depuracion = depuracionBlock([
    inspector({
      cedula: '111', nombre: 'CUENTA SISTEMA MIGRADA', noPersona: true, estado: 'no_persona', codigo: '',
    }),
    inspector({
      cedula: '222', nombre: 'CUENTA SISTEMA', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const identity = buildIdentityIndex({ stickers: [], surveys: [], depuracion });
  assert.equal(identity.profiles.size, 0);
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.size, 0);
});

test('a certified person is never absorbed by another certified person', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'JUAN PEREZ GOMEZ' }),
    inspector({ cedula: '222', nombre: 'JUAN PEREZ' }),
  ]);
  const identity = buildIdentityIndex({ stickers: [], surveys: [], depuracion });
  assert.equal(identity.profiles.size, 2);
});

test('alias cédulas with dots, float tails and leading zeros all route home', () => {
  const depuracion = depuracionBlock([
    inspector({
      cedula: '0012345', nombre: 'ANA MARIA PEREZ', alias: ['1.234.567', '7654321.0'],
    }),
  ]);
  const stickers = [
    sticker({ cedula: '0012345', nombre: 'ANA MARIA PEREZ' }),
    sticker({ cedula: '1234567', nombre: 'ANA MARIA PEREZ' }),
    sticker({ cedula: ' 7654321 ', nombre: 'ANA MARIA PEREZ' }),
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  for (const s of stickers) assert.equal(professionalKeyOf(s, identity), 'ced:0012345');
  const { rows } = buildProfessionalRows({ stickers, surveys: [], identity, today: '2026-09-19' });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].stickersTotal, 3);
});

test('an ABSORBED stub never steals an alias cédula a certified person already owns', () => {
  // "999" is Ana's merged-away cédula. The stub IS absorbed (by Juan), and while
  // re-pointing its own aliases it must leave Ana's cédula exactly where it was.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA MARIA PEREZ', alias: ['999'] }),
    inspector({ cedula: '333', nombre: 'JUAN PEREZ GOMEZ TORRES' }),
    inspector({
      cedula: '222', nombre: 'JUAN PEREZ GOMEZ', noPersona: true, estado: 'no_persona',
      codigo: '', alias: ['999', '888'],
    }),
  ]);
  const stickers = [
    sticker({ cedula: '999', nombre: 'ANA MARIA PEREZ' }),
    sticker({ cedula: '222', nombre: 'JUAN PEREZ GOMEZ' }),
    sticker({ cedula: '888', nombre: 'JUAN PEREZ GOMEZ' }),
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.get('999'), '111', 'la cédula de Ana sigue siendo de Ana');
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.get('222'), '333');
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.get('888'), '333', 'el alias propio del stub sí se reencamina');
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:111');
  assert.equal(professionalKeyOf(stickers[1], identity), 'ced:333');
  assert.equal(professionalKeyOf(stickers[2], identity), 'ced:333');
});

test('a malformed inspectores list degrades to an empty padrón, never throws', () => {
  for (const inspectores of [null, undefined, 'x', [null, undefined, {}]]) {
    const identity = buildIdentityIndex({
      stickers: [], surveys: [], depuracion: depuracionBlock(inspectores),
    });
    assert.equal(identity.profiles.size, 0);
  }
});

test('an absorbed stub never takes over a cédula the certified side owns, not even its OWN key', () => {
  // "222" is the stub's own identidad_key AND one of Ana's merged-away cédulas.
  // Ana is a certified person with a row: she keeps it.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA MARIA PEREZ', alias: ['222'] }),
    inspector({ cedula: '333', nombre: 'JUAN PEREZ GOMEZ TORRES' }),
    inspector({
      cedula: '222', nombre: 'JUAN PEREZ GOMEZ', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const stickers = [sticker({ cedula: '222', nombre: 'ANA MARIA PEREZ' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.get('222'), '111');
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:111');
});

test('an absorbed stub never re-points a name that already answers for another certified person', () => {
  // alias_nombres (the backend's own survey-name dedupe) points this spelling at
  // Ana, who HAS a row. The stub's absorption must not steal it.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA RUIZ TORRES' }),
    inspector({ cedula: '333', nombre: 'JUAN PEREZ GOMEZ TORRES' }),
    inspector({
      cedula: '222', nombre: 'JUAN PEREZ GOMEZ', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ], { 'juan perez gomez': '111' });
  const stickers = [sticker({ cedula: '222', nombre: 'JUAN PEREZ GOMEZ' })];
  const surveys = [survey({ nombre: 'JUAN PEREZ GOMEZ' })];
  const identity = buildIdentityIndex({ stickers, surveys, depuracion });
  // The stub IS absorbed by Juan — that part must still happen…
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:333');
  // …but the NAME still answers for Ana, exactly as the backend said.
  assert.equal(identity.nameToCedula.get('juan perez gomez'), '111');
  assert.equal(professionalKeyOf(surveys[0], identity), 'ced:111');
});

test('a NON-absorbed stub keeps its OWN cedulas_unificadas: one row, never two', () => {
  // The same shape main produced: every record of the stub lands in ONE row.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA PEREZ' }),
    inspector({
      cedula: '222', nombre: 'CUENTA SISTEMA MIGRADA', noPersona: true, estado: 'no_persona',
      codigo: '', alias: ['777'],
    }),
  ]);
  const stickers = [
    sticker({ cedula: '111', nombre: 'ANA PEREZ' }),
    sticker({ cedula: '222', nombre: 'CUENTA SISTEMA MIGRADA' }),
    sticker({ cedula: '777', nombre: 'CUENTA SISTEMA MIGRADA' }),
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[1], identity), 'ced:222');
  assert.equal(professionalKeyOf(stickers[2], identity), 'ced:222', 'el alias del stub no abre una segunda fila');
  const { rows, totals } = buildProfessionalRows({
    stickers, surveys: [], identity, today: '2026-09-19',
  });
  const conActividad = rows.filter((r) => r.total > 0);
  assert.equal(conActividad.length, 2, 'una fila de Ana y UNA sola del stub');
  assert.equal(totals.professionals, 2);
  assert.equal(totals.stickers, 3);
  const stub = conActividad.find((r) => r.key === 'ced:222');
  assert.equal(stub.stickersTotal, 2);
  assert.equal(stub.cedula, '222', 'la fila huérfana conserva la cédula de su clave');
});

test('a stub reached only through a Survey still shows the cédula of its resolved key', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA PEREZ' }),
    inspector({
      cedula: '222', nombre: 'CUENTA SISTEMA MIGRADA', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ], { 'cuenta sistema migrada': '222' });
  const surveys = [survey({ nombre: 'CUENTA SISTEMA MIGRADA' })];
  const identity = buildIdentityIndex({ stickers: [], surveys, depuracion });
  const { rows } = buildProfessionalRows({ stickers: [], surveys, identity, today: '2026-09-19' });
  const stub = rows.find((r) => r.total > 0);
  assert.equal(stub.key, 'ced:222');
  assert.equal(stub.cedula, '222');
  assert.equal(stub.name, 'Cuenta Sistema Migrada');
});

test('an orphan row falls back to the cédula of its own key when nothing else carries one', () => {
  // A registry row whose `identificacion` is blank but whose `identidad_key` is
  // not, reached only through a Survey (which never carries a cédula).
  const stub = {
    ...inspector({
      cedula: '222', nombre: 'CUENTA SISTEMA MIGRADA', noPersona: true, estado: 'no_persona', codigo: '',
    }),
    identificacion: '',
  };
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA PEREZ' }), stub,
  ], { 'cuenta sistema migrada': '222' });
  const surveys = [survey({ nombre: 'CUENTA SISTEMA MIGRADA' })];
  const identity = buildIdentityIndex({ stickers: [], surveys, depuracion });
  const { rows } = buildProfessionalRows({ stickers: [], surveys, identity, today: '2026-09-19' });
  const fila = rows.find((r) => r.total > 0);
  assert.equal(fila.key, 'ced:222');
  assert.equal(fila.cedula, '222');
  assert.equal(fila.name, 'Cuenta Sistema Migrada');
});

test('the manual-review panel still names the hidden no_persona half of a duplicate pair', () => {
  const depuracion = padronDelCasoReal();
  const items = [{ motivo: 'nombre_duplicado', identidad_keys: [CERT.cedula, STUB.cedula] }];
  const identity = buildIdentityIndex({ stickers: [], surveys: [], depuracion });
  const html = revisionManualHtml(items, { identity, isAdmin: true });
  assert.ok(html.includes('Juan David Hernandez Bueno'), 'la persona certificada');
  assert.ok(html.includes('Juan David Hernández'), 'y la mitad no_persona, por nombre y en Title Case');
  assert.ok(html.includes(`cédula ${STUB.cedula}`), 'con su cédula, como antes');
});

test('row.noPersona is retained for shape stability and is always false now', () => {
  const depuracion = padronDelCasoReal();
  const stickers = [sticker({ cedula: CERT.cedula, nombre: CERT.nombre })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  const { rows } = buildProfessionalRows({ stickers, surveys: [], identity, today: '2026-09-19' });
  for (const row of rows) {
    assert.equal(Object.prototype.hasOwnProperty.call(row, 'noPersona'), true, 'la clave sigue existiendo');
    assert.equal(row.noPersona, false, 'ninguna fila puede ser no_persona bajo D-NOPERSONA');
  }
});

test('the rows and every total are independent of the payload order', () => {
  const base = [
    inspector({ cedula: CERT.cedula, nombre: CERT.nombre, codigo: '048' }),
    inspector({
      cedula: STUB.cedula, nombre: STUB.nombre, noPersona: true, estado: 'no_persona',
      codigo: '', alias: ['1839622460'],
    }),
    inspector({ cedula: '111', nombre: 'ANA PEREZ' }),
    inspector({
      cedula: '999', nombre: 'CUENTA SISTEMA MIGRADA', noPersona: true, estado: 'no_persona',
      codigo: '', alias: ['888'],
    }),
  ];
  const stickers = [
    sticker({ cedula: CERT.cedula, nombre: CERT.nombre }),
    sticker({ cedula: STUB.cedula, nombre: STUB.nombre }),
    sticker({ cedula: '888', nombre: 'CUENTA SISTEMA MIGRADA' }),
    sticker({ cedula: '111', nombre: 'ANA PEREZ' }),
  ];
  const surveys = [survey({ nombre: 'Juan David Hernández' })];
  const permutaciones = [[0, 1, 2, 3], [3, 2, 1, 0], [1, 0, 3, 2], [2, 3, 0, 1], [1, 3, 0, 2]];
  let esperado = null;
  for (const orden of permutaciones) {
    const depuracion = depuracionBlock(
      orden.map((i) => base[i]), { 'juan david hernandez': STUB.cedula },
    );
    const identity = buildIdentityIndex({ stickers, surveys, depuracion });
    const { rows, totals } = buildProfessionalRows({
      stickers, surveys, identity, today: '2026-09-19',
    });
    const actual = JSON.stringify({
      totals,
      rows: rows.map((r) => [r.key, r.name, r.cedula, r.stickersTotal, r.surveyTotal]).sort(),
    });
    if (esperado === null) esperado = actual;
    else assert.equal(actual, esperado, `orden ${orden.join('')}`);
  }
});

test('D-VARIANTE: particles and initials do NOT count toward the 3-token threshold', () => {
  // Each stub below has only TWO significant tokens (or fewer): "de", "la",
  // "los" are particles and "J"/"D" are initials, so none of them identifies a
  // person well enough to absorb into the longer name.
  const casos = [
    ['MARIA DE LA CRUZ PEREZ', 'MARIA DE LA CRUZ'],
    ['ANA DE LEON GOMEZ', 'ANA DE LEON'],
    ['JOSE DE LOS SANTOS RIOS', 'JOSE DE LOS SANTOS'],
    ['J D HERNANDEZ BUENO', 'J D HERNANDEZ'],
  ];
  for (const [certificado, stub] of casos) {
    const depuracion = depuracionBlock([
      inspector({ cedula: '111', nombre: certificado }),
      inspector({ cedula: '222', nombre: stub, noPersona: true, estado: 'no_persona', codigo: '' }),
    ]);
    const stickers = [sticker({ cedula: '222', nombre: stub })];
    const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
    assert.equal(professionalKeyOf(stickers[0], identity), 'ced:222', `${stub} !-> ${certificado}`);
    assert.equal(identity.cedulaFusionadaACedulaSurvivor.has('222'), false, `${stub} !-> ${certificado}`);
  }
});

test('D-VARIANTE: a certified name with too few significant tokens never absorbs a longer stub', () => {
  // "Ana de Leon" is two significant tokens: it is not enough to claim that
  // "Ana de Leon Gomez" is the same human, in EITHER direction.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA DE LEON' }),
    inspector({
      cedula: '222', nombre: 'ANA DE LEON GOMEZ', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const stickers = [sticker({ cedula: '222', nombre: 'ANA DE LEON GOMEZ' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:222');
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.has('222'), false);
});

test('D-VARIANTE: a name WITH particles still merges once 3 significant tokens are there', () => {
  // "Ana de Leon Gomez" carries three significant tokens (ana/leon/gomez), so the
  // longer spelling of the same person is still absorbed.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA DE LEON GOMEZ' }),
    inspector({
      cedula: '222', nombre: 'ANA DE LEON GOMEZ TORRES', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const stickers = [sticker({ cedula: '222', nombre: 'ANA DE LEON GOMEZ TORRES' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:111');
});

test('an unabsorbed stub row keeps its registry NAME even when the record carries none', () => {
  // The stub is correctly NOT merged (its prefix fits two certified people), and
  // the sticker carries a cédula but no name: the row must still be identifiable.
  const depuracion = depuracionBlock([
    inspector({ cedula: '111', nombre: 'ANA MARIA GOMEZ RUIZ' }),
    inspector({ cedula: '222', nombre: 'ANA MARIA GOMEZ TORRES' }),
    inspector({
      cedula: '901', nombre: 'ANA MARIA GOMEZ', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const stickers = [sticker({ cedula: '901', nombre: '' })];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  const { rows } = buildProfessionalRows({ stickers, surveys: [], identity, today: '2026-09-19' });
  const fila = rows.find((r) => r.total > 0);
  assert.equal(fila.key, 'ced:901');
  assert.equal(fila.name, 'Ana Maria Gomez', 'el nombre del registro oculto, en Title Case');
  assert.equal(fila.cedula, '901');
  // …and it is still NOT a padrón row: no depurado columns, no estado, no flag.
  assert.equal(fila.estadoSugerido, '');
  assert.equal(fila.codigo, '');
  assert.equal(fila.profesion, undefined);
  assert.equal(fila.noPersona, false);
});

test('a cédula claimed by TWO different stub destinations is never re-pointed', () => {
  const inspectores = [
    inspector({ cedula: '111', nombre: 'ANA MARIA GOMEZ RUIZ' }),
    inspector({ cedula: '222', nombre: 'CARLOS ANDRES PEREZ SOTO' }),
    inspector({
      cedula: '901', nombre: 'ANA MARIA GOMEZ RUIZ', noPersona: true, estado: 'no_persona',
      codigo: '', alias: ['999'],
    }),
    inspector({
      cedula: '902', nombre: 'CARLOS ANDRES PEREZ SOTO', noPersona: true, estado: 'no_persona',
      codigo: '', alias: ['999'],
    }),
  ];
  const stickers = [sticker({ cedula: '999', nombre: '' })];
  const identity = buildIdentityIndex({
    stickers, surveys: [], depuracion: depuracionBlock(inspectores),
  });
  // Both stubs ARE absorbed (each is an exact spelling of one certified person)…
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.get('901'), '111');
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.get('902'), '222');
  // …but the cédula they BOTH claim belongs to neither.
  assert.equal(identity.cedulaFusionadaACedulaSurvivor.has('999'), false);
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:999');
});

test('a cédula claimed by two stubs resolves the same way under every payload order', () => {
  const base = [
    inspector({ cedula: '111', nombre: 'ANA MARIA GOMEZ RUIZ' }),
    inspector({ cedula: '222', nombre: 'CARLOS ANDRES PEREZ SOTO' }),
    inspector({
      cedula: '901', nombre: 'ANA MARIA GOMEZ RUIZ', noPersona: true, estado: 'no_persona',
      codigo: '', alias: ['999'],
    }),
    inspector({
      cedula: '902', nombre: 'CARLOS ANDRES PEREZ SOTO', noPersona: true, estado: 'no_persona',
      codigo: '', alias: ['999'],
    }),
  ];
  const stickers = [
    sticker({ cedula: '999', nombre: 'ANA MARIA GOMEZ RUIZ' }),
    sticker({ cedula: '111', nombre: 'ANA MARIA GOMEZ RUIZ' }),
    sticker({ cedula: '902', nombre: 'CARLOS ANDRES PEREZ SOTO' }),
  ];
  const ordenes = [];
  const permutar = (resto, hecho) => {
    if (!resto.length) { ordenes.push(hecho); return; }
    resto.forEach((x, i) => permutar([...resto.slice(0, i), ...resto.slice(i + 1)], [...hecho, x]));
  };
  permutar([0, 1, 2, 3], []);
  assert.equal(ordenes.length, 24);
  let esperado = null;
  for (const orden of ordenes) {
    const depuracion = depuracionBlock(orden.map((i) => base[i]));
    const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
    const { rows, totals } = buildProfessionalRows({
      stickers, surveys: [], identity, today: '2026-09-19',
    });
    const actual = JSON.stringify({
      totals,
      rows: rows.map((r) => [r.key, r.name, r.cedula, r.stickersTotal]).sort(),
    });
    if (esperado === null) esperado = actual;
    else assert.equal(actual, esperado, `orden ${orden.join('')}`);
  }
});

// ── legacy path: byte-identical ─────────────────────────────────────────────

test('without depuracion the legacy identity path is unchanged (names stay raw)', () => {
  const stickers = [
    sticker({ cedula: '111', nombre: 'JUAN DAVID HERNANDEZ BUENO' }),
    sticker({ cedula: '111', nombre: 'JUAN DAVID HERNANDEZ BUENO' }),
  ];
  for (const depuracion of [null, undefined, { activa: false, motivo: 'flag_off' }]) {
    const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
    const { rows, totals } = buildProfessionalRows({
      stickers, surveys: [], identity, today: '2026-09-19',
    });
    assert.equal(rows.length, 1);
    assert.equal(rows[0].name, 'JUAN DAVID HERNANDEZ BUENO', 'la ruta legacy no re-capitaliza');
    assert.equal(rows[0].profesion, undefined);
    assert.equal(totals.padron, undefined);
  }
});

test('a no_persona record on the legacy path is not filtered (no depurado data there)', () => {
  const stickers = [sticker({ cedula: '222', nombre: 'CUENTA SISTEMA' })];
  const { rows } = buildProfessionalRows({ stickers, surveys: [], today: '2026-09-19' });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].stickersTotal, 1);
});
