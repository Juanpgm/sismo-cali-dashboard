// ATC-20 field form logic: geolocation, photos, unique building code and
// create-only submit. Boots only after auth.js confirms a registered inspector.

// Every Firestore/Auth primitive comes from './auth.js' (single Firebase
// boundary, per design "import dedupe") instead of importing the gstatic
// CDN modules a second time here.
import {
  initAuth, getApp, getDb, getAuth,
  collection, doc, getDoc, getDocs, query, runTransaction, serverTimestamp, where,
} from './auth.js';
import {
  MUNICIPIO, buildCodigo, parseConsecutivo, siguienteConsecutivo, validarSegmento, canAddSlot, MAX_FOTOS,
  siguienteDesdeMax, plegarConsecutivoGuardado, consecutivosExistentes,
  habitabilidadColor, colapsoLabel, mapsDirUrl,
  prioridadColor, elegirEnlaceEncuesta,
  ordenarPorCercania, distanciaM, formatDistancia, solicitadosPrimero, prioridadVisual,
  etiquetaCampana, etiquetaAccionCercano, mensajeEstadoCercanos, cercanosMuestraLista,
  mensajeTomarPunto, mensajeErrorTomarPunto,
  CERCANOS_ESPERANDO, CERCANOS_SIN_GPS, CERCANOS_CARGANDO, CERCANOS_VACIO, CERCANOS_LISTO, CERCANOS_ERROR,
} from './logic.js';

// Serverless signer that validates the Firebase idToken and presigns the
// S3 upload (photos live in S3, not Firebase Storage).
const FOTO_SIGNER_URL = 'https://sismo-fotos-signer.vercel.app/api/sign';

// Dashboard admin endpoint for assigned points: reads/writes sticker_matches
// server-side (Firebase Admin), so the form needs NO Firestore rules for it.
// Auth is the inspector's Firebase ID token.
// Repointed to the consolidated FastAPI backend on Railway (tasks.md 5.5,
// gated on 5.4's parity PASS: misPuntos shape-identical, both actions'
// structural rejection identical). The Railway route mounts WITHOUT an
// `/api` prefix (unlike the legacy Vercel function `localhost:3000` still
// serves in local dev) — INSPECTOR_ASIGNACIONES_PREFIX carries that one
// difference so asignacionesApi() below doesn't hardcode either shape.
const DASHBOARD_API = location.hostname === 'localhost' ? 'http://localhost:3000' : 'https://sismo-cali-dashboard-production.up.railway.app';
const INSPECTOR_ASIGNACIONES_PREFIX = location.hostname === 'localhost' ? '/api' : '';

// Signal the inline CDN-failure watchdog in index.html that modules loaded.
window.__atc20Booted = true;
const bootStatus = document.getElementById('boot-status');
if (bootStatus) bootStatus.remove();

const AREA_NOMBRES = { 1: 'Cabecera', 2: 'Centro Poblado', 3: 'Rural Disperso' };

// GPS refinement: stop watching once the fix is at least this accurate (m),
// or after this much time — whichever comes first. The best fix always wins.
const GEO_ACCURACY_TARGET = 12;
const GEO_MAX_WATCH_MS = 90000;

const state = {
  inspector: null,
  coords: null,            // best fix so far: { lat, lng, accuracy }
  geoWatchId: null,        // active watchPosition id (null = not watching)
  geoWatchTimer: null,     // battery-guard timeout for the watch
  area: null,               // selected DIVIPOLA area, e.g. "1"
  codigo: null,            // generated building code, e.g. "76001-1-0040001"
  // Session-scoped cache of this inspector's max known consecutive (not the
  // next one). null = not yet derived from Firestore. Invalidated only on a
  // codigo-duplicado collision.
  maxConsecutivo: null,
  // Consecutivos this inspector has already saved (Set<number>), filled by
  // the same session query that derives maxConsecutivo and extended on each
  // successful submit. Backs the duplicate guard on the editable segment.
  consecutivosUsados: new Set(),
  // The consecutive value last rendered by generarCodigo/renderCodigo (before
  // any manual edit). Used only to decide whether an edited segment is below
  // the derived next value (non-blocking hint, no floor enforced).
  derivedConsecutivo: null,
  fotos: [],                // { file, previewUrl }[] — dense array, max MAX_FOTOS
  fotosSubidas: {},         // "codigo:name:size:lastModified" -> downloadURL (upload retry cache)
  // Assigned points (sticker_matches) for this inspector, still pending. Empty
  // array = none assigned = go straight to the blank form (hard requirement).
  asignaciones: [],
  // The point currently being registered, or null for a free-form record.
  // { id, coords, direccion }. Drives the sticker_matches "hecho" flip on submit.
  asignacion: null,
  // Assigned Planeación (EDAN survey) points for this inspector, still
  // pending. A DIFFERENT task from applying a sticker — opens Survey123,
  // not this ATC-20 form — so it is tracked and rendered separately, never
  // merged into `asignaciones` above. Empty array = none assigned.
  puntosPlaneacion: [],
  // Which assignments tab is showing ('survey' | 'stickers'). null before
  // the first render — renderAsignaciones() picks a default then. Kept
  // across GPS-driven re-sorts so a better fix never yanks the inspector
  // off the tab they are looking at.
  tabAsigActiva: null,
  // Best GPS fix for sorting the assignment lists by proximity ({ lat, lng,
  // accuracy }), separate from the ATC-20 form's own `coords` above — this
  // one is requested BEFORE a point (or the blank form) is even chosen.
  // null = no usable fix yet (unsorted lists, distance shows as "—").
  origenAsignaciones: null,
  geoAsigWatchId: null,
  geoAsigWatchTimer: null,
  // `puntos-disponibles` change (2026-08-26): nearby, UNASSIGNED, not-yet-
  // covered points (either campaign) the inspector can claim on the spot —
  // a THIRD tab, deliberately never merged into `asignaciones`/
  // `puntosPlaneacion` above (those are "mine"; this is "up for grabs").
  puntosCercanos: [],
  // Small state machine driving `#cercanos-status` (see logic.js's own
  // CERCANOS_* constants/mensajeEstadoCercanos) — GPS is a HARD requirement
  // for this tab, unlike the assigned tabs' own best-effort proximity sort.
  cercanosEstado: CERCANOS_ESPERANDO,
  // True once the first usable GPS fix has triggered a puntosCercanosDisponibles
  // fetch this screen-visit — prevents re-fetching on every watchPosition
  // update (only the assigned tabs' own re-sort does that; a fresh network
  // call per GPS tick would hammer the backend for no benefit here).
  cercanosSolicitados: false,
};

const $ = (sel) => document.querySelector(sel);

initAuth(boot);

