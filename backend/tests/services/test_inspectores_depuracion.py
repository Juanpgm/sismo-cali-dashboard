"""RED-first tests for `app.services.inspectores_depuracion` — the 6-seam
depuración engine (design.md Interfaces/Contracts; tasks.md Phase 2).

Every seam is a pure function: plain dict/dataclass fixtures, no mocks, no
I/O. See the module docstring for the exact `stickers`/`roster_by_cedula`
dict contracts this module defines.
"""
from __future__ import annotations

import inspect
import logging
import time
from datetime import date

import pytest

from app.services import inspectores_depuracion as dep
from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle


# ── Fixture builders ─────────────────────────────────────────────────────


def _entrada(cedula_key="", nombre_norm="", np="", entidad="", codigo="", pasos=(), no_persona=False, **extra):
    """`extra` carries the seven optional contact fields (`nombre`, `telefono`,
    `creado_en`, `id`, `correo`, `tarjeta_profesional`)."""
    return EntradaReferencia(
        cedula_key=cedula_key, nombre_norm=nombre_norm, np=np, entidad=entidad,
        codigo=codigo, pasos=tuple(pasos), no_persona=no_persona, **extra,
    )


def _bundle(vercel=(), fase2=(), main=(), codigos_duplicados=(), activa=True, motivo="", generado_en="2026-09-12"):
    return ReferenciaBundle(
        vercel=tuple(vercel), fase2=tuple(fase2), main=tuple(main),
        generado_en=generado_en, activa=activa, motivo=motivo,
        codigos_duplicados=tuple(codigos_duplicados),
    )


def _roster_entry(identificacion, nombre_completo, **kwargs):
    entry = {"identificacion": identificacion, "nombre_completo": nombre_completo}
    entry.update(kwargs)
    return entry


def _sticker(identificacion, *, origen="sistema", fecha_creacion=None, codigo="", nombre_completo=""):
    return {
        "origen": origen,
        "fecha_creacion": fecha_creacion,
        "inspector": {"identificacion": identificacion, "codigo": codigo, "nombre_completo": nombre_completo},
    }


EMPTY_REF = ReferenciaBundle.vacia(motivo="sin_blob")


# ── es_cuenta_no_persona / cedula_sospechosa (task 2.x heuristics) ─────────


def test_es_cuenta_no_persona_correo_pattern():
    assert dep.es_cuenta_no_persona("alguien@import.local", "Juan Perez") is True


def test_es_cuenta_no_persona_nombre_pattern():
    assert dep.es_cuenta_no_persona("real@example.com", "Brigada Norte") is True


def test_es_cuenta_no_persona_normal_person():
    assert dep.es_cuenta_no_persona("juan@example.com", "Juan Perez") is False


def test_cedula_sospechosa_empty():
    assert dep.cedula_sospechosa("") is True


def test_cedula_sospechosa_exactly_6_digits_ok():
    assert dep.cedula_sospechosa("123456") is False


def test_cedula_sospechosa_exactly_10_digits_starting_1_ok():
    assert dep.cedula_sospechosa("1234567890") is False


def test_cedula_sospechosa_exactly_10_digits_not_starting_1():
    assert dep.cedula_sospechosa("2234567890") is True


def test_cedula_sospechosa_5_digits_too_short():
    assert dep.cedula_sospechosa("12345") is True


def test_cedula_sospechosa_11_digits_too_long():
    assert dep.cedula_sospechosa("12345678901") is True


# ── tiene_sticker_valido cutoff (task 2.20, 2.21) ───────────────────────────


def test_sticker_valido_sistema_after_cutoff():
    sticker = _sticker("1", fecha_creacion="2026-08-20T00:00:00Z")
    assert dep._sticker_es_valido(sticker) is True


def test_sticker_valido_sistema_before_cutoff():
    sticker = _sticker("1", fecha_creacion="2026-08-19T23:59:59Z")
    assert dep._sticker_es_valido(sticker) is False


def test_sticker_valido_firebase_never_counts():
    sticker = _sticker("1", origen="firebase", fecha_creacion="2026-09-01T00:00:00Z")
    assert dep._sticker_es_valido(sticker) is False


def test_sticker_valido_unknown_origen_treated_as_excluded():
    sticker = _sticker("1", origen="migracion_manual", fecha_creacion="2026-09-01T00:00:00Z")
    assert dep._sticker_es_valido(sticker) is False


def test_sticker_valido_malformed_fecha_returns_false():
    sticker = _sticker("1", fecha_creacion="no es una fecha")
    assert dep._sticker_es_valido(sticker) is False


def test_sticker_valido_missing_fecha_returns_false():
    sticker = _sticker("1", fecha_creacion=None)
    assert dep._sticker_es_valido(sticker) is False


# ── Stage 1: fusionar_identidad (tasks 2.2, 2.3) ────────────────────────────


def test_fusionar_identidad_basic():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert set(perfiles) == {"1234567"}
    assert perfiles["1234567"].nombre_norm == "juan perez"


def test_fusionar_identidad_blank_identificacion_skipped():
    roster = {"000": _roster_entry("", "Sin Identidad")}
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert perfiles == {}


def test_fusionar_identidad_cedula_ties_same_name_unified():
    # D10 (slice 07): D-P2 unification moved AFTER overlays/stickers, so stage 1
    # (`fusionar_identidad`) no longer unifies — it keeps both profiles and
    # `unificar_duplicados` (run later by `depurar`) does the merge.
    roster = {
        "111": _roster_entry("111", "Juan Perez"),
        "222": _roster_entry("222", "Juan Perez"),
    }
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert len(perfiles) == 2
    unificados, _fusiones = dep.unificar_duplicados(perfiles)
    assert len(unificados) == 1
    survivor = next(iter(unificados.values()))
    assert set(survivor.cedulas_unificadas) | {survivor.identificacion} == {"111", "222"}


def test_fusionar_identidad_cedula_ties_different_name_not_unified():
    roster = {
        "111": _roster_entry("111", "Juan Perez"),
        "222": _roster_entry("222", "Maria Lopez"),
    }
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert len(perfiles) == 2


def test_fusionar_identidad_persona_sin_ninguna_fuente_de_identidad():
    # roster vacío, stickers vacíos, referencia vacía -> no explota, dict vacío
    perfiles, _ = dep.fusionar_identidad([], {}, EMPTY_REF)
    assert perfiles == {}


def test_fusionar_identidad_malformed_roster_entry_skipped():
    roster = {"1": "not-a-dict", "2": _roster_entry("222", "Juan Perez")}
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert set(perfiles) == {"222"}


def test_fusionar_identidad_sticker_aggregation():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    stickers = [
        _sticker("1234567", fecha_creacion="2026-08-25T00:00:00Z"),
        _sticker("1234567", fecha_creacion="2026-08-30T00:00:00Z"),
    ]
    perfiles, _ = dep.fusionar_identidad(stickers, roster, EMPTY_REF)
    perfil = perfiles["1234567"]
    assert perfil.n_stickers == 2
    assert perfil.ultimo_sticker == date(2026, 8, 30)
    assert perfil.tiene_sticker_valido is True


def test_fusionar_identidad_sticker_unattributable_skipped_not_raised():
    stickers = [_sticker("nadie-existe", fecha_creacion="2026-08-25T00:00:00Z")]
    perfiles, _ = dep.fusionar_identidad(stickers, {}, EMPTY_REF)
    assert perfiles == {}


def test_fusionar_identidad_malformed_sticker_skipped():
    roster = {"1": _roster_entry("1", "Juan Perez")}
    stickers = ["not-a-dict", {"origen": "sistema"}]  # no "inspector" key
    perfiles, _ = dep.fusionar_identidad(stickers, roster, EMPTY_REF)
    assert perfiles["1"].n_stickers == 0


def test_fusionar_identidad_referencia_overlay_cedula_match():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(
        vercel=[_entrada(cedula_key="1234567", nombre_norm="juan perez", np="P3", entidad="DAGRD")],
        fase2=[_entrada(cedula_key="1234567", nombre_norm="juan perez", np="P3")],
        main=[_entrada(cedula_key="1234567", nombre_norm="juan perez", np="P2", no_persona=False)],
    )
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    p = perfiles["1234567"]
    assert p.en_vercel is True and p.np_vercel == "P3"
    assert p.en_fase2 is True and p.np_fase2 == "P3"
    assert p.rango_main == "P2"


def test_fusionar_identidad_referencia_overlay_nombre_fallback():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(vercel=[_entrada(cedula_key="9999999", nombre_norm="juan perez", np="P4")])
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    assert perfiles["1234567"].en_vercel is True
    assert perfiles["1234567"].np_vercel == "P4"


def test_fusionar_identidad_no_persona_ref_from_main_folds_into_heuristic():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez", correo="juan@example.com")}
    ref = _bundle(main=[_entrada(cedula_key="1234567", nombre_norm="juan perez", no_persona=True)])
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    assert perfiles["1234567"].es_cuenta_no_persona is True


# ── Stage 2: remapear_codigos (tasks 2.4, 2.5, 2.6) ─────────────────────────


def test_remapear_codigos_cedula_match_assigns_current_titular():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(vercel=[_entrada(cedula_key="1234567", nombre_norm="juan perez", codigo="041")])
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1234567"].codigo == "041"
    assert revision == ()


def test_remapear_codigos_vercel_duplicate_excluded():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(vercel=[
        _entrada(cedula_key="1111111", nombre_norm="ana gomez", codigo="097"),
        _entrada(cedula_key="2222222", nombre_norm="ana gomez dos", codigo="097"),
    ])
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1234567"].codigo == ""
    assert {"motivo": "codigo_vercel_duplicado", "codigo": "097"} in revision


def test_remapear_codigos_referencia_codigos_duplicados_also_excluded():
    roster = {}
    ref = _bundle(
        vercel=[_entrada(cedula_key="1111111", nombre_norm="ana gomez", codigo="127")],
        codigos_duplicados=["127"],
    )
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    # already-known duplicate is not RE-reported (only newly-discovered ones are)
    assert revision == ()


def test_remapear_codigos_fuzzy_never_autoemerges():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez Gomez")}
    ref = _bundle(vercel=[_entrada(cedula_key="9999999", nombre_norm="juan perez gomes", codigo="055")])
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1234567"].codigo == ""  # never auto-assigned
    assert any(r["motivo"] == "codigo_remap_candidato" and r["codigo"] == "055" for r in revision)


def test_remapear_codigos_no_match_at_all_skipped_not_raised():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(vercel=[_entrada(cedula_key="9999999", nombre_norm="alguien completamente distinto", codigo="200")])
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1234567"].codigo == ""
    assert revision == ()  # "código que ya no existe" -> nadie lo reclama, sin crash


def test_remapear_codigos_empty_referencia_no_crash():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    perfiles, revision = dep.remapear_codigos(perfiles, EMPTY_REF)
    assert revision == ()


# ── Stage 3: unificar_duplicados (tasks 2.7, 2.8, 2.9) ──────────────────────


def _perfil(identidad_key, nombre_norm, **kwargs):
    defaults = dict(
        identidad_key=identidad_key, identificacion=identidad_key,
        nombre_completo=nombre_norm.title(), nombre_norm=nombre_norm,
    )
    defaults.update(kwargs)
    return dep.Perfil(**defaults)


def test_unificar_duplicados_no_duplicates_noop():
    perfiles = {"1": _perfil("1", "juan perez"), "2": _perfil("2", "maria lopez")}
    resultado, fusiones = dep.unificar_duplicados(perfiles)
    assert set(resultado) == {"1", "2"}
    assert fusiones == ()


def test_unificar_duplicados_name_collision_survivor_score_full_chain():
    # Full tie-break chain: has_sticker > has_codigo > en_vercel > en_fase2 >
    # not_no_persona > not_cedula_sospechosa > oldest creado_en.
    perdedor_sin_nada = _perfil("A", "juan perez", creado_en="2020-01-01")
    survivor_con_sticker = _perfil(
        "B", "juan perez", ultimo_sticker=date(2026, 8, 1), creado_en="2021-01-01",
    )
    perfiles = {"A": perdedor_sin_nada, "B": survivor_con_sticker}
    resultado, fusiones = dep.unificar_duplicados(perfiles)
    assert set(resultado) == {"B"}
    assert fusiones == ({"survivor": "B", "perdedor": "A", "nombre_norm": "juan perez"},)


def test_unificar_duplicados_tie_break_oldest_creado_en():
    older = _perfil("A", "juan perez", creado_en="2019-01-01")
    newer = _perfil("B", "juan perez", creado_en="2022-01-01")
    perfiles = {"A": older, "B": newer}
    resultado, _ = dep.unificar_duplicados(perfiles)
    assert set(resultado) == {"A"}


def test_unificar_duplicados_loser_not_deleted_from_source_dict():
    perfiles = {"A": _perfil("A", "juan perez"), "B": _perfil("B", "juan perez", ultimo_sticker=date(2026, 8, 1))}
    original_ids = {id(p) for p in perfiles.values()}
    dep.unificar_duplicados(perfiles)
    assert set(perfiles) == {"A", "B"}  # input dict itself untouched
    assert {id(p) for p in perfiles.values()} == original_ids  # same objects, unmutated


def test_unificar_duplicados_self_merge_never_happens():
    perfiles = {"A": _perfil("A", "juan perez")}
    resultado, fusiones = dep.unificar_duplicados(perfiles)
    assert set(resultado) == {"A"}
    assert fusiones == ()


