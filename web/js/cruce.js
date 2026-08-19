// Gestión view: live cruce críticos↔survey backed by Firestore (dagma-85aad).
// Reads the `cruce_criticos_survey` collection in real time (public read); the
// PMU team (Firebase Auth email/password + custom claim `pmu`) can edit the
// gestión fields inline. The cruce itself is produced server-side by
// integracion_F1/cruce_criticos_survey.py --firebase. Exposes the same three
// entry points the old Gestión view did, so main.js wiring is unchanged.
import { COLORS, escapeHtml, interpolateRamp, basemapTileUrl, formatTs } from './utils.js';

// Public Firebase web config (safe in the browser; NOT the admin service account).
const FIREBASE_CONFIG = {
  apiKey: 'AIzaSyAVVewMgunLWBiZz5XU-GjrzbO3ZKcyvD0',
  authDomain: 'dagma-85aad.firebaseapp.com',
  projectId: 'dagma-85aad',
  storageBucket: 'dagma-85aad.firebasestorage.app',
  messagingSenderId: '716440297451',
  appId: '1:716440297451:web:6971b2bb4118f7ea3cc3ae',
};
const COLLECTION = 'cruce_criticos_survey';
const DESPACHOS_COLLECTION = 'despachos';
const LIDERES_COLLECTION = 'lideres';
const SDK = 'https://www.gstatic.com/firebasejs/10.12.2';

const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
const ZONES_URL = 'data/zonas_asignacion.geojson';
const PENDING_COLOR = COLORS.status.r2;   // rojo — falta levantamiento
const DONE_COLOR = COLORS.status.h;       // verde — levantado en campo

// Gestión workflow. `completado` es AUTOMÁTICO: un punto levantado (EDE hecha) se
// muestra siempre como Completado, sin importar su estado manual. El resto es el
// flujo operativo manual antes del levantamiento.
const ESTADOS = ['sin_asignar', 'asignado', 'en_campo'];   // override manual por punto
const ESTADO_LABEL = { sin_asignar: 'Pendiente', asignado: 'Asignado', en_campo: 'En campo', completado: 'Completado' };

/** Despacho enganchado a ESTE punto (gestion_despacho_id). La asignación es
 *  PUNTO A PUNTO: cada asignación cubre un solo punto; el despacho es solo el
 *  registro de la cuadrilla, no asigna nada por sí mismo. */
function puntoDespacho(r) {
  if (!r.gestion_despacho_id) return null;
  return despachos.find((d) => d.id === r.gestion_despacho_id) || null;
}

/** Estado efectivo: levantado ⇒ Completado (auto); si no, el estado del punto. */
function effectiveEstado(r) {
  if (r.estado === 'levantado') return 'completado';
  return r.gestion_estado || 'sin_asignar';
}

/** Responsable del punto: su asignación individual (o el líder de su despacho). */
function asignadoDe(r) {
  return r.gestion_asignado_a || (puntoDespacho(r)?.lider ?? '');
}

let map = null;
let baseTile = null;
let zonesLayer = null;
let pointsLayer = null;
let legendEl = null;
let zonesGeo = null;
let records = [];          // live docs: [{ id, ...data }]
let initialized = false;

let fb = null;             // firebase handles, loaded lazily
let user = null;           // firebase Auth user or null
let canEdit = false;       // user has the pmu custom claim
let unsub = null;          // onSnapshot unsubscribe (cruce)
let unsubDespachos = null; // onSnapshot unsubscribe (despachos)
let despachos = [];        // live despachos: [{ id, ...data }]
let despachoEditId = null; // id del despacho en edición (null = crear)
let soloPendientes = false;// filtro rápido de tabla: solo pendientes sin asignar
let filtroLider = null;    // filtro: mostrar solo los puntos de una persona (líder)
let cruceMeta = null;      // _meta/resumen: fecha de CORRIDA del pipeline
let lideres = [];          // directorio de líderes: [{ id, nombre, telefono }]

const el = (sel) => document.querySelector(sel);

// ── Firebase (ESM CDN, cargado solo al abrir la tab) ─────────────────────────
async function loadFirebase() {
  if (fb) return fb;
  const [appMod, authMod, fsMod] = await Promise.all([
    import(`${SDK}/firebase-app.js`),
    import(`${SDK}/firebase-auth.js`),
    import(`${SDK}/firebase-firestore.js`),
  ]);
  const app = appMod.initializeApp(FIREBASE_CONFIG);
  fb = {
    auth: authMod.getAuth(app),
    db: fsMod.getFirestore(app),
    onAuthStateChanged: authMod.onAuthStateChanged,
    signIn: authMod.signInWithEmailAndPassword,
    signOutFn: authMod.signOut,
    collection: fsMod.collection,
    onSnapshot: fsMod.onSnapshot,
    doc: fsMod.doc,
    updateDoc: fsMod.updateDoc,
    addDoc: fsMod.addDoc,
    setDoc: fsMod.setDoc,
    deleteDoc: fsMod.deleteDoc,
    writeBatch: fsMod.writeBatch,
    serverTimestamp: fsMod.serverTimestamp,
  };
  return fb;
}

// ── Login bar ────────────────────────────────────────────────────────────────
function renderAuthBar() {
  const bar = el('#gestion-authbar');
  if (!bar) return;
  if (user) {
    const role = canEdit ? '<span class="asig-badge asig-badge-done">puede editar</span>'
                         : '<span class="asig-badge asig-badge-pending">solo lectura</span>';
    bar.innerHTML = `
      <span class="gestion-user">${escapeHtml(user.email || 'sesión')} ${role}</span>
      <button type="button" class="btn-secondary" id="gestion-signout">Salir</button>`;
    el('#gestion-signout').addEventListener('click', () => fb.signOutFn(fb.auth));
    return;
  }
  bar.innerHTML = `
    <form class="gestion-login" id="gestion-login">
      <input type="email" id="gestion-email" placeholder="correo del equipo" autocomplete="username" required>
      <input type="password" id="gestion-pass" placeholder="clave" autocomplete="current-password" required>
      <button type="submit" class="btn-primary">Ingresar</button>
      <span class="asig-error" id="gestion-login-err" hidden></span>
    </form>`;
  el('#gestion-login').addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = el('#gestion-login-err');
    err.hidden = true;
    try {
      await fb.signIn(fb.auth, el('#gestion-email').value.trim(), el('#gestion-pass').value);
    } catch (ex) {
      err.textContent = 'No se pudo ingresar. Revisá correo y clave.';
      err.hidden = false;
      console.error(ex);
    }
  });
}

