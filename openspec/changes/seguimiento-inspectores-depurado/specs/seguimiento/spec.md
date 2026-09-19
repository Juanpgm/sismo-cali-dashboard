# Delta for Seguimiento

Change: `seguimiento-inspectores-depurado`. NOTE: the proposal lists `seguimiento` under "Modified
Capabilities", but no `openspec/specs/seguimiento/spec.md` baseline exists yet (this tab shipped
before specs were tracked for it). Per sdd-spec rules, this is written as an `ADDED` block — a full
first-time spec for the tab's identity-consumption and non-person UI behavior — rather than a
MODIFIED diff against a nonexistent baseline.

## ADDED Requirements

### Requirement: Frontend Consumes Backend-Resolved NP
`web/js/seguimiento.js` MUST use the `np`/`np_fuente` fields returned by the backend's depurado
endpoint (`inspectores-depurado`) as authoritative, and MUST NOT overwrite or backfill them with
raw `profesional.rango` from the live API.

#### Scenario: Backend NP is not overwritten by raw rango
- GIVEN the backend returns `np="P3"`, `np_fuente="fase2"` for an inspector
- WHEN the live API also has `profesional.rango="P1"` for the same person
- THEN the table displays `np="P3"`, not `"P1"`

#### Scenario: Grouping by identity uses resolved np, not a client-side merge
- GIVEN multiple raw records exist for the same inspector identity
- WHEN the frontend groups them for display
- THEN it uses the backend-resolved `np`/`np_fuente` directly, not the prior client-side
  "first non-empty wins" merge over `insp.np`

### Requirement: Non-Person Group Row Is Expandable
The Seguimiento table MUST render the non-person aggregate as a single collapsed row with its
counts, and MUST provide a UI control to expand it and view the underlying per-inspector detail
without navigating away from the tab.

#### Scenario: Expanding the aggregate row shows individual entries
- GIVEN the aggregate non-person row is visible with a count of 5
- WHEN a user expands it
- THEN the 5 underlying individual inspector entries are displayed inline

#### Scenario: Collapsed by default
- GIVEN the Seguimiento table loads
- WHEN the non-person aggregate row is first rendered
- THEN its detail is collapsed until the user interacts with it

### Requirement: Manual Review Section Surfaces Unresolved Depuration Cases
[SUPERSEDED — see extension delta]
Seguimiento MUST provide a dedicated section (tab or expandable panel, separate from the main
professional table) that lists every record the backend's `depuracion.revision_manual` returns —
código remaps in conflict, remapped códigos with no owner among current inspectors, Vercel-internal
duplicate códigos, and any other case the pipeline could not resolve automatically. This is the
live equivalent of the xlsx's "Pendientes revisión manual" sheet and MUST NOT require opening the
notebook or reading a static file to see this information.

#### Scenario: Conflicted remap is visible without leaving the dashboard
- GIVEN `depuracion.revision_manual` contains an entry with `motivo="remap_conflicto"`
- WHEN a user opens the manual-review section
- THEN that entry is listed with its código and motivo, without needing any file outside the app

#### Scenario: Empty revision_manual renders an empty, not missing, section
- GIVEN `depuracion.revision_manual` is an empty list
- WHEN the manual-review section renders
- THEN it shows an explicit "nothing pending" state, not a hidden or broken section

### Requirement: Table And Export Reflect Depurado Fields Consistently
[SUPERSEDED — see extension delta]
The XLSX export and search functionality MUST operate on the same backend-resolved fields (`np`,
`fase`, `estado_sugerido`, `fuente_dato`) as the on-screen table, not a separately-derived client
value.

#### Scenario: Exported XLSX matches on-screen np
- GIVEN the on-screen table shows a resolved `np` for an inspector
- WHEN that inspector is included in the XLSX export
- THEN the exported `np` value matches the on-screen value exactly

#### Scenario: Search matches the resolved np, not a stale client value
- GIVEN a user searches by an inspector's Fase-2-resolved `np`
- WHEN the search runs
- THEN it matches against the backend-resolved `np`, not a raw `profesional.rango` value

---

# Extension 2026-09-19 — Delta (complete base)

Source: `explore-extension.md`. Blocks marked MODIFIED REPLACE the same-named requirement above
(annotated `[SUPERSEDED — see extension delta]`); ADDED blocks append.

