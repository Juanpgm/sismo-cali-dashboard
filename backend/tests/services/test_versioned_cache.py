"""Generic TTL + content-version + single-flight cache (D22, tasks
10.18-10.21). Fakes with call counters and an injectable clock; the only real
concurrency is the threaded tests, gated by barriers/events (no real sleeps
decide an outcome)."""
from __future__ import annotations

import logging
import threading

import pytest

from app.services.versioned_cache import VersionedCache
from tests.ledger_fakes import CallLedger, FakeClock

TTL = 100.0


def _cache(clock, **kw) -> VersionedCache:
    return VersionedCache(name="t", ttl_s=TTL, clock=clock, **kw)


def _counting(ledger: CallLedger, factory, name="fetch"):
    def _fetch():
        ledger.hit(name)
        return factory()
    return _fetch


def test_single_fetch_within_ttl_and_version_stable_on_identical_refetch():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    fetch = _counting(ledger, lambda: {"a": 1})  # a NEW dict on every call

    first = cache.get(fetch)
    for _ in range(50):
        assert cache.get(fetch) is first
    assert ledger["fetch"] == 1

    clock.advance(TTL + 1)
    again = cache.get(fetch)
    assert ledger["fetch"] == 2
    assert again.version == first.version
    assert again.value is first.value  # identical content keeps the object

    clock.advance(TTL + 1)
    changed = cache.get(_counting(ledger, lambda: {"a": 2}))
    assert changed.version != first.version and changed.value == {"a": 2}


def test_ttl_boundary_exactly_at_ttl_is_fresh_one_second_past_is_stale():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    fetch = _counting(ledger, lambda: [1])
    cache.get(fetch)
    clock.advance(TTL)
    cache.get(fetch)
    assert ledger["fetch"] == 1
    clock.advance(1)
    cache.get(fetch)
    assert ledger["fetch"] == 2


def test_version_is_independent_of_dict_key_order():
    clock = FakeClock()
    cache = _cache(clock)
    v1 = cache.get(lambda: {"a": 1, "b": 2}).version
    clock.advance(TTL + 1)
    assert cache.get(lambda: {"b": 2, "a": 1}).version == v1


def test_fetch_error_serves_last_good_with_same_version(caplog):
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    good = cache.get(lambda: ["x"])
    clock.advance(TTL + 1)

    def boom():
        ledger.hit("boom")
        raise RuntimeError("cedula 123456 exploded")

    with caplog.at_level(logging.DEBUG):
        served = cache.get(boom)
    assert served is good
    assert "123456" not in caplog.text  # the upstream message never reaches the log


def test_failure_backoff_no_hot_retry_at_most_one_retry_per_ttl():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    cache.get(lambda: ["x"])
    clock.advance(TTL + 1)

    def boom():
        ledger.hit("boom")
        raise RuntimeError("down")

    for _ in range(50):
        cache.get(boom)
    assert ledger["boom"] == 1  # 49 requests inside the backoff never touch the upstream

    clock.advance(TTL + 1)  # a full TTL later: exactly one more attempt
    for _ in range(50):
        cache.get(boom)
    assert ledger["boom"] == 2


def test_explicit_failure_backoff_overrides_the_ttl_default():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock, failure_backoff_s=10.0)
    cache.get(lambda: ["x"])
    clock.advance(TTL + 1)

    def boom():
        ledger.hit("boom")
        raise RuntimeError("down")

    cache.get(boom)
    clock.advance(9)
    cache.get(boom)
    assert ledger["boom"] == 1
    clock.advance(2)
    cache.get(boom)
    assert ledger["boom"] == 2


def test_recovery_after_failure_refetches_and_clears_backoff():
    clock = FakeClock()
    cache = _cache(clock, failure_backoff_s=10.0)
    cache.get(lambda: ["x"])
    clock.advance(TTL + 1)

    def boom():
        raise RuntimeError("down")

    cache.get(boom)
    clock.advance(11)
    recovered = cache.get(lambda: ["y"])
    assert recovered.value == ["y"]
    clock.advance(1)
    assert cache.get(boom).value == ["y"]  # fresh again, no fetch


def test_cold_start_error_propagates_as_before_and_is_retried_every_call():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)

    def boom():
        ledger.hit("boom")
        raise RuntimeError("cold")

    for _ in range(3):
        with pytest.raises(RuntimeError, match="cold"):
            cache.get(boom)
    assert ledger["boom"] == 3
    assert cache.get(lambda: [1]).value == [1]  # and it still works afterwards


def test_invalidate_forces_one_refetch_but_keeps_last_good_on_failure():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    good = cache.get(lambda: ["x"])
    cache.invalidate()

    def boom():
        ledger.hit("boom")
        raise RuntimeError("429")

    assert cache.get(boom) is good  # forced refetch failed -> last-good served
    assert ledger["boom"] == 1
    clock.advance(TTL + 1)  # the failure backoff is over
    cache.invalidate()
    same = cache.get(_counting(ledger, lambda: ["x"]))
    assert same.version == good.version and ledger["fetch"] == 1
    cache.get(_counting(ledger, lambda: ["x"]))
    assert ledger["fetch"] == 1  # only ONE refetch per invalidate


