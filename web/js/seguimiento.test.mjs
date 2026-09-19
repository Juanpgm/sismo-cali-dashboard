// Self-check for the pure aggregation helpers behind the Seguimiento tab.
// Run: node web/js/seguimiento.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  normalizeName, cedulaKey, professionalKeyOf, buildIdentityIndex,
  buildProfessionalRows, buildTimeline, sortRows,
  professionalRecords, buildTemporalMetrics, buildTemporalMetricsByKey,
  buildBarriosActivos, buildBarriosActivosByKey,
  buildProfessionalReportDocDefinition, hasActiveSegFilters,
  createSegCache, createIdentityCache, makeSearchController,
  DASH, DEGRADED_STICKERS_NOTE, reportSelectedButtonState,
  kpiTotals, matchesSearch, visibleRowsFor, degradedStickerNote, unassignedNote, kpisHtml,
  timelineChartConfig, timelineDataKey, shouldSkipChartRender,
  COLUMNS_TOTALES, COLUMNS_TEMPORALES, columnsFor, defaultSortFor, formatMinutes, xlsxRowsFor, xlsxFiltersSummary,
  objetivoDiario, visitasUltimos7Dias, reportFilenameSlug, buildMassReportDocDefinition,
  cellHtml, rowReportButtonsBlocked, canStartMassExport, shouldDeliverExport,
  professionalRecordsByKey, visitasUltimos7DiasByKey, massExportOverlayText,
  // W11: report visual redesign — colored classification badges, KPI stat
  // cards, section-header rule, and the new activity sparkline.
  badgeStyleFor, badgeCell, statCard, statCardsRow, sectionHeaderNode, buildActivitySparkline,
  // Fase 4 (seguimiento-inspectores-depurado): GRUPO-EXTERNOS expandable
  // row, the "Revisión manual" section, and the depuracion freshness badge.
  grupoExternosRowHtml, revisionManualHtml, depuracionBadgeHtml,
} from './seguimiento.js';
import * as SEG from './seguimiento.js';
import { COLORS } from './utils.js';

// Small test-local helper: the "no identity/no cédula at all" key shape a
// name-only record resolves to — every existing single-professional fixture
// below (no `identificacion`/`cedula`) uses this instead of the bare
// normalizeName() output the OLD row.key/professionalKey used to be (D7,
// plan §Decisiones: join by cédula first, name key is now PREFIXED so it can
// never collide with a 'ced:...' key).
const nomKey = (name) => `nom:${normalizeName(name)}`;

// ── normalizeName ──────────────────────────────────────────────────────────

assert.equal(normalizeName('Juan Pérez'), 'juan perez');
assert.equal(normalizeName('  Juan   Pérez  '), 'juan perez');
assert.equal(normalizeName('JUAN PEREZ'), 'juan perez');
assert.equal(normalizeName('juan perez'), normalizeName('JUAN   PÉREZ'));
assert.equal(normalizeName(null), '');
assert.equal(normalizeName(undefined), '');
assert.equal(normalizeName(''), '');
assert.equal(normalizeName('   '), '');
console.log('normalizeName OK');

// ── cedulaKey ───────────────────────────────────────────────────────────────
// Mirrors backend's cedula_key (stickers_atencionsismo.py:220) EXACTLY:
// digits-only, no leading-zero stripping (decision: kept verbatim, same as
// the backend, which only strips non-digits and does nothing else).

assert.equal(cedulaKey('1.234.567'), '1234567');
assert.equal(cedulaKey(' 1234567 '), '1234567');
assert.equal(cedulaKey('1234567'), '1234567');
assert.equal(cedulaKey(1234567), '1234567');
assert.equal(cedulaKey('CC'), '');
assert.equal(cedulaKey(''), '');
assert.equal(cedulaKey(null), '');
assert.equal(cedulaKey(undefined), '');
// Leading-zero policy: digits kept verbatim, never stripped -- "0123" and
// "123" are DIFFERENT keys (a cédula legitimately starting with 0 must not
// collide with one that doesn't).
assert.equal(cedulaKey('0123'), '0123');
assert.notEqual(cedulaKey('0123'), cedulaKey('123'));
// Float artifact (review 2026-09-19 C3, design D-CEDDEC): ONLY a lone ".0" at the
// end of an otherwise digit-only string is dropped (a float cell stringified);
// every other dot — "166.000", "12.000", "12345.00" — is a thousands separator
// or noise and is stripped like any non-digit. Identical to the backend
// `cedula_utils.COLA_FLOTANTE_PATRON`.
assert.equal(cedulaKey('1234567.0'), '1234567');
assert.equal(cedulaKey(' 1234567.0 '), '1234567');
assert.equal(cedulaKey(1234567.0), '1234567');
assert.equal(cedulaKey('0.0'), '0');
assert.equal(cedulaKey('166.000'), '166000');
assert.equal(cedulaKey('12.000'), '12000');
assert.equal(cedulaKey('1.234.567'), '1234567');
assert.equal(cedulaKey('12345.00'), '1234500');
assert.equal(cedulaKey('1234567.0.0'), '123456700');
assert.equal(cedulaKey('12345.0a'), '123450');
assert.equal(cedulaKey('١٢٣٤٥٦٧'), '');
console.log('cedulaKey OK');

// ── buildIdentityIndex / professionalKeyOf: the single identity resolver ──
// D7 (plan §Decisiones): join by cédula first; unify name -> cédula ONLY
// when the name maps to exactly one eligible cédula across the whole
// sticker set; a cédula seen ONLY via inspector_fuente === 'roster' is never
// an eligible merge key (misattribution risk, stickers_atencionsismo.py:20-27).

{
  // No identity at all (default) -> every record resolves to its own
  // 'nom:normalizedName' bucket, exactly the old normalizeName-based
  // behavior except for the 'nom:' prefix (never collides with 'ced:').
  const sticker = { inspector: { nombre_completo: 'Gil Soto' } };
  assert.equal(professionalKeyOf(sticker), nomKey('Gil Soto'));
  assert.equal(professionalKeyOf(sticker, undefined), nomKey('Gil Soto'));
  const survey = { nombre_evaluador: 'Gil Soto' };
  assert.equal(professionalKeyOf(survey), nomKey('Gil Soto'));
  assert.equal(professionalKeyOf(null), '');
  assert.equal(professionalKeyOf({ inspector: {} }), '');
  assert.equal(professionalKeyOf({ nombre_evaluador: '' }), '');
}
console.log('professionalKeyOf: no-identity default (nom: prefix) OK');

{
  // A sticker's own eligible cédula (>=1 non-roster occurrence) always wins,
  // regardless of name.
  const stickers = [
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '1.234.567' }, inspector_fuente: 'evaluacion' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:1234567');
}
console.log('professionalKeyOf: own eligible cédula wins OK');

{
  // A cédula that ONLY ever appears via inspector_fuente === 'roster' is
  // NEVER eligible as a merge key -- even a record carrying it resolves via
  // name instead (falling back to nom: since the name maps to nothing else).
  const stickers = [
    { inspector: { nombre_completo: 'Beto Ríos', identificacion: '999' }, inspector_fuente: 'roster' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  assert.equal(professionalKeyOf(stickers[0], identity), nomKey('Beto Ríos'), 'roster-only cédula must never become ced:999');
}
console.log('professionalKeyOf: roster-only cédula never eligible OK');

{
  // Name -> cédula unification: a name-only record (Survey, no cédula at
  // all) attaches to the SAME cédula-based row when the name maps to
  // exactly ONE eligible cédula among the stickers.
  const stickers = [
    { inspector: { nombre_completo: 'Carla Nova', identificacion: '555' }, inspector_fuente: 'evaluacion' },
  ];
  const survey = { nombre_evaluador: 'CARLA   nova' }; // case/spacing drift, same normalized name
  const identity = buildIdentityIndex({ stickers, surveys: [survey] });
  assert.equal(professionalKeyOf(survey, identity), 'ced:555');
}
console.log('professionalKeyOf: unique name->cédula unifies a name-only Survey record OK');

{
  // Homonyms with DIFFERENT cédulas: the name maps to >=2 eligible cédulas
  // -> each sticker keeps its OWN cédula key (never merged under the shared
  // name), and a name-only record (no cédula) falls into its own separate
  // 'nom:' bucket instead of guessing which of the two it belongs to --
  // "nombre con dos cédulas -> 3 filas" (plan W5 edge case).
  const stickers = [
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '111' }, inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '222' }, inspector_fuente: 'evaluacion' },
  ];
  const survey = { nombre_evaluador: 'Luis Gil' };
  const identity = buildIdentityIndex({ stickers, surveys: [survey] });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:111');
  assert.equal(professionalKeyOf(stickers[1], identity), 'ced:222');
  assert.equal(professionalKeyOf(survey, identity), nomKey('Luis Gil'), 'ambiguous name-only record must not guess a cédula');
  assert.equal(identity.ambiguousNames.has(normalizeName('Luis Gil')), true);
  // Distinct keys -> distinct rows once buildProfessionalRows runs (checked below).
  assert.notEqual(professionalKeyOf(stickers[0], identity), professionalKeyOf(stickers[1], identity));
}
console.log('professionalKeyOf: homonyms with different cédulas separate into 3 buckets OK');

{
  // profiles: merged display fields per resolved key -- "most frequent raw
  // spelling wins" for the name (same rule as before), "first non-blank
  // wins" for cedula/codigo/entidad/np/tarjetaProfesional/celular/correo.
  const stickers = [
    { inspector: {
      nombre_completo: 'María José López', identificacion: '123', codigo: 'B1', entidad: 'DAGRD',
      np: 'P2', tarjeta_profesional: 'TP-1', num_telefono: '3001234567', correo_contacto: 'mj@example.com',
    }, inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: '  MARIA JOSE LOPEZ  ', identificacion: '123' }, inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'maria   jose lopez', identificacion: '123' }, inspector_fuente: 'evaluacion' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const key = professionalKeyOf(stickers[0], identity);
  const profile = identity.profiles.get(key);
  assert.equal(profile.name, 'María José López', 'most common raw spelling wins');
  assert.equal(profile.cedula, '123');
  assert.equal(profile.codigo, 'B1');
  assert.equal(profile.entidad, 'DAGRD');
  assert.equal(profile.np, 'P2');
  assert.equal(profile.tarjetaProfesional, 'TP-1');
  assert.equal(profile.celular, '3001234567');
  assert.equal(profile.correo, 'mj@example.com');
  assert.equal(profile.ambiguous, false);
}
console.log('buildIdentityIndex: profiles merge display fields (most-frequent name, first-non-blank contact) OK');

{
  // W-TP: "first non-blank wins" is order-driven, not inspector_fuente-
  // driven -- an `evaluacion`-sourced sticker with no TP followed by an
  // `api`-sourced one that DOES carry a TP must still backfill the
  // profile's tarjetaProfesional from whichever record has it first.
  const stickers = [
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '9', tarjeta_profesional: '' }, inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '9', tarjeta_profesional: 'TP-API-1' }, inspector_fuente: 'api' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const key = professionalKeyOf(stickers[0], identity);
  const profile = identity.profiles.get(key);
  assert.equal(profile.tarjetaProfesional, 'TP-API-1', 'TP backfilled from the later api-sourced record when the earlier evaluacion one left it blank');
}
console.log('buildIdentityIndex: tarjetaProfesional first-non-blank works across mixed inspector_fuente rows OK');

// ── buildIdentityIndex: SIN_DATO stickers must never drive identity (H2) ───
// A record whose Fase never resolves (faseKeyDe -> 'SIN_DATO') is dropped
// from buildProfessionalRows entirely (#29) -- but buildIdentityIndex used to
// iterate ALL stickers regardless, so a discarded record could still steer a
// profile's display name, cédula mapping or ambiguity flag. Both sticker
// loops must skip SIN_DATO as their FIRST statement.

