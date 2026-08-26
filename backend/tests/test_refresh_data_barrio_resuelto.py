"""barrio_vereda_resuelto / barrio_vereda_fuente -- geo-first resolution of
"Barrio / vereda" (user request: obtain that field as the product of the
geographic intersection with the barrios_veredas / comunas_corregimientos
basemaps, without destroying the inspector's typed value).

Measured on inspections.json (1000 records, refresh_data.py's spatial_join
output): barrio_geo non-empty 988/1000, barrio_vereda (typed) non-empty
938/1000, 12 records have ONLY the typed value (no polygon match) and MUST
NOT go blank, 926 have both and 412 of those (44.5%) disagree -- the polygon
is consistently more precise ('Napoles' -> 'Alto Napoles', 'Suba 1' -- a
Bogota neighbourhood typed by mistake -- vs the correct Cali polygon), so
geo wins whenever both are present.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows)


def test_only_geo_present_resolves_to_geo():
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "a", "barrio_geo": "San Fernando Nuevo", "barrio_vereda": None},
    ]))
    assert df.loc[0, "barrio_vereda_resuelto"] == "San Fernando Nuevo"
    assert df.loc[0, "barrio_vereda_fuente"] == "geo"


def test_only_typed_present_does_not_go_blank_and_is_marked_reportado():
    """The 12-record case: point falls outside every polygon. The typed
    value must survive the resolution AND be flagged as non-geographic."""
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "b", "barrio_geo": None, "barrio_vereda": "Vereda El Saladito"},
    ]))
    assert df.loc[0, "barrio_vereda_resuelto"] == "Vereda El Saladito"
    assert df.loc[0, "barrio_vereda_fuente"] == "reportado"


def test_neither_present_is_sin_dato_without_crashing():
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "c", "barrio_geo": None, "barrio_vereda": None},
    ]))
    # pandas assigns a missing value in an object/str column as float NaN
    # (not the Python `None` we passed in) -- pd.isna() is the correct check
    # here, matching this pipeline's own established convention.
    assert pd.isna(df.loc[0, "barrio_vereda_resuelto"])
    assert df.loc[0, "barrio_vereda_fuente"] == "sin_dato"


def test_both_present_and_disagreeing_geo_wins():
    """The polygon is more precise in essentially every sampled case
    (measured: 412/926 = 44.5% of dual-value records disagree); one typed
    value observed was 'Suba 1', a Bogota neighbourhood, not Cali at all."""
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "d", "barrio_geo": "Alto Napoles", "barrio_vereda": "Napoles"},
        {"GlobalID": "e", "barrio_geo": "Alto Aguacatal", "barrio_vereda": "Suba 1"},
    ]))
    assert list(df["barrio_vereda_resuelto"]) == ["Alto Napoles", "Alto Aguacatal"]
    assert list(df["barrio_vereda_fuente"]) == ["geo", "geo"]


def test_nan_dtype_columns_do_not_leak_the_string_nan():
    """pandas float columns full of missing values surface as float NaN, not
    None -- str(float('nan')) == 'nan', a truthy non-empty string this repo
    has been bitten by before. Build the columns the way a real DataFrame
    would (an object column mixing real strings with float NaN) and confirm
    resolve_barrio_vereda() guards on pd.isna() rather than truthiness/str()."""
    df = pd.DataFrame({
        "GlobalID": ["f", "g"],
        "barrio_geo": [float("nan"), "Pance"],
        "barrio_vereda": [float("nan"), float("nan")],
    })
    out = rd.resolve_barrio_vereda(df)
    assert pd.isna(out.loc[0, "barrio_vereda_resuelto"])
    assert out.loc[0, "barrio_vereda_fuente"] == "sin_dato"
    assert out.loc[1, "barrio_vereda_resuelto"] == "Pance"
    assert out.loc[1, "barrio_vereda_fuente"] == "geo"


def test_blank_strings_are_treated_as_missing_not_as_values():
    """Whitespace-only typed values (a common Survey123 artifact) must not
    win over 'sin_dato', and must not be reported as a real barrio."""
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "h", "barrio_geo": None, "barrio_vereda": "   "},
    ]))
    assert pd.isna(df.loc[0, "barrio_vereda_resuelto"])
    assert df.loc[0, "barrio_vereda_fuente"] == "sin_dato"


def test_missing_source_columns_do_not_crash():
    """Defensive: a DataFrame that hasn't gone through spatial_join yet (or
    a caller that passes a frame without barrio_vereda) should resolve to
    sin_dato per row instead of raising KeyError."""
    df = rd.resolve_barrio_vereda(_df([{"GlobalID": "i"}]))
    assert pd.isna(df.loc[0, "barrio_vereda_resuelto"])
    assert df.loc[0, "barrio_vereda_fuente"] == "sin_dato"
