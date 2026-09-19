"""`DepuracionCache` — content key, single-flight, referencia outside the lock
and the immutable boundary (D23/D24, tasks 10.28-10.38 and 10.42b).

Fakes with call counters and an injectable clock. The threaded tests use
barriers/events for the interleavings; no real sleep decides an outcome."""
from __future__ import annotations

import logging
import threading
from datetime import date

import pytest

from app.routers import stickers_atencionsismo as router_mod
from app.services.inspectores_depuracion import Depuracion
from app.services.inspectores_referencia import ReferenciaBundle
from tests.ledger_fakes import CallLedger, FakeClock

HOY = date(2026, 9, 19)
TTL = router_mod.REFERENCIA_CACHE_TTL_SECONDS


def _bundle(huella: str = "h1", generado_en: str = "2026-09-12", activa: bool = True, motivo: str = "") -> ReferenciaBundle:
    return ReferenciaBundle(
        vercel=(), fase2=(), main=(), generado_en=generado_en, activa=activa, motivo=motivo,
        codigos_duplicados=(), huella=huella,
    )


def _depuracion(referencia: ReferenciaBundle) -> Depuracion:
    return Depuracion(
        activa=referencia.activa, motivo=referencia.motivo, referencia_generada_en=referencia.generado_en,
        inspectores=({"identidad_key": "a", "np": "P1", "detalle": {"pasos": [1]}},),
        grupo_externos={"detalle": [{"identificacion": "1"}]},
        alias_nombres={"ana": "Ana Gomez"},
        revision_manual=({"motivo": "m", "codigo": "001"},),
    )


def _calculo_fallido() -> Depuracion:
    """The block served when the compute failed and nothing valid is left to serve."""
    return Depuracion(
        activa=False, motivo="calculo_fallido", referencia_generada_en="",
        inspectores=(), grupo_externos=None, alias_nombres={}, revision_manual=(),
    )


class Env:
    """A cache wired to counting fakes: `loads` (referencia GETs) and
    `computes` (`depurar` runs)."""

    def __init__(self, referencia=None, *, compute_wait=None):
        self.clock = FakeClock()
        self.ledger = CallLedger()
        self.referencia = referencia if referencia is not None else _bundle()
        self._compute_wait = compute_wait
        self.cache = router_mod.DepuracionCache(cargar_referencia=self._load, clock=self.clock)

    def _load(self):
        self.ledger.hit("load")
        ref = self.referencia
        if isinstance(ref, Exception):
            raise ref
        return ref

    def compute(self, referencia):
        self.ledger.hit("compute")
        if self._compute_wait is not None:
            self._compute_wait()
        return _depuracion(referencia)

    def get(self, *, inputs="s1", roster="r1", survey="v1", hoy=HOY):
        return self.cache.get_or_compute(
            inputs_version=inputs, roster_version=roster, survey_version=survey, hoy=hoy, compute=self.compute
        )

    @property
    def loads(self) -> int:
        return self.ledger["load"]

    @property
    def computes(self) -> int:
        return self.ledger["compute"]


# ── Key: exactly one compute per changed fingerprint (10.33-10.34) ──────────


def test_depuracion_cache_recomputes_once_per_changed_fingerprint():
    env = Env()
    env.get()
    assert env.computes == 1
    for _ in range(5):
        env.get()
    assert env.computes == 1  # identical fingerprints: +0

    variants = [
        dict(inputs="s2"),
        dict(inputs="s2", roster="r2"),
        dict(inputs="s2", roster="r2", survey="v2"),
        dict(inputs="s2", roster="r2", survey="v2", hoy=date(2026, 9, 20)),
    ]
    expected = 1
    for kw in variants:  # each step changes exactly ONE more fingerprint
        env.get(**kw)
        expected += 1
        assert env.computes == expected
        env.get(**kw)
        assert env.computes == expected  # and repeating it costs nothing


def test_depuracion_cache_referencia_huella_change_recomputes_once():
    env = Env(_bundle(huella="h1"))
    env.get()
    env.clock.advance(TTL + 1)
    env.referencia = _bundle(huella="h2")  # same-day republish: same generado_en, new content
    env.get()
    env.get()
    assert env.computes == 2 and env.loads == 2


def test_depuracion_cache_identical_referencia_after_ttl_costs_zero_computes():
    env = Env(_bundle(huella="h1"))
    env.get()
    env.clock.advance(TTL + 1)
    env.get()  # the bundle was re-downloaded, identical content
    assert env.loads == 2 and env.computes == 1


