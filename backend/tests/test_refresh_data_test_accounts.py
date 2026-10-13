"""Survey123 test accounts never reach the Panel.

Two evaluator names belong to QA accounts ("Especializado pruebas" and
"Especializado bbpruebas"); their records are dropped by EXACT normalised
name -- never by substring, so a real person whose name merely contains
"prueba" is kept.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


def _df(names):
    return pd.DataFrame({"GlobalID": [f"g{i}" for i in range(len(names))], "nombre_evaluador": names})


def test_constant_lists_the_two_known_evaluators():
    assert rd.EVALUADORES_PRUEBA == frozenset({"especializado pruebas", "especializado bbpruebas"})


@pytest.mark.parametrize("name", [
    "Especializado pruebas", "ESPECIALIZADO PRUEBAS", "  especializado   pruebas  ",
    "Especializado\tpruebas", "Especializado Prúebas", "especializado bbpruebas",
    "Especializado BBPruebas", "ESPECIALIZADO BBPRUEBAS ",
])
def test_exact_name_variants_are_dropped(name):
    out = rd.exclude_test_accounts(_df([name, "Ana Gomez"]))
    assert list(out["nombre_evaluador"]) == ["Ana Gomez"]


@pytest.mark.parametrize("name", [
    "Juan Pruebas Gomez", "Maria Prueba", "Pruebas", "Especializado", "Especializado pruebas 2",
    "Especializado pruebas y mas", "Dra. Especializado Pruebas Lopez", "Carlos Bbpruebas",
])
def test_names_that_merely_contain_prueba_are_kept(name):
    out = rd.exclude_test_accounts(_df([name]))
    assert list(out["nombre_evaluador"]) == [name]


def test_empty_none_and_nan_names_are_kept():
    out = rd.exclude_test_accounts(_df([None, "", "   ", float("nan")]))
    assert len(out) == 4


def test_non_string_names_do_not_raise():
    out = rd.exclude_test_accounts(_df([123, 4.5, True]))
    assert len(out) == 3


def test_a_frame_without_the_column_is_returned_unchanged():
    df = pd.DataFrame({"GlobalID": ["a", "b"]})
    assert rd.exclude_test_accounts(df).equals(df)


def test_an_empty_frame_is_returned_unchanged():
    out = rd.exclude_test_accounts(pd.DataFrame({"nombre_evaluador": []}))
    assert len(out) == 0


def test_the_index_is_reset_so_positional_grouping_stays_valid():
    out = rd.exclude_test_accounts(_df(["Especializado pruebas", "Ana", "Luis"]))
    assert list(out.index) == [0, 1]


def test_normalize_drops_test_records_from_the_layer_rows():
    raw = pd.DataFrame({"GlobalID": ["a", "b"], "Nombre Evaluador:": ["Especializado pruebas", "Ana Gomez"]})
    assert list(rd.exclude_test_accounts(raw.rename(columns=rd.RENAME_MAP))["GlobalID"]) == ["b"]


def test_building_grouping_still_works_with_a_test_record_inside_a_group():
    """The test record is the MOST severe one at the address. It is removed
    BEFORE grouping, so it can neither represent the building nor inflate
    `dup_n`; the real records still group and get exactly one representative."""
    df = pd.DataFrame([
        {"GlobalID": "qa", "direccion_norm": "CL 5", "nombre_evaluador": "Especializado pruebas",
         "colapso_total": "si", "criterio_habitabilidad": "i3"},
        {"GlobalID": "r1", "direccion_norm": "CL 5", "nombre_evaluador": "Ana Gomez",
         "criterio_habitabilidad": "r1"},
        {"GlobalID": "r2", "direccion_norm": "CL 5", "nombre_evaluador": "Luis Perez",
         "criterio_habitabilidad": "i1"},
        {"GlobalID": "solo", "direccion_norm": "KR 9", "nombre_evaluador": "Ana Gomez"},
    ])
    out = rd.add_dup_group(rd.exclude_test_accounts(df))
    assert "qa" not in set(out["GlobalID"])
    grp = out[out["direccion_norm"] == "CL 5"]
    assert list(grp["dup_n"]) == [2, 2]
    assert grp["es_representante"].sum() == 1
    assert grp[grp["es_representante"]].iloc[0]["GlobalID"] == "r2"
    assert out["es_representante"].sum() == 2


def test_a_group_made_only_of_test_records_disappears_entirely():
    df = pd.DataFrame([
        {"GlobalID": "qa1", "direccion_norm": "CL 5", "nombre_evaluador": "Especializado pruebas"},
        {"GlobalID": "qa2", "direccion_norm": "CL 5", "nombre_evaluador": "Especializado bbpruebas"},
        {"GlobalID": "r", "direccion_norm": "KR 9", "nombre_evaluador": "Ana"},
    ])
    out = rd.add_dup_group(rd.exclude_test_accounts(df))
    assert list(out["GlobalID"]) == ["r"]