{
  // (a) Two SIN_DATO stickers with a misspelled name outnumber a single
  // fase-1 sticker with the CORRECT spelling, same cédula -- "most frequent
  // raw spelling wins" must never even see the SIN_DATO records, so the
  // fase-1 spelling wins despite its lower raw count.
  const stickers = [
    { inspector: { nombre_completo: 'NOMBRE MAL ESCRITO', identificacion: '700' }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'NOMBRE MAL ESCRITO', identificacion: '700' }, fecha: '2026-01-02', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Nombre Bien Escrito', identificacion: '700' }, fecha: '2026-01-03', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const key = professionalKeyOf(stickers[2], identity);
  assert.equal(key, 'ced:700');
  const profile = identity.profiles.get(key);
  assert.equal(profile.name, 'Nombre Bien Escrito', 'SIN_DATO records must never outvote the real fase-1 spelling');
}
console.log('buildIdentityIndex: SIN_DATO stickers never drive the display name (a) OK');

{
  // (b) A fase-1 sticker {Ana Lopez, 888} + a SIN_DATO sticker {Ana Lopez,
  // 889} + a Survey record for "Ana Lopez" -- the SIN_DATO cédula must never
  // become an eligible merge key, so the name maps to exactly ONE eligible
  // cédula (888) and everything (sticker + Survey) merges into ONE row,
  // never split into an ambiguous pair + a detached nom: row.
  const stickers = [
    { inspector: { nombre_completo: 'Ana Lopez', identificacion: '888' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Ana Lopez', identificacion: '889' }, fecha: '2026-01-02', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const surveys = [{ nombre_evaluador: 'Ana Lopez', fecha_inspeccion: '2026-01-03' }];
  const result = buildProfessionalRows({ stickers, surveys });
  assert.equal(result.rows.length, 1, 'the SIN_DATO cédula must never fragment this into an ambiguous pair + detached nom: row');
  assert.equal(result.rows[0].key, 'ced:888');
  assert.equal(result.rows[0].stickersTotal, 1, 'only the fase-1 sticker counts (SIN_DATO excluded)');
  assert.equal(result.rows[0].surveyTotal, 1);
  assert.equal(result.rows[0].ambiguous, false);
}
console.log('buildIdentityIndex: SIN_DATO cédula never fragments a single-professional merge (b) OK');

// ── buildIdentityIndex: `depuracion` (seguimiento-inspectores-depurado, Fase 4) ──
// Task 4.1/design "Frontend Changes (minimal)": `depuracion.activa=true` ->
// profiles are built DIRECTLY from `depuracion.inspectores`; the "primer no
// vacío gana" loop above (buildIdentityIndex's own sticker/Survey passes) is
// skipped ENTIRELY, so a raw sticker's own `profesional.rango`/`insp.np` can
// never backfill or overwrite the backend-resolved `np` (spec: "Frontend
// Consumes Backend-Resolved NP").

{
  const depuracion = {
    activa: true,
    motivo: '',
    referencia_generada_en: '2026-09-12',
    inspectores: [{
      identidad_key: '123', identificacion: '123', nombre_completo: 'Juan Perez',
      codigo: '041', entidad: 'DAGRD', np: 'P3', np_fuente: 'fase2', fase: 'Fase II',
      fase_np_faltante: false, estado_sugerido: 'activo', fuente_dato: 'main+fase2',
      tarjeta_profesional: 'TP-1', num_telefono: '3000000000', correo_contacto: 'juan@example.com',
      no_persona: false,
    }],
    grupo_externos: null,
    alias_nombres: {},
    revision_manual: [],
  };
  // The live API's raw record for the SAME person still carries the OLD
  // profesional.rango (here modeled as the sticker's own `insp.np`) -- it
  // must never win over the backend's np.
  const stickers = [{ inspector: { identificacion: '123', nombre_completo: 'Juan Perez', np: 'P1' } }];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  const key = professionalKeyOf(stickers[0], identity);
  assert.equal(key, 'ced:123');
  const profile = identity.profiles.get(key);
  assert.equal(profile.np, 'P3', 'backend np must not be overwritten by the raw sticker np');
  assert.equal(profile.npFuente, 'fase2');
  assert.equal(profile.estadoSugerido, 'activo');
  assert.equal(profile.fuenteDato, 'main+fase2');
  assert.equal(profile.fase, 'Fase II');
  assert.equal(identity.depuracionActiva, true);
  assert.equal(identity.referenciaGeneradaEn, '2026-09-12');
}
console.log('buildIdentityIndex: depuracion.activa=true -> backend np/estado/fuente win over the raw sticker np OK');

{
  // Same fields, one level up: buildProfessionalRows' own row output must
  // forward fase/estadoSugerido/fuenteDato from the profile untouched (task
  // 4.9's "same resolved fields as the table" -- these are the fields
  // xlsxRowsFor/matchesSearch/cellHtml read from `row`, never re-derived).
  const depuracion = {
    activa: true, motivo: '', referencia_generada_en: '2026-09-12',
    inspectores: [{
      identidad_key: '77', identificacion: '77', nombre_completo: 'Rita Diaz',
      codigo: '', entidad: '', np: 'P4', np_fuente: 'fase2', fase: 'Fase II',
      fase_np_faltante: false, estado_sugerido: 'candidato_desactivacion', fuente_dato: 'fase2',
      tarjeta_profesional: '', num_telefono: '', correo_contacto: '', no_persona: false,
    }],
    grupo_externos: null, alias_nombres: {}, revision_manual: [],
  };
  const stickers = [{ inspector: { identificacion: '77', nombre_completo: 'Rita Diaz' } }];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  const result = buildProfessionalRows({ stickers, surveys: [], identity });
  const row = result.rows[0];
  assert.equal(row.fase, 'Fase II');
  assert.equal(row.estadoSugerido, 'candidato_desactivacion');
  assert.equal(row.fuenteDato, 'fase2');
  assert.equal(row.npFuente, 'fase2');
}
console.log('buildProfessionalRows: forwards fase/estadoSugerido/fuenteDato from the profile untouched OK');

{
  // Grouping: multiple raw sticker records for the SAME backend identity
  // still merge into ONE row via the resolved cédula (eligibleCedulas built
  // from depuracion.inspectores), never a client-side "first non-empty
  // wins" recompute over the raw records (spec: "Grouping by identity uses
  // resolved np, not a client-side merge").
  const depuracion = {
    activa: true,
    motivo: '',
    referencia_generada_en: '2026-09-12',
    inspectores: [{
      identidad_key: '9', identificacion: '9', nombre_completo: 'Ana Ruiz',
      codigo: '', entidad: '', np: 'P2', np_fuente: 'vercel', fase: 'Fase I',
      fase_np_faltante: false, estado_sugerido: 'activo', fuente_dato: 'vercel',
      tarjeta_profesional: '', num_telefono: '', correo_contacto: '', no_persona: false,
    }],
    grupo_externos: null,
    alias_nombres: {},
    revision_manual: [],
  };
  const stickers = [
    { inspector: { identificacion: '9', nombre_completo: 'Ana Ruiz' } },
    { inspector: { identificacion: '9', nombre_completo: 'ANA RUIZ' } },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  const result = buildProfessionalRows({ stickers, surveys: [], identity });
  assert.equal(result.rows.length, 1, 'both raw records for identidad_key 9 must merge into ONE row');
  assert.equal(result.rows[0].np, 'P2');
}
console.log('buildIdentityIndex: depuracion grouping uses the resolved cédula, not a client re-merge OK');

{
  // CRITICAL fix (adversarial review): when the backend merges two
  // identities by exact name (_unificar_por_nombre), the LOSING cédula is
  // now serialized on the survivor's own `cedulas_unificadas` (backend
  // fix, _perfil_a_dict). A raw sticker still carrying that losing cédula
  // in `inspector.identificacion` must resolve into the SAME row as the
  // survivor's own cédula, never an orphan `nom:` bucket — spec: "Grouping
  // by identity uses resolved np, not a client-side merge".
  const depuracion = {
    activa: true,
    motivo: '',
    referencia_generada_en: '2026-09-12',
    inspectores: [{
      identidad_key: '111', identificacion: '111', nombre_completo: 'Juan Perez',
      cedulas_unificadas: ['222'],
      codigo: '', entidad: '', np: 'P2', np_fuente: 'vercel', fase: 'Fase I',
      fase_np_faltante: false, estado_sugerido: 'activo', fuente_dato: 'vercel',
      tarjeta_profesional: '', num_telefono: '', correo_contacto: '', no_persona: false,
    }],
    grupo_externos: null,
    alias_nombres: {},
    revision_manual: [],
  };
  const stickers = [
    { inspector: { identificacion: '111', nombre_completo: 'Juan Perez' } },
    // Same person, but the sticker still carries the LOSING cédula ('222')
    // that got merged away on the backend.
    { inspector: { identificacion: '222', nombre_completo: 'Juan Perez' } },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
  assert.equal(professionalKeyOf(stickers[0], identity), 'ced:111');
  assert.equal(
    professionalKeyOf(stickers[1], identity), 'ced:111',
    'a merged-away cédula must resolve to the survivor row, never its own ced:222 or an orphan nom: bucket',
  );
  const result = buildProfessionalRows({ stickers, surveys: [], identity });
  assert.equal(result.rows.length, 1, 'both raw records must merge into ONE row, not an orphan row for the losing cédula');
  assert.equal(result.rows[0].stickersTotal, 2);
  assert.equal(result.rows[0].np, 'P2');
}
console.log('buildIdentityIndex: a merged-away cédula (cedulas_unificadas) groups into the survivor row, not an orphan OK');

{
  // seguimiento-inspectores-depurado, slice 08 (D29): the backend now emits
  // `depuracion.inspectores` in canonical (identidad_key) order instead of
  // roster-then-main order. The identity index must NOT depend on that order:
  // every permutation of the rows yields the same eligible cédulas, the same
  // merged-away -> survivor routing (incl. the pre-Fase-2-fix cédula and its
  // zero-padded form the backend exports in `cedulas_unificadas`) and the same
  // profiles.
  const fila = (key, nombre, unificadas = []) => ({
    identidad_key: key, identificacion: key, nombre_completo: nombre, cedulas_unificadas: unificadas,
    codigo: '', entidad: '', np: 'P2', np_fuente: 'fase2', fase: 'Fase I', fase_np_faltante: false,
    estado_sugerido: 'activo', fuente_dato: 'main+fase2', tarjeta_profesional: '', num_telefono: '',
    correo_contacto: '', no_persona: false,
  });
  const filas = [
    fila('1053812345', 'Ana Gomez', ['999', '0999']),   // Fase 2 cédula fix: old key exported
    fila('1111111', 'Beto Dos', ['2222222']),           // unified loser
    fila('3333333', 'Carla Tres'),
  ];
  const stickers = ['999', '0999', '1053812345', '2222222', '1111111', '3333333'].map((ced) => ({
    inspector: { identificacion: ced, nombre_completo: 'x' },
  }));
  const instantanea = (orden) => {
    const depuracion = {
      activa: true, motivo: '', referencia_generada_en: '2026-09-12', inspectores: orden,
      grupo_externos: null, alias_nombres: {}, revision_manual: [],
    };
    const identity = buildIdentityIndex({ stickers, surveys: [], depuracion });
    return JSON.stringify({
      eligible: [...identity.eligibleCedulas].sort(),
      fusionadas: [...identity.cedulaFusionadaACedulaSurvivor.entries()].sort(),
      profiles: [...identity.profiles.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)),
      rutas: stickers.map((st) => professionalKeyOf(st, identity)),
    });
  };
  const esperado = instantanea(filas);
  assert.deepEqual(
    JSON.parse(esperado).rutas,
    ['ced:1053812345', 'ced:1053812345', 'ced:1053812345', 'ced:1111111', 'ced:1111111', 'ced:3333333'],
    'every cédula form a sticker can carry must route to its row',
  );
  const permutaciones = (lista) => (lista.length <= 1 ? [lista] : lista.flatMap((item, i) => (
    permutaciones([...lista.slice(0, i), ...lista.slice(i + 1)]).map((resto) => [item, ...resto])
  )));
  const todas = permutaciones(filas);
  assert.equal(todas.length, 6);
  for (const orden of todas) assert.equal(instantanea(orden), esperado, 'row order must not change the identity index');
}
console.log('buildIdentityIndex: the depuracion row order (canonical since slice 08) does not change the identity index OK');

// Task 4.3: `depuracion` absent, or `activa:false`, must be BYTE-IDENTICAL
// to the pre-existing "primer no vacío gana" code path (cold start / feature
// flag off / reference Blob down) -- same profile.np result as calling
// buildIdentityIndex WITHOUT a depuracion argument at all.
{
  const stickers = [{ inspector: { identificacion: '55', nombre_completo: 'Luis Gomez', np: 'P1' } }];
  const withoutDepuracion = buildIdentityIndex({ stickers, surveys: [] });
  const inactiveDepuracion = buildIdentityIndex({
    stickers,
    surveys: [],
    depuracion: {
      activa: false, motivo: 'sin_blob', referencia_generada_en: '',
      inspectores: [], grupo_externos: null, alias_nombres: {}, revision_manual: [],
    },
  });
  const keyA = professionalKeyOf(stickers[0], withoutDepuracion);
  const keyB = professionalKeyOf(stickers[0], inactiveDepuracion);
  assert.equal(keyA, keyB);
  assert.deepEqual(
    withoutDepuracion.profiles.get(keyA),
    inactiveDepuracion.profiles.get(keyB),
    'activa:false must fall through to the exact same profile the no-depuracion call produces',
  );
  assert.equal(inactiveDepuracion.depuracionActiva, false);
  assert.equal(inactiveDepuracion.depuracionMotivo, 'sin_blob');
  // A completely absent `depuracion` (cold start / feature-flag-off
  // payload, tagFuente's own `depuracion: null` default) must degrade the
  // same way -- never throw, never report `activa: true`.
  const noDepuracionAtAll = buildIdentityIndex({ stickers, surveys: [], depuracion: null });
  assert.equal(noDepuracionAtAll.depuracionActiva, false);
  assert.equal(noDepuracionAtAll.depuracionMotivo, '');
  assert.equal(noDepuracionAtAll.grupoExternos, null);
  assert.deepEqual(noDepuracionAtAll.revisionManual, []);
}
console.log('buildIdentityIndex: depuracion absent/activa:false -> byte-identical to the current code path OK');

// ── buildProfessionalRows: empty inputs ────────────────────────────────────

{
  const result = buildProfessionalRows({ stickers: [], surveys: [] });
  assert.deepEqual(result.rows, []);
  assert.deepEqual(result.unassigned, { stickers: 0, surveys: 0 });
  assert.equal(result.stickersWithoutDate, 0);
  assert.deepEqual(result.totals, {
    professionals: 0, stickers: 0, surveys: 0, avgPerProfessional: 0, unassigned: 0, stickersWithoutDate: 0,
  });
}

// Also tolerant of missing/undefined stickers/surveys arrays entirely.
{
  const result = buildProfessionalRows({});
  assert.deepEqual(result.rows, []);
  assert.equal(result.totals.professionals, 0);
}
console.log('buildProfessionalRows: empty inputs OK');

// ── buildProfessionalRows: null/undefined/empty name fields -> unassigned ──

{
  const stickers = [
    { inspector: { nombre_completo: '' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' },
    { inspector: {}, fecha: '2026-01-02', fase: 2, fuente: 'atencionsismo' },
    { fecha: '2026-01-03', fase: 1, fuente: 'atencionsismo' },
  ];
  const surveys = [
    { nombre_evaluador: '', fecha_inspeccion: '2026-01-01' },
    { fecha_inspeccion: '2026-01-02' },
  ];
  const result = buildProfessionalRows({ stickers, surveys });
  assert.deepEqual(result.rows, []);
  assert.deepEqual(result.unassigned, { stickers: 3, surveys: 2 });
  assert.equal(result.totals.stickers, 3);
  assert.equal(result.totals.surveys, 2);
  assert.equal(result.totals.unassigned, 5);
}
console.log('buildProfessionalRows: unassigned bucket OK');

// ── buildProfessionalRows: accent/case/whitespace merge into one row ───────

{
  const stickers = [
    { inspector: { nombre_completo: 'María José López', identificacion: '123', codigo: 'B1', entidad: 'DAGRD' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: '  MARIA JOSE LOPEZ  ' }, fecha: '2026-01-02', fase: 2, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'maria   jose lopez' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(result.rows.length, 1);
  const row = result.rows[0];
  assert.equal(row.name, 'María José López'); // most common raw spelling seen
  assert.equal(row.cedula, '123');
  assert.equal(row.codigo, 'B1');
  assert.equal(row.entidad, 'DAGRD');
  assert.equal(row.stickersFase1, 2);
  assert.equal(row.stickersFase2, 1);
  assert.equal(row.stickersTotal, 3);
}
console.log('buildProfessionalRows: name normalization merge OK');

// ── buildProfessionalRows: sticker with null fecha -> counted but no date ──

{
  const stickers = [
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: null, fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: '2026-02-01', fase: 1, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(result.rows.length, 1);
  const row = result.rows[0];
  assert.equal(row.stickersTotal, 2); // both counted in totals
  assert.equal(row.firstDate, '2026-02-01'); // only the dated one contributes
  assert.equal(row.lastDate, '2026-02-01');
  assert.equal(row.activeDays, 1);
  assert.equal(result.stickersWithoutDate, 1);
  assert.equal(result.totals.stickers, 2);
}
console.log('buildProfessionalRows: null-fecha sticker counted OK');

// ── buildProfessionalRows: date filter excludes rows + drops null-fecha stickers ──

{
  const stickers = [
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: null, fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: '2026-02-01', fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: '2026-03-15', fase: 1, fuente: 'atencionsismo' },
  ];
  // No filter: all 3 counted (1 without date).
  const noFilter = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(noFilter.rows[0].stickersTotal, 3);
  assert.equal(noFilter.stickersWithoutDate, 1);

  // Filtered to February only: the null-fecha sticker must drop out entirely
  // (never assumed to be "in range"), and the March sticker is excluded too.
  const filtered = buildProfessionalRows({ stickers, surveys: [], from: '2026-02-01', to: '2026-02-28' });
  assert.equal(filtered.rows[0].stickersTotal, 1);
  assert.equal(filtered.stickersWithoutDate, 0);
  assert.equal(filtered.totals.stickers, 1);

  // `to` is inclusive: a record dated exactly on the boundary still counts.
  const inclusiveTo = buildProfessionalRows({ stickers, surveys: [], from: '2026-02-01', to: '2026-02-01' });
  assert.equal(inclusiveTo.rows[0].stickersTotal, 1);

  // `to`-only filter (no `from`): everything on or before `to` counts, the
  // null-fecha record still drops (a filter is active) and the March one is excluded.
  const toOnly = buildProfessionalRows({ stickers, surveys: [], to: '2026-02-01' });
  assert.equal(toOnly.rows[0].stickersTotal, 1);
  assert.equal(toOnly.stickersWithoutDate, 0);

  // A from-only filter that excludes everything collapses the professional
  // entirely (no assigned records at all -> no row).
  const excludesAll = buildProfessionalRows({ stickers, surveys: [], from: '2027-01-01' });
  assert.deepEqual(excludesAll.rows, []);
}
console.log('buildProfessionalRows: date filter OK (incl. inclusive `to` and `to`-only) ');

// ── buildProfessionalRows: SIN_DATO stickers excluded entirely (#29, same
// rule as evaluaciones.js's Stickers tab) ──────────────────────────────────

{
  const stickers = [
    // SIN_DATO (atencionsismo, fase null, no inspector.np) -- must not count
    // anywhere: not in stickersFase1/2, not in stickersTotal, not in
    // stickersWithoutDate (it HAS a valid fecha) and not in unassigned.
    { inspector: { nombre_completo: 'Luis Gil' }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Luis Gil' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Luis Gil' }, fecha: '2026-01-03', fase: 2, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  const row = result.rows[0];
  assert.equal(row.stickersFase1, 1);
  assert.equal(row.stickersFase2, 1);
  assert.equal(row.stickersTotal, 2, 'the SIN_DATO record must never count toward the total');
  assert.equal(result.stickersWithoutDate, 0, 'a SIN_DATO record with a real fecha still must not count here');
  assert.equal(result.totals.stickers, 2);
}
console.log('buildProfessionalRows: SIN_DATO stickers excluded from Fase buckets/totals OK');

{
  // A SIN_DATO sticker with no resolvable name must not even fall into the
  // "sin profesional identificado" bucket -- it's dropped before that check.
  const stickers = [
    { inspector: {}, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.deepEqual(result.rows, []);
  assert.equal(result.unassigned.stickers, 0);
  assert.equal(result.totals.stickers, 0);
}
console.log('buildProfessionalRows: unnamed SIN_DATO sticker never counted as unassigned OK');

// ── buildProfessionalRows: fase rule shared with utils.js's faseKeyDe ──────
// (contrato v3: fase null/unrecognized on an atencionsismo sticker falls
// back to the inspector's NP category — P3+ is Fase II — same shared rule
// evaluaciones.js uses, instead of a second independent switch here.)

{
  const stickers = [
    { inspector: { nombre_completo: 'Nora Vidal', np: 'P4' }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  const row = result.rows[0];
  assert.equal(row.stickersFase2, 1);
  assert.equal(row.stickersFase1, 0);
}
console.log('buildProfessionalRows: fase fallback via inspector.np (faseKeyDe) OK');

// ── buildProfessionalRows: invalid fecha_inspeccion (ignored for dates but counted) ──

{
  const surveys = [
    { nombre_evaluador: 'Rita Paz', fecha_inspeccion: 'no-es-una-fecha' },
    { nombre_evaluador: 'Rita Paz', fecha_inspeccion: '2026-04-01' },
  ];
  const result = buildProfessionalRows({ stickers: [], surveys });
  const row = result.rows[0];
  assert.equal(row.surveyTotal, 2); // both counted
  assert.equal(row.firstDate, '2026-04-01'); // invalid one contributes no date
  assert.equal(row.lastDate, '2026-04-01');
  assert.equal(row.activeDays, 1);
}
console.log('buildProfessionalRows: invalid fecha_inspeccion OK');

// ── buildProfessionalRows: date-overflow guard ("2026-02-30" rejected) ─────
// Must never silently roll over to a real day (e.g. March 2) — treated the
// same as any other unparseable date: counted, but contributes no date.

{
  const surveys = [{ nombre_evaluador: 'Rita Paz', fecha_inspeccion: '2026-02-30' }];
  const result = buildProfessionalRows({ stickers: [], surveys });
  const row = result.rows[0];
  assert.equal(row.surveyTotal, 1);
  assert.equal(row.firstDate, null);
  assert.equal(row.lastDate, null);
  assert.equal(row.activeDays, 0);
}
console.log('buildProfessionalRows: date-overflow guard rejects 2026-02-30 OK');

// ── buildProfessionalRows: rosterSourced counting ──────────────────────────

{
  const stickers = [
    { inspector: { nombre_completo: 'Beto Ríos' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'roster' },
    { inspector: { nombre_completo: 'Beto Ríos' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Beto Ríos' }, fecha: '2026-01-03', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'roster' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(result.rows[0].rosterSourced, 2);
}
console.log('buildProfessionalRows: rosterSourced OK');

// ── buildProfessionalRows: activeDays/avgPerActiveDay across multiple days ─
// Bare dates on purpose (no time component): this test's intent is the
// activeDays/avg arithmetic, not timezone bucketing (that has its own
// dedicated buildTimeline test below) — using a machine-local-timezone-
// sensitive timestamp here would make this test flaky on CI.

{
  const stickers = [
    { inspector: { nombre_completo: 'Dana Vela' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Dana Vela' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Dana Vela' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  const row = result.rows[0];
  assert.equal(row.activeDays, 2);
  assert.equal(row.avgPerActiveDay, 1.5); // 3 dated records / 2 active days
}
console.log('buildProfessionalRows: activeDays/avgPerActiveDay OK');

// ── buildProfessionalRows: identity layer end-to-end (D7, cédula-first) ────

{
  // "nombre con dos cédulas -> 3 filas": two stickers share a name but carry
  // different eligible cédulas, plus a Survey-only record with that same
  // name (no cédula at all) -- must produce THREE separate rows, never
  // merged under the shared name.
  const stickers = [
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '111' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '222' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const surveys = [{ nombre_evaluador: 'Luis Gil', fecha_inspeccion: '2026-01-03' }];
  const result = buildProfessionalRows({ stickers, surveys });
  assert.equal(result.rows.length, 3);
  const keys = result.rows.map((r) => r.key).sort();
  assert.deepEqual(keys, ['ced:111', 'ced:222', nomKey('Luis Gil')].sort());
  const cedRow111 = result.rows.find((r) => r.key === 'ced:111');
  const cedRow222 = result.rows.find((r) => r.key === 'ced:222');
  const nomRow = result.rows.find((r) => r.key === nomKey('Luis Gil'));
  assert.equal(cedRow111.stickersTotal, 1);
  assert.equal(cedRow222.stickersTotal, 1);
  assert.equal(nomRow.surveyTotal, 1, 'the ambiguous-name Survey record must attach to its OWN nom: bucket');
  assert.equal(nomRow.stickersTotal, 0);
  // All three rows carry the ambiguous flag -- a viewer must be warned that
  // this name is shared by more than one identity.
  assert.equal(cedRow111.ambiguous, true);
  assert.equal(cedRow222.ambiguous, true);
  assert.equal(nomRow.ambiguous, true);
}
console.log('buildProfessionalRows: homonyms with different cédulas -> 3 rows OK');

{
  // A Survey-only record (no cédula at all) whose name maps to EXACTLY ONE
  // eligible cédula attaches to that professional's ced: row -- the row's
  // surveyTotal/stickersTotal both come from the SAME key, and the sum of
  // buildTimeline's per-source counts for that key matches the row totals.
  const stickers = [
    { inspector: { nombre_completo: 'Carla Nova', identificacion: '555' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const surveys = [{ nombre_evaluador: 'CARLA nova', fecha_inspeccion: '2026-01-02' }];
  const result = buildProfessionalRows({ stickers, surveys });
  assert.equal(result.rows.length, 1);
  const row = result.rows[0];
  assert.equal(row.key, 'ced:555');
  assert.equal(row.stickersTotal, 1);
  assert.equal(row.surveyTotal, 1);
  const identity = buildIdentityIndex({ stickers, surveys });
  const timeline = buildTimeline({
    stickers, surveys, identity, professionalKey: row.key,
  });
  const timelineStickers = timeline.stickers.reduce((a, b) => a + b, 0);
  const timelineSurveys = timeline.surveys.reduce((a, b) => a + b, 0);
  assert.equal(timelineStickers, row.stickersTotal);
  assert.equal(timelineSurveys, row.surveyTotal);
}
console.log('buildProfessionalRows: survey-only name unifies into the ced: row (sum(timeline)===row totals) OK');

{
  // A cédula sourced ONLY via roster must NEVER absorb a Survey record with
  // the same name -- the roster-only sticker and the Survey record land in
  // TWO different rows (the roster-only one keeps its own nom: bucket).
  const stickers = [
    { inspector: { nombre_completo: 'Beto Ríos', identificacion: '999' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'roster' },
  ];
  const surveys = [{ nombre_evaluador: 'Beto Ríos', fecha_inspeccion: '2026-01-02' }];
  const result = buildProfessionalRows({ stickers, surveys });
  // Both the sticker and the Survey record resolve via name (no eligible
  // cédula exists for "Beto Ríos" at all) -- so they DO share the same
  // nom: bucket, but it must never be 'ced:999' (the roster-only cédula).
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].key, nomKey('Beto Ríos'));
  assert.notEqual(result.rows[0].key, 'ced:999');
}
console.log('buildProfessionalRows: roster-only cédula never absorbs a Survey record under ced: OK');

{
  // New identity/contact fields (W5): first non-blank wins, exposed on the row.
  const stickers = [
    { inspector: {
      nombre_completo: 'Nora Vidal', identificacion: '777', np: 'P4',
      tarjeta_profesional: 'TP-9', num_telefono: '3009998888', correo_contacto: 'nora@example.com',
    }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  const row = result.rows[0];
  assert.equal(row.np, 'P4');
  assert.equal(row.tarjetaProfesional, 'TP-9');
  assert.equal(row.celular, '3009998888');
  assert.equal(row.correo, 'nora@example.com');
  assert.equal(row.ambiguous, false);
  assert.deepEqual(row.barriosActivos, [], 'no barrio_reportado on this fixture -> empty array, never undefined/throw');
}
console.log('buildProfessionalRows: np/tarjetaProfesional/celular/correo/barriosActivos fields present OK');

{
  // Totals must reconcile regardless of how many rows the identity split
  // produces: totals.stickers === Σ rows.stickersTotal + unassigned.stickers.
  const stickers = [
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '111' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Luis Gil', identificacion: '222' }, fecha: '2026-01-02', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: {}, fecha: '2026-01-03', fase: 1, fuente: 'atencionsismo' }, // unassigned (no name)
  ];
  const surveys = [
    { nombre_evaluador: 'Luis Gil', fecha_inspeccion: '2026-01-04' },
    { nombre_evaluador: '', fecha_inspeccion: '2026-01-04' }, // unassigned
  ];
  const result = buildProfessionalRows({ stickers, surveys });
  const rowsStickerSum = result.rows.reduce((n, r) => n + r.stickersTotal, 0);
  const rowsSurveySum = result.rows.reduce((n, r) => n + r.surveyTotal, 0);
  assert.equal(result.totals.stickers, rowsStickerSum + result.unassigned.stickers);
  assert.equal(result.totals.surveys, rowsSurveySum + result.unassigned.surveys);
}
console.log('buildProfessionalRows: totals reconcile across a multi-row identity split OK');

// ── buildBarriosActivos ─────────────────────────────────────────────────────

{
  // Window is inclusive of today-6, exclusive of today-7 (default days=7).
  const row = { key: 'ced:1' };
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'Barrio Adentro' }, // today (2026-01-09 Bogotá, see below)
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-03T12:00:00+00:00', barrio_reportado: 'Barrio Limite' }, // today-6, IN
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-02T12:00:00+00:00', barrio_reportado: 'Barrio Fuera' }, // today-7, OUT
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const barrios = buildBarriosActivos(row, { stickers, today: '2026-01-09', identity });
  assert.deepEqual(barrios, ['Barrio Adentro', 'Barrio Limite']);
}
console.log('buildBarriosActivos: window inclusive today-6 / exclusive today-7 OK');

{
  // Blank barrio_reportado and "Sin identificar" (case/accent-insensitive)
  // never count; accent/case variants of the SAME real barrio count once.
  const row = { key: 'ced:1' };
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'San Antonio' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T13:00:00+00:00', barrio_reportado: 'SAN ANTONIO' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T14:00:00+00:00', barrio_reportado: '' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T15:00:00+00:00', barrio_reportado: 'Sin identificar' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const barrios = buildBarriosActivos(row, { stickers, today: '2026-01-09', identity });
  assert.deepEqual(barrios, ['San Antonio']);
}
console.log('buildBarriosActivos: blank/"Sin identificar" excluded, accent/case variants dedupe OK');

{
  // Only this professional's OWN stickers count, and SIN_DATO is excluded.
  const row = { key: 'ced:1' };
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'Mío' },
    { inspector: { nombre_completo: 'Y', identificacion: '2' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'Ajeno' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: null, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'SinDato' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const barrios = buildBarriosActivos(row, { stickers, today: '2026-01-09', identity });
  assert.deepEqual(barrios, ['Mío']);
}
console.log('buildBarriosActivos: only own (non-SIN_DATO) stickers count OK');

{
  // L6: the dedupe key must strip accents, lowercase, AND collapse internal
  // whitespace -- "San  Antonio" (double space) and "San Antonio" must
  // dedupe to ONE entry, not two.
  const row = { key: 'ced:1' };
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'San  Antonio' },
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T13:00:00+00:00', barrio_reportado: 'San Antonio' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const barrios = buildBarriosActivos(row, { stickers, today: '2026-01-09', identity });
  assert.deepEqual(barrios, ['San  Antonio'], 'double-internal-space and single-space variants must dedupe to ONE entry (first-seen spelling kept)');
}
console.log('buildBarriosActivos: internal-whitespace variants dedupe to one entry (L6) OK');

// ── buildBarriosActivosByKey (B1): ONE pass over all stickers, keyed by ────
// professional -- buildProfessionalRows used to call buildBarriosActivos
// (a full sticker rescan) once PER ROW: 110 rows x ~3,037 stickers each
// rescanned ~ 334k identity resolutions (measured 168 ms vs 8.8 ms before
// this change). buildBarriosActivos(row, ...) above is now a thin wrapper
// over this batch builder -- same output, single pass.

{
  // Batch vs per-row equality on a mixed fixture (several professionals,
  // SIN_DATO mixed in, blank/"Sin identificar" barrios, accent/case variants,
  // out-of-window dates).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'San Antonio' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-08T12:00:00+00:00', barrio_reportado: 'SAN ANTONIO' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: null, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: 'Descartado' }, // SIN_DATO
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2025-12-01T12:00:00+00:00', barrio_reportado: 'Fuera De Ventana' }, // out of window
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '2' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00', barrio_reportado: '' },
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '2' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T13:00:00+00:00', barrio_reportado: 'Sin identificar' },
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '2' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T14:00:00+00:00', barrio_reportado: 'Barrio Ana' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const today = '2026-01-09';
  const keys = ['ced:1', 'ced:2'];
  const byKey = buildBarriosActivosByKey({
    stickers, identity, today,
  });
  for (const key of keys) {
    const perRow = buildBarriosActivos({ key }, { stickers, today, identity });
    assert.deepEqual(byKey.get(key) || [], perRow, `batch and per-row must agree for ${key}`);
  }
  assert.deepEqual(byKey.get('ced:1'), ['San Antonio'], 'SIN_DATO excluded, out-of-window excluded, accent/case variant deduped');
  assert.deepEqual(byKey.get('ced:2'), ['Barrio Ana'], 'blank and Sin identificar excluded');
}
console.log('buildBarriosActivosByKey: batch vs per-row equality on a mixed fixture (B1) OK');

// ── buildTemporalMetrics ─────────────────────────────────────────────────────
// CONTRATO CAMBIADO (H3): firstRecordMinutes/lastRecordMinutes used to come
// from the EARLIEST day's min and the LATEST day's max respectively -- so
// "last" could end up EARLIER than "first" (e.g. 09-10 09:00, 09-10 17:00,
// 09-12 08:00 -> old first=540, old last=480). The UI's columns are "Hora
// 1er/últ. registro (día ant.)": both must come from the SAME day, `prevDay`
// (the latest day strictly before `today`) -- renamed prevDayFirstMinutes/
// prevDayLastMinutes accordingly, null when prevDay is null or that day has
// no timed record (defensive; prevDay is only ever set when it does).

{
  // Empty (no timed records at all) -> everything null, daysSinceFirst null
  // when the row has no firstDate either.
  const row = { key: nomKey('Gil Soto'), firstDate: null };
  const result = buildTemporalMetrics(row, { stickers: [], surveys: [], today: '2026-01-09' });
  assert.deepEqual(result, {
    prevDayFirstMinutes: null, prevDayLastMinutes: null,
    avgFirstMinutes: null, avgLastMinutes: null,
    prevDay: null, daysSinceFirst: null,
  });
}
console.log('buildTemporalMetrics: empty inputs OK');

{
  // Survey fecha_hora minute-of-day parsing: T00:05 -> 5, T12:05 -> 725,
  // both on the SAME (only) day, which resolves as prevDay.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-01' };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-01T00:05' },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-01T12:05' },
  ];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.prevDay, '2026-01-01');
  assert.equal(result.prevDayFirstMinutes, 5);
  assert.equal(result.prevDayLastMinutes, 725);
  assert.equal(result.avgFirstMinutes, 5);
  assert.equal(result.avgLastMinutes, 725);
}
console.log('buildTemporalMetrics: Survey fecha_hora minute-of-day (00:05->5, 12:05->725) OK');

{
  // Missing/malformed fecha_hora contributes no hour, never throws:
  // T25:00 (out-of-range hour) and T7:5 (single-digit minute, no match).
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-01' };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_hora: undefined },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-01T25:00' },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-01T7:5' },
  ];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.prevDayFirstMinutes, null);
  assert.equal(result.avgFirstMinutes, null);
}
console.log('buildTemporalMetrics: missing/malformed fecha_hora -> no hour, never throws OK');

{
  // prevDay: only a record dated TODAY -> prevDay null (never "yesterday"
  // unless there really is a timed record on a day strictly before today) --
  // and prevDayFirstMinutes/prevDayLastMinutes must be null right along with it.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-09' };
  const surveys = [{ nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-09T08:00' }];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.prevDay, null);
  assert.equal(result.prevDayFirstMinutes, null);
  assert.equal(result.prevDayLastMinutes, null);
  assert.equal(result.daysSinceFirst, 0);
}
console.log('buildTemporalMetrics: only-today record -> prevDay null, daysSinceFirst 0 OK');

{
  // prevDay resolves to the last timed day strictly before today; a
  // dated-but-UNtimed day (no fecha_hora) counts toward buildProfessionalRows'
  // activeDays but contributes NOTHING to buildTemporalMetrics' own day set.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-05' };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-08', fecha_hora: undefined }, // dated, no hour
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-07', fecha_hora: '2026-01-07T09:30' },
  ];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-01-09' });
  assert.equal(result.prevDay, '2026-01-07', 'the undated-hour 2026-01-08 record must not count as a timed day');
  assert.equal(result.prevDayFirstMinutes, 570); // 09:30
  assert.equal(result.prevDayLastMinutes, 570);
  assert.equal(result.daysSinceFirst, 4); // 2026-01-09 - 2026-01-05
}
console.log('buildTemporalMetrics: daysSinceFirst (yesterday=1 case covered via prevDay path) + untimed day excluded OK');

{
  // daysSinceFirst: yesterday -> 1.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-01-08' };
  const result = buildTemporalMetrics(row, { stickers: [], surveys: [], today: '2026-01-09' });
  assert.equal(result.daysSinceFirst, 1);
}
console.log('buildTemporalMetrics: daysSinceFirst yesterday -> 1 OK');

{
  // H3 (Blocker regression, reproduced exactly): 09-10 09:00 (540), 09-10
  // 17:00 (1020), 09-12 08:00 (480) -- the OLD firstRecordMinutes/
  // lastRecordMinutes (earliest-day-min / latest-day-max) gave first=540,
  // last=480, i.e. "last" EARLIER than "first". Both prevDayFirstMinutes and
  // prevDayLastMinutes must come from THE SAME day (prevDay = 09-12, the
  // latest day strictly before today), so last >= first always holds.
  const row = { key: nomKey('Gil Soto'), firstDate: '2026-09-10' };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-09-10T09:00' },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-09-10T17:00' },
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-09-12T08:00' },
  ];
  const result = buildTemporalMetrics(row, { stickers: [], surveys, today: '2026-09-13' });
  assert.equal(result.prevDay, '2026-09-12');
  assert.equal(result.prevDayFirstMinutes, 480);
  assert.equal(result.prevDayLastMinutes, 480);
  assert.ok(result.prevDayLastMinutes >= result.prevDayFirstMinutes, 'last must never be earlier than first (H3)');
}
console.log('buildTemporalMetrics: prevDayFirst/LastMinutes come from the SAME day, last>=first (H3) OK');

{
  // M5: batch vs per-row equality on a mixed fixture (several professionals,
  // stickers + surveys, SIN_DATO mixed in, untimed records mixed in) --
  // buildTemporalMetricsByKey's single-pass result must match calling
  // buildTemporalMetrics() once per professional exactly.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-05T09:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-06T14:30:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-05T20:00:00+00:00', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' }, // SIN_DATO
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '2' }, fecha: '2026-01-07T12:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_hora: '2026-01-08T08:15' },
    { nombre_evaluador: 'Ana Ruiz', fecha_hora: '2026-01-08T09:45' },
    { nombre_evaluador: 'Ana Ruiz', fecha_inspeccion: '2026-01-08', fecha_hora: undefined }, // dated, untimed
  ];
  const identity = buildIdentityIndex({ stickers, surveys });
  const today = '2026-01-09';
  const keys = ['ced:1', 'ced:2'];
  const byKey = buildTemporalMetricsByKey({
    stickers, surveys, identity, today,
  });
  for (const key of keys) {
    const perRow = buildTemporalMetrics({ key, firstDate: null }, {
      stickers, surveys, identity, today,
    });
    const { daysSinceFirst, ...perRowMetrics } = perRow;
    assert.deepEqual(byKey.get(key), perRowMetrics, `batch and per-row must agree for ${key}`);
  }
}
console.log('buildTemporalMetricsByKey: batch vs per-row equality on a mixed fixture (M5) OK');

// ── sortRows ────────────────────────────────────────────────────────────

{
  const rows = [
    { key: 'a', name: 'Ana', total: 5 },
    { key: 'b', name: 'Beto', total: 10 },
    { key: 'c', name: 'Caro', total: 10 }, // tie with Beto on `total`
  ];
  const byTotalDesc = sortRows(rows, 'total', 'desc');
  assert.deepEqual(byTotalDesc.map((r) => r.key), ['b', 'c', 'a']); // ties broken by key, stable
  const byTotalAsc = sortRows(rows, 'total', 'asc');
  assert.deepEqual(byTotalAsc.map((r) => r.key), ['a', 'b', 'c']);
  const byNameAsc = sortRows(rows, 'name', 'asc');
  assert.deepEqual(byNameAsc.map((r) => r.key), ['a', 'b', 'c']);
  const byNameDesc = sortRows(rows, 'name', 'desc');
  assert.deepEqual(byNameDesc.map((r) => r.key), ['c', 'b', 'a']);
  // Original array untouched (returns a new array).
  assert.deepEqual(rows.map((r) => r.key), ['a', 'b', 'c']);
}
console.log('sortRows: string vs numeric columns + ties OK');

{
  // Numeric column with a null value must not crash and sorts as lowest.
  // `activeDays` (an actual number|null column), not `lastDate`: a string
  // column like lastDate makes `typeof av === 'string' || typeof bv ===
  // 'string'` true whenever the OTHER row holds a real date, so it was
  // silently exercising the localeCompare/string branch instead of the
  // numeric -Infinity one this test claims to cover.
  //
  // A NEGATIVE real value (-5), not a positive one: a placeholder of, say,
  // 0 instead of -Infinity would still coincidentally sort null "lowest"
  // against any non-negative real value, passing this test for the WRONG
  // reason. Only a genuine -Infinity placeholder correctly sorts null below
  // a negative number too — asserted both ascending and descending so the
  // ordering is pinned in both directions, not just the one direction that
  // happens to match array insertion order.
  const rows = [
    { key: 'a', name: 'Ana', activeDays: null },
    { key: 'b', name: 'Beto', activeDays: -5 },
  ];
  const asc = sortRows(rows, 'activeDays', 'asc');
  assert.deepEqual(asc.map((r) => r.key), ['a', 'b']); // null (-Infinity) sorts before -5
  const desc = sortRows(rows, 'activeDays', 'desc');
  assert.deepEqual(desc.map((r) => r.key), ['b', 'a']); // -5 sorts before null (-Infinity) descending
}
console.log('sortRows: null-value numeric column (negative pin, both directions) OK');

// ── buildTimeline ──────────────────────────────────────────────────────────

{
  // Empty inputs -> empty timeline, never throws.
  // CONTRATO CAMBIADO (W5/W8, plan): `offsets` es nuevo -- pre-range counts
  // que W8 usará para arrancar el acumulado en un valor > 0 cuando `from`
  // deja historia fuera de la ventana visible; con inputs vacíos ambos son 0.
  const result = buildTimeline({ stickers: [], surveys: [] });
  assert.deepEqual(result, {
    labels: [], stickers: [], surveys: [], stickersCumulative: [], surveysCumulative: [],
    offsets: { stickers: 0, surveys: 0 },
  });
}
console.log('buildTimeline: empty inputs OK');

{
  // Single day: exactly one label.
  const stickers = [{ inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-05', fase: 1 }];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-05']);
  assert.deepEqual(result.stickers, [1]);
  assert.deepEqual(result.surveys, [0]);
  assert.deepEqual(result.stickersCumulative, [1]);
  assert.deepEqual(result.surveysCumulative, [0]);
}
console.log('buildTimeline: single day OK');

{
  // Gap filling: records on day 1 and day 4 must yield days 1-4 with zeros between.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-04', fase: 1 },
  ];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04']);
  assert.deepEqual(result.stickers, [1, 0, 0, 1]);
  // Running total never resets on a zero-count day, and never double-counts.
  assert.deepEqual(result.stickersCumulative, [1, 1, 1, 2]);
  assert.deepEqual(result.surveysCumulative, [0, 0, 0, 0]);
}
console.log('buildTimeline: gap filling OK');

{
  // Cumulative totals combine BOTH sources independently and monotonically,
  // even when one source is quiet on a day the other is active.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 2 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-03', fase: 1 },
  ];
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-02' },
  ];
  const result = buildTimeline({ stickers, surveys });
  assert.deepEqual(result.labels, ['2026-01-01', '2026-01-02', '2026-01-03']);
  assert.deepEqual(result.stickers, [2, 0, 1]);
  assert.deepEqual(result.surveys, [0, 1, 0]);
  assert.deepEqual(result.stickersCumulative, [2, 2, 3]);
  assert.deepEqual(result.surveysCumulative, [0, 1, 1]);
}
console.log('buildTimeline: cumulative totals per source OK');

{
  // Sticker with null fecha never enters the timeline (only totals/KPIs count it).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: null, fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
  ];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-01']);
  assert.deepEqual(result.stickers, [1]);
}
console.log('buildTimeline: null-fecha sticker excluded from timeline OK');

{
  // SIN_DATO stickers (Fase never resolved) never enter the timeline either
  // -- same exclusion rule as buildProfessionalRows (#29).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' },
  ];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.stickers, [1]);
}
console.log('buildTimeline: SIN_DATO sticker excluded OK');

{
  // professionalKey filter: only that professional's dated records count.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: '2026-01-01', fase: 1 },
  ];
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-01' },
  ];
  const all = buildTimeline({ stickers, surveys, professionalKey: null });
  assert.deepEqual(all.stickers, [2]);
  assert.deepEqual(all.surveys, [1]);
  // CONTRATO CAMBIADO (D7): professionalKey ahora es un professionalKeyOf()
  // ('nom:'/'ced:' prefijado), nunca un normalizeName() a secas.
  const onlyGil = buildTimeline({ stickers, surveys, professionalKey: nomKey('Gil Soto') });
  assert.deepEqual(onlyGil.stickers, [1]);
  assert.deepEqual(onlyGil.surveys, [1]);
}
console.log('buildTimeline: professionalKey filter OK');

// ── buildTimeline: offsets (pre-range counts, W8 will consume them) ────────

{
  // `from` null -> offset is always 0 (no "before" an unbounded window).
  const stickers = [{ inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 }];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.offsets, { stickers: 0, surveys: 0 });
}
console.log('buildTimeline: offsets — from null OK');

{
  // Records strictly before `from` count toward the offset, not the series;
  // the cumulative running total starts FROM that offset, not from zero.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2025-12-30', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2025-12-31', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
  ];
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2025-12-31' },
  ];
  const result = buildTimeline({
    stickers, surveys, from: '2026-01-01', to: '2026-01-31',
  });
  assert.deepEqual(result.offsets, { stickers: 2, surveys: 1 });
  assert.deepEqual(result.labels, ['2026-01-01']);
  assert.deepEqual(result.stickers, [1]);
  assert.deepEqual(result.stickersCumulative, [3], 'offset (2) + day-1 daily (1) = 3');
  assert.deepEqual(result.surveysCumulative, [1], 'offset (1) + day-1 daily (0) = 1');
}
console.log('buildTimeline: offsets — pre-range records accumulate, cumulative starts from offset OK');

{
  // A record with no resolvable date contributes 0 to the offset (never
  // assumed to be "before" the range) even though a date filter is active.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: null, fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-05', fase: 1 },
  ];
  const result = buildTimeline({ stickers, surveys: [], from: '2026-01-01' });
  assert.deepEqual(result.offsets, { stickers: 0, surveys: 0 });
}
console.log('buildTimeline: offsets — undated record contributes 0 OK');

{
  // A record strictly AFTER `to` is excluded entirely -- not counted in the
  // offset either (it isn't "before" the window, it's beyond it).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2025-01-01', fase: 1 }, // before `from`
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-03-01', fase: 1 }, // after `to`
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-15', fase: 1 }, // in range
  ];
  const result = buildTimeline({
    stickers, surveys: [], from: '2026-01-01', to: '2026-01-31',
  });
  assert.deepEqual(result.offsets, { stickers: 1, surveys: 0 });
  assert.deepEqual(result.labels, ['2026-01-15']);
  assert.deepEqual(result.stickers, [1]);
}
console.log('buildTimeline: offsets — record after `to` excluded entirely OK');

{
  // Date range filter applied the same way as buildProfessionalRows.
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-02-01', fase: 1 },
  ];
  const result = buildTimeline({ stickers, surveys: [], from: '2026-02-01', to: '2026-02-28' });
  assert.deepEqual(result.labels, ['2026-02-01']);
  assert.deepEqual(result.stickers, [1]);
}
console.log('buildTimeline: date range filter OK');

{
  // N10: an inverted range (`to` < `from`) has no valid window at all --
  // labels must be empty (already true before this fix) AND offsets must be
  // {stickers: 0, surveys: 0} (the bug: `to < from` made every date <= `to`
  // satisfy both "not > to" and "< from", so it fell into the offset
  // instead of being excluded, producing a non-zero offset alongside empty
  // labels).
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-05', fase: 1 },
  ];
  const surveys = [{ nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-03' }];
  const result = buildTimeline({
    stickers, surveys, from: '2026-02-01', to: '2026-01-01',
  });
  assert.deepEqual(result.labels, []);
  assert.deepEqual(result.offsets, { stickers: 0, surveys: 0 }, 'an inverted range must never produce a non-zero offset');
}
console.log('buildTimeline: inverted range (to < from) -> empty labels AND zero offsets (N10) OK');

// ── buildTimeline: UTC-aware timestamp buckets to the BOGOTÁ calendar day ──
// CONTRATO CAMBIADO DELIBERADAMENTE (D6, plan §Zona horaria): antes este test
// comparaba contra la zona LOCAL de la máquina que corre el test (via `new
// Date(raw).getFullYear()/getMonth()/getDate()`); ahora dateOnly/bogotaParts
// usan el offset FIJO -05:00 de Bogotá, nunca la zona del proceso -- un admin
// con laptop en otra zona debe ver el MISMO día que uno en Cali. El valor
// esperado está escrito a mano (no recalculado con la misma aritmética que
// el código bajo prueba, para no ser una aserción tautológica): 02:00 UTC
// menos 5 horas = 21:00 del día ANTERIOR en Bogotá.
{
  const raw = '2026-01-02T02:00:00+00:00';
  const stickers = [{ inspector: { nombre_completo: 'Gil Soto' }, fecha: raw, fase: 1, fuente: 'atencionsismo' }];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-01'], 'debe ser el día anterior (Bogotá, UTC-5), no el día UTC');
}
console.log('buildTimeline: UTC-aware fecha buckets to the Bogotá calendar day OK');

{
  // An offset-less, time-less date string ("YYYY-MM-DD" only, Survey's own
  // fecha_inspeccion shape) must stay EXACTLY as written — no timezone
  // conversion applies to a value that never carried a time component.
  const stickers = [{ inspector: { nombre_completo: 'Gil Soto' }, fecha: '2026-01-05', fase: 1, fuente: 'atencionsismo' }];
  const result = buildTimeline({ stickers, surveys: [] });
  assert.deepEqual(result.labels, ['2026-01-05']);
}
console.log('buildTimeline: offset-less date-only string stays unchanged OK');

// ── professionalRecords: the raw points behind a professional's summary ────
// Per-row PDF report needs the actual stickers/surveys attributed to that
// professional ("los puntos recogidos"), not just the aggregate counts
// buildProfessionalRows already computes.

{
  const row = { key: nomKey('Gil Soto') };
  const result = professionalRecords(row, { stickers: [], surveys: [] });
  assert.deepEqual(result, { stickerPoints: [], surveyPoints: [] });
}
console.log('professionalRecords: empty inputs OK');

{
  // Only records matching the row's normalized name key are included —
  // same join rule as buildProfessionalRows/buildTimeline, case/accent-
  // insensitive.
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'GIL   sotó' }, codigo_edificacion: '76001-1-0010001', fecha: '2026-01-02', fase: 1 },
    { inspector: { nombre_completo: 'Ana Ruiz' }, codigo_edificacion: '76001-1-0010002', fecha: '2026-01-01', fase: 2 },
  ];
  const surveys = [
    { nombre_evaluador: 'gil soto', direccion: 'Cl 5 # 1-2', fecha_inspeccion: '2026-01-03' },
    { nombre_evaluador: 'Ana Ruiz', direccion: 'Cl 9 # 1-2', fecha_inspeccion: '2026-01-01' },
  ];
  const result = professionalRecords(row, { stickers, surveys });
  assert.equal(result.stickerPoints.length, 1);
  assert.equal(result.stickerPoints[0].codigo, '76001-1-0010001');
  assert.equal(result.surveyPoints.length, 1);
  assert.equal(result.surveyPoints[0].direccion, 'Cl 5 # 1-2');
}
console.log('professionalRecords: filters by normalized name (case/accent-insensitive) OK');

{
  // Chronological order, ascending; an undated sticker still appears (it IS
  // a real collected point, same reasoning as the KPI totals) but sorts
  // after every dated one.
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: '76001-1-0010003', fecha: '2026-01-03', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: '76001-1-0010001', fecha: null, fase: 2 },
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: '76001-1-0010002', fecha: '2026-01-01', fase: 2 },
  ];
  const result = professionalRecords(row, { stickers, surveys: [] });
  assert.deepEqual(result.stickerPoints.map((p) => p.codigo),
    ['76001-1-0010002', '76001-1-0010003', '76001-1-0010001']);
  assert.equal(result.stickerPoints[2].fecha, null);
}
console.log('professionalRecords: sticker points sorted chronologically, undated last OK');

{
  // Same chronological-ascending, undated-last rule for Survey points, keyed
  // off fecha_inspeccion (an invalid/garbage value counts as undated too).
  const row = { key: nomKey('Gil Soto') };
  const surveys = [
    { nombre_evaluador: 'Gil Soto', direccion: 'C', fecha_inspeccion: '2026-01-05' },
    { nombre_evaluador: 'Gil Soto', direccion: 'A', fecha_inspeccion: 'no-es-una-fecha' },
    { nombre_evaluador: 'Gil Soto', direccion: 'B', fecha_inspeccion: '2026-01-01' },
  ];
  const result = professionalRecords(row, { stickers: [], surveys });
  assert.deepEqual(result.surveyPoints.map((p) => p.direccion), ['B', 'C', 'A']);
}
console.log('professionalRecords: survey points sorted chronologically, invalid date last OK');

{
  // Fase label uses the SAME shared rule as buildProfessionalRows (faseKeyDe)
  // -- an atencionsismo sticker with fase:null still resolves via
  // inspector.np, never falls into a separate "sin fase" bucket here that
  // would disagree with the aggregate table above it in the same tab.
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', np: 'P4' }, codigo_edificacion: '76001-1-0010001', fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
  ];
  const result = professionalRecords(row, { stickers, surveys: [] });
  assert.equal(result.stickerPoints[0].faseLabel, 'Fase II');
}
console.log('professionalRecords: fase label matches faseKeyDe (inspector.np fallback) OK');

{
  // SIN_DATO stickers never show up as one of this professional's "puntos
  // recogidos" either -- same exclusion rule as buildProfessionalRows/
  // buildTimeline (#29).
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: '76001-1-0010001', fecha: '2026-01-01', fase: null, fuente: 'atencionsismo' },
  ];
  const result = professionalRecords(row, { stickers, surveys: [] });
  assert.deepEqual(result.stickerPoints, []);
}
console.log('professionalRecords: SIN_DATO sticker excluded entirely OK');

{
  // New optional from/to (W5, for the future per-period report, W10):
  // default null/null keeps the OLD "whole career, undated last" behavior
  // unchanged (already covered by every test above, none of which pass
  // from/to). When a period IS given, records are narrowed via the SAME
  // stickerIncluded/surveyIncluded rule used elsewhere -- an undated record
  // then drops out instead of being kept-and-sorted-last.
  const row = { key: nomKey('Gil Soto') };
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: 'A', fecha: '2026-01-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: 'B', fecha: '2026-02-01', fase: 1 },
    { inspector: { nombre_completo: 'Gil Soto' }, codigo_edificacion: 'C', fecha: null, fase: 1 },
  ];
  const noFilter = professionalRecords(row, { stickers, surveys: [] });
  assert.equal(noFilter.stickerPoints.length, 3, 'default (no period) keeps the undated point too');

  const filtered = professionalRecords(row, {
    stickers, surveys: [], from: '2026-01-01', to: '2026-01-31',
  });
  assert.deepEqual(filtered.stickerPoints.map((p) => p.codigo), ['A'], 'a period filter drops the out-of-range AND the undated point');
}
console.log('professionalRecords: optional from/to period filter (default = whole career) OK');

// ── W11: badgeStyleFor — sticker classification -> colored badge style ─────
// Reuses the app's OWN ATC-20/colorEtiqueta vocabulary (see utils.js
// KNOWN_LABELS/habitabilityColor and evaluaciones.js's color_etiqueta/
// clasificacion usage) instead of inventing a parallel one: colorEtiqueta
// ("Habitable" / "Acceso restringido" / "No habitable" / "Sin clasificación",
// the human label the atencionsismo API already gives) is the PRIMARY
// signal; clasificacion (ATC-20: inspeccionado/uso_restringido/
// peligro_colapso, or the backend's own INSPECCIONADA/USO_RESTRINGIDO/
// INSEGURO clase fallback) is the FALLBACK signal used only when
// colorEtiqueta is blank.
{
  const inspeccionado = { bg: '#e6f4ea', color: '#1a7d3a', label: 'Inspeccionado' };
  const restringido = { bg: '#fff3e0', color: '#b8730a', label: 'Con restricciones' };
  const inseguro = { bg: '#fce4ec', color: '#c62828', label: 'Inseguro' };
  const neutral = { bg: '#eeeeee', color: '#555555', label: 'Sin clasificar' };

  assert.deepEqual(badgeStyleFor('Habitable', ''), inspeccionado, 'colorEtiqueta "Habitable" -> green Inspeccionado badge');
  assert.deepEqual(badgeStyleFor('', 'inspeccionado'), inspeccionado, 'clasificacion "inspeccionado" (ATC-20) falls back correctly');
  assert.deepEqual(badgeStyleFor('', 'INSPECCIONADA'), inspeccionado, 'backend clase fallback "INSPECCIONADA" (uppercase, different suffix) still matches');
  assert.deepEqual(badgeStyleFor('HABITABLE', ''), inspeccionado, 'case-insensitive');

  assert.deepEqual(badgeStyleFor('Acceso restringido', ''), restringido, 'colorEtiqueta "Acceso restringido" -> orange badge');
  assert.deepEqual(badgeStyleFor('', 'uso_restringido'), restringido, 'clasificacion "uso_restringido" falls back correctly');
  assert.deepEqual(badgeStyleFor('', 'USO_RESTRINGIDO'), restringido, 'backend clase fallback, uppercase');
  assert.deepEqual(badgeStyleFor('Áccéso Réstringído', ''), restringido, 'accent-insensitive (normalize() strips diacritics)');

  assert.deepEqual(badgeStyleFor('No habitable', ''), inseguro, 'colorEtiqueta "No habitable" -> red badge');
  assert.deepEqual(badgeStyleFor('', 'peligro_colapso'), inseguro, 'clasificacion "peligro_colapso" (ATC-20) falls back correctly');
  assert.deepEqual(badgeStyleFor('', 'INSEGURO'), inseguro, 'backend clase fallback "INSEGURO"');

  assert.deepEqual(badgeStyleFor('Sin clasificación', ''), neutral, 'the API\'s own "no classification yet" label never throws, renders neutral');
  assert.deepEqual(badgeStyleFor('', ''), neutral, 'both blank -> neutral fallback, never a crash');
  assert.deepEqual(badgeStyleFor(null, null), neutral, 'null input -> neutral fallback');
  assert.deepEqual(badgeStyleFor(undefined, undefined), neutral, 'undefined input -> neutral fallback');
  assert.deepEqual(badgeStyleFor('algo-desconocido', 'algo-desconocido'), neutral, 'an unrecognized value never throws, renders neutral');
}
console.log('badgeStyleFor: maps colorEtiqueta/clasificacion to the app\'s own ATC-20 badge vocabulary OK');

// ── H1/M4: badgeStyleFor priority — clasificacion is AUTHORITATIVE, not
// colorEtiqueta. The backend (stickers_atencionsismo.py) sets `clasificacion`
// from a matched Firestore evaluación when one exists, which can OVERRIDE a
// stale `colorEtiqueta` from the raw API; evaluaciones.js's claseDe (what the
// Stickers tab actually renders) reads `clasificacion` ONLY. A report that
// prioritized colorEtiqueta could show the SAFER of two contradictory
// classifications for the same sticker — dangerous for a disaster-response
// tool. M4 (bundled): clasificacion is normalized with the same
// `.replace(/[\s-]+/g, '_')` the two EXISTING readers of this field already
// apply (evaluaciones.js's claseDe, report.js's evalClaseLabel), so
// space/hyphen drift on this field is treated as noise here too. ───────────
{
  const restringido = { bg: '#fff3e0', color: '#b8730a', label: 'Con restricciones' };
  const inseguro = { bg: '#fce4ec', color: '#c62828', label: 'Inseguro' };

  // H1: a conflicting pair (colorEtiqueta says safe, clasificacion says
  // unsafe) must resolve to clasificacion's RED "Inseguro" badge, matching
  // what the Stickers tab shows for the same sticker — never the green
  // badge just because colorEtiqueta happens to be checked first.
  assert.deepEqual(
    badgeStyleFor('Habitable', 'INSEGURO'),
    inseguro,
    'H1: clasificacion is authoritative — a conflicting colorEtiqueta must never win',
  );

  // M4: space/hyphen drift on clasificacion must not fall through to neutral.
  assert.deepEqual(badgeStyleFor('', 'Uso restringido'), restringido, 'M4: space-separated clasificacion variant matches');
  assert.deepEqual(badgeStyleFor('', 'uso-restringido'), restringido, 'M4: hyphenated clasificacion variant matches');
  assert.deepEqual(badgeStyleFor('', 'uso_restringido'), restringido, 'M4: underscore clasificacion variant (baseline) matches');
}
console.log('badgeStyleFor (H1/M4): clasificacion is authoritative over colorEtiqueta; space/hyphen drift normalized OK');

// ── W11: badgeCell — pure pdfmake cell for one colored classification badge ─
{
  const style = badgeStyleFor('Habitable', '');
  const cell = badgeCell(style.label, style);
  assert.equal(cell.text, 'Inspeccionado');
  assert.equal(cell.color, '#1a7d3a');
  assert.equal(cell.fillColor, '#e6f4ea');
  assert.equal(cell.alignment, 'center');
}
console.log('badgeCell: pure pdfmake cell shape (text/color/fillColor/alignment) OK');

// ── W11: statCard — one colored KPI card, plain or highlighted ─────────────
{
  const plain = statCard('Visitas totales en el período', 6);
  assert.ok(plain.table, 'must be a pdfmake table node (so it renders as a bordered box)');
  const plainStack = plain.table.body[0][0].stack;
  assert.equal(plainStack.length, 2, 'no caption -> label + value only, no 3rd stack node');
  const plainText = JSON.stringify(plain);
  assert.ok(plainText.includes('Visitas totales en el período'));
  assert.ok(plainText.includes('"6"'), 'the value must render as its own text node');
  assert.ok(plainText.includes('#f0f2f7'), 'a plain card uses the neutral gray card background');
  assert.ok(!plainText.includes('#e8f0fc'), 'a plain (non-accent) card never uses the accent blue background');

  const accentCard = statCard('Visita objetivo diario', 2.5, {
    accent: true, caption: 'Proyectado al 30 de septiembre de 2026',
  });
  const accentText = JSON.stringify(accentCard);
  assert.equal(accentCard.table.body[0][0].stack.length, 3, 'a caption adds a 3rd stack node');
  assert.ok(accentText.includes('#e8f0fc'), 'accent card uses the highlighted blue background from the mockups');
  assert.ok(accentText.includes('#2186E0'), 'accent card value text uses the blue accent color');
  assert.ok(accentText.includes('Proyectado al 30 de septiembre de 2026'), 'the caption renders when given');
}
console.log('statCard: plain vs accent card shape (background/text color, optional caption) OK');