## ADDED Requirements

### Requirement: Rows Are Seeded From depuracion.inspectores

When `depuracion.activa` is true, `web/js/seguimiento.js` MUST create one table row per entry in
`depuracion.inspectores`, independently of whether that person has any sticker or survey in the
selected date range, plus the `GRUPO-EXTERNOS` row. Sticker/survey records MUST only enrich rows
that already exist from that seed, and a sticker attributed to a cédula with no seeded row MUST
still create its row (never be dropped). When `depuracion` is absent or `activa:false`, the current
"create a row only from stickers/surveys" behavior MUST be preserved byte-identically.

#### Scenario: Zero-activity person still appears
- GIVEN `depuracion.inspectores` contains a person with no sticker and no survey in range
- WHEN the table renders
- THEN that person has a row, with zero counters

#### Scenario: Sticker for an unseeded cédula still produces a row
- GIVEN a sticker whose identity key is absent from `depuracion.inspectores`
- WHEN the table renders
- THEN a row exists for it and no record is silently dropped

#### Scenario: Legacy path unchanged when depuración is off
- GIVEN the response has no `depuracion` key, or `activa:false`
- WHEN the table renders
- THEN rows come only from stickers/surveys, exactly as before

### Requirement: KPIs Are Not Diluted By Zero-Activity Rows

The Seguimiento KPIs ("profesionales activos", averages, the timeline and any per-professional
chart) MUST be computed over rows WITH activity in the selected range, not over the seeded row
count. The total row count MUST be surfaced separately from the active count so the two are never
conflated.

#### Scenario: Seeding does not change the active-professional KPI
- GIVEN 373 seeded rows of which 116 have activity in range
- WHEN the KPIs render
- THEN "profesionales activos" is 116 and the averages divide by 116, not 373

#### Scenario: Total is still visible
- GIVEN the same data
- WHEN the header renders
- THEN the 373 total is shown as its own figure, labeled distinctly from the active count

#### Scenario: Empty range does not divide by zero
- GIVEN a date range in which no row has activity
- WHEN the KPIs render
- THEN averages render as `0` or `—` and no `NaN`/`Infinity` reaches the DOM

### Requirement: Estado Sugerido Filter And Column

The table MUST expose `estado_sugerido` as a column and as a filter control offering at least
`activo`, `revisar`, `candidato_desactivacion`, `no_persona` and "all", defaulting to "all". The
filter MUST compose with the existing search and date-range controls rather than replacing them.

#### Scenario: Filtering narrows the table without touching the date range
- GIVEN the filter is set to `candidato_desactivacion`
- WHEN the table re-renders
- THEN only those rows are shown and the selected date range is unchanged

#### Scenario: Filter composes with search
- GIVEN a search term matching 3 rows, 1 of them `activo`
- WHEN the filter is set to `activo`
- THEN exactly 1 row is shown

#### Scenario: Unknown estado value does not hide a row under "all"
- GIVEN a row whose `estado_sugerido` is an unexpected string
- WHEN the filter is "all"
- THEN the row is still listed

### Requirement: Degraded Or Absent Depuración Is Announced

When `depuracion` is absent, or `activa:false` with any `motivo` (including
`"stickers_degradados"`), Seguimiento MUST render an explicit banner stating that the depurado table
is unavailable and naming the `motivo`, and MUST fall back to the legacy identity path rather than
render an empty table. When `activa:true`, `referencia_generada_en` MUST remain visible.

#### Scenario: Degraded stickers show the banner
- GIVEN `depuracion.activa=false` with `motivo="stickers_degradados"`
- WHEN the tab renders
- THEN a banner names the motivo and the legacy rows are still displayed

#### Scenario: Active depuración shows freshness instead of a banner
- GIVEN `depuracion.activa=true` with `referencia_generada_en="2026-09-19"`
- WHEN the tab renders
- THEN no degraded banner is shown and the date is visible

### Requirement: Seguimiento Renders The Full Universe Within Budget

Rendering, filtering and sorting the table MUST stay responsive with at least 400 seeded rows plus
the aggregate row, without freezing the tab. A regression test MUST cover this size.

#### Scenario: 400-row render stays within budget
- GIVEN 400 seeded inspectors and the aggregate row
- WHEN the table renders and is then filtered and sorted
- THEN the operation completes within the project's existing perf-test budget and no row is lost

