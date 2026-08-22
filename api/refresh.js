// Vercel Serverless Function — manual trigger for the dashboard data refresh.
//
// The dashboard is a static site, so the browser cannot run the pipeline
// itself. This endpoint is the server hop the "Actualizar datos" button calls:
// it redeploys the Railway `dashboard-refresh` cron container, which runs
// scripts/refresh_data.py, regenerates web/data/*.json from the F3 Sheet and
// pushes to the repo → Vercel redeploys with the fresh data.
//
// Required env in Vercel (Project Settings → Environment Variables):
//   RAILWAY_API_TOKEN   Railway API token with access to the project (secret).
// Optional (default to the provisioned `dashboard-refresh` service in the
// `normalizador-sismo-cali` project — override only if the IDs change):
//   RAILWAY_SERVICE_ID
//   RAILWAY_ENVIRONMENT_ID

const crypto = require('crypto');

const RAILWAY_API = 'https://backboard.railway.com/graphql/v2';

// Firebase project that issues the ID tokens (public identifier). Env override
// only if it ever changes.
const FIREBASE_PROJECT_ID = process.env.FIREBASE_PROJECT_ID || 'sismo-agosto-sgred';
const FIREBASE_CERTS_URL =
  'https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com';

const b64urlToBuf = (s) => Buffer.from(s.replace(/-/g, '+').replace(/_/g, '/'), 'base64');

// Verify a Firebase ID token with zero dependencies: RS256 signature against
// Google's rotating x509 certs + the standard Firebase claim checks. Returns the
// decoded payload, or throws.
async function verifyFirebaseToken(idToken, projectId) {
  const parts = String(idToken || '').split('.');
  if (parts.length !== 3) throw new Error('token malformado');
  const [h, p, s] = parts;
  const header = JSON.parse(b64urlToBuf(h).toString('utf8'));
  const payload = JSON.parse(b64urlToBuf(p).toString('utf8'));

  const certRes = await fetch(FIREBASE_CERTS_URL);
  const certs = await certRes.json();
  const pem = certs[header.kid];
  if (!pem) throw new Error('kid desconocido');

  const verifier = crypto.createVerify('RSA-SHA256');
  verifier.update(`${h}.${p}`);
  verifier.end();
  if (!verifier.verify(pem, b64urlToBuf(s))) throw new Error('firma inválida');

  const now = Math.floor(Date.now() / 1000);
  if (payload.aud !== projectId) throw new Error('aud inválido');
  if (payload.iss !== `https://securetoken.google.com/${projectId}`) throw new Error('iss inválido');
  if (typeof payload.exp !== 'number' || payload.exp < now) throw new Error('token expirado');
  if (typeof payload.iat !== 'number' || payload.iat > now + 300) throw new Error('iat inválido');
  return payload;
}

const SERVICE_ID = process.env.RAILWAY_SERVICE_ID || '156e97a2-596b-4861-95f4-4060dab408e2';
// Cruce críticos↔survey (vista Gestión). El botón "Actualizar datos" refresca TODO:
// datos del Panel (dashboard-refresh) + cruce de Gestión (cruce-gestion).
const CRUCE_SERVICE_ID = process.env.RAILWAY_CRUCE_SERVICE_ID || 'b4c8fd15-aa3b-4157-b787-2034c89a108b';
const ENVIRONMENT_ID = process.env.RAILWAY_ENVIRONMENT_ID || '4418f451-bd97-4d96-ba6e-b5ecbbd49c9b';

// serviceInstanceRedeploy redeploys the service's latest deployment (i.e. runs
// the cron container now) and returns the new deployment id.
const REDEPLOY = `mutation($s:String!,$e:String!){
  serviceInstanceRedeploy(serviceId:$s, environmentId:$e) }`;

async function railway(token, query, variables) {
  // Railway authenticates account/team tokens via `Authorization: Bearer` and
  // project tokens via the `Project-Access-Token` header. Try Bearer first and
  // fall back to the project header so either token type works transparently.
  const authHeaders = [
    { Authorization: `Bearer ${token}` },
    { 'Project-Access-Token': token },
  ];
  let lastError;
  for (const auth of authHeaders) {
    const res = await fetch(RAILWAY_API, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        // Cloudflare answers 403 to requests without a User-Agent.
        'User-Agent': 'sismo-cali-dashboard/1.0',
        ...auth,
      },
      body: JSON.stringify({ query, variables }),
    });
    const json = await res.json().catch(() => ({}));
    if (res.ok && !json.errors) return json.data;
    lastError = `Railway API ${res.status}: ${JSON.stringify(json.errors || json)}`;
  }
  throw new Error(lastError);
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  // Solo administradores (login por usuario/contraseña) pueden disparar el
  // refresh. Verificamos el ID token de Firebase y exigimos que la sesión sea
  // por proveedor "password". Fail-closed: sin token válido → 401/403.
  const authHeader = req.headers.authorization || '';
  const idToken = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
  if (!idToken) {
    return res.status(401).json({ error: 'Autenticación requerida.' });
  }
  try {
    const claims = await verifyFirebaseToken(idToken, FIREBASE_PROJECT_ID);
    const provider = claims.firebase && claims.firebase.sign_in_provider;
    if (provider !== 'password') {
      return res.status(403).json({ error: 'Solo administradores pueden actualizar los datos.' });
    }
  } catch (err) {
    return res.status(401).json({ error: `Token inválido: ${(err && err.message) || err}` });
  }

  const token = process.env.RAILWAY_API_TOKEN;
  if (!token) {
    return res
      .status(500)
      .json({ error: 'RAILWAY_API_TOKEN no está configurado en Vercel.' });
  }

  try {
    const data = await railway(token, REDEPLOY, { s: SERVICE_ID, e: ENVIRONMENT_ID });
    // Cruce de Gestión: fail-soft — si falla, el refresh del Panel ya quedó
    // encolado y la Gestión se refresca sola con su cron de 15 min.
    let cruceDeploymentId = null;
    try {
      const cruce = await railway(token, REDEPLOY, { s: CRUCE_SERVICE_ID, e: ENVIRONMENT_ID });
      cruceDeploymentId = cruce.serviceInstanceRedeploy;
    } catch (err) {
      console.error('cruce-gestion redeploy failed (non-fatal):', err);
    }
    // 202 Accepted: the refresh is running, but the fresh data lands minutes
    // later (pipeline + Vercel redeploy), so nothing to return but the ids.
    return res.status(202).json({ ok: true, deploymentId: data.serviceInstanceRedeploy, cruceDeploymentId });
  } catch (err) {
    return res.status(502).json({ error: String((err && err.message) || err) });
  }
};

// Exposed for the self-check (api/refresh.test.js); Vercel uses the default export.
module.exports.verifyFirebaseToken = verifyFirebaseToken;
