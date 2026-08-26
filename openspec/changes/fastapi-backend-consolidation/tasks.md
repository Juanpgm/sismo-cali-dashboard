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
      — STATUS: RUN (2026-08-26) against the live Railway URL with a real inspector token — STRUCTURAL
      tier PASS (identical rejection behavior on missing/invalid Bearer, bad `codigo`; the
      missing-slot case diverges 400-vs-422 as already documented — JS hand-checks the body, FastAPI's
      pydantic validation rejects first, a KNOWN/expected divergence, not a new finding). **TOKEN tier
      FOUND A REAL BUG, not a parity gap**: the new route returns 200 with a syntactically valid
      presigned URL, but that URL fails a real `PUT` with S3 `403 InvalidAccessKeyId` — the
      `SIGNER_AWS_ACCESS_KEY_ID`/`SIGNER_AWS_SECRET_ACCESS_KEY` values provisioned on the Railway "web"
      service (task 1.4's manual step) do not match a real AWS IAM credential, unlike the legacy
      signer's (which uploaded successfully in the same test run). This is NOT fixable by any
      automated apply batch — the correct values live only in the legacy Vercel project's env config
      (`sismo-fotos-signer`) and must be copied into Railway's dashboard by a human with access to
      both. BLOCKED on that credential fix, not on a missing token (1.4 and 2.3's own script both
      function correctly — the underlying S3 access key is simply wrong).

- [ ] **2.4** REPOINT: `formulario/js/form.js` — `FOTO_SIGNER_URL` → consolidated app base URL;
      `subirUnaFoto` (`form.js:556-575`) changes from `body:{idToken,codigo,slot}` to
      `Authorization: Bearer ${idToken}` header + `body:{codigo,slot}`. MANUAL Vercel redeploy of
      `formulario/`.
      — Satisfies: inspection-photo-capture "FOTO_SIGNER_URL repoint after parity verification".
      — STATUS: BLOCKED on 2.3's real finding (invalid S3 credentials on Railway), NOT on 1.4 (done)
      or on a missing token (2.3 was actually run). Repointing now would break production photo
      uploads for field inspectors the moment `formulario/` redeploys — every upload would 403 against
      S3. Fix the `SIGNER_AWS_*` values on Railway's "web" service first (see 2.3), re-run
      `verify_sign_parity.py`'s TOKEN tier to confirm a real `PUT` succeeds, THEN apply this repoint.
      The exact diff is already fully specified above (this task's own text) — no design work remains.

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

- [x] **4.1** (RED) Write `backend/tests/routers/test_sticker_status.py` FIRST: any authenticated role
      → 200; cached response within 5-min TTL served without a new Firestore read (call-count fake on
      `sismo()`); unauthenticated → 401. MUST fail.
      — Satisfies: backend-platform "Any-authenticated role-wide route accepts every valid role",
      "sticker-status cache hit within TTL".
      — STATUS: done. Confirmed RED — 3 failed, all `404 Not Found` (route did not exist yet) — before
      4.2 landed.

- [x] **4.2** (GREEN) Implement `backend/app/routers/sticker_status.py`: `GET /sticker-status`,
      `Depends(require_auth)`, working 5-min TTL cache (fixes legacy warm-lambda-only caching),
      preserve `Cache-Control`. Run 4.1, confirm green.
      — Satisfies: backend-platform "sticker-status cache hit within TTL", "Cache-Control headers
      preserved".
      — STATUS: done. First GREEN pass, no rework (3/3, full suite 100/100). Cache is attached to
      `app.state` (one instance per `create_app()` call, same convention as `reportados_snapshot`)
      instead of a module-level variable — this is the actual fix for the legacy warm-lambda-only
      guarantee, not a cosmetic port. **Deviation flagged for verify**: confirmed by reading
      `api/sticker-status.js` in full (and `vercel.json`, whose `headers` block only covers static
      `/data/*.json` files) that the legacy handler sets NO `Cache-Control` header at all — this
      task's "preserve `Cache-Control`" text does not match the legacy source for THIS route (it
      matches `source-status`'s `private, no-store`, ported in 4.4 instead). Implemented with no
      `Cache-Control` header, which IS the verbatim-parity behavior; the spec's own "Cache-Control
      headers preserved" scenario (`spec.md:145-149`) is scoped to `reportados`'s
      `s-maxage=900`, not this route, confirming no header is the correct target.

- [x] **4.3** (RED) Write `backend/tests/routers/test_source_status.py` FIRST: admin token → 200;
      non-admin → 403, no mutation. MUST fail.
      — Satisfies: backend-platform "Admin-gated route rejects non-admin" (`/source-status`).
      — STATUS: done. Confirmed RED — 4 failed, all `404 Not Found` — before 4.4 landed.

- [x] **4.4** (GREEN) Implement `backend/app/routers/source_status.py`: `GET /source-status`,
      `Depends(require_role("admin"))`, port `api/source-status.js` verbatim. Run 4.3, confirm green.
      — Satisfies: backend-platform "Route Parity Across Consolidated Endpoints" (`/source-status`
      row).
      — STATUS: done. First GREEN pass after fixing a test-fixture typo (fake admin claims used a
      nested `customClaims.role` shape; `role_from_claims` reads a top-level `role` key — caught here
      because this route's non-admin/admin split actually exercises role resolution, unlike 4.1's
      `require_auth`, which doesn't care about role). 4/4, full suite 104/104. Ports the legacy
      handler's `private, no-store` `Cache-Control` on both `ok:true` and `ok:false` branches
      (verbatim, `api/source-status.js:66,69`) and its always-200-never-5xx shape for upstream
      failures.

- [x] **4.5** VERIFY (ADR-7 procedure): side-by-side same-token calls for both routes; record diff.
      — Satisfies: backend-platform "Old endpoint still serves after the new one deploys".
      — STATUS: DONE, PASS (2026-08-26). `sticker-status`: exact-match payload against a real inspector
      token — `total: 1101, con: 323` identical on both old and new, `200`/`200`. `source-status`:
      exact-match shape against a synthetic admin-claim token (`{ok:true, status:"conectado",
      checked_at}`, `200`/`200`); non-admin correctly `403` on both sides (message text differs —
      `"Solo administradores pueden ver el estado de las fuentes."` vs `"No autorizado."` — a known,
      acceptable divergence, not a shape mismatch). Structural tier (no-auth/bad-token 401) also PASS
      for both routes.

- [x] **4.6** REPOINT: flip `sticker-status`/`source-status` entries in `api-config.js`. MANUAL Vercel
      redeploy of `web/`.
      — Satisfies: backend-platform "Rollback is a config revert".
      — STATUS: DONE (2026-08-26), gated on 4.5's PASS. Both `api-config.js` entries flipped to the
      Railway base URL; `web/js/main.js:130` now reads `apiUrl('stickerStatus')` instead of the
      hardcoded `/api/sticker-status`, and `web/js/analista.js`'s `SOURCE_STATUS_ENDPOINT` now resolves
      via `apiUrl('sourceStatus')` at module load instead of a hardcoded string — the exact two-step
      pattern slice 3's cutover used for `data.js`. `node --check` on all three edited files passed;
      Vercel redeploy of `web/` still pending (push + `vercel:deploy` command, not yet run this batch).

**ROLLBACK BOUNDARY (Slice 4)**: revert the two `api-config.js` entries; redeploy `web/`.

**Batch status (sdd-apply, `feat/fastapi-consolidation-4-status`)**: 4.1-4.6 ALL DONE — both routers +
tests (113/113 `backend/tests/` green at merge), 4.5 parity PASS against the live Railway routes with a
real inspector token + a synthetic admin-claim token, 4.6 repoint applied (`api-config.js` flipped,
`main.js`/`analista.js` wired through `apiUrl()`). See `apply-progress.md` "Batch 4" and the top-level
"Live verification" note for full detail.

---

## Phase 5 — Slice 5: `inspector-asignaciones`

Chain PR #5. Depends on: Phase 1. Completes formulario cutover.

- [x] **5.1** (RED) Write `backend/tests/routers/test_inspector_asignaciones.py` FIRST: inspector A
      (`sub==uidA`) targeting a point with `inspector_uid==uidB` → rejected, no write; A targeting own
      point → write succeeds; unauthenticated → 401. MUST fail.
      — Satisfies: field-form-session "Cross-inspector access still rejected after migration", "Own-uid
      access still succeeds after migration"; backend-platform "Own-uid-scoped route rejects cross-uid
      access".
      — STATUS: done. Confirmed RED — 7 of 9 failed (all router cases `404 Not Found`, route did not
      exist yet); 2 passed coincidentally (nonexistent-point case expects 404 and got it for the wrong
      reason — the whole route was 404; the cuadrillas invariant test legitimately passed on an empty
      hit set) before 5.2 landed. 7 test cases total, incl. two beyond the task's literal 3 (missing
      punto_id, unrecognized action) for full dispatch coverage.

- [x] **5.2** (GREEN) Implement `backend/app/routers/inspector_asignaciones.py`: `POST
      /inspector-asignaciones`, `Depends(require_auth)`, every `sticker_matches` query/write scoped to
      `inspector_uid == token.sub`, port `api/inspector-asignaciones.js`'s `misPuntos`/`marcarHecho`
      dispatch verbatim. First of three modules allowlisted for the `sticker_matches`/`cuadrillas`
      literal (ADR-9). Run 5.1, confirm green.
      — Satisfies: backend-platform "Own-uid-scoped route rejects cross-uid access", "sticker_matches
      And cuadrillas Sole-Writer Invariant".
      — STATUS: done. First GREEN pass, no rework — `python -m pytest backend/tests/routers/
      test_inspector_asignaciones.py backend/tests/invariants/test_sole_writer.py -v` → 9 passed. Route
      has NO `/api` prefix (matches the field-form-session/backend-platform spec deltas' own scenario
      text and the `reportados`/`sticker-status`/`source-status` precedent, unlike `/api/sign`) — see
      5.5's BLOCKED finding for what that means for the eventual repoint. Full suite:
      `python -m pytest backend/tests/ -v` → 106 passed (97 baseline on `main` + 9 new).

- [x] **5.3** (RED) Write `backend/tests/invariants/test_sole_writer.py` FIRST (new file — first
      literal introduced): assert `sticker_matches`/`cuadrillas` appear ONLY in
      `routers/inspector_asignaciones.py`. MUST fail until 5.2 lands (or pass immediately after if
      written post-5.2 — keep RED-before-GREEN by drafting the assertion before confirming the
      allowlist).
      — Satisfies: backend-platform "No write path exists outside the designated two" (partial; closes
      in slices 7/8).
      — STATUS: done, GENUINE RED-before-GREEN (not the no-gap-left situation 1.13/3.5 hit). Written and
      run immediately after 5.1, BEFORE 5.2's router existed: confirmed failing —
      `AssertionError: expected sticker_matches to be referenced by an allowlisted module by now` (0
      hits under `backend/app/` at that point) — then 5.2 landed and the same command passed (2/2). The
      `cuadrillas` half legitimately stays an empty-set pass this slice (`inspector-asignaciones.js`
      never touches `cuadrillas` at all — only `sticker-asignaciones.js`, slice 8, does); its positive
      (non-empty) assertion is deferred to slice 8's 8.4, not added here (do not anticipate).

- [x] **5.4** VERIFY (ADR-7 procedure): side-by-side same-inspector-token calls, both actions; record
      diff.
      — Satisfies: field-form-session "Cross-inspector access still rejected after migration".
      — STATUS: DONE, PASS (2026-08-26). Structural tier (no-auth/bad-token 401, both actions): exact
      match. Token-required `misPuntos`: exact match (`200`, `{ok:true, puntos:[]}` on both sides for
      the test inspector, who has no pending points right now). `marcarHecho` deliberately NOT
      exercised live (mutating action, would flip a real production point) — its cross-uid-rejection
      and own-uid-success behavior is already covered by 5.1's 7 unit-test cases against a fake
      Firestore store, which is the stronger signal for that path anyway (asserts the actual document
      state, not just the HTTP status).

- [x] **5.5** REPOINT: `formulario/js/form.js`'s `DASHBOARD_API` → consolidated app base URL. MANUAL
      Vercel redeploy of `formulario/`.
      — Satisfies: field-form-session "DASHBOARD_API repoint after parity verification"; "CORS Enabled
      For The formulario Origin".
      — STATUS: DONE (2026-08-26), gated on 5.4's PASS. `DASHBOARD_API` now resolves to
      `https://sismo-cali-dashboard-production.up.railway.app` in production (localhost dev unchanged).
      `asignacionesApi()`'s template literal now reads
      `` `${DASHBOARD_API}${INSPECTOR_ASIGNACIONES_PREFIX}/inspector-asignaciones` `` — a new
      `INSPECTOR_ASIGNACIONES_PREFIX` constant carries the `/api` segment ONLY for `localhost` (the
      local dev server still serves the legacy Vercel-function shape), empty string in production
      (the Railway route has no `/api` prefix) — so local dev against `localhost:3000` keeps working
      unchanged. `node --check` passed; `formulario`'s 53/53 `npm run test:unit` suite still green
      (none of those tests touch `DASHBOARD_API` directly, but confirms no syntax/import regression).
      Vercel redeploy of `formulario/` still pending (push + `vercel:deploy`, not yet run this batch).

**ROLLBACK BOUNDARY (Slice 5)**: revert `DASHBOARD_API`; redeploy `formulario/`.

**Batch status (sdd-apply, `feat/fastapi-consolidation-5-inspector-asignaciones`)**: 5.1-5.5 ALL DONE —
router + tests + sole-writer invariant (106/106 `backend/tests/` green at merge), 5.4 parity PASS
against the live Railway route with a real inspector token, 5.5 repoint applied. See
`apply-progress.md` "Batch 5" and the top-level "Live verification" note for full detail.

---

## Phase 6 — Slice 6: `refresh` endpoint

Chain PR #6. Depends on: Phase 1. Re-wires the Vercel↔Railway GraphQL coupling.

- [x] **6.1** (RED) Write `backend/tests/routers/test_refresh.py` FIRST: admin token → 202 with
      `deploymentId` (mocked Railway GraphQL client, `dashboard-refresh` service only — NO
      `cruceDeploymentId`/cruce-gestion trigger, per proposal.md Scope Exclusion Addendum Extension 2
      item 5); non-admin → 403, no Railway call. MUST fail.
      — Satisfies: backend-platform "Admin-gated route rejects non-admin" (`/refresh`).
      — STATUS: done. Confirmed RED — `ImportError: cannot import name 'refresh' from 'app.routers'`
      (1 collection error) before 6.2 landed. 3 cases: admin → 202 + exactly ONE Railway call
      (asserts no second `cruce-gestion` call); non-admin → 403, zero Railway calls; unauthenticated →
      401, zero Railway calls. Mocks `app.routers.refresh._railway_graphql` (call-count fake), no real
      network.

- [x] **6.2** (GREEN) Implement `backend/app/routers/refresh.py`: `POST /refresh`,
      `Depends(require_role("admin"))`, port `api/refresh.js:134-181`'s dual-header Railway auth
      fallback, triggering ONLY the `dashboard-refresh` service redeploy — the legacy fail-soft
      cruce-gestion redeploy branch is NOT ported (cruce-gestion is excluded from migration). Update
      the service/environment id to the NEW consolidated `dashboard-refresh` Railway service id
      created in slice 7 — confirm exact id before hardcoding. Run 6.1, confirm green.
      — Satisfies: backend-platform "Route Parity Across Consolidated Endpoints" (`/refresh` row).
      — STATUS: done. First GREEN pass, no rework (3/3; full suite 116/116 — 113 baseline + 3 new).
      **Service/environment id resolution**: slice 7 (job code absorption into `backend/app/jobs/`)
      has NOT run yet, so there is no NEW consolidated `dashboard-refresh` Railway service to point
      at. Rather than fabricate one, this router reads `RAILWAY_SERVICE_ID`/`RAILWAY_ENVIRONMENT_ID`
      env vars AT REQUEST TIME (mirroring `api/refresh.js`'s own env var names verbatim), each
      defaulting to the exact literal `api/refresh.js:96,100` already hardcodes
      (`156e97a2-596b-4861-95f4-4060dab408e2` / `4418f451-bd97-4d96-ba6e-b5ecbbd49c9b`) — the REAL,
      currently-live `dashboard-refresh` service in the `normalizador-sismo-cali` Railway project.
      This means the route redeploys the actual production job today, not a placeholder; slice 7 can
      repoint it to a new consolidated service later by setting the env var, no code change required.

- [ ] **6.3** VERIFY (ADR-7 procedure, mutating-action carve-out — redeploy trigger is idempotent
      enough to exercise live): admin-token POST old vs new; both 202, `deploymentId` present (old
      response's `cruceDeploymentId` field has no new-side equivalent — expected, documented
      difference, not a parity failure).
      — Satisfies: backend-platform "Old endpoint still serves after the new one deploys".
      — STATUS: NOT RUN this batch (repo-side prep done). `backend/scripts/verify_refresh_parity.py`
      written following the exact two-tier convention `verify_sign_parity.py`/
      `verify_status_routes_parity.py`/`verify_inspector_asignaciones_parity.py` established
      (STRUCTURAL tier: no-auth/bad-token, safe/non-mutating; TOKEN-REQUIRED tier: real admin POST on
      both sides). Confirmed its BLOCKED guard: running with no `NEW_REFRESH_URL` set exits 2 with an
      explanatory stderr message. **Extra safety guard beyond every prior parity script**: this
      endpoint's redeploy trigger is a REAL Railway `serviceInstanceRedeploy` mutation on BOTH old and
      new when exercised — unlike 2.3/4.5/5.4's token-required tiers (which only ever read data), so a
      `FIREBASE_ID_TOKEN` alone does NOT unlock the token-required tier here; it also requires an
      explicit `CONFIRM_REDEPLOY=yes` env var (verified in the script's logic — the token-required
      branch is gated on `id_token and confirm_redeploy`, both must be true). BLOCKED on BOTH a live
      admin `FIREBASE_ID_TOKEN` AND explicit human confirmation to fire two real production redeploys
      — neither is something an automated apply batch may fabricate or set on its own; not run live
      this batch.

- [ ] **6.4** REPOINT: flip the `refresh` entry in `api-config.js`. MANUAL Vercel redeploy of `web/`.
      — Satisfies: backend-platform "Rollback is a config revert".
      — STATUS: BLOCKED on 6.3 (no parity result exists yet to gate a repoint on), same dependency
      ordering every prior slice's REPOINT task followed. `web/js/api-config.js` and `web/js/main.js`
      were read but NOT modified this batch (out of scope: `web/js/api-config.js` is explicitly
      read-only for this finding; `main.js` is outside `backend/`). **Finding, confirmed by reading
      both files, not assumed**: `web/js/api-config.js`'s `refresh` entry (line 29) currently reads
      `refresh: '/api/refresh'` (a relative path, unflipped, same as `stickers`/`sticker-asignaciones`/
      `usuarios`). But UNLIKE the three already-flipped entries (`reportados`, `stickerStatus`,
      `sourceStatus`), `web/js/main.js` does NOT consume this entry through `api-config.js`'s
      `apiUrl()` accessor at all — `main.js:335` hardcodes its own separate literal,
      `const REFRESH_ENDPOINT = '/api/refresh';`, used by `triggerRefresh()` (`main.js:421-457`, wired
      to the admin-only "Actualizar datos" button via `refreshBtn.addEventListener('click', () =>
      triggerRefresh())`, `main.js:592`). So flipping `api-config.js`'s `refresh` entry ALONE would do
      nothing for this button — the eventual repoint needs a two-part edit (flip the `api-config.js`
      entry AND change `main.js:335` to `const REFRESH_ENDPOINT = apiUrl('refresh');`, importing
      `apiUrl` — already imported at `main.js:20` for other endpoints), the same two-part-edit pattern
      slice 5's 5.5 finding documented for `formulario/js/form.js`. Documented here as a concrete
      finding for whoever applies this repoint once 6.3 unblocks it.

**ROLLBACK BOUNDARY (Slice 6)**: revert the `refresh` entry; redeploy `web/`.

**Batch status (sdd-apply, `feat/fastapi-consolidation-6-refresh`)**: 6.1-6.2 DONE — router + tests
(116/116 `backend/tests/` green, 113 baseline + 3 new); 6.3 repo-side prep done (two-tier parity script
with an extra `CONFIRM_REDEPLOY=yes` guard), execution BLOCKED on a live admin token + explicit human
confirmation (never fabricated); 6.4 BLOCKED on 6.3 by design, with a concrete finding that `main.js`
doesn't even read `api-config.js`'s `refresh` entry yet (needs a two-part edit, not a single-line
flip). See `apply-progress.md` "Batch 6" for full detail.

---

## Phase 7 — Slice 7 + 7b: Crons per-job + `survey_cali` ingestion

Depends on: Phase 1. Can interleave from slice 2 onward per proposal; sequenced here to match the PR
chain. **Migrated set is TWO job services**: `dashboard-refresh`, `cruce-sticker` (per Task 0.1's
resolution and proposal.md Scope Exclusion Addendum Extension 2 — `integracion-f3`, `asignaciones`,
and `cruce-gestion` are ALL excluded; see 7.7, 7.10, 7.11). `cruce-gestion` is excluded because its
sole purpose was writing Firestore `dagma-85aad`/`cruce_criticos_survey`, and nothing dagma-related is
used anywhere in the new backend. **Recommend per-job sub-PRs** (7a-7c, see forecast) — each
independently mergeable once Phase 1 lands. `dashboard-refresh` first (code already in this repo).

- [x] **7.1** (RED) Write `backend/tests/jobs/test_dashboard_refresh.py` FIRST: offline `--check`-style
      idempotency/watermark fixtures for `refresh_data.py` + `fetch_reportes_api.py` (now calling
      3.2's `services/atencionsismo.py`). MUST fail.
      — Satisfies: job-scheduling "Re-running a job does not duplicate output" (dashboard-refresh row).
      — STATUS: done. Confirmed RED — `ImportError: cannot import name 'dashboard_refresh' from
      'app.jobs'` (1 collection error) before 7.2 landed. 15 test cases: `_raw_record_mapper`
      (PII/heavy-field stripping, coord parsing), `_dedupe_sorted` (idempotency across day-walk
      windows), `_meta_guard` (never publish empty/broken data), `_publish_all` (skip-if-missing, no
      spurious Blob calls), and an offline `--check` entrypoint smoke test.

- [x] **7.2** (GREEN) Implement `backend/app/jobs/dashboard_refresh.py`: `runlog.resolve_log_dir()` →
      `start_tee` → run `refresh_data.py` + `fetch_reportes_api.py` → `append_run(...)`; preserve
      `deploy/refresh.sh`'s timeout/meta-guard/trap structure (no more clone-at-start
      `entrypoint.sh`/`DASHBOARD_REPO_TOKEN` — code is in the image). Run 7.1, confirm green.
      — Satisfies: job-scheduling "dashboard-refresh needs no cross-repo absorption", "Watermark And
      Idempotent-Write Behavior Preserved".
      — STATUS: done. First GREEN pass, no rework — `python -m pytest backend/tests/jobs/
      test_dashboard_refresh.py -v` → 15 passed. `scripts/refresh_data.py` invoked via `subprocess.run`
      (`timeout=300`, `cwd=scripts/`) preserving the bash `timeout 300` semantics unchanged; the
      `fetch_reportes_api.py` half is REPLACED (not called as a subprocess) — its own day-walk logic is
      now `app.services.atencionsismo.day_walk()`, extracted from `count_reportes()` behind a pluggable
      `mapper` (default behavior verified unchanged: 34/34 pre-existing `test_atencionsismo.py` cases
      still green), called with a fuller field-preserving mapper (`_raw_record_mapper`, ported verbatim
      from `fetch_reportes_api.py`'s `strip_report()`) instead of duplicating the split/retry mechanics
      — task 7.2's own instruction. `reportes_agg.json` now reuses `atencionsismo.summarize()` instead
      of a second aggregation implementation (same precedent 3.4's Blob-seed path set). `deploy/
      blob_sync.py` imported directly (sys.path insert, not re-implemented) for the Blob seed/publish/
      status-write steps — `_status.json`'s best-effort write mirrors the bash `trap report_status
      EXIT`. Full suite: `python -m pytest backend/tests/ -v` → 131 passed (116 baseline + 15 new).

- [x] **7.3** (RED) Write `backend/tests/services/test_survey_cali.py` FIRST (mutation core only,
      ADR-10/11/12 — router lands in 8b): `apply_mutation` diff/revision computation; ingest
      idempotency (same input twice → zero new writes/revisions, hash equality); per-field conflict
      rule (manual edit on an unchanged-upstream field survives ingest; upstream change on a
      manually-edited field overwrites it, records `kind:'ingest'` with `before` = the manual value);
      first-run `kind:'create'`. MUST fail.
      — Satisfies: survey-cali-collection "Unchanged record is skipped", "Changed record is upserted by
      GlobalID", "A run never rewrites the full collection", "Ingest update writes a pipeline-authored
      revision", "History is never destroyed", "Manual edit survives an unrelated ingest run", "Source
      move overwrites a manually-edited field, visibly".
      — STATUS: done. 14 cases (apply_mutation create/edit/no-op/metadata-only, canonical_hash RAW-vs-
      derived, diff_upstream_fields, ingest_records skip/upsert/never-rewrite-full-collection/pipeline-
      authored-revision, manual-edit-survives, source-move-overwrites). Confirmed RED by temporarily
      hiding the already-drafted `app/services/survey_cali.py` (same honesty convention 7.8 used) —
      `ImportError: cannot import name 'survey_cali' from 'app.services'`, 1 collection error — restored,
      then 7.4 landed GREEN.

- [x] **7.4** (GREEN) Implement `backend/app/services/survey_cali.py`:
      `apply_mutation(id, changes, author, kind, revert_of=None)` per ADR-12 (Firestore transaction:
      read → diff → write effective fields + `_rev+1` + history doc atomically); document shape per
      ADR-10 (`_rev`, `_updated_at`, `_updated_by`, `_source`, `_source_hash`, `_deleted`;
      `history/{rev_NNNNNN}`). One of three modules allowlisted for the `survey_cali` literal (ADR-9).
      Run 7.3, confirm green.
      — Satisfies: survey-cali-collection "Append-Only Per-Record Revision History" (all 3 scenarios).
      — STATUS: done. `apply_mutation(id, changes, author, kind, revert_of=None, *, db=None)` — the `db=`
      keyword is an ADDITIVE deviation from the literal signature (injectable for testing without a live
      Firestore project; defaults to `credentials.sismo().firestore` in production, so every production
      call site is unaffected). Real transactions use `db.transaction()` + the SDK's own
      `@firestore.transactional` (confirmed via the installed SDK's source that this is the ONLY
      officially-supported atomic path — a hand-rolled transaction would fight the SDK, not simplify it);
      test doubles (marked `_is_test_double = True`) bypass the decorator and call the mutation function
      directly, since the real decorator requires a live gRPC-backed `Transaction` (verified by reading
      `_Transactional._pre_commit`/`__call__` — calls real `_begin()`/`_commit()`/`_rollback()`), matching
      how every other Firestore-touching module in this repo is tested (fakes only, no live project
      anywhere in this suite). First GREEN pass required ONE fix (not zero) — see Deviations/Issues in
      apply-progress.md's Batch 7b entry (EditDate/CreationDate/Creator/Editor/ObjectID excluded from the
      canonical hash as Survey123 audit metadata, not content).

- [x] **7.5** (GREEN) Wire ingestion into 7.2's `dashboard_refresh.py`: after the Survey123 fetch, per
      record build the canonical normalized form → SHA-256 → compare to `_source_hash`; skip on
      equality; else diff each field against `_source[field]`, write only changed fields via
      `apply_mutation(..., author='pipeline', kind='ingest')` (design open question 4: confirm at
      implementation time whether hashing uses RAW upstream fields only per ADR-11's recommendation —
      default to RAW unless the check finds a reason otherwise). Batch current-doc reads via `get_all`
      in chunks; outer batching ≤500 ops. Update `_meta/survey_cali_ingest_state`.
      — Satisfies: survey-cali-collection "Unchanged record is skipped", "Changed record is upserted by
      GlobalID", "A run never rewrites the full collection", "Manual edit survives an unrelated ingest
      run", "Source move overwrites a manually-edited field, visibly".
      — STATUS: done. `dashboard_refresh.py` gains `ingest_survey_cali()`, called fail-soft (try/except,
      same convention `fetch_reportes()` uses) right after the `refresh_data` subprocess step. **Design
      Interpretation — RAW-vs-computed hashing (open question 4), DEVIATES from the literal recommendation
      for a documented reason**: this batch's scope forbids editing anything outside `backend/` (no
      `scripts/refresh_data.py` changes) and forbids a second Survey123 upstream call; `refresh_data.py`
      runs as an opaque `subprocess.run`, so its pre-normalize DataFrame is unreachable across that process
      boundary without one of those two edits. The only artifact available without violating either
      constraint is `web/data/inspections.json` — `refresh_data.py`'s ALREADY-NORMALIZED output. The
      canonical hash therefore hashes THAT record shape minus an explicit `DERIVED_FIELDS` exclusion set
      (every pipeline-computed field name — `comuna`/`barrio_geo`/`x`/`y`/`geocode_*`/`*_calc`/etc. —
      confirmed by reading `scripts/refresh_data.py`'s `normalize()` pipeline function-by-function) plus a
      `SOURCE_SYSTEM_FIELDS` set (`EditDate`/`CreationDate`/`Creator`/`Editor`/`ObjectID` — Survey123 audit
      metadata, not content). This is the closest achievable approximation to "RAW fields only" within the
      batch's file-scope constraint — see `app/services/survey_cali.py`'s module docstring for the full
      reasoning. Batching per ADR-11 done via `_batched_read_source_state` (`get_all` in ≤500-id chunks,
      `field_paths=["_source","_source_hash"]` projection, mirroring `cruce_sticker.py`'s
      `read_tiene_sticker_state` precedent). `_meta/survey_cali_ingest_state` updated every run
      (`last_run_at`, `max_edit_date`, `created`/`updated`/`skipped` counts).

- [x] **7.6** (RED) Extend `test_sole_writer.py`: assert `survey_cali` literal appears ONLY in
      `services/survey_cali.py`, `routers/survey_cali.py` (allowlist entry now, router lands 8b),
      `app/jobs/dashboard_refresh.py`.
      — Satisfies: design.md ADR-9 (survey_cali sole-writer treatment).
      — STATUS: done, NO genuine RED-before-GREEN gap this task (same honest-flagging situation 1.13/3.5/
      7.9's docstring note already documented) — `services/survey_cali.py` and `dashboard_refresh.py`'s
      wiring already existed by the time this task ran (7.3-7.5 landed first per this batch's own
      sequencing), so the new `test_survey_cali_literal_is_used_by_an_allowlisted_module` passed on first
      run rather than failing first. New `ALLOWED_MODULES_SURVEY_CALI` set, INDEPENDENT of the existing
      `sticker_matches`/`cuadrillas` `ALLOWED_MODULES` (different collection, different ADR-9 clause):
      `services/survey_cali.py` + `app/jobs/dashboard_refresh.py` ONLY — `routers/survey_cali.py` named by
      ADR-9 but NOT added (doesn't exist yet, slice 8b, "do not anticipate" discipline preserved). One
      unplanned finding: `app/services/__init__.py`'s own module docstring mentions `survey_cali.py` by
      name (plain prose, "land in their own migration slices") — genuinely matched by the literal scan (no
      Firestore access, verified by reading the file in full, 3 lines total) — allowlisted with an inline
      comment explaining why, rather than rewording the docstring to dodge the scan.

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

- [x] **7.8** (RED) Write `backend/tests/jobs/test_cruce_sticker.py` FIRST: port the offline `--check`
      fixture already established in `integracion_F1/cruce_sticker.py` (stickers-asignacion change) —
      same pipeline-owned merge-safety/first-write assertions, targeting the new
      `backend/app/jobs/cruce_sticker.py` location. MUST fail.
      — Satisfies: job-scheduling "Watermark And Idempotent-Write Behavior Preserved" (cruce-sticker
      row); backend-platform "sticker_matches And cuadrillas Sole-Writer Invariant" (job side).
      — STATUS: done. Confirmed RED — `ImportError: cannot import name 'cruce_sticker' from
      'app.jobs'` (1 collection error, verified by temporarily hiding the already-written job file and
      restoring it) before 7.9 landed. 9 test cases: `doc_id`, matching-cascade reuse (geo hit,
      address-fallback hit, clean miss — via `app.integracion.cruce_gestor`), `build_write_ops`
      (pipeline-fields-only on existing docs, admin-defaults seeded only on first write),
      `select_candidates` (incremental — never re-scans an already-matched point), the offline
      `--check` entrypoint, and `REQUIRED_CLIENTS == ("sismo",)`.

- [x] **7.9** (GREEN) Absorb `integracion_F1/cruce_sticker.py` into `backend/app/integracion/` +
      `backend/app/jobs/cruce_sticker.py` (ADR-2 provenance; no gspread — confirmed clean). Third
      module allowlisted for `sticker_matches`/`cuadrillas` (with 5.2, and slice 8's
      `sticker_asignaciones.py`) — extend `test_sole_writer.py`. `sismo()` client (fail-fast). Run 7.8,
      confirm green.
      — Satisfies: job-scheduling "integracion_F1 Job Code Absorbed With Provenance".
      — STATUS: done. First GREEN pass, no rework — `python -m pytest backend/tests/jobs/
      test_cruce_sticker.py backend/tests/invariants/test_sole_writer.py -v` → 11 passed. Verified
      clean of gspread AND dagma: read `integracion_F1/cruce_sticker.py` in full — no gspread import,
      no dagma reference anywhere; its ONE dependency, `cruce_gestor.py`, is unrelated to dagma (a
      different "Gestor de Zonas" PMU Apps Script API, not Firestore) but DOES pull in
      `integracion/config.py`'s dagma constants (`FIRESTORE_PROJECT`/`FIRESTORE_COLLECTION =
      "cruce_criticos_survey"`, EDAN/VISITAS Sheets ids) transitively via `from .config import
      CALI_BBOX` — so `config.py` was NOT copied verbatim; a NEW trimmed `app/integracion/config.py`
      keeps ONLY `BOGOTA_TZ`/`CALI_BBOX` (proposal.md Extension 2: no dagma credential/project
      id/collection anywhere in `backend/`), a deviation from ADR-2's literal "copy exactly the
      modules it imports" for this ONE file, flagged for verify. `cruce_gestor.py`/`coords.py`/
      `normalization.py` copied otherwise verbatim (with provenance headers); `cruce_gestor.py`'s
      absolute `integracion.*` imports fixed to `app.integracion.*` (mechanical path fix, not a
      behavior change). Firestore access switched from the legacy module's own 3-tier SA resolution to
      `credentials.sismo()` per ADR-4/ADR-9. `app/jobs/cruce_sticker.py` merges the legacy pipeline
      module (`cruce_sticker.py`) with its runlog-wrapped entrypoint (`job_sticker.py`) into one file,
      same pattern 7.2 established for `dashboard_refresh.py`. `test_sole_writer.py`'s
      `ALLOWED_MODULES` extended with `app/jobs/cruce_sticker.py` as a WRITE module (confirmed
      `cruce_gestor.py` itself has zero `sticker_matches`/`cuadrillas` references — only the job module
      needed the allowlist entry). Full suite: `python -m pytest backend/tests/ -v` → 140 passed (131
      baseline + 9 new).

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

- [x] **7.12** Implement/update `backend/scripts/railway_services.py` (ADR-6, replaces
      `integracion_F1/scripts/railway_setup.py` as source of truth): drift-only LIST/INSTANCE/UPDATE
      GraphQL, `SERVICES` rows for exactly the TWO migrated jobs — `dashboard-refresh`,
      `cruce-sticker` (schedules per job-scheduling spec table). No rows for
      `integracion-f3`/`asignaciones`/`cruce-gestion` — per 7.7/7.10/7.11 none of them ever move to
      this script. Delete the migrated rows from `integracion_F1/scripts/railway_setup.py` in the SAME
      PR per job; `normalizador`'s, `integracion-f3`'s, `asignaciones`', and `cruce-gestion`'s rows
      stay untouched there.
      — Satisfies: job-scheduling "Per-Job Schedule Parity", "Drift-Only Provisioning Convention
      Preserved".
      — STATUS: done. `backend/scripts/railway_services.py` ports `railway_setup.py`'s exact
      `gql()`/`_token()`/`LIST_SERVICES`/`INSTANCE`/`CREATE`/`UPDATE`/`desired()`/`apply_service()`
      drift-only structure, scoped to 2 `SERVICES` rows: `dashboard-refresh` (`startCommand: python -m
      app.jobs.dashboard_refresh`, reuses its already-provisioned `service_id` — task 6.2's
      `/refresh` route already defaults to this exact id, so no re-provisioning) and `cruce-sticker`
      (`startCommand: python -m app.jobs.cruce_sticker`, `service_id: None` pending 7.13's MANUAL
      creation). Schedules verbatim from `railway_setup.py`'s own `EVERY_15`/`STICKER_EVERY_15`
      constants, cross-checked against job-scheduling spec's table (`*/15 13-23,0 * * *` /
      `7,22,37,52 13-23,0 * * *`) — identical, no discrepancy. Reuses the SAME `PROJECT_ID`/
      `ENVIRONMENT_ID` as the legacy script (`normalizador-sismo-cali` project) rather than a new
      project, matching 6.2's precedent. Syntax-checked (`python -c "import ast; ast.parse(...)"`) and
      smoke-run (`--show` with no `RAILWAY_API_TOKEN` set → clean `SystemExit` guard message, no
      crash) — no dedicated pytest file, matching the precedent every prior `verify_*_parity.py`
      operator script in `backend/scripts/` set (manual-tool convention, not unit-testable without a
      live Railway token). Deleted the `dashboard-refresh`/`cruce-sticker` rows (and the now-orphaned
      `STICKER_EVERY_15` constant) from `integracion_F1/scripts/railway_setup.py` in this same batch —
      that repo is a SEPARATE git remote (`Juanpgm/normalizador_data_sismo_cali`, gitignored from this
      repo), so the deletion is its own local commit there (`c54d6db`), not part of this repo's branch;
      `normalizador`/`integracion-f3`/`asignaciones`/`cruce-gestion` rows there are untouched, verified
      by re-reading the file after the edit.

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

**Batch status (sdd-apply, `feat/fastapi-consolidation-7a-jobs`)**: 7.1/7.2/7.8/7.9/7.12 ALL DONE — the
JOB-ABSORPTION portion of slice 7 (7a dashboard-refresh + 7c cruce-sticker+railway_services.py per the
Review Workload Forecast's suggested split, combined into one batch per this batch's own scope). Both
jobs absorbed with RED-before-GREEN TDD evidence, full `backend/tests/` suite 140/140 green at merge
(116 baseline + 24 new), neither mounted as an HTTP route in `app/main.py`. Task 7.6 (survey_cali
sole-writer allowlist extension) and 7.3-7.5 (survey_cali ingestion, slice 7b) are OUT OF SCOPE for this
batch — a separate batch handles `survey_cali` next, per this batch's own instructions. 7.13/7.14/7.15
remain manual-operator/verify steps, unticked. See `apply-progress.md` "Batch 7a" for full detail incl.
the Review Budget flag (this batch's actual diff is well above the forecast's 650-800-line estimate for
7/7b combined, driven mostly by `cruce_gestor.py`/`coords.py`/`normalization.py`'s verbatim ADR-2
copies — a recommended PR split is documented there).

**Batch status (sdd-apply, `feat/fastapi-consolidation-7b-survey-cali`)**: 7.3/7.4/7.5/7.6 ALL DONE — the
`survey_cali` INGESTION-CORE portion of slice 7b (mutation core + document/history model + wiring into
`dashboard_refresh.py`'s ingest step ONLY). The CRUD/history/revert ROUTER (`routers/survey_cali.py`,
tasks 8.9/8.10-shaped) is explicitly OUT OF SCOPE for this batch — it lands in slice 8b. Full
`backend/tests/` suite 158/158 green at merge (140 baseline + 18 new: 14 mutation-core + 3
dashboard-refresh wiring + 1 sole-writer invariant), `services/survey_cali.py` confirmed NOT mounted as
an HTTP route (`app/main.py`'s `_ROUTERS` untouched). See `apply-progress.md` "Batch 7b" for full detail
incl. the Review Budget flag and recommended PR split (854 changed lines total, above the 400-line
single-PR budget — 2 recommended PRs: mutation-core foundation, then wiring+invariant).

---

## Phase 8 — Slice 8 + 8b: Admin CRUD + `survey_cali` CRUD/history/revert

Depends on: Phase 1, patterns from Phase 5/7, Phase 7's `services/survey_cali.py` (8b reuses
`apply_mutation`). **Recommend per-area sub-PRs** (8a stickers+sticker-asignaciones, 8b-admin usuarios,
8c survey_cali — see forecast). Heaviest auth logic, moves last.

- [x] **8.1** (RED) Write `backend/tests/routers/test_stickers.py` FIRST: admin token → CRUD actions
      succeed; non-admin → 403, no mutation. Port the exact action set from `api/stickers.js`. MUST
      fail.
      — Satisfies: backend-platform "Admin-gated route rejects non-admin" (`/stickers`).
      — STATUS: done. Confirmed RED — `ImportError: cannot import name 'stickers' from 'app.routers'`
      (1 collection error) before 8.2 landed. 17 cases: 5 pure-validator ports (`api/stickers.test.js`'s
      cedula/codigo/password/email/`nextAvailableCodigo` matrix verbatim), 4 non-admin-rejected-no-
      mutation (parametrized over all 4 actions), 1 unauthenticated-401, and 7 admin-success/failure
      cases covering `list` (sorted by cedula, gmail non-inspector filtered out, missing-profile-doc
      defaults), `evaluaciones` (flattened shape, falsy-photo filtering), `create` (next-free-codigo
      allocation, invalid-cedula rejection with zero Auth calls, orphan-Auth-account rollback when the
      brigade-code transaction fails), `setEnabled` (Auth+Firestore flip), and an unrecognized-action
      case.

- [x] **8.2** (GREEN) Implement `backend/app/routers/stickers.py`: `POST /stickers`,
      `Depends(require_role("admin"))`, port `api/stickers.js` handler verbatim (client access via
      `credentials.sismo()`). Run 8.1, confirm green.
      — Satisfies: backend-platform "Route Parity Across Consolidated Endpoints" (`/stickers` row).
      — STATUS: done. First GREEN pass, no rework — `python -m pytest backend/tests/routers/
      test_stickers.py -v` → 17 passed. `firebase_admin.auth` imported at module level as `fb_auth`
      (not wrapped in a `credentials.py` accessor) so tests can monkeypatch it wholesale — same
      "patch the imported module reference" convention `routers/source_status.py` established for
      `atencionsismo.probe_api`. The brigade-code allocation transaction reuses `services/
      survey_cali.py`'s own `db.transaction()` + `_is_test_double`-detection pattern (task 7.4's
      precedent) rather than inventing a second transaction-testing convention. **Design
      interpretation flagged for verify**: `api/stickers.js`'s `listEvaluaciones` checks
      `typeof e.timestamp.toDate === 'function'` (JS Firestore SDK's Timestamp wrapper); the Python
      `google-cloud-firestore` client auto-converts Timestamp fields to native `datetime` objects on
      `to_dict()` — there is no `.toDate()` method to duck-type against — so this is ported as
      `isinstance(ts_value, datetime)` instead, the direct Python-native equivalent, not a behavior
      change. Mounted in `app/main.py`'s `_ROUTERS`. Full suite:
      `python -m pytest backend/tests/ -q` → 175 passed (158 baseline + 17 new).

- [x] **8.3** (RED) Write `backend/tests/routers/test_sticker_asignaciones.py` FIRST: port the 8-action
      matrix from `api/sticker-asignaciones.test.js` (`autoAgrupar` determinism/maxSize/maxRadius/
      empty-input; `listPuntos`/`listCuadrillas`; `crearCuadrilla`; `editarCuadrilla`;
      `asignarInspector`; `reasignarPunto`; `eliminarCuadrilla` clears membership before delete) as
      pytest cases. MUST fail.
      — Satisfies: backend-platform "sticker_matches And cuadrillas Sole-Writer Invariant" (route side).
      — STATUS: done. Confirmed RED — `ImportError: cannot import name 'sticker_asignaciones' from
      'app.routers'` (1 collection error) before 8.4 landed. 38 cases: 4 pure `autoAgrupar` determinism/
      maxSize/maxRadius/empty-input ports (`api/sticker-asignaciones.test.js`'s own fixtures), 10
      non-admin-rejected-no-mutation (parametrized over ALL 10 dispatch actions — see 8.4's finding on
      the actual action count), 1 unauthenticated-401, and 23 admin success/failure cases across every
      action incl. `eliminarCuadrilla`'s clears-membership-before-delete ordering and
      `reiniciarAgrupacion`'s auto-only-not-manual scoping.
      — FINDING (flag for verify): `api/sticker-asignaciones.js`'s dispatcher exposes 10 actions, not
      8 — `desasignarInspector` and `reiniciarAgrupacion` exist in the source but are not named in this
      task's own "8-action matrix" enumeration. See 8.4's STATUS note for the resolution (ported both,
      since "verbatim" of the whole file requires it) — this test file covers all 10, with the 8 named
      actions getting the fuller scenario coverage per the task's own emphasis.

- [x] **8.4** (GREEN) Implement `backend/app/routers/sticker_asignaciones.py`: port
      `api/sticker-asignaciones.js` verbatim (all 8 actions incl. pure `autoAgrupar`/`haversineM`).
      Fourth and final module allowlisted for `sticker_matches`/`cuadrillas` — extend
      `test_sole_writer.py` to its final set (`sticker_asignaciones.py`, `inspector_asignaciones.py`,
      `jobs/cruce_sticker.py`). Run 8.3, confirm green.
      — Satisfies: backend-platform "No write path exists outside the designated two" (final closure).
      — STATUS: done. First GREEN pass, no rework — `python -m pytest backend/tests/routers/
      test_sticker_asignaciones.py backend/tests/invariants/test_sole_writer.py -v` → 38 passed.
      **Design interpretation, flagged for verify**: `api/sticker-asignaciones.js` actually dispatches
      10 actions (`listPuntos`, `listCuadrillas`, `autoAgrupar`, `crearCuadrilla`, `editarCuadrilla`,
      `asignarInspector`, `desasignarInspector`, `reasignarPunto`, `eliminarCuadrilla`,
      `reiniciarAgrupacion`), not the 8 this task's own text enumerates. Since the task's own
      instruction is to port the file "verbatim ... (all 8 actions ...)", and a verbatim port of the
      WHOLE FILE necessarily includes every dispatch branch, all 10 were ported — silently dropping
      `desasignarInspector`/`reiniciarAgrupacion` would have left two real production capabilities
      unported, which is not "verbatim" by the task's own stated intent. `test_sole_writer.py`'s
      `ALLOWED_MODULES` extended to its CLOSED final set (`inspector_asignaciones.py`,
      `sticker_status.py` read-only, `jobs/cruce_sticker.py`, `sticker_asignaciones.py`);
      `test_cuadrillas_literal_appears_only_in_allowlisted_modules` now also asserts a non-empty hit
      set (its first real hit — `inspector-asignaciones.js` never touched `cuadrillas`). Mounted in
      `app/main.py`'s `_ROUTERS`. Full suite: `python -m pytest backend/tests/ -q` → 210 passed (175
      baseline + 35 new — 38 new test functions minus 3 pre-existing `test_sole_writer.py` cases whose
      assertions were extended, not added).

**Batch status (sdd-apply, `feat/fastapi-consolidation-8a-stickers`)**: 8.1-8.4 ALL DONE — both admin
routers + tests + the FINAL closure of the `sticker_matches`/`cuadrillas` sole-writer allowlist
(210/210 `backend/tests/` green at merge, 158 baseline + 52 new). 8.5-8.12 (usuarios, survey_cali CRUD
router) are OUT OF SCOPE for this batch — separate batches handle those next, per this batch's own
instructions. See `apply-progress.md` "Batch 8a" for full detail incl. the Review Budget flag (1873
changed lines, well above the 400-line single-PR budget and above even this sub-slice's own share of
the 950-1150+ forecast for combined 8/8b — a recommended 4-way PR split is documented there) and the
two Design Interpretation findings (Python `datetime` vs JS `.toDate()`; the actual 10-action dispatcher
vs the task text's "8-action matrix").

**Batch status (sdd-apply, `feat/fastapi-consolidation-8b-usuarios`)**: 8.5-8.6 ALL DONE — the admin
`usuarios` router (create/list/setPassword/delete, per this batch's own explicit 4-action scope) + its
extra provider/domain gate (242/242 `backend/tests/` green at merge, 210 baseline + 32 new). 8.7/8.8
(VERIFY/REPOINT for the WHOLE slice-8 batch — stickers + sticker-asignaciones + usuarios together) and
8.9-8.12 (`survey_cali` CRUD router, slice 8c) are OUT OF SCOPE for this batch. See `apply-progress.md`
"Batch 8b" for full detail incl. the Review Budget flag (774 changed lines, above the 400-line
single-PR budget but within this designated 8b-admin split of the 950-1150+ combined 8/8b forecast) and
the Design Interpretation finding on the extra provider/domain gate (a deliberate additive security
check, not a literal port of `usuarios.js`'s stale-commented executable shortcut) plus the `setEnabled`/
`setRole` scope gap flagged for a future batch.

- [x] **8.5** (RED) Write `backend/tests/routers/test_usuarios.py` FIRST: port `api/usuarios.test.js`'s
      full fixture matrix verbatim (`classify` precedence incl. claim-override; `checkDeleteGuards`
      last-enabled-admin block, non-admin delete allowed, second-admin unblocks, self-uid delete always
      blocked; `isValidPassword` bounds) plus the extra gate (`api/usuarios.js:200-201`: acting admin's
      provider MUST be `password` AND email MUST NOT be under `@sismocali.gov.co`, byte-for-byte per
      design open question 2). MUST fail.
      — Satisfies: backend-platform "usuarios endpoint enforces its extra provider/domain gate".
      — STATUS: done. Confirmed RED — `ImportError: cannot import name 'usuarios' from 'app.routers'`
      (1 collection error) before 8.6 landed. 33 cases (32 after removing one router-level test that
      was unreachable as originally written — see 8.6's STATUS note): 7 `classify` precedence cases
      incl. claim-override, 4 pure `checkDeleteGuards` cases (last-admin block, non-admin allowed,
      second-admin unblocks, self-uid always blocked x3 targets), `isValidPassword` bounds, 3 extra-gate
      cases (sismocali-domain admin rejected, google.com-provider admin rejected, password+non-sismocali
      admin accepted), 4 non-admin-rejected-no-mutation (parametrized over the 4 in-scope actions), 1
      unauthenticated-401, and 12 admin success/failure cases across `list`/`create`/`setPassword`/
      `delete`. **Scope note**: per this task's own text and the orchestrator's scope instructions,
      only `create`/`list`/`setPassword`/`delete` are covered — `setEnabled`/`setRole` are explicitly
      OUT of scope for this batch (no task assigns them); see 8.6's module-docstring flag.

- [x] **8.6** (GREEN) Implement `backend/app/routers/usuarios.py`: `POST /usuarios`,
      `Depends(require_role("admin"))` + 8.5's extra gate, port `api/usuarios.js` handler
      (create/list/setPassword/delete) verbatim incl. `checkDeleteGuards`/`isValidPassword`/`classify`.
      Run 8.5, confirm green.
      — Satisfies: backend-platform "usuarios endpoint enforces its extra provider/domain gate",
      "Route Parity Across Consolidated Endpoints" (`/usuarios` row).
      — STATUS: done. First GREEN pass except one self-authored router-level test bug (not an
      implementation bug — a "last admin, non-self delete" router test that turned out unreachable as
      written, since any admin caller reaching `delete` necessarily counts as an enabled admin
      themselves; removed, the equivalent guard stays fully covered at the pure-function level, matching
      `api/usuarios.test.js`'s own fixture shape). `python -m pytest backend/tests/routers/
      test_usuarios.py -v` → 32 passed. Full suite: `python -m pytest backend/tests/ -q` → 242 passed
      (210 baseline + 32 new). Mounted in `app/main.py`'s `_ROUTERS`.
      — **Extra provider/domain gate — design interpretation, flagged for verify.** Read
      `api/usuarios.js:200-214` character-by-character: its comment (lines 200-201) claims "provider
      'password', caller NOT @sismocali.gov.co", but its ACTUAL executable check is a single
      `roleFromClaims(claims) !== 'admin'` test — identical in shape to `stickers.js`'s admin gate.
      Reading `stickers.js:231-234`'s own comment confirms why: "`roleFromClaims` already resolves
      inspectors ... to 'inspector' — not 'admin' — so this one check REPLACES the old provider + domain
      guard." `usuarios.js`'s comment is the stale, un-updated leftover from BEFORE that refactor — the
      separate check was never literally re-added when `classify`/`roleFromClaims` absorbed it. A LITERAL
      byte-for-byte port of `usuarios.js`'s EXECUTABLE behavior would therefore need NO separate gate at
      all beyond `require_role("admin")` — but that reading is REJECTED here in favor of implementing the
      gate as a real, separate, additive check, because: (1)
      `openspec/changes/fastapi-backend-consolidation/specs/backend-platform/spec.md`'s own formal
      requirement row and dedicated scenario ("usuarios endpoint enforces its extra provider/domain gate")
      explicitly require it as SEPARATE; (2) `openspec/specs/user-management/spec.md` (the already-built
      spec) states the identical requirement; (3) the archived `usuarios-tab` design.md's ADR-1 locked
      this in as the INTENDED auth preamble at design time, with a real security rationale that still
      holds today: `role_from`'s claim-override precedence means an admin using `setRole` (out of THIS
      batch's scope, but already shipped in `api/usuarios.js`) could in principle grant an `admin` custom
      claim to a `@sismocali.gov.co` inspector or a `google.com`-provider viewer account, and such an
      account would then pass a bare `require_role("admin")` check. `usuarios.py`'s
      `_require_usuarios_admin` closes this gap explicitly at the route (layered on top of
      `Depends(require_role("admin"))`, per `auth/deps.py`'s own docstring anticipating exactly this).
      Full reasoning lives in `usuarios.py`'s module docstring.
      — **Scope note, flagged for verify/future batch**: `setEnabled` and `setRole` (both present in
      `api/usuarios.js`, both documented in `openspec/specs/user-management/spec.md`'s "Disable / enable
      user" requirement) are NOT implemented this batch — tasks.md's own 8.6 text names exactly FOUR
      actions (`create`/`list`/`setPassword`/`delete`), and the orchestrator's launch prompt repeated the
      same four-action scope verbatim, unlike 8.3/8.4's "verbatim = whole file" instruction for
      `sticker-asignaciones.js`. If task 8.8's repoint ever points `web/js/usuarios.js`'s "Habilitar/
      deshabilitar" or "Cambiar rol" UI actions at this router before a follow-up batch adds them, those
      two buttons will 400 (`Acción desconocida`) — a real functional gap, documented here rather than
      silently discovered later.

- [ ] **8.7** VERIFY (ADR-7 procedure, admin-POST carve-out): read-only actions (`listPuntos`,
      `listCuadrillas`, `usuarios` list) live side-by-side; mutating actions verified by
      8.1/8.3/8.5's pytest suites + one manual production smoke test per repoint.
      — Satisfies: backend-platform "Old endpoint still serves after the new one deploys".

- [ ] **8.8** REPOINT: flip `stickers`, `sticker-asignaciones`, `usuarios` entries in `api-config.js`.
      MANUAL Vercel redeploy of `web/`.
      — Satisfies: backend-platform "Rollback is a config revert".

- [x] **8.9** (RED) Write `backend/tests/routers/test_survey_cali.py` FIRST: non-admin → 403, no state
      change (all 7 routes); `PATCH` merge-only (`{a:1,b:2}`+`{b:3}` → `{a:1,b:3}`); no-op `PATCH` →
      200, zero new revision; underscore-prefixed metadata rejected by schema; `GET /survey-cali`
      excludes `_deleted`, never embeds history; `GET /survey-cali/{id}/history` returns all revisions
      newest-first; `POST /survey-cali/{id}/revert {rev}` → new revision, current state matches target
      rev, prior revisions unchanged. MUST fail.
      — Satisfies: survey-cali-collection "Non-admin call is rejected", "Update is a merge, not a
      replace", "Admin update writes a uid-authored revision", "Listing history returns all revisions
      in order", "Viewing a revision shows its changed fields", "Revert creates a new revision instead
      of mutating history", "Default list omits history", "History is available on explicit request".
      — STATUS: done. 17 cases across a router-owned Fake Firestore (extends `tests/services/
      test_survey_cali.py`'s path-keyed fake with whole-collection `.get()`, which `apply_mutation`
      itself never needs but list/history do): 7 non-admin-rejected-no-state-change cases (parametrized
      over all 7 routes, asserting the fake DB's full store snapshot is byte-identical before/after), 1
      unauthenticated-401, merge-only PATCH, no-op-PATCH-zero-revision, 2 underscore-metadata-rejected
      (create + patch), list-excludes-deleted-no-embedded-history, history-newest-first,
      history-shows-changed-fields, admin-update-uid-authored-revision, and the full
      revert-creates-new-revision-prior-unchanged round-trip. Confirmed RED — `404 Not Found` on every
      route (router did not exist / not mounted) before 8.10 landed.

- [x] **8.10** (GREEN) Implement `backend/app/routers/survey_cali.py` per ADR-12: `GET/POST
      /survey-cali`, `GET/PATCH/DELETE /survey-cali/{id}`, `GET /survey-cali/{id}/history`, `POST
      /survey-cali/{id}/revert`; all `Depends(require_role("admin"))`; all mutations go through 7.4's
      `apply_mutation` — no direct Firestore write in this router. Pick a history-list page size
      (design open question 5) and document the default. Run 8.9, confirm green.
      — Satisfies: survey-cali-collection (all requirements, route layer); design.md ADR-12.
      — STATUS: done. First GREEN pass, no rework — `python -m pytest backend/tests/routers/
      test_survey_cali.py -v` → 17 passed. History page size (open question 5): default 50
      revisions/page, `limit` query param, capped at 200 — documented in the router's own module
      docstring. **Design interpretation, flagged for verify**: ADR-12's table writes these routes as
      `/api/survey-cali...`, but task 8.10's own text (this batch's actual instruction) lists them
      WITHOUT an `/api` prefix, matching every other new-shape admin router this change has mounted
      (`/stickers`, `/sticker-asignaciones`, `/usuarios`, `/refresh`, `/sticker-status`,
      `/source-status` — only `/api/sign` keeps the prefix, for legacy parity `survey_cali` has none
      of). Followed the no-prefix convention; ADR-12's table treated as slightly stale on this cosmetic
      point. Also flagged: a soft-deleted doc 404s on `GET /survey-cali/{id}` too, not just the list
      endpoint (design interpretation, not explicitly specified either way). Mounted in `app/main.py`'s
      `_ROUTERS`. **Unplanned finding, fixed in this batch**: mounting the router made `app/main.py`'s
      own source text contain the literal `survey_cali` (`from app.routers import (..., survey_cali,
      ...)`) for the first time — an inherent consequence of this router module sharing its name with
      the Firestore collection, unlike every other router — so `app/main.py` was added to
      `test_sole_writer.py`'s `ALLOWED_MODULES_SURVEY_CALI` (verified harmless: import + mount only,
      zero Firestore access). A second, unrelated collision was also found and fixed: this router's own
      module docstring quoted the literal string `sticker_matches` in prose (citing
      `sticker_status.py`'s precedent), which tripped the OTHER (already-closed)
      `sticker_matches`/`cuadrillas` invariant check — reworded to describe the precedent without the
      literal substring, rather than reopening that closed allowlist. Full suite: `python -m pytest
      backend/tests/ -q` → 259 passed (242 baseline + 17 new).

- [x] **8.11** (RED) Finalize `test_sole_writer.py`'s `survey_cali` allowlist to its closed set
      (`services/survey_cali.py`, `routers/survey_cali.py`, `app/jobs/dashboard_refresh.py`); confirm
      no other module references the literal.
      — Satisfies: design.md ADR-9 (survey_cali sole-writer, final closure).
      — STATUS: done, NO genuine RED-before-GREEN gap this task (same honest-flagging situation
      7.6/1.13/3.5 already documented) — `routers/survey_cali.py` already existed by the time this task
      ran (8.9/8.10 landed first per this batch's own sequencing), so extending
      `ALLOWED_MODULES_SURVEY_CALI` with `routers/survey_cali.py` (+ the two unplanned findings above,
      `app/main.py` and the `sticker_matches`-substring docstring collision) made all three invariant
      checks pass immediately rather than failing first. `python -m pytest backend/tests/invariants/
      test_sole_writer.py -v` → 3 passed — confirmed the scan finds no OTHER module referencing
      `survey_cali` beyond the now-closed 3-write-module + 2-verified-harmless-mention allowlist. This
      is the FINAL closure of both sole-writer allowlists in this change (`sticker_matches`/`cuadrillas`
      closed at 8.4; `survey_cali` closed here).

- [ ] **8.12** VERIFY: exercise create → patch → delete → revert round-trip against a real/emulated
      `sismo-agosto-sgred` Firestore project; confirm history append-only (N → N+1, none altered) and
      the default list never returns `_deleted` docs or an embedded history array.
      — Satisfies: survey-cali-collection "History is never destroyed", "Default list omits history".
      — STATUS: BLOCKED, NOT RUN this batch — same class of blocker as every other live-Firestore/live-
      admin-token VERIFY task in this change (2.3's live S3 PUT, 6.3's live Railway redeploy, 7.14's
      live cron trigger, 8.7's live admin-POST side-by-side). This batch has no real or emulated
      `sismo-agosto-sgred` Firestore project available to it — 8.9/8.10/8.11's full round-trip
      (create → patch → delete → revert, history append-only, list/get exclusion of `_deleted`) is
      already exercised against a FAKE in-memory Firestore in `test_survey_cali.py` (17 cases,
      including the exact create→edit→edit→revert→verify-prior-unchanged sequence this VERIFY task
      describes), which is the strongest signal available without fabricating a live run. Genuinely
      running this task requires either provisioning the Firestore Emulator in CI/locally or a live
      admin `FIREBASE_ID_TOKEN` against the real project — neither is something an automated apply
      batch may set up or fabricate on its own, per this change's own established convention (no
      apply batch has ever faked a live-integration VERIFY result).

**Batch status (sdd-apply, `feat/fastapi-consolidation-8c-survey-cali-router`)**: 8.9-8.11 ALL DONE —
the `survey_cali` CRUD/history/revert router, its full RED-then-GREEN test suite, and the FINAL closure
of the `survey_cali` sole-writer allowlist (259/259 `backend/tests/` green at merge, 242 baseline + 17
new). This is the LAST scoped router for the whole `fastapi-backend-consolidation` change — Phase 9 is
decommission-only, no further new routes. 8.12 (live Firestore round-trip VERIFY) is explicitly OUT OF
SCOPE / BLOCKED, same class of blocker as every prior live-integration VERIFY task in this change — not
run, not fabricated. See `apply-progress.md` "Batch 8c" for full detail incl. the Review Budget flag
(712 changed lines — above the 400-line single-PR budget, but this IS the designated "8c survey_cali
CRUD/history/revert" split unit the Review Workload Forecast itself recommended for the combined 8/8b
slice) and two unplanned cross-module findings (a `sole_writer` scan collision introduced by mounting
this router in `app/main.py`, and an unrelated docstring substring collision with the OTHER, already-
closed `sticker_matches`/`cuadrillas` allowlist) — both fixed in this same batch, not deferred.

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
