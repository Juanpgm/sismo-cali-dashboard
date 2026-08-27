# Delta for Planeación — cruce y asignación

Change: `survey-sticker-sync` · Base: `openspec/specs/planeacion-asignaciones/spec.md`. This delta
adds requirements for the grupo↔sticker propagation/desassignment radius sweep; the underlying
exact-twin propagation code already exists (`_encontrar_twin_sticker`,
`_propagar_grupo_a_stickers`/`_desasignar_grupo_de_stickers`) but was not previously captured as a
formal requirement in the base spec, so this delta documents it as ADDED rather than MODIFIED.

## ADDED Requirements

### Requirement: Group assignment propagates to sticker points by radius, not only the exact twin
When `asignarGrupoAPuntos` assigns `grupo_id` to a set of `planeacion_puntos`, the system MUST, for
EACH assigned point: (a) keep the existing exact-twin match (`_encontrar_twin_sticker`, which also
stamps `clave_integracion` + `planeacion_punto_id` on that one document), AND (b) additionally
sweep every `sticker_matches` document within `DEFAULT_MAX_RADIUS_M` (800 m, haversine — the same
constant `_propagar_grupo_a_stickers` already imports) of that point's coords, setting `grupo_id`
on every ELIGIBLE one. Eligible means: `grupo_id` not already set, `estado_asignacion != 'hecho'`,
and `tiene_sticker is not true` — the same `_sticker_twin_libre` predicate, minus its
`clave_integracion` ownership check (which applies only to the exact-twin link, not to radius
neighbors). Radius-swept documents MUST NOT have `clave_integracion` or `planeacion_punto_id`
written — those linkage fields stay reserved for the true twin so later twin lookups
(`_encontrar_twin_sticker`/`_buscar_gemelo`) are unaffected by radius neighbors. No capacity cap
applies to the radius sweep (explicit, binding decision). This write remains best-effort/fail-soft
and MUST NOT fail the survey-side `grupo_id` write on error.

Within one `asignarGrupoAPuntos` call, first-link-wins across BOTH the exact-twin and radius
passes: once a `sticker_matches` document is consumed by any point in the batch (exact-twin match
or radius sweep), it MUST NOT be swept again for a later point in the same batch.

#### Scenario: Radius sweep assigns unassigned stickers within 800m
- GIVEN a survey point P is assigned to `grupo_id:'g1'`, and an unassigned, pending
  `sticker_matches` document S sits 500 m from P's coords (not P's exact twin)
- WHEN `asignarGrupoAPuntos` assigns P to `g1`
- THEN S's `grupo_id` becomes `'g1'`

#### Scenario: A sticker beyond 800m is untouched
- GIVEN an unassigned, pending `sticker_matches` document S sits 900 m from an assigned point's
  coords
- WHEN `asignarGrupoAPuntos` runs
- THEN S's `grupo_id` remains unset

#### Scenario: An already-grouped sticker is skipped
- GIVEN a `sticker_matches` document within radius already has `grupo_id:'g0'` (set by a prior
  call)
- WHEN `asignarGrupoAPuntos` assigns a nearby point to a DIFFERENT `grupo_id:'g1'`
- THEN that document's `grupo_id` stays `'g0'` (never overwritten)

#### Scenario: A hecho sticker within radius is skipped
- GIVEN a `sticker_matches` document within radius has `estado_asignacion:'hecho'`
- WHEN `asignarGrupoAPuntos` runs
- THEN that document's `grupo_id` is not set

#### Scenario: A tiene_sticker:true document within radius is skipped
- GIVEN a `sticker_matches` document within radius has `tiene_sticker:true`
- WHEN `asignarGrupoAPuntos` runs
- THEN that document's `grupo_id` is not set

#### Scenario: Radius-swept docs never get the twin linkage fields
- GIVEN a `sticker_matches` document is swept into a group only via the radius pass (not the exact
  twin)
- WHEN the write completes
- THEN that document's `clave_integracion` and `planeacion_punto_id` remain unset

#### Scenario: Two points in the same batch never claim the same sticker
- GIVEN two assigned points P1, P2 in the same `asignarGrupoAPuntos` call both have an unassigned
  sticker S within their respective radii
- WHEN the batch is processed
- THEN S is claimed by whichever of P1/P2 is processed first, and is not reprocessed for the other

#### Scenario: Radius sweep failure never fails the survey-side write
- GIVEN the radius sweep raises an unexpected error (e.g. a transient Firestore failure)
- WHEN `asignarGrupoAPuntos` runs
- THEN the `planeacion_puntos` `grupo_id` write still completes successfully

### Requirement: Group desassignment symmetrically retracts the radius sweep
When `desasignarGrupo` clears `grupo_id` from a set of `planeacion_puntos`, the system MUST, for
EACH point, retract `grupo_id` (set to `null`) from every `sticker_matches` document within
`DEFAULT_MAX_RADIUS_M` of that point's coords whose CURRENT `grupo_id` equals the grupo id being
desassigned — covering both the exact twin and any radius-swept sibling — and MUST NOT touch a
sticker whose `grupo_id` belongs to a different group. The exact twin's `clave_integracion` /
`planeacion_punto_id` linkage MUST be left untouched by desassignment (mirrors the existing
exact-twin behavior — the physical building pairing stays true regardless of assignment state).
This write remains best-effort/fail-soft and MUST NOT fail the survey-side `grupo_id` clear.

#### Scenario: Desassign clears grupo_id on both twin and radius siblings
- GIVEN a survey point P was assigned to `grupo_id:'g1'`, propagating to its exact twin AND a
  radius-swept sibling S
- WHEN `desasignarGrupo` clears P's `grupo_id`
- THEN both the exact twin and S have `grupo_id` cleared to `null`

#### Scenario: A sticker with a different grupo_id in radius is left untouched
- GIVEN a `sticker_matches` document within radius of a desassigned point has `grupo_id:'g2'`
  (a different group)
- WHEN `desasignarGrupo` clears `grupo_id:'g1'` from that point
- THEN the `grupo_id:'g2'` document is unchanged

#### Scenario: Exact twin's linkage fields survive desassignment
- GIVEN the exact twin has `clave_integracion` and `planeacion_punto_id` set from the earlier
  assignment
- WHEN `desasignarGrupo` clears the group
- THEN `clave_integracion` and `planeacion_punto_id` remain set on the exact twin; only `grupo_id`
  is cleared

#### Scenario: Desassign failure never fails the survey-side clear
- GIVEN the radius retract raises an unexpected error
- WHEN `desasignarGrupo` runs
- THEN the `planeacion_puntos` `grupo_id` clear still completes successfully
