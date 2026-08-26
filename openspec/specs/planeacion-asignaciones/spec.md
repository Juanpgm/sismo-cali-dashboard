# Planeación — cruce Survey Cali ↔ API y asignación de levantamientos Specification

Change: `planeacion-asignaciones` · New capability (no prior spec existed for this domain).

## Purpose

A recurring, persisted process that determines which atencionsismo API reports already have an EDAN
survey in `survey_cali` versus which are still pending, ranks the pending ones by a deterministic
priority, and surfaces them in a new admin-only "Planeación" dashboard tab with a prioritized table,
a Leaflet map, and CRUD to group pending points into cuadrillas and assign, reassign, correct, or
exclude them — where every assigned point carries a deterministic `clave_integracion` prefilled into
the Survey123 form's `codigoapp` question, so the returned survey identifies its own point without
guessing.

## Requirements

### Requirement: `codigoapp` survives the Survey123 ingestion pipeline
The system MUST carry the Survey123 layer field `codigoapp` from the FeatureServer query through
`scripts/refresh_data.py`'s column allowlist (`LAYER_TO_RAW`), through `normalize()`, into
`web/data/inspections.json`, and into the `survey_cali` Firestore document via the existing
`ingest_records` path. `codigoapp` MUST be treated as RAW upstream content (not a derived or
source-system field), so a change to its value participates in the content-hash gate and is
ingested.

#### Scenario: The column survives the allowlist
- GIVEN the Survey123 layer query returns a feature whose attributes include `codigoapp`
- WHEN `scripts/refresh_data.py` builds the raw DataFrame and applies its column allowlist
- THEN the resulting frame still contains a `codigoapp` column

#### Scenario: The value reaches the survey document
- GIVEN a Survey123 record whose `codigoapp` is `PLN-14832-9C4A1F0B`
- WHEN the dashboard refresh runs and ingests that record into `survey_cali`
- THEN the `survey_cali/{GlobalID}` document has `codigoapp` equal to `PLN-14832-9C4A1F0B`

#### Scenario: A changed key is ingested, not skipped
- GIVEN a `survey_cali` document already ingested with an empty `codigoapp`
- WHEN the upstream record's `codigoapp` changes to a real key and ingestion runs
- THEN the content-hash gate does not skip the record and the new value is written

### Requirement: `clave_integracion` minting rule
The system MUST mint the integration key with a pure, deterministic function of the point's
immutable identity (`fuente`, `registro_id`) only — no clock, no counter, no randomness. The key
MUST use only the character set `[A-Z0-9-]`, MUST be at most 255 characters, MUST carry a checksum
segment derived from the raw identity, and MUST be recoverable to its `registro_id` by parsing. A
key whose checksum does not verify MUST be treated as no-match, never as a match to a different
point.

#### Scenario: The same point always mints the same key
- GIVEN a point with `fuente='atencionsismo'` and `registro_id='14832'`
- WHEN the key is minted twice, in separate processes and on separate days
- THEN both calls return the identical string

#### Scenario: The key is safe in a URL query parameter
- GIVEN any minted `clave_integracion`
- WHEN it is inspected
- THEN it contains only characters in `[A-Z0-9-]` and its length is at most 255

#### Scenario: Two different points mint different keys
- GIVEN two points whose `registro_id` values differ only in characters that key sanitization strips
- WHEN both keys are minted
- THEN the two keys differ, because the checksum is computed over the raw identity

#### Scenario: A damaged key does not match a real point
- GIVEN a returned `codigoapp` whose id segment parses but whose checksum segment does not match a
  re-mint from that id
- WHEN the cascade evaluates the exact-key rung
- THEN no match is recorded and the point continues to the fuzzy rungs

