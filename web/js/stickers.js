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
export function tagFuente(data) {
  const fuenteRespuesta = (data && data.fuente) || 'firestore';
  const list = Array.isArray(data && data.evaluaciones) ? data.evaluaciones : [];
  return {
    evaluaciones: list.map((e) => ({ fuente: fuenteRespuesta, ...e })),
    degraded: Boolean(data && data.degraded),
  };
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
export async function fetchEvaluacionesOnce(getToken, endpoint) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volvé a iniciar sesión.');
  const res = await fetch(apiUrl(endpoint), { headers: { Authorization: `Bearer ${token}` } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorMessageFor(data, res.status));
  return tagFuente(data);
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
    // Read-only, safe to retry: intermittent "Failed to fetch" (network blip,
    // cold serverless connection) shouldn't surface as an error when a second
    // attempt half a second later would have worked.
    fetchEvaluaciones: async () => {
      try {
        return await fetchEvaluacionesOnce(getToken, ENDPOINT);
      } catch (err) {
        await new Promise((r) => setTimeout(r, 500));
        return await fetchEvaluacionesOnce(getToken, ENDPOINT);
      }
    },
  });

  // Leaflet renders broken tiles when its container was hidden at build time
  // (e.g. the Stickers view tab itself was not yet the active one); re-measure
  // once the section is actually visible/sized.
  setTimeout(() => evaluacionesHandle.invalidate(), 60);
}
