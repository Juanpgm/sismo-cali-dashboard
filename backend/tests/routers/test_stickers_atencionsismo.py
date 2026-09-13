"""GET /stickers-atencionsismo (RED first) — design D4. Same fixture
conventions as test_stickers.py: TestClient(create_app()), auth via
dependency override of current_claims, in-memory Firestore fake, and the
atencionsismo client monkeypatched wholesale (no network).

No `fake_sismo` fixture exists in test_stickers.py (plan's fallback
instruction): this file reuses that module's `_app`/`_FakeAuth` helpers —
the SAME Firestore fake `_app` already wires through `credentials.sismo` —
instead of creating a second fake."""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.main import create_app
from app.routers import stickers
from app.routers import stickers_atencionsismo as router_mod
from app.services import atencionsismo

# Reuse the fake Firestore/Auth/claims fixtures from test_stickers.py by
# importing them (pytest collects fixtures from imported modules only when
# re-exported here):
from tests.routers.test_stickers import (  # noqa: F401
    FAKE_CLAIMS_ADMIN, FAKE_CLAIMS_INSTITUCIONAL, FAKE_CLAIMS_VIEWER, _app, _FakeAuth, _FakeFirestore,
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
    stores = {
        "inspectores": {
            "u1": {
                "codigo": "004",
                "NP": "P4",
                "nombre_completo": "Ana Gomez",
                "identificacion": "123",
                "entidad": "Curaduria 1",
            }
        },
        "evaluaciones": {},
    }
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


def test_no_match_sticker_completes_full_identity_from_roster_end_to_end(client):
    # Extension (2026-09-08): no matching Firestore evaluación for ev-1's
    # code -> nombre_completo/identificacion/entidad/uid come from the
    # seeded `inspectores` roster doc, not just np.
    body = client.get("/stickers-atencionsismo").json()
    by_id = {e["id"]: e for e in body["evaluaciones"]}
    assert by_id["ev-1"]["inspector"] == {
        "uid": "u1",
        "codigo": "004",
        "nombre_completo": "Ana Gomez",
        "identificacion": "123",
        "entidad": "Curaduria 1",
        "np": "P4",
        "tarjeta_profesional": "",
        "num_telefono": "",
        "correo_contacto": "",
    }


def test_matched_sticker_uses_evaluacion_identity_not_roster(monkeypatch):
    # Critical regression guard end-to-end: a MATCHED sticker's name comes
    # from the evaluación even when a differently-named roster doc shares
    # its brigade code.
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def fake_fetch(client, user, password, **kw):
        return list(ROWS)

    monkeypatch.setattr(atencionsismo, "fetch_stickers", fake_fetch)
    fake_auth = _FakeAuth()
    stores = {
        "inspectores": {
            # NP matches the evaluación's own NP on purpose: `list_evaluaciones`
            # already joins `inspector.np` by uid independently of this
            # change (pre-existing D1 step 1), so keeping both NPs equal
            # isolates THIS test to the field this task actually touches —
            # nombre_completo/identificacion/entidad must never come from
            # here once a Firestore evaluación has matched.
            "u1": {
                "codigo": "004",
                "NP": "P4",
                "nombre_completo": "Roster Name",
                "identificacion": "999",
                "entidad": "Otra Entidad",
            }
        },
        "evaluaciones": {
            "fs-1": {
                "codigo_edificacion": "76001-1-0040001",
                "inspector": {
                    "uid": "u1",
                    "codigo": "004",
                    "nombre_completo": "Eval Name",
                    "identificacion": "1",
                    "entidad": "E",
                    "np": "P4",
                },
            }
        },
    }
    app = _app(monkeypatch, fake_auth, stores)
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN
    body = TestClient(app).get("/stickers-atencionsismo").json()
    by_id = {e["id"]: e for e in body["evaluaciones"]}
    assert by_id["ev-1"]["inspector"] == {
        "uid": "u1",
        "codigo": "004",
        "nombre_completo": "Eval Name",
        "identificacion": "1",
        "entidad": "E",
        "np": "P4",
        "tarjeta_profesional": "",
        "num_telefono": "",
        "correo_contacto": "",
    }


def test_build_payload_passes_cedula_roster_from_new_helper(monkeypatch):
    # Contrato v3 (2026-09-08): build_payload must join `profesional.cedula`
    # against the roster-by-cedula map returned by the NEW
    # `stickers.inspector_profiles` single-scan helper (patched here, per
    # the F6 single-read contract), not the two old separate helpers, for a
    # row with no matching Firestore evaluación.
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def fake_fetch(client, user, password, **kw):
        return [{"id": "ev-1", "numero": "76001001-999-0001", "origen": "sistema",
                 "profesional": {"cedula": "999", "nombre": "Juan Perez", "rango": "P2"}}]

    monkeypatch.setattr(atencionsismo, "fetch_stickers", fake_fetch)
    monkeypatch.setattr(
        stickers, "inspector_profiles",
        lambda db: ({}, {"999": {"uid": "u9", "entidad": "E9", "nombre_completo": "", "np": ""}}),
    )
    monkeypatch.setattr(stickers, "list_evaluaciones", lambda db: [])

    class _FakeEvalCache:
        degraded = False

        def get_or_fetch(self, fn):
            return fn()

    payload = router_mod.build_payload(db=object(), evaluaciones_cache=_FakeEvalCache())

    assert payload[0]["inspector"]["uid"] == "u9"
    assert payload[0]["inspector"]["entidad"] == "E9"
    assert payload[0]["inspector_fuente"] == "api"


def test_build_payload_reads_inspectores_collection_exactly_once(monkeypatch):
    # F6: build_payload must call `inspector_profiles` exactly once —
    # calling the two old wrapper helpers separately meant TWO Firestore
    # scans of the same 'inspectores' collection for one request.
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def fake_fetch(client, user, password, **kw):
        return []

    monkeypatch.setattr(atencionsismo, "fetch_stickers", fake_fetch)
    monkeypatch.setattr(stickers, "list_evaluaciones", lambda db: [])

    class _FakeEvalCache:
        degraded = False

        def get_or_fetch(self, fn):
            return fn()

    inner = _FakeFirestore({"inspectores": {}})
    calls = {"n": 0}
    real_collection = inner.collection

    def counting_collection(name):
        col = real_collection(name)
        if name == "inspectores":
            orig_get = col.get

            def counted_get():
                calls["n"] += 1
                return orig_get()

            col.get = counted_get
        return col

    inner.collection = counting_collection

    router_mod.build_payload(db=inner, evaluaciones_cache=_FakeEvalCache())

    assert calls["n"] == 1


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
    monkeypatch.setattr(stickers.blob_lkg, "load_json", lambda *a: None)
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
    assert out["inspector"] == {"uid": "u", "codigo": "004", "entidad": "E", "nombre_completo": "", "identificacion": "", "np": "",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}
    assert out["comentarios"] == "" and out["fotos"] == []
    assert out["fuente"] == "atencionsismo" and out["origen"] == "firebase" and out["color_etiqueta"] == "Habitable"
    assert payload[0]["descripcion"]["nombre"] == "Juan"  # never mutates input
    # A2: exact allowlist key set — no field silently added or dropped.
    assert set(out) == set(router_mod._BLOB_ALLOWED_FIELDS) | {"descripcion", "inspector", "comentarios", "fotos"}


def test_redaction_keeps_fase_it_is_a_bare_1_2_none_not_pii():
    # fase (contrato v3, 2026-09-08) is a bare 1|2|None value — the Stickers
    # tab's own Fase signal (API developer confirmation: 1/2 = Fase I/II by
    # atencionsismo's process), not PII — it must survive the public Blob
    # redaction like inspector_fuente, while np/nombre/identificacion stay
    # blanked.
    payload = [{"id": "1", "fuente": "atencionsismo", "origen": "firebase", "color_etiqueta": "Habitable",
                "codigo_edificacion": "c", "consecutivo": 1, "municipio": "76001", "area": "1", "area_nombre": "",
                "clasificacion": "INSPECCIONADA", "alcance": "", "coords": {"lat": 1, "lng": 2, "accuracy": None},
                "restricciones": "", "acciones_posteriores": {"barricadas": False, "evaluacion_detallada": False},
                "fecha": None, "descripcion": {"nombre": "Juan", "direccion": "Calle 1"},
                "fase": 2,
                "inspector": {"uid": "u", "codigo": "004", "nombre_completo": "Ana", "identificacion": "1",
                              "entidad": "E", "np": "P4"}, "comentarios": "c", "fotos": ["x"]}]
    out = router_mod.redact_for_blob(payload)[0]
    assert out["fase"] == 2
    assert out["inspector"]["np"] == "" and out["inspector"]["nombre_completo"] == "" and out["inspector"]["identificacion"] == ""


def test_redaction_keeps_inspector_fuente_it_is_not_personally_identifying():
    # inspector_fuente is a bare "evaluacion"|"api"|"roster"|"" enum, not PII — it
    # must survive the public Blob redaction (misattribution-risk fix
    # 2026-09-08) instead of being silently dropped by the allowlist.
    payload = [{"id": "1", "fuente": "atencionsismo", "origen": "firebase", "color_etiqueta": "Habitable",
                "codigo_edificacion": "c", "consecutivo": 1, "municipio": "76001", "area": "1", "area_nombre": "",
                "clasificacion": "INSPECCIONADA", "alcance": "", "coords": {"lat": 1, "lng": 2, "accuracy": None},
                "restricciones": "", "acciones_posteriores": {"barricadas": False, "evaluacion_detallada": False},
                "fecha": None, "descripcion": {"nombre": "Juan", "direccion": "Calle 1"},
                "inspector_fuente": "roster",
                "inspector": {"uid": "u", "codigo": "004", "nombre_completo": "Ana", "identificacion": "1",
                              "entidad": "E", "np": "P4"}, "comentarios": "c", "fotos": ["x"]}]
    out = router_mod.redact_for_blob(payload)[0]
    assert out["inspector_fuente"] == "roster"


# ── W3 (plan cozy-wobbling-dragonfly): redaction keeps fecha_fuente,
# barrio_reportado, comuna_reportada (not PII), and blanks contact fields
# WITH the keys still present (never removed). ──────────────────────────


def _blob_payload_row(**over) -> dict:
    base = {"id": "1", "fuente": "atencionsismo", "origen": "firebase", "color_etiqueta": "Habitable",
            "codigo_edificacion": "c", "consecutivo": 1, "municipio": "76001", "area": "1", "area_nombre": "",
            "clasificacion": "INSPECCIONADA", "alcance": "", "coords": {"lat": 1, "lng": 2, "accuracy": None},
            "restricciones": "", "acciones_posteriores": {"barricadas": False, "evaluacion_detallada": False},
            "fecha": "2026-08-18T18:33:00+00:00", "fecha_fuente": "api",
            "barrio_reportado": "San Antonio", "comuna_reportada": "Comuna 3",
            "descripcion": {"nombre": "Juan", "direccion": "Calle 1"},
            "inspector": {"uid": "u", "codigo": "004", "nombre_completo": "Ana", "identificacion": "1",
                          "entidad": "E", "np": "P4", "tarjeta_profesional": "TP-1",
                          "num_telefono": "3001234567", "correo_contacto": "ana@x.co"},
            "comentarios": "c", "fotos": ["x"]}
    base.update(over)
    return base


def test_redaction_keeps_fecha_fuente_barrio_and_comuna():
    out = router_mod.redact_for_blob([_blob_payload_row()])[0]
    assert out["fecha"] == "2026-08-18T18:33:00+00:00"
    assert out["fecha_fuente"] == "api"
    assert out["barrio_reportado"] == "San Antonio"
    assert out["comuna_reportada"] == "Comuna 3"


def test_redaction_blanks_contact_fields_but_keeps_the_keys():
    out = router_mod.redact_for_blob([_blob_payload_row()])[0]
    assert out["inspector"]["tarjeta_profesional"] == ""
    assert out["inspector"]["num_telefono"] == ""
    assert out["inspector"]["correo_contacto"] == ""
    for key in ("tarjeta_profesional", "num_telefono", "correo_contacto"):
        assert key in out["inspector"]


def test_redaction_never_mutates_the_input_contact_fields():
    payload = [_blob_payload_row()]
    router_mod.redact_for_blob(payload)
    assert payload[0]["inspector"]["tarjeta_profesional"] == "TP-1"
    assert payload[0]["inspector"]["num_telefono"] == "3001234567"
    assert payload[0]["inspector"]["correo_contacto"] == "ana@x.co"


def test_redaction_unknown_field_never_leaks():
    out = router_mod.redact_for_blob([_blob_payload_row(campo_desconocido="x")])[0]
    assert "campo_desconocido" not in out


def test_redaction_degraded_payload_has_no_identity_or_contact_end_to_end(client, monkeypatch):
    _degrade_evaluaciones_cache(client)
    old_row = {"id": "old", "fuente": "atencionsismo", "origen": "firebase", "color_etiqueta": "",
               "codigo_edificacion": "c", "consecutivo": 1, "municipio": "76001", "area": "1",
               "area_nombre": "", "clasificacion": "", "alcance": "", "coords": None,
               "restricciones": "", "acciones_posteriores": {"barricadas": False, "evaluacion_detallada": False},
               "fecha": None, "fecha_fuente": "no_aplica", "barrio_reportado": "", "comuna_reportada": "",
               "descripcion": {"nombre": "", "direccion": ""},
               "inspector": {"uid": "", "codigo": "", "nombre_completo": "", "identificacion": "",
                             "entidad": "", "np": "", "tarjeta_profesional": "", "num_telefono": "",
                             "correo_contacto": ""},
               "comentarios": "", "fotos": []}

    def fake_load(pathname, expected_type):
        if pathname == router_mod.STICKERS_LKG_BLOB:
            return [old_row]
        return None

    monkeypatch.setattr(stickers.blob_lkg, "load_json", fake_load)

    resp = client.get("/stickers-atencionsismo")

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    row = body["evaluaciones"][0]
    assert row["inspector"]["identificacion"] == ""
    assert row["inspector"]["tarjeta_profesional"] == ""
    assert row["inspector"]["num_telefono"] == ""
    assert row["inspector"]["correo_contacto"] == ""


def test_redaction_degraded_pre_rollout_blob_missing_new_keys_serves_verbatim_no_keyerror(client, monkeypatch):
    # M7 (adversarial review 2026-09-12): `test_redaction_degraded_payload_
    # has_no_identity_or_contact_end_to_end` above already has ALL the new
    # W3 keys on its `old_row` fixture, so it never exercised the real
    # rollout hazard — a Blob LKG copy written by a PRE-deploy process,
    # before `fecha_fuente`/`barrio_reportado`/`comuna_reportada`/the
    # contact fields existed at all. The route must still return 200 with
    # `degraded: true` and serve that old shape verbatim (no KeyError) —
    # it is served as-is, never re-normalized.
    _degrade_evaluaciones_cache(client)
    old_row_pre_rollout = {
        "id": "old", "fuente": "atencionsismo", "origen": "firebase", "color_etiqueta": "",
        "codigo_edificacion": "c", "consecutivo": 1, "municipio": "76001", "area": "1",
        "area_nombre": "", "clasificacion": "", "alcance": "", "coords": None,
        "restricciones": "", "acciones_posteriores": {"barricadas": False, "evaluacion_detallada": False},
        "fecha": None,
        # NOTE: no fecha_fuente/barrio_reportado/comuna_reportada — the
        # pre-rollout shape.
        "descripcion": {"nombre": "", "direccion": ""},
        "inspector": {"uid": "", "codigo": "", "nombre_completo": "", "identificacion": "",
                      "entidad": "", "np": ""},
        # NOTE: no tarjeta_profesional/num_telefono/correo_contacto either.
        "comentarios": "", "fotos": [],
    }

    def fake_load(pathname, expected_type):
        if pathname == router_mod.STICKERS_LKG_BLOB:
            return [old_row_pre_rollout]
        return None

    monkeypatch.setattr(stickers.blob_lkg, "load_json", fake_load)

    resp = client.get("/stickers-atencionsismo")

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["evaluaciones"] == [old_row_pre_rollout]  # served verbatim, untouched


def test_blob_allowed_fields_extended_with_fecha_fuente_barrio_comuna():
    for field in ("fecha_fuente", "barrio_reportado", "comuna_reportada"):
        assert field in router_mod._BLOB_ALLOWED_FIELDS


def test_stickers_blob_allowed_inspector_untouched():
    # W3 hard rule: never touch stickers._BLOB_ALLOWED_INSPECTOR — it is
    # shared with /evaluaciones.
    assert stickers._BLOB_ALLOWED_INSPECTOR == ("uid", "codigo", "entidad")


def test_redact_for_blob_3000_rows_size_canary_under_4mb():
    # L2 (adversarial review 2026-09-12): the OLD synthetic rows here were
    # minimal (short/blank strings), so the canary measured a best-case
    # that never reflected the headroom a REAL payload actually has —
    # `direccion`/`restricciones`/`alcance` all survive redaction (see
    # `_BLOB_ALLOWED_FIELDS`), so their real-world length matters for this
    # budget. Realistic-length strings here, 4 MB threshold unchanged.
    import json

    payload = [
        _blob_payload_row(
            id=str(i),
            descripcion={"nombre": "Juan Perez", "direccion": "Carrera 10 # 25-30 Bis, Barrio San Antonio, Cali"},
            restricciones="No ingresar sin acompañamiento de la brigada de emergencias",
            alcance="Inspección visual exterior e interior completa de la edificación",
            comentarios="Grietas visibles en fachada norte, se recomienda revisar el sistema de drenaje",
        )
        for i in range(3000)
    ]
    out = router_mod.redact_for_blob(payload)
    size = len(json.dumps(out).encode("utf-8"))
    assert size < 4_000_000, f"redacted payload is {size} bytes, budget is 4_000_000"


def test_redact_for_blob_is_a_pure_projection_no_logging(caplog):
    # H1 (adversarial review 2026-09-10): `redact_for_blob` must never log
    # or serialize on its own — it used to run its own `json.dumps(out)`
    # (WITHOUT `default=str`) just to log a byte count, which raised
    # TypeError whenever `fecha` carried a raw `datetime` (see
    # test_datetime_fecha_survives_redact_and_persist_without_raising
    # below). Size visibility now lives at the Blob-upload decision point
    # (`EvaluacionesCache._persist_last_good`), not here.
    import logging

    with caplog.at_level(logging.INFO):
        router_mod.redact_for_blob([_blob_payload_row()])
    assert caplog.records == []


def test_datetime_fecha_survives_redact_and_persist_without_raising(monkeypatch):
    # H1 regression: `stickers.list_evaluaciones` (~line 581) can hand back
    # a raw `fecha_hora_dispositivo` `datetime` as `fecha`. The OLD
    # `redact_for_blob` ran a bare `json.dumps(out)` (no `default=str`) just
    # to log a byte count, which raised TypeError on that datetime. That
    # TypeError propagated out of `_redact`/`_persist_last_good` into
    # `get_or_fetch`'s `except Exception`, mis-logged as a fetch failure,
    # armed the failure backoff, and the LKG Blob write never happened.
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "t")
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        stickers.blob_lkg, "save_json",
        lambda path, payload: saved.update({path: payload}) or True,
    )

    cache = stickers.EvaluacionesCache(lkg_blob=router_mod.STICKERS_LKG_BLOB, redact=router_mod.redact_for_blob)
    row = _blob_payload_row(fecha=datetime(2026, 8, 18, 18, 33))

    result = cache.get_or_fetch(lambda: [row])

    assert result == [row]  # served normally, not swallowed as a fetch failure
    assert cache._failed_at is None  # no backoff armed
    cache._persist_thread.join(timeout=2)
    assert router_mod.STICKERS_LKG_BLOB in saved  # LKG save was actually invoked


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

    monkeypatch.setattr(stickers.blob_lkg, "load_json", fake_load)

    resp = client.get("/stickers-atencionsismo")

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["evaluaciones"] == [old_row]