def test_unificar_duplicados_backfill_first_non_empty_wins_and_sticker_counts_absorbed():
    # Slice 07 (spec: "The survivor MUST absorb the losers' sticker aggregates")
    # deliberately replaces the earlier "n_stickers is NOT summed" behavior. The
    # sum cannot change any parity column: `estado_sugerido` only reads
    # `n_stickers > 0`, and the survivor score already ranks a sticker-bearing
    # profile first, so a survivor never has 0 when a loser has >0.
    # Both sides now have sticker history (`n_stickers > 0` counts toward the score, per
    # spec), so the oldest `creado_en` decides — B is the survivor.
    perdedor = _perfil("A", "juan perez", correo="juan@old.com", n_stickers=5, creado_en="2022-01-01")
    survivor = _perfil("B", "juan perez", ultimo_sticker=date(2026, 8, 1), n_stickers=3, creado_en="2019-01-01")
    perfiles = {"A": perdedor, "B": survivor}
    resultado, _ = dep.unificar_duplicados(perfiles)
    ganador = resultado["B"]
    assert ganador.correo == "juan@old.com"  # backfilled: survivor's own was blank
    assert ganador.n_stickers == 8  # 5 + 3 absorbed


# ── Stage 4: colapsar_externos (tasks 2.10, 2.11, 2.12) ─────────────────────


def test_colapsar_externos_dias_inactivo_boundary_exactly_7_not_collapsed():
    p = _perfil("A", "cuenta sospechosa", cedula_sospechosa=True, ultimo_sticker=date(2026, 8, 24))
    restantes, detalle = dep.colapsar_externos({"A": p}, hoy=date(2026, 8, 31))
    assert detalle == ()
    assert "A" in restantes


def test_colapsar_externos_dias_inactivo_boundary_8_collapsed():
    p = _perfil("A", "cuenta sospechosa", cedula_sospechosa=True, ultimo_sticker=date(2026, 8, 23))
    restantes, detalle = dep.colapsar_externos({"A": p}, hoy=date(2026, 8, 31))
    assert len(detalle) == 1
    assert "A" not in restantes


def test_colapsar_externos_sin_ultimo_sticker_collapsed():
    p = _perfil("A", "cuenta fantasma", es_cuenta_no_persona=True, ultimo_sticker=None)
    restantes, detalle = dep.colapsar_externos({"A": p}, hoy=date(2026, 9, 1))
    assert len(detalle) == 1


def test_colapsar_externos_codigo_excludes():
    p = _perfil(
        "A", "cuenta con codigo", es_cuenta_no_persona=True, ultimo_sticker=None, codigo="099",
    )
    restantes, detalle = dep.colapsar_externos({"A": p}, hoy=date(2026, 9, 1))
    assert detalle == ()
    assert "A" in restantes


def test_colapsar_externos_no_heuristic_never_collapsed():
    p = _perfil("A", "persona normal", ultimo_sticker=None)
    restantes, detalle = dep.colapsar_externos({"A": p}, hoy=date(2026, 9, 1))
    assert detalle == ()
    assert "A" in restantes


def test_colapsar_externos_exentos_never_collapsed():
    p = _perfil("A", "cuenta fantasma", es_cuenta_no_persona=True, ultimo_sticker=None)
    restantes, detalle = dep.colapsar_externos({"A": p}, hoy=date(2026, 9, 1), exentos=frozenset({"A"}))
    assert detalle == ()
    assert "A" in restantes


def test_colapsar_externos_dias_inactivo_always_informational():
    p = _perfil("A", "persona normal", ultimo_sticker=date(2026, 8, 1))
    restantes, _ = dep.colapsar_externos({"A": p}, hoy=date(2026, 9, 1))
    assert restantes["A"].dias_inactivo == 31


def test_colapsar_externos_empty_perfiles_no_crash():
    restantes, detalle = dep.colapsar_externos({}, hoy=date(2026, 9, 1))
    assert restantes == {}
    assert detalle == ()


# ── Stage 5: resolver_np / calcular_fase / estado_sugerido / fuente_dato ────


def test_resolver_np_fase2_wins():
    p = _perfil("A", "juan perez", en_fase2=True, np_fase2="P3", en_vercel=True, np_vercel="P1")
    assert dep.resolver_np(p) == ("P3", "fase2")


def test_resolver_np_vercel_wins_over_main():
    p = _perfil("A", "juan perez", en_vercel=True, np_vercel="P2", rango_main="P1")
    assert dep.resolver_np(p) == ("P2", "vercel")


def test_resolver_np_main_fallback():
    p = _perfil("A", "juan perez", rango_main="P1")
    assert dep.resolver_np(p) == ("P1", "main")


def test_resolver_np_no_source_has_np():
    p = _perfil("A", "juan perez")
    assert dep.resolver_np(p) == ("", "ninguno")


def test_calcular_fase_below_threshold():
    assert dep.calcular_fase("P2") == ("Fase I", False)


def test_calcular_fase_at_threshold():
    assert dep.calcular_fase("P3") == ("Fase II", False)


def test_calcular_fase_non_matching_empty():
    assert dep.calcular_fase("") == ("Fase I", True)


def test_calcular_fase_non_matching_garbage():
    assert dep.calcular_fase("NO SIRVE") == ("Fase I", True)


def test_fuente_dato_main_only():
    assert dep.fuente_dato(_perfil("A", "x")) == "main"


def test_fuente_dato_main_fase2_vercel():
    p = _perfil("A", "x", en_fase2=True, en_vercel=True)
    assert dep.fuente_dato(p) == "main+fase2+vercel"


def test_estado_sugerido_no_persona_first():
    p = _perfil("A", "x", es_cuenta_no_persona=True, codigo="001")
    assert dep.estado_sugerido(p) == "no_persona"


def test_estado_sugerido_candidato_desactivacion():
    p = _perfil("A", "x")
    assert dep.estado_sugerido(p) == "candidato_desactivacion"


def test_estado_sugerido_revisar_when_in_reference():
    p = _perfil("A", "x", en_vercel=True)
    assert dep.estado_sugerido(p) == "revisar"


def test_estado_sugerido_activo_needs_codigo():
    p = _perfil("A", "x", codigo="001")
    assert dep.estado_sugerido(p) == "activo"


def test_estado_sugerido_sticker_activity_alone_never_activo():
    # tiene_sticker_valido=True alone, with n_stickers left at its default 0
    # (raw tiene_sticker=False) -> still candidato_desactivacion. Guards the
    # invariant that tiene_sticker_valido is NEVER read by estado_sugerido
    # for anything, not even to escape candidato_desactivacion.
    p = _perfil("A", "x", tiene_sticker_valido=True)
    assert dep.estado_sugerido(p) == "candidato_desactivacion"


def test_estado_sugerido_sticker_history_without_codigo_falls_to_revisar():
    # Spec scenario: "Sticker history without a current código falls to the
    # review fallback". Real bug this guards: the notebook's own
    # `desactivar`/`revision` id sets key off `tiene_sticker` = the RAW
    # all-time flag (`n_stickers > 0`), never `tiene_sticker_valido` (the
    # post-20-Aug-informational one). An inspector who has had at least one
    # sticker ever, but none of them post-20-Aug, no código, and absent from
    # BOTH Fase 2 and Vercel must land in "revisar" (fallback), not
    # "candidato_desactivacion".
    p = _perfil("A", "x", n_stickers=2, tiene_sticker_valido=False)
    assert dep.estado_sugerido(p) == "revisar"


def test_estado_sugerido_sticker_history_with_valido_and_no_codigo_still_revisar():
    # Same fallback applies even when the sticker history DOES include a
    # post-20-Aug-valid one — tiene_sticker_valido is still never a trigger
    # for "activo" (D7), it just isn't why this lands in "revisar" either.
    p = _perfil("A", "x", n_stickers=1, tiene_sticker_valido=True)
    assert dep.estado_sugerido(p) == "revisar"


def test_estado_sugerido_no_sticker_ever_and_in_referencia_still_revisar():
    # Branch 3: tiene_sticker=False (n_stickers=0), no código, but present in
    # Fase 2 or Vercel -> revisar (unaffected by the raw-vs-valido fix,
    # regression guard for the pre-existing branch).
    p = _perfil("A", "x", n_stickers=0, en_vercel=True)
    assert dep.estado_sugerido(p) == "revisar"


def test_estado_sugerido_cedula_sospechosa_alone_not_forced_to_no_persona():
    p = _perfil("A", "x", cedula_sospechosa=True, es_cuenta_no_persona=False, codigo="001")
    assert dep.estado_sugerido(p) == "activo"


# ── Stage 6: alias_nombres (tasks 2.22, 2.23) ───────────────────────────────


def test_alias_nombres_survey_name_maps_to_real_person():
    perfiles = {"1234567": _perfil("1234567", "juan perez")}
    alias = dep.alias_nombres(perfiles, ["Juan Pérez"])
    assert alias == {"juan perez": "1234567"}


def test_alias_nombres_no_match_empty():
    perfiles = {"1234567": _perfil("1234567", "juan perez")}
    alias = dep.alias_nombres(perfiles, ["Alguien Mas"])
    assert alias == {}


def test_alias_nombres_dedupe_collision_first_wins():
    perfiles = {
        "111": _perfil("111", "juan perez"),
        "222": _perfil("222", "juan perez"),
    }
    alias = dep.alias_nombres(perfiles, ["juan perez"])
    assert alias == {"juan perez": "111"}  # first-seen (dict insertion order) wins


def test_alias_nombres_empty_survey_list():
    perfiles = {"1234567": _perfil("1234567", "juan perez")}
    assert dep.alias_nombres(perfiles, []) == {}


def test_alias_nombres_blank_names_ignored():
    perfiles = {"1234567": _perfil("1234567", "juan perez")}
    assert dep.alias_nombres(perfiles, ["", None]) == {}


# ── depurar() orchestration (tasks 2.24, 2.25, 2.26, 2.27) ──────────────────


def test_depurar_empty_referencia_bundle_degrades_gracefully():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    resultado = dep.depurar(
        stickers=[], roster_by_cedula=roster, nombres_survey=[], referencia=EMPTY_REF, hoy=date(2026, 9, 1),
    )
    assert resultado.activa is False
    inspector = resultado.inspectores[0]
    assert inspector["np_fuente"] == "ninguno"


def test_depurar_non_person_deduped_against_survey_cali():
    roster = {"1234567": _roster_entry("1234567", "Prueba Migracion")}
    resultado = dep.depurar(
        stickers=[], roster_by_cedula=roster, nombres_survey=["Prueba Migracion"],
        referencia=EMPTY_REF, hoy=date(2026, 9, 1),
    )
    # exempted via alias_nombres -> stays a standalone inspector row, not collapsed
    assert resultado.grupo_externos is None
    assert len(resultado.inspectores) == 1


def test_depurar_non_person_without_survey_match_collapses():
    roster = {"1234567": _roster_entry("1234567", "Prueba Migracion")}
    resultado = dep.depurar(
        stickers=[], roster_by_cedula=roster, nombres_survey=[],
        referencia=EMPTY_REF, hoy=date(2026, 9, 1),
    )
    assert resultado.grupo_externos is not None
    assert resultado.grupo_externos["n_colapsados"] == 1
    assert resultado.inspectores == ()


def test_depurar_estado_sugerido_never_writes_no_firestore_handle_param():
    sig = inspect.signature(dep.depurar)
    assert set(sig.parameters) == {"stickers", "roster_by_cedula", "nombres_survey", "referencia", "hoy"}
    # No write-capable dependency is ever imported or called by this module —
    # advisory-only invariant (spec: "Classification never writes to the
    # inspector record"). Checked against imports/calls, not the docstring
    # prose (which legitimately explains the Firestore identity anchor).
    assert "credentials" not in dep.__dict__
    assert not any(name.startswith("firestore") for name in dir(dep))
    for line in inspect.getsource(dep).splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "firestore" not in stripped.lower()
            assert "credentials" not in stripped.lower()


def test_depurar_returns_value_type():
    resultado = dep.depurar(
        stickers=[], roster_by_cedula={}, nombres_survey=[], referencia=EMPTY_REF, hoy=date(2026, 9, 1),
    )
    assert isinstance(resultado, dep.Depuracion)


def test_depurar_full_pipeline_estado_activo_via_remap():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(
        vercel=[_entrada(cedula_key="1234567", nombre_norm="juan perez", np="P3", codigo="041")],
    )
    resultado = dep.depurar(
        stickers=[], roster_by_cedula=roster, nombres_survey=[], referencia=ref, hoy=date(2026, 9, 1),
    )
    inspector = resultado.inspectores[0]
    assert inspector["codigo"] == "041"
    assert inspector["np"] == "P3"
    assert inspector["fase"] == "Fase II"
    assert inspector["estado_sugerido"] == "activo"


def test_depurar_full_pipeline_sticker_history_falls_to_revisar_not_desactivacion():
    # End-to-end guard (not just the isolated estado_sugerido unit test):
    # a real pre-20-Aug sticker, attributed through fusionar_identidad, must
    # survive the whole pipeline and still land the inspector in "revisar",
    # never "candidato_desactivacion", even with no código and no Fase2/
    # Vercel membership.
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    stickers = [_sticker("1234567", fecha_creacion="2026-07-01T00:00:00Z")]  # pre-cutoff -> not "valido"
    resultado = dep.depurar(
        stickers=stickers, roster_by_cedula=roster, nombres_survey=[],
        referencia=EMPTY_REF, hoy=date(2026, 9, 1),
    )
    inspector = resultado.inspectores[0]
    assert inspector["codigo"] == ""
    assert inspector["tiene_sticker_valido"] is False
    assert inspector["estado_sugerido"] == "revisar"


