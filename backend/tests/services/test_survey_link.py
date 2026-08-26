"""backend/app/services/survey_link.py (RED first, task 3.1) — design.md
ADR-6; spec `getEnlaceSurvey builds a prefilled Survey123 URL from
configuration`.

Pure URL construction only — no FastAPI, no Firestore, no environment.
`build_survey_urls(clave, *, form_url, field_app_item_id)` takes
configuration as arguments so it is fully testable offline.
"""
from __future__ import annotations

from app.services import survey_link as sl


def test_web_link_carries_the_key_as_field_codigoapp():
    urls = sl.build_survey_urls(
        "PLN-14832-9C4A1F0B",
        form_url="https://survey123.arcgis.com/share/abc123",
        field_app_item_id=None,
    )
    assert "field:codigoapp=PLN-14832-9C4A1F0B" in urls["web"]


def test_separator_is_ampersand_when_form_url_already_has_a_query_string():
    urls = sl.build_survey_urls(
        "PLN-14832-9C4A1F0B",
        form_url="https://survey123.arcgis.com/share/abc123?draft=1",
        field_app_item_id=None,
    )
    assert "?draft=1&field:codigoapp=PLN-14832-9C4A1F0B" in urls["web"]
    assert urls["web"].count("?") == 1


def test_separator_is_question_mark_when_form_url_has_no_query_string():
    urls = sl.build_survey_urls(
        "PLN-14832-9C4A1F0B",
        form_url="https://survey123.arcgis.com/share/abc123",
        field_app_item_id=None,
    )
    assert "?field:codigoapp=PLN-14832-9C4A1F0B" in urls["web"]
    assert urls["web"].count("?") == 1


def test_key_is_percent_encoded():
    urls = sl.build_survey_urls(
        "PLN-A B-9C4A1F0B",  # a space, deliberately not URL-safe by itself
        form_url="https://survey123.arcgis.com/share/abc123",
        field_app_item_id=None,
    )
    assert " " not in urls["web"]
    assert "PLN-A%20B-9C4A1F0B" in urls["web"]


def test_app_link_is_none_without_a_field_app_item_id():
    urls = sl.build_survey_urls(
        "PLN-14832-9C4A1F0B",
        form_url="https://survey123.arcgis.com/share/abc123",
        field_app_item_id=None,
    )
    assert urls["app"] is None


def test_app_link_is_present_when_item_id_is_configured():
    urls = sl.build_survey_urls(
        "PLN-14832-9C4A1F0B",
        form_url="https://survey123.arcgis.com/share/abc123",
        field_app_item_id="074aeda67b10b4725bb47e7b20ae6a2bf",
    )
    assert urls["app"] == (
        "arcgis-survey123:///?itemID=074aeda67b10b4725bb47e7b20ae6a2bf"
        "&field:codigoapp=PLN-14832-9C4A1F0B"
    )


def test_no_other_field_parameter_appears_in_either_url():
    urls = sl.build_survey_urls(
        "PLN-14832-9C4A1F0B",
        form_url="https://survey123.arcgis.com/share/abc123",
        field_app_item_id="itemid123",
    )
    assert urls["web"].count("field:") == 1
    assert urls["app"].count("field:") == 1
