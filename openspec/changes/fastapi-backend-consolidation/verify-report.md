# Verification Report

**Change**: `fastapi-backend-consolidation` -- SLICE 1 ONLY (Phase 1, tasks 1.1-1.14)
**Version**: spec `specs/backend-platform/spec.md` (target-state contract, no prior version)
**Mode**: Strict TDD (active, test runner: `python -m pytest backend/tests/ -v`)
**Branch reviewed**: `feat/fastapi-consolidation-1-scaffold` (11 commits ahead of `main`, not pushed)
**Reviewer**: fresh-context adversarial verifier. Did not trust apply-progress.md claims; re-ran
the suite, re-read every source file cited below, and independently diffed the JS parity source.

---

### Completeness

| Metric | Value |
|--------|-------|
| Slice-1 tasks total | 14 (1.1-1.14) |
| Slice-1 tasks complete | 13 |
| Slice-1 tasks incomplete | 1 -- 1.4, expected: MANUAL OPERATOR STEP (create Railway web service, provision env vars). Correctly left unchecked with a status note; not an automated-scope gap. |

Task checkbox state in tasks.md matches code state: 1.1-1.3 and 1.5-1.14 checked, 1.4 unchecked.
No task claims completion without corresponding code/tests.

---

### Build and Tests Execution

Tests: PASS -- 38 passed / 0 failed / 0 skipped (independently re-run, not trusted from apply-progress)

```text
$ python -m pytest backend/tests/ -v
...
======================= 38 passed, 17 warnings in 0.23s =======================
```

All 17 warnings are a single DeprecationWarning (asyncio.iscoroutinefunction, FastAPI internal,
Python 3.14) -- pre-existing library noise, not project code, not actionable.

Build: N/A for slice 1 (no Docker build executed in this verify pass; Dockerfile/railway.json
reviewed statically, see Deploy Config section below; task 1.4's actual Railway build is explicitly
out of automated scope and gated on a human operator step).

Coverage: not available -- no coverage tool configured in backend/requirements.txt (pytest-cov
not listed). Not a failure per strict-tdd-verify.md rules (informational-only, never blocking); noted
as a SUGGESTION below.

---

### Spec Compliance Matrix (backend-platform, slice-1-relevant scenarios only)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Ported Auth Verifier And Role Resolution | Precedence order resolves to earliest matching rule | test_roles_parity.py::test_role_from_superadmin_email_wins_over_claim_role | COMPLIANT |
| Ported Auth Verifier And Role Resolution | Parity suite passes identically to the JS test matrix | test_roles_parity.py (14 parametrized cases) | COMPLIANT |
| Single-Service-Account Credential Client | Missing web-route credential fails startup | test_startup.py::test_missing_firebase_service_account_json_fails_startup | COMPLIANT |
| Single-Service-Account Credential Client | No dagma reference exists in backend/ | grep sweep (see below) | COMPLIANT |
| Universal Explicit CORS Allowlist | Allowed origin receives CORS headers | test_cors.py::test_allowed_origin_receives_cors_header | COMPLIANT |
| Universal Explicit CORS Allowlist | Unlisted origin is rejected | test_cors.py::test_unlisted_origin_gets_no_permitting_cors_header | COMPLIANT |
| Universal Explicit CORS Allowlist | No cookie credentials accepted | test_cors.py::test_cookie_only_request_is_rejected_on_authenticated_route | COMPLIANT |
| Reproducible Git-Connected Deploy | No CLI-upload path exists for the web service | static review of Dockerfile + railway.json | COMPLIANT structurally -- full closure depends on task 1.4's manual Railway service creation |

Slice-1 compliance summary: 8/8 in-scope scenarios compliant.

---

### Correctness -- Detailed Audits

#### 1. roleFrom precedence parity -- line-by-line diff, api/refresh.js vs backend/app/auth/roles.py

