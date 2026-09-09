"""Data-entry error: `fecha_inspeccion` typed years before the real submission.

Verified on the live dataset (2026-09-09): across 1728 records with both
fields, the gap between `CreationDate` (system submission timestamp, never
false) and `fecha_inspeccion` (field-reported, free date-picker) is 0-17 days
(median 3, p99 11) -- a late submission of a real inspection. One record
(GlobalID d83f01ee-45d7-4568-82a5-80c08c54882a) had `fecha_inspeccion`
"2022-09-15" against a `CreationDate` of "2026-08-28" -- a 1443-day gap,
clearly a mistyped year, not a legitimately late submission.

`add_date_fields` already corrects the mirror case (`fecha_inspeccion` AFTER
`CreationDate` -- the "+7 days" form bug / a future date): see the existing
comment in scripts/refresh_data.py. This extends the same reasoning to the
opposite direction: a `fecha_inspeccion` implausibly far BEFORE
`CreationDate` is also corrected to `CreationDate`, using a 180-day
threshold -- more than 10x the largest observed legitimate gap, so no real
late submission is ever touched by this rule.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


def _ts(value):
    """Mirrors the real call site: `fetch_survey_raw` (refresh_data.py:1284-1286)
    converts `fecha_inspeccion`/`CreationDate`/`EditDate` from epoch-ms to
    `datetime64` BEFORE `normalize()` calls `add_date_fields`, so by the time
    this function runs these columns are never raw text. Feeding it plain
    ISO strings instead would (accidentally) exercise `dayfirst=True`'s
    string-parsing path, which has its own day/month-swap ambiguity for any
    day <= 12 (e.g. "2026-03-02" -> "2026-02-03") -- a real but SEPARATE
    latent bug, unreachable here because this column is never text-typed at
    this call site. Using `pd.Timestamp` for both fields keeps this test
    honest about that contract."""
    return pd.Timestamp(value) if value is not None else pd.NaT


def _df(rows):
    df = pd.DataFrame(rows)
    if "hora" not in df.columns:
        df["hora"] = None
    return df


def test_a_fecha_inspeccion_years_before_creation_is_corrected_to_creation_date():
    df = rd.add_date_fields(_df([
        {"GlobalID": "typo", "fecha_inspeccion": _ts("2022-09-15"), "CreationDate": _ts("2026-08-28T16:12:40.346")},
    ]))
    assert df.loc[0, "fecha_inspeccion"] == "2026-08-28"


def test_a_legitimate_late_submission_within_180_days_is_left_untouched():
    """The largest observed real gap is 17 days; 30 days is still ordinary
    (an inspector catching up on a backlog), and must not be "corrected"."""
    df = rd.add_date_fields(_df([
        {"GlobalID": "tardio", "fecha_inspeccion": _ts("2026-07-30"), "CreationDate": _ts("2026-08-29T09:00:00")},
    ]))
    assert df.loc[0, "fecha_inspeccion"] == "2026-07-30"


def test_the_180_day_boundary_is_exclusive_on_the_safe_side():
    """Exactly 180 days is still left alone; the rule only fires strictly
    beyond it, so the threshold itself never becomes an edge-case trap."""
    df = rd.add_date_fields(_df([
        {"GlobalID": "limite", "fecha_inspeccion": _ts("2026-03-02"), "CreationDate": _ts("2026-08-29T00:00:00")},
    ]))
    assert df.loc[0, "fecha_inspeccion"] == "2026-03-02"


def test_the_future_date_rule_still_wins_and_is_not_broken_by_this_change():
    df = rd.add_date_fields(_df([
        {"GlobalID": "futuro", "fecha_inspeccion": _ts("2026-09-05"), "CreationDate": _ts("2026-08-29T00:00:00")},
    ]))
    assert df.loc[0, "fecha_inspeccion"] == "2026-08-29"


def test_nothing_is_dropped_and_unrelated_rows_are_untouched():
    df = rd.add_date_fields(_df([
        {"GlobalID": "typo", "fecha_inspeccion": _ts("2022-09-15"), "CreationDate": _ts("2026-08-28T16:12:40.346")},
        {"GlobalID": "normal", "fecha_inspeccion": _ts("2026-08-20"), "CreationDate": _ts("2026-08-22T10:00:00")},
    ]))
    assert len(df) == 2
    assert set(df["GlobalID"]) == {"typo", "normal"}
    assert df.loc[df["GlobalID"] == "normal", "fecha_inspeccion"].iloc[0] == "2026-08-20"


def test_a_blank_creation_date_leaves_fecha_inspeccion_untouched():
    """No system truth to compare against -- never guess, never blank it out."""
    df = rd.add_date_fields(_df([
        {"GlobalID": "sin_creation", "fecha_inspeccion": _ts("2022-09-15"), "CreationDate": pd.NaT},
    ]))
    assert df.loc[0, "fecha_inspeccion"] == "2022-09-15"


def test_a_blank_fecha_inspeccion_stays_blank():
    df = rd.add_date_fields(_df([
        {"GlobalID": "sin_fecha", "fecha_inspeccion": pd.NaT, "CreationDate": _ts("2026-08-28T00:00:00")},
    ]))
    assert pd.isna(df.loc[0, "fecha_inspeccion"]) or df.loc[0, "fecha_inspeccion"] is None


# NOTE (out of scope for this fix, kept here for the next person who touches
# `add_date_fields`): `pd.to_datetime(..., dayfirst=True)` reorders day/month
# for ANY string input with day <= 12 -- even a 4-digit-year ISO string WITH
# a time component, e.g. "2026-08-05T00:00:00" -> 2026-05-08. This is
# unreachable today because `fecha_inspeccion` is always `datetime64` by the
# time `add_date_fields` runs (see `_ts` above) -- `dayfirst` is a no-op on
# an already-typed column. It would only bite if a future code path ever
# fed this function raw text for that column.
