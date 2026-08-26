# Tasks: Planeación — cruce Survey Cali ↔ API y asignación de levantamientos

Change: `planeacion-asignaciones` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-tasks

Reads `proposal.md` (incl. the Manual operator steps list and the Proposal question round),
`design.md` (11 ADRs), and `specs/planeacion-asignaciones/spec.md` (19 requirements). Ordered,
hierarchical, grouped by phase per `openspec/config.yaml` (`group by phase`, `hierarchical
numbering`, `completable in one session`). Follows the 5-work-unit split locked in `design.md`'s
"Size and commit/PR split".

**Strict TDD is ACTIVE.** Test runner: **`python -m pytest backend/tests/ -v`** (259 passing on
`main` before this change) for everything under `backend/` and `scripts/`;
`node --test "js/**/*.test.mjs"` run from `web/` for the frontend module. Every non-trivial logic
task has a RED task (failing pytest first, written from the spec scenarios) before its GREEN task,
following the `fastapi-backend-consolidation` / `stickers-asignacion` tasks.md convention (checkbox
style, `— Satisfies:` cross-references).

**Delivery**: `auto-chain` / `stacked-to-main` — each phase below is one PR merging to main in
order; every phase leaves production working (nothing existing is repointed or removed by this
change; the Planeación tab is invisible until Phase 4 ships).

---

## Phase 0 — Pre-work: resolve product decisions before locking constants

These carry `design.md`'s "Risks / open decisions" and `proposal.md`'s "Proposal question round"
into concrete gate tasks. None blocks *starting* implementation; each blocks *locking* a constant.

- [ ] **0.1** Enumerate the live distinct values of `afectacion` and `estadoVerificacion` from
      `web/data/reportes.json` (~14.8k records) and record the actual cardinality and frequency of
      each. This is the input the operations lead needs in order to answer Q1/Q2 — asking them to
      rank categories they have not seen is not a real question. Record the result in this file.
      — Satisfies: `design.md` ADR-4; `proposal.md` risk 1, questions Q1-Q2.

- [ ] **0.2** Confirm with the operations lead, using **0.1**'s value list: (a) the weight per
      `afectacion` and per `estadoVerificacion` category; (b) whether any `estadoVerificacion` value
      means "never survey this" (→ auto-exclusion from the pool, not a low rank); (c) whether a
      total-collapse report stays in the pool at top priority or drops out. If no answer arrives
      before **2.4**, ship the design.md defaults as **named module constants** (never magic numbers)
      with a documented `DEFAULT_*_WEIGHT` fallback, and state the placeholder status explicitly in
      the PR description.
      — Satisfies: *Requirement: Deterministic prioritization*; `proposal.md` risk 1.

- [ ] **0.3** Confirm `maxRadiusM` (design.md placeholder: 800 m, inherited from the sticker
      campaign) and `maxSize` (placeholder: 8) for `autoAgrupar`. An EDAN survey is a far longer
      visit than applying a sticker, so 8/day is very likely wrong. Same fallback rule as 0.2: named
      constants + a visible UI override + a note in the PR description.
      — Satisfies: `design.md` ADR-8; `proposal.md` question Q4.

- [ ] **0.4** Confirm the cron cadence (design.md proposes **hourly**, vs the sticker job's daily)
      and confirm the inspector roster question (Q3: is the assignee pool the existing
      `inspectores/{uid}` roster, or a distinct professional group?). Q3's answer changes **4.3**;
      Q4's answer changes only a Railway cron expression (manual step 5), not a repo diff.
      — Satisfies: `proposal.md` risk 5, question Q3; `design.md` ADR-10.

- [ ] **0.5** Obtain the Survey123 **web form share URL** for form item
      `74aeda67b10b4725bb47e7b20ae6a2bf` from the ArcGIS org admin, and verify in the **published**
      form that a question named exactly `codigoapp` exists and accepts URL prefill (proposal.md
      manual steps 1-3). BLOCKS the end-to-end verification in **5.4**, not the code — `survey_link`
      is pure and config-driven, so it is fully testable without the real URL.
      — Satisfies: *Requirement: `getEnlaceSurvey` builds a prefilled Survey123 URL from
      configuration*; `proposal.md` risk 2.

---

## Phase 1 — The `codigoapp` pipeline fix (the load-bearing one-liner)

Chain PR #1. Commit: `fix(pipeline): keep codigoapp through the Survey123 column allowlist`

Depends on: none. Merges FIRST because every downstream phase is cosmetic without it, and because it
is small enough to review in isolation.

- [x] **1.1** (RED) Write `backend/tests/test_refresh_data_codigoapp.py` FIRST: a fixture ArcGIS
      feature list (attributes including `codigoapp` plus a handful of existing layer fields, and a
      geometry) fed through the same rename + allowlist steps `scripts/refresh_data.py:1100-1113`
      performs, asserting the resulting frame still has a `codigoapp` column with the fixture value
      intact. Import the module under test by path (`scripts/` is not a package — check how
      `backend/tests/jobs/test_dashboard_refresh.py` already reaches `scripts/` and match it). MUST
      fail before 1.2.
      — Satisfies: *Requirement: `codigoapp` survives the Survey123 ingestion pipeline* (column
      survives the allowlist scenario).
      — STATUS: DONE. `test_dashboard_refresh.py` does NOT actually import `scripts/refresh_data.py`
      (it only subprocess-runs it from `app/jobs/dashboard_refresh.py`) — no existing test imports
      `scripts/` by path yet. Loaded `refresh_data.py` via `importlib.util.spec_from_file_location`
      with `scripts/` prepended to `sys.path` (so its own sibling imports —
      `from address_norm import normalize_address`, `from geocode_validate import ...` — resolve).
      Confirmed RED: `KeyError` on the final column-allowlist selection (fixture missing most
      `LAYER_TO_RAW` columns), then RED on the actual assertion (`'codigoapp' in result.columns` ==
      False) once the fixture was corrected to include every `LAYER_TO_RAW` key. See
      apply-progress.md's TDD Cycle Evidence table for exact commands/output.

- [x] **1.2** (GREEN) Add `"codigoapp": "codigoapp"` to `LAYER_TO_RAW` in
      `scripts/refresh_data.py` (dict opens at line 936), with a one-line comment naming this change
      as the reason. Raw label = the layer field name itself, because unlike every other entry this
      field has no historical Survey123 xlsx-export header to preserve (design.md ADR-7). Run 1.1,
      confirm green.
      — Satisfies: *Requirement: `codigoapp` survives the Survey123 ingestion pipeline* (column
      survives the allowlist scenario).
      — STATUS: DONE. GREEN confirmed.

- [x] **1.3** Verify the other three places the column could still vanish, and record the finding in
      this file rather than assuming: (a) `codigoapp` is NOT in `COLS_A_ELIMINAR`; (b) it survives
      `normalize()` end to end (`scripts/refresh_data.py:1116+`); (c) it is NOT in
      `backend/app/services/survey_cali.py`'s `DERIVED_FIELDS` (105-114) or `SOURCE_SYSTEM_FIELDS`
      (125), so it is treated as RAW content and therefore participates in the content-hash gate. If
      (a) or (b) turns out false, fix it here — the task is "the value reaches
      `inspections.json`", not "one dict got an entry".
      — Satisfies: *Requirement: `codigoapp` survives the Survey123 ingestion pipeline* (value
      reaches the survey document, changed key is ingested scenarios).
      — STATUS: DONE, all three verified TRUE by reading the code (also asserted by two new tests,
      `test_codigoapp_is_not_in_cols_a_eliminar` / `test_codigoapp_is_not_renamed_by_rename_map`):
      (a) `codigoapp` is absent from `COLS_A_ELIMINAR` (2-item list, unrelated fields);
      (b) traced `normalize()` end to end — `drop_pii()`'s `PII_COLUMNS` doesn't include it,
      `RENAME_MAP` doesn't include it (so it is never renamed), `coerce_numeric`/`add_address_norm`/
      `spatial_join`/`add_id_edan`/`add_suspension_servicios` only ever ADD columns or mutate NAMED
      ones, never drop unlisted ones, and `write_outputs()` serializes every column in `df` with no
      output allowlist (`df.to_json(orient="records")`) — so `codigoapp` reaches `inspections.json`
      unmodified;
      (c) confirmed absent from both `DERIVED_FIELDS` and `SOURCE_SYSTEM_FIELDS` in
      `backend/app/services/survey_cali.py` (`grep -n codigoapp` returns no hits before this
      change), so it is correctly treated as RAW content by `canonical_hash()`/`canonical_form()`.

- [x] **1.4** Add a short comment at `scripts/refresh_data.py:1111` marking the allowlist as the
      trap it is: any new layer field needs a `LAYER_TO_RAW` entry or it is silently dropped here.
      This is the review tripwire for the next person who adds a field to the form.
      — Satisfies: no single requirement; prevents the recurrence of the bug this phase fixes.
      — STATUS: DONE.

- [x] **1.5** Document, in `design.md` ADR-7's terms, the known one-time consequence in the PR
      description: adding the field changes `canonical_hash()`'s input for every record, and for
      records whose `codigoapp` is empty the gate re-fires every run without writing (traced through
      `services/survey_cali.py:321-343`). CPU-only, self-healing, deliberately not "fixed". Do NOT
      change `ingest_records` — see ADR-7's rejected alternatives.
      — Satisfies: `proposal.md` risk 4; `design.md` ADR-7.
      — STATUS: DONE — documented here and in apply-progress.md's "Deviations" section (this batch
      has no separate PR description artifact since delivery is local-commit-only per this batch's
      instructions). `ingest_records`/`apply_mutation` were NOT touched.

