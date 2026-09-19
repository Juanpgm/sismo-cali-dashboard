"""Tests for `scripts/publicar_referencia_inspectores.py` (tasks.md 3.3/3.4).

`construir_bundle` is PURE (plain list-of-dict fixtures, no pandas/file I/O
needed) — every output row must round-trip through
`app.services.inspectores_referencia.parse_bundle` unchanged (the CLI's own
self-check before a real publish). `publicar`'s upload step is exercised via
dependency injection (`upload=...`), same seam convention as
`inspectores_referencia.cargar_referencia(load_json=...)` — no real Blob
network call anywhere in this file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import publicar_referencia_inspectores as cli  # noqa: E402

from app.services import inspectores_referencia as ir  # noqa: E402


# --- construir_bundle: happy path, matches parse_bundle expectations ------


def test_construir_bundle_valid_rows_pass_parse_bundle():
    bundle = cli.construir_bundle(
        vercel_records=[
            {"identificacion": "1234567", "nombre_completo": "Juan Perez", "NP": "P3",
             "entidad": "DAGRD", "codigo": "041"},
        ],
        fase2_records=[
            {"identificacion": "1234567", "nombre_completo": "Juan Perez", "NP": "P3"},
        ],
        main_records=[
            {"cedula": 1234567, "nombre": "Juan Perez", "addlInfo.rango": "P2",
             "addlInfo.matriculaProfesional": "TP-1", "correo": "juan@example.com"},
        ],
        generado_en="2026-09-16",
        origen={"vercel": "v.csv", "fase2": "f.xlsx", "main": "m.csv"},
    )

    assert bundle["schema"] == 1
    assert bundle["generado_en"] == "2026-09-16"
    validado = ir.parse_bundle(bundle)
    assert validado is not None
    assert validado.activa is True
    assert len(validado.vercel) == 1
    assert validado.vercel[0].cedula_key == "1234567"
    assert validado.vercel[0].codigo == "041"
    assert len(validado.fase2) == 1
    assert len(validado.main) == 1
    assert validado.main[0].np == "P2"  # "rango" folded into "np" by parse_bundle


# --- edge case: missing cédula on every source is skipped, not raised -----


def test_construir_bundle_row_without_any_cedula_column_is_skipped():
    bundle = cli.construir_bundle(
        vercel_records=[{"nombre_completo": "Sin Cedula", "NP": "P1"}],
        fase2_records=[{"nombre_completo": "Sin Cedula", "NP": "P1"}],
        main_records=[{"nombre": "Sin Cedula", "correo": "x@y.co"}],
        generado_en="2026-09-16",
        origen={},
    )
    assert bundle["vercel"] == []
    assert bundle["fase2"] == []
    assert bundle["main"] == []


# --- W1: rows dropped for lacking a cédula are counted, never silent -------


def test_construir_bundle_counts_rows_dropped_for_lacking_a_cedula():
    bundle = cli.construir_bundle(
        vercel_records=[{"nombre_completo": "Sin Cedula", "NP": "P1"},
                        {"identificacion": "1234567", "nombre_completo": "Con"}],
        fase2_records=[{"nombre_completo": "Sin Cedula", "NP": "P1"},
                       {"identificacion": "abc", "nombre_completo": "Junk"},
                       {"identificacion": float("nan"), "nombre_completo": "Nan"}],
        main_records=[{"nombre": "Sin Cedula"}, {"cedula": "", "nombre": "Blanco"},
                      {"cedula": "N/A", "nombre": "Junk"}, {"cedula": 7654321, "nombre": "Ok"}],
        generado_en="2026-09-16", origen={},
    )
    assert bundle["descartados_sin_cedula"] == {"main": 3, "vercel": 1, "fase2": 3}
    assert len(bundle["main"]) == 1 and len(bundle["vercel"]) == 1 and bundle["fase2"] == []


def test_construir_bundle_reports_zero_dropped_when_every_row_has_a_cedula():
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[], main_records=[{"cedula": "1234567"}],
        generado_en="2026-09-16", origen={},
    )
    assert bundle["descartados_sin_cedula"] == {"main": 0, "vercel": 0, "fase2": 0}
    assert bundle["schema"] == 1  # additive optional key: the schema does NOT bump


def test_dropped_count_bundle_still_parses_and_carries_no_row_data():
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[],
        main_records=[{"nombre": "Persona Secreta", "correo": "secreto@example.com"}],
        generado_en="2026-09-16", origen={},
    )
    assert ir.parse_bundle(bundle) is not None
    assert "Persona Secreta" not in json.dumps(bundle["descartados_sin_cedula"])


def test_publicar_prints_the_dropped_count_without_pii(capsys):
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[],
        main_records=[{"nombre": "Persona Secreta", "correo": "secreto@example.com"},
                      {"cedula": "1234567", "nombre": "Otra Persona"}],
        generado_en="2026-09-16", origen={},
    )
    cli.publicar(bundle, dry_run=True)
    salida = capsys.readouterr().out
    assert "descartadas" in salida.lower() and "main=1" in salida
    assert "Persona Secreta" not in salida and "secreto@example.com" not in salida
    assert "Otra Persona" not in salida


def test_publicar_tolerates_a_bundle_without_the_dropped_count(capsys):
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[], main_records=[{"cedula": "1234567"}],
        generado_en="2026-09-16", origen={},
    )
    del bundle["descartados_sin_cedula"]
    cli.publicar(bundle, dry_run=True)  # must not raise
    assert "Bundle valido" in capsys.readouterr().out


# --- edge case: cedula fallback (identificacion blank, cedula present) ----


def test_construir_bundle_falls_back_to_cedula_column_when_identificacion_blank():
    bundle = cli.construir_bundle(
        vercel_records=[{"identificacion": None, "cedula": "1.234.567", "nombre_completo": "Ana"}],
        fase2_records=[],
        main_records=[],
        generado_en="2026-09-16",
        origen={},
    )
    assert bundle["vercel"][0]["cedula_key"] == "1234567"


# --- edge case: empty input files produce empty (not malformed) sections --


def test_construir_bundle_all_empty_sources_still_passes_parse_bundle():
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[], main_records=[],
        generado_en="2026-09-16", origen={},
    )
    validado = ir.parse_bundle(bundle)
    assert validado is not None
    assert validado.vercel == () and validado.fase2 == () and validado.main == ()
    assert validado.codigos_duplicados == ()


# --- codigos_duplicados: a codigo shared by 2+ DIFFERENT vercel identities -


def test_construir_bundle_flags_codigo_shared_by_two_identities():
    bundle = cli.construir_bundle(
        vercel_records=[
            {"identificacion": "111", "nombre_completo": "A", "codigo": "097"},
            {"identificacion": "222", "nombre_completo": "B", "codigo": "097"},
        ],
        fase2_records=[], main_records=[],
        generado_en="2026-09-16", origen={},
    )
    assert bundle["codigos_duplicados"] == ["097"]


def test_construir_bundle_same_identity_repeated_codigo_not_flagged_duplicate():
    # Same cedula_key twice (e.g. re-exported row) must not falsely trigger
    # the 2-DIFFERENT-identities rule.
    bundle = cli.construir_bundle(
        vercel_records=[
            {"identificacion": "111", "nombre_completo": "A", "codigo": "097"},
            {"identificacion": "111", "nombre_completo": "A", "codigo": "097"},
        ],
        fase2_records=[], main_records=[],
        generado_en="2026-09-16", origen={},
    )
    assert bundle["codigos_duplicados"] == []


# --- main rows: no_persona heuristic reused from inspectores_depuracion ---


def test_construir_bundle_main_row_flags_no_persona_via_correo_pattern():
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[],
        main_records=[{"cedula": "999", "nombre": "Cuenta Migrada", "correo": "x@sismo.cali.gov.co"}],
        generado_en="2026-09-16", origen={},
    )
    assert bundle["main"][0]["no_persona"] is True


def test_construir_bundle_main_row_real_person_not_flagged_no_persona():
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[],
        main_records=[{"cedula": "999", "nombre": "Ana Gomez", "correo": "ana@gmail.com"}],
        generado_en="2026-09-16", origen={},
    )
    assert bundle["main"][0]["no_persona"] is False


# --- edge case: pandas NaN values never leak as the string "nan" ----------


def test_limpiar_pandas_nan_becomes_empty_string():
    import math

    assert cli._limpiar(float("nan")) == ""
    assert cli._limpiar(math.nan) == ""
    assert cli._limpiar(None) == ""
    assert cli._limpiar("  Ana  ") == "Ana"


# --- publicar: self-check gate — an invalid bundle never reaches upload ---


def test_publicar_invalid_bundle_never_calls_upload():
    calls = []

    def _upload(*args, **kwargs):
        calls.append((args, kwargs))
        return "https://example/never-reached"

    with pytest.raises(SystemExit):
        cli.publicar({"schema": 99}, dry_run=False, upload=_upload)
    assert calls == []


# --- publicar: --dry-run never calls upload, even for a valid bundle ------


def test_publicar_dry_run_never_calls_upload():
    calls = []

    def _upload(*args, **kwargs):
        calls.append((args, kwargs))
        return "https://example/never-reached"

    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[], main_records=[],
        generado_en="2026-09-16", origen={},
    )
    result = cli.publicar(bundle, dry_run=True, upload=_upload)
    assert result is None
    assert calls == []


# --- publicar: a real publish uploads to the SAME pathname the reader uses,
# with access='private' (D8) --------------------------------------------


def test_publicar_uploads_to_bundle_blob_pathname_with_private_access():
    captured = {}

    def _fake_upload(local_path, pathname, max_age, content_type, access="public"):
        captured["pathname"] = pathname
        captured["access"] = access
        captured["content_type"] = content_type
        return "https://blob.example/bundle.json"

    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[], main_records=[],
        generado_en="2026-09-16", origen={},
    )
    url = cli.publicar(bundle, dry_run=False, upload=_fake_upload)

    assert url == "https://blob.example/bundle.json"
    assert captured["pathname"] == ir.BUNDLE_BLOB
    assert captured["access"] == "private"
    assert captured["content_type"] == "application/json"


# --- CLI end-to-end (argparse + real file reads), --dry-run only ----------


def test_main_dry_run_reads_real_fixture_files(tmp_path, capsys):
    vercel_csv = tmp_path / "vercel.csv"
    vercel_csv.write_text("identificacion,nombre_completo,NP,entidad,codigo\n1234567,Juan Perez,P3,DAGRD,041\n",
                           encoding="utf-8")
    fase2_csv = tmp_path / "fase2.csv"
    fase2_csv.write_text("identificacion,nombre_completo,NP\n1234567,Juan Perez,P3\n", encoding="utf-8")
    main_csv = tmp_path / "main.csv"
    main_csv.write_text("cedula,nombre,correo\n1234567,Juan Perez,juan@example.com\n", encoding="utf-8")

    cli.main([
        "--vercel", str(vercel_csv),
        "--fase2", str(fase2_csv),
        "--main", str(main_csv),
        "--generado-en", "2026-09-16",
        "--dry-run",
    ])

    out = capsys.readouterr().out
    assert "Bundle valido" in out
    assert "no se sube nada" in out


# --- regression: identifier columns must never be float-inferred ----------
#
# A column like `identificacion` holding ONE blank is inferred as float64 by
# pandas, turning 31837630 into 31837630.0; stripping non-digits then yields
# 318376300 (spurious trailing 0), and `codigo` "021" loses its leading zero.
# `_leer_tabla` must read everything as text, and the digit helpers must be
# robust to a float-ish "NNN.0" that still reaches them (e.g. an Excel cell
# stored as a float).


def _bundle_desde_vercel(records):
    return cli.construir_bundle(
        vercel_records=records, fase2_records=[], main_records=[],
        generado_en="2026-09-16", origen={},
    )


def _escribir_tabla(path, filas, columnas):
    import pandas as pd

    df = pd.DataFrame(filas, columns=columnas)
    if path.suffix == ".xlsx":
        df.to_excel(path, index=False)
    else:
        df.to_csv(path, index=False)


@pytest.mark.parametrize("sufijo", [".csv", ".xlsx"])
def test_leer_tabla_cedula_and_codigo_survive_a_blank_row_in_the_column(tmp_path, sufijo):
    ruta = tmp_path / f"vercel{sufijo}"
    _escribir_tabla(
        ruta,
        [
            ["31837630", "Ana Uno", "021"],
            [None, "Sin Cedula", "022"],
            ["7552410", "Beto Dos", "058"],
            ["1024591312", "Caro Tres", "101"],
        ],
        ["identificacion", "nombre_completo", "codigo"],
    )

    bundle = _bundle_desde_vercel(cli._leer_tabla(ruta))

    claves = [f["cedula_key"] for f in bundle["vercel"]]
    assert claves == ["31837630", "7552410", "1024591312"]  # blank row dropped, no digit gained/lost
    assert [f["codigo"] for f in bundle["vercel"]] == ["021", "058", "101"]  # leading zeros intact


@pytest.mark.parametrize("sufijo", [".csv", ".xlsx"])
def test_leer_tabla_main_cedula_with_blank_row_keeps_exact_digits(tmp_path, sufijo):
    ruta = tmp_path / f"main{sufijo}"
    _escribir_tabla(
        ruta,
        [["31837630", "Ana Uno", "a@x.co"], [None, "Sin Cedula", "b@x.co"], ["66826632", "Beto", "c@x.co"]],
        ["cedula", "nombre", "correo"],
    )

    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[], main_records=cli._leer_tabla(ruta),
        generado_en="2026-09-16", origen={},
    )

    assert [f["cedula_key"] for f in bundle["main"]] == ["31837630", "66826632"]


def test_leer_tabla_csv_preserves_leading_zero_cedula_and_long_ids(tmp_path):
    ruta = tmp_path / "vercel.csv"
    ruta.write_text(
        "identificacion,nombre_completo,codigo\n"
        "0012345,Con Ceros,007\n"
        "12345678901234567890,Id Larguisimo,008\n"
        ",Sin Cedula,009\n",
        encoding="utf-8",
    )

    bundle = _bundle_desde_vercel(cli._leer_tabla(ruta))

    assert [f["cedula_key"] for f in bundle["vercel"]] == ["0012345", "12345678901234567890"]
    assert [f["codigo"] for f in bundle["vercel"]] == ["007", "008"]


def test_leer_tabla_xlsx_long_text_id_keeps_every_digit(tmp_path):
    ruta = tmp_path / "vercel.xlsx"
    _escribir_tabla(
        ruta, [["12345678901234567890", "Id Larguisimo", "008"]],
        ["identificacion", "nombre_completo", "codigo"],
    )

    bundle = _bundle_desde_vercel(cli._leer_tabla(ruta))

    assert bundle["vercel"][0]["cedula_key"] == "12345678901234567890"


def test_leer_tabla_xlsx_mixed_int_and_text_cells_in_one_column(tmp_path):
    ruta = tmp_path / "vercel.xlsx"
    _escribir_tabla(
        ruta,
        [[31837630, "Ana Uno", 21], ["0012345", "Con Ceros", "021"], [None, "Sin", ""], ["7552410", "Beto", "058"]],
        ["identificacion", "nombre_completo", "codigo"],
    )

    bundle = _bundle_desde_vercel(cli._leer_tabla(ruta))

    assert [f["cedula_key"] for f in bundle["vercel"]] == ["31837630", "0012345", "7552410"]
    # an int cell has no leading zero to preserve; the text cell keeps its own
    assert [f["codigo"] for f in bundle["vercel"]] == ["21", "021", "058"]


def test_leer_tabla_xlsx_float_cell_never_gains_a_trailing_zero(tmp_path):
    # Real numeric float cells (what Excel stores for a number typed into a
    # column that also has a blank) must not become 318376300.
    ruta = tmp_path / "vercel.xlsx"
    _escribir_tabla(
        ruta,
        [[31837630.0, "Ana Uno", 21.0], [None, "Sin", None], [7552410.0, "Beto", 58.0]],
        ["identificacion", "nombre_completo", "codigo"],
    )

    bundle = _bundle_desde_vercel(cli._leer_tabla(ruta))

    assert [f["cedula_key"] for f in bundle["vercel"]] == ["31837630", "7552410"]
    assert [f["codigo"] for f in bundle["vercel"]] == ["21", "58"]


@pytest.mark.parametrize(
    "crudo, esperado",
    [
        ("31837630.0", "31837630"),
        ("31837630.00", "3183763000"),  # C3: only a lone ".0" is a float artifact
        ("12.000", "12000"),           # C3: thousands separator, dots simply removed
        ("166.000", "166000"),
        ("1234567.0.0", "123456700"),
        ("١٢٣٤٥٦٧", ""),                # non-ASCII digits are not digits (mirrors the JS \d)
        (31837630.0, "31837630"),
        ("0012345", "0012345"),
        ("1.234.567", "1234567"),  # thousands separators still stripped
        ("31.837.630", "31837630"),
        ("CC 31837630", "31837630"),
        ("  31837630  ", "31837630"),
    ],
)
def test_cedula_key_decimal_zero_suffix_is_dropped_not_appended(crudo, esperado):
    assert cli._cedula_key({"identificacion": crudo}, "identificacion") == esperado


@pytest.mark.parametrize("crudo", [None, "", "   ", "abc", "N/A", "-", float("nan")])
def test_cedula_key_blank_or_non_numeric_is_empty_never_zero(crudo):
    assert cli._cedula_key({"identificacion": crudo}, "identificacion") == ""


def test_fila_vercel_blank_or_junk_identificacion_is_dropped():
    for crudo in (None, "", "   ", "abc", float("nan")):
        assert cli._fila_vercel({"identificacion": crudo, "nombre_completo": "X", "codigo": "021"}) is None


@pytest.mark.parametrize(
    "crudo, esperado",
    [("021", "021"), ("21.0", "21"), ("021.0", "021"), (21.0, "21"), ("  041 ", "041"),
     ("A-12", "A-12"), (None, ""), (float("nan"), ""), ("3.5", "3.5"),
     ("21.00", "21.00"), ("12.000", "12.000")],  # C3: only a lone ".0" is dropped, `.00`/`.000` stay text
)
def test_fila_vercel_codigo_float_suffix_is_dropped_leading_zeros_kept(crudo, esperado):
    fila = cli._fila_vercel({"identificacion": "111", "nombre_completo": "A", "codigo": crudo})
    assert fila["codigo"] == esperado


def test_construir_bundle_duplicate_cedulas_stay_exact_and_are_kept_per_row():
    bundle = _bundle_desde_vercel([
        {"identificacion": "31837630.0", "nombre_completo": "A", "codigo": "021"},
        {"identificacion": "31837630", "nombre_completo": "A", "codigo": "021.0"},
    ])
    assert [f["cedula_key"] for f in bundle["vercel"]] == ["31837630", "31837630"]
    assert [f["codigo"] for f in bundle["vercel"]] == ["021", "021"]
    # same identity twice -> NOT a shared-by-two-identities duplicate
    assert bundle["codigos_duplicados"] == []


# --- Extension 2026-09-19 (PR 06): main rows emit the optional fields -------
#
# Real `main` export column names (header-only check, 2026-09-18): id, cedula,
# nombre, telefono, correo, codigoInspector, creadoEn, addlInfo.rango,
# addlInfo.matriculaProfesional, addlInfo.matricula.

_COLUMNAS_MAIN = [
    "id", "cedula", "nombre", "telefono", "correo", "codigoInspector", "creadoEn",
    "addlInfo.rango", "addlInfo.matriculaProfesional", "addlInfo.matricula",
]


def _bundle_desde_main_archivo(ruta):
    return cli.construir_bundle(
        vercel_records=[], fase2_records=[], main_records=cli._leer_tabla(ruta),
        generado_en="2026-09-16", origen={},
    )


@pytest.mark.parametrize("sufijo", [".csv", ".xlsx"])
def test_cli_emits_optional_fields(tmp_path, sufijo):
    ruta = tmp_path / f"main{sufijo}"
    _escribir_tabla(
        ruta,
        [["uuid-1", "1234567890", "Juan Perez", "30012345678", "juan@example.com", "021",
          "2026-01-05T10:00:00.000Z", "P2", "TP-99", ""]],
        _COLUMNAS_MAIN,
    )

    bundle = _bundle_desde_main_archivo(ruta)

    fila = bundle["main"][0]
    assert fila["nombre"] == "Juan Perez"
    assert fila["telefono"] == "30012345678"
    assert fila["codigo"] == "021"
    assert fila["creado_en"] == "2026-01-05T10:00:00.000Z"
    assert fila["id"] == "uuid-1"
    assert fila["correo"] == "juan@example.com"
    assert fila["tarjeta_profesional"] == "TP-99"
    assert fila["rango"] == "P2"
    # the emitted bundle round-trips through the tolerant parser
    entrada = ir.parse_bundle(bundle).main[0]
    assert (entrada.telefono, entrada.codigo, entrada.id) == ("30012345678", "021", "uuid-1")
    assert bundle["schema"] == 1


def test_cli_matricula_fallback_when_matricula_profesional_blank(tmp_path):
    ruta = tmp_path / "main.csv"
    _escribir_tabla(ruta, [["u", "111", "A", "", "", "", "", "", None, "MAT-7"]], _COLUMNAS_MAIN)

    assert _bundle_desde_main_archivo(ruta)["main"][0]["tarjeta_profesional"] == "MAT-7"


@pytest.mark.parametrize("sufijo", [".csv", ".xlsx"])
def test_cli_preserves_leading_zeros_and_long_numerics(tmp_path, sufijo):
    ruta = tmp_path / f"main{sufijo}"
    _escribir_tabla(
        ruta,
        [["u1", "1012345678", "Ana", "30012345678", "a@x.co", "041", "", "", "", ""],
         ["u2", "0012345", "Beto", "03001234567", "b@x.co", "007", "", "", "", ""]],
        _COLUMNAS_MAIN,
    )

    filas = _bundle_desde_main_archivo(ruta)["main"]

    assert [f["cedula_key"] for f in filas] == ["1012345678", "0012345"]
    assert [f["codigo"] for f in filas] == ["041", "007"]
    assert [f["telefono"] for f in filas] == ["30012345678", "03001234567"]


def test_cli_missing_optional_columns_yield_empty_not_nan(tmp_path):
    ruta = tmp_path / "main.csv"
    ruta.write_text("cedula,nombre\n1234567,Juan Perez\n", encoding="utf-8")

    fila = _bundle_desde_main_archivo(ruta)["main"][0]

    for campo in ("telefono", "codigo", "creado_en", "id", "correo", "tarjeta_profesional"):
        assert fila[campo] == ""
    assert fila["nombre"] == "Juan Perez"
    assert "NaN" not in json.dumps(fila) and "nan" not in fila.values()
    assert "None" not in json.dumps(fila)


def test_cli_blank_cells_yield_empty_strings_not_nan(tmp_path):
    ruta = tmp_path / "main.csv"
    _escribir_tabla(ruta, [["", "1234567", "Juan", None, None, None, None, None, None, None]], _COLUMNAS_MAIN)

    fila = _bundle_desde_main_archivo(ruta)["main"][0]

    for campo in ("telefono", "codigo", "creado_en", "id", "correo", "tarjeta_profesional", "rango"):
        assert fila[campo] == ""


def test_cli_duplicate_main_cedula_rows_both_kept_with_their_own_fields(tmp_path):
    ruta = tmp_path / "main.csv"
    _escribir_tabla(
        ruta,
        [["u1", "1234567", "Juan A", "300", "a@x.co", "021", "2026-01-01", "", "", ""],
         ["u2", "1234567", "Juan B", "301", "b@x.co", "022", "2026-02-01", "", "", ""]],
        _COLUMNAS_MAIN,
    )

    filas = _bundle_desde_main_archivo(ruta)["main"]

    assert [f["id"] for f in filas] == ["u1", "u2"]
    assert [f["codigo"] for f in filas] == ["021", "022"]
    assert [f["telefono"] for f in filas] == ["300", "301"]


def test_cli_main_row_without_cedula_is_still_dropped(tmp_path):
    ruta = tmp_path / "main.csv"
    _escribir_tabla(
        ruta,
        [["u1", "", "Sin Cedula", "300", "a@x.co", "021", "", "", "", ""],
         ["u2", "abc", "Junk", "301", "b@x.co", "022", "", "", "", ""],
         ["u3", "555", "Con Cedula", "", "", "", "", "", "", ""]],
        _COLUMNAS_MAIN,
    )

    assert [f["id"] for f in _bundle_desde_main_archivo(ruta)["main"]] == ["u3"]


def test_cli_main_records_with_wrong_typed_cells_never_raise():
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[],
        main_records=[{"cedula": 1234567, "telefono": 3001234567.0, "codigoInspector": 21,
                       "creadoEn": float("nan"), "id": None, "correo": "  A@X.co "}],
        generado_en="2026-09-16", origen={},
    )
    fila = bundle["main"][0]
    assert fila["telefono"] == "3001234567"
    assert fila["codigo"] == "21"
    assert fila["creado_en"] == ""
    assert fila["id"] == ""
    assert fila["correo"] == "A@X.co"


def test_cli_existing_main_fields_unchanged_regression():
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[],
        main_records=[{"cedula": "1234567", "nombre": "Juan Perez", "addlInfo.rango": "P2",
                       "addlInfo.matriculaProfesional": "TP-1", "correo": "juan@example.com"}],
        generado_en="2026-09-16", origen={},
    )
    fila = bundle["main"][0]
    assert fila["cedula_key"] == "1234567"
    assert fila["nombre_norm"] == "juan perez"
    assert fila["rango"] == "P2"
    assert fila["tarjeta_profesional"] == "TP-1"
    assert fila["correo"] == "juan@example.com"
    assert fila["no_persona"] is False


# --- review W2: publisher and parser share ONE identifier coercion ---------


@pytest.mark.parametrize(
    "crudo",
    ["21.0", "21.00", " 21.0 ", "021", "021.0", "0.0", "3.0e9", "3.5", "1.234.567", ".0",
     "12.000", "166.000", "1.0", "1.0.0",
     "21.0a", "", "   ", 21.0, 21, 3001234567.0, float("nan"), None],
)
def test_campo_id_and_texto_opcional_agree(crudo):
    assert cli._campo_id({"c": crudo}, "c") == ir._texto_opcional(crudo)


# --- review: _limpiar treats every pandas missing marker as blank ----------


@pytest.mark.parametrize("faltante", [None, float("nan"), pd.NA, pd.NaT, np.nan, np.float64("nan")])
def test_limpiar_every_missing_marker_becomes_empty(faltante):
    assert cli._limpiar(faltante) == ""


@pytest.mark.parametrize(
    "crudo, esperado",
    [
        ("  Ana  ", "Ana"),
        (0, "0"),
        (False, "False"),
        ([1, 2], "[1, 2]"),
        (np.array([1, 2]), "[1 2]"),
        ((), "()"),
    ],
)
def test_limpiar_non_scalar_or_falsy_values_never_raise(crudo, esperado):
    assert cli._limpiar(crudo) == esperado


# --- review W3: main rows carry `entidad` from addlInfo.entidad ------------


def _main_records(**extra):
    return cli.construir_bundle(
        vercel_records=[], fase2_records=[],
        main_records=[{"cedula": "1234567", "nombre": "Juan Perez", **extra}],
        generado_en="2026-09-16", origen={},
    )


def test_main_row_emits_entidad_from_addl_info_entidad_and_roundtrips():
    bundle = _main_records(**{"addlInfo.entidad": "  DAGRD  "})

    assert bundle["main"][0]["entidad"] == "DAGRD"
    assert ir.parse_bundle(bundle).main[0].entidad == "DAGRD"


@pytest.mark.parametrize("crudo", [None, float("nan"), pd.NA, "", "   "])
def test_main_row_blank_entidad_is_empty_string(crudo):
    assert _main_records(**{"addlInfo.entidad": crudo})["main"][0]["entidad"] == ""


def test_main_row_entidad_column_missing_entirely_is_empty_string():
    fila = _main_records()["main"][0]

    assert fila["entidad"] == ""
    assert ir.parse_bundle(_main_records()).main[0].entidad == ""


def test_main_row_entidad_from_csv_file_with_nan_and_whitespace(tmp_path):
    ruta = tmp_path / "main.csv"
    ruta.write_text(
        "cedula,nombre,addlInfo.entidad\n111,A,DAGRD\n222,B,\n333,C,   \n",
        encoding="utf-8",
    )

    filas = _bundle_desde_main_archivo(ruta)["main"]

    assert [f["entidad"] for f in filas] == ["DAGRD", "", ""]


# --- review: a main codigo colliding with a vercel codigo is NOT a duplicate


def test_main_codigo_colliding_with_vercel_codigo_not_in_codigos_duplicados():
    bundle = cli.construir_bundle(
        vercel_records=[{"identificacion": "111", "nombre_completo": "A", "codigo": "097"}],
        fase2_records=[],
        main_records=[
            {"cedula": "222", "nombre": "B", "codigoInspector": "097"},
            {"cedula": "333", "nombre": "C", "codigoInspector": "097"},
        ],
        generado_en="2026-09-16", origen={},
    )

    assert bundle["codigos_duplicados"] == []
    assert [f["codigo"] for f in bundle["main"]] == ["097", "097"]


# ── Review 2026-09-19 round 3 — S1: `creado_en` round-trips publish -> parse ─


def test_cli_creado_en_float_tail_is_dropped_at_publish_so_round_trip_is_idempotent():
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[],
        main_records=[{"cedula": "1234567", "creadoEn": "1735689600.0"}],
        generado_en="2026-09-16", origen={},
    )
    publicado = bundle["main"][0]["creado_en"]
    assert publicado == "1735689600"
    assert ir.parse_bundle(bundle).main[0].creado_en == publicado


@pytest.mark.parametrize("fecha", ["2026-01-05T10:00:00.000Z", "2026-01-05", "2026-01-05 10:00:00"])
def test_cli_creado_en_iso_dates_stay_byte_identical_through_round_trip(fecha):
    bundle = cli.construir_bundle(
        vercel_records=[], fase2_records=[],
        main_records=[{"cedula": "1234567", "creadoEn": fecha}],
        generado_en="2026-09-16", origen={},
    )
    assert bundle["main"][0]["creado_en"] == fecha
    assert ir.parse_bundle(bundle).main[0].creado_en == fecha
