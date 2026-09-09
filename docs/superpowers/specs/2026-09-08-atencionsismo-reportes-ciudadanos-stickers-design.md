# Diseño: Atención Sismo como fuente de "Reportes ciudadanos" y "Stickers"

Fecha: 2026-09-08
Estado: propuesto
Contrato de la API: `docs/api-informe-json (docs-api-atencionsismo).md`
Exploración previa: `docs/informe-exploratorio-api-atencionsismo.md`

## 1. Objetivo

1. Nueva pestaña **Reportes ciudadanos** en el dashboard, alimentada por `GET /api/informe/json`.
2. La pestaña **Stickers** (segmento Evaluaciones) pasa a alimentarse de `GET /api/informe/stickers`, conservando la clasificación **Fase I / Fase II**.

## 2. Hallazgos que condicionan el diseño

| Hecho | Fuente | Consecuencia |
|---|---|---|
| `informe/stickers` devuelve 9 campos: `id, direccion, latitud, longitud, numero, personaAfectada, origen, color, colorEtiqueta`. No trae inspector, NP, fotos, fecha ni alcance. | Spec §Stickers JSON | Fase I/II no puede leerse del endpoint. Hay que derivarla. |
| `color`/`colorEtiqueta` son el cartel ATC-20 (verde, amarillo, rojo), no la Fase. | Spec | Mapean 1:1 a `INSPECCIONADA / USO_RESTRINGIDO / INSEGURO`. |
| `origen: "firebase"` marca stickers importados desde nuestro Firestore (`gpsOrigen === "firebase-sticker"`). | Spec §sticker | Para esas filas `numero` debe coincidir con nuestro `codigo_edificacion`. |
| Nuestro código es `76001-{área}-{códigoInspector 3 dígitos}{consecutivo 4+ dígitos}`, por ejemplo `76001-1-0040001`. | `formulario/js/logic.js` `buildCodigo` | El código del inspector viaja dentro del número de sticker. |
| Fase II = inspector con NP de categoría P3 o superior; el NP vive en `inspectores/{uid}.NP`. | `web/js/utils.js` `faseInspector`, `stickers.py` `_np_by_uid` | Con el código de inspector se llega al NP sin pasar por la evaluación. |
| El cache local de `informe/json` (25 de agosto) no trae `sticker`. | Perfilado local | Hay que verificar con credenciales reales que el objeto llega. |
| `fechaCreacion` es texto `es-CO` ("martes, 18 de agosto de 2026, 06:33 p. m."). | Perfilado local | Se parsea en el backend a ISO. |
| No hay credenciales v2 en el entorno local ni en el repo. | Verificado | La tarea 0 del plan es un gate manual. |

### 2.1 Resultado de la exploración con credenciales reales (2026-09-08, tarea 0)

| Pregunta | Resultado |
|---|---|
| ¿`informe/json` incluye `sticker` en cada fila? | Sí, en el 100 % de las filas. Además aparece un campo nuevo `urbano` ("Sí") que la spec no documenta. |
| ¿`numero` de origen `firebase` coincide con nuestro formato? | Sí: 1 436 de 1 470. Los 34 restantes vienen como "Sin código". Cero códigos duplicados, ids únicos (UUID). |
| ¿Y los de origen `sistema`? | 749 de 783 vienen "Sin código" (Fase "sin dato"). Los otros 34 usan NUESTRO formato con códigos de brigada reales (070, 131, 027...), así que el roster también les da NP. |
| ¿`descripcion` trae datos personales? | Sí, en baja proporción: en 600 reportes, 7 con patrón de teléfono y 11 con patrón de cédula. Se enmascaran (ver D5). |
| Universo actual | KPIs: 20 075 reportes, 13 152 inmuebles, 2 933 verificados. Stickers: 2 253 (973 rojos, 743 amarillos, 537 verdes). 76 % sin `personaAfectada`. |

El cache local de reportes (14 804 filas, 25 de agosto) está desactualizado en más de 5 000 reportes: el job de refresh no corre desde entonces con credenciales válidas.

## 3. Decisiones

