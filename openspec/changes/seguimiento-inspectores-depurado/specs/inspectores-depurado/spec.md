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
| D-P1: Vercel roster has 2+ entries sharing a code, OR a person duplicated by `nombre_norm` in the Vercel roster (every one of their codes; the publisher lists them in `codigos_duplicados`) | Exclude that code from remap; list for manual review |
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
- AND it is reported, never silently lost: rows with no digits at all are counted by the
  publisher (`descartados_sin_cedula`, printed and stored as an optional top-level bundle key; the
  parser drops blank `cedula_key` rows), and the engine records a `revision_manual` entry with
  `motivo="main_sin_cedula"` when such a row arrives as a non-empty, non-digit string (or is built
  directly, e.g. in tests)

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
| D-P1: Vercel roster has 2+ entries sharing a code, OR a person duplicated by `nombre_norm` in the Vercel roster (every one of their codes; the publisher lists them in `codigos_duplicados`) | Exclude that code from remap; list for manual review |
| D-P2: Same person, multiple cédulas | Unify only on exact `nombre_norm` match; report discrepancies |
| D1: Sticker code changes owner | Attribute to the CURRENT titular; no ownership history kept |
| D2: Merge outcome for a duplicate | Exclude/mark the duplicate in the computed view; NEVER hard-delete |
| D3: Fuzzy name match 85-99% | Never auto-merge; always route to manual review |
| D5: Fase 2 row without NP | `np=""`, `np_fuente="ninguno"`; MUST NOT invent `pasos` |

