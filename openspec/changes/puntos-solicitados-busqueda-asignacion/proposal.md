# Proposal: Puntos Solicitados — search prefill, card-level assignment, targeted polish

Change: `puntos-solicitados-busqueda-asignacion` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-propose
Follow-up to the functionally-complete `puntos-solicitados` change (same router/tab, ADR conventions, sole-writer invariant).

## Intent

Admins register citizen-requested inspection points by retyping data the citizen already submitted through the atencionsismo API, assignment is buried inside the detail modal, and the tab lags `evaluaciones.js` on three small affordances. This change removes the retyping (search + prefill), surfaces assignment on the list, and closes the polish gap — with no new deps and no change to the CRUD/sole-writer contract.

## Scope

### In Scope
- **F1 — "Buscar punto"**: admin-only search modal (debounced) hitting a NEW `GET /puntos-solicitados/buscar` endpoint. Results prefill the EXISTING `#ps-crear-modal` via "Usar este punto"; "Crear punto nuevo" prefills only `direccion` from the typed query. Reuses the create modal as-is.
- **F1 backend**: admin-gated search over the PII-free public `reportes.json` (direccion/barrio/comuna/lat/lng) joined server-side to requester name from the EXISTING private Firestore `puntos_contacto` collection. Returns top ~20 with the fields needed to prefill a point.
- **F2 — card-level assign**: "Asignar" button on each `listItemHtml` card opening an inline popover with the SAME inspector combobox, reusing `asignarInspector()`/`mountCombobox` unchanged. Detail-modal path stays.
- **F3 — polish**: "Descargar .xlsx" (copy `evaluaciones.js` `downloadStamp`/`loadXlsx`); active-load count badges on inspector combobox options (reuse `inspectorOptionLabel` + `.asignacion-combo-count`); `.asignacion-spinner` busy states on "Crear punto"/"Ubicar".

### Out of Scope
- No new PII artifact/Blob store — reuse existing private `puntos_contacto` (see Approach). Public `reportes.json` PII-strip is unchanged.
- No fuzzy/ML/elastic search (substring, case-insensitive). No new deps, no CSS framework.
- No change to create/edit/delete CRUD, the dual-write mirror, or the sole-writer invariant. F2 adds only a UI entry point to the existing assignment endpoint.
- No new geocoding, no formulario changes.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `puntos-solicitados`: adds the admin-only `buscar` search-prefill endpoint + private-contact join; adds a card-level assignment entry point; adds xlsx export, inspector load-count badges, and spinner busy states to the tab.

## Approach

**PII (highest-risk, resolved by reuse — no new storage):** requester name/phone are ALREADY stripped from public `reportes.json` and persisted separately to the private Firestore `puntos_contacto/atencionsismo_{registro_id}` on every refresh (`dashboard_refresh._write_contactos`). That is this repo's established private-PII pattern for exactly these fields — admin-gated behind the backend, never in the public bundle, never committed to git, never on the Blob public path. The new `buscar` endpoint (same `require_role("admin")` gate as the rest of `puntos_solicitados.py`) substring-searches the PII-free public address fields, then batch-joins `puntos_contacto` for the name on the matched top-N only. No new artifact means no new leak surface and no new PII-strip code to keep in sync.

**F2/F3** are pure frontend reuse of already-present helpers (`asignarInspector`, `mountCombobox`, `inspectorOptionLabel`, `downloadStamp`/`loadXlsx`, `.asignacion-spinner`/`.asignacion-combo-count`). The count badge needs an active-assignments-per-inspector source; whether one already exists (as in `stickers-asignacion`) is a design-phase check, not new product scope.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/app/routers/puntos_solicitados.py` | Modified | New admin-only `GET /buscar`: reportes.json substring search + `puntos_contacto` name join |
| `web/js/puntos_solicitados.js` | Modified | Buscar button+modal (F1), card Asignar popover (F2), xlsx/count/spinner (F3) |
| `web/styles.css` | Modified | Search-modal + assign-popover styling (reuse existing classes where possible) |
| `backend/tests/…` | Modified | Tests for `buscar` (match, PII-join, admin gate, no-PII-in-public-path) |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| PII (nombre/telefono) leaks into a public/search response for non-admins | Low | `require_role("admin")` on `buscar`; name comes only from private `puntos_contacto`; public `reportes.json` strip unchanged; test asserts gate + no PII without admin |
| Full scan of ~14.8k reportes per search is slow | Low | Substring filter then top-N; reportes.json read once/cached; admin-only low volume |
| Search-by-requester-name unsupported without scanning Firestore | Med | See Proposal question round — address search + name display may suffice (YAGNI) |
| Count-badge source missing for this tab | Low | Design-phase check; reuse existing aggregate if present, else small read |

## Rollback Plan

All additive, no data migration. Revert `puntos_solicitados.js`/`styles.css` (tab returns to current behavior) and the `buscar` handler in the router (existing CRUD untouched). `puntos_contacto` is written by the refresh regardless and read by nothing else here.

## Dependencies

- Existing private Firestore `puntos_contacto` populated by the refresh (already live).
- `reportes.json` available to the backend (local `web/data` and/or Blob) — already published each refresh.

## Success Criteria

- [ ] Admin searches by direccion/barrio/comuna and gets matching reportes with requester name prefilled into the existing create modal; "Crear punto nuevo" prefills only the typed dirección.
- [ ] Requester name/phone never appear in any public response or for a non-admin caller; public `reportes.json` stays PII-free (existing check passes).
- [ ] "Asignar" on a card assigns via the existing endpoint with no new assignment/lifecycle write.
- [ ] xlsx export downloads; inspector options show load-count badges; "Crear punto"/"Ubicar" show the spinner treatment.

## Proposal question round

Interactive asking was unavailable (sub-agent). One product decision needs user review before spec/design:

1. **Search-by-requester-name**: is searching BY the citizen's name required, or is searching by address (direccion/barrio/comuna) with the name shown/prefilled on results enough? Address-only search reuses the existing private `puntos_contacto` with zero new storage. Supporting name-search cleanly would justify a small new PRIVATE searchable index (address fields + name merged) written during the existing refresh to a non-public, non-git path — a minimal, additive extension. Assumption taken: **address-only search + name display/prefill** (leaner, no new artifact). Confirm or request name-search.

Assumptions to confirm: (a) reuse Firestore `puntos_contacto` as the private PII store instead of a new Blob artifact; (b) top ~20 results; (c) F2/F3 are UI-only with no backend assignment change beyond a possible read for count badges.