def test_depuracion_cache_referencia_key_also_covers_generado_en_and_motivo():
    """Manually built bundles (huella "") still differ in what the RESULT
    embeds (`referencia_generada_en`, `motivo`, `activa`), so those are part
    of the referencia fingerprint too."""
    env = Env(_bundle(huella="", generado_en="2026-09-12"))
    env.get()
    env.clock.advance(TTL + 1)
    env.referencia = _bundle(huella="", generado_en="2026-09-16")
    assert env.get().referencia_generada_en == "2026-09-16"
    assert env.computes == 2


def test_hoy_rollover_costs_exactly_one_compute_and_no_referencia_get():
    env = Env()
    env.get(hoy=date(2026, 9, 19))
    env.get(hoy=date(2026, 9, 20))
    env.get(hoy=date(2026, 9, 20))
    assert env.computes == 2 and env.loads == 1


def test_only_the_current_key_plus_last_good_is_retained():
    env = Env()
    for i in range(30):
        env.get(inputs=f"s{i}")
    assert len(env.cache._inflight) == 0
    assert env.cache._key is not None and env.cache._key[0] == "s29"


# ── Single flight (10.35-10.38) ─────────────────────────────────────────────


def _run_threads(n, target):
    barrier = threading.Barrier(n)
    out: list = [None] * n
    errors: list = []

    def worker(i):
        try:
            barrier.wait(timeout=5)
            out[i] = target(i)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return out, errors


def test_depuracion_single_flight_20_threads_one_compute():
    env = Env(compute_wait=lambda: threading.Event().wait(0.05))  # widen the window
    out, errors = _run_threads(20, lambda i: env.get())
    assert not errors
    assert env.computes == 1
    assert env.loads == 1
    assert all(r == out[0] for r in out)


def test_leader_exception_clears_marker_waiters_do_not_deadlock_next_request_retries():
    """Updated deliberately with judgment-day W6: with no last-good, leader AND
    waiter used to differ (leader raised -> `depuracion` omitted; waiter re-raised
    the leader's error). Now both get the same `calculo_fallido` block, and the
    waiter first retries ONCE as a new leader (so 2 computes, not 1)."""
    entered, release = threading.Event(), threading.Event()
    state = {"fail": True}

    def compute(referencia):
        env.ledger.hit("compute")
        if state["fail"]:
            entered.set()
            assert release.wait(timeout=5)
            raise RuntimeError("engine boom")
        return _depuracion(referencia)

    env = Env()
    results: dict[str, object] = {}

    def call(name):
        try:
            results[name] = env.cache.get_or_compute(
                inputs_version="s1", roster_version="r", survey_version="v", hoy=HOY, compute=compute
            )
        except Exception as exc:  # noqa: BLE001
            results[name] = exc

    leader = threading.Thread(target=call, args=("leader",))
    leader.start()
    assert entered.wait(timeout=5)
    waiter = threading.Thread(target=call, args=("waiter",))
    waiter.start()
    release.set()
    leader.join(timeout=5)
    waiter.join(timeout=5)
    assert not leader.is_alive() and not waiter.is_alive()  # nobody deadlocked
    assert results["leader"] == results["waiter"] == _calculo_fallido()  # same outcome for both
    assert env.computes == 2  # the leader, plus the waiter's single retry as a new leader
    assert env.cache._inflight == {}

    state["fail"] = False
    before = env.computes
    retry = env.cache.get_or_compute(inputs_version="s1", roster_version="r", survey_version="v", hoy=HOY, compute=compute)
    assert retry.activa is True
    assert env.computes == before + 1  # the retry really computed, once


def test_waiters_never_get_a_last_good_computed_under_a_different_key():
    """Updated deliberately with judgment-day W6. This used to be
    `test_waiters_get_last_good_when_leader_fails` and ASSERTED the bug: a waiter
    on key s2 received the s1 result. A result computed under a different key
    must never be presented as current, so the waiter (and the leader) now get
    the `calculo_fallido` block instead."""
    entered, release = threading.Event(), threading.Event()
    env = Env()
    good = env.get(inputs="s1")

    def failing(referencia):
        entered.set()
        assert release.wait(timeout=5)
        raise RuntimeError("boom")

    results: dict[str, object] = {}

    def lead():
        results["leader"] = env.cache.get_or_compute(
            inputs_version="s2", roster_version="r1", survey_version="v1", hoy=HOY, compute=failing
        )

    def wait():
        results["waiter"] = env.cache.get_or_compute(
            inputs_version="s2", roster_version="r1", survey_version="v1", hoy=HOY, compute=failing
        )

    t1 = threading.Thread(target=lead)
    t1.start()
    assert entered.wait(timeout=5)
    t2 = threading.Thread(target=wait)
    t2.start()
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive()
    assert results["leader"] == results["waiter"] == _calculo_fallido()
    assert results["waiter"] != good


