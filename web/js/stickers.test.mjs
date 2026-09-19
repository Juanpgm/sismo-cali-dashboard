// Self-check for the pure helpers behind the Stickers tab's fetch layer.
// Run: node web/js/stickers.test.mjs
import assert from 'node:assert/strict';
import {
  tagFuente, errorMessageFor, fetchEvaluacionesOnce, fetchEvaluacionesForTab, buildEvaluacionesUrl, usableEtag,
} from './stickers.js';

// --- tagFuente: a record's own `fuente` wins over the response's top-level one
// DELIBERATE UPDATE (PR 10 part 2, task 11.22): the full-object assertions below
// gain `etag: null` -- tagFuente now surfaces the response ETag additively (a
// caller that passes none gets `null`, never `undefined`).
assert.deepStrictEqual(
  tagFuente({ fuente: 'atencionsismo', evaluaciones: [{ id: '1', fuente: 'firestore' }], degraded: false }),
  { evaluaciones: [{ id: '1', fuente: 'firestore' }], degraded: false, depuracion: null, etag: null },
  'a record carrying its own fuente must not be overwritten by the response fuente',
);

// --- tagFuente: missing top-level `fuente` -> defaults to 'firestore' (GET
// /evaluaciones's legacy shape, which predates the field).
assert.deepStrictEqual(
  tagFuente({ evaluaciones: [{ id: '1' }], degraded: false }),
  { evaluaciones: [{ id: '1', fuente: 'firestore' }], degraded: false, depuracion: null, etag: null },
);

// --- tagFuente: the ETag (a response HEADER, passed as the 2nd argument) is
// surfaced verbatim when usable, `null` when not.
assert.strictEqual(tagFuente({ evaluaciones: [] }, '"abc123"').etag, '"abc123"');
assert.strictEqual(tagFuente({ evaluaciones: [] }, 'W/"abc123"').etag, 'W/"abc123"', 'a weak validator (from an intermediary) is kept as-is');
assert.strictEqual(tagFuente({ evaluaciones: [] }, undefined).etag, null);
assert.strictEqual(tagFuente({ evaluaciones: [] }, '').etag, null);
assert.strictEqual(tagFuente(null, '"x"').etag, '"x"', 'a malformed body still carries the header');

// --- tagFuente: `depuracion` (seguimiento-inspectores-depurado Fase 3) is
// passed through as-is when present (seguimiento.js's buildIdentityIndex is
// the only consumer that interprets its shape) -- initStickers' OWN endpoint
// never sends this field, so it must default to `null`, never `undefined`
// (a `deepStrictEqual` against a fixed shape, same as every other tagFuente
// assertion here) and never throw on a malformed (non-object) value.
assert.deepStrictEqual(
  tagFuente({ evaluaciones: [], depuracion: { activa: true, inspectores: [] } }).depuracion,
  { activa: true, inspectores: [] },
);
assert.strictEqual(tagFuente({ evaluaciones: [] }).depuracion, null, 'missing depuracion -> null, never undefined');
assert.strictEqual(tagFuente({ evaluaciones: [], depuracion: null }).depuracion, null);
assert.strictEqual(tagFuente(null).depuracion, null, 'a null/malformed data object must never throw');

// --- tagFuente: non-array `evaluaciones` -> [] (never throws on a malformed
// payload)
assert.deepStrictEqual(tagFuente({ fuente: 'atencionsismo', evaluaciones: null, degraded: false }).evaluaciones, []);
assert.deepStrictEqual(tagFuente({ fuente: 'atencionsismo', degraded: false }).evaluaciones, []);
assert.deepStrictEqual(tagFuente({ fuente: 'atencionsismo', evaluaciones: 'oops', degraded: false }).evaluaciones, []);

// --- tagFuente: degraded coerced to a real boolean
assert.strictEqual(tagFuente({ evaluaciones: [] }).degraded, false);
assert.strictEqual(tagFuente({ evaluaciones: [], degraded: 1 }).degraded, true);

