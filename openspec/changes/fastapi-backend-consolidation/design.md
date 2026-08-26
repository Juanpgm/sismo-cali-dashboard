# Design: FastAPI Backend Consolidation

Change: `fastapi-backend-consolidation` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-design

Reads: `proposal.md` (its 8 answered questions are binding), `exploration.md`. Mirrors real code:
`api/refresh.js` (verifier + `roleFrom`), `integracion_F1/scripts/railway_setup.py` (drift-only
provisioning, `SERVICES`), `integracion_F1/job_*.py` + `integracion/runlog.py` (runs.jsonl tee),
`scripts/fetch_reportes_api.py` (Python day-walk), `web/js/main.js`/`data.js` (relative `/api/*`
fetches), `formulario/js/form.js` (`DASHBOARD_API`, `FOTO_SIGNER_URL`).

## Target architecture at a glance

```
 Browsers (Vercel static)                     Railway project (git-connected, ONE image from backend/Dockerfile)
 ────────────────────────                     ────────────────────────────────────────────────────────────────
 web/js/*  ──Bearer──┐                        ┌─ web service (always-on)  uvicorn app.main:app ─────────────┐
 formulario/js/* ────┤   CORS allowlist       │  routers: refresh reportados stickers sticker-status        │
                     ├──HTTPS───────────────► │           sticker-asignaciones inspector-asignaciones       │
 (public: reportados)┘                        │           usuarios source-status sign health                │
                                              │  background task: reportados snapshot refresher             │
                                              └──────┬───────────────────────┬──────────┬───────────────────┘
                                                     │                       │          │
   Firestore sismo-agosto-sgred ◄────────────────────┘                       │          └─► Railway GraphQL
   (inspectores, evaluaciones,                                               │              (refresh endpoint →
    sticker_matches, cuadrillas,                                             │               dashboard-refresh only)
    survey_cali + history subcoll,                                atencionsismo API · AWS S3 (presign)
    _meta, Firebase Auth CRUD)                                    Survey123 · Vercel Blob (NO Sheets — excluded)
   [NOTHING dagma-related in the new backend — Extension 2. Browser-direct client-SDK reads of
    dagma-85aad public collections continue as today, out of scope/untouched.]
 2 cron services (SAME image, distinct startCommand + cronSchedule):
   cruce-sticker · dashboard-refresh
   each: python -m app.jobs.<job>  →  runs.jsonl (runlog tee, volume) → same external targets as today
 (normalizador, integracion-f3, asignaciones, cruce-gestion do NOT migrate — Sheets-only or
  dagma-only I/O; they stay on the legacy integracion_F1 image until slice 9 decommission,
  operator-confirmed each)
```

---

## ADR-1 — Repository layout & app structure

**Decision.** New top-level `backend/` directory in THIS repo; Railway services are git-connected
to this repo with **root directory = repo root** and **dockerfilePath = `backend/Dockerfile`**
(pinned in repo-root `railway.json` build config). Root stays the repo root — not `backend/` —
because the `dashboard-refresh` job needs `scripts/refresh_data.py`, `scripts/fetch_reportes_api.py`
and `deploy/` assets in the build context; the Dockerfile COPYs `backend/` + `scripts/` + `deploy/`
explicitly.

```
backend/
├── Dockerfile                  # python:3.12-slim; COPY backend/ scripts/ deploy/; CMD uvicorn app.main:app
├── requirements.txt
├── app/
│   ├── main.py                 # create_app() factory: CORS, routers, startup fail-fast, snapshot lifespan
│   ├── config.py               # Settings: env var names, CORS allowlist, per-module required clients
│   ├── auth/
│   │   ├── verify.py           # verifyFirebaseToken port (x509 fetch/cache/rotation)
│   │   ├── roles.py            # role_from / role_from_claims — pure functions
│   │   └── deps.py             # FastAPI Depends: require_auth, require_role("admin"), current_claims
│   ├── credentials/
│   │   └── clients.py          # named memoized clients: sismo(), s3(), blob token — NO sheets, NO dagma (excluded)
│   ├── routers/                # one module per legacy function, same route paths (/api/...)
│   │   ├── refresh.py reportados.py stickers.py sticker_status.py sticker_asignaciones.py
│   │   ├── inspector_asignaciones.py usuarios.py source_status.py sign.py health.py
│   │   └── survey_cali.py      # NEW capability: survey_cali CRUD + history/revert (ADR-12)
│   ├── services/
│   │   ├── atencionsismo.py    # THE single day-walk/split-retry implementation
│   │   ├── snapshot.py         # reportados in-memory snapshot + background refresh loop
│   │   └── survey_cali.py      # sole survey_cali Firestore access: mutation core + queries (ADR-10..12)
│   ├── jobs/                   # cron entrypoints, `python -m app.jobs.<name>` each
│   │   ├── cruce_sticker.py dashboard_refresh.py
│   │   │                       # no normalizador/integracion_f3/asignaciones/cruce_gestion — Sheets/dagma, excluded
│   └── integracion/            # absorbed integracion_F1 modules (ADR-2), incl. runlog.py
│       └── PROVENANCE.md
├── scripts/
│   └── railway_services.py     # SERVICES source of truth + drift-only apply (ADR-6)
└── tests/                      # pytest (ADR-8)
```

