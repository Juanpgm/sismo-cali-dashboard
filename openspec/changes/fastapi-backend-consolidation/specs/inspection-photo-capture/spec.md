# Delta for Inspection Photo Capture

Change: `fastapi-backend-consolidation` · Modified capability. Existing spec: `openspec/specs/inspection-photo-capture/spec.md`. This delta adds host/verifier requirements without changing any existing client-side requirement (slot count, gallery/camera sourcing, concurrency, fallback all unchanged).

## ADDED Requirements

### Requirement: Signer Endpoint Host Repoint

`/api/sign` MUST move from its standalone, non-git-connected Vercel deployment to the consolidated FastAPI app. `formulario/js/form.js`'s `FOTO_SIGNER_URL` MUST repoint to the consolidated app's base URL only after parity verification, per `backend-platform`'s cutover safety requirement.

#### Scenario: FOTO_SIGNER_URL repoint after parity verification

- GIVEN the consolidated `/api/sign` route has passed parity checks against the legacy signer
- WHEN `FOTO_SIGNER_URL` is updated
- THEN subsequent presign requests target the consolidated app, and the old signer project remains live until this slice is confirmed working

#### Scenario: Old signer stays live during transition

- GIVEN the `FOTO_SIGNER_URL` repoint has not yet shipped
- WHEN a field-form client calls `sismo-fotos-signer.vercel.app`
- THEN it continues to presign uploads exactly as before

### Requirement: Unified Token Verification For Signer

The signer's Firebase ID token check MUST move from its independent REST `accounts:lookup` verification to the shared RS256 verifier used by every other route, preserving identical accept/reject outcomes.

#### Scenario: Valid token still accepted under the unified verifier

- GIVEN a Firebase ID token the legacy `accounts:lookup` check would accept
- WHEN `/api/sign` verifies it with the shared RS256 verifier
- THEN the token is accepted and a presigned URL is returned

#### Scenario: Expired/invalid token still rejected

- GIVEN an expired or tampered Firebase ID token
- WHEN `/api/sign` verifies it with the shared RS256 verifier
- THEN the request is rejected, same outcome as the legacy check

### Requirement: Presign Acceptance Semantics Unchanged

S3 presign behavior (bucket/region selection, `PutObject`-only presigned URLs, required fields) MUST remain identical to the legacy signer; the host move MUST NOT introduce any new validation rule or required field.

#### Scenario: Same presign request shape accepted

- GIVEN a presign request payload the legacy signer accepted
- WHEN the consolidated `/api/sign` route receives the same payload
- THEN it returns an equivalent presigned `PutObject` URL with no additional required fields
