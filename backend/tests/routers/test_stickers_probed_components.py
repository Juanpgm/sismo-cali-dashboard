"""Route-level ledger for the probe-gated components (design D34): the
`survey_names` component and the Firestore `evaluaciones` side stop paying a full
scan per TTL expiry and pay a `count()` + newest-document probe instead, plus a
forced reconcile every N hours. Read-billing fakes (`LedgerFirestore`), the real
app and one injectable clock; no real time, no network."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from app.routers import stickers
from app.routers import stickers_atencionsismo as router_mod
from app.services.probed_scan import DEFAULT_RECONCILE_S, ProbedScan
from tests.routers.test_stickers_atencionsismo_components import (
    EVALS_TTL, OPT_IN, ROSTER_TTL, SURVEY_TTL, Rig,
)

HOUR = 3600.0
T0 = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
S, E, I_ = 1924, 1470, 131  # the live sizes of the 2026-09-19 measurement
STICKER_TTL = stickers.EVALUACIONES_CACHE_TTL_SECONDS


def _wide_grace(rig: Rig) -> None:
    """A failed probe = the TRANSIENT contract (serve stale, no scan): widen the probe-failure grace
    and the consecutive-failure threshold so the C1 fall-through of D34 does not turn failures into
    scans. The default policy (2 consecutive failures, grace = the component TTL) has its own tests."""
    for source in (rig.app.state.survey_names_source, rig.app.state.evaluaciones_source):
        source._probe_failure_grace_s = 10 * HOUR
        source._max_probe_failures = 10**6



def _survey(n: int) -> dict:
    return {f"g{i}": {"nombre_evaluador": f"Evaluador {i % 400}", "_updated_at": T0 + timedelta(seconds=i)} for i in range(n)}


def _evals(n: int, n_uids: int) -> dict:
    return {
        f"ev-{i}": {"inspector": {"uid": f"u{i % n_uids}"}, "codigo_edificacion": f"C-{i}", "timestamp": T0 + timedelta(seconds=i)}
        for i in range(n)
    }


def _inspectores(n: int) -> dict:
    return {f"u{i}": {"codigo": f"{i:03d}", "NP": "P3", "nombre_completo": f"Insp {i}", "identificacion": str(1000 + i)}
            for i in range(n)}


def _live_rig(monkeypatch, *, s: int = S, e: int = E, i: int = I_, u: int = 99) -> Rig:
    rig = Rig(monkeypatch)
    rig.stores["survey_cali"] = _survey(s)
    rig.stores["inspectores"] = _inspectores(i)
    rig.stores["evaluaciones"] = _evals(e, u)
    return rig


def _run(rig: Rig, seconds: float, *, step: float = 60.0, start: float | None = None) -> None:
    """A continuously open admin tab: one request every `step` seconds."""
    t0 = rig.clock.t if start is None else start
    n = int(seconds // step)
    for k in range(1, n + 1):
        rig.clock.t = t0 + k * step
        rig.get()


def _warm(rig: Rig) -> None:
    rig.get()  # cold start: everything scanned once (plus the signature probes)


def _delta(rig: Rig, before: dict, key: str) -> int:
    return rig.ledger[key] - before[key]


def _snap(rig: Rig, *keys: str) -> dict:
    return {k: rig.ledger[k] for k in keys}


SURVEY_KEYS = ("scan:survey_cali", "count:survey_cali", "newest:survey_cali", "reads:survey_cali", "attempt:count:survey_cali")
EVAL_KEYS = ("scan:evaluaciones", "count:evaluaciones", "newest:evaluaciones", "reads:evaluaciones")


# ── survey_names ────────────────────────────────────────────────────────────


def test_survey_static_hour_costs_one_probe_of_three_reads_instead_of_a_1924_read_scan(monkeypatch):
    rig = _live_rig(monkeypatch)
    _warm(rig)
    assert rig.ledger["scan:survey_cali"] == 1
    before = _snap(rig, *SURVEY_KEYS)
    _run(rig, HOUR + 60)  # one TTL expiry (60 min) inside the window
    assert _delta(rig, before, "scan:survey_cali") == 0
    assert _delta(rig, before, "count:survey_cali") == 1 and _delta(rig, before, "newest:survey_cali") == 1
    assert _delta(rig, before, "reads:survey_cali") == 3  # count() 2 + newest 1
    assert rig.ledger["depurar"] == 1  # unchanged names: same version, no recompute


def test_survey_simulated_eight_hour_day_scans_only_at_cold_start_and_at_the_forced_reconcile(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _warm(rig)
    _run(rig, 8 * HOUR, step=300.0)
    assert rig.ledger["scan:survey_cali"] == 2  # cold + the 6 h reconcile (never one per TTL)
    assert rig.ledger["count:survey_cali"] <= 1 + 7 + 1  # cold signature + <= 7 expiries + the reconcile's
    assert rig.ledger["reads:survey_cali"] <= 2 * S + 9 * 3
    assert rig.ledger["depurar"] == 1


def test_a_changed_count_costs_exactly_one_scan_and_one_recompute(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _warm(rig)
    rig.stores["survey_cali"]["gnew"] = {"nombre_evaluador": "Ana Gomez", "_updated_at": T0 + timedelta(days=1)}
    before = _snap(rig, *SURVEY_KEYS)
    rig.clock.advance(SURVEY_TTL + 1)
    rig.get()
    rig.get()
    assert _delta(rig, before, "scan:survey_cali") == 1
    assert rig.ledger["depurar"] == 2  # the survey content changed: exactly +1 recompute


def test_a_changed_newest_timestamp_with_the_same_count_costs_exactly_one_scan(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _warm(rig)
    rig.stores["survey_cali"]["g3"] = {"nombre_evaluador": "Renombrado", "_updated_at": T0 + timedelta(days=2)}
    before = _snap(rig, *SURVEY_KEYS)
    rig.clock.advance(SURVEY_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:survey_cali") == 1


def test_a_deleted_survey_document_is_caught_by_the_count(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _warm(rig)
    del rig.stores["survey_cali"]["g10"]
    before = _snap(rig, *SURVEY_KEYS)
    rig.clock.advance(SURVEY_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:survey_cali") == 1


def test_forced_reconcile_at_the_injected_interval_bounds_the_blind_spot(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    rig.app.state.survey_names_source = ProbedScan(
        name="survey_names", collection="survey_cali", order_field="_updated_at", reconcile_s=2 * HOUR, clock=rig.clock)
    _warm(rig)
    rig.stores["survey_cali"]["g5"]["nombre_evaluador"] = "Edicion silenciosa"  # `_updated_at` untouched
    before = _snap(rig, *SURVEY_KEYS)
    _run(rig, 2 * HOUR - 60, step=300.0)
    assert _delta(rig, before, "scan:survey_cali") == 0  # inside the interval the edit is invisible
    _run(rig, 2 * HOUR, step=300.0)
    assert _delta(rig, before, "scan:survey_cali") == 1  # the reconcile found it
    assert rig.ledger["depurar"] == 2


def test_the_default_reconcile_is_six_hours(monkeypatch):
    rig = Rig(monkeypatch)
    assert rig.app.state.survey_names_source._reconcile_s == DEFAULT_RECONCILE_S == 6 * HOUR
    assert rig.app.state.evaluaciones_source._reconcile_s == 6 * HOUR


def test_survey_probe_failure_serves_stale_no_scan_no_crash_at_most_one_retry_per_ttl(monkeypatch, caplog):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _wide_grace(rig)
    first = rig.get()
    rig.db.fail_on.add(("count", "survey_cali"))
    before = _snap(rig, *SURVEY_KEYS)
    rig.clock.advance(SURVEY_TTL + 1)
    with caplog.at_level(logging.DEBUG):
        for _ in range(10):
            body = rig.get()
    assert body["depuracion"] == first["depuracion"]  # last-good names
    assert _delta(rig, before, "scan:survey_cali") == 0
    assert _delta(rig, before, "attempt:count:survey_cali") == 1  # the single failed retry
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in text  # the type is logged ...
    assert "429" not in text and "Evaluador" not in text  # ... the message and the data never are


def test_count_unsupported_keeps_working_and_refreshes_at_the_reconcile(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _wide_grace(rig)
    _warm(rig)
    rig.db.count_unsupported = True
    rig.stores["survey_cali"]["gnew"] = {"nombre_evaluador": "Nuevo Nombre", "_updated_at": T0 + timedelta(days=1)}
    rig.clock.advance(SURVEY_TTL + 1)
    assert rig.get()["depuracion"]["activa"] is True  # serve-stale, no crash
    assert rig.ledger["scan:survey_cali"] == 1
    _run(rig, 6 * HOUR, step=600.0)
    assert rig.ledger["scan:survey_cali"] == 2  # refreshed by the reconcile, never blind forever


def test_count_unsupported_with_the_default_policy_refreshes_at_the_first_refresh_past_the_ttl_not_the_reconcile(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _warm(rig)
    rig.db.count_unsupported = True
    rig.stores["survey_cali"]["gnew"] = {"nombre_evaluador": "Nuevo Nombre", "_updated_at": T0 + timedelta(days=1)}
    rig.clock.advance(SURVEY_TTL + 1)
    assert rig.get()["depuracion"]["activa"] is True
    assert rig.ledger["scan:survey_cali"] == 2  # the dead probe fell through to the scan: never a 6 h blind window
    _run(rig, 6 * HOUR, step=600.0)
    assert rig.ledger["scan:survey_cali"] <= 2 + 6 + 1  # a permanently dead probe costs the pre-D34 rate at most


def test_cold_start_with_a_dead_probe_still_serves(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    rig.db.count_unsupported = True
    body = rig.get()
    assert body["depuracion"]["activa"] is True and rig.ledger["scan:survey_cali"] == 1


def test_an_empty_survey_collection_is_a_valid_static_state(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    rig.stores["survey_cali"] = {}
    _warm(rig)
    before = _snap(rig, *SURVEY_KEYS)
    rig.clock.advance(SURVEY_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:survey_cali") == 0 and _delta(rig, before, "newest:survey_cali") == 0
    assert _delta(rig, before, "reads:survey_cali") == 1  # one count read, no newest query


def test_survey_documents_without_updated_at_still_work_through_the_count(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    rig.stores["survey_cali"] = {f"g{i}": {"nombre_evaluador": f"N {i}"} for i in range(20)}
    _warm(rig)
    before = _snap(rig, *SURVEY_KEYS)
    rig.clock.advance(SURVEY_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:survey_cali") == 0
    rig.stores["survey_cali"]["extra"] = {"nombre_evaluador": "Sin sello"}
    rig.clock.advance(SURVEY_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:survey_cali") == 1


def test_a_backwards_clock_never_crashes_the_survey_component(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    rig.clock.t = 50_000.0
    _warm(rig)
    rig.clock.t = 100.0  # the clock jumped back hours
    assert rig.get()["depuracion"]["activa"] is True
    assert rig.get()["depuracion"]["activa"] is True


def test_twenty_threads_at_survey_expiry_run_one_probe_and_no_scan_when_unchanged(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _warm(rig)
    rig.clock.advance(SURVEY_TTL + 1)
    before = _snap(rig, *SURVEY_KEYS)
    barrier = threading.Barrier(20)
    statuses: list[int] = []

    def worker():
        barrier.wait(timeout=10)
        statuses.append(rig.client.get("/stickers-atencionsismo", params=OPT_IN).status_code)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert statuses == [200] * 20
    assert _delta(rig, before, "count:survey_cali") == 1  # ONE probe, never twenty
    assert _delta(rig, before, "scan:survey_cali") == 0


def test_twenty_threads_at_survey_expiry_with_a_changed_collection_scan_at_most_once(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _warm(rig)
    rig.stores["survey_cali"]["gnew"] = {"nombre_evaluador": "Otro", "_updated_at": T0 + timedelta(days=1)}
    rig.clock.advance(SURVEY_TTL + 1)
    before = _snap(rig, *SURVEY_KEYS)
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait(timeout=10)
        rig.client.get("/stickers-atencionsismo", params=OPT_IN)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert _delta(rig, before, "scan:survey_cali") == 1
    assert _delta(rig, before, "count:survey_cali") == 1


# ── Firestore evaluaciones side ─────────────────────────────────────────────


def test_evaluaciones_static_hour_pays_four_probes_and_no_scan_and_no_np_lookups(monkeypatch):
    rig = _live_rig(monkeypatch)
    _warm(rig)
    assert rig.ledger["scan:evaluaciones"] == 1
    before = _snap(rig, *EVAL_KEYS, "get_all_calls", "reads:inspectores")
    _run(rig, HOUR)
    probes = _delta(rig, before, "count:evaluaciones")
    assert _delta(rig, before, "scan:evaluaciones") == 0
    assert 3 <= probes <= 4  # the 15-minute component only refreshes on a walk rebuild: at most 4 an hour
    assert _delta(rig, before, "reads:evaluaciones") == probes * 3  # 2 + 1 per probe, instead of 4 x 1,470
    assert _delta(rig, before, "get_all_calls") == 0
    assert rig.ledger["depurar"] == 1


def test_a_whole_open_day_of_static_evaluaciones_scans_only_at_cold_start_and_the_reconcile(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=60, u=10, i=10)
    _warm(rig)
    _run(rig, 8 * HOUR, step=300.0)
    assert rig.ledger["scan:evaluaciones"] == 2  # cold + reconcile
    assert rig.ledger["reads:evaluaciones"] <= 2 * 60 + 33 * 3


def test_a_new_evaluacion_costs_exactly_one_scan_at_the_next_refresh_and_shows_up(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    _warm(rig)
    before = _snap(rig, *EVAL_KEYS)
    rig.stores["evaluaciones"]["ev-new"] = {
        "inspector": {"uid": "u1"}, "codigo_edificacion": "C-new", "timestamp": T0 + timedelta(days=3)}
    rig.clock.advance(EVALS_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:evaluaciones") == 1
    assert "C-new" in {e["codigo_edificacion"] for e in rig.client.get("/evaluaciones").json()["evaluaciones"]}


def test_a_deleted_evaluacion_is_caught_by_the_count(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    _warm(rig)
    del rig.stores["evaluaciones"]["ev-7"]
    before = _snap(rig, *EVAL_KEYS)
    rig.clock.advance(EVALS_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:evaluaciones") == 1


def test_an_evaluaciones_edit_without_a_timestamp_bump_is_a_documented_blind_spot_closed_by_the_reconcile(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    _warm(rig)
    rig.stores["evaluaciones"]["ev-3"]["comentarios"] = "editado en consola"
    before = _snap(rig, *EVAL_KEYS)
    rig.clock.advance(EVALS_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:evaluaciones") == 0
    _run(rig, 6 * HOUR + 600, step=600.0)
    assert _delta(rig, before, "scan:evaluaciones") == 1


def test_a_changed_roster_np_forces_an_evaluaciones_rescan_so_np_is_never_stale_beyond_the_roster(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    _warm(rig)
    rig.stores["inspectores"]["u1"]["NP"] = "P4"
    from app.services.roster_invalidation import invalidate_inspectores_roster

    invalidate_inspectores_roster(rig.app.state)
    before = _snap(rig, *EVAL_KEYS)
    rig.clock.advance(EVALS_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:evaluaciones") == 1  # the roster version is part of the signature
    nps = {e["id"]: e["inspector"]["np"] for e in rig.client.get("/evaluaciones").json()["evaluaciones"]}
    assert nps["ev-1"] == "P4"


def test_an_unchanged_roster_refresh_does_not_rescan_evaluaciones(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    _warm(rig)
    before = _snap(rig, *EVAL_KEYS)
    rig.clock.advance(ROSTER_TTL + 1)  # the roster is re-read, with identical content
    rig.get()
    assert _delta(rig, before, "scan:evaluaciones") == 0


def test_evaluaciones_probe_failure_serves_stale_and_retries_at_most_once_per_ttl(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    _wide_grace(rig)
    first = rig.get()
    rig.db.fail_on.add(("count", "evaluaciones"))
    before = _snap(rig, *EVAL_KEYS, "attempt:count:evaluaciones")
    rig.clock.advance(EVALS_TTL + 1)
    for _ in range(10):
        body = rig.get()
    assert len(body["evaluaciones"]) == len(first["evaluaciones"])
    assert _delta(rig, before, "scan:evaluaciones") == 0
    assert _delta(rig, before, "attempt:count:evaluaciones") <= 3  # 5-min list cache + 15-min component, backed off


def test_count_unsupported_on_evaluaciones_never_blocks_the_stickers_tab(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    rig.db.count_unsupported = True
    assert rig.get()["depuracion"]["activa"] is True  # cold: probes are best effort
    rig.clock.advance(EVALS_TTL + 1)
    assert rig.get()["depuracion"]["activa"] is True


def test_empty_evaluaciones_collection_is_a_valid_static_state(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=0, u=1, i=3)
    _warm(rig)
    before = _snap(rig, *EVAL_KEYS)
    rig.clock.advance(EVALS_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:evaluaciones") == 0


def test_evaluaciones_without_a_timestamp_are_counted_but_never_break_the_probe(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    for doc in rig.stores["evaluaciones"].values():
        doc.pop("timestamp")
    _warm(rig)
    before = _snap(rig, *EVAL_KEYS)
    rig.clock.advance(EVALS_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:evaluaciones") == 0


def test_get_evaluaciones_route_and_the_stickers_walk_share_one_probe_source(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    rig.client.get("/evaluaciones")  # cold: the ROUTE scans once
    assert rig.ledger["scan:evaluaciones"] == 1
    rig.get()  # the walk reuses the shared list cache
    assert rig.ledger["scan:evaluaciones"] == 1
    before = _snap(rig, *EVAL_KEYS)
    rig.clock.advance(stickers.EVALUACIONES_CACHE_TTL_SECONDS + 1)
    rig.client.get("/evaluaciones")
    assert _delta(rig, before, "scan:evaluaciones") == 0 and _delta(rig, before, "count:evaluaciones") == 1


def test_twenty_threads_at_evaluaciones_expiry_run_one_probe_and_no_scan(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    _warm(rig)
    rig.clock.advance(EVALS_TTL + 1)
    before = _snap(rig, *EVAL_KEYS)
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait(timeout=10)
        rig.client.get("/stickers-atencionsismo", params=OPT_IN)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert _delta(rig, before, "count:evaluaciones") == 1
    assert _delta(rig, before, "scan:evaluaciones") == 0


def test_the_evaluaciones_output_is_unchanged_by_the_probe(monkeypatch):
    """Same served payload with the probe as a rebuilt-from-scratch scan."""
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    warm = rig.get()
    rig.clock.advance(EVALS_TTL + 1)
    after_probe = rig.get()
    assert after_probe["evaluaciones"] == warm["evaluaciones"]
    fresh = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    assert fresh.get()["evaluaciones"] == warm["evaluaciones"]


def test_no_pii_in_any_log_line_of_the_probed_paths(monkeypatch, caplog):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    rig.stores["inspectores"]["u1"]["nombre_completo"] = "Ana Perez 1020304050"
    with caplog.at_level(logging.DEBUG):
        rig.get()
        rig.db.fail_on.update({("count", "survey_cali"), ("count", "evaluaciones")})
        rig.clock.advance(EVALS_TTL + SURVEY_TTL + 1)
        rig.get()
        rig.get()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "1020304050" not in text and "Ana Perez" not in text and "Evaluador" not in text


# ── C1: a probe-only failure must not freeze the served body until the reconcile ──


def _new_eval(rig: Rig, key: str = "ev-new", code: str = "C-new") -> None:
    rig.stores["evaluaciones"][key] = {
        "inspector": {"uid": "u1"}, "codigo_edificacion": code, "timestamp": T0 + timedelta(days=3)}


def _codes(rig: Rig) -> tuple[set, bool]:
    body = rig.client.get("/evaluaciones").json()
    return {e["codigo_edificacion"] for e in body["evaluaciones"]}, body["degraded"]


def test_the_production_sources_carry_their_component_ttl_as_the_probe_failure_grace(monkeypatch):
    rig = Rig(monkeypatch)
    assert rig.app.state.survey_names_source._probe_failure_grace_s == SURVEY_TTL
    assert rig.app.state.evaluaciones_source._probe_failure_grace_s == EVALS_TTL
    assert stickers.EVALUACIONES_PROBE_FAILURE_GRACE_S == EVALS_TTL
    assert rig.app.state.evaluaciones_source._max_probe_failures == 2


def test_c1_repro_a_dead_count_never_freezes_the_evaluaciones_body_until_the_reconcile(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    codes, _ = _codes(rig)
    assert "C-new" not in codes
    rig.db.fail_on.add(("count", "evaluaciones"))  # plain scans keep working; only the aggregation dies
    _new_eval(rig)
    created = rig.clock.t
    seen_after: float | None = None
    scans_before = rig.ledger["scan:evaluaciones"]
    for _ in range(35):  # a tab polling every 5 minutes for ~3 h (the repro polled ~6 h)
        rig.clock.advance(STICKER_TTL + 1)
        codes, degraded = _codes(rig)
        assert degraded is False  # a plain serve-stale never claims to be degraded
        if seen_after is None and "C-new" in codes:
            seen_after = rig.clock.t - created
    assert seen_after is not None and seen_after <= STICKER_TTL + EVALS_TTL  # TTL + grace, not 6 h
    assert rig.ledger["scan:evaluaciones"] - scans_before <= 35 // 2 + 1  # a dead probe: a scan every 2nd refresh


def test_a_single_transient_count_failure_on_evaluaciones_stays_cheap(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    _codes(rig)
    scans_before = rig.ledger["scan:evaluaciones"]
    for _ in range(6):
        rig.db.fail_on.add(("count", "evaluaciones"))
        rig.clock.advance(STICKER_TTL + 1)
        _codes(rig)  # one failed refresh, served stale
        rig.db.fail_on.clear()
        rig.clock.advance(STICKER_TTL + 1)
        _codes(rig)  # recovered: unchanged
    assert rig.ledger["scan:evaluaciones"] == scans_before


def test_probe_and_scan_both_failing_on_evaluaciones_serves_the_last_good_body(monkeypatch, caplog):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    first, _ = _codes(rig)
    rig.db.fail_on.update({("count", "evaluaciones"), ("scan", "evaluaciones")})
    _new_eval(rig)
    with caplog.at_level(logging.DEBUG):
        for _ in range(6):
            rig.clock.advance(STICKER_TTL + 1)
            codes, degraded = _codes(rig)
            assert codes == first and degraded is False  # last-good, exactly as before
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "429" not in text and "Insp " not in text
    rig.db.fail_on.clear()
    rig.clock.advance(STICKER_TTL + 1)
    assert "C-new" in _codes(rig)[0]  # recovered


def test_survey_side_a_dead_count_falls_back_to_the_scan_at_the_first_refresh_past_the_ttl(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _warm(rig)
    rig.db.fail_on.add(("count", "survey_cali"))
    rig.stores["survey_cali"]["gnew"] = {"nombre_evaluador": "Ana Gomez", "_updated_at": T0 + timedelta(days=1)}
    before = _snap(rig, *SURVEY_KEYS)
    rig.clock.advance(SURVEY_TTL + 1)  # the grace is the survey TTL: the failing probe cannot stand in for the scan
    body = rig.get()
    assert body["depuracion"]["activa"] is True
    assert _delta(rig, before, "scan:survey_cali") == 1
    assert rig.ledger["depurar"] == 2  # the new name reached the depuracion


# ── W4: only the newest document's own timestamp moves (same id, same count) ──


def test_the_newest_evaluacion_edited_again_moves_only_its_timestamp_and_costs_exactly_one_scan(monkeypatch):
    rig = _live_rig(monkeypatch, s=20, e=30, u=10, i=10)
    _warm(rig)
    newest = max(rig.stores["evaluaciones"], key=lambda k: rig.stores["evaluaciones"][k]["timestamp"])
    count_before = len(rig.stores["evaluaciones"])
    rig.stores["evaluaciones"][newest]["timestamp"] = T0 + timedelta(days=9)  # same id, same count
    assert len(rig.stores["evaluaciones"]) == count_before
    assert max(rig.stores["evaluaciones"], key=lambda k: rig.stores["evaluaciones"][k]["timestamp"]) == newest
    before = _snap(rig, *EVAL_KEYS)
    rig.clock.advance(EVALS_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:evaluaciones") == 1
    rig.clock.advance(EVALS_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:evaluaciones") == 1  # and it settles


def test_the_newest_survey_document_edited_again_moves_only_its_timestamp_and_costs_exactly_one_scan(monkeypatch):
    rig = _live_rig(monkeypatch, e=10, u=5, i=5)
    _warm(rig)
    newest = max(rig.stores["survey_cali"], key=lambda k: rig.stores["survey_cali"][k]["_updated_at"])
    rig.stores["survey_cali"][newest] = {"nombre_evaluador": "Renombrado", "_updated_at": T0 + timedelta(days=9)}
    before = _snap(rig, *SURVEY_KEYS)
    rig.clock.advance(SURVEY_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:survey_cali") == 1
    rig.clock.advance(SURVEY_TTL + 1)
    rig.get()
    assert _delta(rig, before, "scan:survey_cali") == 1
