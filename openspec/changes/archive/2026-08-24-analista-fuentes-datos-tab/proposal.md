# Proposal: analista-fuentes-datos-tab

## Intent

### Problem

Administrators have no single place to see the health of the data sources that
feed the dashboard. Today the state of each source is scattered and effectively
invisible after a run:

- `refresh_data.py` logs per-source `warning`s only to Railway stdout, which is
  unreadable after the run completes (see project memory
  `data-refresh-architecture` and commit "Railway logs unreadable post-run").
- Freshness signals (`meta.json`, `reportes_meta.json`) and the global run
  outcome (`_status.json`) exist in Blob but no UI surfaces them.
- The `atencionsismo` API connection is only exercised implicitly by the
  "Reportados" KPI; there is no explicit "is this API reachable right now?"
  signal an admin can trust.
- Several outputs (`asignaciones.json`, `cruce_gestor.json`,
  `cruce_criticos_survey.json`, `criticos_api.json`) are still produced by cron
  but consumed by no live tab — dead data an admin cannot currently discover.

### Why now

The dashboard now depends on two Railway pipelines, Vercel Blob, a live API
proxy, and client-side FeatureServer fetches. When something upstream breaks,
the only symptom is stale or missing dashboard data, with no way to localize the
failure. A source-inventory view turns "the dashboard looks wrong" into "source
X is stale / unreachable / orphaned".

### Success looks like

An administrator opens the **Analista** tab and sees, in one list, every data
source feeding the dashboard, each with a name, a short description, its last
read timestamp and row count (where available), and a color-coded status
(verde / amarillo / rojo) with a short Spanish label. For the `atencionsismo`
API the status reflects a *real, verified* live probe, not a decorative
snapshot. Orphaned outputs are visibly labeled as having no consumer.

## Scope

### In scope

1. A new dashboard tab labeled exactly **"Analista"**, visible only to
   Administrators, reusing the existing admin-gate pattern.
2. A source list rendered with existing generic building blocks
   (`sticker-list`/`sticker-row`, `section-bar`) — no new component.
3. Status colors from the existing 3-state semáforo palette (`COLORS.status` in
   `web/js/utils.js`).
4. Sources shown in v1: EDAN-F3, Survey123, Geocoding, atencionsismo (with live
   probe), global pipeline run, orphaned outputs, and Israel FeatureServer.
5. One new admin-gated serverless endpoint for the atencionsismo live probe.

### Out of scope

- Modifying `refresh_data.py`'s error handling beyond the atencionsismo probe
- Building monitoring for `integracion_F1` / `cruce-gestion` pipeline
- Fixing stale `README.md` pipeline docs
- Adding per-source error arrays to `_status.json`
- New reachability probes for Sheet/Survey123/Geocoding

## User-facing behavior

The **Analista** tab renders a header with "Actualizar" button and a list of 10
source rows, each showing: nombre, descripcion, ultima_lectura, registros
(where available), and estado (semáforo color + Spanish label).

**Refresh:** Re-fetches on every tab open + explicit "Actualizar" button. No
polling/auto-refresh.

## atencionsismo live-check approach

**Chosen: Reuse `probeApi()` from `api/reportados.js` in new `api/source-status.js`.**

The probe is a ~200ms request against the atencionsismo API that distinguishes
"alive" from "down". It reuses existing logic, keeps the endpoint lightweight,
and the "Actualizar" button lets admins verify connectivity on demand.

## Rollback plan

This change is frontend-only plus one additive serverless endpoint:
- Revert path: single `git revert` removes the tab and endpoint
- No data migration, schema change, or Firestore rules change
- Partial disable: removing one CSS selector or switchView() branch disables it
- Manual verification after deploy confirms admin-only visibility, source
  rows render correctly, and atencionsismo status reflects live probe

## Risks

1. Geocoding source shows only "sin metadata" (no freshness data available)
2. Orphaned sources have weak signals (file presence + last-modified only)
3. Global run status is whole-pipeline granularity, not per-source
4. Survey123 sub-source errors invisible (folded into inspections.json)
5. Live probe depends on VISITADOS_API_PASS in Vercel
</content>
