"""HTTP encoding of `GET /stickers-atencionsismo` (design D26/D27, tasks
10b.1-10b.12): the encoded-bytes cache, ETag / 304 and gzip.

The response BODY stays what it was (the 09a golden tests keep guarding that);
only headers are additive. Same rig as the component tests: the real app, every
upstream a counting fake, every cache on one injectable clock. The only spies
added here count body serializations and compressions. No real sleeps."""
from __future__ import annotations

import copy
import logging
import re
import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.routers import stickers_atencionsismo as router_mod
from app.services import atencionsismo as atencionsismo_mod
from app.services import versioned_cache
from tests.routers.test_stickers import FAKE_CLAIMS_ADMIN, FAKE_CLAIMS_INSTITUCIONAL, FAKE_CLAIMS_VIEWER
from tests.routers.test_stickers_atencionsismo_components import OPT_IN, STICKER_TTL, Rig, _referencia
from tests.routers.test_stickers_atencionsismo_optin import (
    CEDULA, CORREO, GOLDEN_BODY, NOMBRE, TARJETA, TELEFONO, URL,
    _force_degraded, _recover, _rig_with_fixed_payload, _secret_roster,
)

ETAG_IDENTITY = re.compile(r'"[0-9a-f]{32}"')
ETAG_GZIP = re.compile(r'"[0-9a-f]{32}-gzip"')


@pytest.fixture
def rig(monkeypatch):
    return Rig(monkeypatch)


@pytest.fixture
def spy(rig):
    """The rig; its ledger already counts body serializations (`encode`) and
    compressions (`gzip`)."""
    return rig


def _req(rig, params=OPT_IN, *, ae="identity", inm=None, client=None):
    headers = {} if ae is None else {"Accept-Encoding": ae}
    if inm is not None:
        headers["If-None-Match"] = inm
    return (client or rig.client).get(URL, params=params, headers=headers)


def _bare_client(rig, **kw) -> TestClient:
    """A client that sends NO `Accept-Encoding` at all (httpx adds one by default)."""
    client = TestClient(rig.app, **kw)
    client.headers.pop("accept-encoding", None)
    return client


def _as(rig, claims) -> None:
    rig.app.dependency_overrides[current_claims] = lambda: claims


def _tokens(value: str) -> set[str]:
    return {t.strip().lower() for t in value.split(",") if t.strip()}


def _assert_cache_headers(resp) -> None:
    assert resp.headers["cache-control"] == "no-store"
    assert {"authorization", "accept-encoding"} <= _tokens(resp.headers["vary"])


# ── 10b.1-10b.2 Encoded bytes cache ─────────────────────────────────────────


def test_encoded_body_serialized_once_per_key(spy):
    bodies = [_req(spy).content for _ in range(50)]
    assert spy.ledger["encode"] == 1 and spy.ledger["gzip"] == 0
    assert len(set(bodies)) == 1 and b'"depuracion"' in bodies[0]


def test_a_changed_key_serializes_exactly_once_more(spy):
    _req(spy)
    spy.rows[0]["origen"] = "sistema"  # a consumed field: new snapshot AND new depuracion
    spy.clock.advance(STICKER_TTL + 1)
    for _ in range(5):
        _req(spy)
    assert spy.ledger["encode"] == 2


def test_only_the_current_key_of_each_variant_is_retained(spy):
    cache = spy.app.state.encoded_bodies
    for i in range(4):
        spy.rows[0]["origen"] = "sistema" if i % 2 == 0 else "firebase"
        spy.rows[0]["id"] = f"ev-{i}"
        spy.clock.advance(STICKER_TTL + 1)
        _req(spy)
    assert len(cache.retained_keys()) == 1  # the three superseded keys were dropped, not accumulated
    _req(spy, None)
    _as(spy, FAKE_CLAIMS_INSTITUCIONAL)
    _req(spy, None)
    assert len(cache.retained_keys()) == 3  # admin opt-in, admin plain, viewer: the whole reachable set


def test_bytes_cache_keys_are_isolated_by_role_degraded_and_opt_in(spy):
    admin_optin = _req(spy, OPT_IN)
    admin_plain = _req(spy, None)
    _as(spy, FAKE_CLAIMS_INSTITUCIONAL)
    viewer = _req(spy, OPT_IN)
    viewer_plain = _req(spy, None)

    assert "depuracion" in admin_optin.json()  # opt-in bytes exist ...
    assert "depuracion" not in admin_plain.json() and "depuracion" not in viewer.json()  # ... and never leak
    assert len({admin_optin.headers["etag"], admin_plain.headers["etag"], viewer.headers["etag"]}) == 3
    assert viewer_plain.headers["etag"] == viewer.headers["etag"]  # a viewer's opt-in changes nothing
    assert spy.ledger["encode"] == 3

    _as(spy, FAKE_CLAIMS_ADMIN)
    assert _req(spy, OPT_IN).content == admin_optin.content  # served back from its own slot
    assert spy.ledger["encode"] == 3


