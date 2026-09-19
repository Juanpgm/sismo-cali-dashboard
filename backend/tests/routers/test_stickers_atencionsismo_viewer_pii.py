"""D-VIEWER-PII: `tarjeta_profesional` / `num_telefono` / `correo_contacto` in
`evaluaciones[].inspector` are admin-only on `GET /stickers-atencionsismo`.

`require_role("admin", "viewer")` lets ANY authenticated @cali.gov.co account
through as a viewer, and `normalize_sticker` fills those three fields from the
roster on every branch that names a person (Rule A cedula match, the `rango`
roster branch, the brigade-code roster branch). Only the admin-only Seguimiento
tab reads them, so a viewer's copy carries the same KEYS with `""` values (the
`redact_for_blob` convention: response shape stable, nothing to guard).

The redaction lives in the role-specific body builder, so each cached variant
(role x opt-in, see `EncodedBodyCache`) is BUILT redacted; nothing is redacted
after a shared lookup and the admin bytes are never reachable from a viewer.

Same rig as the opt-in/HTTP tests: the real app, counting fakes, one clock."""
from __future__ import annotations

import copy
import json
import logging
import time

import pytest
from starlette.responses import JSONResponse

from app.auth.deps import current_claims
from app.routers import stickers
from app.routers import stickers_atencionsismo as router_mod
from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle
from tests.routers.test_stickers import FAKE_CLAIMS_ADMIN, FAKE_CLAIMS_INSTITUCIONAL, FAKE_CLAIMS_VIEWER
from tests.routers.test_stickers_atencionsismo_components import OPT_IN, Rig
from tests.routers.test_stickers_atencionsismo_optin import (
    CEDULA, FLAG, URL, _force_degraded, _lkg_row,
)

CONTACT = ("tarjeta_profesional", "num_telefono", "correo_contacto")

# Synthetic secrets, one set per roster person: none may reach a viewer body.
TP_A, PHONE_A, MAIL_A = "TP-ROSTER-A-111", "3115550001", "roster.a@example.com"
TP_B, PHONE_B, MAIL_B = "TP-ROSTER-B-222", "3115550002", "roster.b@example.com"
TP_API = "TP-FROM-API-333"
ALL_SECRETS = (TP_A, PHONE_A, MAIL_A, TP_B, PHONE_B, MAIL_B, TP_API)

PATHS = ("rule_a", "rango_api", "brigade_code", "rule_b")


def _paths_rig(monkeypatch, *, admin: bool) -> Rig:
    """One row per producer branch of `normalize_sticker`."""
    rig = Rig(monkeypatch, admin=admin)
    rig.stores["inspectores"] = {
        "u1": {"codigo": "004", "NP": "P4", "nombre_completo": "Ana Gomez", "identificacion": "123",
               "entidad": "Curaduria 1", "tarjeta_profesional": TP_A, "num_telefono": PHONE_A,
               "correo_contacto": MAIL_A},
        "u2": {"NP": "P2", "nombre_completo": "Beto Secreto", "identificacion": CEDULA,
               "entidad": "DAGRD", "tarjeta_profesional": TP_B, "num_telefono": PHONE_B,
               "correo_contacto": MAIL_B},
    }
    rig.stores["evaluaciones"] = {
        "ev-d": {"codigo_edificacion": "76001-1-0040004", "inspector": {
            "uid": "u1", "codigo": "004", "nombre_completo": "Eval Persona", "identificacion": "777",
            "entidad": "DAGRD"}},
    }
    base = {"direccion": "Calle 1", "latitud": "3.45", "longitud": "-76.53", "personaAfectada": "Juan",
            "origen": "sistema", "color": "verde", "colorEtiqueta": "Habitable"}
    rig.rows = [
        {**base, "id": "rule_a", "numero": "76001-1-0090001",
         "profesional": {"cedula": CEDULA, "nombre": "Beto Secreto", "rango": "P2"}},
        {**base, "id": "rango_api", "numero": "76001-1-0040002", "profesional": {"rango": "P3"}},
        {**base, "id": "brigade_code", "numero": "76001-1-0040003"},
        {**base, "id": "rule_b", "numero": "76001-1-0040004"},
    ]
    return rig


