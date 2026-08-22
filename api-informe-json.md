# APIs de integración — Atención Sismo Cali

**Host de producción:** `https://atencionsismo.cali.gov.co`

Las dos APIs de exportación JSON usan el **mismo usuario y contraseña** (HTTP Basic Auth).

| Endpoint | Uso | Fechas |
|----------|-----|--------|
| `GET /api/informe/json` | Informe completo de reportes (visita paso 1 + fotos) | `desde_utc` / `hasta_utc` **opcionales** |
| `GET /api/operario/reports/visitados-criticos` | Solo casos visitados críticos A/B | `desde_utc` / `hasta_utc` **obligatorios** |

| Propiedad | Valor |
|-----------|--------|
| Método | `GET` |
| Content-Type respuesta | `application/json` |
| Autenticación | HTTP Basic Auth (`personal.api === "read"`) |
| Tiempo máximo | 60 s |

---

## Autenticación (común)

Credenciales de una fila en **`personal`** con permiso **`api: "read"`** y contraseña ya configurada en:

`https://atencionsismo.cali.gov.co/ingresar`

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

Una fila = un **reporte**. No pagina: sin filtros puede devolver el conjunto completo. Conviene acotar con fechas.

### Parámetros (todos opcionales)

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `desde_utc` | entero (ms Unix UTC) | Inclusive. Filtra `reporte.creadoEn` |
| `hasta_utc` | entero (ms Unix UTC) | Inclusive |
| `comuna` | CSV de enteros | Ej.: `comuna=14,15` (filtra `subcluster.comuna`) |
| `barrio` | CSV de strings | Ej.: `barrio=San Antonio,Granada` |
| `verif` | CSV | `pending`, `assigned`, `visited`, `critical`, `unavailable`, `ede` |
| `afectacion` | CSV | Grado de **ingreso**: `A`, `B`, `C`, `D`, `E`, `F`, `unset` |
| `inmueble` | CSV | `casa`, `condominio`, `escuela`, `edificio`, `hospital`, `local_comercial`, `otro` |
| `q` | string | Búsqueda libre (dirección, contacto, barrio, comuna, etc.) |

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
   - `desde_utc` = `1724025600000`
   - `hasta_utc` = `1724111999999`
   - `verif` = `visited,critical`

### Ejemplo cURL

```bash
curl -u "integracion@ejemplo.com:su-contraseña" \
  "https://atencionsismo.cali.gov.co/api/informe/json?desde_utc=1724025600000&hasta_utc=1724111999999&verif=visited,critical"
```

### Respuesta exitosa (`200`)

```json
{
  "ok": true,
  "cantidad": 2,
  "generado_utc": 1724112000123,
  "desde_utc": 1724025600000,
  "hasta_utc": 1724111999999,
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
      ]
    }
  ]
}
```

Los demás campos coinciden con las columnas del Excel (`nombre`, `cedula`, `direccion`, daños, habitabilidad, etc.). Valores formateados en español (`es-CO`), con weekday y mes completos.

Campos de cada reporte:

`id`, `nombre`, `cedula`, `telefono`, `direccion`, `barrio`, `comuna`, `estadoVerificacion`, `afectacion`, `tipoInmueble`, `nombreEdificio`, `apartamento`, `casa`, `predioCompleto`, `descripcion`, `latitud`, `longitud`, `fechaCreacion`, `fechaEnvio`, `fechaVencimiento`, `completado`, `pudoEvaluar`, `motivoNoEvaluacion`, `otroMotivoNoEvaluacion`, `pisos`, `sotanos`, `anoConstruccion`, `alcanceInspeccion`, `danosMurosFachadas`, `danosParticiones`, `danosCieloRaso`, `danosCubierta`, `danosEscaleras`, `danosServiciosPublicos`, `fallecidos`, `atrapados`, `rescatados`, `necesitaEvacuacion`, `evacuados`, `porEvacuar`, `habitabilidad`, `conceptoTecnico`, `aspectosVisitaEspecializada`, `visitado`, `fechaEvaluacion`, `fotografiasEvaluacion`, `mensajes`.

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
| `400` | `{ "error": "..." }` | Parámetro `desde_utc` / `hasta_utc` inválido o rango inconsistente |
| `401` | `{ "error": "..." }` | Autenticación fallida |
| `403` | `{ "error": "..." }` | Contraseña no configurada en `/ingresar` |
| `500` | `{ "error": "No pudimos cargar el informe. Intente de nuevo." }` | Error interno |

### Alcance y limitaciones (v2)

**Incluido**

- Datos del `reporte` (ingreso ciudadano/operario)
- Contacto desde `ciudadano` (`creador` + `ciudadanos`)
- Evaluación paso 1 más reciente del `subcluster` del reporte
- Fotos de evaluación y fotos de ingreso del reporte
- Estado de verificación derivado de `paso1Estado`, `paso1TipoAfectacion`, `paso2Estado`

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
3. Credenciales válidas + rango corto en informe → `200` y `reportes[].id`
4. Las mismas credenciales en visitados críticos **con** fechas → `200` y `casos`
5. `desde_utc` mayor que `hasta_utc` → `400`

---

## Implementación

| Archivo | Responsabilidad |
|---------|-----------------|
| [`app/api/informe/json/route.ts`](../app/api/informe/json/route.ts) | Informe JSON |
| [`lib/informe/json-query.ts`](../lib/informe/json-query.ts) | Query v2 + filtros |
| [`lib/informe/json-map.ts`](../lib/informe/json-map.ts) | Mapeo `reporte` → JSON |
| [`app/api/operario/reports/visitados-criticos/route.ts`](../app/api/operario/reports/visitados-criticos/route.ts) | Visitados críticos |
| [`lib/personal/api-basic-auth.ts`](../lib/personal/api-basic-auth.ts) | Basic Auth `personal.api` (compartido) |
