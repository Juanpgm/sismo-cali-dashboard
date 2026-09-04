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
import math
import io
import json
from difflib import SequenceMatcher
import logging
import os
import re
import unicodedata
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
from geocode_validate import (
    API_KEY_ENV,
    cache_key,
    closer_candidate,
    geocode_address,
    haversine_m,
    load_cache,
    save_cache,
    to_google_address,
)

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

# Public Survey123 feature layer behind the form. Read-only: we only call
# queryAttachments to harvest photo EXIF GPS metadata (no image downloads,
# nothing is ever written to ArcGIS).
SURVEY_LAYER_URL = (
    "https://services8.arcgis.com/ljfiJpg35HWgdtaC/arcgis/rest/services/"
    "service_16fa1d2000ea4fa68304bc030a95e8d1/FeatureServer/0"
)

# Generous Cali bounding box for sanity-filtering photo GPS fixes.
CALI_LAT_RANGE = (2.9, 3.9)
CALI_LON_RANGE = (-77.2, -76.0)

# Tuning knob: photo-vs-form displacement (m) beyond which the photo centroid
# is suspicious enough to arbitrate with a geocoded address.
VALIDATION_DIST_M = 300

# Committed geocode cache — lives in the dashboard repo so it seeds the
# publish container and reruns cost 0 API calls.
GEOCODE_CACHE_PATH = REPO_ROOT / "web" / "data" / "geocode" / "geocode_cache.json"

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
    # cache_discovery=False silences the harmless "file_cache is only supported
    # with oauth2client<4.0.0" INFO line on modern oauth2client.
    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)

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


def dedup_latest_by_globalid(df: pd.DataFrame) -> pd.DataFrame:
    """Idempotency guard: keep exactly one row per GlobalID — the newest by
    ObjectID — so repeated loads/syncs and any accidental duplicate rows always
    converge to the same result. Rows without a GlobalID are kept untouched, and
    the original row order is preserved."""
    if "GlobalID" not in df.columns:
        return df
    df = df.reset_index(drop=True)
    gid = df["GlobalID"].astype("string").str.strip()
    has_gid = gid.notna() & gid.ne("")
    rank = (pd.to_numeric(df["ObjectID"], errors="coerce")
            if "ObjectID" in df.columns
            else pd.Series(range(len(df)), dtype="float")).fillna(-1)
    keep = list(df.index[~has_gid])
    with_gid = pd.DataFrame({"gid": gid[has_gid], "rank": rank[has_gid]}, index=df.index[has_gid])
    # For each GlobalID, keep the row index with the highest ObjectID.
    keep += list(with_gid.sort_values("rank").reset_index().groupby("gid")["index"].last())
    return df.loc[sorted(set(keep))].reset_index(drop=True)


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
    before = len(df)
    df = dedup_latest_by_globalid(df)
    if len(df) != before:
        log.warning("Colapsadas %d fila(s) duplicada(s) por GlobalID (idempotencia).", before - len(df))
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
    # dayfirst=True por si alguna fecha llegara como texto DD/MM/YYYY (colombiano);
    # las del layer ya vienen como datetime (epoch ms), así que el flag las deja
    # intactas.
    fecha_dt = pd.to_datetime(df["fecha_inspeccion"], errors="coerce", dayfirst=True)

    # Corrección de fechas imposibles: una inspección NO puede ser posterior a su
    # envío al sistema. Cuando fecha_inspeccion queda después de CreationDate
    # (timestamp de envío) —p.ej. el bug de "+7 días" del formulario, o fechas
    # futuras— se usa la fecha de CreationDate, que es la verdad del sistema y
    # nunca es futura. Las inspecciones enviadas días después (fecha < Creation)
    # no se tocan.
    if "CreationDate" in df.columns:
        creation_dt = pd.to_datetime(df["CreationDate"], errors="coerce")
        bad = (fecha_dt.notna() & creation_dt.notna()
               & (fecha_dt.dt.normalize() > creation_dt.dt.normalize()))
        if bad.any():
            log.info("Fechas posteriores al envío corregidas a CreationDate: %d.", int(bad.sum()))
        fecha_dt = fecha_dt.where(~bad, creation_dt)
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


# CRRA is a common data-entry typo for CARRERA that `normalize_address`'s
# road-type table does not cover -- none of its \bCRA\b-style patterns match
# a literal "CRRA" token, so it survives that function unchanged.
_CRRA_RE = re.compile(r"\bCRRA\b")


def normalize_direccion(value) -> str:
    """Single normalizer shared by BOTH the `direccion_norm` column shipped
    to the panel and `_clave_direccion`'s bucket key -- the address-matching
    step that runs BEFORE `_misma_edificacion`'s name-similarity/30 m cascade
    ever sees a pair of records (see that function's docstring: address
    alone is never a merge key, but a weak bucket key still hides true
    duplicates by splitting them into different buckets before the cascade
    gets a chance to compare them).

    `normalize_address` already canonicalizes most IGAC road-type
    abbreviations (CARRERA/CRA/KRA/CR -> KR, CALLE/CLL -> CL, AVENIDA/AVDA
    -> AV, DIAGONAL/DIAG -> DG, TRANSVERSAL/TRANSV -> TV) and No./Nro/N°/Nº
    -> '#'. This closes what it still misses: the CRRA typo, accent/case
    variance, and inconsistent spacing around '-'. Measured on live data,
    closing those gaps collapses 998 raw unique addresses to 962 -- 36 true
    duplicates the weaker key was hiding from the grouping cascade.
    """
    if pd.isna(value):
        return ""
    igac = normalize_address(value)
    if not igac or igac in {"-", " "}:
        return ""
    # Fold accents/case the same way `_norm_nombre` does for building names:
    # direccion_norm is a matching key first, a display value second, so
    # "Peñón" and "Penon" must bucket together.
    txt = unicodedata.normalize("NFKD", igac).encode("ascii", "ignore").decode().upper()
    # Stray dots `normalize_address` leaves on abbreviations it doesn't
    # recognize (e.g. "CRRA." -- not in its known-codes list) -- punctuation
    # noise, never meaningful in a Colombian cadastral address.
    txt = txt.replace(".", "")
    txt = _CRRA_RE.sub("KR", txt)
    txt = re.sub(r"\s*-\s*", "-", txt)   # tighten "46 - 45" -> "46-45"
    txt = re.sub(r"\s*#\s*", " # ", txt)  # re-apply after the fold above
    return re.sub(r"\s+", " ", txt).strip()


