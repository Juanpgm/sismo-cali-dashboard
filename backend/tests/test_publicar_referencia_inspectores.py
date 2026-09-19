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

import sys
from pathlib import Path

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
        ("31837630.00", "31837630"),
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
     ("A-12", "A-12"), (None, ""), (float("nan"), ""), ("3.5", "3.5")],
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
