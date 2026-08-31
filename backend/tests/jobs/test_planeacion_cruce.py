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


# ── read_punto_state: batched getAll, throttled between chunks (30-ago-2026,
# a burst of ~30 back-to-back get_all() calls over the full ~14.8k-doc
# collection was tripping Firestore's per-project read-RATE quota) ─────────


class _FakePuntoSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _FakePuntoRef:
    def __init__(self, doc_id):
        self.id = doc_id


class _FakePuntoCollection:
    def document(self, doc_id):
        return _FakePuntoRef(doc_id)


class _FakePuntoDb:
    """Records every get_all() call's ref count, so the test can assert the
    chunking (BATCH_SIZE) and throttle (sleep between, never before/after)
    without a real Firestore client."""

    def __init__(self, known_ids):
        self._known_ids = set(known_ids)
        self.get_all_calls: list[int] = []  # ref count per call

    def collection(self, name):
        return _FakePuntoCollection()

    def get_all(self, refs, field_paths=None):
        self.get_all_calls.append(len(refs))
        return [
            _FakePuntoSnap(
                ref.id,
                {"tiene_survey": True, "clave_integracion": f"k-{ref.id}", "estado_asignacion": "pendiente"}
                if ref.id in self._known_ids
                else None,
            )
            for ref in refs
        ]


def test_read_punto_state_chunks_at_batch_size_and_merges_all_results(monkeypatch):
    monkeypatch.setattr(job, "BATCH_READ_THROTTLE_S", 0)  # keep the test instant
    doc_ids = [f"id-{i}" for i in range(job.BATCH_SIZE + 1)]  # forces exactly 2 chunks
    db = _FakePuntoDb(known_ids=doc_ids[:1])

    result = job.read_punto_state(db, doc_ids)

    assert db.get_all_calls == [job.BATCH_SIZE, 1]  # chunked, not one giant call
    assert len(result) == len(doc_ids)
    assert result["id-0"]["exists"] is True
    assert result["id-0"]["tiene_survey"] is True
    assert result[f"id-{job.BATCH_SIZE}"]["exists"] is False