When Vercel assigns a `codigo` to cédula A and a DIFFERENT PERSON currently holds that `codigo`,
the system MUST clear the `codigo` from that wrong holder before assigning it. A holder is the SAME
person as the Vercel owner (and is therefore never a wrong holder) when the Vercel cédula key equals
its own cédula key, the cédula key it had before the Fase 2 cédula fix, or its matched Fase 2
cédula, or when `token_sort_ratio` of the normalized names is `>= 90` (inclusive); an empty
cédula or an empty name never matches. A same-person holder keeps its `codigo` and nothing is
assigned, cleared for it, or reported for that pair. If cédula A has no profile in the universe,
the código MUST be cleared from every wrong holder anyway and reported, one item per cleared holder,
as `motivo="remap_sin_duenio"` (with the holder's `identidad_key`). If ONE profile is claimed by two
DIFFERENT Vercel códigos (a person registered twice in Vercel), the system MUST leave its `codigo`
empty and report each código as `motivo="remap_conflicto"`; two Vercel entries sharing the SAME código
are a D-P1 duplicate (`codigo_vercel_duplicado`), never a conflict. The owner of a Vercel cédula is
resolved through the profile's own cédula, else its matched Fase 2 cédula; a Fase 2 cédula shared by
2+ profiles is ambiguous: nothing is assigned or cleared and `motivo="remap_owner_ambiguo"` lists the
`identidad_keys` and, in `identidad_keys_titulares`, the current holders of the código (who keep it:
there is no evidence to clear them). When 2+ current holders of a código are ALL the same person as the Vercel owner,
exactly ONE MUST keep it (the cédula owner by own or pre-fix cédula, else the resolved owner, else
the highest `token_sort_ratio`, else the lowest `identidad_key`) and each other one MUST be cleared and
reported as `motivo="remap_mismos_titulares"` with `codigo`, `identidad_key` (the cleared holder) and
`identidad_key_conservado` (the keeper), BUT only when the keeper surely ends up holding the código:
a keeper that its own `remap_conflicto` empties (or that another claim re-assigns) MUST NOT cause the
other same-person holders to be cleared; they keep the código and the conflict item names it. When the
owner of a Vercel cédula held a DIFFERENT código and
NOBODY held Vercel's, the código is still assigned (Vercel is the golden rule) and the replacement
MUST be reported as `motivo="codigo_reemplazado"` with `identidad_key`, `codigo_anterior` and
`codigo_nuevo`; a código that had a holder (a swap or a rotation) reports nothing.

Review-item invariants (judgment-day round 3, property-tested): (INV-1) after `depurar`, every
non-empty `codigo` held by 2+ output rows MUST be named by a `revision_manual` item whose `codigo`
equals it (`codigo_vercel_duplicado`, `remap_owner_ambiguo`, `remap_conflicto`,
`remap_mismos_titulares` or `codigo_duplicado_local`); clearing a código needs Vercel evidence (D13),
so a código Vercel never mentions is kept and reported as
`{"motivo":"codigo_duplicado_local","codigo":...,"identidad_keys":[...]}`. A código pre-listed in
`referencia.codigos_duplicados` is excluded from the remap but MUST be reported as
`codigo_vercel_duplicado` (with `identidad_keys_titulares`) whenever 2+ profiles hold it. (INV-2)
every profile key a review item names MUST be a final `inspectores` key or a `grupo_externos.detalle`
member: a key the D-P2 unification absorbed is reported as its survivor. When the survivor already
holds a DIFFERENT código, the absorbed profile's código cannot be backfilled and MUST be reported as
`{"motivo":"codigo_perdido_unificacion","codigo":...,"identidad_key":<survivor>,"identidad_key_absorbido":<key>}`.
(INV-5) a keeper is never left empty by the pass that cleared the other holders. (INV-3/INV-4) the output
does not depend on the roster/sticker/survey order and is idempotent. (INV-6) `activo` requires a
non-empty `codigo`. A Vercel `codigo` is `_txt`-trimmed and a whitespace-only one is no código
(D-CODIGOTRIM).

D-P2 unification MUST run AFTER the Vercel/Fase 2 overlays, the Fase 2 cédula fix, sticker
attribution and the remap, so the survivor score
`n_stickers>0 > has_codigo > en_vercel > en_fase2 > not no_persona > not cedula_sospechosa` is
evaluated on populated flags. Ties MUST break on the oldest `creado_en`, then on Firestore-backed
before main-only. The survivor MUST absorb the losers' sticker aggregates and MUST backfill each
empty field with the first non-empty loser value. Every unified-away `cedula_key` MUST be registered
so later lookups resolve to the survivor. The unification MUST NOT change the survivor's own flags
(`es_cuenta_no_persona`, `cedula_sospechosa`; D-SURVFLAGS, notebook parity): they stay what the survivor's
own cédula / correo / nombre / bundle flag gave BEFORE the merge, so a real person merged with a non-person
duplicate stays a person (and keeps its `estado_sugerido`), a non-person survivor is not cured by a real
duplicate, and a correo backfilled from a loser never flips the flag (INV-7).
(Previously: unification ran before overlays and stickers, so the survivor score was degenerate; the
remap never cleared the código from a wrong holder and had no `remap_sin_duenio`/`remap_conflicto`;
a holder who was the same person as the Vercel owner was also cleared.)

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

#### Scenario: One profile claimed by two different Vercel códigos is left empty
- GIVEN two Vercel entries whose cédulas resolve to the same profile, with `codigo="041"` and `codigo="052"`
- WHEN remap runs
- THEN that profile's `codigo` is `""`
- AND `revision_manual` contains `{"codigo":"041","motivo":"remap_conflicto","identidad_key":<profile>}` and the same for `"052"`

#### Scenario: Two Vercel entries with the same código are a duplicate, not a conflict
- GIVEN two Vercel entries share `codigo="041"` and their cédulas resolve to the same profile
- WHEN remap runs
- THEN `revision_manual` contains `{"codigo":"041","motivo":"codigo_vercel_duplicado"}` and no `remap_conflicto`

#### Scenario: A holder who is the same person is never a wrong holder
- GIVEN Vercel assigns `codigo="041"` to cédula `1099999999` named "Juan Perez Gomez" and the roster profile with cédula `1234567` and the same name holds `"041"`
- WHEN remap runs
- THEN that profile keeps `codigo="041"` and `estado_sugerido="activo"`
- AND `revision_manual` is empty

#### Scenario: Same-person matching is inclusive at 90 and never matches empty names
- GIVEN a holder whose name scores exactly 90.0 (resp. 88.9) against the Vercel name
- WHEN remap runs
- THEN the holder keeps the código at 90.0 and is cleared at 88.9
- AND an empty holder name or an empty Vercel name never counts as a match

#### Scenario: Two holders that are the same person keep exactly one código
- GIVEN Vercel assigns `codigo="041"` to a third cédula named "Ana Maria Gomez", and two roster profiles named "Ana Maria Gomez" (cédula A) and "Ana Maria Gomes" (cédula B, `token_sort_ratio` 93.3) both hold `"041"`
- WHEN remap runs
- THEN exactly one of them keeps `"041"` (the cédula owner if one of them is, else the highest score, else the lowest `identidad_key`) and the other's `codigo` becomes `""`
- AND `revision_manual` contains `{"motivo":"remap_mismos_titulares","codigo":"041","identidad_key":<cleared>,"identidad_key_conservado":<keeper>}` once per cleared holder
- AND the result is identical for any roster order; a lone same-person holder reports nothing; "021" and "21" are different códigos

#### Scenario: A keeper emptied by its own conflict does not get its twins cleared
- GIVEN cédula A (roster, "Maria Fernanda Lopez", `codigo="021"`) and a `main`-only profile B with the same name and `codigo="021"`, and Vercel registers A twice (`021` and `052`)
- WHEN remap runs
- THEN A's `codigo` is `""` (`remap_conflicto` for 021 and 052) and B keeps `"021"`; no `remap_mismos_titulares` item is emitted
- AND after the unification the single row holds `"021"`

#### Scenario: A código lost in the unification is reported
- GIVEN the same-person keeper of `"099"` (cédula 999) is unified into a profile that already holds `"052"`
- WHEN `depurar` runs
- THEN `revision_manual` contains `codigo_perdido_unificacion` with `codigo="099"`, `identidad_key=<survivor>` and `identidad_key_absorbido="999"`, and the `remap_mismos_titulares` item names the survivor as `identidad_key_conservado`
- AND a survivor with no código inherits the absorbed one and reports nothing

#### Scenario: A código held twice that Vercel never mentions is reported, not cleared
- GIVEN two different people hold `codigo="041"` and Vercel has no entry for it
- WHEN `depurar` runs
- THEN both keep `"041"` and `revision_manual` contains `{"motivo":"codigo_duplicado_local","codigo":"041","identidad_keys":[<both>]}`
- AND "021" vs "21", blank/whitespace códigos and a single holder report nothing

#### Scenario: A pre-listed duplicate código held by two profiles is reported
- GIVEN `codigos_duplicados` lists `"041"` and two profiles hold it
- WHEN `depurar` runs
- THEN nobody is cleared and `revision_manual` contains `codigo_vercel_duplicado` for `"041"` with `identidad_keys_titulares` listing both; held by one profile it reports nothing

#### Scenario: A Vercel código nobody held replaces the owner's different código and is reported
- GIVEN the owner of cédula A holds `codigo="052"` and Vercel assigns `codigo="041"` to A, and nobody holds `"041"`
- WHEN remap runs
- THEN A's `codigo` is `"041"`
- AND `revision_manual` contains `{"motivo":"codigo_reemplazado","identidad_key":<A>,"codigo_anterior":"052","codigo_nuevo":"041"}`
- AND a swap or rotation of códigos between profiles that all hold one reports no `codigo_reemplazado`

#### Scenario: A Fase 2 cédula shared by two profiles is an ambiguous owner
- GIVEN two profiles matched the same Fase 2 cédula (their fix was blocked), the Vercel owner cédula is that one, and a third profile holds the código
- WHEN remap runs
- THEN the código is not reassigned and the holder is not cleared
- AND `revision_manual` contains a `remap_owner_ambiguo` item listing both `identidad_keys`

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
resolvable so previously attributed stickers are not lost. After the rewrite the system MUST resolve
`entidad`, `en_vercel`, `fuente_dato` and the Vercel `np` for that profile by its CORRECTED cédula
(exact key match only, never by name); when the fix is blocked by a collision, nothing is re-resolved.
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

#### Scenario: The corrected cédula finds its Vercel row
- GIVEN the profile above and a Vercel row with cédula `"1053812345"`, `codigo="041"`, `entidad="DAGRD"`
- WHEN the pipeline runs
- THEN the row `"1053812345"` has `entidad="DAGRD"`, `fuente_dato` containing `vercel` and `codigo="041"`

#### Scenario: The corrected cédula's Vercel row never wipes a pre-fix Vercel np
- GIVEN the profile above matched a Vercel row by NAME before the fix (`np="P7"`) and the Vercel row for the corrected cédula has an empty (or a different) `np` and `entidad="DAGRD"`
- WHEN the pipeline runs
- THEN `np="P7"` from `vercel` is kept (the `np_vercel` is only filled when empty) and `entidad="DAGRD"` is resolved from the corrected cédula

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
- AND the sticker's own form (`"12345"`) is exported in that profile's `cedulas_unificadas`, so the
  frontend (whose `cedulaKey` keeps leading zeros) routes it to the same row
- AND an exact `cedula_key` match always wins; a zero-stripped match claimed by two different
  profiles is ambiguous and attributes nothing

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

---

# Efficiency Extension 2026-09-19 — Delta (incremental, quota-safe base)

Source: `efficiency-extension.md`, `design.md` D21-D32. The engine stays a pure function of
`(stickers, roster_by_cedula, nombres_survey, referencia, hoy)`; the router owns the input
fingerprints. Blocks here APPEND (ADDED) or REPLACE (MODIFIED) the body above. `referencia` row order
is part of the INPUT (its `huella` is order-sensitive); the roster order and the sticker order are not.

## ADDED Requirements

### Requirement: Engine Output Is Independent Of Roster And Sticker Order

For the same set of inputs, `depurar()` MUST return a structurally identical result regardless of the
iteration order of the roster and of the stickers. Where two candidates tie completely, the survivor
tie-break chain MUST be: the survivor score, then the oldest parsed `creado_en` (a blank or
unparseable date never beats a real one), then the Firestore-backed profile, then the ascending
`identidad_key` as the terminal total order. Every choice that depends on iteration order (alias
collisions, fuzzy-remap candidates, unification groups) MUST iterate in canonical sorted order, and
every emitted list MUST be canonical: `inspectores` by `identidad_key`, `revision_manual` by
`(motivo, codigo, identidad_key)`, `grupo_externos.detalle` by `identificacion`.

#### Scenario: Shuffled roster and sticker order yields an identical result
- GIVEN the full-universe fixture and at least 25 different permutations of the roster dict order and
  of the sticker list order
- WHEN `depurar()` runs on each permutation
- THEN every result equals the first (inspectores, grupo_externos, alias_nombres, revision_manual,
  cedulas_unificadas)

#### Scenario: Terminal tie-break is the identidad_key when creado_en is blank
- GIVEN two same-name profiles with an equal survivor score, a blank `creado_en` on both and both
  Firestore-backed
- WHEN they are unified, in either input order
- THEN the profile with the lower `identidad_key` survives both times

#### Scenario: Firestore-backed still outranks the identidad_key
- GIVEN two same-name profiles with an equal score and equal or blank `creado_en`, exactly one of them
  Firestore-backed and that one having the HIGHER `identidad_key`
- WHEN they are unified
- THEN the Firestore-backed profile survives (design D10 is unchanged; the key only breaks a full tie)

#### Scenario: A blank creado_en never beats a real date
- GIVEN two otherwise tied profiles where one has a real `creado_en` and the other a blank value
- WHEN they are unified
- THEN the one with the real date survives regardless of `identidad_key` or order

#### Scenario: alias_nombres collisions resolve the same way in any order
- GIVEN two different survey names that normalize to the same key
- WHEN `alias_nombres` runs over any permutation of the inputs
- THEN the same name wins every time and the conflict is recorded once

#### Scenario: Empty inputs are deterministic
- GIVEN an empty roster, no stickers, no survey names and an empty referencia
- WHEN `depurar()` runs repeatedly
- THEN every call returns the same empty result and none raises

#### Scenario: Bundle order, unlike roster or sticker order, is part of the input
- GIVEN two `main` rows share a `cedula_key` (design D19: first wins)
- WHEN the bundle rows are swapped
- THEN the other row may win — the winner is defined by bundle order — while permuting the roster or
  the stickers never changes it

### Requirement: Engine Is Pure

`depurar()` MUST NOT mutate any of its inputs, MUST NOT return objects aliased to its inputs, and MUST
NOT read the clock, the process environment or mutable module-level state; `hoy` is the only date
source. Two interleaved calls with different inputs MUST NOT influence each other.

#### Scenario: Inputs are not mutated
- GIVEN deep copies of the stickers, the roster dicts and the survey names taken before the call
- WHEN `depurar()` returns
- THEN every input still equals its copy and the frozen `ReferenciaBundle` is untouched

#### Scenario: Survey names may be a one-shot iterable
- GIVEN `nombres_survey` supplied as a generator, and separately as a set
- WHEN `depurar()` runs
- THEN both produce the same result (the iterable is materialized once, never consumed twice)

#### Scenario: The result does not alias the inputs
- GIVEN a returned `Depuracion` whose lists and dicts are then mutated by the caller
- WHEN `depurar()` runs again on the same inputs
- THEN the second result equals the first pristine result and the inputs are unchanged

#### Scenario: No clock, environment or global reads
- GIVEN `date.today`, `datetime.now`, `time.time` and `os.environ` patched to raise
- WHEN `depurar()` runs with an explicit `hoy`
- THEN it completes, and two different `hoy` values change only the `dias_inactivo`-dependent fields
  and the collapse membership

#### Scenario: Interleaved calls do not leak state
- GIVEN two calls with different inputs run alternately
- WHEN each is compared with its own isolated run
- THEN both equal their isolated result and no module-level state changed

### Requirement: Engine Is Idempotent And Cheap Enough To Recompute Only On A Fingerprint Change

`depurar()` MUST return equal results for equal inputs, and MUST compute the reference universe
(373 profiles, 3,000 stickers) in under 0.5 seconds (best of at least 5 runs; the recorded baseline is
about 0.26 s), so that recomputing exactly once per changed input fingerprint is affordable and a
per-inspector delta recompute is unnecessary (design D30).

#### Scenario: Same inputs twice give equal results
- GIVEN identical inputs
- WHEN `depurar()` runs twice
- THEN the two results are equal

#### Scenario: Performance canary at the reference size
- GIVEN 373 profiles and 3,000 stickers
- WHEN `depurar()` runs (best of at least 5 runs)
- THEN it completes in under 0.5 s and a failure message prints the measured time

#### Scenario: Worst case with unattributable stickers stays within the same budget
- GIVEN 373 profiles and 3,000 stickers none of which resolves to a cédula
- WHEN `depurar()` runs
- THEN it still completes in under 0.5 s and raises nothing

## MODIFIED Requirements

### Requirement: Response Caching With TTL

The system MUST cache the computed table keyed by the fingerprints of ALL its inputs — the content
version of the sticker snapshot, the roster, the survey names, the reference bundle's content hash
(`huella`) and the Bogotá `hoy` — so that with unchanged inputs the table is never recomputed, however
often the TTLs of the individual sources expire. Each Firestore-derived input MUST own its TTL, a
content version and a single-flight guard (roster 30 min, evaluaciones Firestore side 15 min, survey
names 60 min, reference 30 min; the sticker walk 5 min). When N concurrent requests need the same
missing key, exactly ONE computation MUST run and every caller MUST receive its result. The system MUST
serve the last-good cached value if a live source is unavailable, rather than failing the request.
(Previously: "MUST cache the computed table for 5-10 minutes (`EVALUACIONES_CACHE_TTL_SECONDS` pattern)",
keyed by the identity of the sticker payload, which forced a recompute at every 5-minute refresh even
when the content was identical, and let concurrent requests each recompute.)

#### Scenario: Cache serves within TTL without recomputation
- GIVEN the table was computed less than the TTL ago
- WHEN a new request arrives
- THEN the cached value is returned without recomputing

#### Scenario: Unavailable reference source degrades to last-good cache
- GIVEN the reference Blob is temporarily unreachable
- WHEN a request arrives after the cache would normally expire
- THEN the system serves the last successfully computed value instead of an error

#### Scenario: Identical upstream past every TTL causes no recompute
- GIVEN every source's TTL has expired and each re-fetch returns identical content
- WHEN requests arrive
- THEN sources are re-read within their own budgets, yet `depurar()` is not called again

#### Scenario: Each changed fingerprint causes exactly one recompute
- GIVEN the previous result was computed
- WHEN, separately, the snapshot content, the roster content, the survey names, the reference `huella`,
  or the Bogotá `hoy` changes
- THEN each change causes exactly one recompute and the next request with the same key causes none

#### Scenario: Concurrent requests at expiry produce one computation
- GIVEN 20 concurrent requests for the same missing key released together
- WHEN they are served
- THEN exactly one `depurar()` ran, at most one survey scan and at most one roster scan, and all 20
  receive an equal result

#### Scenario: A failing computation does not deadlock or poison the cache
- GIVEN the computation for a key raises
- WHEN concurrent and subsequent requests arrive
- THEN waiters are released (they receive the last-good result when one exists), the in-flight marker
  is cleared, and the next request retries

#### Scenario: Midnight rollover recomputes once and nothing else
- GIVEN the Bogotá date changes with every other input identical
- WHEN requests arrive
- THEN exactly one recompute runs and no extra scan, download or Blob write occurs

#### Scenario: The roster is scanned once per compute window
- GIVEN a recompute that both classifies profiles and builds the response payload
- WHEN it runs
- THEN the roster is read once and shared, never twice
