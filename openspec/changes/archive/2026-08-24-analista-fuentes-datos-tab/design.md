# Design: analista-fuentes-datos-tab

Technical design for the admin-only **Analista** tab: a read-only source-health
inventory. Scope fixed by proposal; this document is the HOW at architecture
level.

## Architecture approach

Same shape as existing admin tabs (`stickers`, `usuarios`): lazy-init ESM
module rendered into pre-existing `<section>` on tab open, gated by CSS admin
selector plus defense-in-depth `isAdmin()` guard, refetching on every open.
Net-new server surface: one read-only serverless endpoint for live atencionsismo
probe. No framework, no bundler, no new dependency, no build step.

The core design insight: sources are heterogeneous in signal quality (rich meta
vs. file-presence-only vs. live probe), so the module normalizes each into one
uniform "source row" shape (ADR-1) and renders through existing
`sticker-list`/`sticker-row` building blocks.

## File-level plan

| File | Change | Notes |
|---|---|---|
| `web/index.html` | ADD Analista nav button + view section | Mirrors Stickers/Usuarios |
| `web/styles.css` | ADD selector to admin-gate list | One line added to existing comma list |
| `web/js/main.js` | ADD import + switchView() branch | Same lifecycle as initUsuarios |
| `web/js/analista.js` | NEW | Fetch orchestration + normalization + render |
| `api/source-status.js` | NEW | Admin-gated GET endpoint + live probe |
| `api/source-status.test.js` | NEW | Assert-based self-check |
| `api/reportados.js` | EDIT (1 line) | Export probeApi for reuse |

Nothing destructive. Revert = `git revert` of the one commit.

## 2. Normalized source-row data model (ADR-1)

Every source mapped to this shape for uniform rendering:

```js
{
  id: 'edan-f3',
  nombre: 'Google Sheet EDAN-F3',
  descripcion: 'Tabla normalizada...',
  ultima_lectura: '2026-08-24T10:15:00Z' | null,
  registros: 352 | null,
  estado_color: 'verde'|'amarillo'|'rojo',
  estado_label: 'conectado'|'sin metadata'|'sin consumidor'|'desactualizado'|'con errores'|'sin datos',
  detalle: 'extra note' | null
}
```

Colors map to `COLORS.status` in `utils.js`: verde #22c55e, amarillo #eab308, rojo #ef4444.

## 3. Per-source data-fetching plan

10 sources, fetched in parallel via `Promise.allSettled`:

1. **EDAN-F3**: meta.json → freshness rule → verde/amarillo
2. **Survey123**: meta.json same object → freshness rule
3. **Geocoding**: geocode_cache.json presence only → always amarillo `sin metadata`
4. **atencionsismo**: reportes_meta.json (snapshot) + `/api/source-status` live probe → probe overrides snapshot
5. **Global pipeline run**: _status.json ok flag → verde/amarillo/rojo
6-9. **Orphaned outputs**: asignaciones.json, cruce_gestor.json, cruce_criticos_survey.json, criticos_api.json → amarillo `sin consumidor`
10. **Israel**: fetchIsraelRecords() → verde if non-empty, amarillo if empty

Staleness threshold: 45 minutes (three missed 15-min runs) triggers `desactualizado` (amarillo).

Transport failures (network, Blob down) → amarillo `sin datos` (not rojo).

## 4. `api/source-status.js` (ADR-3)

Read-only GET endpoint.

**Role verification** (identical to `api/usuarios.js`):
- Missing/invalid token → 401
- Non-admin → 403
- Admin → proceed

**Probe** (ADR-4):
- Reuse `probeApi()` from `api/reportados.js` (one-line additive export)
- Basic auth from `VISITADOS_API_PASS`
- Against `https://atencionsismo.cali.gov.co/api/informe/json`

**Caching** (RELIABILITY-001 correction):
- `Cache-Control: private, no-store` on both ok:true and ok:false branches
- Earlier design proposed `public, s-maxage=60` but that was a shared-cache auth bypass (Vercel Edge Network caches by URL+method, so one admin's cached response could leak to unauthorized caller)
- No shared caching acceptable for low-traffic admin diagnostic view

**Response shape**:
```js
{ ok: true|false, status: 'conectado'|'con errores', detail: '...' | null, checked_at: '<ISO>' }
```

## 5. `web/js/analista.js` module plan (ADR-1)

`initAnalista(root, { getToken })`, mirroring `initUsuarios`.

- **Defense-in-depth guard**: `isAdmin()` check; return if non-admin
- **Shell**: sticker-page-head + "Actualizar" button + sticker-list container
- **Fetch orchestration** (`reload()`):
  - `Promise.allSettled([...])` over all 10 sources + 1 live probe
  - Each fetch fault-isolated (one failure doesn't abort others)
  - Rejected fetch (network, Blob error) → amarillo `sin datos` (not rojo)
  - Non-ok HTTP on live endpoint (401/403/network) → amarillo `sin datos`
  - Received `{ ok:false }` from probe → rojo `con errores`
  - Timeouts via `withTimeout()` (RESILIENCE-001 correction) → amarillo `sin datos`
- **Render**: `renderRows(rows)` → `<li class="sticker-row">` via sticker-identity + dot + estado_label + sticker-meta
- **Wiring**: #analista-refresh click → `reload()` with cache-bust on live-probe URL
- **No polling**: diagnostic view, not a monitor

## 6. Firebase / Firestore impact

**No changes.**

- `api/source-status.js` only verifies Firebase ID token (no Firestore reads/writes)
- Israel source uses existing `inspecciones_israel` collection + existing public-read rule
- All other sources are public Blob/JSON

## 7. Testing approach

Plain `node api/source-status.test.js` assert-based self-check.

Assertions:
1. Missing token → 401
2. Invalid token → 401
3. Non-admin → 403
4. Valid admin + bad VISITADOS_API_PASS → ok:false
5. Valid admin + correct env → ok:true (gated behind RUN_LIVE_PROBE env var; self-skips when unavailable)

Testability seam: exported `handle({ verify, probe })` factory for stubbing dependencies.

## ADR summary

- **ADR-1**: Uniform SourceRow + client-side normalization → one shape for heterogeneous signals
- **ADR-2**: Reuse sticker-list/section-bar; no new component (only inline background dot)
- **ADR-3**: One admin-gated read-only endpoint for live probe only (atencionsismo has cheap probe; others don't)
- **ADR-4**: Export and reuse probeApi from reportados.js (single source of truth for "alive vs down")
- **ADR-5**: 45-min staleness threshold, amarillo-only (rojo never inferred from age)
- **ADR-6**: Fetch failure ≠ source error (failed analista-side fetch → amarillo `sin datos`, not rojo)
- **RESILIENCE-001**: Timeout wrapper (15s) on all fetches to prevent hangs
- **RELIABILITY-001**: Cache-Control private, no-store (no shared-cache auth bypass)
</content>