def test_degraded_bytes_are_never_served_to_a_live_snapshot(rig, monkeypatch):
    _force_degraded(rig, monkeypatch)
    degraded = _req(rig)
    assert degraded.json()["degraded"] is True
    _recover(rig)
    live = _req(rig)
    assert live.json()["degraded"] is False and len(live.json()["evaluaciones"]) == 2
    assert live.headers["etag"] != degraded.headers["etag"]
    assert _req(rig, inm=degraded.headers["etag"]).status_code == 200  # the old validator no longer matches


@pytest.mark.parametrize("component, other", [
    ("snapshot_version", 2), ("depuracion_version", "b"), ("role", "viewer"), ("degraded", True), ("opt_in", False),
])
def test_etag_depends_on_exactly_the_key_components(component, other):
    def entry(cache, **kw):
        key = {"snapshot_version": 1, "depuracion_version": "a", "role": "admin", "degraded": False, "opt_in": True, **kw}
        return cache.entry(**key, build=lambda: {"ok": True})

    cache = router_mod.EncodedBodyCache()
    base = entry(cache)
    assert entry(cache) is base and entry(cache).etag(False) == base.etag(False)  # same key: same entry
    changed = entry(cache, **{component: other})
    assert changed.etag(False) != base.etag(False) and changed.etag(True) != base.etag(True)


def test_an_etag_from_another_process_lifetime_can_never_validate_a_body():
    """`snapshot_version` is a counter that restarts at 1 on every deploy, so the
    same key names DIFFERENT content across restarts; a per-process nonce keeps a
    pre-deploy validator from matching a post-deploy body."""
    key = dict(snapshot_version=1, depuracion_version="a", role="admin", degraded=False, opt_in=True,
               build=lambda: {"ok": True})
    first, second = router_mod.EncodedBodyCache(), router_mod.EncodedBodyCache()
    assert first.entry(**key).etag(False) != second.entry(**key).etag(False)


def test_encoded_bytes_are_immutable_and_built_once_per_encoding(spy):
    cache = spy.app.state.encoded_bodies
    _req(spy, ae="gzip")
    _req(spy, ae="identity")
    (entry,) = [e for e in cache._entries.values()]
    identity, gz = entry.body(False), entry.body(True)
    assert type(identity) is bytes and type(gz) is bytes
    assert entry.body(False) is identity and entry.body(True) is gz  # the very same objects, never rebuilt
    assert spy.ledger["encode"] == 1 and spy.ledger["gzip"] == 1


def test_serialized_body_is_dropped_from_memory_by_the_entry_once_built(spy):
    """The entry must not keep the payload (and the PII block) alive through its
    builder closure after the bytes exist."""
    _req(spy)
    (entry,) = spy.app.state.encoded_bodies._entries.values()
    assert entry._build is None


# ── 10b.3 Golden identity body and the additive headers ─────────────────────


def test_identity_response_is_the_golden_bytes_plus_only_additive_headers(monkeypatch):
    rig = _rig_with_fixed_payload(monkeypatch)
    resp = _req(rig, None, ae="identity")
    assert resp.status_code == 200 and resp.content == GOLDEN_BODY
    assert resp.headers["content-type"] == "application/json"
    assert "content-encoding" not in resp.headers
    assert ETAG_IDENTITY.fullmatch(resp.headers["etag"])
    _assert_cache_headers(resp)


def test_a_client_that_sends_no_conditional_and_no_accept_encoding_gets_todays_response(monkeypatch):
    rig = _rig_with_fixed_payload(monkeypatch)
    resp = _req(rig, None, ae=None, client=_bare_client(rig))
    assert resp.status_code == 200 and resp.content == GOLDEN_BODY and "content-encoding" not in resp.headers


# ── 10b.4-10b.6 ETag and 304 ────────────────────────────────────────────────


def test_if_none_match_equal_returns_304_empty_body_with_etag(spy):
    first = _req(spy)
    etag = first.headers["etag"]
    encoded = spy.ledger["encode"]
    resp = _req(spy, inm=etag)
    assert resp.status_code == 304 and resp.content == b""
    assert resp.headers["etag"] == etag
    _assert_cache_headers(resp)
    assert "content-length" not in resp.headers or resp.headers["content-length"] == "0"
    assert spy.ledger["encode"] == encoded


