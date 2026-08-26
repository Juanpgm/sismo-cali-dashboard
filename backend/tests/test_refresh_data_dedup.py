"""Duplicate-building grouping in the Survey123 pipeline.

The same building gets inspected more than once (re-visits, accidental
re-submits). Every submission has its own GlobalID, so nothing is a
duplicate by key -- but 1091 real records collapse to 941 real buildings,
inflating every Panel figure by ~13.7%. Reported by the user as "mi cifra
de colapso total incrementa".

Nothing is deleted: each record is tagged with its building group and a
single representative per group is flagged, so KPIs can count buildings
while the table still shows every inspection.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows)


def test_records_at_the_same_address_share_one_group():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5 # 60-64", "x": -76.5, "y": 3.4},
        {"GlobalID": "b", "direccion_norm": "CL 5 # 60-64", "x": -76.5, "y": 3.4},
        {"GlobalID": "c", "direccion_norm": "KR 9 # 1-2", "x": -76.6, "y": 3.5},
    ]))
    assert df.loc[0, "dup_grupo_id"] == df.loc[1, "dup_grupo_id"]
    assert df.loc[2, "dup_grupo_id"] != df.loc[0, "dup_grupo_id"]
    assert list(df["dup_n"]) == [2, 2, 1]


def test_exactly_one_representative_per_group():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5", "colapso_total": "no"},
        {"GlobalID": "b", "direccion_norm": "CL 5", "colapso_total": "no"},
        {"GlobalID": "c", "direccion_norm": "CL 5", "colapso_total": "no"},
    ]))
    assert df["es_representante"].sum() == 1


def test_representative_is_the_most_critical_not_the_first():
    """The user's rule: keep the most critical value. The severe record is
    LAST here, so a naive first-wins would pick the wrong one."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "leve", "direccion_norm": "CL 5",
         "colapso_total": "no", "colapso_parcial": "no",
         "criterio_habitabilidad": "h", "nivel_dano": "bajo"},
        {"GlobalID": "grave", "direccion_norm": "CL 5",
         "colapso_total": "si", "colapso_parcial": "no",
         "criterio_habitabilidad": "i2", "nivel_dano": "alto"},
    ]))
    rep = df[df["es_representante"]].iloc[0]
    assert rep["GlobalID"] == "grave"


@pytest.mark.parametrize("campo,leve,grave", [
    ("colapso_total", "no", "si"),
    ("colapso_parcial", "no", "si"),
    ("criterio_habitabilidad", "h", "i2"),
    ("nivel_dano", "bajo", "alto"),
])
def test_each_severity_signal_breaks_the_tie(campo, leve, grave):
    df = rd.add_dup_group(_df([
        {"GlobalID": "leve", "direccion_norm": "CL 5", campo: leve},
        {"GlobalID": "grave", "direccion_norm": "CL 5", campo: grave},
    ]))
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "grave"


def test_nothing_is_dropped():
    df = rd.add_dup_group(_df([
        {"GlobalID": g, "direccion_norm": "CL 5"} for g in "abcde"
    ]))
    assert len(df) == 5, "grouping must tag rows, never remove them"
    assert set(df["GlobalID"]) == set("abcde")


def test_falls_back_to_coordinates_when_the_address_is_blank():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "", "x": -76.512345, "y": 3.412345},
        {"GlobalID": "b", "direccion_norm": None, "x": -76.512345, "y": 3.412345},
    ]))
    assert df.loc[0, "dup_grupo_id"] == df.loc[1, "dup_grupo_id"]


def test_a_record_with_neither_address_nor_coords_stands_alone():
    """No identity signal -> it must NOT be pooled with other unidentifiable
    rows, which would silently merge unrelated buildings."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "", "x": None, "y": None},
        {"GlobalID": "b", "direccion_norm": "", "x": None, "y": None},
    ]))
    assert df.loc[0, "dup_grupo_id"] != df.loc[1, "dup_grupo_id"]
    assert list(df["dup_n"]) == [1, 1]
    assert df["es_representante"].all()
