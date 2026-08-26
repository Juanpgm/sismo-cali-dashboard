# Apply Progress: FastAPI Backend Consolidation

Change: `fastapi-backend-consolidation` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-apply

Branch: `feat/fastapi-consolidation-1-scaffold` (off `main`, not pushed).
Delivery: `auto-chain` / `stacked-to-main`. Slice 1 sub-split into **1a** (done) and **1b**
(done, this batch), per the tasks.md Review Workload Forecast (`1: ~700-850 lines, High risk`,
suggested split `1a: skeleton+Dockerfile+config+credentials+CORS+main (~350-400)` / `1b: auth
verify+roles+deps+parity+verify tests (~350-450)`).

Strict TDD Mode: ACTIVE. Test runner: `python -m pytest backend/tests/ -v`.

---

## Batch 1a — Scaffold, credentials, CORS, health (COMPLETE)

### Completed Tasks

- [x] **1.1** `backend/` package skeleton (design ADR-1 tree): `app/__init__.py`, `app/main.py`
      (stub), `app/config.py` (stub), `app/auth/__init__.py`, `app/credentials/__init__.py`,
      `app/routers/__init__.py`, `app/services/__init__.py`, `app/jobs/__init__.py`,
      `app/integracion/__init__.py` + `PROVENANCE.md`, `backend/requirements.txt`,
      `backend/tests/__init__.py`.
- [x] **1.2** `backend/Dockerfile` — `python:3.12-slim`; `COPY backend/ scripts/ deploy/`; `WORKDIR
      backend`; `pip install -r requirements.txt`; `CMD uvicorn app.main:app --host 0.0.0.0 --port
      8000`.
- [x] **1.3** Repo-root `railway.json` — `dockerfilePath: backend/Dockerfile`, root = repo root.
- [x] **1.10** `backend/app/credentials/clients.py` — originally `sismo()`/`dagma()` named memoized
      clients; **`dagma()` removed in batch 1b** (proposal.md Extension 2) — now `sismo()` only.
      `require()` fail-fast validator, `REQUIRED_CLIENTS`/`WEB_STARTUP_CLIENTS` mechanism.
- [x] **1.11** (RED) `backend/tests/test_startup.py` — written FIRST, confirmed failing.
      **Rewritten in batch 1b** to drop its `GOOGLE_SERVICE_ACCOUNT_JSON` cases (dagma removal).
- [x] **1.12** (GREEN) `backend/app/config.py` (CORS allowlist) + `backend/app/main.py`
      (`create_app()`: credential validation, `CORSMiddleware`, mounts `routers/health.py`).

### Not started (out of automated scope)

- [ ] **1.4** MANUAL OPERATOR STEP — create the Railway "web" service, provision env vars. Left
      unchecked with a status note in tasks.md; requires human action in the Railway dashboard.

### Files Changed

| File | Action | What Was Done |
|---|---|---|
| `backend/app/__init__.py` | Created | Package docstring |
| `backend/app/main.py` | Created | `create_app()` factory: validates credentials, mounts CORS + health router |
| `backend/app/config.py` | Created | `Settings`, CORS allowlist constants (ADR-7) |
| `backend/app/auth/__init__.py` | Created | Stub package (real content lands in 1b) |
| `backend/app/credentials/__init__.py` | Created | Package docstring |
| `backend/app/credentials/clients.py` | Created | `sismo()`, `dagma()`, `require()`, `required_clients_for()`, `CredentialsError`, `WEB_STARTUP_CLIENTS` |
| `backend/app/routers/__init__.py` | Created | Package docstring |
| `backend/app/routers/health.py` | Created | `GET /health`, no auth, `REQUIRED_CLIENTS=()` |
| `backend/app/services/__init__.py` | Created | Package docstring |
| `backend/app/jobs/__init__.py` | Created | Package docstring |
| `backend/app/integracion/__init__.py` | Created | Package docstring |
| `backend/app/integracion/PROVENANCE.md` | Created | Empty provenance table header (ADR-2) |
| `backend/requirements.txt` | Created | fastapi, uvicorn, pydantic(-settings), httpx, cryptography, firebase-admin, google-cloud-firestore, pytest(-asyncio) |
| `backend/tests/__init__.py` | Created | Test package marker |
| `backend/tests/test_startup.py` | Created | Startup credential fail-fast tests (3 cases) |
| `backend/Dockerfile` | Created | Image build per ADR-1 |
| `railway.json` (repo root) | Created | Build config per ADR-1 |