// ── Map ──────────────────────────────────────────────────────────────────────
// Coropleta de avance por polígono: rojo (0% levantado) → amarillo → verde (100%).
const PROGRESS_RAMP = ['#dc2626', '#eab308', '#22c55e'];

/** zona_id -> { total, done } contando puntos críticos por zona. */
function zoneProgress() {
  const m = {};
  for (const r of records) {
    if (!r.zona_id) continue;
    const z = m[r.zona_id] || (m[r.zona_id] = { total: 0, done: 0 });
    z.total += 1;
    if (r.estado === 'levantado') z.done += 1;
  }
  return m;
}

function zoneStyle(feature, zstats) {
  const s = zstats[feature.properties.zone_id];
  if (!s || !s.total) {
    return { color: 'rgba(255,255,255,0.20)', weight: 1, fillColor: '#64748b', fillOpacity: 0.05 };
  }
  return { color: 'rgba(255,255,255,0.35)', weight: 1,
           fillColor: interpolateRamp(PROGRESS_RAMP, s.done / s.total), fillOpacity: 0.32 };
}

function pointPopup(r) {
  const done = r.estado === 'levantado';
  const badge = done ? '<span class="asig-badge asig-badge-done">Levantado</span>'
                     : '<span class="asig-badge asig-badge-pending">Falta EDE</span>';
  const gest = `<span class="asig-badge">${escapeHtml(ESTADO_LABEL[r.gestion_estado] || 'Sin asignar')}</span>`;
  return `
    <div class="map-popup">
      <h4>${escapeHtml(r.direccion || r.registro_id || 'Sin dirección')}</h4>
      <div class="asig-popup-badge">${badge} ${gest}</div>
      <dl>
        <dt>Llave</dt><dd class="tabular">${escapeHtml(r.clave_integracion || '')}</dd>
        <dt>Match</dt><dd>${escapeHtml(r.match || '—')}${r.dist_m != null ? ` · ${escapeHtml(String(r.dist_m))} m` : ''}</dd>
        <dt>Barrio / comuna</dt><dd>${escapeHtml(r.barrio || '—')} · ${escapeHtml(r.comuna || '—')}</dd>
        <dt>Zona · ola</dt><dd>${escapeHtml(r.zona_id || 'fuera de zona')} · ${escapeHtml(String(r.ola || '—'))}</dd>
        <dt>Nivel de riesgo</dt><dd>${escapeHtml(r.nivel_riesgo || '—')}</dd>
        <dt>Asignado a</dt><dd>${escapeHtml(r.gestion_asignado_a || '—')}</dd>
      </dl>
    </div>`;
}

function renderMap() {
  const pts = records.filter((r) => r.lat != null && r.lon != null
    && !Number.isNaN(Number(r.lat)) && !Number.isNaN(Number(r.lon)));

  zonesLayer.clearLayers();
  if (zonesGeo) {
    const zstats = zoneProgress();
    L.geoJSON(zonesGeo, {
      style: (f) => zoneStyle(f, zstats),
      onEachFeature: (f, lyr) => {
        const p = f.properties;
        const s = zstats[p.zone_id];
        const av = s && s.total ? ` · ${s.done}/${s.total} (${Math.round((s.done / s.total) * 100)}%)` : ' · sin puntos';
        lyr.bindTooltip(`<strong>${escapeHtml(p.zone_id || '')}</strong> · ola ${escapeHtml(String(p.ola || '—'))}${av}`, { sticky: true });
      },
    }).addTo(zonesLayer);
  }

  pointsLayer.clearLayers();
  // pendientes al final: quedan arriba (son los accionables).
  const ordered = [...pts].sort((a, b) => (a.estado === 'levantado' ? -1 : 1) - (b.estado === 'levantado' ? -1 : 1));
  for (const r of ordered) {
    const done = r.estado === 'levantado';
    L.circleMarker([Number(r.lat), Number(r.lon)], {
      radius: done ? 5 : 7, color: '#0B1D33', weight: 1,
      fillColor: done ? DONE_COLOR : PENDING_COLOR, fillOpacity: done ? 0.7 : 0.95,
    }).bindPopup(pointPopup(r), { maxWidth: 300 }).addTo(pointsLayer);
  }
  setLegend();
}

function setLegend() {
  if (!legendEl) return;
  const points = [
    { label: 'Levantado (EDE hecha)', color: DONE_COLOR },
    { label: 'Falta EDE — asignable', color: PENDING_COLOR },
  ];
  const zonas = [
    { label: '0% levantado', color: PROGRESS_RAMP[0] },
    { label: '50%', color: PROGRESS_RAMP[1] },
    { label: '100%', color: PROGRESS_RAMP[2] },
  ];
  legendEl.style.display = 'block';
  legendEl.innerHTML = `
    <div class="legend-title">Estado del punto</div>
    ${points.map((e) => `<div class="legend-row"><span class="legend-swatch legend-circle" style="background:${e.color}"></span><span>${escapeHtml(e.label)}</span></div>`).join('')}
    <div class="legend-title" style="margin-top:8px">Avance por zona</div>
    ${zonas.map((e) => `<div class="legend-row"><span class="legend-swatch" style="background:${e.color}"></span><span>${escapeHtml(e.label)}</span></div>`).join('')}`;
}

