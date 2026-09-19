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

---

# Extension 2026-09-19 — Delta (complete base)

Source: `explore-extension.md`. No requirement above is modified or removed; the blocks below APPEND.

## ADDED Requirements

### Requirement: Bundle Entries Carry Optional Identity And Contact Fields At Schema 1

`EntradaReferencia` MUST accept, in addition to the existing fields, the optional
`nombre`, `telefono`, `codigo`, `creado_en`, `id`, `correo` and `tarjeta_profesional`, each
defaulting to an empty value when the source row omits it. The bundle's `schema` MUST stay `1`:
adding these keys is purely additive, so a deployed backend that predates them ignores them and a
new backend reads an old bundle without error. Bumping `schema` is forbidden for this change,
because the deployed `parse_bundle` returns `None` for any unknown value and would silently disable
depuración in production.

#### Scenario: New bundle parsed by the tolerant backend
- GIVEN a `schema: 1` bundle whose `main` rows carry `correo`, `telefono` and `tarjeta_profesional`
- WHEN `parse_bundle` runs
- THEN the resulting entries expose those values and `activa=true`

#### Scenario: Old bundle yields empty contact fields, not an error
- GIVEN a previously published `schema: 1` bundle with none of the new keys
- WHEN `parse_bundle` runs
- THEN it returns a valid bundle whose entries have empty `nombre`, `telefono`, `codigo`,
  `creado_en`, `id`, `correo` and `tarjeta_profesional`

#### Scenario: Unknown schema still degrades
- GIVEN a bundle with `schema: 2`
- WHEN `parse_bundle` runs
- THEN it returns `None` (unchanged behavior)

#### Scenario: A row with a wrong-typed optional field is coerced or skipped, never fatal
- GIVEN a `main` row whose `telefono` is a number and whose `creado_en` is `null`
- WHEN `parse_bundle` runs
- THEN the entry is produced with a string `telefono` and an empty `creado_en`, and no exception
  propagates

### Requirement: Publisher Reads Source Files As Strings

The publish CLI MUST read every source file with string dtypes so `cedula`, `codigo`,
`tarjeta_profesional` and `telefono` keep their exact digits — no numeric coercion, no leading-zero
loss, no scientific notation, no trailing `.0`. It MUST emit the optional fields defined above.

#### Scenario: Leading zeros survive publication
- GIVEN a source row with `codigoInspector="041"` and a 10-digit cédula starting with `1`
- WHEN the CLI publishes the bundle
- THEN the bundle carries `"041"` and the full cédula string, not `41` or a float

#### Scenario: Long numeric identifiers are not rendered in scientific notation
- GIVEN a source row with an 11-digit `telefono`
- WHEN the CLI publishes the bundle
- THEN the bundle carries all 11 digits as a string

### Requirement: Rollout Order Is Deploy-Then-Republish

A bundle carrying the new optional fields MUST NOT be published before the backend that parses them
tolerantly is deployed. Because the additive change is backward compatible in both directions, the
order is a safety margin, not a correctness gate — but the runbook MUST state it and the publish step
MUST be recorded with its `generado_en`.

#### Scenario: Republishing after deploy is a no-op for consumers on the old path
- GIVEN the tolerant backend is deployed and the bundle is republished with the new fields
- WHEN a request is served with `SEGUIMIENTO_DEPURACION` unset
- THEN the response shape is unchanged and no error is raised

### Requirement: Bundle PII Stays In The Private Store And Admin-Only In Responses

The bundle carries personal data (`nombre`, `correo`, `telefono`, `tarjeta_profesional`,
`identificacion`). It MUST remain in the `access: 'private'` Blob, MUST never be written to the
public last-known-good copy of `evaluaciones`, and its values MUST reach a response only under the
admin role. Log records naming a bundle entry MUST be redacted at INFO level.

#### Scenario: Bundle values never reach the public Blob copy
- GIVEN a bundle entry with a distinctive `correo` and `telefono`
- WHEN the route persists `evaluaciones` to the public Blob
- THEN neither value, nor the string `depuracion`, appears in the persisted bytes

#### Scenario: Publish emits no public URL
- GIVEN the CLI publishes the bundle
- WHEN the upload completes
- THEN it used `access: 'private'` and no public URL was minted or logged

---

# Efficiency Extension 2026-09-19 — Delta (incremental, quota-safe base)

