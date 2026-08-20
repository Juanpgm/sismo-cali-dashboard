// Authentication gate for the dashboard.
//
// Roles are derived purely from HOW the user signed in:
//   • Email/password (provider "password")  → ADMIN  — sees everything,
//     including the "Acciones" tab and the "Actualizar datos" button.
//     These users are created by hand in the Firebase console.
//   • Google (provider "google.com") whose email ends in @cali.gov.co → VIEWER
//     — Panel only; the Acciones tab and refresh button are hidden.
//   • Anything else (e.g. a Google account outside @cali.gov.co) is rejected.
//
// The role is applied to the document via `document.body.dataset.role`, which
// CSS uses to hide admin-only chrome. The privileged action (refresh) is ALSO
// verified server-side (api/refresh.js), so hiding the button is not the only
// line of defense.
//
// ceiling: the static JSON in web/data/ stays publicly fetchable by URL — this
// gates the UI and the refresh trigger, not the raw data files. Real data
// protection would require serving those behind token-checked functions.

import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js';
import {
  getAuth, setPersistence, browserLocalPersistence,
  GoogleAuthProvider, signInWithPopup, signInWithEmailAndPassword,
  onAuthStateChanged, signOut,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js';
import { firebaseConfig, ALLOWED_DOMAIN, isConfigured } from './firebase-config.js';

let auth = null;
let currentRole = null; // 'admin' | 'viewer' | null
let overlayEl = null;   // referencia al overlay para cubrir desde cualquier lado

export const getRole = () => currentRole;
export const isAdmin = () => currentRole === 'admin';

// Cubrir el contenido AL INSTANTE (sin transición de opacidad): así al cerrar
// sesión no queda ni un frame donde se vea el contenido restringido detrás.
function coverInstant() {
  const o = overlayEl;
  if (!o) return;
  o.style.transition = 'none';
  o.style.display = '';
  o.classList.remove('is-authed');
  o.style.opacity = '1';
  void o.offsetWidth;          // fuerza el commit del estado opaco
  o.style.transition = '';     // restaura la transición para el fade-out del próximo login
}

/** Firebase ID token for the signed-in user (or null). Attached to /api/refresh. */
export async function getIdToken() {
  return auth && auth.currentUser ? auth.currentUser.getIdToken() : null;
}

export async function signOutUser() {
  // 1) tapar el contenido de inmediato (antes de que nada más corra).
  coverInstant();
  // 2) cerrar la sesión de Firebase y 3) recargar: la recarga BORRA de memoria y
  //    del DOM cualquier dato restringido; al volver, sin sesión, la app no
  //    arranca y solo se ve el login. A prueba de flashes y de fugas en el DOM.
  try {
    if (auth) await signOut(auth);
  } finally {
    window.location.reload();
  }
}

function roleForUser(user) {
  const providers = (user.providerData || []).map((p) => p.providerId);
  if (providers.includes('password')) return 'admin';
  if (providers.includes('google.com')) {
    const email = (user.email || '').toLowerCase();
    return email.endsWith(`@${ALLOWED_DOMAIN}`) ? 'viewer' : null;
  }
  return null;
}

// ---- Login overlay ----------------------------------------------------------

function buildOverlay() {
  const root = document.createElement('div');
  root.className = 'auth-overlay';
  root.id = 'auth-overlay';
  root.innerHTML = `
    <div class="auth-card" role="dialog" aria-modal="true" aria-labelledby="auth-title">
      <div class="auth-brand">
        <div>
          <h1 id="auth-title">Visualización datos evento sísmico, agosto 2026</h1>
          <p class="auth-sub">Acceso restringido · Evaluación de daños</p>
        </div>
      </div>

      <p class="auth-lead">Ingresá para ver el panel de inspecciones.</p>

      <button type="button" class="auth-google" id="auth-google">
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.5 29.6 4.5 24 4.5 13.2 4.5 4.5 13.2 4.5 24S13.2 43.5 24 43.5 43.5 34.8 43.5 24c0-1.2-.1-2.3-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.5 29.6 4.5 24 4.5 16.3 4.5 9.7 8.9 6.3 14.7z"/><path fill="#4CAF50" d="M24 43.5c5.5 0 10.4-1.9 14.1-5.2l-6.5-5.5c-2 1.5-4.6 2.4-7.6 2.4-5.2 0-9.6-3.3-11.2-7.9l-6.5 5C9.6 39 16.2 43.5 24 43.5z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4.1 5.4l6.5 5.5C42.6 35.9 43.5 30.4 43.5 24c0-1.2-.1-2.3-.4-3.5z"/></svg>
        <span>Continuar con Google <b>@${ALLOWED_DOMAIN}</b></span>
      </button>

      <div class="auth-divider"><span>o con usuario administrador</span></div>

      <form class="auth-form" id="auth-form" novalidate>
        <label class="auth-field">
          <span>Correo</span>
          <input type="email" id="auth-email" autocomplete="username" required placeholder="admin@ejemplo.com">
        </label>
        <label class="auth-field">
          <span>Contraseña</span>
          <input type="password" id="auth-password" autocomplete="current-password" required placeholder="••••••••">
        </label>
        <button type="submit" class="auth-submit" id="auth-submit">Ingresar</button>
      </form>

      <p class="auth-error" id="auth-error" role="alert" hidden></p>
      <p class="auth-foot">Solo personal autorizado. Los accesos con Google se limitan a cuentas <b>@${ALLOWED_DOMAIN}</b>.</p>
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
      return 'Demasiados intentos. Esperá un momento e intentá de nuevo.';
    case 'auth/popup-closed-by-user':
    case 'auth/cancelled-popup-request':
      return 'Se cerró la ventana de Google antes de terminar.';
    case 'auth/popup-blocked':
      return 'El navegador bloqueó la ventana de Google. Permití las ventanas emergentes.';
    case 'auth/unauthorized-domain':
      return 'Este dominio no está autorizado en Firebase (Authentication → Settings → Authorized domains).';
    default:
      return (err && err.message) || 'No se pudo iniciar sesión.';
  }
}

/**
 * Initialize auth and gate the app.
 * @param {() => void} onFirstAuthorized called ONCE, the first time a valid
 *   user is authorized. The app boots (loads data) only after this fires.
 */
export function initAuth(onFirstAuthorized) {
  const overlay = buildOverlay();
  overlayEl = overlay;

  if (!isConfigured()) {
    overlay.querySelector('.auth-lead').textContent =
      'Falta configurar Firebase: pegá tus credenciales en web/js/firebase-config.js.';
    setBusy(overlay, true);
    return;
  }

  const app = initializeApp(firebaseConfig);
  auth = getAuth(app);
  setPersistence(auth, browserLocalPersistence).catch(() => {});

  const googleProvider = new GoogleAuthProvider();
  googleProvider.setCustomParameters({ hd: ALLOWED_DOMAIN, prompt: 'select_account' });

  overlay.querySelector('#auth-google').addEventListener('click', async () => {
    showError(overlay, '');
    setBusy(overlay, true);
    try {
      await signInWithPopup(auth, googleProvider);
      // role/gating handled by onAuthStateChanged below.
    } catch (err) {
      setBusy(overlay, false);
      showError(overlay, friendlyError(err));
    }
  });

  overlay.querySelector('#auth-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    showError(overlay, '');
    const email = overlay.querySelector('#auth-email').value.trim();
    const password = overlay.querySelector('#auth-password').value;
    if (!email || !password) { showError(overlay, 'Ingresá correo y contraseña.'); return; }
    setBusy(overlay, true);
    try {
      await signInWithEmailAndPassword(auth, email, password);
    } catch (err) {
      setBusy(overlay, false);
      showError(overlay, friendlyError(err));
    }
  });

  let booted = false;
  onAuthStateChanged(auth, async (user) => {
    if (!user) {
      currentRole = null;
      delete document.body.dataset.role;
      coverInstant(); // overlay opaco YA, sin fade → nunca se ve el contenido detrás
      const form = overlay.querySelector('#auth-form');
      if (form) form.reset();
      showError(overlay, '');
      setBusy(overlay, false);
      return;
    }

    const role = roleForUser(user);
    if (!role) {
      // Signed in but not allowed (e.g. Google account outside the domain).
      await signOut(auth);
      setBusy(overlay, false);
      showError(overlay, `Solo cuentas @${ALLOWED_DOMAIN} pueden ingresar con Google. Usá tu correo institucional o una cuenta administradora.`);
      return;
    }

    currentRole = role;
    document.body.dataset.role = role;
    mountUserChip(user, role);
    overlay.style.opacity = ''; // soltar el opacity inline de coverInstant para que el fade-out corra
    overlay.classList.add('is-authed');
    setBusy(overlay, false);
    // Remove the overlay from the a11y tree once hidden.
    setTimeout(() => { overlay.style.display = 'none'; }, 350);

    if (!booted) { booted = true; onFirstAuthorized(); }
  });
}

// ---- Signed-in chip in the header ------------------------------------------

function mountUserChip(user, role) {
  const host = document.querySelector('.app-header-top');
  if (!host) return;
  let chip = document.getElementById('user-chip');
  if (!chip) {
    chip = document.createElement('div');
    chip.className = 'user-chip';
    chip.id = 'user-chip';
    // Insert before the theme toggle so it sits at the right of the header.
    const themeBtn = document.getElementById('theme-toggle');
    host.insertBefore(chip, themeBtn);
  }
  const label = role === 'admin' ? 'Administrador' : 'Institucional';
  chip.innerHTML = `
    <div class="user-chip-info">
      <span class="user-chip-role" data-role="${role}">${label}</span>
      <span class="user-chip-email" title="${user.email || ''}">${user.email || ''}</span>
    </div>
    <button type="button" class="user-chip-logout" id="user-logout" aria-label="Cerrar sesión" title="Cerrar sesión">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
    </button>`;
  chip.querySelector('#user-logout').addEventListener('click', () => signOutUser());
}
