# Design: Puntos Solicitados — buscar existing reports + assign from the card

Change: `puntos-solicitados-busqueda-asignacion` · Project: sismo-cali-dashboard · Phase: sdd-design

Follow-up to the shipped `puntos-solicitados` feature. Reads: this change's `proposal.md`
(Engram `sdd/puntos-solicitados-busqueda-asignacion/proposal`). Builds on — and must NOT violate —
the base feature's design (`openspec/changes/puntos-solicitados/design.md`, its ADR-1…ADR-6): the
atomic dual-write to `planeacion_puntos`, and specifically **base ADR-4 (this router never owns
assignment/lifecycle fields)** enforced by `backend/tests/invariants/test_sole_writer.py`.

Mirrors real code: `backend/app/jobs/planeacion_cruce.py` (`_load_reportes:685`, the local-file-or-
`$REPORTES_URL` reportes reader), `backend/app/jobs/dashboard_refresh.py` (`_make_raw_mapper:136` /
`_write_contactos:160` — the private `puntos_contacto/atencionsismo_{registro_id}` store), `backend/
app/routers/puntos_solicitados.py` (existing `require_role("admin")` CRUD), `web/js/
puntos_solicitados.js` (`sectionHtml:194`, `#ps-crear-modal:256`, `listItemHtml:448`,
`asignarInspector:740`, `mountCombobox` usage), `web/js/stickers-asignacion.js`
(`inspectorOptionLabel:351`), `web/js/evaluaciones.js` (`#eval-download:201`, xlsx export
`710-756`).

**ADR numbering.** This repo restarts ADR numbering per change (each `changes/*/design.md` begins at
ADR-1 — verified against `puntos-solicitados` and `planeacion-flujo-confiable`). So this change's ADRs
are ADR-1…ADR-5; references to the base feature are written as "base ADR-N" to avoid collision.

## Architecture at a glance

```
Browser (admin, "Puntos Solicitados" tab)                FastAPI (puntos_solicitados.py)               Data sources
────────────────────────────────────────                ───────────────────────────────               ────────────
 "Buscar punto" modal                                     GET /puntos-solicitados/buscar?q=…
   debounced input ── GET ?q= ──────────────────────►      require_role("admin")                        reportes.json  (_load_reportes,
   results ◄── top ~20 {direccion,barrio,comuna,           join reportes[id] ⋈ puntos_contacto           PII-free: direccion/barrio/comuna/lat/lng)
              lat,lng,nombre_solicitante,telefono} ──      [registro_id], filter q over                  puntos_contacto/atencionsismo_{id}
                                                            direccion/barrio/comuna/nombre                (private Firestore: nombre/telefono)
   "Usar este punto" ─► prefills EXISTING #ps-crear-modal    module-level TTL cache (5 min)                (both produced in the SAME refresh pass)
   "Crear punto nuevo" ─► prefills only direccion=q

 list rows (listItemHtml)                                 (no backend change for assignment)
   "Asignar" ▸ inline panel ── reuse asignarInspector ──►  EXISTING planeacion-asignaciones editarAsignacion
                (mountCombobox + count badge)               writes inspector_uid on the mirror (base ADR-4 unchanged)
```

The only new server capability is a read-only `GET /puntos-solicitados/buscar`. Assignment, xlsx,
badges, and spinners are frontend-only; they add ZERO new write paths (base ADR-4/ADR-6 hold).

---

## ADR-1 — `GET /buscar` joins `_load_reportes()` ⋈ `puntos_contacto` on `id == registro_id`

**Decision.** New admin-only `GET /puntos-solicitados/buscar?q=…` in `puntos_solicitados.py`, gated by
the same `Depends(require_role("admin"))` as the rest of the router. It builds ONE joined in-memory
list from two sources already in the backend's reach:

1. **Address fields** come from `reportes.json` via `_load_reportes()` **imported from
   `app.jobs.planeacion_cruce`** — the exact reader the pipeline already uses (`REPORTES_JSON` local
   file if present, else `$REPORTES_URL`; ADR-2 caveat below). Each record carries `id`, `direccion`,
   `barrio`, `comuna`, `lat`, `lng` and is **already PII-stripped** (`_raw_record_mapper` drops
   `PII_FIELDS = {nombre, telefono, …}` before it ever lands in `reportes.json`).
