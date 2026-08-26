# Design: Usuarios/Personas unificadas

## Technical Approach

Five frontend-only slices over the legacy Vercel pair (`web/js/usuarios.js`, `web/js/stickers.js`)
and the FastAPI-backed `web/js/planeacion.js`. Zero new backend files: every creation path fans out
to an endpoint that already owns that person kind. The Usuarios modal becomes the single creation
surface via a `tipo` selector; the Inspectores roster relocates from Stickers to Planeación (reusing
the `callStickersApi` client `planeacion.js` already has); the vehiculo modal loses its inline
create-conductor UI; and three individual-assignment controls are hidden in Planeación. See
proposal decisions 1-6.

## Architecture Decisions

### ADR-1: Usuarios modal fan-out routes per-tipo from the frontend

**Choice**: `web/js/usuarios.js` adds a `tipo` selector (`admin/viewer/usuario` | `inspector` |
`conductor`). The submit handler (currently `usuarios.js:491-510`) dispatches by tipo to three
endpoints via `apiUrl()`, mirroring `planeacion.js:79-90`'s `callStickersApi` pattern:

| tipo | endpoint | body | success copy |
|---|---|---|---|
| admin/viewer/usuario | `/api/usuarios` (existing `callApi`) | `{action:'create', email, password}` | `Usuario creado: {email}` |
| inspector | `apiUrl('stickers')` (new local `callStickersApi`) | `{action:'create', cedula, nombre_completo, entidad, password}` | `Inspector creado. Código: {codigo}` |
| conductor | `apiUrl('planeacionAsignaciones')` | `buildConductorPayload({...})` | `Conductor creado. Visible en Planeación` |

**Alternatives considered**: duplicate `createInspector`'s transactional brigade-code allocation into
`api/usuarios.js` (rejected — copies Auth-create + `runTransaction` + rollback, large diff, drift risk).

**Rationale**: inspector goes straight to `api/stickers.js`, so **`api/usuarios.js` create is untouched
and its `@sismocali.gov.co` rejection (`usuarios.js:83-85`) STAYS** — it still correctly guards the
`admin/viewer/usuario` branch (proposal Q2: reject with a message that names the inspector tipo).
Field-swap: the modal shows email+password by default, swaps to cedula+nombre+entidad+password for
inspector, and nombre+cedula+email+telefono for conductor (reuse the `field()` helper at
`usuarios.js:33`). Each tipo is exactly ONE write, so there is no cross-endpoint transaction; errors
surface inline on the existing `#usuario-form-error`, prefixed by tipo so a FastAPI conductor failure
(e.g. duplicate cédula) is never confused with a Vercel inspector failure.

### ADR-2: Roster move is a UI port, backend untouched

**Choice**: port `stickers.js`'s `rowHtml`/`rosterListHtml`/`rosterHtml`/create-modal/`wire()`
(`stickers.js:35-149,249-330`) into a new Planeación segment. Reuse the `callStickersApi` +
`inspectoresCache`/`ensureInspectores` (`planeacion.js:79-90,1706-1711`) already in the module — the
create call and `setEnabled` toggle still hit `api/stickers.js` unchanged. Add a 5th subtab to
`planeacion.js`'s `shellHtml` nav (`planeacion.js:280-285`). Stickers shrinks: drop the `roster`
segment button + `data-sticker-section="roster"` wrapper (`stickers.js:167,172-174`), the
`initStickers` roster reload path, leaving a 2-way control (Evaluaciones + Asignación); default
segment becomes `evaluaciones`.

**Alternatives considered**: shared roster module imported by both tabs (rejected — v1 removes the
roster from Stickers entirely, so no second consumer; a module is speculative).

**Rationale**: the ported `wireRows` MUST carry the `finally { busy = false; btn.disabled = false; }`
reset (`stickers.js:308-313`, the F5-toggle fix from `7977fb7`) — copy it verbatim, do not
reintroduce the stuck-`busy` bug. After create, `inspectoresCache` must refresh so Planeación's own
inspector selects see the new person (call `ensureInspectores` with a forced reload or re-`list`).

### ADR-3: Vehiculo Slice A — targeted deletions, keep the payload builder

