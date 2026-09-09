"""backend/tests/services/test_reportes_historico.py — RED-first tests for
the Blob-persisted rolling-window + historical-backfill pool
(backend/app/services/reportes_historico.py). `blob_lkg` is mocked via
monkeypatch on the module-level reference (`reportes_historico.blob_lkg`)
everywhere — never a real network/Blob call, same convention
test_reportes_panel_state.py uses."""
from __future__ import annotations

from datetime import datetime, timezone

from app.services import reportes_historico as rh
from app.services.atencionsismo import DAY_MS


class _FakeBlob:
    """Stand-in for app.services.blob_lkg — same shape as
    test_reportes_panel_state.py's _FakeBlob."""

    def __init__(self, *, load_result=None, save_result=True):
        self.saved: list[tuple[str, object]] = []
        self.load_calls: list[str] = []
        self._load_result = load_result
        self._save_result = save_result

    def load_json(self, pathname, expected_type):
        self.load_calls.append(pathname)
        return self._load_result

    def save_json(self, pathname, payload):
        self.saved.append((pathname, payload))
        return self._save_result


def _record(rid, **extra):
    return {"id": rid, **extra}


# ── rolling_start_ms / rolling_start_date ──────────────────────────────────


def test_rolling_start_ms_is_30_days_before_now():
    now_ms = 1_700_000_000_000
    assert rh.rolling_start_ms(now_ms) == now_ms - rh.ROLLING_WINDOW_DAYS * DAY_MS


def test_rolling_start_date_is_yyyy_mm_dd_of_rolling_start_ms():
    now_ms = 1_799_452_800_000
    expected = datetime.fromtimestamp(
        rh.rolling_start_ms(now_ms) / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d")
    assert rh.rolling_start_date(now_ms) == expected


# ── load_pool / save_pool ───────────────────────────────────────────────────


def test_load_pool_returns_empty_dict_on_fresh_blob(monkeypatch):
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(load_result=None))
    assert rh.load_pool() == {}


def test_load_pool_returns_stored_dict(monkeypatch):
    stored = {"1": _record("1"), "2": _record("2")}
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(load_result=stored))
    assert rh.load_pool() == stored


def test_load_pool_drops_list_payload_defensively(monkeypatch):
    # blob_lkg.load_json(POOL_BLOB, dict) already returns None for a
    # wrong-typed payload in the real implementation, but load_pool must
    # not trust a fake/mocked blob_lkg blindly either.
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(load_result=["not", "a", "dict"]))
    assert rh.load_pool() == {}


def test_load_pool_drops_non_dict_values(monkeypatch):
    stored = {"1": _record("1"), "2": "not-a-dict"}
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(load_result=stored))
    assert rh.load_pool() == {"1": _record("1")}


def test_load_pool_drops_entries_where_key_does_not_match_record_id(monkeypatch):
    stored = {"1": _record("2")}  # key "1" but record id "2"
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(load_result=stored))
    assert rh.load_pool() == {}


def test_save_pool_calls_blob_lkg_save_json(monkeypatch):
    fake = _FakeBlob()
    monkeypatch.setattr(rh, "blob_lkg", fake)
    pool = {"1": _record("1")}
    rh.save_pool(pool)
    assert fake.saved == [(rh.POOL_BLOB, pool)]


def test_save_pool_does_not_raise_when_save_returns_false(monkeypatch):
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(save_result=False))
    rh.save_pool({"1": _record("1")})  # must not raise


# ── load_state / save_state ─────────────────────────────────────────────────


def test_load_state_returns_empty_dict_on_fresh_blob(monkeypatch):
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(load_result=None))
    assert rh.load_state() == {}


def test_load_state_returns_stored_state(monkeypatch):
    state = {"backfill_frontier_ms": 123, "backfill_complete": False}
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(load_result=state))
    assert rh.load_state() == state


def test_load_state_fails_soft_on_wrong_shaped_payload(monkeypatch):
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(load_result=["not", "a", "dict"]))
    assert rh.load_state() == {}


def test_save_state_calls_blob_lkg_save_json(monkeypatch):
    fake = _FakeBlob()
    monkeypatch.setattr(rh, "blob_lkg", fake)
    state = {"backfill_frontier_ms": 1, "backfill_complete": True}
    rh.save_state(state)
    assert fake.saved == [(rh.STATE_BLOB, state)]


def test_save_state_does_not_raise_when_save_returns_false(monkeypatch):
    monkeypatch.setattr(rh, "blob_lkg", _FakeBlob(save_result=False))
    rh.save_state({"backfill_complete": False})  # must not raise


# ── merge_records ────────────────────────────────────────────────────────────


def test_merge_records_empty_pool_and_empty_records():
    assert rh.merge_records({}, [], overwrite=True) == {}
    assert rh.merge_records({}, [], overwrite=False) == {}


def test_merge_records_skips_records_without_id():
    out = rh.merge_records({}, [{"estado": "x"}, {"id": None}, {"id": ""}], overwrite=True)
    assert out == {}


def test_merge_records_overwrite_true_replaces_existing_id_with_differing_content():
    pool = {"1": _record("1", estado="Reportado")}
    new = [_record("1", estado="Visitado")]
    out = rh.merge_records(pool, new, overwrite=True)
    assert out["1"]["estado"] == "Visitado"


