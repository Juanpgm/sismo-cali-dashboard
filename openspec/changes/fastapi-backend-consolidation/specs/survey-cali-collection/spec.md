# Survey Cali Collection Specification

Change: `fastapi-backend-consolidation` · New capability (Scope Addendum, 2026-08-25). No prior spec exists. Depends on `backend-platform` (admin auth, credentials) and `job-scheduling` (`dashboard-refresh` cadence).

## Purpose

Persist the Survey123-sourced Survey Cali inspection records as a versioned Firestore collection (`survey_cali`, project `sismo-agosto-sgred`), ingested incrementally by the existing 15-min refresh job, editable via admin-gated CRUD, with full append-only revision history and safe reconciliation between pipeline ingest and manual edits.

## Requirements

### Requirement: Incremental, Idempotent Ingestion Keyed By GlobalID

The `dashboard-refresh` job MUST upsert Survey Cali inspection records into `survey_cali`, keyed by `GlobalID`. On each run it MUST write only records whose source content changed since the last ingest (detected via source `EditDate` and/or a content hash) and MUST NOT perform a full-collection rewrite.

#### Scenario: Unchanged record is skipped

- GIVEN a `survey_cali` record whose source `EditDate` has not advanced since the last ingest
- WHEN `dashboard-refresh` runs
- THEN no write is issued for that record

#### Scenario: Changed record is upserted by GlobalID

- GIVEN a Survey Cali source record's `EditDate` has advanced since the last ingest
- WHEN `dashboard-refresh` runs
- THEN the `survey_cali/{GlobalID}` document is upserted with the new content, not duplicated under a new id

#### Scenario: A run never rewrites the full collection

- GIVEN `dashboard-refresh` processes N source records where fewer than N changed
- WHEN the run completes
- THEN the number of Firestore writes equals the number of changed records, not N

### Requirement: Admin-Gated CRUD With Merge-Only Mutations

The system MUST expose create/read/update/delete endpoints over `survey_cali`, gated identically to other admin routes (Bearer + `admin`, per `backend-platform`). Every mutation MUST be PATCH/upsert-style with merge semantics; no endpoint MAY perform a full-document replace.

#### Scenario: Non-admin call is rejected

- GIVEN a valid Bearer token whose resolved role is not `admin`
- WHEN any `survey_cali` CRUD endpoint is called
- THEN the request is rejected and no Firestore state changes

#### Scenario: Update is a merge, not a replace

- GIVEN a `survey_cali` record with fields `{a: 1, b: 2}`
- WHEN an admin calls update with `{b: 3}`
- THEN the resulting document is `{a: 1, b: 3}` — field `a` is untouched, not dropped

### Requirement: Append-Only Per-Record Revision History

Every mutation to a `survey_cali` record — including ingest-driven updates — MUST append a revision document (e.g. `survey_cali/{id}/history/{rev}`) recording author (`pipeline` or a user uid), timestamp, and the changed fields. History MUST be append-only: no revision is ever deleted or overwritten.

#### Scenario: Ingest update writes a pipeline-authored revision

- GIVEN `dashboard-refresh` upserts a changed field on a `survey_cali` record
- WHEN the upsert completes
- THEN a new revision is appended with `author:'pipeline'`, a timestamp, and the changed field(s)

#### Scenario: Admin update writes a uid-authored revision

- GIVEN an admin calls update on a `survey_cali` record via the CRUD endpoint
- WHEN the update completes
- THEN a new revision is appended with `author` set to the admin's uid, a timestamp, and the changed field(s)

#### Scenario: History is never destroyed

- GIVEN a record has N existing revisions
- WHEN any subsequent mutation (ingest, CRUD update, or revert) occurs
- THEN the record has N+1 revisions; none of the prior N are deleted or altered

### Requirement: List History, View Diff, And Revert-As-New-Revision

The system MUST let an admin (a) list a record's revision history, (b) view the changed-field diff for a given revision, and (c) revert a record to a prior revision's field values. Revert MUST itself append a new revision — it MUST NOT delete or rewrite any existing history entry.

#### Scenario: Listing history returns all revisions in order

- GIVEN a record has 5 revisions
- WHEN an admin lists its history
- THEN all 5 revisions are returned, ordered by timestamp

#### Scenario: Viewing a revision shows its changed fields

- GIVEN revision R3 recorded `{estado: 'revisado'}` as its changed fields
- WHEN an admin views R3's diff
- THEN the response shows `estado` changed to `'revisado'` at that revision

#### Scenario: Revert creates a new revision instead of mutating history

- GIVEN a record is currently at revision R5 and an admin reverts it to R3's values
- WHEN the revert completes
- THEN the record's current state matches R3's field values, a new revision R6 is appended recording the revert, and R1–R5 remain unchanged in history

### Requirement: Default Read Path Returns Current State Only

The dashboard/default read path for `survey_cali` MUST return only each record's current (most recent) state. Revision history MUST be reachable only via an explicit history request, never included in the default list/read response.

#### Scenario: Default list omits history

- GIVEN a `survey_cali` record has 5 revisions
- WHEN the default list/read endpoint is called
- THEN the response contains only current field values, with no embedded history array

#### Scenario: History is available on explicit request

- GIVEN the default read path was just called
- WHEN the admin explicitly requests that record's history
- THEN the full revision list is returned

### Requirement: Source-Wins Ingest-Versus-Manual-Edit Conflict Resolution

Per-field, incremental ingest MUST only overwrite a field when the upstream source value for that field changed since the previous ingest. A manually-edited field MUST survive subsequent pipeline runs unless the source itself moves that field, in which case the source value overwrites it — visibly, and revertibly via history.

#### Scenario: Manual edit survives an unrelated ingest run

- GIVEN an admin manually edited field `notas` on a record, and the upstream source has not changed `notas` since
- WHEN `dashboard-refresh` ingests that record again
- THEN `notas` retains the admin's manually-edited value

#### Scenario: Source move overwrites a manually-edited field, visibly

- GIVEN an admin manually edited field `direccion`, and the upstream source subsequently changes `direccion` to a new value
- WHEN `dashboard-refresh` ingests that record
- THEN `direccion` is overwritten with the new source value, and a pipeline-authored revision records this overwrite

#### Scenario: An overwritten manual edit is revertible

- GIVEN the source-wins overwrite scenario above has occurred
- WHEN an admin reverts to the revision preceding the overwrite
- THEN `direccion` returns to the admin's manually-edited value, recorded as a new revision
