# API Visitados Críticos

Documentación para consultar los casos **Visitado Crítico** (evaluación técnica paso 1 con colapso A o B) en un rango de fechas.

Cada elemento de la lista es una **edificación** (`subcluster`), no un reporte de ingreso individual. Si esa edificación tiene varias evaluaciones A/B en el rango, se entrega solo la **más reciente**.

---

## Qué entrega

Lista de evaluaciones críticas completadas entre dos instantes UTC, con:

- Ubicación (dirección, coordenadas, comuna, barrio)
- Datos de ingreso del reporte más antiguo vinculado a esa edificación
- Contacto de la persona afectada e inmueble
- Personal de ingreso y técnico de verificación — ingeniero/arquitecto que verificó y evaluó (si aplica)
- Evaluación técnica completa (daños, víctimas, habitabilidad, concepto, etc.)
- Mensajes: la descripción del ingreso, como un único mensaje (no hay hilo de chat)

Devuelve **todos** los casos críticos del rango solicitado. El acceso requiere autenticación (ver abajo).

Esta API **no** incluye fotos.

---

## Endpoint

```
GET https://atencionsismo.cali.gov.co/api/operario/reports/visitados-criticos
```

Host fijo de producción: **`atencionsismo.cali.gov.co`**

Ejemplo con rango:

```
https://atencionsismo.cali.gov.co/api/operario/reports/visitados-criticos?desde_utc=1722470400000&hasta_utc=1725148799999
```

---

## Autenticación

HTTP **Basic Auth** con el correo y la contraseña de una cuenta de personal con columna **API = Lectura** en Autorización.

### Cómo obtener acceso

1. Escriba por WhatsApp a **+1 617 599 6919** (Martin) el correo electrónico que desea usar para autenticarse.
2. Cuando le confirmen que el correo quedó habilitado con acceso a la API, entre a  
   **https://atencionsismo.cali.gov.co/ingresar**
3. Cree su contraseña en esa pantalla (o inicie sesión si ya la tenía).
4. En **Sus Accesos** verá la fila **API**. No hay escritorio para este permiso: solo sirve para confirmar que la contraseña quedó lista.
5. A partir de ahí, use ese **correo y contraseña** en cada llamada a la API (Basic Auth).

| Campo Basic Auth | Valor |
| --- | --- |
| Usuario | El correo que indicó por WhatsApp |
| Contraseña | La que creó en el paso 3 |

Un permiso de Operarios / Administración / Onboarding **no** abre esta API. Hace falta la columna **API**.

### Ejemplo con curl

```bash
curl -u 'su-correo@ejemplo.com:su-contraseña' \
  'https://atencionsismo.cali.gov.co/api/operario/reports/visitados-criticos?desde_utc=1722470400000&hasta_utc=1725148799999'
```

---

## Parámetros de consulta

| Parámetro | Tipo | Obligatorio | Descripción |
| --- | --- | --- | --- |
| `desde_utc` | entero | sí | Inicio del rango (Unix **milisegundos** UTC, inclusive) |
| `hasta_utc` | entero | sí | Fin del rango (Unix **milisegundos** UTC, inclusive) |

Reglas:

- Deben ser enteros (solo dígitos; sin decimales ni letras).
- `desde_utc` no puede ser mayor que `hasta_utc`.
- El filtro usa la fecha de **creación de la evaluación** (`evaluacion.creado_utc`), no la del reporte de ingreso.

---

## Respuesta exitosa (`200`)

```json
{
  "ok": true,
  "cantidad": 2,
  "generado_utc": 1725148800123,
  "desde_utc": 1722470400000,
  "hasta_utc": 1725148799999,
  "casos": [ ]
}
```

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `ok` | boolean | Siempre `true` si la petición fue válida |
| `cantidad` | number | Número de elementos en `casos` |
| `generado_utc` | number | Momento en que se generó la respuesta (ms UTC) |
| `desde_utc` | number | Eco del parámetro de entrada |
| `hasta_utc` | number | Eco del parámetro de entrada |
| `casos` | array | Lista de casos críticos |

Todos los campos `*_utc` son **números enteros** en milisegundos desde el epoch Unix (UTC).

---

## Esquema de cada caso (`casos[]`)

### Identificación y ubicación

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `id` | string | ID de la **edificación** (subcluster), no del reporte de ingreso |
| `estado` | `"critico"` | Código fijo |
| `estadoEtiqueta` | `"Visitado Crítico"` | Etiqueta en español |
| `direccion` | string | Dirección / rótulo de la edificación |
| `lat` / `lng` | number | Coordenadas |
| `placeId` | string | `place_id` de Google del punto (cluster). Si no hay, el del ingreso |
| `comuna` | number \| null | Código de comuna |
| `comunaEtiqueta` | string \| null | Ej. `"Comuna 05"` |
| `barrio` | string \| null | Nombre del barrio; en zona rural, la vereda si no hay barrio |
| `resumenUnidad` | string \| null | Resumen de unidad / edificio |

