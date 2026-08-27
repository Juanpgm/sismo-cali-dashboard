# Design: Puntos Solicitados — special-case points through the existing assignment machinery

Change: `puntos-solicitados` · Project: seismic_disaster_data_analisys_cali · Phase: sdd-design

Reads: `proposal.md`. Mirrors real code: `backend/app/jobs/planeacion_cruce.py`
(`doc_id`, `clave_integracion`, `PIPELINE_FIELDS`/`ADMIN_DEFAULT_FIELDS`, the batched
`merge:true` write path `write_planeacion_puntos:623-634`), `backend/app/main.py`
(`_ROUTERS` mounting), `backend/tests/invariants/test_sole_writer.py` (per-collection
allowlist pattern), `scripts/geocode_validate.py` (acceptance logic), `backend/app/routers/sign.py`
(presigned S3 + `require_auth`), `formulario/js/form.js:380` (`buildPlaneacionCard`),
`web/js/evaluaciones.js` (tab clone).

## Architecture at a glance

```
Browser (admin, "Puntos Solicitados" tab)                  FastAPI (puntos_solicitados.py)                 Firestore (sismo-agosto-sgred)
──────────────────────────────────────────                ────────────────────────────────                ─────────────────────────────
 create modal ── POST /geocode {direccion} ─────────────►  proxy → Google Geocoding (key server-side)
   (draggable marker)  ◄── {lat,lng,accepted,reason} ────  ACCEPTED = ROOFTOP/RANGE_INTERPOLATED ∈ Cali bbox
   submit ── POST {nombre,contacto,coords,fotos…} ──────►  pre-gen id = puntos_solicitados.document().id
                                                             clave_integracion('solicitado', id)
                                                             ┌── db.batch() (ATOMIC, both writes) ──┐
                                                             │  puntos_solicitados/{id}             │  request metadata + estado
                                                             │  planeacion_puntos/solicitado_{id}   │  fuente='solicitado', es_solicitado=true
                                                             └──────────────────────────────────────┘
 tab list ◄── GET (join mirror estado_asignacion) ───────  batched get_all(planeacion_puntos)      planeacion_puntos mirror inherits:
                                                                                                     · codigoapp minting (clave_integracion)
 formulario ◄── misPuntosPlaneacion (unchanged) ─────────  reads planeacion_puntos only             · existing grupo/cuadrilla/inspector endpoints
   buildPlaneacionCard: es_solicitado → PRIORIDAD badge                                              · build_survey_urls / getEnlaceSurvey
```

The point becomes indistinguishable from a pipeline point downstream: zero new assignment,
codigoapp, or Survey123 code. The only new server capability is the `/geocode` proxy.

---

## ADR-1 — Dual-write: one atomic `db.batch()`, id pre-generated first

**Decision.** The create flow pre-generates the `puntos_solicitados` id, then writes BOTH docs in
one `db.batch()` committed once:

```python
ref = db.collection("puntos_solicitados").document()   # allocates id, no write yet
sid = ref.id
clave = clave_integracion("solicitado", sid)           # imported from app.jobs.planeacion_cruce
batch = db.batch()
batch.set(ref, {...request metadata..., "estado_seguimiento": "pendiente", "clave_integracion": clave})
batch.set(db.collection("planeacion_puntos").document(f"solicitado_{sid}"), mirror_fields, merge=True)
batch.commit()                                         # atomic: both land or neither does
```

**Ordering.** The `puntos_solicitados` id MUST exist before the batch is built, because the mirror
doc id (`solicitado_{sid}`, via `doc_id('solicitado', sid)`) and its `registro_id=sid` both
reference it. `.document()` with no path allocates a client-side id without a round trip, so the id
is known before either `set`.

**Why a batch, not `client.transaction()`.** A Firestore `WriteBatch` is already atomic across
documents and collections in the same database — all writes commit or none do. A transaction adds
value only for read-then-write; this flow is two blind creates with no read dependency, so a batch
is the correct, simpler primitive. It is also the EXACT precedent already in this collection's
pipeline (`planeacion_cruce.write_planeacion_puntos:628-633`), so there is one write style for
`planeacion_puntos`, not two.

