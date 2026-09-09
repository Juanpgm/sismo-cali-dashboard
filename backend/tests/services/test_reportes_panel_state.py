"""backend/tests/services/test_reportes_panel_state.py — RED-first tests for
the Blob-persisted incremental state around reportes_panel_match's grid join
(backend/app/services/reportes_panel_state.py). `blob_lkg` is mocked via
monkeypatch on the module-level reference (`reportes_panel_state.blob_lkg`)
everywhere — never a real network/Blob call, same convention this repo's
other blob_lkg-consuming tests use (see routers/sticker_status tests)."""
from __future__ import annotations

import logging

from app.services import reportes_panel_state as st


def _panel(registro_id, lat, lon, direccion=""):
    return {"fuente": "ede", "registro_id": registro_id, "lat": lat, "lon": lon,
            "direccion": direccion, "zona_id": None, "criterio_habitabilidad": None,
            "colapso": "no"}


def _reporte(rid, lat=3.42, lng=-76.53, direccion="Calle 1 # 2-3"):
    return {"id": rid, "lat": lat, "lng": lng, "direccion": direccion}


class _FakeBlob:
    """Stand-in for app.services.blob_lkg — records calls, lets tests script
    load_json/save_json's return values without any real I/O."""

    def __init__(self, *, load_result=None, save_result=True):
        self.saved: list[tuple[str, object]] = []
        self.load_calls: list[str] = []
        self._load_result = load_result
        self._save_result = save_result
        self.load_json_impl = None
        self.save_json_impl = None

    def load_json(self, pathname, expected_type):
        self.load_calls.append(pathname)
        if self.load_json_impl is not None:
            return self.load_json_impl(pathname, expected_type)
        return self._load_result

    def save_json(self, pathname, payload):
        if self.save_json_impl is not None:
            return self.save_json_impl(pathname, payload)
        self.saved.append((pathname, payload))
        return self._save_result


# ── load_state ───────────────────────────────────────────────────────────────

def test_load_state_returns_matches_from_blob(monkeypatch):
    fake = _FakeBlob(load_result={"matches": {"r1": {"panel_registro_id": "ede_1", "tier": "alta", "dist_m": 5.0}}})
    monkeypatch.setattr(st, "blob_lkg", fake)
    assert st.load_state() == {"matches": {"r1": {"panel_registro_id": "ede_1", "tier": "alta", "dist_m": 5.0}}}


def test_load_state_first_run_shape_when_blob_returns_none(monkeypatch):
    fake = _FakeBlob(load_result=None)
    monkeypatch.setattr(st, "blob_lkg", fake)
    assert st.load_state() == {"matches": {}}


def test_load_state_fails_soft_on_wrong_shaped_payload_a_list(monkeypatch):
    fake = _FakeBlob(load_result=["not", "a", "dict"])
    monkeypatch.setattr(st, "blob_lkg", fake)
    assert st.load_state() == {"matches": {}}


def test_load_state_fails_soft_when_matches_key_is_missing_or_wrong_type(monkeypatch):
    monkeypatch.setattr(st, "blob_lkg", _FakeBlob(load_result={}))
    assert st.load_state() == {"matches": {}}
    monkeypatch.setattr(st, "blob_lkg", _FakeBlob(load_result={"matches": "not-a-dict"}))
    assert st.load_state() == {"matches": {}}


# ── save_state ───────────────────────────────────────────────────────────────

def test_save_state_calls_blob_lkg_save_json(monkeypatch):
    fake = _FakeBlob()
    monkeypatch.setattr(st, "blob_lkg", fake)
    st.save_state({"matches": {"r1": {"panel_registro_id": "ede_1", "tier": "alta", "dist_m": 1.0}}})
    assert fake.saved == [(st.PANEL_MATCH_STATE_BLOB, {"matches": {"r1": {"panel_registro_id": "ede_1", "tier": "alta", "dist_m": 1.0}}})]


def test_save_state_does_not_raise_when_blob_save_returns_false(monkeypatch):
    fake = _FakeBlob(save_result=False)
    monkeypatch.setattr(st, "blob_lkg", fake)
    st.save_state({"matches": {}})  # must not raise


# ── load_sticker_coverage ────────────────────────────────────────────────────

def test_load_sticker_coverage_returns_set_of_ids(monkeypatch):
    fake = _FakeBlob(load_result={"con_sticker": ["1", "2", "3"], "total": 10, "con": 3})
    monkeypatch.setattr(st, "blob_lkg", fake)
    assert st.load_sticker_coverage() == {"1", "2", "3"}


def test_load_sticker_coverage_empty_set_when_blob_returns_none(monkeypatch):
    monkeypatch.setattr(st, "blob_lkg", _FakeBlob(load_result=None))
    assert st.load_sticker_coverage() == set()


