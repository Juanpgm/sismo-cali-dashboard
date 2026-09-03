"""Colombian cadastral address normalization to IGAC standard (Circular 300/01).

Self-contained port of `normalize_address` from the integracion_F1 pipeline
(integracion/normalization.py) so the dashboard transform (refresh_data.py) can
produce a `direccion_norm` column without depending on that separate repo.

    "Calle 80 No. 45-23, barrio el peñón"  ->  "CL 80 # 45-23, Barrio El Peñón"
"""
from __future__ import annotations

import re

# `\b` treats digits as word characters too, so it does NOT separate a letter
# from a directly-glued digit ("CARRERA77", "CALLE3D") -- that boundary is
# invisible to `\b`, so those tokens survived every `\bWORD\b` pattern below
# unchanged. `_wb()` rewrites each `\b`-delimited alternation into one bounded
# only by *letters*: still blocked from matching inside another word (e.g. the
# "CARRERA" inside "XCARRERA"), but now free to touch a digit directly, with
# or without a space -- matching how IGAC addresses are actually typed
# ("KR 96 # 48-53", but also "K 85 E # 28-06" and messy "CARRERA77 #1c-140").
_LETTERS = 'A-ZÁÉÍÓÚÑÜ'
_START = rf'(?<![{_LETTERS}])'
_END = rf'(?![{_LETTERS}])'


def _wb(pattern: str) -> str:
    """Strip the redundant per-alternative `\\b`s and wrap the whole
    alternation in one letter-only boundary pair (see module comment above)."""
    body = pattern.replace(r'\b', '')
    return f'{_START}(?:{body}){_END}'


# Road type -> IGAC standard code (order matters: compound before components).
_ROAD_TYPES = [
    (_wb(r'\bAVENIDA CALLE\b|\bAV CALLE\b|\bAV CL\b'),                          'AC'),
    (_wb(r'\bAVENIDA K?ARRERA\b|\bAV K?ARRERA\b|\bAV K?R\b|\bAV CRA\b'),        'AK'),
    (_wb(r'\bAUTOPISTA\b|\bAUTOP\b|\bAUT\b'),                                   'AU'),
    (_wb(r'\bAVENIDA\b|\bAVDA\b|\bAVD\b|\bAVE\b|\bAV\b'),                       'AV'),
    (_wb(r'\bCARRETERA\b|\bCARRET\b'),                                          'CT'),
    (_wb(r'\bCARRERA\b|\bKARRERA\b|\bCARR\b|\bCRA\b|\bKRA\b|\bKRR\b|\bKR\b|\bCR\b'), 'KR'),
    # Lone "K" as a carrera abbreviation (real IGAC form, e.g. "K 85 E # 28-06")
    # -- deliberately NOT folded into the alternation above. A bare "K" glued
    # to a number is structurally ambiguous in the raw data: it is also the
    # short form of "kilometro" ("K18", "K10.5", "K14.5, Sector ..."), which
    # outnumbers the real carrera usage roughly 5 to 1 in this dataset (10 of
    # 12 raw occurrences). Digit-adjacency alone (the previous rule) cannot
    # tell them apart -- both "K18" and "K 85" look identical to it -- so it
    # was silently corrupting kilometer references into fake carreras.
    #
    # What DOES tell them apart, on every occurrence seen in the data: a
    # cadastral number-sign ("#", the house/lot number marker) shows up
    # shortly after the road number in the real carrera cases ("K 67#3C-15",
    # "K 58 #3 - 136 4 G") and never does in the kilometer ones (they are
    # followed by free text -- a sector/place name -- or nothing at all).
    # Kilometers are also sometimes written with a decimal ("K10.5", "K14.5"),
    # which a carrera number never has -- an independent second signal, kept
    # as defense in depth even though every current kilometer example already
    # lacks a nearby "#" too.
    #
    # So: only convert when, close after the number, a "#" actually appears --
    # allowing a single cadastral sub-letter and some whitespace in between
    # (as in "KR 26 L # 72 W - 39"'s "L"), but nothing else (no comma, no
    # digit, no free text) between the number and it. Decimal numbers are
    # rejected outright regardless of what follows.
    (rf'(?<![{_LETTERS}0-9])K(?=\s*\d+(?!\.\d)[{_LETTERS}\s]{{0,3}}#)',          'KR'),
    (_wb(r'\bCALLE\b|\bCALL\b|\bCLLE\b|\bCLL\b|\bCL\b'),                        'CL'),
    (_wb(r'\bCIRCUNVALAR\b|\bCIRCUNV\b|\bCIRCV\b'),                            'CV'),
    (_wb(r'\bCIRCULAR\b|\bCIRC\b'),                                            'CQ'),
    (_wb(r'\bDIAGONAL\b|\bDIAG\b|\bDG\b'),                                      'DG'),
    (_wb(r'\bTRANSVERSAL\b|\bTRANSV\b|\bTRANS\b|\bTRAV\b|\bTV\b|\bTR\b'),      'TV'),
    (_wb(r'\bTRONCAL\b|\bTRONC\b'),                                            'TC'),
    (_wb(r'\bBULEVAR\b|\bBOULEVAR\b|\bBLVD\b|\bBL\b'),                        'BL'),
    (_wb(r'\bPASAJE\b|\bPSJE\b|\bPJE\b|\bPJ\b'),                               'PJ'),
    (_wb(r'\bPASEO\b|\bPSO\b'),                                                'PS'),
    (_wb(r'\bPEATONAL\b|\bPEAT\b'),                                            'PT'),
    (_wb(r'\bVARIANTE\b'),                                                      'VT'),
    (_wb(r'\bV[IÍ]A\b'),                                                        'VI'),
    (_wb(r'\bCUENTAS? CORRIDAS?\b'),                                           'CC'),
]
_CODES = 'AU|AV|AC|AK|KR|CL|CV|CQ|CT|DG|TV|TC|BL|PJ|PS|PT|VT|VI|CC'


