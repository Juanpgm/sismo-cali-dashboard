// Vercel Serverless Function — manage the field-form inspectors ("Stickers").
//
// The dashboard "Stickers" tab calls this to list, create, and enable/disable
// the @sismocali.gov.co inspector accounts. Inspectors sign in to the field
// form with cedula@sismocali.gov.co (Firebase Auth) and their profile lives in
// Firestore `inspectores/{uid}`. Disabling flips Auth `disabled` AND the doc's
// `activo` flag — the latter is the durable gate the Firestore rules check, so
// an already-issued ID token (valid ~1h) can no longer create evaluaciones.
//
// Required env in Vercel (Project Settings → Environment Variables):
//   FIREBASE_SERVICE_ACCOUNT_JSON  Service-account key JSON for dagma-85aad,
//     as a single-line string (Firebase console → Project settings →
//     Service accounts → Generate new private key).
//
// Auth: same Bearer ID-token check as api/refresh.js, PLUS a hard guard that
// the caller is a real dashboard admin and NOT an inspector. Inspectors are
// also `password` provider, so provider alone is not enough for user
// management — we reject any caller whose own email is @sismocali.gov.co.

const { verifyFirebaseToken } = require('./refresh.js');

const FIREBASE_PROJECT_ID = process.env.FIREBASE_PROJECT_ID || 'dagma-85aad';
const INSPECTOR_DOMAIN = '@sismocali.gov.co';

// ---- Pure validators (exported for the self-check) --------------------------
const isValidCedula = (v) => /^\d{5,12}$/.test(String(v ?? '').trim());
const isValidCodigo = (v) => /^\d{3}$/.test(String(v ?? '').trim());
const isValidPassword = (v) => typeof v === 'string' && v.length >= 6;
const cedulaToEmail = (cedula) => `${String(cedula).trim().toLowerCase()}${INSPECTOR_DOMAIN}`;
const emailToCedula = (email) => String(email || '').split('@')[0];

// ---- firebase-admin singleton ----------------------------------------------
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

// List every @sismocali.gov.co Auth user joined with its inspectores/{uid} doc.
// ponytail: single listUsers page (max 1000). If the roster ever exceeds that,
// paginate with the pageToken.
async function listInspectores(admin) {
  const { users } = await admin.auth().listUsers(1000);
  const inspectores = users.filter((u) => (u.email || '').toLowerCase().endsWith(INSPECTOR_DOMAIN));
  const db = admin.firestore();
  const snaps = inspectores.length
    ? await db.getAll(...inspectores.map((u) => db.doc(`inspectores/${u.uid}`)))
    : [];
  const byUid = new Map(snaps.map((s) => [s.id, s.exists ? s.data() : null]));
  // Real submitted evaluaciones per inspector. NOT `consecutivo` — that counter
  // increments every time a code is generated (form started), not when a record
  // is saved, so it overcounts. Count the source of truth instead. `null` on
  // error so the UI can tell "unknown" from a real zero.
  const registros = await Promise.all(inspectores.map((u) =>
    db.collection('evaluaciones').where('inspector.uid', '==', u.uid).count().get()
      .then((a) => a.data().count)
      .catch(() => null)));
  return inspectores
    .map((u, idx) => {
      const d = byUid.get(u.uid) || {};
      return {
        uid: u.uid,
        email: u.email,
        cedula: emailToCedula(u.email),
        nombre_completo: d.nombre_completo || '',
        codigo: d.codigo || '',
        entidad: d.entidad || '',
        registros: registros[idx],
        registrado: byUid.get(u.uid) !== null, // has a profile doc
        disabled: !!u.disabled,
        // Missing `activo` counts as active (legacy inspectors predate the flag).
        activo: d.activo !== false,
      };
    })
    .sort((a, b) => a.cedula.localeCompare(b.cedula));
}

async function createInspector(admin, body) {
  const cedula = String(body.cedula ?? '').trim();
  const codigo = String(body.codigo ?? '').trim();
  const password = body.password;
  if (!isValidCedula(cedula)) throw badRequest('La cédula debe tener solo dígitos (5 a 12).');
  if (!isValidCodigo(codigo)) throw badRequest('El código debe ser de 3 dígitos, ej "004".');
  if (!isValidPassword(password)) throw badRequest('La contraseña debe tener al menos 6 caracteres.');

  const email = cedulaToEmail(cedula);
  const user = await admin.auth().createUser({ email, password });
  await admin.firestore().doc(`inspectores/${user.uid}`).set({
    nombre_completo: String(body.nombre_completo || '').trim(),
    identificacion: String(body.identificacion || cedula).trim(),
    profesion: String(body.profesion || '').trim(),
    num_telefono: String(body.num_telefono || '').trim(),
    entidad: String(body.entidad || '').trim(),
    codigo,
    consecutivo: 0,
    activo: true,
  });
  return { uid: user.uid, email, cedula, codigo };
}

async function setEnabled(admin, body) {
  const uid = String(body.uid ?? '').trim();
  const enabled = body.enabled === true || body.enabled === 'true';
  if (!uid) throw badRequest('Falta el uid del inspector.');
  await admin.auth().updateUser(uid, { disabled: !enabled });
  // The Firestore flag is the gate the security rules read; keep it in sync.
  await admin.firestore().doc(`inspectores/${uid}`).set({ activo: enabled }, { merge: true });
  return { uid, activo: enabled, disabled: !enabled };
}

function badRequest(message) {
  const err = new Error(message);
  err.status = 400;
  return err;
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  // Fail-closed auth: valid Firebase ID token, provider "password", and the
  // caller must be a dashboard admin — NOT an inspector (inspectors are also
  // password-provider, so we reject @sismocali.gov.co callers).
  const authHeader = req.headers.authorization || '';
  const idToken = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
  if (!idToken) return res.status(401).json({ error: 'Autenticación requerida.' });
  try {
    const claims = await verifyFirebaseToken(idToken, FIREBASE_PROJECT_ID);
    const provider = claims.firebase && claims.firebase.sign_in_provider;
    const email = String(claims.email || '').toLowerCase();
    if (provider !== 'password' || email.endsWith(INSPECTOR_DOMAIN)) {
      return res.status(403).json({ error: 'Solo administradores pueden gestionar inspectores.' });
    }
  } catch (err) {
    return res.status(401).json({ error: `Token inválido: ${(err && err.message) || err}` });
  }

  const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
  const action = body.action;

  try {
    const admin = getAdmin();
    if (action === 'list') return res.status(200).json({ ok: true, inspectores: await listInspectores(admin) });
    if (action === 'create') return res.status(201).json({ ok: true, ...(await createInspector(admin, body)) });
    if (action === 'setEnabled') return res.status(200).json({ ok: true, ...(await setEnabled(admin, body)) });
    return res.status(400).json({ error: `Acción desconocida: ${action}` });
  } catch (err) {
    // Surface Firebase's own messages (e.g. email-already-exists) to the admin.
    const status = err && err.status ? err.status : 502;
    return res.status(status).json({ error: String((err && err.message) || err) });
  }
};

// Exposed for the self-check (api/stickers.test.js); Vercel uses the default export.
module.exports.isValidCedula = isValidCedula;
module.exports.isValidCodigo = isValidCodigo;
module.exports.isValidPassword = isValidPassword;
module.exports.cedulaToEmail = cedulaToEmail;
module.exports.emailToCedula = emailToCedula;
