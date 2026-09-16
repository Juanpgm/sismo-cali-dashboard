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
