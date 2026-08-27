# Survey ↔ Sticker Real-Time Sync Specification

Change: `survey-sticker-sync` · New capability (no prior spec exists for this domain).

## Purpose

Close two field-visible gaps between the survey and sticker campaigns: (1) a completed survey
does not surface its sticker task to the same inspector/group until the next `cruce_sticker.py`
cron run, and (2) group assignment only ever propagated to the exact-twin sticker point, leaving
nearby sticker-only buildings in the same block unassigned. This capability adds an in-app
"Survey completado" action (`marcarSurveyHecho`) that marks the survey done and immediately
materializes/pre-assigns its sticker twin, plus per-campaign CTA labels on the Cercanos tab and
SVG icons replacing the remaining emoji chrome in `formulario/js/form.js`.

## Requirements

### Requirement: `marcarSurveyHecho` action contract
The system MUST expose a `marcarSurveyHecho` action on `POST /inspector-asignaciones`, input
`{punto_id}` (a `planeacion_puntos` doc id), gated by `Depends(require_auth)` and `_puede_actuar`
(own-uid `inspector_uid == uid` OR active `grupo_id` membership) — rejecting any other caller with
403 and no write, mirroring `marcarHechoPlaneacion`. On success it MUST: (a) write
`estado_asignacion:'hecho'`, `completado_por: uid`, `completado_en: now` onto the `planeacion_puntos`
document — the identical write shape `marcarHechoPlaneacion` already performs; (b) run the
sticker-twin materialization/pre-assignment described below as a best-effort, fail-soft step that
NEVER blocks or fails the survey-completion write on error; (c) return
`{id: punto_id, estado_asignacion:'hecho', sticker_matches_id, sticker_creado: bool}`.

#### Scenario: Own-uid marks survey done and gets the twin id back
- GIVEN a `planeacion_puntos` point owned by inspector uid U (`inspector_uid == U`)
- WHEN U calls `marcarSurveyHecho {punto_id}`
- THEN the point's `estado_asignacion` becomes `hecho`, `completado_por` is `U`, and the response
  includes a non-null `sticker_matches_id`

#### Scenario: Non-owner, non-group caller is rejected
- GIVEN a `planeacion_puntos` point with `inspector_uid` set to a DIFFERENT uid and no `grupo_id`
  the caller belongs to
- WHEN the caller invokes `marcarSurveyHecho {punto_id}`
- THEN the call fails with 403 and neither the `planeacion_puntos` nor any `sticker_matches`
  document is written

#### Scenario: Sticker materialization failure never fails the survey write
- GIVEN the sticker-twin lookup/creation step raises an unexpected error (e.g. a transient
  Firestore failure)
- WHEN `marcarSurveyHecho` runs
- THEN the `planeacion_puntos` write still completes (`estado_asignacion:'hecho'`) and the action
  returns success with `sticker_matches_id: null`

### Requirement: Sticker twin lookup reuses `_buscar_gemelo` verbatim
`marcarSurveyHecho` MUST locate the candidate sticker twin by calling
`_buscar_gemelo(db, CAMPANA_STICKER, punto_data)` — the SAME geo-then-address cascade
`_tomar_punto` already uses (`nearest` within `MAX_MATCH_M` = 40 m haversine, then `addr_key` /
`ADDR_MATCH_RATIO` fuzzy match), over the SAME `_disponible` eligibility filter (no
`inspector_uid`, no `grupo_id`, `estado_asignacion` not `hecho`, `tiene_sticker` not `true`)
within the `NEARBY_RADIUS_M` bbox prefilter — and MUST NOT introduce a new matching rule,
threshold, or radius constant.

#### Scenario: Twin lookup uses the existing cascade unchanged
- GIVEN a pending, unassigned `sticker_matches` document within `MAX_MATCH_M` of the completed
  survey point's coords
- WHEN `marcarSurveyHecho` searches for its twin
- THEN it is found via the same `nearest`/`addr_key` cascade `_tomar_punto` uses, with no new
  matching logic

### Requirement: Twin FOUND — pre-assignment fields
When `_buscar_gemelo` returns an existing, eligible `sticker_matches` document, `marcarSurveyHecho`
MUST merge exactly `grupo_id` (the survey point's own `grupo_id`), `inspector_uid` (the survey
point's own `inspector_uid`), `estado_asignacion:'asignado'`, and `asignado_en: now` onto that
document — copying the SAME assignment shape the survey point itself carries (group-only,
uid-only, or both). It MUST NOT write `cuadrilla_id` onto the twin: that field names a
`cuadrillas` document in the sticker campaign's OWN id-space, distinct from `planeacion_puntos`'
own `cuadrilla_id` (`planeacion_cuadrillas`), so copying it would silently create a cross-collection
id collision.

#### Scenario: Group-assigned survey point pre-assigns the twin by grupo_id
- GIVEN a survey point has `grupo_id:'g1'` and `inspector_uid:null`
- WHEN its twin is found and pre-assigned
- THEN the twin's `grupo_id` becomes `'g1'`, `inspector_uid` stays `null`, and
  `estado_asignacion` becomes `'asignado'`

#### Scenario: Individually-assigned survey point pre-assigns the twin by inspector_uid
- GIVEN a survey point has `inspector_uid:'u1'` and `grupo_id:null`
- WHEN its twin is found and pre-assigned
- THEN the twin's `inspector_uid` becomes `'u1'`, `grupo_id` stays `null`, and
  `estado_asignacion` becomes `'asignado'`

#### Scenario: cuadrilla_id is never written onto the sticker twin
- GIVEN a survey point has a non-null `cuadrilla_id` (a `planeacion_cuadrillas` id)
- WHEN its sticker twin is pre-assigned by `marcarSurveyHecho`
- THEN the twin's `cuadrilla_id` field is left unset by this action (unchanged from whatever it
  was before)

