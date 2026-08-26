"""Ingest israel survey points into the sismo project's `inspecciones_israel`
collection — the LIVE source `app/jobs/planeacion_cruce.fetch_israel` reads
(feature D: a punto is "levantado" if it matches survey_cali OR israel).

Reads israel LIVE from its ArcGIS source (no static file, no dagma), projects
only the geo/address fields the cruce needs, and upserts them into
`sismo-agosto-sgred / inspecciones_israel` keyed by GlobalID. Credential:
`FIREBASE_SERVICE_ACCOUNT_JSON` — the SAME service account the backend uses
for the sismo project; the dagma-85aad project is NEVER touched here.

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
import urllib.parse
import urllib.request

# Source of truth for this URL is israel_to_cali.LAYER; duplicated here as a
# one-liner rather than importing that module (which pulls pandas) just for a
# constant. Keep in sync if the ArcGIS layer ever moves.
LAYER = ("https://services-eu1.arcgis.com/eeu6dGizBqA14mjm/ArcGIS/rest/services/"
         "service_c9a79f2605a4455582d81c12ec3ba2f3_form/FeatureServer/0")

COLLECTION = "inspecciones_israel"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def fetch_israel_points() -> list[dict]:
    """Live ArcGIS query → [{id, x, y, direccion}]. Mirrors
    israel_to_cali.fetch_raw's where/geometry, projecting only what the cruce
    matches on (globalid, geometry lon/lat, Building_Address)."""
    params = urllib.parse.urlencode({
        "where": "BldDetailedRate is not NULL",
        "outFields": "globalid,Building_Address",
        "returnGeometry": "true",
        "outSR": 4326,
        "f": "json",
    })
    with urllib.request.urlopen(f"{LAYER}/query?{params}", timeout=60) as resp:
        feats = json.load(resp).get("features") or []
    out = []
    for f in feats:
        attrs = f.get("attributes") or {}
        geom = f.get("geometry") or {}
        gid = attrs.get("globalid") or attrs.get("GlobalID")
        if not gid:
            continue  # no stable id -> can't key the doc; skip
        out.append({
            "id": str(gid),
            "x": geom.get("x"),
            "y": geom.get("y"),
            "direccion": attrs.get("Building_Address"),
        })
    return out


def _client():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise SystemExit("FIREBASE_SERVICE_ACCOUNT_JSON no está configurado (SA del proyecto sismo).")
    from google.cloud import firestore
    info = json.loads(raw)
    return firestore.Client.from_service_account_info(info, project=info.get("project_id"))


def main() -> None:
    points = fetch_israel_points()
    with_coords = sum(1 for p in points if p["x"] is not None and p["y"] is not None)
    print(f"israel (ArcGIS en vivo): {len(points)} puntos, {with_coords} con coordenadas")
    if "--dry" in sys.argv:
        print("(dry) sin escribir")
        return

    db = _client()
    col = db.collection(COLLECTION)
    batch = db.batch()
    written = 0
    for i, p in enumerate(points):
        batch.set(
            col.document(p["id"]),
            {"x": p["x"], "y": p["y"], "direccion": p["direccion"]},
            merge=True,
        )
        written += 1
        if (i + 1) % 400 == 0:  # Firestore batch cap
            batch.commit()
            batch = db.batch()
    batch.commit()
    print(f"upsert {written} docs -> sismo/{COLLECTION}")


if __name__ == "__main__":
    main()
