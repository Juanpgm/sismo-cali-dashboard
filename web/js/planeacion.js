// Planeación tab: cross-reference of atencionsismo API reports against
// survey_cali (which ones already have an EDAN survey) and CRUD to group
// the still-pending ones into cuadrillas and assign, correct, exclude, or
// reopen them — `planeacion-asignaciones` change (Phase 4); design.md
// ADR-9/ADR-10; spec `Planeación tab mounting and admin-only role gating`,
// `Planeación UI — priority table, map, and correction affordances`.
//
// Structurally a clone of web/js/stickers-asignacion.js (design.md ADR-10):
// the callApi Bearer helper, a 3-step guided layout, a sortable/filterable
// table + Leaflet map sharing one `rows` array, a searchable inspector
// combobox, optimistic local mutation + renderAll() for per-item actions
// and a full reload() only for toolbar actions, teardownMap()/renderMap().
//
// TWO real differences from that template, both load-bearing:
//   1. Scale (ADR-9): `planeacion_puntos` holds ~14.8k documents. `listPuntos`
//      is a BOUNDED query (never "load everything"), so this module ALSO
//      calls `resumen` for the KPI tiles and surfaces `truncado` honestly —
//      never silently rendering a partial list as if it were the whole pool.
//   2. Roster (ADR-10): Planeación is a TOP-LEVEL tab, not a sub-section of
//      Stickers, so nothing has loaded the inspector roster for it yet. This
//      module fetches its OWN roster from `/api/stickers` `{action:'list'}`
//      once per init and caches it for the session — the SAME roster
//      Stickers uses (binding constraint: no separate professionals
//      collection), filtered by the same `habilitado` rule.
import { COLORS, escapeHtml, basemapTileUrl } from './utils.js';
import { apiUrl } from './api-config.js';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
const CALI_BBOX = { latMin: 3.30, latMax: 3.55, lngMin: -76.60, lngMax: -76.40 };

// design.md ADR-8, task 0.3's carried-over placeholders — a full EDAN survey
// is a far longer visit than applying a sticker, hence the visible override
// inputs (never enforced client-side; the backend applies its own default).
const DEFAULT_MAX_RADIUS_M = 800;
const DEFAULT_MAX_SIZE = 10;

// `grupos-inspectores` follow-up (2026-08-26): mirrors the backend's own
// named MAX_MIEMBROS_GRUPO constant — a UI-side hint/label ONLY. The
// endpoint (planeacion_asignaciones.py's crearGrupo/editarGrupo) is the
// real boundary that enforces this; a client-side check is not enough.
const MAX_MIEMBROS_GRUPO = 4;

const PRIORIDADES = ['alta', 'media', 'baja'];
const PRIORIDAD_LABELS = { alta: 'Alta', media: 'Media', baja: 'Baja' };
const PRIORIDAD_RANK = { alta: 3, media: 2, baja: 1 };
const ESTADO_LABELS = {
  pendiente: 'Pendiente', asignado: 'Asignado', en_proceso: 'En proceso',
  hecho: 'Hecho', no_aplica: 'No aplica',
};

// design.md ADR-10 map legend — 5 colours, the smallest legend that keeps
// both "assigned but not visited" and "high priority" distinguishable from
// a plain "pending".
const MARKER_HEX = {
  green: COLORS.status.h, red: COLORS.status.i2, amber: COLORS.status.r2,
  blue: COLORS.categorical[0], grey: COLORS.unknown,
};

