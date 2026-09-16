# Inspectores Depurado Specification

Change: `seguimiento-inspectores-depurado` · New capability (no prior spec exists).

## Purpose

A backend-computed, cached table of de-duplicated inspector identity and field-readiness status
(`np`, `fase`, `estado_sugerido`, `fuente_dato`), replacing ad-hoc consumption of raw live
API/Firestore identity fields by the frontend. The system MUST NOT read the static xlsx at runtime.

## Requirements

### Requirement: NP Resolution Hierarchy
The system MUST resolve `np`/`np_fuente` per inspector using: Fase 2 NP (if `en_fase2` and non-empty)
→ Vercel NP (if `en_vercel` and non-empty) → `rango_main` (base CSV) → empty string with
`np_fuente="ninguno"`.

#### Scenario: Fase 2 wins over Vercel and main
- GIVEN an inspector is in Fase 2 with `np_fase2="P3"` and also in Vercel with a different NP
- WHEN the resolver runs
- THEN `np="P3"` and `np_fuente="fase2"`

#### Scenario: No source has an NP
- GIVEN an inspector is absent from Fase 2 and Vercel, with empty `rango_main`
- WHEN the resolver runs
- THEN `np=""` and `np_fuente="ninguno"` (no NP is invented)

### Requirement: Fase Calculation From NP
The system MUST compute `fase` by matching `np` against `^P?\s*(\d+)`; a captured number >= 3 yields
`"Fase II"`, otherwise `"Fase I"`. When the pattern does not match, `fase="Fase I"` and
`fase_np_faltante=true`.

#### Scenario: Numeric NP below threshold
- GIVEN `np="P2"`
- WHEN fase is computed
- THEN `fase="Fase I"` and `fase_np_faltante=false`

#### Scenario: Non-matching NP defaults safely
- GIVEN `np=""` or `np="NO SIRVE"`
- WHEN fase is computed
- THEN `fase="Fase I"` and `fase_np_faltante=true`

### Requirement: Estado Sugerido Precedence (Advisory Only)
The system MUST classify each inspector into exactly one `estado_sugerido`, in this order:
(1) `"no_persona"` if the non-person heuristic fires; (2) `"candidato_desactivacion"` if no
código, no valid sticker, and absent from Fase 2 and Vercel; (3) `"revisar"` if no código, no
valid sticker, but present in Fase 2 or Vercel; (4) `"activo"` if a código is present;
(5) `"revisar"` as fallback. `tiene_sticker_valido` (post-20-Aug activity) is NEVER a trigger for
`"activo"` — per the user's explicit decision, recent-activity status must not drive the
classification. It is surfaced only as an informational field (alongside `dias_inactivo`) on the
same record. This classification is advisory only — the system MUST NOT perform any automatic
deactivation, deletion, or write to the live inspector record as a side effect (resolves the D6
contradiction: the 20-Aug cutoff feeds only the `tiene_sticker_valido`/`dias_inactivo` informational
fields, it never drives `estado_sugerido` or a person-level deactivation write).

#### Scenario: No reference match → candidate for deactivation
- GIVEN an inspector has no código, `tiene_sticker_valido=false`, and is absent from Fase 2/Vercel
- WHEN classification runs
- THEN `estado_sugerido="candidato_desactivacion"`

#### Scenario: Same gap but present in a reference source → review, not deactivation
- GIVEN the same inspector but present in Vercel
- WHEN classification runs
- THEN `estado_sugerido="revisar"`

#### Scenario: Recent sticker activity alone never yields "activo"
- GIVEN an inspector has no código, `tiene_sticker_valido=true` (a valid post-20-Aug sticker), and
  is absent from Fase 2/Vercel
- WHEN classification runs
- THEN `estado_sugerido="candidato_desactivacion"`, NOT `"activo"` — recent activity is informational
  only and never substitutes for having a real código assigned

#### Scenario: Classification never writes to the inspector record
- GIVEN any inspector is classified as `candidato_desactivacion`
- WHEN classification completes
- THEN no field on the live Firestore inspector document changes

### Requirement: Priority Heuristics Are Not Auto-Disqualifying
`cedula_sospechosa` (digit-only cédula length outside 6-10, or exactly 10 digits not starting with
`"1"`) and `es_cuenta_no_persona` (email/name pattern match) MUST be used only to prioritize manual
review or trigger non-person aggregation, and MUST NOT by themselves force any `estado_sugerido`
outside the dedicated `"no_persona"` branch.

#### Scenario: Suspicious cédula alone does not force non-person classification
- GIVEN `cedula_sospechosa=true` but `es_cuenta_no_persona=false`
- WHEN classification runs
- THEN `estado_sugerido` is not forced to `"no_persona"` by the cédula check alone

