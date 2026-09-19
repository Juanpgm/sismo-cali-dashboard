# Propuesta: seguimiento-inspectores-depurado

## Intención

La pestaña "Seguimiento" sirve identidad de inspectores en vivo (API Atención Sismo + Firestore crudo) sin las reglas de depuración de `analisis_calidad_inspectores.ipynb`: duplicados sin unificar, códigos sin remapear, ruido no colapsado, corte 20-ago mal aplicado. El backend debe calcular esa tabla depurada en vivo y cacheada, sin leer nunca el xlsx estático.

## Alcance

### Entra
- Tabla depurada en el backend (`np`/`np_fuente`, `fase`, `estado_sugerido`, `fuente_dato`) con caché TTL 5–10 min (patrón `EVALUACIONES_CACHE_TTL_SECONDS`).
- Datos de referencia (padrón Vercel, Fase 2, CSV base) publicados en Vercel Blob (`blob_lkg`), no en Firestore.
- Identidad única: `identificacion` de Firestore (`inspector_profiles()`); nunca la cédula derivada del email.
- Corte 20-ago solo para `origen=="sistema"`; limita `tiene_sticker_valido` pero no define activo/inactivo (D6).
- Cuentas no-persona: fila agregada con conteos, desplegable a detalle en el front.
- Dedupe de esos conteos contra `survey_cali` por nombre de persona.
- `np`/`np_fuente` resueltos por el backend para que el front no los pise con `profesional.rango`.

### No entra
- Job programado ni colección Firestore nueva.
- Historial de remaps (`codigo_inspector_original`) e `inspector_atribuido_id` (D1).
- Limpieza río arriba de Vercel/Fase 2; auto-fusión difusa 85–95 % (D3).
- Escrituras a la base real (desactivar/eliminar inspectores).

## Criterio humano — resolución propuesta

| Guía | Decisión |
|---|---|
| D-P1 códigos Vercel duplicados | Excluir del remap; exponer lista de revisión manual |
| D-P2 varias cédulas | Unificar solo por nombre exacto; reportar discrepancias |
| D1 sticker cambia de dueño | Atribuir al titular vigente, sin historial |
| D2 desactivar vs eliminar | Nunca hard-delete: excluir/marcar en la vista calculada |
| D3 difuso 85–95 % | No auto-fusionar (comportamiento actual del notebook) |
| D5 Fase 2 sin NP | `np=""`, `np_fuente="ninguno"`; no inventar `pasos` |

## Decisión 8 — CONFIRMADA

`web/js/seguimiento.js:341-378` agrupa por identidad con "primer no vacío gana" sobre `insp.np`. Confirmado con evidencia de código: el backend emite `np`/`np_fuente` ya resueltos (fase2 > vercel > rango_main > ninguno) y el front deja de rellenar con `rango` crudo. Usuario aprobó la propuesta completa sin cambios.

## Bloqueante de seguridad — RESUELTO

Vercel Blob en este repo se publica normalmente como **público** (`deploy/blob_sync.py`). Para el bundle de referencia (Vercel/Fase2/CSV base) se usa en cambio `access: 'private'` de Vercel Blob (confirmado como control de acceso real, no oscuridad) — el backend lee con `BLOB_READ_WRITE_TOKEN`, nunca se publica una URL pública. `correo_contacto` puede incluirse en el bundle sin riesgo de exposición.

## Ampliación de alcance (post-aprobación, via /goal del usuario)

- Nueva subpestaña/sección "Revisión manual" en Seguimiento: muestra los pendientes que hoy solo vive en la hoja "Pendientes revisión manual" del xlsx (remaps en conflicto, sin dueño, duplicados internos de Vercel, revisión manual) — servidos por el campo `revision_manual` que el backend ya contempla en `depuracion`.
- Criterio de éxito explícito: estructura/reportes equivalentes al xlsx, pero 100% desde datos reales agregados vía API/Firestore en vivo, nunca leyendo el xlsx estático.

## Capabilities

### New
- `inspectores-depurado`: tabla depurada por profesional, cacheada, servida por el backend.
- `datos-referencia-blob`: publicación/lectura de padrón Vercel, Fase 2 y CSV base en Vercel Blob.