**Why the `--path-as-root` failure becomes structurally impossible.** There is no CLI upload
path at all: every Railway service (web + 2 crons) points at the same GitHub repo + same pinned
dockerfilePath; the build context is decided by git + `railway.json`, never by which directory an
operator ran `railway up` from. Wrong-root/wrong-language detection (Railpack seeing Node) cannot
occur because the Dockerfile is explicit.

- *Rejected:* Railway root = `backend/`. Breaks `dashboard-refresh` (needs `scripts/`, `deploy/`)
  or forces file moves in slice 1; repo-root context with explicit COPY is one decision, zero moves.
- *Rejected:* separate image for jobs. Proposal binds "one image, N services"; two images would
  reintroduce the drift class that broke cruce-sticker.

## ADR-2 — `integracion_F1` code absorption: copy-with-provenance per job slice

**Decision.** **Copy-with-provenance, per job slice** (the granularity of slice 7). Absorbed
integracion_F1 entry points drop to **ONE**: `job_sticker.py`/`cruce_sticker.py` (cruce-sticker)
— plus its shared `integracion/` dependencies with Sheets branches cut. (`dashboard-refresh` code
already lives in this repo; `normalizador`, `integracion-f3`, `asignaciones`, `cruce-gestion` are
excluded, see below.) The migrating job's PR copies exactly the modules it imports into
`backend/app/integracion/` (keeping module names so imports port mechanically), with:

1. A header in every copied file:
   `# Ported from Juanpgm/normalizador_data_sismo_cali@<short-sha> <original/path> (YYYY-MM-DD)`.
2. A row in `backend/app/integracion/PROVENANCE.md`: `file | source path | source SHA | copied date | job slice`.
3. A **freeze rule**: once a job's copy PR merges, that job's modules are frozen upstream — fixes
   land only in this repo. Enforced socially via PROVENANCE.md + a note in the integracion_F1 README
   at first copy.

`runlog.py` and shared config/credential helpers are copied in the FIRST job slice and reused by
later ones (PROVENANCE tracks the earliest copy).

**Sheets branches are CUT at copy time** (binding Scope Exclusion Addendum): ported job modules
are copied WITHOUT their Google Sheets code paths, and `integracion/export_sheets.py` is NOT
ported. Verified import map: `export_sheets` is imported only by `integracion_F1/job.py` (the
normalizador entrypoint — excluded) and `run_integration.py` (local CLI — not ported); no
migrating job imports it. If a shared `integracion/` dependency copied for the migrating job
carries an incidental Sheets branch, the copy PR records the removed branch (file, function,
spreadsheet id) in its PROVENANCE.md row (`cut: sheets` column).

**Four legacy jobs do not migrate** (user decisions, addendum extensions 1 and 2): `normalizador`
(EDAN Sheet push only), `integracion-f3` and `asignaciones` (their gspread I/O — F3 input tab,
VISITAS/`asignaciones` output tabs — is live in code but operationally dead; exclusion chosen
over I/O replacement), and `cruce-gestion` (its sole purpose is writing Firestore
`dagma-85aad`/`cruce_criticos_survey` — nothing dagma-related enters the new backend). All four
stay as-is on the legacy integracion_F1 image/services until slice 9, where EACH is
decommissioned pending explicit operator confirmation.

