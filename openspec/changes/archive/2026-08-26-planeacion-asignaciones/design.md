# Design: Planeación — cruce Survey Cali ↔ API y asignación de levantamientos

Change: `planeacion-asignaciones` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-design

Reads: `proposal.md`, `exploration.md`. Mirrors real code:
`backend/app/jobs/cruce_sticker.py` (pipeline shape, field-ownership split, watermark,
candidate pre-read), `backend/app/routers/sticker_asignaciones.py` (dispatcher, clustering, guards,
chunked commits), `backend/app/services/survey_cali.py` (read surface, ingestion gates),
`backend/app/services/atencionsismo.py` + `backend/app/jobs/dashboard_refresh.py` (API record shape
and its persisted artifact), `backend/app/main.py` / `backend/app/config.py` (mounting,
configuration), `backend/tests/invariants/test_sole_writer.py` (sole-writer invariant),
`scripts/refresh_data.py` (the `codigoapp` allowlist bug), `web/js/stickers-asignacion.js`,
`web/js/main.js`, `web/index.html`, `web/styles.css`, `web/js/api-config.js`.

## Architecture at a glance

```
ArcGIS Survey123 (EDE_v1)                        Railway cron                    Firestore (sismo-agosto-sgred)          Railway web (FastAPI)                Browser (admin)
─────────────────────────                        ─────────────                   ─────────────────────────────           ─────────────────────                ───────────────
 form question `codigoapp`  ◄──prefill URL───────────────────────────────────────────────────────────────────────────  GET link via getEnlaceSurvey  ◄──POST── web/js/planeacion.js
        │                                                                                                                                                       (tab "Planeación")
        │ submit                                 dashboard_refresh (15 min)
        ▼                                          refresh_data.py ──► inspections.json ──► ingest_records ──► survey_cali/{GlobalID}
   FeatureServer/0                                 (LAYER_TO_RAW now keeps codigoapp)                              │  read-only
   attrs incl. codigoapp                           fetch_reportes  ──► reportes.json (Blob) ─┐                     │
                                                                                             │                     ▼
                                                 planeacion_cruce (hourly)  ◄────────────────┘        planeacion_puntos/{fuente}_{registro_id}
                                                   cascade: clave → geo → dir → combinado → miss        pipeline-owned fields  ◄──┐
                                                   mints clave_integracion                              admin-owned fields    ◄──┼── POST /planeacion-asignaciones
                                                   scores prioridad                                                              │     ├ listPuntos / resumen
                                                   batched merge:true, pipeline fields only              planeacion_cuadrillas/{id} ◄─┤     ├ autoAgrupar
                                                   watermark _meta/planeacion_cruce_state                  puntos[], inspector_uid,      │     ├ crear/editar/eliminarCuadrilla
                                                                                                           origen: auto|manual          │     ├ asignar/desasignarInspector
                                                                                                                                        │     ├ reasignarPunto / reiniciarAgrupacion
                                                                                                                                        └─────┤ editarAsignacion / marcarNoAplica
                                                                                                                                              └ getEnlaceSurvey
```

The round trip that makes this feature real:
`planeacion_cruce` mints `clave_integracion` → `getEnlaceSurvey` embeds it as `field:codigoapp` →
the crew submits → `refresh_data.py` keeps the column (the fix) → `ingest_records` lands it in
`survey_cali` → the next `planeacion_cruce` run matches it on rung 1 and flips `tiene_survey`.
**Every link is load-bearing; ADR-7 is the one that has historically been broken.**

---

## ADR-1 — Two new collections, `planeacion_puntos` / `planeacion_cuadrillas`

**Decision.** Two new collections, feature-prefixed, in `sismo-agosto-sgred`. The point doc id is
deterministic: `doc_id(fuente, registro_id) -> f"{fuente}_{registro_id}"` with
`fuente = "atencionsismo"` — the identical helper shape as `cruce_sticker.py:91-94`, so re-running
the job **updates** the same doc instead of duplicating it, and no "does this exist" query is needed
before writing.

```
planeacion_puntos/{fuente}_{registro_id} {
  // ── pipeline-owned: rewritten every run, merge:true on THIS SUBSET ONLY ──
  fuente:                'atencionsismo'
  registro_id:            string           // the API record's `id`
  clave_integracion:      string           // ADR-3, minted deterministically
  tiene_survey:           boolean
  survey_globalid:        string | null    // the matched survey_cali doc id
  match_via:              'clave' | 'cercania' | 'direccion' | 'combinado' | null
  match_dist_m:           number | null
  tier:                   'exacta' | 'alta' | 'media' | 'sospechoso' | null
  direccion:              string
  barrio:                 string | null
  comuna:                 string | null
  coords:                 { lat: number, lon: number } | null
  afectacion:             string | null
  estado_verificacion:    string | null
  tipo_inmueble:          string | null
  habitabilidad:          string | null
  fecha_creacion:         string | null
  prioridad_score:        number           // 0-100, ADR-4
  prioridad:              'alta' | 'media' | 'baja'
  matched_at:             Timestamp

  // ── admin-owned: only routers/planeacion_asignaciones.py ever writes these ──
  estado_asignacion:      'pendiente' | 'asignado' | 'en_proceso' | 'hecho' | 'no_aplica'
  cuadrilla_id:           string | null
  inspector_uid:          string | null
  prioridad_override:     'alta' | 'media' | 'baja' | null
  asignado_en:            Timestamp | null
  reasignado_de:          string | null    // previous inspector_uid, one-hop breadcrumb
  motivo_exclusion:       string | null    // required when estado_asignacion = 'no_aplica'
  notas:                  string | null
  editado_en:             Timestamp | null
  editado_por:            string | null    // uid of the admin who last corrected this point
}

planeacion_cuadrillas/{id} {
  nombre:          string
  puntos:          string[]                 // planeacion_puntos doc ids
  inspector_uid:   string | null
  origen:          'auto' | 'manual'
  zona_id:         string | null            // dominant comuna of the group, display only
  creada_en:       Timestamp
}
```

