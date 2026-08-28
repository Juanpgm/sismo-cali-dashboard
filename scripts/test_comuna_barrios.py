"""Self-check for build_comuna_barrios_catalog. Run: python scripts/test_comuna_barrios.py

Verifies the comuna/corregimiento -> [barrio/vereda name, ...] catalog built
from basemaps/barrios_veredas.geojson: urban rows keyed by the same
"COMUNA NN" form comunas.geojson uses (get_comuna_name), rural rows keyed by
their corregimiento name, values sorted and deduplicated per key.
"""
from pathlib import Path

from prepare_basemaps import build_comuna_barrios_catalog

REPO_ROOT = Path(__file__).resolve().parent.parent
BARRIOS_SRC = REPO_ROOT / "basemaps" / "barrios_veredas.geojson"

catalog = build_comuna_barrios_catalog(BARRIOS_SRC, reference_tokens=[])

# Urban comuna key form matches comunas.geojson's own "COMUNA NN" naming.
assert "COMUNA 01" in catalog, sorted(catalog.keys())
assert "Vista Hermosa" in catalog["COMUNA 01"], catalog["COMUNA 01"]

# Rural rows key by corregimiento name (no "COMUNA" numeric code on them).
assert "Los Andes" in catalog, sorted(catalog.keys())
assert "El Pinar" in catalog["La Castilla"], catalog.get("La Castilla")

# Values sorted, deduplicated, no blanks.
for key, barrios in catalog.items():
    assert barrios == sorted(barrios), f"{key} not sorted: {barrios}"
    assert len(barrios) == len(set(barrios)), f"{key} has duplicates: {barrios}"
    assert all(b and b.strip() for b in barrios), f"{key} has a blank entry: {barrios}"
    assert key and key.strip(), "blank catalog key"

# 22 urban comunas + rural corregimientos, same universe as comunas.geojson's
# 37 mutually-exclusive features (see prepare_basemaps.py's module docstring).
assert len(catalog) == 37, f"expected 37 comuna/corregimiento keys, got {len(catalog)}: {sorted(catalog.keys())}"

print(f"scripts/test_comuna_barrios.py OK — {len(catalog)} comunas/corregimientos, "
      f"{sum(len(v) for v in catalog.values())} barrio/vereda names")
