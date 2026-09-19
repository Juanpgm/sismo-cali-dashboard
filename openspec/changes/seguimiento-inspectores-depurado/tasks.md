# Tasks: seguimiento-inspectores-depurado

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~950-1250 (2 new service modules ~350 LOC + tests ~450 LOC + router ~120 + CLI ~90 + frontend ~180 + requirements 1) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (referencia I/O) → PR 2 (depuración engine + parity) → PR 3 (router wiring + CLI) → PR 4 (frontend) |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain (resolved 2026-09-16 — PR3 targets PR2's branch, per the PR3 apply run's Chain Context) |

Decision needed before apply: No (resolved — feature-branch-chain, PR3 = Router wiring, CLI, dependency, base = PR2 branch)
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | `inspectores_referencia.py` + tests | PR 1 | Base = main (or tracker branch). Independent of engine; no router wiring. |
| 2 | `inspectores_depuracion.py` (6 seams) + parity fixture test | PR 2 | Base = PR 1 branch (feature-branch-chain) or main (stacked). Pure functions, no I/O. |
| 3 | Router wiring (`DepuracionCache`, flag, `main.py`), `publicar_referencia_inspectores.py`, `requirements.txt` | PR 3 | Base = PR 2 branch. Ships with flag OFF. |
| 4 | Frontend `seguimiento.js` (identity index, GRUPO-EXTERNOS row, manual-review section) + `seguimiento.test.mjs` | PR 4 | Base = PR 3 branch. Ships after backend parity verified in logs. |

## Spec/design conflict — RESOLVED

`specs/datos-referencia-blob/spec.md` was reconciled to match `design.md` (D8): one atomic private `bundle.json`, not three independent blobs. The spec's "Three Named Reference Artifacts" requirement was rewritten to "Three Reference Sources In A Single Atomic Bundle" before Phase 1 started. No remaining discrepancy — verified by `sdd-verify` against the implementation.

## Phase 1: Referencia I/O Seam (PR 1)

- [x] 1.1 Create `backend/app/services/inspectores_referencia.py`: `EntradaReferencia`, `ReferenciaBundle` dataclasses per design Interfaces/Contracts.
- [x] 1.2 RED: `backend/tests/services/test_inspectores_referencia.py::test_parse_bundle_valid` — valid `schema:1` dict → populated `ReferenciaBundle`.
- [x] 1.3 GREEN: implement `parse_bundle(raw: dict) -> ReferenciaBundle | None`.
- [x] 1.4 RED+GREEN: `test_parse_bundle_unknown_schema_returns_none` — `schema: 2` → `None`.
- [x] 1.5 RED+GREEN: `test_parse_bundle_malformed_rows_skipped_not_raised` — a row missing `cedula_key` doesn't raise, is excluded or flagged.
- [x] 1.6 RED+GREEN: `test_parse_bundle_none_or_garbage_input` — `None`/non-dict input → `None`.
- [x] 1.7 RED: `test_cargar_referencia_valid` — inject fake `load_json` returning valid bundle dict → `ReferenciaBundle(activa=True, motivo="")`.
- [x] 1.8 GREEN: implement `cargar_referencia(*, load_json=blob_lkg.load_json, ahora=None)`, never raises.
- [x] 1.9 RED+GREEN: `test_cargar_referencia_blob_missing` — `load_json` returns `None` → `ReferenciaBundle.vacia()`, `motivo="sin_blob"`.
- [x] 1.10 RED+GREEN: `test_cargar_referencia_no_token` — simulate missing `BLOB_READ_WRITE_TOKEN` → `motivo="sin_token"`.
- [x] 1.11 RED+GREEN: `test_cargar_referencia_corrupt_json` — `load_json` raises/returns garbage → `motivo="manifiesto_invalido"` or `"esquema_invalido"`, no exception propagates.

## Phase 2: Depuración Engine — 6 Pure Seams (PR 2)

**Second documented divergence found during the parity fixture (2.27), beyond the permitted D7 fix — root cause corrected 2026-09-16**: the golden `outputs/inspectores_depurado_seguimiento.xlsx` snapshot has 3 rows with `estado_sugerido="revisar"` that are absent from BOTH Vercel and Fase 2 (confirmed against the raw `context/EDA_HAMON/01_Insumos/` source files) — per `spec.md`'s literal "Estado Sugerido Precedence" rule, absence from both sources with no `tiene_sticker` history means `"candidato_desactivacion"`, not `"revisar"`.

A first pass at this fixture (this note, prior version) misattributed the divergence to the base CSV's own `activo="true"` flag (correlation, not causation — all 3 rows do carry `activo="true"`, but that column is never read by the notebook's classification logic). Re-verified 2026-09-16 directly against `outputs/accion_remap_codigos.csv` and the notebook's actual cell order: the true root cause is a **pipeline-ordering artifact** — the notebook computes its `desactivar`/`revision` id sets from `codigo_inspector` BEFORE the code-remap step runs later in the same section. All 3 rows (Mario Fernando Rosas Martinez/058, Rafael Marin Valencia/061, Juan Camilo Haya Castaño/101) originally held a código that rightfully belonged to a different Vercel-registered person; they were excluded from both classification sets while still holding that código, then had it stripped by remap, and fell through to the notebook's trailing `else: "revisar"` with no other signal.

Separately (also verified 2026-09-16, same session): `estado_sugerido()` had a REAL, independent bug — it never distinguished the raw `tiene_sticker` (`n_stickers > 0`, all-time) from `tiene_sticker_valido` (post-20-Aug only), so an inspector who had sticker history but none of it post-cutoff, no código, and no Fase2/Vercel membership was incorrectly classified `"candidato_desactivacion"` instead of the correct `"revisar"` fallback. Fixed in `estado_sugerido()` (now reads `perfil.n_stickers > 0` for branches 2-3) with new coverage in `test_inspectores_depuracion.py` (`test_estado_sugerido_sticker_history_without_codigo_falls_to_revisar` and the full-pipeline `test_depurar_full_pipeline_sticker_history_falls_to_revisar_not_desactivacion`). This fix does NOT resolve the 3-row divergence above — all 3 have `n_stickers=0` in the golden data, so raw `tiene_sticker` is `False` for them too; the two issues are unrelated.

**RESOLVED 2026-09-16 (user decision)**: for the remap-timing case, the user confirmed the implementation's current behavior (`candidato_desactivacion`, computed from the post-remap código) is the DESIRED behavior, not a bug — a código stripped by a correct remap must not keep protecting someone from the deactivation-candidate bucket. This is now an accepted, intentional divergence from the golden xlsx (same category as the D7 fix), not an open question. No further code change needed — `estado_sugerido()` already computes off the post-remap `codigo`, matching this decision as-is. `test_inspectores_depuracion_parity.py`'s docstring and assertions should treat these 3 rows as a documented, expected divergence (D-REMAP) alongside D7, not a failure.

