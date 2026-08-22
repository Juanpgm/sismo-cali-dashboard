# Archive Report: stickers-form-upgrade

## Executive Summary

The **stickers-form-upgrade** change is archived and closed. All 36 implementation tasks completed across three autonomous slices plus remediation. Verification verdict: **PASS** (zero CRITICAL, zero WARNING, 2 pre-existing non-blocking SUGGESTIONs). Tests green: 36/36 unit, 25/25 e2e. The ATC-20 field form (`formulario/`) now derives sticker codes from real records, allows corrections to the last 4 digits, supports dynamic photo capture with parallel upload (capped at 3 slots per signer limitation), and retries transient auth failures instead of forcing logout.

## Change Overview

**Branch**: feature/stickers-form-upgrade (off main)  
**Commits**: `7659e82`, `8e8a885`, `f70907c`, `cb2693e` (4 commits, 1270 lines changed across `formulario/`)  
**Verdict**: PASS  
**Specs Created**: 3 first-time domain specs (sticker-code-assignment, inspection-photo-capture, field-form-session)

## Three-Slice Implementation

### Slice 1: Code Assignment (Commit 7659e82)
- Records-derived consecutive: `max(existing) + 1`, gap-tolerant
- Session-cached query (`where inspector.uid == uid`) run once per session
- Editable last-4-digit segment, validated and collision-protected
- Pre-submit existence check + create-only transaction fail-closed
- Pure functions: `parseConsecutivo`, `siguienteConsecutivo`, `validarSegmento`
- 13 tasks, 298 lines changed

### Slice 2: Photo Capture (Commit 8e8a885)
- Dynamic photo slots (design: up to 10; actual: capped at 3 per signer probe)
- Gallery ("Agregar fotos", multi-select) and camera ("Tomar foto") affordances
- Parallel upload with concurrency cap of 3
- Slot-generic design: only the `MAX_FOTOS` constant needs change if signer is fixed
- 12 tasks, 353 lines changed (incl. full UI/grid rewrite)
- **Key decision**: Task 2.1 probed `sismo-fotos-signer.vercel.app` with `slot: 10` → signer rejects `slot > 3` → fallback to `MAX_FOTOS = 3`

### Slice 3: Session Resilience (Commit f70907c)
- Transient error retry (3 attempts, 600ms backoff) instead of forced logout
- Error classification: fatal (`permission-denied`, `not-found`) vs transient (all others)
- Retry affordance ("Reintentar") button on exhaustion
- Firebase SDK import dedupe (one boundary via auth.js)
- Performance optimizations: preconnect + modulepreload in `<head>`
- 12 tasks, 224 lines changed

### Remediation: Verify-Report WARNINGs (Commit cb2693e)
- Below-next code hint UI (non-blocking, gap-filling allowed)
- Auth retry-exhaustion e2e coverage
- MAX_FOTOS-derived literals (instead of hardcoded `3`)
- Non-4-digit segment rejection e2e coverage
- Spec.md Testability Notes naming alignment (Spanish: `parseConsecutivo`, etc.)
- 5 WARNINGs resolved, 0 CRITICAL, 0 remaining WARNINGs

## Architecture Decisions

**D1 — Consecutive Semantics: Max-Based, Session-Cached**
- Next = `max(consecutive parsed from inspector's evaluaciones) + 1`
- Justification: Count-based collides on gaps (e.g., records {1,3} → count 2 → next 3 → duplicate)
- Session cache: query runs at most once, re-run only on collision
- New `consecutivo` field persisted on create for future indexed queries
- **Firestore rules: zero-change rollout** — client stops writing `inspectores/{uid}.consecutivo`, +1-only rule never exercised again