Source: `efficiency-extension.md`, `design.md` D22 and D24. No requirement above is modified or removed;
the blocks below APPEND. The bundle `schema` stays `1` and the published file gains NO new key: the
content hash is computed by the reader.

## ADDED Requirements

### Requirement: The Loaded Bundle Carries A Content Hash (`huella`)

`ReferenciaBundle` MUST expose `huella`, a stable hash of the bundle's parsed content computed on read
(the same stable-JSON hash `blob_lkg.payload_hash` uses, so object-key order does not matter but row
order does). The consumers' cache keys MUST use `huella`, not the day-granular `generado_en`, so a
republish on the same day is detected. A degraded or empty bundle MUST have an empty `huella`. The
publisher MUST NOT be required to emit it, so a bundle published before this change keeps working.

#### Scenario: Identical content has an identical huella
- GIVEN two loads of the same bundle content, one with the JSON object keys in a different order
- WHEN `huella` is compared
- THEN the two values are equal

#### Scenario: A same-day republish is detected
- GIVEN a bundle republished on the same day (same `generado_en`) with one changed row
- WHEN the next TTL refresh loads it
- THEN `huella` differs and exactly one depuración recompute follows

#### Scenario: An optional contact field alone changes the huella
- GIVEN a republish that changes only one entry's `telefono`
- WHEN the bundle is loaded
- THEN `huella` differs (the hash covers the optional fields)

#### Scenario: Degraded bundle has an empty huella
- GIVEN the bundle is missing, corrupt or of an unknown schema
- WHEN the degraded bundle is produced
- THEN `huella` is `""` and never equals the `huella` of a real bundle

#### Scenario: A bundle published before this change still loads
- GIVEN a `schema: 1` bundle that has no `huella` anywhere in it
- WHEN `parse_bundle` runs
- THEN it returns a valid bundle whose `huella` is computed from its content

### Requirement: Reference Downloads Are Rate-Limited And Failure Keeps The Last-Good Bundle

With a 30-minute TTL the system MUST issue at most 2 reference GETs per hour and MUST NOT issue HEAD
or list requests on this path. A failed or corrupt download MUST keep the last-good bundle, MUST NOT
trigger a recompute, and MUST be retried at most once per TTL window — never once per request. A
degraded bundle MUST be adopted only when no good bundle exists.

#### Scenario: Fifty requests in an hour cost at most two GETs
- GIVEN a fake clock and 50 requests spread over one simulated hour
- WHEN they are served
- THEN at most 2 reference GETs occurred and no HEAD or list call was made

#### Scenario: A failed download keeps the last-good bundle
- GIVEN a good bundle is cached and the next scheduled GET fails or returns a corrupt bundle
- WHEN requests arrive
- THEN the last-good bundle is still served, no recompute happens, and the retry occurs at most once
  in that TTL window

#### Scenario: Cold-start failure is retried once per TTL, not per request
- GIVEN no bundle was ever loaded and the GET fails
- WHEN 20 requests arrive within the TTL
- THEN the degraded bundle is served, exactly one GET was attempted, and the next attempt waits for
  the next TTL window

#### Scenario: Recovery with identical content does not recompute
- GIVEN the GET failed and a later GET succeeds with content identical to the last-good bundle
- WHEN the bundle is adopted
- THEN `huella` is unchanged and no recompute happens

#### Scenario: Recovery with new content recomputes once
- GIVEN a later GET succeeds with different content
- WHEN the bundle is adopted
- THEN `huella` changes and exactly one recompute follows

### Requirement: Reference Loading Never Blocks Other Requests

The reference download MUST run outside the cache lock, single-flight: at most one caller downloads
while all other callers read the last-good bundle without waiting. A slow or failing loader MUST NOT
leave the lock or the in-flight marker held.

#### Scenario: A slow download does not block a fast-path request
- GIVEN a reference download in progress that will take a long time
- WHEN a concurrent request whose result is still fresh arrives
- THEN it is served without waiting for the download

#### Scenario: Concurrent stale callers download once
- GIVEN the bundle's TTL has expired and 20 requests arrive together
- WHEN they are served
- THEN exactly one GET was issued and every request received a bundle (the last-good one until the new
  one is adopted)

#### Scenario: A loader exception releases everything
- GIVEN the download raises
- WHEN the next request arrives
- THEN no lock or marker is left held, and the retry policy above applies
