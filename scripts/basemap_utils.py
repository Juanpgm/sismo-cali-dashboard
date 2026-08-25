"""Shared helpers for working with the Cali basemap GeoJSON files.

Both `prepare_basemaps.py` (which produces the slim polygons served to the
frontend) and `refresh_data.py` (which does the point-in-polygon spatial
join) must agree on:
  - which property in the source GeoJSON holds the human-readable name
  - how a stable `id` is derived from that name
  - how a corrupted (U+FFFD mojibake) name gets repaired

Keeping that logic in one place guarantees the `comuna` / `barrio_geo`
values written into `inspections.json` line up with the `name` values in
`comunas.geojson` / `barrios.geojson`, so the frontend can join choropleth
aggregates to polygons by name.

`refresh_data.py` does not re-derive polygon names on its own: it reads the
already-repaired `web/data/comunas.geojson` / `web/data/barrios.geojson`
produced by `prepare_basemaps.py` for its spatial join. That is a stronger
consistency guarantee than repairing twice from the raw basemaps and hoping
both runs agree — see `refresh_data.spatial_join`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Source properties that hold the polygon's display name, per basemap file.
# Both basemaps are raw shapefile-to-GeoJSON conversions (scripts/shp_to_geojson.py,
# 2026-08-25) that ship the DBF's own field names — no `comuna_corregimiento` /
# `barrio_vereda` property exists in them (that was a property added by an
# earlier, now-replaced version of these files). Each layer combines two
# related feature types in one FeatureCollection (comunas + corregimientos;
# barrios + veredas), mutually exclusive per feature except for 3 barrio rows
# that also carry a leftover VEREDA value from an upstream table join — see
# get_barrio_name.
COMUNA_NOMBRE_PROPERTY = "NOMBRE"  # set only on comuna features, e.g. "Comuna 6"
COMUNA_NUMBER_PROPERTY = "COMUNA"  # numeric comuna id, e.g. 6 — used to rebuild "COMUNA 06"
COMUNA_CORREGIMIENTO_PROPERTY = "CORREGIMIE"  # set only on corregimiento features, e.g. "Pance"
BARRIO_PROPERTY = "BARRIO"  # set only on barrio (urban) features, e.g. "Chiminangos II"
VEREDA_PROPERTY = "VEREDA"  # set only on vereda (rural) features

COMUNAS_FILE = "basemaps/comunas_corregimientos.geojson"
BARRIOS_FILE = "basemaps/barrios_veredas.geojson"


def slugify(name: str) -> str:
    """Turn a display name into a stable, URL-safe ASCII id.

    'COMUNA 06' -> 'comuna-06', 'Los Farallones' -> 'los-farallones'.
    Not guaranteed unique on its own when two features share a name; callers
    that need uniqueness (e.g. `prepare_basemaps.py`) append a numeric suffix.
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return slug or "unknown"


def get_comuna_name(properties: dict) -> str | None:
    """'COMUNA 06' for an actual comuna — zero-padded 2-digit, rebuilt from the
    numeric COMUNA field so it matches the dashboard's historical naming
    (inspection records already tagged `comuna: "COMUNA 06"` by prior pipeline
    runs) — else the corregimiento's own name unchanged, e.g. 'Pance'."""
    nombre = properties.get(COMUNA_NOMBRE_PROPERTY)
    if isinstance(nombre, str) and nombre.strip():
        try:
            n = int(properties.get(COMUNA_NUMBER_PROPERTY))
        except (TypeError, ValueError):
            return nombre.strip()  # unexpected shape — NOMBRE as-is beats no name at all
        return f"COMUNA {n:02d}"
    correg = properties.get(COMUNA_CORREGIMIENTO_PROPERTY)
    return correg.strip() if isinstance(correg, str) else correg


def get_barrio_name(properties: dict) -> str | None:
    """BARRIO when set (a real urban-barrio polygon) — 3/440 rows also carry a
    leftover VEREDA value from an upstream table join; BARRIO is the actual
    polygon identity there, so it wins. VEREDA otherwise."""
    barrio = properties.get(BARRIO_PROPERTY)
    if isinstance(barrio, str) and barrio.strip():
        return barrio.strip()
    vereda = properties.get(VEREDA_PROPERTY)
    return vereda.strip() if isinstance(vereda, str) else vereda


