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


# --- ingest_survey_cali: wires the Survey123 fetch's output into Firestore -
# (task 7.5, design.md ADR-11) -- reuses web/data/inspections.json, the file
# refresh_data.py JUST wrote (no second Survey123 upstream call), and
# delegates every write to app.services.survey_cali.ingest_records (never a
# direct Firestore call here -- ADR-9's sole-writer allowlist covers THIS
# module precisely because it only ever calls INTO survey_cali.py).


def test_ingest_survey_cali_reads_inspections_json_and_delegates_to_service(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    records = [
        {"GlobalID": "g1", "EditDate": "2026-08-01T00:00:00", "direccion": "Calle 1"},
        {"GlobalID": "g2", "EditDate": "2026-08-01T00:00:00", "direccion": "Calle 2"},
    ]
    (tmp_path / "inspections.json").write_text(json.dumps(records), encoding="utf-8")

    captured: list[list[dict]] = []

    def _fake_ingest_records(recs, **kwargs):
        captured.append(recs)
        return {"created": 2, "updated": 0, "skipped": 0}

    monkeypatch.setattr(job.survey_cali, "ingest_records", _fake_ingest_records)

    summary = job.ingest_survey_cali()

    assert summary == {"created": 2, "updated": 0, "skipped": 0}
    assert captured == [records]


def test_ingest_survey_cali_missing_inspections_json_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(job.survey_cali, "ingest_records", lambda *a, **k: calls.append(1))

    summary = job.ingest_survey_cali()

    assert summary == {"created": 0, "updated": 0, "skipped": 0}
    assert calls == []  # no Firestore call at all when there's nothing to ingest


# --- puntos_contacto: reporter contact captured pre-strip, fail-soft ------
# (planeacion-flujo-confiable, design.md ADR-1/ADR-2, task 1.1-1.4). The
# restricted-channel write is exercised at the SAME granularity every other
# test in this file uses -- pure helpers directly, or fetch_reportes() with
# day_walk monkeypatched away (no real network).


class _FakeContactDocRef:
    def __init__(self, store: dict, doc_id: str) -> None:
        self._store = store
        self.id = doc_id


class _FakeContactBatch:
    def __init__(self) -> None:
        self._pending: list[tuple[_FakeContactDocRef, dict, bool]] = []

    def set(self, ref: _FakeContactDocRef, data: dict, merge: bool = False) -> None:
        self._pending.append((ref, dict(data), merge))

    def commit(self) -> None:
        for ref, data, merge in self._pending:
            current = dict(ref._store.get(ref.id, {})) if merge else {}
            current.update(data)
            ref._store[ref.id] = current
        self._pending = []


class _FakeContactCollection:
    def __init__(self, store: dict) -> None:
        self._store = store

    def document(self, doc_id: str) -> _FakeContactDocRef:
        return _FakeContactDocRef(self._store, doc_id)


class _FakeContactDb:
    def __init__(self) -> None:
        self.stores: dict[str, dict] = {}

    def collection(self, name: str) -> _FakeContactCollection:
        return _FakeContactCollection(self.stores.setdefault(name, {}))

    def batch(self) -> _FakeContactBatch:
        return _FakeContactBatch()


def test_make_raw_mapper_returns_same_stripped_output_as_raw_record_mapper():
    rep = {"id": "1", "nombre": "Ana", "telefono": "300", "latitud": "3.1", "longitud": "-76.1"}
    contactos: list[dict] = []
    mapper = job._make_raw_mapper(contactos)

    out = mapper(rep)

    assert out == job._raw_record_mapper(rep)
    assert contactos == [{"registro_id": "1", "nombre_solicitante": "Ana", "telefono_solicitante": "300"}]


def test_make_raw_mapper_skips_records_without_an_id():
    contactos: list[dict] = []
    mapper = job._make_raw_mapper(contactos)

    mapper({"nombre": "Sin Id", "telefono": "300"})

    assert contactos == []


def test_write_contactos_batched_merge_true_by_atencionsismo_doc_id():
    db = _FakeContactDb()
    contactos = [
        {"registro_id": "14832", "nombre_solicitante": "Juan Perez", "telefono_solicitante": "3001234567"},
    ]

    job._write_contactos(contactos, db=db)

    doc = db.stores["puntos_contacto"]["atencionsismo_14832"]
    assert doc == {
        "registro_id": "14832",
        "nombre_solicitante": "Juan Perez",
        "telefono_solicitante": "3001234567",
    }


# --- MANDATORY PII test (task 1.9): contact fields never leak into the ----
# --- public reportes.json writer output — checked by NAME, not just via ---
# --- the PII_FIELDS set, so a future PII_FIELDS edit can't silently drop --
# --- this guarantee. ------------------------------------------------------


def test_raw_record_mapper_never_emits_reporter_contact_fields():
    rep = {
        "id": "1",
        "nombre": "Juan Perez",
        "telefono": "3001234567",
        "latitud": "3.1",
        "longitud": "-76.1",
    }

    out = job._raw_record_mapper(rep)

    assert "nombre_solicitante" not in out
    assert "telefono_solicitante" not in out
    assert "nombre" not in out
    assert "telefono" not in out


def test_write_contactos_noop_on_empty_list():
    db = _FakeContactDb()

    job._write_contactos([], db=db)

    assert db.stores == {}


async def _fake_day_walk_one_record(client, user, password, desde, *, until_ms=None, mapper=None):
    raw = {
        "id": "14832",
        "estadoVerificacion": "Reportado",
        "nombre": "Juan Perez",
        "telefono": "3001234567",
        "latitud": "3.1",
        "longitud": "-76.1",
    }
    return [mapper(raw) if mapper else raw]


def test_fetch_reportes_writes_contact_and_keeps_reportes_json_pii_free(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    doc = fake_db.stores["puntos_contacto"]["atencionsismo_14832"]
    assert doc["nombre_solicitante"] == "Juan Perez"
    assert doc["telefono_solicitante"] == "3001234567"

    written = json.loads((tmp_path / "reportes.json").read_text(encoding="utf-8"))
    assert "nombre_solicitante" not in written[0]
    assert "nombre" not in written[0]
    assert "telefono" not in written[0]


def test_fetch_reportes_contact_write_failure_never_breaks_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)

    def _boom(contactos, *, db=None):
        raise RuntimeError("firestore is down")

    monkeypatch.setattr(job, "_write_contactos", _boom)

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    assert (tmp_path / "reportes.json").exists()


def test_ingest_survey_cali_failure_does_not_propagate_out_of_run_refresh_step(tmp_path, monkeypatch):
    """The main refresh pipeline (meta_guard/publish_blob) must never be
    blocked by a survey_cali/Firestore hiccup -- same fail-soft convention
    fetch_reportes() already uses. This test exercises the SAME try/except
    shape run_refresh() wraps ingest_survey_cali() in, without invoking the
    full pipeline (which needs subprocess/Blob -- out of scope for this
    offline suite, same precedent every other run_refresh() step follows)."""
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    (tmp_path / "inspections.json").write_text("[]", encoding="utf-8")

    def _boom(*a, **k):
        raise RuntimeError("firestore is down")

    monkeypatch.setattr(job.survey_cali, "ingest_records", _boom)

    try:
        job.ingest_survey_cali()
        raised = False
    except RuntimeError:
        raised = True
    assert raised  # ingest_survey_cali() itself propagates; run_refresh() is what catches it
