"""fetch_layer_domains() / decode_domain_codes() -- coded-domain decoding for
`barrio_vereda_lista` (the new EDE_v1 layer field with a ~420-code Cali
barrio domain). Labels are COSMETIC: any fetch/parse failure must never
fail the refresh (unlike fetch_survey_raw()'s query, which raises on
failure by design) -- see the module docstring on fetch_layer_domains.

`concepto_cierre` deliberately stays coded (decoded client-side, same as
habitabilidad/nivel_dano) and is NOT covered by decode_domain_codes here.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import refresh_data as rd  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _pjson_payload(coded_fields: dict) -> dict:
    fields = []
    for name, codes in coded_fields.items():
        fields.append({
            "name": name,
            "type": "esriFieldTypeString",
            "domain": {
                "type": "codedValue",
                "codedValues": [{"name": label, "code": code} for code, label in codes.items()],
            },
        })
    # A plain field with no domain -- must be skipped, not crash.
    fields.append({"name": "objectid", "type": "esriFieldTypeOID", "domain": None})
    return {"fields": fields}


# --- decode_domain_codes: pure function -------------------------------------


def test_decode_domain_codes_maps_known_code_to_label():
    s = pd.Series(["san_fernando_nuevo"])
    out = rd.decode_domain_codes(s, {"san_fernando_nuevo": "San Fernando Nuevo"})
    assert out.iloc[0] == "San Fernando Nuevo"


def test_decode_domain_codes_leaves_unknown_code_unchanged():
    s = pd.Series(["codigo_no_documentado"])
    out = rd.decode_domain_codes(s, {"san_fernando_nuevo": "San Fernando Nuevo"})
    assert out.iloc[0] == "codigo_no_documentado"


@pytest.mark.parametrize("value", [None, float("nan"), "", "   "])
def test_decode_domain_codes_maps_missing_values_to_none(value):
    s = pd.Series([value])
    out = rd.decode_domain_codes(s, {"x": "y"})
    assert pd.isna(out.iloc[0])


def test_decode_domain_codes_is_exact_match_case_sensitive():
    """The layer ships one code with an unusual capital ('Sticker_verde').
    Exact match only -- a differently-cased variant is an unknown code and
    passes through unchanged, it is not folded to the known one."""
    s = pd.Series(["Sticker_verde", "sticker_verde"])
    mapping = {"Sticker_verde": "Si su Sticker es verde marque esta opcion"}
    out = rd.decode_domain_codes(s, mapping)
    assert out.iloc[0] == "Si su Sticker es verde marque esta opcion"
    assert out.iloc[1] == "sticker_verde"


def test_decode_domain_codes_with_empty_mapping_passes_codes_through():
    s = pd.Series(["san_fernando_nuevo", None])
    out = rd.decode_domain_codes(s, {})
    assert out.iloc[0] == "san_fernando_nuevo"
    assert pd.isna(out.iloc[1])


def test_decode_domain_codes_does_not_mutate_input_series():
    s = pd.Series(["san_fernando_nuevo"])
    rd.decode_domain_codes(s, {"san_fernando_nuevo": "San Fernando Nuevo"})
    assert s.iloc[0] == "san_fernando_nuevo"


def test_decode_domain_codes_handles_all_nan_float_column():
    """A real float64-dtype column that is entirely missing (e.g. no record
    in a batch answered this question) -- must not raise, every value None."""
    s = pd.Series([float("nan"), float("nan")], dtype=float)
    out = rd.decode_domain_codes(s, {"san_fernando_nuevo": "San Fernando Nuevo"})
    assert out.isna().all()
    assert len(out) == 2


def test_decode_domain_codes_handles_empty_series():
    s = pd.Series([], dtype=object)
    out = rd.decode_domain_codes(s, {"san_fernando_nuevo": "San Fernando Nuevo"})
    assert len(out) == 0


def test_decode_domain_codes_handles_mixed_dtype_object_column():
    """A defensive fixture: str/int/None/NaN/bool all in the same object
    column. Must not raise; None/NaN/blank -> None, unknown values pass
    through (as their trimmed string form, same as any other unknown code)."""
    s = pd.Series(["san_fernando_nuevo", 5, None, float("nan"), True, ""], dtype=object)
    out = rd.decode_domain_codes(s, {"san_fernando_nuevo": "San Fernando Nuevo"})
    assert out.iloc[0] == "San Fernando Nuevo"
    assert out.iloc[1] == "5"
    assert pd.isna(out.iloc[2])
    assert pd.isna(out.iloc[3])
    assert out.iloc[4] == "True"
    assert pd.isna(out.iloc[5])


# --- fetch_layer_domains: network + parsing ---------------------------------


def test_fetch_layer_domains_returns_field_to_code_label_map(monkeypatch):
    payload = _pjson_payload({
        "barrio_vereda": {"san_fernando_nuevo": "San Fernando Nuevo"},
        "concepto_cierre": {"demolicion": "Demolicion"},
    })
    monkeypatch.setattr(rd.requests, "get", lambda *a, **k: _FakeResponse(payload))

    domains = rd.fetch_layer_domains(rd.SURVEY_LAYER_URL)

    assert domains == {
        "barrio_vereda": {"san_fernando_nuevo": "San Fernando Nuevo"},
        "concepto_cierre": {"demolicion": "Demolicion"},
    }
    assert "objectid" not in domains


def test_fetch_layer_domains_returns_empty_dict_on_connection_error(monkeypatch, caplog):
    def _raise(*a, **k):
        raise rd.requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(rd.requests, "get", _raise)

    with caplog.at_level("WARNING"):
        domains = rd.fetch_layer_domains(rd.SURVEY_LAYER_URL)

    assert domains == {}
    assert any("domain" in r.message.lower() for r in caplog.records)


def test_fetch_layer_domains_returns_empty_dict_on_http_error(monkeypatch):
    class _ErrResponse:
        def raise_for_status(self):
            raise rd.requests.exceptions.HTTPError("500 Server Error")

    monkeypatch.setattr(rd.requests, "get", lambda *a, **k: _ErrResponse())

    assert rd.fetch_layer_domains(rd.SURVEY_LAYER_URL) == {}


def test_fetch_layer_domains_returns_empty_dict_on_malformed_payload(monkeypatch):
    monkeypatch.setattr(rd.requests, "get", lambda *a, **k: _FakeResponse({}))

    assert rd.fetch_layer_domains(rd.SURVEY_LAYER_URL) == {}


# --- fetch_survey_raw() integration: barrio_vereda_lista gets decoded ------


def _full_attrs(**overrides) -> dict:
    attrs = {key: None for key in rd.LAYER_TO_RAW}
    attrs.update(overrides)
    return attrs


def _query_payload(attrs: dict) -> dict:
    return {"features": [{"attributes": attrs, "geometry": {"x": -76.53, "y": 3.42}}]}


def _mock_both_endpoints(monkeypatch, query_payload, pjson_payload):
    def fake_get(url, params=None, timeout=None):
        if url == f"{rd.SURVEY_LAYER_URL}/query":
            return _FakeResponse(query_payload)
        if url == rd.SURVEY_LAYER_URL:
            return _FakeResponse(pjson_payload)
        raise AssertionError(f"unexpected URL requested: {url}")

    monkeypatch.setattr(rd.requests, "get", fake_get)


def test_fetch_survey_raw_decodes_barrio_vereda_lista_via_layer_domain(monkeypatch):
    attrs = _full_attrs(
        objectid=1,
        globalid="{AAAAAAAA-1111-2222-3333-444444444444}",
        fecha_inspeccion=1755550000000,
        barrio_vereda="san_fernando_nuevo",
    )
    _mock_both_endpoints(
        monkeypatch,
        _query_payload(attrs),
        _pjson_payload({"barrio_vereda": {"san_fernando_nuevo": "San Fernando Nuevo"}}),
    )

    df = rd.fetch_survey_raw()

    assert df.loc[0, "barrio_vereda_lista"] == "San Fernando Nuevo"


def test_fetch_survey_raw_keeps_concepto_cierre_as_raw_code(monkeypatch):
    attrs = _full_attrs(
        objectid=1,
        globalid="{AAAAAAAA-1111-2222-3333-444444444444}",
        fecha_inspeccion=1755550000000,
        concepto_cierre="demolicion",
    )
    _mock_both_endpoints(
        monkeypatch,
        _query_payload(attrs),
        _pjson_payload({"concepto_cierre": {"demolicion": "Demolicion - texto largo"}}),
    )

    df = rd.fetch_survey_raw()

    assert df.loc[0, "concepto_cierre"] == "demolicion"


def test_fetch_survey_raw_domain_fetch_failure_keeps_raw_codes_and_does_not_crash(monkeypatch):
    attrs = _full_attrs(
        objectid=1,
        globalid="{AAAAAAAA-1111-2222-3333-444444444444}",
        fecha_inspeccion=1755550000000,
        barrio_vereda="san_fernando_nuevo",
    )

    def fake_get(url, params=None, timeout=None):
        if url == f"{rd.SURVEY_LAYER_URL}/query":
            return _FakeResponse(_query_payload(attrs))
        raise rd.requests.exceptions.ConnectionError("domain schema fetch failed")

    monkeypatch.setattr(rd.requests, "get", fake_get)

    df = rd.fetch_survey_raw()

    assert df.loc[0, "barrio_vereda_lista"] == "san_fernando_nuevo"


def test_fetch_survey_raw_legacy_record_with_no_new_field_values(monkeypatch):
    """1700+ legacy rows: every new-field attribute is None. barrio_vereda_lista
    must resolve to NA, never crash the decode step."""
    attrs = _full_attrs(
        objectid=1,
        globalid="{AAAAAAAA-1111-2222-3333-444444444444}",
        fecha_inspeccion=1700000000000,
    )
    _mock_both_endpoints(monkeypatch, _query_payload(attrs), _pjson_payload({}))

    df = rd.fetch_survey_raw()

    assert pd.isna(df.loc[0, "barrio_vereda_lista"])
