"""Opt-in `?depuracion=1`, the degraded-snapshot gate, the flag-off golden bytes,
the PII / log review and the payload budget of `GET /stickers-atencionsismo`
(design D16, D25; tasks 10.1-10.10, 10.39-10.42, PR 09a).

Same rig as the component tests (`Rig`): the real app, every upstream replaced
by a counting fake, every cache on one injectable clock. No real sleeps, no
network. HTTP encoding (ETag/304/gzip) is slice 09b and is NOT tested here."""
from __future__ import annotations

import json
import logging
import threading
from datetime import date

import pytest
from starlette.responses import JSONResponse

from app.auth.deps import current_claims
from app.routers import stickers
from app.routers import stickers_atencionsismo as router_mod
from app.services import blob_lkg
from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle
from tests.routers.test_stickers import FAKE_CLAIMS_ADMIN, FAKE_CLAIMS_INSTITUCIONAL, FAKE_CLAIMS_VIEWER
from tests.routers.test_stickers_atencionsismo_components import (
    OPT_IN, STICKER_TTL, Rig, _referencia,
)

FLAG = router_mod.SEGUIMIENTO_DEPURACION_ENV
URL = "/stickers-atencionsismo"
ZERO_WORK = {"survey": 0, "depurar": 0, "referencia": 0}
DEGRADED_BLOCK = {
    "activa": False, "motivo": "stickers_degradados", "referencia_generada_en": "",
    "inspectores": [], "grupo_externos": None, "alias_nombres": {}, "revision_manual": [],
}

# Synthetic secrets: none of these may ever reach a log line, a viewer body or
# the public Blob copy.
NOMBRE = "Ana Maria Gomez Secreta"
CEDULA = "1020304050"
CORREO = "ana.secreta@example.com"
TELEFONO = "3001112233"
TARJETA = "TP-99887766"


@pytest.fixture
def rig(monkeypatch):
    return Rig(monkeypatch)


def _work(rig: Rig) -> dict[str, int]:
    return {k: rig.ledger[k] for k in ZERO_WORK}


def _assert_no_depuracion_work(rig: Rig) -> None:
    """0 `depurar`, 0 survey scan, 0 referencia GET AND none of the depuracion
    components was even touched (not merely a cheap fast-path hit)."""
    assert _work(rig) == ZERO_WORK
    state = rig.app.state
    assert state.survey_names_cache.current is None
    assert state.depuracion_cache._referencia is None
    assert state.depuracion_cache._result is None
    assert state.depuracion_cache._inflight == {}


def _secret_roster(rig: Rig) -> None:
    rig.stores["inspectores"]["u1"] = {
        "codigo": "004", "NP": "P4", "nombre_completo": NOMBRE, "identificacion": CEDULA,
        "entidad": "Curaduria 1", "correo_contacto": CORREO, "num_telefono": TELEFONO,
        "tarjeta_profesional": TARJETA,
    }
    rig.referencia = ReferenciaBundle(
        vercel=(EntradaReferencia(cedula_key=CEDULA, nombre_norm="ana maria gomez secreta", np="P3",
                                  entidad="DAGRD", codigo="", pasos=(), no_persona=False),),
        fase2=(), main=(), generado_en="2026-09-12", activa=True, motivo="", codigos_duplicados=(),
        huella="h-secret",
    )


def _lkg_row() -> dict:
    return {"id": "old", "fuente": "atencionsismo", "origen": "firebase", "color_etiqueta": "",
            "codigo_edificacion": "c", "consecutivo": 1, "municipio": "76001", "area": "1",
            "area_nombre": "", "clasificacion": "", "alcance": "", "coords": None,
            "restricciones": "", "acciones_posteriores": {"barricadas": False, "evaluacion_detallada": False},
            "fecha": None, "fecha_fuente": "no_aplica", "barrio_reportado": "", "comuna_reportada": "",
            "descripcion": {"nombre": "", "direccion": ""},
            "inspector": {"uid": "", "codigo": "", "nombre_completo": "", "identificacion": "",
                          "entidad": "", "np": "", "tarjeta_profesional": "", "num_telefono": "",
                          "correo_contacto": ""},
            "comentarios": "", "fotos": []}


def _force_degraded(rig: Rig, monkeypatch) -> None:
    """Cold start + upstream walk down + a Blob last-known-good: the stickers
    cache serves the redacted copy with `degraded=True`."""
    rig.fail.add("walk")
    monkeypatch.setattr(
        stickers.blob_lkg, "load_json",
        lambda pathname, expected_type: [_lkg_row()] if pathname == router_mod.STICKERS_LKG_BLOB else None,
    )


