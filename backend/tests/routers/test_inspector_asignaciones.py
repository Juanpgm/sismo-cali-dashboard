"""POST /inspector-asignaciones (RED first) — design.md ADR-3/ADR-9;
backend-platform spec "Own-uid-scoped route rejects cross-uid access",
"sticker_matches And cuadrillas Sole-Writer Invariant"; field-form-session
spec "Inspector Own-UID Scoping Preserved" ("Cross-inspector access still
rejected after migration", "Own-uid access still succeeds after
migration").

Ports `api/inspector-asignaciones.js`'s misPuntos/marcarHecho dispatch.
Unlike sticker-asignaciones (admin-only, not yet ported), this endpoint is
ANY-authenticated (`Depends(require_auth)`) but scopes every sticker_matches
read/write to the caller's OWN uid (`inspector_uid == token.sub`) — no
Firestore rules back this collection, so this scoping is the only thing
standing between one inspector's data and another's.

`planeacion-asignaciones` follow-up batch (2026-08-26): the same router
gains `misPuntosPlaneacion`/`marcarHechoPlaneacion`, the inspector-facing
counterpart to the admin-only `planeacion_puntos` collection
(`routers/planeacion_asignaciones.py`). Same own-uid-scoping discipline,
same collection, different literal — see the new tests below.

`grupos-inspectores` change (2026-08-26): groups of INSPECTORS (not to be
confused with `planeacion_cuadrillas`, which groups POINTS under one
inspector). A point can now carry an optional `grupo_id`; a caller's
visible set is own points UNION every point assigned to a group they
belong to (two independent queries merged in Python — Firestore cannot OR
across fields in one query), and the own-uid write guard widens to "own uid
OR member of the point's group". See the new tests below for both the
widened read set and the widened (and negatively-tested) write guard.

Uses a call-count-instrumented fake `credentials.sismo()` override (no real
service-account JSON, no network), same convention `test_sticker_status.py`
established. The fake Firestore is a tiny in-memory dict keyed by doc id,
supporting `.collection(name).where(field, op, value).get()` (`==`,
`array_contains`, `in` — the last capped at 30 values per query, mirroring
real Firestore's own limit, so a chunking bug fails loudly instead of
silently truncating) and `.collection(name).document(id).get()/.set(data,
merge=True)`. It now backs THREE independent stores (`sticker_matches`,
`planeacion_puntos`, `grupos_inspectores`) keyed by collection name, so a
query against one can never see another's docs.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.credentials import clients as credentials
from app.integracion.coords import haversine_m
from app.main import create_app

UID_A = "uid-inspector-a"
UID_B = "uid-inspector-b"
FAKE_CLAIMS_A = {"sub": UID_A, "email": "a@sismocali.gov.co"}


class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store = store
        self._id = doc_id

    def get(self, transaction: Any = None) -> _FakeSnapshot:
        # `transaction=` accepted and ignored — the fake store is a plain
        # single-threaded dict, so a transactional read sees exactly the
        # same data a plain read would. What's under test (`_tomar_punto`'s
        # transactional claim) is the READ-CHECK-WRITE DECISION, not
        # Firestore's own snapshot-isolation machinery.
        return _FakeSnapshot(self._id, self._store.get(self._id))

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        current = self._store.get(self._id, {}) if merge else {}
        current = dict(current)
        current.update(data)
        self._store[self._id] = current


def _get_field(data: dict[str, Any], field: str) -> Any:
    """Dotted-path field access (`coords.lat`) — mirrors real Firestore's
    own nested-field `where()` support, needed by the `puntos-disponibles`
    change's bounding-box query."""
    cur: Any = data
    for part in field.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


class _FakeQuery:
    """Supports CHAINED `.where()` calls (the `puntos-disponibles` change's
    `coords.lat >= ... <= ...` bounding box needs two) — the original fake
    here only supported a single `.where()` off `_FakeCollection`."""

    def __init__(self, store: dict[str, dict[str, Any]], ids: list[str]) -> None:
        self._store = store
        self._ids = ids

    def where(self, field: str, op: str, value: Any) -> "_FakeQuery":
        if op == "==":
            matched = [i for i in self._ids if _get_field(self._store.get(i, {}), field) == value]
        elif op == ">=":
            matched = [
                i
                for i in self._ids
                if (v := _get_field(self._store.get(i, {}), field)) is not None and v >= value
            ]
        elif op == "<=":
            matched = [
                i
                for i in self._ids
                if (v := _get_field(self._store.get(i, {}), field)) is not None and v <= value
            ]
        elif op == "array_contains":
            matched = [i for i in self._ids if value in (_get_field(self._store.get(i, {}), field) or [])]
        elif op == "in":
            # Real Firestore caps `in` at 30 values per query — assert it
            # here too so a chunking bug in the router fails loudly instead
            # of silently truncating a caller with >30 groups.
            assert len(value) <= 30, "fake Firestore: 'in' query exceeds the 30-value cap"
            matched = [i for i in self._ids if _get_field(self._store.get(i, {}), field) in value]
        else:
            raise AssertionError(f"unsupported op {op!r} in fake Firestore")
        return _FakeQuery(self._store, matched)

    def get(self) -> list[_FakeSnapshot]:
        return [_FakeSnapshot(doc_id, self._store.get(doc_id)) for doc_id in self._ids]


