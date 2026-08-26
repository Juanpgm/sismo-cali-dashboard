# Design: Planeación — append-only audit log ("bitácora")

Change: `planeacion-auditoria` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-design

## Technical Approach

Add an append-only `planeacion_auditoria` collection written by exactly one new module,
`backend/app/services/planeacion_audit.py`. The write is injected at the SINGLE dispatch site in
`planeacion_asignaciones()` (`routers/planeacion_asignaciones.py:1570`), after a successful mutating
dispatch, gated on a `MUTATING_ACTIONS` map. Reads go through one new `listAuditoria` branch on the
SAME admin-gated dispatcher — no new route, no new client, no versioning. The dispatcher stays thin: a
declarative `accion → {entidad, id_extractor, resumen}` table in the new service carries all
per-action knowledge. Frontend adds one "Historial" sub-tab to `web/js/planeacion.js`.

This is the LIGHTER variant of `services/survey_cali.py`: append-only, no `_rev`, no
`history/{rev_NNNNNN}` subcollection, no before→after diff, no revert. The returned `resultado` (new
state) is the whole record.

## Architecture Decisions

### ADR-1: Best-effort audit — a logging failure NEVER breaks the mutation

**Choice**: The audit call is wrapped so any exception is caught and logged, never propagated. It runs
only AFTER the mutating dispatch returns its `JSONResponse` (i.e. the write already committed).
**Alternatives considered**: (a) audit inside the mutation's own Firestore batch (atomic); (b) let
audit exceptions bubble.
**Rationale**: This is the single most important correctness property. The operator's mutation already
committed; a bitácora write failing must never roll back or 500 a completed operation. Atomicity (a)
would couple every mutation function to the log and defeat the one-call-site goal; bubbling (b) turns a
cosmetic logging miss into a user-facing failure. A dropped audit row is an acceptable, logged loss.

### ADR-2: One call site via a captured response, not 30 edited branches

**Choice**: Extract the existing `if body.action == ...` chain into a local `_dispatch(...) ->
JSONResponse` (a mechanical move — the branch bodies already `return JSONResponse(...)`). The endpoint
then does: capture `resp = _dispatch(...)`, and if `body.action in MUTATING_ACTIONS`, call the
best-effort audit with the parsed `resp` body. `resultado` is read via `json.loads(resp.body)`.
**Alternatives considered**: edit each of ~24 mutating branches to also audit; audit inside each action
function.
**Rationale**: The proposal mandates ONE insertion, not edits scattered across the action functions.
Reading the already-built response body means the audit sees exactly what the client sees (already
`_jsonable`-normalized), with zero change to any action function.

### ADR-3: Per-action metadata table drives entidad/entidad_id/resumen

**Choice**: `MUTATING_ACTIONS` in `planeacion_audit.py` maps each mutating action to
`{entidad, id_extractor(params, resultado) -> str|None, resumen(params, resultado) -> str}`. Result
id shapes vary (`{id}`, `{grupo_id}`, `{vehiculo_id}`, `{punto:{id}}`, bulk with no id), so a callable
extractor per action is the honest fit; bulk ops return `None` id and put counts in `resumen`.
**Alternatives considered**: derive id generically from the result by probing common keys; store a diff.
**Rationale**: A generic probe silently mislabels the varied shapes. A small declarative table keeps
the dispatcher thin, makes "which actions are audited" one reviewable list, and makes adding a future
action a one-row change — the mitigation the proposal's top risk names.

### ADR-4: `listAuditoria` pagination — ts-inequality cursor, not offset or start_after

**Choice**: One query: optional `where("entidad","==",...)`, optional `where("actor_uid","==",...)`,
optional `where("ts",">=",desde)` / `where("ts","<",antes_de)` cursor, `order_by("ts", DESCENDING)`,
`.limit(page_size + 1)`. `page_size` default **50**. The cursor is the `ts` of the last row returned
(passed back as `antes_de`); the `+1` fetch reports `hay_mas` without a count query — the same trick
`list_puntos` uses with `LIMIT_MAX + 1`.
**Alternatives considered**: Firestore `offset` (billed per skipped doc, discouraged);
`start_after(snapshot)` cursor (the repo's in-memory fake test double exercises `where`/`order_by`/
`limit` but not `start_after`, so it would be untestable here); Python-side filtering of a bounded read
(breaks correct pagination once filtered).
**Rationale**: A `ts <` cursor is native, testable against the existing fake, and pairs with the
`order_by("ts")` for free (no extra index for the cursor itself). At ~a dozen writes/day, distinct
SERVER_TIMESTAMP nanos make same-ts collisions negligible.
`ponytail: ts-cursor, dup/skip risk on identical ts; upgrade to (ts, doc_id) start_after if write volume ever spikes.`

