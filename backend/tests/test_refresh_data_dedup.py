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


def test_representative_is_the_most_recent_inspection():
    """User's rule (2026-08-26, superseding "most critical"): the LATEST
    re-inspection is the current truth about a building."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "vieja", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-14"},
        {"GlobalID": "nueva", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-20"},
    ]))
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "nueva"


def test_most_recent_wins_even_when_it_is_the_less_severe_one():
    """The case that makes this rule a real choice: a re-inspection that
    DOWNGRADES a building. Under the old "most critical" rule the older,
    more alarming record won; now the newer assessment does."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "vieja_grave", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-14",
         "colapso_total": "si", "criterio_habitabilidad": "i2", "nivel_dano": "alto"},
        {"GlobalID": "nueva_leve", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-20",
         "colapso_total": "no", "criterio_habitabilidad": "h", "nivel_dano": "bajo"},
    ]))
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "nueva_leve"


def test_same_day_ties_break_on_submission_time():
    """61 of 77 real duplicate groups share an inspection DATE, so the date
    alone cannot order them -- CreationDate (the system's own submission
    timestamp) is what actually separates a re-submit from its original."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "temprano", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-14",
         "CreationDate": "2026-08-14T08:00:00"},
        {"GlobalID": "tarde", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-14",
         "CreationDate": "2026-08-14T17:30:00"},
    ]))
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "tarde"


def test_a_manual_override_wins_over_the_automatic_rule():
    """The operator can pin a specific record as the group's representative
    (`representante_manual`), for the cases the automatic rule gets wrong."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "auto", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-20"},
        {"GlobalID": "elegido", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-14"},
    ]), overrides={"dir:CL 5": "elegido"})
    rep = df[df["es_representante"]].iloc[0]
    assert rep["GlobalID"] == "elegido"
    assert df["es_representante"].sum() == 1


def test_an_override_pointing_at_a_missing_record_falls_back_to_the_rule():
    """A stale pin (its record was deleted upstream) must not leave the
    group with ZERO representatives -- that would silently drop a building
    from every figure."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-20"},
        {"GlobalID": "b", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-14"},
    ]), overrides={"dir:CL 5": "ya-no-existe"})
    assert df["es_representante"].sum() == 1
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "a"


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
