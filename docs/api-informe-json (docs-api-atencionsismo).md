# APIs de integración — Atención Sismo Cali

**Host de producción:** `https://atencionsismo.cali.gov.co`

Las APIs de exportación JSON usan el **mismo usuario y contraseña** (HTTP Basic Auth).

| Endpoint | Uso | Fechas |
|----------|-----|--------|
| `GET /api/informe/json` | Reportes paginados (visita paso 1 + fotos + sticker del inmueble); KPIs del dashboard **opt-in** (`kpis=1`) | `desde_utc` / `hasta_utc` **opcionales** |
| `GET /api/informe/stickers` | Solo evaluaciones con sticker (dirección, coords, código, persona afectada, origen, color) | `desde_utc` / `hasta_utc` **opcionales** |
| `GET /api/operario/reports/visitados-criticos` | Solo casos visitados críticos A/B | `desde_utc` / `hasta_utc` **obligatorios** |

| Propiedad | Valor |
|-----------|--------|
| Método | `GET` |
| Content-Type respuesta | `application/json` |
| Autenticación | HTTP Basic Auth (`personal.api === "read"`) |
| Tiempo máximo | 60 s |

---

## Autenticación (común)

Credenciales deben ser solicitadas a Mateo Echeverry (mateo.echeverry@cali.gov.co - Cel: 3226274835) indicar que es para uso de la api

posterior a la respuesta de acceso debe ingresar a la pagina `https://atencionsismo.cali.gov.co/ingresar` y crear su contraseña.

No es necesario volver a acceder a la pagina para nada mas


```
Authorization: Basic base64(correo:contraseña)
```

En Postman: pestaña **Authorization** → tipo **Basic Auth** → Username = correo, Password = contraseña. No use Bearer.

En solicitudes no autenticadas la API responde `401` con:

```
WWW-Authenticate: Basic realm="informe-json"
```

(Visitados críticos usa `realm="visitados-criticos"`. El `realm` no cambia el usuario ni la contraseña.)

### Errores de autenticación

| HTTP | Situación |
|------|-----------|
| `401` | Sin header `Authorization`, credenciales incorrectas o correo sin permiso `api: read` |
| `403` | Usuario válido pero aún no ha creado su contraseña en `/ingresar` |

> Los operarios/viewers del modelo v1 ya no aplican. Use una cuenta `personal` con columna `api` en `"read"`.

---

## Informe JSON

```
GET https://atencionsismo.cali.gov.co/api/informe/json
```

Una fila en `reportes[]` = un **reporte**. La respuesta es **paginada** (lotes de hasta 200 filas) para evitar tiempos de espera largos (`504`). Una sola llamada **no** devuelve todo el universo.

Opcionalmente, con `kpis=1`, incluye `kpis`: los mismos totales del dashboard (chips + flujo operativo), calculados sobre **inmuebles agrupados**, no sobre filas de reporte.

### Consumo recomendado (apps externas)

1. **Primer lote con KPIs:** `GET .../api/informe/json?kpis=1&offset=0&limit=200`
2. **Siguientes lotes sin KPIs:** repetir con `offset=<nextOffset>` hasta `done: true`
3. Acotar con `desde_utc` / `hasta_utc` cuando el caso de uso lo permita

```bash
# Lote 1 (KPIs + reportes)
curl -u "integracion@ejemplo.com:su-contraseña" \
  "https://atencionsismo.cali.gov.co/api/informe/json?kpis=1&offset=0&limit=200"

# Lote 2 (solo reportes)
curl -u "integracion@ejemplo.com:su-contraseña" \
  "https://atencionsismo.cali.gov.co/api/informe/json?offset=200&limit=200"
```

En JavaScript:

```js
async function fetchAllReportes(authHeader, baseUrl) {
  const all = [];
  let offset = 0;
  let kpis = null;

  for (;;) {
    const params = new URLSearchParams({ offset: String(offset), limit: "200" });
    if (offset === 0) params.set("kpis", "1");

    const res = await fetch(`${baseUrl}/api/informe/json?${params}`, {
      headers: { Authorization: authHeader },
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error);

    if (body.kpis) kpis = body.kpis;
    all.push(...body.reportes);
    if (body.done) return { reportes: all, kpis };
    offset = body.nextOffset;
  }
}
```

