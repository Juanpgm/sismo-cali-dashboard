"""Sync inspections from raw_data into tabla_normalizada (upsert by GlobalID).

The dashboard's source of truth is the curated `tabla_normalizada` tab, which
`refresh_data.py` only reads (never rewrites). This script keeps that tab in
sync with the live `raw_data` form responses:

  * NEW inspection (GlobalID not in tabla_normalizada) -> normalized + APPENDED.
  * EDITED inspection (Survey123 edits appear in raw_data as a new row with the
    same GlobalID but a higher ObjectID) -> the existing row is UPDATED IN
    PLACE with the newest version.
  * UNCHANGED inspection (no newer ObjectID in raw_data) -> left untouched, so
    manual curation on those rows is preserved.

"Newest" is the row with the highest ObjectID for a GlobalID (ObjectID grows
with every submission/edit). id_edan is derived from GlobalID, so an edited row
keeps its id_edan; only its other fields change.

Flow:  form -> raw_data -> [this script: upsert] -> tabla_normalizada
       -> refresh_data.py (read-only) -> dashboard JSON.

Idempotent: re-running when nothing changed writes nothing. Run before
`refresh_data.py` in the same cron/trigger.

    python scripts/normalize_sync.py            # apply the upsert
    python scripts/normalize_sync.py --dry-run  # report new/edited; write nothing

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


def run(dry_run: bool = False) -> None:
    content, local_path, _ = acquire_xlsx("drive")
    source = io.BytesIO(content) if content is not None else local_path
    xls = pd.ExcelFile(source)
    for tab in (RAW_TAB, NORMALIZED_TAB):
        if tab not in xls.sheet_names:
            raise ValueError(f"'{tab}' tab not found (tabs: {xls.sheet_names}).")

    raw = pd.read_excel(xls, sheet_name=RAW_TAB)
    # Read WITHOUT dropna so DataFrame row i maps to sheet row i+2 (header + 1-based).
    norm = pd.read_excel(xls, sheet_name=NORMALIZED_TAB)
    target_cols = list(norm.columns)
    log.info("raw_data=%d filas, %s=%d filas.", len(raw), NORMALIZED_TAB, len(norm))

    if "GlobalID" not in raw.columns or "ObjectID" not in raw.columns:
        raise ValueError("raw_data must have GlobalID and ObjectID to sync by identity.")

    # Keep only the newest raw row per GlobalID (max ObjectID = latest edit).
    raw = raw[raw["GlobalID"].notna()].copy()
    raw["_oid"] = raw["ObjectID"].map(_int)
    raw_latest = raw.sort_values("_oid").drop_duplicates("GlobalID", keep="last")

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
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
