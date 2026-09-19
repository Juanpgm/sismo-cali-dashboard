"""Route-level wiring of the versioned component caches (D21-D24, tasks
10.22-10.24): roster (30 min), survey names (60 min), evaluaciones Firestore
side (15 min) and the referencia bundle feed ONE `depurar` per changed
fingerprint. Counting fakes plus an injectable clock; no real sleeps.

The route-level HTTP/ETag/bytes budget tests belong to slice 09b; this file
only asserts the unit-level mechanisms through the real route."""
from __future__ import annotations

import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.routers import stickers
from app.routers import stickers_atencionsismo as router_mod
from app.services import atencionsismo
from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle
from tests.ledger_fakes import CallLedger, FakeClock
from tests.routers.test_stickers import FAKE_CLAIMS_ADMIN, FAKE_CLAIMS_INSTITUCIONAL, _app, _FakeAuth

ROWS = [
    {"id": "ev-1", "direccion": "Calle 1", "latitud": "3.45", "longitud": "-76.53", "numero": "76001-1-0040001",
     "personaAfectada": "Juan", "origen": "firebase", "color": "rojo", "colorEtiqueta": "No habitable"},
    {"id": "ev-2", "direccion": "Calle 2", "latitud": "3.46", "longitud": "-76.52", "numero": "76001001-123-0001",
     "personaAfectada": "Ana", "origen": "sistema", "color": "verde", "colorEtiqueta": "Habitable"},
]
STICKER_TTL = stickers.EVALUACIONES_CACHE_TTL_SECONDS
ROSTER_TTL = router_mod.ROSTER_CACHE_TTL_SECONDS
SURVEY_TTL = router_mod.SURVEY_NAMES_CACHE_TTL_SECONDS
EVALS_TTL = router_mod.EVALUACIONES_FS_CACHE_TTL_SECONDS
REF_TTL = router_mod.REFERENCIA_CACHE_TTL_SECONDS
OPT_IN = {"depuracion": "1"}


def _referencia(huella: str = "h1") -> ReferenciaBundle:
    return ReferenciaBundle(
        vercel=(EntradaReferencia(cedula_key="123", nombre_norm="ana gomez", np="P3",
                                  entidad="DAGRD", codigo="", pasos=(), no_persona=False),),
        fase2=(), main=(), generado_en="2026-09-12", activa=True, motivo="", codigos_duplicados=(),
        huella=huella,
    )


