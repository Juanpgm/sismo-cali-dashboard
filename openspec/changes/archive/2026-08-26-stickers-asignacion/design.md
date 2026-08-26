# Design: Stickers — cruce y asignación

Change: `stickers-asignacion` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-design

Reads: `proposal.md`, `exploration.md`. Mirrors real code: `integracion_F1/cruce_gestor.py`
(cascade functions), `integracion_F1/subir_cruce_firebase.py` (batch-write shape),
`integracion_F1/asignar_f3.py` (zone/KML pattern, deployment gotcha), `api/stickers.js` /
`api/usuarios.js` (endpoint shape, `getAdmin()`), `web/js/stickers.js` / `web/js/evaluaciones.js`
(`callApi`, `initStickers`, Leaflet map pattern), `web/index.html`, `web/js/main.js`.

## Architecture at a glance

```
integracion_F1 (Railway cron, new service)          Firestore (sismo-agosto-sgred)         Vercel serverless          Browser (admin, Stickers tab)
────────────────────────────────────          ──────────────────────────────         ─────────────────          ─────────────────────────────
cruce_sticker.py (daily)                       sticker_matches/{punto_id}             api/sticker-asignaciones.js
  reuses cruce_gestor cascade    ──batch                pipeline-owned fields ◄────┐   ├ listPuntos            ◄──POST──  web/js/stickers-asignacion.js
  reads Panel (inspections.json    set,merge=true►      admin-owned fields    ◄────┼───┤ listCuadrillas                    (new sub-section inside
  + puntos_israel_cali.json)                            (never touched by job)     │   ├ autoAgrupar                        #view-stickers)
  reads evaluaciones (Firestore)                                                    │   ├ crearCuadrilla/editarCuadrilla    ├ tabla ordenable
                                                cuadrillas/{id}            ◄────────┘   ├ asignarInspector                  └ mapa azul/rojo (Leaflet)
                                                  puntos[], inspector_uid,               ├ reasignarPunto
                                                  origen: auto|manual                    └ eliminarCuadrilla
                                                                                          (admin-SDK, Bearer token,
                                                                                           same auth preamble as
                                                                                           api/stickers.js)
```

The table+map sub-section only ever reads `sticker_matches`/`cuadrillas` (≈1100 lean docs) through
the admin API — it never loads `inspections.json` or `puntos_israel_cali.json`, which is the
literal "sin necesidad de cargar todos los datos de Panel" requirement.

---

## ADR-1 — `sticker_matches` document shape and id

**Decision.** One doc per Panel point, id = `` `${fuente}_${registro_id}` `` (e.g. `ede_1234`,
`israel_45`) — the same stable key the notebook already uses to concatenate the two Panel sources
(`exploration.md` §1). Deterministic id means re-running the job **updates** the same doc instead
of duplicating it; no separate "does this point already exist" query is needed before writing.

Two disjoint field groups in the same doc, so ownership is enforced by *which fields a writer ever
touches*, not by a second collection:

```
sticker_matches/{fuente}_{registro_id} {
  // pipeline-owned — overwritten every cruce_sticker.py run, merge:true on this subset only
  fuente:           'ede' | 'israel'
  registro_id:       string
  tiene_sticker:      boolean
  tier:              'alta' | 'media' | 'sospechoso' | null   // null when tiene_sticker=false
  sticker_dist_m:     number | null
  direccion:          string
  coords:             { lat: number, lon: number }
  zona_id:            string | null
  matched_at:         Timestamp

  // admin-owned — only api/sticker-asignaciones.js ever writes these
  estado_asignacion:  'pendiente' | 'asignado' | 'en_proceso' | 'hecho'   // default 'pendiente'
  cuadrilla_id:       string | null
  inspector_uid:      string | null
  asignado_en:        Timestamp | null
  reasignado_de:      string | null   // previous inspector_uid, for the one-hop breadcrumb (proposal.md risk 3)
}
```

**Why one doc with two field groups, not two collections.** A point's identity and its assignment
state are the same real-world thing (this Panel point); splitting them into
`sticker_matches`/`sticker_assignments` would need a join on every table/map read for no benefit —
the table needs both groups in the same row. The ownership boundary is enforced in code (§ADR-2,
§ADR-3), not by physical separation.

