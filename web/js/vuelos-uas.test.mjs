// Self-check for the pure filtering/search/geo-resolution logic behind the
// Vuelos UAS tab. Run: node web/js/vuelos-uas.test.mjs
import assert from 'node:assert';
import {
  defaultUasFilters, applyUasFilters, matchesSearch, hasActiveUasFilters,
  comunaBarrioMap, comunaOptionsFrom, barrioOptionsFrom,
  pruneToValid, pruneInvalidBarrios, toggleSetValue, toggleBarrioValue, barrioDisabledFor,
  resolveGeoFor, resolveRowsGeo, vuelosPorComunaData,
  visibleSetKey, visibleSetChanged,
} from './vuelos-uas.js';

// ── defaultUasFilters: fresh-instance factory, mirrors defaultEvalFilters ──
{
  const f = defaultUasFilters();
  assert.deepStrictEqual(f, { search: '', comuna: new Set(), barrio: new Set() });
  const f1 = defaultUasFilters();
  const f2 = defaultUasFilters();
  assert.notStrictEqual(f1.comuna, f2.comuna, 'comuna Set must be a new instance each call');
  assert.notStrictEqual(f1.barrio, f2.barrio, 'barrio Set must be a new instance each call');
  f1.comuna.add('Comuna 9');
  assert.strictEqual(f2.comuna.size, 0, 'mutating one call\'s Set must not affect another call\'s Set');
}
console.log('vuelos-uas.test.mjs: defaultUasFilters OK');

// ── hasActiveUasFilters: drives "Reiniciar filtros"' enabled/soft-orange state ──
{
  assert.strictEqual(hasActiveUasFilters(defaultUasFilters()), false, 'no filters -> inactive');
  assert.strictEqual(hasActiveUasFilters({ ...defaultUasFilters(), search: 'cra 44' }), true, 'search alone -> active');
  assert.strictEqual(hasActiveUasFilters({ ...defaultUasFilters(), comuna: new Set(['Comuna 1']) }), true, 'comuna alone -> active');
  assert.strictEqual(hasActiveUasFilters({ ...defaultUasFilters(), barrio: new Set(['Barrio B']) }), true, 'barrio alone -> active');
  // Boundary (F9): whitespace-only search must NOT read as active —
  // matchesSearch already treats it as "no filtering" (see matchesSearch's
  // own empty/whitespace-only assertions below), so the button must agree —
  // otherwise it sits enabled but inert, with nothing for "Reiniciar
  // filtros" to actually reset.
  assert.strictEqual(hasActiveUasFilters({ ...defaultUasFilters(), search: '   ' }), false, 'whitespace-only search must NOT count as active');
  // State transition: clearing every filter back down must read inactive again.
  const cleared = defaultUasFilters();
  cleared.comuna.add('Comuna 1');
  cleared.comuna.delete('Comuna 1');
  assert.strictEqual(hasActiveUasFilters(cleared), false, 'emptied-back-out Set -> inactive');
  // Malformed input: must never throw.
  assert.strictEqual(hasActiveUasFilters(null), false, 'null filters -> inactive, no throw');
  assert.strictEqual(hasActiveUasFilters({}), false, 'filters missing comuna/barrio Sets -> inactive, no throw');
}
console.log('vuelos-uas.test.mjs: hasActiveUasFilters OK');

// ── matchesSearch: free-text search across every displayed variable ────────
const rowFull = {
  objectid: 1, lugar: 'Cra 44A #10-25', dia_captura: '2026-08-15',
  estado_edificacion: 'colapso_total', confirmacion: 'verificar', definicion: 'se_debe_confirmar',
  origen: 'sgred siata', origen_otro: 'Brigada externa', productos: 'foto_panoramica video_panoramico',
  observacion: 'Requiere demolición urgente', objectid_survey: 12345,
  _comuna: 'Comuna 3', _barrio: 'San Fernando',
};

// Empty/whitespace-only query: no filtering at all.
assert.strictEqual(matchesSearch(rowFull, ''), true, 'empty search string matches everything');
assert.strictEqual(matchesSearch(rowFull, '   '), true, 'whitespace-only search string matches everything');
assert.strictEqual(matchesSearch(rowFull, undefined), true, 'undefined search must not throw and matches everything');
assert.strictEqual(matchesSearch(rowFull, null), true, 'null search must not throw and matches everything');

