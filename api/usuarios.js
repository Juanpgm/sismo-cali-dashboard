// Vercel Serverless Function — superset user management ("Usuarios" tab).
//
// Lists every Firebase Auth user on sismo-agosto-sgred across the three real
// populations (password admins, google.com @cali.gov.co viewers,
// @sismocali.gov.co inspectors), and lets an admin create a password admin,
// enable/disable, or delete an account. Mirrors api/stickers.js's shape
// (getAdmin singleton, auth preamble, action router) — see design.md ADR-1.
//
// Required env in Vercel (Project Settings → Environment Variables):
//   FIREBASE_SERVICE_ACCOUNT_JSON  Service-account key JSON for sismo-agosto-sgred,
//     as a single-line string (Firebase console → Project settings →
//     Service accounts → Generate new private key).
//
// Auth: same Bearer ID-token check as api/stickers.js — valid token,
// provider "password", caller NOT @sismocali.gov.co (inspectors are also
// password-provider, so provider alone doesn't prove "admin").

const { verifyFirebaseToken, roleFrom, roleFromClaims } = require('./refresh.js');

const FIREBASE_PROJECT_ID = process.env.FIREBASE_PROJECT_ID || 'sismo-agosto-sgred';
const INSPECTOR_DOMAIN = '@sismocali.gov.co';

// ---- Pure validators / classifiers (exported for the self-check) ----------
const isValidPassword = (v) => typeof v === 'string' && v.length >= 6;
const hasProvider = (u, id) => (u.providerData || []).some((p) => p.providerId === id);

// Effective role for a listUsers UserRecord, delegating to the single source
// of truth (refresh.js roleFrom) so the server's notion of role — including the
// assignable custom claim and the superadmin bootstrap — cannot drift between
// endpoints or from web/js/auth.js. customClaims ride on the UserRecord.
function classify(u) {
  return roleFrom({
    email: u.email,
    claimRole: u.customClaims && u.customClaims.role,
    provider: hasProvider(u, 'password') ? 'password' : (hasProvider(u, 'google.com') ? 'google.com' : ''),
  });
}

const isEnabledAdmin = (u) => !u.disabled && classify(u) === 'admin';

// ---- firebase-admin singleton ----------------------------------------------
// Duplicated (not imported from stickers.js) on purpose: each serverless
// function stays self-contained, and firebase-admin is itself a process
// singleton, so this re-uses the same initialized app — zero double-init
// risk. design.md ADR-1.
let adminSdk = null;
function getAdmin() {
  if (adminSdk) return adminSdk;
  const raw = process.env.FIREBASE_SERVICE_ACCOUNT_JSON;
  if (!raw) throw new Error('FIREBASE_SERVICE_ACCOUNT_JSON no está configurado en Vercel.');
  const admin = require('firebase-admin');
  if (!admin.apps.length) {
    admin.initializeApp({ credential: admin.credential.cert(JSON.parse(raw)) });
  }
  adminSdk = admin;
  return admin;
}

// ponytail: listUsers(1000) ceiling, page with pageToken if ever exceeded
// (same ceiling as stickers.js:65 — combined population is well under 1000).
async function listUsuarios(admin) {
  const { users } = await admin.auth().listUsers(1000);
  return users.map((u) => ({
    uid: u.uid,
    email: u.email || '',
    role: classify(u),
    disabled: !!u.disabled,
    lastSignInTime: (u.metadata && u.metadata.lastSignInTime) || null,
    creationTime: (u.metadata && u.metadata.creationTime) || null,
  }));
}

// Admin-only creation: viewers auto-provision at first Google sign-in, and
// inspectors need the brigade-code transaction that already lives in
// Stickers. So this mints a plain password admin — no Firestore profile
// write, hence no rollback branch (narrowed from the general "create" spec
// language, which describes the Stickers createInspector rollback).
// design.md ADR-1.
async function createUsuario(admin, body) {
  const email = String(body.email || '').trim().toLowerCase();
  const password = body.password;
  if (!email || !email.includes('@')) throw badRequest('Email inválido.');
  if (email.endsWith(INSPECTOR_DOMAIN)) {
    throw badRequest('Los inspectores se crean desde la pestaña Stickers, no aquí.');
  }
  if (!isValidPassword(password)) throw badRequest('La contraseña debe tener al menos 6 caracteres.');
  const user = await admin.auth().createUser({ email, password });
  return { uid: user.uid, email };
}

async function setEnabled(admin, body, callerUid) {
  const uid = String(body.uid ?? '').trim();
  const enabled = body.enabled === true || body.enabled === 'true';
  if (!uid) throw badRequest('Falta el uid.');
  if (uid === callerUid) throw forbidden('No podés inhabilitar tu propia cuenta.');
  const target = await admin.auth().getUser(uid);
  await admin.auth().updateUser(uid, { disabled: !enabled });
  // The Firestore `activo` flag is the durable gate the security rules
  // check for inspectors; keep it in sync exactly as stickers.js does.
  if (classify(target) === 'inspector') {
    await admin.firestore().doc(`inspectores/${uid}`).set({ activo: enabled }, { merge: true });
  }
  return { uid, disabled: !enabled };
}

