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
from pathlib import Path

import httpx
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


# --- Blob hash-map diff-gate (quota fix): skip unchanged records, fail --
# --- open (write everything) on ANY Blob problem -- a bug that makes -----
# --- contacts silently STOP being written is worse than the quota issue. -


def _contact(rid, nombre, telefono):
    return {"registro_id": rid, "nombre_solicitante": nombre, "telefono_solicitante": telefono}


def test_write_contactos_skips_unchanged_records_when_hashmap_available(monkeypatch):
    db = _FakeContactDb()
    contactos = [_contact("1", "Juan", "300"), _contact("2", "Ana", "301")]
    old_hashes = {
        "1": job._contacto_hash(contactos[0]),
        "2": job._contacto_hash(contactos[1]),
    }
    monkeypatch.setattr(job, "_load_contacto_hashes", lambda: old_hashes)
    published: list[dict] = []
    monkeypatch.setattr(job, "_publish_contacto_hashes", lambda h: published.append(h))

    job._write_contactos(contactos, db=db)

    assert db.stores.get("puntos_contacto", {}) == {}
    assert published == []


def test_write_contactos_writes_only_changed_and_new_records(monkeypatch):
    db = _FakeContactDb()
    unchanged = _contact("1", "Juan", "300")
    changed = _contact("2", "Ana", "301")
    new = _contact("3", "Luis", "302")
    old_hashes = {
        "1": job._contacto_hash(unchanged),
        "2": "stale-hash-does-not-match",
        # "3" absent -> new
    }
    monkeypatch.setattr(job, "_load_contacto_hashes", lambda: old_hashes)
    published: list[dict] = []
    monkeypatch.setattr(job, "_publish_contacto_hashes", lambda h: published.append(h))

    job._write_contactos([unchanged, changed, new], db=db)

    written = db.stores["puntos_contacto"]
    assert set(written.keys()) == {"atencionsismo_2", "atencionsismo_3"}
    assert len(published) == 1
    assert set(published[0].keys()) == {"1", "2", "3"}


def test_write_contactos_writes_all_when_hashmap_fetch_fails(monkeypatch):
    db = _FakeContactDb()
    contactos = [_contact("1", "Juan", "300"), _contact("2", "Ana", "301")]
    monkeypatch.setattr(job, "_load_contacto_hashes", lambda: None)
    published: list[dict] = []
    monkeypatch.setattr(job, "_publish_contacto_hashes", lambda h: published.append(h))

    job._write_contactos(contactos, db=db)

    written = db.stores["puntos_contacto"]
    assert set(written.keys()) == {"atencionsismo_1", "atencionsismo_2"}
    assert len(published) == 1


def test_load_contacto_hashes_returns_none_on_blob_failure(monkeypatch, tmp_path):
    def _sys_exit_download(pathname, local_path):
        raise SystemExit("no token")

    monkeypatch.setattr(job.blob_sync, "download", _sys_exit_download)
    assert job._load_contacto_hashes() is None

    def _generic_exception_download(pathname, local_path):
        raise RuntimeError("network down")

    monkeypatch.setattr(job.blob_sync, "download", _generic_exception_download)
    assert job._load_contacto_hashes() is None

    def _malformed_json_download(pathname, local_path):
        Path(local_path).write_text("not json{{{", encoding="utf-8")
        return True

    monkeypatch.setattr(job.blob_sync, "download", _malformed_json_download)
    assert job._load_contacto_hashes() is None

    # Syntactically valid JSON but the wrong shape (not a {id: str} map) must
    # also fall back to None instead of propagating a non-dict downstream.
    for wrong_shaped_payload in ("[]", "0", '"x"', '{"1": 123}'):

        def _wrong_shape_download(pathname, local_path, payload=wrong_shaped_payload):
            Path(local_path).write_text(payload, encoding="utf-8")
            return True

        monkeypatch.setattr(job.blob_sync, "download", _wrong_shape_download)
        assert job._load_contacto_hashes() is None


def test_malformed_hashmap_payload_triggers_full_fallback_write(monkeypatch):
    def _wrong_shape_download(pathname, local_path):
        Path(local_path).write_text("[]", encoding="utf-8")
        return True

    monkeypatch.setattr(job.blob_sync, "download", _wrong_shape_download)
    published: list[dict] = []
    monkeypatch.setattr(job, "_publish_contacto_hashes", lambda h: published.append(h))
    db = _FakeContactDb()

    job._write_contactos([_contact("1", "Juan", "300"), _contact("2", "Ana", "301")], db=db)

    written = db.stores["puntos_contacto"]
    assert set(written.keys()) == {"atencionsismo_1", "atencionsismo_2"}
    assert len(published) == 1


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


# --- reportes_ciudadanos.json: lightweight public projection written -----
# --- right after reportes.json, same never-publish-empty guard (design D5,
# --- Task 6). ---------------------------------------------------------------


