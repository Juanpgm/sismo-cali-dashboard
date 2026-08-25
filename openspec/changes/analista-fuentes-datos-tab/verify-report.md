# Verify Report: analista-fuentes-datos-tab

## Verdict
**PASS.** 0 CRITICAL, 2 WARNING, 1 SUGGESTION. Ready for archive.

## Test evidence

```
node api/source-status.test.js
  source-status.test.js: skipping RUN_LIVE_PROBE real-network assertion (set RUN_LIVE_PROBE=1 and VISITADOS_API_PASS to run it)
  source-status.test.js OK

node api/usuarios.test.js   (regression check, reportados.js was edited)
  usuarios.test.js OK

node --test "js/**/*.test.mjs"   (run from web/)
  ✔ js\analista.test.mjs
  ✔ js\charts.test.mjs
  ✔ js\data.test.mjs
  ✔ js\evaluaciones.test.mjs
  ✔ js\utils.test.mjs
  tests 5, pass 5, fail 0
```

`node --check` clean on `api/source-status.js` and `api/reportados.js`.

## Spec-vs-implementation check (current spec.md, post-correction)

- **Tab visibility** — `web/index.html:76,277` add the nav button and
  `#view-analista` section; `web/styles.css:1564` adds
  `.view-tab[data-view="analista"]` to the existing admin-gate selector list;
  `web/js/analista.js:251-255` (`initAnalista`) independently guards with
  `isAdmin()` before any render/fetch. Matches Stickers/Usuarios pattern
  exactly, including the residual property that `#view-analista` itself isn't
  separately CSS-gated — same as `#view-stickers`/`#view-usuarios` today; the
  JS guard is the explicit defense-in-depth spec scenario 3 covers.
- **Source list rendering** — `loadSourceRows()` returns exactly the 10 named
  rows (edan-f3, survey123, geocoding, atencionsismo, pipeline-run,
  asignaciones, cruce-gestor, cruce-criticos-survey, criticos-api, israel);
  `rowHtml()` omits `registros` when null/undefined rather than showing a
  placeholder.
- **Status color/label per category** — verified each category's logic in
  `analista.js` against spec's per-category MUST bullets: snapshot freshness
  (`rowFromMeta` + `STALE_MS`/`freshnessColor`), always-amarillo Geocoding
  (`rowGeocoding`), whole-run `_status.json` (`rowGlobalRun`), orphaned
  "sin consumidor" (`rowOrphan`), and the live-probe override rule
  (`rowAtencionsismo`, probe result overrides snapshot staleness). The
  transport-failure-vs-explicit-error distinction (spec's most nuanced rule)
  is correctly implemented: `readJson()`'s caught-exception path → `state:
  'error'` → `sinDatosRow()` (amarillo `sin datos`); a resolved-but-not-ok
  response → `state: 'missing'` → category-specific amarillo label (`sin
  metadata` / `ausente`), never rojo either way. `rojo` only ever comes from
  `_status.json.ok === false` or a received `{ ok: false }` probe body —
  confirmed no other code path sets `estado_color: 'rojo'`.
- **Live-probe endpoint `api/source-status.js`** — GET-only (405 guard),
  `verifyFirebaseToken`/`roleFromClaims` gate (401 missing/invalid token, 403
  non-admin), `probeApi()` reused from `api/reportados.js` (one-line additive
  export confirmed at `api/reportados.js:161`), response shape `{ ok, status,
  detail, checked_at }`. **Cache-Control is `private, no-store` on both the
  `ok:true` and `ok:false` branches** (lines 66 and 69) — matches the
  corrected spec requirement and the "Response is never shared-cached"
  scenario; `api/source-status.test.js` asserts this explicitly on both
  branches (`!includes('public')` + `includes('no-store')`).
- **Fetch-hang resilience (from the 4R correction)** — `withTimeout()`
  (`web/js/analista.js:151-156`) wraps every one of the 9 static reads plus
  the live-probe call at `SOURCE_TIMEOUT_MS = 15000` before `Promise
  .allSettled`, so a stalled TCP/DNS/serverless response can no longer hang
  the whole tab indefinitely — it now settles into the same "sin datos" path
  a rejection would. Covered by `web/js/analista.test.mjs` (fast-resolve case
  + a never-resolving promise asserted to reject within the timeout window).
- **Refresh behavior** — `main.js:206-210` calls `initAnalista(...)` inside
  `switchView()` unconditionally on every `view === 'analista'` transition
  (same lifecycle branch as Stickers/Usuarios, which already re-fetch each
  open). `#analista-refresh` click re-runs `reload({ bust: true })`, cache
  -busting only the live-probe URL. No `setInterval`/`setTimeout` polling
  exists anywhere in `analista.js` outside the one-shot `withTimeout` guards.
- **Non-goals** — none of the explicitly out-of-scope items (per-source
  `_status.json` array, `integracion_F1` monitoring, `README.md` fixes, new
  probes for Sheet/Survey123/Geocoding) were touched or fabricated. Correctly
  not flagged as gaps.

## Tasks vs. code state

- Phase 1 (1.1-1.5), Phase 2 (2.1-2.3), Phase 3 (3.1-3.6) are all checked `[x]`
  in `tasks.md` and match the code: `api/reportados.js`'s additive export,
  `api/source-status.js`/`api/source-status.test.js`, the HTML/CSS/main.js
  scaffolding, and `web/js/analista.js`'s full implementation all exist and
  pass their tests.
- Phase 4 (4.1-4.5, manual post-deploy verification) is correctly left
  unchecked `[ ]` — genuinely pending a real admin browser session against a
  live deploy, not something this environment can perform. Not silently
  treated as done.

## Findings

### WARNING
1. **`tasks.md` task 1.4's description text is stale relative to the
   corrected implementation.** It still reads "`Cache-Control: public,
   s-maxage=60` on success" even though the box is checked and the actual
   code (and spec.md) now require `private, no-store`. The checkbox reflects
   the *initial* apply, not the subsequent correction. Functionally harmless
   (spec.md is the authoritative source and matches code), but a future
   reader of `tasks.md` alone would get the wrong caching header. Recommend a
   one-line amendment to task 1.4's text before/at archive for accuracy, not
   a re-open of the fix itself.
2. **`apply-progress.md` predates the 4R correction** and does not mention
   `withTimeout()`, the `Cache-Control` fix, or the two new/extended test
   files (`web/js/analista.test.mjs`, extended `api/source-status.test.js`).
   Per the task framing this is expected/out-of-scope for this verify pass
   (spec.md/design.md are the reconciled source of truth), but flagging so
   the archive step can decide whether to append a short addendum to
   `apply-progress.md` for historical completeness.

### SUGGESTION
1. `design.md`'s §1 file-level table does not list `web/js/analista.test.mjs`
   as a new file (it lists only `api/source-status.test.js`), even though
   §4's "Caching" section was updated to document the correction. Minor
   doc-completeness gap in the file table only; no functional impact.

## Result

- `status`: done
- `executive_summary`: Implementation satisfies every spec.md requirement and scenario, including the corrected private/no-store caching and the withTimeout hang-guard; 0 CRITICAL, 2 WARNING (stale task/apply-progress text), 1 SUGGESTION (design.md file-table gap).
- `artifacts`: openspec/changes/analista-fuentes-datos-tab/verify-report.md
- `next_recommended`: sdd-archive
- `risks`: none blocking archive
