"""Single entry point every in-process writer of the Firestore `inspectores`
collection calls AFTER its write (design D22, judgment-day W1).

Two in-memory caches derive from that collection and must not outlive an admin
write: the Stickers tab's `action:"list"` picker (`stickers_inspectores_cache`)
and the depuración roster component (`roster_cache`, 30 min TTL). Keeping the
list here means a new writer wires ONE call instead of remembering each cache
(`tests/services/test_roster_invalidation.py` fails when a module starts
touching the collection without it).

It takes `app.state` and looks the caches up by name, so it imports nothing
from the routers (no import cycle: `routers/stickers.py` and
`routers/usuarios.py` both import THIS module, never each other). It never
raises: the write already succeeded, and a broken cache must not turn it into
a 5xx. The log carries the exception TYPE only.

`DepuracionCache.invalidate()` is deliberately NOT called here: its key already
includes the roster CONTENT version, so a changed roster recomputes by itself
and an identical one correctly costs nothing.

Writers outside this process (the legacy Vercel functions `api/stickers.js`
and `api/usuarios.js`) cannot reach these caches; their changes are picked up
when the TTL expires.
"""
from __future__ import annotations

import logging
from typing import Any

ROSTER_DERIVED_CACHES: tuple[str, ...] = ("stickers_inspectores_cache", "roster_cache")


def invalidate_inspectores_roster(state: Any) -> None:
    for name in ROSTER_DERIVED_CACHES:
        cache = getattr(state, name, None)
        if cache is None:
            continue
        try:
            cache.invalidate()
        except Exception as exc:  # noqa: BLE001 - best effort after a committed write
            logging.warning("roster invalidation: %s.invalidate() failed (%s)", name, type(exc).__name__)
