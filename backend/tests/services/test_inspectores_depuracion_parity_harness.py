"""Tests for `scripts/parity_inspectores_depurado.py` (tasks.md Phase 12, PR 11).

The harness is a set of PURE functions over already-loaded inputs, so every test
uses small synthetic fixtures with FAKE identities (no real name, cedula, correo
or phone) and never touches a live service or the real reference xlsx. The
"notebook" xlsx of a test is derived from the engine's own output and then
altered on purpose in the way the notebook really diverges (a stale pre-remap
codigo, a firebase sticker counted as valid, a survey exemption it never had).
"""
from __future__ import annotations

import copy
import dataclasses
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import parity_inspectores_depurado as parity  # noqa: E402

from app.services import inspectores_depuracion as dep  # noqa: E402
from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle  # noqa: E402

HOY = date(2026, 9, 12)
nn = dep.normalizar_nombre

XLSX_COLS = (
    "id", "identificacion", "nombre_completo", "codigo", "entidad", "np", "np_fuente", "fase",
    "fase_np_faltante", "tarjeta_profesional", "num_telefono", "correo_contacto", "pasos", "activo",
    "estado_sugerido", "fuente_dato", "n_stickers", "tiene_sticker_valido", "codigo_inspector_original",
)


# ── fixtures: fake identities only ──────────────────────────────────────────

def _entrada(cedula, nombre, np="", entidad="", codigo="", **kw):
    return EntradaReferencia(cedula_key=cedula, nombre_norm=nn(nombre), np=np, entidad=entidad,
                             codigo=codigo, pasos=(), no_persona=False, **kw)


def _roster(cedula, nombre, codigo="", entidad="", tel="", tarjeta="", correo=""):
    return {"identificacion": cedula, "nombre_completo": nombre, "correo": correo, "codigo": codigo,
            "entidad": entidad, "tarjeta_profesional": tarjeta, "num_telefono": tel,
            "correo_contacto": correo, "creado_en": ""}


def _bundle(*, vercel=(), fase2=(), main=(), codigos_duplicados=()):
    return ReferenciaBundle(vercel=tuple(vercel), fase2=tuple(fase2), main=tuple(main), generado_en="2026-09-12",
                            activa=True, motivo="", codigos_duplicados=tuple(codigos_duplicados), huella="h")


def _sticker(cedula, origen, fecha):
    return {"origen": origen, "fecha_creacion": fecha,
            "inspector": {"identificacion": cedula, "codigo": "", "nombre_completo": ""}}


def _inputs(roster=None, main=(), vercel=(), fase2=(), stickers=(), survey=(), codigos_duplicados=()):
    return parity.Inputs(
        stickers=list(stickers), roster_by_cedula=roster or {}, nombres_survey=list(survey),
        referencia=_bundle(vercel=vercel, fase2=fase2, main=main, codigos_duplicados=codigos_duplicados), hoy=HOY,
    )


def _register_inputs():
    """One snapshot that exercises every register entry once."""
    roster = {
        "a": _roster("1000000001", "Zulema Quintero Ortiz", "010", "SGRED", "3001110001", "TP-1", "zulema@example.test"),
        "b": _roster("1000000002", "Bernardo Salcedo Mora"),
        "c": _roster("1000000003", "Clemencia Duarte Rey", "052"),  # Vercel says 041, nobody holds it
        "d": _roster("1000000004", "Damaso Fuentes Lara"),  # only firebase stickers
        "e": _roster("1000000005", "Eulalia Ponce Vega"),  # Firestore only: not in main
        "g": _roster("1000000007", "Homero Salinas Pardo"),  # full tie with the next one
        "h": _roster("1000000008", "Homero Salinas Pardo"),
        "i": _roster("1000000009.0", "Ines Tovar Gil"),  # float artifact in the raw cedula
        "j": _roster("1000000010", "Julieta Arango Paz", "077"),  # Vercel owner of 077 has no profile
    }
    return _inputs(
        roster=roster,
        vercel=(
            _entrada("1000000001", "Zulema Quintero Ortiz", "P3", "DAGRD", "010"),
            _entrada("1000000002", "Bernardo Salcedo Mora", "P1", "", "020"),
            _entrada("1000000003", "Clemencia Duarte Rey", "", "", "041"),
            _entrada("1000000099", "Otro Nombre Totalmente", "", "", "077"),
        ),
        fase2=(_entrada("1000000001", "Zulema Quintero Ortiz", "P4"),),
        main=(
            _entrada("1000000001", "Zulema Quintero Ortiz", "P2"),
            _entrada("1000000002", "Bernardo Salcedo Mora", "P1"),
            _entrada("1000000003", "Clemencia Duarte Rey", codigo="052"),  # main carries the code the notebook saw
            _entrada("1000000004", "Damaso Fuentes Lara", "P1"),
            _entrada("1000000006", "Fabio Naranjo Cruz"),
            _entrada("12345", "Externo Sospechoso Uno"),
            _entrada("12346", "Externo Sospechoso Dos"),
        ),
        stickers=[
            _sticker("1000000001", "sistema", "2026-09-10T10:00:00Z"),
            _sticker("1000000004", "firebase", "2026-09-07T10:00:00Z"),
        ],
        survey=["Externo Sospechoso Dos"],
    )


def _bool(value):
    return "True" if value else "False"


def _xlsx_row(row):
    out = {col: "" for col in XLSX_COLS}
    out.update(
        id="uuid-" + row["identidad_key"], identificacion=row["identificacion"], nombre_completo=row["nombre_completo"],
        codigo=row["codigo"], entidad=row["entidad"], np=row["np"], np_fuente=row["np_fuente"], fase=row["fase"],
        fase_np_faltante=_bool(row["fase_np_faltante"]), tarjeta_profesional=row["tarjeta_profesional"],
        num_telefono=row["num_telefono"], correo_contacto=row["correo_contacto"],
        estado_sugerido=row["estado_sugerido"], fuente_dato=row["fuente_dato"],
        tiene_sticker_valido=_bool(row["tiene_sticker_valido"]),
    )
    return out


def _xlsx_rows(depurado):
    """The xlsx an identical notebook would have produced."""
    rows = [_xlsx_row(r) for r in depurado.inspectores]
    grupo = depurado.grupo_externos
    if grupo:
        rows.append({**{c: "" for c in XLSX_COLS}, "id": "GRUPO-EXTERNOS", "nombre_completo": "Profesionales externos",
                     "np_fuente": "ninguno", "fase": "Fase I", "fase_np_faltante": "True", "activo": "False",
                     "estado_sugerido": "grupo_externos_agrupado",
                     "fuente_dato": f"grupo_agregado (main, {grupo['n_colapsados']} registros colapsados)"})
    return rows


def _row(rows, key):
    return next(r for r in rows if parity.normalize_key(r["identificacion"]) == key)


def _run(inputs, xlsx_rows, **kw):
    depurado = inputs.run()
    register = parity.build_register(inputs, depurado)
    return parity.compare(depurado, xlsx_rows, register, **kw)


def _hand(key, **over):
    """A hand-made engine row, for compare-level tests that need no engine run."""
    base = {"identidad_key": key, "identificacion": key, "nombre_completo": "Fake Person " + key,
            "cedulas_unificadas": [], "codigo": "", "entidad": "", "np": "", "np_fuente": "ninguno",
            "fase": "Fase I", "fase_np_faltante": True, "estado_sugerido": "revisar", "fuente_dato": "main",
            "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": "", "no_persona": False,
            "cedula_sospechosa": False, "tiene_sticker_valido": False, "dias_inactivo": None, "ultimo_sticker": None}
    return {**base, **over}


def _hand_depurado(rows, grupo=None):
    return dep.Depuracion(activa=True, motivo="", referencia_generada_en="2026-09-12",
                          inspectores=tuple(rows), grupo_externos=grupo, alias_nombres={}, revision_manual=())


def _hand_compare(backend_rows, xlsx_rows, **kw):
    depurado = _hand_depurado(backend_rows)
    return parity.compare(depurado, xlsx_rows, parity.build_register(_inputs(), depurado), **kw)


def _hand_xlsx(key, **over):
    return _xlsx_row(_hand(key, **over))


# ── 12.1 / 12.15: the register is explicit and asserted key by key ──────────

def _notebook_xlsx(inputs):
    """The notebook version of `_register_inputs()`: it counts the firebase sticker as valid (D7), keeps
    the stale pre-remap codigo (D-REMAP) and has no survey exemption, so it collapsed BOTH externos. Its universe is
    `main`, so it has no row for the Firestore-only person either."""
    depurado = inputs.run()
    rows = [r for r in _xlsx_rows(depurado) if parity.normalize_key(r["identificacion"]) not in ("12346", "1000000005")]
    _row(rows, "1000000004").update(tiene_sticker_valido="True", estado_sugerido="activo")
    _row(rows, "1000000003").update(codigo="052")
    next(r for r in rows if r["id"] == "GRUPO-EXTERNOS")["fuente_dato"] = "grupo_agregado (main, 2 registros colapsados)"
    externos = [{"identificacion": "12345"}, {"identificacion": "12346"}]
    return rows, externos


def test_parity_register_is_explicit():
    inputs = _register_inputs()
    rows, externos = _notebook_xlsx(inputs)
    report = _run(inputs, rows, externos_rows=externos, n_colapsados_notebook=2)
    register = report["register"]
    assert set(parity.REGISTER_NAMES) <= set(register)  # every documented divergence has an explicit entry
    assert register["D-EXENTOS"] == ["12346"]
    assert register["D7"] == ["1000000004"]
    assert register["D7-SIN-EXPLICAR"] == []
    assert register["D-REMAP"] == ["1000000003"]  # codigo_reemplazado: 052 -> 041
    assert register["D-REMAP-TODOS"] == ["1000000010"]  # remap_sin_duenio: cleared holder of 077
    assert register["D-TIEBREAK"] == ["1000000007"]
    assert register["D-TIEBREAK-ROSTER"] == []
    assert register["D-SURVFECHA"] == []
    assert register["D-CEDDEC"] == ["1000000009"]
    # the divergences explain the mismatches they cause, key by key, not through a tolerance
    codigo = report["columns"]["codigo"]["mismatches"]
    assert [(m["key"], m["backend"], m["xlsx"], m["explained"]) for m in codigo] == [
        ("1000000003", "041", "052", "D-REMAP")]
    estado = report["columns"]["estado_sugerido"]
    assert [(m["key"], m["explained"]) for m in estado["mismatches"]] == [("1000000004", "D7")]
    assert estado["n_effective"] == estado["n"] - 1  # registered divergences leave the estado denominator
    assert estado["passed"] is True


def test_parity_register_names_every_documented_divergence():
    for name in ("D7", "D-REMAP", "D-EXENTOS", "D-TIEBREAK", "D-MISMOSTITULARES", "D-CODIGOPERDIDO", "D-DUPLOCAL",
                 "D-CEDDEC", "D-SURVFECHA", "D-ENTIDADTRIM", "D-CODIGOTRIM", "D-N-COLAPSADOS", "D-REMAP-TODOS"):
        assert name in parity.REGISTER_NAMES


def test_parity_register_lists_tiebreak_survivors_by_key():
    def survivors(order):
        roster = {f"r{cedula}": _roster(cedula, "Homero Salinas Pardo") for cedula in order}
        inputs = _inputs(roster=roster, main=(_entrada("1000000099", "Otra Persona Distinta"),))
        return parity.build_register(inputs, inputs.run())["D-TIEBREAK"]

    # a three-way full tie is decided by the terminal identidad_key, whatever the roster order
    assert survivors(["1000000003", "1000000001", "1000000002"]) == ["1000000001"]
    assert survivors(["1000000001", "1000000002", "1000000003"]) == ["1000000001"]


def test_parity_register_tiebreak_empty_list_is_an_explicit_result():
    roster = {"a": _roster("1000000001", "Zulema Quintero Ortiz"), "b": _roster("1000000002", "Bernardo Salcedo Mora")}
    register = parity.build_register(_inputs(roster=roster), _inputs(roster=roster).run())
    assert register["D-TIEBREAK"] == []
    assert "D-TIEBREAK" in register


def test_parity_register_does_not_call_a_score_decided_pair_a_tiebreak():
    # the one with a sticker wins on score: no tie, so nothing to list
    roster = {"a": _roster("1000000001", "Homero Salinas Pardo"), "b": _roster("1000000002", "Homero Salinas Pardo")}
    inputs = _inputs(roster=roster, stickers=[_sticker("1000000002", "sistema", "2026-09-10T10:00:00Z")])
    assert parity.build_register(inputs, inputs.run())["D-TIEBREAK"] == []


def test_parity_register_survfecha_lists_survivor_when_parsing_dates_changes_the_winner():
    # lexicographic min picks B ("2025-01-01T23..."), parsed dates make A the older one
    inputs = _inputs(main=(
        _entrada("1000000001", "Homero Salinas Pardo", creado_en="2025-01-02T00:00:00Z"),
        _entrada("1000000002", "Homero Salinas Pardo", creado_en="2025-01-01T23:59:59-05:00"),
    ))
    register = parity.build_register(inputs, inputs.run())
    assert register["D-SURVFECHA"] == ["1000000001"]


def test_parity_register_remap_family_motivos_map_to_their_register_entry():
    inputs = _register_inputs()
    register = parity.build_register(inputs, inputs.run())
    assert register["D-REMAP"] == ["1000000003"]
    assert register["D-REMAP-TODOS"] == ["1000000010"]
    for name in ("D-MISMOSTITULARES", "D-CODIGOPERDIDO", "D-DUPLOCAL", "D-REMAPCONFLICTO"):
        assert register[name] == []  # explicit empty lists, not missing keys


def test_parity_register_reports_revision_motivos_it_does_not_register():
    inputs = _inputs(main=(_entrada("1000000001", "Zulema Quintero Ortiz"), _entrada("1000000001", "Otra Persona Distinta")))
    register = parity.build_register(inputs, inputs.run())
    assert register["revision_no_registrados"] == {"cedula_duplicada_main": 1}


# ── D7: firebase-only tiene_sticker_valido ──────────────────────────────────

def test_d7_true_in_notebook_false_in_backend_with_only_firebase_stickers_is_explained():
    inputs = _register_inputs()
    rows, externos = _notebook_xlsx(inputs)
    report = _run(inputs, rows, externos_rows=externos)
    valido = report["columns"]["tiene_sticker_valido"]
    assert [(m["key"], m["explained"]) for m in valido["mismatches"]] == [("1000000004", "D7")]


def test_d7_with_a_sistema_sticker_is_not_explained_and_is_listed_loudly():
    # same notebook claim, but the person HAS a valid sistema sticker in the inputs: D7 cannot explain it
    inputs = _inputs(
        roster={"a": _roster("1000000004", "Damaso Fuentes Lara")},
        main=(_entrada("1000000004", "Damaso Fuentes Lara", "P1"),),
        stickers=[_sticker("1000000004", "sistema", "2026-08-01T10:00:00Z")],  # before the 20-Aug cutoff: not valid
    )
    rows = _xlsx_rows(inputs.run())
    _row(rows, "1000000004")["tiene_sticker_valido"] = "True"
    report = _run(inputs, rows)
    assert report["register"]["D7"] == []
    assert report["register"]["D7-SIN-EXPLICAR"] == ["1000000004"]
    assert report["columns"]["tiene_sticker_valido"]["mismatches"][0]["explained"] is None


# ── 12.3: acceptance columns, mismatches enumerated ─────────────────────────

ACCEPTANCE = ("np", "np_fuente", "fase", "fase_np_faltante", "codigo", "fuente_dato", "entidad", "identificacion")


def test_parity_np_fase_codigo_entidad_identificacion_fuente_dato():
    inputs = _register_inputs()
    depurado = inputs.run()
    rows = _xlsx_rows(depurado)
    report = _run(inputs, rows)
    assert report["counts"]["matched"] == len(depurado.inspectores) == 10
    for column in ACCEPTANCE:
        result = report["columns"][column]
        assert (result["n"], result["matches"], result["mismatches"]) == (10, 10, []), column


def test_parity_mismatch_is_enumerated_with_key_and_both_values():
    inputs = _register_inputs()
    rows = _xlsx_rows(inputs.run())
    _row(rows, "1000000002")["np"] = "P9"
    _row(rows, "1000000001")["entidad"] = "CAMACOL"
    report = _run(inputs, rows)
    assert report["columns"]["np"]["mismatches"] == [
        {"key": "1000000002", "backend": "P1", "xlsx": "P9", "explained": None}]
    assert report["columns"]["np"]["matches"] == 9
    assert report["columns"]["np"]["passed"] is False  # 90% < 99%: never hidden inside a tolerance
    assert [m["key"] for m in report["columns"]["entidad"]["mismatches"]] == ["1000000001"]
    assert report["columns"]["fase"]["passed"] is True  # the untouched columns stay green


def test_parity_boundary_exactly_99_percent_passes_and_98_9_fails():
    assert parity.meets_threshold(990, 1000, 99.0) is True
    assert parity.meets_threshold(989, 1000, 99.0) is False
    assert parity.meets_threshold(99, 100, 99.0) is True
    assert parity.meets_threshold(98, 100, 99.0) is False
    assert parity.meets_threshold(0, 0, 99.0) is False  # never 100% on nothing

    keys = [f"{2000000000 + i}" for i in range(1000)]
    backend = [_hand(k, np="P1") for k in keys]
    xlsx = [_hand_xlsx(k, np="P1") for k in keys]
    assert _hand_compare(backend, xlsx)["columns"]["np"]["passed"] is True
    for row in xlsx[:10]:
        row["np"] = "P2"  # exactly 99.0%
    assert _hand_compare(backend, xlsx)["columns"]["np"]["passed"] is True
    xlsx[10]["np"] = "P2"  # 98.9%
    assert _hand_compare(backend, xlsx)["columns"]["np"]["passed"] is False


def test_parity_column_that_is_100_percent_mismatched_is_reported_loudly():
    keys = [f"{2000000000 + i}" for i in range(5)]
    report = _hand_compare([_hand(k, np="P1") for k in keys], [_hand_xlsx(k, np="P3") for k in keys])
    result = report["columns"]["np"]
    assert (result["matches"], result["fully_mismatched"], result["passed"]) == (0, True, False)
    assert len(result["mismatches"]) == 5
    text = parity.format_parity_report(report)
    assert "FULLY MISMATCHED" in text and "np" in text
    assert "np: 0/5" in text


# ── 12.4 / 12.5: contact columns and names ──────────────────────────────────

def test_parity_contact_columns_where_source_non_empty():
    inputs = _register_inputs()
    rows = _xlsx_rows(inputs.run())
    # only one person has contact data in the snapshot: the denominator is where the SOURCE is non-empty
    result = _run(inputs, rows)["columns"]
    for column in ("tarjeta_profesional", "num_telefono", "correo_contacto"):
        assert (result[column]["n"], result[column]["matches"]) == (1, 1), column
    # formatting differences the notebook normalizes away are not mismatches
    _row(rows, "1000000001").update(num_telefono="300 111 0001", correo_contacto="ZULEMA@EXAMPLE.TEST")
    result = _run(inputs, rows)["columns"]
    assert result["num_telefono"]["matches"] == 1 and result["correo_contacto"]["matches"] == 1
    # a different value is a mismatch and its content is never stored or printed
    _row(rows, "1000000001")["tarjeta_profesional"] = "TP-999"
    report = _run(inputs, rows)
    assert report["columns"]["tarjeta_profesional"]["mismatches"] == [
        {"key": "1000000001", "backend": "<set>", "xlsx": "<set>", "explained": None}]
    assert "TP-999" not in parity.format_parity_report(report)


