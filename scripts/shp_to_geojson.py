"""Convert the barrios_veredas / comunas_corregimientos shapefiles in context/
into standalone GeoJSON files, ready for a later manual basemap swap.

- CRS: both .prj files already declare GCS_WGS_1984 (geographic WGS84,
  degrees) -- no reprojection math needed, coordinates pass through as-is.
  Per RFC 7946, GeoJSON's implicit CRS IS WGS84, so no "crs" member is
  written (a "crs" member is legacy/non-standard and most modern GIS/web
  tooling ignores or warns on it).
- Encoding: both .cpg files declare UTF-8. Read with encoding='utf-8' and
  write with ensure_ascii=False so accented characters (á, é, í, ó, ú, ñ,
  Á, É, ...) land as literal UTF-8 bytes in the output, not \\uXXXX escapes
  -- easy to eyeball-verify nothing got mangled.
- Geometry: pyshp's Shape.__geo_interface__ handles multipart polygons and
  ring winding (outer/hole) correctly, producing valid Polygon/MultiPolygon
  GeoJSON geometry without hand-rolled ring math.
- Attributes: EVERY .dbf field is carried over verbatim as a GeoJSON
  property (no renaming/subsetting) -- this is a faithful conversion, not
  a reshape into the app's current comunas.geojson/barrios.geojson schema.
  That reshape is a separate, later decision for whoever does the basemap
  swap.

Run: python scripts/shp_to_geojson.py
"""
import json
from pathlib import Path

import shapefile

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTEXT_DIR = REPO_ROOT / "context"

SOURCES = ["barrios_veredas", "comunas_corregimientos"]


def convert(name: str) -> None:
    shp_path = CONTEXT_DIR / name
    sf = shapefile.Reader(str(shp_path), encoding="utf-8")

    features = []
    for shape_rec in sf.iterShapeRecords():
        geometry = shape_rec.shape.__geo_interface__
        properties = shape_rec.record.as_dict()
        features.append({
            "type": "Feature",
            "properties": properties,
            "geometry": geometry,
        })

    fc = {"type": "FeatureCollection", "features": features}

    out_path = CONTEXT_DIR / f"{name}.geojson"
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(fc, f, ensure_ascii=False, indent=None, separators=(",", ":"))

    print(f"{name}: {len(features)} features -> {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    for source in SOURCES:
        convert(source)
