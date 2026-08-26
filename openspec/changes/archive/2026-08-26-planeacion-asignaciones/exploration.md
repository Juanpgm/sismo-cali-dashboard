# Exploration: Planeación — cruce Survey Cali ↔ API y asignación de levantamientos

Change: `planeacion-asignaciones` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-explore

## Feature request (user, Spanish)

"Una nueva pestaña **Planeación**, hermana de Stickers, que cruce los registros de **Survey Cali**
(las inspecciones EDAN de Survey123 que viven en Firestore `survey_cali`) contra **el total de
puntos obtenidos en la API de atencionsismo**, identifique y **priorice los puntos a los que aún no
se les ha aplicado el survey**, los asigne a inspectores/profesionales replicando la lógica del
endpoint/UI de asignación de stickers, y **pase una clave de integración prellenada en el survey**
para poder rastrear el registro devuelto hasta su punto vía Firebase — con **posibilidad de edición
o corrección** de las asignaciones, tanto en la API como en el dashboard."

Decoded: a second, independent assignment campaign — same shape as the sticker campaign, different
point universe (atencionsismo API reports, not Panel/EDE points), different "done" signal (an EDAN
survey exists in `survey_cali`, not a field sticker in `evaluaciones`), plus two things the sticker
campaign does not have: an explicit **priority ranking** of the pending pool, and a **round-trip
integration key** written into the Survey123 form so the returned survey identifies its own point.

---

## 1. Precedent that must NOT be reused: `integracion_F1/cruce_criticos_survey.py`

The legacy `integracion_F1` repo already implements this exact cross-reference:

- 5-step cascade: exact `globalid` → proximity ≤ 20 m → address → combined → miss.
- Tags each point `levantado` / `pendiente`.
- Mints a stable `clave_integracion` via `id_asignacion()` (`integracion_F1/asignar_f3.py:259-267`).
- Uploaded by `integracion_F1/subir_cruce_firebase.py`.

**It is unusable as-is.** It writes to Firestore project `dagma-85aad`, collection
`cruce_criticos_survey`, which is explicitly EXCLUDED by
`openspec/changes/fastapi-backend-consolidation/proposal.md:109-118` — "Extension 2 (user directive,
2026-08-25): NOTHING dagma-related is used … No dagma credential, project id, collection name, or
API constant appears anywhere in `backend/`."

**Consequence for this change:** the cross-reference is reimplemented FRESH against
`sismo-agosto-sgred`, mirroring `backend/app/jobs/cruce_sticker.py`'s patterns. The legacy file may
be read as *conceptual reference for the matching cascade only*; no dagma project id, collection
name, credential, or import may appear anywhere in `backend/`.

## 2. Data source A — atencionsismo API points (the "total")

- **Client**: `backend/app/services/atencionsismo.py`, `API_URL =
  https://atencionsismo.cali.gov.co/api/informe/json` (line 35), HTTP Basic auth from
  `VISITADOS_API_USER` / `VISITADOS_API_PASS` (lines 37-38). Day-walk with recursive window halving
  (`fetch_window`, `day_walk`), `mapper` hook for the per-record shape.
- **Persisted artifact**: `backend/app/jobs/dashboard_refresh.py` `fetch_reportes()` (lines 140-183)
  already walks the whole range with `_raw_record_mapper` (every analytic field, PII/heavy stripped)
  and writes `web/data/reportes.json` (+ `reportes_meta.json`, `reportes_agg.json`), then publishes
  them to Vercel Blob (`_PUBLISH_FILES`, lines 82-90). **~14.8k records live today.**
- **Record shape confirmed** from the live artifact: `id`, `direccion`, `barrio`, `comuna`,
  `estadoVerificacion`, `afectacion`, `tipoInmueble`, `nombreEdificio`, `habitabilidad` (empty until
  inspected), `conceptoTecnico`, `visitado`, `fechaEvaluacion`, `latitud`/`longitud` (strings),
  `lat`/`lng` (numeric, parsed by `_parse_coords`), `fechaCreacion`, `fechaEnvio`. PII already
  stripped (`PII_FIELDS`/`HEAVY_FIELDS`, `dashboard_refresh.py:77-78`).
- **No ArcGIS GlobalID on this endpoint.** So the legacy cascade's step 1 (exact globalid match) is
  NOT available from this source — geo + address only, exactly like `cruce_sticker.py`.
- **Flagged alternative**: `operario/reports/visitados-criticos`
  (`integracion_F1/integracion/api_visitados.py:23`) DOES carry `placeId` (`arcgis:<GlobalID>`) and
  is a pre-filtered critical subset — but it is NOT ported into `backend/app/` and its only consumer
  is the excluded dagma pipeline. The user's wording ("el total de puntos obtenidos en la API")
  points at `informe/json`. Recorded as a resolved-but-flagged decision (design.md ADR-2).

## 3. Data source B — `survey_cali` (the "done" signal)

