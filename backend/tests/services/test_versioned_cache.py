"""Generic TTL + content-version + single-flight cache (D22, tasks
10.18-10.21). Fakes with call counters and an injectable clock; the only real
concurrency is the threaded tests, gated by barriers/events (no real sleeps
decide an outcome)."""
from __future__ import annotations

import logging
import threading
import traceback

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


# ── Bounded wait on the fetch lock (09a review W-1) ─────────────────────────
#
# A hung upstream call parks the leader inside `_fetch_lock`; every follower used
# to queue behind it with no deadline, exhausting the worker pool. The wait is
# now bounded (injectable, REAL seconds: it bounds an actual wait, so the tests
# inject a short one and gate the hang on an event instead of sleeping).

WAIT = 0.2


def _hung_leader(cache, hung_fetch):
    """Start a leader that parks inside `hung_fetch`; returns (thread, out, inside, release)."""
    inside, release = threading.Event(), threading.Event()

    def fetch():
        inside.set()
        assert release.wait(timeout=10)
        return hung_fetch()

    thread, out = _in_flight(cache, fetch)
    assert inside.wait(timeout=5)
    return thread, out, release


def test_default_fetch_wait_is_ten_seconds_like_the_depuracion_flight_wait():
    from app.routers import stickers_atencionsismo as router_mod
    from app.services import versioned_cache

    assert versioned_cache.DEFAULT_FETCH_WAIT_S == 10.0 == router_mod._FLIGHT_WAIT_S


def test_32_threads_behind_a_hung_fetch_serve_last_good_after_the_bounded_wait():
    clock, ledger = FakeClock(), CallLedger()
    cache = _cache(clock, fetch_wait_s=WAIT)
    good = cache.get(lambda: ["x"])
    clock.advance(TTL + 1)  # stale: the next caller becomes the leader
    leader, leader_out, release = _hung_leader(cache, lambda: ["fresh"])
    barrier = threading.Barrier(31)
    results: list = []

    def follower():
        barrier.wait(timeout=5)
        results.append(cache.get(_counting(ledger, lambda: pytest.fail("a follower never fetches"))))

    threads = [threading.Thread(target=follower) for _ in range(31)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)  # bounded: a follower that waits forever would still be alive

    try:
        assert not any(t.is_alive() for t in threads), "followers stayed parked behind the hung leader"
        assert len(results) == 31 and all(r is good for r in results)  # last-good, not an error
        assert ledger["fetch"] == 0
    finally:
        release.set()
        leader.join(timeout=5)
    assert leader_out[0].value == ["fresh"]  # the hung leader still completes and publishes
    assert cache.get(lambda: pytest.fail("fresh again")).value == ["fresh"]


def test_cold_start_followers_of_a_hung_fetch_raise_a_runtimeerror_after_the_bounded_wait():
    """Nothing to serve: the follower raises the error class the route already maps
    to 503 (`RuntimeError`), never hangs and never fetches a second time."""
    from app.services.versioned_cache import FetchWaitTimeout

    cache = _cache(FakeClock(), fetch_wait_s=WAIT)
    leader, _, release = _hung_leader(cache, lambda: ["late"])
    try:
        with pytest.raises(FetchWaitTimeout) as info:
            cache.get(lambda: pytest.fail("a follower never fetches"))
        assert isinstance(info.value, RuntimeError) and str(info.value).startswith("t:")  # names the component only
    finally:
        release.set()
        leader.join(timeout=5)
    assert cache.get(lambda: pytest.fail("published by the leader")).value == ["late"]


def test_a_follower_timing_out_does_not_arm_the_failure_backoff():
    clock = FakeClock()
    cache = _cache(clock, fetch_wait_s=WAIT)
    cache.get(lambda: ["x"])
    clock.advance(TTL + 1)
    leader, _, release = _hung_leader(cache, lambda: ["y"])
    cache.get(lambda: pytest.fail("never"))  # times out, serves last-good
    release.set()
    leader.join(timeout=5)
    clock.advance(TTL + 1)
    assert cache.get(lambda: ["z"]).value == ["z"]  # no leftover backoff: the next refresh runs


