# Analista (Data Sources) Tab Specification — Delta

## Purpose

A new admin-only "Analista" tab that lists every data source feeding the dashboard (name,
description, last read, row count where available, and a color-coded status), including a real
live-connectivity probe for the `atencionsismo` API, so an admin can localize a failure instead of
only observing "the dashboard looks wrong".

## Requirements

### Requirement: Tab visibility (admin-only, defense-in-depth)
The system MUST render `#view-analista` / `.view-tab[data-view="analista"]` only for admin
sessions and MUST hide both via CSS for `body:not([data-role="admin"])`, matching the existing
Stickers/Usuarios pattern. The `web/js/analista.js` module MUST independently guard with an
`isAdmin()` check before rendering or fetching, matching `usuarios.js`/`stickers.js`, so a
non-admin session that bypasses the CSS gate still cannot trigger the tab's own logic.

#### Scenario: Admin sees the tab
- GIVEN an authenticated admin session
- WHEN the dashboard UI loads
- THEN the "Analista" nav button and `#view-analista` section are visible when selected

#### Scenario: Non-admin does not see the tab
- GIVEN an authenticated non-admin (viewer or inspector) session
- WHEN the dashboard UI loads
- THEN the "Analista" nav button and `#view-analista` section are hidden by CSS

#### Scenario: JS guard blocks rendering even if CSS is bypassed
- GIVEN a non-admin session where the CSS hide rule does not apply (e.g. manually unhidden)
- WHEN `initAnalista(root, { getToken })` runs
- THEN the module's `isAdmin()` guard MUST prevent fetching or rendering source rows

### Requirement: Source list rendering
The system MUST render one row per known data source using the existing `sticker-list` /
`sticker-row` / `section-bar` building blocks (no new component), and MUST list every source named
in this change: Google Sheet EDAN-F3, Survey123 ArcGIS FeatureServer, Google Maps Geocoding API,
atencionsismo API (reportes), the global pipeline run, the four orphaned outputs
(`asignaciones.json`, `cruce_gestor.json`, `cruce_criticos_survey.json`, `criticos_api.json`), and
the Israel FeatureServer/Firestore `inspecciones_israel` source. Each row MUST show: **nombre**,
**descripción**, **última lectura**, **registros** (omitted when not available), and **estado**
(a semáforo-colored dot plus a Spanish label).

#### Scenario: All known sources are listed
- GIVEN the Analista tab has finished its initial fetch
- WHEN the source list renders
- THEN a row exists for each of the 10 named sources (EDAN-F3, Survey123, Geocoding, atencionsismo,
  global pipeline run, and the 4 orphaned outputs counted individually, plus Israel)

#### Scenario: Row with no row count omits the field
- GIVEN a source whose metadata provides no `row_count` (e.g. Geocoding cache)
- WHEN that row renders
- THEN the "registros" field is omitted rather than showing a placeholder like 0 or "N/A"

### Requirement: Status color and label derivation per source category
The system MUST derive each row's semáforo color and Spanish label from `COLORS.status` in
`web/js/utils.js` (verde `#22c55e`, amarillo `#eab308`, rojo `#ef4444`) using the following
category rules:

- **Snapshot-freshness sources** (EDAN-F3 via `meta.json`; Survey123 folded into the same
  `meta.json`; atencionsismo snapshot via `reportes_meta.json`): the system MUST read
  `generated_at`, `source`, and `row_count` and MUST label `conectado` (verde) when the snapshot
  is present and within a freshness threshold, and `desactualizado` (amarillo) when present but
  stale, or `sin metadata` (amarillo) when the meta file is missing/unreadable.
- **"Sin metadata" sources** (Google Maps Geocoding cache, `geocode_cache.json`): the system MUST
  label these `sin metadata` (amarillo) and MUST NOT fabricate a last-read timestamp or row count
  that the cache does not provide.
