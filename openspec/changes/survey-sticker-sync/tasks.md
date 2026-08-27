# Tasks: Survey ↔ Sticker campaign sync

Change: `survey-sticker-sync` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-tasks

`strict_tdd`: every non-trivial branch gets a RED test task before its GREEN implementation task,
per `design.md`'s "Testing Strategy" table (extends existing suites, no new frameworks).

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~650-850 (prod ~250-320, tests ~370-480, formulario prod ~90-120) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (radius sweep) → PR 2 (marcarSurveyHecho) → PR 3 (formulario) |
| Delivery strategy | ask-on-risk (resolved: split) |
| Chain strategy | stacked-to-main |

Decision needed before apply: No (resolved)
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Radius sweep on grupo assign/desassign (`planeacion_asignaciones.py`) | PR 1 | `pytest backend/tests/routers/test_planeacion_asignaciones.py -k sticker` | fake-Firestore fixture in the same test module (no live DB needed) | Revert `planeacion_asignaciones.py` + its test additions; exact-twin propagation unaffected |
| 2 | `marcarSurveyHecho` action + on-demand twin creation (`inspector_asignaciones.py`) | PR 2 | `pytest backend/tests/routers/test_inspector_asignaciones.py -k marcarSurveyHecho` | same fake-Firestore fixture pattern as `test_marcar_hecho_planeacion_*` | Revert `inspector_asignaciones.py` + tests; `marcarHechoPlaneacion`/`tomarPunto` unaffected |
| 3 | CTA labels, "Survey completado" button, SVG chrome (`form.js`/`logic.js`) | PR 3 | `node --test formulario/test/logic.test.mjs` | manual DOM check (`formulario/index.html` in a browser) — no e2e harness for this surface, per design.md | Revert the two `.js` files; no backend dependency |

## Phase 1 — Backend: radius sweep (`backend/app/routers/planeacion_asignaciones.py`)

