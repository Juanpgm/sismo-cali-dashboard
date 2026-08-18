"""Refresh the public seismic-inspection dashboard data.

The dashboard's source of truth is the curated `tabla_normalizada` tab of the
Google Sheet. This script EXPORTS the Sheet, READS `tabla_normalizada` as-is
(it is maintained externally and already dashboard-shaped), strips PII
defensively, and writes `inspections.json` + `meta.json` for the static
frontend. It NEVER re-derives that table from `raw_data` and NEVER writes it
back — `raw_data` only supplies the original variable labels, encoded in
RENAME_MAP for the dashboard's field titles.

No API/server is started here — this is a batch/cron-style script. Run it
directly, or with `--loop SECONDS` to re-run forever (e.g. from an hourly
cron wrapper).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import string
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from address_norm import normalize_address

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("refresh_data")

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_XLSX = REPO_ROOT / "S123_042e021e34e349ddadf738270674dcc9_EXCEL.xlsx"

# Polygon names for the spatial join are read from the *prepared* basemaps
# (web/data/comunas.geojson, web/data/barrios.geojson), not the raw files
# under basemaps/. prepare_basemaps.py already repairs the U+FFFD mojibake
# in barrio names there; reading its output instead of re-deriving names
# from the raw basemap guarantees `comuna`/`barrio_geo` match the polygon
# `name` values the frontend renders, by construction — run
# `python scripts/prepare_basemaps.py` before this script.
PREPARED_BASEMAPS_DIR = REPO_ROOT / "web" / "data"

# Source is now a NATIVE Google Sheet (converted from the Survey123 xlsx export),
# not an uploaded .xlsx. Native Sheets must be *exported* to xlsx (Drive API
# files.export, or the docs export URL) — a plain file download returns HTML.
DRIVE_FILE_ID = "19k--nAEScol_3E7nbSpPev07gW2_UT8ojSsaMGbn6Ds"
# Export a public native Sheet as xlsx without auth (only works if it's shared publicly).
SHEET_EXPORT_URL = "https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"
# MIME type requested when exporting a native Sheet via the Drive API (service account).
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Tab (worksheet) in the same Sheet where the normalized table is written back.
NORMALIZED_TAB = "tabla_normalizada"

# Service-account scopes: read (export the raw tab) + write (update the normalized tab).
SA_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# id_edan: 5-char code from 0-9 + A-Z (uppercase, no symbols).
ID_EDAN_ALPHABET = string.digits + string.ascii_uppercase

DEFAULT_OUT_DIR = REPO_ROOT / "web" / "data"

# --- Cleaning contract copied verbatim from data_cleaning.ipynb ---------

COLS_A_ELIMINAR = [
    "¿Se conoce el número de muertos y heridos?",
    "Alcance de la evaluación:Exterior: qué tanto del perímetro externo de la edificación se logró observar — "
    "Parcial (solo una parte visible) o Completa (se recorrió todo el contorno)."
    "Interior: si se pudo ingresar a la edificación — No Ingreso (no fue posible entrar), "
    "Parcial (se inspeccionaron algunas áreas) o Completa (se recorrió toda la edificación por dentro).",
]

RENAME_MAP = {
    "Fecha de Inspección:": "fecha_inspeccion",
    "Hora:": "hora",
    "Nombre Evaluador:": "nombre_evaluador",
    "ID Grupo:": "id_grupo",
    "Especifique la entidad:": "entidad",
    "Tipo de evento:": "tipo_evento",
    "Nombre de la edificación:": "nombre_edificacion",
    "Municipio:": "municipio",
    "Barrio/vereda:": "barrio_vereda",
    "Dirección:": "direccion",
    "2.2 Código predial / catastral (si se tiene):": "cod_predial_catastral",
    "Tipo de propiedad:": "tipo_propiedad",
    "Nombre de contacto:": "nombre_contacto",
    "Relación con la edificación:": "relacion_edificacion",
    "Especifique otro:": "otro",
    "E-mail:": "email",
    "Teléfono:": "telefono",
    "3.1 Época de construcción": "epoca_construccion",
    "Número de pisos sobre el terreno": "n_pisos",
    "Número de sótanos": "n_sotanos",
    "Número aproximado de ocupantes": "n_ocupantes",
    "Dimensiones — Frente (m):": "frente",
    "Dimensiones — Fondo (m):": "fondo",
    "Número de unidades residenciales:": "n_residenciales",
    "Número de unidades comerciales:": "n_comerciales",
    "Número de unidades no habitadas:": "n_no_habitadas",
    "Muertos:": "n_muertos",
    "Heridos:": "n_heridos",
    "Acceso a la edificación": "acceso_edificacion",
    "3.2 Uso de la edificación": "uso_edificacion",
    "¿Cuál?": "uso_cual",
    "3.3.1 Sistema estructural:": "sistema_estructural",
    "¿Cuál?.1": "sistema_estructural_cual",
    "3.3.2 Material:": "material_estructura",
    "3.4.1 Material del entrepiso:": "material_entrepiso",
    "3.4.2 Sistema de entrepiso:": "sistema_entrepiso",
    "¿Cuál?.2": "sistema_entrepiso_cual",
    "¿Existen sistemas combinados?": "existen_sistemas_combinados",
    "Observaciones:": "observaciones",
    "3.5.1 Sistema de soporte de cubierta:": "sistema_cubierta",
    "¿Cuál?.3": "sistema_cubierta_cual",
    "3.5.2 Revestimiento de cubierta:": "revestimiento_cubierta",
    "¿Cuál?.4": "revestimiento_cubierta_cual",
    "3.6.1 Muros divisorios:": "sistema_muros_divisorios",
    "¿Cuál?.5": "sistema_muros_divisorios_cual",
    "3.6.2 Fachadas:": "fachadas",
    "¿Cuál?.6": "fachadas_cual",
    "3.6.3 Escaleras:": "escaleras",
    "¿Cuál?.7": "escaleras_cual",
    "3.7 Calidad del diseño y la construcción:": "calidad_construccion",
    "3.8 Estado de la edificación (conservación):": "estado_edificacion",
    "4.1 Caída de objetos de edificios adyacentes — a) Existe riesgo externo": "41_a",
    "4.1 — b) Compromete estabilidad de la edificación": "41_b",
    "4.1 — c) Compromete accesos y/o ocupantes": "41_c",
    "4.2 Colapso o probable colapso de edificios adyacentes — a) Existe riesgo externo": "42_a",
    "4.2 — b) Compromete estabilidad de la edificación": "42_b",
    "4.2 — c) Compromete accesos y/o ocupantes": "42_c",
    "4.3 Falla en sistemas de distribución de servicios — a) Existe riesgo externo": "43_a",
    "4.3 — b) Compromete estabilidad de la edificación": "43_b",
    "4.3 — c) Compromete accesos y/o ocupantes": "43_c",
    "4.4 Inestabilidad del terreno, movimientos en masa — a) Existe riesgo externo": "44_a",
    "4.4 — b) Compromete estabilidad de la edificación": "44_b",
    "4.4 — c) Compromete accesos y/o ocupantes": "44_c",
    "4.5 Accesos y salidas — a) Existe riesgo externo": "45_a",
    "4.5 — b) Compromete estabilidad de la edificación": "45_b",
    "4.5 — c) Compromete accesos y/o ocupantes": "45_c",
    "4.6 Otro — a) Existe riesgo externo": "46_a",
    "4.6 — b) Compromete estabilidad de la edificación": "46_b",
    "4.6 — c) Compromete accesos y/o ocupantes": "46_c",
    "4.6 Especifique cuál:": "46_especifique_cual",
    "5.1 Colapso total": "colapso_total",
    "5.2 Colapso parcial": "colapso_parcial",
    "5.3 Asentamiento severo en elementos estructurales": "asentamiento_severo",
    "5.4 Inclinación o desviación importante": "inclinacion_importante",
    "5.5 Problemas de inestabilidad en el suelo de cimentación": "suelo_inestable",
    "5.6 Riesgo de caída de elementos de la edificación": "riesgo_caida",
    "5.7 Daño en muros de carga, columnas y otros elementos": "danos_estructura",
    "5.8 Daño en contrapiso, entrepiso, muros de contención": "danos_contrapiso_entrepiso_muroscont",
    "5.9 Daño en muros divisorios, fachada, antepechos": "danos_muro_div",
    "5.10 Cubierta (recubrimiento y estructura de soporte)": "danos_cubierta",
    "5.11 Cielo rasos, luminarias, instalaciones": "cielos_instalaciones",
    "Alcance — Exterior:": "alc_exterior",
    "Alcance — Interior:": "alc_interior",
    "Matriz de referencia (6.2 y 6.3)": "matriz_ref",
    "6.1 Porcentaje de afectación en planta:": "afectacion_planta",
    "severidad_calc": "afectacion_planta_calc",
    "6.2 Severidad de daños:": "severidad_danos",
    "nivel_dano_calc": "severidad_danos_calc",
    "6.3 Nivel de daño en la edificación:": "nivel_dano",
    "7. Criterio de habitabilidad:": "criterio_habitabilidad",
    "Justifique el cambio respecto al criterio sugerido:": "justificacion_criterio",
    "8.1 Evaluación adicional requerida:": "requiere_evaluacion_adicional",
    "8.1 Evaluación estructural — detalle:": "eval_estructural",
    "8.1 Evaluación geotécnica — detalle:": "eval_geotecnica",
    "8.1 Otra evaluación — ¿cuál?": "eval_otra",
    "8.2 Recomendaciones y medidas:": "recomendaciones",
    "Aislamiento — indique las áreas:": "aislamiento",
    "Intervención de entidades — ¿cuáles?": "intervencion_entades",
    "Observaciones generales:": "observaciones_generales",
    "Matrícula Profesional:": "matricula_profesional",
}

# Public dashboard: never export these, regardless of what's in RENAME_MAP.
PII_COLUMNS = ["email", "telefono", "nombre_contacto", "cod_predial_catastral", "matricula_profesional"]

NUMERIC_COLUMNS = [
    "n_pisos",
    "n_ocupantes",
    "n_muertos",
    "n_heridos",
    "n_residenciales",
    "n_comerciales",
    "n_no_habitadas",
    "frente",
    "fondo",
    "gps_precision_m",
    "x",
    "y",
]


# --- Step 1: acquire the source xlsx ------------------------------------


def _looks_like_xlsx(content: bytes) -> bool:
    # xlsx files are zip archives -> local file header magic bytes "PK\x03\x04"
    return content[:2] == b"PK"


def download_public(file_id: str) -> bytes:
    """Export a *public* native Google Sheet as xlsx. If the sheet isn't shared
    publicly, Google returns an HTML sign-in page — caught by the PK check."""
    session = requests.Session()
    url = SHEET_EXPORT_URL.format(file_id=file_id)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    if _looks_like_xlsx(resp.content):
        return resp.content
    raise ValueError("Public Sheet export did not return a valid xlsx (sheet not shared publicly?).")


def download_service_account(file_id: str) -> bytes:
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set.")

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError as exc:
        raise ImportError("googleapiclient/google-auth not installed; skipping service-account download.") from exc

    credentials = service_account.Credentials.from_service_account_file(creds_path, scopes=SA_SCOPES)
    drive_service = build("drive", "v3", credentials=credentials)

    # Native Google Sheets are exported (not downloaded) to xlsx via export_media.
    request = drive_service.files().export_media(fileId=file_id, mimeType=XLSX_MIME)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    content = buffer.getvalue()
    if not _looks_like_xlsx(content):
        raise ValueError("Service-account export did not return a valid xlsx.")
    return content


def acquire_xlsx(source: str) -> tuple[bytes | None, Path | None, str]:
    """Returns (content_bytes_or_None, local_path_or_None, source_used)."""
    if source == "local":
        if not LOCAL_XLSX.exists():
            raise FileNotFoundError(f"Local fallback xlsx not found: {LOCAL_XLSX}")
        log.warning("Using local fallback xlsx: %s", LOCAL_XLSX)
        return None, LOCAL_XLSX, "local"

    attempts = []
    if source in ("auto", "drive"):
        # Service account first: the Sheet is private, shared only with the SA.
        # Public export is a fallback for when the sheet is also shared publicly.
        attempts.append(("service_account", lambda: download_service_account(DRIVE_FILE_ID)))
        attempts.append(("public", lambda: download_public(DRIVE_FILE_ID)))

    for label, fn in attempts:
        try:
            log.info("Trying Drive download via %s ...", label)
            content = fn()
            log.info("Drive download via %s succeeded (%d bytes).", label, len(content))
            return content, None, "drive"
        except Exception as exc:  # noqa: BLE001 - any failure just moves to next strategy
            log.warning("Drive download via %s failed: %s", label, exc)

    if source == "drive":
        raise RuntimeError("All Drive download strategies failed and source='drive' disallows local fallback.")

    if not LOCAL_XLSX.exists():
        raise FileNotFoundError(f"No Drive download succeeded and local fallback xlsx not found: {LOCAL_XLSX}")
    log.warning("All Drive download strategies failed. Using local fallback xlsx: %s", LOCAL_XLSX)
    return None, LOCAL_XLSX, "local"


# --- Step 2: clean per data_cleaning.ipynb -------------------------------


def load_normalized_table(content: bytes | None, local_path: Path | None) -> pd.DataFrame:
    """Read the curated `tabla_normalizada` tab — the dashboard's source of
    truth. It is maintained externally and already dashboard-shaped (normalized
    column names, id_edan, coords, comuna/barrio, …), so it is served as-is: we
    never re-derive it from `raw_data` and never write it back."""
    source = io.BytesIO(content) if content is not None else local_path
    xls = pd.ExcelFile(source)
    if NORMALIZED_TAB not in xls.sheet_names:
        raise ValueError(
            f"'{NORMALIZED_TAB}' tab not found in the sheet (tabs: {xls.sheet_names})."
        )
    log.info("Reading '%s' (tabs available: %s).", NORMALIZED_TAB, xls.sheet_names)
    df = pd.read_excel(xls, sheet_name=NORMALIZED_TAB)
    df = df.dropna(how="all")  # drop fully-empty rows a Sheet export can append
    return df


# --- Step 3: post-processing ---------------------------------------------


def normalize_municipio(series: pd.Series) -> pd.Series:
    def _norm(value):
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return value
        # Every observed value in this dataset (Cali, Santiago de Cali,
        # Valle del Cauca, Calib, typos, casing/spacing variants) refers to
        # the same municipality: Cali.
        return "Cali"

    return series.apply(_norm)


def add_date_fields(df: pd.DataFrame) -> pd.DataFrame:
    fecha_dt = pd.to_datetime(df["fecha_inspeccion"], errors="coerce")
    df["fecha_inspeccion"] = fecha_dt.dt.strftime("%Y-%m-%d")

    hora_str = df["hora"].astype("string")

    def _combine(date_str, hora_val):
        if pd.isna(date_str) or not isinstance(hora_val, str) or not hora_val.strip():
            return None
        match = re.match(r"^(\d{1,2}):(\d{2})", hora_val.strip())
        if not match:
            return None
        hh, mm = match.groups()
        return f"{date_str}T{int(hh):02d}:{mm}"

    df["fecha_hora"] = [
        _combine(d, h) for d, h in zip(df["fecha_inspeccion"], hora_str)
    ]
    return df


def coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def add_address_norm(df: pd.DataFrame) -> pd.DataFrame:
    """Add `direccion_norm` (IGAC-normalized address) right after `direccion`,
    and `coords` — the x (lon) / y (lat) pair as WGS84 "lat, lon", the same
    format as `coords_unificadas` in the EDAN SISMO table. Runs after
    coerce_numeric so x/y are already numeric."""
    if "direccion" in df.columns:
        norm = df["direccion"].apply(normalize_address)
        df.insert(df.columns.get_loc("direccion") + 1, "direccion_norm", norm)
    if "x" in df.columns and "y" in df.columns:
        coords = [
            f"{lat:.6f}, {lon:.6f}" if pd.notna(lon) and pd.notna(lat) else ""
            for lon, lat in zip(df["x"], df["y"])
        ]
        pos = (df.columns.get_loc("direccion_norm") + 1
               if "direccion_norm" in df.columns else len(df.columns))
        df.insert(pos, "coords", coords)
    return df


def drop_pii(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop(columns=[c for c in PII_COLUMNS if c in df.columns])


def _base36(n: int) -> str:
    if n == 0:
        return "0"
    out = []
    while n:
        n, rem = divmod(n, 36)
        out.append(ID_EDAN_ALPHABET[rem])
    return "".join(reversed(out))


def _id_edan(key: str) -> str:
    """Deterministic 5-char uppercase alphanumeric id derived from a stable key,
    so the same inspection keeps its id across refreshes. 36^5 ≈ 60M codes."""
    digest = hashlib.sha1(str(key).encode("utf-8")).hexdigest()
    return _base36(int(digest, 16))[:5].rjust(5, "0")


def add_id_edan(df: pd.DataFrame) -> pd.DataFrame:
    """Prepend an `id_edan` column (5 chars, [0-9A-Z], no symbols). Stable key
    preference: GlobalID (survey UUID) → ObjectID → row position."""
    def key_for(row: dict, i: int) -> str:
        for col in ("GlobalID", "ObjectID"):
            v = row.get(col)
            if v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip():
                return str(v)
        return f"row-{i}"

    ids = [_id_edan(key_for(r, i)) for i, r in enumerate(df.to_dict("records"))]
    # Distinct records must get distinct ids. With ~hundreds of rows in a 60M
    # space a clash is astronomically unlikely, but never ship the ID path unchecked.
    if len(set(ids)) != len(ids):
        raise ValueError("id_edan collision detected; widen the id space or add a suffix.")
    df.insert(0, "id_edan", ids)
    return df


# --- Step 4: spatial join --------------------------------------------------


def load_prepared_polygons(geojson_path: Path):
    """Load geometries + `properties.name` from a *prepared* basemap (the
    output of prepare_basemaps.py), which already has repaired, join-ready
    names."""
    if not geojson_path.exists():
        raise FileNotFoundError(
            f"{geojson_path} not found. Run `python scripts/prepare_basemaps.py` first "
            "to generate the prepared/repaired basemaps used for the spatial join."
        )
    with geojson_path.open(encoding="utf-8") as f:
        data = json.load(f)
    geoms = []
    names = []
    for ft in data["features"]:
        geoms.append(shape(ft["geometry"]))
        names.append(ft["properties"].get("name"))
    return geoms, names


def spatial_join(df: pd.DataFrame) -> pd.DataFrame:
    comunas_geoms, comunas_names = load_prepared_polygons(PREPARED_BASEMAPS_DIR / "comunas.geojson")
    barrios_geoms, barrios_names = load_prepared_polygons(PREPARED_BASEMAPS_DIR / "barrios.geojson")

    comunas_tree = STRtree(comunas_geoms)
    barrios_tree = STRtree(barrios_geoms)

    def match(tree: STRtree, geoms, names, x, y):
        if pd.isna(x) or pd.isna(y):
            return None
        point = Point(x, y)
        candidate_idx = tree.query(point, predicate="intersects")
        for idx in candidate_idx:
            if geoms[idx].contains(point) or geoms[idx].intersects(point):
                return names[idx]
        return None

    comuna_vals = []
    barrio_geo_vals = []
    for x, y in zip(df["x"], df["y"]):
        comuna_vals.append(match(comunas_tree, comunas_geoms, comunas_names, x, y))
        barrio_geo_vals.append(match(barrios_tree, barrios_geoms, barrios_names, x, y))

    df["comuna"] = comuna_vals
    df["barrio_geo"] = barrio_geo_vals
    return df


# --- Step 5: write outputs -------------------------------------------------


def write_outputs(df: pd.DataFrame, out_dir: Path, source_used: str) -> tuple[Path, Path, int]:
    out_dir.mkdir(parents=True, exist_ok=True)

    records_json = df.to_json(orient="records", date_format="iso", force_ascii=False)
    records = json.loads(records_json)

    inspections_path = out_dir / "inspections.json"
    inspections_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if "evento_id" in df.columns and df["evento_id"].notna().any():
        event_id = Counter(df["evento_id"].dropna()).most_common(1)[0][0]
    else:
        event_id = None

    # Original (pre-normalization) EDAN-F3 column names, for the dashboard to
    # display in its selectable options. Only columns actually exported get a
    # label; internal/derived names are skipped.
    source_labels = {norm: orig for orig, norm in RENAME_MAP.items() if norm in df.columns}

    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "row_count": int(len(df)),
        "source": source_used,
        "event_id": event_id,
        "source_labels": source_labels,
    }
    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return inspections_path, meta_path, len(records)


# --- Orchestration ----------------------------------------------------------


def run_once(source: str, out_dir: Path) -> None:
    log.info("=== refresh_data run start (source=%s, out=%s) ===", source, out_dir)

    content, local_path, source_used = acquire_xlsx(source)
    df = load_normalized_table(content, local_path)
    log.info("Read '%s': %d rows, %d columns.", NORMALIZED_TAB, len(df), len(df.columns))

    # tabla_normalizada is already curated/dashboard-shaped; only enforce two
    # invariants before publishing: numeric dtypes survive the Sheet round-trip,
    # and PII is never exposed even if a column slipped into the table. We do NOT
    # re-derive, re-key, or write anything back to the Sheet.
    df = coerce_numeric(df)
    df = drop_pii(df)

    inspections_path, meta_path, n = write_outputs(df, out_dir, source_used)
    log.info("Wrote %s (%d records) and %s.", inspections_path, n, meta_path)
    log.info("=== refresh_data run complete (source_used=%s) ===", source_used)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the seismic dashboard data files.")
    parser.add_argument("--source", choices=["auto", "drive", "local"], default="auto")
    parser.add_argument("--loop", type=int, default=0, help="Re-run every N seconds forever. Default: run once.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Output directory for JSON files.")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="After a successful refresh, redeploy the web/ directory to Vercel production.",
    )
    return parser.parse_args()


def deploy_to_vercel() -> None:
    import subprocess

    web_dir = DEFAULT_OUT_DIR.parent
    log.info("Deploying %s to Vercel production...", web_dir)
    result = subprocess.run(
        ["vercel", "deploy", "--prod", "--yes"],
        cwd=web_dir,
        capture_output=True,
        text=True,
        shell=(os.name == "nt"),
    )
    if result.returncode == 0:
        log.info("Vercel deploy OK: %s", result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "done")
    else:
        log.error("Vercel deploy failed (exit %d): %s", result.returncode, result.stderr.strip()[-500:])


def main() -> None:
    args = parse_args()

    if args.loop and args.loop > 0:
        log.info("Looping every %d seconds. Press Ctrl+C to stop.", args.loop)
        while True:
            try:
                run_once(args.source, args.out)
                if args.deploy:
                    deploy_to_vercel()
            except Exception:  # noqa: BLE001 - keep the loop alive across failures
                log.exception("Run failed; will retry on next loop iteration.")
            time.sleep(args.loop)
    else:
        run_once(args.source, args.out)
        if args.deploy:
            deploy_to_vercel()


if __name__ == "__main__":
    main()