class _FakeCollection(_FakeQuery):
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        super().__init__(store, list(store.keys()))

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id)


class _FakeTransaction:
    """Minimal, duck-typed double for `google.cloud.firestore`'s real
    `Transaction` — just enough surface for `@transactional` (imported
    UNMODIFIED from `google.cloud.firestore` by the router, never
    reimplemented) to drive it: `_clean_up`/`_begin`/`_id`/`_read_only`/
    `_max_attempts`/`_commit`/`_rollback`, plus `.set()` for the buffered
    write the router's transactional callback issues. Writes apply
    immediately (single-threaded test — no concurrent commit ordering to
    simulate); what's under test is the read-check-write DECISION inside
    the callback (`_tomar_punto`), not Firestore's own commit protocol."""

    _read_only = False
    _max_attempts = 1

    def __init__(self) -> None:
        self._id: bytes | None = None

    def _clean_up(self) -> None:
        pass

    def _begin(self, retry_id: bytes | None = None) -> None:
        self._id = b"fake-txn"

    def _commit(self) -> list[Any]:
        return []

    def _rollback(self) -> None:
        pass

    def set(self, ref: _FakeDocRef, data: dict[str, Any], merge: bool = False) -> None:
        ref.set(data, merge=merge)


class _FakeFirestore:
    """Backs THREE independent collections by name so a query against one
    can never see another's docs — `sticker_matches` (misPuntos/
    marcarHecho), `planeacion_puntos` (misPuntosPlaneacion/
    marcarHechoPlaneacion, `planeacion-asignaciones` follow-up batch), and
    `grupos_inspectores` (`grupos-inspectores` change)."""

    def __init__(
        self,
        sticker_store: dict[str, dict[str, Any]],
        planeacion_store: dict[str, dict[str, Any]] | None = None,
        grupos_store: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._stores = {
            "sticker_matches": sticker_store,
            "planeacion_puntos": planeacion_store if planeacion_store is not None else {},
            "grupos_inspectores": grupos_store if grupos_store is not None else {},
        }

    def collection(self, name: str) -> _FakeCollection:
        assert name in self._stores, f"unexpected collection: {name}"
        return _FakeCollection(self._stores[name])

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakeSismoClients:
    def __init__(
        self,
        sticker_store: dict[str, dict[str, Any]],
        planeacion_store: dict[str, dict[str, Any]] | None = None,
        grupos_store: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.firestore = _FakeFirestore(sticker_store, planeacion_store, grupos_store)
        self.app = None


def _store() -> dict[str, dict[str, Any]]:
    return {
        "point-a": {
            "inspector_uid": UID_A,
            "estado_asignacion": "pendiente",
            "direccion": "Calle 1 #2-3",
            "zona_id": "1",
        },
        "point-b": {
            "inspector_uid": UID_B,
            "estado_asignacion": "pendiente",
            "direccion": "Calle 9 #8-7",
        },
        "point-a-done": {
            "inspector_uid": UID_A,
            "estado_asignacion": "hecho",
        },
    }


def _planeacion_store() -> dict[str, dict[str, Any]]:
    return {
        "pln-a": {
            "inspector_uid": UID_A,
            "estado_asignacion": "pendiente",
            "clave_integracion": "PLN-AAAAAA-11111111",
            "direccion": "Calle 1 #2-3",
            "coords": {"lat": 3.4, "lon": -76.5},
            "comuna": "1",
            "afectacion": "DAÑO ESTRUCTURAL",
            "prioridad": "alta",
        },
        "pln-b": {
            "inspector_uid": UID_B,
            "estado_asignacion": "pendiente",
            "clave_integracion": "PLN-BBBBBB-22222222",
        },
        "pln-a-hecho": {
            "inspector_uid": UID_A,
            "estado_asignacion": "hecho",
            "clave_integracion": "PLN-CCCCCC-33333333",
        },
        "pln-a-no-aplica": {
            "inspector_uid": UID_A,
            "estado_asignacion": "no_aplica",
            "clave_integracion": "PLN-DDDDDD-44444444",
        },
    }


def _app(
    monkeypatch,
    store: dict[str, dict[str, Any]],
    planeacion_store: dict[str, dict[str, Any]] | None = None,
    grupos_store: dict[str, dict[str, Any]] | None = None,
) -> FastAPI:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("SIGNER_AWS_ACCESS_KEY_ID", "fake-access-key-id")
    monkeypatch.setenv("SIGNER_AWS_SECRET_ACCESS_KEY", "fake-secret-access-key")
    monkeypatch.setenv("SIGNER_S3_BUCKET", "test-sismo-fotos")
    monkeypatch.setenv("SURVEY123_FORM_URL", "https://survey123.arcgis.com/share/abc123")
    credentials.s3.cache_clear()
    monkeypatch.setattr(
        credentials, "sismo", lambda: _FakeSismoClients(store, planeacion_store, grupos_store)
    )
    return create_app()


def _client(monkeypatch, store: dict[str, dict[str, Any]]) -> TestClient:
    return TestClient(_app(monkeypatch, store))


def _authed_client(
    monkeypatch,
    store: dict[str, dict[str, Any]],
    claims: dict[str, Any],
    planeacion_store: dict[str, dict[str, Any]] | None = None,
    grupos_store: dict[str, dict[str, Any]] | None = None,
) -> TestClient:
    app = _app(monkeypatch, store, planeacion_store, grupos_store)
    app.dependency_overrides[current_claims] = lambda: claims
    return TestClient(app)


def test_unauthenticated_is_rejected(monkeypatch):
    store = _store()
    client = _client(monkeypatch, store)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntos"})

    assert resp.status_code == 401


def test_mis_puntos_returns_only_own_pending_points(monkeypatch):
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntos"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    ids = {p["id"] for p in body["puntos"]}
    # Only inspector A's PENDING point — not B's (cross-uid) and not A's
    # already-`hecho` point (`pendiente()` filter, ported verbatim).
    assert ids == {"point-a"}


def test_cross_inspector_marcar_hecho_is_rejected_no_write(monkeypatch):
    """field-form-session 'Cross-inspector access still rejected after
    migration' / backend-platform 'Own-uid-scoped route rejects cross-uid
    access': inspector A (sub==uidA) targeting a point whose
    inspector_uid is uidB must be rejected, with NO sticker_matches write."""
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "point-b"},
    )

    assert resp.status_code == 403
    # No write occurred — point-b's state is exactly as it started.
    assert store["point-b"]["estado_asignacion"] == "pendiente"


