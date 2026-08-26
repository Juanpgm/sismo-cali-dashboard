# Exploration: fastapi-backend-consolidation

## Current State Inventory

### 1. Vercel serverless functions — `api/*.js` (dashboard project, `sismo-cali-dashboard.vercel.app`)

All auth flows through one shared module, `api/refresh.js`, which every other function `require()`s:
- `verifyFirebaseToken(idToken, projectId)` — zero-dependency RS256 verification of a Firebase ID token against Google's rotating x509 certs (no `firebase-admin` needed just to verify).
- `roleFrom({email, claimRole, provider})` / `roleFromClaims(claims)` — single source of truth for the effective role (`admin` / `usuario` / `viewer` / `inspector` / `otro`), precedence: hardcoded `SUPERADMIN_EMAIL` → custom claim `role` → `@sismocali.gov.co` domain → `password` provider → `google.com` + `@cali.gov.co`.

| File | Method | Auth | Firestore / external | Cache | Called by |
|---|---|---|---|---|---|
| `api/refresh.js` | POST | Bearer token, role must be `admin` | none directly; calls Railway GraphQL API (`serviceInstanceRedeploy` on `dashboard-refresh` + `cruce-gestion`) | none (202 mutation) | `web/js/main.js` (`REFRESH_ENDPOINT`) |
| `api/reportados.js` | GET | **none** — no Firebase check at all | reads external atencionsismo API live (day-walk + 413/500/502/503/504 split-and-retry down to 1-minute windows, `CONCURRENCY=4`) | `s-maxage=900, stale-while-revalidate=86400` (CDN) | `web/js/data.js` |
| `api/stickers.js` | POST | Bearer + `admin` | `firestore().collection('inspectores')`, `evaluaciones`; `auth().createUser/listUsers/updateUser/deleteUser` | none | `web/js/stickers.js`, `web/js/evaluaciones.js` |
| `api/sticker-status.js` | GET | Bearer, any authenticated role | `firestore().collection('sticker_matches')` | 5-min **module-level** cache (warm-lambda only) | `web/js/main.js`, `web/js/data.js`, `web/js/coverage-gauge.js` |
| `api/sticker-asignaciones.js` | POST | Bearer + `admin` | `sticker_matches` + `cuadrillas` (CRUD, greedy nearest-neighbor auto-cluster, haversine) | none | `web/js/stickers-asignacion.js` |
| `api/inspector-asignaciones.js` | POST | Bearer, **any** authenticated user, scoped to `inspector_uid == token.sub` | `sticker_matches` (read own, mark `hecho`) | none | **cross-origin**: `formulario/js/form.js` |
| `api/usuarios.js` | POST | Bearer + `admin` (password provider, not `@sismocali`) | `auth()` full CRUD across all Firebase Auth users; `inspectores/{uid}` sync | none | `web/js/usuarios.js` |
| `api/source-status.js` | GET | Bearer + `admin` | none Firestore; reuses `reportados.js`'s `probeApi` | `no-store` | `web/js/analista.js` |

Convention: every admin-gated function **duplicates** its own `firebase-admin` singleton (`getAdmin()`) rather than importing a shared one — deliberate, documented as "each serverless function stays self-contained" (`api/usuarios.js` header, `design.md` ADR-1/ADR-3 in `stickers-asignacion`). Testing convention: plain Node `assert`-based self-check files (`api/*.test.js`, run via `node api/x.test.js`), no Jest/Mocha.

### 2. Additional Vercel projects in scope for "all backend"

- **`services/photo-signer/api/sign.js`** — deployed as its **own** Vercel project (`sismo-fotos-signer.vercel.app`), **not connected to git** (`npx vercel deploy --prod` manually from that directory). Presigns S3 `PutObject` uploads for field-form photos. Validates the Firebase ID token via the REST `accounts:lookup` endpoint (not the RS256 verifier `refresh.js` uses — a second, independent verification implementation). Needs `SIGNER_S3_BUCKET/REGION`, `SIGNER_FIREBASE_API_KEY`, `SIGNER_AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY`. Its README documents a real incident: when the project migrated Firebase projects (`dagma-85aad` → `sismo-agosto-sgred`), this signer kept validating against the old project and every photo upload silently 401'd, because the code/config lived only inside the Vercel deployment, invisible to the repo.
- **`formulario/`** — its own Vercel static project (`formulario-atc20-cali.vercel.app`), client Firebase Auth + client Firestore SDK, calls two cross-origin backends: `DASHBOARD_API/api/inspector-asignaciones` and `FOTO_SIGNER_URL`.

So today there are **3 separate Vercel deployments** (dashboard `web/`+`api/`, `formulario/`, `services/photo-signer/`) plus the Railway fleet below — "consolidate all backend into one FastAPI app" means folding in `api/*.js` **and** `services/photo-signer/api/sign.js`; `web/` and `formulario/` stay static.

