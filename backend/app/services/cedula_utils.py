"""The ONE cédula-normalization rule shared by the depuración engine, the
reference-bundle parser and the bundle publisher (review 2026-09-19 C3,
design.md D-CEDDEC).

A "float artifact" is the exact text `<digits>.0` a float cell stringifies to
(pandas/Excel/JSON: `31837630` -> `31837630.0`); it ALWAYS carries exactly one
".0". Anything else with dots — "166.000", "1.234.567", "12.000", "12345.00" —
is a Colombian thousands-separated cédula (or plain noise) and its dots are
ordinary separators removed like any other non-digit.

The frontend's `cedulaKey` (web/js/seguimiento.js) mirrors the digit extraction
and the float-artifact rule, and embeds `COLA_FLOTANTE_PATRON` verbatim; a
backend test asserts that, so the two cannot drift silently. Digits are ASCII
only (`re.ASCII`), like JS `\\d`: Arabic-Indic or full-width digits are not
digits here, on either side. Known, accepted divergence (design D-CEDDEC):
exotic whitespace around a ".0" tail (BOM, NEL, control chars) — JS `\\s` and
Python `re.ASCII` + `str.strip()` disagree there; very low realism (a BOM-
prefixed cell means the source was read without utf-8-sig).

Pure and dependency-free on purpose: it is imported by three modules that must
not import each other (no cycles). Never raises on str input, never logs.
"""
from __future__ import annotations

import re

# Written once, in the exact form embedded in the JS source (`/.../`).
COLA_FLOTANTE_PATRON = r"^\s*(\d+)\.0\s*$"

_COLA_FLOTANTE = re.compile(COLA_FLOTANTE_PATRON, re.ASCII)
_NO_DIGITO = re.compile(r"\D", re.ASCII)


def quitar_cola_flotante(texto: str) -> str:
    """`"1234567.0"` -> `"1234567"`; any other text is returned untouched
    (`"166.000"`, `"12345.00"`, `"1.234.567"`, `"21.0a"`, `".0"`...). Surrounding
    whitespace is tolerated only when the artifact matches."""
    coincidencia = _COLA_FLOTANTE.match(texto)
    return coincidencia.group(1) if coincidencia else texto


def solo_digitos(texto: str) -> str:
    """Digits-only form: the float-artifact tail is dropped first, then every
    non-digit (dots, spaces, dashes, letters) is stripped. Leading zeros are
    kept (`"0012345"` stays `"0012345"`)."""
    return _NO_DIGITO.sub("", quitar_cola_flotante(texto))
