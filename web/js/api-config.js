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
// `reportados` (tasks.md 3.7) and `stickerStatus`/`sourceStatus` (tasks.md
// 4.6) are now WIRED into their consumers — task 1.4's Railway "web"
// service is live and each entry's own parity check passed (3.6: shape-
// identical, totals within tolerance, <2s response; 4.5: exact-match
// totals/con for sticker-status, identical `ok`/`status` shape for
// source-status with an admin token). Every OTHER entry below stays on its
// legacy relative path — `stickers.js`, `usuarios.js`, `auth.js`,
// `evaluaciones.js`, `stickers-asignacion.js`, `coverage-gauge.js` are
// untouched; their own consolidation slices (6-8) flip their entries only
// after each one's own parity check passes.
const RAILWAY_BASE_URL = 'https://sismo-cali-dashboard-production.up.railway.app';

export const API_CONFIG = {
  reportados: `${RAILWAY_BASE_URL}/reportados`,
  stickerStatus: `${RAILWAY_BASE_URL}/sticker-status`,
  refresh: '/api/refresh',
  stickers: '/api/stickers',
  stickerAsignaciones: '/api/sticker-asignaciones',
  usuarios: '/api/usuarios',
  sourceStatus: `${RAILWAY_BASE_URL}/source-status`,
};

// Small accessor so future consumers don't hand-index the map (typo-safety
// at the call site: `apiUrl('reportados')` throws loudly on an unknown key
// instead of silently fetching `undefined`).
export function apiUrl(name) {
  const url = API_CONFIG[name];
  if (!url) throw new Error(`api-config.js: unknown endpoint "${name}"`);
  return url;
}