def _rows(body: dict) -> dict[str, dict]:
    return {e["id"]: e for e in body["evaluaciones"]}


def _get(rig, params=None, *, ae="identity", inm=None):
    headers = {"Accept-Encoding": ae}
    if inm is not None:
        headers["If-None-Match"] = inm
    return rig.client.get(URL, params=params, headers=headers)


def _as(rig, claims) -> None:
    rig.app.dependency_overrides[current_claims] = lambda: claims


# ── The producers really carry the values today (the test is not vacuous) ───


def test_admin_receives_the_contact_values_on_every_producer_branch(monkeypatch):
    rig = _paths_rig(monkeypatch, admin=True)
    rows = _rows(_get(rig).json())
    assert {k: rows["rule_a"]["inspector"][k] for k in CONTACT} == {
        "tarjeta_profesional": TP_B, "num_telefono": PHONE_B, "correo_contacto": MAIL_B}
    for path in ("rango_api", "brigade_code"):
        assert {k: rows[path]["inspector"][k] for k in CONTACT} == {
            "tarjeta_profesional": TP_A, "num_telefono": PHONE_A, "correo_contacto": MAIL_A}, path
    # Rule B step 1 never carries them (a Firestore evaluacion has none): the
    # identity is there, the contact block is blank by construction.
    assert rows["rule_b"]["inspector"]["nombre_completo"] == "Eval Persona"
    assert all(rows["rule_b"]["inspector"][k] == "" for k in CONTACT)


def test_admin_receives_the_api_supplied_tarjeta_too(monkeypatch):
    rig = _paths_rig(monkeypatch, admin=True)
    rig.rows[0]["profesional"]["tarjetaProfesional"] = TP_API
    assert _rows(_get(rig).json())["rule_a"]["inspector"]["tarjeta_profesional"] == TP_API


# ── A viewer never receives a value, on any producer path ───────────────────


@pytest.mark.parametrize("flag", ["1", "0"])
@pytest.mark.parametrize("params", [OPT_IN, None])
@pytest.mark.parametrize("ae", ["identity", "gzip"])
def test_viewer_gets_blank_contact_fields_on_every_producer_branch(monkeypatch, flag, params, ae):
    rig = _paths_rig(monkeypatch, admin=False)
    monkeypatch.setenv(FLAG, flag)
    resp = _get(rig, params, ae=ae)
    assert resp.status_code == 200
    assert (resp.headers.get("content-encoding") == "gzip") is (ae == "gzip")
    for secret in ALL_SECRETS:
        assert secret not in resp.text, secret
    rows = _rows(resp.json())
    assert set(rows) == set(PATHS)
    for path, row in rows.items():
        # Shape stable: the keys stay, with an empty value.
        assert {k: row["inspector"][k] for k in CONTACT} == dict.fromkeys(CONTACT, ""), path


def test_viewer_keeps_every_other_field_admin_and_viewer_differ_only_in_the_three_values(monkeypatch):
    admin = _rows(_get(_paths_rig(monkeypatch, admin=True)).json())
    viewer = _rows(_get(_paths_rig(monkeypatch, admin=False)).json())
    assert set(admin) == set(viewer) == set(PATHS)
    for path in PATHS:
        stripped = copy.deepcopy(admin[path])
        for key in CONTACT:
            stripped["inspector"][key] = ""
        assert viewer[path] == stripped, path
        assert list(viewer[path]["inspector"]) == list(admin[path]["inspector"]), path  # same key order
    # And the identity a viewer's own tab renders is untouched.
    assert viewer["rule_a"]["inspector"]["nombre_completo"] == "Beto Secreto"
    assert viewer["rule_a"]["inspector"]["identificacion"] == CEDULA