def test_fetch_reportes_also_writes_reportes_ciudadanos(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    data = json.loads((tmp_path / "reportes_ciudadanos.json").read_text(encoding="utf-8"))
    assert isinstance(data, list) and data and "estado" in data[0] and "nombre" not in data[0]
    assert data[0]["id"] == "14832"
    assert ("reportes_ciudadanos.json", "data/reportes_ciudadanos.json") in job._PUBLISH_FILES


async def _fake_day_walk_empty(client, user, password, desde, *, until_ms=None, mapper=None):
    return []


def test_fetch_reportes_continues_when_ciudadanos_projection_fails(tmp_path, monkeypatch):
    # The projection sits AFTER reportes_meta.json/reportes_agg.json are
    # written, and is fail-soft: a bug in the projection must never leave
    # the main reportes.json/meta/agg trio inconsistent (design D5).
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    def _boom(records):
        raise ValueError("boom")

    monkeypatch.setattr(job, "build_snapshot", _boom)

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    assert (tmp_path / "reportes_agg.json").exists()
    assert not (tmp_path / "reportes_ciudadanos.json").exists()


def test_fetch_reportes_zero_records_keeps_previous_files_and_skips_ciudadanos(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_empty)
    (tmp_path / "reportes.json").write_text('[{"id": "old"}]', encoding="utf-8")
    (tmp_path / "reportes_ciudadanos.json").write_text('[{"id": "old-c"}]', encoding="utf-8")

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 0
    assert json.loads((tmp_path / "reportes.json").read_text(encoding="utf-8")) == [{"id": "old"}]
    assert json.loads((tmp_path / "reportes_ciudadanos.json").read_text(encoding="utf-8")) == [{"id": "old-c"}]


# --- Panel cross-reference hook: reportes_ciudadanos.json rows gain a -----
# --- 'panel' key from app.services.reportes_panel_state.apply_panel_match -
# --- (design "reportes ciudadanos vs Panel" — see reportes_panel_state.py).


def test_fetch_reportes_calls_panel_cross_reference_and_writes_panel_key(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    calls: list[list[dict]] = []

    def _fake_apply_panel_match(reportes):
        calls.append(reportes)
        return [{**r, "panel": {"visitado": True, "sticker": True}} for r in reportes]

    monkeypatch.setattr(job.reportes_panel_state, "apply_panel_match", _fake_apply_panel_match)

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    assert len(calls) == 1  # the hook ran exactly once, over the projected snapshot
    data = json.loads((tmp_path / "reportes_ciudadanos.json").read_text(encoding="utf-8"))
    assert data[0]["panel"] == {"visitado": True, "sticker": True}


def test_fetch_reportes_panel_hook_failure_still_fails_soft(tmp_path, monkeypatch):
    # Same fail-soft guarantee as build_snapshot itself (design D5): a bug in
    # the Panel cross-reference must never leave reportes.json/meta/agg
    # (already written, already the source of truth) inconsistent.
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    def _boom(reportes):
        raise RuntimeError("panel cross-reference blew up")

    monkeypatch.setattr(job.reportes_panel_state, "apply_panel_match", _boom)

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    assert (tmp_path / "reportes_agg.json").exists()
    assert not (tmp_path / "reportes_ciudadanos.json").exists()


# --- Fix 1b: panel-match has its OWN, separately-checked remaining-budget --
# --- guard (checked LATER than backfill's, since panel-match runs after ----
# --- backfill has already consumed part of the outer 240s budget), and -----
# --- runs off the event loop via asyncio.to_thread so a slow run can't ----
# --- starve asyncio.wait_for's own cancellation machinery. -----------------


def test_fetch_reportes_skips_panel_match_when_remaining_budget_too_low_but_backfill_still_ran(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)

    calls: list[dict] = []
    monkeypatch.setattr(job.atencionsismo, "day_walk", _make_day_walk_recorder(calls))
    monkeypatch.setattr(job.reportes_historico, "load_state", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    saved_state: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_state", lambda s: saved_state.append(s))
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)

    panel_calls: list[list[dict]] = []
    monkeypatch.setattr(
        job.reportes_panel_state, "apply_panel_match", lambda reportes: panel_calls.append(reportes) or reportes
    )
    kpis_calls: list[int] = []
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _make_fetch_kpis_recorder(kpis_calls))

    # started_at=1000.0; backfill's own elapsed read = 1050.0 -> remaining=190s,
    # comfortably >= BACKFILL_MIN_REMAINING_BUDGET_S (150s, Fix 2a) -> backfill
    # runs normally. panel-match's own elapsed read (F5: now checked BEFORE
    # kpis, so panel-match's own budget is never narrowed by a kpis call) =
    # 1215.0 -> remaining=25s < PANEL_MATCH_MIN_REMAINING_BUDGET_S (60s) ->
    # panel-match skipped. kpis' own elapsed read (checked LAST now, F5) =
    # 1220.0 -> remaining=20s < KPIS_MIN_REMAINING_BUDGET_S (45s) -> kpis
    # fetch is also skipped, even though backfill already ran.
    times = iter([1000.0, 1050.0, 1215.0, 1220.0])
    monkeypatch.setattr(job, "_monotonic", lambda: next(times))

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 2  # rolling + backfill record both merged in -> backfill DID run
    assert len(calls) == 2
    assert len(saved_state) == 1  # backfill progressed and persisted state
    assert kpis_calls == []  # kpis fetch skipped by its OWN, separately-checked budget guard
    assert panel_calls == []  # panel-match skipped by its OWN, earlier (F5) budget check
    data = json.loads((tmp_path / "reportes_ciudadanos.json").read_text(encoding="utf-8"))
    assert "panel" not in data[0]


def test_fetch_reportes_panel_match_runs_and_kpis_still_fetched_when_both_budgets_fit(tmp_path, monkeypatch):
    """F5 regression: kpis moved AFTER panel-match in call order specifically
    so a slow/bounded kpis call can never narrow panel-match's own budget
    check. Asserts panel-match still runs (and kpis too) when remaining
    budget comfortably clears BOTH PANEL_MATCH_MIN_REMAINING_BUDGET_S (60s)
    and KPIS_MIN_REMAINING_BUDGET_S (45s) -- e.g. 70s."""
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)

    calls: list[dict] = []
    monkeypatch.setattr(job.atencionsismo, "day_walk", _make_day_walk_recorder(calls))
    monkeypatch.setattr(
        job.reportes_historico, "load_state",
        lambda: {"backfill_complete": True, "backfill_frontier_ms": 1_600_000_000_000},
    )
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)

    panel_calls: list[list[dict]] = []

    def _fake_apply_panel_match(reportes):
        panel_calls.append(reportes)
        return [{**r, "panel": {"visitado": True, "sticker": True}} for r in reportes]

    monkeypatch.setattr(job.reportes_panel_state, "apply_panel_match", _fake_apply_panel_match)
    kpis_calls: list[int] = []

    async def _fake_fetch_kpis(client, user, password):
        kpis_calls.append(1)
        return {"reportes": 5}

    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _fake_fetch_kpis)

    # started_at=1000.0; backfill's own elapsed read=1000.0 (backfill already
    # complete, so the block is skipped regardless, but the guard's own read
    # still runs unconditionally) -> remaining=240s. panel-match's own
    # elapsed read (checked FIRST now, F5) = 1170.0 -> remaining=70s >=
    # PANEL_MATCH_MIN_REMAINING_BUDGET_S (60s) -> panel-match RUNS. kpis' own
    # elapsed read (checked LAST, F5) = 1170.0 -> remaining=70s >=
    # KPIS_MIN_REMAINING_BUDGET_S (45s) -> fetch_kpis ALSO runs, proving it
    # never got a chance to narrow panel-match's own guard above.
    times = iter([1000.0, 1000.0, 1170.0, 1170.0])
    monkeypatch.setattr(job, "_monotonic", lambda: next(times))

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1  # only the rolling-window record (backfill already complete)
    assert len(panel_calls) == 1  # panel-match ran
    assert kpis_calls == [1]  # kpis fetch also ran
    data = json.loads((tmp_path / "reportes_ciudadanos.json").read_text(encoding="utf-8"))
    assert "panel" in data[0]
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] == {"reportes": 5}


