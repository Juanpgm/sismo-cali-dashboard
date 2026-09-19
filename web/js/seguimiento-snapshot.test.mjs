// PR 10 part 2 (tasks 11.21-11.29, design D25/D28): Seguimiento's opt-in
// `depuracion=1` request, in-memory snapshot retention and skip-render, driven
// through the REAL initSeguimiento() + fetchEvaluacionesOnce() with a small
// fake DOM and a stubbed `fetch` (no network, no browser).
//
// What "render()" means here: every render() pass writes `#seg-kpis`'s
// innerHTML exactly once, so `kpis.htmlWrites` IS the render count (the initial
// synchronous paint counts as 1).
//
// Global invariants asserted after EVERY test by `named()`: nothing was written
// to (or even read from) localStorage / sessionStorage / IndexedDB, nothing was
// logged to the console and no promise rejection went unhandled.
// Run: node web/js/seguimiento-snapshot.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// ── fake DOM ────────────────────────────────────────────────────────────────

class FakeEl {
  constructor(id) {
    this.id = id;
    this.value = id === 'seg-estado' ? 'all' : '';
    this.hidden = false;
    this.disabled = false;
    this.title = '';
    this.textContent = '';
    this._html = '';
    this.htmlWrites = 0;
    this.listeners = {};
    this.attrs = {};
    this.classes = new Set();
    this.children = new Map();
    this.dataset = {};
    this.style = {};
    this.tabIndex = 0;
    const self = this;
    this.classList = {
      toggle(name, force) {
        const on = force === undefined ? !self.classes.has(name) : Boolean(force);
        if (on) self.classes.add(name); else self.classes.delete(name);
        return on;
      },
      add(name) { self.classes.add(name); },
      remove(name) { self.classes.delete(name); },
      contains(name) { return self.classes.has(name); },
    };
  }

  get innerHTML() { return this._html; }

  set innerHTML(value) { this._html = String(value); this.htmlWrites += 1; }

  insertAdjacentHTML(_where, html) { this._html += String(html); }

  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }

  dispatch(type, extra = {}) {
    for (const fn of this.listeners[type] || []) fn({ target: this, preventDefault() {}, ...extra });
  }

  setAttribute(name, value) { this.attrs[name] = String(value); }

  removeAttribute(name) { delete this.attrs[name]; }

  querySelector(selector) {
    if (!this.children.has(selector)) this.children.set(selector, new FakeEl(selector));
    return this.children.get(selector);
  }

  querySelectorAll() { return []; }

  closest() { return null; }

  focus() {}

  remove() {}

  appendChild() {}

  /** Every html string written under this element (itself + all descendants). */
  deepHtml() {
    return this._html + [...this.children.values()].map((child) => child.deepHtml()).join('');
  }

  /** How many event listeners are registered on this element and its descendants. */
  listenerCount() {
    return Object.values(this.listeners).reduce((n, list) => n + list.length, 0)
      + [...this.children.values()].reduce((n, child) => n + child.listenerCount(), 0);
  }
}

class FakeRoot {
  constructor() { this.hidden = false; this.els = new Map(); this._html = ''; }

  // main.js re-uses the same root: assigning innerHTML orphans every element.
  set innerHTML(value) { this._html = String(value); this.els = new Map(); }

  get innerHTML() { return this._html; }

  el(id) {
    if (!this.els.has(id)) this.els.set(id, new FakeEl(id));
    return this.els.get(id);
  }

  querySelector(selector) { return this.el(selector.startsWith('#') ? selector.slice(1) : selector); }

  /** Everything currently attached under the root: its own markup plus every
   *  element the code under test wrote into (table body, KPIs, selects...). The
   *  fake DOM's stand-in for "the container's whole innerHTML". */
  deepHtml() {
    return this._html + [...this.els.values()].map((el) => el.deepHtml()).join('');
  }

  listenerCount() { return [...this.els.values()].reduce((n, el) => n + el.listenerCount(), 0); }
}

// document-level listeners are never released: any registration after module
// load would accumulate across sign-in/out cycles (asserted by count).
const documentListenerRegistrations = [];
globalThis.document = {
  addEventListener(type) { documentListenerRegistrations.push(type); },
  getElementById: () => new FakeEl('canvas'),
  createElement: () => new FakeEl('created'),
  querySelector: () => new FakeEl('toast'),
};

// Storage spies: ANY access (read or write) is recorded.
const storageAccess = [];
function storageSpy(name) {
  return new Proxy({}, {
    get(_t, prop) { storageAccess.push(`${name}.${String(prop)}`); return () => null; },
    set(_t, prop) { storageAccess.push(`${name}.${String(prop)}=`); return true; },
  });
}
for (const name of ['localStorage', 'sessionStorage', 'indexedDB']) {
  Object.defineProperty(globalThis, name, { value: storageSpy(name), configurable: true, writable: true });
}

const unhandled = [];
process.on('unhandledRejection', (reason) => unhandled.push(reason));

const SEG = await import('./seguimiento.js');
const { announceRole, onRoleChange } = await import('./role-events.js');
const { wireSeguimientoSession } = await import('./seguimiento-session.js');

// The REAL session wiring main.js makes (its presence is pinned by
// role-events.test.mjs), with the view-state hooks replaced by a recording
// harness: `session.root` is #view-seguimiento, `session.active` says whether
// the Seguimiento tab is the current view, `reopen`/`leave` stand for
// main.js's switchView('seguimiento') / switchView('panel').
const session = {
  root: null, active: false, reopened: 0, left: 0, onReopen: null,
};
function resetSession() {
  Object.assign(session, {
    root: null, active: false, reopened: 0, left: 0, onReopen: null,
  });
}
wireSeguimientoSession({
  getRoot: () => session.root,
  isViewActive: () => session.active,
  reopen: () => { session.reopened += 1; if (session.onReopen) session.onReopen(); },
  leave: () => { session.left += 1; },
});
documentListenerRegistrations.length = 0; // module-load registrations don't count
// Node emits its "module type not specified" process warning on a later tick;
// let it print now so it is not mistaken for console output of the code under test.
await new Promise((resolve) => { setTimeout(resolve, 30); });

// ── harness ─────────────────────────────────────────────────────────────────

const failures = [];
async function named(name, fn) {
  resetSession();
  announceRole(null);
  SEG.clearSeguimientoSnapshot();
  documentListenerRegistrations.length = 0;
  storageAccess.length = 0;
  unhandled.length = 0;
  const consoleCalls = [];
  const saved = {};
  for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
    saved[level] = console[level];
    console[level] = (...args) => consoleCalls.push([level, ...args]);
  }
  const restore = () => { for (const level of Object.keys(saved)) console[level] = saved[level]; };
  try {
    await fn();
    await settle();
    assert.deepStrictEqual(storageAccess, [], 'the retained payload is MEMORY ONLY: no storage API was touched');
    assert.deepStrictEqual(consoleCalls.map((c) => c.map(String).join(' ').slice(0, 200)), [], 'nothing may be logged to the console');
    assert.deepStrictEqual(unhandled, [], 'no unhandled promise rejection');
    restore();
    console.log(`${name} OK`);
  } catch (err) {
    restore();
    failures.push(name);
    console.error(`${name} FAILED: ${err && err.message ? String(err.message).split('\n').slice(0, 5).join(' | ') : err}`);
  } finally {
    SEG.clearSeguimientoSnapshot();
  }
}

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
// setImmediate, not timers: Windows timers have ~15 ms granularity.
async function settle() { for (let i = 0; i < 12; i += 1) await new Promise((resolve) => { setImmediate(resolve); }); }