### Modified
- `seguimiento`: el front consume identidad depurada del backend en vez de derivarla.

## Áreas afectadas

| Área | Impacto | Qué cambia |
|---|---|---|
| `backend/app/services/` | Nuevo | Motor de depuración |
| `backend/app/routers/stickers.py` | Modificado | Endpoint + caché TTL |
| `backend/app/services/blob_lkg.py` | Modificado | Carga de referencia |
| `backend/app/services/survey_cali.py` | Lectura | Dedupe de agregados |
| `web/js/seguimiento.js` | Modificado | Consume `np`/`np_fuente`; fila desplegable |
| `backend/requirements.txt` | Modificado | `rapidfuzz` |

## Riesgos

| Riesgo | Prob. | Mitigación |
|---|---|---|
| Blob de referencia desactualizado | Alta | `fuente_dato` + fecha visible; degradar a `np_fuente="ninguno"` |
| `origen=="firebase"` sin fecha real | Alta | Excluir del corte; informativo |
| Latencia del cálculo por request | Media | Caché TTL + último-valor-bueno |
| `dias_inactivo` contra reloj de pared | Media | Calcular en request, no en caché |
| `rapidfuzz` en deploy Railway | Baja | Validar build antes de usarlo |

## Rollback

Feature flag de entorno: apagado devuelve el payload actual sin depurar. Revertir el commit del front y del router; el Blob de referencia queda inerte.

## Dependencias

`BLOB_READ_WRITE_TOKEN` activo; padrón Vercel, Fase 2 y CSV base publicados; confirmación de la decisión 8.

## Criterios de éxito

- [ ] Ninguna lectura del xlsx estático en runtime.
- [ ] `np`/`fase` coinciden con el notebook sobre el mismo insumo.
- [ ] Sin conteos duplicados entre stickers y `survey_cali`.
- [ ] Corte 20-ago aplicado solo a `origen=="sistema"`.
- [ ] Respuesta cacheada bajo TTL sin degradar la pestaña.

---

# Extension 2026-09-19: complete base

> Appended after PRs 1-5 shipped (backend HEAD `ccbc71e`). The original text above is
> unchanged and remains the record of the first delivery. This section is written in English
> per the artifact language contract. Source of truth: `explore-extension.md`.

## Intent

The delivered engine serves a **partial** depurado table: 129 rows against the notebook's 373,
`GRUPO-EXTERNOS` empty (0 vs 252 collapsed), and every contact column at 0% fill. The live parity
run shows the *rules* are right — `np`, `np_fuente`, `fase`, `fase_np_faltante`, `estado_sugerido`
match 116/116 on keys present in both — but the *universe* is wrong. The backend must serve the
COMPLETE depurado base, at notebook parity, integrated end to end in Seguimiento.

## Scope

### In

- Universe = Firestore roster ∪ `referencia.main`, keyed by cédula (`cedula_key`).
- Cédula-keyed sticker attribution through an alias index (original, Fase-2-fixed and unified-away keys).
- D-P2 unification moved after overlays and sticker attribution (notebook order).
- `entidad` precedence Vercel > Firestore > main, cédula match only.
- Remap clears the código from the wrong current holder; new motivos `remap_sin_duenio`, `remap_conflicto`.
- Fase 2 cédula fix when matched by name only, keeping `cedula_sospechosa` on the ORIGINAL cédula.
- Reference bundle gains optional contact/identity fields (`nombre`, `telefono`, `codigo`,
  `creado_en`, `id`, `correo`, `tarjeta_profesional`) at **`schema: 1`**, parsed tolerantly.
- Router: `depuracion` gated off when the sticker snapshot is degraded (`motivo="stickers_degradados"`).
- Frontend: seed rows from `depuracion.inspectores`, `estado_sugerido` filter, KPI semantics that
  zero-activity rows do not dilute, mass PDF export defaulting to rows with activity, degraded banner.
- A parity harness comparing `depurar()` against `outputs/inspectores_depurado_seguimiento.xlsx`.

### Out

- `pasos`, `activo`, `id`, `n_stickers`, `codigo_inspector_original` as exposed columns
  (`codigo_original` only if it falls out for free) — decision 4 of `explore-extension.md`.
