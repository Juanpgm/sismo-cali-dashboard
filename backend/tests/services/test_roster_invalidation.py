"""One helper for every writer of the `inspectores` collection (judgment-day
W1, design D22): each write path tells every cache derived from the roster.
Unit tests of the helper plus a source-level guard so a NEW writer cannot be
added without wiring it."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace

from app.services.roster_invalidation import invalidate_inspectores_roster

APP_DIR = Path(__file__).resolve().parents[2] / "app"


class _Cache:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self._error = error

    def invalidate(self) -> None:
        self.calls += 1
        if self._error is not None:
            raise self._error


def test_invalidates_the_stickers_tab_list_cache_and_the_roster_component():
    state = SimpleNamespace(stickers_inspectores_cache=_Cache(), roster_cache=_Cache())
    invalidate_inspectores_roster(state)
    assert (state.stickers_inspectores_cache.calls, state.roster_cache.calls) == (1, 1)


def test_missing_caches_are_skipped_never_raise():
    invalidate_inspectores_roster(SimpleNamespace())  # e.g. a bare app in a unit test
    only_one = SimpleNamespace(roster_cache=_Cache())
    invalidate_inspectores_roster(only_one)
    assert only_one.roster_cache.calls == 1


def test_one_failing_invalidate_neither_raises_nor_skips_the_others_and_logs_only_the_type(caplog):
    """The admin write already succeeded: a broken cache must never turn it
    into a 5xx, and the remaining caches must still be invalidated. The log
    carries the exception TYPE only (its message may echo PII)."""
    state = SimpleNamespace(
        stickers_inspectores_cache=_Cache(RuntimeError("cedula 1020735324 exploded")),
        roster_cache=_Cache(),
    )
    with caplog.at_level(logging.DEBUG):
        invalidate_inspectores_roster(state)
    assert state.roster_cache.calls == 1
    assert "1020735324" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_invalidating_twice_in_a_row_is_idempotent():
    state = SimpleNamespace(stickers_inspectores_cache=_Cache(), roster_cache=_Cache())
    invalidate_inspectores_roster(state)
    invalidate_inspectores_roster(state)
    assert state.roster_cache.calls == 2


_WRITES_INSPECTORES = re.compile(r"(?<![A-Z_])INSPECTORES_COLLECTION|collection\(\s*[\"']inspectores[\"']\s*\)")


def test_every_module_touching_the_inspectores_collection_is_known_and_wired():
    """Registry of the in-process writers found by the W1 audit. A module that
    starts touching `inspectores` must be added here and call
    `invalidate_inspectores_roster` after its write (a writer living in another
    process cannot reach the in-memory caches: document it in design.md D22)."""
    writers = {"routers/stickers.py", "routers/usuarios.py"}
    found = {
        path.relative_to(APP_DIR).as_posix()
        for path in APP_DIR.rglob("*.py")
        if _WRITES_INSPECTORES.search(path.read_text(encoding="utf-8"))
    }
    assert found == writers, f"new module touching `inspectores`: {sorted(found ^ writers)}"
    for rel in writers:
        assert "invalidate_inspectores_roster" in (APP_DIR / rel).read_text(encoding="utf-8"), rel