function boot(inspector) {
  state.inspector = inspector;

  // Wire everything once; visibility of #app / #asignaciones is decided by the
  // assignments flow below (and requestLocation only runs when the form shows).
  $('#btn-geo').addEventListener('click', requestLocation);

  wirePhotos();

  $('#btn-codigo').addEventListener('click', generarCodigo);
  $('#btn-codigo-editar').addEventListener('click', abrirEdicionConsecutivo);
  $('#btn-codigo-confirmar').addEventListener('click', confirmarConsecutivo);
  $('#btn-codigo-cancelar').addEventListener('click', cancelarConsecutivo);
  $('#codigo-consecutivo').addEventListener('keydown', onConsecutivoKeydown);

  $('#eval-form').addEventListener('submit', onSubmit);
  $('#btn-nuevo').addEventListener('click', nuevoRegistro);

  $('#asig-tab-survey').addEventListener('click', () => activarTabAsignaciones('survey'));
  $('#asig-tab-stickers').addEventListener('click', () => activarTabAsignaciones('stickers'));
  $('#asig-tab-cercanos').addEventListener('click', () => activarTabAsignaciones('cercanos'));
  $('#btn-registro-libre').addEventListener('click', () => mostrarFormulario());
  // Back from the form to "Tus puntos asignados" without submitting —
  // reuses renderAsignaciones(), the SAME function that lands here after
  // boot/submit, so the list/tab/GPS-sort state is always consistent.
  $('#btn-volver-asignaciones').addEventListener('click', () => renderAsignaciones());

  iniciarAsignaciones();
}

// ---- Assigned points (pre-form) ---------------------------------------------
// Before showing the blank form, ask the dashboard admin endpoint for this
// inspector's pending points (it reads sticker_matches server-side, so the
// form needs no Firestore rules). Zero (or any failure) → go straight to the
// form, so an inspector with nothing assigned is never blocked. One or more →
// show the picker; the form opens only once a point is chosen.

