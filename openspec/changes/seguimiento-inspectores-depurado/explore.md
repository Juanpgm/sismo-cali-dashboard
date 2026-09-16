# Exploración: lógica de depuración de inspectores (notebook → backend)

Change: `seguimiento-inspectores-depurado`. Fuente: sdd-explore, 2026-09-15.

## Mapa de líneas del notebook (`analisis_calidad_inspectores.ipynb`)

- Sec 0 "Contexto y fuentes": líneas 242-296
- Sec 1 "Carga de datos" (utils, `es_cuenta_no_persona`/`cedula_sospechosa`): 304-1085 (funciones en 1111-1520)
- Sec 3 "DataFrame maestro de inspectores": 1086-1690
- Sec 4 "Verificación... Hamon" (4.a-4.k): 1694-4629
- Sec 5 "Problemas encontrados": 4630-5061
- Sec 6 "Guía paso a paso" (6.0-6.9, guías D-P1, D-P2, D1-D7): 5062-6194
- Sec 8.1-8.5 (construcción de "Inspectores Depurado"): 6195-7209 — acá vive el código ejecutable que responde np/fase/estado_sugerido/fuente_dato; la sección 6 da solo la intención en texto.

## Q1 — `np` / `np_fuente` (celdas 6462-6474, sección 8.1 paso 5)

```python
if r.en_fase2 and r.np_fase2:      np_resuelto=r.np_fase2; np_fuente="fase2"
elif r.en_vercel and r.np_vercel:  np_resuelto=r.np_vercel; np_fuente="vercel"
elif r.rango_main:                 np_resuelto=r.rango_main; np_fuente="main"
else:                              np_resuelto=""; np_fuente="ninguno"
```
Jerarquía: fase2 > vercel > rango_main (`addlInfo.rango` de la CSV base) > ninguno. `np_vercel`/`np_fase2` se arman en sección 3 (1310-1329) vía `cruzar()`: cédula primero, `nombre_norm` como fallback.

## Q2 — `fase` (celdas 6476-6490)

```python
FASE_NP_RE = re.compile(r"^P?\s*(\d+)", re.IGNORECASE)
def _calcular_fase(np_valor):
    m = FASE_NP_RE.match(str(np_valor or "").strip())
    if not m: return "Fase I", True   # fase_np_faltante=True
    return ("Fase II" if int(m.group(1)) >= 3 else "Fase I"), False
```
Idéntico byte a byte a `faseInspector()` en `web/js/utils.js:1058-1064` (mismo regex, mismo umbral >=3).

## Q3 — `estado_sugerido` (celdas 6517-6531 base + 6700-6710 override post-colapso)

Orden de precedencia:
1. `"no_persona"` si `es_cuenta_no_persona`
2. `"candidato_desactivacion"` si `id ∈ ids_desactivar` (§6.6/P5: sin sticker Y sin código Y NO en_fase2 Y NO en_vercel, código en 5303-5309)
3. `"revisar"` si `id ∈ ids_revision` (sin sticker Y sin código Y (en_fase2 O en_vercel), código en 5318-5324)
4. `"activo"` si `codigo` presente O `tiene_sticker_valido`
5. si no, `"revisar"` (fallback)
6. `"grupo_externos_agrupado"` — hardcodeado solo en la fila agregada de §8.2 (6700-6710), pisa lo que tuviera cada fila colapsada individualmente.

**Contradicción encontrada**: la guía D6 (§6.8, 5801-5815) recomienda explícitamente "aplicar la regla a la visita, no auto-derivar la desactivación de la persona" — pero el branch 4 de `estado_sugerido` usa `tiene_sticker_valido` (el agregado post-corte) como uno de sus dos triggers. El código hace justo lo que la guía dice que no hay que hacer.

## Q4 — `cedula_sospechosa` / `es_cuenta_no_persona` (sección 3, líneas 1433-1477)

```python
PATRONES_CORREO_NO_PERSONA = ("@import.local", ".internal", "migrated", "@sismo.cali.gov.co")
PATRONES_NOMBRE_NO_PERSONA = ("prueba", "migracion", "brigada", "secretaria de educacion", "test ")
def es_cuenta_no_persona(correo, nombre) -> bool:
    if any(p in correo.lower() for p in PATRONES_CORREO_NO_PERSONA): return True
    return any(p in normalizar_nombre(nombre) for p in PATRONES_NOMBRE_NO_PERSONA)

def cedula_sospechosa(cedula: str) -> bool:
    if not cedula: return True
    if not 6 <= len(cedula) <= 10: return True
    return len(cedula) == 10 and not cedula.startswith("1")
```
Ambas documentadas explícitamente como heurísticas de formato/patrón para PRIORIZAR revisión, no para baja automática (docstrings, líneas 1439-1443, 1464-1468).

## Q5 — `fuente_dato` (celdas 6533-6540)