function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
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
const ok = (body, etag) => fakeResponse({ body, etag });
const notModified = (etag = null) => fakeResponse({ status: 304, etag });

function stubFetch(respond) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    // initSeguimiento also fetches reportes_agg.json (fail-soft, unrelated to
    // this change): answer 404 and keep it out of the sticker call log.
    if (!String(url).includes('/stickers-atencionsismo')) return fakeResponse({ status: 404, body: {} });
    calls.push({ url: String(url), init });
    return respond(calls.length, String(url), init);
  };
  return calls;
}
const headerOf = (call, name) => {
  const headers = call.init.headers || {};
  const key = Object.keys(headers).find((k) => k.toLowerCase() === name.toLowerCase());
  return key ? headers[key] : undefined;
};

const ESTADOS = ['activo', 'revisar', 'activo'];
const cedulaOf = (i) => String(1000000 + i);

function depInspector(i, name, extra = {}) {
  return {
    identidad_key: cedulaOf(i),
    identificacion: cedulaOf(i),
    nombre_completo: name,
    np: 'P2',
    np_fuente: 'vercel',
    fase: 'FASE_I',
    estado_sugerido: ESTADOS[i - 1] || 'revisar',
    codigo: '',
    entidad: '',
    cedulas_unificadas: [],
    ...extra,
  };
}

/** Three seeded people; the first two have one sticker each, the third none. */
function universe({ suffix = '', evil = false, block = {} } = {}) {
  const names = [1, 2, 3].map((i) => (evil ? `<img src=x onerror=alert(${i})>` : `Profesional ${i}${suffix}`));
  const inspectores = names.map((name, k) => depInspector(k + 1, name));
  const stickers = [0, 1].map((k) => ({
    inspector: { nombre_completo: names[k], identificacion: cedulaOf(k + 1) },
    inspector_fuente: 'evaluacion',
    fuente: 'atencionsismo',
    fase: 1,
    fecha: '2026-09-10T15:00:00+00:00',
  }));
  const depuracion = {
    activa: true,
    motivo: '',
    referencia_generada_en: '2026-09-19',
    inspectores,
    grupo_externos: null,
    alias_nombres: {},
    revision_manual: [],
    ...block,
  };
  return { names, stickers, depuracion };
}
const bodyOf = (u, { degraded = false, withBlock = true } = {}) => ({
  fuente: 'atencionsismo',
  evaluaciones: u.stickers,
  degraded,
  ...(withBlock ? { depuracion: u.depuracion } : {}),
});

function openTab({
  isAdmin = true, root = new FakeRoot(), getToken = async () => 'TOKEN-DO-NOT-LOG', retryMs = 0, records = [],
} = {}) {
  SEG.initSeguimiento(root, {
    getToken, records, isAdmin, snapshotRetryDelayMs: retryMs,
  });
  return {
    root,
    kpis: root.el('seg-kpis'),
    tbody: root.el('seg-table').querySelector('tbody'),
    status: root.el('seg-status'),
    badge: root.el('seg-depuracion-badge'),
    from: root.el('seg-from'),
    estado: root.el('seg-estado'),
    estadoField: root.el('seg-estado-field'),
    revision: root.el('seg-revision-manual'),
    chartSelect: root.el('seg-chart-professional'),
  };
}

const E1 = '"etag-1"';
const E2 = '"etag-2"';

// ── 11.21 / 11.22: the request ──────────────────────────────────────────────

await named('test_seguimiento_fetch_sends_depuracion_param', async () => {
  const u = universe();
  const calls = stubFetch(() => ok(bodyOf(u), E1));
  openTab();
  await settle();
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/stickers-atencionsismo\?depuracion=1$/);
  assert.equal((calls[0].url.match(/\?/g) || []).length, 1, 'exactly one "?"');
  assert.equal((calls[0].url.match(/depuracion=/g) || []).length, 1);
  assert.equal(headerOf(calls[0], 'Authorization'), 'Bearer TOKEN-DO-NOT-LOG');
  assert.equal(headerOf(calls[0], 'If-None-Match'), undefined, 'first open: nothing retained, no validator');
});

// ── 11.26: first open ───────────────────────────────────────────────────────

await named('test_first_open_full_fetch_retains_snapshot_in_memory', async () => {
  const u = universe();
  stubFetch(() => ok(bodyOf(u), E1));
  const tab = openTab();
  assert.equal(SEG.peekSeguimientoSnapshot(), null, 'nothing retained before the first response');
  assert.equal(tab.kpis.htmlWrites, 1, 'initial paint (loading state)');
  assert.match(tab.status.textContent, /Cargando/);
  await settle();
  assert.equal(tab.kpis.htmlWrites, 2, 'exactly one render for the response');
  assert.match(tab.tbody.innerHTML, /Profesional 1/);
  assert.equal(tab.status.hidden, true);
  const held = SEG.peekSeguimientoSnapshot();
  assert.equal(held.snapshotId, E1);
  assert.equal(held.stickers.length, 2);
  assert.equal(held.degraded, false);
  assert.deepStrictEqual(held.depuracion, u.depuracion);
});

// ── 11.24 / 11.25: re-open with an unchanged snapshot ───────────────────────

await named('test_reopen_with_same_snapshot_id_skips_render', async () => {
  const u = universe();
  const gate = deferred();
  const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(u), E1) : gate.promise));
  const root = new FakeRoot();
  openTab({ root });
  await settle();

  const tab2 = openTab({ root });
  // Rendered from the retained value IMMEDIATELY (before the network answers).
  assert.equal(tab2.kpis.htmlWrites, 1);
  assert.match(tab2.tbody.innerHTML, /Profesional 1/, 'the retained rows are on screen at once');
  assert.match(tab2.tbody.innerHTML, /Profesional 2/);
  await sleep(5); // let the (still unanswered) revalidation request go out
  assert.equal(calls.length, 2);
  assert.equal(headerOf(calls[1], 'If-None-Match'), E1, 'revalidation is a conditional GET with the stored ETag');
  assert.match(calls[1].url, /\?depuracion=1$/);
  assert.equal(tab2.kpis.htmlWrites, 1, 'still just the retained paint while the network is pending');

  gate.resolve(notModified(E1));
  await settle();
  assert.equal(tab2.kpis.htmlWrites, 1, '304 => ZERO additional render() calls');
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1);
  assert.equal(calls.length, 2, 'a 304 is not retried');
});

await named('test_reopen_equal_etag_with_a_200_body_skips_render_too', async () => {
  const u = universe();
  const calls = stubFetch(() => ok(bodyOf(u), E1)); // the server ignores the validator
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  const tab2 = openTab({ root });
  await settle();
  assert.equal(calls.length, 2);
  assert.equal(headerOf(calls[1], 'If-None-Match'), E1);
  assert.equal(tab2.kpis.htmlWrites, 1, 'an equal ETag is unchanged even when a full body came back');
});

await named('test_weak_etag_is_echoed_verbatim_and_compared_as_an_opaque_string', async () => {
  const u = universe();
  const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(u), 'W/"weak-1"') : notModified('W/"weak-1"')));
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, 'W/"weak-1"');
  const tab2 = openTab({ root });
  await settle();
  assert.equal(headerOf(calls[1], 'If-None-Match'), 'W/"weak-1"');
  assert.equal(tab2.kpis.htmlWrites, 1);
  // A strong tag with the same opaque text is a DIFFERENT string: conservative re-render.
  stubFetch(() => ok(bodyOf(u), '"weak-1"'));
  const tab3 = openTab({ root });
  await settle();
  assert.equal(tab3.kpis.htmlWrites, 2);
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, '"weak-1"');
});

