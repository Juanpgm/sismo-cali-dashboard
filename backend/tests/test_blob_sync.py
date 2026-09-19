"""Tests for `deploy/blob_sync.py`'s `access` parameter (`upload`) and the
new `download_authenticated` (seguimiento-inspectores-depurado, design.md
D8: `access:'private'` reference bundle). No real network call anywhere in
this file — `urllib.request.urlopen` is monkeypatched.

`upload`/`download` themselves had ZERO direct unit tests before this file
(only exercised indirectly through `blob_lkg`'s own callers) — this file
adds direct coverage for the NEW surface only (the `access` param and
`download_authenticated`), not a full retrofit of the pre-existing
functions.
"""
from __future__ import annotations

import email.message
import io
import json
import sys
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
import blob_sync as bs  # noqa: E402

FAKE_TOKEN = "vercel_blob_rw_abcd1234_secretpart"
PRIV_URL = "https://abcd1234.private.blob.vercel-storage.com/referencia/inspectores/bundle.json"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Stands in for the `OpenerDirector` `download_authenticated` builds."""

    def __init__(self, fn) -> None:
        self._fn = fn

    def open(self, req, timeout=None):
        return self._fn(req, timeout=timeout)


def _patch_opener(monkeypatch, fn):
    monkeypatch.setattr(bs, "_build_opener", lambda *a, **k: _FakeOpener(fn))


def test_upload_defaults_to_public_access(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", FAKE_TOKEN)
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse(json.dumps({"url": "https://x/bundle.json"}).encode())

    monkeypatch.setattr(bs.urllib.request, "urlopen", _fake_urlopen)
    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")

    bs.upload(str(local), "some/path.json", 0, "application/json")

    assert captured["headers"]["X-vercel-blob-access"] == "public"


def test_upload_access_private_sends_private_header(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", FAKE_TOKEN)
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse(json.dumps({"url": PRIV_URL}).encode())

    monkeypatch.setattr(bs.urllib.request, "urlopen", _fake_urlopen)
    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")

    url = bs.upload(str(local), "referencia/inspectores/bundle.json", 0, "application/json", access="private")

    assert captured["headers"]["X-vercel-blob-access"] == "private"
    assert url == PRIV_URL


# ── download_authenticated ─────────────────────────────────────────────


def test_download_authenticated_sends_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", FAKE_TOKEN)
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse(b'{"ok": true}')

    _patch_opener(monkeypatch, _fake_urlopen)
    dest = tmp_path / "out.json"

    ok = bs.download_authenticated("referencia/inspectores/bundle.json", str(dest))

    assert ok is True
    assert captured["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert dest.read_text(encoding="utf-8") == '{"ok": true}'


def test_download_authenticated_404_returns_false_not_raise(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", FAKE_TOKEN)

    def _fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=None)

    _patch_opener(monkeypatch, _fake_urlopen)

    assert bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "out.json")) is False


def test_download_authenticated_5xx_exits_not_silent(monkeypatch, tmp_path):
    """Same convention as `download`: a non-404 HTTPError is NOT swallowed
    here — `sys.exit` (caught by `blob_lkg.load_json_private`'s own
    `except (Exception, SystemExit)`, never by this function itself)."""
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", FAKE_TOKEN)

    def _fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "server error", hdrs=None, fp=None)

    _patch_opener(monkeypatch, _fake_urlopen)

    with pytest.raises(SystemExit):
        bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "out.json"))


def test_download_authenticated_missing_token_exits(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    with pytest.raises(SystemExit):
        bs.download_authenticated("referencia/inspectores/bundle.json", "irrelevant.json")


# ── private-store token resolution (BLOB_PRIVATE_TOKEN first, fallback to
# BLOB_READ_WRITE_TOKEN) + private host ─────────────────────────────────────

PRIVATE_TOKEN = "vercel_blob_rw_PrivStore9_privsecret"
PUBLIC_TOKEN = "vercel_blob_rw_PubStore1_pubsecret"


@pytest.fixture(autouse=True)
def _clean_private_token(monkeypatch):
    """Hermetic guard: a real BLOB_PRIVATE_TOKEN / BLOB_READ_WRITE_TOKEN in the
    dev shell must never leak into these tests."""
    monkeypatch.delenv("BLOB_PRIVATE_TOKEN", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)


def _capture_urlopen(monkeypatch, body=b"{}"):
    captured = {}

    def _fake_urlopen(req, timeout=None):
        # `download` (public) passes a bare URL string; the others a Request.
        captured["url"] = req if isinstance(req, str) else req.full_url
        if not isinstance(req, str):
            captured["headers"] = dict(req.header_items())
        return _FakeResponse(body)

    monkeypatch.setattr(bs.urllib.request, "urlopen", _fake_urlopen)
    _patch_opener(monkeypatch, _fake_urlopen)
    return captured


def test_private_token_prefers_private_over_rw(monkeypatch):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)

    assert bs.private_token() == PRIVATE_TOKEN


def test_private_token_falls_back_to_rw_when_private_unset(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)

    assert bs.private_token() == PUBLIC_TOKEN


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_private_token_blank_private_falls_back(monkeypatch, blank):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", blank)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)

    assert bs.private_token() == PUBLIC_TOKEN


def test_private_token_both_unset_or_blank_is_empty(monkeypatch):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", "  ")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", " ")

    assert bs.private_token() == ""
    monkeypatch.delenv("BLOB_PRIVATE_TOKEN")
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN")
    assert bs.private_token() == ""


def test_private_token_is_stripped(monkeypatch):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", f"  {PRIVATE_TOKEN}\n")

    assert bs.private_token() == PRIVATE_TOKEN


def test_download_authenticated_targets_private_host_with_private_token(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)
    captured = _capture_urlopen(monkeypatch)

    ok = bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "o.json"))

    assert ok is True
    assert captured["url"] == (
        "https://privstore9.private.blob.vercel-storage.com/referencia/inspectores/bundle.json"
    )
    assert captured["headers"]["Authorization"] == f"Bearer {PRIVATE_TOKEN}"
    # The public store's token must never reach the private read.
    assert PUBLIC_TOKEN not in captured["headers"]["Authorization"]


def test_download_authenticated_falls_back_to_rw_token_store(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", "   ")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)
    captured = _capture_urlopen(monkeypatch)

    assert bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "o.json")) is True

    assert captured["url"].startswith("https://pubstore1.private.blob.vercel-storage.com/")
    assert captured["headers"]["Authorization"] == f"Bearer {PUBLIC_TOKEN}"


def test_download_authenticated_malformed_token_exits_without_network_or_leak(monkeypatch, tmp_path):
    secret = "not-a-vercel-token-SUPERSECRET"
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", secret)

    def _no_network(req, timeout=None):
        raise AssertionError("no request may be made with a malformed token")

    monkeypatch.setattr(bs.urllib.request, "urlopen", _no_network)

    with pytest.raises(SystemExit) as excinfo:
        bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "o.json"))

    assert secret not in str(excinfo.value)


def test_public_download_and_upload_ignore_private_token(monkeypatch, tmp_path):
    """Regression guard: the public-store consumers keep using
    BLOB_READ_WRITE_TOKEN even when BLOB_PRIVATE_TOKEN is set."""
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)
    captured = _capture_urlopen(monkeypatch, body=json.dumps({"url": "https://x/f"}).encode())

    bs.download("some/path.json", str(tmp_path / "d.json"))
    assert captured["url"] == "https://pubstore1.public.blob.vercel-storage.com/some/path.json"

    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")
    bs.upload(str(local), "some/path.json", 0, "application/json")
    assert captured["headers"]["Authorization"] == f"Bearer {PUBLIC_TOKEN}"


# ── C1: publish (write) and read must resolve to the SAME private store ────────


def _upload_capture(monkeypatch, tmp_path, *, access, body=None):
    default = {"url": PRIV_URL if access == "private" else "https://x/f"}
    captured = _capture_urlopen(monkeypatch, body=json.dumps(body if body is not None else default).encode())
    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")
    url = bs.upload(str(local), "referencia/inspectores/bundle.json", 0, "application/json", access=access)
    return captured, url


def test_upload_private_uses_private_token_public_uses_rw_token(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)

    priv, _ = _upload_capture(monkeypatch, tmp_path, access="private")
    pub, _ = _upload_capture(monkeypatch, tmp_path, access="public")

    assert priv["headers"]["Authorization"] == f"Bearer {PRIVATE_TOKEN}"
    assert pub["headers"]["Authorization"] == f"Bearer {PUBLIC_TOKEN}"


def test_upload_private_falls_back_to_rw_token_when_private_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)

    priv, _ = _upload_capture(monkeypatch, tmp_path, access="private")

    assert priv["headers"]["Authorization"] == f"Bearer {PUBLIC_TOKEN}"


def test_upload_private_with_no_token_at_all_exits_before_network(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(bs.urllib.request, "urlopen", lambda req, timeout=None: calls.append(req))
    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        bs.upload(str(local), "p.json", 0, None, access="private")

    assert calls == []


def test_upload_public_ignores_private_token_when_rw_missing(monkeypatch, tmp_path):
    """Public writes must never silently use the private store's token."""
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    calls = []
    monkeypatch.setattr(bs.urllib.request, "urlopen", lambda req, timeout=None: calls.append(req))
    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        bs.upload(str(local), "p.json", 0, None)

    assert calls == []