- **Whole-run status source** (global pipeline run via `deploy/refresh.sh`'s `_status.json`): the
  system MUST label `conectado` (verde) when `_status.json.ok === true`, and `con errores` (rojo)
  when `ok === false`, and MUST present this explicitly as whole-run granularity, not attributable
  to a specific source.
- **Orphaned / "sin consumidor" sources** (`asignaciones.json`, `cruce_gestor.json`,
  `cruce_criticos_survey.json`, `criticos_api.json`): the system MUST label these `sin consumidor`
  (amarillo) regardless of freshness, and MUST derive última lectura only from file presence and
  last-modified (Blob or git-tracked copy) — no other health signal.
- **Live-probe source** (atencionsismo API connectivity): the system MUST set `conectado` (verde)
  when `GET /api/source-status` responds and its body is `{ ok: true }`, and MUST set `con errores`
  (rojo) when it responds and its body is `{ ok: false }` — this is a genuine signal that the
  upstream API itself is unreachable, independent of the snapshot freshness state described above.
  If the request to `/api/source-status` itself fails (network error, non-2xx transport failure
  such as an expired session returning 401/403 to the *frontend* caller) before any endpoint body
  is received, the system MUST NOT report `con errores`; it MUST instead label the row `sin datos`
  (amarillo), since a failure to reach the analista endpoint is not evidence that the atencionsismo
  API itself is down.
- **Any source, transport-failure override**: regardless of category, if the client-side fetch for
  a source's underlying file/endpoint fails at the transport level (network error, Blob
  unreachable) before any response body is available, the system MUST label that row `sin datos`
  (amarillo) rather than `con errores` (rojo). `rojo` is reserved exclusively for an explicit,
  received error signal (`_status.json.ok === false`, or a received `{ ok: false }` probe body) —
  never inferred from the analista tab's own inability to fetch.

#### Scenarios (showing status color derivation for each category)
All scenario implementations verified in verify-report.md, spec-vs-implementation section.

### Requirement: Live-probe endpoint `api/source-status.js`
The system MUST expose a new serverless endpoint `api/source-status.js` that:
- MUST verify the caller's Firebase ID token and MUST require `role === 'admin'` using the same
  `roleFromClaims()` precedence as `api/usuarios.js` / `api/refresh.js`.
- MUST reject requests with a missing or invalid token with HTTP 401.
- MUST reject requests from an authenticated non-admin caller with HTTP 403.
- MUST, for an authorized admin request, build Basic auth from `VISITADOS_API_PASS` and invoke
  `probeApi()` (reused from `api/reportados.js`) against
  `https://atencionsismo.cali.gov.co/api/informe/json`.
- MUST return a JSON body of the shape
  `{ ok: true|false, status: 'conectado'|'con errores', detail, checked_at }`.
- MUST set `Cache-Control: private, no-store` on every response (both `ok: true` and `ok: false`
  branches). A shared/CDN-cacheable response (`public, s-maxage=...`) MUST NOT be used on this
  endpoint: because the auth gate runs inside the function body, a shared cache entry keyed only by
  URL+method (Vercel's default for Node.js Serverless Functions) would let one admin's cached 200
  response be served to a later unauthenticated or non-admin caller within the cache window,
  bypassing the 401/403 checks above. This endpoint intentionally accepts re-hitting the upstream
  API on every tab open/refresh rather than risk a shared-cache auth bypass.

#### Scenario: Response is never shared-cached
- GIVEN any probe response (`ok: true` or `ok: false`)
- WHEN the response headers are inspected
- THEN `Cache-Control` is `private, no-store` and does NOT include `public` or `s-maxage`, so no
  shared/CDN cache can serve this admin-gated response to a different caller

### Requirement: Refresh behavior (lazy-init + manual refresh)
The system MUST re-fetch all source data (including re-invoking the live probe) every time the
Analista tab is opened, following the same lazy-init-on-tab-open pattern already used by
`initUsuarios`/`initStickers` in `main.js`. The system MUST also render an "Actualizar" button in
the tab's header (`sticker-page-head`) that re-triggers the same fetch, including the live probe,
on demand. The system MUST NOT implement polling or automatic background refresh — this is a
diagnostic, on-demand view.

#### Scenario: Reopening the tab re-fetches
- GIVEN the admin has already opened the Analista tab once in the session
- WHEN the admin navigates away and reopens the Analista tab
- THEN a fresh fetch of all sources (including the live probe) is triggered

#### Scenario: Manual Actualizar button re-triggers fetch
- GIVEN the Analista tab is open and rendered
- WHEN the admin clicks "Actualizar"
- THEN all source rows re-fetch, including a fresh call to `api/source-status.js`

#### Scenario: No background polling occurs
- GIVEN the Analista tab is open and idle with no user interaction
- WHEN time passes without a tab switch or "Actualizar" click
- THEN no additional fetch or probe request is made

### Requirement: Non-goals are explicit exclusions, not gaps
The following are explicitly OUT OF SCOPE for this change and MUST NOT be treated as missing
requirements during verification:
- Modifying `refresh_data.py`'s error handling or per-source instrumentation
- Building monitoring for `integracion_F1` / `cruce-gestion` pipeline
- Fixing stale `README.md` pipeline documentation
- Adding structured per-source `{source, ok, error}` arrays to `_status.json`
- Adding new reachability probes for Sheet/Survey123/Geocoding sources
</content>
