// Vercel Serverless Function — real, live connectivity check for the
// atencionsismo API, backing the "Analista" tab's atencionsismo row. GET-only,
// admin-gated. A snapshot (reportes_meta.json) proves the pipeline ran, not
// that the API is reachable NOW — this endpoint answers that second question
// by re-running the same one-minute probe api/reportados.js already uses
// before its full day-walk (see probeApi there). See design.md ADR-3/ADR-4.
//
// Required env in Vercel (Project Settings → Environment Variables):
//   VISITADOS_API_PASS   password for the api="read" account (same as
//     api/reportados.js). Missing/invalid -> ok:false, NOT a 5xx: the
//     endpoint successfully determined the source is down.
// Optional:
//   VISITADOS_API_USER   default juanp.gzmz@gmail.com

const { verifyFirebaseToken, roleFromClaims } = require('./refresh.js');
const { probeApi } = require('./reportados.js');

const FIREBASE_PROJECT_ID = process.env.FIREBASE_PROJECT_ID || 'sismo-agosto-sgred';
const DEFAULT_USER = 'juanp.gzmz@gmail.com';

async function defaultVerify(idToken) {
  const claims = await verifyFirebaseToken(idToken, FIREBASE_PROJECT_ID);
  return { role: roleFromClaims(claims), email: claims && claims.email };
}

async function defaultProbe() {
  const pass = (process.env.VISITADOS_API_PASS || '').trim();
  if (!pass) {
    const err = new Error('VISITADOS_API_PASS no está configurado en Vercel.');
    err.status = 503;
    throw err;
  }
  const user = (process.env.VISITADOS_API_USER || '').trim() || DEFAULT_USER;
  const auth = Buffer.from(`${user}:${pass}`).toString('base64');
  await probeApi(auth);
}

// Factory over an injectable { verify, probe } so the auth-gate and ok/not-ok
// mapping are testable without the Firebase Admin SDK or a real network call
// (api/source-status.test.js). Vercel's default export uses the real
// dependencies; module.exports.handle is exposed only for the self-check.
function handle({ verify = defaultVerify, probe = defaultProbe } = {}) {
  return async (req, res) => {
    if (req.method !== 'GET') {
      res.setHeader('Allow', 'GET');
      return res.status(405).json({ error: 'Method Not Allowed' });
    }

    const authHeader = req.headers.authorization || '';
    const idToken = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
    if (!idToken) return res.status(401).json({ error: 'Autenticación requerida.' });

    let role;
    try {
      ({ role } = await verify(idToken));
    } catch (err) {
      return res.status(401).json({ error: `Token inválido: ${(err && err.message) || err}` });
    }
    if (role !== 'admin') {
      return res.status(403).json({ error: 'Solo administradores pueden ver el estado de las fuentes.' });
    }

    const checked_at = new Date().toISOString();
    try {
      await probe();
      res.setHeader('Cache-Control', 'private, no-store');
      return res.status(200).json({ ok: true, status: 'conectado', detail: null, checked_at });
    } catch (err) {
      res.setHeader('Cache-Control', 'private, no-store');
      return res.status(200).json({
        ok: false,
        status: 'con errores',
        detail: String((err && err.message) || err),
        checked_at,
      });
    }
  };
}

module.exports = handle();
module.exports.handle = handle;