// ── W11: statCardsRow — chunks N cards into pdfmake `columns` rows ─────────
{
  const mk = (n) => Array.from({ length: n }, (_, i) => statCard(`L${i}`, i));
  const two = statCardsRow(mk(2));
  assert.equal(two.length, 1);
  assert.equal(two[0].columns.length, 2);

  const three = statCardsRow(mk(3));
  assert.equal(three.length, 1);
  assert.equal(three[0].columns.length, 3);

  const five = statCardsRow(mk(5));
  assert.equal(five.length, 2, '5 cards at 3/row -> 2 rows');
  assert.equal(five[0].columns.length, 3);
  assert.equal(five[1].columns.length, 2, 'the last row holds the remainder, never padded with empty cells');

  const six = statCardsRow(mk(6));
  assert.equal(six.length, 2, '6 cards at 3/row -> exactly 2 full rows');
  assert.equal(six[0].columns.length, 3);
  assert.equal(six[1].columns.length, 3);
}
console.log('statCardsRow: chunks 2/3/5/6 cards into proper columns rows (3/row) OK');

// ── L9: statCardsRow — a trailing partial row must not render WIDER cards
// than the full rows above it. pdfmake defaults a `columns` entry with no
// explicit `width` to `'*'` (share of remaining space), so a 2-card final
// row (perRow=3) used to stretch those 2 cards wider than the 3-card full
// rows above — visually inconsistent. Every card, in every row, must now
// carry the SAME explicit width. ────────────────────────────────────────
{
  const mk = (n) => Array.from({ length: n }, (_, i) => statCard(`L${i}`, i));
  const four = statCardsRow(mk(4), 3);
  assert.equal(four.length, 2, '4 cards at perRow=3 -> 2 rows (3 + 1)');
  const widths4 = four.flatMap((r) => r.columns.map((c) => c.width));
  assert.ok(widths4.every((w) => w !== undefined), 'L9: every card must carry an explicit width');
  assert.ok(widths4.every((w) => w === widths4[0]), `L9: every card (incl. the trailing partial row) must share the same width (got ${JSON.stringify(widths4)})`);

  const seven = statCardsRow(mk(7), 3);
  assert.equal(seven.length, 3, '7 cards at perRow=3 -> 3 rows (3 + 3 + 1)');
  const widths7 = seven.flatMap((r) => r.columns.map((c) => c.width));
  assert.ok(widths7.every((w) => w === widths7[0]), `L9: every card across all rows must share the same width (got ${JSON.stringify(widths7)})`);
}
console.log('statCardsRow (L9): trailing partial row cards share the same width as full rows OK');

// ── W11: sectionHeaderNode — section title + colored bottom rule ──────────
{
  const node = sectionHeaderNode('Datos del profesional');
  assert.ok(Array.isArray(node), 'must be a stack-like array, spreadable into `content` like kvTable/pointsTable');
  assert.equal(node[0].text, 'Datos del profesional');
  assert.equal(node[0].style, 'sectionHeader');
  assert.ok(Array.isArray(node[1].canvas), 'the 2nd node must be a canvas rule');
  assert.equal(node[1].canvas[0].lineColor, '#151F55', 'the rule matches the mockups\' border-bottom:2px solid #151F55');
}
console.log('sectionHeaderNode: text + colored bottom rule (mirrors both mockups\' header style) OK');

// ── W11: buildActivitySparkline — the new graphical element (neither mockup
// had this): a bounded, deterministic daily-activity bar chart built from
// stickerPoints' own `fecha` (already a Bogotá calendar day per
// professionalRecords/toStickerPoint — dateOnly()/bogotaParts() already ran,
// so this never re-derives timezone logic). ──────────────────────────────
{
  // 0 stickers -> never an empty/broken canvas, an explanatory message instead.
  const empty = buildActivitySparkline([]);
  assert.ok(!empty.canvas, 'no data at all -> no canvas node');
  assert.ok(/sin actividad/i.test(empty.text), 'must explain there is nothing to graph');

  // Every point has an unresolvable fecha -> same "no data" fallback, never a throw.
  const onlyBlankDates = buildActivitySparkline([{ fecha: null }, { fecha: '' }]);
  assert.ok(/sin actividad/i.test(onlyBlankDates.text));

  // Single day -> exactly 1 bar, tall enough to read as "real activity".
  const single = buildActivitySparkline([{ fecha: '2026-01-01' }, { fecha: '2026-01-01' }]);
  assert.ok(Array.isArray(single.canvas));
  assert.equal(single.canvas.length, 1);
  assert.equal(single.canvas[0].type, 'rect');
  assert.equal(single.canvas[0].color, COLORS.accent, 'bars use the app\'s own accent color, not an invented one');
  assert.ok(single.canvas[0].h >= 2, 'a real (non-zero) count renders taller than the zero-count baseline tick');

  // A zero-count day in the MIDDLE of the range must still get its own bar
  // (a 1px baseline tick) — never silently skipped, which would misread as
  // "no data that day" instead of "confirmed zero that day".
  const gapPoints = [{ fecha: '2026-01-01' }, { fecha: '2026-01-03' }]; // 01-02 has zero
  const withGap = buildActivitySparkline(gapPoints);
  assert.equal(withGap.canvas.length, 3, 'the zero-count middle day must still produce a bar, not a gap');
  assert.equal(withGap.canvas[1].h, 1, 'a zero-count day renders the minimum baseline tick');
  assert.ok(withGap.canvas[0].h > withGap.canvas[1].h, 'a real day must still look taller than a zero-count day');

  // A run longer than maxBars must aggregate into bounded buckets, never an
  // ever-growing canvas for a professional active across many months.
  function ymd(offsetDays) {
    const d = new Date(Date.UTC(2026, 0, 1) + offsetDays * 86400000);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
  }
  const longPoints = Array.from({ length: 45 }, (_, i) => ({ fecha: ymd(i) }));
  const long = buildActivitySparkline(longPoints, { maxBars: 30 });
  assert.ok(Array.isArray(long.canvas));
  assert.ok(long.canvas.length <= 30, `45 distinct days with maxBars=30 must aggregate (got ${long.canvas.length} buckets)`);
  assert.ok(long.canvas.length > 1);
  for (const rect of long.canvas) {
    assert.ok(Number.isFinite(rect.h) && rect.h >= 1, 'every bucket must have a finite, positive height');
    assert.ok(Number.isFinite(rect.x) && Number.isFinite(rect.w) && rect.w > 0, 'every bucket must have a valid, non-zero width');
  }
}
console.log('buildActivitySparkline: empty/single-day/zero-gap/aggregation-over-maxBars all render safely OK');

// ── H2/L8/M5: buildActivitySparkline — rate-based buckets (not raw sums),
// index-based bucket boundaries (not a fixed Math.ceil bucket SIZE), and an
// exposed `bucketSize` for the caller's caption. ───────────────────────────
{
  function ymdFrom(base, offsetDays) {
    const d = new Date(Date.UTC(base, 0, 1) + offsetDays * 86400000);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
  }

  // H2: a perfectly constant daily rate (4 stickers/day for 45 days) must
  // produce EQUAL bar heights across ALL buckets, including the final one —
  // the old sum-based last bucket (a shorter remainder) read as "output
  // collapsed" when nothing actually changed.
  const constantPoints = [];
  for (let day = 0; day < 45; day += 1) {
    for (let k = 0; k < 4; k += 1) constantPoints.push({ fecha: ymdFrom(2026, day) });
  }
  const constant = buildActivitySparkline(constantPoints, { maxBars: 30 });
  assert.equal(constant.canvas.length, 30, 'L8: 45 days at maxBars=30 must still fill all 30 buckets');
  const heights = constant.canvas.map((r) => r.h);
  for (const h of heights) {
    assert.equal(h, heights[0], `H2: every bucket (incl. the last) must be the same height for a constant rate (got ${JSON.stringify(heights)})`);
  }
  // M5: exposed effective days-per-bar for the caller's caption.
  assert.equal(constant.bucketSize, 2, '45 days over 30 buckets -> ~1.5 days/bucket, rounds to 2');

  const single = buildActivitySparkline([{ fecha: '2026-01-01' }], { maxBars: 30 });
  assert.equal(single.bucketSize, 1, 'M5: a 1-day range must report bucketSize 1 (one day per bar)');

  // L8: the resolution "cliff" — 31 days used to collapse to 16 bars
  // (ceil(31/30)=2 forces bucketSize 2 for EVERY bucket); index-based
  // boundaries must still fill all 30 bars.
  const points31 = Array.from({ length: 31 }, (_, i) => ({ fecha: ymdFrom(2026, i) }));
  const withL8 = buildActivitySparkline(points31, { maxBars: 30 });
  assert.equal(withL8.canvas.length, 30, `L8: 31 days at maxBars=30 must yield 30 bars, not 16 (got ${withL8.canvas.length})`);

  // L8 (count conservation): a spike on the very LAST day of a range longer
  // than maxBars must land in the last bucket, not be silently dropped at a
  // boundary miscalculation — proves the index-based partition covers every
  // day exactly once, all the way to the end.
  const spikePoints = [{ fecha: ymdFrom(2026, 0) }, ...Array.from({ length: 100 }, () => ({ fecha: ymdFrom(2026, 30) }))];
  const spike = buildActivitySparkline(spikePoints, { maxBars: 30 });
  assert.equal(spike.canvas.length, 30);
  const spikeHeights = spike.canvas.map((r) => r.h);
  const maxSpikeH = Math.max(...spikeHeights);
  assert.equal(spikeHeights[spikeHeights.length - 1], maxSpikeH, 'L8: a spike on the last day must land in the last bucket, never dropped at the boundary');
  assert.ok(maxSpikeH > Math.min(...spikeHeights) * 5, 'the spike must be clearly distinguishable from baseline buckets');
}
console.log('buildActivitySparkline (H2/L8/M5): rate-based equal-height buckets, smooth index-based bucket count, bucketSize exposed OK');

// ── L10: buildActivitySparkline must not hang/allocate a huge array when a
// malformed `fecha` is far outside any sane range (e.g. a stray
// "9999-12-31") — this now runs ONCE PER PROFESSIONAL in the mass export
// (up to ~110x), unlike the pre-existing single global buildTimeline call.
{
  const start = Date.now();
  const degenerate = buildActivitySparkline([
    { fecha: '2026-01-01' },
    { fecha: '9999-12-31' },
  ]);
  const elapsed = Date.now() - start;
  assert.ok(elapsed < 2000, `L10: must return quickly even with a degenerate date span (took ${elapsed}ms)`);
  assert.ok(!degenerate.canvas || degenerate.canvas.length <= 30, 'L10: must never produce an unbounded canvas');
}
console.log('buildActivitySparkline (L10): a degenerate far-future fecha never hangs or allocates an enormous array OK');

// ── buildProfessionalReportDocDefinition ────────────────────────────────────

{
  const row = {
    name: 'Gil Soto', cedula: '123', codigo: '004', entidad: 'DAGMA', tarjetaProfesional: 'TP-9988',
    stickersFase1: 2, stickersFase2: 1, surveyTotal: 3, total: 6,
    firstDate: '2026-01-01', lastDate: '2026-01-05', activeDays: 3, avgPerActiveDay: 2, rosterSourced: 0,
  };
  const points = {
    stickerPoints: [
      {
        codigo: '76001-1-0010001', direccion: 'Cl 5 # 1-2', municipio: 'Cali', fecha: '2026-01-01', faseLabel: 'Fase I', colorEtiqueta: 'Habitable', clasificacion: '',
      },
    ],
    surveyPoints: [
      { direccion: 'Cl 9 # 1-2', nombreEdificacion: 'Casa', fecha: '2026-01-05' },
    ],
  };
  const doc = buildProfessionalReportDocDefinition(row, points);
  const flatText = JSON.stringify(doc.content);
  assert.ok(flatText.includes('Gil Soto'), 'header should show the professional name');
  assert.ok(flatText.includes('76001-1-0010001'), 'should list the sticker code');
  assert.ok(flatText.includes('Cl 9 # 1-2'), 'should list the survey address');
  assert.ok(/Fecha de generaci/i.test(flatText), 'should include a generation-date label');
  // W11: TP prominence — visible in the subtitle line (glanceable), not only
  // buried inside "Datos del profesional" further down the page.
  assert.ok(/TP:\s*TP-9988/.test(flatText), 'the tarjeta profesional must be shown inline in the subtitle line');
  // W11: the sticker's colorEtiqueta ("Habitable") renders as the
  // "Inspeccionado" colored badge instead of plain text.
  assert.ok(flatText.includes('Inspeccionado'), 'a Habitable sticker must render the green "Inspeccionado" badge label');
  // W11 (Borrador mockup): the uncapped total, right after the sticker table.
  assert.ok(flatText.includes('Total stickers en período: 1'), 'must show the UNCAPPED sticker total');
  // W11: the new graphical element neither mockup had.
  assert.ok(flatText.includes('Actividad en el período'), 'must include the new activity sparkline section');
}
console.log('buildProfessionalReportDocDefinition: includes header, stats and points OK');

// ── M5: the sparkline caption reflects the actual bucket size — plain
// "Promedio diario de stickers." for a 1-day-per-bar range, an explicit
// "(agrupado cada N días)" note when buildActivitySparkline aggregated
// multiple days per bar. ─────────────────────────────────────────────────
{
  const row = {
    name: 'Gil Soto', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 1, stickersFase2: 0, surveyTotal: 0, total: 1,
    firstDate: '2026-01-01', lastDate: '2026-01-01', activeDays: 1, avgPerActiveDay: 1,
    avgStickersPerDay: 1, rosterSourced: 0,
  };
  // Look up the caption node by its TOP-LEVEL `.text` field directly in
  // `doc.content` (not a flattened JSON string search) — the KPI stat card
  // labeled "Promedio diario de stickers" is nested several levels deep
  // inside a `statCardsRow` table/stack, so a naive substring search across
  // the whole flattened content would trivially "pass" via that unrelated
  // card regardless of what the sparkline caption actually says.
  const oneDayPoints = { stickerPoints: [{ codigo: 'A1', direccion: '', municipio: '', fecha: '2026-01-01', faseLabel: 'Fase I' }], surveyPoints: [] };
  const oneDayDoc = buildProfessionalReportDocDefinition(row, oneDayPoints, {});
  const oneDayCaption = oneDayDoc.content.find((n) => n && typeof n.text === 'string' && /Promedio diario de stickers/.test(n.text));
  assert.ok(oneDayCaption, 'M5: a 1-day-per-bar range must render the "Promedio diario de stickers" caption as its own content node');
  assert.ok(!/agrupado cada/i.test(oneDayCaption.text), 'M5: a 1-day-per-bar range must NOT claim a multi-day grouping');

  function ymdFrom(base, offsetDays) {
    const d = new Date(Date.UTC(base, 0, 1) + offsetDays * 86400000);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
  }
  const manyDayPoints = Array.from({ length: 45 }, (_, i) => ({
    codigo: `COD-${i}`, direccion: 'x', municipio: 'Cali', fecha: ymdFrom(2026, i), faseLabel: 'Fase I',
  }));
  const rowLong = { ...row, firstDate: '2026-01-01', lastDate: ymdFrom(2026, 44) };
  const manyDayDoc = buildProfessionalReportDocDefinition(rowLong, { stickerPoints: manyDayPoints, surveyPoints: [] }, {});
  const manyDayCaption = manyDayDoc.content.find((n) => n && typeof n.text === 'string' && /Promedio diario de stickers/.test(n.text));
  assert.ok(manyDayCaption, 'M5: the multi-day-per-bar range must still render a "Promedio diario de stickers" caption node');
  assert.ok(/agrupado cada 2 d.as/i.test(manyDayCaption.text), 'M5: a multi-day-per-bar range must state the effective bucket size in the caption');
}
console.log('buildProfessionalReportDocDefinition (M5): sparkline caption reflects effective bucketSize OK');

{
  // A professional with zero points in one or both sources must not crash
  // the builder -- it should say so in plain text, not render an empty table.
  const row = {
    name: 'Sin Puntos', cedula: '', codigo: '', entidad: '',
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0, rosterSourced: 0,
  };
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] });
  const flatText = JSON.stringify(doc.content);
  assert.ok(/sin registros/i.test(flatText), 'should say there are no points for an empty source');
}
console.log('buildProfessionalReportDocDefinition: empty point lists render a plain message OK');

{
  // A roster-sourced identity (approximate, per the cross-source caveat) is
  // flagged in the report the same way the table row flags it.
  const row = {
    name: 'Gil Soto', cedula: '', codigo: '', entidad: '',
    stickersFase1: 1, stickersFase2: 0, surveyTotal: 0, total: 1,
    firstDate: '2026-01-01', lastDate: '2026-01-01', activeDays: 1, avgPerActiveDay: 1, rosterSourced: 1,
  };
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] });
  const flatText = JSON.stringify(doc.content);
  assert.ok(/roster/i.test(flatText), 'should carry the roster-sourced caveat');
}
console.log('buildProfessionalReportDocDefinition: roster-sourced caveat included OK');

// ── hasActiveSegFilters: drives "Reiniciar filtros"' enabled/disabled state.
// Search, Desde/Hasta AND seg-chart-professional all narrow the table (W7) —
// only sort order stays excluded (never a data-narrowing filter). ──────────
assert.equal(hasActiveSegFilters(), false, 'no args at all -> nothing active');
assert.equal(hasActiveSegFilters({}), false, 'an empty object -> nothing active');
assert.equal(hasActiveSegFilters({ search: '', from: null, to: null }), false, 'the default all-empty/null shape has nothing active');
assert.equal(hasActiveSegFilters({ search: 'ana', from: null, to: null }), true, 'a non-empty search is active');
assert.equal(hasActiveSegFilters({ search: '', from: '2026-01-01', to: null }), true, 'a Desde date alone is active');
assert.equal(hasActiveSegFilters({ search: '', from: null, to: '2026-01-31' }), true, 'a Hasta date alone is active');
assert.equal(hasActiveSegFilters({ search: '', from: '2026-01-01', to: '2026-01-31' }), true, 'both dates active');
// Clearing the LAST active filter by hand must flip back to false — the real
// transition the reset button's disabled state relies on.
{
  const f = { search: '', from: '2026-01-01', to: null };
  assert.equal(hasActiveSegFilters(f), true);
  f.from = null;
  assert.equal(hasActiveSegFilters(f), false, 'clearing the only active filter (from) flips back to inactive');
}

console.log('seguimiento.test.mjs: hasActiveSegFilters OK');

// ── createSegCache: single-entry memo (W6) ──────────────────────────────────
// Key: strict identity (===) of the stickers/surveys arrays + string
// equality of from/to/professionalKey/today. A single entry -- never a Map
// -- so there is structurally never more than 1 cached result no matter how
// many distinct ranges are requested.

{
  // Same key (identical references + equal strings) -> compute() runs ONCE;
  // a second .get() with the SAME key returns the cached result.
  const cache = createSegCache();
  const stickers = [];
  const surveys = [];
  let calls = 0;
  const key = {
    stickers, surveys, from: '2026-01-01', to: '2026-01-31', professionalKey: null, today: '2026-01-15',
  };
  const r1 = cache.get(key, () => { calls += 1; return { n: calls }; });
  const r2 = cache.get({ ...key }, () => { calls += 1; return { n: calls }; });
  assert.equal(calls, 1, 'compute() must run exactly once for two .get() calls with the same key');
  assert.equal(r1, r2, 'the SECOND call must return the exact cached object, not a new one');
}
console.log('createSegCache: same key -> single execution, cached result returned OK');

{
  // A NEW array reference with EQUAL contents must still recompute --
  // identity (===), never a deep-equality/content comparison.
  const cache = createSegCache();
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  const stickersA = [{ a: 1 }];
  const stickersB = [{ a: 1 }]; // same shape, different reference
  cache.get({
    stickers: stickersA, surveys: [], from: null, to: null, professionalKey: null, today: '2026-01-15',
  }, compute);
  cache.get({
    stickers: stickersB, surveys: [], from: null, to: null, professionalKey: null, today: '2026-01-15',
  }, compute);
  assert.equal(calls, 2, 'a new array reference (even with identical contents) must recompute');
}
console.log('createSegCache: new array identity (equal contents) recomputes OK');

{
  // Changing `to` (a string field of the key) must recompute too.
  const cache = createSegCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  cache.get({
    stickers, surveys, from: '2026-01-01', to: '2026-01-31', professionalKey: null, today: '2026-01-15',
  }, compute);
  cache.get({
    stickers, surveys, from: '2026-01-01', to: '2026-02-28', professionalKey: null, today: '2026-01-15',
  }, compute);
  assert.equal(calls, 2, 'changing `to` must invalidate the single cached entry');
}
console.log('createSegCache: changing `to` recomputes OK');

{
  // <=1 entry after 50 DIFFERENT ranges -- a single-entry cache structurally
  // cannot grow, unlike a Map keyed by range that would leak one entry per
  // distinct range ever requested.
  const cache = createSegCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  for (let i = 0; i < 50; i++) {
    cache.get({
      stickers, surveys, from: `2026-01-${String(i + 1).padStart(2, '0')}`, to: null, professionalKey: null, today: '2026-01-15',
    }, () => { calls += 1; return { n: calls }; });
  }
  assert.equal(calls, 50, 'every distinct range really did recompute (not silently cached under the wrong key)');
  // L7: a REAL size() (0 or 1), not a vacuous `cache.size ? cache.size() : 1`
  // fallback that always passes regardless of what the cache actually holds.
  assert.equal(typeof cache.size, 'function', 'createSegCache must expose a real size() method');
  assert.equal(cache.size(), 1, 'a single-entry cache holds exactly 1 entry after 50 distinct ranges, never more');
}
console.log('createSegCache: <=1 entry after 50 ranges OK');

{
  // clear() drops the cached entry -- the next .get() (even with the SAME
  // key) must recompute, same as initSeguimiento()'s guard against a stale
  // cache surviving a fresh open.
  const cache = createSegCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  const key = {
    stickers, surveys, from: null, to: null, professionalKey: null, today: '2026-01-15',
  };
  cache.get(key, () => { calls += 1; return { n: calls }; });
  cache.clear();
  cache.get(key, () => { calls += 1; return { n: calls }; });
  assert.equal(calls, 2, 'clear() must force the next .get() (even with an identical key) to recompute');
}
console.log('createSegCache: clear() forces recompute OK');

// ── createIdentityCache (M4): single-entry memo keyed on stickers/surveys ──
// array identity, independent of segCache's own (from/to/professionalKey/
// today) key -- render() used to call buildIdentityIndex() UNCONDITIONALLY
// before the segCache memo check even ran (7.6 ms every render, regardless
// of whether segCache itself would hit), even though its only real inputs
// are the exact same stickers/surveys array references segCache already
// keys on.

{
  // Same stickers/surveys refs -> compute() runs ONCE across two .get() calls.
  const cache = createIdentityCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  const r1 = cache.get(stickers, surveys, compute);
  const r2 = cache.get(stickers, surveys, compute);
  assert.equal(calls, 1, 'compute() must run once for two .get() calls with identical stickers/surveys refs');
  assert.equal(r1, r2, 'the second call must return the exact cached object, not a new one');
}
console.log('createIdentityCache: same stickers/surveys refs -> single execution OK');

{
  // A NEW array reference (even with identical/empty contents) must recompute.
  const cache = createIdentityCache();
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  const surveys = [];
  cache.get([], surveys, compute);
  cache.get([], surveys, compute);
  assert.equal(calls, 2, 'a new stickers array reference must recompute, even with identical (empty) contents');
}
console.log('createIdentityCache: new array reference recomputes OK');

{
  // clear() forces the next .get() (even with the SAME refs) to recompute.
  const cache = createIdentityCache();
  const stickers = []; const surveys = [];
  let calls = 0;
  const compute = () => { calls += 1; return { n: calls }; };
  cache.get(stickers, surveys, compute);
  cache.clear();
  cache.get(stickers, surveys, compute);
  assert.equal(calls, 2, 'clear() must force the next .get() to recompute');
}
console.log('createIdentityCache: clear() forces recompute OK');

// ── makeSearchController: debounce() wrapper with a working cancel() ────────

{
  // trigger() schedules the callback; a second trigger() before it fires
  // resets the timer (debounce semantics) -- only the LAST scheduled call
  // actually runs.
  const calls = [];
  const controller = makeSearchController((label) => calls.push(label), 20);
  controller.trigger('a');
  controller.trigger('b');
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.deepEqual(calls, ['b'], 'only the last trigger() before the wait must fire');
}
console.log('makeSearchController: trigger() debounces, only the last call fires OK');

{
  // cancel() prevents a PENDING callback from ever firing -- the real
  // scenario this exists for: a filter reset or a fresh init must be able to
  // kill a stale debounced render before it stomps new state.
  const calls = [];
  const controller = makeSearchController(() => calls.push('fired'), 20);
  controller.trigger();
  controller.cancel();
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.deepEqual(calls, [], 'cancel() must prevent the pending callback from firing at all');
}
console.log('makeSearchController: cancel() prevents a pending callback OK');

{
  // cancel() with nothing pending is a harmless no-op, and a NEW trigger()
  // after a cancel() schedules normally (cancel does not permanently disable
  // the controller).
  const calls = [];
  const controller = makeSearchController(() => calls.push('fired'), 15);
  controller.cancel(); // nothing pending -- must not throw
  controller.trigger();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.deepEqual(calls, ['fired'], 'a trigger() after a no-op cancel() still fires normally');
}
console.log('makeSearchController: cancel() with nothing pending is a no-op; later trigger() still fires OK');

// ── W7: kpiTotals ────────────────────────────────────────────────────────
// The 5 KPI tiles' values, derived from a buildProfessionalRows() result —
// "sin profesional identificado" and "stickers sin fecha" are no longer
// tiles (W7, plan), replaced by barrios activos (7 d) and the promedio
// tile now being STICKER-pace-specific (see doc comment on kpiTotals itself).

{
  // Empty rows -> everything zero, never NaN/throw.
  const result = { rows: [], totals: { professionals: 0, stickers: 0, surveys: 0 } };
  const t = kpiTotals(result, { stickersLoaded: true });
  assert.deepEqual(t, {
    professionals: 0, stickers: 0, surveys: 0, avgStickersPerDayPerProfessional: 0, barriosActivos: 0,
  });
}
console.log('kpiTotals: empty rows OK');

{
  // H3/L7: stickersLoaded=false masks EVERY sticker-derived tile behind DASH
  // — stickers total, the per-professional daily-pace average, AND (unlike
  // the old behavior) `professionals`/`barriosActivos` too:
  //  - `barriosActivos` is derived entirely from stickers (buildBarriosActivos
  //    reads `row.barriosActivos`, itself built from sticker `barrio`s) — a
  //    real 0 while stickers are still loading/failed would read as
  //    "confirmed zero active barrios", not "unknown".
  //  - `professionals` (= rows.length from buildProfessionalRows) is a MIX of
  //    professionals identified via stickers AND via Survey-only records — a
  //    row built while stickers haven't resolved yet only reflects the
  //    Survey-only subset, a partial count masquerading as the real total.
  // Only `surveys` is genuinely never sticker-derived and stays unmasked.
  const result = {
    rows: [{ stickersTotal: 4, activeDays: 2, barriosActivos: ['San Antonio'] }],
    totals: { professionals: 1, stickers: 4, surveys: 0 },
  };
  const t = kpiTotals(result, { stickersLoaded: false });
  assert.equal(t.stickers, DASH);
  assert.equal(t.avgStickersPerDayPerProfessional, DASH);
  assert.equal(t.barriosActivos, DASH, 'H3: barriosActivos is sticker-derived, must be DASH-masked too');
  assert.equal(t.professionals, DASH, 'L7: professionals is a partial (sticker-loading-dependent) count while !stickersLoaded, must be DASH-masked too');
  // Survey count is NOT sticker-derived — never masked.
  assert.equal(t.surveys, 0);
}
console.log('kpiTotals: stickersLoaded=false masks every sticker-derived tile (stickers/pace/barriosActivos/professionals) behind DASH (H3/L7) OK');

{
  // M6: avgStickersPerDayPerProfessional = mean, ACROSS PROFESSIONALS WITH AT
  // LEAST ONE STICKER-ACTIVE DAY, of each row's own stickersTotal/
  // stickerActiveDays — NOT stickersAssigned / Σ stickerActiveDays (a single
  // pooled average would let one very active professional dominate the
  // tile), and a professional with ZERO sticker-active days is EXCLUDED from
  // the mean entirely (never folded in as a 0 that drags the average down —
  // that would understate the pace of everyone who IS actually placing
  // stickers just because a survey-only colleague exists).
  const result = {
    rows: [
      { stickersTotal: 10, stickerActiveDays: 2, barriosActivos: [] }, // 5/day
      { stickersTotal: 3, stickerActiveDays: 3, barriosActivos: [] }, // 1/day
      { stickersTotal: 0, stickerActiveDays: 0, barriosActivos: [] }, // no sticker-active day -> excluded, not a 0
    ],
    totals: { professionals: 3, stickers: 13, surveys: 0 },
  };
  const t = kpiTotals(result, { stickersLoaded: true });
  // (5 + 1) / 2 = 3 -- the zero-pace professional never enters the mean.
  assert.equal(t.avgStickersPerDayPerProfessional, 3);
}
console.log('kpiTotals: avgStickersPerDayPerProfessional is the mean over professionals WITH sticker activity only (M6) OK');

{
  // M6: every professional has zero sticker-active days (survey-only, or
  // truly zero stickers) -> DASH, never a fake 0 (there IS no sticker pace to
  // report, distinct from "confirmed zero pace").
  const result = {
    rows: [
      { stickersTotal: 0, stickerActiveDays: 0, barriosActivos: [] },
      { stickersTotal: 0, stickerActiveDays: 0, barriosActivos: [] },
    ],
    totals: { professionals: 2, stickers: 0, surveys: 5 },
  };
  const t = kpiTotals(result, { stickersLoaded: true });
  assert.equal(t.avgStickersPerDayPerProfessional, DASH, 'M6: no professional with any sticker-active day -> DASH, never 0');
}
console.log('kpiTotals: avgStickersPerDayPerProfessional is DASH when NO professional has sticker activity (M6) OK');

{
  // barriosActivos (7 d): count of DISTINCT barrios across ALL professionals'
  // own barriosActivos arrays — accent/case variants across DIFFERENT
  // professionals must still dedupe to one (same normalizeName rule
  // buildBarriosActivosByKey uses internally for a single professional).
  const result = {
    rows: [
      { stickersTotal: 1, activeDays: 1, barriosActivos: ['San Antonio', 'El Ingenio'] },
      { stickersTotal: 1, activeDays: 1, barriosActivos: ['SAN ANTONIO', 'Otro Barrio'] },
    ],
    totals: { professionals: 2, stickers: 2, surveys: 0 },
  };
  const t = kpiTotals(result, { stickersLoaded: true });
  // Distinct: San Antonio (deduped), El Ingenio, Otro Barrio = 3.
  assert.equal(t.barriosActivos, 3);
}
console.log('kpiTotals: barriosActivos dedupes accent/case variants ACROSS professionals OK');

{
  // Missing/malformed rowsResult tolerated -- never throws.
  const t = kpiTotals(undefined, {});
  assert.deepEqual(t, {
    professionals: 0, stickers: 0, surveys: 0, avgStickersPerDayPerProfessional: 0, barriosActivos: 0,
  });
}
console.log('kpiTotals: missing/malformed rowsResult tolerated OK');

// ── W7: matchesSearch ────────────────────────────────────────────────────
// Cédula path when the query has >=3 digits (after stripping non-digits);
// name path otherwise. A short numeric query ('123') must NOT match a name
// like "Gil" via the name path (it never reaches it -- goes cedula path,
// which correctly fails against a blank/non-matching cedula).

assert.equal(matchesSearch({ name: 'Gil Soto', cedula: '' }, ''), true, 'empty query matches everything');
assert.equal(matchesSearch({ name: 'Gil Soto', cedula: '' }, '   '), true, 'whitespace-only query matches everything');
assert.equal(matchesSearch({ name: 'Gil Soto', cedula: '1234567' }, 'gil'), true, 'name substring, case-insensitive');
assert.equal(matchesSearch({ name: 'María José López', cedula: '' }, 'jose lopez'), true, 'accent-insensitive name substring');
assert.equal(matchesSearch({ name: 'Gil Soto', cedula: '' }, 'xyz'), false, 'no match on name');
assert.equal(matchesSearch({ name: 'Gil', cedula: '' }, '123'), false, '"123" (>=3 digits) must NOT fall back to matching the name "Gil"');
assert.equal(matchesSearch({ name: 'Ana', cedula: '1234567' }, '234'), true, '3-digit query matches a cedula substring');
assert.equal(matchesSearch({ name: 'Ana', cedula: '1.234.567' }, '234'), true, 'cedula substring match ignores dots in the STORED cedula');
assert.equal(matchesSearch({ name: 'Ana', cedula: '1234567' }, '99'), false, '2-digit query (below the >=3 threshold) falls back to the NAME path, "99" not in "Ana"');
assert.equal(matchesSearch({ name: '99', cedula: '1234567' }, '99'), true, '2-digit query matches via the NAME path when the name itself is "99"');
assert.equal(matchesSearch({ name: 'Ana', cedula: '' }, '234'), false, '3-digit query with a blank cedula never falls back to the name path');
console.log('matchesSearch: cedula path (>=3 digits) vs name path OK');

// ── W-TP: the search box also matches tarjeta profesional ─────────────────
assert.equal(matchesSearch({ name: 'Ana', cedula: '', tarjetaProfesional: 'TP-778899' }, '7788'), true, '4-digit query matches a TP digit substring when cedula does not');
assert.equal(matchesSearch({ name: 'Ana', cedula: '1234567', tarjetaProfesional: '' }, '7788'), false, 'no match when neither cedula nor TP contains the digit run');
assert.equal(matchesSearch({ name: 'Xyz', cedula: '', tarjetaProfesional: 'TP-778899' }, 'tp-77'), true, 'short alphanumeric query matches TP via the name-path substring check');
assert.equal(matchesSearch({ name: 'Xyz', cedula: '', tarjetaProfesional: '' }, 'tp-77'), false, 'no TP and no matching name -> no match');
console.log('matchesSearch: tarjetaProfesional also matched (digits via cedula-path, text via name-path) OK');

// ── Task 4.9/4.10: matchesSearch also matches the backend-resolved `np` ────
// (spec: "Search matches the resolved np, not a stale client value") -- a
// short alphanumeric query like "P3" has <3 digits (cedulaKey('P3') === '3',
// length 1), so it takes the name-path branch; `np` is now checked there
// alongside name/tarjetaProfesional.
assert.equal(matchesSearch({ name: 'Ana', cedula: '', np: 'P3' }, 'p3'), true, 'query matches the resolved np field, case-insensitive');
assert.equal(matchesSearch({ name: 'Ana', cedula: '', np: '' }, 'p3'), false, 'no np and no matching name -> no match');
assert.equal(matchesSearch({ name: 'Xyz', cedula: '', tarjetaProfesional: '', np: 'P1' }, 'p3'), false, 'a DIFFERENT np must not match');
console.log('matchesSearch: also matches the backend-resolved np (task 4.9/4.10) OK');

// ── W7: visibleRowsFor ──────────────────────────────────────────────────

{
  const rows = [
    { key: 'ced:1', name: 'Ana Ruiz', cedula: '111' },
    { key: 'ced:2', name: 'Beto Ríos', cedula: '222' },
    { key: nomKey('Gil Soto'), name: 'Gil Soto', cedula: '' },
  ];
  assert.deepEqual(visibleRowsFor(rows, {}).map((r) => r.key), rows.map((r) => r.key), 'no filters -> everything visible');
  assert.deepEqual(visibleRowsFor(rows, { query: 'ana' }).map((r) => r.key), ['ced:1']);
  assert.deepEqual(visibleRowsFor(rows, { professionalKey: 'ced:2' }).map((r) => r.key), ['ced:2']);
  assert.deepEqual(
    visibleRowsFor(rows, { query: 'gil', professionalKey: 'ced:2' }).map((r) => r.key),
    [],
    'both filters apply together (AND), not either/or',
  );
  assert.deepEqual(visibleRowsFor([], { query: 'ana' }), [], 'empty rows -> empty, never throws');
  assert.deepEqual(visibleRowsFor(undefined, {}), [], 'missing rows tolerated');
}
console.log('visibleRowsFor: search + professionalKey filters (AND) OK');

// ── W7: degradedStickerNote ──────────────────────────────────────────────
// 4 states (isDegraded x stickersLoaded): only BOTH true produces a note --
// matches the real production flow (isDegraded is only ever set once the
// sticker fetch has resolved, i.e. alongside stickersLoaded=true).

assert.equal(degradedStickerNote(false, false), null);
assert.equal(degradedStickerNote(false, true), null);
assert.equal(degradedStickerNote(true, false), null, 'degraded-but-not-yet-loaded is defensively null, not a premature note');
assert.equal(degradedStickerNote(true, true), DEGRADED_STICKERS_NOTE);
assert.ok(!/sin profesional identificado/i.test(DEGRADED_STICKERS_NOTE), 'must not promise the removed "sin profesional identificado" KPI bucket');
console.log('degradedStickerNote: 4-state truth table OK');

// ── W7: unassignedNote ───────────────────────────────────────────────────
// Reconciles the (now-hidden) "sin profesional identificado" count with the
// visible stickers KPI/table, as a disclosure note instead of its own tile.

assert.equal(unassignedNote({ stickers: 0, surveys: 5 }), null, 'zero unassigned stickers -> no note (surveys not this note\'s concern)');
assert.equal(unassignedNote({ stickers: 0 }), null);
assert.equal(unassignedNote(undefined), null, 'missing input tolerated');
{
  const note = unassignedNote({ stickers: 7, surveys: 2 });
  assert.ok(/7/.test(note), 'must mention the unassigned STICKER count');
  assert.ok(/sin profesional atribuible/i.test(note));
}
console.log('unassignedNote: null when zero, mentions the sticker count otherwise OK');

