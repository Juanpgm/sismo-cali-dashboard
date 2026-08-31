#!/usr/bin/env python3
"""Minimal Vercel Blob client for the refresh pipeline (Railway is Python-only,
so we can't use @vercel/blob). Uploads the dashboard's data files to the public
Blob store at STABLE pathnames (overwrite in place) — no git commit, no Vercel
deploy per refresh. Reads are public + CDN-cached; only writes need the token.

Env:
  BLOB_READ_WRITE_TOKEN   secret, rw token for the store (set in Railway).

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
BLOB_API_VERSION = "12"


def _token() -> str:
    tok = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if not tok:
        sys.exit("BLOB_READ_WRITE_TOKEN no está seteado.")
    return tok


def _store_id(token: str) -> str:
    # Token shape: vercel_blob_rw_<STOREID>_<secret>. The public host uses the
    # lowercased store id. Deriving it here keeps the caller from having to pass
    # the store id around.
    parts = token.split("_")
    if len(parts) < 5 or parts[0] != "vercel" or parts[1] != "blob":
        sys.exit("BLOB_READ_WRITE_TOKEN con formato inesperado.")
    return parts[3].lower()


def upload(local_path: str, pathname: str, max_age: int, content_type: str | None,
           timeout: int = 120) -> str:
    token = _token()
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
            "x-vercel-blob-access": "public",
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
    # The API returns the canonical public URL; fall back to constructing it.
    return payload.get("url") or PUBLIC_HOST_TMPL.format(store=_store_id(token), pathname=pathname)


def download(pathname: str, local_path: str, timeout: int = 120) -> bool:
    token = _token()
    url = PUBLIC_HOST_TMPL.format(store=_store_id(token), pathname=pathname)
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