def test_invalidate_lifts_an_armed_failure_backoff():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    cache.get(lambda: ["x"])
    clock.advance(TTL + 1)

    def boom():
        raise RuntimeError("down")

    cache.get(boom)  # backoff armed for a full TTL
    cache.invalidate()  # an admin write must be seen NOW, not after the backoff
    assert cache.get(_counting(ledger, lambda: ["fresh"])).value == ["fresh"]


def test_invalidate_during_in_flight_fetch_keeps_the_result_but_leaves_it_stale():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    inside, release = threading.Event(), threading.Event()
    seen: list = []

    def slow():
        ledger.hit("slow")
        inside.set()
        assert release.wait(timeout=5)
        return ["old-read"]

    worker = threading.Thread(target=lambda: seen.append(cache.get(slow)))
    worker.start()
    assert inside.wait(timeout=5)
    cache.invalidate()  # an admin write lands while the scan is in flight
    release.set()
    worker.join(timeout=5)

    assert seen[0].value == ["old-read"]
    fresh = cache.get(_counting(ledger, lambda: ["new-read"]))
    assert fresh.value == ["new-read"]  # the in-flight result was NOT trusted as fresh


def test_clock_going_backwards_never_serves_forever_nor_raises():
    clock, ledger = FakeClock(start=5000.0), CallLedger()
    cache = _cache(clock)
    fetch = _counting(ledger, lambda: [1])
    cache.get(fetch)
    clock.t = 10.0  # monotonic clock jumped back by ~83 minutes
    cache.get(fetch)
    assert ledger["fetch"] == 2  # a negative age counts as stale: one safe refetch
    cache.get(fetch)
    assert ledger["fetch"] == 2  # and the new timestamp is honoured


def test_20_threads_at_expiry_one_fetch_behind_barrier():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    cache.get(lambda: ["x"])
    clock.advance(TTL + 1)
    barrier = threading.Barrier(20)
    results: list = []

    def slow_fetch():
        ledger.hit("fetch")
        threading.Event().wait(0.05)  # widen the race window; not an outcome gate
        return ["x"]

    def worker():
        barrier.wait(timeout=5)
        results.append(cache.get(slow_fetch))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert ledger["fetch"] == 1
    assert len(results) == 20 and len({id(r) for r in results}) == 1


def test_slow_fetch_of_one_component_does_not_block_another_components_fast_path():
    clock = FakeClock()
    slow_cache, fast_cache = _cache(clock), _cache(clock)
    fast_cache.get(lambda: ["fast"])
    inside, release = threading.Event(), threading.Event()

    def slow():
        inside.set()
        assert release.wait(timeout=5)
        return ["slow"]

    worker = threading.Thread(target=lambda: slow_cache.get(slow))
    worker.start()
    try:
        assert inside.wait(timeout=5)
        assert fast_cache.get(lambda: pytest.fail("fresh: must not fetch")).value == ["fast"]
        fast_cache.invalidate()
        assert fast_cache.get(lambda: ["fast2"]).value == ["fast2"]  # its own lock is free too
    finally:
        release.set()
        worker.join(timeout=5)


def test_huge_and_empty_inputs_version_normally():
    clock = FakeClock()
    cache = _cache(clock)
    big = cache.get(lambda: list(range(200_000)))
    clock.advance(TTL + 1)
    assert cache.get(lambda: list(range(200_000))).version == big.version
    clock.advance(TTL + 1)
    empty = cache.get(lambda: [])
    assert empty.value == [] and empty.version != big.version


# ── invalidate() vs in-flight refreshes (judgment-day W2/S4) ────────────────
#
# `invalidate()` must be atomic with respect to the state a refresh publishes,
# WITHOUT ever waiting behind a slow fetch (the admin write path calls it), and
# a refresh that STARTED before the invalidation must never undo it.


def _in_flight(cache, fetch):
    """Start `cache.get(fetch)` in a thread; returns (thread, out list)."""
    out: list = []

    def run():
        try:
            out.append(cache.get(fetch))
        except BaseException as exc:  # noqa: BLE001
            out.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def _invalidate_without_blocking(cache) -> None:
    """`invalidate()` on its own thread with a deadline: it must not wait for
    an in-flight fetch (a hang here would be the bug, not a slow test)."""
    thread = threading.Thread(target=cache.invalidate)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive(), "invalidate() blocked behind an in-flight fetch"


def test_invalidate_during_in_flight_failing_refresh_lifts_the_backoff_scan_exactly_once():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    good = cache.get(lambda: ["x"])
    clock.advance(TTL + 1)
    inside, release = threading.Event(), threading.Event()

    def failing():
        ledger.hit("failing")
        inside.set()
        assert release.wait(timeout=5)
        raise RuntimeError("429")

    leader, out = _in_flight(cache, failing)
    assert inside.wait(timeout=5)
    _invalidate_without_blocking(cache)  # the admin write lands while the scan is failing
    release.set()
    leader.join(timeout=5)

    assert out == [good]  # the stale leader still serves last-good to its own caller
    fresh = cache.get(_counting(ledger, lambda: ["after-write"]))
    assert fresh.value == ["after-write"]  # NO backoff re-armed: the write is visible now
    assert ledger["fetch"] == 1
    cache.get(_counting(ledger, lambda: ["never"]))
    assert ledger["fetch"] == 1  # and exactly one scan, not one per call


