# Auditoría: cobertura de datos en vivo vs. columnas del xlsx "Inspectores Depurado"

Fecha: 2026-09-15
Alcance: 19 columnas de `outputs/inspectores_depurado_seguimiento.xlsx` (hoja "Inspectores Depurado"), generado por `analisis_calidad_inspectores.ipynb`.
Método: mapeo inicial columna→fuente + verificación adversarial punto por punto (dos pasadas). Para cada columna se usa el **status corregido** (no la afirmación original) cuando la verificación adversarial marcó `claim_correct: false`.

---

## Tabla 1 — Disponible ya (`live_direct` / `live_derivable`)

Columnas que hoy se pueden poblar leyendo Firestore o la API en vivo (`stickers_api_live.json` / backend), sin depender de subir o mantener actualizados los archivos estáticos Vercel/Fase 2.

| Columna | Status final | Fuente exacta | Riesgo |
|---|---|---|---|
| `np_fuente` | `live_direct` | `backend/app/services/stickers_atencionsismo.py:470` — campo `inspector_fuente` (`"api"` / `"evaluacion"` / `"roster"` / `""`), ya emitido en cada registro normalizado del pipeline de stickers. | Ninguno relevante. |
| `n_stickers` | `live_derivable` | `outputs/stickers_api_live.json` campo `profesional.cedula` (en vivo) → notebook: `cedula_profesional = solo_digitos(profesional.cedula)`, `tiene_profesional`, `groupby("cedula_profesional").size()`. Regla A de identidad del profesional en `backend/app/services/stickers_atencionsismo.py` (líneas ~33-48, **no** 22-38 como se citó originalmente). | Cuenta todos los stickers con profesional identificado sin filtrar por origen ni fecha; correcto para esta columna, pero no confundir con `tiene_sticker_valido`. |
| `tiene_sticker_valido` | `live_derivable` | `fechaCreacion` + `origen` de `outputs/stickers_api_live.json`, comparados contra `CORTE_20AGO`. Fórmula real en el notebook (celda "`df_stickers`: una fila por sticker" y celda "Agregados de stickers por cédula del profesional"; ubicación real ~línea 1204 y ~línea 1431 del `.ipynb`, **no** las líneas ~364/~394-415 citadas originalmente). | **Bug de implementación activo**: la fórmula actual no filtra `origen == "sistema"` antes de comparar contra el corte del 20-ago. Los 1436 stickers `origen=firebase` comparten un único timestamp de importación (2026-09-07, posterior al corte) que los declara "válidos" por accidente — afecta ~60% de los stickers. Hay que agregar el filtro `origen == "sistema"` antes de confiar en esta columna. |

---

## Tabla 2 — Bloqueada, requiere referencia manual o archivo estático (Vercel / Fase 2 / snapshot CSV)

Columnas que no salen de una lectura en vivo de Firestore/API: dependen de `Listado verificado Fase 2.xlsx`, `inspectores_vercel-app.csv`, del CSV congelado `tecnicos atension sismo (12 sept 2026).csv`, o de artefactos propios de la auditoría (`accion_desactivar.csv`, `revision_manual.csv`).

