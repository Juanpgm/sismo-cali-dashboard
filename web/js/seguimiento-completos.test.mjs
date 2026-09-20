// D-COMPLETOS (2026-09-20): only people with COMPLETE data are visible in Seguimiento, and every
// professional-level metric is computed over exactly those people.
// Run: node --test web/js/seguimiento-completos.test.mjs
//
// The owner's rule, verbatim: "no quiero ver datos de personas que no estén completos, calcula
// todas las métricas, establece todo bien en Seguimiento".
//
//   VISIBLE   <=> the row resolves to a CERTIFIED depurado profile (non-`no_persona`, with the
//                 registry identity: nombre, estado sugerido, código, profesión…). A certified
//                 person with no activity in the range stays visible exactly where they were
//                 before (the padrón), which is what `totals.padron` counts.
//   HIDDEN    <=> an orphan row: activity that resolves to no certified profile, whether it is
//                 keyed by a `no_persona` stub's cédula or by a bare name. Those rows used to
//                 render with "Sin dato" in every identity column.
//
// Global ACTIVITY totals still count everything (no record is ever lost); professional-level
// metrics count only the visible people, and the gap is disclosed by one calm note.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildIdentityIndex, buildProfessionalRows, kpiTotals, ocultosNote, rowHasActivity,
  xlsxRowsFor, grupoExternosRowHtml, visibleRowsFor, DASH,
} from './seguimiento.js';

// ── fixtures ────────────────────────────────────────────────────────────────

function sticker({
  cedula = '', nombre = '', fecha = '2026-09-10T14:00:00+00:00', fase = 1, barrio = 'BARRIO',
} = {}) {
  return {
    fuente: 'atencionsismo',
    fecha,
    fase,
    inspector_fuente: 'api',
    // `barrio_reportado` is the field buildBarriosActivosByKey reads (the "barrios activos (7 d)" KPI).
    barrio_reportado: barrio,
    inspector: { identificacion: cedula, nombre_completo: nombre, np: 'P1', entidad: '' },
  };
}

function survey({ nombre = '', fecha = '2026-09-10' } = {}) {
  return {
    nombre_evaluador: nombre, fecha_inspeccion: fecha, entidad: '', id_grupo: '',
  };
}

function inspector({
  cedula, nombre, noPersona = false, estado = 'activo', codigo = '048',
} = {}) {
  return {
    identidad_key: cedula,
    identificacion: cedula,
    nombre_completo: nombre,
    cedulas_unificadas: [],
    codigo,
    entidad: '',
    np: 'P2',
    np_fuente: 'vercel',
    fase: 'Fase I',
    fase_np_faltante: false,
    estado_sugerido: estado,
    fuente_dato: 'main',
    tarjeta_profesional: '',
    enfasis: '',
    profesion: 'Ingeniero civil',
    num_telefono: '',
    correo_contacto: '',
    no_persona: noPersona,
    cedula_sospechosa: false,
    tiene_sticker_valido: true,
    dias_inactivo: null,
    ultimo_sticker: null,
  };
}

function depuracionBlock(inspectores, aliasNombres = {}, extra = {}) {
  return {
    activa: true,
    motivo: '',
    referencia_generada_en: '2026-09-20T00:00:00+00:00',
    inspectores,
    grupo_externos: null,
    alias_nombres: aliasNombres,
    revision_manual: [],
    ...extra,
  };
}

function build({ stickers = [], surveys = [], depuracion = null, from = null, to = null } = {}) {
  const identity = buildIdentityIndex({ stickers, surveys, depuracion });
  return {
    identity,
    ...buildProfessionalRows({
      stickers, surveys, identity, from, to, today: '2026-09-20',
    }),
  };
}

const CERT = { cedula: '1140000048', nombre: 'Juan David Hernandez Bueno' };

/** One certified person with activity, one `no_persona` stub with activity, one bare-name
 *  spelling with activity that resolves to nobody. The shape of the owner's screenshot. */
