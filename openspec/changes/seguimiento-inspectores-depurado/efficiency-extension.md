# Efficiency requirement: incremental, quota-safe Seguimiento base (extension)

Date: 2026-09-19. User requirement: the depurado base must not saturate servers or maximum update quotas and must be incremental, with no unnecessary recomputation or re-fetching. This is an ACCEPTANCE CRITERION for the flag flip, not a nice-to-have.

## Measured / known facts
- Live sizes (parity run): ~1470 `evaluaciones`, 130 `inspectores`, ~1900 `survey_cali` names, ~2990 API sticker rows.
- Firestore project on Spark values: 50k reads/day, 20k writes/day (a 106k-read day already happened; see archive 2026-08-31-firestore-blaze-quota-429). Vercel plan unknown -> assume Hobby-like Blob limits (PUT only when content changed).
- One uvicorn worker (`backend/Dockerfile`), caches are per process; replica count of the web service is unverified.
- Engine compute is ~0.26 s CPU on realistic data: the cost is I/O and quota, not CPU.

## Where work is wasted today (source: read-only I/O audit)
1. `DepuracionCache` keys on object identity of the evaluaciones list; `EvaluacionesCache` builds a new list on every refetch even when the content is identical, so EVERY 5-minute stickers refresh forces a full recompute (including a full `survey_cali` scan).
2. Full `survey_cali` scan (~1.9k reads) per recompute, though only `nombre_evaluador` is used and it changes slowly.
3. Thundering herd in `DepuracionCache.get_or_compute`: `compute()` runs outside the lock with no in-flight marker; N concurrent admin requests each recompute and each scan survey (+ roster).
4. The 30-minute referencia download runs inside the cache lock (up to 30 s block).
5. Roster scanned twice in the same window when `_compute` runs without `build_payload`.
6. No ETag / gzip / Cache-Control; the whole body is re-serialized on every request; the Stickers tab downloads and ignores the `depuracion` PII block.
7. `generado_en` is date-granular: a same-day republish of the bundle would be ignored until restart (needs a content hash `huella`).
8. Frontend rebuilds Seguimiento on every tab open; the Stickers tab polls every 5 min (keeps the cache hot).
Ledger: flag ON with one open admin tab ~= 3.6k Firestore reads per 5-min refresh ~= 43k reads/hour vs the 50k/day cap. The flag MUST NOT be flipped before slices 09a/09b/10 land.

## Design (smallest change that meets the requirement)
Every derived value is keyed by input fingerprints; every Firestore-derived input has its own TTL and a single-flight lock; no network I/O under a shared lock.
- A. Content-stable stickers snapshot: after each fetch hash the payload (`blob_lkg.payload_hash` exists); if equal to the previous content keep the old snapshot object/version, else bump `snapshot_version`.
- B. Versioned component caches (pattern of `InspectoresCache`: own lock, serve-stale on error, `invalidate()`): `roster` (TTL 30 min, invalidated on admin create/setEnabled), `survey_names` (TTL 60 min), `evaluaciones` Firestore side (TTL 15 min), `referencia` (TTL 30 min, loaded outside the lock, keyed by content `huella` + `activa`).
- C. `DepuracionCache` key = `(depuracion_inputs_version, roster version, survey version, referencia huella, hoy)` with an in-flight marker so N requests produce exactly 1 compute. `depuracion_inputs_version` is the hash of the projected inputs `depurar()` consumes (judgment-day W5), not the whole-payload `snapshot_version`, which is kept for the ETag (D).
- D. HTTP: cache the encoded body bytes per `(snapshot_version, depuracion_version, role, degraded)`; `ETag` + 304 on `If-None-Match`; gzip (precompressed per snapshot preferred).
- E. `depuracion` is opt-in: only Seguimiento sends `?depuracion=1`; the Stickers tab gets neither the compute nor the PII. Viewers cost 0.
- F. Frontend: keep the last `{snapshot_id, stickers, depuracion}` at module level, render from it immediately, revalidate in the background, skip `render()` when `snapshot_id` is unchanged.
- Rejected: per-inspector delta recompute (unify/remap/collapse/alias index are cross-profile; 0.26 s compute; fingerprint gating gives 0 recomputes when static). Deferred: evaluaciones watermark + reconcile, `desde_utc` API delta, stale-while-revalidate, cross-instance snapshot sharing (PII + single instance today).
- Multi-instance: caches stay per process (N replicas => N x ledger); documented, not solved.

## Decisions taken (defaults; user delegated)
1. Firestore budget for this path: <= 10k reads/day (20% of the Spark cap); measure E, I, S again at parity time. `[SUPERSEDED 2026-09-19 by design O1 — ratified by the owner 2026-09-19]` the live measurement (E 1,470, I 131, S 1,924, U 99) showed 10k/day is not achievable by any TTL; the criterion is now **<= 20,000 reads per OPEN hour (the flag-OFF baseline), checked against the worst case of the projection**, with the daily figure printed for information. After the follow-up below the projection is worst case 8,081 per open hour (8,066 of scans + 15 of probes) and ~1,125 per open hour / 9,004 per 8-hour day with static inputs.
2. Freshness: roster 30 min, evaluaciones 15 min, survey names 60 min, API walk 5 min; admin writes invalidate immediately. `[2026-09-19]` plus: the evaluación NP join is at most one roster TTL old (D33); survey names and the Firestore evaluaciones list refresh through a count + newest-timestamp probe, forced full reconcile every 6 h (D34, D35).
3. No browser-disk cache of the PII body: in-memory manual conditional GET (`If-None-Match`); requires CORS `allow_headers += If-None-Match` and `expose_headers=["ETag"]` (verify in a browser).
4. Vercel plan unverified -> assume Hobby-like: Blob PUT only when content hash changed; referencia GET <= 2/hour; no HEAD/list in this path.

