"""Cron entrypoints, one module per Railway cron service.

Each is run as `python -m app.jobs.<name>` (design.md ADR-6). `normalizador`
does not migrate here — it stays on the legacy `integracion_F1` image until
slice 9 (EDAN Google Sheet push, out of scope per the Scope Exclusion
Addendum).
"""
