# Tasks: FastAPI Backend Consolidation

Change: `fastapi-backend-consolidation` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-tasks

Reads `proposal.md` (incl. all three addenda — survey_cali scope addition, Google Sheets exclusion,
and its `integracion-f3`/`asignaciones` extension) and `design.md` (12 ADRs, all final
post-Sheets-exclusion) and all 5 spec files under `specs/`. Ordered, hierarchical, grouped by phase =
migration slice (1-9, plus addendum sub-slices 7b/8b; `normalizador`, `integracion-f3`, and
`asignaciones` do NOT migrate — all three stay on the legacy `integracion_F1` image until slice 9).
Strict TDD is ACTIVE: every non-trivial logic task has a RED task (failing pytest
first, from the spec scenarios) before its GREEN task, following the `stickers-asignacion` tasks.md
convention (checkbox style, `— Satisfies:` cross-references).

**Delivery**: `auto-chain` / `stacked-to-main` — each slice below is one PR merging to main in order;
every slice leaves production working (old endpoints/jobs stay live until their own cutover slice
verifies). Sub-slices are called out where a single proposal slice itself risks exceeding the 400-line
review budget (see Review Workload Forecast).

**Skills to load before work** (sdd-apply, every slice): `C:\Users\User\.config\agents\skills\chained-pr\SKILL.md`,
`C:\Users\User\.config\agents\skills\work-unit-commits\SKILL.md`.

---

## Phase 0 — Pre-work: resolve design open question 6 (RESOLVED — see 0.1)