def test_depurar_exact_name_merge_exposes_cedulas_unificadas_on_survivor():
    # CRITICAL fix (adversarial review): `_perfil_a_dict` must serialize
    # `Perfil.cedulas_unificadas` -- without it, the frontend has no way to
    # know a raw sticker carrying the LOSING cédula belongs to this same
    # survivor row (see seguimiento.js's buildIdentityIndexFromDepuracion).
    # 7-digit cédulas (like the rest of this file's fixtures) so
    # `cedula_sospechosa` is False and colapsar_externos never collapses
    # this pair away — otherwise the merge would never reach `inspectores`.
    roster = {
        "1111111": _roster_entry("1111111", "Juan Perez"),
        "2222222": _roster_entry("2222222", "Juan Perez"),
    }
    resultado = dep.depurar(
        stickers=[], roster_by_cedula=roster, nombres_survey=[],
        referencia=EMPTY_REF, hoy=date(2026, 9, 1),
    )
    assert len(resultado.inspectores) == 1
    survivor = resultado.inspectores[0]
    assert "cedulas_unificadas" in survivor
    assert set(survivor["cedulas_unificadas"]) | {survivor["identificacion"]} == {"1111111", "2222222"}


def test_depurar_no_merge_cedulas_unificadas_is_empty_list():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    resultado = dep.depurar(
        stickers=[], roster_by_cedula=roster, nombres_survey=[],
        referencia=EMPTY_REF, hoy=date(2026, 9, 1),
    )
    assert resultado.inspectores[0]["cedulas_unificadas"] == []


# ══════════════════════════════════════════════════════════════════════════
# Extension 2026-09-19, slice 07 — full universe (Phase 8, D9-D12, D14, D19)
# ══════════════════════════════════════════════════════════════════════════

HOY = date(2026, 9, 12)


def _depurar(*, roster=None, main=(), vercel=(), fase2=(), stickers=(), survey=(), hoy=HOY):
    return dep.depurar(
        stickers=list(stickers), roster_by_cedula=roster or {}, nombres_survey=list(survey),
        referencia=_bundle(vercel=vercel, fase2=fase2, main=main), hoy=hoy,
    )


def _by_key(resultado):
    return {i["identidad_key"]: i for i in resultado.inspectores}


# ── 8.1 / 8.2 main rows create profiles ─────────────────────────────────────


def test_fusionar_identidad_main_row_creates_perfil():
    main = [_entrada(
        cedula_key="12345678", nombre_norm="ana gomez", nombre="Ana Gomez", np="P2",
        entidad="DAGRD", codigo="041", telefono="3001234567", correo="Ana@Example.com",
        tarjeta_profesional="TP-1", creado_en="2026-01-02", id="uuid-1",
    )]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert set(perfiles) == {"12345678"}
    p = perfiles["12345678"]
    assert p.identidad_key == "12345678" and p.identificacion == "12345678"
    assert p.nombre_completo == "Ana Gomez" and p.nombre_norm == "ana gomez"
    assert p.firestore_backed is False
    assert (p.codigo, p.num_telefono, p.tarjeta_profesional) == ("041", "3001234567", "TP-1")
    assert (p.correo, p.correo_contacto) == ("Ana@Example.com", "ana@example.com")
    assert (p.creado_en, p.id) == ("2026-01-02", "uuid-1")
    assert p.rango_main == "P2" and p.entidad_main == "DAGRD"
    assert revision == ()


def test_fusionar_identidad_main_row_without_nombre_falls_back_to_nombre_norm_title():
    main = [_entrada(cedula_key="12345678", nombre_norm="ana gomez")]  # old bundle: no `nombre`
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert perfiles["12345678"].nombre_completo == "Ana Gomez"
    assert perfiles["12345678"].nombre_norm == "ana gomez"


def test_fusionar_identidad_roster_and_main_only_rows_coexist():
    roster = {"1111111": _roster_entry("1111111", "Juan Perez")}
    main = [_entrada(cedula_key="2222222", nombre_norm="maria lopez")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(main=main))
    assert set(perfiles) == {"1111111", "2222222"}
    assert perfiles["1111111"].firestore_backed is True
    assert perfiles["2222222"].firestore_backed is False


# ── 8.3 Firestore first, main backfills only empty fields ───────────────────


def test_fusionar_identidad_firestore_first_main_backfills_empty_only():
    roster = {"12345678": _roster_entry(
        "12345678", "Juan Perez", num_telefono="3000000000", tarjeta_profesional="",
    )}
    main = [_entrada(
        cedula_key="12345678", nombre_norm="otro nombre distinto", nombre="Otro Nombre Distinto",
        tarjeta_profesional="TP-9", telefono="3119999999", creado_en="2020-05-05", id="uuid-9",
    )]
    perfiles, revision = dep.fusionar_identidad([], roster, _bundle(main=main))
    assert set(perfiles) == {"12345678"}  # exactly one profile
    p = perfiles["12345678"]
    assert p.nombre_completo == "Juan Perez" and p.nombre_norm == "juan perez"  # Firestore kept
    assert p.tarjeta_profesional == "TP-9"  # empty -> backfilled
    assert p.num_telefono == "3000000000"  # non-empty Firestore value NOT overwritten
    assert p.creado_en == "2020-05-05" and p.id == "uuid-9"
    assert p.firestore_backed is True
    assert revision == ()


def test_fusionar_identidad_main_backfills_codigo_only_when_firestore_codigo_empty():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo=""),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="007"),
    }
    main = [
        _entrada(cedula_key="1111111", nombre_norm="ana uno", codigo="041"),
        _entrada(cedula_key="2222222", nombre_norm="beto dos", codigo="099"),
    ]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(main=main))
    assert perfiles["1111111"].codigo == "041"
    assert perfiles["2222222"].codigo == "007"


# ── 8.4 duplicate cédula inside main: first-wins + revision ─────────────────


def test_fusionar_identidad_main_duplicate_cedula_first_wins():
    main = [
        _entrada(cedula_key="12345678", nombre_norm="ana gomez", nombre="Ana Gomez",
                 telefono="3001111111", id="uuid-1"),
        _entrada(cedula_key="12345678", nombre_norm="beto ruiz", nombre="Beto Ruiz",
                 telefono="3002222222", tarjeta_profesional="TP-2", id="uuid-2"),
    ]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert set(perfiles) == {"12345678"}
    p = perfiles["12345678"]
    assert p.nombre_completo == "Ana Gomez"  # first wins
    assert p.num_telefono == "3001111111"
    assert p.tarjeta_profesional == ""  # the duplicate's fields never leak in
    assert p.id == "uuid-1"
    assert len(revision) == 1
    item = revision[0]
    assert item["motivo"] == "cedula_duplicada_main"
    assert item["cedula_key"] == "12345678"
    assert item["nombre_completo"] == "Ana Gomez"
    assert item["nombre_completo_duplicado"] == "Beto Ruiz"
    assert item["mismo_nombre"] is False


def test_fusionar_identidad_main_duplicate_cedula_same_name_still_reported_and_flagged():
    main = [
        _entrada(cedula_key="12345678", nombre_norm="ana gomez", nombre="Ana Gomez"),
        _entrada(cedula_key="12345678", nombre_norm="ana gomez", nombre="ANA GOMEZ"),
    ]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert len(perfiles) == 1
    assert [r["motivo"] for r in revision] == ["cedula_duplicada_main"]
    assert revision[0]["mismo_nombre"] is True


def test_fusionar_identidad_main_duplicate_cedula_formats_are_the_same_key():
    # dots / spaces / float tail normalize to ONE cédula. Leading zeros do NOT
    # (C1: the join key mirrors the frontend's `cedulaKey`, which keeps them).
    main = [
        _entrada(cedula_key="12345678", nombre_norm="ana gomez"),
        _entrada(cedula_key="12.345.678", nombre_norm="ana gomez b"),
        _entrada(cedula_key=" 12345678 ", nombre_norm="ana gomez c"),
        _entrada(cedula_key="12345678.0", nombre_norm="ana gomez d"),
    ]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert set(perfiles) == {"12345678"}
    assert [r["motivo"] for r in revision] == ["cedula_duplicada_main"] * 3


def test_fusionar_identidad_main_duplicate_of_a_roster_backed_key_is_still_flagged():
    roster = {"12345678": _roster_entry("12345678", "Juan Perez")}
    main = [
        _entrada(cedula_key="12345678", nombre_norm="juan perez", tarjeta_profesional="TP-1"),
        _entrada(cedula_key="12345678", nombre_norm="otro", tarjeta_profesional="TP-2"),
    ]
    perfiles, revision = dep.fusionar_identidad([], roster, _bundle(main=main))
    assert len(perfiles) == 1
    assert perfiles["12345678"].tarjeta_profesional == "TP-1"  # first main row backfills, second never
    assert [r["motivo"] for r in revision] == ["cedula_duplicada_main"]


# ── 8.5 main row without a usable cédula is reported, not dropped silently ──


def test_engine_tolerates_malformed_main_entries_without_a_cedula_and_reports_them():
    # Engine tolerance to MALFORMED entries built directly: in production the
    # parser (`_parse_entrada`) drops blank `cedula_key` rows and the publisher
    # counts them (`descartados_sin_cedula`), so only a non-empty non-digit
    # string ("N/A") can reach `main_sin_cedula` through the real pipeline.
    main = [
        _entrada(cedula_key="", nombre_norm="sin cedula uno", nombre="Sin Cedula Uno", id="u1"),
        _entrada(cedula_key="N/A", nombre_norm="sin cedula dos", nombre="Sin Cedula Dos"),
        _entrada(cedula_key="12345678", nombre_norm="con cedula"),
    ]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert set(perfiles) == {"12345678"}  # neither digit-less row became a profile
    assert [r["motivo"] for r in revision] == ["main_sin_cedula", "main_sin_cedula"]
    assert revision[0]["nombre_completo"] == "Sin Cedula Uno"
    assert revision[0]["id"] == "u1"
    assert revision[1]["nombre_completo"] == "Sin Cedula Dos"


@pytest.mark.parametrize("cedula", [None, float("nan"), "nan", "None", "   ", "abc"])
def test_engine_tolerates_nan_like_cedula_entries_built_directly(cedula):
    # Direct-construction tolerance test (None/NaN/blank never come out of
    # `parse_bundle`; "nan"/"None"/"abc" strings can).
    main = [_entrada(cedula_key=cedula, nombre_norm="alguien")]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert perfiles == {}
    assert [r["motivo"] for r in revision] == ["main_sin_cedula"]


@pytest.mark.parametrize("cedula", ["0", "000"])
def test_fusionar_identidad_main_placeholder_zero_cedula_is_kept_not_crashing(cedula):
    # digits ARE present (all zeros): keyed by the raw zeros, flagged sospechosa later.
    perfiles, revision = dep.fusionar_identidad(
        [], {}, _bundle(main=[_entrada(cedula_key=cedula, nombre_norm="alguien")]),
    )
    assert set(perfiles) == {cedula}
    assert perfiles[cedula].cedula_sospechosa is True
    assert revision == ()


# ── 8.6 empty normalized name survives, never unified ───────────────────────


def test_fusionar_identidad_empty_nombre_survives():
    main = [_entrada(cedula_key="1111111", nombre_norm="", nombre="")]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert set(perfiles) == {"1111111"}
    assert perfiles["1111111"].nombre_norm == ""
    assert revision == ()


def test_empty_nombre_profiles_are_never_unified_end_to_end():
    main = [
        _entrada(cedula_key="1111111", nombre_norm=""),
        _entrada(cedula_key="2222222", nombre_norm=""),
    ]
    resultado = _depurar(main=main)
    assert set(_by_key(resultado)) == {"1111111", "2222222"}
    assert all(i["cedulas_unificadas"] == [] for i in resultado.inspectores)


def test_name_only_overlay_never_matches_an_empty_name():
    roster = {"1111111": _roster_entry("1111111", "")}  # empty name on the Firestore side
    vercel = [_entrada(cedula_key="9999999", nombre_norm="", np="P9")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(vercel=vercel))
    assert perfiles["1111111"].en_vercel is False


# ── 8.7 - 8.10 sticker attribution through the cédula alias index ───────────


def test_atribuir_stickers_resolves_through_alias_index():
    survivor = _perfil("1111111", "juan perez", cedulas_unificadas=("777",))
    perfiles = {"1111111": survivor}
    stickers = [_sticker("777", fecha_creacion="2026-08-25T00:00:00Z")]
    dep.atribuir_stickers(perfiles, stickers)
    assert survivor.n_stickers == 1
    assert survivor.ultimo_sticker == date(2026, 8, 25)
    assert survivor.tiene_sticker_valido is True


def test_atribuir_stickers_own_key_wins_over_a_foreign_alias_claim():
    # B claims "5555555" as a unified-away key, but A OWNS it: the owner wins.
    a = _perfil("5555555", "ana uno")
    b = _perfil("6666666", "beto dos", cedulas_unificadas=("5555555",))
    dep.atribuir_stickers(
        {"5555555": a, "6666666": b}, [_sticker("5555555", fecha_creacion="2026-08-25T00:00:00Z")],
    )
    assert (a.n_stickers, b.n_stickers) == (1, 0)