def test_parity_contact_source_blank_in_notebook_is_excluded_even_when_backend_has_a_value():
    backend = [_hand("2000000001", correo_contacto="a@example.test"), _hand("2000000002", correo_contacto="b@example.test")]
    xlsx = [_hand_xlsx("2000000001", correo_contacto="a@example.test"), _hand_xlsx("2000000002", correo_contacto="")]
    result = _hand_compare(backend, xlsx)["columns"]["correo_contacto"]
    assert (result["n"], result["matches"], result["mismatches"]) == (1, 1, [])


def test_parity_nombre_completo_through_normalizar_nombre():
    inputs = _register_inputs()
    rows = _xlsx_rows(inputs.run())
    _row(rows, "1000000001")["nombre_completo"] = "  ZULEMÁ  quintero ortiz "  # case, accent, spaces: same person
    _row(rows, "1000000002")["nombre_completo"] = "Bernardo Salcedo Otro"
    result = _run(inputs, rows)["columns"]["nombre_completo"]
    assert result["matches"] == 9
    assert result["mismatches"] == [{"key": "1000000002", "backend": "<set>", "xlsx": "<set>", "explained": None}]
    assert result["threshold"] is None  # informational: the acceptance table gives no threshold


# ── 12.6 / 12.7: row count, extras, n_colapsados ────────────────────────────

def test_parity_row_count_373_plus_enumerated_firestore_extras():
    inputs = _register_inputs()
    rows, externos = _notebook_xlsx(inputs)
    report = _run(inputs, rows, externos_rows=externos)
    # 1000000005 exists only in Firestore (not in main): it is an enumerated extra, and so is the exempt survivor
    assert report["keys"]["only_in_backend"] == ["1000000005", "12346"]
    assert report["extras"]["firestore_only"] == ["1000000005"]
    assert report["extras"]["exento_survivor"] == ["12346"]
    assert report["extras"]["unexplained"] == []
    assert report["keys"]["only_in_xlsx"] == []


def test_parity_extra_not_explained_by_firestore_or_exemption_is_listed_as_unexplained():
    inputs = _register_inputs()
    rows = [r for r in _xlsx_rows(inputs.run()) if parity.normalize_key(r["identificacion"]) != "1000000006"]
    report = _run(inputs, rows)
    assert report["extras"]["unexplained"] == ["1000000006"]  # a main row the notebook does not have
    assert "1000000006" in report["keys"]["only_in_backend"]


def test_parity_n_colapsados_recomputed_and_reported_next_to_the_notebook_figure():
    inputs = _register_inputs()
    rows, externos = _notebook_xlsx(inputs)
    report = _run(inputs, rows, externos_rows=externos, n_colapsados_notebook=2)
    n = report["register"]["D-N-COLAPSADOS"]
    assert (n["backend"], n["notebook"]) == (1, 2)
    assert n["exentos_survivors"] == ["12346"]  # the D-EXENTOS survivor explains the whole difference
    assert n["dropped_exclusion"] == [] and n["started_exclusion"] == []
    assert n["expected"] == 1 and n["unexplained_delta"] == 0


def test_parity_n_colapsados_names_the_rows_whose_codigo_exclusion_changed():
    # notebook: X held a code (excluded from the collapse), the remap cleared it, so the engine collapses it (dropped);
    # Y was collapsed by the notebook, the engine gave it a code, so it survives (started)
    grupo = {"identidad_key": "GRUPO-EXTERNOS", "n_colapsados": 2, "detalle": (
        {"identificacion": "2000000001", "nombre_completo": "Fake X", "motivo": "cuenta_no_persona", "ultimo_sticker": None},
        {"identificacion": "2000000003", "nombre_completo": "Fake Z", "motivo": "cuenta_no_persona", "ultimo_sticker": None},
    )}
    depurado = _hand_depurado([_hand("2000000002", codigo="044")], grupo=grupo)
    xlsx = [_hand_xlsx("2000000001", codigo="031"), _hand_xlsx("2000000002", codigo="")]
    externos = [{"identificacion": "2000000002"}, {"identificacion": "2000000003"}]
    report = parity.compare(depurado, xlsx, parity.build_register(_inputs(), depurado),
                            externos_rows=externos, n_colapsados_notebook=2)
    n = report["register"]["D-N-COLAPSADOS"]
    assert n["dropped_exclusion"] == ["2000000001"]
    assert n["started_exclusion"] == ["2000000002"]
    assert (n["backend"], n["notebook"], n["expected"], n["unexplained_delta"]) == (2, 2, 2, 0)


def test_parity_n_colapsados_without_the_notebook_figure_says_so_instead_of_assuming_252():
    report = _hand_compare([_hand("2000000001")], [_hand_xlsx("2000000001")])
    n = report["register"]["D-N-COLAPSADOS"]
    assert n["notebook"] is None and n["backend"] == 0
    assert "n_colapsados: backend 0, notebook n/a" in parity.format_parity_report(report)


# ── keys: dots, zero padding, float tails, duplicates ───────────────────────

def test_keys_with_thousands_dots_and_float_tails_match_on_either_side():
    backend = [_hand("1000000001"), _hand("1000000002"), _hand("31837630")]
    xlsx = [_hand_xlsx("1.000.000.001"), _hand_xlsx("1000000002.0"), _hand_xlsx("31837630.0")]
    report = _hand_compare(backend, xlsx)
    assert report["counts"]["matched"] == 3
    assert report["keys"]["only_in_backend"] == [] and report["keys"]["only_in_xlsx"] == []


def test_backend_side_dotted_and_float_keys_are_normalized_too():
    backend = [_hand("1.000.000.001"), _hand("1000000002.0")]
    xlsx = [_hand_xlsx("1000000001"), _hand_xlsx("1000000002")]
    assert _hand_compare(backend, xlsx)["counts"]["matched"] == 2


def test_zero_padded_key_is_a_different_cedula_and_is_listed_not_silently_matched():
    report = _hand_compare([_hand("1000000003")], [_hand_xlsx("01000000003")])
    assert report["counts"]["matched"] == 0
    assert report["keys"]["only_in_backend"] == ["1000000003"]
    assert report["keys"]["only_in_xlsx"] == ["01000000003"]


def test_duplicate_keys_on_the_xlsx_side_are_reported_and_first_wins():
    backend = [_hand("2000000001", np="P1")]
    xlsx = [_hand_xlsx("2000000001", np="P1"), _hand_xlsx("2000000001.0", np="P9")]
    report = _hand_compare(backend, xlsx)
    assert report["keys"]["duplicate_xlsx"] == ["2000000001"]
    assert report["columns"]["np"]["matches"] == 1 and report["columns"]["np"]["n"] == 1  # the duplicate is not counted
    assert report["passed"] is False  # a duplicated reference key is a finding, not noise


def test_xlsx_rows_without_a_usable_key_are_counted_not_dropped_silently():
    report = _hand_compare([_hand("2000000001")], [_hand_xlsx("2000000001"), _hand_xlsx("")])
    assert report["keys"]["xlsx_sin_clave"] == 1


def test_only_in_backend_and_only_in_xlsx_are_enumerated_and_full_keys_stay_in_the_structure():
    report = _hand_compare([_hand("2000000001"), _hand("2000000002")], [_hand_xlsx("2000000002"), _hand_xlsx("2000000003")])
    assert report["keys"]["matched"] == ["2000000002"]
    assert report["keys"]["only_in_backend"] == ["2000000001"]
    assert report["keys"]["only_in_xlsx"] == ["2000000003"]
    assert report["only_in_xlsx_detail"]["missing"] == ["2000000003"]


def test_only_in_xlsx_key_absorbed_by_a_backend_survivor_is_reported_as_resolved_via_unificadas():
    backend = [_hand("2000000001", cedulas_unificadas=["2000000009"])]
    report = _hand_compare(backend, [_hand_xlsx("2000000001"), _hand_xlsx("2000000009")])
    assert report["only_in_xlsx_detail"]["resolved_via_unificadas"] == ["2000000009"]
    ident = report["columns"]["identificacion"]
    # the person is present but anchored on another cedula: an identificacion mismatch, enumerated
    assert (ident["n"], ident["matches"]) == (2, 1)
    assert ident["mismatches"] == [{"key": "2000000009", "backend": "2000000001", "xlsx": "2000000009",
                                    "explained": None}]


def test_only_in_xlsx_key_collapsed_in_the_backend_is_classified():
    grupo = {"identidad_key": "GRUPO-EXTERNOS", "n_colapsados": 1, "detalle": (
        {"identificacion": "2000000009", "nombre_completo": "Fake", "motivo": "cedula_sospechosa", "ultimo_sticker": None},)}
    depurado = _hand_depurado([_hand("2000000001")], grupo=grupo)
    report = parity.compare(depurado, [_hand_xlsx("2000000001"), _hand_xlsx("2000000009")],
                            parity.build_register(_inputs(), depurado))
    assert report["only_in_xlsx_detail"]["collapsed_in_backend"] == ["2000000009"]


# ── degenerate inputs ───────────────────────────────────────────────────────

def test_single_row_snapshot_compares_that_row():
    report = _hand_compare([_hand("2000000001", np="P1")], [_hand_xlsx("2000000001", np="P1")])
    assert report["counts"] == {"backend": 1, "xlsx": 1, "matched": 1}
    assert report["columns"]["np"]["matches"] == 1


def test_empty_backend_never_reports_a_pass():
    report = _hand_compare([], [_hand_xlsx("2000000001")])
    assert report["counts"]["matched"] == 0
    assert all(c["passed"] is False for c in report["columns"].values() if c["threshold"] is not None)
    assert report["passed"] is False
    assert any("no matched keys" in f for f in report["failures"])


def test_empty_xlsx_rows_never_report_a_pass():
    report = _hand_compare([_hand("2000000001")], [])
    assert report["passed"] is False and report["counts"]["matched"] == 0


# ── the register's trim divergences come from the xlsx cells ────────────────

def test_d_entidadtrim_lists_the_keys_where_only_surrounding_whitespace_differs():
    backend = [_hand("2000000001", entidad="DAGRD", np="P1"), _hand("2000000002", entidad="SGRED", np="P2")]
    xlsx = [_hand_xlsx("2000000001", entidad=" DAGRD "), _hand_xlsx("2000000002", entidad="OTRA", np="P2 ")]
    report = _hand_compare(backend, xlsx)
    assert report["register"]["D-ENTIDADTRIM"] == ["2000000001", "2000000002"]  # np of 02 and entidad of 01
    entidad = report["columns"]["entidad"]["mismatches"]
    assert [(m["key"], m["explained"]) for m in entidad] == [("2000000001", "D-ENTIDADTRIM"), ("2000000002", None)]


def test_d_codigotrim_and_zero_padding_of_codigo():
    backend = [_hand("2000000001", codigo="041"), _hand("2000000002", codigo="021"), _hand("2000000003", codigo="")]
    xlsx = [_hand_xlsx("2000000001", codigo="041 "), _hand_xlsx("2000000002", codigo="21"), _hand_xlsx("2000000003", codigo="")]
    report = _hand_compare(backend, xlsx)
    assert report["register"]["D-CODIGOTRIM"] == ["2000000001"]
    assert [m["key"] for m in report["columns"]["codigo"]["mismatches"]] == ["2000000001"]  # 021 vs 21 is padding only


def test_d_ceddec_lists_keys_whose_raw_cedula_had_a_float_tail_on_the_xlsx_side():
    report = _hand_compare([_hand("2000000001")], [_hand_xlsx("2000000001.0")])
    assert report["register"]["D-CEDDEC"] == ["2000000001"]


# ── the printed report never carries PII ────────────────────────────────────

def test_report_has_no_pii_and_keys_are_masked_to_the_last_three_digits():
    inputs = _register_inputs()
    rows, externos = _notebook_xlsx(inputs)
    _row(rows, "1000000001")["nombre_completo"] = "Otro Nombre Distinto"
    _row(rows, "1000000001")["correo_contacto"] = "otro@example.test"
    _row(rows, "1000000002")["np"] = "P9"
    report = _run(inputs, rows, externos_rows=externos)
    text = parity.format_parity_report(report)
    for secret in ("Zulema", "Quintero", "zulema@example.test", "otro@example.test", "Otro Nombre", "3001110001",
                   "TP-1", "Bernardo", "Fabio", "Externo"):
        assert secret not in text, secret
    assert not re.search(r"\d{5,}", text)  # no full cedula / phone anywhere
    assert "***002" in text  # the np mismatch key, masked
    assert parity.mask_key("1000000002") == "***002"
    assert parity.mask_key("12") == "***"
    assert parity.mask_key("") == "***"
    assert "1000000002" in str(report["keys"]["matched"])  # full keys live in the returned structure only


# ── 12.8: the reference xlsx ────────────────────────────────────────────────

def _write_xlsx(path, rows, sheet=parity.SHEET_PRINCIPAL, columns=XLSX_COLS, externos=None):
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(rows, columns=list(columns)).to_excel(writer, sheet_name=sheet, index=False)
        if externos is not None:
            pd.DataFrame(externos, columns=["id", "nombre_completo", "identificacion", "codigo", "n_stickers",
                                            "estado_sugerido"]).to_excel(writer, sheet_name=parity.SHEET_EXTERNOS, index=False)


def test_parity_harness_fails_loudly_on_missing_xlsx(tmp_path, capsys):
    missing = tmp_path / "nope.xlsx"
    with pytest.raises(parity.HarnessError, match="not found"):
        parity.load_reference_xlsx(missing)


def test_load_reference_xlsx_reads_rows_externos_and_the_notebook_count(tmp_path):
    path = tmp_path / "ref.xlsx"
    grupo = {**_hand_xlsx(""), "id": "GRUPO-EXTERNOS", "fuente_dato": "grupo_agregado (main, 2 registros colapsados)"}
    _write_xlsx(path, [_hand_xlsx("2000000001"), grupo], externos=[
        {"id": "u1", "identificacion": "2000000002"}, {"id": "u2", "identificacion": "2000000003"}])
    ref = parity.load_reference_xlsx(path)
    assert [parity.normalize_key(r["identificacion"]) for r in ref.rows] == ["2000000001", ""]
    assert [r["identificacion"] for r in ref.externos] == ["2000000002", "2000000003"]
    assert ref.n_colapsados == 2


def test_load_reference_xlsx_reads_the_count_from_the_group_row_when_there_is_no_externos_sheet(tmp_path):
    path = tmp_path / "ref.xlsx"
    grupo = {**_hand_xlsx(""), "id": "GRUPO-EXTERNOS", "fuente_dato": "grupo_agregado (main, 252 registros colapsados)"}
    _write_xlsx(path, [_hand_xlsx("2000000001"), grupo])
    ref = parity.load_reference_xlsx(path)
    assert (ref.externos, ref.n_colapsados) == ([], 252)


@pytest.mark.parametrize("case", ["empty", "wrong_sheet", "wrong_columns", "not_an_xlsx"])
def test_load_reference_xlsx_rejects_an_unusable_file_with_a_clear_message(tmp_path, case):
    path = tmp_path / "ref.xlsx"
    if case == "empty":
        _write_xlsx(path, [])
        expected = "no rows"
    elif case == "wrong_sheet":
        _write_xlsx(path, [_hand_xlsx("2000000001")], sheet="Otra Hoja")
        expected = "sheet"
    elif case == "wrong_columns":
        _write_xlsx(path, [{"a": "1"}], columns=("a",))
        expected = "missing columns"
    else:
        path.write_text("this is not a workbook", encoding="utf-8")
        expected = "cannot read"
    with pytest.raises(parity.HarnessError, match=expected):
        parity.load_reference_xlsx(path)


# ── 12.11-12.13: determinism ────────────────────────────────────────────────

def _stub_result(rows, revision=()):
    return dep.Depuracion(activa=True, motivo="", referencia_generada_en="", inspectores=tuple(rows),
                          grupo_externos=None, alias_nombres={}, revision_manual=tuple(revision))


def _first_roster_key(kw):
    return parity.normalize_key(next(iter(kw["roster_by_cedula"].values()))["identificacion"])


def _order_dependent_engine(**kw):
    """An engine that leaks the roster's iteration order into every row (the bug the check exists to catch)."""
    first = _first_roster_key(kw)
    return _stub_result([_hand(parity.normalize_key(r["identificacion"]), primero=first)
                         for r in kw["roster_by_cedula"].values()])


def test_parity_determinism_shuffled_real_snapshot_fixture():
    inputs = _register_inputs()
    result = parity.check_determinism(inputs)
    assert result["ok"] is True
    assert result["n_variants"] == 3  # the original plus two shuffled orders
    assert result["n_inspectores"] == 10
    assert result["diffs"] == {}
    # the shuffles are real: without this the check could pass by comparing three identical orders
    for variant in (parity.shuffled_inputs(inputs, 1), parity.shuffled_inputs(inputs, 2)):
        assert list(variant.roster_by_cedula) != list(inputs.roster_by_cedula)
        assert sorted(variant.roster_by_cedula) == sorted(inputs.roster_by_cedula)
        assert variant.referencia is inputs.referencia  # its row order is part of the input (D29)


def test_parity_determinism_does_not_mutate_the_snapshot():
    inputs = _register_inputs()
    before = copy.deepcopy((inputs.stickers, inputs.roster_by_cedula, inputs.nombres_survey))
    parity.check_determinism(inputs)
    assert (inputs.stickers, inputs.roster_by_cedula, inputs.nombres_survey) == before
    assert list(inputs.roster_by_cedula) == list(before[1])


def test_parity_determinism_check_fails_loudly_on_order_dependent_engine():
    inputs = _register_inputs()
    result = parity.check_determinism(inputs, depurar_fn=_order_dependent_engine)
    assert result["ok"] is False
    assert result["diffs"]["primero"]  # the differing identidad_key list, per field
    assert set(result["diffs"]["primero"]) <= {parity.normalize_key(r["identificacion"]) for r in inputs.roster_by_cedula.values()}
    text = parity.format_determinism_report(result)
    assert "DETERMINISM FAIL" in text and "primero" in text
    assert "***" in text and not re.search(r"\d{5,}", text)  # keys masked


def test_parity_determinism_check_reports_rows_that_appear_or_vanish_with_the_order():
    def engine(**kw):
        rows = [_hand(parity.normalize_key(r["identificacion"])) for r in kw["roster_by_cedula"].values()]
        return _stub_result(rows[1:] if next(iter(kw["roster_by_cedula"])) != "a" else rows)

    result = parity.check_determinism(_register_inputs(), depurar_fn=engine)
    assert result["ok"] is False and result["diffs"]["<row>"]


def test_parity_determinism_check_covers_the_revision_and_alias_structures():
    def revision_engine(**kw):
        return _stub_result([_hand("2000000001")], revision=[{"motivo": "x", "identidad_key": _first_roster_key(kw)}])

    result = parity.check_determinism(_register_inputs(), depurar_fn=revision_engine)
    assert result["ok"] is False and result["diffs"]["revision_manual"]

    def alias_engine(**kw):
        return dep.Depuracion(activa=True, motivo="", referencia_generada_en="", inspectores=(_hand("2000000001"),),
                              grupo_externos=None, alias_nombres={"un nombre": _first_roster_key(kw)}, revision_manual=())

    assert parity.check_determinism(_register_inputs(), depurar_fn=alias_engine)["diffs"]["alias_nombres"]


