# Proposal: Puntos Solicitados — special-case points that flow through the existing assignment machinery

Change: `puntos-solicitados` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-propose

## Intent

Planeación today can only assign points produced by the automatic matching pipeline (EDAN/survey cross-referenced). An admin has no way to register a special-case point — a citizen request, a follow-up, a priority case — that still needs the SAME assignment machinery (grupo/cuadrilla/inspector), the SAME minted `codigoapp`, and the SAME field-formulario experience as any other point, but visually flagged as priority. This change adds that path without duplicating the assignment/codigoapp/Survey123 logic that already works.

## Scope

### In Scope

- **New admin-only tab "Puntos Solicitados"** — registered the mechanical way (`web/index.html` nav button + `data-view-panel` section + `switchView()` branch in `web/js/main.js`), gated to admin/superadmin via the existing custom-claims gate (same as Stickers/Usuarios). Structure clones `web/js/evaluaciones.js` (KPIs + filters + Leaflet map colored by `estado_seguimiento` + card list + detail modal with photos/justificación/contacto).
- **"Crear punto solicitado" modal** inside that tab. Required: `nombre`, `comuna_corregimiento`, `barrio_vereda`, `nombre_solicitante`, `telefono_solicitante`, `justificacion`. Coordinates via live geocode of a typed address OR manual lat/lng; after geocoding a small Leaflet map shows a **draggable marker** (inline note explains nudging for precision). Optional: up to 10 photos.
- **New router `backend/app/routers/puntos_solicitados.py`** (`REQUIRED_CLIENTS = ("sismo",)`, registered in `_ROUTERS`): `POST` (create, admin-only, dual-write), `GET` (list), `PATCH /{id}` (edit + `estado_seguimiento`), `DELETE /{id}`, and `POST /geocode` (new LIVE proxy to Google Geocoding using the existing `GOOGLE_MAPS_API_KEY`; auth required, key never reaches client).
- **Hybrid dual-write (Approach A)**: new `puntos_solicitados` collection (request fields) + a **mirror doc in `planeacion_puntos`** with `fuente='solicitado'`, `registro_id=<doc id>`, so the point inherits minted `codigoapp` (`clave_integracion()`), the existing grupo/cuadrilla/inspector endpoints (`planeacion_asignaciones.py`), and formulario appearance — all reused as-is.
- **Sole-writer allowlist**: add the new router to `backend/tests/invariants/test_sole_writer.py` (same pattern as `planeacion_cruce`).
- **Formulario "PRIORIDAD" flag**: add `es_solicitado` (from the mirror doc) to `buildPlaneacionCard()` (`formulario/js/form.js` ~379-421) — sorts these points first and renders a visually distinct PRIORIDAD badge with its OWN styling (not the existing alta/media/baja pill scheme).
- **Rename** Planeación's "Auto-agrupar" button to **"Crear Cluster"** — copy-only (`web/js/planeacion.js:497`, handler `:2569`); same `action:'autoAgrupar'` backend, no contract change.
- **Photos**: reuse the presigned S3 flow (`backend/app/routers/sign.py`, `MAX_SLOT`=10); only the key prefix changes (`solicitados/{id}/foto_{slot}.jpg`).

### Out of Scope

- No change to `autoAgrupar`/`planeacionAsignaciones` action names or contracts (rename is copy-only).
- No new Survey123 link logic — the mirror doc flows through `build_survey_urls()` unchanged.
- No 4th formulario picker tab — reuses the existing "Planeación" picker (`state.puntosPlaneacion` / `buildPlaneacionCard`).
- No offline/batch geocoding — the new proxy is LIVE, separate from `scripts/geocode_validate.py`.
- No new role — create/edit/delete is admin/superadmin only.

## Capabilities

### New Capabilities
- `puntos-solicitados`: admin-only registration, listing, editing, follow-up state, geocoding, and photo capture for special-case points, plus the dual-write mirror into `planeacion_puntos`.

### Modified Capabilities
- `field-form-session`: assigned-point card surfaces `es_solicitado`, sorts solicitados first, and renders a distinct PRIORIDAD badge.