### Parámetros (todos opcionales)

| Parámetro | Tipo | Default | Descripción |
|-----------|------|---------|-------------|
| `offset` | entero ≥ 0 | `0` | Cursor de paginación (filas Instant ya leídas) |
| `limit` | entero 1–200 | `200` | Tamaño del lote |
| `kpis` | `1` | ausente | Solo con `kpis=1` se calcula e incluye el objeto `kpis` |
| `desde_utc` | entero (ms Unix UTC) | — | Inclusive. Filtra `reporte.creadoEn` |
| `hasta_utc` | entero (ms Unix UTC) | — | Inclusive |
| `comuna` | CSV de enteros | — | Ej.: `comuna=14,15` (filtra `subcluster.comuna`) |
| `barrio` | CSV de strings | — | Ej.: `barrio=San Antonio,Granada` |
| `verif` | CSV | — | `pending`, `assigned`, `visited`, `critical`, `unavailable`, `ede` |
| `afectacion` | CSV | — | Grado de **ingreso**: `A`, `B`, `C`, `D`, `E`, `F`, `unset` |
| `inmueble` | CSV | — | `casa`, `condominio`, `escuela`, `edificio`, `hospital`, `local_comercial`, `otro` |
| `q` | string | — | Búsqueda libre (dirección, contacto, barrio, comuna, etc.) |

**Qué filtra cada parámetro**

| Parámetro | `reportes[]` | `kpis` |
|-----------|--------------|--------|
| `desde_utc`, `hasta_utc` | Sí | No |
| `verif` | Sí | No |
| `comuna`, `barrio`, `afectacion`, `inmueble`, `q` | Sí | Sí |

Los KPIs reflejan el universo operativo actual (como abrir el dashboard en vista **Todos**), recortado solo por comuna, barrio, afectación de ingreso, tipo de inmueble y búsqueda. Así `inmueblesVerificados` no cambia si el consumidor pide, por ejemplo, `verif=visited,critical,ede` para exportar solo visitados.

Reglas de fechas:

- Ambos son opcionales; puede usarse solo uno.
- `desde_utc` no puede ser mayor que `hasta_utc`.
- Deben ser enteros en milisegundos Unix UTC.

Para armar los milisegundos (consola del navegador):

```js
Date.parse("2026-08-22T00:00:00.000Z")
Date.parse("2026-08-22T23:59:59.999Z")
```

### Probar con Postman

1. **Method:** `GET`
2. **URL:** `https://atencionsismo.cali.gov.co/api/informe/json`
3. **Authorization:** tipo **Basic Auth** (mismas credenciales que visitados críticos)
4. **Params** (opcionales), por ejemplo:
   - `kpis` = `1`
   - `offset` = `0`
   - `limit` = `200`
   - `desde_utc` = `1724025600000`
   - `hasta_utc` = `1724111999999`
   - `verif` = `visited,critical`

### Ejemplo cURL

```bash
curl -u "integracion@ejemplo.com:su-contraseña" \
  "https://atencionsismo.cali.gov.co/api/informe/json?kpis=1&offset=0&limit=200&desde_utc=1724025600000&hasta_utc=1724111999999&verif=visited,critical"
```

### Respuesta exitosa (`200`)