def test_parity_determinism_check_handles_empty_snapshot():
    empty = _inputs(main=(_entrada("1000000001", "Zulema Quintero Ortiz"),))  # nothing to shuffle: no roster, no stickers
    with pytest.raises(parity.HarnessError, match="empty snapshot"):
        parity.check_determinism(empty)


def test_parity_determinism_single_profile_snapshot_passes_with_one_row():
    inputs = _inputs(roster={"a": _roster("1000000001", "Zulema Quintero Ortiz")})
    result = parity.check_determinism(inputs)
    assert (result["ok"], result["n_inspectores"]) == (True, 1)


def test_parity_determinism_report_says_pass_with_the_counts():
    text = parity.format_determinism_report(parity.check_determinism(_register_inputs()))
    assert "DETERMINISM PASS" in text and "3 orders" in text and "10 profiles" in text


# ── 12.14: timing ───────────────────────────────────────────────────────────

class _SteppedClock:
    """Each engine call takes the next duration of the script: no real time, no jitter."""

    def __init__(self, durations):
        self.now, self._durations = 100.0, iter(durations)

    def __call__(self):
        return self.now

    def engine(self, **kw):
        self.now += next(self._durations)
        return _stub_result([_hand("2000000001")])


def test_parity_timing_real_snapshot_under_budget():
    result = parity.check_timing(_register_inputs())  # real engine, real clock, generous 0.5 s ceiling
    assert result["ok"] is True
    assert result["runs"] == 3 and 0 <= result["best_s"] < parity.TIMING_CEILING_S
    assert (result["n_roster"], result["n_main"], result["n_stickers"], result["n_inspectores"]) == (9, 7, 2, 10)
    text = parity.format_timing_report(result)
    assert re.search(r"best of 3: \d+\.\d{3} s", text)
    assert "roster 9" in text and "stickers 2" in text and "TIMING PASS" in text


def test_parity_timing_with_a_stubbed_slow_engine_fails():
    clock = _SteppedClock([0.9, 0.8, 0.7])
    result = parity.check_timing(_register_inputs(), depurar_fn=clock.engine, clock=clock)
    assert result["ok"] is False and result["best_s"] == pytest.approx(0.7)
    assert "TIMING FAIL" in parity.format_timing_report(result)


def test_parity_timing_uses_the_best_of_n_so_one_slow_run_does_not_fail_it():
    clock = _SteppedClock([2.0, 0.1, 3.0])
    result = parity.check_timing(_register_inputs(), depurar_fn=clock.engine, clock=clock)
    assert result["ok"] is True and result["best_s"] == pytest.approx(0.1)


def test_parity_timing_boundary_is_strictly_under_the_ceiling():
    at_ceiling = _SteppedClock([0.5, 0.5, 0.5])
    assert parity.check_timing(_register_inputs(), depurar_fn=at_ceiling.engine, clock=at_ceiling)["ok"] is False
    under = _SteppedClock([0.49, 0.5, 0.5])
    assert parity.check_timing(_register_inputs(), depurar_fn=under.engine, clock=under)["ok"] is True


def test_parity_timing_empty_or_missing_snapshot_is_an_error_never_a_vacuous_pass():
    with pytest.raises(parity.HarnessError, match="empty snapshot"):
        parity.check_timing(_inputs(main=(_entrada("1000000001", "Zulema Quintero Ortiz"),)))
    with pytest.raises(parity.HarnessError, match="empty snapshot"):
        parity.check_timing(None)


def test_parity_timing_needs_at_least_three_runs_and_a_positive_ceiling():
    with pytest.raises(parity.HarnessError, match="at least 3"):
        parity.check_timing(_register_inputs(), runs=2)
    with pytest.raises(parity.HarnessError, match="ceiling"):
        parity.check_timing(_register_inputs(), ceiling_s=0)


def test_parity_timing_single_row_snapshot_is_measured():
    result = parity.check_timing(_inputs(roster={"a": _roster("1000000001", "Zulema Quintero Ortiz")}))
    assert (result["ok"], result["n_inspectores"]) == (True, 1)


# ── 12.16-12.17: the read ledger ────────────────────────────────────────────

SECRET = "SECRETMARK"  # planted in every fake document: it must never reach a report


class _Snap:
    def __init__(self, doc_id, data):
        self.id, self.exists, self._data = doc_id, True, data

    def to_dict(self):
        return dict(self._data)


def _forbidden(*_a, **_k):
    raise AssertionError("a write or an unexpected call reached the Firestore client")


class _FakeRef:
    """A document reference WITH a write surface, to prove the wrapper never hands it out."""

    def __init__(self, doc_id):
        self.doc_id = doc_id

    set = update = delete = create = _forbidden


class _FakeCollection:
    def __init__(self, snaps, fail_after=None):
        self._snaps, self._fail_after = snaps, fail_after

    def stream(self):
        for index, snap in enumerate(self._snaps):
            if self._fail_after is not None and index == self._fail_after:
                raise RuntimeError(f"stream broke {SECRET}")
            yield snap

    def get(self):  # what the real client does, not what the harness relies on
        return list(self.stream())

    def document(self, doc_id):
        return _FakeRef(doc_id)

    add = set = update = delete = where = on_snapshot = _forbidden


class _FakeDb:
    def __init__(self, collections, fail=None):
        self._collections, self._fail = collections, fail or {}

    def collection(self, name):
        return _FakeCollection(self._collections.get(name, []), self._fail.get(name))

    def get_all(self, refs):
        by_id = {s.id: s for s in self._collections.get("inspectores", [])}
        for ref in refs:
            if ref.doc_id in by_id:
                yield by_id[ref.doc_id]

    batch = transaction = bulk_writer = collection_group = recursive_delete = commit = write = create = set = _forbidden


def _docs(name, n):
    return [_Snap(f"{name}-{i}", {"nombre": f"Nombre {SECRET}", "identificacion": "1099999999"}) for i in range(n)]


def test_ledger_counts_documents_per_collection(monkeypatch):
    ledger = parity.ReadLedger()
    db = parity.CountingFirestore(_FakeDb({"evaluaciones": _docs("e", 3), "inspectores": _docs("i", 2),
                                           "survey_cali": _docs("s", 4)}), ledger)
    assert len(list(db.collection("evaluaciones").stream())) == 3
    assert len(db.collection("inspectores").get()) == 2  # `get()` is counted through the same stream
    assert len(db.collection("survey_cali").get()) == 4
    ledger.add_api_rows(5)
    fake_get = lambda *_a, **_k: {"fake": True}  # noqa: E731 - no real network, whatever the environment
    monkeypatch.setattr(parity.blob_lkg, "load_json_private", fake_get)
    with parity.guard_blob(ledger, put="refuse"):
        assert parity.blob_lkg.load_json_private("referencia/x", dict) == {"fake": True}
    assert ledger.report() == {"E": 3, "I": 2, "S": 4, "np_lookup": 0, "other_docs": 0, "api_rows": 5,
                               "blob_get": 1, "blob_put": 0}


def test_ledger_counts_the_np_lookup_reads_of_the_batch_get_all():
    # `list_evaluaciones` reads `inspectores/{uid}` for every distinct uid through `get_all`: real reads, counted apart
    ledger = parity.ReadLedger()
    db = parity.CountingFirestore(_FakeDb({"inspectores": _docs("i", 3)}), ledger)
    refs = [db.collection("inspectores").document(f"i-{n}") for n in range(3)] + [db.collection("inspectores").document("gone")]
    assert [s.id for s in db.get_all(refs)] == ["i-0", "i-1", "i-2"]
    assert ledger.report()["np_lookup"] == 3 and ledger.report()["I"] == 0


def test_ledger_other_collections_are_counted_apart_never_dropped():
    ledger = parity.ReadLedger()
    db = parity.CountingFirestore(_FakeDb({"planeacion": _docs("p", 2)}), ledger)
    list(db.collection("planeacion").stream())
    assert ledger.report()["other_docs"] == 2 and ledger.report()["E"] == 0


WRITE_NAMES = ("set", "add", "update", "delete", "create", "batch", "transaction", "bulk_writer", "commit", "write",
               "collection_group", "on_snapshot", "where", "recursive_delete")


def test_ledger_wrapper_exposes_no_write_method():
    db = parity.CountingFirestore(_FakeDb({"inspectores": _docs("i", 1)}), parity.ReadLedger())
    collection = db.collection("inspectores")
    reference = collection.document("i-0")
    for target in (db, collection, reference):
        for name in WRITE_NAMES:
            assert not hasattr(target, name), (type(target).__name__, name)
        with pytest.raises(AttributeError):
            target.anything_else  # noqa: B018 - no catch-all proxying either
    assert [n for n in dir(db) if not n.startswith("_")] == ["collection", "get_all"]
    assert [n for n in dir(collection) if not n.startswith("_")] == ["document", "get", "stream"]


def test_ledger_report_and_text_hold_counts_only_and_no_document_content():
    ledger = parity.ReadLedger()
    db = parity.CountingFirestore(_FakeDb({"evaluaciones": _docs("e", 3)}), ledger)
    docs = db.collection("evaluaciones").get()
    assert docs[0].to_dict()["nombre"].endswith(SECRET)  # the caller still gets the documents it needs...
    report = ledger.report()
    assert all(isinstance(v, int) for v in report.values())  # ...the ledger keeps only integers
    text = parity.format_ledger_report(report)
    assert SECRET not in text and "1099999999" not in text and "Nombre" not in text
    assert "E 3" in text


def test_ledger_zero_document_collection_reports_zero():
    ledger = parity.ReadLedger()
    db = parity.CountingFirestore(_FakeDb({"evaluaciones": []}), ledger)
    assert db.collection("evaluaciones").get() == []
    assert ledger.report()["E"] == 0
    assert "E 0" in parity.format_ledger_report(ledger.report())


def test_ledger_stream_that_raises_records_the_partial_count():
    ledger = parity.ReadLedger()
    db = parity.CountingFirestore(_FakeDb({"evaluaciones": _docs("e", 5), "survey_cali": _docs("s", 2)},
                                          fail={"evaluaciones": 3}), ledger)
    with pytest.raises(RuntimeError):
        db.collection("evaluaciones").get()
    assert ledger.report()["E"] == 3  # what was streamed before the failure
    assert len(db.collection("survey_cali").get()) == 2 and ledger.report()["S"] == 2  # the ledger keeps counting


def test_guard_blob_refuses_a_put_counts_the_attempt_and_restores_the_module(monkeypatch):
    original = parity.blob_lkg.save_json
    ledger = parity.ReadLedger()
    with parity.guard_blob(ledger, put="refuse"):
        with pytest.raises(parity.ReadOnlyViolation):
            parity.blob_lkg.save_json("data/x.json", {"secret": SECRET})
    assert ledger.report()["blob_put"] == 1
    assert parity.blob_lkg.save_json is original


def test_guard_blob_count_mode_counts_and_performs_nothing():
    ledger = parity.ReadLedger()
    with parity.guard_blob(ledger, put="count"):
        assert parity.blob_lkg.save_json("data/x.json", {"a": 1}) is True
    assert ledger.report()["blob_put"] == 1


_API_ROWS = [
    {"id": "ev-1", "direccion": "Calle 1", "latitud": "3.45", "longitud": "-76.53", "numero": "76001-1-0040001",
     "personaAfectada": "Alguien", "origen": "sistema", "color": "rojo", "colorEtiqueta": "No habitable"},
    {"id": "ev-2", "direccion": "Calle 2", "latitud": "3.46", "longitud": "-76.52", "numero": "76001001-123-0001",
     "personaAfectada": "Otra", "origen": "firebase", "color": "verde", "colorEtiqueta": "Habitable"},
]


def _live_fake_db():
    return _FakeDb({
        "inspectores": [_Snap("u1", {"identificacion": "1000000001", "nombre_completo": "Zulema Quintero Ortiz",
                                     "NP": "P3", "codigo": "010"}),
                        _Snap("u2", {"identificacion": "1000000002", "nombre_completo": "Bernardo Salcedo Mora"})],
        "evaluaciones": [_Snap("e1", {"inspector": {"uid": "u1"}}), _Snap("e2", {"inspector": {"uid": "u1"}})],
        "survey_cali": [_Snap(f"s{i}", {"nombre_evaluador": n}) for i, n in enumerate(["Uno A", "Dos B", "Tres C"])],
    })


def test_assemble_inputs_mirrors_the_router_and_counts_each_scan_once():
    ledger = parity.ReadLedger()
    inputs = parity.assemble_inputs(
        parity.CountingFirestore(_live_fake_db(), ledger), fetch_rows=lambda: list(_API_ROWS),
        cargar_referencia=lambda: _bundle(main=(_entrada("1000000001", "Zulema Quintero Ortiz"),)), hoy=HOY, ledger=ledger)
    report = ledger.report()
    assert (report["E"], report["I"], report["S"], report["api_rows"]) == (2, 2, 3, 2)
    assert report["np_lookup"] == 0  # D33: the only uid (u1) is in the roster just read, so no get_all at all
    assert sorted(v["identificacion"] for v in inputs.roster_by_cedula.values()) == ["1000000001", "1000000002"]
    assert len(inputs.nombres_survey) == 3 and len(inputs.stickers) == 2
    assert inputs.run().inspectores  # and the assembled inputs really feed the engine


def test_assemble_inputs_np_lookups_count_only_the_uids_the_roster_cannot_resolve():
    db = _live_fake_db()
    # two inspectores docs with neither codigo nor identificacion: invisible to both roster maps, so only a get_all sees them
    db._collections["inspectores"] += [_Snap("solo-1", {"NP": "P2"}), _Snap("solo-2", {"NP": "P4"})]
    db._collections["evaluaciones"].append(_Snap("e3", {"inspector": {"uid": "solo-1"}}))
    db._collections["evaluaciones"].append(_Snap("e4", {"inspector": {"uid": "solo-2"}}))
    ledger = parity.ReadLedger()
    parity.assemble_inputs(parity.CountingFirestore(db, ledger), fetch_rows=lambda: list(_API_ROWS),
                           cargar_referencia=lambda: _bundle(main=(_entrada("1000000001", "Zulema Quintero Ortiz"),)),
                           hoy=HOY, ledger=ledger)
    assert ledger.report()["np_lookup"] == 2  # solo-1 and solo-2; u1 came from the roster


def test_assemble_inputs_partial_ledger_survives_a_failing_stream():
    ledger = parity.ReadLedger()
    db = parity.CountingFirestore(_FakeDb({"inspectores": _docs("i", 4)}, fail={"inspectores": 2}), ledger)
    with pytest.raises(RuntimeError):
        parity.assemble_inputs(db, fetch_rows=lambda: [], cargar_referencia=lambda: _bundle(), hoy=HOY, ledger=ledger)
    assert ledger.report()["I"] == 2


def test_assemble_inputs_refuses_a_degraded_reference_bundle():
    ledger = parity.ReadLedger()
    with pytest.raises(parity.HarnessError, match="reference bundle unavailable"):
        parity.assemble_inputs(parity.CountingFirestore(_live_fake_db(), ledger), fetch_rows=lambda: list(_API_ROWS),
                               cargar_referencia=lambda: ReferenciaBundle.vacia(motivo="sin_blob"), hoy=HOY, ledger=ledger)


# ── 12.18: projection through the real component caches ─────────────────────

def _routers():
    from app.routers import stickers, stickers_atencionsismo
    return stickers, stickers_atencionsismo


def test_ledger_projection_uses_ttls_and_reports_per_hour_and_per_day(monkeypatch):
    saved = parity.blob_lkg.save_json
    sim = parity.simulate_static_hour()  # the real caches on a fake clock, default configuration, one static hour
    assert sim["scans"] == {"roster": 2, "survey": 1, "evaluaciones": 4}  # component refreshes: the design's 2*I + S + 4*E
    assert sim["full_scans"] == {"roster": 2, "survey": 1, "evaluaciones": 1}  # D34: only the cold refresh reads everything
    assert sim["probes"] == {"survey": 1, "evaluaciones": 4}  # the cold signature probe / one probe per expiry
    assert sim["walks"] == 12  # one per 5-minute TTL
    assert sim["referencia_get"] == 2 and sim["depurar"] == 1 and sim["blob_put"] == 1  # cold start only
    assert parity.blob_lkg.save_json is saved  # nothing was left patched, nothing was uploaded

    projection = parity.project_reads(sim, parity.Sizes(E=1470, I=130, S=1900, api_rows=2990), open_hours_per_day=1)
    # every refresh finds a change: the scans PLUS the probe each refresh pays before it (S3): 1 survey + 4
    # evaluaciones probes of 3 reads (count() of 1,900 / 1,470 entries = 2 reads, + the newest document = 1)
    assert projection["worst_case_scan_reads_per_hour"] == 2 * 130 + 1900 + 4 * 1470 == 8040
    assert projection["worst_case_probe_reads_per_hour"] == (1 + 4) * 3 == 15
    assert projection["worst_case_reads_per_hour"] == 8040 + 15 == 8055
    assert projection["reads_per_hour_design_formula"] == 8040  # the design's own formula stays scan-only
    assert projection["budget_per_hour"] == parity.DEFAULT_BUDGET_PER_HOUR == 20_000 and projection["verdict"] == "PASS"
    eight = parity.project_reads(sim, parity.Sizes(E=1470, I=130, S=1900), open_hours_per_day=8)
    # static inputs over 8 open hours, worked by hand: roster 16 x 130; survey cold + 6 h reconcile = 2 scans of 1900
    # and 8 probes of 3 reads; evaluaciones 2 scans of 1470 and 32 probes of 3 reads
    assert eight["reads_per_day"] == 16 * 130 + (2 * 1900 + 8 * 3) + (2 * 1470 + 32 * 3) == 8_940
    assert eight["worst_case_reads_per_day"] == 8 * 8055 and eight["verdict_per_day"] is None
    text = parity.format_projection(eight)
    assert ("2 x 130 (I) + 1 x 1900 (S) + 4 x (1470 + 0) (E + U) + 1 survey probes x 3 + 4 evaluaciones probes x 3"
            " = 8,055") in text
    assert "design formula 2*I + S + 4*E = 8,040" in text
    assert "8,940" in text and "20,000" in text and "ratified by the owner 2026-09-19" in text
    assert "pending owner confirmation" not in text and "recommended default" not in text  # O1 is ratified
    assert parity.probe_reads(1924) == 3 and parity.probe_reads(1000) == 2 and parity.probe_reads(1001) == 3
    assert parity.probe_reads(1) == 2 and parity.probe_reads(0) == 1


def test_the_default_budget_is_per_open_hour_and_the_daily_budget_is_only_informational():
    sim = parity.simulate_static_hour()
    sizes = parity.Sizes(E=1470, I=131, S=1924, U=99)
    plain = parity.project_reads(sim, sizes)  # 8 open hours by default
    assert plain["verdict"] == "PASS" and plain["verdict_per_day"] is None and plain["open_hours_per_day"] == 8
    strict = parity.project_reads(sim, sizes, budget_per_day=10_000)
    assert strict["verdict"] == "PASS"  # the gate is per hour; the daily verdict is extra
    assert strict["verdict_per_day"] == "PASS" and strict["reads_per_day"] == 9_202 <= 10_000
    assert parity.project_reads(sim, sizes, budget_per_day=9_201)["verdict_per_day"] == "FAIL"


