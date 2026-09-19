#!/usr/bin/env python3
"""Minimal Vercel Blob client for the refresh pipeline (Railway is Python-only,
so we can't use @vercel/blob). Uploads the dashboard's data files to the public
Blob store at STABLE pathnames (overwrite in place) — no git commit, no Vercel
deploy per refresh. Reads are public + CDN-cached; only writes need the token.

Env:
  BLOB_READ_WRITE_TOKEN   secret, rw token for the store (set in Railway).
  BLOB_PRIVATE_TOKEN      optional; token of the separate PRIVATE store. Used
                          by `upload(access="private")` AND
                          `download_authenticated` so publish and read hit
                          the SAME store (falls back to BLOB_READ_WRITE_TOKEN
                          when unset/blank).

Usage:
  python blob_sync.py upload <localPath> <pathname> [--max-age N] [--content-type T]
  python blob_sync.py download <pathname> <localPath>   # best-effort; exits 0 if missing

`upload` prints the public URL. Overwrites the same pathname every run, so the
URL is stable and the dashboard can hardcode it. See web/js/data.js BLOB_BASE.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse

# Vercel Blob HTTP API contract (mirrors @vercel/blob so we don't guess):
#   Uploads:  PUT https://vercel.com/api/blob/?pathname=<urlencoded>  (pathname
#             is a QUERY param, not the URL path) + x-api-version header.
#   Reads:    GET https://{storeId}.public.blob.vercel-storage.com/{pathname}
# BLOB_API_VERSION tracks @vercel/blob; bump if Vercel deprecates it.
API_UPLOAD_BASE = "https://vercel.com/api/blob/?pathname={pathname}"
PUBLIC_HOST_TMPL = "https://{store}.public.blob.vercel-storage.com/{pathname}"
PRIVATE_HOST_TMPL = "https://{store}.private.blob.vercel-storage.com/{pathname}"
BLOB_API_VERSION = "12"


def _quote_path(pathname: str) -> str:
    """Percent-encode a pathname for interpolation into a host URL, keeping
    `/` separators."""
    return urllib.parse.quote(pathname, safe="/")


def _token() -> str:
    tok = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if not tok:
        sys.exit("BLOB_READ_WRITE_TOKEN no está seteado.")
    return tok


def private_token_with_name() -> tuple[str, str]:
    """Single resolver for the PRIVATE store (the `referencia/inspectores`
    bundle, design.md D8): `BLOB_PRIVATE_TOKEN` first, falling back to
    `BLOB_READ_WRITE_TOKEN` when the former is unset/blank. Returns
    `(var_name, token)` — the NAME of the variable the token came from (for
    error labels; never the value) — or `("", "")` when neither is usable.
    Every private-store operation (write AND read) must go through this so
    they can never resolve to different stores; the public-store paths
    (`upload` public, `download`) keep `_token()`."""
    for name in ("BLOB_PRIVATE_TOKEN", "BLOB_READ_WRITE_TOKEN"):
        tok = os.environ.get(name, "").strip()
        if tok:
            return name, tok
    return "", ""


def private_token() -> str:
    """Token half of `private_token_with_name()`; "" when neither is usable."""
    return private_token_with_name()[1]


def _store_id(token: str, var_name: str = "BLOB_READ_WRITE_TOKEN") -> str:
    # Token shape: vercel_blob_rw_<STOREID>_<secret>. The store host uses the
    # lowercased store id. Deriving it here keeps the caller from having to pass
    # the store id around. `var_name` only labels the error; the token value
    # is never included in it. The store id becomes a hostname label, so it
    # must be strictly alphanumeric: anything else (`.`, `/`, `?`, `@`, `#`…)
    # would let a crafted token redirect the Bearer to another host.
    parts = token.split("_")
    if len(parts) < 5 or parts[0] != "vercel" or parts[1] != "blob":
        sys.exit(f"{var_name} con formato inesperado.")
    store = parts[3].lower()
    if not re.fullmatch(r"[a-z0-9]+", store):
        sys.exit(f"{var_name} con formato inesperado.")
    return store


def _is_private_blob_url(url: object) -> bool:
    """True only when `url`'s HOSTNAME (parsed, not a substring match on the
    whole URL) is a `*.private.blob.vercel-storage.com` host."""
    if not isinstance(url, str):
        return False
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host.endswith(".private.blob.vercel-storage.com")


class _StripAuthOnCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follows redirects but drops the `Authorization` header whenever the
    target's netloc differs from the original request's — urllib's default
    handler would replay the Bearer token to whatever host is redirected to."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old_netloc = urllib.parse.urlsplit(req.full_url).netloc.lower()
            new_netloc = urllib.parse.urlsplit(newurl).netloc.lower()
            if old_netloc != new_netloc:
                for store in (new.headers, new.unredirected_hdrs):
                    for key in [k for k in store if k.lower() == "authorization"]:
                        del store[key]
        return new


def _build_opener(*extra_handlers):
    return urllib.request.build_opener(_StripAuthOnCrossHostRedirect, *extra_handlers)


def upload(local_path: str, pathname: str, max_age: int, content_type: str | None,
           timeout: int = 120, access: str = "public") -> str:
    if access not in ("public", "private"):
        sys.exit("Blob upload: access debe ser 'public' o 'private'.")
    if access == "private":
        # Same resolver as `download_authenticated`: publish and read must
        # land on the SAME (private) store.
        var_name, token = private_token_with_name()
        if not token:
            sys.exit("BLOB_PRIVATE_TOKEN / BLOB_READ_WRITE_TOKEN no están seteados.")
    else:
        var_name, token = "BLOB_READ_WRITE_TOKEN", _token()
    # Validate the token shape (and derive the store) BEFORE any network call.
    store = _store_id(token, var_name)
    host_tmpl = PRIVATE_HOST_TMPL if access == "private" else PUBLIC_HOST_TMPL
    with open(local_path, "rb") as fh:
        body = fh.read()
    ctype = content_type or mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    req = urllib.request.Request(
        API_UPLOAD_BASE.format(pathname=urllib.parse.quote(pathname, safe="")),
        data=body,
        method="PUT",
        headers={
            "authorization": f"Bearer {token}",
            "x-api-version": BLOB_API_VERSION,
            # `access` defaults to "public" (every existing caller, unchanged)
            # — `access="private"` is used by
            # `scripts/publicar_referencia_inspectores.py` (design.md D8):
            # the reference bundle must never get a public URL.
            "x-vercel-blob-access": access,
            "x-content-type": ctype,
            "x-content-length": str(len(body)),
            "x-add-random-suffix": "0",       # stable pathname, no suffix
            "x-allow-overwrite": "1",         # overwrite the previous run's file
            "x-cache-control-max-age": str(max_age),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            payload = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"Blob upload {e.code} para {pathname}: {detail}")
    # The API returns the canonical URL; fall back to constructing it on the
    # host matching `access` (never mint a public URL for a private blob). A
    # private blob's URL is NOT publicly fetchable (see
    # `download_authenticated` below) — it still requires the Bearer token.
    url = payload.get("url")
    if url and access == "private" and not _is_private_blob_url(url):
        # A private publish must never silently succeed against a public blob.
        sys.exit(
            f"Blob upload de {pathname}: se pidio access=private pero la API "
            "devolvio una URL no privada."
        )
    return url or host_tmpl.format(store=store, pathname=_quote_path(pathname))


def download(pathname: str, local_path: str, timeout: int = 120) -> bool:
    token = _token()
    url = PUBLIC_HOST_TMPL.format(store=_store_id(token), pathname=_quote_path(pathname))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as res:
            data = res.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False  # first run — nothing to seed yet
        sys.exit(f"Blob download {e.code} para {pathname}")
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    with open(local_path, "wb") as fh:
        fh.write(data)
    return True


def download_authenticated(pathname: str, local_path: str, timeout: int = 120) -> bool:
    """Same contract as `download`, but sends the Bearer token on the GET —
    required for `access:'private'` blobs (design.md D8: the
    `inspectores-depurado` reference bundle is the only private blob this
    repo publishes today). Deliberately NOT folded into `download` itself:
    every OTHER caller in this repo (`blob_lkg.load_json`,
    `dashboard_refresh.py`, `planeacion_cruce.py`) reads a PUBLIC blob and
    must keep using the unauthenticated path unchanged.

    Token + host: resolved via `private_token()` (BLOB_PRIVATE_TOKEN, else
    BLOB_READ_WRITE_TOKEN) and sent ONLY to the `.private.` host of THAT
    token's own store — never to the public host, never with the public
    store's token when a private one is configured. Redirects are followed,
    but if one points at a different host (netloc) the `Authorization`
    header is dropped from that hop and every later one; same-host redirects
    keep it."""
    var_name, token = private_token_with_name()
    if not token:
        sys.exit("BLOB_PRIVATE_TOKEN / BLOB_READ_WRITE_TOKEN no están seteados.")
    url = PRIVATE_HOST_TMPL.format(
        store=_store_id(token, var_name), pathname=_quote_path(pathname)
    )
    req = urllib.request.Request(url, headers={"authorization": f"Bearer {token}"})
    try:
        with _build_opener().open(req, timeout=timeout) as res:
            data = res.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False  # nothing published yet
        sys.exit(f"Blob download {e.code} para {pathname}")
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    with open(local_path, "wb") as fh:
        fh.write(data)
    return True


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upload")
    up.add_argument("local_path")
    up.add_argument("pathname")
    up.add_argument("--max-age", type=int, default=60)
    up.add_argument("--content-type", default=None)
    dn = sub.add_parser("download")
    dn.add_argument("pathname")
    dn.add_argument("local_path")
    args = p.parse_args()

    if args.cmd == "upload":
        print(upload(args.local_path, args.pathname, args.max_age, args.content_type))
    else:
        ok = download(args.pathname, args.local_path)
        print("ok" if ok else "missing")


if __name__ == "__main__":
    main()
