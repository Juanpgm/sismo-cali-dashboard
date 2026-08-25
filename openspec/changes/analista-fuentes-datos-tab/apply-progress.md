# Apply progress: analista-fuentes-datos-tab

Status: Phases 1-3 implemented and checked off in `tasks.md`. Phase 4 (manual
post-deploy verification) is intentionally left unchecked — it requires a real
admin browser session and cannot be performed from this environment.

## What was implemented

### Phase 1 — backend live-probe endpoint (test-first, strict TDD)
- `api/reportados.js`: one additive line, `module.exports.probeApi = probeApi;`
  (task 1.1).
- `api/source-status.test.js`: written FIRST (task 1.2), confirmed RED by
  running `node api/source-status.test.js` against the not-yet-existing
  module (task 1.3 — `Cannot find module './source-status.js'`).
- `api/source-status.js`: implemented per design §4/ADR-3/ADR-4 (task 1.4).
- Re-ran the test, confirmed GREEN (task 1.5). Final verbatim output:
  ```
  source-status.test.js: skipping RUN_LIVE_PROBE real-network assertion (set RUN_LIVE_PROBE=1 and VISITADOS_API_PASS to run it)
  source-status.test.js OK
  ```
  The `RUN_LIVE_PROBE`-gated real-network assertion (design §7 item 5) was not
  exercised — no `VISITADOS_API_PASS` is available in this environment, and it
  is designed to self-skip, which it did (printed the skip line, exit 0).
  `node api/usuarios.test.js` was also re-run after editing `reportados.js` to
  confirm no regression (still `usuarios.test.js OK`).

### Phase 2 — frontend scaffolding
- `web/index.html`: added the `Analista` nav button next to `Usuarios`, and
  `<section id="view-analista" data-view-panel="analista" hidden>` next to
  `#view-usuarios` (task 2.1).
- `web/styles.css`: added `.view-tab[data-view="analista"]` to the existing
  `body:not([data-role="admin"]) …` selector list (task 2.2). Also added two
  small new rules (`.analista-estado`, `.analista-dot`) for the semáforo dot,
  per design ADR-2 ("one optional dot class if needed") — no new list/row
  component.
- `web/js/main.js`: added `import { initAnalista } from './analista.js';` and
  the `switchView()` branch calling `initAnalista(...)` on tab open (task 2.3),
  landed together with `web/js/analista.js` per the sequencing note (a missing
  import target would break `main.js` for every view).

### Phase 3 — `web/js/analista.js`
Implemented as one cohesive file (tasks 3.1-3.6 together, since it's an
incremental build-out of the same module):
- `initAnalista(root, { getToken })` with the `isAdmin()` defense-in-depth
  guard, `sticker-page-head` shell + `Actualizar` button.
- Normalized `SourceRow` shape (id, nombre, descripcion, ultima_lectura,
  registros, estado_color, estado_label, detalle) and `STALE_MS = 45 * 60_000`
  per design §2/ADR-5.
- `loadSourceRows()` fetch orchestration via `Promise.allSettled` over the 9
  static reads (`meta.json`, `reportes_meta.json`, `_status.json`, the 4
  orphaned JSON files, `geocode/geocode_cache.json`, `fetchIsraelRecords()`)
  plus the one live call to `/api/source-status`.
- Per-source mapping functions (`rowFromMeta`, `rowGeocoding`,
  `rowAtencionsismo`, `rowGlobalRun`, `rowOrphan`, `rowIsrael`) implementing
  the design §3 table and the spec's color/label rules, including ADR-6
  (a transport-level fetch failure always renders amarillo `sin datos`,
  never `rojo`).
- `rowHtml()` rendering into `<li class="sticker-row">` reusing
  `sticker-identity`/`sticker-meta`, with a semáforo dot + Spanish label; all
  interpolated values pass through `escapeHtml`.
- `#analista-refresh` click re-runs `reload({ bust: true })`, cache-busting the
  `/api/source-status` call only; no timers/polling anywhere in the module.

## Deviations from design (with justification)

1. **`asignaciones.json` `registros` derivation.** Design §3 row 6 says
   `registros = pendientes.length + visitados.length`. Inspecting the actual
   file (`web/data/asignaciones.json`) shows `pendientes` and `visitados` are
   **numbers** (counts), not arrays — `.length` on a number is `undefined`.
   Implemented as `Number(pendientes) + Number(visitados)` instead, which is
   the number of records the design intended to count. Verified against the
   real file: `pendientes: 173, visitados: 116`.
