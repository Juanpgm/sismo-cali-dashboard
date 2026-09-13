"""Shared es-CO date parser (W1, plan cozy-wobbling-dragonfly D8) — pure,
tz-explicit. Consolidates the two previously-duplicated regexes in
`planeacion_cruce.parse_fecha_creacion_es` (UTC) and
`reportes_ciudadanos.parse_fecha_es_co` (Bogota) into ONE parser that takes
`tz` explicitly, so no caller's timezone changes."""
from __future__ import annotations

from datetime import datetime, timezone

from app.jobs import planeacion_cruce as job
from app.services import fechas_es_co as fe

UTC = timezone.utc


# ── 12h parsing: am/pm rule (`int(h) % 12 + (12 if pm)`) ───────────────────


def test_12_05_am_is_00_05():
    dt = fe.parse_fecha_es_co("lunes, 3 de agosto de 2026, 12:05 a. m.", tz=UTC)
    assert (dt.hour, dt.minute) == (0, 5)


def test_12_05_pm_is_12_05():
    dt = fe.parse_fecha_es_co("lunes, 3 de agosto de 2026, 12:05 p. m.", tz=UTC)
    assert (dt.hour, dt.minute) == (12, 5)


def test_11_59_pm_is_23_59():
    dt = fe.parse_fecha_es_co("lunes, 3 de agosto de 2026, 11:59 p. m.", tz=UTC)
    assert (dt.hour, dt.minute) == (23, 59)


def test_without_weekday():
    dt = fe.parse_fecha_es_co("18 de agosto de 2026, 6:33 p. m.", tz=UTC)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 8, 18, 18, 33)


def test_weekday_and_meridiem_variants():
    variants = [
        "Sábado, 12 de septiembre de 2026, 10:32 p. m.",
        "sabado, 12 de septiembre de 2026, 10:32 P. M.",
        "sabado, 12 de septiembre de 2026, 10:32 p.m.",
        "sabado, 12 de septiembre de 2026, 10:32 pm",
    ]
    for texto in variants:
        dt = fe.parse_fecha_es_co(texto, tz=UTC)
        assert (dt.hour, dt.minute) == (22, 32), texto


def test_nbsp_before_meridiem_and_around_de():
    # U+00A0 (NBSP) right before "p. m." and around " de " — real API text.
    texto = "sábado, 12 de septiembre de 2026, 10:32 p. m."
    dt = fe.parse_fecha_es_co(texto, tz=UTC)
    assert (dt.month, dt.day, dt.hour, dt.minute) == (9, 12, 22, 32)


def test_narrow_nbsp_around_de():
    # U+202F (narrow no-break space), a variant seen in some locales.
    texto = "12 de septiembre de 2026, 10:32 p. m."
    dt = fe.parse_fecha_es_co(texto, tz=UTC)
    assert (dt.month, dt.day) == (9, 12)


def test_month_uppercase():
    dt = fe.parse_fecha_es_co("martes, 18 de AGOSTO de 2026, 06:33 p. m.", tz=UTC)
    assert dt.month == 8


def test_double_spaces_are_tolerated():
    dt = fe.parse_fecha_es_co("martes,  18  de  agosto  de  2026,  06:33  p. m.", tz=UTC)
    assert (dt.day, dt.hour) == (18, 18)


# ── M3 (adversarial review 2026-09-12): tolerant like the OLD unanchored
# `re.search`-based `planeacion_cruce` parser — locate the date expression
# ANYWHERE in the text (leading/trailing junk allowed), weekday optional
# with an optional comma (not mandatory), and leading zeros on the day
# tolerated. Switching the shared wrappers from `re.search` to a
# full-string `re.match` narrowed the accepted set; these must parse again
# while the mandatory-rejection set (unknown month, Feb 31, `13:00 p. m.`,
# `0:30 a. m.`, garbage) still returns None. ───────────────────────────────


def test_leading_junk_before_the_date_expression_is_tolerated():
    dt = fe.parse_fecha_es_co("Creado el 18 de agosto de 2026, 06:33 p. m. por Juan", tz=UTC)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 8, 18, 18, 33)


def test_weekday_without_a_comma_is_tolerated():
    dt = fe.parse_fecha_es_co("viernes 18 de agosto de 2026, 06:33 p. m.", tz=UTC)
    assert (dt.day, dt.hour, dt.minute) == (18, 18, 33)


def test_leading_zero_on_the_day_is_tolerated():
    dt = fe.parse_fecha_es_co("018 de agosto de 2026, 06:33 p. m.", tz=UTC)
    assert dt.day == 18


# ── invalid input -> None, never raises ────────────────────────────────────


def test_unknown_month_name_is_none():
    assert fe.parse_fecha_es_co("18 de brumario de 2026, 06:33 p. m.", tz=UTC) is None


def test_february_31_is_none():
    assert fe.parse_fecha_es_co("31 de febrero de 2026, 06:33 p. m.", tz=UTC) is None