def test_evaluaciones_degraded_and_no_stickers_blob_backup_is_503(client, monkeypatch):
    _degrade_evaluaciones_cache(client)
    monkeypatch.setattr(stickers.blob_lkg, "load_json", lambda *a: None)

    resp = client.get("/stickers-atencionsismo")

    assert resp.status_code == 503


def test_evaluaciones_degraded_never_persists_the_degraded_derived_payload(client, monkeypatch):
    _degrade_evaluaciones_cache(client)
    saved_paths: list[str] = []
    monkeypatch.setattr(stickers.blob_lkg, "save_json",
                        lambda pathname, payload: saved_paths.append(pathname) or True)
    monkeypatch.setattr(stickers.blob_lkg, "load_json", lambda *a: None)

    client.get("/stickers-atencionsismo")

    assert router_mod.STICKERS_LKG_BLOB not in saved_paths


# ── B3: a hung upstream must not hang the route forever ────────────────


def test_slow_fetch_stickers_times_out_and_is_503_cold(client, monkeypatch):
    monkeypatch.setattr(router_mod, "STICKERS_FETCH_DEADLINE_S", 0.05)

    async def slow_fetch(client, user, password, **kw):
        await asyncio.sleep(0.3)
        return list(ROWS)

    monkeypatch.setattr(atencionsismo, "fetch_stickers", slow_fetch)

    resp = client.get("/stickers-atencionsismo")

    assert resp.status_code == 503


