"""GET /stickers-atencionsismo (RED first) — design D4. Same fixture
conventions as test_stickers.py: TestClient(create_app()), auth via
dependency override of current_claims, in-memory Firestore fake, and the
atencionsismo client monkeypatched wholesale (no network).

No `fake_sismo` fixture exists in test_stickers.py (plan's fallback
instruction): this file reuses that module's `_app`/`_FakeAuth` helpers —
the SAME Firestore fake `_app` already wires through `credentials.sismo` —
instead of creating a second fake."""
from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.main import create_app
from app.routers import stickers_atencionsismo as router_mod
from app.services import atencionsismo

# Reuse the fake Firestore/Auth/claims fixtures from test_stickers.py by
# importing them (pytest collects fixtures from imported modules only when
# re-exported here):
from tests.routers.test_stickers import (  # noqa: F401
    FAKE_CLAIMS_ADMIN, FAKE_CLAIMS_INSTITUCIONAL, FAKE_CLAIMS_VIEWER, _app, _FakeAuth,
)

ROWS = [
    {"id": "ev-1", "direccion": "Calle 1", "latitud": "3.45", "longitud": "-76.53", "numero": "76001-1-0040001",
     "personaAfectada": "Juan", "origen": "firebase", "color": "rojo", "colorEtiqueta": "No habitable"},
    {"id": "ev-2", "direccion": "Calle 2", "latitud": "3.46", "longitud": "-76.52", "numero": "76001001-123-0001",
     "personaAfectada": "Ana", "origen": "sistema", "color": "verde", "colorEtiqueta": "Habitable"},
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def fake_fetch(client, user, password, **kw):
        return list(ROWS)

    monkeypatch.setattr(atencionsismo, "fetch_stickers", fake_fetch)
    fake_auth = _FakeAuth()
    stores = {"inspectores": {"u1": {"codigo": "004", "NP": "P4"}}, "evaluaciones": {}}
    app = _app(monkeypatch, fake_auth, stores)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN
    return TestClient(app)


def test_rejects_role_otro(client):
    client.app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER
    assert client.get("/stickers-atencionsismo").status_code == 403


def test_viewer_can_read(client):
    client.app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_INSTITUCIONAL
    assert client.get("/stickers-atencionsismo").status_code == 200


def test_shape_and_np_join(client):
    body = client.get("/stickers-atencionsismo").json()
    assert body["ok"] is True and body["fuente"] == "atencionsismo" and body["degraded"] is False
    by_id = {e["id"]: e for e in body["evaluaciones"]}
    assert by_id["ev-1"]["inspector"]["np"] == "P4"
    assert by_id["ev-1"]["clasificacion"] == "INSEGURO"
    assert by_id["ev-2"]["inspector"]["np"] == ""
    assert by_id["ev-2"]["origen"] == "sistema"


def test_api_failure_serves_stale(client, monkeypatch):
    assert client.get("/stickers-atencionsismo").status_code == 200

    async def boom(*a, **kw):
        raise atencionsismo.ApiUnavailableError("down")

    monkeypatch.setattr(atencionsismo, "fetch_stickers", boom)
    client.app.state.stickers_atencionsismo_cache._at = 0  # force TTL expiry
    body = client.get("/stickers-atencionsismo").json()
    assert len(body["evaluaciones"]) == 2 and body["degraded"] is False


def test_api_failure_cold_start_without_blob_is_503(client, monkeypatch):
    async def boom(*a, **kw):
        raise atencionsismo.ApiUnavailableError("down")

    monkeypatch.setattr(atencionsismo, "fetch_stickers", boom)
    monkeypatch.setattr(router_mod.blob_lkg, "load_json", lambda *a: None)
    assert client.get("/stickers-atencionsismo").status_code == 503


def test_missing_password_is_503(client, monkeypatch):
    def no_creds():
        raise atencionsismo.ApiCredentialsError("VISITADOS_API_PASS is not set")

    monkeypatch.setattr(atencionsismo, "credentials_from_env", no_creds)
    resp = client.get("/stickers-atencionsismo")
    assert resp.status_code == 503 and "VISITADOS_API_PASS" in resp.json()["detail"]


def test_redaction_blanks_persona_and_np():
    payload = [{"id": "1", "fuente": "atencionsismo", "origen": "firebase", "color_etiqueta": "Habitable",
                "codigo_edificacion": "c", "consecutivo": 1, "municipio": "76001", "area": "1", "area_nombre": "",
                "clasificacion": "INSPECCIONADA", "alcance": "", "coords": {"lat": 1, "lng": 2, "accuracy": None},
                "restricciones": "", "acciones_posteriores": {"barricadas": False, "evaluacion_detallada": False},
                "fecha": None, "descripcion": {"nombre": "Juan", "direccion": "Calle 1"},
                "inspector": {"uid": "u", "codigo": "004", "nombre_completo": "Ana", "identificacion": "1",
                              "entidad": "E", "np": "P4"}, "comentarios": "c", "fotos": ["x"]}]
    out = router_mod.redact_for_blob(payload)[0]
    assert out["descripcion"] == {"nombre": "", "direccion": "Calle 1"}
    assert out["inspector"] == {"uid": "u", "codigo": "004", "entidad": "E", "nombre_completo": "", "identificacion": "", "np": ""}
    assert out["comentarios"] == "" and out["fotos"] == []
    assert out["fuente"] == "atencionsismo" and out["origen"] == "firebase" and out["color_etiqueta"] == "Habitable"
    assert payload[0]["descripcion"]["nombre"] == "Juan"  # never mutates input
    # A2: exact allowlist key set — no field silently added or dropped.
    assert set(out) == set(router_mod._BLOB_ALLOWED_FIELDS) | {"descripcion", "inspector", "comentarios", "fotos"}


# ── A1: evaluaciones cache degraded -> build_payload fails completo, so the
# stickers cache runs its OWN serve-stale/Blob-restore chain (design D4:
# "si Firestore falla, el fetch falla completo") ────────────────────────


def _degrade_evaluaciones_cache(client) -> None:
    """Force the evaluaciones cache into the same state a Blob-restored
    cold start leaves it: a payload present, fresh (not TTL-stale), but
    `degraded=True` (blanked NP -> no Fase)."""
    cache = client.app.state.stickers_evaluaciones_cache
    cache._payload = [{"codigo_edificacion": "old-fs"}]
    cache._at = time.monotonic()
    cache._degraded = True


def test_evaluaciones_degraded_restores_stickers_cache_from_its_own_blob(client, monkeypatch):
    _degrade_evaluaciones_cache(client)

    old_row = {"id": "old", "fuente": "atencionsismo", "origen": "firebase", "color_etiqueta": "",
               "codigo_edificacion": "c", "consecutivo": 1, "municipio": "76001", "area": "1",
               "area_nombre": "", "clasificacion": "", "alcance": "", "coords": None,
               "restricciones": "", "acciones_posteriores": {"barricadas": False, "evaluacion_detallada": False},
               "fecha": None, "descripcion": {"nombre": "", "direccion": ""},
               "inspector": {"uid": "", "codigo": "", "nombre_completo": "", "identificacion": "",
                             "entidad": "", "np": ""}, "comentarios": "", "fotos": []}

    def fake_load(pathname, expected_type):
        if pathname == router_mod.STICKERS_LKG_BLOB:
            return [old_row]
        return None

    monkeypatch.setattr(router_mod.blob_lkg, "load_json", fake_load)

    resp = client.get("/stickers-atencionsismo")

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["evaluaciones"] == [old_row]


def test_evaluaciones_degraded_and_no_stickers_blob_backup_is_503(client, monkeypatch):
    _degrade_evaluaciones_cache(client)
    monkeypatch.setattr(router_mod.blob_lkg, "load_json", lambda *a: None)

    resp = client.get("/stickers-atencionsismo")

    assert resp.status_code == 503


def test_evaluaciones_degraded_never_persists_the_degraded_derived_payload(client, monkeypatch):
    _degrade_evaluaciones_cache(client)
    saved_paths: list[str] = []
    monkeypatch.setattr(router_mod.blob_lkg, "save_json",
                        lambda pathname, payload: saved_paths.append(pathname) or True)
    monkeypatch.setattr(router_mod.blob_lkg, "load_json", lambda *a: None)

    client.get("/stickers-atencionsismo")

    assert router_mod.STICKERS_LKG_BLOB not in saved_paths
