"""Colombian cadastral address normalization to IGAC standard (Circular 300/01).

Self-contained port of `normalize_address` from the integracion_F1 pipeline
(integracion/normalization.py) so the dashboard transform (refresh_data.py) can
produce a `direccion_norm` column without depending on that separate repo.

    "Calle 80 No. 45-23, barrio el peñón"  ->  "CL 80 # 45-23, Barrio El Peñón"
"""
from __future__ import annotations

import re

# Road type -> IGAC standard code (order matters: compound before components).
_ROAD_TYPES = [
    (r'\bAVENIDA CALLE\b|\bAV CALLE\b|\bAV CL\b',                          'AC'),
    (r'\bAVENIDA K?ARRERA\b|\bAV K?ARRERA\b|\bAV K?R\b|\bAV CRA\b',        'AK'),
    (r'\bAUTOPISTA\b|\bAUTOP\b|\bAUT\b',                                   'AU'),
    (r'\bAVENIDA\b|\bAVDA\b|\bAVD\b|\bAVE\b|\bAV\b',                       'AV'),
    (r'\bCARRETERA\b|\bCARRET\b',                                          'CT'),
    (r'\bCARRERA\b|\bKARRERA\b|\bCARR\b|\bCRA\b|\bKRA\b|\bKRR\b|\bKR\b|\bCR\b', 'KR'),
    (r'\bCALLE\b|\bCALL\b|\bCLLE\b|\bCLL\b|\bCL\b',                        'CL'),
    (r'\bCIRCUNVALAR\b|\bCIRCUNV\b|\bCIRCV\b',                            'CV'),
    (r'\bCIRCULAR\b|\bCIRC\b',                                            'CQ'),
    (r'\bDIAGONAL\b|\bDIAG\b|\bDG\b',                                      'DG'),
    (r'\bTRANSVERSAL\b|\bTRANSV\b|\bTRANS\b|\bTRAV\b|\bTV\b|\bTR\b',      'TV'),
    (r'\bTRONCAL\b|\bTRONC\b',                                            'TC'),
    (r'\bBULEVAR\b|\bBOULEVAR\b|\bBLVD\b|\bBL\b',                        'BL'),
    (r'\bPASAJE\b|\bPSJE\b|\bPJE\b|\bPJ\b',                               'PJ'),
    (r'\bPASEO\b|\bPSO\b',                                                'PS'),
    (r'\bPEATONAL\b|\bPEAT\b',                                            'PT'),
    (r'\bVARIANTE\b',                                                      'VT'),
    (r'\bV[IÍ]A\b',                                                        'VI'),
    (r'\bCUENTAS? CORRIDAS?\b',                                           'CC'),
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
    print("address_norm selfcheck ok")
