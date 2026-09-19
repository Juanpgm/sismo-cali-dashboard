"""RED-first tests for `app.services.inspectores_referencia` — the referencia
bundle I/O seam (design.md D8 / Interfaces-Contracts; tasks.md Phase 1).

`parse_bundle` is pure (plain dict fixtures, no mocks). `cargar_referencia`
is exercised via dependency injection of a fake `load_json` — no monkeypatch
on `blob_lkg` needed, since the seam takes `load_json` as a keyword default
(design contract: `cargar_referencia(*, load_json=blob_lkg.load_json, ...)`).
"""
from __future__ import annotations

import json
import logging
import urllib.error

import pytest

from app.services import blob_lkg
from app.services import inspectores_referencia as ir

VALID_RAW = {
    "schema": 1,
    "generado_en": "2026-09-12",
    "origen": {
        "vercel": "inspectores_vercel-app.csv",
        "fase2": "Listado verificado Fase 2.xlsx",
        "main": "tecnicos atencion sismo (12 sept 2026).csv",
    },
    "codigos_duplicados": ["097", "127"],
    "vercel": [
        {"cedula_key": "1234567", "nombre_norm": "juan perez", "np": "P3", "entidad": "DAGRD", "codigo": "041"}
    ],
    "fase2": [
        {"cedula_key": "1234567", "nombre_norm": "juan perez", "np": "P3", "pasos": [1, 2]}
    ],
    "main": [
        {
            "cedula_key": "1234567",
            "nombre_norm": "juan perez",
            "rango": "P2",
            "tarjeta_profesional": "TP-1",
            "correo": "juan@example.com",
            "no_persona": False,
        }
    ],
}


# --- parse_bundle: valid bundle (task 1.2 RED / 1.3 GREEN) ------------------


def test_parse_bundle_valid_populates_all_sections():
    bundle = ir.parse_bundle(VALID_RAW)

    assert bundle is not None
    assert bundle.activa is True
    assert bundle.motivo == ""
    assert bundle.generado_en == "2026-09-12"
    assert bundle.codigos_duplicados == ("097", "127")

    assert len(bundle.vercel) == 1
    entrada_vercel = bundle.vercel[0]
    assert entrada_vercel.cedula_key == "1234567"
    assert entrada_vercel.nombre_norm == "juan perez"
    assert entrada_vercel.np == "P3"
    assert entrada_vercel.entidad == "DAGRD"
    assert entrada_vercel.codigo == "041"

    assert len(bundle.fase2) == 1
    assert bundle.fase2[0].pasos == (1, 2)

    assert len(bundle.main) == 1
    entrada_main = bundle.main[0]
    # main rows carry "rango" instead of "np" in the raw bundle (design.md's
    # own bundle example) — parse_bundle must fold it into the shared field.
    assert entrada_main.np == "P2"
    assert entrada_main.no_persona is False


# --- parse_bundle: unknown schema (task 1.4) --------------------------------


def test_parse_bundle_unknown_schema_returns_none():
    raw = {**VALID_RAW, "schema": 2}
    assert ir.parse_bundle(raw) is None


# --- parse_bundle: malformed rows skipped, not raised (task 1.5) -----------


def test_parse_bundle_malformed_rows_skipped_not_raised():
    raw = {
        **VALID_RAW,
        "vercel": [
            {"cedula_key": "1234567", "nombre_norm": "juan perez", "np": "P3"},
            {"nombre_norm": "sin cedula", "np": "P1"},  # missing cedula_key
        ],
    }
    bundle = ir.parse_bundle(raw)

    assert bundle is not None
    assert len(bundle.vercel) == 1
    assert bundle.vercel[0].cedula_key == "1234567"


# --- parse_bundle: None/garbage input (task 1.6) ----------------------------


def test_parse_bundle_none_input_returns_none():
    assert ir.parse_bundle(None) is None


def test_parse_bundle_garbage_input_returns_none():
    assert ir.parse_bundle(["not", "a", "dict"]) is None


# --- parse_bundle: one malformed section leaves others intact (spec scenario,
# design's Testing Strategy row) --------------------------------------------


def test_parse_bundle_one_malformed_section_others_intact():
    raw = {**VALID_RAW, "fase2": "not-a-list"}
    bundle = ir.parse_bundle(raw)

    assert bundle is not None
    assert bundle.fase2 == ()
    assert len(bundle.vercel) == 1
    assert len(bundle.main) == 1


def test_parse_bundle_all_three_sections_empty():
    raw = {**VALID_RAW, "vercel": [], "fase2": [], "main": []}
    bundle = ir.parse_bundle(raw)

    assert bundle is not None
    assert bundle.activa is True
    assert bundle.vercel == ()
    assert bundle.fase2 == ()
    assert bundle.main == ()