await named('test_changed_etag_rerenders_exactly_once_and_replaces_the_retained_value', async () => {
  const before = universe();
  const after = universe({ suffix: ' v2' });
  const calls = stubFetch((n) => {
    if (n === 1) return ok(bodyOf(before), E1);
    if (n === 2) return ok(bodyOf(after), E2);
    return notModified(E2);
  });
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  const tab2 = openTab({ root });
  assert.match(tab2.tbody.innerHTML, /Profesional 1(?! v2)/, 'first paint is the OLD retained snapshot');
  await settle();
  assert.equal(tab2.kpis.htmlWrites, 2, 'initial + EXACTLY one re-render');
  assert.match(tab2.tbody.innerHTML, /Profesional 1 v2/, 'the new snapshot is what is on screen now');
  const held = SEG.peekSeguimientoSnapshot();
  assert.equal(held.snapshotId, E2);
  assert.equal(held.stickers[0].inspector.nombre_completo, 'Profesional 1 v2');
  // The replaced value is what the NEXT open validates against.
  const tab3 = openTab({ root });
  await settle();
  assert.equal(headerOf(calls[2], 'If-None-Match'), E2);
  assert.equal(tab3.kpis.htmlWrites, 1);
});

await named('test_304_with_nothing_retained_is_a_full_fetch_never_a_blank_table', async () => {
  const u = universe();
  const calls = stubFetch((n) => (n === 1 ? notModified(E1) : ok(bodyOf(u), E1)));
  const tab = openTab();
  await settle();
  assert.equal(calls.length, 2, 'the 304 was followed by a full fetch');
  assert.equal(headerOf(calls[1], 'If-None-Match'), undefined, 'the retry carries no validator');
  assert.equal(calls[1].init.cache, 'no-store', 'and bypasses any HTTP cache validator');
  assert.match(tab.tbody.innerHTML, /Profesional 1/, 'the table is populated, not blank');
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1);
  assert.equal(tab.status.hidden, true);
});

await named('test_304_twice_with_nothing_retained_is_an_error_state_not_a_crash', async () => {
  const calls = stubFetch(() => notModified(E1));
  const tab = openTab();
  await settle();
  assert.equal(calls.length, 2, 'one full-fetch retry, then give up (no loop)');
  assert.equal(SEG.peekSeguimientoSnapshot(), null);
  assert.match(tab.status.textContent, /Stickers no disponibles/);
  assert.equal(tab.kpis.htmlWrites, 2);
});

// ── 11.27: failures ─────────────────────────────────────────────────────────

const FAILURES = {
  network: () => { throw new TypeError('Failed to fetch'); },
  http500: () => fakeResponse({ status: 500, body: { detail: 'boom' } }),
  http503: () => fakeResponse({ status: 503, jsonThrows: true }),
  http502_html: () => fakeResponse({ status: 502, body: '<html>Bad gateway</html>' }),
  malformed_json: () => fakeResponse({ jsonThrows: true, etag: '"e-bad"' }),
  json_null: () => fakeResponse({ body: null, etag: '"e-bad"' }),
  json_array: () => fakeResponse({ body: [], etag: '"e-bad"' }),
};

for (const [kind, failure] of Object.entries(FAILURES)) {
  await named(`test_revalidation_failure_keeps_the_retained_render__${kind}`, async () => {
    const u = universe();
    const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(u), E1) : failure()));
    const root = new FakeRoot();
    openTab({ root });
    await settle();
    const tab2 = openTab({ root });
    await settle();
    assert.equal(calls.length, 3, 'first open + one revalidation + its single retry');
    assert.equal(tab2.kpis.htmlWrites, 1, 'no re-render, no blanking');
    assert.match(tab2.tbody.innerHTML, /Profesional 1/, 'the retained table stays on screen');
    assert.equal(tab2.status.hidden, true, 'no error banner over a table that is still valid');
    assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1, 'the retained value is untouched');
  });
}

await named('test_transient_failure_then_304_on_the_retry_is_unchanged', async () => {
  const u = universe();
  const calls = stubFetch((n) => {
    if (n === 1) return ok(bodyOf(u), E1);
    if (n === 2) return fakeResponse({ status: 500, body: {} });
    return notModified(E1);
  });
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  const tab2 = openTab({ root });
  await settle();
  assert.equal(calls.length, 3);
  assert.equal(headerOf(calls[2], 'If-None-Match'), E1, 'the retry keeps the validator');
  assert.equal(tab2.kpis.htmlWrites, 1);
});

await named('test_first_open_failure_shows_the_error_state_and_retains_nothing', async () => {
  const calls = stubFetch(() => fakeResponse({ status: 500, body: { detail: 'boom' } }));
  const tab = openTab();
  await settle();
  assert.equal(calls.length, 2);
  assert.match(tab.status.textContent, /Stickers no disponibles: boom/);
  assert.equal(SEG.peekSeguimientoSnapshot(), null);
  // A malformed 200 is a failure too, never "zero stickers".
  stubFetch(() => fakeResponse({ jsonThrows: true, etag: E1 }));
  const tab2 = openTab();
  await settle();
  assert.match(tab2.status.textContent, /Stickers no disponibles/);
  assert.equal(SEG.peekSeguimientoSnapshot(), null);
});

for (const status of [401, 403]) {
  await named(`test_${status}_clears_the_retention_and_the_pii_leaves_the_view`, async () => {
    const u = universe();
    const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(u), E1) : fakeResponse({ status, body: { detail: 'Sesión expirada' } })));
    const root = new FakeRoot();
    openTab({ root });
    await settle();
    const tab2 = openTab({ root });
    assert.match(tab2.tbody.innerHTML, /Profesional 1/, 'painted from the retained value first');
    await settle();
    assert.equal(calls.length, 2, `a ${status} is not retried`);
    assert.equal(SEG.peekSeguimientoSnapshot(), null, 'the retained admin PII is dropped');
    assert.doesNotMatch(tab2.tbody.innerHTML, /Profesional/, 'and it no longer sits in the table');
    assert.doesNotMatch(tab2.kpis.innerHTML, /Profesional/);
    assert.match(tab2.status.textContent, /Stickers no disponibles: Sesión expirada/);
    // The next open is a plain full fetch.
    const tab3 = openTab({ root });
    await settle();
    assert.equal(headerOf(calls[2], 'If-None-Match'), undefined);
    assert.ok(tab3.kpis.htmlWrites >= 1);
  });
}

// ── 11.28: sign-out / role change / memory only ─────────────────────────────

await named('test_sign_out_or_role_change_clears_the_retention', async () => {
  const u = universe();
  const calls = stubFetch(() => ok(bodyOf(u), E1));
  // Judgment-day C1: the memory variable is only half of it — the rendered
  // view (same container main.js reuses) must be emptied by every transition.
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin');
  openTab({ root });
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1);
  assert.match(root.deepHtml(), /Profesional 1/);

  announceRole('admin'); // same role (a token refresh): keeps it
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1);
  assert.match(root.deepHtml(), /Profesional 1/, 'a token refresh leaves the rendered view alone');

  announceRole('viewer'); // admin -> viewer: the PII must go
  assert.equal(SEG.peekSeguimientoSnapshot(), null);
  assert.equal(root.deepHtml(), '', 'and it leaves the DOM too');

  announceRole('admin');
  openTab({ root });
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1);
  assert.match(root.deepHtml(), /Profesional 1/);
  announceRole(null); // sign-out
  assert.equal(SEG.peekSeguimientoSnapshot(), null);
  assert.equal(root.deepHtml(), '');

  announceRole('admin');
  openTab({ root });
  await settle();
  assert.equal(headerOf(calls[calls.length - 1], 'If-None-Match'), undefined, 'after a clear the open is a full fetch');
});

