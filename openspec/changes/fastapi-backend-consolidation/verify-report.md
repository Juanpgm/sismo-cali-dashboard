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

---

## Slice 2: Photo signer `/api/sign` (tasks 2.1-2.2 + repo-side 2.3 prep)

**Change**: `fastapi-backend-consolidation` -- SLICE 2 ONLY (Phase 2, tasks 2.1-2.5; scope: 2.1-2.2
implemented, 2.3 repo-side prep only)
**Version**: spec `specs/inspection-photo-capture/spec.md` delta + `specs/backend-platform/spec.md`
(`/sign` row of "Route Parity Across Consolidated Endpoints")
**Mode**: Strict TDD (active, test runner: `python -m pytest backend/tests/ -v`)
**Branch reviewed**: `feat/fastapi-consolidation-2-sign` (5 commits ahead of `main`, not pushed)
**Reviewer**: fresh-context adversarial verifier. Re-ran the full suite independently, reproduced the
claimed RED failure by checking out the RED commit into a scratch worktree, and diffed
`backend/app/routers/sign.py` line-by-line against `services/photo-signer/api/sign.js` (the parity
source) rather than trusting apply-progress.md's summary.

### Completeness

| Metric | Value |
|--------|-------|
| Slice-2 tasks total (this batch's scope) | 5 (2.1-2.5) |
| Complete | 2 (2.1, 2.2) |
| Repo-side prep done, execution blocked | 1 (2.3 -- blocked on manual task 1.4, no live Railway URL) |
| Blocked by design | 2 (2.4, 2.5 -- correctly gated on 2.3 passing against a real deployment) |

Task checkbox state matches code state exactly: 2.1/2.2 checked, 2.3/2.4/2.5 unchecked with accurate
STATUS notes in `tasks.md`. No task claims completion it hasn't earned.

### Build and Tests Execution

Tests: PASS -- 45 passed / 0 failed / 0 skipped (independently re-run).

```text
$ python -m pytest backend/tests/ -v
...
======================= 45 passed, 35 warnings in 0.47s =======================
```

Warnings are the same pre-existing `asyncio.iscoroutinefunction` deprecation noise from FastAPI
internals (Python 3.14) seen in the slice-1 report -- not project code, not actionable.

RED evidence for 2.1/2.2 independently reproduced (not trusted from apply-progress): checked out
commit `826cc72` (the RED commit) into a scratch git worktree and ran
`python -m pytest backend/tests/routers/test_sign.py -v` there. Result: **6 failed**,
`AttributeError: module 'app.credentials.clients' has no attribute 's3'` -- exact match to the
claimed failure reason. This is a legitimate RED: the failure is caused by the router/client under
test not existing yet, not a fixture bug or an unrelated import error. Worktree removed after
verification, working tree left clean.

### Sign Parity Audit (line-by-line vs `services/photo-signer/api/sign.js`)

| Behavior | Legacy (`sign.js`) | New (`sign.py`) | Match? |
|---|---|---|---|
| `codigo` regex | `^76001-[123]-\d{7,8}$` | identical, `re.compile` | Yes |
| Slot range | `1 <= Number(slot) <= MAX_SLOT` | `1 <= body.slot <= _max_slot()` | Yes |
| `MAX_SLOT` source | `SIGNER_MAX_SLOT` env, default 10 | same env var, same default, read per-request | Yes |
| S3 key shape | `evaluaciones/{codigo}/foto_{slot}.jpg` | identical f-string | Yes |
| Presign method | `PutObjectCommand` + `getSignedUrl` | `generate_presigned_url("put_object", ...)` | Yes (equivalent semantics) |
| `ContentType` binding | hardcoded `image/jpeg` (not client-controlled) | hardcoded `image/jpeg` (not client-controlled) | Yes |
| `ExpiresIn` | 300s | 300s | Yes |
| Bucket/region source | `SIGNER_S3_BUCKET`/`SIGNER_S3_REGION` (default `us-east-1`) | same env vars, same default | Yes |
| Response shape | `{uploadUrl, publicUrl}` | `{uploadUrl, publicUrl}` (Pydantic `SignResponse`) | Yes |
| `publicUrl` construction | `https://{BUCKET}.s3.amazonaws.com/{key}` | identical f-string | Yes |
| Bad `codigo`/`slot` status | `400` with `error=bad-request` | `400` (`HTTPException(detail="bad-request")`) | Yes |
| Token verification | `idToken` in JSON body -> `accounts:lookup` REST call | `Authorization: Bearer` header -> shared RS256 verifier (`require_auth`) | Documented delta (spec-authorized) |

**Behavioral differences found beyond the documented auth delta:**

1. Config-missing failure mode differs (WARNING, not a regression). Legacy: a request against a
   misconfigured deployment returns `500` with `error=config` and a `missing` list, per request. New:
   `s3()`'s env vars are validated at `create_app()` startup (fail-fast) -- a misconfigured deployment
   never starts serving at all, so this failure mode structurally cannot be hit at request time. This
   is the intended ADR-4 design (fail-fast beats runtime 500s) and is a strict improvement, but it is
   a genuine behavioral difference from the literal legacy contract documented in
   `services/photo-signer/README.md` (a request against a deployment missing any required variable
   returns 500). Not spec-blocking; note for the eventual parity-procedure PR description since
   `backend-platform`'s parity procedure asks for status codes to match.
2. Validation-order difference on the missing-Bearer-plus-bad-body combination (WARNING, spec-covered
   deliberately). Legacy checks `idToken`, `codigo`, `slot` all together as one `400 bad-request`
   block before calling Firebase -- so a request with a missing `idToken` AND a malformed `codigo`
   returns `400`, not `401`. New: `Depends(require_auth)` resolves before the route body's
   codigo/slot checks run, so a request with a missing/invalid Bearer header always returns `401`
   regardless of what else is wrong with the body. This is a consequence of moving auth to a FastAPI
   dependency rather than an in-handler check, is exercised directly by
   `test_missing_bearer_is_rejected` / `test_invalid_bearer_is_rejected`, and is consistent with
   every other route's `require_auth` pattern in this backend -- but it is not literally covered by
   the spec's two token-verification scenarios (which only test the accept/reject token outcome, not
   response ordering against a simultaneously-bad body). Judged non-blocking: no legitimate client
   sends a request with both a missing token and a malformed body, and the new ordering is uniform
   with every other consolidated route.