def test_own_uid_marcar_hecho_succeeds(monkeypatch):
    """field-form-session 'Own-uid access still succeeds after migration':
    inspector A targeting their OWN point succeeds and flips
    estado_asignacion to 'hecho'."""
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "point-a"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["id"] == "point-a"
    assert body["estado_asignacion"] == "hecho"
    assert store["point-a"]["estado_asignacion"] == "hecho"


def test_marcar_hecho_missing_punto_id_is_rejected(monkeypatch):
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post("/inspector-asignaciones", json={"action": "marcarHecho"})

    assert resp.status_code == 400


def test_marcar_hecho_nonexistent_point_is_rejected(monkeypatch):
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "does-not-exist"},
    )

    assert resp.status_code == 404


def test_unrecognized_action_is_rejected(monkeypatch):
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post("/inspector-asignaciones", json={"action": "bogus"})

    assert resp.status_code == 400


# ---- misPuntosPlaneacion / marcarHechoPlaneacion --------------------------
# `planeacion-asignaciones` follow-up batch (2026-08-26): the inspector-
# facing counterpart to `planeacion_puntos`, own-uid-scoped exactly like
# misPuntos/marcarHecho above — same auth surface, different collection.


def test_mis_puntos_planeacion_returns_only_own_pending_points(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntosPlaneacion"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    ids = {p["id"] for p in body["puntos"]}
    # Only inspector A's still-pending point — not B's (cross-uid), not A's
    # already-`hecho` point, and not A's `no_aplica` point (excluded from
    # the pool, same as the admin dashboard treats it).
    assert ids == {"pln-a"}


def test_mis_puntos_planeacion_includes_survey_link_and_point_fields(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntosPlaneacion"})

    punto = resp.json()["puntos"][0]
    assert punto["clave_integracion"] == "PLN-AAAAAA-11111111"
    assert punto["direccion"] == "Calle 1 #2-3"
    assert punto["coords"] == {"lat": 3.4, "lon": -76.5}
    assert punto["comuna"] == "1"
    assert punto["afectacion"] == "DAÑO ESTRUCTURAL"
    assert punto["prioridad"] == "alta"
    # Built via services.survey_link.build_survey_urls with the SAME
    # SURVEY123_FORM_URL the admin router reads — no duplicated URL logic.
    assert punto["survey_web"].startswith("https://survey123.arcgis.com/share/abc123")
    assert "field:codigoapp=PLN-AAAAAA-11111111" in punto["survey_web"]


def test_mis_puntos_planeacion_omits_survey_links_when_form_url_unset(monkeypatch):
    """Fail-open, not fail-loud: unlike getEnlaceSurvey's 503, a LIST action
    must never blank-page an inspector's whole picker over one missing env
    var — the point still shows, just without a clickable link yet."""
    store = _store()
    planeacion = _planeacion_store()
    app = _app(monkeypatch, store, planeacion)
    monkeypatch.setenv("SURVEY123_FORM_URL", "")
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_A
    client = TestClient(app)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntosPlaneacion"})

    assert resp.status_code == 200
    punto = resp.json()["puntos"][0]
    assert punto["survey_web"] is None
    assert punto["survey_app"] is None


def test_cross_inspector_marcar_hecho_planeacion_is_rejected_no_write(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "pln-b"},
    )

    assert resp.status_code == 403
    assert planeacion["pln-b"]["estado_asignacion"] == "pendiente"


def test_own_uid_marcar_hecho_planeacion_succeeds(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "pln-a"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["id"] == "pln-a"
    assert body["estado_asignacion"] == "hecho"
    assert planeacion["pln-a"]["estado_asignacion"] == "hecho"


def test_marcar_hecho_planeacion_missing_punto_id_is_rejected(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post("/inspector-asignaciones", json={"action": "marcarHechoPlaneacion"})

    assert resp.status_code == 400


def test_marcar_hecho_planeacion_nonexistent_point_is_rejected(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "does-not-exist"},
    )

    assert resp.status_code == 404


def test_marcar_hecho_planeacion_never_touches_sticker_matches(monkeypatch):
    """Cross-collection safety: marcarHechoPlaneacion must write ONLY to
    planeacion_puntos, never sticker_matches — same doc-id space collision
    risk a shared literal would otherwise create."""
    store = _store()
    planeacion = _planeacion_store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "pln-a"},
    )

    assert resp.status_code == 200
    assert "pln-a" not in store  # sticker_matches store untouched


