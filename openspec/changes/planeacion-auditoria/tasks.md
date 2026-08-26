# Tasks: Planeación — bitácora de auditoría append-only

Change: `planeacion-auditoria` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-tasks

Reads `proposal.md` (scope, non-goals, Rollback Plan, "Rough size"), `design.md` (4 ADRs +
Testing Strategy + the Operator step), and `specs/planeacion-auditoria/spec.md` (6 requirements).
Ordered, hierarchical, grouped by phase per `openspec/config.yaml`. Follows the
`fastapi-backend-consolidation` / `planeacion-asignaciones` tasks.md convention (checkbox style,
RED→GREEN pairs, `— Satisfies:` cross-references).

**Strict TDD is ACTIVE.** Backend test runner: `python -m pytest backend/tests/` (391 passing on
`main` before this change, per `planeacion-asignaciones` tasks.md's last recorded count — re-verify
the live baseline at Phase 1.1). Frontend test runner: `node --test "js/**/*.test.mjs"` run from
`web/`. Every non-trivial logic task has a RED task (failing test first, written from a spec
scenario) before its GREEN task.

**Delivery**: `ask-on-risk`. The proposal's own "Rough size" calls this "one small work unit... well
under the 400-line budget, single PR." This file's own line-count exercise (see the Review Workload
Forecast) does **not** confirm that — flagged explicitly there, not silently overridden.

---

## Phase 1 — `backend/app/services/planeacion_audit.py`: the write side

New module. Sole writer of `planeacion_auditoria`. No dependency on Phase 2+ (pure + a fake
Firestore double).

- [x] **1.1** (RED) Write `backend/tests/services/test_planeacion_audit.py` FIRST, against a fake
      Firestore double (reuse or trim the one in `tests/routers/test_planeacion_asignaciones.py` —
      only `.collection(name).document().set(doc)` is needed here, no query support). Cover, from the
      spec scenarios:
      - `registrar(...)` writes exactly one doc to `planeacion_auditoria` with `actor_uid`,
        `actor_email`, `accion`, `entidad`, `entidad_id`, `params`, `resultado`, `resumen`, `ts` all
        present, for a representative action from each `entidad` (`crearGrupo`→grupo,
        `crearVehiculo`→vehiculo, `crearConductor`→conductor, `editarAsignacion`→asignacion,
        `crearCuadrilla`→cuadrilla) plus one bulk action with no natural id
        (`autoAgrupar`/`reiniciarAgrupacion` → `entidad_id is None`).
      - `resumen` renders the exact neutral-Spanish-infinitive strings design.md gives as examples
        (`crearGrupo({"nombre":"Norte"})` → `"Crear grupo «Norte»"`, etc.) for at least 3 actions
        spanning different `id_extractor`/`resumen` shapes (single-id, bulk-count, rename/edit).
      - `_sanitize_params` drops the `action` key, drops Pydantic `_UNSET` sentinels, and drops
        `None` values, leaving only the fields the caller actually set.
      - Calling `registrar(...)` with an action NOT in `MUTATING_ACTIONS` raises (or is simply never
        called by contract — assert the module does not silently no-op an unknown action; pick one
        and assert it, since design.md's contract assumes the caller only ever passes a
        `MUTATING_ACTIONS` key).
      MUST fail (module does not exist yet).
      — Satisfies: *Requirement: Append-only write on successful mutation* (correct-document
      scenario). — STATUS: DONE. Baseline re-verified at 529 passing (not 391 — that count was from
      `planeacion-asignaciones`'s own last-recorded baseline) before this change. RED confirmed:
      `ImportError: cannot import name 'planeacion_audit' from 'app.services'`.

- [x] **1.2** (GREEN) Implement `backend/app/services/planeacion_audit.py`: `PLANEACION_AUDITORIA_COLLECTION`
      constant, the `MUTATING_ACTIONS` table (all 23 actions named in the spec: `crearGrupo`,
      `editarGrupo`, `eliminarGrupo`, `asignarGrupoAPuntos`, `desasignarGrupo`, `crearVehiculo`,
      `editarVehiculo`, `eliminarVehiculo`, `asignarVehiculoAGrupo`, `desasignarVehiculo`,
      `crearConductor`, `editarConductor`, `eliminarConductor`, `crearCuadrilla`, `editarCuadrilla`,
      `eliminarCuadrilla`, `autoAgrupar`, `reiniciarAgrupacion`, `asignarInspector`,
      `desasignarInspector`, `reasignarPunto`, `editarAsignacion`, `marcarNoAplica`, `reopen`), each
      mapped to `{entidad, id_extractor(params, resultado) -> str | None, resumen(params, resultado)
      -> str}` per design.md ADR-3, `_sanitize_params(params)`, and `registrar(db, *, actor_uid,
      actor_email, accion, params, resultado) -> None` (writes via
      `db.collection(...).document().set(doc)`, matching `survey_cali`/`cruce_sticker`'s
      fake-double-friendly write shape — never `.add()`). Run 1.1, confirm green.
      — Satisfies: *Requirement: Append-only write on successful mutation*. — STATUS: DONE. GREEN
      confirmed, 14/14 in `test_planeacion_audit.py` (all of 1.1's scenarios + 1.3/1.4's, written in
      the same pass). All 24 actions named in the spec's `MUTATING_ACTIONS` list implemented (the
      spec text says "23" but literally lists 24 — a pre-existing count discrepancy in the spec/tasks
      prose, not a scope decision made here; every named action is covered).