- *Rejected:* re-deriving `estado_asignacion` from `cuadrilla_id` presence (`asignado` iff
  `cuadrilla_id != null`). Collapses `en_proceso`/`hecho` into the same bucket as `asignado`,
  losing the workflow granularity the CRUD needs (a cuadrilla can be assigned but not yet visited).

## ADR-2 — `cruce_sticker.py`: extraction, write path, and cron wiring

**Decision.** New file `integracion_F1/cruce_sticker.py`, structured like
`integracion_F1/asignar_f3.py` (`main()`, `--check`, `--dry`, `--top N` flags):

1. Load Panel points the same way the notebook does (§`exploration.md` §1: `inspections.json` +
   `puntos_israel_cali.json`, `x`/`y` EXIF-corrected coords).
2. Read `evaluaciones` from Firestore (same 3-tier credential resolution as
   `subir_cruce_firebase.py`).
3. Run `cruce_sticker()` **imported from the notebook's extraction target, `cruce_gestor.py`'s
   existing cascade functions** (`nearest`, `match_by_direccion`, `build_addr_index`, `addr_key`,
   `_eval_latlon`) — same import shape the notebook itself already uses, so there is exactly one
   place the matching logic lives.
4. Write only the **pipeline-owned field subset** (ADR-1) per point via
   `db.batch().set(doc_ref, pipeline_fields, merge=True)` — batched in groups of ≤500 (Firestore
   batch limit), following `subir_cruce_firebase.py upload()`.
5. First-write default: if a `sticker_matches/{id}` doc does not exist yet, the same `merge=True`
   set also needs `estado_asignacion: 'pendiente'` seeded — done by checking existence via a
   pre-read `getAll()` batch (same shape as the `inspectores` join in `api/stickers.js`), not by a
   full document overwrite.
6. Offline `--check` self-test (same idiom as `_selfcheck_cruce_sticker` in the notebook) —
   asserts the pipeline-owned/admin-owned merge never touches the admin fields, using a fixture
   Firestore-shaped dict.

**Cron wiring.** New Railway service in the same `integracion_F1` Docker image (no new image, no
new `COPY` — the job only needs what `job_asignaciones.py` already has access to: `integracion/`,
`basemaps/`, `.py` scripts). Cadence: daily, same slot family as `job_asignaciones.py`
(`integracion_F1/railway.json` gets one more `startCommand`/`cronSchedule` pair). Confirmed with
the operator per `proposal.md` risk 3 — no on-demand trigger in v1.

**Why Firestore, not `web/data/asignaciones.json`.** `asignar_f3.py`'s `export_web()` silently
no-ops in production because the `integracion_F1` image doesn't `COPY web/` — the same trap would
hit a JSON-file approach here. Writing straight to Firestore sidesteps it entirely and matches how
`evaluaciones`/`inspectores` are already read (admin-SDK, not a static file).

- *Rejected:* running the cruce inside the existing hourly `job.py`. Keeps unrelated pipelines
  independently schedulable/debuggable (mirrors why `job_asignaciones.py` is already its own
  service rather than folded into `job.py`).

## ADR-3 — `api/sticker-asignaciones.js`: endpoint shape and auto-agrupar algorithm

**Decision.** One new serverless function, byte-for-byte the same skeleton as `api/stickers.js`
(POST-only, `{action, ...args}` body, `verifyFirebaseToken` + admin-role check, own `getAdmin()`
singleton — duplicated per file, per the repo's established "each function stands alone"
convention, `exploration.md` §3).

| action | result | notes |
|---|---|---|
| `listPuntos` | `{ok, puntos}` | full `sticker_matches` read (≈1100 docs, lean fields — the "no full Panel load" requirement) |
| `listCuadrillas` | `{ok, cuadrillas}` | full `cuadrillas` read |
| `autoAgrupar` | `{ok, cuadrillas}` | clusters current `pendiente` points with no `cuadrilla_id`, creates new `cuadrillas` docs, sets `cuadrilla_id` on member points; leaves `estado_asignacion:'pendiente'` until an inspector is actually assigned (§ below) |
| `crearCuadrilla` | `{ok, id}` | manual: `{nombre, puntos: [id...]}` |
| `editarCuadrilla` | `{ok}` | add/remove points from an existing cuadrilla |
| `asignarInspector` | `{ok}` | `{cuadrilla_id, inspector_uid}` → propagates `inspector_uid`/`asignado_en`/`estado_asignacion:'asignado'` to every point in the cuadrilla |
| `reasignarPunto` | `{ok}` | `{punto_id, nuevo_inspector_uid}` → sets `reasignado_de` = old uid, updates `inspector_uid` |
| `eliminarCuadrilla` | `{ok}` | clears `cuadrilla_id`/`inspector_uid` on member points, deletes the cuadrilla doc |

