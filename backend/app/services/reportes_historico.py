"""Blob-persisted accumulation pool behind `dashboard_refresh.fetch_reportes()`
(rolling-window-plus-backfill design, replacing the old fixed-floor
`REPORTES_DESDE` re-download-everything-every-run behavior).

Why a pool instead of "fetch only what's new since last run": the
atencionsismo `informe/json` API's `desde_utc`/`hasta_utc` params filter
`reporte.creadoEn` (creation date) ONLY — there is no "updated since"
parameter (see `docs/api-informe-json (docs-api-atencionsismo).md`). A
report's `estadoVerificacion` (Reportado -> Asignado -> Visitado ->
Evaluación especializada) and its sticker can change long after
`creadoEn`, so a naive "only fetch reports created after the last
watermark" cache would go permanently blind to status changes on any
report it already fetched once. This module splits the universe into two
regimes instead:

- **Rolling window** (`ROLLING_WINDOW_DAYS`): re-walked in FULL on every
  refresh, so any report created recently — and therefore still likely to
  move through the verification pipeline — always reflects its current
  state. `rolling_start_ms`/`rolling_start_date` compute this window's
  floor from "now".
- **Historical backfill**: everything older than the rolling window is
  treated as settled/frozen (an explicit, accepted tradeoff — a report
  that changes state weeks after creation would be missed, but that is
  judged rare enough to trade for bounded per-run cost) and is therefore
  only ever fetched ONCE, walking backward in `BACKFILL_CHUNK_DAYS`-wide
  chunks, one chunk per refresh run, tracked by `backfill_frontier_ms` in
  the persisted state (`STATE_BLOB`) until `is_genesis_reached` sees an
  empty chunk and flips `backfill_complete` for good.

Both the accumulated pool (`POOL_BLOB`, keyed by report id so a rolling
overwrite or a backfill merge is a cheap dict op) and the backfill
progress state (`STATE_BLOB`) live in Vercel Blob via `app.services.blob_lkg`
— never Firestore, matching this project's Firestore-quota sensitivity
(see `reportes_panel_state.py`'s own docstring) and its "one Blob client"
convention. Every function here is either a pure helper (no I/O, trivially
unit-testable) or a thin fail-soft Blob read/write — the actual
`atencionsismo.day_walk` network calls are made by the caller
(`app.jobs.dashboard_refresh.fetch_reportes`), not by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services import blob_lkg
from app.services.atencionsismo import DAY_MS

POOL_BLOB = "data/reportes_historico.json"  # {id: record}, id-keyed for cheap merge
STATE_BLOB = "data/reportes_historico_state.json"  # {"backfill_frontier_ms", "backfill_complete"}

ROLLING_WINDOW_DAYS = 30
BACKFILL_CHUNK_DAYS = 7
BACKFILL_CHUNK_TIMEOUT_S = 120
# Skip the backfill step this run if less of the outer fetch_reportes()
# budget remains than this — a slow/stuck backfill chunk must never risk
# the outer 240s timeout that also guards reportes.json/reportes_ciudadanos.json.
#
# MUST exceed BACKFILL_CHUNK_TIMEOUT_S, not just be "some margin": this guard
# only fires BEFORE a chunk starts, so a guard value <= the chunk's own
# timeout can admit a chunk that then takes up to BACKFILL_CHUNK_TIMEOUT_S
# itself, pushing total elapsed past the outer 240s asyncio.wait_for budget —
# which raises CancelledError (a BaseException, NOT caught by this module's
# own `except Exception:`), propagating out of fetch_reportes() and
# discarding the whole run's already-fetched data. 150 = 120s chunk timeout
# + 30s safety margin for the write phase that follows (including the
# now-budget-checked panel-match step, see dashboard_refresh.py's
# PANEL_MATCH_MIN_REMAINING_BUDGET_S).
BACKFILL_MIN_REMAINING_BUDGET_S = 150


def date_from_ms(ms: int) -> str:
    """`ms` (UTC epoch milliseconds) -> `YYYY-MM-DD`, the string shape
    `atencionsismo.day_walk`'s `desde` parameter expects."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def rolling_start_ms(now_ms: int) -> int:
    """`now_ms` minus `ROLLING_WINDOW_DAYS` days, in ms epoch — the floor of
    the always-fully-re-walked rolling window."""
    return now_ms - ROLLING_WINDOW_DAYS * DAY_MS


def rolling_start_date(now_ms: int) -> str:
    """`rolling_start_ms(now_ms)` as a `YYYY-MM-DD` UTC date string."""
    return date_from_ms(rolling_start_ms(now_ms))


