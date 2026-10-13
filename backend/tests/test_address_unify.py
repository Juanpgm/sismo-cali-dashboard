"""`address_unify_key` must behave exactly like atencionsismo's `addressUnifyKey`.

Both dashboards bucket survey inspections into buildings by this key; if the
two disagree, one of them shows more (or fewer) buildings than the other.

`fixtures/address_unify_golden.json` maps raw address -> the key the REFERENCE
implementation (atencionsismo `lib/geo/normalize-address.ts`) produced. It
holds every address literal from that repo's own `normalize-address.check.ts`
(the golden pairs behind `edan-transform` / `edan-dedup`'s `direccion_norm`),
the variants listed in the parity request (abbreviations, '#'/'No'/'N°',
plate separators, BIS suffixes, noise words, unicode, giant strings) and a
deterministic sample of real survey addresses. To refresh it, re-run the
reference over the same inputs; never hand-edit a value to make a test pass.

Known reference quirks are pinned in the golden file on purpose (e.g. a
space-separated plate "KR 47 # 4 157" keys as `cra:47:4`, NOT `cra:47:4-157`):
parity means copying them, and the quirks are reported upstream instead.
"""
import json
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from address_unify import address_unify_key  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "address_unify_golden.json").read_text(encoding="utf8"))


def test_the_golden_file_is_big_enough_to_mean_something():
    assert len(GOLDEN) >= 300
    assert sum(1 for k in GOLDEN.values() if k == "") < len(GOLDEN) / 10


@pytest.mark.parametrize("raw,expected", sorted(GOLDEN.items()), ids=lambda v: str(v)[:40])
def test_matches_the_reference_implementation(raw, expected):
    assert address_unify_key(raw) == expected


# Same building, different spelling -- every row of a group must share the key.
SAME_BUILDING = [
    ("cra:47:4-157", ["KR 47 # 4-157", "KR 47 # 4 - 157", "CRA 47 No. 4-157", "Cr 47 #4-157 barrio Tequendama",
                      "   KR   47   #  4-157   ", "kr 47 # 4-157", "KR 47 # 4-157,", "KR 47 # 4-157."]),
    ("cra:72:10-40", ["KR 72 # 10-40", "CRA 72 No 10-40 Madrigal", "Carrera 72 # 10-40 apto 301"]),
    ("cl:9:42-78", ["CL 9 # 42-78", "Calle 9 No. 42-78 Kaliva", "CALLE 9 #42-78 casa 2"]),
    ("cl:5:60-64", ["CL 5 # 60-64", "Calle 5 # 60 - 64", "CLL 5 #60-64", "CL. 5 #60-64, Cali, Valle del Cauca"]),
    ("cl:10:42a-2", ["Calle 10 No. 42a 02", "Calle 10 Nro 42a 02"]),
    ("cl:80:45-23", ["Calle 80 No. 45-23, barrio el peñón", "CL 80 # 45-23, Barrio El Peñón",
                     "CALLE 80 #45-23 BARRIO EL PEÑÓN"]),
    ("cl:2a:bis:94", ["CL 2A BIS # 94", "CL 2 A bis # 94"]),
    ("av:3:n:15-20", ["AV 3N # 15-20", "Avenida 3 Norte # 15-20"]),
    ("dg:21:8-30", ["DG 21 # 8-30", "Diagonal 21 # 8-30", "Dg 21 8-30"]),
    ("tv:21:8-30", ["TV 21 # 8-30", "Transversal 21 No. 8 30"]),
    ("cra:36b:5-118", ["CRRA 36 B # 05-118", "Carrera 36 b # 05-118"]),
    ("cl:52:3-20", ["CALLE 52 # 03-20", "cl 52 # 03 - 20", "CL 52 # 3-20, Salomia, Cali"]),
    ("cl:6:42-96|torre:2", ["Calle 6 #42-96 Torre 2", "Cl 6 42-96 Trr 2", "Cl 6 # 42-96 Torre 2"]),
]


@pytest.mark.parametrize("expected,variants", SAME_BUILDING, ids=[e for e, _ in SAME_BUILDING])
def test_spelling_variants_of_one_building_share_one_key(expected, variants):
    assert {address_unify_key(v) for v in variants} == {expected}


