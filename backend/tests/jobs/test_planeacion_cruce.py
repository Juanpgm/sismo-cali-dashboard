"""backend/tests/jobs/test_planeacion_cruce.py — offline, no network, no
Firestore (task 2.1/2.3/2.5/2.7, RED first; design.md ADR-1/ADR-3/ADR-4/
ADR-5; spec.md "clave_integracion minting rule", "Deterministic
prioritization", "Matching cascade order and tiering", "planeacion_puntos
document ownership and merge safety").

Mirrors `backend/tests/jobs/test_cruce_sticker.py`'s offline pure-function
convention. Written and confirmed RED in stages, one per task slice (see
apply-progress.md's TDD Cycle Evidence table for the exact RED/GREEN
command+output pairs).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from app.jobs import planeacion_cruce as job


# ── doc_id: stable, deterministic (task 2.1) ────────────────────────────────


def test_doc_id_is_stable_and_deterministic():
    assert job.doc_id("atencionsismo", "14832") == "atencionsismo_14832"
    assert job.doc_id("atencionsismo", "14832") == job.doc_id("atencionsismo", "14832")


# ── clave_integracion: the minting rule (task 2.1) ──────────────────────────


def test_clave_integracion_is_deterministic_across_calls():
    k1 = job.clave_integracion("atencionsismo", "14832")
    k2 = job.clave_integracion("atencionsismo", "14832")
    assert k1 == k2


def test_clave_integracion_is_url_safe_and_bounded():
    clave = job.clave_integracion("atencionsismo", "14832")
    assert clave.startswith("PLN-")
    assert len(clave) <= 255
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
    assert set(clave) <= allowed


def test_clave_integracion_differs_when_sanitization_would_collapse_ids():
    # "14832" and "1-4832" collapse to the identical slug ("14832") after
    # sanitization, but the checksum is computed over the RAW id (with the
    # dash), so the two keys must still differ.
    k1 = job.clave_integracion("atencionsismo", "14832")
    k2 = job.clave_integracion("atencionsismo", "1-4832")
    assert k1 != k2


def test_verify_clave_integracion_accepts_a_genuine_key():
    clave = job.clave_integracion("atencionsismo", "14832")
    assert job.verify_clave_integracion(clave) is True


def test_verify_clave_integracion_rejects_a_mutated_checksum():
    clave = job.clave_integracion("atencionsismo", "14832")
    prefix, slug, digest = clave.split("-")
    damaged = f"{prefix}-{slug}-{'0' if digest[0] != '0' else '1'}{digest[1:]}"
    assert job.verify_clave_integracion(damaged) is False


def test_verify_clave_integracion_rejects_malformed_input():
    assert job.verify_clave_integracion("not-a-real-key") is False
    assert job.verify_clave_integracion("") is False
    assert job.verify_clave_integracion(None) is False


# ── Deterministic prioritization (task 2.3) ─────────────────────────────────


AHORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _rec(afectacion, estado, fecha_creacion=None, comuna="Comuna 1"):
    return {
        "afectacion": afectacion,
        "estado_verificacion": estado,
        "fecha_creacion": fecha_creacion,
        "comuna": comuna,
    }


def test_severity_outranks_verification_state():
    severo_no_verificado = _rec("COLAPSO TOTAL", "Reportado", AHORA.isoformat())
    leve_verificado = _rec("NO SE EVIDENCIA NINGÚN DAÑO", "Visitado crítico", AHORA.isoformat())

    score_severo = job.prioridad_score(severo_no_verificado, AHORA)
    score_leve = job.prioridad_score(leve_verificado, AHORA)

    assert score_severo > score_leve


def test_age_breaks_ties_but_never_dominates():
    reciente = _rec("COLAPSO PARCIAL", "Asignado", AHORA.isoformat())
    vieja_fecha = (AHORA - timedelta(days=400)).isoformat()
    vieja_misma_severidad = _rec("COLAPSO PARCIAL", "Asignado", vieja_fecha)

    assert job.prioridad_score(vieja_misma_severidad, AHORA) > job.prioridad_score(reciente, AHORA)

    # A much older, strictly LESS severe report still scores below a newer,
    # more severe one — age can break ties but never flips a severity gap.
    vieja_menos_severa = _rec("DAÑO MAMPOSTERÍA", "Asignado", vieja_fecha)
    nueva_mas_severa = _rec("COLAPSO TOTAL", "Asignado", AHORA.isoformat())
    assert job.prioridad_score(vieja_menos_severa, AHORA) < job.prioridad_score(nueva_mas_severa, AHORA)


def test_age_saturates():
    fecha_90 = (AHORA - timedelta(days=90)).isoformat()
    fecha_400 = (AHORA - timedelta(days=400)).isoformat()
    r90 = _rec("RIESGO COLAPSO", "Asignado", fecha_90)
    r400 = _rec("RIESGO COLAPSO", "Asignado", fecha_400)

    assert job.peso_antiguedad(r90, AHORA) == job.peso_antiguedad(r400, AHORA)


def test_unknown_afectacion_category_falls_back_and_does_not_raise():
    rec = _rec("CATEGORIA-NUEVA-DESCONOCIDA", "Reportado", AHORA.isoformat())

    score = job.prioridad_score(rec, AHORA)  # must not raise

    assert score >= 0


def test_same_input_always_yields_the_same_score():
    rec = _rec("DAÑO ESTRUCTURAL", "Visitado", AHORA.isoformat())

    s1 = job.prioridad_score(rec, AHORA)
    s2 = job.prioridad_score(rec, AHORA)

    assert s1 == s2
    assert job.prioridad_de(s1) == job.prioridad_de(s2)


def test_comuna_does_not_affect_priority():
    rec_a = _rec("DAÑO ESTRUCTURAL", "Visitado", AHORA.isoformat(), comuna="Comuna 1")
    rec_b = _rec("DAÑO ESTRUCTURAL", "Visitado", AHORA.isoformat(), comuna="Comuna 20")

    assert job.prioridad_score(rec_a, AHORA) == job.prioridad_score(rec_b, AHORA)


# ── Matching cascade: 5 rungs, exact key first (task 2.5) ───────────────────


def _survey(global_id, lat, lon, direccion, codigoapp=""):
    return {"GlobalID": global_id, "Y": lat, "X": lon, "DIRECCION": direccion,
            "codigoapp": codigoapp}


def test_exact_key_beats_a_nearer_fuzzy_candidate():
    clave = job.clave_integracion("atencionsismo", "14832")
    lejano_con_clave = _survey("S-LEJANO", 3.9000, -76.9000, "Otra direccion", codigoapp=clave)
    cercano_sin_clave = _survey("S-CERCANO", 3.42001, -76.53001, "Calle 1 # 2-3")
    surveys = [lejano_con_clave, cercano_sin_clave]
    key_index = job.build_key_index(surveys)
    addr_index = job.build_addr_index(surveys)

    r = job.cruce_punto(3.4200, -76.5300, "Calle 1 # 2-3", clave,
                        key_index=key_index, surveys=surveys, addr_index=addr_index)

    assert r["tiene_survey"] is True
    assert r["match_via"] == "clave" and r["tier"] == "exacta"
    assert r["survey_globalid"] == "S-LEJANO"


def test_key_rung_matches_nothing_before_any_key_in_circulation():
    surveys = [_survey("S1", 3.4200, -76.5300, "Calle 1 # 2-3")]
    key_index = job.build_key_index(surveys)  # no codigoapp values at all
    addr_index = job.build_addr_index(surveys)
    clave = job.clave_integracion("atencionsismo", "99999")

    r = job.cruce_punto(3.9000, -76.9000, "Direccion sin relacion", clave,
                        key_index=key_index, surveys=surveys, addr_index=addr_index)

    assert r["match_via"] != "clave"  # falls through to fuzzy rungs, no error


def test_proximity_match_tiered_alta_by_distance_and_address_agreement():
    surveys = [_survey("S1", 3.4200, -76.5300, "Calle 1 # 2-3")]
    key_index = job.build_key_index(surveys)
    addr_index = job.build_addr_index(surveys)
    clave = job.clave_integracion("atencionsismo", "no-circulating")

    # ~12 m from S1, same normalized address.
    r = job.cruce_punto(3.42011, -76.53001, "Calle 1 # 2-3", clave,
                        key_index=key_index, surveys=surveys, addr_index=addr_index)

    assert r["tiene_survey"] is True
    assert r["match_via"] == "cercania" and r["tier"] == "alta"


def test_distant_address_only_match_tiered_media():
    surveys = [_survey("S1", 3.4200, -76.5300, "Calle 1 # 2-3")]
    key_index = job.build_key_index(surveys)
    addr_index = job.build_addr_index(surveys)
    clave = job.clave_integracion("atencionsismo", "no-circulating-2")

    r = job.cruce_punto(3.9000, -76.9000, "Calle 1 No. 2-3, Cali", clave,
                        key_index=key_index, surveys=surveys, addr_index=addr_index)

    assert r["tiene_survey"] is True
    assert r["match_via"] == "direccion" and r["tier"] == "media"


def test_combined_rung_when_neither_signal_clears_its_own_bar():
    # ~70 m away (within COMBINED_MAX_M=100, outside MATCH_MAX_M=40) with an
    # address similarity of exactly 0.80 (>= COMBINED_SEM, < SEM_OK=0.90 and
    # < cruce_gestor's own combo ratio 0.85 — so match_by_direccion itself
    # returns no match here; only planeacion's own wider combined check does).
    surveys = [_survey("S1", 3.42063, -76.53000, "Diagonal 1 # 2-3")]
    key_index = job.build_key_index(surveys)
    addr_index = job.build_addr_index(surveys)
    clave = job.clave_integracion("atencionsismo", "no-circulating-3")

    r = job.cruce_punto(3.4200, -76.5300, "Calle 1 # 2-3", clave,
                        key_index=key_index, surveys=surveys, addr_index=addr_index)

    assert r["tiene_survey"] is True
    assert r["match_via"] == "combinado" and r["tier"] == "sospechoso"


def test_clean_miss_stays_pending():
    surveys = [_survey("S1", 3.4200, -76.5300, "Calle 1 # 2-3")]
    key_index = job.build_key_index(surveys)
    addr_index = job.build_addr_index(surveys)
    clave = job.clave_integracion("atencionsismo", "no-circulating-4")

    r = job.cruce_punto(3.9000, -76.9000, "DG 99 # 1-1", clave,
                        key_index=key_index, surveys=surveys, addr_index=addr_index)

    assert r == {"tiene_survey": False, "survey_globalid": None,
                 "match_via": None, "tier": None, "match_dist_m": None}


def test_well_formed_key_matching_no_point_does_not_corrupt_anything():
    orphan_clave = job.clave_integracion("atencionsismo", "orphan-999")
    surveys = [_survey("S1", 3.9000, -76.9000, "Direccion irrelevante", codigoapp=orphan_clave)]
    key_index = job.build_key_index(surveys)
    addr_index = job.build_addr_index(surveys)
    other_clave = job.clave_integracion("atencionsismo", "14832")

    r = job.cruce_punto(3.4200, -76.5300, "Calle 1 # 2-3", other_clave,
                        key_index=key_index, surveys=surveys, addr_index=addr_index)

    assert r["match_via"] != "clave"


# ── build_write_ops: ownership + merge safety (task 2.7) ────────────────────


def _pipeline_point(fuente="atencionsismo", registro_id="14832", match_via=None, tiene_survey=False):
    return {
        "fuente": fuente, "registro_id": registro_id,
        "clave_integracion": job.clave_integracion(fuente, registro_id),
        "tiene_survey": tiene_survey, "survey_globalid": None,
        "match_via": match_via, "match_dist_m": None, "tier": None,
        "direccion": "CL 1 # 2-3", "barrio": None, "comuna": "Comuna 1",
        "coords": {"lat": 3.42, "lon": -76.53}, "afectacion": "DAÑO ESTRUCTURAL",
        "estado_verificacion": "Reportado", "tipo_inmueble": None,
        "habitabilidad": None, "fecha_creacion": "2026-08-01T00:00:00+00:00",
        "prioridad_score": 40, "prioridad": "media",
        "matched_at": "2026-08-26T00:00:00+00:00",
    }


def test_existing_doc_write_never_contains_an_admin_owned_key():
    p = _pipeline_point()
    existing = {job.doc_id("atencionsismo", "14832")}

    ops = job.build_write_ops([p], existing, estado_actual={job.doc_id("atencionsismo", "14832"): "pendiente"})
    fields = dict(ops)[job.doc_id("atencionsismo", "14832")]

    assert set(fields) == set(job.PIPELINE_FIELDS)


def test_first_write_seeds_exactly_admin_default_fields():
    p = _pipeline_point()
    did = job.doc_id("atencionsismo", "14832")

    ops = job.build_write_ops([p], existing_ids=set(), estado_actual={})
    fields = dict(ops)[did]

    assert set(fields) == set(job.PIPELINE_FIELDS) | set(job.ADMIN_DEFAULT_FIELDS)
    assert fields["estado_asignacion"] == "pendiente"
    assert fields["cuadrilla_id"] is None and fields["inspector_uid"] is None


def test_surveyed_point_dropped_from_candidates():
    panel = [{"fuente": "atencionsismo", "registro_id": "A"},
             {"fuente": "atencionsismo", "registro_id": "B"}]
    state = {
        "atencionsismo_A": {"exists": True, "tiene_survey": False, "estado_asignacion": "pendiente"},
        "atencionsismo_B": {"exists": True, "tiene_survey": True, "estado_asignacion": "hecho"},
    }

    cands = job.select_candidates(panel, state)

    assert {p["registro_id"] for p in cands} == {"A"}


# ── Auto-close exception (binding decision 2026-08-26, Q5) ──────────────────


def test_exact_key_match_auto_closes_a_pending_point_to_hecho():
    p = _pipeline_point(match_via="clave", tiene_survey=True)
    did = job.doc_id("atencionsismo", "14832")
    existing = {did}

    ops = job.build_write_ops([p], existing, estado_actual={did: "pendiente"})
    fields = dict(ops)[did]

    assert fields["estado_asignacion"] == "hecho"


def test_exact_key_match_auto_closes_from_asignado_and_en_proceso_too():
    did = job.doc_id("atencionsismo", "14832")
    for from_state in ("asignado", "en_proceso"):
        p = _pipeline_point(match_via="clave", tiene_survey=True)
        ops = job.build_write_ops([p], {did}, estado_actual={did: from_state})
        assert dict(ops)[did]["estado_asignacion"] == "hecho"


def test_a_hecho_point_is_never_reopened_by_a_later_run():
    # The point still matches via clave on this run (e.g. a re-run before
    # the watermark advances) but its CURRENT state is already 'hecho' —
    # the pipeline must never write estado_asignacion here at all.
    p = _pipeline_point(match_via="clave", tiene_survey=True)
    did = job.doc_id("atencionsismo", "14832")

    ops = job.build_write_ops([p], {did}, estado_actual={did: "hecho"})
    fields = dict(ops)[did]

    assert "estado_asignacion" not in fields


def test_no_aplica_point_is_never_auto_closed():
    p = _pipeline_point(match_via="clave", tiene_survey=True)
    did = job.doc_id("atencionsismo", "14832")

    ops = job.build_write_ops([p], {did}, estado_actual={did: "no_aplica"})
    fields = dict(ops)[did]

    assert "estado_asignacion" not in fields


def test_fuzzy_match_never_auto_closes():
    for via in ("cercania", "direccion", "combinado", None):
        p = _pipeline_point(match_via=via, tiene_survey=(via is not None))
        did = job.doc_id("atencionsismo", "14832")
        ops = job.build_write_ops([p], {did}, estado_actual={did: "pendiente"})
        assert "estado_asignacion" not in dict(ops)[did], via


def test_auto_close_never_touches_cuadrilla_or_inspector():
    p = _pipeline_point(match_via="clave", tiene_survey=True)
    did = job.doc_id("atencionsismo", "14832")

    ops = job.build_write_ops([p], {did}, estado_actual={did: "asignado"})
    fields = dict(ops)[did]

    assert "cuadrilla_id" not in fields
    assert "inspector_uid" not in fields


# ── Spanish fechaCreacion parsing (task 2.9 — pure, load-time normalization) ─


def test_parses_the_live_atencionsismo_fecha_creacion_format():
    dt = job.parse_fecha_creacion_es("martes, 18 de agosto de 2026, 06:33 p. m.")

    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 8, 18, 18, 33)


def test_parses_am_and_noon_boundary_correctly():
    dt_am = job.parse_fecha_creacion_es("lunes, 17 de agosto de 2026, 12:26 a. m.")
    dt_pm = job.parse_fecha_creacion_es("lunes, 17 de agosto de 2026, 12:00 p. m.")

    assert dt_am.hour == 0
    assert dt_pm.hour == 12


def test_returns_none_for_empty_or_unparseable_input():
    assert job.parse_fecha_creacion_es("") is None
    assert job.parse_fecha_creacion_es(None) is None
    assert job.parse_fecha_creacion_es("not a date") is None


# --check offline self-check --------------------------------------------------


def test_main_check_flag_runs_offline_selfcheck_and_returns_zero(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["planeacion_cruce.py", "--check"])

    assert job.main() == 0


def test_required_clients_declares_only_sismo():
    assert job.REQUIRED_CLIENTS == ("sismo",)
