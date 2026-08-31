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
  getFirestore, doc, getDoc, getDocs, collection, query, where, runTransaction, serverTimestamp,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js';
import { firebaseConfig, isConfigured } from './firebase-config.js';
import { cedulaToEmail, clasificarErrorFirestore, backoffDelay } from './logic.js';

// Single Firebase boundary (design "import dedupe"): form.js imports every
// Firestore/Auth primitive it needs from here instead of the gstatic CDN
// directly, so there is one seam to intercept in the e2e mock and one place
// that owns the SDK imports.
export {
  getAuth, doc, getDoc, getDocs, collection, query, where, runTransaction, serverTimestamp,
};

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
      <p class="auth-lead">Ingrese con su cédula y contraseña para registrar evaluaciones.</p>

      <form class="auth-form" id="auth-form" novalidate>
        <label class="auth-field">
          <span>Cédula</span>
          <input type="text" id="auth-email" inputmode="numeric" autocomplete="username" required placeholder="1110547406">
        </label>
        <label class="auth-field">
          <span>Contraseña</span>
          <input type="password" id="auth-password" autocomplete="current-password" required placeholder="••••••••">
        </label>
        <button type="submit" class="auth-submit" id="auth-submit">Ingresar</button>
      </form>

      <p class="auth-error" id="auth-error" role="alert" hidden></p>
      <button type="button" class="auth-retry" id="auth-retry" hidden>Reintentar</button>
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

function showRetry(overlay, show) {
  overlay.querySelector('#auth-retry').hidden = !show;
}

function sleep(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

// Retries a Firestore call up to 3 attempts with backoff, but ONLY for
// genuine Firestore errors (transient per clasificarErrorFirestore, e.g. a
// `resource-exhausted` quota 429 — this project's recurring failure mode).
// Anything without a `.code` (a plain app error, like onSubmit's own
// 'codigo-duplicado') is not a Firestore error at all and is re-thrown
// immediately — retrying it would never succeed and would only delay a
// result the caller already knows how to handle. Originally just the
// inspector-profile read below; generalized so form.js's own unretried
// getDoc/getDocs/runTransaction calls (code generation, submit) share the
// same resilience instead of surfacing quota exhaustion as a generic
// "no se pudo enviar" error.
export async function retryTransient(fn) {
  let lastErr;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      // eslint-disable-next-line no-await-in-loop -- retries are inherently sequential
      return await fn();
    } catch (err) {
      lastErr = err;
      if (!err || !err.code || clasificarErrorFirestore(err) === 'fatal') throw err;
      // eslint-disable-next-line no-await-in-loop -- backoff between retries is sequential by design
      if (attempt < 3) await sleep(backoffDelay(attempt));
    }
  }
  throw lastErr;
}

// Reads the inspector-profile doc with up to 3 attempts, retrying only
// transient failures with backoff. A fatal classification (permission-denied,
// not-found) is re-thrown immediately — retrying it would never succeed and
// would only delay the sign-out.
async function readProfileWithRetry(db, uid) {
  return retryTransient(() => getDoc(doc(db, 'inspectores', uid)));
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
    const cedula = overlay.querySelector('#auth-email').value.trim();
    const password = overlay.querySelector('#auth-password').value;
    if (!cedula || !password) { showError(overlay, 'Ingrese la cédula y la contraseña.'); return; }
    const email = cedulaToEmail(cedula);
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
  let retryUser = null; // set only while the retry affordance is shown

  // The profile doc gates access: no doc, no form. Split out so the
  // "Reintentar" button can re-run exactly this check for the same user.
  async function checkProfile(user) {
    let snap = null;
    try {
      snap = await readProfileWithRetry(db, user.uid);
    } catch (err) {
      console.error(err);
      if (clasificarErrorFirestore(err) === 'fatal') {
        await signOut(auth);
        setBusy(overlay, false);
        showError(overlay, 'No se pudo verificar el perfil de inspector. Intente de nuevo.');
        return;
      }
      // Transient and retries exhausted: do NOT sign out (the field-logout
      // bug this feature exists to fix). Offer a manual retry instead.
      retryUser = user;
      setBusy(overlay, false);
      showError(overlay, 'No se pudo verificar el perfil de inspector por una falla de conexión.');
      showRetry(overlay, true);
      return;
    }
    retryUser = null;
    showRetry(overlay, false);
    if (!snap.exists()) {
      await signOut(auth);
      setBusy(overlay, false);
      showError(overlay, 'No está registrado como inspector. Contacte a la coordinación.');
      return;
    }
    // Disabled from the dashboard: block new records. The Firestore rules are
    // the durable gate (a live ID token can't create evaluaciones); this is the
    // friendly client-side message. Missing `activo` = legacy inspector = active.
    if (snap.data().activo === false) {
      await signOut(auth);
      setBusy(overlay, false);
      showError(overlay, 'Su usuario está inhabilitado. Contacte a la coordinación.');
      return;
    }

    inspector = { uid: user.uid, email: user.email || '', ...snap.data() };
    mountHeader(inspector);
    overlay.classList.add('is-authed');
    setBusy(overlay, false);
    setTimeout(() => { overlay.style.display = 'none'; }, 350);

    if (!booted) { booted = true; onFirstAuthorized(inspector); }
  }

  overlay.querySelector('#auth-retry').addEventListener('click', () => {
    if (!retryUser) return;
    showError(overlay, '');
    showRetry(overlay, false);
    setBusy(overlay, true);
    checkProfile(retryUser);
  });

  onAuthStateChanged(auth, async (user) => {
    if (!user) {
      inspector = null;
      retryUser = null;
      const form = overlay.querySelector('#auth-form');
      if (form) form.reset();
      showError(overlay, '');
      showRetry(overlay, false);
      setBusy(overlay, false);
      overlay.style.display = '';
      overlay.classList.remove('is-authed');
      return;
    }
    await checkProfile(user);
  });
}