# ---- grupos-inspectores: group-widened misPuntos/misPuntosPlaneacion and
# the widened own-uid-OR-group-member write guard ---------------------------
# `grupos-inspectores` change (2026-08-26). Groups of INSPECTORS (people),
# NOT to be confused with `planeacion_cuadrillas` (groups of POINTS under
# ONE inspector). A point's visible/completable set widens to own-uid
# points UNION every point whose `grupo_id` names a group the caller is an
# active member of.

UID_C = "uid-inspector-c"


def _grupo(nombre="Grupo Norte", miembros=None, activo=True):
    return {"nombre": nombre, "miembros": miembros or [], "activo": activo}


def test_mis_puntos_includes_group_assigned_points_not_owned_by_uid(monkeypatch):
    store = _store()
    store["point-group"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "pendiente",
        "direccion": "Cra 5 #10-20",
    }
    grupos = {"g1": _grupo(miembros=[UID_A, UID_C])}
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, grupos_store=grupos)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntos"})

    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()["puntos"]}
    assert ids == {"point-a", "point-group"}


def test_mis_puntos_dedupes_when_point_is_both_own_and_group(monkeypatch):
    store = _store()
    store["point-a"]["grupo_id"] = "g1"
    grupos = {"g1": _grupo(miembros=[UID_A])}
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, grupos_store=grupos)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntos"})

    ids = [p["id"] for p in resp.json()["puntos"]]
    assert ids == ["point-a"]  # not duplicated


def test_mis_puntos_excludes_group_points_for_non_member(monkeypatch):
    store = _store()
    store["point-group"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "pendiente",
    }
    grupos = {"g1": _grupo(miembros=[UID_B])}  # A is NOT a member
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, grupos_store=grupos)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntos"})

    ids = {p["id"] for p in resp.json()["puntos"]}
    assert "point-group" not in ids


def test_mis_puntos_excludes_points_from_inactive_group(monkeypatch):
    store = _store()
    store["point-group"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "pendiente",
    }
    grupos = {"g1": _grupo(miembros=[UID_A], activo=False)}
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, grupos_store=grupos)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntos"})

    ids = {p["id"] for p in resp.json()["puntos"]}
    assert "point-group" not in ids


