"""backend/tests/jobs/test_dashboard_refresh.py — offline `--check`-style
idempotency/watermark fixtures for the absorbed dashboard-refresh job (task
7.1, RED first; design.md ADR-6; job-scheduling spec "Watermark And
Idempotent-Write Behavior Preserved" (dashboard-refresh row)).

Mirrors the offline self-check convention the job family already uses:
`scripts/fetch_reportes_api.py --check` (validate already-written files, no
network) and `integracion_F1/cruce_sticker.py --check` (pure self-check, no
network, no Firestore). `dashboard-refresh` has no Firestore watermark like
`cruce-sticker` — its idempotency guarantee is file-level: dedup-by-id +
sorted output (byte-identical `reportes.json` for an unchanged dataset,
`meta_guard` never publishing empty/broken data over good prior data).

No real network/subprocess/Blob calls anywhere in this file: every test
exercises the job's pure helpers directly, or `main()` with `--check` in
argv (which — like `cruce_sticker.py`'s own `--check` — returns before any
network/Firestore/subprocess code path is reached).
"""
from __future__ import annotations

import json
import sys

import pytest

from app.jobs import dashboard_refresh as job


# --- _raw_record_mapper: full-field strip (reportes.json needs every ------
# --- analytic field, unlike atencionsismo's default AGG-only mapper) ------


def test_raw_record_mapper_strips_pii_and_heavy_fields_keeps_analytics():
    rep = {
        "id": "r1",
        "estadoVerificacion": "Reportado",
        "afectacion": "DAÑO ESTRUCTURAL",
        "comuna": "Comuna 3",
        "habitabilidad": "No habitable",
        "tipoInmueble": "Casa",
        "nombre": "Juan Perez",
        "telefono": "3001234567",
        "cedula": "123456",
        "correo": "juan@example.com",
        "matriculaProfesional": "MP-1",
        "fotografiasEvaluacion": ["https://example.com/x.jpg"],
        "mensajes": [{"texto": "hola"}],
        "latitud": "3.42",
        "longitud": "-76.53",
    }

    out = job._raw_record_mapper(rep)

    for pii_field in job.PII_FIELDS:
        assert pii_field not in out, pii_field
    for heavy_field in job.HEAVY_FIELDS:
        assert heavy_field not in out, heavy_field
    assert out["id"] == "r1"
    assert out["estadoVerificacion"] == "Reportado"
    assert out["afectacion"] == "DAÑO ESTRUCTURAL"
    assert out["lat"] == 3.42
    assert out["lng"] == -76.53


@pytest.mark.parametrize(
    "latitud,longitud",
    [(None, None), ("", ""), ("0", "0"), ("n/a", "n/a")],
)
def test_raw_record_mapper_nulls_unparseable_or_zero_zero_coords(latitud, longitud):
    out = job._raw_record_mapper({"id": "x", "latitud": latitud, "longitud": longitud})

    assert out["lat"] is None
    assert out["lng"] is None


# --- _dedupe_sorted: idempotent output across overlapping day windows -----


def test_dedupe_sorted_collapses_duplicate_ids_first_seen_wins():
    a = {"id": "z1", "estadoVerificacion": "Reportado"}
    b = {"id": "a1", "estadoVerificacion": "Verificado"}

    out = job._dedupe_sorted([a, b, a])

    assert out == [b, a]  # sorted by id: "a1" before "z1"


def test_dedupe_sorted_is_stable_regardless_of_arrival_order():
    a = {"id": "z1"}
    b = {"id": "a1"}

    assert job._dedupe_sorted([a, b]) == job._dedupe_sorted([b, a]) == [b, a]


def test_dedupe_sorted_drops_records_without_an_id():
    out = job._dedupe_sorted([{"id": "a1"}, {"estadoVerificacion": "sin id"}, {"id": None}])

    assert out == [{"id": "a1"}]


# --- meta_guard: never publish empty/broken data over good prior data -----


def test_meta_guard_passes_and_returns_row_count(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    (tmp_path / "meta.json").write_text(
        json.dumps({"row_count": 42, "source": "survey123"}), encoding="utf-8"
    )

    assert job._meta_guard() == 42


def test_meta_guard_raises_when_meta_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)

    with pytest.raises(RuntimeError):
        job._meta_guard()


@pytest.mark.parametrize("row_count", [0, -1])
def test_meta_guard_raises_when_row_count_not_positive(tmp_path, monkeypatch, row_count):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    (tmp_path / "meta.json").write_text(json.dumps({"row_count": row_count}), encoding="utf-8")

    with pytest.raises(RuntimeError):
        job._meta_guard()


# --- _publish_all: missing files are skipped, never crash, never re-fetch -


def test_publish_all_skips_missing_files_without_calling_blob(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        job.blob_sync, "upload", lambda local, pathname, *a, **k: calls.append(pathname) or "https://blob/x"
    )

    job._publish_all()

    assert calls == []


def test_publish_all_uploads_only_existing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    (tmp_path / "meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "reportes.json").write_text("[]", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(
        job.blob_sync, "upload", lambda local, pathname, *a, **k: calls.append(pathname) or "https://blob/x"
    )

    job._publish_all()

    assert calls == ["data/meta.json", "data/reportes.json"]


# --- --check: offline self-check, zero network/subprocess/Firestore calls -


def test_main_check_flag_runs_offline_selfcheck_and_returns_zero(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["dashboard_refresh.py", "--check"])

    assert job.main() == 0