def test_merge_records_overwrite_false_leaves_existing_id_untouched_with_differing_content():
    pool = {"1": _record("1", estado="Reportado")}
    new = [_record("1", estado="Visitado")]
    out = rh.merge_records(pool, new, overwrite=False)
    assert out["1"]["estado"] == "Reportado"


def test_merge_records_never_mutates_input_pool():
    pool = {"1": _record("1", estado="Reportado")}
    before = dict(pool)
    rh.merge_records(pool, [_record("1", estado="Visitado")], overwrite=True)
    assert pool == before


def test_merge_records_adds_new_ids_regardless_of_overwrite_flag():
    out_true = rh.merge_records({}, [_record("1")], overwrite=True)
    out_false = rh.merge_records({}, [_record("2")], overwrite=False)
    assert out_true == {"1": _record("1")}
    assert out_false == {"2": _record("2")}


def test_merge_records_overwrite_false_redundant_refetch_does_not_duplicate():
    # A backfill chunk can legitimately re-return a record already in the
    # pool from an earlier overlapping fetch — dict-by-id merge must be a
    # no-op, not a duplicate.
    pool = {"1": _record("1", estado="Reportado")}
    out = rh.merge_records(pool, [_record("1", estado="Reportado")], overwrite=False)
    assert out == {"1": _record("1", estado="Reportado")}
    assert len(out) == 1


# ── next_backfill_chunk ──────────────────────────────────────────────────────


def test_next_backfill_chunk_fresh_state_starts_at_rolling_start_with_no_gap():
    rolling_start = 1_700_000_000_000
    chunk_start, chunk_end = rh.next_backfill_chunk({}, rolling_start)
    assert chunk_end == rolling_start
    assert chunk_start == rolling_start - rh.BACKFILL_CHUNK_DAYS * DAY_MS


def test_next_backfill_chunk_width_is_exactly_backfill_chunk_days():
    rolling_start = 1_700_000_000_000
    chunk_start, chunk_end = rh.next_backfill_chunk({}, rolling_start)
    assert chunk_end - chunk_start == rh.BACKFILL_CHUNK_DAYS * DAY_MS


def test_next_backfill_chunk_uses_existing_frontier():
    state = {"backfill_frontier_ms": 1_600_000_000_000}
    chunk_start, chunk_end = rh.next_backfill_chunk(state, 1_700_000_000_000)
    assert chunk_end == 1_600_000_000_000
    assert chunk_start == 1_600_000_000_000 - rh.BACKFILL_CHUNK_DAYS * DAY_MS


# ── is_genesis_reached ───────────────────────────────────────────────────────


def test_is_genesis_reached_true_for_empty_chunk():
    assert rh.is_genesis_reached([]) is True


def test_is_genesis_reached_false_when_records_present():
    assert rh.is_genesis_reached([_record("1")]) is False


# ── advance_state_after_chunk ────────────────────────────────────────────────


def test_advance_state_after_chunk_empty_chunk_marks_complete():
    state = {"backfill_frontier_ms": 1_700_000_000_000, "backfill_complete": False}
    new_state = rh.advance_state_after_chunk(state, 1_600_000_000_000, chunk_was_empty=True)
    assert new_state["backfill_complete"] is True
    assert new_state["backfill_frontier_ms"] == 1_600_000_000_000


def test_advance_state_after_chunk_non_empty_chunk_still_advances_frontier():
    state = {"backfill_frontier_ms": 1_700_000_000_000, "backfill_complete": False}
    new_state = rh.advance_state_after_chunk(state, 1_600_000_000_000, chunk_was_empty=False)
    assert new_state["backfill_complete"] is False
    assert new_state["backfill_frontier_ms"] == 1_600_000_000_000


def test_advance_state_after_chunk_never_mutates_input_state():
    state = {"backfill_frontier_ms": 1_700_000_000_000, "backfill_complete": False}
    before = dict(state)
    rh.advance_state_after_chunk(state, 1_600_000_000_000, chunk_was_empty=True)
    assert state == before


def test_advance_state_after_chunk_once_complete_stays_complete():
    # advance_state_after_chunk is only ever called by fetch_reportes() for a
    # NON-complete state, but stays defensive: a stray call on an
    # already-complete state must never flip backfill_complete back to False.
    state = {"backfill_frontier_ms": 1_600_000_000_000, "backfill_complete": True}
    new_state = rh.advance_state_after_chunk(state, 1_500_000_000_000, chunk_was_empty=False)
    assert new_state["backfill_complete"] is True


# ── two consecutive chunks walk adjoining, non-overlapping windows ─────────


def test_two_consecutive_backfill_chunks_walk_adjoining_non_overlapping_windows():
    rolling_start = 1_700_000_000_000
    state: dict = {}
    chunk1_start, chunk1_end = rh.next_backfill_chunk(state, rolling_start)
    state = rh.advance_state_after_chunk(state, chunk1_start, chunk_was_empty=False)
    chunk2_start, chunk2_end = rh.next_backfill_chunk(state, rolling_start)
    state = rh.advance_state_after_chunk(state, chunk2_start, chunk_was_empty=False)

    assert chunk1_end == rolling_start
    assert chunk1_start == rolling_start - rh.BACKFILL_CHUNK_DAYS * DAY_MS
    assert chunk2_end == chunk1_start  # adjoining: chunk2 ends exactly where chunk1 began
    assert chunk2_start == chunk1_start - rh.BACKFILL_CHUNK_DAYS * DAY_MS
    assert chunk1_start != chunk2_start and chunk1_end != chunk2_end