def test_projection_records_the_o1_figures_for_the_live_sizes():
    """E=1470, I=131, S=1924, U=99, 8 open hours (the 2026-09-19 measurement), by hand:
    worst case per hour, scans only, 2*131 + 1924 + 4*(1470+99) = 8,462 (the shipped chain before the two savings);
    after D33 the 99 np lookups are served by the roster (U=0): 8,066 of scans, and (S3) a refresh that finds a change
    pays its probe first: + 5 probes x 3 reads = 8,081 worst case; 9,004 static per 8 h."""
    sim = parity.simulate_static_hour()
    before = parity.project_reads(sim, parity.Sizes(E=1470, I=131, S=1924, U=99), open_hours_per_day=8)
    assert before["worst_case_scan_reads_per_hour"] == 2 * 131 + 1924 + 4 * (1470 + 99) == 8_462
    assert before["worst_case_reads_per_hour"] == 8_462 + 15 == 8_477
    after = parity.project_reads(sim, parity.Sizes(E=1470, I=131, S=1924, U=0), open_hours_per_day=8)
    assert after["worst_case_scan_reads_per_hour"] == 2 * 131 + 1924 + 4 * 1470 == 8_066
    assert after["worst_case_reads_per_hour"] == 8_066 + 5 * 3 == 8_081
    assert after["worst_case_reads_per_day"] == 8 * 8_081 == 64_648
    assert after["reads_per_day"] == 16 * 131 + (2 * 1924 + 8 * 3) + (2 * 1470 + 32 * 3) == 9_004
    assert after["reads_per_hour"] == pytest.approx(9_004 / 8, abs=0.1)


def test_projection_follows_the_configured_ttls_not_a_hardcoded_formula():
    default = parity.default_ttls()
    hour = parity.simulate_static_hour(parity.Ttls(**{**default.__dict__, "evaluaciones": 3600}))
    assert hour["scans"]["evaluaciones"] == 1  # design O1 option (b): 60 minutes
    sizes = parity.Sizes(E=1470, I=130, S=1900)
    # 3,630 of scans + the two probes (survey 1, evaluaciones 1) of 3 reads
    assert parity.project_reads(hour, sizes, open_hours_per_day=1)["worst_case_reads_per_hour"] == 2 * 130 + 1900 + 1470 + 2 * 3 == 3636


def test_projection_reads_the_real_router_constants(monkeypatch):
    _, router = _routers()
    monkeypatch.setattr(router, "ROSTER_CACHE_TTL_SECONDS", 600)
    assert parity.default_ttls().roster == 600
    assert parity.simulate_static_hour(parity.default_ttls())["scans"]["roster"] == 6


def test_the_reconcile_interval_comes_from_the_real_constant_and_is_a_configurable_ttl():
    from app.services import probed_scan

    assert parity.default_ttls().survey_reconcile == probed_scan.DEFAULT_RECONCILE_S == 6 * 3600
    assert parity.default_ttls().evaluaciones_reconcile == probed_scan.DEFAULT_RECONCILE_S


def test_a_shorter_reconcile_costs_more_full_scans_a_day():
    sizes = parity.Sizes(E=1470, I=131, S=1924)
    default = parity.default_ttls()
    every_two_hours = parity.Ttls(**{**default.__dict__, "survey_reconcile": 2 * 3600, "evaluaciones_reconcile": 2 * 3600})
    a = parity.project_reads(parity.simulate_static_hour(default), sizes, open_hours_per_day=8)
    b = parity.project_reads(parity.simulate_static_hour(every_two_hours), sizes, open_hours_per_day=8)
    assert b["full_scans_per_day"]["survey"] > a["full_scans_per_day"]["survey"] == 2
    assert b["reads_per_day"] > a["reads_per_day"]


def test_projection_counts_the_np_lookup_reads_when_measured():
    sim = parity.simulate_static_hour()
    projection = parity.project_reads(sim, parity.Sizes(E=1470, I=130, S=1900, U=120), open_hours_per_day=1)
    assert projection["worst_case_reads_per_hour"] == 2 * 130 + 1900 + 4 * (1470 + 120) + 5 * 3
    assert projection["reads_per_hour_design_formula"] == 8040
    assert "(E + U)" in parity.format_projection(projection)


@pytest.mark.parametrize("field", ["roster", "survey", "evaluaciones", "referencia", "walk"])
@pytest.mark.parametrize("bad", [0, -1, -0.5, float("nan"), float("inf"), True, None, "1800"])
def test_a_ttl_of_zero_or_less_is_rejected_not_treated_as_always_fresh(field, bad):
    ttls = parity.Ttls(**{**parity.default_ttls().__dict__, field: bad})
    with pytest.raises(parity.HarnessError, match="TTL"):
        parity.validate_ttls(ttls)
    with pytest.raises(parity.HarnessError, match="TTL"):
        parity.simulate_static_hour(ttls)


def test_projection_boundaries_budget_equal_passes_and_one_over_fails():
    sim = parity.simulate_static_hour()
    sizes = parity.Sizes(E=1470, I=130, S=1900)
    # the gate is the worst case INCLUDING the probes (8,040 of scans + 15 of probes)
    assert parity.project_reads(sim, sizes, open_hours_per_day=1, budget_per_hour=8055)["verdict"] == "PASS"
    assert parity.project_reads(sim, sizes, open_hours_per_day=1, budget_per_hour=8054)["verdict"] == "FAIL"
    assert parity.project_reads(sim, sizes, open_hours_per_day=1, budget_per_hour=8040)["verdict"] == "FAIL"  # scans alone would pass


def test_projection_zero_sized_collections_cost_only_the_empty_count_probes():
    projection = parity.project_reads(parity.simulate_static_hour(), parity.Sizes(E=0, I=0, S=0), open_hours_per_day=24)
    assert projection["worst_case_scan_reads_per_hour"] == 0
    assert projection["worst_case_reads_per_hour"] == 1 + 4 == 5 and projection["verdict"] == "PASS"  # 5 probes x 1 read
    probes = projection["probes_per_day"]
    assert projection["reads_per_day"] == probes["survey"] + probes["evaluaciones"]  # an empty count() still bills 1 read


def test_projection_rejects_a_nonsense_budget_hours_or_sizes():
    sim = parity.simulate_static_hour()
    for kwargs in ({"budget_per_day": 0}, {"budget_per_hour": 0}, {"budget_per_hour": -5}):
        with pytest.raises(parity.HarnessError, match="budget"):
            parity.project_reads(sim, parity.Sizes(E=1, I=1, S=1), open_hours_per_day=1, **kwargs)
    with pytest.raises(parity.HarnessError, match="open hours"):
        parity.project_reads(sim, parity.Sizes(E=1, I=1, S=1), open_hours_per_day=25)
    with pytest.raises(parity.HarnessError, match="size"):
        parity.project_reads(sim, parity.Sizes(E=-1, I=1, S=1), open_hours_per_day=1)


def test_simulation_flags_scan_counts_above_the_hourly_limits():
    default = parity.default_ttls()
    sim = parity.simulate_static_hour(parity.Ttls(**{**default.__dict__, "evaluaciones": 60}))  # 60 s: a hot loop
    projection = parity.project_reads(sim, parity.Sizes(E=1, I=1, S=1), open_hours_per_day=1)
    assert projection["limits"]["evaluaciones"] == {"measured": sim["scans"]["evaluaciones"], "limit": 4, "ok": False}
    assert projection["limits_ok"] is False
    assert parity.project_reads(parity.simulate_static_hour(), parity.Sizes(E=1, I=1, S=1),
                                open_hours_per_day=1)["limits_ok"] is True


# ── the snapshot file and the CLI ───────────────────────────────────────────

def _bundle_json(referencia):
    def entry(e):
        return {"cedula_key": e.cedula_key, "nombre_norm": e.nombre_norm, "np": e.np, "entidad": e.entidad,
                "codigo": e.codigo, "pasos": [], "no_persona": e.no_persona, "nombre": e.nombre, "telefono": e.telefono,
                "creado_en": e.creado_en, "id": e.id, "correo": e.correo, "tarjeta_profesional": e.tarjeta_profesional}
    return {"schema": 1, "generado_en": referencia.generado_en, "codigos_duplicados": [],
            "vercel": [entry(e) for e in referencia.vercel], "fase2": [entry(e) for e in referencia.fase2],
            "main": [entry(e) for e in referencia.main]}


def _snapshot_dict(inputs, ledger=None):
    data = {"stickers": inputs.stickers, "roster_by_cedula": inputs.roster_by_cedula, "nombres_survey": inputs.nombres_survey,
            "referencia": _bundle_json(inputs.referencia), "hoy": inputs.hoy.isoformat()}
    if ledger is not None:
        data["ledger"] = ledger
    return data


def _write_snapshot(path, data):
    import json
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return path


def _cli_files(tmp_path, *, xlsx_edit=None, ledger=None):
    inputs = _register_inputs()
    rows = _xlsx_rows(inputs.run())
    if xlsx_edit:
        xlsx_edit(rows)
    xlsx = tmp_path / "ref.xlsx"
    _write_xlsx(xlsx, rows)
    return _write_snapshot(tmp_path / "snap.json", _snapshot_dict(inputs, ledger)), xlsx


def test_load_snapshot_round_trips_the_inputs_through_json(tmp_path):
    inputs = _register_inputs()
    path = _write_snapshot(tmp_path / "s.json", _snapshot_dict(inputs, {"E": 10, "I": 5, "S": 3, "api_rows": 7, "U": 2}))
    loaded, sizes = parity.load_snapshot(path)
    assert loaded.hoy == HOY and loaded.roster_by_cedula == inputs.roster_by_cedula
    assert loaded.run().inspectores == inputs.run().inspectores  # the same engine result, so the JSON loses nothing
    assert sizes == parity.Sizes(E=10, I=5, S=3, api_rows=7, U=2)
    assert parity.load_snapshot(_write_snapshot(tmp_path / "t.json", _snapshot_dict(inputs)))[1] is None


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d.pop("stickers"), "stickers"),
    (lambda d: d.pop("hoy"), "hoy"),
    (lambda d: d.update(hoy="12/09/2026"), "hoy"),
    (lambda d: d.update(stickers={"a": 1}), "stickers"),
    (lambda d: d.update(roster_by_cedula=[1]), "roster_by_cedula"),
    (lambda d: d.update(referencia={"schema": 99}), "referencia"),
    (lambda d: d.update(ledger={"E": -1, "I": 1, "S": 1}), "ledger"),
    (lambda d: d.update(ledger={"E": 1, "I": 1, "S": 1, "zzz": 1}), "ledger"),
    (lambda d: d.update(ledger={"E": 1}), "ledger"),
])
def test_load_snapshot_rejects_a_malformed_snapshot_with_a_clear_message(tmp_path, mutate, message):
    data = _snapshot_dict(_register_inputs())
    mutate(data)
    with pytest.raises(parity.HarnessError, match=message):
        parity.load_snapshot(_write_snapshot(tmp_path / "s.json", data))


def test_load_snapshot_missing_or_invalid_json_file(tmp_path):
    with pytest.raises(parity.HarnessError, match="not found"):
        parity.load_snapshot(tmp_path / "nope.json")
    (tmp_path / "bad.json").write_text("{not json " + SECRET, encoding="utf-8")
    with pytest.raises(parity.HarnessError, match="cannot read snapshot") as info:
        parity.load_snapshot(tmp_path / "bad.json")
    assert SECRET not in str(info.value)


def test_cli_end_to_end_on_a_tiny_synthetic_snapshot_and_xlsx_passes_every_check(tmp_path, capsys):
    snapshot, xlsx = _cli_files(tmp_path, ledger={"E": 20, "I": 5, "S": 8, "api_rows": 40})
    code = parity.main(["--snapshot", str(snapshot), "--xlsx", str(xlsx), "--open-hours-per-day", "1"])
    captured = capsys.readouterr()
    assert code == parity.EXIT_OK, captured.out + captured.err
    for line in ("PARITY PASS", "DETERMINISM PASS", "TIMING PASS", "PROJECTION", "worst case per open hour", "OVERALL PASS"):
        assert line in captured.out, line
    assert captured.err == ""


def test_cli_output_has_no_pii_and_masks_every_key(tmp_path, capsys):
    def edit(rows):
        _row(rows, "1000000002")["np"] = "P9"
        _row(rows, "1000000001")["nombre_completo"] = "Otro Nombre Distinto"

    snapshot, xlsx = _cli_files(tmp_path, xlsx_edit=edit)
    code = parity.main(["--snapshot", str(snapshot), "--xlsx", str(xlsx)])
    out = capsys.readouterr()
    text = out.out + out.err
    assert code == parity.EXIT_CHECK_FAILED and "***002" in text
    for secret in ("Zulema", "Quintero", "zulema@example.test", "Otro Nombre", "3001110001", "TP-1", "Bernardo", "Fabio",
                   "Homero", "Externo", "Damaso", "Julieta"):
        assert secret not in text, secret
    assert not re.search(r"\d{5,}", text)


def test_cli_a_failing_parity_threshold_exits_1_and_says_so(tmp_path, capsys):
    snapshot, xlsx = _cli_files(tmp_path, xlsx_edit=lambda rows: _row(rows, "1000000002").update(np="P9"))
    code = parity.main(["--snapshot", str(snapshot), "--xlsx", str(xlsx)])
    out = capsys.readouterr().out
    assert code == parity.EXIT_CHECK_FAILED
    assert "PARITY FAIL" in out and "OVERALL FAIL" in out and "np below 99.0%" in out


def test_cli_missing_xlsx_exits_non_zero_with_a_clear_message_and_never_reports_a_pass(tmp_path, capsys):
    snapshot, _ = _cli_files(tmp_path)
    code = parity.main(["--snapshot", str(snapshot), "--xlsx", str(tmp_path / "missing.xlsx")])
    out = capsys.readouterr()
    assert code == parity.EXIT_ERROR
    assert "not found" in out.err and "ERROR" in out.err
    assert "PASS" not in out.out and "100" not in out.out  # nothing ran, nothing was "green"


def test_cli_skip_parity_runs_the_other_checks_and_says_it_skipped(tmp_path, capsys):
    snapshot, _ = _cli_files(tmp_path)
    code = parity.main(["--snapshot", str(snapshot), "--skip-parity", "--xlsx", str(tmp_path / "missing.xlsx")])
    out = capsys.readouterr().out
    assert code == parity.EXIT_OK
    assert "PARITY SKIPPED" in out and "DETERMINISM PASS" in out and "TIMING PASS" in out
    assert "PROJECTION SKIPPED" in out  # the snapshot carries no measured sizes


def test_cli_order_dependent_engine_exits_1_with_masked_keys(tmp_path, capsys, monkeypatch):
    snapshot, _ = _cli_files(tmp_path)
    monkeypatch.setattr(dep, "depurar", _order_dependent_engine)
    code = parity.main(["--snapshot", str(snapshot), "--skip-parity"])
    out = capsys.readouterr().out
    assert code == parity.EXIT_CHECK_FAILED
    assert "DETERMINISM FAIL" in out and "'primero'" in out and "***" in out


def test_cli_slow_engine_exits_1(tmp_path, capsys):
    snapshot, _ = _cli_files(tmp_path)
    code = parity.main(["--snapshot", str(snapshot), "--skip-parity", "--timing-ceiling", "0.000001"])
    assert code == parity.EXIT_CHECK_FAILED and "TIMING FAIL" in capsys.readouterr().out


def test_cli_empty_snapshot_is_an_error_exit_never_a_vacuous_pass(tmp_path, capsys):
    data = _snapshot_dict(_register_inputs())
    data.update(stickers=[], roster_by_cedula={})
    code = parity.main(["--snapshot", str(_write_snapshot(tmp_path / "e.json", data)), "--skip-parity"])
    out = capsys.readouterr()
    assert code == parity.EXIT_ERROR and "empty snapshot" in out.err and "PASS" not in out.out


def test_cli_projection_over_budget_exits_1_with_the_arithmetic(tmp_path, capsys):
    snapshot, xlsx = _cli_files(tmp_path, ledger={"E": 1470, "I": 130, "S": 1900})
    code = parity.main(["--snapshot", str(snapshot), "--xlsx", str(xlsx), "--open-hours-per-day", "8",
                        "--budget-per-hour", "5000"])
    out = capsys.readouterr().out
    assert code == parity.EXIT_CHECK_FAILED
    assert ("2 x 130 (I) + 1 x 1900 (S) + 4 x (1470 + 0) (E + U) + 1 survey probes x 3 + 4 evaluaciones probes x 3"
            " = 8,055") in out
    assert "5,000" in out and "FAIL" in out
    assert "PARITY PASS" in out  # the parity itself is fine: the budget is what fails


def test_cli_default_budget_is_20000_per_open_hour_and_passes_at_the_live_sizes(tmp_path, capsys):
    snapshot, xlsx = _cli_files(tmp_path, ledger={"E": 1470, "I": 131, "S": 1924})
    code = parity.main(["--snapshot", str(snapshot), "--xlsx", str(xlsx)])
    out = capsys.readouterr().out
    assert code == parity.EXIT_OK, out
    assert "budget per open hour: 20,000" in out and "ratified by the owner 2026-09-19" in out
    assert "pending owner confirmation" not in out and "recommended default" not in out
    assert "daily projection, information only" in out and "9,004" in out


def test_cli_a_daily_budget_is_only_an_extra_verdict(tmp_path, capsys):
    snapshot, xlsx = _cli_files(tmp_path, ledger={"E": 1470, "I": 131, "S": 1924})
    code = parity.main(["--snapshot", str(snapshot), "--xlsx", str(xlsx), "--budget-per-day", "5000"])
    out = capsys.readouterr().out
    assert code == parity.EXIT_CHECK_FAILED and "vs the daily budget 5,000: FAIL" in out


def test_cli_rejects_a_zero_or_negative_ttl(tmp_path, capsys):
    snapshot, xlsx = _cli_files(tmp_path, ledger={"E": 1, "I": 1, "S": 1})
    for value in ("0", "-5"):
        code = parity.main(["--snapshot", str(snapshot), "--xlsx", str(xlsx), "--ttl-roster", value])
        assert code == parity.EXIT_ERROR and "TTL roster" in capsys.readouterr().err


def test_cli_requires_exactly_one_of_live_or_snapshot(tmp_path):
    for argv in ([], ["--live", "--snapshot", str(tmp_path / "x.json")]):
        with pytest.raises(SystemExit) as info:
            parity.main(argv)
        assert info.value.code == 2


def test_cli_parses_the_live_flags():
    args = parity.build_parser().parse_args(["--live", "--xlsx", "ref.xlsx", "--budget-per-day", "5000"])
    assert args.live is True and args.snapshot is None and args.budget_per_day == 5000
    assert args.budget_per_hour == parity.DEFAULT_BUDGET_PER_HOUR
    assert parity.build_parser().parse_args(["--snapshot", "s.json", "--budget-per-hour", "7"]).budget_per_hour == 7
    assert parity.build_parser().parse_args(["--snapshot", "s.json"]).live is False


LIVE_ENV = ("FIREBASE_SERVICE_ACCOUNT_JSON", "VISITADOS_API_PASS", "BLOB_PRIVATE_TOKEN", "BLOB_READ_WRITE_TOKEN")