# --- cargar_referencia: valid (task 1.7 RED / 1.8 GREEN) --------------------


def _fake_load_json(returns):
    def _loader(pathname, expected_type):
        assert pathname == ir.BUNDLE_BLOB
        assert expected_type is dict
        return returns

    return _loader


def test_cargar_referencia_valid_bundle(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    bundle = ir.cargar_referencia(load_json=_fake_load_json(VALID_RAW))

    assert bundle.activa is True
    assert bundle.motivo == ""
    assert len(bundle.vercel) == 1


# --- cargar_referencia: blob missing (task 1.9) -----------------------------


def test_cargar_referencia_blob_missing(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    bundle = ir.cargar_referencia(load_json=_fake_load_json(None))

    assert bundle == ir.ReferenciaBundle.vacia(motivo="sin_blob")


# --- cargar_referencia: no token (task 1.10) --------------------------------


def test_cargar_referencia_no_token(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    def _loader_should_not_be_called(pathname, expected_type):
        raise AssertionError("load_json must not be called without a token")

    bundle = ir.cargar_referencia(load_json=_loader_should_not_be_called)

    assert bundle == ir.ReferenciaBundle.vacia(motivo="sin_token")


# --- cargar_referencia: corrupt json (task 1.11) ----------------------------


def test_cargar_referencia_load_json_raises(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    def _raising_loader(pathname, expected_type):
        raise ValueError("boom")

    bundle = ir.cargar_referencia(load_json=_raising_loader)

    assert bundle.activa is False
    assert bundle.motivo == "manifiesto_invalido"


def test_cargar_referencia_load_json_returns_garbage_type(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    bundle = ir.cargar_referencia(load_json=_fake_load_json(["not", "a", "dict"]))

    assert bundle.activa is False
    assert bundle.motivo == "manifiesto_invalido"


def test_cargar_referencia_load_json_returns_unknown_schema(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    bundle = ir.cargar_referencia(load_json=_fake_load_json({**VALID_RAW, "schema": 99}))

    assert bundle.activa is False
    assert bundle.motivo == "esquema_invalido"


# --- token resolution: BLOB_PRIVATE_TOKEN first, BLOB_READ_WRITE_TOKEN fallback


@pytest.fixture(autouse=True)
def _clean_blob_env(monkeypatch):
    """Hermetic guard: real Blob tokens in the dev shell must not leak in."""
    monkeypatch.delenv("BLOB_PRIVATE_TOKEN", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)


def _never_called(pathname, expected_type):
    raise AssertionError("load_json must not be called without a usable token")


def test_token_private_only_is_enough(monkeypatch):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", "vercel_blob_rw_priv_secret")

    bundle = ir.cargar_referencia(load_json=_fake_load_json(VALID_RAW))

    assert bundle.activa is True


def test_token_both_set_private_wins(monkeypatch):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", "vercel_blob_rw_priv_secret")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_pub_secret")

    assert ir._token_available() is True
    assert isinstance(ir._token_available(), bool)
    assert ir.blob_sync.private_token() == "vercel_blob_rw_priv_secret"


def test_token_only_rw_set_is_fallback(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_pub_secret")

    assert ir._token_available() is True
    assert ir.cargar_referencia(load_json=_fake_load_json(VALID_RAW)).activa is True


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_token_blank_private_falls_back_to_rw(monkeypatch, blank):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", blank)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_pub_secret")

    assert ir._token_available() is True
    assert ir.cargar_referencia(load_json=_fake_load_json(VALID_RAW)).activa is True


@pytest.mark.parametrize("private,rw", [("", ""), ("  ", "  "), (None, None), ("  ", None)])
def test_token_both_unset_or_blank_is_sin_token(monkeypatch, private, rw):
    if private is not None:
        monkeypatch.setenv("BLOB_PRIVATE_TOKEN", private)
    if rw is not None:
        monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", rw)

    bundle = ir.cargar_referencia(load_json=_never_called)

    assert bundle == ir.ReferenciaBundle.vacia(motivo="sin_token")


def test_single_resolver_shared_with_blob_lkg_gate(monkeypatch):
    """The presence check the reference service and the blob_lkg private
    reader use must agree in every env combination (one resolver)."""
    for private, rw in [(None, None), ("x", None), (None, "y"), ("  ", "y"), ("x", "y"), ("  ", " ")]:
        monkeypatch.delenv("BLOB_PRIVATE_TOKEN", raising=False)
        monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
        if private is not None:
            monkeypatch.setenv("BLOB_PRIVATE_TOKEN", private)
        if rw is not None:
            monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", rw)
        assert ir._token_available() == blob_lkg._private_token_available()


# --- end-to-end through the REAL default reader (blob_lkg.load_json_private
# -> blob_sync.download_authenticated); only urlopen is faked ---------------

PRIV = "vercel_blob_rw_PrivStore9_SUPERSECRETVALUE"
PUB = "vercel_blob_rw_PubStore1_PUBLICSECRETVALUE"


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_urlopen(monkeypatch, behavior):
    calls = []

    def _fake(req, timeout=None):
        calls.append((req.full_url, dict(req.header_items())))
        return behavior(req)

    class _Opener:
        # `download_authenticated` now goes through `blob_sync._build_opener()`
        # (redirect handler that strips the Bearer cross-host), not `urlopen`.
        def open(self, req, timeout=None):
            return _fake(req, timeout=timeout)

    monkeypatch.setattr(blob_lkg.blob_sync.urllib.request, "urlopen", _fake)
    monkeypatch.setattr(blob_lkg.blob_sync, "_build_opener", lambda *a, **k: _Opener())
    return calls


def test_default_reader_hits_private_store_host_with_private_token(monkeypatch):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIV)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUB)
    calls = _install_urlopen(monkeypatch, lambda req: _Resp(json.dumps(VALID_RAW).encode()))

    bundle = ir.cargar_referencia()

    assert bundle.activa is True
    assert len(calls) == 1
    url, headers = calls[0]
    assert url == "https://privstore9.private.blob.vercel-storage.com/referencia/inspectores/bundle.json"
    assert headers["Authorization"] == f"Bearer {PRIV}"
    assert "PUBLICSECRETVALUE" not in str(calls)


def test_default_reader_works_with_only_private_token(monkeypatch):
    """Production shape: BLOB_PRIVATE_TOKEN set, BLOB_READ_WRITE_TOKEN unset
    or pointing elsewhere — the old gate (rw token only) reported sin_token."""
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIV)
    _install_urlopen(monkeypatch, lambda req: _Resp(json.dumps(VALID_RAW).encode()))

    assert ir.cargar_referencia().activa is True


def _http_error(code):
    def _raise(req):
        raise urllib.error.HTTPError(req.full_url, code, "err", hdrs=None, fp=None)

    return _raise


@pytest.mark.parametrize("code", [401, 403, 404, 500, 503])
def test_default_reader_http_failures_degrade_without_token_leak(monkeypatch, caplog, code):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIV)
    _install_urlopen(monkeypatch, _http_error(code))

    with caplog.at_level(logging.DEBUG):
        bundle = ir.cargar_referencia()

    assert bundle == ir.ReferenciaBundle.vacia(motivo="sin_blob")
    assert "SUPERSECRETVALUE" not in caplog.text


@pytest.mark.parametrize(
    "exc",
    [TimeoutError("timed out"), urllib.error.URLError("boom"), ConnectionResetError("reset")],
)
def test_default_reader_network_failures_degrade_without_token_leak(monkeypatch, caplog, exc):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIV)

    def _raise(req):
        raise exc

    _install_urlopen(monkeypatch, _raise)

    with caplog.at_level(logging.DEBUG):
        bundle = ir.cargar_referencia()

    assert bundle == ir.ReferenciaBundle.vacia(motivo="sin_blob")
    assert "SUPERSECRETVALUE" not in caplog.text