def test_a_304_never_needs_the_body_to_have_been_serialized(spy):
    etag = _req(spy).headers["etag"]
    spy.app.state.encoded_bodies._entries.clear()  # bytes dropped, validator (same process) still valid
    assert _req(spy, inm=etag).status_code == 304
    assert spy.ledger["encode"] == 1  # answering 304 built nothing


def test_etag_shape_is_a_quoted_truncated_hash_with_a_suffix_only_for_gzip(spy):
    assert ETAG_IDENTITY.fullmatch(_req(spy, ae="identity").headers["etag"])
    assert ETAG_GZIP.fullmatch(_req(spy, ae="gzip").headers["etag"])


@pytest.mark.parametrize("build, expected", [
    (lambda e: e, 304),                                   # exact
    (lambda e: f"W/{e}", 304),                            # weak comparison
    (lambda e: f'"nope", {e}', 304),                      # a list, match last
    (lambda e: f'{e}, "nope"', 304),                      # a list, match first
    (lambda e: f'"a" ,   {e}  , "b"', 304),               # stray whitespace
    (lambda e: f'W/"nope", W/{e}', 304),                  # a weak list
    # `*` is deliberately NOT honored (design D26, judgment-day W1): it used to be a 304 for any
    # authorised caller, which validated a body the caller never received.
    (lambda e: "*", 200),
    (lambda e: " * ", 200),
    (lambda e: "*, *", 200),
    (lambda e: f"*, {e}", 304),                           # not a valid list, but the tag still matches
    (lambda e: '"nope"', 200),                            # mismatch
    (lambda e: '"nope", "nada"', 200),
    (lambda e: e.strip('"'), 200),                        # unquoted is not an entity-tag
    (lambda e: "garbage", 200),
    (lambda e: "W/", 200),
    (lambda e: '"', 200),
    (lambda e: "", 200),                                  # empty header
    (lambda e: "   ", 200),
    (lambda e: "éñ".encode("utf-8"), 200),      # raw non-ASCII bytes on the wire
    (lambda e: e[:-2] + '"', 200),                        # truncated tag
    (lambda e: "x" * 10_000, 200),                        # absurdly long garbage
])
def test_if_none_match_forms(rig, build, expected):
    etag = _req(rig).headers["etag"]
    resp = _req(rig, inm=build(etag))
    assert resp.status_code == expected
    assert (resp.content == b"") if expected == 304 else (resp.json()["ok"] is True)
    assert resp.headers["etag"] == etag  # a 200 after a mismatch still carries the current validator


def test_if_none_match_split_over_two_header_lines(rig):
    etag = _req(rig).headers["etag"]
    resp = rig.client.get(URL, params=OPT_IN, headers=[("Accept-Encoding", "identity"),
                                                       ("If-None-Match", '"nope"'), ("If-None-Match", etag)])
    assert resp.status_code == 304


def test_a_changed_snapshot_invalidates_the_validator(rig):
    etag = _req(rig).headers["etag"]
    rig.rows[0]["direccion"] = "Calle 99"  # not consumed by depurar(), but part of the served bytes
    rig.clock.advance(STICKER_TTL + 1)
    resp = _req(rig, inm=etag)
    assert resp.status_code == 200 and resp.headers["etag"] != etag
    assert _req(rig, inm=resp.headers["etag"]).status_code == 304


def test_unauthenticated_request_is_401_never_304_even_with_a_valid_or_wildcard_validator(rig):
    etag = _req(rig).headers["etag"]
    walks = rig.ledger["walk"]
    del rig.app.dependency_overrides[current_claims]  # no bearer token at all
    for inm in (etag, "*", f"W/{etag}"):
        resp = _req(rig, inm=inm)
        assert resp.status_code == 401 and resp.headers.get("etag") is None
    assert rig.ledger["walk"] == walks


def test_a_rejected_role_is_403_never_304(rig):
    etag = _req(rig).headers["etag"]
    _as(rig, FAKE_CLAIMS_VIEWER)  # resolves to "otro"
    for inm in (etag, "*"):
        resp = _req(rig, inm=inm)
        assert resp.status_code == 403 and resp.headers.get("etag") is None


def test_a_viewer_sending_the_admins_etag_gets_200_with_the_viewers_own_body(rig):
    admin = _req(rig, OPT_IN)
    assert "depuracion" in admin.json()
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
    for params in (OPT_IN, None):
        resp = _req(rig, params, inm=admin.headers["etag"])
        assert resp.status_code == 200
        assert "depuracion" not in resp.json() and b"revision_manual" not in resp.content
        assert resp.headers["etag"] != admin.headers["etag"]


