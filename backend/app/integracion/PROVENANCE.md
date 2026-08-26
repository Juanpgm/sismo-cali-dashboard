# Provenance — `backend/app/integracion/`

Every file copied from `integracion_F1` (design.md ADR-2) is recorded here at
copy time, with a `# Ported from Juanpgm/normalizador_data_sismo_cali@<sha>
<original/path> (YYYY-MM-DD)` header in the file itself. Once a job's copy PR
merges, its modules are frozen upstream — fixes land only in this repo.

| File | Source path | Source SHA | Copied date | Job slice | Cut: sheets |
|---|---|---|---|---|---|
| `config.py` | `integracion/config.py` | `4999bfc` | 2026-08-26 | cruce-sticker (7) | dagma + sheets (trimmed to `BOGOTA_TZ`/`CALI_BBOX` only — see file docstring) |
| `coords.py` | `integracion/coords.py` | `4999bfc` | 2026-08-26 | cruce-sticker (7) | none |
| `normalization.py` | `integracion/normalization.py` | `4999bfc` | 2026-08-26 | cruce-sticker (7) | none |
| `runlog.py` | `integracion/runlog.py` | `48a807c` | 2026-08-26 | dashboard-refresh (7) | none |
| `cruce_gestor.py` | `cruce_gestor.py` | `ce51838` | 2026-08-26 | cruce-sticker (7) | none (its own dagma-unrelated "Gestor de Zonas" Apps Script code is dead in this context, not cut — see file docstring) |
| `jobs/cruce_sticker.py` | `cruce_sticker.py`@`b013360` + `job_sticker.py`@`551a73a` (merged) | `b013360`/`551a73a` | 2026-08-26 | cruce-sticker (7) | Firestore access switched from the source's own 3-tier SA resolution to `credentials.sismo()` (design.md ADR-4/ADR-9) |