# --- reportes_historico backfill wiring: rolling window (always re-walked, --
# --- overwrite=True) + bounded historical backfill chunk (overwrite=False), -
# --- budget-guarded and fail-soft (see reportes_historico.py's module -------
# --- docstring for the creadoEn-only-filter rationale). ---------------------


def _rolling_record(rid="14832"):
    return {
        "id": rid,
        "estadoVerificacion": "Reportado",
        "latitud": "3.1",
        "longitud": "-76.1",
    }


def _backfill_record(rid="9000"):
    return {
        "id": rid,
        "estadoVerificacion": "Visitado",
        "latitud": "3.2",
        "longitud": "-76.2",
    }


def _make_day_walk_recorder(calls, *, backfill_records=None, backfill_exc=None, backfill_sleep=None):
    """Fake `atencionsismo.day_walk`: the rolling-window call site never
    passes `until_ms`, the backfill call site always does — used here to
    distinguish which call is which without depending on call order."""
    if backfill_records is None:
        backfill_records = [_backfill_record()]

    async def _fake(client, user, password, desde, *, until_ms=None, mapper=None):
        calls.append({"desde": desde, "until_ms": until_ms})
        is_backfill = until_ms is not None
        if is_backfill:
            if backfill_sleep is not None:
                import asyncio as _asyncio

                await _asyncio.sleep(backfill_sleep)
            if backfill_exc is not None:
                raise backfill_exc
            raw_list = backfill_records
        else:
            raw_list = [_rolling_record()]
        return [mapper(r) if mapper else r for r in raw_list]

    return _fake


def _patch_credentials_and_fake_firestore(monkeypatch):
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())
    return fake_db