def test_an_admin_without_opt_in_cannot_be_validated_with_the_opt_in_etag(rig):
    opt_in = _req(rig, OPT_IN).headers["etag"]
    plain = _req(rig, None, inm=opt_in)
    assert plain.status_code == 200 and "depuracion" not in plain.json()


def test_depuracion_changes_move_only_the_opt_in_etag(rig):
    plain, opt_in = _req(rig, None).headers["etag"], _req(rig, OPT_IN).headers["etag"]
    rig.referencia = _referencia(huella="h2")  # same-day republish: depuracion changes, the stickers do not
    rig.clock.advance(router_mod.REFERENCIA_CACHE_TTL_SECONDS + 1)
    assert _req(rig, None).headers["etag"] == plain
    assert _req(rig, OPT_IN).headers["etag"] != opt_in


def test_hoy_rollover_moves_only_the_opt_in_etag(rig):
    plain, opt_in = _req(rig, None).headers["etag"], _req(rig, OPT_IN).headers["etag"]
    rig.hoy = date(2026, 9, 20)
    assert _req(rig, None).headers["etag"] == plain
    assert _req(rig, OPT_IN).headers["etag"] != opt_in


def test_identical_upstream_after_the_ttl_keeps_the_etag(rig):
    etag = _req(rig).headers["etag"]
    rig.clock.advance(STICKER_TTL + 1)
    assert _req(rig, inm=etag).status_code == 304


def test_the_etag_follows_the_failure_outcome_of_the_depuracion(rig, monkeypatch):
    """A `calculo_fallido` block and the recovered table are different bytes and
    must not share a validator."""
    good = _req(rig).headers["etag"]
    working = router_mod.depuracion_svc.depurar

    def boom(**kw):
        raise RuntimeError("engine boom")

    monkeypatch.setattr(router_mod.depuracion_svc, "depurar", boom)
    rig.hoy = date(2026, 9, 20)
    failed = _req(rig)
    assert failed.json()["depuracion"]["motivo"] == "calculo_fallido"
    assert failed.headers["etag"] != good
    assert _req(rig, inm=failed.headers["etag"]).status_code == 304  # same failure, same bytes
    monkeypatch.setattr(router_mod.depuracion_svc, "depurar", working)
    recovered = _req(rig)
    assert recovered.json()["depuracion"]["activa"] is True
    assert recovered.headers["etag"] not in (good, failed.headers["etag"])


def test_an_omitted_depuracion_block_has_its_own_validator(rig):
    rig.fail.add("survey")  # cold start: the component read fails, the key is omitted
    omitted = _req(rig)
    assert "depuracion" not in omitted.json()
    rig.fail.discard("survey")
    rig.clock.advance(router_mod.SURVEY_NAMES_CACHE_TTL_SECONDS + 1)
    recovered = _req(rig)
    assert recovered.json()["depuracion"]["activa"] is True
    assert recovered.headers["etag"] != omitted.headers["etag"]


def test_empty_evaluaciones_still_validates_and_caches(rig):
    rig.rows.clear()
    first = _req(rig, None)
    assert first.json()["evaluaciones"] == []
    assert _req(rig, None, inm=first.headers["etag"]).status_code == 304


# ── 10b.7-10b.9 gzip ────────────────────────────────────────────────────────


def test_gzip_precompressed_once_per_key(spy):
    plain = _req(spy, ae="identity")
    responses = [_req(spy, ae="gzip") for _ in range(50)]
    assert spy.ledger["gzip"] == 1 and spy.ledger["encode"] == 1
    for resp in responses:
        assert resp.headers["content-encoding"] == "gzip"
        assert resp.content == plain.content  # httpx already gunzipped it: same bytes as identity
        assert "accept-encoding" in _tokens(resp.headers["vary"])
    assert int(responses[0].headers["content-length"]) < len(plain.content)  # genuinely compressed on the wire