@pytest.mark.parametrize("body", [b"{not json", b"", b"\xff\xfe", b"[1, 2]", b"null"])
def test_default_reader_malformed_body_degrades_without_token_leak(monkeypatch, caplog, body):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIV)
    _install_urlopen(monkeypatch, lambda req: _Resp(body))

    with caplog.at_level(logging.DEBUG):
        bundle = ir.cargar_referencia()

    assert bundle.activa is False
    assert bundle.motivo in {"sin_blob", "manifiesto_invalido"}
    assert "SUPERSECRETVALUE" not in caplog.text


@pytest.mark.parametrize(
    "bad",
    ["no-prefix-SUPERSECRETVALUE", "vercel_blob_SUPERSECRETVALUE", "a_b_c_d_e_SUPERSECRETVALUE"],
)
def test_default_reader_malformed_token_degrades_gracefully_no_leak(monkeypatch, caplog, bad):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", bad)
    calls = _install_urlopen(monkeypatch, lambda req: _Resp(b"{}"))

    with caplog.at_level(logging.DEBUG):
        bundle = ir.cargar_referencia()

    assert calls == []  # the token is never sent anywhere
    assert bundle.activa is False
    assert bundle.motivo == "sin_blob"
    assert "SUPERSECRETVALUE" not in caplog.text