// POSTs to the assigned-points endpoint with the inspector's ID token. Same
// token pattern as subirFotos (getAuth(getApp()).currentUser).
async function asignacionesApi(body) {
  const token = await getAuth(getApp()).currentUser.getIdToken();
  return fetch(`${DASHBOARD_API}${INSPECTOR_ASIGNACIONES_PREFIX}/inspector-asignaciones`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
}

// Fetches misPuntos + misPuntosPlaneacion (each fails OPEN independently)
// and updates state in place. Extracted from iniciarAsignaciones so
// onTomarPunto can re-run the SAME logic after a successful claim — the
// newly-assigned point then shows up with its full fields (survey links,
// etc.) via the SAME endpoints the picker already trusts, not a hand-rolled
// merge of tomarPunto's own (deliberately minimal) response.
async function cargarMisPuntos() {
  try {
    const res = await asignacionesApi({ action: 'misPuntos' });
    if (!res.ok) throw new Error(`misPuntos-${res.status}`);
    // Endpoint already filters to this inspector's not-done points.
    state.asignaciones = (await res.json()).puntos || [];
  } catch (err) {
    // Fail open: never block the form on an assignments lookup problem.
    console.warn('No se pudieron cargar los puntos asignados:', err);
    state.asignaciones = [];
  }

  // Independent try/catch: a Planeación lookup failure must never take down
  // the (unrelated) sticker picker above, and vice versa — each assignment
  // kind fails open on its own.
  try {
    const res = await asignacionesApi({ action: 'misPuntosPlaneacion' });
    if (!res.ok) throw new Error(`misPuntosPlaneacion-${res.status}`);
    state.puntosPlaneacion = (await res.json()).puntos || [];
  } catch (err) {
    console.warn('No se pudieron cargar los levantamientos de Planeación:', err);
    state.puntosPlaneacion = [];
  }
}

async function iniciarAsignaciones() {
  await cargarMisPuntos();

  // `puntos-disponibles` change: the picker screen is now ALWAYS shown —
  // even with zero individual assignments — because the "Cercanos" tab may
  // still find nearby unassigned work once GPS resolves. `#btn-registro-libre`
  // is the fail-open escape hatch to a blank record (see index.html's own
  // note on it), replacing the old "both lists empty -> straight to the
  // blank form" bypass.
  requestLocationAsignaciones();
  // Fail open on a render fault too: a bug building the cards must never leave
  // the inspector on a blank screen — fall through to the usable blank form.
  try {
    renderAsignaciones();
  } catch (err) {
    console.warn('No se pudo mostrar el listado de asignados:', err);
    mostrarFormulario();
  }
}

// Shows the form screen and starts GPS. Single entry point for "reveal #app".
// Also the single point where the assignments-screen proximity watch stops —
// every path off that screen goes through here, so a stray late GPS fix can
// never silently flip #asignaciones back to visible after the inspector has
// moved on to the form.
function mostrarFormulario() {
  stopGeoAsigWatch();
  $('#asignaciones').hidden = true;
  $('#confirm').hidden = true;
  $('#app').hidden = false;
  requestLocation();
}

// Rebuilds both tab panels from current state, nearest-first via
// ordenarPorCercania (unsorted, in original order, while no GPS fix is
// available yet — never a wrong or arbitrary order). Keeps whichever tab is
// already active (state.tabAsigActiva) so a GPS re-sort never yanks the
// inspector off the tab/scroll position they are looking at; only picks a
// default the first time (state.tabAsigActiva starts null).
function renderAsignaciones() {
  const stickersOrdenados = ordenarPorCercania(state.asignaciones, state.origenAsignaciones);
  // puntos-solicitados, field-form-session delta: solicited points ("PRIORIDAD")
  // sort before every other point, keeping nearest-first within each group.
  const planeacionOrdenados = solicitadosPrimero(ordenarPorCercania(state.puntosPlaneacion, state.origenAsignaciones));

  const cont = $('#asignaciones-lista');
  cont.innerHTML = '';
  stickersOrdenados.forEach((a) => cont.append(buildAsignacionCard(a, state.origenAsignaciones)));
  $('#asignaciones-vacio').hidden = stickersOrdenados.length > 0;

  const contPlaneacion = $('#planeacion-asignaciones-lista');
  contPlaneacion.innerHTML = '';
  planeacionOrdenados.forEach((p) => contPlaneacion.append(buildPlaneacionCard(p, state.origenAsignaciones)));
  $('#planeacion-asignaciones-vacio').hidden = planeacionOrdenados.length > 0;

  $('#asig-tab-survey-count').textContent = String(planeacionOrdenados.length);
  $('#asig-tab-stickers-count').textContent = String(stickersOrdenados.length);

  // Open on whichever tab actually has work; if both have work, default to
  // Survey. If NEITHER has work, default to Cercanos instead — an inspector
  // with nothing individually assigned should land straight on the one tab
  // that might still have something to do, not on an empty Survey tab that
  // gives no hint the Cercanos tab exists (`puntos-disponibles` change).
  if (!state.tabAsigActiva) {
    if (planeacionOrdenados.length === 0 && stickersOrdenados.length === 0) {
      state.tabAsigActiva = 'cercanos';
    } else {
      state.tabAsigActiva = (planeacionOrdenados.length === 0 && stickersOrdenados.length > 0) ? 'stickers' : 'survey';
    }
  }
  activarTabAsignaciones(state.tabAsigActiva);
  renderCercanos();

  $('#app').hidden = true;
  $('#confirm').hidden = true;
  $('#asignaciones').hidden = false;
}

// Switches the visible tab panel; does NOT touch scroll position or re-fetch
// anything, so it is safe to call on every re-sort re-render too.
function activarTabAsignaciones(tab) {
  state.tabAsigActiva = tab;
  const esSurvey = tab === 'survey';
  const esStickers = tab === 'stickers';
  const esCercanos = tab === 'cercanos';
  $('#asig-tab-survey').classList.toggle('is-active', esSurvey);
  $('#asig-tab-survey').setAttribute('aria-selected', String(esSurvey));
  $('#asig-tab-stickers').classList.toggle('is-active', esStickers);
  $('#asig-tab-stickers').setAttribute('aria-selected', String(esStickers));
  $('#asig-tab-cercanos').classList.toggle('is-active', esCercanos);
  $('#asig-tab-cercanos').setAttribute('aria-selected', String(esCercanos));
  $('#planeacion-asignaciones-section').hidden = !esSurvey;
  $('#asignaciones-stickers-section').hidden = !esStickers;
  $('#cercanos-asignaciones-section').hidden = !esCercanos;
  ocultarErrorTomarPunto(); // a stale claim rejection must not linger across tab switches
}

// Inline SVG chrome replacing the emoji this UI used to prefix "Cómo
// llegar"/"Llamar" with (Feather `map-pin`/`phone`, 24x24, stroke=currentColor,
// no fill, `aria-hidden` since the adjacent text already names the action).
const ICONO_MAPA_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>';
const ICONO_TELEFONO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>';

// Prepends an icon span + plain-text label onto a link, replacing whatever
// emoji-prefixed textContent the 4 call sites used to set directly.
function conIcono(link, svgMarkup, texto) {
  const icono = document.createElement('span');
  icono.className = 'icon';
  icono.innerHTML = svgMarkup;
  link.append(icono, document.createTextNode(texto));
}

// Prominent distance line shared by both card kinds — distance is the
// primary sort key (proximity) and must read at a glance, not be buried
// among the other pills.
function buildDistanciaLinea(coords, origen) {
  const p = document.createElement('p');
  p.className = 'asignacion-distancia';
  p.textContent = formatDistancia(distanciaM(origen, coords));
  return p;
}

function buildAsignacionCard(a, origen) {
  const card = document.createElement('article');
  card.className = 'card asignacion-card';
  card.style.borderLeftColor = habitabilidadColor(a.criterio_habitabilidad);

  card.append(buildDistanciaLinea(a.coords, origen));

  const dir = document.createElement('h3');
  dir.className = 'asignacion-dir';
  dir.textContent = a.direccion || 'Dirección no registrada';
  card.append(dir);

  const pills = document.createElement('div');
  pills.className = 'asignacion-pills';
  const hab = document.createElement('span');
  hab.className = 'pill';
  hab.style.background = habitabilidadColor(a.criterio_habitabilidad);
  hab.textContent = a.criterio_habitabilidad ? String(a.criterio_habitabilidad).toUpperCase() : '—';
  pills.append(hab);
  const colapso = colapsoLabel(a.colapso);
  if (colapso) {
    const cp = document.createElement('span');
    cp.className = 'pill pill-colapso';
    cp.textContent = `Colapso ${colapso}`;
    pills.append(cp);
  }
  card.append(pills);

  const acciones = document.createElement('div');
  acciones.className = 'asignacion-acciones';

  const url = mapsDirUrl(a.coords);
  if (url) {
    const link = document.createElement('a');
    link.className = 'btn-secondary asignacion-maps';
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener';
    conIcono(link, ICONO_MAPA_SVG, 'Cómo llegar');
    acciones.append(link);
  }

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn-primary';
  btn.textContent = 'Registrar Sticker';
  btn.addEventListener('click', () => registrarSticker(a));
  acciones.append(btn);

  card.append(acciones);
  return card;
}

// Link the chosen point to the form: stash it, prefill what is clean to
// prefill (address; GPS stays authoritative for coords), and open the form.
function registrarSticker(a) {
  // clave_integracion rides along when misPuntos' sticker_matches doc has one
  // (planeacion_asignaciones.py's sticker-twin propagation) — null otherwise.
  // Carried through onSubmit so the evaluación can be cross-referenced back
  // to its planeación point, same "traceability back to the assigned point"
  // purpose sticker_match_id already serves.
  state.asignacion = { id: a.id, coords: a.coords, direccion: a.direccion, clave_integracion: a.clave_integracion };
  $('#direccion').value = a.direccion || '';
  mostrarFormulario();
}

// A Planeación point is a DIFFERENT task from a sticker point — it opens an
// external Survey123 EDAN form, it never touches this ATC-20 form/state,
// and it does not call marcarHechoPlaneacion here: the field crew closes it
// FROM Survey123's own submit, the same "the survey closes itself" flow
// `app/jobs/planeacion_cruce.py`'s exact-key auto-close already relies on
// (its own module docstring, "the ONE binding auto-close exception").
function buildPlaneacionCard(p, origen) {
  const card = document.createElement('article');
  card.className = 'card asignacion-card';
  card.style.borderLeftColor = prioridadColor(p.prioridad);

  card.append(buildDistanciaLinea(p.coords, origen));

  // Badge vs. pill decision is a pure function (formulario/js/logic.js,
  // prioridadVisual) so it has real test coverage instead of only being
  // exercised by eyeballing a deployed session.
  const visual = prioridadVisual(p);
  if (visual.badge) {
    const badge = document.createElement('span');
    badge.className = 'badge-prioridad';
    badge.textContent = 'PRIORIDAD';
    card.append(badge);
  }

  const dir = document.createElement('h3');
  dir.className = 'asignacion-dir';
  dir.textContent = p.direccion || 'Dirección no registrada';
  card.append(dir);

  const pills = document.createElement('div');
  pills.className = 'asignacion-pills';
  if (visual.pill) {
    const pr = document.createElement('span');
    pr.className = 'pill';
    pr.style.background = prioridadColor(p.prioridad);
    pr.textContent = `Prioridad ${String(p.prioridad).toUpperCase()}`;
    pills.append(pr);
  }
  if (p.afectacion) {
    const af = document.createElement('span');
    af.className = 'pill pill-colapso';
    af.textContent = p.afectacion;
    pills.append(af);
  }
  card.append(pills);

  // planeacion-flujo-confiable, design.md ADR-3: reporter contact, when
  // `misPuntosPlaneacion` included it (own/group points only — never a
  // public surface). Null-safe: no contact data -> no block, no gap.
  if (p.nombre_solicitante) {
    const solicitante = document.createElement('p');
    solicitante.className = 'asignacion-solicitante';
    solicitante.textContent = `Solicitante: ${p.nombre_solicitante}`;
    card.append(solicitante);
  }

  const acciones = document.createElement('div');
  acciones.className = 'asignacion-acciones';

  const mapsUrl = mapsDirUrl(p.coords);
  if (mapsUrl) {
    const link = document.createElement('a');
    link.className = 'btn-secondary asignacion-maps';
    link.href = mapsUrl;
    link.target = '_blank';
    link.rel = 'noopener';
    conIcono(link, ICONO_MAPA_SVG, 'Cómo llegar');
    acciones.append(link);
  }
  if (p.telefono_solicitante) {
    const llamar = document.createElement('a');
    llamar.className = 'btn-secondary asignacion-llamar';
    llamar.href = `tel:${p.telefono_solicitante}`;
    conIcono(llamar, ICONO_TELEFONO_SVG, 'Llamar');
    acciones.append(llamar);
  }

  const encuestaUrl = elegirEnlaceEncuesta(p, esDispositivoMovil());
  const btn = document.createElement('a');
  btn.className = 'btn-primary';
  btn.textContent = 'Abrir encuesta';
  if (encuestaUrl) {
    btn.href = encuestaUrl;
    btn.target = '_blank';
    btn.rel = 'noopener';
  } else {
    // No SURVEY123_FORM_URL configured for this point (misPuntosPlaneacion
    // fails OPEN on that, per its own contract) — an inert-looking button
    // with no href is safer than a dead link that silently does nothing.
    btn.setAttribute('aria-disabled', 'true');
    btn.title = 'Enlace de encuesta no disponible todavía.';
  }
  acciones.append(btn);

  card.append(acciones);
  return card;
}

// Coarse device check for elegirEnlaceEncuesta's app-vs-web preference —
// the ATC-20 field app itself only runs on a phone/tablet in practice, but
// this still guards the desktop-dev-server case sanely.
function esDispositivoMovil() {
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || '');
}

