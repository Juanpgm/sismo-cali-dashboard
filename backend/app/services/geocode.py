"""app/services/geocode.py — Nominatim-backed geocoding for the special-case-
points router's `POST /geocode` (design.md ADR-5 of the `puntos-solicitados`
change). Originally a Google Geocoding API port; switched to Nominatim
(OpenStreetMap, no API key) because `GOOGLE_MAPS_API_KEY` was never
configured live in Railway — see ADR-5's "supersedes" note.

Container-boundary port, NOT an import: the FastAPI image does not package
`scripts/`, so this module cannot import from there — the exact same
reasoning `scripts/geocode_validate.py`'s own module docstring already
documents for why IT cannot import `integracion_F1`. `scripts/
geocode_validate.py` itself is OUT OF SCOPE for this change — it is an
offline batch script, still on Google, deliberately untouched.

Pure: no Firestore access, no direct network call by default (an injectable
`http_get` makes this testable offline against fixture responses) — same
"pure function + injected side effect" shape `app/jobs/planeacion_cruce.py`
already uses for `_load_reportes`/`fetch_surveys`.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

GEOCODE_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy (operations.osmfoundation.org/policies/nominatim/)
# requires a descriptive User-Agent identifying the application — a generic/
# default requests UA risks a block. No established per-app contact email
# exists anywhere else in this codebase (only the personal SUPERADMIN_EMAIL),
# so this points at the deployed dashboard URL instead of inventing one.
USER_AGENT = "sismo-cali-dashboard/1.0 (+https://sismo-cali-dashboard.vercel.app)"

# Same bbox `scripts/geocode_validate.py` and the old Google implementation
# used — reused here, not re-derived, for the Nominatim `viewbox`/bounds
# check below.
CALI_BBOX = {"lat_min": 2.9, "lat_max": 4.1, "lon_min": -77.0, "lon_max": -76.0}

_EMPTY = {"", "-", "nan", "None"}
_ROAD_EXPANSIONS = [
    (re.compile(r'\b(?:KR|CRA|KRA|CR)\b\.?', re.IGNORECASE), 'Carrera'),
    (re.compile(r'\b(?:CL|CLL)\b\.?', re.IGNORECASE), 'Calle'),
    (re.compile(r'\bAV\b\.?', re.IGNORECASE), 'Avenida'),
    (re.compile(r'\bDG\b\.?', re.IGNORECASE), 'Diagonal'),
    (re.compile(r'\bTV\b\.?', re.IGNORECASE), 'Transversal'),
    (re.compile(r'\bNTE\b\.?', re.IGNORECASE), 'Norte'),
]
# Strips ANY trailing run of Cali/Valle del Cauca/Colombia tokens, not just a
# bare ", Cali" — an address typed as "..., Cali, Colombia" (a completely
# natural way to type one) used to survive this strip untouched and then get
# ", Cali, Valle del Cauca, Colombia" appended on top of it anyway, producing
# a duplicated query ("..., Cali, Colombia, Cali, Valle del Cauca, Colombia")
# that Nominatim resolves to zero results even for well-known landmarks.
_TRAILING_CALI = re.compile(
    r'(,\s*(Cali|Valle del Cauca|Colombia))+\s*$', re.IGNORECASE)


def to_nominatim_address(direccion: str | None) -> str:
    """Address normalization, unchanged from the old Google port (this part
    of the logic is provider-agnostic free-text query building, not a Google
    API detail)."""
    s = str(direccion or "").strip()
    if not s or s in _EMPTY:
        return ""
    s = _TRAILING_CALI.sub("", s.strip(" ,")).strip(" ,")
    for rx, word in _ROAD_EXPANSIONS:
        s = rx.sub(word, s)
    s = re.sub(r'\s+', ' ', s).strip(" ,")
    return s + ", Cali, Valle del Cauca, Colombia"


class GeocodeTransportError(RuntimeError):
    """Transport-level failure (timeout/connection error/bad HTTP status) or
    a malformed/non-JSON Nominatim response (not a list, missing lat/lon,
    etc). Nominatim has no API-key concept, so — unlike the old Google port
    — there is no separate "key/quota problem" error class: rate-limiting
    (HTTP 429) and any other non-2xx status fold into this same bucket. The
    router maps this to a clean 502, never an unhandled 500."""


def _default_http_get(url: str, *, params: dict[str, Any], timeout: int) -> Any:
    import requests  # deferred import — same convention as planeacion_cruce.py's _load_reportes

    try:
        response = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as exc:
        logging.exception("Geocoding API request failed")
        raise GeocodeTransportError("Geocoding API request failed") from exc
    except ValueError as exc:  # non-JSON body (json.JSONDecodeError is a ValueError)
        logging.exception("Geocoding API returned a non-JSON response")
        raise GeocodeTransportError("Geocoding API returned a non-JSON response") from exc


def _is_high_confidence(result: dict[str, Any]) -> bool:
    """Nominatim has no Google-style ROOFTOP/RANGE_INTERPOLATED precision
    tier — the closest equivalent is whether the match resolved to a known
    house number vs. a broad area (suburb/city/etc) or an unnumbered
    building. `class == 'building'` alone is NOT enough: OSM tags features
    of any size (a school, a mall, a full city block) as `building`, and
    its centroid can be hundreds of meters off — unlike Google's old
    ROOFTOP tier this replaced. A populated `address.house_number` is the
    real precision signal (Google's old RANGE_INTERPOLATED-equivalent:
    interpolated between two known addresses), so it is required
    regardless of class."""
    address = result.get("address") or {}
    return bool(address.get("house_number"))


def geocode(
    direccion: str | None,
    *,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """One geocoding attempt. Returns `{ok:true, accepted:true, lat, lng,
    formatted, location_type}` or `{ok:true, accepted:false, reason}`
    (`sin_resultado`|`precision_insuficiente`|`fuera_de_cali`). Raises
    `GeocodeTransportError` on a transport/malformed-response failure — the
    caller (the router) is responsible for turning that into a 502, never a
    200.

    `http_get(url, params=..., timeout=...) -> list` is injected so this is
    testable offline against fixture responses; defaults to a real
    `requests.get(...).json()` call against Nominatim."""
    address = to_nominatim_address(direccion)
    if not address:
        return {"ok": True, "accepted": False, "reason": "sin_resultado"}

    get = http_get or _default_http_get
    payload = get(
        GEOCODE_URL,
        params={
            "q": address,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 1,
            # west,north,east,south — Nominatim's viewbox order — built from
            # CALI_BBOX, never a second hardcoded copy of Cali's bounds.
            "viewbox": (f"{CALI_BBOX['lon_min']},{CALI_BBOX['lat_max']},"
                        f"{CALI_BBOX['lon_max']},{CALI_BBOX['lat_min']}"),
            "bounded": 1,
        },
        timeout=30,
    )
    if not isinstance(payload, list):
        logging.error("Malformed Geocoding API response: expected a list, got %r", type(payload).__name__)
        raise GeocodeTransportError("Malformed Geocoding API response")
    if not payload:
        return {"ok": True, "accepted": False, "reason": "sin_resultado"}

    try:
        result = payload[0]
        lat, lng = float(result["lat"]), float(result["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        logging.exception("Malformed Geocoding API response")
        raise GeocodeTransportError("Malformed Geocoding API response") from exc

    # Field kept as `location_type` for response-shape stability with the
    # old Google Geocoding integration this replaced, but it no longer holds
    # Google's ROOFTOP/RANGE_INTERPOLATED precision tier — it's Nominatim's
    # raw OSM `type` tag (e.g. "house", "yes", "suburb"), a different
    # vocabulary. No frontend consumer reads this field (grepped
    # web/js/*.js) — it's exposed for API completeness/debugging only.
    location_type = result.get("type", "")
    if not _is_high_confidence(result):
        return {"ok": True, "accepted": False, "reason": "precision_insuficiente",
                "location_type": location_type}
    if not (CALI_BBOX["lat_min"] <= lat <= CALI_BBOX["lat_max"]
            and CALI_BBOX["lon_min"] <= lng <= CALI_BBOX["lon_max"]):
        return {"ok": True, "accepted": False, "reason": "fuera_de_cali",
                "location_type": location_type}
    return {
        "ok": True,
        "accepted": True,
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "formatted": result.get("display_name", ""),
        "location_type": location_type,
    }


def _selfcheck() -> None:
    assert to_nominatim_address("KR 39 # 12-34, Barrio El Peñón") == (
        "Carrera 39 # 12-34, Barrio El Peñón, Cali, Valle del Cauca, Colombia")
    assert to_nominatim_address("") == ""
    assert to_nominatim_address(None) == ""
    # Regression: an address already ending in the city/country used to
    # survive _TRAILING_CALI untouched and get the suffix appended AGAIN,
    # producing a duplicated query Nominatim can't resolve (see this
    # module's _TRAILING_CALI comment).
    assert to_nominatim_address("Plaza de Caycedo, Cali, Colombia") == (
        "Plaza de Caycedo, Cali, Valle del Cauca, Colombia")
    assert to_nominatim_address("Avenida 6N con Calle 14, Cali, Valle del Cauca") == (
        "Avenida 6N con Calle 14, Cali, Valle del Cauca, Colombia")
    assert to_nominatim_address("Avenida 6N con Calle 14, Cali, Valle del Cauca, Colombia") == (
        "Avenida 6N con Calle 14, Cali, Valle del Cauca, Colombia")

    def _fake_building(url, *, params, timeout):
        return [{
            "lat": "3.42", "lon": "-76.53", "display_name": "Calle 1 # 2-3, Cali, Valle del Cauca, Colombia",
            "class": "building", "type": "yes", "address": {"house_number": "1-2"},
        }]

    r = geocode("CL 1 # 2-3", http_get=_fake_building)
    assert r == {"ok": True, "accepted": True, "lat": 3.42, "lng": -76.53,
                "formatted": "Calle 1 # 2-3, Cali, Valle del Cauca, Colombia",
                "location_type": "yes"}, r

    def _fake_suburb(url, *, params, timeout):
        return [{"lat": "3.42", "lon": "-76.53", "class": "place", "type": "suburb", "address": {}}]

    r = geocode("CL 1 # 2-3", http_get=_fake_suburb)
    assert r == {"ok": True, "accepted": False, "reason": "precision_insuficiente",
                "location_type": "suburb"}, r

    def _fake_outside_bbox(url, *, params, timeout):
        # Needs a house_number, else _is_high_confidence rejects it before
        # the bbox check this fixture exists to exercise ever runs.
        return [{"lat": "10.0", "lon": "-76.53", "class": "building", "type": "yes",
                 "display_name": "somewhere else", "address": {"house_number": "1"}}]

    r = geocode("CL 1 # 2-3", http_get=_fake_outside_bbox)
    assert r == {"ok": True, "accepted": False, "reason": "fuera_de_cali",
                "location_type": "yes"}, r

    def _fake_no_result(url, *, params, timeout):
        return []

    r = geocode("CL 1 # 2-3", http_get=_fake_no_result)
    assert r == {"ok": True, "accepted": False, "reason": "sin_resultado"}, r
    assert geocode("", http_get=_fake_no_result) == {
        "ok": True, "accepted": False, "reason": "sin_resultado"}

    def _fake_malformed(url, *, params, timeout):
        return [{"lat": "3.42", "class": "building", "type": "yes"}]  # no "lon"

    try:
        geocode("CL 1 # 2-3", http_get=_fake_malformed)
        raise AssertionError("expected GeocodeTransportError")
    except GeocodeTransportError:
        pass

    print("geocode self-check OK")


if __name__ == "__main__":
    _selfcheck()
