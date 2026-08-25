# Exploration: analista-fuentes-datos-tab

## Request

New admin-only tab named **"Analista"** in the dashboard (`web/`) listing all project data sources, with per-source read/connection status and error state, color-coded.

## 1. Role-gating pattern (reusable as-is)

Admin-only visibility is enforced in two layers; a third tab slots in with a small diff:

- **CSS gate** — `web/styles.css:1559-1563`:
  ```css
  body:not([data-role="admin"]) .view-tab[data-view="acciones"],
  body:not([data-role="admin"]) .view-tab[data-view="stickers"],
  body:not([data-role="admin"]) .view-tab[data-view="usuarios"] { display: none !important; }
  ```
  Add `.view-tab[data-view="analista"]` to this selector list.
- **JS role source of truth** — `web/js/auth.js` (`roleForUser`) sets `document.body.dataset.role`; `isAdmin()` is exported and used by `main.js`/`usuarios.js`/`stickers.js` for defense-in-depth (button guards even if CSS is bypassed).
- **Server-side mirror** — `api/refresh.js` `roleFrom()`/`roleFromClaims()` duplicates the same precedence logic server-side. If "Analista" only *reads* already-public `web/data/*.json`/Blob JSON (no privileged action), no new server endpoint is strictly needed — but any new status-check endpoint must re-verify `role === 'admin'` the same way `api/usuarios.js`/`api/refresh.js` do.

## 2. Tab architecture (reusable as-is)

Pattern for every extra tab (Stickers, Usuarios) is identical, in `web/index.html` + `web/js/main.js`:
1. `<button class="view-tab" data-view="X" role="tab">` in `<nav class="view-tabs">` (`web/index.html:70-76`).
2. `<section id="view-X" data-view-panel="X" hidden></section>` sibling section (`web/index.html:269-273`).
3. `switchView()` in `main.js:181-212` toggles `.is-active`/`hidden` by `data-view`/`data-view-panel`, and lazy-inits the tab's module on first open: `if (view === 'usuarios') { initUsuarios(document.getElementById('view-usuarios'), { getToken: getIdToken }); }`.
4. A new `web/js/analista.js` exporting `initAnalista(root, opts)` would mirror `usuarios.js`'s shape (`initXxx(root, {getToken})`, re-fetch/recompute on every open, own render/wire functions) — no new architecture needed.

Generic CSS building blocks already exist and are reused across Stickers/Usuarios (`sticker-page-head`, `sticker-roster`, `sticker-chips`, `sticker-chip`, `sticker-list`/`sticker-row`, `section-bar`) — a data-sources list is naturally a `sticker-list` of rows, no new component required.

## 3. Data source inventory (core of this tab)

**Pipeline A — `dashboard-refresh` (Railway service, cron `*/15`, `deploy/entrypoint.sh`/`deploy/refresh.sh`):**

| Step (script) | External source | Output(s) | Consumed by |
|---|---|---|---|
| `scripts/refresh_data.py` | Google Sheet `tabla_normalizada` (EDAN-F3), service account or public xlsx export (`DRIVE_FILE_ID`) | `web/data/inspections.json`, `web/data/meta.json`, `web/data/inspections.xlsx` | `data.js` (Panel/map/table/KPIs) |
| same script, sub-step | Survey123 ArcGIS FeatureServer (`SURVEY_LAYER_URL`) — `queryAttachments` (photo EXIF GPS) + `/query` (geometry cross-check) | folded into `inspections.json` | same |
| same script, sub-step | Google Maps Geocoding API (optional, `GOOGLE_MAPS_API_KEY`) | `web/data/geocode/geocode_cache.json` | internal cache only |
| `scripts/fetch_reportes_api.py` | `atencionsismo` API `informe/json` (needs `VISITADOS_API_PASS`) | `web/data/reportes.json`, `reportes_meta.json`, `reportes_agg.json` | `data.js` `refreshReportados()` fallback, KPI "Reportados" |
| `deploy/blob_sync.py upload` (×6 files, `refresh.sh:88-96`) | Vercel Blob (`sismo-dashboard-data` store) | mirrors the 6 files + `data/_status.json` | `data.js` `fetchData()` tries Blob first, falls back to git-deployed `web/data/` |
| live, not part of the batch | `atencionsismo` API via `/api/reportados` serverless proxy (15-min CDN cache) | live JSON | `store.refreshReportados()` |

**Pipeline B — `cruce-gestion` (separate Railway service, own 15-min cron), living in `integracion_F1/`:**

| Script | Source | Output | Consumed by |
|---|---|---|---|
| `integracion_F1/asignar_f3.py` | Google Sheets roster + `atencionsismo` API "Matriz EDE"/críticos | `web/data/asignaciones.json`, `web/data/zonas_asignacion.geojson` | **nothing in current `web/js`** |
| `integracion_F1/cruce_gestor.py` | reads `asignaciones.json` + PMU Apps Script API | `web/data/cruce_gestor.json` | **nothing in current `web/js`** |
| `integracion_F1/cruce_criticos_survey.py` | criticos API vs corrected survey coords | `web/data/cruce_criticos_survey.json` | **nothing in current `web/js`** |

**Untracked / ad-hoc (git status shows untracked, no cron):**
- `criticos_api.json` — only used by `analisis_puntos_criticos.ipynb` (notebook, not scheduled).
- `aseguradoras.ipynb`, `aseguradoras_match_shp/`, `puntos_israel_cali.csv` — manual/notebook artifacts, no pipeline wiring.
- Israel FeatureServer + Firestore `inspecciones_israel` — live-fetched client-side by `web/js/israel-source.js` on every Panel load (`data.js:6`), independent of both Railway pipelines.