// ── Summary ──────────────────────────────────────────────────────────────────
function renderSummary() {
  const total = records.length;
  const levantados = records.filter((r) => r.estado === 'levantado').length;
  const pendientes = total - levantados;
  const asignados = records.filter((r) => { const e = effectiveEstado(r); return e === 'asignado' || e === 'en_campo'; }).length;
  const sinAsignar = records.filter((r) => effectiveEstado(r) === 'sin_asignar').length;
  const pct = total > 0 ? Math.round((levantados / total) * 100) : 0;
  el('#gestion-summary').innerHTML = `
    <div class="asig-stat"><span class="asig-stat-value tabular">${total}</span><span class="asig-stat-label">Puntos críticos</span></div>
    <div class="asig-stat"><span class="asig-stat-value tabular" style="color:${DONE_COLOR}">${levantados}</span><span class="asig-stat-label">Completados</span></div>
    <div class="asig-stat"><span class="asig-stat-value tabular" style="color:${PENDING_COLOR}">${pendientes}</span><span class="asig-stat-label">Falta EDE</span></div>
    <div class="asig-stat"><span class="asig-stat-value tabular">${asignados}</span><span class="asig-stat-label">Asignados</span></div>
    <div class="asig-stat"><span class="asig-stat-value tabular">${sinAsignar}</span><span class="asig-stat-label">Sin asignar</span></div>
    <div class="asig-progress-wrap">
      <div class="asig-progress-head"><span>Avance de campo</span><span class="tabular">${pct}%</span></div>
      <div class="asig-progress"><div class="asig-progress-fill" style="width:${pct}%"></div></div>
      <p class="asig-progress-note">${cruceMeta?.generated_at ? `Última corrida: ${escapeHtml(formatTs(cruceMeta.generated_at))}` : 'en vivo desde Firestore'}${records.length ? '' : ' · sin datos'}</p>
    </div>`;
}

// ── Table (con edición inline + acciones si canEdit) ─────────────────────────
// Tabla de solo lectura y clara: el estado es un badge, la edición vive en el
// modal de detalle (ojito). Nada de desplegables ni textboxes inline.
const COLS = [
  ['estado', 'Punto'], ['gestion_estado', 'Gestión'], ['gestion_asignado_a', 'Asignado a'],
  ['registro_id', 'ID crítico'], ['direccion', 'Dirección'],
  ['barrio', 'Barrio'], ['comuna', 'Comuna'], ['zona_id', 'Zona'], ['nivel_riesgo', 'Riesgo'],
];

const EYE_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const GESTION_BADGE_CLS = { sin_asignar: 'g-none', asignado: 'g-asig', en_campo: 'g-campo', completado: 'g-done' };

function gestionBadge(r) {
  const s = effectiveEstado(r);
  return `<span class="gestion-badge ${GESTION_BADGE_CLS[s] || 'g-none'}">${escapeHtml(ESTADO_LABEL[s])}</span>`;
}

function estadoOptions(sel) {
  return ESTADOS.map((s) => `<option value="${s}"${(sel || 'sin_asignar') === s ? ' selected' : ''}>${ESTADO_LABEL[s]}</option>`).join('');
}

/** Filas visibles según los filtros activos (líder y/o "solo pendientes"). */
function visibleRecords() {
  let rows = records;
  if (filtroLider) rows = rows.filter((r) => asignadoDe(r) === filtroLider);
  if (soloPendientes) rows = rows.filter((r) => effectiveEstado(r) === 'sin_asignar');
  // pendientes primero (accionables arriba).
  return [...rows].sort((a, b) => (a.estado === 'levantado' ? 1 : 0) - (b.estado === 'levantado' ? 1 : 0));
}

