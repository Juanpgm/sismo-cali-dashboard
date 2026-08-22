# Field Form Session Specification

## Purpose

Keep an authenticated inspector signed in through transient backend failures, signing out only on authoritative rejections, and reduce redundant SDK loading that slows session startup in the field.

## Requirements

### Requirement: Transient-Error Retry Instead Of Sign-Out

WHEN the inspector-profile Firestore read (`getDoc`) fails with a transient error, the system MUST NOT sign the inspector out. It MUST retry with backoff and, if retries are exhausted, offer a manual Spanish retry affordance instead of forcing logout.

#### Scenario: Network blip does not log out

- GIVEN `getDoc` throws an `unavailable`-class error on auth state change
- WHEN the failure is classified
- THEN the system retries with backoff and does not call `signOut`

#### Scenario: Retries exhausted still avoids forced logout

- GIVEN retries are exhausted and the profile is still unreadable
- WHEN the final retry fails
- THEN the inspector sees a Spanish retry affordance instead of being signed out

### Requirement: Authoritative Rejection Still Signs Out

The system MUST still sign the inspector out when the profile document does not exist, when `activo === false`, or when the read fails with a fatal error code (`permission-denied`, `not-found`).

#### Scenario: Missing profile

- GIVEN the inspector's profile document does not exist
- WHEN auth state fires
- THEN the system signs out immediately

#### Scenario: Disabled inspector

- GIVEN the profile document has `activo === false`
- WHEN auth state fires
- THEN the system signs out immediately

#### Scenario: Fatal read error

- GIVEN `getDoc` fails with `permission-denied` or `not-found`
- WHEN the error is classified
- THEN it is treated as fatal, not transient, and the system signs out immediately

### Requirement: Error Classification Helper

The system MUST provide a pure function that classifies a Firebase/Firestore error object as `"transient"` or `"fatal"` from its error code, usable identically wherever profile reads occur.

#### Scenario: Transient codes

- GIVEN an error code in `{unavailable, deadline-exceeded, network-request-failed}`
- WHEN classified
- THEN the result is `"transient"`

#### Scenario: Fatal codes

- GIVEN an error code in `{permission-denied, not-found}`
- WHEN classified
- THEN the result is `"fatal"`

### Requirement: Deduplicated Firebase SDK Imports

The system MUST fetch each Firebase SDK module (auth, firestore) at most once per page load rather than once per importing file.

#### Scenario: Shared module fetch

- GIVEN both `auth.js` and `form.js` need `firebase-firestore.js`
- WHEN the page loads
- THEN the module is fetched once and shared between both

## Testability Notes

- `clasificarErrorFirestore` and the pure retry/backoff scheduling helper `backoffDelay` MUST have `node --test` coverage.
- The retry-vs-sign-out branches and the shared-import behavior MUST have Playwright e2e coverage against mocked transient/fatal `getDoc` failures.

## Out of Scope

No additional navigation/routing requirement is defined beyond the retry affordance above; the existing CDN-load watchdog (`index.html`) is unchanged by this spec.
