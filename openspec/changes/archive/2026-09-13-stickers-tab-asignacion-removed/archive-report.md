# Archive Report: stickers-tab-asignacion-removed

Change: `stickers-tab-asignacion-removed` · Project: seismic_disaster_data_analisys_cali
Archived: 2026-09-13 · Artifact store: openspec

## Context

Implementation shipped directly (no `/sdd-new` cycle) in commit `e063b14` on `main`, at the user's
explicit request to eliminate the Stickers tab's Asignación segment and the Fuente
(Atención Sismo/Formulario) toggle. This report + its delta spec exist to sync
`openspec/specs/stickers-asignacion/spec.md` with what's actually deployed, per this project's
openspec artifact-store convention (there is no separate tasks.md/verify-report.md — no code was
written as part of this change, only spec text).

## Verification before merge

- Confirmed `api/sticker-asignaciones.js`, `backend/app/jobs/cruce_sticker.py`, and
  `backend/app/routers/cruce_sticker.py` are still wired into `backend/app/main.py` and still have
  passing tests (`test_cruce_sticker.py`, `test_sticker_asignaciones.py`, `test_sole_writer.py`) —
  the matching pipeline and its CRUD endpoint are alive, just UI-less.
- Confirmed `web/js/planeacion.js`'s own `autoAgrupar`/`crearCuadrilla`/`asignarInspector` calls hit
  a *different* endpoint (`planeacionAsignaciones`, Survey Cali/EDAN domain) than
  `stickerAsignaciones` (`/api/sticker-asignaciones`) — not a replacement UI for this spec's CRUD,
  a structurally cloned but functionally separate system. Correction from an earlier session
  statement ("this spec describes a feature that no longer exists") — only the frontend-mounting
  requirements were dead, the backend requirements are still accurate.
- Confirmed no other importer of the deleted `web/js/stickers-asignacion.js` exists, and no
  `?tab=asignacion`-style deep link into the removed segment exists anywhere in `web/`.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| `stickers-asignacion` | Updated | REMOVED "CRUD affordances in the frontend", "Mounted as a sub-section of the existing Stickers tab", "Table view — sortable, filterable by `estado_asignacion`", "Map view — 3-color legend" (all four described the deleted Stickers-tab Asignación UI); MODIFIED "Scope boundaries" (documents the endpoint now has zero frontend callers, and that Planeación's own CRUD is a separate system); MODIFIED "Purpose" (splits the still-live matching pipeline from the now-removed UI, with a pointer to this change). |

## Reconciliation notes

- Per this repo's house style (established in `2026-08-26-usuarios-personas-unificadas`'s archive
  report), `(Previously: ...)` transitional annotations are omitted from the merged main spec — the
  main spec is a current-state snapshot, not a change log. This delta used `**Reason**`/`**Migration**`
  markers on REMOVED requirements instead (no prior art for REMOVED in this repo; followed standard
  OpenSpec delta convention).
- All ten remaining requirements in the live spec (`sticker_matches` document ownership, the
  matching cascade, `cuadrillas` document shape, the six CRUD-endpoint contract requirements, and
  Scope boundaries) are backend/API-only and were left untouched — none of them assert anything
  about a frontend mount point.

## Archive Contents

- `proposal.md` ✅
- `specs/stickers-asignacion/spec.md` ✅ (delta, preserved verbatim)
- No `exploration.md`/`design.md`/`tasks.md`/`verify-report.md` — this change had no code phase,
  only a retroactive spec sync (see Context above).
