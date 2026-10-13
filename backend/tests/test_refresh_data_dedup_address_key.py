"""The building bucket key must be atencionsismo's canonical address key.

`add_dup_group` buckets inspections by address before the name/distance
cascade runs. The reference dashboard buckets by `addressUnifyKey(direccion)`
(`dir:` + key), so the two apps only count the same buildings when this app
buckets by the same key. Before this change it bucketed by the IGAC display
string (`direccion_norm`), which keeps "KR 72 # 10-40" and "CRA 72 No 10-40
Madrigal" (and "CL 2ABIS # 94" / "CL 2A BIS # 94" style variants) apart.
"""
import logging
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows)


def _rows(*direcciones, **common):
    # No coordinates on purpose: with identical pins a geo-cell fallback would
    # merge everything and hide a wrong address key.
    return [{"GlobalID": f"g{i}", "direccion": d, **common}
            for i, d in enumerate(direcciones)]


@pytest.mark.parametrize("variants", [
    ["KR 72 # 10-40", "CRA 72 No 10-40 Madrigal", "Carrera 72 # 10-40 apto 301"],
    ["CL 9 # 42-78", "Calle 9 No. 42-78 Kaliva", "CALLE 9 #42-78 casa 2"],
    ["KR 47 # 4-157", "KR 47 # 4 - 157", "CRA 47 No. 4-157"],
    ["CL 2A BIS # 94", "CL 2 A bis # 94"],
])
def test_spelling_variants_land_in_one_group(variants):
    df = rd.add_dup_group(_df(_rows(*variants)))
    assert df["dup_grupo_id"].nunique() == 1
    assert df["es_representante"].sum() == 1


def test_group_id_is_the_reference_bucket_key():
    df = rd.add_dup_group(_df(_rows("KR 47 # 4 - 157", "Cr 47 #4-157 barrio Tequendama")))
    assert set(df["dup_grupo_id"]) == {"dir:cra:47:4-157"}


def test_different_plates_stay_in_different_groups():
    df = rd.add_dup_group(_df(_rows("KR 47 # 4-157", "KR 47 # 4-158", "KR 47 # 4157")))
    assert df["dup_grupo_id"].nunique() == 3


def test_same_key_far_apart_is_still_split_by_the_building_cascade():
    rows = _rows("CL 5 # 60-64", "Calle 5 No. 60-64", nombre_edificacion="Torre Pacifico")
    rows[0].update(x=-76.5, y=3.4)
    rows[1].update(x=-76.5, y=3.4150)  # ~1.6 km away: same key, different building
    df = rd.add_dup_group(_df(rows))
    assert df["dup_grupo_id"].nunique() == 2


def test_raw_direccion_wins_over_a_stale_direccion_norm():
    rows = _rows("KR 72 # 10-40", "CRA 72 No 10-40 Madrigal")
    rows[0]["direccion_norm"] = "KR 72 # 10-40"
    rows[1]["direccion_norm"] = "KR 72 # 10-40 MADRIGAL"  # display string differs
    df = rd.add_dup_group(_df(rows))
    assert df["dup_grupo_id"].nunique() == 1


def test_falls_back_to_direccion_norm_when_there_is_no_raw_direccion():
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion_norm": "CL 5 # 60-64", "x": -76.5, "y": 3.4},
        {"GlobalID": "b", "direccion_norm": "CALLE 5 NO 60-64", "x": -76.5, "y": 3.4},
    ]))
    assert df["dup_grupo_id"].nunique() == 1
    assert set(df["dup_grupo_id"]) == {"dir:cl:5:60-64"}


@pytest.mark.parametrize("blank", [None, float("nan"), pd.NA, "", "   ", "-"])
def test_address_less_records_do_not_collapse_into_one_bogus_building(blank):
    df = rd.add_dup_group(_df([
        {"GlobalID": "a", "direccion": blank, "x": -76.5000, "y": 3.4000},
        {"GlobalID": "b", "direccion": blank, "x": -76.6000, "y": 3.5000},
    ]))
    assert df["dup_grupo_id"].nunique() == 2
    assert all(g.startswith("geo:") for g in df["dup_grupo_id"])


def test_address_less_records_without_coordinates_get_their_own_id_bucket():
    df = rd.add_dup_group(_df([{"GlobalID": "a", "direccion": None}, {"GlobalID": "b", "direccion": ""}]))
    assert set(df["dup_grupo_id"]) == {"id:a", "id:b"}


def test_free_text_without_a_street_still_buckets_by_its_compact_text():
    df = rd.add_dup_group(_df(_rows("Clinica Colombia", "CLINICA  colombia")))
    assert df["dup_grupo_id"].nunique() == 1


def test_non_string_direccion_does_not_crash():
    df = rd.add_dup_group(_df(_rows(12345, 12345.0, "12345")))
    assert df["dup_grupo_id"].nunique() == 1


def test_clave_direccion_matches_the_audit_signal_helper():
    """`_signals_grupo` re-derives buckets with the same key function."""
    df = rd.add_dup_group(_df(_rows("KR 72 # 10-40", "CRA 72 No 10-40 Madrigal")))
    audit = rd.build_dup_audit(df)
    assert "puente-geo" not in set(audit["senales"].str.split(", ").explode())


# Operator pins ---------------------------------------------------------------
# A pin is keyed by `dup_grupo_id`. Changing the key format orphans every
# existing pin; a pin whose record still exists must follow it to its new group.


def test_a_pin_keyed_by_the_old_group_id_follows_its_record(caplog):
    rows = _rows("CL 5 # 60-64", "Calle 5 No. 60-64", fecha_inspeccion="2026-08-20")
    rows[1]["fecha_inspeccion"] = "2026-08-14"  # the pinned one is the OLDER record
    with caplog.at_level(logging.WARNING):
        df = rd.add_dup_group(_df(rows), overrides={"dir:CL 5 # 60-64": "g1"})
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "g1"
    assert not [r for r in caplog.records if "huerfano" in r.getMessage()]


def test_a_pin_on_the_new_group_id_still_works():
    rows = _rows("CL 5 # 60-64", "Calle 5 No. 60-64", fecha_inspeccion="2026-08-20")
    rows[1]["fecha_inspeccion"] = "2026-08-14"
    df = rd.add_dup_group(_df(rows), overrides={"dir:cl:5:60-64": "g1"})
    assert df[df["es_representante"]].iloc[0]["GlobalID"] == "g1"


def test_a_pin_whose_record_is_gone_is_still_reported_and_ignored(caplog):
    rows = _rows("CL 5 # 60-64", "Calle 5 No. 60-64", fecha_inspeccion="2026-08-20")
    with caplog.at_level(logging.WARNING):
        df = rd.add_dup_group(_df(rows), overrides={"dir:CL 5 # 60-64": "ya-no-existe"})
    assert df["es_representante"].sum() == 1
    assert any("huerfano" in r.getMessage() for r in caplog.records)


def test_a_migrated_pin_never_creates_a_second_representative():
    rows = _rows("CL 5 # 60-64", "Calle 5 No. 60-64", "KR 9 # 1-2")
    df = rd.add_dup_group(_df(rows), overrides={"dir:CL 5 # 60-64": "g0", "dir:KR 9 # 1-2": "g2"})
    assert df.groupby("dup_grupo_id")["es_representante"].sum().eq(1).all()
