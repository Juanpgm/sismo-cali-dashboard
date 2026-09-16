"""LAYER_TO_RAW allowlist regression for the 9 new EDE_v1 layer fields
(2026-09-15 schema change). Only 4 are real survey answers worth shipping;
the other 5 are QA-validator scratch columns or empty note/label fields and
must stay excluded so they never reach inspections.json.

Same trap as `codigoapp` (test_refresh_data_codigoapp.py, design.md ADR-7):
`fetch_survey_raw()`'s column selection
(`df[list(LAYER_TO_RAW.values()) + ["x", "y"]]`) silently drops any layer
field with no LAYER_TO_RAW entry -- an explicit ALLOWLIST, not a drop-list.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402

# ArcGIS field name -> expected raw_data label, per the brief. Collision-
# driven names: the layer's `comuna` and `barrio_vereda` fields are DIFFERENT
# survey questions from the already-normalized `comuna` (geo spatial join)
# and `barrio_vereda` (legacy typed-value RENAME_MAP target) columns, so the
# raw labels (and therefore final normalized names, since none of these are
# in RENAME_MAP) are deliberately distinct.
_EXPECTED_NEW_ENTRIES = {
    "concepto_cierre": "concepto_cierre",
    "recomendacio_evaluacion": "recomendacion_evaluacion_detallada",
    "comuna": "comuna_formulario",
    "barrio_vereda": "barrio_vereda_lista",
}

# QA-validator scratch columns / empty note fields -- intentionally excluded.
_EXCLUDED_FIELDS = [
    "nota_habilitacion_demolicion",
    "coinciden_barrio",
    "objectid_amva",
    "Barrio_validador",
    "Barrio_prueba",
]


def test_new_fields_are_mapped_in_layer_to_raw():
    for arcgis_field, raw_label in _EXPECTED_NEW_ENTRIES.items():
        assert rd.LAYER_TO_RAW.get(arcgis_field) == raw_label


def test_excluded_validator_and_note_columns_are_not_mapped():
    for field in _EXCLUDED_FIELDS:
        assert field not in rd.LAYER_TO_RAW


def test_new_raw_labels_are_not_renamed_by_rename_map():
    # Precondition: if any of these were in RENAME_MAP, normalize() would
    # rename them away from the label asserted above.
    for raw_label in _EXPECTED_NEW_ENTRIES.values():
        assert raw_label not in rd.RENAME_MAP


def test_new_raw_labels_are_not_stripped_as_pii():
    for raw_label in _EXPECTED_NEW_ENTRIES.values():
        assert raw_label not in rd.PII_COLUMNS


def _fixture_attrs(**overrides) -> dict:
    """Every LAYER_TO_RAW key defaulted to None (outFields: "*" always
    returns every attribute, even blank), plus overrides."""
    attrs = {key: None for key in rd.LAYER_TO_RAW}
    attrs.update(overrides)
    return attrs


def _apply_allowlist_pipeline(attrs: dict) -> pd.DataFrame:
    """The exact steps fetch_survey_raw() performs on raw attributes, using
    the module's own constants -- mirrors test_refresh_data_codigoapp.py."""
    row = dict(attrs)
    row["x"], row["y"] = -76.5300, 3.4200
    df = pd.DataFrame([row]).rename(columns=rd.LAYER_TO_RAW)
    for field in rd.SURVEY_DATE_FIELDS:
        col = rd.LAYER_TO_RAW[field]
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], unit="ms", errors="coerce")
    df = df.replace("", pd.NA)
    if "GlobalID" in df.columns:
        df["GlobalID"] = df["GlobalID"].astype("string").str.strip("{}").str.lower()
    return df[list(rd.LAYER_TO_RAW.values()) + ["x", "y"]]


def test_new_fields_survive_the_column_allowlist():
    attrs = _fixture_attrs(
        objectid=1,
        globalid="{ABCDEF12-3456-7890-ABCD-EF1234567890}",
        fecha_inspeccion=1755550000000,
        concepto_cierre="demolicion",
        recomendacio_evaluacion="Se recomienda evaluación estructural adicional.",
        comuna="Comuna 19",
        barrio_vereda="san_fernando_nuevo",
    )

    result = _apply_allowlist_pipeline(attrs)

    assert result.loc[0, "concepto_cierre"] == "demolicion"
    assert result.loc[0, "recomendacion_evaluacion_detallada"] == (
        "Se recomienda evaluación estructural adicional."
    )
    assert result.loc[0, "comuna_formulario"] == "Comuna 19"
    assert result.loc[0, "barrio_vereda_lista"] == "san_fernando_nuevo"


def test_legacy_record_with_all_new_fields_blank_survives_allowlist():
    """The 1700+ pre-2026-09-13 rows: every new field is blank (the layer
    always serves the attribute key, but Survey123 never asked the
    question). Must not crash and must surface as NA, not KeyError."""
    attrs = _fixture_attrs(
        objectid=2,
        globalid="{00000000-0000-0000-0000-000000000000}",
        fecha_inspeccion=1700000000000,
    )

    result = _apply_allowlist_pipeline(attrs)

    for col in ("concepto_cierre", "recomendacion_evaluacion_detallada",
                "comuna_formulario", "barrio_vereda_lista"):
        assert col in result.columns
        assert pd.isna(result.loc[0, col])