def test_cli_live_refuses_without_the_required_environment(monkeypatch, capsys):
    for name in LIVE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(parity, "collect_live", lambda *a, **k: pytest.fail("no live source may be touched"))
    code = parity.main(["--live"])
    captured = capsys.readouterr()
    err = captured.err
    assert "LEDGER" not in captured.out  # nothing was read, so there is no ledger to print
    assert code == parity.EXIT_ERROR
    for name in ("FIREBASE_SERVICE_ACCOUNT_JSON", "VISITADOS_API_PASS", "BLOB_PRIVATE_TOKEN"):
        assert name in err  # names only
    assert ".env" in err  # says where credentials must come from


def test_cli_live_needs_each_variable_and_accepts_either_blob_token(monkeypatch):
    env = {"FIREBASE_SERVICE_ACCOUNT_JSON": "x", "VISITADOS_API_PASS": "x", "BLOB_READ_WRITE_TOKEN": "x"}
    parity.check_live_env(env)  # the read-write token is an accepted fallback for the private read
    for missing in ("FIREBASE_SERVICE_ACCOUNT_JSON", "VISITADOS_API_PASS", "BLOB_READ_WRITE_TOKEN"):
        with pytest.raises(parity.HarnessError, match=missing if missing != "BLOB_READ_WRITE_TOKEN" else "BLOB_PRIVATE_TOKEN"):
            parity.check_live_env({k: v for k, v in env.items() if k != missing})
    with pytest.raises(parity.HarnessError):
        parity.check_live_env({**env, "VISITADOS_API_PASS": "   "})  # blank counts as missing


def test_live_env_names_match_the_source_of_truth():
    from app.credentials import clients
    from app.services import atencionsismo
    assert parity.LIVE_ENV_FIREBASE == clients._ENV_VARS["sismo"]
    assert parity.LIVE_ENV_API_PASS == atencionsismo.PASS_ENV


def test_cli_live_failure_keeps_the_partial_ledger_exits_non_zero_and_withholds_the_message(monkeypatch, capsys):
    for name, value in zip(LIVE_ENV, ("x", "x", "x", "")):
        monkeypatch.setenv(name, value)

    def broken(environ, ledger, **kwargs):
        ledger.count_docs("evaluaciones", 3)
        raise RuntimeError(f"stream broke {SECRET}")

    monkeypatch.setattr(parity, "collect_live", broken)
    code = parity.main(["--live", "--skip-parity"])
    out = capsys.readouterr()
    assert code == parity.EXIT_ERROR
    assert "E 3" in out.out  # the partial count survives
    assert "RuntimeError" in out.err and SECRET not in out.out + out.err


def test_collect_live_uses_the_read_only_wrapper_and_refuses_any_blob_put():
    ledger = parity.ReadLedger()

    def fetch_rows():
        parity.blob_lkg.save_json("data/x.json", {})  # what a write attempt inside the live path would look like
        return []

    with pytest.raises(parity.ReadOnlyViolation):
        parity.collect_live({}, ledger, db_factory=_live_fake_db, fetch_rows=fetch_rows,
                            cargar_referencia=lambda: _bundle(), hoy=HOY)
    assert ledger.report()["blob_put"] == 1


def test_collect_live_with_fakes_counts_everything_and_returns_the_inputs():
    ledger = parity.ReadLedger()
    inputs = parity.collect_live({}, ledger, db_factory=_live_fake_db, fetch_rows=lambda: list(_API_ROWS),
                                 cargar_referencia=lambda: _bundle(main=(_entrada("1000000001", "Zulema Quintero Ortiz"),)),
                                 hoy=HOY)
    report = ledger.report()
    assert (report["E"], report["I"], report["S"], report["api_rows"], report["blob_put"]) == (2, 2, 3, 2, 0)
    assert inputs.run().inspectores


def test_the_script_runs_standalone_outside_pytest():
    import subprocess
    script = Path(__file__).resolve().parents[3] / "scripts" / "parity_inspectores_depurado.py"
    done = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "--snapshot" in done.stdout and "--live" in done.stdout


# ── live parity run 2026-09-19: estado replay (D-REMAP / D7), D12, D-EXENTOS, residuals, ledger ──────────────────

def _estado_inputs():
    """Fake identities that reproduce, in miniature, the three estado shapes of the first live run.

    a  never had a codigo, valid `sistema` sticker  -> notebook `activo` (through tiene_sticker_valido), engine `revisar`
    c  holds 033 but Vercel says Q owns it: the remap clears it (Q has a profile, so NO review item); valid sticker
    d  holds 032 but Vercel says R owns it: cleared; NO stickers, no reference -> engine `candidato_desactivacion`,
       the notebook (its `desactivar` set is computed BEFORE the remap) says `revisar`
    f  a genuine non-person account with a codigo (control)"""
    roster = {
        "a": _roster("1000000041", "Ana Lucia Restrepo Gil"),
        "c": _roster("1000000043", "Cecilia Duran Solano", "033"),
        "d": _roster("1000000044", "Dario Escobar Pinto", "032"),
        "e": _roster("1000000045", "Elena Fajardo Roa", "050"),
        "f": _roster("1000000046", "Brigada Sur Norte", "051"),
    }
    names = {"1000000041": "Ana Lucia Restrepo Gil", "1000000043": "Cecilia Duran Solano",
             "1000000044": "Dario Escobar Pinto", "1000000045": "Elena Fajardo Roa", "1000000046": "Brigada Sur Norte",
             "1000000050": "Quirino Zapata Uribe", "1000000051": "Rocio Tello Vidal"}
    return _inputs(
        roster=roster,
        vercel=(_entrada("1000000050", "Quirino Zapata Uribe", "P1", "", "033"),
                _entrada("1000000051", "Rocio Tello Vidal", "P1", "", "032"),
                _entrada("1000000045", "Elena Fajardo Roa", "P2", "", "050")),
        main=tuple(_entrada(cedula, nombre) for cedula, nombre in names.items()),
        stickers=[_sticker("1000000041", "sistema", "2026-09-10T10:00:00Z"),
                  _sticker("1000000043", "sistema", "2026-09-09T10:00:00Z")],
    )


def _estado_notebook_rows(inputs):
    """The xlsx the NOTEBOOK produced for `_estado_inputs()`: the D7 rows are `activo` via tiene_sticker_valido and the
    pre-remap `desactivar` set left `d` in `revisar`. `codigo_inspector_original` is the notebook's own pre-remap column."""
    rows = _xlsx_rows(inputs.run())
    _row(rows, "1000000041").update(estado_sugerido="activo", tiene_sticker_valido="True", codigo_inspector_original="")
    _row(rows, "1000000043").update(estado_sugerido="activo", tiene_sticker_valido="True", codigo_inspector_original="033")
    _row(rows, "1000000044").update(estado_sugerido="revisar", tiene_sticker_valido="False", codigo_inspector_original="032")
    return rows


def _estado_mismatches(report):
    return {m["key"]: m["explained"] for m in report["columns"]["estado_sugerido"]["mismatches"]}


def test_estado_scenario_is_the_shape_of_the_live_run_engine_side():
    depurado = _estado_inputs().run()
    by_key = {r["identidad_key"]: r for r in depurado.inspectores}
    assert by_key["1000000041"]["estado_sugerido"] == "revisar" and by_key["1000000041"]["codigo"] == ""
    assert by_key["1000000043"]["estado_sugerido"] == "revisar" and by_key["1000000043"]["codigo"] == ""
    assert by_key["1000000044"]["estado_sugerido"] == "candidato_desactivacion"
    assert by_key["1000000050"]["codigo"] == "033" and by_key["1000000051"]["codigo"] == "032"
    assert by_key["1000000046"]["estado_sugerido"] == "no_persona"
    # the 16-of-18 shape: a clear-and-reassign remap emits NO review item, so item-based tagging cannot see it
    assert not [i for i in depurado.revision_manual if i["motivo"].startswith("remap_")]


def test_estado_notebook_activo_without_codigo_is_d7_by_key_never_unexplained():
    inputs = _estado_inputs()
    report = _run(inputs, _estado_notebook_rows(inputs))
    assert _estado_mismatches(report)["1000000041"] == "D7"  # never had a codigo (the a3 shape)
    assert "1000000041" in report["register"]["D7"]
    assert report["register"]["D7-SIN-EXPLICAR"] == []


def test_estado_activo_after_a_cleared_codigo_is_d7_not_d_remap_because_the_stickers_not_the_remap_cause_it():
    """The a1 shape: the notebook cleared the codigo too (`codigo` matches), and says `activo` only through
    tiene_sticker_valido. The pre-remap sets are skipped on a sticker holder in both models, so the remap cannot be the cause."""
    inputs = _estado_inputs()
    report = _run(inputs, _estado_notebook_rows(inputs))
    assert _estado_mismatches(report)["1000000043"] == "D7"
    assert "1000000043" in report["register"]["D7"] and "1000000043" not in report["register"]["D-REMAP"]
    assert report["columns"]["codigo"]["mismatches"] == []  # the codigo column agrees: the notebook cleared it as well


def test_estado_revisar_vs_desactivacion_after_a_cleared_codigo_is_d_remap_by_key():
    """The pre-remap `desactivar` rule (the 3 rows of the 2026-09-16 decision): no stickers, codigo cleared by the remap."""
    inputs = _estado_inputs()
    report = _run(inputs, _estado_notebook_rows(inputs))
    assert _estado_mismatches(report)["1000000044"] == "D-REMAP"
    assert report["register"]["D-REMAP"] == ["1000000044"]


def test_estado_explained_rows_leave_the_denominator_and_the_gate_passes_only_when_all_are_explained():
    inputs = _estado_inputs()
    report = _run(inputs, _estado_notebook_rows(inputs))
    estado = report["columns"]["estado_sugerido"]
    assert estado["n"] - estado["n_effective"] == 3 and estado["passed"] is True
    assert "estado_sugerido" not in " ".join(report["failures"])


def test_estado_a_mismatch_neither_rule_reproduces_stays_unexplained_and_fails_the_gate():
    inputs = _estado_inputs()
    rows = _estado_notebook_rows(inputs)
    _row(rows, "1000000041").update(estado_sugerido="candidato_desactivacion")  # has a sticker: no rule yields this
    report = _run(inputs, rows)
    assert _estado_mismatches(report)["1000000041"] is None
    assert "1000000041" not in report["register"]["D7"]
    assert report["columns"]["estado_sugerido"]["passed"] is False
    assert report["passed"] is False


def test_estado_notebook_row_inconsistent_with_its_own_rule_is_not_explained():
    # `activo` with no codigo AND tiene_sticker_valido false cannot come from the notebook's rule: never excuse it
    inputs = _estado_inputs()
    rows = _estado_notebook_rows(inputs)
    _row(rows, "1000000041").update(tiene_sticker_valido="False")
    assert _estado_mismatches(_run(inputs, rows))["1000000041"] is None


def test_estado_a_no_persona_flip_is_never_excused_by_the_register():
    inputs = _estado_inputs()
    rows = _estado_notebook_rows(inputs)
    _row(rows, "1000000046").update(estado_sugerido="activo")  # the engine says no_persona
    mismatches = _estado_mismatches(_run(inputs, rows))
    assert mismatches["1000000046"] is None


def test_estado_a_stale_codigo_in_the_notebook_is_a_codigo_difference_not_an_estado_rule_excuse():
    """The notebook still holds 033 (`activo` through its codigo) while the engine cleared it: the estado differs
    BECAUSE the codigo differs (a `codigo` mismatch, unexplained here), so neither D7 nor D-REMAP may excuse it."""
    inputs = _estado_inputs()
    rows = _estado_notebook_rows(inputs)
    _row(rows, "1000000043").update(codigo="033", tiene_sticker_valido="False", estado_sugerido="activo")
    report = _run(inputs, rows)
    assert _estado_mismatches(report)["1000000043"] is None
    assert [m["key"] for m in report["columns"]["codigo"]["mismatches"]] == ["1000000043"]
    assert "1000000043" not in report["register"]["D7"] + report["register"]["D-REMAP"]


def test_estado_cause_returns_none_when_neither_engine_difference_reproduces_the_engine_estado():
    """Unit level: the notebook's rule reproduces ITS estado, but the engine's estado is not what turning D-REMAP or
    D7 on gives (a shape the two registered rules do not cover): unexplained, never a guessed label."""
    ctx = {"pre": {"k": {"no_persona": False, "sticker": True, "referencia": False, "codigo": False}}}
    backend = {"estado_sugerido": "candidato_desactivacion", "codigo": ""}
    xlsx = {"estado_sugerido": "revisar", "codigo": "", "tiene_sticker_valido": "False", "codigo_inspector_original": ""}
    assert parity._estado_cause("k", backend, xlsx, ctx) is None
    assert parity._estado_cause("missing-key", backend, xlsx, ctx) is None


def test_estado_without_the_notebook_pre_remap_column_the_engine_pre_state_is_used():
    inputs = _estado_inputs()
    rows = _estado_notebook_rows(inputs)
    for row in rows:
        row.pop("codigo_inspector_original", None)
    mismatches = _estado_mismatches(_run(inputs, rows))
    assert mismatches["1000000044"] == "D-REMAP" and mismatches["1000000043"] == "D7"


def test_estado_the_notebooks_own_pre_remap_column_wins_over_the_engine_reconstruction():
    inputs = _estado_inputs()
    rows = _estado_notebook_rows(inputs)
    _row(rows, "1000000044").update(codigo_inspector_original="")  # the notebook says it never held a codigo
    assert _estado_mismatches(_run(inputs, rows))["1000000044"] is None  # `revisar` would then be impossible


def test_estado_hand_made_rows_without_engine_context_are_never_tagged():
    backend = [_hand("2000000001", estado_sugerido="revisar")]
    xlsx = [_hand_xlsx("2000000001", estado_sugerido="activo", tiene_sticker_valido="True")]
    report = _hand_compare(backend, xlsx)
    assert _estado_mismatches(report) == {"2000000001": None}


def test_estado_report_lists_the_d7_and_d_remap_keys_masked():
    inputs = _estado_inputs()
    report = _run(inputs, _estado_notebook_rows(inputs))
    text = parity.format_parity_report(report)
    assert "D7: 2 [***041, ***043]" in text and "D-REMAP: 1 [***044]" in text
    assert "[D7]" in text and "[D-REMAP]" in text  # each mismatch carries its cause


def test_notebook_estado_model_precedence_and_edges():
    f = parity.notebook_estado
    base = dict(no_persona=False, sticker=False, referencia=False, pre_codigo=False, post_codigo=False, tiene_sticker_valido=False)
    assert f(**{**base, "no_persona": True, "post_codigo": True}) == "no_persona"
    assert f(**base) == "candidato_desactivacion"
    assert f(**{**base, "referencia": True}) == "revisar"
    assert f(**{**base, "pre_codigo": True}) == "revisar"  # skipped both sets: falls to the trailing `revisar`
    assert f(**{**base, "pre_codigo": True, "post_codigo": True}) == "activo"
    assert f(**{**base, "sticker": True, "tiene_sticker_valido": True}) == "activo"  # activo through the stickers alone
    assert f(**{**base, "sticker": True}) == "revisar"


# ── D12: entidad taken from `main` ─────────────────────────────────────────────────────────────────────────────────

def _entidad_inputs():
    roster = {k: _roster(c, n, entidad=e) for k, (c, n, e) in {
        "a": ("1000000071", "Amparo Lozano Cruz", ""), "b": ("1000000072", "Baltasar Rivas Mena", ""),
        "c": ("1000000073", "Camila Osorio Pena", "CAMACOL"), "d": ("1000000074", "Dora Quintana Vela", ""),
        "e": ("1000000075", "Efrain Gallo Sanz", "")}.items()}
    return _inputs(
        roster=roster,
        vercel=(_entrada("1000000072", "Baltasar Rivas Mena", "P1", "SGRED", ""),),
        main=(_entrada("1000000071", "Amparo Lozano Cruz", entidad="DAGRD"),   # entidad ONLY in main -> D12
              _entrada("1000000072", "Baltasar Rivas Mena", entidad="OTRA"),    # Vercel by cedula wins over main
              _entrada("1000000073", "Camila Osorio Pena"),
              _entrada("1000000074", "Dora Quintana Vela", entidad="EPM"),
              _entrada("1000000075", "Efrain Gallo Sanz")),
    )


def _entidad_report(**xlsx_over):
    inputs = _entidad_inputs()
    rows = _xlsx_rows(inputs.run())
    for key in ("1000000071", "1000000074"):
        _row(rows, key)["entidad"] = ""  # the notebook maps entidad from the Vercel cedula map only
    for key, value in xlsx_over.items():
        _row(rows, key.removeprefix("k"))["entidad"] = value
    return _run(inputs, rows)


def test_entidad_from_main_where_the_notebook_is_blank_is_d12_by_key_and_leaves_the_gate():
    report = _entidad_report()
    entidad = report["columns"]["entidad"]
    assert {m["key"]: (m["backend"], m["xlsx"], m["explained"]) for m in entidad["mismatches"]} == {
        "1000000071": ("DAGRD", "", "D12"), "1000000074": ("EPM", "", "D12")}
    assert report["register"]["D12"] == ["1000000071", "1000000074"]
    assert entidad["n"] - entidad["n_effective"] == 2 and entidad["passed"] is True
    assert "D12: 2 [***071, ***074]" in parity.format_parity_report(report)


def test_entidad_unexplained_mismatches_still_fail_the_gate():
    # a Firestore-sourced value the notebook lacks is not D12 (only `entidad_main` is registered)
    report = _entidad_report(**{"k1000000073": ""})
    assert {m["key"]: m["explained"] for m in report["columns"]["entidad"]["mismatches"]}["1000000073"] is None
    assert report["register"]["D12"] == ["1000000071", "1000000074"]
    assert report["columns"]["entidad"]["passed"] is False  # 1 unexplained of 3 effective rows


def test_entidad_from_main_but_a_different_non_blank_notebook_value_is_not_d12():
    report = _entidad_report(**{"k1000000071": "OTRO VALOR"})
    assert {m["key"]: m["explained"] for m in report["columns"]["entidad"]["mismatches"]}["1000000071"] is None
    assert "1000000071" not in report["register"]["D12"]


def test_entidad_vercel_by_cedula_beats_main_so_it_is_never_tagged_d12():
    report = _entidad_report(**{"k1000000072": ""})
    assert {m["key"]: m["explained"] for m in report["columns"]["entidad"]["mismatches"]}["1000000072"] is None
    assert "1000000072" not in report["register"]["D12"]


def test_entidad_main_value_backfilled_through_a_d_p2_merge_is_still_d12():
    inputs = _inputs(main=(_entrada("1000000081", "Gabriela Ochoa Ruiz", "P1"),
                           _entrada("1000000082", "Gabriela Ochoa Ruiz", entidad="DAGRD"),
                           _entrada("1000000083", "Horacio Pinzon Leon", "P1"),
                           _entrada("1000000084", "Isabel Cuesta Mora", "P1")))
    rows = _xlsx_rows(inputs.run())
    _row(rows, "1000000081")["entidad"] = ""
    report = _run(inputs, rows)
    assert {m["key"]: m["explained"] for m in report["columns"]["entidad"]["mismatches"]} == {"1000000081": "D12"}


