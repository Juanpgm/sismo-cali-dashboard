"""backend/tests/jobs/test_cruce_sticker.py — port of the offline `--check`
fixture pattern already established in `integracion_F1/cruce_sticker.py`
(the stickers-asignacion change) to the new `app/jobs/cruce_sticker.py`
location (task 7.8, RED first; design.md ADR-2/ADR-9; job-scheduling spec
"Watermark And Idempotent-Write Behavior Preserved" (cruce-sticker row);
backend-platform spec "sticker_matches And cuadrillas Sole-Writer Invariant"
(job side)).

Same pipeline-owned merge-safety/first-write assertions the legacy module's
own `_selfcheck_cruce_sticker()` made, plus the incremental-candidate
selection (`select_candidates`) and matching-cascade reuse
(`cruce_sticker_punto` calling into `app.integracion.cruce_gestor`) — all
pure functions, no Firestore/network. `main()`'s `--check` path is exercised
too, mirroring `job_sticker.py`'s own `--check` dispatch (before any
Firestore/runlog setup).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from app.jobs import cruce_sticker as job


# --- doc_id: stable, deterministic --------------------------------------


def test_doc_id_is_stable_and_deterministic():
    assert job.doc_id("ede", "1234") == "ede_1234"
    assert job.doc_id("israel", "45") == "israel_45"


# --- matching cascade: geo hit, address-fallback hit, clean miss ----------


def _evaluaciones():
    return [
        {"CODIGO_EDIFICACION": "76001-1-0010001", "Y": 3.4200, "X": -76.5300,
         "DIRECCION": "Calle 1 # 2-3"},
        {"CODIGO_EDIFICACION": "76001-1-0020001", "Y": 3.4500, "X": -76.5600,
         "DIRECCION": "Carrera 9 # 8-7"},
    ]


def test_cruce_sticker_punto_matches_by_geo_proximity():
    evaluaciones = _evaluaciones()
    addr_index = job.build_addr_index(evaluaciones)

    r = job.cruce_sticker_punto(3.42001, -76.53001, "Calle 1 # 2-3", evaluaciones, addr_index)

    assert r["tiene_sticker"] is True
    assert r["sticker_dist_m"] < 2.0
    assert r["tier"] == "alta"


def test_cruce_sticker_punto_falls_back_to_address_match():
    evaluaciones = _evaluaciones()
    addr_index = job.build_addr_index(evaluaciones)

    r = job.cruce_sticker_punto(3.9, -76.9, "CL 1 No. 2-3, Cali", evaluaciones, addr_index)

    assert r["tiene_sticker"] is True
    assert r["tier"] == "media"  # address agrees, geo doesn't


def test_cruce_sticker_punto_no_match_when_neither_signal_agrees():
    evaluaciones = _evaluaciones()
    addr_index = job.build_addr_index(evaluaciones)

    r = job.cruce_sticker_punto(3.9, -76.9, "DG 99 # 1-1", evaluaciones, addr_index)

    assert r["tiene_sticker"] is False
    assert r["tier"] is None


# --- build_write_ops: pipeline-owned fields only, admin fields protected --


def test_build_write_ops_never_writes_admin_fields_on_existing_doc():
    points = [{
        "fuente": "ede", "registro_id": "1234", "tiene_sticker": True, "tier": "alta",
        "sticker_dist_m": 5.0, "direccion": "CL 1 # 2-3", "coords": {"lat": 3.42, "lon": -76.53},
        "zona_id": "Comuna 3", "matched_at": "2026-08-25T00:00:00",
        "criterio_habitabilidad": None, "colapso": "no",
    }]
    existing = {job.doc_id("ede", "1234")}

    ops = job.build_write_ops(points, existing)
    fields = dict(ops)[job.doc_id("ede", "1234")]

    for admin_field in ("estado_asignacion", "cuadrilla_id", "inspector_uid"):
        assert admin_field not in fields, admin_field
    assert set(fields) == set(job.PIPELINE_FIELDS)


def test_build_write_ops_seeds_admin_defaults_only_on_first_write():
    points = [{
        "fuente": "israel", "registro_id": "45", "tiene_sticker": False, "tier": None,
        "sticker_dist_m": None, "direccion": "", "coords": {"lat": 3.50, "lon": -76.40},
        "zona_id": None, "matched_at": "2026-08-25T00:00:00",
        "criterio_habitabilidad": None, "colapso": "no",
    }]

    ops = job.build_write_ops(points, existing_ids=set())
    fields = dict(ops)[job.doc_id("israel", "45")]

    assert fields["estado_asignacion"] == "pendiente"
    assert fields["cuadrilla_id"] is None and fields["inspector_uid"] is None
    assert set(fields) == set(job.PIPELINE_FIELDS) | set(job.ADMIN_DEFAULT_FIELDS)


# --- select_candidates: incremental core — never re-scan a matched point --


def test_select_candidates_drops_already_matched_points():
    panel = [
        {"fuente": "ede", "registro_id": "A"},   # no state entry at all -> new, candidate
        {"fuente": "ede", "registro_id": "B"},   # exists, pendiente -> candidate
        {"fuente": "ede", "registro_id": "C"},   # exists, ya con sticker -> NOT a candidate
    ]
    state = {
        "ede_B": {"exists": True, "tiene_sticker": False},
        "ede_C": {"exists": True, "tiene_sticker": True},
    }

    candidates = job.select_candidates(panel, state)

    assert {p["registro_id"] for p in candidates} == {"A", "B"}


# --- --check: offline self-check, zero network/Firestore calls ------------


def test_main_check_flag_runs_offline_selfcheck_and_returns_zero(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cruce_sticker.py", "--check"])

    assert job.main() == 0


# --- sole-writer: REQUIRED_CLIENTS declares exactly `sismo` ---------------


def test_required_clients_declares_only_sismo():
    assert job.REQUIRED_CLIENTS == ("sismo",)


# --- run_cruce_sticker early-exit gate: skip the sticker_matches pre-read
# when nothing changed since the last run (mirrors
# test_planeacion_cruce.py's own gate tests — same monkeypatching style,
# no fake db heavier than the gate itself touches) --------------------------


def _gate_panel():
    return [{"fuente": "ede", "registro_id": "A", "lat": 3.42, "lon": -76.53,
             "direccion": "CL 1 # 2-3", "zona_id": None,
             "criterio_habitabilidad": None, "colapso": "no"}]


def _never_called(name):
    def _fake(*a, **kw):
        raise AssertionError(f"{name} must not be called when the gate fires")
    return _fake


class _FakeDb:
    project = "fake-project"


def test_run_is_a_noop_when_no_new_evaluaciones_and_panel_unchanged(monkeypatch):
    panel = _gate_panel()
    expected_hash = job._hash_panel(panel)
    when = datetime(2026, 8, 31, tzinfo=timezone.utc)

    monkeypatch.setattr(sys, "argv", ["cruce_sticker.py"])
    monkeypatch.setattr(job, "load_panel", lambda: panel)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeDb()})())
    monkeypatch.setattr(job, "read_state",
                        lambda db: {"last_run_at": when, "panel_hash": expected_hash})
    monkeypatch.setattr(job, "fetch_evaluaciones", lambda db, watermark: [])
    monkeypatch.setattr(job, "read_tiene_sticker_state", _never_called("read_tiene_sticker_state"))
    monkeypatch.setattr(job, "write_sticker_matches", _never_called("write_sticker_matches"))
    monkeypatch.setattr(job, "write_watermark", _never_called("write_watermark"))
    touched = []
    monkeypatch.setattr(job, "touch_last_checked", lambda db, now: touched.append(now))

    summary = job.run_cruce_sticker()

    assert summary["noop"] is True
    assert touched, "touch_last_checked must stamp the doc on a no-op run"


def test_run_executes_fully_when_new_evaluaciones_arrive(monkeypatch):
    panel = _gate_panel()
    expected_hash = job._hash_panel(panel)
    when = datetime(2026, 8, 31, tzinfo=timezone.utc)
    nueva = {"CODIGO_EDIFICACION": "76001-1-0010001", "Y": 3.42, "X": -76.53,
             "DIRECCION": "CL 1 # 2-3"}

    monkeypatch.setattr(sys, "argv", ["cruce_sticker.py"])
    monkeypatch.setattr(job, "load_panel", lambda: panel)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeDb()})())
    monkeypatch.setattr(job, "read_state",
                        lambda db: {"last_run_at": when, "panel_hash": expected_hash})
    monkeypatch.setattr(job, "fetch_evaluaciones", lambda db, watermark: [nueva])

    calls = []
    monkeypatch.setattr(job, "read_tiene_sticker_state",
                        lambda db, ids: calls.append("read_tiene_sticker_state") or {})
    monkeypatch.setattr(job, "write_sticker_matches", lambda db, points, existing: len(points))
    watermark_calls = []
    monkeypatch.setattr(job, "write_watermark",
                        lambda db, now, **kw: watermark_calls.append(kw))

    summary = job.run_cruce_sticker()

    assert "noop" not in summary
    assert "read_tiene_sticker_state" in calls
    assert watermark_calls[-1]["panel_hash"] == expected_hash


def test_run_executes_fully_when_panel_hash_changed(monkeypatch):
    panel = _gate_panel()
    when = datetime(2026, 8, 31, tzinfo=timezone.utc)

    monkeypatch.setattr(sys, "argv", ["cruce_sticker.py"])
    monkeypatch.setattr(job, "load_panel", lambda: panel)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeDb()})())
    # 0 new evaluaciones but a STALE panel_hash (e.g. a brand-new Panel point
    # arrived): the full path must still run so the point gets its pendiente
    # sticker_matches seed doc.
    monkeypatch.setattr(job, "read_state",
                        lambda db: {"last_run_at": when, "panel_hash": "stale-hash"})
    monkeypatch.setattr(job, "fetch_evaluaciones", lambda db, watermark: [])

    calls = []
    monkeypatch.setattr(job, "read_tiene_sticker_state",
                        lambda db, ids: calls.append("read_tiene_sticker_state") or {})
    monkeypatch.setattr(job, "write_sticker_matches", lambda db, points, existing: len(points))
    monkeypatch.setattr(job, "write_watermark", lambda db, now, **kw: None)

    summary = job.run_cruce_sticker()

    assert "noop" not in summary
    assert "read_tiene_sticker_state" in calls


def test_dry_gate_run_never_writes_last_checked(monkeypatch):
    """--dry documents "no Firestore write" — that contract holds even when
    the early-exit gate fires: last_checked_at is NOT stamped."""
    panel = _gate_panel()
    expected_hash = job._hash_panel(panel)
    when = datetime(2026, 8, 31, tzinfo=timezone.utc)

    monkeypatch.setattr(sys, "argv", ["cruce_sticker.py", "--dry"])
    monkeypatch.setattr(job, "load_panel", lambda: panel)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeDb()})())
    monkeypatch.setattr(job, "read_state",
                        lambda db: {"last_run_at": when, "panel_hash": expected_hash})
    monkeypatch.setattr(job, "fetch_evaluaciones", lambda db, watermark: [])
    monkeypatch.setattr(job, "read_tiene_sticker_state", _never_called("read_tiene_sticker_state"))
    monkeypatch.setattr(job, "touch_last_checked", _never_called("touch_last_checked"))
    monkeypatch.setattr(job, "write_watermark", _never_called("write_watermark"))

    summary = job.run_cruce_sticker()

    assert summary["noop"] is True


def test_top_run_does_not_poison_the_gate_for_the_next_full_run(monkeypatch):
    """A --top N debug run must persist the TRUNCATED panel's hash — the
    next normal run's gate then mismatches and runs fully, instead of
    no-oping forever over the never-scanned tail (RELI-001)."""
    panel = _gate_panel() + [
        {"fuente": "ede", "registro_id": "B", "lat": 3.45, "lon": -76.56,
         "direccion": "CR 9 # 8-7", "zona_id": None,
         "criterio_habitabilidad": None, "colapso": "no"},
    ]
    when = datetime(2026, 8, 31, tzinfo=timezone.utc)

    # 1) --top 1 run: only the first point is processed and hashed.
    monkeypatch.setattr(sys, "argv", ["cruce_sticker.py", "--top", "1"])
    monkeypatch.setattr(job, "load_panel", lambda: panel)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeDb()})())
    monkeypatch.setattr(job, "read_state", lambda db: {})
    monkeypatch.setattr(job, "fetch_evaluaciones", lambda db, watermark: [])
    monkeypatch.setattr(job, "read_tiene_sticker_state", lambda db, ids: {})
    monkeypatch.setattr(job, "write_sticker_matches", lambda db, points, existing: len(points))
    persisted = {}
    monkeypatch.setattr(job, "write_watermark",
                        lambda db, now, **kw: persisted.update(kw))
    job.run_cruce_sticker()
    assert persisted["panel_hash"] == job._hash_panel(panel[:1])

    # 2) next NORMAL run against that persisted state, 0 new evaluaciones:
    # the gate must NOT fire (truncated hash != full-panel hash).
    monkeypatch.setattr(sys, "argv", ["cruce_sticker.py"])
    monkeypatch.setattr(job, "read_state",
                        lambda db: {"last_run_at": when,
                                    "panel_hash": persisted["panel_hash"]})
    calls = []
    monkeypatch.setattr(job, "read_tiene_sticker_state",
                        lambda db, ids: calls.append("read_tiene_sticker_state") or {})
    monkeypatch.setattr(job, "touch_last_checked", _never_called("touch_last_checked"))

    summary = job.run_cruce_sticker()

    assert "noop" not in summary
    assert "read_tiene_sticker_state" in calls


def test_full_flag_bypasses_the_early_exit_gate(monkeypatch):
    panel = _gate_panel()

    monkeypatch.setattr(sys, "argv", ["cruce_sticker.py", "--full"])
    monkeypatch.setattr(job, "load_panel", lambda: panel)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeDb()})())
    # --full must not even read the persisted state: watermark None -> every
    # evaluación is re-fetched, and the gate can never fire.
    monkeypatch.setattr(job, "read_state", _never_called("read_state"))
    fetch_watermarks = []
    monkeypatch.setattr(job, "fetch_evaluaciones",
                        lambda db, watermark: fetch_watermarks.append(watermark) or [])

    calls = []
    monkeypatch.setattr(job, "read_tiene_sticker_state",
                        lambda db, ids: calls.append("read_tiene_sticker_state") or {})
    monkeypatch.setattr(job, "write_sticker_matches", lambda db, points, existing: len(points))
    monkeypatch.setattr(job, "touch_last_checked", _never_called("touch_last_checked"))
    monkeypatch.setattr(job, "write_watermark", lambda db, now, **kw: None)

    summary = job.run_cruce_sticker()

    assert "noop" not in summary
    assert "read_tiene_sticker_state" in calls
    assert fetch_watermarks == [None]
