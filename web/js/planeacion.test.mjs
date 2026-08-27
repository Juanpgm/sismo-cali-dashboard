// Self-check for the pure table/map/filter logic behind the Planeación tab.
// Run: node --test "js/**/*.test.mjs" (from web/)
import assert from 'node:assert/strict';
import {
  colorForPunto, buildRows, sortRows, filterRows, formatTruncacion, metricasHtml,
  kpisFromRows, barriosPorComunaFromRows, recoleccionResumen,
  buildHistorialRows, buildHistorialFiltro,
  buildVehiculoPayload, buildConductorPayload,
  rowHtml, filterRosterInspectores, filterInspectores,
  diaPicoPlacaHoy, autoAgruparMensaje,
  stickersAsignadosSuffix, stickersDesasignadosSuffix,
} from './planeacion.js';

// ---- colorForPunto — design.md ADR-10 map legend, 5 states -----------------
// green: tiene_survey (levantado) — wins over everything else.
assert.equal(colorForPunto({ tiene_survey: true, estado_asignacion: 'pendiente', prioridad: 'alta' }), 'green');
assert.equal(colorForPunto({ tiene_survey: true, estado_asignacion: 'no_aplica' }), 'green');
// grey: no_aplica — excluded from the pool, must never read as "pending".
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'no_aplica', prioridad: 'alta' }), 'grey');
// blue: asignado / en_proceso — work already under way.
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'asignado', prioridad: 'alta' }), 'blue');
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'en_proceso', prioridad: 'baja' }), 'blue');
// red: pendiente, effective priority alta.
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'pendiente', prioridad: 'alta' }), 'red');
// amber: pendiente, effective priority media or baja.
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'pendiente', prioridad: 'media' }), 'amber');
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'pendiente', prioridad: 'baja' }), 'amber');
// prioridad_override wins over the computed prioridad for the red/amber split too.
assert.equal(colorForPunto({ tiene_survey: false, estado_asignacion: 'pendiente', prioridad: 'baja', prioridad_override: 'alta' }), 'red');
assert.equal(colorForPunto(null), 'amber');
assert.equal(colorForPunto(undefined), 'amber');

// ---- buildRows — joins puntos with cuadrilla/inspector for the table -------
const puntos = [
  {
    id: 'atencionsismo_1', direccion: 'Cra 1', comuna: 'COMUNA 1', barrio: 'Barrio 1',
    afectacion: 'COLAPSO PARCIAL', estado_verificacion: 'Visitado', tipo_inmueble: 'Casa',
    habitabilidad: 'I', prioridad_score: 72, prioridad: 'alta', prioridad_override: null,
    estado_asignacion: 'asignado', cuadrilla_id: 'c1', inspector_uid: 'u1', grupo_id: 'g1', tier: 'alta',
    match_via: null, tiene_survey: false, coords: { lat: 3.4, lon: -76.5 },
    notas: null, motivo_exclusion: null, clave_integracion: 'PLN-1-ABCDEF01',
  },
  {
    id: 'atencionsismo_2', direccion: 'Cra 2', comuna: 'COMUNA 2', barrio: 'Barrio 2',
    afectacion: 'DAÑO ESTRUCTURAL', estado_verificacion: 'Reportado', tipo_inmueble: 'Edificio',
    habitabilidad: 'R', prioridad_score: 40, prioridad: 'media', prioridad_override: null,
    estado_asignacion: 'pendiente', cuadrilla_id: null, inspector_uid: null, tier: null,
    match_via: null, tiene_survey: false, coords: { lat: 3.5, lon: -76.4 },
    notas: null, motivo_exclusion: null, clave_integracion: 'PLN-2-ABCDEF02',
  },
];
const cuadrillas = [{ id: 'c1', nombre: 'Cuadrilla 1', puntos: ['atencionsismo_1'], inspector_uid: 'u1', origen: 'manual' }];
const inspectores = [{ uid: 'u1', nombre_completo: 'Ana Torres', codigo: '001' }];
const grupos = [{ id: 'g1', nombre: 'Grupo Norte' }];

