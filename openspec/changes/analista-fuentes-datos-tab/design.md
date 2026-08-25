# Design: analista-fuentes-datos-tab

Technical design for the admin-only **Analista** tab: a read-only source-health
inventory. Scope is fixed by the approved proposal; this document is the HOW at
architecture level, not the task list.

## Architecture approach

Same shape as the existing admin tabs (`stickers`, `usuarios`): a lazy-init ESM
module rendered into a pre-existing `<section>` on tab open, gated by the CSS
admin selector plus a defense-in-depth `isAdmin()` guard, refetching its data on
every open. The one net-new server surface is a single read-only serverless
endpoint for the live atencionsismo probe. No framework, no bundler, no new
dependency, no build step — consistent with `openspec/config.yaml`.

The core design insight: the sources are **heterogeneous in signal quality**
(rich meta vs. file-presence-only vs. live probe), so the module normalizes each
into one uniform "source row" shape (ADR-1) and renders them through the existing
`sticker-list`/`sticker-row` building blocks. All normalization is client-side;
the only thing that *must* be server-side is the atencionsismo probe, because the
API needs HTTP Basic auth the browser cannot hold.

Data flow:

```
tab open ──▶ initAnalista(root,{getToken})
              │
              ├─ fetchData('meta.json')          ┐ Blob→deploy fallback (data.js)
              ├─ fetchData('reportes_meta.json')  │
              ├─ fetchData('_status.json')        │ parallel, each fault-isolated
              ├─ fetchData('geocode/…') [HEAD]    │
              ├─ fetchData('asignaciones.json')…  ┘ (orphans, presence + last-mod)
              └─ GET /api/source-status (Bearer)  ── live probe, admin-gated
              │
              ▼
        normalize each → SourceRow[]  ──▶ render sticker-list rows
```

## 1. File-level plan

| File | Change | Notes |
|---|---|---|
| `web/index.html` | ADD one `<button class="view-tab" data-view="analista">Analista</button>` in the tab bar next to Usuarios, and one `<section id="view-analista" data-view-panel="analista" hidden></section>` next to `#view-usuarios`. | Mirrors the Stickers/Usuarios markup exactly. |
| `web/styles.css` | ADD `body:not([data-role="admin"]) .view-tab[data-view="analista"]` to the existing admin-gate selector list (currently lines 1561-1563). One selector added to the comma list; the `{ display: none !important; }` block is reused. | No new component CSS — reuses `sticker-list`/`sticker-row`/`section-bar`/`sticker-page-head`. One optional dot class if needed (see ADR-2). |
| `web/js/main.js` | ADD `import { initAnalista } from './analista.js';` and one lazy-init branch in `switchView()`: `if (view === 'analista') initAnalista(document.getElementById('view-analista'), { getToken: getIdToken });` — placed beside the `usuarios` branch. | Same lifecycle as `initUsuarios`. |
| `web/js/analista.js` | NEW. `initAnalista(root, { getToken })`. Fetch orchestration + normalization + render. | ADR-1, section 5. |
| `api/source-status.js` | NEW. Admin-gated GET endpoint running the live atencionsismo probe. | ADR-3, section 4. |
| `api/source-status.test.js` | NEW. `node api/source-status.test.js` assert-based self-check. | Section 7. |
| `api/reportados.js` | EDIT (one line): `module.exports.probeApi = probeApi;` at the bottom so `source-status.js` can reuse it. | ADR-4. Only additive; the default export (the KPI handler) is untouched. |

