# Design: Survey ↔ Sticker campaign sync

## Technical Approach

Three edits, all additive, following patterns already in the two dispatchers:

1. **Radius sweep** — extend `_propagar_grupo_a_stickers` / `_desasignar_grupo_de_stickers` (planeacion_asignaciones.py) so one survey-point grupo assignment also grabs *nearby* free stickers, not just the exact 40 m twin. Reuses `haversine_m` + `DEFAULT_MAX_RADIUS_M` (800 m) + the existing `_sticker_twin_libre` / `consumidos` first-link-wins machinery.
2. **`marcarSurveyHecho`** — new own-uid action in inspector_asignaciones.py: mark the survey `hecho` (as `_marcar_hecho_planeacion` does) then best-effort materialize + pre-assign its sticker twin.
3. **Formulario** — "Survey completado" button on `buildPlaneacionCard`, per-type Cercanos CTA labels, emoji→inline-SVG.

## Architecture Decisions

### Two link tiers on the sweep
**Choice**: The exact twin (≤`MAX_MATCH_M` 40 m via `_encontrar_twin_sticker`) keeps the durable linkage (`clave_integracion` + `planeacion_punto_id`); radius neighbours (>40 m, ≤800 m, `_sticker_twin_libre`, not already `consumidos`) get **`grupo_id` only**.
**Alternatives**: stamp `clave_integracion` on every swept sticker.
**Rationale**: a neighbour is a *different* building — persisting the survey point's key there would fabricate a false pairing. `grupo_id` is a routing hint (same crew, same block); the pairing key is not.

### Circle-per-point, whole-collection-in-memory
**Choice**: sweep a `DEFAULT_MAX_RADIUS_M` circle around **each assigned survey point** over the once-loaded `sticker_matches` collection (~1.2 k docs).
**Alternatives**: cuadrilla bounding box; Firestore geo/bbox query.
**Rationale**: per-point mirrors `cluster_mas_denso`'s own seed-radius scan and needs no extra state. The collection is already loaded once per call (existing code); an in-memory `haversine_m` filter needs no composite/geo index at this volume.

### `marcarSurveyHecho` is fail-soft, not one transaction
**Choice**: mark the survey `hecho` first (primary, must succeed), then best-effort create/pre-assign the twin — mirroring `_propagar_grupo_a_stickers`'s own `try/except` + "sticker-side failure must NEVER fail the survey-side write" contract.
**Alternatives**: single cross-collection `@transactional` write (like `_tomar_punto`).
**Rationale**: closing the survey is the user intent; the twin is a convenience. The repo already chose fail-soft over cross-collection transactions at exactly this seam. The deterministic doc id makes a retry idempotent, so a crash between writes self-heals.

### Deterministic on-demand doc id `atencionsismo_{registro_id}`
**Choice**: created via `sticker_matches` doc id `doc_id('atencionsismo', registro_id)` — the SAME `doc_id(fuente, registro_id)` shape `cruce_sticker.py`/`planeacion_cruce.py` already use — `fuente="atencionsismo"`, `tiene_sticker=false`.
**Alternatives**: random doc id; a `survey_*` ad-hoc prefix.
**Rationale**: the cron only writes `ede_*`/`israel_*` ids from panel points (`select_candidates` never iterates survey rows), so `atencionsismo_*` is **collision-proof by construction** — the cron's merge-only invariant is preserved untouched. Deterministic ⇒ a repeat call merges, never duplicates.

## Data Flow

    asignarGrupoAPuntos ─→ _propagar_grupo_a_stickers ─→ exact twin (grupo+clave+pln_id)
         (survey pts)                    └────────────→ radius neighbours (grupo only)

    marcarSurveyHecho ─→ survey doc = hecho (must succeed)
                     └─→ best-effort: _buscar_gemelo ? pre-assign twin : create atencionsismo_{id}

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `backend/app/routers/planeacion_asignaciones.py` | Modify | Radius pass in the two twin helpers (grupo-only tier + symmetric retract) |
| `backend/app/routers/inspector_asignaciones.py` | Modify | `marcarSurveyHecho` action + on-demand sticker create/pre-assign |
| `formulario/js/form.js` | Modify | "Survey completado" button + handler; per-type CTA; 4 emoji→SVG |
| `formulario/js/logic.js` | Modify | `etiquetaAccionCercano(campana)` (pure, testable) |

## Interfaces / Contracts

Created `sticker_matches/atencionsismo_{registro_id}` doc — cruce-compatible field shape:
`fuente="atencionsismo"`, `registro_id`, `tiene_sticker=false`, `tier/sticker_dist_m=None`, `direccion`, `coords`, `zona_id` (=comuna), `matched_at=now`, `clave_integracion`, `planeacion_punto_id`, plus assignment: `estado_asignacion="asignado"`, `grupo_id`/`inspector_uid` copied from the survey point, `cuadrilla_id=null` (NEVER copied — `cuadrillas` and `planeacion_cuadrillas` are distinct id-spaces; copying would silently create a cross-collection collision, per spec), `reasignado_de=null`, `asignado_en=now`.

Request: `{action:"marcarSurveyHecho", punto_id}` → `{id, estado_asignacion:"hecho", sticker_matches_id, sticker_creado:bool}`.

Cercanos SVG helpers: Feather `map-pin` and `phone` (24×24, `stroke=currentColor`, no fill, `aria-hidden`), injected via a `<span class="icon">` before each label.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit (py) | radius neighbour gets grupo only (no clave); >800 m untouched; conflict skipped; symmetric radius retract | extend `test_planeacion_asignaciones.py` (fake-Firestore, alongside existing twin tests ~2259-2404) |
| Unit (py) | `marcarSurveyHecho`: hecho stamped; on-demand `atencionsismo_{id}` create + fields + pre-assign; twin-hit pre-assigns; idempotent repeat; 403 non-assignee; fail-soft; created id ∉ cron `ede_*`/`israel_*` namespace | new cases in `test_inspector_asignaciones.py` |
| Unit (js) | `etiquetaAccionCercano` survey/sticker/unknown | add to `logic.test.mjs` (mirrors `etiquetaCampana` tests) |
| E2E/manual | button flow + SVG render | DOM-level, out of `logic.test` scope |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. Firestore writes + DOM only.

## Migration / Rollout

No migration. Pure additive; revert the four files to restore exact-twin-only behavior. `survey_*` docs remain valid `sticker_matches` rows the cron reconciles.

## Open Questions

- [ ] `DEFAULT_MAX_RADIUS_M` (800 m) still flagged unconfirmed upstream (proposal risk "over-assigns a wide zone") — retune in one place if field feedback says too wide.