# --- U+FFFD mojibake repair for barrio_vereda names -------------------------
#
# `basemaps/barrios_veredas.geojson` has 76/433 barrio names where an
# accented character was lossily replaced with the Unicode replacement
# character (U+FFFD, "\ufffd") at some earlier export/re-encode step. The
# original byte is gone; it cannot be recovered, only *inferred*. Repair
# happens per whitespace-delimited token (word), in confidence order:
#
#   (a) reference  — the token (accent/case-insensitive, positions with
#       U+FFFD ignored) matches a word from the free-text `Barrio/vereda:`
#       survey answers in the source xlsx, AND that survey word actually
#       carries a diacritic at the corrupted position.
#   (b) dictionary — the token matches a small built-in table of common
#       Spanish words / well-known Colombian toponyms and historical names
#       actually observed among the corrupted tokens (Simón Bolívar,
#       Belalcázar, Nariño, Siloé, Cañaveralejo, ...).
#   (c) orthography — narrow heuristic: a single U+FFFD strictly between two
#       vowels is, in the large majority of Spanish words, an intervocalic
#       'ñ' (campiña, montaña, ...).
#   (d) fallback — no confident source. The U+FFFD character(s) are dropped
#       (not replaced with a placeholder glyph) and the token is logged so a
#       human can review it. No U+FFFD ever reaches the exported JSON/GeoJSON.

ACCENT_CHARS = set("áéíóúñüÁÉÍÓÚÑÜ")

# (b) token (accent-stripped, lowercased) -> canonical, correctly accented
# form. Every entry here was verified against the actual corrupted tokens in
# basemaps/barrios_veredas.geojson (see scripts/basemap_utils.py history /
# engram topic `architecture/data-pipeline`) — either a common Spanish word
# (e.g. "urbanización", "unión", "orquídeas") or a widely-known
# Colombian/Cali proper noun (e.g. "Simón Bolívar", "Belalcázar", "Nariño",
# "Siloé", "Cañaveralejo", "José María Córdoba"). Not a guess-everything
# dictionary — entries are only added when the correct accenting is not in
# doubt.
KNOWN_TOKEN_REPAIRS: dict[str, str] = {
    "urbanizacion": "Urbanización",
    "campina": "Campiña",
    "eliecer": "Eliécer",
    "gaitan": "Gaitán",
    "suarez": "Suárez",
    "monica": "Mónica",
    "berlin": "Berlín",
    "fatima": "Fátima",
    "aerea": "Aérea",
    "nicolas": "Nicolás",
    "juanambu": "Juanambú",
    "cana": "Caña",
    "penon": "Peñón",
    "americas": "Américas",
    "benjamin": "Benjamín",
    "trebol": "Trébol",
    "barbara": "Bárbara",
    "belalcazar": "Belalcázar",
    "simon": "Simón",
    "bolivar": "Bolívar",
    "mortinal": "Mortiñal",
    "fe": "Fé",
    "bretana": "Bretaña",
    "jose": "José",
    "marroquin": "Marroquín",
    "beltran": "Beltrán",
    "junin": "Junín",
    "balcazar": "Balcázar",
    "cristobal": "Cristóbal",
    "paraiso": "Paraíso",
    "belen": "Belén",
    "olimpico": "Olímpico",
    "jardin": "Jardín",
    "rincon": "Rincón",
    "maria": "María",
    "cordoba": "Córdoba",
    "colon": "Colón",
    "leon": "León",
    "aragon": "Aragón",
    "narino": "Nariño",
    "orquideas": "Orquídeas",
    "union": "Unión",
    "gomez": "Gómez",
    "canaveralejo": "Cañaveralejo",
    "republica": "República",
    "canaveral": "Cañaveral",
    "canaverales": "Cañaverales",
    "holguin": "Holguín",
    "garces": "Garcés",
    "cambulos": "Cámbulos",
    "alferez": "Alférez",
    "ramirez": "Ramírez",
    "napoles": "Nápoles",
    "melendez": "Meléndez",
    "jordan": "Jordán",
    "siloe": "Siloé",
}


