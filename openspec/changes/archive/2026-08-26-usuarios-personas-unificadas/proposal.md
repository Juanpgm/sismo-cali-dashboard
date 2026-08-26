# Proposal: Usuarios/Personas unificadas — creación única y roster de inspectores en Planeación

Change: `usuarios-personas-unificadas` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-propose

## Why

The dashboard has three overlapping ways to create a "person", split across three tabs and two live
backends, and none of them agrees on what a person is:

- **Inspectors** are created from **Stickers** (`api/stickers.js` `createInspector`) — real Firebase
  Auth accounts WITH login, `@sismocali.gov.co` email as the role marker, brigade-code allocation
  inside a transaction.
- **Admins/viewers/usuarios** are created from **Usuarios** (`api/usuarios.js` `createUsuario`) —
  Auth accounts that **explicitly reject** `@sismocali.gov.co` with the message *"Los inspectores se
  crean desde la pestaña Stickers, no aquí"*. That guard is the friction: the operator is told to go
  to a different tab to make a different kind of person.
- **Conductors** are created inline from the **Planeación** vehiculo modal (`crearConductor`, shipped
  transitionally in `a2a8eb5`) — person DATA RECORDS with NO login, buried inside a form whose job is
  to describe a vehicle, not to manage people.

So today "create a person" means: know which of three tabs owns which kind, accept that one tab
actively refuses the kind another tab owns, and create a conductor as a side effect of editing a
truck. The user has approved a reconciled model that collapses the creation surface without collapsing
the identity distinction: **inspectors = Auth users with login; conductors = data records without
login**, and the **Usuarios creation modal becomes the single creation surface** via a `tipo`
selector, while the **Inspectores roster moves next to where inspectors are actually assigned
(Planeación)**.

## What Changes (v1, in scope)

Three UI slices plus two Planeación-UI cleanups. **Zero backend files** — every creation path reuses
an endpoint that already exists.

- **Slice A — Vehiculo modal cleanup** (`web/js/planeacion.js`): delete the obsolete inline
  create-conductor UI (the "Crear conductor" fieldset, `NUEVO_CONDUCTOR` sentinel, `syncConductorNuevo`,
  the two-step save branch, `buildConductorPayload`). The `empresa` input and the existing-conductor
  `<select>` **stay**. Pure deletion (~40-50 lines), zero backend.
- **Slice B — Move the Inspectores roster Stickers → Planeación** (`web/js/stickers.js` removal,
  `web/js/planeacion.js` addition): port `rosterHtml`/`rowHtml`/create-modal/`wire()` into a new
  Planeación segment, reusing the `callStickersApi` client `planeacion.js` **already** has for inspector
  selects. Shrink Stickers to a 2-way segmented control (Evaluaciones + Asignación). No backend change —
  the roster still reads/writes `api/stickers.js`.
- **Slice C — Unified creation in the Usuarios modal** (`web/js/usuarios.js`): add a `tipo` selector
  (inspector | conductor | admin/viewer/usuario) that **fans out to existing endpoints per tipo** —
  inspector → `apiUrl('stickers')` create, conductor → `apiUrl('planeacionAsignaciones')`
  `crearConductor`, others → `apiUrl('usuarios')` create. Lands last (must know the roster's final
  home). The `api/usuarios.js` guard string that rejects `@sismocali.gov.co` and points the user at
  Stickers becomes obsolete and is removed on this path.
- **Hide individual inspector assignment in Planeación (group-only UI)** (`web/js/planeacion.js`):
  hide the cuadrilla inspector combobox (`asignarInspector`), the map per-point reassign select
  (`reasignarPunto`), and the individual desasignar button (`desasignarInspector`). **Backend branches
  stay** — this is UI-only hiding, previously approved as *"solo ocultar en la UI"*.

## Explicitly Out of Scope

- **`backend/app/routers/usuarios.py` (unwired FastAPI port).** It is not routed into production, is
  parity-incomplete (missing `setEnabled`/`setRole`), and changing it would silently NOT affect the
  live dashboard. All production changes target the legacy Vercel pair `api/usuarios.js` +
  `web/js/usuarios.js`. A follow-up parity pass is flagged for when `fastapi-backend-consolidation`
  resumes.
- **No backend duplication of `createInspector`.** The Usuarios modal calls the existing Stickers
  endpoint; the transactional Auth-create + brigade-code allocation is NOT copied into `api/usuarios.js`.
- **No true "personas" superset roster in Usuarios (v1).** The Usuarios LIST keeps listing only
  Auth-backed accounts as it does today (inspectors already appear via the `inspector` role chip).
  Conductors are creatable from the modal but remain visible in Planeación's Vehículos/Conductores
  view — not in a merged personas list. A unified roster is a later revisit.
- **No rename of the Stickers tab.** It keeps its name; only its segmented control drops from 3-way to
  2-way.
- **No revert of `a2a8eb5`.** Its backend (empresa + `conductor_id` on vehiculos, conductor CRUD) and
  the reusable frontend selector stay. Only the inline-create fieldset + handler branch are deleted.
- **No role-cascade / custom-claim change.** `role_from` already resolves any `@sismocali.gov.co`
  password account to `inspector`; conductors have no Auth account and need no role/claim.
- **No removal of individual-assignment backend logic.** The `asignarInspector` /
  `reasignarPunto` / `desasignarInspector` backend branches remain callable; only their UI controls
  are hidden.