**Ownership is enforced by which fields a writer ever touches**, exactly as
`cruce_sticker.py:83-87` does it:

```python
PIPELINE_FIELDS = ("fuente", "registro_id", "clave_integracion", "tiene_survey",
                   "survey_globalid", "match_via", "match_dist_m", "tier",
                   "direccion", "barrio", "comuna", "coords", "afectacion",
                   "estado_verificacion", "tipo_inmueble", "habitabilidad",
                   "fecha_creacion", "prioridad_score", "prioridad", "matched_at")
ADMIN_DEFAULT_FIELDS = {"estado_asignacion": "pendiente", "cuadrilla_id": None,
                        "inspector_uid": None, "prioridad_override": None}
```

`ADMIN_DEFAULT_FIELDS` is seeded ONLY on a doc's first write (`build_write_ops`'s
`if did not in existing_ids` branch, `cruce_sticker.py:267-268`) and never re-applied. Every other
admin field is absent until an admin sets it — absence *is* "not corrected yet", which is
distinguishable from an explicit `null`.

**Why its OWN collections, not the sticker campaign's.** Different point universe (atencionsismo
reports vs Panel/EDE points), different doc-id namespace, different "done" signal, an order of
magnitude more documents, and a different admin field set. Sharing `cuadrillas` would mean a
cuadrilla whose `puntos[]` ids resolve against two different collections depending on which campaign
created it — an ambiguity with no upside.

- *Rejected:* one collection with a `campana: 'sticker' | 'survey'` discriminator. Every query in
  both campaigns would need an extra equality filter (and therefore an extra composite index leg),
  and `test_sole_writer.py`'s per-collection allowlists — currently CLOSED for `sticker_matches` —
  would have to be reopened and merged, destroying the review tripwire ADR-9 of the consolidation
  change exists to provide.

## ADR-2 — Point source: `reportes.json`, not a live day-walk; `informe/json`, not `visitados-criticos`

**Decision (source artifact).** `planeacion_cruce.py` reads the API points from
`web/data/reportes.json` when present, else `$REPORTES_URL` (the Vercel Blob copy) — the identical
two-tier pattern `cruce_sticker.py:98-112`'s `_load_ede()` uses for `inspections.json`, and for the
identical reason: the backend Docker image does not `COPY web/`, so the cron must fetch over HTTP.
Raise (never silently proceed with zero points) when neither source is available.

**Why not call `atencionsismo.day_walk()` directly.** `dashboard_refresh` already walks the full
range every ~15 minutes and writes exactly the artifact this job needs
(`dashboard_refresh.py:140-183`, `_raw_record_mapper` keeps every analytic field). Re-walking ~25
days of windows hourly would triple the load on an API that already 504s on dense windows, for data
that is at most 15 minutes staler than a fresh walk. "Call it, don't duplicate it" is the same
principle `dashboard_refresh.py:60-65` applies to `blob_sync`.

**Decision (endpoint), resolved but flagged.** The point universe is `informe/json`'s full record
set, not `operario/reports/visitados-criticos`. Three reasons: the user asked for "el total de
puntos"; `informe/json` is the only atencionsismo client ported into `backend/`
(`app/services/atencionsismo.py`); and `visitados-criticos`'s only consumer today is the excluded
dagma pipeline, so porting it would be new, unreviewed surface in a change that already has plenty.

**Tradeoff, stated plainly.** `visitados-criticos` carries `placeId` (`arcgis:<GlobalID>`), which
would give the cascade a genuine identity rung for the *pre-existing* survey backlog.
`informe/json` does not, so for surveys submitted before this change ships, matching is fuzzy-only
(ADR-5 rungs 2-4). This is accepted because `codigoapp` (ADR-3) is strictly better going forward — it
works for **all ~14.8k points**, not just the pre-filtered critical subset — and because the backlog
is ~1091 records that the legacy job already matched successfully with the same fuzzy cascade. If the
operator later needs the critical subset or retroactive GlobalID linkage, porting
`visitados-criticos` is an additive Phase 2, not a v1 blocker.

## ADR-3 — `clave_integracion`: the minting rule

**Decision.**

```python
KEY_PREFIX = "PLN"

def clave_integracion(fuente: str, registro_id: str) -> str:
    """Deterministic, URL-safe, checksummed integration key. Pure — no
    Firestore access, no clock, no randomness: the same point always mints
    the same key, on every run, forever."""
    raw = f"{fuente}:{registro_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8].upper()
    slug = re.sub(r"[^A-Z0-9]", "", str(registro_id).upper())[:24]
    return f"{KEY_PREFIX}-{slug}-{digest}"          # e.g. PLN-14832-9C4A1F0B
```

Every property the requirement asks for, and where it comes from:

| Property | How |
|---|---|
| **Stable** | Derived only from immutable identity (`fuente`, `registro_id`). No clock, no counter, no RNG. A re-run mints the identical key, so the job never needs to read the doc back to learn what key it already assigned |
| **Deterministic** | Pure function; the same input is the same output in the job, in the router, and in a test fixture — the three places that need to agree |
| **Collision-resistant** | `registro_id` is already unique within the source; the 32-bit digest is computed over the RAW `fuente:registro_id` pair, so two ids that collapse to the same `slug` after sanitization still differ in the digest |
| **≤ 255 chars** | `4 + ≤24 + 1 + 8 = ≤37`. The layer field is String(255); there is an order of magnitude of headroom |
| **URL-safe** | Charset is `[A-Z0-9-]` only. No percent-encoding, no `+`/space ambiguity, no characters that a copy-paste through a chat client or a QR code will mangle |
| **Human-traceable** | The `PLN-` prefix makes keys greppable/filterable in the ArcGIS layer and instantly distinguishable from anything a human typed; the raw id is visible in the middle segment |
| **Verifiable** | Given a returned key, re-minting from the parsed id reproduces the digest — a mismatch means the key was damaged in transit and MUST be treated as no-match rather than a wrong match |

