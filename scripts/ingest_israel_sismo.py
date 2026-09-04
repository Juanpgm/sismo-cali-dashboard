"""Ingest israel survey points into the sismo project's `inspecciones_israel`
collection — the LIVE source `app/jobs/planeacion_cruce.fetch_israel` reads
(feature D: a punto is "levantado" if it matches survey_cali OR israel) and
that `web/js/israel-source.js` also reads directly for the live map.

Reads israel LIVE from its ArcGIS source (no static file, no dagma) and
reuses `israel_to_cali` (repo root) to remap the FULL survey to Cali's
snake_case schema — colapso_total, nivel_dano, danos_estructura,
criterio_habitabilidad, criterio_color, comuna/barrio_geo, etc. — not just
geo/dirección, then upserts the COMPLETE remapped record per feature into
`sismo-agosto-sgred / inspecciones_israel`, keyed by the feature's raw
GlobalID (never the `isr-`-prefixed `ObjectID` that `remap` puts INSIDE the
document — that one is load-bearing for the dashboard's row key, not for
keying this doc). Uploading only geo/address left every damage/habitability
field empty on the dashboard even though `israel_to_cali` already knows how
to derive them from the same survey; this script now writes what it derives.
Credential: `FIREBASE_SERVICE_ACCOUNT_JSON` — the SAME service account the
backend uses for the sismo project; the dagma-85aad project is NEVER touched
here.

israel is a small, near-static set (~101 points), so this runs infrequently
(manual, or a low-frequency cron) — the cruce full-scans the collection every
run, so a single populate is enough until israel changes.

    FIREBASE_SERVICE_ACCOUNT_JSON=... python scripts/ingest_israel_sismo.py
    python scripts/ingest_israel_sismo.py --dry   # fetch + count, write nothing
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

from israel_to_cali import cali_schema, fetch_raw, fill_comuna_barrio, remap  # noqa: E402

COLLECTION = "inspecciones_israel"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _clean(v):
    """NaN/NaT -> None (Firestore rejects NaN unless allow_nan is set).
    Mirrors israel_to_cali.main()'s cleanup rule, broadened to also catch
    pandas' NaT sentinel (a bare isinstance(v, float) check misses it).
    Safe on scalars only — every value here is one DataFrame cell, never
    a list/array (pd.isna raises ambiguous-truth-value on those)."""
    return None if pd.isna(v) else v


def _row_gid(row: dict) -> str | None:
    """Raw feature id -> the Firestore doc id. ArcGIS normally serves this
    field as `globalid` (lowercase); `GlobalID` is a defensive fallback —
    same check the previous minimal fetch used. Never the remapped
    `ObjectID` (isr-N) inside the payload; that one keys the dashboard row,
    not this document."""
    for key in ("globalid", "GlobalID"):
        v = row.get(key)
        if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
            continue
        return str(v)
    return None


def fetch_israel_records() -> list[tuple[str, dict]]:
    """Live ArcGIS query -> [(globalid, full_cali_schema_record), ...].

    Reuses israel_to_cali's fetch_raw/remap/cali_schema/fill_comuna_barrio so
    every field it can derive (colapso, daño, habitabilidad, comuna,
    barrio_geo, ...) reaches Firestore, not just geo/dirección. Rows without
    a globalid/GlobalID are dropped — no stable id to key the doc."""
    raw = fetch_raw()
    schema = cali_schema()
    out = remap(raw, target_cols=schema)
    out = fill_comuna_barrio(out)

    records: list[tuple[str, dict]] = []
    for raw_row, out_row in zip(raw.to_dict("records"), out.to_dict("records"), strict=True):
        gid = _row_gid(raw_row)
        if gid is None:
            continue
        cleaned = {k: _clean(v) for k, v in out_row.items()}
        # FIELD_MAP only maps lowercase `globalid` -> `GlobalID`; a feature
        # served under the capital `GlobalID` fallback (see _row_gid) would
        # otherwise reach Firestore keyed correctly but with `GlobalID: None`
        # inside the doc. Always set it from the same id used as the doc key.
        cleaned["GlobalID"] = gid
        records.append((gid, cleaned))
    return records


def _client():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise SystemExit("FIREBASE_SERVICE_ACCOUNT_JSON no está configurado (SA del proyecto sismo).")
    from google.cloud import firestore
    info = json.loads(raw)
    return firestore.Client.from_service_account_info(info, project=info.get("project_id"))


def main() -> None:
    records = fetch_israel_records()
    with_coords = sum(1 for _, r in records if r.get("x") is not None and r.get("y") is not None)
    print(f"israel (ArcGIS en vivo): {len(records)} puntos, {with_coords} con coordenadas")
    if "--dry" in sys.argv:
        print("(dry) sin escribir")
        return

    db = _client()
    col = db.collection(COLLECTION)
    batch = db.batch()
    for i, (gid, data) in enumerate(records):
        batch.set(col.document(gid), data, merge=True)
        if (i + 1) % 400 == 0:  # Firestore batch cap
            batch.commit()
            batch = db.batch()
    batch.commit()
    print(f"upsert {len(records)} docs -> sismo/{COLLECTION}")


if __name__ == "__main__":
    main()
