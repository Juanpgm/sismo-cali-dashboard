# Proposal: analista-fuentes-datos-tab

## Intent

### Problem

Administrators have no single place to see the health of the data sources that
feed the dashboard. Today the state of each source is scattered and effectively
invisible after a run:

- `refresh_data.py` logs per-source `warning`s only to Railway stdout, which is
  unreadable after the run completes (see project memory
  `data-refresh-architecture` and commit "Railway logs unreadable post-run").
- Freshness signals (`meta.json`, `reportes_meta.json`) and the global run
  outcome (`_status.json`) exist in Blob but no UI surfaces them.
- The `atencionsismo` API connection is only exercised implicitly by the
  "Reportados" KPI; there is no explicit "is this API reachable right now?"
  signal an admin can trust.
- Several outputs (`asignaciones.json`, `cruce_gestor.json`,
  `cruce_criticos_survey.json`, `criticos_api.json`) are still produced by cron
  but consumed by no live tab — dead data an admin cannot currently discover.

### Why now

The dashboard now depends on two Railway pipelines, Vercel Blob, a live API
proxy, and client-side FeatureServer fetches. When something upstream breaks,
the only symptom is stale or missing dashboard data, with no way to localize the
failure. A source-inventory view turns "the dashboard looks wrong" into "source
X is stale / unreachable / orphaned".

### Success looks like

An administrator opens the **Analista** tab and sees, in one list, every data
source feeding the dashboard, each with a name, a short description, its last
read timestamp and row count (where available), and a color-coded status
(verde / amarillo / rojo) with a short Spanish label. For the `atencionsismo`
API the status reflects a *real, verified* live probe, not a decorative
snapshot. Orphaned outputs are visibly labeled as having no consumer.

## Scope

### In scope

1. A new dashboard tab labeled exactly **"Analista"**, visible only to
   Administrators, reusing the existing admin-gate pattern:
   - Add `.view-tab[data-view="analista"]` to the
     `body:not([data-role="admin"])` selector list in `web/styles.css`.
   - `<button class="view-tab" data-view="analista">` + `<section
     id="view-analista" data-view-panel="analista" hidden>` in
     `web/index.html`, mirroring Stickers/Usuarios.
   - Lazy-init a new `web/js/analista.js` (`initAnalista(root, { getToken })`)
     from `switchView()` in `web/js/main.js`, same shape as `initUsuarios`.
   - Defense-in-depth `isAdmin()` guard in the module, matching
     `usuarios.js`/`stickers.js`.
2. A source list rendered with existing generic building blocks
   (`sticker-list`/`sticker-row`, `section-bar`) — no new component. Each row:
   source **name**, **description**, **last read** timestamp, **row count**
   (where available), **status color**, **status label**.
3. Status colors from the existing 3-state semáforo palette
   (`COLORS.status` in `web/js/utils.js`: verde `#22c55e` / amarillo `#eab308`
   / rojo `#ef4444`), the same vocabulary already trained into this dashboard
   for `criterio_habitabilidad`.
4. Sources shown in v1 (status derived from what ALREADY exists today):
   - **Google Sheet EDAN-F3** (`tabla_normalizada`) → freshness from
     `meta.json` (`generated_at`, `source`, `row_count`).
   - **Survey123 ArcGIS FeatureServer** (photo-EXIF / geometry cross-check) →
     folded into `inspections.json`; same `meta.json` freshness signal, with an
     explicit note that no distinct sub-source error is available (only the
     whole-run meta).
   - **Google Maps Geocoding API** → `geocode_cache.json`; internal cache only,
     no dedicated freshness metadata. Flagged as "sin metadata".
   - **atencionsismo API (reportes)** → `reportes_meta.json` snapshot
     (`generated_at`, `source`, `row_count`) PLUS a real live-connectivity check
     (see "atencionsismo live-check approach" below).
   - **Global pipeline run** → `deploy/refresh.sh`'s `_status.json`
     (`ok`, `step`, `exit_code`) from Blob, surfaced as the "última corrida"
     signal, explicitly labeled whole-run granularity, not per-source.
   - **Orphaned outputs** (`asignaciones.json`, `cruce_gestor.json`,
     `cruce_criticos_survey.json`, `criticos_api.json`) → listed and clearly
     labeled "sin consumidor" (no tab reads them). Freshness only from cheap
     signals already available (file presence + last-modified from Blob or the
     git-tracked copy). No new instrumentation of the `integracion_F1` pipeline.
   - **Israel FeatureServer + Firestore `inspecciones_israel`** → its own
     source with its own connection story (live client-side fetch via
     `web/js/israel-source.js`, independent of the Railway pipelines).