- A `survey_cali` exemption inside the collapse rule beyond the already-shipped `exentos`
  (the notebook has none; any resulting delta is registered as a documented divergence).
- Upstream cleanup of Vercel/Fase 2; fuzzy auto-merge (D3 stands).
- Any write to the live inspector record (D2 stands).

## Approach

Six chained slices, each a reviewable PR under the 400-line budget, continuing the existing
`feature-branch-chain` from `feat/seguimiento-inspectores-depurado-05-mobile-overflow`:

| Slice | Branch | Content |
|---|---|---|
| 06 | `…-06-referencia-contract` | optional bundle fields, tolerant parser, publisher emits them |
| 07 | `…-07-engine-universe` | main Perfiles, cédula attribution, unify-after-overlays, `entidad`, dict indexes |
| 08 | `…-08-engine-remap` | clear wrong holder, new motivos, Fase 2 cédula fix |
| 09 | `…-09-router` | degraded gate, log redaction, PII/cache checks, payload-size test |
| 10 | `…-10-frontend` | seed rows, KPI semantics, estado filter, export rule, motivos, perf test |
| 11 | `…-11-parity-harness` | `depurar()` vs xlsx comparator + rollout runbook |

## Supersedes D1

The scope line *"Identidad única: `identificacion` de Firestore (`inspector_profiles()`); nunca la
cédula derivada del email"* and the spec requirement **"Identity Anchor Is Firestore
`identificacion`"** are superseded: `referencia.main` now DOES create a Perfil when its cédula has
no Firestore counterpart. Two clarifications, because "D1" is overloaded in these artifacts:

- The **prohibition still stands**: an email-derived cédula is never an identity key. The new anchor
  is `cedula_key` (Firestore `identificacion` digits, else `main` cédula digits).
- `design.md`'s **D1** (emit an index, not per-professional counts) is **not** superseded, nor is the
  "Criterio humano" row D1 (attribute a sticker to the current titular, no ownership history).

## Risks

| Risk | Prob. | Mitigation |
|---|---|---|
| Republishing the bundle before the tolerant backend is deployed | Medium | Schema stays `1` and new keys are additive, so an old backend ignores them; rollout order is still deploy → republish |
| Universe growth turns silent gaps into 244 extra `candidato_desactivacion` rows | High | Degraded gate (`activa=false`) + the estado filter + KPI rows-with-activity semantics |
| Payload grows to ~200-300 KB | Medium | Cached by snapshot identity (D4); explicit payload-size test in slice 09 |
| PII surface widens (`correo`, `telefono`, `tarjeta_profesional`) | Medium | Private Blob, `depuracion` only added to `body` for role admin, INFO-log redaction of `alias_nombres` |
| Front rendering 400+ rows | Medium | `web/js/seguimiento-perf.test.mjs` 400-row case in slice 10 |
| Parity regressions hidden by aggregate percentages | Medium | Harness reports per-column mismatch lists, not just a score |

## Rollback

`SEGUIMIENTO_DEPURACION` **stays off** (unset/`0`) until the parity harness passes live against the
xlsx. With the flag off the response is byte-identical to the pre-change shape and the frontend takes
its legacy path, so every slice is mergeable without user-visible effect. Rollback = flip the flag
back to `0`; the bundle and the chain stay inert. Slice 10 (frontend) must not merge to the tracker
before slice 11 confirms parity.

## Success criteria (parity, live, against the xlsx)

- [ ] `np`, `np_fuente`, `fase`, `fase_np_faltante`, `codigo`, `fuente_dato`, `identificacion`,
      `entidad` ≥ 99% on matched keys.
- [ ] `estado_sugerido` ≥ 99% excluding the enumerated D7 / D-REMAP divergences.
- [ ] `tarjeta_profesional`, `num_telefono`, `correo_contacto` ≥ 99% where the source is non-empty.
- [ ] `nombre_completo` compared through `normalizar_nombre`.
- [ ] Row accounting `[MODIFIED 2026-09-19]`: `backend = matched + firestore-only + D-EXENTOS survivors + unexplained`; every backend-only row is a Firestore-only extra, a D-EXENTOS survivor or listed as unexplained (the stale "373 ± 13" count is gone).
- [ ] `n_colapsados` = 252, with the same 3 código-holding exclusions.
