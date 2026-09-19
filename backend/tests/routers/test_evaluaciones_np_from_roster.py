"""`list_evaluaciones` reads `inspectores/{uid}.NP` from the roster component
(30 min TTL) instead of one `get_all` per distinct evaluación uid on EVERY scan
(design D33). Fallback: only the uids the roster cannot resolve are read, in
ONE batched `get_all`. Output stays byte-identical to the old path.

Counting fakes (`tests/ledger_fakes.py`) that bill reads like Firestore; no
network, no real time."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import pytest

from app.routers import stickers
from app.services.probed_scan import ProbedScan
from app.services.roster_invalidation import invalidate_inspectores_roster
from app.services.versioned_cache import VersionedCache
from tests.ledger_fakes import CallLedger, FakeClock, LedgerFirestore
from tests.routers.test_stickers_atencionsismo_components import ROSTER_TTL, Rig

_T0 = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)


def _stores() -> dict:
    return {
        "inspectores": {
            "u1": {"codigo": "004", "NP": "  P4 ", "nombre_completo": "Ana Gomez", "identificacion": "123", "entidad": "E1"},
            "u2": {"NP": "P3", "identificacion": "456"},  # cedula only, no codigo
            "u3": {"nombre_completo": "Sin claves", "NP": "P2"},  # neither codigo nor cedula: not in the roster maps
            "u4": {"codigo": "005", "identificacion": "789"},  # a doc without the NP field
        },
        "evaluaciones": {
            "ev-1": {"inspector": {"uid": "u1", "codigo": "004"}, "codigo_edificacion": "A-1"},
            "ev-2": {"inspector": {"uid": "u2"}, "codigo_edificacion": "A-2"},
            "ev-3": {"inspector": {"uid": "u3"}, "codigo_edificacion": "A-3"},
            "ev-4": {"inspector": {"uid": "u4"}, "codigo_edificacion": "A-4"},
            "ev-5": {"inspector": {"uid": "ghost"}, "codigo_edificacion": "A-5"},  # no inspectores doc at all
            "ev-6": {"inspector": {}, "codigo_edificacion": "A-6"},  # blank uid
            "ev-7": {"inspector": None, "codigo_edificacion": "A-7"},  # explicit null
            "ev-8": {"inspector": {"uid": "u1"}, "codigo_edificacion": "A-8"},  # a repeated uid
        },
    }


def _canon(rows: list) -> str:
    return json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str)


def _roster_of(db: LedgerFirestore):
    profiles = stickers.inspector_profiles(db)
    return lambda: profiles


# ── the lookup itself ───────────────────────────────────────────────────────


def test_output_is_byte_identical_to_the_old_path_for_roster_and_missing_uids():
    old_db = LedgerFirestore(_stores())
    old = stickers.list_evaluaciones(old_db)
    new_db = LedgerFirestore(_stores())
    new = stickers.list_evaluaciones(new_db, np_lookup=stickers.roster_np_lookup(new_db, _roster_of(new_db)))
    assert _canon(new) == _canon(old)
    by_id = {r["id"]: r["inspector"]["np"] for r in new}
    assert by_id["ev-1"] == "P4" and by_id["ev-2"] == "P3"  # served from the roster, stripped
    assert by_id["ev-3"] == "P2"  # NOT in the roster maps: resolved by the fallback
    assert by_id["ev-4"] == "" and by_id["ev-5"] == "" and by_id["ev-6"] == "" and by_id["ev-7"] == ""


def test_only_the_uids_missing_from_the_roster_are_read_in_one_batched_get_all():
    db = LedgerFirestore(_stores())
    lookup = stickers.roster_np_lookup(db, _roster_of(db))
    reads_before, calls_before = db.reads("inspectores"), db.ledger["get_all_calls"]
    stickers.list_evaluaciones(db, np_lookup=lookup)
    assert db.ledger["get_all_calls"] - calls_before == 1
    assert db.reads("inspectores") - reads_before == 2  # u3 (no codigo/cedula) and ghost; the old path read 5

    old_db = LedgerFirestore(_stores())
    stickers.list_evaluaciones(old_db)
    assert old_db.reads("inspectores") == 5  # u1..u4 + ghost: one read per distinct uid


def test_when_the_roster_covers_every_uid_there_is_no_get_all_at_all():
    stores = _stores()
    for key in ("ev-3", "ev-5"):
        del stores["evaluaciones"][key]
    db = LedgerFirestore(stores)
    lookup = stickers.roster_np_lookup(db, _roster_of(db))
    baseline = db.ledger["get_all_calls"]
    stickers.list_evaluaciones(db, np_lookup=lookup)
    assert db.ledger["get_all_calls"] == baseline


def test_no_uids_touches_neither_the_roster_nor_firestore():
    db = LedgerFirestore({"inspectores": {}, "evaluaciones": {"e": {"inspector": {}}}})

    def boom():
        raise AssertionError("the roster must not be read when there is nothing to look up")

    assert stickers.list_evaluaciones(db, np_lookup=stickers.roster_np_lookup(db, boom))[0]["inspector"]["np"] == ""
    assert db.ledger["get_all_calls"] == 0


def test_an_unavailable_roster_falls_back_to_the_old_batched_lookup_and_the_same_output():
    old = stickers.list_evaluaciones(LedgerFirestore(_stores()))
    db = LedgerFirestore(_stores())

    def down():
        raise RuntimeError("roster 429")

    new = stickers.list_evaluaciones(db, np_lookup=stickers.roster_np_lookup(db, down))
    assert _canon(new) == _canon(old)
    assert db.ledger["get_all_calls"] == 1 and db.reads("inspectores") == 5


def test_a_roster_profile_without_the_np_key_is_treated_as_missing_not_as_blank():
    db = LedgerFirestore({"inspectores": {"u1": {"NP": "P3"}}, "evaluaciones": {"e": {"inspector": {"uid": "u1"}}}})
    hand_built = ({}, {"1": {"uid": "u1"}})  # a profile with no "np" at all
    rows = stickers.list_evaluaciones(db, np_lookup=stickers.roster_np_lookup(db, lambda: hand_built))
    assert rows[0]["inspector"]["np"] == "P3"  # resolved by the fallback, not silently blanked


def test_a_uid_present_in_both_roster_maps_resolves_once_and_a_blank_roster_np_is_authoritative():
    db = LedgerFirestore({"inspectores": {"u1": {"codigo": "001", "identificacion": "9", "NP": ""}},
                          "evaluaciones": {"e": {"inspector": {"uid": "u1"}}}})
    lookup = stickers.roster_np_lookup(db, _roster_of(db))
    rows = stickers.list_evaluaciones(db, np_lookup=lookup)
    assert rows[0]["inspector"]["np"] == "" and db.ledger["get_all_calls"] == 0


def test_the_legacy_post_evaluaciones_path_without_a_lookup_is_unchanged():
    db = LedgerFirestore(_stores())
    stickers.list_evaluaciones(db)
    assert db.ledger["get_all_calls"] == 1


# ── through the real routes ─────────────────────────────────────────────────


def _big_rig(monkeypatch, *, n_inspectors: int = 20) -> Rig:
    rig = Rig(monkeypatch)
    rig.stores["inspectores"] = {
        f"u{i}": {"codigo": f"{i:03d}", "NP": "P3", "nombre_completo": f"Insp {i}", "identificacion": str(1000 + i)}
        for i in range(1, n_inspectors + 1)
    }
    rig.stores["evaluaciones"] = {
        f"ev-{i}": {"inspector": {"uid": f"u{i}"}, "codigo_edificacion": f"C-{i}", "timestamp": None}
        for i in range(1, n_inspectors + 1)
    }
    return rig


def test_a_simulated_hour_of_static_inputs_never_reads_inspector_docs_beyond_the_roster_scans(monkeypatch):
    rig = _big_rig(monkeypatch)
    for i in range(60):  # a continuously open tab, one request per minute
        rig.clock.t = 1000.0 + i * 60
        rig.get()
    assert rig.db.ledger["get_all_calls"] == 0
    assert rig.db.reads("inspectores") <= 2 * 20  # roster scans only (<= 2 per hour x I), no per-uid lookups


def test_a_simulated_eight_hour_day_never_uses_get_all_for_uids_the_roster_holds(monkeypatch):
    rig = _big_rig(monkeypatch)
    for i in range(8 * 12):  # one request per 5 minutes
        rig.clock.t = 1000.0 + i * 300
        rig.get()
    assert rig.db.ledger["get_all_calls"] == 0
    assert rig.db.reads("inspectores") <= 17 * 20  # 16 roster scans over 8 h + the cold one, never 4 x U per hour


def test_get_evaluaciones_route_serves_np_from_the_roster_cache(monkeypatch):
    rig = _big_rig(monkeypatch)
    body = rig.client.get("/evaluaciones").json()
    assert {e["inspector"]["np"] for e in body["evaluaciones"]} == {"P3"}
    assert rig.db.ledger["get_all_calls"] == 0
    assert rig.app.state.roster_cache.current is not None  # the roster component did the work


def test_get_evaluaciones_falls_back_to_get_all_when_the_roster_scan_fails_cold(monkeypatch):
    rig = _big_rig(monkeypatch, n_inspectors=3)
    rig.db.fail_on.add(("scan", "inspectores"))
    body = rig.client.get("/evaluaciones")
    assert body.status_code == 200
    assert {e["inspector"]["np"] for e in body.json()["evaluaciones"]} == {"P3"}
    assert rig.db.ledger["get_all_calls"] == 1


def test_np_changes_show_after_the_roster_refresh_never_later_than_its_ttl(monkeypatch):
    rig = _big_rig(monkeypatch, n_inspectors=3)
    assert {e["inspector"]["np"] for e in rig.client.get("/evaluaciones").json()["evaluaciones"]} == {"P3"}
    rig.stores["inspectores"]["u1"]["NP"] = "P4"

    rig.clock.advance(stickers.EVALUACIONES_CACHE_TTL_SECONDS + 1)  # the list expires, the roster is still fresh
    nps = {e["id"]: e["inspector"]["np"] for e in rig.client.get("/evaluaciones").json()["evaluaciones"]}
    assert nps["ev-1"] == "P3"  # the documented window: at most one roster TTL

    rig.clock.advance(ROSTER_TTL + 1)  # roster expired (and the list with it)
    nps = {e["id"]: e["inspector"]["np"] for e in rig.client.get("/evaluaciones").json()["evaluaciones"]}
    assert nps["ev-1"] == "P4"


def test_an_admin_roster_invalidation_makes_the_next_evaluaciones_scan_use_a_fresh_roster(monkeypatch):
    rig = _big_rig(monkeypatch, n_inspectors=3)
    rig.client.get("/evaluaciones")
    rig.stores["inspectores"]["u2"]["NP"] = "P4"
    invalidate_inspectores_roster(rig.app.state)  # what admin create / setEnabled / usuarios delete call
    rig.clock.advance(stickers.EVALUACIONES_CACHE_TTL_SECONDS + 1)
    nps = {e["id"]: e["inspector"]["np"] for e in rig.client.get("/evaluaciones").json()["evaluaciones"]}
    assert nps["ev-2"] == "P4"


def test_a_uid_that_only_the_fallback_knows_is_still_resolved_through_the_route(monkeypatch):
    rig = _big_rig(monkeypatch, n_inspectors=2)
    rig.stores["inspectores"]["solo"] = {"NP": "P2"}  # no codigo, no cedula: invisible to the roster maps
    rig.stores["evaluaciones"]["ev-x"] = {"inspector": {"uid": "solo"}, "codigo_edificacion": "C-x"}
    nps = {e["id"]: e["inspector"]["np"] for e in rig.client.get("/evaluaciones").json()["evaluaciones"]}
    assert nps["ev-x"] == "P2"
    assert rig.db.ledger["get_all_calls"] == 1


def test_twenty_threads_on_a_cold_start_scan_the_roster_once(monkeypatch):
    rig = _big_rig(monkeypatch, n_inspectors=5)
    barrier = threading.Barrier(20)
    statuses: list[int] = []

    def worker():
        barrier.wait(timeout=10)
        statuses.append(rig.client.get("/evaluaciones").status_code)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert statuses == [200] * 20
    assert rig.db.ledger["scan:inspectores"] == 1  # single-flight roster; the evaluaciones scan reuses it
    assert rig.db.ledger["get_all_calls"] == 0


def test_the_ledger_fake_bills_like_firestore():
    db = LedgerFirestore({"c": {"a": {"x": 1}, "b": {"x": 2}}}, CallLedger())
    db.collection("c").get()
    assert db.reads("c") == 2
    db.collection("c").count().get()
    assert db.reads("c") == 3  # one read per 1000 entries, minimum one
    db.get_all([db.collection("c").document("nope")])
    assert db.reads("c") == 4  # a missing document lookup is still a read


# ── W5: the roster snapshot behind the NP join IS the one the probe signature records ──


class _ScanHookProxy:
    def __init__(self, col, fire):
        self._col, self._fire = col, fire

    def get(self):
        self._fire()
        return self._col.get()

    def __getattr__(self, name):
        return getattr(self._col, name)


class _HookedDb(LedgerFirestore):
    """Runs `hook()` once, when the `evaluaciones` collection is SCANNED (not on its probes)."""

    def __init__(self, stores):
        super().__init__(stores)
        self.hook = None

    def _fire(self):
        hook, self.hook = self.hook, None
        if hook is not None:
            hook()

    def collection(self, name):
        col = super().collection(name)
        return _ScanHookProxy(col, self._fire) if name == "evaluaciones" else col


def _w5_rig():
    clock = FakeClock(1000.0)
    stores = {
        "inspectores": {"u1": {"codigo": "004", "NP": "P3", "identificacion": "123", "nombre_completo": "Ana"}},
        "evaluaciones": {"ev-1": {"inspector": {"uid": "u1"}, "codigo_edificacion": "A-1", "timestamp": _T0}},
    }
    db = _HookedDb(stores)
    roster = VersionedCache(name="roster", ttl_s=ROSTER_TTL, clock=clock)
    source = ProbedScan(name="evaluaciones", collection="evaluaciones", order_field="timestamp", clock=clock)
    return clock, stores, db, roster, source


def _np_of(rows: list) -> str:
    return rows[0]["inspector"]["np"]


def _set_np(stores: dict, roster: VersionedCache, np: str) -> None:
    stores["inspectores"]["u1"]["NP"] = np
    roster.invalidate()


def test_a_roster_that_changes_mid_scan_never_leaks_into_rows_recorded_under_the_previous_version():
    clock, stores, db, roster, source = _w5_rig()
    db.hook = lambda: _set_np(stores, roster, "P4")  # V2 lands while the scan is running
    rows = stickers.scan_evaluaciones(db, roster, source)
    assert _np_of(rows) == "P3"  # the snapshot the signature recorded (V1), not the newer V2


def test_a_mid_scan_change_then_a_revert_to_the_old_roster_content_keeps_the_rows_of_that_content():
    clock, stores, db, roster, source = _w5_rig()
    db.hook = lambda: _set_np(stores, roster, "P4")
    stickers.scan_evaluaciones(db, roster, source)  # rows joined with V1 (P3); the live roster is now V2
    _set_np(stores, roster, "P3")  # ... and it reverts to V1's content
    clock.advance(60)
    rows = stickers.scan_evaluaciones(db, roster, source)
    assert _np_of(rows) == "P3"  # before the fix: the P4 rows survived under V1's signature


def test_a_mid_scan_change_is_picked_up_by_the_next_refresh_and_a_later_revert_rescans_again():
    clock, stores, db, roster, source = _w5_rig()
    db.hook = lambda: _set_np(stores, roster, "P4")
    stickers.scan_evaluaciones(db, roster, source)
    clock.advance(60)
    assert _np_of(stickers.scan_evaluaciones(db, roster, source)) == "P4"  # V2 differs from the recorded V1: rescan
    _set_np(stores, roster, "P3")
    clock.advance(60)
    assert _np_of(stickers.scan_evaluaciones(db, roster, source)) == "P3"  # and back: another rescan, never stale


def test_one_scan_of_the_evaluaciones_reads_the_roster_component_exactly_once():
    clock, stores, db, roster, source = _w5_rig()
    calls: list[int] = []
    real_get = roster.get
    roster.get = lambda fetch: calls.append(1) or real_get(fetch)
    stickers.scan_evaluaciones(db, roster, source)  # cold: the probe AND the scan run
    assert len(calls) == 1  # ONE snapshot serves the signature and the lookup
    assert db.ledger["scan:inspectores"] == 1


def test_an_unavailable_roster_still_joins_np_through_the_fallback_and_recovers_when_it_returns():
    clock, stores, db, roster, source = _w5_rig()
    db.fail_on.add(("scan", "inspectores"))
    rows = stickers.scan_evaluaciones(db, roster, source)
    assert _np_of(rows) == "P3"  # the roster maps are unreachable: the per-uid `get_all` fallback answers
    assert db.ledger["get_all_calls"] == 1
    stores["inspectores"]["u1"]["NP"] = "P4"
    db.fail_on.clear()
    clock.advance(60)
    assert _np_of(stickers.scan_evaluaciones(db, roster, source)) == "P4"  # the real roster version forces a rescan
