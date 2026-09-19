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
