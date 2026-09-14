# Proposal: stickers-tab-asignacion-removed

## Why

The Stickers tab's "Asignación" segment (cuadrillas/inspector assignment, `web/js/stickers-asignacion.js`)
and the "Fuente" toggle (Atención Sismo/Formulario) were removed from `web/js/stickers.js` at the
user's explicit request (annotated screenshot marking both as legacy). "Operación de campo" now
renders Evaluaciones ATC-20 from Atención Sismo directly — no tabs, no toggles.

This change was implemented directly (no proposal/design/tasks cycle) and shipped in commit
`e063b14` on `main` before this spec sync — this proposal + delta exist to bring
`openspec/specs/stickers-asignacion/spec.md` back in line with what's actually deployed, per the
project's SDD/openspec artifact-store convention.

## What changes

- Remove the four requirements describing the Stickers-tab Asignación UI ("CRUD affordances in the
  frontend", "Mounted as a sub-section of the existing Stickers tab", "Table view — sortable,
  filterable by `estado_asignacion`", "Map view — 3-color legend") — that UI no longer exists,
  nowhere in the frontend.
- Update "Scope boundaries" to reflect that `api/sticker-asignaciones.js` (admin-SDK, `sticker_matches`/
  `cuadrillas`) now has zero frontend callers. It stays deployed (out of scope for this change —
  a separate decision if it should be removed too) but orphaned.
- **Not changed**: the matching pipeline itself (`cruce_sticker.py`, `sticker_matches` document
  ownership/merge-safety rules) — that keeps running independently of any UI and is still accurate
  as documented.

## Impact

- Affected spec: `stickers-asignacion` (this delta)
- Affected code (already shipped): `web/js/stickers.js`, deleted `web/js/stickers-asignacion.js` +
  its test
- Not affected: `backend/app/jobs/cruce_sticker.py`, `backend/app/routers/cruce_sticker.py`,
  `api/sticker-asignaciones.js` — explicitly out of scope, left running