// --- tagFuente: multiple records, mixed own-fuente / fallback
assert.deepStrictEqual(
  tagFuente({
    fuente: 'atencionsismo',
    evaluaciones: [{ id: 'a', fuente: 'firestore' }, { id: 'b' }],
    degraded: false,
  }).evaluaciones,
  [{ id: 'a', fuente: 'firestore' }, { id: 'b', fuente: 'atencionsismo' }],
);

console.log('ok — tagFuente');

// --- errorMessageFor: structured `error` wins ------------------------------
assert.strictEqual(errorMessageFor({ error: 'boom' }, 500), 'boom');

// --- errorMessageFor: string `detail` used when there is no `error` --------
assert.strictEqual(errorMessageFor({ detail: 'Sesión expirada' }, 401), 'Sesión expirada');

// --- errorMessageFor: non-string `detail` (e.g. a FastAPI validation-error
// array/object) must NOT stringify to "[object Object]" — fall back to the
// generic status message instead.
assert.strictEqual(errorMessageFor({ detail: [{ msg: 'invalid' }] }, 422), 'Error 422');
assert.strictEqual(errorMessageFor({ detail: { msg: 'invalid' } }, 500), 'Error 500');
assert.strictEqual(errorMessageFor({}, 503), 'Error 503');
assert.strictEqual(errorMessageFor(null, 500), 'Error 500');

console.log('ok — errorMessageFor');

// ══ PR 10 part 2 (tasks 11.21-11.23, design D25/D28): the opt-in `depuracion`
// request, the ETag / conditional GET plumbing and the Stickers tab's isolation.
// Each block is a NAMED async test; failures are collected so a RED run lists
// every missing behaviour, and the file throws at the end.

const failures = [];
async function named(name, fn) {
  const consoleCalls = [];
  const saved = {};
  for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
    saved[level] = console[level];
    console[level] = (...args) => consoleCalls.push([level, ...args]);
  }
  const savedFetch = globalThis.fetch;
  try {
    await fn();
    assert.deepStrictEqual(consoleCalls, [], 'the fetch layer must never write to the console');
    for (const level of Object.keys(saved)) console[level] = saved[level];
    console.log(`${name} OK`);
  } catch (err) {
    for (const level of Object.keys(saved)) console[level] = saved[level];
    failures.push(name);
    console.error(`${name} FAILED: ${err && err.message ? String(err.message).split('\n')[0] : err}`);
  } finally {
    globalThis.fetch = savedFetch;
  }
}

function fakeResponse({ status = 200, body = {}, etag = null, jsonThrows = false } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => (String(name).toLowerCase() === 'etag' ? etag : null) },
    json: async () => {
      if (jsonThrows) throw new SyntaxError('Unexpected token < in JSON');
      return body;
    },
  };
}

/** Installs a fetch stub; `respond(n, url, init)` answers call number n (1-based). */
function stubFetch(respond) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    return respond(calls.length, String(url), init);
  };
  return calls;
}

const token = async () => 'TOKEN-DO-NOT-LOG';
const headerOf = (call, name) => {
  const headers = call.init.headers || {};
  const key = Object.keys(headers).find((k) => k.toLowerCase() === name.toLowerCase());
  return key ? headers[key] : undefined;
};
const PII_BLOCK = { activa: true, inspectores: [{ identificacion: '1000001', nombre_completo: 'Nombre Secreto' }] };