- [x] 2.1 Create `backend/app/services/inspectores_depuracion.py` skeleton with public `depurar(*, stickers, roster_by_cedula, nombres_survey, referencia, hoy) -> Depuracion`.
- [x] 2.2 RED+GREEN: `fusionar_identidad` — cédula-fix D-P2 (unify only exact name match), keyed by Firestore `identificacion` (spec: Identity Anchor).
- [x] 2.3 Edge case: `test_fusionar_identidad_cedula_ties` — two cédulas, same exact name → unified; different name → not unified, discrepancy reported.
- [x] 2.4 RED+GREEN: `remapear_codigos` — cédula match first, `rapidfuzz.token_sort_ratio >= 90` only surfaces candidates (never auto-merges), skips `referencia.codigos_duplicados` (D-P1).
- [x] 2.5 Edge case: `test_remapear_codigos_vercel_duplicate_excluded` — 2+ Vercel entries share a `codigo` → excluded from remap, added to `revision_manual` with `motivo="codigo_vercel_duplicado"`.
- [x] 2.6 Edge case: `test_remapear_codigos_fuzzy_never_autoemerges` — 90-99% name similarity → no merge, routed to `revision_manual`.
- [x] 2.7 RED+GREEN: `unificar_duplicados` — `nombre_norm` exact match; survivor score `has_sticker > has_codigo > en_vercel > en_fase2 > not_no_persona > not_cedula_sospechosa`, tie → oldest.
- [x] 2.8 Edge case: `test_unificar_duplicados_name_collision_survivor_score` — construct a full tie-break chain fixture, assert survivor order matches the score list exactly.
- [x] 2.9 Edge case: `test_unificar_duplicados_loser_not_deleted` — losing record excluded from output list but not mutated/removed from source roster dict.
- [x] 2.10 RED+GREEN: `colapsar_externos` — `(cedula_sospechosa OR no_persona) AND (sin ultimo_sticker OR dias_inactivo > 7) AND sin codigo`.
- [x] 2.11 Edge case: `test_colapsar_externos_dias_inactivo_boundary` — exactly `dias_inactivo == 7` → NOT collapsed; `8` → collapsed. Assert boundary is `> 7`, not `>= 7`.
- [x] 2.12 Edge case: `test_colapsar_externos_codigo_excludes` — flagged non-person WITH a `codigo` stays standalone (spec scenario).
- [x] 2.13 RED+GREEN: `resolver_np` — Fase2 > Vercel > `rango_main` > `""`/`"ninguno"` (spec: NP Resolution Hierarchy), covering both scenarios (Fase2 wins; no source has NP).
- [x] 2.14 RED+GREEN: `calcular_fase` — regex `^P?\s*(\d+)`, `>=3` → `"Fase II"`, else `"Fase I"`; non-match → `fase_np_faltante=true`.
- [x] 2.15 Edge case: `test_calcular_fase_non_matching_np` — `np=""` and `np="NO SIRVE"` both → `"Fase I"` + `fase_np_faltante=true`.
- [x] 2.16 RED+GREEN: `estado_sugerido` — precedence `no_persona` → `candidato_desactivacion` → `revisar` → `activo` → `revisar` fallback; `tiene_sticker_valido` NEVER triggers `"activo"` (D7 fix).
- [x] 2.17 Edge case: `test_estado_sugerido_sticker_activity_alone_never_activo` — sticker valid, no código, absent from Fase2/Vercel → `candidato_desactivacion`, not `activo` (spec scenario, guards regression to notebook's bug).
- [x] 2.18 Edge case: `test_estado_sugerido_cedula_sospechosa_alone_not_forced` — `cedula_sospechosa=true`, `es_cuenta_no_persona=false` → not forced into `"no_persona"`.
- [x] 2.19 RED+GREEN: `fuente_dato` — dynamic string reflecting actually-used sources per inspector; degrades when a reference source is unavailable (no false claim of `"vercel"` etc).
- [x] 2.20 RED+GREEN: `tiene_sticker_valido` cutoff helper — only `origen=="sistema"` AND `fechaCreacion >= 2026-08-20T00:00:00Z`; `origen=="firebase"` always excluded.
- [x] 2.21 Edge case: `test_tiene_sticker_valido_origen_other_than_sistema_firebase` — an `origen` value outside `{"sistema","firebase"}` is treated as excluded (not sistema), documented explicitly.
- [x] 2.22 RED+GREEN: `alias_nombres(perfiles, nombres_survey)` — survey name mapping to a real person's key never lands in GRUPO-EXTERNOS.
- [x] 2.23 Edge case: `test_alias_nombres_dedupe_collision` — two different survey names normalize to the same key; second one does not silently overwrite the first's mapping (assert deterministic resolution, e.g. first-wins + logged conflict).
- [x] 2.24 RED+GREEN: `test_depurar_empty_referencia_bundle` — `ReferenciaBundle.vacia()` in → every inspector's `np_fuente` degrades to `"main"`/`"ninguno"`, `activa=false` surfaced, no exception.
- [x] 2.25 RED+GREEN: `test_depurar_non_person_deduped_against_survey_cali` — a non-person account's visit also present in `survey_cali` is counted once, not twice (spec scenario).
- [x] 2.26 RED+GREEN: `test_depurar_estado_sugerido_never_writes` — assert `depurar()` returns a value only, takes no mutable Firestore handle, and does not call any write API (advisory-only invariant).
- [x] 2.27 Parity: `backend/tests/services/test_inspectores_depuracion_parity.py` — golden fixture from the 12-sep xlsx snapshot; run `depurar()` over the same input; diff `np`/`fase`/`estado_sugerido` per row; assert divergences are limited to the documented D7 fix (informational `tiene_sticker_valido` no longer drives `activo`).

## Phase 3: Router Wiring, CLI, Dependency (PR 3)

- [x] 3.1 Add `rapidfuzz>=3.9` to `backend/requirements.txt`.
- [x] 3.2 Build/deploy check: confirm `rapidfuzz` installs cleanly under `python:3.12-slim` (manylinux wheel, no compiler) per design's rapidfuzz note; record result. **Verified 2026-09-16**: `pip download rapidfuzz>=3.9 --python-version 312 --platform manylinux2014_x86_64 --only-binary=:all:` resolves `rapidfuzz-3.13.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl` — no compiler needed.
- [x] 3.3 Create `scripts/publicar_referencia_inspectores.py`: one-shot CLI, CSV/xlsx → normalized JSON → upload to `referencia/inspectores/bundle.json` with `access:'private'` via existing Blob credential pattern.
- [x] 3.4 Test: `backend/tests/test_publicar_referencia_inspectores.py` — CLI produces schema-valid JSON matching `parse_bundle` expectations from a small fixture CSV/xlsx.
- [x] 3.5 Modify `backend/app/routers/stickers_atencionsismo.py`: add `DepuracionCache` keyed by sticker-payload object identity + `hoy` + `generado_en` (D4/D6).
- [x] 3.6 RED+GREEN: `backend/tests/routers/test_stickers_atencionsismo.py::test_depuracion_cache_*` — same list object → no recompute; new object/new `hoy`/new `generado_en` → recompute (plus a referencia-TTL-not-expired coverage test).
- [x] 3.7 Assemble `depuracion` block in `get_stickers_atencionsismo` per design's Data Flow (outside the cached, Blob-persisted `evaluaciones` list — D3, PII isolation).
- [x] 3.8 Add `SEGUIMIENTO_DEPURACION` env flag; when unset/`0`, response omits `depuracion` (byte-identical current payload).
- [x] 3.9 Wire `app.state.depuracion_cache` in `backend/app/main.py`.
- [x] 3.10 Integration test: flag off → no `depuracion` key in response.
- [x] 3.11 Integration test: reference missing → `depuracion.activa=false`, `motivo` set, response still 200.
- [x] 3.12 Integration test: reference present → `depuracion.inspectores` populated, `activa=true`.
- [x] 3.13 Integration test: degraded payload (last-good cache serves when a live source fails) — assert no error response.
- [x] 3.14 Security test: assert `depuracion` block (nombres, cédulas, teléfonos, correos) never appears in the payload written to the public `blob_lkg` copy of `evaluaciones`.

**Found/fixed during this pass (beyond the literal task list)**:
- `stickers.inspector_profiles()` computed `codigo` into a local var only used as the `by_codigo` dict KEY — never stored as a field on either map's profile VALUE, so `stickers_atencionsismo.normalize_sticker`'s Rule A (`roster_cedula_match.get("codigo")`) always read `""` against a REAL roster; only hand-built test fixtures ever exercised that branch. Fixed (additive field), with new regression tests and updated 4 pre-existing exact-equality tests in `test_stickers.py`.
- `inspectores_referencia.cargar_referencia`'s default `load_json=blob_lkg.load_json` read via an UNAUTHENTICATED GET to the public CDN host — incompatible with D8's `access:'private'` bundle. Added `blob_lkg.load_json_private` (+ `blob_sync.download_authenticated`, + an `access` param on `blob_sync.upload`) and switched the default to `load_json_private`. Every Phase 1 test injects its own `load_json`, so this default-only change broke nothing.
- `_nombres_survey(db)` reads `survey_cali`'s `nombre_evaluador` field directly (per `scripts/refresh_data.py`'s own RENAME_MAP) — added `routers/stickers_atencionsismo.py` to `backend/tests/invariants/test_sole_writer.py`'s `ALLOWED_MODULES_SURVEY_CALI` (read-only, same precedent as `jobs/planeacion_cruce.py`).