// Matches across every displayed variable, accent/case-insensitive.
assert.strictEqual(matchesSearch(rowFull, 'DEMOLICIÓN'), true, 'matches observacion, accent/case-insensitive');
assert.strictEqual(matchesSearch(rowFull, 'colapso total'), true, 'matches the estado_edificacion label');
assert.strictEqual(matchesSearch(rowFull, 'verificar'), true, 'matches the confirmacion label');
assert.strictEqual(matchesSearch(rowFull, 'sgred'), true, 'matches an origen token');
assert.strictEqual(matchesSearch(rowFull, 'brigada externa'), true, 'matches origen_otro');
assert.strictEqual(matchesSearch(rowFull, 'panoramica'), true, 'matches a productos token');
assert.strictEqual(matchesSearch(rowFull, '12345'), true, 'matches objectid_survey');
assert.strictEqual(matchesSearch(rowFull, 'comuna 3'), true, 'matches the resolved _comuna');
assert.strictEqual(matchesSearch(rowFull, 'san fernando'), true, 'matches the resolved _barrio');
// Note: the exact formatted string is locale/ICU-dependent (a small-ICU
// Node build renders '15/08/2026' instead of a month name) — match on the
// year, which is stable either way.
assert.strictEqual(matchesSearch(rowFull, '2026'), true, 'matches the formatted dia_captura');
assert.strictEqual(matchesSearch(rowFull, 'no existe nada de esto'), false, 'a non-matching query excludes the row');

// Malformed dia_captura must never crash the search, even though it can
// never itself be matched by a date-formatted query.
const rowMalformedDia = { ...rowFull, dia_captura: 'no-es-una-fecha' };
assert.doesNotThrow(() => matchesSearch(rowMalformedDia, 'colapso'), 'malformed dia_captura must not crash search');
assert.strictEqual(matchesSearch(rowMalformedDia, 'colapso'), true, 'other fields still match despite malformed dia_captura');

// Address-aware matching on `lugar`: a query in a different address format
// than the stored value must still match (normalizeAddressText on both).
assert.strictEqual(matchesSearch(rowFull, 'carrera 44 a'), true, 'address-normalized query matches a differently-formatted lugar');
assert.strictEqual(matchesSearch({ ...rowFull, lugar: 'Kr 44 A # 10-25' }, 'carrera 44a'), true, 'a different way-type abbreviation in lugar still matches an address-normalized query');

console.log('vuelos-uas.test.mjs: matchesSearch OK');

// ── applyUasFilters: comuna/barrio Sets + search, AND between fields ───────
const rowsCB = [
  { objectid: 1, lugar: 'Calle 1', _comuna: 'Comuna 1', _barrio: 'Barrio A', dia_captura: '2026-08-01', estado_edificacion: '', confirmacion: '', definicion: '', origen: '', productos: '', observacion: '', objectid_survey: '' },
  { objectid: 2, lugar: 'Calle 2', _comuna: 'Comuna 1', _barrio: 'Barrio B', dia_captura: '2026-08-02', estado_edificacion: '', confirmacion: '', definicion: '', origen: '', productos: '', observacion: '', objectid_survey: '' },
  { objectid: 3, lugar: 'Calle 3', _comuna: 'Comuna 2', _barrio: 'Barrio B', dia_captura: '2026-08-03', estado_edificacion: '', confirmacion: '', definicion: '', origen: '', productos: '', observacion: '', objectid_survey: '' },
  // Unresolved geo (no coords, or fell outside every polygon) — must never
  // match a non-empty comuna/barrio filter.
  { objectid: 4, lugar: 'Calle 4', _comuna: null, _barrio: null, dia_captura: '2026-08-04', estado_edificacion: '', confirmacion: '', definicion: '', origen: '', productos: '', observacion: '', objectid_survey: '' },
];