### `ingreso` — como se reportó al inicio

Datos del **reporte más antiguo** vinculado a la edificación.

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `descripcion` | string \| null | Descripción del caso |
| `tipoColapso` | string \| null | Código A–F del ingreso |
| `tipoColapsoEtiqueta` | string \| null | Etiqueta en español |
| `tipoInmueble` | string \| null | Código de tipo de inmueble |
| `tipoInmuebleEtiqueta` | string \| null | Etiqueta en español |
| `nombreEdificio` | string \| null | |
| `numeroApartamento` | string \| null | |
| `numeroCasa` | string \| null | |
| `edificioCompleto` | boolean \| null | |
| `contacto` | objeto | `{ nombre, telefono, cedula }` |
| `creado_utc` | number | Creación del reporte |
| `enviado_utc` | number \| null | Igual a `creado_utc` (ya no hay borrador) |
| `estado` | string \| null | `"submitted"` cuando hay ingreso |
| `estadoEtiqueta` | string \| null | `"Enviado"` |
| `completado` | boolean \| null | `true` cuando hay ingreso |

### `contacto` — persona afectada

Datos de contacto de la **persona afectada** (creador del ingreso, o primer ciudadano vinculado): `{ nombre, telefono, cedula }`.

### `inmueble` — inmueble efectivo

Valores verificados en la evaluación cuando existen; si no, los del ingreso.

| Campo | Tipo |
| --- | --- |
| `tipoInmueble` | string |
| `tipoInmuebleEtiqueta` | string |
| `nombreEdificio` | string \| null |
| `numeroApartamento` | string \| null |
| `numeroCasa` | string \| null |
| `edificioCompleto` | boolean \| null |

### Personal

**`operarioIngreso`** (o `null`) — quién registró el ingreso:

```json
{ "id": "...", "nombre": "...", "correo": "..." }
```

**`tecnicoVerificacion`** (o `null`) — ingeniero o arquitecto que fue a verificar y evaluar el inmueble:

| Campo | Tipo |
| --- | --- |
| `id` | string |
| `nombre` | string \| null |
| `correo` | string \| null |
| `profesion` | string \| null |
| `cedula` | string \| null |
| `telefono` | string \| null |
| `matriculaProfesional` | string \| null |
| `enfasis` | string \| null |
| `anosExperiencia` | number \| null |

**`verificacion_asignada_utc`**: number \| null — cuándo se asignó ese técnico al paso 1.

### `evaluacion` — evaluación técnica (la del rango)

Solo entran evaluaciones de **paso 1** con visita realizada y tipo de colapso **A** o **B**.

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `id` | string | ID de la evaluación |
| `creado_utc` | number | Momento en que se guardó la evaluación |
| `visitado` | `true` | Siempre true en esta API |
| `puedeEvaluar` | `true` | Siempre true (visitas fallidas no entran) |
| `tipoColapso` | `"A"` \| `"B"` | Código verificado |
| `tipoColapsoEtiqueta` | string | Etiqueta completa en español |
| `tipoInmueble` / `tipoInmuebleEtiqueta` | string \| null | |
| `nombreEdificio`, `numeroApartamento`, `numeroCasa` | string \| null | |
| `edificioCompleto` | boolean \| null | |
| `pisosSobreNivel` | number \| null | |
| `sotanos` | number \| null | |
| `anioConstruccion` / `anioConstruccionEtiqueta` | string \| null | No se captura en esta versión; siempre `null` |
| `alcanceInspeccion` / `alcanceInspeccionEtiqueta` | string \| null | Interior / exterior |
| `danos` | array | Ver abajo |
| `victimas` | objeto | Ver abajo |
| `habitabilidad` / `habitabilidadEtiqueta` | string \| null | |
| `conceptoTecnico` | string \| null | |
| `aspectosVisitaEspecializada` | string \| null | Etiquetas unidas por coma |
| `contacto` | objeto | Persona afectada: `{ nombre, telefono, cedula }` |

**`danos[]`:**

```json
{
  "clave": "damageStairs",
  "campoEtiqueta": "Daño en escaleras",
  "valor": "severo",
  "valorEtiqueta": "Severo"
}
```

Las claves siguen siendo las de la API anterior (`damageWallsFacades`, `damagePartitions`, `damageCeilings`, `damageRoof`, `damageStairs`, `damagePublicServices`).

**`victimas`:**

| Campo | Tipo | Notas |
| --- | --- | --- |
| `fallecidos` | number \| null | No se captura; siempre `null` |
| `atrapados` | number \| null | No se captura; siempre `null` |
| `rescatados` | number \| null | No se captura; siempre `null` |
| `necesitaEvacuacion` | boolean \| null | `true` si el estado de evacuación es «necesita evacuación» |
| `evacuados` | number \| null | |
| `porEvacuar` | number \| null | |