function renderTable() {
  const head = '<th>Ver</th>' + COLS.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join('')
    + (canEdit ? '<th>Asignar</th>' : '');
  const rows = visibleRecords().map((r) => {
    const detalle = `<td><button type="button" class="btn-icon gestion-eye" data-id="${escapeHtml(r.id)}" aria-label="Ver detalle" title="Ver / gestionar">${EYE_SVG}</button></td>`;
    const cells = COLS.map(([key]) => {
      if (key === 'estado') {
        return r.estado === 'levantado'
          ? '<td><span class="asig-badge asig-badge-done">Levantado</span></td>'
          : '<td><span class="asig-badge asig-badge-pending">Falta EDE</span></td>';
      }
      if (key === 'gestion_estado') return `<td>${gestionBadge(r)}</td>`;
      if (key === 'gestion_asignado_a') return `<td>${escapeHtml(asignadoDe(r) || '—')}</td>`;
      if (key === 'registro_id') {
        // ID único real del crítico (API). Truncado en la tabla; completo en el detalle.
        const rid = r.registro_id || '';
        return `<td class="tabular" title="${escapeHtml(rid)}">${escapeHtml(rid.slice(0, 8))}${rid.length > 8 ? '…' : ''}</td>`;
      }
      const v = r[key] == null ? '' : String(r[key]);
      return `<td>${escapeHtml(v)}</td>`;
    }).join('');
    // Botón Asignar como acción de fila: asigna SOLO ESE PUNTO (uno a uno).
    // La asignación masiva por zona vive en "Registrar despacho".
    let asignar = '';
    if (canEdit) {
      const already = effectiveEstado(r) !== 'sin_asignar';
      const done = r.estado === 'levantado';
      asignar = done
        ? '<td>—</td>'
        : `<td><button type="button" class="btn-mini gestion-asignar" data-id="${escapeHtml(r.id)}">${already ? 'Reasignar' : 'Asignar'}</button></td>`;
    }
    const stateClass = r.estado === 'levantado' ? 'asig-row-done' : 'asig-row-pending';
    return `<tr class="${stateClass}">${detalle}${cells}${asignar}</tr>`;
  }).join('');
  const banner = filtroLider
    ? `<div class="gestion-filter-banner">Puntos asignados a <strong>${escapeHtml(filtroLider)}</strong> · ${visibleRecords().length} punto(s) <button type="button" class="btn-mini" id="gestion-clear-lider">Quitar filtro</button></div>`
    : '';
  el('#gestion-table').innerHTML = banner + `<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
  const t = el('#gestion-table');
  const clear = el('#gestion-clear-lider');
  if (clear) clear.addEventListener('click', () => { filtroLider = null; renderTable(); });
  t.querySelectorAll('.gestion-eye').forEach((btn) =>
    btn.addEventListener('click', () => openPunto(btn.dataset.id)));
  t.querySelectorAll('.gestion-asignar').forEach((btn) =>
    btn.addEventListener('click', () => openAsignarPunto(btn.dataset.id)));
}

// ── Directorio de líderes (autocompletar nombre + teléfono) ──────────────────
function subscribeLideres() {
  fb.onSnapshot(fb.collection(fb.db, LIDERES_COLLECTION), (snap) => {
    lideres = snap.docs.map((d) => ({ id: d.id, ...d.data() }))
      .sort((a, b) => String(a.nombre || '').localeCompare(String(b.nombre || ''), 'es'));
    refreshLideresDatalist();
  }, (err) => console.error('Firestore lideres', err));
}

function refreshLideresDatalist() {
  const dl = el('#lideres-list');
  if (!dl) return;
  dl.innerHTML = lideres.map((l) =>
    `<option value="${escapeHtml(l.nombre || '')}">${escapeHtml(l.telefono || '')}</option>`).join('');
}

function liderPorNombre(nombre) {
  const n = String(nombre || '').trim().toLowerCase();
  return lideres.find((l) => String(l.nombre || '').trim().toLowerCase() === n) || null;
}

/** Guarda/actualiza el líder en el directorio (id = nombre normalizado).
 *  Best-effort: un fallo acá nunca bloquea la asignación. */
async function upsertLider(nombre, telefono, entidad) {
  const n = String(nombre || '').trim();
  if (!canEdit || !n) return;
  const id = n.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'lider';
  try {
    await fb.setDoc(fb.doc(fb.db, LIDERES_COLLECTION, id), {
      nombre: n,
      ...(String(telefono || '').trim() ? { telefono: String(telefono).trim() } : {}),
      ...(String(entidad || '').trim() ? { entidad: String(entidad).trim() } : {}),
      actualizado_utc: Date.now(),
    }, { merge: true });
  } catch (ex) {
    console.error('No se pudo guardar el líder en el directorio', ex);
  }
}

// ── Modal Asignar punto (asignación punto a punto) ───────────────────────────
// El override individual (gestion_estado/gestion_asignado_a/gestion_despacho_id)
// GANA sobre la derivación por zona (ver effectiveEstado/asignadoDe), así que
// asignar un punto no toca a los demás de la zona.
let asignarPuntoId = null;

function openAsignarPunto(id) {
  if (!canEdit) return;
  const r = records.find((x) => x.id === id);
  if (!r) return;
  asignarPuntoId = id;
  el('#asignar-dir').textContent = `${r.direccion || r.registro_id} · zona ${r.zona_id || '—'}`;
  el('#asignar-despacho').innerHTML = '<option value="">Sin despacho — asignar solo a una persona</option>'
    + despachos.map((d) => `<option value="${escapeHtml(d.id)}"${r.gestion_despacho_id === d.id ? ' selected' : ''}>${escapeHtml(d.lider || '—')} · ${escapeHtml((d.zonas || []).join(', ') || 'sin zona')}</option>`).join('');
  const lid = liderPorNombre(r.gestion_asignado_a);
  el('#asignar-persona').value = r.gestion_asignado_a || '';
  el('#asignar-telefono').value = lid?.telefono || '';
  el('#asignar-entidad').value = r.gestion_entidad || lid?.entidad || '';
  el('#asignar-fecha').value = r.gestion_fecha || new Date().toISOString().slice(0, 10);
  el('#asignar-nota').value = r.gestion_nota || '';
  refreshLideresDatalist();
  el('#asignar-err').hidden = true;
  const m = el('#asignar-modal');
  m.classList.add('is-open');
  m.setAttribute('aria-hidden', 'false');
}

function closeAsignarPunto() {
  asignarPuntoId = null;
  const m = el('#asignar-modal');
  m.classList.remove('is-open');
  m.setAttribute('aria-hidden', 'true');
}

async function submitAsignarPunto(e) {
  e.preventDefault();
  if (!canEdit || !asignarPuntoId) return;
  const err = el('#asignar-err');
  err.hidden = true;
  const despId = el('#asignar-despacho').value;
  const desp = despId ? despachos.find((d) => d.id === despId) : null;
  const persona = el('#asignar-persona').value.trim() || (desp ? (desp.lider || '') : '');
  if (!persona) {
    err.textContent = 'Poné la persona o elegí un despacho.';
    err.hidden = false;
    return;
  }
  try {
    await fb.updateDoc(fb.doc(fb.db, COLLECTION, asignarPuntoId), {
      gestion_estado: 'asignado',
      gestion_asignado_a: persona,
      gestion_despacho_id: despId || '',
      gestion_entidad: el('#asignar-entidad').value.trim(),
      gestion_fecha: el('#asignar-fecha').value,
      gestion_nota: el('#asignar-nota').value.trim(),
      gestion_actualizado_utc: Date.now(),
      gestion_actualizado_por: user?.email || '',
    });
    // Guarda el líder en el directorio (nombre + tel + entidad) para la próxima.
    upsertLider(persona, el('#asignar-telefono').value, el('#asignar-entidad').value);
    closeAsignarPunto();
  } catch (ex) {
    console.error('No se pudo asignar el punto', ex);
    err.textContent = 'No se pudo guardar. ¿Tenés permiso de edición?';
    err.hidden = false;
  }
}

async function quitarAsignacionPunto() {
  if (!canEdit || !asignarPuntoId) return;
  try {
    // Limpia la asignación completa: el punto vuelve a Pendiente.
    await fb.updateDoc(fb.doc(fb.db, COLLECTION, asignarPuntoId), {
      gestion_estado: 'sin_asignar',
      gestion_asignado_a: '',
      gestion_despacho_id: '',
      gestion_entidad: '',
      gestion_fecha: '',
      gestion_nota: '',
      gestion_actualizado_utc: Date.now(),
      gestion_actualizado_por: user?.email || '',
    });
    closeAsignarPunto();
  } catch (ex) {
    console.error('No se pudo quitar la asignación', ex);
  }
}

function wireAsignarModal() {
  el('#asignar-form').addEventListener('submit', submitAsignarPunto);
  el('#asignar-quitar').addEventListener('click', quitarAsignacionPunto);
  document.querySelectorAll('[data-asignar-close]').forEach((b) => b.addEventListener('click', closeAsignarPunto));
  // Autocompletar teléfono al elegir un líder guardado (en ambos modales).
  el('#asignar-persona').addEventListener('input', (e) => {
    const l = liderPorNombre(e.target.value);
    if (l?.telefono) el('#asignar-telefono').value = l.telefono;
    if (l?.entidad) el('#asignar-entidad').value = l.entidad;
  });
  el('#despacho-lider').addEventListener('input', (e) => {
    const l = liderPorNombre(e.target.value);
    if (l?.telefono) el('#despacho-celular').value = l.telefono;
  });
}

// ── Modal de detalle del punto (dos IDs + datos del match) ───────────────────
function openPunto(id) {
  const r = records.find((x) => x.id === id);
  if (!r) return;
  const row = (k, v) => `<dt>${escapeHtml(k)}</dt><dd>${v}</dd>`;
  const mono = (v) => `<span class="tabular">${escapeHtml(v == null || v === '' ? '—' : String(v))}</span>`;
  const matchTxt = r.estado === 'levantado'
    ? `${escapeHtml(r.match || '—')}${r.dist_m != null ? ` · ${r.dist_m} m` : (r.match === 'globalid' ? ' · por GlobalID' : '')}`
    : 'sin match (falta EDE)';
  // Despacho enganchado a ESTE punto (asignación punto a punto).
  const desp = puntoDespacho(r);
  const despachoBlock = desp ? `
    <h5 class="punto-modal-sect">Despacho asignado a este punto</h5>
    <dl class="punto-modal-dl">
      ${row('Líder', escapeHtml(desp.lider || '—'))}
      ${row('Entidad · cuadrilla', `${escapeHtml(desp.entidad || '—')} · ${Number(desp.n_personas) || 0} pers · ${Number(desp.n_vehiculos) || 0} veh`)}
      ${row('Celular', escapeHtml(desp.celular || '—'))}
      ${row('Fecha del despacho', escapeHtml(desp.fecha || '—'))}
    </dl>` : '';
  el('#punto-modal-body').innerHTML = `
    <h4 class="punto-modal-dir">${escapeHtml(r.direccion || 'Sin dirección')}</h4>
    <div class="punto-modal-badges">
      ${r.estado === 'levantado' ? '<span class="asig-badge asig-badge-done">Levantado</span>' : '<span class="asig-badge asig-badge-pending">Falta EDE</span>'}
      ${gestionBadge(r)}
    </div>
    <h5 class="punto-modal-sect">Identificadores</h5>
    <dl class="punto-modal-dl">
      ${row('ID crítico (API atencionsismo)', mono(r.registro_id))}
      ${row('ID evaluación (API)', mono(r.evaluacion_id))}
      ${row('ID survey EDE (GlobalID)', mono(r.survey_globalid))}
      ${row('Llave de integración', mono(r.clave_integracion))}
    </dl>
    <h5 class="punto-modal-sect">Datos del match</h5>
    <dl class="punto-modal-dl">
      ${row('Método', escapeHtml(matchTxt))}
      ${row('Coordenadas (corregidas)', mono(r.lat != null ? `${r.lat}, ${r.lon}` : '—'))}
      ${row('Zona · ola · despacho', `${escapeHtml(r.zona_id || 'fuera de zona')} · ${escapeHtml(String(r.ola || '—'))} · ${escapeHtml(String(r.despacho || '—'))}`)}
      ${row('Barrio · comuna', `${escapeHtml(r.barrio || '—')} · ${escapeHtml(r.comuna || '—')}`)}
      ${row('Fecha EDE · evaluador', `${escapeHtml(r.survey_fecha || '—')} · ${escapeHtml(r.survey_evaluador || '—')}`)}
      ${row('Nivel de riesgo', escapeHtml(r.nivel_riesgo || '—'))}
      ${row('Requiere demolición', escapeHtml(r.requiere_demolicion || '—'))}
    </dl>
    <h5 class="punto-modal-sect">Datos técnicos (API)</h5>
    <dl class="punto-modal-dl">
      ${row('Habitabilidad', escapeHtml(r.habitabilidad || '—'))}
      ${row('Tipo · nombre estructura', `${escapeHtml(r.tipo_estructura || '—')}${r.nombre_estructura ? ' · ' + escapeHtml(r.nombre_estructura) : ''}`)}
      ${row('Víctimas (fall. / atrap. / resc.)', `${r.n_fallecidos ?? 0} / ${r.n_atrapamientos ?? 0} / ${r.n_rescatados ?? 0}`)}
      ${r.descripcion_visita ? row('Concepto técnico (visita)', escapeHtml(r.descripcion_visita)) : ''}
      ${r.descripcion_edan ? row('Descripción de ingreso', escapeHtml(r.descripcion_edan)) : ''}
    </dl>
    ${despachoBlock}
    <h5 class="punto-modal-sect">Gestión</h5>
    ${(r.gestion_entidad || r.gestion_fecha) ? `<p class="asig-progress-note">Entidad: ${escapeHtml(r.gestion_entidad || '—')} · fecha de visita: ${escapeHtml(r.gestion_fecha || '—')}</p>` : ''}
    ${canEdit ? `
      ${r.estado === 'levantado' ? '<p class="asig-progress-note">EDE hecha → estado <strong>Completado</strong> automático.</p>' : ''}
      <div class="punto-edit">
        <label>Estado
          <select id="punto-edit-estado"${r.estado === 'levantado' ? ' disabled' : ''}>${estadoOptions(r.gestion_estado)}</select>
        </label>
        <label>Asignado a <small>(vacío = líder del despacho de la zona)</small>
          <input type="text" id="punto-edit-asignado" value="${escapeHtml(r.gestion_asignado_a || '')}" placeholder="${escapeHtml(asignadoDe(r) || '—')}">
        </label>
        <label>Nota
          <textarea id="punto-edit-nota" rows="2" placeholder="nota…">${escapeHtml(r.gestion_nota || '')}</textarea>
        </label>
        <div class="punto-edit-actions">
          <button type="button" class="btn-secondary" id="punto-asignar">Asignar este punto</button>
          <button type="button" class="btn-primary" id="punto-edit-save">Guardar gestión</button>
          <span class="asig-error" id="punto-edit-msg" hidden></span>
        </div>
      </div>`
    : `<dl class="punto-modal-dl">
        ${row('Estado', escapeHtml(ESTADO_LABEL[effectiveEstado(r)]))}
        ${row('Asignado a', escapeHtml(asignadoDe(r) || '—'))}
        ${row('Nota', escapeHtml(r.gestion_nota || '—'))}
      </dl>`}`;
  if (canEdit) {
    el('#punto-edit-save').addEventListener('click', () => saveGestion(id));
    el('#punto-asignar').addEventListener('click', () => { closePunto(); openAsignarPunto(id); });
  }
  const m = el('#punto-modal');
  m.classList.add('is-open');
  m.setAttribute('aria-hidden', 'false');
}

function closePunto() {
  const m = el('#punto-modal');
  m.classList.remove('is-open');
  m.setAttribute('aria-hidden', 'true');
}

async function saveGestion(id) {
  if (!canEdit || !fb) return;
  const patch = {
    gestion_estado: el('#punto-edit-estado').value,
    gestion_asignado_a: el('#punto-edit-asignado').value.trim(),
    gestion_nota: el('#punto-edit-nota').value.trim(),
    gestion_actualizado_utc: Date.now(),
    gestion_actualizado_por: user?.email || '',
  };
  const msg = el('#punto-edit-msg');
  try {
    await fb.updateDoc(fb.doc(fb.db, COLLECTION, id), patch);
    closePunto();
  } catch (ex) {
    console.error('No se pudo guardar la gestión', ex);
    if (msg) { msg.textContent = 'No se pudo guardar. ¿Tenés permiso de edición?'; msg.hidden = false; }
  }
}

// ── Live data ────────────────────────────────────────────────────────────────
/** Etiqueta de fecha de CORRIDA (no de mutación real): usa _meta/resumen.generated_at. */
function corridaLabel() {
  const upd = el('#gestion-updated');
  if (!upd) return;
  const fecha = cruceMeta?.generated_at ? formatTs(cruceMeta.generated_at) : '—';
  upd.textContent = `${records.length} puntos · corrida ${fecha}`;
}

function subscribeMeta() {
  fb.onSnapshot(fb.doc(fb.db, '_meta', 'resumen'), (snap) => {
    cruceMeta = snap.exists() ? snap.data() : null;
    corridaLabel();
  }, (err) => console.error('Firestore _meta', err));
}

function subscribe() {
  if (unsub) return;
  unsub = fb.onSnapshot(fb.collection(fb.db, COLLECTION), (snap) => {
    records = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
    corridaLabel();
    renderSummary();
    renderMap();
    renderTable();
    renderDespachos();  // nPuntos por despacho depende de records
    invalidateGestionSize();
  }, (err) => {
    console.error('Firestore onSnapshot', err);
    el('#gestion-summary').innerHTML = '<p class="asig-error">No se pudo leer el registro en vivo desde Firestore.</p>';
  });
}

// ── Modal Registrar despacho ─────────────────────────────────────────────────
let despachoZonas = [];  // zonas agregadas a la lista del form

/** Puntos pendientes (sin levantar) de una zona — la "carga" del recorrido. */
function zonaPendientes(zonaId) {
  return records.filter((r) => r.zona_id === zonaId && r.estado !== 'levantado').length;
}

function zonaOptions() {
  // Zonas de la base KML; excluye las ya agregadas. Cada opción muestra su carga.
  const fromKml = (zonesGeo?.features || []).map((f) => f.properties?.zone_id).filter(Boolean);
  const ids = [...new Set(fromKml.length ? fromKml : records.map((r) => r.zona_id).filter(Boolean))].sort();
  return ['<option value="">Agregar una zona…</option>']
    .concat(ids.filter((z) => !despachoZonas.includes(z)).map((z) => {
      const p = zonaPendientes(z);
      return `<option value="${escapeHtml(z)}">${escapeHtml(z)} · ${p} pendiente${p === 1 ? '' : 's'}</option>`;
    })).join('');
}

/** Re-dibuja el selector (opciones addable) y los chips de zonas elegidas. */
function refreshZonaPicker() {
  const sel = el('#despacho-zona');
  if (sel) sel.innerHTML = zonaOptions();
  const box = el('#despacho-zona-list');
  if (!box) return;
  box.innerHTML = despachoZonas.map((z) =>
    `<span class="despacho-chip">${escapeHtml(z)} · ${zonaPendientes(z)} pend.<button type="button" data-zona="${escapeHtml(z)}" aria-label="Quitar">×</button></span>`).join('');
  box.querySelectorAll('button[data-zona]').forEach((b) =>
    b.addEventListener('click', () => { despachoZonas = despachoZonas.filter((z) => z !== b.dataset.zona); refreshZonaPicker(); }));
}

/** Abre el modal. opts.prefillZona precarga una zona (botón de fila); opts.edit
 *  carga un despacho existente para editarlo (PUT). */
function openDespacho(opts = {}) {
  if (!canEdit) return;
  const d = opts.edit || null;
  despachoEditId = d ? d.id : null;
  despachoZonas = d ? [...(d.zonas || [])]
    : (opts.prefillZona ? [opts.prefillZona] : []);
  refreshZonaPicker();
  el('#despacho-lider').value = d?.lider || '';
  el('#despacho-celular').value = d?.celular || '';
  el('#despacho-entidad').value = d?.entidad || '';
  el('#despacho-personas').value = d?.n_personas || '';
  el('#despacho-vehiculos').value = d?.n_vehiculos || '';
  el('#despacho-sociales').value = d?.profesionales_sociales || '';
  el('#despacho-fecha').value = d?.fecha || new Date().toISOString().slice(0, 10);
  el('#despacho-observaciones').value = d?.observaciones || '';
  el('#despacho-err').hidden = true;
  el('#despacho-modal-title').textContent = d ? 'Editar despacho' : 'Registrar despacho';
  el('#despacho-submit').textContent = d ? 'Guardar cambios' : 'Asignar zona';
  const m = el('#despacho-modal');
  m.classList.add('is-open');
  m.setAttribute('aria-hidden', 'false');
}

function closeDespacho() {
  despachoEditId = null;
  const m = el('#despacho-modal');
  m.classList.remove('is-open');
  m.setAttribute('aria-hidden', 'true');
}

async function submitDespacho(e) {
  e.preventDefault();
  const err = el('#despacho-err');
  err.hidden = true;
  const lider = el('#despacho-lider').value.trim();
  if (!despachoZonas.length) { err.textContent = 'Agregá al menos una zona.'; err.hidden = false; return; }
  if (!lider) { err.textContent = 'Falta el líder.'; err.hidden = false; return; }
  const despacho = {
    zonas: despachoZonas,
    lider,
    celular: el('#despacho-celular').value.trim(),
    entidad: el('#despacho-entidad').value.trim(),
    n_personas: Number(el('#despacho-personas').value) || 0,
    n_vehiculos: Number(el('#despacho-vehiculos').value) || 0,
    profesionales_sociales: el('#despacho-sociales').value.trim(),
    fecha: el('#despacho-fecha').value,
    observaciones: el('#despacho-observaciones').value.trim(),
    actualizado_utc: Date.now(),
    actualizado_por: user?.email || '',
  };
  const submitBtn = el('#despacho-submit');
  submitBtn.disabled = true;
  try {
    // La asignación es POR ZONA: el despacho guarda sus zonas y de ahí se deriva
    // el estado de cada punto. No se escribe punto por punto (un recorrido cubre
    // varios puntos de la zona), así se evita repetir y duplicar estados.
    if (despachoEditId) {
      await fb.updateDoc(fb.doc(fb.db, DESPACHOS_COLLECTION, despachoEditId), despacho);
    } else {
      despacho.creado_utc = Date.now();
      despacho.creado_por = user?.email || '';
      await fb.addDoc(fb.collection(fb.db, DESPACHOS_COLLECTION), despacho);
    }
    upsertLider(lider, despacho.celular, despacho.entidad);  // directorio para próximas asignaciones
    closeDespacho();
  } catch (ex) {
    console.error('No se pudo guardar el despacho', ex);
    err.textContent = 'No se pudo guardar el despacho. ¿Tenés permiso de edición?';
    err.hidden = false;
  } finally {
    submitBtn.disabled = false;
  }
}

function wireDespachoModal() {
  el('#gestion-despacho-btn').addEventListener('click', () => openDespacho());
  el('#despacho-zona').addEventListener('change', (e) => {
    const v = e.target.value;
    if (v && !despachoZonas.includes(v)) despachoZonas.push(v);
    e.target.value = '';
    refreshZonaPicker();
  });
  el('#despacho-form').addEventListener('submit', submitDespacho);
  document.querySelectorAll('[data-despacho-close]').forEach((b) => b.addEventListener('click', closeDespacho));
  document.querySelectorAll('[data-punto-close]').forEach((b) => b.addEventListener('click', closePunto));
}

// ── Despachos (CRUD) ─────────────────────────────────────────────────────────
function subscribeDespachos() {
  if (unsubDespachos) return;
  unsubDespachos = fb.onSnapshot(fb.collection(fb.db, DESPACHOS_COLLECTION), (snap) => {
    despachos = snap.docs.map((d) => ({ id: d.id, ...d.data() }))
      .sort((a, b) => (b.creado_utc || 0) - (a.creado_utc || 0));
    // El estado/responsable de cada punto se DERIVA de la cobertura de zona, así
    // que un cambio en despachos re-renderiza tabla, mapa y resumen también.
    renderDespachos();
    renderSummary();
    renderMap();
    renderTable();
  }, (err) => console.error('Firestore despachos', err));
}

function renderDespachos() {
  const box = el('#gestion-despachos');
  if (!box) return;
  if (!despachos.length) {
    box.innerHTML = '<p class="asig-progress-note">Sin despachos registrados.</p>';
    return;
  }
  const rows = despachos.map((d) => {
    // Puntos ENGANCHADOS a este despacho uno a uno (no por zona).
    const propios = records.filter((r) => r.gestion_despacho_id === d.id);
    const nPuntos = propios.length;
    const nPend = propios.filter((r) => r.estado !== 'levantado').length;
    const acciones = canEdit
      ? `<div class="despacho-card-actions">
           <button type="button" class="btn-mini despacho-edit" data-id="${escapeHtml(d.id)}">Editar</button>
           <button type="button" class="btn-mini btn-mini-danger despacho-del" data-id="${escapeHtml(d.id)}">Eliminar</button>
         </div>` : '';
    return `<div class="despacho-card">
      <div class="despacho-card-head">
        <strong>${escapeHtml(d.lider || '—')}</strong>
        <span class="asig-badge">${escapeHtml((d.zonas || []).join(', ') || 'sin zona')}</span>
        ${acciones}
      </div>
      <div class="despacho-card-meta">
        ${escapeHtml(d.entidad || '—')} · cuadrilla ${Number(d.n_personas) || 0} · ${Number(d.n_vehiculos) || 0} veh.
        · ${escapeHtml(d.fecha || '—')} · <strong>${nPuntos}</strong> pts (${nPend} pend.)${d.celular ? ` · ${escapeHtml(d.celular)}` : ''}
        ${d.observaciones ? `<br><em>${escapeHtml(d.observaciones)}</em>` : ''}
        <div class="despacho-card-foot"><button type="button" class="btn-mini despacho-verpuntos" data-id="${escapeHtml(d.id)}">Ver puntos asignados</button></div>
      </div>
    </div>`;
  }).join('');
  box.innerHTML = rows;
  box.querySelectorAll('.despacho-verpuntos').forEach((b) =>
    b.addEventListener('click', () => {
      const d = despachos.find((x) => x.id === b.dataset.id);
      if (!d) return;
      filtroLider = d.lider;
      renderTable();
      el('#gestion-table').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
  if (canEdit) {
    box.querySelectorAll('.despacho-edit').forEach((b) =>
      b.addEventListener('click', () => {
        const d = despachos.find((x) => x.id === b.dataset.id);
        if (d) openDespacho({ edit: d });
      }));
    box.querySelectorAll('.despacho-del').forEach((b) =>
      b.addEventListener('click', () => deleteDespacho(b.dataset.id)));
  }
}

async function deleteDespacho(id) {
  if (!canEdit) return;
  const d = despachos.find((x) => x.id === id);
  if (!confirm(`¿Eliminar el despacho de ${d?.lider || 'este líder'}? Los puntos quedan asignados; cambialos manualmente si hace falta.`)) return;
  try {
    await fb.deleteDoc(fb.doc(fb.db, DESPACHOS_COLLECTION, id));
  } catch (ex) {
    console.error('No se pudo eliminar el despacho', ex);
    alert('No se pudo eliminar el despacho.');
  }
}

// ── Descarga de la lista visible (para pasar a los voluntarios) ──────────────
// Exporta lo que la tabla muestra: si está activo el filtro por líder ("Ver
// puntos asignados") baja la lista de ESA persona; si no, todo el registro.
// CSV con BOM: Excel lo abre con tildes correctas, sin dependencias.
function buildCsv() {
  const headers = ['Asignado a', 'Teléfono', 'Entidad', 'Fecha visita', 'Estado gestión', 'Punto',
    'Dirección', 'Barrio', 'Comuna', 'Zona', 'Nivel de riesgo', 'Observaciones',
    'ID crítico', 'Lat', 'Lon', 'Google Maps'];
  const cell = (v) => {
    const s = v == null ? '' : String(v);
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const rows = visibleRecords().map((r) => {
    const persona = asignadoDe(r);
    const tel = liderPorNombre(persona)?.telefono || puntoDespacho(r)?.celular || '';
    const maps = (r.lat != null && r.lon != null) ? `https://maps.google.com/?q=${r.lat},${r.lon}` : '';
    return [persona || '—', tel, r.gestion_entidad || '', r.gestion_fecha || '',
      ESTADO_LABEL[effectiveEstado(r)],
      r.estado === 'levantado' ? 'Levantado' : 'Falta EDE',
      r.direccion || '', r.barrio || '', r.comuna || '', r.zona_id || '',
      r.nivel_riesgo || '', r.gestion_nota || '',
      r.registro_id || '', r.lat ?? '', r.lon ?? '', maps]
      .map(cell).join(',');
  });
  return '﻿' + headers.map(cell).join(',') + '\n' + rows.join('\n');
}