assert.deepStrictEqual(applyUasFilters(rowsCB, defaultUasFilters()).map((r) => r.objectid), [1, 2, 3, 4], 'no filters -> everything passes');
assert.deepStrictEqual(
  applyUasFilters(rowsCB, { ...defaultUasFilters(), comuna: new Set(['Comuna 1']) }).map((r) => r.objectid),
  [1, 2],
);
assert.deepStrictEqual(
  applyUasFilters(rowsCB, { ...defaultUasFilters(), comuna: new Set(['Comuna 1', 'Comuna 2']), barrio: new Set(['Barrio B']) }).map((r) => r.objectid),
  [2, 3],
  'comuna + barrio combine as AND between fields, OR within each Set',
);
assert.strictEqual(
  applyUasFilters(rowsCB, { ...defaultUasFilters(), comuna: new Set(['Comuna 1']) }).some((r) => r.objectid === 4),
  false,
  'an unresolved-geo row never matches a non-empty comuna filter',
);
assert.deepStrictEqual(
  applyUasFilters(rowsCB, { ...defaultUasFilters(), search: 'calle 2' }).map((r) => r.objectid),
  [2],
);
assert.doesNotThrow(() => applyUasFilters(rowsCB, {}), 'a malformed/partial filters object must not throw');
assert.deepStrictEqual(applyUasFilters(rowsCB, {}).map((r) => r.objectid), [1, 2, 3, 4]);
assert.deepStrictEqual(applyUasFilters([], defaultUasFilters()), [], 'empty row list -> empty result, never throws');

console.log('vuelos-uas.test.mjs: applyUasFilters OK');

// ── comunaBarrioMap / comunaOptionsFrom / barrioOptionsFrom / pruneToValid ──
const rowsForMap = [
  { _comuna: 'Comuna 2', _barrio: 'Barrio C' },
  { _comuna: 'Comuna 2', _barrio: 'Barrio B' },
  { _comuna: 'Comuna 1', _barrio: 'Barrio A' },
  { _comuna: 'Comuna 1', _barrio: 'Barrio B' },
  { _comuna: 'Comuna 3', _barrio: null }, // resolved comuna, no barrio
  { _comuna: null, _barrio: null }, // unresolved entirely
];
const cbMap = comunaBarrioMap(rowsForMap);
assert.deepStrictEqual(comunaOptionsFrom(cbMap).map((o) => o.value), ['Comuna 1', 'Comuna 2', 'Comuna 3'], 'sorted, independent of insertion order');
assert.deepStrictEqual(comunaOptionsFrom(comunaBarrioMap([])), [], 'no rows -> no comuna options');
assert.deepStrictEqual(barrioOptionsFrom(cbMap, new Set(['Comuna 1'])).map((o) => o.value), ['Barrio A', 'Barrio B']);
assert.deepStrictEqual(
  barrioOptionsFrom(cbMap, new Set(['Comuna 1', 'Comuna 2'])).map((o) => o.value),
  ['Barrio A', 'Barrio B', 'Barrio C'],
  'union across selected comunas, deduped and sorted',
);
assert.deepStrictEqual(barrioOptionsFrom(cbMap, new Set()), [], 'zero comunas selected -> no barrio options');
assert.deepStrictEqual(barrioOptionsFrom(cbMap, new Set(['Comuna 3'])), [], 'a comuna with no resolved barrios offers none');
assert.deepStrictEqual(barrioOptionsFrom(cbMap, null), [], 'must not throw on a null comunaSet');
assert.deepStrictEqual(barrioOptionsFrom(cbMap, undefined), [], 'must not throw on an undefined comunaSet');

assert.deepStrictEqual(pruneToValid(new Set(['Barrio A', 'Barrio Z']), ['Barrio A', 'Barrio B']), new Set(['Barrio A']));
assert.deepStrictEqual(pruneToValid(new Set(), ['Barrio A']), new Set());
assert.deepStrictEqual(pruneToValid(new Set(['Barrio A']), []), new Set());

console.log('vuelos-uas.test.mjs: comunaBarrioMap / comunaOptionsFrom / barrioOptionsFrom / pruneToValid OK');

