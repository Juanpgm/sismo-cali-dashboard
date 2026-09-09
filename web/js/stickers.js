// Stickers view: the field operation in one screen.
//
// Two sections over the same /api/stickers endpoint:
//   · Evaluaciones ATC-20 — KPIs, map and detail of what the brigades recorded
//     (see evaluaciones.js).
//   · Asignación — cuadrillas/inspector assignment of Panel points
//     (stickers-asignacion.js).
//
// Inspector-roster CRUD (list, create, enable/disable) moved to Planeación
// (`usuarios-personas-unificadas` Phase 3, `web/js/planeacion.js`'s own
// "Inspectores" segment) — it is no longer part of this tab.
import { sectionHtml as evalSectionHtml, initEvaluaciones } from './evaluaciones.js';
import { initStickersAsignacion } from './stickers-asignacion.js';
import { apiUrl } from './api-config.js';
import { escapeHtml } from './utils.js';

// Two interchangeable sources for the Evaluaciones section, same response
// shape (design D2/D3). atencionsismo is the default; the Formulario
// (Firestore) source stays as the safety valve while the code join is
// being validated in production.
const FUENTES = {
  atencionsismo: { label: 'Atención Sismo', endpoint: 'stickersAtencionsismo' },
  firestore: { label: 'Formulario', endpoint: 'evaluaciones' },
};
let fuente = 'atencionsismo';

// Cached backend read (backend/app/routers/stickers.py GET /evaluaciones and
// backend/app/routers/stickers_atencionsismo.py GET /stickers-atencionsismo,
// both 5-min TTL) — replaces the legacy POST /api/stickers
// {action:'evaluaciones'} full-collection read. `endpoint` selects which of
// the two the caller wants (see FUENTES above).
// `degraded` (see backend/app/routers/stickers.py's EvaluacionesCache): true
// when a cold start served the Blob-redacted last-known-good copy instead of
// a live read — that copy has inspector.np blanked, which silently wrecks
// the Fase I/II classification, so evaluaciones.js needs the flag alongside
// the data to warn about it.
//
// `fuente` (design D2): each record carries its own `fuente` from the
// backend already, but tag it here too from the RESPONSE's top-level
// `fuente` field (falling back to 'firestore', GET /evaluaciones's shape) so
// a record missing the field (older cached payload) still classifies
// correctly for faseDe()/detailHtml() — a RECORD's own `fuente` still wins
// (object-spread order below), this is only the fallback for records that
// don't carry one. Exported: pure, so a self-check can exercise it without
// a fetch/DOM stub.
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
// without duplicating the fetch/error-handling logic — it isn't a "Fuente"
// toggle there (Seguimiento only ever wants Atención Sismo's stickers), just
// this same one-shot authenticated read.
export async function fetchEvaluacionesOnce(getToken, endpoint) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volvé a iniciar sesión.');
  const res = await fetch(apiUrl(endpoint), { headers: { Authorization: `Bearer ${token}` } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorMessageFor(data, res.status));
  return tagFuente(data);
}

// Segmented control for the Evaluaciones data source (design D3) — same
// chip shape as the Evaluaciones/Asignación segment below, one entry per
// FUENTES key. This one is a TOGGLE (pick one of two data sources), not a
// tab set (which section is showing) — aria-pressed, not aria-selected/role
// "tab", is the correct semantics; the wrapper below carries role="group"
// to match.
function fuenteSegmentedHtml() {
  return Object.entries(FUENTES).map(([key, def]) => `
      <button type="button" class="asignacion-segment${key === fuente ? ' is-active' : ''}"
        data-sticker-fuente="${key}" aria-pressed="${key === fuente}">${escapeHtml(def.label)}</button>`).join('');
}

