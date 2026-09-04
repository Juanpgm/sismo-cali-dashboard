"""One-time script: build the Santiago de Cali municipal boundary polygon.

Reads the 37 raw comuna/corregimiento polygons under `basemaps/`, dissolves
them into a single municipal boundary, and writes a slim, frontend-ready
GeoJSON to `web/data/cali_boundary.geojson`. Used by web/js/data.js
(isInsideCali, in utils.js) to exclude inspection records whose coordinates
fall outside Santiago de Cali from every part of the dashboard (KPIs,
charts, map, table, Excel export).

Source: `basemaps/comunas_corregimientos.geojson` -- the same 37-feature,
all-`Polygon` layer `prepare_basemaps.py` reads for comunas.geojson (see
that script's own docstring for the NOMBRE/CORREGIMIE property mapping).
This script does NOT touch prepare_basemaps.py or its outputs -- re-running
that script would regenerate other committed artifacts and create diff
noise unrelated to this change.

Union + hole handling: `shapely.ops.unary_union` of the 37 polygons yields a
single `Polygon` with 2 interior holes (~17.60 ha and ~13.47 ha). These are
NOT real exclaves inside Cali -- they are gaps in the source DIVIPOLA data
where two neighboring comuna/corregimiento polygons don't quite meet. A real
inspection point that happens to fall in one of these gaps is still
physically inside the city and must not be excluded, so this script keeps
ONLY the exterior ring (`Polygon(union.exterior)`), discarding the holes.

Simplification: `.simplify(1e-5, preserve_topology=True)` -- verified to
still exclude exactly the same 9 out-of-Cali records (out of the 1000 in the
committed web/data/inspections.json) as tolerance 0 and coarser tolerances
up to 5e-5, while keeping the output small (~78 KB, ~3358 vertices at
6-decimal rounding).

Output feature shape: {"type": "Feature", "properties": {"id", "name"},
"geometry": <single Polygon, exterior ring only, simplified, 6-decimal-
rounded, buffer(0)-repaired if simplify() left it invalid>}. Same
buffer(0) safety net prepare_basemaps.py uses: simplify()+rounding can
occasionally leave a self-touching ring even with preserve_topology=True.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from basemap_utils import COMUNAS_FILE  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "web" / "data" / "cali_boundary.geojson"

SIMPLIFY_TOLERANCE = 1e-5
COORD_DECIMALS = 6

BOUNDARY_ID = "santiago-de-cali"
BOUNDARY_NAME = "Santiago de Cali"


def round_geometry(geom):
    return shapely.transform(geom, lambda coords: coords.round(COORD_DECIMALS))


def build_boundary(source_path: Path):
    with source_path.open(encoding="utf-8") as f:
        data = json.load(f)

    geoms = [shape(ft["geometry"]) for ft in data["features"]]
    union = unary_union(geoms)

    # Keep only the exterior ring: the 2 interior holes left by unary_union
    # (see module docstring) are source-data gaps, not real exclaves -- a
    # point inside one of them is still inside Cali.
    if union.geom_type == "Polygon":
        exterior_only = shape({"type": "Polygon", "coordinates": [list(union.exterior.coords)]})
    else:
        # Defensive: unary_union of 37 mutually-adjacent-or-overlapping comuna/
        # corregimiento polygons has always produced a single Polygon in
        # practice (verified on the current basemap) -- a MultiPolygon would
        # mean the source data no longer forms one contiguous municipality.
        # Fail loudly rather than silently picking one part.
        raise ValueError(
            f"Expected the dissolved union to be a single Polygon, got {union.geom_type} -- "
            "the source basemap may no longer form one contiguous municipality."
        )

    simplified = exterior_only.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
    simplified = round_geometry(simplified)
    if not simplified.is_valid:
        print("  WARNING: simplified boundary is invalid -- repairing with buffer(0).")
        simplified = simplified.buffer(0)

    return simplified


def write_geojson(geom, out_path: Path) -> int:
    feature = {
        "type": "Feature",
        "properties": {"id": BOUNDARY_ID, "name": BOUNDARY_NAME},
        "geometry": mapping(geom),
    }
    payload = {"type": "FeatureCollection", "features": [feature]}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def main() -> None:
    source_path = REPO_ROOT / COMUNAS_FILE
    print(f"Reading {source_path} ...")
    boundary = build_boundary(source_path)

    n_vertices = len(boundary.exterior.coords)
    n_interior = len(boundary.interiors)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    size = write_geojson(boundary, OUT_PATH)

    print(
        f"  -> 1 feature, {n_vertices} vertices, {n_interior} interior ring(s), "
        f"tolerance={SIMPLIFY_TOLERANCE}, size={size / 1024:.1f} KB -> {OUT_PATH.relative_to(REPO_ROOT)}"
    )
    if n_interior:
        print(f"  WARNING: expected 0 interior rings (exterior-only by construction), got {n_interior}.")


if __name__ == "__main__":
    main()