def test_read_punto_state_sleeps_between_chunks_but_not_before_the_first_or_after_the_last(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(job.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(job, "BATCH_READ_THROTTLE_S", 0.15)
    doc_ids = [f"id-{i}" for i in range(job.BATCH_SIZE * 3)]  # 3 chunks -> 2 gaps
    db = _FakePuntoDb(known_ids=[])

    job.read_punto_state(db, doc_ids)

    assert sleeps == [0.15, 0.15]


def test_read_punto_state_single_chunk_never_sleeps(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(job.time, "sleep", lambda s: sleeps.append(s))
    db = _FakePuntoDb(known_ids=[])

    job.read_punto_state(db, ["id-0", "id-1"])

    assert sleeps == []


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


# ── run_planeacion_cruce early-exit gate: skip the expensive reads when
# nothing changed since the last run ────────────────────────────────────────


class _FakeAggRow:
    def __init__(self, value):
        self.value = value


class _FakeAggQuery:
    def __init__(self, count):
        self._count = count

    def get(self):
        return [[_FakeAggRow(self._count)]]


class _FakeGateCol:
    def __init__(self, count):
        self._count = count

    def count(self):
        return _FakeAggQuery(self._count)


class _FakeGateDb:
    """Minimal fake for `run_planeacion_cruce`'s gate path — only
    `.collection(EVALUACIONES_COLLECTION).count().get()` needs to be real;
    every other Firestore call the FULL path would make is monkeypatched
    away at the module-function level in the tests below (ponytail: no
    heavier fake db than the gate itself actually touches)."""
    project = "fake-project"

    def __init__(self, evaluaciones_count):
        self._evaluaciones_count = evaluaciones_count

    def collection(self, name):
        if name == job.EVALUACIONES_COLLECTION:
            return _FakeGateCol(self._evaluaciones_count)
        raise AssertionError(f"unexpected collection() call in gate test: {name}")


def _never_called(name):
    def _fake(*a, **kw):
        raise AssertionError(f"{name} must not be called when the gate fires")
    return _fake


def test_evaluaciones_count_reads_the_count_aggregate_shape():
    class _Row:
        value = 42

    class _Agg:
        def get(self_inner):
            return [[_Row()]]

    class _Col:
        def count(self_inner):
            return _Agg()

    class _Db:
        def collection(self_inner, name):
            return _Col()

    assert job._evaluaciones_count(_Db()) == 42


def test_run_is_a_noop_when_nothing_changed(monkeypatch):
    puntos = [{"fuente": "atencionsismo", "registro_id": "1", "lat": 3.42, "lon": -76.53,
              "direccion": "CL 1", "barrio": None, "comuna": None, "coords": None,
              "afectacion": None, "estado_verificacion": None, "tipo_inmueble": None,
              "habitabilidad": None, "fecha_creacion": None}]
    expected_hash = job._hash_puntos(puntos)
    when = datetime(2026, 8, 28, tzinfo=timezone.utc)

    monkeypatch.setattr(job, "load_puntos", lambda: puntos)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeGateDb(7)})())
    monkeypatch.setattr(job, "read_state", lambda db: {
        "last_run_at": when, "puntos_hash": expected_hash, "evaluaciones_count": 7,
        "israel_count": 3,
    })
    monkeypatch.setattr(job, "fetch_surveys", lambda db, watermark: [])
    monkeypatch.setattr(job, "_evaluaciones_count", lambda db: 7)
    monkeypatch.setattr(job, "_israel_count", lambda db: 3)
    monkeypatch.setattr(job, "read_punto_state", _never_called("read_punto_state"))
    monkeypatch.setattr(job, "fetch_evaluaciones", _never_called("fetch_evaluaciones"))
    monkeypatch.setattr(job, "fetch_israel", _never_called("fetch_israel"))
    touched = []
    monkeypatch.setattr(job, "touch_last_checked", lambda db, now: touched.append(now))

    summary = job.run_planeacion_cruce()

    assert summary["noop"] is True
    assert touched, "touch_last_checked must stamp the doc on a no-op run"


def test_run_executes_fully_when_puntos_hash_changed(monkeypatch):
    puntos = [{"fuente": "atencionsismo", "registro_id": "1", "lat": 3.42, "lon": -76.53,
              "direccion": "CL 1", "barrio": None, "comuna": None, "coords": None,
              "afectacion": None, "estado_verificacion": None, "tipo_inmueble": None,
              "habitabilidad": None, "fecha_creacion": None}]
    when = datetime(2026, 8, 28, tzinfo=timezone.utc)

    monkeypatch.setattr(job, "load_puntos", lambda: puntos)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeGateDb(7)})())
    monkeypatch.setattr(job, "read_state", lambda db: {
        "last_run_at": when, "puntos_hash": "stale-hash", "evaluaciones_count": 7,
        "israel_count": 3,
    })
    monkeypatch.setattr(job, "fetch_surveys", lambda db, watermark: [])
    monkeypatch.setattr(job, "_evaluaciones_count", lambda db: 7)
    monkeypatch.setattr(job, "_israel_count", lambda db: 3)

    calls = []
    monkeypatch.setattr(job, "read_punto_state", lambda db, ids: calls.append("read_punto_state") or {})
    monkeypatch.setattr(job, "fetch_evaluaciones", lambda db: calls.append("fetch_evaluaciones") or [])
    monkeypatch.setattr(job, "fetch_israel", lambda db: calls.append("fetch_israel") or [])
    monkeypatch.setattr(job, "write_planeacion_puntos", lambda db, ops: len(ops))
    write_state_calls = []
    monkeypatch.setattr(job, "write_state",
                        lambda db, now, summary, **kw: write_state_calls.append(kw))
    monkeypatch.setattr(job, "load_resolved_cache", lambda: set())
    monkeypatch.setattr(job, "publish_resolved_cache", lambda ids: None)

    summary = job.run_planeacion_cruce()

    assert {"read_punto_state", "fetch_evaluaciones", "fetch_israel"} <= set(calls)
    assert "noop" not in summary
    assert write_state_calls[-1]["puntos_hash"] == job._hash_puntos(puntos)
    assert write_state_calls[-1]["evaluaciones_count"] == 7
    assert write_state_calls[-1]["israel_count"] == 3