// ── W7: hasActiveSegFilters gains `professional` ─────────────────────────
// seg-chart-professional now ALSO narrows the table (W7) — so it must count
// as an active filter for "Reiniciar filtros"' enabled/disabled state, unlike
// before (W5/W6, when it only picked the chart's highlighted line).

assert.equal(hasActiveSegFilters({ professional: 'ced:1' }), true, 'a professional selection alone is active');
assert.equal(hasActiveSegFilters({ professional: '' }), false, 'an empty-string professional is not active');
assert.equal(hasActiveSegFilters({ professional: null }), false);
assert.equal(
  hasActiveSegFilters({ search: '', from: null, to: null, professional: 'ced:1' }),
  true,
  'professional active even with every other filter empty',
);
console.log('hasActiveSegFilters: professional selection counts as an active filter (W7) OK');

// ── M5: reportSelectedButtonState ─────────────────────────────────────────
// The pure decision behind "Reporte PDF individual"'s disabled/title state.
// The regression: renderChartOptions can reset seg-chart-professional back
// to "" when a Desde/Hasta change narrows the previous selection out of
// `rows` — the button must read as disabled (no selection) in that state,
// which requires render() to re-derive it every time, not just on the
// select's own 'change' handler.
{
  const noSelection = reportSelectedButtonState({
    isDegraded: false, stickersLoaded: true, busy: false, hasSelection: false,
  });
  assert.equal(noSelection.disabled, true, 'no selection -> disabled');
  assert.match(noSelection.title, /seleccion/i, 'no selection -> title explains a selection is required');
}
{
  const selected = reportSelectedButtonState({
    isDegraded: false, stickersLoaded: true, busy: false, hasSelection: true,
  });
  assert.equal(selected.disabled, false, 'selection present + nothing else blocking -> enabled');
  assert.match(selected.title, /descargar informe/i);
}
assert.equal(reportSelectedButtonState({ hasSelection: true, isDegraded: true }).disabled, true, 'degraded blocks even with a selection');
assert.equal(reportSelectedButtonState({ hasSelection: true, stickersLoaded: false }).disabled, true, 'stickers not loaded yet blocks even with a selection');
assert.equal(reportSelectedButtonState({ hasSelection: true, busy: true }).disabled, true, 'a mass export in flight blocks even with a selection');
assert.equal(reportSelectedButtonState().disabled, true, 'no args at all -> disabled (defaults to no selection)');
console.log('reportSelectedButtonState: disabled/title truth table, "no selection" is the M5 regression case OK');

// ── W7: kpisHtml (5 tiles, HTML string) ──────────────────────────────────

{
  const rowsResult = {
    rows: [{
      stickersTotal: 4, activeDays: 2, stickerActiveDays: 2, barriosActivos: ['San Antonio'],
    }],
    totals: { professionals: 1, stickers: 4, surveys: 2 },
  };
  const html = kpisHtml(rowsResult, true);
  assert.ok(!/sin profesional identificado/i.test(html), 'the removed "sin profesional identificado" tile must not render');
  assert.ok(!/stickers sin fecha/i.test(html), 'the removed "stickers sin fecha" tile must not render');
  assert.ok(/barrios activos/i.test(html), 'the new "barrios activos (7 d)" tile must render');
  // M6: the tile's product semantics changed (mean over sticker-active
  // professionals only, denominator = stickerActiveDays) — renamed to match,
  // with a `title` explaining the denominator so it doesn't read as a pooled
  // stickers+surveys pace.
  assert.ok(/stickers\/d.a por profesional/i.test(html), 'M6: tile is renamed "stickers/día por profesional"');
  assert.ok(!/promedio diario por profesional/i.test(html), 'M6: the old, more ambiguous label must be gone');
  assert.match(html, /title="[^"]*stickers por d.a con actividad de stickers[^"]*"/i, 'M6: a title tooltip explains the denominator (sticker-active days), averaged across professionals');
  const tileCount = (html.match(/kpi-tile/g) || []).length;
  assert.equal(tileCount, 5, 'exactly 5 KPI tiles');
}
console.log('kpisHtml: 5 tiles, old ones dropped, new one present, M6 rename + caption OK');

{
  // stickersLoaded=false masks the sticker-derived tiles with DASH in the
  // rendered HTML too (not just the pure kpiTotals value).
  const rowsResult = { rows: [], totals: { professionals: 0, stickers: 0, surveys: 0 } };
  const html = kpisHtml(rowsResult, false);
  assert.ok(html.includes(DASH), 'DASH must appear in the rendered HTML while stickers have not loaded');
}
console.log('kpisHtml: stickersLoaded=false renders DASH OK');

// ── W8: timelineChartConfig ───────────────────────────────────────────────
// Dual linear axis (replaces the old shared logarithmic axis): daily counts
// on the right ('y1'), cumulative running totals on the left ('y').

{
  const empty = { labels: [], stickers: [], surveys: [], stickersCumulative: [], surveysCumulative: [], offsets: { stickers: 0, surveys: 0 } };
  const config = timelineChartConfig(empty);
  assert.deepEqual(config.data.labels, []);
  for (const ds of config.data.datasets) assert.deepEqual(ds.data, []);
}
console.log('timelineChartConfig: empty timeline -> empty dataset arrays, never throws OK');

{
  // Single label: the cumulative (dashed, normally pointRadius 0) datasets
  // get pointRadius 3 instead -- a 1-point dashed line with radius 0 would
  // otherwise render NOTHING at all.
  const single = {
    labels: ['2026-01-01'], stickers: [2], surveys: [1], stickersCumulative: [5], surveysCumulative: [3],
    offsets: { stickers: 3, surveys: 2 },
  };
  const config = timelineChartConfig(single);
  const cumulativeDatasets = config.data.datasets.filter((d) => /acum/i.test(d.label));
  assert.equal(cumulativeDatasets.length, 2);
  for (const ds of cumulativeDatasets) assert.equal(ds.pointRadius, 3);
  const dailyDatasets = config.data.datasets.filter((d) => /diario/i.test(d.label));
  assert.equal(dailyDatasets.length, 2);
  for (const ds of dailyDatasets) { assert.equal(ds.pointRadius, 3); assert.equal(ds.borderWidth, 3); }
}
console.log('timelineChartConfig: single label -> cumulative pointRadius 3 OK');

{
  // A day with 0 daily records still plots 0 (not null/undefined) -- Chart.js
  // would otherwise render a gap instead of a real zero point.
  const timeline = {
    labels: ['2026-01-01', '2026-01-02'], stickers: [1, 0], surveys: [0, 0],
    stickersCumulative: [1, 1], surveysCumulative: [0, 0], offsets: { stickers: 0, surveys: 0 },
  };
  const config = timelineChartConfig(timeline);
  const stickerDaily = config.data.datasets.find((d) => d.label === 'Sticker diario');
  assert.equal(stickerDaily.data[1], 0);
  assert.notEqual(stickerDaily.data[1], null);
}
console.log('timelineChartConfig: a zero-count day plots 0, not null OK');

{
  // yAxisID assignment: daily on y1 (right), cumulative on y (left) --
  // scales.y is titled "Acumulado", scales.y1 "Diario", y1 never draws its
  // own gridlines over y's (drawOnChartArea:false).
  const timeline = { labels: ['2026-01-01'], stickers: [1], surveys: [1], stickersCumulative: [1], surveysCumulative: [1], offsets: { stickers: 0, surveys: 0 } };
  const config = timelineChartConfig(timeline);
  const byLabel = Object.fromEntries(config.data.datasets.map((d) => [d.label, d]));
  assert.equal(byLabel['Sticker diario'].yAxisID, 'y1');
  assert.equal(byLabel['Survey diario'].yAxisID, 'y1');
  assert.equal(byLabel['Sticker acum.'].yAxisID, 'y');
  assert.equal(byLabel['Survey acum.'].yAxisID, 'y');
  assert.equal(config.options.scales.y.title.text, 'Acumulado');
  assert.equal(config.options.scales.y1.title.text, 'Diario');
  assert.equal(config.options.scales.y1.grid.drawOnChartArea, false);
  // Node-safe theming (baseOptions/themeColor return fallbacks without `document`).
  assert.ok(config.options.scales.y1.ticks.color, 'y1 must be themed (W8 baseOptions fix)');
  assert.equal(config.options.scales.y.type, 'linear');
  assert.equal(config.options.scales.y1.type, 'linear');
}
console.log('timelineChartConfig: daily on y1 (right), cumulative on y (left), both linear + themed OK');

{
  // Last-point label (_totalLabel) equals offset + Σ daily -- exactly
  // timeline.*Cumulative's own last entry (buildTimeline already folds the
  // offset into the running total), never recomputed a second, divergent way.
  const timeline = {
    labels: ['2026-01-01', '2026-01-02'], stickers: [2, 3], surveys: [1, 1],
    stickersCumulative: [7, 10], surveysCumulative: [4, 5], offsets: { stickers: 5, surveys: 3 },
  };
  const config = timelineChartConfig(timeline);
  const byLabel = Object.fromEntries(config.data.datasets.map((d) => [d.label, d]));
  assert.equal(byLabel['Sticker acum.']._totalLabel, '10');
  assert.equal(byLabel['Survey acum.']._totalLabel, '5');
}
console.log('timelineChartConfig: last-point label equals offset + Σ daily OK');

// ── W8: timelineDataKey ───────────────────────────────────────────────────
// Drives renderChart()'s upsertChart-skip: identical inputs -> identical key;
// any of labels/from/to/professionalKey/offsets/series-sums changing ->
// a different key.

{
  const t1 = { labels: ['2026-01-01'], stickers: [1], surveys: [0], stickersCumulative: [1], surveysCumulative: [0], offsets: { stickers: 0, surveys: 0 } };
  const t2 = { labels: ['2026-01-01'], stickers: [1], surveys: [0], stickersCumulative: [1], surveysCumulative: [0], offsets: { stickers: 0, surveys: 0 } };
  assert.equal(
    timelineDataKey(t1, { from: '2026-01-01', to: '2026-01-31', professionalKey: null }),
    timelineDataKey(t2, { from: '2026-01-01', to: '2026-01-31', professionalKey: null }),
    'identical inputs -> identical key',
  );
  assert.notEqual(
    timelineDataKey(t1, { from: '2026-01-01', to: '2026-01-31', professionalKey: null }),
    timelineDataKey(t1, { from: '2026-02-01', to: '2026-01-31', professionalKey: null }),
    '`from` changing must change the key',
  );
  assert.notEqual(
    timelineDataKey(t1, { professionalKey: null }),
    timelineDataKey(t1, { professionalKey: 'ced:1' }),
    '`professionalKey` changing must change the key',
  );
  const t3 = { ...t1, offsets: { stickers: 5, surveys: 0 } };
  assert.notEqual(
    timelineDataKey(t1, {}),
    timelineDataKey(t3, {}),
    'offsets changing must change the key even when labels/series are identical',
  );
  const t4 = { ...t1, labels: ['2026-01-01', '2026-01-02'], stickers: [1, 0] };
  assert.notEqual(timelineDataKey(t1, {}), timelineDataKey(t4, {}), 'labels changing must change the key');
}
console.log('timelineDataKey: identical inputs -> identical key, any relevant field changing -> different key OK');

// ── H2: timelineDataKey must not collapse a re-ordered day-by-day series ───
// The old key only summed each series (Σstickers, Σsurveys) — two timelines
// with the SAME labels and the SAME totals but a DIFFERENT per-day order
// ([1,2] vs [2,1], e.g. after switching the professional-select filter to
// someone whose daily counts happen to sum the same) hashed to the identical
// key, so renderChart() silently skipped the repaint even though the actual
// data being plotted had changed.
{
  const perm1 = {
    labels: ['2026-01-01', '2026-01-02'], stickers: [1, 2], surveys: [0, 0],
    stickersCumulative: [1, 3], surveysCumulative: [0, 0], offsets: { stickers: 0, surveys: 0 },
  };
  const perm2 = {
    labels: ['2026-01-01', '2026-01-02'], stickers: [2, 1], surveys: [0, 0],
    stickersCumulative: [2, 3], surveysCumulative: [0, 0], offsets: { stickers: 0, surveys: 0 },
  };
  assert.notEqual(
    timelineDataKey(perm1, { from: null, to: null, professionalKey: null }),
    timelineDataKey(perm2, { from: null, to: null, professionalKey: null }),
    'H2: same labels + same per-series total but a different day-by-day order must still produce a different key',
  );
  // Same reasoning on the surveys series.
  const survPerm1 = { ...perm1, stickers: [0, 0], surveys: [1, 2] };
  const survPerm2 = { ...perm1, stickers: [0, 0], surveys: [2, 1] };
  assert.notEqual(
    timelineDataKey(survPerm1, {}),
    timelineDataKey(survPerm2, {}),
    'H2: same reasoning applies to the surveys series',
  );
}
console.log('timelineDataKey: a re-ordered per-day series (same labels/totals) still changes the key (H2) OK');

// ── B1: shouldSkipChartRender — the actual gate renderChart() uses to decide
// whether upsertChart() is worth calling again. Gating on the dataKey ALONE
// broke after a theme change: main.js's 'themechange' listener destroys every
// registered chart (charts.js's resetCharts()) BEFORE this module's own
// deferred re-render fires, but the dataKey computed from the SAME
// stickers/surveys/from/to/professionalKey is unchanged -> the old
// `dataKey === lastChartDataKey` check alone returned true and skipped the
// rebuild, leaving the canvas permanently blank. The fix threads whether the
// chart is STILL actually registered (charts.js's hasChart()) into the
// decision. ───────────────────────────────────────────────────────────────
assert.equal(
  shouldSkipChartRender({ dataKey: 'k1', lastKey: 'k1', hasChart: true }),
  true,
  'same key, chart still registered -> safe to skip',
);
assert.equal(
  shouldSkipChartRender({ dataKey: 'k1', lastKey: 'k1', hasChart: false }),
  false,
  'B1: same key but the chart was destroyed (e.g. a theme change) -> must NOT skip, must rebuild',
);
assert.equal(
  shouldSkipChartRender({ dataKey: 'k1', lastKey: 'k2', hasChart: true }),
  false,
  'different key -> never skip regardless of registration',
);
assert.equal(
  shouldSkipChartRender({ dataKey: 'k1', lastKey: null, hasChart: true }),
  false,
  'no previous key (first render / just torn down) -> never skip',
);
assert.equal(shouldSkipChartRender(), false, 'no args at all -> never skip');
console.log('shouldSkipChartRender: gates the upsertChart skip on BOTH key match AND the chart still being registered (B1) OK');

// ── W9: formatMinutes ───────────────────────────────────────────────────────
assert.equal(formatMinutes(5), '00:05');
assert.equal(formatMinutes(725), '12:05');
assert.equal(formatMinutes(0), '00:00');
assert.equal(formatMinutes(1439), '23:59');
assert.equal(formatMinutes(null), DASH);
assert.equal(formatMinutes(undefined), DASH);
assert.equal(formatMinutes(NaN), DASH);
assert.equal(formatMinutes(-5), DASH, 'out-of-range minute must never throw/produce a bogus time');
assert.equal(formatMinutes(1440), DASH, 'out-of-range minute (>= 24h) must never throw/produce a bogus time');
// L9: a fractional minute must be rounded ONCE, up front, before splitting
// into hour/minute -- flooring the hour and separately rounding the minute
// (the old behavior) could carry the minute past 59 (e.g. 719.6 used to
// yield the bogus "11:60" instead of rolling over into the next hour).
assert.equal(formatMinutes(719.6), '12:00', 'L9: 719.6 must roll over to 12:00, never the bogus 11:60');
assert.equal(formatMinutes(59.6), '01:00', 'L9: 59.6 must roll over to 01:00, never 00:60');
console.log('formatMinutes: HH:MM formatting + DASH for null/invalid + L9 rollover OK');

// ── W9: columnsFor / defaultSortFor / COLUMNS_TOTALES / COLUMNS_TEMPORALES ──
assert.ok(Array.isArray(COLUMNS_TOTALES) && COLUMNS_TOTALES.length > 0);
assert.ok(Array.isArray(COLUMNS_TEMPORALES) && COLUMNS_TEMPORALES.length > 0);
assert.deepEqual(columnsFor('totales'), COLUMNS_TOTALES);
assert.deepEqual(columnsFor('temporales'), COLUMNS_TEMPORALES);
assert.deepEqual(columnsFor(undefined), COLUMNS_TOTALES, 'default subTab is totales');
assert.deepEqual(columnsFor('unknown-sub-tab'), COLUMNS_TOTALES, 'an unknown subTab falls back to totales, never throws');
// M5: 'total' is NOT one of COLUMNS_TOTALES' own keys (see the array above)
// -- a default sort column that isn't a real header means no header ever
// shows the sort indicator, and clicking it can't toggle asc/desc (the
// header-click handler matches on `c.key`). 'stickersFase1' IS a real
// COLUMNS_TOTALES header.
assert.deepEqual(defaultSortFor('totales'), { column: 'stickersFase1', dir: 'desc' });
assert.deepEqual(defaultSortFor('temporales'), { column: 'firstDate', dir: 'desc' });
assert.deepEqual(defaultSortFor('unknown'), { column: 'stickersFase1', dir: 'desc' }, 'unknown subTab defaults like totales');
assert.ok(COLUMNS_TOTALES.some((c) => c.key === 'np' && c.label === 'Clase (P)'));
assert.ok(COLUMNS_TOTALES.some((c) => c.key === 'codigo' && c.label === 'Código vigente'));
assert.ok(COLUMNS_TEMPORALES.some((c) => c.key === 'codigo' && c.label === 'Código'));
// W-TP (user request 2026-09-15): tarjeta profesional is now shown inline in
// the Seguimiento totales table (previously PDF-only) -- right after cedula,
// never in the temporales sub-tab.
{
  const cedulaIdx = COLUMNS_TOTALES.findIndex((c) => c.key === 'cedula');
  const tpIdx = COLUMNS_TOTALES.findIndex((c) => c.key === 'tarjetaProfesional');
  assert.ok(cedulaIdx >= 0 && tpIdx === cedulaIdx + 1, 'tarjetaProfesional must sit right after cedula in COLUMNS_TOTALES');
  assert.equal(COLUMNS_TOTALES[tpIdx].label, 'Tarjeta profesional');
  assert.ok(!COLUMNS_TEMPORALES.some((c) => c.key === 'tarjetaProfesional'), 'tarjetaProfesional must NOT be a temporales column');
}
// M5 (generalized): whichever column a sub-tab defaults to must always be
// one of that SAME sub-tab's own visible headers, for EVERY sub-tab —
// otherwise the sort indicator (▲/▼) can never render and a header click on
// that column can never "un-invert" it back to the default.
for (const subTab of ['totales', 'temporales', 'unknown-sub-tab']) {
  const sort = defaultSortFor(subTab);
  const cols = columnsFor(subTab);
  assert.ok(cols.some((c) => c.key === sort.column), `${subTab}: default sort column "${sort.column}" must exist in its own columnsFor()`);
}
console.log('columnsFor/defaultSortFor OK');

// ── W9: buildProfessionalRows now also carries avgStickersPerDay + the batch
// temporal-metrics fields (reusing buildTemporalMetricsByKey — B1-style single
// pass instead of a per-subtab recompute) ───────────────────────────────────
{
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-01T09:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-02T10:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [], today: '2026-01-09' });
  const row = result.rows[0];
  assert.equal(row.avgStickersPerDay, 1, '2 stickers / 2 active days');
  assert.equal(row.daysSinceFirst, 8, 'today (01-09) minus firstDate (01-01)');
  assert.equal(typeof row.prevDayFirstMinutes, 'number');
}
console.log('buildProfessionalRows: avgStickersPerDay + temporal fields merged in OK');

{
  // activeDays 0 (no dated records at all) -> avgStickersPerDay is null (never
  // NaN/Infinity, never a silent 0 masquerading as "confirmed zero pace").
  const stickers = [
    { inspector: { nombre_completo: 'Ana Ruiz' }, fecha: null, fase: 1, fuente: 'atencionsismo' },
  ];
  const result = buildProfessionalRows({ stickers, surveys: [] });
  assert.equal(result.rows[0].avgStickersPerDay, null);
}
console.log('buildProfessionalRows: avgStickersPerDay null when activeDays is 0 OK');

// ── M6: avgStickersPerDay is driven by STICKER-only active days, never the
// pooled `activeDays` (which also counts survey-only days) — "stickers/día
// por profesional" must reflect only days the professional actually placed a
// sticker on. ────────────────────────────────────────────────────────────
{
  // 10 stickers on ONE day, plus Survey activity on 4 OTHER (sticker-less)
  // days: activeDays pools all 5 days, but the sticker pace must stay 10 (not
  // 10/5 = 2, diluted by days that have no sticker at all).
  const stickerFixture = { inspector: { nombre_completo: 'Mono Pace' }, fecha: '2026-01-01', fase: 1, fuente: 'atencionsismo' };
  const tenStickers = Array.from({ length: 10 }, () => ({ ...stickerFixture }));
  const surveys = ['2026-01-02', '2026-01-03', '2026-01-04', '2026-01-05'].map((d) => ({
    nombre_evaluador: 'Mono Pace', fecha_inspeccion: d,
  }));
  const result = buildProfessionalRows({ stickers: tenStickers, surveys });
  const row = result.rows[0];
  assert.equal(row.activeDays, 5, 'sticker day + 4 survey-only days = 5 active days total (the OLD, wrong denominator)');
  assert.equal(row.stickerActiveDays, 1, 'only 1 distinct day actually has a counted sticker');
  assert.equal(row.avgStickersPerDay, 10, '10 stickers / 1 sticker-active day, never diluted by survey-only days');
}
{
  // A survey-only professional (zero stickers ever) -> stickerActiveDays 0,
  // avgStickersPerDay null (not 0/NaN) even though activeDays is nonzero.
  const surveys = [{ nombre_evaluador: 'Solo Survey', fecha_inspeccion: '2026-01-01' }];
  const result = buildProfessionalRows({ stickers: [], surveys });
  const row = result.rows[0];
  assert.equal(row.activeDays, 1);
  assert.equal(row.stickerActiveDays, 0);
  assert.equal(row.avgStickersPerDay, null);
}
console.log('buildProfessionalRows: avgStickersPerDay uses sticker-only active days, not the pooled activeDays (M6) OK');

// ── W9: xlsxRowsFor ──────────────────────────────────────────────────────────
{
  const rowFull = {
    key: 'ced:1', name: 'Gil Soto', cedula: '123', codigo: 'B1', entidad: 'DAGRD',
    np: 'P2', tarjetaProfesional: 'TP-1', celular: '300', correo: 'g@x.com',
    stickersFase1: 2, stickersFase2: 1, stickersTotal: 3, surveyTotal: 1, total: 4,
    firstDate: '2026-01-01', lastDate: '2026-01-05', activeDays: 3, avgPerActiveDay: 1.33,
    avgStickersPerDay: 1, rosterSourced: 0, barriosActivos: ['San Antonio', 'El Poblado'],
    daysSinceFirst: 8, prevDay: '2026-01-08', prevDayFirstMinutes: 480, prevDayLastMinutes: 600,
    avgFirstMinutes: 500, avgLastMinutes: 620,
    // Task 4.9 (spec: "Table And Export Reflect Depurado Fields
    // Consistently") -- backend-resolved fields, present on the row exactly
    // like np/codigo already are.
    fase: 'Fase II', estadoSugerido: 'activo', fuenteDato: 'main+fase2',
  };
  // Row missing contact/temporal data entirely (only the bare minimum a
  // buildProfessionalRows result can produce for an untimed professional) --
  // every key must still be present, defaulted via `?? ''`, never a missing
  // key/undefined value.
  const rowSparse = {
    key: 'nom:sin datos', name: 'Sin Datos', cedula: '', codigo: '', entidad: '',
    np: '', tarjetaProfesional: '', celular: '', correo: '',
    stickersFase1: 0, stickersFase2: 0, stickersTotal: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0,
    avgStickersPerDay: null, rosterSourced: 0, barriosActivos: [],
    daysSinceFirst: null, prevDay: null, prevDayFirstMinutes: null, prevDayLastMinutes: null,
    avgFirstMinutes: null, avgLastMinutes: null,
    fase: '', estadoSugerido: '', fuenteDato: '',
  };

  const totalesRows = xlsxRowsFor([rowFull, rowSparse], { subTab: 'totales' });
  assert.equal(totalesRows.length, 2);
  const keysFull = Object.keys(totalesRows[0]).sort();
  const keysSparse = Object.keys(totalesRows[1]).sort();
  assert.deepEqual(keysFull, keysSparse, 'every row must carry the identical key set');
  assert.equal(totalesRows[0].profesional, 'Gil Soto');
  assert.equal(totalesRows[0].clase_p, 'P2');
  assert.equal(totalesRows[0].codigo_vigente, 'B1');
  // H1 (superseded for TP, W-TP, user request 2026-09-15): tarjeta
  // profesional is now visible in the Seguimiento tab itself, so it is
  // ALSO included in the totales XLSX; celular/correo stay PDF-only
  // (H1's original reasoning still applies to those two: an Excel file is
  // far more likely to be forwarded/copied around than a per-professional
  // PDF).
  assert.ok('tarjeta_profesional' in totalesRows[0], 'W-TP: tarjeta_profesional must now appear in the totales XLSX');
  assert.equal(totalesRows[0].tarjeta_profesional, 'TP-1');
  assert.equal(totalesRows[1].tarjeta_profesional, '', 'missing TP defaults to empty string, never undefined/null');
  assert.ok(!('celular' in totalesRows[0]), 'H1: celular must never appear in the XLSX');
  assert.ok(!('correo' in totalesRows[0]), 'H1: correo must never appear in the XLSX');
  // Nit: barrios join with ', ' -- same separator the table cell uses
  // (cellHtml's 'barriosActivos' case), so the two never disagree in style.
  assert.equal(totalesRows[0].barrios_activos_7d, 'San Antonio, El Poblado');
  assert.equal(totalesRows[0].stickers_promedio_diario, 1);
  assert.equal(totalesRows[1].barrios_activos_7d, '');
  assert.equal(totalesRows[1].stickers_promedio_diario, '', 'null avgStickersPerDay -> empty string, never the literal null');
  // Task 4.9: fase/estado_sugerido/fuente_dato read straight off the row,
  // same field the on-screen table would read -- never a separately
  // client-derived value.
  assert.equal(totalesRows[0].fase, 'Fase II');
  assert.equal(totalesRows[0].estado_sugerido, 'activo');
  assert.equal(totalesRows[0].fuente_dato, 'main+fase2');
  assert.equal(totalesRows[1].fase, '', 'missing fase defaults to empty string, never undefined/null');
  assert.equal(totalesRows[1].estado_sugerido, '');
  assert.equal(totalesRows[1].fuente_dato, '');

  const temporalesRows = xlsxRowsFor([rowFull, rowSparse], { subTab: 'temporales' });
  assert.equal(temporalesRows.length, 2);
  const tKeysFull = Object.keys(temporalesRows[0]).sort();
  const tKeysSparse = Object.keys(temporalesRows[1]).sort();
  assert.deepEqual(tKeysFull, tKeysSparse, 'every temporales row must carry the identical key set too');
  assert.equal(temporalesRows[0].hora_1er_registro_dia_ant, '08:00');
  assert.equal(temporalesRows[0].hora_prom_ult_registro, '10:20');
  assert.equal(temporalesRows[1].hora_1er_registro_dia_ant, DASH, 'a professional with no timed record shows DASH, not a crash/blank');
  assert.equal(temporalesRows[1].fecha_primer_registro, '');
  assert.equal(temporalesRows[1].dias_desde_primera_actividad, '');
}
console.log('xlsxRowsFor: totales/temporales sheets carry the identical key set, missing data defaults via ?? "" OK');

{
  // Empty/undefined input -> empty array, never throws.
  assert.deepEqual(xlsxRowsFor([], { subTab: 'totales' }), []);
  assert.deepEqual(xlsxRowsFor(undefined, { subTab: 'totales' }), []);
  assert.deepEqual(xlsxRowsFor([], {}), [], 'default subTab (totales) with no rows');
}
console.log('xlsxRowsFor: empty/undefined input OK');

// ── Task 4.10: exported XLSX np matches the on-screen (cellHtml) np ────────
// (spec: "Exported XLSX matches on-screen np") -- both read `row.np`
// directly, never a separately-derived client value.
{
  const row = {
    name: 'Gil Soto', np: 'P3', cedula: '123', codigo: 'B1', entidad: 'DAGRD',
    tarjetaProfesional: '', celular: '', correo: '',
    stickersFase1: 0, stickersFase2: 0, stickersTotal: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgStickersPerDay: null,
    barriosActivos: [], fase: '', estadoSugerido: '', fuenteDato: '',
  };
  const onScreenNp = cellHtml(row, 'np', true);
  const exportedNp = xlsxRowsFor([row], { subTab: 'totales' })[0].clase_p;
  assert.equal(onScreenNp, 'P3');
  assert.equal(exportedNp, 'P3');
  assert.equal(onScreenNp, exportedNp, 'the table cell and the XLSX export must read the identical resolved np');
}
console.log('xlsxRowsFor/cellHtml: exported np matches the on-screen np (task 4.10) OK');

// ── N13: xlsxFiltersSummary — the XLSX header's "Filtros:" row, so an export
// carries which search/range/professional narrowed it, not just a bare
// "Registros: N" that could otherwise be misread as the WHOLE dataset. ────
assert.equal(
  xlsxFiltersSummary({
    search: '', from: null, to: null, professionalName: '',
  }),
  'ninguno',
  'no active filter at all -> "ninguno", never a blank/misleading cell',
);
assert.equal(xlsxFiltersSummary(), 'ninguno', 'no args at all -> "ninguno"');
assert.equal(
  xlsxFiltersSummary({ search: 'ana' }),
  'Búsqueda: "ana"',
);
assert.equal(
  xlsxFiltersSummary({ search: '  ana  ' }),
  'Búsqueda: "ana"',
  'search is trimmed before it renders',
);
assert.equal(
  xlsxFiltersSummary({ from: '2026-01-01', to: '2026-01-31' }),
  'Desde: 2026-01-01; Hasta: 2026-01-31',
);
assert.equal(
  xlsxFiltersSummary({ professionalName: 'Gil Soto' }),
  'Profesional: Gil Soto',
  'the professional NAME renders, not the raw ced:/nom: key',
);
assert.equal(
  xlsxFiltersSummary({
    search: 'ana', from: '2026-01-01', to: null, professionalName: 'Gil Soto',
  }),
  'Búsqueda: "ana"; Desde: 2026-01-01; Profesional: Gil Soto',
  'every active filter joins in a stable order, absent ones simply omitted',
);
console.log('xlsxFiltersSummary: "ninguno" when nothing active, otherwise a stable summary of the active filters (N13) OK');

// ── sortRows: temporal (nullable numeric minute) column, per W9 ────────────
{
  const rows = [
    { key: 'a', prevDayFirstMinutes: null },
    { key: 'b', prevDayFirstMinutes: 300 },
    { key: 'c', prevDayFirstMinutes: 60 },
  ];
  const asc = sortRows(rows, 'prevDayFirstMinutes', 'asc');
  assert.deepEqual(asc.map((r) => r.key), ['a', 'c', 'b'], 'null (no previous timed day) sorts lowest, ascending');
  const desc = sortRows(rows, 'prevDayFirstMinutes', 'desc');
  assert.deepEqual(desc.map((r) => r.key), ['b', 'c', 'a'], 'null still sorts lowest, i.e. LAST descending');
}
console.log('sortRows: nullable temporal-minutes column (prevDayFirstMinutes) OK');

// ── W10: objetivoDiario ──────────────────────────────────────────────────────
assert.equal(objetivoDiario({ pendientes: null, profesionalesActivos: 10, today: '2026-09-13' }), null, 'non-finite pendientes -> null, never substituted with 0');
assert.equal(objetivoDiario({ pendientes: NaN, profesionalesActivos: 10, today: '2026-09-13' }), null);
assert.equal(objetivoDiario({ pendientes: undefined, profesionalesActivos: 10, today: '2026-09-13' }), null, 'missing pendientes (agg without kpis) -> null, never 0');
assert.equal(objetivoDiario({ pendientes: 100, profesionalesActivos: 0, today: '2026-09-13' }), null, 'zero active professionals -> null');
assert.equal(objetivoDiario({ pendientes: 100, profesionalesActivos: -1, today: '2026-09-13' }), null, 'negative active professionals -> null');
assert.equal(objetivoDiario({ pendientes: 100, profesionalesActivos: 10, today: '2026-10-01' }), null, 'today past the deadline -> null');
assert.equal(objetivoDiario({ pendientes: 100, profesionalesActivos: 10, today: null }), null, 'missing today -> null');
// M3: a negative `pendientes` (a backlog that has gone negative — a data
// bug upstream, or a KPI counting the wrong direction) must never produce a
// negative daily TARGET — that reads as "you may do fewer visits", the
// opposite of what a target means. `null` (sin dato), same as any other
// value this function can't turn into a sane target.
assert.equal(objetivoDiario({ pendientes: -5, profesionalesActivos: 10, today: '2026-09-13' }), null, 'negative pendientes -> null, never a negative target');
{
  // today === deadline -> days = max(1, 0) = 1, never a division by zero.
  const v = objetivoDiario({
    pendientes: 50, profesionalesActivos: 5, today: '2026-09-30', deadline: '2026-09-30',
  });
  assert.equal(v, 10, '50/5/1 día = 10.0');
}
{
  // M3: rounding direction is Math.ceil, never Math.round -- a daily TARGET
  // to hit a fixed deadline must never be UNDER-stated (rounding 3.33 down
  // to 3.3 would let a professional finish the backlog late while still
  // hitting "their" rounded number every day).
  const v = objetivoDiario({
    pendientes: 333, profesionalesActivos: 10, today: '2026-09-20', deadline: '2026-09-30',
  });
  assert.equal(v, 3.4, '333 / 10 profesionales / 10 días = 3.33 -> ceil -> 3.4, never rounded down to 3.3');
}
{
  // M3: exact rounding-direction example from the finding — 3.31 -> 3.4
  // (ceil(33.1) = 34 -> 3.4), never 3.3.
  const v = objetivoDiario({
    pendientes: 331, profesionalesActivos: 100, today: '2026-09-29', deadline: '2026-09-30',
  });
  assert.equal(v, 3.4, '331 / 100 profesionales / 1 día = 3.31 -> ceil -> 3.4');
}
{
  // M3: `pendientes: 1, profesionalesActivos: 110` used to floor/round down
  // to a fabricated 0 (a real backlog silently read as "no target needed").
  // ceil() guarantees the result is never 0 while `pendientes > 0`.
  const v = objetivoDiario({
    pendientes: 1, profesionalesActivos: 110, today: '2026-09-29', deadline: '2026-09-30',
  });
  assert.ok(Number.isFinite(v) && v > 0, `a positive pendientes must never round down to a fabricated 0 (got ${v})`);
}
console.log('objetivoDiario: null-cases + ceil-rounding (never under-stated, never a fabricated 0) OK');

// ── W10: visitasUltimos7Dias — rolling today-6..today (inclusive), stickers +
// surveys, SIN_DATO excluded first (D3) ──────────────────────────────────────
{
  const stickers = [
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00' }, // today
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-03T12:00:00+00:00' }, // today-6, IN
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: 1, fuente: 'atencionsismo', fecha: '2026-01-02T12:00:00+00:00' }, // today-7, OUT
    { inspector: { nombre_completo: 'X', identificacion: '1' }, inspector_fuente: 'evaluacion', fase: null, fuente: 'atencionsismo', fecha: '2026-01-09T12:00:00+00:00' }, // SIN_DATO, excluded
  ];
  const surveys = [
    { nombre_evaluador: 'X', fecha_inspeccion: '2026-01-08' }, // IN
  ];
  const identity = buildIdentityIndex({ stickers, surveys });
  const row = { key: 'ced:1' };
  const n = visitasUltimos7Dias(row, {
    stickers, surveys, identity, today: '2026-01-09',
  });
  assert.equal(n, 3, '2 in-window stickers (SIN_DATO excluded) + 1 survey');
}
console.log('visitasUltimos7Dias: window inclusive today-6/exclusive today-7, SIN_DATO excluded first OK');

{
  // Zero records in the window -> 0, never throws.
  const row = { key: 'ced:99' };
  const n = visitasUltimos7Dias(row, { stickers: [], surveys: [], today: '2026-01-09' });
  assert.equal(n, 0);
}
console.log('visitasUltimos7Dias: zero records -> 0 OK');

// ── W10: reportFilenameSlug ──────────────────────────────────────────────────
assert.equal(reportFilenameSlug('Gil Soto'), 'Gil_Soto');
assert.equal(reportFilenameSlug(''), 'profesional', 'blank name falls back to a stable slug, never an empty filename');
assert.equal(reportFilenameSlug('   '), 'profesional', 'whitespace-only name falls back too');
assert.equal(reportFilenameSlug(null), 'profesional');
assert.equal(reportFilenameSlug(undefined), 'profesional');
assert.equal(reportFilenameSlug('Gil/Soto?'), 'Gil_Soto_', 'unsafe filename characters collapse to _, never dropped silently in a way that could collide');
console.log('reportFilenameSlug OK');

