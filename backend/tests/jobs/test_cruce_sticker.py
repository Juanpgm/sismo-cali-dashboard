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
