"""`normalize_direccion` -- the single Colombian-address normalizer shared by

  1. the `direccion_norm` column shipped to the panel (`add_address_norm`),
  2. `_clave_direccion`'s bucket key, the address-matching step that runs
     BEFORE `_misma_edificacion`'s name-similarity/30 m cascade ever sees a
     pair of records (see that function's docstring: address alone is never
     a merge key, but a weak bucket key still hides true duplicates from the
     cascade by splitting them into different buckets in the first place).

`normalize_address` (address_norm.py) already canonicalizes most IGAC road
types and No./Nro/N°/Nº -> '#', but it does not fold accents/case, does not
know the CRRA typo, and does not tighten spacing around '-'. Measured on live
data, closing those gaps collapses 998 raw unique addresses to 962 -- 36 true
duplicates the weaker key kept apart (see refresh_data.py's normalize_direccion
docstring).
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA, "", "  ", "-"])
def test_blank_and_missing_input_returns_empty_string(value):
    assert rd.normalize_direccion(value) == ""


@pytest.mark.parametrize("variant", ["Carrera 46 # 10-04", "Cra 46 # 10-04",
                                      "Crra 46 # 10-04", "Kra 46 # 10-04",
                                      "Cr 46 # 10-04", "Kr 46 # 10-04"])
def test_carrera_abbreviation_variants_normalize_the_same(variant):
    assert rd.normalize_direccion(variant) == "KR 46 # 10-04"


@pytest.mark.parametrize("variant", ["Calle 5 # 60-64", "Cll 5 # 60-64", "Cl 5 # 60-64"])
def test_calle_abbreviation_variants_normalize_the_same(variant):
    assert rd.normalize_direccion(variant) == "CL 5 # 60-64"


@pytest.mark.parametrize("variant", ["Avenida 3N # 15-20", "Avda 3N # 15-20", "Av 3N # 15-20"])
def test_avenida_abbreviation_variants_normalize_the_same(variant):
    assert rd.normalize_direccion(variant) == "AV 3N # 15-20"


@pytest.mark.parametrize("variant", ["Diagonal 21 # 8-30", "Diag 21 # 8-30", "Dg 21 # 8-30"])
def test_diagonal_abbreviation_variants_normalize_the_same(variant):
    assert rd.normalize_direccion(variant) == "DG 21 # 8-30"


@pytest.mark.parametrize("variant", ["Transversal 21 # 8-30", "Transv 21 # 8-30", "Tv 21 # 8-30"])
def test_transversal_abbreviation_variants_normalize_the_same(variant):
    assert rd.normalize_direccion(variant) == "TV 21 # 8-30"


@pytest.mark.parametrize("variant", [
    "Calle 10 No. 42a 02", "Calle 10 Nro 42a 02", "Calle 10 N° 42a 02", "Calle 10 Nº 42a 02",
])
def test_numero_markers_fold_to_hash(variant):
    assert rd.normalize_direccion(variant) == "CL 10 # 42A 02"


def test_accent_and_case_folding():
    with_accent = rd.normalize_direccion("Calle 80 No. 45-23, barrio el peñón")
    without_accent = rd.normalize_direccion("CALLE 80 NO. 45-23, BARRIO EL PENON")
    assert with_accent == without_accent
    assert "Ñ" not in with_accent and "ñ" not in with_accent


@pytest.mark.parametrize("variant", ["Calle 46 - 45", "Calle 46-45", "Calle 46  -  45"])
def test_dash_spacing_is_canonicalized(variant):
    assert rd.normalize_direccion(variant) == "CL 46-45"


@pytest.mark.parametrize("variant", ["Cra 46#10-04", "Cra 46 #10-04", "Cra 46#  10-04"])
def test_hash_spacing_is_canonicalized(variant):
    assert rd.normalize_direccion(variant) == "KR 46 # 10-04"


# Real messy examples cited in refresh_data.py's own grouping docstrings
# (_claves_por_edificio / test_refresh_data_dedup.py's Danubio towers case).

def test_real_example_danubio_tower_address_is_stable():
    assert rd.normalize_direccion("Kr 77 # 1c-140") == "KR 77 # 1C-140"


def test_real_example_b1_block_address_is_stable():
    assert rd.normalize_direccion("Cra 94 b1 # 2a-26") == "KR 94 B1 # 2A-26"


def test_messy_real_corpus_example_collapses_with_its_clean_form():
    messy = rd.normalize_direccion("crra. 36 b no. 05-118")
    clean = rd.normalize_direccion("Carrera 36 B # 05-118")
    assert messy == clean == "KR 36 B # 05-118"