def test_viewer_request_never_mutates_the_shared_payload_the_admin_is_served_from(monkeypatch):
    rig = _paths_rig(monkeypatch, admin=True)
    before = _get(rig).content
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
    viewer = _get(rig)
    assert PHONE_A not in viewer.text
    payload = rig.app.state.stickers_atencionsismo_cache.get_or_fetch_snapshot(lambda: pytest.fail("refetched")).payload
    assert any(e["inspector"]["num_telefono"] == PHONE_A for e in payload)  # the shared list still holds them
    _as(rig, FAKE_CLAIMS_ADMIN)
    assert _get(rig).content == before


# ── HTTP layer: ETags, 304, role changes ────────────────────────────────────


def test_admin_and_viewer_variants_have_different_etags_in_both_encodings(monkeypatch):
    rig = _paths_rig(monkeypatch, admin=True)
    admin = {ae: _get(rig, ae=ae).headers["etag"] for ae in ("identity", "gzip")}
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
    viewer = {ae: _get(rig, ae=ae).headers["etag"] for ae in ("identity", "gzip")}
    assert len({*admin.values(), *viewer.values()}) == 4


@pytest.mark.parametrize("ae", ["identity", "gzip"])
@pytest.mark.parametrize("params", [OPT_IN, None])
def test_a_viewer_sending_the_admins_etag_gets_200_with_the_redacted_body(monkeypatch, ae, params):
    rig = _paths_rig(monkeypatch, admin=True)
    admin = _get(rig, params, ae=ae)
    assert PHONE_A in admin.text
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
    resp = _get(rig, params, ae=ae, inm=admin.headers["etag"])
    assert resp.status_code == 200
    assert resp.headers["etag"] != admin.headers["etag"]
    for secret in ALL_SECRETS:
        assert secret not in resp.text, secret


@pytest.mark.parametrize("ae", ["identity", "gzip"])
def test_the_viewers_own_304_carries_no_body_and_validates_only_the_viewer_representation(monkeypatch, ae):
    rig = _paths_rig(monkeypatch, admin=False)
    first = _get(rig, ae=ae)
    assert first.status_code == 200 and PHONE_A not in first.text
    second = _get(rig, ae=ae, inm=first.headers["etag"])
    assert second.status_code == 304 and second.content == b""
    assert second.headers["etag"] == first.headers["etag"]


def test_role_change_admin_to_viewer_on_the_same_token_never_serves_cached_admin_bytes(monkeypatch):
    rig = _paths_rig(monkeypatch, admin=True)
    admin_first = _get(rig)
    _get(rig, ae="gzip")
    assert MAIL_A in admin_first.text
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)  # same bearer token, role downgraded
    for params in (OPT_IN, None):
        for ae in ("identity", "gzip"):
            resp = _get(rig, params, ae=ae)
            assert resp.status_code == 200
            for secret in ALL_SECRETS:
                assert secret not in resp.text, secret
    _as(rig, FAKE_CLAIMS_ADMIN)  # and back: the admin variant is still intact
    assert _get(rig).content == admin_first.content


def test_role_change_viewer_to_admin_serves_the_full_variant(monkeypatch):
    rig = _paths_rig(monkeypatch, admin=False)
    assert PHONE_B not in _get(rig).text
    _as(rig, FAKE_CLAIMS_ADMIN)
    assert PHONE_B in _get(rig).text
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
    assert PHONE_B not in _get(rig).text


def test_the_wildcard_validator_of_a_downgraded_session_gets_the_redacted_body(monkeypatch):
    rig = _paths_rig(monkeypatch, admin=True)
    _get(rig)
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
    resp = _get(rig, inm="*")
    assert resp.status_code == 200
    assert MAIL_B not in resp.text


