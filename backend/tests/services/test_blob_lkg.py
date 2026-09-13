"""Focused regression coverage for `app.services.blob_lkg`'s tuning
constants — not a full behavioral suite (the module's actual save/load I/O
is already exercised indirectly through `routers/stickers.py`'s
`EvaluacionesCache` tests)."""
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