def test_fetch_reportes_backfill_runs_and_advances_state_on_normal_call(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)

    calls: list[dict] = []
    monkeypatch.setattr(job.atencionsismo, "day_walk", _make_day_walk_recorder(calls))
    monkeypatch.setattr(job.reportes_historico, "load_state", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    saved_state: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_state", lambda s: saved_state.append(s))
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 2  # rolling record + backfill record, both merged into the pool
    assert len(calls) == 2
    assert calls[0]["until_ms"] is None  # rolling window call
    assert calls[1]["until_ms"] is not None  # backfill call

    written = json.loads((tmp_path / "reportes.json").read_text(encoding="utf-8"))
    assert {r["id"] for r in written} == {"14832", "9000"}

    assert len(saved_state) == 1
    assert saved_state[0]["backfill_complete"] is False  # chunk had records -> not genesis
    assert "backfill_frontier_ms" in saved_state[0]


def test_fetch_reportes_skips_backfill_when_already_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)

    calls: list[dict] = []
    monkeypatch.setattr(job.atencionsismo, "day_walk", _make_day_walk_recorder(calls))
    monkeypatch.setattr(
        job.reportes_historico,
        "load_state",
        lambda: {"backfill_complete": True, "backfill_frontier_ms": 1_600_000_000_000},
    )
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    saved_state: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_state", lambda s: saved_state.append(s))
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1  # only the rolling-window record
    assert len(calls) == 1  # the backfill day_walk call site was never reached
    assert saved_state == []  # state never re-saved once already complete

    meta = json.loads((tmp_path / "reportes_meta.json").read_text(encoding="utf-8"))
    assert meta["backfill_completo"] is True
    assert meta["cobertura_desde"] == job.reportes_historico.date_from_ms(1_600_000_000_000)


def test_fetch_reportes_skips_backfill_when_remaining_budget_too_low(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)

    calls: list[dict] = []
    monkeypatch.setattr(job.atencionsismo, "day_walk", _make_day_walk_recorder(calls))
    monkeypatch.setattr(job.reportes_historico, "load_state", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    saved_state: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_state", lambda s: saved_state.append(s))
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)

    # started_at reads 1000.0, the elapsed-time read later reads 1000.0+200 ->
    # elapsed=200s, remaining_budget = 240-200 = 40s < BACKFILL_MIN_REMAINING_BUDGET_S (150s).
    # A third read (panel-match's own budget check, F5: now checked BEFORE
    # kpis) reads 1215.0 -> elapsed=215s, remaining=25s <
    # PANEL_MATCH_MIN_REMAINING_BUDGET_S (60s) -> panel-match skipped. A
    # fourth read (kpis' own budget check, checked LAST now) reads 1215.0
    # again -> still 25s remaining, well under KPIS_MIN_REMAINING_BUDGET_S
    # (45s) too, so kpis fetch is also skipped this run. Patches
    # job._monotonic (a thin wrapper), NOT the real time.monotonic --
    # asyncio's own ProactorEventLoop teardown calls time.monotonic()
    # internally on Windows, so patching the global directly starves it.
    times = iter([1000.0, 1200.0, 1215.0, 1215.0])
    monkeypatch.setattr(job, "_monotonic", lambda: next(times))

    panel_calls: list[list[dict]] = []
    monkeypatch.setattr(
        job.reportes_panel_state, "apply_panel_match", lambda reportes: panel_calls.append(reportes) or reportes
    )
    kpis_calls: list[int] = []
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _make_fetch_kpis_recorder(kpis_calls))

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1  # only the rolling-window record
    assert len(calls) == 1  # backfill never attempted -> budget guard worked
    assert saved_state == []
    assert kpis_calls == []  # kpis fetch never attempted either -> budget guard worked
    assert panel_calls == []  # panel-match never attempted either -> budget guard worked
    data = json.loads((tmp_path / "reportes_ciudadanos.json").read_text(encoding="utf-8"))
    assert "panel" not in data[0]  # skipped -> no panel key added this run


def test_fetch_reportes_backfill_chunk_failure_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)

    calls: list[dict] = []
    monkeypatch.setattr(
        job.atencionsismo,
        "day_walk",
        _make_day_walk_recorder(calls, backfill_exc=httpx.HTTPError("boom")),
    )
    monkeypatch.setattr(job.reportes_historico, "load_state", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    saved_state: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_state", lambda s: saved_state.append(s))
    saved_pool: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: saved_pool.append(p))

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    # The backfill exception must not affect the rolling-window data at all:
    # reportes.json/meta/agg still get written from the rolling-window pool.
    assert count == 1
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    assert (tmp_path / "reportes_agg.json").exists()
    written = json.loads((tmp_path / "reportes.json").read_text(encoding="utf-8"))
    assert {r["id"] for r in written} == {"14832"}
    assert saved_state == []  # backfill progress state must not advance on failure
    # Fix 2b (defense in depth): save_pool now also runs right after the
    # rolling-window merge, BEFORE the backfill attempt -- so it's called
    # TWICE here (once durably before backfill, once again after, a harmless
    # idempotent duplicate) and every save reflects the rolling-window data,
    # since the failed backfill chunk was never merged in.
    assert len(saved_pool) == 2
    for saved in saved_pool:
        assert set(saved.keys()) == {"14832"}  # backfill record never merged in


