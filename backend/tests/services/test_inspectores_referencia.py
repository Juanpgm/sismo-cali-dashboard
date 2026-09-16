"""RED-first tests for `app.services.inspectores_referencia` — the referencia
bundle I/O seam (design.md D8 / Interfaces-Contracts; tasks.md Phase 1).

`parse_bundle` is pure (plain dict fixtures, no mocks). `cargar_referencia`
is exercised via dependency injection of a fake `load_json` — no monkeypatch
on `blob_lkg` needed, since the seam takes `load_json` as a keyword default
(design contract: `cargar_referencia(*, load_json=blob_lkg.load_json, ...)`).
"""
from __future__ import annotations

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
