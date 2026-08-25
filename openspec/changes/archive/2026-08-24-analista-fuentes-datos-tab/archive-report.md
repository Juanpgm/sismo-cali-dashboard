# Archive Report: analista-fuentes-datos-tab

**Archived**: 2026-08-24
**Status**: COMPLETE — change fully planned, implemented, verified, and closed
**Commit**: bde113d (main)

## Executive Summary

The **analista-fuentes-datos-tab** change introduces a new admin-only "Analista" tab to the dashboard, providing a data-source health inventory with 10 sources listed (metadata freshness, whole-run status, and a live connectivity probe for the atencionsismo API). The change was fully implemented, reviewed with full 4R scope (risk, resilience, readability, reliability), corrected for two CRITICAL security and resilience issues, and independently verified with 0 remaining CRITICAL findings. All task phases 1-3 are complete and checked; phase 4 (post-deploy manual verification) is correctly left unchecked (requires live admin browser session). The change is ready for production deployment.

## Scope Summary

### What was added
- New admin-gated `GET /api/source-status` serverless endpoint for live atencionsismo API probe
- New `web/js/analista.js` module: fetch orchestration (10 sources in parallel), normalization to uniform SourceRow shape, and rendering via existing sticker-list blocks
- HTML/CSS/nav scaffolding: Analista tab button, view section, admin-gate selector
- One additive export from `api/reportados.js` (probeApi reuse)
- Test files: `api/source-status.test.js` and `web/js/analista.test.mjs` (latter added post-apply for timeout testing)

### No destructive changes
- Nothing modified destructively; rollback is `git revert` of one commit
- No data migration, schema change, or Firestore rules change
- All data reads from already-public Blob/JSON or existing Firestore collections (Israel inspecciones_israel)

## Lifecycle

### Proposal Phase
- Explored 10 data sources feeding the dashboard
- Evaluated approaches: snapshot-only, pipeline instrumentation, live-on-load
- Chose pragmatic hybrid: snapshot freshness signals + one new live probe (atencionsismo only, has cheap existing probe)
- Defined rollback, risks, and manual verification checklist

### Spec Phase
- Defined 5 core requirements with 24 scenarios covering: tab visibility (admin-only, defense-in-depth), source list rendering, status color/label derivation per category, live-probe endpoint, refresh behavior
- Specified: non-goals explicitly (no per-source _status.json array, no integracion_F1 monitoring, no new probes for Sheet/Survey123/Geocoding)

### Design Phase
- Designed uniform SourceRow normalization (ADR-1) for heterogeneous signal quality
- Designed live-probe endpoint (ADR-3) and probeApi reuse (ADR-4)
- Defined 45-min staleness threshold (ADR-5, derived from 15-min cron cadence)
- Specified fetch-failure handling: transport errors → amarillo `sin datos`, never rojo (ADR-6)

### Apply Phase
- **Phase 1 (test-first)**: Wrote test, confirmed red, implemented endpoint, confirmed green
- **Phase 2 (scaffolding)**: HTML/CSS/nav branches added
- **Phase 3 (module)**: Analista.js with orchestration, per-source normalization, rendering
- **Deviations from design with justification**: asignaciones.json registros derivation (numbers not arrays), geocoding caching, _status.json fallback, testability seam for endpoint

### Verify Phase
- **0 CRITICAL findings** (after 4R corrections)
- All 5 requirements verified implemented
- All 24 scenarios verified passing
- 2 WARNING (stale documentation text), 1 SUGGESTION (file-table gap) — all non-blocking

### Post-Apply 4R Review & Correction
Full risk/resilience/readability/reliability review identified two CRITICAL issues:

#### RESILIENCE-001: Network hang
- **Finding**: No timeout on Promise.allSettled batch; stalled fetch could hang tab indefinitely
- **Root cause**: Oversight in timeout protection design
- **Fix**: `withTimeout()` helper (15s) wraps all 10 sources
- **New test**: `web/js/analista.test.mjs` covers timeout + recovery path
- **Verification**: Independent fix-delta validator approved; re-verified 0 CRITICAL

#### RELIABILITY-001: Shared-cache auth bypass
- **Finding**: `Cache-Control: public, s-maxage=60` on admin-gated endpoint; Vercel's shared Edge Network caches by URL+method, so one admin's cached response could serve later unauthorized caller
- **Root cause**: Initial design used shared caching to reduce upstream API load; missed that shared cache can breach authentication
- **Fix**: Changed to `Cache-Control: private, no-store` on both ok:true and ok:false branches
- **Spec update**: Updated "Live-probe endpoint" requirement to specify private/no-store
- **Test extension**: `api/source-status.test.js` now asserts cache header on both branches
- **Verification**: Independent fix-delta validator approved; re-verified 0 CRITICAL

