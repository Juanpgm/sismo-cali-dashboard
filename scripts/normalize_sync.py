"""Sync live Survey123 inspections into tabla_normalizada (upsert by GlobalID).

The dashboard's source of truth is the curated `tabla_normalizada` tab, which
`refresh_data.py` only reads (never rewrites). This script keeps that tab in
sync with the live Survey123 form responses, fetched straight from the public
ArcGIS feature layer (default) or from the legacy `raw_data` tab (--source
sheet). Either way the source is reshaped into the exact raw_data column
contract (the original Survey123 xlsx export headers), so everything downstream
is IDENTICAL:

  * NEW inspection (GlobalID not in tabla_normalizada) -> normalized + APPENDED.
  * EDITED inspection (Survey123 edits bump the record's ObjectID) -> the
    existing row is UPDATED IN PLACE with the newest version.
  * UNCHANGED inspection (no newer ObjectID) -> left untouched, so manual
    curation on those rows is preserved.

"Newest" is the row with the highest ObjectID for a GlobalID (ObjectID grows
with every submission/edit). id_edan is derived from GlobalID, so an edited row
keeps its id_edan; only its other fields change.

Flow:  form -> Survey123 feature layer -> [this script: fetch + upsert]
       -> tabla_normalizada -> refresh_data.py (read-only) -> dashboard JSON.

Idempotent: re-running when nothing changed writes nothing. Run before
`refresh_data.py` in the same cron/trigger.

    python scripts/normalize_sync.py                 # apply the upsert (live layer)
    python scripts/normalize_sync.py --dry-run       # report new/edited; write nothing
    python scripts/normalize_sync.py --source sheet  # legacy: read the raw_data tab

Requires GOOGLE_APPLICATION_CREDENTIALS (service account, EDITOR on the Sheet).

Tradeoff (chosen): for a row edited in the form, the form wins — any manual edit
made to that same row in tabla_normalizada is overwritten by the newest form
version. Rows the form did not re-touch keep their curation.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os

import pandas as pd
import requests

from refresh_data import (
    DRIVE_FILE_ID,
    NORMALIZED_TAB,
    SA_SCOPES,
    COLS_A_ELIMINAR,
    RENAME_MAP,
    acquire_xlsx,
    add_address_norm,
    add_date_fields,
    add_id_edan,
    coerce_numeric,
    drop_pii,
    normalize_municipio,
    spatial_join,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("normalize_sync")

RAW_TAB = "raw_data"

# Public Survey123 feature layer holding the live form responses (no token).
SURVEY_LAYER_URL = (
    "https://services6.arcgis.com/EF6OTqvE0RxR2jwj/arcgis/rest/services/"
    "service_d108cb3c79e242eabe99b458798936d1/FeatureServer/0"
)

# Layer date fields arrive as Unix epoch milliseconds; the raw contract has
# naive datetimes (what pd.read_excel produced from the xlsx export).
SURVEY_DATE_FIELDS = ["fecha_inspeccion", "CreationDate", "EditDate"]

# Layer field name -> raw_data column label (the Survey123 xlsx export headers,
# including pandas' ".N" suffixes for the duplicated "¿Cuál?" columns). Both
# sides are in survey order; every one of the layer's 113 fields has a raw
# counterpart (verified field-by-field against the original export, which also
# confirmed the export stores domain CODES — 'r1', 'publica', … — not labels,
# so layer values pass through untranslated). Geometry supplies x/y.
LAYER_TO_RAW = {
    "objectid": "ObjectID",
    "globalid": "GlobalID",
    "fecha_inspeccion": "Fecha de Inspección:",
    "hora_inspeccion": "Hora:",
    "nombre_evaluador": "Nombre Evaluador:",
    "id_grupo": "ID Grupo:",
    "id_grupo_otro": "Especifique la entidad:",
    "tipo_evento": "Tipo de evento:",
    "nombre_edif": "Nombre de la edificación:",
    "municipio": "Municipio:",
    "barrio": "Barrio/vereda:",
    "direccion": "Dirección:",
    "codigo_predial": "2.2 Código predial / catastral (si se tiene):",
    "propiedad": "Tipo de propiedad:",
    "contacto_nombre": "Nombre de contacto:",
    "contacto_rol": "Relación con la edificación:",
    "contacto_rol_otro": "Especifique otro:",
    "contacto_email": "E-mail:",
    "contacto_tel": "Teléfono:",
    "epoca_const_p": "3.1 Época de construcción",
    "pisos_sobre": "Número de pisos sobre el terreno",
    "sotanos": "Número de sótanos",
    "ocupantes": "Número aproximado de ocupantes",
    "frente_m": "Dimensiones — Frente (m):",
    "fondo_m": "Dimensiones — Fondo (m):",
    "unidades_residenciales": "Número de unidades residenciales:",
    "unidades_comerciales": "Número de unidades comerciales:",
    "unidades_no_habitadas": "Número de unidades no habitadas:",
    "estado_victimas": "¿Se conoce el número de muertos y heridos?",
    "muertos": "Muertos:",
    "heridos": "Heridos:",
    "acceso": "Acceso a la edificación",
    "uso_edif": "3.2 Uso de la edificación",
    "uso_otro": "¿Cuál?",
    "sis_estructural_p": "3.3.1 Sistema estructural:",
    "sis_estructural_otro": "¿Cuál?.1",
    "material_p": "3.3.2 Material:",
    "entrepiso_material": "3.4.1 Material del entrepiso:",
    "entrepiso_p": "3.4.2 Sistema de entrepiso:",
    "entrepiso_otro": "¿Cuál?.2",
    "sistemas_combinados": "¿Existen sistemas combinados?",
    "observ_sistemas": "Observaciones:",
    "cubierta_soporte_p": "3.5.1 Sistema de soporte de cubierta:",
    "cubierta_soporte_otro": "¿Cuál?.3",
    "cubierta_revest_p": "3.5.2 Revestimiento de cubierta:",
    "cubierta_revest_otro": "¿Cuál?.4",
    "muros_divisorios_p": "3.6.1 Muros divisorios:",
    "muros_div_otro": "¿Cuál?.5",
    "fachadas_p": "3.6.2 Fachadas:",
    "fachadas_otro": "¿Cuál?.6",
    "escaleras_p": "3.6.3 Escaleras:",
    "escaleras_otro": "¿Cuál?.7",
    "calidad_diseno_p": "3.7 Calidad del diseño y la construcción:",
    "estado_conservacion_p": "3.8 Estado de la edificación (conservación):",
    "r_41_a": "4.1 Caída de objetos de edificios adyacentes — a) Existe riesgo externo",
    "r_41_b": "4.1 — b) Compromete estabilidad de la edificación",
    "r_41_c": "4.1 — c) Compromete accesos y/o ocupantes",
    "r_42_a": "4.2 Colapso o probable colapso de edificios adyacentes — a) Existe riesgo externo",
    "r_42_b": "4.2 — b) Compromete estabilidad de la edificación",
    "r_42_c": "4.2 — c) Compromete accesos y/o ocupantes",
    "r_43_a": "4.3 Falla en sistemas de distribución de servicios — a) Existe riesgo externo",
    "r_43_b": "4.3 — b) Compromete estabilidad de la edificación",
    "r_43_c": "4.3 — c) Compromete accesos y/o ocupantes",
    "r_44_a": "4.4 Inestabilidad del terreno, movimientos en masa — a) Existe riesgo externo",
    "r_44_b": "4.4 — b) Compromete estabilidad de la edificación",
    "r_44_c": "4.4 — c) Compromete accesos y/o ocupantes",
    "r_45_a": "4.5 Accesos y salidas — a) Existe riesgo externo",
    "r_45_b": "4.5 — b) Compromete estabilidad de la edificación",
    "r_45_c": "4.5 — c) Compromete accesos y/o ocupantes",
    "r_46_a": "4.6 Otro — a) Existe riesgo externo",
    "r_46_b": "4.6 — b) Compromete estabilidad de la edificación",
    "r_46_c": "4.6 — c) Compromete accesos y/o ocupantes",
    "r_46_desc": "4.6 Especifique cuál:",
    "d_colapso_total": "5.1 Colapso total",
    "d_colapso_parcial": "5.2 Colapso parcial",
    "d_asentamiento": "5.3 Asentamiento severo en elementos estructurales",
    "d_inclinacion": "5.4 Inclinación o desviación importante",
    "d_inestabilidad_suelo": "5.5 Problemas de inestabilidad en el suelo de cimentación",
    "d_riesgo_caidas": "5.6 Riesgo de caída de elementos de la edificación",
    "d_estructurales": "5.7 Daño en muros de carga, columnas y otros elementos",
    "d_contrapiso": "5.8 Daño en contrapiso, entrepiso, muros de contención",
    "d_muros_div": "5.9 Daño en muros divisorios, fachada, antepechos",
    "d_cubierta": "5.10 Cubierta (recubrimiento y estructura de soporte)",
    "d_cielorrasos": "5.11 Cielo rasos, luminarias, instalaciones",
    "alcance_exterior": "Alcance — Exterior:",
    "alcance_interior": "Alcance — Interior:",
    "nota_matriz": "Matriz de referencia (6.2 y 6.3)",
    "afectacion_planta": "6.1 Porcentaje de afectación en planta:",
    "severidad_calc": "severidad_calc",
    "severidad_final": "6.2 Severidad de daños:",
    "nivel_dano_calc": "nivel_dano_calc",
    "nivel_dano_final": "6.3 Nivel de daño en la edificación:",
    "riesgo_ab": "riesgo_ab",
    "riesgo_ac": "riesgo_ac",
    "habitabilidad_calc": "habitabilidad_calc",
    "habitabilidad_final": "7. Criterio de habitabilidad:",
    "justif_habitabilidad": "Justifique el cambio respecto al criterio sugerido:",
    "eval_adicional_p": "8.1 Evaluación adicional requerida:",
    "eval_estructural_obs": "8.1 Evaluación estructural — detalle:",
    "eval_geotecnica_obs": "8.1 Evaluación geotécnica — detalle:",
    "eval_otra_obs": "8.1 Otra evaluación — ¿cuál?",
    "recomendaciones_p": "8.2 Recomendaciones y medidas:",
    "aislamiento_areas": "Aislamiento — indique las áreas:",
    "intervencion_cual": "Intervención de entidades — ¿cuáles?",
    "observaciones_generales": "Observaciones generales:",
    "evento_id": "evento_id",
    "gps_precision_m": "gps_precision_m",
    "CreationDate": "CreationDate",
    "Creator": "Creator",
    "EditDate": "EditDate",
    "Editor": "Editor",
    "note_alcance": "Alcance de la evaluación:Exterior: qué tanto del perímetro externo de la "
    "edificación se logró observar — Parcial (solo una parte visible) o Completa "
    "(se recorrió todo el contorno).Interior: si se pudo ingresar a la edificación "
    "— No Ingreso (no fue posible entrar), Parcial (se inspeccionaron algunas "
    "áreas) o Completa (se recorrió toda la edificación por dentro).",
    "matricula_profesional": "Matrícula Profesional:",
}


def fetch_survey_raw() -> pd.DataFrame:
    """Fetch every record from the live Survey123 feature layer and return a
    DataFrame with the exact raw_data column contract (labels, dtypes, GlobalID
    format), so normalize() behaves identically to the sheet path. Any fetch or
    pagination failure raises — this sync must never run on partial data."""
    features: list[dict] = []
    offset = 0
    while True:
        resp = requests.get(
            f"{SURVEY_LAYER_URL}/query",
            params={
                "where": "1=1",
                "orderByFields": "objectid ASC",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "json",
                "resultOffset": offset,
            },
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"Survey123 layer query failed: {payload['error']}")
        page = payload.get("features", [])
        if not page:
            if payload.get("exceededTransferLimit"):
                raise RuntimeError("Survey123 pagination returned an empty page mid-stream.")
            break
        features.extend(page)
        if not payload.get("exceededTransferLimit"):
            break
        offset = len(features)
    if not features:
        raise RuntimeError("Survey123 layer returned 0 records; refusing to sync.")

    rows = []
    for ft in features:
        attrs = dict(ft["attributes"])
        geom = ft.get("geometry") or {}
        attrs["x"], attrs["y"] = geom.get("x"), geom.get("y")
        rows.append(attrs)
    df = pd.DataFrame(rows).rename(columns=LAYER_TO_RAW)

    # Raw contract fidelity (all verified against the original xlsx export):
    # epoch ms -> naive datetime, '' -> NA (blank cells), GlobalID lowercase
    # without braces (the layer already serves that format; normalizing keeps
    # the upsert key safe if the service ever switches to '{UPPER}').
    for field in SURVEY_DATE_FIELDS:
        col = LAYER_TO_RAW[field]
        df[col] = pd.to_datetime(df[col], unit="ms", errors="coerce")
    df = df.replace("", pd.NA)
    df["GlobalID"] = df["GlobalID"].astype("string").str.strip("{}").str.lower()

    # Same column order as the xlsx export: survey order, then x/y.
    df = df[list(LAYER_TO_RAW.values()) + ["x", "y"]]
    log.info("Fetched %d record(s) from the Survey123 layer.", len(df))
    return df


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize(rows_raw: pd.DataFrame) -> pd.DataFrame:
    """Apply the exact contract refresh_data used to build tabla_normalizada."""
    df = rows_raw.drop(columns=[c for c in COLS_A_ELIMINAR if c in rows_raw.columns])
    df = df.rename(columns=RENAME_MAP)
    if "municipio" in df.columns:
        df["municipio"] = normalize_municipio(df["municipio"])
    df = add_date_fields(df)
    df = coerce_numeric(df)
    df = drop_pii(df)
    df = spatial_join(df)
    df = add_id_edan(df)
    df = add_address_norm(df)
    return df


def run(dry_run: bool = False, source: str = "survey") -> None:
    content, local_path, _ = acquire_xlsx("drive")
    sheet_src = io.BytesIO(content) if content is not None else local_path
    xls = pd.ExcelFile(sheet_src)
    if NORMALIZED_TAB not in xls.sheet_names:
        raise ValueError(f"'{NORMALIZED_TAB}' tab not found (tabs: {xls.sheet_names}).")

    if source == "survey":
        raw = fetch_survey_raw()
    else:
        if RAW_TAB not in xls.sheet_names:
            raise ValueError(f"'{RAW_TAB}' tab not found (tabs: {xls.sheet_names}).")
        raw = pd.read_excel(xls, sheet_name=RAW_TAB)
    # Read WITHOUT dropna so DataFrame row i maps to sheet row i+2 (header + 1-based).
    norm = pd.read_excel(xls, sheet_name=NORMALIZED_TAB)
    target_cols = list(norm.columns)
    log.info("raw(%s)=%d filas, %s=%d filas.", source, len(raw), NORMALIZED_TAB, len(norm))

    if "GlobalID" not in raw.columns or "ObjectID" not in raw.columns:
        raise ValueError("raw_data must have GlobalID and ObjectID to sync by identity.")

    # Keep only the newest raw row per GlobalID (max ObjectID = latest edit).
    raw = raw[raw["GlobalID"].notna()].copy()
    raw["_oid"] = raw["ObjectID"].map(_int)
    # na_position="first": a row with a malformed/missing ObjectID must never
    # win over a sibling with a real ObjectID (default NaN-last would keep it).
    raw_latest = (raw.sort_values("_oid", na_position="first")
                     .drop_duplicates("GlobalID", keep="last"))

    # Current state of each inspection in the tab: GlobalID -> (sheet_row, ObjectID).
    norm_oid = norm["ObjectID"].map(_int) if "ObjectID" in norm.columns else [None] * len(norm)
    gid_to_row: dict[str, tuple[int, int | None]] = {}
    for i, (gid, oid) in enumerate(zip(norm["GlobalID"], norm_oid)):
        if pd.notna(gid) and str(gid).strip():
            key = str(gid).strip()
            prev = gid_to_row.get(key)
            # If the tab already holds duplicate rows for a GlobalID, treat the
            # newest (max ObjectID) as canonical so edit detection stays correct
            # and we never re-append an inspection that already exists.
            if prev is None or (oid is not None and (prev[1] is None or oid > prev[1])):
                gid_to_row[key] = (i + 2, oid)

    new_idx: list = []
    edited: list[tuple[int, object]] = []  # (sheet_row, raw_index)
    for raw_idx, row in raw_latest.iterrows():
        gid = str(row["GlobalID"]).strip()
        oid = row["_oid"]
        if gid not in gid_to_row:
            new_idx.append(raw_idx)
        else:
            sheet_row, cur_oid = gid_to_row[gid]
            if oid is not None and (cur_oid is None or oid > cur_oid):
                edited.append((sheet_row, raw_idx))

    log.info("A sincronizar: %d nueva(s), %d editada(s) (de %d inspecciones).",
             len(new_idx), len(edited), len(raw_latest))
    if not new_idx and not edited:
        log.info("Nada que sincronizar; tabla_normalizada ya está al día.")
        return

    touched_idx = list(new_idx) + [i for _, i in edited]
    touched = normalize(raw_latest.loc[touched_idx]).reindex(columns=target_cols)

    def values_for(raw_idx) -> list:
        row_df = touched.loc[[raw_idx]]
        vals = json.loads(row_df.to_json(orient="values", date_format="iso"))[0]
        return ["" if c is None else c for c in vals]

    if dry_run:
        preview = [(str(raw_latest.loc[i, "GlobalID"])[:8],
                    raw_latest.loc[i, "Nombre de la edificación:"] if "Nombre de la edificación:" in raw_latest.columns else "")
                   for _, i in edited][:20]
        log.info("[dry-run] no se escribe nada.")
        log.info("[dry-run] editadas (GlobalID, edificación): %s", preview)
        return

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=SA_SCOPES)
    ss = build("sheets", "v4", credentials=creds).spreadsheets()

    # Update edited inspections in place — a single batch keeps it atomic-ish.
    if edited:
        data = [{"range": f"'{NORMALIZED_TAB}'!A{sheet_row}", "values": [values_for(raw_idx)]}
                for sheet_row, raw_idx in edited]
        ss.values().batchUpdate(
            spreadsheetId=DRIVE_FILE_ID,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        log.info("Actualizadas %d fila(s) editada(s) en su lugar.", len(edited))

    # Append genuinely new inspections after the last row.
    if new_idx:
        rows = [values_for(i) for i in new_idx]
        ss.values().append(
            spreadsheetId=DRIVE_FILE_ID,
            range=f"'{NORMALIZED_TAB}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
        log.info("Agregadas %d inspección(es) nueva(s).", len(new_idx))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report new/edited rows; write nothing.")
    parser.add_argument("--source", choices=["survey", "sheet"], default="survey",
                        help="Raw source: live Survey123 layer (default) or the legacy raw_data tab.")
    args = parser.parse_args()
    run(dry_run=args.dry_run, source=args.source)


if __name__ == "__main__":
    main()
