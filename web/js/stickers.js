// Stickers view: the field operation in one screen.
//
// Renders Evaluaciones ATC-20 — KPIs, map and detail of what the brigades
// recorded (see evaluaciones.js) — directly, with no sub-tabs above it.
//
// Formerly this tab also carried an "Asignación" segment (cuadrillas/
// inspector assignment of Panel points, stickers-asignacion.js) and a
// "Fuente" toggle between Atención Sismo and the Formulario (Firestore)
// endpoint. Both were removed once Atención Sismo proved out in production:
// Asignación's workflow is not used, and Firestore was only ever a fallback
// while the code join was being validated. Atención Sismo is now the ONLY
// data source. The backend routes those two features depended on
// (`api/sticker-asignaciones.js`, `backend/app/routers/stickers.py`'s
// /evaluaciones, `stickers_atencionsismo.py` stays in use) were left as-is —
// they may now be unreferenced from the UI, which is fine, out of scope for
// this restructure.
//
// Inspector-roster CRUD (list, create, enable/disable) moved to Planeación
// (`usuarios-personas-unificadas` Phase 3, `web/js/planeacion.js`'s own
// "Inspectores" segment) — it is no longer part of this tab.
import { sectionHtml as evalSectionHtml, initEvaluaciones } from './evaluaciones.js';
import { apiUrl } from './api-config.js';

// The single source for the Evaluaciones section (design D2's
// stickersAtencionsismo shape) — see the top-of-file comment for why the
// Firestore alternative (`evaluaciones` endpoint) was dropped.
const ENDPOINT = 'stickersAtencionsismo';

// Cached backend read (backend/app/routers/stickers_atencionsismo.py GET
// /stickers-atencionsismo, 5-min TTL) — replaces the legacy POST
// /api/stickers {action:'evaluaciones'} full-collection read.
// `degraded` (see backend/app/routers/stickers.py's EvaluacionesCache): true
// when a cold start served the Blob-redacted last-known-good copy instead of
// a live read — that copy has inspector.np blanked, which silently wrecks
// the Fase I/II classification, so evaluaciones.js needs the flag alongside
// the data to warn about it.
//
// `fuente` (design D2): each record carries its own `fuente` from the
// backend already, but tag it here too from the RESPONSE's top-level
// `fuente` field (falling back to 'firestore', GET /evaluaciones's legacy
// shape) so a record missing the field (older cached payload) still
// classifies correctly for faseDe()/detailHtml() — a RECORD's own `fuente`
// still wins (object-spread order below), this is only the fallback for
// records that don't carry one. Exported: pure, so a self-check can exercise
// it without a fetch/DOM stub.
//
// `depuracion` (seguimiento-inspectores-depurado, Fase 4): passed through
// AS-IS, never interpreted here — the only consumer that reads its shape is
// seguimiento.js's buildIdentityIndex. This is the ONE shared read path
// between initStickers (this file's own tab, whose endpoint never sends the
// field) and seguimiento.js's fetchEvaluacionesOnce reuse, so a plain
// passthrough with a `null` default (never `undefined`, matching every other
// field's fixed-shape contract) keeps initStickers byte-identical while
// finally letting the field reach seguimiento.js at all.
//
// `etag` (seguimiento-inspectores-depurado PR 10 part 2, D28): the response's
// ETag HEADER, surfaced additively as the 2nd argument (`null` when absent or
// unusable, see usableEtag). It is the opaque `snapshot_id` Seguimiento
// retains; the body never carries one (that would break the flag-off 4-key
// byte-identity, design C-E3).
export function tagFuente(data, etag = null) {
  const fuenteRespuesta = (data && data.fuente) || 'firestore';
  const list = Array.isArray(data && data.evaluaciones) ? data.evaluaciones : [];
  return {
    evaluaciones: list.map((e) => ({ fuente: fuenteRespuesta, ...e })),
    degraded: Boolean(data && data.degraded),
    depuracion: (data && data.depuracion) || null,
    etag: usableEtag(etag),
  };
}

// Real ETags are ~50 characters; anything beyond this is not echoed back as an
// If-None-Match request header (a huge header value risks a 431/400 that would
// break every revalidation, so the frontend degrades to "full fetch instead").
const MAX_ETAG_LENGTH = 512;

// An ETag is an OPAQUE string: kept byte-for-byte (a weak `W/"..."` from an
// intermediary included) and only ever compared / echoed, never parsed. This
// only rejects what could not safely travel as a header value: non-strings,
// blank, oversized, or anything outside printable ASCII (control characters,
// CR/LF header injection, non-Latin1). `null` means "no usable validator" and
// makes the caller fall back to a plain full fetch. Exported: pure.
export function usableEtag(raw) {
  if (typeof raw !== 'string') return null;
  const value = raw.trim();
  if (!value || value.length > MAX_ETAG_LENGTH) return null;
  return /^[\x20-\x7e]+$/.test(value) ? value : null;
}

// The opt-in `depuracion=1` request parameter (design D25: EXACTLY that value,
// never a truthy-ish variant). Appends with `?` or `&` as the base needs, drops
// any `depuracion` the base already carried so exactly one survives, and keeps
// a `#fragment` last. `depuracion` other than the boolean `true` -> unchanged
// URL. Exported: pure.
export function buildEvaluacionesUrl(base, { depuracion = false } = {}) {
  if (depuracion !== true) return base;
  const hashAt = base.indexOf('#');
  const hash = hashAt >= 0 ? base.slice(hashAt) : '';
  const beforeHash = hashAt >= 0 ? base.slice(0, hashAt) : base;
  const queryAt = beforeHash.indexOf('?');
  const path = queryAt >= 0 ? beforeHash.slice(0, queryAt) : beforeHash;
  const kept = (queryAt >= 0 ? beforeHash.slice(queryAt + 1) : '')
    .split('&')
    .filter((part) => part && part.split('=')[0] !== 'depuracion');
  return `${path}?${[...kept, 'depuracion=1'].join('&')}${hash}`;
}