Depends on: none (independent of Phase 2/3, can ship first per design's own layering).

- [x] 1.1 (RED) Extend `backend/tests/routers/test_planeacion_asignaciones.py` (alongside the
      existing exact-twin tests, ~2259-2404) with fake-Firestore cases for the radius pass, MUST
      fail before 1.2: neighbour within 800 m and unassigned gets `grupo_id` only (no
      `clave_integracion`/`planeacion_punto_id`); neighbour beyond 800 m untouched; already-grouped
      neighbour (different `grupo_id`) untouched; `estado_asignacion:'hecho'` neighbour untouched;
      `tiene_sticker:true` neighbour untouched; two points in one batch never both claim the same
      sticker (first-link-wins across exact-twin AND radius passes); radius sweep raising an
      exception never fails the survey-side `grupo_id` write.
      — Satisfies: *planeacion-asignaciones* "Group assignment propagates to sticker points by
      radius" (all 7 scenarios).
- [x] 1.2 (RED) Same file: symmetric desassign cases, MUST fail before 1.3: clears `grupo_id` on
      both exact twin and radius sibling; a sibling with a DIFFERENT `grupo_id` is left untouched;
      exact twin's `clave_integracion`/`planeacion_punto_id` survive desassignment; retract failure
      never fails the survey-side clear.
      — Satisfies: *planeacion-asignaciones* "Group desassignment symmetrically retracts the radius
      sweep" (all 4 scenarios).
- [x] 1.3 (GREEN) In `_propagar_grupo_a_stickers` (`planeacion_asignaciones.py:1619`), after the
      existing exact-twin match via `_encontrar_twin_sticker`, add a radius pass over the SAME
      already-loaded `candidatos` list: for each assigned point, sweep every remaining candidate
      (not in `consumidos`) within `DEFAULT_MAX_RADIUS_M` (`haversine_m`, both already imported)
      that passes `_sticker_twin_libre` MINUS its `clave_integracion` check (new helper or inline
      predicate — do not touch `clave_integracion`/`planeacion_punto_id` for these), set `grupo_id`
      only, add each to `consumidos`. No capacity cap. Keep the whole pass inside the existing
      `try/except` (fail-soft, unchanged contract). Run 1.1 and confirm it passes.
      — Satisfies: *planeacion-asignaciones* "Group assignment propagates..." (implementation).
- [x] 1.4 (GREEN) In `_desasignar_grupo_de_stickers` (`planeacion_asignaciones.py:1652`), extend the
      retract loop to also clear `grupo_id` (set `null`, leave `clave_integracion`/
      `planeacion_punto_id` untouched) on every `sticker_matches` doc within `DEFAULT_MAX_RADIUS_M`
      of the point whose CURRENT `grupo_id` equals the one being desassigned — covers the exact twin
      and any radius sibling from 1.3 in one sweep. Keep inside the existing `try/except`. Run 1.2
      and confirm it passes.
      — Satisfies: *planeacion-asignaciones* "Group desassignment symmetrically retracts..."
      (implementation).
- [x] 1.5 Run `pytest backend/tests/routers/test_planeacion_asignaciones.py` full file green; confirm
      no regression in the pre-existing exact-twin tests (~2259-2404).
      — Satisfies: regression safety for both modified helpers.

## Phase 2 — Backend: `marcarSurveyHecho` (`backend/app/routers/inspector_asignaciones.py`)

Depends on: Phase 1 only for shared vocabulary (`DEFAULT_MAX_RADIUS_M` untouched here); can be
written in parallel with Phase 1, must land before Phase 3's button is exercised end-to-end.

- [x] 2.1 (RED) Create `backend/tests/routers/test_inspector_asignaciones.py` cases (fake-Firestore,
      same fixture shape as `test_marcar_hecho_planeacion_*`), MUST fail before 2.2-2.4:
      own-uid success stamps `estado_asignacion:'hecho'`/`completado_por`/`completado_en` and
      returns a non-null `sticker_matches_id`; non-owner/non-group caller gets 403 with NO write to
      either collection; sticker-materialization raising an exception still completes the survey
      write and returns `sticker_matches_id: null`.
      — Satisfies: *survey-sticker-realtime-sync* "`marcarSurveyHecho` action contract" (all 3
      scenarios).
- [x] 2.2 (RED) Same file: twin-FOUND cases, MUST fail before 2.3: group-assigned survey point
      (`grupo_id` set, `inspector_uid` null) pre-assigns the twin's `grupo_id` only, leaves
      `inspector_uid` null, sets `estado_asignacion:'asignado'`; individually-assigned point mirrors
      that with `inspector_uid`; twin's `cuadrilla_id` is left unset regardless of the survey point's
      own `cuadrilla_id`.
      — Satisfies: *survey-sticker-realtime-sync* "Twin FOUND — pre-assignment fields" (all 3
      scenarios).
- [x] 2.3 (RED) Same file: twin-MISSING cases, MUST fail before 2.4: creates
      `sticker_matches/atencionsismo_{registro_id}` with `fuente:'atencionsismo'`,
      `tiene_sticker:false`, `tier:null`, `sticker_dist_m:null`, `direccion`, `coords`,
      `zona_id`=`comuna`, `matched_at`, `clave_integracion`, `planeacion_punto_id`,
      `estado_asignacion:'asignado'`, `grupo_id`/`inspector_uid` copied, `cuadrilla_id:null`,
      `reasignado_de:null`, `asignado_en`; re-running for the same point does not duplicate the doc
      (merge-write, same id); the created id never collides with an `ede_*`/`israel_*` id shape.
      — Satisfies: *survey-sticker-realtime-sync* "Twin MISSING — deterministic on-demand creation"
      (all 4 scenarios); *stickers-asignacion* "on-demand creation" scenarios (namespace + seeded
      ASSIGNED state).
- [x] 2.4 (GREEN) Implement `_marcar_survey_hecho(db, uid, punto_id)` in
      `inspector_asignaciones.py`, next to `_marcar_hecho_planeacion` (~line 441): same
      `_puede_actuar` gate and 403/404 shape; on pass, write `estado_asignacion:'hecho'` +
      `completado_por`/`completado_en` onto `planeacion_puntos` FIRST (must succeed, mirrors
      `_marcar_hecho_planeacion`'s write). Then, in a `try/except` that NEVER re-raises: call
      `_buscar_gemelo(db, CAMPANA_STICKER, punto_data)`; if found, merge `grupo_id`,
      `inspector_uid`, `estado_asignacion:'asignado'`, `asignado_en` onto that doc (never
      `cuadrilla_id`); if not found, import `doc_id` from `app.jobs.cruce_sticker` and `.set(...,
      merge=True)` a new `sticker_matches/{doc_id('atencionsismo', registro_id)}` doc with the field
      shape from `design.md`'s "Interfaces / Contracts". Return
      `{id, estado_asignacion:'hecho', sticker_matches_id, sticker_creado}` (`sticker_matches_id:
      null` if the except branch fired). Run 2.1-2.3 and confirm they pass.
      — Satisfies: all `survey-sticker-realtime-sync` requirements (implementation).
- [x] 2.5 Wire the dispatcher: add `if body.action == "marcarSurveyHecho": return
      _marcar_survey_hecho(db, claims["uid"], body.punto_id)` next to the existing
      `marcarHechoPlaneacion` branch (~line 701), same `Depends(require_auth)` guard already applied
      to the whole endpoint.
      — Satisfies: *survey-sticker-realtime-sync* "`marcarSurveyHecho` action contract" (routing).
- [x] 2.6 Run `pytest backend/tests/routers/test_inspector_asignaciones.py` full file green.

## Phase 3 — Formulario: button, CTA labels, SVG chrome

Depends on: Phase 2 (button calls `marcarSurveyHecho`); CTA-label and SVG tasks are independent of
Phase 1/2 and can land in the same PR without a backend dependency.

- [x] 3.1 (RED) Add `etiquetaAccionCercano` cases to `formulario/test/logic.test.mjs` (mirrors the
      existing `etiquetaCampana` tests), MUST fail before 3.2: `'survey'` → `'Levantar survey'`;
      `'sticker'` → `'Pegar sticker'`; unknown/undefined → a safe fallback (reuse
      `etiquetaCampana`'s own unknown-value convention).
      — Satisfies: *survey-sticker-realtime-sync* "Cercanos CTA shows a per-campaign label" (both
      scenarios).
- [x] 3.2 (GREEN) Add pure `export function etiquetaAccionCercano(campana)` to `formulario/js/logic.js`
      next to `etiquetaCampana` (~line 261). Run 3.1 and confirm it passes.
      — Satisfies: same requirement (implementation).
- [x] 3.3 Wire `buildCercanoCard` (`form.js:544`): replace the hard-coded `'Tomar este punto'` button
      text with `etiquetaAccionCercano(p.campana)`.
      — Satisfies: *survey-sticker-realtime-sync* "Cercanos CTA..." scenarios, end to end.
- [x] 3.4 Add inline SVG helpers to `form.js` (Feather `map-pin`/`phone`, 24×24, `stroke=currentColor`,
      no fill, `aria-hidden="true"`) and replace the 4 emoji-prefixed strings: `buildAsignacionCard`'s
      `'📍 Cómo llegar'` (~line 336), `buildPlaneacionCard`'s `'📍 Cómo llegar'` (~line 414) and
      `'📞 Llamar'` (~line 421), `buildCercanoCard`'s `'📍 Cómo llegar'` (~line 537) — each becomes a
      `<span class="icon">`+SVG prepended to the existing link, text node kept as plain
      `'Cómo llegar'`/`'Llamar'`.
      — Satisfies: *survey-sticker-realtime-sync* "No emoji chrome in form.js" (all 3 scenarios).
- [x] 3.5 Add the "Survey completado" button to `buildPlaneacionCard` (`form.js:365`), appended into
      the same `acciones` div as the existing "Abrir encuesta" link, only when `p.campana ===
      'survey'` (guard against rendering it for a sticker-shaped `planeacion_puntos` card, if any
      reach this renderer). Add `async function onMarcarSurveyHecho(p, btn)` mirroring
      `onTomarPunto`'s disable/try/finally shape (`form.js:573`): calls `asignacionesApi({action:
      'marcarSurveyHecho', punto_id: p.id})`, on success refreshes `misPuntosPlaneacion`
      (`cargarMisPuntos()`) so the completed point drops off this list and, on the next
      `cargarPuntosCercanos()`/sticker-tab load, its twin is visible; on failure, show an inline error
      near the button (reuse `mostrarErrorTomarPunto`'s box or a sibling one scoped to this card).
      — Satisfies: *survey-sticker-realtime-sync* "`marcarSurveyHecho` action contract" (end-to-end
      UI flow, all 3 scenarios).
- [x] 3.6 Grep `formulario/js/form.js` for emoji codepoints (Unicode ranges U+1F300-U+1FAFF,
      U+2600-U+27BF) after 3.4/3.5 — MUST return zero matches.
      — Satisfies: *survey-sticker-realtime-sync* "No emoji codepoint remains in form.js" scenario.
- [x] 3.7 Run `node --test formulario/test/logic.test.mjs` green; manual browser check of the
      Cercanos tab CTA labels, the "Survey completado" button, and the 4 SVG icons rendering
      (no automated e2e harness covers this surface, per `design.md`'s Testing Strategy row).

## Phase 4 — Wrap-up

- [ ] 4.1 Run the full backend suite (`pytest backend/tests/`) and the full formulario suite
      (`node --test formulario/test/`) once end to end; confirm no cross-phase regression.
- [ ] 4.2 Update `proposal.md`'s Success Criteria checkboxes once each is manually confirmed.
