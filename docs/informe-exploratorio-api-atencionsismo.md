# Informe exploratorio — API Atención Sismo Cali (v2)

Fecha: 2026-09-08
Fuente de la especificación: `docs/api-informe-json (docs-api-atencionsismo).md`
Host: `https://atencionsismo.cali.gov.co`

## 1. Estado de los endpoints (verificado hoy)

Sondeo sin credenciales el 2026-09-08. Los tres endpoints responden `401` con `WWW-Authenticate: Basic`, es decir, están desplegados y protegidos. En agosto `informe/json` devolvía `404`; ya no.

| Endpoint | Estado | Realm | Fechas |
|---|---|---|---|
| `GET /api/informe/json` | Vivo (401) | `informe-json` | opcionales |
| `GET /api/informe/stickers` | Vivo (401) | `informe-json` | opcionales |
| `GET /api/operario/reports/visitados-criticos` | Vivo (401) | `visitados-criticos` | obligatorias |

Autenticación común: HTTP Basic (`correo:contraseña`), cuenta `personal` con `api = "read"`. Errores: `401` sin permiso, `403` sin contraseña creada en `/ingresar`, `400` parámetros inválidos, `504` timeout (usar paginación).

No hay credenciales en esta máquina ni en el repo (`VISITADOS_API_USER` / `VISITADOS_API_PASS` vacías). Los conteos de la sección 3 vienen del cache local generado por `scripts/fetch_reportes_api.py`.

## 2. Relación de endpoints y variables

### 2.1 `GET /api/informe/json`

Una fila = un reporte de ingreso. Paginado en lotes de hasta 200.

**Parámetros (todos opcionales)**

| Parámetro | Tipo | Aplica a `reportes[]` | Aplica a `kpis` |
|---|---|---|---|
| `offset` | entero ≥ 0 (default 0) | sí | — |
| `limit` | entero 1–200 (default 200) | sí | — |
| `kpis` | `1` | — | activa el objeto |
| `desde_utc`, `hasta_utc` | ms Unix UTC, inclusivos | sí | no |
| `verif` | CSV: pending, assigned, visited, critical, unavailable, ede | sí | no |
| `comuna` | CSV enteros | sí | sí |
| `barrio` | CSV strings | sí | sí |
| `afectacion` | CSV: A, B, C, D, E, F, unset | sí | sí |
| `inmueble` | CSV: casa, condominio, escuela, edificio, hospital, local_comercial, otro | sí | sí |
| `q` | texto libre | sí | sí |

**Envoltura de respuesta** (10 campos): `ok`, `cantidad`, `generado_utc`, `desde_utc`, `hasta_utc`, `offset`, `limit`, `nextOffset`, `done`, `kpis` (solo con `kpis=1`), `reportes[]`.

`cantidad` es el tamaño del lote actual, no el total. El total solo se conoce iterando hasta `done: true`.

**Objeto `kpis`** (12 campos, calculado sobre inmuebles agrupados, no sobre filas):
`inmueblesVerificados`, `cuadrillasEnCampo`, `asignaciones`, `cuadrillasEspecializadas`, `asignacionesEspecializadas`, `reportes`, `inmueblesReportados`, `pendientes`, `inmueblesVisitadosNoCriticos`, `avancePct`, `zonaRural`, `fueraDeCali`.

**Campos de cada reporte** (49 campos), agrupados:

| Grupo | Campos |
|---|---|
| Identidad y contacto (PII) | `id`, `nombre`, `cedula`, `telefono` |
| Ubicación | `direccion`, `barrio`, `comuna`, `latitud`, `longitud`, `nombreEdificio`, `apartamento`, `casa`, `predioCompleto` |
| Clasificación | `estadoVerificacion`, `afectacion`, `tipoInmueble` |
| Ingreso | `descripcion`, `fechaCreacion`, `fechaEnvio`, `fechaVencimiento`*, `completado`* |
| Evaluación paso 1 | `visitado`, `fechaEvaluacion`, `pudoEvaluar`, `motivoNoEvaluacion`, `otroMotivoNoEvaluacion`, `pisos`, `sotanos`, `anoConstruccion`*, `alcanceInspeccion` |
| Daños | `danosMurosFachadas`, `danosParticiones`, `danosCieloRaso`, `danosCubierta`, `danosEscaleras`, `danosServiciosPublicos` |
| Personas | `fallecidos`*, `atrapados`*, `rescatados`*, `necesitaEvacuacion`, `evacuados`, `porEvacuar` |
| Concepto | `habitabilidad`, `conceptoTecnico`, `aspectosVisitaEspecializada` |
| Anidados | `fotografiasEvaluacion[]` (`id`, `url`), `mensajes[]` (`id`, `texto`, `creado_utc`, `fotografias[]`), `sticker{}` |

