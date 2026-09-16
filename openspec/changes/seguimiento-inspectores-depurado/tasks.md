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

## Known spec/design conflict (resolve before Phase 2)

`specs/datos-referencia-blob/spec.md` requires **three independently-published/fetched blobs**; `design.md` (D8, File Changes, Blob reference bundle) specifies **one `bundle.json`** with `access:'private'`. Tasks below follow **design.md** (single private bundle) since it is the latest confirmed decision; the spec text needs a follow-up correction pass. Flagged as a risk below — confirm with user before PR 1 merges.

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
- [ ] 5.6 Confirm with user whether `correo_contacto` is in scope for this delivery (open item from design's Open Questions) before shipping 4.1.

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