### `mensajes[]` — relato del ingreso

No hay conversación. Si el ingreso tiene descripción, llega **un** mensaje con ese texto:

```json
{
  "id": "...",
  "texto": "...",
  "creado_utc": 1725148800000,
  "autor": {
    "rol": "operario",
    "rolEtiqueta": "Operario",
    "id": "...",
    "nombre": "...",
    "correo": "..."
  }
}
```

- `id` y `creado_utc` son los del reporte de ingreso.
- `autor.rol`: `operario` si un operario registró el ingreso; si no, `ciudadano` (correo `null`). También puede ser `desconocido`.
- Valores posibles de `autor.rol` (compatibilidad): `ciudadano` | `admin` | `profesional` | `rufe` | `operario` | `desconocido`.
- Sin descripción: `mensajes` es `[]`.

---

## Códigos frecuentes (valor → etiqueta)

### Tipo de colapso

| Código | Etiqueta |
| --- | --- |
| `A` | COLAPSO TOTAL |
| `B` | RIESGO COLAPSO |
| `C` | COLAPSO PARCIAL |
| `D` | DAÑO ESTRUCTURAL |
| `E` | DAÑO MAMPOSTERÍA |
| `F` | NO SE EVIDENCIA NINGÚN DAÑO |

En esta API, `evaluacion.tipoColapso` solo puede ser **A** o **B**.

### Severidad de daño (`danos[].valor`)

| Código | Etiqueta |
| --- | --- |
| `none` | Ninguno |
| `leve` | Leve |
| `moderado` | Moderado |
| `severo` | Severo |

### Habitabilidad

| Código | Etiqueta |
| --- | --- |
| `habitable` | Habitable |
| `partial` | Parcial |
| `not_habitable` | No habitable |

### Alcance de inspección

| Código | Etiqueta |
| --- | --- |
| `interior` | En el interior |
| `exterior` | En el exterior (no se permite el ingreso) |

---

## Criterio de “Visitado Crítico”

Un caso entra en la respuesta si su evaluación cumple:

1. Es de **paso 1** (verificación)
2. `puedeEvaluar === true` (no es visita fallida)
3. Tipo de colapso verificado **A** o **B**
4. No está anulada (`invalida` distinto de `true`)
5. `creado_utc` de la evaluación está entre `desde_utc` y `hasta_utc` (inclusive)

El tipo de colapso del **ingreso** del reporte no define el filtro; solo el de la evaluación.

Una edificación que después pasó a evaluación especializada (EDE) **sí entra** si la visita crítica del paso 1 cayó en el rango.

---

## Errores

Respuestas en JSON: `{ "error": "mensaje en español" }`.

| HTTP | Cuándo |
| --- | --- |
| `400` | Falta `desde_utc` / `hasta_utc`, no son enteros, o `desde_utc > hasta_utc` |
| `401` | Sin Basic Auth, credenciales inválidas o contraseña incorrecta |
| `403` | Correo habilitado para la API pero aún sin contraseña (créela en https://atencionsismo.cali.gov.co/ingresar) |
| `500` | Error interno |

Ejemplos de mensaje:

- `"Falta el parámetro desde_utc (Unix ms UTC)."`
- `"desde_utc no puede ser mayor que hasta_utc."`
- `"Correo o contraseña incorrectos."`
- `"Debe crear su contraseña en la página de ingreso de personal antes de usar esta API."`

---

## Cambios respecto a la versión anterior

| Cambio | Detalle |
| --- | --- |
| Un caso = una edificación | `id` es el subcluster, no el reporte. `cantidad` es menor si un edificio tenía muchos ingresos. Varias visitas A/B en el rango → una fila (la más reciente) |
| Auth | Columna **API** en Autorización (lectura). Un operario sin esa columna no entra |
| Contraseña | https://atencionsismo.cali.gov.co/ingresar (antes `/operario`) |
| `mensajes` | Un mensaje sintético con la descripción del ingreso; no hay chat |
| Siempre `null` | `anioConstruccion`, `fallecidos`, `atrapados`, `rescatados` |
| `evaluacion.contacto` | Del ingreso (creador / ciudadanos), no de un formulario aparte en la visita |
| Sin fotos | Igual que antes: esta API no entrega imágenes |

Siguen iguales: URL, `desde_utc` / `hasta_utc`, sobre de respuesta, nombres de campos, claves de `danos[]`, Basic Auth, filtro A/B por fecha de la evaluación.

---

## Notas prácticas

1. Use siempre **HTTPS**.
2. No comparta su correo/contraseña de la API en repositorios ni tickets públicos.
3. Para ventanas diarias en hora de Colombia (UTC−5), convierta el inicio/fin del día local a milisegundos UTC antes de llamar la API.
4. La lista puede ser grande en rangos amplios; filtre por días o semanas según necesidad.
5. Campos opcionales pueden venir como `null` cuando no hay dato registrado.
