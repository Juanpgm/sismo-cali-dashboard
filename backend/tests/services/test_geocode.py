"""tests/services/test_geocode.py (task 1.2) — design.md ADR-5 of the
`puntos-solicitados` change; spec puntos-solicitados/"Live geocoding with
manual fallback".

Pure unit tests against `app.services.geocode.geocode`, offline fixture
`http_get` doubles — no network, mirrors `scripts/geocode_validate.py`'s own
offline self-check idiom.
"""
from __future__ import annotations

import pytest

from app.services.geocode import GeocodeKeyError, GeocodeTransportError, geocode


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


# ── Transport/malformed-response failures map to GeocodeTransportError ─────


def test_malformed_response_missing_location_raises_transport_error():
    def fake_get(url, *, params, timeout):
        # "OK" status but no "location" under geometry — malformed shape.
        return _payload("OK", [{"geometry": {"location_type": "ROOFTOP"}}])

    with pytest.raises(GeocodeTransportError):
        geocode("CL 1 # 2-3", http_get=fake_get, api_key="fake")


def test_default_http_get_wraps_timeout_as_transport_error(monkeypatch):
    import requests

    from app.services import geocode as geocode_module

    def _raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(requests, "get", _raise_timeout)
    with pytest.raises(GeocodeTransportError):
        geocode_module._default_http_get(
            geocode_module.GEOCODE_URL, params={"key": "fake"}, timeout=30
        )


def test_default_http_get_wraps_connection_error_as_transport_error(monkeypatch):
    import requests

    from app.services import geocode as geocode_module

    def _raise_connection_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", _raise_connection_error)
    with pytest.raises(GeocodeTransportError):
        geocode_module._default_http_get(
            geocode_module.GEOCODE_URL, params={"key": "fake"}, timeout=30
        )


def test_transport_error_message_never_leaks_the_api_key(monkeypatch, caplog):
    """Regression: urllib3/requests connection-error strings (e.g.
    MaxRetryError.__str__) can embed the full request URL, including
    `key=<GOOGLE_MAPS_API_KEY>`. The exception raised out of
    `_default_http_get` — which becomes the client-facing 502 `detail` in
    the router — must be a fixed generic message, never str(exc). The real
    exception must still be logged server-side via logging.exception."""
    import logging

    import requests

    from app.services import geocode as geocode_module

    fake_key = "AIzaFAKE-SECRET-KEY-12345"

    def _raise_with_key_in_message(*args, **kwargs):
        raise requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='maps.googleapis.com', port=443): "
            f"Max retries exceeded with url: /maps/api/geocode/json?key={fake_key}&address=X"
        )

    monkeypatch.setattr(requests, "get", _raise_with_key_in_message)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(GeocodeTransportError) as exc_info:
            geocode_module._default_http_get(
                geocode_module.GEOCODE_URL, params={"key": fake_key}, timeout=30
            )

    assert fake_key not in str(exc_info.value)
    assert fake_key in caplog.text  # original exception still reaches server logs
