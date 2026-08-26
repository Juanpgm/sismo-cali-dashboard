"""Firebase ID token verification — ported from `api/refresh.js:16-54`
(design.md ADR-3).

Zero `firebase-admin`-for-verification, matching the JS approach: RS256
signature check against Google's rotating x509 certs
(`securetoken@system.gserviceaccount.com` metadata URL), using
`cryptography` to load the certificate and extract its public key. The
cert fetcher is an injectable async callable (`CertFetcher`) and the cache
is an injectable `CertCache` instance, so tests supply a fake keypair with
zero network access.

Claim checks: `aud`, `iss`, `exp`, `iat` — identical to the JS source — plus
a non-empty `sub` check. The `sub` check is a design-level addition beyond
`api/refresh.js`'s literal checks (design.md ADR-3: "Claim checks identical
to JS: iss, aud, exp, iat, non-empty sub"); flagged in apply-progress.md as
an intentional hardening, not a parity gap.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Awaitable, Callable, NamedTuple

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

FIREBASE_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)
_DEFAULT_MAX_AGE = 3600


class TokenVerificationError(Exception):
    """The presented ID token failed verification (401-worthy at the route
    layer — see `auth/deps.py`)."""


class CertFetchResult(NamedTuple):
    certs: dict[str, str]  # kid -> PEM x509 certificate
    max_age: int  # seconds, from the response's Cache-Control: max-age


CertFetcher = Callable[[], Awaitable[CertFetchResult]]


class CertCache:
    """Per-instance cert cache with a TTL. Tests construct their own
    instance so runs never share cached certs across cases."""

    def __init__(self) -> None:
        self._certs: dict[str, str] | None = None
        self._expires_at: float = 0.0

    async def get(self, fetcher: CertFetcher, *, force_refetch: bool = False) -> dict[str, str]:
        now = time.monotonic()
        if force_refetch or self._certs is None or now >= self._expires_at:
            result = await fetcher()
            self._certs = result.certs
            self._expires_at = now + result.max_age
        return self._certs


def _parse_max_age(cache_control: str | None, default: int = _DEFAULT_MAX_AGE) -> int:
    match = re.search(r"max-age=(\d+)", cache_control or "")
    return int(match.group(1)) if match else default


async def _default_cert_fetcher() -> CertFetchResult:
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.get(FIREBASE_CERTS_URL)
        response.raise_for_status()
        certs = response.json()
        max_age = _parse_max_age(response.headers.get("cache-control"))
    return CertFetchResult(certs=certs, max_age=max_age)


_default_cache = CertCache()


def _b64url_decode(segment: str) -> bytes:
    padding_needed = -len(segment) % 4
    return base64.urlsafe_b64decode(segment + "=" * padding_needed)


def _verify_signature(pem: str, signing_input: bytes, signature: bytes) -> None:
    try:
        cert = x509.load_pem_x509_certificate(pem.encode("utf-8"))
        public_key = cert.public_key()
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError) as exc:
        raise TokenVerificationError("firma inválida") from exc


async def verify_firebase_token(
    id_token: str,
    project_id: str,
    *,
    cert_fetcher: CertFetcher | None = None,
    cert_cache: CertCache | None = None,
) -> dict[str, Any]:
    """Verify a Firebase ID token with zero `firebase-admin` dependency.

    Port of `api/refresh.js#verifyFirebaseToken`. Returns the decoded claims
    payload, or raises `TokenVerificationError`.
    """
    cert_fetcher = cert_fetcher or _default_cert_fetcher
    cert_cache = cert_cache or _default_cache

    parts = str(id_token or "").split(".")
    if len(parts) != 3:
        raise TokenVerificationError("token malformado")
    h, p, s = parts
    try:
        header = json.loads(_b64url_decode(h))
        payload = json.loads(_b64url_decode(p))
        signature = _b64url_decode(s)
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenVerificationError("token malformado") from exc

    kid = header.get("kid")
    certs = await cert_cache.get(cert_fetcher)
    pem = certs.get(kid)
    if pem is None:
        # Rotation handling: unknown kid triggers exactly one forced
        # refetch, then reject if still unknown.
        certs = await cert_cache.get(cert_fetcher, force_refetch=True)
        pem = certs.get(kid)
        if pem is None:
            raise TokenVerificationError("kid desconocido")

    _verify_signature(pem, f"{h}.{p}".encode("ascii"), signature)

    now = time.time()
    if payload.get("aud") != project_id:
        raise TokenVerificationError("aud inválido")
    if payload.get("iss") != f"https://securetoken.google.com/{project_id}":
        raise TokenVerificationError("iss inválido")
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp < now:
        raise TokenVerificationError("token expirado")
    iat = payload.get("iat")
    if not isinstance(iat, (int, float)) or iat > now + 300:
        raise TokenVerificationError("iat inválido")
    if not payload.get("sub"):
        raise TokenVerificationError("sub vacío")
    return payload
