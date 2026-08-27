# Proposal: Planeación — flujo de asignaciones confiable (FASE 1)

Change: `planeacion-flujo-confiable` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-propose

## Intent

The Planeación assignment flow (points → grouping → groups/vehicles/drivers → inspectors → survey →
close) grew feature by feature and now carries product gaps and UX debt that make the day-to-day
workflow hard to trust. FASE 0 production hotfixes (A2 gate, A3/C4 targeted refresh, B1 index errors,
C1 `tipo` cleanup — commits `dc4ae77`, `071b40f`) already shipped and are **out of scope here**. This
change closes the FASE 1 work: the one approved-but-unbuilt feature (reporter contact reachable by the
field crew), two visibility gaps (pico y placa, auto-agrupar feedback), UI/UX consistency, and a test
foundation (green Node suite + a Playwright E2E scaffold) so the flow stops regressing silently.

## Scope

### In Scope

- **B3 — Reporter contact (`nombre_solicitante`, `telefono_solicitante`).** Visible to EVERY member of
  the assigned group via `misPuntosPlaneacion`, and shown on the `formulario/` assigned-point card with
  a `tel:` call button. Source: RAW atencionsismo records in `dashboard_refresh.py` (~line 77) BEFORE
  the `PII_FIELDS` strip, written over a **restricted channel** (design picks: merge onto
  `planeacion_puntos` keyed by `registro_id`, or a sibling collection). Fail-soft writes reusing the
  `registrar_best_effort` pattern.
- **B4 — Pico y placa visible in UI.** Day chip in the vehicle list (partially added in FASE 0 C1) and
  the per-grupo vehicle selector disables/labels vehicles restricted TODAY. Backend stays the real barrier.
- **C3 — Auto-agrupar feedback.** Success message with N cuadrillas created + note that re-running takes
  the next top-N batch.
- **UI/UX consistency:** C2 (`.sticker-field select/textarea` CSS gap), C7 (subtab flow guidance/order:
  priorizar → grupos → vehículos/conductores → asignar → seguimiento; `frontend-design` skill loaded at
  apply), C8 (uniform loading/empty/success states), C5 (consolidate the three identical `run*Action`
  helpers), C6 (fix dead line-number comments).
- **D2 — Green Node suite.** Fix `web/js/evaluaciones.test.mjs` by lazy-importing the CDN dependency
  (`israel-source.js` chain) so `node --test "js/**/*.test.mjs"` passes completely.
- **D3 — Playwright E2E scaffold (new).** Add `@playwright/test` (devDependency + config + `e2e/`):
  (a) unauthenticated smoke (dashboard loads, login renders); (b) authenticated admin flow via
  `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD` (Firebase password provider) or storageState — crear grupo →
  refresh sin F5 → vehicle+conductor assign → auto-agrupar feedback; (c) Survey123 connectivity — fetch
  the prefilled web URL (`getEnlaceSurvey`/`SURVEY123_FORM_URL`), assert HTTP 200 + `field:codigoapp`
  param intact, NO submission. Headless-runnable; skips with a clear message when credentials are absent.

### Out of Scope

- **C9** — `initPlaneacion` split (~950 lines): deferred to its own change.
- **D1** — `formulario/` test suite: deferred.
- **Survey123 real form submission automation** — external ArcGIS system; connectivity check only.
- FASE 0 hotfixes (already shipped) and the operator index/credential steps below (no repo diff).

## Capabilities

### New Capabilities
- None. All behavior fits existing specs; no new `openspec/specs/<name>/` created.

### Modified Capabilities
- `planeacion-asignaciones`: `misPuntosPlaneacion` returns reporter contact over the restricted channel
  (never in `reportes.json`, public `listPuntos`, or `/integracion/*`); vehicle list/selector surfaces
  pico y placa restriction; auto-agrupar returns actionable created-count feedback.
- `field-form-session`: the assigned-point card exposes reporter name + `tel:` call button.

## Approach