5. One new admin-gated serverless endpoint for the atencionsismo live probe
   (see below). It re-verifies `role === 'admin'` the same way `api/usuarios.js`
   does.

### Out of scope (non-goals, verbatim)

- Modifying `refresh_data.py`'s error handling / instrumentation for other
  sources beyond what's needed for the atencionsismo API check.
- Building any monitoring for the `integracion_F1` / `cruce-gestion` pipeline.
- Fixing the stale `README.md` pipeline docs.

Additionally NOT in this change: adding a structured per-source
`{source, ok, error}` array to `_status.json` (deferred; v1 uses the coarser
global flag), and any new probe for the Google Sheet / Survey123 / Geocoding
sources (no cheap reachability probe exists; they show snapshot freshness only).

## User-facing behavior

The **Analista** tab renders a header (`sticker-page-head`) with a title and a
manual "Actualizar" button, and a `sticker-list` of source rows. Each row shows:

- **Nombre** — e.g. "Google Sheet EDAN-F3", "API atención sismo", "FeatureServer
  Israel".
- **Descripción** — one line: what the source is and what it feeds.
- **Última lectura** — timestamp from the source's meta (`generated_at` /
  Blob last-modified), or "sin metadata" when none exists.
- **Registros** — `row_count` where the meta provides it; omitted otherwise.
- **Estado** — a semáforo dot + Spanish label:
  - verde: `conectado` (fresh snapshot within threshold, or live probe OK).
  - amarillo: `sin metadata` / `sin consumidor` / `desactualizado` (reachable
    but weak or stale signal, or orphaned output).
  - rojo: `con errores` (global `_status.json.ok === false`, or live probe
    failed).

**Refresh trigger:** the view re-fetches on every tab open (lazy-init on first
open, re-run on subsequent opens), consistent with how `initUsuarios`/
`initStickers` already refetch on tab open per `main.js`'s lazy-init pattern,
PLUS an explicit "Actualizar" button in the header. Rationale: snapshot metas
(`meta.json`, `reportes_meta.json`, `_status.json`) are cheap Blob/JSON reads
already done by `data.js`, and the manual button lets an admin re-run the
atencionsismo live probe on demand without reloading the page. No polling/auto-
refresh — this is a diagnostic view, not a live monitor.

## atencionsismo live-check approach

**Chosen approach: reuse the existing `probeApi()` from `api/reportados.js` in a
new lightweight admin-gated endpoint `api/source-status.js`.**

`api/reportados.js` already contains `probeApi(auth)` — a single ~200ms request
for a 1-minute window against `https://atencionsismo.cali.gov.co/api/informe/json`
that returns fast and distinguishes "alive" (200 / 413 / 504) from "down"
(401 credential error, 503 maintenance, no response). This is exactly a genuine
live-connectivity check.

`api/source-status.js` will:
1. Verify the caller's Firebase token and `role === 'admin'` (same
   `roleFromClaims()` precedence as `api/usuarios.js` / `api/refresh.js`).
2. Build Basic auth from `VISITADOS_API_PASS` (already required in Vercel).
3. Run the one-minute probe and return
   `{ ok: true|false, status: 'conectado'|'con errores', detail, checked_at }`.
