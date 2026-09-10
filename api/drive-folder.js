// Vercel Serverless Function — proxies a Google Drive folder's file listing
// for the Vuelos UAS "Ver fotos" in-app carousel (web/js/drive-viewer.js).
//
// The browser never talks to the Drive API directly: this repo's frontend
// (web/) is served as static files with no build step (vercel.json has no
// buildCommand), so a client-side key would have to be committed to source
// and shipped verbatim in the page. Keeping the key server-side makes it a
// real secret instead — no HTTP-referrer restriction needed, never appears
// in git or in view-source — same idea as api/reportados.js's Basic-auth hop
// in front of an API the browser can't call directly.
//
// Required env in Vercel (Project Settings → Environment Variables — set it
// for Production, Preview AND Development, then redeploy):
//   GOOGLE_DRIVE_API_KEY   Google Cloud API key with the Drive API enabled.
//                          No HTTP-referrer restriction needed (it never
//                          leaves the server), but still scope its "API
//                          restrictions" to Drive API only.
//
// The target folders must stay shared as "Anyone with the link — Viewer" —
// this endpoint uses the API key alone (no OAuth), so it can only see what
// an anonymous viewer could already see by opening the folder's URL.

const FIELDS = 'nextPageToken,files(id,name,mimeType,thumbnailLink)';
const FOLDER_MIME = 'application/vnd.google-apps.folder';
// Drive folder/file IDs are URL-safe: letters, digits, - and _. Validating
// this before it reaches the `q` filter also rules out query injection.
const ID_RE = /^[\w-]+$/;

/** Lists every non-folder file directly inside `folderId`, paging through
 *  nextPageToken. Throws on a non-OK response or a network failure; the
 *  thrown Error carries `.status` for the handler to pass through. */
async function listFolder(folderId, apiKey) {
  const files = [];
  let pageToken = '';
  do {
    const params = new URLSearchParams({
      q: `'${folderId}' in parents and trashed = false`,
      key: apiKey,
      fields: FIELDS,
      pageSize: '1000',
      orderBy: 'name_natural',
    });
    if (pageToken) params.set('pageToken', pageToken);
    // eslint-disable-next-line no-await-in-loop -- sequential pages, deterministic
    const res = await fetch(`https://www.googleapis.com/drive/v3/files?${params}`, {
      signal: AbortSignal.timeout(20_000),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(json?.error?.message || `HTTP ${res.status}`);
      err.status = res.status >= 400 && res.status < 500 ? res.status : 502;
      throw err;
    }
    for (const f of json.files || []) {
      if (f && f.mimeType !== FOLDER_MIME) files.push(f);
    }
    pageToken = json.nextPageToken || '';
  } while (pageToken);
  return files;
}

module.exports = async (req, res) => {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const folderId = String(req.query?.id || '').trim();
  if (!folderId || !ID_RE.test(folderId)) {
    return res.status(400).json({ error: 'Parámetro "id" de carpeta de Drive ausente o inválido.' });
  }

  const apiKey = (process.env.GOOGLE_DRIVE_API_KEY || '').trim();
  if (!apiKey) {
    return res.status(500).json({ error: 'GOOGLE_DRIVE_API_KEY no está configurado en Vercel.' });
  }

  try {
    const files = await listFolder(folderId, apiKey);
    // Folder contents change slowly (a field team adds photos over days, not
    // seconds) — same caching idea as the /data/*.json headers in vercel.json.
    res.setHeader('Cache-Control', 'public, s-maxage=300, stale-while-revalidate=3600');
    return res.status(200).json({ ok: true, files });
  } catch (err) {
    const status = (err && err.status) || 502;
    return res.status(status).json({ error: String((err && err.message) || err) });
  }
};

// Exposed for the self-check (api/drive-folder.test.js).
module.exports.listFolder = listFolder;