Data path reuses the proven pattern: `dashboard_refresh` already holds RAW records before the PII strip,
so contact fields are written fail-soft to a Firestore-only restricted channel (design ADR decides the
exact collection) keyed by `registro_id` — the same posture as `sticker_matches`/`planeacion_puntos`,
never a public JSON surface. `misPuntosPlaneacion` and the formulario card read from that channel.
B4/C3 are read-model/UI additions on top of existing endpoints. UI/UX debt (C2/C5/C6/C7/C8) is
consolidated in `web/js/planeacion.js` + `web/styles.css` under the `frontend-design` skill. Tests: D2
is a lazy-import one-liner; D3 adds a self-contained, credential-gated Playwright project so CI/local
can run the flow headless without hard-coupling to secrets.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/app/jobs/dashboard_refresh.py` | Modified | Capture contact RAW pre-strip → restricted channel (fail-soft) |
| `backend/app/routers/inspector_asignaciones.py` | Modified | `misPuntosPlaneacion` returns contact fields |
| `backend/app/routers/planeacion_asignaciones.py` | Modified | Auto-agrupar created-count feedback; pico y placa surfacing data |
| `backend/app/jobs/planeacion_cruce.py` | Modified | Merge contact onto points if design places it here |
| `web/js/planeacion.js` | Modified | B4 chip/selector, C3 feedback, C5/C6/C7/C8 consolidation |
| `web/styles.css` | Modified | C2 `.sticker-field select/textarea` |
| `formulario/index.html`, `formulario/js/form.js` | Modified | Contact name + `tel:` call button on card |
| `web/js/evaluaciones.test.mjs`, `web/js/israel-source.js` | Modified | D2 lazy-import CDN |
| `package.json`, `playwright.config.*`, `e2e/` | New | D3 Playwright scaffold |
| `backend/tests/**`, `web/js/planeacion.test.mjs` | New/Modified | Cover contact channel, feedback, UI helpers |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| PII leaks to a public surface | Med | HARD constraint: contact never in `reportes.json`, public `listPuntos`, or `/integracion/*`; sole-writer test + restricted Firestore channel; design ADR names allowed surfaces |
| Restricted-channel write fails and blocks refresh | Low | Fail-soft `registrar_best_effort`; refresh never blocks on contact write |
| Playwright secrets in CI | Med | Credentials env-provided; suite skips with clear message when absent; no secrets committed |
| Survey123 connectivity flakiness | Low | Assert HTTP 200 + param only, no submission; treat as smoke, allow skip offline |
| New devDependency (`@playwright/test`) weight | Low | devDependency only; not shipped to runtime; already used in `formulario/` |

## Rollback Plan

Per surface, reverse dependency order — every step is a config/deploy revert, no data migration:
- **Frontend/formulario**: revert the `web/`/`formulario/` commits; endpoints keep working.
- **Contact channel**: revert the `dashboard_refresh`/reader diff; already-written contact docs are
  additive, read by nothing else, safe to leave or delete.
- **B4/C3**: revert endpoint read-model additions; core flow unaffected.
- **Tests**: D2 and the Playwright scaffold are additive; delete `e2e/` + the devDependency to remove.

## Dependencies

- Operator: 4 Firestore composite indexes created (see Operator steps).
- Operator: E2E admin credentials provisioned for the authenticated Playwright flow.
- Restricted-channel design decision (ADR in design phase) — not a user decision.

## Operator steps (outside the repo — no code diff)

Ordered by when they are needed. These block or invalidate parts of this change.

1. **Firestore composite indexes** (`sismo-agosto-sgred`):
   - `planeacion_puntos`: `cuadrilla_id` ASC + `estado_asignacion` ASC + `prioridad_score` DESC
     (autoAgrupar top-N; creation link already captured this session).
   - `planeacion_auditoria`: `entidad` ASC + `ts` DESC.
   - `planeacion_auditoria`: `actor_uid` ASC + `ts` DESC.
   - `planeacion_auditoria`: `entidad` ASC + `actor_uid` ASC + `ts` DESC.
2. **Provide E2E admin credentials** — set `E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD` (Firebase password
   provider, admin role) locally/CI for the authenticated Playwright flow, or a captured storageState.

## Success Criteria

- [ ] Every member of an assigned group sees reporter name + phone in `misPuntosPlaneacion`; the
      `formulario/` card shows name + a working `tel:` call button.
- [ ] Contact fields are ABSENT from `reportes.json`, public `listPuntos`, and `/integracion/*`
      (sole-writer/surface test proves it).
- [ ] Vehicle list shows the pico y placa day chip; per-grupo selector disables/labels TODAY-restricted vehicles.
- [ ] Auto-agrupar returns a success message with N cuadrillas created + next-batch note.
- [ ] `.sticker-field select/textarea` styled; loading/empty/success states uniform across subtabs;
      the three `run*Action` helpers are one; dead line-number comments removed.
- [ ] `node --test "js/**/*.test.mjs"` passes COMPLETELY (evaluaciones included).
- [ ] Playwright: unauth smoke + Survey123 connectivity pass headless; authenticated admin flow passes
      with credentials and skips with a clear message without them.

## Proposal question round

Scope is user-approved and BINDING ("do not reopen decisions"). No question round is required. One
residual choice — the exact restricted channel for contact (`planeacion_puntos` merge vs. sibling
collection) — is delegated to the **design phase**, not the user, and is the only open ADR.
