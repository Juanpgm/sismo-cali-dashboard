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

import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
import blob_sync as bs  # noqa: E402

FAKE_TOKEN = "vercel_blob_rw_abcd1234_secretpart"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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
        return _FakeResponse(json.dumps({"url": "https://x/bundle.json"}).encode())

    monkeypatch.setattr(bs.urllib.request, "urlopen", _fake_urlopen)
    local = tmp_path / "f.json"
    local.write_text("{}", encoding="utf-8")

    url = bs.upload(str(local), "referencia/inspectores/bundle.json", 0, "application/json", access="private")

    assert captured["headers"]["X-vercel-blob-access"] == "private"
    assert url == "https://x/bundle.json"


# ── download_authenticated ─────────────────────────────────────────────


def test_download_authenticated_sends_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", FAKE_TOKEN)
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(bs.urllib.request, "urlopen", _fake_urlopen)
    dest = tmp_path / "out.json"

    ok = bs.download_authenticated("referencia/inspectores/bundle.json", str(dest))

    assert ok is True
    assert captured["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert dest.read_text(encoding="utf-8") == '{"ok": true}'


def test_download_authenticated_404_returns_false_not_raise(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", FAKE_TOKEN)

    def _fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=None)

    monkeypatch.setattr(bs.urllib.request, "urlopen", _fake_urlopen)

    assert bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "out.json")) is False


def test_download_authenticated_5xx_exits_not_silent(monkeypatch, tmp_path):
    """Same convention as `download`: a non-404 HTTPError is NOT swallowed
    here — `sys.exit` (caught by `blob_lkg.load_json_private`'s own
    `except (Exception, SystemExit)`, never by this function itself)."""
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", FAKE_TOKEN)

    def _fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "server error", hdrs=None, fp=None)

    monkeypatch.setattr(bs.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(SystemExit):
        bs.download_authenticated("referencia/inspectores/bundle.json", str(tmp_path / "out.json"))


def test_download_authenticated_missing_token_exits(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    with pytest.raises(SystemExit):
        bs.download_authenticated("referencia/inspectores/bundle.json", "irrelevant.json")