function downloadLista() {
  const csv = buildCsv();
  const slug = (filtroLider || 'todos').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = `puntos_${slug}_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

// ── Public API (mismos nombres que la vista Gestión anterior) ────────────────
export function invalidateGestionSize() {
  if (map) setTimeout(() => map.invalidateSize(), 60);
}

export function applyGestionTheme() {
  if (!map) return;
  if (baseTile) map.removeLayer(baseTile);
  baseTile = L.tileLayer(basemapTileUrl(), {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd', maxZoom: 20,
  }).addTo(map);
  baseTile.bringToBack();
}

export async function initGestion() {
  if (initialized) { invalidateGestionSize(); return; }
  initialized = true;

  map = L.map('gestion-map', { zoomControl: true, minZoom: 10, maxZoom: 18 }).setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd', maxZoom: 20,
  }).addTo(map);
  zonesLayer = L.layerGroup().addTo(map);
  pointsLayer = L.layerGroup().addTo(map);

  const legendControl = L.control({ position: 'bottomright' });
  legendControl.onAdd = () => { legendEl = L.DomUtil.create('div', 'map-legend'); L.DomEvent.disableClickPropagation(legendEl); return legendEl; };
  legendControl.addTo(map);

  // Zonas (polígonos) siguen siendo un artefacto estático.
  try {
    const z = await fetch(`${ZONES_URL}?t=${Date.now()}`, { cache: 'no-store' });
    zonesGeo = z.ok ? await z.json() : null;
  } catch { zonesGeo = null; }

  try {
    await loadFirebase();
  } catch (ex) {
    console.error('Firebase SDK', ex);
    el('#gestion-summary').innerHTML = '<p class="asig-error">No se pudo cargar Firebase.</p>';
    return;
  }

  wireDespachoModal();
  wireAsignarModal();
  el('#gestion-download').addEventListener('click', downloadLista);

  // Filtro rápido: solo pendientes sin asignar (agilidad, evitar repetir).
  const toggle = el('#gestion-solo-pendientes');
  if (toggle) toggle.addEventListener('change', (e) => { soloPendientes = e.target.checked; renderTable(); });

  fb.onAuthStateChanged(fb.auth, async (u) => {
    user = u;
    canEdit = false;
    if (u) {
      try { const tok = await u.getIdTokenResult(); canEdit = tok.claims.pmu === true; }
      catch (ex) { console.error(ex); }
    }
    renderAuthBar();
    el('#gestion-despacho-btn').hidden = !canEdit;
    renderTable();       // muestra/oculta controles + acciones
    renderDespachos();   // muestra/oculta editar/eliminar
  });

  subscribe();
  subscribeDespachos();
  subscribeMeta();
  subscribeLideres();
  invalidateGestionSize();
}