\* Siempre vacíos en v2 (no existen en el modelo).

**Sub-objeto `sticker`** (5 campos, siempre presente): `numero`, `color` (verde/amarillo/rojo), `colorEtiqueta` (Habitable / Acceso restringido / No habitable / Sin clasificación), `origen` (sistema / firebase), `clasificacion` (ATC-20: inspeccionado / uso_restringido / peligro_colapso).

Valores de `estadoVerificacion`: Reportado, Asignado, Visitado, Visitado crítico, Evaluación especializada, Visita fallida.

### 2.2 `GET /api/informe/stickers`

Una fila = una evaluación con sticker (`tieneSticker === true`, no invalidada). No es un reporte.

**Parámetros**: `offset`, `limit`, `desde_utc`, `hasta_utc` (filtran `evaluacion.creadoEn`). Sin filtros por comuna, barrio o texto.

**Envoltura**: misma que informe/json, sin `kpis`, con `stickers[]`.

**Campos por sticker** (9):

| Campo | Fuente | Valor si falta |
|---|---|---|
| `id` | `evaluacion.id` | se omite la fila |
| `direccion` | evaluación → subcluster → primer reporte | `"Sin dirección"` |
| `latitud`, `longitud` | evaluación → subcluster | `""` |
| `numero` | `codigoEvaluacion` | `"Sin código"` |
| `personaAfectada` | creador o ciudadano del subcluster | `"Sin identificar"` |
| `origen` | `gpsOrigen` | `"sistema"` |
| `color` | derivado ATC-20 / EDE / habitabilidad | `""` |
| `colorEtiqueta` | derivado | `"Sin clasificación"` |

### 2.3 `GET /api/operario/reports/visitados-criticos`

Una fila = una edificación (`subcluster`) con evaluación paso 1 visitable y afectación A o B en el rango.

**Parámetros**: `desde_utc`, `hasta_utc` (obligatorios, `400` si faltan). Sin paginación documentada.

**Envoltura**: `ok`, `cantidad`, `generado_utc`, `desde_utc`, `hasta_utc`, `casos[]`.

**Campos por caso**: la spec NO los documenta. Es la única laguna seria del documento. El backend del proyecto solo persiste `id`, `estado`, `direccion`, `comuna`, `barrio`, `lat`, `lng`.

## 3. Número de datos (cache local)

### 3.1 informe/json → `web/data/reportes.json`

Descarga del 2026-08-25, ventana 2026-08-01 a 2026-08-26, PII y listas pesadas eliminadas por el script.

| Métrica | Valor |
|---|---|
| Reportes | 14 804 |
| IDs únicos | 14 804 (sin duplicados) |
| Con coordenadas | 14 804 (100 %) |
| Direcciones distintas | 11 534 |
| Comunas distintas | 38 |
| Con evaluación paso 1 (`visitado`) | 2 740 (18,5 %) |

Distribución por `estadoVerificacion`:

| Estado | Reportes | % |
|---|---|---|
| Reportado | 8 384 | 56,6 |
| Asignado | 2 938 | 19,8 |
| Evaluación especializada | 1 408 | 9,5 |
| Visitado | 1 360 | 9,2 |
| Visita fallida | 481 | 3,2 |
| Visitado crítico | 233 | 1,6 |

Distribución por `afectacion` (grado de ingreso, autodeclarado):

| Afectación | Reportes |
|---|---|
| Daño estructural | 5 785 |
| Daño mampostería | 3 568 |
| Colapso parcial | 2 810 |
| Riesgo colapso | 1 744 |
| No se evidencia ningún daño | 686 |
| Colapso total | 211 |