def add_address_norm(df: pd.DataFrame) -> pd.DataFrame:
    """Add `direccion_norm` (IGAC-normalized address) right after `direccion`,
    and `coords` — the x (lon) / y (lat) pair as WGS84 "lat, lon", the same
    format as `coords_unificadas` in the EDAN SISMO table. Runs after
    coerce_numeric so x/y are already numeric."""
    if "direccion" in df.columns:
        norm = df["direccion"].apply(normalize_direccion)
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


# No-habitable habitability codes (mirrors the frontend's NO_HABITABLE_CODES).
NO_HABITABLE_CODES = ("i1", "i2", "i3")


def _norm(value) -> str:
    """Accent-strip + lowercase + trim, matching the frontend's normalize()."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    import unicodedata

    stripped = unicodedata.normalize("NFD", str(value))
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    return stripped.lower().strip()


def add_suspension_servicios(df: pd.DataFrame) -> pd.DataFrame:
    """Derived `suspension_servicios` (si/no): colapso (parcial O total) declarado
    Y criterio de habitabilidad no habitable (I1–I3). Same rule the dashboard
    applies client-side; deriving it here too keeps inspections.xlsx consistent."""
    def _flag(row) -> str:
        colapso = (_norm(row.get("colapso_total")) in ("si", "sí")
                   or _norm(row.get("colapso_parcial")) in ("si", "sí"))
        hab = _norm(row.get("criterio_habitabilidad")) or _norm(row.get("habitabilidad_calc"))
        return "si" if colapso and hab in NO_HABITABLE_CODES else "no"

    df["suspension_servicios"] = [_flag(r) for r in df.to_dict("records")]
    return df


# --- Step 3b: photo-EXIF GPS coordinates ----------------------------------


def _dms_to_decimal(dms, ref) -> float:
    """[deg, min, sec] + hemisphere ref → signed decimal degrees. The EXIF
    value array is unsigned; the sign comes entirely from the S/W ref."""
    deg, minutes, seconds = (float(v) for v in dms)
    decimal = deg + minutes / 60.0 + seconds / 3600.0
    return -decimal if str(ref).strip().upper() in ("S", "W") else decimal


def _query_attachment_groups(definition_expression: str) -> dict:
    resp = requests.get(
        f"{SURVEY_LAYER_URL}/queryAttachments",
        params={
            "definitionExpression": definition_expression,
            "returnMetadata": "true",
            "f": "json",
        },
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"queryAttachments error: {data['error']}")
    return data


def _all_attachment_groups() -> list[dict]:
    """Every attachmentGroup for the layer, in one queryAttachments call (with a
    batched fallback if the transfer limit is hit). Raises if the endpoint
    returns nothing, so callers fail loud instead of marking records photo-less."""
    data = _query_attachment_groups("1=1")
    groups = data.get("attachmentGroups", [])
    if data.get("exceededTransferLimit"):
        # Verified: all 609 groups fit one ~5 MB response today. If the layer
        # ever grows past the transfer limit, re-fetch in objectid batches.
        log.info("queryAttachments truncated; re-fetching in objectid batches.")
        resp = requests.get(
            f"{SURVEY_LAYER_URL}/query",
            params={"where": "1=1", "returnIdsOnly": "true", "f": "json"},
            timeout=60,
        )
        resp.raise_for_status()
        ids = sorted(resp.json().get("objectIds") or [])
        groups = []
        batch = 200
        for start in range(0, len(ids), batch):
            chunk = ids[start:start + batch]
            expr = f"objectid >= {chunk[0]} AND objectid <= {chunk[-1]}"
            groups.extend(_query_attachment_groups(expr).get("attachmentGroups", []))
    if not groups:
        raise RuntimeError("queryAttachments returned 0 attachment groups.")
    return groups


def _photo_urls_from_groups(groups: list[dict]) -> dict[int, list[str]]:
    """Pure: attachmentGroups → {objectid: [download url, ...]}. Keeps only image
    attachments and skips the evaluator's signature (firma*), matching what the
    dashboard shows as "las fotos". Separated from the fetch so it is testable
    without the network (see scripts/test_photo_urls.py)."""
    urls: dict[int, list[str]] = {}
    for group in groups:
        oid = group.get("parentObjectId")
        if oid is None:
            continue
        for info in group.get("attachmentInfos", []):
            if not (info.get("contentType") or "").startswith("image"):
                continue
            if re.match(r"firma", info.get("name") or "", re.IGNORECASE):
                continue  # el attachment de la firma del evaluador, no una foto
            aid = info.get("id")
            if aid is None:
                continue
            urls.setdefault(int(oid), []).append(
                f"{SURVEY_LAYER_URL}/{int(oid)}/attachments/{aid}"
            )
    return urls


def fetch_photo_urls(groups: list[dict] | None = None) -> dict[int, list[str]]:
    """Direct download URLs for each record's inspection photos, for the xlsx
    export. {objectid: [url, ...]}.

    `groups` lets run_once() pass ONE _all_attachment_groups() result into both
    this and fetch_photo_coords() — the ~5 MB queryAttachments fetch used to run
    twice per refresh. Omit `groups` to fetch fresh (standalone/testing use)."""
    return _photo_urls_from_groups(groups if groups is not None else _all_attachment_groups())


def fetch_photo_coords(groups: list[dict] | None = None) -> dict[int, dict]:
    """Per-record GPS centroid from the photo attachments' EXIF metadata.

    Everything comes from queryAttachments with returnMetadata=true — no image
    downloads. Attachments without a GPS IFD (signatures, GPS-less photos) are
    skipped. Returns {objectid: {lat, lon, n_fotos_gps, gps_error_m}}.

    `groups`: see fetch_photo_urls — pass a pre-fetched result to avoid a
    second network round-trip; omit to fetch fresh.
    """
    if groups is None:
        groups = _all_attachment_groups()

    points: dict[int, list[tuple[float, float, float | None]]] = {}
    n_photos_gps = 0
    n_dropped = 0
    for group in groups:
        oid = group.get("parentObjectId")
        if oid is None:
            continue
        for info in group.get("attachmentInfos", []):
            gps = None
            for ifd in info.get("exifInfo") or []:
                if ifd.get("name") == "GPS":
                    gps = {t.get("name"): t.get("value") for t in ifd.get("tags", [])}
                    break
            if not gps or gps.get("GPS Latitude") is None or gps.get("GPS Longitude") is None:
                continue  # no GPS IFD → signature or GPS-less photo
            try:
                lat = _dms_to_decimal(gps["GPS Latitude"], gps.get("GPS Latitude Ref", "N"))
                lon = _dms_to_decimal(gps["GPS Longitude"], gps.get("GPS Longitude Ref", "W"))
            except (TypeError, ValueError):
                continue
            n_photos_gps += 1
            if not (CALI_LAT_RANGE[0] <= lat <= CALI_LAT_RANGE[1]
                    and CALI_LON_RANGE[0] <= lon <= CALI_LON_RANGE[1]):
                n_dropped += 1
                continue
            err = gps.get("GPS Horizontal Positioning Error")
            err_m = float(err) if isinstance(err, (int, float)) else None
            points.setdefault(int(oid), []).append((lat, lon, err_m))

    centroids: dict[int, dict] = {}
    for oid, pts in points.items():
        errs = [p[2] for p in pts if p[2] is not None]
        centroids[oid] = {
            "lat": sum(p[0] for p in pts) / len(pts),
            "lon": sum(p[1] for p in pts) / len(pts),
            "n_fotos_gps": len(pts),
            "gps_error_m": round(sum(errs) / len(errs), 1) if errs else None,
        }
    log.info(
        "Photo EXIF GPS: %d groups, %d photos with GPS (%d outliers dropped), %d record centroids.",
        len(groups), n_photos_gps, n_dropped, len(centroids),
    )
    return centroids


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


def apply_photo_coords(df: pd.DataFrame, groups: list[dict] | None = None) -> pd.DataFrame:
    """Override the form x/y with the photo-EXIF GPS centroid where available,
    keeping the originals in x_form/y_form. Fail-soft: any attachments-endpoint
    failure logs a warning and publishes form coordinates unchanged — the
    refresh is never blocked by ArcGIS. `groups`: see fetch_photo_urls."""
    df["x_form"] = df["x"]
    df["y_form"] = df["y"]
    df["n_fotos_gps"] = 0
    df["gps_error_m"] = None
    df["desplazamiento_m"] = None
    has_xy = df["x"].notna() & df["y"].notna()
    df["coords_fuente"] = None
    df.loc[has_xy, "coords_fuente"] = "formulario"

    try:
        centroids = fetch_photo_coords(groups)
    except Exception as exc:  # noqa: BLE001 - fail-soft by design
        log.warning("Photo-EXIF coords unavailable (%s); publishing form coordinates.", exc)
        return df

    oids = pd.to_numeric(df.get("ObjectID"), errors="coerce")
    corrected = pd.Series(False, index=df.index)
    for i in df.index:
        oid = oids[i]
        if pd.isna(oid) or int(oid) not in centroids:
            continue
        c = centroids[int(oid)]
        df.at[i, "x"] = c["lon"]
        df.at[i, "y"] = c["lat"]
        df.at[i, "coords_fuente"] = "foto_exif"
        df.at[i, "n_fotos_gps"] = c["n_fotos_gps"]
        df.at[i, "gps_error_m"] = c["gps_error_m"]
        xf, yf = df.at[i, "x_form"], df.at[i, "y_form"]
        if pd.notna(xf) and pd.notna(yf):
            df.at[i, "desplazamiento_m"] = round(
                _haversine_m(float(yf), float(xf), c["lat"], c["lon"]), 1
            )
        corrected[i] = True

    if corrected.any():
        # Keep the derived columns consistent with the corrected position:
        # the "lat, lon" string and the comuna/barrio_geo spatial join.
        if "coords" in df.columns:
            df.loc[corrected, "coords"] = [
                f"{y:.6f}, {x:.6f}"
                for x, y in zip(df.loc[corrected, "x"], df.loc[corrected, "y"])
            ]
        df = spatial_join(df, mask=corrected)
        df = resolve_zona_interes(df, mask=corrected)
    log.info("Photo-EXIF centroid applied to %d/%d records.", int(corrected.sum()), len(df))
    return df


def validate_photo_coords(df: pd.DataFrame) -> pd.DataFrame:
    """Arbitrate suspicious photo-EXIF corrections with a geocoded address.

    Only records whose photo centroid landed > VALIDATION_DIST_M from the form
    pin are evaluated (a handful — photos taken away from the building would
    otherwise move it kilometers). The geocode point is the ARBITER, never the
    published coordinate: whichever candidate (form pin or photo centroid) is
    closer to it wins. Fail-soft: any failure logs a warning and keeps the
    photo corrections as-is — the publish never breaks over geocoding.
    """
    for col in ("coords_validacion", "geocode_lat", "geocode_lon", "dist_geocode_m"):
        df[col] = None

    suspicious = (
        (df["coords_fuente"] == "foto_exif")
        & pd.to_numeric(df["desplazamiento_m"], errors="coerce").gt(VALIDATION_DIST_M)
    )
    if not suspicious.any():
        return df

    try:
        cache = load_cache(GEOCODE_CACHE_PATH)
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        session = requests.Session()
        dirty = False
        reverted = pd.Series(False, index=df.index)
        counts = Counter()
        for i in df.index[suspicious]:
            addr = None
            for col in ("direccion_norm", "direccion"):
                v = df.at[i, col] if col in df.columns else None
                if isinstance(v, str) and v.strip():
                    addr = v.strip()
                    break
            rec = cache.get(cache_key(addr)) if addr else None
            if rec is None and addr and api_key:
                try:
                    rec = geocode_address(to_google_address(addr), session, api_key)
                except Exception as exc:  # noqa: BLE001 - keep prior arbitrations consistent
                    log.warning("Geocode API failed (%s); remaining rows left unvalidated.", exc)
                    break
                rec["ts"] = int(time.time())
                rec["direccion"] = addr
                cache[cache_key(addr)] = rec
                dirty = True
                time.sleep(0.05)  # stay far under Google's QPS cap
            if not rec or not rec.get("accepted"):
                df.at[i, "coords_validacion"] = "sin_geocode"
                counts["sin_geocode"] += 1
                continue
            glat, glon = float(rec["lat"]), float(rec["lon"])
            xf, yf = float(df.at[i, "x_form"]), float(df.at[i, "y_form"])
            xp, yp = float(df.at[i, "x"]), float(df.at[i, "y"])
            if closer_candidate(glat, glon, yf, xf, yp, xp) == "formulario":
                df.at[i, "x"], df.at[i, "y"] = xf, yf
                df.at[i, "coords_fuente"] = "formulario"
                df.at[i, "coords_validacion"] = "foto_descartada_geocode"
                reverted[i] = True
            else:
                df.at[i, "coords_validacion"] = "foto_confirmada"
            counts[df.at[i, "coords_validacion"]] += 1
            df.at[i, "geocode_lat"] = glat
            df.at[i, "geocode_lon"] = glon
            df.at[i, "dist_geocode_m"] = round(
                haversine_m(glat, glon, float(df.at[i, "y"]), float(df.at[i, "x"])), 1
            )
        if dirty:
            save_cache(cache, GEOCODE_CACHE_PATH)
        if reverted.any():
            if "coords" in df.columns:
                df.loc[reverted, "coords"] = [
                    f"{y:.6f}, {x:.6f}"
                    for x, y in zip(df.loc[reverted, "x"], df.loc[reverted, "y"])
                ]
            df = spatial_join(df, mask=reverted)
            df = resolve_zona_interes(df, mask=reverted)
        log.info(
            "Geocode validation: %d suspicious (> %dm), %s.",
            int(suspicious.sum()), VALIDATION_DIST_M,
            dict(counts) or "none resolved",
        )
    except Exception as exc:  # noqa: BLE001 - fail-soft by design
        log.warning("Geocode validation failed (%s); keeping photo coords as-is.", exc)
    return df


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


def spatial_join(df: pd.DataFrame, mask: pd.Series | None = None) -> pd.DataFrame:
    """Assign comuna/barrio_geo from the prepared basemaps. With `mask`, only
    those rows are (re)joined and every other row keeps its existing values —
    used to refresh just the records whose x/y were corrected."""
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

    for col in ("comuna", "barrio_geo"):
        if col not in df.columns:
            df[col] = None
    rows = df.index if mask is None else df.index[mask]
    for i in rows:
        x, y = df.at[i, "x"], df.at[i, "y"]
        df.at[i, "comuna"] = match(comunas_tree, comunas_geoms, comunas_names, x, y)
        df.at[i, "barrio_geo"] = match(barrios_tree, barrios_geoms, barrios_names, x, y)
    return df


ZONAS_INTERES_GEOJSON = PREPARED_BASEMAPS_DIR / "zonas_interes.geojson"


def resolve_zona_interes(df: pd.DataFrame, mask: pd.Series | None = None) -> pd.DataFrame:
    """Assign `zona_interes` from the zonas_interes basemap (Centro Histórico
    / Avenida 6ta — see scripts/kml_to_zonas_interes.py). Also fetched (on
    demand) and drawn client-side by web/js/mapview.js as an optional,
    user-toggleable overlay layer, hidden by default and independent of the
    active map mode. Point-in-polygon join, same STRtree pattern as
    `spatial_join` above, but `covers()` instead of `contains()/intersects()`
    so a point exactly on the polygon boundary still resolves to that zone
    (matches the client-side ray-casting port in web/js/utils.js, which
    treats an edge/vertex hit as inside).

    El port de utils.js normaliza su tolerancia de borde por el largo de la
    arista (EPS_ON_SEGMENT, 1e-9 grados ~ 0,1 mm de distancia perpendicular),
    asi que ambas implementaciones coinciden salvo en esa banda submilimetrica.
    Medido con un fuzz diferencial de 33.852 puntos (aleatorios en el bbox,
    cada vertice, e interpolaciones sobre cada arista con offsets normales de
    1e-12 a 1e-4 grados): 0 desacuerdos por encima de 1e-9 grados, y el sesgo
    es siempre "utils.js incluye, covers() excluye", nunca al reves.

    Unlike `comuna`/`barrio_geo`, this basemap is OPTIONAL: it only exists
    once `python scripts/kml_to_zonas_interes.py` has been run. Missing file
    -> log a warning and leave the column as `None` for the affected rows
    rather than crashing the whole refresh (`spatial_join`'s comuna/barrio_geo
    basemaps are load-bearing for the rest of the pipeline and stay a hard
    FileNotFoundError; this one is not).

    Takes the same `mask` parameter as `spatial_join` and is wired at the
    IDENTICAL three call sites (normalize()'s initial join, plus the masked
    re-joins inside apply_photo_coords/validate_photo_coords): a photo-EXIF
    or geocode x/y correction moves the point, so zona_interes must be
    re-resolved for that row too, exactly like comuna/barrio_geo are — a
    single resolve-once-at-the-end call (the resolve_barrio_vereda() style)
    would leave corrected rows with a stale zone label."""
    if "zona_interes" not in df.columns:
        df["zona_interes"] = None
    if not ZONAS_INTERES_GEOJSON.exists():
        log.warning(
            "%s not found; zona_interes left as None. Run "
            "`python scripts/kml_to_zonas_interes.py` to generate it.",
            ZONAS_INTERES_GEOJSON,
        )
        return df

    geoms, names = load_prepared_polygons(ZONAS_INTERES_GEOJSON)
    tree = STRtree(geoms)

    def match(x, y):
        if pd.isna(x) or pd.isna(y):
            return None
        point = Point(x, y)
        for idx in tree.query(point, predicate="intersects"):
            if geoms[idx].covers(point):
                return names[idx]
        return None

    rows = df.index if mask is None else df.index[mask]
    for i in rows:
        df.at[i, "zona_interes"] = match(df.at[i, "x"], df.at[i, "y"])
    return df


def _clean_barrio_value(v):
    """None/NaN/blank -> None; anything else -> its stripped string. Guards
    on pd.isna() FIRST (not truthiness/str()) because str(float('nan')) ==
    'nan', a truthy non-empty string -- this pipeline has been bitten by
    exactly that before."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass  # v isn't NaN-able (e.g. already a plain str) -- fall through
    s = str(v).strip()
    return s or None


