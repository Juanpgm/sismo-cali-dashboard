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

---

# Addendum — Extension 2026-09-19 (complete base)

Baseline: backend HEAD `ccbc71e`. Source of truth: `explore-extension.md`. Nothing above is deleted;
D9-D20 extend D1-D8 and, where stated, supersede parts of them. **No open questions remain.**

## Architecture Decisions D9-D20

| # | Decision | Rejected alternative | Rationale |
|---|---|---|---|
| D9 | Universe = Firestore roster ∪ `referencia.main`, keyed by `cedula_key`. Firestore first; a `main` row with an existing key only backfills empty fields | Keep `main` as a pure overlay (status quo) | The shipped engine serves 129 of 373 rows and `n_colapsados=0`. The user's decision is a complete base. **Supersedes** the spec requirement "Identity Anchor Is Firestore `identificacion`" and the proposal's "Identidad única" scope line — NOT design D1 (index, not counts) nor the "Criterio humano" D1 row (current titular, no history). The email-derived-cédula prohibition is unchanged |
| D10 | Run D-P2 unification AFTER overlays, the Fase 2 cédula fix, sticker attribution and remap (notebook order) | Keep unification first and rank "Firestore-backed" first | Before overlays, `en_vercel`/`en_fase2`/`n_stickers`/`has_codigo` are all false, so the survivor score is degenerate and the winner is effectively arbitrary — a `main` row can beat the Firestore row. A Firestore-first ranking would fix *that* symptom but diverge from the notebook on `identificacion` and `nombre_completo`, which are exactly two of the ≥99% parity columns. Notebook order makes the score meaningful AND parity-checkable. Firestore-backed is kept as the LAST tie-break (after the notebook's `creado_en`) so determinism is added without displacing a parity-bearing rule. **Expected production behavior (review 2026-09-19):** the roster sends `creado_en=""` (`stickers_atencionsismo.py`) while `main` rows carry a real date, and a blank never beats a real date, so for a same-name cross-source pair with no sticker/code/Vercel difference the MAIN cédula becomes `identidad_key`/`identificacion` and the roster cédula is exported in `cedulas_unificadas` (the frontend maps it through `cedulaFusionadaACedulaSurvivor`). Firestore-backed only decides a FULL tie (both dates blank/equal) |
| D11 | Sticker attribution resolves the professional cédula through a `cedula_key` alias index (current key + pre-Fase-2-fix keys + `cedulas_unificadas`) | `perfiles.get(identificacion)` exact-string lookup (status quo) | With the full universe an unresolved cédula leaves `ultimo_sticker=None`, which makes active people match the collapse predicate and disappear into GRUPO-EXTERNOS. Attribution happens on the pre-fix cédula (notebook step 3) and must survive both the fix and unification. **Key contract (review 2026-09-19, C1):** `_cedula_key` is zero-PRESERVING (digits only, pure `.0` tail dropped) — an exact mirror of the frontend's `cedulaKey` — and is what keys profiles, overlays, the remap, `identificacion`/`identidad_key` and `cedulas_unificadas`. The zero-STRIPPED form (`_cedula_alias_key`) is used only as the FALLBACK of the sticker alias index (spec: `"0012345"` and `"12345"` match): exact key first; a stripped key claimed by two different profiles is ambiguous and attributes nothing (order-independent); when the fallback resolves a sticker, the sticker's own key form is exported in that profile's `cedulas_unificadas` so the frontend can route it. A roster duplicate's absorbed cédula is always exported. Overlays stay exact-key (pre-slice behavior) |
| D12 | `entidad` = first non-empty of Vercel-matched-by-cédula, Firestore, `main` | Vercel by any match (cédula or name) | The notebook maps `entidad` from `dict(zip(vercel.cedula_key, vercel.entidad))` only. Live parity already showed 1 mismatching row from a name-only match. A name-only Vercel hit must not carry `entidad` |
| D13 | Remap clears the código from the wrong current holder; new `revision_manual` motivos `remap_sin_duenio` and `remap_conflicto` | Assign to the rightful owner and leave the duplicate in place | Two profiles holding the same código break the "Vercel is the golden rule" invariant and inflate `activo` (código presence alone drives `activo`, per D7). Clearing first makes the código single-valued by construction; the two motivos surface the cases a machine must not decide |
| D14 | Index `main`/`vercel`/`fase2` by `cedula_key` and by `nombre_norm` in dicts built once | Keep the linear `_buscar_entrada` scan | The universe goes from ~129 to ~373 profiles over a 743-row `main`; the linear scan is O(n·m) on every stage. Dict indexes make it O(n) and are a prerequisite for the payload/latency budget in D16 |
| D15 | Reference bundle stays `schema: 1`; the seven new fields are optional with empty defaults and a tolerant parse in both directions | Bump to `schema: 2` | The DEPLOYED `parse_bundle` returns `None` for any unknown `schema`, so a bump silently disables depuración in production for the whole window between republish and deploy. Additive optional keys are compatible both ways; deploy-then-republish is kept as a safety margin, not a correctness gate |
| D16 | `depuracion.activa=false`, `motivo="stickers_degradados"`, empty `inspectores`, when the sticker snapshot is the redacted LKG restore | Compute anyway and let the front show it | In degraded mode stickers are unattributable, so with the full universe every code-less profile becomes `candidato_desactivacion` and every suspicious one is wrongly collapsed — a confidently wrong table is worse than a declared gap. Distinct from a missing REFERENCE bundle, which still computes (D5) |
| D17 | Front seeds rows from `depuracion.inspectores`; KPIs compute over rows WITH activity; mass PDF export defaults to rows with activity | Show only rows with activity (status quo), or let KPIs count seeded rows | Decision 1 of `explore-extension.md` is "show all zero-activity people, with an estado filter". Seeding without changing KPI semantics would move "profesionales activos" from 116 to 373 and divide every average by the wrong denominator; the 200-row PDF cap would also start truncating silently |
| D18 | The Fase 2 cédula fix adopts the Fase 2 cédula but keeps `cedula_sospechosa` computed from the ORIGINAL `main` cédula | Recompute the flag after the fix | Recomputing clears the flag on exactly the rows that most need review, and changes collapse membership away from notebook parity. The original key stays resolvable (D11) so attributed stickers survive |
| D19 | Duplicate `cedula_key` across two `main` rows: first-wins, no field overwrite, `revision_manual` entry `cedula_duplicada_main`; a `main` row without digits yields `main_sin_cedula` | Last-wins, or silent drop | A silent overwrite makes the universe non-deterministic in row order and loses a person. First-wins plus a revision item is deterministic and auditable |
| D20 | Parity is measured against an explicit divergence register: D7, D-REMAP, D-EXENTOS, D-CEDDEC, D-SURVFECHA and D-ENTIDADTRIM. Anything outside the register is a failure | Accept an aggregate ≥99% score | The engine intentionally differs from the notebook in the places enumerated here; without an enumerated register a real regression can hide inside the 1% tolerance. `exentos` (the `survey_cali` exemption the notebook does not have) is registered as D-EXENTOS: `n_colapsados` MUST be 252 minus the enumerated exempt survivors, and each survivor MUST be listed by key in the harness report. **D-CEDDEC** (2026-09-19 review, C3; REPLACES the earlier D-CEDSOSP, which registered the wrong rule `^(\d+)\.0+$`): float artifact `.0` tail dropped in cédulas; thousands-separated dots are just dots. ONE shared rule (`backend/app/services/cedula_utils.py`, regex `^\s*(\d+)\.0\s*$`, ASCII digits) used by `_cedula_key`/`_digitos`/`cedula_sospechosa`, `_texto_opcional`, the publisher's `_sin_sufijo_decimal`/`_campo_id`/`_solo_digitos`, and mirrored verbatim by the frontend `cedulaKey` (a backend test asserts the JS source embeds the exact regex). A float cell stringifies with EXACTLY one `.0` ("31837630.0"); `"166.000"` is the Colombian thousands form of cédula 166000, `"12.000"` -> `12000`, `"12345.00"` -> `1234500`. Old-vs-new behaviour equals 04fbf2e (`re.sub(r"\D", ...)`) for every realistic shape except the single divergence: a lone trailing `.0` is now dropped, so `"1234567890.0"` was suspicious (11 digits) and is fine (10, starts with "1"), `"12345.0"` was fine (6 digits, spurious trailing 0) and is suspicious (5). Roster profiles are keyed by `_cedula_key` like `_perfil_desde_main`, and the RAW roster `identificacion` is exported once in `cedulas_unificadas` when it differs from the key. Digits are ASCII-only on both sides (Arabic-Indic / full-width digits are not digits, matching JS `\d`; unregistered pre-fix behaviour kept them in Python only, a backend/frontend mirror gap). Affected rows MUST be listed by key in the harness report; pinned by `test_cedula_sospechosa_pinned_behavior` and `test_c3_*`. Round-3 review notes: the frontend/backend mirror covers the digit extraction and the float-artifact rule, NOT exotic whitespace around a ".0" tail (BOM, NEL, control chars; JS `\s` vs Python `re.ASCII` + `str.strip()`), a known, accepted divergence of very low realism (a BOM-prefixed cell means the source was read without utf-8-sig); `stickers_atencionsismo.cedula_key` still uses a plain `re.sub(r"\D", ...)`, so a roster `identificacion` "1234567.0" is keyed "12345670" in `inspector_profile_by_identificacion` / `same_cedula_match` but "1234567" by `_cedula_key`/`cedulaKey` — a known limitation until task 10.12 routes it through `cedula_utils.solo_digitos`; a roster `identificacion` such as "1.234.567" used to be echoed verbatim in the payload\'s `identificacion` (rendered by seguimiento.js) and is now the normalized key "1234567", the raw form being exported in `cedulas_unificadas`. **D-SURVFECHA** (review W6): `_elegir_survivor` parses `creado_en` (an unparseable date counts as blank and never beats a real one) instead of the notebook's lexicographic `min` over any truthy string; affects only the `identificacion`/`nombre_completo` tie-break of the survivor, intended; pinned by `test_w6_*`. **D-ENTIDADTRIM** (review S2): `np`/`entidad` read from the Vercel / Fase 2 / `main` overlays are `_txt`-trimmed in `superponer_referencia` (the notebook kept surrounding whitespace); intended; pinned by `test_s2_overlay_trims_np_and_entidad` |

## Pipeline Order (authoritative, supersedes the Interfaces stage table's implied order)

1. Build universe — Firestore roster profiles, then `main` profiles for unseen `cedula_key` (D9, D19).
2. Overlay Vercel and Fase 2 by `cedula_key`, else by exact `nombre_norm`; first wins (D14 indexes).
3. Attribute sticker aggregates by the pre-fix cédula through the alias index (D11).
4. Fase 2 cédula fix for name-only matches; keep `cedula_sospechosa` from the original cédula; register the old key (D18).
5. `remapear_codigos` — clear the wrong holder, emit `remap_sin_duenio` / `remap_conflicto` (D13).
6. `unificar_duplicados` — exact `nombre_norm`, full survivor score, register `cedulas_unificadas` (D10).
7. `entidad` (D12), `resolver_np`, `calcular_fase`, `estado_sugerido` (D7 unchanged), `fuente_dato`.
8. `colapsar_externos` (+ `exentos`, D20).
9. `alias_nombres` — built last, logged redacted at INFO.

## File Changes Per Slice

| Slice | File | Action | Description |
|---|---|---|---|
| 06 | `backend/app/services/inspectores_referencia.py` | Modify | Seven optional fields on `EntradaReferencia`; tolerant `_parse_entrada` (D15) |
| 06 | `scripts/publicar_referencia_inspectores.py` | Modify | Emit the new fields; string dtypes already fixed in `ccbc71e` |
| 06 | `backend/tests/services/test_inspectores_referencia.py` | Modify | Old-bundle / new-bundle / wrong-typed-field cases |
| 06 | `backend/tests/test_publicar_referencia_inspectores.py` | Modify | Leading zeros, long numerics, new fields present |
| 07 | `backend/app/services/inspectores_depuracion.py` | Modify | Universe from `main`, alias index, unify-after-overlays, `entidad`, dict indexes (D9-D12, D14) |
| 07 | `backend/tests/services/test_inspectores_depuracion.py` | Modify | Universe, attribution, unification-order and `entidad` units |
| 08 | `backend/app/services/inspectores_depuracion.py` | Modify | Remap holder-clearing, new motivos, Fase 2 cédula fix (D13, D18, D19) |
| 08 | `backend/tests/services/test_inspectores_depuracion.py` | Modify | Remap and cédula-fix units |
| 09 | `backend/app/routers/stickers_atencionsismo.py` | Modify | Degraded gate, admin-only gate assertions, `alias_nombres` log redaction (D16) |
| 09 | `backend/tests/routers/test_stickers_atencionsismo.py` | Modify | Degraded, admin-vs-viewer, PII-not-in-Blob, payload-size |
| 10 | `web/js/seguimiento.js` | Modify | Row seeding, KPI denominators, estado filter, export scope, motivos, banner (D17) |
| 10 | `web/js/seguimiento.test.mjs` | Modify | Seeding, KPI, filter, export, motivos, banner cases |
| 10 | `web/js/seguimiento-perf.test.mjs` | Modify | 400-row render/filter/sort case |
| 11 | `scripts/parity_inspectores_depurado.py` | Create | Compare `depurar()` output with the xlsx; per-column mismatch lists + divergence register (D20) |
| 11 | `backend/tests/services/test_inspectores_depuracion_parity.py` | Modify | Assert the register, not just an aggregate score |

## Rollout Order

1. **Deploy** each backend slice (06→09) to production with `SEGUIMIENTO_DEPURACION` unset/`0`. The
   response stays byte-identical, so every merge is invisible to users.
2. **Republish** the bundle with `scripts/publicar_referencia_inspectores.py` once slice 06 is
   deployed. Record the resulting `generado_en`.
3. **Live parity** — run slice 11's harness against the live `depurar()` output and the xlsx.
   The acceptance criteria below are the gate.
4. **Flip the flag** to `SEGUIMIENTO_DEPURACION=1` only after step 3 passes.
5. **Merge the chain** — slice 10 (frontend) merges into the tracker last; the tracker
   `feat/seguimiento-inspectores-depurado` is the only branch that merges to `main`.

Rollback at any point = set `SEGUIMIENTO_DEPURACION=0`. The bundle and the merged code go inert.

## Parity Acceptance Criteria

| Check | Threshold |
|---|---|
| `np`, `np_fuente`, `fase`, `fase_np_faltante`, `codigo`, `fuente_dato`, `identificacion`, `entidad` | ≥ 99% on matched keys |
| `estado_sugerido` | ≥ 99% excluding the enumerated D7 / D-REMAP divergences |
| `tarjeta_profesional`, `num_telefono`, `correo_contacto` | ≥ 99% where the source is non-empty |
| `nombre_completo` | compared through `normalizar_nombre` |
| Row count | 373 ± the enumerated Firestore-only extras (13 today) |
| `n_colapsados` | 252, with the same 3 código-holding exclusions, minus the enumerated D-EXENTOS survivors |

Every shortfall MUST be reported as an explicit list of mismatching keys per column — an aggregate
percentage alone does not satisfy the gate (D20).

## Open Questions

None. The five decisions in `explore-extension.md` are final and are encoded as D9-D20 above.
(Superseded for the efficiency extension only: see "Open Questions (Efficiency Extension)" at the end
of this file — exactly one item, O1, is open and gates the flag flip.)

---

# Addendum — Efficiency extension 2026-09-19

Source of truth: `efficiency-extension.md` (acceptance criterion for the flag flip, not a nice-to-have).
Baseline: this branch (`…-08-engine-remap`), backend HEAD `ff474a1`. Append-only: nothing above is
deleted; D21-D32 extend D1-D20 and, where marked `[MODIFIED]`, supersede parts of D4/D6 and of the
Rollout Order. The engine stays a pure function (D29); every derived value is keyed by input
fingerprints; every Firestore-derived input has its own TTL and a single-flight lock; no network I/O
runs under a shared lock. Live sizes (parity run): ~1470 `evaluaciones` (E), 130 `inspectores` (I),
~1900 `survey_cali` names (S), ~2990 API sticker rows. Engine CPU is ~0.26 s: the cost is I/O and
quota, not CPU. One uvicorn worker (`backend/Dockerfile`); caches are per process.

## Architecture Decisions D21-D32

| # | Decision | Rejected alternative | Rationale |
|---|---|---|---|
| D21 `[ADDED]` | **Content-stable stickers snapshot.** After every atencionsismo fetch, hash the FULL in-memory served payload with `blob_lkg.payload_hash`; if equal to the previous content, keep the previous list object AND `snapshot_version`; otherwise bump it. `degraded` is part of the version. The hash is over the UNREDACTED payload (the redacted Blob copy blanks `inspector.np`, which feeds classification, so hashing it would hide a real change); it never leaves the process except folded into the ETag composite (D26). The existing hash of the REDACTED copy that gates the Blob PUT is unchanged and separate. No per-fetch volatile field may enter the hashed payload (asserted by a test). A change of row ORDER alone bumps the version — conservative and accepted (costs one recompute; the engine itself is order-independent, D29) | Object identity (`self._src is payload`, D4) as the recompute signal | `EvaluacionesCache` builds a NEW list on every 5-minute refetch even when the content is identical, so every refresh forced a full recompute including a full `survey_cali` scan (audit waste #1). Hashing ~3k rows costs milliseconds against 0.26 s of compute plus ~3.6k Firestore reads. The version is internal: it is NOT added to the body (that would break the flag-off 4-key byte-identity of D2/3.8/10.10); the wire identity is the ETag (D26) |
| D22 `[ADDED]` | **Versioned component caches**, one per Firestore-derived input, following the `InspectoresCache` pattern (own lock, serve-stale on error, `invalidate()`): `roster` TTL 30 min (invalidated on admin create/setEnabled), `survey_names` TTL 60 min (`nombre_evaluador` only — Firestore bills per document, so a field projection saves nothing), `evaluaciones` Firestore side TTL 15 min, `referencia` TTL 30 min (keyed by content `huella`, D24). Each exposes a `version` = `payload_hash` of its content: a refetch with identical content keeps the version, so an expired TTL alone never forces a recompute. Failed refreshes reuse the existing `failure_backoff_s` convention of the atencionsismo `EvaluacionesCache` (serve last-good, no hot retry). One roster scan is shared by `_compute` and `build_payload` (audit waste #5). A small generic primitive (proposed `backend/app/services/versioned_cache.py`) is preferred over four copies. The roster picker's own 5-minute `InspectoresCache` behavior is NOT changed; the create/setEnabled hook invalidates both | One global TTL; recompute every input on every 5-minute stickers refresh; extending `InspectoresCache`'s TTL for everyone | Inputs change at very different rates (a survey name set drifts slowly, the roster changes only on admin writes, stickers move every few minutes). Independent TTLs + content versions are what make "0 recomputes/hour with static inputs" true. Admin writes still show immediately through `invalidate()` |
| D23 `[ADDED]` `[MODIFIED: D4, D6]` | **`DepuracionCache` key = `(snapshot_version, roster_version, survey_version, referencia_huella, hoy)`** plus an in-flight marker: the first caller for a key computes OUTSIDE the lock, concurrent callers for the SAME key wait on the marker and receive the same result (exactly 1 `depurar` and 1 set of scans for N requests). The marker is always cleared in `finally`; if the leader raises, waiters serve the last-good result when one exists, otherwise observe the same outcome the leader's caller does, and the next request retries (no deadlock, no poisoned marker). Only the current key (plus last-good) is retained. No TTL of its own — freshness is bounded by the component TTLs (D22) | Keep the identity key (`is`, D4); a global lock held during compute; compute per request | Today `compute()` runs outside the lock with no in-flight marker, so N concurrent admin requests each recompute and each scan survey and roster (audit waste #3). `[MODIFIED]` D4: "key by object identity, inherits the 5-minute TTL" is replaced by the content key above. D6 is KEPT: `hoy` (Bogotá) stays in the key, so the midnight rollover costs exactly one recompute |
| D24 `[ADDED]` | **`referencia` is loaded OUTSIDE the cache lock**, single-flight: the TTL check under the lock is O(1); at most one caller downloads, everyone else reads the last-good bundle. The loaded bundle carries `huella` = `payload_hash` of the parsed raw JSON (computed on read, NOT stored in the published file, so old bundles keep working and no republish is needed). The `huella` (not the day-granular `generado_en`) is what keys D23 — a same-day republish is detected on the next TTL refresh. A failed load keeps the last-good bundle, does not trigger a recompute, and is retried at most once per TTL (the existing "advance `_referencia_at` even on failure" behavior is retained and now covered by a test); a degraded bundle is adopted only when there was no good one. Budget: ≤ 2 GETs/hour, no HEAD/list in this path | Load under the lock (status quo: up to 30 s block); key by `generado_en` | Audit waste #4 and #7. Under the lock a slow Blob GET blocks even requests that would have hit the fast path; `generado_en` is date-granular, so a same-day republish would be ignored until restart |
| D25 `[ADDED]` | **`depuracion` is opt-in per request: `GET /stickers-atencionsismo?depuracion=1`**, exact value `1` only (anything else, repeated or absent, means "not requested"). It composes with — never replaces — the existing gates: flag `SEGUIMIENTO_DEPURACION`, admin role, live (non-degraded) snapshot. A request that does not opt in performs 0 depuración work (no `depurar`, no survey scan, no referencia GET, no roster scan beyond what the existing sticker normalization already does) and its body is byte-identical to the flag-off shape. Viewers cost 0 even when they send the param | Always-on for admin (status quo); a second endpoint | The Stickers tab uses the same endpoint and today downloads and ignores the PII `depuracion` block (audit waste #6). D2 stands: it is still ONE snapshot and ONE cache — the parameter is only a projection switch, not a second cache |
| D26 `[ADDED]` | **Encoded-bytes cache + ETag/304 + gzip.** Cache the fully encoded body per `(snapshot_version, depuracion_version, role, degraded)` and per encoding; gzip is precompressed once per key (never re-compressed per request). `ETag` = quoted, truncated sha256 of that key (+ an encoding suffix, because strong validators must differ per representation); `depuracion_version` is `"none"` for a non-opt-in request. `If-None-Match` handling accepts a list, weak `W/` prefixes and `*`; a match returns 304 with an empty body and the ETag; authentication and role resolution ALWAYS precede the 304 decision (an unauthenticated request never gets a 304, and one role's ETag never validates another role's body). Headers: `Cache-Control: no-store` (no browser-disk copy of PII — the client does an in-memory MANUAL conditional GET), `Vary: Authorization, Accept-Encoding`, `Content-Encoding: gzip` only when `Accept-Encoding` allows it (`q=0` and absent header mean identity). The serialized body of the flag-off / non-opt-in path stays byte-identical to the pre-extension `JSONResponse` output | `GZipMiddleware` alone (re-compresses per request, no 304); a time-bucket weak ETag; `max-age` browser caching | Audit waste #6: the whole body is re-serialized on every request. A 304 needs no body and no serialization; a cache hit is bytes. `no-store` is deliberate: the body carries names, cédulas, phones and emails |
| D27 `[ADDED]` | **CORS**: `CORS_ALLOW_HEADERS` (`backend/app/config.py`) gains `If-None-Match`; the `CORSMiddleware` call in `backend/app/main.py` gains `expose_headers=["ETag"]`. Existing headers stay. Verified in a real browser (Chromium/Playwright, cross-origin Vercel→Railway shape), not only with `TestClient` | Assume same-origin; ship without a browser check | The frontend (Vercel) and backend (Railway) are cross-origin. Without `allow_headers` the preflight of a request carrying `If-None-Match` fails and the fetch is blocked; without `expose_headers` JS cannot read the ETag, so 304/skip-render silently never engages. The frontend MUST degrade to a plain full render when the ETag is unreadable |
| D28 `[ADDED]` | **Frontend snapshot retention + skip-render.** `web/js/seguimiento.js` keeps the last `{snapshot_id, stickers, depuracion}` at module level, where `snapshot_id` is the opaque ETag string of D26. On tab open it renders from the retained value immediately, revalidates in the background with `If-None-Match`, and skips `render()` when the answer is a 304 or an equal ETag; a changed ETag re-renders once. Only `?depuracion=1` is sent, and only from Seguimiento; the Stickers tab neither requests nor receives `depuracion`. Retention is in memory only and is cleared on sign-out or role change (the retained value is admin PII). `fetchEvaluacionesOnce`/`tagFuente` (`web/js/stickers.js`) surface the ETag additively | `sessionStorage`/IndexedDB persistence; a `snapshot_id` key in the body | Audit waste #8: the tab rebuilds on every open and the Stickers tab's 5-minute poll keeps the cache hot. Persistent browser storage would put PII at rest; a body key would break the flag-off byte-identity (D21) |
| D29 `[ADDED]` | **Engine determinism, purity and idempotency.** `depurar()` keeps the signature `(stickers, roster_by_cedula, nombres_survey, referencia, hoy)`; the router owns fingerprints, the engine stays pure. Contract: (a) the result is independent of the order of the roster and of the stickers — `referencia` row order is part of the INPUT (its `huella` is order-sensitive), which is what keeps D19's "first-wins" well-defined; (b) survivor tie-break chain, in order: D-P2 score → oldest parsed `creado_en` (blank never beats a real date, D-SURVFECHA) → Firestore-backed (D10) → ascending `identidad_key` as the TERMINAL total order (so a full tie with blank `creado_en` on both sides is decided by the key, never by iteration order); (c) every other first-wins choice that depends on iteration order (`alias_nombres` collisions, fuzzy-remap candidates) iterates in canonical (sorted) order; (d) every emitted list is canonical (`inspectores` by `identidad_key`, `revision_manual` by `(motivo, codigo, identidad_key)`, `grupo_externos.detalle` by `identificacion`); (e) no input mutation, no result aliasing of inputs, no clock/env/global reads (`hoy` is a parameter); (f) `depurar(x) == depurar(x)`. Registered divergence **D-TIEBREAK**: the notebook's tie resolution follows row order; the engine's is the total order above. It can only change a survivor in a FULL tie; the harness lists every such key (D20 style) | Rely on dict insertion order; Firestore-backed as the terminal tie-break | The D23 cache is only sound if equal fingerprints imply equal results, and the engine may run in any process on any roster ordering. Reconciles with D10: Firestore-backed stays ahead of `identidad_key`, so the earlier "Firestore-backed decides a FULL tie" text still holds whenever exactly one side is Firestore-backed |
| D30 `[ADDED]` | **Per-inspector delta recompute is REJECTED.** Evidence: (1) unification, remap, collapse and the `alias_nombres` index are cross-profile — one changed sticker can move a profile into or out of `GRUPO-EXTERNOS`, flip a survivor, or change an alias winner, so a correct delta needs a dependency graph and a parallel code path that must be kept at parity with the full path; (2) the full compute is ~0.26 s CPU, so a delta saves at most a few hundred milliseconds once per real change; (3) the quota cost lives UPSTREAM of the engine (scans in D22), which a delta of the engine does not touch; (4) fingerprint gating (D21-D23) already yields 0 recomputes with static inputs and exactly 1 per real change | Incremental engine keyed per `identidad_key` | Smallest change that meets the requirement. Parity risk (D20) rises with every second code path |
| D31 `[ADDED]` | **Deferred, not planned in this change:** the `evaluaciones` watermark + reconcile (read only changed documents — see O1, a PROMOTION CANDIDATE), the atencionsismo `desde_utc` API delta, stale-while-revalidate, cross-instance snapshot sharing (PII in shared storage, single instance today) | — | Each adds a second source of truth or storage of PII; none is needed for correctness |
| D32 `[ADDED]` | **Multi-instance note.** All caches (D21-D24) are per process. If the web service ever runs N replicas the ledger is N× the single-process figures. The replica count is unverified today; this is documented, not solved. The parity harness measures ONE process | Sharing caches across replicas | Would require shared storage of PII or a coordination layer; out of scope (D31) |

`[MODIFIED]` markers above supersede, for the depuración path only: D4 ("keyed by object identity … inherits
the 5-minute TTL") by the content key of D23, and the Data Flow line `DepuracionCache (keyed by is + hoy +
generado_en)` by `(snapshot_version, roster_version, survey_version, referencia_huella, hoy)`. D6 (`hoy` in
the key) and D3 (PII outside the cached, Blob-persisted list) are unchanged.

## Efficiency Data Flow (supersedes the Data Flow diagram for the depuración path)

    atencionsismo API (5-min walk) ─→ snapshot: payload_hash ─→ snapshot_version   (D21)
    roster (30 min) ─────────────────→ version                                       (D22)
    survey names (60 min) ───────────→ version                                       (D22)
    evaluaciones Firestore (15 min) ─→ version                                       (D22)
    referencia Blob (30 min, OUTSIDE the lock) → huella                               (D24)
                     │  only when ?depuracion=1 + flag + admin + live snapshot     (D25)
                     ▼
    DepuracionCache key (snapshot_version, roster, survey, huella, hoy) + in-flight marker   (D23)
                     ▼
    encoded-bytes cache per (snapshot_version, depuracion_version, role, degraded, encoding)
                     ▼  ETag / 304 / gzip                                             (D26, D27)
    seguimiento.js: retained {snapshot_id = ETag, stickers, depuracion}; skip-render  (D28)

## Efficiency Acceptance Budgets

Scope: one process, at least one admin tab continuously open. "Static inputs" = no upstream change.
Every row marked "test" is enforced with fakes that count calls and an injectable clock (deterministic,
no real time); rows marked "ledger" are measured on live data by the parity harness (slice 11).

| Budget | Limit | Enforced by |
|---|---|---|
| Recomputes per hour, static inputs | 0 (exactly 1 per day at the Bogotá midnight `hoy` rollover) | test |
| Recomputes per real change | exactly 1 per changed fingerprint (sticker, roster, survey, `huella`, `hoy`) | test |
| N concurrent requests at expiry | 1 walk, 1 `depurar`, ≤ 1 survey scan, ≤ 1 roster scan | test |
| 50 requests inside the TTLs | 1 walk, 1 evaluaciones scan, 1 roster scan, 1 survey scan, 1 `depurar`, 1 referencia GET, ≤ 2 Blob PUTs at cold start | test |
| Firestore scans per continuously-open hour, static inputs (derived from the TTLs) | roster ≤ 2, survey ≤ 1, evaluaciones ≤ 4 | test (fake clock) |
| Firestore reads per open hour (derived) | 2·I + S + 4·E ≈ 260 + 1,900 + 5,880 ≈ **8,040** at the live sizes | ledger (reported) |
| Firestore reads for this path | ≤ 10,000/day (20% of the Spark 50k cap) — see **O1**: not attainable with a continuously open tab at these TTLs | ledger, gates the flag flip |
| Atención Sismo API | ≤ 1 walk per 5-min TTL; 0 with no viewers | test |
| Blob | 0 PUTs when content is unchanged; referencia GET ≤ 2/hour; no HEAD/list in this path | test |
| Payload | `evaluaciones` ≤ 4 MB raw; `depuracion` ≤ 400 KB raw for 400 profiles; gzip target ≤ ~15% of raw (measured and recorded at parity) | test (raw ceilings) + ledger (ratio) |
| Latency on a cache hit | p95 ≤ 50 ms for a 304; p95 ≤ 250 ms for a 200 served from precomputed bytes (in-process, over ≥ 50 samples) | test |
| Engine | 373 profiles + 3,000 stickers < 0.5 s (best of N; baseline ≈ 0.26 s) | test (canary) + ledger (real snapshot) |
| Viewer role or missing `?depuracion=1` | 0 depuración work | test |

The doc's per-hour figure "≤ 1-2 evaluaciones scans" is corrected to ≤ 4: a 15-minute TTL cannot yield fewer
than four scans in a continuously open hour (contradiction register below).

## File Changes — Efficiency Slices

| Slice | File | Action | Description |
|---|---|---|---|
| 08 | `backend/app/services/inspectores_depuracion.py` | Modify | Canonical ordering, terminal `identidad_key` tie-break, order-independent first-wins (D29) |
| 08 | `backend/tests/services/test_inspectores_depuracion.py` | Modify | Shuffle, purity, idempotency and perf-canary units |
| 09a | `backend/app/services/versioned_cache.py` | Create (proposed) | Generic TTL + content-version + single-flight primitive used by D22-D24 |
| 09a | `backend/app/routers/stickers.py` | Modify | `snapshot_version` on `EvaluacionesCache` (content-stable, D21) |
| 09a | `backend/app/routers/stickers_atencionsismo.py` | Modify | Component caches, `DepuracionCache` key + in-flight marker, referencia outside the lock, `?depuracion=1` (D22-D25) |
| 09a | `backend/app/services/inspectores_referencia.py` | Modify | `huella` on `ReferenciaBundle` (D24) |
| 09a | `backend/tests/routers/test_stickers_atencionsismo.py`, `backend/tests/services/test_inspectores_referencia.py`, shared counting-fake fixtures | Modify/Create | Unit-level tests per mechanism |
| 09b | `backend/app/routers/stickers_atencionsismo.py` | Modify | Encoded-bytes cache, ETag/304, gzip, headers (D26) |
| 09b | `backend/app/config.py`, `backend/app/main.py` | Modify | `If-None-Match` allowed, `ETag` exposed (D27) |
| 09b | `backend/tests/routers/test_stickers_atencionsismo_budget.py` | Create | Budget tests 1-9 and 11 (ledger fakes + injectable clock) |
| 10 | `web/js/seguimiento.js`, `web/js/stickers.js` | Modify | `?depuracion=1` from Seguimiento only; retention + skip-render; ETag surfaced (D28) |
| 10 | `web/js/seguimiento.test.mjs`, `web/js/stickers.test.mjs` | Modify | The two Node tests plus edge cases |
| 11 | `scripts/parity_inspectores_depurado.py` | Modify | Determinism + timing check on the real snapshot; E/I/S read-ledger |

## Rollout Order (amended — supersedes the Rollout Order above where it differs)

1. **Deploy** backend slices 06 → 08 → 09a → 09b with `SEGUIMIENTO_DEPURACION` unset/`0`. The response BODY stays
   byte-identical; from 09b the response HEADERS gain `ETag`, `Cache-Control`, `Vary` and, when accepted,
   `Content-Encoding: gzip` — verify the Stickers tab still loads (browsers decode gzip transparently).
2. **Republish** the bundle once slice 06 is deployed (deploy-then-republish); record `generado_en`. No
   `huella` is published — it is computed on read.
3. **Merge slice 10** (frontend: opt-in param, retention, skip-render) into the tracker. Per 11.20 this happens
   only after slice 11's tests are green. `[MODIFIED]` The earlier step "slice 10 merges into the tracker last,
   after the flip" no longer holds: the frontend now precedes the flip because the opt-in parameter is what makes
   the flip cheap.
4. **Live parity + read ledger** — slice 11's harness against live output and the xlsx; it also records the
   determinism check, the timing check and the E/I/S ledger.
5. **Flip the flag** to `SEGUIMIENTO_DEPURACION=1` ONLY when ALL hold: slices 09a, 09b and 10 are merged; the
   budget tests are green; step 4 passes every parity threshold; and the measured ledger is within the budget
   ratified under O1.
6. **Merge the tracker into `main`** (the only branch that merges to `main`).

Rollback at any point = `SEGUIMIENTO_DEPURACION=0`; with the flag off (or no opt-in) the compute, the survey scan
and the referencia GET all stop by construction.

## Contradiction Register (efficiency extension)

| # | Finding | Resolution |
|---|---|---|
| C-E1 | The doc budgets "Firestore ≤ 10k reads/day" with a continuously open admin tab, but its own TTLs give 8,040 reads per open hour (D22 arithmetic), i.e. ≈ 193k/day for 24 h and 10k only in ≈ 1.2 h. The "≤ 1-2 evaluaciones scans/hour" figure is also incompatible with a 15-minute TTL (4/hour) | Per-hour scan counts are corrected to what the TTLs imply (≤ 2 / ≤ 1 / ≤ 4) and enforced by tests; the daily ceiling is kept as a measured LEDGER gate on the flag flip, and the unresolved arithmetic is raised as O1 for the user rather than silently weakened or silently kept |
| C-E2 | D4 says the derived cache has "no TTL of its own, inherits the 5-minute TTL" and the shipped `DepuracionCache` keys by identity | Superseded by D23 (`[MODIFIED]`); task 10.9 is kept and re-labelled, not deleted |
| C-E3 | The efficiency doc says the frontend skips render when `snapshot_id` is unchanged, but adding a `snapshot_id` key to the body would break the flag-off 4-key byte-identity (3.8, 10.10) | No new body key. `snapshot_id` is the frontend's name for the opaque ETag (D26/D28), which is why `expose_headers=["ETag"]` is mandatory |
| C-E4 | "Shuffled roster/sticker order yields an identical result" versus D19's "first-wins" on duplicate `main` cédulas and the "first-wins" `alias_nombres` collision (2.23) | `referencia` row order is part of the input (its `huella` is order-sensitive); the roster/sticker order is not. Iteration-order-dependent choices iterate in canonical order (D29) |
| C-E5 | The efficiency doc asks for a stable tie-break by `identidad_key` "when `creado_en` is blank", D10/8.13 make Firestore-backed the last tie-break | Layered: score → `creado_en` → Firestore-backed → `identidad_key` terminal (D29). D-TIEBREAK is registered |
| C-E6 | The old Rollout Order says slice 10 merges last (after the flip); the efficiency gate requires slice 10 merged before the flip | Rollout amended above; 11.20's "not into the tracker before Phase 12 passes" is compatible (slice 11's tests green → merge 10 and 11 → measure → flip) |
| C-E7 | Old Rollout Order: "response stays byte-identical, invisible to users" | True for the body only; 09b adds headers (rollout step 1 states it) |
| C-E8 | Budget tests 6-8 (single-flight, slow referencia, failure keeps last-good) exercise mechanisms built in 09a, but the efficiency doc assigns the budget tests to 09b | 09a keeps unit-level TDD tests for each mechanism (tasks 10.13-10.42); 09b hosts the route-level ledger versions 1-9 and 11 that reuse the 09a fixtures and assert counts only |

## Open Questions (Efficiency Extension)

- [ ] **O1 (blocks the flag flip, needs a user decision).** With E ≈ 1,470, I ≈ 130, S ≈ 1,900 and the delegated
  TTLs (roster 30 min, evaluaciones 15 min, survey 60 min), one continuously open admin tab costs ≈ 8,040
  Firestore reads per hour, so the ≤ 10,000/day default is met only for ≈ 1.2 open hours/day (the pre-change
  figure is ≈ 43,000/hour, so the improvement is real, ≈ 5×, but it does not reach the stated number). Options:
  (a) ratify a different budget (for example per open-hour, or a larger daily share of the 50k Spark cap);
  (b) lengthen the `evaluaciones` TTL via configuration (60 min lowers the hour to ≈ 3,630 reads — still only
  ≈ 2.75 open hours at 10k); (c) promote the deferred `evaluaciones` watermark + reconcile (D31) so reads become
  proportional to changed documents — the only option that scales, at the cost of a second read path and its
  parity risk. Recommendation: measure first (12.19), then choose between (a) and (c). Until ratified,
  12b.4 stays blocked.