def _floor_to_utc_day(ms: int) -> int:
    """Floor `ms` to that UTC day's midnight, in ms epoch."""
    return (ms // DAY_MS) * DAY_MS


def load_pool() -> dict[str, dict]:
    """The accumulated {id: record} pool from Blob. Fails soft to `{}` on
    ANY problem (`blob_lkg.load_json` itself already guards missing
    token/blob/network/malformed-JSON/wrong-top-level-type). On top of
    that, never trusts a possibly-corrupted dict blindly: a value that
    isn't itself a dict, or whose own `id` field doesn't match the key it's
    stored under, is dropped rather than kept or allowed to raise
    downstream."""
    data = blob_lkg.load_json(POOL_BLOB, dict)
    if not isinstance(data, dict):
        return {}
    pool: dict[str, dict] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        record_id = value.get("id")
        if record_id is None or str(record_id) != key:
            continue
        pool[key] = value
    return pool


def save_pool(pool: dict[str, dict]) -> None:
    """Fire-and-forget persist via `blob_lkg.save_json` — never raises."""
    blob_lkg.save_json(POOL_BLOB, pool)


def load_state() -> dict:
    """`{'backfill_frontier_ms': int|None, 'backfill_complete': bool}`.
    Fails soft to `{}` (fresh-state shape) on ANY problem — missing Blob,
    malformed JSON, or a payload that isn't a dict at all
    (`blob_lkg.load_json` already guards all three)."""
    data = blob_lkg.load_json(STATE_BLOB, dict)
    if not isinstance(data, dict):
        return {}
    return data


def save_state(state: dict) -> None:
    """Fire-and-forget persist via `blob_lkg.save_json` — never raises."""
    blob_lkg.save_json(STATE_BLOB, state)


def merge_records(pool: dict[str, dict], records: list[dict], *, overwrite: bool) -> dict[str, dict]:
    """Returns a NEW dict (never mutates `pool` in place). Records without a
    truthy `id` are skipped (mirrors `dashboard_refresh._dedupe_sorted`'s
    own id-less-record handling).

    `overwrite=True` (rolling-window call site): a record already in
    `pool` under the same id is REPLACED — the rolling window is always
    the freshest truth for anything within it.

    `overwrite=False` (backfill call site): a record already in `pool`
    under the same id is left UNTOUCHED — the historical tail is assumed
    settled, so an older/slower backfill fetch must never clobber
    something the rolling window already established fresher. In
    practice the two ranges shouldn't overlap by construction, but this
    is the safety rule if they ever do (and it also makes a backfill
    chunk that redundantly re-returns an already-pooled id a harmless
    no-op, not a duplicate — the pool is dict-keyed by id either way)."""
    out = dict(pool)
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rid = rec.get("id")
        if not rid:
            continue
        key = str(rid)
        if overwrite or key not in out:
            out[key] = rec
    return out


def next_backfill_chunk(state: dict, rolling_start_ms_value: int) -> tuple[int, int]:
    """`(chunk_start_ms, chunk_end_ms)` for the NEXT backward chunk to walk.

    `chunk_end_ms` is the current frontier (`state['backfill_frontier_ms']`,
    defaulting to `rolling_start_ms_value` on a fresh state) — backfill
    starts exactly where the rolling window's own coverage begins, no gap,
    no overlap. `chunk_start_ms` is `chunk_end_ms` minus exactly
    `BACKFILL_CHUNK_DAYS` days.

    The FIRST chunk's `chunk_end_ms` (the fresh-state default, derived from
    `rolling_start_ms_value`) is floored to that UTC day's midnight
    (`_floor_to_utc_day`) — `rolling_start_ms_value` itself usually isn't
    day-aligned (it's `now_ms` minus a fixed offset). Flooring only here
    makes every SUBSEQUENT frontier value day-aligned by construction too
    (each is exactly `BACKFILL_CHUNK_DAYS * DAY_MS` — itself a whole number
    of days — less than the previous one), which in turn makes
    `dashboard_refresh.py`'s `date_from_ms(chunk_start_ms)` truncation (used
    to build `day_walk`'s `desde` string) a no-op: without this, that
    truncation silently widens every chunk's REAL walked range by up to
    ~24h, overlapping the previous chunk's tail (harmless due to
    `merge_records(..., overwrite=False)`'s id-keyed dedup, but contradicts
    this docstring's own "no gap, no overlap" claim)."""
    chunk_end_ms = state.get("backfill_frontier_ms")
    if not isinstance(chunk_end_ms, int):
        chunk_end_ms = _floor_to_utc_day(rolling_start_ms_value)
    chunk_start_ms = chunk_end_ms - BACKFILL_CHUNK_DAYS * DAY_MS
    return chunk_start_ms, chunk_end_ms


def is_genesis_reached(chunk_records: list[dict]) -> bool:
    """True iff `chunk_records` is empty. Extracted as its own function
    (not an inline `if not chunk_records`) so the policy for "what counts
    as reaching the start of the platform's data" has one obvious place to
    find and extend, and is trivially unit-testable in isolation.

    Assumption, stated plainly: an empty `BACKFILL_CHUNK_DAYS`-wide window
    is treated as "no more history behind this point" — reasonable for a
    disaster-report platform where reports cannot predate the platform's
    own existence, but this is an approximation: a genuine multi-week
    reporting lull mid-history would be misread as genesis (same class of
    known, documented approximation as `cruce_sticker.py`'s own
    heuristics)."""
    return not chunk_records


def advance_state_after_chunk(state: dict, chunk_start_ms: int, *, chunk_was_empty: bool) -> dict:
    """Returns a NEW state dict (never mutates in place). The frontier
    always moves backward by exactly one chunk — `backfill_frontier_ms` =
    `chunk_start_ms` — whether or not this chunk found records: an empty
    chunk still means "do not re-walk this range again".

    `backfill_complete` becomes True iff `chunk_was_empty`, else it keeps
    whatever it already was (`state.get('backfill_complete', False)`) —
    once True it stays True; a later run must never un-set it. This
    function is only ever called by the caller for a NON-complete state to
    begin with, but stays defensive about that invariant regardless."""
    new_state = dict(state)
    new_state["backfill_frontier_ms"] = chunk_start_ms
    new_state["backfill_complete"] = True if chunk_was_empty else bool(state.get("backfill_complete", False))
    return new_state