Nothing is modified destructively. Revert = `git revert` of the one commit
(proposal's rollback plan).

## 2. Normalized source-row data model (ADR-1)

Every source, regardless of how thin or rich its underlying signal is, is mapped
to this shape so `render()` stays uniform:

```js
/**
 * @typedef {Object} SourceRow
 * @property {string}  id            stable key, e.g. 'edan-f3', 'atencionsismo'
 * @property {string}  nombre        display name (Spanish UI copy)
 * @property {string}  descripcion   one line: what it is / what it feeds (Spanish)
 * @property {?string} ultima_lectura ISO timestamp, or null when no signal exists
 * @property {?number} registros     row_count when the meta provides it, else null
 * @property {'verde'|'amarillo'|'rojo'} estado_color
 * @property {string}  estado_label  Spanish semáforo label (see below)
 * @property {?string} detalle       optional extra note (e.g. probe HTTP detail,
 *                                    'sin consumidor', 'sin metadata')
 */
```

`estado_color` maps to the existing palette via `COLORS.status` in `utils.js`
(the same 3-state vocabulary trained into the dashboard):
`verde → #22c55e (COLORS.status.h)`, `amarillo → #eab308 (COLORS.status.r2)`,
`rojo → #ef4444 (COLORS.status.i2)`. No new color tokens.

`estado_label` vocabulary (Spanish UI copy, verbatim from the proposal):
- verde: `conectado`
- amarillo: `sin metadata` / `sin consumidor` / `desactualizado`
- rojo: `con errores`

`ultima_lectura` renders through `formatTs()` (already in `utils.js`); `null`
renders as `sin metadata` (amarillo). `registros === null` omits the count.

## 3. Per-source data-fetching plan

All static reads go through `fetchData(name)` (exported by `web/js/data.js`),
which already implements the Blob-first → deployed-copy fallback against
`BLOB_DATA_BASE = https://xsr0euqif1ryb8id.public.blob.vercel-storage.com/data`.
Reused, not re-implemented (ladder rung 2). Each fetch is fault-isolated
(section 5): a failed *fetch* is a transport error on the analista side, never a
`rojo` source status.

| # | Source (nombre) | Reads from | Maps to SourceRow | Color rule |
|---|---|---|---|---|
| 1 | Google Sheet EDAN-F3 (`tabla_normalizada`) | `meta.json` (`generated_at`, `row_count`, `source`) | `ultima_lectura=generated_at`, `registros=row_count` | staleness rule (below) |
| 2 | Survey123 ArcGIS FeatureServer | `meta.json` (same object — Survey123 is folded into `inspections.json`; `source: "survey123"`) | same freshness signal as #1; `detalle` = "sin error de sub-fuente: solo hay meta de la corrida completa" | staleness rule |
| 3 | Google Maps Geocoding API | `geocode/geocode_cache.json` (Blob path `data/geocode/geocode_cache.json`) via a HEAD/GET presence check only — the cache has no freshness metadata | `ultima_lectura=null`, `registros=null`, `detalle="caché interna, sin metadata"` | always **amarillo `sin metadata`** |
| 4 | API atención sismo (reportes) | `reportes_meta.json` (`generated_at`, `row_count`) for the **freshness snapshot**, PLUS `GET /api/source-status` for the **live** status | `ultima_lectura=generated_at`, `registros=row_count`; color driven by the live probe result, `detalle`=probe `detail` | probe `ok:true` → verde `conectado`; `ok:false` → **rojo `con errores`** (overrides snapshot) |
| 5 | Corrida global del pipeline | `_status.json` (`{ok, step, exit_code}`) from Blob; timestamp from the response `last-modified`/`date` header (the JSON carries no timestamp) | `ultima_lectura`=Blob last-modified, `registros=null`, `detalle`="corrida completa (no por sub-fuente); paso: <step>" | `ok===false` → **rojo `con errores`**; `ok===true` but stale by the rule → amarillo `desactualizado`; else verde `conectado` |
| 6 | Orphaned output: `asignaciones.json` | via `fetchData`; body is a top-level object `{generated_at, pendientes, visitados, ede_hechas, ede_pendientes}` | `ultima_lectura=generated_at` (field, not Blob header — already present and more precise); `registros = pendientes.length + visitados.length` | present → **amarillo `sin consumidor`**; fetch 404/absent → amarillo `detalle="ausente"` |
| 7 | Orphaned output: `cruce_gestor.json` | via `fetchData`; body is a top-level object `{generated_at, resumen, zonas, records}` | `ultima_lectura=generated_at`; `registros = records.length` | same as #6 |
| 8 | Orphaned output: `cruce_criticos_survey.json` | via `fetchData`; body is a top-level object `{generated_at, match_radio_m, resumen, zonas, records}` | `ultima_lectura=generated_at`; `registros = records.length` | same as #6 |
| 9 | Orphaned output: `criticos_api.json` | via `fetchData`; body is a top-level JSON **array** (no `generated_at` field — this file is untracked/ad-hoc, not part of any cron) | `ultima_lectura`=Blob `last-modified` header (only signal available); `registros = array.length` | same as #6 |
| 10 | FeatureServer Israel + Firestore `inspecciones_israel` | live client-side read via `fetchIsraelRecords()` from `web/js/israel-source.js` (returns `[]` on any failure, never throws) | `registros`=array length; `ultima_lectura=null` (no per-run meta) | non-empty result → verde `conectado`; empty (`[]`) → amarillo `sin metadata` (cannot distinguish "empty" from "unreachable" — labeled honestly, not rojo) |

### Staleness threshold (proposed — none defined in proposal/spec)

The proposal names the amarillo `desactualizado` state but defines no numeric
threshold, so this design proposes one:

> **A snapshot is `desactualizado` (amarillo) when its `generated_at` is older
> than 45 minutes.** Fresh (within 45 min) → verde.

Rationale: the dashboard-refresh cron runs `*/15` (confirmed by
`main.js`'s auto-refresh comment "alineado con el cron de Railway (*/15)"). One
missed run is normal jitter; flagging at 45 min (three consecutive missed
publishes) surfaces a real stall without false alarms on ordinary timing drift.
Encoded as one constant `STALE_MS = 45 * 60 * 1000` so it is a single tuning
knob. Staleness only ever produces amarillo — `rojo` is reserved for explicit
error signals (probe failure, `_status.ok===false`), never inferred from age.

## 4. `api/source-status.js` implementation plan (ADR-3)

Read-only GET endpoint. Method guard `GET` (405 otherwise), matching
`reportados.js`.

**Role verification** — identical to `api/usuarios.js`:
`const { verifyFirebaseToken, roleFromClaims } = require('./refresh.js');`
Read `Authorization: Bearer <token>`; missing → `401`; `verifyFirebaseToken`
throws → `401`; `roleFromClaims(claims) !== 'admin'` → `403`. Same
`FIREBASE_PROJECT_ID` default (`sismo-agosto-sgred`). No new collections, no
Firestore reads (see section 6).

**Probe reuse (ADR-4)** — `const { probeApi } = require('./reportados.js');`.
`probeApi` is exported from `reportados.js` by adding one line
`module.exports.probeApi = probeApi;` (the module currently exports only the
default handler). Reuse over duplication so the "alive vs. down" status-code
logic (413/504 = alive, everything else = down) has a **single source of truth**
shared with the KPI proxy; duplicating it risks the two endpoints disagreeing
about what "reachable" means if the API's acceptable-status set ever changes.
Requiring `reportados.js` only executes top-level `const`/function definitions
(no side effects), so it is safe.

**Basic auth** — same construction as `reportados.js`:
```js
const pass = (process.env.VISITADOS_API_PASS || '').trim();
if (!pass) return res.status(200).json({ ok:false, status:'con errores',
  detail:'VISITADOS_API_PASS no está configurado', checked_at:new Date().toISOString() });
const user = (process.env.VISITADOS_API_USER || '').trim() || 'juanp.gzmz@gmail.com';
const auth = Buffer.from(`${user}:${pass}`).toString('base64');
```
Note: a missing env var returns `200 { ok:false }`, **not** a 5xx — the endpoint
succeeded in reporting that the source is down (the semantically correct
"con errores"). This matches proposal risk #5.

**Caching** — `res.setHeader('Cache-Control', 'private, no-store')`. An earlier
version of this design proposed `public, s-maxage=60` to spare the upstream API
on repeated tab opens, but post-implementation review (RELIABILITY-001)
corroborated that a shared/CDN-cacheable response on this admin-gated endpoint
would let Vercel's shared Edge Network serve one admin's cached 200 to a later
unauthenticated/non-admin caller within the cache window — a real auth bypass,
not a theoretical one (confirmed against this same pattern already in
production on `api/reportados.js`, which is safe there only because that
endpoint has no auth gate at all). Fix: no shared caching on this endpoint;
every tab open/refresh re-hits the upstream API, which is acceptable for a
low-traffic admin diagnostic view.

**Response shape** (exact):
```js
// success (reachable)
{ ok: true,  status: 'conectado',   detail: null,               checked_at: '<ISO>' }
// unreachable / bad credentials / missing env
{ ok: false, status: 'con errores', detail: 'API no disponible (HTTP 401)', checked_at: '<ISO>' }
```
`probeApi` throws on failure (with `err.status` and a Spanish message); the
handler catches it and returns `ok:false` with `detail = err.message`. HTTP
status of the endpoint itself stays `200` for any *reached* answer (including
"API down") and only 401/403 for auth failures — the frontend distinguishes an
endpoint-transport failure (network) from a source being down via `res.ok`.

## 5. `web/js/analista.js` module plan (ADR-1)

`initAnalista(root, { getToken })`, mirroring `initUsuarios`'s shape.

- **Defense-in-depth guard**: `import { isAdmin } from './auth.js';` — if
  `!isAdmin()` render an empty/permission notice and return (matches
  `usuarios.js`/`stickers.js`; the CSS selector already hides the tab).
- **Shell**: `sticker-page-head` with title `Analista` (Spanish lead: e.g.
  "Salud de las fuentes de datos que alimentan el tablero.") and one
  `<button class="btn-primary" id="analista-refresh">Actualizar</button>`, plus a
  `sticker-list` container.
- **Fetch orchestration** (`reload()`):
  - `Promise.allSettled([...])` over the static reads (`fetchData` calls #1-9)
    **and** the one live call `callApi(getToken)` → `/api/source-status`.
    `allSettled`, not `all`, so one failing fetch never aborts the others.
  - Each source builds its `SourceRow` inside a small per-source try/catch (or by
    inspecting its settled result). A **rejected fetch** (network error, Blob
    down) yields a row with `estado_color:'amarillo'`, `estado_label:'sin datos'`,
    `detalle:'no se pudo leer la fuente'` — explicitly **not** `rojo`, because a
    fetch failure on the analista side is a transport problem, not evidence the
    source itself errored. `rojo` is reserved for real error signals
    (`_status.ok===false`, live probe `ok:false`).
  - The live endpoint call reuses the `callApi` pattern from `usuarios.js`
    (GET with `Authorization: Bearer <token>`); a non-ok HTTP response (401/403/
    network) is treated as an analista-side error for the atencionsismo row
    (amarillo `sin datos` + detail), distinct from the endpoint returning
    `{ok:false}` (which is a genuine source `rojo`).
- **Render**: a pure `renderRows(rows)` producing `<li class="sticker-row">`
  entries — name + description in `sticker-identity`, a semáforo dot
  (`<span>` with inline `background:<color>`) + `estado_label` where usuarios puts
  its pill, and `ultima_lectura`/`registros`/`detalle` in `sticker-meta`. All
  interpolated values pass through `escapeHtml`.
- **Wiring**: `#analista-refresh` click → `reload()` with a cache-bust param on
  the `/api/source-status` call so the button re-runs the live probe on demand.
  No polling / auto-refresh (diagnostic view, not a monitor).
- **Lifecycle**: re-runs on every tab open (main.js calls `initAnalista` each
  time), same as `initUsuarios`/`initStickers`.

## 6. Firebase / Firestore impact (rules.design requirement)

**No Firestore schema changes. No Firestore security-rules changes. No new
collections or documents.**

- `api/source-status.js` only *verifies a Firebase ID token* to read the role
  claim (`verifyFirebaseToken` + `roleFromClaims`), exactly as `api/usuarios.js`
  already does. It performs zero Firestore reads or writes.
- The Israel source (#10) reads the **existing** `inspecciones_israel` collection
  via the **existing** `fetchIsraelRecords()` with its existing public-read rule —
  no rule change, no new access path.
- All other sources are already-public Blob/JSON reads.

## 7. Testing approach (`api/source-status.test.js`)

Matches the repo convention (`openspec/config.yaml`): plain
`node api/source-status.test.js`, assert-based self-check, no framework, no
fixtures. Mirrors `api/usuarios.test.js`'s style of exercising exported pure
pieces plus a gated live path.

Assertions (per the proposal's Testability section):

1. **Missing token → 401.** Invoke the handler with a mock `req` (no
   `Authorization` header) and a mock `res` capturing `status()`/`json()`;
   assert `401`.
2. **Invalid / non-admin token → 401/403.** Stub `verifyFirebaseToken` /
   `roleFromClaims` (or pass a token that resolves to a non-admin role) and
   assert `403`; a throwing verify asserts `401`.
3. **Valid admin probe against the real API → `ok:true`** — gated behind an env
   var (`RUN_LIVE_PROBE` + `VISITADOS_API_PASS` present). When the gate is off,
   the test prints a skip line and passes, so it runs in credential-less
   environments. When on, it asserts the reachable path returns
   `{ ok:true, status:'conectado' }` — proving the green reflects real
   connectivity, never a hardcoded value.
4. **Forced bad credentials → `ok:false`.** With a deliberately wrong
   `VISITADOS_API_PASS`, assert the response is `{ ok:false, status:'con errores' }`
   with a non-null `detail` — proving `rojo` is a real signal.

To keep 1-2 testable without a live network or the Firebase Admin SDK, the
handler's auth preamble is written so the token-verification dependency can be
injected/stubbed (same seam `usuarios.js` exposes for its self-check). The
`probeApi` reuse (ADR-4) means the live-probe logic itself is already covered by
its behavior in `reportados.js`; this test focuses on the endpoint's auth gate
and its ok/not-ok mapping.

## ADR summary

- **ADR-1 — Uniform SourceRow + client-side normalization.** One shape for
  heterogeneous signals, rendered via existing `sticker-*` blocks. Alternative
  (per-source bespoke rendering) rejected: more code, no benefit, breaks the
  established tab pattern.
- **ADR-2 — Reuse `sticker-list`/`section-bar`; no new component.** Only a
  semáforo dot via inline `background` from `COLORS.status`. Alternative (new CSS
  component) rejected as unnecessary (proposal §In-scope 2).
- **ADR-3 — One admin-gated read-only endpoint for the live probe only.** Other
  sources have no cheap/safe reachability probe, so a live check is added only
  where one already exists and is cheap (atencionsismo). Alternatives
  (snapshot-only; full `/api/reportados` count; generic live-check-for-all)
  rejected in the proposal's "Why this over the alternatives".
- **ADR-4 — Export and reuse `probeApi` from `reportados.js`.** One-line additive
  export; single source of truth for "alive vs down". Alternative (duplicate the
  probe) rejected: drift risk.
- **ADR-5 — 45-min staleness threshold, amarillo-only.** Derived from the `*/15`
  cron cadence (three missed runs). `rojo` never inferred from age. Single
  `STALE_MS` constant as the tuning knob.
- **ADR-6 — Fetch failure ≠ source error.** A failed analista-side fetch renders
  amarillo `sin datos`, never `rojo`; `rojo` is reserved for genuine upstream
  error signals. Prevents a transient Blob/network hiccup from being misread as
  an API outage.
</content>
</invoke>
