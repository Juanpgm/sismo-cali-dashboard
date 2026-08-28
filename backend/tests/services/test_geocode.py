"""tests/services/test_geocode.py (task 1.2) — design.md ADR-5 of the
`puntos-solicitados` change (Nominatim revision); spec puntos-solicitados/
"Live geocoding with manual fallback".

Pure unit tests against `app.services.geocode.geocode`, offline fixture
`http_get` doubles — no network, mirrors `scripts/geocode_validate.py`'s own
offline self-check idiom. Fixtures use Nominatim's response shape: a JSON
ARRAY of results (not Google's `{status, results:[...]}` object), `lat`/`lon`
as STRINGS, `class`/`type`/`address` instead of `geometry.location_type`.
"""
from __future__ import annotations

from app.services.geocode import GeocodeTransportError, geocode


def _result(lat: str, lon: str, *, cls: str = "building", typ: str = "yes",
            house_number: str | None = "12-34", display_name: str = "Calle 1 # 2-3, Cali") -> dict:
    address: dict = {}
    if house_number is not None:
        address["house_number"] = house_number
    return {
        "lat": lat, "lon": lon, "display_name": display_name,
        "class": cls, "type": typ, "importance": 0.5,
        "boundingbox": [lat, lat, lon, lon], "address": address,
    }


def test_building_match_inside_bbox_is_accepted():
    def fake_get(url, *, params, timeout):
        return [_result("3.42", "-76.53")]

    r = geocode("CL 1 # 2-3", http_get=fake_get)
    assert r["ok"] is True
    assert r["accepted"] is True
    assert r["lat"] == 3.42 and r["lng"] == -76.53
    assert r["formatted"] == "Calle 1 # 2-3, Cali"
    assert r["location_type"] == "yes"


def test_building_class_without_house_number_is_not_accepted():
    """OSM tags features of any size (a school, a mall, a full city block)
    as `class=='building'` — its centroid can be hundreds of meters off.
    `class=='building'` alone, with no `house_number`, must fall back to
    `accepted:false` (draggable-marker fallback), not be silently accepted."""
    def fake_get(url, *, params, timeout):
        return [_result("3.42", "-76.53", cls="building", typ="yes", house_number=None)]

    r = geocode("Colegio San Antonio", http_get=fake_get)
    assert r == {"ok": True, "accepted": False, "reason": "precision_insuficiente",
                "location_type": "yes"}


def test_house_number_present_is_accepted_even_off_building_class():
    """Interpolated house-number matches (Nominatim's equivalent of Google's
    RANGE_INTERPOLATED tier) come back with a non-'building' class but a
    populated `address.house_number` — still a specific, addressable match."""
    def fake_get(url, *, params, timeout):
        return [_result("3.42", "-76.53", cls="place", typ="house", house_number="1-20")]

    r = geocode("CL 1", http_get=fake_get)
    assert r["accepted"] is True


def test_broad_area_match_falls_back_with_reason():
    def fake_get(url, *, params, timeout):
        return [_result("3.42", "-76.53", cls="place", typ="suburb", house_number=None)]

    r = geocode("San Antonio", http_get=fake_get)
    assert r == {"ok": True, "accepted": False, "reason": "precision_insuficiente",
                "location_type": "suburb"}


def test_outside_cali_bbox_falls_back_with_reason():
    def fake_get(url, *, params, timeout):
        return [_result("10.0", "-76.53")]

    r = geocode("CL 1 # 2-3", http_get=fake_get)
    assert r == {"ok": True, "accepted": False, "reason": "fuera_de_cali", "location_type": "yes"}


def test_no_result_falls_back_with_reason():
    def fake_get(url, *, params, timeout):
        return []

    r = geocode("CL 1 # 2-3", http_get=fake_get)
    assert r == {"ok": True, "accepted": False, "reason": "sin_resultado"}


def test_empty_direccion_never_calls_http_get():
    def fake_get(url, *, params, timeout):
        raise AssertionError("must not call Nominatim for an empty address")

    r = geocode("", http_get=fake_get)
    assert r == {"ok": True, "accepted": False, "reason": "sin_resultado"}


def test_request_uses_cali_viewbox_bounded_to_the_bbox():
    seen = {}

    def fake_get(url, *, params, timeout):
        seen.update(params)
        return [_result("3.42", "-76.53")]

    geocode("CL 1", http_get=fake_get)
    assert seen["bounded"] == 1
    assert seen["viewbox"] == "-77.0,4.1,-76.0,2.9"
    assert seen["limit"] == 1
    assert seen["format"] == "jsonv2"
    assert seen["addressdetails"] == 1


# ── Transport/malformed-response failures map to GeocodeTransportError ─────


def test_non_list_response_raises_transport_error():
    def fake_get(url, *, params, timeout):
        return {"error": "Unable to geocode"}  # Nominatim error shape, not a list

    try:
        geocode("CL 1 # 2-3", http_get=fake_get)
        raise AssertionError("expected GeocodeTransportError")
    except GeocodeTransportError:
        pass


def test_malformed_result_missing_lon_raises_transport_error():
    def fake_get(url, *, params, timeout):
        return [{"lat": "3.42", "class": "building", "type": "yes"}]  # no "lon"

    try:
        geocode("CL 1 # 2-3", http_get=fake_get)
        raise AssertionError("expected GeocodeTransportError")
    except GeocodeTransportError:
        pass


def test_default_http_get_wraps_timeout_as_transport_error(monkeypatch):
    import requests

    from app.services import geocode as geocode_module

    def _raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(requests, "get", _raise_timeout)
    try:
        geocode_module._default_http_get(
            geocode_module.GEOCODE_URL, params={"q": "x"}, timeout=30
        )
        raise AssertionError("expected GeocodeTransportError")
    except GeocodeTransportError:
        pass


def test_default_http_get_wraps_connection_error_as_transport_error(monkeypatch):
    import requests

    from app.services import geocode as geocode_module

    def _raise_connection_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", _raise_connection_error)
    try:
        geocode_module._default_http_get(
            geocode_module.GEOCODE_URL, params={"q": "x"}, timeout=30
        )
        raise AssertionError("expected GeocodeTransportError")
    except GeocodeTransportError:
        pass


def test_default_http_get_wraps_http_error_status_as_transport_error(monkeypatch):
    """Nominatim rate-limiting (429) or an upstream 5xx — no API-key concept
    exists for Nominatim, so a bad HTTP status is just another transport
    failure, same bucket as a timeout or connection error."""
    import requests

    from app.services import geocode as geocode_module

    class _FakeResponse:
        def raise_for_status(self):
            raise requests.exceptions.HTTPError("429 Too Many Requests")

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse())
    try:
        geocode_module._default_http_get(
            geocode_module.GEOCODE_URL, params={"q": "x"}, timeout=30
        )
        raise AssertionError("expected GeocodeTransportError")
    except GeocodeTransportError:
        pass


def test_default_http_get_sets_a_descriptive_user_agent(monkeypatch):
    """Nominatim's usage policy requires a descriptive User-Agent, not the
    requests-library default."""
    import requests

    from app.services import geocode as geocode_module

    seen = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    def _fake_get(url, *, params, timeout, headers):
        seen.update(headers)
        return _FakeResponse()

    monkeypatch.setattr(requests, "get", _fake_get)
    geocode_module._default_http_get(geocode_module.GEOCODE_URL, params={"q": "x"}, timeout=30)
    assert "User-Agent" in seen
    assert seen["User-Agent"] == geocode_module.USER_AGENT
    assert "requests/" not in seen["User-Agent"]
