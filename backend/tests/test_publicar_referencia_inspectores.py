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