def test_mis_puntos_chunks_more_than_thirty_groups(monkeypatch):
    """The `in` operator caps at 30 values per query — an inspector in MORE
    than 30 groups must degrade safely (chunked queries), not silently
    truncate. The fake's own 'in' op asserts <=30 per call, so this test
    fails loudly if the router ever stops chunking."""
    store = _store()
    grupos: dict[str, dict[str, Any]] = {}
    for i in range(35):
        gid = f"g{i}"
        grupos[gid] = _grupo(miembros=[UID_A])
        store[f"point-group-{i}"] = {
            "inspector_uid": None,
            "grupo_id": gid,
            "estado_asignacion": "pendiente",
        }
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, grupos_store=grupos)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntos"})

    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()["puntos"]}
    assert ids == {"point-a"} | {f"point-group-{i}" for i in range(35)}


def test_mis_puntos_planeacion_includes_group_assigned_points(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    planeacion["pln-group"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "pendiente",
        "clave_integracion": "PLN-EEEEEE-55555555",
    }
    grupos = {"g1": _grupo(miembros=[UID_A])}
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion, grupos)

    resp = client.post("/inspector-asignaciones", json={"action": "misPuntosPlaneacion"})

    ids = {p["id"] for p in resp.json()["puntos"]}
    assert ids == {"pln-a", "pln-group"}


def test_mis_puntos_planeacion_hides_a_hecho_point_from_every_group_member(monkeypatch):
    """The guarantee the user asked for: once a group-assigned point is
    completed (estado_asignacion='hecho' — whether via the pipeline's own
    exact-key auto-close after a survey submission, or via
    marcarHechoPlaneacion), it disappears from misPuntosPlaneacion for EVERY
    member of the group, not just whoever completed it — nobody in the group
    keeps seeing it as pending work."""
    store = _store()
    planeacion = _planeacion_store()
    planeacion["pln-done"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "hecho",  # already completed (auto-close or manual)
        "clave_integracion": "PLN-DDDDDD-11111111",
    }
    grupos = {"g1": _grupo(miembros=[UID_A, UID_C])}
    claims_c = {"sub": UID_C, "email": "c@sismocali.gov.co"}

    for claims in (FAKE_CLAIMS_A, claims_c):
        client = _authed_client(monkeypatch, store, claims, planeacion, grupos)
        resp = client.post("/inspector-asignaciones", json={"action": "misPuntosPlaneacion"})
        ids = {p["id"] for p in resp.json()["puntos"]}
        assert "pln-done" not in ids, f"{claims['sub']} should not see the completed group point"


def test_group_member_can_complete_group_assigned_point(monkeypatch):
    """The core capability this change exists for: a member of a point's
    group who is NOT the point's own inspector_uid can still marcarHecho
    it, and the write records WHO acted (completado_por)."""
    store = _store()
    store["point-group"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "pendiente",
    }
    grupos = {"g1": _grupo(miembros=[UID_A, UID_C])}
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, grupos_store=grupos)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "point-group"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["estado_asignacion"] == "hecho"
    assert store["point-group"]["estado_asignacion"] == "hecho"
    assert store["point-group"]["completado_por"] == UID_A
    assert "completado_en" in store["point-group"]


def test_non_member_of_group_cannot_complete_group_point_no_write(monkeypatch):
    """The negative case: a caller who is neither the point's own inspector
    NOR a member of its group must be rejected 403, with NO write."""
    store = _store()
    store["point-group"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "pendiente",
    }
    grupos = {"g1": _grupo(miembros=[UID_B])}  # A is NOT a member
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, grupos_store=grupos)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "point-group"},
    )

    assert resp.status_code == 403
    assert store["point-group"]["estado_asignacion"] == "pendiente"
    assert "completado_por" not in store["point-group"]


def test_member_of_inactive_group_cannot_complete_group_point(monkeypatch):
    store = _store()
    store["point-group"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "pendiente",
    }
    grupos = {"g1": _grupo(miembros=[UID_A], activo=False)}
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, grupos_store=grupos)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "point-group"},
    )

    assert resp.status_code == 403
    assert store["point-group"]["estado_asignacion"] == "pendiente"


def test_own_uid_marcar_hecho_still_works_and_now_stamps_completado_por(monkeypatch):
    """Individual assignment keeps working unchanged — same 200/estado
    contract as `test_own_uid_marcar_hecho_succeeds` above — and ALSO now
    gets the same completado_por/completado_en accountability stamp,
    consistent regardless of whether the point was individually or
    group-assigned."""
    store = _store()
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHecho", "punto_id": "point-a"},
    )

    assert resp.status_code == 200
    assert store["point-a"]["estado_asignacion"] == "hecho"
    assert store["point-a"]["completado_por"] == UID_A
    assert "completado_en" in store["point-a"]