2. **Requester name/phone** come from Firestore `puntos_contacto` — the private store
   `dashboard_refresh._write_contactos` writes on every refresh as
   `puntos_contacto/atencionsismo_{registro_id}` with `{registro_id, nombre_solicitante,
   telefono_solicitante}`. Never public, never in git/Blob, admin-gated here.

**Join key — exact, by construction.** `_make_raw_mapper` emits the `reportes.json` record and the
`puntos_contacto` doc from the SAME raw `rep` in one pass, both keyed by `str(rep["id"])`. So
`reportes[i].id (str) == puntos_contacto.registro_id` is a guaranteed 1:1 join, not a fuzzy match.
Build `contacto_by_id = {c["registro_id"]: c}` from a single `puntos_contacto` collection read, then
for each reporte attach `nombre_solicitante`/`telefono_solicitante` (or `None` — the contact write is
fail-soft, so a few reportes may lack a contact; name is optional in the result).

**Filter + shape.** Lowercase-normalize `q`, keep records where `q` is a substring of any of
`direccion`, `barrio`, `comuna`, `nombre_solicitante` (case-insensitive, in-memory). Return the top
~20: `{registro_id, direccion, barrio, comuna, lat, lng, nombre_solicitante, telefono_solicitante}`.
Empty/whitespace `q` → `{ok:true, resultados:[]}` (no full-dump). Wrap the two source fetches in the
same clean-502 `try/except` every other route in this file uses.

