"""Building-name normalisation must match atencionsismo's `normNombre`.

The name is the second signal of the building cascade (`_misma_edificacion`)
and the only one of the cross-address bridge (`_puente_geo`). If this app
normalises a name differently from the reference, the same two inspections
are one building there and two here. Real case: "Antonio Nari\u00f1o. Etapa 3
Torre A -B- C" kept double spaces ("NARINO  ETAPA"), which pulled
SequenceMatcher's ratio under the threshold and split a pair the reference
merges.

`fixtures/nombre_edificacion_golden.json` holds, for a spread of real names
and edge cases, the reference's normalised form and `nombresCoinciden`
verdict for neighbouring pairs.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "nombre_edificacion_golden.json").read_text(encoding="utf8"))


@pytest.mark.parametrize("raw,expected", sorted(GOLDEN["norm"].items()), ids=lambda v: str(v)[:30])
def test_norm_nombre_matches_the_reference(raw, expected):
    assert rd._norm_nombre(raw) == expected


@pytest.mark.parametrize("a,b,expected", GOLDEN["matches"], ids=lambda v: str(v)[:20])
def test_nombres_coinciden_matches_the_reference_after_normalising(a, b, expected):
    assert rd._nombres_coinciden(rd._norm_nombre(a), rd._norm_nombre(b)) is expected


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA, "", "   ", "-", "..."])
def test_missing_or_symbol_only_names_normalise_to_empty(value):
    assert rd._norm_nombre(value) == ""


def test_runs_of_punctuation_and_spaces_collapse_to_one_space():
    assert rd._norm_nombre("Antonio Nari\u00f1o. Etapa 3  Torre A -B- C") == "ANTONIO NARINO ETAPA 3 TORRE A B C"


@pytest.mark.parametrize("a,b", [
    ("Antonio Nari\u00f1o. Etapa 3  Torre A -B- C", "Antonio Nari\u00f1o - Etapa 3-Torre H-Torre G"),
    ("Parroquia San Vivente de Pa\u00fal", "Parroquia San Vicente de Pa\u00fal- templo"),
])
def test_real_names_the_reference_merges_are_merged_here(a, b):
    assert rd._nombres_coinciden(rd._norm_nombre(a), rd._norm_nombre(b))


def test_distinct_tower_numbers_still_never_match():
    assert not rd._nombres_coinciden(rd._norm_nombre("Torre 1"), rd._norm_nombre("Torre 15"))