def test_publish_store_equals_read_store(monkeypatch, tmp_path):
    """Both env vars set to DIFFERENT stores: the bundle is published to and
    read from the same (private) store. The response lacks `url` so the
    publish store is visible in the fallback URL; the read exposes it in
    its GET."""
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)

    _, up_url = _upload_capture(monkeypatch, tmp_path, access="private", body={})
    captured = _capture_urlopen(monkeypatch)
    bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "o.json"))

    up_host = up_url.split("/")[2]
    read_host = captured["url"].split("/")[2]
    assert up_host == read_host == "privstore9.private.blob.vercel-storage.com"


# ── W4: private upload never mints a public URL ────────────────────────────────


def test_upload_private_without_url_in_response_falls_back_to_private_host(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)

    _, url = _upload_capture(monkeypatch, tmp_path, access="private", body={})

    assert url == "https://privstore9.private.blob.vercel-storage.com/referencia/inspectores/bundle.json"
    assert ".public." not in url


def test_upload_public_without_url_in_response_falls_back_to_public_host(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)

    _, url = _upload_capture(monkeypatch, tmp_path, access="public", body={})

    assert url == "https://pubstore1.public.blob.vercel-storage.com/referencia/inspectores/bundle.json"


def test_upload_private_empty_url_in_response_falls_back_to_private_host(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)

    _, url = _upload_capture(monkeypatch, tmp_path, access="private", body={"url": ""})

    assert ".private." in url
    assert ".public." not in url