// ---- `puntos-disponibles` change: nearby UNASSIGNED points -------------------
// Third tab, deliberately kept visually and structurally separate from the
// two "mine" tabs above — see index.html/logic.js's own notes on why a tab
// (not a section) was chosen. Requires a GPS fix (see `cargarPuntosCercanos`
// below); every non-"listo" state shows an explicit `#cercanos-status`
// message instead of an ambiguous empty list.

function renderCercanos() {
  const status = $('#cercanos-status');
  const cont = $('#cercanos-lista');
  const mostrarLista = cercanosMuestraLista(state.cercanosEstado);

  status.textContent = mensajeEstadoCercanos(state.cercanosEstado);
  status.hidden = mostrarLista;
  // `cont` is left visible (not `.hidden`-toggled) on purpose, matching the
  // existing `#asignaciones-lista`/`#planeacion-asignaciones-lista`
  // convention above: `.asignaciones-lista` sets `display: grid`
  // unconditionally, which — like `.foto-actions .foto-action-btn` (see
  // form.css's own note on that) — would silently defeat the UA `[hidden]`
  // rule. An empty grid with zero children renders nothing, so simply not
  // populating it while the status message is showing is enough.
  cont.innerHTML = '';
  if (mostrarLista) {
    const ordenados = ordenarPorCercania(state.puntosCercanos, state.origenAsignaciones);
    ordenados.forEach((p) => cont.append(buildCercanoCard(p, state.origenAsignaciones)));
  }
  $('#asig-tab-cercanos-count').textContent = String(state.puntosCercanos.length);
}

// Fired once, from the FIRST usable GPS fix each screen-visit (see
// requestLocationAsignaciones below) — never on every watchPosition tick,
// which would hammer the backend for no benefit (the assigned tabs' own
// re-sort is free; this is a network call). Fails OPEN: any error just
// leaves the tab on its explanatory status message, never throws up to
// iniciarAsignaciones/requestLocationAsignaciones.
async function cargarPuntosCercanos() {
  state.cercanosEstado = CERCANOS_CARGANDO;
  renderCercanos();
  try {
    const res = await asignacionesApi({
      action: 'puntosCercanosDisponibles',
      lat: state.origenAsignaciones.lat,
      lng: state.origenAsignaciones.lng,
    });
    if (!res.ok) throw new Error(`puntosCercanosDisponibles-${res.status}`);
    state.puntosCercanos = (await res.json()).puntos || [];
    state.cercanosEstado = state.puntosCercanos.length > 0 ? CERCANOS_LISTO : CERCANOS_VACIO;
  } catch (err) {
    console.warn('No se pudieron cargar los puntos cercanos disponibles:', err);
    state.puntosCercanos = [];
    state.cercanosEstado = CERCANOS_ERROR;
  }
  renderCercanos();
}

function buildCercanoCard(p, origen) {
  const card = document.createElement('article');
  card.className = 'card asignacion-card cercano-card';

  card.append(buildDistanciaLinea(p.coords, origen));

  const dir = document.createElement('h3');
  dir.className = 'asignacion-dir';
  dir.textContent = p.direccion || 'Dirección no registrada';
  card.append(dir);

  const pills = document.createElement('div');
  pills.className = 'asignacion-pills';
  const campanaPill = document.createElement('span');
  campanaPill.className = 'pill pill-campana';
  campanaPill.textContent = etiquetaCampana(p.campana);
  pills.append(campanaPill);
  card.append(pills);

  const acciones = document.createElement('div');
  acciones.className = 'asignacion-acciones';

  const mapsUrl = mapsDirUrl(p.coords);
  if (mapsUrl) {
    const link = document.createElement('a');
    link.className = 'btn-secondary asignacion-maps';
    link.href = mapsUrl;
    link.target = '_blank';
    link.rel = 'noopener';
    conIcono(link, ICONO_MAPA_SVG, 'Cómo llegar');
    acciones.append(link);
  }

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn-primary';
  btn.textContent = etiquetaAccionCercano(p.campana);
  btn.addEventListener('click', () => onTomarPunto(p, btn));
  acciones.append(btn);

  card.append(acciones);
  return card;
}

// Claims a nearby point. Success: refreshes misPuntos/misPuntosPlaneacion
// and the cercanos list (the backend's own "available" definition is
// authoritative — never a hand-rolled local removal), surfaces the "también
// se te asignó X" outcome (mensajeTomarPunto — never swallowed), and hands
// the right instrument: a sticker claim jumps straight into THIS form
// (same as tapping "Registrar Sticker"); a survey claim opens the freshly
// prefilled Survey123 link when misPuntosPlaneacion's re-fetch has it,
// otherwise just switches to the Survey tab (fail open — never a dead
// link). Failure (lost race / already covered / network): shows
// mensajeErrorTomarPunto's message on the status line and refreshes the
// list so a stale card cannot be tapped again.
function mostrarErrorTomarPunto(msg) {
  const box = $('#cercanos-claim-error');
  box.textContent = msg;
  box.hidden = false;
}

function ocultarErrorTomarPunto() {
  $('#cercanos-claim-error').hidden = true;
}

