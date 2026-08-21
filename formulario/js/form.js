// ATC-20 field form logic: geolocation, photos, unique building code and
// create-only submit. Boots only after auth.js confirms a registered inspector.

import { initAuth, getApp, getDb } from './auth.js';
import {
  doc, runTransaction, serverTimestamp,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js';
import {
  getStorage, ref, uploadBytes, getDownloadURL,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js';
import { MUNICIPIO, buildCodigo } from './logic.js';

// Signal the inline CDN-failure watchdog in index.html that modules loaded.
window.__atc20Booted = true;
const bootStatus = document.getElementById('boot-status');
if (bootStatus) bootStatus.remove();

const AREA_NOMBRES = { 1: 'Cabecera', 2: 'Centro Poblado', 3: 'Rural Disperso' };

const state = {
  inspector: null,
  coords: null,            // { lat, lng, accuracy }
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

  display.textContent = 'Obteniendo ubicación…';
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      state.coords = {
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
      };
      display.textContent =
        `Lat: ${state.coords.lat.toFixed(6)} · Lng: ${state.coords.lng.toFixed(6)} · Precisión: ${Math.round(state.coords.accuracy)} m`;
    },
    (err) => {
      display.textContent = state.coords
        ? `Lat: ${state.coords.lat.toFixed(6)} · Lng: ${state.coords.lng.toFixed(6)} · Precisión: ${Math.round(state.coords.accuracy)} m`
        : '—';
      errBox.textContent = err && err.code === 1
        ? 'Permiso de ubicación denegado. Habilite la ubicación para este sitio e intente de nuevo.'
        : 'No se pudo obtener la ubicación. Intente de nuevo con "Actualizar ubicación".';
      errBox.hidden = false;
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
  );
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
    const storage = getStorage(getApp());

    // Upload photos first; URLs go inside the evaluation doc. Successful
    // uploads are cached so a retry after a failed doc write skips them.
    // ponytail: photos uploaded before the doc write can be orphaned if the form is abandoned; bounded to 3 files per code, clean manually if it ever matters.
    const fotos = [];
    for (let slot = 0; slot < state.fotos.length; slot++) {
      const file = state.fotos[slot];
      if (!file) continue;
      // Key by slot + file identity so removing/replacing a photo between a
      // failed submit and the retry never reuses a stale cached URL.
      const key = `${state.codigo}:${slot}:${file.name}:${file.size}:${file.lastModified}`;
      if (!state.fotosSubidas[key]) {
        const fotoRef = ref(storage, `evaluaciones/${state.codigo}/foto_${slot + 1}.jpg`);
        await uploadBytes(fotoRef, file);
        state.fotosSubidas[key] = await getDownloadURL(fotoRef);
      }
      fotos.push(state.fotosSubidas[key]);
    }

    const num = (id) => {
      const v = $(id).value;
      return v === '' ? null : Number(v);
    };

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
        n_pisos: num('#n_pisos'),
        n_unidades: num('#n_unidades'),
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