@pytest.mark.parametrize("value, gzipped", [
    ("gzip", True), ("GZIP", True), ("Gzip", True), ("br, gzip", True), ("gzip, deflate", True),
    ("deflate, gzip;q=0.5", True), ("gzip; q=0.5", True), ("gzip;q=1", True), ("gzip;q=0.001", True),
    (" gzip ", True), ("br;q=1.0, GZIP;Q=0.8", True),
    ("identity", False), ("gzip;q=0", False), ("gzip;q=0.0", False), ("gzip; q=0", False),
    ("br", False), ("deflate", False), ("compress", False), ("*", False), ("", False),
    ("gzip;q=abc", False), ("gzip;q=2", False), ("gzip;q=nan", False), ("gzip;q=0, gzip", False),
    ("gzipx", False), ("xgzip", False),
])
def test_accept_encoding_forms(rig, value, gzipped):
    resp = _req(rig, ae=value)
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert resp.headers.get("content-encoding") == ("gzip" if gzipped else None)
    assert (ETAG_GZIP if gzipped else ETAG_IDENTITY).fullmatch(resp.headers["etag"])
    assert "accept-encoding" in _tokens(resp.headers["vary"])  # Vary announces the negotiation either way


def test_absent_accept_encoding_is_identity(rig):
    resp = _req(rig, ae=None, client=_bare_client(rig))
    assert resp.status_code == 200 and "content-encoding" not in resp.headers
    assert ETAG_IDENTITY.fullmatch(resp.headers["etag"]) and resp.json()["ok"] is True


def test_each_representation_has_its_own_etag_and_a_304_works_inside_each(rig):
    identity, gz = _req(rig, ae="identity"), _req(rig, ae="gzip")
    assert identity.headers["etag"] != gz.headers["etag"]
    assert identity.headers["etag"] == gz.headers["etag"].replace("-gzip", "")  # one key, two representations
    for resp, ae in ((identity, "identity"), (gz, "gzip")):
        again = _req(rig, ae=ae, inm=resp.headers["etag"])
        assert again.status_code == 304 and again.headers["etag"] == resp.headers["etag"]
    # A validator of one representation never validates the other one.
    crossed = _req(rig, ae="gzip", inm=identity.headers["etag"])
    assert crossed.status_code == 200 and crossed.headers["content-encoding"] == "gzip"
    assert _req(rig, ae="identity", inm=gz.headers["etag"]).status_code == 200


def test_empty_evaluaciones_compresses_and_round_trips(spy):
    spy.rows.clear()
    plain = _req(spy, None, ae="identity")
    gz = _req(spy, None, ae="gzip")
    assert gz.headers["content-encoding"] == "gzip" and gz.content == plain.content
    assert gz.json()["evaluaciones"] == [] and spy.ledger["gzip"] == 1


def test_headers_on_200_and_304_for_both_encodings(rig):
    for ae in ("identity", "gzip"):
        ok = _req(rig, ae=ae)
        _assert_cache_headers(ok)
        _assert_cache_headers(_req(rig, ae=ae, inm=ok.headers["etag"]))


def test_a_304_carries_no_content_encoding_or_body(rig):
    etag = _req(rig, ae="gzip").headers["etag"]
    resp = _req(rig, ae="gzip", inm=etag)
    assert resp.status_code == 304 and resp.content == b"" and "content-length" not in resp.headers


# ── CORS on the real route (10b.10) ─────────────────────────────────────────

ORIGIN = "https://sismo-cali-dashboard.vercel.app"


def test_the_route_exposes_etag_and_keeps_vary_origin_next_to_the_negotiation_headers(rig):
    resp = rig.client.get(URL, params=OPT_IN, headers={"Origin": ORIGIN, "Accept-Encoding": "gzip"})
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["access-control-expose-headers"] == "ETag" and resp.headers["etag"]
    assert {"authorization", "accept-encoding", "origin"} <= _tokens(resp.headers["vary"])
    cond = rig.client.get(URL, params=OPT_IN, headers={"Origin": ORIGIN, "If-None-Match": resp.headers["etag"],
                                                       "Accept-Encoding": "gzip"})
    assert cond.status_code == 304 and cond.headers["access-control-expose-headers"] == "ETag"


def test_a_real_preflight_for_the_conditional_get_passes_without_touching_the_route(rig):
    pre = rig.client.options(URL, headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,if-none-match",
    })
    assert pre.status_code == 200 and "if-none-match" in _tokens(pre.headers["access-control-allow-headers"])
    assert rig.ledger["walk"] == 0


# ── HEAD / OPTIONS (characterization: unchanged by this slice) ──────────────


def test_head_is_not_routed_and_does_no_work(rig):
    resp = rig.client.head(URL, params=OPT_IN)
    assert resp.status_code == 405 and resp.content == b""
    assert rig.counts() == {"walk": 0, "roster": 0, "survey": 0, "evals": 0, "depurar": 0, "referencia": 0}


def test_a_plain_options_without_cors_headers_is_405_and_never_a_304(rig):
    resp = rig.client.options(URL, headers={"If-None-Match": "*"})
    assert resp.status_code == 405 and "etag" not in resp.headers
    assert rig.ledger["walk"] == 0