// ── Boundary: deselecting a comuna prunes the barrio Set that depended
// entirely on it down to empty, not stale. ─────────────────────────────────
{
  const soloMap = new Map([['Comuna 9', new Set(['Barrio Solo'])]]);
  let selectedComunas = new Set(['Comuna 9']);
  let selectedBarrios = new Set(['Barrio Solo']);
  selectedComunas.delete('Comuna 9');
  selectedBarrios = pruneInvalidBarrios(selectedBarrios, selectedComunas, soloMap);
  assert.deepStrictEqual(selectedComunas, new Set(), 'comuna Set is empty after deselecting its only member');
  assert.deepStrictEqual(selectedBarrios, new Set(), 'barrio Set ends up empty, not stale, once its only comuna is gone');
}

// pruneInvalidBarrios: union semantics preserved (a barrio under a STILL
// selected comuna survives even if it also belonged to a just-deselected one).
{
  const twoMap = new Map([
    ['Comuna 1', new Set(['Barrio A', 'Barrio B'])],
    ['Comuna 2', new Set(['Barrio B', 'Barrio C'])],
  ]);
  assert.deepStrictEqual(
    pruneInvalidBarrios(new Set(['Barrio B']), new Set(['Comuna 2']), twoMap),
    new Set(['Barrio B']),
    'Barrio B (under both comunas) survives when only Comuna 1 is deselected',
  );
  assert.deepStrictEqual(
    pruneInvalidBarrios(new Set(['Barrio A']), new Set(['Comuna 2']), twoMap),
    new Set(),
    'Barrio A, tied only to the now-deselected Comuna 1, is dropped',
  );
}

console.log('vuelos-uas.test.mjs: pruneInvalidBarrios / comuna deselection boundary OK');

// ── toggleSetValue: has/delete/add dance, mutates in place ─────────────────
{
  const s = new Set(['a']);
  toggleSetValue(s, 'b');
  assert.deepStrictEqual(s, new Set(['a', 'b']));
  toggleSetValue(s, 'b');
  assert.deepStrictEqual(s, new Set(['a']));
}
console.log('vuelos-uas.test.mjs: toggleSetValue OK');

// ── toggleBarrioValue: invalid state transition — toggling a barrio value
// that is not among the currently-valid options is a no-op / gets pruned,
// never silently accepted into the Set. ─────────────────────────────────────
{
  const validOptions = ['Barrio A', 'Barrio B'];
  const barrioSet = new Set();
  toggleBarrioValue(barrioSet, 'Barrio Z', validOptions);
  assert.deepStrictEqual(barrioSet, new Set(), 'toggling a value outside the valid options is a no-op, never added');

  // A normal, valid toggle still works exactly like toggleSetValue.
  toggleBarrioValue(barrioSet, 'Barrio A', validOptions);
  assert.deepStrictEqual(barrioSet, new Set(['Barrio A']), 'a valid value toggles on normally');
  toggleBarrioValue(barrioSet, 'Barrio A', validOptions);
  assert.deepStrictEqual(barrioSet, new Set(), 'a valid value toggles off normally');

  // A stale value that WAS selected but is no longer valid (e.g. comuna
  // selection narrowed) is pruned rather than toggled off-then-back-on.
  const staleSet = new Set(['Barrio Stale']);
  toggleBarrioValue(staleSet, 'Barrio Stale', validOptions);
  assert.deepStrictEqual(staleSet, new Set(), 'a stale, no-longer-valid selection is pruned away, not toggled');

  // Also accepts a Set directly for validOptions (not just an array).
  const bySet = new Set();
  toggleBarrioValue(bySet, 'Barrio A', new Set(validOptions));
  assert.deepStrictEqual(bySet, new Set(['Barrio A']), 'validOptions may be a Set, not just an array');
}
console.log('vuelos-uas.test.mjs: toggleBarrioValue OK');

// ── barrioDisabledFor ───────────────────────────────────────────────────────
assert.strictEqual(barrioDisabledFor(new Set()), true);
assert.strictEqual(barrioDisabledFor(new Set(['Comuna 1'])), false);
assert.strictEqual(barrioDisabledFor(null), true, 'must not throw on null');
assert.strictEqual(barrioDisabledFor(undefined), true, 'must not throw on undefined');
console.log('vuelos-uas.test.mjs: barrioDisabledFor OK');