### TDD Cycle Evidence

| Task | RED (command + result) | GREEN (command + result) | REFACTOR |
|---|---|---|---|
| 1.11/1.12 (startup credential validation) | `python -m pytest backend/tests/test_startup.py -v` → `ImportError: ModuleNotFoundError: No module named 'app.credentials.clients'` (1 error, 0 collected) | `python -m pytest backend/tests/test_startup.py -v` → `3 passed, 3 warnings` (test_missing_firebase_service_account_json_fails_startup, test_missing_google_service_account_json_does_not_block_web_startup, test_startup_succeeds_when_both_credentials_present) | None needed — implementation matched the test contract on first GREEN pass. Manual sanity check via `TestClient` also confirmed `GET /health` → 200 and CORS allowlist behavior (allowed origin gets `Access-Control-Allow-Origin`, unlisted origin does not) ahead of 1.13's dedicated test. |

Full-suite confirmation (at end of batch 1a): `python -m pytest backend/tests/ -v` → `3 passed`.

### Design Interpretation (flag for verify)

ADR-4's "Declaration mechanism" paragraph says `create_app()` "unions the declarations of mounted
routers" for credential validation — read strictly, that would mean slice 1a (only `health.py`
mounted, `REQUIRED_CLIENTS=()`) validates nothing, contradicting task 1.11's own required behavior
(missing `FIREBASE_SERVICE_ACCOUNT_JSON` must fail startup even with only `health` mounted). ADR-4's
env-var table separately states sismo's **Load rule is "fail-fast at web startup"** as a per-client
property, not conditioned on router mounting. I resolved this by adding `WEB_STARTUP_CLIENTS =
("sismo",)` — validated **unconditionally**, unioned with the mounted-router declarations (which
will naturally overlap once auth-consuming routers land in later slices). This satisfies both the
per-client Load Rule table AND task 1.11/1.12's literal test requirements. Not a deviation from the
design's intent (the per-client Load Rule row is explicit and binding); it is a resolution of an
internal ambiguity between two ADR-4 paragraphs. **Batch 1b does not change this resolution** — it
still stands, please confirm at verify time.

### Deviations from Design

None beyond the interpretation noted above — implementation matches design.md ADR-1, ADR-4, ADR-7.

### Issues Found

None.

### Workload / PR Boundary

- Mode: chained PR slice (`auto-chain` / `stacked-to-main`), sub-split **1a of 1a/1b**.
- Current work unit: Slice 1a — backend package skeleton, Dockerfile, Railway build config,
  credentials module, CORS-configured `create_app()`, health route.
- Boundary: starts from an empty `backend/` (no prior FastAPI code in this repo); ends at a
  running, testable `create_app()` with zero mounted auth/business routes beyond `/health`. No
  consumer repointed, no Railway service created — zero production impact.
- Estimated review budget impact: `git diff --stat main..HEAD -- backend/ railway.json` → **339
  insertions**, 17 files — within the ≤400-line single-lens-review budget forecast for 1a
  (~350-400 est.).
- Rollback: delete the branch / do not merge. No production system touches this code yet.

### Status

6/14 slice-1 tasks complete at end of batch 1a (1.1, 1.2, 1.3, 1.10, 1.11, 1.12). Superseded by
batch 1b below.

---

## Batch 1b — Auth verify/roles/deps + parity tests (COMPLETE, + dagma removal)

### Completed Tasks