**Orphaned outputs finding**: `asignaciones.json`, `cruce_gestor.json`, `cruce_criticos_survey.json` are produced by a running cron pipeline but are dead data for the live dashboard (the "Gestión" tab that consumed them was deleted — see project memory `gestion-eliminada-tab-acciones`). A source can be "connected/healthy" yet completely unused.

## 4. What "read/connection status" and "errors" can be computed from today

- **Global pipeline outcome (real, exists)**: `deploy/refresh.sh` `trap report_status EXIT` uploads `data/_status.json = {ok, step, exit_code}` to Blob on every run, even on failure. This is **one status for the whole `dashboard-refresh` run**, not per-source. No frontend reads it yet.
- **Per-source freshness (real, exists)**: `meta.json.generated_at`/`source`/`row_count` and `reportes_meta.json.generated_at`/`source`/`row_count` are genuine per-source timestamps+row-counts already published, fetchable the same way `data.js` already does (Blob first, git fallback).
- **Per-source error detail (NOT available today)**: `refresh_data.py` has fail-soft `try/except` + `log.warning(...)` per external call (lines ~306-319, 653-656, 727-771, 1148-1151) but these only go to Railway stdout, which is effectively unreadable after the run (per memory `data-refresh-architecture`, and recent commit "Railway logs unreadable post-run"). Getting real per-source error state requires extending these except blocks to publish a structured `{source, ok, error}` array (e.g. extend `_status.json` or a new `_sources_status.json`).
- **`cruce-gestion` pipeline**: no equivalent status/heartbeat mechanism found in this repo (logic lives in `integracion_F1/`, not traced in depth — open question).
- **Live connection check**: only existing live-on-load pattern is `/api/reportados` (serverless proxy, 15-min CDN cache) — template if "Analista" should ping a source live instead of reading a snapshot.

## 5. Existing color-coded status UI to reuse

- **2-state (on/off)**: `--sticker-on: #37c871` / `--sticker-off: #ff6b6b` (dark), `#1f9d57`/`#d64545` (light) — `.sticker-pill-on/-off`, `.sticker-chip.is-on/-off` (`web/styles.css:1578-1700`).
- **3-state semáforo (verde/amarillo/rojo)**: `COLORS.status` in `web/js/utils.js:14-18` → `h:#22c55e` / `r2:#eab308` / `i2:#ef4444`, also `SEMAFORO_DE` in `charts.js:620-627`. Better match for "conectado / con advertencias / con errores" since it's the 3-way vocabulary already trained into this dashboard's UI.

## Approaches for "connection status" (for proposal phase)

| Approach | What it shows | Effort | Pros | Cons |
|---|---|---|---|---|
| A. Snapshot-only (read existing `meta.json`/`reportes_meta.json`/`_status.json`) | Freshness+row_count for 2 sources; global ok/fail for the whole run | Low, frontend-only | No backend risk, ships fast | Not truly per-source; orphaned sources + 2nd pipeline show nothing; "errors" is one global flag |
| B. Extend pipeline instrumentation (capture existing `log.warning` sites into a structured per-source status file) | Real per-source ok/error/last-run for `dashboard-refresh` | Medium (touches `refresh_data.py` + `refresh.sh`) | Answers the ask precisely | Touches production pipeline; `cruce-gestion` needs the same treatment to be complete, untraced |
| C. Live check-on-load (tab pings each source on open) | True live connectivity | Medium-High | Freshest signal | Slow; some sources (Sheet via service account, ArcGIS) have no cheap reachability probe; doesn't reflect pipeline success, only reachability |

Pragmatic default: hybrid of A + incrementally extending B, explicitly labeling orphaned/untraced sources as "not wired to any live tab" rather than faking a health check — decision for `sdd-propose`.

## Open questions for sdd-propose

1. "Estado de lectura y conexión": per-run snapshot (cheap, reuses `meta.json`/`_status.json`) or live-on-tab-open (new endpoints, some sources have no natural probe)?
2. Should the tab include both Railway pipelines' health, or only `dashboard-refresh` (which has `_status.json`) while flagging `cruce-gestion`/`integracion_F1` as untraced/unknown?
3. Should orphaned outputs (`asignaciones.json`, `cruce_gestor.json`, `cruce_criticos_survey.json`, `criticos_api.json`) be listed as sources — surfacing dead cron work is arguably the point of this tab?
4. Is extending `refresh_data.py`'s `except`/`log.warning` blocks into a structured status object in-scope for this change, or should v1 ship with the coarser global `_status.json` only, deferring per-source error detail?
5. (Side note, out of scope) `README.md`'s pipeline description is stale — no mention of Blob/dual-Railway-service architecture.

## Files read

`web/js/auth.js`, `web/js/main.js`, `web/js/usuarios.js`, `web/js/data.js`, `web/js/utils.js`, `web/index.html`, `web/styles.css`, `api/refresh.js`, `scripts/refresh_data.py`, `deploy/refresh.sh`, `deploy/blob_sync.py`, `deploy/entrypoint.sh`, `web/data/meta.json`, `web/data/reportes_meta.json`, `integracion_F1/asignar_f3.py`, `integracion_F1/cruce_gestor.py`, `README.md`, `.github/workflows/manual-refresh.yml`

## Risks

1. True per-source error status requires editing the production `refresh_data.py`/`refresh.sh` pipeline, not just the frontend.
2. The `cruce-gestion`/`integracion_F1` service has no traced status/heartbeat mechanism — its health story is unknown.
3. 4 of ~10 candidate "sources" are already orphaned (no live consumer) — needs clear labeling to avoid confusion.
4. `README.md` pipeline docs are stale relative to the actual Blob/dual-service architecture.
