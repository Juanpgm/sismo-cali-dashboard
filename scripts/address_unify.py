"""Canonical address key (`address_unify_key`) -- the grouping key shared with
the atencionsismo dashboard.

Faithful Python port of `addressUnifyKey` from atencionsismo's
`lib/geo/normalize-address.ts` (the reference implementation). Both
dashboards bucket survey inspections into buildings by this key, so any
behavioural drift here splits or merges buildings differently from the
reference. Do not "improve" it: change the reference first, then re-port and
regenerate `backend/tests/fixtures/address_unify_golden.json`.

    "KR 47 # 4-157"  ->  "cra:47:4-157"
    "KR 47 # 4 157"  ->  "cra:47:4-157"   (same building)
    "Cl 3 A #19-39, COMUNA 3, Cali"  ->  "cl:3a:19-39"

This is a MATCHING key, not a display value: the human-readable IGAC address
stays in `direccion_norm` (see refresh_data.normalize_direccion).

JS/Python differences handled on purpose:
  * `String(Number(n))` (plate un-padding) is emulated by `_js_number_str`,
    including JS's loss of precision above 2**53 and `e+21` notation.
  * `$` is `\\Z` (JS `$` has no multiline/newline quirk), `\\b`/`[a-z]` are
    ASCII-only (`re.ASCII`), and `\\s` in the one regex that sees raw
    (unfolded) text spells out JS's whitespace set.
"""
from __future__ import annotations

import math
import re
import unicodedata
from decimal import Decimal
from functools import lru_cache

import pandas as pd

_F = re.ASCII

# JS `\s` (ECMAScript WhiteSpace + LineTerminator), spelled out for the raw text.
_JS_WS = "\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff"

# Longest-first. Applied after NFD / lower / punctuation -> space.
_STREET_ALIASES: list[tuple[str, str]] = [
    (r"\bct\b", "tv"),
    (r"\btransversal\b", "tv"),
    (r"\btransv\b", "tv"),
    (r"\btrans\b", "tv"),
    (r"\btrv\b", "tv"),
    (r"\bdiagonal\b", "dg"),
    (r"\bdiag\b", "dg"),
    (r"\bdgn\b", "dg"),
    (r"\bavenida\b", "av"),
    (r"\bavda\b", "av"),
    (r"\bave\b", "av"),
    (r"\bavn\b", "av"),
    (r"\bcarrera\b", "cra"),
    (r"\bkarrera\b", "cra"),
    (r"\bcrra\b", "cra"),
    (r"\bcra\b", "cra"),
    (r"\bkra\b", "cra"),
    (r"\bkrr\b", "cra"),
    (r"\bkr\b", "cra"),
    (r"\bcr\b", "cra"),
    (r"\bcalle\b", "cl"),
    (r"\bcall\b", "cl"),
    (r"\bclle\b", "cl"),
    (r"\bcll\b", "cl"),
    (r"\btorre\b", "torre"),
    (r"\btorr\b", "torre"),
    (r"\btrr\b", "torre"),
    (r"\btor\b", "torre"),
    (r"\bbloque\b", "bloque"),
    (r"\bblock\b", "bloque"),
    (r"\bblq\b", "bloque"),
    (r"\boccidente\b", "oe"),
    (r"\boeste\b", "oe"),
    (r"\bnorte\b", "n"),
    (r"\bnte\b", "n"),
    (r"\beste\b", "e"),
    (r"\bsur\b", "s"),
    (r"\bnumero\b", " "),
    (r"\bnro\b", " "),
    (r"\bnum\b", " "),
    (r"\bno\b", " "),
    (r"\bbis\b", "bis"),
    (r"\btv\b", "tv"),
    (r"\bdg\b", "dg"),
    (r"\bav\b", "av"),
    (r"\bcl\b", "cl"),
    (r"\btr\b", "torre"),
    (r"\bbl\b", "bloque"),
]
_STREET_ALIASES_RE = [(re.compile(p, _F), r) for p, r in _STREET_ALIASES]

_UNIFY_NOISE = [
    (re.compile(p, _F), r)
    for p, r in [
        (r"\bsantiago de cali\b", " "),
        (r"\bvalle del cauca\b", " "),
        (r"\bcolombia\b", " "),
        (r"\bcali\b", " "),
        (r"\bedificio\b", " "),
        (r"\bedif\b", " "),
        (r"\bapartamento\b", " "),
        (r"\bapto\b", " "),
        (r"\bapt\b", " "),
        (r"\bconjunto\b", " "),
        (r"\bconj\b", " "),
    ]
]