**Why name IS searchable (resolves the proposal's open question).** The proposal flagged
"search-by-requester-name" as undecided, with the cheaper alternative being address-only filtering
(filter reportes first, then join only the matched top-N `puntos_contacto` docs — a handful of
`get_all` reads instead of a full-collection read). This design INCLUDES name in the filter per the
brief, which forces the join to happen *before* filtering (name lives only in `puntos_contacto`).
The cost — one full `puntos_contacto` read — is paid down by the ADR-2 cache. If read cost ever bites,
the documented fallback is address-first filtering + per-N name join (drops name-search).
`ponytail:` full-collection read on cache-miss, downgrade to address-first + per-N join if Firestore
read volume matters.

**No new storage, no new refresh write.** `puntos_contacto` already exists and is already populated by
the existing refresh; this endpoint is a pure reader. This retires the proposal's highest risk (PII):
nothing new is persisted, nothing PII-bearing is added to `reportes.json`/Blob/git.

- *Rejected:* a new private searchable index written during refresh. Only justified if name-search
  needed sub-second latency over the full corpus; the TTL-cached in-memory scan (~14k tiny records) is
  fast enough for a per-admin, debounced action. YAGNI.
- *Rejected:* re-implementing a reportes reader here. `_load_reportes` is the one canonical path;
  re-literaling `REPORTES_JSON`/`$REPORTES_URL` would be a second source of truth for the same file.

## ADR-2 — Caching: one module-level TTL snapshot of the joined list

**Decision.** Cache the JOINED list (reportes ⋈ contacto) in a module-level TTL holder so a debounced
keystroke storm doesn't refetch Firestore + re-read `reportes.json` on every character:

```python
_BUSCAR_CACHE: dict[str, Any] = {"at": 0.0, "rows": None}
_BUSCAR_TTL_S = 300  # 5 min

def _joined_rows() -> list[dict]:
    now = time.monotonic()
    if _BUSCAR_CACHE["rows"] is None or now - _BUSCAR_CACHE["at"] > _BUSCAR_TTL_S:
        reportes = _load_reportes()
        contacto_by_id = {d.id.removeprefix("atencionsismo_"): (d.to_dict() or {})
                          for d in db.collection("puntos_contacto").get()}
        _BUSCAR_CACHE["rows"] = _build_rows(reportes, contacto_by_id)
        _BUSCAR_CACHE["at"] = now
    return _BUSCAR_CACHE["rows"]
```

**Why module-level TTL and not `lru_cache`.** `functools.lru_cache` has no time-based eviction — a
solicited-report dataset that refreshes every ~30 min must not be cached forever, and there's no cache
key here (the input `q` is applied *after* the cached list, not part of the cached build). A 5-minute
TTL means a long admin search session costs at most one full build per 5 min while staying fresh
enough that a report added in the last refresh shows up quickly.

**Why not `app.state` (the `StickerStatusCache`/`PlaneacionAggregatesCache` convention).** Those exist
because they're written from request handlers AND need per-app isolation for tests. This cache is
read-only-derived and trivially resettable; a module-level dict is the smaller correct primitive the
brief blessed. `ponytail:` process-local cache (not shared across Uvicorn workers); each worker builds
its own — acceptable for a low-QPS admin helper. Upgrade path: move to `app.state` only if worker
memory duplication or cross-worker staleness ever matters.

**Testability.** `_build_rows(reportes, contacto_by_id)` is a pure function (no Firestore, no clock) —
unit-tested directly for the join + filter + top-N; the TTL wrapper is exercised once for
build-once/serve-cached.

- *Rejected:* no cache (refetch per keystroke). A debounced input still fires several requests per
  search; each doing a full `puntos_contacto` collection read is needless Firestore cost.

## ADR-3 — Card-level "Asignar": inline expand-in-place, reusing `asignarInspector` + `mountCombobox`

**Decision.** Add an "Asignar" affordance to each `listItemHtml` row that toggles an **inline panel
expanding in place below the row** (not a floating/anchored popover), mounting the SAME
`mountCombobox` over `inspectoresCache` and calling the EXISTING `asignarInspector(id, uid)` closure —
byte-for-byte the machinery the detail modal already uses (`puntos_solicitados.js:719-731`).

**Row restructure (required, not optional).** Today `listItemHtml` renders the whole row as a single
`<button class="eval-row" data-ps-detail>`. An "Asignar" control CANNOT be nested inside it (a
`<button>` inside a `<button>` is invalid HTML and breaks the row's click target). So the `<li>` gets
two siblings: the existing `eval-row` detail button, and a small `.ps-asignar-btn` beside it; clicking
Asignar toggles a sibling `.ps-asignar-panel[hidden]` holding the combobox input/list. Event wiring
lives in `init()` via delegation on the list container — the same scope where `asignarInspector` is
defined, so no export/refactor of that closure is needed.

**Why inline expand-in-place over a floating popover.** The list is vertically scrollable. A floating
panel anchored to the button needs absolute positioning + reposition-on-scroll + overflow-clipping
handling against the scroll container — real complexity for no benefit. An inline panel reflows the
list naturally, reuses the exact `.asignacion-combo` markup/CSS the detail modal and stickers tab
already ship, and is keyboard/focus-trivial. Only one panel open at a time (opening one closes the
others). `asignarInspector` already reloads the list on success, so the row's estado/inspector badge
updates for free.

**No backend change.** `asignarInspector` posts to the existing `planeacion-asignaciones`
`editarAsignacion`, writing `inspector_uid`/`estado_asignacion` on the `planeacion_puntos` mirror.
`puntos_solicitados.py` is NOT touched — base ADR-4 (this router never writes lifecycle/assignment
fields) is preserved verbatim (see ADR-4 below).

- *Rejected:* a floating popover anchored to the button. Positioning/scroll/clipping cost with no UX
  gain over an inline panel in a scrolling list.
- *Rejected:* exporting `asignarInspector` to module scope. It's already in `init()` scope where the
  list events are wired; hoisting it is churn for nothing.

## ADR-4 — Sole-writer invariant: unchanged, and it is explicitly re-verified

**Decision & confirmation.** This change adds NO write path. The only new backend route (`GET
/buscar`, ADR-1) is read-only over `reportes.json` + `puntos_contacto`. Card-level assignment (ADR-3)
routes through the EXISTING `planeacion-asignaciones` endpoint, which is already the allowlisted writer
of the mirror's assignment fields. Therefore:

- `puntos_solicitados.py` gains **no** write to `estado_asignacion`/`cuadrilla_id`/`inspector_uid`/
  `estado_seguimiento` — base ADR-4 holds unchanged.
- `backend/tests/invariants/test_sole_writer.py` needs **no** new *writer* allowlist entry:
  `puntos_contacto` is READ here, not written. If the invariant additionally scans for any *reference*
  to a collection literal (as it does for `puntos_solicitados`/`survey_cali`), then
  `routers/puntos_solicitados.py` is added to `puntos_contacto`'s read-allowed set as a flagged
  READ-ONLY entry — mirroring how `planeacion_cruce.py` is a flagged read-only entry in
  `ALLOWED_MODULES_SURVEY_CALI`. Confirm the exact scan shape in the tasks phase; no *writer*
  allowlist changes either way.

**Why this is called out as an ADR.** The base feature's whole architecture rests on the mirror being
sole-written by the pipeline/assignment endpoints. A "card-level assign" feature is exactly the kind of
convenience that tempts a shortcut write into `puntos_solicitados.py`; this ADR records that the
correct implementation reuses the existing writer and touches no new write path.

## ADR-5 — Inspector load-count badges are computed client-side from already-fetched points

**Decision.** The `.asignacion-combo-count` badge on each inspector option (both the detail-modal
combobox and the new card-level panel) is derived **client-side** from the solicitado-points list the
tab already fetched — no new backend call. Adapt `inspectorOptionLabel(insp, count)` from
`stickers-asignacion.js:351-356` (`Nombre — codigo (N)`); `count` is a separate argument there
precisely because it is computed by the caller, not embedded in the inspector object.

`count[uid] = number of currently-loaded solicitado puntos whose inspector_uid === uid` — a one-pass
tally over the already-in-memory list, recomputed on each list reload.

**Ceiling (documented).** This reflects **solicitado-tab load only**, not the inspector's global
planeacion load. A true global count exists via `planeacion_asignaciones`' `metricasProgreso`
(`por_inspector`), but consuming it here is a second fetch this tab doesn't currently make. Per the
brief ("prefer reusing what's already fetched over adding a new backend call") and YAGNI, the
solicitado-scoped count ships; the badge label can say so if ambiguity matters. `ponytail:`
tab-scoped count; swap to `metricasProgreso.por_inspector` only if admins need each inspector's global
load at assign-time.

- *Rejected:* a new backend count query per open. Extra latency + Firestore reads for a hint that the
  already-fetched list mostly answers.

---

## Prefill field mapping (F1 — "Usar este punto" → `#ps-crear-modal`)

The modal is ALREADY reused for edit mode (`editing` state, `puntos_solicitados.js:690`), so a
programmatic-prefill path into the comuna/barrio comboboxes already exists — extend it, don't
duplicate. Mapping from a `buscar` result → existing form field:

| result field | form target | notes |
|---|---|---|
| `direccion` | `input[name="direccion"]` (`#ps-direccion`) | also default `input[name="nombre"]` ← `direccion` (editable point name; admin can overwrite) |
| `comuna` | `#ps-comuna-input` (combobox) | set value + fire the combobox's select so barrios load |
| `barrio` | `#ps-barrio-input` (combobox) | **set AFTER comuna** — the barrio combo is `disabled` until a comuna is chosen (`"Elegí una comuna primero…"`); prefill sequence must be comuna → load barrios → barrio |
| `lat` | `#ps-lat` | |
| `lng` | `#ps-lng` | number inputs; also drop the draggable marker at `{lat,lng}` |
| `nombre_solicitante` | `input[name="nombre_solicitante"]` | |
| `telefono_solicitante` | `input[name="telefono_solicitante"]` | |
| — | `textarea[name="justificacion"]` | left EMPTY (required; admin states why the point is solicited) |

**"Crear punto nuevo" fallback:** prefill ONLY `input[name="direccion"]` (and `name="nombre"`) ← the
typed `q`; everything else blank. Same modal, no result selected.

**Gotcha:** the comuna/barrio ordering above is the one real sequencing trap — a naive
`form.reset()`-then-set-all would leave barrio disabled and silently drop its value.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `backend/app/routers/puntos_solicitados.py` | Modify | Add `GET /puntos-solicitados/buscar` (ADR-1, `require_role("admin")`), `_build_rows` pure helper, module-level TTL cache (ADR-2). Import `_load_reportes` from `app.jobs.planeacion_cruce`. NO write path added. |
| `web/js/puntos_solicitados.js` | Modify | "Buscar punto" button + modal + debounced fetch + results list (F1); prefill into existing `#ps-crear-modal` (mapping above); "Asignar" inline panel in `listItemHtml` + wiring (ADR-3); count badges via `inspectorOptionLabel` (ADR-5); xlsx export mirroring `evaluaciones.js:710-756`; `.asignacion-spinner` on `#ps-crear-submit` + `#ps-geocode-btn` replacing text-only busy states. |
| `web/styles.css` | Modify | `.ps-asignar-btn`/`.ps-asignar-panel` inline-panel styles; reuse existing `.asignacion-combo`/`.asignacion-combo-count`/`.asignacion-spinner`. |
| `backend/tests/invariants/test_sole_writer.py` | Modify (conditional) | Only if the invariant scans for any `puntos_contacto` *reference*: add `routers/puntos_solicitados.py` as a flagged READ-ONLY entry (ADR-4). No writer-allowlist change. |
| `openspec/specs/puntos-solicitados/spec.md` | Modify | New scenarios: buscar search, prefill, card-level assign, xlsx, badges, spinners (tasks/spec phase). |

## Interfaces / Contracts

**`GET /puntos-solicitados/buscar?q={str}`** — `require_role("admin")`:
```
200 { ok:true, resultados:[ { registro_id, direccion, barrio, comuna, lat, lng,
                              nombre_solicitante|null, telefono_solicitante|null }, … ≤20 ] }
200 { ok:true, resultados:[] }         # empty/whitespace q, or no matches
502 { detail }                          # reportes/Firestore read failure (same convention as sibling routes)
```
Filter: `q.lower()` substring over `direccion|barrio|comuna|nombre_solicitante`. No pagination (top-20
is the contract; refine `q` for more). No caller-supplied limit (YAGNI).

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| pytest unit | `_build_rows`: join on `id==registro_id`, name attached/`None`, substring filter over all 4 fields, top-20 cap, case-insensitive | pure function, fake reportes list + fake contacto dict |
| pytest unit | TTL cache: builds once, serves cached within TTL, rebuilds after | monkeypatch `time.monotonic` + counting fake `_load_reportes` |
| pytest guard | non-admin `GET /buscar` → 403, zero source reads | `TestClient` + `require_role` override |
| pytest guard | `puntos_solicitados.py` still writes NO assignment/lifecycle field (base ADR-4) | existing sole-writer invariant, unchanged |
| node pure | prefill mapping (result→field), comuna-before-barrio ordering, client-side count tally, buscar result rendering | `node --test`, mirroring `evaluaciones.test.mjs` |

## Threat Matrix

PII is the sensitive surface. `nombre_solicitante`/`telefono_solicitante` are served ONLY by
`GET /buscar` behind `require_role("admin")`, sourced from the private `puntos_contacto` store that is
already never public/git/Blob. `reportes.json` stays PII-free (unchanged). No new routing, shell,
subprocess, VCS, or executable-classification surface. No new write path → no new integrity/authz
write risk (base ADR-4/ADR-6 intact).

## Migration / Rollout

No migration, no schema change, no new storage. Purely additive: one read-only endpoint + frontend.
Rollback = revert the frontend + remove the `buscar` route; `puntos_contacto` and `reportes.json` are
untouched. No new env var (reuses `$REPORTES_URL` only in the same fallback `_load_reportes` already
uses when the local file is absent).

## Open Questions

- [ ] None blocking. Confirm in the tasks phase whether `test_sole_writer.py` scans for
  `puntos_contacto` *references* (would add one flagged read-only entry) or only *writers* (no change).
  Either way, no writer allowlist changes and base ADR-4 is preserved.