**When integracion_F1 stops being a deploy unit:** it remains a deploy unit SOLELY for the four
excluded legacy jobs. It ends after the migrating job runs green from the consolidated image
(slice 7 complete) AND the four legacy services are deleted in slice 9 (operator-confirmed
each). The repo then survives as analysis/notebook archive only; `railway_setup.py` there is
superseded by `backend/scripts/railway_services.py` (ADR-6).

- *Rejected:* git subtree/submodule. Imports the whole repo (notebooks, basemaps, unrelated
  scripts) into the image context, makes per-job slicing impossible, and subtree merges are a
  poor fit for a one-way, terminal migration.
- *Rejected:* pip dependency (`git+https://...`). Keeps integracion_F1 a live coupling point
  (build-time token, release coordination) — the opposite of "stops being a deploy unit".

## ADR-3 — Auth port: verifier, roleFrom, per-route Depends

**Decision.** Three modules, mirroring `api/refresh.js`'s zero-`firebase-admin`-for-verification
approach:

- **`auth/verify.py`** — `async verify_firebase_token(id_token, project_id) -> claims`. RS256
  against Google's rotating x509 certs (`securetoken@system.gserviceaccount.com` metadata URL),
  using `cryptography` to extract public keys. Cert cache TTL from the response's `Cache-Control:
  max-age`; **rotation handling**: unknown `kid` triggers exactly one forced refetch, then 401.
  Claim checks identical to JS: `iss`, `aud`, `exp`, `iat`, non-empty `sub`. The cert fetcher is an
  injectable callable so tests supply a fake keypair (no network).
- **`auth/roles.py`** — `role_from(email, claim_role, provider)` and `role_from_claims(claims)`:
  pure functions, precedence ported verbatim (SUPERADMIN_EMAIL → claim `role` →
  `@sismocali.gov.co` → `password` provider → `google.com` + `@cali.gov.co`).
- **`auth/deps.py`** — FastAPI dependencies preserving the per-route matrix EXACTLY:

| Route | Dependency |
|---|---|
| `/api/reportados` | none (public) |
| `/api/sticker-status` | `require_auth` (any authenticated) |
| `/api/inspector-asignaciones` | `require_auth` + handler scopes queries to `token.sub` |
| `/api/sign` | `require_auth` (replaces the signer's `accounts:lookup` — acceptance unchanged) |
| `/api/refresh`, `/api/stickers`, `/api/sticker-asignaciones`, `/api/source-status` | `require_role("admin")` |
| `/api/usuarios` | `require_role("admin")` + its extra gate ported byte-for-byte from `usuarios.js` |

**Parity suite** (`tests/auth/test_roles_parity.py`): table-driven port of the exact JS test
matrix — every (email, claimRole, provider) case from the Node tests, same expected roles. This
suite is written and green in slice 1, before any admin route moves.

- *Rejected:* `firebase-admin` `verify_id_token`. Adds a heavyweight dependency for verification
  and changes error semantics; the JS port is ~100 lines and byte-comparable in behavior.
- *Rejected:* middleware-level auth. A blanket middleware cannot express the per-route matrix
  (public vs any-auth vs admin vs admin+extra) without route-name switching; `Depends` puts each
  route's requirement AT the route, greppable and testable.

## ADR-4 — Credentials module: named clients, env-var matrix

**Decision.** `credentials/clients.py` is the ONLY module that reads SA env vars or constructs
Firestore/S3 clients. **Exactly ONE named SA client** (sismo) — the Sheets/geocode "pmegeocode"
SA is NOT ported (Scope Exclusion Addendum: no `sheets()` client, no `SHEETS_SERVICE_ACCOUNT_JSON`
env var) and the dagma SA is NOT ported either (Extension 2: no `dagma()` client, no
`GOOGLE_SERVICE_ACCOUNT_JSON`, and no dagma credential, project id, collection name, or API
constant anywhere in `backend/`). Named, memoized accessors; **reuse existing Railway env var
names** so parallel run needs zero re-provisioning:

| Client | Env var | Used by | Load rule |
|---|---|---|---|
| `sismo()` — Firestore + Auth admin, `sismo-agosto-sgred` | `FIREBASE_SERVICE_ACCOUNT_JSON` (existing, cruce-sticker) | stickers, sticker-status, sticker-asignaciones, inspector-asignaciones, usuarios routers; cruce_sticker job | **fail-fast at web startup** |
| `s3()` — presigner | `SIGNER_AWS_ACCESS_KEY_ID/SECRET`, `SIGNER_S3_BUCKET/REGION` (existing) | sign router | fail-fast at web startup |
| plain secrets | `BLOB_READ_WRITE_TOKEN`, `INSPECTIONS_URL`, `VISITADOS_API_PASS`, `GOOGLE_MAPS_API_KEY`, `RAILWAY_API_TOKEN` | per table in ADR-6 / refresh router | fail-fast only if a mounted route needs it |

**Declaration mechanism:** each router/job module declares `REQUIRED_CLIENTS: tuple[str, ...]` at
module top. `create_app()` unions the declarations of mounted routers and validates presence +
JSON-parse of those env vars at startup (crash early, matching Railway restart policy); each job
`main()` calls `credentials.require(*REQUIRED_CLIENTS)` as its first statement. Accessing a client
not declared by the calling module is a programming error surfaced by the invariant test (ADR-9
pattern), not a runtime ACL.

**Geocoding is unaffected by the Sheets exclusion (verified).** `refresh_data.py`'s geocode step
uses the plain `GOOGLE_MAPS_API_KEY` env var (`scripts/geocode_validate.py:22`
`API_KEY_ENV = "GOOGLE_MAPS_API_KEY"`, read in `refresh_data.py:713`) — it never touches the
Sheets SA. The legacy SA-based Drive/Sheets xlsx download fallback in `refresh_data.py`
(`acquire_xlsx` → `download_service_account`) becomes dead code in the migrated job: it is not
wired to any env var and its failure already falls through to the public/local strategies.

- *Rejected:* renaming to a clean `SA_SISMO_JSON`-style scheme. Double-provisioning during the
  parallel run for cosmetic gain; rename later if ever, in one PR, after slice 9.
- *Rejected:* all-lazy loading. Repeats the photo-signer failure class (misconfig discovered only
  at first request, silently 401ing in production).

## ADR-5 — `reportados` redesign: snapshot + unified day-walk

**Decision.**

- **Single implementation**: `services/atencionsismo.py`, extracted from
  `scripts/fetch_reportes_api.py` (already Python — the JS twin in `api/reportados.js` is retired,
  not ported). Exposes the day-walk with split-on-413/500/502/503/504 retry down to 1-minute
  windows, concurrency 4. Consumed by BOTH the web snapshot refresher and the `dashboard_refresh`
  job (`fetch_reportes_api.py` becomes a thin caller in its slice).
- **Snapshot loop** (`services/snapshot.py`): asyncio task started in FastAPI `lifespan` (web
  service is always-on per proposal answer 8). Loop: refresh → store `{payload, fetched_at}` in
  process memory → sleep **900 s** (parity with the old CDN `s-maxage=900`).
- **Cold start**: on startup, best-effort seed from the Blob snapshot the cron already publishes
  (`reportes*.json`, reachable like `INSPECTIONS_URL`) → serve immediately with its age; if Blob
  seed fails and no live refresh has completed yet → **503 + `Retry-After: 60`** (never inline
  ~150 s fetch in a request; `web/js/data.js` is fire-and-forget with no fallback, so a brief 503
  degrades to today's "card hidden" behavior).
- **Staleness bound**: responses carry `X-Snapshot-Age` and
  `Cache-Control: s-maxage=900, stale-while-revalidate=86400` (headers kept for optional future
  CDN). Snapshots older than 86400 s are treated as absent (503) — same outer bound the CDN had.
- **Parity-diff plan**: before the repoint slice merges, run a documented check — fetch live
  `https://sismo-cali-dashboard.vercel.app/api/reportados` and the Railway route within the same
  15-min window; compare JSON shape and the consumed fields (`reportados` total, `inmuebles`)
  with a small tolerance for in-flight-window drift; record both payloads in the PR description.

