# API Informe JSON

Exportación en JSON de reportes con datos de visita (evaluación v1) y enlaces a fotografías, pensada para integraciones externas.

Equivalente funcional al Excel de `GET /api/informe/export`, con campos adicionales (`id`, fotos de evaluación y fotos del chat).

## Endpoint

```
GET /api/informe/json
```

| Propiedad | Valor |
|-----------|--------|
| Método | `GET` |
| Content-Type respuesta | `application/json` |
| Autenticación | HTTP Basic Auth |
| Tiempo máximo | 60 s |

## Autenticación

Credenciales de un usuario **operario** o **viewer** (solo lectura) registrado en el sistema, con contraseña ya configurada en su portal correspondiente.

```
Authorization: Basic base64(correo:contraseña)
```

En solicitudes no autenticadas la API responde `401` con:

```
WWW-Authenticate: Basic realm="informe-json"
```

### Roles permitidos

| Rol | Portal de contraseña |
|-----|----------------------|
| Operario | Portal de operarios (`/`) |
| Viewer | Portal de administración (acceso de solo lectura) |

### Errores de autenticación

| HTTP | Situación |
|------|-----------|
| `401` | Sin header `Authorization`, credenciales incorrectas o correo no autorizado |
| `403` | Usuario válido pero aún no ha creado su contraseña en el portal |

## Parámetros de consulta

### Filtros (mismos que `/api/informe`)

Todos son opcionales. Sin filtros se devuelven **todos** los reportes que cumplan el rango de fechas (si se indica).

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `comuna` | CSV de enteros | Ej.: `comuna=14,15` |
| `barrio` | CSV de strings | Ej.: `barrio=San Antonio,Granada` |
| `verif` | CSV | Estados de verificación: `pending`, `assigned`, `visited`, `critical`, `unavailable`, `ede` |
| `afectacion` | CSV | Tipos de colapso: `A`, `B`, `C`, `D`, `E`, `F`, `unset` |
| `inmueble` | CSV | Tipos de inmueble según el catálogo de la app |
| `q` | string | Búsqueda libre (dirección, contacto, barrio, etc.) |

### Rango de fechas (opcional)

Filtra por `reports.createdAt` en milisegundos Unix UTC.

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `desde_utc` | entero (ms) | Inclusive. Ej.: `1724025600000` |
| `hasta_utc` | entero (ms) | Inclusive. Ej.: `1724111999999` |

Reglas:

- Ambos son opcionales; puede usarse solo uno.
- `desde_utc` no puede ser mayor que `hasta_utc`.
- Deben ser enteros en milisegundos Unix UTC.

> **Nota:** El parámetro `page` se parsea por compatibilidad con `/api/informe`, pero esta API **no pagina**: devuelve el conjunto completo de filas que coinciden con los filtros.

## Ejemplos de solicitud

### cURL — todos los reportes

```bash
curl -u "operario@ejemplo.com:mi-contraseña" \
  "https://atencionsismo.cali.gov.co/api/informe/json"
```

### cURL — rango de fechas + filtro de verificación

```bash
curl -u "viewer@ejemplo.com:mi-contraseña" \
  "https://atencionsismo.cali.gov.co/api/informe/json?desde_utc=1724025600000&hasta_utc=1724111999999&verif=visited,critical"
```

### JavaScript (fetch)

```javascript
const credentials = btoa("operario@ejemplo.com:mi-contraseña");

const response = await fetch(
  "https://atencionsismo.cali.gov.co/api/informe/json?desde_utc=1724025600000",
  {
    headers: {
      Authorization: `Basic ${credentials}`,
    },
  },
);

const data = await response.json();
```

## Respuesta exitosa (`200`)

