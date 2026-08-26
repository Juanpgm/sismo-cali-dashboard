// Per-endpoint URL map for the dashboard's `/api/*` calls — the consumer
// repoint mechanism the FastAPI backend consolidation cuts over to
// (openspec/changes/fastapi-backend-consolidation/design.md ADR-7).
//
// `web/js` calls RELATIVE paths today (`/api/reportados`, `/api/sticker-status`,
// ...), so there was no single base constant to flip. This map gives each
// endpoint its OWN entry, defaulting to that same relative path (today's
// same-origin Vercel function) — zero behavior change while every value
// below stays a relative path. Each consolidation slice flips EXACTLY ONE
// entry to the Railway base URL once that endpoint's parity check passes
// (ADR-7's "Parity procedure per endpoint"); rollback is reverting that
// one line and redeploying `web/`.
//
// NOT wired into any consumer yet (`data.js`, `main.js`, `stickers.js`,
// `usuarios.js`, `analista.js`, `auth.js`, `evaluaciones.js`,
// `stickers-asignacion.js`, `coverage-gauge.js` all still call their
// relative paths directly). The first slice to actually flip an entry
// (reportados, tasks.md 3.7) needs a live Railway URL from the manual
// operator step (tasks.md 1.4) before it can wire `data.js`'s
// `refreshReportados()` to read from here instead of the hardcoded
// `/api/reportados` literal — until then this module is inert.
export const API_CONFIG = {
  reportados: '/api/reportados',
  stickerStatus: '/api/sticker-status',
  refresh: '/api/refresh',
  stickers: '/api/stickers',
  stickerAsignaciones: '/api/sticker-asignaciones',
  usuarios: '/api/usuarios',
  sourceStatus: '/api/source-status',
};

// Small accessor so future consumers don't hand-index the map (typo-safety
// at the call site: `apiUrl('reportados')` throws loudly on an unknown key
// instead of silently fetching `undefined`).
export function apiUrl(name) {
  const url = API_CONFIG[name];
  if (!url) throw new Error(`api-config.js: unknown endpoint "${name}"`);
  return url;
}
