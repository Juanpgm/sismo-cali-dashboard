// Authentication gate for the ATC-20 field form.
//
// Email/password only. After sign-in the Firestore doc inspectores/{uid} must
// exist; otherwise the session is rejected (the user is not a field inspector).
// Inspectors are created by hand in the Firebase console (see SETUP.md).

import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js';
import {
  getAuth, setPersistence, browserLocalPersistence,
  signInWithEmailAndPassword, onAuthStateChanged, signOut,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js';
import {
  getFirestore, doc, getDoc,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js';
import { firebaseConfig, isConfigured } from './firebase-config.js';

let app = null;
let auth = null;
let db = null;
let inspector = null; // { uid, email, nombre_completo, identificacion, entidad, codigo, profesion, ... }

export const getApp = () => app;
export const getDb = () => db;
export const getInspector = () => inspector;

export async function signOutUser() {
  try {
    if (auth) await signOut(auth);
  } finally {
    window.location.reload();
  }
}

// ---- Login overlay ----------------------------------------------------------

function buildOverlay() {
  const root = document.createElement('div');
  root.className = 'auth-overlay';
  root.id = 'auth-overlay';
  root.innerHTML = `
    <div class="auth-card" role="dialog" aria-modal="true" aria-labelledby="auth-title">
      <h1 id="auth-title">Evaluación Rápida ATC-20 · Cali</h1>
      <p class="auth-sub">Acceso restringido · Inspectores de campo</p>
      <p class="auth-lead">Ingrese con su usuario y contraseña para registrar evaluaciones.</p>

      <form class="auth-form" id="auth-form" novalidate>
        <label class="auth-field">
          <span>Correo</span>
          <input type="email" id="auth-email" autocomplete="username" required placeholder="inspector@ejemplo.com">
        </label>
        <label class="auth-field">
          <span>Contraseña</span>
          <input type="password" id="auth-password" autocomplete="current-password" required placeholder="••••••••">
        </label>
        <button type="submit" class="auth-submit" id="auth-submit">Ingresar</button>
      </form>

      <p class="auth-error" id="auth-error" role="alert" hidden></p>
      <p class="auth-foot">Solo personal autorizado.</p>
    </div>`;
  document.body.appendChild(root);
  return root;
}

function showError(overlay, msg) {
  const box = overlay.querySelector('#auth-error');
  box.textContent = msg;
  box.hidden = !msg;
}

function setBusy(overlay, busy) {
  overlay.querySelectorAll('button, input').forEach((el) => { el.disabled = busy; });
  overlay.classList.toggle('is-busy', busy);
}

function friendlyError(err) {
  const code = err && err.code ? err.code : '';
  switch (code) {
    case 'auth/invalid-credential':
    case 'auth/wrong-password':
    case 'auth/user-not-found':
      return 'Correo o contraseña incorrectos.';
    case 'auth/invalid-email':
      return 'El correo no tiene un formato válido.';
    case 'auth/too-many-requests':
      return 'Demasiados intentos. Espere un momento e intente de nuevo.';
    case 'auth/network-request-failed':
      return 'Sin conexión. Verifique la señal e intente de nuevo.';
    case 'auth/unauthorized-domain':
      return 'Este dominio no está autorizado en Firebase (Authentication → Settings → Authorized domains).';
    default:
      return (err && err.message) || 'No se pudo iniciar sesión.';
  }
}

// ---- Signed-in header bar ---------------------------------------------------

function mountHeader(profile) {
  const bar = document.getElementById('inspector-bar');
  if (!bar) return;
  bar.hidden = false;
  bar.querySelector('#inspector-nombre').textContent = profile.nombre_completo || profile.email || '';
  bar.querySelector('#inspector-entidad').textContent = profile.entidad || '';
  bar.querySelector('#inspector-logout').addEventListener('click', () => signOutUser());
}

/**
 * Initialize auth and gate the form.
 * @param {(inspector: object) => void} onFirstAuthorized called ONCE with the
 *   inspector profile the first time a registered inspector signs in.
 */
export function initAuth(onFirstAuthorized) {
  const overlay = buildOverlay();

  if (!isConfigured()) {
    overlay.querySelector('.auth-lead').textContent =
      'Falta configurar Firebase: pegue sus credenciales en formulario/js/firebase-config.js.';
    setBusy(overlay, true);
    return;
  }

  app = initializeApp(firebaseConfig);
  auth = getAuth(app);
  db = getFirestore(app);
  setPersistence(auth, browserLocalPersistence).catch((err) => { console.error(err); });

  overlay.querySelector('#auth-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    showError(overlay, '');
    const email = overlay.querySelector('#auth-email').value.trim();
    const password = overlay.querySelector('#auth-password').value;
    if (!email || !password) { showError(overlay, 'Ingrese correo y contraseña.'); return; }
    setBusy(overlay, true);
    try {
      await signInWithEmailAndPassword(auth, email, password);
    } catch (err) {
      // ponytail: console-only diagnostics; add real telemetry if field failure rates need visibility.
      console.error(err);
      setBusy(overlay, false);
      showError(overlay, friendlyError(err));
    }
  });

  let booted = false;
  onAuthStateChanged(auth, async (user) => {
    if (!user) {
      inspector = null;
      const form = overlay.querySelector('#auth-form');
      if (form) form.reset();
      showError(overlay, '');
      setBusy(overlay, false);
      overlay.style.display = '';
      overlay.classList.remove('is-authed');
      return;
    }

    // The profile doc gates access: no doc, no form.
    let snap = null;
    try {
      snap = await getDoc(doc(db, 'inspectores', user.uid));
    } catch (err) {
      console.error(err);
      await signOut(auth);
      setBusy(overlay, false);
      showError(overlay, 'No se pudo verificar el perfil de inspector. Intente de nuevo.');
      return;
    }
    if (!snap.exists()) {
      await signOut(auth);
      setBusy(overlay, false);
      showError(overlay, 'No está registrado como inspector. Contacte a la coordinación.');
      return;
    }

    inspector = { uid: user.uid, email: user.email || '', ...snap.data() };
    mountHeader(inspector);
    overlay.classList.add('is-authed');
    setBusy(overlay, false);
    setTimeout(() => { overlay.style.display = 'none'; }, 350);

    if (!booted) { booted = true; onFirstAuthorized(inspector); }
  });
}
