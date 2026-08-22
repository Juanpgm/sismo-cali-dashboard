// ATC-20 field form logic: geolocation, photos, unique building code and
// create-only submit. Boots only after auth.js confirms a registered inspector.

import { initAuth, getApp, getDb } from './auth.js';
import {
  collection, doc, getDoc, getDocs, query, runTransaction, serverTimestamp, where,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js';
import { getAuth } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js';
import {
  MUNICIPIO, buildCodigo, parseConsecutivo, siguienteConsecutivo, validarSegmento,
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
  fotos: [null, null, null], // File | null per slot
  fotosSubidas: {},        // "codigo:slot" -> downloadURL (upload retry cache)
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

function wirePhotos() {
  document.querySelectorAll('.foto-slot').forEach((slot, i) => {
    const input = slot.querySelector('input[type="file"]');
    const img = slot.querySelector('img');
    const removeBtn = slot.querySelector('.foto-remove');

    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      if (!file) return;
      state.fotos[i] = file;
      if (img.src) URL.revokeObjectURL(img.src);
      img.src = URL.createObjectURL(file);
      img.hidden = false;
      removeBtn.hidden = false;
    });

    removeBtn.addEventListener('click', () => {
      state.fotos[i] = null;
      input.value = '';
      if (img.src) URL.revokeObjectURL(img.src);
      img.removeAttribute('src');
      img.hidden = true;
      removeBtn.hidden = true;
    });
  });
}

function clearPhotos() {
  document.querySelectorAll('.foto-slot').forEach((slot, i) => {
    state.fotos[i] = null;
    slot.querySelector('input[type="file"]').value = '';
    const img = slot.querySelector('img');
    if (img.src) URL.revokeObjectURL(img.src);
    img.removeAttribute('src');
    img.hidden = true;
    slot.querySelector('.foto-remove').hidden = true;
  });
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
  $('#codigo-prefijo').textContent = `${MUNICIPIO}-${area}-${state.inspector.codigo}`;
  $('#codigo-consecutivo').value = String(consecutivo).padStart(4, '0');
  $('#codigo-display').hidden = false;
}

// Re-validates the editable segment on blur/submit and, if valid, rebuilds
// state.codigo from it (the prefix segments stay fixed).
function validarSegmentoInput() {
  const errBox = $('#codigo-error');
  const input = $('#codigo-consecutivo');
  const res = validarSegmento(input.value);
  if (!res.ok) {
    errBox.textContent = SEGMENTO_ERRORES[res.code] || 'Consecutivo inválido.';
    errBox.hidden = false;
    return false;
  }
  errBox.hidden = true;
  state.codigo = buildCodigo(state.area, state.inspector.codigo, res.value);
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
  if (!state.fotos.some(Boolean)) return 'Agregue al menos una foto de la edificación antes de enviar.';
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
    // failed doc write skips them.
    // ponytail: photos uploaded before the doc write can be orphaned if the form is abandoned; bounded to 3 files per code, clean manually if it ever matters.
    const fotos = [];
    try {
      const idToken = await getAuth(getApp()).currentUser.getIdToken();
      for (let slot = 0; slot < state.fotos.length; slot++) {
        const file = state.fotos[slot];
        if (!file) continue;
        // Key by slot + file identity so removing/replacing a photo between a
        // failed submit and the retry never reuses a stale cached URL.
        const key = `${state.codigo}:${slot}:${file.name}:${file.size}:${file.lastModified}`;
        if (!state.fotosSubidas[key] && window.__fotosMock) {
          // demo.html only: skip the network, fake the stored URL.
          state.fotosSubidas[key] = `https://demo.invalid/evaluaciones/${state.codigo}/foto_${slot + 1}.jpg`;
        }
        if (!state.fotosSubidas[key]) {
          const sr = await fetch(FOTO_SIGNER_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ idToken, codigo: state.codigo, slot: slot + 1 }),
          });
          if (!sr.ok) throw new Error(`sign-${sr.status}`);
          const { uploadUrl, publicUrl } = await sr.json();
          const up = await fetch(uploadUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'image/jpeg' },
            body: file,
          });
          if (!up.ok) throw new Error(`put-${up.status}`);
          state.fotosSubidas[key] = publicUrl;
        }
        fotos.push(state.fotosSubidas[key]);
      }
    } catch (err) {
      console.error(err);
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
  showSubmitError('');

  $('#confirm').hidden = true;
  $('#app').hidden = false;
  window.scrollTo(0, 0);
  requestLocation();
}