// cloned verbatim from stickers-asignacion.js's own callApi, endpoint
// resolved via the per-endpoint URL map (spec: "The endpoint URL comes from
// the config map"), never a literal path.
async function callApi(getToken, body) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volver a iniciar sesión.');
  const res = await fetch(apiUrl('planeacionAsignaciones'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

// Roster read-only reuse of Stickers' own endpoint (design.md ADR-10 — this
// module is a top-level tab, so nothing has loaded the roster for it).
async function callStickersApi(getToken, body) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volver a iniciar sesión.');
  const res = await fetch(apiUrl('stickers'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

// ---- pure logic (exported for the self-check, planeacion.test.mjs) --------

/** Same "habilitado" rule stickers-asignacion.js:isHabilitado / stickers.js
 *  use — neither Firebase Auth `disabled` nor the profile `activo` false. */
export function isHabilitado(i) {
  return !!i && !i.disabled && !!i.activo;
}

/** The priority a row/point is actually ranked by: the admin's
 *  `prioridad_override` wins over the pipeline-computed `prioridad`
 *  (design.md ADR-4's "escape hatch"). */
export function prioridadEfectiva(p) {
  return (p && (p.prioridad_override || p.prioridad)) || 'baja';
}

/** Map marker / row colour per design.md ADR-10's 5-state legend.
 *  Precedence, most specific first: a surveyed point always reads as
 *  "levantado" (green) even if somehow also excluded; an excluded point
 *  reads as grey even if it was mid-priority; work already under way
 *  (asignado/en_proceso) reads blue regardless of priority; everything
 *  else pending splits red (alta) / amber (media, baja). */
export function colorForPunto(punto) {
  if (punto && punto.tiene_survey === true) return 'green';
  if (punto && punto.estado_asignacion === 'no_aplica') return 'grey';
  if (punto && (punto.estado_asignacion === 'asignado' || punto.estado_asignacion === 'en_proceso')) return 'blue';
  return prioridadEfectiva(punto) === 'alta' ? 'red' : 'amber';
}

/** Joins raw planeacion_puntos points with their cuadrilla/inspector for the
 *  table — computed once per load so client-side sort/filter never re-look-up. */
export function buildRows(puntos, cuadrillas, inspectores) {
  const cuadrillaById = new Map((cuadrillas || []).map((c) => [c.id, c]));
  const inspectorById = new Map((inspectores || []).map((i) => [i.uid, i]));
  return (puntos || []).map((p) => {
    const cuadrilla = p.cuadrilla_id ? cuadrillaById.get(p.cuadrilla_id) : null;
    const inspector = p.inspector_uid ? inspectorById.get(p.inspector_uid) : null;
    return {
      id: p.id,
      direccion: p.direccion || '',
      barrio: p.barrio || '',
      comuna: p.comuna || '',
      afectacion: p.afectacion || '',
      estadoVerificacion: p.estado_verificacion || '',
      tipoInmueble: p.tipo_inmueble || '',
      habitabilidad: p.habitabilidad || '',
      prioridad: p.prioridad || null,
      prioridad_override: p.prioridad_override || null,
      prioridadEfectiva: prioridadEfectiva(p),
      prioridad_score: Number.isFinite(p.prioridad_score) ? p.prioridad_score : 0,
      estado_asignacion: p.estado_asignacion || 'pendiente',
      cuadrilla_id: p.cuadrilla_id || null,
      cuadrillaLabel: cuadrilla ? (cuadrilla.nombre || cuadrilla.id) : '—',
      inspector_uid: p.inspector_uid || null,
      inspectorLabel: inspector ? (inspector.nombre_completo || inspector.codigo || inspector.uid) : '—',
      tier: p.tier || null,
      match_via: p.match_via || null,
      tiene_survey: !!p.tiene_survey,
      coords: p.coords || null,
      notas: p.notas || null,
      motivo_exclusion: p.motivo_exclusion || null,
      clave_integracion: p.clave_integracion || null,
      color: colorForPunto(p),
    };
  });
}

/** Ordered by effective priority DESC, tie-broken by the raw
 *  `prioridad_score` DESC — mirrors the backend's own `_sort_key`
 *  (spec.md "Results are ordered by priority" / "An admin priority
 *  override is respected in ordering"). */
export function sortRows(rows) {
  return [...(rows || [])].sort((a, b) => {
    const rankDiff = (PRIORIDAD_RANK[b.prioridadEfectiva] || 0) - (PRIORIDAD_RANK[a.prioridadEfectiva] || 0);
    if (rankDiff !== 0) return rankDiff;
    return (b.prioridad_score || 0) - (a.prioridad_score || 0);
  });
}

/** Filter chip logic (spec.md "Filtering narrows the working set"):
 *  `prioridad` narrows by the EFFECTIVE priority, `comuna`/`afectacion` by
 *  exact match. A falsy/missing filter key does not narrow that dimension. */
export function filterRows(rows, { prioridad, comuna, afectacion } = {}) {
  return (rows || []).filter((r) => {
    if (prioridad && r.prioridadEfectiva !== prioridad) return false;
    if (comuna && r.comuna !== comuna) return false;
    if (afectacion && r.afectacion !== afectacion) return false;
    return true;
  });
}

/** design.md ADR-9's "truncation is shown, never hidden" message. `null`
 *  when nothing was truncated (shown >= total), so callers can decide
 *  whether to render the banner at all. */
export function formatTruncacion(shown, totalPendientes) {
  if (!(totalPendientes > shown)) return null;
  return `Mostrando los ${shown} puntos de mayor prioridad de ${totalPendientes} pendientes.`;
}

/** Filter the roster by a free-text query over nombre/código/cédula — same
 *  pattern as stickers-asignacion.js:filterInspectores. */
export function filterInspectores(inspectores, query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return inspectores || [];
  return (inspectores || []).filter((i) => {
    const hay = `${i.nombre_completo || ''} ${i.codigo || ''} ${i.cedula || ''}`.toLowerCase();
    return hay.includes(q);
  });
}

// ---- markup -----------------------------------------------------------------

function cardHead(title, subtitle, extra = '') {
  return `<div class="card-toolbar asignacion-card-head">
    <div class="asignacion-card-titles">
      <span class="eval-toolbar-title">${title}</span>
      <span class="asignacion-subtitle">${subtitle}</span>
    </div>
    ${extra}
  </div>`;
}

function shellHtml() {
  return `
    <div class="section-bar">
      <h3 class="section-bar-title">Planeación</h3>
      <span class="eval-toolbar-meta" id="planeacion-map-meta"></span>
    </div>
    <p class="sticker-ok" id="planeacion-ok" role="status" hidden></p>
    <p class="sticker-error" id="planeacion-error" role="alert" hidden></p>

    <nav class="asignacion-segmented planeacion-subtabs" role="tablist" aria-label="Vistas de Planeación">
      <button type="button" class="asignacion-segment is-active" data-subtab-btn="puntos" id="planeacion-tab-puntos" role="tab" aria-selected="true" aria-controls="planeacion-panel-puntos">Puntos</button>
      <button type="button" class="asignacion-segment" data-subtab-btn="grupos" id="planeacion-tab-grupos" role="tab" aria-selected="false" aria-controls="planeacion-panel-grupos">Grupos</button>
      <button type="button" class="asignacion-segment" data-subtab-btn="vehiculos" id="planeacion-tab-vehiculos" role="tab" aria-selected="false" aria-controls="planeacion-panel-vehiculos">Vehículos</button>
    </nav>

    <section class="planeacion-subpanel" data-subtab="puntos" id="planeacion-panel-puntos" role="tabpanel" aria-labelledby="planeacion-tab-puntos">
    <p class="asignacion-intro">Reportes del API sin levantamiento EDAN todavía, priorizados. Agrupar en cuadrillas, asignar inspectores y abrir el enlace de Survey123 prellenado para cada punto.</p>
    <ol class="asignacion-steps">
      <li><span class="asignacion-step-n">1</span> Priorizar.</li>
      <li><span class="asignacion-step-n">2</span> Cuadrillas e inspectores.</li>
      <li><span class="asignacion-step-n">3</span> Puntos.</li>
    </ol>

    <section class="kpi-row" id="planeacion-kpis" aria-label="Resumen de planeación"></section>
    <p class="planeacion-truncacion" id="planeacion-truncacion" role="status" hidden></p>

    <div class="asignacion-workspace">
      <div class="asignacion-main">
        <div class="card">
          ${cardHead('Paso 1 · Priorizar', 'Auto-agrupar los puntos pendientes por cercanía, o marcar filas en la tabla para crear una cuadrilla manual.')}
          <div class="card-toolbar asignacion-actions-bar" id="planeacion-toolbar">
            <button type="button" class="btn-primary" id="planeacion-auto">Auto-agrupar</button>
            <label class="sticker-field asignacion-inline-field">
              <span>Radio (m)</span>
              <input type="number" id="planeacion-max-radius" min="50" step="50" placeholder="${DEFAULT_MAX_RADIUS_M}">
            </label>
            <label class="sticker-field asignacion-inline-field">
              <span>Tamaño máx.</span>
              <input type="number" id="planeacion-max-size" min="2" step="1" placeholder="${DEFAULT_MAX_SIZE}">
            </label>
            <button type="button" class="sticker-action" id="planeacion-crear" disabled>Crear cuadrilla de la selección</button>
            <label class="asignacion-inline-field planeacion-toggle-field" title="Muestra también los puntos que ya tienen encuesta, para revisar o corregir un cierre automático equivocado.">
              <input type="checkbox" id="planeacion-incluir-levantados">
              <span>Incluir levantados</span>
            </label>
          </div>
          <div class="card-toolbar asignacion-actions-bar" id="planeacion-grupo-toolbar" title="Un grupo de inspectores: cualquier miembro puede completar (stickers o survey) los puntos asignados al grupo.">
            <label class="sticker-field asignacion-inline-field">
              <span>Grupo de inspectores</span>
              <select id="planeacion-grupo-select"><option value="">— Elegir grupo —</option></select>
            </label>
            <button type="button" class="sticker-action" id="planeacion-asignar-grupo" disabled>Asignar grupo a selección</button>
            <button type="button" class="sticker-action sticker-action-off" id="planeacion-quitar-grupo" disabled>Quitar grupo de selección</button>
          </div>
        </div>

        <div class="card">
          ${cardHead('Paso 2 · Cuadrillas e inspectores', 'Asignar un inspector a cada cuadrilla. «Reiniciar agrupación» borra solo las automáticas.', '<button type="button" class="sticker-action sticker-action-off" id="planeacion-reiniciar">Reiniciar agrupación</button>')}
          <div class="asignacion-cuadrillas-scroll" id="planeacion-cuadrillas"></div>
        </div>

        <div class="card">
          ${cardHead('Progreso por grupo e inspector', 'Avance combinado de ambas campañas (stickers y encuesta EDAN), con el detalle de cada una.')}
          <div id="planeacion-metricas"></div>
        </div>

        <div class="card">
          ${cardHead('Paso 3 · Puntos', 'Filtrar, ordenar, corregir, excluir o abrir el enlace de Survey123.')}
          <div class="card-toolbar asignacion-filters" id="planeacion-filters"></div>
          <div class="table-scroll asignacion-table-scroll" id="planeacion-table-wrap"></div>
        </div>
      </div>

      <aside class="asignacion-aside">
        <div class="card eval-workspace-card asignacion-map-card">
          ${cardHead('Mapa de puntos', 'Reasignar un punto desde su globo.')}
          <div class="eval-map asignacion-map" id="planeacion-map"></div>
        </div>
      </aside>
    </div>
    </section>

    <section class="planeacion-subpanel" data-subtab="grupos" id="planeacion-panel-grupos" role="tabpanel" aria-labelledby="planeacion-tab-grupos" hidden>
      <div class="card">
        ${cardHead('Grupos de inspectores', `Un grupo de inspectores (máximo ${MAX_MIEMBROS_GRUPO} personas): cualquier miembro puede completar los puntos asignados al grupo, ya sea de stickers o de survey. Cada grupo sale en un vehículo.`, '<button type="button" class="btn-primary" id="planeacion-grupo-crear">Crear grupo</button>')}
        <div class="asignacion-cuadrillas-scroll" id="planeacion-grupos"></div>
      </div>
    </section>

    <section class="planeacion-subpanel" data-subtab="vehiculos" id="planeacion-panel-vehiculos" role="tabpanel" aria-labelledby="planeacion-tab-vehiculos" hidden>
      <div class="card">
        ${cardHead('Vehículos', 'Vehículos disponibles para asignar a un grupo — un vehículo solo puede estar en un grupo a la vez.', '<button type="button" class="btn-primary" id="planeacion-vehiculo-crear">Crear vehículo</button>')}
        <div class="asignacion-cuadrillas-scroll" id="planeacion-vehiculos"></div>
      </div>
    </section>

    <div class="modal" id="planeacion-editar-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="planeacion-editar-title">
      <div class="modal-backdrop" data-editar-close></div>
      <div class="modal-panel sticker-modal-panel">
        <div class="modal-header">
          <h2 id="planeacion-editar-title">Editar asignación</h2>
          <button type="button" class="btn-icon" data-editar-close aria-label="Cerrar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <input type="hidden" id="planeacion-editar-punto-id">
          <label class="sticker-field"><span>Estado</span>
            <select id="planeacion-editar-estado">
              <option value="">— sin cambiar —</option>
              <option value="pendiente">Pendiente</option>
              <option value="asignado">Asignado</option>
              <option value="en_proceso">En proceso</option>
              <option value="hecho">Hecho</option>
            </select>
          </label>
          <label class="sticker-field"><span>Prioridad (override)</span>
            <select id="planeacion-editar-prioridad">
              <option value="">— sin cambiar —</option>
              <option value="alta">Alta</option>
              <option value="media">Media</option>
              <option value="baja">Baja</option>
            </select>
          </label>
          <label class="sticker-field"><span>Notas</span>
            <textarea id="planeacion-editar-notas" rows="3"></textarea>
          </label>
          <p class="sticker-error" id="planeacion-editar-error" role="alert" hidden></p>
          <div class="sticker-form-actions">
            <button type="button" class="btn-secondary" data-editar-close>Cancelar</button>
            <button type="button" class="btn-primary" id="planeacion-editar-save">Guardar</button>
          </div>
        </div>
      </div>
    </div>

    <div class="modal" id="planeacion-noaplica-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="planeacion-noaplica-title">
      <div class="modal-backdrop" data-noaplica-close></div>
      <div class="modal-panel sticker-modal-panel">
        <div class="modal-header">
          <h2 id="planeacion-noaplica-title">Marcar no aplica</h2>
          <button type="button" class="btn-icon" data-noaplica-close aria-label="Cerrar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <input type="hidden" id="planeacion-noaplica-punto-id">
          <label class="sticker-field"><span>Motivo (obligatorio)</span>
            <textarea id="planeacion-noaplica-motivo" rows="3" required></textarea>
          </label>
          <p class="sticker-error" id="planeacion-noaplica-error" role="alert" hidden></p>
          <div class="sticker-form-actions">
            <button type="button" class="btn-secondary" data-noaplica-close>Cancelar</button>
            <button type="button" class="btn-primary" id="planeacion-noaplica-save">Excluir</button>
          </div>
        </div>
      </div>
    </div>

    <div class="modal" id="planeacion-survey-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="planeacion-survey-title">
      <div class="modal-backdrop" data-survey-close></div>
      <div class="modal-panel sticker-modal-panel">
        <div class="modal-header">
          <h2 id="planeacion-survey-title">Enlace de Survey123</h2>
          <button type="button" class="btn-icon" data-survey-close aria-label="Cerrar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
          </button>
        </div>
        <div class="modal-body" id="planeacion-survey-body">
          <p class="sticker-loading">Generando enlace…</p>
        </div>
      </div>
    </div>

    <div class="modal" id="planeacion-grupo-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="planeacion-grupo-title">
      <div class="modal-backdrop" data-grupo-close></div>
      <div class="modal-panel sticker-modal-panel">
        <div class="modal-header">
          <h2 id="planeacion-grupo-title">Grupo de inspectores</h2>
          <button type="button" class="btn-icon" data-grupo-close aria-label="Cerrar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <input type="hidden" id="planeacion-grupo-id">
          <label class="sticker-field"><span>Nombre</span>
            <input type="text" id="planeacion-grupo-nombre" placeholder="Grupo Norte">
          </label>
          <fieldset class="asignacion-grupo-miembros">
            <legend>Miembros — máximo ${MAX_MIEMBROS_GRUPO} (cualquiera podrá completar los puntos del grupo)</legend>
            <div id="planeacion-grupo-miembros-list" class="asignacion-grupo-miembros-list"></div>
          </fieldset>
          <p class="sticker-error" id="planeacion-grupo-error" role="alert" hidden></p>
          <div class="sticker-form-actions">
            <button type="button" class="btn-secondary" data-grupo-close>Cancelar</button>
            <button type="button" class="btn-primary" id="planeacion-grupo-save">Guardar</button>
          </div>
        </div>
      </div>
    </div>

    <div class="modal" id="planeacion-vehiculo-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="planeacion-vehiculo-title">
      <div class="modal-backdrop" data-vehiculo-close></div>
      <div class="modal-panel sticker-modal-panel">
        <div class="modal-header">
          <h2 id="planeacion-vehiculo-title">Vehículo</h2>
          <button type="button" class="btn-icon" data-vehiculo-close aria-label="Cerrar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <input type="hidden" id="planeacion-vehiculo-id">
          <label class="sticker-field"><span>Placa</span>
            <input type="text" id="planeacion-vehiculo-placa" placeholder="ABC123">
          </label>
          <label class="sticker-field"><span>Tipo</span>
            <input type="text" id="planeacion-vehiculo-tipo" placeholder="Camioneta">
          </label>
          <label class="sticker-field asignacion-inline-field" id="planeacion-vehiculo-activo-field" hidden>
            <input type="checkbox" id="planeacion-vehiculo-activo">
            <span>Activo</span>
          </label>
          <p class="sticker-error" id="planeacion-vehiculo-error" role="alert" hidden></p>
          <div class="sticker-form-actions">
            <button type="button" class="btn-secondary" data-vehiculo-close>Cancelar</button>
            <button type="button" class="btn-primary" id="planeacion-vehiculo-save">Guardar</button>
          </div>
        </div>
      </div>
    </div>`;
}

function kpiTile(label, value, wide = false) {
  return `<div class="kpi-tile${wide ? ' kpi-tile-wide' : ''}">
    <span class="kpi-label">${escapeHtml(label)}</span>
    <span class="kpi-value">${escapeHtml(String(value))}</span>
  </div>`;
}

/** KPI tiles fed by `resumen`, including the `por_match_via` tally — this is
 *  what makes a silent codigoapp-prefill failure visible (proposal.md
 *  risk 2): if `clave` never appears there, rung 1 is not firing. */
function kpisHtml(resumen) {
  if (!resumen) return '';
  const porMatch = Object.entries(resumen.por_match_via || {})
    .map(([k, v]) => `${k}: ${v}`).join(' · ') || 'sin datos aún';
  return [
    kpiTile('Total', resumen.total ?? 0),
    kpiTile('Levantados', resumen.levantados ?? 0),
    kpiTile('Pendientes', resumen.pendientes ?? 0),
    kpiTile('Prioridad alta', (resumen.por_prioridad || {}).alta ?? 0),
    kpiTile('Coincidencias por vía', porMatch, true),
  ].join('');
}

function filtersHtml(state) {
  const chip = (group, value, label, active) => `<button type="button" class="asignacion-chip${active ? ' is-active' : ''}" data-filter-group="${group}" data-filter-value="${value}">${escapeHtml(label)}</button>`;
  const prioridadChips = [chip('prioridad', '', 'Todas', !state.prioridad), ...PRIORIDADES.map((p) => chip('prioridad', p, PRIORIDAD_LABELS[p], state.prioridad === p))];
  const comunaChips = ['', ...state.comunas].map((c) => chip('comuna', c, c || 'Todas las comunas', (state.comuna || '') === c));
  return `<div class="asignacion-filters-group">${prioridadChips.join('')}</div><div class="asignacion-filters-group">${comunaChips.join('')}</div>`;
}

const SORTABLE = [
  ['direccion', 'Dirección'],
  ['comuna', 'Comuna'],
  ['afectacion', 'Afectación'],
  ['prioridadEfectiva', 'Prioridad'],
  ['estado_asignacion', 'Estado'],
  ['inspectorLabel', 'Inspector'],
];

const PRIORIDAD_PILL_COLOR = { alta: COLORS.status.i2, media: COLORS.status.r2, baja: COLORS.unknown };

function tableHtml(rows, selected) {
  const head = SORTABLE.map(([key, label]) => `<th data-sort-field="${key}"><button type="button" class="th-sort-btn">${escapeHtml(label)}</button></th>`).join('');
  const body = rows.length
    ? rows.map((r) => `<tr>
        <td><input type="checkbox" class="asignacion-check" data-punto-check="${escapeHtml(r.id)}" ${selected.has(r.id) ? 'checked' : ''} ${r.tiene_survey ? 'disabled title="Ya tiene survey; no requiere visita"' : (r.estado_asignacion === 'no_aplica' ? 'disabled title="No aplica"' : (r.cuadrilla_id ? 'disabled title="Ya pertenece a una cuadrilla"' : ''))}></td>
        <td>${escapeHtml(r.direccion || 'Sin dato')}</td>
        <td>${escapeHtml(r.comuna || 'Sin dato')}</td>
        <td>${escapeHtml(r.afectacion || 'Sin dato')}</td>
        <td><span class="eval-pill" style="--eval-pill:${PRIORIDAD_PILL_COLOR[r.prioridadEfectiva] || COLORS.unknown}">${escapeHtml(PRIORIDAD_LABELS[r.prioridadEfectiva] || r.prioridadEfectiva)}${r.prioridad_override ? ' *' : ''}</span></td>
        <td><span class="eval-pill" style="--eval-pill:${MARKER_HEX[colorForPunto(r)]}">${escapeHtml(ESTADO_LABELS[r.estado_asignacion] || r.estado_asignacion)}</span></td>
        <td>${escapeHtml(r.inspectorLabel)}</td>
        <td class="planeacion-row-actions">
          <button type="button" class="sticker-action" data-editar-asignacion="${escapeHtml(r.id)}">Editar</button>
          ${r.estado_asignacion === 'no_aplica'
            ? `<button type="button" class="sticker-action" data-revertir-noaplica="${escapeHtml(r.id)}">Revertir</button>`
            : `<button type="button" class="sticker-action sticker-action-off" data-marcar-noaplica="${escapeHtml(r.id)}">No aplica</button>`}
          ${r.estado_asignacion === 'hecho' ? `<button type="button" class="sticker-action" data-reopen="${escapeHtml(r.id)}">Reabrir</button>` : ''}
          <button type="button" class="sticker-action" data-abrir-survey="${escapeHtml(r.id)}">Survey123</button>
        </td>
      </tr>`).join('')
    : `<tr><td colspan="8" class="sticker-empty">Sin puntos para este filtro.</td></tr>`;
  return `<table><thead><tr><th></th>${head}<th>Acciones</th></tr></thead><tbody>${body}</tbody></table>`;
}

function cuadrillasHtml(cuadrillas, inspectorById) {
  if (!cuadrillas.length) {
    return '<p class="sticker-empty">Todavía no hay cuadrillas. Usar «Auto-agrupar» o crear una manualmente desde la tabla.</p>';
  }
  return `<ul class="sticker-list">
    ${cuadrillas.map((c) => {
      const n = (c.puntos || []).length;
      const insp = c.inspector_uid ? inspectorById.get(c.inspector_uid) : null;
      const inspName = insp ? (insp.nombre_completo || `Brigada ${insp.codigo || '—'}`) : '';
      const metaInsp = insp ? `Inspector: ${escapeHtml(inspName)}` : 'Sin asignar';
      return `<li class="sticker-row asignacion-cuadrilla-row" data-cuadrilla-row="${escapeHtml(c.id)}">
        <span class="sticker-code" title="Origen">${c.origen === 'auto' ? 'AUTO' : 'MAN'}</span>
        <div class="sticker-identity">
          <span class="sticker-name" title="ID: ${escapeHtml(c.id)}">${escapeHtml(c.nombre || c.id)}</span>
          <span class="sticker-meta">${n} punto${n === 1 ? '' : 's'} · ${metaInsp}</span>
        </div>
        <div class="asignacion-combo" data-combo-cuadrilla="${escapeHtml(c.id)}">
          <input type="text" class="asignacion-combo-input" role="combobox" aria-expanded="false"
            aria-autocomplete="list" autocomplete="off" spellcheck="false"
            placeholder="${insp ? 'Cambiar inspector…' : 'Asignar inspector…'}" aria-label="Buscar inspector para asignar"
            value="${escapeHtml(inspName)}">
          <ul class="asignacion-combo-list" role="listbox" hidden></ul>
        </div>
        <div class="asignacion-cuadrilla-actions">
          ${insp ? `<button type="button" class="sticker-action asignacion-desasignar" data-desasignar="${escapeHtml(c.id)}">Quitar asignación</button>` : ''}
          <button type="button" class="sticker-action sticker-action-off asignacion-eliminar" data-eliminar="${escapeHtml(c.id)}">Eliminar</button>
        </div>
      </li>`;
    }).join('')}
  </ul>`;
}

/** Groups of INSPECTORS (people) — NOT to be confused with `cuadrillasHtml`
 *  above, which renders groups of POINTS under one inspector. Member uids
 *  are resolved to display names via the SAME cached roster `inspectorById`
 *  already uses for `inspectorLabel`/cuadrillas, not a backend-side
 *  resolution — no separate roster fetch for this section. Each group's
 *  assigned vehicle comes back embedded on `g.vehiculo` (listGrupos'
 *  own no-second-round-trip contract) — a `<select>` here lets the admin
 *  assign/change/clear it without leaving this list. */
function gruposHtml(grupos, inspectorById, vehiculosDisponibles) {
  if (!grupos.length) {
    return '<p class="sticker-empty">Todavía no hay grupos. Usar «Crear grupo».</p>';
  }
  return `<ul class="sticker-list">
    ${grupos.map((g) => {
      const nombres = (g.miembros || []).map((uid) => {
        const insp = inspectorById.get(uid);
        return insp ? (insp.nombre_completo || insp.codigo || uid) : uid;
      });
      const metaMiembros = nombres.length ? `${nombres.length}/${MAX_MIEMBROS_GRUPO} miembro${nombres.length === 1 ? '' : 's'}: ${escapeHtml(nombres.join(', '))}` : 'Sin miembros';
      const vehiculo = g.vehiculo;
      // Options: the group's OWN current vehicle (even if inactive/not in
      // the "disponibles" list, so it always shows correctly) + every
      // active, currently-unassigned vehicle.
      const opciones = [vehiculo, ...vehiculosDisponibles.filter((v) => !vehiculo || v.id !== vehiculo.id)]
        .filter(Boolean);
      const vehiculoSelect = `<select class="asignacion-vehiculo-select" data-vehiculo-select-grupo="${escapeHtml(g.id)}">
        <option value="">— Sin vehículo —</option>
        ${opciones.map((v) => `<option value="${escapeHtml(v.id)}" ${vehiculo && vehiculo.id === v.id ? 'selected' : ''}>${escapeHtml(v.placa)}${v.tipo ? ` (${escapeHtml(v.tipo)})` : ''}</option>`).join('')}
      </select>`;
      return `<li class="sticker-row" data-grupo-row="${escapeHtml(g.id)}">
        <div class="sticker-identity">
          <span class="sticker-name" title="ID: ${escapeHtml(g.id)}">${escapeHtml(g.nombre || g.id)}${g.activo === false ? ' (inactivo)' : ''}</span>
          <span class="sticker-meta">${metaMiembros}</span>
        </div>
        <label class="sticker-field asignacion-inline-field" title="Vehículo asignado — un vehículo solo puede estar en un grupo a la vez.">
          <span>Vehículo</span>
          ${vehiculoSelect}
        </label>
        <div class="asignacion-cuadrilla-actions">
          <button type="button" class="sticker-action" data-editar-grupo="${escapeHtml(g.id)}">Editar</button>
          <button type="button" class="sticker-action sticker-action-off" data-eliminar-grupo="${escapeHtml(g.id)}">Eliminar</button>
        </div>
      </li>`;
    }).join('')}
  </ul>`;
}

/** One progress row's numbers — `{asignados, hechos, pendientes,
 *  completado_pct}` from `metricasProgreso`'s own `_tally()` shape,
 *  reused identically for the group/inspector/combined tallies. */
function progresoBarraHtml(tally, inline = false) {
  const t = tally || { asignados: 0, hechos: 0, pendientes: 0, completado_pct: 0 };
  const cls = inline ? 'planeacion-progreso-cell planeacion-progreso-cell-inline' : 'planeacion-progreso-cell';
  return `<div class="${cls}">
    <div class="planeacion-progreso-barra" title="${t.hechos}/${t.asignados} hecho(s)">
      <div class="planeacion-progreso-relleno" style="width:${Math.min(100, t.completado_pct)}%"></div>
    </div>
    <span class="planeacion-progreso-pct">${t.completado_pct}% · ${t.hechos}/${t.asignados}</span>
  </div>`;
}

/** `metricasProgreso` change (`puntos-disponibles`, 2026-08-26) — per-group
 *  and per-inspector progress, BOTH campaigns combined and broken out.
 *  Inspector NAMES are resolved here, client-side, via the SAME cached
 *  roster `inspectorById` every other section on this tab already uses
 *  (`metricasProgreso` itself returns raw uids — see that endpoint's own
 *  docstring for why: no second, duplicated roster fetch on the backend). */
export function metricasHtml(metricas, inspectorById) {
  if (!metricas) return '<p class="sticker-empty">Sin datos de progreso todavía.</p>';
  const grupos = Object.entries(metricas.grupos || {});
  const inspectores = Object.entries(metricas.inspectores || {});

  const gruposFilas = grupos.length
    ? grupos.map(([id, g]) => `<tr>
        <td>${escapeHtml(g.nombre || id)}${g.activo === false ? ' (inactivo)' : ''}</td>
        <td>${g.miembros}</td>
        <td>${progresoBarraHtml(g.combinado)}</td>
        <td>${progresoBarraHtml(g.stickers)}</td>
        <td>${progresoBarraHtml(g.survey)}</td>
      </tr>`).join('')
    : '<tr><td colspan="5" class="sticker-empty">Todavía no hay grupos.</td></tr>';

  const inspectoresFilas = inspectores.length
    ? inspectores.map(([uid, i]) => {
      const insp = inspectorById.get(uid);
      const nombre = insp ? (insp.nombre_completo || insp.codigo || uid) : uid;
      return `<tr>
        <td>${escapeHtml(nombre)}</td>
        <td>${escapeHtml((i.grupos || []).join(', ') || '—')}</td>
        <td>${progresoBarraHtml(i.combinado)}</td>
        <td>${progresoBarraHtml(i.stickers)}</td>
        <td>${progresoBarraHtml(i.survey)}</td>
      </tr>`;
    }).join('')
    : '<tr><td colspan="5" class="sticker-empty">Todavía no hay inspectores con puntos o grupo.</td></tr>';

  return `<div class="planeacion-metricas-totales">
      <span>Total: ${progresoBarraHtml(metricas.combinado, true)}</span>
      <span>Stickers: ${progresoBarraHtml(metricas.stickers, true)}</span>
      <span>Survey: ${progresoBarraHtml(metricas.survey, true)}</span>
    </div>
    <h4 class="planeacion-metricas-subtitulo">Por grupo</h4>
    <div class="table-scroll"><table class="sticker-table">
      <thead><tr><th>Grupo</th><th>Miembros</th><th>Combinado</th><th>Stickers</th><th>Survey</th></tr></thead>
      <tbody>${gruposFilas}</tbody>
    </table></div>
    <h4 class="planeacion-metricas-subtitulo">Por inspector</h4>
    <div class="table-scroll"><table class="sticker-table">
      <thead><tr><th>Inspector</th><th>Grupos</th><th>Combinado</th><th>Stickers</th><th>Survey</th></tr></thead>
      <tbody>${inspectoresFilas}</tbody>
    </table></div>`;
}

/** Vehículos (`grupos-inspectores` follow-up, 2026-08-26) — "cada grupo
 *  sale en un vehículo". Independent CRUD list; assignment to a grupo
 *  happens from the grupo row above (`vehiculoSelect`), not here. */
function vehiculosHtml(vehiculos) {
  if (!vehiculos.length) {
    return '<p class="sticker-empty">Todavía no hay vehículos. Usar «Crear vehículo».</p>';
  }
  return `<ul class="sticker-list">
    ${vehiculos.map((v) => `<li class="sticker-row" data-vehiculo-row="${escapeHtml(v.id)}">
        <div class="sticker-identity">
          <span class="sticker-name" title="ID: ${escapeHtml(v.id)}">${escapeHtml(v.placa)}${v.activo === false ? ' (inactivo)' : ''}</span>
          <span class="sticker-meta">${escapeHtml(v.tipo || 'Sin tipo')}</span>
        </div>
        <div class="asignacion-cuadrilla-actions">
          <button type="button" class="sticker-action" data-editar-vehiculo="${escapeHtml(v.id)}">Editar</button>
          <button type="button" class="sticker-action sticker-action-off" data-eliminar-vehiculo="${escapeHtml(v.id)}">Eliminar</button>
        </div>
      </li>`).join('')}
  </ul>`;
}

// Vanilla searchable combobox — same shape as stickers-asignacion.js's own
// mountCombobox (no cap, keyboard nav, mousedown-before-blur selection).
//
// Speed follow-up (2026-08-26): `autoOpen` lets the LAZY mounting below
// (see renderCuadrillasSection) open the dropdown on the exact focus event
// that triggered the mount, without relying on the just-attached 'focus'
// listener re-firing for an event already in flight.
function mountCombobox(comboEl, { inspectores, onSelect, autoOpen = false }) {
  const input = comboEl.querySelector('.asignacion-combo-input');
  const list = comboEl.querySelector('.asignacion-combo-list');
  let options = [];
  let active = -1;

  const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };

  function render(query) {
    const matches = filterInspectores(inspectores, query === undefined ? input.value : query);
    options = matches.map((insp) => ({ uid: insp.uid }));
    list.innerHTML = matches.map((insp) => {
      const name = insp.nombre_completo || `Brigada ${insp.codigo || '—'}`;
      const code = insp.codigo ? ` — ${insp.codigo}` : '';
      return `<li role="option" class="asignacion-combo-option" data-uid="${escapeHtml(insp.uid)}">
        <span class="asignacion-combo-name">${escapeHtml(name + code)}</span></li>`;
    }).join('') || '<li class="asignacion-combo-empty" aria-disabled="true">Sin coincidencias</li>';
    active = -1;
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function highlight(i) {
    const items = [...list.querySelectorAll('.asignacion-combo-option')];
    if (!items.length || i < 0 || i >= options.length) return;
    active = i;
    items.forEach((el, idx) => el.classList.toggle('is-active', idx === active));
    items[active].scrollIntoView({ block: 'nearest' });
  }

  function choose(uid) { close(); onSelect(uid); }

  input.addEventListener('focus', () => { input.select(); render(''); });
  input.addEventListener('input', () => render());
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); if (list.hidden) render(''); highlight(active < 0 ? 0 : active + 1); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); highlight(active < 0 ? options.length - 1 : active - 1); }
    else if (ev.key === 'Enter') { ev.preventDefault(); if (active >= 0 && options[active]) choose(options[active].uid); }
    else if (ev.key === 'Escape') { close(); }
  });
  list.addEventListener('mousedown', (ev) => {
    const li = ev.target.closest('.asignacion-combo-option');
    if (!li) return;
    ev.preventDefault();
    choose(li.dataset.uid);
  });
  input.addEventListener('blur', () => { setTimeout(close, 120); });

  if (autoOpen) { input.select(); render(''); }
}