- [x] **0.1** Verified in code whether `integrar_f3.py`/`asignar_f3.py`'s direct-gspread branches are
      live or vestigial since the 2026-08-18 atencionsismo API switch (design.md open question 6;
      `integracion_F1/scripts/railway_setup.py:81` comment claims "integrar/asignar F3 leen la API
      atencionsismo desde 2026-08-18"). **Finding: LIVE, not vestigial — the comment is only half
      true.** Both jobs source `df_integrada` from the new API (`api_visitados.fetch_tabla()`,
      `integrar_f3.py:171`, `asignar_f3.py:783`), but BOTH still (a) READ their other required input —
      the F3 Google Form intake — via `gspread.authorize(...)` + `_read_tab(gc, F3_SPREADSHEET_ID,
      F3_SRC_TAB)` (`integrar_f3.py:169-170`; `asignar_f3.py:782`), and (b) WRITE their sole output
      back to a Sheets tab via `gspread.authorize(...).open_by_key(F3_SPREADSHEET_ID).worksheet(
      DST_TAB)` (`integrar_f3.py:196-199`; `asignar_f3.py:829-835`, incl. `add_worksheet` fallback).
      **Cutting these branches removes the ONLY F3 input source and the ONLY output sink for both
      jobs** — there is no non-Sheets replacement in either module today.
      — **STOP condition triggered, then resolved (twice over).** This finding was escalated to the
      user, who decided (proposal.md, "Extension" to the Scope Exclusion Addendum, 2026-08-25
      post-tasks): `integracion-f3` and `asignaciones` join `normalizador` in the excluded set — their
      Sheets I/O is operationally dead and is NOT replaced (tasks 7.10/7.11 record the exclusion). The
      user then separately directed "no usar nada relacionado con el dagma" (Extension 2, 2026-08-25
      post-slice-1a), which additionally excludes `cruce-gestion` (task 7.7) and removes the `dagma()`
      credentials client entirely (task 1.10). Slice 7's migrated set is therefore TWO job services
      (`dashboard-refresh`, `cruce-sticker`); the excluded legacy set is FOUR jobs (`normalizador`,
      `integracion-f3`, `asignaciones`, `cruce-gestion`).
      — Satisfies: design.md open question 6; Scope Exclusion Addendum item 3 and both Extensions.

---

## Phase 1 — Slice 1: Scaffold (no consumer traffic yet)

Chain PR #1 (may need 1a/1b split — see forecast). Depends on: none.

- [x] **1.1** Create `backend/` package skeleton per design ADR-1 tree: `app/__init__.py`,
      `app/main.py` (stub `create_app()`), `app/config.py` (Settings stub), `app/auth/__init__.py`,
      `app/credentials/__init__.py`, `app/routers/__init__.py`, `app/services/__init__.py`,
      `app/jobs/__init__.py`, `app/integracion/__init__.py` + `PROVENANCE.md` (empty table header),
      `backend/requirements.txt`, `backend/tests/__init__.py`.
      — Satisfies: design.md ADR-1.

- [x] **1.2** Write `backend/Dockerfile`: `python:3.12-slim`; `COPY backend/ scripts/ deploy/`;
      `WORKDIR backend`; `pip install -r requirements.txt`; `CMD uvicorn app.main:app --host 0.0.0.0
      --port 8000`.
      — Satisfies: design.md ADR-1; backend-platform "Reproducible Git-Connected Deploy".

- [x] **1.3** Add repo-root `railway.json` build config: `dockerfilePath: backend/Dockerfile`, root
      directory = repo root (per ADR-1's rejection of `backend/`-as-root).
      — Satisfies: backend-platform "Deploy triggers from a git push" scenario.

- [x] **1.4** — MANUAL OPERATOR STEP, not a repo diff. Create the Railway "web" service, git-connected
      to this GitHub repo, root = repo root, `dockerfilePath = backend/Dockerfile`, always-on (proposal
      answer 8). Provision env vars: `FIREBASE_SERVICE_ACCOUNT_JSON`, `SIGNER_AWS_ACCESS_KEY_ID/SECRET`,
      `SIGNER_S3_BUCKET/REGION`, `SUPERADMIN_EMAIL` — NOT `GOOGLE_SERVICE_ACCOUNT_JSON` (dagma is
      unused anywhere in this backend, per proposal.md Scope Exclusion Addendum Extension 2). Confirm
      no CLI upload is ever used.
      — Satisfies: backend-platform "No CLI-upload path exists for the web service".
      — STATUS: DONE (operator step completed outside the automated apply batches). Live at
      `sismo-cali-dashboard-production.up.railway.app`, confirmed serving `/reportados` in production
      as of commit `c2fb564` / merge `7dacbde` (2026-08-25). This checkbox was left unticked by the
      1a/1b apply batches (correctly, since it was out of their automated scope) but was never synced
      back after the operator finished it — corrected here from git history, not from a new apply run.

- [x] **1.5** (RED) Write `backend/tests/auth/test_roles_parity.py` FIRST: table-driven port of the
      exact fixture matrix from `api/usuarios.test.js:8-22` (the actual JS parity source — NOT a
      dedicated `refresh.test.js`, which does not exist despite `api/refresh.js:183`'s stale comment):
      inspector (`@sismocali.gov.co`+password) → `inspector`; generic password → `usuario`; explicit
      `customClaims.role:'admin'` → `admin`; superadmin email, no claim → `admin`; viewer
      (`google.com`+`@cali.gov.co`) → `viewer`; other → `otro`; claim overrides derived default. MUST
      fail — `app.auth.roles` doesn't exist yet.
      — Satisfies: backend-platform "Parity suite passes identically to the JS test matrix",
      "Precedence order resolves to the earliest matching rule".

- [x] **1.6** (RED) Write `backend/tests/auth/test_verify.py` FIRST: injectable fake cert-fetcher (no
      network); valid token accepted; unknown `kid` → exactly one forced refetch then 401 if still
      unknown; bad `iss`/`aud`/`exp`/`iat`/empty `sub` rejected. MUST fail.
      — Satisfies: backend-platform "Ported Auth Verifier And Role Resolution".

- [x] **1.7** (GREEN) Implement `backend/app/auth/roles.py`: `role_from(email, claim_role, provider)`,
      `role_from_claims(claims)`, precedence ported verbatim from `api/refresh.js:77-94`. Run 1.5,
      confirm green.
      — Satisfies: backend-platform "Precedence order resolves to the earliest matching rule",
      "Parity suite passes identically to the JS test matrix".

- [x] **1.8** (GREEN) Implement `backend/app/auth/verify.py`: `async verify_firebase_token(id_token,
      project_id) -> claims`, RS256 against Google's rotating x509 certs, cert cache TTL from
      `Cache-Control: max-age`, injectable cert-fetcher, single forced refetch on unknown `kid`. Run
      1.6, confirm green.
      — Satisfies: backend-platform "Ported Auth Verifier And Role Resolution".

- [x] **1.9** (GREEN) Implement `backend/app/auth/deps.py`: `require_auth`, `require_role("admin")`,
      `current_claims` — foundation for the ADR-3 per-route matrix later slices attach to.
      — Satisfies: backend-platform "Route Parity Across Consolidated Endpoints" (auth-level column,
      foundation).

- [x] **1.10** (GREEN) Implement `backend/app/credentials/clients.py`: exactly ONE named memoized
      client — `sismo()` (`FIREBASE_SERVICE_ACCOUNT_JSON`, fail-fast). No `dagma()` client — removed
      per proposal.md Scope Exclusion Addendum Extension 2 (`cruce-gestion` is excluded from
      migration, see Phase 7); no `sheets()` client, no dagma/Sheets env var anywhere.
      `REQUIRED_CLIENTS` declaration mechanism per ADR-4.
      — Satisfies: backend-platform "Named-Client Credential Matrix Across Two Service Accounts" (all
      3 scenarios).
      — STATUS NOTE: this task was already implemented with 2 clients (sismo + dagma) before this
      revision; checkbox stays ticked — the apply agent's current batch removes the `dagma()` client
      to match this revised text, not a new task.

- [x] **1.11** (RED) Write `backend/tests/test_startup.py` FIRST: missing
      `FIREBASE_SERVICE_ACCOUNT_JSON` → app startup fails before serving. No second/job-only client
      to test — `GOOGLE_SERVICE_ACCOUNT_JSON`/dagma is unused anywhere in this backend (Scope
      Exclusion Addendum Extension 2). MUST fail (`create_app()` incomplete).
      — Satisfies: backend-platform "Missing web-route credential fails startup".
      — STATUS NOTE: originally written/implemented with a dagma job-only-credential assertion;
      checkbox stays ticked — the apply agent's current batch drops that assertion to match this
      revised text, not a new task.

- [x] **1.12** (GREEN) Implement `backend/app/config.py` (CORS allowlist per ADR-7:
      `https://sismo-cali-dashboard.vercel.app`, `https://formulario-atc20-cali.vercel.app`,
      `allow_origin_regex` for localhost/127.0.0.1) and `backend/app/main.py` `create_app()`: mounts
      `CORSMiddleware` (`allow_credentials=False`, `GET,POST,OPTIONS`, `Authorization,Content-Type`),
      unions mounted routers' `REQUIRED_CLIENTS` and validates at startup, adds `routers/health.py`
      (`GET /health`, no auth). Run 1.11, confirm green.
      — Satisfies: backend-platform "Named-Client Credential Matrix..."; "Universal Explicit CORS
      Allowlist".

- [x] **1.13** (RED) Write `backend/tests/test_cors.py` FIRST: allowed origin gets
      `Access-Control-Allow-Origin`; unlisted origin gets no permitting header; cookie-only request (no
      Bearer) on a stub authenticated route is rejected. Run against 1.12's `create_app()`.
      — Satisfies: backend-platform "Universal Explicit CORS Allowlist" (all 3 scenarios).

- [x] **1.14** Runnable check: `pytest backend/tests/` green (roles parity, verify, startup, CORS,
      health). Zero repoints this slice.
      — Satisfies: design.md ADR-8 (slice 1 red suite green before any admin route moves).

**ROLLBACK BOUNDARY (Slice 1)**: no consumer repointed — rollback is deleting the Railway service /
reverting the PR; zero production impact.

---

## Phase 2 — Slice 2: Photo signer `/api/sign`

Chain PR #2. Depends on: Phase 1 (auth, credentials, CORS, `create_app()`). Lowest blast radius.

- [x] **2.1** (RED) Write `backend/tests/routers/test_sign.py` FIRST: fake/mocked S3 client (no real
      AWS creds in CI); valid Bearer + `codigo` matching `^76001-[123]-\d{7,8}$` + `slot` in
      `1..MAX_SLOT` → 200 `{uploadUrl, publicUrl}`; missing/invalid Bearer → 401; bad `codigo` → 400;
      out-of-range `slot` → 400. MUST fail.
      — Satisfies: inspection-photo-capture "Unified Token Verification For Signer" (both scenarios),
      "Presign Acceptance Semantics Unchanged".
      — STATUS: done. Confirmed RED — `AttributeError: module 'app.credentials.clients' has no
      attribute 's3'` (6 failed, 0 passed) before 2.2 landed. Uses the REAL `credentials.s3()`
      accessor with fake/dummy AWS keys rather than a hand-rolled fake client:
      `generate_presigned_url` is a pure local HMAC computation (confirmed empirically — no network
      call), so this satisfies "no real AWS creds/calls in CI" without extra mocking machinery.

- [x] **2.2** (GREEN) Implement `backend/app/routers/sign.py`: `POST /api/sign`,
      `Depends(require_auth)` — Authorization Bearer header, NOT `idToken` in the JSON body as the
      legacy signer used (`services/photo-signer/api/sign.js:54`); new body is `{codigo, slot}` only.
      Same `CODIGO_RE`/`MAX_SLOT`/presign shape (`key = evaluaciones/{codigo}/foto_{slot}.jpg`,
      `expiresIn: 300`) as `services/photo-signer/api/sign.js:24,71-79`. Add `s3()` accessor to
      `credentials/clients.py` (`SIGNER_AWS_ACCESS_KEY_ID/SECRET`, `SIGNER_S3_BUCKET/REGION`,
      fail-fast). Run 2.1, confirm green.
      — Satisfies: inspection-photo-capture "Unified Token Verification For Signer", "Presign
      Acceptance Semantics Unchanged".
      — STATUS: done. First GREEN pass, no rework (`python -m pytest backend/tests/routers/test_sign.py
      -v` → 6 passed). `s3()` NOT added to `WEB_STARTUP_CLIENTS` — the sign router's own
      `REQUIRED_CLIENTS = ("s3",)` is unioned in automatically by `create_app()` once mounted, which
      already satisfies ADR-4's per-client "fail-fast at web startup" rule. This DID require updating
      two slice-1 fixtures (`test_cors.py`, `test_startup.py`) to also set `SIGNER_*` env vars, since
      `create_app()` now validates `s3` unconditionally — a correct, expected consequence of mounting
      the router, not a regression. Full suite: `python -m pytest backend/tests/ -v` → 45 passed.

- [ ] **2.3** VERIFY (ADR-7 parity procedure): side-by-side same-token calls, old
      (`sismo-fotos-signer.vercel.app`, body-idToken) vs new (Bearer header) — equivalent presigned URL
      for the same `codigo`/`slot`; both reject the same invalid cases. Record both payloads in the PR
      description.
      — Satisfies: backend-platform "Old endpoint still serves after the new one deploys".
      — STATUS: BLOCKED on 1.4 (no live Railway URL exists yet). Repo-side prep done:
      `backend/scripts/verify_sign_parity.py` — a standalone MANUAL operator tool (not imported by
      `app/` or `tests/`, not run in CI) that, given `NEW_SIGN_URL` + `FIREBASE_ID_TOKEN` env vars,
      calls both endpoints side by side (valid request + bad-codigo + bad-token cases), prints both
      payloads for the PR description, and exits non-zero on any parity mismatch. Verified its
      BLOCKED guard runs correctly with no env vars set (exit 2, explanatory message) — no live call
      is possible until 1.4 lands. Run it once 1.4 is done; no further code changes needed for 2.3
      itself.

- [ ] **2.4** REPOINT: `formulario/js/form.js` — `FOTO_SIGNER_URL` → consolidated app base URL;
      `subirUnaFoto` (`form.js:556-575`) changes from `body:{idToken,codigo,slot}` to
      `Authorization: Bearer ${idToken}` header + `body:{codigo,slot}`. MANUAL Vercel redeploy of
      `formulario/`.
      — Satisfies: inspection-photo-capture "FOTO_SIGNER_URL repoint after parity verification".
      — STATUS: BLOCKED on 1.4 + 2.3, `formulario/js/form.js` intentionally NOT touched this batch.
      This apply batch's hard scope boundary was backend-only (`backend/app/routers/sign.py` + tests,
      no consumer switch) — and touching `subirUnaFoto`'s request shape now (Bearer header +
      `{codigo,slot}` body) while `FOTO_SIGNER_URL` still points at the LIVE legacy signer would
      actively break production photo uploads for field inspectors: `services/photo-signer/api/sign.js`
      only reads `idToken` from the JSON body — it has no Bearer-header support — so the two changes
      (URL flip + body/header shape) MUST land atomically, only after 2.3's parity check passes
      against a real `NEW_SIGN_URL`. The exact diff to apply then is already fully specified above
      (this task's own text) — no design work remains, only the repoint + redeploy once unblocked.

- [ ] **2.5** — MANUAL OPERATOR STEP. Confirm the legacy `sismo-fotos-signer.vercel.app` project stays
      deployed, untouched — rollback target until slice 9.
      — Satisfies: inspection-photo-capture "Old signer stays live during transition".
      — STATUS: not started (manual operator confirmation; `services/photo-signer/` was not modified
      by this batch, so nothing on the repo side changed its live status).

**ROLLBACK BOUNDARY (Slice 2)**: revert `FOTO_SIGNER_URL` + `subirUnaFoto`'s body/header shape in one
commit; redeploy `formulario/`. No deletion before slice 9.

**Batch status (sdd-apply, `feat/fastapi-consolidation-2-sign`)**: 2.1/2.2 done (backend route +
tests, 45/45 `backend/tests/` green); 2.3 repo-side prep done (parity script), execution BLOCKED on
1.4; 2.4 BLOCKED on 1.4+2.3 by design — `formulario/` untouched, zero production risk this batch; 2.5
not started (manual). See `apply-progress.md` "Batch 2" for full detail.

---

## Phase 3 — Slice 3: `reportados` (unified day-walk + snapshot)

Chain PR #3 (may need 3a/3b split — see forecast). Depends on: Phase 1. Public route, easy parity diff.

- [x] **3.1** (RED) Write `backend/tests/services/test_atencionsismo.py` FIRST: day-walk/split-retry
      via `httpx.MockTransport` fixtures — split-on-413/500/502/503/504 down to 1-min windows,
      concurrency 4 (ADR-5). Confirm the Basic-auth username constant (design open question 3) matches
      between `scripts/fetch_reportes_api.py` and `api/reportados.js` before extraction; if they
      differ, use the value `api/reportados.js` (currently live) uses. MUST fail.
      — Satisfies: design.md ADR-5 (single implementation, foundation for backend-platform's caching
      requirements).
      — STATUS: done. Confirmed RED — `ImportError: cannot import name 'atencionsismo' from
      'app.services'` (1 collection error) before 3.2 landed. Confirmed the username constant: both
      `api/reportados.js:27` and `scripts/fetch_reportes_api.py:48` hardcode
      `"juanp.gzmz@gmail.com"` — identical, no "JS wins" substitution needed.

- [x] **3.2** (GREEN) Extract `backend/app/services/atencionsismo.py` from
      `scripts/fetch_reportes_api.py` (single implementation; `api/reportados.js`'s JS twin is
      retired, not ported). Run 3.1, confirm green.
      — Satisfies: design.md ADR-5.
      — STATUS: done. First GREEN pass, no rework (32/32). Followed `api/reportados.js`'s (the
      CURRENTLY LIVE implementation) wider `{413,500,502,503,504}` split set, probe, failed-window
      retry pass, and `coordKey`/dedup semantics — NOT `scripts/fetch_reportes_api.py`'s narrower
      `{413,504}` set, per the apply agent's scope instructions.

- [x] **3.3** (RED) Write `backend/tests/services/test_snapshot.py` FIRST: Blob-seed cold start serves
      immediately with age; Blob-seed failure + no completed refresh → 503 + `Retry-After: 60`; snapshot
      older than 86400s → 503; `X-Snapshot-Age` header present. MUST fail.
      — Satisfies: backend-platform "reportados snapshot staleness bound".
      — STATUS: done. Confirmed RED — `ImportError: cannot import name 'snapshot' from
      'app.services'` (1 collection error) before 3.4 landed.

- [x] **3.4** (GREEN) Implement `backend/app/services/snapshot.py`: asyncio task in FastAPI `lifespan`,
      refresh → store `{payload, fetched_at}` → sleep 900s; cold-start best-effort Blob seed (confirm
      exact `reportes*.json` filename/env var per design open question 1 — read `deploy/refresh.sh`'s
      publish step before hardcoding). Run 3.3, confirm green.
      — Satisfies: backend-platform "In-Process Caching Preserves Or Improves Response Behavior".
      — STATUS: done. First GREEN pass, no rework (44/44 combined with 3.1-3.3). Open question 1
      resolved: `deploy/refresh.sh` publishes `data/reportes.json` (raw stripped records, has
      `estadoVerificacion`/`lat`/`lng`), `data/reportes_meta.json` (freshness only, no counts), and
      `data/reportes_agg.json` (aggregations WITHOUT an `inmuebles` coord-dedup field). Chose
      `REPORTES_BLOB_URL` → `reportes.json` as the seed source (new plain env var, full public Blob
      URL, same pattern `INSPECTIONS_URL` already uses for `cruce_sticker`) so the seed reuses
      `atencionsismo.summarize()` — the exact same counting logic a live refresh uses — instead of a
      second parallel aggregation. Design interpretation (flagged in `snapshot.py`'s module
      docstring for verify): the seeded snapshot's age is measured from THIS process's download time,
      not from `reportes_meta.json`'s `generated_at` — simpler, and every reportados spec scenario
      only needs `X-Snapshot-Age` present + the 86400s hard bound enforced, not that the seeded age
      reflect the cron's original publish time.

- [x] **3.5** (GREEN) Implement `backend/app/routers/reportados.py`: `GET /reportados`, no auth,
      serves from snapshot, sets `X-Snapshot-Age` and `Cache-Control: s-maxage=900,
      stale-while-revalidate=86400`.
      — Satisfies: backend-platform "Public route requires no token"; "Cache-Control headers
      preserved".
      — STATUS: done. No dedicated RED task is listed for this router in tasks.md (same gap as
      1.9/1.13 in batch 1b); Strict TDD Mode is active, so `backend/tests/routers/test_reportados.py`
      was written FIRST (confirmed failing — `AttributeError: 'State' object has no attribute
      'reportados_snapshot'` / `ImportError: cannot import name 'reportados'`, 6 failed) before this
      task's GREEN implementation, flagged here rather than silently claimed as paired RED/GREEN.
      `app/main.py` gained a `lifespan` (previously none existed) that best-effort Blob-seeds then
      starts the forever-refresh background task; `app.state.reportados_snapshot` itself is attached
      SYNCHRONOUSLY in `create_app()` (not inside `lifespan`) so router tests can populate it via a
      plain `TestClient(app)`, matching every other router test file in this suite (none use `with
      TestClient(app) as client:`). Manually smoke-tested `with TestClient(app) as client:` end-to-end
      (lifespan startup → `GET /reportados` → 503+Retry-After when misconfigured → clean shutdown) —
      confirmed no hang/crash. Full suite: 95/95 passed.

- [x] **3.6** VERIFY (ADR-5 parity-diff plan): within the same 15-min window, fetch live
      `sismo-cali-dashboard.vercel.app/api/reportados` and the Railway route; compare JSON shape and
      consumed fields (`reportados` total, `inmuebles`) with tolerance for in-flight drift; confirm
      <2s response; record both payloads in the PR description.
      — Satisfies: backend-platform "reportados responds fast from snapshot".
      — STATUS: DONE, PASS (commit `c2fb564`, 2026-08-25). Shape-identical payload (`ok`, `generado`,
      `fuente`, `total`, `inmuebles`, `por_estadoVerificacion`); `Reportado` delta 5, `inmuebles` delta
      6, both within the 50-record drift tolerance; new route measured 0.346s (<2s budget). Ticked here
      after the fact from git history — the 1a/1b/batch-3 apply-progress record never got a follow-up
      entry for the cutover batch that actually ran this.

- [x] **3.7** REPOINT: introduce `web/js/api-config.js` (per-endpoint URL map, default = current
      relative path, per ADR-7) and flip the `reportados` entry to the Railway base URL in
      `web/js/data.js`. MANUAL Vercel redeploy of `web/`.
      — Satisfies: backend-platform "Rollback is a config revert" (establishes the mechanism reused by
      every later repoint).
      — STATUS: DONE (commit `c2fb564`, 2026-08-25). `web/js/api-config.js`'s `reportados` entry now
      points at `https://sismo-cali-dashboard-production.up.railway.app/reportados`;
      `web/js/data.js`'s `refreshReportados()` reads it via `apiUrl('reportados')`. Every other
      `api-config.js` entry stays on its legacy relative path, per ADR-7's per-endpoint repoint
      mechanism — unaffected by this slice. As a scope extension beyond this task's original text,
      commit `acbde37` also widened `/reportados`' `summarize()` to aggregate every analytic field
      (`por_afectacion`/`comuna`/`habitabilidad`/`tipoInmueble` + coordinate coverage + `sin_id`) per a
      user directive, with the legacy consumer fields (`total`, `inmuebles`,
      `por_estadoVerificacion`) left unchanged so `web/js/data.js` needed no further edit for that
      extension. `formulario/`'s own `DASHBOARD_API`/`FOTO_SIGNER_URL` constants remain OUT of
      `api-config.js`'s scope (ADR-7: those slices flip their existing constants directly).

**ROLLBACK BOUNDARY (Slice 3)**: revert the single `reportados` entry in `api-config.js`; redeploy
`web/`. Old `api/reportados.js` untouched.

---

## Phase 4 — Slice 4: `sticker-status` + `source-status`

Chain PR #4. Depends on: Phase 1, Phase 3 (reuses `api-config.js`). Read-only, low logic.

- [ ] **4.1** (RED) Write `backend/tests/routers/test_sticker_status.py` FIRST: any authenticated role
      → 200; cached response within 5-min TTL served without a new Firestore read (call-count fake on
      `sismo()`); unauthenticated → 401. MUST fail.
      — Satisfies: backend-platform "Any-authenticated role-wide route accepts every valid role",
      "sticker-status cache hit within TTL".

- [ ] **4.2** (GREEN) Implement `backend/app/routers/sticker_status.py`: `GET /sticker-status`,
      `Depends(require_auth)`, working 5-min TTL cache (fixes legacy warm-lambda-only caching),
      preserve `Cache-Control`. Run 4.1, confirm green.
      — Satisfies: backend-platform "sticker-status cache hit within TTL", "Cache-Control headers
      preserved".

- [ ] **4.3** (RED) Write `backend/tests/routers/test_source_status.py` FIRST: admin token → 200;
      non-admin → 403, no mutation. MUST fail.
      — Satisfies: backend-platform "Admin-gated route rejects non-admin" (`/source-status`).

- [ ] **4.4** (GREEN) Implement `backend/app/routers/source_status.py`: `GET /source-status`,
      `Depends(require_role("admin"))`, port `api/source-status.js` verbatim. Run 4.3, confirm green.
      — Satisfies: backend-platform "Route Parity Across Consolidated Endpoints" (`/source-status`
      row).

- [ ] **4.5** VERIFY (ADR-7 procedure): side-by-side same-token calls for both routes; record diff.
      — Satisfies: backend-platform "Old endpoint still serves after the new one deploys".

- [ ] **4.6** REPOINT: flip `sticker-status`/`source-status` entries in `api-config.js`. MANUAL Vercel
      redeploy of `web/`.
      — Satisfies: backend-platform "Rollback is a config revert".

**ROLLBACK BOUNDARY (Slice 4)**: revert the two `api-config.js` entries; redeploy `web/`.

---

## Phase 5 — Slice 5: `inspector-asignaciones`

Chain PR #5. Depends on: Phase 1. Completes formulario cutover.

- [ ] **5.1** (RED) Write `backend/tests/routers/test_inspector_asignaciones.py` FIRST: inspector A
      (`sub==uidA`) targeting a point with `inspector_uid==uidB` → rejected, no write; A targeting own
      point → write succeeds; unauthenticated → 401. MUST fail.
      — Satisfies: field-form-session "Cross-inspector access still rejected after migration", "Own-uid
      access still succeeds after migration"; backend-platform "Own-uid-scoped route rejects cross-uid
      access".

- [ ] **5.2** (GREEN) Implement `backend/app/routers/inspector_asignaciones.py`: `POST
      /inspector-asignaciones`, `Depends(require_auth)`, every `sticker_matches` query/write scoped to
      `inspector_uid == token.sub`, port `api/inspector-asignaciones.js`'s `misPuntos`/`marcarHecho`
      dispatch verbatim. First of three modules allowlisted for the `sticker_matches`/`cuadrillas`
      literal (ADR-9). Run 5.1, confirm green.
      — Satisfies: backend-platform "Own-uid-scoped route rejects cross-uid access", "sticker_matches
      And cuadrillas Sole-Writer Invariant".

- [ ] **5.3** (RED) Write `backend/tests/invariants/test_sole_writer.py` FIRST (new file — first
      literal introduced): assert `sticker_matches`/`cuadrillas` appear ONLY in
      `routers/inspector_asignaciones.py`. MUST fail until 5.2 lands (or pass immediately after if
      written post-5.2 — keep RED-before-GREEN by drafting the assertion before confirming the
      allowlist).
      — Satisfies: backend-platform "No write path exists outside the designated two" (partial; closes
      in slices 7/8).

- [ ] **5.4** VERIFY (ADR-7 procedure): side-by-side same-inspector-token calls, both actions; record
      diff.
      — Satisfies: field-form-session "Cross-inspector access still rejected after migration".

- [ ] **5.5** REPOINT: `formulario/js/form.js`'s `DASHBOARD_API` → consolidated app base URL. MANUAL
      Vercel redeploy of `formulario/`.
      — Satisfies: field-form-session "DASHBOARD_API repoint after parity verification"; "CORS Enabled
      For The formulario Origin".

**ROLLBACK BOUNDARY (Slice 5)**: revert `DASHBOARD_API`; redeploy `formulario/`.

---

## Phase 6 — Slice 6: `refresh` endpoint

Chain PR #6. Depends on: Phase 1. Re-wires the Vercel↔Railway GraphQL coupling.

- [ ] **6.1** (RED) Write `backend/tests/routers/test_refresh.py` FIRST: admin token → 202 with
      `deploymentId` (mocked Railway GraphQL client, `dashboard-refresh` service only — NO
      `cruceDeploymentId`/cruce-gestion trigger, per proposal.md Scope Exclusion Addendum Extension 2
      item 5); non-admin → 403, no Railway call. MUST fail.
      — Satisfies: backend-platform "Admin-gated route rejects non-admin" (`/refresh`).

- [ ] **6.2** (GREEN) Implement `backend/app/routers/refresh.py`: `POST /refresh`,
      `Depends(require_role("admin"))`, port `api/refresh.js:134-181`'s dual-header Railway auth
      fallback, triggering ONLY the `dashboard-refresh` service redeploy — the legacy fail-soft
      cruce-gestion redeploy branch is NOT ported (cruce-gestion is excluded from migration). Update
      the service/environment id to the NEW consolidated `dashboard-refresh` Railway service id
      created in slice 7 — confirm exact id before hardcoding. Run 6.1, confirm green.
      — Satisfies: backend-platform "Route Parity Across Consolidated Endpoints" (`/refresh` row).

- [ ] **6.3** VERIFY (ADR-7 procedure, mutating-action carve-out — redeploy trigger is idempotent
      enough to exercise live): admin-token POST old vs new; both 202, `deploymentId` present (old
      response's `cruceDeploymentId` field has no new-side equivalent — expected, documented
      difference, not a parity failure).
      — Satisfies: backend-platform "Old endpoint still serves after the new one deploys".

- [ ] **6.4** REPOINT: flip the `refresh` entry in `api-config.js`. MANUAL Vercel redeploy of `web/`.
      — Satisfies: backend-platform "Rollback is a config revert".

**ROLLBACK BOUNDARY (Slice 6)**: revert the `refresh` entry; redeploy `web/`.

---

## Phase 7 — Slice 7 + 7b: Crons per-job + `survey_cali` ingestion

Depends on: Phase 1. Can interleave from slice 2 onward per proposal; sequenced here to match the PR
chain. **Migrated set is TWO job services**: `dashboard-refresh`, `cruce-sticker` (per Task 0.1's
resolution and proposal.md Scope Exclusion Addendum Extension 2 — `integracion-f3`, `asignaciones`,
and `cruce-gestion` are ALL excluded; see 7.7, 7.10, 7.11). `cruce-gestion` is excluded because its
sole purpose was writing Firestore `dagma-85aad`/`cruce_criticos_survey`, and nothing dagma-related is
used anywhere in the new backend. **Recommend per-job sub-PRs** (7a-7c, see forecast) — each
independently mergeable once Phase 1 lands. `dashboard-refresh` first (code already in this repo).

- [ ] **7.1** (RED) Write `backend/tests/jobs/test_dashboard_refresh.py` FIRST: offline `--check`-style
      idempotency/watermark fixtures for `refresh_data.py` + `fetch_reportes_api.py` (now calling
      3.2's `services/atencionsismo.py`). MUST fail.
      — Satisfies: job-scheduling "Re-running a job does not duplicate output" (dashboard-refresh row).

- [ ] **7.2** (GREEN) Implement `backend/app/jobs/dashboard_refresh.py`: `runlog.resolve_log_dir()` →
      `start_tee` → run `refresh_data.py` + `fetch_reportes_api.py` → `append_run(...)`; preserve
      `deploy/refresh.sh`'s timeout/meta-guard/trap structure (no more clone-at-start
      `entrypoint.sh`/`DASHBOARD_REPO_TOKEN` — code is in the image). Run 7.1, confirm green.
      — Satisfies: job-scheduling "dashboard-refresh needs no cross-repo absorption", "Watermark And
      Idempotent-Write Behavior Preserved".

- [ ] **7.3** (RED) Write `backend/tests/services/test_survey_cali.py` FIRST (mutation core only,
      ADR-10/11/12 — router lands in 8b): `apply_mutation` diff/revision computation; ingest
      idempotency (same input twice → zero new writes/revisions, hash equality); per-field conflict
      rule (manual edit on an unchanged-upstream field survives ingest; upstream change on a
      manually-edited field overwrites it, records `kind:'ingest'` with `before` = the manual value);
      first-run `kind:'create'`. MUST fail.
      — Satisfies: survey-cali-collection "Unchanged record is skipped", "Changed record is upserted by
      GlobalID", "A run never rewrites the full collection", "Ingest update writes a pipeline-authored
      revision", "History is never destroyed", "Manual edit survives an unrelated ingest run", "Source
      move overwrites a manually-edited field, visibly".

- [ ] **7.4** (GREEN) Implement `backend/app/services/survey_cali.py`:
      `apply_mutation(id, changes, author, kind, revert_of=None)` per ADR-12 (Firestore transaction:
      read → diff → write effective fields + `_rev+1` + history doc atomically); document shape per
      ADR-10 (`_rev`, `_updated_at`, `_updated_by`, `_source`, `_source_hash`, `_deleted`;
      `history/{rev_NNNNNN}`). One of three modules allowlisted for the `survey_cali` literal (ADR-9).
      Run 7.3, confirm green.
      — Satisfies: survey-cali-collection "Append-Only Per-Record Revision History" (all 3 scenarios).

- [ ] **7.5** (GREEN) Wire ingestion into 7.2's `dashboard_refresh.py`: after the Survey123 fetch, per
      record build the canonical normalized form → SHA-256 → compare to `_source_hash`; skip on
      equality; else diff each field against `_source[field]`, write only changed fields via
      `apply_mutation(..., author='pipeline', kind='ingest')` (design open question 4: confirm at
      implementation time whether hashing uses RAW upstream fields only per ADR-11's recommendation —
      default to RAW unless the check finds a reason otherwise). Batch current-doc reads via `get_all`
      in chunks; outer batching ≤500 ops. Update `_meta/survey_cali_ingest_state`.
      — Satisfies: survey-cali-collection "Unchanged record is skipped", "Changed record is upserted by
      GlobalID", "A run never rewrites the full collection", "Manual edit survives an unrelated ingest
      run", "Source move overwrites a manually-edited field, visibly".

- [ ] **7.6** (RED) Extend `test_sole_writer.py`: assert `survey_cali` literal appears ONLY in
      `services/survey_cali.py`, `routers/survey_cali.py` (allowlist entry now, router lands 8b),
      `app/jobs/dashboard_refresh.py`.
      — Satisfies: design.md ADR-9 (survey_cali sole-writer treatment).

- [x] **7.7** (RESOLVED — `cruce-gestion` EXCLUDED) Per the user's binding directive "no usar nada
      relacionado con el dagma" (proposal.md Scope Exclusion Addendum Extension 2): `cruce-gestion`
      does NOT migrate. Its sole purpose is writing Firestore `dagma-85aad`/`cruce_criticos_survey` via
      the `dagma()` client, which is removed from the new backend entirely (see 1.10) — there is no
      dagma-free version of this job to port. No RED/GREEN tasks, no absorption of
      `cruce_criticos_survey.py` into `backend/app/integracion/`, no Railway cron service on the
      consolidated image. It joins `normalizador`, `integracion-f3`, `asignaciones` in the excluded
      set, staying on the legacy `integracion_F1` image/service (`job_cruce.py`) until decommissioned
      in slice 9 (task 9.8) pending explicit operator confirmation.
      — Satisfies: proposal.md Scope Exclusion Addendum Extension 2 items 1-2.

- [ ] **7.8** (RED) Write `backend/tests/jobs/test_cruce_sticker.py` FIRST: port the offline `--check`
      fixture already established in `integracion_F1/cruce_sticker.py` (stickers-asignacion change) —
      same pipeline-owned merge-safety/first-write assertions, targeting the new
      `backend/app/jobs/cruce_sticker.py` location. MUST fail.
      — Satisfies: job-scheduling "Watermark And Idempotent-Write Behavior Preserved" (cruce-sticker
      row); backend-platform "sticker_matches And cuadrillas Sole-Writer Invariant" (job side).

- [ ] **7.9** (GREEN) Absorb `integracion_F1/cruce_sticker.py` into `backend/app/integracion/` +
      `backend/app/jobs/cruce_sticker.py` (ADR-2 provenance; no gspread — confirmed clean). Third
      module allowlisted for `sticker_matches`/`cuadrillas` (with 5.2, and slice 8's
      `sticker_asignaciones.py`) — extend `test_sole_writer.py`. `sismo()` client (fail-fast). Run 7.8,
      confirm green.
      — Satisfies: job-scheduling "integracion_F1 Job Code Absorbed With Provenance".

- [x] **7.10** (RESOLVED — `integracion-f3` EXCLUDED) Per Task 0.1's escalation and the user's final
      decision (proposal.md Scope Exclusion Addendum Extension, 2026-08-25 post-tasks): `integracion-f3`
      does NOT migrate. Its gspread I/O (F3_SRC_TAB read, DST_TAB write) is confirmed operationally dead
      and is not replaced — the job joins `normalizador`/`cruce-gestion` in the excluded set. No
      RED/GREEN tasks, no absorption into `backend/app/integracion/`, no Railway cron service on the
      consolidated image. It stays on the legacy `integracion_F1` image/service (`job_integrar_f3.py`,
      `python job_integrar_f3.py`) until decommissioned in slice 9 (task 9.6) pending explicit operator
      confirmation.
      — Satisfies: design.md open question 6 (resolved); Scope Exclusion Addendum Extension items 1-2.

- [x] **7.11** (RESOLVED — `asignaciones` EXCLUDED) Same resolution as 7.10 for `asignar_f3.py`: does
      NOT migrate, gspread I/O confirmed dead, no replacement, joins `normalizador`/`cruce-gestion`/
      `integracion-f3` in the excluded set. Stays on the legacy `integracion_F1` image/service
      (`job_asignaciones.py`) until decommissioned in slice 9 (task 9.7) pending explicit operator
      confirmation.
      — Satisfies: design.md open question 6 (resolved); Scope Exclusion Addendum Extension items 1-2.

- [ ] **7.12** Implement/update `backend/scripts/railway_services.py` (ADR-6, replaces
      `integracion_F1/scripts/railway_setup.py` as source of truth): drift-only LIST/INSTANCE/UPDATE
      GraphQL, `SERVICES` rows for exactly the TWO migrated jobs — `dashboard-refresh`,
      `cruce-sticker` (schedules per job-scheduling spec table). No rows for
      `integracion-f3`/`asignaciones`/`cruce-gestion` — per 7.7/7.10/7.11 none of them ever move to
      this script. Delete the migrated rows from `integracion_F1/scripts/railway_setup.py` in the SAME
      PR per job; `normalizador`'s, `integracion-f3`'s, `asignaciones`', and `cruce-gestion`'s rows
      stay untouched there.
      — Satisfies: job-scheduling "Per-Job Schedule Parity", "Drift-Only Provisioning Convention
      Preserved".

- [ ] **7.13** — MANUAL OPERATOR STEP. Create Railway cron services for the TWO migrated jobs
      (`dashboard-refresh`, `cruce-sticker`), git-connected, same pinned `dockerfilePath`,
      `startCommand: python -m app.jobs.<job>`, schedules from 7.12. Provision per-job env vars per
      ADR-6 table (`cruce-sticker` needs only `FIREBASE_SERVICE_ACCOUNT_JSON`/`INSPECTIONS_URL` — no
      `GOOGLE_SERVICE_ACCOUNT_JSON`). No service is created for
      `integracion-f3`/`asignaciones`/`cruce-gestion`.
      — Satisfies: job-scheduling "Per-Job Schedule Parity"; backend-platform "Deploy triggers from a
      git push".

- [ ] **7.14** VERIFY: manually trigger each new cron service once; confirm `runs.jsonl` on the mounted
      volume shows a successful run; confirm `cruce-sticker` resumes from its existing
      `_meta/cruce_sticker_state` watermark.
      — Satisfies: job-scheduling "cruce-sticker resumes from watermark after migration", "Re-running a
      job does not duplicate output".

- [ ] **7.15** Delete each migrated job's row from `integracion_F1/scripts/railway_setup.py` (same PR
      as that job's migration); pause/delete its OLD Railway cron service only after 7.14 verifies the
      new one green.
      — Satisfies: job-scheduling "Per-Job Independent Rollback".

**ROLLBACK BOUNDARY (Slice 7/7b)**: each of the two migrated jobs rolls back independently — repoint
its Railway service back to the old image/command; web service and the other job unaffected.
`integracion-f3`/`asignaciones`/`cruce-gestion` never leave the legacy `integracion_F1` service in this
slice (excluded per 7.7/7.10/7.11) — no rollback applies to them until their slice 9 decommission.

---

## Phase 8 — Slice 8 + 8b: Admin CRUD + `survey_cali` CRUD/history/revert

Depends on: Phase 1, patterns from Phase 5/7, Phase 7's `services/survey_cali.py` (8b reuses
`apply_mutation`). **Recommend per-area sub-PRs** (8a stickers+sticker-asignaciones, 8b-admin usuarios,
8c survey_cali — see forecast). Heaviest auth logic, moves last.

