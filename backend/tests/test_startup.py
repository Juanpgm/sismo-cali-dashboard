"""Web-startup credential fail-fast behavior (design.md ADR-4; backend-platform
spec: "Missing web-route credential fails startup", "Job-only credential
loads lazily").

`sismo()` (FIREBASE_SERVICE_ACCOUNT_JSON) is the web-route credential and
MUST be validated unconditionally at `create_app()` time — before any request
is served. `dagma()` (GOOGLE_SERVICE_ACCOUNT_JSON) is job-only and MUST NOT
block web startup.
"""
from __future__ import annotations

import pytest

from app.credentials.clients import CredentialsError


def test_missing_firebase_service_account_json_fails_startup(monkeypatch):
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)

    from app.main import create_app

    with pytest.raises(CredentialsError):
        create_app()


def test_missing_google_service_account_json_does_not_block_web_startup(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)

    from app.main import create_app

    app = create_app()

    assert app is not None


def test_startup_succeeds_when_both_credentials_present(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')

    from app.main import create_app

    app = create_app()

    assert app is not None