function popupHtml(row) {
  return `<div class="map-popup">
    <h4>${escapeHtml(row.direccion || 'Sin dirección')}</h4>
    <dl>
      <dt>Estado</dt><dd>${escapeHtml(ESTADO_LABELS[row.estado_asignacion] || row.estado_asignacion)}</dd>
      <dt>Prioridad</dt><dd>${escapeHtml(PRIORIDAD_LABELS[row.prioridadEfectiva] || row.prioridadEfectiva)}</dd>
      <dt>Comuna</dt><dd>${escapeHtml(row.comuna || 'Sin dato')}</dd>
      <dt>Cuadrilla</dt><dd>${escapeHtml(row.cuadrillaLabel)}</dd>
    </dl>
    <label class="sticker-field">
      <span>Reasignar a</span>
      <select data-reasignar-select="${escapeHtml(row.id)}"><option value="">— Elegir inspector —</option></select>
    </label>
  </div>`;
}

// ---- map --------------------------------------------------------------------

let map = null;
let baseTile = null;
let pointsLayer = null;
let legendEl = null;

function teardownMap() {
  if (map) { map.remove(); map = null; }
  baseTile = null;
  pointsLayer = null;
  legendEl = null;
}

// Guarded so the pure-logic self-check can import this module under Node.
if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => {
    if (!map || !baseTile) return;
    map.removeLayer(baseTile);
    baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
    baseTile.bringToBack();
  });
}