### 3. Railway cron jobs — two Docker images, one Railway project (`normalizador-sismo-cali`), **not git-connected** (deployed via `railway up --path-as-root .` CLI upload)

From `integracion_F1/scripts/railway_setup.py` `SERVICES` (source of truth over any docstring elsewhere):

| Service | Image | Command | Schedule (UTC) | Purpose | Credentials |
|---|---|---|---|---|---|
| `normalizador` | `integracion_F1/Dockerfile` | `python job.py` | `5 13-23,0 * * *` | `integracion.pipeline.run()` → pushes `tabla_integrada`/`integracion_stats` to the EDAN Google Sheet | `service_account.json` (Sheets write scope) |
| `integracion-f3` | same | `python job_integrar_f3.py` | `*/15 13-23,0 * * *` | `integrar_f3.main()` — cruce F3↔integrada | same |
| `asignaciones` | same | `python job_asignaciones.py` | `*/15 13-23,0 * * *` (module docstring "daily 16:00 Bogota" is stale) | `asignar_f3.main()` — top-100 priorizado | same |
| `cruce-gestion` | same | `python job_cruce.py` | `10,25,40,55 13-23,0 * * *` | `cruce_criticos_survey.main(--firebase)` → writes Firestore **`dagma-85aad`** `cruce_criticos_survey` | `GOOGLE_SERVICE_ACCOUNT_JSON` (dagma SA) + `INSPECTIONS_URL` (Blob) |
| `cruce-sticker` | same | `python job_sticker.py` | `7,22,37,52 13-23,0 * * *` | `cruce_sticker.main()` — matches Panel points ↔ `evaluaciones`, writes **`sismo-agosto-sgred`** `sticker_matches` incrementally (watermark `_meta/cruce_sticker_state`) | `FIREBASE_SERVICE_ACCOUNT_JSON` (sismo SA) + `INSPECTIONS_URL` |
| `dashboard-refresh` | **separate** `deploy/Dockerfile` (clones the dashboard repo at container start via `entrypoint.sh` + `DASHBOARD_REPO_TOKEN`) | `deploy/refresh.sh` (baked CMD) | `*/15 13-23,0 * * *` | `scripts/refresh_data.py` (Survey123 + geocode) + `scripts/fetch_reportes_api.py` (atencionsismo) → publish JSON to **Vercel Blob** (no git commit) | `BLOB_READ_WRITE_TOKEN`, optional `GOOGLE_MAPS_API_KEY`, `VISITADOS_API_PASS` |

`api/refresh.js`'s manual "Actualizar datos" button redeploys `dashboard-refresh` **and** `cruce-gestion` (fail-soft on the second) via the Railway GraphQL API — the one place the Vercel and Railway sides are directly coupled today.

### 4. `dashboard-refresh` pipeline detail (`deploy/refresh.sh`)

Every network step wrapped in `timeout`; seeds the geocode cache from Blob, runs `refresh_data.py` (300s timeout) then `fetch_reportes_api.py` (240s, non-fatal on failure), a `meta_guard` step that aborts publishing on `row_count<=0`, then publishes `meta.json`, `inspections.json`, `inspections.xlsx`, `reportes*.json`, `geocode_cache.json` to the `sismo-dashboard-data` Blob store — **no git commit, no Vercel deploy spent**. A best-effort `data/_status.json` is always written on exit via a `trap`.

### 5. `formulario/` backend surface

- `formulario/js/auth.js`: client Firebase Auth (email/password with synthetic `{cedula}@sismocali.gov.co`), client Firestore read of `inspectores/{uid}` with retry/backoff, gates the app on `activo !== false`.
- `formulario/js/form.js`: `DASHBOARD_API` = `http://localhost:3000` (dev) or `https://sismo-cali-dashboard.vercel.app` (prod) → `POST /api/inspector-asignaciones` with the inspector's ID token (CORS handled server-side by that one endpoint reflecting `Origin`). `FOTO_SIGNER_URL` hardcoded to `https://sismo-fotos-signer.vercel.app/api/sign`.
- Evaluations themselves (`evaluaciones` collection) are created **client-side** via `runTransaction` directly against Firestore rules — not through any `api/` endpoint.

### 6. Firebase/Firestore surface — **two independent Firebase/GCP projects**