4. Set a short CDN cache (`s-maxage=60`) so repeated tab opens don't hammer the
   API, while the "Actualizar" button (fresh request) still reflects reality
   within a minute.

**Why this over the alternatives** (from exploration §"Approaches"):

- **vs. A. Snapshot-only** (`reportes_meta.json` freshness): a snapshot proves
  the pipeline ran, not that the API is reachable *now* — the user explicitly
  wants a real, verified connection status, not a decorative green derived from
  a stale file. We keep the snapshot as the freshness signal but add the live
  probe for the actual connection status.
- **vs. reusing the full `/api/reportados` count**: that walks the entire date
  range (~150s) and can 502 on transient failures — far too heavy and slow for
  a status dot. The `probeApi` sub-check is the cheapest honest signal.
- **vs. C. generic live-check-on-load for all sources**: the Sheet (service
  account), ArcGIS FeatureServer, and Geocoding cache have no cheap, safe
  reachability probe, so a live check is only justified where one already exists
  and is cheap — the atencionsismo API.

**Testability (the user's requirement that this genuinely works):** because the
probe hits the real API with real credentials, the endpoint can be exercised
directly — a `node api/source-status.test.js` self-check (matching the repo's
`node <file>.test.js` convention) asserts that (a) a missing/invalid token or
non-admin role returns 401/403, (b) a valid admin probe against the real API
returns `ok: true` when reachable, and (c) a forced-bad-credential path returns
`ok: false` — proving the green reflects real connectivity, never a hardcoded
value. The self-check that needs live credentials is gated behind an env var so
it is skippable in environments without `VISITADOS_API_PASS`.

## Rollback plan

This change is frontend-only plus one small, additive serverless endpoint;
nothing existing is modified destructively.

- **Revert path:** a single `git revert` of the change commit removes the tab
  and endpoint. The additions are self-contained: one nav button + one section
  in `index.html`, one selector line in `styles.css`, one lazy-init branch in
  `main.js`, the new `web/js/analista.js`, and the new `api/source-status.js`.
- **No data migration, no schema change, no Firestore rules change** — the tab
  only reads already-public Blob/JSON and calls one new read-only endpoint.
- **Partial disable:** if the live probe misbehaves in production, removing the
  single `switchView()` branch (or hiding the tab via the CSS selector) disables
  the whole feature without touching any pipeline. The `api/source-status.js`
  endpoint has no side effects and can be left deployed or deleted independently.
- **Manual verification** (this project has no CI test gate): after deploy, log
  in as an admin and confirm (1) the tab appears only for admins, (2) each
  source row shows a plausible timestamp/status, (3) the atencionsismo row goes
  rojo when `VISITADOS_API_PASS` is unset/invalid and verde when valid, and
  (4) a non-admin session does not see the tab and the endpoint returns 403.

## Risks (carried from exploration)

1. **`geocode_cache.json` has no freshness metadata** — the Geocoding source can
   only show "sin metadata" (amarillo), not a real last-read/row-count. Accepted:
   labeled honestly rather than faking a signal.
2. **Orphaned sources have only weak freshness signals** — `asignaciones.json`,
   `cruce_gestor.json`, `cruce_criticos_survey.json`, `criticos_api.json` are
   produced by the untraced `integracion_F1` cron; we can show file presence +
   last-modified only, and label them "sin consumidor". No health/heartbeat is
   built for that pipeline (out of scope).
3. **`_status.json` is whole-run granularity** — a rojo global flag does not tell
   which sub-source failed. v1 accepts this and labels it clearly; per-source
   error detail is deferred (would require editing `refresh_data.py`, out of
   scope).
4. **Survey123 sub-source error is invisible** — folded into `inspections.json`,
   so only the whole-run `meta.json` freshness is available; no distinct
   Survey123 error state in v1.
5. **Live probe depends on `VISITADOS_API_PASS` in Vercel** — if the env var is
   missing the atencionsismo row shows rojo/"con errores", which is correct
   behavior but worth noting so it isn't misread as an API outage.
