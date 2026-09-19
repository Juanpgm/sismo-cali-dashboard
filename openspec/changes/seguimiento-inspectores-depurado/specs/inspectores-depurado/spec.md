# Inspectores Depurado Specification

Change: `seguimiento-inspectores-depurado` · New capability (no prior spec exists).

## Purpose

A backend-computed, cached table of de-duplicated inspector identity and field-readiness status
(`np`, `fase`, `estado_sugerido`, `fuente_dato`), replacing ad-hoc consumption of raw live
API/Firestore identity fields by the frontend. The system MUST NOT read the static xlsx at runtime.

> **Extension 2026-09-19.** The `## RENAMED / MODIFIED / ADDED Requirements` sections at the END of
> this file are the delta for the "complete base" extension. Where a requirement below is annotated
> `[SUPERSEDED — see extension delta]`, the block at the end of the file is authoritative and
> REPLACES it at archive time. Unannotated requirements are unchanged.

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
(1) `"no_persona"` if the non-person heuristic fires; (2) `"candidato_desactivacion"` if the
inspector has NEVER had any sticker (`tiene_sticker=false`, the raw all-time flag — NOT
`tiene_sticker_valido`, the post-20-Aug-informational one), no código, and is absent from both
Fase 2 and Vercel; (3) `"revisar"` if the inspector has never had any sticker, no código, but is
present in Fase 2 or Vercel; (4) `"activo"` if, and only if, a código is present —
`tiene_sticker_valido` is NEVER a trigger for `"activo"`, per the user's explicit decision that
recent-activity status must not drive the classification; (5) `"revisar"` as the fallback for
everyone else — in practice this is anyone with no código who HAS had at least one sticker at some
point (`tiene_sticker=true`) but isn't currently código-holding, regardless of whether that history
includes a post-20-Aug-valid sticker and regardless of Fase 2/Vercel membership (both are already
exhausted by branches 2-3, which require `tiene_sticker=false`).

`tiene_sticker_valido`/`dias_inactivo` (post-20-Aug activity) are surfaced only as informational
fields on the record and never participate in this precedence at all. This classification is
advisory only — the system MUST NOT perform any automatic deactivation, deletion, or write to the
live inspector record as a side effect (resolves the D6 contradiction: the 20-Aug cutoff feeds only
the `tiene_sticker_valido`/`dias_inactivo` informational fields, it never drives `estado_sugerido`
or a person-level deactivation write).

#### Scenario: Never had any sticker, no reference match → candidate for deactivation
- GIVEN an inspector has `tiene_sticker=false`, no código, and is absent from Fase 2/Vercel
- WHEN classification runs
- THEN `estado_sugerido="candidato_desactivacion"`

#### Scenario: Same gap but present in a reference source → review, not deactivation
- GIVEN the same inspector but present in Vercel
- WHEN classification runs
- THEN `estado_sugerido="revisar"`

#### Scenario: Código alone yields "activo", sticker recency is irrelevant
- GIVEN an inspector has a non-empty código and `tiene_sticker_valido=false`
- WHEN classification runs
- THEN `estado_sugerido="activo"` — a real código is sufficient regardless of recent activity

#### Scenario: Sticker history without a current código falls to the review fallback
- GIVEN an inspector has `tiene_sticker=true` (some sticker, ever) but `tiene_sticker_valido=false`
  (none of them post-20-Aug), no código, and is absent from BOTH Fase 2 and Vercel
- WHEN classification runs
- THEN `estado_sugerido="revisar"` via the fallback branch, NOT `"candidato_desactivacion"` —
  having ever done field work takes the inspector out of the "clean deactivation candidate" bucket
  even without a Fase 2/Vercel match

#### Scenario: Classification never writes to the inspector record
- GIVEN any inspector is classified as `candidato_desactivacion`
- WHEN classification completes
- THEN no field on the live Firestore inspector document changes

