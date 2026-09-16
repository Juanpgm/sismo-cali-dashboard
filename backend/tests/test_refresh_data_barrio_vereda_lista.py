"""resolve_barrio_vereda() precedence extension: the new `barrio_vereda_lista`
(coded-domain field, decoded label) slots in BETWEEN the geo join and the
free-typed `barrio_vereda` -- geo -> lista -> reportado -> sin_dato.

Does not touch test_refresh_data_barrio_resuelto.py: that file's existing
cases (no `barrio_vereda_lista` column at all) must keep passing unchanged,
since resolve_barrio_vereda() defaults a missing column to an all-None
series, identical to before this change.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows)


def test_only_lista_present_resolves_to_lista_and_is_tagged():
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "a", "barrio_geo": None, "barrio_vereda_lista": "San Fernando Nuevo",
         "barrio_vereda": None},
    ]))
    assert df.loc[0, "barrio_vereda_resuelto"] == "San Fernando Nuevo"
    assert df.loc[0, "barrio_vereda_fuente"] == "lista"


def test_geo_beats_lista_when_both_present():
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "b", "barrio_geo": "Alto Napoles", "barrio_vereda_lista": "Napoles",
         "barrio_vereda": None},
    ]))
    assert df.loc[0, "barrio_vereda_resuelto"] == "Alto Napoles"
    assert df.loc[0, "barrio_vereda_fuente"] == "geo"


def test_lista_beats_reportado_when_geo_absent():
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "c", "barrio_geo": None, "barrio_vereda_lista": "San Fernando Nuevo",
         "barrio_vereda": "san fernando (typed)"},
    ]))
    assert df.loc[0, "barrio_vereda_resuelto"] == "San Fernando Nuevo"
    assert df.loc[0, "barrio_vereda_fuente"] == "lista"


def test_all_three_present_geo_still_wins():
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "d", "barrio_geo": "Alto Napoles", "barrio_vereda_lista": "Napoles",
         "barrio_vereda": "napoles"},
    ]))
    assert df.loc[0, "barrio_vereda_resuelto"] == "Alto Napoles"
    assert df.loc[0, "barrio_vereda_fuente"] == "geo"


def test_blank_lista_falls_back_to_reportado():
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "e", "barrio_geo": None, "barrio_vereda_lista": "   ",
         "barrio_vereda": "reportado value"},
    ]))
    assert df.loc[0, "barrio_vereda_resuelto"] == "reportado value"
    assert df.loc[0, "barrio_vereda_fuente"] == "reportado"


def test_nan_dtype_lista_column_does_not_leak_string_nan():
    df = pd.DataFrame({
        "GlobalID": ["f"],
        "barrio_geo": [float("nan")],
        "barrio_vereda_lista": [float("nan")],
        "barrio_vereda": [float("nan")],
    })
    out = rd.resolve_barrio_vereda(df)
    assert pd.isna(out.loc[0, "barrio_vereda_resuelto"])
    assert out.loc[0, "barrio_vereda_fuente"] == "sin_dato"


def test_missing_lista_column_behaves_exactly_as_before():
    """Legacy raw frames without `barrio_vereda_lista` at all (pre-schema-
    change rows, or a caller that hasn't run the new decode step)."""
    df = rd.resolve_barrio_vereda(_df([
        {"GlobalID": "g", "barrio_geo": None, "barrio_vereda": "Vereda El Saladito"},
    ]))
    assert df.loc[0, "barrio_vereda_resuelto"] == "Vereda El Saladito"
    assert df.loc[0, "barrio_vereda_fuente"] == "reportado"


def test_missing_all_three_columns_is_sin_dato_without_crashing():
    df = rd.resolve_barrio_vereda(_df([{"GlobalID": "h"}]))
    assert pd.isna(df.loc[0, "barrio_vereda_resuelto"])
    assert df.loc[0, "barrio_vereda_fuente"] == "sin_dato"