**Documented gap, not fixed (out of this PR's scope)**: `_stickers_para_depuracion` uses the cached `evaluaciones[]`'s own `fecha` field as a proxy for `depurar()`'s `fecha_creacion` input — `fecha` is the MATCHED Firestore evaluación's own date when one exists, not always byte-identical to the sticker's raw `fechaCreacion` (which `normalize_sticker`, a Phase 2 file, never preserves once a match overrides it). Impact: `tiene_sticker_valido`/`dias_inactivo` may be marginally stale for the subset of stickers with a Firestore-matched evaluación. A real fix threads a new raw-timestamp field through `normalize_sticker` — flagged as a follow-up, not implemented here to keep this PR's diff to the assigned router-wiring/CLI/dependency scope.

## Phase 4: Frontend (PR 4)

- [x] 4.1 Modify `web/js/seguimiento.js`: `buildIdentityIndex({stickers, surveys, depuracion})` — when `depuracion.activa`, skip "first non-empty wins" loop (lines ~341-378), build `profiles` from `depuracion.inspectores` directly.
- [x] 4.2 RED+GREEN: `web/js/seguimiento.test.mjs::test_buildIdentityIndex_backend_np_not_overwritten` — backend `np="P3"` not overwritten by raw `profesional.rango="P1"` (spec scenario).
- [x] 4.3 RED+GREEN: `test_buildIdentityIndex_depuracion_absent_byte_identical` — no `depuracion` or `activa:false` → current code path unchanged.
- [x] 4.4 Add expandable GRUPO-EXTERNOS row rendering `grupo_externos.detalle`; collapsed by default (spec scenarios).
- [x] 4.5 RED+GREEN: `test_grupo_externos_row_collapsed_by_default` and `test_grupo_externos_expand_shows_detail`.
- [x] 4.6 Add "Revisión manual" section/tab rendering `depuracion.revision_manual`; explicit empty state when list is empty (spec scenarios).
- [x] 4.7 RED+GREEN: `test_revision_manual_section_renders_entries` and `test_revision_manual_empty_state`.
- [x] 4.8 Add freshness badge: `activa:false` badge, `referencia_generada_en` shown when `true`.
- [x] 4.9 Update XLSX export and search to read `np`/`fase`/`estado_sugerido`/`fuente_dato` from the same resolved fields as the table (spec: Table And Export Reflect Depurado Fields Consistently).
- [x] 4.10 RED+GREEN: `test_export_matches_onscreen_np` and `test_search_matches_resolved_np_not_rango`.

**Found/fixed during this pass (beyond the literal task list)**: design's File Changes table only listed `web/js/seguimiento.js` for the frontend, but the ACTUAL wiring gap was one level below it — `web/js/stickers.js`'s `tagFuente()` (the shared fetch-normalization helper both `initStickers` and `seguimiento.js`'s `fetchEvaluacionesOnce` reuse) dropped the response's top-level `depuracion` key entirely before `buildIdentityIndex` ever saw it. Without this fix, `depuracion` would never reach the frontend in a real browser regardless of how correct `buildIdentityIndex` itself is — only unit tests that construct the `depuracion` argument by hand would ever exercise the new code path. Fixed with a minimal, backward-compatible passthrough (`depuracion: (data && data.depuracion) || null`); `initStickers`'s own endpoint never sends this field, so its behavior is unchanged (verified: `stickers.test.mjs` still green, 2 existing full-object assertions updated to include the new key, malformed/absent-input edge cases added).

## Phase 5: Migration / Rollout Verification

- [ ] 5.1 Run `scripts/publicar_referencia_inspectores.py` against real Vercel/Fase2/main sources to publish `bundle.json`.
- [ ] 5.2 Deploy backend with `SEGUIMIENTO_DEPURACION=0`; confirm payload unchanged.
- [ ] 5.3 Verify parity in logs (compare computed `depuracion` against expectation without exposing it to the front yet).
- [ ] 5.4 Flip `SEGUIMIENTO_DEPURACION=1`; confirm `depuracion` block appears.
- [ ] 5.5 Ship PR 4 (frontend) once backend parity is confirmed in production logs.
- [x] 5.6 Confirmed with user 2026-09-16: `correo_contacto` IS in scope for this delivery (private bundle removes the exposure risk; already wired into the individual PDF report from Phase 4).

## Phase 5b: Mobile Overflow Fix (PR 5)

Not part of the original plan — found during visual verification (Playwright,
375px viewport, full-page screenshot) of PR 4's frontend, after Phase 4 was
already merged into this chain's base branch. `#view-seguimiento` (the
Seguimiento tab's real markup, including the 12-column `.tipologia-table`,
the GRUPO-EXTERNOS row, and the Revisión manual card) was forced to ~1180px
wide on a 375px viewport: it is a flex item of `.main-column`
(flex-direction:column) that also carries `margin: 0 auto` for desktop
centering under `max-width: 1180px`. A flex item with an auto margin on the
cross axis is sized by shrink-to-fit (its own content's preferred width,
clamped only by max-width) instead of stretching to the container's
available width — so on mobile the section held its full desktop width
regardless of viewport, and since `html, body { overflow-x: clip }`
suppresses the page-level horizontal scrollbar, that excess content was
genuinely unreachable, not just "needs a scroll". `.main-column`,
`.eval-section`, and `.card` are shared by every other tab (Reportes
ciudadanos, Asignación/Stickers, Usuarios, etc. — confirmed via grep before
touching anything), so the fix is scoped to the `#view-seguimiento` selector
only.

- [x] 5b.1 Add `width: 100%` (fixes the shrink-to-fit sizing) alongside the
      existing `max-width: 1180px; margin: 0 auto;` and a new `min-width: 0`
      (belt-and-suspenders against the flex item's content-based auto
      minimum) on `#view-seguimiento` in `web/styles.css`. No other selector
      touched.
- [x] 5b.2 Verify with Playwright at 375px and 1440px, before/after the fix,
      using a static harness that reuses the real `sectionHtml()` markup
      shape (see `web/js/seguimiento.js`): `#view-seguimiento`'s own
      geometry goes from clientWidth 1180 (mobile) / 1180 overflowing its
      1092px-available desktop column, to clientWidth 351 (mobile) / 1092
      (desktop, no longer clipped) — `.table-scroll` inside it still scrolls
      horizontally as designed for the remaining table overflow.
- [x] 5b.3 Verify no collateral effect: same harness includes proxy sections
      for Reportes ciudadanos (real `sectionHtml()` from
      `reportes-ciudadanos.js`) and Asignación/Stickers (`.card` +
      `.table-scroll` shape from `planeacion.js`), sharing `.eval-section`/
      `.card`. Geometry (scrollWidth/clientWidth/offsetWidth/rect) for both
      is byte-identical before vs after the CSS change at both viewports —
      confirmed no leakage outside the `#view-seguimiento` ID selector.
- [x] 5b.4 Run `node --test "web/js/*.test.mjs"` — unaffected (CSS-only
      change): 18/18 passing, 0 failures.

## Full Test Suite Gate

- [ ] 6.1 Run `python -m pytest backend/tests/ -q` — all 1442+ existing tests plus new ones green.
- [ ] 6.2 Run `node --test "web/js/*.test.mjs"` — all existing tests plus new `seguimiento.test.mjs` cases green.

---

# Extension 2026-09-19 — Phases 7-12 (complete base)

Source: `explore-extension.md` + `design.md` addendum (D9-D20). STRICT TDD is active
(`openspec/config.yaml: strict_tdd: true`) — every behavior change is RED → GREEN → REFACTOR.
Test commands: `python -m pytest backend/tests/ -q` and `node --test "web/js/*.test.mjs"`.

## Extension Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1500-1750 across 6 slices (each slice ≤ 400) — `[MODIFIED 2026-09-19 efficiency extension]` ~2100-2500 across 8 PRs (06, 07, 08, 09a, 09b, 10, 11; PR 10 may split into 10/10b), target ≤ 400 changed lines per PR |
| 400-line budget risk | High (as a single PR) / Low per slice — `[MODIFIED]` Low-Medium per slice, except PR 08 (Medium-High) and PR 10 (High, pre-planned split point, see units 8 and 10) |
| Chained PRs recommended | Yes |
| Suggested split | PR 06 → PR 07 → PR 08 → PR 09 → PR 10 → PR 11 — `[MODIFIED]` PR 06 → PR 07 → PR 08 → **PR 09a → PR 09b** → PR 10 (→ PR 10b if it crosses 400 lines) → PR 11 |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units (extension)

| Unit | Slice | Branch | Base (PR target) | Est. lines |
|------|-------|--------|------------------|-----------|
| 6 | 06 referencia contract | `feat/seguimiento-inspectores-depurado-06-referencia-contract` | `feat/seguimiento-inspectores-depurado-05-mobile-overflow` | ~200 |
| 7 | 07 engine universe | `feat/seguimiento-inspectores-depurado-07-engine-universe` | unit 6's branch | ~350-400 |
| 8 | 08 engine remap | `feat/seguimiento-inspectores-depurado-08-engine-remap` | unit 7's branch | ~300 `[MODIFIED]` ~400 with the D29 determinism/purity/idempotency tasks 9.16-9.23 (Medium-High; cut line: the perf canary and purity tests 9.22-9.23 may move to a follow-up commit if the PR crosses 400) |
| 9 | 09 router — **re-labelled 09a** (versioned caches, single-flight, referencia outside the lock, `huella`, opt-in `?depuracion=1`, degraded gate and PII tasks as already specified) | `feat/seguimiento-inspectores-depurado-09-router` (unchanged name) | unit 8's branch | ~150 `[MODIFIED]` ~380-400 (Medium; cut line: task 10.12 becomes its own fix PR if 09a crosses 400) |
| 9b | **09b router HTTP efficiency** (encoded-bytes cache, ETag/304, gzip, CORS headers, budget tests 1-9 and 11) — a SEPARATE PR from 09a `[ADDED]` | `feat/seguimiento-inspectores-depurado-09b-router-http` | unit 9 (09a)'s branch | ~300-350 (Medium) |
| 10 | 10 frontend | `feat/seguimiento-inspectores-depurado-10-frontend` | `[MODIFIED]` unit 9b's branch (was unit 9's) | ~350-400 `[MODIFIED]` ~470-520 with the efficiency tasks 11.21-11.31 (High). Pre-planned split: tasks 11.1-11.20 are one work-unit commit group, 11.21-11.31 a second; if the PR crosses 400 lines it splits at that boundary into PR 10 and PR 10b (10b's base = PR 10's branch) |
| 11 | 11 parity harness | `feat/seguimiento-inspectores-depurado-11-parity-harness` | unit 10's branch (or 10b's when split) | ~150 `[MODIFIED]` ~250 with the determinism/timing check and the read ledger (Low-Medium) |

Tracker branch: `feat/seguimiento-inspectores-depurado` — the ONLY branch that merges to `main`.
If a child PR's diff shows a previous slice's changes, its base is wrong: retarget/rebase before review.
`SEGUIMIENTO_DEPURACION` stays unset/`0` through every slice; it is flipped only after Phase 12 passes.
`[MODIFIED 2026-09-19 efficiency extension]` It is flipped only after Phase 12 passes AND slices 09a, 09b and 10 are merged AND the budget tests (Phase 10b) are green AND the measured read ledger is within the budget ratified under design O1 (see 12b.4). Source: `efficiency-extension.md`, `design.md` D21-D32.

## Phase 7: Reference Contract — Optional Fields (PR 06)

Slice forecast: ~200 lines · risk Low · base `…-05-mobile-overflow`.

- [x] 7.1 RED: `backend/tests/services/test_inspectores_referencia.py::test_parse_entrada_new_optional_fields` — a `main` row with `nombre`/`telefono`/`codigo`/`creado_en`/`id`/`correo`/`tarjeta_profesional` exposes all seven.
- [x] 7.2 GREEN: add the seven optional fields with empty defaults to `EntradaReferencia` and `_parse_entrada` in `backend/app/services/inspectores_referencia.py`.
- [x] 7.3 RED+GREEN edge: `test_parse_bundle_old_bundle_yields_empty_contact_fields` — a `schema:1` bundle without the new keys parses to entries with all seven empty, `activa=true`.
- [x] 7.4 RED+GREEN edge: `test_parse_bundle_wrong_typed_optional_fields` — numeric `telefono`, `null` `creado_en`, list-valued `correo` → coerced to string / empty, never raises.
- [x] 7.5 RED+GREEN edge: `test_parse_bundle_schema_2_still_none` — regression guard that `schema` stays `1` and an unknown value still returns `None` (D15).
- [x] 7.6 RED+GREEN edge: `test_parse_bundle_vercel_fase2_sections_accept_new_fields_too` — the optional fields are accepted (and ignored when absent) on all three sections, not only `main`.
- [x] 7.7 RED: `backend/tests/test_publicar_referencia_inspectores.py::test_cli_emits_optional_fields` — the produced JSON carries the seven fields for a fixture source row.
- [x] 7.8 GREEN: emit the seven fields from `scripts/publicar_referencia_inspectores.py`.
- [x] 7.9 RED+GREEN edge: `test_cli_preserves_leading_zeros_and_long_numerics` — `codigoInspector="041"`, 10-digit cédula, 11-digit `telefono` survive as exact strings (guards the `ccbc71e` string-dtype fix).
- [x] 7.10 RED+GREEN edge: `test_cli_missing_optional_columns_yield_empty_not_nan` — a source file lacking `correo`/`matricula` produces `""`, never `"nan"`/`"None"`.
- [x] 7.11 REFACTOR: extract the per-field coercion into one helper; assert no behavior change by re-running `python -m pytest backend/tests/services/test_inspectores_referencia.py backend/tests/test_publicar_referencia_inspectores.py -q`.
- [ ] 7.12 Gate: `python -m pytest backend/tests/ -q` green; open PR 06 against `…-05-mobile-overflow`.

