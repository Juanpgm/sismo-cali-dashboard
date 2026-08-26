# Archive Report: Stickers — cruce y asignación

Change: `stickers-asignacion` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-archive
Archived: 2026-08-26 · Artifact store: openspec

## Archive Decision: intentional-with-warnings

This change is archived as **implementation-complete and shipped to production**, with 4 known
operator/manual follow-up items explicitly carried forward (not silently dropped, not falsely
checked). This is an explicit orchestrator-directed intentional archive per the
Strict-vs-OpenSpec Archive Policy's non-critical-partial-archive allowance:

- `verify-report.md` final verdict: **PASS WITH WARNINGS**, **CRITICAL: None found**. All 6
  test/check suites pass for real; every sampled spec requirement (merge-safety, `autoAgrupar`
  determinism, admin-only auth, scope boundaries, CRUD scope, lazy init, roster reuse) is
  source-verified against the actual implementation.
- `tasks.md`: 26/30 tasks checked. The 4 unchecked tasks (`0.2`, `1.7`, `3.9`, `4.1`) are **not**
  stale checkboxes for completed work — they are genuinely open, non-repo-diff operator actions,
  confirmed as such by both `apply-progress.md` and `verify-report.md`. No task was falsely marked
  complete during this archive.

## Remaining operator follow-ups (tracked, not blocking)

| Task | What's open | Type |
|---|---|---|
| **0.2** | Confirm `maxRadiusM`/`maxSize` (currently named placeholder constants `DEFAULT_MAX_RADIUS_M=800`, `DEFAULT_MAX_SIZE=8` in `api/sticker-asignaciones.js`) with the operator; one-line change if/when confirmed, no code defect | Product-decision confirmation |
| **1.7** | Create the Railway cron service for `integracion_F1/job_sticker.py` (daily cadence, same image as `job_asignaciones.py`) — `integracion_F1/railway.json` has no per-service fields, so this is a Railway CLI/dashboard action, not a repo diff | Manual operator/deploy action |
| **3.9** | Real browser/DOM smoke test of the Asignación sub-section (segment click-through, Leaflet map render/fitBounds/legend, network-tab single-call assertion, live CRUD round-trips) — no live browser or Firestore credentials were available to any apply/verify agent in this environment; static analysis + passing offline self-checks cover the pure logic underneath it | Manual QA/verification step |
| **4.1** | Add Firestore console rules for `sticker_matches`/`cuadrillas` (admin-SDK-only, deny client reads/writes) in the `sismo-agosto-sgred` Firebase console — no `.rules` file in this repo governs that project's deployed ruleset | Firebase-console-only action |

None of these four items has a repo diff attached to it; none gates the shipped, live feature.

## Specs Synced

| Domain | Action | Details |
|---|---|---|
| `stickers-asignacion` | Created | New capability — no prior main spec existed for this domain. Full delta spec (17 requirements, all scenarios) copied verbatim to `openspec/specs/stickers-asignacion/spec.md` as the source of truth. |

## Archive Contents

`openspec/changes/archive/2026-08-26-stickers-asignacion/`:
- `proposal.md` ✅
- `exploration.md` ✅
- `design.md` ✅
- `specs/stickers-asignacion/spec.md` ✅
- `tasks.md` ✅ (26/30 tasks complete; 4 correctly left unchecked as documented above)
- `apply-progress.md` ✅ (3 batches: pipeline, API, frontend)
- `verify-report.md` ✅ (PASS WITH WARNINGS, no CRITICAL)
- `archive-report.md` ✅ (this file)

## Source of Truth Updated

- `openspec/specs/stickers-asignacion/spec.md` now reflects the new behavior (new capability spec).

## Known cross-repo note (carried from apply-progress.md)

`integracion_F1/cruce_sticker.py` and `integracion_F1/job_sticker.py` live in the separate
`normalizador_data_sismo_cali` git repository (not tracked by this repo, per `.gitignore`).
`apply-progress.md` records those files as verified/tested and confirmed committed to that
subrepo's own `main` at commit `551a73a` (per `verify-report.md`'s "Scope verified" section).
No further action needed from this repo's perspective.

## Traceability

- Proposal, exploration, design, spec, tasks, apply-progress, verify-report: all read directly
  from `openspec/changes/stickers-asignacion/` at archive time (filesystem artifact store — no
  Engram observation IDs apply to this openspec-mode change).

## SDD Cycle Complete

The change has been fully planned, implemented, verified, and archived. The live feature is
shipped; only non-blocking operator/manual follow-ups remain, tracked above.