**D2 — Editable Segment: Permissive, Fail-Closed**
- Range: `0001`–`9999` only (exactly 4 digits)
- No floor at "next available" (would block gap-filling corrections, the feature's core intent)
- Guardrails: input validation → pre-submit existence check → create-only transaction backstop
- Non-blocking hint when value below derived next (gap correction awareness)

**D3 — Photo Signer Slot Range: Slot-Generic with Fallback**
- Design intention: 1–10 slots, client-side slot-generic
- Apply gate: manual probe of signer with `slot: 10`
- Probe result: signer rejects `slot > 3` (400 bad-request on slot validation)
- **Shipped cap**: `MAX_FOTOS = 3` (single constant; if signer is fixed, only this value changes)
- Defensive fallback: `slot > MAX_FOTOS` signer error → distinguish with specific Spanish message

**D4 — Firestore Rules: Zero-Change Rollout, Optional Post-Rollout Tightening**
- Existing rules grant `allow read: if isInspector()` on `/evaluaciones/{id}` → query works without deployment
- Client stops writing `consecutivo` → +1-only rule becomes dead, never exercises
- New `consecutivo` field on creates allowed (rule only constrains `inspector.uid`)
- Optional post-rollout: operator can deploy `allow update: if false` on `inspectores` (documented in SETUP.md §8, not required for this change)

## Test Results

| Suite | Result | Coverage |
|-------|--------|----------|
| Unit | 36/36 pass (100%) | Pure logic: parseConsecutivo, siguienteConsecutivo, validarSegmento, canAddSlot, clasificarErrorFirestore, backoffDelay |
| E2E | 25/25 pass (100%) | Code derivation (gaps, caching, collision), editable segment (validation, hints, duplicates), photo capture (gallery, camera, cap, removal reordering), parallel upload (concurrency, cache-reuse), auth retry (transient vs fatal, exhaustion) |
| Total | 61/61 (100%) | All new features + full regression across 3 slices + remediation |

## Key Shipped Behaviors

### Code Assignment
- Inspector generates a code → queries own `evaluaciones` → derives `max + 1` → renders as read-only prefix + editable 4-digit input
- Editing `0002` when next is `0006` → non-blocking hint shown, edit accepted (gap filling)
- Duplicate submit (same code) → pre-check catches it (friendly error) or create-only transaction fails closed (authoritative)
- Abandoning the form → code not persisted → next session re-derives without consuming a number

### Photo Capture
- Add photos via gallery (multi-select) or camera (one-shot)
- Max 3 photos visible (1 constant controls this)
- Remove photo → remaining reorder with no gaps, URLs in order persisted to Firestore
- Submit → parallel upload (up to 3 in flight), cache by `{codigo}:{name}:{size}:{lastModified}`, reuse on retry
- Cache survives form re-submissions (idempotent retries)

### Session Resilience
- Profile read fails with `unavailable` → retry up to 3 times with backoff (600ms, 1800ms)
- All 3 retries fail → show "Reintentar" button, inspector taps to recover without re-authenticating
- Profile read fails with `permission-denied` → immediate sign-out (authoritative rejection)
- Missing profile or `activo === false` → immediate sign-out

## Firestore Schema Impact

**New field on `evaluaciones/{codigo}`**:
- `consecutivo: number` — numeric value (1..9999), parsed from final code segment
- Additive only, ignored by all current readers
- Enables future cheap indexed `orderBy(consecutivo)` queries without changing client behavior now

**No changes to `inspectores/{uid}` writes** (existing `consecutivo` field left in place, just not updated by the client anymore).

## Rollback Plan

All three slices independently revertible via `git revert`:
1. `git revert 7659e82` → old counter-based code generation restored
2. `git revert 8e8a885` → photo UI back to 3 hardcoded slots, sequential uploads
3. `git revert f70907c` → unconditional logout on transient errors restored

No Firestore rules deployed, no schema removal needed. Legacy `consecutivo` field on `inspectores` untouched and ignored by reverted client.

## Post-Rollout Optional Steps

Documented in `formulario/SETUP.md` §8 (not required for this change, optional operator decision):
- Tighten `inspectores` rule: add `allow update: if false` to prevent any future writes
- This papercuts the legacy `consecutivo` field as read-only, removing the stale-field temptation

## Follow-Up Items (Non-Blocking)

Two pre-existing SUGGESTIONs from verify-report (documented, not archive-blocking):
1. **Concurrent two-device race untested** — pre-existing transaction pattern is unchanged by this change; a future advanced e2e scenario (two devices both generating same code simultaneously) could be added, but create-only transaction already fails closed
2. **Root-level untracked `node_modules/` and `package-lock.json` noise** — unrelated to this change, not committed

Both are optional. The change is complete and shippable as-is.

## Files in Archive

### Artifacts
- `explore.md` — exploration phase findings and risk analysis
- `proposal.md` — intent, scope, decisions (D1–D4), approach, risks, rollback plan
- `design.md` — technical architecture, data flow, file changes, testing strategy
- `tasks.md` — 36 itemized implementation tasks (all checked), Slice 1–3, Slice 3 remediation
- `apply-progress.md` — TDD cycle evidence, per-slice verification, workload notes
- `verify-report.md` — test results (36 unit, 25 e2e), per-finding resolution, verdict: PASS
- `specs/sticker-code-assignment/spec.md` — domain spec (first-time create)
- `specs/inspection-photo-capture/spec.md` — domain spec (first-time create)
- `specs/field-form-session/spec.md` — domain spec (first-time create)

### Deployment Files (in main spec store)
- `openspec/specs/sticker-code-assignment/spec.md` — promoted from delta
- `openspec/specs/inspection-photo-capture/spec.md` — promoted from delta
- `openspec/specs/field-form-session/spec.md` — promoted from delta

## Commits on Feature Branch

```
7659e82 feat(formulario): derive sticker consecutive from records and allow segment edit
8e8a885 feat(formulario): dynamic photo capture via gallery/camera with parallel upload
f70907c feat(formulario): retry transient profile reads and dedupe Firebase imports
cb2693e fix(formulario): implement below-next code hint and derive photo-cap literals from MAX_FOTOS
```

All authored by juanpgm <juanp.gzmz@gmail.com>, conventional commits, no AI attribution.

## Sign-Off

Change closed and moved to archive on **2026-08-22**.
All 36 implementation tasks complete.
Verification verdict: **PASS** (0 CRITICAL, 0 WARNING, 2 pre-existing non-blocking SUGGESTION).
Ready for merge to main and deployment.