def test_two_different_keys_do_not_share_a_marker():
    both_inside = threading.Barrier(2)

    def compute_needs_both(referencia):
        both_inside.wait(timeout=5)  # only passes if the two keys compute CONCURRENTLY
        return _depuracion(referencia)

    env = Env()
    out, errors = _run_threads(
        2,
        lambda i: env.cache.get_or_compute(
            inputs_version=f"s{i}", roster_version="r", survey_version="v", hoy=HOY, compute=compute_needs_both
        ),
    )
    assert not errors and all(r is not None for r in out)


def test_invalidate_during_in_flight_compute_serves_it_but_never_caches_it():
    entered, release = threading.Event(), threading.Event()
    env = Env()
    seen: list = []

    def slow(referencia):
        env.ledger.hit("compute")
        entered.set()
        assert release.wait(timeout=5)
        return _depuracion(referencia)

    worker = threading.Thread(
        target=lambda: seen.append(
            env.cache.get_or_compute(inputs_version="s1", roster_version="r", survey_version="v", hoy=HOY, compute=slow)
        )
    )
    worker.start()
    assert entered.wait(timeout=5)
    env.cache.invalidate()
    release.set()
    worker.join(timeout=5)

    assert seen and seen[0].activa is True
    env.cache.get_or_compute(inputs_version="s1", roster_version="r", survey_version="v", hoy=HOY, compute=env.compute)
    assert env.computes == 2  # the pre-invalidate result was not trusted as current


def test_invalidate_forces_one_recompute_then_caches_again():
    env = Env()
    env.get()
    env.cache.invalidate()
    env.get()
    env.get()
    assert env.computes == 2


# ── Immutable boundary (10.42b, D23) ────────────────────────────────────────


def _mutate(result: Depuracion) -> None:
    result.inspectores[0]["np"] = "HACKED"
    result.inspectores[0]["detalle"]["pasos"].append(99)
    result.grupo_externos["detalle"].clear()
    result.grupo_externos["extra"] = True
    result.alias_nombres["ana"] = "HACKED"
    result.alias_nombres["nuevo"] = "x"
    result.revision_manual[0]["motivo"] = "HACKED"


def test_cache_boundary_hands_out_copies_a_mutating_caller_cannot_leak():
    env = Env()
    pristine = _depuracion(_bundle())
    first = env.get()
    _mutate(first)  # the leader's own caller mutates what it received
    second = env.get()
    assert second == pristine  # the cached value is untouched
    _mutate(second)  # ...and so is a cache HIT's caller
    assert env.get() == pristine
    assert env.computes == 1


def test_cache_boundary_copies_also_for_the_same_key_last_good_fallback():
    """Updated with judgment-day W6: the last-good is served only under the SAME
    key (reachable after `invalidate()`), and even then as a private copy."""
    env = Env()
    pristine = _depuracion(_bundle())
    good = env.get(inputs="s1")
    _mutate(good)
    env.cache.invalidate()  # key cleared, last-good kept for a failing recompute

    def failing(referencia):
        raise RuntimeError("boom")

    def again():
        return env.cache.get_or_compute(
            inputs_version="s1", roster_version="r1", survey_version="v1", hoy=HOY, compute=failing
        )

    served = again()
    assert served == pristine
    _mutate(served)
    assert again() == pristine  # the mutation of a fallback copy never reached the stored last-good


# ── Referencia outside the lock (10.28-10.32) ───────────────────────────────


def test_slow_cargar_referencia_does_not_block_a_concurrent_fast_path_request():
    env = Env()
    first = env.get()  # warm: referencia loaded, result cached
    env.clock.advance(TTL + 1)  # the referencia is now stale
    inside, release = threading.Event(), threading.Event()
    original = env._load

    def slow_load():
        inside.set()
        assert release.wait(timeout=5)
        return original()

    env.cache._cargar_referencia = slow_load
    slow = threading.Thread(target=env.get)
    slow.start()
    try:
        assert inside.wait(timeout=5)
        done: list = []
        fast = threading.Thread(target=lambda: done.append(env.get()))
        fast.start()
        fast.join(timeout=3)  # must finish while the download is STILL blocked
        assert not fast.is_alive(), "a request behind a slow referencia download must not wait"
        assert done and done[0] == first
    finally:
        release.set()
        slow.join(timeout=5)
    assert env.computes == 1