| Step | api/refresh.js:77-85 | backend/app/auth/roles.py:36-47 | Match |
|---|---|---|---|
| 1 | e === SUPERADMIN_EMAIL -> admin | e == SUPERADMIN_EMAIL -> "admin" | Yes |
| 2 | claimRole -> return it | claim_role -> return it | Yes |
| 3 | e.endsWith(INSPECTOR_DOMAIN) -> inspector | e.endswith(INSPECTOR_DOMAIN) -> "inspector" | Yes |
| 4 | provider === 'password' -> usuario | provider == "password" -> "usuario" | Yes |
| 5 | provider === 'google.com' && e.endsWith(VIEWER_DOMAIN) -> viewer | same, and | Yes |
| else | otro | "otro" | Yes |

SUPERADMIN_EMAIL/INSPECTOR_DOMAIN/VIEWER_DOMAIN constants match verbatim
(@sismocali.gov.co, @cali.gov.co, default juanp.gzmz@gmail.com env-overridable). roleFromClaims
destructuring (claims.email / claims.role / claims.firebase.sign_in_provider) also matches
api/refresh.js:88-94 field-for-field.

Parity fixture matrix vs api/usuarios.test.js:8-22 (the actual JS parity source -- confirmed by
direct `ls api/*.test.js`: api/refresh.test.js does NOT exist, despite api/refresh.js:183's
stale comment pointing at it; the apply agent's claim on this point is independently verified TRUE):

| JS fixture case (usuarios.test.js) | Python case (test_roles_parity.py) | Present |
|---|---|---|
| inspector (@sismocali wins over password) | test_role_from_matches_js_fixture_matrix[inspector...] | Yes |
| usuario (password default, NOT admin) | [usuario: password default is usuario...] | Yes |
| admin (explicit custom claim) | [admin: explicit custom claim] | Yes |
| superadmin (email, no claim needed) | [superadmin: superadmin email...] | Yes |
| viewer (google.com + @cali.gov.co) | [viewer: google.com...] | Yes |
| otro (google.com, no @cali.gov.co) | [otro: google.com...] | Yes |
| claim-override case (line 22, usuario->admin via claim) | [claim overrides the derived default...] | Yes |

Zero JS fixture cases missing in Python. Python additionally adds a dedicated role_from_claims
parametrization (7 cases mirroring the same matrix in verified-token-claims shape) and an explicit
superadmin-vs-claim precedence test -- both are additive coverage, not a parity gap.
checkDeleteGuards/isValidPassword fixtures in usuarios.test.js are correctly NOT ported here --
those are usuarios.js-specific business rules scoped to task 8.5, not roleFrom parity.

#### 2. No-dagma scenario

Command: `grep -rniE "dagma|GOOGLE_SERVICE_ACCOUNT_JSON|cruce_criticos_survey" backend/`

Result: zero functional references. The only matches are (a) prose in
credentials/clients.py / credentials/__init__.py docstrings and test_credentials.py /
test_startup.py explaining/asserting the REMOVAL, and (b) compiled .pyc cache artifacts
(non-source, harmless). No dagma() client, no dagma-85aad project id, no
GOOGLE_SERVICE_ACCOUNT_JSON env var, no cruce_criticos_survey collection literal exists anywhere
in backend/ source. COMPLIANT with spec scenario "No dagma reference exists in backend/".

credentials/clients.py exposes exactly ONE named client -- sismo()
(FIREBASE_SERVICE_ACCOUNT_JSON) -- memoized via lru_cache, fail-fast via require(). Test coverage:
test_credentials.py (dagma absence, 3 cases) + test_startup.py (fail-fast, 2 cases). COMPLIANT
with "Single-Service-Account Credential Client" requirement.

#### 3. CORS allowlist vs design ADR-7

| ADR-7 item | config.py / main.py | Match |
|---|---|---|
| Explicit origins: dashboard + formulario | CORS_ALLOW_ORIGINS with both vercel.app origins | Yes |
| allow_origin_regex for localhost/127.0.0.1 dev | regex covers both host forms, any port | Yes |
| allow_credentials=False (Bearer, no cookies) | CORS_ALLOW_CREDENTIALS = False | Yes |
| methods GET, POST, OPTIONS | CORS_ALLOW_METHODS matches | Yes |
| headers Authorization, Content-Type | CORS_ALLOW_HEADERS matches | Yes |

test_cors.py exercises this against the real create_app()/CORSMiddleware instance (not a
stand-in stub app), covering allowed origin, unlisted origin, localhost regex, and a Bearer-vs-cookie
route via a stub route attached to the real app -- all 4 assertions call production code and check real
response values (headers/status), not tautologies.

#### 4. Deploy config vs design ADR-1/ADR-6

- railway.json (repo root): build.builder DOCKERFILE, build.dockerfilePath backend/Dockerfile,
  deploy.restartPolicyType ON_FAILURE. Root directory is not (and per Railway's config model,
  cannot be) pinned via railway.json -- it is a per-service dashboard setting, explicitly left to
  task 1.4 (manual operator step: "root = repo root"). This matches the design's own description of
  the mechanism (ADR-1: "pinned in repo-root railway.json build config" refers specifically to
  dockerfilePath; root staying at repo-root is a service-creation-time choice by construction, not a
  config file field).
- backend/Dockerfile: python:3.12-slim, COPY backend/ scripts/ deploy/ into the build context,
  WORKDIR /app/backend, pip install -r requirements.txt, CMD uvicorn app.main:app --host 0.0.0.0
  --port 8000. Matches ADR-1's tree/COPY intent exactly (repo-root build context so
  dashboard-refresh's later dependency on scripts/ + deploy/ is satisfied without a directory move).
- No CLI-upload path exists anywhere: confirmed by inspection -- every deploy artifact is
  git+Dockerfile-driven; no `railway up` invocation, script, or CI step exists in the diff or the
  repo. This structurally satisfies the spec scenario and the design's stated goal (the
  --path-as-root failure becomes structurally impossible).

