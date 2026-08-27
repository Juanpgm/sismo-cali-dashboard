# Design: Planeación — flujo de asignaciones confiable (FASE 1)

## Technical Approach

Reuse existing primitives; add zero backend frameworks. Reporter contact is captured
fail-soft in `dashboard_refresh` (the ONE place that holds raw records before the PII
strip) into a **separate restricted collection**, read only by the assignee surfaces.
B4/C3 are read-model/UI additions on existing endpoints. UI/UX debt and tests are
mechanical. Grounds: `PII_FIELDS` strip (dashboard_refresh.py:77), `PIPELINE_FIELDS`
sole-writer split (planeacion_cruce.py), `_project` allowlist (integracion.py:97),
`test_sole_writer.py` allowlists.

## Architecture Decisions

### ADR-1: Restricted contact channel — sibling `puntos_contacto` collection (THE open ADR)

| Option | Read cost | PII blast radius | Sole-writer impact | Decision |
|--------|-----------|------------------|--------------------|----------|
| Merge onto `planeacion_puntos` | free (same doc) | contact rides `_doc_to_dict` → `list_puntos`/`resumen` (admin payload) + any future full-doc reader; leak prevented only by discipline+test | adds `dashboard_refresh` as 2nd writer to a CLOSED allowlist | rejected |
| **Sibling `puntos_contacto`** | +1 batched `get_all` in ONE endpoint | contact CANNOT appear in a `planeacion_puntos` doc — leak structurally impossible | new independent allowlist; `planeacion_puntos` stays closed | **CHOSEN** |

**Rationale**: HARD constraint + MANDATORY PII test → make the leak impossible by construction,
not test-guarded. `_project` is already a strict projection (safe either way), but `list_puntos`,
`resumen`, `_doc_to_dict` return full docs — a merge would carry contact into the admin panel and
every future reader. The only cost is one extra batched `get_all` in `misPuntosPlaneacion`, keyed
by doc ids it already holds — trivial. Doc id `atencionsismo_{registro_id}` (same as
`planeacion_puntos`, so the assignee endpoint reads by known ids). New allowlist
`ALLOWED_MODULES_PUNTOS_CONTACTO = {dashboard_refresh.py (write), inspector_asignaciones.py (read)}`
in `test_sole_writer.py`.

### ADR-2: Write happens in `dashboard_refresh.fetch_reportes` (fail-soft)

`planeacion_cruce` reads `reportes.json` — already PII-stripped — so it can NEVER see contact;
it stays out. In `fetch_reportes`, wrap the `day_walk` mapper in a closure that accumulates
`{registro_id, nombre_solicitante, telefono_solicitante}` from `rep` (pre-strip) while still
returning `_raw_record_mapper(rep)` unchanged → `reportes.json` stays byte-identical. After the
walk, `_write_contactos(db, contactos)` (batched `merge:true` into `puntos_contacto`) wrapped
best-effort at the call site (try/except/log, never raise — same convention as `fetch_reportes`/
`ingest_survey_cali`, the `registrar_best_effort` posture). Refresh never blocks on contact.

### ADR-3: `misPuntosPlaneacion` + formulario surface

Endpoint adds two fields: `nombre_solicitante`, `telefono_solicitante` (null when absent — fail
open, same as the survey-link fields). Read: one `db.get_all` over `puntos_contacto/{doc.id}` for
the already-merged doc ids. Formulario `buildPlaneacionCard`: add a contact line "Solicitante:
{nombre}" and, when phone present, `<a class="btn-secondary" href="tel:{telefono}">Llamar</a>`.

### ADR-4: Pico y placa UI (frontend derives Bogotá weekday; backend stays the barrier)

Small pure helper `diaPicoPlacaHoy()` in planeacion.js: `Intl.DateTimeFormat('en-US',{timeZone:
'America/Bogota',weekday:'long'})` → map English day → backend's unaccented spanish set
(`_WEEKDAY_A_DIA`). Per-grupo vehicle `<select>`: options where `v.dia_pico_placa === diaHoy` get
`disabled` + label "(pico y placa hoy)". Row chip already exists (planeacion.js:825). Backend gate
`_hoy_es_dia_pico_placa` unchanged.

### ADR-5: Auto-agrupar feedback (no backend change)

`run_auto_agrupar` already returns the created-cuadrillas list. Frontend after `autoAgrupar`:
`n>0` → `showOk("{n} cuadrilla(s) creada(s). Volver a ejecutar agrupa el siguiente lote.")`;
`n===0` → "No hay puntos pendientes sin agrupar."

### ADR-6: Playwright scaffold at repo root

`e2e/` + `playwright.config.ts` at repo root; add `@playwright/test` devDependency + `test:e2e`
script to the existing root `package.json` (formulario already uses ^1.55.0 — reuse the version).
`baseURL` defaults to the prod dashboard URL, overridable via `E2E_BASE_URL`. Auth specs
`test.skip(!process.env.E2E_ADMIN_EMAIL, "credenciales ausentes")`. Survey123 spec: `request.get`
the prefilled share URL, `maxRedirects` on (ArcGIS may 302), assert 200 + `field:codigoapp` param
intact, NO submit. Headless, CI-friendly.

### ADR-7: D2 — lazy CDN import in `israel-source.js`

Root cause: `data.js` → `israel-source.js:12` top-level `import ... from 'https://…firebase-
firestore.js'` → `node --test` on `evaluaciones.test.mjs` (imports `store` from data.js) crashes.
Fix once at the root: move that CDN import into `fetchIsraelRecords`'s body via `await import()`.
Every transitive importer (data.js, analista.js, evaluaciones.js) then loads in node; browser
behavior unchanged (same module, resolved lazily on first call).

### ADR-8: C2/C5/C6/C8 minimal

C2: extend `.sticker-field select, .sticker-field textarea` in styles.css (one-liner, reuse input
rules). C5: `run{Cuadrilla,Grupo,Vehiculo}Action` already share the `reloadFn` param (hotfix
dc4ae77/071b40f) → collapse to one `runAction(body, okMsg, reloadFn)`. C6: delete dead
line-number comments. C8: standardize on the existing `.sticker-loading`/`.sticker-error` +
`hidden`-empty pattern already used by `reload()`.

## Data Flow

    atencionsismo raw ──(pre-strip)──► puntos_contacto ──► misPuntosPlaneacion ──► formulario tel:
          │                                                  ▲
          └──_raw_record_mapper (strip)──► reportes.json ──► planeacion_cruce ──► planeacion_puntos

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| pytest unit | contact write shape, fail-soft, misPuntosPlaneacion merge | fake Firestore double |
| **pytest MANDATORY (PII)** | contact ABSENT from `reportes.json` writer, `_project`, any public payload | grep/assert `nombre_solicitante`/`telefono_solicitante` not in reportes writer output nor `_project` keys; extend `test_sole_writer.py` (puntos_contacto allowlist) |
| node pure | `diaPicoPlacaHoy` (injected date), auto-agrupar message, evaluaciones (D2 green) | `node --test` |
| Playwright | unauth smoke, admin flow, Survey123 connectivity | credential-gated, skip-when-absent |
| manual | visual C7 subtab guidance only | one pass under frontend-design |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. (Playwright is a test harness; Survey123 check is read-only HTTP.)

## Migration / Rollout

No migration. `puntos_contacto` docs are additive, read by nothing else — safe to leave or delete
on rollback. Each surface reverts independently (proposal rollback plan).

## Open Questions

- [ ] None blocking. The restricted-channel ADR (ADR-1) is resolved: sibling collection.
