// Self-check for drive-viewer.js's pure logic: mime classification, thumbnail
// URL sizing, carousel index wrap-around, and the Drive API paging/error
// paths. DOM-mounting (mountDriveCarousel) is left untested, same convention
// as the rest of this app's *.test.mjs files — no jsdom here, only fetch is
// mockable without a real browser.
// Run: node web/js/drive-viewer.test.mjs
import assert from 'node:assert/strict';
import { listDriveFolder, mimeKind, thumbSrc, wrapIndex } from './drive-viewer.js';

// --- mimeKind -------------------------------------------------------------
assert.equal(mimeKind('image/jpeg'), 'image');
assert.equal(mimeKind('image/png'), 'image');
assert.equal(mimeKind('video/mp4'), 'video');
assert.equal(mimeKind('video/quicktime'), 'video');
assert.equal(mimeKind('application/pdf'), 'other');
assert.equal(mimeKind('application/vnd.google-apps.folder'), 'other', 'folders are filtered upstream, not classified as media');
assert.equal(mimeKind(''), 'other', 'empty string -> other, not a crash');
assert.equal(mimeKind(null), 'other', 'null -> other, not a crash');
assert.equal(mimeKind(undefined), 'other', 'undefined -> other, not a crash');

// --- thumbSrc ---------------------------------------------------------------
assert.equal(thumbSrc({ thumbnailLink: 'https://lh3.googleusercontent.com/abc=s220' }, 1600), 'https://lh3.googleusercontent.com/abc=s1600', 'existing =s### suffix is replaced');
assert.equal(thumbSrc({ thumbnailLink: 'https://lh3.googleusercontent.com/abc' }, 160), 'https://lh3.googleusercontent.com/abc=s160', 'no suffix -> one is appended');
assert.equal(thumbSrc({ thumbnailLink: null }, 1600), null, 'missing thumbnailLink -> null, not a broken URL');
assert.equal(thumbSrc({}, 1600), null, 'file with no thumbnailLink key at all -> null');
assert.equal(thumbSrc(undefined, 1600), null, 'undefined file -> null, not a throw');
assert.equal(thumbSrc(null, 1600), null, 'null file -> null, not a throw');

// --- wrapIndex (carousel next/prev boundary transitions) --------------------
assert.equal(wrapIndex(0, -1, 5), 4, 'stepping back from the first item wraps to the last');
assert.equal(wrapIndex(4, 1, 5), 0, 'stepping forward from the last item wraps to the first');
assert.equal(wrapIndex(2, 1, 5), 3, 'ordinary forward step');
assert.equal(wrapIndex(2, -1, 5), 1, 'ordinary backward step');
assert.equal(wrapIndex(0, -1, 1), 0, 'single-item list stays put stepping back');
assert.equal(wrapIndex(0, 1, 1), 0, 'single-item list stays put stepping forward');
assert.equal(wrapIndex(0, 1, 0), 0, 'empty list never divides by zero / returns NaN');
assert.equal(wrapIndex(0, -1, 0), 0, 'empty list, backward step, still 0');

// --- listDriveFolder: calls our own server proxy, never googleapis.com directly ---
// Pagination, folder filtering and the Drive API key itself all live
// server-side now (api/drive-folder.js, covered by api/drive-folder.test.js)
// — the client only has to parse whatever /api/drive-folder answers.
{
  const originalFetch = global.fetch;
  let calledUrl = null;
  global.fetch = async (url) => {
    calledUrl = String(url);
    return { ok: true, json: async () => ({ ok: true, files: [{ id: '1', name: 'DJI_0001.JPG', mimeType: 'image/jpeg' }] }) };
  };
  const files = await listDriveFolder('folder-1');
  assert.equal(calledUrl, '/api/drive-folder?id=folder-1', 'must hit our own proxy endpoint, not googleapis.com');
  assert.deepEqual(files.map((f) => f.id), ['1']);
  global.fetch = originalFetch;
}

// --- listDriveFolder: folder id is URL-encoded ------------------------------
{
  const originalFetch = global.fetch;
  let calledUrl = null;
  global.fetch = async (url) => { calledUrl = String(url); return { ok: true, json: async () => ({ files: [] }) }; };
  await listDriveFolder('weird id/with?chars');
  assert.equal(calledUrl, `/api/drive-folder?id=${encodeURIComponent('weird id/with?chars')}`);
  global.fetch = originalFetch;
}

// --- listDriveFolder: empty folder --------------------------------------------
{
  const originalFetch = global.fetch;
  global.fetch = async () => ({ ok: true, json: async () => ({ files: [] }) });
  assert.deepEqual(await listDriveFolder('folder-empty'), []);
  global.fetch = originalFetch;
}

// --- listDriveFolder: malformed response (no `files` key at all) -------------
{
  const originalFetch = global.fetch;
  global.fetch = async () => ({ ok: true, json: async () => ({}) });
  assert.deepEqual(await listDriveFolder('folder-malformed'), [], 'missing `files` key degrades to an empty list, not a throw');
  global.fetch = originalFetch;
}

// --- listDriveFolder: proxy error paths (4xx/5xx) with the proxied message ---
{
  const originalFetch = global.fetch;
  for (const [status, error] of [[400, 'Parámetro "id" de carpeta de Drive ausente o inválido.'], [500, 'GOOGLE_DRIVE_API_KEY no está configurado en Vercel.'], [502, 'Internal error']]) {
    global.fetch = async () => ({ ok: false, status, json: async () => ({ error }) });
    // eslint-disable-next-line no-await-in-loop -- sequential status cases, deterministic
    await assert.rejects(() => listDriveFolder('folder-err'), { message: error });
  }
  global.fetch = originalFetch;
}

// --- listDriveFolder: HTTP error with an unparseable body -> falls back to status ---
{
  const originalFetch = global.fetch;
  global.fetch = async () => ({ ok: false, status: 502, json: async () => { throw new SyntaxError('Unexpected token'); } });
  await assert.rejects(() => listDriveFolder('folder-badbody'), { message: 'HTTP 502' });
  global.fetch = originalFetch;
}

// --- listDriveFolder: network failure propagates -----------------------------
{
  const originalFetch = global.fetch;
  global.fetch = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(() => listDriveFolder('folder-network'), { message: 'Failed to fetch' });
  global.fetch = originalFetch;
}

console.log('drive-viewer.test.mjs: all assertions passed');