def test_concurrent_stale_callers_produce_one_referencia_get():
    env = Env()
    env.get()
    env.clock.advance(TTL + 1)
    out, errors = _run_threads(20, lambda i: env.get())
    assert not errors and env.loads == 2  # 1 initial + exactly 1 refresh
    assert env.computes == 1


def test_concurrent_cold_callers_produce_one_referencia_get_and_all_get_a_result():
    env = Env()
    out, errors = _run_threads(20, lambda i: env.get())
    assert not errors and env.loads == 1 and env.computes == 1
    assert all(r == out[0] for r in out)


def test_loader_exception_never_leaves_lock_or_marker_held():
    env = Env(RuntimeError("blob exploded"))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            env.get()
    assert env.loads == 3  # cold start with nothing to serve: retried per call, never wedged
    assert env.cache._referencia_flight is None
    assert env.cache._lock.acquire(timeout=1)  # the lock is free
    env.cache._lock.release()

    env.referencia = _bundle()
    assert env.get().activa is True


def test_loader_exception_with_last_good_keeps_it_and_retries_once_per_ttl(caplog):
    env = Env(_bundle(huella="h1"))
    good = env.get()
    env.clock.advance(TTL + 1)
    env.referencia = RuntimeError("cedula 123456789 in the blob error")
    with caplog.at_level(logging.DEBUG):
        for _ in range(50):
            assert env.get() == good
    assert env.loads == 2  # 1 initial + 1 failed retry; not one per request
    assert env.computes == 1
    assert "123456789" not in caplog.text  # only the exception type is logged
    env.clock.advance(TTL + 1)
    env.get()
    assert env.loads == 3


def test_referencia_failure_keeps_last_good_without_recompute_and_retries_once_per_ttl():
    env = Env(_bundle(huella="h1"))
    good = env.get()
    env.clock.advance(TTL + 1)
    env.referencia = ReferenciaBundle.vacia(motivo="sin_blob")  # cargar_referencia never raises: it degrades
    for _ in range(50):
        assert env.get() == good
    assert env.computes == 1 and env.loads == 2
    env.clock.advance(TTL + 1)
    env.get()
    assert env.loads == 3 and env.computes == 1


def test_cold_start_failure_adopts_degraded_bundle_retried_once_per_ttl_not_per_request():
    env = Env(ReferenciaBundle.vacia(motivo="sin_blob"))
    for _ in range(50):
        result = env.get()
    assert result.activa is False and result.motivo == "sin_blob"
    assert env.loads == 1 and env.computes == 1
    env.clock.advance(TTL + 1)
    env.referencia = _bundle()
    assert env.get().activa is True  # recovers at the next TTL
    assert env.loads == 2 and env.computes == 2


def test_malformed_and_schema_mismatch_bundles_degrade_without_raising():
    for motivo in ("esquema_invalido", "manifiesto_invalido"):
        env = Env(ReferenciaBundle.vacia(motivo=motivo))
        assert env.get().motivo == motivo


def test_recovery_with_identical_content_causes_no_recompute():
    env = Env(_bundle(huella="h1"))
    env.get()
    env.clock.advance(TTL + 1)
    env.referencia = ReferenciaBundle.vacia(motivo="blip")
    env.get()
    env.clock.advance(TTL + 1)
    env.referencia = _bundle(huella="h1")  # the source is back, identical content
    env.get()
    assert env.loads == 3 and env.computes == 1


def test_referencia_get_at_most_two_per_hour_with_fifty_requests():
    env = Env()
    for i in range(50):
        env.clock.t = 1000.0 + i * 72  # 50 requests spread over one hour
        env.get()
    assert env.loads <= 2
    assert env.computes == 1


def test_referencia_clock_going_backwards_reloads_once_and_never_raises():
    env = Env()
    env.get()
    env.clock.t -= 5 * TTL
    env.get()
    env.get()
    assert env.loads == 2 and env.computes == 1