## Phase 8: Engine — Full Universe (PR 07)

Slice forecast: ~350-400 lines · risk Medium · base PR 06's branch.

- [x] 8.1 RED: `test_fusionar_identidad_main_row_creates_perfil` — a `main` `cedula_key` with no roster entry produces a profile (D9).
- [x] 8.2 GREEN: add the `referencia.main` pass after the roster loop in `fusionar_identidad`, keyed by `cedula_key`.
- [x] 8.3 RED+GREEN: `test_fusionar_identidad_firestore_first_main_backfills_empty_only` — matching key → one profile, Firestore `nombre` kept, empty `tarjeta_profesional` backfilled.
- [x] 8.4 RED+GREEN edge: `test_fusionar_identidad_main_duplicate_cedula_first_wins` — two `main` rows share a key → first wins, no overwrite, `revision_manual` gets `cedula_duplicada_main` (D19).
- [x] 8.5 RED+GREEN edge: `test_fusionar_identidad_main_row_without_cedula_not_dropped_silently` — no digits → no profile, `revision_manual` gets `main_sin_cedula`.
- [x] 8.6 RED+GREEN edge: `test_fusionar_identidad_empty_nombre_survives` — a profile with an empty `nombre_norm` is created and never unified against another empty name.
- [x] 8.7 RED: `test_atribuir_stickers_resolves_through_alias_index` — a sticker cédula matching a unified-away key lands on the survivor (D11).
- [x] 8.8 GREEN: build the `cedula_key` alias index (current + pre-fix + `cedulas_unificadas`) and route sticker attribution through it, replacing the exact-string `perfiles.get(identificacion)`.
- [x] 8.9 RED+GREEN edge: `test_atribuir_stickers_unknown_cedula_is_noop` — an unresolvable cédula changes no profile and raises nothing.
- [x] 8.10 RED+GREEN edge: `test_atribuir_stickers_normalizes_leading_zeros` — `"0012345"` and `"12345"` resolve to the same profile.
- [x] 8.11 RED: `test_unificar_duplicados_runs_after_overlays_scores_populated_flags` — a D-P2 pair where only one side is in Vercel and only the other has stickers → the sticker-bearing profile survives and inherits `en_vercel`/`np`/`codigo` (D10).
- [x] 8.12 GREEN: move `unificar_duplicados` after the overlays, the cédula fix and sticker attribution; register every loser key in `cedulas_unificadas`; backfill empty fields from the first non-empty loser.
- [x] 8.13 RED+GREEN edge: `test_unificar_duplicados_tiebreak_creado_en_then_firestore_backed` — full score tie → oldest `creado_en` wins; equal/empty `creado_en` → the Firestore-backed profile wins.
- [x] 8.14 RED+GREEN edge: `test_unificar_duplicados_absorbs_loser_sticker_aggregates` — survivor's `n_stickers`/`ultimo_sticker` reflect both sides.
- [x] 8.15 RED: `test_entidad_precedence_vercel_by_cedula_then_firestore_then_main` (D12).
- [x] 8.16 GREEN: implement the `entidad` resolver.
- [x] 8.17 RED+GREEN edge: `test_entidad_name_only_vercel_match_does_not_supply_entidad` and `test_entidad_absent_everywhere_is_empty_string`.
- [x] 8.18 REFACTOR: replace `_buscar_entrada`'s linear scan with `cedula_key` and `nombre_norm` dict indexes built once per `depurar()` call (D14); keep the public signature unchanged.
- [x] 8.19 RED+GREEN: `test_depurar_universe_row_count_grows_without_duplicates` — full-pipeline assertion that every emitted `identidad_key` is unique and the count equals `|roster ∪ main|` minus unified and collapsed rows.
- [ ] 8.20 Gate: `python -m pytest backend/tests/ -q` green; open PR 07 against PR 06's branch.

**GATING NOTE (slice 07 -> 08, review 2026-09-19 W3)**: main `codigo` now backfills EMPTY Firestore codes, but slice 08 (clear the wrong holder, tasks 9.1-9.6, D13) is not in yet, so two profiles can hold the same `codigo` (pinned by `test_known_state_until_slice_08_two_profiles_can_hold_the_same_codigo`, to be replaced in slice 08). `SEGUIMIENTO_DEPURACION` MUST NOT be flipped to `1` before slice 08 lands.