`"main+fase2+vercel"` / `"main+fase2"` / `"main+vercel"` / `"main"` según combinación de `en_fase2`/`en_vercel`. Gotcha: la fila GRUPO-EXTERNOS tiene un 5to valor con forma distinta: `f"grupo_agregado (main, {N} registros colapsados)"` (línea 6707) — string dinámico, no uno de los 4 valores enum. Cualquier contrato/tipo de `fuente_dato` en el backend tiene que contemplar este outlier.

## Q6 — Regla del 20 de agosto

- `CORTE_20AGO = 2026-08-20 UTC` (línea 371). `valido_post_20ago = fecha >= CORTE_20AGO` se calcula en `construir_df_stickers` (línea 1204) **para todos los stickers sin importar `origen`**.
- Regla de negocio (§0, 271-296; confirmada contra datos reales en §4.j, 4049-4149): solo `origen=="sistema"` tiene `fechaCreacion` real; `origen=="firebase"` comparte un único timestamp de importación masiva (hoy `2026-09-07`, que coincidentemente es posterior al corte) — así que los stickers firebase quedan `valido_post_20ago=True` por coincidencia, no por fecha verificada.
- El archivo exportado `stickers_pre_20ago.csv` (código §6.8, bloque "I.", líneas 5387-5399) sí filtra correctamente `origen=="sistema"` antes de escribir — pero el agregado por inspector `n_stickers_validos_post_20ago`/`tiene_sticker_valido` (sección 3, líneas 1414/1431) **no** está limitado a `sistema` — bug latente hoy enmascarado por los datos (la API en vivo devuelve 0 stickers pre-corte al 2026-09-14; el volcado de Hamon tenía 47, afectando a 24 profesionales, §4.j).
- `n_stickers` (crudo, con backfill en unificación de duplicados, `CAMPOS_BACKFILL` línea 6421) vs `tiene_sticker_valido` (filtrado post-corte, **no** está en `CAMPOS_BACKFILL`, nunca se rellena en la unificación) — inconsistentes entre sí: una fusión de duplicados puede dejar `tiene_sticker_valido=False` en el sobreviviente aunque el duplicado fusionado tuviera stickers válidos recientes.

## Q7 — Construcción de `df_inspectores` (sección 3, líneas 1251-1332)

Una fila por registro de la base principal (no por persona — el dedupe es un paso posterior). Campos de identidad directos de la CSV: `id`, `nombre`, `cedula` (→`solo_digitos`), `correo`, `telefono`, `codigo_inspector` (→`codigo3`), `pasos_raw`→`pasos` (parseado), `activo` (string crudo "true"/""), `rango_main` (`addlInfo.rango`), más columnas de cruce Vercel/Fase2 vía `cruzar()` (clave cédula, fallback `nombre_norm`) y agregados de stickers (`n_stickers*`, `primer_sticker`, `ultimo_sticker`, `codigos_usados_en_numero`, `tiene_sticker`, `tiene_sticker_valido`). `es_cuenta_no_persona`/`cedula_sospechosa` se calculan al final (Q4).

## Lógica adicional relevante para "qué tan portable es esto"

- **Remap de código** (`df_reconciliacion`/`misma_persona`, §4.d líneas 2524-2556): para cada `codigo` de Vercel, compara al titular actual en la base principal — match por `cedula` o `cedula_fase2` **o** `rapidfuzz.fuzz.token_sort_ratio(nombre_norm) >= 90`. `remap` = filas donde `misma_persona==False`, excluyendo códigos de una entrada Vercel duplicada internamente (`dup_v`, caso Adán Durán Yomayusa, códigos 097/127) — esos van a revisión manual (§8.3).
- **Unificación de duplicados exactos** (§6.5/§8.1 "B", líneas 5260-5298): solo se fusionan automáticamente grupos con `nombre_norm` EXACTO. Score de sobreviviente: `has_sticker > has_codigo > en_vercel > en_fase2 > not_no_persona > not_cedula_sospechosa`, empate por `creado_en` más antiguo. El backfill es "primero no vacío gana", **no suma** — si sobreviviente y duplicado tienen `n_stickers>0`, el conteo del duplicado se descarta silenciosamente. Los duplicados fusionados se **eliminan definitivamente** de la tabla calculada (no se desactivan, contra lo que recomienda la guía D2 para la base real).
- **Colapso GRUPO-EXTERNOS** (§8.2, líneas 6668-6716): `(cedula_sospechosa OR es_cuenta_no_persona) AND (ultimo_sticker es nulo OR dias_inactivo > 7)`, excluye a quien tiene `codigo` no vacío. `dias_inactivo` se mide contra `pd.Timestamp.now()` en tiempo de ejecución — corte de reloj de pared, relevante para el diseño del TTL de caché.

## 8 puntos que la propia sección 6 marca como "requiere criterio humano" (guías D-P1, D-P2, D1-D7)

