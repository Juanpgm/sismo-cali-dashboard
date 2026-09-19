"""`ProbedScan` (design D34): on a component's TTL expiry, replace the full
collection scan by a cheap `count()` + newest-by-timestamp probe and rescan only
when `(count, newest doc id, newest timestamp, extra)` moved, plus one forced
full reconcile every N hours. Read-billing fakes and an injectable clock; no
network, no real time."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.services.probed_scan import (
    DEFAULT_MAX_PROBE_FAILURES,
    DEFAULT_PROBE_FAILURE_GRACE_S,
    DEFAULT_RECONCILE_S,
    ProbedScan,
)
from app.services.versioned_cache import VersionedCache
from tests.ledger_fakes import FakeClock, LedgerFirestore

COL = "survey_cali"
T0 = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
HOUR = 3600.0


def _docs(n: int, *, stamped: bool = True) -> dict:
    return {
        f"s{i}": {"nombre_evaluador": f"Nombre {i}", **({"_updated_at": T0 + timedelta(seconds=i)} if stamped else {})}
        for i in range(n)
    }


class Rig:
    def __init__(self, n: int = 5, *, stamped: bool = True, reconcile_s: float = 6 * HOUR, **source_kw) -> None:
        self.clock = FakeClock(1000.0)
        self.db = LedgerFirestore({COL: _docs(n, stamped=stamped)})
        self.source = ProbedScan(name="survey_names", collection=COL, order_field="_updated_at",
                                 reconcile_s=reconcile_s, clock=self.clock, **source_kw)
        self.scans = 0

    def scan(self) -> list[str]:
        self.scans += 1
        return sorted((snap.to_dict() or {})["nombre_evaluador"] for snap in self.db.collection(COL).get())

    def fetch(self, **kw) -> list[str]:
        return self.source.fetch(self.db, self.scan, **kw)

    @property
    def store(self) -> dict:
        return self.db.stores[COL]

    def reads(self) -> int:
        return self.db.reads(COL)


def test_the_default_reconcile_interval_is_six_hours_and_is_a_named_constant():
    assert DEFAULT_RECONCILE_S == 6 * HOUR
    assert ProbedScan(name="x", collection=COL, order_field="f")._reconcile_s == DEFAULT_RECONCILE_S


def test_cold_start_scans_once_and_records_the_signature_taken_before_the_scan():
    rig = Rig(5)
    names = rig.fetch()
    assert rig.scans == 1 and len(names) == 5
    assert rig.db.ledger["count:survey_cali"] == 1 and rig.db.ledger["newest:survey_cali"] == 1


def test_a_probe_costs_about_three_reads_at_the_live_size_instead_of_a_1924_read_scan():
    rig = Rig(1924)
    rig.fetch()  # cold: one scan + the signature probe
    cold = rig.reads()
    assert cold == 1924 + 2 + 1  # scan + count (2 reads for 1,924 entries) + newest (1 read)
    rig.clock.advance(HOUR + 1)
    again = rig.fetch()
    assert rig.scans == 1  # unchanged: no scan
    assert rig.reads() - cold == 3  # count() 2 + newest 1
    assert again == rig.fetch()


def test_unchanged_probe_returns_the_very_same_cached_object():
    rig = Rig(4)
    first = rig.fetch()
    rig.clock.advance(HOUR + 1)
    assert rig.fetch() is first and rig.scans == 1


def test_a_new_document_changes_the_count_and_costs_exactly_one_scan():
    rig = Rig(4)
    rig.fetch()
    rig.store["s99"] = {"nombre_evaluador": "Nuevo", "_updated_at": T0 + timedelta(hours=1)}
    rig.clock.advance(HOUR + 1)
    names = rig.fetch()
    assert rig.scans == 2 and "Nuevo" in names
    rig.clock.advance(HOUR + 1)
    rig.fetch()
    assert rig.scans == 2  # stable again


def test_a_deletion_changes_the_count_and_costs_one_scan():
    rig = Rig(4)
    rig.fetch()
    del rig.store["s2"]
    rig.clock.advance(HOUR + 1)
    assert len(rig.fetch()) == 3 and rig.scans == 2


def test_an_edit_that_bumps_updated_at_costs_one_scan_with_the_count_unchanged():
    rig = Rig(4)
    rig.fetch()
    rig.store["s1"] = {"nombre_evaluador": "Renombrado", "_updated_at": T0 + timedelta(days=1)}
    rig.clock.advance(HOUR + 1)
    assert "Renombrado" in rig.fetch() and rig.scans == 2


def test_replacing_the_newest_document_by_another_id_with_the_same_timestamp_is_detected():
    rig = Rig(3)
    rig.fetch()
    stamp = rig.store["s2"]["_updated_at"]
    del rig.store["s2"]
    rig.store["zz"] = {"nombre_evaluador": "Otro", "_updated_at": stamp}
    rig.clock.advance(HOUR + 1)
    assert "Otro" in rig.fetch() and rig.scans == 2


def test_blind_spot_an_edit_that_never_touches_updated_at_is_not_seen_until_the_forced_reconcile():
    rig = Rig(4)
    rig.fetch()
    rig.store["s0"]["nombre_evaluador"] = "Cambio silencioso"  # e.g. a console edit: `_updated_at` untouched
    rig.clock.advance(HOUR + 1)
    assert "Cambio silencioso" not in rig.fetch() and rig.scans == 1  # documented blind spot
    rig.clock.advance(6 * HOUR)
    assert "Cambio silencioso" in rig.fetch() and rig.scans == 2  # bounded by the reconcile


def test_forced_full_reconcile_after_n_hours_even_when_the_probe_says_unchanged():
    rig = Rig(4, reconcile_s=6 * HOUR)
    rig.fetch()
    for hour in range(1, 6):
        rig.clock.t = 1000.0 + hour * HOUR + 1
        rig.fetch()
    assert rig.scans == 1  # five probes, no scan
    rig.clock.t = 1000.0 + 6 * HOUR + 1
    rig.fetch()
    assert rig.scans == 2  # the reconcile
    rig.clock.t = 1000.0 + 7 * HOUR + 2
    rig.fetch()
    assert rig.scans == 2  # and the interval restarts from the reconcile


def test_the_reconcile_boundary_is_inclusive_and_the_interval_is_injectable():
    rig = Rig(3, reconcile_s=100.0)
    rig.fetch()
    rig.clock.advance(99.9)
    rig.fetch()
    assert rig.scans == 1
    rig.clock.advance(0.1)  # exactly 100 s since the scan
    rig.fetch()
    assert rig.scans == 2


def test_a_backwards_clock_costs_one_safe_rescan_and_then_settles():
    rig = Rig(3)
    rig.clock.t = 10_000.0
    rig.fetch()
    rig.clock.t = 500.0  # moved backwards: elapsed time is unknowable
    rig.fetch()
    assert rig.scans == 2
    rig.fetch()
    rig.clock.advance(HOUR + 1)
    rig.fetch()
    assert rig.scans == 2  # no rescan loop


def test_probe_failure_after_warm_raises_so_the_cache_can_serve_stale_and_never_scans():
    rig = Rig(3, probe_failure_grace_s=10 * HOUR)  # one failure = the transient contract
    rig.fetch()
    rig.db.fail_on.add(("count", COL))
    rig.clock.advance(HOUR + 1)
    with pytest.raises(RuntimeError):
        rig.fetch()
    assert rig.scans == 1
    rig.db.fail_on.clear()
    rig.fetch()  # recovered: still unchanged
    assert rig.scans == 1


def test_a_failing_newest_query_is_a_probe_failure_too():
    rig = Rig(3, probe_failure_grace_s=10 * HOUR)  # one failure = the transient contract
    rig.fetch()
    rig.db.fail_on.add(("newest", COL))
    rig.clock.advance(HOUR + 1)
    with pytest.raises(RuntimeError):
        rig.fetch()
    assert rig.scans == 1


def test_count_unsupported_raises_when_warm_but_a_due_reconcile_still_scans():
    rig = Rig(3, probe_failure_grace_s=10 * HOUR)  # one failure = the transient contract
    rig.fetch()
    rig.db.count_unsupported = True
    rig.clock.advance(HOUR + 1)
    with pytest.raises(AttributeError):
        rig.fetch()  # the cache above serves stale; nothing scanned
    assert rig.scans == 1
    rig.clock.advance(6 * HOUR)
    rig.fetch()  # the forced reconcile does not need the probe
    assert rig.scans == 2


def test_cold_start_with_a_dead_probe_still_scans_then_the_next_working_probe_rescans_once():
    rig = Rig(3)
    rig.db.fail_on.add(("count", COL))
    assert len(rig.fetch()) == 3 and rig.scans == 1
    rig.db.fail_on.clear()
    rig.clock.advance(HOUR + 1)
    rig.fetch()
    assert rig.scans == 2  # signature unknown until now
    rig.clock.advance(HOUR + 1)
    rig.fetch()
    assert rig.scans == 2


def test_an_empty_collection_costs_one_count_read_and_no_newest_query():
    rig = Rig(0)
    assert rig.fetch() == [] and rig.scans == 1
    assert rig.db.ledger["newest:survey_cali"] == 0
    rig.clock.advance(HOUR + 1)
    assert rig.fetch() == [] and rig.scans == 1
    rig.store["s1"] = {"nombre_evaluador": "Primero", "_updated_at": T0}
    rig.clock.advance(HOUR + 1)
    assert rig.fetch() == ["Primero"] and rig.scans == 2


def test_documents_without_the_ordered_field_are_invisible_to_newest_but_counted():
    rig = Rig(3, stamped=False)  # no `_updated_at` anywhere: the newest query returns nothing
    rig.fetch()
    rig.clock.advance(HOUR + 1)
    rig.fetch()
    assert rig.scans == 1  # (count, None): stable
    rig.store["x"] = {"nombre_evaluador": "Sin sello"}
    rig.clock.advance(HOUR + 1)
    assert "Sin sello" in rig.fetch() and rig.scans == 2  # the count still catches it


def test_a_malformed_count_result_is_a_probe_failure_not_a_crash_or_a_false_unchanged():
    rig = Rig(3, probe_failure_grace_s=10 * HOUR)  # one failure = the transient contract
    rig.fetch()

    class _NoRows:
        def get(self):
            return []

    rig.db.collection = lambda name: type("C", (), {"count": lambda self: _NoRows(),
                                                   "order_by": lambda self, *a, **k: None})()
    rig.clock.advance(HOUR + 1)
    with pytest.raises(Exception):
        rig.fetch()
    assert rig.scans == 1


def test_a_failing_scan_keeps_the_previous_state_and_is_retried():
    rig = Rig(3)
    first = rig.fetch()
    rig.store["n"] = {"nombre_evaluador": "N", "_updated_at": T0 + timedelta(hours=5)}
    real_scan = rig.scan
    rig.scan = lambda: (_ for _ in ()).throw(RuntimeError("scan 429"))
    rig.clock.advance(HOUR + 1)
    with pytest.raises(RuntimeError):
        rig.fetch()
    rig.scan = real_scan
    assert "N" in rig.fetch() and first != rig.fetch()


def test_the_extra_fingerprint_forces_a_scan_only_when_it_changes():
    rig = Rig(3)
    rig.fetch(extra="np-a")
    rig.clock.advance(HOUR + 1)
    rig.fetch(extra="np-a")
    assert rig.scans == 1
    rig.clock.advance(HOUR + 1)
    rig.fetch(extra="np-b")
    assert rig.scans == 2


def test_probe_failures_are_logged_by_type_only_never_by_message(caplog):
    rig = Rig(3)
    rig.db.stores[COL]["s0"]["nombre_evaluador"] = "Ana Perez 1020304050"
    rig.db.fail_on.add(("count", COL))
    with caplog.at_level(logging.DEBUG):
        rig.fetch()  # cold: best-effort probe fails, the scan proceeds
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in text
    assert "429" not in text and "1020304050" not in text and "Ana Perez" not in text


def test_twenty_threads_at_expiry_run_one_probe_and_at_most_one_scan_behind_the_versioned_cache():
    rig = Rig(50)
    cache = VersionedCache(name="survey_names", ttl_s=HOUR, clock=rig.clock)
    cache.get(lambda: rig.fetch())
    rig.store["new"] = {"nombre_evaluador": "Nuevo", "_updated_at": T0 + timedelta(hours=9)}
    rig.clock.advance(HOUR + 1)
    before_counts, before_scans = rig.db.ledger["count:survey_cali"], rig.scans
    barrier = threading.Barrier(20)
    results: list = []

    def worker():
        barrier.wait(timeout=10)
        results.append(cache.get(lambda: rig.fetch()).version)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(results) == 20 and len(set(results)) == 1
    assert rig.db.ledger["count:survey_cali"] - before_counts == 1  # ONE probe, whose signature the scan reuses
    assert rig.scans - before_scans == 1


def test_twenty_threads_with_unchanged_content_run_one_probe_and_zero_scans():
    rig = Rig(50)
    cache = VersionedCache(name="survey_names", ttl_s=HOUR, clock=rig.clock)
    cache.get(lambda: rig.fetch())
    rig.clock.advance(HOUR + 1)
    before_counts = rig.db.ledger["count:survey_cali"]
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait(timeout=10)
        cache.get(lambda: rig.fetch())

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert rig.db.ledger["count:survey_cali"] - before_counts == 1
    assert rig.scans == 1


def test_a_versioned_cache_over_the_source_keeps_its_version_across_an_unchanged_probe():
    rig = Rig(6)
    cache = VersionedCache(name="survey_names", ttl_s=HOUR, clock=rig.clock)
    v1 = cache.get(lambda: rig.fetch())
    rig.clock.advance(HOUR + 1)
    v2 = cache.get(lambda: rig.fetch())
    assert v1 is v2 and rig.scans == 1


# ── W4: the newest document edited AGAIN (only its own timestamp moves) ─────


def test_the_already_newest_document_edited_again_moves_only_its_timestamp_and_costs_one_scan():
    rig = Rig(4)
    rig.fetch()
    newest = max(rig.store, key=lambda k: rig.store[k]["_updated_at"])
    count_before = len(rig.store)
    rig.store[newest] = {"nombre_evaluador": "Editado otra vez", "_updated_at": T0 + timedelta(days=3)}
    assert len(rig.store) == count_before  # same count ...
    assert max(rig.store, key=lambda k: rig.store[k]["_updated_at"]) == newest  # ... and the same newest id
    rig.clock.advance(HOUR + 1)
    assert "Editado otra vez" in rig.fetch() and rig.scans == 2  # ONLY `newest_at` differs
    rig.clock.advance(HOUR + 1)
    rig.fetch()
    assert rig.scans == 2  # and it settles


def test_the_probe_signature_carries_the_newest_timestamp():
    rig = Rig(4)
    count, newest_id, newest_at, extra = rig.source._probe(rig.db, "x")
    assert (count, newest_id, extra) == (4, "s3", "x")
    assert newest_at == (T0 + timedelta(seconds=3)).isoformat()
    assert rig.source._probe(rig.db, "")[2] == newest_at  # stable across probes


# ── C1: a probe that cannot run must never silently lengthen the staleness window ──


def test_the_probe_failure_policy_defaults_are_named_constants_and_injectable():
    assert DEFAULT_MAX_PROBE_FAILURES == 2
    assert DEFAULT_PROBE_FAILURE_GRACE_S == 15 * 60.0
    default = ProbedScan(name="x", collection=COL, order_field="f")
    assert default._max_probe_failures == DEFAULT_MAX_PROBE_FAILURES
    assert default._probe_failure_grace_s == DEFAULT_PROBE_FAILURE_GRACE_S
    custom = ProbedScan(name="x", collection=COL, order_field="f", max_probe_failures=5, probe_failure_grace_s=30.0)
    assert custom._max_probe_failures == 5 and custom._probe_failure_grace_s == 30.0


@pytest.mark.parametrize("kw", [{"max_probe_failures": 0}, {"max_probe_failures": -1}, {"probe_failure_grace_s": -1.0}])
def test_a_nonsense_probe_failure_policy_is_rejected_not_treated_as_never_or_always(kw):
    with pytest.raises(ValueError):
        ProbedScan(name="x", collection=COL, order_field="f", **kw)


def test_repro_count_fails_scans_work_a_new_document_shows_up_within_grace_not_at_the_reconcile():
    rig = Rig(5)
    rig.fetch()
    rig.store["new"] = {"nombre_evaluador": "Nuevo", "_updated_at": T0 + timedelta(hours=1)}
    rig.db.fail_on.add(("count", COL))
    rig.clock.advance(300)
    with pytest.raises(RuntimeError):
        rig.fetch()  # failure 1, inside the grace: the caller serves stale, nothing scanned
    assert rig.scans == 1
    rig.clock.advance(300)
    names = rig.fetch()  # failure 2: the probe cannot run, so the scan (the source of truth) runs
    assert "Nuevo" in names and rig.scans == 2
    assert rig.clock.t - 1000.0 < DEFAULT_PROBE_FAILURE_GRACE_S  # well inside TTL + grace, not 6 h


def test_a_transient_single_probe_failure_does_not_trigger_a_scan_and_recovery_resets_the_counter():
    rig = Rig(4)
    rig.fetch()
    for _ in range(5):  # fail, recover, fail, recover ...: never two CONSECUTIVE failures
        rig.db.fail_on.add(("count", COL))
        rig.clock.advance(300)
        with pytest.raises(RuntimeError):
            rig.fetch()
        rig.db.fail_on.clear()
        rig.clock.advance(60)
        rig.fetch()
    assert rig.scans == 1  # every refresh stayed cheap


def test_the_grace_since_the_last_success_triggers_the_scan_independently_of_the_failure_count():
    rig = Rig(4, probe_failure_grace_s=900.0, max_probe_failures=10)  # only the age rule can fire
    rig.fetch()
    rig.db.fail_on.add(("count", COL))
    rig.clock.advance(899.0)
    with pytest.raises(RuntimeError):
        rig.fetch()
    assert rig.scans == 1
    rig.clock.advance(1.0)  # exactly 900 s since the last success (the scan): inclusive boundary, like the reconcile
    rig.fetch()
    assert rig.scans == 2


def test_the_grace_is_measured_from_the_last_successful_probe_not_only_from_the_last_scan():
    rig = Rig(4, probe_failure_grace_s=900.0)
    rig.fetch()
    rig.clock.advance(800)
    rig.fetch()  # a successful (unchanged) probe re-anchors the grace at t = 800
    rig.db.fail_on.add(("count", COL))
    rig.clock.advance(800)  # 1,600 s after the scan but only 800 s after the last good probe
    with pytest.raises(RuntimeError):
        rig.fetch()
    assert rig.scans == 1


def test_probe_and_scan_both_failing_propagates_so_the_serve_stale_paths_apply():
    rig = Rig(4)
    cache = VersionedCache(name="survey_names", ttl_s=HOUR, clock=rig.clock)
    good = cache.get(lambda: rig.fetch())
    rig.store["new"] = {"nombre_evaluador": "Nuevo", "_updated_at": T0 + timedelta(hours=1)}
    rig.db.fail_on.update({("count", COL), ("scan", COL)})
    rig.clock.advance(HOUR + 1)  # the grace (15 min) has long elapsed: the scan is attempted ...
    assert cache.get(lambda: rig.fetch()) is good  # ... fails, and the cache above serves last-good as before
    assert rig.db.ledger[f"attempt:scan:{COL}"] == 2  # cold scan + the failed attempt
    rig.db.fail_on.clear()
    rig.clock.advance(HOUR + 1)
    assert "Nuevo" in cache.get(lambda: rig.fetch()).value  # recovery: fresh again, state was intact


def test_a_failing_scan_after_the_threshold_is_retried_at_the_next_refresh_not_swallowed():
    rig = Rig(4)
    rig.fetch()
    rig.db.fail_on.update({("count", COL), ("scan", COL)})
    for _ in range(3):
        rig.clock.advance(300)
        with pytest.raises(RuntimeError):
            rig.fetch()
    assert rig.db.ledger[f"attempt:scan:{COL}"] == 1 + 2  # failure 1: no scan; failures 2 and 3 each retry the scan
    rig.db.fail_on.discard(("scan", COL))
    rig.clock.advance(300)
    assert len(rig.fetch()) == 4  # the scan works again while the probe is still dead


def test_the_failure_threshold_is_injectable():
    rig = Rig(4, max_probe_failures=1)
    rig.fetch()
    rig.db.fail_on.add(("count", COL))
    rig.clock.advance(300)
    rig.fetch()
    assert rig.scans == 2  # one failure was enough

    slow = Rig(4, max_probe_failures=3)
    slow.fetch()
    slow.db.fail_on.add(("count", COL))
    for _ in range(2):
        slow.clock.advance(300)
        with pytest.raises(RuntimeError):
            slow.fetch()
    assert slow.scans == 1
    slow.clock.advance(300)
    slow.fetch()
    assert slow.scans == 2


def test_a_permanently_dead_probe_costs_at_most_one_scan_per_max_failures_refreshes_and_settles_after_recovery():
    rig = Rig(4)
    rig.fetch()
    rig.db.fail_on.add(("count", COL))
    raised = 0
    for _ in range(10):
        rig.clock.advance(300)
        try:
            rig.fetch()
        except RuntimeError:
            raised += 1
    assert rig.scans == 1 + 5 and raised == 5  # a scan every 2nd refresh, never every one
    rig.db.fail_on.clear()
    rig.clock.advance(300)
    rig.fetch()
    assert rig.scans == 1 + 5 + 1  # the signature was unknown: exactly one more scan
    rig.clock.advance(300)
    rig.fetch()
    assert rig.scans == 1 + 5 + 1  # stable again


def test_a_backwards_clock_with_a_failing_probe_scans_instead_of_guessing_the_age():
    rig = Rig(3)
    rig.fetch()  # scan at t = 1000
    rig.clock.t = 5000.0
    rig.fetch()  # a good probe re-anchors the last success at t = 5000 (the scan anchor stays at 1000)
    rig.db.fail_on.add(("count", COL))
    rig.clock.t = 3000.0  # moved backwards, yet still inside the reconcile interval of the scan
    rig.fetch()
    assert rig.scans == 2  # the time since the last success is negative: a safe scan, not a silent stale


def test_a_probe_failure_that_falls_through_to_the_scan_is_logged_once_per_grace_window_by_type_only(caplog):
    rig = Rig(3, max_probe_failures=1)
    rig.store["s0"]["nombre_evaluador"] = "Ana Perez 1020304050"
    rig.fetch()
    rig.db.fail_on.add(("count", COL))
    with caplog.at_level(logging.DEBUG):
        for _ in range(4):
            rig.clock.advance(60)
            rig.fetch()  # four fall-throughs inside ONE 15-minute window
    lines = [r.getMessage() for r in caplog.records if "falling back" in r.getMessage()]
    assert len(lines) == 1
    assert "RuntimeError" in lines[0] and "survey_names" in lines[0]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "429" not in text and "1020304050" not in text and "Ana Perez" not in text
    rig.clock.advance(DEFAULT_PROBE_FAILURE_GRACE_S)
    with caplog.at_level(logging.DEBUG):
        rig.fetch()
    assert len([r for r in caplog.records if "falling back" in r.getMessage()]) == 2  # a new window logs again


def test_twenty_threads_with_a_failing_probe_still_run_one_probe_attempt_and_one_scan():
    rig = Rig(50, max_probe_failures=1)
    cache = VersionedCache(name="survey_names", ttl_s=HOUR, clock=rig.clock)
    cache.get(lambda: rig.fetch())
    rig.store["new"] = {"nombre_evaluador": "Nuevo", "_updated_at": T0 + timedelta(hours=9)}
    rig.db.fail_on.add(("count", COL))
    rig.clock.advance(HOUR + 1)
    before_attempts, before_scans = rig.db.ledger[f"attempt:count:{COL}"], rig.scans
    barrier = threading.Barrier(20)
    results: list = []

    def worker():
        barrier.wait(timeout=10)
        results.append(cache.get(lambda: rig.fetch()).version)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(results) == 20 and len(set(results)) == 1
    assert rig.db.ledger[f"attempt:count:{COL}"] - before_attempts == 1
    assert rig.scans - before_scans == 1