def test_anonymous_and_otro_are_rejected_before_any_work(monkeypatch):
    rig = _paths_rig(monkeypatch, admin=False)
    _as(rig, FAKE_CLAIMS_VIEWER)  # resolves to "otro"
    resp = _get(rig)
    assert resp.status_code == 403 and PHONE_A not in resp.text
    del rig.app.dependency_overrides[current_claims]
    resp = _get(rig)
    assert resp.status_code == 401 and PHONE_A not in resp.text
    assert rig.ledger["walk"] == 0 and rig.ledger["roster"] == 0 and rig.ledger["encode"] == 0


# ── Degraded / Blob last-known-good path ────────────────────────────────────


def _poisoned_lkg_row() -> dict:
    """A copy written by an OLDER build (before the blob redaction blanked the
    contact fields): values sit in the stored last-known-good."""
    row = _lkg_row()
    row["inspector"].update(nombre_completo="Old Name", tarjeta_profesional=TP_A, num_telefono=PHONE_A,
                            correo_contacto=MAIL_A)
    return row


def test_a_degraded_blob_restore_is_blank_for_a_viewer_even_if_the_stored_copy_carries_values(monkeypatch):
    rig = Rig(monkeypatch, admin=False)
    _force_degraded(rig, monkeypatch)
    monkeypatch.setattr(
        stickers.blob_lkg, "load_json",
        lambda pathname, expected_type: [_poisoned_lkg_row()] if pathname == router_mod.STICKERS_LKG_BLOB else None,
    )
    for params in (OPT_IN, None):
        resp = _get(rig, params)
        body = resp.json()
        assert body["degraded"] is True and len(body["evaluaciones"]) == 1
        assert {k: body["evaluaciones"][0]["inspector"][k] for k in CONTACT} == dict.fromkeys(CONTACT, "")
        for secret in (TP_A, PHONE_A, MAIL_A):
            assert secret not in resp.text


def test_a_normal_degraded_restore_is_blank_for_both_roles_and_keeps_the_keys(monkeypatch):
    """Triangulation: the redacted LKG is already blank (`redact_for_blob`); the
    viewer redaction must not add or drop anything on it."""
    rig = Rig(monkeypatch, admin=False)
    _force_degraded(rig, monkeypatch)
    resp = _get(rig)
    assert resp.json()["evaluaciones"] == [_lkg_row()]


def test_the_admin_is_still_served_the_restored_copy_as_stored(monkeypatch):
    rig = Rig(monkeypatch, admin=True)
    _force_degraded(rig, monkeypatch)
    monkeypatch.setattr(
        stickers.blob_lkg, "load_json",
        lambda pathname, expected_type: [_poisoned_lkg_row()] if pathname == router_mod.STICKERS_LKG_BLOB else None,
    )
    assert _get(rig).json()["evaluaciones"][0]["inspector"]["num_telefono"] == PHONE_A


# ── Golden bytes ────────────────────────────────────────────────────────────

FIXED_PAYLOAD = [
    {"id": "g1", "inspector": {"uid": "u", "identificacion": "123", "nombre_completo": "Ana Gómez",
                               "tarjeta_profesional": "TP-9", "num_telefono": "3001", "correo_contacto": "a@x.co"}},
    {"id": "g2", "inspector": {"nombre_completo": "Sin Contacto"}},              # keys absent: nothing to blank
    {"id": "g3"},                                                                  # no inspector block
]
ADMIN_GOLDEN = JSONResponse(
    {"ok": True, "fuente": "atencionsismo", "evaluaciones": FIXED_PAYLOAD, "degraded": False}
).body  # the pre-change serialization, produced by Starlette itself
VIEWER_GOLDEN = (
    '{"ok":true,"fuente":"atencionsismo","evaluaciones":['
    '{"id":"g1","inspector":{"uid":"u","identificacion":"123","nombre_completo":"Ana Gómez",'
    '"tarjeta_profesional":"","num_telefono":"","correo_contacto":""}},'
    '{"id":"g2","inspector":{"nombre_completo":"Sin Contacto"}},'
    '{"id":"g3"}],"degraded":false}'
).encode("utf-8")