function escenarioMixto() {
  return {
    depuracion: depuracionBlock([
      inspector({ cedula: CERT.cedula, nombre: CERT.nombre, codigo: '048', estado: 'activo' }),
      inspector({
        cedula: '4710000000', nombre: 'Persona Importada Uno', noPersona: true, estado: 'no_persona', codigo: '',
      }),
    ]),
    // Dated inside the "barrios activos (7 d)" window of `today` = 2026-09-20, so that KPI is
    // genuinely exercised: the hidden person's own barrio must not reach it.
    stickers: [
      sticker({ cedula: CERT.cedula, nombre: CERT.nombre, fase: 1, barrio: 'SAN ANTONIO', fecha: '2026-09-18T14:00:00+00:00' }),
      sticker({ cedula: CERT.cedula, nombre: CERT.nombre, fase: 2, barrio: 'san antonio', fecha: '2026-09-18T15:00:00+00:00' }),
      sticker({ cedula: '4710000000', nombre: 'Persona Importada Uno', fase: 1, barrio: 'EL PEÑON', fecha: '2026-09-18T16:00:00+00:00' }),
    ],
    surveys: [
      survey({ nombre: CERT.nombre }),
      survey({ nombre: 'Persona Importada Uno' }),
      survey({ nombre: 'Alguien Que No Existe En El Padron' }),
      survey({ nombre: 'Alguien Que No Existe En El Padron' }),
    ],
  };
}

// ── visibility ──────────────────────────────────────────────────────────────

test('completos: an orphan name-only row with activity never reaches the table', () => {
  const { rows } = build(escenarioMixto());
  assert.deepEqual(rows.map((r) => r.key), [`ced:${CERT.cedula}`]);
  assert.ok(rows.every((r) => r.name && r.codigo && r.estadoSugerido),
    'every visible row carries the depurado identity');
});

test('completos: a no_persona stub row with activity never reaches the table either', () => {
  const { rows } = build(escenarioMixto());
  assert.ok(!rows.some((r) => r.key === 'ced:4710000000'));
});

test('completos: a certified person with NO activity stays visible (the padrón is unchanged)', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: CERT.cedula, nombre: CERT.nombre, codigo: '048' }),
    inspector({ cedula: '1140000049', nombre: 'Sin Actividad Alguna', codigo: '049' }),
  ]);
  const { rows, totals } = build({ depuracion, surveys: [survey({ nombre: CERT.nombre })] });
  assert.equal(rows.length, 2);
  assert.equal(totals.padron, 2);
  assert.equal(totals.professionals, 1, 'only the one WITH activity counts as a professional');
});

test('completos: the hidden activity is counted and reported, never silently dropped', () => {
  const { totals, ocultos } = build(escenarioMixto());
  assert.deepEqual(ocultos, { stickers: 1, surveys: 3, profesionales: 2 });
  assert.equal(totals.stickers, 3, 'the global sticker total still counts every sticker');
  assert.equal(totals.surveys, 4, 'the global Survey total still counts every record');
  assert.equal(totals.unassigned, 0);
});

test('completos: a person becomes visible the moment a certified alias resolves them', () => {
  const base = escenarioMixto();
  const sinAlias = build(base);
  assert.equal(sinAlias.ocultos.surveys, 3);
  // The backend's own survey alias now points the unresolved spelling at the certified person.
  const conAlias = build({
    ...base,
    depuracion: depuracionBlock(base.depuracion.inspectores, {
      'alguien que no existe en el padron': CERT.cedula,
    }),
  });
  assert.equal(conAlias.ocultos.surveys, 1, 'the two records moved into the certified row');
  assert.equal(conAlias.rows.find((r) => r.key === `ced:${CERT.cedula}`).surveyTotal, 3);
  assert.equal(conAlias.totals.surveys, 4, 'the global total did not move');
});

// ── metric consistency ──────────────────────────────────────────────────────

test('metricas: every activity record is accounted for exactly once (visible + oculto + sin dueño)', () => {
  const { rows, totals, ocultos, unassigned } = build(escenarioMixto());
  const visibleStickers = rows.reduce((n, r) => n + r.stickersTotal, 0);
  const visibleSurveys = rows.reduce((n, r) => n + r.surveyTotal, 0);
  assert.equal(visibleStickers + ocultos.stickers + unassigned.stickers, totals.stickers);
  assert.equal(visibleSurveys + ocultos.surveys + unassigned.surveys, totals.surveys);
});