2. **Geocoding cache: no per-source fetch drives the status, but a fetch is
   still issued.** The spec requires this row to be amarillo `sin metadata`
   "regardless of cache file age" and forbids fabricating a timestamp/count —
   so `rowGeocoding()` returns a fixed row with no dependency on any fetch
   result. A `readJson('geocode/geocode_cache.json')` call is still included
   in the `Promise.allSettled` batch (matching task 3.3's literal enumeration
   of the 9 static reads and design §3 row 3's "HEAD/GET presence check"),
   but its result is intentionally unused — this satisfies the "fault
   isolation" shape of the batch without inventing a signal the spec says
   must not be shown.
3. **`_status.json` missing/unreadable state.** Neither the spec nor design
   define what to show if `_status.json` can't be read at all (it's
   Blob-only, not git-tracked, so it 404s against the local static fallback
   in this dev checkout). Implemented as amarillo `sin metadata` /
   `"sin dato de corrida global"` — consistent with the "sin metadata" amarillo
   category used elsewhere for "signal absent" (as opposed to `sin datos` for
   read failures, or `con errores` which is reserved for an explicit
   `ok: false`).
4. **Testability seam for `api/source-status.js`.** Design §7 says "the
   token-verification dependency can be injected/stubbed"; implemented as an
   exported `handle({ verify, probe })` factory (`module.exports.handle`)
   alongside the default export (`module.exports = handle()`), used by
   `api/source-status.test.js` to exercise the 403/401/ok:true/ok:false paths
   without the Firebase Admin SDK or a real network call, mirroring
   `api/usuarios.test.js`'s pattern of exporting pure/injectable pieces for
   its self-check.

No other deviations from `tasks.md`, `spec.md`, or `design.md`.

## Explicitly not done (by instruction)

- **Phase 4 (manual verification, tasks 4.1-4.5)** — left unchecked. Requires
  a real deployed admin session (Firebase role claims, live Vercel/Railway
  env) not available in this environment.
- No `web/js/analista.test.mjs` was written — `tasks.md`'s own preamble
  designates this module's test evidence as the Phase 4 manual checklist,
  matching the existing convention for `stickers.js`/`usuarios.js` (neither
  has a dedicated frontend test file either).

## Files changed / added

- `api/reportados.js` (edited — additive export)
- `api/source-status.js` (new)
- `api/source-status.test.js` (new)
- `web/index.html` (edited)
- `web/styles.css` (edited)
- `web/js/main.js` (edited)
- `web/js/analista.js` (new)
- `openspec/changes/analista-fuentes-datos-tab/tasks.md` (checkboxes updated)

## Post-apply 4R review correction (addendum)

A full 4R review (risk/resilience/readability/reliability) ran after the apply
above and corroborated two CRITICAL findings, both fixed in one scoped
correction transaction, independently validated (fix-delta validator:
APPROVE after `spec.md`/`design.md` were reconciled — see below):

1. **RESILIENCE-001** (deterministic): no fetch in `loadSourceRows()`'s
   `Promise.allSettled` batch had a timeout, so a genuine network hang (not an
   explicit rejection) would leave the tab stuck loading forever. Fixed by
   adding a local `withTimeout(promise, ms, label)` helper (15s) wrapping all
   10 promises; a timeout now falls through the same existing "sin datos"
   fallback path an ordinary rejection already used. New test:
   `web/js/analista.test.mjs`.
2. **RELIABILITY-001** (inferential, refuter-corroborated): `api/source-status.js`
   originally set `Cache-Control: public, s-maxage=60` on an admin-gated
   endpoint with no `Vary` header — Vercel's shared Edge Network caches Node
   Serverless Function responses by URL+method, so one admin's cached response
   could be served to a later unauthorized/non-admin caller within the cache
   window, bypassing the 401/403 gate. Fixed by changing both response
   branches to `Cache-Control: private, no-store`. `spec.md`'s "Live-probe
   endpoint" requirement and its CDN-caching scenario were updated to match
   this as the final, authoritative behavior (the original `s-maxage=60`
   requirement was the vulnerable design, not a valid acceptance criterion).
   Extended `api/source-status.test.js` to assert the header on both branches.

Both fixes re-verified independently by `sdd-verify` against the corrected
`spec.md`: 0 CRITICAL findings, implementation matches spec exactly.
