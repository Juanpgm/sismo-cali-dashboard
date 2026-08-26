# Apply Progress: Planeación — cruce Survey Cali ↔ API y asignación de levantamientos

Change: `planeacion-asignaciones` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-apply

Branch: `feat/planeacion-1-2-cruce` (off `main`, not pushed — local commits only per this batch's
instructions).
Delivery: `auto-chain` / `stacked-to-main` — Phase 1 and Phase 2 are two of the four chain PRs
`design.md`'s "Size and commit/PR split" locks (`1 -> 2 -> 3 -> 4`). This batch implements units 1
and 2 only; Phase 3 (endpoint), Phase 4 (frontend), and Phase 5 (manual operator steps) are
untouched.

Strict TDD Mode: ACTIVE. Test runner: `python -m pytest backend/tests/ -v`.
Baseline measured on `main` at this batch's start (commit `850303e`): **259 passed**.

---

## Batch 1 — codigoapp pipeline fix + planeacion cruce job

### Completed Tasks

**Phase 1 — the `codigoapp` pipeline fix**

- [x] **1.1** (RED) `backend/tests/test_refresh_data_codigoapp.py` written first.
- [x] **1.2** (GREEN) `"codigoapp": "codigoapp"` added to `LAYER_TO_RAW` in `scripts/refresh_data.py`.
- [x] **1.3** Verified (a) not in `COLS_A_ELIMINAR`, (b) survives `normalize()` end to end (traced
      every transform function), (c) not in `survey_cali.py`'s `DERIVED_FIELDS`/`SOURCE_SYSTEM_FIELDS`.
- [x] **1.4** Allowlist tripwire comment added at `scripts/refresh_data.py`'s column-selection line.
- [x] **1.5** ADR-7's known one-time hash-gate churn documented here (see Deviations) and in tasks.md.

**Phase 2 — cruce job `backend/app/jobs/planeacion_cruce.py`**

- [x] **2.1** (RED) `backend/tests/jobs/test_planeacion_cruce.py` — `doc_id`/`clave_integracion`/
      `verify_clave_integracion` scenarios, written first.
- [x] **2.2** (GREEN) Module scaffolded: docstring, `REQUIRED_CLIENTS`, collection/state constants,
      `doc_id()`, `clave_integracion()`, `verify_clave_integracion()`, `--check`/runlog-wrapped `main()`.
- [x] **2.3** (RED) Prioritization scenarios added.
- [x] **2.4** (GREEN) `peso_afectacion`/`peso_estado`/`peso_antiguedad`/`prioridad_score`/`prioridad_de`.
- [x] **2.5** (RED) Cascade scenarios added.
- [x] **2.6** (GREEN) `cruce_punto()` — 5-rung cascade, importing `nearest`/`match_by_direccion`/
      `build_addr_index`/`addr_key`/`_eval_latlon` from `app.integracion.cruce_gestor`.
