"""Credentials module holds exactly ONE named client (proposal.md Extension 2,
2026-08-25, "no usar nada relacionado con el dagma"): `sismo()`
(FIREBASE_SERVICE_ACCOUNT_JSON). The `dagma` client scaffolded in slice 1a is
REMOVED — no dagma credential, project id, or env var may exist in `backend/`.
"""
from __future__ import annotations

import pytest

from app.credentials import clients as credentials


def test_dagma_client_does_not_exist():
    assert not hasattr(credentials, "dagma")


def test_dagma_is_not_a_known_credential_client():
    with pytest.raises(credentials.CredentialsError, match="unknown credential client"):
        credentials.require("dagma")


def test_web_startup_clients_is_sismo_only():
    assert credentials.WEB_STARTUP_CLIENTS == ("sismo",)
