// Vercel Serverless Function — an INSPECTOR's own sticker assignments.
//
// The ATC-20 field form (separate Vercel project `formulario/`) calls this to
// show a logged-in inspector the points assigned to them and to mark one done
// after they register its sticker. It reads/writes `sticker_matches` with the
// Admin SDK (bypassing Firestore rules), so inspectors need NO client-side
// Firestore access — the auth boundary is the Firebase ID token, not a rule.
//
// Unlike api/sticker-asignaciones.js (admin-only), this endpoint authorizes ANY
// authenticated user and scopes every read/write to their OWN uid
// (`inspector_uid == token.sub`) — an inspector can only ever see or touch the
// points assigned to them.
//
// CORS: the caller is a different origin (the formulario project). Auth is the
// token, so we reflect the request Origin and allow the Authorization header.
//
// Required env (same as api/sticker-asignaciones.js):
//   FIREBASE_SERVICE_ACCOUNT_JSON  Service-account key JSON for sismo-agosto-sgred.

const { verifyFirebaseToken } = require('./refresh.js');

const FIREBASE_PROJECT_ID = process.env.FIREBASE_PROJECT_ID || 'sismo-agosto-sgred';
const DONE_ESTADO = 'hecho';

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

// ---- pure helper (exported for the self-check) -----------------------------

/** An assignment is still "pending" (should show in the picker) when it is not
 *  yet marked done. Points already registered drop off the inspector's list. */
function pendiente(doc) {
  return !!doc && doc.estado_asignacion !== DONE_ESTADO;
}

// ---- request handling ------------------------------------------------------

function setCors(req, res) {
  // Token-based auth, so any origin may call; reflect it to satisfy browsers.
  res.setHeader('Access-Control-Allow-Origin', req.headers.origin || '*');
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type');
  res.setHeader('Access-Control-Max-Age', '86400');
}

async function misPuntos(admin, uid) {
  const snap = await admin.firestore().collection('sticker_matches')
    .where('inspector_uid', '==', uid).get();
  return snap.docs
    .map((d) => ({ id: d.id, ...d.data() }))
    .filter(pendiente)
    .map((p) => ({
      id: p.id,
      direccion: p.direccion || '',
      zona_id: p.zona_id || '',
      coords: p.coords || null,
      criterio_habitabilidad: p.criterio_habitabilidad || null,
      colapso: p.colapso || 'no',
      estado_asignacion: p.estado_asignacion || 'pendiente',
    }));
}

async function marcarHecho(admin, uid, puntoId) {
  if (!puntoId) { const e = new Error('Falta el id del punto.'); e.status = 400; throw e; }
  const ref = admin.firestore().doc(`sticker_matches/${puntoId}`);
  const snap = await ref.get();
  if (!snap.exists) { const e = new Error('El punto no existe.'); e.status = 404; throw e; }
  if (snap.data().inspector_uid !== uid) {
    const e = new Error('Ese punto no está asignado a este inspector.'); e.status = 403; throw e;
  }
  await ref.set({ estado_asignacion: DONE_ESTADO }, { merge: true });
  return { id: puntoId, estado_asignacion: DONE_ESTADO };
}

module.exports = async (req, res) => {
  setCors(req, res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  let uid;
  try {
    const authz = req.headers.authorization || '';
    const idToken = authz.startsWith('Bearer ') ? authz.slice(7) : '';
    if (!idToken) return res.status(401).json({ error: 'Autenticación requerida.' });
    const claims = await verifyFirebaseToken(idToken, FIREBASE_PROJECT_ID);
    uid = claims.sub || claims.uid;
    if (!uid) return res.status(401).json({ error: 'Token sin identificador de usuario.' });
  } catch (err) {
    return res.status(401).json({ error: `Token inválido: ${(err && err.message) || err}` });
  }

  const body = typeof req.body === 'object' && req.body ? req.body : {};
  try {
    const admin = getAdmin();
    if (body.action === 'misPuntos') {
      return res.status(200).json({ ok: true, puntos: await misPuntos(admin, uid) });
    }
    if (body.action === 'marcarHecho') {
      return res.status(200).json({ ok: true, ...(await marcarHecho(admin, uid, String(body.punto_id || ''))) });
    }
    return res.status(400).json({ error: 'Acción no reconocida.' });
  } catch (err) {
    return res.status((err && err.status) || 502).json({ error: String((err && err.message) || err) });
  }
};

module.exports.pendiente = pendiente;
