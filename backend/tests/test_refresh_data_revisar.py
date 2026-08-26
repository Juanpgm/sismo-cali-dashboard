"""add_revisar_flags — data-quality review flags for the Analista "Gestión de
datos" grid. Each record gets `revisar` (bool) + `revisar_casos` (list of
{caso, campos}) from conservative contradiction/outlier rules, measured
against the live dataset (review-cases analysis 2026-08-26)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows)


def test_colapso_total_and_parcial_together_is_flagged():
    df = rd.add_revisar_flags(_df([{"colapso_total": "si", "colapso_parcial": "si"}]))
    assert df.loc[0, "revisar"]
    casos = df.loc[0, "revisar_casos"]
    assert casos[0]["caso"] == "Colapso total y parcial simultáneos"
    assert casos[0]["campos"] == ["colapso_total", "colapso_parcial"]


def test_only_parcial_is_not_flagged():
    df = rd.add_revisar_flags(_df([{"colapso_total": "no", "colapso_parcial": "si"}]))
    assert not df.loc[0, "revisar"]
    assert df.loc[0, "revisar_casos"] == []


def test_atypical_n_pisos_is_flagged_high_and_nonpositive_but_not_normal():
    df = rd.add_revisar_flags(_df([{"n_pisos": 91980}, {"n_pisos": 0}, {"n_pisos": 5}]))
    assert df.loc[0, "revisar"]
    assert df.loc[1, "revisar"]
    assert not df.loc[2, "revisar"]


def test_atypical_n_ocupantes_is_flagged():
    df = rd.add_revisar_flags(_df([{"n_ocupantes": 5000}, {"n_ocupantes": 20}]))
    assert df.loc[0, "revisar"]
    assert not df.loc[1, "revisar"]


def test_nivel_alto_but_habitable_is_flagged():
    df = rd.add_revisar_flags(_df([{"nivel_dano": "alto", "criterio_habitabilidad": "h"}]))
    casos = df.loc[0, "revisar_casos"]
    assert any(c["caso"] == "Nivel de daño alto con criterio habitable" for c in casos)


def test_multiple_cases_accumulate_in_one_record():
    df = rd.add_revisar_flags(_df([
        {"colapso_total": "si", "colapso_parcial": "si", "n_pisos": 999},
    ]))
    assert len(df.loc[0, "revisar_casos"]) == 2


def test_clean_record_has_empty_casos():
    df = rd.add_revisar_flags(_df([
        {"colapso_total": "no", "colapso_parcial": "no", "n_pisos": 3,
         "n_ocupantes": 10, "nivel_dano": "medio", "criterio_habitabilidad": "i2"},
    ]))
    assert not df.loc[0, "revisar"]
    assert df.loc[0, "revisar_casos"] == []
