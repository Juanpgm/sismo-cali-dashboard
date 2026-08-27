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


def test_a_mutated_checksum_pairs_with_no_point():
    """A damaged digest must never pair a survey to a building.

    It is NOT caught by `verify_clave_integracion` — that is structural
    only, because the digest covers the full `registro_id` while the slug
    is capped at 24 chars, so a stateless recompute is impossible for the
    UUID-shaped ids every real point actually has (an earlier revision
    tried, and thereby rejected every genuine real-world key). The
    guarantee lives one layer up instead, at the exact-equality lookup:
    a mutated key simply is not in the index of keys minted from known
    points, so it matches nothing.
    """
    clave = job.clave_integracion("atencionsismo", "14832")
    prefix, slug, digest = clave.split("-")
    damaged = f"{prefix}-{slug}-{'0' if digest[0] != '0' else '1'}{digest[1:]}"

    assert damaged != clave
    index = job.build_key_index([{"GlobalID": "g", "codigoapp": damaged}])
    assert index.get(clave) is None, "a damaged key must not resolve to the real point"


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


def test_full_integration_key_loop_survey_link_to_triple_key():
    """END-TO-END relationship (minus the external Survey123 submission + cron):
    the `codigoapp` a crew receives in the prefill link is EXACTLY the key that
    ties the survey back to its point and its atencionsismo `registro_id`.

      registro_id (atencionsismo API)
        -> clave_integracion  (minted, unique)
        -> field:codigoapp=<clave> in the Survey123 URL   (build_survey_urls)
        -> survey submitted carrying that codigoapp, GlobalID G   (ArcGIS)
        -> planeacion_cruce rung-1 exact match -> point tiene_survey + survey_globalid=G

    Proves the TRIPLE KEY is established: registro_id + clave/codigoapp + survey_globalid,
    i.e. we can tell an assigned point was surveyed and by WHICH survey.
    """
    from urllib.parse import parse_qs, urlsplit

    from app.services.survey_link import build_survey_urls

    registro_id = "e7758d05-6671-4c98-a057-f8bdf8d7e2b3"  # atencionsismo report UUID
    clave = job.clave_integracion("atencionsismo", registro_id)

    # The codigoapp placed in the Survey123 prefill link IS the point's clave.
    urls = build_survey_urls(clave, form_url="https://survey123.arcgis.com/share/FORMID",
                             field_app_item_id=None)
    codigoapp_en_link = parse_qs(urlsplit(urls["web"]).query)["field:codigoapp"][0]
    assert codigoapp_en_link == clave

    # A survey submitted with that codigoapp (GlobalID assigned by ArcGIS). Placed
    # FAR from the point + a different address, so ONLY the exact key can match —
    # this isolates the integration key as what establishes the relationship.
    survey = _survey("SURVEY-GID-777", 3.9000, -76.9000, "Otra direccion",
                     codigoapp=codigoapp_en_link)
    surveys = [survey]
    key_index = job.build_key_index(surveys)
    addr_index = job.build_addr_index(surveys)

    result = job.cruce_punto(3.4200, -76.5300, "Calle 1 # 2-3", clave,
                             key_index=key_index, surveys=surveys, addr_index=addr_index)

    # Triple key fully established:
    assert result["tiene_survey"] is True                 # assigned point is now "levantado"
    assert result["match_via"] == "clave"                 # by the integration key, not fuzzy geo
    assert result["survey_globalid"] == "SURVEY-GID-777"  # survey side of the key
    assert codigoapp_en_link == clave                     # atencionsismo id -> clave -> codigoapp: one key
    assert job.clave_integracion("atencionsismo", registro_id) == clave  # deterministic/reproducible

    # Uniqueness -> no false positives: a DIFFERENT point (different registro_id ->
    # different clave) must NOT key-match this survey.
    otro_clave = job.clave_integracion("atencionsismo", "9f2b1c44-0000-4aaa-bbbb-cccccccccccc")
    assert otro_clave != clave
    r2 = job.cruce_punto(3.4200, -76.5300, "Calle 1 # 2-3", otro_clave,
                         key_index=key_index, surveys=surveys, addr_index=addr_index)
    assert r2["match_via"] != "clave"


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


# ── feature D: fetch_israel — israel as a second "levantado" source ─────────