#### Scenario: Re-filtering does not rebuild from scratch quadratically
- GIVEN the same 400 rows
- WHEN the estado filter changes twice in a row
- THEN each re-render stays within the same budget

## MODIFIED Requirements

### Requirement: Manual Review Section Surfaces Unresolved Depuration Cases

Seguimiento MUST provide a dedicated section (tab or expandable panel, separate from the main
professional table) that lists every record the backend's `depuracion.revision_manual` returns —
código remaps in conflict (`remap_conflicto`), remapped códigos with no owner among current
inspectors (`remap_sin_duenio`), Vercel-internal duplicate códigos (`codigo_vercel_duplicado`),
duplicate cédulas in the base CSV (`cedula_duplicada_main`), base rows without a usable cédula
(`main_sin_cedula`), and any other case the pipeline could not resolve automatically. Each entry
MUST display its `motivo` together with the affected person's `nombre_completo` and `identificacion`
when the backend supplies them, and that identity detail MUST render only for the admin role. This
is the live equivalent of the xlsx's "Pendientes revisión manual" sheet and MUST NOT require opening
the notebook or reading a static file.
(Previously: the section only had to list `codigo` + `motivo`, and predated the
`remap_sin_duenio` / `cedula_duplicada_main` / `main_sin_cedula` motivos.)

#### Scenario: Conflicted remap is visible without leaving the dashboard
- GIVEN `depuracion.revision_manual` contains an entry with `motivo="remap_conflicto"`
- WHEN a user opens the manual-review section
- THEN that entry is listed with its código and motivo, without needing any file outside the app

#### Scenario: Empty revision_manual renders an empty, not missing, section
- GIVEN `depuracion.revision_manual` is an empty list
- WHEN the manual-review section renders
- THEN it shows an explicit "nothing pending" state, not a hidden or broken section

#### Scenario: New motivos are listed with name and cédula for an admin
- GIVEN entries with `motivo="remap_sin_duenio"` and `motivo="cedula_duplicada_main"`
- WHEN an admin opens the section
- THEN each entry shows its `motivo`, `nombre_completo` and `identificacion`

#### Scenario: An unrecognized motivo is still listed
- GIVEN an entry whose `motivo` the frontend has no label for
- WHEN the section renders
- THEN the raw `motivo` string is shown rather than the entry being dropped

### Requirement: Table And Export Reflect Depurado Fields Consistently

The XLSX export and search functionality MUST operate on the same backend-resolved fields (`np`,
`fase`, `estado_sugerido`, `fuente_dato`) as the on-screen table, not a separately-derived client
value. Because seeding can push the table past the mass PDF export's 200-row cap, the mass PDF
export MUST default to the currently-visible rows WITH activity in the selected range, MUST state
that scope in the UI before generating, and MUST refuse (with an explicit message) rather than
silently truncate when the selected scope still exceeds the cap.
(Previously: the requirement covered only XLSX/search consistency and said nothing about the mass
PDF export scope, which silently capped at 200 rows.)

#### Scenario: Exported XLSX matches on-screen np
- GIVEN the on-screen table shows a resolved `np` for an inspector
- WHEN that inspector is included in the XLSX export
- THEN the exported `np` value matches the on-screen value exactly

#### Scenario: Search matches the resolved np, not a stale client value
- GIVEN a user searches by an inspector's Fase-2-resolved `np`
- WHEN the search runs
- THEN it matches against the backend-resolved `np`, not a raw `profesional.rango` value

#### Scenario: Mass PDF export defaults to rows with activity
- GIVEN 373 visible rows of which 116 have activity in range
- WHEN the user triggers the mass PDF export without changing its scope
- THEN 116 reports are generated and the UI stated that scope beforehand

#### Scenario: Over-cap selection refuses instead of truncating
- GIVEN the chosen scope still resolves to more than 200 rows
- WHEN the user triggers the mass export
- THEN it is refused with a message naming the cap and the current count, and no partial batch is
  produced

---

# Efficiency Extension 2026-09-19 — Delta (incremental, quota-safe base)

