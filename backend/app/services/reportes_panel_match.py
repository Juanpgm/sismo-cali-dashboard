"""Pure grid-indexed spatial join between atencionsismo citizen reports
("reportes ciudadanos", web/data/reportes_ciudadanos.json rows) and Panel
points (Survey123/EDE field inspections, web/data/inspections.json). No I/O.

Rationale: these are two different systems with NO shared id — a citizen
report and a Panel inspection can only ever be linked by proximity (with an
address fallback for the rare case a report has coordinates too imprecise or
missing to geo-match). A naive full cross-product check (every reporte
against every Panel point — roughly 20k x 13k, hundreds of millions of
haversine calls) is unacceptable to run on every refresh. `build_grid`
buckets Panel points into ~111 m cells (`GRID_CELL_DEG`) so a candidate's
comparison is bounded to its own neighborhood (its cell + the 8 adjacent
ones) instead of the whole Panel — the 3x3 scan (not just the point's own
cell) is required because a report can sit a few meters from a Panel point
that happens to fall just across a cell boundary.

This module has NO notion of "already resolved" — every call here re-matches
every candidato it's given. Keeping the expensive part of this problem cheap
on every refresh is only HALF grid indexing; the other half — never handing
this module a reporte that was already matched in a previous run — is the
caller's job (see `app.services.reportes_panel_state.select_candidatos`).

Reuses `app.integracion.cruce_gestor`'s existing matching cascade
(`nearest`, `match_by_direccion`, `build_addr_index`, `MAX_MATCH_M`) rather
than reimplementing geo/address matching — the grid here only narrows WHICH
points that cascade ever sees.
"""
from __future__ import annotations

import math

from app.integracion.cruce_gestor import (
    MAX_MATCH_M,
    _eval_latlon,
    build_addr_index,
    match_by_direccion,
    nearest,
)

# ~111 m at Cali's latitude. MAX_MATCH_M (40.0) fits well inside one
# neighbor-ring scan (a point up to 40 m away can never be more than one grid
# cell away from its own cell, so the point's cell + its 8 neighbors always
# cover every Panel point within range).
GRID_CELL_DEG = 0.001


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / GRID_CELL_DEG), math.floor(lon / GRID_CELL_DEG))


def build_grid(panel_points: list[dict]) -> dict[tuple[int, int], list[dict]]:
    """Bucket Panel points (each with Y/X keys, cruce_gestor's convention) by
    grid cell. O(m), no pairwise comparison. A point with non-numeric or
    missing Y/X is skipped here — it's still reachable via the address
    fallback in `match_reporte`, just not through the grid."""
    grid: dict[tuple[int, int], list[dict]] = {}
    for p in panel_points:
        lat, lon = _num(p.get("Y")), _num(p.get("X"))
        if lat is None or lon is None:
            continue
        grid.setdefault(_cell(lat, lon), []).append(p)
    return grid


def nearest_in_grid(lat, lon, grid: dict[tuple[int, int], list[dict]]):
    """Nearest Panel point within MAX_MATCH_M, scanning ONLY the point's own
    cell + its 8 neighbors — never the whole grid. `(None, None)` when
    lat/lon are missing/non-numeric or nothing is in range."""
    lat_n, lon_n = _num(lat), _num(lon)
    if lat_n is None or lon_n is None:
        return None, None
    ci, cj = _cell(lat_n, lon_n)
    candidates: list[dict] = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            candidates.extend(grid.get((ci + di, cj + dj), []))
    if not candidates:
        return None, None
    return nearest(lat_n, lon_n, candidates, _eval_latlon, max_m=MAX_MATCH_M)


def match_reporte(reporte: dict, grid, addr_index) -> dict | None:
    """`reporte` has 'lat', 'lng', 'direccion'. Tries the geo cascade first
    (grid-bounded nearest); on a geo miss (or missing coords), falls back to
    `cruce_gestor.match_by_direccion`. Returns
    `{'panel_registro_id', 'tier', 'dist_m'}` or None. `tier` is 'alta' for a
    geo hit; otherwise whatever `via` `match_by_direccion` reports
    ('direccion' or 'combinado')."""
    lat, lon = reporte.get("lat"), reporte.get("lng")
    best, dist = nearest_in_grid(lat, lon, grid)
    if best is not None:
        return {
            "panel_registro_id": best.get("registro_id"),
            "tier": "alta",
            "dist_m": round(dist, 1) if dist is not None else None,
        }
    direccion = reporte.get("direccion")
    best2, via, dist2 = match_by_direccion(lat, lon, direccion, addr_index)
    if best2 is None:
        return None
    return {
        "panel_registro_id": best2.get("registro_id"),
        "tier": via,
        "dist_m": round(dist2, 1) if dist2 is not None else None,
    }


def cross_reference(candidatos: list[dict], panel_points_yx: list[dict]) -> dict[str, dict]:
    """`{reporte_id: match}` for every candidato (each with 'id', 'lat',
    'lng', 'direccion') that matched. `panel_points_yx`:
    `[{'registro_id', 'Y', 'X', 'DIRECCION'}, ...]`. Pure — builds the grid +
    address index ONCE here, no persisted state; incrementality (never
    re-matching an already-resolved reporte) is the CALLER's responsibility
    — only pass candidatos that aren't already resolved (see
    `reportes_panel_state.select_candidatos`). Skips candidatos missing an
    'id'."""
    grid = build_grid(panel_points_yx)
    addr_index = build_addr_index(panel_points_yx)
    out: dict[str, dict] = {}
    for c in candidatos:
        if not c:
            continue
        rid = c.get("id")
        if not rid:
            continue
        match = match_reporte(c, grid, addr_index)
        if match is not None:
            out[rid] = match
    return out