# --- Extension 2026-09-19 (PR 06): optional identity/contact fields at schema 1
# (spec datos-referencia-blob "Bundle Entries Carry Optional Identity And
# Contact Fields At Schema 1"; design D15) -----------------------------------

_CAMPOS_OPCIONALES = ("nombre", "telefono", "codigo", "creado_en", "id", "correo", "tarjeta_profesional")

_MAIN_COMPLETA = {
    "cedula_key": "1234567",
    "nombre_norm": "juan perez",
    "rango": "P2",
    "nombre": "Juan Perez",
    "telefono": "3001234567",
    "codigo": "021",
    "creado_en": "2026-01-05T10:00:00Z",
    "id": "3f2b8c1e-0000-4000-8000-000000000001",
    "correo": "juan@example.com",
    "tarjeta_profesional": "TP-1",
    "no_persona": False,
}


def _bundle_con(main=None, vercel=None, fase2=None):
    raw = {
        "schema": 1,
        "generado_en": "2026-09-12",
        "vercel": vercel if vercel is not None else [],
        "fase2": fase2 if fase2 is not None else [],
        "main": main if main is not None else [],
    }
    return ir.parse_bundle(raw)


def test_parse_entrada_new_optional_fields():
    entrada = ir._parse_entrada(_MAIN_COMPLETA)

    assert entrada is not None
    assert entrada.nombre == "Juan Perez"
    assert entrada.telefono == "3001234567"
    assert entrada.codigo == "021"
    assert entrada.creado_en == "2026-01-05T10:00:00Z"
    assert entrada.id == "3f2b8c1e-0000-4000-8000-000000000001"
    assert entrada.correo == "juan@example.com"
    assert entrada.tarjeta_profesional == "TP-1"
    # existing fields untouched
    assert entrada.cedula_key == "1234567"
    assert entrada.np == "P2"


def test_parse_bundle_new_bundle_exposes_contact_fields_and_stays_active():
    bundle = _bundle_con(main=[_MAIN_COMPLETA])

    assert bundle is not None and bundle.activa is True
    assert bundle.main[0].telefono == "3001234567"
    assert bundle.main[0].correo == "juan@example.com"


def test_entrada_referencia_new_fields_default_empty_when_built_directly():
    entrada = ir.EntradaReferencia(
        cedula_key="1", nombre_norm="x", np="", entidad="", codigo="", pasos=(), no_persona=False,
    )
    for campo in ("nombre", "telefono", "creado_en", "id", "correo", "tarjeta_profesional"):
        assert getattr(entrada, campo) == ""


def test_parse_bundle_old_bundle_yields_empty_contact_fields():
    bundle = ir.parse_bundle(VALID_RAW)  # published shape: none of the new keys

    assert bundle is not None
    assert bundle.activa is True
    assert bundle.generado_en == "2026-09-12"
    for entrada in (*bundle.vercel, *bundle.fase2):
        for campo in ("nombre", "telefono", "creado_en", "id", "correo", "tarjeta_profesional"):
            assert getattr(entrada, campo) == ""
    # `main` in the published shape already carries correo/tarjeta_profesional
    # (the publisher always emitted them) — they are now exposed, the rest empty.
    principal = bundle.main[0]
    assert principal.correo == "juan@example.com"
    assert principal.tarjeta_profesional == "TP-1"
    for campo in ("nombre", "telefono", "codigo", "creado_en", "id"):
        assert getattr(principal, campo) == ""


def test_parse_bundle_regression_published_bundle_existing_fields_unchanged():
    bundle = ir.parse_bundle(VALID_RAW)

    assert bundle.vercel[0].codigo == "041"
    assert bundle.vercel[0].entidad == "DAGRD"
    assert bundle.fase2[0].pasos == (1, 2)
    assert bundle.main[0].np == "P2"
    assert bundle.codigos_duplicados == ("097", "127")


def test_parse_bundle_unknown_extra_keys_are_ignored():
    fila = {**_MAIN_COMPLETA, "campo_futuro": "x", "otro": {"a": 1}}
    bundle = _bundle_con(main=[fila])

    assert bundle is not None
    assert bundle.main[0].correo == "juan@example.com"


def test_parse_bundle_wrong_typed_optional_fields():
    fila = {
        **_MAIN_COMPLETA,
        "telefono": 3001234567,
        "creado_en": None,
        "correo": ["a@b.co", "c@d.co"],
    }
    bundle = _bundle_con(main=[fila])

    assert bundle is not None
    entrada = bundle.main[0]
    assert entrada.telefono == "3001234567"
    assert entrada.creado_en == ""
    assert entrada.correo == ""
    # untouched siblings and identity
    assert entrada.cedula_key == "1234567"
    assert entrada.nombre_norm == "juan perez"
    assert entrada.np == "P2"
    assert entrada.tarjeta_profesional == "TP-1"