const rows = buildRows(puntos, cuadrillas, inspectores, grupos);
assert.equal(rows.length, 2);
assert.equal(rows[0].cuadrillaLabel, 'Cuadrilla 1');
assert.equal(rows[0].inspectorLabel, 'Ana Torres');
assert.equal(rows[0].color, 'blue');
assert.equal(rows[0].prioridadEfectiva, 'alta');
// New: the point's inspector GROUP, resolved from its grupo_id.
assert.equal(rows[0].grupoLabel, 'Grupo Norte');
assert.equal(rows[0].recolectado, false); // no survey, not hecho
assert.equal(rows[1].cuadrillaLabel, '—');
assert.equal(rows[1].inspectorLabel, '—');
assert.equal(rows[1].grupoLabel, '—'); // no grupo_id -> em dash
assert.equal(rows[1].color, 'amber');
assert.equal(rows[1].prioridadEfectiva, 'media');
// recolectado is true once a survey exists or the assignment is marked hecho.
assert.equal(buildRows([{ id: 'x', tiene_survey: true }])[0].recolectado, true);
assert.equal(buildRows([{ id: 'y', estado_asignacion: 'hecho' }])[0].recolectado, true);
assert.equal(buildRows([{ id: 'z', estado_asignacion: 'asignado' }])[0].recolectado, false);

// ---- sortRows — effective priority DESC, prioridad_override wins -----------
const unordered = [
  { id: 'low', prioridad: 'baja', prioridad_override: null, prioridad_score: 10, prioridadEfectiva: 'baja' },
  { id: 'high', prioridad: 'alta', prioridad_override: null, prioridad_score: 90, prioridadEfectiva: 'alta' },
  { id: 'mid', prioridad: 'media', prioridad_override: null, prioridad_score: 50, prioridadEfectiva: 'media' },
];
assert.deepEqual(sortRows(unordered).map((r) => r.id), ['high', 'mid', 'low']);

// A low raw prioridad_score but an admin override to 'alta' must sort as alta —
// spec.md "An admin priority override is respected in ordering".
const withOverride = [
  { id: 'raw-high', prioridad: 'alta', prioridad_override: null, prioridad_score: 95, prioridadEfectiva: 'alta' },
  { id: 'overridden', prioridad: 'baja', prioridad_override: 'alta', prioridad_score: 5, prioridadEfectiva: 'alta' },
];
const sortedOverride = sortRows(withOverride);
assert.equal(sortedOverride[0].prioridadEfectiva, 'alta');
assert.equal(sortedOverride[1].prioridadEfectiva, 'alta');
// Tie on effective priority breaks by raw prioridad_score, descending.
assert.deepEqual(sortedOverride.map((r) => r.id), ['raw-high', 'overridden']);

// ---- filterRows — narrows by prioridad and comuna --------------------------
assert.equal(filterRows(rows, {}).length, 2);
assert.equal(filterRows(rows, { prioridad: 'alta' }).length, 1);
assert.deepEqual(filterRows(rows, { prioridad: 'alta' }).map((r) => r.id), ['atencionsismo_1']);
assert.deepEqual(filterRows(rows, { comuna: 'COMUNA 2' }).map((r) => r.id), ['atencionsismo_2']);
assert.equal(filterRows(rows, { prioridad: 'alta', comuna: 'COMUNA 2' }).length, 0);
assert.equal(filterRows(rows, { afectacion: 'DAÑO ESTRUCTURAL' }).length, 1);

// ---- filterRows search — dirección / grupo / comuna, case-insensitive ------
assert.equal(filterRows(rows, { search: '' }).length, 2, 'empty search never narrows');
assert.deepEqual(filterRows(rows, { search: 'cra 1' }).map((r) => r.id), ['atencionsismo_1']);
assert.deepEqual(filterRows(rows, { search: 'grupo norte' }).map((r) => r.id), ['atencionsismo_1'], 'search by group name finds its points');
assert.deepEqual(filterRows(rows, { search: 'COMUNA 2' }).map((r) => r.id), ['atencionsismo_2']);
assert.equal(filterRows(rows, { search: 'no-existe' }).length, 0);
// Search composes with the other narrowers (AND).
assert.equal(filterRows(rows, { prioridad: 'media', search: 'grupo norte' }).length, 0);