// ── resolveGeoFor: memoization, invalid coords, and failure degradation ────
{
  // Boundary/empty/malformed: non-finite lat/lng resolves to null/null
  // WITHOUT ever calling the resolver.
  let calls = 0;
  const spy = async () => { calls += 1; return { comuna: 'X', barrio: 'Y' }; };
  const r1 = await resolveGeoFor(NaN, -76.5, spy);
  const r2 = await resolveGeoFor(3.4, undefined, spy);
  const r3 = await resolveGeoFor(null, null, spy);
  assert.deepStrictEqual(r1, { _comuna: null, _barrio: null });
  assert.deepStrictEqual(r2, { _comuna: null, _barrio: null });
  assert.deepStrictEqual(r3, { _comuna: null, _barrio: null });
  assert.strictEqual(calls, 0, 'the resolver is never invoked for non-finite coordinates');
}

{
  // Concurrent/race: two overlapping lookups for the EXACT SAME rounded
  // coordinate share one in-flight promise — the resolver is called once,
  // not twice, even when both calls happen before either resolves.
  let calls = 0;
  let resolveDeferred;
  const deferred = new Promise((res) => { resolveDeferred = res; });
  const spy = async (lat, lng) => { calls += 1; await deferred; return { comuna: 'Comuna Race', barrio: 'Barrio Race' }; };

  const lat = 3.412345;
  const lng = -76.512345;
  const p1 = resolveGeoFor(lat, lng, spy);
  const p2 = resolveGeoFor(lat, lng, spy); // same rounded key, issued before p1 settles
  assert.strictEqual(calls, 1, 'a second overlapping lookup for the same key does not issue a second resolver call');
  resolveDeferred();
  const [res1, res2] = await Promise.all([p1, p2]);
  assert.deepStrictEqual(res1, { _comuna: 'Comuna Race', _barrio: 'Barrio Race' });
  assert.deepStrictEqual(res2, { _comuna: 'Comuna Race', _barrio: 'Barrio Race' });
  assert.strictEqual(calls, 1, 'still exactly one resolver call after both promises settle');
}

{
  // Network/server failure: a rejecting resolver degrades to null/null for
  // the affected coordinate — never throws, never blanks unrelated lookups.
  const failingSpy = async () => { throw new Error('geojson fetch failed'); };
  await assert.doesNotReject(
    async () => resolveGeoFor(3.499999, -76.499999, failingSpy),
    'a rejecting resolver must not propagate as a rejection',
  );
  const failed = await resolveGeoFor(3.499999, -76.499999, failingSpy);
  assert.deepStrictEqual(failed, { _comuna: null, _barrio: null }, 'a failed lookup degrades to null/null, not a thrown error');

  // Recovery-after-transient-failure (F3): geoCache must NOT memoize the
  // failure — a later call for the EXACT SAME key, once the resolver starts
  // succeeding again, must actually re-invoke it and return the real
  // comuna/barrio, not the stale cached null/null. This is the concurrent/
  // race-adjacent "transient-then-success" resolution the fix exists for.
  const recoverySpy = async () => ({ comuna: 'Comuna Recuperada', barrio: 'Barrio Recuperado' });
  const recovered = await resolveGeoFor(3.499999, -76.499999, recoverySpy);
  assert.deepStrictEqual(
    recovered, { _comuna: 'Comuna Recuperada', _barrio: 'Barrio Recuperado' },
    'a retry after a transient failure resolves the real comuna/barrio — the failed promise must not have been memoized',
  );

  // A DIFFERENT coordinate resolved successfully in the same batch is
  // unaffected by the failure above.
  const okSpy = async () => ({ comuna: 'Comuna OK', barrio: 'Barrio OK' });
  const ok = await resolveGeoFor(3.488888, -76.488888, okSpy);
  assert.deepStrictEqual(ok, { _comuna: 'Comuna OK', _barrio: 'Barrio OK' });
}

console.log('vuelos-uas.test.mjs: resolveGeoFor memoization/invalid-coords/failure-degradation OK');