- **Service**: `backend/app/services/survey_cali.py`. Collection `survey_cali`, doc id = `GlobalID`.
- **Document shape**: `web/data/inspections.json`'s per-record dict verbatim, plus `_rev`,
  `_updated_at`, `_updated_by`, `_source`, `_source_hash`, `_deleted`, with an append-only
  `history/{rev_NNNNNN}` subcollection.
- **Location/address fields confirmed**: `x` (lon), `y` (lat), `coords` ("lat, lon" string),
  `direccion`, `direccion_norm`, `comuna`, `barrio_geo`.
- **Sole write path**: `apply_mutation()` (ADR-12 of the consolidation change) — every create,
  patch, revert, and ingest funnels through it. This change **reads only**.
- **Ingestion**: `ingest_records()` (lines 289-353), called from
  `app/jobs/dashboard_refresh.py`. Per-record gate: `EditDate` pre-filter → `canonical_hash`
  content gate → `diff_upstream_fields` against `_source`.

## 4. THE INTEGRATION KEY — the centerpiece finding

- The Survey123 layer behind the EDAN form is `EDE_v1`:
  `https://services8.arcgis.com/ljfiJpg35HWgdtaC/arcgis/rest/services/service_16fa1d2000ea4fa68304bc030a95e8d1/FeatureServer/0`
  (`scripts/refresh_data.py:79-82`), `serviceItemId = 74aeda67b10b4725bb47e7b20ae6a2bf`,
  capabilities `Create,Query,Editing`.
- **The layer already carries a purpose-built, completely unused field: `codigoapp`** (String 255,
  alias "Codigo generado aplicativo"). Verified against the live published data: **0 of 1091 records
  carry any value.** It is free to claim as the integration key.
- **Prefill mechanism** (verified against Esri docs): Survey123 **web forms** accept
  `?field:<question_name>=<value>`; the **field app** accepts
  `arcgis-survey123:///?itemID=<id>&field:<name>=<value>`. The question `name` must match the layer
  column name — literally `codigoapp`.
- **The form share URL / item id is NOT present anywhere in this repo.** Only the FeatureServer URL
  is. It must come from the ArcGIS org admin and be supplied as configuration (design.md ADR-6).

### The bug that would silently break the whole chain

`scripts/refresh_data.py:1111`:

```python
df = df[list(LAYER_TO_RAW.values()) + ["x", "y"]]
```

An explicit column allowlist. The layer query at line 1070 uses `outFields: "*"`, so `codigoapp`
**is fetched** — but `codigoapp` is not a key in `LAYER_TO_RAW` (dict opens at line 936), so line
1111 drops it before `inspections.json` is written, and it therefore never reaches `survey_cali`.
Verified empirically: `'codigoapp' in record` → `False` for every live published record.

**Adding `codigoapp` to `LAYER_TO_RAW` is a mandatory, load-bearing task.** Without it, the survey
comes back with the key filled in on the ArcGIS side and the backend never sees it — the round trip
looks like it works in the field and silently fails in the data.

## 5. Patterns to replicate

### `backend/app/routers/sticker_asignaciones.py` (485 lines)

Single admin-gated `POST /sticker-asignaciones` dispatcher (`require_role("admin")`, line 454) with
**10 actions**: `listPuntos`, `listCuadrillas`, `autoAgrupar`, `crearCuadrilla`, `editarCuadrilla`,
`asignarInspector`, `desasignarInspector`, `reasignarPunto`, `eliminarCuadrilla`,
`reiniciarAgrupacion` (dispatch table, lines 460-480). Reusable machinery:

- `haversine_m` (67-75), `auto_agrupar` (78-102) — deterministic greedy nearest-neighbour, stable
  `[lat, lon]` sort, no RNG, defaults `DEFAULT_MAX_RADIUS_M = 800` / `DEFAULT_MAX_SIZE = 8` (56-57).
- Guards (105-122): `points_already_assigned` (one point → at most one cuadrilla),
  `points_with_sticker`, `points_with_colapso_total` — read-before-write, specific error first.
- `commit_in_chunks` (125-132) — 500-op Firestore batch chunking.
- Grouping and assigning are **separate actions**: `run_auto_agrupar` MUST NOT touch
  `estado_asignacion` (161-193).

### `backend/app/jobs/cruce_sticker.py` (454 lines)

The cron cross-reference. **Its field-ownership split is copied exactly**:

- `PIPELINE_FIELDS` (83-85) — written every run via `merge:true`.
- `ADMIN_DEFAULT_FIELDS` (86-87, `estado_asignacion:'pendiente'`, `cuadrilla_id:None`,
  `inspector_uid:None`) — seeded ONLY on a doc's first write, never re-applied (`build_write_ops`,
  256-270).
