"""clean_comuna_formulario() -- normalizes the new, free-typed `comuna`
Survey123 layer field (final column `comuna_formulario`) into the geo format
("COMUNA 02") when it clearly names a comuna number, drops unusable junk,
and otherwise passes other free text through unchanged.

`comuna_formulario` is a PROVENANCE/detail column only. It is never merged
into the existing `comuna` column (spatial join against comunas.geojson,
feeds the choropleth/filters/charts) -- see
test_clean_comuna_formulario_does_not_overwrite_the_geo_comuna_column.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


@pytest.mark.parametrize("value,expected", [
    ("19", "COMUNA 19"),
    ("19 ", "COMUNA 19"),
    ("Comuna 19", "COMUNA 19"),
    ("COMUNA 19", "COMUNA 19"),
    ("Comuna19", "COMUNA 19"),
    ("comuna19...", "COMUNA 19"),
    ("5", "COMUNA 05"),
    ("Comuna 5", "COMUNA 05"),
    ("05", "COMUNA 05"),
])
def test_clean_comuna_formulario_normalizes_known_variants(value, expected):
    assert rd.clean_comuna_formulario(value) == expected


@pytest.mark.parametrize("value", ["0", "23", "99", "Comuna 0", "Comuna 25", "Comuna 23"])
def test_clean_comuna_formulario_rejects_numbers_outside_1_22(value):
    assert rd.clean_comuna_formulario(value) is None


@pytest.mark.parametrize("value", ["xxx", "no se sabe", "Comuna ll", "Comuna", "Comuna "])
def test_clean_comuna_formulario_rejects_junk(value):
    assert rd.clean_comuna_formulario(value) is None


@pytest.mark.parametrize("value,expected", [
    # Digits glued directly to trailing letters (fix-first review, HIGH):
    # the old `\b` boundary right after the digit group never matches when a
    # letter is glued on (digit/letter are both "word" chars to `\b`), so
    # "Comuna19administra" fell all the way through to the free-text branch
    # instead of being recognized as comuna 19.
    ("Comuna19administra", "COMUNA 19"),
    ("COMUNA19ADMINISTRATIVA", "COMUNA 19"),
])
def test_clean_comuna_formulario_recognizes_digits_glued_to_trailing_letters(value, expected):
    assert rd.clean_comuna_formulario(value) == expected


def test_clean_comuna_formulario_leading_number_followed_by_a_word_is_free_text_not_a_comuna():
    """"64 campoalegre" does NOT start with the word "comuna", so it is never
    parsed as a comuna-number answer even though it starts with digits --
    only a "comuna"-prefixed or purely-numeric answer is treated as a comuna
    number (see the docstring). This is free text (a place name), passed
    through unchanged, exactly like "Zona Rural" or a corregimiento name."""
    assert rd.clean_comuna_formulario("64 campoalegre") == "64 campoalegre"


@pytest.mark.parametrize("value,expected", [
    (19, "COMUNA 19"),
    (19.0, "COMUNA 19"),  # whole-number float (e.g. a pandas numeric dtype column)
])
def test_clean_comuna_formulario_accepts_numeric_input_types(value, expected):
    assert rd.clean_comuna_formulario(value) == expected


def test_clean_comuna_formulario_nan_is_none():
    assert rd.clean_comuna_formulario(float("nan")) is None


@pytest.mark.parametrize("value", ["Zona Rural", "Corregimiento La Buitrera"])
def test_clean_comuna_formulario_passes_through_other_free_text(value):
    assert rd.clean_comuna_formulario(value) == value


@pytest.mark.parametrize("value", [None, float("nan"), "", "   "])
def test_clean_comuna_formulario_missing_values_are_none(value):
    assert rd.clean_comuna_formulario(value) is None


def test_clean_comuna_formulario_does_not_overwrite_the_geo_comuna_column():
    """Integration through normalize(): comuna_formulario is cleaned, but the
    geo-joined `comuna` column (re-derived from x/y by spatial_join, which
    runs unconditionally and independently) is never set from it."""
    df = pd.DataFrame({
        "GlobalID": ["x"],
        "ObjectID": [1],
        "Fecha de Inspección:": [pd.Timestamp("2026-08-01")],
        "Hora:": ["10:00"],
        "comuna": ["Comuna 19 (typed value that must not leak in)"],
        "x": [0.0], "y": [0.0],  # outside every polygon -> geo comuna is None
    })
    df["comuna_formulario"] = ["19"]

    out = rd.normalize(df)

    assert out.loc[0, "comuna_formulario"] == "COMUNA 19"
    # spatial_join() unconditionally overwrites `comuna` from x/y -- the
    # bogus pre-set value above must be gone, proving no merge from
    # comuna_formulario happened.
    assert pd.isna(out.loc[0, "comuna"])


def test_normalize_without_comuna_formulario_column_does_not_crash():
    """Legacy raw frame (pre-2026-09-13 schema): no comuna_formulario column
    at all. normalize() must run to completion and simply not fabricate it."""
    df = pd.DataFrame({
        "GlobalID": ["y"],
        "ObjectID": [2],
        "Fecha de Inspección:": [pd.Timestamp("2026-08-01")],
        "Hora:": ["10:00"],
        "x": [-76.53], "y": [3.42],
    })

    out = rd.normalize(df)

    assert "comuna_formulario" not in out.columns
