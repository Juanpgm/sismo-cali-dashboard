// Self-check for the pure logic behind the "Puntos Solicitados" tab: estado
// classification, counts, filters and sort. Run: node web/js/puntos_solicitados.test.mjs
import assert from 'node:assert';
import {
  ESTADOS, estadoDe, contarPorEstado, applyFilters, sortPuntos, removeFotoAt, nombreInspectorPorUid,
  prefillStepsFromResultado, prefillStepsFromQuery, apiBuscar, runGuardedBuscar,
  contarCargaPorInspector, inspectorLabelConCarga,
} from './puntos_solicitados.js';

// The derived lifecycle states (ADR-4 map's output values), in the order they
// progress through the assignment machinery.
assert.deepStrictEqual(ESTADOS.map((e) => e.key), ['pendiente', 'asignado', 'en_proceso', 'visitado', 'excluido']);

// estadoDe: exact stored values (what the router's GET already derives server-side).
assert.strictEqual(estadoDe({ estado_seguimiento: 'pendiente' }).key, 'pendiente');
assert.strictEqual(estadoDe({ estado_seguimiento: 'asignado' }).key, 'asignado');
assert.strictEqual(estadoDe({ estado_seguimiento: 'en_proceso' }).key, 'en_proceso');
assert.strictEqual(estadoDe({ estado_seguimiento: 'visitado' }).key, 'visitado');
assert.strictEqual(estadoDe({ estado_seguimiento: 'excluido' }).key, 'excluido');

// Case drift and blank/unknown values fall back to 'pendiente' — never crash,
// never silently invent a 6th state.
assert.strictEqual(estadoDe({ estado_seguimiento: ' ASIGNADO ' }).key, 'asignado');
assert.strictEqual(estadoDe({ estado_seguimiento: '' }).key, 'pendiente');
assert.strictEqual(estadoDe({}).key, 'pendiente');
assert.strictEqual(estadoDe({ estado_seguimiento: 'otra_cosa' }).key, 'pendiente');

// contarPorEstado: counts every state, including zero-count ones — real
// production code walking a mixed, non-trivial dataset.
const counts = contarPorEstado([
  { estado_seguimiento: 'pendiente' },
  { estado_seguimiento: 'pendiente' },
  { estado_seguimiento: 'asignado' },
  { estado_seguimiento: 'visitado' },
]);
assert.deepStrictEqual(counts, {
  pendiente: 2, asignado: 1, en_proceso: 0, visitado: 1, excluido: 0,
});
assert.deepStrictEqual(contarPorEstado([]), {
  pendiente: 0, asignado: 0, en_proceso: 0, visitado: 0, excluido: 0,
});

// applyFilters — a realistic mixed dataset, filtered by each dimension in turn.
const dataset = [
  {
    id: '1', nombre: 'Casa esquinera', direccion: 'Calle 5 # 10-20', nombre_solicitante: 'María Pérez',
    comuna_corregimiento: 'Comuna 3', barrio_vereda: 'San Antonio', clave_integracion: 'PLN-AAA-11111111',
    estado_seguimiento: 'pendiente',
  },
  {
    id: '2', nombre: 'Edificio Torre Azul', direccion: 'Carrera 8 # 4-30', nombre_solicitante: 'Jorge Ruiz',
    comuna_corregimiento: 'Comuna 3', barrio_vereda: 'El Peñón', clave_integracion: 'PLN-BBB-22222222',
    estado_seguimiento: 'asignado',
  },
  {
    id: '3', nombre: 'Local comercial', direccion: 'Avenida 6N', nombre_solicitante: 'Ana Gómez',
    comuna_corregimiento: 'Comuna 19', barrio_vereda: 'Tequendama', clave_integracion: 'PLN-CCC-33333333',
    estado_seguimiento: 'visitado',
  },
];

assert.strictEqual(applyFilters(dataset, {}).length, 3);
assert.deepStrictEqual(applyFilters(dataset, { estado: 'asignado' }).map((p) => p.id), ['2']);
assert.deepStrictEqual(applyFilters(dataset, { comuna: 'Comuna 3' }).map((p) => p.id), ['1', '2']);
assert.deepStrictEqual(applyFilters(dataset, { comuna: 'Comuna 3', barrio: 'El Peñón' }).map((p) => p.id), ['2']);
// Search is case/accent-insensitive across nombre/direccion/solicitante.
assert.deepStrictEqual(applyFilters(dataset, { search: 'peñon' }).map((p) => p.id), ['2']);
assert.deepStrictEqual(applyFilters(dataset, { search: 'MARIA' }).map((p) => p.id), ['1']);
assert.deepStrictEqual(applyFilters(dataset, { search: 'no-existe-nada' }), []);