def test_load_sticker_coverage_empty_set_when_con_sticker_missing_or_wrong_type(monkeypatch):
    monkeypatch.setattr(st, "blob_lkg", _FakeBlob(load_result={"total": 0}))
    assert st.load_sticker_coverage() == set()
    monkeypatch.setattr(st, "blob_lkg", _FakeBlob(load_result={"con_sticker": "not-a-list"}))
    assert st.load_sticker_coverage() == set()


def test_load_sticker_coverage_empty_set_when_payload_not_a_dict(monkeypatch):
    monkeypatch.setattr(st, "blob_lkg", _FakeBlob(load_result=["oops"]))
    assert st.load_sticker_coverage() == set()


# ── select_candidatos: the incremental core ─────────────────────────────────

def test_select_candidatos_excludes_ids_already_in_state_matches():
    reportes = [_reporte("r1"), _reporte("r2"), _reporte("r3")]
    state = {"matches": {"r1": {"panel_registro_id": "ede_1", "tier": "alta", "dist_m": 1.0}}}
    out = st.select_candidatos(reportes, state)
    assert {r["id"] for r in out} == {"r2", "r3"}


def test_select_candidatos_empty_reportes():
    assert st.select_candidatos([], {"matches": {}}) == []


def test_select_candidatos_empty_state_returns_everything():
    reportes = [_reporte("r1"), _reporte("r2")]
    assert st.select_candidatos(reportes, {"matches": {}}) == reportes


def test_select_candidatos_same_reporte_excluded_on_second_call_even_if_unchanged():
    """Invariant 1: a reporte already resolved is excluded even when passed
    again with the EXACT same input dict."""
    reportes = [_reporte("r1"), _reporte("r2")]
    state = {"matches": {}}
    first = st.select_candidatos(reportes, state)
    assert {r["id"] for r in first} == {"r1", "r2"}
    state_after = {"matches": {"r1": {"panel_registro_id": "ede_1", "tier": "alta", "dist_m": 1.0}}}
    second = st.select_candidatos(reportes, state_after)
    assert {r["id"] for r in second} == {"r2"}


def test_select_candidatos_unmatched_reporte_remains_a_candidate_next_run():
    """Invariant 4: a reporte that did NOT match (absent from state after a
    run) must remain a candidate the next time — so a Panel point added
    later can still match it."""
    reportes = [_reporte("r1"), _reporte("r2")]
    state = {"matches": {}}  # r1 ran last time and found nothing -> still absent
    out = st.select_candidatos(reportes, state)
    assert {r["id"] for r in out} == {"r1", "r2"}


# ── apply_panel_match: orchestration ────────────────────────────────────────

def test_apply_panel_match_first_run_matches_and_marks_visitado(monkeypatch):
    fake = _FakeBlob(load_result=None)
    monkeypatch.setattr(st, "blob_lkg", fake)
    panel = [_panel("1", 3.42, -76.53, "Calle 1 # 2-3")]
    reportes = [_reporte("r1", 3.42001, -76.53001, "otra direccion")]

    out = st.apply_panel_match(reportes, panel_loader=lambda: panel)

    assert out[0]["panel"]["visitado"] is True
    assert out[0]["panel"]["sticker"] is False  # sticker-status blob is None -> empty coverage
    assert out is not reportes
    assert out[0] is not reportes[0]


def test_apply_panel_match_no_match_returns_visitado_false():
    panel = [_panel("1", 3.9999, -76.9999, "Nada que ver")]
    reportes = [_reporte("r1", 3.4200, -76.5300, "Otra direccion muy distinta # 1-1")]

    out = st.apply_panel_match(reportes, panel_loader=lambda: panel, blob_lkg_module=_FakeBlob(load_result=None))

    assert out[0]["panel"] == {"visitado": False, "sticker": False}


def test_apply_panel_match_never_mutates_input_reportes(monkeypatch):
    panel = [_panel("1", 3.42, -76.53, "Calle 1 # 2-3")]
    reportes = [_reporte("r1", 3.42, -76.53, "x")]
    before = [dict(r) for r in reportes]

    st.apply_panel_match(reportes, panel_loader=lambda: panel, blob_lkg_module=_FakeBlob(load_result=None))

    assert reportes == before


def test_apply_panel_match_second_call_reuses_state_no_new_candidatos(monkeypatch):
    """Invariant 2: an unchanged reportes list + unchanged Panel must not
    re-run cross_reference/match for already-resolved reportes on a second
    call — proven via the panel_loader mock's call count."""
    panel = [_panel("1", 3.42, -76.53, "Calle 1 # 2-3")]
    reportes = [_reporte("r1", 3.42, -76.53, "x")]
    calls = []

    def loader():
        calls.append(1)
        return panel

    shared_state = {}

    class _StatefulBlob(_FakeBlob):
        def load_json(self, pathname, expected_type):
            self.load_calls.append(pathname)
            return shared_state.get(pathname)

        def save_json(self, pathname, payload):
            shared_state[pathname] = payload
            return True

    blob = _StatefulBlob()

    out1 = st.apply_panel_match(reportes, panel_loader=loader, blob_lkg_module=blob)
    assert out1[0]["panel"]["visitado"] is True
    assert len(calls) == 1

    out2 = st.apply_panel_match(reportes, panel_loader=loader, blob_lkg_module=blob)
    assert out2[0]["panel"]["visitado"] is True
    assert len(calls) == 1  # panel_loader NOT called again -> select_candidatos returned []