def test_invalidate_during_in_flight_successful_refresh_next_get_scans_exactly_once():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    cache.get(lambda: ["v0"])
    clock.advance(TTL + 1)
    inside, release = threading.Event(), threading.Event()

    def slow():
        inside.set()
        assert release.wait(timeout=5)
        return ["pre-write-read"]

    leader, out = _in_flight(cache, slow)
    assert inside.wait(timeout=5)
    _invalidate_without_blocking(cache)
    release.set()
    leader.join(timeout=5)

    assert out[0].value == ["pre-write-read"]  # the leader's own caller still gets what it read
    assert cache.get(_counting(ledger, lambda: ["post-write-read"])).value == ["post-write-read"]
    cache.get(_counting(ledger, lambda: ["never"]))
    assert ledger["fetch"] == 1


def test_stale_leader_failure_after_a_failed_backoff_window_cannot_rearm_it():
    """The backoff armed by an EARLIER failure is lifted by invalidate(); a
    leader that began before the invalidation and then fails must not arm a
    new one (the write would stay invisible for a whole TTL)."""
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock, failure_backoff_s=50.0)
    cache.get(lambda: ["x"])
    clock.advance(TTL + 1)
    inside, release = threading.Event(), threading.Event()

    def failing():
        inside.set()
        assert release.wait(timeout=5)
        raise RuntimeError("down")

    leader, _ = _in_flight(cache, failing)
    assert inside.wait(timeout=5)
    _invalidate_without_blocking(cache)
    release.set()
    leader.join(timeout=5)
    clock.advance(1)  # well inside what would have been the 50 s backoff
    assert cache.get(_counting(ledger, lambda: ["fresh"])).value == ["fresh"]


def test_two_concurrent_invalidations_behind_a_barrier_still_cost_one_refetch():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    cache.get(lambda: ["x"])
    barrier = threading.Barrier(8)

    def invalidate():
        barrier.wait(timeout=5)
        for _ in range(200):
            cache.invalidate()

    threads = [threading.Thread(target=invalidate) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    for _ in range(5):
        cache.get(_counting(ledger, lambda: ["x"]))
    assert ledger["fetch"] == 1


def test_generation_counts_every_concurrent_invalidation():
    """`_generation += 1` is a read-modify-write: without the lock two
    invalidations could collapse into one increment (a leader that started
    between them could then compare equal). 8 x 500 must all be counted."""
    cache = _cache(FakeClock())
    barrier = threading.Barrier(8)

    def invalidate():
        barrier.wait(timeout=5)
        for _ in range(500):
            cache.invalidate()

    threads = [threading.Thread(target=invalidate) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert cache._generation == 8 * 500


def test_invalidate_then_immediate_get_refetches_once_and_on_an_empty_cache_is_harmless():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock)
    cache.invalidate()  # nothing cached yet: no error, no state that could wedge a later get
    assert cache.get(_counting(ledger, lambda: ["a"])).value == ["a"]
    cache.invalidate()
    assert cache.get(_counting(ledger, lambda: ["b"])).value == ["b"]  # immediately, same tick
    cache.get(_counting(ledger, lambda: ["never"]))
    assert ledger["fetch"] == 2


def test_fast_path_reads_value_and_timestamp_as_one_snapshot():
    """S4: the fast path used to read `_cur` and then `_at` separately. If a
    refresh publishes in between, the OLD value was served stamped with the
    NEW timestamp (i.e. an expired value presented as fresh). The clock call
    sits exactly between those two reads, so it can inject that publish."""
    clock = FakeClock()
    ledger = CallLedger()
    injected = {"armed": False, "done": False}
    holder: dict = {}

    def hooked_clock() -> float:
        if injected["armed"] and not injected["done"]:
            injected["done"] = True
            holder["cache"].get(_counting(ledger, lambda: ["new"]))  # another caller refreshes meanwhile
        return clock()

    cache = VersionedCache(name="t", ttl_s=TTL, clock=hooked_clock)
    holder["cache"] = cache
    cache.get(lambda: ["old"])
    clock.advance(TTL + 1)  # "old" is expired
    injected["armed"] = True
    assert cache.get(lambda: pytest.fail("must reuse the concurrent refresh")).value == ["new"]


def test_default_clock_resolves_time_monotonic_at_call_time(monkeypatch):
    import time as _time

    now = {"t": 0.0}
    monkeypatch.setattr(_time, "monotonic", lambda: now["t"])
    ledger = CallLedger()
    cache = VersionedCache(name="t", ttl_s=TTL)
    fetch = _counting(ledger, lambda: [1])
    cache.get(fetch)
    now["t"] += TTL + 1
    cache.get(fetch)
    assert ledger["fetch"] == 2