@pytest.mark.parametrize(
    "crudo, esperado",
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("  3001234567  ", "3001234567"),
        (3001234567, "3001234567"),
        (3001234567.0, "3001234567"),
        (float("nan"), ""),
        (float("inf"), ""),
        (True, ""),
        (False, ""),
        ([], ""),
        (["a"], ""),
        ({}, ""),
        ({"k": "v"}, ""),
        (("a",), ""),
        pytest.param("x" * 100_000, "x" * 100_000, id="very-long-string"),
    ],
    ids=lambda v: None if isinstance(v, str) and len(v) > 50 else repr(v)[:20],
)
def test_parse_entrada_optional_field_coercion_never_raises_or_alters_siblings(crudo, esperado):
    for campo in _CAMPOS_OPCIONALES:
        fila = {**_MAIN_COMPLETA, campo: crudo}
        entrada = ir._parse_entrada(fila)

        assert entrada is not None
        assert getattr(entrada, campo) == esperado
        # every OTHER field keeps its parsed value
        for otro in _CAMPOS_OPCIONALES:
            if otro != campo:
                assert getattr(entrada, otro) == ir._parse_entrada(_MAIN_COMPLETA).__getattribute__(otro)
        assert entrada.cedula_key == "1234567"
        assert entrada.nombre_norm == "juan perez"
        assert entrada.no_persona is False


def test_parse_bundle_schema_2_still_none():
    assert ir.parse_bundle({**VALID_RAW, "schema": 2, "main": [_MAIN_COMPLETA]}) is None
    assert ir._SCHEMA_VERSION == 1


@pytest.mark.parametrize("descartados", [
    {"main": 3, "vercel": 1, "fase2": 0}, 5, "n/a", None, [], {"main": "x"}, {"desconocido": float("nan")},
])
def test_parse_bundle_tolerates_the_optional_descartados_sin_cedula_key(descartados):
    # Publisher-side diagnostic only (W1): the parser ignores it whatever its
    # shape, the schema stays 1 and the entries are unaffected.
    bundle = ir.parse_bundle({**VALID_RAW, "main": [_MAIN_COMPLETA], "descartados_sin_cedula": descartados})
    assert bundle is not None and bundle.activa is True
    assert len(bundle.main) == 1


def test_parse_bundle_still_drops_blank_cedula_rows_so_the_publisher_count_is_the_only_trace():
    filas = [{"cedula_key": "", "nombre_norm": "x"}, {"cedula_key": "   ", "nombre_norm": "y"},
             {"cedula_key": None, "nombre_norm": "z"}, {"cedula_key": 123, "nombre_norm": "w"}]
    assert ir.parse_bundle({**VALID_RAW, "main": filas}).main == ()


def test_parse_bundle_vercel_fase2_sections_accept_new_fields_too():
    fila = {"cedula_key": "9", "nombre_norm": "ana", "np": "P3", "telefono": "3111111111",
            "correo": "ana@example.com", "creado_en": "2026-02-02", "id": "abc"}
    bundle = _bundle_con(main=[_MAIN_COMPLETA], vercel=[fila], fase2=[fila])

    assert bundle.vercel[0].telefono == "3111111111"
    assert bundle.fase2[0].correo == "ana@example.com"
    assert bundle.fase2[0].id == "abc"
    # absent on a section -> ignored (empty), not an error
    sin_nuevos = _bundle_con(vercel=[{"cedula_key": "9", "nombre_norm": "ana"}])
    assert sin_nuevos.vercel[0].telefono == ""


def test_cargar_referencia_malformed_bundle_never_logs_or_exposes_pii(monkeypatch, caplog):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")
    pii = "pii-secreto-3001234567@example.com"
    entradas_malas = [
        {"cedula_key": pii, "correo": pii, "telefono": {"x": pii}},
        {"cedula_key": "", "correo": pii, "nombre": pii},
        "no es un dict " + pii,
        {"cedula_key": "1", "correo": [pii], "pasos": [pii]},
    ]
    payloads = [
        {"schema": 1, "vercel": entradas_malas, "fase2": pii, "main": entradas_malas,
         "codigos_duplicados": [pii, {"x": pii}]},
        {"schema": pii, "main": entradas_malas},
        {"schema": 99, "main": entradas_malas},
        [pii],
        pii,
    ]
    with caplog.at_level(logging.DEBUG):
        for payload in payloads:
            bundle = ir.cargar_referencia(load_json=_fake_load_json(payload))
            assert pii not in bundle.motivo
            assert pii not in bundle.generado_en
            assert "3001234567" not in bundle.motivo

    assert pii not in caplog.text
    assert "3001234567" not in caplog.text


