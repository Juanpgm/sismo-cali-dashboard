"""clean_concepto_cierre_code() / normalize() -- concepto_cierre's coded
domain must be lowercased+stripped SERVER-SIDE, not just client-side.

Fix-first review finding (MEDIUM): the layer ships one code with an unusual
capital ('Sticker_verde'). data.js's computeOptions ranks FILTER options by
the raw record value, so a mixed-case record ('Sticker_verde') and a
lowercase one ('sticker_verde') would appear as two DIFFERENT filter
options even though utils.js's client-side normalize() makes them look
identical everywhere else (color, label). Normalizing the code once here,
at the source, closes that gap for every downstream consumer (filters,
table, xlsx export) at once -- the client-side normalize() in utils.js
stays in place too, as defense in depth, not a replacement for this.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


@pytest.mark.parametrize("value,expected", [
    ("Sticker_verde", "sticker_verde"),
    ("STICKER_VERDE", "sticker_verde"),
    ("demolicion", "demolicion"),
    ("  Fase3  ", "fase3"),
    ("  arreglos_locativos", "arreglos_locativos"),
])
def test_clean_concepto_cierre_code_lowercases_and_strips(value, expected):
    assert rd.clean_concepto_cierre_code(value) == expected


@pytest.mark.parametrize("value", [None, float("nan"), "", "   "])
def test_clean_concepto_cierre_code_missing_values_are_none(value):
    assert rd.clean_concepto_cierre_code(value) is None


def test_normalize_lowercases_mixed_case_concepto_cierre_codes():
    df = pd.DataFrame({
        "GlobalID": ["a", "b"],
        "ObjectID": [1, 2],
        "Fecha de Inspección:": [pd.Timestamp("2026-09-13"), pd.Timestamp("2026-09-14")],
        "Hora:": ["10:00", "11:00"],
        "concepto_cierre": ["Sticker_verde", "sticker_verde"],
        "x": [-76.53, -76.53], "y": [3.42, 3.42],
    })

    out = rd.normalize(df)

    # Both records must collapse onto the SAME normalized code -- this is
    # exactly the "split filter option" bug this fix closes.
    assert list(out["concepto_cierre"]) == ["sticker_verde", "sticker_verde"]


def test_normalize_without_concepto_cierre_column_does_not_crash():
    """Legacy raw frame (pre-2026-09-13 schema): no concepto_cierre column at
    all. normalize() must run to completion and simply not fabricate it."""
    df = pd.DataFrame({
        "GlobalID": ["c"],
        "ObjectID": [3],
        "Fecha de Inspección:": [pd.Timestamp("2026-08-01")],
        "Hora:": ["10:00"],
        "x": [-76.53], "y": [3.42],
    })

    out = rd.normalize(df)

    assert "concepto_cierre" not in out.columns


def test_normalize_blank_concepto_cierre_values_stay_none():
    df = pd.DataFrame({
        "GlobalID": ["d"],
        "ObjectID": [4],
        "Fecha de Inspección:": [pd.Timestamp("2026-08-01")],
        "Hora:": ["10:00"],
        "concepto_cierre": [None],
        "x": [-76.53], "y": [3.42],
    })

    out = rd.normalize(df)

    assert pd.isna(out.loc[0, "concepto_cierre"])
