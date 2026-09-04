"""build_cali_boundary -- the Santiago de Cali municipal boundary used to
exclude out-of-Cali inspection records from the whole dashboard (see
web/js/utils.js:isInsideCali). Checks the ACTUAL committed output of
scripts/build_cali_boundary.py: exactly 1 feature, a closed ring with no
interior holes, the expected id/name, and -- the regression test that
matters most -- that the two known-good real points sitting just outside the
SIMPLIFIED comunas.geojson (empty `comuna`, but genuinely inside Cali per the
RAW polygons) resolve INSIDE the generated boundary, while a real out-of-Cali
GPS error (latitude ~5.74, ~260 km north of Cali) resolves OUTSIDE.

This is the test that stops someone from reintroducing the empty-`comuna`
heuristic as the "outside Cali" signal (see the module docstring in
scripts/build_cali_boundary.py and web/js/data.js for the full story).
"""
import json
import sys
from pathlib import Path

from shapely.geometry import Point, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_cali_boundary as bcb  # noqa: E402

REPO_ROOT = bcb.REPO_ROOT
BOUNDARY_PATH = REPO_ROOT / "web" / "data" / "cali_boundary.geojson"


def _load_boundary():
    assert BOUNDARY_PATH.exists(), f"{BOUNDARY_PATH} missing -- run `python scripts/build_cali_boundary.py`"
    return json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))


def test_generated_boundary_has_one_feature_with_expected_properties():
    data = _load_boundary()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 1
    feature = data["features"][0]
    assert feature["properties"]["id"] == "santiago-de-cali"
    assert feature["properties"]["name"] == "Santiago de Cali"


def test_generated_boundary_ring_is_closed_with_no_holes():
    data = _load_boundary()
    geometry = data["features"][0]["geometry"]
    assert geometry["type"] == "Polygon"
    # Exactly one ring (exterior only) -- the 2 interior holes left by
    # unary_union (source-data gaps, not real exclaves) are deliberately
    # dropped by build_cali_boundary.build_boundary(). A record inside one of
    # those gaps must resolve as INSIDE Cali, which only holds if no hole
    # made it into the output.
    assert len(geometry["coordinates"]) == 1, "boundary must have no interior rings (holes)"
    ring = geometry["coordinates"][0]
    assert ring[0] == ring[-1], "exterior ring must be closed (first point repeated at the end)"
    assert len(ring) >= 4


def test_known_good_points_with_empty_comuna_are_inside():
    """ObjectID 685 ("Km18", -76.622000,3.514121) and 703 ("Ciudad melendez",
    -76.509090,3.367370) both have an empty `comuna` in inspections.json
    because refresh_data.py joins against the SIMPLIFIED comunas.geojson,
    whose simplification moved the border past them -- but both are covered
    by the RAW comuna/corregimiento polygons, i.e. genuinely inside Cali.
    Excluding on empty `comuna` would have thrown these away; the boundary
    generated from the union of the RAW polygons must not."""
    data = _load_boundary()
    boundary = shape(data["features"][0]["geometry"])

    km18 = Point(-76.622000, 3.514121)
    ciudad_melendez = Point(-76.509090, 3.367370)
    assert boundary.covers(km18), "ObjectID 685 (Km18) must be inside the generated Cali boundary"
    assert boundary.covers(ciudad_melendez), "ObjectID 703 (Ciudad melendez) must be inside the generated Cali boundary"


def test_gps_error_far_north_of_cali_is_outside():
    """A real out-of-Cali record shape: latitude ~5.74 instead of ~3.4 (roughly
    260 km north of the city) -- one of the 9 GPS entry errors this feature
    exists to exclude."""
    data = _load_boundary()
    boundary = shape(data["features"][0]["geometry"])

    gps_error = Point(-76.53978, 5.74574)
    assert not boundary.covers(gps_error), "a point at latitude ~5.74 must be outside the generated Cali boundary"