def test_group_member_can_complete_group_assigned_point_planeacion(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    planeacion["pln-group"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "pendiente",
    }
    grupos = {"g1": _grupo(miembros=[UID_A, UID_C])}
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion, grupos)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "pln-group"},
    )

    assert resp.status_code == 200
    assert planeacion["pln-group"]["estado_asignacion"] == "hecho"
    assert planeacion["pln-group"]["completado_por"] == UID_A


def test_non_member_cannot_complete_group_point_planeacion_no_write(monkeypatch):
    store = _store()
    planeacion = _planeacion_store()
    planeacion["pln-group"] = {
        "inspector_uid": None,
        "grupo_id": "g1",
        "estado_asignacion": "pendiente",
    }
    grupos = {"g1": _grupo(miembros=[UID_B])}
    client = _authed_client(monkeypatch, store, FAKE_CLAIMS_A, planeacion, grupos)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "marcarHechoPlaneacion", "punto_id": "pln-group"},
    )

    assert resp.status_code == 403


# ---- puntos-disponibles: puntosCercanosDisponibles / tomarPunto -----------
# `puntos-disponibles` change (2026-08-26). Nearby UNASSIGNED, still-pending,
# NOT-ALREADY-COVERED points (either campaign) an inspector standing next to
# one can claim on the spot — claiming assigns BOTH campaigns when the same
# building has a pending record in each (binding user decision 1). See the
# router module's own docstring ("puntos-disponibles change" section) for
# the scale/race/identity reasoning these tests exercise.

BASE_LAT, BASE_LON = 3.4200, -76.5300


def _offset_lat_for_distance_m(base_lat: float, base_lon: float, target_m: float) -> float:
    """Bisects a pure-north latitude offset until `haversine_m` against
    (base_lat, base_lon) is as close as float precision allows to
    `target_m` — used to build EXACT 299 m / 301 m boundary fixtures
    against the SAME haversine function the router itself uses, rather
    than a hand-rolled degrees-per-meter approximation."""
    lo, hi = 0.0, 0.01
    for _ in range(60):
        mid = (lo + hi) / 2
        d = haversine_m((base_lat, base_lon), (base_lat + mid, base_lon))
        if d < target_m:
            lo = mid
        else:
            hi = mid
    return base_lat + hi


def _sticker_punto(lat=BASE_LAT, lon=BASE_LON, **overrides) -> dict[str, Any]:
    data = {
        "inspector_uid": None,
        "grupo_id": None,
        "estado_asignacion": "pendiente",
        "tiene_sticker": False,
        "direccion": "Calle 1 #2-3",
        "coords": {"lat": lat, "lon": lon},
    }
    data.update(overrides)
    return data


def _planeacion_punto(lat=BASE_LAT, lon=BASE_LON, **overrides) -> dict[str, Any]:
    data = {
        "inspector_uid": None,
        "grupo_id": None,
        "estado_asignacion": "pendiente",
        "tiene_survey": False,
        "direccion": "Calle 1 #2-3",
        "coords": {"lat": lat, "lon": lon},
        "clave_integracion": "PLN-AAAAAA-11111111",
    }
    data.update(overrides)
    return data


def _cercanos_client(monkeypatch, sticker=None, planeacion=None, claims=FAKE_CLAIMS_A):
    return _authed_client(monkeypatch, sticker or {}, claims, planeacion or {})


def test_puntos_cercanos_requires_lat_lng(monkeypatch):
    client = _cercanos_client(monkeypatch)

    resp = client.post("/inspector-asignaciones", json={"action": "puntosCercanosDisponibles"})

    assert resp.status_code == 400


def test_puntos_cercanos_incluye_puntos_de_ambas_campanas(monkeypatch):
    sticker = {"sk-1": _sticker_punto()}
    planeacion = {"pl-1": _planeacion_punto(lat=BASE_LAT + 0.0005)}  # ~55 m away, still within radius
    client = _cercanos_client(monkeypatch, sticker, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "puntosCercanosDisponibles", "lat": BASE_LAT, "lng": BASE_LON},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    by_id = {p["id"]: p for p in body["puntos"]}
    assert by_id["sk-1"]["campana"] == "sticker"
    assert by_id["pl-1"]["campana"] == "survey"
    # Nearest-first.
    assert [p["id"] for p in body["puntos"]] == ["sk-1", "pl-1"]