# ── Perf (2026-09-10): build_payload runs the upstream walk and the two
# Firestore reads concurrently. These tests pin down (a) that they actually
# overlap in wall-clock time and (b) that every failure combination still
# raises/serves EXACTLY what the old sequential code did — captured against
# today's behavior before the refactor, kept green after it. ─────────────


class _SlowFakeEvalCache:
    """Same shape as stickers.EvaluacionesCache's public surface
    (get_or_fetch + degraded) but with a controllable artificial delay, so a
    test can prove the Firestore read and the upstream walk overlap instead
    of running back to back."""

    def __init__(self, delay_s: float = 0.0, degraded: bool = False):
        self._delay_s = delay_s
        self.degraded = degraded
        self.calls = 0

    def get_or_fetch(self, fetch):
        self.calls += 1
        time.sleep(self._delay_s)
        return fetch()


def test_build_payload_runs_upstream_and_firestore_concurrently(monkeypatch):
    # Deterministic proof of concurrency (F2, adversarial review
    # 2026-09-10) -- NOT a wall-clock race. A `threading.Barrier(2)` that
    # BOTH to_thread-wrapped stubs (roster + the evaluaciones-cache read)
    # must reach only unblocks if they are actually running on separate
    # threads AT THE SAME TIME: a sequential/blocking implementation would
    # leave the second stub waiting alone and the barrier would time out
    # and raise `BrokenBarrierError`, failing the test outright. The async
    # `pull` leg additionally only returns once it has observed (via a
    # plain `threading.Event`, checked through a bounded, non-blocking
    # poll) that BOTH threads passed the barrier -- proving `pull` was
    # scheduled concurrently with the thread tasks too, not run to
    # completion before they even started. The `asyncio.Event` records that
    # handshake on the coroutine side for the final assertion.
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    barrier = threading.Barrier(2, timeout=2.0)
    threads_passed_barrier = threading.Event()
    pull_scheduled = asyncio.Event()

    def blocking_inspector_profiles(db):
        barrier.wait()
        threads_passed_barrier.set()
        return ({}, {})

    class _BarrierEvalCache:
        degraded = False
        calls = 0

        def get_or_fetch(self, fetch):
            self.calls += 1
            barrier.wait()
            threads_passed_barrier.set()
            return fetch()

    async def concurrent_fetch(client, user, password, **kw):
        pull_scheduled.set()
        # Bounded poll (a safety-net timeout, not a correctness assertion —
        # the correctness proof is the Barrier above): if the thread tasks
        # were never started concurrently with `pull`, they never reach the
        # barrier and this loop exhausts and fails loudly instead of
        # hanging.
        for _ in range(400):
            if threads_passed_barrier.is_set():
                return []
            await asyncio.sleep(0.005)
        raise AssertionError("pull never observed the thread tasks reach the barrier concurrently")

    monkeypatch.setattr(atencionsismo, "fetch_stickers", concurrent_fetch)
    monkeypatch.setattr(stickers, "inspector_profiles", blocking_inspector_profiles)
    monkeypatch.setattr(stickers, "list_evaluaciones", lambda db: [])

    cache = _BarrierEvalCache()
    payload = router_mod.build_payload(db=object(), evaluaciones_cache=cache)

    assert payload == []
    assert pull_scheduled.is_set()
    assert cache.calls == 1