### D1. Fase I/II sobre stickers de Atención Sismo: derivación por código de inspector (ahora el fallback — ver Extensión contrato v3)

Regla, en orden — esta sección describe cómo se completa `inspector.np` (y de ahí, hoy solo como FALLBACK de la Fase; ver más abajo):

1. Si `numero` parsea como `76001-{a}-{iii}{cccc}` y existe una evaluación en Firestore con ese `codigo_edificacion`, se toma `inspector.np` de esa evaluación (join ya cacheado en `EvaluacionesCache`). Además se enriquecen `fecha`, `fotos`, `inspector.*`, `alcance`, `comentarios`.
2. Si no hay evaluación pero el código de inspector `iii` existe en el roster `inspectores` (campo `codigo`), se toma `NP` del roster.
3. Si no hay forma de conocer el NP, la Fase (por esta vía) es **"sin dato"**. Nunca se muestra Fase I por defecto en filas de Atención Sismo. La regla actual (NP vacío = Fase I) se mantiene solo para la fuente Firestore, donde toda evaluación tiene inspector.

Si hay evaluación, su NP es autoritativo aunque esté vacío. El roster solo aplica cuando no hay evaluación, porque los códigos de brigada se reutilizan al borrar un inspector y una evaluación vieja podría heredar el NP del inspector nuevo.

**Vigencia (contrato v3, 2026-09-08):** para la pestaña Stickers, `inspector.np` derivado aquí ya NO es la fuente primaria de la Fase — es el FALLBACK que usa `faseDe` cuando el campo `fase` de la API (ver Extensión contrato v3 más abajo) no es `1` ni `2`. `inspector.np` en sí sigue completándose exactamente igual (identidad/NP sin cambios) y sigue siendo la fuente de Fase para la pestaña Evaluaciones (Firestore).

Extensión (2026-09-08): cuando NO hay evaluación, además del NP se completan `nombre_completo`, `identificacion`, `entidad` y `uid` desde el mismo documento del roster (`inspectores` por código de brigada) — mismo criterio de autoridad que el NP: solo aplica sin match, nunca se mezcla con una evaluación existente.

Extensión (2026-09-08, fix de mala atribución): el paso 2 anterior es un riesgo real de atribución — los códigos de brigada se reutilizan al borrar un inspector, así que un sticker viejo puede terminar mostrando el nombre y la cédula reales de quien HOY tiene ese código, sin relación con ese registro. Cada registro trae ahora `inspector_fuente: "evaluacion" | "api" | "roster" | ""` (`"evaluacion"` con match en Firestore, `"api"` con identidad tomada del `profesional` propio de la API — ver Extensión contrato v3 más abajo —, `"roster"` sin match y con al menos uno de `nombre_completo`/`identificacion`/`entidad` no vacío en el roster — `uid` NO cuenta, porque `inspector_profiles` siempre lo completa con el id del documento de Firestore y por sí solo no identifica a nadie —, `""` en cualquier otro caso, incluida una entrada de roster que solo trae `np`). El frontend usa este campo para nunca presentar una identidad de roster o de API con la misma confianza que un match verificado (lista, modal, PDF, xlsx).

La regla de negocio `faseInspector(np)` no cambia.

#### Extensión contrato v3 (2026-09-08)

La API de atencionsismo agregó tres campos por sticker: `fase`, `profesional` (`{cedula, nombre, rango}`) y `fotografias`.

