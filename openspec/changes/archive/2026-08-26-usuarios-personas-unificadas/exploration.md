# Exploration: usuarios-personas-unificadas

Change: `usuarios-personas-unificadas` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-explore

Reconciled model (fixed with the user): inspectors = real Firebase Auth users WITH login;
conductors = person DATA RECORDS (no auth/login), assignable to vehiculos; the Usuarios creation
modal becomes the unified creation surface via a `tipo` selector; the Inspectores roster subtab moves
from Stickers to Planeación; the vehiculo form keeps `empresa` + a selector for an EXISTING conductor
(no inline-create).

## 0. Critical architecture finding — split backend

The code base is mid-migration between two live backends, and the three tabs sit on opposite sides:
- `web/js/usuarios.js` → **legacy Vercel** `api/usuarios.js` (LIVE).
- `web/js/stickers.js` → **legacy Vercel** `api/stickers.js` (LIVE).
- `web/js/planeacion.js` → **Railway/FastAPI** `backend/app/routers/planeacion_asignaciones.py` (new, no Vercel twin).
- `backend/app/routers/usuarios.py` exists but is UNWIRED and parity-incomplete (missing `setEnabled`/`setRole`) — do NOT target it; any production change must target `api/usuarios.js` + `web/js/usuarios.js`.

So the roster move + modal unification cross the Vercel/FastAPI boundary. `api-config.js` already has
`stickers` and `planeacionAsignaciones` entries, so the Usuarios modal can fan out per tipo with no
new config.

## 1. Inspector creation (api/stickers.js `createInspector`, ~105-152)
Two-system, transactional, code-allocating: validate cedula/codigo/password → email
`${cedula}@sismocali.gov.co` (the @sismocali domain IS the inspector marker for role derivation) →
`createUser` (Auth) → Firestore profile → brigade-code allocation inside a `runTransaction`
(lowest-free 3-digit, gap-filling) → rollback (delete Auth user) on any failure. The admin learns the
assigned code only from the response.

## 2. Usuario creation (api/usuarios.js `createUsuario`, ~79-89, the LIVE one)
Validate email (must contain `@`), **explicitly reject @sismocali.gov.co** ("Los inspectores se crean
desde la pestaña Stickers, no aquí" — this string must stop being true), password ≥6, `createUser`.
`ASSIGNABLE_ROLES = ['admin','usuario','viewer']`; inspector/otro derived, never hand-assigned.

## 3. Conductor creation (planeacion_asignaciones.py `crear_conductor`, ~1215-1238)
Railway/FastAPI, `require_role("admin")`; cedula (unique) + nombre_completo required; writes a plain
`conductores` doc, NO Auth account. Only current frontend caller is the vehiculo modal's inline-create
(to be removed). Driving it from the Usuarios modal → `usuarios.js` calls
`apiUrl('planeacionAsignaciones')` `{action:'crearConductor', ...}` (a second endpoint dependency).

## 4. Role system
Inspector creatable-from-Usuarios requires NO role-cascade change — `role_from` already resolves any
@sismocali.gov.co password account to `inspector`. Conductor needs NO role/claim (no Auth account).

## 5. Nav / subtabs
Stickers = 3-way (roster / evaluaciones / asignacion). Planeación = 4-way (puntos / grupos / vehiculos
/ historial). Both already admin-only via one shared CSS rule. `planeacion.js` ALREADY calls
`/api/stickers` (`callStickersApi` + cached `inspectoresCache`/`getInspectores`) to populate inspector
selects — so moving the roster subtab is mostly porting `stickers.js`'s `rosterHtml`/`rowHtml`/create-
modal/`wire()` into a new Planeación segment reusing `callStickersApi`, and shrinking Stickers to
2-way (Evaluaciones + Asignación). No backend change for the move.

## 6. a2a8eb5 verdict — REWORK, do not revert
Backend (empresa + conductor_id on vehiculos, conductor CRUD) fully reusable. Frontend: empresa input +
conductor `<select>` + `buildVehiculoPayload` reusable; OBSOLETE = the inline "Crear conductor"
fieldset + `NUEVO_CONDUCTOR` sentinel + `syncConductorNuevo` + the two-step save branch +
`buildConductorPayload`. Net deletion ~40-50 lines.

## Recommended decomposition (order)
- **Slice A** — Vehiculo form cleanup: delete the inline-create-conductor UI from `planeacion.js`
  (pure deletion, zero backend). Smallest, removes the now-conflicting UI immediately.
- **Slice B** — Move Inspectores roster Stickers→Planeación: port roster UI into a new Planeación
  segment (reuse `callStickersApi`); shrink Stickers to 2-way. No backend change.
- **Slice C** — Usuarios modal unified creation: add `tipo` selector fanning out per tipo — inspector →
  `apiUrl('stickers')` create (reuse `createInspector`), conductor → `apiUrl('planeacionAsignaciones')`
  `crearConductor`, admin/viewer/usuario → existing `apiUrl('usuarios')` create. Zero backend files if
  Open Question 1 → reuse existing endpoints (recommended). Land last.

A and B independent; C lands last (must know roster's final home).

## Open design questions (recommendations)
1. Inspector creation from Usuarios: frontend fans out to existing endpoints per tipo (**recommended**)
   vs `api/usuarios.js` duplicating `createInspector`. → fan-out (smaller, no duplicated transactional code).
2. Brigade code on Usuarios-created inspectors: **yes, identical to today's Stickers flow** (operational identity).
3. Does the Usuarios roster LIST show conductors/inspectors too? **v1: Option B** — Usuarios lists only
   Auth-backed accounts as today (inspectors already appear via the `inspector` role chip); conductors are
   creatable there but visible in Planeación's Vehículos/Conductores view. Revisit a true "personas" roster later.
4. What stays in Stickers: Evaluaciones + Asignación (both survive untouched). **Keep the tab name**; drop to 2-way.
5. a2a8eb5: **rework, don't revert** (see §6).
6. `backend/app/routers/usuarios.py` (FastAPI port): **explicitly OUT OF SCOPE** (unwired, parity-incomplete);
   flag a follow-up parity pass for when `fastapi-backend-consolidation` resumes.

## Files per slice
- A: `web/js/planeacion.js` (deletions).
- B: `web/js/stickers.js` (remove roster), `web/js/planeacion.js` (add roster, reuse `callStickersApi`).
- C: `web/js/usuarios.js` (tipo selector + fan-out). No backend files.

## Risks
- `usuarios` in api-config.js still points at legacy Vercel; target `api/usuarios.js` + `web/js/usuarios.js`, NOT the Python router.
- Usuarios modal fanning out to 3 endpoints is a new pattern for that tab — needs clear per-branch error surfacing.
