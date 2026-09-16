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
