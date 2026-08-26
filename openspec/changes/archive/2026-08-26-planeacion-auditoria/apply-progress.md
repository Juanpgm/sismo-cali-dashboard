# Apply progress: `planeacion-auditoria`

Change: `planeacion-auditoria` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-apply

Delivery per user instruction: ONE apply run, TWO logical commits.
- Commit 1 = write path = Phases 1 + 2 + 4.
- Commit 2 = read path + UI = Phases 3 + 5 + 6 (+ Phase M.1 operator note in the commit body).

Strict TDD active throughout: RED confirmed failing before every GREEN implementation.

## Baseline

`python -m pytest backend/tests/ -q` before this change: **529 passed** (re-verified live per task
1.1 — supersedes the `planeacion-asignaciones` tasks.md's stale "391" figure).

## Commit 1 — write path (Phases 1, 2, 4)

- New `backend/app/services/planeacion_audit.py`: `PLANEACION_AUDITORIA_COLLECTION`,
  `MUTATING_ACTIONS` (all 24 actions literally named in the spec — the spec's own prose says "23"
  but its list has 24 entries; implemented every named action, not a scope decision), `registrar`,
  `registrar_best_effort`, `_sanitize_params`.
- New `backend/tests/services/test_planeacion_audit.py` — 14 tests, all green.
- `backend/app/routers/planeacion_asignaciones.py`: mechanical extraction of the existing
  `if body.action == ...` chain into `_dispatch()`, plus the single post-mutation audit hook in
  `planeacion_asignaciones()` (best-effort, after `_dispatch()` returns, gated on
  `body.action in MUTATING_ACTIONS`).
- `backend/tests/routers/test_planeacion_asignaciones.py`: 3 new hook tests + `PLANEACION_AUDITORIA`
  added to `_stores()`. Full module regression re-run after the extraction: 133/133 green — zero
  behavior change to any existing action.
- `backend/tests/invariants/test_sole_writer.py`: new `ALLOWED_MODULES_PLANEACION_AUDITORIA` +
  `test_planeacion_auditoria_literal_is_used_by_an_allowlisted_module`. Scratch-probe sanity check
  performed and reverted (see tasks.md task 4.1 STATUS note).

**Issues found (both resolved, neither touches the `planeacion_auditoria` literal itself):**
1. The module docstring's plain-English mention of the survey campaign's own versioned-history
   service module tripped `ALLOWED_MODULES_SURVEY_CALI`'s `survey_cali` whole-identifier scan.
   Resolved by paraphrasing the docstring (same "honest reword, not obfuscation" precedent
   `services/__init__.py`'s own entry in that allowlist already uses for a doc-only mention).
2. `MUTATING_ACTIONS`' `autoAgrupar`/`reiniciarAgrupacion` resumen builders read the `cuadrillas`
   JSON key off `autoAgrupar`'s own resultado dict and use the plain word "cuadrillas" in a
   human-readable string. This tripped the STICKER campaign's CLOSED `cuadrillas` allowlist.
   Resolved by adding `planeacion_audit.py` to that allowlist with the SAME annotation
   (`routers/planeacion_asignaciones.py` was already there for the identical JSON-key-only reason) —
   not by reopening or weakening the CLOSED set's semantics.

Backend suite after Commit 1: **547 passed** (529 baseline + 14 audit service + 3 hook + 1 invariant).

## Commit 2 — read path + UI (Phases 3, 5, 6)

- `backend/tests/routers/test_planeacion_asignaciones.py`: `_FakeQuery.where` extended with `>=`/`<`
  support; `"listAuditoria"` added to the non-admin rejection parametrize list; 5 new `listAuditoria`
  scenario tests (no filters/newest-first, tipo, usuario, date range, pagination + cursor).
- `backend/app/services/planeacion_audit.py`: `list_auditoria(...)` (ADR-4 — ts-inequality cursor,
  `page_size + 1` fetch, `hay_mas`/`antes_de` idiom, `PAGE_SIZE_DEFAULT = 50`) + a small local
  `_jsonable`/`_doc_to_dict` pair (duplicated from the router's own, not imported — the router
  already imports this module at module level, so the reverse import would be circular).
- `backend/app/routers/planeacion_asignaciones.py`: `listAuditoria` branch in `_dispatch()`; new
  `usuario`/`desde`/`antes_de` Pydantic fields (`tipo` reused verbatim from the existing vehiculo
  field — no new field needed, no collision); a `_positive_int` page-size helper (kept separate from
  `_clamp_limit`, whose defaults belong to `listPuntos`, not the bitácora).
- `web/js/planeacion.js` / `web/js/planeacion.test.mjs`: new "Historial" sub-tab (4th, sibling to
  Puntos/Grupos/Vehículos), `buildHistorialRows`/`buildHistorialFiltro` pure helpers (RED-tested
  first), `renderHistorialSection`/`loadHistorial`, filter selects (tipo/usuario/fecha) + a
  "Ver más" pagination button using the `hay_mas`/`antes_de` cursor. Lazy-loaded on first switch to
  the sub-tab only (`historialLoaded` guard), NOT on every `initPlaneacion` — the deliberate
  difference from Grupos/Vehículos (eagerly fetched by `reload()`) design.md's File Changes note
  calls for.

**Verification (Phase 6):** backend 553/553 passing; frontend `planeacion.test.mjs` 7/7 (one
pre-existing, unrelated `evaluaciones.test.mjs` failure, reproduced on `main` before this change);
`rg -n "planeacion_auditoria" backend/app/` — 5 hits, all inside `planeacion_audit.py`; no
update/delete action against the collection exists (confirmed by inspecting every
`planeacion_audit.*` call site in the router).

**Phase M.1 (manual, no repo diff):** carried as a "Follow-up (operator):" note in Commit 2's own
commit message — 3 Firestore composite indexes needed on `sismo-agosto-sgred` for `listAuditoria`'s
filtered queries (`entidad+ts`, `actor_uid+ts`, `entidad+actor_uid+ts`). Not performed by this agent
(console-only step); the in-memory test double needs no index and passes without it.