def test_run_executes_fully_when_new_surveys_arrive(monkeypatch):
    puntos = [{"fuente": "atencionsismo", "registro_id": "1", "lat": 3.42, "lon": -76.53,
              "direccion": "CL 1", "barrio": None, "comuna": None, "coords": None,
              "afectacion": None, "estado_verificacion": None, "tipo_inmueble": None,
              "habitabilidad": None, "fecha_creacion": None}]
    expected_hash = job._hash_puntos(puntos)
    when = datetime(2026, 8, 28, tzinfo=timezone.utc)
    nueva_survey = {"GlobalID": "g1", "Y": 3.42, "X": -76.53, "DIRECCION": "CL 1", "codigoapp": ""}

    monkeypatch.setattr(job, "load_puntos", lambda: puntos)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeGateDb(7)})())
    monkeypatch.setattr(job, "read_state", lambda db: {
        "last_run_at": when, "puntos_hash": expected_hash, "evaluaciones_count": 7,
        "israel_count": 3,
    })
    monkeypatch.setattr(job, "fetch_surveys", lambda db, watermark: [nueva_survey])
    monkeypatch.setattr(job, "_evaluaciones_count", lambda db: 7)
    monkeypatch.setattr(job, "_israel_count", lambda db: 3)

    calls = []
    monkeypatch.setattr(job, "read_punto_state", lambda db, ids: calls.append("read_punto_state") or {})
    monkeypatch.setattr(job, "fetch_evaluaciones", lambda db: calls.append("fetch_evaluaciones") or [])
    monkeypatch.setattr(job, "fetch_israel", lambda db: calls.append("fetch_israel") or [])
    monkeypatch.setattr(job, "write_planeacion_puntos", lambda db, ops: len(ops))
    monkeypatch.setattr(job, "write_state", lambda db, now, summary, **kw: None)
    monkeypatch.setattr(job, "load_resolved_cache", lambda: set())
    monkeypatch.setattr(job, "publish_resolved_cache", lambda ids: None)

    summary = job.run_planeacion_cruce()

    assert {"read_punto_state", "fetch_evaluaciones", "fetch_israel"} <= set(calls)
    assert "noop" not in summary


def test_run_executes_fully_when_israel_count_changes(monkeypatch):
    puntos = [{"fuente": "atencionsismo", "registro_id": "1", "lat": 3.42, "lon": -76.53,
              "direccion": "CL 1", "barrio": None, "comuna": None, "coords": None,
              "afectacion": None, "estado_verificacion": None, "tipo_inmueble": None,
              "habitabilidad": None, "fecha_creacion": None}]
    expected_hash = job._hash_puntos(puntos)
    when = datetime(2026, 8, 28, tzinfo=timezone.utc)

    monkeypatch.setattr(job, "load_puntos", lambda: puntos)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeGateDb(7)})())
    monkeypatch.setattr(job, "read_state", lambda db: {
        "last_run_at": when, "puntos_hash": expected_hash, "evaluaciones_count": 7,
        "israel_count": 3,
    })
    monkeypatch.setattr(job, "fetch_surveys", lambda db, watermark: [])
    monkeypatch.setattr(job, "_evaluaciones_count", lambda db: 7)
    monkeypatch.setattr(job, "_israel_count", lambda db: 4)  # only the changed signal

    calls = []
    monkeypatch.setattr(job, "read_punto_state", lambda db, ids: calls.append("read_punto_state") or {})
    monkeypatch.setattr(job, "fetch_evaluaciones", lambda db: calls.append("fetch_evaluaciones") or [])
    monkeypatch.setattr(job, "fetch_israel", lambda db: calls.append("fetch_israel") or [])
    monkeypatch.setattr(job, "write_planeacion_puntos", lambda db, ops: len(ops))
    monkeypatch.setattr(job, "write_state", lambda db, now, summary, **kw: None)
    monkeypatch.setattr(job, "load_resolved_cache", lambda: set())
    monkeypatch.setattr(job, "publish_resolved_cache", lambda ids: None)

    summary = job.run_planeacion_cruce()

    assert {"read_punto_state", "fetch_evaluaciones", "fetch_israel"} <= set(calls)
    assert "noop" not in summary