WARNING -- stale comment, not a functional defect: both backend/Dockerfile line 1 and
railway.json's top-level comment say "web + 5 crons". The current design (post-Extension-2,
post-tasks-0.1-resolution) migrates only TWO cron services (cruce-sticker, dashboard-refresh);
normalizador, integracion-f3, asignaciones, cruce-gestion are excluded and stay on the legacy
integracion_F1 image. This is leftover prose from an earlier (pre-exclusion) version of the plan.
It has zero effect on actual deploy behavior (comments are non-functional), but it will mislead anyone
reading these two files as the topology reference during slice 7/9. Should be corrected to "web + 2
crons" in a follow-up commit (slice 7 or a small doc-fix), not blocking for slice 1 merge.

#### 5. The 2 flagged deviations

(a) Non-empty-sub check in verify.py, not present in api/refresh.js.
Confirmed: api/refresh.js:31-54 checks only aud/iss/exp/iat -- no sub check exists in the
JS source. Confirmed design.md ADR-3 literally states: "Claim checks identical to JS: iss,
aud, exp, iat, non-empty sub." -- the design text itself lists sub alongside the JS-identical
checks. Task 1.6 also explicitly lists "empty sub rejected" as a MUST-fail RED case. Verdict: this
is design-mandated, not an unauthorized deviation. Correctly implemented and correctly disclosed by
the apply agent rather than silently added.

(b) Task 1.13's no-true-RED CORS test.
test_cors.py is disclosed in apply-progress.md as having passed on its first run (no genuine
RED-to-GREEN cycle) because CORS (task 1.12) and require_auth (task 1.9) were already correctly
implemented earlier in the same batch. Independently assessed: the 4 assertions in test_cors.py are
NOT trivial -- they instantiate the real create_app() with its real CORSMiddleware wiring, issue
real HTTP requests via TestClient, and assert on real response headers/status codes
(access-control-allow-origin presence/absence per origin, 401 on cookie-only auth). This is
meaningful integration coverage of the actual production code path, not a smoke test and not a
tautology. Verdict: the coverage is real; the process deviation (no literal RED failure) is a minor,
disclosed Strict-TDD-protocol gap -- WARNING, not CRITICAL, specifically because (1) it was honestly
flagged rather than hidden, and (2) the behavior it covers already has independent RED-to-GREEN evidence
via test_deps.py (1.9) and the CORS wiring's own earlier manual sanity check noted in batch 1a.

