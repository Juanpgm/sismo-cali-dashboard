# Archive Report: usuarios-tab

## Executive Summary

The **usuarios-tab** change is archived and closed. All implementation tasks completed except manual browser smoke test (task 2.9), which is a genuinely outstanding manual-QA step, not a code defect. Verification verdict: **PASS-WITH-CAVEATS** (all 8 spec requirements MET, 0 CRITICAL findings, 2 WARNING, 2 SUGGESTION, 1 outstanding manual-QA task). Code committed and deployed-ready pending browser smoke test before production use.

## Change Overview

**Change**: usuarios-tab (Usuarios tab v1 — user management & access control)  
**Branch**: feature/usuarios-tab (off main)  
**Verdict**: PASS-WITH-CAVEATS  
**Spec Created**: 1 first-time domain spec (user-management)  
**Tasks Completion**: 16/17 implementation tasks done (94.1%); 1 manual-QA item unchecked but correctly flagged as outstanding  

## What Shipped (v1 scope)

### New Usuarios Tab: superset user list + CRUD + anti-lockout guards

- **Superset user listing** across all three populations: password admins, Google `@cali.gov.co` viewers, `@sismocali.gov.co` inspectors.
- **Metrics included**: last sign-in time and account creation time (free from Firebase Admin SDK).
- **Metrics explicitly NOT included**: login counts, download counts (Phase 2, server-side capture required).
- **Admin actions**, all authorized server-side:
  - `create` — new password-admin account
  - `setEnabled` — toggle enable/disable + sync Firestore `activo` flag
  - `delete` — **net-new**; removes Auth user and Firestore `inspectores/{uid}` profile; leaves `evaluaciones` intact (historical records)
  - `sendPasswordResetEmail` — Firebase client SDK (no custom email infra)
- **Filtering** — role (admin/viewer/inspector/otro) + status (enabled/disabled), client-side over fetched data.
- **In-tab stat chips** — total, activos, inhabilitados, plus per-role counts (admin/viewer/inspector); `otro` intentionally not counted.
- **Anti-lockout guards** (server-side, fail-closed):
  - No self-disable/delete: caller cannot manage their own account
  - No last-admin delete: cannot delete the only remaining enabled admin

## Verification Verdict: PASS-WITH-CAVEATS

### Per-Requirement Results
- **Tab visibility and server-side authorization** — MET
- **Superset user listing** — MET
- **Role, domain, and status filtering** — MET
- **In-tab stat chips** — MET
- **Create user** — MET (with documented narrowing: admin-create only, no Firestore profile write)
- **Disable / enable user** — MET
- **Delete user with anti-lockout guards** — MET
- **Send password reset** — MET

### Test Results
| Suite | Result | Coverage |
|-------|--------|----------|
| api/usuarios.test.js | PASS | classify predicate; last-admin count logic; self-uid guard |
| api/stickers.test.js (regression) | PASS | Pre-existing tests unaffected |
| node --check web/js/usuarios.js | PASS | Syntax validation |

### Known Findings (Non-Blocking)

**WARNINGS** (low-severity, documented):
1. **Mobile CSS cramping** — `.usuario-actions` (flex-wrapped buttons) does not get the full-width treatment that Stickers' mobile layout does. Renders slightly cramped on narrow screens but not broken. Media-query override needed if reported. Documented: `apply-progress.md`, ponytail comment.
2. **Best-effort Firestore delete** — if `inspectores/{uid}` delete fails after `deleteUser` succeeds, the Auth account is gone but the Firestore doc survives (orphaned). Low-probability, low-impact; no retry or warning surfaced. Acceptable for a non-inspector target.

**SUGGESTIONS** (informational):
1. **`otro` bucket has no stat chip** — intentional per design (ADR-5 locked chip shape). `otro` rows remain visible/filterable; just not counted in the chips. Not a spec gap.
2. **Inline ponytail comment** — the mobile-gap ponytail was documented in `apply-progress.md` rather than inline in `styles.css`. Consider adding the inline comment for future readers.

### Critical Gaps: NONE

**Outstanding Manual QA** (pre-production gate, not a code defect):
- **Task 2.9: Browser smoke test** — log in as admin, open Usuarios tab, verify superset renders with rows from all three populations, chips match totals, role/status filters work, disable→enable→delete round trip succeeds, reset-password click works, then log in as viewer and confirm tab is absent + direct API call rejected. This was NOT run (apply and verify environments lack browser). It is a genuine end-to-end validation before production deployment, not a hidden code defect.

## Implementation Details

### Files Delivered