def _fixed_rig(monkeypatch, *, admin: bool) -> Rig:
    rig = Rig(monkeypatch, admin=admin)
    monkeypatch.setattr(router_mod, "build_payload", lambda *a, **k: copy.deepcopy(FIXED_PAYLOAD))
    return rig


@pytest.mark.parametrize("params", [None, {"depuracion": "0"}])
def test_admin_body_is_byte_identical_to_the_pre_change_serialization(monkeypatch, params):
    rig = _fixed_rig(monkeypatch, admin=True)
    assert _get(rig, params).content == ADMIN_GOLDEN


def test_admin_opt_in_body_is_the_golden_plus_only_the_depuracion_key(monkeypatch):
    rig = _fixed_rig(monkeypatch, admin=True)
    body = json.loads(_get(rig, OPT_IN).content)
    assert list(body) == ["ok", "fuente", "evaluaciones", "degraded", "depuracion"]
    assert json.dumps({k: v for k, v in body.items() if k != "depuracion"},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8") == ADMIN_GOLDEN


@pytest.mark.parametrize("params", [None, OPT_IN])
def test_viewer_body_is_the_golden_with_only_the_present_contact_values_blanked(monkeypatch, params):
    rig = _fixed_rig(monkeypatch, admin=False)
    assert _get(rig, params).content == VIEWER_GOLDEN


# ── Fail closed: only an exact "admin" gets the unredacted list ─────────────


@pytest.mark.parametrize("role", ["viewer", "otro", "", "ADMIN", "Admin", " admin", "usuario", None])
def test_anything_that_is_not_exactly_admin_gets_the_redacted_list(role):
    """`require_role` never lets these reach the builder today; the selector is
    the second lock, so a future role can never inherit the admin bytes."""
    payload = [{"id": "x", "inspector": {"num_telefono": PHONE_A}}]
    assert router_mod.evaluaciones_for_role(payload, role) == [{"id": "x", "inspector": {"num_telefono": ""}}]


def test_admin_gets_the_shared_list_itself_no_copy():
    payload = [{"id": "x", "inspector": {"num_telefono": PHONE_A}}]
    assert router_mod.evaluaciones_for_role(payload, "admin") is payload


# ── Odd rows: None / empty / missing / wrong types ──────────────────────────


def _redact(rows):
    return router_mod.redact_contact_fields(rows)


def test_redact_blanks_present_values_and_keeps_key_order():
    row = {"id": "x", "inspector": {"uid": "u", "tarjeta_profesional": TP_A, "np": "P1",
                                     "num_telefono": PHONE_A, "correo_contacto": MAIL_A, "codigo": "004"}}
    out = _redact([row])
    assert out == [{"id": "x", "inspector": {"uid": "u", "tarjeta_profesional": "", "np": "P1",
                                             "num_telefono": "", "correo_contacto": "", "codigo": "004"}}]
    assert list(out[0]["inspector"]) == list(row["inspector"])


@pytest.mark.parametrize("value", [None, "", "   ", 0, 3001112233, ["x"], {"a": 1}, False])
def test_redact_blanks_whatever_type_the_value_has(value):
    out = _redact([{"inspector": {"num_telefono": value, "correo_contacto": value, "tarjeta_profesional": value}}])
    assert out[0]["inspector"] == dict.fromkeys(CONTACT, "")


@pytest.mark.parametrize("row", [
    {"id": "no-inspector"},
    {"id": "none", "inspector": None},
    {"id": "empty", "inspector": {}},
    {"id": "wrong-type", "inspector": "Ana"},
    {"id": "list", "inspector": ["x"]},
    {"id": "no-contact-keys", "inspector": {"np": "P1"}},
])
def test_redact_leaves_rows_without_contact_keys_exactly_as_they_are(row):
    assert _redact([row]) == [row]


def test_redact_only_touches_the_present_keys_never_adds_missing_ones():
    out = _redact([{"inspector": {"num_telefono": PHONE_A}}])
    assert out == [{"inspector": {"num_telefono": ""}}]


def test_redact_of_an_empty_payload_is_an_empty_list_and_never_the_same_object():
    src: list = []
    out = _redact(src)
    assert out == [] and out is not src


def test_redact_never_mutates_its_input_nor_shares_the_redacted_dicts():
    row = {"id": "x", "fotos": ["f"], "inspector": {"num_telefono": PHONE_A, "np": "P1"}}
    src = [row]
    snapshot = copy.deepcopy(src)
    out = _redact(src)
    assert src == snapshot
    assert out[0] is not row and out[0]["inspector"] is not row["inspector"]
    assert out[0]["fotos"] is row["fotos"]  # everything else is shared, not deep-copied


def test_redact_passes_untouched_rows_through_by_identity():
    row = {"id": "x", "inspector": {"np": "P1"}}
    assert _redact([row])[0] is row


def test_redact_fails_closed_on_non_dict_rows_without_raising():
    weird = [None, 5, "x"]
    assert _redact(weird) == weird


def test_redact_of_a_large_payload_is_fast():
    rows = [{"id": str(i), "inspector": {"tarjeta_profesional": TP_A, "num_telefono": PHONE_A,
                                          "correo_contacto": MAIL_A, "np": "P1"}} for i in range(20_000)]
    start = time.perf_counter()
    out = _redact(rows)
    # Gross-regression ceiling only (>= 100x the measured cost): the guarantee
    # is the behavior asserted below, not a wall-clock budget that CI jitter breaks.
    assert time.perf_counter() - start < 30
    assert all(r["inspector"]["num_telefono"] == "" and r["inspector"]["np"] == "P1" for r in out)


def test_a_viewer_request_over_a_large_payload_is_served_redacted_and_built_once(monkeypatch):
    rig = Rig(monkeypatch, admin=False)
    big = [{"id": str(i), "inspector": {"tarjeta_profesional": TP_A, "num_telefono": PHONE_A,
                                        "correo_contacto": MAIL_A}} for i in range(5_000)]
    monkeypatch.setattr(router_mod, "build_payload", lambda *a, **k: copy.deepcopy(big))
    start = time.perf_counter()
    resp = _get(rig, ae="gzip")
    for _ in range(20):
        _get(rig, ae="gzip")
    assert time.perf_counter() - start < 60  # gross-regression ceiling, jitter-proof (>= 10x measured)
    assert resp.status_code == 200 and PHONE_A not in resp.text and len(resp.json()["evaluaciones"]) == 5_000
    assert rig.ledger["encode"] == 1  # 21 requests, one build of the redacted variant


# ── No PII in logs ──────────────────────────────────────────────────────────


def test_no_contact_value_reaches_a_log_line_across_a_viewer_and_admin_cycle(monkeypatch, caplog):
    rig = _paths_rig(monkeypatch, admin=False)
    with caplog.at_level(logging.DEBUG):
        first = _get(rig, OPT_IN, ae="gzip")
        _get(rig, ae="identity", inm=first.headers["etag"])
        _as(rig, FAKE_CLAIMS_ADMIN)
        _get(rig, OPT_IN)
        _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
        _get(rig, None)
    for secret in ALL_SECRETS:
        assert secret.lower() not in caplog.text.lower(), secret


# ── Routes that never carried the fields (characterization) ─────────────────


def test_the_plain_evaluaciones_route_never_serves_the_contact_fields(monkeypatch):
    """`list_evaluaciones` (GET /evaluaciones, POST /stickers {action:"evaluaciones"})
    projects `uid/codigo/nombre_completo/identificacion/entidad/np` explicitly:
    a Firestore doc carrying contact fields inside `inspector` cannot leak them."""
    rig = Rig(monkeypatch, admin=False)
    rig.stores["evaluaciones"] = {
        "e1": {"codigo_edificacion": "c1", "inspector": {
            "uid": "u1", "nombre_completo": "Ana Gomez", "tarjeta_profesional": TP_A,
            "num_telefono": PHONE_A, "correo_contacto": MAIL_A}},
    }
    resp = rig.client.get("/evaluaciones")
    assert resp.status_code == 200 and len(resp.json()["evaluaciones"]) == 1
    assert resp.json()["evaluaciones"][0]["inspector"]["nombre_completo"] == "Ana Gomez"  # not vacuous
    for secret in (TP_A, PHONE_A, MAIL_A):
        assert secret not in resp.text
    for key in CONTACT:
        assert key not in resp.json()["evaluaciones"][0]["inspector"]


def test_list_evaluaciones_projects_the_inspector_block_explicitly():
    from tests.ledger_fakes import CallLedger, LedgerFirestore

    stores = {"evaluaciones": {"e1": {"codigo_edificacion": "c1", "inspector": {
        "uid": "u1", "nombre_completo": "Ana", "tarjeta_profesional": TP_A, "num_telefono": PHONE_A,
        "correo_contacto": MAIL_A}}}, "inspectores": {"u1": {"NP": "P1"}}}
    rows = stickers.list_evaluaciones(LedgerFirestore(stores, CallLedger()))
    assert set(rows[0]["inspector"]) == {"uid", "codigo", "nombre_completo", "identificacion", "entidad", "np"}


# ── W1: error bodies carry no exception text (a viewer can reach every one) ─

LEAK_CEDULA, LEAK_NOMBRE, LEAK_MAIL = "1098765432", "Zulma Reservada Prieto", "zulma.reservada@example.com"
LEAKY_MESSAGE = f"cedula {LEAK_CEDULA} de {LEAK_NOMBRE} <{LEAK_MAIL}> no encontrada"
LEAK_TOKENS = (LEAK_CEDULA, LEAK_NOMBRE, LEAK_MAIL)


def _assert_leak_free(resp, caplog) -> None:
    for token in LEAK_TOKENS:
        assert token.lower() not in resp.text.lower(), token
        assert token.lower() not in caplog.text.lower(), token


def _raising_build(exc: BaseException):
    def build(*a, **k):
        raise exc
    return build


UNAVAILABLE_CASES = [
    ("api_unavailable", lambda: router_mod.atencionsismo.ApiUnavailableError(LEAKY_MESSAGE, status=503)),
    ("api_credentials", lambda: router_mod.atencionsismo.ApiCredentialsError(LEAKY_MESSAGE)),
    ("api_empty", lambda: router_mod.atencionsismo.ApiEmptyResultError(LEAKY_MESSAGE)),
    ("degraded_runtime_error", lambda: RuntimeError(LEAKY_MESSAGE)),
]


@pytest.mark.parametrize("admin", [True, False], ids=["admin", "viewer"])
@pytest.mark.parametrize("make", [c[1] for c in UNAVAILABLE_CASES], ids=[c[0] for c in UNAVAILABLE_CASES])
def test_a_503_carries_a_generic_stable_detail_never_the_exception_text(monkeypatch, caplog, admin, make):
    rig = Rig(monkeypatch, admin=admin)
    monkeypatch.setattr(router_mod, "build_payload", _raising_build(make()))
    with caplog.at_level(logging.DEBUG):
        resp = _get(rig)
    assert resp.status_code == 503
    assert resp.json() == {"detail": router_mod.DETALLE_SERVICIO_NO_DISPONIBLE}
    _assert_leak_free(resp, caplog)


@pytest.mark.parametrize("admin", [True, False], ids=["admin", "viewer"])
def test_a_502_carries_a_generic_stable_detail_and_a_type_and_location_only_log(monkeypatch, caplog, admin):
    from fastapi.testclient import TestClient

    rig = Rig(monkeypatch, admin=admin)
    monkeypatch.setattr(router_mod, "build_payload", _raising_build(KeyError(LEAKY_MESSAGE)))
    quiet = TestClient(rig.app, raise_server_exceptions=False)
    with caplog.at_level(logging.DEBUG):
        resp = quiet.get(URL, headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 502
    assert resp.json() == {"detail": router_mod.DETALLE_FALLO_STICKERS}
    assert "fallo no clasificado" in caplog.text and "KeyError" in caplog.text  # the log keeps type + location
    _assert_leak_free(resp, caplog)


@pytest.mark.parametrize("admin", [True, False], ids=["admin", "viewer"])
def test_get_evaluaciones_502_carries_a_generic_stable_detail_never_the_exception_text(monkeypatch, caplog, admin):
    rig = Rig(monkeypatch, admin=admin)
    monkeypatch.setattr(stickers, "scan_evaluaciones", _raising_build(RuntimeError(LEAKY_MESSAGE)))
    with caplog.at_level(logging.DEBUG):
        resp = rig.client.get("/evaluaciones")
    assert resp.status_code == 502
    assert resp.json() == {"detail": stickers.DETALLE_FALLO_EVALUACIONES}
    _assert_leak_free(resp, caplog)


def test_the_generic_details_are_distinct_non_empty_spanish_strings():
    details = {router_mod.DETALLE_SERVICIO_NO_DISPONIBLE, router_mod.DETALLE_FALLO_STICKERS,
               stickers.DETALLE_FALLO_EVALUACIONES}
    assert len(details) == 3 and all(isinstance(d, str) and d.strip() for d in details)


def test_a_healthy_degraded_response_is_still_a_200_and_the_error_mapping_is_untouched(monkeypatch):
    rig = Rig(monkeypatch, admin=False)
    _force_degraded(rig, monkeypatch)
    resp = _get(rig)
    assert resp.status_code == 200 and resp.json()["degraded"] is True


# ── D-ENFASIS: the registry's free-text `enfasis` is admin-only (depuracion) ─

ENFASIS_SECRETO = "Especializacion en estructuras SECRETA-ENF-77"


def _rig_with_enfasis(monkeypatch, *, admin: bool) -> Rig:
    rig = _paths_rig(monkeypatch, admin=admin)
    rig.referencia = ReferenciaBundle(
        vercel=(), fase2=(), generado_en="2026-09-12", activa=True, motivo="", codigos_duplicados=(),
        main=(EntradaReferencia(cedula_key=CEDULA, nombre_norm="beto secreto", np="P2", entidad="DAGRD",
                                codigo="", pasos=(), no_persona=False, nombre="Beto Secreto",
                                enfasis=ENFASIS_SECRETO),),
        huella="h-enfasis",
    )
    return rig


def test_admin_receives_enfasis_only_inside_the_depuracion_block(monkeypatch):
    rig = _rig_with_enfasis(monkeypatch, admin=True)
    body = _get(rig, OPT_IN).json()
    persona = next(i for i in body["depuracion"]["inspectores"] if i["identificacion"] == CEDULA)
    assert persona["enfasis"] == ENFASIS_SECRETO
    # never in the sticker rows' inspector block, for anyone
    for row in body["evaluaciones"]:
        assert "enfasis" not in row["inspector"]


@pytest.mark.parametrize("params", [OPT_IN, None, {"depuracion": "0"}])
@pytest.mark.parametrize("ae", ["identity", "gzip"])
def test_viewer_response_never_contains_enfasis_anywhere(monkeypatch, params, ae):
    rig = _rig_with_enfasis(monkeypatch, admin=False)
    resp = _get(rig, params, ae=ae)
    assert resp.status_code == 200
    assert "enfasis" not in resp.text
    assert ENFASIS_SECRETO not in resp.text
    assert "depuracion" not in resp.json()