def test_entidad_d_entidadtrim_is_not_relabelled_d12():
    report = _entidad_report(**{"k1000000073": " CAMACOL "})
    tags = {m["key"]: m["explained"] for m in report["columns"]["entidad"]["mismatches"]}
    assert tags["1000000073"] == "D-ENTIDADTRIM"
    assert report["columns"]["entidad"]["n_effective"] == 3  # only the two D12 rows leave the gated denominator


def test_entidad_hand_made_rows_without_engine_context_are_never_tagged_d12():
    report = _hand_compare([_hand("2000000001", entidad="DAGRD")], [_hand_xlsx("2000000001", entidad="")])
    assert report["columns"]["entidad"]["mismatches"][0]["explained"] is None
    assert report["register"]["D12"] == []


def test_d12_is_in_the_register_names():
    assert "D12" in parity.REGISTER_NAMES


# ── D-EXENTOS: registered survivors are recognised by the engine's own exemption ───────────────────────────────────

def test_exento_survivor_added_after_the_xlsx_snapshot_is_recognised_not_unexplained():
    """Repro of the live `***715`: a person added to `main` after the notebook ran is not in its externos sheet."""
    inputs = _register_inputs()
    rows, _ = _notebook_xlsx(inputs)
    externos = [{"identificacion": "12345"}]  # the snapshot's sheet does not know 12346
    report = _run(inputs, rows, externos_rows=externos, n_colapsados_notebook=1)
    assert "12346" in report["register"]["D-EXENTOS"]
    assert report["extras"]["exento_survivor"] == ["12346"]
    assert report["extras"]["exento_post_snapshot"] == ["12346"]
    assert report["extras"]["unexplained"] == []
    assert "row(s) beyond the enumerated extras" not in " ".join(report["failures"])


def test_exento_survivor_the_notebook_did_collapse_is_not_listed_as_post_snapshot():
    inputs = _register_inputs()
    rows, externos = _notebook_xlsx(inputs)
    report = _run(inputs, rows, externos_rows=externos)
    assert report["extras"]["exento_survivor"] == ["12346"] and report["extras"]["exento_post_snapshot"] == []


def test_only_in_backend_collapse_candidate_that_the_engine_did_not_exempt_stays_unexplained():
    inputs = _register_inputs()
    rows, externos = _notebook_xlsx(inputs)
    rows = [r for r in rows if parity.normalize_key(r["identificacion"]) != "1000000006"]  # a non-exempt main person
    report = _run(inputs, rows, externos_rows=externos)
    assert report["extras"]["unexplained"] == ["1000000006"]


def test_exento_registration_needs_the_row_to_be_a_collapse_candidate():
    """A survey-corroborated person who is NOT a collapse candidate (has a codigo) is not a D-EXENTOS survivor."""
    inputs = _inputs(main=(_entrada("12399", "Externo Con Codigo", codigo="044"),), survey=["Externo Con Codigo"])
    assert parity.build_register(inputs, inputs.run())["D-EXENTOS"] == []


def test_row_accounting_line_states_every_backend_row_and_the_residual():
    inputs = _register_inputs()
    rows, externos = _notebook_xlsx(inputs)
    report = _run(inputs, rows, externos_rows=externos)
    text = parity.format_parity_report(report)
    matched, backend = report["counts"]["matched"], report["counts"]["backend"]
    assert (f"row accounting: backend {backend} = matched {matched} + firestore-only 1 + D-EXENTOS survivors 1"
            f" + unexplained 0") in text
    assert report["accounting"] == {"backend": backend, "matched": matched, "firestore_only": 1,
                                    "exento_survivor": 1, "unexplained": 0, "balanced": True}


def test_row_accounting_balances_with_unexplained_rows_listed_never_absorbed():
    inputs = _register_inputs()
    rows = [r for r in _xlsx_rows(inputs.run()) if parity.normalize_key(r["identificacion"]) != "1000000006"]
    report = _run(inputs, rows)
    assert report["accounting"]["unexplained"] == 1 and report["accounting"]["balanced"] is True
    assert report["extras"]["unexplained"] == ["1000000006"]


# ── D-N-COLAPSADOS: residual rows by key, with the reason ─────────────────────────────────────────────────────────

def _residual_scenario():
    grupo = {"identidad_key": "GRUPO-EXTERNOS", "n_colapsados": 2, "detalle": (
        {"identificacion": "2000000001", "nombre_completo": "Fake A", "motivo": "cuenta_no_persona", "ultimo_sticker": None},
        {"identificacion": "2000000005", "nombre_completo": "Fake E", "motivo": "cedula_sospechosa", "ultimo_sticker": None},
    )}
    backend = [_hand("2000000002"), _hand("2000000003", cedulas_unificadas=["2000000004"])]
    depurado = dep.Depuracion(activa=True, motivo="", referencia_generada_en="2026-09-12",
                              inspectores=tuple(backend), grupo_externos=grupo, alias_nombres={}, revision_manual=())
    xlsx = [_hand_xlsx("2000000001"), _hand_xlsx("2000000002"), _hand_xlsx("2000000003")]
    externos = [{"identificacion": "2000000002"}, {"identificacion": "2000000004"}, {"identificacion": "2000000006"}]
    return parity.compare(depurado, xlsx, parity.build_register(_inputs(), depurado),
                          externos_rows=externos, n_colapsados_notebook=3)


def test_n_colapsados_residual_rows_are_listed_by_key_with_their_reason():
    n = _residual_scenario()["register"]["D-N-COLAPSADOS"]
    assert n["residual_only_backend"] == ["2000000001", "2000000005"]
    assert n["residual_only_notebook"] == ["2000000002", "2000000004", "2000000006"]
    assert n["residual_reasons"] == {
        "2000000001": "notebook kept the row (backend motivo cuenta_no_persona)",
        "2000000005": "absent from the reference xlsx (backend motivo cedula_sospechosa)",
        "2000000002": "backend row is not a collapse candidate (no flag)",
        "2000000004": "absorbed by a D-P2 survivor in the backend",
        "2000000006": "absent from the backend",
    }


def test_n_colapsados_unexplained_delta_is_exactly_the_residual_balance():
    n = _residual_scenario()["register"]["D-N-COLAPSADOS"]
    assert n["unexplained_delta"] == len(n["residual_only_backend"]) - len(n["residual_only_notebook"]) == -1
    assert n["backend"] == 2 and n["expected"] == 3


def test_n_colapsados_report_prints_each_residual_reason_with_masked_keys():
    text = parity.format_parity_report(_residual_scenario())
    assert "***001: notebook kept the row (backend motivo cuenta_no_persona)" in text
    assert "***006: absent from the backend" in text
    assert "2000000001" not in text


def test_n_colapsados_a_real_survivor_merged_with_a_non_person_duplicate_is_not_collapsed_and_not_a_residual():
    """The corrected no_persona rule end to end (D-SURVFLAGS): the survivor keeps its own flags, so it is a plain row
    in the engine, exactly like in the notebook (which flags per row BEFORE the unification)."""
    main = [
        _entrada("1000000061", "Pilar Vega Rojas", "P1", correo="pilar@example.test"),
        dataclasses.replace(_entrada("1000000062", "Pilar Vega Rojas", "P1", correo="pilar@import.local"), no_persona=True),
        _entrada("12348", "Externo Sospechoso Tres"),
        _entrada("1000000063", "Quique Mora Soto", "P1", correo="quique@example.test"),
    ]
    inputs = _inputs(main=main)
    depurado = inputs.run()
    survivor = next(r for r in depurado.inspectores if r["identidad_key"] == "1000000061")
    assert survivor["no_persona"] is False and depurado.grupo_externos["n_colapsados"] == 1
    rows = _xlsx_rows(depurado)
    report = _run(inputs, rows, externos_rows=[{"identificacion": "12348"}], n_colapsados_notebook=1)
    n = report["register"]["D-N-COLAPSADOS"]
    assert (n["backend"], n["expected"], n["unexplained_delta"]) == (1, 1, 0)
    assert n["residual_only_backend"] == [] and n["residual_only_notebook"] == []


# ── ledger projection: long TTLs and the probe-gated components, over the whole modeled day ───────────────────────

LIVE_SIZES = dict(E=1470, U=99, I=131, S=1924)  # the first live run
HOURS = 3600


def _project(roster_h, survey_h, evaluaciones_h, open_hours=8, **sizes):
    default = parity.default_ttls()
    ttls = parity.Ttls(**{**default.__dict__, "roster": roster_h * HOURS, "survey": survey_h * HOURS,
                          "evaluaciones": evaluaciones_h * HOURS})
    return parity.project_reads(parity.simulate_static_hour(ttls), parity.Sizes(**(sizes or LIVE_SIZES)),
                                open_hours_per_day=open_hours)


@pytest.mark.parametrize("roster_h,survey_h,evaluaciones_h,per_day", [
    # refreshes fall at k x (TTL + 1 s) after the cold one, inside 28,800 s; the 6 h reconcile fires at the first
    # refresh at or after 21,600 s. Every figure worked by hand:
    # (1,1,1): 8 refreshes each; roster 8 x 131; survey 2 scans x 1924 + 8 probes x 3; evaluaciones 2 x 1569 + 8 x 3
    (1, 1, 1, 8 * 131 + (2 * 1924 + 8 * 3) + (2 * 1569 + 8 * 3)),
    # (2,8,4): roster refreshes 0, 7201, 14402, 21603 -> 4; survey only the cold one; evaluaciones 0 and 14401
    (2, 8, 4, 4 * 131 + (1924 + 3) + (1569 + 2 * 3)),
    # (8,8,8): the cold refresh only
    (8, 8, 8, 131 + (1924 + 3) + (1569 + 3)),
])
def test_projection_table_from_the_live_run_with_long_ttls(roster_h, survey_h, evaluaciones_h, per_day):
    projection = _project(roster_h, survey_h, evaluaciones_h)
    assert projection["reads_per_day"] == per_day
    assert projection["verdict"] == "PASS"  # the gate is the WORST CASE of one refresh cycle, far under 20,000


def test_projection_refreshes_per_day_are_the_exact_simulation_not_a_floor_formula():
    projection = _project(2, 8, 4)
    assert projection["scans_per_day"] == {"roster": 4, "survey": 1, "evaluaciones": 2}
    assert projection["full_scans_per_day"] == {"roster": 4, "survey": 1, "evaluaciones": 1}
    assert projection["probes_per_day"] == {"survey": 1, "evaluaciones": 2}
    text = parity.format_projection(projection)
    assert "roster 4 x 131 (I) + survey 1 scans x 1924 (S) + 1 probes x 3 + evaluaciones 1 scans x (1470 + 99) (E + U)" in text
    assert "+ 2 probes x 3 = 4,026 per day" in text


def test_projection_ttl_of_exactly_one_hour_refreshes_every_3601_seconds():
    projection = _project(1, 1, 1)
    assert projection["scans_per_day"] == {"roster": 8, "survey": 8, "evaluaciones": 8}
    assert projection["full_scans_per_day"] == {"roster": 8, "survey": 2, "evaluaciones": 2}  # cold + the 6 h reconcile


def test_projection_fractional_open_hours_and_a_ttl_longer_than_the_day():
    projection = _project(2, 12, 24, open_hours=7.5)
    assert projection["scans_per_day"] == {"roster": 4, "survey": 1, "evaluaciones": 1}
    assert projection["reads_per_day"] == 4 * 131 + (1924 + 3) + (1469 + 99 + 1 + 3)


def test_projection_mixed_short_and_long_ttls_use_each_components_own_clock():
    default = parity.default_ttls()
    ttls = parity.Ttls(**{**default.__dict__, "roster": 1800, "survey": 8 * HOURS, "evaluaciones": 900})
    projection = parity.project_reads(parity.simulate_static_hour(ttls), parity.Sizes(**LIVE_SIZES), open_hours_per_day=8)
    assert projection["scans_per_day"] == {"roster": 16, "survey": 1, "evaluaciones": 32}


def test_projection_default_configuration_over_a_working_day():
    sim = parity.simulate_static_hour()
    projection = parity.project_reads(sim, parity.Sizes(E=1470, I=130, S=1900), open_hours_per_day=8)
    assert projection["scans_per_day"] == {"roster": 16, "survey": 8, "evaluaciones": 32}
    assert projection["full_scans_per_day"] == {"roster": 16, "survey": 2, "evaluaciones": 2}
    assert projection["probes_per_day"] == {"survey": 8, "evaluaciones": 32}
    assert projection["reads_per_day"] == 8_940 and projection["worst_case_reads_per_day"] == 8 * (8_040 + 15)


def test_projection_zero_sized_collections_with_long_ttls_cost_one_empty_count_each():
    projection = _project(8, 8, 8, E=0, I=0, S=0)
    assert projection["reads_per_day"] == 2  # the cold probe of each probe-gated component: count() of nothing = 1 read
    assert projection["worst_case_scan_reads_per_hour"] == 0
    assert projection["worst_case_reads_per_hour"] == 2  # one probe of one read each, nothing else


# ── D-N-COLAPSADOS: residuals paired by PERSON (the D-P2 name group), not by cedula key ────────────────────────────

def _pair_scenario(backend, notebook):
    """`backend` / `notebook`: `(cedula, name)` rows that only one side collapsed. One matched row keeps the run valid."""
    detalle = tuple({"identificacion": k, "nombre_completo": n, "motivo": "cuenta_no_persona", "ultimo_sticker": None}
                    for k, n in backend)
    grupo = {"identidad_key": "GRUPO-EXTERNOS", "n_colapsados": len(detalle), "detalle": detalle}
    depurado = dep.Depuracion(activa=True, motivo="", referencia_generada_en="2026-09-12",
                              inspectores=(_hand("3000000001"),), grupo_externos=grupo, alias_nombres={},
                              revision_manual=())
    externos = [{"identificacion": k, "nombre_completo": n} for k, n in notebook]
    return parity.compare(depurado, [_hand_xlsx("3000000001")], parity.build_register(_inputs(), depurado),
                          externos_rows=externos, n_colapsados_notebook=len(externos))


def _pairs(report):
    return [(p["backend"], p["notebook"]) for p in report["register"]["D-N-COLAPSADOS"]["same_person_different_survivor"]]


def test_residuals_of_the_same_person_under_different_survivor_cedulas_pair_one_to_one_and_are_explained():
    """The live run: 4 backend-only keys pair with 4 notebook-only keys inside the same D-P2 name group."""
    names = ["Ana Gomez Ruiz", "Beto Mora Sol", "Carla Diaz Paz", "Dario Vega Rey"]
    backend = [(f"200000000{i}", n) for i, n in enumerate(names, 1)]
    notebook = [(f"200000001{i}", n) for i, n in enumerate(names, 1)]
    report = _pair_scenario(backend, notebook)
    n = report["register"]["D-N-COLAPSADOS"]
    assert _pairs(report) == [("2000000001", "2000000011"), ("2000000002", "2000000012"),
                              ("2000000003", "2000000013"), ("2000000004", "2000000014")]
    assert n["residual_only_backend"] == [] and n["residual_only_notebook"] == [] and n["residual_reasons"] == {}
    assert (n["backend"], n["notebook"], n["expected"], n["unexplained_delta"]) == (4, 4, 4, 0)
    assert not any("n_colapsados" in f for f in report["failures"])


def test_a_pair_carries_no_name_and_the_pairs_are_keyed_by_cedula_only():
    report = _pair_scenario([("2000000001", "Ana Gomez Ruiz")], [("2000000011", "Ana Gomez Ruiz")])
    pair = report["register"]["D-N-COLAPSADOS"]["same_person_different_survivor"][0]
    assert set(pair) == {"backend", "notebook"}


def test_an_unpaired_residual_still_counts_toward_the_unexplained_delta_and_keeps_its_reason():
    report = _pair_scenario([("2000000001", "Ana Gomez Ruiz"), ("2000000002", "Solo Backend Uno")],
                            [("2000000011", "Ana Gomez Ruiz")])
    n = report["register"]["D-N-COLAPSADOS"]
    assert _pairs(report) == [("2000000001", "2000000011")]
    assert n["residual_only_backend"] == ["2000000002"] and n["residual_only_notebook"] == []
    assert list(n["residual_reasons"]) == ["2000000002"]
    assert n["residual_reasons"]["2000000002"] == "absent from the reference xlsx (backend motivo cuenta_no_persona)"
    assert n["unexplained_delta"] == 1  # the pair cancels; the leftover row is the whole delta
    assert any("n_colapsados" in f for f in report["failures"])


def test_names_are_paired_through_the_engines_own_normalization_accents_case_and_spaces():
    report = _pair_scenario([("2000000001", "José  Ñandú   Pérez")], [("2000000011", "  jose nandu PEREZ ")])
    assert _pairs(report) == [("2000000001", "2000000011")]


@pytest.mark.parametrize("backend_name,notebook_name", [
    ("", ""), ("   ", "   "), (None, None), ("", "Ana Gomez Ruiz"), ("Ana Gomez Ruiz", ""), ("Ana Gomez Ruiz", None),
])
def test_empty_or_missing_names_never_pair(backend_name, notebook_name):
    report = _pair_scenario([("2000000001", backend_name)], [("2000000011", notebook_name)])
    n = report["register"]["D-N-COLAPSADOS"]
    assert n["same_person_different_survivor"] == []
    assert n["residual_only_backend"] == ["2000000001"] and n["residual_only_notebook"] == ["2000000011"]


def test_a_notebook_externos_sheet_without_a_name_column_never_pairs_and_never_crashes():
    depurado = _hand_depurado([_hand("3000000001")], {"identidad_key": "GRUPO-EXTERNOS", "n_colapsados": 1, "detalle": (
        {"identificacion": "2000000001", "nombre_completo": "Ana Gomez Ruiz", "motivo": "cuenta_no_persona",
         "ultimo_sticker": None},)})
    report = parity.compare(depurado, [_hand_xlsx("3000000001")], parity.build_register(_inputs(), depurado),
                            externos_rows=[{"identificacion": "2000000011"}], n_colapsados_notebook=1)
    assert report["register"]["D-N-COLAPSADOS"]["same_person_different_survivor"] == []
    assert report["register"]["D-N-COLAPSADOS"]["residual_only_notebook"] == ["2000000011"]


def test_more_backend_residuals_than_notebook_residuals_in_one_group_pair_only_the_minimum():
    same = "Ana Gomez Ruiz"
    report = _pair_scenario([("2000000001", same), ("2000000002", same), ("2000000003", same)], [("2000000011", same)])
    n = report["register"]["D-N-COLAPSADOS"]
    assert _pairs(report) == [("2000000001", "2000000011")]  # the lowest keys pair, deterministically
    assert n["residual_only_backend"] == ["2000000002", "2000000003"] and n["residual_only_notebook"] == []
    assert n["unexplained_delta"] == 2


def test_more_notebook_residuals_than_backend_residuals_in_one_group_pair_only_the_minimum():
    same = "Ana Gomez Ruiz"
    report = _pair_scenario([("2000000001", same)], [("2000000011", same), ("2000000012", same), ("2000000013", same)])
    n = report["register"]["D-N-COLAPSADOS"]
    assert _pairs(report) == [("2000000001", "2000000011")]
    assert n["residual_only_notebook"] == ["2000000012", "2000000013"] and n["residual_only_backend"] == []
    assert n["unexplained_delta"] == -2


