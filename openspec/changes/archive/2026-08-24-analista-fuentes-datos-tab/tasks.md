# Tasks: analista-fuentes-datos-tab (ARCHIVED)

Strict TDD active. Phases 1-3 completed and verified. Phase 4 correctly left unchecked (post-deploy manual verification).

## Phase 1: Backend live-probe endpoint (test-first)

- [x] **1.1** `api/reportados.js` — additive export `module.exports.probeApi = probeApi;`
- [x] **1.2** Write `api/source-status.test.js` FIRST (assert-based, auth gates, ok/not-ok mapping)
- [x] **1.3** Confirm RED: `node api/source-status.test.js` fails (module doesn't exist)
- [x] **1.4** Implement `api/source-status.js` — GET-only, auth gate, probe reuse, `{ ok, status, detail, checked_at }`, **Cache-Control: private, no-store** (post-apply 4R RELIABILITY-001 correction)
- [x] **1.5** Confirm GREEN: `node api/source-status.test.js` passes; regression check on `usuarios.test.js` passes

## Phase 2: Frontend scaffolding

- [x] **2.1** `web/index.html` — add Analista button + view section
- [x] **2.2** `web/styles.css` — add selector to admin-gate list
- [x] **2.3** `web/js/main.js` — add import + switchView() branch (land together with 3.1 per sequencing note)

## Phase 3: Frontend module (analista.js)

- [x] **3.1** Create `web/js/analista.js` skeleton with isAdmin() guard, shell, empty sticker-list
- [x] **3.2** Implement SourceRow shape + STALE_MS = 45*60*1000 constant
- [x] **3.3** Implement fetch orchestration via Promise.allSettled (9 static + 1 live), with timeout guards (RESILIENCE-001 correction via `withTimeout()`)
- [x] **3.4** Map all 10 sources to SourceRow per design table
- [x] **3.5** Implement renderRows() — sticker-row + semáforo dot + estado_label + sticker-meta
- [x] **3.6** Wire #analista-refresh click → reload() (cache-busting live-probe call; no polling)

## Phase 4: Manual post-deploy verification

- [ ] **4.1** Log in as admin → confirm tab visible and opens correctly
- [ ] **4.2** Log in as non-admin → confirm tab/button hidden by CSS; GET /api/source-status returns 403
- [ ] **4.3** As admin, open tab → confirm all 10 rows render with plausible name/description/timestamp/status
- [ ] **4.4** With valid VISITADOS_API_PASS → atencionsismo row is verde `conectado`; unset/break env → row flips to rojo `con errores`
- [ ] **4.5** Click "Actualizar" → confirm fresh fetch; no residual polling

---

## Review Workload Forecast

| File | Tasks | Changed lines | Notes |
|---|---|---:|---|
| `api/reportados.js` | 1.1 | ~1 | Additive export |
| `api/source-status.test.js` | 1.2 | ~90-120 | New file, assert-based |
| `api/source-status.js` | 1.4 | ~60-80 | New file, mirrors `usuarios.js` auth |
| `web/index.html` | 2.1 | ~2 | Button + section |
| `web/styles.css` | 2.2 | ~1 | Selector added |
| `web/js/main.js` | 2.3 | ~2 | Import + branch |
| `web/js/analista.js` | 3.1-3.6 | ~200+ | New file, fetch + render + wiring |
| `web/js/analista.test.mjs` | 3.3 (4R) | ~80 | Added post-apply for timeout testing |
| **Total** | | **~350+** | Likely triggers full 4R review (security endpoint + size) |

## Post-Apply 4R Review Corrections

Two CRITICAL issues identified and fixed in one scoped correction transaction:

### RESILIENCE-001: Network hang protection
- **Issue**: No timeout on Promise.allSettled batch; stalled fetch hangs tab indefinitely
- **Fix**: Added `withTimeout(promise, ms, label)` helper (15s) wrapping all 10 fetches
- **Test**: New `web/js/analista.test.mjs` covering timeout assertion

### RELIABILITY-001: Shared-cache auth bypass
- **Issue**: Originally set `Cache-Control: public, s-maxage=60` on admin-gated endpoint; Vercel's shared Edge Network caches by URL+method, so one admin's cached response could serve later unauthorized caller
- **Fix**: Changed to `Cache-Control: private, no-store` on both ok:true and ok:false branches
- **Test**: Extended `api/source-status.test.js` to assert cache header on both branches

Both independently verified by `sdd-verify`: 0 CRITICAL post-correction.

## Notes

- Phase 4 tasks correctly left unchecked; require real deployed admin browser session (Firebase claims, live Railway/Blob)
- `spec.md` and `design.md` updated post-4R to reflect final authoritative behavior (private/no-store caching, timeout guards)
- `apply-progress.md` contains full historical breakdown of implementation with deviations and justifications
</content>
