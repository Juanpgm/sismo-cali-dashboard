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

// Cached backend read (backend/app/routers/stickers.py GET /evaluaciones,
// 5-min TTL) — replaces the legacy POST /api/stickers {action:'evaluaciones'}
// full-collection read.
async function fetchEvaluacionesOnce(getToken) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volvé a iniciar sesión.');
  const res = await fetch(apiUrl('evaluaciones'), {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data.evaluaciones;
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

    <div data-sticker-section="evaluaciones">${evalSectionHtml()}</div>
    <div data-sticker-section="asignacion" hidden></div>`;
}

// initStickers(root, { getToken }) — renders the tab and wires its actions.
export function initStickers(root, { getToken }) {
  root.innerHTML = shellHtml();
  const segmentButtons = root.querySelectorAll('[data-sticker-segment]');
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
      try {
        return await fetchEvaluacionesOnce(getToken);
      } catch (err) {
        await new Promise((r) => setTimeout(r, 500));
        return await fetchEvaluacionesOnce(getToken);
      }
    },
  });

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