def normalize_address(address) -> str:
    """Normalizes Colombian cadastral addresses to IGAC standard.

    Nomenclature (codes + numbers) stays uppercase; the complement (text after
    the first comma) is title-cased. Carrera code is KR (not CR).
    """
    if address is None:
        return ""
    value = str(address).strip()
    if not value or value in {"-", " "}:
        return value

    s = value.upper()

    # Strip internal dots from abbreviations  ->  K.R.A. -> KRA, C.L. -> CL
    s = re.sub(
        r'\b([A-Z])\.([A-Z])(?:\.([A-Z]))?\.?',
        lambda m: m.group(1) + m.group(2) + (m.group(3) or ""),
        s,
    )

    for pattern, code in _ROAD_TYPES:
        s = re.sub(pattern, code, s)

    # Strip stray trailing dots left after code substitution  ->  "CL." -> "CL"
    s = re.sub(rf'\b({_CODES})\.', r'\1', s)

    # Número sign (# separator). (?![A-ZÁÉÍÓÚ]) guards against eating longer words
    s = re.sub(
        r'\b(?:N[ÚU]MERO|NUMERO|N[ÚU]M|NUM|NRO|NO|NN)(?![A-ZÁÉÍÓÚ])\.?\s*',
        '# ',
        s,
        flags=re.UNICODE,
    )
    s = re.sub(r'N[°º]\.?\s*', '# ', s)

    # Whitespace normalization: exactly one space on each side of #
    s = re.sub(r'\s*#\s*', ' # ', s)
    s = re.sub(r'\s+', ' ', s).strip()

    # Casing: nomenclature stays uppercase; complement (after comma) -> title
    if ',' in s:
        nomenclatura, complement = s.split(',', 1)
        return nomenclatura.strip() + ', ' + complement.strip().title()
    return s


if __name__ == "__main__":
    assert normalize_address("Calle 46-45") == "CL 46-45"
    assert normalize_address("Carrera 36 b # 05-118") == "KR 36 B # 05-118"
    assert normalize_address("Calle 10 no. 42a 02") == "CL 10 # 42A 02"
    assert normalize_address("Cra 46 # 10-04") == "KR 46 # 10-04"
    assert normalize_address("Calle 80 No. 45-23, barrio el peñón") == "CL 80 # 45-23, Barrio El Peñón"
    assert normalize_address("") == ""
    assert normalize_address(None) == ""

    # Abbreviation glued directly to the number (the main bug): `\b` does not
    # separate a letter from a digit, so these used to survive untouched.
    # (Dash spacing is NOT tightened by normalize_address -- that is
    # normalize_direccion's job in refresh_data.py -- so "1c-140" stays as-is.)
    assert normalize_address("Carrera77 #1c-140") == "KR77 # 1C-140"
    assert normalize_address("CARRERA77") == "KR77"
    assert normalize_address("Calle3D # 45-23") == "CL3D # 45-23"
    assert normalize_address("KR15 # 8-20") == "KR15 # 8-20"

    # Already well-formed IGAC addresses must not change (road-type code
    # already isolated by spaces on both sides -- \b worked fine there).
    assert normalize_address("KR 96 # 48 - 53 BLQ 1 AP 502") == "KR 96 # 48 - 53 BLQ 1 AP 502"
    assert normalize_address("KR 26 L # 72 W - 39") == "KR 26 L # 72 W - 39"

    # Lone "K" as a carrera abbreviation -- only when glued to a digit.
    assert normalize_address("K 85 E # 28 - 06") == "KR 85 E # 28 - 06"
    assert normalize_address("K85 # 28-06") == "KR85 # 28-06"
    # False-positive guards: "K" not glued to a digit, or part of another
    # token (kilometer abbreviation, a plain building/tower letter), is left
    # alone -- normalize_address has no other rule for these either, so they
    # pass through unchanged, same as any other free text it can't typify.
    # ("VIA" -> "VI" here is the unrelated, pre-existing VI[ÍA] road-type rule.)
    assert normalize_address("KM 18 VIA CALI JAMUNDI") == "KM 18 VI CALI JAMUNDI"
    assert normalize_address("TORRE K - 5") == "TORRE K - 5"
    assert normalize_address("BLOQUE K") == "BLOQUE K"

    # Free text that is not a cadastral address -- must pass through untyped
    # (only upper-cased; title-casing only applies after a comma).
    assert normalize_address("Clinica colombia") == "CLINICA COLOMBIA"
    assert normalize_address("Finca El Refujio") == "FINCA EL REFUJIO"

    # Whitespace-only / dash-only.
    assert normalize_address("   ") == ""
    assert normalize_address("-") == "-"

    # Mixed case + accents + irregular spacing.
    assert normalize_address("cra 44a") == "KR 44A"
    assert normalize_address("Calle 3 c  # 66b-03") == "CL 3 C # 66B-03"
    assert normalize_address("CL 72 W # 28 D - 11") == "CL 72 W # 28 D - 11"
    assert normalize_address("Avenida 5 ta norte # 23 74") == "AV 5 TA NORTE # 23 74"

    print("address_norm selfcheck ok")