// ── W10: buildProfessionalReportDocDefinition — ctx-driven extensions ───────
{
  const row = {
    name: 'Gil Soto', cedula: '123', codigo: '004', entidad: 'DAGMA', np: 'P2',
    tarjetaProfesional: '', celular: '', correo: '', barriosActivos: [],
    stickersFase1: 2, stickersFase2: 1, surveyTotal: 3, total: 6,
    firstDate: '2026-01-01', lastDate: '2026-01-05', activeDays: 3, avgPerActiveDay: 2,
    avgStickersPerDay: 1, rosterSourced: 0,
  };
  const points = { stickerPoints: [], surveyPoints: [] };
  const ctx = {
    from: '2026-01-01', to: '2026-01-05', generatedAt: '2026-01-06', today: '2026-01-06',
    objetivoDiario: null, degraded: false,
  };
  const doc = buildProfessionalReportDocDefinition(row, points, { ...ctx, last7: 2 });
  const flatText = JSON.stringify(doc.content);
  assert.ok(/sin dato/i.test(flatText), 'objetivoDiario null must render "sin dato", never a lying number');
  assert.ok(flatText.includes('—'), 'blank tarjeta/celular/correo/barrios must render the forced DASH');
  // M7 (CONTRACT CHANGE): the confidentiality notice moved OUT of `content`
  // and into a repeating `footer:` function (see the dedicated M7 test
  // below) — a ~110-person mass export must show it on every physical page,
  // not just once wherever it happened to sit in the flattened content
  // array. `doc.content` must therefore no longer carry it.
  assert.ok(!/personal/i.test(flatText), 'M7: the confidentiality notice no longer lives in content — moved to the repeating footer');
  // W11: TP is also shown inline in the subtitle — a blank tarjetaProfesional
  // must render the DASH there too, never a silently blank "TP: " label.
  assert.ok(/TP:\s*—/.test(flatText), 'a blank tarjeta profesional must render as DASH in the subtitle, not blank');

  // M6: a projection deadline caption ("Proyectado al …") attached to a
  // non-existent projection is fabricated confidence — when ctx.objetivoDiario
  // is null the "Visita objetivo diario" stat card must show "sin dato" with
  // NO deadline text anywhere in its own structure (main's pre-redesign code
  // never showed one either in this case).
  const rows = doc.content.filter((n) => n && Array.isArray(n.columns));
  const cards = rows.flatMap((r) => r.columns);
  const objetivoCard = cards.find((c) => JSON.stringify(c).includes('Visita objetivo diario'));
  assert.ok(objetivoCard, 'the objetivo diario stat card must be present');
  assert.ok(!JSON.stringify(objetivoCard).includes('Proyectado al'), 'M6: no deadline caption when objetivoDiario is null (sin dato)');

  const docOther = buildProfessionalReportDocDefinition(row, points, { ...ctx, last7: 9 });
  assert.notEqual(JSON.stringify(docOther.content), flatText, 'ctx.last7 must actually reach the rendered report');
  assert.ok(JSON.stringify(docOther.content).includes('"9"'), 'the last7 value must render as its own cell text');
}
console.log('buildProfessionalReportDocDefinition: ctx-driven fields (objetivoDiario sin dato, DASH-forced blanks, confidentiality line, last7) OK');

// ── M7: the confidentiality notice is now a pdfmake `footer:` function so it
// repeats on EVERY page — a ~110-person mass export must show it for every
// professional's pages, not only once wherever it happened to sit in the
// flattened content array (previously it sat at the end of each
// professional's own content block, unreachable once a reader has scrolled
// past it on a multi-page-per-professional export). ────────────────────────
{
  const row = {
    name: 'Gil Soto', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0,
    avgStickersPerDay: null, rosterSourced: 0,
  };
  const points = { stickerPoints: [], surveyPoints: [] };
  const doc = buildProfessionalReportDocDefinition(row, points, {});
  assert.ok(typeof doc.footer === 'function', 'M7: doc.footer must be a function so pdfmake repeats it on every page');
  const rendered = doc.footer(1, 3);
  assert.ok(/personal/i.test(JSON.stringify(rendered)), 'M7: the footer must carry the confidentiality notice');

  // Two SEPARATE calls to the builder for identical inputs must still
  // produce a footer that deepStrictEqual accepts as equal — function
  // objects compare by REFERENCE only (never by behavior), so this only
  // holds if the footer is a STABLE module-scope reference, same trick as
  // the pre-existing REPORT_CARD_LAYOUT fix. Verify this explicitly by
  // diffing `footer`, not just `.content` (the pre-existing mass≡individual
  // test only ever diffed content).
  const docAgain = buildProfessionalReportDocDefinition(row, points, {});
  assert.deepEqual(doc.footer, docAgain.footer, 'M7: footer must be the SAME stable function reference across separate builder calls');
}
console.log('buildProfessionalReportDocDefinition (M7): confidentiality notice is a stable, repeating page footer OK');

{
  const row = {
    name: 'X', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0,
    avgStickersPerDay: null, rosterSourced: 0,
  };
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] }, { objetivoDiario: 3.3, last7: 0 });
  const flatText = JSON.stringify(doc.content);
  assert.ok(flatText.includes('3.3'), 'a real objetivoDiario value must render');
  assert.ok(/30 de septiembre de 2026/.test(flatText), 'must show the "Proyectado al 30 de septiembre de 2026" phrasing');
}
console.log('buildProfessionalReportDocDefinition: objetivoDiario real value renders with the "Proyectado…" phrasing OK');

{
  const row = { name: 'X' };
  assert.throws(
    () => buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] }, { degraded: true }),
    /degradad/i,
    'a degraded data source must refuse to build the report, not silently ship redacted/wrong data',
  );
}
console.log('buildProfessionalReportDocDefinition: degraded ctx refuses to build (throws) OK');

{
  const row = {
    name: 'Gil Soto', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 60, stickersFase2: 0, surveyTotal: 0, total: 60,
    firstDate: '2026-01-01', lastDate: '2026-03-01', activeDays: 60, avgPerActiveDay: 1,
    avgStickersPerDay: 1, rosterSourced: 0,
  };
  const stickerPoints = Array.from({ length: 60 }, (_, i) => ({
    codigo: `COD-${i}`, direccion: 'x', municipio: 'Cali', fecha: '2026-01-01', faseLabel: 'Fase I',
  }));
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints, surveyPoints: [] }, {});
  const flatText = JSON.stringify(doc.content);
  assert.ok(flatText.includes('COD-49'), 'the 50th row (index 49) must still be included');
  assert.ok(!flatText.includes('COD-50'), 'row 51 (index 50) must be cut off — cap is 50');
  assert.ok(/y 10 m.s/i.test(flatText), 'overflow note must report the exact excess count (60-50=10)');
}
console.log('buildProfessionalReportDocDefinition: sticker listing capped at 50 rows + overflow note OK');

{
  const row = {
    name: '', cedula: '', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0,
    avgStickersPerDay: null, rosterSourced: 0,
  };
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] }, {});
  const flatText = JSON.stringify(doc.content);
  assert.ok(flatText.includes('Sin dato'), 'a blank name must render as "Sin dato" in the title, never an empty header');
}
console.log('buildProfessionalReportDocDefinition: row.name === "" renders "Sin dato" OK');

{
  // A professional with zero stickers in the requested period still renders
  // "Sin registros" instead of an empty/blank table (existing convention,
  // still exercised here explicitly for the period-filtered W10 flow).
  const row = {
    name: 'Sin Stickers', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 2, total: 2,
    firstDate: '2026-01-01', lastDate: '2026-01-02', activeDays: 2, avgPerActiveDay: 1,
    avgStickersPerDay: null, rosterSourced: 0,
  };
  const doc = buildProfessionalReportDocDefinition(row, {
    stickerPoints: [], surveyPoints: [{ direccion: 'X', nombreEdificacion: 'Y', fecha: '2026-01-01' }],
  }, {});
  assert.ok(/sin registros/i.test(JSON.stringify(doc.content)));
}
console.log('buildProfessionalReportDocDefinition: zero stickers in range -> "Sin registros" OK');

// ── W10: buildMassReportDocDefinition ────────────────────────────────────────
{
  const rowA = {
    name: 'Ana Ruiz', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 1, stickersFase2: 0, surveyTotal: 0, total: 1,
    firstDate: '2026-01-01', lastDate: '2026-01-01', activeDays: 1, avgPerActiveDay: 1,
    avgStickersPerDay: 1, rosterSourced: 0,
  };
  const rowB = {
    name: 'Beto Ríos', cedula: '2', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 1, surveyTotal: 0, total: 1,
    firstDate: '2026-01-02', lastDate: '2026-01-02', activeDays: 1, avgPerActiveDay: 1,
    avgStickersPerDay: 1, rosterSourced: 0,
  };
  const pointsA = { stickerPoints: [{ codigo: 'A1', direccion: '', municipio: '', fecha: '2026-01-01', faseLabel: 'Fase I' }], surveyPoints: [] };
  const pointsB = { stickerPoints: [{ codigo: 'B1', direccion: '', municipio: '', fecha: '2026-01-02', faseLabel: 'Fase II' }], surveyPoints: [] };
  const ctx = {
    from: '2026-01-01', to: '2026-01-02', generatedAt: '2026-01-03', today: '2026-01-03', objetivoDiario: null, degraded: false,
  };

  const mass = buildMassReportDocDefinition([
    { row: rowA, points: pointsA, last7: 1 },
    { row: rowB, points: pointsB, last7: 1 },
  ], ctx);

  const soloA = buildProfessionalReportDocDefinition(rowA, pointsA, { ...ctx, last7: 1 });
  const soloB = buildProfessionalReportDocDefinition(rowB, pointsB, { ...ctx, last7: 1 });

  const breakIndex = mass.content.findIndex((n) => n && n.pageBreak === 'before');
  assert.ok(breakIndex > 0, 'a page break must exist before the second professional');
  const firstBlock = mass.content.slice(0, breakIndex);
  const secondBlock = mass.content.slice(breakIndex).map((n, i) => {
    if (i !== 0) return n;
    const { pageBreak, ...rest } = n;
    return rest;
  });
  assert.deepEqual(firstBlock, soloA.content, 'first professional block must match the solo builder exactly');
  assert.deepEqual(secondBlock, soloB.content, 'second professional block (pageBreak stripped) must match the solo builder exactly');

  // M7: the mass export is ONE docDefinition/one PDF, so the confidentiality
  // footer must be attached at the TOP LEVEL of the mass doc too (not only
  // inside each merged-away per-professional `content`, which the merge
  // above never carries a `footer` key from anyway) — otherwise only page 1
  // of the whole mass PDF would ever show it. Diff `footer` explicitly, not
  // just `.content` (the pre-existing equality test above only ever diffed
  // content) — same deepStrictEqual-by-reference trick as REPORT_CARD_LAYOUT.
  assert.ok(typeof mass.footer === 'function', 'M7: buildMassReportDocDefinition must expose a top-level footer function');
  assert.deepEqual(mass.footer, soloA.footer, 'M7: the mass doc footer must be the SAME stable reference the solo builder uses');
}
console.log('buildMassReportDocDefinition: individual and mass builders produce identical per-professional content OK');

{
  // 0 professionals -> a single informational page, never an empty content array.
  const doc = buildMassReportDocDefinition([], {});
  assert.ok(doc.content.length > 0, 'must never ship an empty content array');
  assert.ok(JSON.stringify(doc.content).includes('Sin profesionales para los filtros seleccionados'));
}
console.log('buildMassReportDocDefinition: 0 professionals -> single "sin profesionales" page OK');

{
  // Degraded ctx refuses the whole batch too, even with 0 rows.
  assert.throws(() => buildMassReportDocDefinition([], { degraded: true }), /degradad/i);
}
console.log('buildMassReportDocDefinition: degraded ctx refuses (throws) OK');

{
  // W10 perf sanity (Node-side only -- pdfmake's actual PAGE LAYOUT cannot run
  // outside a browser; the real timing/heap gate from the plan's performance
  // budget (< 20 s and < 700 MB for 110 professionals) must be measured
  // manually in DevTools against the real dataset.
  // TODO perf gate: manual measurement required (browser: getBlob() timing +
  // heap snapshot around the mass-export click, per the plan's own
  // "Presupuesto de rendimiento" table, row "Export masivo build/total").
  const rowsWithPoints = Array.from({ length: 110 }, (_, i) => {
    const row = {
      key: `ced:${i}`, name: `Profesional ${i}`, cedula: String(1000 + i), codigo: 'B1', entidad: 'DAGRD',
      np: 'P2', tarjetaProfesional: 'TP', celular: '300', correo: 'x@x.com',
      stickersFase1: 15, stickersFase2: 15, surveyTotal: 0, total: 30,
      firstDate: '2026-08-01', lastDate: '2026-09-01', activeDays: 20, avgPerActiveDay: 1.5,
      avgStickersPerDay: 1.5, rosterSourced: 0, barriosActivos: ['Barrio A'],
    };
    const stickerPoints = Array.from({ length: 30 }, (_, j) => ({
      codigo: `COD-${i}-${j}`, direccion: `Calle ${j}`, municipio: 'Cali', fecha: '2026-08-15', faseLabel: j % 2 ? 'Fase I' : 'Fase II',
    }));
    return { row, points: { stickerPoints, surveyPoints: [] }, last7: 5 };
  });
  const def = buildMassReportDocDefinition(rowsWithPoints, {
    from: '2026-08-01', to: '2026-09-01', generatedAt: '2026-09-13', objetivoDiario: 2.5, degraded: false, today: '2026-09-13',
  });
  const pageBreaks = def.content.filter((n) => n && n.pageBreak === 'before').length;
  assert.equal(pageBreaks, 109, '110 professionals -> 109 page breaks (none before the first)');
  const size = JSON.stringify(def).length;
  assert.ok(size < 3 * 1024 * 1024, `doc-definition JSON should stay under 3MB (was ${size} bytes)`);
  console.log(`buildMassReportDocDefinition: 110-professional doc-definition size = ${(size / 1024).toFixed(1)} KB`);
}
console.log('buildMassReportDocDefinition: 110-professional perf sanity (page breaks + size) — TODO perf gate: manual browser measurement per plan OK');

// ── H2: cellHtml — every sticker-derived column masked behind DASH while
// !stickersLoaded, same "Degradado/cargando ≠ cero" invariant kpisHtml/
// rowHtml already apply elsewhere. `np`/`codigo`/`cedula`/`barriosActivos`/
// `activeDays`/`firstDate`/`lastDate` used to render their real ("Sin dato"
// or a real value) content even while stickers hadn't resolved yet -- an
// inconsistency with `daysSinceFirst`, which WAS already masked. ──────────
{
  const row = {
    name: 'Gil Soto', cedula: '123', np: 'P2', codigo: 'B1', tarjetaProfesional: 'TP-9988',
    stickersFase1: 2, stickersFase2: 1, surveyTotal: 1, activeDays: 3,
    avgStickersPerDay: 1.5, barriosActivos: ['San Antonio'],
    firstDate: '2026-01-01', lastDate: '2026-01-05', daysSinceFirst: 8,
  };
  const maskedColumns = ['np', 'codigo', 'cedula', 'tarjetaProfesional', 'barriosActivos', 'activeDays', 'firstDate', 'lastDate'];
  for (const key of maskedColumns) {
    assert.equal(cellHtml(row, key, false), DASH, `H2: column "${key}" must be DASH-masked while stickersLoaded=false`);
  }
  // Same columns, stickersLoaded true -> real values, never DASH.
  assert.equal(cellHtml(row, 'np', true), 'P2');
  assert.equal(cellHtml(row, 'codigo', true), 'B1');
  assert.equal(cellHtml(row, 'cedula', true), '123');
  assert.equal(cellHtml(row, 'tarjetaProfesional', true), 'TP-9988');
  assert.equal(cellHtml(row, 'barriosActivos', true), 'San Antonio');
  assert.equal(cellHtml(row, 'activeDays', true), 3);
  assert.equal(cellHtml(row, 'firstDate', true), '2026-01-01');
  assert.equal(cellHtml(row, 'lastDate', true), '2026-01-05');
  // Already-masked column (daysSinceFirst) stays masked -- no regression.
  assert.equal(cellHtml(row, 'daysSinceFirst', false), DASH);
}
console.log('cellHtml (H2): np/codigo/cedula/tarjetaProfesional/barriosActivos/activeDays/firstDate/lastDate all DASH-masked while !stickersLoaded OK');

// ── W-TP: cellHtml renders tarjetaProfesional like cedula/codigo (blank ->
// "Sin dato", same masking rule) ────────────────────────────────────────
{
  const rowWithTp = { tarjetaProfesional: 'TP-123' };
  const rowBlankTp = { tarjetaProfesional: '' };
  assert.equal(cellHtml(rowWithTp, 'tarjetaProfesional', true), 'TP-123');
  assert.equal(cellHtml(rowBlankTp, 'tarjetaProfesional', true), 'Sin dato');
  assert.equal(cellHtml(rowWithTp, 'tarjetaProfesional', false), DASH);
}
console.log('cellHtml: tarjetaProfesional renders real value / "Sin dato" / DASH-masked, same rule as cedula/codigo OK');

// ── M4: buildProfessionalReportDocDefinition must use ctx.generatedAt when
// given, instead of always calling downloadStamp() itself -- a sentinel
// string that can never come out of a real downloadStamp() call proves the
// ctx value actually reached the render, not a coincidentally-matching
// timestamp. ──────────────────────────────────────────────────────────────
{
  const row = {
    name: 'Gil Soto', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    stickersFase1: 0, stickersFase2: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0,
    avgStickersPerDay: null, rosterSourced: 0,
  };
  const doc = buildProfessionalReportDocDefinition(row, { stickerPoints: [], surveyPoints: [] }, {
    generatedAt: 'SENTINEL_TIMESTAMP_2026',
  });
  const flatText = JSON.stringify(doc.content);
  assert.ok(flatText.includes('SENTINEL_TIMESTAMP_2026'), 'M4: ctx.generatedAt must render verbatim, never overridden by downloadStamp()');
}
console.log('buildProfessionalReportDocDefinition: ctx.generatedAt (M4) overrides downloadStamp() OK');

// ── L10: buildTemporalMetricsByKey must honor from/to — sibling functions
// (buildProfessionalRows' stickersTotal/surveyTotal, buildTimeline) already
// drop out-of-range records; the hour-of-day columns used to silently
// ignore the active Desde/Hasta filter. ────────────────────────────────────
{
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-01T09:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
    // Outside the [from,to] range below -- must NOT affect prevDay/averages.
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-08T18:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys: [] });
  const withRange = buildTemporalMetricsByKey({
    stickers, surveys: [], identity, today: '2026-01-09', from: '2026-01-01', to: '2026-01-02',
  });
  const metrics = withRange.get('ced:1');
  assert.ok(metrics, 'the in-range record must still produce a metrics entry');
  assert.equal(metrics.prevDay, '2026-01-01', 'L10: the out-of-range 01-08 record must not become prevDay');
  assert.notEqual(metrics.prevDay, '2026-01-08');

  // Without a range (from/to both null, the existing default): unchanged --
  // both records count, same as before this fix.
  const noRange = buildTemporalMetricsByKey({
    stickers, surveys: [], identity, today: '2026-01-09',
  });
  assert.equal(noRange.get('ced:1').prevDay, '2026-01-08', 'L10: no active range -> unchanged behavior (both records count)');
}
console.log('buildTemporalMetricsByKey (L10): honors from/to, unchanged when absent OK');

// ── M6: professionalRecordsByKey / visitasUltimos7DiasByKey — ONE batch pass
// must agree EXACTLY with calling professionalRecords/visitasUltimos7Dias
// once per row, on a mixed fixture (several professionals, SIN_DATO mixed
// in, a period filter active). ─────────────────────────────────────────────
{
  const stickers = [
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-05T09:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion', codigo_edificacion: 'C1' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-06T14:30:00+00:00', fase: 2, fuente: 'atencionsismo', inspector_fuente: 'evaluacion', codigo_edificacion: 'C2' },
    { inspector: { nombre_completo: 'Gil Soto', identificacion: '1' }, fecha: '2026-01-05T20:00:00+00:00', fase: null, fuente: 'atencionsismo', inspector_fuente: 'evaluacion', codigo_edificacion: 'C3' }, // SIN_DATO
    { inspector: { nombre_completo: 'Ana Ruiz', identificacion: '2' }, fecha: '2026-01-07T12:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion', codigo_edificacion: 'C4' },
    { inspector: { nombre_completo: 'Beto Ríos', identificacion: '3' }, fecha: '2026-01-20T12:00:00+00:00', fase: 1, fuente: 'atencionsismo', inspector_fuente: 'evaluacion', codigo_edificacion: 'C5' }, // outside period below
  ];
  const surveys = [
    { nombre_evaluador: 'Gil Soto', fecha_inspeccion: '2026-01-08', direccion: 'X' },
    { nombre_evaluador: 'Ana Ruiz', fecha_inspeccion: '2026-01-08', direccion: 'Y' },
  ];
  const identity = buildIdentityIndex({ stickers, surveys });
  const rows = [{ key: 'ced:1' }, { key: 'ced:2' }, { key: 'ced:3' }, { key: 'nom:nadie' }];
  const from = '2026-01-01';
  const to = '2026-01-08';
  const today = '2026-01-09';

  const byKey = professionalRecordsByKey({
    stickers, surveys, identity, from, to,
  });
  const last7ByKey = visitasUltimos7DiasByKey({
    stickers, surveys, identity, today,
  });

  for (const row of rows) {
    const perRow = professionalRecords(row, {
      stickers, surveys, identity, from, to,
    });
    const batch = byKey.get(row.key) || { stickerPoints: [], surveyPoints: [] };
    assert.deepEqual(batch, perRow, `M6: batch professionalRecordsByKey must equal per-row professionalRecords for ${row.key}`);

    const perRowLast7 = visitasUltimos7Dias(row, {
      stickers, surveys, identity, today,
    });
    const batchLast7 = last7ByKey.get(row.key) || 0;
    assert.equal(batchLast7, perRowLast7, `M6: batch visitasUltimos7DiasByKey must equal per-row visitasUltimos7Dias for ${row.key}`);
  }
}
console.log('professionalRecordsByKey / visitasUltimos7DiasByKey (M6): batch equals per-row on a mixed fixture OK');

// ── L7: rowReportButtonsBlocked — per-row "📄 Reporte" buttons must also be
// blocked while a mass export is `busy`, same as the mass/XLSX buttons
// (updateDownloadAvailability's own isDegraded||!stickersLoaded block). ────
assert.equal(rowReportButtonsBlocked(), false, 'defaults: nothing blocks the row buttons');
assert.equal(rowReportButtonsBlocked({ isDegraded: true }), true);
assert.equal(rowReportButtonsBlocked({ stickersLoaded: false }), true);
assert.equal(rowReportButtonsBlocked({ busy: true }), true, 'L7: a mass export in flight must block the per-row report buttons too');
assert.equal(rowReportButtonsBlocked({ isDegraded: false, stickersLoaded: true, busy: false }), false);
console.log('rowReportButtonsBlocked (L7) OK');

// ── L8: module-level export guard — canStartMassExport (a second start
// while one is in flight is a no-op) + shouldDeliverExport (skip the
// download entirely, never just a "stale" toast after already downloading,
// when the tab was reloaded mid-build). ────────────────────────────────────
assert.equal(canStartMassExport(), true, 'nothing in flight -> may start');
assert.equal(canStartMassExport({ exportInFlight: true }), false, 'L8: a second export must be a no-op while one is already in flight');
assert.equal(canStartMassExport({ exportInFlight: false }), true);
assert.equal(shouldDeliverExport({ seq: 3, loadSeq: 3 }), true, 'same generation -> deliver');
assert.equal(shouldDeliverExport({ seq: 3, loadSeq: 4 }), false, 'L8: the tab reloaded mid-build (loadSeq bumped) -> never deliver the stale build');
console.log('canStartMassExport / shouldDeliverExport (L8) OK');

// ── M6 (mass export overlay text) ───────────────────────────────────────────
assert.equal(massExportOverlayText(1), 'Generando 1 reportes… esto puede tardar unos segundos.');
assert.equal(massExportOverlayText(37), 'Generando 37 reportes… esto puede tardar unos segundos.');
assert.equal(massExportOverlayText(0), 'Generando 0 reportes… esto puede tardar unos segundos.');
console.log('massExportOverlayText (M6) OK');

// ── Task 4.4/4.5: grupoExternosRowHtml — GRUPO-EXTERNOS renders as ONE row,
// collapsed by default, with a toggle that reveals grupo_externos.detalle
// inline (spec: "Non-Person Group Row Is Expandable"). ─────────────────────

{
  // No grupo_externos at all (depuracion inactive, or every non-person
  // record was exempted via alias_nombres) -> no row, never a stray empty tr.
  assert.equal(grupoExternosRowHtml(null), '');
  assert.equal(grupoExternosRowHtml(undefined), '');
}
console.log('grupoExternosRowHtml: null/undefined -> no row rendered OK');

{
  const grupoExternos = {
    identidad_key: 'GRUPO-EXTERNOS',
    n_colapsados: 2,
    estado_sugerido: 'grupo_externos_agrupado',
    fuente_dato: 'grupo_agregado (main, 2 registros colapsados)',
    detalle: [
      { nombre_completo: 'Juan Sospechoso', identificacion: '999', motivo: 'cedula_sospechosa', ultimo_sticker: '2026-08-30' },
      { nombre_completo: 'Cuenta Generica', identificacion: '888', motivo: 'cuenta_no_persona', ultimo_sticker: null },
    ],
  };
  const html = grupoExternosRowHtml(grupoExternos, 5);
  // Scenario: "Collapsed by default" -- the detail block ships with the
  // `hidden` attribute; a click handler (wired in initSeguimiento) removes
  // it, it is never removed/rebuilt by this pure function itself.
  assert.match(html, /seg-externos-detail[^>]*hidden/, 'the detail block must be collapsed (hidden) by default');
  assert.match(html, /colspan="5"/);
  assert.match(html, /2/, 'the aggregate count (n_colapsados) must be visible on the collapsed row itself');
  // Scenario: "Expanding the aggregate row shows individual entries" -- the
  // 2 individual entries are already present in the markup (toggling
  // `hidden` in the DOM is what "expanding" means; the pure function's job
  // is making sure the content EXISTS to reveal).
  assert.match(html, /Juan Sospechoso/);
  assert.match(html, /Cuenta Generica/);
  assert.match(html, /cedula_sospechosa/);
  assert.match(html, /cuenta_no_persona/);
}
console.log('grupoExternosRowHtml: collapsed by default, detail entries present for expansion OK');

{
  // Escaping: a detalle entry's own fields must never inject raw HTML.
  const grupoExternos = {
    n_colapsados: 1, fuente_dato: '', estado_sugerido: '',
    detalle: [{ nombre_completo: '<img src=x onerror=alert(1)>', identificacion: '1', motivo: 'cedula_sospechosa', ultimo_sticker: null }],
  };
  const html = grupoExternosRowHtml(grupoExternos, 3);
  assert.ok(!html.includes('<img src=x'), 'a malicious nombre_completo must be HTML-escaped');
}
console.log('grupoExternosRowHtml: detail fields are HTML-escaped OK');

// ── Task 4.6/4.7: revisionManualHtml — depuracion.revision_manual, with an
// EXPLICIT empty state (spec: "Manual Review Section Surfaces Unresolved
// Depuration Cases"). ──────────────────────────────────────────────────────

{
  const html = revisionManualHtml([]);
  assert.match(html, /[Ss]in pendientes/, 'an empty list must render an explicit "nothing pending" state, never a blank/hidden section');
}
console.log('revisionManualHtml: empty list -> explicit "nothing pending" state, not a hidden section OK');

{
  assert.equal(revisionManualHtml(null), revisionManualHtml([]), 'malformed input degrades to the same empty state, never throws');
  assert.equal(revisionManualHtml(undefined), revisionManualHtml([]));
}
console.log('revisionManualHtml: malformed input tolerated, same empty state OK');

{
  const list = [
    { motivo: 'codigo_vercel_duplicado', codigo: '097' },
    { motivo: 'codigo_remap_candidato', codigo: '041', nombre_vercel: 'juan perez', identidad_key_candidato: '123', score: 92.5 },
  ];
  const html = revisionManualHtml(list);
  assert.match(html, /codigo_vercel_duplicado/);
  assert.match(html, /097/);
  assert.match(html, /codigo_remap_candidato/);
  assert.match(html, /123/, 'the candidate identidad_key must be visible without opening the notebook');
  assert.match(html, /92\.5/);
}
console.log('revisionManualHtml: renders every entry (remap conflict + Vercel duplicate) without needing an external file OK');

// ── Task 4.8: depuracionBadgeHtml — freshness/degraded indicator ──────────

assert.equal(depuracionBadgeHtml(null), null, 'no identity at all -> no badge');
assert.equal(
  depuracionBadgeHtml({ depuracionActiva: false, depuracionMotivo: '' }),
  null,
  // A REAL identity built from an absent depuracion carries `depuracionAusente:
  // true` and is silent too (test_absent_depuracion_block_is_silent_no_banner);
  // this hand-built identity has no such flag, and the old contract holds.
  'a hand-built identity without the depuracionAusente flag and with no motivo -> no badge',
);
{
  const badge = depuracionBadgeHtml({ depuracionActiva: false, depuracionMotivo: 'sin_blob' });
  assert.match(badge, /sin_blob/, 'activa:false must surface the degraded motivo');
}
{
  const badge = depuracionBadgeHtml({ depuracionActiva: true, referenciaGeneradaEn: '2026-09-12' });
  assert.match(badge, /2026-09-12/, 'activa:true must surface referencia_generada_en so the user knows how fresh it is');
}
console.log('depuracionBadgeHtml: activa:false surfaces the motivo, activa:true surfaces the freshness date OK');

// ══ Phase 11 (PR 10, part 1) — seeding, KPIs, estado filter, mass export ═════
// scope, manual review, degraded banner (design D17; spec "Extension
// 2026-09-19 — Delta"). Each block is a NAMED test (tasks 11.1-11.17) run
// through `named()`, which records a failure and keeps going so a RED run
// lists EVERY missing behaviour at once; the file still throws at the end of
// the block. Tests marked "characterization" pass on the pre-change code on
// purpose: they pin behaviour the seeding must NOT alter.

const phase11Failures = [];
function named(name, fn) {
  try {
    fn();
    console.log(`${name} OK`);
  } catch (err) {
    phase11Failures.push(name);
    console.error(`${name} FAILED: ${err && err.message ? String(err.message).split('\n')[0] : err}`);
  }
}

const P11_TODAY = '2026-09-19';

function depInspector(i, extra = {}) {
  const ced = String(1000000 + i);
  return {
    identidad_key: ced,
    identificacion: ced,
    nombre_completo: `Profesional ${i}`,
    np: 'P2',
    np_fuente: 'vercel',
    fase: 'FASE_I',
    estado_sugerido: 'revisar',
    codigo: '',
    entidad: '',
    cedulas_unificadas: [],
    ...extra,
  };
}
function depuracionOf(inspectores, extra = {}) {
  return {
    activa: true,
    motivo: '',
    referencia_generada_en: '2026-09-19',
    inspectores,
    grupo_externos: null,
    alias_nombres: {},
    revision_manual: [],
    ...extra,
  };
}
function stickerFor(cedula, nombre, fecha = '2026-09-10T15:00:00+00:00', extra = {}) {
  return {
    inspector: { nombre_completo: nombre, identificacion: cedula },
    inspector_fuente: 'evaluacion',
    fuente: 'atencionsismo',
    fase: 1,
    fecha,
    ...extra,
  };
}
function rowsFor({
  stickers = [], surveys = [], depuracion = null, from = null, to = null,
} = {}) {
  const identity = buildIdentityIndex({ stickers, surveys, depuracion });
  return buildProfessionalRows({
    stickers, surveys, from, to, identity, today: P11_TODAY,
  });
}
// 373 seeded people, the first `nActive` of them with 4 stickers over 2 days.
function seededUniverse(nSeeded = 373, nActive = 116) {
  const inspectores = Array.from({ length: nSeeded }, (_, i) => depInspector(i + 1));
  const stickers = [];
  for (let i = 1; i <= nActive; i += 1) {
    const ced = String(1000000 + i);
    stickers.push(stickerFor(ced, `Profesional ${i}`, '2026-09-10T15:00:00+00:00'));
    stickers.push(stickerFor(ced, `Profesional ${i}`, '2026-09-10T16:00:00+00:00'));
    stickers.push(stickerFor(ced, `Profesional ${i}`, '2026-09-11T15:00:00+00:00'));
    stickers.push(stickerFor(ced, `Profesional ${i}`, '2026-09-11T16:00:00+00:00'));
  }
  return { inspectores, stickers, depuracion: depuracionOf(inspectores) };
}

// ── 11.1 / 11.2 (seeding) ───────────────────────────────────────────────────

named('test_rows_seeded_from_depuracion_inspectores', () => {
  const dep = depuracionOf(
    [depInspector(1), depInspector(2), depInspector(3)],
    { alias_nombres: { 'profesional 2': '1000002' } },
  );
  const stickers = [
    stickerFor('1000001', 'Profesional 1'),
    stickerFor('1000001', 'Profesional 1', '2026-09-11T15:00:00+00:00'),
  ];
  const surveys = [{ nombre_evaluador: 'Profesional 2', fecha_inspeccion: '2026-09-12' }];
  const { rows } = rowsFor({ stickers, surveys, depuracion: dep });
  assert.equal(rows.length, 3, 'one row per depuracion.inspectores entry');
  const byKey = new Map(rows.map((r) => [r.key, r]));
  const zero = byKey.get('ced:1000003');
  assert.ok(zero, 'the zero-activity person has a row');
  assert.equal(zero.name, 'Profesional 3');
  assert.equal(zero.cedula, '1000003');
  assert.equal(zero.np, 'P2');
  assert.equal(zero.estadoSugerido, 'revisar');
  for (const field of ['stickersFase1', 'stickersFase2', 'stickersTotal', 'surveyTotal', 'total', 'activeDays', 'avgPerActiveDay']) {
    assert.equal(zero[field], 0, `zero counter: ${field}`);
  }
  assert.equal(zero.firstDate, null);
  assert.equal(zero.lastDate, null);
  // Enrichment still lands on the seeded rows (sticker by cédula, survey by alias).
  assert.equal(byKey.get('ced:1000001').stickersFase1, 2);
  assert.equal(byKey.get('ced:1000002').surveyTotal, 1);
  // Triangulation: a range that excludes every record still lists all 3.
  const outOfRange = rowsFor({
    stickers, surveys, depuracion: dep, from: '2026-01-01', to: '2026-01-31',
  });
  assert.equal(outOfRange.rows.length, 3);
  assert.ok(outOfRange.rows.every((r) => r.total === 0));
});

named('test_seeding_empty_depuracion_inspectores_falls_back_to_records', () => {
  const dep = depuracionOf([]);
  const stickers = [stickerFor('7000001', 'Solo Sticker')];
  const result = rowsFor({ stickers, depuracion: dep });
  assert.equal(result.rows.length, 1, 'nothing seeded: the sticker still creates its row');
  assert.equal(result.rows[0].stickersTotal, 1);
  // Judgment-day C2 (deliberate change): the padrón is the number of SEEDED
  // profiles, not the number of rows. Nothing was seeded, so it is 0 even
  // though the orphan sticker still produced one (unseeded) row.
  assert.equal(result.totals.padron, 0);
  const none = rowsFor({ depuracion: dep });
  assert.deepEqual(none.rows, []);
  assert.equal(none.totals.professionals, 0);
  assert.equal(none.totals.padron, 0);
});

named('test_seeding_tolerates_null_and_malformed_entries', () => {
  const dep = depuracionOf([
    null,
    undefined,
    {},
    { identidad_key: null, identificacion: null, nombre_completo: 'Sin cedula' },
    depInspector(1, { nombre_completo: null, np: null, estado_sugerido: null, codigo: undefined }),
    depInspector(2),
    depInspector(2, { nombre_completo: 'Duplicada' }),
  ]);
  const { rows } = rowsFor({ depuracion: dep });
  assert.deepEqual(rows.map((r) => r.key).sort(), ['ced:1000001', 'ced:1000002']);
  const nullish = rows.find((r) => r.key === 'ced:1000001');
  assert.equal(nullish.name, '');
  assert.equal(nullish.np, '');
  assert.equal(nullish.estadoSugerido, '');
  assert.equal(nullish.codigo, '');
});

named('test_seeding_cedula_forms_and_unificadas_route_every_sticker_to_one_row', () => {
  const dep = depuracionOf([
    depInspector(1, { identidad_key: '166000', identificacion: '166.000' }),
    depInspector(2, { identidad_key: '1234567', identificacion: '1234567', cedulas_unificadas: ['1.234.567', '1234567.0', '0001234567', 'CC'] }),
    depInspector(3, { identidad_key: '0012345', identificacion: '0012345' }),
    // the same alias is claimed by TWO survivors: it must still resolve to ONE row
    depInspector(4, { cedulas_unificadas: ['555'] }),
    depInspector(5, { cedulas_unificadas: ['555'] }),
  ]);
  const stickers = [
    stickerFor('166.000', 'Profesional 1'),
    stickerFor('166000', 'Profesional 1'),
    stickerFor('1234567.0', 'Profesional 2'),
    stickerFor('1.234.567', 'Profesional 2'),
    stickerFor('0001234567', 'Profesional 2'),
    stickerFor('0012345', 'Profesional 3'),
    stickerFor('12345', 'Otro Distinto'), // zero-preserving: NOT the padded cédula's row
    stickerFor('555', 'Profesional 4'),
  ];
  const { rows, totals } = rowsFor({ stickers, depuracion: dep });
  const byKey = new Map(rows.map((r) => [r.key, r]));
  assert.equal(byKey.get('ced:166000').stickersTotal, 2);
  assert.equal(byKey.get('ced:1234567').stickersTotal, 3);
  assert.equal(byKey.get('ced:0012345').stickersTotal, 1);
  assert.equal(byKey.get(`nom:${normalizeName('Otro Distinto')}`).stickersTotal, 1, 'unpadded cédula is NOT the padded one: its own row, never dropped');
  const claimed = (byKey.get('ced:1000004').stickersTotal) + (byKey.get('ced:1000005').stickersTotal);
  assert.equal(claimed, 1, 'a doubly-claimed alias resolves to exactly one row, no drop, no double count');
  const perRow = rows.reduce((n, r) => n + r.stickersTotal, 0);
  assert.equal(perRow, stickers.length, 'every sticker landed on a row');
  assert.equal(totals.stickers, stickers.length);
});