test('metricas: professionals and avgPerProfessional match the rows the user can actually see', () => {
  const { rows, totals } = build(escenarioMixto());
  const conActividad = rows.filter(rowHasActivity);
  assert.equal(totals.professionals, conActividad.length);
  const suma = conActividad.reduce((n, r) => n + r.total, 0);
  assert.equal(totals.avgPerProfessional, Math.round((suma / conActividad.length) * 100) / 100);
});

test('metricas: the KPI tiles are computed over the visible people only', () => {
  const { rows, totals } = build(escenarioMixto());
  const kpis = kpiTotals({ rows, totals });
  assert.equal(kpis.professionals, 1);
  assert.equal(kpis.padron, 1, 'the padrón counts CERTIFIED profiles, so the stub is not in it');
  assert.equal(kpis.inspectoresActivos, 1);
  // "EL PEÑON" belongs to the hidden person and must not reach the barrios KPI.
  assert.equal(kpis.barriosActivos, 1);
  // Global activity tiles still show everything.
  assert.equal(kpis.stickers, 3);
  assert.equal(kpis.surveys, 4);
});

test('metricas: stickers/día por profesional averages the visible people only', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '1000000001', nombre: 'Visible Persona Una', codigo: '001' }),
  ]);
  const stickers = [
    sticker({ cedula: '1000000001', nombre: 'Visible Persona Una', fecha: '2026-09-10T14:00:00+00:00' }),
    sticker({ cedula: '1000000001', nombre: 'Visible Persona Una', fecha: '2026-09-10T15:00:00+00:00' }),
    // 6 stickers on ONE day for an invisible person would triple the average if counted.
    ...Array.from({ length: 6 }, () => sticker({ cedula: '', nombre: 'Fantasma Sin Padron Alguno' })),
  ];
  const { rows, totals } = build({ stickers, depuracion });
  assert.equal(kpiTotals({ rows, totals }).avgStickersPerDayPerProfessional, 2);
});

test('metricas: stickers/día por inspector activo never counts a hidden person', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '1000000001', nombre: 'Visible Persona Una', codigo: '001', estado: 'activo' }),
  ]);
  const stickers = [
    sticker({ cedula: '1000000001', nombre: 'Visible Persona Una' }),
    sticker({ cedula: '', nombre: 'Fantasma Sin Padron Alguno' }),
  ];
  const { totals } = build({
    stickers, depuracion, from: '2026-09-10', to: '2026-09-10',
  });
  assert.equal(totals.inspectoresActivos, 1);
  assert.equal(totals.stickersInspectoresActivos, 1);
  assert.equal(totals.rangoDias, 1);
  assert.equal(kpiTotals({ rows: [], totals }).stickersPorInspectorActivo, 1);
});

test('metricas: the XLSX sheets and the chart selector only ever see visible people', () => {
  const { rows } = build(escenarioMixto());
  for (const subTab of ['totales', 'temporales']) {
    const sheet = xlsxRowsFor(rows, { subTab, withEnfasis: true, withProfesion: true });
    assert.equal(sheet.length, 1, `${subTab}: only the visible professional is exported`);
    assert.equal(sheet[0].profesional, CERT.nombre);
  }
  assert.deepEqual(visibleRowsFor(rows, {}).map((r) => r.key), [`ced:${CERT.cedula}`]);
});

// ── the disclosure note ─────────────────────────────────────────────────────

test('nota: nothing hidden -> no note at all', () => {
  for (const v of [null, undefined, {}, { stickers: 0, surveys: 0, profesionales: 0 }]) {
    assert.equal(ocultosNote(v), null);
  }
});

test('nota: the note names the record counts, in Spanish, without naming anybody', () => {
  const texto = ocultosNote({ stickers: 1, surveys: 3, profesionales: 2 });
  assert.match(texto, /4 registros de actividad de personas sin datos completos no se listan/);
  assert.match(texto, /1 sticker\b/);
  assert.match(texto, /3 evaluaciones Survey/);
  assert.ok(!/[<>]/.test(texto), 'the note is plain text, it never carries markup');
});

