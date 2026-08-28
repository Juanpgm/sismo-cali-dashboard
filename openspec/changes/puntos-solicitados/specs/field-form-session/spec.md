# Delta for field-form-session

## ADDED Requirements

### Requirement: Assigned-point card surfaces solicited points as PRIORIDAD, sorted first

The inspector's assigned-point list MUST sort points carrying `es_solicitado:true` before all other
assigned points, and MUST render them with a distinct "PRIORIDAD" badge, visually separate from the
existing alta/media/baja priority pill.

#### Scenario: Mixed list sorts solicited points first with a distinct badge
- GIVEN an inspector assigned a mix of pipeline points and solicited points
- WHEN their assignment list renders
- THEN every solicited point appears before every non-solicited point, each showing a PRIORIDAD
  badge, and no solicited point shows the alta/media/baja pill in its place

#### Scenario: No solicited points falls back to unchanged ordering
- GIVEN an inspector assigned only pipeline points
- WHEN their assignment list renders
- THEN ordering and pill rendering are unchanged from current behavior
