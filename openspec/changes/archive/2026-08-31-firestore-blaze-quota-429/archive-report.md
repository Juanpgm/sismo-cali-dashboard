# Ops Report: `firestore-blaze-quota-429`

Change: `firestore-blaze-quota-429` · Project: seismic_disaster_data_analisys_cali · Type: **incident/ops + small resilience fix**

**Executed**: 2026-08-31 (afternoon, UTC) · **Status**: RESOLVED — quota propagation completed; resilience fix shipped in `89aa756`

---

## Why this is not a full SDD cycle

Same rationale as [`railway-fase0-cleanup`](../2026-08-31-railway-fase0-cleanup/archive-report.md): this is an incident investigation whose definitive fix was on Google's side (Blaze quota propagation), plus one small, single-concern resilience patch (a fetch-failure cooldown in two existing caches). No capability behavior changed, so no `spec.md`/`design.md`/`tasks.md` — recorded as a plain ops report.

## Incident summary

Firestore `429 RESOURCE_EXHAUSTED: Quota exceeded` errors persisted in production **after** the fase0 cron cleanup and **after** the project was upgraded to the Blaze plan. `GET /puntos-solicitados` returned 502s to the admin board; `GET /sticker-status` degraded to stale correctly. Errors fired every ~20–40s (16:49–17:06 UTC), sourced from the **web service** `sismo-cali-dashboard` (`d2a33b31-…`), not from any cron.

## Diagnosis chain (what was checked, in order)

1. **Billing** — `gcloud billing projects describe sismo-agosto-sgred`: `billingEnabled: true`, billing account `01304C-05B2E8-C4ADB3` `open: true`. Firebase Console confirmed Blaze active and **billing the overage** (106k reads used vs 50k free — "se superó la cuota sin costo por 56k").
2. **Effective API quota** — `gcloud alpha services quota list --service=firestore.googleapis.com`: `effectiveLimit == defaultLimit` for `read_operations_per_project` (50k/day), `write_operations_per_project` (20k/day), `write_units_per_project` (40k/day). The Service Usage technical cap was **still at Spark free-tier values** despite Blaze being active — these are two independent systems (billing vs. request-cap), and the cap had not propagated yet.
3. **Quota override attempt** — `gcloud alpha services quota update … --value=10000000 --force` was **rejected**: `FAILED_PRECONDITION … COMMON_QUOTA_CONSUMER_OVERRIDE_FOR_FIXED_LIMIT` (`is_fixed`). This limit is NOT editable — not via gcloud, not via the Cloud Console "Edit Quotas" flow. The only paths are Blaze propagation (documented as up to 24h) or a Firebase support ticket.
4. **Exact failure site** — Railway `environmentLogs` GraphQL (filter `"429 OR RESOURCE_EXHAUSTED"`) captured the full traceback: `backend/app/routers/puntos_solicitados.py` `_fetch_puntos_solicitados` (`db.collection(PUNTOS_SOLICITADOS_COLLECTION).get()` — unbounded collection scan) and `sticker_status.py` `_read_coverage`. The `PuntosSolicitadosCache` serve-stale/Blob-LKG design was sound, but Firestore had been failing since the process booted (deploy 16:08:59 UTC), so there was never a good payload in memory or in Blob to degrade to — hence raw 502s.

## Code fix (commit `89aa756`)

`fix(resiliencia): cooldown tras fallo de Firestore en puntos-solicitados y sticker-status`

- `FETCH_FAIL_COOLDOWN_SECONDS = 60` in `backend/app/routers/puntos_solicitados.py` and `backend/app/routers/sticker_status.py`.
- After a failed fetch, requests within the cooldown serve the existing payload without touching Firestore, or fail fast with `HTTPException(503)` on a truly cold cache — replacing a slow 502 that paid the gRPC client's full internal retry chain on **every** request during the outage.
- A successful fetch clears the failure timestamp; `PuntosSolicitadosCache.invalidate()` also clears it (a successful write proves Firestore is reachable, preserving the "visible on the next GET" contract).
- Recovery is automatic: the first request after the window attempts a live fetch.
- Tests: 6 new (cooldown serves stale without refetching; re-attempt after expiry; cold-start fails fast), 81 total passing in the two affected suites.

## Post-implementation review (R4 resilience lens, single sweep)

No BLOCKER/CRITICAL. Two non-blocking WARNINGs, both `info`:

| ID | Finding | Disposition |
|---|---|---|
| RESILIENCE-001 | `get_or_fetch` mutates `_payload`/`_at`/`_failed_at` without a lock; concurrent requests at TTL expiry can double-fetch, and a slow failing fetch can re-arm a just-cleared cooldown (delays recovery ≤60s) | Accepted debt (ponytail-style); add a lock only if it shows up in a profile |
| RESILIENCE-002 | The writer's rationale comment claimed "frontend polls every ~20s"; actual scheduled auto-refresh is 5 min (`puntos_solicitados.js:826`) / 15 min (`main.js:640`). The 20–40s cadence was concurrent admin sessions + manual reloads during the outage | Comment corrected in both files before commit |

## Resolution

- Last `RESOURCE_EXHAUSTED` in Railway logs: **17:06:26 UTC**. As of 17:28 UTC: 22 minutes of continuous real traffic, all 200 OK — including `GET /puntos-solicitados` (previously 502) at 17:28:54. At least one Firestore fetch succeeded post-17:06, so the cap is admitting reads again → Blaze propagation landed.
- The caches are now seeded (memory + Blob LKG), so any future 429 episode degrades to stale instead of 502.

## Known follow-ups (non-blocking)

- **Rotate the Railway API token** — pasted in plaintext in chat for the second time (first: fase0 report). Railway dashboard → Account → Tokens.
- If 429s recur after 24h+ from the Blaze upgrade, open a Firebase support ticket (the cap is `is_fixed`; nothing self-serve can raise it).
- `_fetch_puntos_solicitados`'s unbounded collection scan is fine at current collection size but is the next candidate if read volume becomes a problem again.

## Related

- [`railway-fase0-cleanup`](../2026-08-31-railway-fase0-cleanup/archive-report.md) — same-day predecessor; killed the legacy cron read floor
- Engram: `firestore-429-sostenido-cuota-service-usage-sin-subir-pese-a-blaze-activo`, `fix-cooldown-tras-fallo-en-puntossolicitadoscache-y-stickerstatuscache`
