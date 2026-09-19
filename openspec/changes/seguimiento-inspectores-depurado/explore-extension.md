# Exploration: complete depurado base in Seguimiento (extension)

Date: 2026-09-19. Baseline: backend HEAD ccbc71e, live parity run vs `outputs/inspectores_depurado_seguimiento.xlsx`.
User decision: the backend must serve the COMPLETE depurado base (notebook parity: 373 rows, 252 collapsed into GRUPO-EXTERNOS), integrated end to end in Seguimiento. This supersedes decision D1 ("identity anchor is Firestore identificacion; `referencia.main` never creates a Perfil").

## Live parity baseline (before this extension)
- Engine columns `np`, `np_fuente`, `fase`, `fase_np_faltante`, `estado_sugerido`: 116/116 on matched keys.
- Gaps: universe (129 backend rows vs 373), GRUPO-EXTERNOS 0 vs 252, contact columns 0%, `entidad` precedence (1 row).

## Notebook algorithm (analisis_calidad_inspectores.ipynb)
1. Universe = one row per `main` row (743, "tecnicos atencion sismo" CSV); Firestore is not involved.
2. Vercel / Fase 2 join: digits-only cedula first, then exact `nombre_norm`; first row wins (`drop_duplicates`).
3. Sticker aggregates joined by the main row's own pre-fix cedula; heuristics `es_cuenta_no_persona(correo, nombre)` and `cedula_sospechosa(cedula)` come from the same row.
4. Correction order (section 8.1): Fase 2 cedula overwrites main's (20 rows); code remap against Vercel (the wrong holder's code is cleared; the owner gets it only if it exists among the 743); exact-name unification (119 removed; survivor score `n_stickers>0 > codigo > en_vercel > en_fase2 > not no_persona > not cedula_sospechosa`, oldest `creado_en` tie-break, first-non-empty backfill); `pasos`; `np` = fase2 > vercel > rango_main; `fase` from `np`.
5. Chain 743 -> 624 (unification) -> 373 (collapse of 252, plus the group row).
6. Column sources: `nombre_completo` first non-empty of fase2/vercel/main name (title-cased); `entidad` Vercel by cedula only, else ""; `tarjeta_profesional` main `addlInfo.matriculaProfesional` else `addlInfo.matricula`; `num_telefono` digits of main `telefono`; `correo_contacto` main `correo` lowercased; `codigo` main `codigoInspector` (3-digit) after remap; `id` main uuid.
7. `estado_sugerido` first match: no_persona > candidato_desactivacion (id in `desactivar`) > revisar (id in `revision`) > activo (codigo or tiene_sticker_valido) > revisar. `desactivar`/`revision` are computed BEFORE remap from the original codigo and tiene_sticker (engine diverges on purpose: D7, D-REMAP).
8. GRUPO-EXTERNOS rule: `(cedula_sospechosa OR no_persona) AND (no ultimo_sticker OR dias_inactivo > 7) AND sin codigo`; members leave the table; one row `id="GRUPO-EXTERNOS"`, `np_fuente="ninguno"`, `fase="Fase I"`, `fase_np_faltante=True`, `activo=False`. 3 candidates excluded because they hold a code. The notebook has no `survey_cali` exemption (the engine's `exentos` is an addition).

## Engine changes required (backend/app/services/inspectores_depuracion.py)
- Universe: after the roster loop in `fusionar_identidad`, add one Perfil per `referencia.main` entry keyed by `cedula_key`; skip if the cedula already matches a roster Perfil (Firestore-first, main backfills empty fields). Vercel / Fase 2 stay overlays (no rows), as in the notebook.
- Stage-1 unification currently runs before overlays and stickers, so the survivor score is degenerate and a main row could beat the Firestore row: run D-P2 unification after overlays and stickers (notebook order) or rank "Firestore-backed" first.
- Sticker attribution uses exact-string `perfiles.get(identificacion)`: attribute through `_cedula_key` with a map that includes `cedulas_unificadas`, otherwise `ultimo_sticker=None` wrongly hides active people in the collapse.
- `remapear_codigos`: also clear the code from a different current holder; new revision items `remap_sin_duenio`, `remap_conflicto`.
- `entidad` precedence: Vercel > Firestore > main, by cedula match only (notebook + parity evidence).
- Fase 2 cedula fix when matched only by name (keep `cedula_sospechosa` on the ORIGINAL main cedula).
- Perf: dict indexes by cedula and by name instead of the linear `_buscar_entrada`.
- Edge cases: same cedula on two main rows with different names (first-wins + revision item, never silent overwrite); empty `nombre_norm` survives; D-P2 pair with different cedulas across sources; main rows without cedula must not be dropped silently.

## Reference contract (backend/app/services/inspectores_referencia.py + publisher)
- `EntradaReferencia` / `_parse_entrada` today drop `correo` and `tarjeta_profesional` (the publisher already emits them). Add optional fields with defaults: `nombre`, `telefono`, `codigo`, `creado_en`, `id`, `correo`, `tarjeta_profesional`.
- Keep `schema: 1` (a bump makes the deployed `parse_bundle` return None). Ship the tolerant backend first, then republish the bundle. Old bundles yield empty contact fields.
- PII stays admin-only: `depuracion` is added to `body` only when role is admin; the Blob-persisted LKG carries only the redacted payload. Downgrade/redact the INFO log of `alias_nombres` (`identidad_key` is now a cedula).

## Router
- No `cache.degraded` gate on `depuracion`: in degraded mode stickers are unattributable (redacted LKG), so with the full universe every person without a code becomes `candidato_desactivacion` and suspicious ones are wrongly collapsed. Return `activa=false`, `motivo="stickers_degradados"`.
- Payload ~200-300 KB (estimate); compute a few hundred ms, cached; `compute` already runs outside the lock.

## Frontend (web/js/seguimiento.js)
- Rows are created only from stickers/surveys (`ensureRow`), so zero-activity people never appear: seed rows from `depuracion.inspectores` when `depuracionActiva`.
- KPI semantics ("profesionales activos", averages, timeline) must not be diluted by empty rows.
- Add an `estado_sugerido` filter/column; search already handles cedula.
- Mass PDF export cap is 200 rows: default to rows with activity.
- Revision manual section: show the new motivos with name and cedula (admin only).
- Add an explicit banner when `depuracion` is degraded/absent; add a 400-row perf case.

## Decisions taken (defaults, user delegated: "work with what you have")
1. Show all zero-activity people, with an estado filter.
2. `entidad` precedence Vercel > Firestore > main by cedula.
3. Main-only rows anchored by cedula key; supersede the "Identity Anchor Is Firestore identificacion" requirement.
4. Out of scope: `pasos`, `activo`, `id`, `n_stickers`, `codigo_inspector_original` (only `codigo_original` if cheap).
5. Mass export restricted to rows with activity by default.

## Slicing (chain continues from branch -05; feature-branch-chain, each PR targeting the previous one)
| Slice | Content | Size |
|---|---|---|
| 06-referencia-contract | optional fields, parser, publisher emits new fields, old/new bundle tests | ~200 |
| 07-engine-universe | main Perfiles, cedula attribution, unify after overlays, entidad precedence, dict indexes | ~350-400 |
| 08-engine-remap | clear wrong holder, new revision motivos, Fase 2 cedula fix | ~300 |
| 09-router | degraded gate, log redaction, PII/cache checks, payload-size test | ~150 |
| 10-frontend | seed rows, KPI semantics, estado filter, export rule, new motivos, perf test | ~350-400 |
| 11-parity-harness | script comparing `depurar()` with the xlsx; rollout order deploy -> republish -> flip flag | ~150 |

## Acceptance (against the xlsx, live)
- `np`, `np_fuente`, `fase`, `fase_np_faltante`, `codigo`, `fuente_dato`, `identificacion`, `entidad`: >= 99% on matched keys.
- `estado_sugerido`: >= 99% excluding documented D7 / D-REMAP divergences (listed).
- `tarjeta_profesional`, `num_telefono`, `correo_contacto`: >= 99% where the source is non-empty.
- `nombre_completo`: compared through `normalizar_nombre`.
- Row count 373 +/- Firestore-only extras (13 today), documented; `n_colapsados` = 252 with the same 3 code-holding exclusions.