- *Rejected:* background-job-plus-polling API redesign. Changes the frontend contract;
  snapshot-serving keeps the GET contract identical at <2 s.
- *Rejected:* refreshing inside the cron only (serve Blob always). Doubles staleness (cron is
  */15 with its own pipeline latency) and couples the public route to Blob availability; in-process
  refresh is the reason the web service is always-on.

## ADR-6 — Cron services on the shared image

**Decision.**

- **Entrypoints**: `python -m app.jobs.<job>` as each service's `startCommand`. Each job module
  preserves the existing `job_*.py` skeleton: `runlog.resolve_log_dir()` → `start_tee` →
  run → `append_run({estado, duracion_seg, ...})` — `runlog.py` copied with provenance (ADR-2),
  volume-backed `runs.jsonl` behavior unchanged.
- **TWO migrated cron services**: `cruce-sticker`, `dashboard-refresh`.
  **`normalizador`, `integracion-f3`, `asignaciones`, and `cruce-gestion` do NOT migrate**
  (Sheets-only I/O or dagma-only writes — addendum extensions 1 and 2); they keep running as-is
  on the legacy integracion_F1 image/services until slice 9, where each is decommissioned pending
  explicit operator confirmation (ADR-2).
- **Schedule source of truth**: **replace** `integracion_F1/scripts/railway_setup.py` with
  `backend/scripts/railway_services.py` in this repo — a port of the same drift-only pattern
  (LIST/INSTANCE/UPDATE GraphQL, `desired()` diff, dry-run). Its `SERVICES` table owns the 3
  consolidated services (web + 2 crons) with the schedules currently in `railway_setup.py` (e.g.
  cruce-sticker `7,22,37,52 13-23,0 * * *`). Rationale: the source of truth must live in the
  surviving repo; extending the old script keeps the retired repo as control plane. During slice
  7, each migrated job's row moves from the old table to the new; the old script's row is deleted
  in the same PR. The rows for `normalizador`, `integracion-f3`, `asignaciones`, and
  `cruce-gestion` stay in the OLD script untouched until slice 9.
