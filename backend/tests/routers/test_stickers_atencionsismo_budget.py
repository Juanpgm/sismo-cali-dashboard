"""Efficiency budgets of `GET /stickers-atencionsismo`, asserted as COUNTS at
the route with the shared ledger fakes (design "Efficiency Acceptance Budgets",
tasks 10b.14-10b.27, efficiency doc "Tests that enforce the budgets" 1-9 and 11).

These are the flag-flip evidence: the real app, every upstream a counting fake
(walk, evaluaciones scan, roster scan, survey scan, `depurar`, referencia GET,
Blob PUT, body encode, gzip), every cache on one injectable clock. The unit
mechanisms are tested elsewhere and are NOT re-tested here. The only real time
is the latency test (in-process) and the event-gated threaded ones."""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import date

import pytest

from app.routers import stickers_atencionsismo as router_mod
from app.services.inspectores_referencia import ReferenciaBundle
from tests.routers.test_stickers_atencionsismo_components import (
    EVALS_TTL, OPT_IN, REF_TTL, ROSTER_TTL, STICKER_TTL, SURVEY_TTL, Rig, _referencia,
)
from tests.routers.test_stickers_atencionsismo_http import _as, _req
from tests.routers.test_stickers_atencionsismo_optin import DEPURACION_BUDGET_BYTES, _universe_of_400
from tests.routers.test_stickers import FAKE_CLAIMS_INSTITUCIONAL

SCANS = ("walk", "roster", "survey", "evals", "depurar", "referencia")


@pytest.fixture
def rig(monkeypatch):
    return Rig(monkeypatch)


def _delta(rig: Rig, before: dict[str, int]) -> dict[str, int]:
    return {k: v - before[k] for k, v in rig.counts().items()}


def _snapshot(rig: Rig) -> dict[str, int]:
    return {**rig.counts(), "put": rig.puts(), "encode": rig.ledger["encode"]}


def _diff(rig: Rig, before: dict[str, int]) -> dict[str, int]:
    now = _snapshot(rig)
    return {k: now[k] - before[k] for k in now}


def _bulk_rows(n: int) -> list[dict]:
    """Realistic-weight sticker rows (photos, address, names), like the live ~1470."""
    return [{
        "id": f"ev-{i:05d}", "direccion": f"Carrera {i % 90} # {i % 70}-{i % 40} Barrio San Antonio Comuna {i % 22 + 1}",
        "latitud": f"3.{4500 + i % 500}", "longitud": f"-76.{5000 + i % 500}", "numero": f"76001-1-{i:07d}",
        "personaAfectada": f"Persona Afectada Numero {i}", "origen": "firebase" if i % 2 else "sistema",
        "color": ("rojo", "amarillo", "verde")[i % 3], "colorEtiqueta": ("No habitable", "Restringido", "Habitable")[i % 3],
        "fechaCreacion": f"{i % 28 + 1:02d}/09/2026 08:30:00", "barrio": "San Antonio", "comuna": str(i % 22 + 1),
        "fotografias": [{"id": f"f{i}-{j}", "url": f"https://blob.example.com/fotos/{i:05d}/foto-{j}-{'x' * 40}.jpg"}
                        for j in range(3)],
    } for i in range(n)]


# ── 1. Fifty requests inside the TTLs ───────────────────────────────────────


def test_budget_fifty_requests_within_ttl(rig):
    etags = {_req(rig).headers["etag"] for _ in range(50)}
    assert len(etags) == 1
    assert rig.counts() == {"walk": 1, "roster": 1, "survey": 1, "evals": 1, "depurar": 1, "referencia": 1}
    assert 1 <= rig.puts() <= 2  # cold start: at most one Blob copy per sticker cache
    assert rig.ledger["encode"] == 1


def test_budget_ttl_boundary_exactly_at_the_ttl_is_still_fresh(rig):
    _req(rig)
    before = _snapshot(rig)
    rig.clock.advance(STICKER_TTL)  # exactly at the boundary
    _req(rig)
    assert _diff(rig, before) == {k: 0 for k in before}  # nothing at all: no walk, scan, compute, PUT or encode
    rig.clock.advance(1)
    _req(rig)
    assert _diff(rig, before)["walk"] == 1  # one second past: the 5-minute walk is stale


# ── 2. Past the TTL with identical upstream ─────────────────────────────────


def test_budget_past_ttl_identical_upstream(rig):
    first = _req(rig)
    before = _snapshot(rig)
    rig.clock.advance(STICKER_TTL + 1)  # the 5-minute walk is stale; the 15/30/60-minute components are not
    assert STICKER_TTL + 1 < min(EVALS_TTL, ROSTER_TTL, SURVEY_TTL)
    again = _req(rig)
    assert _diff(rig, before) == {"walk": 1, "roster": 0, "survey": 0, "evals": 0, "depurar": 0,
                                  "referencia": 0, "put": 0, "encode": 0}
    assert again.headers["etag"] == first.headers["etag"]
    cond = _req(rig, inm=first.headers["etag"])
    assert cond.status_code == 304 and cond.content == b""