def test_israel_count_reads_the_count_aggregate_shape():
    class _Row:
        value = 101

    class _Agg:
        def get(self_inner):
            return [[_Row()]]

    class _Col:
        def count(self_inner):
            return _Agg()

    class _Db:
        def collection(self_inner, name):
            return _Col()

    assert job._israel_count(_Db()) == 101


# ── read-reduction: known-resolved Blob cache narrows read_punto_state ─────


def _puntos_minimos(*registro_ids: str) -> list[dict]:
    """Minimal `load_puntos()`-shaped points, one per given registro_id —
    reused across the read-reduction tests below."""
    return [{"fuente": "atencionsismo", "registro_id": rid, "lat": 3.42, "lon": -76.53,
             "direccion": f"CL {rid}", "barrio": None, "comuna": None, "coords": None,
             "afectacion": None, "estado_verificacion": None, "tipo_inmueble": None,
             "habitabilidad": None, "fecha_creacion": None}
            for rid in registro_ids]


def _setup_read_reduction_run(monkeypatch, puntos, *, cache_ids, state_by_id=None):
    """Common monkeypatching for the read-reduction tests: the gate never
    early-exits (stale `puntos_hash`), no surveys/evaluaciones/israel, and
    `read_punto_state`/`write_planeacion_puntos` are recorded rather than
    hitting Firestore. `load_resolved_cache` returns `cache_ids`;
    `publish_resolved_cache` is a no-op by default (a test that needs to
    inspect the published set overrides it AFTER calling this helper).
    Returns (ids_seen, ops_seen) — lists of, respectively, the doc_ids passed
    to each `read_punto_state` call and the ops passed to each
    `write_planeacion_puntos` call."""
    when = datetime(2026, 8, 28, tzinfo=timezone.utc)
    state_by_id = state_by_id or {}

    monkeypatch.setattr(job, "load_puntos", lambda: puntos)
    monkeypatch.setattr(job.credentials, "sismo",
                        lambda: type("C", (), {"firestore": _FakeGateDb(7)})())
    monkeypatch.setattr(job, "read_state", lambda db: {
        "last_run_at": when, "puntos_hash": "stale-hash", "evaluaciones_count": 7,
        "israel_count": 3,
    })
    monkeypatch.setattr(job, "fetch_surveys", lambda db, watermark: [])
    monkeypatch.setattr(job, "_evaluaciones_count", lambda db: 7)
    monkeypatch.setattr(job, "_israel_count", lambda db: 3)
    monkeypatch.setattr(job, "fetch_evaluaciones", lambda db: [])
    monkeypatch.setattr(job, "fetch_israel", lambda db: [])
    monkeypatch.setattr(job, "write_state", lambda db, now, summary, **kw: None)
    monkeypatch.setattr(job, "load_resolved_cache", lambda: set(cache_ids))
    monkeypatch.setattr(job, "publish_resolved_cache", lambda ids: None)

    ids_seen: list[list[str]] = []

    def _fake_read_punto_state(db, ids):
        ids_seen.append(list(ids))
        default = {"exists": False, "tiene_survey": False,
                   "clave_integracion": None, "estado_asignacion": None}
        return {d: dict(state_by_id.get(d, default)) for d in ids}

    monkeypatch.setattr(job, "read_punto_state", _fake_read_punto_state)

    ops_seen: list[list[tuple[str, dict]]] = []

    def _fake_write_ops(db, ops):
        ops_seen.append(ops)
        return len(ops)

    monkeypatch.setattr(job, "write_planeacion_puntos", _fake_write_ops)

    return ids_seen, ops_seen