**Failure mode — resolved, not a runtime edge case.** If the batch fails, nothing is written; an
orphan is impossible by construction. No compensation, cleanup, or two-phase logic in application
code. This retires proposal.md risk 1.

- *Rejected:* two sequential `.set()` calls with best-effort compensation. Reintroduces exactly the
  orphan window a batch removes for free.

## ADR-2 — `es_solicitado` is a flat field on the mirror, written at creation

**Decision.** `es_solicitado: true` is written DIRECTLY onto the `planeacion_puntos` mirror inside
the same batch — never resolved by a join against `puntos_solicitados` at read time.

**Rationale.** `misPuntosPlaneacion` (the endpoint the formulario already calls) reads only
`planeacion_puntos`. A flat boolean is zero-cost and sits exactly where `fuente`/`registro_id`
already sit; a read-time join would add a cross-collection lookup to every formulario load. The
formulario needs nothing more than the badge + sort, and the mirror already carries the standard
`nombre`/`direccion`/`coords`/`prioridad` planeacion fields the point needs to render at all.

**Exactly one non-standard field is copied: `es_solicitado`.** `nombre`, `direccion`, `barrio`,
`comuna`, `coords`, `clave_integracion`, `tiene_survey:false`, `estado_asignacion:'pendiente'`,
`prioridad:'alta'`, `matched_at` are the ordinary planeacion_puntos fields any point carries.
`justificacion`, `nombre_solicitante`, `telefono_solicitante`, photos stay on `puntos_solicitados`
only, surfaced by the tab's "Ver detalle" (`GET`), never denormalized to the mirror. YAGNI: the
field crew card does not need the justificación.

- *Rejected:* denormalizing `justificacion`/contacto onto the mirror. Two copies to keep in sync for
  data no downstream reader of `planeacion_puntos` uses.

## ADR-3 — `clave_integracion`/codigoapp minting for `fuente='solicitado'`

**Decision.** `puntos_solicitados.py` imports `clave_integracion` and `doc_id` from
`app.jobs.planeacion_cruce` and calls `clave_integracion('solicitado', sid)` with **no change** to
that function. It is pure over `f"{fuente}:{registro_id}"`; a Firestore auto-id is a 20-char
alphanumeric slug that mints cleanly (`PLN-<sid>-<digest>`). The mirror gets that key as its
`clave_integracion`, so `getEnlaceSurvey`/`build_survey_urls` prefill `field:codigoapp` unchanged.

**The cruce job cannot double-process or overwrite the mirror — verified.** `planeacion_cruce`'s
point universe is `reportes.json` (its ADR-2), and it keys candidates by `atencionsismo_{registro_id}`
doc ids derived from those records. A `solicitado_{sid}` doc is never in that source set, so the
hourly cruce never reads, rewrites, or re-scores a manually-created mirror. No guard/skip is needed
in `planeacion_cruce.py`; document the reason so a future reader does not add one defensively.

- *Rejected:* a new minting path in `puntos_solicitados.py`. Duplicates a pure, tested function for
  no behavioral difference.

## ADR-4 — `estado_seguimiento` is derived from the mirror, not a synced second lifecycle

**Decision.** The lifecycle `pendiente -> asignado -> en_proceso -> visitado` is DERIVED in the tab's
list endpoint by reading the mirror's `estado_asignacion` (one batched `get_all` over the known
`solicitado_{sid}` ids), mapped: `pendiente→pendiente`, `asignado→asignado`, `en_proceso→en_proceso`,
`hecho→visitado`, `no_aplica→excluido`. The map colors and KPIs use this derived value.

