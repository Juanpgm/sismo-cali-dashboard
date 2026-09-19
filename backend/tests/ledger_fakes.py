"""Shared counting fakes for the efficiency tests (tasks 10.13+, reused by
slice 09b): an injectable clock plus a thread-safe call ledger. No real
sleeps, no network. Counts only; nothing here captures payloads."""
from __future__ import annotations

import threading
from collections import defaultdict


class FakeClock:
    """Callable monotonic clock the tests advance by hand (may go backwards)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class CallLedger:
    """Thread-safe named counters: `ledger.hit("survey")`, `ledger["survey"]`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = defaultdict(int)

    def hit(self, name: str) -> None:
        with self._lock:
            self._counts[name] += 1

    def __getitem__(self, name: str) -> int:
        with self._lock:
            return self._counts.get(name, 0)
