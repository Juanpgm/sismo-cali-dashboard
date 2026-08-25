// Vercel Serverless Function — lightweight sticker-coverage lookup for the
// Panel + Evaluaciones views.
//
// Returns, for every Panel point that the cruce_sticker.py pipeline has matched
// against a field sticker (evaluación), whether it already has one. The browser
// joins `record.GlobalID` against `con_sticker` to colour the map (blue = has a
// sticker, red = pending) and to draw the coverage gauge.
//
// Unlike api/sticker-asignaciones.js this is READ-ONLY and open to ANY logged-in
// user (the whole dashboard is already behind Firebase auth), not admin-only —
// it exposes no assignment state, just the boolean coverage flag keyed by the
// Panel GlobalID (no PII).
//
// The payload (a list of ~300 ids) is identical for every viewer, so it is held
// in a short module-level cache to spare Firestore a full-collection read on
// every page load. Aligned to the 15-min pipeline cadence.
//
// Required env in Vercel:
//   FIREBASE_SERVICE_ACCOUNT_JSON  Service-account key JSON for sismo-agosto-sgred.

const { verifyFirebaseToken } = require('./refresh.js');

const FIREBASE_PROJECT_ID = process.env.FIREBASE_PROJECT_ID || 'sismo-agosto-sgred';
const CACHE_TTL_MS = 5 * 60 * 1000;

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

// { at, payload } — the payload is viewer-independent, so one warm-lambda cache
// serves every request within the TTL.
let cache = null;

async function readCoverage(admin) {
  const snap = await admin.firestore().collection('sticker_matches').get();
  const conSticker = [];
  let total = 0;
  snap.forEach((doc) => {
    const d = doc.data() || {};
    const rid = d.registro_id;
    if (rid == null) return;
    total += 1;
    if (d.tiene_sticker === true) conSticker.push(String(rid));
  });
  return { con_sticker: conSticker, total, con: conSticker.length };
}

module.exports = async (req, res) => {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  // Any valid Firebase ID token — the Panel view is shown to every logged-in
  // role, so this is not gated to admins.
  const authHeader = req.headers.authorization || '';
  const idToken = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
  if (!idToken) return res.status(401).json({ error: 'Autenticación requerida.' });
  try {
    await verifyFirebaseToken(idToken, FIREBASE_PROJECT_ID);
  } catch (err) {
    return res.status(401).json({ error: `Token inválido: ${(err && err.message) || err}` });
  }

  try {
    if (!cache || Date.now() - cache.at > CACHE_TTL_MS) {
      cache = { at: Date.now(), payload: await readCoverage(getAdmin()) };
    }
    return res.status(200).json({ ok: true, ...cache.payload });
  } catch (err) {
    const status = (err && err.status) || 502;
    return res.status(status).json({ error: String((err && err.message) || err) });
  }
};
