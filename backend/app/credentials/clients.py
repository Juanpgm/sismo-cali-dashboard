"""Named, memoized service-account/credential clients — design.md ADR-4, as
amended by proposal.md Extension 2 (2026-08-25, "no usar nada relacionado
con el dagma").

The ONLY module that reads service-account env vars or constructs
Firestore/Auth/S3 clients. Two named clients:

- ``sismo()``  — Firestore + Auth admin, `sismo-agosto-sgred`, bound to
  ``FIREBASE_SERVICE_ACCOUNT_JSON``. Used by nearly every web route
  (stickers, sticker-status, sticker-asignaciones, inspector-asignaciones,
  usuarios) and the ``cruce_sticker`` job. **Load rule: fail-fast at web
  startup** — this is why ``WEB_STARTUP_CLIENTS`` below always includes it,
  independent of which routers happen to be mounted yet (relevant in slice 1,
  where only the unauthenticated ``health`` router exists).
- ``s3()``  — presigner for ATC-20 field-form photo uploads, bound to
  ``SIGNER_AWS_ACCESS_KEY_ID``/``SIGNER_AWS_SECRET_ACCESS_KEY``/
  ``SIGNER_S3_BUCKET`` (``SIGNER_S3_REGION`` optional, default
  ``us-east-1`` — matches `services/photo-signer/api/sign.js`'s default).
  Used only by ``routers/sign.py`` (slice 2). Not a JSON service-account
  blob like ``sismo()`` — plain AWS IAM key/secret env vars instead, so it
  is validated separately from ``_service_account_info``.

No `dagma()`/`sheets()` client exists in this module: proposal.md Extension
2 item 1 removed the one scaffolded in slice 1a (whose sole consumer job is
excluded from migration under that same Extension), and Google Sheets is
fully out of scope for this consolidation (Scope Exclusion Addendum) — no
Sheets-related env var exists here either.

Declaration mechanism (ADR-4): each router/job module declares
``REQUIRED_CLIENTS: tuple[str, ...]`` at module top. ``create_app()`` unions
those declarations with ``WEB_STARTUP_CLIENTS`` and calls ``require(...)`` on
the result at startup (crash early, matching Railway's restart policy). This
is how ``s3()``'s "fail-fast at web startup" load rule (ADR-4 table) is
satisfied without adding it to ``WEB_STARTUP_CLIENTS`` unconditionally: the
``sign`` router is always mounted once slice 2 lands, so its
``REQUIRED_CLIENTS = ("s3",)`` is always in the union `create_app()` builds.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Iterable, NamedTuple

# Env var name per named JSON-service-account client (ADR-4 table, dagma
# removed per proposal.md Extension 2). "s3" is NOT a JSON service account —
# see `_S3_REQUIRED_ENV_VARS`/`_s3_settings` below — so it is deliberately
# absent from this dict; `require()` dispatches "s3" to that separate path.
_ENV_VARS: dict[str, str] = {
    "sismo": "FIREBASE_SERVICE_ACCOUNT_JSON",
}

# Plain (non-JSON) env vars required to construct s3(). SIGNER_S3_REGION is
# NOT required here — it has a default ("us-east-1"), matching
# services/photo-signer/api/sign.js's own fallback.
_S3_REQUIRED_ENV_VARS: tuple[str, ...] = (
    "SIGNER_AWS_ACCESS_KEY_ID",
    "SIGNER_AWS_SECRET_ACCESS_KEY",
    "SIGNER_S3_BUCKET",
)

# Clients whose absence MUST fail web-process startup, regardless of which
# routers are mounted (ADR-4's per-client "Load rule" column). "s3" is
# deliberately NOT listed here — see the module docstring's "Declaration
# mechanism" note: app/routers/sign.py's REQUIRED_CLIENTS = ("s3",) already
# gets unioned in by create_app() once the sign router is mounted (slice 2).
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


def _s3_settings() -> "S3Settings":
    missing = [v for v in _S3_REQUIRED_ENV_VARS if not os.environ.get(v, "").strip()]
    if missing:
        raise CredentialsError(
            f"{', '.join(missing)} not set (required by s3())"
        )
    return S3Settings(
        access_key_id=os.environ["SIGNER_AWS_ACCESS_KEY_ID"],
        secret_access_key=os.environ["SIGNER_AWS_SECRET_ACCESS_KEY"],
        bucket=os.environ["SIGNER_S3_BUCKET"],
        region=os.environ.get("SIGNER_S3_REGION", "us-east-1"),
    )


def require(*client_names: str) -> None:
    """Validate presence (+ JSON-parseability, for JSON-SA clients) of the
    named clients' backing env vars.

    Raises ``CredentialsError`` (crash early) if any is missing/invalid. Does
    NOT construct the client itself — that happens lazily in the accessor
    functions below, memoized per process.
    """
    for name in client_names:
        if name == "s3":
            _s3_settings()
        else:
            _service_account_info(name)


def required_clients_for(routers: Iterable[object]) -> tuple[str, ...]:
    """Union WEB_STARTUP_CLIENTS with every mounted router's REQUIRED_CLIENTS."""
    names: set[str] = set(WEB_STARTUP_CLIENTS)
    for router_module in routers:
        names.update(getattr(router_module, "REQUIRED_CLIENTS", ()))
    return tuple(sorted(names))


class S3Settings(NamedTuple):
    """Validated plain-env-var S3 presigner settings (not a JSON SA blob)."""

    access_key_id: str
    secret_access_key: str
    bucket: str
    region: str


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


class S3Client(NamedTuple):
    """Boto3 S3 client + its configured bucket, for `routers/sign.py`."""

    client: object
    bucket: str


@lru_cache(maxsize=1)
def s3() -> S3Client:
    """Memoized boto3 S3 client for presigning ATC-20 photo uploads.

    Mirrors `services/photo-signer/api/sign.js`'s bucket/region selection —
    `SIGNER_S3_REGION` defaults to `us-east-1` when unset, exactly like the
    legacy signer.

    ``Config(signature_version="s3v4")`` is REQUIRED: without it, botocore
    picks a signer per-region, and for the default `us-east-1` bucket it
    falls back to the legacy SigV2 scheme (`AWSAccessKeyId=...&Signature=...
    &Expires=...` query params) instead of SigV4
    (`X-Amz-Algorithm=AWS4-HMAC-SHA256...`, what the legacy Node signer
    always produces via the AWS SDK v3, which defaults to SigV4
    unconditionally). AWS now rejects SigV2-presigned URLs with `403
    InvalidAccessKeyId` even for a perfectly valid key — a misleading error
    that looks like a credentials problem but isn't one. Confirmed live
    (2026-08-26): the SAME `SIGNER_AWS_*` values that work fine through the
    legacy signer failed every real S3 PUT through this client until this
    `Config` was added.
    """
    import boto3
    from botocore.config import Config

    settings = _s3_settings()
    client = boto3.client(
        "s3",
        region_name=settings.region,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        config=Config(signature_version="s3v4"),
    )
    return S3Client(client=client, bucket=settings.bucket)