```json
{
  "ok": true,
  "cantidad": 2,
  "generado_utc": 1724112000123,
  "desde_utc": 1724025600000,
  "hasta_utc": null,
  "reportes": [
    {
      "id": "uuid-del-reporte",
      "nombre": "Juan Pérez",
      "cedula": "1234567890",
      "telefono": "300 123 4567",
      "direccion": "Calle 1 # 2-3",
      "barrio": "San Antonio",
      "comuna": "14",
      "estadoVerificacion": "Visitado crítico",
      "afectacion": "A — Colapso total",
      "tipoInmueble": "Apartamento",
      "nombreEdificio": "Torre Central",
      "apartamento": "501",
      "casa": "",
      "predioCompleto": "No",
      "descripcion": "Grieta en muro principal",
      "latitud": "3.4512",
      "longitud": "-76.5321",
      "fechaCreacion": "19/08/2026, 10:30 a. m.",
      "fechaEnvio": "19/08/2026, 10:35 a. m.",
      "fechaVencimiento": "",
      "completado": "Sí",
      "pudoEvaluar": "Sí",
      "motivoNoEvaluacion": "",
      "otroMotivoNoEvaluacion": "",
      "pisos": "5",
      "sotanos": "1",
      "anoConstruccion": "1980–1989",
      "alcanceInspeccion": "Exterior e interior",
      "danosMurosFachadas": "Moderado",
      "danosParticiones": "Leve",
      "danosCieloRaso": "Sin daño",
      "danosCubierta": "Leve",
      "danosEscaleras": "Sin daño",
      "danosServiciosPublicos": "Sin daño",
      "fallecidos": "0",
      "atrapados": "0",
      "rescatados": "0",
      "necesitaEvacuacion": "No",
      "evacuados": "0",
      "porEvacuar": "0",
      "habitabilidad": "No habitable",
      "conceptoTecnico": "Daño estructural moderado…",
      "aspectosVisitaEspecializada": "",
      "visitado": "Sí",
      "fechaEvaluacion": "19/08/2026, 2:00 p. m.",
      "fotografiasEvaluacion": [
        {
          "id": "file-id-1",
          "url": "https://storage.instantdb.com/..."
        }
      ],
      "mensajes": [
        {
          "id": "msg-id-1",
          "texto": "Foto de la grieta en la fachada",
          "creado_utc": 1724073600000,
          "fotografias": [
            {
              "id": "file-id-2",
              "url": "https://storage.instantdb.com/..."
            }
          ]
        }
      ]
    }
  ]
}
```

### Campos de cada reporte

Los campos de datos del informe (`nombre`, `cedula`, `direccion`, etc.) coinciden con las columnas del Excel de `/api/informe/export`. Los valores de texto están **formateados para lectura humana** (etiquetas en español, fechas localizadas `es-CO`, teléfonos formateados).

| Campo extra | Descripción |
|-------------|-------------|
| `id` | Identificador único del reporte en InstantDB |
| `fotografiasEvaluacion` | Fotos adjuntas a la **evaluación v1 más reciente** del reporte |
| `mensajes` | Mensajes del chat del reporte, ordenados por fecha ascendente |

Cada foto incluye:

| Campo | Descripción |
|-------|-------------|
| `id` | ID del archivo en `$files` |
| `url` | URL pública/de acceso directo al archivo en Instant Storage |

### Fotografías

- **Evaluación:** se toma la evaluación v1 con `createdAt` más reciente (misma lógica que el Excel).
- **Chat:** todos los mensajes del reporte; solo se incluyen fotos con `id` y `url` válidos.
- Las URLs provienen de Instant Storage y pueden usarse directamente en `<img src="...">` o descargas HTTP.

## Errores

| HTTP | Cuerpo | Causa |
|------|--------|-------|
| `400` | `{ "error": "..." }` | Parámetro `desde_utc` / `hasta_utc` inválido o rango inconsistente |
| `401` | `{ "error": "..." }` | Autenticación fallida |
| `403` | `{ "error": "..." }` | Contraseña no configurada en el portal |
| `500` | `{ "error": "No pudimos cargar el informe. Intente de nuevo." }` | Error interno |

## Alcance y limitaciones

### Incluido

- Datos del reporte (ingreso / ciudadano)
- Datos de la evaluación de verificación **v1** (visita técnica)
- Fotos de la evaluación v1
- Mensajes del chat del reporte con sus fotos
- Estado de verificación (incluye detección de EDE v2 para la etiqueta, sin exportar el detalle EDE)

### No incluido

- Detalle completo de **evaluaciones EDE v2** (`evaluationsV2`)
- Datos **RUFE**
- `accessSecret` del reporte (capability de autogestión ciudadana)
- Paginación (usar filtros y rango de fechas para acotar resultados)

## Implementación en el repositorio

| Archivo | Responsabilidad |
|---------|-----------------|
| [app/api/informe/json/route.ts](../app/api/informe/json/route.ts) | Route handler |
| [lib/informe/query.ts](../lib/informe/query.ts) | `queryInformeJsonRows` |
| [lib/informe/json-map.ts](../lib/informe/json-map.ts) | Mapeo reporte → JSON |
| [lib/informe/export-map.ts](../lib/informe/export-map.ts) | Campos compartidos con Excel |
| [lib/operator-basic-auth.ts](../lib/operator-basic-auth.ts) | `requireOperatorOrViewerBasicAuth` |

## APIs relacionadas

| Endpoint | Formato | Auth | Uso |
|----------|---------|------|-----|
| `GET /api/informe` | JSON resumido | Sesión Instant (Bearer) | Tabla UI `/informe` |
| `GET /api/informe/export` | Excel | Sesión Instant (Bearer) | Descarga desde el portal |
| `GET /api/informe/json` | JSON completo + fotos | Basic Auth | Integraciones externas |
| `GET /api/operario/reports/visitados-criticos` | JSON críticos A/B | Basic Auth (solo operario) | Casos críticos visitados |
