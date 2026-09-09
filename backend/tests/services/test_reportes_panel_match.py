"""backend/tests/services/test_reportes_panel_match.py — RED-first tests for
the pure grid-indexed spatial join between atencionsismo citizen reports and
Panel (Survey123/EDE) points (see backend/app/services/reportes_panel_match.py
module docstring for the design rationale). No I/O — every fixture here is a
plain dict, same convention as test_stickers_atencionsismo.py.
"""
from __future__ import annotations

import math

from app.services import reportes_panel_match as m

R_EARTH_M = 6371000.0


def _point_north(lat0: float, lon0: float, dist_m: float) -> tuple[float, float]:
    """A (lat, lon) exactly `dist_m` meters north of (lat0, lon0) — pure
    latitude offset, so haversine_m's own formula collapses to
    `R * delta_lat_rad` exactly (no small-angle approximation error), making
    this safe for an exact-boundary (40.0 m vs 40.1 m) test."""
    dlat_rad = dist_m / R_EARTH_M
    return lat0 + math.degrees(dlat_rad), lon0


def _panel(registro_id, y, x, direccion=""):
    return {"registro_id": registro_id, "Y": y, "X": x, "DIRECCION": direccion}


def _reporte(rid, lat, lng, direccion=""):
    return {"id": rid, "lat": lat, "lng": lng, "direccion": direccion}


# ── _cell / build_grid ───────────────────────────────────────────────────────

def test_build_grid_buckets_points_by_cell():
    points = [_panel("A", 3.4200, -76.5300), _panel("B", 3.4200, -76.5301)]
    grid = m.build_grid(points)
    assert sum(len(v) for v in grid.values()) == 2


def test_build_grid_skips_points_with_missing_or_non_numeric_yx():
    points = [
        _panel("A", None, -76.5300),
        _panel("B", 3.4200, None),
        _panel("C", "no-es-numero", -76.5300),
        _panel("D", 3.4200, -76.5300),
    ]
    grid = m.build_grid(points)
    ids_in_grid = {p["registro_id"] for cell in grid.values() for p in cell}
    assert ids_in_grid == {"D"}


def test_build_grid_empty_input():
    assert m.build_grid([]) == {}


# ── nearest_in_grid: boundary + neighbor-cell scan ──────────────────────────

def test_nearest_in_grid_matches_at_exactly_40_meters():
    lat0, lon0 = 3.4200, -76.5300
    lat40, _ = _point_north(lat0, lon0, 40.0)
    grid = m.build_grid([_panel("P1", lat40, lon0)])
    best, dist = m.nearest_in_grid(lat0, lon0, grid)
    assert best is not None and best["registro_id"] == "P1"
    assert dist <= 40.0


def test_nearest_in_grid_does_not_match_at_40_point_1_meters():
    lat0, lon0 = 3.4200, -76.5300
    lat_over, _ = _point_north(lat0, lon0, 40.1)
    grid = m.build_grid([_panel("P1", lat_over, lon0)])
    best, dist = m.nearest_in_grid(lat0, lon0, grid)
    assert best is None and dist is None


def test_nearest_in_grid_scans_neighbor_cells_not_just_own_cell():
    # Straddles a grid-cell boundary (GRID_CELL_DEG = 0.001): the reporte and
    # the Panel point are ~22 m apart (well within MAX_MATCH_M=40) but land in
    # ADJACENT cells. A scan limited to the reporte's own cell would find
    # nothing here — this test fails under that (wrong) implementation.
    reporte_lat, lon = 3.0011, -76.0000   # cell index floor(3.0011/0.001) = 3001
    panel_lat = 3.0009                     # cell index floor(3.0009/0.001) = 3000
    assert m._cell(reporte_lat, lon)[0] != m._cell(panel_lat, lon)[0]  # sanity: different cells
    grid = m.build_grid([_panel("NEIGHBOR", panel_lat, lon)])
    best, dist = m.nearest_in_grid(reporte_lat, lon, grid)
    assert best is not None and best["registro_id"] == "NEIGHBOR"
    assert dist is not None and dist < 40.0


def test_nearest_in_grid_none_lat_lon_returns_none_none():
    grid = m.build_grid([_panel("P1", 3.42, -76.53)])
    assert m.nearest_in_grid(None, None, grid) == (None, None)
    assert m.nearest_in_grid(3.42, None, grid) == (None, None)
    assert m.nearest_in_grid(None, -76.53, grid) == (None, None)


def test_nearest_in_grid_empty_grid_returns_none_none():
    assert m.nearest_in_grid(3.42, -76.53, {}) == (None, None)


def test_nearest_in_grid_picks_the_closest_among_multiple_candidates():
    lat0, lon0 = 3.4200, -76.5300
    near_lat, _ = _point_north(lat0, lon0, 5.0)
    far_lat, _ = _point_north(lat0, lon0, 35.0)
    grid = m.build_grid([_panel("FAR", far_lat, lon0), _panel("NEAR", near_lat, lon0)])
    best, dist = m.nearest_in_grid(lat0, lon0, grid)
    assert best["registro_id"] == "NEAR"