def test_fetch_lock_is_released_when_the_leader_raises():
    """A failing leader must not leave the lock held (the bounded acquire would
    turn that into a 10 s stall per caller instead of a deadlock)."""
    cache = _cache(FakeClock(), fetch_wait_s=WAIT)

    def boom():
        raise RuntimeError("down")

    for _ in range(3):
        with pytest.raises(RuntimeError, match="down"):
            cache.get(boom)
    assert cache.get(lambda: ["ok"]).value == ["ok"]


# ── Max-stale ceiling (09a review S-4) ──────────────────────────────────────

CEILING = 500.0  # > TTL and > any backoff used below


def _ceiling_cache(clock, **kw) -> VersionedCache:
    return _cache(clock, max_stale_s=CEILING, **kw)


def _boom(ledger: CallLedger | None = None):
    def boom():
        if ledger is not None:
            ledger.hit("boom")
        raise RuntimeError("cedula 1234567 exploded")
    return boom


def test_default_max_stale_is_six_hours():
    from app.services import versioned_cache

    assert versioned_cache.DEFAULT_MAX_STALE_S == 6 * 60 * 60


def test_failing_refresh_serves_last_good_up_to_the_ceiling_and_raises_one_second_past_it():
    from app.services.versioned_cache import StaleBeyondCeiling

    clock = FakeClock()
    cache = _ceiling_cache(clock, failure_backoff_s=1.0)
    good = cache.get(lambda: ["x"])
    clock.advance(CEILING)  # exactly at the ceiling: still last-good
    assert cache.get(_boom()) is good
    clock.advance(2)  # past the backoff and past the ceiling: loud
    with pytest.raises(StaleBeyondCeiling) as info:
        cache.get(_boom())
    assert isinstance(info.value, RuntimeError)  # the route already maps it to 503 / an omitted block
    assert "1234567" not in str(info.value)  # component name only, never the upstream message


def test_beyond_the_ceiling_the_backoff_window_does_not_serve_silently_either():
    from app.services.versioned_cache import StaleBeyondCeiling

    clock, ledger = FakeClock(), CallLedger()
    cache = _ceiling_cache(clock)
    cache.get(lambda: ["x"])
    clock.advance(CEILING + 1)
    with pytest.raises(StaleBeyondCeiling):
        cache.get(_boom(ledger))  # the refresh fails past the ceiling and arms the backoff
    for _ in range(5):
        with pytest.raises(StaleBeyondCeiling):
            cache.get(_boom(ledger))  # inside the backoff: still loud, and no hot retry
    assert ledger["boom"] == 1


def test_a_successful_refresh_past_the_ceiling_recovers_and_resets_the_clock():
    clock = FakeClock()
    cache = _ceiling_cache(clock)
    cache.get(lambda: ["x"])
    clock.advance(CEILING + 1)
    assert cache.get(lambda: ["y"]).value == ["y"]  # the upstream came back: served normally
    clock.advance(TTL + 1)
    assert cache.get(_boom()).value == ["y"]  # ceiling measured from the NEW last success


def test_invalidate_does_not_hide_the_age_of_the_last_good_value():
    from app.services.versioned_cache import StaleBeyondCeiling

    clock = FakeClock()
    cache = _ceiling_cache(clock)
    cache.get(lambda: ["x"])
    clock.advance(CEILING + 1)
    cache.invalidate()  # clears the freshness timestamp, not the age of the last success
    with pytest.raises(StaleBeyondCeiling):
        cache.get(_boom())


def test_ceiling_warning_is_logged_once_per_ttl_with_the_type_only(caplog):
    from app.services.versioned_cache import StaleBeyondCeiling

    clock = FakeClock()
    cache = _ceiling_cache(clock, failure_backoff_s=1.0)
    cache.get(lambda: ["x"])
    clock.advance(CEILING + 1)

    def ceiling_warnings() -> int:
        return sum("ceiling" in r.getMessage().lower() for r in caplog.records)

    with caplog.at_level(logging.DEBUG):
        for _ in range(20):
            with pytest.raises(StaleBeyondCeiling):
                cache.get(_boom())
        assert ceiling_warnings() == 1  # 20 requests inside one TTL -> one line
        clock.advance(TTL + 1)
        with pytest.raises(StaleBeyondCeiling):
            cache.get(_boom())
        assert ceiling_warnings() == 2  # a new TTL -> one more
    assert "1234567" not in caplog.text and "RuntimeError" in caplog.text


