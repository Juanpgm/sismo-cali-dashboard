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