named('test_seeding_unicode_and_huge_names_and_search', () => {
  const huge = 'Ñ'.repeat(50000);
  const dep = depuracionOf([
    depInspector(1, { nombre_completo: 'José Ñandú 李雷' }),
    depInspector(2, { nombre_completo: huge }),
  ]);
  const { rows } = rowsFor({ depuracion: dep });
  assert.equal(rows.length, 2);
  assert.equal(rows.find((r) => r.key === 'ced:1000001').name, 'José Ñandú 李雷');
  assert.equal(visibleRowsFor(rows, { query: 'jose nandu' }).length, 1);
  assert.equal(rows.find((r) => r.key === 'ced:1000002').name.length, 50000);
});

// ── 11.3 ────────────────────────────────────────────────────────────────────

named('test_sticker_for_unseeded_cedula_still_creates_row', () => {
  const dep = depuracionOf([depInspector(1), depInspector(2)]);
  const stickers = [
    stickerFor('9999999', 'Sin Sembrar', '2026-09-10T15:00:00+00:00'),
    stickerFor('9999999', 'Sin Sembrar', '2026-09-11T15:00:00+00:00'),
  ];
  const surveys = [{ nombre_evaluador: 'Evaluador Suelto', fecha_inspeccion: '2026-09-12' }];
  const { rows, totals, unassigned } = rowsFor({ stickers, surveys, depuracion: dep });
  assert.equal(rows.length, 4, '2 seeded + the unseeded cédula + the name-only survey');
  // The resolver only trusts an ELIGIBLE (seeded) cédula as a merge key, so an
  // unseeded one falls back to its name key — but the row exists and keeps the cédula.
  const extra = rows.find((r) => r.key === `nom:${normalizeName('Sin Sembrar')}`);
  assert.ok(extra, 'the unseeded cédula has a row');
  assert.equal(extra.stickersTotal, 2);
  assert.equal(extra.name, 'Sin Sembrar', 'the row is identifiable, not blank');
  assert.equal(extra.cedula, '9999999');
  assert.ok(rows.some((r) => r.key === `nom:${normalizeName('Evaluador Suelto')}` && r.surveyTotal === 1));
  assert.equal(totals.stickers, 2);
  assert.deepEqual(unassigned, { stickers: 0, surveys: 0 });
  // Judgment-day C2 (deliberate change, was `4`): this used to pin the WRONG
  // semantics (padron = rows.length, i.e. 2 seeded + 2 orphan nom: rows). The
  // padrón is the number of seeded profiles and never counts the orphan rows
  // that records resolved to nobody create.
  assert.equal(totals.padron, 2);
  assert.equal(totals.professionals, 2, 'only the two rows WITH activity count as active');
});

// ── 11.4 (characterization) ─────────────────────────────────────────────────

named('test_legacy_path_unchanged_when_depuracion_absent_or_inactive', () => {
  const stickers = [
    stickerFor('1000001', 'Profesional 1'),
    stickerFor('1000001', 'Profesional 1', '2026-09-11T15:00:00+00:00'),
    stickerFor('', 'Solo Nombre'),
  ];
  const surveys = [{ nombre_evaluador: 'Solo Nombre', fecha_inspeccion: '2026-09-12' }];
  const baseline = buildProfessionalRows({
    stickers, surveys, identity: buildIdentityIndex({ stickers, surveys }), today: P11_TODAY,
  });
  const seededButInactive = depuracionOf(
    [depInspector(7), depInspector(8), depInspector(9)],
    { activa: false, motivo: 'stickers_degradados' },
  );
  for (const depuracion of [null, undefined, seededButInactive, { activa: false, inspectores: [depInspector(7)] }]) {
    const out = rowsFor({ stickers, surveys, depuracion });
    assert.deepEqual(out.rows, baseline.rows, 'rows byte-identical to the no-depuracion call');
    assert.deepEqual(out.totals, baseline.totals);
    assert.deepEqual(Object.keys(out.totals), ['professionals', 'stickers', 'surveys', 'avgPerProfessional', 'unassigned', 'stickersWithoutDate'], 'no `padron` key on the legacy path');
    assert.equal(out.rows.length, 2, 'rows only from stickers/surveys, the inactive inspectores are ignored');
  }
});

// ── 11.5 / 11.6 / 11.7 (KPIs) ───────────────────────────────────────────────

named('test_kpis_use_rows_with_activity_as_denominator', () => {
  const { stickers, depuracion } = seededUniverse(373, 116);
  const result = rowsFor({ stickers, depuracion });
  assert.equal(result.rows.length, 373);
  assert.equal(result.totals.padron, 373);
  assert.equal(result.totals.professionals, 116);
  assert.equal(result.totals.stickers, 464);
  assert.equal(result.totals.avgPerProfessional, 4, '464 stickers / 116 active (NOT / 373 = 1.24)');
  const t = kpiTotals(result, { stickersLoaded: true });
  assert.equal(t.professionals, 116);
  assert.equal(t.padron, 373, 'the seeded total is surfaced separately');
  assert.equal(t.avgStickersPerDayPerProfessional, 2, 'each active professional: 4 stickers / 2 days');
  const html = kpisHtml(result, true);
  assert.match(html, /profesionales con actividad[\s\S]*?>116</);
  assert.match(html, /padr.n[\s\S]*?>373</i, 'the padrón total has its own, distinctly labelled tile');
  assert.equal((html.match(/kpi-tile/g) || []).length, 8, 'inspectores activos and stickers/día por inspector activo join the padrón tile in the seeded row');
  // Triangulation: a different split.
  const other = rowsFor({ ...seededUniverse(40, 3) });
  assert.equal(kpiTotals(other).professionals, 3);
  assert.equal(kpiTotals(other).padron, 40);
});

named('test_kpis_legacy_still_five_tiles_and_no_padron', () => {
  const stickers = [stickerFor('1000001', 'Profesional 1')];
  const result = rowsFor({ stickers });
  const t = kpiTotals(result);
  assert.equal(Object.prototype.hasOwnProperty.call(t, 'padron'), false);
  assert.equal(t.professionals, 1);
  assert.equal((kpisHtml(result, true).match(/kpi-tile/g) || []).length, 5);
});

named('test_kpis_empty_range_no_nan', () => {
  const { inspectores, stickers } = seededUniverse(373, 116);
  const dep = depuracionOf(inspectores);
  const result = rowsFor({
    stickers, depuracion: dep, from: '2027-01-01', to: '2027-01-31',
  });
  assert.equal(result.rows.length, 373, 'everybody still listed');
  assert.equal(result.totals.professionals, 0);
  assert.equal(result.totals.avgPerProfessional, 0);
  const t = kpiTotals(result, { stickersLoaded: true });
  assert.equal(t.professionals, 0);
  assert.equal(t.padron, 373);
  assert.equal(t.stickers, 0);
  assert.equal(t.avgStickersPerDayPerProfessional, 0);
  assert.equal(t.barriosActivos, 0);
  const html = kpisHtml(result, true);
  assert.ok(!/NaN|Infinity|undefined|null/.test(html), 'no NaN/Infinity/undefined reaches the DOM');
  // Inverted range (to < from) and an entirely empty seeded set behave the same.
  const inverted = rowsFor({
    stickers, depuracion: dep, from: '2026-09-30', to: '2026-09-01',
  });
  assert.equal(kpiTotals(inverted).professionals, 0);
  assert.ok(!/NaN|Infinity/.test(kpisHtml(inverted, true)));
  const empty = rowsFor({ depuracion: depuracionOf([]) });
  assert.ok(!/NaN|Infinity/.test(kpisHtml(empty, true)));
  // A seeded padrón with stickers not loaded yet keeps masking behind DASH.
  assert.equal(kpiTotals(result, { stickersLoaded: false }).padron, DASH);
});

named('test_kpis_barrios_ignore_zero_activity_rows', () => {
  const result = {
    rows: [
      { stickersTotal: 1, total: 1, stickerActiveDays: 1, barriosActivos: ['San Antonio'] },
      { stickersTotal: 0, total: 0, stickerActiveDays: 0, barriosActivos: ['Recent Barrio Outside Range'] },
    ],
    totals: { professionals: 1, padron: 2, stickers: 1, surveys: 0 },
  };
  const t = kpiTotals(result);
  assert.equal(t.barriosActivos, 1, 'a zero-activity row (barrios are last-7-days, not range-bound) never feeds the KPI');
  assert.equal(t.professionals, 1);
});

// ── 11.8 (characterization) ─────────────────────────────────────────────────

named('test_timeline_and_charts_ignore_zero_activity_rows', () => {
  const { stickers, inspectores } = seededUniverse(373, 5);
  const seeded = buildIdentityIndex({ stickers, surveys: [], depuracion: depuracionOf(inspectores) });
  const plain = buildIdentityIndex({ stickers, surveys: [] });
  const withSeed = buildTimeline({ stickers, surveys: [], identity: seeded });
  const without = buildTimeline({ stickers, surveys: [], identity: plain });
  assert.deepEqual(withSeed, without, 'seeding never changes the timeline');
  assert.deepEqual(withSeed.labels, ['2026-09-10', '2026-09-11']);
  assert.deepEqual(withSeed.stickers, [10, 10]);
  assert.equal(timelineChartConfig(withSeed).data.datasets.length, 4, 'no series per seeded row');
  // Selecting a zero-activity professional yields an empty (not zero-height) timeline.
  const zeroOnly = buildTimeline({
    stickers, surveys: [], identity: seeded, professionalKey: 'ced:1000373',
  });
  assert.deepEqual(zeroOnly.labels, []);
});

// ── 11.9 / 11.10 / 11.11 (estado filter + column) ───────────────────────────

named('test_estado_filter_narrows_table_and_composes_with_search', () => {
  const dep = depuracionOf([
    depInspector(1, { nombre_completo: 'Garcia Uno', estado_sugerido: 'activo' }),
    depInspector(2, { nombre_completo: 'Garcia Dos', estado_sugerido: 'candidato_desactivacion' }),
    depInspector(3, { nombre_completo: 'Garcia Tres', estado_sugerido: 'revisar' }),
    depInspector(4, { nombre_completo: 'Perez Cuatro', estado_sugerido: 'activo' }),
    depInspector(5, { nombre_completo: 'Lopez Cinco', estado_sugerido: 'no_persona' }),
  ]);
  const stickers = [stickerFor('1000001', 'Garcia Uno')];
  const { rows } = rowsFor({ stickers, depuracion: dep });
  assert.equal(visibleRowsFor(rows, { query: 'garcia' }).length, 3);
  assert.deepEqual(
    visibleRowsFor(rows, { query: 'garcia', estado: 'activo' }).map((r) => r.name),
    ['Garcia Uno'],
    'search matches 3, only 1 is activo',
  );
  assert.equal(visibleRowsFor(rows, { estado: 'activo' }).length, 2);
  assert.equal(visibleRowsFor(rows, { estado: 'candidato_desactivacion' }).length, 1);
  assert.equal(visibleRowsFor(rows, { estado: 'no_persona' }).length, 1);
  assert.equal(visibleRowsFor(rows, { estado: 'all' }).length, 5);
  assert.equal(visibleRowsFor(rows, {}).length, 5, 'default is "all"');
  assert.equal(visibleRowsFor(rows, { estado: '' }).length, 5, 'an empty value means "all"');
  // Composes with the professional select too (AND).
  assert.equal(visibleRowsFor(rows, { estado: 'activo', professionalKey: 'ced:1000004' }).length, 1);
  assert.equal(visibleRowsFor(rows, { estado: 'activo', professionalKey: 'ced:1000002' }).length, 0);
  // The date range is upstream of the filter: it never changes with the estado.
  const narrowed = rowsFor({
    stickers, depuracion: dep, from: '2026-09-10', to: '2026-09-10',
  });
  assert.equal(narrowed.rows.length, 5);
  assert.equal(visibleRowsFor(narrowed.rows, { estado: 'activo', query: 'garcia' }).length, 1);
  assert.equal(hasActiveSegFilters({ estado: 'activo' }), true);
  assert.equal(hasActiveSegFilters({ estado: 'all' }), false);
  assert.equal(hasActiveSegFilters({}), false);
});

named('test_estado_column_only_with_depuracion_and_options_default_all', () => {
  assert.deepEqual(columnsFor('totales'), COLUMNS_TOTALES, 'legacy columns untouched');
  const withEstado = columnsFor('totales', { withEstado: true }).map((c) => c.key);
  assert.equal(withEstado.length, COLUMNS_TOTALES.length + 1);
  assert.ok(withEstado.includes('estadoSugerido'));
  assert.equal(withEstado[withEstado.indexOf('np') + 1], 'estadoSugerido', 'right after "Clase (P)"');
  assert.deepEqual(columnsFor('temporales', { withEstado: true }), COLUMNS_TEMPORALES, 'temporales sub-tab unchanged');
  const values = SEG.ESTADO_FILTER_OPTIONS.map((o) => o.value);
  assert.equal(values[0], 'all');
  for (const v of ['activo', 'revisar', 'candidato_desactivacion', 'no_persona']) assert.ok(values.includes(v), v);
  assert.equal(cellHtml({ estadoSugerido: 'candidato_desactivacion' }, 'estadoSugerido', true), 'candidato_desactivacion');
  assert.equal(cellHtml({ estadoSugerido: '' }, 'estadoSugerido', true), 'Sin dato');
  assert.equal(cellHtml({ estadoSugerido: 'activo' }, 'estadoSugerido', false), DASH);
});

named('test_estado_filter_unknown_value_visible_under_all', () => {
  const dep = depuracionOf([
    depInspector(1, { estado_sugerido: 'algo_inesperado' }),
    depInspector(2, { estado_sugerido: '<b>x</b>' }),
    depInspector(3, { estado_sugerido: 'activo' }),
  ]);
  const { rows } = rowsFor({ depuracion: dep });
  assert.equal(visibleRowsFor(rows, { estado: 'all' }).length, 3, 'unknown strings stay visible under "all"');
  assert.equal(visibleRowsFor(rows, {}).length, 3);
  assert.equal(visibleRowsFor(rows, { estado: 'activo' }).length, 1, 'and are narrowed away by a concrete estado');
  assert.equal(visibleRowsFor(rows, { estado: 'algo_inesperado' }).length, 1, 'an unexpected value can still be matched exactly');
  assert.equal(cellHtml(rows.find((r) => r.name === 'Profesional 2'), 'estadoSugerido', true), '&lt;b&gt;x&lt;/b&gt;', 'the unknown estado is escaped in the cell');
});

named('test_sort_is_stable_with_seeded_zero_activity_rows', () => {
  const { stickers, depuracion } = seededUniverse(60, 6);
  const { rows } = rowsFor({ stickers, depuracion });
  const a = sortRows(rows, 'stickersFase1', 'desc').map((r) => r.key);
  const shuffled = [...rows].reverse();
  const b = sortRows(shuffled, 'stickersFase1', 'desc').map((r) => r.key);
  assert.deepEqual(a, b, 'input order never changes the result: ties break by key');
  assert.equal(new Set(a).size, 60, 'sorting never loses or repeats a row');
  assert.ok(a.slice(0, 6).every((k) => Number(k.slice(4)) <= 1000006), 'the 6 active rows first');
  const asc = sortRows(rows, 'estadoSugerido', 'asc').map((r) => r.key);
  assert.deepEqual(asc, [...asc].sort(), 'all-equal estado: pure key order');
});

// ── 11.12 / 11.13 / 11.14 (mass PDF export scope) ───────────────────────────

named('test_mass_pdf_export_defaults_to_rows_with_activity', () => {
  const { stickers, depuracion } = seededUniverse(373, 116);
  const { rows } = rowsFor({ stickers, depuracion });
  assert.equal(SEG.rowHasActivity(rows.find((r) => r.key === 'ced:1000001')), true);
  assert.equal(SEG.rowHasActivity(rows.find((r) => r.key === 'ced:1000373')), false);
  const scope = SEG.massExportScope(rows);
  assert.equal(scope.status, 'ok');
  assert.equal(scope.rows.length, 116, '116 reports, not 373');
  assert.equal(scope.visible, 373);
  assert.equal(scope.withActivity, 116);
  assert.ok(scope.rows.every((r) => r.total > 0));
  const text = SEG.massExportScopeText(scope);
  assert.match(text, /116/);
  assert.match(text, /373/);
  assert.match(text, /con actividad/, 'the scope is stated in the UI before running');
  // Filters narrow the base first: only the visible rows are considered.
  const narrowed = visibleRowsFor(rows, { query: 'profesional 1' });
  assert.ok(SEG.massExportScope(narrowed).rows.length <= narrowed.length);
  // All visible rows active: the statement says so instead of "X de X".
  const allActive = SEG.massExportScope(rows.filter((r) => r.total > 0));
  assert.equal(allActive.rows.length, 116);
  assert.doesNotMatch(SEG.massExportScopeText(allActive), /116 de 116/);
});

named('test_mass_pdf_export_over_cap_refuses_instead_of_truncating', () => {
  const mk = (n) => Array.from({ length: n }, (_, i) => ({ key: `ced:${i}`, total: 1 }));
  const at = SEG.massExportScope(mk(200));
  assert.equal(at.status, 'ok', '200 is allowed');
  assert.equal(at.rows.length, 200);
  const over = SEG.massExportScope(mk(201));
  assert.equal(over.status, 'refused', '201 is refused');
  assert.deepEqual(over.rows, [], 'no partial batch');
  assert.match(over.message, /200/, 'names the cap');
  assert.match(over.message, /201/, 'names the count');
  // The persistent notice is now a calm hint (see test_mass_export_persistent_notice_*); only the
  // click-time refusal keeps `message`.
  assert.match(SEG.massExportScopeText(over), /^Exportación masiva: hay 201 profesionales con actividad y el máximo por exportación es 200\./);
  assert.equal(SEG.MASS_EXPORT_CAP, 200);
  // The cap applies to rows WITH activity: 500 visible / 150 active is fine.
  const mixed = [...mk(150), ...Array.from({ length: 350 }, (_, i) => ({ key: `z:${i}`, total: 0 }))];
  const scope = SEG.massExportScope(mixed);
  assert.equal(scope.status, 'ok');
  assert.equal(scope.rows.length, 150);
  assert.equal(SEG.massExportScope(mk(5), { cap: 4 }).status, 'refused', 'a custom cap is honoured');
});

named('test_mass_pdf_export_scope_empty_and_malformed', () => {
  for (const input of [[], null, undefined, 'x', [null, undefined]]) {
    const scope = SEG.massExportScope(input);
    assert.equal(scope.status, 'empty');
    assert.deepEqual(scope.rows, []);
    assert.equal(scope.visible, Array.isArray(input) ? input.length : 0);
  }
  const zeroOnly = SEG.massExportScope([{ key: 'a', total: 0 }, { key: 'b' }, { key: 'c', total: Number.NaN }]);
  assert.equal(zeroOnly.status, 'empty', 'visible rows exist but none has activity');
  assert.equal(zeroOnly.visible, 3);
  assert.match(SEG.massExportScopeText(zeroOnly), /ning.n profesional/i);
});

// ── 11.15 / 11.15b / 11.16 (revisión manual) ────────────────────────────────

const REV_IDENTITY = buildIdentityIndex({
  depuracion: depuracionOf(
    [
      depInspector(1, { nombre_completo: 'Ana Uno' }),
      depInspector(2, { nombre_completo: 'Beto Dos' }),
      depInspector(3, { nombre_completo: 'Carla Tres' }),
    ],
    {
      grupo_externos: {
        n_colapsados: 1,
        detalle: [{
          nombre_completo: 'Extern Nueve', identificacion: '9999999', motivo: 'cedula_sospechosa', ultimo_sticker: null,
        }],
      },
    },
  ),
});
const REV_ITEMS = [
  { motivo: 'codigo_remap_candidato', codigo: '041', nombre_vercel: 'ana uno', identidad_key_candidato: '1000001', score: 92.5 },
  { motivo: 'remap_sin_duenio', codigo: '042', identidad_key: '1000002' },
  { motivo: 'remap_conflicto', codigo: '043', identidad_key: '1000003' },
  {
    motivo: 'remap_owner_ambiguo', codigo: '044', cedula_key: '1000001', identidad_keys: ['1000001', '1000002'], identidad_keys_titulares: ['1000003'],
  },
  { motivo: 'remap_mismos_titulares', codigo: '045', identidad_key: '1000002', identidad_key_conservado: '1000001' },
  { motivo: 'codigo_reemplazado', identidad_key: '1000003', codigo_anterior: '052', codigo_nuevo: '046' },
  { motivo: 'codigo_duplicado_local', codigo: '047', identidad_keys: ['1000001', '1000003'] },
  {
    motivo: 'codigo_perdido_unificacion', codigo: '048', identidad_key: '1000001', identidad_key_absorbido: '2000002',
  },
  {
    motivo: 'fase2_cedula_colision', cedula_key: '1000004', identidad_key: '1000002', identidad_key_existente: '1000001', nombre_completo: 'Beto Dos',
  },
  {
    motivo: 'cedula_duplicada_main', cedula_key: '1000005', nombre_completo: 'Doble Cinco', nombre_completo_duplicado: 'Doble Cinco B', id: 'm1', id_duplicado: 'm2', mismo_nombre: false,
  },
  { motivo: 'main_sin_cedula', cedula_key: '', nombre_completo: 'Sin Cedula Seis', id: 'm6' },
  { motivo: 'codigo_vercel_duplicado', codigo: '049', identidad_keys_titulares: ['1000001', '9999999'] },
];
const REV_EXPECTED = [
  [/Ana Uno/, /1000001/, /92\.5/, /041/],
  [/Beto Dos/, /1000002/, /042/],
  [/Carla Tres/, /1000003/, /043/],
  [/Ana Uno/, /Beto Dos/, /Carla Tres/, /044/, /1000001/],
  [/Beto Dos/, /Ana Uno/, /1000002/, /1000001/, /045/, /conserva/i],
  [/Carla Tres/, /1000003/, /052/, /046/, /→/],
  [/Ana Uno/, /Carla Tres/, /047/],
  [/Ana Uno/, /1000001/, /2000002/, /048/, /absorbid/i],
  [/Beto Dos/, /Ana Uno/, /1000004/, /1000002/],
  [/Doble Cinco B/, /1000005/, /Doble Cinco/],
  [/Sin Cedula Seis/],
  [/Ana Uno/, /Extern Nueve/, /9999999/, /049/],
];

named('test_revision_manual_renders_new_motivos_with_name_and_cedula', () => {
  assert.equal(REV_ITEMS.length, REV_EXPECTED.length);
  REV_ITEMS.forEach((item, i) => {
    const html = revisionManualHtml([item], { identity: REV_IDENTITY, isAdmin: true });
    assert.equal((html.match(/<li/g) || []).length, 1, item.motivo);
    assert.ok(html.includes(item.motivo), `${item.motivo}: the raw motivo stays visible`);
    for (const pattern of REV_EXPECTED[i]) assert.match(html, pattern, `${item.motivo}: ${pattern}`);
  });
  // One combined list renders every entry in order.
  const all = revisionManualHtml(REV_ITEMS, { identity: REV_IDENTITY, isAdmin: true });
  assert.equal((all.match(/<li/g) || []).length, REV_ITEMS.length);
});

named('test_revision_manual_unknown_motivo_shows_raw_string', () => {
  const html = revisionManualHtml(
    [{ motivo: 'motivo_del_futuro', codigo: '077', identidad_key: '1000001' }, { motivo: '' }, {}, null, 'basura'],
    { identity: REV_IDENTITY, isAdmin: true },
  );
  assert.match(html, /motivo_del_futuro/, 'the raw string is shown, the entry is not dropped');
  assert.match(html, /Ana Uno/, 'generic fields of an unknown motivo still resolve');
  assert.match(html, /sin motivo/);
  assert.equal((html.match(/<li/g) || []).length, 4, 'null entries are skipped, every real one is listed');
});

named('test_revision_manual_shows_ocurrencias_only_from_two', () => {
  const base = { motivo: 'cedula_duplicada_main', cedula_key: '1000005', nombre_completo: 'Doble Cinco' };
  const opts = { identity: REV_IDENTITY, isAdmin: true };
  const plain = revisionManualHtml([base], opts);
  assert.doesNotMatch(plain, /×/);
  assert.match(revisionManualHtml([{ ...base, n_ocurrencias: 3 }], opts), /×3/);
  assert.match(revisionManualHtml([{ ...base, n_ocurrencias: 2 }], opts), /×2/);
  for (const n of [1, 0, -4, null, undefined, '3', Number.NaN, Infinity]) {
    assert.equal(revisionManualHtml([{ ...base, n_ocurrencias: n }], opts), plain, `n_ocurrencias=${String(n)} renders exactly as before`);
  }
  assert.match(revisionManualHtml([{ ...base, n_ocurrencias: 3 }], { isAdmin: false }), /×3/, 'the counter is not PII');
});

named('test_revision_manual_without_options_keeps_the_previous_render', () => {
  // characterization of the pre-Phase-11 contract (Task 4.6/4.7).
  const html = revisionManualHtml([{ motivo: 'codigo_vercel_duplicado', codigo: '097' }]);
  assert.equal(html, '<ul class="seg-revision-manual-list"><li>codigo_vercel_duplicado · código 097</li></ul>');
});

named('test_revision_manual_identity_detail_admin_only', () => {
  const html = revisionManualHtml(REV_ITEMS, { identity: REV_IDENTITY, isAdmin: false });
  assert.equal((html.match(/<li/g) || []).length, REV_ITEMS.length, 'the section is still rendered');
  for (const item of REV_ITEMS) assert.ok(html.includes(item.motivo), item.motivo);
  for (const codigo of ['041', '042', '043', '044', '045', '047', '048', '049']) assert.ok(html.includes(codigo), `código ${codigo} is not PII`);
  for (const pii of [/Ana Uno/, /Beto Dos/, /Carla Tres/, /Extern Nueve/, /Doble Cinco/, /Sin Cedula Seis/, /100000\d/, /999999/, /2000002/]) {
    assert.doesNotMatch(html, pii, `non-admin payload must not carry ${pii}`);
  }
  // Empty state is unchanged for a non-admin.
  assert.match(revisionManualHtml([], { isAdmin: false }), /[Ss]in pendientes/);
});

named('test_revision_manual_escapes_every_new_render_path', () => {
  const evil = '<img src=x onerror=alert(1)>';
  const identity = buildIdentityIndex({
    depuracion: depuracionOf(
      [depInspector(1, { nombre_completo: evil, identificacion: evil })],
      {
        grupo_externos: {
          n_colapsados: 1, detalle: [{ nombre_completo: '<svg onload=1>', identificacion: '9999999', motivo: 'x', ultimo_sticker: null }],
        },
      },
    ),
  });
  const html = revisionManualHtml([
    {
      motivo: evil,
      codigo: '"><script>alert(1)</script>',
      identidad_key: '1000001',
      identidad_keys: ['1000001', evil],
      identidad_keys_titulares: ['9999999'],
      identidad_key_conservado: `'${evil}`,
      codigo_anterior: '<b>a</b>',
      codigo_nuevo: '</li><li>injected',
      cedula_key: '<u>1</u>',
      nombre_completo: '<iframe src=javascript:1>',
      nombre_completo_duplicado: '<a href="javascript:1">x</a>',
      n_ocurrencias: 4,
    },
  ], { identity, isAdmin: true });
  for (const raw of ['<img', '<script', '<svg', '<b>', '<u>', '<iframe', '<a href', '</li><li>injected']) {
    assert.ok(!html.includes(raw), `raw ${raw} must not survive`);
  }
  assert.match(html, /&lt;img/);
  assert.equal((html.match(/<li/g) || []).length, 1, 'the injected </li><li> did not add an entry');
});

named('test_revision_manual_huge_lists_and_names_stay_bounded', () => {
  const keys = Array.from({ length: 5000 }, (_, i) => String(2000000 + i));
  const started = performance.now();
  const html = revisionManualHtml(
    [{ motivo: 'codigo_duplicado_local', codigo: '1', identidad_keys: keys, nombre_completo: 'N'.repeat(200000) }],
    { identity: REV_IDENTITY, isAdmin: true },
  );
  const elapsed = performance.now() - started;
  assert.ok(elapsed < 2000, `rendered in ${elapsed.toFixed(0)} ms`);
  assert.match(html, /\+[\d.]+ m.s/, 'a huge key list is capped with a "+N más" tail');
  assert.ok(!html.includes('2004999'), 'the tail of the list is not rendered');
});

named('test_revision_manual_unresolvable_keys_and_missing_identity', () => {
  const item = { motivo: 'remap_conflicto', codigo: '043', identidad_key: '5550001' };
  const withIdentity = revisionManualHtml([item], { identity: REV_IDENTITY, isAdmin: true });
  assert.match(withIdentity, /5550001/, 'a key with no profile still shows its cédula, never a blank');
  for (const identity of [null, undefined, {}, { profiles: null }, { profiles: new Map(), grupoExternos: { detalle: null } }]) {
    const html = revisionManualHtml([item], { identity, isAdmin: true });
    assert.match(html, /5550001/);
  }
});

// ── 11.17 (banner) ──────────────────────────────────────────────────────────

named('test_degraded_banner_shown_with_motivo', () => {
  for (const motivo of ['stickers_degradados', 'sin_blob', 'calculo_fallido', 'referencia_timeout', 'motivo_raro']) {
    const identity = buildIdentityIndex({ depuracion: { activa: false, motivo, inspectores: [depInspector(1)] } });
    const text = depuracionBadgeHtml(identity, { loaded: true });
    assert.ok(text && text.includes(motivo), `banner names ${motivo}`);
    assert.doesNotMatch(text, /referencia generada/);
  }
  // A block with activa:false and NO motivo is still a server-sent degradation.
  for (const block of [{ activa: false }, { activa: false, motivo: '' }, { activa: false, motivo: null }]) {
    const noMotivo = buildIdentityIndex({ depuracion: block });
    assert.equal(noMotivo.depuracionAusente, false, 'a block was sent');
    assert.match(depuracionBadgeHtml(noMotivo, { loaded: true }), /sin_motivo/);
    assert.equal(SEG.depuracionBadgeIsDegraded(noMotivo), true);
  }
  // ...but never while the sticker fetch is still in flight or has failed.
  const degraded = buildIdentityIndex({ depuracion: { activa: false, motivo: 'sin_blob' } });
  assert.equal(depuracionBadgeHtml(degraded, { loaded: false }), null);
});