# Genuinely different buildings must NOT collapse.
DIFFERENT_BUILDING = [
    ("KR 47 # 4-157", "KR 47 # 4157"),      # plate "4-157" vs "4157"
    ("KR 47 # 4-157", "KR 47 # 4-158"),
    ("Calle 5 #10-20", "Calle 6 #10-20"),   # different street
    ("Calle 5 Norte #10-20", "Calle 5 #10-20"),
    ("Calle 6 Bis #42-96", "Calle 6 #42-96"),
    ("Calle 6 #42-96 Torre 2", "Calle 6 #42-96 Torre 3"),
    ("CL 6 # 42-96", "KR 6 # 42-96"),       # calle vs carrera
    ("Cl 3 A #19-39", "Cl 3 A #19-28"),
]


@pytest.mark.parametrize("a,b", DIFFERENT_BUILDING)
def test_different_buildings_keep_different_keys(a, b):
    assert address_unify_key(a) != address_unify_key(b)


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA, "", " ", "   ", "\t\n", " ", "﻿"])
def test_missing_or_blank_input_has_no_key(value):
    assert address_unify_key(value) == ""


@pytest.mark.parametrize("value", ["-", "--", "#", "# #", "N/A", "null", "None", "nan"])
def test_placeholder_strings_never_raise_and_never_look_like_a_street(value):
    key = address_unify_key(value)
    assert isinstance(key, str)
    assert ":" not in key


@pytest.mark.parametrize("value,expected", [
    (12345, "12345"), (12345.0, "12345"), ("12345", "12345"), ("007", "7"), (0, "0"),
    (-5, "5"),
])
def test_numeric_only_input_is_keyed_like_its_text(value, expected):
    assert address_unify_key(value) == expected


@pytest.mark.parametrize("value", [
    "123 Main Street, Springfield", "Rua das Flores 123", "東京都千代田区1-1",
    "Ünïcödé Straße 5 # 3-4", "\U0001f3e0 KR 5 # 1-2", "İSTANBUL CL 5 # 3-20",
])
def test_non_colombian_and_unicode_strings_do_not_crash_and_are_stable(value):
    first = address_unify_key(value)
    assert isinstance(first, str)
    assert address_unify_key(value) == first


def test_unicode_whitespace_around_the_plate_is_tolerated_like_the_reference():
    assert address_unify_key("KR 47 # 4-157") == "cra:47:4-157"
    assert address_unify_key("CL 5 # 3-20") == "cl:5:3-20"
    assert address_unify_key("KR 47\t#\n4-157") == address_unify_key("KR 47 # 4-157")


def test_en_dash_is_a_plate_separator():
    assert address_unify_key("KR 47 # 4–157") == "cra:47:4-157"


def test_plate_digits_past_double_precision_follow_javascript_number_printing():
    """The reference un-pads plates with String(Number(n)), which rounds past
    2**53 and prints 1e21+ in exponent form. Parity includes that."""
    assert address_unify_key("Cl 1 # 9007199254740993-1") == "cl:1:9007199254740992-1"
    assert address_unify_key("Cl 1 # 12345678901234567890123-1") == "cl:1:1.2345678901234568e+22-1"
    assert address_unify_key("CL 5 # 00-007") == "cl:5:0-7"


def test_a_digit_run_too_long_for_a_double_does_not_crash():
    assert address_unify_key("Cl 1 # " + "9" * 400 + "-1") == "cl:1:Infinity-1"


def test_giant_strings_finish_quickly_and_do_not_blow_up():
    started = time.perf_counter()
    assert address_unify_key("x" * 5000) == "x" * 5000
    assert address_unify_key("KR 47 # 4-157 " + "barrio la flora " * 400) == "cra:47:4-157"
    assert address_unify_key("1 " * 3000) == "1" * 3000
    assert address_unify_key("CL " * 2000 + "5 # 1-2") == "cl:5:1-2"  # pathological repetition
    assert time.perf_counter() - started < 5


def test_key_ignores_case_and_surrounding_whitespace_on_real_looking_input():
    real = [raw for raw, key in GOLDEN.items() if ":" in key and len(raw) < 80]
    assert len(real) > 100
    for raw in real:
        assert address_unify_key(f"  {raw.upper()} \n") == address_unify_key(raw), raw


def test_nan_float_is_not_confused_with_the_text_nan():
    assert address_unify_key(float("nan")) == ""
    assert address_unify_key("nan") == "nan"