test('nota: singular and plural are both correct', () => {
  assert.match(ocultosNote({ stickers: 1, surveys: 0 }), /^1 registro de actividad .* \(1 sticker\)\.$/);
  assert.match(ocultosNote({ stickers: 0, surveys: 1 }), /\(1 evaluación Survey\)\./);
  assert.match(ocultosNote({ stickers: 2, surveys: 0 }), /\(2 stickers\)\./);
});

test('nota: huge counts are grouped with the es-CO separator and never overflow', () => {
  const texto = ocultosNote({ stickers: 1234567, surveys: 7654321 });
  assert.match(texto, /8\.888\.888 registros/);
  assert.match(texto, /1\.234\.567 stickers/);
  assert.match(texto, /7\.654\.321 evaluaciones Survey/);
});

test('nota: a hostile name among the hidden people never reaches any surface', () => {
  const hostil = '<img src=x onerror=alert(1)>Fulano';
  const depuracion = depuracionBlock([inspector({ cedula: '1000000001', nombre: 'Visible Persona Una', codigo: '001' })]);
  const { rows, ocultos } = build({
    depuracion,
    surveys: [survey({ nombre: hostil }), survey({ nombre: 'Visible Persona Una' })],
  });
  assert.equal(ocultos.surveys, 1);
  assert.ok(!rows.some((r) => (r.name || '').includes('onerror')));
  assert.ok(!ocultosNote(ocultos).includes('onerror'));
});

// ── edge cases ──────────────────────────────────────────────────────────────

test('borde: an EMPTY depuración hides everything and still reports honest zeros', () => {
  const { rows, totals, ocultos } = build({
    depuracion: depuracionBlock([]),
    surveys: [survey({ nombre: 'Alguien Sin Padron Alguno' })],
    stickers: [sticker({ cedula: '', nombre: 'Alguien Sin Padron Alguno' })],
  });
  assert.deepEqual(rows, []);
  assert.equal(totals.padron, 0);
  assert.equal(totals.professionals, 0);
  assert.equal(totals.avgPerProfessional, 0, 'never NaN when there is nobody to divide by');
  assert.equal(totals.stickers, 1);
  assert.equal(totals.surveys, 1);
  assert.deepEqual(ocultos, { stickers: 1, surveys: 1, profesionales: 1 });
  const kpis = kpiTotals({ rows, totals });
  assert.equal(kpis.professionals, 0);
  // Review round 4: there IS a sticker, it just belongs to nobody we can show — "0 barrios" would
  // be a confirmed zero the data does not support, so the tile says "not available".
  assert.equal(kpis.barriosActivos, DASH);
  assert.equal(kpis.padron, 0);
  assert.equal(kpis.inspectoresActivos, undefined, 'no padrón to classify -> no tile at all');
});

test('borde: with NOBODY incomplete the note disappears and nothing is hidden', () => {
  const depuracion = depuracionBlock([inspector({ cedula: CERT.cedula, nombre: CERT.nombre, codigo: '048' })]);
  const { rows, ocultos, totals } = build({
    depuracion, surveys: [survey({ nombre: CERT.nombre })], stickers: [sticker({ cedula: CERT.cedula, nombre: CERT.nombre })],
  });
  assert.equal(rows.length, 1);
  assert.deepEqual(ocultos, { stickers: 0, surveys: 0, profesionales: 0 });
  assert.equal(ocultosNote(ocultos), null);
  assert.equal(totals.professionals, 1);
});

test('borde: the LEGACY path (no depuración, or activa:false) is byte-identical to before', () => {
  const stickers = [sticker({ cedula: '999', nombre: 'Alguien Crudo' }), sticker({ cedula: '', nombre: 'Otro Crudo' })];
  const surveys = [survey({ nombre: 'Tercero Crudo' })];
  const esperado = JSON.stringify(buildProfessionalRows({
    stickers, surveys, identity: buildIdentityIndex({ stickers, surveys }), today: '2026-09-20',
  }));
  for (const depuracion of [null, undefined, { activa: false, motivo: 'flag off' }, {}]) {
    const resultado = build({ stickers, surveys, depuracion });
    const { identity, ...sinIdentity } = resultado;
    assert.equal(JSON.stringify(sinIdentity), esperado, 'the legacy result shape and values are untouched');
    assert.equal(sinIdentity.ocultos, undefined, 'the legacy path has no completeness concept at all');
    assert.equal(sinIdentity.rows.length, 3, 'everything stays visible on the legacy path');
  }
});