def resolve_barrio_vereda(df: pd.DataFrame) -> pd.DataFrame:
    """Derive `barrio_vereda_resuelto` / `barrio_vereda_fuente`: the
    geographic intersection against the basemaps (`barrio_geo`, from
    `spatial_join`) wins over the inspector's free-typed value
    (`barrio_vereda`) whenever both exist -- measured on inspections.json,
    the polygon is more precise in ~44.5% of the records that carry both
    ('Napoles' -> 'Alto Napoles'; one typed value was 'Suba 1', a Bogota
    neighbourhood, not even in Cali). Falls back to the typed value when the
    point falls outside every polygon (~1.2% of records) so those rows never
    go blank, tagged 'reportado' so downstream consumers know it is NOT
    geographic. Neither present -> `None` / 'sin_dato', never a crash.

    Neither `barrio_geo` nor `barrio_vereda` is mutated or removed -- both
    stay in the output, unchanged, for provenance/inspection. Pure function
    of those two columns: safe to call once, after every `spatial_join()`
    (including the corrected/reverted re-joins inside `apply_photo_coords`/
    `validate_photo_coords`) has settled."""
    n = len(df)
    geo_col = df["barrio_geo"] if "barrio_geo" in df.columns else pd.Series([None] * n, index=df.index)
    tipeado_col = df["barrio_vereda"] if "barrio_vereda" in df.columns else pd.Series([None] * n, index=df.index)

    resuelto = []
    fuente = []
    for g_raw, t_raw in zip(geo_col, tipeado_col):
        g = _clean_barrio_value(g_raw)
        t = _clean_barrio_value(t_raw)
        if g is not None:
            resuelto.append(g)
            fuente.append("geo")
        elif t is not None:
            resuelto.append(t)
            fuente.append("reportado")
        else:
            resuelto.append(None)
            fuente.append("sin_dato")

    df["barrio_vereda_resuelto"] = resuelto
    df["barrio_vereda_fuente"] = fuente
    return df