# ── 3. One changed sticker ──────────────────────────────────────────────────


def test_budget_one_changed_sticker(rig):
    first = _req(rig)
    before = _snapshot(rig)
    rig.rows[1]["origen"] = "firebase"  # a consumed field
    rig.clock.advance(STICKER_TTL + 1)
    changed = _req(rig)
    _req(rig)
    d = _diff(rig, before)
    assert d["depurar"] == 1 and d["walk"] == 1 and d["roster"] == d["survey"] == d["evals"] == 0
    assert d["put"] <= 1 and d["encode"] == 1
    assert changed.headers["etag"] != first.headers["etag"]
    assert _req(rig, inm=first.headers["etag"]).status_code == 200  # the old validator is dead


def test_budget_a_sticker_field_depurar_never_reads_costs_no_recompute_but_a_new_etag(rig):
    first = _req(rig)
    rig.rows[0]["direccion"] = "Calle 99"
    rig.clock.advance(STICKER_TTL + 1)
    second = _req(rig)
    assert rig.ledger["depurar"] == 1
    assert second.headers["etag"] != first.headers["etag"]  # the bytes did change


# ── 4. `hoy` rollover ───────────────────────────────────────────────────────


def test_budget_hoy_rollover_only_recomputes(rig):
    first = _req(rig)
    before = _snapshot(rig)
    rig.hoy = date(2026, 9, 20)
    rolled = _req(rig)
    _req(rig)
    assert _diff(rig, before) == {"walk": 0, "roster": 0, "survey": 0, "evals": 0, "depurar": 1,
                                  "referencia": 0, "put": 0, "encode": 1}
    assert rolled.headers["etag"] != first.headers["etag"]


# ── 5. New referencia huella, also a same-day republish ─────────────────────


def test_budget_new_referencia_huella_incl_same_day_republish(rig):
    first = _req(rig)
    rig.clock.advance(REF_TTL + 1)  # the reference TTL expires; the content is identical
    same = _req(rig)
    assert rig.ledger["referencia"] == 2 and rig.ledger["depurar"] == 1  # identical re-download: +0
    assert same.headers["etag"] == first.headers["etag"]

    rig.clock.advance(REF_TTL + 1)
    rig.referencia = _referencia(huella="h2")  # SAME generado_en, different content
    assert rig.referencia.generado_en == _referencia().generado_en
    republished = _req(rig)
    _req(rig)
    assert rig.ledger["referencia"] == 3 and rig.ledger["depurar"] == 2  # exactly +1
    assert republished.headers["etag"] != first.headers["etag"]


# ── 6. Twenty threads ───────────────────────────────────────────────────────


def _hammer(rig: Rig, n: int = 20, *, ae: str = "identity"):
    barrier = threading.Barrier(n)
    out: list = []
    lock = threading.Lock()

    def worker():
        barrier.wait(timeout=10)
        resp = _req(rig, ae=ae)
        with lock:
            out.append(resp)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return out


def test_budget_20_threads_at_cold_start(rig):
    out = _hammer(rig)
    assert len(out) == 20 and all(r.status_code == 200 for r in out)
    assert rig.counts() == {"walk": 1, "roster": 1, "survey": 1, "evals": 1, "depurar": 1, "referencia": 1}
    assert len({r.content for r in out}) == 1 and len({r.headers["etag"] for r in out}) == 1  # all equal
    assert rig.ledger["encode"] == 1


def test_budget_20_threads_at_expiry(rig):
    _req(rig)
    rig.rows[0]["origen"] = "sistema"  # a real change waiting behind the expiry
    rig.clock.advance(max(SURVEY_TTL, ROSTER_TTL, REF_TTL) + 1)  # every component expires at once
    before = _snapshot(rig)
    out = _hammer(rig)
    d = _diff(rig, before)
    assert len(out) == 20 and all(r.status_code == 200 for r in out)
    assert d["walk"] == 1 and d["depurar"] == 1 and d["survey"] <= 1 and d["roster"] <= 1 and d["evals"] <= 1
    assert len({r.content for r in out}) == 1 and len({r.headers["etag"] for r in out}) == 1
    assert d["encode"] == 1


# ── 7. Slow referencia never blocks the fast path ───────────────────────────


