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
    "https://services6.arcgis.com/EF6OTqvE0RxR2jwj/arcgis/rest/services/"
    "service_d108cb3c79e242eabe99b458798936d1/FeatureServer/0"
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


def fetch_photo_coords() -> dict[int, dict]:
    """Per-record GPS centroid from the photo attachments' EXIF metadata.

    Everything comes from queryAttachments with returnMetadata=true — no image
    downloads. Attachments without a GPS IFD (signatures, GPS-less photos) are
    skipped. Returns {objectid: {lat, lon, n_fotos_gps, gps_error_m}}.
    """
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
        # An empty result is indistinguishable from a broken endpoint — treat
        # it as failure rather than silently marking every record photo-less.
        raise RuntimeError("queryAttachments returned 0 attachment groups.")

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


def apply_photo_coords(df: pd.DataFrame) -> pd.DataFrame:
    """Override the form x/y with the photo-EXIF GPS centroid where available,
    keeping the originals in x_form/y_form. Fail-soft: any attachments-endpoint
    failure logs a warning and publishes form coordinates unchanged — the
    refresh is never blocked by ArcGIS."""
    df["x_form"] = df["x"]
    df["y_form"] = df["y"]
    df["n_fotos_gps"] = 0
    df["gps_error_m"] = None
    df["desplazamiento_m"] = None
    has_xy = df["x"].notna() & df["y"].notna()
    df["coords_fuente"] = None
    df.loc[has_xy, "coords_fuente"] = "formulario"

    try:
        centroids = fetch_photo_coords()
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

    # Full dataset as xlsx for the dashboard's download button (same columns
    # as inspections.json). Optional: never block the JSON publish over it.
    try:
        df.to_excel(out_dir / "inspections.xlsx", index=False)
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

    # Same column order as the xlsx export: survey order, then x/y.
    df = df[list(LAYER_TO_RAW.values()) + ["x", "y"]]
    log.info("Fetched %d record(s) from the Survey123 layer.", len(df))
    return df


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
    df = add_id_edan(df)
    df = add_address_norm(df)
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

    # Photo-EXIF GPS centroids beat hand-placed form pins; every dashboard map
    # reads x/y, so correcting them here corrects every view at once.
    df = apply_photo_coords(df)
    # Geocoded address as third opinion for photo centroids far from the form
    # pin — reverts to the form coordinate when the address sides with it.
    df = validate_photo_coords(df)
    # Derived triage flag consumed by the dashboard and shipped in the xlsx.
    df = add_suspension_servicios(df)

    inspections_path, meta_path, n = write_outputs(df, out_dir, "survey123")
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