---

### Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| ADR-1 (repo layout, backend/ tree, repo-root Dockerfile context) | Yes | Tree matches; root-directory mechanism correctly deferred to manual task 1.4 |
| ADR-3 (auth port: verify/roles/deps, 3 modules, sub check) | Yes | See detailed audit above |
| ADR-4 (credentials: exactly ONE named client, fail-fast, declaration mechanism) | Yes | dagma() correctly absent; WEB_STARTUP_CLIENTS interpretation is a reasonable, disclosed resolution of an internal ADR-4 ambiguity between its router-union prose and its per-client fail-fast Load Rule row -- does not contradict the spec scenario or task 1.11/1.12's literal requirement |
| ADR-7 (CORS allowlist, Bearer-only) | Yes | Exact match, see audit above |
| ADR-8 (pytest + TestClient, create_app() factory for fakes) | Yes | All 38 tests use this pattern; zero real network/credentials in CI |
| ADR-9 (sole-writer invariant scaffolding) | N/A this slice | No sticker_matches/cuadrillas/survey_cali modules exist yet (correct -- those land slices 5/7/8) |

---

### TDD Compliance

| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | Yes | Both apply-progress batches (1a, 1b) include a TDD Cycle Evidence table |
| All tasks have tests | Yes | 12/12 logic-bearing tasks (1.5-1.14, excluding 1.1-1.3 scaffold/config and manual 1.4) have covering test files |
| RED confirmed (tests exist) | Yes | All cited test files verified present in the tree and read directly (not trusted from the report) |
| GREEN confirmed (tests pass) | Yes | 38/38 pass on independent re-run |
| Triangulation adequate | Yes | roles.py: 14 cases; verify.py: 9 cases; deps.py: 5 cases; cors: 4 cases; credentials: 3 cases; startup: 2 cases |
| Safety Net for modified files | Yes | Dagma-removal batch shows explicit RED (2/3 failing against the pre-removal module) before GREEN |

TDD Compliance: 6/6 checks passed, with one disclosed exception (task 1.13, see deviation b above --
counted under RED confirmed as a partial/WARNING, not a failure of the check itself since the test
file does exist and does pass).

---

### Test Layer Distribution

| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Unit | ~24 | test_roles_parity.py (14), test_verify.py (9), test_credentials.py (partial) | pytest |
| Integration | ~14 | test_deps.py (5), test_cors.py (4), test_startup.py (2), test_credentials.py (partial) | pytest + fastapi.testclient.TestClient |
| E2E | 0 | -- | not applicable to slice 1 (no deployed environment exercised) |
| Total | 38 | 8 files | -- |

Counts approximate due to some overlap between unit/integration classification for credential tests;
total matches the 38 passed.

---

### Assertion Quality

Scanned all 8 test files created/modified in this slice. Zero tautologies, zero ghost loops, zero
ACL-bypassing patterns found. All assertions call production code (role_from, role_from_claims,
verify_firebase_token, create_app(), require_auth/require_role, credentials.require) and
check real return values, exceptions, or HTTP response fields.

Assertion quality: All assertions verify real behavior.

---

### Commit Hygiene

- 11 commits on feat/fastapi-consolidation-1-scaffold, all conventional-commit-formatted
  (feat(backend): ..., test(backend): ..., refactor(backend): ..., docs(...): ...).
- Command run: git log main..HEAD --format=%B, piped to grep -iE for co-authored, generated,
  claude, anthropic -- result: zero matches. No AI attribution anywhere in the commit history.
- Diff scope is clean: git diff --stat main..HEAD for backend/ and railway.json touches only
  backend/ plus root railway.json (26 files, 1027 insertions, 0 deletions). The two docs commits
  touch only openspec/changes/fastapi-backend-consolidation/tasks.md and apply-progress.md.
  Nothing unrelated swept in.
