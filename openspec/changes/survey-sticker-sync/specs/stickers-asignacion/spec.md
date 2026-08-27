# Delta for Stickers — cruce y asignación

Change: `survey-sticker-sync` · Base: `openspec/specs/stickers-asignacion/spec.md`

## MODIFIED Requirements

### Requirement: `sticker_matches` document ownership and merge safety
The system MUST store one `sticker_matches/{fuente}_{registro_id}` document per point, split into
a pipeline-owned field group (`fuente`, `registro_id`, `tiene_sticker`, `tier`, `sticker_dist_m`,
`direccion`, `coords`, `zona_id`, `matched_at`) and an admin-owned field group
(`estado_asignacion`, `cuadrilla_id`, `inspector_uid`, `asignado_en`, `reasignado_de`). The doc id
MUST be derived deterministically from `fuente` + `registro_id` so re-running any writer updates
the same document instead of creating a duplicate. `cruce_sticker.py` MUST only ever write the
pipeline-owned field subset via a `merge:true` set, never a full-document `set()`, and MUST NOT
touch any admin-owned field on an existing document. A document MAY also be created on demand by
`inspector_asignaciones.py`'s `marcarSurveyHecho` action (see the `survey-sticker-realtime-sync`
spec) under its OWN `fuente:'atencionsismo'` namespace — `cruce_sticker.py` MUST NEVER mint a
`fuente:'atencionsismo'` id, so the two writers' doc-id namespaces never collide and neither ever
needs to special-case the other's documents.
(Previously: document creation was exclusively `cruce_sticker.py`'s; the pipeline-owned/admin-owned
split and merge-only discipline applied only to that one writer.)

#### Scenario: Doc id is stable across re-runs
- GIVEN a Panel point with `fuente='ede'`, `registro_id='1234'` already has a `sticker_matches`
  document
- WHEN `cruce_sticker.py` runs again for the same point
- THEN the write targets doc id `ede_1234` (the same document), not a new document

#### Scenario: Pipeline re-run never clobbers admin-owned fields
- GIVEN a `sticker_matches` document has `estado_asignacion:'asignado'`, `cuadrilla_id:'c1'`,
  `inspector_uid:'u1'` set by the admin API
- WHEN `cruce_sticker.py` re-runs and re-writes the pipeline-owned fields for that same point
- THEN `estado_asignacion`, `cuadrilla_id`, `inspector_uid`, `asignado_en`, and `reasignado_de`
  remain unchanged after the write

#### Scenario: First cron write seeds a pending assignment state
- GIVEN no `sticker_matches` document exists yet for a given `fuente`/`registro_id`
- WHEN `cruce_sticker.py` writes that point for the first time
- THEN the resulting document has `estado_asignacion:'pendiente'`, `cuadrilla_id:null`,
  `inspector_uid:null`

#### Scenario: On-demand creation uses the atencionsismo_ namespace, never colliding with the cron
- GIVEN `marcarSurveyHecho` creates a `sticker_matches` document for a survey point with no cron
  match yet
- WHEN the document is written
- THEN its id is `atencionsismo_{registro_id}`, a namespace `cruce_sticker.py` never mints
  (`ede_*`/`israel_*` only), so no future cron run ever targets that same document

#### Scenario: On-demand creation seeds an ASSIGNED state, not pendiente
- GIVEN `marcarSurveyHecho` creates a new `sticker_matches` document (twin miss)
- WHEN the document is written
- THEN `estado_asignacion` is `'asignado'` (pre-assigned to the survey point's own
  `grupo_id`/`inspector_uid`), unlike `cruce_sticker.py`'s own first-write scenario which always
  seeds `'pendiente'`