- **Git-connected crons**: every cron service attaches to this GitHub repo with the same pinned
  dockerfilePath (ADR-1) — schedules/commands via `railway_services.py`, code via git push. No
  service is ever `railway up`'d.
- **Env wiring per job** (set on each Railway service, names per ADR-4):

| Job | Env vars |
|---|---|
| cruce-sticker | `FIREBASE_SERVICE_ACCOUNT_JSON`, `INSPECTIONS_URL` |
| dashboard-refresh | `BLOB_READ_WRITE_TOKEN`, `GOOGLE_MAPS_API_KEY` (opt), `VISITADOS_API_PASS`, `FIREBASE_SERVICE_ACCOUNT_JSON` (survey_cali ingest, ADR-11 — added in slice 7b) |

  `dashboard-refresh` loses its clone-at-start `entrypoint.sh`/`DASHBOARD_REPO_TOKEN` hack — the
  code is IN the image now; `deploy/refresh.sh`'s timeout/meta-guard/trap structure is preserved,
  invoked by `app.jobs.dashboard_refresh`.

- *Rejected:* extending `railway_setup.py` in place. See rationale above.
- *Rejected:* APScheduler in-process. Already rejected by binding proposal answer 2.

## ADR-7 — CORS & cutover configuration

**Decision.**

- **CORS**: `CORSMiddleware`, explicit allowlist in `config.py`:
  `https://sismo-cali-dashboard.vercel.app`, `https://formulario-atc20-cali.vercel.app`, plus
  `allow_origin_regex` for `http://localhost:\d+` / `http://127.0.0.1:\d+` dev.
  `allow_credentials=False` (Bearer, no cookies), methods `GET, POST, OPTIONS`, headers
  `Authorization, Content-Type`.
- **Consumer repoint mechanism** — the key finding: `web/js` calls RELATIVE paths
  (`/api/reportados`, `/api/sticker-status`, `/api/refresh`, ...), so there is no base constant to
  flip today. Introduce `web/js/api-config.js` exporting a per-endpoint URL map (default = current
  relative path). Each cutover slice flips exactly one entry to the Railway base URL; rollback is a
  one-line revert. `formulario/js/form.js` already has `DASHBOARD_API` and `FOTO_SIGNER_URL` —
  those slices flip the constant values only.
- **Parity procedure per endpoint (before each repoint)**: (1) route live on Railway, no consumer
  change; (2) same-token side-by-side calls old vs new — status code, CORS headers, JSON shape,
  and consumed-field values match (admin POST endpoints: exercise read-only actions live; mutating
  actions verified by pytest against the same handler + a manual smoke in production after
  repoint); (3) record the diff in the slice PR; (4) flip the one config entry; (5) old Vercel
  function stays deployed until slice 9.
- **Rollback**: revert the config entry (consumer redeploy on Vercel, seconds). Never requires
  redeploying deleted code before slice 9, per proposal.