def test_names_that_differ_by_a_letter_are_different_people_and_never_cross_pair():
    report = _pair_scenario([("2000000001", "Ana Gomez Ruiz")], [("2000000011", "Ana Gomes Ruiz")])
    n = report["register"]["D-N-COLAPSADOS"]
    assert n["same_person_different_survivor"] == []
    assert n["residual_only_backend"] == ["2000000001"] and n["residual_only_notebook"] == ["2000000011"]


def test_pairs_never_cross_name_groups_even_when_the_counts_would_balance():
    report = _pair_scenario([("2000000001", "Ana Gomez Ruiz"), ("2000000002", "Beto Mora Sol")],
                            [("2000000011", "Beto Mora Sol"), ("2000000012", "Ana Gomez Ruiz")])
    assert _pairs(report) == [("2000000001", "2000000012"), ("2000000002", "2000000011")]


def test_pairing_does_not_depend_on_the_order_of_the_inputs():
    backend = [("2000000001", "Ana Gomez Ruiz"), ("2000000002", "Ana Gomez Ruiz"), ("2000000003", "Beto Mora Sol")]
    notebook = [("2000000012", "Ana Gomez Ruiz"), ("2000000011", "Ana Gomez Ruiz"), ("2000000013", "Beto Mora Sol")]
    a = _pair_scenario(backend, notebook)
    b = _pair_scenario(list(reversed(backend)), list(reversed(notebook)))
    assert _pairs(a) == _pairs(b) == [("2000000001", "2000000011"), ("2000000002", "2000000012"),
                                      ("2000000003", "2000000013")]


def test_the_report_prints_the_pairs_as_masked_keys_only():
    report = _pair_scenario([("2000000001", "Ana Gomez Ruiz")], [("2000000011", "Ana Gomez Ruiz")])
    text = parity.format_parity_report(report)
    assert "same_person_different_survivor: 1" in text and "***001 <-> ***011" in text
    assert "D-TIEBREAK" in text and "D-P2" in text  # the category names its cause
    assert "Ana Gomez" not in text and "2000000001" not in text and "2000000011" not in text


def test_no_pairs_prints_an_explicit_zero_not_a_missing_line():
    text = parity.format_parity_report(_residual_scenario())
    assert "same_person_different_survivor: 0 []" in text


def test_worst_case_probe_reads_use_each_collections_own_size_and_are_reported_apart():
    # survey 1,000 entries -> count 1 read + newest 1 = 2 per probe; evaluaciones 2,500 -> count 3 + newest 1 = 4 per probe
    projection = parity.project_reads(parity.simulate_static_hour(), parity.Sizes(E=2500, I=10, S=1000), open_hours_per_day=1)
    assert projection["worst_case_probe_reads_per_hour"] == 1 * 2 + 4 * 4 == 18
    assert projection["worst_case_scan_reads_per_hour"] == 2 * 10 + 1 * 1000 + 4 * 2500 == 11_020
    assert projection["worst_case_reads_per_hour"] == 11_020 + 18
    assert "1 survey probes x 2 + 4 evaluaciones probes x 4" in projection["arithmetic_worst_case"]


def test_the_pairing_function_is_deterministic_for_unsorted_input_and_min_pairs_lowest_keys_first():
    names = {"b2": "Ana Gomez", "b1": "ana  gómez", "n2": "ANA GOMEZ", "n1": "Ana Gomez", "n3": "Ana Gomez"}
    pairs = parity._pair_residuals(["b2", "b1"], ["n3", "n2", "n1"], names, names)
    assert pairs == [{"backend": "b1", "notebook": "n1"}, {"backend": "b2", "notebook": "n2"}]  # n3 stays unpaired
    assert parity._pair_residuals([], [], {}, {}) == []


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# Live parity run 2026-09-19 (second pass): the `codigo` replay, the roster-independent D-REMAP tag, the Fase 2
# collision classifier and the registered review signals. Fake identities only.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _replay(main=(), vercel=(), fase2=(), **kw):
    return parity.notebook_codigo(_bundle(main=main, vercel=vercel, fase2=fase2), **kw)


def _by_key(replay):
    return dict(zip(replay.keys, replay.codes))


def _counts(replay):
    return (replay.aplicados, replay.omitidos, replay.sin_duenio, replay.conflicto)


# ── notebook_codigo: the notebook's remap loop (cells 29, 53, 67), replayed WITH mutation in row order ──────────────

def test_notebook_codigo_a_different_person_titular_loses_the_code_and_the_vercel_owner_gets_it():
    r = _replay(main=(_entrada("111", "Ana Uno", codigo="041"), _entrada("222", "Beto Dos")),
                vercel=(_entrada("222", "Beto Dos", codigo="041"),))
    assert _by_key(r) == {"111": "", "222": "041"} and _counts(r) == (1, 0, 0, 0)


def test_notebook_codigo_the_titular_who_is_the_same_person_is_left_alone_by_cedula_fase2_cedula_or_name():
    by_cedula = _replay(main=(_entrada("222", "Beto Dos", codigo="041"),), vercel=(_entrada("222", "Beto Dos", codigo="041"),))
    assert _by_key(by_cedula) == {"222": "041"} and _counts(by_cedula) == (0, 0, 0, 0)
    # the main row matched Fase 2 by name, whose cedula is the Vercel owner's: same person (`titular.cedula_fase2`)
    by_fase2 = _replay(main=(_entrada("111", "Ana Uno", codigo="041"),),
                       fase2=(_entrada("222", "Ana Uno"),), vercel=(_entrada("222", "Otro Nombre", codigo="041"),))
    assert _by_key(by_fase2) == {"111": "041"}
    by_name = _replay(main=(_entrada("111", "Ana Maria Uno", codigo="041"),),
                      vercel=(_entrada("999", "uno ana maria", codigo="041"),))  # token_sort_ratio 100 >= 90
    assert _by_key(by_name) == {"111": "041"} and _counts(by_name) == (0, 0, 0, 0)


def test_notebook_codigo_the_name_cutoff_is_inclusive_at_exactly_ninety():
    from rapidfuzz import fuzz

    assert fuzz.token_sort_ratio("abcdefghij", "abcdefghix") == 90.0 and fuzz.token_sort_ratio("abcdefghijklmnopqrs", "abcdefghijklmnopqxy") < 90
    at = _replay(main=(_entrada("111", "abcdefghij", codigo="041"),), vercel=(_entrada("999", "abcdefghix", codigo="041"),))
    assert at.codes == ("041",)  # 90.0: the same person, the titular is untouched
    below = _replay(main=(_entrada("111", "abcdefghijklmnopqrs", codigo="041"),),
                    vercel=(_entrada("999", "abcdefghijklmnopqxy", codigo="041"),))
    assert below.codes == ("",)  # 89.47: a different person


def test_notebook_codigo_a_name_below_ninety_is_a_different_person():
    r = _replay(main=(_entrada("111", "Ana Maria Uno", codigo="041"),), vercel=(_entrada("999", "Carlos Otro Diaz", codigo="041"),))
    assert _by_key(r) == {"111": ""} and _counts(r) == (0, 0, 1, 0)  # cleared, and the owner has no row: sin_duenio


def test_notebook_codigo_a_code_nobody_holds_in_main_is_not_a_remap_row_and_the_owner_keeps_its_own():
    r = _replay(main=(_entrada("111", "Ana Uno", codigo="059"),), vercel=(_entrada("111", "Ana Uno", codigo="146"),))
    assert _by_key(r) == {"111": "059"} and _counts(r) == (0, 0, 0, 0)  # the notebook never assigns 146


def test_notebook_codigo_owner_absent_clears_the_titular_and_counts_sin_duenio():
    r = _replay(main=(_entrada("111", "Ana Uno", codigo="041"),), vercel=(_entrada("999", "Nadie Parecido Aqui", codigo="041"),))
    assert _by_key(r) == {"111": ""} and _counts(r) == (0, 0, 1, 0)


def test_notebook_codigo_a_titular_already_cleared_by_an_earlier_row_is_a_conflict_not_a_second_clearing():
    main = (_entrada("111", "Ana Uno", codigo="041"), _entrada("222", "Beto Dos"), _entrada("333", "Carla Tres"))
    vercel = (_entrada("222", "Beto Dos", codigo="041"), _entrada("333", "Carla Tres", codigo="041"))
    r = _replay(main=main, vercel=vercel)
    assert _by_key(r) == {"111": "", "222": "041", "333": ""} and _counts(r) == (1, 0, 0, 1)  # the second row finds it gone


def test_notebook_codigo_an_excluded_code_is_omitted_entirely_and_its_holder_keeps_it():
    main = (_entrada("660", "Beto Otro", codigo="148"), _entrada("486", "Ivan Ejemplo"))
    vercel = (_entrada("486", "Ivan Ejemplo", codigo="148"), _entrada("487", "Ivan Ejemplo", codigo="097"))
    assert parity.notebook_dup_codes(_bundle(vercel=vercel)) == frozenset({"148", "097"})
    r = _replay(main=main, vercel=vercel)
    assert _by_key(r) == {"660": "148", "486": ""} and _counts(r) == (0, 1, 0, 0)
    assert _by_key(_replay(main=main, vercel=vercel, excluded=frozenset())) == {"660": "", "486": "148"}  # not excluded: remapped


def test_notebook_dup_codes_follows_the_publisher_rule_names_only_and_empty_names_never_pair():
    v = (_entrada("1", "Adan Duran", codigo="097"), _entrada("2", "adán  DURÁN", codigo="127"),
         _entrada("3", "", codigo="010"), _entrada("4", "", codigo="011"), _entrada("5", "Solo Uno", codigo="012"),
         _entrada("6", "Adan Duran", codigo=""))
    assert parity.notebook_dup_codes(_bundle(vercel=v)) == frozenset({"097", "127"})
    assert parity.notebook_dup_codes(_bundle()) == frozenset()


def test_notebook_codigo_the_owner_index_is_first_row_wins_not_last_row_wins():
    holder = _entrada("333", "Carla Tres", codigo="050")
    vercel = (_entrada("111", "Otra Persona", codigo="050"),)
    first, second = _entrada("111", "Otra Persona"), _entrada("111", "Otra Persona Copia")  # duplicate cedulas
    assert _replay(main=(holder, first, second), vercel=vercel).codes == ("", "050", "")  # the first row gets the code
    assert _replay(main=(holder, second, first), vercel=vercel).codes == ("", "050", "")  # in either order: the FIRST one


def test_notebook_codigo_a_later_rows_own_cedula_does_not_beat_an_earlier_rows_fase2_claim():
    main = (_entrada("222", "Beto Dos"), _entrada("111", "Otra Persona"), _entrada("333", "Carla Tres", codigo="050"))
    fase2 = (_entrada("111", "Beto Dos"),)  # row 0 matches Fase 2 by name: its cedula_fase2 is 111
    vercel = (_entrada("111", "Otra Persona", codigo="050"),)
    assert _replay(main=main, fase2=fase2, vercel=vercel).codes == ("050", "", "")  # the notebook's cedula_idx quirk
    assert _replay(main=main, fase2=fase2, vercel=vercel, rules=frozenset({"D-OWNERFASE2"})).codes == ("", "050", "")


def test_notebook_codigo_leading_zeros_are_different_codes():
    r = _replay(main=(_entrada("111", "Ana Uno", codigo="021"), _entrada("222", "Beto Dos")),
                vercel=(_entrada("222", "Beto Dos", codigo="21"),))
    assert _by_key(r) == {"111": "021", "222": ""} and _counts(r) == (0, 0, 0, 0)  # "21" has no titular: "021" is another code


def test_notebook_codigo_none_blank_and_whitespace_codes_are_no_codes():
    main = (_entrada("111", "Ana Uno", codigo=None), _entrada("222", "Beto Dos", codigo="   "), _entrada("333", "Carla Tres", codigo=" 041 "))
    vercel = (_entrada("111", "Ana Uno", codigo=None), _entrada("222", "Beto Dos", codigo=" "), _entrada("444", "Dario Cuatro", codigo=" 041 "))
    r = _replay(main=main, vercel=vercel)
    assert _by_key(r) == {"111": "", "222": "", "333": ""} and _counts(r) == (0, 0, 1, 0)  # the padded 041 is trimmed: a real remap


def test_notebook_codigo_empty_and_degenerate_inputs():
    empty = _replay()
    assert (empty.codes, empty.keys) == ((), ()) and _counts(empty) == (0, 0, 0, 0)
    assert _replay(main=(_entrada("111", "Ana Uno", codigo="041"),)).codes == ("041",)  # no Vercel: nothing to remap
    assert _replay(vercel=(_entrada("111", "Ana Uno", codigo="041"),)).codes == ()  # no main: no titular
    r = _replay(main=(_entrada("111", "Ana Uno", codigo="041"),), vercel=(_entrada("111", "Ana Uno", codigo="041"),) * 2)
    assert r.codes == ("041",)  # a repeated identical row is a name duplicate: omitted, untouched


def test_notebook_codigo_a_replay_is_pure_it_never_mutates_the_bundle_and_is_repeatable():
    ref = _bundle(main=(_entrada("111", "Ana Uno", codigo="041"), _entrada("222", "Beto Dos")),
                  vercel=(_entrada("222", "Beto Dos", codigo="041"),))
    before = copy.deepcopy(ref)
    assert parity.notebook_codigo(ref) == parity.notebook_codigo(ref)
    assert ref == before


def test_notebook_codigo_the_empty_names_quirk_two_blank_names_are_the_same_person_in_the_notebook():
    main = (_entrada("111", "", codigo="041"), _entrada("222", ""))
    vercel = (_entrada("222", "", codigo="041"),)
    assert _by_key(_replay(main=main, vercel=vercel)) == {"111": "041", "222": ""}  # rapidfuzz scores "" vs "" as 100
    guarded = _replay(main=main, vercel=vercel, rules=frozenset({"D-MISMAPERSONA"}))
    assert _by_key(guarded) == {"111": "", "222": "041"}  # the engine never matches an empty name


def test_notebook_codigo_huge_inputs_stay_fast():
    import time

    n = 20_000
    main = tuple(_entrada(str(1_000_000 + i), f"Persona Numero {i}", codigo=f"{i:05d}") for i in range(n))
    vercel = tuple(_entrada(str(1_000_000 + (i + 1) % n), f"Persona Numero {(i + 1) % n}", codigo=f"{i:05d}") for i in range(n))
    started = time.perf_counter()
    r = _replay(main=main, vercel=vercel, rules=frozenset(parity.CODIGO_RULES))
    assert time.perf_counter() - started < 5.0 and len(r.codes) == n


# ── the `codigo` cause: replay reproduces the xlsx AND exactly one registered rule reproduces the backend ────────────

def _codigo_ctx(notebook, rules, perdido=()):
    return {"context": {"codigo_replay": {"notebook": notebook, "rules": rules}}, "D-CODIGOPERDIDO": list(perdido)}


def test_codigo_cause_needs_the_replay_to_reproduce_the_xlsx_first():
    ctx = _codigo_ctx({"k": "041"}, {"D-REMAP": {"k": ""}})
    assert parity._codigo_cause("k", "", "041", ctx) == "D-REMAP"
    assert parity._codigo_cause("k", "", "099", ctx) is None  # the notebook replay says 041, the xlsx says 099: not this
    assert parity._codigo_cause("k", "", "  041 ", ctx) == "D-REMAP"  # trimmed
    assert parity._codigo_cause("k", "", "41", ctx) == "D-REMAP"  # padding-only is the same code


def test_codigo_cause_two_rules_reproducing_the_backend_is_ambiguous_and_stays_unexplained():
    ctx = _codigo_ctx({"k": "041"}, {"D-REMAP": {"k": ""}, "D-REMAP-TODOS": {"k": ""}, "D-OWNERFASE2": {"k": "041"}})
    assert parity._codigo_cause("k", "", "041", ctx) is None


def test_codigo_cause_no_rule_reproducing_the_backend_is_unexplained():
    ctx = _codigo_ctx({"k": "041"}, {"D-REMAP": {"k": "050"}, "D-REMAP-TODOS": {"k": "041"}})
    assert parity._codigo_cause("k", "", "041", ctx) is None


def test_codigo_cause_unknown_key_or_missing_replay_is_never_tagged():
    assert parity._codigo_cause("zz", "", "041", _codigo_ctx({"k": "041"}, {"D-REMAP": {"zz": ""}})) is None
    assert parity._codigo_cause("k", "", "041", {"context": {}, "D-CODIGOPERDIDO": []}) is None  # a hand-made register


def test_codigo_cause_d_codigoperdido_is_item_based_and_counts_as_one_rule():
    ctx = _codigo_ctx({"k": "041"}, {"D-REMAP": {"k": "041"}}, perdido=["k"])
    assert parity._codigo_cause("k", "", "041", ctx) == "D-CODIGOPERDIDO"
    both = _codigo_ctx({"k": "041"}, {"D-REMAP": {"k": ""}}, perdido=["k"])
    assert parity._codigo_cause("k", "", "041", both) is None  # two rules: ambiguous


def test_codigo_rules_are_the_registered_names():
    assert set(parity.CODIGO_RULES) == {"D-REMAP", "D-REMAP-TODOS", "D-MISMAPERSONA", "D-OWNERFASE2"}
    assert set(parity.CODIGO_RULES) <= set(parity.REGISTER_NAMES) and "D-CODIGOPERDIDO" in parity.REGISTER_NAMES


# ── compare level: hand-made engine rows against a replay of the inputs ────────────────────────────────────────────

def _hand_compare_inputs(inputs, backend_rows, xlsx_rows, **kw):
    depurado = _hand_depurado(backend_rows)
    return parity.compare(depurado, xlsx_rows, parity.build_register(inputs, depurado), **kw)


def _codigo_mismatches(report):
    return {m["key"]: (m["backend"], m["xlsx"], m["explained"]) for m in report["columns"]["codigo"]["mismatches"]}


def test_d_mismapersona_empty_names_engine_guard_is_attributed_by_replay():
    inputs = _inputs(main=(_entrada("1000000010", "", codigo="041"), _entrada("1000000011", "")),
                     vercel=(_entrada("1000000011", "", codigo="041"),))
    backend = [_hand("1000000010", codigo=""), _hand("1000000011", codigo="041")]
    xlsx = [_hand_xlsx("1000000010", codigo="041"), _hand_xlsx("1000000011", codigo="")]
    report = _hand_compare_inputs(inputs, backend, xlsx)
    assert _codigo_mismatches(report) == {"1000000010": ("", "041", "D-MISMAPERSONA"),
                                          "1000000011": ("041", "", "D-MISMAPERSONA")}
    assert report["register"]["D-MISMAPERSONA"] == ["1000000010", "1000000011"]


def test_d_ownerfase2_own_cedula_beats_an_earlier_fase2_claim_is_attributed_by_replay():
    inputs = _inputs(
        main=(_entrada("1000000222", "Beto Dos"), _entrada("1000000111", "Otra Persona"),
              _entrada("1000000333", "Carla Tres", codigo="050")),
        fase2=(_entrada("1000000111", "Beto Dos"),),
        vercel=(_entrada("1000000111", "Otra Persona", codigo="050"),),
    )
    backend = [_hand("1000000222", codigo=""), _hand("1000000111", codigo="050"), _hand("1000000333", codigo="")]
    xlsx = [_hand_xlsx("1000000222", codigo="050"), _hand_xlsx("1000000111", codigo=""), _hand_xlsx("1000000333", codigo="")]
    report = _hand_compare_inputs(inputs, backend, xlsx)
    assert _codigo_mismatches(report) == {"1000000222": ("", "050", "D-OWNERFASE2"), "1000000111": ("050", "", "D-OWNERFASE2")}
    assert report["register"]["D-OWNERFASE2"] == ["1000000111", "1000000222"]