await named('test_non_admin_open_never_retains_and_drops_any_retained_value', async () => {
  const u = universe();
  const calls = stubFetch(() => ok(bodyOf(u), E1));
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1);
  const viewer = openTab({ isAdmin: false, root });
  assert.equal(SEG.peekSeguimientoSnapshot(), null, 'fail closed: a non-admin open drops the retained admin data');
  assert.doesNotMatch(viewer.tbody.innerHTML, /Profesional 1/, 'and never paints it');
  await settle();
  assert.equal(headerOf(calls[1], 'If-None-Match'), undefined);
  assert.equal(SEG.peekSeguimientoSnapshot(), null, 'a non-admin result is never retained');
});

await named('test_retention_is_memory_only_never_touches_any_browser_storage', async () => {
  const before = universe();
  const after = universe({ suffix: ' v2' });
  stubFetch((n) => (n === 1 ? ok(bodyOf(before), E1) : n === 2 ? notModified(E1) : ok(bodyOf(after), E2)));
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  openTab({ root });
  await settle();
  openTab({ root });
  await settle();
  announceRole('admin');
  announceRole(null);
  assert.deepStrictEqual(storageAccess, [], 'not even a READ of localStorage/sessionStorage/IndexedDB');
  const source = readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  const stickers = readFileSync(new URL('./stickers.js', import.meta.url), 'utf8');
  const roleEvents = readFileSync(new URL('./role-events.js', import.meta.url), 'utf8');
  const sessionWiring = readFileSync(new URL('./seguimiento-session.js', import.meta.url), 'utf8');
  for (const file of [source, stickers, roleEvents, sessionWiring]) {
    assert.doesNotMatch(file, /localStorage|sessionStorage|indexedDB|caches\.open/, 'no persistence API is even referenced');
  }
});

// ── 11.29: unreadable ETag, rapid opens, filters ────────────────────────────

for (const [label, header] of [['missing', null], ['empty', ''], ['whitespace', '   '], ['huge', `"${'x'.repeat(9000)}"`], ['control_chars', 'a\r\nb']]) {
  await named(`test_unreadable_etag_${label}_gives_a_full_render_every_time_and_no_exception`, async () => {
    const u = universe();
    const calls = stubFetch(() => ok(bodyOf(u), header));
    const root = new FakeRoot();
    const tab1 = openTab({ root });
    await settle();
    assert.equal(tab1.kpis.htmlWrites, 2);
    assert.match(tab1.tbody.innerHTML, /Profesional 1/);
    assert.equal(SEG.peekSeguimientoSnapshot(), null, 'no validator, nothing worth retaining');
    const tab2 = openTab({ root });
    assert.equal(tab2.kpis.htmlWrites, 1);
    assert.match(tab2.status.textContent, /Cargando/, 'no retained render to paint from');
    await settle();
    assert.equal(headerOf(calls[1], 'If-None-Match'), undefined, 'never a bogus validator');
    assert.equal(tab2.kpis.htmlWrites, 2, 'a full render again, every time');
    assert.match(tab2.tbody.innerHTML, /Profesional 1/);
  });
}

await named('test_a_response_without_a_readable_etag_clears_the_older_retained_value', async () => {
  const before = universe();
  const after = universe({ suffix: ' v2' });
  stubFetch((n) => (n === 1 ? ok(bodyOf(before), E1) : ok(bodyOf(after), null)));
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  const tab2 = openTab({ root });
  await settle();
  assert.equal(tab2.kpis.htmlWrites, 2, 'cannot prove "unchanged": renders the fresh body');
  assert.match(tab2.tbody.innerHTML, /Profesional 1 v2/);
  assert.equal(SEG.peekSeguimientoSnapshot(), null, 'the stale validator must not survive');
});

await named('test_two_rapid_opens_produce_one_in_flight_revalidation', async () => {
  const u = universe();
  const gate = deferred();
  // Cold: two rapid opens share ONE full fetch.
  const cold = stubFetch(() => gate.promise);
  const root = new FakeRoot();
  const first = openTab({ root });
  const second = openTab({ root });
  await settle();
  assert.equal(cold.length, 1, 'a single request for two opens');
  gate.resolve(ok(bodyOf(u), E1));
  await settle();
  assert.match(second.tbody.innerHTML, /Profesional 1/, 'the latest open is the one that renders');
  assert.equal(second.kpis.htmlWrites, 2);
  assert.equal(first.kpis.htmlWrites, 1, 'the superseded open never re-renders');

  // Warm: two rapid re-opens share ONE conditional request.
  const gate2 = deferred();
  const warm = stubFetch(() => gate2.promise);
  const a = openTab({ root });
  const b = openTab({ root });
  const c = openTab({ root });
  await settle();
  assert.equal(warm.length, 1);
  assert.equal(headerOf(warm[0], 'If-None-Match'), E1);
  gate2.resolve(notModified(E1));
  await settle();
  assert.equal(a.kpis.htmlWrites + b.kpis.htmlWrites + c.kpis.htmlWrites, 3, 'each open painted once from the retained value, nothing more');
  // After settling, a fresh open starts a NEW flight (the slot was released).
  const later = stubFetch(() => notModified(E1));
  openTab({ root });
  await settle();
  assert.equal(later.length, 1);
});

await named('test_a_failed_flight_releases_the_slot_so_the_next_open_can_retry', async () => {
  const u = universe();
  let attempt = 0;
  const calls = stubFetch(() => {
    attempt += 1;
    if (attempt <= 2) throw new TypeError('Failed to fetch'); // first open: attempt + retry both fail
    return ok(bodyOf(u), E1);
  });
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  assert.equal(calls.length, 2);
  const tab2 = openTab({ root });
  await settle();
  assert.equal(calls.length, 3, 'the second open issued a fresh request');
  assert.match(tab2.tbody.innerHTML, /Profesional 1/);
});

await named('test_date_range_and_estado_filter_change_still_rerender_when_the_etag_is_unchanged', async () => {
  const u = universe();
  stubFetch((n) => (n === 1 ? ok(bodyOf(u), E1) : notModified(E1)));
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  const tab = openTab({ root });
  await settle();
  assert.equal(tab.kpis.htmlWrites, 1, 'the unchanged snapshot caused no render');

  // Date range: a full render() even though the snapshot did not change.
  const kpisBefore = tab.kpis.innerHTML;
  tab.from.value = '2026-09-11'; // after every sticker (2026-09-10)
  tab.from.dispatch('change');
  assert.equal(tab.kpis.htmlWrites, 2);
  assert.notEqual(tab.kpis.innerHTML, kpisBefore, 'the KPIs reflect the new range');

  // Estado filter: the table narrows to the matching rows.
  tab.from.value = '';
  tab.from.dispatch('change');
  assert.equal(tab.kpis.htmlWrites, 3);
  assert.match(tab.tbody.innerHTML, /Profesional 2/);
  const tbodyWrites = tab.tbody.htmlWrites;
  tab.estado.value = 'activo';
  tab.estado.dispatch('change');
  assert.equal(tab.tbody.htmlWrites, tbodyWrites + 1, 'the estado filter re-rendered the table');
  assert.match(tab.tbody.innerHTML, /Profesional 1/);
  assert.doesNotMatch(tab.tbody.innerHTML, /Profesional 2/, 'Profesional 2 is "revisar"');
  assert.equal(tab.kpis.htmlWrites, 3, 'estado only narrows the table: still no extra render() from the snapshot');
});

