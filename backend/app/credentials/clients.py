"""Named, memoized service-account clients — design.md ADR-4, as amended by
proposal.md Extension 2 (2026-08-25, "no usar nada relacionado con el
dagma").

The ONLY module that reads service-account env vars or constructs
Firestore/Auth clients. Exactly ONE named client:

- ``sismo()``  — Firestore + Auth admin, `sismo-agosto-sgred`, bound to
  ``FIREBASE_SERVICE_ACCOUNT_JSON``. Used by nearly every web route
  (stickers, sticker-status, sticker-asignaciones, inspector-asignaciones,
  usuarios) and the ``cruce_sticker`` job. **Load rule: fail-fast at web
  startup** — this is why ``WEB_STARTUP_CLIENTS`` below always includes it,
  independent of which routers happen to be mounted yet (relevant in slice 1,
  where only the unauthenticated ``health`` router exists).

No second named client exists in this module: proposal.md Extension 2 item 1
removed the one scaffolded in slice 1a, whose sole consumer job is excluded
from migration under that same Extension. Google Sheets is likewise fully
out of scope for this consolidation (Scope Exclusion Addendum): no
``sheets()`` client and no Sheets-related env var exist here.

Declaration mechanism (ADR-4): each router/job module declares
``REQUIRED_CLIENTS: tuple[str, ...]`` at module top. ``create_app()`` unions
those declarations with ``WEB_STARTUP_CLIENTS`` and calls ``require(...)`` on
the result at startup (crash early, matching Railway's restart policy).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Iterable, NamedTuple

# Env var name per named client (ADR-4 table, dagma removed per proposal.md
# Extension 2).
_ENV_VARS: dict[str, str] = {
    "sismo": "FIREBASE_SERVICE_ACCOUNT_JSON",
}

# Clients whose absence MUST fail web-process startup, regardless of which
# routers are mounted (ADR-4's per-client "Load rule" column). Extended in
# slice 2 by app/routers/sign.py declaring "s3" via REQUIRED_CLIENTS.
WEB_STARTUP_CLIENTS: tuple[str, ...] = ("sismo",)


class CredentialsError(RuntimeError):
    """A required credential is missing or not valid JSON."""


def _service_account_info(client_name: str) -> dict:
    env_var = _ENV_VARS.get(client_name)
    if env_var is None:
        raise CredentialsError(f"unknown credential client: {client_name!r}")
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        raise CredentialsError(f"{env_var} is not set (required by {client_name}())")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CredentialsError(f"{env_var} is not valid JSON: {exc}") from exc


def require(*client_names: str) -> None:
    """Validate presence + JSON-parseability of the named clients' env vars.

    Raises ``CredentialsError`` (crash early) if any is missing/invalid. Does
    NOT construct the client itself — that happens lazily in the accessor
    functions below, memoized per process.
    """
    for name in client_names:
        _service_account_info(name)


def required_clients_for(routers: Iterable[object]) -> tuple[str, ...]:
    """Union WEB_STARTUP_CLIENTS with every mounted router's REQUIRED_CLIENTS."""
    names: set[str] = set(WEB_STARTUP_CLIENTS)
    for router_module in routers:
        names.update(getattr(router_module, "REQUIRED_CLIENTS", ()))
    return tuple(sorted(names))


class SismoClients(NamedTuple):
    """Firestore + Auth admin clients for the `sismo-agosto-sgred` project."""

    firestore: object
    app: object  # firebase_admin App bound to this service account


@lru_cache(maxsize=1)
def sismo() -> SismoClients:
    """Memoized Firestore + Auth admin clients, `sismo-agosto-sgred`."""
    import firebase_admin
    from firebase_admin import credentials as fb_credentials
    from google.cloud import firestore

    info = _service_account_info("sismo")
    try:
        app = firebase_admin.get_app("sismo")
    except ValueError:
        app = firebase_admin.initialize_app(
            fb_credentials.Certificate(info), name="sismo"
        )
    db = firestore.Client.from_service_account_info(
        info, project=info.get("project_id")
    )
    return SismoClients(firestore=db, app=app)
