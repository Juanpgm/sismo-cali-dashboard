"""Firebase ID token verification (design.md ADR-3; backend-platform spec:
"Ported Auth Verifier And Role Resolution").

Ported semantics from `api/refresh.js:31-54` (`verifyFirebaseToken`): RS256
signature against Google's rotating x509 certs, `aud`/`iss`/`exp`/`iat`
claim checks. The cert fetcher (and its cache) are injectable so this suite
never touches the network — a self-signed RSA keypair stands in for
Google's rotating certs.

Beyond the literal JS source, design.md ADR-3 also requires a non-empty
`sub` claim (the JS source has no such check; this is an intentional
design-level hardening, not a parity gap — flagged in apply-progress.md for
verify).
"""
from __future__ import annotations

import base64
import datetime
import json
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from app.auth.verify import CertCache, CertFetchResult, TokenVerificationError, verify_firebase_token

PROJECT_ID = "sismo-agosto-sgred"
KID = "test-kid-1"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_keypair_and_cert():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-firebase")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return private_key, pem


_PRIVATE_KEY, _CERT_PEM = _generate_keypair_and_cert()


def _sign_token(payload: dict, *, kid: str = KID, private_key=None) -> str:
    private_key = private_key or _PRIVATE_KEY
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    h = _b64url(json.dumps(header).encode("utf-8"))
    p = _b64url(json.dumps(payload).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("utf-8")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{_b64url(signature)}"


def _valid_payload(**overrides) -> dict:
    now = int(time.time())
    payload = {
        "aud": PROJECT_ID,
        "iss": f"https://securetoken.google.com/{PROJECT_ID}",
        "exp": now + 3600,
        "iat": now - 10,
        "sub": "uid-123",
        "email": "someone@example.com",
    }
    payload.update(overrides)
    return payload


def _fetcher_with_certs(certs: dict[str, str], *, max_age: int = 3600, call_log: list | None = None):
    async def fetch():
        if call_log is not None:
            call_log.append(1)
        return CertFetchResult(certs=certs, max_age=max_age)

    return fetch


@pytest.mark.asyncio
async def test_valid_token_is_accepted():
    token = _sign_token(_valid_payload())
    fetcher = _fetcher_with_certs({KID: _CERT_PEM})

    claims = await verify_firebase_token(
        token, PROJECT_ID, cert_fetcher=fetcher, cert_cache=CertCache()
    )

    assert claims["sub"] == "uid-123"
    assert claims["email"] == "someone@example.com"


@pytest.mark.asyncio
async def test_unknown_kid_forces_exactly_one_refetch_then_rejects():
    call_log: list = []
    # Certs never contain KID -> initial fetch (call 1) + forced refetch
    # (call 2), still unknown -> reject.
    fetcher = _fetcher_with_certs({"other-kid": _CERT_PEM}, call_log=call_log)
    token = _sign_token(_valid_payload())

    with pytest.raises(TokenVerificationError):
        await verify_firebase_token(token, PROJECT_ID, cert_fetcher=fetcher, cert_cache=CertCache())

    assert len(call_log) == 2


@pytest.mark.asyncio
async def test_bad_aud_is_rejected():
    token = _sign_token(_valid_payload(aud="some-other-project"))
    fetcher = _fetcher_with_certs({KID: _CERT_PEM})

    with pytest.raises(TokenVerificationError):
        await verify_firebase_token(token, PROJECT_ID, cert_fetcher=fetcher, cert_cache=CertCache())


@pytest.mark.asyncio
async def test_bad_iss_is_rejected():
    token = _sign_token(_valid_payload(iss="https://securetoken.google.com/wrong-project"))
    fetcher = _fetcher_with_certs({KID: _CERT_PEM})

    with pytest.raises(TokenVerificationError):
        await verify_firebase_token(token, PROJECT_ID, cert_fetcher=fetcher, cert_cache=CertCache())


@pytest.mark.asyncio
async def test_expired_token_is_rejected():
    now = int(time.time())
    token = _sign_token(_valid_payload(exp=now - 10))
    fetcher = _fetcher_with_certs({KID: _CERT_PEM})

    with pytest.raises(TokenVerificationError):
        await verify_firebase_token(token, PROJECT_ID, cert_fetcher=fetcher, cert_cache=CertCache())


@pytest.mark.asyncio
async def test_iat_too_far_in_future_is_rejected():
    now = int(time.time())
    token = _sign_token(_valid_payload(iat=now + 301))
    fetcher = _fetcher_with_certs({KID: _CERT_PEM})

    with pytest.raises(TokenVerificationError):
        await verify_firebase_token(token, PROJECT_ID, cert_fetcher=fetcher, cert_cache=CertCache())


@pytest.mark.asyncio
async def test_empty_sub_is_rejected():
    token = _sign_token(_valid_payload(sub=""))
    fetcher = _fetcher_with_certs({KID: _CERT_PEM})

    with pytest.raises(TokenVerificationError):
        await verify_firebase_token(token, PROJECT_ID, cert_fetcher=fetcher, cert_cache=CertCache())


@pytest.mark.asyncio
async def test_bad_signature_is_rejected():
    other_private_key, _ = _generate_keypair_and_cert()
    # Signed with a DIFFERENT key than the one the fetcher's cert exposes.
    token = _sign_token(_valid_payload(), private_key=other_private_key)
    fetcher = _fetcher_with_certs({KID: _CERT_PEM})

    with pytest.raises(TokenVerificationError):
        await verify_firebase_token(token, PROJECT_ID, cert_fetcher=fetcher, cert_cache=CertCache())


@pytest.mark.asyncio
async def test_malformed_token_is_rejected():
    fetcher = _fetcher_with_certs({KID: _CERT_PEM})

    with pytest.raises(TokenVerificationError):
        await verify_firebase_token("not-a-jwt", PROJECT_ID, cert_fetcher=fetcher, cert_cache=CertCache())