test('borde: a huge dataset stays consistent and does not blow the arithmetic', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '1000000001', nombre: 'Visible Persona Una', codigo: '001' }),
  ]);
  const stickers = [];
  const surveys = [];
  for (let i = 0; i < 20000; i += 1) {
    stickers.push(sticker({ cedula: i % 2 ? '1000000001' : '', nombre: i % 2 ? 'Visible Persona Una' : `Fantasma Numero ${i} Apellido` }));
    surveys.push(survey({ nombre: i % 2 ? 'Visible Persona Una' : `Fantasma Numero ${i} Apellido` }));
  }
  const { rows, totals, ocultos, unassigned } = build({ stickers, surveys, depuracion });
  assert.equal(rows.length, 1);
  assert.equal(totals.stickers, 20000);
  assert.equal(totals.surveys, 20000);
  assert.equal(
    rows.reduce((n, r) => n + r.stickersTotal, 0) + ocultos.stickers + unassigned.stickers,
    totals.stickers,
  );
  assert.equal(
    rows.reduce((n, r) => n + r.surveyTotal, 0) + ocultos.surveys + unassigned.surveys,
    totals.surveys,
  );
  assert.equal(ocultos.profesionales, 10000);
});

test('externos: the collapsed group keeps its count but stops listing the people', () => {
  const grupo = {
    n_colapsados: 2,
    fuente_dato: 'main',
    detalle: [
      { nombre_completo: 'Cuenta Importada Una', identificacion: '123', motivo: 'cuenta_no_persona' },
      { nombre_completo: '<b>hostil</b>', identificacion: '456', motivo: 'cedula_sospechosa' },
    ],
  };
  const conDetalle = grupoExternosRowHtml(grupo, 5);
  assert.match(conDetalle, /Cuenta Importada Una/, 'the legacy call still lists them');
  const sinDetalle = grupoExternosRowHtml(grupo, 5, { conDetalle: false });
  assert.match(sinDetalle, /Externos agrupados \(2\)/, 'the accepted aggregate count stays');
  assert.ok(!sinDetalle.includes('Cuenta Importada Una'), 'no incomplete person is named');
  assert.ok(!sinDetalle.includes('123'), 'no cédula of an incomplete person is shown');
  assert.ok(!sinDetalle.includes('seg-externos-toggle'), 'and there is nothing left to expand');
  assert.ok(!sinDetalle.includes('<b>hostil</b>'));
});

test('completos: DASH masking still applies to every sticker-derived tile while loading', () => {
  const { rows, totals } = build(escenarioMixto());
  const kpis = kpiTotals({ rows, totals }, { stickersLoaded: false });
  for (const key of ['professionals', 'stickers', 'avgStickersPerDayPerProfessional', 'barriosActivos', 'padron']) {
    assert.equal(kpis[key], DASH, `${key} stays masked`);
  }
  assert.equal(kpis.surveys, 4, 'the Survey tile is never sticker-derived');
});

test('borde: the legacy path never consults the profile map, whatever identity it is handed', () => {
  // Robustness, and the guard that keeps the legacy path legacy: several helpers in this module
  // build an identity from a NARROWER dataset (`buildBarriosActivosByKey` uses `surveys: []`).
  // If the completeness filter ran without the depuración gate, handing such an identity to
  // buildProfessionalRows would silently empty the table.
  const stickers = [sticker({ cedula: '999', nombre: 'Alguien Crudo' })];
  const surveys = [survey({ nombre: 'Tercero Crudo' })];
  const identityParcial = buildIdentityIndex({ stickers: [], surveys: [] });
  assert.equal(identityParcial.profiles.size, 0);
  const { rows, totals } = buildProfessionalRows({
    stickers, surveys, identity: identityParcial, today: '2026-09-20',
  });
  assert.equal(rows.length, 2, 'both records still produce their row on the legacy path');
  assert.equal(totals.professionals, 2);
  assert.equal(totals.padron, undefined);
});