### Archive Phase
- All change artifacts (proposal, spec, design, tasks, apply-progress, verify-report) archived to `openspec/changes/archive/2026-08-24-analista-fuentes-datos-tab/`
- Delta spec merged to main specs: new file `openspec/specs/data-sources-analista/spec.md` (purely additive, no destructive merge)
- Archive report written summarizing entire lifecycle and closure

## Verification Results

**Final verdict: PASS — Ready for production**

- **0 CRITICAL issues** (post-4R correction)
- **2 WARNING** (stale task/apply-progress text) — non-blocking, historical artifacts
- **1 SUGGESTION** (design file-table incompleteness) — non-blocking documentation gap
- **All 5 requirements** verified implemented
- **All 24 scenarios** verified passing
- **All automated tests** passing:
  - `node api/source-status.test.js` ✓
  - `node api/usuarios.test.js` (regression) ✓
  - `node --test "js/**/*.test.mjs"` (5/5 pass, including new analista.test.mjs) ✓

## Spec Reconciliation

The final authoritative specs are:
- `openspec/specs/data-sources-analista/spec.md` — main spec, updated post-4R to reflect `private, no-store` caching requirement and timeout resilience
- `openspec/changes/archive/2026-08-24-analista-fuentes-datos-tab/design.md` — implementation design with ADRs 1-6 and corrections documented
- `openspec/changes/archive/2026-08-24-analista-fuentes-datos-tab/tasks.md` — task breakdown, phases 1-3 complete, phase 4 pending post-deploy

## Known Limitations & Future Work

### v1 Limitations (by design, not gaps)
- Geocoding source can only show `sin metadata` (no freshness data available for cache-only source)
- Orphaned outputs show weak signals (file presence + last-modified only; no monitoring of integracion_F1 pipeline)
- Global pipeline status is coarse-grained (whole run, not per-source; per-source error array deferred to future)
- Survey123 sub-source errors invisible (folded into inspections.json; only whole-run freshness available)
- Live probe depends on VISITADOS_API_PASS environment variable in Vercel (if missing, atencionsismo row shows `con errores` — correct behavior but worth noting)

### Recommended Future Enhancements
1. Extend `refresh_data.py` to publish per-source error detail to `_sources_status.json` (would replace coarse-grained whole-run flag)
2. Add monitoring/heartbeat for integracion_F1 cruce-gestion pipeline (would provide richer orphaned-source signals)
3. Implement live-check for Sheet/Survey123/Geocoding if cheap, safe probes become available
4. Add historical audit log of source health state (currently snapshot-only, no trend visibility)

## Artifacts

### Archived to openspec/changes/archive/2026-08-24-analista-fuentes-datos-tab/
- `proposal.md` — problem statement, solution approach, rollback plan
- `spec.md` — 5 core requirements with 24 Given/When/Then scenarios
- `design.md` — architecture, file-level plan, ADRs 1-6, testing approach
- `tasks.md` — 3 implementation phases (1-3 complete) + 1 post-deploy phase (pending) + review workload forecast
- `apply-progress.md` — full implementation breakdown by phase, deviations with justification, post-apply 4R corrections
- `verify-report.md` — test evidence, spec-vs-implementation verification, findings, final verdict
- `exploration.md` — pre-proposal exploration of data sources and approaches

### Created in openspec/specs/
- `openspec/specs/data-sources-analista/spec.md` — merged from delta spec (main source of truth)

## Archive Integrity Check

✓ Main spec created: `openspec/specs/data-sources-analista/spec.md`
✓ All change artifacts archived to `openspec/changes/archive/2026-08-24-analista-fuentes-datos-tab/`
✓ No destructive merges (spec is new file, purely additive)
✓ Task completion: phases 1-3 all checked, phase 4 correctly left unchecked
✓ Verification: 0 CRITICAL findings (post-correction)
✓ No open blockers or pending corrections

## Status: COMPLETE & CLOSED

The analista-fuentes-datos-tab change is fully archived and ready for production deployment.
Next step: manual post-deploy verification (phase 4 tasks) against live admin session.

---

**Archive created by**: sdd-archive executor
**Date**: 2026-08-24
**Mode**: openspec (filesystem-based artifact store)
</content>
