# Datos de Referencia en Blob Specification

Change: `seguimiento-inspectores-depurado` · New capability (no prior spec exists).

## Purpose

Vercel roster, Fase 2 verified listing, and the base CSV are published to and read from Vercel Blob
following the `blob_lkg` last-known-good pattern, replacing ad-hoc static file access and giving
`inspectores-depurado` a dated, degradable reference source.

## Requirements

### Requirement: Three Reference Sources In A Single Atomic Bundle
The system MUST publish Vercel roster, Fase 2 verified listing, and base CSV together as one
last-known-good (`blob_lkg`) JSON bundle (`referencia/inspectores/bundle.json`), not as three
independently-fetched blobs. Reconciled with `design.md` D8: a single atomic read avoids
partial-bundle state (e.g. a stale Vercel section paired with a freshly-republished Fase2 section)
and keeps one CLI publish step, one schema version, one privacy boundary (`access: 'private'`) to
reason about instead of three.

#### Scenario: One read returns all three sources together
- GIVEN the reference bundle has been published
- WHEN `inspectores-depurado` reads reference data
- THEN it fetches the single bundle once and gets `vercel`, `fase2`, and `main` sections from that
  one atomic read, never a partial mix of old and new sections

### Requirement: The Bundle Carries A Publish Timestamp
The system MUST record and expose a single publish/refresh timestamp (`generado_en`) for the bundle
as a whole, so consumers can determine how old the reference data is without interpreting the
identity records themselves.

#### Scenario: Timestamp is readable without interpreting identity data
- GIVEN the reference bundle was last published 3 days ago
- WHEN a consumer checks its freshness
- THEN `generado_en` is readable directly from the bundle without resolving any `np`/`fase`/
  identity field

### Requirement: Missing Or Corrupt Bundle (Or Section) Degrades Instead Of Failing
If the reference bundle is entirely missing/unreadable, or an unknown `schema` value makes
`parse_bundle` return `None`, the system MUST treat every section as absent. If the bundle parses
but one section (`vercel`/`fase2`/`main`) is malformed or empty, the system MUST treat only that
section as absent, degrading affected inspectors' `np_fuente` to the next hierarchy level or
`"ninguno"` — never raising an error that breaks the depurado table.

#### Scenario: Malformed Fase 2 section does not break the endpoint
- GIVEN the bundle parses but its `fase2` section is malformed
- WHEN the depurado table is computed
- THEN the endpoint still responds successfully, resolving affected inspectors' `np_fuente` from
  Vercel/main instead of Fase 2

#### Scenario: Whole bundle unreachable degrades to raw identity
- GIVEN the reference bundle cannot be read at all (missing, unreachable, or unknown `schema`)
- WHEN the depurado table is computed
- THEN every inspector's `np_fuente` falls back to `"main"` or `"ninguno"`, and the response still
  succeeds

### Requirement: fuente_dato Reflects Reference Data Availability
The system MUST let `fuente_dato` composition reflect which reference sources were actually
available and used per inspector at computation time, including the outlier aggregate label for
the collapsed non-person group (a dynamic string, not one of the standard enum values).

#### Scenario: Degraded source is reflected in fuente_dato
- GIVEN the bundle's `vercel` section was unavailable during computation
- WHEN `fuente_dato` is set for an inspector who would normally include Vercel
- THEN `fuente_dato` does not claim `"vercel"` as a contributing source for that inspector

### Requirement: Publish Path Follows blob_lkg / dashboard_refresh.py Pattern
Publishing/refreshing the bundle MUST reuse the existing `blob_lkg`/`dashboard_refresh.py` write
pattern (last-known-good replace, `access: 'private'`), not a new bespoke upload mechanism.

#### Scenario: Refresh replaces the prior bundle atomically
- GIVEN a new version of the base CSV is published via `scripts/publicar_referencia_inspectores.py`
- WHEN the CLI runs
- THEN the new bundle replaces the previous one following the same last-known-good mechanism used by
  other `blob_lkg` datasets, in one atomic write
