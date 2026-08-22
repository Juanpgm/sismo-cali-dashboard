# Sticker Code Assignment Specification

## Purpose

Derive the sticker code's consecutive segment from real submitted records (not an independently-incrementing counter), and let inspectors correct that segment while keeping the code duplicate-free.

## Requirements

### Requirement: Records-Derived Next Consecutive

The system MUST derive the next consecutive number as `max(consecutive parsed from this inspector's evaluaciones codes) + 1`, tolerating gaps in the sequence.

#### Scenario: Contiguous records

- GIVEN the inspector's evaluaciones have consecutives {1, 2, 3}
- WHEN a new code is generated
- THEN the next consecutive is 4

#### Scenario: Records with a gap

- GIVEN the inspector's evaluaciones have consecutives {1, 3}
- WHEN a new code is generated
- THEN the next consecutive is 4, not 3 (max-based, avoids the collision a count-based derivation would produce)

#### Scenario: No prior records

- GIVEN the inspector has zero evaluaciones
- WHEN a new code is generated
- THEN the next consecutive is 1

### Requirement: Session-Scoped Derivation Cache

The system MUST run the records-derived query at most once per session, cache the result in memory, and increment the cached value locally for subsequent generations. The system MUST re-run the query after a duplicate collision.

#### Scenario: Cached increment without re-query

- GIVEN a cached next-consecutive value of 5 already exists in the session
- WHEN the inspector generates another code without a collision
- THEN the next value is 6 and no new Firestore query is issued

#### Scenario: Collision triggers re-derivation

- GIVEN a submit fails because the generated code already exists
- WHEN the collision is detected
- THEN the system re-runs the records query and retries with a corrected next value

### Requirement: Numeric Consecutivo Persisted On New Docs

New `evaluaciones` documents MUST persist a numeric `consecutivo` field matching the code's 4-digit segment, without changing the existing `create` rule constraints.

#### Scenario: New record stores numeric field

- GIVEN a new evaluacion is submitted with code segment `0007`
- WHEN the document is written
- THEN it includes a numeric `consecutivo` field equal to `7`

### Requirement: Editable Last-4-Digits Segment

The system MUST let the inspector edit exactly the 4-digit consecutive segment of the generated code via an input field; the municipio/area/inspector segments MUST remain fixed and non-editable.

#### Scenario: Valid edit updates the code

- GIVEN a generated code with consecutive segment `0001`
- WHEN the inspector edits the segment to `0005`
- THEN the code's prefix is unchanged and the segment becomes `0005`

#### Scenario: Non-4-digit input is rejected

- GIVEN the inspector types `12` into the segment field
- WHEN the field loses focus or submit is attempted
- THEN a Spanish inline validation message appears and submit is blocked

#### Scenario: Below-next edit is permitted with a hint

- GIVEN the derived next consecutive is `0006`
- WHEN the inspector edits the segment to `0002`
- THEN the edit is accepted and a non-blocking Spanish hint is shown (gap-filling corrections are a legitimate use case, not an error)

### Requirement: Fail-Closed Duplicate Protection

The system MUST perform a pre-submit existence check on the edited code and MUST use a create-only transaction as the authoritative, fail-closed backstop against duplicates.

#### Scenario: Pre-check catches an existing code

- GIVEN the edited code already exists in `evaluaciones`
- WHEN the inspector attempts to submit
- THEN a Spanish duplicate-code error is shown and submit is blocked before the create attempt

#### Scenario: Concurrent generation on two devices

- GIVEN two devices independently generate the same code
- WHEN both attempt the create-only transaction
- THEN exactly one document is created and the other transaction fails closed with a Spanish duplicate error

## Testability Notes

- Pure functions (`parseConsecutivo`, `siguienteConsecutivo`, `validarSegmento`, `buildCodigo`) MUST have `node --test` coverage in `formulario/test/logic.test.mjs`.
- Session cache, collision retry, and the create-only transaction path MUST have Playwright e2e coverage against an extended `firebase-mock.js` (query support).
