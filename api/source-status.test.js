// Self-check for api/source-status.js. Run: node api/source-status.test.js
//
// The auth preamble is exercised with mock req/res objects and a stubbable
// verifyFirebaseToken/roleFromClaims seam (same idea as api/usuarios.test.js,
// which stubs the pure exported predicates). The real-network probe assertion
// is gated behind RUN_LIVE_PROBE + VISITADOS_API_PASS so this file runs green
// in credential-less environments (CI, a fresh clone) — it prints a skip line
// and passes instead of failing.
const assert = require('assert');

// ---- mock req/res -----------------------------------------------------------
function mockReq({ authorization } = {}) {
  return { method: 'GET', headers: authorization ? { authorization } : {} };
}
function mockRes() {
  const res = { statusCode: null, body: null, headers: {} };
  res.status = (code) => { res.statusCode = code; return res; };
  res.json = (body) => { res.body = body; return res; };
  res.setHeader = (k, v) => { res.headers[k] = v; return res; };
  return res;
}

async function run() {
  const handler = require('./source-status.js');

  // 1. Missing Authorization header -> 401, no probe attempted.
  {
    const res = mockRes();
    await handler(mockReq(), res);
    assert.strictEqual(res.statusCode, 401, 'missing token must be rejected with 401');
  }

  // 2. Invalid/expired token -> 401. A malformed JWT (not 3 dot-separated
  //    base64url segments) fails verifyFirebaseToken's own parsing before any
  //    network call, so this exercises the real code path without stubbing.
  {
    const res = mockRes();
    await handler(mockReq({ authorization: 'Bearer not-a-real-jwt' }), res);
    assert.strictEqual(res.statusCode, 401, 'invalid token must be rejected with 401');
  }

  // 3 & 4 need a verified admin/non-admin claim without hitting Google's cert
  // endpoint or minting a real signed JWT. source-status.js exposes the same
  // injectable-dependency seam usuarios.js's self-check relies on conceptually:
  // here it's done by exporting the pure `handle({ verify, probe })` factory
  // used internally, so the HTTP auth-header parsing is real but token
  // verification and the upstream probe are swapped for fixtures.
  const { handle } = require('./source-status.js');
  assert.strictEqual(typeof handle, 'function', 'source-status.js must export handle() for testing');

  // 3. Valid token, non-admin role -> 403, no probe attempted.
  {
    let probeCalled = false;
    const fakeHandler = handle({
      verify: async () => ({ role: 'usuario', email: 'someone@example.com' }),
      probe: async () => { probeCalled = true; },
    });
    const res = mockRes();
    await fakeHandler(mockReq({ authorization: 'Bearer whatever' }), res);
    assert.strictEqual(res.statusCode, 403, 'non-admin caller must be rejected with 403');
    assert.strictEqual(probeCalled, false, 'the probe must not run for a rejected caller');
  }

  // 3b. Verify throwing (expired/invalid token, from the real dependency's
  //     point of view) -> 401.
  {
    const fakeHandler = handle({
      verify: async () => { throw new Error('token expirado'); },
      probe: async () => {},
    });
    const res = mockRes();
    await fakeHandler(mockReq({ authorization: 'Bearer whatever' }), res);
    assert.strictEqual(res.statusCode, 401, 'a throwing verify must be rejected with 401');
  }

  // 4. Forced bad credentials (probe rejects, mirroring probeApi's throw
  //    contract) -> 200 { ok:false, status:'con errores' } with a non-null detail.
  {
    const fakeHandler = handle({
      verify: async () => ({ role: 'admin', email: 'admin@example.com' }),
      probe: async () => { const err = new Error('API no disponible (HTTP 401)'); err.status = 503; throw err; },
    });
    const res = mockRes();
    await fakeHandler(mockReq({ authorization: 'Bearer whatever' }), res);
    assert.strictEqual(res.statusCode, 200, 'a reached-but-down probe still answers 200 (the endpoint itself succeeded)');
    assert.strictEqual(res.body.ok, false);
    assert.strictEqual(res.body.status, 'con errores');
    assert.ok(res.body.detail, 'detail must be non-null so rojo is a real, explained signal');
    assert.ok(res.body.checked_at, 'checked_at must be present');
    const cacheControlNotOk = res.headers['Cache-Control'];
    assert.ok(!cacheControlNotOk.includes('public'), 'ok:false responses must not be shared/CDN-cacheable (admin-gated endpoint)');
    assert.ok(cacheControlNotOk.includes('no-store'), 'ok:false responses must be non-cached (private, no-store)');
  }

  // 4b. Admin caller, probe resolves (alive) -> 200 { ok:true, status:'conectado' }.
  {
    const fakeHandler = handle({
      verify: async () => ({ role: 'admin', email: 'admin@example.com' }),
      probe: async () => {}, // probeApi resolves (no throw) when the API is alive
    });
    const res = mockRes();
    await fakeHandler(mockReq({ authorization: 'Bearer whatever' }), res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true);
    assert.strictEqual(res.body.status, 'conectado');
    assert.ok(res.body.checked_at);
    const cacheControlOk = res.headers['Cache-Control'];
    assert.ok(!cacheControlOk.includes('public'), 'ok:true responses must not be shared/CDN-cacheable (admin-gated endpoint)');
    assert.ok(cacheControlOk.includes('no-store'), 'ok:true responses must be non-cached (private, no-store)');
  }

  // 5. RUN_LIVE_PROBE-gated real-network case: valid admin token + real
  //    VISITADOS_API_PASS -> ok:true. Skips (prints a line, still passes) when
  //    the gate or the credential is absent.
  if (process.env.RUN_LIVE_PROBE && process.env.VISITADOS_API_PASS) {
    const res = mockRes();
    const fakeHandler = handle({ verify: async () => ({ role: 'admin', email: 'admin@example.com' }) }); // real probe (no probe override)
    await fakeHandler(mockReq({ authorization: 'Bearer whatever' }), res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true, `expected a reachable atencionsismo API, got: ${JSON.stringify(res.body)}`);
    assert.strictEqual(res.body.status, 'conectado');
  } else {
    console.log('source-status.test.js: skipping RUN_LIVE_PROBE real-network assertion (set RUN_LIVE_PROBE=1 and VISITADOS_API_PASS to run it)');
  }

  console.log('source-status.test.js OK');
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