- **`fase` SÍ es la Fase de negocio para la pestaña Stickers.** El equipo desarrollador de la API de atencionsismo confirmó (2026-09-08) que `fase` es su propia variable de Fase — `1` = Fase I, `2` = Fase II — según el proceso propio de atencionsismo (paso 1 vs paso 2 / evaluación especializada), no un artefacto de almacenamiento. `evaluaciones.js` `faseDe` lo usa directamente para registros `fuente === "atencionsismo"`, cayendo a `inspector.np` (vía `faseInspector`) solo cuando `fase` no es exactamente `1` ni `2`. Nota factual: toda fila importada de NUESTRO Firebase (`origen == "firebase"`) trae `fase: 2` sin importar el NP real del inspector en Firestore (verificado: 1068 de 1436 filas así). La pestaña Evaluaciones (fuente Firestore) sigue clasificando esas mismas evaluaciones por NP del inspector, sin cambios — por diseño, las dos pestañas pueden mostrar una Fase distinta para el mismo registro, porque ahora son dos señales independientes.
- **`profesional` solo es confiable cuando `origen` (recortado, en minúsculas) es `"sistema"`.** La misma importación que escribe `fase: 2` en esas filas también escribió `profesional` en esas filas, y su confiabilidad ahí no está verificada — para filas `origen == "firebase"` (o en blanco/desconocido) NUESTRO Firestore es la fuente de verdad y `profesional` se ignora por completo, aplicando el fallback de roster por código de brigada de siempre.
- Con `origen == "sistema"` y sin match de evaluación, la identidad puede venir de `profesional` (`inspector_fuente = "api"`): la cédula de la API es una clave única por persona (a diferencia del código de brigada reutilizable), así que el join contra el roster por cédula no tiene el riesgo de mala atribución que sí tiene el join por código. Ese join normaliza la cédula a solo dígitos en ambos lados (`cedula_key`) — "1.234.567" y "1234567" son la misma clave — pero el valor de `identificacion` guardado sigue siendo el de la API, verbatim.

### D2. Forma normalizada única para Evaluaciones

El backend devuelve para ambas fuentes la MISMA forma que hoy produce `list_evaluaciones`, más cinco campos:

```
fuente: "atencionsismo" | "firestore"
origen: "sistema" | "firebase" | ""
color_etiqueta: "Habitable" | "Acceso restringido" | "No habitable" | "Sin clasificación"
inspector_fuente: "evaluacion" | "api" | "roster" | ""
fase: 1 | 2 | null
```

Así `evaluaciones.js` cambia lo mínimo: `faseDe` aprende "sin dato", el modal muestra `Origen` y `Fuente`, el banner de degradación generaliza su texto.

### D3. Selector de fuente en Stickers, Atención Sismo por defecto

Un control segmentado "Fuente" con dos valores: `Atención Sismo` (default) y `Formulario`. Costo mínimo porque ambas fuentes comparten forma. Es la válvula de seguridad mientras el cruce por código no esté verificado en producción.

### D4. Ruta backend nueva, cache con la misma semántica de degradación

`GET /stickers-atencionsismo` (Railway, roles admin y viewer). Reutiliza `EvaluacionesCache` parametrizado con su propio Blob de último-bueno y su propia redacción. Semántica:

- TTL 5 minutos; al fallar la API sirve el último payload en memoria.
- Arranque en frío sin payload: restaura desde Blob y marca `degraded: true`.
- Si Firestore falla (roster o evaluaciones), el fetch falla completo y aplica la cadena anterior. El match de evaluación en Firestore sigue siendo la fuente de identidad autoritativa (contrato v3): un caché de evaluaciones degradado sigue haciendo fallar el fetch aunque el roster esté disponible, porque sin ese match no hay identidad verificada, y el NP del roster (por código de brigada) es ahora solo el FALLBACK de Fase e identidad cuando no hay match — sin Firestore no hay ninguna de las dos cosas, y una Fase o identidad silenciosamente vacía (o, peor, mal atribuida) es peor que servir un dato viejo.
- La copia en Blob redacta `descripcion.nombre` (persona afectada) y `inspector.np`.
- `fetch_stickers` con 0 filas lanza `ApiEmptyResultError`: nunca se cachea un universo vacío.

### D5. Reportes ciudadanos: snapshot estático proyectado, sin PII

El job `dashboard_refresh.fetch_reportes` ya baja `informe/json` completo y publica `reportes.json` (19,8 MB, sin PII ni fotos). Se agrega una proyección liviana `reportes_ciudadanos.json` con solo lo que la pestaña necesita, y `creado` en ISO. Se escribe DESPUÉS de `reportes.json`/`reportes_meta.json`/`reportes_agg.json` y de forma fail-soft (`try/except` + `logging.exception`, igual que `_write_contactos`): un bug en la proyección nunca deja inconsistente el trío principal, que ya es la fuente de verdad del resto del refresh. Se publica al Blob junto con el resto y se lee con `fetchData`, igual que el Panel. Sin ruta nueva ni autenticación.