Distribución por `tipoInmueble`: Casa 6 198, Edificio 5 591, Condominio / Parcelación 1 186, Otro 1 171, Local comercial 496, Escuela 109, Hospital 53.

Top comunas: 19 (3 749), 2 (2 068), 17 (1 349), 18 (1 190), 3 (968).

`habitabilidad` (solo evaluados): No habitable 1 037, Habitable 557, Parcial 369, vacío 12 841.

### 3.2 visitados-criticos → `web/data/criticos_api.json`

Descarga del 2026-09-02.

| Métrica | Valor |
|---|---|
| Casos críticos | 510 |
| Comunas distintas | 13 |
| Comuna 19 | 307 (60 %) |
| Comuna 18 | 64 |
| Comuna 5 | 48 |

### 3.3 informe/stickers

Sin cache local. Nunca se ha consumido en este proyecto.

## 4. Completitud de campos (informe/json, 14 804 filas)

| Campo | Vacío |
|---|---|
| `id`, `direccion`, `latitud`, `longitud`, `fechaCreacion`, `fechaEnvio`, `estadoVerificacion`, `afectacion`, `tipoInmueble` | 0 % |
| `comuna` | 0,1 % |
| `barrio` | 3,8 % |
| `descripcion` | 15,1 % |
| `nombreEdificio` | 43 % |
| `apartamento` / `predioCompleto` / `casa` | 78 / 84 / 90 % |
| Campos de evaluación paso 1 (`visitado`, `pudoEvaluar`, `fechaEvaluacion`) | 81,5 % |
| Campos de daños (`danos*`) | 87–90 % |
| `habitabilidad`, `conceptoTecnico` | 86,7 % |
| `fechaVencimiento`, `completado`, `anoConstruccion`, `fallecidos`, `atrapados`, `rescatados` | 100 % (no existen en v2) |

El 81,5 % de vacío en evaluación es esperado: solo 2 740 reportes tienen visita. Dentro de los visitados, `pudoEvaluar = No` en 766 casos (28 %).

## 5. Hallazgos y riesgos

1. **Sin credenciales v2 en el entorno local.** El cache de reportes tiene dos semanas. Los KPIs en vivo del dashboard dependen de que `VISITADOS_API_USER` / `VISITADOS_API_PASS` estén cargadas en Vercel y Railway con una cuenta `personal` habilitada.
2. **`sticker` no está en el cache.** La descarga del 25 de agosto es anterior a ese campo, o el script no lo conserva. Hay que re-descargar y verificar que llegue el objeto. Hoy la clasificación Fase I/II del dashboard de Stickers no puede alimentarse desde este cache.
3. **`informe/stickers` nunca se consumió.** Es la fuente directa para el módulo de Stickers y evita inferir el sticker desde reportes.
4. **Fechas formateadas en español** (`"martes, 18 de agosto de 2026, 06:33 p. m."`). No hay timestamp numérico por reporte. Cualquier análisis temporal exige parsear ese string con locale `es-CO`.
5. **`visitados-criticos` sin campos documentados.** Pedir al proveedor el esquema de `casos[]` o capturar una respuesta real y documentarla.
6. **`cantidad` no es total.** El script actual (`fetch_reportes_api.py`) pagina por ventanas de fecha con halving, no por `offset`/`nextOffset`. Funciona, pero la spec v2 recomienda `offset` y `kpis=1` solo en el primer lote.
7. **`afectacion` es autodeclarada al ingreso**, no el resultado técnico. Para severidad real usar `habitabilidad` o `sticker.color`.
8. **Concentración geográfica.** Comuna 19 acumula el 25 % de reportes y el 60 % de críticos.

## 6. Próximos pasos sugeridos

- Obtener credenciales v2 y cargarlas en `.env` local, Vercel y Railway.
- Re-descargar `informe/json` con `kpis=1` en el primer lote y confirmar `sticker` en cada fila.
- Consumir `informe/stickers` por primera vez y guardar `web/data/stickers_api.json`.
- Capturar un caso real de `visitados-criticos` y documentar sus campos.