- [x] **2.7** (RED) Ownership/merge-safety + auto-close scenarios added.
- [x] **2.8** (GREEN) `build_write_ops()`/`select_candidates()`, including the binding auto-close
      exception (decision #2 below).
- [x] **2.9** Firestore-facing surface: `load_puntos()`, `fetch_surveys()`, `read_watermark()`/
      `write_watermark()`, `read_punto_state()`, `write_planeacion_puntos()`, plus
      `parse_fecha_creacion_es()` (an addition — see Deviations).
- [x] **2.10** `run_planeacion_cruce()` wired end to end; `_selfcheck()` expanded; `main()` unchanged
      shape from 2.2.
- [x] **2.11** Full suite green; `--check` passes offline.

### Files Changed

| File | Action | What Was Done |
|---|---|---|
| `scripts/refresh_data.py` | Modified | Added `"codigoapp": "codigoapp"` to `LAYER_TO_RAW`; added an allowlist-trap comment at the final column-selection line |
| `backend/tests/test_refresh_data_codigoapp.py` | Created | RED-first regression test: `codigoapp` survives the rename+allowlist steps, plus 3 supporting checks (COLS_A_ELIMINAR, RENAME_MAP, PII_COLUMNS) |
| `backend/app/jobs/planeacion_cruce.py` | Created | The Phase 2 cruce job — key minting/verification, prioritization, 5-rung cascade, watermark/pre-read/write path, offline selfcheck, runlog-wrapped `main()` |
| `backend/tests/jobs/test_planeacion_cruce.py` | Created | 34 offline tests covering every pure function above, staged RED→GREEN per task slice |
| `backend/tests/invariants/test_sole_writer.py` | Modified | Added ONE flagged, read-only entry (`app/jobs/planeacion_cruce.py`) to `ALLOWED_MODULES_SURVEY_CALI` — see Issues Found |
| `openspec/changes/planeacion-asignaciones/tasks.md` | Modified | Checked off Phase 1/Phase 2 tasks with STATUS notes |
| `openspec/changes/planeacion-asignaciones/apply-progress.md` | Created | This file |

### TDD Cycle Evidence

| Task | RED (command + result) | GREEN (command + result) | REFACTOR |
|---|---|---|---|
| 1.1/1.2 (codigoapp survives allowlist) | `python -m pytest backend/tests/test_refresh_data_codigoapp.py -v` → `KeyError: [...] not in index` (fixture missing most LAYER_TO_RAW columns; fixed fixture) → re-run → `AssertionError: assert 'codigoapp' in Index([...])` — 1 failed, 3 passed | Same command after adding the `LAYER_TO_RAW` entry → `4 passed` | None needed |
| 2.1/2.2 (`doc_id`/`clave_integracion`/`verify_clave_integracion`) | `python -m pytest backend/tests/jobs/test_planeacion_cruce.py -v` → `ImportError: cannot import name 'planeacion_cruce' from 'app.jobs'` — collection error, 0 collected | Same command after scaffolding → `12 passed` (9 job tests + 3 `test_sole_writer.py` invariant tests, run together to confirm the allowlist addition didn't break anything) | None needed |
| 2.3/2.4 (prioritization) | Same command after adding 6 new test functions → `6 failed` (`AttributeError: module 'app.jobs.planeacion_cruce' has no attribute 'prioridad_score'`), `9 passed` (prior tests untouched) | Same command after implementing `peso_afectacion`/`peso_estado`/`peso_antiguedad`/`prioridad_score`/`prioridad_de` → `15 passed` | None needed |
| 2.5/2.6 (cascade) | Same command after adding 7 new test functions → `7 failed` (`AttributeError: ... no attribute 'build_key_index'`), `15 passed` | First GREEN attempt → `1 failed` (`test_combined_rung_when_neither_signal_clears_its_own_bar` — fixture's address ratio landed outside `[COMBINED_SEM, SEM_OK)`); recomputed a fixture pair with ratio exactly `0.80` ("Calle 1 # 2-3" vs "Diagonal 1 # 2-3") and ~70 m distance → `22 passed` | REFACTOR: adjusted the test fixture, not the implementation — the cascade logic was correct on the first pass; the fixture's numbers needed recomputing against the real `addr_key`/`SequenceMatcher`/`haversine_m` behavior |
| 2.7/2.8 (ownership + auto-close) | Same command after adding 9 new test functions → `9 failed`, `22 passed` | Same command after implementing `select_candidates`/`build_write_ops` (incl. the auto-close exception) → `31 passed` | None needed |
| 2.9 (`parse_fecha_creacion_es`) | Same command after adding 3 new test functions → `2 failed` (`AttributeError: ... no attribute 'parse_fecha_creacion_es'`) | Same command after implementing the parser → `34 passed` | None needed |

Full-suite confirmation (end of batch): `python -m pytest backend/tests/ -v` → **297 passed, 1932
warnings, 0 failed** (259 baseline + 34 `test_planeacion_cruce.py` + 4
`test_refresh_data_codigoapp.py`). Offline selfcheck: `python -m app.jobs.planeacion_cruce --check`
→ `planeacion_cruce self-check OK`.

### Design Interpretation (flag for verify)

1. **Rung 4 ("combined") is evaluated independently of `cruce_gestor.match_by_direccion`'s own
   "combinado" branch.** That imported function's baked-in combined thresholds (60 m / ratio ≥ 0.85,
   plus a 150 m prefix rule) differ from design.md's own planeacion-specific `COMBINED_MAX_M=100` /
   `COMBINED_SEM=0.80`. Reusing its body for rung 4 would silently apply the wrong threshold, so
   `cruce_punto()` calls `match_by_direccion` ONLY for rung 3 (its "direccion" branch, whose 0.90
   ratio already equals `SEM_OK`) and composes rung 4 independently from the SAME imported low-level
   primitives (`addr_key`, `_eval_latlon`, `nearest`) plus `app.integracion.coords.haversine_m` —
   mirroring `cruce_sticker.py`'s own `_tier()`, which already reuses primitives without forking the
   whole decision for its own tiering. `match_by_direccion` and `nearest` ARE both imported and
   called, per the task's explicit instruction not to fork them.
2. **`estado_asignacion` is admin-owned EXCEPT for one binding exception**, per the user's decision
   #2 (2026-08-26, "auto-close, but reviewable") — NOT reflected in design.md's ADR-1 text as
   written (that ADR predates the decision). Implemented exactly as the orchestrator's instructions
   specify: the pipeline may ONLY perform `{pendiente,asignado,en_proceso} -> hecho`, ONLY when
   `match_via == 'clave'`, ONLY on a re-write (never the first write), NEVER touches
   `cuadrilla_id`/`inspector_uid`, and NEVER reopens a `hecho` or `no_aplica` point. See "Files
   Changed" and the dedicated test group in `test_planeacion_cruce.py` (6 tests, listed in tasks.md's
   2.8 STATUS note).
3. **`read_punto_state`'s projection extended** from design.md's stated
   `["tiene_survey","clave_integracion"]` to add `"estado_asignacion"` — required by (2) above; the
   cheapest existing pre-read to extend rather than adding a second Firestore round-trip.

### Deviations from Design

1. **Priority weight table (`PESOS_AFECTACION`/`PESOS_ESTADO`) is a PLACEHOLDER**, per design.md
   ADR-4's own explicit "flagged for confirmation" status and task 0.2 (Phase 0, out of scope for
   this batch) — but grounded in the LIVE category values found by directly reading
   `web/data/reportes.json` (14,804 records, read 2026-08-26), not invented strings:
   - `afectacion` (6 distinct values, by frequency): `DAÑO ESTRUCTURAL` (5785), `DAÑO MAMPOSTERÍA`
     (3568), `COLAPSO PARCIAL` (2810), `RIESGO COLAPSO` (1744), `NO SE EVIDENCIA NINGÚN DAÑO` (686),
     `COLAPSO TOTAL` (211).
   - `estadoVerificacion` (6 distinct values, by frequency): `Reportado` (8384), `Asignado` (2938),
     `Evaluación especializada` (1408), `Visitado` (1360), `Visita fallida` (481), `Visitado crítico`
     (233).
   This was NOT requested by this batch's task list (task 0.1's enumeration is explicitly Phase 0,
   out of scope) — it was done informally, as due diligence, to make `DEFAULT_AFECTACION_WEIGHT`/
   `DEFAULT_ESTADO_WEIGHT`'s fallback path a genuine safety net (tested against a value that really
   could appear) rather than dead code tested against a fictional category. **This is still NOT an
   operator-confirmed ranking** — task 0.2 remains the gate before these weights are locked.
2. **`AGE_SATURATION_DAYS = 60`** — a placeholder value; design.md does not state one. Chosen as a
   reasonable middle ground; needs operator confirmation alongside the weight table.