// ---- recoleccionResumen — recolectados / total over a row set --------------
assert.deepEqual(recoleccionResumen(rows), { recolectados: 0, total: 2 });
assert.deepEqual(
  recoleccionResumen([{ recolectado: true }, { recolectado: false }, { recolectado: true }]),
  { recolectados: 2, total: 3 },
);
assert.deepEqual(recoleccionResumen([]), { recolectados: 0, total: 0 });

// ---- formatTruncacion — spec.md "Truncation is surfaced to the operator" ---
assert.equal(
  formatTruncacion(2000, 13713),
  'Mostrando los 2000 puntos de mayor prioridad de 13713 pendientes.',
);
assert.equal(formatTruncacion(50, 50), null, 'not truncated -> no message');
assert.equal(formatTruncacion(0, 0), null);

// ---- kpisFromRows — working-set KPIs (2026-08-27), NOT resumen's
// full-collection tallies (rows[0]: alta/asignado, rows[1]: media/pendiente) --
const kpis = kpisFromRows(rows);
assert.equal(kpis.total, 2);
assert.deepEqual(kpis.porPrioridad, { alta: 1, media: 1, baja: 0 });
assert.deepEqual(kpis.porEstado, { pendiente: 1, asignado: 1, en_proceso: 0, hecho: 0, no_aplica: 0 });
assert.deepEqual(kpisFromRows([]), {
  total: 0,
  porPrioridad: { alta: 0, media: 0, baja: 0 },
  porEstado: { pendiente: 0, asignado: 0, en_proceso: 0, hecho: 0, no_aplica: 0 },
});

// ---- barriosPorComunaFromRows — auto-agrupar scope selects derived from the
// working set, not resumen's full-collection barrios_por_comuna ------------
assert.deepEqual(barriosPorComunaFromRows(rows), { 'COMUNA 1': ['Barrio 1'], 'COMUNA 2': ['Barrio 2'] });
assert.deepEqual(barriosPorComunaFromRows([{ comuna: 'C', barrio: '' }, { comuna: '', barrio: 'B' }]), {});
// Item 5 (2026-08-27): still reads correctly with the new 4500 default.
assert.equal(
  formatTruncacion(4500, 11000),
  'Mostrando los 4500 puntos de mayor prioridad de 11000 pendientes.',
);

// ---- stickersAsignadosSuffix / stickersDesasignadosSuffix — item 6 --------
assert.equal(stickersAsignadosSuffix({ stickers_asignados: 3 }), ' Y 3 puntos de sticker asignados al mismo grupo.');
assert.equal(stickersAsignadosSuffix({ stickers_asignados: 1 }), ' Y 1 punto de sticker asignado al mismo grupo.');
assert.equal(stickersAsignadosSuffix({ stickers_asignados: 0 }), '');
assert.equal(stickersAsignadosSuffix({}), '');
assert.equal(stickersAsignadosSuffix(null), '');
assert.equal(stickersDesasignadosSuffix({ stickers_desasignados: 2 }), ' Y 2 puntos de sticker quitados del mismo grupo.');
assert.equal(stickersDesasignadosSuffix({}), '');

// ---- metricasHtml — `metricasProgreso` change (`puntos-disponibles`, 2026-08-26)

assert.equal(metricasHtml(null, new Map()), '<p class="sticker-empty">Sin datos de progreso todavía.</p>');

