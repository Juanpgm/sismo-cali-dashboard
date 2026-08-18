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

const RAILWAY_API = 'https://backboard.railway.com/graphql/v2';
const SERVICE_ID = process.env.RAILWAY_SERVICE_ID || '156e97a2-596b-4861-95f4-4060dab408e2';
const ENVIRONMENT_ID = process.env.RAILWAY_ENVIRONMENT_ID || '4418f451-bd97-4d96-ba6e-b5ecbbd49c9b';

// serviceInstanceRedeploy redeploys the service's latest deployment (i.e. runs
// the cron container now) and returns the new deployment id.
const REDEPLOY = `mutation($s:String!,$e:String!){
  serviceInstanceRedeploy(serviceId:$s, environmentId:$e) }`;

async function railway(token, query, variables) {
  const res = await fetch(RAILWAY_API, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      // Cloudflare answers 403 to requests without a User-Agent.
      'User-Agent': 'sismo-cali-dashboard/1.0',
    },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok || json.errors) {
    throw new Error(`Railway API ${res.status}: ${JSON.stringify(json.errors || json)}`);
  }
  return json.data;
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const token = process.env.RAILWAY_API_TOKEN;
  if (!token) {
    return res
      .status(500)
      .json({ error: 'RAILWAY_API_TOKEN no está configurado en Vercel.' });
  }

  try {
    const data = await railway(token, REDEPLOY, { s: SERVICE_ID, e: ENVIRONMENT_ID });
    // 202 Accepted: the refresh is running, but the fresh data lands minutes
    // later (pipeline + Vercel redeploy), so nothing to return but the id.
    return res.status(202).json({ ok: true, deploymentId: data.serviceInstanceRedeploy });
  } catch (err) {
    return res.status(502).json({ error: String((err && err.message) || err) });
  }
};