| Guía | Tema | ¿Automatizado hoy? |
|---|---|---|
| D-P1 (6.2) | Cuál de 2 códigos Vercel duplicados sobrevive | No — asume que el padrón Vercel ya viene limpio |
| D-P2 (6.3) | Misma persona, varias cédulas | Parcial — el fix de P2 aplica ciegamente a todas las `discrepancias`, no distingue typo de cuenta duplicada como matiza la guía |
| D1 (6.4) | Atribución de sticker cuando el código cambia de dueño | No — la columna recomendada `inspector_atribuido_id` no está implementada |
| D2 (6.5) | Desactivar vs. eliminar duplicado | El código elimina definitivamente en la vista calculada (la guía recomienda desactivar para la base real) |
| D3 (6.5) | Matches de nombre difusos 85-95 | No — nunca se fusionan automáticamente |
| D4 (6.6) | Cuentas no-persona que SÍ tienen stickers | No — van a `no_persona`/GRUPO-EXTERNOS sin decisión de atribución caso por caso |
| D5 (6.7) | Filas de Fase2 sin NP / "NO SIRVE" | No — pasos queda intacto, el fallback de la guía (`[1]` salvo evidencia) no está implementado |
| D6 (6.8) | La regla del 20-ago no debería auto-desactivar a la persona | Contradicho — `estado_sugerido` sí la usa (ver Q6/Q3) |
| D7 (6.8) | Stickers sin atribución | No aplica a la tabla a nivel inspector (es a nivel sticker) |

## Áreas afectadas

- `analisis_calidad_inspectores.ipynb` — secciones 0,1,3,4,5,6,8
- `backend/app/routers/stickers_atencionsismo.py`, `backend/app/routers/stickers.py` — resolución de identidad por sticker, a complementar con un agregado por inspector cacheado
- `backend/app/jobs/` — ubicación candidata para un nuevo job `inspectores_depurado` (patrón: `dashboard_refresh.py`, `cruce_sticker.py`)
- `backend/requirements.txt` — necesita agregar `rapidfuzz` (no está hoy); `pandas`/`openpyxl` ya están (usados por `refresh_data.py`)
- `web/js/utils.js:1058-1064` (`faseInspector`) — ya implementa la misma regla de fase; hay que mantenerla sincronizada o reemplazarla consumiendo el `fase` calculado por el backend directamente
- `context/EDA_HAMON/01_Insumos/inspectores_vercel-app.csv`, `.../Listado verificado Fase 2.xlsx` — pasarán a ser colecciones Firestore de referencia; sus problemas de calidad actuales (3 identidades Vercel duplicadas, filas Fase2 sin NP) se heredan igual salvo que se limpien antes/durante la ingesta

## Recomendación

Resolver los 8 puntos de decisión explícitamente durante `sdd-propose`, en vez de portar 1:1 y heredar en silencio la contradicción D6 y los otros 7 gaps de criterio humano. El notebook no es una spec terminada: la sección 6 marca 7 pasos como "requiere criterio humano", y uno de los comportamientos automatizados (`estado_sugerido` usando `tiene_sticker_valido`) contradice su propia recomendación escrita (D6).

## Riesgos

- La contradicción D6 puede estar afectando ya en silencio `estado_sugerido="activo"` en el xlsx de referencia; hoy queda enmascarada porque la API en vivo devuelve 0 stickers pre-20-ago.
- `tiene_sticker_valido` no está limitado a `origen=="sistema"` a nivel de flag — bug latente, "seguro por accidente" solo mientras el timestamp de importación de firebase sea posterior al corte.
- Los duplicados del padrón Vercel (caso Adán Durán) siguen sin resolver río arriba; migrar los CSV de Vercel/Fase2 a colecciones Firestore de referencia tal cual los hereda salvo que se limpien primero.
- `n_stickers` (crudo, con backfill) vs `tiene_sticker_valido` (filtrado, sin backfill) quedan inconsistentes entre sí tras la unificación de duplicados — una caché en vivo construida igual expone la misma inconsistencia a los usuarios finales.
- Nueva dependencia `rapidfuzz` para el fallback de nombre difuso en el remap de código (`misma_persona`) — riesgo bajo pero hay que confirmarla contra el pipeline de deploy de Railway.
- La ventana de inactividad de 7 días de GRUPO-EXTERNOS es relativa al reloj de pared — hace falta una decisión explícita de TTL/cadencia de recálculo de caché.

## Listo para propuesta

Sí, con un llamado de atención al usuario: el núcleo algorítmico (np/np_fuente, fase, estado_sugerido, fuente_dato, ambas heurísticas, remap de código, unificación de duplicados exactos, colapso GRUPO-EXTERNOS) está completamente documentado, citado a celda/línea exacta, y es mecánicamente portable a Python. Pero los 8 puntos de decisión (D-P1, D-P2, D1-D7) más la auto-contradicción D6/`estado_sugerido` necesitan respuesta explícita del usuario antes/durante `sdd-propose` — el propio notebook los marca como de criterio humano, no de código.