def test_fetch_reportes_backfill_chunk_timeout_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)

    calls: list[dict] = []
    monkeypatch.setattr(
        job.atencionsismo,
        "day_walk",
        _make_day_walk_recorder(calls, backfill_sleep=0.2),
    )
    monkeypatch.setattr(job.reportes_historico, "BACKFILL_CHUNK_TIMEOUT_S", 0.01)
    monkeypatch.setattr(job.reportes_historico, "load_state", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    saved_state: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_state", lambda s: saved_state.append(s))
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1  # rolling-window data still written despite the hung backfill chunk
    assert (tmp_path / "reportes.json").exists()
    assert saved_state == []


async def _fake_probe_api_ok(client, user, password):
    """Fix 3: an empty backfill chunk now triggers a probe_api health check
    before it's trusted as genesis. Mocked to succeed here so this test's
    empty-chunk-is-genesis path still behaves as before."""
    return None


def test_reportes_meta_carries_backfill_fields_when_backfill_reaches_genesis(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)

    calls: list[dict] = []
    monkeypatch.setattr(
        job.atencionsismo,
        "day_walk",
        _make_day_walk_recorder(calls, backfill_records=[]),  # empty chunk -> genesis reached
    )
    monkeypatch.setattr(job.atencionsismo, "probe_api", _fake_probe_api_ok)
    monkeypatch.setattr(job.reportes_historico, "load_state", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    saved_state: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_state", lambda s: saved_state.append(s))
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    meta = json.loads((tmp_path / "reportes_meta.json").read_text(encoding="utf-8"))
    assert meta["backfill_completo"] is True
    assert meta["cobertura_desde"] == job.reportes_historico.date_from_ms(saved_state[0]["backfill_frontier_ms"])
    assert isinstance(meta["cobertura_desde"], str)


async def _fake_probe_api_down(client, user, password):
    raise job.atencionsismo.ApiUnavailableError("API caída durante el chunk vacío")


def test_fetch_reportes_backfill_empty_chunk_during_api_outage_leaves_state_unsaved(tmp_path, monkeypatch):
    """Fix 3: an empty backfill chunk during a genuine API outage must NOT be
    trusted as 'reached genesis' -- the probe_api health check raising here
    must be caught by the SAME existing except Exception: around the whole
    backfill block (no new except needed), leaving state completely unsaved
    so the SAME chunk range is retried next run once the API recovers
    (mirrors test_fetch_reportes_backfill_chunk_failure_is_swallowed's
    shape)."""
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)

    calls: list[dict] = []
    monkeypatch.setattr(
        job.atencionsismo,
        "day_walk",
        _make_day_walk_recorder(calls, backfill_records=[]),  # empty chunk
    )
    monkeypatch.setattr(job.atencionsismo, "probe_api", _fake_probe_api_down)
    monkeypatch.setattr(job.reportes_historico, "load_state", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    saved_state: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_state", lambda s: saved_state.append(s))
    saved_pool: list[dict] = []
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: saved_pool.append(p))

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    # Rolling-window data is entirely unaffected -- reportes.json still gets
    # written normally (fail-soft, same as every other backfill failure mode).
    assert count == 1
    assert len(calls) == 2  # rolling walk + the (empty) backfill attempt both ran
    assert saved_state == []  # neither the frontier nor backfill_complete advanced
    assert len(saved_pool) >= 1  # Fix 2b: pool still durably saved from the rolling merge
    written = json.loads((tmp_path / "reportes.json").read_text(encoding="utf-8"))
    assert {r["id"] for r in written} == {"14832"}


def test_reportes_meta_cobertura_desde_is_null_when_backfill_never_started(tmp_path, monkeypatch):
    # Backfill skipped entirely (budget too low) and state was never
    # persisted before -> cobertura_desde must be null, not raise/omit.
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)

    calls: list[dict] = []
    monkeypatch.setattr(job.atencionsismo, "day_walk", _make_day_walk_recorder(calls))
    monkeypatch.setattr(job.reportes_historico, "load_state", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "save_state", lambda s: None)
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _make_fetch_kpis_recorder([]))
    # 3rd read is panel-match's own budget check (F5: now checked BEFORE
    # kpis), 4th is kpis' own budget check, both later in the function.
    times = iter([1000.0, 1200.0, 1215.0, 1215.0])
    monkeypatch.setattr(job, "_monotonic", lambda: next(times))

    import asyncio

    asyncio.run(job.fetch_reportes())

    meta = json.loads((tmp_path / "reportes_meta.json").read_text(encoding="utf-8"))
    assert meta["backfill_completo"] is False
    assert meta["cobertura_desde"] is None


# --- kpis: server-side kpis object (never counted client-side), attached --
# --- to reportes_agg.json; fail-soft with "carry forward previous + mark --
# --- stale" (never fabricate a value). ------------------------------------


async def _fake_fetch_kpis_ok(client, user, password):
    return {"reportes": 10, "avancePct": 55.5}


async def _fake_fetch_kpis_fail(client, user, password):
    raise job.atencionsismo.ApiUnavailableError("kpis API no disponible (HTTP 503)")


async def _fake_fetch_kpis_unexpected_bug(client, user, password):
    # Simulates a bug in the kpis path unrelated to ApiUnavailableError (e.g.
    # a KeyError from a future refactor) -- must still never propagate out of
    # fetch_reportes(), same fail-soft contract as every other broad except
    # in this function (backfill, contactos, ciudadanos projection, panel).
    raise KeyError("simulated bug in kpis processing")