# ── Failure semantics (judgment-day W6) ─────────────────────────────────────
#
# A result computed under a DIFFERENT key must never be presented as current
# (across a `hoy` rollover it would serve yesterday's dias_inactivo/estado with
# no marker). A failing compute is retried once per waiter, bounded; then the
# last-good is served ONLY under strict key equality, else `calculo_fallido`.


class _Abort(BaseException):
    """Not an `Exception`: models KeyboardInterrupt/SystemExit-style unwinding."""


def _boom(env, message="engine boom"):
    def compute(referencia):
        env.ledger.hit("compute")
        raise RuntimeError(message)

    return compute


def _kw(**over):
    return {**dict(inputs_version="s1", roster_version="r1", survey_version="v1", hoy=HOY), **over}


def test_failure_across_a_hoy_rollover_never_serves_yesterdays_result():
    env = Env()
    yesterday = env.get(hoy=date(2026, 9, 19))
    out = env.cache.get_or_compute(compute=_boom(env), **_kw(hoy=date(2026, 9, 20)))
    assert out == _calculo_fallido() and out != yesterday
    # the failure was not cached as a result: a healthy retry recomputes and recovers
    recovered = env.cache.get_or_compute(compute=env.compute, **_kw(hoy=date(2026, 9, 20)))
    assert recovered.activa is True and recovered == yesterday


def test_failure_with_a_changed_input_never_serves_the_previous_inputs_result():
    env = Env()
    env.get(inputs="s1", roster="r1", survey="v1")
    for change in (dict(inputs_version="s2"), dict(roster_version="r2"), dict(survey_version="v2")):
        assert env.cache.get_or_compute(compute=_boom(env), **_kw(**change)) == _calculo_fallido()


def test_failure_with_the_same_key_serves_the_last_good_of_that_key():
    env = Env()
    good = env.get()
    env.cache.invalidate()  # forces a recompute of the SAME key; the last-good stays available
    assert env.cache.get_or_compute(compute=_boom(env), **_kw()) == good
    assert env.computes == 2  # the good one plus the failed recompute: no retry storm


def test_failed_compute_for_a_new_key_does_not_evict_the_good_current_key():
    env = Env()
    good = env.get()
    assert env.cache.get_or_compute(compute=_boom(env), **_kw(inputs_version="s2")) == _calculo_fallido()
    assert env.get() == good and env.computes == 2  # s1 is still a cache hit (no extra compute)


def test_a_failing_first_ever_compute_returns_calculo_fallido_and_next_request_retries():
    env = Env()
    assert env.cache.get_or_compute(compute=_boom(env), **_kw()) == _calculo_fallido()
    assert env.cache._inflight == {} and env.cache._lock.acquire(timeout=1)
    env.cache._lock.release()
    assert env.get().activa is True and env.computes == 2


class _CountingEvent(threading.Event):
    """Signals `arrived` right before a caller blocks on the flight, so a test
    can release a gated leader only once every waiter is really parked."""

    def __init__(self, arrived):
        super().__init__()
        self._arrived = arrived

    def wait(self, timeout=None):
        if not self.is_set():
            self._arrived.release()
        return super().wait(timeout)


def _count_parked_waiters(monkeypatch) -> threading.Semaphore:
    arrived = threading.Semaphore(0)
    real_flight = router_mod._Flight

    def counting_flight():
        flight = real_flight()
        flight.event = _CountingEvent(arrived)
        return flight

    monkeypatch.setattr(router_mod, "_Flight", counting_flight)
    return arrived


def test_all_waiters_failing_cost_leader_plus_one_retry_and_every_caller_gets_calculo_fallido(monkeypatch):
    n = 6
    arrived = _count_parked_waiters(monkeypatch)
    env = Env()
    entered = threading.Event()
    order_lock = threading.Lock()
    order = {"n": 0}

    def compute(referencia):
        with order_lock:
            order["n"] += 1
            index = order["n"]
        env.ledger.hit("compute")
        entered.set()
        parked = {1: n - 1, 2: n - 2}.get(index, 0)  # first the waiters, then the retriers behind leader #2
        for _ in range(parked):
            assert arrived.acquire(timeout=5), "a waiter never parked on the flight"
        raise RuntimeError("engine boom")

    out: list = [None] * n

    def call(i):
        out[i] = env.cache.get_or_compute(compute=compute, **_kw())

    leader = threading.Thread(target=call, args=(0,))
    leader.start()
    assert entered.wait(timeout=5)
    others = [threading.Thread(target=call, args=(i,)) for i in range(1, n)]
    for t in others:
        t.start()
    for t in [leader, *others]:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in [leader, *others]), "deadlock"
    assert env.computes == 2  # the leader and ONE retry leader, never one per waiter
    assert out == [_calculo_fallido()] * n
    assert env.cache._inflight == {}