**`autoAgrupar` does not assign an inspector.** Grouping (which points belong together) and
assigning (which inspector covers them) are separate actions — `autoAgrupar` only creates
`cuadrillas` docs with `origen:'auto'`, `inspector_uid: null`; the operator then calls
`asignarInspector` per cuadrilla (or the frontend chains both calls from one "auto-agrupar y
asignar" button — a UI convenience, not a new backend action).

**Auto-agrupar algorithm — deterministic greedy nearest-neighbor, not k-means.** Locked per
`proposal.md` risk 4 (no RNG, so re-running on an unchanged point set is explainable/stable):

```js
// input: pendiente points with no cuadrilla_id, sorted by [lat, lon] for determinism
// params: maxRadiusM (default e.g. 800), maxSize (default e.g. 8)
function autoAgrupar(puntos, { maxRadiusM, maxSize }) {
  const unassigned = new Set(puntos.map(p => p.id));
  const grupos = [];
  for (const seed of puntos) {                 // stable iteration order
    if (!unassigned.has(seed.id)) continue;
    const grupo = [seed]; unassigned.delete(seed.id);
    for (const p of puntos) {
      if (grupo.length >= maxSize) break;
      if (!unassigned.has(p.id)) continue;
      if (haversineM(seed.coords, p.coords) <= maxRadiusM) {
        grupo.push(p); unassigned.delete(p.id);
      }
    }
    grupos.push(grupo);
  }
  return grupos;
}
```

`haversineM` is copy-imported from `cruce_gestor.py`'s existing haversine helper's JS-side
equivalent (or reused if the repo already has a JS haversine — else a five-line port, no new
dependency). This is intentionally the *simplest* rung that holds: no k-means, no external
clustering library, O(n²) over ≈hundreds of pending points (well within a serverless function's
budget at this scale).
`// ponytail: O(n²) greedy grouping, fine to a few thousand pending points; switch to a spatial
// grid/STRtree pre-bucket (same pattern as asignar_f3.py's zone lookup) if it ever gets slow.`

- *Rejected:* k-means. Needs a random/seeded init to converge, which reintroduces the
  non-determinism this ADR is explicitly avoiding, for a clustering problem (a few hundred points,
  loose "nearby" grouping) that doesn't need k-means' iterative refinement.
- *Rejected:* reusing `asignar_f3.py`'s KML zones as the grouping unit (the option the user did
  **not** pick). Locked by the user's explicit choice of proximity clustering over zone-based
  grouping in the brainstorm conversation.

## ADR-4 — Frontend: `web/js/stickers-asignacion.js`, mounted inside the Stickers tab

**Decision.** New module, same lifecycle shape as `web/js/evaluaciones.js`:
`initStickersAsignacion(root, {getToken})` renders once, `reload()` fetches `listPuntos` +
`listCuadrillas`, re-renders on every Stickers-tab open (not a separate top-level tab — locked
decision, `proposal.md` Impact).

- **Placement.** A segmented control inside `#view-stickers` alongside the existing
  roster/evaluaciones sections (e.g. "Roster · Evaluaciones · Asignación"), reusing whatever
  sub-nav pattern `stickers.js` already uses to switch between its own sections, or a simple
  three-button toggle if none exists yet.
- **Tabla.** Columns: dirección, zona, estado_asignacion, cuadrilla, inspector, tier. Sortable by
  clicking a column header (client-side sort over the already-fetched `puntos` array — same
  weight class as the `usuarios-tab` inline-filter decision, no new dependency). Filter chips for
  `estado_asignacion`.