Source: `efficiency-extension.md`, `design.md` D21-D32 and "Efficiency Acceptance Budgets". This is an
ACCEPTANCE CRITERION for flipping `SEGUIMIENTO_DEPURACION`, not an optimization. Requirements below
describe the `GET /stickers-atencionsismo` endpoint that feeds the tab and the tab's own behavior.
Budgets assume one process and at least one admin tab continuously open; "static inputs" means no
upstream change. `snapshot_id` in the frontend is the opaque `ETag` value — the response body gains no
new key (the flag-off body stays byte-identical). Every ADDED block appends; nothing above changes.

## ADDED Requirements

### Requirement: Depuración Is Opt-In Per Request

The response MUST contain a `depuracion` block only when ALL of the following hold: the request carries
exactly `depuracion=1`, `SEGUIMIENTO_DEPURACION` is on, the caller is an admin, and the sticker
snapshot is live (a degraded snapshot still yields the `stickers_degradados` block, per the earlier
requirement). A request that does not opt in — and every viewer request — MUST cost 0 depuración work:
no `depurar()`, no survey scan, no reference download. Only Seguimiento sends the parameter.

#### Scenario: Seguimiento opts in
- GIVEN the flag is on, the caller is an admin and the snapshot is live
- WHEN Seguimiento requests `GET /stickers-atencionsismo?depuracion=1`
- THEN the response includes `depuracion`

#### Scenario: A request without the parameter costs nothing
- GIVEN the flag is on, an admin caller and a live snapshot
- WHEN the request has no `depuracion` parameter
- THEN there is no `depuracion` key and `depurar()`, the survey scan and the reference download all
  ran 0 times for it

#### Scenario: Only the exact value 1 opts in
- GIVEN the values `0`, `true`, `yes`, an empty value, `01`, `1 ` (trailing space) and a repeated
  `depuracion=1&depuracion=0`
- WHEN each is sent
- THEN none opts in and none costs any depuración work

#### Scenario: The flag off wins over the parameter
- GIVEN `SEGUIMIENTO_DEPURACION` is unset or `0`
- WHEN a request carries `?depuracion=1`
- THEN there is no `depuracion` key and no depuración work

#### Scenario: A viewer costs zero even with the parameter
- GIVEN an authenticated viewer sending `?depuracion=1`
- WHEN the response is assembled
- THEN there is no `depuracion` key and 0 depuración work was done

#### Scenario: The non-opt-in body is byte-identical to the flag-off shape
- GIVEN a request without the parameter and a golden captured from the pre-extension serialization
- WHEN the encoded body is compared
- THEN the bytes are identical

### Requirement: The Stickers Snapshot Version Is Content-Stable

The system MUST derive a `snapshot_version` from a stable hash of the full served sticker payload after
every walk, and MUST keep the previous list object and version when the content is identical. Any
content change, and a change of the `degraded` flag, MUST produce a new version. The version MUST NOT
be exposed as a body key; its only wire form is the `ETag` (see below).

#### Scenario: A refetch with identical content keeps the version
- GIVEN the 5-minute walk returns exactly the same content
- WHEN the snapshot is refreshed
- THEN the list object and `snapshot_version` are unchanged and no downstream recompute is triggered

#### Scenario: One changed sticker produces a new version
- GIVEN a walk in which exactly one sticker differs
- WHEN the snapshot is refreshed
- THEN `snapshot_version` changes

#### Scenario: An np-only change is detected
- GIVEN a walk that differs only in a field the Blob copy redacts (`inspector.np`)
- WHEN the snapshot is refreshed
- THEN `snapshot_version` changes, while the hash that gates the Blob PUT does not

#### Scenario: Degraded state changes the version
- GIVEN identical content served once live and once from the redacted last-known-good copy
- WHEN the versions are compared
- THEN they differ

#### Scenario: A failed walk keeps the previous snapshot
- GIVEN the walk fails
- WHEN the snapshot is read
- THEN the previous object and version are served and no version bump occurs

#### Scenario: Volatile per-fetch fields never enter the hash
- GIVEN two walks of unchanged upstream data
- WHEN the hashed payloads are compared
- THEN they are equal (no fetch-time timestamp is part of the hashed content)

### Requirement: Conditional Responses Use ETag And 304