- [x] **1.3** (RED) Extend the test module: `registrar_best_effort(db, ...)` — when the wrapped
      `registrar` call raises any exception, `registrar_best_effort` does NOT propagate it (assert no
      exception escapes) and the exception is logged (assert via `caplog` or a monkeypatched
      `logging.exception` call). MUST fail (function does not exist yet).
      — Satisfies: *Requirement: A logging failure never alters a completed mutation* (audit write
      fails but caller sees no exception, at the service-function level). — STATUS: DONE, written and
      GREEN-confirmed together with 1.4 (both new; RED/GREEN run as one pass against the whole file).

- [x] **1.4** (GREEN) Implement `registrar_best_effort(db, *, actor_uid, actor_email, accion, params,
      resultado) -> None` per design.md's Interfaces/Contracts: `try: registrar(...) except Exception:
      logging.exception("planeacion_auditoria append failed for %s", accion)`. Run 1.3, confirm
      green.
      — Satisfies: *Requirement: A logging failure never alters a completed mutation*. — STATUS: DONE.
      GREEN confirmed.

---

## Phase 2 — Hook at the single dispatch site (`routers/planeacion_asignaciones.py`)

Depends on Phase 1 (`registrar_best_effort`, `MUTATING_ACTIONS`). Touches the existing ~68-line
`if body.action == ...` chain at `planeacion_asignaciones.py:1579-1642` — a mechanical extraction per
design.md ADR-2, not a rewrite of any action function.

- [x] **2.1** (RED) Extend `backend/tests/routers/test_planeacion_asignaciones.py` FIRST with the
      hook scenarios, using the existing fake Firestore double (add a `PLANEACION_AUDITORIA =
      "planeacion_auditoria"` store to `_stores()`):
      - A mutating action (e.g. `crearGrupo`) succeeds AND leaves exactly one
        `planeacion_auditoria` doc behind, carrying the calling admin's `actor_uid`/`actor_email`.
      - A read-only action (e.g. `listGrupos`) leaves zero `planeacion_auditoria` docs.
      - Monkeypatch `planeacion_audit.registrar` (or `registrar_best_effort`'s inner call) to raise;
        assert the mutating action's own HTTP response is unchanged (same status code, same body
        shape) and no exception surfaces to the client.
      MUST fail (no hook exists yet — zero docs are written for `crearGrupo` today, and the
      raise-injection has nothing to patch).
      — Satisfies: *Requirement: Append-only write on successful mutation* (both scenarios);
      *Requirement: A logging failure never alters a completed mutation* (both scenarios, at the
      HTTP boundary). — STATUS: DONE. RED confirmed: the "one doc left behind" scenario failed
      (`assert 0 == 1`, no hook existed yet); the read-only and raise-injection scenarios were
      trivially already true pre-hook (nothing to hook yet) and remain true post-hook.

- [x] **2.2** (GREEN) Extract the existing `if body.action == ...` chain (currently inline in
      `planeacion_asignaciones()`, lines ~1579-1642) into a local `_dispatch(body, payload, claims,
      db) -> JSONResponse` — a mechanical move only, no branch body changes. In
      `planeacion_asignaciones()`: call `resp = _dispatch(...)`; if `body.action in MUTATING_ACTIONS`,
      read `resultado = json.loads(resp.body)` and call
      `planeacion_audit.registrar_best_effort(db, actor_uid=claims.get("sub"),
      actor_email=claims.get("email"), accion=body.action, params=payload, resultado=resultado)`;
      return `resp` unchanged either way. Run 2.1, confirm green, THEN re-run the FULL existing
      dispatcher test module (regression guard, since the if-chain moved) — every prior test in
      `test_planeacion_asignaciones.py` must still pass unchanged.
      — Satisfies: *Requirement: Append-only write on successful mutation*; *Requirement: A logging
      failure never alters a completed mutation*. — STATUS: DONE. GREEN confirmed (2.1's 3 new
      scenarios pass); full regression re-run of `test_planeacion_asignaciones.py` afterward: 133/133
      passing (130 prior + 3 new), zero behavior change to any existing action.

---

## Phase 3 — `listAuditoria` read action

Depends on Phase 1/2 (reads the collection Phase 2 starts writing to). The read query itself lives in
`planeacion_audit.py` (per the sole-writer/sole-reader spec wording: the literal `planeacion_auditoria`
may appear ONLY inside that file — the router must call into it, never query the collection directly).

- [ ] **3.1** Extend the fake Firestore double in `test_planeacion_asignaciones.py`: `_FakeQuery.where`
      currently supports only `==`/`!=` (needed by every other action so far). Add `>=` and `<`
      support so the `ts`-cursor pagination (design.md ADR-4) is testable against this repo's
      existing fake-double convention, matching the precedent `planeacion-asignaciones` design.md set
      for extending this same double (`.order_by()`/`.limit()` were added there for the identical
      reason).
      — Satisfies: *Requirement: `listAuditoria` read action* (date-range and pagination scenarios,
      test-infrastructure prerequisite).

- [ ] **3.2** (RED) Extend `test_planeacion_asignaciones.py` FIRST with the `listAuditoria` scenarios,
      seeding `planeacion_auditoria` docs directly into the fake store:
      - No filters → results ordered by `ts` descending.
      - `tipo:'vehiculo'` → only `entidad:'vehiculo'` entries returned.
      - `usuario:'u9'` → only entries whose `actor_uid == 'u9'` returned.
      - `desde`/`hasta` (or design.md's actual field names, `desde`/`antes_de`) → only entries with
        `ts` inside the range returned.
      - A page-size limit → at most that many entries returned, with a `hay_mas`/cursor value that
        lets a second call fetch the next page (mirrors `listPuntos`'s `truncado`/`+1`-fetch idiom,
        design.md ADR-4).
      - Add `"listAuditoria"` to the existing parametrized non-admin rejection test
        (`test_non_admin_is_rejected_no_mutation`, line ~309) so the admin-only gate is proven for
        this action too, not assumed from the shared `Depends(require_role("admin"))`.
      MUST fail (`listAuditoria` is not a recognized action yet — 400 "Acción desconocida").
      — Satisfies: *Requirement: `listAuditoria` read action* (all 6 scenarios, including the
      non-admin-403 scenario).

- [ ] **3.3** (GREEN) Implement `list_auditoria(db, *, tipo=None, usuario=None, desde=None,
      antes_de=None, page_size=50)` in `planeacion_audit.py` per design.md ADR-4: optional
      `where("entidad","==",tipo)`, optional `where("actor_uid","==",usuario)`, optional
      `where("ts",">=",desde)` / `where("ts","<",antes_de)`, `order_by("ts", DESCENDING)`,
      `.limit(page_size + 1)`; `hay_mas = len(rows) > page_size`, trim to `page_size`, and return the
      last row's `ts` as the next-page cursor. Add a `listAuditoria` branch inside `_dispatch()`
      calling this function, and add `tipo: str | None`, `usuario: str | None`, `desde: Any = None`,
      `antes_de: Any = None` fields to `PlaneacionAsignacionesRequest`. Run 3.2, confirm green.
      — Satisfies: *Requirement: `listAuditoria` read action*.

---

## Phase 4 — Sole-writer invariant

Depends on Phase 1 (the literal must exist to be scanned). Mirrors the established
`ALLOWED_MODULES_VEHICULOS`/`ALLOWED_MODULES_CONDUCTORES` single-module pattern in
`test_sole_writer.py`.

- [x] **4.1** Extend `backend/tests/invariants/test_sole_writer.py`: add
      `ALLOWED_MODULES_PLANEACION_AUDITORIA = {APP_ROOT / "services" / "planeacion_audit.py"}` and
      `test_planeacion_auditoria_literal_is_used_by_an_allowlisted_module`, scanning for the raw
      literal `"planeacion_auditoria"` (unlike the `cuadrillas` case, this literal has no known
      substring collision with an existing scan, so no `_COLLECTION`-identifier workaround is
      needed — confirm this by running the new test once with the literal search before writing the
      docstring note). Sanity-check the scanner genuinely detects a violation: temporarily drop a
      scratch file containing the literal `planeacion_auditoria` under a non-allowlisted module (e.g.
      `backend/app/routers/`), confirm the test fails naming it, then delete the scratch file and
      reconfirm green — same discipline `planeacion-asignaciones` task 3.11 used for its own two
      invariant tests.
      — Satisfies: *Requirement: Sole-writer invariant*. — STATUS: DONE. The literal collided with
      TWO unrelated closed scans, NOT the one the task anticipated (`_COLLECTION`-identifier vs.
      `cuadrillas`): (1) the module docstring's plain-English mention of the survey campaign's own
      versioned-history service tripped `ALLOWED_MODULES_SURVEY_CALI`'s `survey_cali` scan — reworded
      to a paraphrase, same "honest reword, not obfuscation" resolution `services/__init__.py`'s own
      entry already used; (2) `MUTATING_ACTIONS`' `autoAgrupar`/`reiniciarAgrupacion` entries read the
      `cuadrillas` JSON key off `autoAgrupar`'s own resultado and use the plain word in a `resumen`
      string, tripping the STICKER campaign's CLOSED `cuadrillas` allowlist — resolved the same way
      `routers/planeacion_asignaciones.py` was already annotated there (JSON-key-only, not a
      collection reference), NOT by editing the CLOSED set's semantics. Neither collision involves
      the `planeacion_auditoria` literal itself, which has zero known collisions as anticipated —
      confirmed via the scratch-probe sanity check (dropped a scratch file under
      `backend/app/routers/` with the raw literal, confirmed the new test failed naming it, deleted
      it, reconfirmed green). Full suite: 547/547 passing.

---

## Phase 5 — Frontend: "Historial" sub-tab

Depends on Phase 3 (`listAuditoria` must exist to call). Sibling to the existing Puntos / Grupos /
Vehículos sub-tabs already in `web/js/planeacion.js` (the proposal's wording says "Grupos / Vehículos
/ Asignaciones" — the actual sub-tab set shipped by `planeacion-asignaciones` is Puntos / Grupos /
Vehículos; Historial becomes the 4th).

- [ ] **5.1** (RED) Extend `web/js/planeacion.test.mjs` FIRST (matches the existing file's
      script-style convention — plain asserts + a trailing `console.log`, not per-case `test()`
      blocks) with assertions against a not-yet-exported historial helper, e.g. `buildHistorialRows`
      or equivalent pure formatter (entry → display row) and a filter-params builder (tipo/usuario/
      fecha selects → the `{tipo, usuario, desde, antes_de}` request body). MUST fail (`node --test
      js/planeacion.test.mjs` errors — export does not exist).
      — Satisfies: *Requirement: "Historial" sub-tab renders the feed and its filters*.

- [ ] **5.2** (GREEN) Add the "Historial" sub-tab to `web/js/planeacion.js`: a 4th
      `[data-subtab-btn="historial"]` button + `[data-subtab="historial"]` panel (matching the
      existing 3 sub-tabs' markup shape at lines ~222-227/228-304), filter `<select>`s for tipo/
      usuario/fecha, a `renderHistorialSection()` that calls `listAuditoria` via
      `apiUrl('planeacionAsignaciones')` (reusing `callApi`), and lazy-loads (fetches) only on first
      switch to this sub-tab — not on every `initPlaneacion` — matching design.md's File Changes note.
      UI copy in neutral Spanish infinitive ("Historial", "Filtrar por tipo", "Filtrar por usuario",
      "Filtrar por fecha"). Run 5.1, confirm green.
      — Satisfies: *Requirement: "Historial" sub-tab renders the feed and its filters*.

---

## Phase 6 — Verification

- [ ] **6.1** Run the full backend suite: `python -m pytest backend/tests/ -v`, all green (baseline +
      this change's new tests in `test_planeacion_audit.py`, the hook/`listAuditoria` additions to
      `test_planeacion_asignaciones.py`, and the new `test_sole_writer.py` test).
      — Satisfies: design.md Testing Strategy (Unit + Integration + Invariant rows).

- [ ] **6.2** Run the frontend suite: `node --test "js/**/*.test.mjs"` from `web/`, all green.
      — Satisfies: design.md Testing Strategy (E2E/frontend row).

- [ ] **6.3** Grep-verify no accidental second writer: `rg -n "planeacion_auditoria" backend/app/`
      returns hits ONLY inside `backend/app/services/planeacion_audit.py`. Record the result in this
      file.
      — Satisfies: *Requirement: Sole-writer invariant*; *Requirement: Audit entries are immutable*
      (no update/delete action exists — confirm by inspecting every branch inside `_dispatch()` for a
      `planeacion_auditoria` write other than `registrar`'s own `.document().set(...)` append).

---

## Manual operator task (no repo diff)

- [ ] **M.1** — MANUAL, OPERATOR-ONLY, no code change. In the Firestore console for
      `sismo-agosto-sgred`, create the composite indexes `listAuditoria`'s filters need on
      `planeacion_auditoria`:
      - `(entidad ASC, ts DESC)` — filtering by tipo alone.
      - `(actor_uid ASC, ts DESC)` — filtering by usuario alone.
      - `(entidad ASC, actor_uid ASC, ts DESC)` — filtering by tipo AND usuario together. **Default:
        the v1 UI allows both simultaneously** (design.md's Open Question, resolved to "allow both" by
        default), so this third index is required, not optional.
      Firestore emits the exact index-creation link on the first failing `listAuditoria` query in the
      Cloud console/logs once Phase 3 is deployed — creating the index is a console click, not a code
      change. Date-range filtering + `order_by("ts")` share the same field and need no extra index.
      — Satisfies: `design.md` "Migration / Rollout" operator step; unblocks Phase 3's filtered
      queries in production (the in-memory fake double in tests needs no index and will pass without
      this step).

---

## Review Workload Forecast

- **Estimated changed lines (rough, per file):**
  - `backend/app/services/planeacion_audit.py` (new — `MUTATING_ACTIONS` table for 23 actions,
    `registrar`, `registrar_best_effort`, `_sanitize_params`, `list_auditoria`): **~170-210 lines**.
  - `backend/tests/services/test_planeacion_audit.py` (new): **~160-200 lines**.
  - `backend/app/routers/planeacion_asignaciones.py` (modified): the ADR-2 extraction of the existing
    ~68-line `if body.action == ...` chain (lines 1579-1642) into a local `_dispatch()` is a
    line-for-line relocation — it shows as ~68 removed + ~70 re-added in the diff even though no
    branch body changes, plus genuinely new content (the `MUTATING_ACTIONS` gate + best-effort audit
    call ~10 lines, the `listAuditoria` branch ~6 lines, 4 new Pydantic request fields ~5 lines):
    **~155-190 lines total, of which ~140 is a mechanical no-op relocation**.
  - `backend/tests/routers/test_planeacion_asignaciones.py` (modified — hook tests, `listAuditoria`
    tests, the fake double's `>=`/`<` extension, one parametrize-list addition): **~150-205 lines**.
  - `backend/tests/invariants/test_sole_writer.py` (modified — one allowlist constant + one test +
    docstring note): **~18-25 lines**.
  - `web/js/planeacion.js` (modified — 4th sub-tab button/panel, `renderHistorialSection`, filter
    selects, lazy-load wiring): **~80-115 lines**.
  - `web/js/planeacion.test.mjs` (modified — one added assertion block): **~30-50 lines**.
  - **Total: roughly 763-995 authored lines** (of which ~140 is the mechanical dispatch-chain
    relocation, not new logic).

- **400-line budget risk: High.** The total is roughly 2-2.5x the single-PR budget, and even
  discounting the mechanical relocation entirely (~620-855 lines of genuine new/changed content) it
  still clears 400 by a wide margin. **This contradicts `proposal.md`'s "Rough size" claim** ("well
  under the 400-line budget, single PR") — flagged here rather than silently deferred to at apply
  time, per this change's `ask-on-risk` delivery strategy.

- **Chained PRs recommended: Yes**, split along the write/read boundary already implied by the
  spec's own requirement grouping:
  - **PR1 — write path**: Phase 1 (`planeacion_audit.py`'s `registrar`/`registrar_best_effort`
    without `list_auditoria`) + Phase 2 (the dispatch-site hook, including the mechanical
    extraction) + Phase 4 (sole-writer invariant). Estimated **~440-555 lines** — still somewhat
    over budget, almost entirely because of the mechanical `_dispatch()` relocation (~140 of it).
    This is a judgment call worth surfacing rather than deciding unilaterally: accept PR1 slightly
    over budget on the grounds that the relocation is low-review-risk (no branch body changes, easy
    to diff-verify with `git diff -M`), or split it further into 1a (service module alone, ~330-410
    lines) and 1b (hook wiring + invariant, ~175-215 lines) if the reviewer prefers every PR strictly
    under 400.
  - **PR2 — read path + UI**: Phase 3 (`list_auditoria` + the `listAuditoria` branch + its tests,
    including the fake-double `>=`/`<` extension) + Phase 5 (frontend "Historial" sub-tab) + Phase 6
    (verification). Estimated **~275-390 lines** — within budget.
  - Phase M.1 (manual operator step) attaches to PR2's description; it has no PR of its own.

- **Decision needed before/at apply:** Yes — one item: confirm whether PR1's ~440-555 lines
  (dominated by the mechanical, low-risk `_dispatch()` relocation) is acceptable as a single PR, or
  whether the reviewer wants the further 1a/1b split named above. Nothing else in this change carries
  an open product decision — scope, altitude, collection shape, and non-goals were already settled in
  `proposal.md`.