def test_upstream_failure_takes_priority_over_independent_firestore_failure(monkeypatch):
    # Old sequential code: the walk ran FIRST, so an upstream failure meant
    # the Firestore reads were never even attempted — only the upstream
    # error ever surfaced. New concurrent code must reproduce that exact
    # priority even though the Firestore read is independently also
    # failing: the caller (the route) should still see the upstream's
    # ApiUnavailableError -> 503, never a Firestore-error-shaped 502.
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def boom_fetch(client, user, password, **kw):
        raise atencionsismo.ApiUnavailableError("upstream down")

    monkeypatch.setattr(atencionsismo, "fetch_stickers", boom_fetch)
    monkeypatch.setattr(stickers, "inspector_profiles", lambda db: (_ for _ in ()).throw(RuntimeError("roster boom")))
    monkeypatch.setattr(stickers, "list_evaluaciones", lambda db: [])

    cache = _SlowFakeEvalCache()
    with pytest.raises(atencionsismo.ApiUnavailableError):
        router_mod.build_payload(db=object(), evaluaciones_cache=cache)


def test_upstream_failure_never_waits_for_or_consumes_slow_firestore_reads(monkeypatch):
    # F1 fail-fast (adversarial review, 2026-09-10): on a pull failure,
    # build_payload must behave like the OLD sequential code, which never
    # even reached the two Firestore reads. Both stubs block on a
    # `threading.Event` that this test only releases in `finally`, AFTER
    # the assertions below already ran -- so if build_payload incorrectly
    # awaited either result, this test would hang (up to the generous
    # per-wait timeout) instead of returning immediately, and the
    # "never consumed" assertion would already have been proven false by
    # the time control got here.
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def boom_fetch(client, user, password, **kw):
        raise atencionsismo.ApiUnavailableError("upstream down", status=503)

    monkeypatch.setattr(atencionsismo, "fetch_stickers", boom_fetch)

    roster_event = threading.Event()
    firestore_event = threading.Event()
    firestore_fetch_called = {"value": False}

    def blocking_inspector_profiles(db):
        roster_event.wait(timeout=3)
        return ({}, {})

    class _BlockingEvalCache:
        degraded = False

        def get_or_fetch(self, fetch):
            firestore_event.wait(timeout=3)
            # Only reached once the event is released (in `finally`,
            # after every assertion below already ran) -- proves
            # build_payload's failure path never waited for, and never
            # consumed, this result.
            firestore_fetch_called["value"] = True
            return fetch()

    monkeypatch.setattr(stickers, "inspector_profiles", blocking_inspector_profiles)

    try:
        with pytest.raises(atencionsismo.ApiUnavailableError) as excinfo:
            router_mod.build_payload(db=object(), evaluaciones_cache=_BlockingEvalCache())
        # Same error/status as before the fix: a bare upstream failure
        # still surfaces as the exact ApiUnavailableError pull raised.
        assert str(excinfo.value) == "upstream down"
        assert excinfo.value.status == 503
        # Reaching this line proves get_or_fetch's blocking call was never
        # awaited/consumed by build_payload -- it is still parked on
        # firestore_event.wait() right now.
        assert firestore_fetch_called["value"] is False
    finally:
        roster_event.set()
        firestore_event.set()