function renderMap(rows, inspectores, onReasignar) {
  teardownMap();
  const conCoords = rows.filter((r) => r.coords && Number.isFinite(r.coords.lat) && Number.isFinite(r.coords.lon));

  // preferCanvas: circleMarkers render on a single <canvas> instead of one
  // SVG node each — the decisive perf win when the point set grows (no
  // thousands of DOM nodes to lay out / repaint on pan/zoom).
  map = L.map('planeacion-map', { zoomControl: true, minZoom: 10, maxZoom: 18, preferCanvas: true }).setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
  pointsLayer = L.layerGroup().addTo(map);

  for (const r of conCoords) {
    const marker = L.circleMarker([r.coords.lat, r.coords.lon], {
      radius: 4, color: '#0B1D33', weight: 1, fillColor: MARKER_HEX[r.color], fillOpacity: 0.9,
    });
    marker.bindPopup(popupHtml(r), { maxWidth: 280 });
    marker.on('popupopen', (ev) => {
      const sel = ev.popup.getElement().querySelector('[data-reasignar-select]');
      if (!sel) return;
      inspectores.forEach((i) => {
        const opt = document.createElement('option');
        opt.value = i.uid;
        opt.textContent = i.nombre_completo || `Brigada ${i.codigo || '—'}`;
        if (i.uid === r.inspector_uid) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', async () => {
        if (!sel.value) return;
        sel.disabled = true;
        try { await onReasignar(r.id, sel.value); } finally { sel.disabled = false; }
      });
    });
    marker.addTo(pointsLayer);
  }

  const legend = L.control({ position: 'bottomright' });
  legend.onAdd = () => {
    legendEl = L.DomUtil.create('div', 'map-legend');
    L.DomEvent.disableClickPropagation(legendEl);
    legendEl.innerHTML = `
      <div class="legend-title">Estado del punto</div>
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.green}"></span><span>Levantado</span></div>
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.red}"></span><span>Pendiente · alta</span></div>
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.amber}"></span><span>Pendiente · media/baja</span></div>
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.blue}"></span><span>Asignado / en proceso</span></div>
      <div class="legend-row"><span class="legend-swatch legend-circle" style="background:${MARKER_HEX.grey}"></span><span>No aplica</span></div>`;
    return legendEl;
  };
  legend.addTo(map);

  const inBox = conCoords.filter((r) =>
    r.coords.lat >= CALI_BBOX.latMin && r.coords.lat <= CALI_BBOX.latMax
    && r.coords.lon >= CALI_BBOX.lngMin && r.coords.lon <= CALI_BBOX.lngMax);
  if (inBox.length) {
    map.fitBounds(L.latLngBounds(inBox.map((r) => [r.coords.lat, r.coords.lon])), { padding: [40, 40], maxZoom: 16 });
  } else {
    map.setView(CALI_CENTER, CALI_ZOOM);
  }
  setTimeout(() => { if (map) map.invalidateSize(); }, 80);
  return conCoords.length;
}