// Task 1 correction (PR 10, part 2): an ABSENT block is the NORMAL state
// (backend flag off, request did not opt in, viewer, old backend) and the
// frontend cannot tell those apart -- so it is SILENT: legacy render, no banner.
named('test_absent_depuracion_block_is_silent_no_banner', () => {
  for (const payload of [
    {}, { depuracion: null }, { depuracion: undefined }, { stickers: [], surveys: [] },
    { depuracion: 'x' }, { depuracion: 7 }, { depuracion: [] }, { depuracion: true },
  ]) {
    const identity = buildIdentityIndex(payload);
    assert.equal(identity.depuracionAusente, true, `absent: ${JSON.stringify(payload)}`);
    assert.equal(identity.depuracionActiva, false);
    for (const loaded of [true, false]) {
      assert.equal(depuracionBadgeHtml(identity, { loaded }), null, `no badge text (loaded=${loaded}) for ${JSON.stringify(payload)}`);
    }
    assert.equal(SEG.depuracionBadgeIsDegraded(identity), false, 'an absent block is not "degraded"');
  }
  // The absent path must not even mention the synthetic motivo the part-1 code invented.
  assert.doesNotMatch(readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8'), /sin_depuracion/);
});

named('test_active_shows_referencia_generada_en_no_banner', () => {
  const identity = buildIdentityIndex({ depuracion: depuracionOf([depInspector(1)], { referencia_generada_en: '2026-09-19' }) });
  assert.equal(identity.depuracionAusente, false);
  const text = depuracionBadgeHtml(identity, { loaded: true });
  assert.match(text, /2026-09-19/);
  assert.doesNotMatch(text, /sin depurar|no disponible|sin_depuracion/i);
  assert.equal(SEG.depuracionBadgeIsDegraded(identity), false);
  assert.equal(SEG.depuracionBadgeIsDegraded(buildIdentityIndex({ depuracion: null })), false, 'an absent block is silent, not degraded');
  assert.equal(SEG.depuracionBadgeIsDegraded(buildIdentityIndex({ depuracion: { activa: false, motivo: 'sin_blob' } })), true);
  // Active but without a date: still no banner, the date is reported unknown.
  const noDate = buildIdentityIndex({ depuracion: depuracionOf([], { referencia_generada_en: '' }) });
  assert.match(depuracionBadgeHtml(noDate, { loaded: true }), /fecha desconocida/);
});

// ── table rendering (tableBodyHtml) and the mobile hooks ────────────────────

named('test_table_body_escapes_seeded_rows_and_keeps_mobile_hooks', () => {
  const evil = '<img src=x onerror=alert(1)>';
  const dep = depuracionOf([
    depInspector(1, {
      nombre_completo: evil, identificacion: `"><script>1</script>`, np: '<b>P9</b>', estado_sugerido: '<i>x</i>', tarjeta_profesional: '<u>tp</u>',
    }),
    depInspector(2),
  ]);
  const { rows } = rowsFor({ depuracion: dep });
  const columns = columnsFor('totales', { withEstado: true });
  const html = SEG.tableBodyHtml(sortRows(rows, 'name', 'asc'), true, false, columns, false);
  for (const raw of ['<img', '<script', '<b>P9', '<i>x', '<u>tp']) assert.ok(!html.includes(raw), `raw ${raw} must not survive in the table`);
  assert.equal((html.match(/<tr>/g) || []).length, 2);
  assert.equal((html.match(/seg-report-btn/g) || []).length, 2, 'the per-row report button hook is intact');
  assert.match(html, /data-seg-report="ced:1000002"/);
  assert.match(SEG.tableBodyHtml([], true, false, columns, false), new RegExp(`colspan="${columns.length + 1}"[^>]*eval-empty`));
  // Source-level guards for the branch-05 mobile-overflow work.
  const js = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
  assert.match(js, /<div class="table-scroll">\s*<table class="tipologia-table" id="seg-table">/, 'the table stays inside .table-scroll');
  assert.match(css, /#view-seguimiento\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0[^}]*\}/, '#view-seguimiento keeps its definite width');
  assert.match(css, /\.seg-sort-btn\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(css, /\.seg-grupo-externos-row td\s*\{/);
  // The new controls live in the shared, wrapping toolbar and reuse its field class.
  assert.match(js, /card-toolbar asignacion-filters">[\s\S]*?id="seg-estado"/);
  assert.match(js, /<label class="sticker-field asignacion-inline-field"[^>]*>\s*<span>Estado sugerido<\/span>/);
});

// The filter toolbar must stay inside the viewport on a phone. The layout itself
// is verified in Chromium (see the render measurements); these source-level pins
// guard the CSS contract and the markup hooks that contract depends on.
named('test_filter_toolbar_is_bounded_on_phones_and_keeps_its_hooks', () => {
  const js = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
  // Markup hooks the CSS relies on: same toolbar/field classes and control ids.
  assert.match(js, /<div class="card-toolbar asignacion-filters">/);
  for (const id of ['seg-from', 'seg-to', 'seg-chart-professional', 'seg-estado', 'seg-download', 'seg-report-selected', 'seg-report-mass']) {
    assert.ok(js.includes(`id="${id}"`), `${id} keeps its id`);
  }
  assert.equal((js.match(/<label class="sticker-field asignacion-inline-field"/g) || []).length, 4, 'the four filter fields share one class');
  // Every viewport: a select never outgrows its field and truncates its closed label.
  assert.match(css, /\.seg-section \.asignacion-inline-field select\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*text-overflow:\s*ellipsis/);
  // Phones: the toolbar is a wrapping ROW (never the shared column that made it multi-line
  // and as wide as the widest select), fields stack label-over-control, buttons wrap.
  const phone = css.match(/@media \(max-width: 640px\) \{\n\s*\.seg-section \.asignacion-filters[\s\S]*?\n\}\n/);
  assert.ok(phone, 'the seguimiento phone toolbar block exists');
  assert.match(phone[0], /\.seg-section \.asignacion-filters\s*\{[^}]*flex-direction:\s*row/);
  assert.match(phone[0], /\.seg-section \.asignacion-inline-field\s*\{[^}]*flex:\s*1 1 100%[^}]*flex-direction:\s*column !important/);
  assert.match(phone[0], /input,\s*\.seg-section \.asignacion-inline-field select\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0/);
  assert.match(phone[0], /\.sticker-action\s*\{[^}]*flex:\s*1 1 auto/);
  assert.doesNotMatch(phone[0], /flex-wrap:\s*nowrap/);
});

// ══ Judgment-day fixes (fresh adversarial review of PR 10 parts 1+2) ═════════

// C2: the padrón tile is the DEPURADO total ("independent of the range").
named('test_padron_is_the_seeded_profile_count_in_every_range', () => {
  const dep = depuracionOf([depInspector(1), depInspector(2)]);
  const stickers = [
    stickerFor('1000001', 'Profesional 1', '2026-09-10T15:00:00+00:00'),
    stickerFor('', 'Ghost A', '2026-09-10T15:00:00+00:00'), // blank cédula, nobody seeded: orphan nom: row
    stickerFor('', 'Ghost B', '2026-09-15T15:00:00+00:00'),
  ];
  const ranges = [
    [{}, 3], // full range: Profesional 1 + both orphans are active
    [{ to: '2026-09-12' }, 2], // Ghost B drops out
    [{ from: '2026-09-14' }, 1], // only Ghost B
    [{ from: '2027-01-01', to: '2027-01-31' }, 0], // nobody active
    [{ from: '2026-09-30', to: '2026-09-01' }, 0], // inverted range
  ];
  for (const [range, activos] of ranges) {
    const result = rowsFor({ stickers, depuracion: dep, ...range });
    assert.equal(result.totals.padron, 2, `padron is 2 for ${JSON.stringify(range)}`);
    assert.equal(result.totals.professionals, activos, `activos varies with the range ${JSON.stringify(range)}`);
    assert.equal(kpiTotals(result, { stickersLoaded: true }).padron, 2);
  }
  const html = kpisHtml(rowsFor({ stickers, depuracion: dep }), true);
  assert.match(html, /padr.n[\s\S]*?>2</i, 'the tile shows 2, not 4');
  assert.doesNotMatch(html, /padr.n[\s\S]*?>4</i);
  // A duplicated / cédula-less inspector entry never inflates it.
  const dup = depuracionOf([depInspector(1), depInspector(1, { nombre_completo: 'Duplicada' }), { nombre_completo: 'Sin cedula' }, null]);
  assert.equal(rowsFor({ stickers, depuracion: dup }).totals.padron, 1);
  // Nothing seeded (active block, empty list) -> 0, never the row count.
  assert.equal(rowsFor({ stickers, depuracion: depuracionOf([]) }).totals.padron, 0);
});

// ── KPI "inspectores activos" (owner request 2026-09-19) ────────────────────
// The depuración's own ACTIVE classification (estado_sugerido === 'activo') is a
// padrón-level figure: range-, search- and estado-filter-independent, present
// only when the table is seeded; the legacy 5-tile row stays byte-identical.

const LEGACY_KPI_HTML = '\n    <div class="kpi-tile is-neutral">\n      <span class="kpi-label kpi-label-lower">profesionales activos</span>\n      <span class="kpi-value">1</span>\n    </div>\n    <div class="kpi-tile is-neutral">\n      <span class="kpi-label kpi-label-lower">stickers (F1+F2)</span>\n      <span class="kpi-value">1</span>\n    </div>\n    <div class="kpi-tile is-neutral">\n      <span class="kpi-label kpi-label-lower">evaluaciones survey</span>\n      <span class="kpi-value">0</span>\n    </div>\n    <div class="kpi-tile is-neutral" title="Stickers por día con actividad de stickers, promedio entre profesionales (excluye a quien no tiene ningún día con stickers).">\n      <span class="kpi-label kpi-label-lower">stickers/día por profesional</span>\n      <span class="kpi-value">1</span>\n    </div>\n    <div class="kpi-tile is-neutral">\n      <span class="kpi-label kpi-label-lower">barrios activos (7 d)</span>\n      <span class="kpi-value">0</span>\n    </div>';

// Value text of the tile whose label is exactly `label`, or null when absent.
function kpiTileValue(html, label) {
  const m = html.match(new RegExp(`<span class="kpi-label kpi-label-lower">${label}</span>\\s*<span class="kpi-value">([^<]*)</span>`));
  return m ? m[1] : null;
}
// Title attribute of the tile whose label is exactly `label`, or null.
function kpiTileTitle(html, label) {
  const m = html.match(new RegExp(`<div class="kpi-tile is-neutral"(?: title="([^"]*)")?>\\s*<span class="kpi-label kpi-label-lower">${label}</span>`));
  return m ? (m[1] ?? '') : null;
}
// inspectors with the given estado counts, in order; `activity` = cédulas with a sticker.
function estadoInspectors(counts) {
  const out = [];
  let i = 0;
  for (const [estado, n] of Object.entries(counts)) {
    for (let k = 0; k < n; k += 1) { i += 1; out.push(depInspector(i, { estado_sugerido: estado })); }
  }
  return out;
}

named('test_kpi_inspectores_activos_counts_only_estado_activo', () => {
  const inspectores = estadoInspectors({
    activo: 3, candidato_desactivacion: 2, no_persona: 1, revisar: 1,
  }); // cédulas 1000001-3 activo, 4-5 candidato, 6 no_persona, 7 revisar
  const dep = depuracionOf(inspectores);
  // Activity on ONE activo (1000001) and ONE revisar (1000007): 2 rows with activity, != 3.
  const stickers = [
    stickerFor('1000001', 'Profesional 1', '2026-09-10T15:00:00+00:00'),
    stickerFor('1000007', 'Profesional 7', '2026-09-10T15:00:00+00:00'),
  ];
  const result = rowsFor({ stickers, depuracion: dep });
  assert.equal(result.totals.inspectoresActivos, 3);
  assert.equal(result.totals.padron, 7);
  const t = kpiTotals(result, { stickersLoaded: true });
  assert.equal(t.inspectoresActivos, 3);
  const html = kpisHtml(result, true);
  assert.equal(kpiTileValue(html, 'inspectores activos'), '3');
  assert.equal(kpiTileValue(html, 'profesionales con actividad'), '2', 'activity tile still counts only rows with activity');
  assert.equal((html.match(/kpi-tile/g) || []).length, 8);
  // Prominent: first depuración tile, i.e. right after the five legacy ones... and before padrón.
  assert.ok(html.indexOf('inspectores activos') < html.indexOf('profesionales en padrón'));
  // Range-independent: same value for a different (and an empty / inverted) range.
  for (const range of [{ from: '2026-09-11' }, { from: '2027-01-01', to: '2027-01-31' }, { from: '2026-09-30', to: '2026-09-01' }]) {
    const r = rowsFor({ stickers, depuracion: dep, ...range });
    assert.equal(r.totals.inspectoresActivos, 3, `range ${JSON.stringify(range)}`);
    assert.equal(kpiTileValue(kpisHtml(r, true), 'inspectores activos'), '3');
  }
  // Search / estado filter never touch it: the UI hands kpisHtml the full totals, and even
  // a narrowed row list cannot move a padrón-level figure.
  const narrowed = visibleRowsFor(result.rows, { query: 'Profesional 5', estado: 'candidato_desactivacion' });
  assert.equal(narrowed.length, 1);
  assert.equal(kpiTileValue(kpisHtml({ rows: narrowed, totals: result.totals }, true), 'inspectores activos'), '3');
  // Triangulation: a different split.
  const other = rowsFor({ depuracion: depuracionOf(estadoInspectors({ activo: 1, revisar: 4 })) });
  assert.equal(other.totals.inspectoresActivos, 1);
});

named('test_kpi_inspectores_activos_ignores_unknown_and_missing_estado', () => {
  const dep = depuracionOf([
    depInspector(1, { estado_sugerido: 'activo' }),
    depInspector(2, { estado_sugerido: 'algo_inesperado' }),
    depInspector(3, { estado_sugerido: undefined }),
    depInspector(4, { estado_sugerido: null }),
    depInspector(5, { estado_sugerido: 'Activo' }),
    depInspector(6, { estado_sugerido: 'activo ' }),
    depInspector(7, { estado_sugerido: 'inactivo' }),
    depInspector(8, { estado_sugerido: '' }),
  ]);
  const result = rowsFor({ depuracion: dep });
  assert.equal(result.totals.padron, 8);
  assert.equal(result.totals.inspectoresActivos, 1, 'only the exact string "activo" counts');
  // All-inactive padrón: a real 0, never NaN / dash / blank.
  const none = rowsFor({ depuracion: depuracionOf(estadoInspectors({ candidato_desactivacion: 3, no_persona: 1 })) });
  assert.equal(none.totals.inspectoresActivos, 0);
  const html = kpisHtml(none, true);
  assert.equal(kpiTileValue(html, 'inspectores activos'), '0');
  assert.ok(!/NaN|Infinity|undefined|null/.test(html));
  // Every profile is activo: equals the padrón.
  const all = rowsFor({ depuracion: depuracionOf(estadoInspectors({ activo: 4 })) });
  assert.equal(all.totals.inspectoresActivos, 4);
  assert.equal(all.totals.padron, 4);
});

named('test_kpi_inspectores_activos_absent_for_empty_seeded_padron', () => {
  const empty = rowsFor({
    stickers: [stickerFor('1000001', 'Profesional 1')],
    depuracion: depuracionOf([]),
  });
  assert.equal(empty.totals.padron, 0);
  let html;
  assert.doesNotThrow(() => { html = kpisHtml(empty, true); });
  assert.equal(kpiTileValue(html, 'inspectores activos'), null, 'nothing to classify: no tile');
  assert.ok(!/NaN|Infinity|undefined|null/.test(html));
  assert.equal(Object.prototype.hasOwnProperty.call(kpiTotals(empty), 'inspectoresActivos'), false);
  // A hand-built seeded result without the figure (older shape) renders without it, no crash.
  const handBuilt = { rows: [], totals: { professionals: 0, padron: 2, stickers: 0, surveys: 0 } };
  assert.doesNotThrow(() => kpisHtml(handBuilt, true));
  assert.equal(kpiTileValue(kpisHtml(handBuilt, true), 'inspectores activos'), null);
});

named('test_kpi_legacy_html_is_byte_identical_without_active_depuracion', () => {
  const stickers = [stickerFor('1000001', 'Profesional 1')];
  const degraded = depuracionOf([depInspector(1, { estado_sugerido: 'activo' })], { activa: false, motivo: 'stickers_degradados' });
  const inactiveWithList = { activa: false, inspectores: [depInspector(1, { estado_sugerido: 'activo' })] };
  for (const depuracion of [null, undefined, degraded, inactiveWithList]) {
    const result = rowsFor({ stickers, depuracion });
    assert.equal(Object.prototype.hasOwnProperty.call(result.totals, 'inspectoresActivos'), false);
    assert.equal(Object.prototype.hasOwnProperty.call(kpiTotals(result), 'inspectoresActivos'), false);
    const html = kpisHtml(result, true);
    assert.equal(html, LEGACY_KPI_HTML, 'full HTML equality against the pre-change render');
    assert.ok(html.includes('profesionales activos'), 'legacy label unchanged');
  }
});

named('test_kpi_activity_label_renamed_only_when_seeded', () => {
  const seeded = kpisHtml(rowsFor({ depuracion: depuracionOf(estadoInspectors({ activo: 2 })) }), true);
  assert.equal(kpiTileValue(seeded, 'profesionales activos'), null, 'ambiguous label gone when seeded');
  assert.equal(kpiTileValue(seeded, 'profesionales con actividad'), '0');
  assert.equal(
    kpiTileTitle(seeded, 'profesionales con actividad'),
    'Profesionales con actividad en el rango de fechas seleccionado.',
  );
  assert.match(kpiTileTitle(seeded, 'inspectores activos'), /^Inspectores que la depuración clasifica como activos \(con código vigente o sticker válido\), sobre el total del padrón \(2 de 2\)\. No depende del rango de fechas\.$/);
  // Other tiles unchanged when seeded.
  for (const label of ['stickers \\(F1\\+F2\\)', 'evaluaciones survey', 'stickers/día por profesional', 'barrios activos \\(7 d\\)', 'profesionales en padrón']) {
    assert.notEqual(kpiTileValue(seeded, label), null, `${label} still rendered`);
  }
  // Legacy keeps the original label.
  const legacy = kpisHtml(rowsFor({ stickers: [stickerFor('1000001', 'Profesional 1')] }), true);
  assert.notEqual(kpiTileValue(legacy, 'profesionales activos'), null);
  assert.equal(kpiTileValue(legacy, 'profesionales con actividad'), null);
});

named('test_kpi_inspectores_activos_masks_while_stickers_load_and_escapes_markup', () => {
  const dep = depuracionOf([
    depInspector(1, { nombre_completo: '<script>alert(1)</script>', estado_sugerido: 'activo' }),
    depInspector(2, { estado_sugerido: '"><img src=x onerror=1>' }),
  ]);
  const result = rowsFor({ depuracion: dep });
  const masked = kpisHtml(result, false);
  assert.equal(kpiTileValue(masked, 'inspectores activos'), DASH, 'masked like padrón while stickers are unresolved');
  const html = kpisHtml(result, true);
  assert.equal(kpiTileValue(html, 'inspectores activos'), '1');
  assert.ok(!/<script|<img|onerror/.test(html), 'no record text ever reaches the KPI markup');
  // Every title attribute stays a well-formed, quote-free string.
  for (const m of html.matchAll(/title="([^"]*)"/g)) assert.ok(!/[<>]/.test(m[1]));
  assert.equal((html.match(/title="/g) || []).length, (html.match(/title=/g) || []).length);
});

named('test_kpi_inspectores_activos_uses_es_co_number_format', () => {
  const dep = depuracionOf(estadoInspectors({ activo: 1234, revisar: 266 }));
  const result = rowsFor({ depuracion: dep });
  assert.equal(result.totals.inspectoresActivos, 1234);
  const html = kpisHtml(result, true);
  assert.equal(kpiTileValue(html, 'inspectores activos'), (1234).toLocaleString('es-CO'));
  assert.equal(kpiTileValue(html, 'inspectores activos'), '1.234');
  assert.match(kpiTileTitle(html, 'inspectores activos'), /\(1\.234 de 1\.500\)/);
});

named('test_kpi_inspectores_activos_is_the_first_tile_of_the_row', () => {
  // The owner asked to SEE the active inspectors: on a phone (two tiles per row) a sixth
  // position would bury it on the third row, so it must lead the row when seeded.
  const dep = depuracionOf(estadoInspectors({ activo: 2, no_persona: 1 }));
  const stickers = [stickerFor('1000001', 'Profesional 1', '2026-09-10T15:00:00+00:00')];
  const labelsOf = (html) => [...html.matchAll(/kpi-label[^>]*>([^<]*)</g)].map((m) => m[1]);
  const seeded = labelsOf(kpisHtml(rowsFor({ stickers, depuracion: dep }), true));
  assert.equal(seeded[0], 'inspectores activos');
  assert.equal(seeded[1], 'stickers/día por inspector activo', 'the per-active-inspector pace is the second tile');
  assert.equal(seeded[2], 'profesionales con actividad');
  assert.equal(seeded.length, 8);
  // Legacy row (no depuración): unchanged, "profesionales activos" still leads it.
  const legacy = labelsOf(kpisHtml(rowsFor({ stickers }), true));
  assert.equal(legacy[0], 'profesionales activos');
  assert.equal(legacy.length, 5);
  assert.ok(!legacy.includes('inspectores activos'));
});

named('test_kpi_inspectores_activos_373_row_fixture_is_fast', () => {
  const inspectores = Array.from({ length: 373 }, (_, i) => depInspector(i + 1, { estado_sugerido: i % 3 === 0 ? 'activo' : 'revisar' }));
  const stickers = [];
  for (let i = 1; i <= 116; i += 1) stickers.push(stickerFor(String(1000000 + i), `Profesional ${i}`));
  const result = rowsFor({ stickers, depuracion: depuracionOf(inspectores) });
  const started = performance.now();
  let html;
  for (let n = 0; n < 50; n += 1) html = kpisHtml(result, true);
  const perCall = (performance.now() - started) / 50;
  assert.equal(kpiTileValue(html, 'inspectores activos'), '125');
  assert.ok(perCall < 20, `kpisHtml over 373 rows took ${perCall}ms per call`);
});

// ── KPI "stickers/día por inspector activo" (owner definition 2026-09-19) ───
// Daily sticker performance of the ACTIVE padrón: stickers placed by profiles whose
// estado is exactly `activo`, inside the selected range, ÷ days of the range ÷ number
// of active inspectors. START = `from` else the earliest sticker date in the data;
// END = `to` else today (Bogotá) — a stray future sticker never stretches the range.

const SPI_LABEL = 'stickers/día por inspector activo';
const P11_DAY = 86400000;
function isoDayPlus(ymd, n) {
  const [y, m, d] = ymd.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d) + n * P11_DAY).toISOString().slice(0, 10);
}
// `n` stickers of one cédula on one Bogotá day (12:00 UTC = 07:00 Bogotá).
function stickersOn(cedula, day, n, fase = 1) {
  return Array.from({ length: n }, () => stickerFor(cedula, `Profesional ${Number(cedula) - 1000000}`, `${day}T12:00:00+00:00`, { fase }));
}
// 3 activos (1-3), 1 revisar (4), today = 2026-09-19.
//  activo 1: 2 F1 (09-10) + 1 F2 (09-12) + 1 F1 (09-13) = 4, plus 1 stray FUTURE sticker on 09-25
//  activo 2: 5 on 09-15                                  activo 3: nothing
//  revisar 4: 1 on 09-08 (earliest sticker of the data, NOT an active one) + 5 on 09-11
//  unattributed (no cédula, no name): 1 on 09-14
function spiFixture() {
  const dep = depuracionOf([
    depInspector(1, { estado_sugerido: 'activo' }),
    depInspector(2, { estado_sugerido: 'activo' }),
    depInspector(3, { estado_sugerido: 'activo' }),
    depInspector(4, { estado_sugerido: 'revisar' }),
  ]);
  const stickers = [
    ...stickersOn('1000001', '2026-09-10', 2, 1),
    ...stickersOn('1000001', '2026-09-12', 1, 2),
    ...stickersOn('1000001', '2026-09-13', 1, 1),
    ...stickersOn('1000001', '2026-09-25', 1, 1),
    ...stickersOn('1000002', '2026-09-15', 5, 2),
    ...stickersOn('1000004', '2026-09-08', 1, 1),
    ...stickersOn('1000004', '2026-09-11', 5, 1),
    stickerFor('', '', '2026-09-14T12:00:00+00:00'),
  ];
  return { dep, stickers };
}
const spiValue = (range) => {
  const { dep, stickers } = spiFixture();
  return kpiTileValue(kpisHtml(rowsFor({ stickers, depuracion: dep, ...range }), true), SPI_LABEL);
};

named('test_kpi_stickers_per_day_per_active_inspector_exact_arithmetic', () => {
  const { dep, stickers } = spiFixture();
  const result = rowsFor({ stickers, depuracion: dep });
  // No range: START = earliest sticker of the data (09-08, a NON-active one) .. END = today (09-19)
  // = 12 days; only the 9 counted stickers of activos 1 and 2 (the future one is out) / 12 / 3.
  assert.equal(result.totals.stickersInspectoresActivos, 9);
  assert.equal(result.totals.rangoDias, 12);
  assert.equal(result.totals.inspectoresActivos, 3);
  assert.equal(kpiTotals(result, { stickersLoaded: true }).stickersPorInspectorActivo, 0.25);
  assert.equal(kpiTileValue(kpisHtml(result, true), SPI_LABEL), '0,25');
  assert.equal(spiValue({ from: '2026-09-10', to: '2026-09-15' }), '0,5', '9 / 6 days / 3');
  assert.equal(spiValue({ to: '2026-09-12' }), '0,2', 'START = earliest sticker: 09-08..09-12 = 5 days, 3 counted stickers');
  assert.equal(spiValue({ from: '2026-09-10' }), '0,3', 'END = today, not the future sticker: 9 / 10 days / 3');
  // The revisar profile's stickers and the unattributed one never reach the numerator.
  assert.equal(spiValue({ from: '2026-09-11', to: '2026-09-11' }), '0', '1-day range holding only a non-active profile: real zero');
});

named('test_kpi_stickers_per_day_range_edges_show_dash_never_nan', () => {
  assert.equal(spiValue({ from: '2026-09-15', to: '2026-09-10' }), DASH, 'inverted range');
  assert.equal(spiValue({ from: '2026-09-25' }), DASH, 'from after today, no `to`');
  assert.equal(spiValue({ from: '2026-09-19' }), '0', 'one-day range (today) with nothing in it');
  assert.equal(spiValue({ from: '2026-09-19', to: '2026-09-19' }), '0');
  // An explicit `to` in the future is the user own choice: 09-10..09-30 = 21 days, 10 stickers / 21 / 3.
  assert.equal(spiValue({ from: '2026-09-10', to: '2026-09-30' }), '0,16');
  // No sticker at all and no `from`: nothing to anchor START on.
  const dep = depuracionOf(estadoInspectors({ activo: 3 }));
  const none = rowsFor({ depuracion: dep });
  assert.equal(none.totals.rangoDias, null);
  assert.equal(kpiTileValue(kpisHtml(none, true), SPI_LABEL), DASH);
  // ...but with a `from` the range is well defined and the value is a real 0.
  assert.equal(kpiTileValue(kpisHtml(rowsFor({ depuracion: dep, from: '2026-09-10' }), true), SPI_LABEL), '0');
  // Only undated stickers: they cannot be placed inside any range.
  const undated = rowsFor({ depuracion: dep, stickers: [stickerFor('1000001', 'Profesional 1', null)] });
  assert.equal(undated.totals.stickersInspectoresActivos, 0);
  assert.equal(kpiTileValue(kpisHtml(undated, true), SPI_LABEL), DASH);
  for (const range of [{ from: '2026-09-15', to: '2026-09-10' }, { from: '2026-09-25' }, {}, { from: '2027-01-01', to: '2027-01-31' }]) {
    assert.ok(!/NaN|Infinity|undefined|-\d/.test(spiValue(range) || ''), JSON.stringify(range));
  }
});

named('test_kpi_stickers_per_day_dash_without_active_inspectors_and_while_loading', () => {
  const { stickers } = spiFixture();
  const noActive = depuracionOf(estadoInspectors({ revisar: 3, candidato_desactivacion: 1 }));
  const zero = rowsFor({ stickers, depuracion: noActive });
  assert.equal(zero.totals.inspectoresActivos, 0);
  assert.equal(kpiTileValue(kpisHtml(zero, true), SPI_LABEL), DASH, 'no active inspector: never a division by zero');
  const { dep } = spiFixture();
  const result = rowsFor({ stickers, depuracion: dep });
  assert.equal(kpiTileValue(kpisHtml(result, false), SPI_LABEL), DASH, 'masked while stickers load');
  assert.equal(kpiTotals(result, { stickersLoaded: false }).stickersPorInspectorActivo, DASH);
  // Hand-built / older result shapes: no crash, DASH instead of NaN.
  const handBuilt = { rows: [], totals: { professionals: 0, padron: 3, inspectoresActivos: 3, stickers: 0, surveys: 0 } };
  let html;
  assert.doesNotThrow(() => { html = kpisHtml(handBuilt, true); });
  assert.equal(kpiTileValue(html, SPI_LABEL), DASH);
  assert.ok(!/NaN|Infinity/.test(html));
});

named('test_kpi_stickers_per_day_ignores_search_estado_and_non_active_activity', () => {
  const { dep, stickers } = spiFixture();
  const result = rowsFor({ stickers, depuracion: dep });
  const narrowed = visibleRowsFor(result.rows, { query: 'Profesional 4', estado: 'revisar' });
  assert.equal(narrowed.length, 1);
  assert.equal(kpiTileValue(kpisHtml({ rows: narrowed, totals: result.totals }, true), SPI_LABEL), '0,25', 'search/estado never move it');
  // More stickers from NON-active profiles change the rows with activity but not the tile.
  const noisy = rowsFor({ stickers: [...stickers, ...stickersOn('1000004', '2026-09-16', 40)], depuracion: dep });
  assert.equal(kpiTileValue(kpisHtml(noisy, true), SPI_LABEL), '0,25');
  // Orphan (unseeded) profiles are not active inspectors either.
  const orphan = rowsFor({ stickers: [...stickers, ...stickersOn('9999999', '2026-09-16', 40)], depuracion: dep });
  assert.equal(kpiTileValue(kpisHtml(orphan, true), SPI_LABEL), '0,25');
});

named('test_kpi_stickers_per_day_scaled_fixture_reproduces_the_live_shape', () => {
  // 146 active inspectors, 30 days (2026-08-21 .. 2026-09-19), 2,859 stickers of active
  // profiles, 706 of them in the last 7 days (09-13 .. 09-19): 0,65 overall and 0,69 for the week.
  const inspectores = [
    ...Array.from({ length: 146 }, (_, i) => depInspector(i + 1, { estado_sugerido: 'activo' })),
    ...Array.from({ length: 227 }, (_, i) => depInspector(147 + i, { estado_sugerido: 'revisar' })),
  ];
  const perDay = [];
  for (let d = 0; d < 23; d += 1) perDay.push(d < 14 ? 94 : 93); // 08-21 .. 09-12 = 2,153
  perDay.push(100, 100, 100, 100, 100, 100, 106); // 09-13 .. 09-19 = 706
  assert.equal(perDay.reduce((a, b) => a + b, 0), 2859);
  const stickers = [];
  let k = 0;
  perDay.forEach((n, d) => {
    const day = isoDayPlus('2026-08-21', d);
    for (let i = 0; i < n; i += 1) { k += 1; stickers.push(...stickersOn(String(1000001 + (k % 146)), day, 1)); }
  });
  stickers.push(...stickersOn('1000200', '2026-09-01', 300)); // non-active noise
  const dep = depuracionOf(inspectores);
  const all = rowsFor({ stickers, depuracion: dep });
  assert.equal(all.totals.inspectoresActivos, 146);
  assert.equal(all.totals.rangoDias, 30);
  assert.equal(all.totals.stickersInspectoresActivos, 2859);
  assert.equal(kpiTileValue(kpisHtml(all, true), SPI_LABEL), '0,65');
  const week = rowsFor({ stickers, depuracion: dep, from: '2026-09-13' });
  assert.equal(week.totals.rangoDias, 7);
  assert.equal(week.totals.stickersInspectoresActivos, 706);
  assert.equal(kpiTileValue(kpisHtml(week, true), SPI_LABEL), '0,69');
});

named('test_kpi_stickers_per_day_tile_position_title_and_legacy_row', () => {
  const { dep, stickers } = spiFixture();
  const html = kpisHtml(rowsFor({ stickers, depuracion: dep }), true);
  const labels = [...html.matchAll(/kpi-label[^>]*>([^<]*)</g)].map((m) => m[1]);
  assert.deepEqual(labels.slice(0, 3), ['inspectores activos', SPI_LABEL, 'profesionales con actividad']);
  assert.equal(labels.length, 8);
  assert.equal(
    kpiTileTitle(html, SPI_LABEL),
    'Stickers de los inspectores activos en el rango ÷ días del rango (12) ÷ inspectores activos (3). Es el rendimiento del padrón activo completo; no depende del buscador ni del filtro de estado.',
  );
  // Real numbers in the title use es-CO grouping too.
  const big = kpisHtml({ rows: [], totals: { professionals: 0, padron: 2000, inspectoresActivos: 1500, stickersInspectoresActivos: 9, rangoDias: 1200, stickers: 0, surveys: 0 } }, true);
  assert.match(kpiTileTitle(big, SPI_LABEL), /días del rango \(1\.200\) ÷ inspectores activos \(1\.500\)\./);
  // Legacy (no active depuración): byte-identical row, no new key anywhere.
  const legacyResult = rowsFor({ stickers: [stickerFor('1000001', 'Profesional 1')] });
  assert.equal(kpisHtml(legacyResult, true), LEGACY_KPI_HTML);
  assert.equal(Object.prototype.hasOwnProperty.call(legacyResult.totals, 'stickersInspectoresActivos'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(legacyResult.totals, 'rangoDias'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(kpiTotals(legacyResult), 'stickersPorInspectorActivo'), false);
  // Empty seeded padrón: nothing to classify, no tile (same condition as "inspectores activos").
  const empty = rowsFor({ stickers, depuracion: depuracionOf([]) });
  assert.equal(kpiTileValue(kpisHtml(empty, true), SPI_LABEL), null);
});

// ── Mass export notice: calm hint under the buttons, explicit refusal on click ──

named('test_mass_export_persistent_notice_is_a_calm_hint_over_the_cap', () => {
  const mk = (n) => Array.from({ length: n }, (_, i) => ({ key: `ced:${i}`, total: 1 }));
  const over = SEG.massExportScope(mk(407));
  assert.equal(
    SEG.massExportScopeText(over),
    'Exportación masiva: hay 407 profesionales con actividad y el máximo por exportación es 200. Para exportar, acotá los filtros (búsqueda, estado, rango o profesional).',
  );
  assert.doesNotMatch(SEG.massExportScopeText(over), /No se puede exportar|superan/i, 'reads as guidance, not as an error');
  assert.match(SEG.massExportScopeText(SEG.massExportScope(mk(1234))), /hay 1\.234 profesionales con actividad/, 'es-CO grouping');
  // Boundary: 200 is within the cap (informational sentence), 201 is over it (the hint).
  const at = SEG.massExportScope(mk(200));
  assert.equal(at.status, 'ok');
  assert.equal(SEG.massExportScopeText(at), 'Exportación masiva: 200 profesionales visibles, todos con actividad en el rango.');
  const justOver = SEG.massExportScope(mk(201));
  assert.equal(justOver.status, 'refused');
  assert.match(SEG.massExportScopeText(justOver), /^Exportación masiva: hay 201 profesionales con actividad y el máximo por exportación es 200\./);
  // A custom cap is reflected, never a hard-coded 200.
  assert.match(SEG.massExportScopeText(SEG.massExportScope(mk(5), { cap: 4 })), /hay 5 profesionales .* es 4\./);
  // Within the cap the pre-existing sentences are unchanged.
  const mixed = SEG.massExportScope([...mk(3), { key: 'z', total: 0 }]);
  assert.equal(SEG.massExportScopeText(mixed), 'Exportación masiva: 3 de 4 profesionales visibles (solo los profesionales con actividad en el rango).');
  assert.equal(SEG.massExportScopeText(SEG.massExportScope([])), 'Exportación masiva: ningún profesional visible con actividad en el rango.');
  assert.equal(SEG.massExportScopeText(null), '');
});

named('test_mass_export_click_refusal_keeps_the_explicit_wording_and_never_truncates', () => {
  const mk = (n) => Array.from({ length: n }, (_, i) => ({ key: `ced:${i}`, total: 1 }));
  const over = SEG.massExportScope(mk(407));
  assert.equal(
    over.message,
    'No se puede exportar: 407 profesionales con actividad superan el máximo de 200 por exportación. Acotá los filtros (búsqueda/estado/rango/profesional) antes de exportar.',
  );
  assert.equal(over.status, 'refused');
  assert.deepEqual(over.rows, [], 'no partial batch');
  assert.notEqual(SEG.massExportScopeText(over), over.message, 'the persistent notice is no longer the refusal text');
  const js = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  const handler = js.slice(js.indexOf('async function generarReportesMasivos'));
  assert.match(handler, /scope\.status === 'refused'\) \{ showToast\(scope\.message, 'error'\); return; \}/, 'the click still toasts the strong refusal');
});

named('test_mass_export_notice_has_no_error_style_and_hostile_values_stay_plain_text', () => {
  const js = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
  const tag = js.match(/<p class="([^"]*)" id="seg-report-scope"[^>]*><\/p>/);
  assert.ok(tag, 'the notice element keeps its position and id');
  assert.equal(tag[1], 'sticker-note', 'only the muted hint class');
  assert.doesNotMatch(tag[1], /error|warn|danger|alert/i);
  assert.match(css, /\.sticker-note\s*\{[^}]*color:\s*var\(--text-muted\)/, 'that class is the muted one');
  assert.doesNotMatch(js, /reportScopeEl\.classList/, 'no state class is ever toggled on it (no error/warning colors)');
  assert.match(js, /reportScopeEl\.textContent = text/);
  assert.doesNotMatch(js, /reportScopeEl\.innerHTML/);
  // XSS-looking numbers/text: values are coerced to numbers, markup never survives.
  const hostile = SEG.massExportScopeText({
    status: 'refused', withActivity: '<img src=x onerror=alert(1)>', cap: '<script>alert(1)</script>', visible: 1,
  });
  assert.doesNotMatch(hostile, /[<>]/);
  assert.doesNotMatch(hostile, /NaN|Infinity|undefined/);
  const weird = SEG.massExportScopeText({
    status: 'refused', withActivity: Number.POSITIVE_INFINITY, cap: undefined, visible: 0,
  });
  assert.doesNotMatch(weird, /NaN|Infinity|undefined/);
  const visibleHostile = SEG.massExportScopeText({ status: 'ok', withActivity: 2, visible: '<b>9</b>' });
  assert.doesNotMatch(visibleHostile, /[<>]/);
});

named('test_mass_export_notice_legacy_path_is_unchanged', () => {
  const js = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  // Still only rendered for a seeded table with stickers loaded; nothing shown otherwise.
  assert.match(js, /currentIdentity\.depuracionActiva && stickersLoaded\s*\? massExportScopeText\(massExportScope\(visibleRows\)\)\s*: ''/);
});

// W1: the XLSX "Filtros:" summary must mention the estado filter.
named('test_xlsx_filters_summary_mentions_the_estado_filter', () => {
  assert.equal(
    xlsxFiltersSummary({ estado: 'candidato_desactivacion' }),
    'Estado sugerido: Candidato a desactivación',
  );
  assert.equal(xlsxFiltersSummary({ estado: 'all' }), 'ninguno', '"all" is the no-op default');
  assert.equal(xlsxFiltersSummary({ estado: '' }), 'ninguno');
  assert.equal(xlsxFiltersSummary({ estado: null }), 'ninguno');
  assert.equal(xlsxFiltersSummary({ estado: 'estado_raro' }), 'Estado sugerido: estado_raro', 'an unknown estado prints raw, never dropped');
  assert.equal(
    xlsxFiltersSummary({
      search: 'ana', from: '2026-01-01', to: '2026-01-31', professionalName: 'Gil Soto', estado: 'revisar',
    }),
    'Búsqueda: "ana"; Desde: 2026-01-01; Hasta: 2026-01-31; Profesional: Gil Soto; Estado sugerido: Revisar',
    'stable order: the existing parts first, estado last',
  );
  // It agrees with hasActiveSegFilters: whenever a filter is active, the summary is not "ninguno".
  for (const estado of ['activo', 'revisar', 'candidato_desactivacion', 'no_persona', 'x']) {
    assert.equal(SEG.hasActiveSegFilters({ estado }), true);
    assert.notEqual(xlsxFiltersSummary({ estado }), 'ninguno');
  }
  // The download handler passes the live estado value.
  const js = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  assert.match(js, /xlsxFiltersSummary\(\{[^}]*estado: estadoEl\.value/, 'the handler forwards estadoEl.value');
});

// W3: with an active depuracion the name index is fed by the seeded profiles too.
named('test_seeded_names_unify_blank_and_unlisted_cedula_records_to_the_padron_cedula', () => {
  const dep = depuracionOf([
    depInspector(1, { identidad_key: '1001', identificacion: '1001', nombre_completo: 'Juan Pérez', estado_sugerido: 'activo' }),
    depInspector(2, { identidad_key: '1002', identificacion: '1002', nombre_completo: 'María Ñandú' }),
  ]);
  const stickers = [
    stickerFor('', 'juan perez'), // blank cédula, accents/case differ
    stickerFor('9999', 'JUAN PÉREZ'), // cédula not in the padrón
    stickerFor('   ', '  Maria   Ñandu  '), // whitespace cédula, spacing differs
  ];
  const surveys = [{ nombre_evaluador: 'Juan Perez', fecha_inspeccion: '2026-09-12' }];
  const { rows, totals } = rowsFor({ stickers, surveys, depuracion: dep });
  assert.deepEqual(rows.map((r) => r.key).sort(), ['ced:1001', 'ced:1002'], 'one human, one row: no nom: duplicates');
  const juan = rows.find((r) => r.key === 'ced:1001');
  assert.equal(juan.stickersTotal, 2);
  assert.equal(juan.surveyTotal, 1);
  assert.equal(juan.total, 3);
  assert.equal(juan.estadoSugerido, 'activo', 'the row with activity carries the estado, so the estado filter keeps it');
  assert.equal(rows.find((r) => r.key === 'ced:1002').stickersTotal, 1);
  assert.equal(totals.padron, 2);
  assert.equal(visibleRowsFor(rows, { estado: 'activo' }).filter((r) => r.total > 0).length, 1, 'the activity row is visible under its estado');
});

named('test_seeded_ambiguous_or_aliased_names_are_never_unified', () => {
  // Two seeded people share a normalized name: an unresolvable record stays separate.
  const dep = depuracionOf([
    depInspector(1, { identidad_key: '2001', identificacion: '2001', nombre_completo: 'Ana Gómez' }),
    depInspector(2, { identidad_key: '2002', identificacion: '2002', nombre_completo: 'ANA GOMEZ' }),
    depInspector(3, { identidad_key: '2003', identificacion: '2003', nombre_completo: 'Solo Uno' }),
  ]);
  const idx = buildIdentityIndex({ depuracion: dep });
  assert.equal(idx.nameToCedula.has(normalizeName('Ana Gómez')), false, 'ambiguous seeded name is not indexed');
  assert.equal(idx.nameToCedula.get(normalizeName('Solo Uno')), '2003');
  const { rows } = rowsFor({ stickers: [stickerFor('', 'ana gomez'), stickerFor('', 'Solo Uno')], depuracion: dep });
  const byKey = new Map(rows.map((r) => [r.key, r]));
  assert.equal(byKey.get('ced:2001').total, 0);
  assert.equal(byKey.get('ced:2002').total, 0);
  assert.equal(byKey.get(`nom:${normalizeName('ana gomez')}`).stickersTotal, 1, 'the homonym record keeps its own bucket');
  assert.equal(byKey.get('ced:2003').stickersTotal, 1);
  // The backend's alias_nombres stays authoritative over the seeded-name index.
  const aliased = depuracionOf([
    depInspector(1, { identidad_key: '1001', identificacion: '1001', nombre_completo: 'Juan Pérez' }),
    depInspector(2, { identidad_key: '1002', identificacion: '1002', nombre_completo: 'Otro' }),
  ], { alias_nombres: { [normalizeName('Juan Pérez')]: '1002' } });
  assert.equal(buildIdentityIndex({ depuracion: aliased }).nameToCedula.get(normalizeName('Juan Pérez')), '1002');
  // Two seeded entries for the SAME cédula and name are one person, not ambiguity.
  const same = depuracionOf([
    depInspector(1, { identidad_key: '3001', identificacion: '3001', nombre_completo: 'Luis Mora' }),
    depInspector(1, { identidad_key: '3001', identificacion: '3001', nombre_completo: 'luis mora' }),
  ]);
  assert.equal(buildIdentityIndex({ depuracion: same }).nameToCedula.get(normalizeName('Luis Mora')), '3001');
});

named('test_seeded_empty_or_missing_names_are_not_indexed_and_never_crash', () => {
  const dep = depuracionOf([
    depInspector(1, { nombre_completo: '' }),
    depInspector(2, { nombre_completo: null }),
    depInspector(3, { nombre_completo: '   ' }),
    depInspector(4, { nombre_completo: undefined }),
  ]);
  const idx = buildIdentityIndex({ depuracion: dep });
  assert.equal(idx.nameToCedula.size, 0, 'no empty-string key ever unifies blank-named records');
  const { rows, unassigned } = rowsFor({ stickers: [stickerFor('', ''), stickerFor('', '   ')], surveys: [{ nombre_evaluador: '' }], depuracion: dep });
  assert.equal(rows.length, 4, 'only the four seeded rows');
  assert.deepEqual(unassigned, { stickers: 2, surveys: 1 }, 'nameless records stay unassigned');
});

named('test_legacy_name_index_is_untouched_by_the_seeded_name_feed', () => {
  const stickers = [stickerFor('1001', 'Juan Pérez'), stickerFor('', 'juan perez')];
  const surveys = [{ nombre_evaluador: 'Juan Perez', fecha_inspeccion: '2026-09-12' }];
  const plain = buildIdentityIndex({ stickers, surveys });
  for (const depuracion of [null, { activa: false, inspectores: [depInspector(7, { nombre_completo: 'Otro Nombre' })] }]) {
    const other = buildIdentityIndex({ stickers, surveys, depuracion });
    assert.deepEqual([...other.nameToCedula], [...plain.nameToCedula], 'legacy nameToCedula is byte-identical');
    assert.deepEqual(other.nameToCedula.has(normalizeName('Otro Nombre')), false, 'an inactive block never feeds the name index');
  }
});

// S1: an object without a boolean `activa` is not a degradation announcement.
named('test_depuracion_block_without_boolean_activa_is_treated_as_absent', () => {
  for (const block of [{}, { motivo: 'sin_blob' }, { inspectores: [depInspector(1)] }, { activa: 'yes' }, { activa: 1 }, { activa: null }, { activa: undefined }]) {
    const identity = buildIdentityIndex({ depuracion: block });
    assert.equal(identity.depuracionAusente, true, `absent: ${JSON.stringify(block)}`);
    assert.equal(identity.depuracionActiva, false, `never active: ${JSON.stringify(block)}`);
    assert.equal(depuracionBadgeHtml(identity, { loaded: true }), null, `silent: ${JSON.stringify(block)}`);
    assert.equal(SEG.depuracionBadgeIsDegraded(identity), false);
  }
  // A real degraded/active block is still announced.
  assert.equal(buildIdentityIndex({ depuracion: { activa: false } }).depuracionAusente, false);
  assert.equal(SEG.depuracionBadgeIsDegraded(buildIdentityIndex({ depuracion: { activa: false } })), true);
  assert.equal(buildIdentityIndex({ depuracion: depuracionOf([depInspector(1)]) }).depuracionActiva, true);
});

// S4: a scalar where the backend should send a list must not silently drop the people.
named('test_revision_manual_scalar_identity_keys_render_an_escaped_fallback', () => {
  const identity = buildIdentityIndex({ depuracion: depuracionOf([depInspector(1)]) });
  const html = revisionManualHtml([{ motivo: 'x', identidad_keys: '1000001' }], { identity, isAdmin: true });
  assert.match(html, /Profesional 1 \(cédula 1000001\)/, 'a scalar key still resolves to the person');
  const numeric = revisionManualHtml([{ motivo: 'x', identidad_keys: 1000001 }], { identity, isAdmin: true });
  assert.match(numeric, /Profesional 1/);
  const evil = revisionManualHtml([{ motivo: 'x', identidad_keys: '<img src=x onerror=1>', identidad_keys_titulares: '"><script>1</script>' }], { identity, isAdmin: true });
  assert.match(evil, /&lt;img/, 'the raw fallback is shown escaped, not dropped');
  assert.doesNotMatch(evil, /<img|<script/);
  for (const empty of ['', null, undefined, [], {}, false]) {
    assert.doesNotMatch(revisionManualHtml([{ motivo: 'x', identidad_keys: empty }], { identity, isAdmin: true }), /personas/, `no "personas" for ${JSON.stringify(empty)}`);
  }
  assert.doesNotMatch(revisionManualHtml([{ motivo: 'x', identidad_keys: '1000001' }], { identity, isAdmin: false }), /1000001|Profesional/, 'a non-admin still sees no PII');
  // The list form is unchanged.
  assert.match(revisionManualHtml([{ motivo: 'x', identidad_keys: ['1000001'] }], { identity, isAdmin: true }), /personas: Profesional 1/);
});

// S5: XLSX formula injection. Verified against SheetJS 0.20.3 (the build
// loadXlsx() loads): aoa_to_sheet/sheet_add_json give every JS string a typed
// text cell (`t:"s"`, no `f`), and the writer emits `<c t="str"><v>=1+1</v></c>`
// with no `<f>` element, so a value starting with = + - @ is stored and
// displayed as TEXT, never evaluated. Nothing to neutralize; this guard fails
// if the export ever starts building formula cells.
named('test_xlsx_export_writes_plain_text_cells_never_formulas', () => {
  const js = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  const start = js.indexOf("downloadBtn.addEventListener('click'");
  const handler = js.slice(start, js.indexOf('// Survey renders immediately', start));
  assert.ok(handler.length > 200, 'located the export handler');
  assert.match(handler, /XLSX\.utils\.aoa_to_sheet/);
  assert.match(handler, /XLSX\.utils\.sheet_add_json/);
  assert.doesNotMatch(handler, /\bcellFormula\b|\bf:\s|\.f\s*=|t:\s*'f'|\bset_cell_formula|\bcell_set_formula/, 'no formula cell is ever built');
});

// ── D-ENFASIS: the registry's free-text "énfasis" (depuracion.inspectores[].enfasis) ─────────

const ENFASIS_TEXTO = 'Especialización en estructuras';
const ENFASIS_HOSTIL = `<script>alert('x')</script> "q" & <img src=x onerror=1>`;
const ENFASIS_LARGO = 'Estructuras '.repeat(200).trim(); // 2,399 chars

function enfasisRowFor(extra) {
  const dep = depuracionOf([depInspector(1, extra)]);
  return rowsFor({ depuracion: dep }).rows[0];
}
function enfasisReportRow(extra = {}) {
  return {
    name: 'Gil Soto', cedula: '1', codigo: '', entidad: '', np: '', barriosActivos: [],
    tarjetaProfesional: 'TP-1', stickersFase1: 0, stickersFase2: 0, surveyTotal: 0, total: 0,
    firstDate: null, lastDate: null, activeDays: 0, avgPerActiveDay: 0,
    avgStickersPerDay: null, rosterSourced: 0, ...extra,
  };
}
const ENFASIS_POINTS = { stickerPoints: [], surveyPoints: [] };
const ENFASIS_CTX = { from: null, to: null, generatedAt: '2026-09-19', today: '2026-09-19', objetivoDiario: null, degraded: false };

named('test_enfasis_is_read_into_the_depurado_profile_and_the_row', () => {
  const dep = depuracionOf([depInspector(1, { enfasis: ENFASIS_TEXTO })]);
  const identity = buildIdentityIndex({ stickers: [], surveys: [], depuracion: dep });
  assert.equal(identity.profiles.get('ced:1000001').enfasis, ENFASIS_TEXTO);
  const row = rowsFor({ depuracion: dep }).rows[0];
  assert.equal(row.enfasis, ENFASIS_TEXTO, 'the row carries the profile text untouched (accents and case kept)');
});

named('test_enfasis_missing_or_blank_in_the_depuracion_reads_as_an_empty_string', () => {
  for (const extra of [{}, { enfasis: '' }, { enfasis: null }, { enfasis: undefined }]) {
    const row = enfasisRowFor(extra);
    assert.equal(row.enfasis, '', `depuracion without a usable enfasis (${JSON.stringify(extra)}) -> ''`);
  }
});

named('test_enfasis_never_leaks_into_the_legacy_rows', () => {
  const stickers = [stickerFor('1000001', 'Profesional 1')];
  const legacy = rowsFor({ stickers }).rows[0];
  assert.equal('enfasis' in legacy, false, 'a legacy (unseeded) row has no enfasis key at all');
  const inactive = rowsFor({ stickers, depuracion: { ...depuracionOf([depInspector(1)]), activa: false } }).rows[0];
  assert.equal('enfasis' in inactive, false, 'an inactive depuracion is the legacy path');
});

named('test_enfasis_column_only_when_seeded_and_right_after_tarjeta_profesional', () => {
  assert.deepEqual(columnsFor('totales'), COLUMNS_TOTALES, 'legacy columns untouched');
  assert.deepEqual(columnsFor('totales', { withEstado: true }).map((c) => c.key),
    columnsFor('totales', { withEstado: true, withEnfasis: false }).map((c) => c.key), 'the flag defaults to off');
  const withEnfasis = columnsFor('totales', { withEnfasis: true });
  const keys = withEnfasis.map((c) => c.key);
  assert.equal(keys.length, COLUMNS_TOTALES.length + 1);
  assert.equal(keys[keys.indexOf('tarjetaProfesional') + 1], 'enfasis', 'right after "Tarjeta profesional"');
  assert.equal(withEnfasis.find((c) => c.key === 'enfasis').label, 'Énfasis');
  const both = columnsFor('totales', { withEstado: true, withEnfasis: true }).map((c) => c.key);
  assert.deepEqual(both.slice(1, 6), ['cedula', 'tarjetaProfesional', 'enfasis', 'np', 'estadoSugerido']);
  assert.equal(both[both.indexOf('tarjetaProfesional') + 1], 'enfasis');
  assert.equal(both[both.indexOf('np') + 1], 'estadoSugerido', 'the estado column keeps its own place');
  assert.equal(both.length, COLUMNS_TOTALES.length + 2);
  assert.deepEqual(columnsFor('temporales', { withEnfasis: true }), COLUMNS_TEMPORALES, 'temporales sub-tab unchanged');
  assert.ok(!COLUMNS_TOTALES.some((c) => c.key === 'enfasis'), 'the exported legacy array is never mutated');
});

named('test_enfasis_cell_escapes_and_follows_the_sin_dato_convention', () => {
  const texto = cellHtml({ enfasis: ENFASIS_TEXTO }, 'enfasis', true);
  assert.ok(texto.includes(ENFASIS_TEXTO), 'the real text is shown as is');
  assert.equal(cellHtml({ enfasis: '' }, 'enfasis', true), 'Sin dato');
  assert.equal(cellHtml({}, 'enfasis', true), 'Sin dato');
  assert.equal(cellHtml({ enfasis: ENFASIS_TEXTO }, 'enfasis', false), DASH, 'masked while stickers load, like the other identity cells');
  const hostil = cellHtml({ enfasis: ENFASIS_HOSTIL }, 'enfasis', true);
  assert.doesNotMatch(hostil, /<script|<img|onerror=1>/, 'no raw tag survives');
  assert.ok(hostil.includes('&lt;script&gt;') && hostil.includes('&quot;q&quot;') && hostil.includes('&amp;'), 'text escaped');
  // the tooltip attribute cannot be broken out of either
  const title = /title="([^"]*)"/.exec(hostil);
  assert.ok(title && !/[<>]/.test(title[1]) && !title[1].includes("'"), 'the title attribute is fully escaped');
});

named('test_enfasis_cell_long_text_is_confined_by_a_truncating_class', () => {
  const largo = cellHtml({ enfasis: ENFASIS_LARGO }, 'enfasis', true);
  assert.ok(largo.includes(ENFASIS_LARGO), 'the whole text stays reachable (tooltip / copy), never sliced in JS');
  assert.match(largo, /class="seg-enfasis"/);
  const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
  const rule = /\.seg-enfasis\s*\{([^}]*)\}/.exec(css);
  assert.ok(rule, 'styles.css defines .seg-enfasis');
  assert.match(rule[1], /max-width\s*:/);
  assert.match(rule[1], /overflow\s*:\s*hidden/);
  assert.match(rule[1], /text-overflow\s*:\s*ellipsis/);
  assert.match(rule[1], /white-space\s*:\s*nowrap/);
});

named('test_enfasis_cell_is_left_aligned_including_the_plain_sin_dato_cells', () => {
  // .tipologia-table td right-aligns numeric columns. The Énfasis cell is text: a blank one is the plain
  // string "Sin dato" (no span), so the alignment has to hang off the <td> itself, not off the capped span.
  const dep = depuracionOf([
    depInspector(1, { enfasis: ENFASIS_TEXTO }),
    depInspector(2, { enfasis: '' }),
  ]);
  const { rows } = rowsFor({ depuracion: dep });
  const columns = columnsFor('totales', { withEstado: true, withEnfasis: true });
  const html = SEG.tableBodyHtml(sortRows(rows, 'name', 'asc'), true, false, columns, false);
  const cells = [...html.matchAll(/<td class="seg-td-text">([^<]*(?:<span[^>]*>[^<]*<\/span>)?)<\/td>/g)];
  assert.equal(cells.length, 2, 'exactly the Énfasis cells (one per row) carry the text-alignment class');
  assert.ok(cells.some((m) => m[1] === 'Sin dato'), 'the blank cell is covered too');
  // Legacy table (no depurado base): no Énfasis column, so no td carries the class and the markup is unchanged.
  const legacy = SEG.tableBodyHtml(sortRows(rows, 'name', 'asc'), true, false, columnsFor('totales'), false);
  assert.equal(legacy.includes('seg-td-text'), false);
  const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
  const rule = /\.tipologia-table\s+td\.seg-td-text\s*\{([^}]*)\}/.exec(css);
  assert.ok(rule, 'styles.css aligns the Énfasis cell');
  assert.match(rule[1], /text-align\s*:\s*left/);
});

named('test_enfasis_xlsx_column_only_when_requested_legacy_sheet_untouched', () => {
  const row = { ...enfasisReportRow({ enfasis: ENFASIS_TEXTO }), key: 'ced:1', estadoSugerido: 'activo' };
  const legacyKeys = Object.keys(xlsxRowsFor([row], { subTab: 'totales' })[0]);
  assert.equal(legacyKeys.includes('enfasis'), false, 'the legacy sheet carries no enfasis column');
  assert.deepEqual(xlsxRowsFor([row]), xlsxRowsFor([row], { withEnfasis: false }), 'off by default');
  const [seeded] = xlsxRowsFor([row], { subTab: 'totales', withEnfasis: true });
  const keys = Object.keys(seeded);
  assert.equal(seeded.enfasis, ENFASIS_TEXTO);
  assert.equal(keys[keys.indexOf('tarjeta_profesional') + 1], 'enfasis', 'right after tarjeta_profesional');
  assert.deepEqual(keys.filter((k) => k !== 'enfasis'), legacyKeys, 'every other column is exactly the legacy set, in order');
  const [blank] = xlsxRowsFor([{ ...row, enfasis: undefined }], { withEnfasis: true });
  assert.equal(blank.enfasis, '', 'a missing value is an empty cell, never "undefined"');
  assert.deepEqual(xlsxRowsFor([row], { subTab: 'temporales', withEnfasis: true }), xlsxRowsFor([row], { subTab: 'temporales' }),
    'the temporales sheet never carries it');
  const [hostil] = xlsxRowsFor([{ ...row, enfasis: ENFASIS_HOSTIL }], { withEnfasis: true });
  assert.equal(hostil.enfasis, ENFASIS_HOSTIL, 'a spreadsheet cell keeps the raw text (typed text cell, never HTML)');
});

named('test_enfasis_pdf_row_sits_next_to_the_tarjeta_profesional', () => {
  const doc = buildProfessionalReportDocDefinition(enfasisReportRow({ enfasis: ENFASIS_TEXTO }), ENFASIS_POINTS, ENFASIS_CTX);
  const text = JSON.stringify(doc.content);
  assert.ok(text.includes('Énfasis') && text.includes(ENFASIS_TEXTO));
  assert.ok(text.indexOf('Tarjeta profesional') < text.indexOf('Énfasis'), 'after the tarjeta row');
  assert.ok(text.indexOf('Énfasis') < text.indexOf('Clase (P) / Código vigente'), 'before the next row');
  const vacio = JSON.stringify(buildProfessionalReportDocDefinition(enfasisReportRow({ enfasis: '' }), ENFASIS_POINTS, ENFASIS_CTX).content);
  assert.ok(vacio.includes('Énfasis'), 'a seeded row without énfasis still shows the row');
  assert.match(vacio, /"Énfasis"[^\]]*"—"/, '... with the forced dash');
  const hostil = JSON.stringify(buildProfessionalReportDocDefinition(enfasisReportRow({ enfasis: ENFASIS_HOSTIL }), ENFASIS_POINTS, ENFASIS_CTX).content);
  assert.ok(hostil.includes('onerror=1'), 'pdfmake prints text, it never parses it: the value is kept verbatim');
});

named('test_enfasis_pdf_legacy_report_is_byte_identical', () => {
  const sin = enfasisReportRow();
  assert.equal('enfasis' in sin, false);
  const text = JSON.stringify(buildProfessionalReportDocDefinition(sin, ENFASIS_POINTS, ENFASIS_CTX).content);
  assert.ok(!text.includes('Énfasis'), 'a legacy row (no enfasis key) gets no Énfasis row');
});

named('test_enfasis_search_matches_the_text_like_the_other_name_fields', () => {
  const row = { name: 'Xyz', cedula: '', tarjetaProfesional: '', np: 'P1', enfasis: ENFASIS_TEXTO };
  assert.equal(matchesSearch(row, 'estructuras'), true);
  assert.equal(matchesSearch(row, 'ESTRUCTURAS'), true, 'case-insensitive');
  assert.equal(matchesSearch(row, 'especializacion'), true, 'accent-insensitive: the query lost its accent');
  assert.equal(matchesSearch(row, 'especialización'), true, 'and the accented query matches too');
  assert.equal(matchesSearch(row, '  Estructuras  '), true, 'trimmed query');
  assert.equal(matchesSearch(row, 'geotecnia'), false, 'a different text never matches');
  assert.equal(matchesSearch({ ...row, enfasis: '' }, 'estructuras'), false, 'blank énfasis matches nothing');
  const legacy = { name: 'Xyz', cedula: '', tarjetaProfesional: '', np: 'P1' };
  assert.equal(matchesSearch(legacy, 'estructuras'), false, 'a legacy row (no key) never throws and never matches');
  assert.equal(matchesSearch({ ...row, enfasis: null }, 'estructuras'), false);
  assert.equal(matchesSearch({ ...row, enfasis: ENFASIS_LARGO }, 'estructuras'), true, 'a 2,000-char text is searchable');
  assert.equal(matchesSearch(row, '2026'), false, 'a >=3-digit query stays on the cédula/TP path (digits never search the text)');
  const e = { name: 'A', cedula: '', enfasis: 'Nivel 2026 estructuras' };
  assert.equal(matchesSearch(e, '2026'), false, 'consistent with np/name: digit runs are cédula/TP fragments');
});

named('test_enfasis_search_composes_with_visibleRowsFor_and_sorts_like_any_text_column', () => {
  const dep = depuracionOf([
    depInspector(1, { enfasis: 'Geotecnia' }), depInspector(2, { enfasis: 'Estructuras' }), depInspector(3),
  ]);
  const { rows } = rowsFor({ depuracion: dep });
  assert.equal(visibleRowsFor(rows, { query: 'geotec' }).length, 1);
  assert.equal(visibleRowsFor(rows, { query: 'geotec', estado: 'revisar' }).length, 1);
  assert.deepEqual(sortRows(rows, 'enfasis', 'asc').map((r) => r.enfasis), ['', 'Estructuras', 'Geotecnia']);
});

named('test_enfasis_dom_wiring_passes_the_seeded_flag_to_the_table_and_the_export', () => {
  const js = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  const calls = js.match(/columnsFor\(subTab, \{[^}]*\}\)/g) || [];
  assert.ok(calls.length >= 2 && calls.every((c) => /withEnfasis:\s*currentIdentity\.depuracionActiva/.test(c)),
    'both columnsFor call sites (render + stillSortable) gate the column on the depurado base');
  const xlsx = /xlsxRowsFor\(sorted, \{[^}]*\}\)/.exec(js);
  assert.ok(xlsx && /subTab:\s*sheetSubTab/.test(xlsx[0]) && /withEnfasis:\s*currentIdentity\.depuracionActiva/.test(xlsx[0]));
});