// ── races (controlled promises) ─────────────────────────────────────────────

await named('test_stale_response_after_a_newer_open_does_not_overwrite_the_retained_value', async () => {
  const older = universe({ suffix: ' old' });
  const newer = universe({ suffix: ' new' });
  const stale = universe({ suffix: ' STALE' });
  const gates = [null, deferred(), deferred(), deferred()];
  const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(older), E1) : gates[n - 1].promise));
  const root = new FakeRoot();
  announceRole('admin');
  openTab({ root });
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1);

  // Open A revalidates (call 2) ... and stays in flight.
  const tabA = openTab({ root });
  await settle();
  assert.equal(calls.length, 2);
  assert.equal(headerOf(calls[1], 'If-None-Match'), E1);

  // The session ends and a new one starts; open B fetches from scratch (call 3).
  announceRole(null);
  assert.equal(calls[1].init.signal.aborted, true, 'the in-flight request was aborted on clear');
  announceRole('admin');
  const tabB = openTab({ root });
  await settle();
  assert.equal(calls.length, 3);
  assert.equal(headerOf(calls[2], 'If-None-Match'), undefined);
  gates[2].resolve(ok(bodyOf(newer), E2));
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E2);
  assert.match(tabB.tbody.innerHTML, /Profesional 1 new/);

  // The stale response of open A finally arrives (as a full 200 with another ETag).
  gates[1].resolve(ok(bodyOf(stale), '"etag-stale"'));
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E2, 'the newer retained value survived');
  assert.equal(SEG.peekSeguimientoSnapshot().stickers[0].inspector.nombre_completo, 'Profesional 1 new');
  assert.equal(tabA.kpis.htmlWrites, 1, 'the orphaned open A did not render the stale answer');
  assert.doesNotMatch(tabB.tbody.innerHTML, /STALE/);
});

await named('test_sign_out_during_an_inflight_revalidation_drops_the_late_answer', async () => {
  const u = universe();
  const late = universe({ suffix: ' LATE' });
  const gate = deferred();
  const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(u), E1) : gate.promise));
  const root = new FakeRoot();
  announceRole('admin');
  openTab({ root });
  await settle();
  const tab = openTab({ root });
  await settle();
  assert.equal(calls.length, 2);

  announceRole(null); // sign-out while the revalidation is pending
  assert.equal(SEG.peekSeguimientoSnapshot(), null);
  assert.equal(calls[1].init.signal.aborted, true);

  gate.resolve(ok(bodyOf(late), '"etag-late"')); // the abort did not stop a misbehaving transport
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot(), null, 'a signed-out session must not be repopulated');
  assert.doesNotMatch(tab.tbody.innerHTML, /LATE/);
  assert.equal(tab.kpis.htmlWrites, 1);
  assert.equal(calls.length, 2, 'and nothing was re-requested');
});

await named('test_a_late_304_after_the_retention_was_cleared_is_discarded_not_refetched', async () => {
  const u = universe();
  const gate = deferred();
  const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(u), E1) : gate.promise));
  const root = new FakeRoot();
  announceRole('admin');
  openTab({ root });
  await settle();
  const tab = openTab({ root });
  announceRole('viewer'); // role change: admin PII dropped
  gate.resolve(notModified(E1));
  await settle();
  assert.equal(calls.length, 2, 'no full re-fetch on behalf of a cleared session');
  assert.equal(SEG.peekSeguimientoSnapshot(), null);
  assert.equal(tab.kpis.htmlWrites, 1);
});

await named('test_an_aborted_revalidation_is_not_reported_as_an_error_or_retried', async () => {
  const u = universe();
  const calls = stubFetch((n, _url, init) => {
    if (n === 1) return ok(bodyOf(u), E1);
    return new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    });
  });
  const root = new FakeRoot();
  announceRole('admin');
  openTab({ root });
  await settle();
  const tab = openTab({ root });
  await settle();
  announceRole(null);
  await settle();
  assert.equal(calls.length, 2, 'an abort is never retried');
  assert.equal(tab.status.hidden, true, 'and never surfaces an error banner');
});

// ── escaping after the retained re-render ───────────────────────────────────

await named('test_xss_looking_data_stays_escaped_after_the_retained_rerender', async () => {
  const evil = universe({ evil: true, block: { revision_manual: [{ motivo: '<script>x</script>', codigo: '"><svg onload=1>', nombre_completo: '<img src=x onerror=alert(9)>' }] } });
  const evil2 = universe({ evil: true });
  const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(evil), E1) : n === 2 ? notModified(E1) : ok(bodyOf(evil2), E2)));
  const root = new FakeRoot();
  const check = (tab, label) => {
    for (const [where, html] of [['tbody', tab.tbody.innerHTML], ['revision', tab.revision.innerHTML], ['select', tab.chartSelect.innerHTML]]) {
      assert.doesNotMatch(html, /<img|<script|<svg/, `${label}: raw markup in ${where}`);
    }
    assert.match(tab.tbody.innerHTML, /&lt;img/, `${label}: escaped, not dropped`);
  };
  const first = openTab({ root });
  await settle();
  check(first, 'first render');
  const retained = openTab({ root });
  check(retained, 'retained re-render (before the network answers)');
  await settle();
  check(retained, 'after a 304');
  const changed = openTab({ root });
  await settle();
  check(changed, 'after a changed snapshot');
  assert.equal(calls.length, 3, 'three opens, three requests');
});

// ── the depuracion banner through the real render (Task 1) ──────────────────

await named('test_absent_depuracion_block_renders_legacy_with_no_banner', async () => {
  const u = universe();
  for (const body of [
    { fuente: 'atencionsismo', evaluaciones: u.stickers, degraded: false }, // flag off / not opted in / viewer / old backend
    { fuente: 'atencionsismo', evaluaciones: u.stickers, degraded: false, depuracion: null },
  ]) {
    stubFetch(() => ok(body, E1));
    const tab = openTab();
    await settle();
    assert.equal(tab.badge.hidden, true, 'no banner for an absent block');
    assert.equal(tab.badge.textContent, '');
    assert.equal(tab.badge.attrs.role, undefined, 'no role="alert"');
    assert.equal(tab.badge.classes.has('seg-depuracion-banner'), false);
    assert.equal(tab.estadoField.hidden, true, 'legacy path: no estado filter');
    assert.doesNotMatch(tab.tbody.innerHTML, /Profesional 3/, 'legacy rows: only people with activity, nobody seeded');
    assert.match(tab.tbody.innerHTML, /Profesional 1/);
  }
  // Byte-identical rows/KPIs whether the key is missing or an explicit null.
  const render = async (body) => {
    stubFetch(() => ok(body, null));
    const tab = openTab();
    await settle();
    return [tab.kpis.innerHTML, tab.tbody.innerHTML];
  };
  assert.deepStrictEqual(
    await render({ evaluaciones: u.stickers }),
    await render({ evaluaciones: u.stickers, depuracion: null }),
  );
});