def test_leap_year_2024_february_29_is_valid():
    dt = fe.parse_fecha_es_co("29 de febrero de 2024, 06:33 a. m.", tz=UTC)
    assert (dt.year, dt.month, dt.day) == (2024, 2, 29)


def test_non_leap_year_2026_february_29_is_none():
    assert fe.parse_fecha_es_co("29 de febrero de 2026, 06:33 a. m.", tz=UTC) is None


def test_hour_13_with_pm_marker_is_none():
    # 12h clock: hour must be 1-12 when an am/pm marker is present.
    assert fe.parse_fecha_es_co("18 de agosto de 2026, 13:00 p. m.", tz=UTC) is None


def test_hour_0_with_am_marker_is_none():
    assert fe.parse_fecha_es_co("18 de agosto de 2026, 0:30 a. m.", tz=UTC) is None


def test_blank_none_and_placeholder_inputs_are_none():
    for bad in ("", None, "Sin fecha", 123, {}, []):
        assert fe.parse_fecha_es_co(bad, tz=UTC) is None


def test_already_iso_formatted_input_is_none():
    assert fe.parse_fecha_es_co("2026-08-18T18:33:00+00:00", tz=UTC) is None


def test_never_raises_on_garbage():
    for bad in (object(), b"bytes", ["18 de agosto de 2026, 06:33 p. m."]):
        assert fe.parse_fecha_es_co(bad, tz=UTC) is None


# ── 24h format (HH:mm[:ss], no am/pm marker) ───────────────────────────────


def test_24h_format_without_meridiem():
    dt = fe.parse_fecha_es_co("18 de agosto de 2026, 22:15", tz=UTC)
    assert (dt.hour, dt.minute) == (22, 15)


def test_24h_format_with_seconds():
    dt = fe.parse_fecha_es_co("18 de agosto de 2026, 22:15:47", tz=UTC)
    assert (dt.hour, dt.minute, dt.second) == (22, 15, 47)


def test_24h_format_invalid_hour_is_none():
    assert fe.parse_fecha_es_co("18 de agosto de 2026, 25:00", tz=UTC) is None


# ── tz is explicit, never guessed ───────────────────────────────────────────


def test_tz_is_whatever_the_caller_passes():
    from datetime import timedelta

    bogota = timezone(timedelta(hours=-5))
    dt = fe.parse_fecha_es_co("martes, 18 de agosto de 2026, 06:33 p. m.", tz=bogota)
    assert dt.tzinfo == bogota
    assert dt.utcoffset() == timedelta(hours=-5)


# ── to_iso: always "+00:00"-style offset, never "Z" ────────────────────────


def test_to_iso_utc_uses_plus_00_00_not_z():
    dt = datetime(2026, 8, 26, 21, 14, tzinfo=timezone.utc)
    assert fe.to_iso(dt) == "2026-08-26T21:14:00+00:00"
    assert "Z" not in fe.to_iso(dt)


def test_to_iso_preserves_non_utc_offset():
    from datetime import timedelta

    bogota = timezone(timedelta(hours=-5))
    dt = datetime(2026, 8, 18, 18, 33, tzinfo=bogota)
    assert fe.to_iso(dt) == "2026-08-18T18:33:00-05:00"


# ── cross-check: the shared parser and planeacion_cruce's own wrapper
# (delegating, tz=UTC) must agree on the exact same instant ─────────────────


def test_cross_check_planeacion_cruce_matches_shared_parser():
    texto = "martes, 18 de agosto de 2026, 06:33 p. m."
    assert job.parse_fecha_creacion_es(texto) == fe.parse_fecha_es_co(texto, tz=UTC)


# N2: table of >=8 inputs (valid and rejected) exercised through BOTH the
# shared parser and `planeacion_cruce`'s own UTC wrapper, so the wrapper's
# "byte-for-byte" delegation claim is checked against more than one input.
_CROSS_CHECK_TABLE = [
    "martes, 18 de agosto de 2026, 06:33 p. m.",
    "18 de agosto de 2026, 6:33 p. m.",  # no weekday
    "viernes 18 de agosto de 2026, 06:33 p. m.",  # weekday, no comma
    "Creado el 18 de agosto de 2026, 06:33 p. m. por Juan",  # junk around the date
    "018 de agosto de 2026, 06:33 p. m.",  # leading zero on the day
    "18 de agosto de 2026, 22:15:47",  # 24h with seconds
    "18 de agosto de 2026, 13:00 p. m.",  # rejected: hour out of 1-12 w/ marker
    "31 de febrero de 2026, 06:33 p. m.",  # rejected: Feb 31
    "18 de brumario de 2026, 06:33 p. m.",  # rejected: unknown month
    "Sin fecha",  # rejected: garbage
]


def test_cross_check_planeacion_cruce_matches_shared_parser_table():
    assert len(_CROSS_CHECK_TABLE) >= 8
    for texto in _CROSS_CHECK_TABLE:
        expected = fe.parse_fecha_es_co(texto, tz=UTC)
        got = job.parse_fecha_creacion_es(texto)
        assert got == expected, texto