class Rig:
    """The real app with every upstream replaced by a counting fake and every
    cache on one shared fake clock."""

    def __init__(self, monkeypatch, *, admin: bool = True):
        monkeypatch.setenv(router_mod.SEGUIMIENTO_DEPURACION_ENV, "1")
        monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
        self.clock = FakeClock()
        self.ledger = CallLedger()
        self.rows = [dict(r) for r in ROWS]
        self.referencia = _referencia()
        self.hoy = date(2026, 9, 19)
        self.fail: set[str] = set()

        monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

        async def fake_fetch(client, user, password, **kw):
            self.ledger.hit("walk")
            if "walk" in self.fail:
                raise atencionsismo.ApiUnavailableError("down", status=503)
            return [dict(r) for r in self.rows]

        monkeypatch.setattr(atencionsismo, "fetch_stickers", fake_fetch)

        real_profiles, real_evals = stickers.inspector_profiles, stickers.list_evaluaciones

        def profiles(db):
            self.ledger.hit("roster")
            if "roster" in self.fail:
                raise RuntimeError("roster 429")
            return real_profiles(db)

        def evals(db):
            self.ledger.hit("evals")
            if "evals" in self.fail:
                raise RuntimeError("evals 429")
            return real_evals(db)

        real_survey = router_mod._nombres_survey

        def survey(db):
            self.ledger.hit("survey")
            if "survey" in self.fail:
                raise RuntimeError("survey 429")
            return real_survey(db)

        real_depurar = router_mod.depuracion_svc.depurar

        def depurar(**kw):
            self.ledger.hit("depurar")
            return real_depurar(**kw)

        monkeypatch.setattr(stickers, "inspector_profiles", profiles)
        monkeypatch.setattr(stickers, "list_evaluaciones", evals)
        monkeypatch.setattr(router_mod, "_nombres_survey", survey)
        monkeypatch.setattr(router_mod.depuracion_svc, "depurar", depurar)
        monkeypatch.setattr(router_mod, "_hoy", lambda: self.hoy)

        self.stores = {
            "inspectores": {"u1": {"codigo": "004", "NP": "P4", "nombre_completo": "Ana Gomez",
                                   "identificacion": "123", "entidad": "Curaduria 1"}},
            "evaluaciones": {},
            "survey_cali": {"s1": {"nombre_evaluador": "Ana Gomez"}, "s2": {"nombre_evaluador": "Pedro Ruiz"}},
        }
        self.auth = _FakeAuth()
        self.app = _app(monkeypatch, self.auth, self.stores)
        self.app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN if admin else FAKE_CLAIMS_INSTITUCIONAL

        def load_referencia():
            self.ledger.hit("referencia")
            return self.referencia

        state = self.app.state
        state.depuracion_cache = router_mod.DepuracionCache(cargar_referencia=load_referencia, clock=self.clock)
        for cache in (state.stickers_atencionsismo_cache, state.stickers_evaluaciones_cache):
            cache._clock = self.clock
        for cache in (state.roster_cache, state.survey_names_cache, state.evaluaciones_fs_cache):
            cache._clock = self.clock
        self.client = TestClient(self.app)

    def get(self, params: dict | None = OPT_IN):
        """Opt-in by default (design D25): `?depuracion=1`. Pass `params=None`
        for the plain Stickers-tab request."""
        resp = self.client.get("/stickers-atencionsismo", params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def counts(self) -> dict[str, int]:
        return {k: self.ledger[k] for k in ("walk", "roster", "survey", "evals", "depurar", "referencia")}


@pytest.fixture
def rig(monkeypatch):
    return Rig(monkeypatch)


def test_fifty_requests_within_ttl_one_scan_each_and_one_depurar(rig):
    for _ in range(50):
        body = rig.get()
    assert body["depuracion"]["activa"] is True
    assert rig.counts() == {"walk": 1, "roster": 1, "survey": 1, "evals": 1, "depurar": 1, "referencia": 1}


def test_roster_scanned_once_per_compute_window(rig):
    rig.get()  # build_payload AND the depuracion compute share one roster snapshot
    assert rig.ledger["roster"] == 1


def test_identical_upstream_after_sticker_ttl_costs_zero_depurar_and_zero_scans(rig):
    rig.get()
    version = rig.app.state.stickers_atencionsismo_cache.snapshot_version
    rig.clock.advance(STICKER_TTL + 1)
    rig.get()
    assert rig.app.state.stickers_atencionsismo_cache.snapshot_version == version
    assert rig.counts() == {"walk": 2, "roster": 1, "survey": 1, "evals": 1, "depurar": 1, "referencia": 1}


def test_one_changed_sticker_costs_exactly_one_depurar_and_no_scans(rig):
    """Updated deliberately with judgment-day W5: this used to change
    `direccion`, which `depurar()` never reads and therefore no longer costs a
    recompute (see the projected-fingerprint tests below). A CONSUMED field
    (`origen`) still costs exactly one."""
    rig.get()
    rig.rows[1]["origen"] = "firebase"
    rig.clock.advance(STICKER_TTL + 1)
    rig.get()
    rig.get()
    assert rig.counts() == {"walk": 2, "roster": 1, "survey": 1, "evals": 1, "depurar": 2, "referencia": 1}


# ── Projected fingerprint of the depurar() inputs (judgment-day W5) ─────────
#
# `snapshot_version` hashes the WHOLE served payload (it will drive the ETag in
# 09b) so a new photo bumps it; `depurar()` only reads origen/fecha and the
# inspector identity. The DepuracionCache is keyed by the projected fingerprint.


def _snapshot_version(rig) -> int:
    return rig.app.state.stickers_atencionsismo_cache.snapshot_version


def test_changing_fields_depurar_never_reads_costs_zero_recomputes_but_still_bumps_the_snapshot(rig):
    rig.get()
    version = _snapshot_version(rig)
    row = rig.rows[0]
    row["fotografias"] = [{"id": "f1", "url": "https://blob.example/f1.jpg"}]
    row["direccion"] = "Calle 99"
    row["latitud"], row["longitud"] = "3.50", "-76.50"
    row["personaAfectada"] = "Otra Persona"
    row["barrio"], row["comuna"] = "San Antonio", "3"
    row["colorEtiqueta"] = "Restringido"
    row["fase"] = 2
    rig.clock.advance(STICKER_TTL + 1)
    rig.get()
    rig.get()
    assert _snapshot_version(rig) != version  # the served payload really changed (ETag material)
    assert rig.counts() == {"walk": 2, "roster": 1, "survey": 1, "evals": 1, "depurar": 1, "referencia": 1}


def test_changing_a_consumed_field_on_one_row_costs_exactly_one_recompute(rig):
    rig.get()
    rig.rows[0]["origen"] = "sistema"
    rig.rows[0]["fechaCreacion"] = "15/09/2026 08:30:00"
    rig.clock.advance(STICKER_TTL + 1)
    rig.get()
    rig.get()
    assert rig.ledger["depurar"] == 2


def test_reordering_the_upstream_rows_costs_zero_recomputes(rig):
    rig.get()
    version = _snapshot_version(rig)
    rig.rows.reverse()  # the API walk returns the same stickers in another order
    rig.clock.advance(STICKER_TTL + 1)
    rig.get()
    rig.get()
    assert _snapshot_version(rig) != version  # the payload order changed, so the whole-payload hash did
    assert rig.ledger["depurar"] == 1


def test_adding_and_removing_a_sticker_each_cost_exactly_one_recompute(rig):
    rig.get()
    rig.rows.append({"id": "ev-3", "numero": "76001-1-0040009", "origen": "sistema", "personaAfectada": "X"})
    rig.clock.advance(STICKER_TTL + 1)
    rig.get()
    assert rig.ledger["depurar"] == 2
    rig.rows.pop()
    rig.clock.advance(STICKER_TTL + 1)
    rig.get()
    assert rig.ledger["depurar"] == 3  # back to the first content: a real change again


def test_a_failing_depurar_after_a_rollover_serves_calculo_fallido_never_yesterdays_table_then_recovers(rig, monkeypatch):
    """judgment-day W6 through the real route: yesterday's cached result must not
    be served as today's when today's compute fails."""
    yesterday = rig.get()["depuracion"]
    assert yesterday["activa"] is True and yesterday["inspectores"]
    working = router_mod.depuracion_svc.depurar

    def boom(**kw):
        raise RuntimeError("engine boom")

    monkeypatch.setattr(router_mod.depuracion_svc, "depurar", boom)
    rig.hoy = date(2026, 9, 20)
    body = rig.get()
    assert body["depuracion"] == {
        "activa": False, "motivo": "calculo_fallido", "referencia_generada_en": "", "inspectores": [],
        "grupo_externos": None, "alias_nombres": {}, "revision_manual": [],
    }
    assert len(body["evaluaciones"]) == 2  # the stickers themselves are unaffected

    monkeypatch.setattr(router_mod.depuracion_svc, "depurar", working)
    recovered = rig.get()["depuracion"]
    assert recovered["activa"] is True and recovered["inspectores"]


def test_empty_sticker_list_fingerprints_and_computes_once(monkeypatch):
    rig = Rig(monkeypatch)
    rig.rows.clear()
    body = rig.get()
    rig.get()
    assert body["evaluaciones"] == [] and rig.ledger["depurar"] == 1


def test_inputs_fingerprint_is_order_independent_stable_and_field_selective():
    fp = router_mod.depuracion_inputs_version
    a = {"origen": "sistema", "fecha_creacion": "2026-09-01", "inspector": {"identificacion": "1", "codigo": "004", "nombre_completo": "Ana"}}
    b = {"origen": "firebase", "fecha_creacion": None, "inspector": {"identificacion": "2", "codigo": "", "nombre_completo": "Beto"}}
    assert fp([a, b]) == fp([b, a]) == fp([dict(a), dict(b)])
    assert fp([a]) != fp([b]) and fp([a]) != fp([a, a])  # a duplicated sticker is a real change
    assert fp([]) == fp([]) and fp([]) != fp([a])
    for changed in (
        {**a, "origen": "x"}, {**a, "fecha_creacion": "2026-09-02"},
        {**a, "inspector": {**a["inspector"], "identificacion": "9"}},
        {**a, "inspector": {**a["inspector"], "codigo": "005"}},
        {**a, "inspector": {**a["inspector"], "nombre_completo": "Ana B"}},
    ):
        assert fp([changed]) != fp([a])
    assert fp([{**a, "inspector": {**a["inspector"], "nombre_completo": "Ana"}}]) == fp([a])


def test_inputs_fingerprint_tolerates_none_and_non_json_values():
    from datetime import datetime

    fp = router_mod.depuracion_inputs_version
    row = {"origen": None, "fecha_creacion": datetime(2026, 9, 1, 12, 0), "inspector": {"identificacion": None, "codigo": None, "nombre_completo": None}}
    assert fp([row]) == fp([dict(row)])  # a raw datetime hashes deterministically instead of raising


def test_hoy_rollover_costs_exactly_one_depurar_and_no_scans(rig):
    rig.get()
    rig.hoy = date(2026, 9, 20)
    rig.get()
    rig.get()
    assert rig.counts() == {"walk": 1, "roster": 1, "survey": 1, "evals": 1, "depurar": 2, "referencia": 1}


def test_same_day_republish_with_new_huella_costs_exactly_one_depurar(rig):
    rig.get()
    rig.clock.advance(REF_TTL + 1)  # every TTL except the survey one expires: still identical content
    rig.get()
    assert rig.ledger["depurar"] == 1 and rig.ledger["referencia"] == 2  # identical re-download: +0

    rig.clock.advance(REF_TTL + 1)
    rig.referencia = _referencia(huella="h2")  # same generado_en, different content
    rig.get()
    rig.get()
    assert rig.ledger["depurar"] == 2


def test_component_ttls_static_inputs_one_simulated_hour(rig):
    for i in range(60):  # a continuously open tab, one request per minute
        rig.clock.t = 1000.0 + i * 60
        rig.get()
    counts = rig.counts()
    assert counts["roster"] <= 2 and counts["survey"] <= 1 and counts["evals"] <= 4
    assert counts["depurar"] == 1  # 0 recomputes/hour with static inputs
    assert counts["referencia"] <= 2
    assert counts["walk"] <= 13  # one walk per 5-minute TTL


def test_20_threads_at_expiry_one_compute_at_most_one_scan_per_component(rig):
    rig.get()
    rig.clock.advance(max(SURVEY_TTL, ROSTER_TTL, REF_TTL) + 1)  # every component expires at once
    before = rig.counts()
    barrier = threading.Barrier(20)
    statuses: list[int] = []

    def worker():
        barrier.wait(timeout=10)
        statuses.append(rig.client.get("/stickers-atencionsismo", params=OPT_IN).status_code)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert statuses == [200] * 20
    after = rig.counts()
    assert after["walk"] - before["walk"] == 1
    assert after["roster"] - before["roster"] <= 1
    assert after["survey"] - before["survey"] <= 1
    assert after["evals"] - before["evals"] <= 1
    assert after["depurar"] - before["depurar"] == 0  # identical content: nothing to recompute
    assert after["referencia"] - before["referencia"] == 1


# ── Failure modes ───────────────────────────────────────────────────────────


def test_roster_upstream_error_serves_stale_no_recompute_at_most_one_retry_per_ttl(rig):
    first = rig.get()
    rig.fail.add("roster")
    rig.clock.advance(ROSTER_TTL + 1)
    for _ in range(10):
        body = rig.get()
    assert body["depuracion"] == first["depuracion"]  # last-good roster
    assert rig.ledger["depurar"] == 1
    assert rig.ledger["roster"] == 2  # the single failed retry


def test_survey_upstream_error_serves_stale_no_recompute_at_most_one_retry_per_ttl(rig):
    first = rig.get()
    rig.fail.add("survey")
    rig.clock.advance(SURVEY_TTL + 1)
    for _ in range(10):
        body = rig.get()
    assert body["depuracion"] == first["depuracion"]
    assert rig.ledger["depurar"] == 1
    assert rig.ledger["survey"] == 2


def test_cold_start_survey_error_omits_depuracion_but_keeps_the_stickers_200(rig):
    rig.fail.add("survey")
    body = rig.get()
    assert "depuracion" not in body and len(body["evaluaciones"]) == 2


def test_evaluaciones_firestore_error_after_ttl_serves_stale_side_no_recompute(rig):
    rig.get()
    rig.fail.add("evals")
    rig.clock.advance(EVALS_TTL + 1)
    body = rig.get()  # the sticker TTL also expired: the walk runs, evals scan fails -> last-good evals
    assert "depuracion" in body and rig.ledger["depurar"] == 1


def test_empty_roster_still_computes_from_the_referencia(monkeypatch):
    rig = Rig(monkeypatch)
    rig.stores["inspectores"].clear()
    body = rig.get()
    assert body["depuracion"]["activa"] is True and rig.ledger["depurar"] == 1


def test_evaluaciones_component_never_caches_a_degraded_payload(rig):
    """A Blob-restored (degraded) evaluaciones payload is refused INSIDE the
    component fetch, so the 15-minute cache can never pin it."""
    state = rig.app.state
    state.stickers_evaluaciones_cache._payload = [{"codigo_edificacion": "old"}]
    state.stickers_evaluaciones_cache._at = rig.clock()
    state.stickers_evaluaciones_cache._degraded = True
    resp = rig.client.get("/stickers-atencionsismo", params=OPT_IN)
    assert resp.status_code == 503
    assert state.evaluaciones_fs_cache.current is None


def test_flag_off_never_touches_any_depuracion_component(monkeypatch):
    rig = Rig(monkeypatch)
    monkeypatch.delenv(router_mod.SEGUIMIENTO_DEPURACION_ENV)
    body = rig.get()
    assert "depuracion" not in body
    assert rig.ledger["survey"] == 0 and rig.ledger["depurar"] == 0 and rig.ledger["referencia"] == 0


def test_viewer_never_pays_for_depuracion(monkeypatch):
    rig = Rig(monkeypatch, admin=False)
    body = rig.get()
    assert "depuracion" not in body
    assert rig.ledger["survey"] == 0 and rig.ledger["depurar"] == 0 and rig.ledger["referencia"] == 0


# ── Admin writes invalidate the roster component (10.24) ────────────────────


def test_admin_create_invalidates_the_roster_component_and_changed_content_costs_one_depurar(rig):
    rig.get()
    resp = rig.client.post(
        "/stickers", json={"action": "create", "cedula": "1020735324", "password": "Cali2026+", "nombre_completo": "Zoe Nueva"}
    )
    assert resp.status_code == 201
    rig.get()
    rig.get()
    assert rig.ledger["roster"] == 2  # exactly +1 scan on the very next request
    assert rig.ledger["depurar"] == 2  # the roster CONTENT changed: exactly +1 recompute


def test_admin_set_enabled_invalidates_the_roster_component_identical_content_costs_no_recompute(rig):
    rig.get()
    resp = rig.client.post("/stickers", json={"action": "setEnabled", "uid": "u1", "enabled": False})
    assert resp.status_code == 200
    rig.get()
    rig.get()
    assert rig.ledger["roster"] == 2
    assert rig.ledger["depurar"] == 1  # the profile projection has no `activo`: same version


def test_failed_admin_write_does_not_invalidate_the_roster(rig):
    rig.get()
    resp = rig.client.post("/stickers", json={"action": "create", "cedula": "abc", "password": "Cali2026+"})
    assert resp.status_code == 400
    rig.get()
    assert rig.ledger["roster"] == 1


CLAIMS_PASSWORD_ADMIN = {
    "sub": "uid-admin", "email": "admin@example.com", "role": "admin",
    "firebase": {"sign_in_provider": "password"},
}


def _usuarios_rig(rig, monkeypatch):
    """Point the `/usuarios` router at the rig's fake Auth and give the rig's
    roster inspector (`u1`, cédula 123) a matching Auth account."""
    from app.routers import usuarios
    from tests.routers.test_stickers import _FakeUserRecord

    monkeypatch.setattr(usuarios, "fb_auth", rig.auth)
    rig.auth._users["u1"] = _FakeUserRecord("u1", "123@sismocali.gov.co")
    rig.app.dependency_overrides[current_claims] = lambda: CLAIMS_PASSWORD_ADMIN


def _roster_cedulas(rig) -> set[str]:
    return set(rig.app.state.roster_cache.current.value[1])


def test_admin_delete_usuario_invalidates_the_roster_component_and_the_profile_leaves_the_roster(rig, monkeypatch):
    """judgment-day W1: `POST /usuarios {"action":"delete"}` removes
    `inspectores/{uid}` but never told the roster cache, so the deleted
    inspector kept feeding `depurar()` for a whole 30-minute TTL."""
    _usuarios_rig(rig, monkeypatch)
    before = rig.get()
    assert _roster_cedulas(rig) == {"123"}
    assert rig.ledger["roster"] == 1 and rig.ledger["depurar"] == 1

    resp = rig.client.post("/usuarios", json={"action": "delete", "uid": "u1"})
    assert resp.status_code == 200
    assert "u1" not in rig.stores["inspectores"]

    after = rig.get()
    rig.get()
    assert rig.ledger["roster"] == 2  # exactly +1 scan on the very next request
    assert _roster_cedulas(rig) == set()  # the deleted profile is gone from the served roster
    assert rig.ledger["depurar"] == 2  # roster content changed: exactly +1 recompute
    assert after["depuracion"] != before["depuracion"]


def test_admin_delete_usuario_also_invalidates_the_stickers_tab_roster_list(rig, monkeypatch):
    """Same class of bug on the OTHER roster cache (`InspectoresCache`, the
    Stickers tab's `action:"list"` picker): a deleted inspector must not
    linger there for 5 minutes either."""
    _usuarios_rig(rig, monkeypatch)
    first = rig.client.post("/stickers", json={"action": "list"}).json()
    assert [i["uid"] for i in first["inspectores"]] == ["u1"]

    assert rig.client.post("/usuarios", json={"action": "delete", "uid": "u1"}).status_code == 200
    second = rig.client.post("/stickers", json={"action": "list"}).json()
    assert second["inspectores"] == []


def test_rejected_delete_usuario_does_not_invalidate_the_roster(rig, monkeypatch):
    _usuarios_rig(rig, monkeypatch)
    rig.get()
    resp = rig.client.post("/usuarios", json={"action": "delete", "uid": "uid-admin"})  # self-delete guard
    assert resp.status_code == 403
    resp = rig.client.post("/usuarios", json={"action": "delete", "uid": "nobody"})
    assert resp.status_code == 400
    rig.get()
    assert rig.ledger["roster"] == 1 and "u1" in rig.stores["inspectores"]


def test_delete_usuario_of_an_account_without_inspector_profile_is_harmless(rig, monkeypatch):
    """Deleting a plain admin/viewer account also runs the `inspectores/{uid}`
    cleanup (a no-op) and invalidates: one extra scan, identical content, zero
    recomputes."""
    from tests.routers.test_stickers import _FakeUserRecord

    _usuarios_rig(rig, monkeypatch)
    rig.auth._users["u9"] = _FakeUserRecord("u9", "someone@example.com")
    rig.get()
    assert rig.client.post("/usuarios", json={"action": "delete", "uid": "u9"}).status_code == 200
    rig.get()
    assert rig.ledger["roster"] == 2 and rig.ledger["depurar"] == 1


def test_delete_usuario_when_the_profile_delete_fails_still_answers_200_and_never_500s(rig, monkeypatch):
    """The profile delete is fail-soft in the route (orphan logged); the cache
    invalidation must not change that contract."""
    _usuarios_rig(rig, monkeypatch)
    rig.get()

    class _Boom:
        def collection(self, name):
            raise RuntimeError("firestore down")

    real_sismo = router_mod.credentials.sismo()
    monkeypatch.setattr(
        router_mod.credentials, "sismo",
        lambda: type("Clients", (), {"firestore": _Boom(), "app": real_sismo.app})(),
    )
    resp = rig.client.post("/usuarios", json={"action": "delete", "uid": "u1"})
    assert resp.status_code == 200
    assert "u1" not in rig.auth._users  # the Auth account is gone, as before
    assert rig.app.state.roster_cache.current is not None  # last-good still served after the invalidate


# ── Snapshot hash carries no volatile field (10.17) ─────────────────────────


def test_snapshot_hashed_payload_has_no_volatile_fields(rig):
    """Two builds of identical upstream content must hash identically: a
    per-fetch timestamp/uuid inside the served payload would bump the version
    on EVERY refetch and defeat the whole content-stable snapshot."""
    state = rig.app.state
    db = router_mod.credentials.sismo().firestore
    a = router_mod.build_payload(db, state.stickers_evaluaciones_cache)
    b = router_mod.build_payload(db, state.stickers_evaluaciones_cache)
    assert a is not b
    assert router_mod.stickers.blob_lkg.payload_hash(a) == router_mod.stickers.blob_lkg.payload_hash(b)


def test_flag_off_body_keeps_the_pre_extension_four_key_shape(monkeypatch):
    rig = Rig(monkeypatch)
    monkeypatch.delenv(router_mod.SEGUIMIENTO_DEPURACION_ENV)
    assert set(rig.get()) == {"ok", "fuente", "evaluaciones", "degraded"}