# ── W1: store id must be a bare alphanumeric label, never a host/path injection ─

_HOSTILE_STORES = [
    "attacker.example.com/x",   # '.' and '/'
    "evil.example.com",         # '.'
    "evil/path",
    "a?b",
    "user@evil",
    "a#b",
    "a b",
    "a-b",                      # '-' is not part of a store id
    "",                         # empty
    "ünï",            # non-ascii
]


@pytest.mark.parametrize("store", _HOSTILE_STORES)
@pytest.mark.parametrize("op", ["download", "download_authenticated", "upload_public", "upload_private"])
def test_hostile_store_id_exits_before_any_network_call(monkeypatch, tmp_path, store, op):
    hostile = f"vercel_blob_rw_{store}_SUPERSECRET"
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", hostile)
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", hostile)
    calls = []
    monkeypatch.setattr(bs.urllib.request, "urlopen", lambda req, timeout=None: calls.append(req))
    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        if op == "download":
            bs.download("p.json", str(tmp_path / "o.json"))
        elif op == "download_authenticated":
            bs.download_authenticated("p.json", str(tmp_path / "o.json"))
        elif op == "upload_public":
            bs.upload(str(local), "p.json", 0, None, access="public")
        else:
            bs.upload(str(local), "p.json", 0, None, access="private")

    assert calls == []
    assert "SUPERSECRET" not in str(excinfo.value)


def test_store_id_uppercase_is_lowercased_and_accepted():
    assert bs._store_id("vercel_blob_rw_AbC123_secret") == "abc123"


def test_store_id_digits_only_accepted():
    assert bs._store_id("vercel_blob_rw_12345_secret") == "12345"


# ── W3: malformed-token error names the variable the token really came from ────


def test_malformed_token_error_names_rw_var_when_fallback_used(monkeypatch, tmp_path):
    secret = "garbage-SUPERSECRET"
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", secret)

    with pytest.raises(SystemExit) as excinfo:
        bs.download_authenticated("p.json", str(tmp_path / "o.json"))

    msg = str(excinfo.value)
    assert "BLOB_READ_WRITE_TOKEN" in msg
    assert "BLOB_PRIVATE_TOKEN" not in msg
    assert secret not in msg