- *Rejected:* single global `API_BASE` flip. All-at-once cutover contradicts the per-consumer,
  per-endpoint slice plan and makes rollback all-or-nothing.

## ADR-8 — Testing strategy (strict TDD)

**Decision.** pytest + `fastapi.testclient.TestClient`, rooted at `backend/tests/`:

```
tests/
├── auth/        test_roles_parity.py (JS matrix port) · test_verify.py (fake keypair/cert fetcher)
├── routers/     one file per router; app via create_app() with dependency_overrides
│                (fake verifier claims) + faked credentials clients — no SA JSON in CI
├── services/    test_atencionsismo.py (day-walk/split-retry vs httpx MockTransport fixtures)
│                test_snapshot.py (cold start 503, Blob seed, staleness bound)
│                test_survey_cali.py (apply_mutation diffs/revisions, revert reconstruction,
│                                     ingest idempotency: same input twice → zero new revisions,
│                                     per-field conflict rule: manual edit survives static upstream)
├── jobs/        offline --check-style fixtures per job (idempotency/watermark semantics)
└── invariants/  test_sole_writer.py (ADR-9)
```

- **Per-slice TDD flow**: each apply slice starts with failing tests for its route/job (parity
  table, handler contract), then implements. Slice 1's red suite is the `roleFrom` parity matrix +
  verifier tests.
- **`create_app(settings)` factory** exists precisely so tests inject fakes — no network, no real
  credentials in CI.
- **Legacy Node `assert` tests** (`api/*.test.js`): untouched and authoritative for the Vercel
  side until each endpoint's deletion in slice 9 (binding proposal answer 6); each also serves as
  the source table for its Python parity tests.

- *Rejected:* porting the Node self-check idiom to Python. pytest is already the integracion_F1
  convention and the proposal binds it.

## ADR-9 — Sole-writer invariant for `sticker_matches` / `cuadrillas`

**Decision.** These collections have NO Firestore rules; "backend is sole writer" must survive by
construction:

1. **Module boundary**: exactly three modules may contain the collection-name literals
   `sticker_matches` / `cuadrillas`: `routers/sticker_asignaciones.py` (admin fields),
   `routers/inspector_asignaciones.py` (own-uid `hecho` update), `app/jobs/cruce_sticker.py` +
   its copied pipeline module (pipeline fields, `merge=True` subset only). All access goes through
   `credentials.sismo()`; no other module names the collections.
2. **Static invariant test** (`tests/invariants/test_sole_writer.py`): scans `backend/app/**/*.py`
   and asserts the literals appear ONLY in the allowlisted modules. A new write path cannot merge
   without editing the allowlist — which is the review tripwire.
3. **Review checklist item** (carried into tasks for every slice touching `routers/` or `jobs/`):
   "no new module references sticker_matches/cuadrillas; invariant test allowlist unchanged".
4. **Verify-phase check**: run the invariant test + diff the allowlist against this ADR.