class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _FakeDb:
    def __init__(self, by_collection):
        self._by = by_collection

    def collection(self, name):
        docs = self._by.get(name, [])

        class _Col:
            def stream(self_inner):
                return iter(docs)

        return _Col()


def test_fetch_israel_flattens_to_survey_shape_with_isr_prefix():
    db = _FakeDb({job.INSPECCIONES_ISRAEL_COLLECTION: [
        _FakeDoc("GID-1", {"x": -76.53, "y": 3.42, "direccion_norm": "CALLE 1 2 3"}),
    ]})
    assert job.fetch_israel(db) == [{
        "GlobalID": "isr-GID-1",
        "Y": 3.42, "X": -76.53,
        "DIRECCION": "CALLE 1 2 3",
        "codigoapp": "",
    }]


def test_fetch_israel_falls_back_to_direccion_when_no_norm():
    db = _FakeDb({job.INSPECCIONES_ISRAEL_COLLECTION: [
        _FakeDoc("G2", {"x": -76.5, "y": 3.4, "direccion": "Cra 5 # 6-7"}),
    ]})
    out = job.fetch_israel(db)
    assert out[0]["DIRECCION"] == "Cra 5 # 6-7"


def test_fetch_israel_drops_codigoapp_so_it_never_matches_rung1():
    db = _FakeDb({job.INSPECCIONES_ISRAEL_COLLECTION: [
        _FakeDoc("G3", {"x": -76.5, "y": 3.4, "codigoapp": "SHOULD-BE-IGNORED"}),
    ]})
    out = job.fetch_israel(db)
    assert out[0]["codigoapp"] == ""
    # israel can never enter the clave (rung-1) index
    assert job.build_key_index(out) == {}


def test_fetch_israel_empty_collection_returns_empty():
    db = _FakeDb({job.INSPECCIONES_ISRAEL_COLLECTION: []})
    assert job.fetch_israel(db) == []


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


# ── select_candidates(full=True) — the --full backfill path ────────────────


def test_select_candidates_full_returns_everything_even_an_existing_match():
    panel = [{"fuente": "atencionsismo", "registro_id": "A"},
             {"fuente": "atencionsismo", "registro_id": "B"}]
    state = {
        "atencionsismo_A": {"exists": True, "tiene_survey": False},
        "atencionsismo_B": {"exists": True, "tiene_survey": True},
    }

    cands = job.select_candidates(panel, state, full=True)

    assert {p["registro_id"] for p in cands} == {"A", "B"}


def test_select_candidates_full_false_is_the_default_incremental_behavior():
    panel = [{"fuente": "atencionsismo", "registro_id": "A"}]
    state = {"atencionsismo_A": {"exists": True, "tiene_survey": True}}

    assert job.select_candidates(panel, state) == []
    assert job.select_candidates(panel, state, full=False) == []


# ── tag_duplicados — grouping, never collapsing (module docstring's "Dedup
# tagging" section) ─────────────────────────────────────────────────────────


def _dpt(registro_id, direccion=None, lat=None, lon=None):
    return {"fuente": "atencionsismo", "registro_id": registro_id,
            "direccion": direccion, "lat": lat, "lon": lon}


def test_tag_duplicados_near_pair_sharing_an_address_is_one_group():
    a = _dpt("10", "Calle 1 # 2-3", 3.42000, -76.53000)
    b = _dpt("20", "Calle 1 # 2-3", 3.42005, -76.53005)  # a few meters away

    tagged = {p["registro_id"]: p for p in job.tag_duplicados([a, b])}

    assert tagged["10"]["dup_grupo_id"] == tagged["20"]["dup_grupo_id"] == "dup-10"
    assert tagged["10"]["dup_n"] == 2 and tagged["20"]["dup_n"] == 2
    assert tagged["10"]["es_representante"] is True
    assert tagged["20"]["es_representante"] is False


def test_tag_duplicados_far_apart_same_address_is_two_groups():
    a = _dpt("10", "Calle 1 # 2-3", 3.4200, -76.5300)
    b = _dpt("20", "Calle 1 # 2-3", 3.9000, -76.9000)  # far away, same address text

    tagged = {p["registro_id"]: p for p in job.tag_duplicados([a, b], max_dist_m=30.0)}

    assert tagged["10"]["dup_grupo_id"] != tagged["20"]["dup_grupo_id"]
    assert tagged["10"]["dup_n"] == 1 and tagged["20"]["dup_n"] == 1
    assert tagged["10"]["es_representante"] is True
    assert tagged["20"]["es_representante"] is True