- [ ] **8.1** (RED) Write `backend/tests/routers/test_stickers.py` FIRST: admin token → CRUD actions
      succeed; non-admin → 403, no mutation. Port the exact action set from `api/stickers.js`. MUST
      fail.
      — Satisfies: backend-platform "Admin-gated route rejects non-admin" (`/stickers`).

- [ ] **8.2** (GREEN) Implement `backend/app/routers/stickers.py`: `POST /stickers`,
      `Depends(require_role("admin"))`, port `api/stickers.js` handler verbatim (client access via
      `credentials.sismo()`). Run 8.1, confirm green.
      — Satisfies: backend-platform "Route Parity Across Consolidated Endpoints" (`/stickers` row).

- [ ] **8.3** (RED) Write `backend/tests/routers/test_sticker_asignaciones.py` FIRST: port the 8-action
      matrix from `api/sticker-asignaciones.test.js` (`autoAgrupar` determinism/maxSize/maxRadius/
      empty-input; `listPuntos`/`listCuadrillas`; `crearCuadrilla`; `editarCuadrilla`;
      `asignarInspector`; `reasignarPunto`; `eliminarCuadrilla` clears membership before delete) as
      pytest cases. MUST fail.
      — Satisfies: backend-platform "sticker_matches And cuadrillas Sole-Writer Invariant" (route side).

