"""Full normalize() smoke test on a legacy-shaped raw frame that has NONE of
the 4 new survey123-new-fields columns at all (not even present, not just
blank) -- simulating the 1700+ pre-2026-09-13 rows / any raw_data source that
predates the EDE_v1 schema change. The whole pipeline (rename, dates,
spatial join, id_edan, address norm, dup grouping) must run to completion
without KeyError/AttributeError, and must not fabricate the new columns out
of thin air.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402

_NEW_RAW_LABELS = {
    "concepto_cierre",
    "recomendacion_evaluacion_detallada",
    "comuna_formulario",
    "barrio_vereda_lista",
}


def test_normalize_runs_to_completion_with_no_new_survey_field_columns():
    row = {v: None for v in rd.LAYER_TO_RAW.values() if v not in _NEW_RAW_LABELS}
    row.update({
        "ObjectID": 1,
        "GlobalID": "aaaaaaaa-1111-2222-3333-444444444444",
        "Fecha de Inspección:": pd.Timestamp("2026-08-01"),
        "Hora:": "10:00",
        "Municipio:": "Cali",
        "Barrio/vereda:": "San Fernando",
        "Dirección:": "Calle 5 # 10-20",
        "x": -76.53, "y": 3.42,
    })
    df = pd.DataFrame([row])

    out = rd.normalize(df)

    assert len(out) == 1
    for new_col in _NEW_RAW_LABELS:
        assert new_col not in out.columns