| Columna | Status final | Fuente exacta | Riesgo |
|---|---|---|---|
| `identificacion` | `static_snapshot_derived` (corregido desde `live_direct`) | Notebook celda 67: `df_seguimiento["identificacion"] = df_seguimiento["cedula"].map(solo_digitos)`, con `cedula` alimentada por el CSV congelado (12-sep-2026) y sobrescrita por `cedula_fase2` cuando aplica. | No es lectura en vivo de Firestore; depende de un snapshot puntual y de overrides manuales de Fase 2. |
| `nombre_completo` | `static_snapshot_derived` (corregido desde `live_direct`) | Notebook, sección 8.1: `nombre_elegido = primero_no_vacio(nombre_fase2, nombre_vercel, nombre_main)`, con `.strip().title()`. | Depende de tener `Fase 2.xlsx` y el CSV de Vercel actualizados; ninguna de las tres fuentes es una llamada en vivo. |
| `codigo` | `static_csv_derived` / `remapped_snapshot` (corregido desde `live_direct`) | Notebook celda 16 (`main["codigoInspector"]`) + celda 67, remap contra el padrón `inspectores_vercel-app.csv` ("regla de oro": el código de Vercel manda). | El código final depende del padrón Vercel vigente, no del valor actual en Firestore. |
| `entidad` | `static_csv_derived` (corregido desde `live_direct`) | Notebook celda 67 paso 7: `mapa_entidad = dict(zip(vercel.cedula_key, vercel.entidad))`, `fillna("")`. | Sin match en el padrón Vercel, la columna queda vacía sin aviso. |
| `np` | `static_snapshot_derivable` (corregido desde `live_derivable`) | Notebook, sección 8.1: jerarquía `np_fase2` (si `en_fase2`) > `np_vercel` (si `en_vercel`) > `rango_main` > `""` (`np_fuente="ninguno"`). | Las tres fuentes son estáticas (Fase 2, Vercel, CSV main del 12-sep); no es el `profesional.rango` en vivo de la API. |
| `tarjeta_profesional` | `static_snapshot` / `csv_main_only` (corregido desde `live_direct`) | Notebook, sección 8.1: `tarjeta_profesional = matricula_main = main["addlInfo.matriculaProfesional"].fillna(main["addlInfo.matricula"]).fillna("")`. | Depende únicamente del CSV `main` congelado al 12-sep-2026; Fase 2 no aporta matrícula propia. |
| `correo_contacto` | `external_source_via_batch_join` (corregido desde `type_mismatch_risk`) | Notebook: el correo se toma directo del CSV externo "tecnicos atención sismo (12 sept 2026).csv" (sistema AtencionSismo), cruzado por cédula. | No es un simple mismatch de nombre de campo dentro del mismo doc: el Firestore `inspectores` real (export `inspectores_vercel-app.csv`, 153 filas) **no tiene ningún campo de correo**, solo `email` (placeholder de login, poblado en 6/153). Hoy no existe pipeline que traiga este dato a Firestore; depende de mantener el cruce por cédula contra el CSV externo. |
| `fase_np_faltante` | `static_snapshot_derivable` (corregido desde `live_derivable`) | Notebook celda 67 paso 6, función `_calcular_fase` (regex `^P?\s*(\d+)`, espejo de `faseInspector` en `web/js/utils.js:1058-1064`), aplicada sobre la columna `np` ya resuelta del xlsx. | Hereda la naturaleza estática de `np`: el cálculo en sí es mecánico y determinista, pero no es una lectura en vivo del API/Firestore. |
| `pasos` | `blocked_missing_reference` (confirmado) | Notebook celda 16 (`main["pasos"]`) corregido en celda 67 paso 4 con `pasos_export`, basado en `Listado verificado Fase 2.xlsx` (regla: P1/P2 → `[1]`; P3-P7 → `[1,2]`). | Depende de un archivo manual (Fase 2); el campo no existe en Firestore ni en la API. |
| `estado_sugerido` | `blocked_missing_reference` (confirmado) | Notebook celda 67 líneas 168-182: usa `accion_desactivar.csv` y `revision_manual.csv` (artefactos propios de esta auditoría), además de `codigo`/`tiene_sticker_valido` para la rama "activo". | La mayor parte de la clasificación depende de listas de revisión manual puntuales, no reproducibles solo con API + Firestore de hoy. |
| `fuente_dato` | `blocked_missing_reference` (confirmado) | Notebook celda 67 líneas 184-191: etiqueta textual (`"main+fase2+vercel"`, etc.) basada en `en_fase2`/`en_vercel`, cruces contra `Fase 2.xlsx` y el CSV de Vercel. | Ni la API ni Firestore exponen esta etiqueta de procedencia; es un artefacto propio de la auditoría. |

---

## Tabla 3 — Ambigua / riesgo de tipo

Columnas donde el campo sí puede leerse hoy de una fuente identificable, pero con un riesgo de tipo, de nombre de campo o de definición de negocio sin resolver.

