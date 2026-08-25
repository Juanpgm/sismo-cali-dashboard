# Tasks: analista-fuentes-datos-tab

Strict TDD is active for this project (`openspec/config.yaml` → `strict_tdd: true`,
`test_command: node --test` / `node <file>.test.js`). Per the sibling admin tabs
(`stickers.js`, `usuarios.js`) neither of which has a dedicated `web/js/*.test.mjs`,
this change's only new automated test is `api/source-status.test.js` — the one
file design.md designates as testable in isolation (pure auth-gate + ok/not-ok
mapping, with the live-network assertion self-gated behind `RUN_LIVE_PROBE`).
The frontend module's test evidence is the manual verification checklist in
Phase 4, matching the existing convention for this class of module.

## 1. Backend: live-probe endpoint (test-first)

- [x] **1.1** `api/reportados.js` — add the one additive export line
      `module.exports.probeApi = probeApi;` at the bottom. No other change to
      the file (default export / KPI handler untouched).
      _Satisfies: design ADR-4; spec "Live-probe endpoint" (probe reuse)._
- [x] **1.2** Write `api/source-status.test.js` FIRST, before `api/source-status.js`
      exists, asserting (per design §7 / proposal Testability):
      1. missing `Authorization` header → `401`, no probe attempted
      2. invalid/expired token → `401`
      3. valid token, non-admin role → `403`, no probe attempted
      4. forced bad `VISITADOS_API_PASS` → `200 { ok:false, status:'con errores' }` with non-null `detail`
      5. `RUN_LIVE_PROBE`-gated real-network case → `{ ok:true, status:'conectado' }` when the env var + `VISITADOS_API_PASS` are present, otherwise prints a skip line and passes
      Use the same stubbable-dependency seam as `api/usuarios.test.js` for
      `verifyFirebaseToken`/`roleFromClaims`.
      _Satisfies: spec "Live-probe endpoint" scenarios (401/403/ok:true/ok:false)._