await named('test_degraded_block_renders_banner_with_each_motivo', async () => {
  const u = universe();
  for (const motivo of ['sin_blob', 'stickers_degradados', 'calculo_fallido', 'referencia_timeout', 'motivo_raro']) {
    SEG.clearSeguimientoSnapshot(); // each iteration is a first open (an equal ETag would rightly skip the render)
    stubFetch(() => ok({ ...bodyOf(u, { withBlock: false }), depuracion: { activa: false, motivo, inspectores: [] } }, E1));
    const tab = openTab();
    await settle();
    assert.equal(tab.badge.hidden, false, `banner shown for ${motivo}`);
    assert.ok(tab.badge.textContent.includes(motivo), `banner names ${motivo}`);
    assert.equal(tab.badge.attrs.role, 'alert');
    assert.equal(tab.badge.classes.has('seg-depuracion-banner'), true);
    assert.match(tab.tbody.innerHTML, /Profesional 1/, 'legacy rows still displayed');
    assert.doesNotMatch(tab.tbody.innerHTML, /Profesional 3/, 'and nobody is seeded');
  }
  // A degraded block with no motivo still announces itself.
  SEG.clearSeguimientoSnapshot();
  stubFetch(() => ok({ evaluaciones: u.stickers, depuracion: { activa: false } }, E1));
  const tab = openTab();
  await settle();
  assert.match(tab.badge.textContent, /sin_motivo/);
});

await named('test_active_block_shows_referencia_generada_en_and_no_banner', async () => {
  const u = universe();
  stubFetch(() => ok(bodyOf(u), E1));
  const tab = openTab();
  await settle();
  assert.equal(tab.badge.hidden, false);
  assert.match(tab.badge.textContent, /2026-09-19/);
  assert.equal(tab.badge.attrs.role, undefined, 'the freshness line is a note, not an alert');
  assert.equal(tab.badge.classes.has('seg-depuracion-banner'), false);
  assert.match(tab.tbody.innerHTML, /Profesional 3/, 'the seeded zero-activity person is listed');
});

await named('test_retained_degraded_flag_and_block_survive_the_reopen', async () => {
  const u = universe();
  stubFetch((n) => (n === 1
    ? ok({ evaluaciones: u.stickers, degraded: true, depuracion: { activa: false, motivo: 'stickers_degradados' } }, E1)
    : notModified(E1)));
  const root = new FakeRoot();
  openTab({ root });
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot().degraded, true);
  const tab2 = openTab({ root });
  assert.equal(tab2.status.hidden, false, 'the degraded note is painted from the retained value');
  assert.equal(tab2.status.textContent, SEG.degradedStickerNote(true, true));
  assert.match(tab2.badge.textContent, /stickers_degradados/);
  await settle();
  assert.equal(tab2.kpis.htmlWrites, 1);
});

// ══ Judgment-day C1/W2: the session end must take the PII OUT OF THE DOM ═════
//
// Clearing only the in-memory snapshot left ~373 names/cédulas rendered inside
// #view-seguimiento after sign-out (the panel is merely hidden, and auth.js
// boots the app once per tab). `teardownSeguimiento` + the session wiring
// (seguimiento-session.js, subscribed by main.js) empty the container, cancel
// everything in flight and re-initialize for the next admin.

/** A universe whose every identifying string carries `tag`, on its own cédula range. */
function piiUniverse(tag, base) {
  const ced = (i) => String(base + i);
  const names = [1, 2, 3].map((i) => `Profesional ${i} ${tag}`);
  const contact = (k) => ({
    tarjeta_profesional: `TP-${tag}-${k + 1}`,
    num_telefono: `310${base}${k}`,
    correo_contacto: `${tag.toLowerCase()}${k + 1}@example.org`,
    entidad: `Entidad ${tag}`,
    codigo: `COD-${tag}-${k + 1}`,
  });
  const inspectores = names.map((name, k) => depInspector(k + 1, name, {
    identidad_key: ced(k + 1), identificacion: ced(k + 1), ...contact(k),
  }));
  const stickers = [0, 1].map((k) => ({
    inspector: { nombre_completo: names[k], identificacion: ced(k + 1), ...contact(k) },
    inspector_fuente: 'evaluacion',
    fuente: 'atencionsismo',
    fase: 1,
    fecha: '2026-09-10T15:00:00+00:00',
  }));
  const depuracion = {
    activa: true,
    motivo: '',
    referencia_generada_en: '2026-09-19',
    inspectores,
    grupo_externos: null,
    alias_nombres: {},
    revision_manual: [{ motivo: 'nombre_duplicado', nombre_completo: `Duplicado ${tag}`, identidad_keys: [ced(1), ced(2)] }],
  };
  const secrets = [
    ...names, ced(1), ced(2), ced(3), `TP-${tag}-1`, `310${base}0`, `${tag.toLowerCase()}1@example.org`,
    `Entidad ${tag}`, `COD-${tag}-1`, `Duplicado ${tag}`,
  ];
  return {
    tag, names, ced, stickers, depuracion, secrets,
  };
}
const SEES_A = ['Profesional 1 ADMINA', '1500001', 'TP-ADMINA-1', 'Duplicado ADMINA'];
const leaksOf = (root, secrets) => secrets.filter((s) => root.deepHtml().includes(s));

/** Counts (and cancels on stop) every timer the code under test creates. */
function trackTimers() {
  const realSet = globalThis.setTimeout;
  const realClear = globalThis.clearTimeout;
  const realInterval = globalThis.setInterval;
  const live = new Set();
  const state = { live, intervals: 0 };
  globalThis.setTimeout = (fn, ms, ...args) => {
    const handle = realSet(() => { live.delete(handle); fn(...args); }, ms);
    live.add(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => { live.delete(handle); realClear(handle); };
  globalThis.setInterval = (...args) => { state.intervals += 1; return realInterval(...args); };
  state.stop = () => {
    globalThis.setTimeout = realSet;
    globalThis.clearTimeout = realClear;
    globalThis.setInterval = realInterval;
    for (const handle of live) realClear(handle);
  };
  return state;
}

await named('test_sign_out_leaves_no_trace_of_the_previous_admin_in_the_view', async () => {
  const a = piiUniverse('ADMINA', 1500000);
  stubFetch(() => ok(bodyOf(a), E1));
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin', 'uid-A');
  session.active = true; // the admin is looking at Seguimiento
  const tab = openTab({ root });
  await settle();
  // Sanity: the view really carried the admin's PII (the scan below is meaningful).
  for (const secret of SEES_A) assert.ok(root.deepHtml().includes(secret), `before sign-out the view shows ${secret}`);
  assert.ok(SEG.peekSeguimientoSnapshot());

  announceRole(null);
  assert.equal(root.innerHTML, '', '#view-seguimiento is empty');
  assert.equal(root.deepHtml(), '', 'and nothing is attached under it any more');
  assert.deepStrictEqual(leaksOf(root, a.secrets), [], 'no name/cédula/tarjeta/entidad/código/revisión string survives');
  assert.equal(root.els.size, 0, 'no element (and so no listener) is reachable from the container');
  assert.equal(SEG.peekSeguimientoSnapshot(), null);
  assert.equal(session.reopened, 0, 'sign-out never re-opens anything');
  assert.equal(session.left, 0, 'the view is only left when the NEXT user is not an admin');
  // Whoever sits behind the old handles sees stale markup only, never a live render.
  assert.equal(tab.kpis.htmlWrites, 2);
});

await named('test_admin_b_signing_in_reinitializes_the_view_and_never_paints_admin_a_data', async () => {
  const a = piiUniverse('ADMINA', 1500000);
  const b = piiUniverse('ADMINB', 1600000);
  const gateB = deferred();
  const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(a), E1) : gateB.promise));
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin', 'uid-A');
  session.active = true;
  openTab({ root });
  await settle();
  assert.ok(leaksOf(root, a.secrets).length > 0);

  announceRole(null); // sign-out
  let tabB = null;
  session.onReopen = () => { tabB = openTab({ root }); }; // main.js: switchView('seguimiento')
  announceRole('admin', 'uid-B'); // admin B signs in on the same tab
  assert.equal(session.reopened, 1, 'the view is re-initialized once');
  assert.ok(tabB, 'initSeguimiento ran again for the panel');
  assert.deepStrictEqual(leaksOf(root, a.secrets), [], "A's data is not painted, not even before B's revalidation answers");
  assert.match(tabB.status.textContent, /Cargando/, 'B starts from the loading state, not from a retained render');
  await sleep(5);
  assert.equal(calls.length, 2);
  assert.equal(headerOf(calls[1], 'If-None-Match'), undefined, "no validator of A's snapshot is ever sent for B");

  gateB.resolve(ok(bodyOf(b), '"etag-b"'));
  await settle();
  for (const secret of ['Profesional 1 ADMINB', '1600001', 'TP-ADMINB-1', 'Duplicado ADMINB']) {
    assert.ok(root.deepHtml().includes(secret), `B sees ${secret}`);
  }
  assert.deepStrictEqual(leaksOf(root, a.secrets), [], 'only B\'s fetch results are on screen');
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, '"etag-b"');
});