# --- Step 5: write outputs -------------------------------------------------


def write_outputs(
    df: pd.DataFrame,
    out_dir: Path,
    source_used: str,
    photo_urls: dict[int, list[str]] | None = None,
) -> tuple[Path, Path, int]:
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

    # Full dataset as xlsx for the dashboard's download button. Adds a `fotos`
    # column with the direct download URL of each record's photos (newline-
    # separated) so the xlsx is self-contained — the JSON stays lean without it.
    # Optional: never block the JSON publish over it.
    xlsx_df = df
    if photo_urls and "ObjectID" in df.columns:
        oids = pd.to_numeric(df["ObjectID"], errors="coerce")
        fotos = [
            "\n".join(photo_urls.get(int(o), [])) if pd.notna(o) else ""
            for o in oids
        ]
        xlsx_df = df.assign(fotos=fotos)
    try:
        xlsx_df.to_excel(out_dir / "inspections.xlsx", index=False)
    except Exception as exc:  # noqa: BLE001 - openpyxl may be absent; xlsx is optional
        log.warning("inspections.xlsx not written: %s", exc)

    return inspections_path, meta_path, len(records)


# --- Survey123 source (fuente única) ----------------------------------------
# El dashboard se alimenta directo del layer público de Survey123 (sin Google
# Sheets). fetch_survey_raw() trae el layer con el contrato exacto de raw_data y
# normalize() aplica el mismo pipeline que antes construía tabla_normalizada, así
# que todo lo downstream es idéntico.

