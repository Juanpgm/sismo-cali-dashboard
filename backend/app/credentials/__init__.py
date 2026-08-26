"""The ONLY module that reads service-account env vars or constructs
Firestore/S3/Auth clients (design.md ADR-4).

See `clients.py` for the named, memoized accessors (`sismo()`, `dagma()`)
and the `require()` fail-fast validator.
"""