**`sismo-agosto-sgred`** (dashboard's own project — `FIREBASE_PROJECT_ID` default in every `api/*.js`):
- `inspectores/{uid}` — client read/limited-update (own doc, rules-enforced) + admin CRUD (`api/stickers.js`, `api/usuarios.js`).
- `evaluaciones/{id}` — client create-only by the owning inspector (rules-enforced), admin-SDK read by `api/stickers.js`.
- `sticker_matches/{fuente}_{registro_id}` — pipeline-owned fields written by `cruce_sticker.py` (Railway), admin-owned fields written by `api/sticker-asignaciones.js` and `api/inspector-asignaciones.js`. **No Firestore rules exist for this collection** — security is "only the admin-SDK writers can reach it," by convention, not rules.
- `cuadrillas/{id}` — same: admin-API-only, no rules.
- `_meta/cruce_sticker_state` — pipeline watermark.

**`dagma-85aad`** (a different/legacy team's project — `FIRESTORE_PROJECT` default in `integracion_F1/integracion/config.py`):
- `cruce_criticos_survey/{doc}` — public read; update restricted to a **different custom claim, `pmu`**, and only to a whitelisted `gestion_*` field subset; create/delete admin-SDK/pipeline only.
- `_meta`, `despachos`, `lideres`, `inspecciones_israel`, `_meta_israel` — public read; writes either `pmu`-gated or admin-SDK-only.
- All read **directly by the browser** via the client Firestore SDK (e.g. `web/js/israel-source.js`) — no proxy, no CDN.

**Discovery worth flagging**: exactly **one** `firestore.rules` file exists (`integracion_F1/firestore.rules`), whose header says "Reglas Firestore de dagma-85aad" yet also contains `inspectores`/`evaluaciones` rules that belong to `sismo-agosto-sgred`. No `firebase.json` anywhere — no CLI-deployable single source of truth; rules changes are applied by hand per console. Pre-existing debt a consolidation touching auth boundaries must not inherit or worsen.

### 7. External data sources

| Source | Consumed by | Auth | Notes |
|---|---|---|---|
| atencionsismo API (`/api/informe/json`) | `api/reportados.js` (live GET, CDN 15-min) **and** `scripts/fetch_reportes_api.py` (Railway cron, static Blob snapshot) | HTTP Basic, `personal.api=="read"` | **Two independent implementations** (JS and Python) of the identical day-walk/split-retry algorithm — real duplication. |
| Survey123 public feature layer (ArcGIS) | `scripts/refresh_data.py` | none (public) | EXIF GPS + base inspection rows. |
| Google Sheets (EDAN doc + `VISITAS_SPREADSHEET_ID`) | `integracion_F1/integracion/export_sheets.py`, `scripts/refresh_data.py` | Google SA, `spreadsheets` scope | Pinned `sheetId`s guard renamed/recreated tabs. |
| Vercel Blob (`sismo-dashboard-data`) | `deploy/refresh.sh` (write) / `deploy/blob_sync.py`; read back by `refresh.sh`, `cruce-gestion`/`cruce-sticker` (`INSPECTIONS_URL`), the static frontend | `BLOB_READ_WRITE_TOKEN` | Deliberately replaces "git commit → Vercel redeploy". |
| Google Maps Geocoding | `scripts/refresh_data.py` / `geocode_validate.py` | `GOOGLE_MAPS_API_KEY` | Fill-only-where-empty, Blob-persisted cache. |
| AWS S3 | `services/photo-signer` | AWS IAM keys | Field-photo storage, presigned `PUT`. |

### 8. `integracion_F1/` repo boundary

No `.git` directory was found under `integracion_F1/` by the exploring agent's scan, so from the code alone it reads as one monorepo with an isolated deploy unit. (Orchestrator note: `git rev-parse` from inside `integracion_F1/` resolves to its own toplevel with remote `Juanpgm/normalizador_data_sismo_cali` — it IS a separate repo; treat the hard repo boundary as real.)

## Data & Auth Flows

- **3 identity systems, not 1**: (a) `sismo-agosto-sgred` Firebase Auth + `role` claim (the `roleFrom` precedence chain), (b) `dagma-85aad` Firebase Auth + `pmu` claim (Gestión team), (c) photo-signer's independent verification against `identitytoolkit.googleapis.com`. A single FastAPI app must hold verification for at least (a) and (b), and decide whether to unify (c).
- **Auth model split by caller**: `reportados` public; `sticker-status` and `inspector-asignaciones` any-authenticated (role-wide vs own-uid scoping); everything else fail-closed `admin`. The per-route matrix must be preserved exactly.
- **CORS today is the exception**: only `inspector-asignaciones` and photo-signer set CORS. Moving everything to Railway while frontends stay on Vercel makes **every** route cross-origin — CORS must become universal.
- **Firestore access split three ways**: (1) admin-SDK via `api/*.js` (endpoint IS the security boundary), (2) rules-enforced client-SDK from the browser, (3) admin-SDK from Railway crons. Consolidation naturally absorbs (1) and (3); (2) is a genuine scope question.

## Pain Points (concrete, cited)

1. **cruce-sticker Railway deploy failure saga** (`railway_setup.py` docstring): `railway up` without `--path-as-root .` uploaded the dashboard repo root; Railpack detected Node; every run crashed `python: command not found` (2026-08-25). Evidence the multi-subproject, non-git-connected deploy model is fragile. One deployable removes the failure mode — but only if the migration also drops CLI-upload deploys for something reproducible.
2. **Vercel 100-deploys/day cap** (`deploy/refresh.sh` header) motivated Blob publishing. Stays relevant as long as `web/` stays Vercel-static.
3. **`api/reportados.js` near the 300s Vercel limit** (~150s today). Railway has no hard per-request ceiling — but the user still waits ~150s unless redesigned (background job + polling).
4. **Split credentials across 2 Firebase projects** (`sismo-agosto-sgred` vs `dagma-85aad`) + a third Sheets/geocode SA ("pmegeocode"). A unified app must hold and select between ≥3 SAs per route/job.
5. **`sticker_matches`/`cuadrillas` have no Firestore rules** — "backend is sole writer" by convention only; the invariant must survive the migration exactly.
6. **Photo signer**: manual, non-git-connected deploy that already caused a silent production incident (401s on every photo upload after the Firebase project migration).
7. **Duplicated atencionsismo day-walk algorithm** in JS (`reportados.js`) and Python (`fetch_reportes_api.py`).
8. **Stale schedule docs**: `job_asignaciones.py` docstring says daily; `railway_setup.py` SERVICES (ground truth) says every 15 min.

## Constraints

- Target: single FastAPI application covering all `api/*.js` + `services/photo-signer`, deployable on Railway.
- Migration incremental — chained PRs (auto-chain, stacked-to-main); production never breaks; each slice leaves the app working.
- Processes idempotent and incremental (house style already in `cruce_sticker.py` and `railway_setup.py`).
- Does NOT move: `web/` static hosting, CDN caching semantics the frontend relies on, public Survey123 attachment URLs.
- User decisions (2026-08-25): **everything** migrates (endpoints + crons + signer); Firestore stays as the datastore; parallel run with gradual per-consumer cutover; Railway always-on cost accepted.

## Options Sketch (for the proposal to weigh)

**a) Full consolidation** — one FastAPI app replaces every `api/*.js` + photo signer, and absorbs the 6 cron jobs as in-process scheduled tasks (APScheduler).
- Pros: one deployable, one credential path, no "wrong subproject root" failures, real long-lived in-process caches.
- Cons: single point of failure (today 7 independently-failing units); universal CORS; loses Vercel CDN `s-maxage` unless replaced; always-on billing; Python Firebase story proven viable (`google-cloud-firestore` already used).

**b) FastAPI for endpoints only; cron jobs stay separate Railway services**
- Pros: keeps fault isolation; smaller slices; `railway_setup.py` provisioning unchanged.
- Cons: doesn't deliver "manejar todo desde allí" (N+1 deployables); `--path-as-root` fragility remains for crons.

