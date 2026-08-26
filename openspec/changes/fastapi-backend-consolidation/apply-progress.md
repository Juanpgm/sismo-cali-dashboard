# Apply Progress: FastAPI Backend Consolidation

Change: `fastapi-backend-consolidation` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-apply

Branch: `feat/fastapi-consolidation-1-scaffold` (off `main`, not pushed).
Delivery: `auto-chain` / `stacked-to-main`. Slice 1 sub-split into **1a** (this batch) and **1b**
(next batch), per the tasks.md Review Workload Forecast (`1: ~700-850 lines, High risk`, suggested
split `1a: skeleton+Dockerfile+config+credentials+CORS+main (~350-400)` / `1b: auth
verify+roles+deps+parity+verify tests (~350-450)`).

Strict TDD Mode: ACTIVE. Test runner: `python -m pytest backend/tests/ -v` (established this batch —
new layout, no prior convention).

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
- [x] **1.10** `backend/app/credentials/clients.py` — `sismo()`/`dagma()` named memoized clients,
      `require()` fail-fast validator, `REQUIRED_CLIENTS`/`WEB_STARTUP_CLIENTS` mechanism.
- [x] **1.11** (RED) `backend/tests/test_startup.py` — written FIRST, confirmed failing.
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

Full-suite confirmation: `python -m pytest backend/tests/ -v` → `3 passed` (only `test_startup.py`
exists in this batch; `test_roles_parity.py`, `test_verify.py`, `test_cors.py` land in batch 1b).

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
internal ambiguity between two ADR-4 paragraphs. Please confirm at verify time.

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

6/14 slice-1 tasks complete (1.1, 1.2, 1.3, 1.10, 1.11, 1.12). 1 task (1.4) is a manual operator
step, intentionally out of automated scope, left unchecked with a status note. 7 tasks remain
(1.5-1.9, 1.13, 1.14) → **batch 1b**.

---

## Batch 1b — Auth verify/roles/deps + parity tests (NOT STARTED)

Next batch picks up:

- [ ] **1.5** (RED) `backend/tests/auth/test_roles_parity.py` — table-driven port of the
      `api/usuarios.test.js:8-22` fixture matrix.
- [ ] **1.6** (RED) `backend/tests/auth/test_verify.py` — injectable fake cert-fetcher, token
      validation cases.
- [ ] **1.7** (GREEN) `backend/app/auth/roles.py` — `role_from`/`role_from_claims`, ported verbatim
      from `api/refresh.js:77-94`.
- [ ] **1.8** (GREEN) `backend/app/auth/verify.py` — `verify_firebase_token`, RS256 against Google's
      rotating x509 certs.
- [ ] **1.9** (GREEN) `backend/app/auth/deps.py` — `require_auth`, `require_role("admin")`,
      `current_claims`.
- [ ] **1.13** (RED) `backend/tests/test_cors.py` — CORS allowlist + cookie-only-rejected-on-stub-
      authenticated-route scenarios (needs 1.9's `require_auth` for the stub route).
- [ ] **1.14** Runnable check: `pytest backend/tests/` green across the full slice-1 suite (roles
      parity, verify, startup, CORS, health).

Estimated 1b diff: ~350-450 lines per the tasks.md forecast — plan for its own reviewable commit
sequence (RED test_roles_parity → GREEN roles.py → RED test_verify → GREEN verify.py → GREEN deps.py
→ RED test_cors → confirm 1.14 full-suite green), same branch, before opening the PR for slice 1
(1a+1b together, or as its own follow-up PR per the stacked-to-main chain — orchestrator decides at
PR-creation time).
