"""tests/services/test_geocode.py (task 1.2) — design.md ADR-5 of the
`puntos-solicitados` change; spec puntos-solicitados/"Live geocoding with
manual fallback".

Pure unit tests against `app.services.geocode.geocode`, offline fixture
`http_get` doubles — no network, mirrors `scripts/geocode_validate.py`'s own
offline self-check idiom.
"""
from __future__ import annotations

import pytest

from app.services.geocode import GeocodeKeyError, geocode


def _payload(status: str, results: list[dict] | None = None, error_message: str | None = None) -> dict:
    out: dict = {"status": status, "results": results or []}
    if error_message is not None:
        out["error_message"] = error_message
    return out


def test_rooftop_inside_bbox_is_accepted():
    def fake_get(url, *, params, timeout):
        return _payload("OK", [{
            "geometry": {"location_type": "ROOFTOP", "location": {"lat": 3.42, "lng": -76.53}},
            "formatted_address": "Calle 1 # 2-3, Cali, Valle del Cauca, Colombia",
        }])

    r = geocode("CL 1 # 2-3", http_get=fake_get, api_key="fake")
    assert r["ok"] is True
    assert r["accepted"] is True
    assert r["lat"] == 3.42 and r["lng"] == -76.53
    assert r["location_type"] == "ROOFTOP"


def test_range_interpolated_inside_bbox_is_accepted():
    def fake_get(url, *, params, timeout):
        return _payload("OK", [{
            "geometry": {"location_type": "RANGE_INTERPOLATED", "location": {"lat": 3.42, "lng": -76.53}},
            "formatted_address": "Calle 1, Cali",
        }])

    r = geocode("CL 1", http_get=fake_get, api_key="fake")
    assert r["accepted"] is True


def test_approximate_precision_falls_back_with_reason():
    def fake_get(url, *, params, timeout):
        return _payload("OK", [{
            "geometry": {"location_type": "APPROXIMATE", "location": {"lat": 3.42, "lng": -76.53}},
        }])

    r = geocode("CL 1 # 2-3", http_get=fake_get, api_key="fake")
    assert r == {"ok": True, "accepted": False, "reason": "precision_insuficiente",
                "location_type": "APPROXIMATE"}


def test_outside_cali_bbox_falls_back_with_reason():
    def fake_get(url, *, params, timeout):
        return _payload("OK", [{
            "geometry": {"location_type": "ROOFTOP", "location": {"lat": 10.0, "lng": -76.53}},
            "formatted_address": "somewhere else",
        }])

    r = geocode("CL 1 # 2-3", http_get=fake_get, api_key="fake")
    assert r == {"ok": True, "accepted": False, "reason": "fuera_de_cali", "location_type": "ROOFTOP"}


def test_no_result_falls_back_with_reason():
    def fake_get(url, *, params, timeout):
        return _payload("ZERO_RESULTS")

    r = geocode("CL 1 # 2-3", http_get=fake_get, api_key="fake")
    assert r == {"ok": True, "accepted": False, "reason": "sin_resultado"}


def test_empty_direccion_never_calls_http_get():
    def fake_get(url, *, params, timeout):
        raise AssertionError("must not call Google for an empty address")

    r = geocode("", http_get=fake_get, api_key="fake")
    assert r == {"ok": True, "accepted": False, "reason": "sin_resultado"}


@pytest.mark.parametrize("status", ["REQUEST_DENIED", "OVER_QUERY_LIMIT", "INVALID_REQUEST"])
def test_key_or_quota_problem_raises_geocode_key_error(status):
    def fake_get(url, *, params, timeout):
        return _payload(status, error_message="bad key")

    with pytest.raises(GeocodeKeyError):
        geocode("CL 1 # 2-3", http_get=fake_get, api_key="fake")


def test_api_key_never_appears_in_the_result():
    def fake_get(url, *, params, timeout):
        assert params["key"] == "super-secret-key"
        return _payload("OK", [{
            "geometry": {"location_type": "ROOFTOP", "location": {"lat": 3.42, "lng": -76.53}},
            "formatted_address": "Calle 1, Cali",
        }])

    r = geocode("CL 1", http_get=fake_get, api_key="super-secret-key")
    assert "super-secret-key" not in str(r)
    assert "key" not in r