3. Field-presence validation differs (WARNING, minor, untested edge case). Legacy computes
   `Number(slot)` from a possibly-absent field (`undefined` becomes `NaN`, falling into the existing
   `400 bad-request` branch). New uses a Pydantic `SignRequest` model (`codigo: str`, `slot: int`) --
   an entirely missing `codigo` or `slot` field in the JSON body triggers FastAPI's automatic `422
   Unprocessable Entity` before the route body even runs, not `400`. This case is not covered by
   `test_sign.py` (all 6 cases send both fields) and is not in the spec's explicit scenario list
   (bad codigo / out-of-range slot, not missing-field), so it is not a spec gap, but it is a real,
   untested parity edge case that `verify_sign_parity.py` should ideally exercise if the operator
   wants full acceptance-semantics parity confidence before the 2.4 repoint.

No other behavioral differences found. Bucket/region/key construction, presign expiry, and
content-type binding are byte-for-byte equivalent to the legacy signer.

### Security Review

- Presign parameters cannot be abused via crafted input. `Bucket` comes only from server-side
  config (`bucket_client.bucket`, sourced from `SIGNER_S3_BUCKET`), never from the request. `Key` is
  built from `body.codigo` (constrained by `CODIGO_RE`, rejecting path traversal characters, slashes
  beyond the fixed prefix, etc.) and `body.slot` (an `int`, range-checked 1..MAX_SLOT). `ContentType`
  is hardcoded to `image/jpeg`, never taken from the client -- prevents content-type-based abuse of
  the presigned URL. Confirmed no request field reaches `Bucket`, `Key` path segments beyond the
  validated `codigo`, or `ContentType`.
- Auth is enforced before any presign. `Depends(require_auth)` -> `Depends(current_claims)` is
  resolved by FastAPI before the route function body executes; `credentials.s3()` and
  `generate_presigned_url` are only reached from inside the function body, so no presign URL can be
  minted without a valid, verified Bearer token. Confirmed by `test_missing_bearer_is_rejected` and
  `test_invalid_bearer_is_rejected` (both real requests through the full `TestClient`, not mocked at
  the dependency layer).
- No secrets in code. `backend/app/credentials/clients.py` reads `SIGNER_AWS_ACCESS_KEY_ID`,
  `SIGNER_AWS_SECRET_ACCESS_KEY`, `SIGNER_S3_BUCKET`, `SIGNER_S3_REGION` exclusively via
  `os.environ` -- no hardcoded key material anywhere in `backend/app/` or `backend/tests/`. Test
  fixtures use obviously-fake placeholder values (`fake-access-key-id`, `fake-secret-access-key`),
  confirmed not resembling real AWS credential formats.

### ADR-4 Interpretation Flag: `s3()` Not In `WEB_STARTUP_CLIENTS`

**Finding: COMPLIES with ADR-4, no remediation needed.**

ADR-4's table marks `s3()`'s Load rule as `fail-fast at web startup` -- identical wording to
`sismo()`'s row. The apply agent did NOT add `s3` to the `WEB_STARTUP_CLIENTS` tuple (which stays
`("sismo",)`, confirmed by `test_web_startup_clients_is_sismo_only`), relying instead on ADR-4's own
Declaration mechanism paragraph (design.md, ADR-4): each router/job module declares
`REQUIRED_CLIENTS` at module top, and `create_app()` unions the declarations of mounted routers and
validates presence at startup. This is not a fallback interpretation invented by the apply agent --
it is the literal mechanism ADR-4 itself specifies for achieving fail-fast at web startup for
router-scoped clients, and it is exactly what `credentials.required_clients_for(_ROUTERS)` plus
`credentials.require(*required)` implement in `main.py`'s `create_app()`, called before the FastAPI
app object is even constructed (verified by reading `main.py` lines 20-30).

The result is behaviorally identical to adding `s3` directly to `WEB_STARTUP_CLIENTS`, as long as
the `sign` router stays unconditionally mounted -- which it currently is (`_ROUTERS = (health,
sign)`, no feature flag, no conditional inclusion anywhere in the codebase, confirmed by reading
`main.py` in full). `test_missing_signer_s3_credentials_fails_startup` independently confirms this
holds in practice: startup fails when `SIGNER_*` env vars are absent, with `sign` mounted.

The one theoretical gap the apply agent itself flagged (a future conditional/feature-flagged router
mount would silently stop enforcing `s3`'s fail-fast rule) is real but forward-looking and out of
scope for this slice -- no such conditional mounting exists today. No remediation required for slice
2 as shipped; this is a legitimate, spec-compliant reading of ADR-4, not a deviation.

### Repo Hygiene Checks

- `formulario/js/form.js` confirmed untouched. `git diff --quiet main..HEAD -- formulario/js/form.js`
  exits with no diff. Independently confirmed rather than trusting the STATUS note in `tasks.md` 2.4.
- `backend/scripts/verify_sign_parity.py` makes no live calls unless explicitly invoked with a URL.
  Read the full script: `main()` checks `NEW_SIGN_URL`/`FIREBASE_ID_TOKEN` env vars first and returns
  exit code 2 with a `BLOCKED` stderr message before any `httpx.Client` is constructed if either is
  unset -- the `import httpx` itself is deferred inside `main()`, and the module is not imported by
  `app/` or `backend/tests/` (confirmed via `git diff --stat` showing it only appears once, in
  `backend/scripts/`, and via reading `backend/app/main.py`'s imports, which do not reference
  `scripts`). No `pytest` collection risk, no accidental network call during the test-suite run
  confirmed above (45/45 passed with zero network-related failures/hangs).

### Diff Size vs 400-Line Budget

`git diff --stat main..HEAD -- backend/` (backend-only, excluding openspec docs): **449 insertions,
24 deletions across 9 files** -- independently confirmed, matches apply-progress.md's own flagged
number exactly. This is above both the Review Workload Forecast's ~180-230 estimate for slice 2 and
the general 400-line single-PR review budget.

Breakdown re-verified: `clients.py` (+112/-20, mostly docstring/comment expansion for the two-client
module, not new logic density), `sign.py` (+82, the actual route -- reasonably sized for what it
does), `test_sign.py` (+109, 6 test cases), `verify_sign_parity.py` (+122, a standalone manual tool
with zero import coupling to the router/test commits), remainder (~24 lines) in `main.py`,
`requirements.txt`, and slice-1 fixture fixes.

Agreement with the apply agent's split proposal: reasonable, recommend adopting it. Splitting
`verify_sign_parity.py` (commit `8864a35`, 122 lines) into its own follow-up PR is sound --
independently confirmed it has no runtime dependency on `sign.py`/`test_sign.py` (not imported by
either, no shared new symbols) and is never exercised by the automated suite. Removing it would bring
the router PR to 327 insertions, 24 deletions across 8 files -- inside the 400-line budget. The
remaining 24-line regression-fix commit (`282f7b7`, slice-1 fixture updates) is correctly kept with
the router PR since it is a direct, necessary consequence of mounting `sign` in `create_app()`, and
splitting it out would leave an intermediate broken state (slice-1 tests failing) if merged alone.

### Commit Hygiene

5 commits, all on `feat/fastapi-consolidation-2-sign`, not pushed. Conventional-commit prefixes
throughout (test, feat, fix, chore, docs), each body explains the why not just the what, and each
cites the exact test command plus result it produced. No AI attribution footers found in any commit
message (`git log` reviewed in full for this branch). Work-unit ordering is RED, then GREEN, then
regression-fix, then tooling, then docs, matching Strict TDD's mandated sequence exactly. No unrelated
files swept into any commit (diff-per-commit reviewed above, each touches only what its message
claims).

### Issues Found

CRITICAL: None.

WARNING:
1. Config-missing failure mode changed from per-request `500` to app-startup fail-fast (Sign Parity
   Audit item 1) -- a deliberate, documented-in-code improvement per ADR-4, but not literally
   identical to the legacy contract; record this explicitly in the eventual 2.3 parity PR description
   so it is not mistaken for a live parity mismatch when `verify_sign_parity.py` is finally run.
2. Missing-Bearer-plus-bad-body request now returns `401` where legacy returned `400` (Sign Parity
   Audit item 2) -- consistent with every other route's auth-dependency ordering in this backend and
   directly tested, but not explicitly covered by the spec's scenario text. No action required; noted
   for completeness.
3. Entirely-missing `codigo`/`slot` field now returns FastAPI's automatic `422` instead of the
   legacy's `400 bad-request` (Sign Parity Audit item 3) -- untested edge case, not in the spec's
   explicit scenario list. Recommend `verify_sign_parity.py` gain a missing-field case before it is
   relied on as the sole parity gate at task 2.3 time.
4. Diff size (449/24 across 9 files, backend-only) exceeds both the slice's own forecast and the
   400-line single-PR budget -- the apply agent's proposed split (extract `verify_sign_parity.py`
   into its own follow-up PR) is sound and independently confirmed low-risk; adopt it when this
   branch is actually opened as a PR.

SUGGESTION: None beyond what is folded into WARNING 3 above.

### Verdict

**PASS WITH WARNINGS.**

45/45 tests pass on independent re-run, including the 6 new `/api/sign` cases. The RED failure for
2.1/2.2 was independently reproduced in a scratch worktree at the exact RED commit and is legitimate
-- not a trivial/fake RED. Presign acceptance semantics (codigo regex, slot range, S3 key shape,
content-type binding, expiry, response shape) are byte-for-byte identical to the legacy signer with
the one spec-authorized delta (Bearer-header RS256 verification replacing body-idToken
accounts:lookup); three additional, narrower behavioral differences were found in
error-classification edge cases (config-missing status code, validation ordering on
simultaneously-bad auth+body, missing-field 422-vs-400) and are documented above as WARNINGs, none of
which are exploitable or spec-blocking. Security review found no exploitable presign parameter, no
unauthenticated code path to a presigned URL, and no hardcoded secrets. The flagged ADR-4
interpretation (`s3()` outside `WEB_STARTUP_CLIENTS`, relying on the router's own `REQUIRED_CLIENTS`
union) is a correct, literal application of ADR-4's own declaration mechanism, not a deviation -- no
remediation needed as long as `sign` stays unconditionally mounted, which it currently is.
`formulario/js/form.js` is confirmed genuinely untouched (zero diff vs `main`), and
`verify_sign_parity.py` is confirmed to make no live network call unless both `NEW_SIGN_URL` and
`FIREBASE_ID_TOKEN` are explicitly set. Commit hygiene is clean.

**Safe to merge to main**, PROVIDED: (1) tasks 2.3-2.5 stay blocked and unchecked until manual task
1.4 (Railway web service creation) lands -- this branch does not attempt to close them, correctly;
(2) `verify_sign_parity.py` is split into its own follow-up PR per the apply agent's own
recommendation, to bring the router PR under the 400-line budget; (3) the three WARNING-level parity
edge cases above are carried forward as known, non-blocking differences (not silently forgotten) into
the 2.3 parity-verification PR description once 1.4 unblocks it. Zero production risk either way:
`formulario/` and `services/photo-signer/` are both untouched, so nothing this branch does can affect
live field-inspector photo uploads until 2.4's atomic repoint ships later, gated on 2.3's real parity
pass.

---

## Slice 3 — `reportados` (unified day-walk + snapshot)

Verified fresh-context on branch `feat/fastapi-consolidation-3-reportados`
(`git branch --show-current` confirmed), against `main` at merge-base `1b6006a`. Re-ran the full
test suite, independently reproduced RED evidence in disposable `git worktree`s, and diffed
`api/reportados.js` / `scripts/fetch_reportes_api.py` line-by-line against
`backend/app/services/atencionsismo.py` / `snapshot.py` / `routers/reportados.py`.

### Test suite

`python -m pytest backend/tests/ -v` -> **95 passed, 0 failed** (independently re-run, matches
apply-progress claim exactly).

### RED evidence legitimacy (independently reproduced)

Used disposable `git worktree`s (not `git checkout -- .`, which silently leaves later-commit files
in place and would have produced a false negative) to check out each RED commit in isolation:

- `3a63b7b` (atencionsismo RED): `ImportError: cannot import name atencionsismo from
  app.services` - 1 collection error. Legitimate.
- `65f7623` (reportados router RED): `ImportError: cannot import name reportados from
  app.routers` - 6 failed. Legitimate.

Both match the RED evidence recorded in `apply-progress.md` exactly. Not fabricated/trivial REDs.

### Day-walk parity audit -- `api/reportados.js` vs `backend/app/services/atencionsismo.py`

Field-by-field comparison, both source files read in full:

| Aspect | `api/reportados.js` | `atencionsismo.py` | Match |
|---|---|---|---|
| Split status set | `{413,500,502,503,504}` | `SPLITTABLE_STATUSES = {413,500,502,503,504}` | Yes |
| `MIN_WINDOW_MS` | 60_000 | `MIN_WINDOW_MS = 60_000` | Yes |
| `CONCURRENCY` | 4 | `CONCURRENCY = 4` | Yes |
| Retry attempts / backoff | 3 attempts, 2000ms sleep | `MAX_ATTEMPTS=3`, `RETRY_SLEEP_S=2.0` | Yes |
| Request/probe timeouts | 90s / 15s | `REQUEST_TIMEOUT_S=90.0` / `PROBE_TIMEOUT_S=15.0` | Yes |
| Split recursion | sequential (not `Promise.all`) | sequential (await first half, then second) | Yes |
| Probe (1-min window pre-flight) | `probeApi`, 413/504 = alive | `probe_api`, 413/504 in accepted set | Yes |
| Concurrency batching | `for (i+=CONCURRENCY) Promise.all(batch)` | `for i in range(0,len,CONCURRENCY): asyncio.gather(batch)` | Yes |
| Sequential failed-window retry pass | `failedWindows.splice(0)`, sequential re-fetch | `failed_windows[:]` + `.clear()`, sequential re-fetch | Yes |
| Dedup key | `id` (Map) | `id` (dict) | Yes |
| coordKey null/(0,0) exclusion | non-finite or (0,0) -> null | non-finite or (0,0) -> None | Yes |
| Response shape | ok, generado, fuente, total, inmuebles, por_estadoVerificacion | identical keys, same values | Yes |
| `DEFAULT_USER` | juanp.gzmz@gmail.com | same (confirmed against both legacy sources) | Yes |
| Zero-total guard | 502 in JS (own request-scoped serving) | `ApiEmptyResultError` (mapped in caller) | Behaviorally equivalent -- see below |

No behavioral difference found in the day-walk/split-retry/dedup algorithm itself. The
`scripts/fetch_reportes_api.py` narrower `{413,504}` split set and its `AuthError` fast-abort
special-case were correctly NOT ported -- the apply agent's documented choice to follow the JS's
(currently-live) wider, more-defensive behavior is the right call and is explicitly logged in both
`atencionsismo.py`'s module docstring and `apply-progress.md`.

One structural (non-behavioral) difference: JS's zero-total guard returns HTTP 502 directly from
the request handler (`api/reportados.js`'s serverless function IS the per-request day-walk caller).
The new architecture never runs the day-walk inside a request (ADR-5's explicit design point) -- the
guard now lives in `atencionsismo.fetch_reportados()` as `ApiEmptyResultError`, caught by
`snapshot.refresh_loop()`'s broad `except Exception`, logged, and the previous good snapshot (if
any) keeps serving. This is a spec-compliant, design-intended architectural shift (ADR-5: "never
inline ~150s fetch in a request"), not a parity regression -- flagged as informational only.

### Snapshot semantics

- **<2s serving path**: `routers/reportados.py`'s handler does exactly one synchronous
  `snap.get()` (an in-memory dict/attribute read) -- no `await`, no network call in the request path.
  Confirmed by direct source read; the only I/O in the whole reportados code path happens in the
  lifespan-owned background task, never inside a request.
- **Staleness bound**: `ReportadosSnapshot.get()` raises `SnapshotStaleError` when
  `age > STALE_AFTER_S` (86400s), matching design.md ADR-5's "same outer bound the CDN had." The
  900s refresh interval matches the retired `s-maxage=900`. Verified via
  `test_get_raises_stale_when_entry_exceeds_86400s_bound` and
  `test_get_serves_snapshot_within_86400s_even_if_older_than_900s` (both pass).
- **Cold-start behavior**: `_lifespan` best-effort Blob-seeds (`seed_from_blob`, never raises) before
  starting the forever-refresh task. Manually re-verified end-to-end with
  `with TestClient(create_app()) as client:` (misconfigured env, no `VISITADOS_API_PASS`,
  no `REPORTES_BLOB_URL`): `GET /reportados` -> `503` + `Retry-After: 60` as claimed, clean shutdown,
  zero leaked-task warnings under `warnings.catch_warnings(record=True)`.
- **Error paths**: API down (probe/day-walk failure) or Blob seed failure both degrade to the
  previous snapshot continuing to serve (if one exists) or `503 + Retry-After: 60` (if none does) --
  never a 500, never an inline retry inside the request. Matches ADR-5 intent exactly.
- **Lifespan task cancellation on shutdown**: confirmed via manual smoke test above -- `task.cancel()`
  + `await task` inside `contextlib.suppress(asyncio.CancelledError)` produces a clean shutdown with
  no stray-task warning.

### No-auth route + Cache-Control parity

`routers/reportados.py`'s `get_reportados` has no `Depends(require_auth)`/`Depends(require_role)` --
confirmed by direct source read (grep for `Depends`/`require_auth` in the file returns nothing).
`CACHE_CONTROL = "public, s-maxage=900, stale-while-revalidate=86400"` is byte-identical to
`api/reportados.js`'s `res.setHeader('Cache-Control', ...)` value. Both confirmed by test
(`test_response_preserves_legacy_cache_control_header`) and independent full-suite re-run.

### Flagged interpretation (a): Blob-seed age semantics

Design ADR-5 says cold start should "serve immediately with its age" -- ambiguous between
download-time and the cron's `reportes_meta.json.generated_at`. The implementation measures age from
this process's download time. **Ruling: compliant.** None of backend-platform's three
"In-Process Caching..." spec scenarios require the seeded age to reflect the cron's original publish
time -- they require `X-Snapshot-Age` present and the 86400s hard bound enforced, both of which hold
regardless of which clock anchors "age." Noted as a real, if minor, accuracy gap worth a SUGGESTION
below (not a spec violation): a Blob snapshot published by a 15-minute-cadence cron could itself
already be up to ~15 minutes stale at the moment this process downloads it, and the seeded entry
reports `age~=0` rather than reflecting that pre-existing staleness. This only matters during the
cold-start window before the first live refresh completes.

### Flagged interpretation (b): VISITADOS_API_PASS not in startup fail-fast union

Confirmed in code: `routers/reportados.py`'s `REQUIRED_CLIENTS = ()`, and
`credentials/clients.py`'s `WEB_STARTUP_CLIENTS = ("sismo",)` -- `VISITADOS_API_PASS` is never
validated at `create_app()` startup; it is read lazily by `atencionsismo.credentials_from_env()`
only when a refresh actually runs, and a missing/invalid value is caught by `refresh_loop`'s broad
`except Exception`, logged, loop continues. **Ruling: compliant.** ADR-4's table entry ("plain
secrets... fail-fast only if a mounted route needs it") is ambiguous about whether "the route" means
the request-handling code path or the feature as a whole, but ADR-5's own text is unambiguous and
directly on point: it explicitly designs for graceful 503-degradation as the intended behavior for
every reportados failure mode, including a missing credential, and states this in-process-refresh
design is why the web service is always-on. No backend-platform spec scenario requires reportados
to crash startup on a missing `VISITADOS_API_PASS`. No remediation needed.

### web/js/api-config.js inertness

Confirmed via `grep -rln "api-config"` across the full repo (`web/`, `formulario/`, and root): the
only match is the file itself. No consumer (`data.js`, `main.js`, `stickers.js`, etc.) imports it.
`web/js/data.js`'s `refreshReportados()` still calls the hardcoded relative `/api/reportados` path
directly -- confirmed by direct source read. Zero production behavior change this batch, as claimed.

### Commit hygiene

Seven commits, each single-purpose (RED/GREEN pairs + one final chore commit for the two
non-test-covered artifacts). No secrets, no unrelated files, no `.env` leakage. `git diff --stat
main...HEAD` (merge-base-relative, the correct comparison for a divergent branch) touches exactly
the 9 backend files + `web/js/api-config.js` claimed in `apply-progress.md`'s "Files Changed" table
-- nothing extra, nothing missing.

### CRITICAL

None.

### WARNING

1. **Branch is 1 commit behind main** (`git log --oneline main ^HEAD` -> `a8f98ce
   fix(backend): Dockerfile CMD -- use uvicorn --factory and honor Railway PORT`). This branch
   diverged from `main` at the slice-2 merge point (`1b6006a`) and never picked up this later,
   independently-landed hotfix. Using the wrong diff direction (`git diff main..HEAD`, 2-dot) makes
   this look like slice 3 regresses the Dockerfile CMD back to the broken `uvicorn app.main:app`
   form (main.py exposes only `create_app()`, no module-level `app` -- that CMD would crash the
   container). The correct, merge-base-relative diff (`git diff main...HEAD -- backend/Dockerfile`,
   3-dot) is empty -- this branch's own commits never touch `Dockerfile`, so a standard merge
   commit, rebase, or GitHub squash-merge would all correctly preserve main's current (fixed)
   Dockerfile automatically, with zero conflict. Still, recommend rebasing this branch onto current
   `main` before opening the PR, purely for reviewer clarity -- a reviewer running a naive 2-dot diff
   locally (as this verification initially did, before correcting to the 3-dot comparison) will be
   misled into thinking the PR reverts a production fix, when it does not.
2. **Diff size**: `git diff --stat main...HEAD` shows 1708 insertions across 12 files including the
   two openspec docs; the code-only figure (`backend/` + `web/js/api-config.js`) is 1434 insertions,
   3 deletions -- well above the slice's own ~350-450 line forecast and above the chained-PR skill's
   400-line single-lens-review budget. `apply-progress.md` already self-flags this and recommends
   the exact 3a/3b split the original Review Workload Forecast proposed, mapped onto existing commit
   boundaries with no history rewrite needed. Independently confirmed sound: 3a = `3a63b7b..b0a73a9`
   (atencionsismo test+impl, 684 lines) / 3b = `a481525..5e292c7` (snapshot+router+lifespan+parity
   script+api-config.js, 750 lines). Recommend applying this split before opening the PR(s),
   consistent with the cached auto-chain/stacked-to-main delivery strategy.
3. **Blob-seed age accuracy** (see Flagged interpretation (a) above): the seeded snapshot's reported
   `X-Snapshot-Age` can understate true data staleness by up to the cron's own refresh cadence during
   the cold-start window. Not a spec violation (see ruling above) but worth a one-line code comment
   or a small follow-up if a future consumer ever treats `X-Snapshot-Age` as an exact freshness SLA
   rather than an internal cache-serving signal.

### SUGGESTION

1. Same item as WARNING 3 -- a small, isolated follow-up (fetch `reportes_meta.json`'s
   `generated_at` and anchor `fetched_at` to it) would close the gap entirely if ever needed; the
   apply agent already correctly scoped `seed_from_blob`'s signature to make this a one-function
   change, not a redesign.
2. Consider adding a code comment in `credentials/clients.py` or `atencionsismo.py` cross-referencing
   the ADR-4/ADR-5 interpretation ruling above (interpretation (b)), so a future reader does not
   re-open the same ambiguity without finding this verify report.

### Verdict

**PASS WITH WARNINGS.**

95/95 tests pass on independent re-run. RED evidence for 3.1 and 3.5 (the router's un-tasked but
Strict-TDD-required RED) was independently reproduced in disposable `git worktree`s at the exact RED
commits and is legitimate. The day-walk/split-retry/probe/dedup/coordKey algorithm in
`atencionsismo.py` is a field-for-field behavioral match to the CURRENTLY LIVE `api/reportados.js`
(not the narrower `scripts/fetch_reportes_api.py`), which is the correct choice per task 3.1's own
instructions and is explicitly documented as such. The <2s serving path is real (no network in the
request handler), the 15-minute/86400s staleness bounds are correctly enforced and tested, the route
requires no auth, and the Cache-Control header is byte-identical to the legacy value. Both flagged
design-interpretation questions -- (a) Blob-seed age semantics, (b) VISITADOS_API_PASS fail-fast
scope -- are ruled COMPLIANT against the actual spec scenarios and ADR-5's explicit
graceful-degradation design intent -- no remediation required for either. `web/js/api-config.js` is
confirmed genuinely inert (zero consumers repo-wide). Commit hygiene is clean.

**Safe to merge to main**, PROVIDED: (1) tasks 3.6/3.7 stay blocked and unchecked until manual task
1.4 (Railway web service creation) lands -- this branch correctly does not attempt to close them;
(2) this branch is rebased onto current `main` before opening the PR to pick up the unrelated
`a8f98ce` Dockerfile hotfix and avoid reviewer confusion from a stale 2-dot diff (WARNING 1 -- not a
real regression under any standard merge strategy, but worth doing for clarity); (3) the diff is
split into the 3a/3b chained PRs both the original forecast and `apply-progress.md` already
recommend, to stay within the 400-line review budget (WARNING 2). Zero production risk either way:
`web/js/data.js` is confirmed genuinely untouched, so nothing this branch does can affect the live
"Reportados" KPI card until 3.7's atomic repoint ships later, gated on 3.6's real parity pass against
a live Railway URL.