def test_atribuir_stickers_unknown_cedula_is_noop():
    p = _perfil("1111111", "juan perez")
    stickers = [
        _sticker("9999999", fecha_creacion="2026-08-25T00:00:00Z"),
        _sticker("", fecha_creacion="2026-08-25T00:00:00Z"),
        _sticker(None, fecha_creacion="2026-08-25T00:00:00Z"),
        "not-a-dict",
        {"origen": "sistema"},
        {"inspector": "no-dict"},
    ]
    dep.atribuir_stickers({"1111111": p}, stickers)
    assert p.n_stickers == 0 and p.ultimo_sticker is None and p.tiene_sticker_valido is False


@pytest.mark.parametrize("perfil_id, sticker_id", [
    ("12345", "0012345"),
    ("0012345", "12345"),
    ("12345", "12.345"),
    ("12345", " 12345 "),
    ("12345", "12345.0"),
    ("12345", 12345),
    ("12345", 12345.0),
])
def test_atribuir_stickers_normalizes_leading_zeros_dots_spaces_and_float_tails(perfil_id, sticker_id):
    p = _perfil(perfil_id, "juan perez")
    dep.atribuir_stickers({perfil_id: p}, [_sticker(sticker_id, fecha_creacion="2026-08-25T00:00:00Z")])
    assert p.n_stickers == 1


def test_atribuir_stickers_distinct_cedulas_do_not_collide():
    a = _perfil("12345", "ana uno")
    b = _perfil("123450", "beto dos")  # NOT "12345" (a naive float-tail strip would merge them)
    dep.atribuir_stickers(
        {"12345": a, "123450": b}, [_sticker("123450", fecha_creacion="2026-08-25T00:00:00Z")],
    )
    assert (a.n_stickers, b.n_stickers) == (0, 1)


def test_fusionar_identidad_attributes_stickers_to_main_only_profiles():
    main = [_entrada(cedula_key="12345678", nombre_norm="ana gomez")]
    stickers = [_sticker("12.345.678", fecha_creacion="2026-08-25T00:00:00Z")]
    perfiles, _ = dep.fusionar_identidad(stickers, {}, _bundle(main=main))
    assert perfiles["12345678"].n_stickers == 1


# ── 8.11 - 8.14 D-P2 unification runs AFTER overlays + sticker attribution ──


def test_unificar_duplicados_runs_after_overlays_scores_populated_flags():
    # A = Firestore roster profile, in Vercel (by cédula) with a codigo.
    # B = main-only profile with the same exact name and the ONLY sticker.
    # Before D10 the score was degenerate (all flags false -> first inserted
    # wins = A). After overlays + attribution, `n_stickers>0` outranks
    # `en_vercel`, so B survives and inherits en_vercel / np / codigo.
    roster = {"1111111": _roster_entry("1111111", "Juan Perez")}
    main = [_entrada(cedula_key="2222222", nombre_norm="juan perez", nombre="Juan Perez")]
    vercel = [_entrada(cedula_key="1111111", nombre_norm="j perez vercel", np="P3", codigo="041")]
    stickers = [_sticker("2222222", fecha_creacion="2026-09-01T00:00:00Z")]
    resultado = _depurar(roster=roster, main=main, vercel=vercel, stickers=stickers)
    assert list(_by_key(resultado)) == ["2222222"]
    survivor = _by_key(resultado)["2222222"]
    assert survivor["cedulas_unificadas"] == ["1111111"]  # the loser's key stays resolvable
    assert survivor["codigo"] == "041"
    assert (survivor["np"], survivor["np_fuente"]) == ("P3", "vercel")
    assert "vercel" in survivor["fuente_dato"].split("+")
    assert survivor["estado_sugerido"] == "activo"
    assert survivor["ultimo_sticker"] == "2026-09-01"


def test_cross_source_cedula_mismatch_keeps_firestore_anchor_on_full_tie():
    # Same name, DIFFERENT cédulas across sources, nothing else to separate
    # them: the Firestore-backed profile is the last tie-break, so its
    # identificacion stays the anchor and the main cédula is recorded.
    roster = {"1111111": _roster_entry("1111111", "Juan Perez")}
    main = [_entrada(cedula_key="2222222", nombre_norm="juan perez", nombre="Juan Perez")]
    resultado = _depurar(roster=roster, main=main)
    assert list(_by_key(resultado)) == ["1111111"]
    assert _by_key(resultado)["1111111"]["identificacion"] == "1111111"
    assert _by_key(resultado)["1111111"]["cedulas_unificadas"] == ["2222222"]


def test_main_only_by_name_match_becomes_its_own_profile_then_unifies():
    # The main row shares the roster person's NAME but not the cédula: it does
    # NOT match a roster Perfil by cédula, so it creates its own profile (stage
    # 1) and only the later D-P2 pass merges the pair.
    roster = {"1111111": _roster_entry("1111111", "Juan Perez")}
    main = [_entrada(cedula_key="2222222", nombre_norm="juan perez", np="P2")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(main=main))
    assert set(perfiles) == {"1111111", "2222222"}
    unificados, fusiones = dep.unificar_duplicados(perfiles)
    assert len(unificados) == 1 and len(fusiones) == 1


def test_unificar_duplicados_tiebreak_creado_en_then_firestore_backed():
    firestore = _perfil("A", "juan perez", firestore_backed=True, creado_en="2022-01-01")
    main_viejo = _perfil("B", "juan perez", creado_en="2019-01-01")
    # full score tie -> oldest creado_en wins, even against the Firestore-backed one
    resultado, _ = dep.unificar_duplicados({"A": firestore, "B": main_viejo})
    assert set(resultado) == {"B"}
    # equal creado_en -> Firestore-backed wins (listed SECOND to prove it is not just insertion order)
    main = _perfil("B", "juan perez", creado_en="2022-01-01")
    resultado, _ = dep.unificar_duplicados({"B": main, "A": _perfil(
        "A", "juan perez", firestore_backed=True, creado_en="2022-01-01")})
    assert set(resultado) == {"A"}
    # both creado_en empty -> Firestore-backed wins
    resultado, _ = dep.unificar_duplicados({"B": _perfil("B", "juan perez"), "A": _perfil(
        "A", "juan perez", firestore_backed=True)})
    assert set(resultado) == {"A"}


def test_unificar_duplicados_blank_creado_en_never_beats_a_real_date():
    firestore_sin_fecha = _perfil("A", "juan perez", firestore_backed=True)
    main_con_fecha = _perfil("B", "juan perez", creado_en="2021-03-03")
    resultado, _ = dep.unificar_duplicados({"A": firestore_sin_fecha, "B": main_con_fecha})
    assert set(resultado) == {"B"}


def test_unificar_duplicados_unparseable_creado_en_is_treated_as_blank():
    a = _perfil("A", "juan perez", firestore_backed=True, creado_en="ayer por la tarde")
    b = _perfil("B", "juan perez", creado_en="basura")
    resultado, _ = dep.unificar_duplicados({"B": b, "A": a})
    assert set(resultado) == {"A"}


def test_unificar_duplicados_absorbs_loser_sticker_aggregates():
    survivor = _perfil("B", "juan perez", n_stickers=3, ultimo_sticker=date(2026, 8, 1),
                       tiene_sticker_valido=False)
    perdedor = _perfil("A", "juan perez", n_stickers=2, ultimo_sticker=date(2026, 9, 1),
                       tiene_sticker_valido=True)
    # equal score, no creado_en, neither Firestore-backed -> first-seen (B) survives
    resultado, _ = dep.unificar_duplicados({"B": survivor, "A": perdedor})
    ganador = resultado["B"]
    assert ganador.n_stickers == 5
    assert ganador.ultimo_sticker == date(2026, 9, 1)  # the later of both sides
    assert ganador.tiene_sticker_valido is True
    assert (perdedor.n_stickers, survivor.n_stickers) == (2, 3)  # inputs untouched


def test_unificar_duplicados_registers_every_loser_key_transitively():
    a = _perfil("A", "juan perez", ultimo_sticker=date(2026, 8, 1))
    b = _perfil("B", "juan perez", cedulas_unificadas=("Z",))
    c = _perfil("C", "juan perez")
    resultado, _ = dep.unificar_duplicados({"A": a, "B": b, "C": c})
    assert set(resultado["A"].cedulas_unificadas) == {"B", "C", "Z"}


def test_unificar_duplicados_backfills_first_non_empty_loser_value():
    survivor = _perfil("S", "juan perez", ultimo_sticker=date(2026, 8, 1))
    l1 = _perfil("L1", "juan perez", num_telefono="", tarjeta_profesional="TP-1")
    l2 = _perfil("L2", "juan perez", num_telefono="3001", tarjeta_profesional="TP-2")
    l3 = _perfil("L3", "juan perez", num_telefono="3002", entidad_main="DAGRD", id="uuid-3")
    resultado, _ = dep.unificar_duplicados({"L1": l1, "S": survivor, "L2": l2, "L3": l3})
    g = resultado["S"]
    assert (g.num_telefono, g.tarjeta_profesional) == ("3001", "TP-1")  # first non-empty, in order
    assert (g.entidad_main, g.id) == ("DAGRD", "uuid-3")


def test_unificar_duplicados_firestore_backed_flag_is_kept_if_either_side_had_it():
    survivor = _perfil("B", "juan perez", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("A", "juan perez", firestore_backed=True)
    resultado, _ = dep.unificar_duplicados({"A": perdedor, "B": survivor})
    assert resultado["B"].firestore_backed is True


def test_unificar_duplicados_two_empty_names_stay_separate():
    a = _perfil("A", "", ultimo_sticker=date(2026, 8, 1))
    b = _perfil("B", "")
    resultado, fusiones = dep.unificar_duplicados({"A": a, "B": b})
    assert set(resultado) == {"A", "B"} and fusiones == ()


# ── 8.15 - 8.17 entidad precedence: Vercel (by cédula) > Firestore > main ───


def test_entidad_precedence_vercel_by_cedula_then_firestore_then_main():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", entidad="B")}
    main = [_entrada(cedula_key="1111111", nombre_norm="ana uno", entidad="C")]
    vercel = [_entrada(cedula_key="1111111", nombre_norm="ana uno", entidad="A")]
    assert _by_key(_depurar(roster=roster, main=main, vercel=vercel))["1111111"]["entidad"] == "A"
    assert _by_key(_depurar(roster=roster, main=main))["1111111"]["entidad"] == "B"
    roster_sin_entidad = {"1111111": _roster_entry("1111111", "Ana Uno")}
    assert _by_key(_depurar(roster=roster_sin_entidad, main=main))["1111111"]["entidad"] == "C"


def test_entidad_falls_through_an_empty_vercel_entidad():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", entidad="B")}
    vercel = [_entrada(cedula_key="1111111", nombre_norm="ana uno", entidad="")]
    assert _by_key(_depurar(roster=roster, vercel=vercel))["1111111"]["entidad"] == "B"


def test_entidad_name_only_vercel_match_does_not_supply_entidad():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    main = [_entrada(cedula_key="1111111", nombre_norm="ana uno", entidad="C")]
    vercel = [_entrada(cedula_key="9999999", nombre_norm="ana uno", entidad="A", np="P2")]  # name-only
    inspector = _by_key(_depurar(roster=roster, main=main, vercel=vercel))["1111111"]
    assert inspector["entidad"] == "C"
    assert "vercel" in inspector["fuente_dato"]  # the match still counts for np / en_vercel...
    assert inspector["np"] == "P2"  # ...just not for entidad


def test_entidad_absent_everywhere_is_empty_string():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    vercel = [_entrada(cedula_key="9999999", nombre_norm="ana uno", entidad="")]
    assert _by_key(_depurar(roster=roster, vercel=vercel))["1111111"]["entidad"] == ""
    assert _by_key(_depurar(roster=roster))["1111111"]["entidad"] == ""


def test_resolver_entidad_is_a_pure_precedence_function():
    p = _perfil("A", "ana", entidad_vercel="A", entidad_firestore="B", entidad_main="C")
    assert dep.resolver_entidad(p) == "A"
    assert dep.resolver_entidad(_perfil("A", "ana", entidad_firestore="B", entidad_main="C")) == "B"
    assert dep.resolver_entidad(_perfil("A", "ana", entidad_main="C")) == "C"
    assert dep.resolver_entidad(_perfil("A", "ana")) == ""


# ── 8.18 dict indexes replace the linear scan (approval + perf) ─────────────


def test_reference_lookup_prefers_cedula_match_over_an_earlier_name_match():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    vercel = [
        _entrada(cedula_key="9999999", nombre_norm="ana uno", np="P1"),  # name match, listed first
        _entrada(cedula_key="1111111", nombre_norm="otro nombre", np="P4"),  # cédula match
    ]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(vercel=vercel))
    assert perfiles["1111111"].np_vercel == "P4"


def test_reference_lookup_first_row_wins_on_a_repeated_key():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    vercel = [
        _entrada(cedula_key="1111111", nombre_norm="ana uno", np="P1"),
        _entrada(cedula_key="1111111", nombre_norm="ana uno", np="P5"),
    ]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(vercel=vercel))
    assert perfiles["1111111"].np_vercel == "P1"


@pytest.mark.parametrize("cedula_vercel", ["12.345.678", " 12345678 ", "12345678.0"])
def test_reference_lookup_matches_cedula_across_dots_spaces_and_float_tails(cedula_vercel):
    roster = {"12345678": _roster_entry("12345678", "Ana Uno")}
    vercel = [_entrada(cedula_key=cedula_vercel, nombre_norm="x", np="P2", entidad="DAGRD")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(vercel=vercel))
    assert perfiles["12345678"].en_vercel is True
    assert perfiles["12345678"].entidad_vercel == "DAGRD"