await named('test_admin_to_admin_uid_change_is_a_session_change_and_never_paints_the_first_admins_data', async () => {
  const a = piiUniverse('ADMINA', 1500000);
  const b = piiUniverse('ADMINB', 1600000);
  const gateB = deferred();
  const calls = stubFetch((n) => (n === 1 ? ok(bodyOf(a), E1) : gateB.promise));
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin', 'uid-A');
  session.active = true;
  openTab({ root });
  await settle();
  session.onReopen = () => { openTab({ root }); };
  // No sign-out in between: same role, different uid.
  assert.equal(announceRole('admin', 'uid-B'), true);
  assert.equal(session.reopened, 1);
  assert.deepStrictEqual(leaksOf(root, a.secrets), [], "A's snapshot is not painted to B");
  await sleep(5);
  assert.equal(headerOf(calls[1], 'If-None-Match'), undefined);
  gateB.resolve(ok(bodyOf(b), '"etag-b"'));
  await settle();
  assert.deepStrictEqual(leaksOf(root, a.secrets), []);
  assert.ok(root.deepHtml().includes('Profesional 1 ADMINB'));
});

await named('test_same_user_token_refresh_does_not_tear_down_or_refetch', async () => {
  const a = piiUniverse('ADMINA', 1500000);
  const calls = stubFetch(() => ok(bodyOf(a), E1));
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin', 'uid-A');
  session.active = true;
  const tab = openTab({ root });
  await settle();
  const before = root.deepHtml();
  for (let i = 0; i < 3; i += 1) assert.equal(announceRole('admin', 'uid-A'), false, 'a token refresh re-announces the same session');
  assert.equal(root.deepHtml(), before, 'the rendered table is untouched');
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1, 'the retained snapshot is kept');
  assert.equal(session.reopened + session.left, 0);
  assert.equal(calls.length, 1, 'no needless refetch');
  assert.equal(tab.kpis.htmlWrites, 2);
});

await named('test_viewer_signing_in_never_renders_the_panel_content', async () => {
  const a = piiUniverse('ADMINA', 1500000);
  const calls = stubFetch(() => ok(bodyOf(a), E1));
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin', 'uid-A');
  session.active = true; // the previous admin left the tab on Seguimiento
  openTab({ root });
  await settle();
  const callsBefore = calls.length;

  announceRole(null);
  announceRole('viewer', 'uid-V'); // a viewer signs in on the same tab
  assert.equal(session.reopened, 0, 'no init for a non-admin');
  assert.equal(session.left, 1, 'the view moves away from the admin-only tab');
  assert.equal(root.deepHtml(), '', 'the panel stays blank');
  await settle();
  assert.equal(calls.length, callsBefore, 'and no sticker request is made on the viewer\'s behalf');
  assert.equal(SEG.peekSeguimientoSnapshot(), null);

  // A downgrade admin -> viewer with the SAME uid (claims changed) is handled alike.
  session.active = false;
  announceRole(null);
  announceRole('admin', 'uid-A');
  openTab({ root });
  await settle();
  assert.ok(root.deepHtml().length > 0);
  session.active = true;
  announceRole('viewer', 'uid-A');
  assert.equal(root.deepHtml(), '');
  assert.equal(session.left, 2);
  assert.equal(session.reopened, 0);

  // Not on the Seguimiento tab: nothing to open or to leave.
  session.active = false;
  announceRole(null);
  announceRole('viewer', 'uid-V2');
  assert.equal(session.reopened, 0);
  assert.equal(session.left, 2);
});

await named('test_sign_out_during_an_inflight_first_load_drops_the_late_answer_and_the_view_stays_empty', async () => {
  const a = piiUniverse('ADMINA', 1500000);
  const late = piiUniverse('LATE', 1700000);
  const gate = deferred();
  const calls = stubFetch(() => gate.promise);
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin', 'uid-A');
  session.active = true;
  const tab = openTab({ root });
  await sleep(5);
  assert.equal(calls.length, 1);

  announceRole(null); // sign-out while the very first request is pending
  assert.equal(calls[0].init.signal.aborted, true);
  gate.resolve(ok(bodyOf(late), '"etag-late"')); // a transport that ignores the abort
  await settle();
  assert.equal(root.deepHtml(), '', 'the late answer is never painted');
  assert.deepStrictEqual(leaksOf(root, late.secrets.concat(a.secrets)), []);
  assert.equal(SEG.peekSeguimientoSnapshot(), null, 'and never retained');
  assert.equal(tab.kpis.htmlWrites, 1, 'the orphaned open did not render the late answer');
  assert.equal(calls.length, 1, 'and nothing was re-requested');
});

await named('test_repeated_sign_in_out_cycles_do_not_accumulate_listeners_or_timers', async () => {
  const timers = trackTimers();
  try {
    let n = 0;
    stubFetch(() => { n += 1; return ok(bodyOf(piiUniverse(`C${n}`, 1800000 + n * 100)), `"etag-${n}"`); });
    const root = new FakeRoot();
    session.root = root;
    let live = null;
    const perOpenListeners = [];
    announceRole('admin', 'uid-0');
    session.active = true;
    session.onReopen = () => { live = openTab({ root, retryMs: 50 }); };
    live = openTab({ root, retryMs: 50 });
    await settle();
    perOpenListeners.push(root.listenerCount());
    for (let cycle = 1; cycle <= 6; cycle += 1) {
      root.el('seg-search').dispatch('input'); // leaves a pending 250 ms debounce timer behind
      assert.equal(timers.live.size, 1, `cycle ${cycle}: the debounce timer is pending before the sign-out`);
      announceRole(null);
      assert.equal(root.listenerCount(), 0, `cycle ${cycle}: nothing is reachable from the container after sign-out`);
      assert.equal(timers.live.size, 0, `cycle ${cycle}: no timer survives the sign-out`);
      announceRole('admin', `uid-${cycle}`);
      await settle();
      perOpenListeners.push(root.listenerCount());
      assert.equal(timers.live.size, 0, `cycle ${cycle}: no timer is left running after the re-open`);
    }
    assert.equal(session.reopened, 6, 'exactly one re-open per sign-in: the session wiring is never doubled');
    assert.ok(perOpenListeners.every((count) => count === perOpenListeners[0] && count > 0), `per-open listener count is constant: ${perOpenListeners}`);
    assert.deepStrictEqual(documentListenerRegistrations, [], 'no document-level listener is added by an open or a teardown');
    assert.equal(timers.intervals, 0, 'no interval is ever created');
    assert.ok(live.kpis.htmlWrites >= 1);
  } finally {
    timers.stop();
  }
});

