# Ops Report: `railway-fase0-cleanup`

Change: `railway-fase0-cleanup` · Project: seismic_disaster_data_analisys_cali · Type: **infra/ops** (no code diff, no capability spec change)

**Executed**: 2026-08-31 · **Status**: COMPLETE

---

## Why this is not a full SDD cycle

This closes **Fase 0** of [`firestore-quota-reduccion`](../../..) (2026-08-29/30), left pending because `RAILWAY_API_TOKEN` wasn't available at the time. It changes Railway project topology and cron schedules only — zero lines of application code changed, zero capability behavior changed, so there is no `spec.md`/`design.md`/`tasks.md` to write. Recorded here as a plain ops report instead of forcing the capability-change template.

---

## Root cause confirmed

Firestore quota (`sismo-agosto-sgred`, Spark plan, 50k reads/day) kept exceeding even after the Fase 1-3 code optimizations because **4 legacy Railway services were still running on cron, 24/7, with zero references left in live code** (`backend/app`, `web/`) — only in archived OpenSpec docs:

| Service | Cron before | `startCommand` | Fate |
|---|---|---|---|
| `cruce-gestion` | `0 * * * *` | `job_cruce.py` | Fed the "Gestión" tab, deleted from the dashboard 2026-08-19 |
| `normalizador` | `5 13-23,0 * * *` | `job.py` | Superseded by `dashboard-refresh` |
| `asignaciones` | none (dormant) | `job_asignaciones.py` | Orphaned, superseded by current F3/asignaciones pipeline |
| `integracion-f3` | none (dormant) | `job_integrar_f3.py` | Orphaned, superseded by current F3/asignaciones pipeline |

Additionally, two of the real services had cron drift from their documented desired state (`openspec/changes/fastapi-backend-consolidation/specs/job-scheduling/spec.md` only documents `cruce-sticker`/`dashboard-refresh`; `planeacion-cruce` had no documented schedule at all):

| Service | Before | After |
|---|---|---|
| `cruce-sticker` | `25 * * * *` (24/7) | `7,22,37,52 13-23,0 * * *` (matches `STICKER_EVERY_15` in `backend/scripts/railway_services.py`) |
| `planeacion-cruce` | `10,40 * * * *` (24/7) | `10,40 13-23,0 * * *` (daytime window, same as the other jobs) |

## Actions taken

1. Audited all 8 Railway services in project `normalizador-sismo-cali` via direct GraphQL calls (`backend/scripts/railway_services.py`'s `project_services()`/`instance()`), bypassing the Railway CLI — `railway.exe` is blocked by a Windows Application Control policy on this machine (see memory `railway-cli-bloqueado-windows`).
2. Applied `cruce-sticker`'s cron fix via the existing `railway_services.py --only cruce-sticker`.
3. Applied `planeacion-cruce`'s cron fix via a direct `serviceInstanceUpdate` GraphQL mutation (no existing script covered it).
4. Deleted the 4 legacy services via `serviceDelete` GraphQL mutations.
5. User ran the mutating steps directly (`!` in prompt) — Claude Code's auto-mode classifier blocks production-mutating Bash calls for the agent itself.

## Verification

Final `project_services()` listing after cleanup — exactly the 4 real services, nothing else:

```
cruce-sticker: b18c74c8-0b7a-459c-ada5-5e5df6db8050
dashboard-refresh: 156e97a2-596b-4861-95f4-4060dab408e2
planeacion-cruce: 3db766ea-ac14-4922-bce5-1eacf3dd5bf1
sismo-cali-dashboard: d2a33b31-31d8-4edf-80b4-94cc8129f278
```

No automated test suite covers Railway service topology (external infra, not app code) — verification is this listing plus the grep confirming zero live-code references to the 4 deleted jobs' scripts before deletion.

## Known follow-up (non-blocking)

- The Railway API token used for this cleanup was pasted in plaintext in the chat session — user was advised to rotate it from the Railway dashboard (Account → Tokens).
- Firestore read volume should be monitored for the next 24h (Firebase Console → Firestore → Usage) to confirm the quota no longer gets exceeded; if it still does, the next suspect is the `PlaneacionPuntosSnapshot` full-scan re-triggering on Railway restarts (see `firestore-quota-excedida-de-nuevo-30ago` memory).

---

## Related

- [`firestore-quota-reduccion`](../../../../.claude) — parent optimization effort (Fase 1-3, code-side), Fase 0 closed by this report
- `firestore-quota-excedida-de-nuevo-30ago` — the recurrence that made Fase 0 urgent
- `railway-cli-bloqueado-windows` — why this was done via GraphQL directly instead of the CLI
