# Proposal: Survey ↔ Sticker campaign sync

## Intent

The two field campaigns (survey / sticker) share inspector groups but couple weakly: assigning a group to survey points only propagates to the *exact* twin sticker, so nearby sticker-only buildings in the same zone stay orphaned, and a completed survey does not surface its sticker task until the next `cruce_sticker.py` cron run. Field crews standing in a block get one task type, not the obvious "you are already here, pega el sticker" companion task. This closes both gaps and drops emoji chrome for SVG.

## Scope

### In Scope
- Radius sweep on group assign: propagate `grupo_id` to every `tiene_sticker=false` sticker point within `DEFAULT_MAX_RADIUS_M` of the assigned survey points, not just exact twins (no capacity cap).
- Symmetric radius desassignment: retract the same radius-swept stickers on group desassign.
- First-link-wins conflict rule preserved: only truly unassigned stickers are touched.
- Real-time sync: new in-app "Survey completado" action (`marcarSurveyHecho` in `inspector_asignaciones.py` + button on `misPuntosPlaneacion` cards) that marks survey `hecho`, finds/creates the sticker twin in `sticker_matches`, and pre-assigns it to the same `grupo_id`/`inspector_uid`/`cuadrilla_id` so it appears immediately in that inspector's sticker tab.
- Cercanos CTA labels per type ("Levantar survey" / "Pegar sticker").
- Replace 4 emoji strings in `form.js` with inline SVG.

### Out of Scope
- Matching cascade (reuse `_encontrar_twin_sticker` / `_buscar_gemelo` as-is).
- Capacity cap on radius propagation (explicit user decision).
- `reportes.json` / `inspections.json` data model.
- Survey123 capture itself (the button is a post-capture confirmation, not a replacement).

## Capabilities

### New Capabilities
- `survey-sticker-realtime-sync`: in-app survey-completion action that materializes and pre-assigns the sticker twin instantly, plus Cercanos per-type CTA labels and SVG chrome.

### Modified Capabilities
- `planeacion-asignaciones`: group assign/desassign now sweeps sticker points by radius, not only the exact twin.
- `stickers-asignacion`: `sticker_matches` docs may be created on demand by the real-time sync (seeded pending + pre-assigned), not only by the cron.

## Approach

Extend the existing `_propagar_grupo_a_stickers` / `_desasignar_grupo_de_stickers` helpers with a radius pass over pending sticker points (reuse `cluster` distance math). Add `marcarSurveyHecho` reusing `_buscar_gemelo`; on miss, insert a `sticker_matches` doc with `clave_integracion` linkage and admin-owned assignment fields copied from the survey point. Formulario: add button + CTA labels + SVG, no change to `onTomarPunto` branching.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/app/routers/planeacion_asignaciones.py` | Modified | Radius sweep in propagate/desassign helpers |
| `backend/app/routers/inspector_asignaciones.py` | Modified | New `marcarSurveyHecho` action + on-demand sticker creation |
| `formulario/js/form.js` | Modified | "Survey completado" button, CTA labels, SVG icons |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Radius sweep over-assigns a wide zone | Med | Reuse tuned `DEFAULT_MAX_RADIUS_M`; first-link-wins skips others' points |
| On-demand doc collides with cron write | Low | Deterministic `sticker_matches` id + merge-only pipeline writes (existing invariant) |
| Desassign leaves orphans | Med | Symmetric radius retract mirrors the assign sweep |

## Rollback Plan

Pure additive code. Revert the three files; existing exact-twin propagation and the `cruce_sticker.py` cron continue unchanged. Docs created on-demand remain valid `sticker_matches` rows (cron reconciles them), so no data cleanup is required.

## Dependencies

None new. Reuses existing helpers, radius constants, and `clave_integracion` scheme.

## Success Criteria

- [x] Assigning a group to survey points also assigns unassigned stickers within radius; desassign retracts them.
- [x] "Survey completado" marks the survey `hecho` and the sticker twin appears in the same inspector's sticker tab without waiting for the cron.
- [x] Missing sticker twin is created in `sticker_matches` with correct linkage and assignment fields.
- [x] Cercanos cards show per-type CTA labels; no emoji remain in `form.js`.