# --- W1: PII never leaks through the auto-generated repr --------------------

_PII_FALSOS = {
    "nombre": "Nombre-Falso-Zzyx",
    "telefono": "3009998877",
    "correo": "falso.zzyx@example.com",
    "tarjeta_profesional": "TP-FALSA-77123",
}


def test_entrada_repr_and_bundle_repr_hide_pii_fields():
    fila = {**_MAIN_COMPLETA, **_PII_FALSOS}
    bundle = _bundle_con(main=[fila], vercel=[fila], fase2=[fila])

    assert bundle.main[0].nombre == _PII_FALSOS["nombre"]  # still readable
    for texto in (repr(bundle.main[0]), repr(bundle)):
        for valor in _PII_FALSOS.values():
            assert valor not in texto
    # non-PII identifiers stay visible for debugging
    assert "1234567" in repr(bundle.main[0])


# --- W2: one coercion for optional fields AND codigos_duplicados ------------


@pytest.mark.parametrize(
    "crudo, esperado",
    [
        ("21.0", "21"),
        ("21.00", "21.00"),  # C3: only a lone ".0" is a float artifact
        ("12.000", "12.000"),
        ("166.000", "166.000"),
        (" 21.0 ", "21"),
        ("0.0", "0"),
        ("021", "021"),
        ("021.0", "021"),
        ("3.0e9", "3.0e9"),
        ("3.5", "3.5"),
        ("1.234.567", "1.234.567"),
        (".0", ".0"),
        ("21.0a", "21.0a"),
        (21.0, "21"),
        (21, "21"),
    ],
)
def test_texto_opcional_drops_pure_decimal_zero_tail_from_strings(crudo, esperado):
    assert ir._texto_opcional(crudo) == esperado


def test_parse_bundle_codigos_duplicados_use_the_same_coercion():
    bundle = ir.parse_bundle({
        "schema": 1,
        "codigos_duplicados": [21.0, "021", 22, "22.0", " 23.0 ", None, "", True, float("nan"), ["x"], {"k": 1}],
    })

    assert bundle is not None
    assert bundle.codigos_duplicados == ("21", "021", "22", "22", "23")


def test_remapear_codigos_honors_numeric_codigos_duplicados_from_bundle():
    from app.services import inspectores_depuracion as dep

    def _v(cedula, nombre, codigo):
        return {"cedula_key": cedula, "nombre_norm": nombre, "codigo": codigo}

    bundle = ir.parse_bundle({
        "schema": 1,
        "codigos_duplicados": [21.0, "021", 22],
        "vercel": [
            _v("1111111", "ana uno", "21"),
            _v("2222222", "beto dos", "021"),
            _v("3333333", "carla tres", "22"),
            _v("4444444", "dario cuatro", "23"),
        ],
    })
    roster = {
        c: {"identificacion": c, "nombre_completo": n}
        for c, n in (("1111111", "Ana Uno"), ("2222222", "Beto Dos"),
                     ("3333333", "Carla Tres"), ("4444444", "Dario Cuatro"))
    }
    perfiles, _ = dep.fusionar_identidad([], roster, bundle)
    perfiles, revision = dep.remapear_codigos(perfiles, bundle)

    assert perfiles["1111111"].codigo == ""   # 21.0 (float) excludes "21"
    assert perfiles["2222222"].codigo == ""   # "021" keeps its zero and excludes "021"
    assert perfiles["3333333"].codigo == ""   # 22 (int) excludes "22"
    assert perfiles["4444444"].codigo == "23"  # not listed -> remapped normally
    assert revision == ()


# --- huella: content fingerprint of the raw bundle (D24, tasks 10.25-10.27) --


def _reordered(raw: dict) -> dict:
    """Same content, JSON object keys in reverse order at every dict level."""
    if isinstance(raw, dict):
        return {k: _reordered(raw[k]) for k in reversed(list(raw))}
    if isinstance(raw, list):
        return [_reordered(x) for x in raw]
    return raw


def test_huella_identical_for_identical_content_and_json_key_order():
    a = ir.parse_bundle(VALID_RAW)
    b = ir.parse_bundle(_reordered(VALID_RAW))
    assert a is not None and b is not None
    assert a.huella and len(a.huella) == 64
    assert a.huella == b.huella
    assert a.huella == ir.blob_lkg.payload_hash(VALID_RAW)


