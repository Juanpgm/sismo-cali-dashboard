// Self-check for api/drive-folder.js. Run: node api/drive-folder.test.js
const assert = require('assert');

function mockReq({ method = 'GET', query = {} } = {}) {
  return { method, query };
}
function mockRes() {
  const res = { statusCode: null, body: null, headers: {} };
  res.status = (code) => { res.statusCode = code; return res; };
  res.json = (body) => { res.body = body; return res; };
  res.setHeader = (k, v) => { res.headers[k] = v; return res; };
  return res;
}

async function run() {
  const handler = require('./drive-folder.js');
  const originalFetch = global.fetch;
  const originalKey = process.env.GOOGLE_DRIVE_API_KEY;

  // 1. Non-GET -> 405.
  {
    const res = mockRes();
    await handler(mockReq({ method: 'POST' }), res);
    assert.strictEqual(res.statusCode, 405);
  }

  // 2. Missing id -> 400, no fetch attempted.
  {
    let called = false;
    global.fetch = async () => { called = true; };
    const res = mockRes();
    await handler(mockReq({ query: {} }), res);
    assert.strictEqual(res.statusCode, 400);
    assert.strictEqual(called, false, 'must not call the Drive API without a folder id');
  }

  // 3. Malformed/hostile id (e.g. an attempted `q` filter injection via a
  //    quote) -> 400, no fetch attempted.
  {
    let called = false;
    global.fetch = async () => { called = true; };
    const res = mockRes();
    await handler(mockReq({ query: { id: "abc' or 1=1" } }), res);
    assert.strictEqual(res.statusCode, 400);
    assert.strictEqual(called, false);
  }

  // 4. Valid id but GOOGLE_DRIVE_API_KEY unset -> 500 with a clear message,
  //    no fetch attempted (this is the state right after this endpoint is
  //    deployed but before the env var is added in Vercel).
  {
    delete process.env.GOOGLE_DRIVE_API_KEY;
    let called = false;
    global.fetch = async () => { called = true; };
    const res = mockRes();
    await handler(mockReq({ query: { id: 'abc123-_XYZ' } }), res);
    assert.strictEqual(res.statusCode, 500);
    assert.match(res.body.error, /GOOGLE_DRIVE_API_KEY/);
    assert.strictEqual(called, false);
  }

  process.env.GOOGLE_DRIVE_API_KEY = 'fake-key-for-tests';

  // 5. Happy path: single page, subfolder entry filtered out, Cache-Control set.
  {
    global.fetch = async () => ({
      ok: true,
      json: async () => ({
        files: [
          { id: '1', name: 'DJI_0001.JPG', mimeType: 'image/jpeg' },
          { id: '2', name: 'Sub', mimeType: 'application/vnd.google-apps.folder' },
        ],
      }),
    });
    const res = mockRes();
    await handler(mockReq({ query: { id: 'folder-1' } }), res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true);
    assert.deepStrictEqual(res.body.files.map((f) => f.id), ['1']);
    assert.ok(res.headers['Cache-Control'].includes('public'), 'successful listings are CDN-cacheable');
  }

  // 6. Pagination: nextPageToken is followed and both pages concatenated.
  {
    let calls = 0;
    global.fetch = async (url) => {
      calls += 1;
      const hasToken = /pageToken=/.test(String(url));
      if (!hasToken) return { ok: true, json: async () => ({ nextPageToken: 'p2', files: [{ id: 'a', name: 'a.jpg', mimeType: 'image/jpeg' }] }) };
      return { ok: true, json: async () => ({ files: [{ id: 'b', name: 'b.jpg', mimeType: 'image/jpeg' }] }) };
    };
    const res = mockRes();
    await handler(mockReq({ query: { id: 'folder-2' } }), res);
    assert.strictEqual(calls, 2);
    assert.deepStrictEqual(res.body.files.map((f) => f.id), ['a', 'b']);
  }

  // 7. Empty folder -> 200 with an empty files array (not an error).
  {
    global.fetch = async () => ({ ok: true, json: async () => ({ files: [] }) });
    const res = mockRes();
    await handler(mockReq({ query: { id: 'folder-empty' } }), res);
    assert.strictEqual(res.statusCode, 200);
    assert.deepStrictEqual(res.body.files, []);
  }

  // 8. Malformed upstream body (no `files` key) -> degrades to empty, not a 5xx.
  {
    global.fetch = async () => ({ ok: true, json: async () => ({}) });
    const res = mockRes();
    await handler(mockReq({ query: { id: 'folder-malformed' } }), res);
    assert.strictEqual(res.statusCode, 200);
    assert.deepStrictEqual(res.body.files, []);
  }

  // 9. Drive API 4xx (e.g. bad key / folder not shared) -> passthrough status + message.
  {
    global.fetch = async () => ({ ok: false, status: 403, json: async () => ({ error: { message: 'The caller does not have permission' } }) });
    const res = mockRes();
    await handler(mockReq({ query: { id: 'folder-forbidden' } }), res);
    assert.strictEqual(res.statusCode, 403);
    assert.strictEqual(res.body.error, 'The caller does not have permission');
  }

  // 10. Drive API 5xx -> passed through as 502 (never surfaced as a client error).
  {
    global.fetch = async () => ({ ok: false, status: 500, json: async () => ({ error: { message: 'Internal error' } }) });
    const res = mockRes();
    await handler(mockReq({ query: { id: 'folder-5xx' } }), res);
    assert.strictEqual(res.statusCode, 502);
  }

  // 11. Network failure (fetch rejects) -> 502 with the underlying message.
  {
    global.fetch = async () => { throw new TypeError('fetch failed'); };
    const res = mockRes();
    await handler(mockReq({ query: { id: 'folder-network' } }), res);
    assert.strictEqual(res.statusCode, 502);
    assert.match(res.body.error, /fetch failed/);
  }

  global.fetch = originalFetch;
  if (originalKey === undefined) delete process.env.GOOGLE_DRIVE_API_KEY;
  else process.env.GOOGLE_DRIVE_API_KEY = originalKey;

  console.log('drive-folder.test.js OK');
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