- [x] **1.3** Run `node api/source-status.test.js` and confirm it fails (module
      doesn't exist yet) — red step.
- [x] **1.4** Implement `api/source-status.js` per design §4 (ADR-3): `GET`-only
      (405 otherwise), `verifyFirebaseToken` + `roleFromClaims` gate (401/403),
      Basic auth from `VISITADOS_API_PASS` (missing → `200 { ok:false }`, not
      5xx), reuse `probeApi` from `api/reportados.js`, response shape
      `{ ok, status, detail, checked_at }`. `Cache-Control: private, no-store`
      on every response (post-apply 4R correction, RELIABILITY-001: the
      originally-designed `public, s-maxage=60` was a shared-cache auth-bypass
      risk on this admin-gated endpoint — see design §4 and spec.md's
      "Live-probe endpoint" requirement, current text).
      _Satisfies: spec "Live-probe endpoint" (all sub-bullets), spec "Status
      color… Live-probe source" (endpoint side)._
- [x] **1.5** Run `node api/source-status.test.js` again, confirm all
      non-gated assertions pass (green step) — parallelizable with Phase 2/3
      since it touches no shared file.

## 2. Frontend: tab scaffolding (HTML/CSS/nav)

> Sequencing note: 2.3 adds an ESM `import` of `web/js/analista.js` into
> `main.js`. Since there is no bundler, a missing import target breaks
> `main.js` at runtime for every view, not just Analista. Do 3.1 (create the
> file, even as a minimal no-op-safe skeleton) before or together with 2.3;
> do not commit/deploy 2.3 alone.

- [x] **2.1** `web/index.html` — add
      `<button class="view-tab" data-view="analista" role="tab" aria-selected="false">Analista</button>`
      next to the Usuarios button, and
      `<section id="view-analista" data-view-panel="analista" aria-label="Analista" hidden></section>`
      next to `#view-usuarios`.
      _Satisfies: spec "Tab visibility" (admin sees the tab / non-admin hidden)._
- [x] **2.2** `web/styles.css` — add `.view-tab[data-view="analista"]` to the
      existing `body:not([data-role="admin"]) …` selector list (~lines
      1561-1563); reuse the existing `{ display: none !important; }` block.
      _Satisfies: spec "Tab visibility" (CSS hide for non-admin)._
- [x] **2.3** `web/js/main.js` — add
      `import { initAnalista } from './analista.js';` and one branch in
      `switchView()` beside the `usuarios` branch:
      `if (view === 'analista') initAnalista(document.getElementById('view-analista'), { getToken: getIdToken });`
      _Satisfies: spec "Refresh behavior" (lazy-init-on-tab-open)._

## 3. Frontend: `web/js/analista.js` module

- [x] **3.1** Create `web/js/analista.js` skeleton: `initAnalista(root, { getToken })`,
      `import { isAdmin } from './auth.js';` defense-in-depth guard (return
      early + permission notice if `!isAdmin()`), `sticker-page-head` shell
      with title "Analista" + `<button id="analista-refresh">Actualizar</button>`,
      empty `sticker-list` container. Makes the 2.3 import resolve safely.
      _Satisfies: spec "Tab visibility" (JS guard scenario)._
- [x] **3.2** Implement the normalized `SourceRow` shape and helpers per
      design §2/ADR-1 (`id`, `nombre`, `descripcion`, `ultima_lectura`,
      `registros`, `estado_color`, `estado_label`, `detalle`), plus the
      `STALE_MS = 45 * 60 * 1000` constant (ADR-5).
      _Satisfies: spec "Status color and label derivation per source category"._
- [x] **3.3** Implement fetch orchestration in `reload()`: `Promise.allSettled`
      over the 9 static reads (`fetchData` for `meta.json`, `reportes_meta.json`,
      `_status.json`, `geocode/geocode_cache.json` presence, the 4 orphaned
      JSON files, `fetchIsraelRecords()`) **and** the one live call to
      `/api/source-status` via the `callApi` pattern (Bearer token from
      `getToken`). A rejected/failed fetch or non-ok live-endpoint response
      maps to amarillo `sin datos` (ADR-6) — never rojo; rojo only from a
      received `_status.ok===false` or received `{ ok:false }` probe body.
      _Satisfies: spec "All known sources are listed", "Row with no row count
      omits the field", all per-category status scenarios, "Request to the
      live-probe endpoint itself fails", "A source's underlying fetch fails
      at the transport level"._
- [x] **3.4** Map each of the 10 sources to its `SourceRow` per design §3's
      table (field names, `registros` derivation, color rule per source,
      including the atencionsismo row's live-probe-overrides-snapshot rule).
      _Satisfies: spec scenarios "Fresh/Stale/Missing EDAN-F3", "Geocoding
      always sin metadata", "Global run failed", "Orphaned output row", "Live
      probe succeeds independent of snapshot staleness", "Live probe reports
      the upstream API is down"._
- [x] **3.5** Implement `renderRows(rows)` — `<li class="sticker-row">` per
      row with `sticker-identity` (nombre + descripcion), a semáforo dot
      (`background:` inline from `COLORS.status`) + `estado_label`, and
      `sticker-meta` (ultima_lectura via `formatTs()`, registros when
      non-null, detalle). All interpolated values through `escapeHtml`.
      _Satisfies: spec "Source list rendering"._
- [x] **3.6** Wire `#analista-refresh` click → `reload()` (cache-busting the
      `/api/source-status` call so the button forces a fresh probe); no
      timers/polling added anywhere in the module.
      _Satisfies: spec "Refresh behavior" (reopen re-fetches, manual button,
      no background polling)._

## 4. Manual verification (no CI gate on this project)

Run after Phase 1-3 are deployed together. Matches proposal's rollback plan
manual-verification list and the spec's admin-only / live-probe scenarios
that cannot be asserted by `node api/source-status.test.js` alone (real
browser session + real role claims).

- [ ] **4.1** Log in as admin → confirm the "Analista" nav button and
      `#view-analista` section are visible and open correctly.
      _Verifies: spec "Admin sees the tab"._
- [ ] **4.2** Log in as a non-admin (viewer/inspector) → confirm the nav
      button/section are absent (CSS gate), and that a raw
      `GET /api/source-status` call with that session's token returns `403`.
      _Verifies: spec "Non-admin does not see the tab", "Non-admin
      authenticated caller rejected"._
- [ ] **4.3** As admin, open the tab and confirm all 10 rows render with a
      plausible name/description/timestamp/status (EDAN-F3, Survey123,
      Geocoding, atencionsismo, global pipeline run, 4 orphaned outputs,
      Israel).
      _Verifies: spec "All known sources are listed"._
- [ ] **4.4** With `VISITADOS_API_PASS` valid in the environment, confirm the
      atencionsismo row is verde `conectado`. Temporarily unset/break the env
      var (or use a scratch deploy) and confirm the row flips to rojo `con
      errores`, then restore it.
      _Verifies: spec "Live probe reports the upstream API is down", proposal
      rollback-plan manual verification item 3._
- [ ] **4.5** Click "Actualizar" and confirm a fresh fetch (network tab shows
      a new `/api/source-status` call) with no residual polling afterward.
      _Verifies: spec "Manual Actualizar button re-triggers fetch", "No
      background polling occurs"._

---

## Review Workload Forecast

| File | Task(s) | Est. changed lines | Notes |
|---|---|---:|---|
| `api/reportados.js` | 1.1 | ~1 | Pure additive export |
| `api/source-status.test.js` | 1.2 | ~90-120 | New file, assert-based, no framework |
| `api/source-status.js` | 1.4 | ~60-80 | New file, mirrors `api/usuarios.js` auth gate |
| `web/index.html` | 2.1 | ~2 | One button + one empty section |
| `web/styles.css` | 2.2 | ~1 | One selector added to existing comma list |
| `web/js/main.js` | 2.3 | ~2 | One import + one `switchView()` branch |
| `web/js/analista.js` | 3.1-3.6 | ~180-230 | New file: fetch orchestration (10 sources) + normalization + render + wiring |
| **Total** | | **~340-435** | |

- **400-line budget risk: possible, flag for orchestrator.** The estimate
  straddles the 400-changed-line threshold depending on how verbose
  `analista.js`'s per-source mapping (design §3's 10-row table) ends up in
  practice. Combined with `api/source-status.js` being a new admin-gated
  auth/permission endpoint (token verification, role check — a "security,
  permissions" risk-table signal in its own right), **this change likely
  qualifies for the full 4R review sweep** (risk-risk, resilience,
  readability, reliability) rather than a single dominant-risk lens, on
  either trigger independently. Decision needed before apply: the
  orchestrator should confirm whether to pre-commit to full 4R or wait for
  the actual diff line count before selecting lenses.
- **Chained-PR risk: low.** This is one cohesive, self-contained change
  (proposal's rollback plan: single `git revert`). No natural split point
  that wouldn't leave the app in a broken intermediate state — see the
  Phase 2/3 sequencing note (2.3's import requires 3.1 to exist). Recommend
  landing Phases 1-3 as one PR/commit; Phase 4 is post-deploy manual
  verification, not a code change.
- **Parallelizable work:** 1.1-1.5 (backend) has zero file overlap with
  2.1-2.2 (HTML/CSS) and can proceed in parallel. 2.3 and 3.1 must land
  together (sequencing note above). 3.2-3.6 are sequential within
  `analista.js` (same file, incremental build-out). 4.x is strictly after
  1-3 are deployed.
