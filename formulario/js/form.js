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
} from './logic.js';

// Serverless signer that validates the Firebase idToken and presigns the
// S3 upload (photos live in S3, not Firebase Storage).
const FOTO_SIGNER_URL = 'https://sismo-fotos-signer.vercel.app/api/sign';

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
  // The consecutive value last rendered by generarCodigo/renderCodigo (before
  // any manual edit). Used only to decide whether an edited segment is below
  // the derived next value (non-blocking hint, no floor enforced).
  derivedConsecutivo: null,
  fotos: [],                // { file, previewUrl }[] — dense array, max MAX_FOTOS
  fotosSubidas: {},         // "codigo:name:size:lastModified" -> downloadURL (upload retry cache)
};

const $ = (sel) => document.querySelector(sel);

initAuth(boot);

function boot(inspector) {
  state.inspector = inspector;
  $('#app').hidden = false;

  requestLocation();
  $('#btn-geo').addEventListener('click', requestLocation);

  wirePhotos();

  $('#btn-codigo').addEventListener('click', generarCodigo);
  $('#codigo-consecutivo').addEventListener('blur', validarSegmentoInput);

  $('#eval-form').addEventListener('submit', onSubmit);
  $('#btn-nuevo').addEventListener('click', nuevoRegistro);
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

// Records-derived next consecutive, session-cached. Runs the query at most
// once per session (state.maxConsecutivo starts null); every call after that
// just bumps the cached max locally with no round trip. This is a pure read
// plus in-memory bookkeeping — nothing is written to Firestore here, so a
// generated-but-unsubmitted code never "consumes" a number for real.
async function siguienteConsecutivoSesion() {
  if (state.maxConsecutivo == null) {
    const db = getDb();
    const q = query(collection(db, 'evaluaciones'), where('inspector.uid', '==', state.inspector.uid));
    const snap = await getDocs(q);
    const codigos = [];
    snap.forEach((d) => codigos.push(d.id));
    state.maxConsecutivo = siguienteConsecutivo(codigos, state.inspector.codigo) - 1;
  }
  state.maxConsecutivo += 1;
  return state.maxConsecutivo;
}

function renderCodigo(area, consecutivo) {
  state.area = area;
  state.codigo = buildCodigo(area, state.inspector.codigo, consecutivo);
  state.derivedConsecutivo = consecutivo;
  $('#codigo-prefijo').textContent = `${MUNICIPIO}-${area}-${state.inspector.codigo}`;
  $('#codigo-consecutivo').value = String(consecutivo).padStart(4, '0');
  $('#codigo-display').hidden = false;
  $('#codigo-hint').hidden = true;
}

// Re-validates the editable segment on blur/submit and, if valid, rebuilds
// state.codigo from it (the prefix segments stay fixed). A value below the
// derived next consecutive is accepted (gap-filling correction, not an
// error) but shows a non-blocking Spanish hint per spec "Editable
// Last-4-Digits Segment / Below-next edit is permitted with a hint".
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
  errBox.hidden = true;
  state.codigo = buildCodigo(state.area, state.inspector.codigo, res.value);
  if (state.derivedConsecutivo != null && res.value < state.derivedConsecutivo) {
    hintBox.textContent = 'El consecutivo ingresado es menor al siguiente sugerido. Se acepta si es una corrección intencional.';
    hintBox.hidden = false;
  } else {
    hintBox.hidden = true;
  }
  return true;
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

    // Create-only: the transaction fails if the doc already exists.
    const evalRef = doc(db, 'evaluaciones', state.codigo);
    await runTransaction(db, async (tx) => {
      const snap = await tx.get(evalRef);
      if (snap.exists()) throw new Error('codigo-duplicado');
      tx.set(evalRef, data);
    });

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
  $('#codigo-display').hidden = true;
  $('#codigo-error').hidden = true;
  $('#codigo-hint').hidden = true;
  state.derivedConsecutivo = null;
  showSubmitError('');

  $('#confirm').hidden = true;
  $('#app').hidden = false;
  window.scrollTo(0, 0);
  requestLocation();
}
