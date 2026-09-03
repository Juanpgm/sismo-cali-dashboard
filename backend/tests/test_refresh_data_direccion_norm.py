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


# --- Abbreviation glued to the number (the reported bug) --------------------
# `\b` does not separate a letter from a digit ("A" -> "7" is not a boundary
# to `\b` since both are word characters), so "CARRERA77" survived every
# `\bCARRERA\b`-style pattern in address_norm.py's _ROAD_TYPES unchanged.
# Affects every road type, not just CARRERA -- covering a representative
# sample (KR/CL here; the fix is generic across the whole table).

@pytest.mark.parametrize("variant,expected", [
    ("Carrera77 #1c-140", "KR77 # 1C-140"),
    ("CARRERA77", "KR77"),
    ("Calle3D # 45-23", "CL3D # 45-23"),
    ("KR15 # 8-20", "KR15 # 8-20"),
])
def test_abbreviation_glued_to_number_normalizes(variant, expected):
    assert rd.normalize_direccion(variant) == expected


# --- Already well-formed IGAC addresses must not be perturbed --------------

@pytest.mark.parametrize("variant,expected", [
    ("KR 96 # 48 - 53 BLQ 1 AP 502", "KR 96 # 48-53 BLQ 1 AP 502"),
    ("KR 26 L # 72 W - 39", "KR 26 L # 72 W-39"),
])
def test_well_formed_igac_address_is_unchanged_in_shape(variant, expected):
    assert rd.normalize_direccion(variant) == expected


# --- Lone "K" as a carrera abbreviation (real IGAC form) --------------------
# Digit-adjacency alone is NOT enough evidence: on the real 1000-record
# dataset, a bare "K" glued to a number is the short form of "kilometro"
# ("K18", "K10.5", "K14, Sector...") in 10 of 12 raw occurrences -- only 2
# are a real carrera. Both shapes look identical to a rule that only checks
# "K" + digit. What tells them apart on every occurrence actually seen: a
# cadastral number-sign ("#") shows up shortly after the road number in the
# real carrera cases, and never does in the kilometer ones -- so that's the
# signal this rule requires. Kilometers are also sometimes written with a
# decimal, which a carrera number never has -- rejected outright regardless
# of what follows, as an independent second signal.

@pytest.mark.parametrize("variant,expected", [
    ("K 85 E # 28 - 06", "KR 85 E # 28-06"),
    ("K85 # 28-06", "KR85 # 28-06"),
    # The two real carrera occurrences found in web/data/inspections.json's
    # raw `direccion` field.
    ("K 67#3C-15", "KR 67 # 3C-15"),
    ("K 58 #3 - 136 4 G", "KR 58 # 3-136 4 G"),
])
def test_lone_k_glued_to_digit_normalizes_to_carrera(variant, expected):
    assert rd.normalize_direccion(variant) == expected


@pytest.mark.parametrize("variant,expected", [
    # Kilometer abbreviation: "K" followed by a letter, not a digit -- must
    # NOT be folded into KR. ("VIA" -> "VI" here is the separate, pre-existing
    # VI[ÍA] road-type rule, unrelated to this guard.)
    ("KM 18 VIA CALI JAMUNDI", "KM 18 VI CALI JAMUNDI"),
    # A bare tower/block letter "K", not glued to a digit -- left alone.
    ("TORRE K - 5", "TORRE K-5"),
    ("BLOQUE K", "BLOQUE K"),
    ("TORRE K 5", "TORRE K 5"),
    ("BLOQUE K 3", "BLOQUE K 3"),
    ("MANZANA K 12", "MANZANA K 12"),
    ("MZ K 5 CS 3", "MZ K 5 CS 3"),
    # A real "KR" earlier in the string must not make the digit-adjacency
    # check spill onto an unrelated later "K" -- each occurrence is judged
    # on its own local evidence (no "#" follows this "K 5").
    ("Cra 1 K 5", "KR 1 K 5"),
    # Kilometer markers pulled directly from web/data/inspections.json's raw
    # `direccion` field (see test_lone_k_glued_to_digit_normalizes_to_carrera
    # above for the 2 real carrera occurrences in the same field, which DO
    # convert). None of these have a "#" nearby, so none must convert.
    ("K18 vial al mar sector la vaca", "K18 VIAL AL MAR SECTOR LA VACA"),
    # `normalize_direccion` strips all dots (a separate, pre-existing rule
    # for stray abbreviation punctuation) -- so the decimal point is gone
    # too, but the point of this test is that it never becomes "KR".
    ("K10.5 Casa 6", "K105 CASA 6"),
    ("Sector Altos Los Pinos, K14", "SECTOR ALTOS LOS PINOS, K14"),
])
def test_lone_k_false_positive_guard(variant, expected):
    assert rd.normalize_direccion(variant) == expected


# --- Free text that is not a cadastral address ------------------------------
# normalize_address has no rule to typify these -- they pass through
# untouched (case-folded, same as everything else) rather than getting a
# fabricated road-type code.

@pytest.mark.parametrize("variant,expected", [
    ("Clinica colombia", "CLINICA COLOMBIA"),
    ("Finca El Refujio", "FINCA EL REFUJIO"),
])
def test_free_text_non_address_passes_through_untyped(variant, expected):
    assert rd.normalize_direccion(variant) == expected


# --- Mixed case / accents / irregular spacing -------------------------------

@pytest.mark.parametrize("variant,expected", [
    ("cra 44a", "KR 44A"),
    ("Calle 3 c  # 66b-03", "CL 3 C # 66B-03"),
    ("CL 72 W # 28 D - 11", "CL 72 W # 28 D-11"),
    ("Avenida 5 ta norte # 23 74", "AV 5 TA NORTE # 23 74"),
])
def test_mixed_case_accents_and_irregular_spacing(variant, expected):
    assert rd.normalize_direccion(variant) == expected
