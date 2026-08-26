"""GET /integracion/* — read-only interop endpoints, API-key gated.

Mirrors the router-test convention (`test_planeacion_asignaciones.py`):
`TestClient` + a fake in-memory Firestore double (no real service-account
JSON, no network), `credentials.sismo()` monkeypatched. The API-key gate is
exercised directly (valid/missing/empty/wrong + unset-fail-closed); the
interop projection, pagination, the three lookups, and the inferred
`sticker_globalid` derivation are exercised against the fake store.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.credentials import clients as credentials
from app.main import create_app

PLANEACION_PUNTOS = "planeacion_puntos"
STICKER_MATCHES = "sticker_matches"

API_KEY = "s3cr3t-interop-key"
HDR = {"X-API-Key": API_KEY}

INTEROP_KEYS = {
    "registro_id", "clave_integracion", "codigoapp", "tiene_survey",
    "survey_globalid", "match_via", "sticker_globalid",
}


# ── Fake Firestore: (collection, id) keyed; == where, order_by, start_after,
# limit, get(), get_all(). ──────────────────────────────────────────────────


class _Snap:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _DocRef:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store = store
        self.id = doc_id

    def get(self) -> _Snap:
        return _Snap(self.id, self._store.get(self.id))

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        current = dict(self._store.get(self.id, {})) if merge else {}
        current.update(data)
        self._store[self.id] = current


class _Query:
    def __init__(self, store, ids=None, order_field=None, after=None, limit_n=None) -> None:
        self._store = store
        self._ids = list(store.keys()) if ids is None else ids
        self._order_field = order_field
        self._after = after
        self._limit_n = limit_n

    def where(self, field: str, op: str, value: Any) -> "_Query":
        assert op == "==", op
        matched = [i for i in self._ids if self._store.get(i, {}).get(field) == value]
        return _Query(self._store, matched, self._order_field, self._after, self._limit_n)

    def order_by(self, field: str, direction: str | None = None) -> "_Query":
        return _Query(self._store, self._ids, field, self._after, self._limit_n)

    def start_after(self, value: Any) -> "_Query":
        if isinstance(value, dict):
            value = value[self._order_field]
        return _Query(self._store, self._ids, self._order_field, value, self._limit_n)

    def limit(self, n: int) -> "_Query":
        return _Query(self._store, self._ids, self._order_field, self._after, n)

    def _resolved(self) -> list[str]:
        ids = list(self._ids)
        if self._order_field is not None:
            ids.sort(key=lambda i: self._store.get(i, {}).get(self._order_field))
            if self._after is not None:
                ids = [i for i in ids if self._store.get(i, {}).get(self._order_field) > self._after]
        if self._limit_n is not None:
            ids = ids[: self._limit_n]
        return ids

    def get(self) -> list[_Snap]:
        return [_Snap(i, self._store.get(i)) for i in self._resolved()]


class _Collection(_Query):
    def document(self, doc_id: str) -> _DocRef:
        return _DocRef(self._store, doc_id)


class _Firestore:
    def __init__(self, stores) -> None:
        self._stores = stores

    def collection(self, name: str) -> _Collection:
        return _Collection(self._stores.setdefault(name, {}))

    def get_all(self, refs, field_paths=None) -> list[_Snap]:
        return [ref.get() for ref in refs]


class _SismoClients:
    def __init__(self, stores) -> None:
        self.firestore = _Firestore(stores)
        self.app = object()


def _client(monkeypatch, stores, *, api_key: str | None = API_KEY) -> TestClient:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "x")
    if api_key is None:
        monkeypatch.delenv("INTEROP_API_KEY", raising=False)
    else:
        monkeypatch.setenv("INTEROP_API_KEY", api_key)
    credentials.s3.cache_clear()
    monkeypatch.setattr(credentials, "sismo", lambda: _SismoClients(stores))
    return TestClient(create_app())


def _punto(registro_id, *, clave, tiene_survey=False, survey_globalid=None, match_via=None):
    return {
        "registro_id": registro_id,
        "clave_integracion": clave,
        "tiene_survey": tiene_survey,
        "survey_globalid": survey_globalid,
        "match_via": match_via,
        # noise fields the projection must NOT leak:
        "direccion": "CL 1 # 2-3", "comuna": "3", "estado_asignacion": "pendiente",
        "inspector_uid": "uid-x", "prioridad_score": 42,
    }


def _stores(puntos=None, stickers=None):
    return {
        PLANEACION_PUNTOS: dict(puntos or {}),
        STICKER_MATCHES: dict(stickers or {}),
    }


# ── API-key gate ────────────────────────────────────────────────────────────


def test_valid_api_key_returns_200(monkeypatch):
    client = _client(monkeypatch, _stores())
    resp = client.get("/integracion/llaves", headers=HDR)
    assert resp.status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": ""}, {"X-API-Key": "wrong"}])
def test_bad_api_key_rejected_401(monkeypatch, headers):
    client = _client(monkeypatch, _stores())
    resp = client.get("/integracion/llaves", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "API key inválida o ausente."


def test_unset_interop_key_fails_closed(monkeypatch):
    # Even a request presenting *some* key is rejected when the service is
    # unconfigured — never open access.
    client = _client(monkeypatch, _stores(), api_key=None)
    resp = client.get("/integracion/llaves", headers={"X-API-Key": "anything"})
    assert resp.status_code == 401


# ── /llaves projection + pagination ─────────────────────────────────────────


def test_llaves_projects_exactly_the_interop_key_subset(monkeypatch):
    puntos = {"atencionsismo_a": _punto("a", clave="PLN-A-00000001", tiene_survey=True,
                                        survey_globalid="g1", match_via="clave")}
    client = _client(monkeypatch, _stores(puntos))
    resp = client.get("/integracion/llaves", headers=HDR)
    assert resp.status_code == 200
    llaves = resp.json()["llaves"]
    assert len(llaves) == 1
    llave = llaves[0]
    assert set(llave) == INTEROP_KEYS
    # codigoapp == clave_integracion by construction.
    assert llave["codigoapp"] == llave["clave_integracion"] == "PLN-A-00000001"
    assert llave["registro_id"] == "a"


def test_llaves_does_not_hide_matched_rows(monkeypatch):
    puntos = {
        "atencionsismo_a": _punto("a", clave="PLN-A-1", tiene_survey=True, survey_globalid="g1"),
        "atencionsismo_b": _punto("b", clave="PLN-B-1", tiene_survey=False),
    }
    client = _client(monkeypatch, _stores(puntos))
    resp = client.get("/integracion/llaves", headers=HDR)
    got = {l["registro_id"] for l in resp.json()["llaves"]}
    assert got == {"a", "b"}  # the matched row is present, unlike listPuntos' default


def test_llaves_tiene_survey_filter(monkeypatch):
    puntos = {
        "atencionsismo_a": _punto("a", clave="PLN-A-1", tiene_survey=True, survey_globalid="g1"),
        "atencionsismo_b": _punto("b", clave="PLN-B-1", tiene_survey=False),
    }
    client = _client(monkeypatch, _stores(puntos))
    resp = client.get("/integracion/llaves?tiene_survey=true", headers=HDR)
    got = {l["registro_id"] for l in resp.json()["llaves"]}
    assert got == {"a"}


def test_llaves_paginates_with_cursor(monkeypatch):
    puntos = {f"atencionsismo_{c}": _punto(c, clave=f"PLN-{c}-1") for c in "abcde"}
    client = _client(monkeypatch, _stores(puntos))

    page1 = client.get("/integracion/llaves?limit=2", headers=HDR).json()
    assert [l["registro_id"] for l in page1["llaves"]] == ["a", "b"]
    assert page1["next_cursor"] == "b"

    page2 = client.get(f"/integracion/llaves?limit=2&cursor={page1['next_cursor']}", headers=HDR).json()
    assert [l["registro_id"] for l in page2["llaves"]] == ["c", "d"]
    assert page2["next_cursor"] == "d"

    page3 = client.get(f"/integracion/llaves?limit=2&cursor={page2['next_cursor']}", headers=HDR).json()
    assert [l["registro_id"] for l in page3["llaves"]] == ["e"]
    assert page3["next_cursor"] is None


# ── the three lookups ───────────────────────────────────────────────────────


def test_por_atencionsismo_hit_and_404(monkeypatch):
    puntos = {"atencionsismo_42": _punto("42", clave="PLN-42-1")}
    client = _client(monkeypatch, _stores(puntos))

    hit = client.get("/integracion/por-atencionsismo/42", headers=HDR)
    assert hit.status_code == 200
    assert hit.json()["llave"]["registro_id"] == "42"

    miss = client.get("/integracion/por-atencionsismo/999", headers=HDR)
    assert miss.status_code == 404


def test_por_clave_hit_and_404(monkeypatch):
    puntos = {"atencionsismo_42": _punto("42", clave="PLN-42-DEADBEEF")}
    client = _client(monkeypatch, _stores(puntos))

    hit = client.get("/integracion/por-clave/PLN-42-DEADBEEF", headers=HDR)
    assert hit.status_code == 200
    assert hit.json()["llave"]["registro_id"] == "42"

    miss = client.get("/integracion/por-clave/PLN-NOPE-0", headers=HDR)
    assert miss.status_code == 404


def test_por_survey_hit_and_404(monkeypatch):
    puntos = {"atencionsismo_42": _punto("42", clave="PLN-42-1", tiene_survey=True, survey_globalid="g-42")}
    client = _client(monkeypatch, _stores(puntos))

    hit = client.get("/integracion/por-survey/g-42", headers=HDR)
    assert hit.status_code == 200
    assert hit.json()["llave"]["registro_id"] == "42"

    miss = client.get("/integracion/por-survey/g-nope", headers=HDR)
    assert miss.status_code == 404


# ── sticker_globalid inferred tie ───────────────────────────────────────────


def test_sticker_globalid_present_when_ede_doc_exists_else_null(monkeypatch):
    puntos = {
        "atencionsismo_a": _punto("a", clave="PLN-A-1", tiene_survey=True, survey_globalid="g1"),
        "atencionsismo_b": _punto("b", clave="PLN-B-1", tiene_survey=True, survey_globalid="g2"),
    }
    stickers = {"ede_g1": {"fuente": "ede", "registro_id": "g1", "tiene_sticker": True}}
    client = _client(monkeypatch, _stores(puntos, stickers))
    by_reg = {l["registro_id"]: l for l in client.get("/integracion/llaves", headers=HDR).json()["llaves"]}
    assert by_reg["a"]["sticker_globalid"] == "g1"  # ede_g1 exists -> inferred link
    assert by_reg["b"]["sticker_globalid"] is None   # no ede_g2 doc -> null
