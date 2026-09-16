"""Focused regression coverage for `app.services.blob_lkg`'s tuning
constants — not a full behavioral suite (`load_json`/`save_json`'s actual
I/O is already exercised indirectly through `routers/stickers.py`'s
`EvaluacionesCache` tests).

`load_json_private` (Phase 3, seguimiento-inspectores-depurado, task
3.5/D8) gets its OWN direct coverage below — it is the reader half of the
private-Blob contract `inspectores_referencia.cargar_referencia` depends on
by default, and nothing else in this repo exercises it indirectly yet."""
from __future__ import annotations

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


def test_load_json_private_no_token_returns_none_without_calling_download(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("download_authenticated must not be called without a token")

    monkeypatch.setattr(blob_lkg.blob_sync, "download_authenticated", _should_not_be_called)

    assert blob_lkg.load_json_private("referencia/inspectores/bundle.json", dict) is None


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