## Acceptance budgets (one process, >= 1 admin tab continuously open)
- Recomputes/hour with static inputs: 0 (exactly 1/day at the Bogota midnight `hoy` rollover).
- Firestore reads: `[MODIFIED 2026-09-19, O1]` <= 20,000 per open hour (worst case, every refresh finds a change; default budget of the harness, ratified by the owner 2026-09-19); with static inputs per hour <= 2 roster scans, <= 1 survey FULL scan (cold), <= 1 evaluaciones FULL scan (cold) plus <= 4 probes (~3 reads each), 0 `inspectores/{uid}` lookups for uids the roster holds. (Original text: <= 10k/day; <= 2 roster, <= 1 survey, <= 1-2 evaluaciones scans.)
- Probe-gated scans (D34): an unchanged `survey_cali` / `evaluaciones` costs one count() + one newest query (~3 reads) per TTL expiry and a full scan only when `(count, newest timestamp)` moved, at cold start and at the forced reconcile every 6 h; a probe failure serves stale (at most one retry per TTL); 20 concurrent requests at expiry: 1 probe, <= 1 scan.
- Atencion Sismo: at most one walk per 5 min TTL, 0 with no viewers.
- Blob: 0 PUTs when content is unchanged; referencia GET <= 2/hour.
- Payload: evaluaciones <= 4 MB raw; `depuracion` <= 400 KB raw for 400 profiles; gzip <= ~15% of raw (verify).
- Latency on cache hit: p95 <= 50 ms for 304, <= 250 ms for 200 with precomputed bytes.

## Tests that enforce the budgets (fakes with call counters + injectable clock)
1. 50 requests within TTL => 1 walk, 1 evaluaciones scan, 1 roster scan, 1 survey scan, 1 `depurar`, 1 referencia GET, <= 2 PUTs at cold start.
2. Past TTL with identical upstream => +1 walk, +0 depurar, +0 survey/roster scans, +0 PUT, same ETag, conditional request => 304 empty body.
3. One changed sticker => exactly +1 depurar and a new ETag.
4. `hoy` rollover => +1 depurar only.
5. New referencia `huella` (incl. same-day republish) => +1 depurar.
6. 20 threads at expiry behind a barrier => 1 walk, 1 depurar, <= 1 survey scan, <= 1 roster scan.
7. A slow `cargar_referencia` does not block a concurrent fast-path request.
8. Referencia GET failure keeps the last-good bundle, no recompute, <= 1 retry per TTL.
9. Viewer role or missing `?depuracion=1` => 0 depuracion cost.
10. Engine determinism: shuffled roster/sticker order => identical result; inputs not mutated; no clock/env/global reads (`hoy` is a parameter); perf canary 373 profiles + 3000 stickers < 0.5 s.
11. `Content-Encoding: gzip` when allowed; size ceilings hold.
12. Node tests: reopening with the same `snapshot_id` skips render; the Stickers tab does not request `depuracion`.

## Slice mapping
- 06 (done): the bundle `huella` (content hash) is a contract addition -> slice 09a (or a "06b").
- 07/08 (engine): determinism + purity + idempotency tasks (stable tie-break by `identidad_key` when `creado_en` is blank; no input mutation; perf canary). Signature stays `(stickers, roster_by_cedula, nombres_survey, referencia, hoy)`; the router owns fingerprints, the engine stays pure.
- 09a: versioned component caches, single-flight, referencia out of the lock, `?depuracion=1`, `huella`.
- 09b: ETag/304, gzip, encoded-bytes cache, CORS headers, budget tests 1-9 and 11.
- 10 (frontend): send `?depuracion=1` from Seguimiento only; snapshot retention + skip-render on unchanged `snapshot_id`; Node tests (12).
- 11 (parity harness): determinism + timing check on the real snapshot; measure E, I, S and record the actual read ledger.
- Gate: SEGUIMIENTO_DEPURACION must not be flipped before 09a, 09b and 10 are merged and the budget tests are green.

## Follow-up 2026-09-19 (quota savings, design D33-D35)
Live read-only measurement (one process, one admin tab open): E 1,470, I 131, S 1,924, U 99 -> 8,462 reads per open hour with the shipped TTLs (production today, flag off, is ~20,000). Three savings, each test-first: (1) the evaluación NP join is served from the roster component (U -> 0, fallback only for uids the roster cannot resolve); (2) `survey_names` replaces its timed 1,924-read scan by a count() + newest-`_updated_at` probe (~3 reads) with a forced reconcile every 6 h; (3) the same probe for the Firestore `evaluaciones` side (`timestamp`), implemented because the writer audit shows every mutation path creates a document with `serverTimestamp()` (client updates/deletes are denied by the rules, no Admin SDK writer in the repo) or moves the count. Rejected: a `_meta` watermark as change signal, a watermark/delta read (deferred, D31). Harness: projection through the real probe-gated components over the modeled day, budget per open hour (default 20,000, pending owner confirmation).