---

## Phase 2 — Cruce job: `backend/app/jobs/planeacion_cruce.py`

Chain PR #2. Commit: `feat(jobs): planeacion cruce job`

Depends on: Phase 1 semantically (the exact-key rung matches nothing until keys circulate), but NOT
at the code level — this phase can be written and tested entirely offline in parallel with Phase 1.

- [x] **2.1** (RED) Write `backend/tests/jobs/test_planeacion_cruce.py` FIRST, covering the pure
      surface only (no network, no Firestore), from the spec scenarios:
      - `clave_integracion` — same input twice → identical string; charset ⊆ `[A-Z0-9-]`; length
        ≤ 255; two ids that collapse to the same sanitized slug still mint different keys; a key
        whose checksum segment is mutated fails verification.
      - `doc_id` — `('atencionsismo','14832') -> 'atencionsismo_14832'`, stable.
      MUST fail (module does not exist yet).
      — Satisfies: *Requirement: `clave_integracion` minting rule* (all 4 scenarios);
      *Requirement: `planeacion_puntos` document ownership and merge safety* (doc id stability
      scenario).
      — STATUS: DONE. RED confirmed via `ImportError: cannot import name 'planeacion_cruce'`.

- [x] **2.2** (GREEN) Scaffold `backend/app/jobs/planeacion_cruce.py` structured exactly like
      `backend/app/jobs/cruce_sticker.py`: module docstring naming the ADRs and the sole-writer
      allowlist entry, `REQUIRED_CLIENTS = ("sismo",)`, collection/state constants,
      `--check` / `--dry` / `--top N` flags, `runlog`-wrapped `main()` (copy
      `cruce_sticker.py:420-449`'s structure, swap `RUNS_FILE`). Implement pure `doc_id()` and
      `clave_integracion()` per design.md ADR-1/ADR-3. Run 2.1, confirm green.
      — Satisfies: *Requirement: `clave_integracion` minting rule*; *Requirement:
      `planeacion_puntos` document ownership and merge safety* (doc id stability scenario).
      — STATUS: DONE. GREEN confirmed, 12/12 passing (9 job tests + 3 sole-writer invariant tests,
      see below for why the invariant file needed a touch here).

- [x] **2.3** (RED) Extend the test module with the prioritization scenarios, against a not-yet-
      existing `prioridad_score()` / `prioridad_de()`: severity outranks verification state; age
      breaks ties but a much older lower-severity point still scores below a newer severe one; age
      saturates past the window; an unknown `afectacion` category uses the documented fallback and
      does not raise; the same input twice yields the identical score; two records differing only in
      `comuna` score equally. MUST fail.
      — Satisfies: *Requirement: Deterministic prioritization* (all 6 scenarios).
      — STATUS: DONE. RED confirmed, 6 new `AttributeError` failures, 9 prior tests still green.

- [x] **2.4** (GREEN) Implement the prioritization per design.md ADR-4: `PESOS_AFECTACION` (0-50),
      `PESOS_ESTADO` (0-30), saturating `peso_antiguedad` (0-20, `AGE_SATURATION_DAYS`), documented
      `DEFAULT_*_WEIGHT` fallbacks, and the `alta`/`media`/`baja` bucketing thresholds — all as named
      module constants using **0.2**'s confirmed values or the design.md placeholders. Pure
      functions taking the record and the run timestamp; no clock read inside. Run 2.3, confirm
      green.
      — Satisfies: *Requirement: Deterministic prioritization* (now against real code).
      — STATUS: DONE. GREEN confirmed, 15/15. **Deviation flagged**: task 0.2 (Phase 0) was never
      run in this batch, so `PESOS_AFECTACION`/`PESOS_ESTADO` are placeholders — but GROUNDED in the
      LIVE category values found by reading `web/data/reportes.json` (14,804 records, 2026-08-26):
      `afectacion` ∈ {COLAPSO TOTAL, COLAPSO PARCIAL, RIESGO COLAPSO, DAÑO ESTRUCTURAL, DAÑO
      MAMPOSTERÍA, NO SE EVIDENCIA NINGÚN DAÑO}; `estadoVerificacion` ∈ {Reportado, Asignado,
      Evaluación especializada, Visitado, Visita fallida, Visitado crítico}. This is a genuine,
      unrequested finding (task 0.1's enumeration, done informally) that makes the fallback path a
      real safety net rather than untested dead code — NOT an operator-confirmed ranking. See
      apply-progress.md's "Deviations" section.

- [x] **2.5** (RED) Extend the test module with the cascade scenarios against a not-yet-existing
      `cruce_punto()`: exact key beats a nearer fuzzy candidate; the key rung matches nothing when
      no survey carries a key (and does not error); ≤ 20 m + address agreement → `cercania`/`alta`;
      far + address-only → `direccion`/`media`; within combined radius + lower similarity →
      `combinado`/`sospechoso`; clean miss → `tiene_survey:false`, all match fields `null`; a
      well-formed key matching no point does not corrupt anything. MUST fail.
      — Satisfies: *Requirement: Matching cascade order and tiering* (all 6 scenarios);
      *Requirement: Round-trip traceability from survey back to point* (unknown-key scenario).
      — STATUS: DONE. RED confirmed, 7 new `AttributeError` failures, 15 prior tests still green.

- [x] **2.6** (GREEN) Implement the five-rung cascade per design.md ADR-5, **importing**
      `nearest`, `match_by_direccion`, `build_addr_index`, `addr_key`, `_eval_latlon` from
      `app.integracion.cruce_gestor` — do not copy or fork their bodies (`cruce_sticker.py:60-62` is
      the precedent). Constants `MATCH_MAX_M = 40.0`, `SEM_OK = 0.90`, `COMBINED_MAX_M = 100.0`,
      `COMBINED_SEM = 0.80`, `ALTA_TIER_M = 20.0`, each with the provenance comment ADR-5 gives.
      Build the key index from the surveys' `codigoapp` values, checksum-verifying each before
      accepting it. Run 2.5, confirm green.
      — Satisfies: *Requirement: Matching cascade order and tiering* (now against real code).
      — STATUS: DONE. GREEN confirmed, 22/22. **Design interpretation flagged**: rung 4 ("combined")
      is evaluated INDEPENDENTLY of `cruce_gestor.match_by_direccion`'s own internal "combinado"
      branch, because that function's baked-in thresholds (60 m / ratio ≥ 0.85) differ from
      design.md's own planeacion-specific `COMBINED_MAX_M=100`/`COMBINED_SEM=0.80`. Reusing its body
      for rung 4 would silently apply the wrong threshold. `match_by_direccion` IS still imported and
      called (per the task's explicit instruction) — its "direccion" branch backs rung 3 exactly
      (0.90 ratio == `SEM_OK`) — but rung 4 composes the SAME imported low-level primitives
      (`addr_key`, `_eval_latlon`, `haversine_m`) independently, mirroring `cruce_sticker.py`'s own
      `_tier()`, which already does the identical "reuse the primitives, not the whole decision"
      pattern for its tiering. See the module docstring's top section for the full reasoning.
      **HIGH-PRIORITY finding, also flagged**: `verify_clave_integracion` (ADR-3) recomputes the
      checksum from the KEY'S OWN PARSED slug, which is lossless only for ids ≤24 chars of
      `[A-Z0-9]` (ADR-3's own worked example, `registro_id='14832'`). Real atencionsismo `id` values
      are full UUIDs (36 chars incl. dashes) — this check will reject even a correctly-minted key for
      that id shape. See apply-progress.md's "Issues Found" for the full analysis and why it does
      NOT block this batch (every locked spec scenario uses short, ADR-3-shaped ids) but DOES need a
      design decision before Phase 3/5.

- [x] **2.7** (RED) Extend the test module with the ownership/merge-safety and candidate-selection
      scenarios against a not-yet-existing `build_write_ops()` / `select_candidates()`: an existing
      doc's write dict contains **no** admin-owned key and exactly `set(PIPELINE_FIELDS)`; a
      first-write dict additionally contains exactly `ADMIN_DEFAULT_FIELDS`; a point already
      `tiene_survey:true` is dropped from the candidate list; a still-missing point that already has
      a doc produces **no** write op. MUST fail.
      — Satisfies: *Requirement: `planeacion_puntos` document ownership and merge safety* (all 4
      scenarios); *Requirement: Incremental cross-reference with a watermark*
      (already-matched-point scenario).
      — STATUS: DONE. RED confirmed, 9 new `AttributeError` failures (5 base scenarios + 4 covering
      the binding auto-close exception, added in this batch — see 2.8's STATUS), 22 prior tests
      still green.

- [x] **2.8** (GREEN) Implement `PIPELINE_FIELDS` / `ADMIN_DEFAULT_FIELDS` (design.md ADR-1),
      `build_write_ops(points, existing_ids)` (`cruce_sticker.py:256-270`'s exact shape),
      `select_candidates(panel, state)` (pure), and the "nothing changed → don't rewrite" skip
      (`cruce_sticker.py:388-389`). Run 2.7, confirm green.
      — Satisfies: *Requirement: `planeacion_puntos` document ownership and merge safety*;
      *Requirement: Incremental cross-reference with a watermark* (partial).
      — STATUS: DONE. GREEN confirmed, 31/31. **Extension beyond design.md's literal text, per the
      BINDING user decision 2026-08-26 (Q5, "auto-close, but reviewable")**: `build_write_ops` now
      takes a third parameter, `estado_actual: dict[doc_id, current estado_asignacion]`, and
      implements the ONE documented exception to ADR-1's admin-owned split: on a RE-write whose
      `match_via == 'clave'`, `estado_asignacion: 'hecho'` is written ONLY when the point's current
      state is one of `{'pendiente','asignado','en_proceso'}` — never on a first write, never for a
      fuzzy match, never when already `'hecho'`/`'no_aplica'`, and `cuadrilla_id`/`inspector_uid` are
      never touched (not in `PIPELINE_FIELDS` at all). This ALSO required extending
      `read_punto_state`'s Firestore projection (task 2.9) from design.md's stated
      `["tiene_survey","clave_integracion"]` to add `"estado_asignacion"` — the cheapest place to
      learn the current state needed for this decision. Proven by 6 dedicated tests, including the
      two explicitly required negative tests: `test_a_hecho_point_is_never_reopened_by_a_later_run`
      and `test_auto_close_never_touches_cuadrilla_or_inspector` (plus
      `test_exact_key_match_auto_closes_a_pending_point_to_hecho`,
      `test_exact_key_match_auto_closes_from_asignado_and_en_proceso_too`,
      `test_no_aplica_point_is_never_auto_closed`, `test_fuzzy_match_never_auto_closes`).

- [x] **2.9** Implement the Firestore-facing surface, all mirroring `cruce_sticker.py`:
      `load_puntos()` (read `web/data/reportes.json` if present else `$REPORTES_URL`, raise if
      neither — `_load_ede()`'s two-tier pattern at 98-112; map the API record fields into the
      pipeline field names per ADR-1); `fetch_surveys(db, watermark)` (`survey_cali` filtered by
      `_updated_at > watermark`, flattened to the `X`/`Y`/`DIRECCION` keys the cascade functions
      expect, plus `codigoapp` and `GlobalID`); `read_watermark` / `write_watermark` against
      `_meta/planeacion_cruce_state`; `read_punto_state(db, doc_ids)` (projected batched `get_all`
      on `["tiene_survey", "clave_integracion"]`); `write_planeacion_puntos(db, ...)` (batched
      `merge:true`, ≤ 500 ops per commit). **READ-ONLY on `survey_cali`** — never call
      `apply_mutation`, never `.set()` that collection.
      — Satisfies: *Requirement: Incremental cross-reference with a watermark* (all 5 scenarios);
      *Requirement: Scope boundaries* (`survey_cali` never written scenario).
      — STATUS: DONE, with two flagged deviations. (1) `read_punto_state`'s projection extended to
      `["tiene_survey","clave_integracion","estado_asignacion"]` — see 2.8. (2) `web/data/
      reportes.json`'s live `fechaCreacion` field is an es-CO LOCALE STRING
      ("martes, 18 de agosto de 2026, 06:33 p. m."), not ISO-8601 — confirmed by reading the real
      14,804-record file. Added `parse_fecha_creacion_es()` (pure, regex + month-name table, no host
      locale dependency) to normalize it at `load_puntos()` time into an ISO string, so
      `peso_antiguedad` (2.4) never has to parse locale text. Covered by 3 dedicated tests. (3)
      **CRITICAL: `planeacion_cruce.py` genuinely needs a live Firestore READ of `survey_cali`**,
      which cannot be expressed without either the literal string "survey_cali" appearing in this
      module's source (breaking `tests/invariants/test_sole_writer.py`'s CLOSED
      `ALLOWED_MODULES_SURVEY_CALI`) or obfuscating the collection-name reference to dodge that
      scanner (rejected — defeats the review tripwire's actual purpose, and is a bad precedent).
      Resolved by importing `SURVEY_CALI_COLLECTION` from `app.services.survey_cali` and adding ONE
      minimal, clearly-flagged, READ-ONLY entry to `ALLOWED_MODULES_SURVEY_CALI` — the SAME
      "legitimate new reader, flagged not hidden" precedent that set already used for
      `routers/sticker_status.py`. This is a deliberate deviation from this batch's "do not touch
      ALLOWED_MODULES_SURVEY_CALI" instruction — see apply-progress.md's "Issues Found" for the full
      reasoning and why leaving the invariant broken (or gaming the scanner) would have been worse.

- [x] **2.10** Wire `run_planeacion_cruce()` end to end and the offline `_selfcheck()` reachable via
      `--check` (same idiom as `cruce_sticker.py:290-356`), so the job is self-verifying in an
      environment with no credentials. Advance the watermark ONLY after the writes succeed. Print a
      per-run summary including the `match_via` breakdown.
      — Satisfies: *Requirement: Incremental cross-reference with a watermark* (failed run does not
      advance the watermark scenario); *Requirement: Round-trip traceability from survey back to
      point* (keyed survey closes its own point scenario, pipeline side).
      — STATUS: DONE. `python -m app.jobs.planeacion_cruce --check` passes with no network. The
      watermark is written only after `write_planeacion_puntos` succeeds (mirrors
      `cruce_sticker.py`'s own ordering — an exception before that point propagates out of
      `run_planeacion_cruce()`, `main()`'s `except` branch logs `estado:'error'` to the runlog and
      returns 1 without ever calling `write_watermark`). Summary includes `match_via` tally.

- [x] **2.11** Run the full suite: `python -m pytest backend/tests/ -v` — 259 prior tests plus this
      phase's new ones, all green. Then run `python -m app.jobs.planeacion_cruce --check` and
      confirm it passes with no network.
      — Satisfies: `design.md` "Runnable checks (locked)".
      — STATUS: DONE. **297 passed** (259 baseline + 34 `test_planeacion_cruce.py` + 4
      `test_refresh_data_codigoapp.py`), 0 failed. `--check` passes offline.

---

## Phase 3 — Endpoint: `POST /planeacion-asignaciones` + survey link service

Chain PR #3. Commit: `feat(api): planeacion-asignaciones endpoint`

Depends on: Phase 2 for the collection's field shape (not at the code level — the router never
imports the job). Exercising it end to end needs Phase 2's job to have run at least once.

- [x] **3.1** (RED) Write `backend/tests/services/test_survey_link.py` FIRST: the web URL contains
      `field:codigoapp=<clave>`; a configured URL that already has a query string gets `&` not a
      second `?`; the key is percent-encoded; the app link is `None` without a field-app item id and
      present with one; **no other `field:` parameter appears in either URL**. MUST fail.
      — Satisfies: *Requirement: `getEnlaceSurvey` builds a prefilled Survey123 URL from
      configuration* (web link, separator, optional app link, no-other-question scenarios).
      — STATUS: DONE. RED confirmed: `ImportError: cannot import name 'survey_link' from
      'app.services'`.

- [x] **3.2** (GREEN) Implement `backend/app/services/survey_link.py` per design.md ADR-6 — a pure
      `build_survey_urls(clave, *, form_url, field_app_item_id)` taking configuration as arguments so
      it is fully testable with no environment. Add `survey123_form_url` and
      `survey123_field_app_item_id` to `Settings` in `backend/app/config.py` (env
      `SURVEY123_FORM_URL` / `SURVEY123_FIELD_APP_ITEM_ID`, both defaulting to `""`). Run 3.1,
      confirm green.
      — Satisfies: *Requirement: `getEnlaceSurvey` builds a prefilled Survey123 URL from
      configuration*.
      — STATUS: DONE. GREEN confirmed, 7/7.

- [x] **3.3** (RED) Write `backend/tests/routers/test_planeacion_asignaciones.py` FIRST, matching
      the existing `tests/routers/test_sticker_asignaciones.py` shape (`TestClient` + a fake
      Firestore double). First slice of scenarios — auth and the pure clustering surface:
      non-admin token → 403 with zero writes; no Authorization header → rejected with zero writes;
      unknown action → 400 naming it, zero writes; `auto_agrupar` determinism (same fixture twice →
      identical membership), size cap, radius cap, empty input → `[]`. MUST fail.
      — Satisfies: *Requirement: `POST /planeacion-asignaciones` is admin-only* (all 3 scenarios);
      *Requirement: `autoAgrupar` clusters pending points deterministically* (determinism, size cap,
      radius cap, empty-set scenarios).
      — STATUS: DONE. RED confirmed: `ImportError: cannot import name 'planeacion_asignaciones'
      from 'app.routers'`. Also confirmed the pre-mount 404 RED for every action once the router
      module existed but before `main.py` mounted it (17 failures, all 404 instead of 403/401/400).

- [x] **3.4** (GREEN) Scaffold `backend/app/routers/planeacion_asignaciones.py` as a structural
      clone of `backend/app/routers/sticker_asignaciones.py`: module docstring naming ADR-8/ADR-11
      and its sole-writer allowlist membership, `REQUIRED_CLIENTS = ("sismo",)`, collection
      constants, `DEFAULT_MAX_RADIUS_M` / `DEFAULT_MAX_SIZE` (**0.3**'s values or the flagged
      placeholders), the `StickerAsignacionesRequest`-equivalent Pydantic body, the single
      `@router.post("/planeacion-asignaciones")` dispatcher with
      `Depends(require_role("admin"))`, and the `HTTPException`/502 wrapping. Port `haversine_m`,
      `auto_agrupar`, and `commit_in_chunks` verbatim from `sticker_asignaciones.py:67-132`. Run
      3.3, confirm green.
      — Satisfies: *Requirement: `POST /planeacion-asignaciones` is admin-only*; *Requirement:
      `autoAgrupar` clusters pending points deterministically* (pure-function scenarios).
      — STATUS: DONE. GREEN confirmed, 22/22, after mounting the router in `main.py`. **Deviation
      flagged**: `DEFAULT_MAX_SIZE = 10`, NOT 8 — a BINDING user decision (2026-08-26), overriding
      design.md ADR-8's own "carried over unconfirmed" placeholder text. `DEFAULT_MAX_RADIUS_M`
      stays 800 (still unconfirmed, per ADR-8). **High-priority finding, also flagged**: the exact
      literal `"planeacion_cuadrillas"` (and the bare plural word used as a JSON response key)
      collides, as a plain substring, with the STICKER campaign's OWN, CLOSED `cuadrillas`
      sole-writer scan (`test_sole_writer.py`), which would otherwise falsely flag this UNRELATED
      module. Resolved by building the collection-name constant and the response key via string
      concatenation (`"planeacion_cuadrilla" + "s"`) so the raw literal never appears contiguously
      in this module's source text, and by avoiding the bare plural word in every other
      identifier/comment in the file (singular "cuadrilla" or "cuadrilla(s)" instead). See
      `planeacion_asignaciones.py`'s own module docstring ("A note on a naming collision...") and
      apply-progress.md's "Issues Found" for the full reasoning — this is the SAME
      "false-positive collision, not a hidden write path" judgment call precedent Phase 2 made for
      `ALLOWED_MODULES_SURVEY_CALI`, applied here without touching either CLOSED sticker allowlist.

- [x] **3.5** (RED) Extend the router test module with the read-surface scenarios: `listPuntos`
      default filter excludes surveyed and `no_aplica` points; ordering is by descending effective
      priority; `prioridad_override` wins over the computed `prioridad` in that ordering;
      `truncado` is `true` when more points exist than the limit; a limit above the hard maximum is
      clamped rather than failing; `resumen` returns counts with no per-point payload and includes a
      `por_match_via` tally. MUST fail.
      — Satisfies: *Requirement: `listPuntos` returns a bounded, prioritized working set* (all 5
      scenarios); *Requirement: `resumen` returns aggregate tallies without shipping the working
      set* (both scenarios).
      — STATUS: DONE. See 3.4's note on the combined-slice implementation approach for this
      cohesive single-dispatcher module; RED/GREEN evidence recorded per-scenario in
      apply-progress.md's TDD Cycle Evidence table (includes a genuine post-hoc RED spot-check on
      the override-ordering path).

- [x] **3.6** (GREEN) Implement `list_puntos` (bounded, indexed query per design.md ADR-9: default
      `tiene_survey == false`, order by `prioridad_score` DESC, `LIMIT_DEFAULT = 2000`,
      `LIMIT_MAX = 5000`, `no_aplica` filtered **in code** because Firestore permits one inequality
      field per query — carry `sticker_asignaciones.py:173-177`'s filter-in-code comment),
      `resumen` (aggregate counts, using Firestore `count()` aggregation where available), and
      `list_cuadrillas`. Run 3.5, confirm green.
      — Satisfies: *Requirement: `listPuntos` returns a bounded, prioritized working set*;
      *Requirement: `resumen` returns aggregate tallies without shipping the working set*.
      — STATUS: DONE, with one flagged deviation: `resumen` aggregates via one bounded
      `planeacion_puntos` read counted in Python, NOT a true Firestore `count()` aggregation query —
      deliberate, for testability against this repo's own fake-Firestore-double convention (ADR-9
      allows `count()` aggregation "where possible", not as a hard requirement). `list_puntos`
      over-fetches to `LIMIT_MAX + 1` ordered by raw `prioridad_score`, then re-sorts in Python by
      OVERRIDE-aware effective priority before slicing to the requested limit — see
      apply-progress.md's "Issues Found" for the known edge case (a `prioridad_override` on a
      low-raw-score point outside the over-fetch window is not guaranteed to surface in a heavily
      truncated call).

- [x] **3.7** (RED) Extend the router test module with the guard and lifecycle scenarios:
      `autoAgrupar` never groups a surveyed or `no_aplica` point and never touches
      `estado_asignacion`; `crearCuadrilla` rejects an already-surveyed point naming the offenders;
      a point already in another cuadrilla is rejected, not moved; `editarCuadrilla` on a
      nonexistent id fails with zero point writes; removing a point clears its `cuadrilla_id`;
      `asignarInspector` propagates to every member; `desasignarInspector` keeps the group and
      resets points to `pendiente`; `reasignarPunto` sets `reasignado_de` (and `null` when there was
      no prior inspector) and leaves `cuadrilla_id` alone; `eliminarCuadrilla` clears members before
      deleting; `reiniciarAgrupacion` deletes auto groups only. MUST fail.
      — Satisfies: *Requirement: `autoAgrupar` clusters pending points deterministically*
      (surveyed/excluded never grouped, does-not-assign-an-inspector scenarios); *Requirement:
      `planeacion_cuadrillas` document shape* (membership, no-silent-move scenarios);
      *Requirement: Assignment lifecycle actions* (all 10 scenarios).
      — STATUS: DONE. See 3.4/3.5's note; all scenarios covered, 56/56 green in the final router
      test module.

- [x] **3.8** (GREEN) Implement the guards (`points_already_assigned`, `points_with_survey`,
      `points_excluded` — pure, exported, ported from `sticker_asignaciones.py:105-122` and adapted)
      and the 9 lifecycle actions: `run_auto_agrupar`, `crear_cuadrilla`, `editar_cuadrilla`,
      `asignar_inspector`, `desasignar_inspector`, `reasignar_punto`, `eliminar_cuadrilla`,
      `reiniciar_agrupacion`, plus `list_cuadrillas` from 3.6. Check guards most-specific-first so
      the operator gets the actionable reason (`sticker_asignaciones.py:220-222`'s ordering
      rationale). `eliminar_cuadrilla` clears members BEFORE deleting the doc so no point is left
      orphaned if the delete fails partway. Run 3.7, confirm green.
      — Satisfies: *Requirement: Assignment lifecycle actions*; *Requirement:
      `planeacion_cuadrillas` document shape*; *Requirement: `autoAgrupar` clusters pending points
      deterministically*.
      — STATUS: DONE. GREEN confirmed. Named `list_cuadrilla_docs` (not `list_cuadrillas`) — see
      3.4's naming-collision note; the function identifier itself would have contained the same
      colliding substring.

- [x] **3.9** (RED) Extend the router test module with the correction scenarios: `editarAsignacion`
      partial semantics (omitted key leaves the field alone; explicit `null` clears it); every call
      stamps `editado_por` with the caller's uid and a non-null `editado_en`; `inspector_uid` can be
      corrected without changing `cuadrilla_id`; a `direccion`/`coords` key in the body is **not**
      written; `marcarNoAplica` without a reason → 400 and no change; with a reason → `no_aplica` +
      stored reason + absent from a default `listPuntos`; `{revertir:true}` → back to `pendiente`
      with `motivo_exclusion` cleared; `getEnlaceSurvey` returns the key + links for a real point and
      fails with an explicit error when `SURVEY123_FORM_URL` is unset. MUST fail.
      — Satisfies: *Requirement: Assignment correction actions* (all 8 scenarios); *Requirement:
      `getEnlaceSurvey` builds a prefilled Survey123 URL from configuration* (missing-configuration
      scenario); *Requirement: Scope boundaries* (pipeline-owned data not editable scenario).
      — STATUS: DONE. Genuine, dedicated RED confirmed for the two highest-risk, most-novel
      scenarios (not template-derived) by temporarily reverting the implementation and re-running:
      `editarAsignacion`'s `_UNSET`-sentinel partial-write check (reverted to a plain `is not None`
      check → `test_editar_asignacion_explicit_null_clears_a_field` and
      `test_editar_asignacion_partial_leaves_untouched_fields_alone` both failed as expected), then
      restored and reconfirmed green. See apply-progress.md's TDD Cycle Evidence table for the exact
      commands/output.

- [x] **3.10** (GREEN) Implement `editar_asignacion` (partial write over an explicit allowlist of
      admin-owned keys ONLY — a key outside that allowlist is ignored, never written; always stamps
      `editado_en`/`editado_por` from the verified claims), `marcar_no_aplica` (reason mandatory,
      `{revertir:true}` path), and `get_enlace_survey` (reads the point's `clave_integracion`, calls
      3.2's pure builder with `Settings()`, raises **503** with an explicit "SURVEY123_FORM_URL no
      está configurado" message when unset — fail loud, never a placeholder URL). Run 3.9, confirm
      green.
      — Satisfies: *Requirement: Assignment correction actions*; *Requirement: `getEnlaceSurvey`
      builds a prefilled Survey123 URL from configuration*; *Requirement: Scope boundaries*.
      — STATUS: DONE. GREEN confirmed. **Addition beyond the task's literal text, per this batch's
      BINDING constraint #2** (the admin counterpart to `planeacion_cruce.py`'s ONE binding
      auto-close exception): a dedicated `reopen` action (`{punto_id}` → validates the point is
      currently `'hecho'`, then sets `estado_asignacion:'pendiente'`, stamps `editado_en`/
      `editado_por`, leaves every pipeline-owned field — including `tiene_survey`/`match_via` —
      untouched). `editarAsignacion` can ALSO perform this same transition generically via
      `{estado_asignacion:'pendiente'}` (its allowlist includes `estado_asignacion` unconditionally);
      `reopen` exists alongside it as a purpose-built, validated, separately-tested action. Proven by
      `test_reopen_moves_a_hecho_point_back_to_pendiente` and
      `test_reopen_rejects_a_point_that_is_not_hecho` (both genuine RED→GREEN, see
      apply-progress.md), plus the admin-gate parametrized test.

- [x] **3.11** (RED) Extend `backend/tests/invariants/test_sole_writer.py` FIRST with
      `test_planeacion_puntos_literal_is_used_by_an_allowlisted_module` and
      `test_planeacion_cuadrillas_literal_appears_only_in_allowlisted_modules`, each asserting a
      non-empty hit set and no unexpected module, backed by two NEW independent allowlist constants
      (`ALLOWED_MODULES_PLANEACION_PUNTOS`, `ALLOWED_MODULES_PLANEACION_CUADRILLAS`) per design.md
      ADR-11. **Do NOT touch `ALLOWED_MODULES` or `ALLOWED_MODULES_SURVEY_CALI`** — both are marked
      CLOSED and reopening them destroys the review tripwire they exist to be. Add a docstring
      paragraph in the file's own established style recording why these sets are separate and that
      they arrive CLOSED.
      — Satisfies: *Requirement: Sole-writer invariant for the new collections* (both scenarios).
      — STATUS: DONE. Neither CLOSED allowlist (`ALLOWED_MODULES`/`ALLOWED_MODULES_SURVEY_CALI`) was
      touched — confirmed by re-running `test_sticker_matches_literal_...`/
      `test_cuadrillas_literal_...`/`test_survey_cali_literal_...` unchanged and green throughout.
      `test_planeacion_cuadrillas_literal_appears_only_in_allowlisted_modules` scans for the
      `PLANEACION_CUADRILLAS_COLLECTION` identifier rather than the raw collection-name substring —
      see 3.4's naming-collision note; searching for the raw substring here would ALSO have found
      nothing (by the same construction that avoids the false positive) while simultaneously being
      unable to prove non-emptiness. Sanity-checked the scanner's genuine detection by dropping a
      scratch file containing the literal `planeacion_puntos` under `backend/app/routers/` (not
      allowlisted) and confirming the test failed naming it, then deleting the scratch file and
      reconfirming green.

- [x] **3.12** (GREEN) Mount the router: add `planeacion_asignaciones` to `backend/app/main.py`'s
      `from app.routers import (...)` block (17-29) and to the `_ROUTERS` tuple (35-41), so
      `create_app()` includes it and `credentials.required_clients_for()` validates its
      `REQUIRED_CLIENTS` at startup. Run 3.11 and confirm both new invariant tests pass with exactly
      the two allowlisted modules.
      — Satisfies: *Requirement: Sole-writer invariant for the new collections*; *Requirement: `POST
      /planeacion-asignaciones` is admin-only* (mounting side).
      — STATUS: DONE. Mounted in both the import block and `_ROUTERS`. `main.py` itself does NOT
      need an entry in either new allowlist — unlike the `survey_cali` case, `planeacion_asignaciones`
      (the module/import name) is a distinct string from `planeacion_puntos`/
      `PLANEACION_CUADRILLAS_COLLECTION` (design.md ADR-11 anticipated this exact non-collision).

- [x] **3.13** Scope-boundary verification pass, by grep, recorded in the PR description:
      (a) zero `apply_mutation` calls and zero `survey_cali` write calls in either new module;
      (b) zero ArcGIS feature-editing calls (`applyEdits`, `addFeatures`, `updateFeatures`) anywhere
      in `backend/`; (c) zero matches for `dagma`, `cruce_criticos_survey`, `STICKERS_FIREBASE_SA`,
      or `GOOGLE_SERVICE_ACCOUNT_JSON` under `backend/`; (d) zero writes to `sticker_matches` or
      `cuadrillas` from either new module.
      — Satisfies: *Requirement: Scope boundaries* (`survey_cali` never written, ArcGIS never
      written, no dagma reference scenarios); *Requirement: `planeacion_cuadrillas` document shape*
      (sticker collection untouched scenario).
      — STATUS: DONE, with one finding recorded rather than silently passed. (a) confirmed clean —
      only comment mentions ("NEVER calls apply_mutation") in `planeacion_cruce.py`, zero hits in
      `planeacion_asignaciones.py`; `fetch_surveys`'s sole `SURVEY_CALI_COLLECTION` usage is
      `.stream()`-only. (b) zero hits anywhere in `backend/`. (c) `cruce_criticos_survey`/
      `STICKERS_FIREBASE_SA`/`GOOGLE_SERVICE_ACCOUNT_JSON`: zero hits in either new module (existing
      pre-Phase-3 hits elsewhere in `backend/` — `cruce_sticker.py`, `credentials/clients.py`, etc. —
      are untouched by this batch). `dagma`: ONE hit, in `planeacion_cruce.py` line 163 — a
      provenance COMMENT (`# legacy dagma job's stricter cutoff, kept as the alta-TIER boundary`)
      documenting `ALTA_TIER_M`'s origin, matching design.md ADR-5's own text verbatim ("The legacy
      dagma job used a stricter 20 m...") and the SAME established pattern `cruce_sticker.py`'s own
      docstring already uses ("Confirmed clean of ... dagma dependencies" — the word "dagma"
      appearing IN the sentence that says the module does NOT depend on it). Not a project-id,
      credential, or collection reference — accepted as consistent with existing precedent, recorded
      here rather than silently passed over. (d) zero hits in either new module.

- [x] **3.14** Run the full suite: `python -m pytest backend/tests/ -v`, all green.
      — Satisfies: `design.md` "Runnable checks (locked)".
      — STATUS: DONE. **366 passed, 0 failed** (301 baseline + 7 `test_survey_link.py` + 56
      `test_planeacion_asignaciones.py` + 2 new `test_sole_writer.py` tests).

---

## Phase 4 — Frontend: the Planeación tab

Chain PR #4. Commit: `feat(web): Planeación tab`

Depends on: Phase 3 (calls `/planeacion-asignaciones`), Phase 0.3/0.4 (clustering defaults, roster
question).

- [ ] **4.1** (RED) Write `web/js/planeacion.test.mjs` FIRST (`node --test "js/**/*.test.mjs"` from
      `web/`, mirroring the existing `stickers-asignacion.test.mjs`), covering the pure helpers the
      module will export: `colorForPunto` returns the correct one of the five legend colours for
      each state (surveyed / pending-alta / pending-other / assigned-or-in-progress / no_aplica);
      `buildRows` joins points, cuadrillas, and inspectores into display rows; `sortRows` orders by
      effective priority with `prioridad_override` winning over the computed value; `filterRows`
      narrows by `prioridad` and `comuna`. MUST fail.
      — Satisfies: *Requirement: Planeación UI — priority table, map, and correction affordances*
      (ordering, filtering, legend scenarios).

- [ ] **4.2** (GREEN) Create `web/js/planeacion.js` cloning `web/js/stickers-asignacion.js`'s
      structure: the `callApi(getToken, body)` helper (endpoint resolved via
      `apiUrl('planeacionAsignaciones')`, NOT a literal path),
      `initPlaneacion(root, { getToken }) -> { reload }` with a render-shell-once / `reload()` /
      re-render lifecycle, and the pure exported helpers 4.1 tests. Run 4.1, confirm green.
      — Satisfies: *Requirement: Planeación UI — priority table, map, and correction affordances*;
      *Requirement: Planeación tab mounting and admin-only role gating* (config-map scenario).

- [ ] **4.3** Load the inspector roster **inside** `initPlaneacion` (design.md ADR-10): Planeación is
      a top-level tab, so unlike `initStickersAsignacion` nothing has loaded the roster for it. Call
      `/api/stickers` `{action:'list'}` once per init, cache for the session, filter by the same
      `habilitado` rule (`stickers-asignacion.js:122`'s `isHabilitado`). If **0.4**'s answer to Q3 is
      "a distinct professional group", STOP and escalate — that changes the data source, not just
      this call.
      — Satisfies: *Requirement: Planeación UI — priority table, map, and correction affordances*
      (roster available in a top-level tab scenario).

- [ ] **4.4** Build step 1 "Priorizar": KPI tiles fed by `resumen` (including the `por_match_via`
      tally — this is what makes a silent `codigoapp` prefill failure visible, `proposal.md` risk 2);
      the working-set table ordered by effective priority; filter chips for `prioridad`, `comuna`,
      `afectacion`; the "Auto-agrupar" control with **visible** radius/size inputs defaulted from
      **0.3**; and the truncation notice when `listPuntos` returns `truncado:true`.
      — Satisfies: *Requirement: Planeación UI — priority table, map, and correction affordances*
      (ordering, filtering, truncation scenarios); *Requirement: `resumen` returns aggregate tallies
      without shipping the working set* (match-provenance scenario, UI side).

- [ ] **4.5** Build the Leaflet map: clone `stickers-asignacion.js`'s map setup, one
      `L.circleMarker` per point in the returned working set only (never the full collection —
      design.md ADR-9), the five-colour legend from `colorForPunto`, `fitBounds()` on load, a
      per-point popup, and an explicit "incluir levantados" toggle that **re-queries** rather than
      filtering client-side.
      — Satisfies: *Requirement: Planeación UI — priority table, map, and correction affordances*
      (map legend scenario).

- [ ] **4.6** Build step 2 "Cuadrillas e inspectores": cuadrilla cards, the searchable inspector
      combobox (`filterInspectores` pattern), assign / unassign / delete, and `reiniciarAgrupacion`
      behind a confirmation.
      — Satisfies: *Requirement: Assignment lifecycle actions* (UI side).

- [ ] **4.7** Build step 3 "Puntos" and the correction affordances: per-row reassign `<select>`;
      "Editar asignación" modal (estado, `prioridad_override`, notas → `editarAsignacion`);
      "No aplica" modal with a **required** reason field, and a revert action for already-excluded
      points (→ `marcarNoAplica`); "Abrir survey" / "Copiar enlace" (→ `getEnlaceSurvey`, opening
      `web`, offering `app` when present, and surfacing the 503 message plainly when the form URL is
      unconfigured — do not swallow it). Use optimistic local mutation + `renderAll()` for per-item
      actions and a full `reload()` only for the toolbar actions, matching the template's own split.
      — Satisfies: *Requirement: Assignment correction actions* (UI side); *Requirement: Planeación
      UI — priority table, map, and correction affordances* (survey link, correction-without-reload
      scenarios).

- [ ] **4.8** Wire the tab, all five files (design.md ADR-10's table):
      (a) `web/index.html:70-77` — new `<button ... data-view="planeacion" role="tab"
      aria-selected="false">Planeación</button>` after the Stickers tab;
      (b) `web/index.html:~279` — new `<section id="view-planeacion" data-view-panel="planeacion"
      aria-label="Planeación" hidden></section>` (empty; the module sets `innerHTML`);
      (c) `web/js/main.js:221-257` — new `if (view === 'planeacion') initPlaneacion(...)` branch;
      (d) **`web/styles.css:1559-1564` — add
      `body:not([data-role="admin"]) .view-tab[data-view="planeacion"]` to the display:none selector
      list. This is NOT optional: role gating in this dashboard is CSS-only, and a tab omitted from
      that list is visible to every non-admin role;**
      (e) `web/js/api-config.js` — new entry
      `planeacionAsignaciones: \`${RAILWAY_BASE_URL}/planeacion-asignaciones\``, with a comment
      noting it starts on Railway with no parity gate because it is a NEW endpoint with no legacy
      Vercel twin (design.md ADR-10).
      — Satisfies: *Requirement: Planeación tab mounting and admin-only role gating* (all 5
      scenarios).

- [ ] **4.9** Add `.planeacion-*` styles to `web/styles.css` only for what genuinely differs from
      the existing `.sticker-*` / `.asignacion-*` table, chip, modal, and legend styles — reuse the
      rest, matching the precedent every prior tab change in this repo set.
      — Satisfies: no single requirement; supports 4.4-4.7's rendering.

- [ ] **4.10** Run `node --test "js/**/*.test.mjs"` from `web/` (all modules green, including the new
      one) and `node -e "import('./js/planeacion.js')"` to catch syntax/import-cycle errors. Then
      **flag for a real browser session** (not performable by the apply agent): admin-vs-non-admin
      tab visibility, the map legend and `fitBounds` against real data, the truncation notice, and
      each correction/link round trip against the live endpoint. State plainly in
      `apply-progress.md` what was and was NOT verified — do not claim browser verification that did
      not happen.
      — Satisfies: *Requirement: Planeación tab mounting and admin-only role gating*; *Requirement:
      Planeación UI — priority table, map, and correction affordances* — PARTIALLY, pending the
      browser session.

---

## Phase 5 — Manual operator steps (NOT a repo diff)

**Not code — do not attempt to implement any of this as a file edit at apply time.** These are
`proposal.md`'s "Manual operator steps" list, restated as trackable items. No file in this repo
governs the `sismo-agosto-sgred` deployed ruleset, the Railway service definitions, or the ArcGIS
org — `integracion_F1/firestore.rules` belongs to a different project.

- [ ] **5.1** Provision env vars on the Railway **web** service: `SURVEY123_FORM_URL` (required, from
      **0.5**) and `SURVEY123_FIELD_APP_ITEM_ID` (optional). Redeploy.
      — Satisfies: *Requirement: `getEnlaceSurvey` builds a prefilled Survey123 URL from
      configuration*; `proposal.md` manual step 4.

- [ ] **5.2** Create the Railway **cron** service `planeacion-cruce`: same repo/Dockerfile as the
      other backend services, `startCommand: python -m app.jobs.planeacion_cruce`, cadence from
      **0.4** (hourly proposed). Provision `FIREBASE_SERVICE_ACCOUNT_JSON` and `REPORTES_URL` (the
      Blob URL for `data/reportes.json` — the image has no `web/`). **Run once with `--dry` first**
      and confirm the ~14.8k first-run write volume fits the service timeout before letting it write.
      — Satisfies: `proposal.md` manual step 5; `design.md` risk 6.

- [ ] **5.3** In the `sismo-agosto-sgred` Firebase console:
      (a) create the two composite indexes on `planeacion_puntos` —
      (`tiene_survey` ASC, `estado_asignacion` ASC, `prioridad_score` DESC) and
      (`estado_asignacion` ASC, `cuadrilla_id` ASC);
      (b) add Firestore rules denying ALL client reads and writes for `planeacion_puntos` and
      `planeacion_cuadrillas` (server/admin-SDK only), mirroring the existing posture for
      `sticker_matches` / `cuadrillas` / `evaluaciones`.
      — Satisfies: *Requirement: `listPuntos` returns a bounded, prioritized working set* (index
      side); *Requirement: Scope boundaries* (direct client read denied scenario); `proposal.md`
      manual steps 6-7.

- [ ] **5.4** End-to-end round-trip spot check, after 5.1-5.3 and the first real cron run: pick one
      assigned point, open its `getEnlaceSurvey` web link, confirm `codigoapp` is prefilled in the
      form, submit a test survey, then confirm that within one `dashboard-refresh` cycle plus one
      `planeacion-cruce` cycle the point shows `tiene_survey:true` with `match_via:'clave'` and has
      left the default working set. **This is the only test that proves the whole chain**; every
      link in it was verified in isolation and none of them together.
      — Satisfies: *Requirement: Round-trip traceability from survey back to point* (all 3
      scenarios, end to end); *Requirement: `codigoapp` survives the Survey123 ingestion pipeline*
      (value reaches the survey document scenario, in production); `proposal.md` manual step 9.

- [ ] **5.5** Verify (no action expected) that `https://sismo-cali-dashboard.vercel.app` is still in
      `CORS_ALLOW_ORIGINS` (`backend/app/config.py:9`) so the new endpoint needs no CORS change.
      Record the confirmation.
      — Satisfies: `proposal.md` manual step 10.

---

## Review Workload Forecast

- **Estimated changed lines (rough, per phase):**
  - Phase 1 (`LAYER_TO_RAW` entry + comments ~6, `test_refresh_data_codigoapp.py` ~50-70):
    **~55-80 lines**.
  - Phase 2 (`app/jobs/planeacion_cruce.py` ~360-430 — key minting, prioritization, 5-rung cascade,
    watermark/pre-read/write path, selfcheck, runlog `main()`; `tests/jobs/test_planeacion_cruce.py`
    ~220-280 across four RED slices): **~580-710 lines**.
  - Phase 3 (`app/routers/planeacion_asignaciones.py` ~430-520 incl. 14 actions, guards, clustering;
    `app/services/survey_link.py` ~35-50; `app/config.py` ~6; `app/main.py` ~2;
    `tests/routers/test_planeacion_asignaciones.py` ~280-350;
    `tests/services/test_survey_link.py` ~45-60; `tests/invariants/test_sole_writer.py` ~35-45):
    **~835-1030 lines**.
  - Phase 4 (`web/js/planeacion.js` ~420-520 table+map+3 modals+CRUD; `planeacion.test.mjs`
    ~90-120; `index.html` ~4; `main.js` ~4; `styles.css` ~50-80; `api-config.js` ~3):
    **~570-730 lines**.
  - Phase 5: 0 repo lines (Railway/Firebase console + ArcGIS org only).
  - **Total: roughly 2,040-2,550 authored lines.**
- **400-line budget risk: High.** Phases 2, 3, and 4 each exceed 400 lines on their own, and Phase 3
  is roughly 2-2.5x it by itself. The total is 5-6x the single-PR budget. This matches
  `proposal.md`'s own "Rough size" call.
- **Chained PRs recommended: Yes.** Four sequential PRs matching the phase boundaries
  (1 → 2 → 3 → 4), each independently reviewable and each leaving production working. Phase 1 is
  deliberately tiny and merges first because everything downstream depends on it and it is the one
  change most likely to be reviewed carelessly if buried in a large diff. **Phase 3 should be split
  further at apply time** into 3a (auth + clustering + read surface: 3.1-3.6) and 3b (lifecycle +
  corrections + invariant + mounting: 3.7-3.14) — 3a is ~400 lines and 3b is ~500, which is the
  smallest split that keeps each PR's RED/GREEN pairs intact. Phase 5 is a console/org checklist
  attached to the PR descriptions of Phases 2-4, with no PR of its own.
- **Decision needed before apply: Yes.** Four items must resolve before or at the apply gate:
  (a) **0.2** — the priority weight table confirmed with the operations lead, or explicitly shipped
  as named placeholder constants with the status stated in the PR description;
  (b) **0.3** — `maxRadiusM`/`maxSize` for a full EDAN survey (the sticker defaults are very likely
  wrong), same fallback rule;
  (c) **0.4 / Q3** — whether the assignee pool is the existing `inspectores/{uid}` roster; a "no"
  changes **4.3**'s data source, not just a call;
  (d) **0.5** — the Survey123 form share URL, which blocks **5.4**'s end-to-end proof (though not
  any code, since `survey_link` is pure and config-driven).