Campos: `id, direccion, barrio, comuna, estado, afectacion, tipo_inmueble, nombre_edificio, lat, lng, creado, creado_texto, habitabilidad, visitado, pudo_evaluar, alcance, descripcion (máx. 240 caracteres), sticker{numero, color, etiqueta, origen, clasificacion}`. `creado_texto` solo lleva contenido cuando `creado` no pudo parsearse (fallback de despliegue); si la fecha sí parseó queda en `""`, para no duplicar el texto. `lat`/`lng` se descartan (quedan en `null`) cuando no son finitos (`NaN`/`Infinity`) o cuando resuelven a `(0, 0)` — "isla nula", no una coordenada real de Cali; esto aplica tanto al valor primario como al fallback string `latitud`/`longitud`.

**Enmascarado (no es anonimización completa — ver residual abajo).** `descripcion`, `direccion` y `nombre_edificio` ya son públicos en `reportes.json`, pero la tarea 0 encontró teléfonos y cédulas sueltas en cerca del 2 % de los textos, y una revisión posterior encontró lo mismo en `direccion`/`nombre_edificio`, además de correos y teléfonos separados por espacios o guiones ("301 226 3431", "311-764-8858") que el patrón original (solo dígitos contiguos) no capturaba. En el snapshot se enmascaran con `***`, antes de truncar `descripcion`:

- Secuencias de 7 o más dígitos, con o sin un separador simple (espacio, punto o guion) entre cada par de dígitos — cubre tanto corridas contiguas como números separados. Grupos cortos (números de casa, "# 3-45") quedan intactos.
- Correos electrónicos.

**Residual conocido:** nombres propios escritos en el texto libre NO se enmascaran — mismo residuo que ya existe hoy en `reportes.json`. El texto sigue siendo útil para leer el daño reportado y deja de transportar teléfonos, cédulas y correos.

**Tamaño medido:** sobre el `reportes.json` real (14 804 filas, 18,9 MB de entrada), la proyección con el enmascarado ampliado y `descripcion` a 240 caracteres produce **9,17 MB** (antes de estos ajustes: 10,7 MB con el patrón de solo-dígitos y `descripcion` a 500 caracteres) — sigue por encima del estimado original de 4 a 5 MB, pero es el tamaño real verificado, no una proyección.

### D6. Pestaña Reportes ciudadanos: mismo patrón que Evaluaciones

Módulo `web/js/reportes-ciudadanos.js` con mapa Leaflet propio (`preferCanvas: true` por los 14 800 puntos), KPIs por estado, distribución por afectación, filtros (estado, afectación, tipo de inmueble, comuna, barrio, color de sticker, texto), "Colorear por" (estado, afectación, sticker), lista paginada de a 100, modal de detalle y export xlsx. Abierta a cualquier rol autenticado, igual que Stickers.

## 4. Fuera de alcance

- Fila propia de `informe/stickers` en la pestaña Analista.
- Consumir `visitados-criticos` desde estas pestañas.
- Migrar `fetch_reportes` de ventanas por fecha a `offset`/`nextOffset`.
- Retirar la fuente Firestore de Stickers.

## 5. Riesgos

| Riesgo | Mitigación |
|---|---|
| `numero` de origen `firebase` no coincide con `codigo_edificacion`. | Verificado el 2026-09-08: coincide en el 97,7 %. El resto cae a "sin dato". |
| `informe/json` no devuelve `sticker`. | Verificado: lo devuelve. La proyección conserva defaults vacíos por robustez. |
| 20 000 marcadores en Leaflet. | Renderer canvas y lista paginada. |
| Texto de `descripcion` con datos personales. | Verificado que ocurre; se enmascaran secuencias de 7 o más dígitos. |
