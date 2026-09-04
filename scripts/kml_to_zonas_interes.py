"""One-time/idempotent script: convert `basemaps/zonas_interes.kml` into the
slim, frontend-ready `web/data/zonas_interes.geojson` used for the server-
and client-side point-in-polygon resolution of the `zona_interes` field (see
`resolve_zona_interes` in `refresh_data.py` and `resolveZonaInteres` in
`web/js/utils.js`), and also fetched (on demand) and drawn by
`web/js/mapview.js` as an optional, user-toggleable overlay layer on the
panel map — hidden by default, independent of the Puntos/Calor/Coroplético
map mode.

Source shape (verified against the committed KML): exactly two `Placemark`
elements, each a simple `Polygon/outerBoundaryIs/LinearRing/coordinates` with
no folders, no description CDATA and no inner boundaries (holes) —
`<name>Polígono 1 - Centro Histórico</name>` / `<name>Polígono 2 - Avenida
6ta</name>`, coordinates as whitespace-separated `lon,lat,alt` triples. The
`Polígono N - ` prefix is stripped so the UI shows the canonical zone name
alone ('Centro Histórico', 'Avenida 6ta').

Parsing mirrors `integracion_F1/asignar_f3.py`'s `parse_zonas_kml` (same KML
namespace handling, same `outerBoundaryIs/LinearRing/coordinates` path), but
that script's Folder-based zone-of-assignment concept does not apply here —
this KML has no folders at all, just two bare Placemarks directly under
Document.

Output feature shape matches `scripts/prepare_basemaps.py`'s `build_features`
output: `{"type": "Feature", "properties": {"id", "name"}, "geometry": {...}}`,
so `refresh_data.load_prepared_polygons` (shared by `spatial_join` and
`resolve_zona_interes`) can read it unchanged.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from basemap_utils import slugify  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
KML_PATH = REPO_ROOT / "basemaps" / "zonas_interes.kml"
OUT_PATH = REPO_ROOT / "web" / "data" / "zonas_interes.geojson"

KML_NS = "{http://www.opengis.net/kml/2.2}"

# Strips the "Polígono N - " prefix so the UI/filter shows only the canonical
# zone name, e.g. "Polígono 1 - Centro Histórico" -> "Centro Histórico".
POLYGON_PREFIX_RE = re.compile(r"^Pol[íi]gono\s*\d+\s*-\s*", re.IGNORECASE)


def canonical_zone_name(raw_name: str) -> str:
    return POLYGON_PREFIX_RE.sub("", raw_name or "").strip()


def parse_ring(coordinates_text: str) -> list[list[float]]:
    """'lon,lat,alt lon,lat,alt ...' -> [[lon, lat], ...], altitude dropped,
    ring closed (first point repeated at the end) if it wasn't already."""
    ring: list[list[float]] = []
    for token in (coordinates_text or "").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        lon, lat = float(parts[0]), float(parts[1])
        ring.append([lon, lat])
    if ring and ring[0] != ring[-1]:
        ring.append(list(ring[0]))
    return ring


def build_features(kml_path: Path) -> list[dict]:
    root = ElementTree.parse(kml_path).getroot()
    features = []
    slug_seen: dict[str, int] = {}
    for pm in root.iter(f"{KML_NS}Placemark"):
        raw_name = (pm.findtext(f"{KML_NS}name") or "").strip()
        name = canonical_zone_name(raw_name)
        ring_el = pm.find(
            f"{KML_NS}Polygon/{KML_NS}outerBoundaryIs/{KML_NS}LinearRing/{KML_NS}coordinates"
        )
        if not name or ring_el is None or not (ring_el.text or "").strip():
            print(f"  WARNING: skipping Placemark with name={raw_name!r} — missing name/ring.")
            continue
        ring = parse_ring(ring_el.text)
        if len(ring) < 4:  # 3 distinct points + closing repeat
            print(f"  WARNING: skipping {name!r} — ring has fewer than 3 distinct points.")
            continue

        base_slug = slugify(name)
        slug_seen[base_slug] = slug_seen.get(base_slug, 0) + 1
        feature_id = base_slug if slug_seen[base_slug] == 1 else f"{base_slug}-{slug_seen[base_slug]}"

        features.append(
            {
                "type": "Feature",
                "properties": {"id": feature_id, "name": name},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    return features


def write_geojson(features: list[dict], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": features}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def main() -> None:
    if not KML_PATH.exists():
        raise FileNotFoundError(f"{KML_PATH} not found.")
    print(f"Reading {KML_PATH} ...")
    features = build_features(KML_PATH)
    size = write_geojson(features, OUT_PATH)
    print(f"  -> {len(features)} feature(s), size={size} bytes -> {OUT_PATH.relative_to(REPO_ROOT)}")
    for ft in features:
        print(f"     - {ft['properties']['id']}: {ft['properties']['name']!r} "
              f"({len(ft['geometry']['coordinates'][0])} points)")


if __name__ == "__main__":
    main()
