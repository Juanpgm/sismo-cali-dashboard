// Vercel Serverless Function — RETIRED. This endpoint used to run the live
// day-walk against the atencionsismo API for the "Reportados" KPI; that
// logic now lives in the consolidated Railway backend's GET /reportados
// route (backend/app/routers/reportados.py), which serves from the
// Blob-persisted snapshot instead of re-walking the upstream API on every
// request. web/js/api-config.js already points `reportados` at Railway, so
// nothing in this app calls this relative Vercel path anymore.
//
// The handler below only answers 410 Gone so any stray caller (an old
// bookmark, a stale client build) gets a clear, explained signal instead of
// a confusing 404 or a half-working legacy response.
//
// probeApi is the ONE thing this file still exports for real use:
// api/source-status.js's live "is the atencionsismo API reachable right
// now?" check reuses it (the same one-minute-window probe the old day-walk
// ran before starting). Keep it — and its own API_URL/MIN_WINDOW_MS
// constants — working exactly as before. Everything else the old day-walk
// needed (countReportes/fetchWindow/coordKey/lastFailure/failedWindows/
// sleep, plus the DEFAULT_USER/VISITADOS_API_* env reads only that handler
// used) is gone now that nothing calls it anymore.

const API_URL = 'https://atencionsismo.cali.gov.co/api/informe/json';
const MIN_WINDOW_MS = 60_000; // smallest probe window; matches the old day-walk's split floor

// One tiny probe (a 1-minute window) before the full day walk: while the API
// is down for maintenance it answers 503 to everything, and without this the
// walk retries every window 3 times (~25s of guaranteed failure per uncached
// page load — error responses are not CDN-cached). A healthy API pays one
// ~200ms request.
async function probeApi(auth) {
  const now = Date.now();
  const res = await fetch(`${API_URL}?desde_utc=${now - MIN_WINDOW_MS}&hasta_utc=${now}`, {
    headers: { Authorization: `Basic ${auth}`, 'User-Agent': 'sismo-cali-dashboard/1.0' },
    signal: AbortSignal.timeout(15_000),
  }).catch(() => null);
  // 413/504 = alive but window too dense (fine); anything else non-ok = down.
  if (!res || (!res.ok && res.status !== 413 && res.status !== 504)) {
    const status = res ? `HTTP ${res.status}` : 'sin respuesta';
    const err = new Error(`API no disponible (${status})`);
    err.status = 503;
    throw err;
  }
}

module.exports = async (req, res) => {
  return res.status(410).json({
    error: 'Este endpoint fue retirado; el backend consolidado en Railway sirve /reportados.',
  });
};

// Exposed for reuse (api/source-status.js's atencionsismo live probe); Vercel
// uses the default export.
module.exports.probeApi = probeApi;
