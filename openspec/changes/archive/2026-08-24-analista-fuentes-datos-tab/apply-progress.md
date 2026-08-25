# Apply progress: analista-fuentes-datos-tab (ARCHIVED)

Status: Phases 1-3 implemented and checked off in `tasks.md`. Phase 4 (manual
post-deploy verification) is intentionally left unchecked.

## What was implemented

### Phase 1 — backend live-probe endpoint (test-first, strict TDD)
- `api/reportados.js`: one additive line, `module.exports.probeApi = probeApi;` (task 1.1).
- `api/source-status.test.js`: written FIRST (task 1.2), confirmed RED by running `node api/source-status.test.js` against the not-yet-existing module (task 1.3 — `Cannot find module './source-status.js'`).
- `api/source-status.js`: implemented per design §4/ADR-3/ADR-4 (task 1.4).
- Re-ran the test, confirmed GREEN (task 1.5). Final verbatim output:
  ```
  source-status.test.js: skipping RUN_LIVE_PROBE real-network assertion (set RUN_LIVE_PROBE=1 and VISITADOS_API_PASS to run it)
  source-status.test.js OK
  ```
  The `RUN_LIVE_PROBE`-gated real-network assertion was not exercised — no `VISITADOS_API_PASS` available in this environment, and it is designed to self-skip, which it did.
  `node api/usuarios.test.js` was also re-run after editing `reportados.js` to confirm no regression.

### Phase 2 — frontend scaffolding
- `web/index.html`: added the `Analista` nav button next to `Usuarios`, and `<section id="view-analista" data-view-panel="analista" hidden>` next to `#view-usuarios` (task 2.1).
- `web/styles.css`: added `.view-tab[data-view="analista"]` to the existing `body:not([data-role="admin"]) …` selector list (task 2.2).
- `web/js/main.js`: added `import { initAnalista } from './analista.js';` and the `switchView()` branch calling `initAnalista(...)` on tab open (task 2.3), landed together with `web/js/analista.js` per the sequencing note.

### Phase 3 — `web/js/analista.js`
Implemented as one cohesive file (tasks 3.1-3.6 together, incremental build-out):
- `initAnalista(root, { getToken })` with the `isAdmin()` defense-in-depth guard, `sticker-page-head` shell + `Actualizar` button.
- Normalized `SourceRow` shape (id, nombre, descripcion, ultima_lectura, registros, estado_color, estado_label, detalle) and `STALE_MS = 45 * 60_000` per design §2/ADR-5.
- `loadSourceRows()` fetch orchestration via `Promise.allSettled` over the 10 data sources (9 static reads + 1 live probe).
- Per-source mapping functions (`rowFromMeta`, `rowGeocoding`, `rowAtencionsismo`, `rowGlobalRun`, `rowOrphan`, `rowIsrael`) implementing design §3 table and spec's color/label rules.
- `rowHtml()` rendering into `<li class="sticker-row">` reusing `sticker-identity`/`sticker-meta`, with semáforo dot + Spanish label; all values through `escapeHtml`.
- `#analista-refresh` click re-runs `reload({ bust: true })`, cache-busting the `/api/source-status` call only; no polling anywhere in the module.

## Deviations from design (with justification)

1. **`asignaciones.json` `registros` derivation.** Design §3 row 6 says `registros = pendientes.length + visitados.length`. Actual file shows `pendientes` and `visitados` are **numbers** (counts), not arrays. Implemented as `Number(pendientes) + Number(visitados)` instead.

2. **Geocoding cache: no per-source fetch drives status, but fetch still issued.** Spec requires this row to be amarillo `sin metadata` "regardless of cache file age" and forbids fabricating timestamp/count. `rowGeocoding()` returns fixed row with no dependency on fetch result. Fetch is still included in `Promise.allSettled` batch for fault isolation but result intentionally unused.

3. **`_status.json` missing/unreadable state.** Neither spec nor design define what to show if `_status.json` can't be read. Implemented as amarillo `sin metadata` / `"sin dato de corrida global"` — consistent with "sin metadata" amarillo category for "signal absent".

4. **Testability seam for `api/source-status.js`.** Design §7 says token-verification dependency can be injected/stubbed. Implemented as exported `handle({ verify, probe })` factory alongside default export, used by `api/source-status.test.js` for stubbing without Firebase Admin SDK.

## Explicitly not done (by instruction)

- **Phase 4 (manual verification, tasks 4.1-4.5)** — left unchecked. Requires a real deployed admin session.
- No `web/js/analista.test.mjs` initially — per `tasks.md` preamble, manual verification checklist is the test evidence (matching convention for `stickers.js`/`usuarios.js`). Added post-apply as part of 4R correction.

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
correction transaction, independently validated by fix-delta validator: APPROVE.

### RESILIENCE-001 (deterministic)
**Issue**: No fetch in `loadSourceRows()`'s `Promise.allSettled` batch had a timeout, so a genuine network hang (not an explicit rejection) would leave the tab stuck loading forever.

**Fix**: Added a local `withTimeout(promise, ms, label)` helper (15s) wrapping all 10 promises. A timeout now falls through the same existing "sin datos" fallback path an ordinary rejection already used.

**Test**: New `web/js/analista.test.mjs` covering timeout behavior (fast-resolve case + never-resolving promise asserted to reject within timeout window).

### RELIABILITY-001 (inferential, refuter-corroborated)
**Issue**: `api/source-status.js` originally set `Cache-Control: public, s-maxage=60` on an admin-gated endpoint with no `Vary` header. Vercel's shared Edge Network caches Node Serverless Function responses by URL+method, so one admin's cached response could be served to a later unauthorized/non-admin caller within the cache window, bypassing the 401/403 gate. **A real auth bypass, not theoretical** — confirmed against the same pattern already in production on `api/reportados.js`, which is safe there only because that endpoint has no auth gate.

**Fix**: Changed both response branches to `Cache-Control: private, no-store`. This endpoint intentionally accepts re-hitting the upstream API on every tab open/refresh rather than risk shared-cache auth bypass (acceptable for low-traffic admin diagnostic view).

**Spec**: Updated `spec.md`'s "Live-probe endpoint" requirement (section 4, bullet 5) to specify `private, no-store` as the final authoritative behavior. The original `s-maxage=60` requirement was the vulnerable design, not a valid acceptance criterion.

**Test**: Extended `api/source-status.test.js` to assert the header on both branches (`!includes('public')` + `includes('no-store')`).

## Verification Result

Both fixes re-verified independently by `sdd-verify` against the corrected `spec.md`: **0 CRITICAL findings**, implementation matches spec exactly.

The `spec.md` and `design.md` now reflect the final, corrected, authoritative behavior. The `tasks.md` task 1.4 text remains stale (still reads "public, s-maxage=60") as a historical artifact — see verify-report.md WARNING #1.
</content>