def test_upstream_failure_of_an_unexpected_exception_type_still_cancels_firestore_reads(monkeypatch):
    # Edge case: the fail-fast cancellation must not be special-cased to
    # the atencionsismo.Api*Error family -- ANY exception raised by the
    # pull leg (including one none of the four known types) must still
    # short-circuit before the Firestore reads are consumed.
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def boom_fetch(client, user, password, **kw):
        raise ValueError("unexpected upstream shape")

    monkeypatch.setattr(atencionsismo, "fetch_stickers", boom_fetch)

    firestore_event = threading.Event()
    firestore_fetch_called = {"value": False}

    monkeypatch.setattr(stickers, "inspector_profiles", lambda db: ({}, {}))

    class _BlockingEvalCache:
        degraded = False

        def get_or_fetch(self, fetch):
            firestore_event.wait(timeout=3)
            firestore_fetch_called["value"] = True
            return fetch()

    try:
        with pytest.raises(ValueError, match="unexpected upstream shape"):
            router_mod.build_payload(db=object(), evaluaciones_cache=_BlockingEvalCache())
        assert firestore_fetch_called["value"] is False
    finally:
        firestore_event.set()


def test_roster_read_failure_surfaces_uncaught_like_before(monkeypatch):
    # Today: build_payload does not wrap `stickers.inspector_profiles` in
    # any try/except, so a Firestore error there propagates straight to the
    # route's generic `except Exception` -> 502 handler. Must still be true
    # with the concurrent reads.
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def fake_fetch(client, user, password, **kw):
        return []

    monkeypatch.setattr(atencionsismo, "fetch_stickers", fake_fetch)
    monkeypatch.setattr(stickers, "inspector_profiles", lambda db: (_ for _ in ()).throw(RuntimeError("roster boom")))
    monkeypatch.setattr(stickers, "list_evaluaciones", lambda db: [])

    cache = _SlowFakeEvalCache()
    with pytest.raises(RuntimeError, match="roster boom"):
        router_mod.build_payload(db=object(), evaluaciones_cache=cache)