- [ ] **8.4** (GREEN) Implement `backend/app/routers/sticker_asignaciones.py`: port
      `api/sticker-asignaciones.js` verbatim (all 8 actions incl. pure `autoAgrupar`/`haversineM`).
      Fourth and final module allowlisted for `sticker_matches`/`cuadrillas` — extend
      `test_sole_writer.py` to its final set (`sticker_asignaciones.py`, `inspector_asignaciones.py`,
      `jobs/cruce_sticker.py`). Run 8.3, confirm green.
      — Satisfies: backend-platform "No write path exists outside the designated two" (final closure).

- [ ] **8.5** (RED) Write `backend/tests/routers/test_usuarios.py` FIRST: port `api/usuarios.test.js`'s
      full fixture matrix verbatim (`classify` precedence incl. claim-override; `checkDeleteGuards`
      last-enabled-admin block, non-admin delete allowed, second-admin unblocks, self-uid delete always
      blocked; `isValidPassword` bounds) plus the extra gate (`api/usuarios.js:200-201`: acting admin's
      provider MUST be `password` AND email MUST NOT be under `@sismocali.gov.co`, byte-for-byte per
      design open question 2). MUST fail.
      — Satisfies: backend-platform "usuarios endpoint enforces its extra provider/domain gate".

- [ ] **8.6** (GREEN) Implement `backend/app/routers/usuarios.py`: `POST /usuarios`,
      `Depends(require_role("admin"))` + 8.5's extra gate, port `api/usuarios.js` handler
      (create/list/setPassword/delete) verbatim incl. `checkDeleteGuards`/`isValidPassword`/`classify`.
      Run 8.5, confirm green.
      — Satisfies: backend-platform "usuarios endpoint enforces its extra provider/domain gate",
      "Route Parity Across Consolidated Endpoints" (`/usuarios` row).

