"""Web-startup credential fail-fast behavior (design.md ADR-4; backend-platform
spec: "Missing web-route credential fails startup").

`sismo()` (FIREBASE_SERVICE_ACCOUNT_JSON) is unconditionally required
(proposal.md Extension 2, 2026-08-25: "no usar nada relacionado con el
dagma" — the `dagma` client scaffolded in slice 1a is removed) via
`WEB_STARTUP_CLIENTS`. `s3()` (SIGNER_AWS_ACCESS_KEY_ID/SECRET,
SIGNER_S3_BUCKET) became required too as of slice 2, once
`app/routers/sign.py` (`REQUIRED_CLIENTS = ("s3",)`) is mounted in
`_ROUTERS` — both are validated at `create_app()` time, before any request
is served.
"""
from __future__ import annotations

import pytest

from app.credentials.clients import CredentialsError

_S3_ENV = {
    "SIGNER_AWS_ACCESS_KEY_ID": "fake-access-key-id",
    "SIGNER_AWS_SECRET_ACCESS_KEY": "fake-secret-access-key",
    "SIGNER_S3_BUCKET": "test-sismo-fotos",
}


def _set_s3_env(monkeypatch) -> None:
    for key, value in _S3_ENV.items():
        monkeypatch.setenv(key, value)


def test_missing_firebase_service_account_json_fails_startup(monkeypatch):
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    _set_s3_env(monkeypatch)

    from app.main import create_app

    with pytest.raises(CredentialsError):
        create_app()


def test_missing_signer_s3_credentials_fails_startup(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    for key in _S3_ENV:
        monkeypatch.delenv(key, raising=False)

    from app.main import create_app

    with pytest.raises(CredentialsError):
        create_app()


def test_startup_succeeds_when_firebase_service_account_json_present(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    _set_s3_env(monkeypatch)

    from app.main import create_app

    app = create_app()

    assert app is not None