## Approach

**Approach A (hybrid), chosen over a fully-separate model.** A special-case point is TWO writes in one operation: (1) `puntos_solicitados` holds request-specific data (contacto, justificación, fotos, `estado_seguimiento`, `creado_por/_en`, `clave_integracion`); (2) a mirror in `planeacion_puntos` (`fuente='solicitado'`, `registro_id`) makes the point indistinguishable from a pipeline point downstream — it inherits `codigoapp` minting, the existing assignment endpoints, and formulario rendering with zero new assignment logic. The new tab is an `evaluaciones.js` clone; the geocode proxy is the only new server capability (live Google Geocoding behind auth). Rejected **Approach B** (fully separate: own assignment fields, own codigoapp minting, own Survey123 link building, 4th picker tab) — it duplicates three working subsystems and violates the explicit requirement that the point "pase el codigoapp y todo igual como los otros puntos".

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/app/routers/puntos_solicitados.py` | New | CRUD + `/geocode`; dual-write to both collections |
| `backend/app/main.py` | Modified | Register router in `_ROUTERS` |
| `backend/tests/invariants/test_sole_writer.py` | Modified | Allowlist new router for `planeacion_puntos` |
| `web/index.html`, `web/js/main.js` | Modified | Nav button, panel section, `switchView()` branch (admin-gated) |
| `web/js/puntos_solicitados.js` | New | Tab UI cloned from `evaluaciones.js` + create modal + draggable-marker geocode |
| `web/js/planeacion.js` | Modified | Rename button copy → "Crear Cluster" |
| `formulario/js/form.js` | Modified | `es_solicitado` sort + distinct PRIORIDAD badge in `buildPlaneacionCard()` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Mirror/primary write partially fails → orphan doc | Med | Design phase picks the write order + best-effort/compensation; sole-writer test guards `planeacion_puntos` |
| Google Geocoding key leaks to client | Low | Key stays server-side in the proxy; endpoint is auth-required |
| PRIORIDAD badge clashes with existing alta/media/baja pill | Low | Distinct styling, explicitly not reusing the pill color scheme |
| Geocoding cost/quota on live calls | Low | One call per creation, admin-only; manual lat/lng fallback exists |
| Duplicate/accidental request points pollute assignment queue | Low | Admin-only creation + `estado_seguimiento` lifecycle + delete endpoint |

## Rollback Plan

Reverse dependency order, all config/deploy reverts — no data migration:
- **Frontend**: revert `web/` + `formulario/` commits; assignment endpoints keep working.
- **Router**: unregister from `_ROUTERS` and revert `puntos_solicitados.py`; `puntos_solicitados` docs are additive and read by nothing else. Existing `planeacion_puntos` mirror docs (`fuente='solicitado'`) are safe to leave or bulk-delete.
- **Rename**: revert the one copy string.

## Dependencies

- `GOOGLE_MAPS_API_KEY` present in the backend environment (already used offline by `scripts/geocode_validate.py`) — needed live for `/geocode`.
- Design-phase ADR on dual-write ordering and orphan handling (not a user decision).

## Success Criteria

- [ ] Admin/superadmin sees the "Puntos Solicitados" tab; non-admins do not.
- [ ] Creating a solicited point writes both `puntos_solicitados` and a `planeacion_puntos` mirror (`fuente='solicitado'`, `registro_id`), and the point receives a minted `codigoapp`.
- [ ] The solicited point is assignable through the EXISTING grupo/cuadrilla/inspector endpoints with no new assignment code.
- [ ] The formulario shows the point with a distinct PRIORIDAD badge and sorts it first.
- [ ] `/geocode` returns coordinates; the API key never appears in any client response; the modal marker is draggable.
- [ ] `test_sole_writer.py` passes with the new router allowlisted; up to 10 photos upload via the existing presigned flow under `solicitados/{id}/`.
- [ ] "Crear Cluster" button label shipped with the `autoAgrupar` contract unchanged.