def test_puntos_cercanos_excluye_ya_asignado_individualmente(monkeypatch):
    sticker = {"sk-1": _sticker_punto(inspector_uid=UID_B)}
    client = _cercanos_client(monkeypatch, sticker)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "puntosCercanosDisponibles", "lat": BASE_LAT, "lng": BASE_LON},
    )

    assert resp.json()["puntos"] == []


def test_puntos_cercanos_excluye_asignado_a_grupo(monkeypatch):
    sticker = {"sk-1": _sticker_punto(grupo_id="g1")}
    client = _cercanos_client(monkeypatch, sticker)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "puntosCercanosDisponibles", "lat": BASE_LAT, "lng": BASE_LON},
    )

    assert resp.json()["puntos"] == []


def test_puntos_cercanos_excluye_ya_hecho(monkeypatch):
    sticker = {"sk-1": _sticker_punto(estado_asignacion="hecho")}
    client = _cercanos_client(monkeypatch, sticker)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "puntosCercanosDisponibles", "lat": BASE_LAT, "lng": BASE_LON},
    )

    assert resp.json()["puntos"] == []


def test_puntos_cercanos_excluye_survey_no_aplica(monkeypatch):
    planeacion = {"pl-1": _planeacion_punto(estado_asignacion="no_aplica")}
    client = _cercanos_client(monkeypatch, planeacion=planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "puntosCercanosDisponibles", "lat": BASE_LAT, "lng": BASE_LON},
    )

    assert resp.json()["puntos"] == []


def test_puntos_cercanos_excluye_ya_cubierto_por_sticker(monkeypatch):
    """Coordinator correction (2026-08-26): unassigned is NOT enough — a
    point already `tiene_sticker == True` must never show as available,
    even with no inspector_uid/grupo_id at all (someone did it without an
    assignment, or the cruce job matched it after the fact)."""
    sticker = {"sk-1": _sticker_punto(tiene_sticker=True)}
    client = _cercanos_client(monkeypatch, sticker)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "puntosCercanosDisponibles", "lat": BASE_LAT, "lng": BASE_LON},
    )

    assert resp.json()["puntos"] == []


def test_puntos_cercanos_excluye_ya_cubierto_por_survey(monkeypatch):
    """Same correction, survey side: `tiene_survey == True` excludes a
    point regardless of assignment state."""
    planeacion = {"pl-1": _planeacion_punto(tiene_survey=True)}
    client = _cercanos_client(monkeypatch, planeacion=planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "puntosCercanosDisponibles", "lat": BASE_LAT, "lng": BASE_LON},
    )

    assert resp.json()["puntos"] == []


def test_puntos_cercanos_respeta_radio_300m_frontera(monkeypatch):
    """The 300 m boundary, built against the router's OWN haversine
    function: a point at 299 m appears, a point at 301 m does not."""
    lat_299 = _offset_lat_for_distance_m(BASE_LAT, BASE_LON, 299.0)
    lat_301 = _offset_lat_for_distance_m(BASE_LAT, BASE_LON, 301.0)
    sticker = {
        "near": _sticker_punto(lat=lat_299, direccion="Cerca"),
        "far": _sticker_punto(lat=lat_301, direccion="Lejos"),
    }
    client = _cercanos_client(monkeypatch, sticker)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "puntosCercanosDisponibles", "lat": BASE_LAT, "lng": BASE_LON},
    )

    ids = {p["id"] for p in resp.json()["puntos"]}
    assert ids == {"near"}


# ---- tomarPunto -------------------------------------------------------------


def test_tomar_punto_requires_punto_id(monkeypatch):
    client = _cercanos_client(monkeypatch)

    resp = client.post("/inspector-asignaciones", json={"action": "tomarPunto", "campana": "sticker"})

    assert resp.status_code == 400


def test_tomar_punto_unknown_campana_is_rejected(monkeypatch):
    sticker = {"sk-1": _sticker_punto()}
    client = _cercanos_client(monkeypatch, sticker)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "sk-1", "campana": "bogus"},
    )

    assert resp.status_code == 400


def test_tomar_punto_nonexistent_point_is_rejected(monkeypatch):
    client = _cercanos_client(monkeypatch)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "does-not-exist", "campana": "sticker"},
    )

    assert resp.status_code == 404


def test_tomar_punto_ya_asignado_es_rechazado_sin_escritura(monkeypatch):
    sticker = {"sk-1": _sticker_punto(inspector_uid=UID_B)}
    client = _cercanos_client(monkeypatch, sticker)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "sk-1", "campana": "sticker"},
    )

    assert resp.status_code == 409
    assert "otro inspector ya tomó este punto" in resp.json()["detail"]
    assert sticker["sk-1"]["inspector_uid"] == UID_B  # untouched