def test_fetch_reportes_kpis_success_writes_fresh_kpis_block(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _fake_fetch_kpis_ok)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] == {"reportes": 10, "avancePct": 55.5}
    assert agg["kpis_stale"] is False
    assert agg["kpis_error"] is None
    assert isinstance(agg["kpis_generated_at"], str) and agg["kpis_generated_at"]


def test_fetch_reportes_kpis_failure_with_previous_agg_preserves_kpis_and_marks_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _fake_fetch_kpis_fail)
    # F1: _load_previous_kpis() now tries Blob FIRST -- this test exercises
    # the LOCAL fallback specifically, so Blob must report nothing published.
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    (tmp_path / "reportes_agg.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-01T00:00:00Z",
                "kpis": {"reportes": 5},
                "kpis_generated_at": "2026-09-01T00:00:00Z",
                "kpis_stale": False,
                "kpis_error": None,
            }
        ),
        encoding="utf-8",
    )

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    # reportes.json/reportes_meta.json must still be written normally --
    # the kpis failure never blocks the main publication.
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] == {"reportes": 5}  # previous value carried forward
    assert agg["kpis_generated_at"] == "2026-09-01T00:00:00Z"  # ORIGINAL timestamp preserved
    assert agg["kpis_stale"] is True
    assert agg["kpis_error"]  # non-null reason


def test_fetch_reportes_kpis_failure_with_no_previous_agg_writes_null_kpis(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _fake_fetch_kpis_fail)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)  # F1: nothing published on Blob either
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] is None  # never fabricated
    assert agg["kpis_generated_at"] is None
    assert agg["kpis_stale"] is False  # nothing stale to carry forward -- just absent
    assert agg["kpis_error"]


def test_fetch_reportes_kpis_unexpected_exception_never_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _fake_fetch_kpis_unexpected_bug)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    import asyncio

    count = asyncio.run(job.fetch_reportes())  # must not raise

    assert count == 1
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] is None
    assert agg["kpis_error"]


# --- CRITICAL fix 3: fetch_kpis has its OWN, separately-checked remaining- --
# --- budget guard (checked LATER than both backfill's and panel-match's), --
# --- the same reasoning as PANEL_MATCH_MIN_REMAINING_BUDGET_S: on this ------
# --- Python version asyncio.CancelledError is BaseException, not Exception, -
# --- so a broad `except Exception` around the kpis call does NOT protect ---
# --- against the outer asyncio.wait_for(fetch_reportes(), timeout=---------
# --- FETCH_REPORTES_TIMEOUT_S) firing mid-request. When too little budget --
# --- remains, fetch_kpis is never even attempted -- straight to the SAME --
# --- "carry forward previous / null" fallback the except branch uses. ------


def _make_fetch_kpis_recorder(calls):
    """A fetch_kpis fake that RECORDS whether it was called instead of just
    raising -- a raise would be silently absorbed by fetch_reportes()'s own
    `except Exception` and repurposed into a truthy kpis_error, which would
    make a naive "must not be called" test pass for the wrong reason (the
    exception text alone satisfies `assert kpis_error`). Recording actual
    invocations is the only way to prove the budget guard skipped the call
    outright rather than merely failing it."""

    async def _fake(client, user, password):
        calls.append(1)
        return {"reportes": 999}

    return _fake


def test_fetch_reportes_skips_kpis_fetch_when_remaining_budget_too_low_no_previous_agg(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    kpis_calls: list[int] = []
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _make_fetch_kpis_recorder(kpis_calls))

    # started_at=1000.0; backfill's own elapsed read=1005.0 -> remaining=235s
    # (backfill_complete already True so the block is skipped regardless, but
    # the guard's own elapsed read still runs unconditionally). panel-match's
    # own elapsed read (F5: now checked BEFORE kpis) = 1215.0 -> remaining=25s
    # < PANEL_MATCH_MIN_REMAINING_BUDGET_S (60s) -> panel-match skipped,
    # irrelevant to this test's assertions. kpis' own elapsed read (checked
    # LAST now) = 1215.0 -> remaining=25s < KPIS_MIN_REMAINING_BUDGET_S (45s)
    # -> fetch_kpis skipped entirely.
    monkeypatch.setattr(
        job.reportes_historico, "load_state",
        lambda: {"backfill_complete": True, "backfill_frontier_ms": 1_600_000_000_000},
    )
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)
    times = iter([1000.0, 1005.0, 1215.0, 1215.0])
    monkeypatch.setattr(job, "_monotonic", lambda: next(times))

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    # The main publication is never blocked by the skipped kpis fetch.
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    assert kpis_calls == []  # fetch_kpis was never even attempted
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] is None  # nothing to carry forward -- never fabricated
    assert agg["kpis_generated_at"] is None
    assert agg["kpis_stale"] is False
    assert agg["kpis_error"]  # non-null reason, budget-related