### Requirement: 20-August Cutoff Scoped To `sistema` Origin
The system MUST compute `tiene_sticker_valido` using only stickers where `origen=="sistema"` AND
`fechaCreacion >= 2026-08-20T00:00:00Z`. Stickers with `origen=="firebase"` (bulk-import timestamp,
not a real creation date) MUST be excluded from this comparison.

#### Scenario: Firebase-origin stickers never count toward the cutoff
- GIVEN an inspector has only `origen=="firebase"` stickers imported after 2026-08-20
- WHEN `tiene_sticker_valido` is computed
- THEN it is `false`

#### Scenario: Sistema-origin sticker before the cutoff does not count
- GIVEN an inspector's only `origen=="sistema"` sticker predates 2026-08-20
- WHEN `tiene_sticker_valido` is computed
- THEN it is `false`

### Requirement: Non-Person Account Aggregation With Drill-Down
The system MUST group non-person-flagged inspectors (excluding any with a non-empty `codigo`) that
have a null or stale (>7 days) last sticker into one aggregated row, hidden from the primary
inspector list. The system MUST expose both the aggregate counts AND the underlying per-inspector
detail on demand — never only the total.

#### Scenario: Aggregate row hides individual rows from the main list
- GIVEN 5 inspectors qualify for non-person aggregation
- WHEN the main inspector list is requested
- THEN those 5 do not appear individually; one aggregate row represents them

#### Scenario: Detail is retrievable behind the aggregate
- GIVEN the aggregate row exists
- WHEN its detail is requested
- THEN the response includes each of the 5 underlying inspectors' individual data

#### Scenario: A flagged account with a código is excluded from aggregation
- GIVEN an inspector matches the non-person heuristic but has a non-empty `codigo`
- WHEN aggregation runs
- THEN that inspector remains a standalone row

### Requirement: Non-Person Counts Deduped Against survey_cali
When computing counts for the non-person aggregate, the system MUST cross-reference `survey_cali`
by normalized person name and MUST NOT double-count field work already attributed there.

#### Scenario: Same person's survey_cali work is not double-counted
- GIVEN a non-person-flagged account's name matches a `survey_cali` record for the same visit
- WHEN the aggregate count is computed
- THEN that visit is counted once, not once per source

### Requirement: Identity Anchor Is Firestore `identificacion`
The system MUST use Firestore `inspector_profiles()`'s `identificacion` field as the sole identity
anchor for matching/grouping, and MUST NOT derive or substitute an identity key from an
email-derived cédula.

#### Scenario: Email-derived cédula never overrides identificacion
- GIVEN an inspector's `identificacion` differs from a cédula guessed from their email
- WHEN identity matching runs
- THEN grouping uses `identificacion`, never the email-derived value

### Requirement: Code Remap And Duplicate Unification
The system MUST remap `codigo` against the Vercel roster by cédula match, falling back to
`rapidfuzz.fuzz.token_sort_ratio(nombre_norm) >= 90` only to SURFACE candidates, per:

| Case | Rule |
|---|---|
| D-P1: Vercel roster has 2+ entries sharing a code | Exclude that code from remap; list for manual review |
| D-P2: Same person, multiple cédulas | Unify only on exact name match; report discrepancies |
| D1: Sticker code changes owner | Attribute to the CURRENT titular; no ownership history kept |
| D2: Merge outcome for a duplicate | Exclude/mark the duplicate in the computed view; NEVER hard-delete |
| D3: Fuzzy name match 85-99% | Never auto-merge; always route to manual review |
| D5: Fase 2 row without NP | `np=""`, `np_fuente="ninguno"`; MUST NOT invent `pasos` |

#### Scenario: Duplicated Vercel code is excluded, not guessed
- GIVEN two Vercel roster entries share the same `codigo`
- WHEN remap runs
- THEN that `codigo` is excluded from automatic remap and listed for manual review

#### Scenario: Fuzzy match never merges automatically
- GIVEN two inspector names score 90% similarity
- WHEN unification runs
- THEN no automatic merge occurs; the pair is surfaced for manual review

#### Scenario: Exact-name duplicate merge never deletes the losing record
- GIVEN two records share an exact normalized name and are unified
- WHEN the computed view is produced
- THEN the losing record is excluded/marked, but its Firestore document still exists

### Requirement: Response Caching With TTL
The system MUST cache the computed table for 5-10 minutes (`EVALUACIONES_CACHE_TTL_SECONDS`
pattern) and MUST serve the last-good cached value if a live source is unavailable, rather than
failing the request.

#### Scenario: Cache serves within TTL without recomputation
- GIVEN the table was computed less than the TTL ago
- WHEN a new request arrives
- THEN the cached value is returned without recomputing

#### Scenario: Unavailable reference source degrades to last-good cache
- GIVEN the reference Blob is temporarily unreachable
- WHEN a request arrives after the cache would normally expire
- THEN the system serves the last successfully computed value instead of an error