_STREET_KINDS = frozenset({"cl", "cra", "av", "tv", "dg"})
_CARDINALS = frozenset({"n", "s", "e", "oe"})

_STREET_PLATE_RE = re.compile(
    r"\b(cl|cra|av|tv|dg)\s+([0-9]+[a-z]{0,2})(?:\s+(?!oe\b|n\b|s\b|e\b|bis\b)([a-z]{1,2})\b)?"
    r"(?:\s+(bis))?(?:\s+(oe|n|s|e)\b)?(?:\s+(bis))?"
    r"(?:\s+([0-9]+[a-z]{0,2})(?:\s+(?:oe|n|s|e)\b)?(?:\s+([0-9]+[a-z]{0,2}))?)?",
    _F,
)
_TORRE_RE = re.compile(r"\btorre\s+([a-z0-9]+)\b", _F)
_BLOQUE_RE = re.compile(r"\bbloque\s+([a-z0-9]+)\b", _F)
_W = "[" + _JS_WS + "]*"
_HASH_PLATE_RE = re.compile(
    r"#" + _W + r"([0-9]+[a-z]{0,2})"
    r"(?:" + _W + r"(?:(?:norte|sur|este|oeste|oe|n|s|e)" + _W + r"[-–]?" + _W + r"|[-–]" + _W + r"))?"
    r"([0-9]+[a-z]{0,2})?",
    _F | re.IGNORECASE,
)
_GRID_STREET_SPACED_RE = re.compile(
    r"\b(cl|cra|av|tv|dg)\s+([0-9]+)\s+([a-z]{1,2})\s+([0-9]+)(?:\s+(bis))?(?:\s+(oe|n|s|e)\b)?", _F
)
_GRID_STREET_PAIR_RE = re.compile(
    r"\b(cl|cra|av|tv|dg)\s+([0-9]+)\s+([a-z]{1,2})\b(?!\s+[0-9])(?:\s+(bis))?(?:\s+(oe|n|s|e)\b)?", _F
)
_GRID_STREET_GLUED_RE = re.compile(
    r"\b(cl|cra|av|tv|dg)\s+([0-9]+[a-z][0-9]+|[0-9]+[a-z]{1,2})(?:\s+(bis))?(?:\s+(oe|n|s|e)\b)?", _F
)
_CON_PLATE_RE = re.compile(r"^\s*con\s+([0-9]+[a-z]{0,2})(?:\s+(?:oe|n|s|e)\b)?(?:\s+([0-9]+[a-z]{0,2}))?", _F)
_BARE_PLATE_RE = re.compile(
    r"^\s*([0-9]+[a-z]{0,2})\s*(?:[-–]\s*)?([0-9]+[a-z]{0,2})\b"
    r"|^\s*([0-9]+)\s+([a-z]{1,2})\s+([0-9]+[a-z]{0,2})\b",
    _F | re.IGNORECASE,
)

_WS_RUN = re.compile(r"\s+", _F)
_DIGITS = re.compile(r"[0-9]+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _squeeze(value: str) -> str:
    return _WS_RUN.sub(" ", value).strip()


def _js_number_str(digits: str) -> str:
    """`String(Number(digits))` for a run of ASCII digits.

    JS parses to an IEEE double and prints the shortest round-trip digits, so
    values above 2**53 lose precision and values >= 1e21 switch to `e+`
    notation; a run too long for a double prints `Infinity`.
    """
    number = float(digits)
    if math.isinf(number):
        return "Infinity"
    if number == 0:
        return "0"
    if number >= 1e21:
        mantissa, _, exponent = f"{number:.17e}".partition("e")
        shortest = repr(number)
        if "e" in shortest:
            mantissa, _, exponent = shortest.partition("e")
        else:  # positional repr never happens at >= 1e21; defensive
            mantissa = shortest
        return f"{mantissa}e+{int(exponent)}"
    return str(int(Decimal(repr(number))))


def _unpad_digits(value: str) -> str:
    """`03-20` and `3-20` are the same plate; `10` stays `10`."""
    return _DIGITS.sub(lambda m: _js_number_str(m.group(0)), value)


def _compact(value: str) -> str:
    return _NON_ALNUM.sub("", value)


def _apply_pairs(value: str, pairs) -> str:
    s = value
    for pattern, repl in pairs:
        s = pattern.sub(repl, s)
    return _squeeze(s)


def fold_address_tokens(value: str) -> str:
    """Accents off, lower, punctuation to space, street aliases. Spaces kept."""
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.category(ch).startswith("M"))
    s = _squeeze(_NON_ALNUM.sub(" ", stripped.lower()))
    if not s:
        return ""
    return _unpad_digits(_apply_pairs(s, _STREET_ALIASES_RE))


