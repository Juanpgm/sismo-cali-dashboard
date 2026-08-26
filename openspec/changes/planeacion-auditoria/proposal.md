# Proposal: Planeación — bitácora de auditoría append-only

Change: `planeacion-auditoria` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-propose

## Why

The Planeación area now mutates four kinds of state through one admin endpoint — groups of
inspectors (`grupos_inspectores`), vehicles (`vehiculos`), drivers (`conductores`), and assignments
(`planeacion_cuadrillas` / `planeacion_puntos`). Roughly a dozen mutating actions rewrite this state
daily, and today **none of it leaves a trace**: once a group is edited, a vehicle re-assigned, or a
point reopened, there is no record of *who* did it, *when*, or *what changed*. The operations lead
asked to "monitorear y tener control de todo" — to see, after the fact, every change made in the
tab.

The pieces to answer that already exist next door. The router already runs under
`require_role("admin")` and already resolves the actor from `claims`. `services/survey_cali.py`
already proves an append-only history pattern in this codebase — but its full versioned
`history/{rev_NNNNNN}` + revert model (per-field diff, `_rev` counters, transactional replace) is
more machine than a bitácora needs. This change takes the *lighter* variant: an append-only log of
"who did what", built at the single dispatch site, read back through one admin action, shown as a
chronological feed.

## What Changes (v1, in scope)

- **New Firestore collection `planeacion_auditoria`**, append-only, one doc per mutating change:
  `{ actor_uid, actor_email, accion, entidad, entidad_id, params, resultado, resumen, ts }`.
  `entidad` is one of `grupo | vehiculo | conductor | asignacion | cuadrilla`.
- **New module `backend/app/services/planeacion_audit.py`** exposing a `registrar(...)`-style helper:
  it builds a human-readable `resumen` from an `accion → template` map and appends the doc. Sole
  writer of the new collection.
- **ONE hook point** — the audit write is injected at the SINGLE dispatch site, the
  `planeacion_asignaciones()` endpoint, AFTER a successful mutation, for a defined `MUTATING_ACTIONS`
  set only. Read-only actions (`listPuntos`, `resumen`, `listGrupos`, `listCuadrillas`,
  `getEnlaceSurvey`, `metricasProgreso`, `listVehiculos`, `listConductores`, the new `listAuditoria`
  itself, …) log nothing. This is deliberately one insertion, not edits scattered across ~25 action
  functions.
- **New read action `listAuditoria`** on the SAME `/planeacion-asignaciones` dispatcher (already
  admin-gated) — no new route. Paginated, newest-first, filters by `tipo` (entidad), `usuario`
  (actor), and date range.
- **New "Historial" sub-tab** in `web/js/planeacion.js`, sibling to Grupos / Vehículos /
  Asignaciones: a chronological feed of every change with filter selects (tipo, usuario, fecha),
  calling `listAuditoria` via `apiUrl('planeacionAsignaciones')`.

## Explicitly Out of Scope (YAGNI — Non-Goals)

- **No revert / rollback.** The log is for reading, not undoing. Restoring prior state is not built.
- **No per-entity versioned history with `_rev`.** No `history/{rev_NNNNNN}` subcollection, no
  per-field before→after diff, no `_rev` counters. This was the heavier `survey_cali` option the user
  explicitly declined — the returned `resultado` (new state) is enough for a bitácora.
- **No editing or deleting audit entries.** The log is immutable by design: `registrar` only appends;
  no action updates or removes an existing doc.
- **No audit for areas outside Planeación.** Stickers, inspector, and any other router are untouched.

## Altitude decision (explicit, settled)

Dispatcher-level logging captures **actor + accion + params + resultado**, not a per-field diff:

| Field | Source | Note |
|---|---|---|
| `actor_uid` | `claims.get("sub")` | already resolved by `require_role("admin")` |
| `actor_email` | `claims.get("email")` | for a readable feed without a roster lookup |
| `accion` | `body.action` | the dispatched action name |
| `entidad` / `entidad_id` | derived per action | one of grupo/vehiculo/conductor/asignacion/cuadrilla + its id |
| `params` | request body minus `action` | what was asked |
| `resultado` | the returned doc/summary | the NEW state — no before→after diff is kept |
| `resumen` | `accion → template` map | human-readable one-liner (neutral Spanish, infinitive) |
| `ts` | server time | ordering key, newest-first |

