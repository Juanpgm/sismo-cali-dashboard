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


# Same address, different towers ---------------------------------------------
# A conjunto residencial has ONE street address and many buildings. Grouping
# on the address alone merged 7 towers of "KR 77 # 1C-140" (T1, T3, T10, T15,
# T19, T20 del Danubio) into a single building -- the opposite error to the
# one this module exists to fix: it UNDER-counts.
#
# Measured on the live data, the two cases separate cleanly:
#   accidental re-submits : name similarity 1.00, <= 13 m apart
#   different towers      : name similarity <= 0.67, >= 48 m apart
# So same-address records are the same building only when the building NAME
# matches closely AND the coordinates are within GPS noise of each other.


def test_same_address_different_tower_names_are_different_buildings():
    df = rd.add_dup_group(_df([
        {"GlobalID": "t1", "direccion_norm": "KR 77 # 1C-140",
         "nombre_edificacion": "Torre 1", "y": 3.38858, "x": -76.55357},
        {"GlobalID": "t19", "direccion_norm": "KR 77 # 1C-140",
         "nombre_edificacion": "T19", "y": 3.38874, "x": -76.55227},
    ]))
    assert df.loc[0, "dup_grupo_id"] != df.loc[1, "dup_grupo_id"]
    assert df["es_representante"].all(), "two towers are two buildings, both count"


def test_same_address_same_name_within_gps_noise_is_one_building():
    """The real accidental re-submit: identical name, metres apart."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "KR 94 B1 # 2A-26",
         "nombre_edificacion": "Casa", "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "KR 94 B1 # 2A-26",
         "nombre_edificacion": "Casa", "y": 3.40010, "x": -76.50000},
    ]))
    assert df.loc[0, "dup_grupo_id"] == df.loc[1, "dup_grupo_id"]
    assert df["es_representante"].sum() == 1


def test_spelling_variants_of_the_same_name_still_group():
    """'ASTURIAS' vs 'Conjunto Multifamiliar Asturias' is one building --
    the match must be fuzzy, not exact string equality."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 1", "nombre_edificacion": "ASTURIAS",
         "y": 3.4, "x": -76.5},
        {"GlobalID": "b", "direccion_norm": "CL 1",
         "nombre_edificacion": "Conjunto Multifamiliar Asturias", "y": 3.40005, "x": -76.5},
    ]))
    assert df.loc[0, "dup_grupo_id"] == df.loc[1, "dup_grupo_id"]


def test_same_name_but_far_apart_are_different_buildings():
    """Identical names are common in a complex ('Torre', 'Bloque A'). Distance
    is what says they are different structures."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 1", "nombre_edificacion": "Bloque A",
         "y": 3.4000, "x": -76.5000},
        {"GlobalID": "b", "direccion_norm": "CL 1", "nombre_edificacion": "Bloque A",
         "y": 3.4015, "x": -76.5000},  # ~165 m
    ]))
    assert df.loc[0, "dup_grupo_id"] != df.loc[1, "dup_grupo_id"]


def test_blank_names_fall_back_to_distance_alone():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 1", "nombre_edificacion": "",
         "y": 3.4, "x": -76.5},
        {"GlobalID": "b", "direccion_norm": "CL 1", "nombre_edificacion": None,
         "y": 3.40005, "x": -76.5},
    ]))
    assert df.loc[0, "dup_grupo_id"] == df.loc[1, "dup_grupo_id"]