```json
{
  "ok": true,
  "cantidad": 180,
  "generado_utc": 1724112000123,
  "desde_utc": 1724025600000,
  "hasta_utc": 1724111999999,
  "offset": 0,
  "limit": 200,
  "nextOffset": 200,
  "done": false,
  "kpis": {
    "inmueblesVerificados": 100,
    "cuadrillasEnCampo": 5,
    "asignaciones": 20,
    "cuadrillasEspecializadas": 2,
    "asignacionesEspecializadas": 8,
    "reportes": 1500,
    "inmueblesReportados": 400,
    "pendientes": 280,
    "inmueblesVisitadosNoCriticos": 90,
    "avancePct": 25,
    "zonaRural": 12,
    "fueraDeCali": 3
  },
  "reportes": [
    {
      "id": "uuid-del-reporte",
      "nombre": "Juan Pérez",
      "cedula": "1234567890",
      "telefono": "300 123 4567",
      "direccion": "Calle 1 # 2-3",
      "barrio": "San Antonio",
      "comuna": "Comuna 14",
      "estadoVerificacion": "Visitado crítico",
      "afectacion": "COLAPSO TOTAL",
      "tipoInmueble": "Edificio",
      "fotografiasEvaluacion": [
        { "id": "file-id-1", "url": "https://..." }
      ],
      "mensajes": [
        {
          "id": "uuid-ingreso",
          "texto": "Grieta en muro principal",
          "creado_utc": 1724073600000,
          "fotografias": [
            { "id": "file-id-2", "url": "https://..." }
          ]
        }
      ],
      "sticker": {
        "numero": "76001001-123-0001",
        "color": "rojo",
        "colorEtiqueta": "No habitable",
        "origen": "sistema",
        "clasificacion": "peligro_colapso"
      }
    }
  ]
}
```

### Paginación

| Campo | Descripción |
|-------|-------------|
| `offset` | Cursor enviado en la solicitud |
| `limit` | Tamaño del lote solicitado (máx. 200) |
| `nextOffset` | Valor de `offset` para el siguiente lote |
| `done` | `true` cuando no hay más filas Instant por leer |
| `cantidad` | Filas en `reportes[]` **de este lote** (tras filtros), no el total del universo |

Sin `kpis=1`, el objeto `kpis` **no** aparece en la respuesta.

### Objeto `kpis` (solo con `kpis=1`)

Totales idénticos a los chips y KPIs de flujo del dashboard interno. Se calculan sobre edificaciones nombradas (`subcluster` con `esCatchAll: false`), excluyendo visitas fallidas — **no** se infieren contando `reportes[]`.

| Campo | Descripción |
|-------|-------------|
| `inmueblesVerificados` | Inmuebles con visita de paso 1 hecha (verificados, priorización pendiente y EDE) |
| `cuadrillasEnCampo` | Profesionales con inmuebles asignados sin visitar (paso 1) |
| `asignaciones` | Inmuebles asignados a profesionales (paso 1) |
| `cuadrillasEspecializadas` | Profesionales con visitas especializadas asignadas sin EDE |
| `asignacionesEspecializadas` | Inmuebles asignados a visita especializada (paso 2) |
| `reportes` | Reportes individuales ya agrupados en inmuebles (suma de `reporteCount`) |
| `inmueblesReportados` | Inmuebles agrupados por ubicación exacta |
| `pendientes` | Inmuebles pendientes + asignados (paso 1 sin visita) |
| `inmueblesVisitadosNoCriticos` | Inmuebles visitados con afectación distinta de A/B |
| `avancePct` | Porcentaje de inmuebles visitados o críticos sobre inmuebles filtrados |
| `zonaRural` | Inmuebles en corregimientos o veredas de Cali |
| `fueraDeCali` | Inmuebles con coordenadas fuera de comunas, barrios y corregimientos |

**`cantidad` vs `kpis.reportes`:** `cantidad` es la longitud de `reportes[]` en **este lote** (tras filtros de la solicitud, incluye reportes en cubo General y visitas fallidas si aplican). `kpis.reportes` es la suma de reportes en inmuebles agrupados del universo operativo, con los filtros que aplican a KPIs (ver tabla arriba).

Los demás campos coinciden con las columnas del Excel (`nombre`, `cedula`, `direccion`, daños, habitabilidad, etc.). Valores formateados en español (`es-CO`), con weekday y mes completos.

Campos de cada reporte:

`id`, `nombre`, `cedula`, `telefono`, `direccion`, `barrio`, `comuna`, `estadoVerificacion`, `afectacion`, `tipoInmueble`, `nombreEdificio`, `apartamento`, `casa`, `predioCompleto`, `descripcion`, `latitud`, `longitud`, `fechaCreacion`, `fechaEnvio`, `fechaVencimiento`, `completado`, `pudoEvaluar`, `motivoNoEvaluacion`, `otroMotivoNoEvaluacion`, `pisos`, `sotanos`, `anoConstruccion`, `alcanceInspeccion`, `danosMurosFachadas`, `danosParticiones`, `danosCieloRaso`, `danosCubierta`, `danosEscaleras`, `danosServiciosPublicos`, `fallecidos`, `atrapados`, `rescatados`, `necesitaEvacuacion`, `evacuados`, `porEvacuar`, `habitabilidad`, `conceptoTecnico`, `aspectosVisitaEspecializada`, `visitado`, `fechaEvaluacion`, `fotografiasEvaluacion`, `mensajes`, `sticker`.