**Found/fixed during this pass (beyond the literal task list)**: `fusionar_identidad` now returns `(perfiles, revision_manual)` (was `perfiles`) so `cedula_duplicada_main`/`main_sin_cedula` can surface; `_cedula_key` is the single identity key (digits-only, leading zeros KEPT to mirror the frontend's `cedulaKey`, a lone `.0` float artifact dropped, thousands-separated dots are just dots — the shared `cedula_utils` rule, review 2026-09-19 C3; the zero-stripped form survives only as the sticker alias-index fallback, review 2026-09-19 C1) and `cedula_sospechosa` reads the same digits (divergence D-CEDDEC); `n_stickers` is now SUMMED into the survivor (earlier "not summed" test replaced deliberately: no parity column depends on the count); `num_telefono` from `main` is digits-only (notebook); the alias-nombres INFO log is count-only. Two existing tests changed on purpose (unification moved out of stage 1; sticker-count absorption); no parity test changed.

## Phase 9: Engine — Remap And Cédula Fix (PR 08)

Slice forecast: ~300 lines · risk Medium · base PR 07's branch. `[MODIFIED 2026-09-19]` ~400 lines with tasks 9.16-9.23 · risk Medium-High · target ≤ 400 (cut line in the units table).

- [ ] 9.1 RED: `test_remapear_codigos_clears_wrong_current_holder` — Vercel gives `"041"` to A while B holds it → B's `codigo` becomes `""`, A's becomes `"041"` (D13).
- [ ] 9.2 GREEN: clear the código from the wrong holder before assigning it in `remapear_codigos`.
- [ ] 9.3 RED+GREEN edge: `test_remapear_codigos_sin_duenio` — the rightful owner has no profile → the wrong holder is still cleared and `revision_manual` gets `remap_sin_duenio`.
- [ ] 9.4 RED+GREEN edge: `test_remapear_codigos_conflicto` — two Vercel-registered owners resolve to one profile → `codigo=""` and `revision_manual` gets `remap_conflicto`.
- [ ] 9.5 RED+GREEN edge: `test_remapear_codigos_duplicado_vercel_still_excluded` — regression guard that `codigos_duplicados` (D-P1) still skips remap entirely and emits `codigo_vercel_duplicado`.
- [ ] 9.6 RED+GREEN edge: `test_remapear_codigos_idempotent` — running the remap twice over its own output changes nothing and emits no duplicate revision entries.
- [ ] 9.7 RED: `test_fase2_cedula_fix_name_only_match_keeps_cedula_sospechosa_from_original` (D18).
- [ ] 9.8 GREEN: apply the Fase 2 cédula to name-only matches, keep `cedula_sospechosa` from the original `main` cédula, and register the original key in the alias index.
- [ ] 9.9 RED+GREEN edge: `test_fase2_cedula_fix_preserves_previously_attributed_stickers` — aggregates attributed under the old key survive the fix.
- [ ] 9.10 RED+GREEN edge: `test_fase2_cedula_fix_not_applied_on_cedula_match` — when the match was by cédula, no rewrite happens.
- [ ] 9.11 RED+GREEN edge: `test_fase2_cedula_fix_collision_with_existing_perfil` — the fixed cédula already belongs to another profile → no silent merge; the pair goes to `revision_manual`.
- [ ] 9.12 RED+GREEN: `test_colapsar_externos_cedula_sospechosa_or_no_persona` — `cedula_sospechosa` alone, inactive and code-less, collapses; the `dias_inactivo == 7` / `8` boundary and the código exclusion still hold.
- [ ] 9.13 RED+GREEN: `test_grupo_externos_row_shape` — `np_fuente="ninguno"`, `fase="Fase I"`, `fase_np_faltante=true`, `activo=false`, `n_colapsados` present.
- [ ] 9.14 REFACTOR: collapse the remap and cédula-fix revision emitters into one `_revision(motivo, **detalle)` helper; re-run the engine suite.
- [ ] 9.15 Gate: `python -m pytest backend/tests/ -q` green; open PR 08 against PR 07's branch. `[MODIFIED 2026-09-19]` Also requires tasks 9.16-9.23 green before the PR is opened.

### Efficiency extension 2026-09-19 — engine determinism, purity, idempotency (D29, PR 08)

Source: `efficiency-extension.md` "Slice mapping" (07/08 engine tasks, the doc's 8.21-8.24 equivalents, numbered here in Phase 9's own sequence) and `design.md` D29. The signature stays `(stickers, roster_by_cedula, nombres_survey, referencia, hoy)`; the router owns the fingerprints, the engine stays pure. STRICT TDD applies: if a RED test already passes on the current engine, keep it as a characterization test and record that in the apply-progress artifact — never delete it. Edge cases are mandatory, not optional.

- [ ] 9.16 RED: `test_depurar_result_independent_of_roster_and_sticker_order` — a fixed seed set (at least 25 permutations) of the roster dict order and the sticker list order over the full-universe fixture yields a structurally identical `Depuracion` (inspectores, grupo_externos, alias_nombres, revision_manual, cedulas_unificadas). `referencia` row order is NOT permuted (it is part of the input, design D29 / C-E4).
- [ ] 9.17 GREEN: emit canonical order (`inspectores` by `identidad_key`; `revision_manual` by `(motivo, codigo, identidad_key)`; `grupo_externos.detalle` by `identificacion`) and iterate in sorted order wherever a first-wins choice depends on iteration (`alias_nombres` collisions, fuzzy-remap candidates, unification groups). Verify `web/js/seguimiento.js` does not rely on the old emission order (grep + one Node assertion), and record the result.
- [ ] 9.18 RED+GREEN edge: `test_unificar_duplicados_terminal_tiebreak_identidad_key_when_creado_en_blank` — two same-name profiles with an equal survivor score, blank `creado_en` on both and both Firestore-backed: the lower `identidad_key` survives in BOTH input orders. Companion regression guard `test_unificar_duplicados_firestore_backed_still_outranks_identidad_key` (D10 unchanged): with exactly one Firestore-backed side, that side wins even when its key is higher. A blank `creado_en` never beats a real date (D-SURVFECHA unchanged).
- [ ] 9.19 RED+GREEN edge: `test_alias_nombres_collision_winner_is_order_independent`, `test_revision_manual_emission_is_canonical_and_free_of_duplicates`, `test_depurar_empty_inputs_are_deterministic` (empty roster/stickers/survey/referencia → identical empty result on every call), and `test_main_duplicate_cedula_winner_follows_bundle_order_only` (permuting the BUNDLE order may change the D19 first-wins winner; permuting roster/stickers never does).
- [ ] 9.20 RED+GREEN: `test_depurar_does_not_mutate_inputs` — deep-copy the stickers, the roster dicts and the survey names before the call and assert equality after; the frozen `ReferenciaBundle` is untouched; a generator and a set as `nombres_survey` both work (materialized once, not consumed twice).
- [ ] 9.21 RED+GREEN edge: `test_depurar_result_does_not_alias_inputs` — mutate every list/dict in the returned `Depuracion`, then re-run on the same inputs and assert the second result equals the first pristine result and the inputs are unchanged (cache-safety of D23).
- [ ] 9.22 RED+GREEN: `test_depurar_reads_no_clock_env_or_module_state` — patch `date.today`, `datetime.now`, `time.time` and `os.environ` to raise, and snapshot module-level mutable state before/after (two interleaved calls with different inputs must not leak into each other); `hoy` is the ONLY date source (two different `hoy` values change only `dias_inactivo`-dependent fields and collapse membership).
- [ ] 9.23 RED+GREEN: `test_depurar_idempotent_same_inputs_equal_result` and `test_depurar_perf_canary_373_profiles_3000_stickers_under_500ms` — best of N runs (N ≥ 5) on a synthetic 373-profile / 3,000-sticker fixture stays < 0.5 s (baseline ≈ 0.26 s); edge variant with all 3,000 stickers unattributable (worst-case fuzzy remap) stays under the same budget. The failure message prints the measured time.

## Phase 10: Router — Degraded Gate And PII (PR 09) — `[MODIFIED 2026-09-19]` re-labelled PR 09a

PR 09 is SPLIT (efficiency extension, `efficiency-extension.md` "Slice mapping"): **PR 09a** = this phase (tasks 10.1-10.43: the existing degraded gate and PII tasks 10.1-10.12 as specified, plus the versioned caches, single-flight, referencia outside the lock, `huella` and the opt-in `?depuracion=1` parameter in 10.13-10.43); **PR 09b** = Phase 10b below (ETag/304/gzip/bytes cache/CORS and the budget tests). Existing task text and checkbox states of 10.1-10.12 are unchanged; only the annotations on 10.8, 10.9 and 10.11 are new. Branch name stays `…-09-router`; 09b is `…-09b-router-http` with 09a's branch as its base.

Slice forecast: ~150 lines · risk Low · base PR 08's branch. `[MODIFIED]` ~380-400 lines with 10.13-10.43 · risk Medium · target ≤ 400 (cut line: 10.12 becomes its own fix PR if 09a crosses 400).

- [ ] 10.1 RED: `backend/tests/routers/test_stickers_atencionsismo.py::test_depuracion_gated_off_when_snapshot_degraded` — degraded LKG restore → `activa=false`, `motivo="stickers_degradados"`, empty `inspectores`, HTTP 200 (D16).
- [ ] 10.2 GREEN: add the `cache.degraded` gate before computing `depuracion` in `backend/app/routers/stickers_atencionsismo.py`.
- [ ] 10.3 RED+GREEN edge: `test_depuracion_still_computed_when_only_referencia_missing` — reference unreadable but stickers live → a table is still produced with degraded `np_fuente`.
- [ ] 10.4 RED+GREEN edge: `test_depuracion_absent_for_viewer_role` and `test_depuracion_present_with_contact_fields_for_admin` — admin vs viewer.
- [ ] 10.5 RED+GREEN edge: `test_depuracion_pii_never_reaches_blob_persisted_evaluaciones_full_universe` — extend the existing capture-the-bytes test to the full universe with `telefono`/`tarjeta_profesional` injected.
- [ ] 10.6 RED: `test_alias_nombres_info_log_is_redacted` — caplog at INFO contains neither a name nor a cédula.
- [ ] 10.7 GREEN: redact or downgrade the `alias_nombres` INFO log.
- [ ] 10.8 RED+GREEN: `test_depuracion_payload_size_within_budget` — serialized `depuracion` for a 400-profile fixture stays under the documented ceiling (assert an explicit byte budget, fail loudly above it). `[NOTE 2026-09-19]` the ceiling is now fixed by design's Efficiency Acceptance Budgets: 400 KB raw for 400 profiles (route-level `evaluaciones` ≤ 4 MB and gzip checks live in 10b.25).
- [ ] 10.9 RED+GREEN edge: `test_depuracion_cache_still_keyed_by_snapshot_identity_with_full_universe` — same list object → no recompute; new `hoy`/`generado_en` → recompute (D4/D6 regression guard). `[RE-LABELLED 2026-09-19, D23 supersedes D4]` "snapshot identity" now means the content-stable `snapshot_version` and "`generado_en`" means the referencia `huella`; the observable behavior asserted here is unchanged (same content → no recompute; new `hoy` or new reference content → recompute). The `is`-identity mechanism itself is replaced by tasks 10.33-10.34, and this test is re-pointed at the new key when those land (it must stay green throughout).
- [ ] 10.10 RED+GREEN edge: `test_flag_off_response_still_byte_identical_with_full_universe` — `SEGUIMIENTO_DEPURACION` unset → the 4-key body shape.
- [ ] 10.11 Gate: `python -m pytest backend/tests/ -q` green; open PR 09 against PR 08's branch. `[RE-LABELLED 2026-09-19]` this PR is now PR 09a; the final gate for 09a is 10.43 (which subsumes this line — do not open PR 09a before 10.13-10.42 are green).
- [ ] 10.12 RED+GREEN (follow-up, D-CEDDEC known limitation): route `backend/app/services/stickers_atencionsismo.py::cedula_key` through `cedula_utils.solo_digitos`, with a test on the float-artifact shape (roster `identificacion` "1234567.0" keys as "1234567" in `inspector_profile_by_identificacion` and `same_cedula_match`, not "12345670").

### Efficiency extension 2026-09-19 — versioned caches, single-flight, `huella`, opt-in (D21-D25, PR 09a)

Source: `efficiency-extension.md` designs A, B, C, E and "Slice mapping" (09a), `design.md` D21-D25, the `datos-referencia-blob`, `inspectores-depurado` and `seguimiento` efficiency deltas. Tests use fakes with call counters and an INJECTABLE clock (no real sleeps); the counting fakes are created once in 10.13 and reused by 09b. STRICT TDD applies; a RED that already passes is kept as a characterization test and recorded. Edge cases are part of the task, not extras.

Content-stable snapshot (D21):

- [ ] 10.13 RED: `backend/tests/routers/test_stickers.py::test_snapshot_version_stable_when_refetch_content_identical` (using new shared counting fakes + injectable clock) — two refetches returning equal content keep the SAME list object and `snapshot_version`.
- [ ] 10.14 GREEN: in `EvaluacionesCache` (`backend/app/routers/stickers.py`), hash the full unredacted served payload with `blob_lkg.payload_hash` after each fetch; equal → keep object and version, else bump. The redacted-copy hash that gates the Blob PUT is left untouched.
- [ ] 10.15 RED+GREEN edge: `test_snapshot_version_bumps_on_one_changed_sticker` and `test_snapshot_version_bumps_on_np_only_change_while_blob_put_stays_gated` — an `inspector.np`-only change bumps the version (it feeds classification) yet the redacted-copy hash, and therefore the Blob PUT count, is unchanged.
- [ ] 10.16 RED+GREEN edge: `test_snapshot_version_includes_degraded_flag` (identical content but `degraded` toggled → different version) and `test_snapshot_version_row_order_change_bumps` (documented conservative behavior: one extra recompute, never an error).
- [ ] 10.17 RED+GREEN edge: `test_snapshot_failed_refetch_keeps_previous_object_and_version` (serve-stale, no bump) and `test_snapshot_hashed_payload_has_no_volatile_fields` (a per-fetch timestamp inside the hashed payload would make the version change on every fetch — assert none exists, else exclude it from the hash).

Versioned component caches (D22):

- [ ] 10.18 RED: `backend/tests/services/test_versioned_cache.py::test_single_fetch_within_ttl_and_version_stable_on_identical_refetch` — fake clock: N reads inside the TTL → 1 fetch; past the TTL with identical content → 1 more fetch and the SAME version; changed content → new version. Boundary: exactly at TTL is still fresh, TTL + 1 s is stale.
- [ ] 10.19 GREEN: create the generic TTL + content-version + single-flight primitive (proposed `backend/app/services/versioned_cache.py`); `version` = `blob_lkg.payload_hash` of the content.
- [ ] 10.20 RED+GREEN edge: `test_fetch_error_serves_last_good_with_same_version`, `test_failure_backoff_no_hot_retry` (reuses the `failure_backoff_s` convention), `test_cold_start_error_propagates_as_before`, `test_invalidate_forces_one_refetch_but_keeps_last_good_on_failure`.
- [ ] 10.21 RED+GREEN edge: `test_20_threads_at_expiry_one_fetch_behind_barrier` and `test_slow_fetch_of_one_component_does_not_block_another_components_fast_path` (each component owns its lock).
- [ ] 10.22 RED+GREEN: wire the roster (30 min), survey names (60 min, `nombre_evaluador` only) and evaluaciones Firestore side (15 min) components in `backend/app/routers/stickers_atencionsismo.py`; `test_component_ttls_static_inputs_one_hour` — with a continuously open tab and static inputs, one simulated hour yields ≤ 2 roster, ≤ 1 survey and ≤ 4 evaluaciones scans.
- [ ] 10.23 RED+GREEN: `test_roster_scanned_once_per_compute_window` — `_compute` and `build_payload` share one roster snapshot (audit waste #5).
- [ ] 10.24 RED+GREEN edge: `test_admin_create_and_set_enabled_invalidate_roster_component` — the next request after either write costs exactly +1 roster scan; identical content keeps the version and costs 0 recompute; changed content costs exactly 1.

`huella` (D24, `datos-referencia-blob` delta):

- [ ] 10.25 RED: `backend/tests/services/test_inspectores_referencia.py::test_huella_identical_for_identical_content_and_json_key_order` — `ReferenciaBundle.huella` = `payload_hash` of the parsed raw JSON, so object-key order does not change it.
- [ ] 10.26 GREEN: add `huella: str = ""` to `ReferenciaBundle`, computed in `parse_bundle` (never published; `vacia()` keeps `""`).
- [ ] 10.27 RED+GREEN edge: `test_huella_changes_on_same_day_republish` (same `generado_en`, one changed row), `test_huella_changes_when_only_an_optional_contact_field_changes`, `test_huella_empty_for_degraded_bundle_and_never_equals_a_real_one`, `test_old_bundle_without_any_huella_key_still_parses`.

Referencia outside the lock (D24):

- [ ] 10.28 RED: `test_slow_cargar_referencia_does_not_block_a_concurrent_fast_path_request` — a loader blocked on an event; a second thread with an already-fresh result returns without waiting.
- [ ] 10.29 GREEN: TTL check under the lock (O(1)); single-flight download outside it; other callers read the last-good bundle.
- [ ] 10.30 RED+GREEN edge: `test_concurrent_stale_callers_produce_one_referencia_get` and `test_loader_exception_never_leaves_lock_or_marker_held`.
- [ ] 10.31 RED+GREEN edge: `test_referencia_failure_keeps_last_good_without_recompute_and_retries_once_per_ttl`, `test_cold_start_failure_adopts_degraded_bundle_retried_once_per_ttl_not_per_request`, `test_recovery_with_identical_content_causes_no_recompute` (equal `huella`).
- [ ] 10.32 RED+GREEN: `test_referencia_get_at_most_two_per_hour_with_fifty_requests` (fake clock; no HEAD/list call recorded).

`DepuracionCache` key and single-flight (D23, supersedes D4):

- [ ] 10.33 RED: `test_depuracion_cache_recomputes_once_per_changed_fingerprint` — each of `snapshot_version`, `roster_version`, `survey_version`, referencia `huella` and `hoy` changing alone → exactly +1 compute; identical fingerprints after TTL expiry → +0.
- [ ] 10.34 GREEN: replace the object-identity key with the 5-tuple; keep only the current key plus last-good.
- [ ] 10.35 RED: `test_depuracion_single_flight_20_threads_one_compute` — 20 threads released by a barrier at the same key → exactly 1 `depurar` and 1 set of scans, all 20 get an equal result.
- [ ] 10.36 GREEN: in-flight marker; the first caller computes outside the lock, the rest wait on the marker.
- [ ] 10.37 RED+GREEN edge: `test_leader_exception_clears_marker_waiters_do_not_deadlock_next_request_retries` and `test_waiters_get_last_good_when_leader_fails`.
- [ ] 10.38 RED+GREEN edge: `test_hoy_rollover_costs_exactly_one_compute_and_no_scans` and `test_two_different_keys_do_not_share_a_marker`.

Opt-in `?depuracion=1` (D25):

- [ ] 10.39 RED: `test_depuracion_absent_and_zero_work_without_query_param` — flag on, admin, live snapshot, NO param → no `depuracion` key, 0 `depurar`, 0 survey scan, 0 referencia GET.
- [ ] 10.40 GREEN: accept exactly `depuracion=1` in `get_stickers_atencionsismo`; compose with the flag, role and D16 gates.
- [ ] 10.41 RED+GREEN edge: parametrized values `0`, `true`, `yes`, empty, `1 ` (trailing space), `01`, repeated `depuracion=1&depuracion=0` → not opted in; flag OFF + `?depuracion=1` → no key and 0 work; viewer + `?depuracion=1` → no key and 0 work (the existing 10.4 role assertions stay green).
- [ ] 10.42 RED+GREEN edge: `test_non_opt_in_body_byte_identical_to_flag_off_shape` — compare the encoded body against a golden captured from the pre-extension serialization.
- [ ] 10.43 Gate (PR 09a): `python -m pytest backend/tests/ -q` green including 10.1-10.42; open PR 09a against PR 08's branch. Do NOT bundle Phase 10b into this PR.

## Phase 10b: Router — HTTP Efficiency And Budget Tests (PR 09b) — `[ADDED 2026-09-19]`

Slice forecast: ~300-350 lines · risk Medium · target ≤ 400 · base PR 09a's branch (`feat/seguimiento-inspectores-depurado-09-router`), branch `feat/seguimiento-inspectores-depurado-09b-router-http`. A SEPARATE PR from 09a: 09a changes what is computed and read, 09b changes how the response is encoded and validated. Source: design D26-D27, efficiency doc "Tests that enforce the budgets" 1-9 and 11 (10 lives in Phase 9, 12 in Phase 11). The 09a unit tests already cover the mechanisms; the budget tests below assert COUNTS at the route level with the shared ledger fakes (call counters for walk, evaluaciones scan, roster scan, survey scan, `depurar`, referencia GET, Blob PUT) and an injectable clock, and must not re-test internals.

- [ ] 10b.1 RED: `backend/tests/routers/test_stickers_atencionsismo_http.py::test_encoded_body_serialized_once_per_key` — 50 identical requests → 1 body serialization.
- [ ] 10b.2 GREEN: encoded-bytes cache keyed `(snapshot_version, depuracion_version, role, degraded, encoding)`; only the current key retained.
- [ ] 10b.3 RED+GREEN edge: `test_bytes_cache_keys_are_isolated_by_role_degraded_and_opt_in` — admin bytes are never served to a viewer, degraded bytes never to a live snapshot, opt-in bytes never to a non-opt-in request.
- [ ] 10b.4 RED: `test_if_none_match_equal_returns_304_empty_body_with_etag`.
- [ ] 10b.5 GREEN: ETag = quoted truncated sha256 of the key (+ encoding suffix); 304 with an empty body and the ETag header.
- [ ] 10b.6 RED+GREEN edge: `If-None-Match` as a list, weak (`W/"…"`), `*`, garbage and empty → correct 304/200; mismatch → 200; unauthenticated → 401 (never 304); a viewer sending the admin's ETag → 200 with the viewer body.
- [ ] 10b.7 RED+GREEN: `test_gzip_precompressed_once_per_key` — `Accept-Encoding: gzip` → `Content-Encoding: gzip`, decompressed bytes equal the identity bytes, compression ran once for N requests, `Vary: Accept-Encoding` present.
- [ ] 10b.8 RED+GREEN edge: absent header, `identity`, `gzip;q=0` → identity body; the ETag differs per representation and a 304 works inside each; an `evaluaciones: []` payload compresses and round-trips.
- [ ] 10b.9 RED+GREEN edge: `Cache-Control: no-store` and `Vary: Authorization, Accept-Encoding` on both 200 and 304.
- [ ] 10b.10 RED: `test_cors_preflight_allows_if_none_match_and_exposes_etag` — `OPTIONS` with `Access-Control-Request-Headers: if-none-match` passes; the actual response carries `Access-Control-Expose-Headers: ETag`.
- [ ] 10b.11 GREEN: `CORS_ALLOW_HEADERS += "If-None-Match"` in `backend/app/config.py`; add `expose_headers=["ETag"]` to the `CORSMiddleware` call in `backend/app/main.py`.
- [ ] 10b.12 RED+GREEN edge: `Authorization` and `Content-Type` remain allowed, a disallowed origin is still rejected, and only `ETag` is exposed.
- [ ] 10b.13 Browser verification (record the result in the PR): with the backend on one local origin and a static harness page on another (cross-origin, Playwright/Chromium as in 5b.2), the preflight for `If-None-Match` passes, `response.headers.get("ETag")` is readable and a manual conditional GET returns 304.
- [ ] 10b.14 Extend the 10.13 shared fakes with Blob-PUT and referencia-GET counters (counts only, no payload capture).
- [ ] 10b.15 RED+GREEN budget test 1: `test_budget_fifty_requests_within_ttl` — 1 walk, 1 evaluaciones scan, 1 roster scan, 1 survey scan, 1 `depurar`, 1 referencia GET, ≤ 2 Blob PUTs at cold start. Edge: at exactly the TTL boundary the value is still fresh.
- [ ] 10b.16 RED+GREEN budget test 2: `test_budget_past_ttl_identical_upstream` — +1 walk, +0 `depurar`, +0 survey/roster scans, +0 Blob PUT, the SAME ETag, and a conditional request → 304 empty body. Edge: TTL + 1 s is stale for the 5-minute walk but the 15/30/60-minute components stay fresh.
- [ ] 10b.17 RED+GREEN budget test 3: `test_budget_one_changed_sticker` — exactly +1 `depurar` and a new ETag.
- [ ] 10b.18 RED+GREEN budget test 4: `test_budget_hoy_rollover_only_recomputes` — +1 `depurar` and 0 extra scans/GETs/PUTs.
- [ ] 10b.19 RED+GREEN budget test 5: `test_budget_new_referencia_huella_incl_same_day_republish` — +1 `depurar`; an identical re-download → +0.
- [ ] 10b.20 RED+GREEN budget test 6: `test_budget_20_threads_at_expiry` — barrier → 1 walk, 1 `depurar`, ≤ 1 survey scan, ≤ 1 roster scan, all responses equal.
- [ ] 10b.21 RED+GREEN budget test 7: `test_budget_slow_cargar_referencia_does_not_block_fast_path` at route level.
- [ ] 10b.22 RED+GREEN budget test 8: `test_budget_referencia_failure_keeps_last_good` — no recompute, ≤ 1 retry per TTL, the response still carries the last-good `depuracion`.
- [ ] 10b.23 RED+GREEN budget test 9: `test_budget_viewer_or_missing_param_costs_zero` — 0 `depurar`, 0 survey scan, 0 referencia GET, 0 depuración bytes.
- [ ] 10b.24 RED+GREEN budget test 11 (encoding): `test_budget_gzip_content_encoding_when_allowed` (covers 10b.7-10b.8 at route level with the ledger).
- [ ] 10b.25 RED+GREEN budget test 11 (ceilings): `evaluaciones` ≤ 4 MB raw and `depuracion` ≤ 400 KB raw for a 400-profile fixture (boundary: exactly the ceiling passes, one byte over fails loudly; complements 10.8); gzip is smaller than raw and the measured ratio is printed (target ≈ 15%, recorded at parity, not asserted).
- [ ] 10b.26 RED+GREEN latency: over ≥ 50 in-process samples, p95 ≤ 50 ms for a 304 and ≤ 250 ms for a 200 served from precomputed bytes.
- [ ] 10b.27 RED+GREEN: `test_blob_put_zero_when_content_unchanged_and_exactly_one_when_changed` (0 PUTs on identical refetch; 1 PUT on a redacted-content change; fire-and-forget failure never fails the request).
- [ ] 10b.28 Gate (PR 09b): `python -m pytest backend/tests/ -q` green; open PR 09b against PR 09a's branch. The budget tests are the flag-flip evidence and MUST be green in CI, not only locally.

## Phase 11: Frontend — Seeding, KPIs, Filter, Export (PR 10)

Slice forecast: ~350-400 lines · risk Medium · base PR 09's branch. Ships with the flag still off. `[MODIFIED 2026-09-19]` base = PR 09b's branch (the chain is 09a → 09b → 10); ~470-520 lines with the efficiency tasks 11.21-11.31 · risk High · target ≤ 400: tasks 11.1-11.20 and 11.21-11.31 are two work-unit commit groups and the PR splits at that boundary into PR 10 and PR 10b if it crosses 400 (10b's base = PR 10's branch; 10b then carries the Node tests for retention and the browser check).

- [ ] 11.1 RED: `web/js/seguimiento.test.mjs::test_rows_seeded_from_depuracion_inspectores` — a zero-activity person gets a row with zero counters (D17).
- [ ] 11.2 GREEN: seed rows from `depuracion.inspectores` in `web/js/seguimiento.js` when `depuracionActiva`, before the sticker/survey enrichment pass.
- [ ] 11.3 RED+GREEN edge: `test_sticker_for_unseeded_cedula_still_creates_row` — no record is dropped.
- [ ] 11.4 RED+GREEN edge: `test_legacy_path_unchanged_when_depuracion_absent_or_inactive` — byte-identical rows on both fallbacks.
- [ ] 11.5 RED: `test_kpis_use_rows_with_activity_as_denominator` — 373 seeded / 116 active → "profesionales activos" is 116 and averages divide by 116.
- [ ] 11.6 GREEN: split the KPI denominator from the seeded row count; surface the total separately.
- [ ] 11.7 RED+GREEN edge: `test_kpis_empty_range_no_nan` — a range with zero activity renders `0`/`—`, never `NaN`/`Infinity`.
- [ ] 11.8 RED+GREEN edge: `test_timeline_and_charts_ignore_zero_activity_rows` — no zero-height series is injected per seeded row.
- [ ] 11.9 RED: `test_estado_filter_narrows_table_and_composes_with_search`.
- [ ] 11.10 GREEN: add the `estado_sugerido` column and filter control, defaulting to "all".
- [ ] 11.11 RED+GREEN edge: `test_estado_filter_unknown_value_visible_under_all` — an unexpected `estado_sugerido` string is still listed.
- [ ] 11.12 RED: `test_mass_pdf_export_defaults_to_rows_with_activity` — 373 visible / 116 active → 116 reports, scope stated in the UI first.
- [ ] 11.13 GREEN: default the mass PDF export scope to rows with activity.
- [ ] 11.14 RED+GREEN edge: `test_mass_pdf_export_over_cap_refuses_instead_of_truncating` — >200 selected → explicit refusal naming the cap and the count, no partial batch.
- [ ] 11.15 RED+GREEN: `test_revision_manual_renders_new_motivos_with_name_and_cedula` and `test_revision_manual_unknown_motivo_shows_raw_string`.
- [ ] 11.16 RED+GREEN edge: `test_revision_manual_identity_detail_admin_only` — a non-admin payload renders the section without names/cédulas.
- [ ] 11.17 RED+GREEN: `test_degraded_banner_shown_with_motivo` and `test_active_shows_referencia_generada_en_no_banner`.
- [ ] 11.18 RED+GREEN: `web/js/seguimiento-perf.test.mjs::test_400_row_render_filter_sort_within_budget` and `test_refilter_not_quadratic`.
- [ ] 11.19 REFACTOR: extract the seeding + enrichment passes into one named builder so the legacy branch stays a single early return; re-run `node --test "web/js/*.test.mjs"`.
- [ ] 11.20 Gate: `node --test "web/js/*.test.mjs"` green; open PR 10 against PR 09's branch. Do NOT merge into the tracker before Phase 12 passes. `[MODIFIED 2026-09-19]` the base is PR 09b's branch, and the PR is opened only after 11.21-11.31 are green (or split at the 11.20/11.21 boundary, see the Phase 11 forecast). "Phase 12 passes" means slice 11's tests are green (12.10/12.21): only then are PR 10 and PR 11 merged into the tracker, BEFORE the flag flip (design Rollout Order, C-E6).

### Efficiency extension 2026-09-19 — opt-in, snapshot retention, skip-render (D25, D28, PR 10)

Source: `efficiency-extension.md` design E and F, budget test 12, `seguimiento` efficiency delta. Node tests use `node:assert` like the existing suites, fake `fetch`/clock, no network. STRICT TDD applies. The `snapshot_id` used below is the opaque ETag string (design D28, C-E3) — there is no body key.

- [ ] 11.21 RED: `web/js/seguimiento.test.mjs::test_seguimiento_fetch_sends_depuracion_param` — Seguimiento's request URL carries `depuracion=1`; edge: a base URL that already has a query string gets `&depuracion=1`, never a second `?`.
- [ ] 11.22 GREEN: add an opt-in option to `fetchEvaluacionesOnce(getToken, endpoint, opts)` in `web/js/stickers.js` (default off) and pass it ONLY from `web/js/seguimiento.js`; `tagFuente` surfaces the response ETag additively (existing full-object assertions in `stickers.test.mjs` updated deliberately, as in 4.x).
- [ ] 11.23 RED+GREEN (Node test 12b): `web/js/stickers.test.mjs::test_stickers_tab_does_not_request_depuracion` — the Stickers tab URL has no `depuracion` parameter, and `test_stickers_tab_ignores_depuracion_key_if_present` (defensive: an unexpected block in the response is dropped, never rendered).
- [ ] 11.24 RED (Node test 12a): `test_reopen_with_same_snapshot_id_skips_render` — a second open whose revalidation returns 304 (or an equal ETag) calls `render()` zero additional times.
- [ ] 11.25 GREEN: module-level retention of `{snapshot_id, stickers, depuracion}` in `web/js/seguimiento.js`; on open render from it immediately, then revalidate in the background with `If-None-Match`, skipping `render()` when unchanged.
- [ ] 11.26 RED+GREEN edge: first open (nothing retained) → full fetch, no `If-None-Match` sent; a changed ETag → exactly one re-render and the retained value replaced; a 304 when nothing is retained is treated as a full fetch, never a blank table.
- [ ] 11.27 RED+GREEN edge: revalidation failure (network error, 5xx, malformed JSON) keeps the retained render, shows no blank table and throws nothing; a 401/403 CLEARS the retained value (admin PII must not outlive the authorization).
- [ ] 11.28 RED+GREEN edge: sign-out or role change clears the retention; assert no write to `localStorage`/`sessionStorage`/IndexedDB ever contains the retained payload (memory only).
- [ ] 11.29 RED+GREEN edge: an unreadable ETag (header absent because CORS did not expose it) → a full render every time, no exception; two rapid opens produce ONE in-flight revalidation; a date-range or estado-filter change still re-renders even when the ETag is unchanged (skip-render only suppresses re-renders caused by an unchanged snapshot).
- [ ] 11.30 Browser verification (record the result in the PR): with the 09b backend on another origin, open Seguimiento twice in Chromium/Playwright and observe the second open sends `If-None-Match`, receives 304 and does not re-render; also observe the Stickers tab request has no `depuracion` parameter and no PII block in its response.
- [ ] 11.31 Gate: `node --test "web/js/*.test.mjs"` green including 11.21-11.29. This gate is in addition to 11.20, not a replacement.

## Phase 12: Parity Harness And Rollout (PR 11)

Slice forecast: ~150 lines · risk Low · base PR 10's branch. `[MODIFIED 2026-09-19]` ~250 lines with the determinism/timing check and the read ledger (12.11-12.21) · risk Low-Medium · target ≤ 400 · base PR 10's branch (PR 10b's when split).

- [ ] 12.1 RED: `backend/tests/services/test_inspectores_depuracion_parity.py::test_parity_register_is_explicit` — the divergence register (D7, D-REMAP, D-EXENTOS) is asserted key-by-key, not as an aggregate score (D20).
- [ ] 12.2 GREEN: create `scripts/parity_inspectores_depurado.py` — run `depurar()` over the snapshot, diff every acceptance column against `outputs/inspectores_depurado_seguimiento.xlsx`, print per-column mismatch key lists.
- [ ] 12.3 RED+GREEN: `test_parity_np_fase_codigo_entidad_identificacion_fuente_dato` — ≥99% on matched keys, mismatches enumerated.
- [ ] 12.4 RED+GREEN: `test_parity_contact_columns_where_source_non_empty` — `tarjeta_profesional`, `num_telefono`, `correo_contacto` ≥99%.
- [ ] 12.5 RED+GREEN: `test_parity_nombre_completo_through_normalizar_nombre`.
- [ ] 12.6 RED+GREEN edge: `test_parity_row_count_373_plus_enumerated_firestore_extras` — the 13 Firestore-only extras are listed by key, not absorbed into a tolerance.
- [ ] 12.7 RED+GREEN edge: `test_parity_n_colapsados_252_with_three_codigo_exclusions` — the 3 código-holding exclusions are named; any D-EXENTOS survivor is listed by key.
- [ ] 12.8 RED+GREEN edge: `test_parity_harness_fails_loudly_on_missing_xlsx` — the script exits non-zero with a clear message rather than reporting 100%.
- [ ] 12.9 Document the rollout runbook (deploy → republish → live parity → flip flag → merge chain) in the PR description, mirroring `design.md`'s Rollout Order.
- [ ] 12.10 Gate: `python -m pytest backend/tests/ -q` and `node --test "web/js/*.test.mjs"` both green; open PR 11 against PR 10's branch. `[MODIFIED 2026-09-19]` the final gate for PR 11 is 12.21, which subsumes this line.

### Efficiency extension 2026-09-19 — determinism, timing and read-ledger measurement (D20, D29, D32, PR 11)

Source: `efficiency-extension.md` "Slice mapping" (11) and design "Efficiency Acceptance Budgets". The ledger is a MEASUREMENT (counts only, no payloads, no PII, no writes), taken for ONE process (design D32). STRICT TDD applies to the script's pure parts; the live run in 12.19 is an operational step recorded in the PR/verify report.

- [ ] 12.11 RED: `backend/tests/services/test_inspectores_depuracion_parity.py::test_parity_determinism_shuffled_real_snapshot_fixture` — the harness runs `depurar()` over the snapshot with the roster and stickers in two shuffled orders and asserts identical results.
- [ ] 12.12 GREEN: add a determinism check to `scripts/parity_inspectores_depurado.py` (exit non-zero and print the differing `identidad_key` list per field when results diverge).
- [ ] 12.13 RED+GREEN edge: `test_parity_determinism_check_fails_loudly_on_order_dependent_engine` — fed a deliberately order-dependent stub, the check exits non-zero with keys listed rather than reporting success; `test_parity_determinism_check_handles_empty_snapshot`.
- [ ] 12.14 RED+GREEN: `test_parity_timing_real_snapshot_under_budget` — best of ≥ 3 runs over the real snapshot is < 0.5 s; the report prints the measured seconds and the input sizes (profiles, stickers). Edge: a missing/empty snapshot is an error exit, never a vacuous pass.
- [ ] 12.15 RED+GREEN: register **D-TIEBREAK** (design D29) — `test_parity_register_lists_tiebreak_survivors_by_key` — every survivor decided by the terminal `identidad_key` tie-break is listed by key (an empty list is a valid, explicit result); extends 12.1's register alongside D7, D-REMAP, D-EXENTOS.
- [ ] 12.16 RED+GREEN: `test_ledger_counts_documents_per_collection` — a read-only counting wrapper around the Firestore client counts documents streamed for `evaluaciones` (E), `inspectores` (I) and `survey_cali` (S), plus atencionsismo rows walked and Blob GET/PUT counts, using a fake client.
- [ ] 12.17 RED+GREEN edge: the wrapper exposes no write method (asserted), the report contains counts only and no document content, a zero-document collection reports `0`, and a stream that raises mid-way records the partial count and exits non-zero.
- [ ] 12.18 RED+GREEN: `test_ledger_projection_uses_ttls_and_reports_per_hour_and_per_day` — drive the real component caches with a fake clock for one simulated static hour (no extra live reads) and project reads per continuously-open hour `2·I + S + 4·E` and per day for the modeled workload; compare against the ratified budget (default 10,000/day, design O1) and print PASS/FAIL with the arithmetic. Edge: a TTL of 0 or negative in configuration is rejected, not treated as "always fresh".
- [ ] 12.19 Operational: run the harness once against live data and record in the PR/verify report ONLY numbers — E, I, S, API rows walked, Blob GET/PUT counts, the measured projection per open hour and per day, the measured determinism/timing result, the gzip ratio and the 304/200 latency. This is the measure-first step design O1 asks for; its outcome feeds the O1 decision.
- [ ] 12.20 Extend the runbook of 12.9 with the efficiency steps: 09a/09b deployed, headers change from 09b (verify the Stickers tab), bundle republish, the ledger, and the O1 outcome.
- [ ] 12.21 Gate: `python -m pytest backend/tests/ -q` and `node --test "web/js/*.test.mjs"` both green including 12.11-12.18; open PR 11 against PR 10's branch (PR 10b's when split).

## Phase 12b: Production Rollout (post-merge, not a PR)

- [ ] 12b.1 Deploy slices 06-09 with `SEGUIMIENTO_DEPURACION` unset/`0`; confirm the payload is unchanged. `[MODIFIED 2026-09-19]` "slices 06-09" means 06, 07, 08, 09a and 09b; the BODY is unchanged, while the headers gain `ETag`, `Cache-Control`, `Vary` and (when accepted) gzip from 09b — confirm the Stickers tab and every other consumer of the endpoint still load.
- [ ] 12b.2 Run `scripts/publicar_referencia_inspectores.py`; record the resulting `generado_en`.
- [ ] 12b.3 Run `scripts/parity_inspectores_depurado.py` against live output; attach the report. `[MODIFIED 2026-09-19]` the report also carries the determinism check, the timing check and the E/I/S read ledger with its per-hour and per-day projection (12.14-12.19).
- [ ] 12b.4 Flip `SEGUIMIENTO_DEPURACION=1` only if 12b.3 meets every acceptance threshold AND slice 08 (holder clearing, Phase 9) is deployed — before it, `main.codigo` backfill can leave two profiles with the same código and inflate `activo`. `[MODIFIED 2026-09-19 efficiency gate]` The flip ALSO requires ALL of: slices 09a, 09b and 10 merged into the tracker (chain complete, slice 10 not before slice 11's tests are green, 11.20); the budget tests (10b.15-10b.27) and the Node tests (11.21-11.29) green in CI; the determinism and timing checks of 12.11-12.14 passing on the real snapshot; and the measured ledger of 12.18-12.19 within the budget ratified under design O1 (the ratification, including any changed TTLs or budget, is recorded in the report). If any of them fails the flag stays `0`.
- [ ] 12b.5 Merge the tracker `feat/seguimiento-inspectores-depurado` into `main`.
- [ ] 12b.6 Rollback drill: confirm setting the flag back to `0` restores the pre-extension payload and the frontend's legacy path. `[MODIFIED 2026-09-19]` also confirm that with the flag back at `0` the depuración compute, the survey scan and the referencia GET all stop (counters or logs), and that a retained frontend snapshot clears/re-renders without error.
- [ ] 12b.7 `[ADDED 2026-09-19]` Post-flip observation window: with one admin tab open for one hour, record recomputes (expected 0 on static inputs), Firestore reads (usage console or counters), referencia GETs (≤ 2) and Blob PUTs (0 when unchanged), and confirm the second open of Seguimiento is a 304. If any figure exceeds its budget, set the flag to `0` and open a follow-up; do not tune in production.