def test_firestore_evaluaciones_read_failure_surfaces_uncaught_like_before(monkeypatch):
    # Same as above, for the OTHER independent Firestore read
    # (evaluaciones_cache.get_or_fetch) — its own internal serve-stale
    # handling only shields a bare "the underlying fetch failed" from
    # raising when it already has a cached payload or a Blob backup; a
    # cache with NEITHER still raises straight through, exactly like today.
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def fake_fetch(client, user, password, **kw):
        return []

    monkeypatch.setattr(atencionsismo, "fetch_stickers", fake_fetch)
    monkeypatch.setattr(stickers, "inspector_profiles", lambda db: ({}, {}))

    class _BoomEvalCache:
        degraded = False

        def get_or_fetch(self, fetch):
            raise RuntimeError("firestore evaluaciones boom")

    with pytest.raises(RuntimeError, match="firestore evaluaciones boom"):
        router_mod.build_payload(db=object(), evaluaciones_cache=_BoomEvalCache())


def test_both_upstream_and_firestore_succeed_builds_payload_as_before(monkeypatch):
    # Full success path with the concurrent reads still composes the exact
    # same payload build_evaluaciones would have produced sequentially.
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def fake_fetch(client, user, password, **kw):
        return list(ROWS)

    monkeypatch.setattr(atencionsismo, "fetch_stickers", fake_fetch)
    monkeypatch.setattr(
        stickers, "inspector_profiles",
        lambda db: ({"004": {"uid": "u1", "codigo": "004", "np": "P4",
                              "nombre_completo": "Ana Gomez", "identificacion": "123", "entidad": "Curaduria 1"}}, {}),
    )
    monkeypatch.setattr(stickers, "list_evaluaciones", lambda db: [])

    cache = _SlowFakeEvalCache()
    payload = router_mod.build_payload(db=object(), evaluaciones_cache=cache)

    by_id = {e["id"]: e for e in payload}
    assert by_id["ev-1"]["inspector"]["np"] == "P4"
    assert cache.calls == 1
