# Exploration: Stickers — cruce y asignación

Change: `stickers-asignacion` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-explore

## Feature request (user, Spanish)

"Usa el análisis en `integracion_F1/stickers_analysis.ipynb` para generar una visualización en
'stickers' y un proceso que me permita ir detectando cuáles de los puntos de Panel ya fueron
visitados para stickers (sin necesidad de cargar todos los datos de Panel), de manera recurrente.
Poder visualizar allí mismo una tabla de los que han sido asignados y los que faltan, pudiéndolos
ordenar, también una tab del mapa que permita ver completados con sticker en azul y por asignar en
rojo, y toda una interfaz CRUD y sus respectivas colecciones en Firebase para asignar a los
inspectores los puntos faltantes, pudiendo generar grupos y asignación automática por inspector,
definiendo cuadrillas por zonas con puntos cercanos en automático o manualmente, para lograr
asignar los puntos y asociarlos a una persona, con la opción de reasignar o modificar los puntos
asignados o cambiarlos a otra persona."

Decoded: turn the notebook's one-shot Panel↔Stickers cruce into a recurring, persisted process;
add a table + map view (blue = tiene sticker, red = pendiente) inside the dashboard; add Firestore
collections and CRUD to group pending points into cuadrillas (auto by proximity or manual) and
assign/reassign them to inspectors.

## 1. `integracion_F1/stickers_analysis.ipynb`

- **Goal.** For every point in the dashboard Panel (`web/data/inspections.json`, 1000 EDE records,
  + `puntos_israel_cali.json`, 101 Israel-delegation records), decide whether it already has a
  field sticker (ATC-20 evaluation, Firestore collection `evaluaciones`, project
  `sismo-agosto-sgred`) or is still faltante.
- **Coordinates.** `x`/`y` (EXIF-corrected, preferred) with `x_form/y_form` and
  `geocode_lat/lon` as cross-checks.
- **Stickers source.** Read directly from Firestore (`google-cloud-firestore`), same 3-tier
  credential resolution as `subir_cruce_firebase.py` (`STICKERS_FIREBASE_SA` path →
  `FIREBASE_SERVICE_ACCOUNT_JSON` env → ADC).
- **Barrio enrichment.** Point-in-polygon vs `basemaps/barrios_veredas.geojson` via
  `integracion.spatial_bridge.load_parcels_geojson/assign_parcels`.
- **Matching cascade (`cruce_sticker()`, cell 10) — reused, not reimplemented**, from
  `integracion_F1/cruce_gestor.py`: `nearest`, `match_by_direccion`, `build_addr_index`,
  `addr_key`, `_eval_latlon`. Cascade: haversine ≤ `MATCH_MAX_M = 40.0` → `"cercania"`; else
  address match (IGAC-normalized, exact or fuzzy ≥ 0.90) → `"direccion"`/`"combinado"`; else
  `tiene_sticker=False`. Offline self-check exists (`_selfcheck_cruce_sticker`, same `--check`
  idiom as the rest of the repo).
- **Validation/quality layer.** Tiers every match `alta`/`media`/`sospechoso` (`_tier()`) using geo
  distance + address-string similarity + a calle/carrera-transposition detector; sospechosos
  exported to CSV with a reverse-geocode "second opinion".
- **Outputs today are in-notebook only** — `df_cruce`, `df_faltantes`, PNGs, a text report. **No
  persisted JSON/Firestore write.** The notebook's own final markdown cell already names the next
  step as: assign `df_faltantes` to brigades (asignar_f3.py-style), with a persistent `estado`
  field per point, human-overridable, "living in a new Firestore collection (undecided) or a
  Sheets tab." This change makes that decision (§ design.md ADR-1) and builds it.

## 2. Existing "Stickers" tab (roster + evaluaciones)

- **Backend `api/stickers.js`** — POST-only action router (`list`, `evaluaciones`, `create`,
  `setEnabled`), admin-only (`verifyFirebaseToken` + `roleFromClaims` from `api/refresh.js`),
  `firebase-admin` singleton (`getAdmin()`).
- **Frontend** `web/js/stickers.js` (roster CRUD) + `web/js/evaluaciones.js` (KPI tiles, Leaflet
  map, side-list, detail modal) — both render inside `#view-stickers`, wired lazily on first tab
  open from `main.js` `switchView()`.
- **Map pattern to reuse** (`evaluaciones.js`): `L.map` + CARTO tiles via `basemapTileUrl()`, one
  `L.circleMarker` per record colored by category, `L.layerGroup`, bottom-right legend
  `L.control`, popup with a detail button, `map.fitBounds()`, hover/focus sync between a side list
  and a `markerById` map, `themechange` listener for tile swap. This is the direct template for
  the blue/red completed-vs-pending map.