def test_hung_fetch_beyond_the_ceiling_makes_followers_raise_instead_of_serving():
    from app.services.versioned_cache import StaleBeyondCeiling

    clock = FakeClock()
    cache = _ceiling_cache(clock, fetch_wait_s=WAIT)
    cache.get(lambda: ["x"])
    clock.advance(CEILING + 1)
    leader, _, release = _hung_leader(cache, lambda: ["late"])
    try:
        with pytest.raises(StaleBeyondCeiling):
            cache.get(lambda: pytest.fail("never"))
    finally:
        release.set()
        leader.join(timeout=5)


def test_a_backwards_clock_never_counts_as_beyond_the_ceiling():
    clock = FakeClock(start=5000.0)
    cache = _ceiling_cache(clock)
    good = cache.get(lambda: ["x"])
    clock.t = 10.0  # jumped back: negative age is stale (refetch), but never "too old"
    assert cache.get(_boom()) is good


# ── Judgment-day W2: the upstream message never travels with the exception ──


def _ceiling_error(cache, clock, fetch):
    from app.services.versioned_cache import StaleBeyondCeiling

    cache.get(lambda: ["x"])
    clock.advance(CEILING + 1)
    with pytest.raises(StaleBeyondCeiling) as info:
        cache.get(fetch)
    return info.value


def test_stale_beyond_ceiling_does_not_chain_the_upstream_exception():
    """`logging.exception` prints `__cause__`/`__context__`: chaining would put the
    upstream message (which can echo a cedula or a name) into the log."""
    clock = FakeClock()
    err = _ceiling_error(_ceiling_cache(clock), clock, _boom())
    assert err.__cause__ is None and err.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(err))
    assert "1234567" not in rendered and "exploded" not in rendered


def test_stale_beyond_ceiling_names_the_component_and_the_failure_type_only():
    clock = FakeClock()
    err = _ceiling_error(_ceiling_cache(clock), clock, _boom())
    assert str(err).startswith("t:") and "RuntimeError" in str(err)
    assert "1234567" not in str(err) and "exploded" not in str(err)


def test_stale_beyond_ceiling_without_a_refresh_attempt_says_so_and_chains_nothing():
    """Inside the backoff there is no upstream exception at all."""
    from app.services.versioned_cache import StaleBeyondCeiling

    clock = FakeClock()
    cache = _ceiling_cache(clock)
    cache.get(lambda: ["x"])
    clock.advance(CEILING + 1)
    with pytest.raises(StaleBeyondCeiling):
        cache.get(_boom())  # arms the backoff
    with pytest.raises(StaleBeyondCeiling) as info:
        cache.get(_boom())  # served from the backoff branch: no exception to name
    assert info.value.__cause__ is None and str(info.value).startswith("t:")
    assert "1234567" not in "".join(traceback.format_exception(info.value))


def test_logging_exception_of_stale_beyond_ceiling_carries_no_upstream_text(caplog):
    """The exact call the callers make (`EvaluacionesCache`'s serve-stale branch)."""
    from app.services.versioned_cache import StaleBeyondCeiling

    clock = FakeClock()
    cache = _ceiling_cache(clock)
    cache.get(lambda: ["x"])
    clock.advance(CEILING + 1)
    with caplog.at_level(logging.DEBUG):
        try:
            cache.get(_boom())
        except StaleBeyondCeiling:
            logging.exception("caller: refresh failed")
    assert "caller: refresh failed" in caplog.text and "StaleBeyondCeiling" in caplog.text
    assert "1234567" not in caplog.text and "exploded" not in caplog.text


def test_a_cold_start_failure_still_propagates_the_original_exception_unchanged():
    """Only the serve-stale ceiling path is rewritten: with nothing to serve the
    caller gets the upstream error itself (routes map it to 503)."""
    cache = _ceiling_cache(FakeClock())
    with pytest.raises(RuntimeError, match="exploded"):
        cache.get(_boom())