**Backend** (api/):
- `api/usuarios.js` — serverless function (220 lines)
  - `classify(u)` — role predicate (inspector-domain-first ordering)
  - `action: 'list'` — superset fetch via `listUsers(1000)`
  - `action: 'create'` — password-admin creation
  - `action: 'setEnabled'` — toggle + sync `inspectores/{uid}.activo`
  - `action: 'delete'` — Auth user + `inspectores/{uid}` cleanup; anti-lockout guards
- `api/usuarios.test.js` — assert-based self-check (60+ lines)
  - `classify` on four fixture roles
  - Last-admin count logic
  - Self-uid guard

**Frontend** (web/):
- `web/js/usuarios.js` — new module (250+ lines), clones Stickers structure
  - Shell + reload/render/wire lifecycle
  - Stat chips + inline filters (client-side)
  - Row actions: disable/enable, delete, reset-password
  - Create modal
- `web/index.html` — added tab button + panel (2 lines)
- `web/js/main.js` — import + lazy init in `switchView()` (5 lines)
- `web/styles.css` — viewer-hide rule + minimal net-new styles (9+ lines)
  - `.usuario-filters`, `.usuario-row` (4-col grid), `.usuario-actions` (flex)
  - Reused all `.sticker-*` classes

### Scope Compliance

**In scope (v1, delivered):**
- New Usuarios tab
- Superset list (all three populations)
- Last sign-in / created timestamps
- CRUD + anti-lockout
- Filtering + stats chips

**Explicitly out of scope (Phase 2):**
- Event-logging / usage counters (both capture AND UI deferred)
- Firestore rules edit (console-managed for `sismo-agosto-sgred`; Phase 2 server-side capture)
- Custom email infra (reset uses Firebase hosted templates)
- Pagination (inherits `listUsers(1000)` ceiling; cursor paging only if exceeded)

## Specs Merged

| Domain | Action | Details |
|--------|--------|---------|
| user-management | Created | New first-time domain spec; 8 requirements, 15 scenarios; all MET |

**Deployment file**: `openspec/specs/user-management/spec.md` — promoted from delta in this archive.

## Rollback Plan

All delivered code is independently revertible:
```
git revert <usuarios-feature-commit>
```

No Firestore rules deployed. Schema-only impact: new `.activo` sync in `setEnabled` for existing inspectors (already handled by Stickers pattern). No data cleanup needed on rollback.

## Follow-Up Items (Non-Blocking, Pre-Production)

1. **Manual browser smoke test (Task 2.9)** — MUST run before production sign-off. This is an end-to-end validation that was skipped due to environment constraints, not a code defect.
2. **Mobile CSS override** (SUGGESTION) — if mobile users report cramped action buttons, add media-query override for `.usuario-row .usuario-actions { grid-column: 2/4; }` alongside the existing `.sticker-row .sticker-action` rule.
3. **Inline ponytail comment** (SUGGESTION) — add `// ponytail: usuario-row mobile cramping...` comment in `styles.css` line ~1818 for future readers.

## Size & Delivery

- **Estimated authored lines**: ~450-470 (backend ~260 + frontend ~210 + HTML/CSS wiring ~15)
- **400-line threshold**: Modestly over; can split into backend→frontend two-PR slices if reviewer prefers, or single PR with standard dominant-risk lens (review-reliability).
- **Actual delivery**: One feature branch with both work units; apply phase documented the option to split post-apply if needed.

## Files in Archive

### SDD Artifacts
- `exploration.md` — exploration phase findings, constraints, risk analysis
- `proposal.md` — intent, scope, decisions (D1–D6), approach, risks
- `design.md` — technical architecture, ADRs, data flow, file changes, testability
- `tasks.md` — 17 itemized implementation tasks; 16 checked, 1 manual-QA item (correctly left unchecked)
- `apply-progress.md` — TDD cycle evidence, per-WU verification, known gaps documented
- `verify-report.md` — per-requirement verdict (all MET), test results, findings (0 CRITICAL, 2 WARNING, 2 SUGGESTION), manual-QA item flagged
- `spec.md` — domain spec (first-time create); 8 requirements, 15 scenarios

### Deployment Files (in main spec store)
- `openspec/specs/user-management/spec.md` — promoted from delta; authoritative source of truth for user-management domain

## Sign-Off

Change closed and moved to archive on **2026-08-24**.  
Implementation: 16/17 tasks complete (manual smoke test correctly left as outstanding pre-production gate).  
Verification verdict: **PASS-WITH-CAVEATS** (0 CRITICAL, 2 WARNING, 2 SUGGESTION; all 8 spec requirements MET).  
**Ready for review and merge to main, contingent on manual browser smoke test (Task 2.9) before production deployment.**