async function onTomarPunto(p, btn) {
  if (btn) btn.disabled = true;
  ocultarErrorTomarPunto();
  // iOS Safari (and most mobile WebKit) only allows window.open() to succeed
  // when called synchronously inside the click handler — once the code below
  // crosses an `await`, the tab no longer counts as user-gesture-triggered
  // and the popup is silently blocked. Pre-open a blank tab HERE, still
  // inside the gesture, and redirect it once the survey URL is known.
  const surveyTab = p.campana !== 'sticker' ? window.open('', '_blank', 'noopener') : null;
  try {
    const res = await asignacionesApi({ action: 'tomarPunto', punto_id: p.id, campana: p.campana });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.ok) {
      if (surveyTab) surveyTab.close();
      // A DEDICATED, independent error line (`#cercanos-claim-error`) — NOT
      // `#cercanos-status`, which the cargarPuntosCercanos() refresh below
      // is about to overwrite with the section's own loading/empty/listo
      // state. The claim rejection must stay legible, not flash and vanish.
      mostrarErrorTomarPunto(mensajeErrorTomarPunto(body && body.detail));
      await cargarPuntosCercanos(); // the point is stale either way — drop it
      return;
    }

    await cargarMisPuntos();
    if (state.origenAsignaciones) await cargarPuntosCercanos();

    const aviso = mensajeTomarPunto(p.campana, body);
    if (aviso) window.alert(aviso); // eslint-disable-line no-alert -- do not swallow the "también asignado" outcome

    if (p.campana === 'sticker') {
      registrarSticker({ id: p.id, coords: p.coords, direccion: p.direccion });
      return;
    }
    // Survey: hand the freshly prefilled Survey123 link when available.
    const propio = state.puntosPlaneacion.find((x) => x.id === p.id);
    const encuestaUrl = propio ? elegirEnlaceEncuesta(propio, esDispositivoMovil()) : '';
    if (encuestaUrl && surveyTab) surveyTab.location.href = encuestaUrl;
    else if (encuestaUrl) window.open(encuestaUrl, '_blank', 'noopener');
    else if (surveyTab) surveyTab.close();
    state.tabAsigActiva = 'survey';
    renderAsignaciones();
  } catch (err) {
    if (surveyTab) surveyTab.close();
    console.warn('tomarPunto falló:', err);
    mostrarErrorTomarPunto(mensajeErrorTomarPunto(''));
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---- Geolocation for the assignments proximity sort --------------------------
// Same watchPosition/best-fix/battery-guard shape as requestLocation() below
// (reused on purpose — one geolocation strategy, not two), but scoped to the
// assignments picker: it starts as soon as that screen opens (before any
// point, or the blank form, is chosen) and re-sorts the lists as better
// fixes arrive. Fails OPEN, loudly but harmlessly: a denial or timeout never
// blocks the list, it only shows an inline note (#asig-geo-note) explaining
// why distances read as "—".

function requestLocationAsignaciones() {
  stopGeoAsigWatch();
  state.origenAsignaciones = null;
  ocultarNotaGeoAsignaciones();
  // `puntos-disponibles` change: reset the Cercanos tab's own state machine
  // too — a fresh visit to the picker re-requests location, and the
  // Cercanos fetch (GPS-gated) must re-arm with it, not stay stuck on
  // whatever it last showed.
  state.cercanosSolicitados = false;
  state.cercanosEstado = CERCANOS_ESPERANDO;
  renderCercanos();

  if (!('geolocation' in navigator)) {
    mostrarNotaGeoAsignaciones('Este dispositivo no soporta geolocalización; los puntos se muestran sin ordenar por cercanía.');
    // Binding requirement: Cercanos is HARD-gated on GPS — no fix is ever
    // possible on this device, so say so plainly instead of leaving the
    // tab on "Obteniendo tu ubicación…" forever.
    state.cercanosEstado = CERCANOS_SIN_GPS;
    renderCercanos();
    return;
  }

  state.geoAsigWatchId = navigator.geolocation.watchPosition(
    (pos) => {
      if (!state.origenAsignaciones || pos.coords.accuracy < state.origenAsignaciones.accuracy) {
        state.origenAsignaciones = {
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        };
        ocultarNotaGeoAsignaciones();
        // Re-sort with the better fix. renderAsignaciones() keeps
        // state.tabAsigActiva as-is (already set after the first render),
        // so this never yanks the inspector off the tab they are viewing.
        renderAsignaciones();
      }
      // Trigger the Cercanos fetch off the FIRST usable fix only — see
      // `cargarPuntosCercanos`'s own docstring for why every subsequent,
      // more-accurate fix does NOT re-fetch.
      if (!state.cercanosSolicitados) {
        state.cercanosSolicitados = true;
        cargarPuntosCercanos();
      }
      if (state.origenAsignaciones.accuracy <= GEO_ACCURACY_TARGET) {
        stopGeoAsigWatch();
      }
    },
    (err) => {
      // A fix already in hand is not undone by a later timeout/error.
      if (state.origenAsignaciones) return;
      mostrarNotaGeoAsignaciones(
        err && err.code === 1
          ? 'Ubicación no disponible: permiso denegado. Los puntos se muestran sin ordenar por cercanía.'
          : 'Ubicación no disponible por ahora. Los puntos se muestran sin ordenar por cercanía.',
      );
      // Permission denial is final (the browser will not re-prompt this
      // session) — Cercanos can never work, say so now rather than waiting
      // out the full battery-guard timeout below.
      if (err && err.code === 1) {
        state.cercanosEstado = CERCANOS_SIN_GPS;
        renderCercanos();
      }
    },
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 },
  );

  // Battery guard, same ceiling as the form's own watch. If no fix ever
  // landed, say so — never leave the note silently unexplained.
  state.geoAsigWatchTimer = setTimeout(() => {
    stopGeoAsigWatch();
    if (!state.origenAsignaciones) {
      mostrarNotaGeoAsignaciones('No se pudo obtener la ubicación a tiempo. Los puntos se muestran sin ordenar por cercanía.');
      state.cercanosEstado = CERCANOS_SIN_GPS;
      renderCercanos();
    }
  }, GEO_MAX_WATCH_MS);
}

function stopGeoAsigWatch() {
  if (state.geoAsigWatchId != null) {
    navigator.geolocation.clearWatch(state.geoAsigWatchId);
    state.geoAsigWatchId = null;
  }
  if (state.geoAsigWatchTimer) {
    clearTimeout(state.geoAsigWatchTimer);
    state.geoAsigWatchTimer = null;
  }
}

function mostrarNotaGeoAsignaciones(mensaje) {
  const nota = $('#asig-geo-note');
  nota.textContent = mensaje;
  nota.hidden = false;
}

function ocultarNotaGeoAsignaciones() {
  $('#asig-geo-note').hidden = true;
}

// ---- Geolocation ------------------------------------------------------------

function requestLocation() {
  const display = $('#geo-display');
  const errBox = $('#geo-error');
  errBox.hidden = true;

  if (!('geolocation' in navigator)) {
    display.textContent = '—';
    errBox.textContent = 'Este dispositivo no soporta geolocalización.';
    errBox.hidden = false;
    return;
  }

  // watchPosition instead of a one-shot getCurrentPosition: the first fix is
  // usually the coarse network one (hundreds of meters); the GPS refines over
  // the next seconds. We show every fix immediately (fast response) and keep
  // only the most accurate one (precision), stopping once it is good enough.
  stopGeoWatch();
  state.coords = null;
  display.textContent = 'Obteniendo ubicación…';

  const renderFix = (final) => {
    const c = state.coords;
    if (!c) { display.textContent = '—'; return; }
    const estado = final || c.accuracy <= GEO_ACCURACY_TARGET ? '' : ' · afinando…';
    display.textContent =
      `Lat: ${c.lat.toFixed(6)} · Lng: ${c.lng.toFixed(6)} · Precisión: ±${Math.round(c.accuracy)} m${estado}`;
  };

  state.geoWatchId = navigator.geolocation.watchPosition(
    (pos) => {
      // Keep the best fix seen so far, never a worse one.
      if (!state.coords || pos.coords.accuracy < state.coords.accuracy) {
        state.coords = {
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        };
        renderFix(false);
      }
      if (state.coords.accuracy <= GEO_ACCURACY_TARGET) {
        stopGeoWatch();
        renderFix(true);
      }
    },
    (err) => {
      renderFix(true);
      // A timeout with a fix already in hand is not an error worth showing.
      if (state.coords && err && err.code === 3) return;
      errBox.textContent = err && err.code === 1
        ? 'Permiso de ubicación denegado. Habilite la ubicación para este sitio e intente de nuevo.'
        : 'No se pudo obtener la ubicación. Intente de nuevo con "Actualizar ubicación".';
      errBox.hidden = false;
    },
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 },
  );

  // Battery guard: stop refining after a while; the best fix stays.
  state.geoWatchTimer = setTimeout(() => { stopGeoWatch(); renderFix(true); }, GEO_MAX_WATCH_MS);
}