- [ ] **8.7** VERIFY (ADR-7 procedure, admin-POST carve-out): read-only actions (`listPuntos`,
      `listCuadrillas`, `usuarios` list) live side-by-side; mutating actions verified by
      8.1/8.3/8.5's pytest suites + one manual production smoke test per repoint.
      — Satisfies: backend-platform "Old endpoint still serves after the new one deploys".

- [ ] **8.8** REPOINT: flip `stickers`, `sticker-asignaciones`, `usuarios` entries in `api-config.js`.
      MANUAL Vercel redeploy of `web/`.
      — Satisfies: backend-platform "Rollback is a config revert".

- [ ] **8.9** (RED) Write `backend/tests/routers/test_survey_cali.py` FIRST: non-admin → 403, no state
      change (all 7 routes); `PATCH` merge-only (`{a:1,b:2}`+`{b:3}` → `{a:1,b:3}`); no-op `PATCH` →
      200, zero new revision; underscore-prefixed metadata rejected by schema; `GET /survey-cali`
      excludes `_deleted`, never embeds history; `GET /survey-cali/{id}/history` returns all revisions
      newest-first; `POST /survey-cali/{id}/revert {rev}` → new revision, current state matches target
      rev, prior revisions unchanged. MUST fail.
      — Satisfies: survey-cali-collection "Non-admin call is rejected", "Update is a merge, not a
      replace", "Admin update writes a uid-authored revision", "Listing history returns all revisions
      in order", "Viewing a revision shows its changed fields", "Revert creates a new revision instead
      of mutating history", "Default list omits history", "History is available on explicit request".