### Requirement: Priority Heuristics Are Not Auto-Disqualifying
[SUPERSEDED — see extension delta]
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
[SUPERSEDED — see extension delta]
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
[SUPERSEDED — renamed and rewritten in the extension delta]
The system MUST use Firestore `inspector_profiles()`'s `identificacion` field as the sole identity
anchor for matching/grouping, and MUST NOT derive or substitute an identity key from an
email-derived cédula.

#### Scenario: Email-derived cédula never overrides identificacion
- GIVEN an inspector's `identificacion` differs from a cédula guessed from their email
- WHEN identity matching runs
- THEN grouping uses `identificacion`, never the email-derived value

### Requirement: Code Remap And Duplicate Unification
[SUPERSEDED — see extension delta]
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

---

# Extension 2026-09-19 — Delta (complete base)

Source: `explore-extension.md`. `cedula_key` = digits-only cédula. `main` = the `referencia` bundle's
base-CSV section. Blocks here REPLACE (MODIFIED/RENAMED) or APPEND TO (ADDED) the body above.

## RENAMED Requirements

### Requirement: Identity Anchor Is Firestore `identificacion` → Identity Universe Is Firestore Roster ∪ `referencia.main`, Keyed By Cédula

(Reason: the delivered engine served 129 of the notebook's 373 rows because `referencia.main` was an
overlay that never created a profile. The user's decision is that the backend serves the COMPLETE
depurado base, so `main` becomes a universe source and the anchor becomes the cédula key, not the
Firestore document field. The prohibition on email-derived cédulas is unchanged.)
(Migration: tests referring to "identity anchor"/`identificacion`-keyed dicts must key by
`cedula_key`; `design.md`'s D1 — emit an index, not counts — is NOT affected by this rename.)

## MODIFIED Requirements

### Requirement: Identity Universe Is Firestore Roster ∪ `referencia.main`, Keyed By Cédula

The system MUST build one profile per distinct `cedula_key` drawn from the union of the Firestore
roster (`inspector_profiles()`) and `referencia.main`. Firestore is processed first and wins on
conflict; a `main` row whose `cedula_key` already has a Firestore profile MUST NOT create a second
profile and MUST only backfill fields the Firestore profile leaves empty. The system MUST NOT derive
or substitute an identity key from an email-derived cédula. Vercel and Fase 2 remain overlays and
MUST NOT create profiles.
(Previously: Firestore `identificacion` was the sole anchor and `referencia.main` never created a
profile.)

#### Scenario: Email-derived cédula never overrides the roster identification
- GIVEN an inspector's `identificacion` differs from a cédula guessed from their email
- WHEN identity matching runs
- THEN grouping uses the `identificacion`-derived `cedula_key`, never the email-derived value

#### Scenario: main row without a Firestore counterpart becomes a profile
- GIVEN a `main` row with `cedula_key="12345678"` and no roster entry for that key
- WHEN the universe is built
- THEN a profile keyed `"12345678"` exists, sourced from `main`

#### Scenario: Firestore-first, main backfills only empty fields
- GIVEN a roster profile with `cedula_key="12345678"` and an empty `tarjeta_profesional`, and a
  `main` row with the same key carrying a `tarjeta_profesional` and a different `nombre`
- WHEN the universe is built
- THEN exactly one profile exists, its `nombre` stays the Firestore one and its
  `tarjeta_profesional` is backfilled from `main`

#### Scenario: Duplicate cédula across two main rows is first-wins, never a silent overwrite
- GIVEN two `main` rows share `cedula_key="12345678"` with different names
- WHEN the universe is built
- THEN the first row wins, the second does not overwrite any field
- AND a `revision_manual` entry with `motivo="cedula_duplicada_main"` names both records

#### Scenario: main row without a usable cédula is not dropped silently
- GIVEN a `main` row whose cédula is empty or has no digits
- WHEN the universe is built
- THEN the row does not create a profile and does not raise
- AND a `revision_manual` entry with `motivo="main_sin_cedula"` records it

#### Scenario: Empty normalized name does not block a profile
- GIVEN a `main` row with a valid `cedula_key` and an empty `nombre`
- WHEN the universe is built
- THEN the profile is created and survives every later stage; the empty name never matches another
  empty name for unification

### Requirement: Code Remap And Duplicate Unification

The system MUST remap `codigo` against the Vercel roster by cédula match, falling back to
`rapidfuzz.fuzz.token_sort_ratio(nombre_norm) >= 90` only to SURFACE candidates, per:

| Case | Rule |
|---|---|
| D-P1: Vercel roster has 2+ entries sharing a code | Exclude that code from remap; list for manual review |
| D-P2: Same person, multiple cédulas | Unify only on exact `nombre_norm` match; report discrepancies |
| D1: Sticker code changes owner | Attribute to the CURRENT titular; no ownership history kept |
| D2: Merge outcome for a duplicate | Exclude/mark the duplicate in the computed view; NEVER hard-delete |
| D3: Fuzzy name match 85-99% | Never auto-merge; always route to manual review |
| D5: Fase 2 row without NP | `np=""`, `np_fuente="ninguno"`; MUST NOT invent `pasos` |

When Vercel assigns a `codigo` to cédula A and a DIFFERENT profile currently holds that `codigo`,
the system MUST clear the `codigo` from the wrong holder before assigning it. If cédula A has no
profile in the universe, the código MUST be cleared from the wrong holder anyway and reported as
`motivo="remap_sin_duenio"`; if two Vercel-registered owners contend for one código on the same
profile, the system MUST leave the `codigo` empty and report `motivo="remap_conflicto"`.

D-P2 unification MUST run AFTER the Vercel/Fase 2 overlays, the Fase 2 cédula fix, sticker
attribution and the remap, so the survivor score
`n_stickers>0 > has_codigo > en_vercel > en_fase2 > not no_persona > not cedula_sospechosa` is
evaluated on populated flags. Ties MUST break on the oldest `creado_en`, then on Firestore-backed
before main-only. The survivor MUST absorb the losers' sticker aggregates and MUST backfill each
empty field with the first non-empty loser value. Every unified-away `cedula_key` MUST be registered
so later lookups resolve to the survivor.
(Previously: unification ran before overlays and stickers, so the survivor score was degenerate; the
remap never cleared the código from a wrong holder and had no `remap_sin_duenio`/`remap_conflicto`.)

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

#### Scenario: Remap clears the código from the wrong current holder
- GIVEN Vercel assigns `codigo="041"` to cédula A, and profile B currently holds `codigo="041"`
- WHEN remap runs
- THEN profile B's `codigo` becomes `""` and profile A's becomes `"041"`

#### Scenario: Remapped código with no owner in the universe
- GIVEN Vercel assigns `codigo="041"` to a cédula with no profile, and profile B holds `"041"`
- WHEN remap runs
- THEN profile B's `codigo` becomes `""`
- AND `revision_manual` contains `{"codigo":"041","motivo":"remap_sin_duenio"}`

#### Scenario: Contending owners leave the código empty
- GIVEN two Vercel-registered cédulas both resolve to the same profile for `codigo="041"`
- WHEN remap runs
- THEN that profile's `codigo` is `""`
- AND `revision_manual` contains `{"codigo":"041","motivo":"remap_conflicto"}`

#### Scenario: D-P2 pair carrying different cédulas across sources is unified after overlays
- GIVEN one profile keyed by the Firestore cédula and another keyed by the `main` cédula share an
  exact `nombre_norm`, and only the first is in Vercel while only the second has stickers
- WHEN unification runs after the overlays and sticker attribution
- THEN the sticker-bearing profile survives (`n_stickers>0` outranks `en_vercel`), inherits
  `en_vercel=true` and the Vercel `np`/`codigo`, and the loser's `cedula_key` resolves to it

#### Scenario: Unification never merges two empty names
- GIVEN two profiles both have an empty `nombre_norm`
- WHEN unification runs
- THEN they remain separate profiles

### Requirement: Priority Heuristics Are Not Auto-Disqualifying

`cedula_sospechosa` (digit-only cédula length outside 6-10, or exactly 10 digits not starting with
`"1"`) and `es_cuenta_no_persona` (email/name pattern match) MUST be used only to prioritize manual
review or trigger non-person aggregation, and MUST NOT by themselves force any `estado_sugerido`
outside the dedicated `"no_persona"` branch.

When Fase 2 supplies a corrected cédula for a profile matched by exact `nombre_norm` only (no cédula
match), the system MUST adopt the Fase 2 cédula as the profile's `cedula_key` but MUST keep
`cedula_sospechosa` as computed from the ORIGINAL `main` cédula, and MUST keep the original key
resolvable so previously attributed stickers are not lost.
(Previously: the heuristics requirement said nothing about the Fase 2 cédula fix, and recomputing
`cedula_sospechosa` after the fix silently cleared the flag.)

#### Scenario: Suspicious cédula alone does not force non-person classification
- GIVEN `cedula_sospechosa=true` but `es_cuenta_no_persona=false`
- WHEN classification runs
- THEN `estado_sugerido` is not forced to `"no_persona"` by the cédula check alone

#### Scenario: Fase 2 cédula fix keeps the suspicion flag from the original cédula
- GIVEN a `main` profile with cédula `"999"` (`cedula_sospechosa=true`) matched to a Fase 2 row by
  exact name, where Fase 2 carries cédula `"1053812345"`
- WHEN the cédula fix runs
- THEN `cedula_key="1053812345"`, `identificacion="1053812345"`, and `cedula_sospechosa` is still
  `true`

#### Scenario: Stickers attributed before the fix survive it
- GIVEN the same profile had stickers attributed under `"999"`
- WHEN the cédula fix runs
- THEN those sticker aggregates still belong to the profile and `ultimo_sticker` is unchanged

### Requirement: Non-Person Account Aggregation With Drill-Down

The system MUST collapse into one aggregated row every profile matching
`(cedula_sospechosa OR es_cuenta_no_persona) AND (no ultimo_sticker OR dias_inactivo > 7) AND codigo
is empty`, minus the already-specified `survey_cali` exempt set. Collapsed profiles MUST leave the
primary inspector list. The aggregate row MUST be `identidad_key="GRUPO-EXTERNOS"` with
`np_fuente="ninguno"`, `fase="Fase I"`, `fase_np_faltante=true`, `activo=false`, an `n_colapsados`
count, and per-profile `detalle`. The system MUST expose both the counts AND the underlying detail
on demand — never only the total.
(Previously: the trigger was `non-person-flagged` only; `cedula_sospechosa` was not an entry
condition, and `n_colapsados` / the aggregate row's `np_fuente`/`fase`/`activo` shape was unstated.)

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

#### Scenario: Suspicious cédula alone is enough to collapse when inactive and code-less
- GIVEN a profile with `cedula_sospechosa=true`, `es_cuenta_no_persona=false`, no `ultimo_sticker`
  and no `codigo`
- WHEN aggregation runs
- THEN it is collapsed into GRUPO-EXTERNOS

#### Scenario: Aggregate row carries the parity shape
- GIVEN 252 profiles are collapsed over the reference snapshot
- WHEN the aggregate row is emitted
- THEN `n_colapsados=252`, `np_fuente="ninguno"`, `fase="Fase I"`, `fase_np_faltante=true`,
  `activo=false`

#### Scenario: dias_inactivo boundary is strict
- GIVEN an otherwise-qualifying profile with `dias_inactivo == 7`
- WHEN aggregation runs
- THEN it is NOT collapsed; at `8` it is

## ADDED Requirements

### Requirement: Sticker Attribution Is Cédula-Keyed Through An Alias Index

The system MUST attribute sticker aggregates to profiles by resolving the sticker's professional
cédula through a cédula-key index, NOT by exact-string lookup of the Firestore `identificacion`.
The index MUST contain each profile's current `cedula_key`, every cédula it carried before a Fase 2
fix, and every `cedula_key` unified away into it. An unresolvable cédula MUST leave every profile
untouched rather than raise.

#### Scenario: Sticker resolves through a unified-away cédula
- GIVEN profile S absorbed profile L (`cedula_key="777"`) during unification
- WHEN a sticker whose professional cédula is `"777"` is attributed
- THEN it counts toward S's `n_stickers` and can set S's `ultimo_sticker`

#### Scenario: Sticker for an unknown cédula is ignored, not fatal
- GIVEN a sticker whose professional cédula matches no index entry
- WHEN attribution runs
- THEN no profile changes and no exception is raised

#### Scenario: Exact-string identificacion lookup is not the path
- GIVEN a profile whose `identificacion` is `"0012345"` and a sticker cédula of `"12345"` that
  normalizes to the same `cedula_key`
- WHEN attribution runs
- THEN the sticker is attributed to that profile

### Requirement: Entidad Precedence Is Vercel > Firestore > main, By Cédula Only

The system MUST resolve `entidad` as the first non-empty of: the Vercel entry matched BY CÉDULA, the
Firestore roster value, the `main` value. A Vercel entry matched only by `nombre_norm` MUST NOT
contribute `entidad`. When no source supplies one, `entidad` MUST be `""` — never invented.

#### Scenario: Vercel wins over Firestore on a cédula match
- GIVEN Vercel matched by cédula has `entidad="A"` and Firestore has `"B"`
- WHEN `entidad` is resolved
- THEN `entidad="A"`

#### Scenario: Name-only Vercel match does not supply entidad
- GIVEN the Vercel overlay matched only by exact name and carries `entidad="A"`, while Firestore is
  empty and `main` has `"C"`
- WHEN `entidad` is resolved
- THEN `entidad="C"`

#### Scenario: No source yields an empty entidad
- GIVEN no source carries an `entidad` for the profile
- WHEN `entidad` is resolved
- THEN `entidad=""`

### Requirement: Degraded Sticker Snapshot Disables Depuración

When the sticker snapshot served to the depuration engine comes from the redacted last-known-good
Blob restore (`degraded`), the system MUST NOT emit a computed table. It MUST return
`depuracion.activa=false` with `motivo="stickers_degradados"` and an empty `inspectores` list.
Rationale: without attributable stickers every code-less profile in the full universe would be
classified `candidato_desactivacion` and every suspicious profile would be wrongly collapsed.
This is distinct from an unavailable REFERENCE bundle, which still degrades to a computed table per
"Missing Or Corrupt Bundle (Or Section) Degrades Instead Of Failing".

#### Scenario: Degraded LKG restore does not produce a table
- GIVEN the response is served from the redacted Blob last-known-good copy
- WHEN `depuracion` is assembled
- THEN `activa=false`, `motivo="stickers_degradados"`, `inspectores` is empty, and the response is 200

#### Scenario: Reference missing but stickers live still produces a table
- GIVEN the reference bundle is unreadable but the sticker snapshot is live
- WHEN `depuracion` is assembled
- THEN a table is still computed, with `np_fuente` degraded to `"main"`/`"ninguno"`

### Requirement: Contact Fields Are Admin-Only And Never Logged In Clear

`nombre_completo`, `identificacion`, `num_telefono`, `correo_contacto` and `tarjeta_profesional` MUST
be added to the response only when the authenticated role is admin, and MUST NOT appear in the
Blob-persisted copy of the payload. Any INFO-level log of `alias_nombres` or of identity keys MUST be
redacted or downgraded to DEBUG, because `identidad_key` is now a cédula.

#### Scenario: Viewer role gets no depuración block
- GIVEN an authenticated non-admin (viewer) request
- WHEN the response is assembled
- THEN no `depuracion` key is present

#### Scenario: Admin role gets the contact fields
- GIVEN an authenticated admin request with the flag on and live stickers
- WHEN the response is assembled
- THEN `depuracion.inspectores` entries carry `num_telefono`, `correo_contacto` and
  `tarjeta_profesional` where the source is non-empty

#### Scenario: alias_nombres is not logged in clear at INFO
- GIVEN the engine builds `alias_nombres`
- WHEN it logs at INFO level
- THEN neither a name nor a cédula appears in the record