await named('test_buildEvaluacionesUrl_appends_depuracion_with_the_right_separator', () => {
  const base = 'https://api.example.test/stickers-atencionsismo';
  assert.equal(buildEvaluacionesUrl(base, { depuracion: false }), base, 'default OFF leaves the URL untouched');
  assert.equal(buildEvaluacionesUrl(base), base);
  assert.equal(buildEvaluacionesUrl(base, { depuracion: true }), `${base}?depuracion=1`);
  // A base URL that already has a query string gets `&`, never a second `?`.
  assert.equal(buildEvaluacionesUrl(`${base}?x=1`, { depuracion: true }), `${base}?x=1&depuracion=1`);
  assert.equal(buildEvaluacionesUrl(`${base}?`, { depuracion: true }), `${base}?depuracion=1`);
  assert.equal(buildEvaluacionesUrl(`${base}?x=1&`, { depuracion: true }), `${base}?x=1&depuracion=1`);
  // A fragment stays last; exactly ONE depuracion param, whatever the base carried.
  assert.equal(buildEvaluacionesUrl(`${base}#frag`, { depuracion: true }), `${base}?depuracion=1#frag`);
  assert.equal(buildEvaluacionesUrl(`${base}?depuracion=0&x=1`, { depuracion: true }), `${base}?x=1&depuracion=1`);
  assert.equal(buildEvaluacionesUrl(`${base}?depuracion=1&depuracion=1`, { depuracion: true }), `${base}?depuracion=1`);
  for (const url of [buildEvaluacionesUrl(`${base}?x=1`, { depuracion: true }), buildEvaluacionesUrl(base, { depuracion: true })]) {
    assert.equal((url.match(/\?/g) || []).length, 1, `a single "?" in ${url}`);
    assert.equal((url.match(/depuracion=/g) || []).length, 1);
  }
  // Only the exact opt-in value is ever produced: never a truthy-ish variant.
  for (const notTrue of [0, '1', 'true', null, undefined, {}]) {
    assert.equal(buildEvaluacionesUrl(base, { depuracion: notTrue }), base, `depuracion=${JSON.stringify(notTrue)} is not an opt-in`);
  }
});

await named('test_usableEtag_accepts_only_sane_opaque_strings', () => {
  assert.equal(usableEtag('"abc"'), '"abc"');
  assert.equal(usableEtag('W/"abc"'), 'W/"abc"');
  assert.equal(usableEtag('  "abc"  '), '"abc"', 'surrounding whitespace is trimmed');
  assert.equal(usableEtag('""'), '""', 'an empty quoted tag is still a tag');
  for (const bad of [null, undefined, '', '   ', 42, {}, [], 'abc\r\nX-Injected: 1', 'a\u0000b', 'café']) {
    assert.equal(usableEtag(bad), null, `unusable: ${JSON.stringify(bad)}`);
  }
  assert.equal(usableEtag(`"${'a'.repeat(510)}"`).length, 512, 'the size ceiling is inclusive');
  assert.equal(usableEtag(`"${'a'.repeat(511)}"`), null, 'a huge ETag is never echoed back as a request header');
  assert.equal(usableEtag(`"${'a'.repeat(100000)}"`), null);
});

await named('test_seguimiento_style_request_sends_depuracion_param_and_bearer', async () => {
  const calls = stubFetch(() => fakeResponse({ body: { fuente: 'atencionsismo', evaluaciones: [{ id: '1' }], depuracion: PII_BLOCK }, etag: '"e1"' }));
  const out = await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: true });
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/stickers-atencionsismo\?depuracion=1$/);
  assert.equal(headerOf(calls[0], 'Authorization'), 'Bearer TOKEN-DO-NOT-LOG');
  assert.equal(headerOf(calls[0], 'If-None-Match'), undefined, 'no validator unless one is passed');
  assert.deepStrictEqual(out.depuracion, PII_BLOCK, 'the opt-in caller receives the block');
  assert.equal(out.etag, '"e1"');
  assert.deepStrictEqual(out.evaluaciones, [{ fuente: 'atencionsismo', id: '1' }]);
});

await named('test_stickers_tab_does_not_request_depuracion', async () => {
  const calls = stubFetch(() => fakeResponse({ body: { evaluaciones: [{ id: '1' }] } }));
  await fetchEvaluacionesOnce(token, 'stickersAtencionsismo');
  await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', {});
  await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: false });
  await fetchEvaluacionesForTab(token);
  assert.equal(calls.length, 4);
  for (const call of calls) {
    assert.doesNotMatch(call.url, /depuracion/, `no depuracion param in ${call.url}`);
    assert.doesNotMatch(call.url, /\?/, 'the Stickers tab URL carries no query at all');
    assert.equal(headerOf(call, 'If-None-Match'), undefined, 'the Stickers tab does no conditional GET');
  }
});