// ── Review round 4 (2026-09-20) ─────────────────────────────────────────────

test('completo: a CERTIFIED profile with blank registry fields is NOT complete', () => {
  // Repro: the padrón row exists and is not `no_persona`, but it carries no nombre, no estado and
  // no cédula — it rendered as a visible row with "Sin dato" in every identity column, which is
  // exactly what the owner asked never to see again.
  const depuracion = depuracionBlock([
    {
      identidad_key: '111', identificacion: '111', nombre_completo: '', codigo: '',
      estado_sugerido: '', no_persona: false, cedulas_unificadas: [],
    },
  ]);
  const { rows, totals, ocultos } = build({
    depuracion, stickers: [sticker({ cedula: '111', nombre: '' })],
  });
  assert.deepEqual(rows, []);
  assert.equal(totals.padron, 0, 'an incomplete profile is not in the padrón either');
  assert.equal(totals.professionals, 0);
  assert.equal(totals.stickers, 1, 'its activity is still counted globally');
  assert.deepEqual(ocultos, { stickers: 1, surveys: 0, profesionales: 1 });
});

test('completo: whitespace-only registry fields do not make a person complete', () => {
  for (const campos of [
    { nombre_completo: '   ', identificacion: '111', estado_sugerido: 'activo' },
    { nombre_completo: 'Ana Maria Gomez', identificacion: '   ', estado_sugerido: 'activo' },
    { nombre_completo: 'Ana Maria Gomez', identificacion: '111', estado_sugerido: '  ' },
  ]) {
    const depuracion = depuracionBlock([{
      identidad_key: '111', codigo: '048', no_persona: false, cedulas_unificadas: [], ...campos,
    }]);
    const { rows, totals } = build({ depuracion, stickers: [sticker({ cedula: '111', nombre: 'Ana Maria Gomez' })] });
    assert.deepEqual(rows, [], `blank ${JSON.stringify(campos)} must not be visible`);
    assert.equal(totals.padron, 0);
  }
});

test('completo: nombre + cédula + estado are enough — código stays OPTIONAL', () => {
  // A certified inspector may legitimately have no código; that must not hide them.
  const depuracion = depuracionBlock([{
    identidad_key: '111', identificacion: '111', nombre_completo: 'Ana Maria Gomez',
    codigo: '', estado_sugerido: 'revisar', no_persona: false, cedulas_unificadas: [],
  }]);
  const { rows, totals } = build({ depuracion, stickers: [sticker({ cedula: '111', nombre: 'Ana Maria Gomez' })] });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, 'Ana Maria Gomez');
  assert.equal(rows[0].codigo, '', 'no código, and that is fine');
  assert.equal(totals.padron, 1);
  assert.equal(totals.professionals, 1);
});

test('completo: the active-inspector KPIs count only COMPLETE people', () => {
  const depuracion = depuracionBlock([
    inspector({ cedula: '1000000001', nombre: 'Completa Persona Una', codigo: '001', estado: 'activo' }),
    // Certified and `activo`, but with no nombre: incomplete, so it may not inflate any KPI.
    {
      identidad_key: '1000000002', identificacion: '1000000002', nombre_completo: '',
      codigo: '002', estado_sugerido: 'activo', no_persona: false, cedulas_unificadas: [],
    },
  ]);
  const stickers = [
    sticker({ cedula: '1000000001', nombre: 'Completa Persona Una' }),
    sticker({ cedula: '1000000002', nombre: '' }),
  ];
  const { rows, totals } = build({
    depuracion, stickers, from: '2026-09-10', to: '2026-09-10',
  });
  assert.equal(rows.length, 1);
  assert.equal(totals.padron, 1);
  assert.equal(totals.inspectoresActivos, 1);
  assert.equal(totals.stickersInspectoresActivos, 1, 'the incomplete active profile adds nothing');
  assert.equal(kpiTotals({ rows, totals }).inspectoresActivos, 1);
});