| Columna | Status final | Fuente exacta | Riesgo |
|---|---|---|---|
| `id` | `type_mismatch_risk` (confirmado, evidencia corregida) | Notebook celda 16, `df_inspectores["id"] = main["id"]`, tomado del CSV congelado (12-sep-2026) — **no** de `stickers.py:397/473` (eso es el `uid` de Firebase Auth de otro sistema). | Los valores son UUID v4 (`0cdffa88-a835-...`), y en filas colapsadas de `df_seguimiento_final` puede aparecer el centinela literal `"GRUPO-EXTERNOS"`. No es un id numérico/secuencial ni un uid de Firestore. |
| `activo` | `type_mismatch_risk` (confirmado) | Firestore `inspectores/{uid}.activo`; `backend/app/routers/stickers.py:406-407`: `d.get("activo") is not False` ("faltante cuenta como activo, inspectores legado son anteriores al flag"). | Una lectura ingenua (`d.get("activo")` o `bool(...)`) invierte silenciosamente todos los inspectores legado sin el flag. |
| `num_telefono` | `type_mismatch_risk` (confirmado) | Dato real en Firestore `inspectores/{uid}.telefono` (CSV export confirma 743/743 filas pobladas); el código lee un campo distinto: `backend/app/routers/stickers.py:484` (`d.get("num_telefono")`). | `num_telefono` queda vacío para la mayoría de inspectores legado, cuyo teléfono real vive bajo el campo `telefono`. |
| `fase` | `live_derivable_con_advertencias` (corregido desde `live_derivable` sin matices) | `web/js/utils.js:1082-1091` (`faseKeyDe`/`faseInspector`), alimentada por el `fase` (1/2) y `profesional.rango` de la API en vivo. | Para ~60% de los registros (`origen == "firebase"`) el propio backend documenta que `fase` no refleja el NP real del inspector (todo ese origen llega con `fase: 2` por diseño de importación). El fallback además tiene 3 salidas (`FASE_I`/`FASE_II`/`SIN_DATO`), no 2 como se asumió inicialmente. |
| `codigo_inspector_original` | `ambiguous` (confirmado, cita de archivo corregida) | Notebook celda 67, línea 19: `codigo_inspector_original = codigo_inspector`, capturado **antes** del remap manual contra el padrón Vercel. Campo Firestore más cercano: `inspectores.codigo`, vía `backend/app/routers/stickers.py:487` (**no** `stickers_atencionsismo.py:487`, que en esa línea solo tiene `},`). | Firestore solo expone el valor **actual** de `codigo`; no hay historial de remaps. Requiere una decisión de producto sobre si hace falta conservar el valor previo al remap. |

---

## Veredicto

De las 19 columnas auditadas, solo **3 (≈16%)** — `np_fuente`, `n_stickers` y `tiene_sticker_valido` — son 100% viables hoy leyendo únicamente Firestore/la API en vivo, sin subir ni mantener actualizados Vercel o Fase 2 (y aun así, `tiene_sticker_valido` arrastra un bug de implementación por corregir sobre el filtro de `origen`). Sumando las que ya leen un campo en vivo pero con un riesgo de tipo/nombre por resolver en código (`activo`, `num_telefono`) la cifra sube a **5/19 (≈26%)**; el **74% restante (14/19)** depende de subir y mantener sincronizados los archivos estáticos Vercel/Fase 2, del CSV congelado del 12-sep-2026, o de referencias manuales de la propia auditoría.


## Nota 2026-09-19 — énfasis (D-ENFASIS)

El dueño pidió mostrar "el énfasis, la variable de la API que indica el posgrado". Verificado con datos en vivo:

- El endpoint de STICKERS de Atención Sismo **no** lo trae: su bloque `profesional` solo tiene `cedula`, `nombre`, `rango` y `tarjetaProfesional` (3.035 stickers, ninguna llave parecida a `enfasis`).
- Sí existe en el registro de técnicos (export "tecnicos atencion sismo", columna `addlInfo.enfasis`, la misma fuente de `addlInfo.matriculaProfesional`) y en el endpoint de visitados (`tecnicoVerificacion.enfasis`, no usado aquí).
- Cobertura en el registro: 263 de 743 filas con valor (35%); 79 de las 153 filas que tienen `codigoInspector` (52%).
- Es **texto libre** con 189 valores distintos ("Especialización en estructuras", "Estructuras", "Construccion", "Geotecnia", ...), no un indicador sí/no de posgrado. Se muestra tal cual (recortado); nunca se deriva un booleano.
- Es dato de la referencia (Blob), no del endpoint en vivo: hay que **republicar el bundle** para que aparezca en producción. Solo lo recibe el rol admin (bloque `depuracion`).