def test_huella_changes_on_same_day_republish():
    republished = {**VALID_RAW, "vercel": [{**VALID_RAW["vercel"][0], "np": "P4"}]}
    assert republished["generado_en"] == VALID_RAW["generado_en"]
    assert ir.parse_bundle(republished).huella != ir.parse_bundle(VALID_RAW).huella


def test_huella_changes_when_only_an_optional_contact_field_changes():
    with_phone = {**VALID_RAW, "vercel": [{**VALID_RAW["vercel"][0], "telefono": "3000000001"}]}
    other_phone = {**VALID_RAW, "vercel": [{**VALID_RAW["vercel"][0], "telefono": "3000000002"}]}
    assert ir.parse_bundle(with_phone).huella != ir.parse_bundle(other_phone).huella
    assert ir.parse_bundle(with_phone).huella != ir.parse_bundle(VALID_RAW).huella


def test_huella_is_order_sensitive_for_row_order_inside_a_section():
    row2 = {"cedula_key": "7654321", "nombre_norm": "ana", "np": "P1"}
    a = {**VALID_RAW, "vercel": [VALID_RAW["vercel"][0], row2]}
    b = {**VALID_RAW, "vercel": [row2, VALID_RAW["vercel"][0]]}
    assert ir.parse_bundle(a).huella != ir.parse_bundle(b).huella  # first-wins makes row order an INPUT


def test_huella_empty_for_degraded_bundle_and_never_equals_a_real_one():
    degraded = ir.ReferenciaBundle.vacia(motivo="sin_blob")
    assert degraded.huella == ""
    assert ir.parse_bundle(VALID_RAW).huella != degraded.huella