def test_d_remap_todos_every_different_person_holder_is_cleared_engine_side_only():
    inputs = _inputs(
        main=(_entrada("1000000001", "Ana Uno", codigo="041"), _entrada("1000000002", "Beto Dos", codigo="041"),
              _entrada("1000000003", "Carla Tres", codigo="041")),
        vercel=(_entrada("1000000001", "Ana Uno", codigo="041"),),
    )
    depurado = inputs.run()
    assert {r["identidad_key"]: r["codigo"] for r in depurado.inspectores} == {"1000000001": "041", "1000000002": "", "1000000003": ""}
    xlsx = _xlsx_rows(depurado)
    _row(xlsx, "1000000002")["codigo"] = "041"  # the notebook clears only the recorded titular (the first holder)
    _row(xlsx, "1000000003")["codigo"] = "041"
    report = _run(inputs, xlsx)
    assert _codigo_mismatches(report) == {"1000000002": ("", "041", "D-REMAP-TODOS"), "1000000003": ("", "041", "D-REMAP-TODOS")}
    assert report["register"]["D-REMAP-TODOS"] == ["1000000002", "1000000003"]


def test_a_codigo_mismatch_the_replay_cannot_reproduce_is_unexplained_even_with_a_matching_rule():
    inputs = _inputs(main=(_entrada("1000000010", "", codigo="041"), _entrada("1000000011", "")),
                     vercel=(_entrada("1000000011", "", codigo="041"),))
    backend = [_hand("1000000010", codigo=""), _hand("1000000011", codigo="041")]
    xlsx = [_hand_xlsx("1000000010", codigo="077"), _hand_xlsx("1000000011", codigo="")]  # 077: drift the replay never produces
    mismatches = _codigo_mismatches(_hand_compare_inputs(inputs, backend, xlsx))
    assert mismatches["1000000010"] == ("", "077", None) and mismatches["1000000011"][2] == "D-MISMAPERSONA"


def test_a_codigo_mismatch_without_any_main_row_is_unexplained():
    inputs = _inputs(main=(_entrada("1000000099", "Otra Persona"),))
    report = _hand_compare_inputs(inputs, [_hand("1000000001", codigo="041")], [_hand_xlsx("1000000001", codigo="052")])
    assert _codigo_mismatches(report) == {"1000000001": ("041", "052", None)}


# ── D-REMAP is roster-independent (the live ***145) ─────────────────────────────────────────────────────────────────

def _remap_roster_inputs(roster_codigo="146"):
    """P holds 059 in `main`, and Vercel gives 059 to Q (so the notebook clears it from P) and 146 to P (a code NOBODY
    holds in main, so the notebook never assigns it). The production roster is already remapped: it carries 146 on P."""
    p, q = "1000000145", "1000000200"
    return _inputs(
        roster={"p": _roster(p, "Pablo Ejemplo Ruiz", roster_codigo)},
        main=(_entrada(p, "Pablo Ejemplo Ruiz", codigo="059"), _entrada(q, "Quirino Otro Vega")),
        vercel=(_entrada(q, "Quirino Otro Vega", codigo="059"), _entrada(p, "Pablo Ejemplo Ruiz", codigo="146")),
    )


def _remap_roster_xlsx(inputs):
    xlsx = _xlsx_rows(inputs.run())
    _row(xlsx, "1000000145")["codigo"] = ""  # the notebook: 059 cleared, 146 never assigned
    _row(xlsx, "1000000200")["codigo"] = "059"
    return xlsx


def test_d_remap_is_tagged_even_when_the_production_roster_already_carries_the_new_codigo():
    inputs = _remap_roster_inputs("146")
    depurado = inputs.run()
    # the engine sees anterior == nuevo (roster-fed 146): it emits NO `codigo_reemplazado` item...
    assert not [i for i in depurado.revision_manual if i["motivo"] == "codigo_reemplazado"]
    report = _run(inputs, _remap_roster_xlsx(inputs))
    # ...but the tag compares against the bundle's own codigo (059), so the registered divergence is not hidden
    assert _codigo_mismatches(report) == {"1000000145": ("146", "", "D-REMAP")}
    assert report["register"]["D-REMAP"] == ["1000000145"]
    codigo = report["columns"]["codigo"]
    assert codigo["n"] - codigo["n_effective"] == 1 and codigo["passed"] is True  # explained rows leave the gated denominator
    assert "codigo" not in " ".join(report["failures"])


def test_d_remap_without_a_roster_is_the_same_tag_the_item_and_the_replay_agree():
    inputs = dataclasses.replace(_remap_roster_inputs(""), roster_by_cedula={})
    depurado = inputs.run()
    assert [i["motivo"] for i in depurado.revision_manual if i["motivo"] == "codigo_reemplazado"] == ["codigo_reemplazado"]
    report = _run(inputs, _remap_roster_xlsx(inputs))
    assert _codigo_mismatches(report) == {"1000000145": ("146", "", "D-REMAP")}
    assert report["register"]["D-REMAP"] == ["1000000145"]  # once, never duplicated by the two sources


# ── the ***486 / ***660 shape: the publisher fix and the harness agree ──────────────────────────────────────────────

def _dup_inputs(codigos_duplicados):
    return _inputs(
        main=(_entrada("1000000660", "Beto Otro Distinto", codigo="148"), _entrada("1000000486", "Ivan Ejemplo Rojas")),
        vercel=(_entrada("1000000486", "Ivan Ejemplo Rojas", codigo="148"), _entrada("1000000487", "Ivan Ejemplo Rojas", codigo="097")),
        codigos_duplicados=codigos_duplicados,
    )


def test_a_republished_bundle_listing_the_name_duplicate_codes_has_no_codigo_mismatch():
    inputs = _dup_inputs(("097", "148"))
    notebook = _xlsx_rows(inputs.run())  # skipping the remap of the excluded codes is exactly what the notebook did
    assert {r["identificacion"]: r["codigo"] for r in notebook if r["id"] != "GRUPO-EXTERNOS"} == {
        "1000000660": "148", "1000000486": ""}
    report = _run(inputs, notebook)
    assert report["columns"]["codigo"]["mismatches"] == [] and report["columns"]["codigo"]["passed"] is True


def test_a_stale_bundle_without_the_list_leaves_the_codigo_mismatches_unexplained_and_failing():
    notebook = _xlsx_rows(_dup_inputs(("097", "148")).run())
    report = _run(_dup_inputs(()), notebook)  # the engine remaps 148 (clears the holder, assigns the owner): the live divergence
    assert _codigo_mismatches(report) == {"1000000660": ("", "148", None), "1000000486": ("148", "", None)}
    assert report["columns"]["codigo"]["passed"] is False and any(f.startswith("codigo below") for f in report["failures"])


# ── explained codigo rows stay listed and the report prints the cause ───────────────────────────────────────────────

def test_the_report_prints_the_codigo_cause_per_key_masked():
    inputs = _remap_roster_inputs("146")
    text = parity.format_parity_report(_run(inputs, _remap_roster_xlsx(inputs)))
    assert "***145: backend='146' xlsx='' [D-REMAP]" in text
    assert "D-REMAP: 1 [***145]" in text and "1000000145" not in text and "Pablo" not in text


# ── residual classifier: a blocked Fase 2 fix is looked up under the Fase 2 cedula (the live ***715 / ***957) ───────

F2_CEDULA = "1000000957"


def _colision_inputs():
    """Two externos-looking profiles name-match Fase 2 rows that carry the same cedula: the D18 fix is blocked for both
    (`fase2_cedula_colision`). One is corroborated by survey_cali (D-EXENTOS survivor), the other collapses."""
    return _inputs(
        main=(_entrada("12345", "Externo Uno Prueba"), _entrada("12346", "Externo Dos Prueba"),
              _entrada("1000000001", "Persona Normal Ejemplo", "P1")),
        fase2=(_entrada(F2_CEDULA, "Externo Uno Prueba"), _entrada(F2_CEDULA, "Externo Dos Prueba")),
        survey=["Externo Uno Prueba"],
    )


def _colision_report():
    inputs = _colision_inputs()
    depurado = inputs.run()
    xlsx = [r for r in _xlsx_rows(depurado) if parity.normalize_key(r["identificacion"]) not in ("12345", "12346")]
    externos = [{"identificacion": F2_CEDULA}, {"identificacion": F2_CEDULA}]  # both rows collapsed in the notebook
    return depurado, parity.compare(depurado, xlsx, parity.build_register(inputs, depurado), externos_rows=externos,
                                    n_colapsados_notebook=2)


def test_the_colision_scenario_is_the_shape_of_the_live_run_engine_side():
    depurado, _ = _colision_report()
    assert sorted(i["identidad_key"] for i in depurado.revision_manual if i["motivo"] == "fase2_cedula_colision") == ["12345", "12346"]
    assert "12345" in {r["identidad_key"] for r in depurado.inspectores}  # the exempt survivor stays a row
    assert [d["identificacion"] for d in depurado.grupo_externos["detalle"]] == ["12346"]  # the other one collapsed


def test_a_survivor_whose_fase2_fix_was_blocked_is_not_called_exento_post_snapshot():
    _, report = _colision_report()
    assert "12345" in report["register"]["D-EXENTOS"]
    assert report["extras"]["exento_post_snapshot"] == []  # the notebook has it: under its Fase 2 cedula
    assert report["extras"]["fase2_colision"] == ["12345"]
    assert report["extras"]["unexplained"] == []
    assert "row(s) beyond the enumerated extras" not in " ".join(report["failures"])


def test_a_collapsed_row_under_the_fase2_cedula_is_paired_with_its_blocked_backend_key_not_a_residual():
    _, report = _colision_report()
    n = report["register"]["D-N-COLAPSADOS"]
    assert (n["backend"], n["notebook"], n["expected"], n["unexplained_delta"]) == (1, 2, 1, 0)
    assert n["residual_only_backend"] == [] and n["residual_only_notebook"] == []
    assert {(p["backend"], p["notebook"]) for p in n["fase2_colision"]} == {("12345", F2_CEDULA), ("12346", F2_CEDULA)}
    assert not any("n_colapsados" in f for f in report["failures"])


def test_the_colision_rows_are_listed_under_the_named_category_in_the_report_masked():
    _, report = _colision_report()
    text = parity.format_parity_report(report)
    assert "D-FASE2-COLISION" in text and "fase2_colision: 1 [***345]" in text
    assert "***345 <-> ***957" in text and "***346 <-> ***957" in text
    assert F2_CEDULA not in text and "12345" not in text and "Externo" not in text


def test_a_blocked_fix_whose_fase2_cedula_the_notebook_does_not_have_stays_post_snapshot():
    inputs = _colision_inputs()
    depurado = inputs.run()
    xlsx = [r for r in _xlsx_rows(depurado) if parity.normalize_key(r["identificacion"]) not in ("12345", "12346")]
    report = parity.compare(depurado, xlsx, parity.build_register(inputs, depurado), externos_rows=[{"identificacion": "77777"}],
                            n_colapsados_notebook=1)
    assert report["extras"]["exento_post_snapshot"] == ["12345"] and report["extras"]["fase2_colision"] == []


def test_a_row_without_a_colision_item_is_classified_exactly_as_before():
    inputs = _register_inputs()
    rows, externos = _notebook_xlsx(inputs)
    report = _run(inputs, rows, externos_rows=externos)
    assert report["extras"]["fase2_colision"] == [] and report["only_in_xlsx_detail"]["fase2_colision"] == []
    assert report["register"]["D-N-COLAPSADOS"]["fase2_colision"] == []


def _colision_item(key, fase2, existente=""):
    return {"motivo": "fase2_cedula_colision", "cedula_key": fase2, "identidad_key": key,
            "identidad_key_existente": existente, "nombre_completo": "Fake Person"}


def test_an_active_profile_with_a_blocked_fix_and_a_notebook_row_under_the_fase2_cedula_is_paired_not_missing():
    backend = [_hand("2000000001"), _hand("3000000005")]
    depurado = dataclasses.replace(_hand_depurado(backend), revision_manual=(_colision_item("3000000005", "2000000099"),))
    xlsx = [_hand_xlsx("2000000001"), _hand_xlsx("2000000099")]
    report = parity.compare(depurado, xlsx, parity.build_register(_inputs(), depurado))
    assert report["extras"]["fase2_colision"] == ["3000000005"] and report["extras"]["unexplained"] == []
    assert report["only_in_xlsx_detail"]["fase2_colision"] == ["2000000099"] and report["only_in_xlsx_detail"]["missing"] == []
    assert report["accounting"]["fase2_colision"] == 1 and report["accounting"]["balanced"] is True
    assert not [f for f in report["failures"] if "row(s)" in f]  # (the contact columns of a hand-made row are empty: not this test)


def test_a_blocked_key_that_the_notebook_does_not_have_under_either_cedula_is_still_unexplained():
    backend = [_hand("2000000001"), _hand("3000000005")]
    depurado = dataclasses.replace(_hand_depurado(backend), revision_manual=(_colision_item("3000000005", "2000000099"),))
    report = parity.compare(depurado, [_hand_xlsx("2000000001")], parity.build_register(_inputs(), depurado))
    assert report["extras"]["unexplained"] == ["3000000005"] and report["extras"]["fase2_colision"] == []
    assert any("beyond the enumerated extras" in f for f in report["failures"])


def test_the_colision_lookup_never_swallows_an_unrelated_missing_row():
    backend = [_hand("2000000001"), _hand("3000000005")]
    depurado = dataclasses.replace(_hand_depurado(backend), revision_manual=(_colision_item("3000000005", "2000000099"),))
    xlsx = [_hand_xlsx("2000000001"), _hand_xlsx("2000000099"), _hand_xlsx("2000000055")]
    report = parity.compare(depurado, xlsx, parity.build_register(_inputs(), depurado))
    assert report["only_in_xlsx_detail"]["missing"] == ["2000000055"]
    assert any("missing from the backend" in f for f in report["failures"])


# ── the two review signals have register names (no more "motivos outside the register") ─────────────────────────────

def test_fase2_cedula_colision_and_codigo_remap_candidato_are_registered_review_signals():
    assert parity.REVISION_REGISTER["fase2_cedula_colision"] == "D-FASE2-COLISION"
    assert parity.REVISION_REGISTER["codigo_remap_candidato"] == "D-REMAP-CANDIDATO"
    assert {"D-FASE2-COLISION", "D-REMAP-CANDIDATO", "D-MISMAPERSONA", "D-OWNERFASE2"} <= set(parity.REGISTER_NAMES)


def test_the_colision_items_are_listed_by_key_and_are_not_reported_as_unregistered():
    inputs = _colision_inputs()
    register = parity.build_register(inputs, inputs.run())
    assert {"12345", "12346"} <= set(register["D-FASE2-COLISION"])
    assert register["revision_no_registrados"] == {}
    assert "motivos outside the register" not in parity.format_parity_report(_colision_report()[1])


def test_a_remap_candidate_item_is_listed_under_its_name_and_does_not_explain_a_codigo_mismatch_by_itself():
    # Vercel row 097 whose owner has no profile and nobody holds it: the fuzzy candidate is only a review signal
    inputs = _inputs(main=(_entrada("1000000001", "Ivan Ejemplo Rojas"),),
                     vercel=(_entrada("1000000009", "Ivan Ejemplo Rojas", codigo="097"),))
    depurado = inputs.run()
    assert [i["motivo"] for i in depurado.revision_manual] == ["codigo_remap_candidato"]
    register = parity.build_register(inputs, depurado)
    assert register["D-REMAP-CANDIDATO"] == ["1000000001"] and register["revision_no_registrados"] == {}
    xlsx = _xlsx_rows(depurado)
    _row(xlsx, "1000000001")["codigo"] = "097"  # a codigo mismatch on the very key the item names
    report = _run(inputs, xlsx)
    assert _codigo_mismatches(report) == {"1000000001": ("", "097", None)}  # a signal is not a divergence: still unexplained


# ── mutation follow-ups: the paths the first pass of mutants left alive ─────────────────────────────────────────────

def test_the_register_lists_the_roster_independent_d_remap_key_without_running_compare():
    inputs = _remap_roster_inputs("146")  # the roster already carries 146: the roster-fed pass emits no item
    register = parity.build_register(inputs, inputs.run())
    assert register["D-REMAP"] == ["1000000145"]


def test_codigo_cause_padding_only_on_the_backend_side_is_the_same_code():
    ctx = _codigo_ctx({"k": "052"}, {"D-REMAP": {"k": "041"}})
    assert parity._codigo_cause("k", "41", "052", ctx) == "D-REMAP"
    assert parity._codigo_cause("k", " 041 ", "052", ctx) == "D-REMAP"


def test_notebook_codigo_d_ownerfase2_a_fase2_cedula_shared_by_two_profiles_is_ambiguous_nothing_cleared_or_assigned():
    main = (_entrada("222", "Ana Uno"), _entrada("333", "Beto Dos"), _entrada("444", "Carla Tres", codigo="050"))
    fase2 = (_entrada("111", "Ana Uno"), _entrada("111", "Beto Dos"))  # rows 0 and 1 both claim cedula 111
    vercel = (_entrada("111", "Otro Nombre Distinto", codigo="050"),)
    assert _replay(main=main, fase2=fase2, vercel=vercel).codes == ("050", "", "")  # the notebook: the first claimant
    ambiguous = _replay(main=main, fase2=fase2, vercel=vercel, rules=frozenset({"D-OWNERFASE2"}))
    assert ambiguous.codes == ("", "", "050") and _counts(ambiguous) == (0, 0, 0, 0)  # the holder keeps it, nobody gets it
    unique = _replay(main=main, fase2=(_entrada("111", "Ana Uno"),), vercel=vercel, rules=frozenset({"D-OWNERFASE2"}))
    assert unique.codes == ("050", "", "")  # ONE Fase 2 claimant is the owner


def test_notebook_codigo_d_remap_never_assigns_an_excluded_code():
    main = (_entrada("486", "Ivan Ejemplo"),)
    vercel = (_entrada("486", "Ivan Ejemplo", codigo="148"), _entrada("487", "Ivan Ejemplo", codigo="097"))  # both name-duplicated
    assert _replay(main=main, vercel=vercel, rules=frozenset({"D-REMAP"})).codes == ("",)
    assert _replay(main=main, vercel=vercel, excluded=frozenset(), rules=frozenset({"D-REMAP"})).codes == ("148",)


def test_a_review_signal_never_explains_an_estado_mismatch_either():
    inputs = _inputs(main=(_entrada("1000000001", "Ivan Ejemplo Rojas"),),
                     vercel=(_entrada("1000000009", "Ivan Ejemplo Rojas", codigo="097"),))
    depurado = inputs.run()
    assert [i["motivo"] for i in depurado.revision_manual] == ["codigo_remap_candidato"]
    xlsx = _xlsx_rows(depurado)
    _row(xlsx, "1000000001")["estado_sugerido"] = "activo"
    report = _run(inputs, xlsx)
    assert [(m["key"], m["explained"]) for m in report["columns"]["estado_sugerido"]["mismatches"]] == [("1000000001", None)]
