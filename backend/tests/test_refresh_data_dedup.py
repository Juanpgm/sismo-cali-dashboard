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


def test_representative_is_the_most_critical_assessment():
    """User's rule (reverted 2026-09-07): the MOST CRITICAL record wins,
    never a later re-visit that happens to downgrade the building."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "leve", "direccion_norm": "CL 5", "colapso_total": "no"},
        {"GlobalID": "grave", "direccion_norm": "CL 5", "colapso_total": "si"},
    ]))
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "grave"


def test_most_critical_wins_even_when_it_is_the_older_one():
    """The case that makes this rule a real choice: a re-inspection that
    DOWNGRADES a building must NOT erase the earlier, more alarming record --
    the older, worse assessment still wins."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "vieja_grave", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-14",
         "colapso_total": "si", "criterio_habitabilidad": "i2", "nivel_dano": "alto"},
        {"GlobalID": "nueva_leve", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-20",
         "colapso_total": "no", "criterio_habitabilidad": "h", "nivel_dano": "bajo"},
    ]))
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "vieja_grave"


def test_severity_ties_break_on_recency_then_submission_time():
    """When every severity field ties, recency (date, then CreationDate --
    the system's own submission timestamp) decides, so the pick stays
    deterministic instead of depending on row order."""
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


# Coarser geo bucket (~110 m cell, was ~1 m) ----------------------------------
# The 5-decimal bucket was so tight that GPS jitter alone split the SAME
# address-less building into two buckets, which `_misma_edificacion` never
# even got a chance to re-join (they were never compared). 3 decimals puts
# them in one bucket; the 30 m distance check still does the real splitting.


def test_addressless_records_15m_apart_now_share_one_group():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "", "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "", "y": 3.40013, "x": -76.50000},  # ~14.5 m
    ]))
    assert df.loc[0, "dup_grupo_id"] == df.loc[1, "dup_grupo_id"]
    assert list(df["dup_n"]) == [2, 2]


def test_addressless_records_over_30m_in_the_same_geo_cell_stay_separate():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "", "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "", "y": 3.40040, "x": -76.50000},  # ~44 m, same 3-dec cell
    ]))
    assert df.loc[0, "dup_grupo_id"] != df.loc[1, "dup_grupo_id"]


# Geo bridge across buckets ---------------------------------------------------
# A typo'd/reformatted address string (or a geo-cell boundary) can put the
# SAME building's two records in different `_clave_direccion` buckets.
# `_puente_geo` re-merges those groups, but only when BOTH name and distance
# agree -- never on either signal alone.


def test_bridge_merges_different_address_strings_same_name_close():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5 # 60-64", "nombre_edificacion": "Torre Pacifico",
         "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "CALLE 5 NO 60-64", "nombre_edificacion": "Torre Pacifico",
         "y": 3.40010, "x": -76.50000},  # ~11 m, different address string
    ]))
    assert df.loc[0, "dup_grupo_id"] == df.loc[1, "dup_grupo_id"]
    assert list(df["dup_n"]) == [2, 2]


def test_bridge_does_not_merge_same_name_far_apart():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5 # 60-64", "nombre_edificacion": "Torre Pacifico",
         "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "CALLE 5 NO 60-64", "nombre_edificacion": "Torre Pacifico",
         "y": 3.40150, "x": -76.50000},  # ~165 m
    ]))
    assert df.loc[0, "dup_grupo_id"] != df.loc[1, "dup_grupo_id"]


def test_bridge_does_not_merge_different_names_close_together():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5 # 60-64", "nombre_edificacion": "Torre Pacifico",
         "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "KR 9 # 1-2", "nombre_edificacion": "Edificio Andes",
         "y": 3.40001, "x": -76.50000},
    ]))
    assert df.loc[0, "dup_grupo_id"] != df.loc[1, "dup_grupo_id"]


def test_bridge_does_not_merge_when_a_name_is_blank():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5 # 60-64", "nombre_edificacion": "Torre Pacifico",
         "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "KR 9 # 1-2", "nombre_edificacion": "",
         "y": 3.40001, "x": -76.50000},
    ]))
    assert df.loc[0, "dup_grupo_id"] != df.loc[1, "dup_grupo_id"]


# Operator-facing audit (build_dup_audit) -------------------------------------