def test_reference_lookup_does_not_match_a_zero_padded_cedula_by_cedula():
    # C1: overlays use the zero-PRESERVING key (same as the universe / frontend);
    # "0012345678" is a different cédula from "12345678", so it must not lend its
    # entidad (a name-only hit never does either — D12).
    roster = {"12345678": _roster_entry("12345678", "Ana Uno")}
    vercel = [_entrada(cedula_key="0012345678", nombre_norm="x", np="P2", entidad="DAGRD")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(vercel=vercel))
    assert perfiles["12345678"].en_vercel is False
    assert perfiles["12345678"].entidad_vercel == ""


def test_depurar_2000_main_entries_run_time_bounded():
    n = 2500
    main = [
        _entrada(cedula_key=str(10_000_000 + i), nombre_norm=f"persona numero {i}",
                 nombre=f"Persona Numero {i}", np="P1", telefono=f"300{i:07d}")
        for i in range(n)
    ]
    roster = {
        str(10_000_000 + i): _roster_entry(str(10_000_000 + i), f"Persona Numero {i}")
        for i in range(0, n, 5)
    }
    vercel = [
        _entrada(cedula_key=str(10_000_000 + i), nombre_norm=f"persona numero {i}", np="P2",
                 entidad="DAGRD", codigo=f"C{i}")
        for i in range(0, n, 3)
    ]
    stickers = [_sticker(str(10_000_000 + i), fecha_creacion="2026-09-01T00:00:00Z") for i in range(0, n, 2)]
    t0 = time.perf_counter()
    resultado = _depurar(roster=roster, main=main, vercel=vercel, stickers=stickers)
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, f"depurar() over {n} main rows took {elapsed:.2f}s"
    colapsados = resultado.grupo_externos["n_colapsados"] if resultado.grupo_externos else 0
    assert len(resultado.inspectores) + colapsados == n  # every distinct cédula accounted for once
    assert len({i["identidad_key"] for i in resultado.inspectores}) == len(resultado.inspectores)


# ── 8.19 full-pipeline universe accounting ──────────────────────────────────


def test_depurar_universe_row_count_grows_without_duplicates():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos"),
    }
    main = [
        _entrada(cedula_key="1111111", nombre_norm="ana uno"),        # overlaps roster
        _entrada(cedula_key="3333333", nombre_norm="carla tres"),     # main-only
        _entrada(cedula_key="4444444", nombre_norm="beto dos"),       # same NAME as roster 2222222 -> unified
        _entrada(cedula_key="12345", nombre_norm="dario cuatro"),     # cédula sospechosa, inactive -> collapsed
        _entrada(cedula_key="3333333", nombre_norm="carla otra"),     # duplicate cédula -> not a new row
    ]
    resultado = _depurar(roster=roster, main=main)
    keys = [i["identidad_key"] for i in resultado.inspectores]
    assert len(keys) == len(set(keys))  # every emitted identidad_key is unique
    # roster {1111111, 2222222} ∪ distinct main cédulas {1111111, 3333333, 4444444, 12345}
    universe = len({"1111111", "2222222", "3333333", "4444444", "12345"})
    unificados, colapsados = 1, 1
    assert len(keys) == universe - unificados - colapsados == 3
    assert set(keys) == {"1111111", "2222222", "3333333"}  # Firestore anchor wins the beto dos pair
    assert resultado.grupo_externos["n_colapsados"] == 1
    assert {r["motivo"] for r in resultado.revision_manual} == {"cedula_duplicada_main"}


def test_depurar_revision_manual_orders_universe_items_before_remap_items_for_directly_built_blank_cedula():
    # Ordering test only: a blank cédula is built directly here (engine tolerance
    # to a malformed entry), it is NOT the production path (parser drops it).
    main = [_entrada(cedula_key="", nombre_norm="sin cedula")]
    vercel = [
        _entrada(cedula_key="1111111", nombre_norm="ana gomez", codigo="097"),
        _entrada(cedula_key="2222222", nombre_norm="ana gomez dos", codigo="097"),
    ]
    resultado = _depurar(main=main, vercel=vercel)
    assert [r["motivo"] for r in resultado.revision_manual] == ["main_sin_cedula", "codigo_vercel_duplicado"]


# ── Edge cases: empties, malformed, huge, formats ───────────────────────────


def test_depurar_empty_roster_and_empty_main_is_a_valid_empty_result():
    resultado = _depurar()
    assert resultado.inspectores == () and resultado.grupo_externos is None
    assert resultado.revision_manual == () and resultado.alias_nombres == {}


def test_depurar_empty_main_keeps_the_roster_universe():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    assert list(_by_key(_depurar(roster=roster))) == ["1111111"]


def test_depurar_empty_roster_serves_the_main_universe():
    main = [_entrada(cedula_key="1111111", nombre_norm="ana uno"), _entrada(cedula_key="2222222", nombre_norm="beto dos")]
    assert set(_by_key(_depurar(main=main))) == {"1111111", "2222222"}


def test_depurar_main_cedula_formats_do_not_duplicate_a_roster_profile():
    roster = {
        "12345678": _roster_entry("12345678", "Ana Uno"),
        "23456789": _roster_entry("23456789", "Beto Dos"),
        "34567890": _roster_entry("34567890", "Carla Tres"),
    }
    main = [
        _entrada(cedula_key="12.345.678", nombre_norm="ana uno"),
        _entrada(cedula_key="0023456789", nombre_norm="beto dos"),
        _entrada(cedula_key=" 34567890.0 ", nombre_norm="carla tres"),
    ]
    resultado = _depurar(roster=roster, main=main)
    assert set(_by_key(resultado)) == {"12345678", "23456789", "34567890"}
    assert resultado.revision_manual == ()


def test_depurar_roster_cedulas_with_different_formats_collapse_to_one_profile():
    roster = {"a": _roster_entry("12345", "Juan Perez"), "b": _roster_entry("12.345", "Juan Perez")}
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert len(perfiles) == 1


def test_roster_zero_padded_and_unpadded_cedulas_are_two_profiles_until_dp2():
    # C1: the frontend keeps leading zeros, so the engine must not merge them
    # silently at universe level; same name -> D-P2 unifies them explicitly.
    roster = {"a": _roster_entry("12345", "Juan Perez"), "b": _roster_entry("0012345", "Juan Perez")}
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert set(perfiles) == {"12345", "0012345"}
    unificados, fusiones = dep.unificar_duplicados(perfiles)
    assert len(unificados) == 1 and len(fusiones) == 1


@pytest.mark.parametrize("entrada", [
    EntradaReferencia(cedula_key=None, nombre_norm=None, np=None, entidad=None, codigo=None, pasos=(), no_persona=None),
    EntradaReferencia(cedula_key=float("nan"), nombre_norm=float("nan"), np=float("nan"),
                      entidad=float("nan"), codigo=float("nan"), pasos=(), no_persona=False,
                      nombre=None, telefono=None, correo=float("nan"), tarjeta_profesional=None),
    EntradaReferencia(cedula_key=1234567, nombre_norm=1234567, np=3, entidad=0, codigo=41, pasos=(), no_persona=False),
])
def test_depurar_malformed_main_entries_never_raise(entrada):
    resultado = _depurar(main=[entrada])
    assert isinstance(resultado, dep.Depuracion)


def test_depurar_malformed_roster_and_stickers_alongside_a_main_universe_never_raise():
    roster = {"x": None, "y": {"identificacion": None}, "z": _roster_entry("1111111", None)}
    stickers = [None, 5, {"inspector": None}, _sticker(float("nan"), fecha_creacion=object())]
    resultado = _depurar(roster=roster, stickers=stickers,
                         main=[_entrada(cedula_key="2222222", nombre_norm="beto dos")])
    assert set(_by_key(resultado)) == {"1111111", "2222222"}


def test_cedula_sospechosa_ignores_a_float_tail():
    # 10 digits starting with "1" is fine; a naive digit-strip of the ".0" tail would
    # read 11 digits ("12345678900") and flag it.
    assert dep.cedula_sospechosa("1234567890.0") is False
    assert dep.cedula_sospechosa(1234567890.0) is False
    assert dep.cedula_sospechosa("1234.0") is True  # 4 digits


def test_main_only_profile_flags_come_from_its_own_row():
    main = [
        _entrada(cedula_key="12345", nombre_norm="ana uno", nombre="Ana Uno"),  # 5 digits
        _entrada(cedula_key="7654321", nombre_norm="prueba migracion", nombre="Prueba Migracion"),
        _entrada(cedula_key="7000001", nombre_norm="carla tres", nombre="Carla Tres", no_persona=True),
    ]
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert perfiles["12345"].cedula_sospechosa is True
    assert perfiles["7654321"].es_cuenta_no_persona is True
    assert perfiles["7000001"].es_cuenta_no_persona is True  # bundle-precomputed flag
    assert perfiles["7000001"].cedula_sospechosa is False


def test_main_only_suspicious_inactive_codeless_rows_collapse_into_grupo_externos():
    main = [
        _entrada(cedula_key="12345", nombre_norm="ana uno", nombre="Ana Uno"),
        _entrada(cedula_key="7654321", nombre_norm="beto dos", nombre="Beto Dos"),
    ]
    resultado = _depurar(main=main)
    assert list(_by_key(resultado)) == ["7654321"]
    assert resultado.grupo_externos["n_colapsados"] == 1
    assert resultado.grupo_externos["detalle"][0]["identificacion"] == "12345"


# ── PII must never reach the logs ───────────────────────────────────────────


def test_alias_nombres_collision_log_is_redacted(caplog):
    perfiles = {
        "8123456": _perfil("8123456", "zoraida quintero"),
        "8654321": _perfil("8654321", "zoraida quintero"),
    }
    with caplog.at_level(logging.DEBUG):
        alias = dep.alias_nombres(perfiles, ["Zoraida Quintero"])
    assert alias == {"zoraida quintero": "8123456"}  # first-wins is unchanged
    assert caplog.records  # the collision IS still logged (as a count)...
    assert "zoraida" not in caplog.text.lower()  # ...but never with a name
    assert "8123456" not in caplog.text and "8654321" not in caplog.text  # ...nor a cédula


def test_depurar_never_logs_names_or_cedulas(caplog):
    roster = {"8123456": _roster_entry("8123456", "Zoraida Quintero", correo="zq@example.com")}
    main = [
        _entrada(cedula_key="8654321", nombre_norm="zoraida quintero", nombre="Zoraida Quintero",
                 telefono="3105550123", correo="zq@example.com"),
        _entrada(cedula_key="8654321", nombre_norm="otra persona", nombre="Otra Persona"),  # duplicate
        _entrada(cedula_key="", nombre_norm="sin cedula alguna", nombre="Sin Cedula Alguna"),
    ]
    with caplog.at_level(logging.DEBUG):
        _depurar(roster=roster, main=main, survey=["Zoraida Quintero"])
    texto = caplog.text.lower()
    for secreto in ("zoraida", "quintero", "8123456", "8654321", "3105550123", "zq@example.com", "otra persona"):
        assert secreto not in texto


# ══════════════════════════════════════════════════════════════════════════
# Adversarial-review fixes (C1, C2, W2, W3, W4 + cheap suggestions)
# ══════════════════════════════════════════════════════════════════════════

import itertools  # noqa: E402
import re  # noqa: E402


def _front_key_pre_c3(raw):
    """STALE twin of the frontend key from BEFORE the C3 fix: it implements the
    old rule (Unicode `\\D` strip, leading zeros verbatim) and knows nothing about
    the float-artifact ".0" tail. Kept only for the pre-C3 tests below; use
    `_front_key_v2` for anything that must follow the current `cedulaKey`."""
    return re.sub(r"\D", "", str("" if raw is None else raw))


# ── C1: the join key mirrors the frontend (zero-preserving) ─────────────────


@pytest.mark.parametrize("crudo, esperado", [
    ("0012345", "0012345"),      # leading zeros kept
    ("12.345.678", "12345678"),  # separators dropped
    (" 12 345 ", "12345"),
    ("12345.0", "12345"),        # a lone ".0" float artifact is dropped
    ("12345.00", "1234500"),     # C3: ".00" is NOT an artifact, the dot is a separator
    ("166.000", "166000"),       # C3: thousands-separated cédula keeps every digit
    (12345.0, "12345"),
    (12345, "12345"),
    ("0000000", "0000000"),      # placeholder keeps its zeros
    ("0", "0"),
    ("", ""), ("   ", ""), (None, ""), (float("nan"), ""), ("abc", ""), ("N/A", ""),
])
def test_cedula_key_is_zero_preserving(crudo, esperado):
    assert dep._cedula_key(crudo) == esperado


@pytest.mark.parametrize("crudo, esperado", [
    ("0012345", "12345"), ("12345", "12345"), ("12.0345", "120345"), ("0000000", "0000000"),
    ("0", "0"), ("", ""), (None, ""), ("abc", ""),
])
def test_cedula_alias_key_strips_leading_zeros_but_keeps_placeholders(crudo, esperado):
    assert dep._cedula_alias_key(crudo) == esperado


def test_cedula_key_huge_inputs_stay_fast():
    enorme = "9" * 200_000 + ".0"
    t0 = time.perf_counter()
    assert dep._cedula_key(enorme) == "9" * 200_000
    assert dep._cedula_alias_key("0" * 200_000 + "7") == "7"
    assert time.perf_counter() - t0 < 1.0


