"""The ONLY module that reads service-account env vars or constructs
Firestore/S3/Auth clients (design.md ADR-4, as amended by proposal.md
Extension 2 — no dagma client).

See `clients.py` for the named, memoized accessor (`sismo()`) and the
`require()` fail-fast validator.
"""