def test_audit_excludes_singleton_groups_and_clears_revisar_when_consistent():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5", "nombre_edificacion": "Casa",
         "y": 3.4, "x": -76.5, "colapso_total": "no", "criterio_habitabilidad": "h"},
        {"GlobalID": "b", "direccion_norm": "CL 5", "nombre_edificacion": "Casa",
         "y": 3.40001, "x": -76.5, "colapso_total": "no", "criterio_habitabilidad": "h"},
        {"GlobalID": "c", "direccion_norm": "KR 9", "colapso_total": "no"},
    ]))
    audit = rd.build_dup_audit(df)
    assert set(audit["dup_grupo_id"]) == {"dir:CL 5"}, "the singleton KR 9 group must not appear"
    assert len(audit) == 2
    assert not audit["revisar"].any()
    assert (audit["senales"] == "misma-direccion").all()


def test_audit_flags_contradictory_severity_even_when_corroborated():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5", "nombre_edificacion": "Casa",
         "y": 3.4, "x": -76.5, "colapso_total": "si", "criterio_habitabilidad": "i2",
         "fecha_inspeccion": "2026-08-14"},
        {"GlobalID": "b", "direccion_norm": "CL 5", "nombre_edificacion": "Casa",
         "y": 3.40001, "x": -76.5, "colapso_total": "no", "criterio_habitabilidad": "h",
         "fecha_inspeccion": "2026-08-20"},
    ]))
    audit = rd.build_dup_audit(df)
    assert len(audit) == 2
    assert audit["revisar"].all()


def test_audit_flags_address_only_groups_with_no_corroborating_signal():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5", "colapso_total": "no"},
        {"GlobalID": "b", "direccion_norm": "CL 5", "colapso_total": "no"},
    ]))
    audit = rd.build_dup_audit(df)
    assert (audit["senales"] == "solo-direccion").all()
    assert audit["revisar"].all()


# Specificity guard on the bridge (RELIABILITY-001) --------------------------
# A bare generic name ("Casa", "Vivienda") repeats verbatim across unrelated
# buildings city-wide -- 40 real records are literally named "Casa". Letting
# name-match alone justify a CROSS-address merge folds those together; a real
# cluster at "SECTOR LA CAPILLA, K12" vs the typo "SECTOR LA CAPULLA, K12"
# (both "Casa", ~13-28 m apart) would wrongly merge two distinct rural houses.
# The guard is bridge-only: within one address bucket a generic name still
# corroborates fine (the address itself is the signal there).


def test_bridge_does_not_merge_a_bare_generic_name_across_addresses():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "SECTOR LA CAPILLA, K12", "nombre_edificacion": "Casa",
         "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "SECTOR LA CAPULLA, K12", "nombre_edificacion": "Casa",
         "y": 3.40020, "x": -76.50000},  # ~22 m, different (typo'd) address string
    ]))
    assert df.loc[0, "dup_grupo_id"] != df.loc[1, "dup_grupo_id"]


def test_bridge_still_merges_a_specific_two_token_name_across_addresses():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5 # 60-64", "nombre_edificacion": "Conjunto Asturias",
         "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "CALLE 5 NO 60-64", "nombre_edificacion": "Conjunto Asturias",
         "y": 3.40010, "x": -76.50000},  # ~11 m, different address string
    ]))
    assert df.loc[0, "dup_grupo_id"] == df.loc[1, "dup_grupo_id"]


def test_audit_flags_every_puente_geo_group_for_review():
    """Cross-address merges are a new class of decision (this diff) -- every
    one gets `revisar=True` unconditionally, even when name AND distance both
    corroborate, so an operator eyeballs each one at least once."""
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5 # 60-64", "nombre_edificacion": "Conjunto Asturias",
         "y": 3.40000, "x": -76.50000},
        {"GlobalID": "b", "direccion_norm": "CALLE 5 NO 60-64", "nombre_edificacion": "Conjunto Asturias",
         "y": 3.40010, "x": -76.50000},
    ]))
    audit = rd.build_dup_audit(df)
    assert len(audit) == 2
    assert "puente-geo" in audit["senales"].iloc[0]
    assert audit["revisar"].all()


# Orphaned override warning (RELIABILITY-002) --------------------------------
# This diff changes the geo-key format (3 decimals, was 5) and adds the
# bridge, either of which can remap a group's key -- an operator's pin
# (Firestore `panel_representante`, consumed live by panel_representante.py)
# then silently stops applying with the automatic rule quietly deciding
# instead. Orphaned pins must be visible, not silent.


def test_orphaned_override_warns_and_still_resolves_via_the_automatic_rule(caplog):
    with caplog.at_level("WARNING", logger="refresh_data"):
        df = rd.add_dup_group(_df([
            {"GlobalID": "a", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-20"},
            {"GlobalID": "b", "direccion_norm": "CL 5", "fecha_inspeccion": "2026-08-14"},
        ]), overrides={"dir:CL 5": "ya-no-existe"})
    assert any("huerfano" in r.message for r in caplog.records)
    assert df["es_representante"].sum() == 1
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "a"