// Error message for a failed fetch. Prefers a structured `error`, then a
// STRING `detail` — a non-string detail (e.g. a FastAPI validation-error
// array/object slipping through a proxy) must not stringify into a useless
// "[object Object]" toast, so it falls back to the generic status message
// instead. Exported: pure, so a self-check can exercise it without a fetch
// stub.
export function errorMessageFor(data, status) {
  const detail = typeof (data && data.detail) === 'string' ? data.detail : null;
  return (data && data.error) || detail || `Error ${status}`;
}

// Exported so seguimiento.js can pull the same stickersAtencionsismo feed
// without duplicating the fetch/error-handling logic — just this same
// one-shot authenticated read.
//
// `opts` (all default OFF; seguimiento.js is the ONLY caller that sets them —
// the Stickers tab passes none, so its request and result stay exactly as
// before; design D25/D28):
//  - `depuracion: true`  request the PII `depuracion` block (`?depuracion=1`)
//    and pass it through. Without it the block is DROPPED from the result even
//    if the server sent one: the Stickers tab must never receive or render it.
//  - `ifNoneMatch`       a stored ETag, sent as `If-None-Match` when usable.
//  - `conditional: true` a 304 resolves to `{ notModified: true, etag }`
//    instead of an error. Without it a 304 is an Error (status 304): callers
//    that never sent a validator destructure `{evaluaciones, degraded}` and
//    must not receive a shapeless result.
//  - `strictJson: true`  an unreadable/non-object 200 body is an Error instead
//    of "zero stickers" (an unreadable body must never blank a retained table).
//  - `signal`, `bypassCache` forwarded to fetch (`cache: 'no-store'`).
// A failed HTTP answer throws an Error carrying `.status` (network failures
// carry none), so callers can tell 401/403 (authorization) from 5xx.
export async function fetchEvaluacionesOnce(getToken, endpoint, opts = {}) {
  const {
    depuracion = false, ifNoneMatch = null, conditional = false, strictJson = false, signal, bypassCache = false,
  } = opts || {};
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volvé a iniciar sesión.');
  const headers = { Authorization: `Bearer ${token}` };
  const validator = usableEtag(ifNoneMatch);
  if (validator) headers['If-None-Match'] = validator;
  const init = { headers };
  if (signal) init.signal = signal;
  if (bypassCache) init.cache = 'no-store';
  const res = await fetch(buildEvaluacionesUrl(apiUrl(endpoint), { depuracion }), init);
  const etag = readEtag(res);
  if (conditional && res.status === 304) return { notModified: true, etag };
  let data;
  let unreadable = false;
  try {
    data = await res.json();
  } catch {
    data = {};
    unreadable = true;
  }
  if (!res.ok) {
    const err = new Error(errorMessageFor(unreadable ? {} : data, res.status));
    err.status = res.status;
    throw err;
  }
  if (strictJson && (unreadable || !data || typeof data !== 'object' || Array.isArray(data))) {
    const err = new Error('Respuesta inválida del servidor.');
    err.status = res.status;
    throw err;
  }
  const result = tagFuente(data, etag);
  if (depuracion !== true) result.depuracion = null;
  return result;
}

// The response's ETag, or null: a missing header, a header the browser does not
// expose (CORS `expose_headers` not deployed) and any stub without a working
// `headers.get` all read as "no validator" and never throw.
function readEtag(res) {
  try {
    return usableEtag(res && res.headers && res.headers.get('ETag'));
  } catch {
    return null;
  }
}

// The Stickers tab's read: one retry over a transient network blip (read-only,
// safe to retry: a second attempt half a second later usually works), NEVER
// opting in to `depuracion` and never conditional. Exported so a test can pin
// that the tab's request has no `depuracion` parameter.
export async function fetchEvaluacionesForTab(getToken, { retryDelayMs = 500 } = {}) {
  try {
    return await fetchEvaluacionesOnce(getToken, ENDPOINT);
  } catch (err) {
    await new Promise((r) => setTimeout(r, retryDelayMs));
    return await fetchEvaluacionesOnce(getToken, ENDPOINT);
  }
}

// Rendered once per tab open. No segments/tabs anymore — Evaluaciones ATC-20
// is the only content this tab shows.
function shellHtml() {
  return `
    <header class="sticker-page-head">
      <h2 class="sticker-h1">Operación de campo</h2>
      <p class="sticker-lead">Lo que registran las brigadas en campo.</p>
    </header>

    ${evalSectionHtml()}`;
}

// initStickers(root, { getToken }) — renders the tab and wires its actions.
export function initStickers(root, { getToken }) {
  root.innerHTML = shellHtml();

  const evaluacionesHandle = initEvaluaciones(root.querySelector('.eval-section'), {
    // Read-only, safe to retry (see fetchEvaluacionesForTab): intermittent
    // "Failed to fetch" shouldn't surface as an error when a second attempt
    // half a second later would have worked. Never requests `depuracion`.
    fetchEvaluaciones: () => fetchEvaluacionesForTab(getToken),
  });

  // Leaflet renders broken tiles when its container was hidden at build time
  // (e.g. the Stickers view tab itself was not yet the active one); re-measure
  // once the section is actually visible/sized.
  setTimeout(() => evaluacionesHandle.invalidate(), 60);
}