def test_malformed_token_error_names_private_var_when_it_is_the_source(monkeypatch, tmp_path):
    secret = "garbage-SUPERSECRET"
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", secret)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)

    with pytest.raises(SystemExit) as excinfo:
        bs.download_authenticated("p.json", str(tmp_path / "o.json"))

    msg = str(excinfo.value)
    assert "BLOB_PRIVATE_TOKEN" in msg
    assert secret not in msg


def test_malformed_token_on_private_upload_names_source_var(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "garbage-SUPERSECRET")
    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        bs.upload(str(local), "p.json", 0, None, access="private")

    assert "BLOB_READ_WRITE_TOKEN" in str(excinfo.value)
    assert "SUPERSECRET" not in str(excinfo.value)


def test_private_token_with_name_reports_source(monkeypatch):
    assert bs.private_token_with_name() == ("", "")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)
    assert bs.private_token_with_name() == ("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    assert bs.private_token_with_name() == ("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)


# ── pathname is URL-quoted when interpolated into host URLs ────────────────────


def test_download_authenticated_quotes_pathname(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    captured = _capture_urlopen(monkeypatch)

    bs.download_authenticated("a b/c?d#e.json", str(tmp_path / "o.json"))

    assert captured["url"] == "https://privstore9.private.blob.vercel-storage.com/a%20b/c%3Fd%23e.json"


# ── redirect handling: the Bearer must never follow a cross-host redirect ──────

_ORIGIN = "https://privstore9.private.blob.vercel-storage.com/referencia/inspectores/bundle.json"


def _redirect(handler, origin_url, new_url, code=302):
    req = urllib.request.Request(origin_url, headers={"authorization": f"Bearer {PRIVATE_TOKEN}"})
    return handler.redirect_request(req, io.BytesIO(b""), code, "Found", {}, new_url)


@pytest.mark.parametrize("code", [301, 302, 303, 307])
def test_redirect_to_other_host_drops_authorization(code):
    new = _redirect(bs._StripAuthOnCrossHostRedirect(), _ORIGIN, "https://evil.example/x", code)

    assert new is not None
    assert "Authorization" not in new.headers
    assert "Authorization" not in new.unredirected_hdrs
    assert PRIVATE_TOKEN not in json.dumps({**new.headers, **new.unredirected_hdrs})


def test_redirect_to_other_subdomain_drops_authorization():
    new = _redirect(
        bs._StripAuthOnCrossHostRedirect(), _ORIGIN,
        "https://privstore9.public.blob.vercel-storage.com/x",
    )

    assert "Authorization" not in new.headers


def test_redirect_same_host_keeps_authorization():
    new = _redirect(
        bs._StripAuthOnCrossHostRedirect(), _ORIGIN,
        "https://privstore9.private.blob.vercel-storage.com/other/path.json",
    )

    assert new.headers["Authorization"] == f"Bearer {PRIVATE_TOKEN}"


def test_redirect_same_host_case_insensitive_keeps_authorization():
    new = _redirect(
        bs._StripAuthOnCrossHostRedirect(), _ORIGIN,
        "https://PRIVSTORE9.private.blob.vercel-storage.com/other.json",
    )

    assert "Authorization" in new.headers


def test_redirect_to_other_port_drops_authorization():
    new = _redirect(
        bs._StripAuthOnCrossHostRedirect(), _ORIGIN,
        "https://privstore9.private.blob.vercel-storage.com:8443/x",
    )

    assert "Authorization" not in new.headers


class _ScriptedHttps(urllib.request.BaseHandler):
    """Fake transport: serves a scripted chain of responses keyed by URL and
    records the headers each hop was sent. Runs before the real HTTPS handler."""

    handler_order = 100

    def __init__(self, script):
        self.script = script  # {url: (code, location_or_body)}
        self.seen = []

    def https_open(self, req):
        self.seen.append((req.full_url, dict(req.header_items())))
        code, payload = self.script[req.full_url]
        msg = email.message.Message()
        body = b""
        if code in (301, 302, 303, 307, 308):
            msg["Location"] = payload
        else:
            body = payload
        resp = urllib.response.addinfourl(io.BytesIO(body), msg, req.full_url, code=code)
        resp.msg = "scripted"
        return resp


def _real_chain(monkeypatch, script):
    transport = _ScriptedHttps(script)
    real = bs._build_opener
    monkeypatch.setattr(bs, "_build_opener", lambda *extra: real(transport, *extra))
    return transport


def test_download_authenticated_cross_host_redirect_is_followed_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    transport = _real_chain(monkeypatch, {
        _ORIGIN: (302, "https://cdn.other.example/signed?sig=1"),
        "https://cdn.other.example/signed?sig=1": (200, b'{"ok": 1}'),
    })
    dest = tmp_path / "o.json"

    assert bs.download_authenticated("referencia/inspectores/bundle.json", str(dest)) is True

    assert dest.read_text(encoding="utf-8") == '{"ok": 1}'
    first, second = transport.seen
    assert first[1]["Authorization"] == f"Bearer {PRIVATE_TOKEN}"
    assert "Authorization" not in second[1]


def test_download_authenticated_same_host_redirect_keeps_token(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    hop = "https://privstore9.private.blob.vercel-storage.com/moved.json"
    transport = _real_chain(monkeypatch, {_ORIGIN: (302, hop), hop: (200, b"{}")})

    assert bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "o.json")) is True

    assert transport.seen[1][1]["Authorization"] == f"Bearer {PRIVATE_TOKEN}"