def test_leader_unwinding_with_a_base_exception_releases_the_marker_and_the_waiter_recovers(monkeypatch):
    arrived = _count_parked_waiters(monkeypatch)
    env = Env()
    entered = threading.Event()
    calls = {"n": 0}
    guard = threading.Lock()

    def compute(referencia):
        with guard:
            calls["n"] += 1
            first = calls["n"] == 1
        if first:
            entered.set()
            assert arrived.acquire(timeout=5)  # wait until the waiter is parked
            raise _Abort()
        return _depuracion(referencia)

    results: dict = {}

    def run(name):
        try:
            results[name] = env.cache.get_or_compute(compute=compute, **_kw())
        except BaseException as exc:  # noqa: BLE001
            results[name] = exc

    leader = threading.Thread(target=run, args=("leader",))
    leader.start()
    assert entered.wait(timeout=5)
    waiter = threading.Thread(target=run, args=("waiter",))
    waiter.start()
    leader.join(timeout=10)
    waiter.join(timeout=10)
    assert not leader.is_alive() and not waiter.is_alive()
    assert isinstance(results["leader"], _Abort)  # never swallowed
    assert results["waiter"].activa is True  # the waiter retried as leader and computed
    assert env.cache._inflight == {}


def test_compute_failure_is_logged_by_type_and_location_only_never_the_message(caplog):
    env = Env()
    with caplog.at_level(logging.DEBUG):
        env.cache.get_or_compute(compute=_boom(env, "cedula 1020735324 juan perez"), **_kw())
    assert "1020735324" not in caplog.text and "juan perez" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_waiter_on_a_hung_leader_times_out_to_calculo_fallido_without_deadlock(monkeypatch):
    monkeypatch.setattr(router_mod, "_FLIGHT_WAIT_S", 0.05)  # the deadline itself is the behaviour under test
    env = Env()
    entered, release = threading.Event(), threading.Event()

    def hung(referencia):
        entered.set()
        assert release.wait(timeout=10)
        return _depuracion(referencia)

    leader = threading.Thread(target=lambda: env.cache.get_or_compute(compute=hung, **_kw()))
    leader.start()
    assert entered.wait(timeout=5)
    try:
        assert env.cache.get_or_compute(compute=env.compute, **_kw()) == _calculo_fallido()
        assert env.computes == 0  # the timed-out waiter did not start a competing compute
    finally:
        release.set()
        leader.join(timeout=10)
    assert env.cache._inflight == {}
    assert env.get().activa is True and env.computes == 0  # the hung leader's result was cached in the end


def test_flight_wait_is_ten_seconds_by_default():
    """A sync route parks a Starlette worker (40 by default) while it waits: 120 s
    of hung upstream would exhaust the pool. 10 s is far above a normal compute
    (~0.3 s) and the wait degrades to a `calculo_fallido` block, not an error."""
    assert router_mod._FLIGHT_WAIT_S == 10.0


# ── Referencia follower loop is bounded (judgment-day S2) ───────────────────


def test_cold_start_follower_gives_up_on_a_hung_referencia_download_with_a_degraded_bundle(monkeypatch):
    monkeypatch.setattr(router_mod, "_FLIGHT_WAIT_S", 0.05)
    env = Env()
    inside, release = threading.Event(), threading.Event()
    original = env._load

    def hung_load():
        inside.set()
        assert release.wait(timeout=10)
        return original()

    env.cache._cargar_referencia = hung_load
    leader = threading.Thread(target=env.get)
    leader.start()
    assert inside.wait(timeout=5)
    try:
        degraded = env.get()  # a cold follower: nothing to serve, the download is hung
        assert degraded.activa is False and degraded.motivo == "referencia_timeout"
    finally:
        release.set()
        leader.join(timeout=10)
    assert not leader.is_alive()
    assert env.get().activa is True  # the real bundle arrived: the degraded result no longer matches the key


def test_follower_of_a_failing_cold_download_still_reraises_the_loader_error():
    """The bounded wait must not swallow a REAL loader error (existing contract)."""
    env = Env(RuntimeError("blob exploded"))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            env.get()