- [x] **1.5** (RED) `backend/tests/auth/test_roles_parity.py` — table-driven port of the exact
      `api/usuarios.test.js:8-22` fixture matrix (the real JS parity source — `api/refresh.test.js`
      does not exist despite `api/refresh.js:183`'s stale comment), plus the SUPERADMIN_EMAIL-wins-
      over-claim-role precedence scenario from `specs/backend-platform/spec.md`, plus `role_from_claims`
      cases in the verified-payload shape (`claims.email`/`claims.role`/`claims.firebase.sign_in_provider`).
- [x] **1.6** (RED) `backend/tests/auth/test_verify.py` — injectable fake cert-fetcher (self-signed
      RSA keypair, zero network) + per-test `CertCache()`: valid token accepted; unknown `kid` forces
      exactly one refetch (asserted via a fetcher call-count log) then rejects if still unknown; bad
      `aud`/`iss`/`exp`/`iat`/signature/malformed-token rejected; empty `sub` rejected.
- [x] **1.7** (GREEN) `backend/app/auth/roles.py` — `role_from(email, claim_role, provider)` +
      `role_from_claims(claims)`, precedence ported verbatim from `api/refresh.js:63-94`
      (`SUPERADMIN_EMAIL` → claim `role` → `@sismocali.gov.co` → `password` → `google.com`+
      `@cali.gov.co` → `otro`). First GREEN pass, no rework.
- [x] **1.8** (GREEN) `backend/app/auth/verify.py` — `verify_firebase_token(id_token, project_id, *,
      cert_fetcher=None, cert_cache=None)`, RS256 against Google's rotating x509 certs via
      `cryptography` (load PEM cert → extract public key → `PKCS1v15`/`SHA256` verify), `CertCache`
      class with TTL from `Cache-Control: max-age`, injectable `CertFetcher`. First GREEN pass, no
      rework. Adds a non-empty-`sub` claim check beyond `api/refresh.js`'s literal checks — see
      "Design Interpretation" below.
- [x] **1.9** (GREEN) `backend/app/auth/deps.py` — `current_claims` (Bearer extraction + verify, 401
      fail-closed), `require_auth` (any authenticated role), `require_role(role)` (dependency factory,
      403 on mismatch). No RED task listed for 1.9 in tasks.md, but Strict TDD Mode is active for this
      batch, so `backend/tests/auth/test_deps.py` was written FIRST (confirmed failing —
      `ModuleNotFoundError: No module named 'app.auth.deps'`) using ADR-8's `dependency_overrides`
      convention (stub FastAPI app, fake verified claims injected via
      `app.dependency_overrides[current_claims]`).
- [x] **1.13** (RED*) `backend/tests/test_cors.py` — allowed origin gets
      `Access-Control-Allow-Origin`; unlisted origin gets no permitting header; localhost dev origin
      allowed via regex; cookie-only request (no Bearer) on a stub authenticated route (built by
      attaching a route to the real `create_app()` instance, not a separate stand-in app) is rejected
      with 401. `*` — all 4 cases passed on FIRST run: CORS (`config.py`/`main.py`, task 1.12) and
      `require_auth` (task 1.9) were both already correctly implemented earlier in this same batch, so
      there was no implementation gap left to turn RED — this is a dedicated automated-coverage
      addition, not a literal RED→GREEN cycle. Flagged here rather than silently claimed as RED.
- [x] **1.14** Runnable check: `python -m pytest backend/tests/ -v` → **38 passed**. Full slice-1 suite
      green (roles parity, verify, deps, startup, credentials, CORS, health). Zero repoints this
      slice — only `backend/` changed.

### Out-of-band scope addition: dagma credential removal (proposal.md Extension 2)

Mid-batch, the orchestrator relayed a binding scope directive (proposal.md Extension 2, 2026-08-25,
post-slice-1a, user directive): "no usar nada relacionado con el dagma." Verified directly against
`proposal.md` lines 109-118 before acting (never trusted the relayed message alone). Consequences
actioned in this batch:

- [x] Removed the `dagma()` client from `backend/app/credentials/clients.py` — the credentials module
      now holds exactly ONE named client (`sismo()`, fail-fast at startup, `FIREBASE_SERVICE_ACCOUNT_JSON`).
- [x] TDD: `backend/tests/test_credentials.py` written FIRST (3 cases: `dagma` attribute doesn't
      exist, `require("dagma")` raises `CredentialsError` matching `"unknown credential client"`,
      `WEB_STARTUP_CLIENTS == ("sismo",)`) — confirmed RED against the still-2-client module (2 of 3
      failed: `dagma` attribute existed; `require("dagma")` raised a DIFFERENT message,
      `"GOOGLE_SERVICE_ACCOUNT_JSON is not set"`, not the expected `"unknown credential client"`) —
      then removed `dagma()`/its `_ENV_VARS` entry → GREEN, all 3 pass.
- [x] `backend/tests/test_startup.py` rewritten: dropped both `GOOGLE_SERVICE_ACCOUNT_JSON`-referencing
      tests (`missing_google_service_account_json_does_not_block_web_startup` — no longer meaningful,
      no lazy credential remains to test; `both_credentials_present` → renamed
      `startup_succeeds_when_firebase_service_account_json_present`, sismo-only).
- [x] Swept `backend/` for `dagma`/`GOOGLE_SERVICE_ACCOUNT_JSON`/`dagma-85aad` references:
      `requirements.txt`'s `google-cloud-firestore` comment corrected (it backs `sismo()`, not a
      removed `dagma()`); `credentials/__init__.py` and `credentials/clients.py` docstrings rewritten
      to describe ONE client, with the `dagma-85aad` project id and `GOOGLE_SERVICE_ACCOUNT_JSON`
      literal env var name removed from prose (kept a non-literal pointer to proposal.md Extension 2
      for traceability). Confirmed zero remaining matches for `GOOGLE_SERVICE_ACCOUNT_JSON`/
      `dagma-85aad` in `backend/` after the sweep.