- git status shows untracked files (context/, and openspec/changes/.../design.md,
  exploration.md, proposal.md, specs/) that are NOT part of any commit on this branch -- these
  are planning artifacts sitting in the working tree, not code changes. Not a slice-1 code concern,
  but flag for the orchestrator: confirm these are intentionally staged for a later
  proposal/spec/design commit and not accidentally omitted from version control.
- PR-boundary risk (WARNING, process not code): combined 1a+1b diff is 1027 lines -- well over the
  400-line single-lens-review budget. apply-progress.md explicitly recommends opening 1a and 1b as
  two separate stacked PRs (1a approx 339 lines, 1b approx 640 lines across 5 commits), consistent
  with auto-chain/stacked-to-main. This verify pass confirms the commit boundaries exist cleanly to
  support that split (each commit is independently buildable/testable), but the actual PR-opening
  decision has not happened yet on this branch (not pushed to origin). Flagging so the split is
  actually honored at PR time, not silently squashed into one 1000+-line review.

---

### Issues Found

CRITICAL: None.

WARNING:
1. backend/Dockerfile line 1 and railway.json's header comment both say "web + 5 crons" -- stale,
   should read "web + 2 crons" per the current (post-Extension-2) migrated-job scope. Cosmetic only,
   does not affect deploy behavior, but will confuse readers before slice 7/9. Recommend a follow-up
   doc-fix commit.
2. Task 1.13 (test_cors.py) had no genuine RED failure -- Strict TDD protocol process gap, honestly
   disclosed by the apply agent. Coverage itself is real and meaningful (see deviation b audit
   above); not a correctness risk, but note for process discipline in later slices.
3. Combined 1a+1b diff (1027 lines) exceeds the 400-line review budget if merged as a single PR --
   must be opened as two separate stacked PRs per apply-progress.md's own recommendation and the
   tasks.md Review Workload Forecast. This is a merge-time responsibility, not a code defect.
4. Untracked planning artifacts (design.md, exploration.md, proposal.md, specs/, context/) sit in
   the working tree outside any commit on this branch -- confirm intentional before this branch is
   finalized/pushed.

SUGGESTION:
1. No coverage tool configured (pytest-cov not in requirements.txt). Not blocking per Strict TDD
   rules, but would sharpen future-slice verify passes.
2. ADR-4's "unions the declarations of mounted routers" versus per-client "fail-fast at web startup"
   ambiguity (flagged by the apply agent itself in both batches) should be resolved explicitly in
   design.md at the next design revision, rather than carried forward as tribal knowledge in
   apply-progress.md.

---

### Verdict

PASS WITH WARNINGS.

38/38 tests pass on independent re-run. Line-by-line roleFrom/roleFromClaims parity against
api/refresh.js is exact, and every JS fixture case in api/usuarios.test.js (the real parity
source -- api/refresh.test.js does not exist, independently confirmed) has a corresponding Python
case with zero gaps. The no-dagma scope exclusion is fully clean -- zero functional references
anywhere in backend/. Credentials module exposes exactly one named client with fail-fast startup
validation, tested. CORS allowlist matches design ADR-7 field-for-field, Bearer-only, no cookies,
tested against the real middleware wiring. Deploy config (Dockerfile + railway.json) is
git-connected with a pinned dockerfilePath and structurally excludes any CLI-upload path -- the
class of failure the design was written to prevent cannot recur here. Both flagged deviations are
non-issues on inspection: the sub check is design-mandated (verified against ADR-3's literal text),
and the CORS test's no-true-RED gap is a disclosed process note over otherwise-meaningful coverage.
Commit hygiene is clean (conventional commits, no AI attribution, no unrelated files swept in).

The 4 WARNINGs are all process/documentation items (stale cron-count comments, one disclosed TDD
process gap, a PR-splitting responsibility that must be honored at merge time, and untracked planning
files to confirm) -- none of them block correctness or spec compliance for slice 1's actual code.

Slice 1 is safe to merge to main, PROVIDED it is opened as two separate stacked PRs (1a, then
1b) per apply-progress.md's own sizing recommendation, and NOT as one combined 1027-line PR.