def test_fetch_reportes_skips_kpis_fetch_when_remaining_budget_too_low_with_previous_agg_preserves_and_marks_stale(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    _patch_credentials_and_fake_firestore(monkeypatch)
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    kpis_calls: list[int] = []
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _make_fetch_kpis_recorder(kpis_calls))
    monkeypatch.setattr(
        job.reportes_historico, "load_state",
        lambda: {"backfill_complete": True, "backfill_frontier_ms": 1_600_000_000_000},
    )
    monkeypatch.setattr(job.reportes_historico, "load_pool", lambda: {})
    monkeypatch.setattr(job.reportes_historico, "save_pool", lambda p: None)
    # F5: panel-match's own budget check now runs BEFORE kpis' -- values
    # unchanged, order re-labelled below.
    times = iter([1000.0, 1005.0, 1215.0, 1215.0])
    monkeypatch.setattr(job, "_monotonic", lambda: next(times))
    # F1: _load_previous_kpis() now tries Blob first -- this test exercises
    # the LOCAL fallback specifically (the local file written just below).
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)

    (tmp_path / "reportes_agg.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-01T00:00:00Z",
                "kpis": {"reportes": 5},
                "kpis_generated_at": "2026-09-01T00:00:00Z",
                "kpis_stale": False,
                "kpis_error": None,
            }
        ),
        encoding="utf-8",
    )

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    assert kpis_calls == []  # fetch_kpis was never even attempted
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] == {"reportes": 5}  # previous value carried forward
    assert agg["kpis_generated_at"] == "2026-09-01T00:00:00Z"  # ORIGINAL timestamp preserved
    assert agg["kpis_stale"] is True
    assert agg["kpis_error"]  # non-null reason, budget-related


# --- WARNING fix 5: an UNEXPECTED exception's kpis_error must never carry ---
# --- the raw str(exc) into the public reportes_agg.json Blob file -- only --
# --- the exception's class name (or a static reason) is published for that -
# --- generic fallback branch. ApiUnavailableError keeps its own status- ----
# --- only message (already tested elsewhere not to leak credentials). ------


def test_fetch_reportes_kpis_unexpected_exception_publishes_class_name_not_raw_message(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _fake_fetch_kpis_unexpected_bug)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] is None
    assert agg["kpis_error"] == "KeyError"  # class name only, never the raw exception text
    assert "simulated bug in kpis processing" not in agg["kpis_error"]  # the raw message must never leak


def test_load_previous_kpis_returns_none_when_agg_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)  # F1: nothing on Blob either

    assert job._load_previous_kpis() == (None, None)


def test_load_previous_kpis_returns_none_when_agg_file_is_malformed_json(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)
    (tmp_path / "reportes_agg.json").write_text("not json{{{", encoding="utf-8")

    assert job._load_previous_kpis() == (None, None)


@pytest.mark.parametrize("bad_kpis", [[1, 2], "x", 42, None])
def test_load_previous_kpis_returns_none_when_kpis_field_is_not_a_dict(tmp_path, monkeypatch, bad_kpis):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)
    (tmp_path / "reportes_agg.json").write_text(
        json.dumps({"kpis": bad_kpis, "kpis_generated_at": "2026-09-01T00:00:00Z"}), encoding="utf-8"
    )

    assert job._load_previous_kpis() == (None, None)


def test_load_previous_kpis_returns_kpis_and_timestamp_when_present(tmp_path, monkeypatch):
    # LOCAL fallback path specifically: Blob has nothing published.
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)
    (tmp_path / "reportes_agg.json").write_text(
        json.dumps({"kpis": {"reportes": 9}, "kpis_generated_at": "2026-09-01T00:00:00Z"}), encoding="utf-8"
    )

    assert job._load_previous_kpis() == ({"reportes": 9}, "2026-09-01T00:00:00Z")


# --- F1: carry-forward now reads the last PUBLISHED reportes_agg.json from --
# --- Blob FIRST, via blob_lkg.load_json -- in the cron container, the local -
# --- WEB_DATA_DIR/reportes_agg.json is the image-baked, git-committed copy --
# --- with no 'kpis' key at all, so a local-only read could never carry -----
# --- forward a genuine previous kpis value in production. --------------------


def test_load_previous_kpis_reads_from_blob_first_when_local_has_none(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)  # no local reportes_agg.json at all

    def _fake_load_json(pathname, expected_type):
        assert pathname == "data/reportes_agg.json"
        assert expected_type is dict
        return {"kpis": {"reportes": 42}, "kpis_generated_at": "2026-09-01T00:00:00Z", "generated_at": "x"}

    monkeypatch.setattr(job.blob_lkg, "load_json", _fake_load_json)

    assert job._load_previous_kpis() == ({"reportes": 42}, "2026-09-01T00:00:00Z")


def test_load_previous_kpis_blob_none_and_local_none_returns_none_none(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)  # Blob unreachable/missing/no token

    assert job._load_previous_kpis() == (None, None)