**c) Partial consolidation — only the problematic pieces first** (photo signer + `reportados`)
- Pros: lowest-risk start, targets the two concretely-evidenced pains.
- Cons: not an end-state; a starting slice within (a)/(b).

Cross-cutting: CDN loss for `reportados`/`sticker-status`; universal CORS; always-on cost; multi-credential process; "each function stands alone" convention vs FastAPI's pull toward shared modules — the proposal must say which convention wins.

## Open Questions for the Proposal

1. Do the **public browser-direct Firestore reads** (dagma public collections, `inspectores`/`evaluaciones` client access) move behind the API too, or stay rules-enforced client SDK?
2. Cron scheduling: in-process (APScheduler, 1 deploy) or Railway-native cron pointed at the same image (N deploys, 1 codebase)?
3. Credential strategy for ≥3 SAs: env-var matrix, per-request selection, or migrate `dagma-85aad` data into `sismo-agosto-sgred` first (bigger separate decision)?
4. Accept CDN loss for `reportados`/`sticker-status`, keep those two on Vercel as exceptions, or add a cache layer in front of Railway?
5. `DASHBOARD_API`/`FOTO_SIGNER_URL` hardcoded in `formulario/js/form.js` — repoint in the first slice, or keep a thin Vercel proxy so formulario needs zero changes during transition?
6. Test convention: port the Node `assert` self-checks per endpoint, or adopt pytest across the FastAPI app (integracion_F1 already has pytest-style tests)?
7. Slice order: photo-signer and `reportados` are lowest-blast-radius starts; admin-CRUD endpoints (`usuarios`, `stickers`, `sticker-asignaciones`) carry the most auth logic and should move last.
8. Scale-to-zero Railway config, or plain always-on? (User accepted the cost; sleep-on-idle still worth considering for the web app.)