await named('test_teardown_cancels_the_pending_retry_timer_and_the_request', async () => {
  const timers = trackTimers();
  try {
    const calls = stubFetch(() => { throw new TypeError('Failed to fetch'); });
    const root = new FakeRoot();
    session.root = root;
    announceRole('admin', 'uid-A');
    const tab = openTab({ root, retryMs: 60000 }); // the first attempt fails; the retry waits a minute
    await settle();
    assert.equal(calls.length, 1);
    assert.equal(timers.live.size, 1, 'the retry back-off timer is pending');
    announceRole(null);
    assert.equal(timers.live.size, 0, 'teardown cleared it');
    await settle();
    assert.equal(calls.length, 1, 'the retry never fires after the session ended');
    assert.equal(tab.status.textContent.includes('no disponibles'), false, 'and no error is painted for a cancelled flight');
    assert.equal(root.deepHtml(), '');
  } finally {
    timers.stop();
  }
});

await named('test_a_pending_search_and_a_late_store_update_never_render_after_teardown', async () => {
  const a = piiUniverse('ADMINA', 1500000);
  stubFetch(() => ok(bodyOf(a), E1));
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin', 'uid-A');
  const tab = openTab({ root });
  await settle();
  const search = root.el('seg-search');
  search.value = 'profesional';
  search.dispatch('input'); // starts the 250 ms debounce
  const writes = { tbody: tab.tbody.htmlWrites, kpis: tab.kpis.htmlWrites };
  announceRole(null);
  await sleep(320);
  assert.equal(tab.tbody.htmlWrites, writes.tbody, 'the pending debounced search was cancelled');
  // main.js's onStoreChange keeps calling this after sign-out (the store refresh timer lives on).
  SEG.updateSeguimientoRecords([{ nombre_evaluador: 'FILTRADO Apellido', fecha_inspeccion: '2026-09-10' }]);
  assert.equal(tab.kpis.htmlWrites, writes.kpis, 'no render into the torn-down view');
  assert.equal(root.deepHtml(), '');
  assert.equal(root.el('seg-kpis').htmlWrites, 0, 'and nothing was written into the container\'s new elements');
});

await named('test_teardown_when_never_initialized_is_a_noop_and_is_idempotent', async () => {
  assert.doesNotThrow(() => SEG.teardownSeguimiento());
  assert.doesNotThrow(() => SEG.teardownSeguimiento(null));
  assert.doesNotThrow(() => SEG.teardownSeguimiento({}));
  const stray = new FakeRoot();
  stray.innerHTML = '<p>leftover 1500001</p>';
  SEG.teardownSeguimiento(stray); // an explicit container is emptied even if this module never rendered into it
  assert.equal(stray.innerHTML, '');
  // Wiring a sign-out with nothing ever opened, and repeating it, is harmless.
  session.root = null;
  assert.doesNotThrow(() => { announceRole('admin', 'uid-A'); announceRole(null); announceRole(null); });
  assert.equal(SEG.peekSeguimientoSnapshot(), null);
  // The module can still initialize afterwards.
  stubFetch(() => ok(bodyOf(piiUniverse('ADMINA', 1500000)), E1));
  const tab = openTab();
  await settle();
  assert.match(tab.tbody.innerHTML, /Profesional 1 ADMINA/);
});

await named('test_teardown_leaves_the_module_able_to_init_and_retain_again', async () => {
  const a = piiUniverse('ADMINA', 1500000);
  const calls = stubFetch(() => ok(bodyOf(a), E1));
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin', 'uid-A');
  openTab({ root });
  await settle();
  announceRole(null);
  announceRole('admin', 'uid-A');
  openTab({ root });
  await settle();
  assert.equal(SEG.peekSeguimientoSnapshot().snapshotId, E1);
  const reopened = openTab({ root });
  await settle();
  assert.equal(headerOf(calls[calls.length - 1], 'If-None-Match'), E1, 'retention works again after a teardown');
  assert.equal(reopened.kpis.htmlWrites, 1);
});

await named('test_a_throwing_teardown_never_breaks_the_auth_flow', async () => {
  const errors = [];
  const seen = [];
  const off = onRoleChange((role) => seen.push(role));
  // A second wiring whose container lookup itself throws: the view gating must still run.
  const gating = { reopened: 0, left: 0 };
  const offBad = wireSeguimientoSession({
    getRoot: () => { throw new Error('no container'); },
    isViewActive: () => true,
    reopen: () => { gating.reopened += 1; },
    leave: () => { gating.left += 1; },
  });
  try {
    // The primary wiring's container throws on every write.
    session.root = { set innerHTML(_v) { throw new Error('boom'); }, get innerHTML() { return 'x'; } };
    stubFetch(() => ok(bodyOf(piiUniverse('ADMINA', 1500000)), E1));
    announceRole('admin', 'uid-A');
    openTab();
    await settle();
    assert.ok(SEG.peekSeguimientoSnapshot());
    let returned;
    try { returned = announceRole(null); } catch (err) { errors.push(err); }
    assert.deepStrictEqual(errors, [], 'announceRole (the auth flow) never throws');
    assert.equal(returned, true);
    assert.equal(SEG.peekSeguimientoSnapshot(), null, 'the memory clear happened even though the DOM step threw');
    assert.doesNotThrow(() => announceRole('viewer', 'uid-B'));
    assert.deepStrictEqual(seen, ['admin', null, 'viewer'], 'listeners registered after the wirings still run');
    assert.deepStrictEqual(gating, { reopened: 1, left: 1 }, 'the view gating ran even though teardown and the container lookup threw');
  } finally {
    off();
    offBad();
    session.root = null;
  }
});

await named('test_xss_looking_strings_stay_escaped_after_a_teardown_and_reinit', async () => {
  const evil = universe({ evil: true, block: { revision_manual: [{ motivo: '<script>x</script>', codigo: '"><svg onload=1>', nombre_completo: '<img src=x onerror=alert(9)>' }] } });
  stubFetch(() => ok(bodyOf(evil), E1));
  const root = new FakeRoot();
  session.root = root;
  announceRole('admin', 'uid-A');
  session.active = true;
  let tab = openTab({ root });
  await settle();
  announceRole(null);
  session.onReopen = () => { tab = openTab({ root }); };
  announceRole('admin', 'uid-B');
  await settle();
  for (const [where, html] of [['tbody', tab.tbody.innerHTML], ['revision', tab.revision.innerHTML], ['select', tab.chartSelect.innerHTML]]) {
    assert.doesNotMatch(html, /<img|<script|<svg/, `raw markup in ${where} after the re-init`);
  }
  assert.match(tab.tbody.innerHTML, /&lt;img/);
});

await named('test_wiring_can_be_detached_and_never_leaks_a_listener_per_call', async () => {
  const a = piiUniverse('ADMINA', 1500000);
  stubFetch(() => ok(bodyOf(a), E1));
  const extra = { left: 0, root: new FakeRoot() };
  const off = wireSeguimientoSession({
    getRoot: () => extra.root, isViewActive: () => true, reopen: () => {}, leave: () => { extra.left += 1; },
  });
  announceRole('viewer', 'uid-V');
  assert.equal(extra.left, 1);
  off();
  announceRole(null);
  announceRole('viewer', 'uid-V');
  assert.equal(extra.left, 1, 'an unsubscribed wiring is never called again');
});

if (failures.length) {
  throw new Error(`Seguimiento snapshot named tests failed (${failures.length}): ${failures.join(', ')}`);
}
console.log('seguimiento-snapshot.test.mjs: all assertions passed');