def _recover(rig: Rig) -> None:
    rig.fail.discard("walk")
    rig.clock.advance(STICKER_TTL + 1)


# ── 10.1-10.2 Degraded gate (D16) ───────────────────────────────────────────


def test_depuracion_gated_off_when_snapshot_degraded(rig, monkeypatch):
    _force_degraded(rig, monkeypatch)
    resp = rig.client.get(URL, params=OPT_IN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["depuracion"] == DEGRADED_BLOCK


def test_degraded_gate_costs_no_depuracion_work_and_never_builds_a_table(rig, monkeypatch):
    rig.stores["inspectores"]["u2"] = {"nombre_completo": "Zoe Sin Codigo", "identificacion": "999888777"}
    _force_degraded(rig, monkeypatch)
    for _ in range(5):
        body = rig.get()
    assert body["depuracion"]["motivo"] == "stickers_degradados" and body["depuracion"]["inspectores"] == []
    _assert_no_depuracion_work(rig)


def test_healthy_snapshot_with_the_same_roster_does_build_a_table(rig):
    """Triangulation for the gate above: the empty table there comes from the
    gate, not from an empty roster."""
    rig.stores["inspectores"]["u2"] = {"nombre_completo": "Zoe Sin Codigo", "identificacion": "999888777"}
    body = rig.get()
    assert body["degraded"] is False and body["depuracion"]["activa"] is True
    assert len(body["depuracion"]["inspectores"]) >= 2


def test_degraded_snapshot_without_the_param_has_no_depuracion_key(rig, monkeypatch):
    _force_degraded(rig, monkeypatch)
    body = rig.get(params=None)
    assert body["degraded"] is True and "depuracion" not in body
    _assert_no_depuracion_work(rig)


def test_degraded_snapshot_for_a_viewer_gets_no_block_at_all(monkeypatch):
    rig = Rig(monkeypatch, admin=False)
    _force_degraded(rig, monkeypatch)
    body = rig.get()
    assert body["degraded"] is True and "depuracion" not in body
    _assert_no_depuracion_work(rig)


def test_degraded_snapshot_with_the_flag_off_has_no_block(rig, monkeypatch):
    _force_degraded(rig, monkeypatch)
    monkeypatch.delenv(FLAG)
    assert "depuracion" not in rig.get()


def test_degraded_flipping_back_to_healthy_recomputes_exactly_once(rig, monkeypatch):
    _force_degraded(rig, monkeypatch)
    rig.get()
    rig.get()
    assert rig.ledger["depurar"] == 0

    _recover(rig)
    healthy = [rig.get() for _ in range(5)]
    assert all(b["degraded"] is False and b["depuracion"]["activa"] is True for b in healthy)
    assert rig.ledger["depurar"] == 1  # one compute for the new version, then cached
    assert rig.ledger["survey"] == 1 and rig.ledger["referencia"] == 1


def test_stickers_gate_and_referencia_gate_are_scoped_to_different_sources(rig, monkeypatch):
    """Both sources degraded: the stickers gate wins and the referencia is
    never even downloaded. Referencia degraded alone keeps its own path."""
    rig.referencia = ReferenciaBundle.vacia(motivo="sin_blob")
    _force_degraded(rig, monkeypatch)
    assert rig.get()["depuracion"] == DEGRADED_BLOCK
    assert rig.ledger["referencia"] == 0


# ── 10.3 Reference bundle degraded, stickers live ───────────────────────────


def test_depuracion_still_computed_when_only_referencia_missing(rig):
    rig.referencia = ReferenciaBundle.vacia(motivo="sin_blob")
    body = rig.get()
    dep = body["depuracion"]
    assert body["degraded"] is False
    assert dep["activa"] is False and dep["motivo"] == "sin_blob"
    assert len(dep["inspectores"]) == 1  # a computed table, not the stickers gate's empty one
    assert dep["inspectores"][0]["np_fuente"] in ("main", "ninguno")
    assert rig.ledger["depurar"] == 1


def test_referencia_recovering_after_a_missing_one_recomputes_exactly_once(rig):
    rig.referencia = ReferenciaBundle.vacia(motivo="sin_blob")
    rig.get()
    rig.clock.advance(router_mod.REFERENCIA_CACHE_TTL_SECONDS + 1)
    rig.referencia = _referencia()
    bodies = [rig.get() for _ in range(3)]
    assert bodies[-1]["depuracion"]["activa"] is True
    assert rig.ledger["depurar"] == 2


# ── 10.4 Roles ──────────────────────────────────────────────────────────────


def test_depuracion_present_with_contact_fields_for_admin(rig):
    _secret_roster(rig)
    dep = rig.get()["depuracion"]
    assert dep["activa"] is True
    persona = next(i for i in dep["inspectores"] if i["identificacion"] == CEDULA)
    assert persona["nombre_completo"] == NOMBRE
    assert persona["num_telefono"] == TELEFONO
    assert persona["correo_contacto"] == CORREO
    assert persona["tarjeta_profesional"] == TARJETA


def test_depuracion_absent_for_viewer_role(monkeypatch):
    rig = Rig(monkeypatch, admin=False)
    _secret_roster(rig)
    resp = rig.client.get(URL, params=OPT_IN)
    assert resp.status_code == 200
    assert "depuracion" not in resp.json()
    for marker in ("revision_manual", "alias_nombres", "grupo_externos", "no_persona"):
        assert marker not in resp.text  # the raw body, not only the parsed keys
    # Exactly what the same viewer gets without asking (the `evaluaciones` rows
    # themselves already carry the matched inspector's fields, as before this change).
    assert resp.content == rig.client.get(URL).content
    _assert_no_depuracion_work(rig)


def test_role_otro_and_anonymous_requests_are_rejected_before_any_depuracion_work(rig):
    rig.app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER  # resolves to "otro"
    assert rig.client.get(URL, params=OPT_IN).status_code == 403
    del rig.app.dependency_overrides[current_claims]  # no bearer token at all
    assert rig.client.get(URL, params=OPT_IN).status_code == 401
    _assert_no_depuracion_work(rig)
    assert rig.ledger["walk"] == 0


# ── 10.39-10.41 Opt-in `?depuracion=1` (D25) ────────────────────────────────


def test_depuracion_absent_and_zero_work_without_query_param(rig):
    for _ in range(20):
        body = rig.get(params=None)
    assert "depuracion" not in body and len(body["evaluaciones"]) == 2
    _assert_no_depuracion_work(rig)
    assert rig.ledger["roster"] == 1  # only the scan the sticker normalization itself needs


def test_non_opt_in_requests_do_not_poison_or_warm_the_depuracion_cache(rig):
    rig.get(params=None)
    rig.get(params=None)
    assert "depuracion" in rig.get()  # the first opt-in pays exactly one compute...
    assert rig.ledger["depurar"] == 1
    assert "depuracion" not in rig.get(params=None)  # ...and a later plain request adds nothing
    assert "depuracion" in rig.get()
    assert _work(rig) == {"survey": 1, "depurar": 1, "referencia": 1}


@pytest.mark.parametrize("query", [
    "depuracion=0", "depuracion=true", "depuracion=yes", "depuracion=", "depuracion",
    "depuracion=1%20", "depuracion=1+", "depuracion=01", "depuracion=1&depuracion=0",
    "depuracion=0&depuracion=1", "depuracion=1&depuracion=1", "Depuracion=1", "depuracion=%201",
    "depuracion=1.0", "depuracion=-1", "depuraciones=1", "other=1",
])
def test_only_the_exact_value_1_opts_in(rig, query):
    resp = rig.client.get(f"{URL}?{query}")
    assert resp.status_code == 200  # never a 400/422: an unknown value just means "not requested"
    assert "depuracion" not in resp.json()
    _assert_no_depuracion_work(rig)


@pytest.mark.parametrize("query", ["depuracion=1", "depuracion=%31", "other=x&depuracion=1&z=2"])
def test_exact_value_1_opts_in_however_it_is_encoded(rig, query):
    resp = rig.client.get(f"{URL}?{query}")
    assert resp.status_code == 200 and resp.json()["depuracion"]["activa"] is True
    assert rig.ledger["depurar"] == 1


def test_flag_off_wins_over_the_param_and_costs_zero(rig, monkeypatch):
    monkeypatch.delenv(FLAG)
    assert "depuracion" not in rig.get()
    monkeypatch.setenv(FLAG, "0")
    assert "depuracion" not in rig.get()
    _assert_no_depuracion_work(rig)


def test_viewer_with_the_param_costs_zero_depuracion_work(monkeypatch):
    rig = Rig(monkeypatch, admin=False)
    for _ in range(5):
        assert "depuracion" not in rig.get()
    _assert_no_depuracion_work(rig)


def test_concurrent_opt_in_and_plain_requests_share_one_walk_and_one_compute(rig):
    barrier = threading.Barrier(20)
    results: list[tuple[bool, int, dict]] = []
    lock = threading.Lock()

    def worker(opt_in: bool):
        barrier.wait(timeout=10)
        resp = rig.client.get(URL, params=OPT_IN if opt_in else None)
        with lock:
            results.append((opt_in, resp.status_code, resp.json()))

    threads = [threading.Thread(target=worker, args=(i % 2 == 0,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 20 and all(status == 200 for _, status, _ in results)
    opted = [b for o, _, b in results if o]
    plain = [b for o, _, b in results if not o]
    assert len(opted) == 10 and len(plain) == 10
    assert all("depuracion" not in b for b in plain)
    assert all(b["depuracion"] == opted[0]["depuracion"] for b in opted)
    assert rig.ledger["walk"] == 1
    assert _work(rig) == {"survey": 1, "depurar": 1, "referencia": 1}


# ── 10.10 / 10.42 Flag-off and non-opt-in bodies are byte-identical ─────────

FIXED_PAYLOAD = [{
    "id": "ev-1", "direccion": "Calle 1 ñ", "clasificacion": "INSEGURO", "fotos": [],
    "inspector": {"identificacion": "123", "nombre_completo": "Ana Gómez"},
}]
# Serialization of `{"ok", "fuente", "evaluaciones", "degraded"}` exactly as the
# pre-extension `JSONResponse(body)` produced it (compact separators, UTF-8,
# key order preserved, no `depuracion`).
GOLDEN_BODY = (
    '{"ok":true,"fuente":"atencionsismo","evaluaciones":[{"id":"ev-1","direccion":"Calle 1 ñ",'
    '"clasificacion":"INSEGURO","fotos":[],"inspector":{"identificacion":"123","nombre_completo":"Ana Gómez"}}],'
    '"degraded":false}'
).encode("utf-8")


def _rig_with_fixed_payload(monkeypatch, *, admin: bool = True) -> Rig:
    rig = Rig(monkeypatch, admin=admin)
    monkeypatch.setattr(router_mod, "build_payload", lambda *a, **k: [dict(r) for r in FIXED_PAYLOAD])
    return rig


def test_golden_matches_the_pre_extension_jsonresponse_serialization():
    old = JSONResponse({"ok": True, "fuente": "atencionsismo", "evaluaciones": FIXED_PAYLOAD, "degraded": False})
    assert old.body == GOLDEN_BODY  # the golden itself is the old serialization, not a guess


@pytest.mark.parametrize("flag, role, query", [
    ("1", "admin", ""),                        # flag on, admin, NO param: the case this PR changes
    ("1", "admin", "?depuracion=0"),
    ("1", "admin", "?depuracion=true"),
    ("1", "admin", "?depuracion=1&depuracion=0"),
    ("1", "viewer", "?depuracion=1"),          # a viewer costs 0 and sees the flag-off shape
    ("0", "admin", "?depuracion=1"),           # flag off wins over the param
    ("", "admin", "?depuracion=1"),
    (None, "admin", ""),                       # flag unset: the historical case
])
def test_non_opt_in_body_byte_identical_to_flag_off_shape(monkeypatch, flag, role, query):
    rig = _rig_with_fixed_payload(monkeypatch, admin=role == "admin")
    if flag is None:
        monkeypatch.delenv(FLAG)
    else:
        monkeypatch.setenv(FLAG, flag)
    resp = rig.client.get(URL + query)
    assert resp.status_code == 200
    assert resp.content == GOLDEN_BODY
    assert resp.headers["content-type"] == "application/json"


def test_opt_in_body_is_the_golden_plus_only_the_depuracion_key(monkeypatch):
    rig = _rig_with_fixed_payload(monkeypatch)
    body = json.loads(rig.client.get(URL, params=OPT_IN).content)
    assert list(body) == ["ok", "fuente", "evaluaciones", "degraded", "depuracion"]
    assert json.dumps({k: v for k, v in body.items() if k != "depuracion"},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8") == GOLDEN_BODY


def test_flag_off_response_still_byte_identical_with_full_universe(monkeypatch):
    """`SEGUIMIENTO_DEPURACION` unset with a full universe behind it (roster
    with and without codigo, a live referencia): the 4-key body, in order."""
    rig = Rig(monkeypatch)
    monkeypatch.delenv(FLAG)
    _secret_roster(rig)
    rig.stores["inspectores"]["u2"] = {"nombre_completo": "Zoe Sin Codigo", "identificacion": "999888777"}
    resp = rig.client.get(URL, params=OPT_IN)
    body = resp.json()
    assert list(body) == ["ok", "fuente", "evaluaciones", "degraded"]
    assert resp.content == json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _assert_no_depuracion_work(rig)


# ── 10.5 PII never reaches the Blob-persisted copy ──────────────────────────


def test_depuracion_pii_never_reaches_blob_persisted_evaluaciones_full_universe(rig, monkeypatch):
    _secret_roster(rig)
    rig.stores["inspectores"]["u2"] = {
        "nombre_completo": "Pedro Otro Secreto", "identificacion": "5544332211", "entidad": "DAGRD",
        "correo_contacto": "pedro.secreto@example.com", "num_telefono": "3109998877",
        "tarjeta_profesional": "TP-11223344",
    }
    rig.rows[1]["profesional"] = {"cedula": CEDULA, "nombre": NOMBRE, "rango": "P2"}
    saves: list[tuple[str, object]] = []
    monkeypatch.setattr(blob_lkg, "save_json", lambda pathname, payload: saves.append((pathname, payload)) or True)

    body = rig.get()

    # Not vacuous: the served response carries the secrets in memory...
    assert any(i["num_telefono"] == TELEFONO for i in body["depuracion"]["inspectores"])
    assert any(e["inspector"]["identificacion"] == CEDULA for e in body["evaluaciones"])
    for cache in (rig.app.state.stickers_atencionsismo_cache, rig.app.state.stickers_evaluaciones_cache):
        if cache._persist_thread is not None:
            cache._persist_thread.join()

    assert any(pathname == router_mod.STICKERS_LKG_BLOB for pathname, _ in saves)
    serialized = json.dumps([payload for _, payload in saves], ensure_ascii=False, default=str)
    for secret in (NOMBRE, CEDULA, CORREO, TELEFONO, TARJETA, "Pedro Otro Secreto", "5544332211",
                   "pedro.secreto@example.com", "3109998877", "TP-11223344",
                   "depuracion", "revision_manual", "alias_nombres", "grupo_externos"):
        assert secret not in serialized, secret


def test_redact_for_blob_drops_contact_fields_and_any_depuracion_block_from_a_row():
    row = _lkg_row()
    row["inspector"].update(nombre_completo=NOMBRE, identificacion=CEDULA, np="P4", correo_contacto=CORREO,
                            num_telefono=TELEFONO, tarjeta_profesional=TARJETA)
    row["depuracion"] = {"inspectores": [{"nombre_completo": NOMBRE}]}
    row["revision_manual"] = [{"cedula_key": CEDULA}]
    out = router_mod.redact_for_blob([row])
    serialized = json.dumps(out, ensure_ascii=False)
    for secret in (NOMBRE, CEDULA, CORREO, TELEFONO, TARJETA, "depuracion", "revision_manual"):
        assert secret not in serialized, secret
    assert out[0]["inspector"]["correo_contacto"] == ""  # the KEY stays, blank


def test_depuracion_never_mutates_the_served_evaluaciones_payload(rig):
    plain = rig.get(params=None)["evaluaciones"]
    opted = rig.get()
    assert opted["evaluaciones"] == plain
    assert all("depuracion" not in e and "revision_manual" not in e for e in opted["evaluaciones"])


# ── Logs: no PII in any line (spec: "never logged in clear") ────────────────


@pytest.mark.parametrize("error", [
    KeyError(CEDULA),                                                  # a dict miss echoes the key
    ValueError(f"{NOMBRE} {CEDULA} {CORREO} {TELEFONO}"),
])
def test_depuracion_failure_log_never_carries_the_exception_message(rig, monkeypatch, caplog, error):
    _secret_roster(rig)

    def boom(**kw):
        raise error

    monkeypatch.setattr(router_mod.depuracion_svc, "depurar", boom)
    with caplog.at_level(logging.DEBUG):
        resp = rig.client.get(URL, params=OPT_IN)
    # Advisory-only, still 200. Updated with judgment-day W6: a failed compute is now
    # a declared `calculo_fallido` block (same for the leader and any waiter), no
    # longer an omitted key; the roster/survey/referencia failures keep omitting it.
    depuracion = resp.json()["depuracion"]
    assert resp.status_code == 200
    assert (depuracion["activa"], depuracion["motivo"], depuracion["inspectores"]) == (False, "calculo_fallido", [])
    # The failure IS logged (by exception type and location) ...
    assert any("compute failed" in r.getMessage() and type(error).__name__ in r.getMessage()
               for r in caplog.records)
    # ... and no record - message, args or traceback text - carries a secret.
    for record in caplog.records:
        text = f"{record.getMessage()} {record.exc_text or ''}"
        for secret in (NOMBRE, CEDULA, CORREO, TELEFONO):
            assert secret not in text, secret


def test_alias_nombres_info_log_is_redacted(rig, caplog):
    """A name collision between two profiles (the case that logs) driven through
    the real route at INFO: neither a name nor a cédula appears in a record."""
    rig.stores["inspectores"]["u1"] = {"nombre_completo": NOMBRE, "identificacion": CEDULA, "codigo": "004"}
    rig.stores["inspectores"]["u2"] = {"nombre_completo": NOMBRE, "identificacion": "7766554433"}
    rig.stores["survey_cali"]["s3"] = {"nombre_evaluador": NOMBRE}
    with caplog.at_level(logging.INFO):
        assert rig.get()["depuracion"]["alias_nombres"]
    assert caplog.records
    for secret in (NOMBRE, CEDULA, "7766554433", "ana maria"):
        assert secret.lower() not in caplog.text.lower(), secret


def test_no_pii_in_any_log_line_of_a_full_opt_in_cycle_including_failures(rig, caplog):
    _secret_roster(rig)
    with caplog.at_level(logging.DEBUG):
        rig.get()
        rig.fail.add("survey")
        rig.clock.advance(router_mod.SURVEY_NAMES_CACHE_TTL_SECONDS + 1)
        rig.get()  # survey refresh fails -> last-good served, logged by type only
        rig.referencia = ReferenciaBundle.vacia(motivo="blob caido")
        rig.clock.advance(router_mod.REFERENCIA_CACHE_TTL_SECONDS + 1)
        rig.get()
    text = caplog.text
    for secret in (NOMBRE, CEDULA, CORREO, TELEFONO, TARJETA, "ana maria"):
        assert secret.lower() not in text.lower(), secret


# ── 10.8 Payload budget ─────────────────────────────────────────────────────

DEPURACION_BUDGET_BYTES = 400 * 1024  # design "Efficiency Acceptance Budgets": 400 profiles


def _universe_of_400(rig: Rig) -> None:
    rig.stores["inspectores"].clear()
    entradas = []
    for i in range(400):
        cedula = f"{1_000_000_000 + i * 7919}"
        rig.stores["inspectores"][f"u{i:03d}"] = {
            "codigo": f"{i + 1:03d}", "NP": "P3", "nombre_completo": f"Inspector Numero{i:03d} Apellido Segundo{i:03d}",
            "identificacion": cedula, "entidad": f"Curaduria Urbana {i % 5 + 1}",
            "correo_contacto": f"inspector.numero{i:03d}@ejemplo.gov.co", "num_telefono": f"31{i:08d}",
            "tarjeta_profesional": f"TP-{i:07d}",
        }
        entradas.append(EntradaReferencia(
            cedula_key=cedula, nombre_norm=f"inspector numero{i:03d} apellido segundo{i:03d}", np="P3",
            entidad="DAGRD", codigo="", pasos=(), no_persona=False,
        ))
    rig.referencia = ReferenciaBundle(
        vercel=tuple(entradas), fase2=(), main=(), generado_en="2026-09-19", activa=True, motivo="",
        codigos_duplicados=(), huella="h-400",
    )


def test_depuracion_payload_size_within_budget(rig):
    _universe_of_400(rig)
    dep = rig.get()["depuracion"]
    assert len(dep["inspectores"]) == 400  # the fixture really is the 400-profile universe
    size = len(json.dumps(dep, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    print(f"depuracion for 400 profiles: {size} bytes raw (budget {DEPURACION_BUDGET_BYTES})")
    assert size > 100_000, "fixture too small to say anything about the budget"
    assert size <= DEPURACION_BUDGET_BYTES, (
        f"depuracion for 400 profiles is {size} bytes raw, over the {DEPURACION_BUDGET_BYTES}-byte budget"
    )