# Layer date fields arrive as Unix epoch milliseconds.
SURVEY_DATE_FIELDS = ["fecha_inspeccion", "CreationDate", "EditDate"]

# Layer field name -> raw_data column label (Survey123 xlsx export headers,
# including pandas' ".N" suffixes for the duplicated "¿Cuál?" columns). Geometry
# supplies x/y.
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
    # planeacion-asignaciones (2026-08-26): the round-trip integration key
    # (design.md ADR-7) — was silently dropped by this very allowlist
    # before this fix. Raw label = the layer field name itself, because
    # unlike every other entry this field has no historical Survey123
    # xlsx-export header to preserve.
    "codigoapp": "codigoapp",
}


def fetch_survey_raw() -> pd.DataFrame:
    """Fetch every record from the live Survey123 feature layer and return a
    DataFrame with the exact raw_data column contract (labels, dtypes, GlobalID
    format), so normalize() behaves identically to the old sheet path. Any fetch
    or pagination failure raises — the refresh must never run on partial data."""
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
        raise RuntimeError("Survey123 layer returned 0 records; refusing to publish.")

    rows = []
    for ft in features:
        attrs = dict(ft["attributes"])
        geom = ft.get("geometry") or {}
        attrs["x"], attrs["y"] = geom.get("x"), geom.get("y")
        rows.append(attrs)
    df = pd.DataFrame(rows).rename(columns=LAYER_TO_RAW)

    # Raw contract fidelity: epoch ms -> naive datetime, '' -> NA (blank cells),
    # GlobalID lowercase without braces (the layer serves that format already).
    for field in SURVEY_DATE_FIELDS:
        col = LAYER_TO_RAW[field]
        df[col] = pd.to_datetime(df[col], unit="ms", errors="coerce")
    df = df.replace("", pd.NA)
    df["GlobalID"] = df["GlobalID"].astype("string").str.strip("{}").str.lower()

    # TRAP for the next person who adds a field to the form: this is an
    # explicit ALLOWLIST, not a drop-list. A new layer field with no
    # LAYER_TO_RAW entry is fetched (outFields: "*" above) and then
    # silently discarded right here — it never reaches inspections.json.
    # This is exactly the bug that shipped `codigoapp` empty for months
    # (planeacion-asignaciones, design.md ADR-7); check LAYER_TO_RAW first.
    # Same column order as the xlsx export: survey order, then x/y.
    df = df[list(LAYER_TO_RAW.values()) + ["x", "y"]]
    log.info("Fetched %d record(s) from the Survey123 layer.", len(df))
    return df


