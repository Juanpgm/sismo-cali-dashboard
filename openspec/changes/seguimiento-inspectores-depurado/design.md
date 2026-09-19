# Design: seguimiento-inspectores-depurado

## Technical Approach

The backend owns **WHO** (identity, np, fase, estado, procedencia); the frontend keeps owning **WHEN / how many** (date range, KPIs, charts, PDF). The engine is a pure function of `(stickers snapshot, Firestore roster, reference bundle, today)` that emits an **identity index**, not an aggregated table. `GET /stickers-atencionsismo` gains a top-level `depuracion` block alongside the existing `evaluaciones` list; `seguimiento.js` stops deriving identity and consumes the index.

## Architecture Decisions

| # | Decision | Rejected alternative | Rationale |
|---|---|---|---|
| D1 | Emit an **index** (per-identity attributes + alias map), not per-professional counts | Full aggregated table endpoint | `seguimiento.js` filters `from`/`to` client-side; server-side counts would freeze the date filter, the chart and the per-professional PDF. Side effect: the notebook's `n_stickers` backfill inconsistency (explore Q6) disappears — counts are never precomputed. |
| D2 | Extend `GET /stickers-atencionsismo` with a top-level `depuracion` object | New `GET /inspectores-depurado` | Depuración must be derived from the **same** sticker snapshot the cache already holds. A second endpoint = a second cache = two payloads that disagree at TTL boundaries. |
| D3 | `depuracion` is assembled in the route handler, **outside** the cached `list` | Stamping `identidad_key` on each cached record | The cached list is what `blob_lkg` persists to the **public** Blob. Keeping depuración out of the list means the PII (nombres, cédulas, teléfonos, correos) can never reach Blob *by construction*, instead of by remembering to extend `redact_for_blob`. |
| D4 | Derived cache keyed by **object identity** of the sticker payload, no TTL of its own | A second TTL cache | `EvaluacionesCache` returns the *same list object* until it refetches, so `self._src is payload` is an O(1), exact invalidation signal. Recompute iff the snapshot, the reference generation, or the Bogotá date changed. Transitively inherits `EVALUACIONES_CACHE_TTL_SECONDS` (5 min) with zero drift. |
| D5 | Missing/corrupt reference ⇒ `ReferenciaBundle.vacia()`, never an exception | Raise / 503 | `blob_lkg.load_json` already returns `None` on *any* failure. An empty bundle makes every `en_vercel`/`en_fase2` false, so `np_fuente` degrades to `main`/`ninguno` and `fuente_dato="main"` — Seguimiento keeps working with raw identity plus a visible `depuracion.activa=false`. |
| D6 | `dias_inactivo` computed **inside** the cached build against Bogotá `hoy` | Per-request recompute (proposal risk row) | A ≤5-min staleness on a 7-day window is irrelevant; per-request recompute would force re-running the whole collapse on every request. `hoy` is part of the cache key, so the window still advances at midnight. |
| D7 | Fix two notebook bugs instead of porting them | 1:1 port | `tiene_sticker_valido` filters `origen == "sistema"` (audit's active bug); `estado_sugerido="activo"` keys **only** off `codigo` present, never off `tiene_sticker_valido` — resolves the D6 self-contradiction per the proposal. |
| D8 | Reference bundle published to Vercel Blob with **`access: 'private'`** (public beta), read back via authenticated `get()`/REST + `BLOB_READ_WRITE_TOKEN` — never a public URL | Public Blob with unguessable prefix | RESOLVED — see Open Questions. User chose private Blob over Firestore or public-with-obscurity after the blocker was raised; private access is real access control (confirmed against the `vercel-storage` skill: `get()` requires the token, no public URL is ever minted), so field minimization (dropping `correo`) is no longer required for security, only for scope. |

## Data Flow

    atencionsismo API ─┐
    Firestore roster ──┼─→ EvaluacionesCache (5 min, LKG Blob) ─→ evaluaciones[]  (public-safe)
    Firestore evals  ─┘            │  same list object
                                   ▼
    Blob referencia/ (private, token) ─→ cargar_referencia (30 min) ─→ depurar()  [PURE]
                                   │
                                   ▼
                          DepuracionCache (keyed by `is` + hoy + generado_en)
                                   │
                          route ──→ {ok, evaluaciones[], depuracion{}, degraded}
                                   │
                          seguimiento.js buildIdentityIndex(depuracion)

## File Changes

| File | Action | Description |
|---|---|---|
| `backend/app/services/inspectores_referencia.py` | Create | **I/O seam.** `cargar_referencia()` (Blob read + TTL + fail-soft) and `parse_bundle()` (pure schema validation). Two adapters exist: Blob and the in-memory fake tests inject. |
| `backend/app/services/inspectores_depuracion.py` | Create | **Deep module, zero I/O.** One public entry (`depurar`), six internal seams (below). |
| `backend/app/routers/stickers_atencionsismo.py` | Modify | `DepuracionCache`; assemble the `depuracion` block in `get_stickers_atencionsismo`; `SEGUIMIENTO_DEPURACION` env flag (rollback). |
| `scripts/publicar_referencia_inspectores.py` | Create | One-shot CLI (CSV/xlsx → normalized JSON → Blob). Manual, **not** a cron job. |
| `web/js/seguimiento.js` | Modify | `buildIdentityIndex` accepts `depuracion`; expandable GRUPO-EXTERNOS row. |
| `backend/requirements.txt` | Modify | `rapidfuzz>=3.9`. |
| `backend/app/main.py` | Modify | Wire `app.state.depuracion_cache`. |

## Interfaces / Contracts

```python
# inspectores_referencia.py — the seam
@dataclass(frozen=True)
class EntradaReferencia:
    cedula_key: str; nombre_norm: str; np: str
    entidad: str; codigo: str; pasos: tuple[int, ...]; no_persona: bool

@dataclass(frozen=True)
class ReferenciaBundle:
    vercel: tuple[EntradaReferencia, ...]
    fase2:  tuple[EntradaReferencia, ...]
    main:   tuple[EntradaReferencia, ...]   # rango_main, tarjeta_profesional, flags
    generado_en: str        # "2026-09-12", from the manifest, shown in the UI
    activa: bool
    motivo: str             # "" | "sin_token" | "sin_blob" | "manifiesto_invalido" | "esquema_invalido"
    codigos_duplicados: tuple[str, ...]      # D-P1 → revisión manual, excluded from remap

def parse_bundle(raw: dict) -> ReferenciaBundle | None       # pure; None on any shape error
def cargar_referencia(*, load_json=blob_lkg.load_json, ahora=None) -> ReferenciaBundle  # never raises
```

```python
# inspectores_depuracion.py — one public entry
def depurar(*, stickers: list[dict], roster_by_cedula: dict[str, dict],
            nombres_survey: Iterable[str], referencia: ReferenciaBundle,
            hoy: date) -> Depuracion
```

Internal seams, each independently testable (TDD units for `sdd-tasks`):

| Stage | Signature (sketch) | Rule source |
|---|---|---|
| 1 | `fusionar_identidad(stickers, roster, ref) -> dict[str, Perfil]` | cédula fix D-P2, nombre exacto only |
| 2 | `remapear_codigos(perfiles, ref) -> (perfiles, revision_manual)` | `rapidfuzz.token_sort_ratio >= 90`; skips `ref.codigos_duplicados` |
| 3 | `unificar_duplicados(perfiles) -> (perfiles, fusiones)` | `nombre_norm` exacto; survivor score `has_sticker > has_codigo > en_vercel > en_fase2 > not_no_persona > not_cedula_sospechosa`, tie → oldest |
| 4 | `colapsar_externos(perfiles, hoy) -> (perfiles, grupo)` | `(cedula_sospechosa OR no_persona) AND (sin ultimo_sticker OR dias_inactivo > 7) AND sin codigo` |
| 5 | `resolver_np`, `calcular_fase`, `estado_sugerido`, `fuente_dato` | explore Q1/Q2/Q3/Q5, with D7 applied |
| 6 | `alias_nombres(perfiles, nombres_survey) -> dict[str, str]` | survey dedupe: a survey name that maps to a real person's key never lands in GRUPO-EXTERNOS |

Response addition (auth-gated, never persisted to Blob):

```json
"depuracion": {
  "activa": true, "motivo": "", "referencia_generada_en": "2026-09-12",
  "inspectores": [{"identidad_key":"1234567","nombre_completo":"…","identificacion":"1234567",
                   "codigo":"041","entidad":"…","np":"P3","np_fuente":"fase2","fase":"Fase II",
                   "fase_np_faltante":false,"estado_sugerido":"activo","fuente_dato":"main+fase2+vercel",
                   "tarjeta_profesional":"…","num_telefono":"…","correo_contacto":"…","no_persona":false}],
  "grupo_externos": {"identidad_key":"GRUPO-EXTERNOS","n_colapsados":37,
                     "estado_sugerido":"grupo_externos_agrupado",
                     "fuente_dato":"grupo_agregado (main, 37 registros colapsados)",
                     "detalle":[{"nombre_completo":"…","identificacion":"…","motivo":"cedula_sospechosa",
                                 "ultimo_sticker":"2026-08-30"}]},
  "alias_nombres": {"juan perez": "1234567"},
  "revision_manual": [{"codigo":"097","motivo":"codigo_vercel_duplicado"}]
}
```

### Blob reference bundle

Pathname `referencia/inspectores/bundle.json`, uploaded with `access: 'private'` (single file — one atomic read, no partial-bundle state). No public URL is ever minted for it; `cargar_referencia()` reads it back via the Blob REST API with `BLOB_READ_WRITE_TOKEN` (the same credential the backend already holds for `blob_lkg`), exactly like fetching any other authenticated resource — there is no unguessable-prefix trick to maintain:

**Update:** the private read/publish now uses `BLOB_PRIVATE_TOKEN` (fallback `BLOB_READ_WRITE_TOKEN`) against `https://<storeid>.private.blob.vercel-storage.com`, verified live 2026-09-19 (403 without token, 200 with Bearer).

```json
{"schema": 1, "generado_en": "2026-09-12",
 "origen": {"vercel": "inspectores_vercel-app.csv", "fase2": "Listado verificado Fase 2.xlsx",
            "main": "tecnicos atencion sismo (12 sept 2026).csv"},
 "codigos_duplicados": ["097","127"],
 "vercel": [{"cedula_key":"1234567","nombre_norm":"juan perez","np":"P3","entidad":"…","codigo":"041"}],
 "fase2":  [{"cedula_key":"…","nombre_norm":"…","np":"P3","pasos":[1,2]}],
 "main":   [{"cedula_key":"…","nombre_norm":"…","rango":"P2","tarjeta_profesional":"…","correo":"…","no_persona":false}]}
```

Versioned by `schema` (an unknown value ⇒ `parse_bundle` returns `None` ⇒ degraded, never a crash) and dated by `generado_en`, surfaced in the UI next to `fuente_dato`. Since the bundle is private, `correo` MAY ship in `main` and feed `correo_contacto` (previously dropped for field-minimization under public Blob — no longer a security requirement, only a scope choice for `sdd-tasks`). `es_cuenta_no_persona(correo, nombre)` is still precomputed at publish time into the `no_persona` boolean so the depuration pipeline itself never needs the raw correo for classification.

## Frontend Changes (minimal)

`buildIdentityIndex({stickers, surveys, depuracion})`:
- `depuracion` present ⇒ `nameToCedula = depuracion.alias_nombres`, `eligibleCedulas` = keys of `inspectores`, and `profiles` built **directly from `depuracion.inspectores`**. The "primer no vacío gana" loop (`seguimiento.js:341-378`) is skipped entirely — `np` is never inferred from raw `insp.np`/`profesional.rango` again.
- `depuracion` absent or `activa:false` ⇒ current code path, byte-identical (this is also the Blob-restored cold-start path and the feature-flag-off path).
- `GRUPO-EXTERNOS` renders as one row whose expander lists `grupo_externos.detalle`.
- A badge next to the table when `activa:false`, and `referencia_generada_en` shown when `true`.

## Testing Strategy

| Layer | What | How |
|---|---|---|
| Unit (TDD) | Stages 1-6, `parse_bundle` | Pure functions, plain dict fixtures. Edge cases required: empty bundle, unknown `schema`, malformed rows, cédula ties, name collisions, `dias_inactivo` boundary (exactly 7), survey-name dedupe collisions, `origen` other than `sistema`/`firebase`. |
| Unit | `cargar_referencia` | Inject a fake `load_json` returning `None` / garbage / valid. |
| Unit | `DepuracionCache` | Same list object ⇒ no recompute; new object / new `hoy` / new `generado_en` ⇒ recompute. |
| Integration | route | FastAPI TestClient: flag off, reference missing, reference present, Blob-restored `degraded` payload. Assert **no PII in the Blob-persisted copy**. |
| Parity | notebook | Golden fixture: run `depurar` over the 12-sep snapshot, diff `np`/`fase`/`estado_sugerido` against the xlsx; documented diffs limited to D7. |
| Front | `seguimiento.test.mjs` | `node:assert` self-check, both branches of `buildIdentityIndex`. |

## Migration / Rollout

No data migration. Order: (1) publish the bundle with the CLI, (2) deploy the backend with `SEGUIMIENTO_DEPURACION=0`, (3) verify parity in logs, (4) flip to `1`, (5) ship the front. Rollback = flip the env var; the front already handles an absent `depuracion`.

## rapidfuzz

`backend/Dockerfile:25` is a bare `RUN pip install --no-cache-dir -r requirements.txt` — no lockfile, no hash pinning, no extra build step, so adding `rapidfuzz>=3.9` is sufficient and Railway needs no change. `python:3.12-slim` is glibc/Debian and rapidfuzz publishes `manylinux` cp312 wheels, so no compiler is required. **Verify in the deploy log on the first build** (proposal risk row) — `refresh_data.py`'s `ModuleNotFoundError` precedent shows this repo has no test that exercises the real image.

## Open Questions

- [x] **RESOLVED.** Vercel Blob reads for the *sticker/roster* cache are public (`blob_sync.py:71`), which is why `InspectoresCache` deliberately avoids Blob. For the reference bundle specifically, the user chose **Vercel Blob with `access: 'private'`** over Firestore or public-with-obscurity — confirmed via the `vercel-storage` skill that private access requires an authenticated `get()`/token read, never a public URL, so this is real access control, not obscurity.
- [x] Decision 8 (proposal) confirmed by the user when approving the proposal as a whole: the backend owns `np`/`np_fuente` and the front stops filling from `rango`.
- [x] `correo_contacto`: no longer blocked by exposure risk now that the bundle is private. It ships from the `main` snapshot into `correo_contacto`. Only remaining open item: confirm with the user whether `correo_contacto` is actually in scope for this delivery, since it wasn't part of the original 8 decisions.