## 3. Firestore usage (project `sismo-agosto-sgred`)

- **Collections today:** `cruce_criticos_survey`, `_meta`/`_meta_israel`, `despachos`, `lideres`,
  `inspecciones_israel`, `inspectores/{uid}` (self-read/self-update only, create/delete
  admin-SDK-only), `evaluaciones/{id}` (create-only by the owning inspector, no client update).
  Default: `allow read, write: if false`.
- **Admin-SDK pattern** (`api/stickers.js`, `api/usuarios.js`): each function carries its own
  `getAdmin()` singleton and its own copy of the Bearer-token auth preamble — deliberately
  duplicated per file (see `api/usuarios.js` comment), not shared, to keep each serverless
  function self-contained.
- **Closest full CRUD precedent:** `openspec/changes/archive/2026-08-24-usuarios-tab/design.md` —
  endpoint shape, role/anti-lockout guards, front-end clone-from-Stickers pattern. Reused here for
  the new endpoint's shape (§ design.md ADR-3).
- **Python-side Firestore writes:** `integracion_F1/subir_cruce_firebase.py` — batched
  `db.batch().set(doc, rec, merge=True)` + a `_meta` run-summary doc. This is the direct template
  for the new cruce-writing job (§ design.md ADR-2).

## 4. "Asignaciones" tab / `asignar_f3.py` — closest analog, currently orphaned

- **No live "Asignaciones" tab exists in the dashboard.** `web/data/asignaciones.json` is produced
  by `integracion_F1/asignar_f3.py` but has no frontend consumer (`web/js/analista.js` literally
  flags it as "sin consumidor propio de salud").
- **Producer shape worth reusing:** zone assignment via point-in-polygon against KML priorization
  zones (`basemaps/*rioriz*.kml`, `shapely` + `STRtree`), scored/ranked pendientes.
- **Deployment gotcha (must not repeat):** the `integracion_F1` Docker image does **not** `COPY`
  `web/`, so `asignar_f3.py`'s `export_web()` silently no-ops in production — `asignaciones.json`
  is only ever written when run locally. **This change avoids that trap entirely by writing
  directly to Firestore from the job**, not to a `web/data/*.json` file (§ design.md ADR-1/ADR-2).
- **No dashboard Leaflet precedent for a two-color (matched/pending) point map** — the one to
  clone is `evaluaciones.js`'s single-purpose circleMarker-per-category map (§2 above).

## 5. Scheduling / data-flow pattern

- **Two independent Railway cron pipelines**, both from `entrypoint.sh`-style wrappers:
  - **A) Dashboard-data pipeline** — `scripts/refresh_data.py` + `scripts/fetch_reportes_api.py`,
    publishes to **Vercel Blob** (not git) every ~15 min, to protect the 100/day deploy budget.
  - **B) `integracion_F1` pipeline** — one Docker image, three Railway cron services differing
    only by `startCommand`/`cronSchedule` (`job.py` hourly, `job_integrar_f3.py` every 2h,
    `job_asignaciones.py` daily 16:00 Bogotá). New cruce/assignment jobs plug into this image as a
    fourth service, same shape (§ design.md ADR-2).
- **Firestore vs. `web/data/*.json` for this feature:** the Stickers tab's own precedent
  (`evaluaciones`/`inspectores` are admin-SDK-read, not public Firestore reads, not `web/data/*.json`)
  is the one this change follows — the new `sticker_matches`/`cuadrillas` collections are read
  through a new admin-only API endpoint, never a public Firestore rule and never a `web/data/*.json`
  file (avoids both the Blob-publish latency and the `integracion_F1` image's `web/` omission bug).

## Key files this change reads/touches

- `integracion_F1/stickers_analysis.ipynb` (source of matching logic, to be extracted)
- `integracion_F1/cruce_gestor.py` (cascade functions reused, not duplicated)
- `integracion_F1/subir_cruce_firebase.py` (batch-write pattern reused)
- `integracion_F1/asignar_f3.py` (zone/KML reference, deployment-gotcha lesson)
- `api/stickers.js`, `api/usuarios.js` (endpoint shape precedent)
- `web/js/stickers.js`, `web/js/evaluaciones.js` (frontend shape + map precedent)
- `web/index.html`, `web/js/main.js` (tab wiring)
- `openspec/changes/archive/2026-08-24-usuarios-tab/design.md` (closest full ADR precedent)
