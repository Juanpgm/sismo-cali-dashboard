# Proposal: Planeación — cruce Survey Cali ↔ API y asignación de levantamientos

Change: `planeacion-asignaciones` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-propose

## Why

The city has ~14.8k damage reports in the atencionsismo API and ~1.1k EDAN surveys in `survey_cali`.
Nobody in the dashboard can answer the one question that drives every field day: **which reported
points still have no survey, and which of those should be visited first?**

Today that answer exists in exactly one place — `integracion_F1/cruce_criticos_survey.py`, a legacy
job that writes to the `dagma-85aad` Firestore project, which the user has explicitly ruled out of
the consolidated backend ("no usar nada relacionado con el dagma", binding directive 2026-08-25,
`openspec/changes/fastapi-backend-consolidation/proposal.md:109-118`). So operationally the answer
is unreachable: there is no recurring cross-reference in `sismo-agosto-sgred`, no prioritized
pending list, no way to hand pending points to a professional, and — critically — **no way to tell
which point a returned survey belongs to.** A survey comes back from Survey123 with an address and a
GPS fix and the office has to re-derive, by hand and by proximity, which report it answered.

There is a second cost the operator is paying quietly. The Survey123 layer already ships a
purpose-built field for exactly this, **`codigoapp` ("Codigo generado aplicativo")** — and it is
empty on all 1091 published records, because the dashboard pipeline silently drops the column before
it ever reaches Firestore (`scripts/refresh_data.py:1111`'s allowlist). The traceability mechanism is
already built into the form; it has just never been wired up.

The sticker campaign already proved the shape of the solution — cron cross-reference → lean
Firestore collection → admin-gated action endpoint → guided assignment UI. This change applies that
same proven shape to the survey campaign, and adds the two things it lacks: an explicit priority
ranking of the pending pool, and a round-trip integration key.

## What Changes (v1, in scope)

- **`codigoapp` reaches Firestore.** One-line-class fix in `scripts/refresh_data.py`'s
  `LAYER_TO_RAW` so the field survives the column allowlist, flows through `normalize()` into
  `inspections.json`, and gets ingested into `survey_cali` by the existing
  `ingest_survey_cali()` path. **This is the load-bearing task** — everything else in this change is
  cosmetic without it.
- **New Firestore collection `planeacion_puntos`** — one lean doc per atencionsismo report, refreshed
  recurringly. Carries the cruce result (`tiene_survey`, `survey_globalid`, `match_via`,
  `match_dist_m`, `tier`), the report's own triage attributes, a computed `prioridad_score` /
  `prioridad`, and the minted `clave_integracion` — plus an admin-owned assignment sub-state the
  pipeline never overwrites.
- **New Firestore collection `planeacion_cuadrillas`** — groups of pending points, each optionally
  linked to one inspector (`origen: auto|manual`). Deliberately its OWN collection, not the sticker
  campaign's `cuadrillas`.
- **New `clave_integracion`** — a deterministic, URL-safe, checksummed key minted per point
  (`PLN-<registro_id>-<hash8>`), prefilled into the Survey123 form's `codigoapp` question so the
  returned survey identifies its own point with no guessing.
- **New cron job `backend/app/jobs/planeacion_cruce.py`** — the cross-reference, structured exactly
  like `app/jobs/cruce_sticker.py`: five-rung cascade with **exact `clave_integracion` first**, then
  proximity, address, combined, miss; watermarked and incremental; batched `merge:true` writes over
  the pipeline-owned field subset only.
- **New admin-only endpoint `POST /planeacion-asignaciones`** — the 10 actions
  `sticker_asignaciones.py` already exposes, plus three the user's "posibilidad de edición o
  corrección" requires: `editarAsignacion` (correct one point's assignment fields directly),
  `marcarNoAplica` (remove a point from the pending pool with a mandatory reason, reversible), and
  `getEnlaceSurvey` (server-built prefilled Survey123 URL), plus `resumen` (aggregate tallies).
- **New top-level admin tab "Planeación"** — sibling to Stickers, `web/js/planeacion.js`, cloning
  `stickers-asignacion.js`'s 3-step guided flow (Priorizar → Cuadrillas e inspectores → Puntos) with
  a priority-ordered table, a Leaflet map, and the correction/link affordances.

## Explicitly Out of Scope

- **No writes to `survey_cali`.** Read-only. `apply_mutation()` is never called from any module this
  change adds. A survey's content is the field crew's and the CRUD router's business, not the
  planner's.
- **No writes to ArcGIS/Survey123.** This change *builds a URL*. It never POSTs a feature, never
  edits the layer, never touches `applyEdits`. The form's own submission path is unchanged.
- **No dagma anything.** No `dagma-85aad` project id, no `cruce_criticos_survey` collection name, no
  `integracion_F1` credential resolution, no import from the legacy job — anywhere in `backend/`.
  The legacy cascade is read as conceptual reference only.
- **No changes to the sticker campaign.** `sticker_matches`, `cuadrillas`,
  `/sticker-asignaciones`, and the Stickers tab are untouched. Two campaigns, two collections, two
  endpoints, zero shared state.
- **No new inspector-roster CRUD.** Inspectors already exist in `inspectores/{uid}`; this change
  only reads that roster.
- **No survey-content prefill beyond the key.** `codigoapp` only. Not `direccion`, not `comuna`, not
  `nombre_edif` — prefilling content fields would fight the form's own cascading logic and make the
  survey's provenance ambiguous ("did the inspector observe this, or did we type it for them?").
  Deferred to a Phase 2 if the field crews actually ask.
- **No route optimization.** `autoAgrupar` groups by proximity; it does not order a visit route
  inside a cuadrilla, estimate travel time, or balance workload across inspectors by capacity.
- **No historical audit trail** beyond a one-hop `reasignado_de` breadcrumb plus `editado_en` /
  `editado_por` stamps. A full who-changed-what-when log is a Phase 2 if the operator asks.
- **No public Firestore read rule** for either new collection — reachable only through the
  admin-gated endpoint, same posture as `sticker_matches`/`cuadrillas`.

## Key Decisions Made Here (rationale in design.md)

| Decision | Choice | Why |
|---|---|---|
| Collection names | `planeacion_puntos`, `planeacion_cuadrillas` | Feature-prefixed so both are obviously one campaign's; cannot collide with the sticker campaign's own point universe or grouping docs |
| Integration key | `PLN-<registro_id>-<sha256(fuente:registro_id)[:8]>` | Deterministic (no persist-then-read), URL-safe charset (`A-Z0-9-`, no percent-encoding), ~33 chars ≪ 255, checksum catches transcription damage, and the raw id is recoverable by parsing — no lookup table needed |
| Cascade order | **exact key** → proximity ≤ 40 m → address ≥ 0.90 → combined (≤ 100 m AND ≥ 0.80) → miss | The exact rung matches nothing on the first run, and everything afterwards; the fuzzy rungs are the bootstrap and the safety net for surveys filled outside the assignment flow |
| Priority default | `afectacion` (0-50) + `estadoVerificacion` (0-30) + saturating age since `fechaCreacion` (0-20) → `alta`/`media`/`baja` | Severity is what an EDAN survey exists to triage; verification state separates confirmed damage from unverified citizen reports; age prevents starvation but is capped so it can never outrank severity. **Weight table needs operator confirmation — see risk 1** |
| Comuna clustering | NOT a priority input | Geography is a *routing* concern (`autoAgrupar` already handles it), not a *priority* concern; folding it in would let a dense comuna silently outrank a severe building |
| API endpoint | `informe/json` (via `reportes.json`), not `visitados-criticos` | The user asked for the *total*; `informe/json` is the only atencionsismo client ported to `backend/`. Tradeoff: no ArcGIS GlobalID → no legacy step-1 match. Accepted, because `codigoapp` is strictly better (works for all points, not just the critical subset) |
| Survey123 form URL | Env var, never hardcoded | The form share URL / item id does not exist anywhere in this repo; it must come from the ArcGIS org admin |

## Impact

New / touched surfaces:

- **New `backend/app/jobs/planeacion_cruce.py`** + a new Railway cron service (same image, new
  `startCommand`).
- **New `backend/app/routers/planeacion_asignaciones.py`** + entries in `app/main.py`'s router
  imports and `_ROUTERS` tuple.
- **New `backend/app/services/survey_link.py`** — the prefilled-URL builder, pure + config-driven.
- **`backend/app/config.py`** — two new settings (`SURVEY123_FORM_URL`,
  `SURVEY123_FIELD_APP_ITEM_ID`).
- **`backend/tests/invariants/test_sole_writer.py`** — two new independent allowlists +
  two new test functions for the two new collection literals.
- **`scripts/refresh_data.py`** — `LAYER_TO_RAW` gains `codigoapp`.
- **`web/index.html`** — one new `.view-tabs` button + one new `<section id="view-planeacion">`.
- **`web/js/main.js`** — one new `switchView()` branch.
- **`web/styles.css:1559-1564`** — the new tab MUST join the admin-only display:none selector list.
- **`web/js/api-config.js`** — new `planeacionAsignaciones` entry.
- **New `web/js/planeacion.js`** (+ `web/js/planeacion.test.mjs` self-check for the pure helpers).
- **Firestore console (`sismo-agosto-sgred`)** — two new collections, two composite indexes, and
  rules denying all client access to both.
- **ArcGIS org (outside this repo)** — the `codigoapp` question must be present and prefillable in
  the published form.

## Manual operator steps (outside the repo — nothing here is a code diff)

These block or invalidate parts of this change and cannot be automated from the repo. Ordered by
when they are needed.

1. ~~**Get the Survey123 web form share URL**~~ — **RESOLVED, no operator action needed.** Located and
   verified live (2026-08-26) without ArcGIS admin involvement: `74aeda67b10b4725bb47e7b20ae6a2bf`
   is the *Feature Service* item ("Matriz EDE Cali", owner `juanp.gzmz.sgred`, public), NOT the form.
   The **Form item is `082c0446a4334038b3f8e677bcc27074`** (`type: Form`, `typeKeywords: [Form,
   xForm]`, `access: public`), found via
   `GET https://www.arcgis.com/sharing/rest/search?q=owner:juanp.gzmz.sgred+AND+type:"Form"`.
   Both of these return **HTTP 200** against the live public endpoint:
   - `https://survey123.arcgis.com/share/082c0446a4334038b3f8e677bcc27074`
   - `https://survey123.arcgis.com/share/082c0446a4334038b3f8e677bcc27074?field:codigoapp=TEST123`

   So `SURVEY123_FORM_URL` = `https://survey123.arcgis.com/share/082c0446a4334038b3f8e677bcc27074`
   and `SURVEY123_FIELD_APP_ITEM_ID` = `082c0446a4334038b3f8e677bcc27074`. Step 4 below is now a
   copy-paste, not a discovery task.

   **Caveat that keeps step 2 mandatory**: a 200 on the share URL only proves the form loads and the
   query string is accepted — Survey123 silently ignores `field:<name>` for a name that is not a
   question. The form definition (`/sharing/rest/content/items/<id>/data`) returned 0 bytes
   unauthenticated, so whether `codigoapp` is a real question could NOT be verified from outside.
   That single visual check is step 2 and remains the one true operator gate.
2. **Verify the `codigoapp` question in the PUBLISHED form**: it must exist in the form (not just in
   the layer schema), its `name` must be exactly `codigoapp` (the URL parameter is
   `field:codigoapp`, matched by question name — a renamed question silently ignores the prefill),
   and it must not be excluded from the form. Recommended: set it **read-only** so a field crew
   cannot overwrite or blank the key by accident.
3. **Republish the form** if step 2 required a change, then confirm `codigoapp` is still present in
   the FeatureServer layer schema and still accepts values from a test submission.
4. ~~**Provision env vars on the Railway `web` service**~~ — **DONE 2026-08-26, no operator action
   needed.** Both are already set on the Railway `web` service via the platform API, with the values
   resolved in step 1:
   `SURVEY123_FORM_URL = https://survey123.arcgis.com/share/082c0446a4334038b3f8e677bcc27074`,
   `SURVEY123_FIELD_APP_ITEM_ID = 082c0446a4334038b3f8e677bcc27074`.
5. **Create the Railway cron service `planeacion-cruce`**: same repo/Dockerfile as the other backend
   services, `startCommand: python -m app.jobs.planeacion_cruce`, hourly `cronSchedule`. Provision
   `FIREBASE_SERVICE_ACCOUNT_JSON` and `REPORTES_URL` (the Vercel Blob URL for `data/reportes.json`,
   since the container image has no `web/`).
6. ~~**Create two Firestore composite indexes**~~ — **DONE 2026-08-26, no operator action needed.**
   Created via `gcloud firestore indexes composite create --project=sismo-agosto-sgred`. Note the
   FIRST one differs from what this proposal originally predicted: a live `listPuntos` call returned
   `400 The query requires an index` naming **(`tiene_survey` ASC, `prioridad_score` DESC)** — the
   `estado_asignacion` leg was never part of the query, because `list_puntos()` deliberately filters
   `estado_asignacion` in code rather than in Firestore (only one inequality field per query is
   permitted, and it would conflict with ordering by `prioridad_score`). The index actually built is
   the one the real query asked for; the other two are as originally listed:
   - `planeacion_puntos` (`tiene_survey` ASC, `prioridad_score` DESC) — **READY**, unblocks `listPuntos`
   - `planeacion_puntos` (`estado_asignacion` ASC, `cuadrilla_id` ASC)
   - `planeacion_cuadrillas` (`inspector_uid` ASC, `origen` ASC)

   Worth recording: this gap was invisible to 370 unit tests (which use an in-memory fake Firestore
   with no index concept) and surfaced only on the first real HTTP call against live Firestore.
7. **Add Firestore security rules** in the `sismo-agosto-sgred` console denying ALL client reads and
   writes for `planeacion_puntos` and `planeacion_cuadrillas` (server/admin-SDK only), mirroring the
   existing posture for `sticker_matches` / `cuadrillas` / `evaluaciones`. There is no repo file that
   governs this project's deployed ruleset — `integracion_F1/firestore.rules` belongs to a different
   project.
8. **Confirm the priority weight table and the cron cadence** with the operations lead before the
   weights are locked (risk 1). Specifically: the ranking of the live `afectacion` and
   `estadoVerificacion` category values, and whether hourly is the right freshness.
9. **After the first cron run, spot-check one point end to end**: open its `getEnlaceSurvey` link,
   submit a test survey, and confirm that within one `dashboard-refresh` cycle plus one
   `planeacion-cruce` cycle the point flips to `tiene_survey: true` with `match_via: 'clave'`.
10. **Verify (no action expected)** that the dashboard origin is already in
    `CORS_ALLOW_ORIGINS` — it is (`https://sismo-cali-dashboard.vercel.app`,
    `backend/app/config.py:9`), so the new endpoint needs no CORS change.

## Risks & Open Questions

1. **The priority weight table is a genuine product decision, not a technical one.** The proposed
   default (severity ≫ verification state ≫ age, with comuna deliberately excluded) is defensible and
   deterministic, but the *specific* weight per live `afectacion` / `estadoVerificacion` category
   value is the operations lead's call, not the engineer's. Mitigation: weights ship as named module
   constants with a documented fallback for unknown/new category values, plus a per-point
   admin-owned `prioridad_override` that the pipeline never touches — so a wrong default is a
   one-line retune, and a wrong *individual* ranking is a one-click correction. **Blocking for
   lock-in, not for implementation.**
2. **The `codigoapp` chain has three links outside this repo and any one silently breaks it.** The
   field must exist in the published form (not just the layer), the question name must match, and the
   repo fix must ship. If the pipeline fix lands but the form question is missing, every returned
   survey arrives with an empty key and the cascade silently falls back to fuzzy matching — the
   feature looks like it works and is quietly degraded. Mitigation: the cascade *reports* which rung
   matched (`match_via`), and the UI surfaces a "surveys matched by key vs. by proximity" tally, so
   the degradation is visible on day one instead of discovered in a month.
3. **Scale: ~14.8k points vs. the sticker campaign's ~1.1k.** Shipping the whole collection to the
   browser and rendering it as Leaflet circle markers is a real performance cliff, not a hypothetical
   one. Mitigation (design.md ADR-4): `listPuntos` is a *bounded* query (default: pending +
   not-excluded, ordered by `prioridad_score` desc, `limit` 2000), a separate `resumen` action serves
   the KPI tallies, and the map renders only the returned working set. This is the main structural
   departure from the sticker precedent.
4. **One-time hash-gate churn after the `codigoapp` fix.** Adding the field changes
   `canonical_hash()`'s input for every `survey_cali` record. For records where `codigoapp` is empty,
   the hash differs but `diff_upstream_fields` finds nothing changed, so `ingest_records` skips
   without updating `_source_hash` — meaning the hash gate re-fires every run, forever, until that
   record gets a real value. Verified against `services/survey_cali.py:321-343`. Accepted as
   CPU-only and self-healing (see design.md ADR-7); flagged so it is not later mistaken for a bug.
5. **Cron cadence vs. staleness.** Hourly is proposed (vs. the sticker job's daily) because a planner
   who assigns a point in the morning expects it to drop off the pending list the same day it is
   surveyed. It is cheap — watermark + projected pre-read means an unchanged run does almost no work.
   Confirm with the operator (manual step 8).
6. **Both new collections have no Firestore rules until manual step 7 runs.** Same posture and same
   caveat already on record for `sticker_matches`/`cuadrillas`: the "backend is sole writer" property
   holds by construction (enforced by `tests/invariants/test_sole_writer.py`), not by policy, until
   the console rules are applied.
7. **`informe/json` carries no ArcGIS GlobalID**, so there is no way to link a point to a
   pre-existing survey by identity — only by geography and address. Every survey submitted *before*
   this change ships is matchable only through the fuzzy rungs. Accepted: that backlog is ~1091
   records, and the fuzzy cascade is the same one the legacy job already used successfully on it.

## Rollback Plan

Per surface, in reverse dependency order — every step is a config revert or a deploy revert, never a
data migration:

- **Frontend**: revert the `web/` commit (one tab button, one section, one `switchView()` branch, one
  CSS selector, one `api-config.js` entry, one new module). The backend endpoint keeps working;
  nothing else in the dashboard references it.
- **Endpoint**: remove `planeacion_asignaciones` from `app/main.py`'s `_ROUTERS` and redeploy. The
  collections keep their data; no consumer is left pointing at a dead URL because the frontend was
  already reverted.
- **Cron**: pause or delete the `planeacion-cruce` Railway service. `planeacion_puntos` freezes at
  its last state; nothing else reads it.
- **`codigoapp` pipeline fix**: revert the `LAYER_TO_RAW` entry. Already-ingested `codigoapp` values
  stay in `survey_cali` as harmless extra fields (they are content, not schema); the next ingest run
  simply stops refreshing them.
- **Data**: both new collections are additive and read by nothing else. Deleting them is safe and
  loses only the assignment state, which is re-derivable except for the human-entered
  `prioridad_override` / `motivo_exclusion` / `notas` fields — export those first if the rollback is
  intended to be permanent.

## Rough size

Five work units map cleanly to a dependency chain: (1) the `codigoapp` pipeline fix, (2) the cruce
job + key minting + prioritization, (3) the router + survey-link service, (4) the Planeación tab, (5)
the manual console/ArcGIS steps (no repo diff). Comfortably over the 400-line single-PR budget once
the ~14.8k-scale query surface and the table+map+CRUD UI are counted — plan for a chained PR per work
unit, confirmed at the tasks/apply gate. Delivery: `auto-chain` / `stacked-to-main`.

---

## Proposal question round

The SDD interactive contract calls for a product-question round before this proposal is locked. This
executor cannot prompt the user directly, so the questions and the assumptions they would validate
are recorded here for review. **Answering these changes the proposal; leaving them unanswered means
shipping the stated defaults.**

### ANSWERED by the user (2026-08-26) — these three are now BINDING, not defaults

- **Q3 → the SAME inspector roster as Stickers.** Do NOT create a separate professionals collection
  or a type-discriminator field. The Planeación tab reuses the existing inspector roster exactly as
  `stickers-asignacion.js` does (`getInspectores` injected by the caller), and
  `planeacion_cuadrillas.inspector_uid` holds the same uids as `cuadrillas.inspector_uid`. This is
  the answer that *removes* scope: no new ABM, no new roster surface, no new spec requirement.
- **Q5 → auto-close, but reviewable.** When the cron matches a returned survey by
  `clave_integracion`, it sets the point to `hecho` WITHOUT waiting for a human. The admin endpoint
  must additionally expose a **reopen** action so a point closed by a bad/mistaken survey can be put
  back into the queue. Consequence for ADR-9 field ownership: `estado_asignacion` is normally
  admin-owned, but this makes the pipeline a second writer of that ONE field for the specific
  `pendiente|asignado|en_proceso → hecho` transition. That must be written down explicitly in the
  design and enforced in the job (the pipeline may never move a point OUT of `hecho`, and never
  touches `cuadrilla_id`/`inspector_uid`) — otherwise the sole-writer invariant silently degrades.
- **Q4 → 10 or more points per cuadrilla.** `DEFAULT_MAX_SIZE = 10` for Planeación (not the sticker
  template's 8). Keep it a named constant with the same override-per-call plumbing
  `autoAgrupar` already has, so operations can retune without a deploy.

Q1 (priority weights), Q2 (exclusion vocabulary) and Q6 (correction scope) remain UNANSWERED and ship
with the defaults stated below.

**Q1 — Priority (the one real product decision).** The default ranks severity (`afectacion`) above
verification state (`estadoVerificacion`) above age since `fechaCreacion`, and deliberately excludes
comuna. Is that the operations lead's actual ordering? And what is the ranking of the live
`afectacion` values — is a "colapso total" report a *higher* priority to survey, or does it drop out
of the pool entirely (nothing left to assess)?
*Assumption if unanswered*: severity-first as stated, and total collapse stays IN the pool at highest
priority (the survey documents the collapse; it is not a wasted trip).

**Q2 — Exclusion vocabulary.** Which `estadoVerificacion` values mean "this report should never be
surveyed" (duplicate, rejected, out of jurisdiction, demolished)? Those should be *excluded from the
pool*, not merely ranked low — the same way the sticker campaign excludes `colapso: total` outright
rather than deprioritizing it.
*Assumption if unanswered*: nothing is auto-excluded in v1; the operator excludes case-by-case with
`marcarNoAplica` and a written reason, and the vocabulary is learned from what they actually exclude.

**Q3 — Who is being assigned.** The user says "inspectores/profesionales". Is this the same
`inspectores/{uid}` roster the sticker campaign uses, or a distinct group of professionals (e.g.
structural engineers) who are not sticker inspectors and may not exist in that roster yet?
*Assumption if unanswered*: the same roster, read-only, filtered by `habilitado` — the same
`isHabilitado` filter `stickers-asignacion.js` already applies.

**Q4 — Cuadrilla size for a survey.** An EDAN survey is a much longer visit than applying a sticker,
so the sticker defaults (800 m radius, 8 points) are almost certainly wrong here. How many EDAN
surveys can one professional realistically complete in a working day?
*Assumption if unanswered*: ship the sticker defaults as named constants with a visible in-UI
override, and flag them as unconfirmed in the PR description — same treatment task 0.2 gave the
sticker campaign.

**Q5 — What "done" means for a point.** When a survey comes back matched by key, should the point
automatically become `hecho` and disappear from the working set, or should it land in a "surveyed,
pending office review" state that a human clears?
*Assumption if unanswered*: automatic — `tiene_survey: true` removes the point from the default
pending query, no manual review gate. A review gate is a Phase 2 if the office wants one.

**Q6 — Correction scope.** "Posibilidad de edición o corrección" is read here as: reassign a point,
change its assignment state, override its priority, exclude it with a reason, and annotate it. Does
the operator also need to correct the *report's own data* (a wrong address or coordinate coming from
the API)?
*Assumption if unanswered*: no — report data stays pipeline-owned and read-only. Correcting upstream
data from the planner would create a second, silent source of truth for the atencionsismo record.

Ask for a second round if any answer changes the shape rather than just a constant.
</content>