// sortPuntos — newest creado_en first.
const unsorted = [
  { id: 'old', creado_en: '2026-08-01T10:00:00Z' },
  { id: 'newest', creado_en: '2026-08-27T09:00:00Z' },
  { id: 'mid', creado_en: '2026-08-15T12:00:00Z' },
];
assert.deepStrictEqual(sortPuntos(unsorted).map((p) => p.id), ['newest', 'mid', 'old']);
// Missing creado_en sorts last, never crashes.
assert.deepStrictEqual(
  sortPuntos([{ id: 'sin-fecha' }, { id: 'con-fecha', creado_en: '2026-08-01T00:00:00Z' }]).map((p) => p.id),
  ['con-fecha', 'sin-fecha'],
);

// removeFotoAt — pure removal backing the create modal's "quitar" chip
// button: order-preserving, and never mutates the array it's given.
const fotos = [{ name: 'a.jpg' }, { name: 'b.jpg' }, { name: 'c.jpg' }];
assert.deepStrictEqual(removeFotoAt(fotos, 1).map((f) => f.name), ['a.jpg', 'c.jpg']); // middle
assert.deepStrictEqual(removeFotoAt(fotos, 2).map((f) => f.name), ['a.jpg', 'b.jpg']); // last
assert.deepStrictEqual(removeFotoAt(fotos, 0).map((f) => f.name), ['b.jpg', 'c.jpg']); // first
assert.strictEqual(fotos.length, 3); // input untouched — no in-place mutation
assert.deepStrictEqual(removeFotoAt([{ name: 'only.jpg' }], 0), []); // down to empty

// nombreInspectorPorUid — resolves an inspector_uid against the Stickers
// roster; null/unmatched uid and missing/empty roster all fall back to null
// ("Sin asignar" at render time), never throw.
const roster = [
  { uid: 'uid-1', nombre_completo: 'Ana Torres', codigo: 'INS-01' },
  { uid: 'uid-2', codigo: 'INS-02' }, // no nombre_completo — falls back to codigo
];
assert.strictEqual(nombreInspectorPorUid('uid-1', roster), 'Ana Torres');
assert.strictEqual(nombreInspectorPorUid('uid-2', roster), 'INS-02');
assert.strictEqual(nombreInspectorPorUid('uid-desconocido', roster), null);
assert.strictEqual(nombreInspectorPorUid(null, roster), null);
assert.strictEqual(nombreInspectorPorUid('uid-1', []), null);
assert.strictEqual(nombreInspectorPorUid('uid-1', undefined), null);

// prefillStepsFromResultado — GET /buscar result → ordered [field, value]
// steps for #ps-crear-modal (design.md "Prefill field mapping"). Comuna MUST
// be applied before barrio: the barrio combobox stays disabled until a
// comuna is set, so applying out of order would silently drop the value.
const resultado = {
  registro_id: 'r1', direccion: 'Calle 5 # 10-20', barrio: 'San Antonio', comuna: 'Comuna 3',
  lat: 3.45, lng: -76.53, nombre_solicitante: 'María Pérez', telefono_solicitante: '3001234567',
};
const steps = prefillStepsFromResultado(resultado);
assert.deepStrictEqual(Object.fromEntries(steps), {
  direccion: 'Calle 5 # 10-20',
  nombre: 'Calle 5 # 10-20',
  comuna: 'Comuna 3',
  barrio: 'San Antonio',
  lat: 3.45,
  lng: -76.53,
  nombre_solicitante: 'María Pérez',
  telefono_solicitante: '3001234567',
});
const stepFields = steps.map(([field]) => field);
assert.ok(stepFields.indexOf('comuna') < stepFields.indexOf('barrio'), 'comuna must be applied before barrio');

// Missing optional fields (no puntos_contacto match, no coords) degrade to
// blank/null, never throw.
assert.deepStrictEqual(Object.fromEntries(prefillStepsFromResultado({ direccion: 'Avenida 6N' })), {
  direccion: 'Avenida 6N', nombre: 'Avenida 6N', comuna: '', barrio: '',
  lat: null, lng: null, nombre_solicitante: '', telefono_solicitante: '',
});
assert.deepStrictEqual(Object.fromEntries(prefillStepsFromResultado(undefined)), {
  direccion: '', nombre: '', comuna: '', barrio: '',
  lat: null, lng: null, nombre_solicitante: '', telefono_solicitante: '',
});

// prefillStepsFromQuery — "Crear punto nuevo" fallback: ONLY direccion+nombre
// from the typed search text, everything else stays blank/absent.
assert.deepStrictEqual(Object.fromEntries(prefillStepsFromQuery('  Calle 5  ')), {
  direccion: 'Calle 5', nombre: 'Calle 5',
});
assert.deepStrictEqual(Object.fromEntries(prefillStepsFromQuery('')), { direccion: '', nombre: '' });
assert.deepStrictEqual(Object.fromEntries(prefillStepsFromQuery(undefined)), { direccion: '', nombre: '' });

