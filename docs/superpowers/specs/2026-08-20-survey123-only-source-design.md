# Diseño: fuente única Survey123 (sin Google Sheets)

Fecha: 2026-08-20 · Estado: aprobado (enfoque A, publicar 708)

## Objetivo
El pipeline del dashboard deja de leer Google Sheets (`tabla_normalizada`). Los
datos provienen solo de dos fuentes independientes:
- **Inspecciones EDAN** ← layer público Survey123 (feature layer, epoch ms).
- **Reportes de riesgo** ← API atencionsismo `informe/json` (`fetch_reportes_api.py`,
  ya independiente del Sheet). Sin cambios.

Se abandona la curación manual que vivía en `tabla_normalizada` (confirmado): el
formulario Survey123 es la verdad única.

## Enfoque (A): consolidar la fuente Survey123 en `refresh_data.py`
`normalize_sync.normalize(fetch_survey_raw())` ya produce el df con la forma exacta
del dashboard directo del layer (validado: 708 filas, 114 columnas base; las 11
restantes las agregan los pasos existentes de `run_once`). Se mueve esa capacidad a
`refresh_data.py` y se retira `normalize_sync.py`.

### Cambios
1. `refresh_data.py`:
   - Mover `LAYER_TO_RAW`, `SURVEY_DATE_FIELDS`, `fetch_survey_raw()`, `normalize()`
     desde `normalize_sync.py` (evita el import circular actual).
   - `run_once()` nuevo flujo:
     `df = normalize(fetch_survey_raw())` → `apply_photo_coords` →
     `validate_photo_coords` → `add_suspension_servicios` → `write_outputs`.
   - Eliminar la ruta Google Sheets: `acquire_xlsx`, `load_normalized_table`, el flag
     `--source`, y el uso de `GOOGLE_APPLICATION_CREDENTIALS` / constantes del Sheet
     (`DRIVE_FILE_ID`, `SA_SCOPES`, `NORMALIZED_TAB`) que queden sin uso.
   - Quitar `backfill_missing_dates` (innecesaria: el epoch ms trae fecha para todos;
     además esto elimina de raíz el swap día/mes y el bucket "Sin fecha", que venían
     de la ruta string del Sheet).
2. Borrar `scripts/normalize_sync.py`.
3. `deploy/entrypoint.sh`: quitar el paso `python normalize_sync.py`, el requisito del
   secreto `GOOGLE_SERVICE_ACCOUNT_JSON`, y correr `refresh_data.py` sin `--source drive`.

### Alternativa descartada (B)
Mantener `normalize_sync.py` como librería e importarlo desde `refresh_data`: genera
import circular y deja código muerto de escritura al Sheet.

## Validación
Correr el pipeline nuevo localmente (layer público, sin credenciales) y diffear el
`inspections.json` resultante contra el actual: debe coincidir en esquema y contenido
salvo el registro extra (708 vs 707; se publica el layer tal cual, es la verdad única).

## Riesgos
- El layer trae 708 vs 707: aceptado, se publican los 708.
- `apply_photo_coords`/`validate_photo_coords` ya consultan el layer/geocode (sin
  cambios); `fetch_survey_raw` falla-cerrado si el layer no responde (no publica parcial).
