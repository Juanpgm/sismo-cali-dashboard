"""Blob-backed last-known-good (LKG) payload store for the web routers'
process-lifetime TTL caches (`routers/stickers.py`'s `EvaluacionesCache`,
`routers/sticker_status.py`'s `StickerStatusCache`).

Why: serve-stale-on-error (commit 39b0dc3) only helps while the process
holds a prior payload — every Railway deploy restarts the process, so a
cold start during a sustained Firestore 429 outage still surfaced a bare
502 with NOTHING to show. Persisting each successful fetch to Vercel Blob
gives a restarted process something to fall back to.

Contract (mirrors `jobs/dashboard_refresh.py`'s `_load_contacto_hashes`/
`_publish_contacto_hashes` and `jobs/planeacion_cruce.py`'s
`load_resolved_cache`/`publish_resolved_cache`):

- Reuses `deploy/blob_sync.py` — the repo's one Blob client — never a new
  HTTP client.
- `save_json` is fire-and-forget: any failure (missing token, network,
  API error) is logged and swallowed; a Blob problem must never break the
  route. Returns True only on a confirmed upload so callers can hash-gate
  correctly (retry next window on failure).
- `load_json` returns None on ANY failure INCLUDING a wrong-shaped payload
  (the `_load_contacto_hashes` malformed-Blob BLOCKER precedent: a
  malformed Blob payload must fail to the caller's raise path, never be
  served as if it were data).
- A missing BLOB_READ_WRITE_TOKEN disables both directions fail-soft and
  is logged once per process. `blob_sync` `sys.exit()`s on a missing
  token/API error, hence the SystemExit catches below.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_DIR = REPO_ROOT / "deploy"

# deploy/blob_sync.py is a plain script, not a package (COPYd verbatim into
# the image per ADR-1) — imported directly rather than re-implemented, same
# "call it, don't duplicate it" note app/jobs/dashboard_refresh.py carries.
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))
import blob_sync  # noqa: E402  (path must be set up first)

# Short timeout on BOTH directions: this module runs inside (or right next
# to) the web request path — a hung Blob must degrade in seconds, never
# inherit blob_sync's cron-friendly 120s default (a cold-start 502 turning
# into a 2-minute hang).
_TIMEOUT_S = 10

_warned_no_token = False


def _token_available() -> bool:
    """Fail-soft gate: without BLOB_READ_WRITE_TOKEN there is no fallback
    (and no persistence) — log that once per process instead of letting
    `blob_sync`'s `sys.exit()` spam a warning every TTL window."""
    global _warned_no_token
    if os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip():
        return True
    if not _warned_no_token:
        logging.warning(
            "blob_lkg: BLOB_READ_WRITE_TOKEN ausente; fallback/persistencia Blob deshabilitados"
        )
        _warned_no_token = True
    return False


def payload_hash(payload: Any) -> str:
    """sha256 of a stable JSON serialization — the write gate's change
    signal, same idea as `dashboard_refresh._contacto_hash` (never hash a
    volatile field-free timestamp; these payloads carry none)."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def save_json(pathname: str, payload: Any) -> bool:
    """Fire-and-forget upload of `payload` as JSON to Blob `pathname`.
    Returns True iff the upload succeeded; NEVER raises."""
    if not _token_available():
        return False
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, default=str)
            tmp = fh.name
        blob_sync.upload(tmp, pathname, 0, "application/json", timeout=_TIMEOUT_S)
        return True
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - non-critical, retried next change
        logging.warning("blob_lkg: no pude publicar %s (%s); no es crítico", pathname, exc)
        return False
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def load_json(pathname: str, expected_type: type) -> Any | None:
    """Fetch the last-good payload from Blob `pathname`. Returns None on ANY
    failure (missing token, missing blob, network, malformed JSON, payload
    not an `expected_type` instance) so the caller falls back to its own
    raise path — never serves a wrong-shaped payload as data."""
    if not _token_available():
        return None
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        if not blob_sync.download(pathname, tmp, timeout=_TIMEOUT_S):
            return None  # 404 -> nothing persisted yet
        data = json.loads(Path(tmp).read_text(encoding="utf-8"))
        if not isinstance(data, expected_type):
            logging.warning(
                "blob_lkg: payload de %s con forma inválida (%s, se esperaba %s); descartado",
                pathname, type(data).__name__, expected_type.__name__,
            )
            return None
        return data
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - fallback is "no fallback"
        logging.warning("blob_lkg: no pude leer %s (%s); sin fallback disponible", pathname, exc)
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