### Requirement: Twin MISSING — deterministic on-demand creation
When no eligible twin is found, `marcarSurveyHecho` MUST create exactly one `sticker_matches`
document at doc id `atencionsismo_{registro_id}` (the survey point's OWN `registro_id`, via the
SAME deterministic `doc_id(fuente, registro_id)` shape `cruce_sticker.py`/`planeacion_cruce.py`
already use), with:
- pipeline-shaped fields: `fuente:'atencionsismo'`, `registro_id`, `tiene_sticker:false`,
  `tier:null`, `sticker_dist_m:null`, `direccion`, `coords`, `zona_id` (the survey point's
  `comuna`), `matched_at: now`
- linkage fields: `clave_integracion` (copied from the survey point), `planeacion_punto_id: punto_id`
- pre-assignment fields, per the "Twin FOUND" rule above: `estado_asignacion:'asignado'`,
  `grupo_id`, `inspector_uid`, `asignado_en: now`, `cuadrilla_id: null`, `reasignado_de: null`

The `fuente:'atencionsismo'` namespace MUST NEVER be minted by `cruce_sticker.py` (which only ever
writes `fuente` `'ede'` or `'israel'`), so this doc id can never collide with a future cron write
for a different building — `cruce_sticker.py` never needs to special-case it.

#### Scenario: Missing twin is created with the deterministic id
- GIVEN no `sticker_matches` document matches the completed survey point via `_buscar_gemelo`
- WHEN `marcarSurveyHecho` runs
- THEN a new `sticker_matches/atencionsismo_{registro_id}` document is created with
  `estado_asignacion:'asignado'` and the survey point's own `grupo_id`/`inspector_uid`

#### Scenario: Created doc is immediately visible in the inspector's sticker tab
- GIVEN the newly created twin has `inspector_uid` (or `grupo_id`) matching the acting inspector
- WHEN that inspector next calls `misPuntos`
- THEN the new sticker point is included, with no dependency on `cruce_sticker.py` having run

#### Scenario: Re-running marcarSurveyHecho for the same point is idempotent
- GIVEN `marcarSurveyHecho` already created `sticker_matches/atencionsismo_{registro_id}` for a
  survey point on a prior call
- WHEN `marcarSurveyHecho` is called again for the same `punto_id`
- THEN no duplicate `sticker_matches` document is created (the same doc id is merge-written again)

#### Scenario: cruce_sticker.py never targets an atencionsismo_ doc id
- GIVEN `sticker_matches` contains a document with id `atencionsismo_14832`
- WHEN `cruce_sticker.py` runs its next cycle
- THEN it never writes to that doc id (it only ever mints `ede_*`/`israel_*` ids), so the two
  writers' namespaces never collide

### Requirement: Cercanos CTA shows a per-campaign label
`buildCercanoCard`'s claim button text MUST read "Levantar survey" when `p.campana === 'survey'`
and "Pegar sticker" when `p.campana === 'sticker'`, replacing the previous single generic
"Tomar este punto" label used for both campaigns.

#### Scenario: Survey-campaign nearby point shows "Levantar survey"
- GIVEN a Cercanos card for a point with `campana:'survey'`
- WHEN the card renders
- THEN its claim button reads "Levantar survey"

#### Scenario: Sticker-campaign nearby point shows "Pegar sticker"
- GIVEN a Cercanos card for a point with `campana:'sticker'`
- WHEN the card renders
- THEN its claim button reads "Pegar sticker"

### Requirement: No emoji chrome in form.js
`formulario/js/form.js` MUST replace all 4 emoji-prefixed UI strings — the three
"📍 Cómo llegar" link labels (in `buildAsignacionCard`, `buildPlaneacionCard`, and
`buildCercanoCard`) and the one "📞 Llamar" label (in `buildPlaneacionCard`) — with an inline SVG
icon plus plain text, and MUST NOT contain any emoji codepoint (Unicode ranges U+1F300–U+1FAFF,
U+2600–U+27BF) anywhere in the file afterward.

#### Scenario: Every "Cómo llegar" link uses an SVG icon
- GIVEN the three "Cómo llegar" link-building call sites
- WHEN their cards render
- THEN each link shows an inline SVG icon and the text "Cómo llegar", with no emoji character

#### Scenario: The "Llamar" link uses an SVG icon
- GIVEN `buildPlaneacionCard` renders a solicitante phone link
- WHEN the card renders
- THEN the link shows an inline SVG icon and the text "Llamar", with no emoji character

#### Scenario: No emoji codepoint remains in form.js
- GIVEN the full text of `formulario/js/form.js` after this change
- WHEN scanned for characters in the Unicode emoji ranges (U+1F300–U+1FAFF, U+2600–U+27BF)
- THEN zero matches are found