- [ ] **8.10** (GREEN) Implement `backend/app/routers/survey_cali.py` per ADR-12: `GET/POST
      /survey-cali`, `GET/PATCH/DELETE /survey-cali/{id}`, `GET /survey-cali/{id}/history`, `POST
      /survey-cali/{id}/revert`; all `Depends(require_role("admin"))`; all mutations go through 7.4's
      `apply_mutation` — no direct Firestore write in this router. Pick a history-list page size
      (design open question 5) and document the default. Run 8.9, confirm green.
      — Satisfies: survey-cali-collection (all requirements, route layer); design.md ADR-12.

- [ ] **8.11** (RED) Finalize `test_sole_writer.py`'s `survey_cali` allowlist to its closed set
      (`services/survey_cali.py`, `routers/survey_cali.py`, `app/jobs/dashboard_refresh.py`); confirm
      no other module references the literal.
      — Satisfies: design.md ADR-9 (survey_cali sole-writer, final closure).

- [ ] **8.12** VERIFY: exercise create → patch → delete → revert round-trip against a real/emulated
      `sismo-agosto-sgred` Firestore project; confirm history append-only (N → N+1, none altered) and
      the default list never returns `_deleted` docs or an embedded history array.
      — Satisfies: survey-cali-collection "History is never destroyed", "Default list omits history".