def test_apply_panel_match_sticker_flag_updates_without_rerunning_geo_match(monkeypatch):
    """Invariant 3: a reporte matched with sticker=False in one run must show
    sticker=True in a LATER run once its panel_registro_id appears in a fresh
    load_sticker_coverage() — WITHOUT re-running the geo/address match (the
    panel_loader must not be called again)."""
    panel = [_panel("1", 3.42, -76.53, "Calle 1 # 2-3")]
    reportes = [_reporte("r1", 3.42, -76.53, "x")]
    calls = []

    def loader():
        calls.append(1)
        return panel

    shared_state = {}
    sticker_payload = {"con_sticker": [], "total": 0, "con": 0}

    class _StatefulBlob(_FakeBlob):
        def load_json(self, pathname, expected_type):
            self.load_calls.append(pathname)
            if pathname == st.STICKER_STATUS_BLOB:
                return sticker_payload
            return shared_state.get(pathname)

        def save_json(self, pathname, payload):
            shared_state[pathname] = payload
            return True

    blob = _StatefulBlob()

    out1 = st.apply_panel_match(reportes, panel_loader=loader, blob_lkg_module=blob)
    assert out1[0]["panel"] == {"visitado": True, "sticker": False}
    assert len(calls) == 1

    sticker_payload["con_sticker"] = ["1"]  # the matched Panel point now has a sticker
    out2 = st.apply_panel_match(reportes, panel_loader=loader, blob_lkg_module=blob)
    assert out2[0]["panel"] == {"visitado": True, "sticker": True}
    assert len(calls) == 1  # geo/address match NOT re-run


def test_apply_panel_match_panel_loader_raising_is_caught_and_degrades_gracefully(monkeypatch, caplog):
    def boom():
        raise RuntimeError("panel source unavailable")

    reportes = [_reporte("r1")]
    with caplog.at_level(logging.ERROR):
        out = st.apply_panel_match(reportes, panel_loader=boom, blob_lkg_module=_FakeBlob(load_result=None))

    assert out[0]["panel"] == {"visitado": False, "sticker": False}


def test_apply_panel_match_empty_reportes_list():
    assert st.apply_panel_match([], panel_loader=lambda: [], blob_lkg_module=_FakeBlob(load_result=None)) == []


def test_apply_panel_match_caps_candidatos_passed_to_cross_reference_per_run(monkeypatch):
    """Fix 1a: an unbounded backlog of never-matched reportes must never be
    handed to reportes_panel_match.cross_reference in a single call — only
    PANEL_MATCH_MAX_CANDIDATES_PER_RUN of them, spread across future runs
    (the same way an unmatched reporte already remains a candidate on every
    future run). Proven via a spy on cross_reference's own argument length,
    not just the final output shape."""
    calls: list[list[dict]] = []

    def _spy_cross_reference(candidatos, panel_points_yx):
        calls.append(candidatos)
        return {}

    monkeypatch.setattr(st.reportes_panel_match, "cross_reference", _spy_cross_reference)

    total = st.PANEL_MATCH_MAX_CANDIDATES_PER_RUN + 137
    reportes = [_reporte(f"r{i}") for i in range(total)]
    panel = [_panel("1", 3.42, -76.53, "Calle 1 # 2-3")]

    st.apply_panel_match(reportes, panel_loader=lambda: panel, blob_lkg_module=_FakeBlob(load_result=None))

    assert len(calls) == 1
    assert len(calls[0]) == st.PANEL_MATCH_MAX_CANDIDATES_PER_RUN


def test_apply_panel_match_default_panel_loader_used_when_not_given(monkeypatch):
    """No panel_loader kwarg -> falls back to app.jobs.cruce_sticker.load_panel
    (imported locally, per the module docstring)."""
    import app.jobs.cruce_sticker as cruce_sticker_job

    monkeypatch.setattr(cruce_sticker_job, "load_panel", lambda: [
        {"fuente": "ede", "registro_id": "1", "lat": 3.42, "lon": -76.53, "direccion": "Calle 1 # 2-3",
         "zona_id": None, "criterio_habitabilidad": None, "colapso": "no"},
    ])
    reportes = [_reporte("r1", 3.42, -76.53, "x")]

    out = st.apply_panel_match(reportes, blob_lkg_module=_FakeBlob(load_result=None))

    assert out[0]["panel"]["visitado"] is True
