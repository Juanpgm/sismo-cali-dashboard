# Inspection Photo Capture Specification

## Purpose

Let inspectors attach up to 10 photos per record from gallery or camera through a simple dynamic UI, and upload them resiliently and quickly on submit.

## Requirements

### Requirement: Up To 10 Dynamic Photo Slots

The system MUST support up to 10 photo slots generated dynamically; the slot count MUST NOT be hardcoded to a fixed value below 10 in client code.

#### Scenario: Adding a slot beyond the old fixed limit

- GIVEN 3 photos are already attached
- WHEN the inspector adds a 4th photo
- THEN a 4th slot renders and accepts input

#### Scenario: Hard cap at 10

- GIVEN 10 photos are attached
- WHEN the inspector attempts to add an 11th
- THEN the UI prevents it and shows a Spanish message that 10 is the maximum

### Requirement: Gallery And Camera Sourcing

The system MUST offer an explicit gallery/multi-select entry point ("Agregar fotos") distinct from an explicit camera entry point ("Tomar foto"). Neither input MUST force camera-only capture via an unqualified `capture` attribute.

#### Scenario: Gallery multi-select

- GIVEN the inspector taps "Agregar fotos"
- WHEN the device file picker opens
- THEN multiple existing images can be selected in one action, up to the remaining slot capacity

#### Scenario: Camera capture

- GIVEN the inspector taps "Tomar foto"
- WHEN the camera opens and a photo is captured
- THEN the photo fills the next available slot

### Requirement: Parallel Upload With Concurrency Cap

On submit, the system MUST upload photos to the external signer in parallel with a concurrency cap of 3 simultaneous uploads, and MUST reuse the existing per-photo upload cache to avoid re-uploading photos that already succeeded in a prior failed submit attempt.

#### Scenario: Concurrency respected

- GIVEN 9 photos are attached
- WHEN submit triggers the upload
- THEN at most 3 uploads are in flight at any point until all complete

#### Scenario: Cache avoids re-upload on retry

- GIVEN a prior failed submit already uploaded photo N (cached by `codigo:slot:name:size:lastModified`)
- WHEN the inspector retries submit
- THEN photo N is not re-uploaded

### Requirement: Slot-Generic Design With Capped Fallback

The client design MUST treat `slot` as a generic 1-10 index with no assumption baked in below 10. IF the external signer rejects `slot` values greater than 3 (verified by an apply-time probe), THEN the visible slot cap MUST fall back to 3 without further client redesign.

#### Scenario: Fallback when signer rejects high slots

- GIVEN the apply-phase probe finds the signer rejects `slot=4`
- WHEN the client ships with the cap configured at 3
- THEN inspectors see only 3 slots and the rest of the change (code assignment, session resilience) ships unaffected

## Testability Notes

- Slot-count/cap logic (e.g., `canAddSlot(current, max)`) MUST have `node --test` coverage.
- Multi-select/camera affordances, concurrency cap, and cache-hit-on-retry MUST have Playwright e2e coverage (mocked signer routes counting in-flight requests).