**ROLLBACK BOUNDARY (Slice 8/8b)**: revert the three `api-config.js` entries (stickers,
sticker-asignaciones, usuarios); redeploy `web/`. `survey_cali` CRUD/history/revert is a NEW capability
with no legacy consumer — rollback is disabling the router, zero effect elsewhere (dashboard UI is a
separate future change).

---

## Phase 9 — Slice 9: Decommission (terminal cleanup)

Depends on: ALL prior slices verified (every consumer repointed, every job migrated or excluded).

- [ ] **9.1** Confirm `proposal.md`'s Success Criteria checklist (all consumers repointed; parity green
      per endpoint; 2 migrated crons — `dashboard-refresh`, `cruce-sticker` — running from the
      git-connected image on schedule; `roleFrom` parity suite green; zero production downtime record)
      before deleting anything.
      — Satisfies: proposal.md Success Criteria.

- [ ] **9.2** Delete `api/*.js` (all 8 legacy Vercel functions) and their `.test.js` files.
      — Satisfies: proposal.md Success Criteria; design.md ADR-8 (legacy Node tests retire alongside
      their endpoint).

- [ ] **9.3** Delete `services/photo-signer/`. MANUAL: pause/delete its Vercel project via dashboard.
      — Satisfies: proposal.md Success Criteria.