def test_tag_duplicados_transitive_chain_of_three():
    # a-b within max_dist_m, b-c within max_dist_m, a-c NOT directly within
    # max_dist_m -- the chain must still join all three into one group.
    a = _dpt("10", "Calle 1 # 2-3", 3.420000, -76.530000)
    b = _dpt("20", "Calle 1 # 2-3", 3.420180, -76.530000)  # ~20m from a
    c = _dpt("30", "Calle 1 # 2-3", 3.420360, -76.530000)  # ~20m from b, ~40m from a

    tagged = {p["registro_id"]: p for p in job.tag_duplicados([a, b, c], max_dist_m=25.0)}

    assert tagged["10"]["dup_grupo_id"] == tagged["20"]["dup_grupo_id"] == tagged["30"]["dup_grupo_id"]
    assert tagged["10"]["dup_n"] == 3


def test_tag_duplicados_dup_grupo_id_is_order_independent():
    a = _dpt("10", "Calle 1 # 2-3", 3.42000, -76.53000)
    b = _dpt("20", "Calle 1 # 2-3", 3.42005, -76.53005)

    forward = {p["registro_id"]: p for p in job.tag_duplicados([a, b])}
    shuffled = {p["registro_id"]: p for p in job.tag_duplicados([b, a])}

    assert forward["10"]["dup_grupo_id"] == shuffled["10"]["dup_grupo_id"] == "dup-10"
    assert forward["20"]["dup_grupo_id"] == shuffled["20"]["dup_grupo_id"] == "dup-10"


def test_tag_duplicados_points_missing_coords_still_get_tagged_safely():
    sin_coords = _dpt("40", direccion=None, lat=None, lon=None)

    tagged = job.tag_duplicados([sin_coords])

    assert tagged[0]["dup_grupo_id"] == "dup-40"
    assert tagged[0]["dup_n"] == 1
    assert tagged[0]["es_representante"] is True


def test_tag_duplicados_missing_coords_joins_rather_than_splits_its_bucket():
    # Same address bucket, one member has no coords -- can't be RULED OUT as
    # the same building, so it must join rather than force a split.
    known = _dpt("10", "Calle 1 # 2-3", 3.4200, -76.5300)
    unknown = _dpt("20", "Calle 1 # 2-3", lat=None, lon=None)

    tagged = {p["registro_id"]: p for p in job.tag_duplicados([known, unknown])}

    assert tagged["10"]["dup_grupo_id"] == tagged["20"]["dup_grupo_id"]
    assert tagged["10"]["dup_n"] == 2


def test_pipeline_fields_includes_the_three_dedup_fields():
    assert {"dup_grupo_id", "dup_n", "es_representante"} <= set(job.PIPELINE_FIELDS)


def test_build_write_ops_passes_dedup_fields_through_untouched():
    p = _pipeline_point()
    p["dup_grupo_id"], p["dup_n"], p["es_representante"] = "dup-14832", 2, True
    did = job.doc_id("atencionsismo", "14832")

    ops = job.build_write_ops([p], existing_ids={did}, estado_actual={did: "pendiente"})
    fields = dict(ops)[did]

    assert fields["dup_grupo_id"] == "dup-14832"
    assert fields["dup_n"] == 2
    assert fields["es_representante"] is True


# ── write_state / read_last_run — the last-run summary the status route reads ─


class _FakeStateDoc:
    def __init__(self, store, path):
        self._store, self._path = store, path

    def set(self, data, merge=False):
        current = self._store.get(self._path, {})
        if merge:
            current = {**current, **data}
        else:
            current = data
        self._store[self._path] = current

    def get(self):
        data = self._store.get(self._path)

        class _Snap:
            exists = data is not None

            def to_dict(inner_self):
                return dict(data) if data is not None else None

        return _Snap()


