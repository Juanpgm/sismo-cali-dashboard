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
  // `planeacion-asignaciones` change (Phase 3): this is a NEW endpoint with
  // NO legacy Vercel twin, so — unlike every entry above — it has no
  // parity check to pass before flipping. It starts on Railway from day
  // one (design.md ADR-10's own note; the Planeación tab itself is
  // Phase 4, not wired to this entry yet).
  // Pin manual de cuál inspección representa a un edificio duplicado.
  // Endpoint nuevo, sin gemelo legacy en Vercel: nace en Railway.
  panelRepresentante: `${RAILWAY_BASE_URL}/panel-representante`,
  planeacionAsignaciones: `${RAILWAY_BASE_URL}/planeacion-asignaciones`,
  // `puntos-solicitados` change (Phase 4): REST-shaped CRUD (no `action`
  // dispatch, unlike planeacionAsignaciones) — `backend/app/routers/
  // puntos_solicitados.py`. New endpoint, no legacy Vercel twin, starts on
  // Railway from day one (same rationale as planeacionAsignaciones above).
  puntosSolicitados: `${RAILWAY_BASE_URL}/puntos-solicitados`,
  geocode: `${RAILWAY_BASE_URL}/geocode`,
  // Presigned S3 upload (`backend/app/routers/sign.py`), reused for punto
  // solicitado photos. KNOWN GAP: sign.py's CODIGO_RE/key prefix were never
  // extended for `clave_integracion` values — see puntos_solicitados.js's
  // subirFoto() comment.
  sign: `${RAILWAY_BASE_URL}/api/sign`,
};

// Small accessor so future consumers don't hand-index the map (typo-safety
// at the call site: `apiUrl('reportados')` throws loudly on an unknown key
// instead of silently fetching `undefined`).
export function apiUrl(name) {
  const url = API_CONFIG[name];
  if (!url) throw new Error(`api-config.js: unknown endpoint "${name}"`);
  return url;
}