const metricasFixture = {
  grupos: {
    g1: {
      nombre: 'Norte', miembros: 2, activo: true,
      combinado: { asignados: 4, hechos: 2, pendientes: 1, no_aplica: 1, completado_pct: 50.0 },
      stickers: { asignados: 1, hechos: 1, pendientes: 0, no_aplica: 0, completado_pct: 100.0 },
      survey: { asignados: 3, hechos: 1, pendientes: 1, no_aplica: 1, completado_pct: 33.3 },
    },
  },
  inspectores: {
    'uid-a': {
      grupos: ['Norte'],
      combinado: { asignados: 2, hechos: 1, pendientes: 1, no_aplica: 0, completado_pct: 50.0 },
      stickers: { asignados: 0, hechos: 0, pendientes: 0, no_aplica: 0, completado_pct: 0.0 },
      survey: { asignados: 2, hechos: 1, pendientes: 1, no_aplica: 0, completado_pct: 50.0 },
    },
  },
  combinado: { asignados: 4, hechos: 2, pendientes: 1, no_aplica: 1, completado_pct: 50.0 },
  stickers: { asignados: 1, hechos: 1, pendientes: 0, no_aplica: 0, completado_pct: 100.0 },
  survey: { asignados: 3, hechos: 1, pendientes: 1, no_aplica: 1, completado_pct: 33.3 },
};

const inspectorById = new Map([['uid-a', { uid: 'uid-a', nombre_completo: 'Ana Pérez' }]]);
const html = metricasHtml(metricasFixture, inspectorById);
assert.match(html, /Norte/);
assert.match(html, /50%/); // group's combined completion
assert.match(html, /100%/); // group's sticker completion
// Inspector NAME is resolved via inspectorById, never a bare raw uid.
assert.match(html, /Ana Pérez/);
assert.ok(!html.includes('uid-a'), 'raw uid must not leak into the rendered markup once resolved');
assert.match(html, /Norte/); // the inspector's own group membership list

const htmlSinRoster = metricasHtml(metricasFixture, new Map());
// Fail open: an unresolved uid (roster not loaded yet) falls back to the
// raw uid itself, never a blank/broken cell.
assert.match(htmlSinRoster, /uid-a/);

const vacio = metricasHtml({ grupos: {}, inspectores: {}, combinado: { asignados: 0, hechos: 0, pendientes: 0, no_aplica: 0, completado_pct: 0 }, stickers: { asignados: 0, hechos: 0, pendientes: 0, no_aplica: 0, completado_pct: 0 }, survey: { asignados: 0, hechos: 0, pendientes: 0, no_aplica: 0, completado_pct: 0 } }, new Map());
assert.match(vacio, /Todavía no hay grupos/);
assert.match(vacio, /Todavía no hay inspectores/);

// ---- buildHistorialRows / buildHistorialFiltro — "Historial" sub-tab
// (`planeacion-auditoria` change, Phase 5) --------------------------------

const auditEntradas = [
  { id: '1', actor_uid: 'u1', actor_email: 'ana@x.com', entidad: 'grupo', resumen: 'Crear grupo «Norte»', ts: '2026-08-20T10:00:00' },
  { id: '2', actor_uid: 'u2', actor_email: null, entidad: 'vehiculo', resumen: 'Crear vehículo ABC123', ts: '2026-08-21T10:00:00' },
];
const historialRows = buildHistorialRows(auditEntradas);
assert.equal(historialRows.length, 2);
assert.equal(historialRows[0].actorLabel, 'ana@x.com');
// Fallback to the raw uid when no email is on the entry.
assert.equal(historialRows[1].actorLabel, 'u2');
assert.equal(historialRows[0].entidadLabel, 'Grupo');
assert.equal(historialRows[1].entidadLabel, 'Vehículo');
assert.equal(historialRows[0].resumen, 'Crear grupo «Norte»');
assert.equal(buildHistorialRows(null).length, 0, 'fail open on missing data, never throw');

assert.deepEqual(buildHistorialFiltro({}), { action: 'listAuditoria' });
assert.deepEqual(buildHistorialFiltro({ tipo: 'vehiculo' }), { action: 'listAuditoria', tipo: 'vehiculo' });
assert.deepEqual(buildHistorialFiltro({ usuario: 'u9' }), { action: 'listAuditoria', usuario: 'u9' });
assert.deepEqual(
  buildHistorialFiltro({ tipo: 'vehiculo', usuario: 'u9' }),
  { action: 'listAuditoria', tipo: 'vehiculo', usuario: 'u9' },
);
assert.deepEqual(
  buildHistorialFiltro({ fecha: '2026-08-20' }),
  { action: 'listAuditoria', desde: '2026-08-20T00:00:00', antes_de: '2026-08-20T23:59:59' },
);