// apiBuscar — error path (403/401/502 → thrown Error carrying the backend's
// `detail`, same contract as this file's other fetchJson-backed api* calls).
// Mocks global.fetch so no real network call happens.
{
  const originalFetch = global.fetch;
  const getToken = async () => 'fake-token';
  for (const [status, detail] of [[403, 'Forbidden'], [401, 'Unauthorized'], [502, 'Bad Gateway']]) {
    global.fetch = async () => ({ ok: false, status, json: async () => ({ detail }) });
    // eslint-disable-next-line no-await-in-loop -- sequential status cases, deterministic
    await assert.rejects(() => apiBuscar(getToken, 'calle 5'), { message: detail });
  }
  global.fetch = originalFetch;
}

// runGuardedBuscar — regression test for the stale/out-of-order race
// (debounce() only coalesces calls fired within its window, it does NOT
// cancel in-flight fetches). Two concurrent searches, id 1 ("stale") and id 2
// ("newest"); id 2's promise resolves FIRST and id 1's resolves AFTER — the
// exact out-of-order scenario the CRITICAL fix guards against. Deterministic:
// no real timers/network, `search` is a mock returning caller-controlled
// promises.
{
  const state = { current: 0 };
  const rendered = [];
  const errors = [];
  let resolveStale;
  let resolveNewest;
  const pendingStale = new Promise((res) => { resolveStale = res; });
  const pendingNewest = new Promise((res) => { resolveNewest = res; });
  const search = (q) => (q === 'query-stale' ? pendingStale : pendingNewest);
  const callbacks = { search, onResult: (list) => rendered.push(list), onError: (err) => errors.push(err) };

  state.current = 1;
  const callStale = runGuardedBuscar('query-stale', 1, state, callbacks);
  state.current = 2; // admin typed again before the stale request resolved
  const callNewest = runGuardedBuscar('query-newest', 2, state, callbacks);

  resolveNewest(['resultado-newest']);
  await callNewest;
  resolveStale(['resultado-stale']); // arrives LAST, after the newer request already rendered
  await callStale;

  assert.deepStrictEqual(rendered, [['resultado-newest']], 'only the newest request may ever render — the stale response is silently discarded');
  assert.deepStrictEqual(errors, []);
}

// runGuardedBuscar — a rejected search() routes into onError, never onResult
// (this is what drives the real code's sticker-error render branch).
{
  const state = { current: 1 };
  const rendered = [];
  const errors = [];
  await runGuardedBuscar('calle 5', 1, state, {
    search: async () => { throw new Error('Error 502'); },
    onResult: (list) => rendered.push(list),
    onError: (err) => errors.push(err.message),
  });
  assert.deepStrictEqual(rendered, []);
  assert.deepStrictEqual(errors, ['Error 502']);
}

// runGuardedBuscar — an empty/blank query clears results synchronously
// without calling search() at all, and still respects the same stale guard.
{
  const state = { current: 5 };
  const rendered = [];
  await runGuardedBuscar('   ', 5, state, {
    search: () => { throw new Error('search() must not be called for a blank query'); },
    onResult: (list) => rendered.push(list),
    onError: () => { throw new Error('unreachable'); },
  });
  assert.deepStrictEqual(rendered, [[]]);
}

// contarCargaPorInspector — client-side one-pass tally over the already-
// loaded puntos list (design.md ADR-5): only counts puntos WITH an
// inspector_uid, never throws on a missing/empty list.
assert.deepStrictEqual(
  contarCargaPorInspector([
    { id: '1', inspector_uid: 'uid-1' },
    { id: '2', inspector_uid: 'uid-1' },
    { id: '3', inspector_uid: 'uid-2' },
    { id: '4' }, // no inspector_uid — not counted
    { id: '5', inspector_uid: null },
  ]),
  { 'uid-1': 2, 'uid-2': 1 },
);
assert.deepStrictEqual(contarCargaPorInspector([]), {});
assert.deepStrictEqual(contarCargaPorInspector(undefined), {});

// inspectorLabelConCarga — adapted from stickers-asignacion.js's
// inspectorOptionLabel(): "Nombre — codigo (N)"; falls back to "Brigada
// {codigo}" when nombre_completo is missing, and to 0 when count is falsy.
assert.strictEqual(
  inspectorLabelConCarga({ nombre_completo: 'Ana Torres', codigo: 'INS-01' }, 3),
  'Ana Torres — INS-01 (3)',
);
assert.strictEqual(inspectorLabelConCarga({ codigo: 'INS-02' }, 0), 'Brigada INS-02 — INS-02 (0)');
assert.strictEqual(inspectorLabelConCarga({ nombre_completo: 'Sin código' }, undefined), 'Sin código (0)');

console.log('ok — puntos_solicitados.js estado classification, filters, sort, buscar prefill mapping, stale-search guard, inspector load-count tally');