def test_budget_slow_cargar_referencia_does_not_block_fast_path(rig):
    first = _req(rig)
    inside, release = threading.Event(), threading.Event()
    real_loader = rig.app.state.depuracion_cache._cargar_referencia

    def slow_loader():
        inside.set()
        assert release.wait(timeout=10)
        return real_loader()

    rig.app.state.depuracion_cache._cargar_referencia = slow_loader
    rig.clock.advance(REF_TTL + 1)  # the reference TTL is stale: the next request downloads
    leader_out: list = []
    leader = threading.Thread(target=lambda: leader_out.append(_req(rig)))
    leader.start()
    try:
        assert inside.wait(timeout=10)  # the leader is parked inside the download
        fast: list = []
        follower = threading.Thread(target=lambda: fast.append(_req(rig, inm=first.headers["etag"])))
        follower.start()
        follower.join(timeout=5)
        assert not follower.is_alive(), "a request waited behind the slow referencia download"
        assert fast[0].status_code == 304  # last-good reference: same key, same bytes
    finally:
        release.set()
        leader.join(timeout=10)
    assert leader_out[0].status_code == 200 and leader_out[0].headers["etag"] == first.headers["etag"]


# ── 8. Referencia failure keeps last-good ───────────────────────────────────


@pytest.mark.parametrize("how", ["raises", "degraded_bundle"])
def test_budget_referencia_failure_keeps_last_good(rig, how):
    first = _req(rig)
    good = first.json()["depuracion"]
    assert good["activa"] is True
    if how == "raises":
        rig.fail.add("referencia")
    else:
        rig.referencia = ReferenciaBundle.vacia(motivo="blob caido")
    rig.clock.advance(REF_TTL + 1)
    before = _snapshot(rig)
    for _ in range(10):
        resp = _req(rig)
    d = _diff(rig, before)
    assert d["depurar"] == 0 and d["referencia"] == 1  # no recompute, one retry per TTL
    assert resp.json()["depuracion"] == good  # the response still carries the last-good table
    assert resp.headers["etag"] == first.headers["etag"]
    rig.clock.advance(REF_TTL + 1)
    _req(rig)
    assert _diff(rig, before)["referencia"] == 2  # and exactly one more the next TTL


def test_budget_referencia_recovering_with_identical_content_costs_no_recompute(rig):
    _req(rig)
    rig.fail.add("referencia")
    rig.clock.advance(REF_TTL + 1)
    _req(rig)
    rig.fail.discard("referencia")
    rig.clock.advance(REF_TTL + 1)
    _req(rig)
    assert rig.ledger["depurar"] == 1


# ── 9. Viewer or missing param costs zero ───────────────────────────────────


def test_budget_viewer_or_missing_param_costs_zero(rig):
    def zero_work():
        assert (rig.ledger["depurar"], rig.ledger["survey"], rig.ledger["referencia"]) == (0, 0, 0)

    for _ in range(5):
        resp = _req(rig, None)  # admin, no param
        assert b"depuracion" not in resp.content and b"revision_manual" not in resp.content
    zero_work()
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
    for _ in range(5):
        resp = _req(rig, OPT_IN)  # viewer WITH the param
        assert b"depuracion" not in resp.content and b"revision_manual" not in resp.content
    zero_work()
    assert all(key[1] == router_mod.DEPURACION_VERSION_NONE for key in rig.app.state.encoded_bodies.retained_keys())


# ── 11. Encoding and ceilings ───────────────────────────────────────────────


def test_budget_gzip_content_encoding_when_allowed(rig):
    identity = _req(rig, ae="identity")
    unzipped = [_req(rig, ae=ae) for ae in ("gzip;q=0", "identity", "br", "")]
    zipped = [_req(rig, ae=ae) for ae in ("gzip", "GZIP", "br, gzip") * 10]
    assert all("content-encoding" not in r.headers and r.content == identity.content for r in unzipped)
    assert all(r.headers["content-encoding"] == "gzip" and r.content == identity.content for r in zipped)
    assert rig.counts() == {"walk": 1, "roster": 1, "survey": 1, "evals": 1, "depurar": 1, "referencia": 1}
    assert rig.ledger["encode"] == 1 and rig.ledger["gzip"] == 1  # 34 requests: one serialization, one compression
    assert len({r.headers["etag"] for r in zipped}) == 1 and zipped[0].headers["etag"] != identity.headers["etag"]


EVALUACIONES_BUDGET_BYTES = 4 * 1024 * 1024  # design: `evaluaciones` <= 4 MB raw


def _within(name: str, size: int, ceiling: int) -> None:
    """Fails LOUDLY when `size` is over the ceiling: exactly at it passes."""
    assert size <= ceiling, f"{name} is {size} bytes raw: {size - ceiling} over the {ceiling}-byte budget"