# ── match_reporte: geo first, address fallback ──────────────────────────────

def test_match_reporte_geo_hit_returns_tier_alta():
    lat0, lon0 = 3.4200, -76.5300
    near_lat, _ = _point_north(lat0, lon0, 5.0)
    grid = m.build_grid([_panel("P1", near_lat, lon0, "Calle 9 # 1-1")])
    addr_index = []
    out = m.match_reporte(_reporte("r1", lat0, lon0, "otra direccion"), grid, addr_index)
    assert out == {"panel_registro_id": "P1", "tier": "alta", "dist_m": out["dist_m"]}
    assert out["dist_m"] is not None and out["dist_m"] < 40.0


def test_match_reporte_falls_back_to_address_when_no_geo_hit():
    from app.integracion.cruce_gestor import build_addr_index
    panel = [_panel("P2", 3.9999, -76.9999, "Calle 1 # 2-3")]
    grid = m.build_grid(panel)  # too far for geo match
    addr_index = build_addr_index(panel)
    out = m.match_reporte(_reporte("r2", 3.4200, -76.5300, "Calle 1 No. 2-3"), grid, addr_index)
    assert out is not None
    assert out["panel_registro_id"] == "P2"
    assert out["tier"] == "direccion"


def test_match_reporte_no_coords_and_no_direccion_returns_none():
    grid = m.build_grid([_panel("P1", 3.42, -76.53, "Calle 9 # 1-1")])
    out = m.match_reporte(_reporte("r3", None, None, ""), grid, [])
    assert out is None


def test_match_reporte_no_coords_falls_through_to_address_match():
    from app.integracion.cruce_gestor import build_addr_index
    panel = [_panel("P4", 3.42, -76.53, "Calle 1 # 2-3")]
    addr_index = build_addr_index(panel)
    grid = m.build_grid(panel)
    out = m.match_reporte(_reporte("r4", None, None, "Calle 1 No. 2-3"), grid, addr_index)
    assert out is not None and out["panel_registro_id"] == "P4" and out["tier"] == "direccion"


def test_match_reporte_no_match_at_all_returns_none():
    grid = m.build_grid([_panel("P1", 3.9999, -76.9999, "Otra direccion")])
    from app.integracion.cruce_gestor import build_addr_index
    addr_index = build_addr_index([_panel("P1", 3.9999, -76.9999, "Otra direccion")])
    out = m.match_reporte(_reporte("r5", 3.4200, -76.5300, "Calle nunca vista # 99-99"), grid, addr_index)
    assert out is None


def test_match_reporte_panel_point_missing_yx_still_reachable_by_address():
    from app.integracion.cruce_gestor import build_addr_index
    panel = [{"registro_id": "P5", "Y": None, "X": None, "DIRECCION": "Calle 1 # 2-3"}]
    grid = m.build_grid(panel)  # skipped (no numeric Y/X)
    addr_index = build_addr_index(panel)
    out = m.match_reporte(_reporte("r6", 3.4200, -76.5300, "Calle 1 No. 2-3"), grid, addr_index)
    assert out is not None
    assert out["panel_registro_id"] == "P5"
    assert out["tier"] == "direccion"
    assert out["dist_m"] is None  # neither side has coords to measure


# ── cross_reference: orchestrates grid + addr_index once, per-candidato ────

def test_cross_reference_returns_dict_keyed_by_reporte_id():
    lat0, lon0 = 3.4200, -76.5300
    near_lat, _ = _point_north(lat0, lon0, 5.0)
    panel = [_panel("P1", near_lat, lon0, "Calle 1 # 2-3")]
    candidatos = [_reporte("r1", lat0, lon0, "otra")]
    out = m.cross_reference(candidatos, panel)
    assert set(out.keys()) == {"r1"}
    assert out["r1"]["panel_registro_id"] == "P1"


def test_cross_reference_empty_candidatos():
    assert m.cross_reference([], [_panel("P1", 3.42, -76.53)]) == {}


def test_cross_reference_empty_panel_points():
    assert m.cross_reference([_reporte("r1", 3.42, -76.53, "x")], []) == {}


def test_cross_reference_skips_candidatos_missing_id():
    panel = [_panel("P1", 3.4200, -76.5300, "Calle 1 # 2-3")]
    candidatos = [{"lat": 3.4200, "lng": -76.5300, "direccion": "Calle 1 # 2-3"}, {"id": "", "lat": 3.42, "lng": -76.53}]
    out = m.cross_reference(candidatos, panel)
    assert out == {}


def test_cross_reference_only_includes_actual_matches():
    panel = [_panel("P1", 3.9999, -76.9999, "Nada que ver")]
    candidatos = [_reporte("r1", 3.4200, -76.5300, "Direccion desconocida # 1-1")]
    out = m.cross_reference(candidatos, panel)
    assert out == {}

