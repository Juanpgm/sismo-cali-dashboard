"""backend/tests/test_refresh_data_codigoapp.py — the load-bearing
one-liner (task 1.1, RED first; design.md ADR-7; spec.md "Requirement:
`codigoapp` survives the Survey123 ingestion pipeline").

`scripts/refresh_data.py:1111`'s column allowlist
(`df[list(LAYER_TO_RAW.values()) + ["x", "y"]]`) silently drops any layer
field with no `LAYER_TO_RAW` entry. `codigoapp` ("Codigo generado
aplicativo") is fetched from the Survey123 layer (`outFields: "*"`,
line 1070) but has no entry — verified empirically: `'codigoapp' in
record` is `False` for all 1091 live published records before this fix.

Feeds a fixture ArcGIS feature's attributes through the EXACT rename +
date-coercion + allowlist steps `fetch_survey_raw()` performs
(`scripts/refresh_data.py:1100-1111`), using the module's own
`LAYER_TO_RAW`/`SURVEY_DATE_FIELDS` constants — not a re-implementation of
the pipeline, so this test breaks if that logic ever changes shape, not
just if `codigoapp` regresses. `scripts/` is not a package (no
`__init__.py`), so the module is loaded by file path via `importlib`,
with `scripts/` added to `sys.path` first so `refresh_data.py`'s own
`from address_norm import normalize_address` / `from geocode_validate
import ...` (its sibling, non-package-relative imports) resolve.

MUST fail before task 1.2 adds the `LAYER_TO_RAW` entry.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_refresh_data():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("refresh_data", SCRIPTS_DIR / "refresh_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def refresh_data():
    return _load_refresh_data()


def _fixture_feature(refresh_data, codigoapp: str) -> dict:
    """Every `LAYER_TO_RAW` key (as the live layer query's `outFields: "*"`
    always returns every attribute, even blank) defaulted to `None`, with a
    handful of real values plus `codigoapp` overlaid — enough to exercise
    the rename + allowlist steps without needing the whole ~90-field
    Survey123 schema hand-typed here."""
    attrs = {key: None for key in refresh_data.LAYER_TO_RAW}
    attrs.update({
        "objectid": 1,
        "globalid": "{ABCDEF12-3456-7890-ABCD-EF1234567890}",
        "fecha_inspeccion": 1755550000000,  # epoch ms, per SURVEY_DATE_FIELDS
        "direccion": "Calle 1 # 2-3",
        "barrio": "Barrio Centro",
        "codigoapp": codigoapp,
    })
    return attrs


def _apply_allowlist_pipeline(refresh_data, attrs: dict) -> pd.DataFrame:
    """The exact steps `fetch_survey_raw()` performs on the raw attributes
    dict, using the module's own constants — see the module docstring."""
    row = dict(attrs)
    row["x"], row["y"] = -76.5300, 3.4200  # geometry, as fetch_survey_raw() injects it
    df = pd.DataFrame([row]).rename(columns=refresh_data.LAYER_TO_RAW)
    for field in refresh_data.SURVEY_DATE_FIELDS:
        col = refresh_data.LAYER_TO_RAW[field]
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], unit="ms", errors="coerce")
    df = df.replace("", pd.NA)
    if "GlobalID" in df.columns:
        df["GlobalID"] = df["GlobalID"].astype("string").str.strip("{}").str.lower()
    return df[list(refresh_data.LAYER_TO_RAW.values()) + ["x", "y"]]


def test_codigoapp_survives_the_column_allowlist(refresh_data):
    attrs = _fixture_feature(refresh_data, "PLN-14832-9C4A1F0B")

    result = _apply_allowlist_pipeline(refresh_data, attrs)

    assert "codigoapp" in result.columns
    assert result.loc[0, "codigoapp"] == "PLN-14832-9C4A1F0B"


def test_codigoapp_is_not_in_cols_a_eliminar(refresh_data):
    # (a) — the other place the column could vanish before rename/allowlist.
    assert "codigoapp" not in refresh_data.COLS_A_ELIMINAR


def test_codigoapp_is_not_renamed_by_rename_map(refresh_data):
    # (b) precondition — if it were in RENAME_MAP, normalize() would change
    # its name and it would no longer reach inspections.json as `codigoapp`.
    assert "codigoapp" not in refresh_data.RENAME_MAP


def test_codigoapp_is_not_stripped_as_pii(refresh_data):
    # (b) — drop_pii() must never remove it.
    assert "codigoapp" not in refresh_data.PII_COLUMNS