function stopGeoWatch() {
  if (state.geoWatchId != null) {
    navigator.geolocation.clearWatch(state.geoWatchId);
    state.geoWatchId = null;
  }
  if (state.geoWatchTimer) {
    clearTimeout(state.geoWatchTimer);
    state.geoWatchTimer = null;
  }
}

// ---- Photos -----------------------------------------------------------------
// Dynamic dense array (see design "photo model"): no fixed slots. Two shared
// hidden inputs (gallery multi-select, camera single-shot) feed the same
// addFotos() entry point; renderFotos() rebuilds the grid from state so
// removing a photo never leaves a gap in the displayed order.

function wirePhotos() {
  $('#btn-foto-galeria').addEventListener('click', () => $('#foto-galeria').click());
  $('#btn-foto-camara').addEventListener('click', () => $('#foto-camara').click());
  $('#foto-galeria').addEventListener('change', (e) => {
    addFotos(e.target.files);
    e.target.value = ''; // allow re-selecting the same file later
  });
  $('#foto-camara').addEventListener('change', (e) => {
    addFotos(e.target.files);
    e.target.value = '';
  });
  renderFotos();
}

// Accepts as many files as fit in the remaining capacity; extras beyond
// MAX_FOTOS are silently dropped (per spec: gallery multi-select adds "up to
// the remaining slot capacity", not an error condition).
function addFotos(fileList) {
  const files = Array.from(fileList || []);
  for (const file of files) {
    if (!canAddSlot(state.fotos.length, MAX_FOTOS)) break;
    state.fotos.push({ file, previewUrl: URL.createObjectURL(file) });
  }
  renderFotos();
}

function removeFoto(index) {
  const [removed] = state.fotos.splice(index, 1);
  if (removed) URL.revokeObjectURL(removed.previewUrl);
  renderFotos();
}

function renderFotos() {
  const grid = $('#fotos-grid');
  grid.innerHTML = '';
  state.fotos.forEach((foto, i) => {
    const tile = document.createElement('div');
    tile.className = 'foto-tile';

    const img = document.createElement('img');
    img.src = foto.previewUrl;
    img.alt = `Vista previa de la foto ${i + 1}`;

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'foto-remove';
    removeBtn.textContent = 'Quitar';
    removeBtn.setAttribute('aria-label', `Quitar foto ${i + 1}`);
    removeBtn.addEventListener('click', () => removeFoto(i));

    tile.append(img, removeBtn);
    grid.append(tile);
  });

  const puedeAgregar = canAddSlot(state.fotos.length, MAX_FOTOS);
  $('#btn-foto-galeria').hidden = !puedeAgregar;
  $('#btn-foto-camara').hidden = !puedeAgregar;
  const maxMsg = $('#foto-max-msg');
  maxMsg.textContent = `Alcanzó el máximo de ${MAX_FOTOS} fotos.`;
  maxMsg.hidden = puedeAgregar;
}

function clearPhotos() {
  state.fotos.forEach((f) => URL.revokeObjectURL(f.previewUrl));
  state.fotos = [];
  $('#foto-galeria').value = '';
  $('#foto-camara').value = '';
  renderFotos();
}

// ---- Building code ----------------------------------------------------------

const SEGMENTO_ERRORES = {
  vacio: 'Ingrese el consecutivo de 4 dígitos.',
  longitud: 'El consecutivo debe tener exactamente 4 dígitos.',
  'no-numerico': 'El consecutivo debe contener solo números.',
  cero: 'El consecutivo no puede ser 0000.',
};

// Snapshot of #codigo-consecutivo's value taken when edit mode opens, so a
// cancel (button or Escape) can restore it exactly. Edit-session-scoped, not
// part of `state` — it has no meaning outside an open edit interaction.
let consecutivoPrevio = null;

// Records-derived next consecutive, session-cached. Runs the query at most
// once per session (state.maxConsecutivo starts null); every call after that
// just re-derives from the cached max with no round trip and NO mutation —
// state.maxConsecutivo only advances when a code is actually saved (see
// onSubmit's plegarConsecutivoGuardado fold). This is a pure read plus a
// pure arithmetic step; nothing is written to Firestore or to the cache
// here, so a generated-but-unsubmitted code never "consumes" a number for
// real, and calling this twice in a row without submitting yields the same
// value.
async function siguienteConsecutivoSesion() {
  if (state.maxConsecutivo == null) {
    const db = getDb();
    const q = query(collection(db, 'evaluaciones'), where('inspector.uid', '==', state.inspector.uid));
    const snap = await getDocs(q);
    const codigos = [];
    snap.forEach((d) => codigos.push(d.id));
    state.maxConsecutivo = siguienteConsecutivo(codigos, state.inspector.codigo) - 1;
    state.consecutivosUsados = consecutivosExistentes(codigos, state.inspector.codigo);
  }
  return siguienteDesdeMax(state.maxConsecutivo);
}

function renderCodigo(area, consecutivo) {
  state.area = area;
  state.codigo = buildCodigo(area, state.inspector.codigo, consecutivo);
  state.derivedConsecutivo = consecutivo;
  const valor = String(consecutivo).padStart(4, '0');
  $('#codigo-prefijo').textContent = `${MUNICIPIO}-${area}-${state.inspector.codigo}`;
  $('#codigo-consecutivo').value = valor;
  $('#codigo-consecutivo-texto').textContent = valor;
  $('#codigo-display').hidden = false;
  $('#codigo-hint').hidden = true;
  setCodigoEditMode(false);
}

// Re-validates the editable segment (on confirm/submit) and, if valid,
// rebuilds state.codigo from it (the prefix segments stay fixed) and syncs
// the display-mode text. A value below the derived next consecutive is
// accepted (gap-filling correction, not an error) but shows a non-blocking
// Spanish hint per spec "Editable Last-4-Digits Segment / Below-next edit is
// permitted with a hint".
function validarSegmentoInput() {
  const errBox = $('#codigo-error');
  const hintBox = $('#codigo-hint');
  const input = $('#codigo-consecutivo');
  const res = validarSegmento(input.value);
  if (!res.ok) {
    errBox.textContent = SEGMENTO_ERRORES[res.code] || 'Consecutivo inválido.';
    errBox.hidden = false;
    hintBox.hidden = true;
    return false;
  }
  // Hard stop on an edited value this inspector already saved: it can only
  // fail later at the create-only transaction (after the photo uploads), so
  // it is rejected here, at edit time. The derived value is never in the set
  // (it is max+1), so the automatic path is unaffected.
  if (res.value !== state.derivedConsecutivo && state.consecutivosUsados.has(res.value)) {
    errBox.textContent = `El consecutivo ${input.value} ya existe en sus registros. Use otro número.`;
    errBox.hidden = false;
    hintBox.hidden = true;
    return false;
  }
  errBox.hidden = true;
  state.codigo = buildCodigo(state.area, state.inspector.codigo, res.value);
  $('#codigo-consecutivo-texto').textContent = input.value;
  if (state.derivedConsecutivo != null && res.value < state.derivedConsecutivo) {
    hintBox.textContent = 'El consecutivo ingresado es menor al siguiente sugerido. Se acepta si es una corrección intencional.';
    hintBox.hidden = false;
  } else {
    hintBox.hidden = true;
  }
  return true;
}