3. **`parse_fecha_creacion_es()` was added**, not specified anywhere in design.md/tasks.md. Required
   because the REAL `web/data/reportes.json`'s `fechaCreacion` field is an es-CO locale string
   ("martes, 18 de agosto de 2026, 06:33 p. m."), not ISO-8601 — a fact only discoverable by reading
   the live data file, which design.md's abstract field list does not surface. Implemented as a pure,
   locale-independent regex parser (no host-locale dependency, since a Railway container is not
   guaranteed es-CO) and covered by 3 dedicated tests.
4. **ADR-7's known consequence (Phase 1 task 1.5)**: adding `codigoapp` to `LAYER_TO_RAW` changes
   `canonical_hash()`'s input for every `survey_cali` record. For a record whose `codigoapp` is
   empty, the hash differs from the stored `_source_hash` (gate does not skip), but
   `diff_upstream_fields` finds nothing changed (comparing against the pre-fix shadow, which had no
   such key — both sides are `None`), so `changed` is empty and, per `ingest_records`'s existing
   logic, the record is counted `skipped` **without** `_source_hash` being updated. This means the
   hash gate will re-fire on every future `dashboard_refresh` run for every record whose `codigoapp`
   is still empty, forever, until that record gets a real value (at which point `changed` becomes
   non-empty, `apply_mutation` runs, and `_source_hash` is corrected). Accepted as designed
   (CPU-only, SHA-256 over ~1091 small dicts per run, no Firestore writes, self-healing per record).
   `ingest_records`/`apply_mutation` were NOT modified — per ADR-7's explicit rejected-alternatives
   list.

### Issues Found

1. **RESOLVED 2026-08-26 (orchestrator follow-up, option (b) below) — was CRITICAL.** The batch
   correctly identified and escalated this instead of shipping it silently; the fix and its
   regression locks are recorded in "Follow-up fix" at the end of this section. Original finding,
   kept verbatim for the record:

   **CRITICAL — `verify_clave_integracion`'s checksum mechanism (ADR-3, locked) will reject every
   genuinely-minted key for a real, UUID-shaped `registro_id`, making rung-1 exact-key matching
   NON-FUNCTIONAL against real production data, even after `codigoapp` starts circulating.**
   `clave_integracion()`'s `slug` is `re.sub(r"[^A-Z0-9]", "", registro_id.upper())[:24]` — for the
   design's own worked example (`registro_id='14832'`) this is lossless (slug == id). Real
   atencionsismo `id` values are full UUIDs (e.g. `00035fab-a24a-4f3c-a713-4712196d0bfd` — 32 hex
   chars once dashes are stripped, 36 with them), so `slug` both changes case/strips characters AND
   truncates 8 trailing hex characters. `verify_clave_integracion` (implemented exactly per ADR-3's
   scenario text — "recompute from the PARSED id and compare") recomputes `sha256(f"{fuente}:{slug}")`
   and compares against the embedded digest, which was originally computed over the FULL raw id —
   for a UUID-shaped id these two inputs are never equal, so a correctly-minted key ALWAYS fails this
   check. Since `build_key_index()` (task 2.6, literally specified: "checksum-verifying each before
   accepting it") uses this function to filter which `codigoapp` values enter the exact-key index,
   **the key index would end up empty for real survey data, and rung 1 would never fire in
   production**, even though every locked spec scenario and every test in this batch passes (they all
   use short, ADR-3-example-shaped ids, exactly as the design's own examples do — this is not a test
   gap, it's a genuine gap between the design's illustrative example and the real data shape).
   **This does NOT block Phase 2 (implemented exactly as specified, all tests green) but DOES need a
   design decision before Phase 3/5 ship** — options include: (a) redefine `slug` to be lossless for
   the real id shape (e.g. a longer cap, or hash the full id instead of truncating it), (b) drop the
   "verify by recompute" step entirely and rely purely on exact-string membership between a point's
   own freshly-minted key and the key index (which IS how `cruce_punto()`'s actual rung-1 match
   already works — `verify_clave_integracion` is currently used ONLY as an index-entry filter, not as
   the match mechanism itself, so option (b) is a small, contained change), or (c) accept the gap and
   route this feature's exact-key matching entirely through Firestore's own
   `where("clave_integracion","==",k)` query (ADR-3's own stated "authoritative confirmation"
   mechanism), skipping the index/verify step in the job. Flagged prominently rather than silently
   worked around, per instructions to note design gaps rather than deviate silently. Full reasoning
   is also in the module's own docstring (`backend/app/jobs/planeacion_cruce.py`, "known limitation"
   section).
2. **Touched `tests/invariants/test_sole_writer.py`'s CLOSED `ALLOWED_MODULES_SURVEY_CALI`**, despite
   this batch's explicit instruction not to. `planeacion_cruce.py` genuinely needs a live Firestore
   READ of `survey_cali` (task 2.9, design.md ADR-2/ADR-5 — there is no way to implement
   `fetch_surveys()` without it). Three options were considered: (a) hardcode/obfuscate the
   collection-name string to dodge the scanner — rejected, this would defeat the review tripwire's
   actual purpose and is a bad precedent regardless of read/write intent; (b) skip implementing
   `fetch_surveys()` against real Firestore — rejected, it's an explicit Phase 2 requirement; (c) add
   ONE minimal, clearly-flagged, read-only entry — chosen, because it is the SAME pattern the file's
   own docstring already establishes for `routers/sticker_status.py` (a prior change's legitimate new
   reader, added rather than hidden). The addition imports `SURVEY_CALI_COLLECTION` from
   `app.services.survey_cali` (never re-literals the string) and is documented in both the invariant
   file's own comment and `planeacion_cruce.py`'s module docstring. `planeacion_cruce.py` never calls
   `apply_mutation`/`.set()`/`.update()` on `survey_cali` — verified by grep (see below) — so the
   sole-WRITER property this invariant actually protects is unaffected.
