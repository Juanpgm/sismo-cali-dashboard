"""One-time script: build slim, frontend-ready basemap GeoJSON files.

Reads the two full-resolution basemaps under `basemaps/` and writes
simplified, minimal-property versions to `web/data/comunas.geojson` and
`web/data/barrios.geojson`.

Source property inspection (recorded here so the mapping is explicit):
  - comunas_corregimientos.geojson: properties = {comuna_corregimiento, area}
    -> name property is `comuna_corregimiento` (e.g. "COMUNA 06", "Pance").
    37 features, all names unique.
  - barrios_veredas.geojson: properties = {barrio_vereda, area}
    -> name property is `barrio_vereda` (e.g. "Chiminangos II").
    433 features; 10 names repeat (non-contiguous polygons of the same
    barrio/vereda) so `id` gets a numeric suffix to stay unique while `name`
    is left as-is — that's the value the frontend joins on.

Output feature shape: {"type": "Feature", "properties": {"id", "name"},
"geometry": <simplified, 5-decimal-rounded geometry>}.

`id`/`name` derivation is shared with `refresh_data.py` via
`basemap_utils.py` so the spatial-join output (`comuna`, `barrio_geo`)
matches these polygon names exactly.

Barrio-name repair: 76/433 names in barrios_veredas.geojson carry a U+FFFD
replacement character where an accented letter was lost in some earlier
export. This script repairs them (see `basemap_utils.repair_barrio_name`)
using, in confidence order: (a) the free-text `Barrio/vereda:` survey
answers in the local xlsx, (b) a small built-in dictionary of well-known
Spanish words / Cali toponyms, (c) a narrow intervocalic-'ñ' heuristic, and
(d) as a last resort, dropping the U+FFFD character so no replacement glyph
ever reaches web/data/barrios.geojson. `refresh_data.py` reads names from
*this script's output*, not from the raw basemap, so the repair only has to
happen once and the join keys are guaranteed to match by construction.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import shapely
from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from basemap_utils import (  # noqa: E402
    BARRIOS_FILE,
    COMUNAS_FILE,
    build_reference_tokens,
    get_barrio_name,
    get_comuna_name,
    repair_barrio_name,
    slugify,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "web" / "data"
LOCAL_XLSX = REPO_ROOT / "S123_042e021e34e349ddadf738270674dcc9_EXCEL.xlsx"
BARRIO_VEREDA_SURVEY_COLUMN = "Barrio/vereda:"

# Tuned so comunas stays roughly <=150KB and barrios <=600KB on disk.
COMUNAS_TOLERANCE = 0.0002
BARRIOS_TOLERANCE = 0.00005
COORD_DECIMALS = 5


def round_geometry(geom):
    return shapely.transform(geom, lambda coords: coords.round(COORD_DECIMALS))


def load_reference_tokens() -> list[str]:
    """Free-text words from the xlsx `Barrio/vereda:` survey column, used as
    the highest-confidence source for repairing corrupted polygon names."""
    if not LOCAL_XLSX.exists():
        print(f"WARNING: {LOCAL_XLSX} not found; skipping xlsx reference matching for name repair.")
        return []
    df = pd.read_excel(LOCAL_XLSX, usecols=[BARRIO_VEREDA_SURVEY_COLUMN])
    return build_reference_tokens(df[BARRIO_VEREDA_SURVEY_COLUMN].dropna().tolist())


def build_features(
    source_path: Path,
    name_getter,
    tolerance: float,
    reference_tokens: list[str] | None = None,
) -> tuple[list[dict], Counter]:
    with source_path.open(encoding="utf-8") as f:
        data = json.load(f)

    features = data["features"]
    names = [name_getter(ft["properties"]) for ft in features]
    slug_seen: Counter[str] = Counter()
    repair_stats: Counter = Counter()

    out_features = []
    for ft, raw_name in zip(features, names):
        name = raw_name
        if reference_tokens is not None and raw_name:
            name, source = repair_barrio_name(raw_name, reference_tokens)
            repair_stats[source] += 1
            if source == "fallback_stripped":
                print(f"  WARNING: no confident repair for {raw_name!r} -> fell back to {name!r}")

        base_slug = slugify(name or "unknown")
        slug_seen[base_slug] += 1
        feature_id = base_slug if slug_seen[base_slug] == 1 else f"{base_slug}-{slug_seen[base_slug]}"

        geom = shape(ft["geometry"])
        simplified = geom.simplify(tolerance, preserve_topology=True)
        simplified = round_geometry(simplified)

        out_features.append(
            {
                "type": "Feature",
                "properties": {"id": feature_id, "name": name},
                "geometry": mapping(simplified),
            }
        )

    return out_features, repair_stats


def write_geojson(features: list[dict], out_path: Path) -> int:
    payload = {"type": "FeatureCollection", "features": features}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    comunas_src = REPO_ROOT / COMUNAS_FILE
    barrios_src = REPO_ROOT / BARRIOS_FILE

    print(f"Reading {comunas_src} ...")
    comunas_features, _ = build_features(comunas_src, get_comuna_name, COMUNAS_TOLERANCE)
    comunas_size = write_geojson(comunas_features, OUT_DIR / "comunas.geojson")
    print(
        f"  -> {len(comunas_features)} features, "
        f"tolerance={COMUNAS_TOLERANCE}, size={comunas_size / 1024:.1f} KB "
        f"-> web/data/comunas.geojson"
    )

    print("Loading xlsx Barrio/vereda: survey values for name-repair reference ...")
    reference_tokens = load_reference_tokens()
    print(f"  -> {len(reference_tokens)} reference tokens loaded.")

    print(f"Reading {barrios_src} ...")
    barrios_features, repair_stats = build_features(
        barrios_src, get_barrio_name, BARRIOS_TOLERANCE, reference_tokens=reference_tokens
    )
    barrios_size = write_geojson(barrios_features, OUT_DIR / "barrios.geojson")
    print(
        f"  -> {len(barrios_features)} features, "
        f"tolerance={BARRIOS_TOLERANCE}, size={barrios_size / 1024:.1f} KB "
        f"-> web/data/barrios.geojson"
    )

    corrupted_total = sum(v for k, v in repair_stats.items() if k != "clean")
    print(
        f"  -> name repair: {corrupted_total} corrupted names found "
        f"(reference={repair_stats.get('reference', 0)}, "
        f"dictionary={repair_stats.get('dictionary', 0)}, "
        f"orthography={repair_stats.get('orthography', 0)}, "
        f"fallback_stripped={repair_stats.get('fallback_stripped', 0)}); "
        f"{repair_stats.get('clean', 0)} names were already clean."
    )

    if comunas_size > 150 * 1024:
        print(f"WARNING: comunas.geojson ({comunas_size / 1024:.1f} KB) exceeds the ~150KB target.")
    if barrios_size > 600 * 1024:
        print(f"WARNING: barrios.geojson ({barrios_size / 1024:.1f} KB) exceeds the ~600KB target.")


if __name__ == "__main__":
    main()