// Pure guard: self-management (guard 1) + last-admin (guard 2, delete only).
// "Last admin" = count of enabled password non-@sismocali accounts in the
// same listUsers snapshot the caller already has. Factored out so it's
// testable without mocking the Admin SDK. design.md ADR-3. Returns null when
// allowed, or a { status, message } describing the rejection.
function checkDeleteGuards(users, targetUid, callerUid) {
  if (targetUid === callerUid) return { status: 403, message: 'No podés eliminar tu propia cuenta.' };
  const target = users.find((u) => u.uid === targetUid);
  if (!target) return { status: 400, message: 'Usuario no encontrado.' };
  if (isEnabledAdmin(target)) {
    const enabledAdmins = users.filter(isEnabledAdmin).length;
    if (enabledAdmins <= 1) return { status: 403, message: 'No podés eliminar al último administrador.' };
  }
  return null;
}

// Net-new. Removes the Auth user and its inspectores/{uid} profile (if any).
// `evaluaciones` are left INTACT — they are historical inspection records
// keyed by inspector.uid, not account data. design.md ADR-3.
async function deleteUsuario(admin, body, callerUid) {
  const uid = String(body.uid ?? '').trim();
  if (!uid) throw badRequest('Falta el uid.');

  const { users } = await admin.auth().listUsers(1000);
  const rejection = checkDeleteGuards(users, uid, callerUid);
  if (rejection) {
    const err = new Error(rejection.message);
    err.status = rejection.status;
    throw err;
  }

  await admin.auth().deleteUser(uid);
  // The Auth user is already gone; if the profile delete fails, surface the
  // orphaned inspectores/{uid} doc in the logs instead of swallowing it.
  await admin.firestore().doc(`inspectores/${uid}`).delete().catch((e) => {
    console.error(`usuarios.delete: perfil inspectores/${uid} huérfano — falló el borrado:`, (e && e.message) || e);
  });
  return { uid };
}

// Roles an admin can assign from the "Cambiar rol" UI. 'inspector'/'otro' are
// derived, never hand-assigned here.
const ASSIGNABLE_ROLES = ['admin', 'usuario', 'viewer'];

// Sets the target's effective role via a Firebase custom claim. The claim rides
// in the target's ID token, so it takes effect only after THEIR token refreshes
// (re-login or ~1h) — the design's accepted trade-off for zero per-request
// Firestore reads. Anti-lockout: you cannot strip your own admin role. The
// SUPERADMIN_EMAIL account is un-lockable regardless (roleFrom ignores its
// claim), so a valid Administrador always exists.
async function setRole(admin, body, callerUid) {
  const uid = String(body.uid ?? '').trim();
  const role = String(body.role ?? '').trim();
  if (!uid) throw badRequest('Falta el uid.');
  if (!ASSIGNABLE_ROLES.includes(role)) throw badRequest(`Rol inválido: ${role}.`);
  if (uid === callerUid && role !== 'admin') {
    throw forbidden('No podés quitarte tu propio rol de administrador.');
  }
  const target = await admin.auth().getUser(uid);
  await admin.auth().setCustomUserClaims(uid, { ...(target.customClaims || {}), role });
  return { uid, role };
}

// Set a new password directly (no reset email). Inspectors have synthetic
// @sismocali.gov.co emails that never receive Firebase's password-reset mail, so
// the admin hands them a new password here instead. Any password account is a
// valid target; validated for length only.
async function setPassword(admin, body) {
  const uid = String(body.uid ?? '').trim();
  const password = String(body.password ?? '');
  if (!uid) throw badRequest('Falta el uid.');
  if (!isValidPassword(password)) throw badRequest('La contraseña debe tener al menos 6 caracteres.');
  await admin.auth().updateUser(uid, { password });
  return { uid };
}

function badRequest(message) {
  const err = new Error(message);
  err.status = 400;
  return err;
}

function forbidden(message) {
  const err = new Error(message);
  err.status = 403;
  return err;
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  // Fail-closed auth: valid Firebase ID token, provider "password", caller
  // NOT @sismocali.gov.co (inspectors are also password-provider).
  const authHeader = req.headers.authorization || '';
  const idToken = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
  if (!idToken) return res.status(401).json({ error: 'Autenticación requerida.' });
  let callerUid;
  try {
    const claims = await verifyFirebaseToken(idToken, FIREBASE_PROJECT_ID);
    if (roleFromClaims(claims) !== 'admin') {
      return res.status(403).json({ error: 'Solo administradores pueden gestionar usuarios.' });
    }
    callerUid = claims.sub;
  } catch (err) {
    return res.status(401).json({ error: `Token inválido: ${(err && err.message) || err}` });
  }

  const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
  const action = body.action;

  try {
    const admin = getAdmin();
    if (action === 'list') return res.status(200).json({ ok: true, usuarios: await listUsuarios(admin) });
    if (action === 'create') return res.status(201).json({ ok: true, ...(await createUsuario(admin, body)) });
    if (action === 'setEnabled') return res.status(200).json({ ok: true, ...(await setEnabled(admin, body, callerUid)) });
    if (action === 'delete') return res.status(200).json({ ok: true, ...(await deleteUsuario(admin, body, callerUid)) });
    if (action === 'setRole') return res.status(200).json({ ok: true, ...(await setRole(admin, body, callerUid)) });
    if (action === 'setPassword') return res.status(200).json({ ok: true, ...(await setPassword(admin, body)) });
    return res.status(400).json({ error: `Acción desconocida: ${action}` });
  } catch (err) {
    const status = err && err.status ? err.status : 502;
    return res.status(status).json({ error: String((err && err.message) || err) });
  }
};

// Exposed for the self-check (api/usuarios.test.js); Vercel uses the default export.
module.exports.classify = classify;
module.exports.isEnabledAdmin = isEnabledAdmin;
module.exports.isValidPassword = isValidPassword;
module.exports.checkDeleteGuards = checkDeleteGuards;