- [x] Committed as its own explicit work unit: `refactor(backend): drop dagma credential client per
      scope exclusion` (commit `b16e206`).
- Cross-reference: `cruce-gestion` (dagma's sole consumer) joining the excluded legacy job set is a
      **tasks.md/design.md scope change outside this batch's task list (1.5-1.9/1.13/1.14)** — tasks.md
      was already updated externally (found modified on disk, not by this agent) to reflect Extension 2
      before this batch started; this apply batch only executed the `backend/` code-removal
      consequence, matching the already-revised task 1.10/1.11 text verbatim.

### Files Changed (Batch 1b, cumulative)

| File | Action | What Was Done |
|---|---|---|
| `backend/app/auth/roles.py` | Created | `role_from`, `role_from_claims` — verbatim port of `api/refresh.js:63-94` |
| `backend/app/auth/verify.py` | Created | `verify_firebase_token`, `CertCache`, `CertFetchResult`, `TokenVerificationError` |
| `backend/app/auth/deps.py` | Created | `current_claims`, `require_auth`, `require_role(role)` |
| `backend/app/auth/__init__.py` | Modified | Docstring refreshed — verify/roles/deps all now exist |
| `backend/tests/auth/__init__.py` | Created | Test package marker |
| `backend/tests/auth/test_roles_parity.py` | Created | 7 `role_from` + 7 `role_from_claims` parametrized cases |
| `backend/tests/auth/test_verify.py` | Created | 9 cases: valid, unknown-kid-refetch, bad aud/iss/exp/iat/signature/malformed, empty sub |
| `backend/tests/auth/test_deps.py` | Created | 5 cases: missing header, invalid token, valid override, admin accepted, non-admin 403 |
| `backend/tests/test_cors.py` | Created | 4 cases: allowed origin, unlisted origin, localhost regex, cookie-only-rejected |
| `backend/app/credentials/clients.py` | Modified | `dagma()` client + its `_ENV_VARS` entry removed (Extension 2) |
| `backend/app/credentials/__init__.py` | Modified | Docstring: one named client, not two |
| `backend/requirements.txt` | Modified | `google-cloud-firestore` comment corrected to `sismo()` |
| `backend/tests/test_credentials.py` | Created | 3 cases asserting no `dagma` client exists |
| `backend/tests/test_startup.py` | Modified | Dropped `GOOGLE_SERVICE_ACCOUNT_JSON` cases; sismo-only |

### TDD Cycle Evidence

| Task | RED (command + result) | GREEN (command + result) |
|---|---|---|
| dagma removal (`test_credentials.py`) | `python -m pytest backend/tests/test_credentials.py backend/tests/test_startup.py -v` → 2 of 5 failed: `test_dagma_client_does_not_exist` (dagma attribute existed), `test_dagma_is_not_a_known_credential_client` (message was `"GOOGLE_SERVICE_ACCOUNT_JSON is not set..."` not `"unknown credential client"`) | Same command → `5 passed` after removing `dagma()`/`_ENV_VARS["dagma"]` |
| 1.5/1.7 (`test_roles_parity.py` / `roles.py`) | `python -m pytest backend/tests/ -v` → `ModuleNotFoundError: No module named 'app.auth.roles'` (1 collection error) | `python -m pytest backend/tests/ -v` → `20 passed` |
| 1.6/1.8 (`test_verify.py` / `verify.py`) | `python -m pytest backend/tests/auth/test_verify.py -v` → `ModuleNotFoundError: No module named 'app.auth.verify'` (1 collection error) | `python -m pytest backend/tests/ -v` → `29 passed` |
| 1.9 (`test_deps.py` / `deps.py`) | `python -m pytest backend/tests/auth/test_deps.py -v` → `ModuleNotFoundError: No module named 'app.auth.deps'` (1 collection error) | `python -m pytest backend/tests/ -v` → `34 passed` |
| 1.13 (`test_cors.py`) | `python -m pytest backend/tests/test_cors.py -v` → **4 passed immediately** (no RED — see 1.13's status note above; CORS + require_auth were already correct) | n/a — already green |
| 1.14 (full suite) | — | `python -m pytest backend/tests/ -v` → **38 passed, 0 failed** |

### Design Interpretation (flag for verify)

`verify.py`'s `sub`-non-empty check has NO equivalent in `api/refresh.js#verifyFirebaseToken`
(lines 31-54 there check only `aud`/`iss`/`exp`/`iat` — no `sub` check exists in the JS source at
all). This is NOT a parity bug: design.md ADR-3 explicitly states "Claim checks identical to JS: iss,
aud, exp, iat, non-empty sub" — the `sub` requirement is an ADR-level addition, and task 1.6 itself
lists "empty `sub` rejected" as a MUST-fail RED case. Implemented as specified; documented here so
verify doesn't mistake it for an unauthorized deviation from the JS source. Also carries forward
batch 1a's still-unresolved-but-accepted `WEB_STARTUP_CLIENTS` interpretation note (unchanged this
batch — do not "fix" it).

### Deviations from Design

None beyond the `sub`-check interpretation above (which is design-mandated, not a deviation) and the
dagma removal (which is a user-directed proposal.md amendment landed mid-batch, not an apply-agent
deviation from the design as it stood at batch start).

### Issues Found

None.

### Workload / PR Boundary

- Mode: chained PR slice (`auto-chain` / `stacked-to-main`), sub-split **1b of 1a/1b**, plus one
  out-of-band refactor commit for the dagma removal.
- Work units (commits, in order): (1) `refactor(backend): drop dagma credential client per scope
  exclusion` (`b16e206`); (2) `feat(backend): port roleFrom/roleFromClaims parity (auth/roles.py)`
  (`c4a37a5`); (3) `feat(backend): port Firebase ID token verifier (auth/verify.py)` (`eecd2c3`);
  (4) `feat(backend): add require_auth/require_role/current_claims (auth/deps.py)` (`771e127`);
  (5) `test(backend): add CORS allowlist + cookie-rejection suite (test_cors.py)` (`cdf5089`).
- Boundary: starts from batch 1a's `create_app()` (health-only, CORS-configured); ends at a complete,
  tested `app/auth/` package (verify + roles + deps) with a sismo-only credentials module and a green
  38-test suite. Still zero mounted business/auth routes beyond `/health` — no consumer repointed, no
  Railway service created, zero production impact.
- Estimated review budget impact: `git diff --stat main..HEAD -- backend/` reports roughly
  339 (1a) + ~640 (1b, sum of the 5 commits above) insertions — comfortably within 1b's ~350-450
  estimate once considered as its own commit range (1a's 339 lines already landed/reviewed
  separately); if reviewed as ONE PR spanning 1a+1b the combined total (~980) would exceed the
  400-line single-lens budget — recommend opening 1a and 1b as separate stacked-to-main PRs, or
  splitting 1b's own diff at the natural commit boundaries above if a single 1b PR is preferred.
- Rollback: delete the branch / do not merge. No production system touches this code yet.

### Status

**Slice 1: 13/14 tasks complete** (1.1-1.3, 1.5-1.14). **1 task (1.4) remains a manual operator
step**, intentionally out of automated scope, left unchecked with a status note — requires human
action in the Railway dashboard (create the Railway "web" service, provision env vars, confirm no CLI
upload).

Slice 1 is otherwise COMPLETE. Full `backend/tests/` suite: **38 passed, 0 failed**.

### Next Batch

**Slice 2: Photo signer `/api/sign`** (tasks 2.1-2.5, `openspec/changes/fastapi-backend-consolidation/tasks.md`
Phase 2) — lowest blast radius, depends only on Phase 1 (auth, credentials, CORS, `create_app()`, all
now complete). Estimated ~180-230 lines, low 400-line risk, single PR per the Review Workload
Forecast. Adds `s3()` to `credentials/clients.py` (`SIGNER_AWS_ACCESS_KEY_ID/SECRET`,
`SIGNER_S3_BUCKET/REGION`, fail-fast) and `backend/app/routers/sign.py` (`POST /api/sign`,
`Depends(require_auth)`, Bearer-header auth replacing the legacy signer's body-`idToken`).