# ── Concurrency and failure ─────────────────────────────────────────────────


def test_20_threads_at_a_cold_bytes_cache_one_serialization_one_compression(spy):
    barrier = threading.Barrier(20)
    results: list[tuple[str, int, bytes, str]] = []
    lock = threading.Lock()

    def worker(ae: str):
        barrier.wait(timeout=10)
        resp = _req(spy, ae=ae)
        with lock:
            results.append((ae, resp.status_code, resp.content, resp.headers["etag"]))

    threads = [threading.Thread(target=worker, args=("gzip" if i % 2 else "identity",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 20 and all(status == 200 for _, status, _, _ in results)
    assert len({content for _, _, content, _ in results}) == 1  # decoded bytes are identical
    assert len({etag for ae, _, _, etag in results if ae == "gzip"}) == 1
    assert spy.ledger["encode"] == 1 and spy.ledger["gzip"] == 1


def test_a_failing_serialization_does_not_poison_the_cache(rig, monkeypatch):
    real = router_mod._encode_body
    calls = {"n": 0}

    def flaky(body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TypeError("Object of type datetime is not JSON serializable")
        return real(body)

    monkeypatch.setattr(router_mod, "_encode_body", flaky)
    quiet = TestClient(rig.app, raise_server_exceptions=False)
    assert _req(rig, client=quiet).status_code == 500  # exactly what the un-cached JSONResponse did
    ok = _req(rig)
    assert ok.status_code == 200 and ok.json()["ok"] is True and calls["n"] == 2
    assert _req(rig, inm=ok.headers["etag"]).status_code == 304


def test_a_failing_compression_keeps_the_identity_bytes_and_recovers(rig, monkeypatch):
    real = router_mod._gzip_body
    calls = {"n": 0}

    def flaky(raw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("no space left")
        return real(raw)

    monkeypatch.setattr(router_mod, "_gzip_body", flaky)
    quiet = TestClient(rig.app, raise_server_exceptions=False)
    assert _req(rig, ae="gzip", client=quiet).status_code == 500
    assert _req(rig, ae="identity").status_code == 200
    gz = _req(rig, ae="gzip")
    assert gz.status_code == 200 and gz.headers["content-encoding"] == "gzip" and calls["n"] == 2


def test_a_leader_serializing_while_the_snapshot_moves_on_still_serves_its_own_key(spy):
    """A request that already picked its entry keeps that entry's bytes even if
    a newer request replaced the slot meanwhile."""
    first = _req(spy)
    spy.rows[0]["direccion"] = "Calle 99"
    spy.clock.advance(STICKER_TTL + 1)
    second = _req(spy)
    assert first.headers["etag"] != second.headers["etag"]
    assert b"Calle 99" in second.content and b"Calle 99" not in first.content


# ── No PII in logs ──────────────────────────────────────────────────────────


def test_no_pii_in_any_log_line_of_a_full_http_cycle(rig, caplog, monkeypatch):
    _secret_roster(rig)
    with caplog.at_level(logging.DEBUG):
        first = _req(rig, ae="gzip")
        _req(rig, ae="identity")
        _req(rig, ae="gzip", inm=first.headers["etag"])
        _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
        _req(rig)
        del rig.app.dependency_overrides[current_claims]
        _req(rig, inm="*")
        _as(rig, FAKE_CLAIMS_ADMIN)
        monkeypatch.setattr(router_mod, "_encode_body", lambda body: (_ for _ in ()).throw(TypeError("boom")))
        rig.rows[0]["direccion"] = "Calle 99"
        rig.clock.advance(STICKER_TTL + 1)
        _req(rig, client=TestClient(rig.app, raise_server_exceptions=False))
    for secret in (NOMBRE, CEDULA, CORREO, TELEFONO, TARJETA, "ana maria"):
        assert secret.lower() not in caplog.text.lower(), secret
    assert first.headers["etag"] not in caplog.text


# ── Judgment-day W1: `If-None-Match: *` is never a 304 ──────────────────────

WILDCARDS = ("*", " * ", "*, *")


def test_wildcard_after_a_role_downgrade_gets_the_viewers_own_body_never_a_304(rig):
    """Repro: an admin received the PII body, the session is then downgraded to a
    viewer; `*` used to validate the admin's body for the viewer."""
    admin = _req(rig, OPT_IN)
    assert admin.status_code == 200 and "depuracion" in admin.json()
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
    for inm in WILDCARDS:
        resp = _req(rig, OPT_IN, inm=inm)
        assert resp.status_code == 200 and resp.content != b""
        assert "depuracion" not in resp.json() and b"revision_manual" not in resp.content
        assert resp.headers["etag"] != admin.headers["etag"]


@pytest.mark.parametrize("ae", ["identity", "gzip"])
@pytest.mark.parametrize("params", [OPT_IN, None])
def test_wildcard_from_a_cold_viewer_who_never_received_a_body_gets_a_200(monkeypatch, ae, params):
    rig = Rig(monkeypatch, admin=False)
    resp = _req(rig, params, ae=ae, inm="*")
    assert resp.status_code == 200 and resp.json()["ok"] is True and len(resp.json()["evaluaciones"]) == 2
    assert resp.headers["etag"] and resp.headers["cache-control"] == "no-store"


def test_wildcard_from_a_cold_admin_gets_a_200_with_the_body(rig):
    resp = _req(rig, OPT_IN, inm="*")
    assert resp.status_code == 200 and resp.json()["depuracion"]["activa"] is True


def test_wildcard_over_two_header_lines_is_still_a_200(rig):
    resp = rig.client.get(URL, params=OPT_IN, headers=[("Accept-Encoding", "identity"),
                                                       ("If-None-Match", "*"), ("If-None-Match", "*")])
    assert resp.status_code == 200 and resp.json()["ok"] is True


def test_wildcard_on_a_degraded_snapshot_is_a_200_and_a_real_validator_still_304s(rig, monkeypatch):
    _force_degraded(rig, monkeypatch)
    resp = _req(rig, inm="*")
    assert resp.status_code == 200 and resp.json()["degraded"] is True
    assert _req(rig, inm=resp.headers["etag"]).status_code == 304  # the exact validator is still honoured


def test_a_real_validator_still_304s_after_a_wildcard_request(rig):
    """The wildcard answer must not disturb the cached entry or its validator."""
    etag = _req(rig).headers["etag"]
    assert _req(rig, inm="*").headers["etag"] == etag
    assert _req(rig, inm=etag).status_code == 304
    assert rig.ledger["encode"] == 1


def test_20_threads_mixing_exact_validators_and_wildcards_get_304_and_200_respectively(spy):
    etag = _req(spy).headers["etag"]
    barrier = threading.Barrier(20)
    results: list[tuple[str, int, int]] = []
    lock = threading.Lock()

    def worker(inm: str):
        barrier.wait(timeout=10)
        resp = _req(spy, inm=inm)
        with lock:
            results.append((inm, resp.status_code, len(resp.content)))

    threads = [threading.Thread(target=worker, args=(etag if i % 2 else "*",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 20
    assert all(status == 304 and size == 0 for inm, status, size in results if inm == etag)
    assert all(status == 200 and size > 0 for inm, status, size in results if inm == "*")
    assert spy.ledger["encode"] == 1


# ── Judgment-day W2: no upstream message reaches a log line ─────────────────

LEAKY = f"cedula {CEDULA} de {NOMBRE} no encontrada"
SECRETS = (CEDULA, NOMBRE, "ana maria gomez")


def _assert_no_secret_in(text: str) -> None:
    for secret in SECRETS:
        assert secret.lower() not in text.lower(), secret


def _leaky_roster(monkeypatch) -> None:
    def profiles(db):
        raise RuntimeError(LEAKY)

    monkeypatch.setattr(router_mod.stickers, "inspector_profiles", profiles)


def test_roster_beyond_the_ceiling_serves_the_stale_stickers_without_the_upstream_message_in_the_log(rig, caplog,
                                                                                                    monkeypatch):
    """Drives the REAL handler path: the roster refresh fails past the max-stale
    ceiling, `StaleBeyondCeiling` propagates out of `build_payload` into
    `EvaluacionesCache.get_or_fetch`'s serve-stale branch, which used to
    `logging.exception` the whole chain (upstream message included)."""
    first = _req(rig)
    assert first.status_code == 200
    _leaky_roster(monkeypatch)
    rig.clock.advance(versioned_cache.DEFAULT_MAX_STALE_S + STICKER_TTL + 1)
    with caplog.at_level(logging.DEBUG):
        resp = _req(rig)
    assert resp.status_code == 200 and len(resp.json()["evaluaciones"]) == 2  # stale stickers still served
    assert "StaleBeyondCeiling" in caplog.text or "max-stale" in caplog.text  # the branch really ran
    _assert_no_secret_in(caplog.text)


def test_20_threads_beyond_the_ceiling_leak_nothing_and_never_500(rig, caplog, monkeypatch):
    _req(rig)
    _leaky_roster(monkeypatch)
    rig.clock.advance(versioned_cache.DEFAULT_MAX_STALE_S + STICKER_TTL + 1)
    barrier = threading.Barrier(20)
    statuses: list[int] = []
    lock = threading.Lock()

    def worker():
        barrier.wait(timeout=10)
        resp = _req(rig)
        with lock:
            statuses.append(resp.status_code)

    with caplog.at_level(logging.DEBUG):
        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    assert statuses == [200] * 20
    _assert_no_secret_in(caplog.text)


def test_cold_stickers_cache_serving_the_blob_copy_does_not_log_the_upstream_message(rig, caplog, monkeypatch):
    """`EvaluacionesCache` cold + upstream failing + Blob last-known-good."""
    _force_degraded(rig, monkeypatch)
    monkeypatch.setattr(atencionsismo_mod, "fetch_stickers", _leaky_fetch)
    with caplog.at_level(logging.DEBUG):
        resp = _req(rig)
    assert resp.status_code == 200 and resp.json()["degraded"] is True
    assert "fetch fallo" in caplog.text  # the branch really ran
    _assert_no_secret_in(caplog.text)


def test_unclassified_failure_is_logged_by_type_and_location_only(rig, caplog, monkeypatch):
    """The 502 catch-all used to `logging.exception` the error message."""
    monkeypatch.setattr(atencionsismo_mod, "fetch_stickers", _leaky_fetch_unclassified)
    quiet = TestClient(rig.app, raise_server_exceptions=False)
    with caplog.at_level(logging.DEBUG):
        resp = _req(rig, client=quiet)
    assert resp.status_code == 502
    assert "fallo no clasificado" in caplog.text and "KeyError" in caplog.text
    _assert_no_secret_in(caplog.text)


def test_inspectores_cache_stale_branch_does_not_log_the_upstream_message(caplog):
    from app.routers import stickers

    cache = stickers.InspectoresCache()
    cache.get_or_fetch(lambda: [{"ok": True}])
    cache._at = None  # stale
    with caplog.at_level(logging.DEBUG):
        assert cache.get_or_fetch(lambda: (_ for _ in ()).throw(RuntimeError(LEAKY))) == [{"ok": True}]
    assert "fetch fallo" in caplog.text
    _assert_no_secret_in(caplog.text)


async def _leaky_fetch(client, user, password, **kw):
    raise atencionsismo_mod.ApiUnavailableError(LEAKY, status=503)


async def _leaky_fetch_unclassified(client, user, password, **kw):
    raise KeyError(LEAKY)


# ── Judgment-day W3: the route never mutates the shared depuración result ───


def _shared_state(rig):
    """Deep snapshot of everything the route shares across requests: the cached
    `Depuracion` (its dicts are mutable and NOT copied on the `resolve` path) and
    the stickers payload."""
    return copy.deepcopy((rig.app.state.depuracion_cache._result, rig.app.state.stickers_atencionsismo_cache._payload))


def _full_request_cycle(rig) -> None:
    identity = _req(rig, OPT_IN, ae="identity")
    gz = _req(rig, OPT_IN, ae="gzip")
    assert _req(rig, OPT_IN, ae="identity", inm=identity.headers["etag"]).status_code == 304
    assert _req(rig, OPT_IN, ae="gzip", inm=gz.headers["etag"]).status_code == 304
    assert _req(rig, OPT_IN, inm="*").status_code == 200
    _req(rig, None)
    _as(rig, FAKE_CLAIMS_INSTITUCIONAL)
    _req(rig, OPT_IN)
    _as(rig, FAKE_CLAIMS_ADMIN)


def test_the_route_never_mutates_the_shared_depuracion_result_or_payload(rig):
    _req(rig, OPT_IN)  # warm: the result now sits in the cache
    before = _shared_state(rig)
    assert before[0] is not None and before[0].inspectores  # a real, non-empty result is being guarded
    _full_request_cycle(rig)
    assert _shared_state(rig) == before


def test_the_shared_result_guard_detects_a_mutating_serializer(rig, monkeypatch):
    """Self-test of the guard above: a serializer that mutates the shared dict
    must make the snapshot comparison fail."""
    real = router_mod._depuracion_a_dict

    def mutating(resultado):
        resultado.alias_nombres["intruso"] = "x"
        return real(resultado)

    _req(rig, OPT_IN)
    before = _shared_state(rig)
    monkeypatch.setattr(router_mod, "_depuracion_a_dict", mutating)
    rig.clock.advance(STICKER_TTL + 1)
    rig.rows[0]["direccion"] = "Calle 99"  # new bytes key -> the serializer runs again
    _req(rig, OPT_IN)
    assert _shared_state(rig) != before