def test_tomar_punto_race_solo_el_primero_gana(monkeypatch):
    """THE single most important test in this batch: two inspectors tap
    "tomar" on the same point. The FIRST call succeeds; the SECOND —
    reading the doc's now-updated state inside its own transaction — is
    rejected with a clear message and writes nothing, never a silent
    overwrite."""
    sticker = {"sk-1": _sticker_punto()}
    client_a = _cercanos_client(monkeypatch, sticker, claims=FAKE_CLAIMS_A)

    first = client_a.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "sk-1", "campana": "sticker"},
    )
    assert first.status_code == 200
    assert sticker["sk-1"]["inspector_uid"] == UID_A

    client_b = _cercanos_client(monkeypatch, sticker, claims={"sub": UID_B})
    second = client_b.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "sk-1", "campana": "sticker"},
    )

    assert second.status_code == 409
    assert "otro inspector ya tomó este punto" in second.json()["detail"]
    # The second (losing) call left the FIRST claim completely intact.
    assert sticker["sk-1"]["inspector_uid"] == UID_A


def test_tomar_punto_cubierto_a_mitad_de_camino_es_rechazado_sin_escritura(monkeypatch):
    """Coordinator correction (2026-08-26): the cruce job can flip
    `tiene_sticker`/`tiene_survey` true between the phone rendering the
    list and the inspector tapping "tomar" — `_tomar_punto` MUST re-check
    coverage INSIDE the transaction, not trust what the list said."""
    sticker = {"sk-1": _sticker_punto(tiene_sticker=True)}
    client = _cercanos_client(monkeypatch, sticker)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "sk-1", "campana": "sticker"},
    )

    assert resp.status_code == 409
    assert sticker["sk-1"]["inspector_uid"] is None  # no write happened


def test_tomar_punto_asigna_ambas_campanas_cuando_hay_gemelo(monkeypatch):
    """Binding user decision 1: claiming a point assigns BOTH campaigns
    when the same building has a pending, unassigned, uncovered record in
    the OTHER campaign too."""
    sticker = {"sk-1": _sticker_punto(direccion="Calle 1 #2-3")}
    planeacion = {"pl-1": _planeacion_punto(lat=BASE_LAT + 0.00001, direccion="Calle 1 #2-3")}
    client = _cercanos_client(monkeypatch, sticker, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "sk-1", "campana": "sticker"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["asignados"] == {"sticker": "sk-1", "survey": "pl-1"}
    assert body["tambien_asignado"] is True
    assert sticker["sk-1"]["inspector_uid"] == UID_A
    assert planeacion["pl-1"]["inspector_uid"] == UID_A


def test_tomar_punto_sin_gemelo_solo_asigna_una_campana(monkeypatch):
    sticker = {"sk-1": _sticker_punto(direccion="Calle 1 #2-3")}
    client = _cercanos_client(monkeypatch, sticker)  # no planeacion store at all

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "sk-1", "campana": "sticker"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["asignados"] == {"sticker": "sk-1"}
    assert body["tambien_asignado"] is False


def test_tomar_punto_no_reclama_campana_ya_cubierta(monkeypatch):
    """Coordinator correction (2026-08-26): if the sibling building's OTHER
    campaign is already covered, claim ONLY the uncovered campaign and say
    so — never send the inspector to redo work that already exists."""
    sticker = {"sk-1": _sticker_punto(direccion="Calle 1 #2-3")}
    planeacion = {
        "pl-1": _planeacion_punto(
            lat=BASE_LAT + 0.00001, direccion="Calle 1 #2-3", tiene_survey=True
        )
    }
    client = _cercanos_client(monkeypatch, sticker, planeacion)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "sk-1", "campana": "sticker"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["asignados"] == {"sticker": "sk-1"}
    assert body["tambien_asignado"] is False
    assert planeacion["pl-1"]["inspector_uid"] is None  # untouched — already covered


def test_tomar_punto_stamps_inspector_uid_and_asignado_en(monkeypatch):
    sticker = {"sk-1": _sticker_punto()}
    client = _cercanos_client(monkeypatch, sticker)

    resp = client.post(
        "/inspector-asignaciones",
        json={"action": "tomarPunto", "punto_id": "sk-1", "campana": "sticker"},
    )

    assert resp.status_code == 200
    assert sticker["sk-1"]["inspector_uid"] == UID_A
    assert sticker["sk-1"]["estado_asignacion"] == "asignado"
    assert "asignado_en" in sticker["sk-1"]