**`survey_cali` gets the same treatment** (ADR-10..12): its declared writer set is exactly
`services/survey_cali.py` (the ONLY module that touches Firestore for this collection),
`routers/survey_cali.py` and `app/jobs/dashboard_refresh.py` (both of which mutate ONLY through
the service's `apply_mutation`). The literal `survey_cali` is allowlisted for those three modules
in the same invariant test; same review checklist and verify-phase check apply.

## ADR-10 — `survey_cali` document + history model

**Decision.** Current doc + append-only history subcollection:

```
survey_cali/{GlobalID} {
  // effective record fields (Survey123-sourced + manual edits) — what the dashboard reads
  <record fields...>
  _deleted:      boolean          // soft delete (CRUD delete); default absent/false
  // metadata — written only via services/survey_cali.py
  _rev:          int              // latest revision number (monotonic, starts at 1)
  _updated_at:   Timestamp
  _updated_by:   'pipeline' | uid
  _source:       { field: last_ingested_upstream_value }   // ingest-only shadow (ADR-11)
  _source_hash:  string           // canonical content hash of the last-ingested upstream record
}
survey_cali/{GlobalID}/history/{rev_000042} {
  rev: int, author: 'pipeline' | uid, at: Timestamp,
  kind: 'create' | 'ingest' | 'edit' | 'revert' | 'delete',
  changes: { field: { before, after } },       // create = full doc with before:null
  revert_of: int | null                        // target rev when kind='revert'
}
```

- **Revision id** = zero-padded integer (`rev_000042`) mirrored in `_rev` — lexical order equals
  revision order, no composite index needed to list history in order.
- **Revert mechanics**: value of field at rev N is derivable (the `after` of the last revision
  ≤ N touching it; `create` seeds every field). Revert to rev N = compute that state, diff against
  current, write it as a NEW revision (`kind:'revert'`, `revert_of:N`). Never touches `_source` —
  the next ingest may legitimately re-overwrite if upstream has moved (visible again in history).
- **Delete is soft**: `_deleted:true` + a `delete` revision. "Nothing is ever destroyed" makes
  hard deletes incompatible with append-only history; dashboard reads filter `_deleted`.
- **Why the default read never touches history**: Firestore subcollections are NOT returned with
  parent documents — a collection query on `survey_cali` structurally cannot read `history/*`.
  The hot path is one query over current docs; history costs reads only on the explicit
  history/revert endpoints.

- *Rejected:* top-level `survey_cali_history` collection. Works, but loses path locality, needs a
  composite (record_id, rev) index, and the exclusion from the hot path becomes a query
  discipline instead of a structural guarantee.
- *Rejected:* full-snapshot revisions. Changed-field maps are smaller, make "what changed" a
  direct render (no client-side diffing), and revert state is still fully reconstructible because
  `create` records the full initial document.

## ADR-11 — `survey_cali` incremental ingestion (slice 7b)

**Decision.** Ingestion runs inside `app.jobs.dashboard_refresh` (existing 15-min cadence),
immediately after the Survey123 fetch, reusing the rows `refresh_data` already parsed — no second
upstream call. All writes go through `services/survey_cali.apply_mutation` (ADR-12), so ingest
updates produce history revisions identically to manual edits.

- **Change detection: content hash primary, `EditDate` as pre-filter only.** Per record: build a
  canonical normalized form (sorted keys, normalized types/whitespace) → SHA-256 → compare to the
  doc's `_source_hash`. Equal → skip record entirely (no write, no revision). `EditDate` is used
  only to short-circuit hashing when present AND ≤ the stored max (it is unreliable at this
  source, so it may only SKIP work, never be the sole trigger to write).
- **Per-field ingest-vs-manual-edit rule (adopting the addendum's recommended default)**: when the
  record hash differs, diff each upstream field against `_source[field]` (NOT against the
  effective field). Write a field iff the UPSTREAM value changed since the previous ingest; on
  write, update both the effective field and `_source[field]`. Consequences: a manual edit
  survives every pipeline run while upstream is static; a real upstream change wins over a manual
  edit, and the overwrite appears as a `kind:'ingest'` revision (`before` = the manual value) —
  visible and revertible. This is `sticker_matches`' split-ownership precedent refined to
  per-field granularity.
- **State**: per-record idempotency lives ON the doc (`_source_hash`, `_source`) — correctness
  never depends on external state. `_meta/survey_cali_ingest_state` (mirrors
  `_meta/cruce_sticker_state`) stores run watermark, max `EditDate` seen, and counts for
  observability/short-circuiting only.
- **Idempotency**: re-running against unchanged upstream produces ZERO writes and ZERO revisions
  by construction (hash equality). Current-doc reads batched via `get_all` in chunks; each
  record's current-doc update + history append committed together (ADR-12 transaction helper);
  outer batching ≤500 ops per `subir_cruce_firebase.py` house style.
- **First run / new records**: missing doc → `kind:'create'` revision with the full record,
  `author:'pipeline'`.

- *Rejected:* `EditDate` watermark as the trigger (pure `cruce_sticker` style). The addendum and
  source behavior say `EditDate` is sometimes unreliable — a stale `EditDate` with changed content
  would silently drop updates. Hash catches everything; `EditDate` only saves CPU.
- *Rejected:* full-collection rewrite per run. Explicitly banned by the addendum; would also spam
  history with no-op revisions and destroy manual edits.

## ADR-12 — `survey_cali` CRUD + history endpoints (slice 8b)

**Decision.** New capability with no legacy parity constraint → idiomatic REST (unlike the ported
POST-`{action}` routers, which keep their shape for parity). `routers/survey_cali.py`, ALL routes
`Depends(require_role("admin"))` from ADR-3's chain:

| Route | Semantics |
|---|---|
| `GET /api/survey-cali` | list current docs (filters/pagination; excludes `_deleted`) — dashboard read model |
| `GET /api/survey-cali/{id}` | current doc |
| `POST /api/survey-cali` | create (fails if id exists) → `create` revision |
| `PATCH /api/survey-cali/{id}` | partial merge — ONLY provided fields; no full-doc replace exists |
| `DELETE /api/survey-cali/{id}` | soft delete (`_deleted:true`) → `delete` revision |
| `GET /api/survey-cali/{id}/history` | revision list, newest first (renders "what changed" from `changes`) |
| `POST /api/survey-cali/{id}/revert` | body `{rev}` → new `revert` revision per ADR-10 |

- **Validation boundary**: Pydantic model with optional record fields; underscore-prefixed
  metadata (`_rev`, `_source`, `_source_hash`, `_deleted`, ...) rejected at the schema — clients
  can never write metadata. No-op PATCH (values equal current) returns 200 without creating a
  revision.
- **Single mutation core**: `services/survey_cali.apply_mutation(id, changes, author, kind,
  revert_of=None)` is the ONLY write path, shared by CRUD, revert, and ingest (ADR-11) — one code
  path guarantees uniform history and one place to test.
- **Concurrency**: `apply_mutation` runs a Firestore transaction per record: read current doc,
  compute diff, write effective fields + `_rev+1` + the history doc atomically. Concurrent edits
  cannot mint duplicate revision numbers or lose history.

- *Rejected:* POST-`{action}` shape for consistency with ported routers. Those carry legacy
  parity obligations; a new API should not inherit their constraint — and the future history UI
  benefits from cacheable GETs.
- *Rejected:* letting the ingest job write Firestore directly (bypassing the service). Two write
  paths = two revision formats eventually; the sole-writer invariant (ADR-9) exists precisely to
  prevent this.

- *Rejected:* adding Firestore rules now. Pre-existing debt explicitly out of scope
  (no rules rewrite, per proposal non-goals); the invariant must hold without it.

## Migration / rollout

Slices 1–9 per `proposal.md`, plus addendum slices 7b/8b. Design hooks per slice: slice 1 =
ADR-1/3/4 + parity suite green; slice 2 = sign router (ADR-3 unified verifier); slice 3 = ADR-5 +
parity diff; slice 6 = refresh router repoints its Railway GraphQL target to the new
`dashboard-refresh` service ID ONLY — the legacy endpoint's fail-soft `cruce-gestion` redeploy is
NOT ported (Extension 2); slice 7 = ADR-2 copy (1 integracion_F1 job, Sheets branches cut) +
ADR-6 per-job rows for the 2 migrated crons; slice 7b = ADR-10/11 (ingestion into
`dashboard_refresh` + `services/survey_cali.py` mutation core, ships with slice 7's cron work);
slice 8b = ADR-12 (CRUD/history/revert router, with slice 8's admin CRUD); slice 9 =
decommission + delete old `railway_setup.py` rows, Node tests, the two retired Vercel projects,
AND the four non-migrated legacy cron services (`normalizador`, `integracion-f3`, `asignaciones`,
`cruce-gestion`) — each only with explicit operator confirmation. Dashboard UI for history/revert
is a separate future change — this change ships API + read model only.

## Open questions carried to tasks

1. Exact Blob URL/env var for the `reportados` cold-start seed (ADR-5) — confirm the published
   `reportes*.json` filename `deploy/refresh.sh` uses at task time.
2. `usuarios.js` extra admin gate — port byte-for-byte at task time (read the JS, encode in its
   router test first).
3. Confirm the atencionsismo Basic-auth username constant currently used by both implementations
   when extracting `services/atencionsismo.py`.
4. `survey_cali` canonical-form field set for hashing (ADR-11): confirm at task time which
   Survey123 columns are ingested (raw layer fields vs the derived/geocoded fields
   `refresh_data.py` adds — recommendation: hash RAW upstream fields only, so pipeline-derived
   enrichment never masks or fakes an upstream change).
5. History list pagination default (ADR-12) — pick a page size when the router test is written.

(Former open question 6 — Sheets-cut impact on `integracion-f3`/`asignaciones` — is RESOLVED by
user decision: both jobs are excluded from the migration; no functional replacement needed. See
ADR-2/ADR-6 and the proposal's Scope Exclusion Addendum extension.)
