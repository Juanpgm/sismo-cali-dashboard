# Job Scheduling Specification

Change: `fastapi-backend-consolidation` · New capability (no prior spec exists).

## Purpose

The two pipeline jobs (`normalizador`, `integracion-f3`, `asignaciones`, `cruce-gestion` excluded — see Scope Exclusion Addendum / Extension 2) run as Railway-native cron services on the consolidated image, each with per-job schedule, credentials, and idempotency behavior unchanged from today's `railway_setup.py` fleet.

## Requirements

### Requirement: Per-Job Schedule Parity

The system MUST provision two Railway cron services on the consolidated image, each with the exact cron schedule from `railway_setup.py` `SERVICES` (ground truth over any module docstring):

| Service | Schedule (UTC) | Purpose |
|---|---|---|
| `cruce-sticker` | `7,22,37,52 13-23,0 * * *` | `cruce_sticker.main()` |
| `dashboard-refresh` | `*/15 13-23,0 * * *` | `refresh_data.py` + `fetch_reportes_api.py` (+ `survey_cali` ingest, see `survey-cali-collection`) |

#### Scenario: Each job schedule matches its table entry exactly

- GIVEN the two-service table above
- WHEN each Railway cron service is inspected post-migration
- THEN its schedule string matches the table entry for that service exactly

### Requirement: Legacy Crons Excluded From Migration

`normalizador`, `integracion-f3`, `asignaciones`, and `cruce-gestion` MUST NOT migrate to the consolidated image. `normalizador`'s sole purpose is pushing `tabla_integrada`/`integracion_stats` to the EDAN Google Sheet; `integracion-f3`'s and `asignaciones`' live gspread branches (input `F3_SRC_TAB`; output the VISITAS/asignaciones tabs) were confirmed operationally dead — the Sheets they touch are unused; `cruce-gestion`'s sole purpose is writing Firestore `dagma-85aad`/`cruce_criticos_survey`, and nothing dagma-related is used by the new backend (Extension 2). All four MUST remain on their current legacy `integracion_F1` Railway image, unchanged, until slice 9 decommission, where each requires explicit operator confirmation before removal. The absorbed job code for the jobs that do migrate MUST carry no Google Sheets or dagma read/write path.

#### Scenario: Four legacy jobs stay on the legacy service through slice 9

- GIVEN the two migrated jobs (`cruce-sticker`, `dashboard-refresh`) are running on the consolidated image
- WHEN `normalizador`'s, `integracion-f3`'s, `asignaciones`', and `cruce-gestion`'s deployments are inspected
- THEN all four remain on their original legacy `integracion_F1` Railway image, unmigrated

#### Scenario: Each legacy job's decommission requires explicit operator confirmation

- GIVEN slice 9 (decommission) is being executed
- WHEN any of `normalizador`, `integracion-f3`, `asignaciones`, `cruce-gestion` is considered for removal
- THEN removal proceeds only after explicit operator confirmation that its Google Sheet or dagma dependency is no longer needed

#### Scenario: Absorbed job code carries no Sheets or dagma read/write path

- GIVEN the migrated jobs' absorbed code in this repo's `jobs/` module
- WHEN their imports and code paths are inspected
- THEN none of them import `export_sheets.py`, perform any Google Sheets read/write, or reference any dagma credential/project id/collection

### Requirement: Watermark And Idempotent-Write Behavior Preserved

`cruce-sticker` MUST continue reading/advancing its watermark at `_meta/cruce_sticker_state` and writing `sticker_matches` incrementally. Both migrated jobs MUST remain safe to re-run without duplicating or corrupting output.

#### Scenario: cruce-sticker resumes from watermark after migration

- GIVEN `_meta/cruce_sticker_state` holds a watermark from before the migration
- WHEN `cruce-sticker` runs on the new Railway cron service
- THEN it resumes from that watermark rather than reprocessing from scratch

#### Scenario: Re-running a job does not duplicate output

- GIVEN either of the two migrated jobs completes a run
- WHEN the same job is triggered again before its next scheduled run
- THEN it produces the same idempotent result rather than duplicate records

### Requirement: Drift-Only Provisioning Convention Preserved

Provisioning the two cron services onto the consolidated image MUST follow the existing drift-only convention: only services whose desired schedule/command differ from current Railway state are touched.

#### Scenario: Re-running the provisioning script is a no-op when nothing changed

- GIVEN both Railway cron services already match their desired schedule/command
- WHEN the provisioning script runs again
- THEN it makes zero Railway API calls to modify any service

### Requirement: Per-Job Independent Rollback

Each of the two migrated jobs MUST be individually rollback-able by repointing its Railway service to the old image/command, independent of the other one and of the web service's cutover state.

#### Scenario: Rolling back one job does not affect others

- GIVEN `cruce-sticker` has migrated and a regression is found
- WHEN its Railway service is repointed back to the old image/command
- THEN the other migrated job (`dashboard-refresh`) and the web service remain on their current state, unaffected

### Requirement: integracion_F1 Job Code Absorbed With Provenance

The system MUST absorb the one `integracion_F1`-authored job entry point that migrates — `job_sticker.py`/`cruce_sticker.py` (`cruce-sticker`) — into this repo's `jobs/` module with explicit provenance, including its shared `integracion/` dependencies with Sheets and dagma branches cut. `normalizador`, `integracion-f3`, `asignaciones`, and `cruce-gestion` are excluded from absorption (see above), so `integracion_F1` remains a required deploy unit — solely for those four excluded jobs — until their slice 9 decommission.

#### Scenario: integracion_F1 remains required solely for the four excluded jobs after cruce-sticker migrates

- GIVEN `cruce-sticker` has migrated and `normalizador`/`integracion-f3`/`asignaciones`/`cruce-gestion` have not yet been decommissioned
- WHEN migration status is checked
- THEN `integracion_F1` is still required as a deploy unit, solely for `normalizador`, `integracion-f3`, `asignaciones`, and `cruce-gestion`

#### Scenario: dashboard-refresh needs no cross-repo absorption

- GIVEN `dashboard-refresh`'s code already lives in this repo (`deploy/Dockerfile`, `deploy/refresh.sh`)
- WHEN it migrates to a Railway cron service on the consolidated image
- THEN no code is pulled from `integracion_F1` for this job