def _strip_accents_lower(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _candidate_repair_chars(token: str, positions: list[int], candidate: str) -> list[str] | None:
    """If `candidate` matches `token` everywhere outside `positions` (accent-
    and case-insensitively) and supplies a real diacritic at every position,
    return the replacement characters in order; else None."""
    if len(candidate) != len(token):
        return None
    for i, (a, b) in enumerate(zip(token, candidate)):
        if i in positions:
            continue
        if _strip_accents_lower(a) != _strip_accents_lower(b):
            return None
    repl = []
    for i in positions:
        ch = candidate[i]
        if ch not in ACCENT_CHARS:
            return None
        repl.append(ch)
    return repl


def build_reference_tokens(free_text_values: list) -> list[str]:
    """Tokenize free-text survey values (e.g. the xlsx `Barrio/vereda:`
    column) into individual words, for use as repair confidence-source (a)."""
    tokens: list[str] = []
    for value in free_text_values:
        if not isinstance(value, str):
            continue
        for word in re.findall(r"\S+", value.strip()):
            cleaned = word.strip(".,;:()")
            if cleaned:
                tokens.append(cleaned)
    return tokens


def _apply_positions(token: str, positions: list[int], repl_chars: list[str]) -> str:
    chars = list(token)
    for i, ch in zip(positions, repl_chars):
        chars[i] = ch
    return "".join(chars)


def _repair_token(token: str, reference_tokens: list[str]) -> tuple[str, str]:
    """Repair a single whitespace-delimited token containing U+FFFD.

    Returns (repaired_token, source) where source is one of:
    'reference', 'dictionary', 'orthography', 'fallback_stripped'.
    """
    positions = [i for i, ch in enumerate(token) if ch == "\ufffd"]
    if len(positions) > 2:
        return token.replace("\ufffd", ""), "fallback_stripped"

    # (a) fuzzy/exact match against real survey free-text tokens.
    for ref in reference_tokens:
        repl = _candidate_repair_chars(token, positions, ref)
        if repl:
            return _apply_positions(token, positions, repl), "reference"

    # (b) built-in dictionary of well-known words/toponyms.
    norm_token = [
        ch if ch == "\ufffd" else _strip_accents_lower(ch) for ch in token
    ]
    for dict_key, canonical in KNOWN_TOKEN_REPAIRS.items():
        if len(dict_key) != len(token):
            continue
        if all(i in positions or dict_key[i] == norm_token[i] for i in range(len(token))):
            repl = _candidate_repair_chars(token, positions, canonical)
            if repl:
                return _apply_positions(token, positions, repl), "dictionary"

    # (c) narrow orthography heuristic: single U+FFFD strictly between two
    # vowels -> most likely an intervocalic 'ñ'.
    if len(positions) == 1:
        pos = positions[0]
        before = token[pos - 1] if pos > 0 else ""
        after = token[pos + 1] if pos + 1 < len(token) else ""
        vowels = "aeiouAEIOU"
        if before in vowels and after in vowels:
            replacement = "Ñ" if pos == 0 else "ñ"
            return _apply_positions(token, positions, [replacement]), "orthography"

    # (d) no confident source: drop the U+FFFD char(s) so nothing reaches the UI.
    return token.replace("\ufffd", ""), "fallback_stripped"


def repair_barrio_name(name: str, reference_tokens: list[str]) -> tuple[str, str]:
    """Repair a possibly-corrupted barrio/vereda display name.

    Returns (repaired_name, worst_source). worst_source is 'clean' when the
    name had no U+FFFD, else the lowest-confidence source used across its
    tokens: 'reference' > 'dictionary' > 'orthography' > 'fallback_stripped'.
    """
    if not name or "\ufffd" not in name:
        return name, "clean"

    rank = {"reference": 0, "dictionary": 1, "orthography": 2, "fallback_stripped": 3}
    worst = "clean"
    parts: list[str] = []
    last_end = 0
    for match in re.finditer(r"\S+", name):
        token = match.group()
        parts.append(name[last_end : match.start()])
        if "\ufffd" in token:
            repaired_token, source = _repair_token(token, reference_tokens)
            if rank.get(source, -1) > rank.get(worst, -1):
                worst = source
            parts.append(repaired_token)
        else:
            parts.append(token)
        last_end = match.end()
    parts.append(name[last_end:])
    return "".join(parts), worst


def repair_all_barrio_names(names: list, reference_tokens: list[str]) -> tuple[dict, Counter]:
    """Repair a batch of barrio names. Returns (original -> repaired map, stats
    Counter keyed by source: 'clean', 'reference', 'dictionary', 'orthography',
    'fallback_stripped')."""
    mapping: dict[str, str] = {}
    stats: Counter = Counter()
    for name in names:
        if name is None:
            continue
        repaired, source = repair_barrio_name(name, reference_tokens)
        mapping[name] = repaired
        stats[source] += 1
    return mapping, stats