- Deterministic doc id `f"{fuente}_{registro_id}"` (`doc_id`, 91-94).
- Incrementality: Firestore watermark doc `_meta/cruce_sticker_state` (72, `read_watermark` 180-186,
  `write_watermark` 189-191) + a cheap projected pre-read (`read_tiene_sticker_state`, 194-209,
  `get_all(..., field_paths=["tiene_sticker"])`) feeding a pure `select_candidates` (212-222).
- "Nothing changed → don't rewrite" skip (388-389).
- `--check` / `--dry` / `--top N` flags; offline `_selfcheck()` (290-356); runlog-wrapped `main()`
  (420-449).

### `web/js/stickers-asignacion.js` (906 lines)

3-step guided UI (Agrupar → Cuadrillas e inspectores → tabla de puntos). Leaflet map + sortable/
filterable table sharing one `rows` array. Pure exported helpers for the self-check
(`colorForPunto`, `buildRows`, `sortRows`, `filterRows`, `activeCountsByInspector`, `gaugeCounts`,
`filterInspectores`, `cuadrillaLabel`, `inspectorOptionLabel`). Searchable inspector combobox;
per-point popup `<select>` for reassignment. Optimistic local mutation + `renderAll()` for per-item
actions, full `reload()` only for toolbar actions. Exported as
`initStickersAsignacion(root, {getToken, getInspectores}) -> {reload}` (line 563) — note it
**receives** the roster from its parent rather than fetching it.

### Tab mounting and role gating

- `web/index.html:70-77` — `.view-tabs` buttons (`panel`, `acciones`, `stickers`, `usuarios`,
  `analista`).
- `web/index.html:276-285` — one `<section id="view-X" data-view-panel="X" ... hidden></section>` per
  tab; every admin tab's section is **empty in the HTML** — its JS module sets `root.innerHTML` on
  each open.
- `web/js/main.js:221-257` `switchView()` — one `if (view === 'x') initX(document.getElementById(
  'view-x'), { getToken: getIdToken });` block per admin tab. Re-inits on every open.
- `web/styles.css:1559-1564` — role gating is **CSS-only**:
  `body:not([data-role="admin"]) .view-tab[data-view="..."] { display: none !important; }`, one
  selector per admin tab. **A new tab that is not added to this selector list leaks to non-admins.**
- `web/js/api-config.js` — per-endpoint URL map (`API_CONFIG` + `apiUrl(name)`), the consolidation's
  repoint mechanism. `reportados`/`stickerStatus`/`sourceStatus` already point at
  `RAILWAY_BASE_URL`; the rest are still relative Vercel paths.

### Sole-writer invariant

`backend/tests/invariants/test_sole_writer.py` scans every `.py` under `backend/app/` for literal
collection names and asserts they appear ONLY in an explicit `ALLOWED_MODULES` set — separate,
independent allowlists per collection (`sticker_matches`/`cuadrillas` at lines 87-92, `survey_cali`
at 99-123). Both existing sets are marked CLOSED. **Any new Firestore collection literal needs its
own allowlist constant + its own test function**, because these collections have no Firestore
security rules and "the backend is sole writer" must hold by construction.

## 6. Backend wiring

- `backend/app/main.py:17-29` — routers imported by name; `_ROUTERS` tuple (35-41) is what
  `create_app()` mounts (105-106) and what `credentials.required_clients_for()` validates against
  at startup (80-81). A new router module joins both.
- `backend/app/config.py` — `Settings(BaseSettings)` is nearly empty (only `app_name`); CORS
  allowlist is module-level constants. New env-driven configuration goes here.
- Test suite: `python -m pytest backend/tests/ -v`, **259 passing on `main`**. Layout mirrors the app
  (`tests/routers/`, `tests/jobs/`, `tests/services/`, `tests/invariants/`).

## Key files this change reads/touches

- `backend/app/services/atencionsismo.py` — API client (read; possibly a new mapper)
- `backend/app/jobs/dashboard_refresh.py` — produces `reportes.json` (read-only reference)
- `backend/app/services/survey_cali.py` — survey docs (READ ONLY; `apply_mutation` never called)
- `backend/app/jobs/cruce_sticker.py` — the pipeline template
- `backend/app/routers/sticker_asignaciones.py` — the router template
- `backend/app/integracion/cruce_gestor.py` — cascade functions (`nearest`, `match_by_direccion`,
  `build_addr_index`, `addr_key`, `_eval_latlon`), imported, never forked
- `backend/app/main.py`, `backend/app/config.py` — mounting + configuration
- `backend/tests/invariants/test_sole_writer.py` — new allowlist entries + new test functions
- `scripts/refresh_data.py` — `LAYER_TO_RAW` (**the `codigoapp` fix**)
- `web/index.html`, `web/js/main.js`, `web/styles.css`, `web/js/api-config.js` — tab wiring
- `web/js/stickers-asignacion.js` — the frontend template
- `openspec/changes/fastapi-backend-consolidation/proposal.md:109-118` — the binding dagma exclusion
</content>