// ---- buildVehiculoPayload / buildConductorPayload — feature H frontend -----
// Existing-conductor branch: one crearVehiculo with the chosen conductor_id.
assert.deepEqual(
  buildVehiculoPayload({ placa: ' abc123 ', diaPicoPlaca: 'lunes', empresa: ' Acme ', conductorId: 'c1' }),
  { action: 'crearVehiculo', placa: 'abc123', dia_pico_placa: 'lunes', empresa: 'Acme', conductor_id: 'c1' },
);
// Edit branch keeps vehiculo_id + activo, still carries empresa/conductor_id.
assert.deepEqual(
  buildVehiculoPayload({ vehiculoId: 'v1', placa: 'XYZ', diaPicoPlaca: '', empresa: 'Co', activo: false, conductorId: 'c2' }),
  { action: 'editarVehiculo', vehiculo_id: 'v1', placa: 'XYZ', dia_pico_placa: '', empresa: 'Co', conductor_id: 'c2', activo: false },
);
// No conductor chosen → empty conductor_id (backend reads "" as no driver).
assert.equal(buildVehiculoPayload({ placa: 'A' }).conductor_id, '');

// New-conductor branch (step 1): crearConductor payload from the inline inputs.
assert.deepEqual(
  buildConductorPayload({ nombre_completo: ' Ana ', cedula: ' 123 ', email: ' a@x.com ', telefono: ' 555 ' }),
  { action: 'crearConductor', nombre_completo: 'Ana', cedula: '123', email: 'a@x.com', telefono: '555' },
);
// Step 2: the vehiculo payload uses the id crearConductor returned.
assert.equal(
  buildVehiculoPayload({ placa: 'A', conductorId: 'new-id-from-backend' }).conductor_id,
  'new-id-from-backend',
);

// ---- rowHtml / filterRosterInspectores — Slice B roster port from
// stickers.js (usuarios-personas-unificadas, Phase 3, task 3.1) ------------

// Active inspector: pill "Activo", "Inhabilitar" toggle with data-enable=false.
const activoHtml = rowHtml({ uid: 'u1', codigo: '001', nombre_completo: 'Ana Torres', cedula: '123', activo: true, disabled: false, registrado: true });
assert.match(activoHtml, /Activo/);
assert.match(activoHtml, /data-enable="false"/);
assert.match(activoHtml, /Inhabilitar/);

// disabled: true (Firebase Auth) -> reads as inactive regardless of `activo`.
const disabledHtml = rowHtml({ uid: 'u2', codigo: '002', nombre_completo: 'Beto Ruiz', cedula: '456', activo: true, disabled: true, registrado: true });
assert.match(disabledHtml, /Inhabilitado/);
assert.match(disabledHtml, /data-enable="true"/);
assert.match(disabledHtml, /Habilitar/);

// activo: false (Firestore profile) -> also reads as inactive.
const inactivoHtml = rowHtml({ uid: 'u3', codigo: '003', nombre_completo: 'Cami Ríos', cedula: '789', activo: false, disabled: false, registrado: true });
assert.match(inactivoHtml, /Inhabilitado/);

// Missing i.registrado -> "sin perfil" warning shown.
const sinPerfilHtml = rowHtml({ uid: 'u4', codigo: '004', nombre_completo: 'Dana Paz', cedula: '111', activo: true, disabled: false, registrado: false });
assert.match(sinPerfilHtml, /sin perfil/);
const conPerfilHtml = rowHtml({ uid: 'u5', codigo: '005', nombre_completo: 'Edu Soto', cedula: '222', activo: true, disabled: false, registrado: true });
assert.ok(!conPerfilHtml.includes('sin perfil'));

// Missing i.codigo falls back to em dash.
const sinCodigoHtml = rowHtml({ uid: 'u6', nombre_completo: 'Fer Luna', cedula: '333', activo: true, disabled: false, registrado: true });
assert.match(sinCodigoHtml, /—/);