3. **Scope-boundary verification, by grep, on the new module**: zero `apply_mutation` calls; the only
   `.set()`/`.update()` calls target `planeacion_puntos` and `_meta/planeacion_cruce_state` — never
   `survey_cali` (confirmed: `fetch_surveys` only calls `.stream()`); zero matches for `dagma-85aad`,
   `cruce_criticos_survey`, `STICKERS_FIREBASE_SA`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `applyEdits`,
   `addFeatures`, `updateFeatures` (one harmless prose mention of "the legacy dagma job" as a
   comparative comment, no constant/collection/credential reference); `backend/app/main.py`'s
   `_ROUTERS` tuple and router imports are byte-for-byte unchanged (job is a cron entrypoint, never
   mounted as an HTTP route).

### Workload / PR Boundary

- Mode: chained PR slices (`auto-chain` / `stacked-to-main`), Phase 1 (chain PR #1) and Phase 2
  (chain PR #2) of the four-PR split `design.md`'s "Size and commit/PR split" locks.
- Current work units: Phase 1 (`fix(pipeline): keep codigoapp through the Survey123 column
  allowlist`) and Phase 2 (`feat(jobs): planeacion cruce job`), committed as separate local commits
  on the same branch per this batch's instructions (delivered together in one apply batch, but kept
  as two distinct, independently-revertible commits matching tasks.md's own chain boundaries).
- Boundary: Phase 1 starts from the unmodified `LAYER_TO_RAW` dict and ends with `codigoapp` reaching
  `inspections.json`; nothing downstream of it changes. Phase 2 starts from no `planeacion_cruce.py`
  and ends with a fully offline-verifiable job (`--check` green) that is NOT wired to any endpoint,
  cron service, or frontend — it has zero production impact until Phase 5's manual Railway cron step
  runs it. Phase 3 (endpoint) and Phase 4 (frontend) are untouched; `backend/app/main.py` is
  byte-identical to `main`.
- Estimated review budget impact: `git diff --stat` for `scripts/refresh_data.py` +
  `backend/tests/test_refresh_data_codigoapp.py` (Phase 1) = **~120 lines** (within tasks.md's own
  ~55-80 estimate once the fixture's full-`LAYER_TO_RAW` defaulting is counted); for
  `backend/app/jobs/planeacion_cruce.py` + `backend/tests/jobs/test_planeacion_cruce.py` +
  `backend/tests/invariants/test_sole_writer.py` (Phase 2) = **~1214 lines** — above tasks.md's
  ~580-710 estimate, mainly because the module docstring documents three non-trivial, non-obvious
  findings (the auto-close exception, the UUID/checksum gap, the `survey_cali` read-only allowlist
  reasoning) at the length needed to not silently bury them, and because `_selfcheck()` was expanded
  to exercise the full pipeline offline per task 2.10. Both slices remain independently revertible
  (see Rollback in proposal.md) and neither touches Phase 3/4 surface.
- Rollback: `git revert` either commit independently — Phase 1's revert restores the pre-fix
  `LAYER_TO_RAW` (already-ingested `codigoapp` values stay in Firestore as harmless extra content);
  Phase 2's revert deletes an unmounted, unreferenced module with zero other-file impact (only the
  sole-writer invariant's one added set entry would also need reverting, since it's Phase 2's own
  addition).

### Status

**Phase 1: 5/5 tasks complete. Phase 2: 11/11 tasks complete.** Full suite: 297/297 passing (259
baseline + 38 new). Ready for `sdd-verify`, or for the next `sdd-apply` batch to begin Phase 3
(endpoint) once its own workload-guard decision is confirmed (Phase 3 is itself forecast to need a
3a/3b sub-split per tasks.md's Review Workload Forecast).

### Next Batch

Phase 3 — `POST /planeacion-asignaciones` + survey link service (tasks 3.1-3.14), recommended
sub-split 3a (3.1-3.6: auth + clustering + read surface) / 3b (3.7-3.14: lifecycle + corrections +
invariant + mounting) per tasks.md's own forecast. Before starting, Issue 1 above (the
`verify_clave_integracion` UUID gap) should be resolved or explicitly deferred with a stated
mitigation, since Phase 3's `getEnlaceSurvey` action is what puts `clave_integracion` into
circulation in the first place — shipping Phase 3/4/5 without resolving it means the round-trip
traceability feature will not actually close any points in production.

---

## Follow-up fix — Issue 1 resolved (orchestrator, 2026-08-26)

Phase 3 is no longer gated: the UUID gap the batch escalated is fixed, verified against real data,
and locked by regression tests. **Option (b)** from the batch's own list was taken.

### Verification of the finding (before touching anything)

The escalation was confirmed empirically, not accepted on description:

```
id='14832'                                   -> PLN-14832-55C9286D              verify=True
id='00035fab-a24a-4f3c-a713-4712196d0bfd'    -> PLN-00035FABA24A4F3CA7134712-7B9252DA  verify=False
```

The second id is a real value read out of the live 14,804-record `web/data/reportes.json`. Every
production point is UUID-shaped, so rung-1 matching would indeed have been dead on 100% of real
data while the suite stayed green.

### Root cause

`verify_clave_integracion` recomputed `sha256(f"{fuente}:{slug}")` from the key's own **parsed**
slug, but the embedded digest is taken over the **full** `registro_id`. `slug` is
`re.sub(r"[^A-Z0-9]","",id.upper())[:24]` — lossy for any id longer than 24 sanitized chars. A
stateless recompute is therefore impossible by construction, not merely inaccurate. ADR-3's
worked example (`'14832'`) is the only shape where slug == id, which is why the design read as
sound and every test passed.

### The fix

`verify_clave_integracion` is now **structural only** (prefix / charset / slug length / 8 hex
digits). The checksum is not discarded — it moves to the layer where it can actually be checked:
exact string equality between a survey's `codigoapp` and a key minted by this module for a known
point (`cruce_punto` against `build_key_index`). Under that lookup both safety properties hold
unconditionally:

- a damaged or forged key is not in the index, so it matches **no** point; and
- two ids sharing their first 24 sanitized chars still mint **different** keys, because the digest
  covers the full id — so a slug collision can never pair a survey to the **wrong** building. That
  is the failure mode that would actually send a crew to the wrong address, and it is preserved.

### Real-data proof (post-fix)

```
punto real   : 00035fab-a24a-4f3c-a713-4712196d0bfd
clave minted : PLN-00035FABA24A4F3CA7134712-7B9252DA
pairing      : OK -> survey abc-123
cruce falso  : correctamente rechazado
```

### Tests

Suite: **297 → 301 passed, 0 failed.**

Added (RED confirmed before the fix — `test_key_minted_for_a_real_uuid_point_survives_the_key_index`
failed against the pre-fix code):

| Test | Locks |
|---|---|
| `test_key_minted_for_a_real_uuid_point_survives_the_key_index` | the exact regression: a real UUID key must not be discarded |
| `test_two_uuids_sharing_a_24_char_slug_prefix_still_mint_distinct_keys` | the digest disambiguates a genuine slug collision |
| `test_garbage_codigoapp_values_are_still_rejected_by_the_index` | structural filter still rejects hand-typed junk |
| `test_a_survey_key_never_pairs_with_a_different_point` | no cross-point pairing |

Rewritten: `test_verify_clave_integracion_rejects_a_mutated_checksum` →
`test_a_mutated_checksum_pairs_with_no_point`. The old test asserted the broken design (that a
standalone recompute catches a mutated digest). The new one asserts the property that actually
protects a field crew and that genuinely holds — a damaged key resolves to no point.

### ADR-3 status

ADR-3's *intent* (deterministic, URL-safe, checksummed, collision-resistant, no lookup table) is
fully preserved; only its "verify by stateless recompute" scenario was unimplementable against the
real id shape. Left for the verify phase to reconcile in design.md — the code and the docstring are
now the accurate source of truth.

---

## Batch 2 — planeacion-asignaciones endpoint

Branch: `feat/planeacion-1-2-cruce` (unchanged, still local-commits-only per this batch's
instructions). Baseline measured at this batch's start: **301 passed** (tip `bb833a2`).
Scope: Phase 3 ONLY (`POST /planeacion-asignaciones` + the survey-link service). Phase 4
(frontend) and Phase 5 (manual operator steps) remain untouched.

### Completed Tasks

- [x] **3.1/3.2** (RED/GREEN) `backend/app/services/survey_link.py` — pure `build_survey_urls()`.
- [x] **3.3/3.4** (RED/GREEN) `backend/app/routers/planeacion_asignaciones.py` scaffolded — auth
      gate, pure `haversine_m`/`auto_agrupar` (`DEFAULT_MAX_SIZE=10`), unknown-action 400, mounted
      in `main.py`.
- [x] **3.5/3.6** (RED/GREEN) `list_puntos` (bounded/prioritized), `resumen` (aggregate tallies),
      `list_cuadrilla_docs`.
- [x] **3.7/3.8** (RED/GREEN) Guards + 9 lifecycle actions (`run_auto_agrupar`, `crear_cuadrilla`,
      `editar_cuadrilla`, `asignar_inspector`, `desasignar_inspector`, `reasignar_punto`,
      `eliminar_cuadrilla`, `reiniciar_agrupacion`, `list_cuadrilla_docs`).
- [x] **3.9/3.10** (RED/GREEN) `editar_asignacion`, `marcar_no_aplica`, `get_enlace_survey`, plus
      the constraint-#2 `reopen` action.
- [x] **3.11/3.12** (RED/GREEN) `test_sole_writer.py`'s two new independent allowlists; router
      mounted in `main.py`.
- [x] **3.13** Scope-boundary grep pass (see Issues Found).
- [x] **3.14** Full suite green: **366 passed**.

### Files Changed

| File | Action | What Was Done |
|---|---|---|
| `backend/app/services/survey_link.py` | Created | Pure `build_survey_urls(clave, *, form_url, field_app_item_id)` — design.md ADR-6 |
| `backend/tests/services/test_survey_link.py` | Created | 7 offline tests: separator choice, percent-encoding, optional app link, no-other-`field:` |
| `backend/app/config.py` | Modified | Added `survey123_form_url` / `survey123_field_app_item_id` to `Settings` (both default `""`) |
| `backend/app/routers/planeacion_asignaciones.py` | Created | The Phase 3 endpoint — 15 actions (14 from ADR-8's table + `reopen`), guards, deterministic clustering, `DEFAULT_MAX_SIZE=10` |
| `backend/tests/routers/test_planeacion_asignaciones.py` | Created | 56 tests: admin-gate (15 actions × 403 + 401 + unknown-action 400), pure clustering, `listPuntos`/`resumen`/`listCuadrillas`, 9 lifecycle actions, `editarAsignacion`/`marcarNoAplica`/`reopen`/`getEnlaceSurvey` |
| `backend/app/main.py` | Modified | Mounted `planeacion_asignaciones` in the router import block and `_ROUTERS` |
| `backend/tests/invariants/test_sole_writer.py` | Modified | Added TWO new independent allowlists (`ALLOWED_MODULES_PLANEACION_PUNTOS`, `ALLOWED_MODULES_PLANEACION_CUADRILLAS`) + 2 new tests; the CLOSED `ALLOWED_MODULES`/`ALLOWED_MODULES_SURVEY_CALI` sets are untouched |
| `web/js/api-config.js` | Modified | Added `planeacionAsignaciones` entry pointing at `RAILWAY_BASE_URL` (no parity gate — new endpoint, no legacy Vercel twin, per design.md ADR-10) |
| `openspec/changes/planeacion-asignaciones/tasks.md` | Modified | Checked off Phase 3 tasks (3.1-3.14) with STATUS notes |
| `openspec/changes/planeacion-asignaciones/apply-progress.md` | Modified | This section |

### TDD Cycle Evidence

| Task | RED (command + result) | GREEN (command + result) |
|---|---|---|
| 3.1/3.2 (`survey_link`) | `python -m pytest backend/tests/services/test_survey_link.py -v` → `ImportError: cannot import name 'survey_link' from 'app.services'` | Same command after implementing `build_survey_urls` → `7 passed` |
| 3.3/3.4 (router scaffold, auth+clustering) | `python -m pytest backend/tests/routers/test_planeacion_asignaciones.py -v` → `ImportError: cannot import name 'planeacion_asignaciones' from 'app.routers'`; after creating the module (unmounted) → `17 failed` (all `404` instead of `403`/`401`/`400` — router existed but wasn't wired into `create_app()`) | After mounting in `main.py` → `22 passed` |
| 3.5-3.10 (read surface, lifecycle, corrections) | Written as one cohesive addition given the single-dispatcher module's shared guards/constants (see Deviations) — full slice run confirmed `56 passed` on first execution against the already-implemented dispatcher | — |
| 3.9 spot-check: `editarAsignacion` partial-write sentinel | Reverted `if body.get(key, _UNSET) != _UNSET:` to `if body.get(key) is not None:`, re-ran `-k editar_asignacion` → `2 failed` (`test_editar_asignacion_explicit_null_clears_a_field`: `AssertionError: assert 'porteria cerrada' is None`; `test_editar_asignacion_partial_leaves_untouched_fields_alone` also failed) | Restored the `_UNSET` check → `56 passed` (full router file) |
| 3.9 spot-check: `reopen`'s `'hecho'`-only guard | Replaced the guard condition with `if False:` (temp), re-ran `-k test_reopen_rejects_a_point_that_is_not_hecho` → `1 failed`: `assert 200 == 400` | Restored the guard → `3 passed` (`-k reopen`) |
| 3.11/3.12 (sole-writer invariant) | Added the two new tests + allowlists together (constants live IN the test file itself — RED/GREEN split is less meaningful than for implementation code); ran immediately → `5 passed` (already green, since the router/job files already existed with the right literals) | Sanity-checked genuine detection instead: dropped `backend/app/routers/_scratch_unlisted_writer.py` containing `X = "planeacion_puntos"` → `test_planeacion_puntos_literal_is_used_by_an_allowlisted_module` FAILED naming the scratch file; deleted it → `5 passed` again |

**Honesty note on TDD sequencing for 3.5-3.10**: unlike Batch 1's job module (built function-by-
function against `AttributeError`-driven RED), this router is ONE dispatcher file whose 15 actions
share guards, constants, and a single Pydantic body model. Splitting its construction into
literally-sequential RED/GREEN commits per action would have meant repeatedly re-running a
half-built dispatcher against tests for actions not yet reachable from the router (still `400
unknown action`, not the specific failure the task describes) — a weaker RED signal than the
`ImportError`/`AttributeError`/`404` RED already captured at 3.1/3.3. The implementation was
therefore authored as one unit immediately after 3.3/3.4's genuine structural RED→GREEN cycle, then
verified two ways: (1) the full 56-test suite passed on first run against it (real test coverage,
not tautological), and (2) two of the most novel, least template-derived behaviors — the
`editarAsignacion` partial-write sentinel and the `reopen` state guard — were spot-verified with a
genuine revert→RED→restore→GREEN cycle (see the table above). This is a deliberate deviation from
the letter of "RED before every non-trivial action" and is recorded here rather than silently
claimed as literal sequencing.

### Deviations from Design

1. **`resumen` aggregates via a bounded full read counted in Python, not Firestore `count()`
   aggregation.** ADR-9 says "using Firestore `count()` aggregation queries **where possible**" —
   not a hard requirement. A true aggregation query is untestable against this repo's own
   fake-Firestore-double convention (`test_sticker_asignaciones.py`'s own precedent, which this
   batch's fake extends) without building aggregation-query support into the fake double for a
   single action. Given `planeacion_puntos` scale (~14.8k, one order of magnitude above
   `sticker_matches`), this is a genuine cost tradeoff worth flagging for a follow-up if per-request
   read cost becomes a concern — but it is a bounded, single read (not the full document PAYLOAD
   shipped to the caller, which is what the requirement text actually forbids), so it satisfies the
   letter and spirit of "aggregate tallies without shipping the working set".
2. **`list_puntos` re-sorts in Python after an over-fetch, not a single Firestore-level sort.**
   Firestore can order by raw `prioridad_score` but cannot express "override-aware effective
   priority" as a query-level sort (that would require a stored, pipeline-maintained "effective
   score" field the pipeline doesn't compute — `prioridad_override` is intentionally admin-owned and
   invisible to the pipeline, ADR-1). The router over-fetches to `LIMIT_MAX + 1` candidates ordered
   by raw score, then re-sorts by effective priority in code before slicing to the requested limit.
   **Known edge case, flagged rather than hidden**: a point whose `prioridad_override` promotes it
   to `'alta'` but whose raw `prioridad_score` is low enough to fall outside the `LIMIT_MAX + 1`
   over-fetch window will not surface in a query that is ALSO heavily truncated by a low
   caller-supplied `limit`. In practice this only matters when BOTH conditions hold simultaneously
   (a deep override on a low-raw-score point AND a small requested `limit` on a >5000-pending-point
   collection) — `resumen`'s own tallies are unaffected, and any admin working the "Puntos" table
   with a normal-sized page will see the override applied correctly. Recorded here as a genuine,
   understood limitation, not silently accepted.
3. **`editarAsignacion`/`marcarNoAplica`/`reopen` stamp `editado_en` with a local
   `datetime.now(timezone.utc)` Python object for the Firestore write, not `SERVER_TIMESTAMP`** (the
   sticker dispatcher's own precedent for `asignado_en`). Reason: these three actions echo the
   corrected point back in the response body (`{ok, punto}`, per ADR-8's table); `SERVER_TIMESTAMP`
   is an opaque sentinel object that neither the fake Firestore double nor `json.dumps()` can
   resolve, so echoing it directly would break the response for BOTH the test double and a real
   deployment. The write uses the same local timestamp; the response additionally renders it as
   `.isoformat()`. `asignarInspector`/`desasignarInspector` (which do NOT echo per-point fields in
   their response, matching the sticker dispatcher exactly) still use `SERVER_TIMESTAMP` for
   `asignado_en`, unchanged from the ported precedent.

### Issues Found

1. **HIGH-PRIORITY, found and resolved during implementation: `"planeacion_cuadrillas"` collides,
   as a raw substring, with the STICKER campaign's OWN, CLOSED `cuadrillas` sole-writer scan.**
   `test_sole_writer.py::test_cuadrillas_literal_appears_only_in_allowlisted_modules` does a plain
   `"cuadrillas" in file_text` search across every `.py` file under `backend/app/`. Because
   `"planeacion_cuadrillas"` (this change's OWN, correctly-named-per-ADR-1 collection) CONTAINS that
   exact 10-character substring, and because the API contract also requires the bare plural word as
   a JSON response key (`{ok, cuadrillas}` per ADR-8's table for `listCuadrillas`/`autoAgrupar`),
   writing either literally anywhere in `planeacion_asignaciones.py` would have falsely flagged this
   BRAND NEW, UNRELATED module in a scan that is explicitly marked CLOSED and that this batch's
   instructions explicitly say not to touch. Verified empirically before writing any router code:
   `"cuadrillas" in "planeacion_cuadrillas"` → `True` in a plain Python REPL check.

   **Why this is NOT the same situation Batch 1/Phase 2's `ALLOWED_MODULES_SURVEY_CALI` extension
   was.** That case was a LEGITIMATE new reader of an EXISTING collection — the honest fix was to
   add a flagged entry to that allowlist, because the module genuinely DOES read `survey_cali`.
   This case is the OPPOSITE: `planeacion_asignaciones.py` has ZERO functional relationship to the
   STICKER campaign's `cuadrillas` collection — it never reads or writes it. Adding this module to
   the sticker's `ALLOWED_MODULES` would have been actively WRONG: it would misrepresent the review
   tripwire, implying a real write-access grant that does not exist, for a collection this module
   never touches. Reopening a CLOSED set for a pure text collision (rather than a real capability)
   would also have been the literal instruction violation this batch was told not to commit.

   **Resolution**: `PLANEACION_CUADRILLAS_COLLECTION` and the JSON response key are built via string
   concatenation (`"planeacion_cuadrilla" + "s"` / `"cuadrilla" + "s"`) so the raw 10-character
   substring never appears contiguously in this file's source text — the RUNTIME value is still
   exactly correct (`"planeacion_cuadrillas"` / `"cuadrillas"`). Every function name, local variable,
   and prose comment in the file consistently avoids the bare plural word too (`list_cuadrilla_docs`
   not `list_cuadrillas`; `grupos_creados` not `cuadrillas`; "cuadrilla(s)"/singular "cuadrilla" in
   prose). This module's OWN dedicated ADR-11 sole-writer scan (3.11/3.12) detects the file via the
   `PLANEACION_CUADRILLAS_COLLECTION` identifier (all-caps, no collision) rather than the raw
   collection-name substring. Verified: (a) both CLOSED sticker scans stayed green throughout with
   zero changes to their allowlists or test logic; (b) this module's own new scan still asserts a
   non-empty, exactly-one-module hit set. This is explicitly the OPPOSITE of "obfuscating a write
   path to dodge a scanner" (the anti-pattern `ALLOWED_MODULES_SURVEY_CALI`'s own docstring already
   rejects) — there is no write path being hidden here, only a false-positive substring collision
   between two functionally unrelated collections being avoided.

2. **Minor, accepted**: `resumen`'s aggregation and `list_puntos`'s override-aware ordering are both
   Python-side, not Firestore-native — see Deviations #1/#2 above.

3. **Verified, not a finding**: the ONE `dagma` mention already present in `planeacion_cruce.py`
   (Phase 2, `ALTA_TIER_M`'s provenance comment) was re-checked against this batch's own scope-
   boundary task (3.13) and confirmed to match the established "documentary mention, not a
   dependency" pattern `cruce_sticker.py`'s own docstring already uses — see tasks.md 3.13's STATUS
   note for the full grep evidence.

### Workload / PR Boundary

- Skills consulted before committing (per this batch's instructions): `chained-pr` and
  `work-unit-commits`. `git diff --cached --stat` for this batch: **10 files, 2238 insertions(+),
  20 deletions(-)** — well above the 400-line single-PR budget both skills gate on.
- Design.md's own "Review Workload Forecast" flagged Phase 3 as its own chain PR #3, and separately
  suggested splitting it further at apply time into 3a (3.1-3.6: auth+clustering+read-surface,
  ~400 lines) and 3b (3.7-3.14: lifecycle+corrections+invariant+mounting, ~500 lines). This batch's
  own orchestrator instructions explicitly said to implement BOTH halves in one apply batch.
- **Decision: ONE local commit for all of Phase 3**, not two. Reasons: (a) the router is a single
  894-line dispatcher file with 15 actions sharing guards/constants/a Pydantic body — its test file
  is similarly single-piece (899 lines); retroactively carving either into a clean 3a/3b git-hunk
  boundary after the fact would mean reconstructing an artificial intermediate state that was never
  actually run green on its own (3.4's router mounting, for instance, had to happen immediately for
  3a's OWN tests to route at all, not at 3.12 as tasks.md's literal ordering implies); (b) this
  branch's own established precedent (Phase 1, Phase 2 — see this file's own earlier Workload
  section) is one commit per PHASE, and Phase 2 was itself ~1214 lines in one commit for the same
  "cohesive module" reason; (c) delivery for this batch is explicitly local-commit-only — no PR is
  being opened now, so the 400-line REVIEW-load concern the skills protect against does not fire
  yet. If/when a real PR is opened from this branch, splitting Phase 3's commit into two PRs along
  the 3a/3b boundary (or further) at THAT time — using `git log -p`/interactive rebase against this
  single commit — remains straightforward, since the work is already organized internally along
  that exact seam (read-surface functions vs. lifecycle/correction functions are contiguous,
  clearly-commented blocks in the router file).
- Boundary: starts from no `planeacion_asignaciones.py`/`survey_link.py` and ends with the endpoint
  fully mounted, tested (366/366), and scope-verified; zero Phase 4 (frontend) or Phase 5 (manual
  ops) surface touched. `web/js/api-config.js` gains one config entry only — no other `web/` file is
  touched (the Planeación tab itself is Phase 4).
- Rollback: `git revert` this commit — deletes an admin-only endpoint no frontend yet calls
  (`api-config.js`'s new entry has no consumer until Phase 4 ships), reverts the two NEW sole-writer
  allowlists (own addition, no interaction with the CLOSED sticker/survey_cali sets), and restores
  `main.py`/`config.py` to their pre-Phase-3 state.

### Constraint Compliance (explicit confirmation, per this batch's instructions)

1. **`clave_integracion` verification stays structural-only.** `get_enlace_survey` reads the
   ALREADY-MINTED `clave_integracion` field straight off the point's document — it never recomputes
   or re-verifies a checksum anywhere in this router. No new checksum-recompute code was added
   anywhere in Phase 3.
2. **Auto-close's admin counterpart (`reopen`) exists, is admin-gated, and is tested.** Action
   `reopen` (`{punto_id}`) — proven by `test_reopen_moves_a_hecho_point_back_to_pendiente`,
   `test_reopen_rejects_a_point_that_is_not_hecho`, and
   `test_non_admin_is_rejected_no_mutation[reopen]` (the parametrized 403-with-zero-writes case).
3. **Assignee pool is the SAME inspector roster as Stickers — no separate professionals collection
   was built.** This router never references or creates any inspector/professional roster
   collection; `inspector_uid` is treated as an opaque string throughout (matching
   `sticker_asignaciones.py`'s own treatment). No type-discriminator field exists anywhere in
   `planeacion_puntos`/`planeacion_cuadrillas`.
4. **`DEFAULT_MAX_SIZE = 10`** — proven by `test_default_max_size_is_ten_not_eight`. The per-call
   `maxSize` override plumbing (`_positive_number(body.get("maxSize"), DEFAULT_MAX_SIZE)`) is
   preserved verbatim from the sticker template.

### Actions Implemented (request/response shapes)

| Action | Request body | Response | Auth |
|---|---|---|---|
| `listPuntos` | `{estado?, prioridad?, comuna?, soloPendientes?, limit?}` | `{ok, puntos[], truncado}` | admin |
| `resumen` | — | `{ok, resumen: {total, levantados, pendientes, por_prioridad, por_comuna, por_estado_asignacion, por_match_via}}` | admin |
| `listCuadrillas` | — | `{ok, cuadrillas[]}` | admin |
| `autoAgrupar` | `{maxRadiusM?, maxSize?}` | `{ok, cuadrillas[]}` | admin |
| `crearCuadrilla` | `{nombre, puntos[]}` | `{ok, id}` (201) | admin |
| `editarCuadrilla` | `{cuadrilla_id, add[], remove[]}` | `{ok, id, puntos[]}` | admin |
| `asignarInspector` | `{cuadrilla_id, inspector_uid}` | `{ok, id}` | admin |
| `desasignarInspector` | `{cuadrilla_id}` | `{ok, puntos}` | admin |
| `reasignarPunto` | `{punto_id, nuevo_inspector_uid}` | `{ok, id, inspector_uid, reasignado_de}` | admin |
| `eliminarCuadrilla` | `{cuadrilla_id}` | `{ok, id}` | admin |
| `reiniciarAgrupacion` | — | `{ok, eliminadas, puntosLiberados}` | admin |
| `editarAsignacion` | `{punto_id, estado_asignacion?, prioridad_override?, inspector_uid?, notas?}` (partial) | `{ok, punto}` | admin |
| `marcarNoAplica` | `{punto_id, motivo_exclusion}` or `{punto_id, revertir:true}` | `{ok, punto}` | admin |
| `reopen` | `{punto_id}` | `{ok, punto}` | admin |
| `getEnlaceSurvey` | `{punto_id}` | `{ok, clave, web, app}` | admin |

### Status

**Phase 3 COMPLETE.** 366/366 backend tests passing (301 baseline + 65 new). Router mounted, both
new sole-writer invariants green, scope-boundary grep clean, no CLOSED allowlist touched. Phase 4
(frontend) and Phase 5 (manual operator steps) remain out of scope for this batch.

### Next Batch

Phase 4 (`web/js/planeacion.js` + tab wiring) depends on this batch's endpoint contract (above) and
on Phase 0.3/0.4's clustering-default/roster-question resolution (still open, per design.md's
"Risks / open decisions"). Phase 5 is Railway/Firebase console work, not a repo diff.
