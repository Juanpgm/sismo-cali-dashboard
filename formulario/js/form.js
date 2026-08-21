// ATC-20 field form logic: geolocation, photos, unique building code and
// create-only submit. Boots only after auth.js confirms a registered inspector.

import { initAuth, getApp, getDb } from './auth.js';
import {
  doc, runTransaction, serverTimestamp,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js';
import { getAuth } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js';
import { MUNICIPIO, buildCodigo } from './logic.js';

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
  codigo: null,            // generated building code, e.g. "76001-1-0040001"
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
    const db = getDb();
    const insRef = doc(db, 'inspectores', state.inspector.uid);
    // Transaction: read consecutivo, increment, write back — no duplicates
    // even with the same account on two devices.
    const codigo = await runTransaction(db, async (tx) => {
      const snap = await tx.get(insRef);
      if (!snap.exists()) throw new Error('perfil-no-encontrado');
      const actual = Number(snap.data().consecutivo);
      const consecutivo = (Number.isFinite(actual) ? actual : 0) + 1;
      tx.update(insRef, { consecutivo });
      return buildCodigo(area, state.inspector.codigo, consecutivo);
    });

    state.codigo = codigo;
    areaSel.disabled = true;
    const display = $('#codigo-display');
    display.textContent = codigo;
    display.hidden = false;
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

  const invalid = validate();
  if (invalid) { showSubmitError(invalid); return; }

  setSubmitBusy(true);
  try {
    const db = getDb();

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
      // Recover: force a fresh code, keep the rest of the entered data.
      state.codigo = null;
      state.fotosSubidas = {};
      const display = $('#codigo-display');
      display.textContent = '';
      display.hidden = true;
      $('#btn-codigo').disabled = false;
      $('#area').disabled = false;
      showSubmitError('El código ya existe. Genere un nuevo código e intente de nuevo.');
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
  state.fotosSubidas = {};
  // Drop stale coords before requesting fresh ones.
  state.coords = null;
  $('#geo-display').textContent = 'Obteniendo ubicación…';

  const areaSel = $('#area');
  areaSel.disabled = false;
  $('#btn-codigo').disabled = false;
  const display = $('#codigo-display');
  display.textContent = '';
  display.hidden = true;
  $('#codigo-error').hidden = true;
  showSubmitError('');

  $('#confirm').hidden = true;
  $('#app').hidden = false;
  window.scrollTo(0, 0);
  requestLocation();
}
