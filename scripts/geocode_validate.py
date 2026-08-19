"""Geocoding-based arbitration for suspicious photo-EXIF coordinates.

Self-contained minimal port of integracion_F1/integracion/geocode.py (the
publish container clones ONLY the dashboard repo, so nothing here may import
from integracion_F1 — same rationale as address_norm.py).

The Google Geocoding API result is an independent third opinion used ONLY to
arbitrate between two existing candidates (form pin vs photo-EXIF centroid)
when they disagree strongly. The geocode point is never published as the
coordinate itself. Precision-first: only ROOFTOP / RANGE_INTERPOLATED results
inside the Cali bbox are accepted; everything else (including "no result") is
a cached rejection so reruns are free and idempotent.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
API_KEY_ENV = "GOOGLE_MAPS_API_KEY"
CALI_BBOX = {"lat_min": 2.9, "lat_max": 4.1, "lon_min": -77.0, "lon_max": -76.0}
# Accepted precision (meters) per location_type; GEOMETRIC_CENTER/APPROXIMATE
# (street or neighbourhood centres) are rejected — hundreds of meters off.
ACCEPTED = {"ROOFTOP": 15.0, "RANGE_INTERPOLATED": 40.0}

_EMPTY = {"", "-", "nan", "None"}
# IGAC road codes (what address_norm.py emits) spelled back out — Google
# geocodes the full Spanish words far better than the codes.
_ROAD_EXPANSIONS = [
    (re.compile(r'\b(?:KR|CRA|KRA|CR)\b\.?', re.IGNORECASE), 'Carrera'),
    (re.compile(r'\b(?:CL|CLL)\b\.?', re.IGNORECASE), 'Calle'),
    (re.compile(r'\bAV\b\.?', re.IGNORECASE), 'Avenida'),
    (re.compile(r'\bDG\b\.?', re.IGNORECASE), 'Diagonal'),
    (re.compile(r'\bTV\b\.?', re.IGNORECASE), 'Transversal'),
    (re.compile(r'\bNTE\b\.?', re.IGNORECASE), 'Norte'),
]
_TRAILING_CALI = re.compile(r',\s*Cali\s*$', re.IGNORECASE)


def to_google_address(direccion_norm) -> str:
    """Normalized address -> the phrasing Google geocodes best: road codes
    spelled out and the query pinned to Cali, Valle del Cauca, Colombia."""
    s = str(direccion_norm or "").strip()
    if not s or s in _EMPTY:
        return ""
    s = _TRAILING_CALI.sub("", s.strip(" ,")).strip(" ,")
    for rx, word in _ROAD_EXPANSIONS:
        s = rx.sub(word, s)
    s = re.sub(r'\s+', ' ', s).strip(" ,")
    return s + ", Cali, Valle del Cauca, Colombia"


def cache_key(direccion_norm) -> str:
    """Deterministic cache key: case- and space-insensitive."""
    return re.sub(r'\s+', ' ', str(direccion_norm).strip()).upper()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


def closer_candidate(geo_lat: float, geo_lon: float,
                     form_lat: float, form_lon: float,
                     photo_lat: float, photo_lon: float) -> str:
    """Arbiter: which candidate the geocode point sides with.

    Returns "foto" or "formulario". Ties keep the photo (current) coordinate.
    """
    d_photo = haversine_m(geo_lat, geo_lon, photo_lat, photo_lon)
    d_form = haversine_m(geo_lat, geo_lon, form_lat, form_lon)
    return "foto" if d_photo <= d_form else "formulario"


def load_cache(path: Path) -> dict:
    """Merge-load the JSON cache; missing/corrupt file -> empty cache."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def geocode_address(address: str, session, api_key: str) -> dict:
    """One validated request. Returns a cacheable record — accepted or
    rejected-with-reason. Raises on API-level refusal (key problems must not
    be cached as address rejections)."""
    resp = session.get(GEOCODE_URL, params={
        "address": address,
        "components": "country:CO|locality:Cali",
        "bounds": (f"{CALI_BBOX['lat_min']},{CALI_BBOX['lon_min']}|"
                   f"{CALI_BBOX['lat_max']},{CALI_BBOX['lon_max']}"),
        "region": "co",
        "language": "es",
        "key": api_key,
    }, timeout=30)
    payload = resp.json()
    status = payload.get("status", "")
    if status in ("REQUEST_DENIED", "INVALID_REQUEST", "OVER_QUERY_LIMIT"):
        raise RuntimeError(f"Geocoding API said {status}: {payload.get('error_message', '')}")
    if status != "OK" or not payload.get("results"):
        return {"accepted": False, "reason": "sin_resultado"}

    result = payload["results"][0]
    loc_type = result["geometry"].get("location_type", "")
    if loc_type not in ACCEPTED:
        return {"accepted": False, "reason": "precision_insuficiente",
                "location_type": loc_type}
    loc = result["geometry"]["location"]
    lat, lon = float(loc["lat"]), float(loc["lng"])
    if not (CALI_BBOX["lat_min"] <= lat <= CALI_BBOX["lat_max"]
            and CALI_BBOX["lon_min"] <= lon <= CALI_BBOX["lon_max"]):
        return {"accepted": False, "reason": "fuera_de_cali",
                "location_type": loc_type}
    return {
        "accepted": True,
        "lat": round(lat, 6), "lon": round(lon, 6),
        "location_type": loc_type,
        "precision_m": ACCEPTED[loc_type],
        "formatted": result.get("formatted_address", ""),
    }


if __name__ == "__main__":
    # Offline self-check: address translation + arbiter logic (no network).
    a = to_google_address("KR 39 # 12-34, Barrio El Peñón")
    assert a == "Carrera 39 # 12-34, Barrio El Peñón, Cali, Valle del Cauca, Colombia", a
    a = to_google_address("CL 5 # 10-20, Cali")
    assert a == "Calle 5 # 10-20, Cali, Valle del Cauca, Colombia", a
    assert to_google_address("") == ""
    assert to_google_address(None) == ""

    geo = (3.4500, -76.5300)
    near = (3.4505, -76.5300)   # ~55 m from geo
    far = (3.4700, -76.5300)    # ~2.2 km from geo
    assert closer_candidate(*geo, form_lat=far[0], form_lon=far[1],
                            photo_lat=near[0], photo_lon=near[1]) == "foto"
    assert closer_candidate(*geo, form_lat=near[0], form_lon=near[1],
                            photo_lat=far[0], photo_lon=far[1]) == "formulario"
    # Tie keeps the photo coordinate.
    assert closer_candidate(*geo, form_lat=near[0], form_lon=near[1],
                            photo_lat=near[0], photo_lon=near[1]) == "foto"
    assert abs(haversine_m(3.45, -76.53, 3.45, -76.53)) == 0.0
    print("geocode_validate self-check OK")