// filterRosterInspectores — accent/case-insensitive across nombre/cédula/código/entidad.
const roster = [
  { uid: 'u1', nombre_completo: 'María José Peña', cedula: '1020735324', codigo: '007', entidad: 'SGRED' },
  { uid: 'u2', nombre_completo: 'Carlos Ruiz', cedula: '99887766', codigo: '008', entidad: 'DAGMA' },
];
assert.deepEqual(filterRosterInspectores(roster, 'maria jose').map((i) => i.uid), ['u1']);
assert.deepEqual(filterRosterInspectores(roster, 'MARÍA').map((i) => i.uid), ['u1']);
assert.deepEqual(filterRosterInspectores(roster, '1020735324').map((i) => i.uid), ['u1']);
assert.deepEqual(filterRosterInspectores(roster, '008').map((i) => i.uid), ['u2']);
assert.deepEqual(filterRosterInspectores(roster, 'dagma').map((i) => i.uid), ['u2']);
assert.equal(filterRosterInspectores(roster, '').length, 2);
assert.equal(filterRosterInspectores(roster, 'nomatch').length, 0);
// planeacion.js's OWN filterInspectores (narrower, nombre/código/cédula-only)
// stays a separate function — this is not a rename, both coexist.
assert.notEqual(filterInspectores, filterRosterInspectores);

// ---- diaPicoPlacaHoy — planeacion-flujo-confiable, design.md ADR-4 --------
// Bogotá weekday (no DST), mapped to the backend's unaccented Spanish set.
// 2026-08-24T15:00:00Z is a Monday in Bogotá (UTC-5); 2026-08-25T03:00:00Z
// is still Monday in Bogotá even though it's already Tuesday in UTC.
assert.equal(diaPicoPlacaHoy(new Date('2026-08-24T15:00:00Z')), 'lunes');
assert.equal(diaPicoPlacaHoy(new Date('2026-08-25T15:00:00Z')), 'martes');
assert.equal(diaPicoPlacaHoy(new Date('2026-08-25T03:00:00Z')), 'lunes');

// ---- autoAgruparMensaje — `Auto-agrupar returns actionable created-count
// feedback` (planeacion-asignaciones spec) ----------------------------------
assert.equal(
  autoAgruparMensaje(4),
  '4 cuadrillas creadas. Volver a ejecutar agrupa el siguiente lote.',
);
assert.equal(
  autoAgruparMensaje(1),
  '1 cuadrilla creada. Volver a ejecutar agrupa el siguiente lote.',
);
assert.equal(autoAgruparMensaje(0), 'No hay puntos pendientes sin agrupar.');

// `auto-agrupar-comuna-barrio` change: optional scope suffix.
assert.equal(
  autoAgruparMensaje(3, { comuna: 'COMUNA 19', barrio: 'San Fernando' }),
  '3 cuadrillas creadas en COMUNA 19 · barrio San Fernando. Volver a ejecutar agrupa el siguiente lote.',
);
assert.equal(
  autoAgruparMensaje(1, { comuna: 'COMUNA 19' }),
  '1 cuadrilla creada en COMUNA 19. Volver a ejecutar agrupa el siguiente lote.',
);
// Comuna-only path, exact call shape the click handler sends when the
// barrio select is left at "— Todos —" (empty string, not absent key):
// message must mention only the comuna.
assert.equal(
  autoAgruparMensaje(3, { comuna: 'COMUNA 19', barrio: '' }),
  '3 cuadrillas creadas en COMUNA 19. Volver a ejecutar agrupa el siguiente lote.',
);
assert.equal(
  autoAgruparMensaje(0, { comuna: 'COMUNA 19', barrio: 'San Fernando' }),
  'No hay puntos pendientes sin agrupar en COMUNA 19 · barrio San Fernando.',
);
assert.equal(autoAgruparMensaje(2, {}), autoAgruparMensaje(2));

console.log('ok — planeacion.js pure table/map/filter logic');