class _FakeStateDb:
    """Minimal fake covering only `write_state`/`read_last_run`'s own calls:
    `.collection(name).document(name)` and `.document('coll/name')`."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    def collection(self, name):
        store, prefix = self._store, name

        class _Col:
            def document(self_inner, doc_name):
                return _FakeStateDoc(store, f"{prefix}/{doc_name}")

        return _Col()

    def document(self, path):
        return _FakeStateDoc(self._store, path)


def test_write_state_then_read_last_run_round_trips_the_summary():
    db = _FakeStateDb()
    when = datetime(2026, 8, 27, tzinfo=timezone.utc)
    summary = {"total_puntos": 10, "a_escribir": 3}

    job.write_state(db, when, summary, full=True)
    last_run = job.read_last_run(db)

    assert last_run["total_puntos"] == 10
    assert last_run["a_escribir"] == 3
    assert last_run["full"] is True
    assert last_run["finished_at"] == when


def test_read_last_run_returns_none_when_no_run_has_ever_completed():
    db = _FakeStateDb()

    assert job.read_last_run(db) is None


# ── main() argv shim — --top/--dry/--full forwarded as kwargs ──────────────


def test_main_forwards_top_dry_full_from_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["planeacion_cruce.py", "--top", "50", "--dry", "--full"])
    calls = []
    monkeypatch.setattr(job, "run_planeacion_cruce", lambda **kw: calls.append(kw) or {})

    assert job.main() == 0
    assert calls == [{"top": 50, "dry": True, "full": True}]


def test_main_defaults_top_none_dry_false_full_false(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["planeacion_cruce.py"])
    calls = []
    monkeypatch.setattr(job, "run_planeacion_cruce", lambda **kw: calls.append(kw) or {})

    assert job.main() == 0
    assert calls == [{"top": None, "dry": False, "full": False}]


# Real-UUID round trip (regression: ADR-3's worked example used a short id) ----
# Every real atencionsismo `id` is a UUID, so the minted slug is ALWAYS lossy
# (32 hex chars sanitized, truncated to 24). These lock the property that
# actually matters -- a key minted for a real point must survive the pipeline
# and pair back to that point -- rather than the stateless self-recompute,
# which is impossible for a lossy slug by construction.

REAL_UUID = "00035fab-a24a-4f3c-a713-4712196d0bfd"
OTHER_UUID = "ff4e643a-4057-4b49-bea1-a82f8bee9e81"


def test_key_minted_for_a_real_uuid_point_survives_the_key_index():
    clave = job.clave_integracion(job.FUENTE, REAL_UUID)
    index = job.build_key_index([{"GlobalID": "g-1", "codigoapp": clave}])

    assert clave in index, (
        "a correctly minted key for a REAL (UUID) registro_id must not be "
        "discarded by build_key_index -- discarding it kills rung-1 matching "
        "for every real point in production"
    )
    assert index[clave]["GlobalID"] == "g-1"


def test_two_uuids_sharing_a_24_char_slug_prefix_still_mint_distinct_keys():
    # A UUID sanitizes to 32 hex chars, so the 24-char slug cap drops the last
    # 8. These two differ ONLY in those dropped chars -> identical slugs, and
    # the digest (taken over the FULL id) is the only thing telling them apart.
    a = "aaaaaaaa-bbbb-cccc-dddd-eeee11111111"
    b = "aaaaaaaa-bbbb-cccc-dddd-eeee22222222"
    ka, kb = job.clave_integracion(job.FUENTE, a), job.clave_integracion(job.FUENTE, b)

    assert ka.split("-")[1] == kb.split("-")[1], "precondition: slugs collide"
    assert ka != kb, "the digest must disambiguate a slug collision"


def test_garbage_codigoapp_values_are_still_rejected_by_the_index():
    for junk in ("", None, "hola", "PLN-", "PLN-ABC", "XXX-14832-55C9286D",
                 "PLN-14832-ZZZZZZZZ", "PLN-lowercase-55C9286D"):
        assert job.build_key_index([{"GlobalID": "g", "codigoapp": junk}]) == {}, junk


def test_a_survey_key_never_pairs_with_a_different_point():
    mine = job.clave_integracion(job.FUENTE, REAL_UUID)
    theirs = job.clave_integracion(job.FUENTE, OTHER_UUID)
    index = job.build_key_index([{"GlobalID": "g-other", "codigoapp": theirs}])

    assert index.get(mine) is None
