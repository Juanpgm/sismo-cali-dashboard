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
const SDK = 'https://www.gstatic.com/firebasejs/10.12.2';

const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
const ZONES_URL = 'data/zonas_asignacion.geojson';
const PENDING_COLOR = COLORS.status.r2;   // rojo — falta levantamiento
const DONE_COLOR = COLORS.status.h;       // verde — levantado en campo

// Gestión workflow (editable). Los "hechos" del cruce (levantado/pendiente) no
// se tocan; esto es el estado operativo de la asignación.
const ESTADOS = ['sin_asignar', 'asignado', 'en_campo', 'cerrado'];
const ESTADO_LABEL = { sin_asignar: 'Sin asignar', asignado: 'Asignado', en_campo: 'En campo', cerrado: 'Cerrado' };

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
let unsub = null;          // onSnapshot unsubscribe

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
function zoneStyle(feature) {
  const ola = String(feature.properties.ola || '');
  return { color: 'rgba(255,255,255,0.30)', weight: 1,
           fillColor: ola === '1' ? COLORS.accent : COLORS.status.r2, fillOpacity: 0.06 };
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
    L.geoJSON(zonesGeo, {
      style: zoneStyle,
      onEachFeature: (f, lyr) => {
        const p = f.properties;
        lyr.bindTooltip(`<strong>${escapeHtml(p.zone_id || '')}</strong> · ola ${escapeHtml(String(p.ola || '—'))}`, { sticky: true });
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
  const entries = [
    { label: 'Levantado (EDE hecha)', color: DONE_COLOR },
    { label: 'Falta EDE — asignable', color: PENDING_COLOR },
  ];
  legendEl.style.display = 'block';
  legendEl.innerHTML = `<div class="legend-title">Estado del punto</div>${
    entries.map((e) => `<div class="legend-row"><span class="legend-swatch legend-circle" style="background:${e.color}"></span><span>${escapeHtml(e.label)}</span></div>`).join('')}`;
}

// ── Summary ──────────────────────────────────────────────────────────────────
function renderSummary() {
  const total = records.length;
  const levantados = records.filter((r) => r.estado === 'levantado').length;
  const pendientes = total - levantados;
  const cerrados = records.filter((r) => r.gestion_estado === 'cerrado').length;
  const asignados = records.filter((r) => r.gestion_estado && r.gestion_estado !== 'sin_asignar' && r.gestion_estado !== 'cerrado').length;
  const pct = total > 0 ? Math.round((levantados / total) * 100) : 0;
  el('#gestion-summary').innerHTML = `
    <div class="asig-stat"><span class="asig-stat-value tabular">${total}</span><span class="asig-stat-label">Puntos críticos</span></div>
    <div class="asig-stat"><span class="asig-stat-value tabular" style="color:${DONE_COLOR}">${levantados}</span><span class="asig-stat-label">Levantados</span></div>
    <div class="asig-stat"><span class="asig-stat-value tabular" style="color:${PENDING_COLOR}">${pendientes}</span><span class="asig-stat-label">Falta EDE</span></div>
    <div class="asig-stat"><span class="asig-stat-value tabular">${asignados}</span><span class="asig-stat-label">En gestión</span></div>
    <div class="asig-stat"><span class="asig-stat-value tabular">${cerrados}</span><span class="asig-stat-label">Cerrados</span></div>
    <div class="asig-progress-wrap">
      <div class="asig-progress-head"><span>Avance de campo</span><span class="tabular">${pct}%</span></div>
      <div class="asig-progress"><div class="asig-progress-fill" style="width:${pct}%"></div></div>
      <p class="asig-progress-note">en vivo desde Firestore${records.length ? '' : ' · sin datos'}</p>
    </div>`;
}

// ── Table (con edición inline si canEdit) ────────────────────────────────────
const COLS = [
  ['estado', 'Punto'], ['gestion_estado', 'Gestión'], ['gestion_asignado_a', 'Asignado a'],
  ['clave_integracion', 'Llave'], ['direccion', 'Dirección'], ['barrio', 'Barrio'],
  ['comuna', 'Comuna'], ['zona_id', 'Zona'], ['nivel_riesgo', 'Riesgo'],
];

function estadoSelect(r) {
  const opts = ESTADOS.map((s) => `<option value="${s}"${(r.gestion_estado || 'sin_asignar') === s ? ' selected' : ''}>${ESTADO_LABEL[s]}</option>`).join('');
  return `<select class="gestion-edit" data-id="${escapeHtml(r.id)}" data-field="gestion_estado">${opts}</select>`;
}

function renderTable() {
  const head = COLS.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join('');
  const sorted = [...records].sort((a, b) => (a.estado === 'levantado' ? 1 : 0) - (b.estado === 'levantado' ? 1 : 0));
  const rows = sorted.map((r) => {
    const cells = COLS.map(([key]) => {
      if (key === 'estado') {
        return r.estado === 'levantado'
          ? '<td><span class="asig-badge asig-badge-done">Levantado</span></td>'
          : '<td><span class="asig-badge asig-badge-pending">Falta EDE</span></td>';
      }
      if (key === 'gestion_estado') {
        return `<td>${canEdit ? estadoSelect(r) : escapeHtml(ESTADO_LABEL[r.gestion_estado] || 'Sin asignar')}</td>`;
      }
      if (key === 'gestion_asignado_a') {
        return canEdit
          ? `<td><input class="gestion-edit" type="text" value="${escapeHtml(r.gestion_asignado_a || '')}" data-id="${escapeHtml(r.id)}" data-field="gestion_asignado_a" placeholder="—"></td>`
          : `<td>${escapeHtml(r.gestion_asignado_a || '—')}</td>`;
      }
      const v = r[key] == null ? '' : String(r[key]);
      const cls = key === 'clave_integracion' ? ' class="tabular"' : '';
      return `<td${cls}>${escapeHtml(v)}</td>`;
    }).join('');
    const stateClass = r.estado === 'levantado' ? 'asig-row-done' : 'asig-row-pending';
    return `<tr class="${stateClass}">${cells}</tr>`;
  }).join('');
  el('#gestion-table').innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
  if (canEdit) wireEditControls();
}

function wireEditControls() {
  el('#gestion-table').querySelectorAll('.gestion-edit').forEach((input) => {
    const ev = input.tagName === 'SELECT' ? 'change' : 'change'; // input: dispara al perder foco/enter
    input.addEventListener(ev, () => saveEdit(input.dataset.id, input.dataset.field, input.value));
  });
}

async function saveEdit(id, field, value) {
  if (!canEdit || !fb) return;
  const patch = {
    [field]: value,
    gestion_actualizado_utc: Date.now(),
    gestion_actualizado_por: user?.email || '',
  };
  try {
    await fb.updateDoc(fb.doc(fb.db, COLLECTION, id), patch);
    // onSnapshot re-renderiza solo; actualizamos el objeto local por si acaso.
    const r = records.find((x) => x.id === id);
    if (r) Object.assign(r, patch);
  } catch (ex) {
    console.error('No se pudo guardar la edición', ex);
    alert('No se pudo guardar. ¿Tenés permiso de edición (login del equipo)?');
  }
}

// ── Live data ────────────────────────────────────────────────────────────────
function subscribe() {
  if (unsub) return;
  unsub = fb.onSnapshot(fb.collection(fb.db, COLLECTION), (snap) => {
    records = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
    const upd = el('#gestion-updated');
    if (upd) upd.textContent = `${records.length} puntos · en vivo`;
    renderSummary();
    renderMap();
    renderTable();
    invalidateGestionSize();
  }, (err) => {
    console.error('Firestore onSnapshot', err);
    el('#gestion-summary').innerHTML = '<p class="asig-error">No se pudo leer el registro en vivo desde Firestore.</p>';
  });
}

// ── Modal Registrar despacho ─────────────────────────────────────────────────
let despachoZonas = [];  // zonas agregadas a la lista del form

function zonaOptions() {
  // Todas las zonas de la base KML (zonas_asignacion.geojson); fallback a las que
  // aparecen en los registros si el geojson no cargó.
  const fromKml = (zonesGeo?.features || []).map((f) => f.properties?.zone_id).filter(Boolean);
  const ids = [...new Set(fromKml.length ? fromKml : records.map((r) => r.zona_id).filter(Boolean))].sort();
  return ['<option value="">Selecciona una zona…</option>']
    .concat(ids.map((z) => `<option value="${escapeHtml(z)}">${escapeHtml(z)}</option>`)).join('');
}

function renderZonaChips() {
  const box = el('#despacho-zona-list');
  if (!box) return;
  box.innerHTML = despachoZonas.map((z) =>
    `<span class="despacho-chip">${escapeHtml(z)}<button type="button" data-zona="${escapeHtml(z)}" aria-label="Quitar">×</button></span>`).join('');
  box.querySelectorAll('button[data-zona]').forEach((b) =>
    b.addEventListener('click', () => { despachoZonas = despachoZonas.filter((z) => z !== b.dataset.zona); renderZonaChips(); }));
}

function openDespacho() {
  if (!canEdit) return;
  despachoZonas = [];
  el('#despacho-zona').innerHTML = zonaOptions();
  renderZonaChips();
  el('#despacho-fecha').value = new Date().toISOString().slice(0, 10);
  el('#despacho-err').hidden = true;
  const m = el('#despacho-modal');
  m.classList.add('is-open');
  m.setAttribute('aria-hidden', 'false');
}

function closeDespacho() {
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
    creado_utc: Date.now(),
    creado_por: user?.email || '',
  };
  const submitBtn = el('#despacho-submit');
  submitBtn.disabled = true;
  try {
    await fb.addDoc(fb.collection(fb.db, DESPACHOS_COLLECTION), despacho);
    // Asigna los puntos pendientes de esas zonas a este despacho (líder).
    const targets = records.filter((r) => despachoZonas.includes(r.zona_id)
      && r.estado !== 'levantado'
      && (!r.gestion_estado || r.gestion_estado === 'sin_asignar'));
    if (targets.length) {
      const batch = fb.writeBatch(fb.db);
      for (const r of targets) {
        batch.update(fb.doc(fb.db, COLLECTION, r.id), {
          gestion_estado: 'asignado',
          gestion_asignado_a: lider,
          gestion_actualizado_utc: Date.now(),
          gestion_actualizado_por: user?.email || '',
        });
      }
      await batch.commit();
    }
    closeDespacho();
  } catch (ex) {
    console.error('No se pudo registrar el despacho', ex);
    err.textContent = 'No se pudo guardar el despacho. ¿Tenés permiso de edición?';
    err.hidden = false;
  } finally {
    submitBtn.disabled = false;
  }
}

function wireDespachoModal() {
  el('#gestion-despacho-btn').addEventListener('click', openDespacho);
  el('#despacho-zona-add').addEventListener('click', () => {
    const v = el('#despacho-zona').value;
    if (v && !despachoZonas.includes(v)) { despachoZonas.push(v); renderZonaChips(); }
  });
  el('#despacho-form').addEventListener('submit', submitDespacho);
  document.querySelectorAll('[data-despacho-close]').forEach((b) => b.addEventListener('click', closeDespacho));
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

  fb.onAuthStateChanged(fb.auth, async (u) => {
    user = u;
    canEdit = false;
    if (u) {
      try { const tok = await u.getIdTokenResult(); canEdit = tok.claims.pmu === true; }
      catch (ex) { console.error(ex); }
    }
    renderAuthBar();
    el('#gestion-despacho-btn').hidden = !canEdit;
    renderTable();  // re-render para mostrar/ocultar controles de edición
  });

  subscribe();
  invalidateGestionSize();
}