await named('test_stickers_tab_ignores_depuracion_key_if_present', async () => {
  stubFetch(() => fakeResponse({ body: { evaluaciones: [{ id: '1' }], degraded: false, depuracion: PII_BLOCK }, etag: '"e"' }));
  for (const out of [
    await fetchEvaluacionesOnce(token, 'stickersAtencionsismo'),
    await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: false }),
    await fetchEvaluacionesForTab(token),
  ]) {
    assert.equal(out.depuracion, null, 'an unexpected block is dropped, never surfaced to the Stickers tab');
    assert.equal(JSON.stringify(out).includes('Nombre Secreto'), false, 'no PII survives in the tab result');
    assert.equal(out.evaluaciones.length, 1);
  }
  // Triangulation: the SAME body through the opt-in path keeps the block.
  assert.deepStrictEqual((await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: true })).depuracion, PII_BLOCK);
});

await named('test_conditional_get_sends_if_none_match_and_surfaces_304', async () => {
  const calls = stubFetch(() => fakeResponse({ status: 304, etag: '"e1"' }));
  const out = await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: true, ifNoneMatch: '"e1"', conditional: true });
  assert.equal(headerOf(calls[0], 'If-None-Match'), '"e1"', 'the stored ETag is echoed back verbatim');
  assert.deepStrictEqual(out, { notModified: true, etag: '"e1"' });
  // A weak validator (an intermediary weakened it) is echoed as-is, never rewritten.
  const weak = stubFetch(() => fakeResponse({ status: 304 }));
  const out2 = await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: true, ifNoneMatch: 'W/"e2"', conditional: true });
  assert.equal(headerOf(weak[0], 'If-None-Match'), 'W/"e2"');
  assert.deepStrictEqual(out2, { notModified: true, etag: null });
  // Unusable validators are never sent (a huge / control-char value could break the request).
  for (const bad of [`"${'x'.repeat(5000)}"`, 'a\r\nb', '', null]) {
    const c = stubFetch(() => fakeResponse({ body: { evaluaciones: [] } }));
    await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: true, ifNoneMatch: bad, conditional: true });
    assert.equal(headerOf(c[0], 'If-None-Match'), undefined, `not sent: ${JSON.stringify(bad).slice(0, 20)}`);
  }
});

await named('test_304_without_the_conditional_option_is_an_error_never_a_shapeless_result', async () => {
  stubFetch(() => fakeResponse({ status: 304 }));
  await assert.rejects(
    () => fetchEvaluacionesOnce(token, 'stickersAtencionsismo'),
    (err) => err.status === 304 && /304/.test(err.message),
    'the Stickers tab destructures {evaluaciones, degraded}: it must never receive {notModified}',
  );
});

await named('test_etag_forms_missing_empty_weak_huge', async () => {
  const cases = [
    [null, null], ['', null], ['   ', null], ['"e"', '"e"'], ['W/"e"', 'W/"e"'], [`"${'z'.repeat(9000)}"`, null],
  ];
  for (const [header, expected] of cases) {
    stubFetch(() => fakeResponse({ body: { evaluaciones: [] }, etag: header }));
    const out = await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: true });
    assert.equal(out.etag, expected, `ETag header ${JSON.stringify(header).slice(0, 24)}`);
  }
  // A response object with no usable headers at all (a bare stub, an old proxy) never throws.
  for (const headers of [undefined, null, {}, { get: null }, { get() { throw new Error('boom'); } }]) {
    stubFetch(() => ({ ok: true, status: 200, headers, json: async () => ({ evaluaciones: [] }) }));
    assert.equal((await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: true })).etag, null);
  }
});