def test_load_previous_kpis_blob_dict_without_kpis_key_falls_back_to_local(tmp_path, monkeypatch):
    # Blob answers with a real dict (not None), just one without a usable
    # 'kpis' field -- must fall back to the local file, not to (None, None).
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: {"generated_at": "2026-08-25T00:00:00Z"})
    (tmp_path / "reportes_agg.json").write_text(
        json.dumps({"kpis": {"reportes": 9}, "kpis_generated_at": "2026-09-01T00:00:00Z"}), encoding="utf-8"
    )

    assert job._load_previous_kpis() == ({"reportes": 9}, "2026-09-01T00:00:00Z")


def test_load_previous_kpis_blob_helper_raising_does_not_propagate_falls_back_to_local(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("blob unreachable")

    monkeypatch.setattr(job.blob_lkg, "load_json", _boom)
    (tmp_path / "reportes_agg.json").write_text(
        json.dumps({"kpis": {"reportes": 3}, "kpis_generated_at": "2026-08-01T00:00:00Z"}), encoding="utf-8"
    )

    assert job._load_previous_kpis() == ({"reportes": 3}, "2026-08-01T00:00:00Z")


def test_load_previous_kpis_blob_kpis_present_but_generated_at_missing_still_carried_forward(tmp_path, monkeypatch):
    # Guard against resurrecting a frozen value without inventing a
    # timestamp: kpis_generated_at missing entirely from the Blob payload
    # still carries the kpis value forward -- the caller (fetch_reportes)
    # is what marks the block stale, this function never fabricates a date.
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: {"kpis": {"reportes": 7}})

    assert job._load_previous_kpis() == ({"reportes": 7}, None)


def test_fetch_reportes_kpis_failure_carries_forward_blob_published_kpis_when_local_has_none(tmp_path, monkeypatch):
    """F1 regression, end-to-end: in the cron container WEB_DATA_DIR/
    reportes_agg.json is the image-baked, git-committed copy from before
    this feature existed (no 'kpis' key) -- carry-forward on a failed
    fetch_kpis must come from the last-PUBLISHED Blob copy, not that local
    file, or kpis_stale carry-forward could never fire in production."""
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _fake_fetch_kpis_fail)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    # Local file exists but has NO 'kpis' key at all (the pre-feature,
    # image-baked shape) -- must NOT be mistaken for "nothing to carry".
    (tmp_path / "reportes_agg.json").write_text(
        json.dumps({"generated_at": "2026-08-25T00:00:00Z", "total": 100}), encoding="utf-8"
    )
    monkeypatch.setattr(
        job.blob_lkg, "load_json",
        lambda *a, **k: {"kpis": {"reportes": 77}, "kpis_generated_at": "2026-09-01T00:00:00Z"},
    )

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] == {"reportes": 77}
    assert agg["kpis_generated_at"] == "2026-09-01T00:00:00Z"
    assert agg["kpis_stale"] is True


# --- F3: the _load_previous_kpis() fallback read is now INSIDE its own -----
# --- fail-soft boundary (it used to sit outside the try/except whose own ---
# --- comment claimed to cover it) -- a bug in that helper must never --------
# --- propagate out of fetch_reportes() and block reportes_agg.json. ---------


def test_fetch_reportes_load_previous_kpis_raising_does_not_propagate_publishes_null_kpis(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _fake_fetch_kpis_fail)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    def _boom():
        raise RuntimeError("_load_previous_kpis blew up")

    monkeypatch.setattr(job, "_load_previous_kpis", _boom)

    import asyncio

    count = asyncio.run(job.fetch_reportes())  # must not raise

    assert count == 1
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] is None
    assert agg["kpis_stale"] is False
    assert agg["kpis_error"]  # non-null: the original fetch_kpis failure reason survives


# --- F2: the kpis HTTP call itself is now bounded by ------------------------
# --- asyncio.wait_for(..., KPIS_CALL_TIMEOUT_S) -- a hung request must ------
# --- degrade to a TimeoutError kpis_error, never hang the whole run. --------


async def _fake_fetch_kpis_hangs(client, user, password):
    import asyncio  # module-level fn, not a test function -- needs its own import

    await asyncio.Event().wait()  # never resolves; bounded only by wait_for's own timeout


def test_fetch_reportes_kpis_call_is_bounded_by_timeout_and_publishes_timeout_error(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setenv("VISITADOS_API_PASS", "secret")
    monkeypatch.setattr(job.atencionsismo, "day_walk", _fake_day_walk_one_record)
    monkeypatch.setattr(job.atencionsismo, "fetch_kpis", _fake_fetch_kpis_hangs)
    monkeypatch.setattr(job, "KPIS_CALL_TIMEOUT_S", 0.05)  # bound the test itself, not the real 20s
    monkeypatch.setattr(job.blob_lkg, "load_json", lambda *a, **k: None)
    fake_db = _FakeContactDb()
    monkeypatch.setattr(job.credentials, "sismo", lambda: type("_C", (), {"firestore": fake_db})())

    import asyncio

    count = asyncio.run(job.fetch_reportes())

    assert count == 1
    assert (tmp_path / "reportes.json").exists()
    assert (tmp_path / "reportes_meta.json").exists()
    agg = json.loads((tmp_path / "reportes_agg.json").read_text(encoding="utf-8"))
    assert agg["kpis"] is None
    assert agg["kpis_error"] == "TimeoutError"


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
