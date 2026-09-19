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


# ── Read-billing Firestore fake (efficiency follow-up: U from the roster, probes) ──


class _Agg:
    def __init__(self, value: int) -> None:
        self.value = value


class LedgerSnapshot:
    def __init__(self, doc_id: str, data: dict | None) -> None:
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self) -> dict | None:
        return dict(self._data) if self._data is not None else None


class _LedgerAggregation:
    def __init__(self, db: "LedgerFirestore", name: str) -> None:
        self._db, self._name = db, name

    def get(self) -> list:
        self._db.ledger.hit(f"attempt:count:{self._name}")
        self._db._maybe_fail("count", self._name)
        self._db.ledger.hit(f"count:{self._name}")
        n = len(self._db.stores.setdefault(self._name, {}))
        self._db._bill(self._name, max(1, -(-n // 1000)))  # 1 read per 1000 index entries matched, min 1
        return [[_Agg(n)]]


class _LedgerQuery:
    def __init__(self, db: "LedgerFirestore", name: str, docs: list[LedgerSnapshot]) -> None:
        self._db, self._name, self._docs = db, name, docs
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None

    def order_by(self, field: str, direction: str = "ASCENDING") -> "_LedgerQuery":
        self._order = (field, direction == "DESCENDING")
        return self

    def limit(self, n: int) -> "_LedgerQuery":
        self._limit = n
        return self

    def get(self) -> list[LedgerSnapshot]:
        docs = list(self._docs)
        if self._order is not None:
            field, desc = self._order
            self._db.ledger.hit(f"attempt:newest:{self._name}")
            self._db._maybe_fail("newest", self._name)
            self._db.ledger.hit(f"newest:{self._name}")
            # Firestore excludes documents that lack the ordered field.
            docs = [d for d in docs if (d.to_dict() or {}).get(field) is not None]
            docs.sort(key=lambda d: (d.to_dict() or {})[field], reverse=desc)
        if self._limit is not None:
            docs = docs[: self._limit]
        self._db._bill(self._name, max(1, len(docs)))
        return docs


class _LedgerDocRef:
    def __init__(self, db: "LedgerFirestore", name: str, doc_id: str) -> None:
        self._db, self._name, self.id = db, name, doc_id

    def get(self) -> LedgerSnapshot:
        self._db._bill(self._name, 1)
        return LedgerSnapshot(self.id, self._db.stores.setdefault(self._name, {}).get(self.id))

    def set(self, data: dict, merge: bool = False) -> None:
        store = self._db.stores.setdefault(self._name, {})
        current = dict(store.get(self.id, {})) if merge else {}
        current.update(data)
        store[self.id] = current

    def delete(self) -> None:
        self._db.stores.setdefault(self._name, {}).pop(self.id, None)


class _LedgerCollection:
    def __init__(self, db: "LedgerFirestore", name: str) -> None:
        self._db, self._name = db, name

    def _snaps(self) -> list[LedgerSnapshot]:
        return [LedgerSnapshot(i, d) for i, d in self._db.stores.setdefault(self._name, {}).items()]

    def document(self, doc_id: str) -> _LedgerDocRef:
        return _LedgerDocRef(self._db, self._name, doc_id)

    def get(self) -> list[LedgerSnapshot]:
        self._db.ledger.hit(f"attempt:scan:{self._name}")
        self._db._maybe_fail("scan", self._name)
        self._db.ledger.hit(f"scan:{self._name}")
        snaps = self._snaps()
        self._db._bill(self._name, max(1, len(snaps)))
        return snaps

    def where(self, field: str, op: str, value: object) -> _LedgerQuery:
        assert op == "=="
        return _LedgerQuery(self._db, self._name, [s for s in self._snaps() if (s.to_dict() or {}).get(field) == value])

    def order_by(self, field: str, direction: str = "ASCENDING") -> _LedgerQuery:
        return _LedgerQuery(self._db, self._name, self._snaps()).order_by(field, direction)

    def count(self) -> _LedgerAggregation:
        if self._db.count_unsupported:
            raise AttributeError("count")
        return _LedgerAggregation(self._db, self._name)


class LedgerFirestore:
    """In-memory Firestore that BILLS reads the way Firestore does (one per
    document returned, a document lookup counts even when it is missing, a
    `count()` costs one read per 1000 entries with a minimum of one, any query
    at least one). `ledger` keys: `reads:<collection>`, `scan:<c>`, `count:<c>`,
    `newest:<c>` (successful calls; `attempt:<kind>:<c>` also counts the failed
    ones) and `get_all_calls`. Failure switches: `fail_on = {("scan",
    "survey_cali"), ...}` (kinds: scan, count, newest, get_all) and
    `count_unsupported`. Counts only; nothing here captures payloads."""

    def __init__(self, stores: dict, ledger: CallLedger | None = None) -> None:
        self.stores = stores
        self.ledger = ledger if ledger is not None else CallLedger()
        self.fail_on: set[tuple[str, str]] = set()
        self.count_unsupported = False

    def _bill(self, name: str, n: int) -> None:
        for _ in range(n):
            self.ledger.hit(f"reads:{name}")

    def _maybe_fail(self, kind: str, name: str) -> None:
        if (kind, name) in self.fail_on:
            raise RuntimeError(f"{kind} {name} 429")

    def reads(self, name: str) -> int:
        return self.ledger[f"reads:{name}"]

    def collection(self, name: str) -> _LedgerCollection:
        return _LedgerCollection(self, name)

    def get_all(self, refs: list, field_paths: list | None = None) -> list[LedgerSnapshot]:
        self.ledger.hit("get_all_calls")
        if refs:
            self._maybe_fail("get_all", refs[0]._name)
        return [ref.get() for ref in refs]

    def transaction(self) -> "_LedgerTransaction":
        return _LedgerTransaction(self)


class _LedgerTransaction:
    """Test-double transaction (`_is_test_double`): writes apply immediately, like
    the other fakes' transactions; the whole-collection read is billed."""

    _is_test_double = True

    def __init__(self, db: LedgerFirestore) -> None:
        self._db = db

    def get(self, collection: _LedgerCollection) -> list[LedgerSnapshot]:
        return collection.get()

    def set(self, ref: _LedgerDocRef, data: dict, merge: bool = False) -> None:
        ref.set(data, merge=merge)