**Rationale.** The assignment lifecycle already lives on the mirror, driven by the EXISTING
`planeacion_asignaciones.py` (grupo/cuadrilla/inspector) and `inspector_asignaciones.py`
(`marcarHechoPlaneacion`) endpoints. Storing a parallel `estado_seguimiento` on `puntos_solicitados`
would force those hot-path endpoints to dual-write a second collection they know nothing about —
exactly the coupling Approach A avoids. Deriving it is one batched read (the same pattern
`flujo-confiable` ADR-1 uses for `puntos_contacto`) and keeps ALL transitions triggered by the
existing endpoints. `PATCH /{id}` edits only request metadata (contacto, justificación, nombre,
coords), never lifecycle.

**One stored seed only.** `puntos_solicitados` carries `estado_seguimiento:'pendiente'` at creation
for offline display before the first assignment; the tab prefers the derived value when the mirror
is present. No sync job, no drift.

- *Rejected:* a stored `estado_seguimiento` kept in sync by the assignment endpoints. Fan-out onto a
  closed, unrelated write path for a value a join already yields.

## ADR-5 — `POST /geocode`: live proxy, backend port of the acceptance logic

**Decision.** New `POST /geocode`, `Depends(require_auth)`, key read from `GOOGLE_MAPS_API_KEY`
server-side (never returned). Contract:

| dir | shape |
|---|---|
| request | `{ "direccion": str }` — bbox is fixed to Cali; no caller-supplied bbox (YAGNI) |
| 200 accepted | `{ ok:true, accepted:true, lat, lng, formatted, location_type }` (`ROOFTOP`/`RANGE_INTERPOLATED` ∈ Cali bbox) |
| 200 low-confidence | `{ ok:true, accepted:false, reason }` (`sin_resultado`\|`precision_insuficiente`\|`fuera_de_cali`) — no coords; frontend falls back to the draggable marker / manual lat-lng |
| 502 | Google `REQUEST_DENIED`/`OVER_QUERY_LIMIT`/`INVALID_REQUEST` (key/quota problem, not an address rejection) |

The acceptance rule (`ACCEPTED = {ROOFTOP:15, RANGE_INTERPOLATED:40}`, `CALI_BBOX`,
`to_google_address`) is the SAME as `scripts/geocode_validate.py`. It is copied into a small pure
`backend/app/services/geocode.py`, **not** imported across `scripts/`: `geocode_validate.py` is
itself a documented "self-contained port" because the publish container clones a different repo
subset, and the FastAPI image does not package `scripts/`. This is the second port of the same
~30 lines for the same container-boundary reason, testable offline against fake responses.

**Why surface `accepted:false` instead of silently accepting.** A `GEOMETRIC_CENTER`/`APPROXIMATE`
result is hundreds of meters off; flagging it makes the modal lean on "drag the marker to adjust"
rather than dropping a pin on the wrong block.

## ADR-6 — Sole-writer allowlist (two edits)

**Decision.** In `backend/tests/invariants/test_sole_writer.py`:

1. Add `routers/puntos_solicitados.py` to the existing `ALLOWED_MODULES_PLANEACION_PUNTOS`
   (it writes the mirror), joining `planeacion_cruce.py` / `planeacion_asignaciones.py` /
   `inspector_asignaciones.py` / `integracion.py`.
2. Add a NEW independent constant + test for the new collection, mirroring every other per-collection
   block in the file:

```python
ALLOWED_MODULES_PUNTOS_SOLICITADOS = {
    APP_ROOT / "routers" / "puntos_solicitados.py",
    APP_ROOT / "main.py",   # module name == collection name → `from app.routers import puntos_solicitados`
}
def test_puntos_solicitados_literal_is_used_by_an_allowlisted_module():
    hits = _files_containing("puntos_solicitados")
    unexpected = hits - ALLOWED_MODULES_PUNTOS_SOLICITADOS
    assert not unexpected, f"unexpected puntos_solicitados reference(s): {sorted(unexpected)}"
    assert hits, "expected puntos_solicitados to be referenced by an allowlisted module by now"
```