Every response MUST carry a strong `ETag` derived from `(snapshot_version, depuracion_version, role,
degraded)` and the encoding (`depuracion_version` is `"none"` for a non-opt-in request). A request whose
`If-None-Match` matches MUST receive 304 with an empty body and the `ETag` header. Authentication and
role resolution MUST precede the 304 decision, so an unauthenticated request never gets a 304 and one
role's ETag never validates another role's body. Responses MUST carry `Cache-Control: no-store` (the
body carries personal data; the client revalidates manually, in memory) and `Vary: Authorization,
Accept-Encoding`.

#### Scenario: Matching If-None-Match returns 304 with an empty body
- GIVEN a previous response with ETag `E`
- WHEN the same caller requests again with `If-None-Match: E` and nothing changed
- THEN the response is 304, the body is empty and the `ETag` header is present

#### Scenario: Changed content changes the ETag
- GIVEN one sticker changed
- WHEN the caller requests with the old ETag
- THEN the response is 200 with a new ETag

#### Scenario: If-None-Match accepts lists, weak validators and a star
- GIVEN `If-None-Match` of the forms `"a", "E"`, `W/"E"` and `*`
- WHEN each is sent
- THEN each matching form yields 304, a non-matching or malformed value yields 200

#### Scenario: A stale validator yields a full response
- GIVEN an `If-None-Match` that does not match the current ETag
- WHEN the request is served
- THEN it is 200 with the full body

#### Scenario: An unauthenticated request never gets a 304
- GIVEN a request with a valid-looking `If-None-Match` but no valid credential
- WHEN it is served
- THEN the response is 401, never 304

#### Scenario: One role's ETag never validates another role's body
- GIVEN a viewer sends the ETag issued to an admin
- WHEN the request is served
- THEN the response is 200 with the viewer's body

#### Scenario: Responses are not stored by the browser
- GIVEN a 200 and a 304
- WHEN their headers are inspected
- THEN both carry `Cache-Control: no-store` and `Vary: Authorization, Accept-Encoding`

### Requirement: Responses Are Served As Precomputed, Gzip-Capable Bytes

The system MUST cache the fully encoded body per `(snapshot_version, depuracion_version, role, degraded,
encoding)` so that N identical requests cost one serialization and, for gzip, one compression. It MUST
send `Content-Encoding: gzip` only when `Accept-Encoding` allows it (an absent header, `identity`, or
`gzip;q=0` means identity). The ETag MUST differ per representation. Cached bytes MUST NOT cross roles,
degraded state or opt-in state.

#### Scenario: Fifty identical requests serialize once
- GIVEN 50 identical requests within the TTLs
- WHEN they are served
- THEN the body was serialized once and, when gzip was accepted, compressed once

#### Scenario: Gzip is used when accepted and round-trips exactly
- GIVEN `Accept-Encoding: gzip`
- WHEN the response is received and decompressed
- THEN `Content-Encoding: gzip` was present and the decompressed bytes equal the identity body

#### Scenario: Gzip is not forced on clients that refuse it
- GIVEN no `Accept-Encoding`, `identity` or `gzip;q=0`
- WHEN the request is served
- THEN the body is sent uncompressed and validators still work for that representation

#### Scenario: Cached bytes never cross roles or states
- GIVEN admin, viewer, degraded and non-opt-in variants exist
- WHEN each is requested
- THEN each receives its own bytes and an admin body is never served to a viewer

#### Scenario: An empty evaluaciones payload still encodes
- GIVEN a snapshot with an empty `evaluaciones` list
- WHEN it is served with gzip
- THEN the response is valid and round-trips

### Requirement: CORS Permits And Exposes The Conditional-Request Headers

Because the web client and the API are cross-origin, the API MUST allow the `If-None-Match` request
header and MUST expose the `ETag` response header, in addition to the headers it already allows. This
MUST be verified in a real browser, and the frontend MUST degrade to a plain full render when the
`ETag` is not readable.

#### Scenario: The preflight for a conditional request passes
- GIVEN a cross-origin `OPTIONS` preflight with `Access-Control-Request-Headers: if-none-match`
- WHEN it is served
- THEN the header is allowed, and `Authorization` and `Content-Type` remain allowed

#### Scenario: The ETag is readable by the page
- GIVEN a cross-origin response
- WHEN its headers are inspected
- THEN `Access-Control-Expose-Headers` names `ETag` (and only what is intended)

#### Scenario: A disallowed origin is still rejected
- GIVEN an origin outside the configured list
- WHEN it sends a request
- THEN it gets no CORS permission

