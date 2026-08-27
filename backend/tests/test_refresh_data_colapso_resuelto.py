"""add_colapso_resuelto — resolves the colapso_total/colapso_parcial "both si"
contradiction (see add_revisar_flags's "Colapso total y parcial simultáneos"
case) into one non-destructive derived field the panel can count on without
double-counting. Agreed rule: both si -> parcial. Raw fields are untouched."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows)


def test_both_si_resolves_to_parcial():
    df = rd.add_colapso_resuelto(_df([{"colapso_total": "si", "colapso_parcial": "si"}]))
    assert df.loc[0, "colapso_resuelto"] == "parcial"


def test_only_total_si_resolves_to_total():
    df = rd.add_colapso_resuelto(_df([{"colapso_total": "si", "colapso_parcial": "no"}]))
    assert df.loc[0, "colapso_resuelto"] == "total"


def test_only_parcial_si_resolves_to_parcial():
    df = rd.add_colapso_resuelto(_df([{"colapso_total": "no", "colapso_parcial": "si"}]))
    assert df.loc[0, "colapso_resuelto"] == "parcial"


def test_neither_si_resolves_to_ninguno():
    df = rd.add_colapso_resuelto(_df([{"colapso_total": "no", "colapso_parcial": "no"}]))
    assert df.loc[0, "colapso_resuelto"] == "ninguno"


def test_raw_fields_are_untouched():
    df = rd.add_colapso_resuelto(_df([{"colapso_total": "si", "colapso_parcial": "si"}]))
    assert df.loc[0, "colapso_total"] == "si"
    assert df.loc[0, "colapso_parcial"] == "si"
