"""app/services/geocode.py — pure port of `scripts/geocode_validate.py`'s
acceptance logic, for the special-case-points router's `POST /geocode`
(design.md ADR-5 of the `puntos-solicitados` change).

Container-boundary port, NOT an import: the FastAPI image does not package
`scripts/`, so this module cannot import from there — the exact same
reasoning `scripts/geocode_validate.py`'s own module docstring already
documents for why IT cannot import `integracion_F1`. This is the SECOND
port of the same ~30 lines of address-normalization + acceptance logic, for
the same reason. Deliberately kept independent (not extracted to a shared
package both containers include) — see that module's docstring; this is a
documented, minimal duplication, not an oversight.

Pure: no Firestore access, no direct network call by default (an injectable
`http_get` makes this testable offline against fixture responses) — same
"pure function + injected side effect" shape `app/jobs/planeacion_cruce.py`
already uses for `_load_reportes`/`fetch_surveys`.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
API_KEY_ENV = "GOOGLE_MAPS_API_KEY"

# Verbatim from scripts/geocode_validate.py — same bbox, same accepted
# precision tiers, same reasoning (ROOFTOP/RANGE_INTERPOLATED only;
# GEOMETRIC_CENTER/APPROXIMATE are hundreds of meters off).
CALI_BBOX = {"lat_min": 2.9, "lat_max": 4.1, "lon_min": -77.0, "lon_max": -76.0}
ACCEPTED = {"ROOFTOP": 15.0, "RANGE_INTERPOLATED": 40.0}

# Google statuses that mean "key/quota problem", not "address rejected" —
# the router maps these to 502, never a 200 accepted:false.
KEY_PROBLEM_STATUSES = {"REQUEST_DENIED", "OVER_QUERY_LIMIT", "INVALID_REQUEST"}

_EMPTY = {"", "-", "nan", "None"}
_ROAD_EXPANSIONS = [
    (re.compile(r'\b(?:KR|CRA|KRA|CR)\b\.?', re.IGNORECASE), 'Carrera'),
    (re.compile(r'\b(?:CL|CLL)\b\.?', re.IGNORECASE), 'Calle'),
    (re.compile(r'\bAV\b\.?', re.IGNORECASE), 'Avenida'),
    (re.compile(r'\bDG\b\.?', re.IGNORECASE), 'Diagonal'),
    (re.compile(r'\bTV\b\.?', re.IGNORECASE), 'Transversal'),
    (re.compile(r'\bNTE\b\.?', re.IGNORECASE), 'Norte'),
]
_TRAILING_CALI = re.compile(r',\s*Cali\s*$', re.IGNORECASE)


def to_google_address(direccion: str | None) -> str:
    """Verbatim from `scripts/geocode_validate.py`."""
    s = str(direccion or "").strip()
    if not s or s in _EMPTY:
        return ""
    s = _TRAILING_CALI.sub("", s.strip(" ,")).strip(" ,")
    for rx, word in _ROAD_EXPANSIONS:
        s = rx.sub(word, s)
    s = re.sub(r'\s+', ' ', s).strip(" ,")
    return s + ", Cali, Valle del Cauca, Colombia"


class GeocodeKeyError(RuntimeError):
    """Google rejected the REQUEST itself (bad/quota-exhausted key), not the
    address. The router maps this to a 502 — never surfaced as an
    `accepted:false` address rejection."""


class GeocodeTransportError(RuntimeError):
    """Transport-level failure (timeout/connection error) or a malformed/
    non-JSON Google response (missing `results[0].geometry.location`, etc).
    The router maps this to the SAME clean 502 as `GeocodeKeyError` — never
    an unhandled 500."""


def _default_http_get(url: str, *, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    import requests  # deferred import — same convention as planeacion_cruce.py's _load_reportes

    try:
        return requests.get(url, params=params, timeout=timeout).json()
    except requests.exceptions.RequestException as exc:
        # Never interpolate str(exc) into the client-facing message: urllib3's
        # connection-error strings (e.g. MaxRetryError) can embed the full
        # request URL, which includes the `key=<GOOGLE_MAPS_API_KEY>` query
        # param. Log the real exception server-side only.
        logging.exception("Geocoding API request failed")
        raise GeocodeTransportError("Geocoding API request failed") from exc
    except ValueError as exc:  # non-JSON body (json.JSONDecodeError is a ValueError)
        logging.exception("Geocoding API returned a non-JSON response")
        raise GeocodeTransportError("Geocoding API returned a non-JSON response") from exc


def geocode(
    direccion: str | None,
    *,
    http_get: Callable[..., dict[str, Any]] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """One geocoding attempt. Returns `{ok:true, accepted:true, lat, lng,
    formatted, location_type}` or `{ok:true, accepted:false, reason}`
    (`sin_resultado`|`precision_insuficiente`|`fuera_de_cali`). Raises
    `GeocodeKeyError` on a key/quota-shaped Google status — the caller (the
    router) is responsible for turning that into a 502, never a 200.

    `http_get(url, params=..., timeout=...) -> dict` is injected so this is
    testable offline against fixture responses; defaults to a real
    `requests.get(...).json()` call. `api_key` defaults to
    `$GOOGLE_MAPS_API_KEY`, read here (not by the caller) so the key never
    has to travel through router code — it stays inside this one function
    and is never included in the return value."""
    address = to_google_address(direccion)
    if not address:
        return {"ok": True, "accepted": False, "reason": "sin_resultado"}

    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
    get = http_get or _default_http_get
    payload = get(
        GEOCODE_URL,
        params={
            "address": address,
            "components": "country:CO|locality:Cali",
            "bounds": (f"{CALI_BBOX['lat_min']},{CALI_BBOX['lon_min']}|"
                       f"{CALI_BBOX['lat_max']},{CALI_BBOX['lon_max']}"),
            "region": "co",
            "language": "es",
            "key": key,
        },
        timeout=30,
    )
    status = payload.get("status", "")
    if status in KEY_PROBLEM_STATUSES:
        raise GeocodeKeyError(f"Geocoding API said {status}: {payload.get('error_message', '')}")
    if status != "OK" or not payload.get("results"):
        return {"ok": True, "accepted": False, "reason": "sin_resultado"}

    try:
        result = payload["results"][0]
        loc_type = result["geometry"].get("location_type", "")
        if loc_type not in ACCEPTED:
            return {"ok": True, "accepted": False, "reason": "precision_insuficiente",
                    "location_type": loc_type}
        loc = result["geometry"]["location"]
        lat, lng = float(loc["lat"]), float(loc["lng"])
    except (KeyError, TypeError, ValueError) as exc:
        # Same rationale as _default_http_get: the response payload that
        # produced this error could itself echo back request params (incl.
        # the API key) in a malformed shape — keep the client-facing message
        # generic and log the real exception server-side only.
        logging.exception("Malformed Geocoding API response")
        raise GeocodeTransportError("Malformed Geocoding API response") from exc
    if not (CALI_BBOX["lat_min"] <= lat <= CALI_BBOX["lat_max"]
            and CALI_BBOX["lon_min"] <= lng <= CALI_BBOX["lon_max"]):
        return {"ok": True, "accepted": False, "reason": "fuera_de_cali",
                "location_type": loc_type}
    return {
        "ok": True,
        "accepted": True,
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "formatted": result.get("formatted_address", ""),
        "location_type": loc_type,
    }


def _selfcheck() -> None:
    assert to_google_address("KR 39 # 12-34, Barrio El Peñón") == (
        "Carrera 39 # 12-34, Barrio El Peñón, Cali, Valle del Cauca, Colombia")
    assert to_google_address("") == ""
    assert to_google_address(None) == ""

    def _fake_rooftop(url, *, params, timeout):
        return {"status": "OK", "results": [{
            "geometry": {"location_type": "ROOFTOP", "location": {"lat": 3.42, "lng": -76.53}},
            "formatted_address": "Calle 1 # 2-3, Cali, Valle del Cauca, Colombia",
        }]}

    r = geocode("CL 1 # 2-3", http_get=_fake_rooftop, api_key="fake")
    assert r == {"ok": True, "accepted": True, "lat": 3.42, "lng": -76.53,
                "formatted": "Calle 1 # 2-3, Cali, Valle del Cauca, Colombia",
                "location_type": "ROOFTOP"}, r

    def _fake_approximate(url, *, params, timeout):
        return {"status": "OK", "results": [{
            "geometry": {"location_type": "APPROXIMATE", "location": {"lat": 3.42, "lng": -76.53}},
        }]}

    r = geocode("CL 1 # 2-3", http_get=_fake_approximate, api_key="fake")
    assert r == {"ok": True, "accepted": False, "reason": "precision_insuficiente",
                "location_type": "APPROXIMATE"}, r

    def _fake_outside_bbox(url, *, params, timeout):
        return {"status": "OK", "results": [{
            "geometry": {"location_type": "ROOFTOP", "location": {"lat": 10.0, "lng": -76.53}},
            "formatted_address": "somewhere else",
        }]}

    r = geocode("CL 1 # 2-3", http_get=_fake_outside_bbox, api_key="fake")
    assert r == {"ok": True, "accepted": False, "reason": "fuera_de_cali",
                "location_type": "ROOFTOP"}, r

    def _fake_no_result(url, *, params, timeout):
        return {"status": "ZERO_RESULTS", "results": []}

    r = geocode("CL 1 # 2-3", http_get=_fake_no_result, api_key="fake")
    assert r == {"ok": True, "accepted": False, "reason": "sin_resultado"}, r
    assert geocode("", http_get=_fake_no_result, api_key="fake") == {
        "ok": True, "accepted": False, "reason": "sin_resultado"}

    def _fake_denied(url, *, params, timeout):
        return {"status": "REQUEST_DENIED", "error_message": "bad key"}

    try:
        geocode("CL 1 # 2-3", http_get=_fake_denied, api_key="fake")
        raise AssertionError("expected GeocodeKeyError")
    except GeocodeKeyError:
        pass

    def _fake_malformed(url, *, params, timeout):
        return {"status": "OK", "results": [{"geometry": {"location_type": "ROOFTOP"}}]}  # no "location"

    try:
        geocode("CL 1 # 2-3", http_get=_fake_malformed, api_key="fake")
        raise AssertionError("expected GeocodeTransportError")
    except GeocodeTransportError:
        pass

    print("geocode self-check OK")


if __name__ == "__main__":
    _selfcheck()