def _as_kind(value: str | None) -> str | None:
    return value if value and value in _STREET_KINDS else None


def _as_cardinal(value: str | None) -> str | None:
    return value if value and value in _CARDINALS else None


def _extract_hash_plate(value: str) -> tuple[str, str, str]:
    m = _HASH_PLATE_RE.search(value)
    if not m:
        return "", "", value
    return (
        _unpad_digits(_compact(m.group(1).lower())),
        _unpad_digits(_compact(m.group(2).lower())) if m.group(2) else "",
        _squeeze(f"{value[:m.start()]} {value[m.end():]}"),
    )


def _expand_grid_street_chunk(s: str) -> str:
    head = re.match(r"\s*(cl|cra|av|tv|dg)\s+", s, _F | re.IGNORECASE)
    if not head:
        return s
    start = head.end()
    tail = s[start:]
    tri = re.match(r"([0-9]+)\s+([a-z]{1,2})\s+([0-9]+)(?=\s|\Z)", tail, _F | re.IGNORECASE)
    if tri and tri.group(2).lower() not in _CARDINALS:
        return s[:start] + tri.group(0) + tail[len(tri.group(0)):]
    glued = re.match(r"([0-9]+[a-z][0-9]+)(?=\s|\Z)", tail, _F | re.IGNORECASE)
    if glued:
        split = re.sub(r"^([0-9]+)([a-z])([0-9]+)$", r"\1 \2 \3", glued.group(1), flags=_F | re.IGNORECASE)
        return s[:start] + split + tail[len(glued.group(1)):]
    pair = re.match(r"([0-9]+)\s+([a-z]{1,2})(?=\s|\Z)", tail, _F | re.IGNORECASE)
    if pair and pair.group(2).lower() not in _CARDINALS:
        return s[:start] + pair.group(0) + tail[len(pair.group(0)):]
    duo = re.match(r"([0-9]+[a-z])(?=\s|\Z)", tail, _F | re.IGNORECASE)
    if duo:
        split = re.sub(r"^([0-9]+)([a-z])$", r"\1 \2", duo.group(1), flags=_F | re.IGNORECASE)
        return s[:start] + split + tail[len(duo.group(1)):]
    return s


def _parse_con_plate(rest: str) -> tuple[str, str, str]:
    m = _CON_PLATE_RE.search(rest)
    if not m:
        return "", "", rest
    return m.group(1) or "", m.group(2) or "", _squeeze(rest[m.end():])


def _parse_bare_plate(rest: str) -> tuple[str, str, str]:
    m = _BARE_PLATE_RE.search(rest)
    if not m:
        return "", "", rest
    if m.group(3):
        return _compact(f"{m.group(3)}{m.group(4)}"), m.group(5) or "", _squeeze(rest[m.end():])
    return m.group(1) or "", m.group(2) or "", _squeeze(rest[m.end():])


def _parse_grid_street(s: str):
    """Returns (kind, street, bis, cardinal, rest) or None."""
    spaced = _GRID_STREET_SPACED_RE.search(s)
    if spaced:
        return (
            spaced.group(1),
            _compact(f"{spaced.group(2)}{spaced.group(3)}{spaced.group(4)}"),
            spaced.group(5) == "bis",
            _as_cardinal(spaced.group(6)),
            _squeeze(s[spaced.end():]),
        )
    pair = _GRID_STREET_PAIR_RE.search(s)
    if pair and pair.group(3).lower() not in _CARDINALS:
        return (
            pair.group(1),
            _compact(f"{pair.group(2)}{pair.group(3)}"),
            pair.group(4) == "bis",
            _as_cardinal(pair.group(5)),
            _squeeze(s[pair.end():]),
        )
    glued = _GRID_STREET_GLUED_RE.search(s)
    if glued:
        raw = glued.group(2)
        digits = re.fullmatch(r"([0-9]+)([a-z][0-9]+|[a-z]{0,2})", raw, _F | re.IGNORECASE)
        street = _compact(f"{digits.group(1)}{digits.group(2)}") if digits else _compact(raw)
        return (
            glued.group(1),
            street,
            glued.group(3) == "bis",
            _as_cardinal(glued.group(4)),
            _squeeze(s[glued.end():]),
        )
    return None


