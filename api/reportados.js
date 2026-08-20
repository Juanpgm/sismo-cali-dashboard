// Vercel Serverless Function — live count of citizen reports for the
// "Reportados" KPI, read straight from the atencionsismo API (informe/json),
// NOT from the static reportes_agg.json committed by the Railway pipeline.
//
// The API requires HTTP Basic auth, so the browser cannot call it directly;
// this endpoint is the server hop. The CDN caches the response for 15 minutes
// (s-maxage=900 + stale-while-revalidate), so the API is re-read at most every
// 15 min with zero cron jobs, zero commits and zero redeploys.
//
// The API 504s on the full history in one request and 413s on the densest
// days, so — same algorithm as scripts/fetch_reportes_api.py — we walk the
// range in day windows and split any window that still 413/504s, down to the
// minute. Only id + estadoVerificacion are kept: this endpoint counts, it
// never republishes report contents.
//
// Required env in Vercel (Project Settings → Environment Variables):
//   VISITADOS_API_PASS   password for the operator/viewer account (secret)
// Optional:
//   VISITADOS_API_USER   default juanp.gzmz@gmail.com
//   REPORTES_DESDE       floor date YYYY-MM-DD (default 2026-08-01)

const API_URL = 'https://atencionsismo.cali.gov.co/api/informe/json';
const DEFAULT_USER = 'juanp.gzmz@gmail.com';
const DAY_MS = 86_400_000;
const MIN_WINDOW_MS = 60_000; // smallest split before giving up on a window
const CONCURRENCY = 4; // parallel day windows; the API tolerates this fine

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchWindow(auth, d0, d1) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(`${API_URL}?desde_utc=${d0}&hasta_utc=${d1}`, {
        headers: { Authorization: `Basic ${auth}` },
        signal: AbortSignal.timeout(90_000),
      });
      if ((res.status === 413 || res.status === 504) && d1 - d0 > MIN_WINDOW_MS) {
        const mid = Math.floor((d0 + d1) / 2);
        const [a, b] = await Promise.all([
          fetchWindow(auth, d0, mid),
          fetchWindow(auth, mid + 1, d1),
        ]);
        return a.concat(b);
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      return (json.reportes || []).map((r) => ({
        id: r.id,
        estado: r.estadoVerificacion || '—',
      }));
    } catch (err) {
      if (attempt === 2) {
        console.error(`window ${new Date(d0).toISOString()} failed:`, err.message || err);
        return [];
      }
      await sleep(2000);
    }
  }
  return [];
}

async function countReportes(auth, desde) {
  const start = Date.parse(`${desde}T00:00:00Z`);
  const end = Date.now() + DAY_MS;
  const windows = [];
  for (let d0 = start; d0 < end; d0 += DAY_MS) {
    windows.push([d0, d0 + DAY_MS - 1]);
  }

  const seen = new Map(); // id -> estado (dedup across windows)
  for (let i = 0; i < windows.length; i += CONCURRENCY) {
    const batch = windows.slice(i, i + CONCURRENCY);
    const results = await Promise.all(batch.map(([d0, d1]) => fetchWindow(auth, d0, d1)));
    for (const reps of results) {
      for (const rep of reps) if (rep.id && !seen.has(rep.id)) seen.set(rep.id, rep.estado);
    }
  }

  const porEstado = {};
  for (const estado of seen.values()) porEstado[estado] = (porEstado[estado] || 0) + 1;
  return { total: seen.size, por_estadoVerificacion: porEstado };
}

module.exports = async (req, res) => {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const pass = (process.env.VISITADOS_API_PASS || '').trim();
  if (!pass) {
    return res.status(500).json({ error: 'VISITADOS_API_PASS no está configurado en Vercel.' });
  }
  const user = (process.env.VISITADOS_API_USER || '').trim() || DEFAULT_USER;
  const auth = Buffer.from(`${user}:${pass}`).toString('base64');
  const desde = process.env.REPORTES_DESDE || '2026-08-01';

  try {
    const counts = await countReportes(auth, desde);
    if (counts.total === 0) {
      // Never cache/serve an empty count over a transient API failure; the
      // frontend falls back to the static aggregate.
      return res.status(502).json({ error: 'la API devolvió 0 reportes' });
    }
    // ponytail: the day walk grows with the date range (~150s today); if it
    // ever nears the 300s function limit, persist a checkpoint or narrow desde.
    res.setHeader('Cache-Control', 'public, s-maxage=900, stale-while-revalidate=86400');
    return res.status(200).json({
      ok: true,
      generado: new Date().toISOString(),
      fuente: 'api:informe/json',
      ...counts,
    });
  } catch (err) {
    return res.status(502).json({ error: String((err && err.message) || err) });
  }
};