### Objeto `sticker` por reporte

Siempre presente en cada fila de `reportes[]`. Resume la evaluación con sticker más reciente del `subcluster` del reporte (InstantDB: `evaluacion.tieneSticker === true`).

| Campo | Descripción | Sin sticker |
|-------|-------------|-------------|
| `numero` | Código de evaluación (`codigoEvaluacion`) | `""` |
| `color` | Cartel derivado: `verde`, `amarillo`, `rojo` | `""` |
| `colorEtiqueta` | Etiqueta legible: Habitable, Acceso restringido, No habitable | `"Sin clasificación"` |
| `origen` | `sistema` (generado en la app) o `firebase` (importado con backfill) | `""` |
| `clasificacion` | Código ATC-20 (`inspeccionado`, `uso_restringido`, `peligro_colapso`) | `""` |

El color se calcula con la misma lógica del dashboard: ATC-20, luego EDE, luego habitabilidad paso 1. El origen `firebase` corresponde a `gpsOrigen === "firebase-sticker"` en InstantDB.

### Campos extra por reporte

| Campo | Descripción |
|-------|-------------|
| `id` | Identificador del `reporte` en InstantDB |
| `fotografiasEvaluacion` | Fotos de la evaluación paso 1 más reciente (no invalidada) |
| `mensajes` | Mensaje sintético de ingreso (`descripcion` + fotos del reporte); no hay chat multi-mensaje en v2 |

`estadoVerificacion` usa estas etiquetas: Reportado, Asignado, Visitado, Visitado crítico, Evaluación especializada, Visita fallida.

### Errores

| HTTP | Cuerpo | Causa |
|------|--------|-------|
| `400` | `{ "error": "..." }` | Parámetro `desde_utc` / `hasta_utc` / `offset` / `limit` inválido o rango inconsistente |
| `401` | `{ "error": "..." }` | Autenticación fallida |
| `403` | `{ "error": "..." }` | Contraseña no configurada en `/ingresar` |
| `500` | `{ "error": "No pudimos cargar el informe. Intente de nuevo." }` | Error interno |
| `504` | (sin cuerpo JSON) | Tiempo de espera agotado — use paginación (`offset`/`limit`), acote fechas y pida `kpis=1` solo en el primer lote |

### Alcance y limitaciones (v2)

**Incluido**

- Datos del `reporte` (ingreso ciudadano/operario)
- Contacto desde `ciudadano` (`creador` + `ciudadanos`)
- Evaluación paso 1 más reciente del `subcluster` del reporte
- Sticker del inmueble (`sticker`, evaluación con `tieneSticker` más reciente)
- Fotos de evaluación y fotos de ingreso del reporte
- Estado de verificación derivado de `paso1Estado`, `paso1TipoAfectacion`, `paso2Estado`
- KPIs del dashboard (`kpis`, solo con `kpis=1`) con la misma lógica que el tablero interno
- Paginación por lotes para consumo externo sin agotar el tiempo de espera

**No incluido / campos vacíos**

| Campo JSON | Motivo |
|------------|--------|
| `fechaVencimiento`, `completado` | No existen en `reporte` v2 |
| `anoConstruccion` | No existe en `evaluacion` v2 |
| `fallecidos`, `atrapados`, `rescatados` | No existen en `evaluacion` v2 |
| Detalle EDE (`ede` JSON) | Solo se refleja el estado `ede` en `estadoVerificacion` |
| RUFE | Fuera de este contrato |
| Chat multi-autor | `mensajes` es sintético (un ítem por ingreso) |

`visitado` es `"Sí"` si hay evaluación paso 1 válida.

---

## Stickers JSON