`main.py` needs the entry for the identical reason `survey_cali` did (`test_sole_writer.py:174-184`):
the router module name matches the collection literal, so `main.py`'s import/mount line contains the
substring with zero Firestore access. Flag it read-only, do not obfuscate.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `backend/app/routers/puntos_solicitados.py` | Create | `POST`(create, admin, ADR-1 batch), `GET`(list + mirror-estado join ADR-4), `PATCH /{id}`, `DELETE /{id}`, `POST /geocode`(ADR-5); `REQUIRED_CLIENTS=("sismo",)` |
| `backend/app/services/geocode.py` | Create | Pure acceptance port (ADR-5), offline self-check |
| `backend/app/main.py` | Modify | Add `puntos_solicitados` to router imports + `_ROUTERS` tuple |
| `backend/tests/invariants/test_sole_writer.py` | Modify | ADR-6: new collection allowlist + `puntos_solicitados.py` into `ALLOWED_MODULES_PLANEACION_PUNTOS` |
| `web/js/puntos_solicitados.js` | Create | Tab cloned from `evaluaciones.js`; create modal + draggable-marker geocode INLINE in this one file (keep it simple) |
| `web/index.html`, `web/js/main.js`, `web/styles.css` | Modify | Nav button + `data-view-panel` section + `switchView()` branch + admin-only CSS gate (mechanical, per proposal) |
| `web/js/planeacion.js` | Modify | Rename button copy → "Crear Cluster" (copy-only, `:497`/`:2569`) |
| `formulario/js/form.js` | Modify | `buildPlaneacionCard` (~380): `es_solicitado` sorts first + distinct PRIORIDAD badge (own styling, not the alta/media/baja pill) |

## Interfaces / Contracts

**`planeacion_puntos/solicitado_{sid}` mirror (created fields):**
```
fuente:'solicitado'  registro_id:sid  clave_integracion:'PLN-…'  es_solicitado:true
nombre  direccion  barrio  comuna  coords:{lat,lon}
prioridad:'alta'  prioridad_score:<high>  tiene_survey:false
estado_asignacion:'pendiente'  cuadrilla_id:null  inspector_uid:null  prioridad_override:null
matched_at:<Timestamp>
```
(`ADMIN_DEFAULT_FIELDS` seeded once, same as the pipeline's first write.)

**`puntos_solicitados/{sid}`:** `nombre, comuna_corregimiento, barrio_vereda, nombre_solicitante,
telefono_solicitante, justificacion, coords, fotos[], clave_integracion, estado_seguimiento,
creado_por, creado_en`.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| pytest unit | ADR-1 atomic batch (both docs / neither), id-before-batch ordering, mirror field shape, `clave_integracion('solicitado',…)` determinism | fake Firestore double, `TestClient` (mirror `test_planeacion_asignaciones.py`) |
| pytest unit | `/geocode` accepted / low-confidence / 502 mapping; key never in response | fake Google responses |
| pytest unit | ADR-4 estado mapping (`hecho→visitado`, `no_aplica→excluido`) | pure function |
| pytest invariant | ADR-6: new `puntos_solicitados` allowlist + `puntos_solicitados.py` in planeacion_puntos set | existing literal scan |
| pytest guard | non-admin `POST`/`PATCH`/`DELETE` → 403, zero writes | `TestClient` |
| node pure | tab helpers (color-by-estado, sort, filter) mirroring `evaluaciones.test.mjs` | `node --test` |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, or executable-file classification. `/geocode`
is an outbound read-only HTTP proxy behind `require_auth` with the API key held server-side; the S3
photo path reuses the existing presigned `sign.py` flow unchanged (only the key prefix differs).

## Migration / Rollout

No migration. Both collections are additive. Rollback per proposal: revert frontend, unregister the
router, revert the rename; `puntos_solicitados` docs are read by nothing else and `solicitado_*`
mirror docs are safe to leave or bulk-delete. Requires `GOOGLE_MAPS_API_KEY` live in the backend env
(new usage; already present for the offline script) and the S3 presign env already used by `sign.py`.

## Open Questions

- [ ] None blocking. Live confirmation that the Railway FastAPI service has `GOOGLE_MAPS_API_KEY`
  set (it is currently consumed only by the offline `scripts/` container) is an operator step, not a
  design decision — flagged for the tasks/apply phase.