### Requirement: Seguimiento Retains The Last Snapshot And Skips Redundant Renders

`web/js/seguimiento.js` MUST keep the last `{snapshot_id, stickers, depuracion}` in memory, render from
it immediately on tab open, revalidate in the background with `If-None-Match`, and skip `render()` when
the answer is a 304 or an equal `snapshot_id`. A changed `snapshot_id` MUST cause exactly one
re-render. The retained value contains administrator-only personal data: it MUST live in memory only,
MUST NOT be written to browser storage, and MUST be cleared on sign-out, on role change and on a
401/403 response.

#### Scenario: Reopening with an unchanged snapshot does not re-render
- GIVEN Seguimiento was opened once and the backend snapshot is unchanged
- WHEN the tab is opened again and revalidation returns 304
- THEN it renders immediately from the retained value and performs no additional `render()`

#### Scenario: A changed snapshot re-renders exactly once
- GIVEN revalidation returns 200 with a different ETag
- WHEN the response arrives
- THEN the retained value is replaced and `render()` runs once

#### Scenario: First open sends no validator
- GIVEN nothing is retained
- WHEN Seguimiento opens
- THEN a full request without `If-None-Match` is sent and rendered normally, and a 304 without a
  retained body is treated as a full fetch, never a blank table

#### Scenario: A failed revalidation keeps what is on screen
- GIVEN a network error, a 5xx or malformed JSON during revalidation
- WHEN it occurs
- THEN the retained render stays, nothing throws and no blank table appears

#### Scenario: Losing authorization clears the retained data
- GIVEN revalidation returns 401 or 403, or the user signs out or changes role
- WHEN it happens
- THEN the retained value is discarded

#### Scenario: Nothing is persisted
- GIVEN the retained payload
- WHEN storage is inspected
- THEN `localStorage`, `sessionStorage` and IndexedDB contain none of it

#### Scenario: An unreadable ETag degrades to a normal render
- GIVEN the ETag header cannot be read
- WHEN the tab opens
- THEN it does a full render every time, with no error

#### Scenario: Filters and date range still re-render
- GIVEN an unchanged snapshot
- WHEN the user changes the date range or the estado filter
- THEN the table re-renders (skip-render suppresses only redundant snapshot-driven renders)

#### Scenario: Two rapid opens share one revalidation
- GIVEN the tab is opened twice in quick succession
- WHEN revalidation runs
- THEN only one background request is in flight

### Requirement: The Stickers Tab Neither Requests Nor Receives Depuración

The Stickers tab MUST NOT send `depuracion=1`, MUST NOT receive a `depuracion` block, and MUST NOT
render one if it ever appears. Opening the Stickers tab MUST NOT trigger a depuración computation.

#### Scenario: The Stickers tab request has no depuracion parameter
- GIVEN the Stickers tab loads its data
- WHEN its request URL is inspected
- THEN it contains no `depuracion` parameter

#### Scenario: The Stickers tab response carries no depuración
- GIVEN an admin with the flag on has the Stickers tab open
- WHEN the response arrives
- THEN there is no `depuracion` key, no personal data block, and no depuración work ran for it

#### Scenario: An unexpected block is ignored
- GIVEN a response to the Stickers tab that contains a `depuracion` key
- WHEN it renders
- THEN the block is dropped and nothing from it is displayed

#### Scenario: Both tabs open share one snapshot
- GIVEN the Stickers tab and Seguimiento are both open
- WHEN both refresh within the same TTL
- THEN there is one walk in total and only Seguimiento's request costs depuración work

### Requirement: Upstream Reads And Recomputes Stay Within Budget

With static inputs and one admin tab continuously open, over one hour: recomputes MUST be 0 (exactly 1
per day at the Bogotá midnight rollover); roster scans ≤ 2, survey scans ≤ 1 and `evaluaciones`
scans ≤ 4 (derived from the 30/60/15-minute TTLs); Atención Sismo walks ≤ 1 per 5-minute TTL and 0
when nobody is viewing; reference GETs ≤ 2. Each changed fingerprint MUST cost exactly one recompute.
The parity harness MUST report the measured document counts (`E` evaluaciones, `I` inspectores, `S`
survey names), the projected Firestore reads per open hour (`2·I + S + 4·E`) and per day, and the flag
MUST NOT be flipped unless that projection is within the ratified daily budget (default 10,000
reads/day; design O1). Admin roster writes MUST invalidate the roster component immediately.