def test_c1_reproduced_zero_padded_main_row_and_unpadded_sticker():
    main = [_entrada(cedula_key="01234567", nombre_norm="ana gomez", nombre="Ana Gomez")]
    stickers = [_sticker("1234567", fecha_creacion="2026-09-01T00:00:00Z")]
    resultado = _depurar(main=main, stickers=stickers)
    fila = _by_key(resultado)["01234567"]
    assert fila["identificacion"] == "01234567"  # the key is NOT zero-stripped
    assert fila["ultimo_sticker"] == "2026-09-01"  # the sticker IS attributed...
    assert "1234567" in fila["cedulas_unificadas"]  # ...and the other form is exported


def test_c1_reverse_unpadded_row_and_zero_padded_sticker():
    roster = {"1234567": _roster_entry("1234567", "Ana Gomez")}
    stickers = [_sticker("0001234567", fecha_creacion="2026-09-01T00:00:00Z")]
    fila = _by_key(_depurar(roster=roster, stickers=stickers))["1234567"]
    assert fila["ultimo_sticker"] == "2026-09-01"
    assert fila["cedulas_unificadas"] == ["0001234567"]


def test_c1_exact_own_key_sticker_exports_no_alias():
    main = [_entrada(cedula_key="01234567", nombre_norm="ana gomez")]
    stickers = [_sticker("01234567", fecha_creacion="2026-09-01T00:00:00Z"),
                _sticker(" 01.234.567 ", fecha_creacion="2026-09-02T00:00:00Z")]
    fila = _by_key(_depurar(main=main, stickers=stickers))["01234567"]
    assert fila["cedulas_unificadas"] == []
    assert fila["ultimo_sticker"] == "2026-09-02"


def test_c1_alias_registered_once_however_many_stickers_carry_it():
    roster = {"1234567": _roster_entry("1234567", "Ana Gomez")}
    stickers = [_sticker("01234567", fecha_creacion="2026-09-01T00:00:00Z") for _ in range(3)]
    perfiles, _ = dep.fusionar_identidad(stickers, roster, EMPTY_REF)
    assert perfiles["1234567"].n_stickers == 3
    assert perfiles["1234567"].cedulas_unificadas == ("01234567",)


def test_c1_zero_padded_and_unpadded_different_people_stay_separate_without_pollution():
    roster = {"1234567": _roster_entry(
        "1234567", "Ana Uno", correo="ana@example.com", correo_contacto="ana@example.com",
        tarjeta_profesional="TP-ANA", codigo="011",
    )}
    main = [
        _entrada(cedula_key="01234567", nombre_norm="beto dos", nombre="Beto Dos", np="P4",
                 correo="beto@example.com", tarjeta_profesional="TP-BETO", codigo="022",
                 telefono="3001112222", no_persona=True),
        _entrada(cedula_key="1234567", nombre_norm="ana uno", nombre="Ana Uno", np="P1"),
    ]
    resultado = _depurar(roster=roster, main=main)
    por_clave = _by_key(resultado)
    assert set(por_clave) == {"1234567", "01234567"}
    ana, beto = por_clave["1234567"], por_clave["01234567"]
    assert (ana["nombre_completo"], ana["tarjeta_profesional"], ana["correo_contacto"]) == (
        "Ana Uno", "TP-ANA", "ana@example.com")
    assert (beto["nombre_completo"], beto["tarjeta_profesional"], beto["correo_contacto"]) == (
        "Beto Dos", "TP-BETO", "beto@example.com")
    assert (ana["codigo"], beto["codigo"]) == ("011", "022")
    assert (ana["np"], beto["np"]) == ("P1", "P4")
    assert (ana["num_telefono"], beto["num_telefono"]) == ("", "3001112222")
    assert (ana["no_persona"], beto["no_persona"]) == (False, True)
    assert ana["cedulas_unificadas"] == [] and beto["cedulas_unificadas"] == []


def test_c1_exact_keys_win_and_an_ambiguous_zero_variant_is_not_guessed():
    roster = {"1234567": _roster_entry("1234567", "Ana Uno")}
    main = [_entrada(cedula_key="01234567", nombre_norm="beto dos")]
    stickers = [
        _sticker("1234567", fecha_creacion="2026-09-01T00:00:00Z"),      # exactly Ana
        _sticker("01234567", fecha_creacion="2026-09-02T00:00:00Z"),     # exactly Beto
        _sticker("001234567", fecha_creacion="2026-09-03T00:00:00Z"),    # matches both after stripping
    ]
    por_clave = _by_key(_depurar(roster=roster, main=main, stickers=stickers))
    assert (por_clave["1234567"]["ultimo_sticker"], por_clave["01234567"]["ultimo_sticker"]) == (
        "2026-09-01", "2026-09-02")  # the ambiguous sticker went to nobody
    assert por_clave["1234567"]["cedulas_unificadas"] == []
    assert por_clave["01234567"]["cedulas_unificadas"] == []


def test_c1_same_name_zero_padded_and_unpadded_unify_through_dp2_and_export_the_other_key():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    main = [_entrada(cedula_key="01234567", nombre_norm="juan perez", nombre="Juan Perez")]
    resultado = _depurar(roster=roster, main=main)
    assert list(_by_key(resultado)) == ["1234567"]  # Firestore-backed wins the full tie
    assert _by_key(resultado)["1234567"]["cedulas_unificadas"] == ["01234567"]


def test_c1_same_name_roster_duplicates_export_the_absorbed_key():
    roster = {"a": _roster_entry("12345678", "Ana Uno"), "b": _roster_entry("12.345.678", "ANA UNO")}
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert list(perfiles) == ["12345678"]
    assert perfiles["12345678"].cedulas_unificadas == ("12.345.678",)


def test_c1_different_name_roster_duplicates_export_the_absorbed_key_too():
    roster = {"a": _roster_entry("12345678", "Ana Uno"), "b": _roster_entry("12345678.0", "Otra Persona")}
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert list(perfiles) == ["12345678"]
    assert perfiles["12345678"].cedulas_unificadas == ("12345678.0",)


def test_c1_identical_roster_duplicate_string_is_not_a_self_alias():
    roster = {"a": _roster_entry("12345678", "Ana Uno"), "b": _roster_entry("12345678", "Ana Uno")}
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert perfiles["12345678"].cedulas_unificadas == ()


@pytest.mark.parametrize("cedula", ["0000000", "000", "0"])
def test_c1_placeholder_cedulas_are_unaffected(cedula):
    main = [_entrada(cedula_key=cedula, nombre_norm="alguien")]
    # "00" is a different placeholder: it must never attribute onto another one.
    stickers = [_sticker(cedula, fecha_creacion="2026-09-01T00:00:00Z"),
                _sticker("00", fecha_creacion="2026-09-01T00:00:00Z")]
    perfiles, revision = dep.fusionar_identidad(stickers, {}, _bundle(main=main))
    assert set(perfiles) == {cedula}
    p = perfiles[cedula]
    assert p.cedula_sospechosa is (cedula != "0000000")
    assert revision == ()
    assert p.n_stickers == 1  # only the exact placeholder matches
    assert p.cedulas_unificadas == ()


def test_c1_distinct_placeholders_never_merge():
    main = [_entrada(cedula_key="0000000", nombre_norm="uno"), _entrada(cedula_key="000", nombre_norm="dos")]
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert set(perfiles) == {"0000000", "000"}


@pytest.mark.parametrize("cedula_sticker", [None, "", "   ", "abc", float("nan"), "N/A"])
def test_c1_blank_or_digitless_sticker_cedulas_are_skipped(cedula_sticker):
    perfiles, _ = dep.fusionar_identidad(
        [_sticker(cedula_sticker, fecha_creacion="2026-09-01T00:00:00Z")],
        {"1234567": _roster_entry("1234567", "Ana Uno")}, EMPTY_REF,
    )
    assert perfiles["1234567"].n_stickers == 0 and perfiles["1234567"].cedulas_unificadas == ()


def test_c1_frontend_payload_contract_every_sticker_cedula_form_resolves_to_its_row():
    """The frontend routes a sticker by `cedulaKey(sticker.identificacion)` and
    finds the row through `identidad_key` / `cedulas_unificadas`
    (`cedulaFusionadaACedulaSurvivor`). So, for every sticker the backend
    attributed to a person, its frontend key MUST equal that row's own key or
    appear in its `cedulas_unificadas` — otherwise the frontend builds a ghost
    row."""
    roster = {
        "1234567": _roster_entry("1234567", "Ana Uno"),
        "9876543": _roster_entry("9876543", "Carla Tres"),
    }
    main = [
        _entrada(cedula_key="01234567", nombre_norm="beto dos", nombre="Beto Dos"),
        _entrada(cedula_key="7000001", nombre_norm="carla tres", nombre="Carla Tres"),   # unified into Carla
        _entrada(cedula_key="005555555", nombre_norm="dario cuatro", nombre="Dario Cuatro"),
    ]
    formas = {
        "Ana Uno": ["1234567", "1.234.567"],
        "Beto Dos": ["01234567", "01.234.567"],
        "Carla Tres": ["9876543", "009876543", "7000001", "07000001"],
        "Dario Cuatro": ["005555555", "5555555", "0000005555555"],
    }
    stickers = [
        _sticker(forma, fecha_creacion="2026-09-01T00:00:00Z", nombre_completo=nombre)
        for nombre, lista in formas.items() for forma in lista
    ]
    resultado = _depurar(roster=roster, main=main, stickers=stickers)
    filas = {f["nombre_completo"]: f for f in resultado.inspectores}
    assert set(filas) == set(formas)  # nobody collapsed / lost
    for nombre, lista in formas.items():
        fila = filas[nombre]
        claves_de_la_fila = {
            _front_key_pre_c3(fila["identidad_key"] or fila["identificacion"]),
            *(_front_key_pre_c3(alias) for alias in fila["cedulas_unificadas"]),
        }
        for forma in lista:
            assert _front_key_pre_c3(forma) in claves_de_la_fila, (nombre, forma)
        assert fila["ultimo_sticker"] == "2026-09-01"


def test_c1_result_is_independent_of_input_order():
    roster_items = [
        ("a", _roster_entry("1234567", "Ana Uno")),
        ("b", _roster_entry("7654321", "Carla Tres")),
    ]
    main_rows = [
        _entrada(cedula_key="01234567", nombre_norm="beto dos", nombre="Beto Dos"),
        _entrada(cedula_key="8000001", nombre_norm="carla tres", nombre="Carla Tres", creado_en="2020-01-01"),
        _entrada(cedula_key="9999999", nombre_norm="dario cuatro", nombre="Dario Cuatro"),
    ]
    stickers = [
        _sticker("1234567", fecha_creacion="2026-09-01T00:00:00Z"),
        _sticker("01234567", fecha_creacion="2026-09-02T00:00:00Z"),
        _sticker("001234567", fecha_creacion="2026-09-03T00:00:00Z"),  # ambiguous
        _sticker("8000001", fecha_creacion="2026-09-04T00:00:00Z"),
        _sticker("07654321", fecha_creacion="2026-09-05T00:00:00Z"),
    ]

    def canonico(resultado):
        return {
            f["identidad_key"]: (f["ultimo_sticker"], tuple(sorted(f["cedulas_unificadas"])), f["estado_sugerido"])
            for f in resultado.inspectores
        }

    esperado = canonico(_depurar(roster=dict(roster_items), main=main_rows, stickers=stickers))
    assert len(esperado) >= 3
    for roster_perm in itertools.permutations(roster_items):
        for main_perm in itertools.permutations(main_rows):
            for stickers_perm in (stickers, stickers[::-1], stickers[2:] + stickers[:2]):
                resultado = _depurar(roster=dict(roster_perm), main=main_perm, stickers=stickers_perm)
                assert canonico(resultado) == esperado


# ── C2: no_persona is recomputed after a merge ──────────────────────────────


def test_c2_loser_flagged_no_persona_in_the_bundle_flags_the_survivor():
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", no_persona=True),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]  # A survives (n_stickers>0)
    resultado = _depurar(main=main, stickers=stickers)
    assert list(_by_key(resultado)) == ["7000001"]
    fila = _by_key(resultado)["7000001"]
    assert fila["no_persona"] is True and fila["estado_sugerido"] == "no_persona"
    assert fila["cedulas_unificadas"] == ["7000002"]


def test_c2_loser_import_local_correo_backfilled_into_survivor_flags_it():
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno"),  # empty correo
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="migrated@import.local"),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]
    fila = _by_key(_depurar(main=main, stickers=stickers))["7000001"]
    assert fila["correo_contacto"] == "migrated@import.local"
    assert fila["no_persona"] is True and fila["estado_sugerido"] == "no_persona"


def test_c2_survivor_flagged_loser_not_stays_flagged():
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", no_persona=True),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com"),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]
    fila = _by_key(_depurar(main=main, stickers=stickers))["7000001"]
    assert fila["no_persona"] is True and fila["estado_sugerido"] == "no_persona"


def test_c2_clean_pair_stays_not_no_persona():
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com"),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]
    fila = _by_key(_depurar(main=main, stickers=stickers))["7000001"]
    assert fila["no_persona"] is False and fila["correo_contacto"] == "ana@example.com"


