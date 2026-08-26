# Verify report: `planeacion-auditoria`

Change: `planeacion-auditoria` - Project: seismic_disaster_data_analisys_cali - Phase: sdd-verify
Commits validated: `2f7e953` (write path - Phases 1+2+4), `5fd1de2` (read + Historial - Phases 3+5+6).

## Verdict

APPROVED. 0 CRITICAL, 0 WARNING, 1 SUGGESTION (documentation-only, non-blocking).

## Test suites (run independently, not re-quoted from apply-progress.md)

- `python -m pytest backend/tests/ -q` -> **553 passed** (matches apply-progress.md's claimed count).
- `python -m pytest backend/tests/routers/test_planeacion_asignaciones.py -q` -> 139 passed (regression module, includes the 3 hook tests + 5 listAuditoria tests + 1 non-admin-403 parametrize case, no failures).
- `python -m pytest backend/tests/services/test_planeacion_audit.py tests/invariants/test_sole_writer.py -q` -> 25 passed.
- `node --test "js/**/*.test.mjs"` (from web/) -> 6/7 pass; planeacion.test.mjs passes (includes the Historial block). The one failure, evaluaciones.test.mjs (ERR_UNSUPPORTED_ESM_URL_SCHEME), was reproduced identically by checking out that file at the parent commit (2f7e953^) and re-running - pre-existing environment issue, unrelated to this change, not introduced by it. Confirmed independently, not just re-quoted.

## Requirement-by-requirement verification (file:line citations, independently read)

1. Append-only write on successful mutation - backend/app/services/planeacion_audit.py:59-189 (MUTATING_ACTIONS table, 24 entries, one entidad/id_extractor/resumen entry per action) + :192-218 (registrar writes exactly one doc via .document().set(doc), all 9 required fields present) + hook site backend/app/routers/planeacion_asignaciones.py:1696-1707 (audit call gated on body.action in MUTATING_ACTIONS). Cross-checked every branch of _dispatch() (lines 1602-1679) against MUTATING_ACTIONS: all 24 mutating branches are covered, all 9 read-only branches (listPuntos, resumen, listCuadrillas, getEnlaceSurvey, listGrupos, listVehiculos, listConductores, metricasProgreso, listAuditoria) are correctly excluded. Verified by test: test_registrar_writes_one_doc_with_required_fields (5 entidad shapes) + test_mutating_action_leaves_one_auditoria_doc_with_actor + test_read_only_action_leaves_zero_auditoria_docs.

2. Best-effort logging (critical property) - registrar_best_effort at planeacion_audit.py:221-241 wraps registrar(...) in try/except Exception: logging.exception(...); never propagates. Dispatch-site hook at planeacion_asignaciones.py:1694-1708: resp = _dispatch(...) is computed and returned unconditionally; the audit call happens strictly after and its result/exception never touches resp. Independently confirmed a test genuinely injects a raise and asserts the mutation response is unchanged: test_audit_write_failure_does_not_alter_the_mutation_response (backend/tests/routers/test_planeacion_asignaciones.py:1992-2008) monkeypatches planeacion_audit.registrar to raise RuntimeError, then asserts resp.status_code == 201, resp.json()["ok"] is True, "id" in resp.json(), and that the audit store stays empty. Ran this test in isolation - passes. Also test_registrar_best_effort_swallows_and_logs_exception (service-level) confirms the exception is logged via caplog.

3. Sole-writer invariant - Ran rg -n "planeacion_auditoria" backend/app/ myself: 5 hits, ALL inside backend/app/services/planeacion_audit.py (module docstring x2, the PLANEACION_AUDITORIA_COLLECTION constant, registrar's docstring, the logging.exception message string). Zero hits elsewhere under backend/app/. test_sole_writer.py:402-411 (ALLOWED_MODULES_PLANEACION_AUDITORIA = {services/planeacion_audit.py}, test_planeacion_auditoria_literal_is_used_by_an_allowlisted_module) runs the identical scan and asserts no unexpected hits - passes.

4. listAuditoria - list_auditoria(...) at planeacion_audit.py:252-285 (ts-desc order_by, optional tipo/usuario/desde/antes_de filters, page_size+1 fetch + hay_mas/antes_de cursor). Dispatcher branch at planeacion_asignaciones.py:1666-1678. Each scenario has a passing test in test_planeacion_asignaciones.py: newest-first (:1915-1926), tipo filter (:1929-1940), usuario filter (:1943-1954), date range (:1957-1969), pagination + cursor (:1972-1989), and non-admin 403 (listAuditoria added to the test_non_admin_is_rejected_no_mutation parametrize list at :314). All ran green in this session.

5. No regression - test_planeacion_asignaciones.py full module: 139 passed, 0 failed, in this independent run. The _dispatch() extraction (lines 1591-1683) is a verbatim relocation of the pre-existing if body.action == ... chain (no branch body edited - confirmed by reading the whole block).

6. Immutability - every planeacion_audit.* call site in the router is one of: the PAGE_SIZE_DEFAULT constant read, list_auditoria (read), the MUTATING_ACTIONS membership check, registrar_best_effort (append). No update/delete call exists anywhere.

7. "Historial" sub-tab - web/js/planeacion.js: 4th sub-tab button/panel (:258, :342), tipo/usuario/fecha filter selects (:348-363), renderHistorialSection/loadHistorial (:1711-1739) wired to change listeners on the three filters and a "Ver mas" pagination button, lazy-loaded via a historialLoaded guard fired only on first switch to the sub-tab (:963-987) - distinct from the eager Grupos/Vehiculos load, matching design.md's File Changes note. buildHistorialRows/buildHistorialFiltro pure helpers (:199-220) are unit-tested in planeacion.test.mjs:142-167; ran and confirmed passing.

## Risks flagged by the apply agent - checked

- "23 vs 24 MUTATING_ACTIONS" prose discrepancy: confirmed this is a tasks.md-only miscount (task 1.2's own prose says "23 actions" in one place, "24" in the STATUS note) - the actual spec.md requirement text never states a count, it only lists 24 literal action names, and all 24 are implemented. Independently counted the MUTATING_ACTIONS dict: 24 keys, matching the spec's literal list exactly, and matching every mutating branch in _dispatch() with none missed and none extra.
- Two sole-writer scan collisions: verified both against the actual diff of commit 2f7e953 (backend/tests/invariants/test_sole_writer.py). (1) The survey_cali docstring collision was avoided by paraphrasing from the start - the shipped docstring reads "the survey campaign's own versioned-history service module", never naming the literal identifier; confirmed via git log -p that no earlier literal-naming version was ever committed and reworded post-hoc. (2) The cuadrillas allowlist collision is resolved by adding services/planeacion_audit.py to ALLOWED_MODULES_CUADRILLAS with the same "JSON-key-only, not a collection reference" annotation already used for planeacion_asignaciones.py; read the annotation and the code (autoAgrupar's resumen builder reads the cuadrillas key off its own resultado dict, planeacion_audit.py:147); this is a legitimate reuse of an existing precedent, not scope creep or obfuscation of the CLOSED allowlist's semantics.
- Phase M.1 (Firestore composite indexes): confirmed left as unchecked in tasks.md, carried only as a "Follow-up (operator):" note in commit 5fd1de2's message body. No firestore.indexes.json or any index-config file exists in the repo, and no code diff touches indexes - the manual step was not faked as completed.

## Non-blocking observation (SUGGESTION, not a blocker)

- The spec's own Requirement text lists MUTATING_ACTIONS items and correctly has 24 entries with no explicit count claim - the "23 vs 24" confusion lives entirely in tasks.md prose. This is cosmetic (a stale count in a planning artifact, already superseded by its own STATUS note) and does not affect the shipped code, which correctly implements all 24 named actions. No action needed before archive.

## Artifact

Written to: openspec/changes/planeacion-auditoria/verify-report.md