> Implementation note (recorded 2026-08-26, see the change's `apply-progress.md`): the exact
> "verify by stateless recompute" mechanism ADR-3 originally specified is unimplementable for real
> UUID-shaped `registro_id` values (the sanitized slug truncates before 24 chars, so recomputing the
> checksum from the parsed slug can never reproduce the digest computed over the full raw id). The
> checksum property this requirement protects — a damaged or forged key resolves to no point, and no
> two distinct ids can be paired to the same key by a slug collision — is instead enforced by exact
> membership lookup against an index of keys freshly minted for known points (`build_key_index` /
> `cruce_punto` in `planeacion_cruce.py`), which preserves both safety guarantees unconditionally.
> `verify_clave_integracion` itself performs a structural check only (prefix / charset / slug length /
> digest length).

### Requirement: `planeacion_puntos` document ownership and merge safety
The system MUST store one `planeacion_puntos/{fuente}_{registro_id}` document per atencionsismo
report, split into a pipeline-owned field group (`fuente`, `registro_id`, `clave_integracion`,
`tiene_survey`, `survey_globalid`, `match_via`, `match_dist_m`, `tier`, `direccion`, `barrio`,
`comuna`, `coords`, `afectacion`, `estado_verificacion`, `tipo_inmueble`, `habitabilidad`,
`fecha_creacion`, `prioridad_score`, `prioridad`, `matched_at`) and an admin-owned field group
(`estado_asignacion`, `cuadrilla_id`, `inspector_uid`, `prioridad_override`, `asignado_en`,
`reasignado_de`, `motivo_exclusion`, `notas`, `editado_en`, `editado_por`). The doc id MUST be
derived deterministically from `fuente` + `registro_id`. The pipeline MUST only ever write the
pipeline-owned field subset via a `merge:true` set, never a full-document `set()`, and MUST NOT touch
any admin-owned field on an existing document.

#### Scenario: Doc id is stable across re-runs
- GIVEN a point with `fuente='atencionsismo'`, `registro_id='14832'` already has a document
- WHEN the cruce job runs again for the same point
- THEN the write targets doc id `atencionsismo_14832`, not a new document

#### Scenario: Pipeline re-run never clobbers admin-owned fields
- GIVEN a document has `estado_asignacion:'asignado'`, `cuadrilla_id:'c1'`, `inspector_uid:'u1'`,
  `prioridad_override:'alta'`, and `notas:'coordinar con el conjunto'` set by the admin endpoint
- WHEN the cruce job re-runs and re-writes the pipeline-owned fields for that same point
- THEN every one of those admin-owned fields is unchanged after the write

#### Scenario: First write seeds a pending assignment state
- GIVEN no document exists yet for a given `fuente`/`registro_id`
- WHEN the cruce job writes that point for the first time
- THEN the resulting document has `estado_asignacion:'pendiente'`, `cuadrilla_id:null`,
  `inspector_uid:null`, `prioridad_override:null`

#### Scenario: An unchanged pending point is not rewritten
- GIVEN a point already has a document with `tiene_survey:false`
- WHEN the cruce job runs again and the point still matches no survey
- THEN no write operation is issued for that document

> Implementation note (BINDING user decision 2026-08-26, "auto-close, but reviewable"): the pipeline
> is a second writer of the SINGLE field `estado_asignacion`, for the ONE transition
> `{pendiente,asignado,en_proceso} -> hecho`, and ONLY when the point is re-matched via
> `match_via == 'clave'` on a re-write (never the first write). The pipeline never reopens a `hecho`
> or `no_aplica` point and never touches `cuadrilla_id`/`inspector_uid`. The admin endpoint exposes a
> corresponding `reopen` action to reverse a mistaken auto-close. See Requirement "Round-trip
> traceability from survey back to point" and "Assignment correction actions".

### Requirement: Matching cascade order and tiering
The system MUST evaluate the point↔survey cascade in this order and stop at the first hit:
(1) exact `clave_integracion` == a survey's `codigoapp`, (2) proximity within `MATCH_MAX_M`,
(3) normalized address exact or fuzzy at or above the address threshold, (4) combined
(within the wider combined radius AND at or above the lower combined address threshold), (5) miss.
The system MUST record which rung matched in `match_via` and MUST assign `tier` accordingly
(`exacta` for the key rung; `alta`, `media`, or `sospechoso` for the fuzzy rungs). The fuzzy rungs
MUST be performed by importing the cascade functions from `app.integracion.cruce_gestor`, not by
reimplementing or forking them.

#### Scenario: An exact key match wins over a nearer fuzzy candidate
- GIVEN a point whose `clave_integracion` appears in survey S's `codigoapp`, and a different survey
  T that sits closer to the point's coordinates
- WHEN the cascade runs
- THEN the point matches S with `match_via:'clave'` and `tier:'exacta'`

#### Scenario: The key rung matches nothing before any key is in circulation
- GIVEN no `survey_cali` document carries a non-empty `codigoapp`
- WHEN the cascade runs over every pending point
- THEN no point matches via `clave`, and the fuzzy rungs decide every outcome, without error

#### Scenario: Proximity match is tiered by distance and address agreement
- GIVEN a point 12 m from a survey whose normalized address also agrees
- WHEN the cascade runs
- THEN the point matches with `match_via:'cercania'` and `tier:'alta'`

#### Scenario: A distant, address-only match is tiered lower
- GIVEN a point far outside the proximity threshold whose normalized address matches a survey at or
  above the address threshold
- WHEN the cascade runs
- THEN the point matches with `match_via:'direccion'` and `tier:'media'`

#### Scenario: Neither signal clears its own bar but both agree weakly
- GIVEN a point within the combined radius of a survey whose address similarity is at or above the
  combined threshold but below the address threshold
- WHEN the cascade runs
- THEN the point matches with `match_via:'combinado'` and `tier:'sospechoso'`

#### Scenario: A clean miss stays pending
- GIVEN a point that satisfies no rung
- WHEN the cascade runs
- THEN the document has `tiene_survey:false`, `match_via:null`, `tier:null`, `survey_globalid:null`

### Requirement: Incremental cross-reference with a watermark
The system MUST persist a run watermark under `_meta/` and MUST fetch only `survey_cali` documents
updated after that watermark on a subsequent run. The system MUST determine candidate points via a
projected batched pre-read (not a full-document read of the collection), MUST exclude points already
`tiene_survey:true` from re-scanning, and MUST advance the watermark only after a successful run. A
missing watermark MUST mean "process everything".

#### Scenario: First run processes the full survey set
- GIVEN no watermark document exists
- WHEN the cruce job runs
- THEN it fetches every `survey_cali` document and completes without error

#### Scenario: Subsequent run fetches only new surveys
- GIVEN a watermark from a previous successful run
- WHEN the cruce job runs again
- THEN it fetches only `survey_cali` documents updated after that watermark

#### Scenario: An already-matched point is never re-scanned
- GIVEN a point whose document has `tiene_survey:true`
- WHEN candidate selection runs
- THEN that point is not in the candidate list

#### Scenario: A failed run does not advance the watermark
- GIVEN the cruce job raises before completing its writes
- WHEN the run ends
- THEN the watermark document still holds the previous run's value

#### Scenario: Writes are batched within the Firestore limit
- GIVEN a run produces more than 500 document updates
- WHEN the job writes to `planeacion_puntos`
- THEN the writes are split into batches of at most 500 operations each

### Requirement: Deterministic prioritization
The system MUST compute `prioridad_score` as a pure, deterministic function of the report's
`afectacion` severity, its `estadoVerificacion`, and its age since `fechaCreacion`, weighted so that
severity outranks verification state, which outranks age, and so that the age contribution saturates
and can never outrank a severity difference. The system MUST bucket the score into
`prioridad` ∈ {`alta`, `media`, `baja`}. Unknown or new category values MUST resolve to a documented
fallback weight rather than raising. Geographic clustering MUST NOT contribute to the score.

#### Scenario: Severity outranks verification state
- GIVEN two reports with the same age, one with the highest severity and an unverified state, the
  other with the lowest severity and a verified state
- WHEN both scores are computed
- THEN the high-severity report scores higher

#### Scenario: Age breaks ties but never dominates
- GIVEN two reports with identical severity and verification state but different `fechaCreacion`
- WHEN both scores are computed
- THEN the older report scores higher; AND GIVEN a much older report with strictly lower severity,
  its score is still below the newer, more severe one

#### Scenario: Age saturates
- GIVEN two reports identical except that one is 90 days old and the other 400 days old, both past
  the age saturation window
- WHEN both scores are computed
- THEN their age contributions are equal

#### Scenario: An unknown category does not crash the run
- GIVEN a report whose `afectacion` value is not present in the weight table
- WHEN the score is computed
- THEN a documented fallback weight is used and the run completes

#### Scenario: The same input always yields the same score
- GIVEN the same report record and the same run timestamp
- WHEN the score is computed twice
- THEN both computations return the identical score and `prioridad`

#### Scenario: Comuna does not affect priority
- GIVEN two reports identical in every field except `comuna`
- WHEN both scores are computed
- THEN their scores are equal

> Implementation note: the per-category weights (`PESOS_AFECTACION`, `PESOS_ESTADO`) and
> `AGE_SATURATION_DAYS` shipped as named module constants grounded in the live category values
> observed in production (`afectacion` ∈ {COLAPSO TOTAL, COLAPSO PARCIAL, RIESGO COLAPSO, DAÑO
> ESTRUCTURAL, DAÑO MAMPOSTERÍA, NO SE EVIDENCIA NINGÚN DAÑO}; `estadoVerificacion` ∈ {Reportado,
> Asignado, Evaluación especializada, Visitado, Visita fallida, Visitado crítico}), but were never
> separately re-confirmed against a dedicated operations-lead ranking round. `prioridad_override`
> remains the per-point escape hatch.

### Requirement: `planeacion_cuadrillas` document shape
The system MUST store one `planeacion_cuadrillas/{id}` document per group of pending points,
carrying `nombre`, `puntos` (array of `planeacion_puntos` doc ids), `inspector_uid` (nullable),
`origen` (`'auto'` or `'manual'`), and `creada_en`. Membership MUST be reflected on each member
point's `cuadrilla_id`. A point MUST belong to at most one cuadrilla. This collection MUST be
separate from the sticker campaign's `cuadrillas`.

#### Scenario: Creation sets membership on member points
- GIVEN a new cuadrilla is created (auto or manual) containing points P1, P2, P3
- WHEN the cuadrilla document is written
- THEN `planeacion_puntos/P1`, `P2`, `P3` each have `cuadrilla_id` set to the new cuadrilla's id

#### Scenario: A point cannot be silently moved between cuadrillas
- GIVEN point P already belongs to cuadrilla C1
- WHEN an admin tries to add P to cuadrilla C2
- THEN the call is rejected with an explanatory error and no document is modified

#### Scenario: The sticker campaign's collection is untouched
- GIVEN any action in this change
- WHEN it executes
- THEN no write operation targets `sticker_matches` or `cuadrillas`

### Requirement: `POST /planeacion-asignaciones` is admin-only
The system MUST expose every action behind a single POST endpoint that verifies a Firebase ID token
and rejects any caller whose role is not admin, mirroring the existing `/sticker-asignaciones`
auth dependency. A rejected call MUST cause zero Firestore state change.

#### Scenario: Non-admin call is rejected
- GIVEN a valid Firebase ID token belonging to a viewer, usuario, or inspector account
- WHEN that token is used to call any `/planeacion-asignaciones` action
- THEN the request is rejected with 403 and no Firestore document is written

#### Scenario: Unauthenticated call is rejected
- GIVEN a request with no Authorization header
- WHEN it is sent to `/planeacion-asignaciones`
- THEN it is rejected and no Firestore document is written

#### Scenario: An unknown action is rejected
- GIVEN an admin token and a body with `action:'borrarTodo'`
- WHEN the request is sent
- THEN it fails with a 400 naming the unknown action, and no Firestore document is written

### Requirement: `listPuntos` returns a bounded, prioritized working set
The system MUST return points ordered by effective priority descending, MUST apply a result limit
with a documented default and hard maximum, MUST default to excluding points that already have a
survey and points marked `no_aplica`, and MUST report whether the result was truncated. The system
MUST NOT return the full collection unbounded.

#### Scenario: Default query returns only the actionable pool
- GIVEN the collection holds surveyed points, excluded points, and pending points
- WHEN `listPuntos` is called with no filters
- THEN the response contains only points with `tiene_survey:false` and
  `estado_asignacion != 'no_aplica'`

#### Scenario: Results are ordered by priority
- GIVEN pending points with mixed `prioridad_score` values
- WHEN `listPuntos` is called
- THEN the returned points are ordered by descending effective priority

#### Scenario: Truncation is reported, not hidden
- GIVEN more pending points exist than the requested limit
- WHEN `listPuntos` is called
- THEN the response contains at most `limit` points and `truncado` is `true`

#### Scenario: A limit above the hard maximum is clamped
- GIVEN a caller requests a limit above the documented hard maximum
- WHEN `listPuntos` is called
- THEN the effective limit is the hard maximum, and the call does not fail

#### Scenario: An admin priority override is respected in ordering
- GIVEN a point whose computed `prioridad` is `baja` and whose `prioridad_override` is `alta`
- WHEN `listPuntos` orders the working set
- THEN that point is ordered as `alta`

> Implementation note: `listPuntos` over-fetches to `LIMIT_MAX + 1` candidates ordered by raw
> `prioridad_score`, then re-sorts by override-aware effective priority in code before slicing to the
> requested limit (Firestore cannot express an override-aware sort at the query level, since
> `prioridad_override` is intentionally admin-owned and invisible to the pipeline). A point whose
> override promotes it but whose raw score falls outside that over-fetch window is not guaranteed to
> surface under a simultaneously very small requested `limit` on a >5000-pending collection — a known,
> accepted edge case. `LIMIT_DEFAULT` is `300` (lowered from an initial `2000` for UI render speed);
> `LIMIT_MAX` is `5000`.

### Requirement: `resumen` returns aggregate tallies without shipping the working set
The system MUST expose an action returning aggregate counts over the whole collection — total,
surveyed, pending broken down by `prioridad`, by `comuna`, and by `estado_asignacion`, plus a tally
by `match_via` — without transferring the individual point documents to the caller.

#### Scenario: Totals are available without a full read
- GIVEN a collection of many thousands of points
- WHEN `resumen` is called
- THEN the response contains the counts and contains no per-point document payload

#### Scenario: The match-provenance tally is exposed
- GIVEN surveyed points matched by different rungs
- WHEN `resumen` is called
- THEN the response includes a per-`match_via` count, so an operator can see how many surveys were
  matched by key versus by proximity or address

### Requirement: `autoAgrupar` clusters pending points deterministically
The system MUST cluster pending, ungrouped points using a deterministic greedy nearest-neighbour
pass (stable sort order, no RNG, no k-means), respecting a maximum radius and a maximum group size,
MUST create cuadrilla documents with `origen:'auto'` and `inspector_uid:null`, MUST exclude points
that already have a survey or are marked `no_aplica`, and MUST NOT change any point's
`estado_asignacion`.

#### Scenario: Same input twice produces the same groups
- GIVEN an unchanged set of pending, ungrouped points
- WHEN `autoAgrupar` is called twice with the same parameters
- THEN both calls produce groups with identical membership

#### Scenario: Group size respects the cap
- GIVEN more points than the size cap fall within the radius of a seed
- WHEN `autoAgrupar` runs
- THEN no resulting group contains more than the size cap

#### Scenario: Group radius respects the cap
- GIVEN a candidate point lies farther than the radius from every group seed
- WHEN `autoAgrupar` runs
- THEN it is not added to any of those groups

#### Scenario: Surveyed and excluded points are never grouped
- GIVEN the pending query returns points including one with `tiene_survey:true` and one with
  `estado_asignacion:'no_aplica'`
- WHEN `autoAgrupar` runs
- THEN neither point appears in any created cuadrilla

#### Scenario: Auto-agrupar on an empty pending set is a no-op
- GIVEN no pending, ungrouped points exist
- WHEN `autoAgrupar` is called
- THEN it returns successfully with zero cuadrillas created and no error

#### Scenario: Auto-agrupar does not assign an inspector
- GIVEN `autoAgrupar` creates a cuadrilla from three pending points
- WHEN the resulting documents are inspected
- THEN `inspector_uid` is `null` on the cuadrilla and `estado_asignacion` is still `'pendiente'` on
  every member point

> Implementation note: `DEFAULT_MAX_SIZE = 10` (BINDING user decision, overriding the sticker
> template's default of 8); `DEFAULT_MAX_RADIUS_M = 800`, unconfirmed but carried over with a visible
> per-call override.

### Requirement: Assignment lifecycle actions
The system MUST support creating a cuadrilla manually from an explicit point list
(`origen:'manual'`), adding and removing points from an existing cuadrilla, assigning an inspector
to a cuadrilla (propagating `inspector_uid`, `asignado_en`, and `estado_asignacion:'asignado'` to
every member point), removing an inspector from a cuadrilla while keeping the group intact, deleting
a cuadrilla after clearing its member points, and undoing all auto-grouping while leaving manual
cuadrillas intact.

#### Scenario: Manual creation from selected points
- GIVEN an admin selects a set of pending point ids
- WHEN `crearCuadrilla` is called with `{nombre, puntos}`
- THEN a cuadrilla is created with `origen:'manual'` and every listed point's `cuadrilla_id` is set

#### Scenario: Creating a cuadrilla from an already-surveyed point is rejected
- GIVEN the selection includes a point with `tiene_survey:true`
- WHEN `crearCuadrilla` is called
- THEN the call fails with an error naming the offending points and no document is modified

#### Scenario: Removing a point clears its membership
- GIVEN point P is a member of cuadrilla C
- WHEN `editarCuadrilla` removes P from C
- THEN P has `cuadrilla_id:null` and C's `puntos` no longer includes P

#### Scenario: Editing a nonexistent cuadrilla fails cleanly
- GIVEN a cuadrilla id with no matching document
- WHEN `editarCuadrilla` is called with that id
- THEN the call fails with an error and no point document is modified

#### Scenario: Assigning an inspector updates every member point
- GIVEN a cuadrilla with three member points, none assigned
- WHEN `asignarInspector` is called
- THEN all three points have the given `inspector_uid`, `estado_asignacion:'asignado'`, and a
  non-null `asignado_en`

#### Scenario: Unassigning keeps the cuadrilla but releases its points
- GIVEN a cuadrilla with an assigned inspector and three member points
- WHEN `desasignarInspector` is called
- THEN the cuadrilla still exists with its membership, `inspector_uid` is `null` on the cuadrilla and
  on every member point, and each point is back to `estado_asignacion:'pendiente'`

#### Scenario: Reassigning a point records a breadcrumb
- GIVEN point P is in cuadrilla C and assigned to inspector A
- WHEN `reasignarPunto` is called with a new inspector B
- THEN P's `inspector_uid` becomes B, `reasignado_de` becomes A, and `cuadrilla_id` is unchanged

#### Scenario: Reassigning an unassigned point
- GIVEN point P has `inspector_uid:null`
- WHEN `reasignarPunto` is called with inspector B
- THEN P's `inspector_uid` becomes B and `reasignado_de` is `null`

#### Scenario: Deleting a cuadrilla releases its points
- GIVEN cuadrilla C has member points P1, P2 with `inspector_uid` set
- WHEN `eliminarCuadrilla` is called
- THEN P1 and P2 have `cuadrilla_id:null` and `inspector_uid:null`, and C no longer exists

#### Scenario: Restarting grouping spares manual cuadrillas
- GIVEN both `origen:'auto'` and `origen:'manual'` cuadrillas exist
- WHEN `reiniciarAgrupacion` is called
- THEN every auto cuadrilla is deleted and its points released to `pendiente`, and every manual
  cuadrilla and its membership are unchanged

### Requirement: Assignment correction actions
The system MUST let an admin correct a single point's assignment state directly, without going
through its cuadrilla, via an action accepting a partial field set (`estado_asignacion`,
`prioridad_override`, `inspector_uid`, `notas`) where only the keys present in the request are
written. Every such correction MUST stamp `editado_en` and `editado_por` with the acting admin's
uid. The system MUST also let an admin exclude a point from the pending pool with a **mandatory**
reason, and MUST make that exclusion reversible.

#### Scenario: Partial correction leaves untouched fields alone
- GIVEN point P has `notas:'portería cerrada'` and `prioridad_override:null`
- WHEN `editarAsignacion` is called with only `{punto_id: P, prioridad_override:'alta'}`
- THEN P's `prioridad_override` is `alta` and its `notas` is still `'portería cerrada'`

#### Scenario: An explicit null clears a field
- GIVEN point P has `notas:'portería cerrada'`
- WHEN `editarAsignacion` is called with `{punto_id: P, notas: null}`
- THEN P's `notas` is `null`

#### Scenario: Every correction is attributable
- GIVEN an admin with uid `u9` calls `editarAsignacion` on point P
- WHEN the write completes
- THEN P has `editado_por:'u9'` and a non-null `editado_en`

#### Scenario: An inspector can be corrected without touching the cuadrilla
- GIVEN point P is a member of cuadrilla C assigned to inspector A
- WHEN `editarAsignacion` sets `inspector_uid` to B
- THEN P's `inspector_uid` is B and P's `cuadrilla_id` is still C

#### Scenario: Exclusion requires a reason
- GIVEN an admin calls `marcarNoAplica` with no `motivo_exclusion`
- WHEN the request is processed
- THEN it fails with a 400 and the point is unchanged

#### Scenario: Exclusion removes the point from the pending pool
- GIVEN point P is pending
- WHEN `marcarNoAplica` is called with a reason
- THEN P has `estado_asignacion:'no_aplica'` and the stored reason, and P is absent from a
  default `listPuntos` result

#### Scenario: Exclusion is reversible
- GIVEN point P is marked `no_aplica`
- WHEN `marcarNoAplica` is called with `{punto_id: P, revertir: true}`
- THEN P has `estado_asignacion:'pendiente'` and `motivo_exclusion:null`

#### Scenario: The pipeline never overwrites a correction
- GIVEN an admin set `prioridad_override:'alta'`, `notas`, and `motivo_exclusion` on point P
- WHEN the cruce job next writes P's pipeline fields
- THEN all three admin-owned values are unchanged

> Implementation note: a dedicated `reopen` action (`{punto_id}`) is the admin counterpart to the
> pipeline's one binding auto-close exception (see "planeacion_puntos document ownership and merge
> safety"): it validates the point is currently `hecho`, then sets `estado_asignacion:'pendiente'`,
> stamping `editado_en`/`editado_por`, and never touches `tiene_survey`/`match_via`. `editarAsignacion`
> can also perform this transition generically via `{estado_asignacion:'pendiente'}`; `reopen` exists
> alongside it as a purpose-built, separately validated action. `reopen` is also reachable by the
> assigned inspector themselves for their own points, via `misPuntosPlaneacion`/
> `marcarHechoPlaneacion` on the existing `routers/inspector_asignaciones.py` (added 2026-08-26 to
> close a visibility gap — see "Round-trip traceability" below).

### Requirement: `getEnlaceSurvey` builds a prefilled Survey123 URL from configuration
The system MUST build the Survey123 URL server-side from a configured form URL, appending the
point's `clave_integracion` as the `field:codigoapp` prefill parameter, URL-encoded, using the
correct query separator for the configured URL. When a field-app item id is configured, the system
MUST also return the `arcgis-survey123:///` deep link. The form URL and item id MUST come from
configuration, MUST NOT be hardcoded in the repository, and when the form URL is absent the action
MUST fail loudly rather than return a partial or placeholder link. Only `codigoapp` MUST be
prefilled; no other survey question may be prefilled.

#### Scenario: The web link carries the key
- GIVEN a configured form URL and a point whose `clave_integracion` is `PLN-14832-9C4A1F0B`
- WHEN `getEnlaceSurvey` is called for that point
- THEN the returned web URL contains `field:codigoapp=PLN-14832-9C4A1F0B`

#### Scenario: The separator adapts to the configured URL
- GIVEN a configured form URL that already contains a query string
- WHEN the link is built
- THEN the prefill parameter is appended with `&`, not a second `?`

#### Scenario: The field-app link is optional
- GIVEN no field-app item id is configured
- WHEN `getEnlaceSurvey` is called
- THEN the response's web link is present and its app link is `null`

#### Scenario: Missing configuration fails loudly
- GIVEN the form URL is not configured
- WHEN `getEnlaceSurvey` is called
- THEN the call fails with an explicit error naming the missing configuration, and no URL is returned

#### Scenario: No other question is prefilled
- GIVEN any point, however complete its data
- WHEN the survey URL is built
- THEN the only `field:` parameter present is `field:codigoapp`

### Requirement: Round-trip traceability from survey back to point
The system MUST, on the run following a survey submitted through a prefilled link, match that survey
to its originating point via the exact-key rung, set `tiene_survey:true`, record the matched survey's
`GlobalID` in `survey_globalid`, set `match_via:'clave'`, and remove the point from the default
pending working set.

#### Scenario: A keyed survey closes its own point
- GIVEN point P was assigned and its `clave_integracion` was prefilled into a submitted survey S
- WHEN the survey is ingested into `survey_cali` and the cruce job next runs
- THEN P has `tiene_survey:true`, `survey_globalid` equal to S's `GlobalID`, and `match_via:'clave'`

#### Scenario: A closed point leaves the working set
- GIVEN point P now has `tiene_survey:true`
- WHEN `listPuntos` is called with default filters
- THEN P is not in the result

#### Scenario: A survey carrying an unknown key does not corrupt a point
- GIVEN a survey whose `codigoapp` holds a well-formed key that matches no existing point
- WHEN the cruce job runs
- THEN no point is marked as surveyed by that key and the run completes without error

### Requirement: Planeación tab mounting and admin-only role gating
The system MUST mount "Planeación" as a new top-level view tab, sibling to Stickers, with its own
`data-view-panel` section, its own `switchView()` branch that (re)initializes the module on each
open, and its own entry in the admin-only CSS role-gating selector list. The system MUST route the
tab's API calls through the per-endpoint URL map rather than a hardcoded path.

#### Scenario: The tab is hidden from non-admins
- GIVEN a signed-in user whose role is viewer, usuario, or inspector
- WHEN the dashboard renders
- THEN the Planeación tab button is not visible

#### Scenario: The tab is visible to admins
- GIVEN a signed-in admin
- WHEN the dashboard renders
- THEN the Planeación tab button is visible alongside Stickers

#### Scenario: Opening the tab initializes the view
- GIVEN an admin clicks the Planeación tab
- WHEN the view switches
- THEN the Planeación module renders into its own section and loads its data

#### Scenario: Reopening the tab refreshes the data
- GIVEN an admin leaves Planeación and returns to it
- WHEN the view switches back
- THEN the module re-initializes and re-fetches, matching the other admin tabs' lifecycle

#### Scenario: The endpoint URL comes from the config map
- GIVEN the Planeación module issues an API call
- WHEN the request URL is resolved
- THEN it comes from the per-endpoint URL map, not a literal path in the module

### Requirement: Vehiculo modal assigns only an existing conductor
The vehiculo create/edit modal MUST offer an `empresa` free-text input and a `<select>` of existing
conductores to assign, and MUST NOT offer an inline "crear conductor" path. Creating a new
conductor MUST happen only through the Usuarios modal's `tipo='conductor'` fan-out.

#### Scenario: Vehiculo save with an existing conductor
- GIVEN an admin opens the vehiculo modal and selects an existing conductor plus an `empresa`
  value
- WHEN the vehiculo is saved
- THEN the vehiculo document stores `conductor_id` and `empresa`, and no `crearConductor` call is
  made

#### Scenario: No inline conductor-creation UI is reachable
- GIVEN the vehiculo modal is open
- WHEN the admin looks for a way to create a conductor without leaving the modal
- THEN no such control exists; only the existing-conductor selector is present

### Requirement: Inspector roster CRUD lives in Planeación
The system MUST render the inspector roster (list, create-inspector, enable/disable) as a segment
inside the Planeación tab, calling the same `api/stickers.js` actions via the existing
`callStickersApi` client, and MUST NOT fork or duplicate that client. The roster segment MUST NOT
exist inside the Stickers tab.

#### Scenario: Roster is usable from Planeación
- GIVEN an admin opens the Planeación tab's roster segment
- WHEN they create, list, or disable an inspector
- THEN the call goes through `callStickersApi` to `api/stickers.js`, and the change is reflected
  in the Planeación roster list

#### Scenario: Roster is absent from Stickers
- GIVEN an admin opens the Stickers tab
- WHEN the segmented control renders
- THEN it offers only Evaluaciones and Asignación; no Roster segment exists

### Requirement: Planeación UI — priority table, map, and correction affordances
The system MUST render the working set as a sortable, filterable table ordered by effective
priority and a Leaflet map with a legend distinguishing surveyed, high-priority pending, other
pending, assigned/in-progress, and excluded points. The system MUST provide controls to
auto-group, create a cuadrilla from a manual selection, assign an inspector to a cuadrilla
(group-only), correct a point's assignment, mark a point as not applicable with a reason, open or
copy the point's prefilled survey link, and manage the inspector roster (list/create/enable/
disable) in its own segment. The system MUST NOT render the individual cuadrilla-inspector
combobox (`asignarInspector` at the point level), the map per-point reassign select
(`reasignarPunto`), or the individual desasignar button (`desasignarInspector`) — group assignment
(`asignarGrupoAPuntos`) remains the only assignment path in the UI; their backend actions MUST
remain callable. When the working set is truncated the UI MUST say so.

#### Scenario: Table is ordered by priority by default
- GIVEN the table renders a mixed working set
- WHEN it first appears
- THEN rows are ordered by effective priority, highest first

#### Scenario: Filtering narrows the working set
- GIVEN the table contains points across several comunas and priorities
- WHEN the admin selects a `prioridad` and a `comuna` filter
- THEN only matching rows remain visible

#### Scenario: Map legend distinguishes the five states
- GIVEN points exist in each of the five legend states
- WHEN the map renders
- THEN each state's markers use its own legend colour and the legend identifies all five

#### Scenario: Truncation is surfaced to the operator
- GIVEN `listPuntos` returned `truncado:true`
- WHEN the view renders
- THEN it displays how many points are shown out of how many pending

#### Scenario: The survey link is reachable per point
- GIVEN an admin opens a point's row actions
- WHEN they choose the survey-link action
- THEN the prefilled link is fetched from the endpoint and offered to open or copy

#### Scenario: A correction updates the view without a full reload
- GIVEN an admin corrects a point's priority override from the table
- WHEN the call succeeds
- THEN the row re-renders with the new value without re-fetching the whole working set

#### Scenario: The inspector roster is available in a top-level tab
- GIVEN the admin opens Planeación without having opened the Stickers tab in this session
- WHEN an inspector selection control renders
- THEN it is populated with the enabled inspectors, because the module loads the roster itself

#### Scenario: No individual-assignment control is reachable
- GIVEN an admin views a cuadrilla or a map point
- WHEN they look for a per-point inspector combobox, a per-point reassign select, or an individual
  desasignar button
- THEN none of those controls exist; only group assignment on the cuadrilla is available

#### Scenario: Group assignment still works after hiding individual controls
- GIVEN a cuadrilla with member points
- WHEN an admin calls `asignarGrupoAPuntos` for that cuadrilla
- THEN every member point receives the assigned inspector, exactly as before this change

> Implementation note: the "incluir levantados" map toggle is present in the DOM but disabled — the
> backend's `list_puntos()` applies `tiene_survey == false` unconditionally at the Firestore query
> level with no request parameter to override it, so wiring the toggle would silently no-op. Flagged
> as an open backend/frontend contract gap for a future patch, not fixed as part of this change.

### Requirement: Sole-writer invariant for the new collections
The system MUST enforce, by an automated repository scan, that the literals `planeacion_puntos` and
`planeacion_cuadrillas` appear under `backend/app/` only inside explicitly allowlisted modules, with
each collection governed by its own independent allowlist. The existing `sticker_matches`,
`cuadrillas`, and `survey_cali` allowlists MUST NOT be modified by this change.

#### Scenario: An unallowlisted write path fails the invariant
- GIVEN a new module under `backend/app/` references `planeacion_puntos`
- WHEN the invariant test runs
- THEN it fails, naming the unexpected module

#### Scenario: Allowlists are independent
- GIVEN the new collections' allowlists
- WHEN the invariant test suite runs
- THEN the `sticker_matches`, `cuadrillas`, and `survey_cali` allowlists are unchanged and their
  tests still pass

> Implementation note: `ALLOWED_MODULES_PLANEACION_PUNTOS` has three legitimate, independently
> flagged entries — the pipeline (`jobs/planeacion_cruce.py`), the admin dashboard
> (`routers/planeacion_asignaciones.py`), and the inspector's own-uid-scoped surface
> (`routers/inspector_asignaciones.py`, added 2026-08-26 to close the assignee-visibility gap; see
> "Round-trip traceability" and "Assignment correction actions"). `planeacion_cruce.py` also has one
> flagged, read-only entry in the pre-existing, otherwise-CLOSED `ALLOWED_MODULES_SURVEY_CALI` set,
> because it genuinely needs a live read of `survey_cali` to fetch surveys for the cascade.

### Requirement: Scope boundaries
The system MUST NOT write to the `survey_cali` collection from any module this change adds — it
MUST NOT call the survey mutation core, and MUST NOT create a `survey_cali` history revision. The
system MUST NOT write to ArcGIS or Survey123 — no feature create, update, or `applyEdits` call. The
system MUST NOT reference the `dagma-85aad` project id, the `cruce_criticos_survey`
collection, the legacy `integracion_F1` credential resolution, or any dagma-related constant
anywhere under `backend/`. The system MUST NOT open a client-readable Firestore rule for either
new collection. The system MUST NOT allow an admin to edit the report's own pipeline-owned data
(address, coordinates, severity) through the assignment endpoint. Inspector-roster CRUD
(create/enable/disable) now legitimately lives in the Planeación tab's roster segment, calling
`api/stickers.js` — this is the one exception to the prior no-roster-CRUD boundary and MUST NOT be
duplicated into any new backend endpoint.

#### Scenario: `survey_cali` is never written
- GIVEN any action of the endpoint or any step of the cruce job
- WHEN it executes
- THEN no write targets `survey_cali` and no history revision is created

#### Scenario: ArcGIS is never written
- GIVEN any module this change adds
- WHEN it is inspected
- THEN it contains no ArcGIS feature-editing call; the only ArcGIS interaction is URL construction

#### Scenario: No dagma reference exists in the backend
- GIVEN the `backend/` tree after this change
- WHEN it is scanned for the dagma project id, the `cruce_criticos_survey` collection name, and
  the legacy credential env vars
- THEN there are zero matches

#### Scenario: Pipeline-owned report data is not editable by an admin
- GIVEN an admin sends `editarAsignacion` with a `direccion` or `coords` key
- WHEN the request is processed
- THEN the pipeline-owned field is not written

#### Scenario: Direct client Firestore read is denied
- GIVEN a browser client attempts to read `planeacion_puntos` or `planeacion_cuadrillas` directly
  via the Firestore client SDK
- WHEN the read is attempted
- THEN Firestore security rules deny it

#### Scenario: Inspector-roster CRUD calls the existing Stickers endpoint, not a new one
- GIVEN the Planeación roster segment creates or disables an inspector
- WHEN the request is inspected
- THEN it targets `api/stickers.js`, and no new FastAPI inspector-CRUD endpoint exists under
  `backend/app/routers/planeacion_asignaciones.py`
</content>
