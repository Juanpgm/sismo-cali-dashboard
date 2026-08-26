"""Web-startup credential fail-fast behavior (design.md ADR-4; backend-platform
spec: "Missing web-route credential fails startup").

`sismo()` (FIREBASE_SERVICE_ACCOUNT_JSON) is the ONLY named credential client
(proposal.md Extension 2, 2026-08-25: "no usar nada relacionado con el
dagma" — the `dagma` client scaffolded in slice 1a is removed) and MUST be
validated unconditionally at `create_app()` time — before any request is
served.
"""
from __future__ import annotations

import pytest

from app.credentials.clients import CredentialsError


def test_missing_firebase_service_account_json_fails_startup(monkeypatch):
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)

    from app.main import create_app

    with pytest.raises(CredentialsError):
        create_app()


def test_startup_succeeds_when_firebase_service_account_json_present(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')

    from app.main import create_app

    app = create_app()

    assert app is not None