```
GET https://atencionsismo.cali.gov.co/api/informe/stickers
```

Lista **solo** evaluaciones con sticker en InstantDB (`evaluacion.tieneSticker === true`). Una fila en `stickers[]` = una evaluación con sticker (no un reporte de ingreso). Mismas credenciales y paginación que el informe JSON.

### Consumo recomendado

1. `GET .../api/informe/stickers?offset=0&limit=200`
2. Repetir con `offset=<nextOffset>` hasta `done: true`
3. Opcional: acotar con `desde_utc` / `hasta_utc` sobre `evaluacion.creadoEn`

```bash
curl -u "integracion@ejemplo.com:su-contraseña" \
  "https://atencionsismo.cali.gov.co/api/informe/stickers?offset=0&limit=200"
```

### Parámetros (todos opcionales)

| Parámetro | Tipo | Default | Descripción |
|-----------|------|---------|-------------|
| `offset` | entero ≥ 0 | `0` | Cursor de paginación |
| `limit` | entero 1–200 | `200` | Tamaño del lote |
| `desde_utc` | entero (ms Unix UTC) | — | Inclusive. Filtra `evaluacion.creadoEn` |
| `hasta_utc` | entero (ms Unix UTC) | — | Inclusive |

Reglas de fechas: iguales al informe JSON (`desde_utc` ≤ `hasta_utc`, enteros ms UTC).

### Respuesta exitosa (`200`)

```json
{
  "ok": true,
  "cantidad": 180,
  "generado_utc": 1724112000123,
  "desde_utc": null,
  "hasta_utc": null,
  "offset": 0,
  "limit": 200,
  "nextOffset": 200,
  "done": false,
  "stickers": [
    {
      "id": "uuid-evaluacion",
      "direccion": "Calle 1 # 2-3",
      "latitud": "3.4516",
      "longitud": "-76.5320",
      "numero": "76001001-123-0001",
      "personaAfectada": "Juan Pérez",
      "origen": "sistema",
      "color": "verde",
      "colorEtiqueta": "Habitable",
      "fase": 1,
      "profesional": { "cedula": "123", "nombre": "Ana Gómez", "rango": "P2" },
      "fotografias": [{ "id": "img-1", "url": "https://atencionsismo.cali.gov.co/media/img-1.jpg" }]
    }
  ]
}
```

### Campos de cada sticker

| Campo | Fuente InstantDB | Si falta |
|-------|------------------|----------|
| `id` | `evaluacion.id` | (se omite la fila) |
| `direccion` | `evaluacion.direccion` → label del subcluster → dirección del primer reporte | `"Sin dirección"` |
| `latitud` / `longitud` | coords de la evaluación → coords del subcluster | `""` |
| `numero` | `codigoEvaluacion` | `"Sin código"` |
| `personaAfectada` | nombre del creador o ciudadano del subcluster | `"Sin identificar"` |
| `origen` | `gpsOrigen === "firebase-sticker"` → `"firebase"`, resto → `"sistema"` | `"sistema"` |
| `color` | derivado (ATC-20 / EDE / habitabilidad) | `""` |
| `colorEtiqueta` | Habitable, Acceso restringido, No habitable | `"Sin clasificación"` |
| `fase` | `1` si el número viene de `codigoEvaluacion`, `2` si viene de `codigoEvaluacionEsp` | `null` |
| `profesional` | `evaluacion.tecnico` (`cedula`, `nombre`, `addlInfo.rango`) | `{ "cedula": "", "nombre": "", "rango": "" }` |
| `fotografias` | `evaluacion.imagenes` | `[]` |

Evaluaciones invalidadas (`invalida === true`) no aparecen en `stickers[]`.

#### Nota de integración (2026-09-08)

El equipo desarrollador de la API de atencionsismo confirmó (2026-09-08) que `fase` es su propia variable de Fase de negocio — `1` = Fase I, `2` = Fase II — según el proceso propio de atencionsismo (paso 1 vs paso 2 / evaluación especializada). El dashboard consumidor (pestaña Stickers) usa `fase` directamente como su Fase I/II, cayendo a `inspector.np` (roster/Firestore) solo cuando `fase` no llega en `1` ni `2`.