# --- Duplicate-building grouping -------------------------------------------
# The same building gets inspected more than once (a re-visit days later, or
# an accidental double-submit). Every submission carries its own GlobalID, so
# none of them is a duplicate BY KEY -- yet 1091 real records describe only
# 941 real buildings, inflating every Panel figure by ~13.7% (colapso total
# reads 33 when 30 buildings collapsed; colapso parcial 372 vs 332).
#
# Nothing is deleted. Each row is tagged with the building it belongs to and
# exactly one row per building is flagged `es_representante`, so KPIs and
# charts can count BUILDINGS while the table still lists every inspection.

# Habitability, worst first (ATC-20: i* unsafe > r* restricted > h habitable).
_HAB_SEVERIDAD = {"i2": 5, "i1": 4, "r2": 3, "r1": 2, "h": 1}
_DANO_SEVERIDAD = {"alto": 3, "medio": 2, "bajo": 1}


def _es_si(value) -> bool:
    return str(value).strip().lower() in {"si", "sí", "yes", "true", "1"}


def _recencia(row) -> tuple:
    """Ranking used to pick a group's representative — highest wins.

    The user's rule (2026-08-26, superseding an earlier "most critical"):
    **the most recent inspection is the current truth about a building.** A
    re-visit that downgrades a building now wins over the older, more
    alarming record — which is the whole point: the building was re-checked.

    Ordering, in priority order:
      1. `fecha_inspeccion` — the field truth, but DATE-ONLY, so it cannot
         separate the 61 of 77 real duplicate groups inspected the same day.
      2. `CreationDate` — the system's own submission timestamp, which is
         what actually distinguishes a re-submit from its original.
      3. Severity — a last-resort tiebreak ONLY for rows identical in both
         timestamps, so the pick stays deterministic across runs instead of
         depending on row order. It never overrides recency.
    """
    # format="mixed": the field arrives ISO ("2026-08-13") from the layer but
    # dd/mm from older exports; inferring per value beats guessing one.
    fecha = pd.to_datetime(row.get("fecha_inspeccion"), errors="coerce", format="mixed", dayfirst=True)
    creado = pd.to_datetime(row.get("CreationDate"), errors="coerce")
    return (
        fecha.value if pd.notna(fecha) else -1,
        creado.value if pd.notna(creado) else -1,
        1 if _es_si(row.get("colapso_total")) else 0,
        1 if _es_si(row.get("colapso_parcial")) else 0,
        _HAB_SEVERIDAD.get(str(row.get("criterio_habitabilidad")).strip().lower(), 0),
        _DANO_SEVERIDAD.get(str(row.get("nivel_dano")).strip().lower(), 0),
    )


# A conjunto residencial shares ONE street address across many buildings, so
# the address alone is not a building identity: grouping on it merged 7 towers
# of "KR 77 # 1C-140" (T1, T3, T10, T15, T19, T20 del Danubio) into a single
# building -- UNDER-counting, the opposite of the over-count this module fixes.
#
# Measured on the live data, the two cases separate with a wide margin:
#   accidental re-submit : name similarity 1.00, <= 13 m apart
#   different towers     : name similarity <= 0.67, >= 48 m apart
# Both thresholds sit in that gap, so neither is knife-edge.
_SIM_MISMO_EDIFICIO = 0.85
_DIST_MISMO_EDIFICIO_M = 30.0


def _norm_nombre(value) -> str:
    if pd.isna(value):
        return ""
    txt = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9 ]", " ", txt).strip()