test('nota: Survey records with no evaluator are disclosed too, so the KPI reconciles on screen', () => {
  const depuracion = depuracionBlock([inspector({ cedula: '111', nombre: 'Ana Maria Gomez', codigo: '048' })]);
  const surveys = [
    survey({ nombre: 'Ana Maria Gomez' }), survey({ nombre: '' }), survey({ nombre: '   ' }),
  ];
  const { rows, totals, ocultos, unassigned } = build({ depuracion, surveys });
  assert.equal(totals.surveys, 3);
  assert.equal(rows.reduce((n, r) => n + r.surveyTotal, 0), 1, '1 visible');
  assert.equal(unassigned.surveys, 2, '2 with no evaluator at all');
  const texto = ocultosNote(ocultos, { encuestasSinEvaluador: unassigned.surveys });
  assert.ok(texto, 'the note exists even when only the no-evaluator half has something to say');
  assert.match(texto, /2 encuestas sin evaluador identificado/);
  // 3 = 1 visible + 2 disclosed: the tile can be reconciled with what is on screen.
});

test('nota: the no-evaluator clause composes with the incomplete-people clause', () => {
  const texto = ocultosNote({ stickers: 42, surveys: 976 }, { encuestasSinEvaluador: 7 });
  assert.match(texto, /1\.018 registros de actividad de personas sin datos completos no se listan \(42 stickers, 976 evaluaciones Survey\)/);
  assert.match(texto, /y 7 encuestas sin evaluador identificado\.$/);
  assert.equal(ocultosNote({ stickers: 0, surveys: 0 }, { encuestasSinEvaluador: 1 }),
    '1 encuesta sin evaluador identificado no se lista.');
  assert.equal(ocultosNote({ stickers: 0, surveys: 0 }, { encuestasSinEvaluador: 0 }), null);
  assert.equal(ocultosNote({ stickers: 0, surveys: 0 }), null, 'the second argument is optional');
});

test('kpi: with activity but NO visible professional, the pace and barrios tiles are unavailable', () => {
  // Repro: the padrón has one person with no activity; the 9 stickers in the range all belong to
  // somebody the padrón does not know. A "0" here would claim a confirmed zero pace; the true
  // state is "not available from the people we can show".
  const depuracion = depuracionBlock([inspector({ cedula: '111', nombre: 'Ana Maria Gomez', codigo: '048' })]);
  const stickers = Array.from({ length: 9 }, (_, i) => sticker({
    cedula: '', nombre: 'Fantasma Sin Padron Alguno', barrio: 'SAN ANTONIO', fecha: `2026-09-1${8 + (i % 2)}T1${i % 9}:00:00+00:00`,
  }));
  const { rows, totals } = build({ depuracion, stickers });
  assert.equal(totals.professionals, 0);
  const kpis = kpiTotals({ rows, totals });
  assert.equal(kpis.avgStickersPerDayPerProfessional, DASH);
  assert.equal(kpis.barriosActivos, DASH);
  assert.equal(kpis.stickers, 9, 'the global activity tile still states the truth');
});

test('kpi: a genuinely empty dataset still reports a real 0, never DASH', () => {
  const { rows, totals } = build({ depuracion: depuracionBlock([]) });
  const kpis = kpiTotals({ rows, totals });
  assert.equal(kpis.avgStickersPerDayPerProfessional, 0);
  assert.equal(kpis.barriosActivos, 0);
  assert.equal(kpis.stickers, 0);
});

test('kpi: an EMPTY depuración with activity marks the pace tile unavailable, not zero', () => {
  const { rows, totals } = build({
    depuracion: depuracionBlock([]),
    stickers: [sticker({ cedula: '', nombre: 'Fantasma Sin Padron Alguno', barrio: 'SAN ANTONIO' })],
  });
  const kpis = kpiTotals({ rows, totals });
  assert.equal(kpis.avgStickersPerDayPerProfessional, DASH);
  assert.equal(kpis.barriosActivos, DASH);
  assert.equal(kpis.padron, 0);
});