def test_huella_empty_for_schema_mismatch_and_malformed_bundles(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")
    assert ir.cargar_referencia(load_json=_fake_load_json({**VALID_RAW, "schema": 99})).huella == ""
    assert ir.cargar_referencia(load_json=_fake_load_json(["not", "a", "dict"])).huella == ""
    assert ir.cargar_referencia(load_json=_fake_load_json(None)).huella == ""


def test_old_bundle_without_any_huella_key_still_parses_and_gets_a_computed_one():
    assert "huella" not in VALID_RAW
    bundle = ir.parse_bundle(VALID_RAW)
    assert bundle is not None and bundle.activa is True and bundle.huella


def test_huella_from_cargar_referencia_matches_parse_bundle(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")
    loaded = ir.cargar_referencia(load_json=_fake_load_json(VALID_RAW))
    assert loaded.huella == ir.parse_bundle(VALID_RAW).huella


def test_huella_never_leaks_pii_in_the_bundle_repr():
    with_phone = {**VALID_RAW, "vercel": [{**VALID_RAW["vercel"][0], "telefono": "3000000009"}]}
    assert "3000000009" not in repr(ir.parse_bundle(with_phone))


# --- D-ENFASIS: optional free-text `enfasis` at schema 1 ---------------------


def test_entrada_referencia_enfasis_defaults_to_empty_when_built_directly():
    entrada = ir.EntradaReferencia(
        cedula_key="1", nombre_norm="x", np="", entidad="", codigo="", pasos=(), no_persona=False,
    )
    assert entrada.enfasis == ""


def test_parse_entrada_reads_enfasis_verbatim_trimmed():
    entrada = ir._parse_entrada({**_MAIN_COMPLETA, "enfasis": "  Especialización en Estructuras "})
    assert entrada is not None
    assert entrada.enfasis == "Especialización en Estructuras"  # accents and case untouched
    assert entrada.tarjeta_profesional == "TP-1"  # neighbours untouched


def test_parse_bundle_old_bundle_without_the_key_parses_with_blank_enfasis():
    bundle = ir.parse_bundle(VALID_RAW)  # published before the field existed

    assert bundle is not None and bundle.activa is True
    for entrada in (*bundle.vercel, *bundle.fase2, *bundle.main):
        assert entrada.enfasis == ""


@pytest.mark.parametrize(
    "crudo, esperado",
    [
        ("Geotecnia", "Geotecnia"),
        ("  Geotecnia  ", "Geotecnia"),
        ("ESTRUCTURAS", "ESTRUCTURAS"),
        ("Construcción y sismo", "Construcción y sismo"),
        ("<script>alert('x')</script> \"a\" & <b>", "<script>alert('x')</script> \"a\" & <b>"),
        ("Nivel 2.0 estructuras", "Nivel 2.0 estructuras"),
        ("Estructuras " * 200, ("Estructuras " * 200).strip()),  # 2,000+ chars are kept whole
        (None, ""),
        ("", ""),
        ("   ", ""),
        (float("nan"), ""),
        (float("inf"), ""),
        (5, "5"),  # coerced like every optional field
        (5.0, "5"),
        (True, ""),
        (["Geotecnia"], ""),  # a container is never str()'d
        ({"a": "b"}, ""),
    ],
    ids=lambda v: repr(v)[:24],
)
def test_parse_entrada_enfasis_tolerant_coercion(crudo, esperado):
    entrada = ir._parse_entrada({**_MAIN_COMPLETA, "enfasis": crudo})
    assert entrada is not None
    assert entrada.enfasis == esperado


def test_parse_bundle_enfasis_round_trips_and_wrong_typed_one_never_drops_the_row():
    bundle = _bundle_con(main=[
        {**_MAIN_COMPLETA, "enfasis": "Geotecnia"},
        {**_MAIN_COMPLETA, "cedula_key": "7654321", "enfasis": ["x"]},
    ])
    assert bundle is not None
    assert [e.enfasis for e in bundle.main] == ["Geotecnia", ""]
    assert [e.cedula_key for e in bundle.main] == ["1234567", "7654321"]


# --- D-PROFESION: optional free-text `profesion` at schema 1 -----------------


def test_entrada_referencia_profesion_defaults_to_empty_when_built_directly():
    entrada = ir.EntradaReferencia(
        cedula_key="1", nombre_norm="x", np="", entidad="", codigo="", pasos=(), no_persona=False,
    )
    assert entrada.profesion == ""


def test_parse_entrada_reads_profesion_verbatim_trimmed():
    entrada = ir._parse_entrada({**_MAIN_COMPLETA, "profesion": "  Ingeniero Civil "})
    assert entrada is not None
    assert entrada.profesion == "Ingeniero Civil"  # case untouched
    assert entrada.tarjeta_profesional == "TP-1"  # neighbours untouched


def test_parse_bundle_old_bundle_without_the_key_parses_with_blank_profesion():
    bundle = ir.parse_bundle(VALID_RAW)  # published before the field existed

    assert bundle is not None and bundle.activa is True
    for entrada in (*bundle.vercel, *bundle.fase2, *bundle.main):
        assert entrada.profesion == ""


@pytest.mark.parametrize(
    "crudo, esperado",
    [
        ("Arquitecto", "Arquitecto"),
        ("  Arquitecto  ", "Arquitecto"),
        ("INGENIERO CIVIL", "INGENIERO CIVIL"),
        ("ingeniero", "ingeniero"),
        ("Psicólogo", "Psicólogo"),
        ("<script>alert('x')</script> \"a\" & <b>", "<script>alert('x')</script> \"a\" & <b>"),
        ("Ingeniero 2.0", "Ingeniero 2.0"),
        ("Ingeniero civil " * 150, ("Ingeniero civil " * 150).strip()),  # 2,000+ chars are kept whole
        (None, ""),
        ("", ""),
        ("   ", ""),
        (float("nan"), ""),
        (float("inf"), ""),
        (5, "5"),  # coerced like every optional field
        (5.0, "5"),
        (True, ""),
        (["Arquitecto"], ""),  # a container is never str()'d
        ({"a": "b"}, ""),
    ],
    ids=lambda v: repr(v)[:24],
)
def test_parse_entrada_profesion_tolerant_coercion(crudo, esperado):
    entrada = ir._parse_entrada({**_MAIN_COMPLETA, "profesion": crudo})
    assert entrada is not None
    assert entrada.profesion == esperado


def test_parse_bundle_profesion_round_trips_and_wrong_typed_one_never_drops_the_row():
    bundle = _bundle_con(main=[
        {**_MAIN_COMPLETA, "profesion": "Arquitecto"},
        {**_MAIN_COMPLETA, "cedula_key": "7654321", "profesion": ["x"]},
    ])
    assert bundle is not None
    assert [e.profesion for e in bundle.main] == ["Arquitecto", ""]
    assert [e.cedula_key for e in bundle.main] == ["1234567", "7654321"]


def test_parse_entrada_profesion_and_enfasis_are_read_from_their_own_keys():
    entrada = ir._parse_entrada({**_MAIN_COMPLETA, "enfasis": "Geotecnia", "profesion": "Arquitecto"})
    assert entrada is not None
    assert (entrada.enfasis, entrada.profesion) == ("Geotecnia", "Arquitecto")
    solo = ir._parse_entrada({**_MAIN_COMPLETA, "profesion": "Arquitecto"})
    assert solo is not None and (solo.enfasis, solo.profesion) == ("", "Arquitecto")