**Reverse lookup needs no lookup table.** The key embeds the id, so a returned `codigoapp` is
resolved by parse + checksum verify. `clave_integracion` is still stored on the doc and queried with
`where("clave_integracion", "==", k)` (single-field, auto-indexed) as the authoritative confirmation
— parse-only would trust a client-supplied string, and the Firestore round trip costs one document
read.

- *Rejected:* a bare UUID4. Not deterministic — the job would have to read every doc back to learn
  its own key, and a lost write would mint a second key for the same point.
- *Rejected:* the raw `registro_id` alone. No checksum (a transcription slip silently matches a
  different, real point), no prefix (indistinguishable from noise a crew might type into the field).
- *Rejected:* reusing `integracion_F1/asignar_f3.py:259-267`'s `id_asignacion()`. Excluded surface
  (proposal.md's dagma directive) and its own semantics are tied to the F3 assignment domain.

> **Post-implementation correction, recorded here for audit** (see `apply-progress.md`'s "Follow-up
> fix — Issue 1 resolved"): the "verify by stateless recompute" mechanism this ADR described could
> not be implemented for real UUID-shaped `registro_id` values (the 24-char slug cap is lossy for a
> 32/36-char UUID, so a checksum recomputed from the parsed slug can never match the digest computed
> over the full raw id). The checksum property this ADR protects is instead enforced by exact
> membership lookup against an index of freshly-minted keys for known points — every safety property
> (a damaged/forged key resolves to no point; a slug collision cannot pair two different points) is
> preserved unconditionally under that mechanism. `verify_clave_integracion` is structural-only
> (prefix / charset / slug length / digest length) as shipped.

## ADR-4 — Prioritization: the product decision, made explicit

**Decision.** A deterministic additive score in `[0, 100]`, bucketed into three tiers, computed by a
**pure function** in the job (testable offline, no Firestore, no clock beyond the run timestamp):

```python
prioridad_score = peso_afectacion(rec)        # 0-50  severity
                + peso_estado(rec)            # 0-30  verification state
                + peso_antiguedad(rec, ahora) # 0-20  saturating age since fechaCreacion

prioridad = 'alta' if score >= 60 else 'media' if score >= 35 else 'baja'
```

**Why these three signals, in this order.**

1. **Severity (`afectacion`) dominates, 0-50.** An EDAN survey exists to assess structural damage.
   If the ranking does not put the most damaged buildings first, it is not a triage — it is a queue.
   Weight 50 means severity alone can carry a point into `alta` regardless of the other two.
2. **Verification state (`estadoVerificacion`), 0-30.** A report the city has already verified is a
   confirmed damage case; an unverified citizen report may be noise, a duplicate, or a mistaken
   address. Sending a professional to an unverified report costs the same day as sending them to a
   confirmed one. Weight 30 means it can promote or demote within a severity band but cannot
   outrank a severity gap.
3. **Age since `fechaCreacion`, 0-20, saturating.** Among comparable points, the oldest unattended
   report goes first — otherwise low-severity reports starve forever and the campaign never
   converges. Saturating (capped at 20 after N days, `AGE_SATURATION_DAYS`) specifically so that a
   very old minor report can never outrank a fresh severe one; without the cap, time would
   eventually dominate every other signal, which is the classic failure mode of age-weighted queues.

**Comuna clustering is deliberately NOT a priority input.** Geography is a *routing* concern and
`autoAgrupar` (ADR-5) already handles it by proximity. Folding it into the score would let a dense
comuna silently outrank a severely damaged building in a sparse one — the score would stop meaning
"how badly does this need a survey" and start meaning "how convenient is this to visit", which is a
different question the operator should answer separately, with the map, when forming cuadrillas.

**Unknowns are ranked, not crashed.** Both category weights come from explicit dict constants
(`PESOS_AFECTACION`, `PESOS_ESTADO`) with a documented `DEFAULT_*_WEIGHT` fallback, so a new category
value appearing in the API mid-campaign produces a mid-ranked point and a log line, never a
`KeyError` that kills the cron.

**Two escape hatches, because the default WILL be wrong for individual points.**
- `prioridad_override` — admin-owned, pipeline never touches it (ADR-1). When set, the UI and the
  `listPuntos` ordering use it in place of the computed `prioridad`. A wrong ranking for one building
  is a one-click correction, not a data-model problem.
- `marcarNoAplica` — removes a point from the pool entirely with a **mandatory** `motivo_exclusion`.
  Exclusion is deliberately a separate concept from low priority: "not worth doing first" and "must
  never be done" are different statements and collapsing them loses the operator's reasoning.

**Flagged for confirmation (proposal.md risk 1 / question Q1-Q2).** The *structure* above is an
engineering decision and is locked. The *specific weight per live category value* is the operations
lead's call. Shipping constants + override + a Phase-0 gate task is what makes an unconfirmed default
safe rather than reckless.

- *Rejected:* a learned/statistical ranking. No labelled outcome data exists, and an unexplainable
  ordering is unusable for an operator who has to justify a visit order to a neighbourhood.
- *Rejected:* strict lexicographic sorting (all severe first, then all verified, ...). Produces
  cliff-edge behaviour where a single category boundary reorders hundreds of points, and gives no
  usable tie-break at scale.

## ADR-5 — The cascade, tiering, and incrementality

**Decision.** Five rungs, exact-key FIRST. Implemented in `planeacion_cruce.py` but with the fuzzy
rungs **imported** from `app.integracion.cruce_gestor` (`nearest`, `match_by_direccion`,
`build_addr_index`, `addr_key`, `_eval_latlon`) — never forked, exactly as `cruce_sticker.py:60-62`
does it.

| # | Rung | Condition | `match_via` | `tier` |
|---|---|---|---|---|
| 1 | **Exact key** | a `survey_cali` doc's `codigoapp` equals this point's `clave_integracion` (checksum verified) | `clave` | `exacta` |
| 2 | Proximity | haversine ≤ `MATCH_MAX_M = 40.0` | `cercania` | `alta` if ≤ 20 m or the address also agrees, else `media` |
| 3 | Address | IGAC-normalized exact, or fuzzy ratio ≥ `SEM_OK = 0.90` | `direccion` | `media` |
| 4 | Combined | haversine ≤ `COMBINED_MAX_M = 100.0` **AND** fuzzy ratio ≥ `COMBINED_SEM = 0.80` | `combinado` | `sospechoso` |
| 5 | Miss | none of the above | `null` | `null` (`tiene_survey: false`) |

**Rung 1 matches nothing on the very first run — by construction, and that is fine.** No survey
carries a key until this change ships one. It is placed first anyway because from the second
assignment cycle onward it is the *only* rung that is not a heuristic, and because putting it first
means the fuzzy rungs are never consulted for a point that already has an authoritative answer.

**Thresholds and their provenance.** `MATCH_MAX_M = 40.0` and `SEM_OK = 0.90` are lifted verbatim
from `cruce_sticker.py:74-75` (which took them from `cruce_gestor`/`asignar_f3`) so this repo has one
tuned proximity threshold, not two. The legacy dagma job used a stricter 20 m; 20 m is kept as the
`alta`-tier boundary rather than as the match cutoff, because atencionsismo coordinates are
frequently **geocoded from an address** rather than GPS-captured, and a 20 m cutoff against a
geocoded centroid produces false negatives (a missed match sends a professional on a duplicate trip —
strictly worse than a `media`-tier match the operator can see and question). Rung 4 exists precisely
to catch the pairs where neither single signal clears its own bar but both point the same way.

**Incrementality — three independent mechanisms, all copied from `cruce_sticker.py`:**

1. **Watermark** `_meta/planeacion_cruce_state` holding `last_run_at`. `survey_cali` is queried with
   `where("_updated_at", ">", watermark)` — only surveys touched since the last successful run.
   `None` on the first run (or after a run that never reached the end) means "process everything",
   the same fail-safe semantics as `read_watermark` (`cruce_sticker.py:180-186`).
2. **Projected pre-read** of `planeacion_puntos` via
   `db.get_all(refs, field_paths=["tiene_survey", "clave_integracion"])` in `BATCH_SIZE`-sized
   chunks — the cheap existence+state probe that makes candidate selection possible without ever
   reading ~14.8k full documents (`read_tiene_sticker_state`, `cruce_sticker.py:194-209`).
3. **Pure `select_candidates`** — a point already `tiene_survey: true` is never re-scanned;
   only brand-new points and still-pending ones are candidates.

**"Nothing changed → don't rewrite."** A candidate that misses again and already has a doc is
skipped entirely rather than rewritten with identical values (`cruce_sticker.py:388-389`). Without
this, every hourly run would rewrite ~14k documents and the Firestore bill would be the feature's
dominant cost.

**Correctness note on the watermark + addr_index interaction.** Because only *new* surveys are
fetched, a pending point is compared only against surveys that appeared since the last run. That is
correct precisely because the point was already compared against every older survey in a previous
run and missed. This is the same argument `cruce_sticker.py` relies on; it is written down here
because it looks like a bug on first reading and is not.

## ADR-6 — The Survey123 URL: configuration, not a constant

**Decision.** A new pure module `backend/app/services/survey_link.py`:

```python
def build_survey_urls(clave: str, *, form_url: str, field_app_item_id: str | None) -> dict:
    """{'web': ..., 'app': ... | None}. Pure — the caller supplies config, so
    this is fully testable with no environment."""
    web = f"{form_url}{'&' if '?' in form_url else '?'}field:codigoapp={quote(clave, safe='')}"
    app = (f"arcgis-survey123:///?itemID={field_app_item_id}&field:codigoapp={quote(clave, safe='')}"
           if field_app_item_id else None)
    return {"web": web, "app": app}
```

Configuration lives in `backend/app/config.py`'s `Settings`:
`survey123_form_url: str = ""` (env `SURVEY123_FORM_URL`) and
`survey123_field_app_item_id: str = ""` (env `SURVEY123_FIELD_APP_ITEM_ID`).

**`getEnlaceSurvey` fails LOUD, not soft.** If `SURVEY123_FORM_URL` is unset the action returns
**503 with an explicit "SURVEY123_FORM_URL no está configurado" message**, never a truncated or
placeholder URL. A broken survey link discovered in the field costs a wasted trip; a 503 in the
admin UI costs a Railway env-var edit.

**Why not a repo constant.** The form share URL / item id **does not exist anywhere in this
repository** — only the FeatureServer URL does (`scripts/refresh_data.py:79-82`). It must come from
the ArcGIS org admin (proposal.md manual step 1). Hardcoding it would also make the inevitable form
republish (which can change the item) a code change plus a deploy instead of an env-var edit.

**Why the parameter name is literally `field:codigoapp`.** Survey123 URL prefill matches on the
**question name**, which for this layer-backed form is the layer column name, `codigoapp`. If the
published form's question was ever renamed, the prefill silently no-ops — hence proposal.md manual
step 2, and hence ADR-5's `match_via` reporting, which makes that silent failure visible in the UI.

**Scope boundary this ADR enforces.** Only `codigoapp` is prefilled. Not `direccion`, not `comuna`,
not `nombre_edif`. Prefilling content fields would fight the form's cascading logic and make the
survey's provenance ambiguous — "did the inspector observe this, or did the office type it for
them?" is not a question a damage assessment should raise about its own data.

## ADR-7 — The `codigoapp` pipeline fix, and the churn it causes

**Decision.** Add one entry to `LAYER_TO_RAW` in `scripts/refresh_data.py` (dict opens at line 936):

```python
"codigoapp": "codigoapp",   # integration key round-trip (planeacion-asignaciones)
```

The raw label is the layer field name itself, because unlike every other entry this field has no
historical Survey123 xlsx-export header to preserve — it was never in the export contract.

**Why this one line is the whole feature.** `scripts/refresh_data.py:1111` is an explicit column
allowlist: `df = df[list(LAYER_TO_RAW.values()) + ["x", "y"]]`. The layer query already requests
`outFields: "*"` (line 1070), so `codigoapp` **is fetched** and then dropped one line before
`inspections.json` is written. Verified empirically: `'codigoapp' in record` is `False` for all 1091
live records. Without the fix, the crew fills the key in the field, ArcGIS stores it, and the backend
never sees it — the chain fails silently at exactly the point nobody is looking.

**Verify at implementation time**, because they are the other three places a column can vanish:
`codigoapp` must not be in `COLS_A_ELIMINAR`, must survive `normalize()`, and must not be filtered
out of the `survey_cali` ingest. It is NOT in `services/survey_cali.py`'s `DERIVED_FIELDS` (105-114)
or `SOURCE_SYSTEM_FIELDS` (125), so it is correctly treated as RAW content — which is what makes a
change to it trip the hash gate and get ingested.

**Known consequence: a permanent hash-gate miss for keyless records.** Adding a field changes
`canonical_hash()`'s input for every record. Trace through `ingest_records`
(`services/survey_cali.py:321-343`) for a record whose `codigoapp` is empty:

- hash differs from the stored `_source_hash` → the gate does NOT skip;
- `diff_upstream_fields` compares `record["codigoapp"]` (`None`) against
  `state["_source"].get("codigoapp")` (also `None`, since the pre-fix shadow has no such key) →
  **equal, so `changed` is empty**;
- `changed` empty and the doc exists → `skipped += 1`, and **`_source_hash` is never updated**;
- therefore the gate re-fires on every subsequent run, forever, for that record.

**Accepted as-is.** The cost is a SHA-256 over ~1091 small dicts per run — CPU-only, no Firestore
writes, milliseconds. It self-heals per record: the first time a record gets a real `codigoapp`
value, `changed` is non-empty, `apply_mutation` runs, and `_source_hash` is written correctly.
Documented here and in proposal.md risk 4 so it is not later misdiagnosed as a bug.

- *Rejected:* making `ingest_records` write `_source_hash` when `changed` is empty. Every write goes
  through `apply_mutation` (ADR-12 of the consolidation change), which mints a revision for any
  write — so this would create ~1091 history revisions with empty `changes` maps, polluting the
  audit trail this repo deliberately built. Special-casing metadata-only writes inside
  `apply_mutation` is a real design change to a shipped, spec'd module, disproportionate to a
  millisecond of CPU.
- *Rejected:* a one-time backfill script. Same revision pollution, plus a throwaway script to
  review and a manual step to remember.

## ADR-8 — `POST /planeacion-asignaciones`: 14 actions

**Decision.** One new router `backend/app/routers/planeacion_asignaciones.py`, structurally a clone
of `routers/sticker_asignaciones.py`: single POST, `{action, ...args}` Pydantic body,
`Depends(require_role("admin"))`, `REQUIRED_CLIENTS = ("sismo",)`, `HTTPException` 400 for bad input
and 502 for anything unexpected. Joins `app/main.py`'s router imports and `_ROUTERS` tuple.

| action | body | result | notes |
|---|---|---|---|
| `listPuntos` | `{estado?, prioridad?, comuna?, soloPendientes?, limit?}` | `{ok, puntos, truncado}` | **Bounded** — see ADR-9 |
| `resumen` | — | `{ok, resumen}` | Aggregate tallies without shipping the working set (ADR-9) |
| `listCuadrillas` | — | `{ok, cuadrillas}` | Full read; hundreds of docs, not thousands |
| `autoAgrupar` | `{maxRadiusM?, maxSize?}` | `{ok, cuadrillas}` | Deterministic greedy clustering; MUST NOT touch `estado_asignacion` |
| `crearCuadrilla` | `{nombre, puntos[]}` | `{ok, id}` (201) | `origen:'manual'`, read-before-write guards |
| `editarCuadrilla` | `{cuadrilla_id, add[], remove[]}` | `{ok, id, puntos}` | Membership stays consistent both ways |
| `asignarInspector` | `{cuadrilla_id, inspector_uid}` | `{ok, id}` | Propagates to every member point |
| `desasignarInspector` | `{cuadrilla_id}` | `{ok, puntos}` | Keeps the cuadrilla, releases the inspector |
| `reasignarPunto` | `{punto_id, nuevo_inspector_uid}` | `{ok, ...}` | One-hop `reasignado_de` breadcrumb |
| `eliminarCuadrilla` | `{cuadrilla_id}` | `{ok, id}` | Clears member points BEFORE deleting the doc |
| `reiniciarAgrupacion` | — | `{ok, eliminadas, puntosLiberados}` | Undoes AUTO groups only; manual ones survive |
| **`editarAsignacion`** | `{punto_id, estado_asignacion?, prioridad_override?, inspector_uid?, notas?}` | `{ok, punto}` | **The correction surface** — see below |
| **`marcarNoAplica`** | `{punto_id, motivo_exclusion}` *or* `{punto_id, revertir: true}` | `{ok, punto}` | Reversible pool exclusion, reason mandatory |
| **`getEnlaceSurvey`** | `{punto_id}` | `{ok, clave, web, app}` | 503 if unconfigured (ADR-6) |

**Guards, ported from `sticker_asignaciones.py:105-122`** and adapted to this domain
(pure functions, exported for the offline test):

- `points_already_assigned(points, target_cuadrilla_id)` — one point belongs to at most one
  cuadrilla; adding a point that is already in a *different* one is rejected, never silently moved.
- `points_with_survey(points)` — a point that already has a survey is not assignable.
- `points_excluded(points)` — a point marked `no_aplica` is not assignable.

Guards are checked most-specific-first so the operator gets the actionable reason, not a generic
"some points are invalid" — the same ordering rationale `crear_cuadrilla` documents at
`sticker_asignaciones.py:220-222`.

**`editarAsignacion` is the answer to "posibilidad de edición o corrección"** and it is a distinct
action, not a merge of the others, for three reasons:
- **Partial semantics.** Only the keys present in the body are written. Omitting `notas` leaves the
  existing note; passing `null` clears it. A caller can correct exactly one field without knowing or
  resending the rest.
- **It writes `editado_en` / `editado_por` on every call**, so a correction is always attributable —
  the minimum accountability the "corrección" requirement implies, without building the full audit
  trail proposal.md defers.
- **It is the only action that can set `inspector_uid` independently of a cuadrilla**, which is what
  makes correcting a mis-assignment possible without dismantling the group it belongs to.

`marcarNoAplica` **requires** `motivo_exclusion` (400 without it) and is reversible via
`{revertir: true}`, which restores `estado_asignacion: 'pendiente'` and clears the reason. Exclusion
is kept separate from `estado_asignacion: 'hecho'` because "surveyed" and "will never be surveyed"
are different facts and the pending-pool query must distinguish them.

**Clustering.** `haversine_m` and `auto_agrupar` are ported verbatim from
`sticker_asignaciones.py:67-102` — deterministic greedy nearest-neighbour, stable `[lat, lon]` sort,
no RNG, no k-means, `O(n²)` over the *pending, ungrouped* subset only (not the full 14.8k).
`DEFAULT_MAX_RADIUS_M = 800` / `DEFAULT_MAX_SIZE = 8` are carried over as **named constants flagged
unconfirmed** — an EDAN survey is a far longer visit than applying a sticker, so 8/day is very likely
wrong (proposal.md question Q4). Named constants + a UI override make retuning a one-line change.

- *Rejected:* separate REST routes per action (`POST /planeacion/cuadrillas`, ...). The whole backend
  uses the single-POST action-dispatcher shape (`stickers.py`, `usuarios.py`,
  `sticker_asignaciones.py`, `inspector_asignaciones.py`); one novel router style would be an
  inconsistency with no benefit.

> **Post-implementation note**: `DEFAULT_MAX_SIZE` shipped as `10`, per the BINDING user decision
> recorded in proposal.md's question round (Q4), overriding this ADR's own carried-over "8" default.

## ADR-9 — Scale: bounded queries, not "load everything"

**This is the main structural departure from the sticker precedent, and it is forced by the data.**
`sticker_matches` holds ~1.1k lean docs, so `list_puntos` reads the whole collection
(`sticker_asignaciones.py:142-144`) and the browser renders every marker. `planeacion_puntos` will
hold **~14.8k**. The same approach means a multi-MB JSON response, ~14.8k `L.circleMarker`
instances, and a table the browser sorts on the main thread — a performance cliff, not a hypothetical.

**Decision — three mechanisms:**

1. **`listPuntos` is a bounded, indexed query.** Default filter: `tiene_survey == false` AND
   `estado_asignacion != 'no_aplica'`, ordered by `prioridad_score` DESC, `limit` default **2000**,
   hard max **5000**. The response carries `truncado: bool` so the UI can say so honestly instead of
   pretending it showed everything. Optional `comuna` / `prioridad` / `estado` narrowing.
   Inequality-on-`estado_asignacion` is applied **in code** after the query rather than as a second
   Firestore inequality (Firestore permits only one inequality field per query) — the same
   filter-in-code tradeoff `run_auto_agrupar` already documents at
   `sticker_asignaciones.py:173-177`.
2. **`resumen` serves the KPI tiles.** Aggregate tallies (total, levantados, pendientes by
   `prioridad`, by `comuna`, by `estado_asignacion`, plus a **`por_match_via`** tally) computed
   server-side with Firestore `count()` aggregation queries where possible. This is what lets the UI
   show "14,804 puntos · 1,091 levantados · 13,713 pendientes" without transferring 14,804 documents
   — and `por_match_via` is what makes ADR-6's silent-prefill-failure visible (see proposal.md
   risk 2).
3. **The map renders only the returned working set**, with an explicit "incluir levantados" toggle
   that re-queries rather than filtering client-side.

**Required composite indexes** (manual step 6 — Firestore cannot create these from the client):
`planeacion_puntos`: (`tiene_survey` ASC, `estado_asignacion` ASC, `prioridad_score` DESC) and
(`estado_asignacion` ASC, `cuadrilla_id` ASC, used by `autoAgrupar`'s candidate query).

- *Rejected:* client-side pagination over a full collection read. Moves the cost to the wire and the
  browser instead of removing it.
- *Rejected:* a materialized "pendientes" mirror collection maintained by the job. A second copy that
  can drift, for a problem an index and a `limit` already solve.

> **Post-implementation note**: `list_puntos` shipped as an over-fetch-then-re-sort (fetch
> `LIMIT_MAX + 1` by raw `prioridad_score`, then re-sort in code by override-aware effective
> priority) rather than a single Firestore-level sort, because `prioridad_override` is intentionally
> invisible to the pipeline (ADR-1) and Firestore cannot express that sort at the query level. Also,
> `LIMIT_DEFAULT` was later lowered from 2000 to 300 for frontend render speed (Batch 4/Phase 6). The
> first composite index actually required by the live query differed from this ADR's prediction —
> `(tiene_survey ASC, prioridad_score DESC)` — because `estado_asignacion` is filtered in code, not
> in the Firestore query itself. See `apply-progress.md` and the final `tasks.md` Phase 5/6 notes.

## ADR-10 — Frontend: a top-level tab, and why it fetches its own roster

**Decision.** `web/js/planeacion.js`, exporting
`initPlaneacion(root, { getToken }) -> { reload }`, structurally cloning
`web/js/stickers-asignacion.js` (3-step guided flow, one shared `rows` array behind both the table
and the map, pure exported helpers for the offline self-check, optimistic local mutation +
`renderAll()` for per-item actions and a full `reload()` only for toolbar actions).

**Wiring — five files, and the fifth is the one that leaks if forgotten:**

| File | Change |
|---|---|
| `web/index.html:70-77` | New `<button ... data-view="planeacion" ...>Planeación</button>` after the Stickers tab |
| `web/index.html:~279` | New `<section id="view-planeacion" data-view-panel="planeacion" aria-label="Planeación" hidden></section>` (empty — the module sets `innerHTML`, like every other admin tab) |
| `web/js/main.js:221-257` | New `if (view === 'planeacion') initPlaneacion(document.getElementById('view-planeacion'), { getToken: getIdToken });` branch — re-init on every open, matching stickers/usuarios/analista |
| `web/styles.css:1559-1564` | **Add `body:not([data-role="admin"]) .view-tab[data-view="planeacion"]` to the display:none selector list.** Role gating in this dashboard is CSS-only; a tab omitted from that list is visible to every non-admin role |
| `web/js/api-config.js` | New entry `planeacionAsignaciones: \`${RAILWAY_BASE_URL}/planeacion-asignaciones\`` |

**The api-config entry points straight at Railway, with no parity gate.** Every other entry in that
map exists in two implementations (legacy Vercel + consolidated FastAPI) and flips only after its own
parity check. This endpoint is **new** — there is no legacy twin to be at parity with — so it starts
on `RAILWAY_BASE_URL` from day one. Worth stating because it is the first entry in that file that
does not follow the flip-after-parity procedure, and a reviewer will rightly ask.

**Decision: `initPlaneacion` fetches its own inspector roster.** `initStickersAsignacion` receives
the roster via a `getInspectores` callback (`stickers-asignacion.js:563`) because it lives *inside*
the Stickers tab, which has already loaded it. Planeación is a **top-level sibling**: when it is the
open tab, nothing has loaded the roster. So it calls `/api/stickers` `{action:'list'}` once per init
and caches it for the session, filtering with the same `habilitado` rule
(`stickers-asignacion.js:122`). This is a real difference from the template and is recorded so it is
not "fixed" back into the callback shape.

**UI structure — 3 steps, mirroring the sticker flow's guided shape:**

1. **Priorizar** — KPI tiles from `resumen`; the pending working set as a `prioridad_score`-ordered
   table plus a map; filter chips for `prioridad` / `comuna` / `afectacion`; "Auto-agrupar" with
   visible radius/size inputs.
2. **Cuadrillas e inspectores** — cuadrilla cards, searchable inspector combobox
   (`filterInspectores` pattern), assign / unassign / delete, `reiniciarAgrupacion`.
3. **Puntos** — the full working-set table. Per-row actions: reassign (`<select>`), **"Editar
   asignación"** (modal → `editarAsignacion`: estado, `prioridad_override`, notas), **"No aplica"**
   (modal with a mandatory reason → `marcarNoAplica`), and **"Abrir survey" / "Copiar enlace"**
   (→ `getEnlaceSurvey`, opening `web` on desktop and offering `app` when present).

**Map legend — 5 colours, and why not fewer.**
green `tiene_survey` (levantado) · red pendiente `alta` · amber pendiente `media`/`baja` ·
blue `asignado`/`en_proceso` · grey `no_aplica`. Three states would be simpler but would collapse
either "assigned but not yet visited" into "pending" (hiding the operator's own work in progress) or
"high priority" into "pending" (hiding the entire point of the priority feature). Five is the
smallest legend that keeps both distinctions, and it stays a legend, not a gradient, so it is
readable at a glance.

**Truncation is shown, never hidden.** When `listPuntos` returns `truncado: true`, the UI displays
"mostrando los N puntos de mayor prioridad de M pendientes" — an operator making a day plan must know
they are looking at a slice.

## ADR-11 — Sole-writer invariant for two new collections

**Decision.** `backend/tests/invariants/test_sole_writer.py` gains **two new independent allowlist
constants and two new test functions** — not new entries in the existing sets:

```python
ALLOWED_MODULES_PLANEACION_PUNTOS = {
    APP_ROOT / "jobs" / "planeacion_cruce.py",              # pipeline fields, merge:true
    APP_ROOT / "routers" / "planeacion_asignaciones.py",    # admin fields
}
ALLOWED_MODULES_PLANEACION_CUADRILLAS = {
    APP_ROOT / "routers" / "planeacion_asignaciones.py",
}
```

Both sets are **CLOSED on arrival**: exactly the two modules ADR-1's ownership split names, and no
more. Independent sets (not merged into `ALLOWED_MODULES`) because the existing
`sticker_matches`/`cuadrillas` allowlist is explicitly marked CLOSED at
`test_sole_writer.py:87-92`; reopening it to absorb a different campaign's collections would destroy
the review tripwire it exists to be.

Expect `app/main.py` to need an entry if the router module's own name ever collides with a scanned
literal — it does not here (`planeacion_asignaciones` ≠ `planeacion_puntos`), unlike the
`survey_cali` case that forced `main.py` into that allowlist (`test_sole_writer.py:112-122`).

> **Post-implementation note**: `ALLOWED_MODULES_PLANEACION_PUNTOS` ultimately gained a THIRD entry,
> `routers/inspector_asignaciones.py`, added in the Phase 6 follow-up to give an assigned inspector
> own-uid-scoped read/write access to their own pending points (`misPuntosPlaneacion`/
> `marcarHechoPlaneacion`) — distinct from the pipeline and the admin dashboard. `planeacion_cruce.py`
> also required one flagged, read-only entry in the pre-existing, otherwise-CLOSED
> `ALLOWED_MODULES_SURVEY_CALI` set, since it genuinely reads `survey_cali` for the cascade. Both
> additions are annotated in the invariant file rather than obfuscated.

## Runnable checks (locked)

Test runner: **`python -m pytest backend/tests/ -v`** (259 passing on `main` before this change).

- `backend/tests/jobs/test_planeacion_cruce.py` — offline, no network, no Firestore:
  `clave_integracion` determinism + charset + length + checksum-verification; `doc_id` stability;
  the five cascade rungs and their tier assignment; `prioridad_score` monotonicity (severity
  dominates verification state dominates age), age saturation, unknown-category fallback;
  `build_write_ops` never emits an admin-owned key for an existing doc and seeds exactly
  `ADMIN_DEFAULT_FIELDS` on a first write; `select_candidates` drops `tiene_survey: true` points.
- `backend/tests/routers/test_planeacion_asignaciones.py` — `TestClient` + a fake Firestore double,
  matching `tests/routers/test_sticker_asignaciones.py`'s existing shape: non-admin → 403 with zero
  writes; every action's happy path and its guard rejections; `autoAgrupar` determinism / size cap /
  radius cap / empty-set no-op / does-not-assign-an-inspector; `editarAsignacion` partial semantics
  and `editado_por` stamping; `marcarNoAplica` reason-required and reversible; `listPuntos` bound
  and `truncado` flag; `getEnlaceSurvey` 503 when unconfigured.
- `backend/tests/services/test_survey_link.py` — pure URL construction: separator choice
  (`?` vs `&`), percent-encoding, `app` is `None` without an item id.
- `backend/tests/invariants/test_sole_writer.py` — the two new collection literal scans (ADR-11).
- `web/js/planeacion.test.mjs` — `node --test "js/**/*.test.mjs"` from `web/`, covering the pure
  helpers (`colorForPunto`, `buildRows`, `sortRows`, `filterRows`, priority chip logic), mirroring
  the existing `stickers-asignacion.test.mjs`.

## Size and commit/PR split

Five work units, `auto-chain` / `stacked-to-main`:

1. **`fix(pipeline): keep codigoapp through the Survey123 column allowlist`** — `LAYER_TO_RAW` + a
   regression test asserting the column survives to the normalized frame. Tiny, independent, and
   everything downstream depends on it, so it merges first.
2. **`feat(jobs): planeacion cruce job`** — `app/jobs/planeacion_cruce.py` (key minting, cascade,
   prioritization, watermarked incremental write path) + its offline test module.
3. **`feat(api): planeacion-asignaciones endpoint`** — `app/routers/planeacion_asignaciones.py`,
   `app/services/survey_link.py`, `app/config.py` settings, `app/main.py` mounting,
   `tests/invariants/test_sole_writer.py` entries + the two router/service test modules.
4. **`feat(web): Planeación tab`** — `web/js/planeacion.js` + `index.html` + `main.js` +
   `styles.css` role gating + `api-config.js` + `planeacion.test.mjs`.
5. **Manual console / ArcGIS steps** — proposal.md's manual-operator list. No repo diff; attached to
   the PR descriptions of units 2-4 as prerequisites.

## Risks / open decisions carried to tasks

1. **Priority weight table** (proposal.md risk 1, questions Q1-Q2) — structure locked, per-category
   weights need the operations lead. Ship as named constants + documented fallback + a Phase-0 gate
   task; `prioridad_override` makes a wrong default recoverable per point.
2. **`maxRadiusM` / `maxSize` defaults** (question Q4) — carried over from the sticker campaign and
   very likely wrong for a full EDAN survey. Named constants + a visible UI override; flag as
   unconfirmed in the PR description.
3. **Cron cadence** — hourly proposed vs the sticker job's daily (proposal.md risk 5). Confirm.
4. **`codigoapp` in the published form** (proposal.md risk 2) — three links outside this repo, any
   one of which silently degrades the feature to fuzzy matching. Mitigated but not eliminated by
   `resumen`'s `por_match_via` tally.
5. **Firestore rules and composite indexes are console-managed** — no repo file governs
   `sismo-agosto-sgred`'s deployed ruleset (`integracion_F1/firestore.rules` belongs to a different
   project). Manual steps 6-7.
6. **First-run write volume** — ~14.8k documents in 30 batched commits. One-off, but confirm the
   cron service's timeout accommodates it, and that `--dry` is used for the first rehearsal.
7. Carries proposal.md risks 1-7 unchanged into task-level acceptance criteria.

## Closure note (recorded at archive, 2026-08-26)

Every ADR above shipped to production across Phases 1-4 (chained PRs on `main`) plus a Phase 6
follow-up (inspector-facing visibility + tab speed). Phase 0 (operator confirmation of the weight
table, cluster defaults, roster question, cron cadence, Survey123 URL) and Phase 5 (Railway/Firebase
console provisioning, end-to-end spot check) remain formally unchecked in `tasks.md` — these are
operator/gathering steps with no repo diff, not implementation work, and several of their outcomes
(Survey123 URL, Railway env vars, Firestore indexes) were independently confirmed done in
`proposal.md`'s "Manual operator steps" section as of 2026-08-26. The `planeacion-cruce` Railway cron
and the Planeación tab are live in production at archive time.
</content>