## Key Decisions Made Here (exploration recommendations adopted as decisions — veto at the tasks gate)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Inspector creation from Usuarios | **Frontend fans out to existing endpoints per tipo** (inspector→`stickers`, conductor→`planeacionAsignaciones`, others→`usuarios`) | Smaller diff, no duplicated transactional/code-allocation logic in `api/usuarios.js` |
| 2 | Brigade code on Usuarios-created inspectors | **Yes — identical to today's Stickers flow** (reuse `createInspector`) | Operational identity: an inspector is an inspector regardless of which modal created it |
| 3 | Does the Usuarios LIST show conductors/inspectors? | **v1: Auth-backed accounts only** (inspectors via role chip); conductors visible in Planeación | Avoids building a cross-backend personas roster now; the merged list is a later revisit |
| 4 | What stays in Stickers | **Evaluaciones + Asignación**, tab keeps its name, 2-way control | Both survive untouched; only the roster leaves |
| 5 | `a2a8eb5` disposition | **Rework, not revert** — delete only the inline-create fieldset + handler branch | Backend + conductor selector are reusable; a revert would also drop `empresa`/`conductor_id` |
| 6 | `backend/app/routers/usuarios.py` | **Explicitly out of scope**; follow-up parity pass flagged | Unwired + parity-incomplete; changing it would silently not affect production |

## Impact

Touched surfaces (all frontend + legacy Vercel; no FastAPI, no new backend file):

- **`web/js/planeacion.js`** — Slice A deletions, Slice B roster segment addition (reuse
  `callStickersApi`), and the group-only UI hiding of `asignarInspector` / `reasignarPunto` /
  `desasignarInspector`.
- **`web/js/stickers.js`** — remove the roster segment; shrink segmented control to 2-way.
- **`web/js/usuarios.js`** — `tipo` selector + per-tipo fan-out; drop the "create inspectors in
  Stickers" guard on this path.
- **`api/usuarios.js`** (legacy Vercel) — remove/relax the `@sismocali.gov.co` rejection copy only if a
  reused-endpoint fan-out requires it; no new action. (Confirm at design: the fan-out routes inspectors
  to `api/stickers.js`, so `api/usuarios.js` may not need to accept `@sismocali` at all.)
- **UI copy** — neutral Spanish infinitive (`Crear`, `Seleccionar conductor`, `Tipo`), no voseo.
- **No `api-config.js` change** — `stickers` and `planeacionAsignaciones` entries already exist.
- **No index.html tab/section change** for the roster move beyond relocating the segment markup between
  the two existing tabs (confirm exact DOM ownership at design).

## Risks & Open Questions

1. **KEY ARCHITECTURAL RISK — the Vercel/FastAPI split.** Usuarios and Stickers are on the **legacy
   Vercel** backend (`api/usuarios.js`, `api/stickers.js`); Planeación conductor-create is on
   **Railway/FastAPI** (`planeacion_asignaciones.py`). The unified Usuarios modal therefore fans out
   across **two different backends** from one form. Mitigation: fan out per tipo to the endpoint that
   already owns that person kind, and surface per-branch errors distinctly so a conductor-create failure
   on FastAPI is not confused with an inspector-create failure on Vercel. Do **not** target
   `backend/app/routers/usuarios.py` — it is unwired and a change there would look done but do nothing
   in production.
2. **Three-endpoint fan-out is a new pattern for the Usuarios tab.** It needs clear per-branch error
   surfacing and no partial-success ambiguity (each tipo is a single call, so there is no cross-endpoint
   transaction to coordinate — confirm at design that no tipo does more than one write).
3. **Roster move must preserve brigade-code allocation semantics.** The moved roster still calls
   `api/stickers.js`; verify at spec/design that `createInspector`'s transactional code allocation and
   Auth rollback are unchanged by the relocation (it is a UI port, not a logic change).
4. **Hiding vs. removing individual assignment.** Backend branches stay callable; if any other UI path
   or saved state still triggers them, hiding the controls must not leave orphaned in-flight individual
   assignments. Confirm no code path other than the hidden controls invokes them.

## Rough size

Five UI work units, all frontend, no backend file: (A) vehiculo cleanup, (B) roster move, (C) unified
modal, plus the group-only hiding as part of (A)/planeacion work. A and B are independent; C lands last.
Likely under the single-PR budget per slice; plan chained/stacked delivery confirmed at the tasks gate.

---

## Proposal question round

The SDD interactive contract calls for a product-question round before lock-in. This executor cannot
prompt directly, so the questions and the assumptions they would validate are recorded here. The scope
above was **already reconciled and approved by the user**; these confirm the remaining product edges.
Leaving them unanswered ships the stated defaults.

- **Q1 — Conductor create error UX across backends.** When the Usuarios modal creates a conductor
  (FastAPI) and it fails (e.g. duplicate cedula), should the modal stay open with the FastAPI error
  inline, same as an inspector/usuario failure? *Assumption:* yes — one modal, per-tipo inline error,
  no tipo-specific dead-ends.
- **Q2 — `@sismocali.gov.co` typed under the "admin/viewer/usuario" tipo.** If an operator picks a
  non-inspector tipo but types a `@sismocali` email, do we reject (as today) or auto-suggest switching
  tipo to inspector? *Assumption:* reject with a message that names the inspector tipo, since the
  domain is the role marker.
- **Q3 — Conductor visibility after creation from Usuarios.** v1 keeps conductors out of the Usuarios
  list (visible only in Planeación). Is a confirmation toast with a link to Planeación enough, or does
  the operator expect the just-created conductor to appear somewhere in Usuarios? *Assumption:* toast +
  "visible en Planeación", no Usuarios list entry in v1.
- **Q4 — Group-only assignment.** Hiding `asignarInspector`/`reasignarPunto`/`desasignarInspector`
  leaves group assignment as the only UI path. Confirm there is no operational need to reassign a single
  point outside a cuadrilla in v1. *Assumption:* group-only is sufficient; individual reassignment stays
  backend-only for later re-exposure if asked.

Ask for a second round if any answer changes the shape rather than just a constant or a copy string.