// Rendered once per tab open. Two-way segmented control (Evaluaciones ·
// Asignación) — the Roster segment relocated to Planeación
// (`usuarios-personas-unificadas` Phase 3); inspector-roster CRUD now lives
// exclusively there.
function shellHtml() {
  return `
    <header class="sticker-page-head">
      <h2 class="sticker-h1">Operación de campo</h2>
      <p class="sticker-lead">Lo que registran las brigadas y quién puede registrarlo.</p>
    </header>

    <div class="asignacion-segmented" role="tablist" aria-label="Sección de Stickers">
      <button type="button" class="asignacion-segment is-active" data-sticker-segment="evaluaciones" role="tab" aria-selected="true">Evaluaciones</button>
      <button type="button" class="asignacion-segment" data-sticker-segment="asignacion" role="tab" aria-selected="false">Asignación</button>
    </div>

    <div data-sticker-section="evaluaciones">
      <div class="asignacion-segmented eval-fuente" role="group" aria-labelledby="eval-fuente-label">
        <span class="eval-fuente-label" id="eval-fuente-label">Fuente</span>${fuenteSegmentedHtml()}
      </div>
      ${evalSectionHtml()}
    </div>
    <div data-sticker-section="asignacion" hidden></div>`;
}

// initStickers(root, { getToken }) — renders the tab and wires its actions.
export function initStickers(root, { getToken }) {
  root.innerHTML = shellHtml();
  const segmentButtons = root.querySelectorAll('[data-sticker-segment]');
  const fuenteButtons = root.querySelectorAll('[data-sticker-fuente]');
  const sections = {
    evaluaciones: root.querySelector('[data-sticker-section="evaluaciones"]'),
    asignacion: root.querySelector('[data-sticker-section="asignacion"]'),
  };
  // Set on first "Asignación" open; subsequent opens call .reload() instead
  // of re-initializing (spec.md "Init runs once on first Asignación open").
  let asignacionHandle = null;

  const evaluacionesHandle = initEvaluaciones(root.querySelector('.eval-section'), {
    // Read-only, safe to retry: intermittent "Failed to fetch" (network blip,
    // cold serverless connection) shouldn't surface as an error when a second
    // attempt half a second later would have worked.
    fetchEvaluaciones: async () => {
      const endpoint = FUENTES[fuente].endpoint;
      try {
        return await fetchEvaluacionesOnce(getToken, endpoint);
      } catch (err) {
        await new Promise((r) => setTimeout(r, 500));
        return await fetchEvaluacionesOnce(getToken, endpoint);
      }
    },
  });

  // Switching Fuente re-fetches from the newly selected endpoint instead of
  // filtering client-side — the two sources are not merged (design D3), so
  // each switch is a fresh full load through evaluacionesHandle.reload().
  fuenteButtons.forEach((btn) => btn.addEventListener('click', () => {
    if (btn.dataset.stickerFuente === fuente) return;
    fuente = btn.dataset.stickerFuente;
    fuenteButtons.forEach((b) => {
      const active = b.dataset.stickerFuente === fuente;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-pressed', String(active));
    });
    evaluacionesHandle.reload();
  }));

  function showSegment(name) {
    for (const [key, el] of Object.entries(sections)) el.hidden = key !== name;
    segmentButtons.forEach((btn) => {
      const active = btn.dataset.stickerSegment === name;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', String(active));
    });
    // Leaflet renders broken tiles when its container was hidden at build time;
    // re-measure once the segment is actually visible.
    if (name === 'evaluaciones' && evaluacionesHandle) {
      setTimeout(() => evaluacionesHandle.invalidate(), 60);
    }
    if (name !== 'asignacion') return;
    if (asignacionHandle) {
      asignacionHandle.reload();
    } else {
      // The roster preload this used to reuse (`getInspectores: () =>
      // inspectoresCache`) moved to Planeación with the roster segment itself
      // (Phase 3) — Asignación now fetches its own copy (stickers-asignacion.js).
      asignacionHandle = initStickersAsignacion(sections.asignacion, { getToken });
    }
  }
  segmentButtons.forEach((btn) => btn.addEventListener('click', () => showSegment(btn.dataset.stickerSegment)));
}