## Data Flow

    admin UI (Historial sub-tab)
        │  POST {action:'listAuditoria', tipo?, usuario?, desde?, antes_de?}
        ▼
    planeacion_asignaciones()  ──(read)──►  planeacion_auditoria  (order_by ts desc, limit N+1)
        ▲
        │  mutating action (crearGrupo, editarVehiculo, reopen, …)
        │  1. _dispatch() commits the mutation, returns JSONResponse
        │  2. action ∈ MUTATING_ACTIONS →
        └─ planeacion_audit.registrar(...)  ──(append)──►  planeacion_auditoria   [best-effort, swallowed]

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `backend/app/services/planeacion_audit.py` | Create | `registrar(...)`, `PLANEACION_AUDITORIA_COLLECTION`, `MUTATING_ACTIONS` table, `list_auditoria(...)`, `_sanitize_params`, best-effort wrapper. Sole writer. |
| `backend/app/routers/planeacion_asignaciones.py` | Modify | Extract chain into `_dispatch()`; add capture + best-effort audit at the single site; add `listAuditoria` read branch; add `tipo`/`usuario`/`desde`/`antes_de` request fields. |
| `backend/tests/invariants/test_sole_writer.py` | Modify | New `PLANEACION_AUDITORIA_COLLECTION` literal + `ALLOWED_MODULES_PLANEACION_AUDITORIA = {planeacion_audit.py}` + test. |
| `web/js/planeacion.js` | Modify | "Historial" sub-tab button + panel + `renderHistorialSection()` (feed + filter selects), lazy-loaded on first switch. |
| `web/js/planeacion.test.mjs` | Modify | One test: feed render + filters. |

## Interfaces / Contracts

`planeacion_auditoria` doc shape:

    { actor_uid, actor_email, accion, entidad, entidad_id, params, resultado, resumen, ts }
    # entidad ∈ grupo | vehiculo | conductor | asignacion | cuadrilla
    # ts = firestore.SERVER_TIMESTAMP ; written via collection(...).document().set(doc) (fake-double-friendly, not .add())

`registrar` signature (best-effort caller wraps it):

    def registrar(db, *, actor_uid, actor_email, accion, params, resultado) -> None:
        meta = MUTATING_ACTIONS[accion]
        doc = { "actor_uid": actor_uid, "actor_email": actor_email, "accion": accion,
                "entidad": meta.entidad, "entidad_id": meta.id_extractor(params, resultado),
                "params": _sanitize_params(params), "resultado": resultado,
                "resumen": meta.resumen(params, resultado), "ts": SERVER_TIMESTAMP }
        db.collection(PLANEACION_AUDITORIA_COLLECTION).document().set(doc)

    def registrar_best_effort(db, *, ...) -> None:   # called at the dispatch site
        try: registrar(db, ...)
        except Exception: logging.exception("planeacion_auditoria append failed for %s", accion)

`_sanitize_params`: drop `action`, drop `_UNSET` sentinels and `None` values (payload is
`body.model_dump()` with every model field defaulted) to keep the doc lean. Actor comes from
`claims.get("sub")` / `claims.get("email")` (already resolved by `require_role("admin")`).

`MUTATING_ACTIONS` coverage (entidad · id source):

- grupo: crearGrupo/editarGrupo/eliminarGrupo (`id`), asignarGrupoAPuntos/desasignarGrupo (`grupo_id`/params, bulk)
- vehiculo: crearVehiculo/editarVehiculo/eliminarVehiculo (`id`), asignarVehiculoAGrupo/desasignarVehiculo (`vehiculo_id`/`grupo_id`)
- conductor: crearConductor/editarConductor/eliminarConductor (`id`)
- cuadrilla: crearCuadrilla/editarCuadrilla/eliminarCuadrilla (`id`), asignarInspector/desasignarInspector (`cuadrilla_id`), autoAgrupar/reiniciarAgrupacion (bulk, id=None)
- asignacion: editarAsignacion/marcarNoAplica/reopen (`punto.id`), reasignarPunto (`id`)

NOT audited (read-only): listPuntos, resumen, listCuadrillas, getEnlaceSurvey, listGrupos,
listVehiculos, listConductores, metricasProgreso, **listAuditoria**.

`resumen` examples (neutral Spanish, infinitive, per language contract): "Crear grupo «Norte»",
"Editar vehículo ABC123", "Reabrir punto a pendiente", "Agrupar automáticamente 12 cuadrillas".

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `registrar` builds correct doc; `_sanitize_params` strips `action`/`_UNSET`/`None`; each `id_extractor`/`resumen` | fake Firestore double, table-driven |
| Unit | Best-effort: a `registrar` that raises does NOT propagate; mutation response unchanged | monkeypatch `registrar` to raise, assert 200 + logged |
| Unit | Read-only action writes zero audit docs; a mutating action writes exactly one | assert collection size before/after |
| Integration | `listAuditoria` newest-first, `tipo`/`usuario`/date filters, `hay_mas` + `antes_de` cursor, 403 for non-admin | dispatcher test with seeded docs |
| Invariant | `planeacion_auditoria` has exactly one writer | `test_sole_writer.py` new literal + allowlist |
| E2E (frontend) | "Historial" feed renders + filters | `planeacion.test.mjs` |

## Threat Matrix

N/A — no routing change, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. This is one Firestore append + one read on an already-admin-gated
in-process dispatcher.

## Migration / Rollout

No migration. Additive collection, read by nothing else. Rollback = deploy/config revert per the
proposal's Rollback Plan (frontend, read branch, hook, or the whole collection — each independently
reversible).

**Operator step (flag for tasks):** `listAuditoria`'s server-side filters need Firestore composite
indexes on `planeacion_auditoria`:
- `(entidad ASC, ts DESC)` — when filtering by tipo
- `(actor_uid ASC, ts DESC)` — when filtering by usuario
- `(entidad ASC, actor_uid ASC, ts DESC)` — only if tipo AND usuario are combined in one query

Date range + order both live on `ts`, so they need no extra index. Firestore emits the exact
index-creation link on the first failing query — creating them is a console click, not code.

## Open Questions

- [ ] Should the v1 UI allow tipo AND usuario simultaneously (forces the 3-field index) or restrict to
      one categorical filter at a time (two indexes)? Default: allow both, flag the third index. Trivial
      to constrain in the frontend if the operator prefers fewer indexes.