// ---- entry point --------------------------------------------------------------

/** initPlaneacion(root, { getToken }) -> { reload }. Re-init on every open
 *  (main.js's switchView() calls this every time the tab opens, matching
 *  the other admin tabs' lifecycle — spec.md "Reopening the tab refreshes
 *  the data"). */
export function initPlaneacion(root, { getToken }) {
  let rows = [];
  let cuadrillas = [];
  let grupos = []; // grupos de INSPECTORES — `grupos-inspectores` change
  let vehiculos = []; // `grupos-inspectores` follow-up (2026-08-26)
  let metricasData = null; // `metricasProgreso` — `puntos-disponibles` change (2026-08-26)
  let resumenData = null;
  let inspectoresCache = [];
  let inspectoresLoaded = false;
  let filters = { prioridad: '', comuna: '' };
  const selected = new Set();
  let busy = false;

  root.innerHTML = shellHtml();
  const mapMeta = root.querySelector('#planeacion-map-meta');
  const okBox = root.querySelector('#planeacion-ok');
  const errBox = root.querySelector('#planeacion-error');
  const kpisEl = root.querySelector('#planeacion-kpis');
  const truncEl = root.querySelector('#planeacion-truncacion');
  const filtersEl = root.querySelector('#planeacion-filters');
  const tableWrap = root.querySelector('#planeacion-table-wrap');
  const cuadrillasWrap = root.querySelector('#planeacion-cuadrillas');
  const autoBtn = root.querySelector('#planeacion-auto');
  const radiusInput = root.querySelector('#planeacion-max-radius');
  const sizeInput = root.querySelector('#planeacion-max-size');
  const crearBtn = root.querySelector('#planeacion-crear');
  const reiniciarBtn = root.querySelector('#planeacion-reiniciar');
  const gruposWrap = root.querySelector('#planeacion-grupos');
  const grupoSelect = root.querySelector('#planeacion-grupo-select');
  const metricasWrap = root.querySelector('#planeacion-metricas');
  const asignarGrupoBtn = root.querySelector('#planeacion-asignar-grupo');
  const quitarGrupoBtn = root.querySelector('#planeacion-quitar-grupo');
  const grupoCrearBtn = root.querySelector('#planeacion-grupo-crear');
  const vehiculosWrap = root.querySelector('#planeacion-vehiculos');
  const vehiculoCrearBtn = root.querySelector('#planeacion-vehiculo-crear');

  // ---- sub-tabs (Puntos / Grupos / Vehículos) — show/hide panels that share
  // the same in-memory state; switching never reloads. The map lives in the
  // Puntos panel and Leaflet cannot measure a hidden container, so re-entering
  // Puntos triggers an invalidateSize() (same fix renderMap already applies
  // on first mount). ----------------------------------------------------------
  function switchSubtab(name) {
    root.querySelectorAll('[data-subtab-btn]').forEach((btn) => {
      const active = btn.dataset.subtabBtn === name;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    root.querySelectorAll('[data-subtab]').forEach((panel) => {
      panel.hidden = panel.dataset.subtab !== name;
    });
    if (name === 'puntos' && map) {
      setTimeout(() => { if (map) map.invalidateSize(); }, 0);
    }
  }
  root.querySelectorAll('[data-subtab-btn]').forEach((btn) => {
    btn.addEventListener('click', () => switchSubtab(btn.dataset.subtabBtn));
  });

  const showOk = (msg) => { okBox.textContent = msg; okBox.hidden = !msg; };
  const showErr = (msg) => { errBox.textContent = msg; errBox.hidden = !msg; };

  function getInspectores() { return inspectoresCache; }

  function inspectorLabelFor(uid) {
    if (!uid) return '—';
    const insp = getInspectores().find((i) => i.uid === uid);
    return insp ? (insp.nombre_completo || insp.codigo || insp.uid) : '—';
  }

  function currentRows() {
    return sortRows(filterRows(rows, filters));
  }

  function comunasFromRows() {
    return [...new Set(rows.map((r) => r.comuna).filter(Boolean))].sort();
  }

  function renderKpis() {
    kpisEl.innerHTML = kpisHtml(resumenData);
  }

  function renderTruncacion(truncado, shown, totalPendientes) {
    const msg = truncado ? formatTruncacion(shown, totalPendientes) : null;
    truncEl.textContent = msg || '';
    truncEl.hidden = !msg;
  }

  function renderTable() {
    filtersEl.innerHTML = filtersHtml({ ...filters, comunas: comunasFromRows() });
    tableWrap.innerHTML = tableHtml(currentRows(), selected);
    wireTable();
  }

  function wireTable() {
    tableWrap.querySelectorAll('[data-sort-field]').forEach((th) => {
      // Priority-first table: header click narrows to that field's dominant
      // sort by re-filtering isn't meaningful here (rows are already priority-
      // ordered); header clicks instead toggle a one-off re-sort by that
      // column using the same asc/desc pattern as the sticker template.
      th.addEventListener('click', () => renderTable());
    });
    tableWrap.querySelectorAll('[data-punto-check]').forEach((cb) => {
      cb.addEventListener('change', () => {
        const id = cb.dataset.puntoCheck;
        if (cb.checked) selected.add(id); else selected.delete(id);
        crearBtn.disabled = selected.size === 0;
        quitarGrupoBtn.disabled = selected.size === 0;
        asignarGrupoBtn.disabled = !(selected.size && grupoSelect.value);
      });
    });
    tableWrap.querySelectorAll('[data-editar-asignacion]').forEach((btn) => {
      btn.addEventListener('click', () => openEditarModal(btn.dataset.editarAsignacion));
    });
    tableWrap.querySelectorAll('[data-marcar-noaplica]').forEach((btn) => {
      btn.addEventListener('click', () => openNoAplicaModal(btn.dataset.marcarNoaplica));
    });
    tableWrap.querySelectorAll('[data-revertir-noaplica]').forEach((btn) => {
      btn.addEventListener('click', () => runPuntoAction(
        { action: 'marcarNoAplica', punto_id: btn.dataset.revertirNoaplica, revertir: true },
        'Punto restaurado a pendiente.',
      ));
    });
    tableWrap.querySelectorAll('[data-reopen]').forEach((btn) => {
      btn.addEventListener('click', () => runPuntoAction(
        { action: 'reopen', punto_id: btn.dataset.reopen },
        'Punto reabierto a pendiente.',
      ));
    });
    tableWrap.querySelectorAll('[data-abrir-survey]').forEach((btn) => {
      btn.addEventListener('click', () => openSurveyModal(btn.dataset.abrirSurvey));
    });
  }

  function renderAll() {
    renderKpis();
    renderTable();
    renderCuadrillasSection();
    renderGruposSection();
    renderVehiculosSection();
    renderMapSection();
  }

  // ---- optimistic local mutation of one point's row, then a targeted re-render
  // (spec.md "A correction updates the view without a full reload") ----------
  function applyPuntoPatch(puntoId, patch) {
    const row = rows.find((r) => r.id === puntoId);
    if (!row) return;
    Object.assign(row, patch);
    row.prioridadEfectiva = prioridadEfectiva(row);
    row.color = colorForPunto(row);
  }

  async function runPuntoAction(body, okMsg) {
    if (busy) return;
    busy = true;
    try {
      const { punto } = await callApi(getToken, body);
      showOk(okMsg);
      applyPuntoPatch(body.punto_id, {
        estado_asignacion: punto.estado_asignacion,
        prioridad_override: punto.prioridad_override ?? null,
        notas: punto.notas ?? null,
        motivo_exclusion: punto.motivo_exclusion ?? null,
      });
      renderAll();
    } catch (err) {
      showErr(err.message);
    } finally {
      busy = false;
    }
  }

  // ---- Editar asignación modal (estado, prioridad_override, notas) --------
  const editarModal = root.querySelector('#planeacion-editar-modal');
  const editarErr = root.querySelector('#planeacion-editar-error');
  function openEditarModal(puntoId) {
    const row = rows.find((r) => r.id === puntoId);
    if (!row) return;
    editarErr.hidden = true;
    root.querySelector('#planeacion-editar-punto-id').value = puntoId;
    root.querySelector('#planeacion-editar-estado').value = '';
    root.querySelector('#planeacion-editar-prioridad').value = row.prioridad_override || '';
    root.querySelector('#planeacion-editar-notas').value = row.notas || '';
    editarModal.classList.add('is-open');
    editarModal.setAttribute('aria-hidden', 'false');
  }
  function closeEditarModal() {
    editarModal.classList.remove('is-open');
    editarModal.setAttribute('aria-hidden', 'true');
  }
  editarModal.querySelectorAll('[data-editar-close]').forEach((el) => el.addEventListener('click', closeEditarModal));
  root.querySelector('#planeacion-editar-save').addEventListener('click', async () => {
    if (busy) return;
    const puntoId = root.querySelector('#planeacion-editar-punto-id').value;
    const estado = root.querySelector('#planeacion-editar-estado').value;
    const prioridad = root.querySelector('#planeacion-editar-prioridad').value;
    const notas = root.querySelector('#planeacion-editar-notas').value;
    const body = { action: 'editarAsignacion', punto_id: puntoId };
    if (estado) body.estado_asignacion = estado;
    if (prioridad) body.prioridad_override = prioridad;
    body.notas = notas || null; // explicit null clears — spec.md "An explicit null clears a field"
    busy = true;
    try {
      const { punto } = await callApi(getToken, body);
      showOk('Asignación corregida.');
      applyPuntoPatch(puntoId, {
        estado_asignacion: punto.estado_asignacion,
        prioridad_override: punto.prioridad_override ?? null,
        notas: punto.notas ?? null,
      });
      closeEditarModal();
      renderAll();
    } catch (err) {
      editarErr.textContent = err.message;
      editarErr.hidden = false;
    } finally {
      busy = false;
    }
  });

  // ---- No aplica modal (mandatory reason) ----------------------------------
  const noAplicaModal = root.querySelector('#planeacion-noaplica-modal');
  const noAplicaErr = root.querySelector('#planeacion-noaplica-error');
  function openNoAplicaModal(puntoId) {
    noAplicaErr.hidden = true;
    root.querySelector('#planeacion-noaplica-punto-id').value = puntoId;
    root.querySelector('#planeacion-noaplica-motivo').value = '';
    noAplicaModal.classList.add('is-open');
    noAplicaModal.setAttribute('aria-hidden', 'false');
  }
  function closeNoAplicaModal() {
    noAplicaModal.classList.remove('is-open');
    noAplicaModal.setAttribute('aria-hidden', 'true');
  }
  noAplicaModal.querySelectorAll('[data-noaplica-close]').forEach((el) => el.addEventListener('click', closeNoAplicaModal));
  root.querySelector('#planeacion-noaplica-save').addEventListener('click', async () => {
    if (busy) return;
    const puntoId = root.querySelector('#planeacion-noaplica-punto-id').value;
    const motivo = root.querySelector('#planeacion-noaplica-motivo').value.trim();
    if (!motivo) {
      noAplicaErr.textContent = 'El motivo es obligatorio.';
      noAplicaErr.hidden = false;
      return;
    }
    busy = true;
    try {
      const { punto } = await callApi(getToken, { action: 'marcarNoAplica', punto_id: puntoId, motivo_exclusion: motivo });
      showOk('Punto excluido del pool.');
      applyPuntoPatch(puntoId, { estado_asignacion: punto.estado_asignacion, motivo_exclusion: punto.motivo_exclusion });
      closeNoAplicaModal();
      renderAll();
    } catch (err) {
      noAplicaErr.textContent = err.message;
      noAplicaErr.hidden = false;
    } finally {
      busy = false;
    }
  });

  // ---- Survey123 link modal — the centerpiece: getEnlaceSurvey, open/copy,
  // both the web link and the field-app deep link when present, and the 503
  // "unconfigured" message surfaced plainly, never swallowed. -------------
  const surveyModal = root.querySelector('#planeacion-survey-modal');
  const surveyBody = root.querySelector('#planeacion-survey-body');
  function closeSurveyModal() {
    surveyModal.classList.remove('is-open');
    surveyModal.setAttribute('aria-hidden', 'true');
  }
  surveyModal.querySelectorAll('[data-survey-close]').forEach((el) => el.addEventListener('click', closeSurveyModal));
  async function openSurveyModal(puntoId) {
    surveyBody.innerHTML = '<p class="sticker-loading">Generando enlace…</p>';
    surveyModal.classList.add('is-open');
    surveyModal.setAttribute('aria-hidden', 'false');
    try {
      const { clave, web, app } = await callApi(getToken, { action: 'getEnlaceSurvey', punto_id: puntoId });
      surveyBody.innerHTML = `
        <p class="sticker-note">Clave: <code>${escapeHtml(clave)}</code></p>
        <div class="sticker-form-actions planeacion-survey-links">
          <a class="btn-primary" href="${escapeHtml(web)}" target="_blank" rel="noopener">Abrir en el navegador</a>
          <button type="button" class="btn-secondary" data-copy-link="${escapeHtml(web)}">Copiar enlace web</button>
        </div>
        ${app ? `<div class="sticker-form-actions planeacion-survey-links">
          <a class="btn-primary" href="${escapeHtml(app)}">Abrir en la app Survey123</a>
          <button type="button" class="btn-secondary" data-copy-link="${escapeHtml(app)}">Copiar enlace app</button>
        </div>` : '<p class="sticker-note">Sin enlace de app configurado (SURVEY123_FIELD_APP_ITEM_ID no está configurado).</p>'}`;
      surveyBody.querySelectorAll('[data-copy-link]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          try {
            await navigator.clipboard.writeText(btn.dataset.copyLink);
            showOk('Enlace copiado.');
          } catch {
            /* clipboard permission denied — the link is still visible/selectable */
          }
        });
      });
    } catch (err) {
      // Fail loud, never swallowed (binding constraint #3) — including the
      // 503 "SURVEY123_FORM_URL no está configurado" case.
      surveyBody.innerHTML = `<p class="sticker-error" role="alert">${escapeHtml(err.message)}</p>`;
    }
  }

  // ---- cuadrillas section ---------------------------------------------------
  async function runCuadrillaAction(body, okMsg) {
    if (busy) return;
    busy = true;
    try {
      await callApi(getToken, body);
      showOk(okMsg);
      await reload();
    } catch (err) {
      showErr(err.message);
    } finally {
      busy = false;
    }
  }

  function renderCuadrillasSection() {
    const inspectores = getInspectores();
    const seleccionables = inspectores.filter(isHabilitado);
    const inspectorById = new Map(inspectores.map((i) => [i.uid, i]));
    const cuadrillaById = new Map(cuadrillas.map((c) => [c.id, c]));
    cuadrillasWrap.innerHTML = cuadrillasHtml(cuadrillas, inspectorById);

    // Speed follow-up (2026-08-26): a production board can carry hundreds
    // of cuadrilla rows, and eagerly mounting a full combobox (5 listeners
    // + closures each) for every one of them on every render was pure
    // wasted work for the rows an admin never touches. Mount lazily, on
    // the row's OWN first focus — a plain input has zero combobox cost
    // until it is actually used, and the visible behaviour is unchanged
    // (autoOpen replays the exact "open on focus" effect the eager version
    // already had, on the same focus event that triggered the mount).
    cuadrillasWrap.querySelectorAll('[data-combo-cuadrilla]').forEach((comboEl) => {
      const cuadrillaId = comboEl.dataset.comboCuadrilla;
      const input = comboEl.querySelector('.asignacion-combo-input');
      const mount = () => {
        input.removeEventListener('focus', mount);
        mountCombobox(comboEl, {
          inspectores: seleccionables,
          onSelect: (uid) => runCuadrillaAction(
            { action: 'asignarInspector', cuadrilla_id: cuadrillaId, inspector_uid: uid },
            'Inspector asignado.',
          ),
          autoOpen: true,
        });
      };
      input.addEventListener('focus', mount, { once: true });
    });

    cuadrillasWrap.querySelectorAll('[data-desasignar]').forEach((btn) => {
      btn.addEventListener('click', () => runCuadrillaAction(
        { action: 'desasignarInspector', cuadrilla_id: btn.dataset.desasignar },
        'Asignación retirada; los puntos vuelven a pendiente.',
      ));
    });

    cuadrillasWrap.querySelectorAll('[data-eliminar]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (!window.confirm('Eliminar esta cuadrilla y liberar sus puntos a pendiente. ¿Continuar?')) return;
        runCuadrillaAction(
          { action: 'eliminarCuadrilla', cuadrilla_id: btn.dataset.eliminar },
          'Cuadrilla eliminada.',
        );
      });
    });
  }

  // ---- grupos de inspectores section (`grupos-inspectores` change) --------
  // NOT the cuadrillas section above (groups of POINTS under one inspector)
  // — this is groups of PEOPLE, shared by both campaigns. Same "full reload
  // after a toolbar/per-item mutation" convention `runCuadrillaAction` uses.
  async function runGrupoAction(body, okMsg) {
    if (busy) return;
    busy = true;
    try {
      await callApi(getToken, body);
      showOk(okMsg);
      await reload();
    } catch (err) {
      showErr(err.message);
    } finally {
      busy = false;
    }
  }

  function renderGruposSection() {
    const inspectores = getInspectores();
    const inspectorById = new Map(inspectores.map((i) => [i.uid, i]));
    // Options for the per-row vehicle picker: active vehicles not already
    // holding a DIFFERENT group (the row's own gruposHtml logic also
    // always includes the row's own current vehicle regardless).
    const asignadosAOtroGrupo = new Set(
      grupos.filter((g) => g.vehiculo).map((g) => g.vehiculo.id),
    );
    const vehiculosDisponibles = vehiculos.filter((v) => v.activo !== false && !asignadosAOtroGrupo.has(v.id));
    gruposWrap.innerHTML = gruposHtml(grupos, inspectorById, vehiculosDisponibles);

    gruposWrap.querySelectorAll('[data-editar-grupo]').forEach((btn) => {
      btn.addEventListener('click', () => openGrupoModal(btn.dataset.editarGrupo));
    });
    gruposWrap.querySelectorAll('[data-eliminar-grupo]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (!window.confirm('Eliminar este grupo. Si todavía tiene puntos asignados, la eliminación será rechazada.')) return;
        runGrupoAction({ action: 'eliminarGrupo', grupo_id: btn.dataset.eliminarGrupo }, 'Grupo eliminado.');
      });
    });
    gruposWrap.querySelectorAll('[data-vehiculo-select-grupo]').forEach((sel) => {
      sel.addEventListener('change', () => {
        const grupoId = sel.dataset.vehiculoSelectGrupo;
        if (sel.value) {
          runGrupoAction({ action: 'asignarVehiculoAGrupo', grupo_id: grupoId, vehiculo_id: sel.value }, 'Vehículo asignado al grupo.');
        } else {
          runGrupoAction({ action: 'desasignarVehiculo', grupo_id: grupoId }, 'Vehículo desasignado del grupo.');
        }
      });
    });

    const activos = grupos.filter((g) => g.activo !== false);
    const previoSeleccionado = grupoSelect.value;
    grupoSelect.innerHTML = '<option value="">— Elegir grupo —</option>'
      + activos.map((g) => `<option value="${escapeHtml(g.id)}">${escapeHtml(g.nombre || g.id)}</option>`).join('');
    if (activos.some((g) => g.id === previoSeleccionado)) grupoSelect.value = previoSeleccionado;
    asignarGrupoBtn.disabled = !(selected.size && grupoSelect.value);
    quitarGrupoBtn.disabled = selected.size === 0;
  }

  // ---- vehículos section (`grupos-inspectores` follow-up, 2026-08-26) -----
  async function runVehiculoAction(body, okMsg) {
    if (busy) return;
    busy = true;
    try {
      await callApi(getToken, body);
      showOk(okMsg);
      await reload();
    } catch (err) {
      showErr(err.message);
    } finally {
      busy = false;
    }
  }

  function renderVehiculosSection() {
    vehiculosWrap.innerHTML = vehiculosHtml(vehiculos);
    vehiculosWrap.querySelectorAll('[data-editar-vehiculo]').forEach((btn) => {
      btn.addEventListener('click', () => openVehiculoModal(btn.dataset.editarVehiculo));
    });
    vehiculosWrap.querySelectorAll('[data-eliminar-vehiculo]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (!window.confirm('Eliminar este vehículo. Si todavía está asignado a un grupo, la eliminación será rechazada.')) return;
        runVehiculoAction({ action: 'eliminarVehiculo', vehiculo_id: btn.dataset.eliminarVehiculo }, 'Vehículo eliminado.');
      });
    });
  }

  // ---- progreso section (`metricasProgreso`, `puntos-disponibles` change,
  // 2026-08-26) — read-only, no actions of its own; just renders whatever
  // `reload()` fetched into `metricasData`. Inspector names resolved via
  // the SAME cached roster every other section already uses.
  function renderMetricasSection() {
    const inspectorById = new Map(getInspectores().map((i) => [i.uid, i]));
    metricasWrap.innerHTML = metricasHtml(metricasData, inspectorById);
  }

  const vehiculoModal = root.querySelector('#planeacion-vehiculo-modal');
  const vehiculoErr = root.querySelector('#planeacion-vehiculo-error');
  const vehiculoActivoField = root.querySelector('#planeacion-vehiculo-activo-field');
  function openVehiculoModal(vehiculoId) {
    vehiculoErr.hidden = true;
    const vehiculo = vehiculoId ? vehiculos.find((v) => v.id === vehiculoId) : null;
    root.querySelector('#planeacion-vehiculo-id').value = vehiculoId || '';
    root.querySelector('#planeacion-vehiculo-placa').value = vehiculo ? (vehiculo.placa || '') : '';
    root.querySelector('#planeacion-vehiculo-tipo').value = vehiculo ? (vehiculo.tipo || '') : '';
    root.querySelector('#planeacion-vehiculo-activo').checked = vehiculo ? vehiculo.activo !== false : true;
    vehiculoActivoField.hidden = !vehiculoId; // "activo" only makes sense once a vehículo exists
    vehiculoModal.classList.add('is-open');
    vehiculoModal.setAttribute('aria-hidden', 'false');
  }
  function closeVehiculoModal() {
    vehiculoModal.classList.remove('is-open');
    vehiculoModal.setAttribute('aria-hidden', 'true');
  }
  vehiculoModal.querySelectorAll('[data-vehiculo-close]').forEach((el) => el.addEventListener('click', closeVehiculoModal));
  vehiculoCrearBtn.addEventListener('click', () => openVehiculoModal(null));

  root.querySelector('#planeacion-vehiculo-save').addEventListener('click', async () => {
    if (busy) return;
    const vehiculoId = root.querySelector('#planeacion-vehiculo-id').value;
    const placa = root.querySelector('#planeacion-vehiculo-placa').value.trim();
    const tipo = root.querySelector('#planeacion-vehiculo-tipo').value.trim();
    if (!placa) {
      vehiculoErr.textContent = 'La placa es obligatoria.';
      vehiculoErr.hidden = false;
      return;
    }
    busy = true;
    try {
      if (vehiculoId) {
        const activo = root.querySelector('#planeacion-vehiculo-activo').checked;
        await callApi(getToken, { action: 'editarVehiculo', vehiculo_id: vehiculoId, placa, tipo, activo });
        showOk('Vehículo actualizado.');
      } else {
        await callApi(getToken, { action: 'crearVehiculo', placa, tipo });
        showOk('Vehículo creado.');
      }
      closeVehiculoModal();
      await reload();
    } catch (err) {
      vehiculoErr.textContent = err.message;
      vehiculoErr.hidden = false;
    } finally {
      busy = false;
    }
  });

  // ---- Grupo (create/edit) modal — a checkbox list over the SAME cached
  // roster the cuadrillas combobox already uses (design constraint: reuse
  // the existing inspector roster, no separate member picker data source).
  const grupoModal = root.querySelector('#planeacion-grupo-modal');
  const grupoErr = root.querySelector('#planeacion-grupo-error');
  const grupoMiembrosList = root.querySelector('#planeacion-grupo-miembros-list');
  let grupoOriginalMiembros = [];

  function openGrupoModal(grupoId) {
    grupoErr.hidden = true;
    const grupo = grupoId ? grupos.find((g) => g.id === grupoId) : null;
    grupoOriginalMiembros = grupo ? [...(grupo.miembros || [])] : [];
    root.querySelector('#planeacion-grupo-id').value = grupoId || '';
    root.querySelector('#planeacion-grupo-nombre').value = grupo ? (grupo.nombre || '') : '';
    const miembrosSet = new Set(grupoOriginalMiembros);
    const inspectores = getInspectores().filter(isHabilitado);
    grupoMiembrosList.innerHTML = inspectores.length
      ? inspectores.map((i) => `
        <label class="asignacion-grupo-miembro">
          <input type="checkbox" value="${escapeHtml(i.uid)}" ${miembrosSet.has(i.uid) ? 'checked' : ''}>
          <span>${escapeHtml(i.nombre_completo || i.codigo || i.uid)}</span>
        </label>`).join('')
      : '<p class="sticker-empty">Sin inspectores habilitados.</p>';
    grupoModal.classList.add('is-open');
    grupoModal.setAttribute('aria-hidden', 'false');
  }
  function closeGrupoModal() {
    grupoModal.classList.remove('is-open');
    grupoModal.setAttribute('aria-hidden', 'true');
  }
  grupoModal.querySelectorAll('[data-grupo-close]').forEach((el) => el.addEventListener('click', closeGrupoModal));
  grupoCrearBtn.addEventListener('click', () => openGrupoModal(null));

  // UI-side hint ONLY, mirroring the backend's own MAX_MIEMBROS_GRUPO — the
  // endpoint is the real boundary (see that constant's own comment). Once
  // the cap is reached, further UNCHECKED boxes are disabled so the admin
  // gets immediate feedback instead of a round trip that always fails.
  grupoMiembrosList.addEventListener('change', () => {
    const boxes = [...grupoMiembrosList.querySelectorAll('input[type="checkbox"]')];
    const checkedCount = boxes.filter((cb) => cb.checked).length;
    boxes.forEach((cb) => { cb.disabled = !cb.checked && checkedCount >= MAX_MIEMBROS_GRUPO; });
    grupoErr.hidden = checkedCount <= MAX_MIEMBROS_GRUPO;
    if (checkedCount > MAX_MIEMBROS_GRUPO) {
      grupoErr.textContent = `Un grupo admite máximo ${MAX_MIEMBROS_GRUPO} miembros.`;
    }
  });

  root.querySelector('#planeacion-grupo-save').addEventListener('click', async () => {
    if (busy) return;
    const grupoId = root.querySelector('#planeacion-grupo-id').value;
    const nombre = root.querySelector('#planeacion-grupo-nombre').value.trim();
    const checked = [...grupoMiembrosList.querySelectorAll('input[type="checkbox"]:checked')].map((cb) => cb.value);
    if (!nombre) {
      grupoErr.textContent = 'El nombre es obligatorio.';
      grupoErr.hidden = false;
      return;
    }
    if (!checked.length) {
      grupoErr.textContent = 'Elegir al menos un miembro.';
      grupoErr.hidden = false;
      return;
    }
    busy = true;
    try {
      if (grupoId) {
        const original = new Set(grupoOriginalMiembros);
        const next = new Set(checked);
        const add = checked.filter((uid) => !original.has(uid));
        const remove = grupoOriginalMiembros.filter((uid) => !next.has(uid));
        await callApi(getToken, { action: 'editarGrupo', grupo_id: grupoId, nombre, add, remove });
        showOk('Grupo actualizado.');
      } else {
        await callApi(getToken, { action: 'crearGrupo', nombre, miembros: checked });
        showOk('Grupo creado.');
      }
      closeGrupoModal();
      await reload();
    } catch (err) {
      grupoErr.textContent = err.message;
      grupoErr.hidden = false;
    } finally {
      busy = false;
    }
  });

  // ---- assign/unassign the selected group to/from the currently checked
  // table rows (Paso 1's own selection, shared with «Crear cuadrilla») ------
  grupoSelect.addEventListener('change', () => {
    asignarGrupoBtn.disabled = !(selected.size && grupoSelect.value);
  });
  asignarGrupoBtn.addEventListener('click', async () => {
    if (busy || !selected.size || !grupoSelect.value) return;
    const puntos = [...selected];
    await runGrupoAction(
      { action: 'asignarGrupoAPuntos', grupo_id: grupoSelect.value, puntos },
      `Grupo asignado a ${puntos.length} punto${puntos.length === 1 ? '' : 's'}.`,
    );
  });
  quitarGrupoBtn.addEventListener('click', async () => {
    if (busy || !selected.size) return;
    const puntos = [...selected];
    await runGrupoAction(
      { action: 'desasignarGrupo', puntos },
      `Grupo quitado de ${puntos.length} punto${puntos.length === 1 ? '' : 's'}.`,
    );
  });

  async function reasignar(puntoId, nuevoInspectorUid) {
    try {
      await callApi(getToken, { action: 'reasignarPunto', punto_id: puntoId, nuevo_inspector_uid: nuevoInspectorUid });
      showOk('Punto reasignado.');
      applyPuntoPatch(puntoId, { inspector_uid: nuevoInspectorUid });
      const row = rows.find((r) => r.id === puntoId);
      if (row) row.inspectorLabel = inspectorLabelFor(nuevoInspectorUid);
      renderAll();
    } catch (err) {
      showErr(err.message);
    }
  }

  function renderMapSection() {
    const inspectores = getInspectores().filter(isHabilitado);
    const n = renderMap(currentRows(), inspectores, reasignar);
    const sinCoords = rows.length - n;
    mapMeta.textContent = sinCoords ? `${n} en el mapa · ${sinCoords} sin coordenadas` : `${n} en el mapa`;
  }

  // ---- filter chip wiring (delegated once; re-attached on every renderTable) --
  filtersEl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-filter-group]');
    if (!btn) return;
    filters = { ...filters, [btn.dataset.filterGroup]: btn.dataset.filterValue };
    renderTable();
    renderMapSection();
  });

  // ---- roster: fetched ONCE per init from Stickers' own endpoint, cached for
  // the session (design.md ADR-10 — Planeación is top-level, nothing else has
  // loaded it yet). Binding constraint: SAME roster as Stickers, no separate
  // professionals collection. ------------------------------------------------
  async function ensureInspectores() {
    if (inspectoresLoaded) return;
    const { inspectores } = await callStickersApi(getToken, { action: 'list' });
    inspectoresCache = inspectores || [];
    inspectoresLoaded = true;
  }

  async function reload() {
    showOk('');
    showErr('');
    tableWrap.innerHTML = '<p class="sticker-loading">Cargando planeación…</p>';
    try {
      await ensureInspectores();
      const incluirLevantados = !!root.querySelector('#planeacion-incluir-levantados')?.checked;
      const [listResp, cuadrillasResp, resumenResp, gruposResp, vehiculosResp, metricasResp] = await Promise.all([
        callApi(getToken, { action: 'listPuntos', incluirLevantados }),
        callApi(getToken, { action: 'listCuadrillas' }),
        callApi(getToken, { action: 'resumen' }),
        callApi(getToken, { action: 'listGrupos' }),
        callApi(getToken, { action: 'listVehiculos' }),
        callApi(getToken, { action: 'metricasProgreso' }),
      ]);
      cuadrillas = cuadrillasResp.cuadrillas || [];
      grupos = gruposResp.grupos || [];
      vehiculos = vehiculosResp.vehiculos || [];
      metricasData = metricasResp.metricas || null;
      resumenData = resumenResp.resumen || null;
      rows = buildRows(listResp.puntos, cuadrillas, getInspectores());
      selected.clear();
      crearBtn.disabled = true;
      asignarGrupoBtn.disabled = true;
      quitarGrupoBtn.disabled = true;
      renderTruncacion(!!listResp.truncado, (listResp.puntos || []).length, resumenData ? resumenData.pendientes : (listResp.puntos || []).length);
      renderKpis();
      renderTable();
      renderCuadrillasSection();
      renderGruposSection();
      renderVehiculosSection();
      renderMetricasSection();
      renderMapSection();
    } catch (err) {
      teardownMap();
      tableWrap.innerHTML = `<p class="sticker-error" role="alert">${escapeHtml(err.message)}</p>`;
    }
  }

  autoBtn.addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    autoBtn.disabled = true;
    const originalLabel = autoBtn.innerHTML;
    autoBtn.innerHTML = '<span class="asignacion-spinner" aria-hidden="true"></span>Agrupando…';
    try {
      const body = { action: 'autoAgrupar' };
      if (radiusInput.value) body.maxRadiusM = Number(radiusInput.value);
      if (sizeInput.value) body.maxSize = Number(sizeInput.value);
      const { cuadrillas: nuevas } = await callApi(getToken, body);
      showOk(nuevas.length ? `${nuevas.length} cuadrilla${nuevas.length === 1 ? '' : 's'} nueva${nuevas.length === 1 ? '' : 's'} creada${nuevas.length === 1 ? '' : 's'}.` : 'No había puntos pendientes para agrupar.');
      await reload();
    } catch (err) {
      showErr(err.message);
    } finally {
      busy = false;
      autoBtn.disabled = false;
      autoBtn.innerHTML = originalLabel;
    }
  });

  reiniciarBtn.addEventListener('click', async () => {
    if (busy) return;
    if (!window.confirm('Esto borra las cuadrillas automáticas y libera sus puntos a pendiente. Las cuadrillas manuales se conservan. ¿Continuar?')) return;
    busy = true;
    reiniciarBtn.disabled = true;
    try {
      const { eliminadas, puntosLiberados } = await callApi(getToken, { action: 'reiniciarAgrupacion' });
      showOk(eliminadas
        ? `${eliminadas} cuadrilla${eliminadas === 1 ? '' : 's'} automática${eliminadas === 1 ? '' : 's'} eliminada${eliminadas === 1 ? '' : 's'}; ${puntosLiberados} punto${puntosLiberados === 1 ? '' : 's'} liberado${puntosLiberados === 1 ? '' : 's'} a pendiente.`
        : 'No había cuadrillas automáticas para reiniciar.');
      await reload();
    } catch (err) {
      showErr(err.message);
    } finally {
      busy = false;
      reiniciarBtn.disabled = false;
    }
  });

  crearBtn.addEventListener('click', async () => {
    if (busy || selected.size === 0) return;
    const nombre = window.prompt('Nombre de la cuadrilla:', '');
    if (nombre === null) return;
    busy = true;
    crearBtn.disabled = true;
    try {
      await callApi(getToken, { action: 'crearCuadrilla', nombre: nombre.trim(), puntos: [...selected] });
      showOk('Cuadrilla creada.');
      selected.clear();
      await reload();
    } catch (err) {
      showErr(err.message);
    } finally {
      busy = false;
    }
  });

  // Widening the set to already-surveyed points changes the Firestore query
  // itself, so it needs a real round trip — unlike the client-side estado
  // filter, which just re-renders `rows`.
  root.querySelector('#planeacion-incluir-levantados')
    ?.addEventListener('change', () => { reload(); });

  reload();
  return { reload };
}