// ---- Discreet edit affordance ------------------------------------------------
// Default state is display-only (unified code text + a small pencil button).
// Tapping the pencil swaps the consecutive segment for the real input,
// focused with its content selected, alongside confirm/cancel icon buttons.

function setCodigoEditMode(editing) {
  $('#codigo-consecutivo-texto').hidden = editing;
  $('#btn-codigo-editar').hidden = editing;
  $('#codigo-consecutivo').hidden = !editing;
  $('#btn-codigo-confirmar').hidden = !editing;
  $('#btn-codigo-cancelar').hidden = !editing;
  if (editing) {
    const input = $('#codigo-consecutivo');
    input.focus();
    input.select();
  }
}

function abrirEdicionConsecutivo() {
  consecutivoPrevio = $('#codigo-consecutivo').value;
  setCodigoEditMode(true);
}

// Applies the existing validation path; an invalid value keeps edit mode
// open (error already shown by validarSegmentoInput) instead of returning
// to display mode.
function confirmarConsecutivo() {
  if (!validarSegmentoInput()) return;
  setCodigoEditMode(false);
}

// Restores the value captured when edit mode opened and re-runs validation
// on it (always the last accepted value, so it just re-derives the
// error-free error/hint state) before returning to display mode.
function cancelarConsecutivo() {
  $('#codigo-consecutivo').value = consecutivoPrevio;
  validarSegmentoInput();
  setCodigoEditMode(false);
}

function onConsecutivoKeydown(e) {
  if (e.key === 'Enter') {
    e.preventDefault();
    confirmarConsecutivo();
  } else if (e.key === 'Escape') {
    e.preventDefault();
    cancelarConsecutivo();
  }
}

async function generarCodigo() {
  const areaSel = $('#area');
  const btn = $('#btn-codigo');
  const errBox = $('#codigo-error');
  errBox.hidden = true;

  const area = areaSel.value;
  if (!area) {
    errBox.textContent = 'Seleccione el área antes de generar el código.';
    errBox.hidden = false;
    return;
  }

  btn.disabled = true;
  try {
    if (!/^\d{3}$/.test(String(state.inspector.codigo))) {
      throw new Error('codigo-inspector-invalido');
    }
    const consecutivo = await siguienteConsecutivoSesion();
    renderCodigo(area, consecutivo);
    areaSel.disabled = true;
  } catch (err) {
    console.error(err);
    btn.disabled = false;
    errBox.textContent = err && err.message === 'codigo-inspector-invalido'
      ? 'El código de inspector no es válido (deben ser 3 dígitos). Contacte a la coordinación.'
      : 'No se pudo generar el código. Verifique la conexión e intente de nuevo.';
    errBox.hidden = false;
  }
}

// ---- Photo upload ------------------------------------------------------------

// Uploads all attached photos with a bounded worker pool (design: "photo
// model" / spec "Parallel Upload With Concurrency Cap"): at most `limit`
// requests in flight at any point, results collected index-ordered
// regardless of completion order. Cache key intentionally drops the slot —
// index-keyed caching would force re-upload of every surviving photo after a
// removal shifts the array. `slot` sent to the signer is still index-based
// (1..MAX_FOTOS) since the signer's request schema requires it.
async function subirFotos(fotos, limit = MAX_FOTOS) {
  const idToken = await getAuth(getApp()).currentUser.getIdToken();
  const urls = new Array(fotos.length);
  let next = 0;

  async function worker() {
    while (next < fotos.length) {
      const i = next++;
      urls[i] = await subirUnaFoto(fotos[i].file, i + 1, idToken);
    }
  }

  const workers = Array.from({ length: Math.min(limit, fotos.length) }, worker);
  await Promise.all(workers);
  return urls;
}

async function subirUnaFoto(file, slot, idToken) {
  const key = `${state.codigo}:${file.name}:${file.size}:${file.lastModified}`;
  if (!state.fotosSubidas[key] && window.__fotosMock) {
    // demo.html only: skip the network, fake the stored URL.
    state.fotosSubidas[key] = `https://demo.invalid/evaluaciones/${state.codigo}/foto_${slot}.jpg`;
  }
  if (!state.fotosSubidas[key]) {
    const sr = await fetch(FOTO_SIGNER_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idToken, codigo: state.codigo, slot }),
    });
    if (!sr.ok) {
      // Defensive fallback (design D3): the deployed signer rejects slot >
      // MAX_FOTOS with a 400 before checking the token. MAX_FOTOS already
      // caps the client-constructed slot at this value so this should be
      // unreachable in normal use, but surfaces a specific message instead
      // of the generic upload error if it ever happens.
      if (sr.status === 400 && slot > MAX_FOTOS) throw new Error('signer-slot-limit');
      throw new Error(`sign-${sr.status}`);
    }
    const { uploadUrl, publicUrl } = await sr.json();
    const up = await fetch(uploadUrl, {
      method: 'PUT',
      headers: { 'Content-Type': 'image/jpeg' },
      body: file,
    });
    if (!up.ok) throw new Error(`put-${up.status}`);
    state.fotosSubidas[key] = publicUrl;
  }
  return state.fotosSubidas[key];
}

// ---- Submit -----------------------------------------------------------------