def test_c2_unificar_duplicados_recomputes_the_flag_from_merged_fields():
    survivor = _perfil("A", "juan perez", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("B", "juan perez", no_persona_ref=True)
    resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
    assert resultado["A"].no_persona_ref is True and resultado["A"].es_cuenta_no_persona is True
    assert survivor.es_cuenta_no_persona is False  # the caller's input stays untouched

    survivor2 = _perfil("A", "juan perez", ultimo_sticker=date(2026, 8, 1))
    perdedor2 = _perfil("B", "juan perez", correo="x@import.local")
    resultado2, _ = dep.unificar_duplicados({"A": survivor2, "B": perdedor2})
    assert resultado2["A"].correo == "x@import.local" and resultado2["A"].es_cuenta_no_persona is True


def test_c2_cedula_sospechosa_is_kept_from_the_survivor_original_cedula():
    survivor = _perfil("1234567", "juan perez", ultimo_sticker=date(2026, 8, 1), cedula_sospechosa=False)
    perdedor = _perfil("999", "juan perez", cedula_sospechosa=True)
    resultado, _ = dep.unificar_duplicados({"1234567": survivor, "999": perdedor})
    assert resultado["1234567"].cedula_sospechosa is False
    # ...and the reverse: a suspicious survivor is not "cured" by a healthy loser
    survivor_s = _perfil("999", "juan perez", ultimo_sticker=date(2026, 8, 1), cedula_sospechosa=True)
    perdedor_s = _perfil("1234567", "juan perez", cedula_sospechosa=False)
    resultado_s, _ = dep.unificar_duplicados({"999": survivor_s, "1234567": perdedor_s})
    assert resultado_s["999"].cedula_sospechosa is True


def test_c2_a_flag_set_only_on_the_loser_by_its_own_correo_does_not_leak_to_a_clean_survivor():
    survivor = _perfil("A", "juan perez", correo="juan@example.com", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("B", "juan perez", correo="b@import.local", es_cuenta_no_persona=True)
    resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
    assert resultado["A"].es_cuenta_no_persona is False  # survivor's own correo/name are clean


# ── W2: cedula_sospechosa consistency with the join key ─────────────────────


@pytest.mark.parametrize("cedula, esperado", [
    ("1234567890.0", False),   # old: 11 digits -> True (a ".0" tail was counted)
    ("12345.0", True),         # old: "123450" = 6 digits -> False
    ("0012345678", True),      # zero-preserving: 10 digits starting with "0"
    ("0012345", False),        # 7 digits (zeros count, exactly like the join key)
    ("12345", True), ("123456", False), ("1234567890", False), ("2234567890", True),
    ("12345678901", True), ("", True), (None, True), ("   ", True), ("abc", True),
    ("0000000", False),        # 7 digits: the placeholder length is fine on its own
])
def test_cedula_sospechosa_pinned_behavior(cedula, esperado):
    assert dep.cedula_sospechosa(cedula) is esperado


@pytest.mark.parametrize("cedula", ["1.234.567", "0012345678", "12345.0", " 123456 ", "12 345 678 90", 1234567.0])
def test_cedula_sospechosa_reads_the_same_digits_as_the_join_key(cedula):
    digitos = dep._cedula_key(cedula)
    esperado = (not digitos) or not (6 <= len(digitos) <= 10) or (len(digitos) == 10 and not digitos.startswith("1"))
    assert dep.cedula_sospechosa(cedula) is esperado


# ── W3: known state until slice 08 (holder clearing) ────────────────────────


def test_known_state_until_slice_08_two_profiles_can_hold_the_same_codigo():
    # KNOWN, DELIBERATE gap: `main.codigo` now backfills empty Firestore codes but
    # slice 08 (clear the wrong holder, D13) is NOT in yet, so two profiles can
    # hold the same `codigo`. The flag SEGUIMIENTO_DEPURACION MUST NOT be flipped
    # before slice 08 lands. When slice 08 lands this test must be REPLACED by
    # the holder-clearing scenarios (tasks 9.1-9.6).
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="041")}
    main = [_entrada(cedula_key="2222222", nombre_norm="beto dos", codigo="041")]
    por_clave = _by_key(_depurar(roster=roster, main=main))
    assert por_clave["1111111"]["codigo"] == por_clave["2222222"]["codigo"] == "041"


# ── W4: cross-source cédula mismatch and the creado_en tie-break ────────────


def test_full_tie_between_dateless_profiles_keeps_the_firestore_anchor():
    # Proves ONLY the full-tie case (both `creado_en` empty): Firestore-backed is
    # the last tie-break. See the companion test for the production shape.
    roster = {"1111111": _roster_entry("1111111", "Juan Perez")}
    main = [_entrada(cedula_key="2222222", nombre_norm="juan perez", nombre="Juan Perez")]
    resultado = _depurar(roster=roster, main=main)
    assert list(_by_key(resultado)) == ["1111111"]
    assert _by_key(resultado)["1111111"]["cedulas_unificadas"] == ["2222222"]


def test_production_shape_dated_main_row_beats_a_dateless_roster_row_and_exports_the_roster_key():
    # Production: the roster sends `creado_en=""` and `main` rows carry a real
    # date, so the notebook rule (oldest `creado_en` wins, a blank never beats a
    # real date) makes the MAIN cédula the key (D10). The roster cédula stays
    # resolvable through `cedulas_unificadas` (the frontend maps it there).
    roster = {"1111111": _roster_entry("1111111", "Juan Perez", creado_en="")}
    main = [_entrada(cedula_key="2222222", nombre_norm="juan perez", nombre="Juan Perez",
                     creado_en="2021-05-05")]
    fila = _by_key(_depurar(roster=roster, main=main))["2222222"]
    assert fila["identificacion"] == "2222222"
    assert fila["cedulas_unificadas"] == ["1111111"]


def test_production_shape_sticker_on_the_roster_cedula_still_lands_on_the_surviving_row():
    roster = {"1111111": _roster_entry("1111111", "Juan Perez", creado_en="")}
    main = [_entrada(cedula_key="2222222", nombre_norm="juan perez", nombre="Juan Perez",
                     creado_en="2021-05-05")]
    stickers = [_sticker("1111111", fecha_creacion="2026-09-01T00:00:00Z")]
    resultado = _depurar(roster=roster, main=main, stickers=stickers)
    # a sticker on the roster side outranks the date: the roster cédula survives
    # and the main cédula is exported instead — either way ONE row, one sticker.
    (fila,) = resultado.inspectores
    assert fila["ultimo_sticker"] == "2026-09-01"
    assert {fila["identidad_key"], *fila["cedulas_unificadas"]} == {"1111111", "2222222"}


# ── Cheap suggestions ───────────────────────────────────────────────────────


def test_main_profile_takes_nombre_norm_from_the_entry_when_present():
    main = [_entrada(cedula_key="1111111", nombre_norm="ana gomez", nombre="ANA GÓMEZ PÉREZ")]
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert perfiles["1111111"].nombre_norm == "ana gomez"      # the entry's own norm wins
    assert perfiles["1111111"].nombre_completo == "ANA GÓMEZ PÉREZ"


def test_main_profile_normalizes_nombre_when_the_entry_has_no_nombre_norm():
    main = [_entrada(cedula_key="1111111", nombre_norm="", nombre="  ANA   GÓMEZ ")]
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert perfiles["1111111"].nombre_norm == "ana gomez"


def test_reference_index_uses_the_same_nombre_helper():
    roster = {"1111111": _roster_entry("1111111", "Ana Gomez")}
    vercel = [_entrada(cedula_key="9999999", nombre_norm="", nombre="ANA GÓMEZ", np="P2")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(vercel=vercel))
    assert perfiles["1111111"].en_vercel is True and perfiles["1111111"].np_vercel == "P2"