def test_ceiling_check_boundary_exactly_at_the_ceiling_passes_one_byte_over_fails_loudly():
    _within("x", DEPURACION_BUDGET_BYTES, DEPURACION_BUDGET_BYTES)
    _within("x", 0, DEPURACION_BUDGET_BYTES)
    with pytest.raises(AssertionError, match=r"1 over the 409600-byte budget"):
        _within("depuracion", DEPURACION_BUDGET_BYTES + 1, DEPURACION_BUDGET_BYTES)
    with pytest.raises(AssertionError, match=r"over the 4194304-byte budget"):
        _within("evaluaciones", EVALUACIONES_BUDGET_BYTES + 1, EVALUACIONES_BUDGET_BYTES)


def _compact(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def test_budget_payload_ceilings_and_gzip_ratio_for_the_live_sized_universe(rig):
    rig.rows[:] = _bulk_rows(1470)
    _universe_of_400(rig)
    plain = _req(rig, ae="identity")
    body = plain.json()
    assert len(body["evaluaciones"]) == 1470 and len(body["depuracion"]["inspectores"]) == 400
    evaluaciones, depuracion = _compact(body["evaluaciones"]), _compact(body["depuracion"])
    assert evaluaciones > 500_000 and depuracion > 100_000, "fixtures too small to say anything about the budgets"
    _within("evaluaciones", evaluaciones, EVALUACIONES_BUDGET_BYTES)
    _within("depuracion", depuracion, DEPURACION_BUDGET_BYTES)

    gz = _req(rig, ae="gzip")
    raw, wire = len(plain.content), int(gz.headers["content-length"])
    assert wire < raw
    print(f"payload: evaluaciones {evaluaciones} B, depuracion {depuracion} B, body {raw} B raw / {wire} B gzip "
          f"({wire / raw:.1%}; target ~15%, recorded at parity, not asserted)")


# ── Latency of a cache hit ──────────────────────────────────────────────────


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def test_latency_p95_of_a_304_and_of_a_200_from_precomputed_bytes(rig):
    rig.rows[:] = _bulk_rows(1470)
    _universe_of_400(rig)
    warm = _req(rig, ae="gzip")  # builds and compresses once
    etag = warm.headers["etag"]
    n304, n200 = 60, 60

    def timed(**kw) -> float:
        t0 = time.perf_counter()
        resp = _req(rig, **kw)
        elapsed = time.perf_counter() - t0
        return elapsed if resp.status_code in (200, 304) else float("inf")

    for _ in range(5):  # warm the interpreter paths
        timed(ae="gzip", inm=etag)
        timed(ae="gzip")
    hits_304 = [timed(ae="gzip", inm=etag) for _ in range(n304)]
    hits_200 = [timed(ae="gzip") for _ in range(n200)]

    p304, p200 = _p95(hits_304), _p95(hits_200)
    print(f"latency p95: 304 = {p304 * 1000:.1f} ms (budget 50), 200 = {p200 * 1000:.1f} ms (budget 250)")
    assert len(hits_304) >= 50 and len(hits_200) >= 50
    assert p304 <= 0.050 and p200 <= 0.250
    assert rig.ledger["encode"] == 1 and rig.ledger["gzip"] == 1  # every sample was a bytes hit


# ── Blob PUT ────────────────────────────────────────────────────────────────


def test_blob_put_zero_when_content_unchanged_and_exactly_one_when_changed(rig):
    _req(rig)
    assert rig.puts() == 2  # cold start: one copy per sticker cache
    rig.clock.advance(STICKER_TTL + 1)
    _req(rig)  # identical upstream refetch
    assert rig.puts() == 2  # 0 PUTs: the redacted-copy hash gates it

    rig.rows[0]["origen"] = "sistema"  # `origen` is on the public allowlist: the redacted copy changes
    rig.clock.advance(STICKER_TTL + 1)
    _req(rig)
    assert rig.puts() == 3  # exactly one
    rig.clock.advance(STICKER_TTL + 1)
    _req(rig)
    assert rig.puts() == 3  # and it converges again


def test_a_failed_blob_put_never_fails_the_request_and_is_retried_on_the_next_change_of_state(rig, monkeypatch):
    monkeypatch.setattr(router_mod.stickers.blob_lkg, "save_json", lambda *_a: rig.ledger.hit("put") or False)
    assert _req(rig).status_code == 200  # fire-and-forget: the upload failed, the response did not
    assert rig.puts() == 2
    rig.clock.advance(STICKER_TTL + 1)
    assert _req(rig).status_code == 200
    assert rig.puts() >= 3  # the hash only advances on a confirmed upload, so it is retried


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_raising_blob_put_never_fails_the_request(rig, monkeypatch):
    def explode(*_a):
        rig.ledger.hit("put")
        raise RuntimeError("blob 500")

    monkeypatch.setattr(router_mod.stickers.blob_lkg, "save_json", explode)
    resp = _req(rig)  # the upload runs on a daemon thread: its crash cannot reach the request
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert rig.puts() == 2