await named('test_errors_carry_the_status_and_never_leak_the_token', async () => {
  for (const status of [401, 403, 404, 429, 500, 502, 503]) {
    stubFetch(() => fakeResponse({ status, body: { detail: 'nope' } }));
    await assert.rejects(
      () => fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: true }),
      (err) => err.status === status && err.message === 'nope' && !err.message.includes('TOKEN'),
      `status ${status}`,
    );
  }
  stubFetch(() => fakeResponse({ status: 503, jsonThrows: true }));
  await assert.rejects(() => fetchEvaluacionesOnce(token, 'stickersAtencionsismo'), (err) => err.status === 503 && err.message === 'Error 503');
  // A network failure propagates untouched (no status: it is not an HTTP answer).
  stubFetch(() => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(() => fetchEvaluacionesOnce(token, 'stickersAtencionsismo'), (err) => err instanceof TypeError && err.status === undefined);
  // No token -> the existing session error, no request made.
  const calls = stubFetch(() => fakeResponse());
  await assert.rejects(() => fetchEvaluacionesOnce(async () => null, 'stickersAtencionsismo'), /Sesión no válida/);
  assert.equal(calls.length, 0);
});

await named('test_malformed_json_is_tolerated_by_default_and_an_error_when_strict', async () => {
  // Legacy behaviour of the Stickers tab is unchanged: an unreadable 200 body is an empty list.
  stubFetch(() => fakeResponse({ jsonThrows: true, etag: '"e"' }));
  const legacy = await fetchEvaluacionesOnce(token, 'stickersAtencionsismo');
  assert.deepStrictEqual(legacy.evaluaciones, []);
  // Seguimiento's revalidation must NOT take an unreadable body for "zero stickers".
  await assert.rejects(
    () => fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { depuracion: true, strictJson: true }),
    (err) => /inválida/i.test(err.message) && err.status === 200,
  );
  // A non-object JSON body (null, a string) is malformed too under strictJson.
  for (const body of [null, 'oops', 42]) {
    stubFetch(() => fakeResponse({ body }));
    await assert.rejects(() => fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { strictJson: true }), /inválida/i, `body ${JSON.stringify(body)}`);
  }
});

await named('test_bypass_cache_and_signal_are_forwarded_to_fetch', async () => {
  const controller = new AbortController();
  const calls = stubFetch(() => fakeResponse({ body: { evaluaciones: [] } }));
  await fetchEvaluacionesOnce(token, 'stickersAtencionsismo', { signal: controller.signal, bypassCache: true });
  assert.equal(calls[0].init.signal, controller.signal);
  assert.equal(calls[0].init.cache, 'no-store');
  await fetchEvaluacionesOnce(token, 'stickersAtencionsismo');
  assert.equal(calls[1].init.signal, undefined, 'default request carries neither signal nor cache override');
  assert.equal(calls[1].init.cache, undefined);
});

await named('test_stickers_tab_retries_once_on_a_transient_failure_and_keeps_its_shape', async () => {
  const calls = stubFetch((n) => {
    if (n === 1) throw new TypeError('Failed to fetch');
    return fakeResponse({ body: { evaluaciones: [{ id: 'r' }], degraded: true } });
  });
  const out = await fetchEvaluacionesForTab(token, { retryDelayMs: 0 });
  assert.equal(calls.length, 2);
  assert.deepStrictEqual(out.evaluaciones, [{ fuente: 'firestore', id: 'r' }]);
  assert.equal(out.degraded, true);
  // A second failure surfaces (the tab shows its inline error).
  stubFetch(() => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(() => fetchEvaluacionesForTab(token, { retryDelayMs: 0 }), TypeError);
});

if (failures.length) {
  throw new Error(`PR 10 part 2 named tests failed (${failures.length}): ${failures.join(', ')}`);
}

console.log('ok — fetchEvaluacionesOnce opt-in / conditional GET');
console.log('stickers.test.mjs: all assertions passed');
