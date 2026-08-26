"""POST /api/sign — presigned S3 upload URLs for ATC-20 field-form photos.

Ports `services/photo-signer/api/sign.js` verbatim for presign acceptance
semantics — same `CODIGO_RE`, same `MAX_SLOT` source (`SIGNER_MAX_SLOT`,
default 10), same object key shape
(`evaluaciones/{codigo}/foto_{slot}.jpg`), same `ExpiresIn=300`
(inspection-photo-capture spec: "Presign Acceptance Semantics Unchanged").

ONE deliberate change from the legacy signer (inspection-photo-capture spec:
"Unified Token Verification For Signer"): token verification moves from the
legacy signer's independent `accounts:lookup` REST call onto
`Depends(require_auth)` — the SAME RS256 verifier every other route in this
backend uses (`app/auth/verify.py`) — instead of a second, redundant network
round-trip to Firebase. The request shape changes accordingly: the legacy
body was `{idToken, codigo, slot}`; this route reads the token from the
`Authorization: Bearer` header and the body is `{codigo, slot}` only.
"""
from __future__ import annotations

import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.deps import require_auth
from app.credentials import clients as credentials

# s3() is only reachable from this router (backend-platform spec: "A route
# cannot reach an undeclared client"). create_app() unions this into its
# startup validation once the sign router is mounted — see
# credentials/clients.py's module docstring, "Declaration mechanism" note.
REQUIRED_CLIENTS: tuple[str, ...] = ("s3",)

# Verbatim from services/photo-signer/api/sign.js:24.
CODIGO_RE = re.compile(r"^76001-[123]-\d{7,8}$")


def _max_slot() -> int:
    """`SIGNER_MAX_SLOT`, default 10 — read per-request so tests/deploys can
    change it without an app restart, matching the legacy signer's env-var
    contract (`services/photo-signer/api/sign.js:21`)."""
    return int(os.environ.get("SIGNER_MAX_SLOT", "10"))


router = APIRouter()


class SignRequest(BaseModel):
    codigo: str
    slot: int


class SignResponse(BaseModel):
    uploadUrl: str
    publicUrl: str


@router.post("/api/sign", response_model=SignResponse)
def sign(
    body: SignRequest,
    claims: dict[str, Any] = Depends(require_auth),
) -> SignResponse:
    if not CODIGO_RE.match(body.codigo or ""):
        raise HTTPException(status_code=400, detail="bad-request")
    if not (1 <= body.slot <= _max_slot()):
        raise HTTPException(status_code=400, detail="bad-request")

    bucket_client = credentials.s3()
    key = f"evaluaciones/{body.codigo}/foto_{body.slot}.jpg"
    upload_url = bucket_client.client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": bucket_client.bucket,
            "Key": key,
            "ContentType": "image/jpeg",
        },
        ExpiresIn=300,
    )
    public_url = f"https://{bucket_client.bucket}.s3.amazonaws.com/{key}"
    return SignResponse(uploadUrl=upload_url, publicUrl=public_url)