Nota factual, verificada contra datos en vivo: para toda fila con `origen: "firebase"` (importada desde NUESTRO Firebase), `fase` llega en `2` de forma sistemática — incluidas filas cuyo inspector real (por Firestore) es P1/P2. La pestaña Evaluaciones del mismo dashboard (fuente Firestore) sigue clasificando esas mismas evaluaciones por la categoría NP del inspector, sin cambios — por lo tanto ambas pestañas pueden mostrar una Fase distinta para el mismo registro, por diseño: son dos señales de Fase independientes.

### Errores

| HTTP | Cuerpo | Causa |
|------|--------|-------|
| `400` | `{ "error": "..." }` | Parámetro de fecha, `offset` o `limit` inválido |
| `401` | `{ "error": "..." }` | Autenticación fallida |
| `403` | `{ "error": "..." }` | Contraseña no configurada en `/ingresar` |
| `500` | `{ "error": "No pudimos cargar los stickers. Intente de nuevo." }` | Error interno |

---

## Visitados críticos A/B

```
GET https://atencionsismo.cali.gov.co/api/operario/reports/visitados-criticos
```

**Mismas credenciales** que el informe JSON. Aquí `desde_utc` y `hasta_utc` **sí son obligatorios**. Filtra evaluaciones paso 1 visitable (`puedeEvaluar: true`) con afectación A o B.

### Ejemplo cURL

```bash
curl -u "integracion@ejemplo.com:su-contraseña" \
  "https://atencionsismo.cali.gov.co/api/operario/reports/visitados-criticos?desde_utc=1724025600000&hasta_utc=1724111999999"
```

Respuesta: `{ ok, cantidad, generado_utc, desde_utc, hasta_utc, casos[] }`. Cada caso es una edificación (`subcluster`) con la evaluación más reciente en el rango.

Si faltan fechas: `400` (`Falta el parámetro desde_utc…`). Eso no es un fallo de usuario.

---

## Checklist de verificación

1. Sin auth → `401`
2. Correo sin permiso `api` → `401`
3. Credenciales válidas + `offset=0&limit=200` → `200`, `reportes[]`, `nextOffset`, `done`
4. Misma URL con `kpis=1` → `200` e incluye `kpis`
5. Las mismas credenciales en visitados críticos **con** fechas → `200` y `casos`
6. `desde_utc` mayor que `hasta_utc` → `400`
7. Credenciales válidas en `/api/informe/stickers?offset=0&limit=200` → `200`, `stickers[]`, `nextOffset`, `done`
8. Un reporte con sticker en `/api/informe/json` incluye objeto `sticker` no vacío en `origen`/`numero` cuando aplica

---

## Implementación

| Archivo | Responsabilidad |
|---------|-----------------|
| [`app/api/informe/json/route.ts`](../app/api/informe/json/route.ts) | Informe JSON paginado + KPIs opt-in |
| [`app/api/informe/stickers/route.ts`](../app/api/informe/stickers/route.ts) | Stickers JSON paginado |
| [`lib/informe/json-query.ts`](../lib/informe/json-query.ts) | Lotes JSON, filtros y KPIs |
| [`lib/informe/sticker-query.ts`](../lib/informe/sticker-query.ts) | Lotes de stickers desde InstantDB |
| [`lib/informe/sticker-map.ts`](../lib/informe/sticker-map.ts) | Mapeo evaluación → sticker JSON |
| [`lib/dashboard/metrics.ts`](../lib/dashboard/metrics.ts) | Cálculo compartido de KPIs (`dashboardChipTotals`) |
| [`lib/dashboard/query.ts`](../lib/dashboard/query.ts) | Snapshot paginado de inmuebles para KPIs |
| [`lib/informe/json-map.ts`](../lib/informe/json-map.ts) | Mapeo `reporte` → JSON |
| [`app/api/operario/reports/visitados-criticos/route.ts`](../app/api/operario/reports/visitados-criticos/route.ts) | Visitados críticos |
| [`lib/personal/api-basic-auth.ts`](../lib/personal/api-basic-auth.ts) | Basic Auth `personal.api` (compartido) |