def _resolve_plates(plate_a: str, plate_b: str, rest: str) -> tuple[str, str, str]:
    tail = rest
    if not plate_a:
        con_a, con_b, tail = _parse_con_plate(tail)
        plate_a = con_a
        plate_b = con_b or plate_b
    if not plate_a:
        bare_a, bare_b, tail = _parse_bare_plate(tail)
        plate_a = bare_a
        plate_b = bare_b or plate_b
    if not plate_a:
        classic = _STREET_PLATE_RE.search(tail)
        if classic and classic.group(7):
            plate_a = classic.group(7)
            plate_b = classic.group(8) if classic.group(8) is not None else plate_b
            tail = _squeeze(f"{tail[:classic.start()]} {tail[classic.end():]}")
    return plate_a, plate_b, tail


def _parse_address(value: str) -> dict:
    empty = {"kind": None, "street": "", "bis": False, "cardinal": None, "plate_a": "",
             "plate_b": "", "torre": "", "bloque": "", "rest": ""}
    hash_a, hash_b, stripped = _extract_hash_plate(value)
    s = _expand_grid_street_chunk(fold_address_tokens(stripped))
    if not s:
        return empty
    s = _apply_pairs(s, _UNIFY_NOISE)
    torre_m = _TORRE_RE.search(s)
    bloque_m = _BLOQUE_RE.search(s)
    torre = torre_m.group(1) if torre_m else ""
    bloque = bloque_m.group(1) if bloque_m else ""
    s = _squeeze(_BLOQUE_RE.sub(" ", _TORRE_RE.sub(" ", s, count=1), count=1))

    grid = _parse_grid_street(s)
    if grid:
        kind, street, bis, cardinal, grid_rest = grid
        plate_a, plate_b, rest = _resolve_plates(hash_a, hash_b, grid_rest)
        return {"kind": kind, "street": street, "bis": bis, "cardinal": cardinal,
                "plate_a": plate_a, "plate_b": plate_b, "torre": torre, "bloque": bloque, "rest": rest}

    plate = _STREET_PLATE_RE.search(s)
    if not plate:
        return {**empty, "torre": torre, "bloque": bloque, "rest": s}
    rest0 = _squeeze(f"{s[:plate.start()]} {s[plate.end():]}")
    plate_a, plate_b, rest = _resolve_plates(hash_a, hash_b, rest0)
    return {
        "kind": _as_kind(plate.group(1)),
        "street": _compact(f"{plate.group(2) or ''}{plate.group(3) or ''}"),
        "bis": plate.group(4) == "bis" or plate.group(6) == "bis",
        "cardinal": _as_cardinal(plate.group(5)),
        "plate_a": plate_a or plate.group(7) or "",
        "plate_b": plate_b or plate.group(8) or "",
        "torre": torre,
        "bloque": bloque,
        "rest": rest,
    }


def _extras_key(parsed: dict) -> str:
    return "|".join(
        part
        for part in (
            f"torre:{parsed['torre']}" if parsed["torre"] else "",
            f"bloque:{parsed['bloque']}" if parsed["bloque"] else "",
        )
        if part
    )


def _plate_key(parsed: dict) -> str:
    if not parsed["kind"]:
        return ""
    parts = [parsed["kind"], _unpad_digits(parsed["street"])]
    if parsed["bis"]:
        parts.append("bis")
    if parsed["cardinal"]:
        parts.append(parsed["cardinal"])
    plate_a = _unpad_digits(parsed["plate_a"])
    plate_b = _unpad_digits(parsed["plate_b"])
    if plate_a and plate_b:
        parts.append(f"{plate_a}-{plate_b}")
    elif plate_a:
        parts.append(plate_a)
    return ":".join(parts)


@lru_cache(maxsize=65536)
def _unify_key_str(value: str) -> str:
    parsed = _parse_address(value)
    extras = _extras_key(parsed)
    core = _plate_key(parsed)
    if core:
        return f"{core}|{extras}" if extras else core
    fallback = _compact(parsed["rest"])
    if extras and fallback:
        return f"{fallback}|{extras}"
    return extras or fallback


def address_unify_key(value) -> str:
    """Compact via+placa key (`cl:52:3-20`); `""` when there is nothing to key.

    None, NaN, pd.NA and blank values return `""` (the reference treats those
    as "no address", i.e. a null `direccion_norm`). Other values are keyed by
    their text, like `String(value)` in the reference.
    """
    if value is None or value is pd.NA:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer() and abs(value) < 1e21:
            value = int(value)  # JS String(12345.0) === "12345"
    text = str(value).strip()
    if not text:
        return ""
    return _unify_key_str(text)
