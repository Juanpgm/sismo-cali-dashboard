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
  normalizeName, __statsOrfanos,
} from './seguimiento.js';

// ── fixtures ────────────────────────────────────────────────────────────────

/** A sticker as `/stickers-atencionsismo` serves it. */
function sticker({
  cedula = '', nombre = '', fecha = '2026-09-10T14:00:00+00:00', fase = 1,
  fuente = 'api', tarjeta = '', barrio = 'BARRIO', entidad = '',
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
      identificacion: cedula, nombre_completo: nombre, tarjeta_profesional: tarjeta, np: 'P1', entidad,
    },
  };
}

/** A Survey (`survey_cali`) record — never carries a cédula. `grupo`/`entidad` are the two
 *  fields D-SUBSECUENCIA's corroboration guard reads (`id_grupo`, `entidad`). */
function survey({
  nombre = '', fecha = '2026-09-10', grupo = '', entidad = '',
} = {}) {
  return {
    nombre_evaluador: nombre, fecha_inspeccion: fecha, entidad, id_grupo: grupo,
  };
}

/** One `depuracion.inspectores` record. */
function inspector({
  cedula, nombre, noPersona = false, estado = 'activo', codigo = '048',
  alias = [], profesion = '', tarjeta = '', enfasis = '', entidad = '',
} = {}) {
  return {
    identidad_key: cedula,
    identificacion: cedula,
    nombre_completo: nombre,
    cedulas_unificadas: alias,
    codigo,
    entidad,
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
  const { rows, totals } = filasCompletas({
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
  const { rows } = filasCompletas({ stickers, surveys: [], identity, today: '2026-09-19' });
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
  const { rows, totals } = filasCompletas({
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
  const { rows, totals } = filasCompletas({
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
  const { rows } = filasCompletas({ stickers, surveys: [], identity, today: '2026-09-19' });
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
  const { rows } = filasCompletas({ stickers, surveys: [], identity, today: '2026-09-19' });
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
  const { rows, totals } = filasCompletas({
    stickers, surveys: [], identity, today: '2026-09-19',
  });
  const conActividad = rows.filter((r) => r.total > 0);
  assert.equal(conActividad.length, 2, 'una fila de Ana y UNA sola del stub');
  // D-COMPLETOS: el stub no es una persona completa, así que no entra en la métrica de
  // profesionales (su actividad sigue contada en los totales globales y se reporta aparte).
  assert.equal(totals.professionals, 1);
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
  const { rows } = filasCompletas({ stickers: [], surveys, identity, today: '2026-09-19' });
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
  const { rows } = filasCompletas({ stickers: [], surveys, identity, today: '2026-09-19' });
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
  const { rows } = filasCompletas({ stickers, surveys: [], identity, today: '2026-09-19' });
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
    const { rows, totals } = filasCompletas({
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
  const { rows } = filasCompletas({ stickers, surveys: [], identity, today: '2026-09-19' });
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
    const { rows, totals } = filasCompletas({
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
    const { rows, totals } = filasCompletas({
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
  const { rows } = filasCompletas({ stickers, surveys: [], today: '2026-09-19' });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].stickersTotal, 1);
});

// ── D-SUBSECUENCIA (2026-09-20): orphan spellings resolve to the certified person ──
//
// The gap D-VARIANTE left open: it only ever compared TRUE PREFIXES with 3+ significant
// tokens on the shorter side, so an inserted first name ("Nicole Tello Segura" vs "Jane
// Nicole Tello Segura"), a two-token spelling ("Jairo Lopez" vs "Jairo Giovanny Lopez
// Puentes") and every free-text Survey spelling that never reached the registry at all
// stayed in their own "Sin dato" rows. The rule below is the in-order-subset generalisation,
// with a corroboration guard on the two-token case. Typos are NEVER matched (see the
// "one letter apart" tests): this rule is exact-token-only by design.

/** The padrón + records of the production "Jairo Lopez" case (masked cédulas). */
const SUB_CERT = { cedula: '1010000024', nombre: 'Carlos Arturo Guerrero Quezada' };

function depuracionCon(inspectores, alias = {}) {
  return depuracionBlock(inspectores, alias);
}

/** D-COMPLETOS (2026-09-20): `buildProfessionalRows` plus the rows it now hides, so the resolver
 *  assertions in this file keep reading the COMPLETE set (see `filasDe`). A no-op on the legacy
 *  path, where nothing is ever hidden. */
function filasCompletas(args) {
  const resultado = buildProfessionalRows(args);
  return { ...resultado, rows: [...resultado.rows, ...(resultado.rowsOcultas || [])] };
}

/** D-COMPLETOS (2026-09-20): the tests in this file pin the IDENTITY RESOLVER — WHICH key a record
 *  lands on, and that no activity is ever split or lost — which `rows` used to show directly. The
 *  table now hides every row without a certified profile, so `rows` here is the COMPLETE set
 *  (visible + hidden): the resolver contract is unchanged, only what the user sees is. Tests that
 *  care about visibility itself live in `seguimiento-completos.test.mjs`. */
function filasDe({ stickers = [], surveys = [], depuracion }) {
  const identity = buildIdentityIndex({ stickers, surveys, depuracion });
  return { identity, ...filasCompletas({ stickers, surveys, identity }) };
}

test('subsecuencia: a Survey spelling contained IN ORDER in ONE certified name lands in that row', () => {
  const depuracion = depuracionCon([inspector({ cedula: SUB_CERT.cedula, nombre: SUB_CERT.nombre, codigo: '024' })]);
  const surveys = [survey({ nombre: 'Carlos Arturo Guerrero' }), survey({ nombre: '  carlos arturo GUERRERO  ' })];
  const { rows, totals } = filasDe({ surveys, depuracion });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].key, `ced:${SUB_CERT.cedula}`);
  assert.equal(rows[0].surveyTotal, 2);
  assert.equal(rows[0].name, 'Carlos Arturo Guerrero Quezada');
  assert.equal(totals.padron, 1);
  assert.equal(totals.surveys, 2);
});

test('subsecuencia: both directions — an orphan spelling carrying an EXTRA token merges too', () => {
  const cedula = '1080000107';
  const depuracion = depuracionCon([inspector({ cedula, nombre: 'Jhonatan Lozano Bastidas', codigo: '107' })]);
  const surveys = [survey({ nombre: 'Jhonatan A. Lozano Bastidas' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), [`ced:${cedula}`]);
  assert.equal(rows[0].surveyTotal, 1);
});

test('subsecuencia: an INSERTED first name resolves (the case the TRUE-prefix rule could not see)', () => {
  const cedula = '1150000078';
  const depuracion = depuracionCon([inspector({ cedula, nombre: 'JANE NICOLE TELLO SEGURA', codigo: '078' })]);
  const surveys = [survey({ nombre: 'Nicole Tello Segura' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), [`ced:${cedula}`]);
});

test('subsecuencia: TWO certified candidates are never merged — each keeps its own row', () => {
  // The production "Carlos Ospina" case: the registry carries the same human twice, once with a
  // rank prefix. A frontend rule must not pick; both certified rows stay, the spelling stays apart.
  const depuracion = depuracionCon([
    inspector({ cedula: '1670000145', nombre: 'Carlos Emilio Ospina Monsalve', codigo: '145' }),
    inspector({ cedula: '1570000000', nombre: 'Cn (Ra) Carlos Emilio Ospina Monsalve', codigo: '' }),
  ]);
  const surveys = [survey({ nombre: 'Carlos Emilio Ospina' })];
  const { rows } = filasDe({ surveys, depuracion });
  const conActividad = rows.filter((r) => r.total > 0);
  assert.deepEqual(conActividad.map((r) => r.key), ['nom:carlos emilio ospina']);
  assert.equal(rows.length, 3);
});

test('subsecuencia: a certified name that is a subset of ANOTHER certified name poisons the spelling', () => {
  const depuracion = depuracionCon([
    inspector({ cedula: '1000000001', nombre: 'Ana Maria Torres', codigo: '001' }),
    inspector({ cedula: '1000000002', nombre: 'Ana Maria Torres Lopez', codigo: '002' }),
  ]);
  const surveys = [survey({ nombre: 'Ana Maria Torres Lopez Rojas' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.equal(rows.find((r) => r.total > 0).key, 'nom:ana maria torres lopez rojas');
});

test('subsecuencia: fewer than 2 SIGNIFICANT tokens on the shorter side is never absorbed', () => {
  const depuracion = depuracionCon([
    inspector({ cedula: '1000000003', nombre: 'Maria de la Cruz', codigo: '003' }),
    inspector({ cedula: '1000000004', nombre: 'Juan David Hernandez Bueno', codigo: '004' }),
  ]);
  // "de la Cruz" -> 1 significant token (particles never count); "Juan" -> 1.
  const surveys = [survey({ nombre: 'de la Cruz' }), survey({ nombre: 'Juan' })];
  const { rows } = filasDe({ surveys, depuracion });
  const conActividad = rows.filter((r) => r.total > 0).map((r) => r.key).sort();
  assert.deepEqual(conActividad, ['nom:de la cruz', 'nom:juan']);
});

test('subsecuencia: a 2-significant-token spelling WITHOUT corroboration stays in its own row', () => {
  const cedula = '1670000025';
  const depuracion = depuracionCon([
    inspector({ cedula, nombre: 'Fernando Alberto Padilla Ramirez', codigo: '025', entidad: 'SGRED' }),
  ]);
  const surveys = [survey({ nombre: 'Fernando Padilla' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.equal(rows.find((r) => r.total > 0).key, 'nom:fernando padilla');
  assert.equal(rows.length, 2);
});

test('subsecuencia: a 2-significant-token spelling WITH the same entidad is absorbed', () => {
  const cedula = '1670000025';
  const depuracion = depuracionCon([
    inspector({ cedula, nombre: 'Fernando Alberto Padilla Ramirez', codigo: '025', entidad: 'SGRED' }),
  ]);
  // Entidad is compared normalized: casing, accents and padding never split it.
  const surveys = [survey({ nombre: 'Fernando Padilla', entidad: ' sgred ' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), [`ced:${cedula}`]);
});

test('subsecuencia: a shared id_grupo is NEVER corroboration (it is a 5-value bucket, not a team)', () => {
  // Measured on the 2026-09-20 snapshot: `id_grupo` takes 5 distinct values over 1,909 Survey
  // records ("otro" 715, "alcadia_ugr" 635, "amva" 374, blank 180, "NA" 5). Two strangers sharing
  // one is worth nothing, so it is not evidence and the spelling stays in its own row.
  const cedula = '1670000025';
  const depuracion = depuracionCon([
    inspector({ cedula, nombre: 'Fernando Alberto Padilla Ramirez', codigo: '025' }),
  ]);
  const surveys = [
    survey({ nombre: 'Fernando Alberto Padilla Ramirez', grupo: 'otro' }),
    survey({ nombre: 'Fernando Padilla', grupo: 'otro' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.equal(rows.find((r) => r.total > 0 && r.key.startsWith('nom:')).key, 'nom:fernando padilla');
});

test('subsecuencia: an entidad carried by a LARGE share of the records is not discriminating', () => {
  const cedula = '1670000025';
  const depuracion = depuracionCon([
    inspector({ cedula, nombre: 'Fernando Alberto Padilla Ramirez', codigo: '025', entidad: 'SGRED' }),
  ]);
  // "SGRED" here is what almost every record carries — a team label, not a link between two people.
  const surveys = [survey({ nombre: 'Fernando Padilla', entidad: 'SGRED' })];
  for (let i = 0; i < 12; i += 1) surveys.push(survey({ nombre: `Persona Numero ${i} Apellido`, entidad: 'SGRED' }));
  const { rows } = filasDe({ surveys, depuracion });
  assert.ok(rows.some((r) => r.key === 'nom:fernando padilla' && r.surveyTotal === 1));
});

test('subsecuencia: a 2-significant-token spelling whose only shared context is a BLANK field is not absorbed', () => {
  const cedula = '1670000025';
  const depuracion = depuracionCon([
    inspector({ cedula, nombre: 'Fernando Alberto Padilla Ramirez', codigo: '025', entidad: '   ' }),
  ]);
  const surveys = [
    survey({ nombre: 'Fernando Alberto Padilla Ramirez', grupo: '', entidad: '' }),
    survey({ nombre: 'Fernando Padilla', grupo: '', entidad: '  ' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.equal(rows.find((r) => r.key.startsWith('nom:')).key, 'nom:fernando padilla');
});

test('subsecuencia: tokens OUT OF ORDER never merge', () => {
  const depuracion = depuracionCon([inspector({ cedula: '1000000005', nombre: 'Juan Perez Gomez', codigo: '005' })]);
  const surveys = [survey({ nombre: 'Gomez Juan Perez' }), survey({ nombre: 'Perez Juan' })];
  const { rows } = filasDe({ surveys, depuracion });
  const conActividad = rows.filter((r) => r.total > 0).map((r) => r.key).sort();
  assert.deepEqual(conActividad, ['nom:gomez juan perez', 'nom:perez juan']);
});

test('subsecuencia: names that only SHARE a prefix and then diverge never merge', () => {
  const depuracion = depuracionCon([
    inspector({ cedula: '1000000006', nombre: 'Juan Carlos Ramirez Torres', codigo: '006' }),
  ]);
  const surveys = [survey({ nombre: 'Juan Carlos Gomez' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.equal(rows.find((r) => r.total > 0).key, 'nom:juan carlos gomez');
});

test('subsecuencia: a ONE-LETTER typo is never matched (typos stay out of scope, always)', () => {
  const depuracion = depuracionCon([
    inspector({ cedula: '1020000000', nombre: 'Juan Camilo Haya Castaño', codigo: '' }),
    inspector({ cedula: '8000000022', nombre: 'Tito Alexis Monzón Leyva', codigo: '022' }),
  ]);
  const surveys = [survey({ nombre: 'Juan Camilo Aya Castaño' }), survey({ nombre: 'Tito Alexis Monzón Leiva' })];
  const { rows } = filasDe({ surveys, depuracion });
  const conActividad = rows.filter((r) => r.total > 0).map((r) => r.key).sort();
  assert.deepEqual(conActividad, ['nom:juan camilo aya castano', 'nom:tito alexis monzon leiva']);
});

test('subsecuencia: an alias_nombres entry answering for a certified person WITH a row is never stolen', () => {
  const depuracion = depuracionCon([
    inspector({ cedula: '1000000007', nombre: 'Ana Maria Torres Lopez', codigo: '007' }),
    inspector({ cedula: '1000000008', nombre: 'Beatriz Solano Diaz', codigo: '008' }),
  ], { 'ana maria torres': '1000000008' });
  const surveys = [survey({ nombre: 'Ana Maria Torres' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.equal(rows.find((r) => r.total > 0).key, 'ced:1000000008');
});

test('subsecuencia: an alias_nombres entry pointing at a ROWLESS no_persona stub IS overridden', () => {
  // The production "Jairo Lopez" case: the backend alias pins 39 Survey records on a stub key
  // that has no row, so the table showed a "Sin dato" row instead of the certified inspector.
  const cert = '1010000033';
  const stub = '2870000000';
  const depuracion = depuracionCon([
    inspector({ cedula: cert, nombre: 'Jairo Giovanny Lopez Puentes', codigo: '033', entidad: 'EDRU' }),
    inspector({
      cedula: stub, nombre: 'Jairo Lopez', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ], { 'jairo lopez': stub, 'jairo giovanny lopez puentes': cert });
  const surveys = [survey({ nombre: 'Jairo Lopez', entidad: 'EDRU' }), survey({ nombre: 'Jairo lopez ', entidad: 'EDRU' })];
  const { rows, totals } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), [`ced:${cert}`]);
  assert.equal(rows[0].surveyTotal, 2);
  assert.equal(rows[0].codigo, '033');
  assert.equal(totals.padron, 1);
});

test('subsecuencia: a no_persona stub WITH a código is never absorbed, even with a unique candidate', () => {
  const cert = '1010000033';
  const stub = '2870000000';
  const depuracion = depuracionCon([
    inspector({ cedula: cert, nombre: 'Jairo Giovanny Lopez Puentes', codigo: '033', entidad: 'EDRU' }),
    inspector({
      cedula: stub, nombre: 'Jairo Lopez', noPersona: true, estado: 'no_persona', codigo: '099',
    }),
  ], { 'jairo lopez': stub });
  const surveys = [survey({ nombre: 'Jairo Lopez', entidad: 'EDRU' })];
  const { rows } = filasDe({ surveys, depuracion });
  const orfana = rows.find((r) => r.key === `ced:${stub}`);
  assert.ok(orfana, 'the stub with a código keeps its own orphan row');
  assert.equal(orfana.surveyTotal, 1);
  assert.equal(orfana.codigo, '', 'an orphan row never carries a depurado column');
});

test('subsecuencia: an absorbed stub re-points its OWN cédula and its cedulas_unificadas in ONE hop', () => {
  const cert = '7940000063';
  const stub = '2520000000';
  const fusionada = '9990000000';
  const depuracion = depuracionCon([
    inspector({ cedula: cert, nombre: 'Evelio Castaño Arango', codigo: '063', entidad: 'Voluntarios' }),
    inspector({
      cedula: stub, nombre: 'Evelio Castaño', noPersona: true, estado: 'no_persona', codigo: '',
      alias: [fusionada],
    }),
  ]);
  const stickers = [
    sticker({ cedula: stub, nombre: 'Evelio Castaño' }),
    sticker({ cedula: fusionada, nombre: 'Evelio Castaño' }),
    sticker({ cedula: cert, nombre: 'Evelio Castaño Arango' }),
  ];
  const surveys = [survey({ nombre: 'Evelio Castaño', entidad: 'Voluntarios' })];
  const { rows, totals } = filasDe({ stickers, surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), [`ced:${cert}`]);
  assert.equal(rows[0].stickersTotal, 3);
  assert.equal(rows[0].surveyTotal, 1);
  assert.equal(totals.unassigned, 0);
});

test('subsecuencia: no record is lost or double counted, and the padrón totals never move', () => {
  const cert = '1010000024';
  const depuracion = depuracionCon([
    inspector({ cedula: cert, nombre: 'Carlos Arturo Guerrero Quezada', codigo: '024' }),
    inspector({ cedula: '1010000099', nombre: 'Otra Persona Certificada', codigo: '099' }),
  ]);
  const stickers = [sticker({ cedula: cert, nombre: 'Carlos Arturo Guerrero Quezada' })];
  const surveys = [survey({ nombre: 'Carlos Arturo Guerrero' }), survey({ nombre: 'Nadie Conocido Aqui' })];
  const sinRegla = filasCompletas({
    stickers, surveys, identity: buildIdentityIndex({ stickers, surveys }),
  });
  const { rows, totals } = filasDe({ stickers, surveys, depuracion });
  assert.equal(totals.stickers, sinRegla.totals.stickers);
  assert.equal(totals.surveys, sinRegla.totals.surveys);
  assert.equal(totals.unassigned, 0);
  assert.equal(totals.stickersWithoutDate, 0);
  // The padrón figures count PROFILES, so a merge of RECORDS can never move them.
  assert.equal(totals.padron, 2);
  assert.equal(totals.inspectoresActivos, 2);
  // No record is lost and none is counted twice.
  assert.equal(rows.reduce((n, r) => n + r.stickersTotal, 0), 1);
  assert.equal(rows.reduce((n, r) => n + r.surveyTotal, 0), 2);
});

test('KPI: a name-only sticker re-attributed to an ACTIVE inspector DOES move stickersInspectoresActivos', () => {
  // This KPI counts the stickers of profiles whose estado is exactly "activo", so re-attributing a
  // name-only sticker to such a person MUST move it — that is the correction the owner asked for,
  // not a regression. `inspectoresActivos` counts PROFILES and still cannot move.
  const depuracion = depuracionCon([
    inspector({ cedula: '1110000010', nombre: 'Camila Andrea Rojas Vargas', codigo: '010', estado: 'activo' }),
  ]);
  const stickers = [
    sticker({ cedula: '', nombre: 'Camila Andrea Rojas' }),
    sticker({ cedula: '', nombre: 'Camila Andrea Rojas' }),
  ];
  const conRegla = filasDe({ stickers, depuracion });
  assert.deepEqual(conRegla.rows.filter((r) => r.total > 0).map((r) => r.key), ['ced:1110000010']);
  assert.equal(conRegla.totals.stickersInspectoresActivos, 2);
  assert.equal(conRegla.totals.inspectoresActivos, 1);
  assert.equal(conRegla.totals.stickers, 2);
  assert.equal(conRegla.totals.unassigned, 0);
  // Stickers per active inspector: both stickers now belong to the one active profile.
  assert.equal(conRegla.totals.stickersInspectoresActivos / conRegla.totals.inspectoresActivos, 2);
});

test('KPI: a name-only sticker carries the merge — its own entidad is the corroboration', () => {
  // Kills "contextoDeNombres ignores stickers": the ONLY evidence for this two-token merge is the
  // entidad on the sticker's own inspector block.
  const cedula = '1670000025';
  const depuracion = depuracionCon([
    inspector({ cedula, nombre: 'Fernando Alberto Padilla Ramirez', codigo: '025', entidad: 'Acme Ingenieria SAS' }),
  ]);
  const stickers = [sticker({ cedula: '', nombre: 'Fernando Padilla', entidad: 'Acme Ingenieria SAS' })];
  const { rows, totals } = filasDe({ stickers, depuracion });
  assert.deepEqual(rows.filter((r) => r.total > 0).map((r) => r.key), [`ced:${cedula}`]);
  assert.equal(rows.find((r) => r.key === `ced:${cedula}`).stickersTotal, 1);
  assert.equal(totals.unassigned, 0);
});

test('subsecuencia: the result is independent of the order of inspectores, stickers and surveys', () => {
  const inspectores = [
    inspector({ cedula: '1010000024', nombre: 'Carlos Arturo Guerrero Quezada', codigo: '024' }),
    inspector({ cedula: '1150000078', nombre: 'JANE NICOLE TELLO SEGURA', codigo: '078' }),
    inspector({
      cedula: '2870000000', nombre: 'Jairo Lopez', noPersona: true, estado: 'no_persona', codigo: '',
    }),
    inspector({ cedula: '1010000033', nombre: 'Jairo Giovanny Lopez Puentes', codigo: '033', entidad: 'EDRU' }),
  ];
  const surveys = [
    survey({ nombre: 'Carlos Arturo Guerrero' }),
    survey({ nombre: 'Nicole Tello Segura' }),
    survey({ nombre: 'Jairo Lopez', entidad: 'EDRU' }),
    survey({ nombre: 'Persona Sin Registro Alguno' }),
  ];
  const huella = (orden, ordenSurveys) => {
    const depuracion = depuracionCon(orden, { 'jairo lopez': '2870000000' });
    const { rows, totals } = filasDe({ surveys: ordenSurveys, depuracion });
    return JSON.stringify({
      filas: rows.map((r) => [r.key, r.name, r.surveyTotal]).sort(), totals,
    });
  };
  const esperado = huella(inspectores, surveys);
  const permutaciones = [
    [...inspectores].reverse(),
    [inspectores[2], inspectores[0], inspectores[3], inspectores[1]],
    [inspectores[3], inspectores[2], inspectores[1], inspectores[0]],
    [inspectores[1], inspectores[3], inspectores[0], inspectores[2]],
  ];
  for (const orden of permutaciones) {
    assert.equal(huella(orden, [...surveys].reverse()), esperado);
    assert.equal(huella(orden, surveys), esperado);
  }
});

test('subsecuencia: the raw depuracion payload and the raw records are never mutated', () => {
  const depuracion = depuracionCon([
    inspector({ cedula: '1010000024', nombre: 'Carlos Arturo Guerrero Quezada', codigo: '024' }),
    inspector({
      cedula: '2870000000', nombre: 'Jairo Lopez', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ], { 'jairo lopez': '2870000000' });
  const surveys = [survey({ nombre: 'Carlos Arturo Guerrero' })];
  const stickers = [sticker({ cedula: '2870000000', nombre: 'Jairo Lopez' })];
  const antes = JSON.stringify({ depuracion, surveys, stickers });
  filasDe({ stickers, surveys, depuracion });
  assert.equal(JSON.stringify({ depuracion, surveys, stickers }), antes);
});

test('subsecuencia: the legacy path (no depuracion / activa:false) never merges by subsequence', () => {
  const surveys = [survey({ nombre: 'Carlos Arturo Guerrero' }), survey({ nombre: 'Carlos Arturo Guerrero Quezada' })];
  for (const depuracion of [null, undefined, { activa: false, motivo: 'flag off' }]) {
    const { rows, totals } = filasDe({ surveys, depuracion });
    assert.deepEqual(rows.map((r) => r.key).sort(), ['nom:carlos arturo guerrero', 'nom:carlos arturo guerrero quezada']);
    assert.equal(totals.padron, undefined);
  }
});

test('subsecuencia: malformed records and a malformed padrón never throw and never merge', () => {
  const depuracion = depuracionCon([
    inspector({ cedula: '1010000024', nombre: 'Carlos Arturo Guerrero Quezada', codigo: '024' }),
    null,
    { identidad_key: '', nombre_completo: 'Sin Cedula Alguna' },
    { identidad_key: '1010000077', nombre_completo: null, no_persona: true },
  ]);
  const surveys = [null, survey({ nombre: '' }), survey({ nombre: '   ' }), survey({ nombre: 'Carlos Arturo Guerrero' })];
  const stickers = [null, { fuente: 'atencionsismo', fecha: '2026-09-10T14:00:00+00:00', fase: 1 }];
  const { rows, totals } = filasDe({ stickers, surveys, depuracion });
  assert.equal(rows.find((r) => r.total > 0).key, 'ced:1010000024');
  assert.equal(totals.unassigned, 3);
  const malformado = buildIdentityIndex({ stickers: [], surveys: [], depuracion: { activa: true, inspectores: 'nope' } });
  assert.equal(malformado.profiles.size, 0);
});

// ── D-ORFANO-CANONICO (2026-09-20): duplicate spellings of the SAME orphan ──
//
// Rule A only ever aims at a CERTIFIED person. Everything it leaves behind is still split
// across spellings of one unregistered human. Two steps close that, with the same guards:
// (i) a spelling that is an in-order variant of exactly one unabsorbed `no_persona` stub
// lands in that stub's cédula-keyed row; (ii) a purely name-keyed group collapses onto its
// FULLEST spelling, which is also the name the row displays.

test('canonico: a spelling that varies exactly ONE unabsorbed stub lands in the stub row', () => {
  const stub = '5840000000';
  const depuracion = depuracionCon([
    inspector({
      cedula: stub, nombre: 'Jenny Alejandra Marín Diosa', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const surveys = [
    survey({ nombre: 'Jenny Alejandra Marín Diosa' }),
    survey({ nombre: 'Alejandra Marín Diosa' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), [`ced:${stub}`]);
  assert.equal(rows[0].surveyTotal, 2);
  assert.equal(rows[0].name, 'Jenny Alejandra Marín Diosa');
  assert.equal(rows[0].estadoSugerido, '', 'an orphan row never gains a depurado column');
});

test('canonico: a spelling that varies TWO stubs stays in its own row', () => {
  const depuracion = depuracionCon([
    inspector({
      cedula: '5840000000', nombre: 'Jenny Alejandra Marín Diosa', noPersona: true, estado: 'no_persona', codigo: '',
    }),
    inspector({
      cedula: '3210000000', nombre: 'Alejandra Marín', noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ]);
  const surveys = [survey({ nombre: 'Jenny Alejandra Marín' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), ['nom:jenny alejandra marin']);
});

test('canonico: a purely name-keyed group collapses onto its FULLEST spelling', () => {
  const depuracion = depuracionCon([]);
  const surveys = [
    survey({ nombre: 'Juan Camilo Aya' }),
    survey({ nombre: 'JUAN CAMILO AYA CASTAÑO' }),
    survey({ nombre: 'Juan Camilo Aya Castaño ' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), ['nom:juan camilo aya castano']);
  assert.equal(rows[0].surveyTotal, 3);
  assert.equal(rows[0].name, 'Juan Camilo Aya Castaño');
  assert.equal(rows[0].cedula, '');
});

test('canonico: a chain of spellings compresses to ONE row in a single hop', () => {
  const depuracion = depuracionCon([]);
  const surveys = [
    survey({ nombre: 'Juan Camilo Aya' }),
    survey({ nombre: 'Juan Camilo Aya Castaño' }),
    survey({ nombre: 'Juan Camilo Aya Castaño Lopez' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), ['nom:juan camilo aya castano lopez']);
  assert.equal(rows[0].surveyTotal, 3);
});

test('canonico: a spelling with TWO longer candidates is never guessed', () => {
  const depuracion = depuracionCon([]);
  const surveys = [
    survey({ nombre: 'Adan Duran' }),
    survey({ nombre: 'Adan Duran Yomayusa' }),
    survey({ nombre: 'Adan Duran Perez' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key).sort(), ['nom:adan duran', 'nom:adan duran perez', 'nom:adan duran yomayusa']);
});

test('canonico: a 2-significant-token spelling merges when it is a true PREFIX of exactly one longer one', () => {
  const depuracion = depuracionCon([]);
  const surveys = [survey({ nombre: 'Robbinson Villalobos' }), survey({ nombre: 'Robbinson Villalobos Reyes' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(rows.map((r) => r.key), ['nom:robbinson villalobos reyes']);
  assert.equal(rows[0].name, 'Robbinson Villalobos Reyes');
});

test('canonico: a 2-significant-token NON-prefix spelling needs corroboration', () => {
  const depuracion = depuracionCon([]);
  const sinContexto = [survey({ nombre: 'Norha Cuellar' }), survey({ nombre: 'Luz Norha Cuellar' })];
  assert.deepEqual(
    filasDe({ surveys: sinContexto, depuracion }).rows.map((r) => r.key).sort(),
    ['nom:luz norha cuellar', 'nom:norha cuellar'],
  );
  const conContexto = [
    survey({ nombre: 'Norha Cuellar', entidad: 'Acme Siete' }),
    survey({ nombre: 'Luz Norha Cuellar', entidad: 'Acme Siete' }),
  ];
  assert.deepEqual(filasDe({ surveys: conContexto, depuracion }).rows.map((r) => r.key), ['nom:luz norha cuellar']);
});

test('canonico: a spelling already resolved to a CERTIFIED person is never re-pointed at an orphan', () => {
  const cedula = '1010000024';
  const depuracion = depuracionCon([inspector({ cedula, nombre: 'Carlos Arturo Guerrero', codigo: '024' })]);
  const surveys = [survey({ nombre: 'Carlos Arturo Guerrero' }), survey({ nombre: 'Carlos Arturo Guerrero Quezada' })];
  const { rows } = filasDe({ surveys, depuracion });
  const certificada = rows.find((r) => r.key === `ced:${cedula}`);
  assert.ok(certificada && certificada.surveyTotal >= 1);
  assert.equal(rows.filter((r) => r.total > 0).length, 1, 'the longer spelling joins the certified row, not the other way round');
});

test('canonico: single-token and sub-threshold spellings never collapse into a longer one', () => {
  const depuracion = depuracionCon([]);
  const surveys = [
    survey({ nombre: 'Carlos' }),
    survey({ nombre: 'Carlos Rivera Mora' }),
    survey({ nombre: 'de la Cruz' }),
    survey({ nombre: 'Maria de la Cruz Perez' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.equal(rows.length, 4);
});

test('canonico: the collapse is order independent and conserves every record', () => {
  const depuracion = depuracionCon([]);
  const surveys = [
    survey({ nombre: 'Juan Camilo Aya' }),
    survey({ nombre: 'Juan Camilo Aya Castaño' }),
    survey({ nombre: 'Robbinson Villalobos Reyes' }),
    survey({ nombre: 'Robbinson Villalobos' }),
  ];
  const huella = (orden) => {
    const { rows, totals } = filasDe({ surveys: orden, depuracion });
    return JSON.stringify({ filas: rows.map((r) => [r.key, r.name, r.surveyTotal]).sort(), totals });
  };
  const esperado = huella(surveys);
  assert.equal(huella([...surveys].reverse()), esperado);
  assert.equal(huella([surveys[2], surveys[0], surveys[3], surveys[1]]), esperado);
  const { rows, totals } = filasDe({ surveys, depuracion });
  assert.equal(rows.reduce((n, r) => n + r.surveyTotal, 0), 4);
  assert.equal(totals.surveys, 4);
  assert.equal(totals.unassigned, 0);
});

test('subsecuencia: a cell naming TWO people ("A / B") is never credited to either of them', () => {
  // Real registry artefact: `no_persona` stubs whose `nombre_completo` lists a pair. A slash is
  // never part of one human's name, so the whole spelling is out of every merge, as source and
  // as target — the records stay in their own orphan row instead of being credited to one half.
  const depuracion = depuracionCon([
    inspector({ cedula: '6680000042', nombre: 'ANA MILENA MEJIA SALGADO', codigo: '042', entidad: 'EDRU' }),
    inspector({
      cedula: '4330000000', nombre: 'Leonardo Lenis Palomino / Ana Milena Mejía Salgado',
      noPersona: true, estado: 'no_persona', codigo: '',
    }),
  ], { 'leonardo lenis palomino / ana milena mejia salgado': '4330000000' });
  const surveys = [survey({ nombre: 'Leonardo Lenis Palomino / Ana Milena Mejía Salgado', entidad: 'EDRU' })];
  const { rows } = filasDe({ surveys, depuracion });
  const conActividad = rows.filter((r) => r.total > 0);
  assert.deepEqual(conActividad.map((r) => r.key), ['ced:4330000000']);
  assert.equal(conActividad[0].codigo, '');
});

test('canonico: a joint "A / B" spelling is never collapsed into a single-person orphan either', () => {
  const depuracion = depuracionCon([]);
  const surveys = [
    survey({ nombre: 'Camilo Castro / Tulio Rivera' }),
    survey({ nombre: 'Camilo Castro' }),
    survey({ nombre: 'Camilo Castro Rivera' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.ok(rows.some((r) => r.key === 'nom:camilo castro / tulio rivera'));
  assert.equal(rows.length, 2, 'the two single-person spellings still collapse between themselves');
});

test('subsecuencia: SIGNIFICANT tokens, not raw ones, gate the merge — even WITH corroboration', () => {
  // The guard that stops "Maria de la Cruz" from swallowing "de la Cruz" and "J D Hernandez Bueno"
  // from swallowing "J D Hernandez": both shorter sides have 3 RAW tokens but only ONE that
  // identifies a person, so no amount of shared entidad may merge them.
  const depuracion = depuracionCon([
    inspector({ cedula: '1000000011', nombre: 'Maria de la Cruz Perez', codigo: '011', entidad: 'SGRED' }),
    inspector({ cedula: '1000000012', nombre: 'J D Hernandez Bueno', codigo: '012', entidad: 'SGRED' }),
  ]);
  const surveys = [
    survey({ nombre: 'de la Cruz', entidad: 'SGRED', grupo: 'G-1' }),
    survey({ nombre: 'J D Hernandez', entidad: 'SGRED', grupo: 'G-1' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  const conActividad = rows.filter((r) => r.total > 0).map((r) => r.key).sort();
  assert.deepEqual(conActividad, ['nom:de la cruz', 'nom:j d hernandez']);
});

// ── Review round 3 (2026-09-20): initials, multi-person cells, pass-4 cost ──

test('subsecuencia: an initial written WITH a period is still not a significant token', () => {
  // "j." and "d." are one letter plus punctuation: spelling, not identity. Counting them would let
  // "J. D. Hernandez" be absorbed by "J. D. Hernandez Bueno" — a different human entirely.
  const depuracion = depuracionCon([
    inspector({ cedula: '1000000021', nombre: 'J. D. Hernandez Bueno', codigo: '021', entidad: 'Acme Uno' }),
  ]);
  const surveys = [survey({ nombre: 'J. D. Hernandez', entidad: 'Acme Uno' })];
  const { rows } = filasDe({ surveys, depuracion });
  assert.equal(rows.find((r) => r.total > 0).key, 'nom:j. d. hernandez');
});

test('subsecuencia: a name with ONE initial still carries its other two significant tokens', () => {
  // "Fernando A. Padilla" is 2 significant tokens (the "A." does not count), so it merges only
  // WITH a discriminating entidad — never on the name alone.
  const cedula = '1000000022';
  const padron = [inspector({ cedula, nombre: 'Fernando A. Padilla Ramirez', codigo: '022', entidad: 'Acme Dos' })];
  const sinEntidad = filasDe({ surveys: [survey({ nombre: 'Fernando A. Padilla' })], depuracion: depuracionCon(padron) });
  assert.equal(sinEntidad.rows.find((r) => r.total > 0).key, 'nom:fernando a. padilla');
  const conEntidad = filasDe({
    surveys: [survey({ nombre: 'Fernando A. Padilla', entidad: 'Acme Dos' })], depuracion: depuracionCon(padron),
  });
  assert.deepEqual(conEntidad.rows.filter((r) => r.total > 0).map((r) => r.key), [`ced:${cedula}`]);
});

test('varias personas: a CERTIFIED row naming several people is never a merge TARGET either', () => {
  // Real registry value. Before this guard it was a legal destination, so two different humans'
  // records were credited to one padrón row.
  const depuracion = depuracionCon([
    inspector({
      cedula: '3330000000', nombre: 'Walter Vasquez, Arq Samuel Jiménez, Arq Yeini Roa, Arq Luisa Quiñones',
      codigo: '333', entidad: 'Acme Tres',
    }),
  ]);
  const surveys = [
    survey({ nombre: 'Walter Vasquez Samuel Jimenez', entidad: 'Acme Tres' }),
    survey({ nombre: 'Samuel Jimenez Yeini Roa', entidad: 'Acme Tres' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  const conActividad = rows.filter((r) => r.total > 0).map((r) => r.key).sort();
  assert.deepEqual(conActividad, ['nom:samuel jimenez yeini roa', 'nom:walter vasquez samuel jimenez']);
});

test('varias personas: " y " between two multi-token names is two people, as source AND as target', () => {
  const conjunta = 'Leonardo Lenis Palomino y Ana Milena Mejia Salgado';
  // (a) as a SOURCE: the Survey cell must not be credited to the certified half.
  const comoOrigen = filasDe({
    surveys: [survey({ nombre: conjunta, entidad: 'Acme Cuatro' })],
    depuracion: depuracionCon([
      inspector({ cedula: '6680000042', nombre: 'Ana Milena Mejia Salgado', codigo: '042', entidad: 'Acme Cuatro' }),
    ]),
  });
  assert.equal(comoOrigen.rows.find((r) => r.total > 0).key, `nom:${normalizeName(conjunta)}`);
  // (b) as a TARGET: a single person's spelling must not be absorbed by the joint padrón row.
  const comoDestino = filasDe({
    surveys: [survey({ nombre: 'Ana Milena Mejia Salgado', entidad: 'Acme Cuatro' })],
    depuracion: depuracionCon([
      inspector({ cedula: '4330000001', nombre: `${conjunta} Rojas`, codigo: '433', entidad: 'Acme Cuatro' }),
    ]),
  });
  assert.equal(comoDestino.rows.find((r) => r.total > 0).key, 'nom:ana milena mejia salgado');
});

test('varias personas: commas and the other separators mark a cell as several people', () => {
  const depuracion = depuracionCon([
    inspector({ cedula: '1000000031', nombre: 'Camilo Castro Rivera', codigo: '031', entidad: 'Acme Cinco' }),
    inspector({ cedula: '1000000032', nombre: 'Luz Maritza Romero Castro', codigo: '032', entidad: 'Acme Cinco' }),
  ]);
  const surveys = [
    survey({ nombre: 'Camilo castro, Tulio Rivera', entidad: 'Acme Cinco' }),
    survey({ nombre: 'Luz, Maritza, Romero, Castro.', entidad: 'Acme Cinco' }),
    survey({ nombre: 'Ana Perez & Juan Gomez', entidad: 'Acme Cinco' }),
    survey({ nombre: 'Ana Perez + Juan Gomez', entidad: 'Acme Cinco' }),
    survey({ nombre: 'Ana Perez; Juan Gomez', entidad: 'Acme Cinco' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.equal(rows.filter((r) => r.total > 0).length, 5, 'each joint cell keeps its own row');
  assert.ok(rows.every((r) => !r.key.startsWith('ced:') || r.total === 0));
});

test('varias personas: a lone particle, and a " y " with a one-token side, are NOT two people', () => {
  // Never over-exclude: "Maria de los Angeles" is one human, and "Ortiz y Pino" is a surname pair
  // inside ONE name. Known and accepted miss on the same side of the line: "Mari Sol y Julián"
  // really is two people but has a one-token right side, so it is treated as a single name.
  const depuracion = depuracionCon([
    inspector({ cedula: '1000000041', nombre: 'Maria de los Angeles Perez', codigo: '041', entidad: 'Acme Seis' }),
    inspector({ cedula: '1000000042', nombre: 'Ortiz y Pino Ramirez Solano', codigo: '042', entidad: 'Acme Seis' }),
  ]);
  const surveys = [
    survey({ nombre: 'Maria de los Angeles', entidad: 'Acme Seis' }),
    survey({ nombre: 'Ortiz y Pino Ramirez', entidad: 'Acme Seis' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  assert.deepEqual(
    rows.filter((r) => r.total > 0).map((r) => r.key).sort(),
    ['ced:1000000041', 'ced:1000000042'],
  );
});

test('perf: pass 4 stays linear when thousands of orphan spellings share their leading tokens', () => {
  // Pathological shape from the reviewer's perf2.mjs: N spellings that all begin "Juan Carlos".
  // Indexing by the query's RAREST token keeps every lookup to a one- or two-element bucket.
  const N = 4000;
  const surveys = [];
  for (let i = 0; i < N; i += 1) surveys.push(survey({ nombre: `Juan Carlos Ape${i}` }));
  surveys.push(survey({ nombre: 'Juan Carlos Ape7 Lopez' }));
  const inicio = Date.now();
  const identity = buildIdentityIndex({ stickers: [], surveys, depuracion: depuracionCon([]) });
  const ms = Date.now() - inicio;
  const stats = __statsOrfanos();
  // Instrumented, not wall-clock: the bound is what makes the test non-flaky. A quadratic scan
  // would be ~N*N/2 = 8,000,000 comparisons here.
  assert.ok(stats.comparacionesPase4 <= 4 * N,
    `pass 4 made ${stats.comparacionesPase4} comparisons for ${N} spellings (bound ${4 * N})`);
  assert.ok(ms < 300, `buildIdentityIndex took ${ms} ms for ${N} shared-prefix spellings`);
  // Correctness is not traded away: the one real variant still collapses.
  const { rows } = filasCompletas({ stickers: [], surveys, identity });
  assert.ok(rows.some((r) => r.key === 'nom:juan carlos ape7 lopez' && r.surveyTotal === 2));
  assert.equal(rows.length, N);
});

test('varias personas: "&" and "+" separate two people even when the tokens would line up', () => {
  // Unlike a comma (which sticks to the preceding token and breaks the match by itself), "&" and
  // "+" are standalone tokens, so WITHOUT this guard "Ana Perez & Juan Gomez" is a clean in-order
  // superset of the certified "Ana Perez Gomez" and would be credited to her.
  const depuracion = depuracionCon([
    inspector({ cedula: '1000000051', nombre: 'Ana Perez Gomez', codigo: '051', entidad: 'Acme Ocho' }),
  ]);
  const surveys = [
    survey({ nombre: 'Ana Perez & Juan Gomez', entidad: 'Acme Ocho' }),
    survey({ nombre: 'Ana Perez + Juan Gomez', entidad: 'Acme Ocho' }),
  ];
  const { rows } = filasDe({ surveys, depuracion });
  const conActividad = rows.filter((r) => r.total > 0).map((r) => r.key).sort();
  assert.deepEqual(conActividad, ['nom:ana perez & juan gomez', 'nom:ana perez + juan gomez']);
});