def test_unificar_duplicados_alias_filter_compares_cedula_keys_not_raw_strings():
    survivor = _perfil("12345", "juan perez", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("12.345", "juan perez", cedulas_unificadas=("12345", " 12 345 ", "777"))
    resultado, _ = dep.unificar_duplicados({"12345": survivor, "12.345": perdedor})
    assert resultado["12345"].cedulas_unificadas == ("777",)


def test_unificar_duplicados_keeps_the_survivor_creado_en_and_never_backfills_it():
    survivor = _perfil("A", "juan perez", ultimo_sticker=date(2026, 8, 1), creado_en="")
    perdedor = _perfil("B", "juan perez", creado_en="2019-01-01")
    resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
    assert resultado["A"].creado_en == ""
    con_fecha = _perfil("A", "juan perez", ultimo_sticker=date(2026, 8, 1), creado_en="2022-02-02")
    resultado, _ = dep.unificar_duplicados({"A": con_fecha, "B": perdedor})
    assert resultado["A"].creado_en == "2022-02-02"


# ══════════════════════════════════════════════════════════════════════════
# Review 2026-09-19 round 2 — C3 (one cédula float-tail rule), W5-W7, S2
# ══════════════════════════════════════════════════════════════════════════

import re  # noqa: E402
from pathlib import Path  # noqa: E402

from app.services import cedula_utils  # noqa: E402

_SEGUIMIENTO_JS = Path(__file__).resolve().parents[3] / "web" / "js" / "seguimiento.js"


def _front_key_v2(raw):
    """Python mirror of the FRONTEND `cedulaKey`, generated from the SAME
    documented rule (`cedula_utils.COLA_FLOTANTE_PATRON`, ASCII digits): a lone
    float-artifact ".0" tail is dropped, then every non-digit is stripped. Digit
    extraction and the float-artifact rule match; it is NOT exact for exotic
    whitespace around a ".0" tail (JS `\\s` vs Python `re.ASCII`: BOM, NEL,
    control chars) — a known, accepted divergence (design D-CEDDEC)."""
    texto = "" if raw is None else str(raw)
    cola = re.match(cedula_utils.COLA_FLOTANTE_PATRON, texto, flags=re.ASCII)
    return re.sub(r"\D", "", cola.group(1) if cola else texto, flags=re.ASCII)


def _fe_route(resultado, cedula_sticker):
    """The row the frontend would route a sticker cédula to (or None): its own
    key or any `cedulas_unificadas` entry, all compared through the frontend key."""
    objetivo = _front_key_v2(cedula_sticker)
    filas = [
        fila for fila in resultado.inspectores
        if objetivo and objetivo in {
            _front_key_v2(fila["identidad_key"] or fila["identificacion"]),
            *(_front_key_v2(alias) for alias in fila["cedulas_unificadas"]),
        }
    ]
    return filas[0]["identidad_key"] if len(filas) == 1 else None


# ── the shared helper: ONE rule ─────────────────────────────────────────────


@pytest.mark.parametrize("texto, esperado", [
    ("1234567.0", "1234567"),        # float artifact: exactly one ".0"
    (" 1234567.0 ", "1234567"),      # surrounding whitespace allowed
    ("0.0", "0"), ("1.0", "1"),
    ("166.000", "166.000"),          # thousands separator, NOT a float artifact
    ("31837630.00", "31837630.00"),  # ".00" is not the artifact either
    ("12.000", "12.000"),
    ("1.234.567", "1.234.567"), ("1234567.0.0", "1234567.0.0"),
    ("1234567.05", "1234567.05"), ("1234567.0a", "1234567.0a"),
    (".0", ".0"), ("", ""), ("   ", "   "), ("abc.0", "abc.0"), ("-1234.0", "-1234.0"),
    ("١٢٣.0", "١٢٣.0"),              # non-ASCII digits are not digits here
])
def test_quitar_cola_flotante_only_a_lone_dot_zero_at_the_end(texto, esperado):
    assert cedula_utils.quitar_cola_flotante(texto) == esperado


@pytest.mark.parametrize("texto, esperado", [
    ("166.000", "166000"), ("1.234.567", "1234567"), ("1234567.0", "1234567"),
    ("12.000", "12000"), (" 1234567 ", "1234567"), ("31837630.00", "3183763000"),
    ("0012345", "0012345"), ("0.0", "0"), ("0", "0"), ("-1234567", "1234567"),
    ("CC 1234567", "1234567"), ("١٢٣٤٥٦٧", ""), ("１２３４５６７", ""), ("", ""), ("abc", ""),
])
def test_solo_digitos_applies_the_tail_rule_then_strips_non_digits(texto, esperado):
    assert cedula_utils.solo_digitos(texto) == esperado


def test_cedula_utils_huge_digit_strings_stay_fast():
    enorme = "9" * 300_000
    t0 = time.perf_counter()
    assert cedula_utils.solo_digitos(enorme + ".0") == enorme
    assert cedula_utils.solo_digitos(enorme + ".00") == enorme + "00"
    assert cedula_utils.solo_digitos(" " * 100_000 + "7") == "7"
    assert time.perf_counter() - t0 < 1.0


@pytest.mark.parametrize("crudo, esperado", [
    (None, ""), (float("nan"), ""), (float("inf"), ""), ("", ""), ("   ", ""),
    (0, "0"), (0.0, "0"), (1234567.0, "1234567"), (166000, "166000"),
    ("166.000", "166000"), ("1234567.0", "1234567"), ("1234567.00", "123456700"),
])
def test_cedula_key_c3_semantics(crudo, esperado):
    assert dep._cedula_key(crudo) == esperado


# ── C3 reproduced: thousands-formatted roster cédula vs float artifact ──────


def test_c3_roster_thousands_dotted_cedula_still_attributes_its_sticker():
    roster = {"a": _roster_entry("166.000", "Ana Vieja", codigo="A2")}
    resultado = _depurar(
        roster=roster, stickers=[_sticker("166000", fecha_creacion="2026-09-01T00:00:00Z")],
    )
    fila = _by_key(resultado)["166000"]  # the key is the digits-only form
    assert fila["nombre_completo"] == "Ana Vieja"
    assert fila["ultimo_sticker"] == "2026-09-01"
    assert fila["cedula_sospechosa"] is False  # 6 digits
    assert fila["cedulas_unificadas"] == ["166.000"]  # the RAW roster form is exported
    assert resultado.grupo_externos is None or resultado.grupo_externos["n_colapsados"] == 0
    assert _fe_route(resultado, "166000") == "166000"
    assert _fe_route(resultado, "166.000") == "166000"


def test_c3_roster_float_artifact_and_plain_sticker_agree_backend_and_frontend():
    roster = {"a": _roster_entry("1234567.0", "Ana Lopez", codigo="A1")}
    resultado = _depurar(roster=roster, stickers=[_sticker("1234567", fecha_creacion="2026-09-01T00:00:00Z")])
    fila = _by_key(resultado)["1234567"]
    assert fila["ultimo_sticker"] == "2026-09-01"  # backend attributes
    assert fila["cedulas_unificadas"] == ["1234567.0"]
    assert _fe_route(resultado, "1234567") == "1234567"  # frontend routes the same


def test_c3_profile_key_plain_and_sticker_carrying_a_float_tail():
    main = [_entrada(cedula_key="1234567", nombre_norm="ana lopez", nombre="Ana Lopez", codigo="A1")]
    resultado = _depurar(main=main, stickers=[_sticker("1234567.0", fecha_creacion="2026-09-01T00:00:00Z")])
    assert _by_key(resultado)["1234567"]["ultimo_sticker"] == "2026-09-01"
    assert _fe_route(resultado, "1234567.0") == "1234567"


def test_c3_two_distinct_rows_never_collapse_to_one_frontend_key():
    roster = {"a": _roster_entry("1234567.0", "Ana Uno", codigo="A1")}
    main = [_entrada(cedula_key="12345670", nombre_norm="beto dos", nombre="Beto Dos", np="P4", codigo="B2")]
    resultado = _depurar(roster=roster, main=main)
    claves = [_front_key_v2(f["identidad_key"]) for f in resultado.inspectores]
    assert sorted(claves) == ["1234567", "12345670"]  # two people, two frontend keys


def test_c3_overlay_matches_a_float_artifact_roster_cedula_by_cedula_not_by_name():
    roster = {"a": _roster_entry("1234567.0", "Ana Uno")}
    vercel = [_entrada(cedula_key="1234567", nombre_norm="otro nombre", np="P4", entidad="DAGMA", codigo="041")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(vercel=vercel))
    perfil = perfiles["1234567"]
    assert perfil.en_vercel is True and perfil.np_vercel == "P4"
    assert perfil.entidad_vercel == "DAGMA"  # only ever set by a match BY CÉDULA (D12)


@pytest.mark.parametrize("cedula, digitos, sospechosa", [
    ("166.000", "166000", False),    # 6 digits
    ("1234.000", "1234000", False),  # 7 digits
    ("1094.000", "1094000", False),
    ("16.000", "16000", True),       # 5 digits
    ("1.000", "1000", True),
    (0, "0", True), (0.0, "0", True), ("0.0", "0", True),
    ("1234567.0", "1234567", False), ("1234567890.0", "1234567890", False),
    ("12345.0", "12345", True), ("12345.00", "1234500", False),
])
def test_c3_cedula_sospechosa_thousands_dots_versus_float_artifact(cedula, digitos, sospechosa):
    assert dep._cedula_key(cedula) == digitos
    assert dep.cedula_sospechosa(cedula) is sospechosa


@pytest.mark.parametrize("identificacion, clave", [
    ("1.234.567", "1234567"), ("1234567.0", "1234567"), (1234567.0, "1234567"),
    (" 1234567 ", "1234567"), ("166.000", "166000"), ("0012345", "0012345"),
])
def test_c3_roster_profile_is_keyed_by_the_normalized_cedula_and_exports_the_raw_form(identificacion, clave):
    perfiles, _ = dep.fusionar_identidad([], {"a": _roster_entry(identificacion, "Ana Uno")}, EMPTY_REF)
    assert list(perfiles) == [clave]
    perfil = perfiles[clave]
    assert perfil.identidad_key == perfil.identificacion == clave
    raw = str(identificacion).strip()
    assert perfil.cedulas_unificadas == (() if raw == clave else (raw,))
    assert clave not in perfil.cedulas_unificadas  # never the row's own key


def test_c3_roster_duplicate_raw_forms_register_once_and_never_the_own_key():
    roster = {
        "a": _roster_entry("1234567.0", "Ana Uno"), "b": _roster_entry("1234567.0", "Ana Uno"),
        "c": _roster_entry("1.234.567", "Ana Uno"), "d": _roster_entry("1234567", "Ana Uno"),
    }
    perfiles, _ = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert list(perfiles) == ["1234567"]
    assert perfiles["1234567"].cedulas_unificadas == ("1234567.0", "1.234.567")


@pytest.mark.parametrize("identificacion", ["N/A", "abc", "-"])
def test_c3_roster_without_digits_keeps_its_raw_text_as_the_key(identificacion):
    perfiles, _ = dep.fusionar_identidad([], {"a": _roster_entry(identificacion, "Ana Uno")}, EMPTY_REF)
    assert list(perfiles) == [identificacion]
    assert perfiles[identificacion].cedulas_unificadas == ()


@pytest.mark.parametrize("identificacion", [None, "", "   ", float("nan")])
def test_c3_roster_blank_identificacion_is_skipped(identificacion):
    perfiles, _ = dep.fusionar_identidad([], {"a": _roster_entry(identificacion, "Ana Uno")}, EMPTY_REF)
    assert perfiles == {}


def test_c3_raw_roster_form_survives_a_survivor_merge_and_the_result_is_order_independent():
    roster = {"a": _roster_entry("1234567.0", "Juan Perez", codigo="A1"), "b": _roster_entry("7654321", "Juan Perez")}
    resultados = set()
    for items in (list(roster.items()), list(reversed(list(roster.items())))):
        resultado = _depurar(roster=dict(items), stickers=[_sticker("1234567", fecha_creacion="2026-09-01T00:00:00Z")])
        assert len(resultado.inspectores) == 1
        fila = resultado.inspectores[0]
        assert fila["identidad_key"] == "1234567" and fila["ultimo_sticker"] == "2026-09-01"
        resultados.add((fila["identidad_key"], tuple(sorted(fila["cedulas_unificadas"]))))
    assert resultados == {("1234567", ("1234567.0", "7654321"))}


def test_c3_frontend_payload_contract_decimal_tail_and_thousands_forms():
    roster = {
        "a": _roster_entry("166.000", "Ana Uno"),
        "b": _roster_entry("1234567.0", "Beto Dos"),
        "c": _roster_entry("1.234.568", "Carla Tres"),
        "d": _roster_entry(" 1234569 ", "Dario Cuatro"),
    }
    formas = {
        "Ana Uno": ["166.000", "166000"], "Beto Dos": ["1234567.0", "1234567", "1.234.567"],
        "Carla Tres": ["1.234.568", "1234568.0", "1234568"], "Dario Cuatro": [" 1234569 ", "1234569.0", "1.234.569"],
    }
    stickers = [
        _sticker(forma, fecha_creacion="2026-09-01T00:00:00Z", nombre_completo=nombre)
        for nombre, lista in formas.items() for forma in lista
    ]
    resultado = _depurar(roster=roster, stickers=stickers)
    filas = {f["nombre_completo"]: f for f in resultado.inspectores}
    assert set(filas) == set(formas)
    for nombre, lista in formas.items():
        fila = filas[nombre]
        claves = {
            _front_key_v2(fila["identidad_key"] or fila["identificacion"]),
            *(_front_key_v2(alias) for alias in fila["cedulas_unificadas"]),
        }
        for forma in lista:
            assert _front_key_v2(forma) in claves, (nombre, forma)
        assert fila["ultimo_sticker"] == "2026-09-01"


def test_c3_frontend_source_carries_the_exact_documented_regex():
    """Node-free cross-check: the JS `cedulaKey` embeds the SAME regex the backend
    documents (`COLA_FLOTANTE_PATRON`), so the mirror cannot drift silently."""
    fuente = _SEGUIMIENTO_JS.read_text(encoding="utf-8")
    assert f"/{cedula_utils.COLA_FLOTANTE_PATRON}/" in fuente


def test_c3_backend_modules_share_the_helper_no_local_decimal_regex():
    from app.services import inspectores_referencia as ref

    for modulo in (dep, ref):
        fuente = inspect.getsource(modulo)
        assert "cedula_utils" in fuente
        assert r"\.0+$" not in fuente


# ── W5: the merge flag is NOT a pure recompute (documented + pinned) ────────


def test_w5_survivor_with_a_real_correo_keeps_no_persona_false_despite_a_flagged_loser_correo():
    survivor = _perfil("A", "juan perez", correo="juan@example.com", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("B", "juan perez", correo="b@import.local", es_cuenta_no_persona=True)
    resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
    fusionado = resultado["A"]
    assert fusionado.correo == "juan@example.com"  # backfill only fills EMPTY fields
    assert fusionado.es_cuenta_no_persona is False  # the loser's heuristic flag is dropped
    assert fusionado.no_persona_ref is False


def test_w5_only_the_bundle_flag_no_persona_ref_is_ored_into_the_survivor():
    survivor = _perfil("A", "juan perez", correo="juan@example.com", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("B", "juan perez", correo="b@import.local", no_persona_ref=True, es_cuenta_no_persona=True)
    resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
    assert resultado["A"].no_persona_ref is True
    assert resultado["A"].es_cuenta_no_persona is True


def test_w5_an_empty_survivor_correo_backfills_the_flagged_one_and_the_flag_follows():
    survivor = _perfil("A", "juan perez", correo="", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("B", "juan perez", correo="b@import.local", es_cuenta_no_persona=True)
    resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
    assert resultado["A"].correo == "b@import.local"
    assert resultado["A"].es_cuenta_no_persona is True


# ── W6: unparseable creado_en is blank in the survivor tie-break ────────────


def test_w6_unparseable_creado_en_never_beats_a_real_date_in_the_tie_break():
    basura = _perfil("A", "juan perez", creado_en="0000-garbage")  # lexicographically SMALLEST
    real = _perfil("B", "juan perez", creado_en="2024-05-05T00:00:00Z")
    for orden in ({"A": basura, "B": real}, {"B": real, "A": basura}):
        resultado, fusiones = dep.unificar_duplicados(dict(orden))
        assert list(resultado) == ["B"]
        assert fusiones[0]["survivor"] == "B"


def test_w6_two_unparseable_creado_en_fall_through_to_firestore_then_first_seen():
    a = _perfil("A", "juan perez", creado_en="zzz")
    b = _perfil("B", "juan perez", creado_en="aaa", firestore_backed=True)
    resultado, _ = dep.unificar_duplicados({"A": a, "B": b})
    assert list(resultado) == ["B"]


# ── W7: registrar keeps the zero-stripped ambiguity map truthful ────────────


def test_w7_registrar_marks_a_stripped_key_claimed_by_two_profiles_as_ambiguous():
    a = _perfil("0000123456", "ana uno")
    b = _perfil("9999999", "beto dos")
    indice = dep._IndiceAlias({"a": a, "b": b})
    assert indice.resolver("00123456")[0] is a  # only A claims the stripped key "123456"
    indice.registrar(b, "0123456")  # B now also answers to a form stripping to "123456"
    assert indice.resolver("00123456")[0] is None  # ambiguous: never guessed onto either
    assert indice.resolver("0123456")[0] is b  # the registered exact form is B's
    assert indice.resolver("0000123456")[0] is a  # exact keys are unaffected


def test_w7_registrar_of_the_same_profile_keeps_the_stripped_key_resolvable():
    a = _perfil("0123456", "ana uno")
    indice = dep._IndiceAlias({"a": a})
    indice.registrar(a, "00123456")
    assert indice.resolver("000123456")[0] is a


# ── S2: np / entidad are trimmed in the overlay ─────────────────────────────


def test_s2_overlay_trims_np_and_entidad():
    roster = {"a": _roster_entry("1234567", "Ana Uno")}
    vercel = [_entrada(cedula_key="1234567", nombre_norm="ana uno", np="  P2  ", entidad="  DAGMA ")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(vercel=vercel))
    assert perfiles["1234567"].np_vercel == "P2"
    assert perfiles["1234567"].entidad_vercel == "DAGMA"


# ── Review 2026-09-19 round 3 — S3: phone digits are ASCII-only ─────────────


@pytest.mark.parametrize("crudo, esperado", [
    ("3001234567", "3001234567"),                       # normal phone unchanged
    ("300-123 4567", "3001234567"),                     # separators stripped
    ("٣٠٠١٢٣٤٥٦٧", ""),                                 # Arabic-Indic digits are not digits
    ("３００１２３４５６７", ""),                          # full-width digits are not digits
])
def test_s3_main_telefono_is_ascii_digits_only_on_new_profile(crudo, esperado):
    main = [_entrada(cedula_key="12345678", nombre_norm="ana gomez", nombre="Ana Gomez", telefono=crudo)]
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert perfiles["12345678"].num_telefono == esperado


@pytest.mark.parametrize("crudo, esperado", [
    ("3001234567", "3001234567"),
    ("٣٠٠١٢٣٤٥٦٧", ""),
    ("３００１２３４５６７", ""),
])
def test_s3_main_telefono_is_ascii_digits_only_when_backfilling(crudo, esperado):
    roster = {"12345678": _roster_entry("12345678", "Juan Perez")}
    main = [_entrada(cedula_key="12345678", nombre_norm="juan perez", telefono=crudo)]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(main=main))
    assert perfiles["12345678"].num_telefono == esperado
