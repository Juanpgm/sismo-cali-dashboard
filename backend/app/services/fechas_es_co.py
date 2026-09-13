"""Shared es-CO ("18 de agosto de 2026, 06:33 p. m.") date parser (D8, plan
cozy-wobbling-dragonfly W1).

Before this module there were TWO independent regex-based parsers for the
exact same atencionsismo `fechaCreacion` text format, each hardcoding its
own timezone: `app.jobs.planeacion_cruce.parse_fecha_creacion_es` (UTC) and
`app.services.reportes_ciudadanos.parse_fecha_es_co` (America/Bogota). Both
now delegate to `parse_fecha_es_co` here, which takes `tz` EXPLICITLY
instead of hardcoding it — neither caller's own timezone changes (that is
a deliberate no-op refactor; `reportes_ciudadanos`'s Bogota tz for a string
that may actually be UTC is a separate, tracked issue — see the plan's
"Fuera de alcance").

Tolerant of: weekday prefix (optional, discarded), NBSP (U+00A0) and narrow
NBSP (U+202F) anywhere in the text, diacritics on the weekday, "a. m."/
"p. m." with or without the inner space/dots ("a.m.", "am", "pm"), extra
whitespace, and both a 12h clock (with an am/pm marker, `hour % 12 + 12 if
pm`) and a bare 24h `HH:mm[:ss]` clock (no am/pm marker). Never raises:
any unparseable/malformed/wrong-typed input returns `None`.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

# Moved here from `reportes_ciudadanos.py:15` (D8) — re-exported there so
# nothing that imported it from its old home breaks.
BOGOTA = timezone(timedelta(hours=-5))

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# Weekday (optional, discarded) + "D de MES de AAAA[,] " + either a 12h
# clock with an am/pm marker or a bare 24h HH:mm[:ss] with none.
_FECHA_ES_CO_RE = re.compile(
    r"^(?:[a-z]+,\s*)?"
    r"(?P<d>\d{1,2})\s+de\s+(?P<m>[a-z]+)\s+de\s+(?P<y>\d{4}),?\s*"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?"
    r"(?:\s*(?P<ap>a\.?\s*m\.?|p\.?\s*m\.?))?\s*$",
    re.IGNORECASE,
)


def _normalize(texto: str) -> str:
    """NBSP/narrow-NBSP -> regular space, then strip diacritics (NFKD) so
    an accented weekday ("sábado") or month still matches the plain-ASCII
    patterns/table above. Collapses extra internal whitespace too."""
    text = texto.replace(" ", " ").replace(" ", " ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_fecha_es_co(texto: object, *, tz: timezone) -> Optional[datetime]:
    """Parse an es-CO `fechaCreacion`-shaped string into a tz-aware
    `datetime` in the caller-supplied `tz`. Returns `None` for any
    non-string, empty, or unparseable input, or an out-of-range date/time
    (e.g. `31 de febrero`, `29 de febrero` on a non-leap year, a 12h hour
    outside 1-12 with an am/pm marker, a 24h hour outside 0-23) — never
    raises."""
    if not isinstance(texto, str) or not texto.strip():
        return None
    normalized = _normalize(texto)
    m = _FECHA_ES_CO_RE.match(normalized)
    if not m:
        return None
    mes = _MESES.get(m.group("m").lower())
    if mes is None:
        return None
    hour = int(m.group("h"))
    minute = int(m.group("mi"))
    second = int(m.group("s")) if m.group("s") else 0
    ap = m.group("ap")
    if ap:
        # 12h clock: only 1-12 is a valid hour when an am/pm marker is
        # present (copied rule: `hour % 12 + (12 if pm)`).
        if not (1 <= hour <= 12):
            return None
        ap_norm = re.sub(r"[.\s]", "", ap).lower()
        hour = hour % 12
        if ap_norm.startswith("p"):
            hour += 12
    else:
        # Bare 24h clock: 0-23.
        if not (0 <= hour <= 23):
            return None
    try:
        return datetime(int(m.group("y")), mes, int(m.group("d")), hour, minute, second, tzinfo=tz)
    except ValueError:
        return None


def to_iso(dt: datetime) -> str:
    """ISO 8601 with an explicit `+HH:MM`/`-HH:MM` offset — `datetime.isoformat()`
    already never emits a bare `Z` for a tz-aware value (UTC included), so
    this is a thin, explicit alias documenting that guarantee for callers
    that must never see `Z` (e.g. `build_evaluaciones`'s string sort)."""
    return dt.isoformat()
