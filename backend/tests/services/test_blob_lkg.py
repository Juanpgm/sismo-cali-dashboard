"""Focused regression coverage for `app.services.blob_lkg`'s tuning
constants — not a full behavioral suite (`load_json`/`save_json`'s actual
I/O is already exercised indirectly through `routers/stickers.py`'s
`EvaluacionesCache` tests).

`load_json_private` (Phase 3, seguimiento-inspectores-depurado, task
3.5/D8) gets its OWN direct coverage below — it is the reader half of the
private-Blob contract `inspectores_referencia.cargar_referencia` depends on
by default, and nothing else in this repo exercises it indirectly yet."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import blob_lkg


# M5 (adversarial review 2026-09-12): the realistic redacted stickers
# payload measures ~2.97 MB; the OLD 10s timeout on a Vercel Blob PUT of
# that size in this web-request-adjacent path was measured too tight for a
# slow-but-alive upload to land before the request itself gave up on it,
# turning a slow-but-otherwise-fine upload into a silently-swallowed
# `save_json` failure (logged, never re-raised, per this module's own
# fire-and-forget contract) instead of an eventually-successful LKG write.
def test_timeout_covers_the_measured_redacted_payload_upload_time():
    assert blob_lkg._TIMEOUT_S == 30


# ── load_json_private (D8: authenticated read for the private referencia
# bundle) — same contract as `load_json` (never raises, None on ANY
# failure), verified against `blob_sync.download_authenticated` via
# monkeypatch (no real network) ─────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_private_token(monkeypatch):
    """Hermetic guard: a real BLOB_PRIVATE_TOKEN in the dev shell must not
    leak into these tests."""
    monkeypatch.delenv("BLOB_PRIVATE_TOKEN", raising=False)


def test_load_json_private_only_private_token_reads(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", "vercel_blob_rw_priv_secret")

    def _fake_download(pathname, local_path, timeout):
        Path(local_path).write_text('{"schema": 1}', encoding="utf-8")
        return True

    monkeypatch.setattr(blob_lkg.blob_sync, "download_authenticated", _fake_download)

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) == {"schema": 1}


def test_load_json_private_blank_private_and_no_rw_returns_none(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", "   ")

    calls = []
    # NOTE: raising inside the fake would be swallowed by `load_json_private`'s
    # broad `except`, making the test vacuous — record calls and assert instead.
    monkeypatch.setattr(
        blob_lkg.blob_sync, "download_authenticated", lambda *a, **k: calls.append((a, k))
    )

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) is None
    assert calls == []


def test_public_load_json_ignores_private_token(monkeypatch):
    """`load_json` (public stores) keeps gating on BLOB_READ_WRITE_TOKEN
    alone — the private token must not enable it."""
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", "vercel_blob_rw_priv_secret")

    calls = []
    monkeypatch.setattr(blob_lkg.blob_sync, "download", lambda *a, **k: calls.append((a, k)))

    assert blob_lkg.load_json("some/path.json", dict) is None
    assert calls == []


def test_load_json_private_no_token_returns_none_without_calling_download(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    calls = []
    monkeypatch.setattr(
        blob_lkg.blob_sync, "download_authenticated", lambda *a, **k: calls.append((a, k))
    )

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) is None
    assert calls == []


def test_guards_are_not_vacuous_with_token_present(monkeypatch):
    """Proves the `calls == []` guards above CAN fail: with a token present
    the recorded list is non-empty (the fake returns False -> None)."""
    monkeypatch.setenv("BLOB_PRIVATE_TOKEN", "vercel_blob_rw_priv_secret")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_pub_secret")
    priv_calls, pub_calls = [], []
    monkeypatch.setattr(
        blob_lkg.blob_sync, "download_authenticated",
        lambda *a, **k: priv_calls.append((a, k)) or False,
    )
    monkeypatch.setattr(
        blob_lkg.blob_sync, "download", lambda *a, **k: pub_calls.append((a, k)) or False
    )

    assert blob_lkg.load_json_private("p.json", dict) is None
    assert blob_lkg.load_json("p.json", dict) is None
    assert len(priv_calls) == 1
    assert len(pub_calls) == 1


def test_load_json_private_missing_blob_returns_none(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")
    monkeypatch.setattr(blob_lkg.blob_sync, "download_authenticated", lambda pathname, local_path, timeout: False)

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) is None


def test_load_json_private_valid_payload_returned(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    def _fake_download(pathname, local_path, timeout):
        with open(local_path, "w", encoding="utf-8") as fh:
            fh.write('{"schema": 1}')
        return True

    monkeypatch.setattr(blob_lkg.blob_sync, "download_authenticated", _fake_download)

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) == {"schema": 1}


def test_load_json_private_wrong_type_returns_none(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    def _fake_download(pathname, local_path, timeout):
        with open(local_path, "w", encoding="utf-8") as fh:
            fh.write('["not", "a", "dict"]')
        return True

    monkeypatch.setattr(blob_lkg.blob_sync, "download_authenticated", _fake_download)

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) is None


def test_load_json_private_malformed_json_returns_none(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    def _fake_download(pathname, local_path, timeout):
        with open(local_path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        return True

    monkeypatch.setattr(blob_lkg.blob_sync, "download_authenticated", _fake_download)

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) is None


def test_load_json_private_download_raises_returns_none_never_propagates(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    def _boom(pathname, local_path, timeout):
        raise ConnectionError("network boom")

    monkeypatch.setattr(blob_lkg.blob_sync, "download_authenticated", _boom)

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) is None


def test_load_json_private_download_sys_exit_returns_none_never_propagates(monkeypatch):
    # `blob_sync.download_authenticated` calls `sys.exit(...)` on a non-404
    # HTTPError (same convention as `download`) — must degrade, not crash
    # the caller.
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")

    def _fake_download(pathname, local_path, timeout):
        raise SystemExit("Blob download 500 para referencia/inspectores/bundle.json")

    monkeypatch.setattr(blob_lkg.blob_sync, "download_authenticated", _fake_download)

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) is None