- **Mapa.** Clone of `evaluaciones.js`'s Leaflet setup: `L.circleMarker` per point, color = blue
  (`tiene_sticker === true`) / red (`estado_asignacion === 'pendiente'`) / amber (`asignado` or
  `en_proceso`, so an operator can see work-in-progress distinctly from untouched-red) — a 3-color
  legend, not strictly the 2-color ask, because collapsing "assigned but not yet visited" into
  "pending" would hide the CRUD's own state. `fitBounds()` on load; popup with a "Ver detalle /
  Reasignar" action reusing `evaluaciones.js`'s popup-button pattern.
- **CRUD controls.** "Auto-agrupar" button (calls `autoAgrupar` with default radius/size, editable
  via a small settings affordance); manual multi-select on the table or map (checkbox column /
  shift-click) → "Crear cuadrilla" from selection; per-cuadrilla inspector `<select>` (populated
  from the existing `inspectores` roster the Stickers tab already loads — no new roster fetch)
  → `asignarInspector`; per-point "Reasignar" action in the popup/detail modal → `reasignarPunto`.

- *Rejected:* a fourth marker color per `tier` (alta/media/sospechoso). Out of scope — tier is a
  data-quality signal for the pipeline/notebook, not an assignment-workflow state; it stays a table
  column, not a map dimension, to keep the legend at 3 colors.

## ADR-5 — Tab wiring

**Decision.**
- **`web/index.html`** — no new `.view-tabs` entry. Add the "Asignación" segment/button inside
  `#view-stickers`'s existing sub-nav and a new `<div data-sticker-section="asignacion" hidden>`
  container next to the roster/evaluaciones ones.
- **`web/js/stickers.js`** (or `main.js`, whichever currently owns the Stickers section-switch) —
  import and lazy-call `initStickersAsignacion(container, {getToken})` the first time the
  "Asignación" segment is opened, mirroring how `initStickers`/evaluaciones init already happens
  lazily on first Stickers-tab open.
- **`web/styles.css`** — reuse `.sticker-*` table/chip styles; add `.asignacion-*` only for the
  segmented control and the 3-color legend, matching the `usuarios-tab` precedent of styling only
  what genuinely differs.

## Size and commit/PR split

Four work units (per `proposal.md`):

1. **`feat(pipeline): cruce_sticker job`** — `integracion_F1/cruce_sticker.py` + `--check`
   self-test + `integracion_F1/railway.json` cron entry.
2. **`feat(api): sticker-asignaciones endpoint`** — `api/sticker-asignaciones.js` + a small
   `assert`-based self-check for `autoAgrupar`'s clustering function (pure, testable in isolation:
   fixture points in/out of radius, `maxSize` cap respected, deterministic on repeated calls).
3. **`feat(web): asignación sub-section`** — `web/js/stickers-asignacion.js` + `index.html` +
   wiring + `styles.css`.
4. **Firestore console** — `sticker_matches`/`cuadrillas` rules (admin-SDK-only), applied manually
   in the `sismo-agosto-sgred` console, not a repo diff (same posture as the `usuarios-tab`
   Phase-2 rules note).

## Runnable check (locked)

- `cruce_sticker.py --check`: offline fixture asserting (a) the merge never overwrites
  `estado_asignacion`/`cuadrilla_id`/`inspector_uid` on an existing doc, (b) a first-write seeds
  `estado_asignacion:'pendiente'`.
- `api/sticker-asignaciones.test.js` (mirrors `stickers.test.js`/`usuarios.test.js`): `assert`-based
  `demo()` for the greedy clustering function — fixture points confirm radius/size caps and
  determinism (same input twice → same groups).

## Risks / open decisions carried to tasks

1. Default `maxRadiusM`/`maxSize` for auto-agrupar are placeholders above (800m / 8 points) —
   confirm realistic cuadrilla size with the operator before locking as the shipped default.
2. Sub-nav mechanism inside `#view-stickers` needs a quick read of the current `stickers.js`
   markup at task time to confirm whether a section-switch pattern already exists to extend, or
   needs to be introduced for the first time by this change.
3. Carries proposal.md risks 1-5 unchanged (doc-id stability, merge semantics, cron cadence,
   clustering determinism, console-managed rules) into task-level acceptance criteria.