def test_download_authenticated_redirect_chain_ending_404_returns_false(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    hop = "https://cdn.other.example/gone"
    _real_chain(monkeypatch, {_ORIGIN: (302, hop), hop: (404, b"")})

    assert bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "o.json")) is False


def test_download_authenticated_redirect_chain_ending_403_exits_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    hop = "https://cdn.other.example/denied"
    _real_chain(monkeypatch, {_ORIGIN: (302, hop), hop: (403, b"")})

    with pytest.raises(SystemExit) as excinfo:
        bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "o.json"))

    assert PRIVATE_TOKEN not in str(excinfo.value)
    assert "403" in str(excinfo.value)


def test_download_authenticated_passes_timeout_to_opener(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    seen = {}

    def _fake(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse(b"{}")

    _patch_opener(monkeypatch, _fake)

    bs.download_authenticated("p.json", str(tmp_path / "o.json"), timeout=7)

    assert seen["timeout"] == 7


# ── private upload must never silently succeed against a public blob ───────────


@pytest.mark.parametrize("bad_url", [
    "https://privstore9.public.blob.vercel-storage.com/referencia/inspectores/bundle.json",
    "https://evil.example/x.private.blob.vercel-storage.com/a",
    "https://x.public.blob.vercel-storage.com/a.private.b",
    "https://x.public.blob.vercel-storage.com/?h=.private.",
    "https://x.private.evil.example/a",
    "https://x/bundle.json",
    "not a url",
])
def test_upload_private_rejects_non_private_returned_url(monkeypatch, tmp_path, bad_url):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)

    with pytest.raises(SystemExit) as excinfo:
        _upload_capture(monkeypatch, tmp_path, access="private", body={"url": bad_url})

    msg = str(excinfo.value)
    assert "referencia/inspectores/bundle.json" in msg
    assert "privsecret" not in msg
    assert PRIVATE_TOKEN not in msg


def test_upload_private_returns_private_url_as_is(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)

    _, got = _upload_capture(monkeypatch, tmp_path, access="private", body={"url": PRIV_URL})

    assert got == PRIV_URL


def test_upload_public_returned_url_is_not_host_checked(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)

    _, got = _upload_capture(monkeypatch, tmp_path, access="public", body={"url": "https://x/f"})

    assert got == "https://x/f"


# ── access must be exactly "public" or "private" ───────────────────────────────


@pytest.mark.parametrize("bad_access", ["Private", "PUBLIC", "", " private", None, "other"])
def test_upload_invalid_access_exits_before_token_or_network(monkeypatch, tmp_path, bad_access):
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", PRIVATE_TOKEN)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", PUBLIC_TOKEN)
    calls = []
    monkeypatch.setattr(bs.urllib.request, "urlopen", lambda req, timeout=None: calls.append(req))
    monkeypatch.setattr(bs, "private_token_with_name", lambda: calls.append("tok") or ("", ""))
    monkeypatch.setattr(bs, "_token", lambda: calls.append("tok") or "")
    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        bs.upload(str(local), "p.json", 0, None, access=bad_access)

    assert calls == []
    assert PRIVATE_TOKEN not in str(excinfo.value)
    assert PUBLIC_TOKEN not in str(excinfo.value)