// ── D-PROFESION: the registry's free-text "profesión" (depuracion.inspectores[].profesion) ─────

const PROFESION_TEXTO = 'Ingeniero civil';
const PROFESION_HOSTIL = `<script>alert('x')</script> "q" & <img src=x onerror=1>`;
const PROFESION_LARGA = 'Ingeniero civil '.repeat(150).trim(); // 2,399 chars

function profesionRowFor(extra) {
  const dep = depuracionOf([depInspector(1, extra)]);
  return rowsFor({ depuracion: dep }).rows[0];
}
function profesionReportRow(extra = {}) {
  return enfasisReportRow(extra);
}

named('test_profesion_is_read_into_the_depurado_profile_and_the_row', () => {
  const dep = depuracionOf([depInspector(1, { profesion: 'Psicólogo' })]);
  const identity = buildIdentityIndex({ stickers: [], surveys: [], depuracion: dep });
  assert.equal(identity.profiles.get('ced:1000001').profesion, 'Psicólogo');
  const row = rowsFor({ depuracion: dep }).rows[0];
  assert.equal(row.profesion, 'Psicólogo', 'the row carries the profile text untouched (accents kept)');
  for (const variant of ['ingeniero', 'INGENIERO CIVIL', 'Arquitecta', 'Arquitecto']) {
    assert.equal(profesionRowFor({ profesion: variant }).profesion, variant, `case/gender variant kept verbatim: ${variant}`);
  }
});

named('test_profesion_missing_or_blank_in_the_depuracion_reads_as_an_empty_string', () => {
  for (const extra of [{}, { profesion: '' }, { profesion: null }, { profesion: undefined }, { profesion: 5 }, { profesion: ['x'] }]) {
    const row = profesionRowFor(extra);
    assert.equal(row.profesion, '', `depuracion without a usable profesion (${JSON.stringify(extra)}) -> ''`);
  }
});

named('test_profesion_never_leaks_into_the_legacy_rows', () => {
  const stickers = [stickerFor('1000001', 'Profesional 1')];
  const legacy = rowsFor({ stickers }).rows[0];
  assert.equal('profesion' in legacy, false, 'a legacy (unseeded) row has no profesion key at all');
  const inactive = rowsFor({ stickers, depuracion: { ...depuracionOf([depInspector(1)]), activa: false } }).rows[0];
  assert.equal('profesion' in inactive, false, 'an inactive depuracion is the legacy path');
});

named('test_profesion_and_enfasis_columns_are_independent_on_a_row', () => {
  const soloProfesion = profesionRowFor({ profesion: PROFESION_TEXTO });
  assert.equal(soloProfesion.profesion, PROFESION_TEXTO);
  assert.equal(soloProfesion.enfasis, '', 'a row with profesión but no énfasis');
  const soloEnfasis = profesionRowFor({ enfasis: ENFASIS_TEXTO });
  assert.equal(soloEnfasis.enfasis, ENFASIS_TEXTO);
  assert.equal(soloEnfasis.profesion, '', 'a row with énfasis but no profesión');
  assert.equal(cellHtml(soloProfesion, 'profesion', true).includes(PROFESION_TEXTO), true);
  assert.equal(cellHtml(soloProfesion, 'enfasis', true), 'Sin dato');
  assert.equal(cellHtml(soloEnfasis, 'profesion', true), 'Sin dato');
  assert.equal(cellHtml(soloEnfasis, 'enfasis', true).includes(ENFASIS_TEXTO), true);
});

named('test_profesion_column_only_when_requested_between_tarjeta_profesional_and_enfasis', () => {
  assert.deepEqual(columnsFor('totales'), COLUMNS_TOTALES, 'legacy columns untouched');
  assert.deepEqual(columnsFor('totales', { withEstado: true }).map((c) => c.key),
    columnsFor('totales', { withEstado: true, withProfesion: false }).map((c) => c.key), 'the flag defaults to off');
  const only = columnsFor('totales', { withProfesion: true });
  const onlyKeys = only.map((c) => c.key);
  assert.equal(onlyKeys.length, COLUMNS_TOTALES.length + 1);
  assert.equal(onlyKeys[onlyKeys.indexOf('tarjetaProfesional') + 1], 'profesion', 'right after "Tarjeta profesional"');
  assert.equal(only.find((c) => c.key === 'profesion').label, 'Profesión');
  assert.ok(!onlyKeys.includes('enfasis'), 'the énfasis flag is separate: off means no Énfasis column');
  const both = columnsFor('totales', { withEstado: true, withEnfasis: true, withProfesion: true }).map((c) => c.key);
  assert.deepEqual(both.slice(1, 7), ['cedula', 'tarjetaProfesional', 'profesion', 'enfasis', 'np', 'estadoSugerido'],
    'order: Tarjeta profesional, Profesión, Énfasis, then Clase (P) and Estado sugerido keep their place');
  assert.equal(both.length, COLUMNS_TOTALES.length + 3);
  const noProfesion = columnsFor('totales', { withEstado: true, withEnfasis: true }).map((c) => c.key);
  assert.ok(!noProfesion.includes('profesion'), 'énfasis alone never brings the Profesión column');
  assert.deepEqual(columnsFor('temporales', { withProfesion: true }), COLUMNS_TEMPORALES, 'temporales sub-tab unchanged');
  assert.ok(!COLUMNS_TOTALES.some((c) => c.key === 'profesion'), 'the exported legacy array is never mutated');
});

named('test_profesion_cell_escapes_and_follows_the_sin_dato_convention', () => {
  const texto = cellHtml({ profesion: PROFESION_TEXTO }, 'profesion', true);
  assert.ok(texto.includes(PROFESION_TEXTO), 'the real text is shown as is');
  assert.equal(cellHtml({ profesion: '' }, 'profesion', true), 'Sin dato');
  assert.equal(cellHtml({}, 'profesion', true), 'Sin dato');
  assert.equal(cellHtml({ profesion: null }, 'profesion', true), 'Sin dato');
  assert.equal(cellHtml({ profesion: PROFESION_TEXTO }, 'profesion', false), DASH, 'masked while stickers load, like the other identity cells');
  for (const v of ['ingeniero', 'INGENIERO CIVIL', 'Psicólogo']) {
    assert.ok(cellHtml({ profesion: v }, 'profesion', true).includes(`>${v}</span>`), `verbatim, never re-cased: ${v}`);
  }
  const hostil = cellHtml({ profesion: PROFESION_HOSTIL }, 'profesion', true);
  assert.doesNotMatch(hostil, /<script|<img|onerror=1>/, 'no raw tag survives');
  assert.ok(hostil.includes('&lt;script&gt;') && hostil.includes('&quot;q&quot;') && hostil.includes('&amp;'), 'text escaped');
  const title = /title="([^"]*)"/.exec(hostil);
  assert.ok(title && !/[<>]/.test(title[1]) && !title[1].includes("'"), 'the title attribute is fully escaped');
});

named('test_profesion_cell_long_text_is_confined_by_a_truncating_class', () => {
  const largo = cellHtml({ profesion: PROFESION_LARGA }, 'profesion', true);
  assert.ok(largo.includes(PROFESION_LARGA), 'the whole text stays reachable (tooltip / copy), never sliced in JS');
  assert.match(largo, /class="seg-profesion"/);
  assert.match(largo, /title="Ingeniero civil/);
  const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
  const rule = /([^{}]*\.seg-profesion[^{}]*)\{([^}]*)\}/.exec(css);
  assert.ok(rule, 'styles.css defines .seg-profesion');
  assert.match(rule[2], /max-width\s*:\s*24ch/);
  assert.match(rule[2], /overflow\s*:\s*hidden/);
  assert.match(rule[2], /text-overflow\s*:\s*ellipsis/);
  assert.match(rule[2], /white-space\s*:\s*nowrap/);
});

named('test_profesion_cell_is_left_aligned_including_the_plain_sin_dato_cells', () => {
  const dep = depuracionOf([
    depInspector(1, { profesion: PROFESION_TEXTO }),
    depInspector(2, { profesion: '' }),
  ]);
  const { rows } = rowsFor({ depuracion: dep });
  const columns = columnsFor('totales', { withEstado: true, withProfesion: true });
  const html = SEG.tableBodyHtml(sortRows(rows, 'name', 'asc'), true, false, columns, false);
  const cells = [...html.matchAll(/<td class="seg-td-text">([^<]*(?:<span[^>]*>[^<]*<\/span>)?)<\/td>/g)];
  assert.equal(cells.length, 2, 'exactly the Profesión cells (one per row) carry the text-alignment class');
  assert.ok(cells.some((m) => m[1] === 'Sin dato'), 'the blank cell is covered too');
  const ambas = SEG.tableBodyHtml(sortRows(rows, 'name', 'asc'), true, false,
    columnsFor('totales', { withEstado: true, withEnfasis: true, withProfesion: true }), false);
  assert.equal([...ambas.matchAll(/<td class="seg-td-text">/g)].length, 4, 'Profesión and Énfasis cells, two rows each');
  const legacy = SEG.tableBodyHtml(sortRows(rows, 'name', 'asc'), true, false, columnsFor('totales'), false);
  assert.equal(legacy.includes('seg-td-text'), false, 'the legacy table markup is unchanged');
});

named('test_profesion_table_renders_both_columns_in_order_and_independently', () => {
  const dep = depuracionOf([
    depInspector(1, { profesion: 'Arquitecto', enfasis: '' }),
    depInspector(2, { profesion: '', enfasis: 'Geotecnia' }),
  ]);
  const { rows } = rowsFor({ depuracion: dep });
  const columns = columnsFor('totales', { withEnfasis: true, withProfesion: true });
  const html = SEG.tableBodyHtml(sortRows(rows, 'name', 'asc'), true, false, columns, false);
  const cells = [...html.matchAll(/<td class="seg-td-text">(.*?)<\/td>/g)].map((m) => m[1]);
  assert.equal(cells.length, 4, 'two text cells per row, in column order: Profesión then Énfasis');
  assert.ok(cells[0].includes('Arquitecto') && cells[1] === 'Sin dato', 'row 1: profesión set, énfasis blank');
  assert.ok(cells[2] === 'Sin dato' && cells[3].includes('Geotecnia'), 'row 2: profesión blank, énfasis set');
});

named('test_profesion_xlsx_column_only_when_requested_legacy_sheet_untouched', () => {
  const row = { ...profesionReportRow({ profesion: PROFESION_TEXTO, enfasis: ENFASIS_TEXTO }), key: 'ced:1', estadoSugerido: 'activo' };
  const legacyKeys = Object.keys(xlsxRowsFor([row], { subTab: 'totales' })[0]);
  assert.equal(legacyKeys.includes('profesion'), false, 'the legacy sheet carries no profesion column');
  assert.deepEqual(xlsxRowsFor([row]), xlsxRowsFor([row], { withProfesion: false }), 'off by default');
  const [seeded] = xlsxRowsFor([row], { subTab: 'totales', withProfesion: true });
  const keys = Object.keys(seeded);
  assert.equal(seeded.profesion, PROFESION_TEXTO);
  assert.equal(keys[keys.indexOf('tarjeta_profesional') + 1], 'profesion', 'right after tarjeta_profesional');
  assert.equal(keys.includes('enfasis'), false, 'the énfasis flag is separate');
  assert.deepEqual(keys.filter((k) => k !== 'profesion'), legacyKeys, 'every other column is exactly the legacy set, in order');
  const [ambas] = xlsxRowsFor([row], { withEnfasis: true, withProfesion: true });
  const ambasKeys = Object.keys(ambas);
  assert.deepEqual(ambasKeys.slice(ambasKeys.indexOf('tarjeta_profesional'), ambasKeys.indexOf('tarjeta_profesional') + 3),
    ['tarjeta_profesional', 'profesion', 'enfasis'], 'profesión sits next to (right before) énfasis');
  const [blank] = xlsxRowsFor([{ ...row, profesion: undefined }], { withProfesion: true });
  assert.equal(blank.profesion, '', 'a missing value is an empty cell, never "undefined"');
  assert.deepEqual(xlsxRowsFor([row], { subTab: 'temporales', withProfesion: true }), xlsxRowsFor([row], { subTab: 'temporales' }),
    'the temporales sheet never carries it');
  const [hostil] = xlsxRowsFor([{ ...row, profesion: PROFESION_HOSTIL }], { withProfesion: true });
  assert.equal(hostil.profesion, PROFESION_HOSTIL, 'a spreadsheet cell keeps the raw text (typed text cell, never HTML)');
});

named('test_profesion_pdf_row_sits_right_before_the_enfasis_row', () => {
  const doc = buildProfessionalReportDocDefinition(profesionReportRow({ profesion: PROFESION_TEXTO, enfasis: ENFASIS_TEXTO }), ENFASIS_POINTS, ENFASIS_CTX);
  const text = JSON.stringify(doc.content);
  assert.ok(text.includes('Profesión') && text.includes(PROFESION_TEXTO));
  assert.ok(text.indexOf('Tarjeta profesional') < text.indexOf('Profesión'), 'after the tarjeta row');
  assert.ok(text.indexOf('Profesión') < text.indexOf('Énfasis'), 'right before the énfasis row');
  assert.ok(text.indexOf('Énfasis') < text.indexOf('Clase (P) / Código vigente'), 'the énfasis row keeps its place');
  const vacio = JSON.stringify(buildProfessionalReportDocDefinition(profesionReportRow({ profesion: '' }), ENFASIS_POINTS, ENFASIS_CTX).content);
  assert.ok(vacio.includes('Profesión'), 'a seeded row without profesión still shows the row');
  assert.match(vacio, /"Profesión"[^\]]*"—"/, '... with the forced dash');
  const solo = JSON.stringify(buildProfessionalReportDocDefinition(profesionReportRow({ profesion: PROFESION_TEXTO }), ENFASIS_POINTS, ENFASIS_CTX).content);
  assert.ok(solo.includes('Profesión') && !solo.includes('Énfasis'), 'a row with only profesión gets no Énfasis row');
  const hostil = JSON.stringify(buildProfessionalReportDocDefinition(profesionReportRow({ profesion: PROFESION_HOSTIL }), ENFASIS_POINTS, ENFASIS_CTX).content);
  assert.ok(hostil.includes('onerror=1'), 'pdfmake prints text, it never parses it: the value is kept verbatim');
});

named('test_profesion_pdf_legacy_report_is_byte_identical', () => {
  const sin = profesionReportRow();
  assert.equal('profesion' in sin, false);
  const text = JSON.stringify(buildProfessionalReportDocDefinition(sin, ENFASIS_POINTS, ENFASIS_CTX).content);
  assert.ok(!text.includes('Profesión'), 'a legacy row (no profesion key) gets no Profesión row');
  const conEnfasis = JSON.stringify(buildProfessionalReportDocDefinition(profesionReportRow({ enfasis: ENFASIS_TEXTO }), ENFASIS_POINTS, ENFASIS_CTX).content);
  assert.ok(!conEnfasis.includes('Profesión'), 'a row with only the énfasis key (previous shape) is unchanged too');
});

named('test_profesion_search_matches_the_text_like_the_other_name_fields', () => {
  const row = { name: 'Xyz', cedula: '', tarjetaProfesional: '', np: 'P1', profesion: 'Psicólogo' };
  assert.equal(matchesSearch(row, 'psicologo'), true, 'accent-insensitive: the query lost its accent');
  assert.equal(matchesSearch(row, 'PSICÓLOGO'), true, 'case-insensitive and accented');
  assert.equal(matchesSearch(row, '  psicólogo  '), true, 'trimmed query');
  assert.equal(matchesSearch(row, 'arquitecto'), false, 'a different text never matches');
  assert.equal(matchesSearch({ ...row, profesion: '' }, 'psicologo'), false, 'blank profesión matches nothing');
  const legacy = { name: 'Xyz', cedula: '', tarjetaProfesional: '', np: 'P1' };
  assert.equal(matchesSearch(legacy, 'psicologo'), false, 'a legacy row (no key) never throws and never matches');
  assert.equal(matchesSearch({ ...row, profesion: null }, 'psicologo'), false);
  assert.equal(matchesSearch({ ...row, profesion: PROFESION_LARGA }, 'ingeniero'), true, 'a 2,000-char text is searchable');
  assert.equal(matchesSearch({ ...row, profesion: 'Ingeniero civil' }, 'INGENIERO CIVIL'), true, 'case variants of the same profession are found by one query');
  assert.equal(matchesSearch({ ...row, profesion: 'ingeniero' }, 'Ingeniero'), true);
  assert.equal(matchesSearch(row, '2026'), false, 'a >=3-digit query stays on the cédula/TP path');
  assert.equal(matchesSearch({ name: 'A', cedula: '', profesion: 'Nivel 2026 civil' }, '2026'), false, 'digit runs are cédula/TP fragments');
  assert.equal(matchesSearch({ name: 'A', cedula: '', enfasis: ENFASIS_TEXTO }, 'psicologo'), false);
  assert.equal(matchesSearch({ name: 'A', cedula: '', profesion: 'Arquitecto', enfasis: '' }, 'arquitecto'), true, 'independent of the énfasis clause');
});

named('test_profesion_search_composes_with_visibleRowsFor_and_sorts_like_any_text_column', () => {
  const dep = depuracionOf([
    depInspector(1, { profesion: 'Arquitecto' }), depInspector(2, { profesion: 'Ingeniero civil' }), depInspector(3),
  ]);
  const { rows } = rowsFor({ depuracion: dep });
  assert.equal(visibleRowsFor(rows, { query: 'arquitec' }).length, 1);
  assert.equal(visibleRowsFor(rows, { query: 'arquitec', estado: 'revisar' }).length, 1);
  assert.deepEqual(sortRows(rows, 'profesion', 'asc').map((r) => r.profesion), ['', 'Arquitecto', 'Ingeniero civil']);
});

named('test_profesion_dom_wiring_passes_the_seeded_flag_to_the_table_and_the_export', () => {
  const js = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  const calls = js.match(/columnsFor\(subTab, \{[^}]*\}\)/g) || [];
  assert.ok(calls.length >= 2 && calls.every((c) => /withProfesion:\s*currentIdentity\.depuracionActiva/.test(c)),
    'both columnsFor call sites (render + stillSortable) gate the column on the depurado base');
  const xlsx = /xlsxRowsFor\(sorted, \{[^}]*\}\)/.exec(js);
  assert.ok(xlsx && /withProfesion:\s*currentIdentity\.depuracionActiva/.test(xlsx[0]) && /withEnfasis:\s*currentIdentity\.depuracionActiva/.test(xlsx[0]),
    'the export feeds both flags from the depurado base');
});

if (phase11Failures.length) {
  throw new Error(`Phase 11 named tests failed (${phase11Failures.length}): ${phase11Failures.join(', ')}`);
}

console.log('seguimiento.test.mjs: all assertions passed');