#### Scenario: Fifty requests within the TTLs
- GIVEN a cold start and 50 requests inside every TTL
- WHEN they are served
- THEN there was 1 walk, 1 evaluaciones scan, 1 roster scan, 1 survey scan, 1 `depurar()` and 1
  reference GET

#### Scenario: A static hour costs no recompute
- GIVEN a simulated hour of a continuously open tab with unchanged upstream data
- WHEN the request log is counted
- THEN `depurar()` ran 0 times, and scans stayed within ≤ 2 roster, ≤ 1 survey, ≤ 4 evaluaciones and
  ≤ 12 walks, ≤ 2 reference GETs

#### Scenario: Past the walk TTL only the walk repeats
- GIVEN 5 minutes and 1 second pass with identical upstream content
- WHEN the next request arrives
- THEN there is +1 walk, +0 `depurar()`, +0 survey/roster scans, +0 Blob PUT, the same ETag, and a
  conditional request returns 304 with an empty body

#### Scenario: TTL boundary is inclusive of freshness
- GIVEN a value exactly at its TTL age
- WHEN it is read
- THEN it is still fresh, and one second later it is stale

#### Scenario: One changed sticker recomputes once
- GIVEN exactly one sticker changed
- WHEN requests arrive
- THEN there is exactly +1 `depurar()` and a new ETag

#### Scenario: Nobody viewing costs nothing
- GIVEN no request arrives for an hour
- WHEN the ledger is read
- THEN there were 0 walks and 0 scans

#### Scenario: An admin roster write shows up immediately
- GIVEN an admin creates or disables an inspector
- WHEN the next request arrives
- THEN the roster is re-read once and the table recomputes only if the roster content changed

#### Scenario: The ledger gates the flag flip
- GIVEN the parity harness measured `E`, `I` and `S`
- WHEN the projection is computed from the configured TTLs
- THEN the report prints the arithmetic, per open hour and per day, and the flip is blocked when it
  exceeds the ratified budget

### Requirement: Payload And Latency Budgets

`evaluaciones` MUST stay at or under 4 MB raw and `depuracion` at or under 400 KB raw for 400
profiles; gzip SHOULD reach about 15% of the raw size (measured and recorded at parity, not asserted).
On a cache hit the p95 latency MUST be at most 50 ms for a 304 and at most 250 ms for a 200 served from
precomputed bytes (in-process, at least 50 samples).

#### Scenario: Depuración size ceiling for 400 profiles
- GIVEN a 400-profile fixture
- WHEN the `depuracion` block is serialized
- THEN it is at most 400 KB raw, and the test fails loudly one byte above

#### Scenario: Evaluaciones size ceiling
- GIVEN the largest realistic evaluaciones fixture
- WHEN it is serialized
- THEN it is at most 4 MB raw

#### Scenario: Gzip actually shrinks the payload
- GIVEN the same body identity and gzip
- WHEN their sizes are compared
- THEN gzip is smaller and the measured ratio is recorded

#### Scenario: Cache-hit latency
- GIVEN a warmed cache and at least 50 requests
- WHEN their latencies are measured in-process
- THEN p95 is ≤ 50 ms for 304 and ≤ 250 ms for 200

### Requirement: Blob Writes Are Content-Gated

The system MUST NOT write to Blob when the persisted (redacted) content is unchanged, MUST perform at
most 2 PUTs at cold start, and MUST NOT let a failed fire-and-forget write fail the request. The
reference bundle is never written by the request path.

#### Scenario: An unchanged refetch writes nothing
- GIVEN a refetch whose redacted content is identical
- WHEN the snapshot is refreshed
- THEN 0 Blob PUTs occur

#### Scenario: A changed redacted content writes exactly once
- GIVEN a refetch whose redacted content changed
- WHEN the snapshot is refreshed
- THEN exactly 1 Blob PUT occurs

#### Scenario: Cold start writes at most twice
- GIVEN a cold start with 50 requests
- WHEN they are served
- THEN at most 2 Blob PUTs occurred

#### Scenario: A failed write does not fail the request
- GIVEN the Blob PUT raises
- WHEN a request is served
- THEN the response is still 200