// ── resolveRowsGeo: per-row resolution over a batch, isolating failures ────
{
  const rows = [
    { objectid: 1, _lat: 3.421111, _lng: -76.521111 }, // will succeed
    { objectid: 2, _lat: 3.422222, _lng: -76.522222 }, // will fail -> null/null
    { objectid: 3, _lat: NaN, _lng: -76.523333 }, // non-finite -> null/null, no resolver call
    { objectid: 4 }, // missing lat/lng entirely -> null/null
  ];
  let calls = 0;
  const mixedResolver = async (lat, lng) => {
    calls += 1;
    if (lat === 3.422222) throw new Error('simulated network failure');
    return { comuna: `Comuna-${lat}`, barrio: `Barrio-${lat}` };
  };
  const resolved = await resolveRowsGeo(rows, mixedResolver);
  assert.strictEqual(resolved.length, 4, 'every row survives, even the failed/invalid ones');
  assert.deepStrictEqual(
    { _comuna: resolved[0]._comuna, _barrio: resolved[0]._barrio },
    { _comuna: 'Comuna-3.421111', _barrio: 'Barrio-3.421111' },
  );
  assert.deepStrictEqual(
    { _comuna: resolved[1]._comuna, _barrio: resolved[1]._barrio },
    { _comuna: null, _barrio: null },
    'a rejected resolution degrades this row to null/null',
  );
  assert.deepStrictEqual(
    { _comuna: resolved[2]._comuna, _barrio: resolved[2]._barrio },
    { _comuna: null, _barrio: null },
    'non-finite coordinates resolve to null/null',
  );
  assert.deepStrictEqual(
    { _comuna: resolved[3]._comuna, _barrio: resolved[3]._barrio },
    { _comuna: null, _barrio: null },
    'missing coordinates resolve to null/null',
  );
  assert.strictEqual(calls, 2, 'the resolver is only called for the two rows with finite coordinates');
}
console.log('vuelos-uas.test.mjs: resolveRowsGeo OK');

// ── vuelosPorComunaData: chart data behind "Vuelos por comuna" ─────────────
{
  const rows = [
    { _comuna: 'Comuna 3' }, { _comuna: 'Comuna 1' }, { _comuna: 'Comuna 1' },
    { _comuna: null }, { _comuna: 'Comuna 2' }, { _comuna: null },
  ];
  const { labels, data } = vuelosPorComunaData(rows);
  assert.deepStrictEqual(labels, ['Comuna 1', 'Comuna 2', 'Comuna 3', 'Sin dato'], 'alphabetical, Sin dato last');
  assert.deepStrictEqual(data, [2, 1, 1, 2]);

  const noneMissing = vuelosPorComunaData([{ _comuna: 'Comuna 1' }]);
  assert.deepStrictEqual(noneMissing.labels, ['Comuna 1'], 'no Sin dato bucket when every row resolved');

  const empty = vuelosPorComunaData([]);
  assert.deepStrictEqual(empty, { labels: [], data: [] }, 'empty row list -> empty chart data, never throws');
}
console.log('vuelos-uas.test.mjs: vuelosPorComunaData OK');

// ── visibleSetChanged / visibleSetKey (F6): the "did the visible marker SET
// actually change?" decision behind updateMapVisibility's fitBounds dedupe —
// skips a redundant recompute (and the pre-existing double-fit-on-load bug)
// when the set of visible `_idx` values is the same as last time. ─────────
{
  assert.strictEqual(visibleSetChanged([1, 2, 3], [1, 2, 3]), false, 'same set -> no change');
  assert.strictEqual(visibleSetChanged([1, 2], [1, 2, 3]), true, 'different set -> change');
  assert.strictEqual(visibleSetChanged([], []), false, 'empty -> empty -> no change');
  assert.strictEqual(visibleSetChanged([], [1, 2]), true, 'empty -> N -> change');
  assert.strictEqual(visibleSetChanged([1, 2], []), true, 'N -> empty -> still a change');
  assert.strictEqual(visibleSetChanged([1, 2, 3], [3, 1, 2]), false, 'same members in a different order -> NOT a change');
  assert.strictEqual(visibleSetChanged([1, 1, 2], [2, 1]), false, 'duplicate membership does not count as a difference');
  // Malformed input must not throw.
  assert.doesNotThrow(() => visibleSetChanged(null, undefined));
}
console.log('vuelos-uas.test.mjs: visibleSetChanged / visibleSetKey OK');
