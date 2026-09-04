// Node self-check for mapview.js's ensureGeo promise memoization (via
// resolveBarrioComuna) and the zonas_interes overlay layer. Run:
// node web/js/mapview.test.mjs
import assert from 'node:assert/strict';

let fetchCalls = 0;
let failNext = false;
let mockJson = { features: [] };
globalThis.fetch = async () => {
  fetchCalls += 1;
  if (failNext) return { ok: false };
  return { ok: true, json: async () => mockJson };
};

// Minimal Leaflet stand-in: mapview.js expects a global `L` (loaded via CDN
// script tag in index.html, never imported). Only the pieces initMap/render/
// setZonasInteresVisible actually touch are stubbed — no real
// rendering/DOM, just enough to exercise the layer add/remove bookkeeping.
function makeFakeMap() {
  const layers = new Set();
  return {
    _layers: layers,
    addLayer(layer) { layers.add(layer); return this; },
    removeLayer(layer) { layers.delete(layer); return this; },
    hasLayer(layer) { return layers.has(layer); },
    setView() { return this; },
    invalidateSize() {},
  };
}
// initMap() reads the theme via basemapTileUrl() (utils.js), which touches
// `document.documentElement.dataset.theme` — stub just that much of the DOM.
globalThis.document ??= { documentElement: { dataset: { theme: 'light' } } };

globalThis.L = {
  map: () => makeFakeMap(),
  tileLayer: () => ({ addTo() { return this; }, bringToBack() {} }),
  layerGroup: () => ({
    addTo(map) { map.addLayer(this); return this; },
    clearLayers() {},
  }),
  control: () => ({ addTo() { return this; } }),
  geoJSON: (geo, opts) => {
    const safe = geo && Array.isArray(geo.features) ? geo : { features: [] };
    if (opts && typeof opts.onEachFeature === 'function') {
      for (const f of safe.features) opts.onEachFeature(f, { bindTooltip() {} });
    }
    const layer = {
      __isZonasLayer: true,
      addTo(map) { map.addLayer(layer); return layer; },
      bringToBack() {},
    };
    return layer;
  },
};

const {
  resolveBarrioComuna, initMap, render, setZonasInteresVisible, buildZonasInteresLayer,
} = await import('./mapview.js');

// 1. A failed load rejects every concurrent caller but does NOT poison the
//    cache: the in-flight slot clears so a later call can retry.
failNext = true;
await assert.rejects(() => resolveBarrioComuna(3.42, -76.53), /No se pudo cargar/);

// 2. Concurrent callers share ONE fetch per file instead of a thundering herd
//    (Evaluaciones fires hundreds of these at once).
failNext = false;
fetchCalls = 0;
const results = await Promise.all(
  Array.from({ length: 50 }, () => resolveBarrioComuna(3.42, -76.53)),
);
assert.equal(fetchCalls, 2, `expected 2 fetches (comunas+barrios), got ${fetchCalls}`);
assert.deepEqual(results[0], { comuna: null, barrio: null });

// 3. Once parsed, later calls fetch nothing at all.
fetchCalls = 0;
await resolveBarrioComuna(3.42, -76.53);
assert.equal(fetchCalls, 0);

console.log('ok — mapview.js ensureGeo single-flight geo load');

// ---------------------------------------------------------------------
// zonas_interes overlay: setZonasInteresVisible
// ---------------------------------------------------------------------
// Reset the shared fetch mock's counters (resolveBarrioComuna's tests above
// already used them) before starting a fresh narrative against the fake map.
failNext = false;
fetchCalls = 0;
mockJson = { features: [] };

const isZonaLayer = (l) => !!(l && l.__isZonasLayer);
const countZonaLayers = (m) => [...m._layers].filter(isZonaLayer).length;

const map = initMap('fake-container');

// 0. Off when already off (module starts with the overlay off): no-op, no
//    fetch, nothing added.
await setZonasInteresVisible(false);
assert.equal(fetchCalls, 0, 'off-when-off must not fetch');
assert.equal(countZonaLayers(map), 0);

// 1. A failed fetch does not leave a half-added layer, and rejects the
//    caller (state stays consistent — never "on" with nothing drawn).
failNext = true;
await assert.rejects(() => setZonasInteresVisible(true), /No se pudo cargar/);
assert.equal(countZonaLayers(map), 0, 'a failed enable must not add a layer');

// 2. Two rapid CONCURRENT setZonasInteresVisible(true) calls issue only ONE
//    fetch (single-flight guard) — this is also the "subsequent toggle-on
//    retries the fetch" case from step 1: the failed slot must have cleared,
//    or this would hang/never fetch again.
failNext = false;
fetchCalls = 0;
mockJson = {
  type: 'FeatureCollection',
  features: [{ type: 'Feature', properties: { name: 'Centro Histórico' }, geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1]]] } }],
};
await Promise.all([setZonasInteresVisible(true), setZonasInteresVisible(true)]);
assert.equal(fetchCalls, 1, 'two concurrent enables must share a single fetch');
assert.equal(countZonaLayers(map), 1, 'must not duplicate the layer across concurrent enables');

// 3. Toggling on when already on: no-op (no double fetch, no duplicate layer).
await setZonasInteresVisible(true);
assert.equal(fetchCalls, 1, 'on-when-on must not re-fetch');
assert.equal(countZonaLayers(map), 1, 'on-when-on must not duplicate the layer');

// 4. The layer must survive render()/mode-shaped calls: it lives directly on
//    `map`, never inside pointsLayer/heatLayer/choroplethLayer, so
//    clearLayers() (which only removes those three) never touches it.
await render([]);
await render([]);
assert.equal(countZonaLayers(map), 1, 'render()/clearLayers() must not remove the overlay');

// 5. Off -> on -> off leaves the map without the layer.
await setZonasInteresVisible(false);
assert.equal(countZonaLayers(map), 0, 'off must remove the layer from the map');
await setZonasInteresVisible(true);
assert.equal(countZonaLayers(map), 1);
await setZonasInteresVisible(false);
assert.equal(countZonaLayers(map), 0);

// 6. Off when already off (again): no-op.
await setZonasInteresVisible(false);
assert.equal(countZonaLayers(map), 0);

// 7. The geojson is fetched only ONCE across every on/off cycle above
//    (steps 2-6): the one successful fetch from step 2 is cached forever,
//    same convention as comunasGeo/barriosGeo.
assert.equal(fetchCalls, 1, 'zonas_interes.geojson must be fetched only once across on/off cycles');

console.log('ok — mapview.js setZonasInteresVisible (toggle idempotency, single-flight, survives render())');

// 8. A geojson with 0 features / malformed `features` does not throw.
// Exercised directly against buildZonasInteresLayer (extracted pure-ish
// helper) since by this point in the file zonas_interes.geojson is already
// cached — setZonasInteresVisible itself would never re-parse a new body.
assert.doesNotThrow(() => buildZonasInteresLayer({ type: 'FeatureCollection', features: [] }));
assert.doesNotThrow(() => buildZonasInteresLayer({ type: 'FeatureCollection' })); // missing features
assert.doesNotThrow(() => buildZonasInteresLayer({ features: 'not-an-array' }));
assert.doesNotThrow(() => buildZonasInteresLayer(null));
assert.doesNotThrow(() => buildZonasInteresLayer(undefined));

console.log('ok — mapview.js buildZonasInteresLayer defensive on malformed/empty geojson');