- [ ] **9.4** Confirm no CLI-upload Railway cron services remain for the 1 already-migrated
      `integracion_F1` job (`cruce-sticker`; superseded in 7.15 — `dashboard-refresh`'s code already
      lived in this repo, not `integracion_F1`); confirm `integracion_F1/scripts/railway_setup.py`
      retains exactly four rows at this point — `normalizador`, `integracion-f3`, `asignaciones`,
      `cruce-gestion` — and no others.
      — Satisfies: proposal.md Success Criteria.

- [ ] **9.5** — MANUAL OPERATOR STEP, EXPLICIT CONFIRMATION REQUIRED. Decommission the `normalizador`
      Railway service ONLY after explicit operator confirmation the EDAN Google Sheet is no longer
      consulted (Scope Exclusion Addendum item 2). Standalone sign-off, not bundled into any automated
      task.
      — Satisfies: job-scheduling "normalizador stays on the legacy service through slice 9",
      "normalizador decommission requires explicit operator confirmation".

- [ ] **9.6** — MANUAL OPERATOR STEP, EXPLICIT CONFIRMATION REQUIRED. Decommission the `integracion-f3`
      Railway service ONLY after explicit operator confirmation the F3 Google Sheet input/output
      (`F3_SPREADSHEET_ID`/`F3_SRC_TAB`/`DST_TAB`) is no longer consulted by anyone (Scope Exclusion
      Addendum Extension). Standalone sign-off, separate checkbox from 9.5/9.7/9.8.
      — Satisfies: Scope Exclusion Addendum Extension items 2, 4.

- [ ] **9.7** — MANUAL OPERATOR STEP, EXPLICIT CONFIRMATION REQUIRED. Decommission the `asignaciones`
      Railway service ONLY after explicit operator confirmation its F3/VISITAS Sheets output is no
      longer consulted by anyone (Scope Exclusion Addendum Extension). Standalone sign-off, separate
      checkbox from 9.5/9.6/9.8.
      — Satisfies: Scope Exclusion Addendum Extension items 2, 4.

- [ ] **9.8** — MANUAL OPERATOR STEP, EXPLICIT CONFIRMATION REQUIRED. Decommission the `cruce-gestion`
      Railway service ONLY after explicit operator confirmation that nothing still depends on its
      `dagma-85aad`/`cruce_criticos_survey` Firestore writes (Scope Exclusion Addendum Extension 2).
      Standalone sign-off, separate checkbox from 9.5/9.6/9.7. Once 9.5-9.8 all confirm,
      `integracion_F1` is no longer required as a deploy unit for anything.
      — Satisfies: Scope Exclusion Addendum Extension 2 items 1-2.

- [ ] **9.9** Final parity sign-off: re-run every slice's VERIFY task (2.3, 3.6, 4.5, 5.4, 6.3, 7.14,
      8.7, 8.12) once more against the fully-decommissioned state.
      — Satisfies: proposal.md Success Criteria; Rollback Plan.

**ROLLBACK BOUNDARY (Slice 9)**: NONE — terminal cleanup. Per `proposal.md`'s Rollback Plan, rollback
stops being a config revert once old code is deleted. Do not merge 9.2-9.8 until 9.1 is fully green.

---

## Review Workload Forecast

| Slice | Est. changed lines | 400-line risk | Suggested split |
|---|---|---|---|
| 0 | ~0 (investigation only) | Low | Fold into Slice 7's PR description |
| 1 | ~700-850 | High | 1a: skeleton+Dockerfile+config+credentials+CORS+main (~350-400) / 1b: auth verify+roles+deps+parity+verify tests (~350-450) |
| 2 | ~180-230 | Low | Single PR |
| 3 | ~350-450 | Medium-High | 3a: atencionsismo service+tests (~200) / 3b: snapshot+router+tests+repoint (~200-250) if it runs high |
| 4 | ~180-230 | Low | Single PR |
| 5 | ~150-200 | Low | Single PR |
| 6 | ~130-170 | Low | Single PR |
| 7/7b | ~650-800 | Medium | 7a dashboard-refresh / 7b survey_cali ingestion / 7c cruce-sticker+railway_services.py (2-service SERVICES table, no `integracion-f3`/`asignaciones`/`cruce-gestion` rows — no code for any of the three excluded jobs) |
| 8/8b | ~950-1150+ | High | 8a stickers+sticker-asignaciones / 8b-admin usuarios / 8c survey_cali CRUD/history/revert |
| 9 | ~1000-1500 (mostly deletions) | High (line count) / Low (cognitive load) | 9a api/*.js+tests delete / 9b photo-signer delete / 9c legacy railway_setup.py cleanup / 9d-9g normalizador/integracion-f3/asignaciones/cruce-gestion manual sign-offs (no diff, 4 separate checkboxes) |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

`Decision needed before apply` is `No` because `delivery_strategy=auto-chain` already resolves the
top-level slice chaining; the sub-splits above are guidance for `sdd-apply` to apply autonomously
within each oversized slice, following the same stacked-to-main principle (each sub-PR merges to main
in order, independently reviewable, independently rollback-able). Total estimated lines across all
slices: roughly 4200-5700 authored/deleted lines (down again — Slice 1 drops the `dagma()` client,
Slice 6 drops the fail-soft `cruce-gestion` redeploy branch, and Slice 7 now carries only TWO migrated
jobs' implementation code) — this is a large multi-slice migration by nature (proposal's own 9-slice
plan), not a sizing mistake; the per-slice/sub-slice splits keep every individual PR at or near the
400-line single-lens-review budget.

**Task 0.1's escalation is RESOLVED, twice over**: the user first decided `integracion-f3` and
`asignaciones` join `normalizador` in the excluded set (proposal.md Scope Exclusion Addendum
Extension, 2026-08-25 post-tasks), then directed "no usar nada relacionado con el dagma" (Extension 2,
2026-08-25 post-slice-1a), which additionally excludes `cruce-gestion` and removes the `dagma()`
client from the credentials module entirely. The excluded legacy set is now FOUR jobs
(`normalizador`, `integracion-f3`, `asignaciones`, `cruce-gestion`); the migrated set is TWO
(`dashboard-refresh`, `cruce-sticker`). No outstanding decision gate remains before Slice 6, Slice 7,
or Slice 9 can proceed.