def _nombres_coinciden(na: str, nb: str) -> bool:
    """Do two building names denote the same structure?

    Plain string similarity is not enough: "ASTURIAS" vs "CONJUNTO
    MULTIFAMILIAR ASTURIAS" is the same building on the live data but scores
    only 0.41, because one is four times longer. So a token-SUBSET check runs
    first — every word of the shorter name appearing in the longer one.

    The subset must be over WHOLE tokens, never a substring: "TORRE 1" is
    literally contained in "TORRE 15", and those are two different towers of
    the same complex. Comparing tokens keeps them apart ({TORRE,1} is not a
    subset of {TORRE,15}) where a substring check would have merged them.
    """
    # Numbers first, and they are DECISIVE: in a conjunto the number IS the
    # building's identity ("Torre 1" / "Torre 15", "T10" / "T19"). Fuzzy text
    # similarity actively betrays us here -- "TORRE 1" vs "TORRE 15" scores
    # 0.93 and would merge two different towers. When both names carry digits
    # and the digits differ, nothing else can make them the same structure.
    nums_a, nums_b = set(re.findall(r"\d+", na)), set(re.findall(r"\d+", nb))
    if nums_a and nums_b and nums_a != nums_b:
        return False

    ta, tb = set(na.split()), set(nb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= _SIM_MISMO_EDIFICIO


def _misma_edificacion(a: dict, b: dict) -> bool:
    """Two records at the SAME address: same building, or different towers?

    Requires BOTH signals to agree, because either alone is wrong in a real
    conjunto: names repeat across towers ("Bloque A"), and towers sit only
    tens of metres apart. When one signal is unavailable the other decides
    alone rather than guessing — an unknown must not manufacture a match.
    """
    na, nb = _norm_nombre(a.get("nombre_edificacion")), _norm_nombre(b.get("nombre_edificacion"))
    nombres_ok = _nombres_coinciden(na, nb) if na and nb else None  # None = at least one is blank

    dist = None
    if all(pd.notna(v) for v in (a.get("x"), a.get("y"), b.get("x"), b.get("y"))):
        dist = _haversine_m(float(a["y"]), float(a["x"]), float(b["y"]), float(b["x"]))
    dist_ok = dist <= _DIST_MISMO_EDIFICIO_M if dist is not None else None

    if nombres_ok is None and dist_ok is None:
        # Same address, nothing else to go on. Treat as the same building:
        # the address IS the only identity available, and splitting on no
        # evidence would re-inflate the very figures this module corrects.
        return True
    if nombres_ok is None:
        return bool(dist_ok)
    if dist_ok is None:
        return bool(nombres_ok)
    return bool(nombres_ok and dist_ok)


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _clave_direccion(row) -> str:
    """Coarse bucket: everything that COULD be the same building.

    `_misma_edificacion` then splits each bucket into actual buildings.
    """
    # pd.isna() first: a missing value arrives as NaN, and str(NaN) is the
    # TRUTHY string "nan" -- without this guard every address-less record
    # collapses into one bogus "dir:NAN" building.
    direccion_raw = row.get("direccion_norm")
    direccion = "" if pd.isna(direccion_raw) else str(direccion_raw).strip().upper()
    if direccion:
        return f"dir:{direccion}"
    x, y = row.get("x"), row.get("y")
    if pd.notna(x) and pd.notna(y):
        return f"geo:{round(float(y), 5)},{round(float(x), 5)}"
    return f"id:{row.get('GlobalID')}"


def _claves_por_edificio(df: pd.DataFrame) -> list[str]:
    """Address bucket -> one key per actual BUILDING inside it.

    Within a bucket, records are chained together transitively: A joins B's
    building if it matches B, even when it does not match every other member.
    That is deliberate — a tower photographed from two ends can be 30 m from
    the middle shot and 55 m from the far one, and demanding agreement with
    ALL members would split one real building into three.
    """
    claves: list[str] = []
    por_bucket: dict[str, list[tuple[int, dict]]] = {}
    filas = df.to_dict("records")

    for i, fila in enumerate(filas):
        por_bucket.setdefault(_clave_direccion(fila), []).append((i, fila))

    for bucket, miembros in por_bucket.items():
        # edificios: list of (representative_row, key) built as we walk.
        edificios: list[tuple[dict, str]] = []
        asignada: dict[int, str] = {}
        for idx, fila in miembros:
            for ref, clave in edificios:
                if _misma_edificacion(ref, fila):
                    asignada[idx] = clave
                    break
            else:
                clave = bucket if not edificios else f"{bucket}#{len(edificios) + 1}"
                edificios.append((fila, clave))
                asignada[idx] = clave
        for idx, _ in miembros:
            while len(claves) <= idx:
                claves.append("")
            claves[idx] = asignada[idx]

    # `claves` was filled by index, but buckets are walked out of order.
    ordenadas = [""] * len(filas)
    for bucket, miembros in por_bucket.items():
        for idx, _ in miembros:
            ordenadas[idx] = claves[idx]
    return ordenadas


def leer_representantes_fijados() -> dict:
    """Pins set by an operator from the Panel (`panel_representante`).

    Best-effort ON PURPOSE: if Firestore is unreachable or unconfigured this
    returns `{}` and the automatic recency rule decides every group. A data
    refresh must never fail because an optional override store was down —
    losing today's figures entirely is far worse than losing a handful of
    manual pins until the next run.
    """
    try:
        import json as _json
        import os as _os

        sa_raw = _os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        if not sa_raw:
            return {}
        from google.cloud import firestore
        from google.oauth2 import service_account

        info = _json.loads(sa_raw)
        creds = service_account.Credentials.from_service_account_info(info)
        db = firestore.Client(project=info.get("project_id"), credentials=creds)
        pins = {}
        for doc in db.collection("panel_representante").stream():
            gid = (doc.to_dict() or {}).get("global_id")
            if gid:
                pins[doc.id] = gid
        if pins:
            log.info("Representantes fijados a mano: %d.", len(pins))
        return pins
    except Exception as exc:  # noqa: BLE001 - best-effort, see docstring
        log.warning("No se pudieron leer los representantes fijados (%s); se usa la regla automática.", exc)
        return {}


def add_dup_group(df: pd.DataFrame, overrides: dict | None = None) -> pd.DataFrame:
    """Tag every row with `dup_grupo_id` / `dup_n` / `es_representante`.

    `overrides` maps `dup_grupo_id -> GlobalID` and PINS that record as the
    group's representative, beating the automatic recency rule. It exists
    because no automatic rule is right every time: the operator can look at
    a group and say "this is the one that counts". A pin whose record no
    longer exists is ignored and the rule applies instead — never leave a
    group with zero representatives, which would silently drop a whole
    building out of every figure.
    """
    if df.empty:
        for col, default in (("dup_grupo_id", ""), ("dup_n", 0), ("es_representante", True)):
            df[col] = default
        return df

    df = df.copy()
    df["dup_grupo_id"] = _claves_por_edificio(df)
    df["dup_n"] = df.groupby("dup_grupo_id")["dup_grupo_id"].transform("size")

    df["_orden"] = df.apply(_recencia, axis=1)
    # A pinned row sorts above everything else in its group; the automatic
    # ordering then decides the rest (and decides outright when no pin
    # applies). One sort, no special-casing downstream.
    pins = overrides or {}
    df["_pin"] = [
        1 if pins.get(grupo) is not None and str(pins.get(grupo)) == str(gid) else 0
        for grupo, gid in zip(df["dup_grupo_id"], df.get("GlobalID", pd.Series([None] * len(df))))
    ]

    # idxmax is undefined on tuple-valued columns; sort and take the first
    # row per group instead. mergesort keeps ties in input order (stable).
    ganadores = (
        df.assign(_pos=range(len(df)))
        .sort_values(["_pin", "_orden"], ascending=False, kind="mergesort")
        .groupby("dup_grupo_id", sort=False)["_pos"]
        .first()
    )
    df["es_representante"] = False
    df.loc[df.index[list(ganadores.values)], "es_representante"] = True
    return df.drop(columns=["_orden", "_pin"])


def normalize(rows_raw: pd.DataFrame) -> pd.DataFrame:
    """Apply the exact contract that used to build tabla_normalizada, straight on
    the live layer: rename columns, derive dates/comuna/barrio/id_edan/dirección."""
    df = rows_raw.drop(columns=[c for c in COLS_A_ELIMINAR if c in rows_raw.columns])
    df = df.rename(columns=RENAME_MAP)
    if "municipio" in df.columns:
        df["municipio"] = normalize_municipio(df["municipio"])
    df = add_date_fields(df)
    df = coerce_numeric(df)
    df = drop_pii(df)
    df = spatial_join(df)
    df = resolve_zona_interes(df)
    df = add_id_edan(df)
    df = add_address_norm(df)
    # AFTER add_address_norm: the grouping keys off `direccion_norm`.
    df = add_dup_group(df, overrides=leer_representantes_fijados())
    log.info(
        "Edificios: %d registros -> %d edificios unicos (%d duplicados agrupados).",
        len(df), int(df["es_representante"].sum()), len(df) - int(df["es_representante"].sum()),
    )
    return df


def add_revisar_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Tag each record with data-quality review cases (feature: Analista
    'Gestión de datos'). Adds `revisar` (bool) and `revisar_casos` (list of
    ``{"caso": str, "campos": [str, ...]}``) so the grid can surface the
    records an analyst should verify, and WHICH fields to look at. Rules are
    conservative — only genuinely contradictory or atypical values, measured
    against the live dataset (see the review-cases analysis 2026-08-26)."""
    def _si(v) -> bool:
        return str(v).strip().lower() in ("si", "sí", "true", "1", "x")

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _norm(v) -> str:
        return str(v).strip().lower() if v is not None else ""

    casos_por_fila: list[list[dict]] = []
    for _, r in df.iterrows():
        casos: list[dict] = []
        if _si(r.get("colapso_total")) and _si(r.get("colapso_parcial")):
            casos.append({"caso": "Colapso total y parcial simultáneos",
                          "campos": ["colapso_total", "colapso_parcial"]})
        n_pisos = _num(r.get("n_pisos"))
        if n_pisos is not None and (n_pisos > 60 or n_pisos <= 0):
            casos.append({"caso": "Número de pisos atípico", "campos": ["n_pisos"]})
        n_ocup = _num(r.get("n_ocupantes"))
        if n_ocup is not None and n_ocup > 2000:
            casos.append({"caso": "Número de ocupantes atípico", "campos": ["n_ocupantes"]})
        if _norm(r.get("nivel_dano")) == "alto" and _norm(r.get("criterio_habitabilidad")) == "h":
            casos.append({"caso": "Nivel de daño alto con criterio habitable",
                          "campos": ["nivel_dano", "criterio_habitabilidad"]})
        casos_por_fila.append(casos)

    df["revisar_casos"] = casos_por_fila
    df["revisar"] = [len(c) > 0 for c in casos_por_fila]
    return df


def add_colapso_resuelto(df: pd.DataFrame) -> pd.DataFrame:
    """Derived `colapso_resuelto` ('total' | 'parcial' | 'ninguno'): resolves the
    "colapso_total AND colapso_parcial both si" contradiction add_revisar_flags
    flags as "Colapso total y parcial simultáneos" (22/1142 live records, product
    owner call 2026-08-27: both si -> parcial). Purely additive — colapso_total
    and colapso_parcial stay exactly as submitted for audit; this just gives the
    panel one field to count collapse from without double-counting a record
    across both KPI cards."""
    def _si(v) -> bool:
        return str(v).strip().lower() in ("si", "sí", "true", "1", "x")

    def _resuelto(row) -> str:
        if _si(row.get("colapso_parcial")):
            return "parcial"
        if _si(row.get("colapso_total")):
            return "total"
        return "ninguno"

    df["colapso_resuelto"] = [_resuelto(r) for r in df.to_dict("records")]
    return df


# --- Orchestration ----------------------------------------------------------


def run_once(out_dir: Path) -> None:
    log.info("=== refresh_data run start (source=survey123, out=%s) ===", out_dir)

    # Fuente única: el layer público de Survey123 (sin Google Sheets). normalize()
    # aplica el mismo contrato que antes construía tabla_normalizada, así que todo
    # lo downstream es idéntico. dedup por GlobalID como guard de idempotencia.
    df = normalize(fetch_survey_raw())
    df = dedup_latest_by_globalid(df)
    log.info("Survey123: %d rows, %d columns.", len(df), len(df.columns))

    # ONE queryAttachments fetch for the whole run (was two: EXIF coords +
    # xlsx photo URLs each did their own ~5 MB call). Fail-soft: an empty list
    # here just means both downstream uses fall back to "no photos" gracefully.
    try:
        groups = _all_attachment_groups()
    except Exception as exc:  # noqa: BLE001 - fail-soft by design
        log.warning("Attachment groups unavailable (%s); no photo coords/URLs this run.", exc)
        groups = []

    # Photo-EXIF GPS centroids beat hand-placed form pins; every dashboard map
    # reads x/y, so correcting them here corrects every view at once.
    df = apply_photo_coords(df, groups)
    # Geocoded address as third opinion for photo centroids far from the form
    # pin — reverts to the form coordinate when the address sides with it.
    df = validate_photo_coords(df)
    # Geo-first "Barrio / vereda" resolution, AFTER every spatial_join() call
    # above (including the corrected/reverted re-joins) has settled barrio_geo.
    df = resolve_barrio_vereda(df)
    # Derived triage flag consumed by the dashboard and shipped in the xlsx.
    df = add_suspension_servicios(df)
    # Data-quality review flags for the Analista "Gestión de datos" grid.
    df = add_revisar_flags(df)
    # Resolves the colapso_total/colapso_parcial contradiction add_revisar_flags
    # above just flagged, so KPIs/map count collapse once per record.
    df = add_colapso_resuelto(df)

    # Direct photo download URLs for the xlsx export.
    photo_urls = fetch_photo_urls(groups)
    log.info("Photo URLs: %d records with photos for the xlsx.", len(photo_urls))
    if not groups:
        photo_urls = None

    inspections_path, meta_path, n = write_outputs(df, out_dir, "survey123", photo_urls)
    log.info("Wrote %s (%d records) and %s.", inspections_path, n, meta_path)
    log.info("=== refresh_data run complete ===")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the seismic dashboard data files.")
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
                run_once(args.out)
                if args.deploy:
                    deploy_to_vercel()
            except Exception:  # noqa: BLE001 - keep the loop alive across failures
                log.exception("Run failed; will retry on next loop iteration.")
            time.sleep(args.loop)
    else:
        run_once(args.out)
        if args.deploy:
            deploy_to_vercel()


if __name__ == "__main__":
    main()
