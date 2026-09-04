"""resolve_zona_interes -- point-in-polygon resolution of the `zona_interes`
field against the HIDDEN zonas_interes basemap (Centro Historico / Avenida
6ta; see scripts/kml_to_zonas_interes.py). Unlike comuna/barrio_geo this
basemap is optional -- a missing web/data/zonas_interes.geojson must degrade
to `None` with a warning, never crash the refresh.

Fixture geometry: two simple, deterministic squares (NOT the real polygon
coordinates) so "well inside"/"on the boundary"/"outside" assertions are
exact instead of eyeballed against a 12/15-point real polygon.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402

CENTRO = "Centro Historico"
AVENIDA = "Avenida 6ta"

# Two disjoint unit squares, far enough apart that "outside both" is easy to
# pick. Ring left CLOSED here (first point repeated) -- same shape
# kml_to_zonas_interes.py emits.
FIXTURE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"id": "centro-historico", "name": CENTRO},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"id": "avenida-6ta", "name": AVENIDA},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[10.0, 10.0], [11.0, 10.0], [11.0, 11.0], [10.0, 11.0], [10.0, 10.0]]],
            },
        },
    ],
}


def _df(rows):
    return pd.DataFrame(rows)


@pytest.fixture
def zonas_geojson(tmp_path, monkeypatch):
    """Point rd.ZONAS_INTERES_GEOJSON at a temp fixture file instead of the
    real repo basemap, so these tests are independent of the actual polygon
    coordinates."""
    path = tmp_path / "zonas_interes.geojson"
    path.write_text(json.dumps(FIXTURE_GEOJSON), encoding="utf-8")
    monkeypatch.setattr(rd, "ZONAS_INTERES_GEOJSON", path)
    return path


def test_point_well_inside_centro_historico(zonas_geojson):
    df = rd.resolve_zona_interes(_df([{"x": 0.5, "y": 0.5}]))
    assert df.loc[0, "zona_interes"] == CENTRO


def test_point_well_inside_avenida_6ta(zonas_geojson):
    df = rd.resolve_zona_interes(_df([{"x": 10.5, "y": 10.5}]))
    assert df.loc[0, "zona_interes"] == AVENIDA


def test_point_clearly_outside_both_is_none(zonas_geojson):
    df = rd.resolve_zona_interes(_df([{"x": 5.0, "y": 5.0}]))
    assert df.loc[0, "zona_interes"] is None


def test_point_on_boundary_vertex_resolves_to_zone(zonas_geojson):
    """`covers()` (not `contains()`) is used precisely so a point exactly on
    a vertex or edge still resolves -- matches the client-side ray-casting
    port in web/js/utils.js, which treats the boundary as inside too."""
    # Exact vertex.
    df = rd.resolve_zona_interes(_df([{"x": 0.0, "y": 0.0}]))
    assert df.loc[0, "zona_interes"] == CENTRO
    # Exact mid-edge point (not a vertex).
    df = rd.resolve_zona_interes(_df([{"x": 0.5, "y": 0.0}]))
    assert df.loc[0, "zona_interes"] == CENTRO


def test_nan_none_xy_resolves_to_none_without_crashing(zonas_geojson):
    df = rd.resolve_zona_interes(_df([
        {"x": float("nan"), "y": 0.5},
        {"x": 0.5, "y": None},
        {"x": None, "y": None},
    ]))
    assert df["zona_interes"].isna().all()


def test_swapped_or_out_of_cali_bbox_coords_resolve_to_none(zonas_geojson):
    """resolve_zona_interes does no bbox sanity-check of its own -- it must
    simply not match (and not crash) for coordinates far outside every
    fixture polygon, including a lat/lon-swapped point."""
    df = rd.resolve_zona_interes(_df([
        {"x": 0.5, "y": 500.5},  # swapped-looking / absurd lat
        {"x": -76.5, "y": 3.45},  # real Cali-range coords, outside the tiny fixture squares
    ]))
    assert df.loc[0, "zona_interes"] is None
    assert df.loc[1, "zona_interes"] is None


def test_empty_dataframe_does_not_crash(zonas_geojson):
    df = rd.resolve_zona_interes(_df([]))
    assert df.empty
    assert "zona_interes" in df.columns


def test_missing_geojson_file_warns_and_sets_none(tmp_path, monkeypatch, caplog):
    """Graceful-degradation guard: zonas_interes.geojson is OPTIONAL (unlike
    comunas/barrios), so a missing file must warn and leave the column as
    None instead of raising."""
    monkeypatch.setattr(rd, "ZONAS_INTERES_GEOJSON", tmp_path / "does-not-exist.geojson")
    with caplog.at_level("WARNING"):
        df = rd.resolve_zona_interes(_df([{"x": 0.5, "y": 0.5}]))
    assert df.loc[0, "zona_interes"] is None
    assert any("zonas_interes" in rec.message or "zona_interes" in rec.message for rec in caplog.records)


def test_missing_geojson_file_does_not_clobber_preexisting_values(tmp_path, monkeypatch):
    """The guard must bail out BEFORE touching any row: a DataFrame that
    already carries a zona_interes value (e.g. re-processed output) keeps it
    when the basemap file is absent, rather than being reset to None."""
    monkeypatch.setattr(rd, "ZONAS_INTERES_GEOJSON", tmp_path / "does-not-exist.geojson")
    df = rd.resolve_zona_interes(_df([
        {"x": 0.5, "y": 0.5, "zona_interes": CENTRO},
    ]))
    assert df.loc[0, "zona_interes"] == CENTRO


def test_generated_zonas_interes_geojson_has_two_closed_named_polygons():
    """Sanity-check the ACTUAL committed output of
    scripts/kml_to_zonas_interes.py (not the fixture above): exactly 2
    features, canonical names with the "Poligono N - " prefix stripped, and
    closed rings (first coordinate == last)."""
    path = rd.REPO_ROOT / "web" / "data" / "zonas_interes.geojson"
    assert path.exists(), f"{path} missing -- run `python scripts/kml_to_zonas_interes.py`"
    data = json.loads(path.read_text(encoding="utf-8"))
    features = data["features"]
    assert len(features) == 2
    names = {ft["properties"]["name"] for ft in features}
    assert names == {"Centro Histórico", "Avenida 6ta"}
    for ft in features:
        assert ft["geometry"]["type"] == "Polygon"
        ring = ft["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1], f"ring for {ft['properties']['name']!r} is not closed"
        assert len(ring) >= 4


def test_mask_only_recomputes_selected_rows(zonas_geojson):
    """Mirrors spatial_join's `mask` contract: only the masked rows are
    (re)joined, every other row keeps its existing zona_interes untouched --
    used when a photo-EXIF/geocode correction moves just a few points."""
    df = _df([
        {"x": 0.5, "y": 0.5, "zona_interes": "STALE_SHOULD_STAY"},
        {"x": 10.5, "y": 10.5, "zona_interes": "STALE_SHOULD_BE_OVERWRITTEN"},
    ])
    mask = pd.Series([False, True], index=df.index)
    out = rd.resolve_zona_interes(df, mask=mask)
    assert out.loc[0, "zona_interes"] == "STALE_SHOULD_STAY"
    assert out.loc[1, "zona_interes"] == AVENIDA