def test_first_run_empty_cache_reads_all_ids(monkeypatch):
    puntos = _puntos_minimos("1", "2")
    ids_seen, _ = _setup_read_reduction_run(monkeypatch, puntos, cache_ids=set())

    job.run_planeacion_cruce()

    expected = {job.doc_id("atencionsismo", "1"), job.doc_id("atencionsismo", "2")}
    assert set(ids_seen[0]) == expected


def test_second_run_only_rechecks_unresolved_ids(monkeypatch):
    puntos = _puntos_minimos("1", "2")
    cache = {job.doc_id("atencionsismo", "1")}
    ids_seen, _ = _setup_read_reduction_run(monkeypatch, puntos, cache_ids=cache)

    job.run_planeacion_cruce()

    assert sorted(ids_seen[0]) == sorted([job.doc_id("atencionsismo", "2")])


def test_newly_resolved_point_is_published_to_cache(monkeypatch):
    puntos = _puntos_minimos("2")
    did2 = job.doc_id("atencionsismo", "2")
    state_by_id = {did2: {"exists": True, "tiene_survey": True,
                          "clave_integracion": None, "estado_asignacion": None}}
    _setup_read_reduction_run(monkeypatch, puntos, cache_ids=set(), state_by_id=state_by_id)

    published = []
    monkeypatch.setattr(job, "publish_resolved_cache", lambda ids: published.append(ids))

    job.run_planeacion_cruce()

    assert published, "publish_resolved_cache must be called on a real (non-dry) run"
    assert did2 in published[-1]


def test_full_run_ignores_cache_and_reads_everything(monkeypatch):
    puntos = _puntos_minimos("1", "2")
    cache = {job.doc_id("atencionsismo", "1")}
    ids_seen, _ = _setup_read_reduction_run(monkeypatch, puntos, cache_ids=cache)

    def _must_not_be_called():
        raise AssertionError("load_resolved_cache must not be consulted on --full")
    monkeypatch.setattr(job, "load_resolved_cache", _must_not_be_called)

    job.run_planeacion_cruce(full=True)

    expected = {job.doc_id("atencionsismo", "1"), job.doc_id("atencionsismo", "2")}
    assert set(ids_seen[0]) == expected


def test_resolved_cached_point_is_never_a_candidate_or_rewritten(monkeypatch):
    """Correctness-critical: a point the cache says is already resolved must
    never appear in a write op — otherwise the auto-close exception in
    `build_write_ops` could silently touch its `estado_asignacion` (or a
    future rung could try to re-match/overwrite it) even though it was never
    re-read this run."""
    puntos = _puntos_minimos("1", "2")
    did1 = job.doc_id("atencionsismo", "1")
    cache = {did1}
    _, ops_seen = _setup_read_reduction_run(monkeypatch, puntos, cache_ids=cache)

    job.run_planeacion_cruce()

    written_ids = {did for ops in ops_seen for did, _ in ops}
    assert did1 not in written_ids


def test_brand_new_point_not_in_cache_is_checked(monkeypatch):
    puntos = _puntos_minimos("1", "2", "3")
    cache = {job.doc_id("atencionsismo", "1")}
    ids_seen, _ = _setup_read_reduction_run(monkeypatch, puntos, cache_ids=cache)

    job.run_planeacion_cruce()

    expected = {job.doc_id("atencionsismo", "2"), job.doc_id("atencionsismo", "3")}
    assert set(ids_seen[0]) == expected