The returned `resultado` IS the new state; a bitácora does not need the old one. That is the
deliberate line between this change and `survey_cali`'s versioned model.

## Impact

New / touched surfaces:

- **New `backend/app/services/planeacion_audit.py`** — `registrar(...)` + the `accion → template` map.
- **`backend/app/routers/planeacion_asignaciones.py`** — one `MUTATING_ACTIONS` set, one audit call
  at the dispatch site after a successful mutation, and one new `listAuditoria` branch + read helper.
- **`backend/tests/invariants/test_sole_writer.py`** — new `planeacion_auditoria` literal + its own
  allowlist (only `planeacion_audit.py` writes it) + a test function, same discipline as the other
  Planeación collections.
- **`web/js/planeacion.js`** — new "Historial" sub-tab: feed render + filter selects.
- **`web/js/planeacion.test.mjs`** — one test for the feed render + filters.
- **Firestore console (`sismo-agosto-sgred`)** — one new collection; client rules deny all access
  (server/admin-SDK only), same posture as `planeacion_puntos` / `planeacion_cuadrillas`.

## Language contract

All artifacts, identifiers, and comments in **English**. UI-facing copy in **neutral Spanish,
infinitive form** ("Historial", "Filtrar", "Ver detalle"), never voseo — matching the project's
established UI-copy convention.

## Risks & Open Questions

| Risk | Likelihood | Mitigation |
|---|---|---|
| Hook at the dispatch site misses a new future mutating action | Med | `MUTATING_ACTIONS` is one explicit named set at the single site; adding an action is a one-line set entry, and the sole-writer test proves the collection has exactly one writer |
| `params` may carry large/sensitive payloads | Low | Planeación bodies are small (ids, names, uid lists); no secrets flow through this endpoint. If a field ever needs redacting it is a one-place change in `registrar` |
| Audit write failing could block a successful mutation | Med | The audit append runs AFTER the mutation succeeds; a logging failure must never roll back or 500 the mutation the operator already completed (define as best-effort in design/spec) |
| Feed unbounded as the log grows | Low | `listAuditoria` is paginated + newest-first + filterable from day one; no full-collection ship to the browser |

No open product decisions remain — scope, altitude, collection shape, and non-goals were settled with
the user before this proposal. Ready for spec + design.

## Rollback Plan

Every step is a deploy or config revert, never a data migration:

- **Frontend**: revert the `web/` commit (one sub-tab + its filters). The backend keeps logging;
  nothing else references the feed.
- **Read path**: remove the `listAuditoria` branch and redeploy. The collection keeps its data.
- **Hook**: remove the audit call + `MUTATING_ACTIONS` from the dispatcher and redeploy. Mutations
  behave exactly as before; the collection simply stops growing.
- **Data**: `planeacion_auditoria` is additive and read by nothing else. Deleting it is safe and only
  discards history — no assignment/vehicle/group/driver state depends on it.

## Rough size

One small work unit: one new service module, one dispatch-site hook + one read action, one sole-writer
allowlist + test, one frontend sub-tab + one test. Well under the 400-line single-PR budget. Delivery:
single PR.

## Capabilities

### New Capabilities
- `planeacion-auditoria`: append-only audit log ("bitácora") of every mutating Planeación change,
  written at the single dispatch site and read back through the admin-only `listAuditoria` action and
  the "Historial" sub-tab.

### Modified Capabilities
- None. The existing `planeacion-asignaciones` endpoint behavior is unchanged for every current
  action; this change only ADDS a post-mutation side effect and one new read action.

## Success Criteria

- [ ] A mutating action writes exactly ONE correct `planeacion_auditoria` doc (actor, accion,
      entidad, params, resultado, resumen, ts); a read-only action writes none.
- [ ] `listAuditoria` returns newest-first, filters correctly by `tipo` / `usuario` / date range, and
      is paginated; a non-admin caller gets 403.
- [ ] The "Historial" sub-tab renders the feed and its filters; the frontend test passes.
- [ ] `planeacion_auditoria` has exactly one writer, enforced by `test_sole_writer.py`.