**Choice**: delete from `planeacion.js`: the `#planeacion-vehiculo-conductor-nuevo` fieldset
(`526-540`), the `NUEVO_CONDUCTOR` sentinel + its `<option>` (`1453,1461`), `syncConductorNuevo` +
its listener + call (`1464-1467,1479`), `conductorNuevoBox` ref (`1452`), the conductor-field reset
loop (`1476-1478`), and the two-step save branch (`1504,1507-1519`) — the save collapses to
`conductorId = conductorSelect.value`. **KEEP** `buildConductorPayload` (`240-248`): it is exported,
tested, and Slice C's conductor branch reuses it. The empresa input and existing-conductor `<select>`
stay.

**Rationale**: net ~40 deleted lines, zero backend. Retaining `buildConductorPayload` avoids
deleting-then-recreating it (and its test) between slices A and C.

### ADR-4: Hide individual assignment at the markup, guards already cover the rest

**Choice**: remove three control markups in `planeacion.js`; backend branches stay callable:

| Control | Remove | Dead-ref safety |
|---|---|---|
| Cuadrilla inspector combobox (`asignarInspector`) | combobox markup in `cuadrillasHtml` + mount block `1317-1332` | `querySelectorAll('[data-combo-cuadrilla]')` returns empty → no-op |
| Individual desasignar (`desasignarInspector`) | `[data-desasignar]` button in `cuadrillasHtml` + wiring `1334-1339` | empty NodeList → no-op |
| Map per-point reassign (`reasignarPunto`) | the `Reasignar a` `<label>`/`<select>` block in `popupHtml` `867-870`; drop the now-unused `reasignar` fn `1673-1684` + its `renderMap(...,reasignar)` arg `1688` | `popupopen` handler's `if (!sel) return` `916` already guards |

**Rationale**: UI-only hiding (previously approved "solo ocultar en la UI"). The map guard at
`916` means removing only the popup markup is safe; also dropping `reasignar` avoids a dangling
reference. `asignarGrupoAPuntos`/`desasignarGrupo` (group path, `1660-1671`) are untouched.

## Data Flow

    Usuarios modal ──tipo=inspector──→ apiUrl('stickers')            (Vercel, createInspector)
         │         ──tipo=conductor──→ apiUrl('planeacionAsignaciones') (FastAPI, crearConductor)
         └─────────tipo=admin/…──────→ /api/usuarios create          (Vercel, untouched)

    Planeación roster segment ──→ callStickersApi ──→ api/stickers.js (list/create/setEnabled)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `web/js/usuarios.js` | Modify | `tipo` selector + per-tipo field-swap + 3-way fan-out; local `callStickersApi` |
| `web/js/stickers.js` | Modify | Remove roster segment; shrink to 2-way; default `evaluaciones` |
| `web/js/planeacion.js` | Modify | Slice A deletions; add roster segment; hide 3 assignment controls |
| `api/usuarios.js` | Unchanged | `@sismocali` rejection stays (guards non-inspector branch) |
| `api/stickers.js`, `planeacion_asignaciones.py` | Unchanged | Reused as-is |
| `backend/app/routers/usuarios.py` | **OUT OF SCOPE** | Unwired FastAPI port; changing it does nothing in production |

## Interfaces / Contracts

New pure helper in `usuarios.js`, unit-testable without DOM:

```js
// tipo → {endpoint, body} for the fan-out; throws on @sismocali under a non-inspector tipo.
export function payloadForTipo(tipo, fields) { /* returns {endpoint, body} */ }
```

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `payloadForTipo` routing + @sismocali guard; `buildConductorPayload`/`buildVehiculoPayload` (existing); ported `rowHtml`/`filterInspectores` | `.test.mjs` pure assertions |
| Integration | fan-out per-branch error surfacing; roster create refreshing `inspectoresCache` | Hard to unit-test (DOM + 2 live backends) — **honestly out of automated scope**; manual smoke per tipo |
| E2E | segment toggle, combobox removal, modal wiring | Manual smoke only; UI-heavy, no harness exists |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. Pure client-side endpoint fan-out over existing authenticated APIs.

## Migration / Rollout

No data migration. Deliver as chained slices A → B → C (C last; it needs the roster's final home).
A and B are independent. Each slice is under the single-PR budget.

## Open Questions

- [ ] Confirm proposal Q1-Q4 assumptions at the tasks gate (conductor error UX, @sismocali reject copy,
      conductor toast, group-only sufficiency) — all default to the stated assumptions if unanswered.
