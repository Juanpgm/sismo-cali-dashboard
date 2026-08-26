# Proposal: FastAPI Backend Consolidation

## Intent

Consolidate all backend execution (8 Vercel functions in `api/*.js`, the orphaned `services/photo-signer`, 6 Railway cron jobs) into one FastAPI application deployed on Railway. Concrete pains from exploration:

- **Fragile, non-reproducible deploys**: `railway up --path-as-root` saga (cruce-sticker crashed with `python: command not found`, 2026-08-25); photo-signer deployed manually, not git-connected, already caused a silent production incident (all photo uploads 401'd after the Firebase project migration).
- **Duplicated logic**: atencionsismo day-walk algorithm implemented twice (JS `api/reportados.js` + Python `scripts/fetch_reportes_api.py`); two independent token-verification implementations.
- **Platform limits**: `reportados` runs ~150s against Vercel's 300s cap; warm-lambda-only caching in `sticker-status`.
- **Credential sprawl**: ≥3 service accounts spread across 3 Vercel projects + 1 Railway project with no single source of truth.

## Scope

### In Scope
- One FastAPI app (this repo, git-connected Railway deploy) serving all 8 `api/*.js` routes plus `/api/sign` (photo signer).
- All 6 Railway cron jobs migrated per-job to the same image via Railway-native cron services; `integracion_F1` job code absorbed so it stops being a separate deploy unit.
- Exact port of the auth matrix: `verifyFirebaseToken` semantics and the `roleFrom` precedence chain (SUPERADMIN_EMAIL → claim `role` → `@sismocali.gov.co` → `password` provider → `google.com` + `@cali.gov.co`) preserved verbatim, with parity tests.
- Universal CORS (origin allowlist: dashboard, formulario, localhost dev).
- Parallel run + per-consumer cutover; decommission of old Vercel functions/projects and CLI-upload Railway services at the end.

### Out of Scope (non-goals)
- **Browser-direct client-SDK Firestore reads stay as-is** (dagma public collections, `inspectores`/`evaluaciones` rules-enforced access, client-side `evaluaciones` transaction). Rationale: they are rules-enforced or public-read by design; proxying adds latency and a bigger blast radius with zero security gain. The missing `firebase.json`/rules-deploy debt is flagged but fixed separately.
- `web/` and `formulario/` remain Vercel-static; public Survey123 attachment URLs untouched.
- No migration of `dagma-85aad` data into `sismo-agosto-sgred`; no Firestore rules rewrite.

## Capabilities

### New Capabilities
- `backend-platform`: consolidated FastAPI service — route parity, ported auth (verifier + roleFrom), multi-SA credential selection, universal CORS, in-process caching, reproducible git-connected deploy.
- `job-scheduling`: the 6 cron jobs as Railway-native cron services running per-job commands on the consolidated image (schedules per `railway_setup.py` SERVICES).

### Modified Capabilities
- `inspection-photo-capture`: signer endpoint host moves to the consolidated app; token verification unified onto the shared verifier (acceptance semantics unchanged).
- `field-form-session`: `DASHBOARD_API`/`FOTO_SIGNER_URL` repointed to the consolidated app.
- `sticker-code-assignment`, `user-management`, `data-sources-analista`: behavior unchanged at spec level; delta specs only where an endpoint host is a stated requirement.

## Approach

- **App layout**: single package — `routers/` (one per legacy function), `auth/` (verifier + `roleFrom` as pure functions), `credentials/`, `jobs/` (one entry module per cron), `services/` (shared atencionsismo client, single implementation).
- **Credentials**: env-var matrix — three SA JSON env vars (sismo, dagma, sheets). A credentials module exposes named clients; web-route credentials fail fast at startup, job-only ones load lazily.
- **Cron strategy**: **Railway-native cron on the same image** (not APScheduler). Preserves today's fault isolation (a stuck job cannot take the API down; API deploys cannot skip job runs), keeps `railway_setup.py`-style drift-only provisioning, and long jobs (5+ min) never share a process with request serving. One codebase, one image, N Railway services with distinct commands/schedules.
- **CORS**: `CORSMiddleware` with explicit origin allowlist; Bearer tokens, no cookies.
- **CDN replacement**: always-on process enables real caches. `reportados`: background-refreshed in-memory snapshot (removes the ~150s user wait); `sticker-status`: working 5-min TTL cache. Keep `Cache-Control` headers so a CDN can be re-fronted later.
- **Cutover mechanics**: new base URL deployed alongside; each consumer repoints one config constant per endpoint; parity checks before switching; rollback = repoint back. Old functions removed only after all consumers verified.
- **Deploy model**: git-connected Railway deploy from this repo with an in-repo Dockerfile and pinned root directory. No CLI uploads — the `--path-as-root` failure mode becomes structurally impossible.
- **Testing**: pytest + FastAPI TestClient across the app (matches `integracion_F1` convention). Strict TDD: every apply slice is test-first; the `roleFrom` matrix gets a parity suite mirroring the JS tests.

## Migration Slices (PR chain, stacked-to-main)

| # | Slice | Notes |
|---|---|---|
| 1 | Scaffold: app skeleton, git-connected Railway service, health route, credentials module, CORS, auth port + parity tests | No consumer traffic yet |
| 2 | Photo signer `/api/sign`; repoint `FOTO_SIGNER_URL`; retire manual Vercel project | Lowest blast radius, fixes worst deploy debt |
| 3 | `reportados` (unified Python day-walk + background snapshot); repoint `web/js/data.js` | Public route, easy parity diff |
| 4 | `sticker-status` + `source-status`; repoint web consumers | Read-only, low logic |
| 5 | `inspector-asignaciones`; repoint formulario `DASHBOARD_API` | Completes formulario cutover |
| 6 | `refresh` endpoint (Railway GraphQL / direct job trigger) | Re-wire the Vercel↔Railway coupling |
| 7 | Crons per-job onto the consolidated image (dashboard-refresh first — code already in this repo — then the 5 integracion_F1 jobs) | Can interleave from slice 2 onward |
| 8 | Admin CRUD: `stickers`, `sticker-asignaciones`, `usuarios` | Heaviest auth logic, moves last |
| 9 | Decommission: delete `api/*.js`, `services/photo-signer`, CLI-upload Railway services; final parity sign-off | Terminal cleanup |

## Answers to the 8 Open Questions (binding for design)

1. **Browser-direct Firestore reads**: stay rules-enforced client SDK — no security gain and larger blast radius behind the API.
2. **Cron scheduling**: Railway-native cron on the shared image — keeps fault isolation and provisioning style; APScheduler would couple job reliability to API restarts.
3. **Credentials**: env-var matrix with a named-client credentials module; dagma data migration is a separate, bigger decision — not now.
4. **CDN loss**: accept it; replace with in-process TTL caches + background snapshot for `reportados` (net UX improvement); keep cache headers for optional future CDN.
5. **formulario URLs**: repoint constants in dedicated slices; a Vercel proxy would add a new deployable, contradicting consolidation.
6. **Tests**: pytest across the FastAPI app; legacy Node `assert` checks stay until each endpoint is deleted.
7. **Slice order**: as tabled above — signer/reportados first, admin CRUD last, crons per-job in parallel.
8. **Railway config**: plain always-on for the web service — the caching strategy depends on a warm process; crons don't need always-on.

## Scope Addendum (user directive, 2026-08-25, post-proposal)

### New capability: `survey-cali-collection` — Survey Cali inspections as a versioned Firestore collection

1. **Ingestion**: the existing 15-min refresh cadence continues, and additionally upserts the Survey Cali inspection records (the Survey123-sourced Panel rows) into a Firestore collection (working name `survey_cali`, project `sismo-agosto-sgred`). Idempotent and incremental by design: keyed by `GlobalID`, and only records whose source content actually changed since the last ingest are written (change detection via source `EditDate` and/or content hash — never a full rewrite of the collection per run). This mirrors the house pattern already proven in `cruce_sticker.py` (watermark + skip-unchanged).
2. **CRUD endpoints** (FastAPI, admin-gated): create/read/update/delete over `survey_cali` records to support future UI features. All mutations are PATCH/upsert-style (partial, merge semantics) — no full-document replaces.
3. **Change control & history**: every mutation (ingest updates included) records a revision — author (pipeline vs user uid), timestamp, and the changed fields — into a per-record history (e.g. `survey_cali/{id}/history/{rev}`). The UI must be able to (a) list a record's history, (b) view what changed per revision, (c) revert a record to a prior revision — revert itself is a new revision (append-only history; nothing is ever destroyed).
4. **Dashboard read model**: the dashboard shows ONLY the current (most recent) state of each record; history is reachable on demand, never in the default read path.
5. **Ingest-vs-edit conflict rule (design phase decides the final shape, recommended default)**: the incremental ingest only writes a field when the UPSTREAM value changed since the previous ingest — so a manual edit survives pipeline runs unless the source itself moved, in which case the source value wins and the overwrite is visible (and revertible) in the history. This follows the split-ownership precedent of `sticker_matches` (pipeline-owned vs admin-owned fields), refined to per-field change detection.

### Slice impact
- New slice 7b (with slice 7's cron work): `survey_cali` ingestion added to the refresh job.
- New slice 8b (with slice 8's admin CRUD): `survey_cali` CRUD + history/revert endpoints.
- Dashboard UI for history/revert is a SEPARATE future change — this change delivers the API and the read model only.

## Scope Exclusion Addendum (user directive, 2026-08-25, post-spec)

### Google Sheets is EXCLUDED from the consolidation

The user confirmed Google Sheets data is no longer read by anything. Consequences, binding for design/tasks:

1. **Credentials matrix drops to 2 SAs** (sismo + dagma). The Sheets/geocode "pmegeocode" SA is not ported into the new app's credentials module. (If the geocoding step of refresh_data.py uses a plain `GOOGLE_MAPS_API_KEY` rather than that SA — exploration suggests it does — geocoding is unaffected; design phase verifies.)
2. **The `normalizador` cron does NOT migrate.** Its sole purpose is pushing `tabla_integrada`/`integracion_stats` to the EDAN Google Sheet. It is excluded from the job-scheduling capability; it stays as-is on Railway until slice 9, where it is decommissioned along with the other legacy services — flagged for explicit operator confirmation at that point (in case any human still consults the EDAN sheet).
3. **No Sheets client / export_sheets.py port**: `integracion/export_sheets.py` and any Sheets read/write path in the absorbed job modules is dead code from the new app's perspective — absorbed job code is ported WITHOUT its Sheets branches. Design phase identifies which modules import it (`job.py`/pipeline only, expected) and records the cut.
4. Spec impact: `job-scheduling/spec.md` covers 5 migrated crons, not 6 (`integracion-f3`, `asignaciones`, `cruce-gestion`, `cruce-sticker`, `dashboard-refresh`); `backend-platform`'s credentials requirement covers 2 named clients, not 3.

### Extension (user decision, 2026-08-25, post-tasks): `integracion-f3` and `asignaciones` are ALSO excluded

Phase-0 verification found their gspread branches LIVE (only F3 input `F3_SRC_TAB`, only output sink the VISITAS/asignaciones tabs). The user confirmed those Sheets are not used by anyone anymore — so both jobs join `normalizador` in the excluded set rather than having their I/O replaced:

1. **Migrated cron set is THREE services**: `cruce-gestion`, `cruce-sticker`, `dashboard-refresh`.
2. **Excluded legacy set is three jobs**: `normalizador`, `integracion-f3`, `asignaciones` — all stay as-is on the legacy `integracion_F1` image until slice 9, where each is decommissioned pending explicit operator confirmation.
3. **Absorbed `integracion_F1` entry points drop to two**: `job_cruce.py`/`cruce_criticos_survey.py` and `job_sticker.py`/`cruce_sticker.py` (plus their shared `integracion/` dependencies, Sheets branches cut). `dashboard-refresh` code already lives in this repo.
4. `integracion_F1` remains a deploy unit solely for the three excluded legacy jobs until their decommission.
5. Tasks impact: escalation stubs 7.11/7.12 resolve to "excluded — no migration tasks"; slice 9's decommission checklist covers three legacy cron services plus the previously listed Vercel artifacts.

### Extension 2 (user directive, 2026-08-25, post-slice-1a): NOTHING dagma-related is used

The user directed: "no usar nada relacionado con el dagma". Binding consequences for the NEW backend (browser-direct client-SDK reads were already out of scope and stay untouched):

1. **Credentials: ONE service account** (sismo `FIREBASE_SERVICE_ACCOUNT_JSON`). The `dagma` named client — including the lazy `dagma()` client already scaffolded in slice 1a — is REMOVED. The credentials module holds exactly one client.
2. **`cruce-gestion` is EXCLUDED from migration** (its sole purpose is writing Firestore `dagma-85aad`/`cruce_criticos_survey`). It joins the legacy excluded set, which is now FOUR jobs: `normalizador`, `integracion-f3`, `asignaciones`, `cruce-gestion` — all stay on the legacy `integracion_F1` image until slice 9, each decommissioned pending explicit operator confirmation.
3. **Migrated cron set is TWO services**: `cruce-sticker`, `dashboard-refresh`.
4. **Absorbed `integracion_F1` entry points drop to ONE**: `job_sticker.py`/`cruce_sticker.py` (plus its shared `integracion/` dependencies, Sheets branches cut).
5. **Slice 6 (`/refresh` port)**: the consolidated endpoint redeploys/triggers only `dashboard-refresh` — the legacy endpoint's fail-soft `cruce-gestion` redeploy is NOT ported.
6. No dagma credential, project id, collection name, or API constant appears anywhere in `backend/`.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Single point of failure vs today's 7 independent units | Med | Crons stay separate Railway services (same image); per-consumer cutover means old paths remain until verified; health checks + restart policy |
| `sticker_matches`/`cuadrillas` have no Firestore rules — "backend sole writer" must survive | Med | Invariant documented in spec; only admin-SDK writers in the new app touch them; verify phase checks no new write paths |
| Auth regression in `roleFrom`/verifier port | Med | Parity test suite ported from JS matrix before any admin route moves; admin routes migrate last |
| `reportados` JS→Python behavior drift | Med | Single unified implementation, side-by-side parity diff against live Vercel output before repoint |
| CORS misconfig blocks production frontends | Low | Allowlist tested per slice; old endpoint remains as rollback |
| Non-reproducible deploy repeats (`--path-as-root`) | Low | Git-connected deploy only; CLI-upload path removed; Dockerfile in repo |
| Credential leakage/misuse across 3 SAs | Low | Named-client module is the only access path; per-route/job least-privilege selection |
| `integracion_F1` repo boundary (separate git repo) | Med | Per-job code absorption with explicit provenance; integracion_F1 stops being a deploy unit only after its last job migrates |

## Rollback Plan

Per endpoint: repoint the consumer config constant back to the old Vercel URL (old functions stay live until slice 9). Per cron: repoint the Railway service back to the old image/command. Slice 9 (decommission) only runs after every consumer is verified; before it, rollback is always a config revert, never a redeploy of deleted code.

## Dependencies

- Railway GitHub integration enabled for this repo.
- The 3 SA JSONs + existing secrets (`BLOB_READ_WRITE_TOKEN`, S3 keys, `INSPECTIONS_URL`, etc.) provisioned as Railway env vars.
- Access to redeploy `formulario/` and `web/` on Vercel for repoint slices.

## Continuity Guarantees (user directive, 2026-08-25, binding at every cutover and at slice 9)

1. **Endpoint completeness**: no consumer (web/, formulario/) may ever hit a missing endpoint — every legacy route exists on the consolidated app and answers with contract parity BEFORE its consumer repoints, and the slice-9 decommission gate re-checks the full route inventory one final time.
2. **Config completeness**: every env var / secret each route or job needs is provisioned on the Railway service before its slice cuts over (startup fail-fast enforces the web set; job sets are checked per job slice).
3. **Firestore collections**: every collection the apps depend on (inspectores, evaluaciones, sticker_matches, cuadrillas, inspecciones_israel reads, and the new survey_cali + its history) exists and is reachable through its designated access path before any consumer depends on it; survey_cali ingestion seeds the collection before any UI reads it.
4. **Live data over cached files where not necessary**: in-process/live serving is the default for API responses (e.g. reportados serves from a live-refreshed in-memory snapshot, never a stale file); Blob-published files remain ONLY where they are genuinely necessary today (the static dashboard's inspections/meta/geojson fetch contract, xlsx export). Any future move of those to live API reads is a separate change — this change must not silently add NEW cached-file indirection.

## Success Criteria

- [ ] All consumers (`web/js/*`, `formulario/js/*`) point at the consolidated app; parity checks green per endpoint before each switch.
- [ ] All 6 crons run from the git-connected image on their `railway_setup.py` schedules; watermark/idempotency behavior unchanged.
- [ ] `roleFrom` parity suite passes; per-route auth matrix identical to today.
- [ ] `api/*.js`, `services/photo-signer`, and CLI-upload Railway services deleted; no manual deploy path remains.
- [ ] `reportados` response served from snapshot in <2s (vs ~150s today); `sticker-status` cache hit rate observable.
- [ ] Zero production downtime during migration (old endpoints live until each cutover verified).