function showSubmitError(msg) {
  const box = $('#submit-error');
  box.textContent = msg;
  box.hidden = !msg;
  if (msg) box.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function setSubmitBusy(busy) {
  const btn = $('#btn-submit');
  btn.disabled = busy;
  btn.textContent = busy ? 'Enviando…' : 'Enviar evaluación';
}

function validate() {
  if (!state.codigo) return 'Genere el código de la edificación antes de enviar.';
  if (!state.coords) return 'Falta la ubicación. Use "Actualizar ubicación" antes de enviar.';
  if (state.fotos.length === 0) return 'Agregue al menos una foto de la edificación antes de enviar.';
  const form = $('#eval-form');
  if (!form.checkValidity()) {
    form.reportValidity(); // native messages for criterios/clasificación/alcance
    return 'Complete los campos obligatorios señalados.';
  }
  return null;
}

async function onSubmit(e) {
  e.preventDefault();
  showSubmitError('');

  if (state.codigo && !validarSegmentoInput()) {
    showSubmitError('Corrija el consecutivo del código antes de enviar.');
    return;
  }

  const invalid = validate();
  if (invalid) { showSubmitError(invalid); return; }

  setSubmitBusy(true);
  try {
    const db = getDb();

    // Friendly early guard: catch an existing code before spending time on
    // photo uploads. The create-only transaction below is the authoritative,
    // fail-closed backstop (also catches a race between two devices).
    const preSnap = await getDoc(doc(db, 'evaluaciones', state.codigo));
    if (preSnap.exists()) throw new Error('codigo-duplicado');

    // Upload photos to S3 first (signer presigns per photo); URLs go inside
    // the evaluation doc. Successful uploads are cached so a retry after a
    // failed doc write skips them. Cache key drops the slot (design: "photo
    // model") — removing a photo shifts every later index, and a slot-keyed
    // cache would force re-upload of every surviving photo on retry.
    let fotos;
    try {
      fotos = await subirFotos(state.fotos);
    } catch (err) {
      console.error(err);
      if (err && /^signer-slot-limit$/.test(err.message)) throw err;
      throw new Error('foto-upload');
    }

    const data = {
      codigo_edificacion: state.codigo,
      consecutivo: parseConsecutivo(state.codigo, state.inspector.codigo),
      municipio: MUNICIPIO,
      area: Number($('#area').value),
      area_nombre: AREA_NOMBRES[Number($('#area').value)],
      inspector: {
        uid: state.inspector.uid,
        codigo: state.inspector.codigo,
        nombre_completo: state.inspector.nombre_completo || '',
        identificacion: state.inspector.identificacion || '',
        entidad: state.inspector.entidad || '',
      },
      fecha_hora_dispositivo: new Date().toISOString(),
      timestamp: serverTimestamp(),
      coords: state.coords,
      alcance: document.querySelector('input[name="alcance"]:checked').value,
      clasificacion: document.querySelector('input[name="clasificacion"]:checked').value,
      descripcion: {
        nombre: $('#nombre').value.trim(),
        direccion: $('#direccion').value.trim(),
      },
      restricciones: $('#restricciones').value.trim(),
      acciones_posteriores: {
        barricadas: $('#barricadas').checked,
        evaluacion_detallada: $('#evaluacion_detallada').checked,
      },
      comentarios: $('#comentarios').value.trim(),
      fotos,
    };

    // Traceability back to the assigned point (when this record came from one).
    if (state.asignacion) data.sticker_match_id = state.asignacion.id;
    // planeacion_cruce.py's evaluaciones cross-reference (module docstring's
    // "Camino formulario ATC-20 / stickers") keys off this exact field.
    if (state.asignacion && state.asignacion.clave_integracion) data.clave_integracion = state.asignacion.clave_integracion;

    // Create-only: the transaction fails if the doc already exists.
    const evalRef = doc(db, 'evaluaciones', state.codigo);
    await runTransaction(db, async (tx) => {
      const snap = await tx.get(evalRef);
      if (snap.exists()) throw new Error('codigo-duplicado');
      tx.set(evalRef, data);
    });

    // Fold the ACTUALLY saved consecutive into the session cache (max(), not
    // a blind assignment) so the next generated code derives from what was
    // really written — including a manual edit — and a gap-filling
    // correction (saved value below the current max) never drags the known
    // max backwards.
    state.maxConsecutivo = plegarConsecutivoGuardado(state.maxConsecutivo, data.consecutivo);
    // The number just saved is now taken: the duplicate guard must reject it
    // if the inspector edits a later code back onto it in this same session.
    state.consecutivosUsados.add(data.consecutivo);

    // Best-effort: flip the assigned point to 'hecho' via the dashboard
    // endpoint and drop it from the pending list so the picker shows the next
    // one. The evaluación is already saved regardless of this call's outcome
    // — but a failure here means the sticker case stays open with no other
    // signal, so it's surfaced on the confirm screen (not just console.warn)
    // instead of silently swallowed; the next cruce_sticker.py run will
    // still pick up the flip from the new evaluación either way.
    const warnEl = $('#confirm-sticker-warn');
    if (warnEl) warnEl.hidden = true;
    if (state.asignacion) {
      try {
        await asignacionesApi({ action: 'marcarHecho', punto_id: state.asignacion.id });
      } catch (err) {
        console.warn('No se pudo marcar el punto asignado como hecho:', err);
        if (warnEl) {
          warnEl.textContent = 'La evaluación se guardó, pero no se pudo cerrar el caso del sticker automáticamente. Se cerrará solo en la próxima sincronización.';
          warnEl.hidden = false;
        }
      }
      state.asignaciones = state.asignaciones.filter((a) => a.id !== state.asignacion.id);
    }

    $('#confirm-codigo').textContent = state.codigo;
    $('#app').hidden = true;
    $('#confirm').hidden = false;
    window.scrollTo(0, 0);
  } catch (err) {
    console.error(err);
    if (err && err.message === 'codigo-duplicado') {
      // Recover without wiping the form: invalidate the session cache,
      // re-derive against the latest records, and prefill a fresh code.
      // Area stays locked and all entered data/photos survive — the
      // inspector only needs to review the new code and resend.
      state.maxConsecutivo = null;
      try {
        const consecutivo = await siguienteConsecutivoSesion();
        renderCodigo(state.area, consecutivo);
      } catch (deriveErr) {
        console.error(deriveErr);
      }
      showSubmitError('El código ya existe. Se generó uno nuevo automáticamente; revise y envíe de nuevo.');
    } else if (err && err.message === 'signer-slot-limit') {
      showSubmitError(`Este dispositivo solo admite ${MAX_FOTOS} fotos por registro.`);
    } else if (err && err.message === 'foto-upload') {
      showSubmitError('No se pudieron subir las fotos. Verifique la conexión, o quite las fotos y envíe sin ellas (los demás datos se conservan).');
    } else {
      showSubmitError('No se pudo enviar la evaluación. Verifique la conexión e intente de nuevo (los datos se conservan).');
    }
  } finally {
    setSubmitBusy(false);
  }
}

// ---- New record -------------------------------------------------------------

function nuevoRegistro() {
  $('#eval-form').reset();
  clearPhotos();
  state.codigo = null;
  state.area = null;
  state.fotosSubidas = {};
  // Drop stale coords before requesting fresh ones. state.maxConsecutivo is
  // intentionally kept: it is a session-scoped cache, not a per-record one —
  // the next record in the same session should not re-query Firestore.
  state.coords = null;
  $('#geo-display').textContent = 'Obteniendo ubicación…';

  const areaSel = $('#area');
  areaSel.disabled = false;
  $('#btn-codigo').disabled = false;
  $('#codigo-prefijo').textContent = '';
  $('#codigo-consecutivo').value = '';
  $('#codigo-consecutivo-texto').textContent = '';
  $('#codigo-display').hidden = true;
  $('#codigo-error').hidden = true;
  $('#codigo-hint').hidden = true;
  setCodigoEditMode(false);
  consecutivoPrevio = null;
  state.derivedConsecutivo = null;
  showSubmitError('');

  state.asignacion = null;
  window.scrollTo(0, 0);

  // If assigned points remain in EITHER tab, go back to the picker for the
  // next one — a sticker-only check here would strand an inspector with
  // pending Survey points on a blank form. Bug fix: the original single-tab
  // version only checked state.asignaciones (stickers), silently skipping
  // the picker whenever only Planeación points remained.
  if (state.asignaciones.length > 0 || state.puntosPlaneacion.length > 0) {
    // Fresh visit to the picker: let renderAsignaciones() re-pick whichever
    // tab actually has work now (the just-finished sticker may have emptied
    // its tab) instead of sticking to wherever the inspector was before.
    state.tabAsigActiva = null;
    requestLocationAsignaciones();
    renderAsignaciones();
    return;
  }
  mostrarFormulario();
}
