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
    # equal score, no creado_en, neither Firestore-backed -> the terminal tie-break is the
    # ascending `identidad_key` (slice 08, D29/D-TIEBREAK; it used to be first-seen "B"),
    # so "A" is now the survivor and absorbs "B".
    resultado, _ = dep.unificar_duplicados({"B": survivor, "A": perdedor})
    assert list(resultado) == ["A"]
    ganador = resultado["A"]
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


def test_depurar_revision_manual_is_canonically_ordered_for_directly_built_blank_cedula():
    # Slice 08 (D29) replaces the old "universe items before remap items" emission
    # order with the canonical one (sorted by motivo, codigo, identidad_key).
    # A blank cédula is built directly here (engine tolerance to a malformed entry),
    # it is NOT the production path (parser drops it).
    main = [_entrada(cedula_key="", nombre_norm="sin cedula")]
    vercel = [
        _entrada(cedula_key="1111111", nombre_norm="ana gomez", codigo="097"),
        _entrada(cedula_key="2222222", nombre_norm="ana gomez dos", codigo="097"),
    ]
    resultado = _depurar(main=main, vercel=vercel)
    assert [r["motivo"] for r in resultado.revision_manual] == ["codigo_vercel_duplicado", "main_sin_cedula"]


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


def test_c2_loser_flagged_no_persona_in_the_bundle_does_not_flag_the_survivor():
    """D-SURVFLAGS (2026-09-19, live parity run): a real survivor merged with a duplicate the bundle
    flags as a non-person stays a PERSON. REPLACES the earlier C2 pin, which OR-ed the loser's
    `no_persona_ref` into the survivor: the notebook's survivor keeps its OWN flags (they are computed
    per row BEFORE the unification, which only backfills empty fields)."""
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", no_persona=True),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]  # A survives (n_stickers>0)
    resultado = _depurar(main=main, stickers=stickers)
    assert list(_by_key(resultado)) == ["7000001"]
    fila = _by_key(resultado)["7000001"]
    assert fila["no_persona"] is False and fila["estado_sugerido"] != "no_persona"
    assert fila["cedulas_unificadas"] == ["7000002"]


def test_c2_loser_import_local_correo_backfilled_into_survivor_does_not_flag_it():
    """The correo is still backfilled (first-non-empty wins) but the flag was computed BEFORE, from the
    survivor's own (empty) correo: it stays False. REPLACES the earlier C2 pin (D-SURVFLAGS)."""
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno"),  # empty correo
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="migrated@import.local"),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]
    fila = _by_key(_depurar(main=main, stickers=stickers))["7000001"]
    assert fila["correo_contacto"] == "migrated@import.local"
    assert fila["no_persona"] is False and fila["estado_sugerido"] != "no_persona"


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


def test_c2_unificar_duplicados_never_recomputes_the_flag_from_merged_fields():
    """D-SURVFLAGS: the survivor's flags are its OWN (REPLACES the earlier C2 pin that recomputed the
    flag from the merged correo / OR-ed `no_persona_ref`)."""
    survivor = _perfil("A", "juan perez", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("B", "juan perez", no_persona_ref=True)
    resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
    assert resultado["A"].no_persona_ref is False and resultado["A"].es_cuenta_no_persona is False

    survivor2 = _perfil("A", "juan perez", ultimo_sticker=date(2026, 8, 1))
    perdedor2 = _perfil("B", "juan perez", correo="x@import.local")
    resultado2, _ = dep.unificar_duplicados({"A": survivor2, "B": perdedor2})
    assert resultado2["A"].correo == "x@import.local" and resultado2["A"].es_cuenta_no_persona is False


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


# ── W3 (slice 08 landed): the wrong holder of a código is cleared ───────────


def test_slice_08_replaces_the_known_state_the_wrong_holder_of_a_codigo_is_cleared():
    # Slice 07 pinned "two profiles can hold the same `codigo`" (main.codigo backfills
    # an empty Firestore code, remap did not clear the wrong holder yet). Slice 08
    # (D13) replaces that known state DELIBERATELY: Vercel gives 041 to the `main`
    # cédula, so the Firestore profile that also holds it is cleared.
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="041")}
    main = [_entrada(cedula_key="2222222", nombre_norm="beto dos", codigo="041")]
    vercel = [_entrada(cedula_key="2222222", nombre_norm="beto dos", codigo="041")]
    por_clave = _by_key(_depurar(roster=roster, main=main, vercel=vercel))
    assert por_clave["1111111"]["codigo"] == ""
    assert por_clave["2222222"]["codigo"] == "041"


def test_two_profiles_holding_the_same_codigo_without_vercel_evidence_are_left_alone():
    # Characterization: with no Vercel row for the código there is no ground truth on who
    # is right, so remap must not guess (the golden rule is Vercel, D1/D13).
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
    # canonical order (D29): the roster entries are visited by (cédula key, raw form, roster key)
    assert perfiles["1234567"].cedulas_unificadas == ("1.234.567", "1234567.0")


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


def test_w5_the_bundle_flag_no_persona_ref_of_a_loser_is_not_ored_into_the_survivor():
    """D-SURVFLAGS: REPLACES `test_w5_only_the_bundle_flag_no_persona_ref_is_ored_into_the_survivor`
    (the old rule made 22 real people non-persons in the live parity run)."""
    survivor = _perfil("A", "juan perez", correo="juan@example.com", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("B", "juan perez", correo="b@import.local", no_persona_ref=True, es_cuenta_no_persona=True)
    resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
    assert resultado["A"].no_persona_ref is False
    assert resultado["A"].es_cuenta_no_persona is False


def test_w5_an_empty_survivor_correo_backfills_the_flagged_one_and_the_flag_does_not_follow():
    """D-SURVFLAGS: REPLACES `..._and_the_flag_follows`. The notebook backfills the correo first-non-empty
    but its flag was computed BEFORE, from the survivor's own (empty) correo."""
    survivor = _perfil("A", "juan perez", correo="", ultimo_sticker=date(2026, 8, 1))
    perdedor = _perfil("B", "juan perez", correo="b@import.local", es_cuenta_no_persona=True)
    resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
    assert resultado["A"].correo == "b@import.local"
    assert resultado["A"].es_cuenta_no_persona is False


# ── D-SURVFLAGS: a merge never changes the survivor's own flags (live parity run 2026-09-19) ──


def _flags_de(fila):
    return fila["no_persona"], fila["estado_sugerido"]


def test_survflags_real_survivor_with_a_non_person_duplicate_stays_a_person_and_keeps_its_estado():
    """The 22-profile defect: a real person merged with a duplicate `@import.local` / bundle-flagged
    main row became `no_persona`. With a código the estado must stay `activo`."""
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com", codigo="041"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@import.local",
                 no_persona=True),
    ]
    resultado = _depurar(main=main)
    assert list(_by_key(resultado)) == ["7000001"]
    assert _flags_de(_by_key(resultado)["7000001"]) == (False, "activo")


@pytest.mark.parametrize("loser_extra", [
    {"no_persona": True},
    {"correo": "x@import.local"},
    {"correo": "x@sismo.cali.gov.co"},
    {"correo": "cuenta@migrated.example.com"},
])
def test_survflags_the_loser_flag_is_dropped_for_each_of_the_flag_sources(loser_extra):
    """Bundle flag and every correo pattern on the loser: none leaks into a real survivor."""
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com", codigo="041"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", **loser_extra),
    ]
    assert _flags_de(_by_key(_depurar(main=main))["7000001"]) == (False, "activo")


def test_survflags_a_non_person_survivor_by_its_own_NAME_stays_a_non_person():
    """The survivor's flag also comes from its own name pattern; a real duplicate does not cure it."""
    main = [
        _entrada(cedula_key="7000001", nombre_norm="brigada norte", nombre="Brigada Norte", correo="a@example.com"),
        _entrada(cedula_key="7000002", nombre_norm="brigada norte", nombre="Brigada Norte", correo="b@example.com"),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]
    assert _flags_de(_by_key(_depurar(main=main, stickers=stickers))["7000001"]) == (True, "no_persona")


def test_survflags_the_reverse_a_non_person_survivor_with_a_real_duplicate_stays_a_non_person():
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@import.local"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com"),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]  # the non-person survives
    fila = _by_key(_depurar(main=main, stickers=stickers))["7000001"]
    assert _flags_de(fila) == (True, "no_persona")


def test_survflags_non_person_survivor_with_a_codigo_keeps_the_no_persona_precedence():
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", no_persona=True, codigo="041"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com"),
    ]
    assert _flags_de(_by_key(_depurar(main=main))["7000001"]) == (True, "no_persona")


def test_survflags_empty_correo_survivor_backfilling_a_non_person_correo_keeps_its_flag():
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", codigo="041"),  # empty correo
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="x@import.local"),
    ]
    fila = _by_key(_depurar(main=main))["7000001"]
    assert fila["correo_contacto"] == "x@import.local"  # first-non-empty backfill is kept
    assert _flags_de(fila) == (False, "activo")


def test_survflags_empty_correo_non_person_survivor_backfilling_a_real_correo_keeps_its_flag():
    """The mirror image: the flag came from the survivor's own bundle flag; a real correo backfilled from
    the loser does not cure it."""
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", no_persona=True),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com"),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]
    fila = _by_key(_depurar(main=main, stickers=stickers))["7000001"]
    assert fila["correo_contacto"] == "ana@example.com"
    assert _flags_de(fila) == (True, "no_persona")


def test_survflags_both_non_person_and_both_real_are_unchanged():
    both_bad = [  # a código keeps the pair out of GRUPO-EXTERNOS so the row is observable
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", correo="a@import.local", codigo="041"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="b@import.local"),
    ]
    both_ok = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", correo="a@example.com"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="b@example.com"),
    ]
    assert list(_by_key(_depurar(main=both_bad)).values())[0]["no_persona"] is True
    assert list(_by_key(_depurar(main=both_ok)).values())[0]["no_persona"] is False


def test_survflags_chain_of_three_keeps_the_survivor_flag_in_every_order():
    """One real survivor, a bundle-flagged loser and an `@import.local` loser (a chain of three)."""
    filas = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com", codigo="041"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", no_persona=True),
        _entrada(cedula_key="7000003", nombre_norm="ana uno", nombre="Ana Uno", correo="z@import.local"),
    ]
    for orden in itertools.permutations(filas):
        resultado = _depurar(main=orden)
        assert list(_by_key(resultado)) == ["7000001"]
        fila = _by_key(resultado)["7000001"]
        assert _flags_de(fila) == (False, "activo")
        assert sorted(fila["cedulas_unificadas"]) == ["7000002", "7000003"]


def test_survflags_chain_of_three_with_a_non_person_survivor_stays_a_non_person_in_every_order():
    filas = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", no_persona=True),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="a@example.com"),
        _entrada(cedula_key="7000003", nombre_norm="ana uno", nombre="Ana Uno", correo="b@example.com"),
    ]
    stickers = [_sticker("7000001", fecha_creacion="2026-09-11T00:00:00Z")]
    for orden in itertools.permutations(filas):
        fila = _by_key(_depurar(main=orden, stickers=stickers))["7000001"]
        assert _flags_de(fila) == (True, "no_persona")


def test_survflags_roster_survivor_with_a_flagged_main_duplicate_stays_a_person():
    """Firestore-backed roster profile (with its own clean `main` row) + a flagged `main` row of the same
    name under another cédula."""
    roster = {"7000001": _roster_entry("7000001", "Ana Uno", correo="ana@example.com", codigo="041")}
    main = [
        _entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", correo="ana@example.com"),
        _entrada(cedula_key="7000002", nombre_norm="ana uno", nombre="Ana Uno", correo="x@import.local",
                 no_persona=True),
    ]
    fila = _by_key(_depurar(roster=roster, main=main))["7000001"]
    assert _flags_de(fila) == (False, "activo")


def test_survflags_unificar_duplicados_leaves_every_survivor_flag_untouched_and_the_input_intact():
    for own in (True, False):
        for ref_survivor in (True, False):
            for loser_flag in (True, False):
                survivor = _perfil("A", "juan perez", ultimo_sticker=date(2026, 8, 1), correo="a@example.com",
                                   es_cuenta_no_persona=own, no_persona_ref=ref_survivor)
                perdedor = _perfil("B", "juan perez", correo="b@import.local", es_cuenta_no_persona=loser_flag,
                                   no_persona_ref=loser_flag)
                resultado, _ = dep.unificar_duplicados({"A": survivor, "B": perdedor})
                assert resultado["A"].es_cuenta_no_persona is own
                assert resultado["A"].no_persona_ref is ref_survivor
                assert perdedor.no_persona_ref is loser_flag  # the loser (an input) is never touched


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


# ══════════════════════════════════════════════════════════════════════════
# Extension 2026-09-19, slice 08 — remap holder clearing (Phase 9, D13)
# ══════════════════════════════════════════════════════════════════════════


def _remap(roster, vercel, *, main=(), codigos_duplicados=()):
    """Stage 1 + stage 2 only: `(perfiles, revision)` of `remapear_codigos`."""
    ref = _bundle(vercel=vercel, main=main, codigos_duplicados=codigos_duplicados)
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    return dep.remapear_codigos(perfiles, ref)


def _codigos(perfiles):
    return {clave: perfil.codigo for clave, perfil in perfiles.items()}


def _v(cedula, codigo, nombre="", **extra):
    return _entrada(cedula_key=cedula, nombre_norm=nombre, codigo=codigo, **extra)


def test_remapear_codigos_clears_wrong_current_holder():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": ""}
    assert revision == ()  # the owner exists: nothing for a human to decide


def test_remapear_codigos_clears_the_wrong_holder_when_the_code_came_from_main():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    main = [_entrada(cedula_key="2222222", nombre_norm="beto dos", codigo="041")]
    perfiles, _ = _remap(roster, [_v("1111111", "041", "ana uno")], main=main)
    assert _codigos(perfiles) == {"1111111": "041", "2222222": ""}


def test_remapear_codigos_the_owner_already_holding_the_code_is_untouched():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo="041"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="052"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": "052"}
    assert revision == ()


def test_remapear_codigos_the_same_code_held_by_three_profiles_clears_both_wrong_holders():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
        "3333333": _roster_entry("3333333", "Carla Tres", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": "", "3333333": ""}
    assert revision == ()


def test_remapear_codigos_sin_duenio():
    roster = {"2222222": _roster_entry("2222222", "Beto Dos", codigo="041")}
    perfiles, revision = _remap(roster, [_v("9999999", "041", "alguien completamente distinto")])
    assert _codigos(perfiles) == {"2222222": ""}
    assert revision == ({"motivo": "remap_sin_duenio", "codigo": "041", "identidad_key": "2222222"},)


def test_remapear_codigos_sin_duenio_is_reported_once_per_cleared_holder():
    roster = {
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
        "3333333": _roster_entry("3333333", "Carla Tres", codigo="041"),
        "4444444": _roster_entry("4444444", "Dario Cuatro", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("9999999", "041", "nadie parecido")])
    assert set(_codigos(perfiles).values()) == {""}
    # S2: one item per CLEARED holder (each carries its identidad_key), never a repeat
    assert sorted(r["identidad_key"] for r in revision) == ["2222222", "3333333", "4444444"]
    assert {(r["motivo"], r["codigo"]) for r in revision} == {("remap_sin_duenio", "041")}


def test_remapear_codigos_no_wrong_holder_and_no_owner_reports_nothing():
    roster = {"2222222": _roster_entry("2222222", "Beto Dos", codigo="052")}
    perfiles, revision = _remap(roster, [_v("9999999", "041", "nadie parecido")])
    assert _codigos(perfiles) == {"2222222": "052"}
    assert revision == ()


def test_remapear_codigos_conflicto():
    # One person registered twice in Vercel with two different codes: the profile
    # cannot hold both, and a machine must not pick one.
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    vercel = [_v("1111111", "041", "ana uno"), _v("1.111.111", "052", "ana uno")]
    perfiles, revision = _remap(roster, vercel)
    assert _codigos(perfiles) == {"1111111": ""}
    assert sorted(revision, key=lambda r: r["codigo"]) == [
        {"motivo": "remap_conflicto", "codigo": "041", "identidad_key": "1111111"},
        {"motivo": "remap_conflicto", "codigo": "052", "identidad_key": "1111111"},
    ]


def test_remapear_codigos_conflicto_clears_a_currently_held_contended_code_and_the_wrong_holder():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo="041"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="052"),
    }
    vercel = [_v("1111111", "041", "ana uno"), _v("1111111", "052", "ana uno")]
    perfiles, revision = _remap(roster, vercel)
    assert _codigos(perfiles) == {"1111111": "", "2222222": ""}  # 052's holder is wrong too
    assert {r["motivo"] for r in revision} == {"remap_conflicto"}


def test_remapear_codigos_duplicado_vercel_still_excluded():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="097"),
    }
    vercel = [_v("1111111", "097", "ana uno"), _v("3333333", "097", "carla tres")]
    perfiles, revision = _remap(roster, vercel)
    # D-P1: the code is skipped ENTIRELY: not assigned, and the holder is not cleared
    assert _codigos(perfiles) == {"1111111": "", "2222222": "097"}
    assert revision == ({"motivo": "codigo_vercel_duplicado", "codigo": "097"},)


def test_remapear_codigos_codigos_duplicados_of_the_bundle_also_skip_the_holder_clearing():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="127"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "127", "ana uno")], codigos_duplicados=["127"])
    assert _codigos(perfiles) == {"1111111": "", "2222222": "127"}
    assert revision == ()


def test_remapear_codigos_idempotent():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
        "3333333": _roster_entry("3333333", "Carla Tres", codigo="052"),
        "4444444": _roster_entry("4444444", "Dario Cuatro"),
    }
    ref = _bundle(vercel=[
        _v("1111111", "041", "ana uno"), _v("9999999", "052", "otra persona"),
        _v("4444444", "060", "dario cuatro"), _v("4444444", "061", "dario cuatro"),
        _v("5555555", "070", "x"), _v("6666666", "070", "y"),
    ])
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    primera, revision_1 = dep.remapear_codigos(perfiles, ref)
    estado_1 = _codigos(primera)
    segunda, revision_2 = dep.remapear_codigos(primera, ref)
    assert _codigos(segunda) == estado_1 == {
        "1111111": "041", "2222222": "", "3333333": "", "4444444": "",
    }
    # "Clear the wrong holder" is an event: it is not re-reported on a second pass,
    # and neither pass repeats an entry
    assert {"motivo": "remap_sin_duenio", "codigo": "052", "identidad_key": "3333333"} in revision_1
    assert not _revision_de(revision_2, "remap_sin_duenio")
    for revision in (revision_1, revision_2):
        assert len(revision) == len({tuple(sorted(r.items())) for r in revision})


@pytest.mark.parametrize("vacio", ["", "   ", "\t\n", None, float("nan")])
def test_remapear_codigos_empty_none_nan_or_whitespace_codes_are_ignored(vacio):
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", vacio, "ana uno"), _v("2222222", vacio, "beto dos")])
    assert _codigos(perfiles) == {"1111111": "", "2222222": "041"}  # nothing assigned, nobody cleared
    assert revision == ()


def test_remapear_codigos_two_whitespace_codes_are_not_a_vercel_duplicate():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    perfiles, revision = _remap(roster, [_v("1111111", "  ", "ana uno"), _v("2222222", "  ", "beto dos")])
    assert _codigos(perfiles) == {"1111111": ""} and revision == ()


def test_remapear_codigos_a_padded_vercel_code_is_trimmed_before_it_is_assigned():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    perfiles, _ = _remap(roster, [_v("1111111", " 041 ", "ana uno")])
    assert perfiles["1111111"].codigo == "041"


def test_remapear_codigos_leading_zeros_are_never_conflated():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="021"),
        "3333333": _roster_entry("3333333", "Carla Tres", codigo="21"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "21", "ana uno")])
    # "21" belongs to Ana: only the holder of exactly "21" is cleared, "021" stays
    assert _codigos(perfiles) == {"1111111": "21", "2222222": "021", "3333333": ""}
    assert revision == ()
    perfiles, _ = _remap(roster, [_v("1111111", "021", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "021", "2222222": "", "3333333": "21"}


def test_remapear_codigos_a_swap_between_two_profiles_is_a_chain_not_a_wipe():
    # A holds B's code and B holds A's: Vercel says A->041, B->052
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo="052"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno"), _v("2222222", "052", "beto dos")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": "052"}
    assert revision == ()


def test_remapear_codigos_a_three_way_rotation_resolves_to_the_vercel_owners():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo="052"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="060"),
        "3333333": _roster_entry("3333333", "Carla Tres", codigo="041"),
    }
    vercel = [_v("1111111", "041", "ana uno"), _v("2222222", "052", "beto dos"), _v("3333333", "060", "carla tres")]
    perfiles, revision = _remap(roster, vercel)
    assert _codigos(perfiles) == {"1111111": "041", "2222222": "052", "3333333": "060"}
    assert revision == ()


def test_remapear_codigos_owner_only_present_by_fuzzy_name_surfaces_the_candidate_and_clears_the_holder():
    roster = {
        "1234567": _roster_entry("1234567", "Juan Perez Gomez"),
        "7654321": _roster_entry("7654321", "Otra Persona Distinta", codigo="055"),
    }
    perfiles, revision = _remap(roster, [_v("9999999", "055", "juan perez gomes")])
    # never auto-assigned to the fuzzy candidate; the wrong holder is still cleared
    assert _codigos(perfiles) == {"1234567": "", "7654321": ""}
    motivos = sorted(r["motivo"] for r in revision)
    assert motivos == ["codigo_remap_candidato", "remap_sin_duenio"]
    candidato = next(r for r in revision if r["motivo"] == "codigo_remap_candidato")
    assert candidato["identidad_key_candidato"] == "1234567" and candidato["codigo"] == "055"


def test_remapear_codigos_fuzzy_tie_picks_the_lowest_identidad_key_in_any_order():
    filas = [
        ("8888888", "Juan Perez Gomez"), ("2222222", "Juan Perez Gomez"), ("5555555", "Juan Perez Gomez"),
    ]
    for orden in itertools.permutations(filas):
        roster = {cedula: _roster_entry(cedula, nombre) for cedula, nombre in orden}
        _, revision = _remap(roster, [_v("9999999", "055", "juan perez gomez")])
        candidatos = [r for r in revision if r["motivo"] == "codigo_remap_candidato"]
        assert [c["identidad_key_candidato"] for c in candidatos] == ["2222222"]


def test_depurar_remap_when_the_owner_is_the_survivor_of_a_unification():
    # A (Vercel owner, has stickers) survives the D-P2 unification against A2 (main-only,
    # same name) that holds the same código wrongly.
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    main = [_entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno", codigo="041")]
    resultado = _depurar(
        roster=roster, main=main, vercel=[_v("1111111", "041", "ana uno")],
        stickers=[_sticker("1111111", fecha_creacion="2026-09-10T00:00:00Z")],
    )
    filas = _by_key(resultado)
    assert list(filas) == ["1111111"]
    assert filas["1111111"]["codigo"] == "041"
    assert filas["1111111"]["cedulas_unificadas"] == ["7000001"]


def test_depurar_remap_when_the_owner_is_the_loser_the_survivor_inherits_the_code_once():
    # The wrong holder S (Firestore, has stickers) SURVIVES the unification; the Vercel
    # owner A2 (same name, main-only) is the loser. S is cleared first, then inherits the
    # code back through the first-non-empty backfill: one holder, no duplicates.
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="041")}
    main = [_entrada(cedula_key="7000001", nombre_norm="ana uno", nombre="Ana Uno")]
    resultado = _depurar(
        roster=roster, main=main, vercel=[_v("7000001", "041", "ana uno")],
        stickers=[_sticker("1111111", fecha_creacion="2026-09-10T00:00:00Z")],
    )
    filas = _by_key(resultado)
    assert list(filas) == ["1111111"]
    assert filas["1111111"]["codigo"] == "041"
    assert sum(1 for f in resultado.inspectores if f["codigo"] == "041") == 1


def test_depurar_remap_wrong_holder_cleared_end_to_end_is_no_longer_activo():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
    }
    resultado = _depurar(roster=roster, vercel=[_v("1111111", "041", "ana uno")])
    filas = _by_key(resultado)
    assert filas["1111111"]["estado_sugerido"] == "activo"
    assert filas["2222222"]["codigo"] == "" and filas["2222222"]["estado_sugerido"] != "activo"


# ══════════════════════════════════════════════════════════════════════════
# Extension 2026-09-19, slice 08 — Fase 2 cédula fix (Phase 9, D18) and group row
# ══════════════════════════════════════════════════════════════════════════


def _f2(cedula, nombre_norm, np="P3"):
    return _entrada(cedula_key=cedula, nombre_norm=nombre_norm, np=np, pasos=(1, 2))


def _main(cedula, nombre_norm, **extra):
    return _entrada(cedula_key=cedula, nombre_norm=nombre_norm, nombre=nombre_norm.title(), **extra)


def test_fase2_cedula_fix_name_only_match_keeps_cedula_sospechosa_from_original():
    main = [_main("999", "ana gomez")]
    fase2 = [_f2("1053812345", "ana gomez")]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main, fase2=fase2))
    assert set(perfiles) == {"1053812345"}
    perfil = perfiles["1053812345"]
    assert perfil.identidad_key == perfil.identificacion == "1053812345"
    assert perfil.cedula_sospechosa is True  # computed from the ORIGINAL main cédula "999"
    assert perfil.cedulas_unificadas == ("999",)  # the old key stays resolvable, once
    assert perfil.en_fase2 is True and perfil.np_fase2 == "P3"
    assert revision == ()


def test_fase2_cedula_fix_does_not_turn_a_clean_original_cedula_into_a_suspicious_one():
    # Triangulation of D18: the flag follows the ORIGINAL cédula in BOTH directions.
    main = [_main("1053812345", "ana gomez")]
    fase2 = [_f2("999", "ana gomez")]
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=main, fase2=fase2))
    assert set(perfiles) == {"999"}
    assert perfiles["999"].cedula_sospechosa is False
    assert perfiles["999"].cedulas_unificadas == ("1053812345",)


def test_fase2_cedula_fix_applies_to_a_firestore_profile_too():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(fase2=[_f2("2222222", "ana uno")]))
    assert set(perfiles) == {"2222222"}
    assert perfiles["2222222"].cedulas_unificadas == ("1111111",)
    assert perfiles["2222222"].firestore_backed is True


def test_fase2_cedula_fix_preserves_previously_attributed_stickers():
    main = [_main("999", "ana gomez")]
    fase2 = [_f2("1053812345", "ana gomez")]
    stickers = [
        _sticker("999", fecha_creacion="2026-09-03T00:00:00Z"),
        _sticker("999", fecha_creacion="2026-09-10T00:00:00Z"),
    ]
    perfiles, _ = dep.fusionar_identidad(stickers, {}, _bundle(main=main, fase2=fase2))
    perfil = perfiles["1053812345"]
    assert perfil.n_stickers == 2
    assert perfil.ultimo_sticker == date(2026, 9, 10)
    assert perfil.tiene_sticker_valido is True


def test_fase2_cedula_fix_keeps_the_zero_stripped_alias_a_sticker_registered_and_adds_the_old_key():
    main = [_main("999", "ana gomez")]
    fase2 = [_f2("1053812345", "ana gomez")]
    stickers = [_sticker("0999", fecha_creacion="2026-09-10T00:00:00Z")]  # resolved through the stripped fallback
    perfiles, _ = dep.fusionar_identidad(stickers, {}, _bundle(main=main, fase2=fase2))
    assert sorted(perfiles["1053812345"].cedulas_unificadas) == ["0999", "999"]


def test_fase2_cedula_fix_frontend_payload_contract_every_sticker_cedula_form_resolves_to_the_row():
    """Old forms (what the attributed stickers carry) AND the new forms must route
    to the fixed row through `identidad_key` / `cedulas_unificadas`."""
    main = [_main("999", "ana gomez")]
    fase2 = [_f2("1053812345", "ana gomez")]
    old_forms = ["999", "0999", "999.0", " 999 "]
    stickers = [_sticker(forma, fecha_creacion="2026-09-10T00:00:00Z") for forma in old_forms]
    resultado = _depurar(main=main, fase2=fase2, stickers=stickers)
    assert list(_by_key(resultado)) == ["1053812345"]
    fila = _by_key(resultado)["1053812345"]
    assert fila["ultimo_sticker"] == "2026-09-10" and fila["cedula_sospechosa"] is True
    assert fila["cedulas_unificadas"].count("999") == 1  # once
    assert "1053812345" not in fila["cedulas_unificadas"]  # never the row's own key
    for forma in [*old_forms, "1053812345", "1.053.812.345", "1053812345.0"]:
        assert _fe_route(resultado, forma) == "1053812345", forma


def test_fase2_cedula_fix_not_applied_on_cedula_match():
    main = [_main("1053812345", "ana gomez")]
    fase2 = [_f2("1053812345", "otra persona distinta")]  # same cédula, different name
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main, fase2=fase2))
    assert set(perfiles) == {"1053812345"}
    assert perfiles["1053812345"].cedulas_unificadas == () and revision == ()
    assert perfiles["1053812345"].en_fase2 is True


def test_fase2_cedula_fix_not_applied_when_the_cedula_matches_through_another_format():
    main = [_main("1053812345", "ana gomez")]
    fase2 = [_f2("1.053.812.345", "ana gomez")]  # same person, same cédula, dotted
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main, fase2=fase2))
    assert set(perfiles) == {"1053812345"} and perfiles["1053812345"].cedulas_unificadas == ()
    assert revision == ()


@pytest.mark.parametrize("cedula_fase2", ["", "   ", "abc", None])
def test_fase2_cedula_fix_needs_a_usable_fase2_cedula(cedula_fase2):
    main = [_main("999", "ana gomez")]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main, fase2=[_f2(cedula_fase2, "ana gomez")]))
    assert set(perfiles) == {"999"} and revision == ()
    assert perfiles["999"].en_fase2 is True  # the overlay itself is unchanged


def test_fase2_cedula_fix_is_idempotent_over_a_second_pass():
    main = [_main("999", "ana gomez")]
    ref = _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")])
    perfiles, _ = dep.fusionar_identidad([], {}, ref)
    de_nuevo, revision = dep.corregir_cedula_fase2(perfiles, ref)
    assert set(de_nuevo) == {"1053812345"} and de_nuevo["1053812345"].cedulas_unificadas == ("999",)
    assert revision == ()


def test_fase2_cedula_fix_collision_with_existing_perfil():
    # The fixed cédula already belongs to ANOTHER person (a different name): no silent
    # capture, both profiles stay, and the pair goes to review.
    roster = {"1053812345": _roster_entry("1053812345", "Carla Otra")}
    main = [_main("999", "ana gomez")]
    perfiles, revision = dep.fusionar_identidad([], roster, _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")]))
    assert set(perfiles) == {"999", "1053812345"}
    assert perfiles["999"].cedulas_unificadas == () and perfiles["1053812345"].nombre_completo == "Carla Otra"
    assert revision == ({
        "motivo": "fase2_cedula_colision", "cedula_key": "1053812345", "identidad_key": "999",
        "identidad_key_existente": "1053812345", "nombre_completo": "Ana Gomez",
    },)


def test_fase2_cedula_fix_collision_with_an_alias_another_profile_already_answers_to():
    # Q's own key is zero-padded; a sticker carrying the plain form was routed to it and
    # registered as its alias: that alias is Q's claim too.
    main = [_main("01053812345", "carla otra"), _main("999", "ana gomez")]
    stickers = [_sticker("1053812345", fecha_creacion="2026-09-10T00:00:00Z")]
    perfiles, revision = dep.fusionar_identidad(stickers, {}, _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")]))
    assert set(perfiles) == {"999", "01053812345"}
    assert [r["motivo"] for r in revision] == ["fase2_cedula_colision"]
    assert revision[0]["identidad_key_existente"] == "01053812345"


def test_fase2_cedula_fix_two_profiles_claiming_the_same_fase2_cedula_are_both_left_alone():
    main = [_main("999", "ana gomez"), _main("998", "ana gomez")]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")]))
    assert set(perfiles) == {"999", "998"}
    assert sorted((r["identidad_key"], r["identidad_key_existente"]) for r in revision) == [("998", ""), ("999", "")]


def test_fase2_cedula_fix_collision_outcome_does_not_depend_on_the_input_order():
    for orden in itertools.permutations([("999", "ana gomez"), ("998", "ana gomez"), ("1053812345", "carla otra")]):
        main = [_main(cedula, nombre) for cedula, nombre in orden]
        perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")]))
        assert set(perfiles) == {"999", "998", "1053812345"}
        assert sorted(r["identidad_key"] for r in revision) == ["998", "999"]


def test_fase2_cedula_fix_then_the_remap_finds_the_owner_by_the_new_cedula():
    main = [_main("999", "ana gomez")]
    ref = _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")],
                  vercel=[_v("1053812345", "041", "ana gomez")])
    perfiles, _ = dep.fusionar_identidad([], {}, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1053812345"].codigo == "041" and revision == ()


# ── 9.12 / 9.13 collapse trigger and the group row shape ────────────────────


def test_colapsar_externos_cedula_sospechosa_or_no_persona():
    hoy = date(2026, 9, 12)
    solo_cedula = _perfil("A", "persona a", cedula_sospechosa=True)
    solo_cuenta = _perfil("B", "cuenta b", es_cuenta_no_persona=True)
    ambas = _perfil("C", "persona c", cedula_sospechosa=True, es_cuenta_no_persona=True)
    limpia = _perfil("D", "persona d")
    restantes, detalle = dep.colapsar_externos({p.identidad_key: p for p in (solo_cedula, solo_cuenta, ambas, limpia)}, hoy)
    assert set(restantes) == {"D"}
    assert {d["identificacion"]: d["motivo"] for d in detalle} == {
        "A": "cedula_sospechosa", "B": "cuenta_no_persona", "C": "cedula_sospechosa",
    }


@pytest.mark.parametrize("marca", [{"cedula_sospechosa": True}, {"es_cuenta_no_persona": True}])
def test_colapsar_externos_boundary_7_stays_8_collapses_and_a_codigo_excludes(marca):
    hoy = date(2026, 9, 12)
    en_7 = _perfil("A", "persona a", ultimo_sticker=date(2026, 9, 5), **marca)   # dias_inactivo == 7
    en_8 = _perfil("B", "persona b", ultimo_sticker=date(2026, 9, 4), **marca)   # dias_inactivo == 8
    con_codigo = _perfil("C", "persona c", ultimo_sticker=date(2026, 8, 1), codigo="041", **marca)
    sin_sticker = _perfil("D", "persona d", **marca)
    restantes, detalle = dep.colapsar_externos({p.identidad_key: p for p in (en_7, en_8, con_codigo, sin_sticker)}, hoy)
    assert set(restantes) == {"A", "C"}
    assert restantes["A"].dias_inactivo == 7 and restantes["C"].dias_inactivo > 7
    assert sorted(d["identificacion"] for d in detalle) == ["B", "D"]


def test_depurar_a_suspicious_cedula_alone_is_enough_to_collapse_when_inactive_and_codeless():
    main = [_main("999", "ana gomez")]  # cédula "999" is suspicious, no correo pattern, no sticker, no code
    resultado = _depurar(main=main)
    assert resultado.inspectores == ()
    assert resultado.grupo_externos["n_colapsados"] == 1
    assert resultado.grupo_externos["detalle"][0]["motivo"] == "cedula_sospechosa"


def test_grupo_externos_row_shape():
    main = [_main("999", "ana gomez"), _main("998", "beto dos")]
    grupo = _depurar(main=main).grupo_externos
    assert grupo["identidad_key"] == "GRUPO-EXTERNOS"
    assert grupo["n_colapsados"] == 2 and len(grupo["detalle"]) == 2
    assert grupo["np_fuente"] == "ninguno"
    assert grupo["fase"] == "Fase I"
    assert grupo["fase_np_faltante"] is True
    assert grupo["activo"] is False
    assert grupo["estado_sugerido"] == "grupo_externos_agrupado"


def test_grupo_externos_row_is_absent_when_nobody_collapses():
    main = [_main("1053812345", "ana gomez")]
    assert _depurar(main=main).grupo_externos is None


# ══════════════════════════════════════════════════════════════════════════
# Extension 2026-09-19, slice 08 — determinism, purity, idempotency (D29, 9.16-9.23)
# ══════════════════════════════════════════════════════════════════════════

import __future__  # noqa: E402
import contextlib  # noqa: E402
import copy  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import random  # noqa: E402
import types  # noqa: E402
import unittest.mock  # noqa: E402
from datetime import datetime  # noqa: E402


def _universo_completo():
    """A fixture that exercises every order-sensitive spot of the engine at once:
    a roster duplicate (same cédula key, two raw forms), two Firestore-backed
    same-name profiles with a blank `creado_en` (terminal tie-break), a cross-source
    same-name pair, a duplicated `main` cédula, a Fase 2 cédula fix, wrong code
    holders, a Vercel duplicate and a conflict, a fuzzy candidate, a collapse
    candidate exempted by the survey, a `main` row without cédula, sticker forms
    that resolve through the zero-stripped fallback."""
    roster = {
        "r1": _roster_entry("1111111", "Ana Uno", codigo="041"),
        "r2": _roster_entry("2222222", "Beto Dos", codigo="052"),
        "r3": _roster_entry("3333333", "Carla Tres"),
        "r4": _roster_entry("4444444.0", "Dario Cuatro"),
        "r5": _roster_entry("5.555.555", "Eva Cinco"),
        "r6": _roster_entry("6666666", "Gina Seis"),
        "r7": _roster_entry("6666667", "Gina Seis"),
        "r8": _roster_entry("8888888", "Hugo Ocho", correo="hugo@example.com"),
        "r9": _roster_entry("8.888.888", "Hugo Ocho Dos", correo="otro@example.com"),
        "r10": _roster_entry("1234567", "Fer Perez Gomez"),
        "r11": _roster_entry("7777777", "Luis Wrong", codigo="088"),
    }
    main = [
        _main("7000001", "ana uno", creado_en="2020-01-01"),
        _main("01234567", "beto tres"),
        _main("999", "ivan sospechoso"),
        _main("12345", "zoe sospechosa"),
        _main("998", "julia fase dos"),
        _main("9000001", "kate uno", id="m1"),
        _main("9000001", "kate otra", id="m2"),
        _main("9100001", "cuenta migrada", correo="x@import.local", no_persona=True),
        _main("9200001", "mario dos"),
        _main("9200002", "mario dos"),
        _entrada(cedula_key="", nombre_norm="sin cedula", nombre="Sin Cedula"),
    ]
    vercel = [
        _v("1111111", "041", "ana uno", np="P2", entidad="DAGRD"),
        _v("3333333", "052", "carla tres", np="P3"),
        _v("9999999", "077", "fer perez gomes"),
        _v("8080808", "088", "nadie parecido a nadie"),
        _v("4444444", "097", "dario cuatro"),
        _v("5555555", "097", "eva cinco"),
        _v("5555555", "120", "eva cinco"),
        _v("5.555.555", "121", "eva cinco"),
    ]
    fase2 = [_f2("1053812345", "julia fase dos"), _f2("1111111", "ana uno", np="P2")]
    stickers = [
        _sticker("1111111", fecha_creacion="2026-09-05T00:00:00Z"),
        _sticker("01111111", fecha_creacion="2026-09-06T00:00:00Z"),
        _sticker("3333333", fecha_creacion="2026-09-10T00:00:00Z"),
        _sticker("999", fecha_creacion="2026-08-01T00:00:00Z"),
        _sticker("998", fecha_creacion="2026-09-09T00:00:00Z"),
        _sticker("0998", fecha_creacion="2026-09-11T00:00:00Z"),
        _sticker("9200002", fecha_creacion="2026-09-02T00:00:00Z"),
        _sticker("1234567", fecha_creacion="2026-09-04T00:00:00Z"),
        _sticker("4444444.0", fecha_creacion="2026-09-08T00:00:00Z"),
        _sticker("5555555", fecha_creacion="2026-08-25T00:00:00Z"),
        _sticker("8.888.888", fecha_creacion="2026-09-07T00:00:00Z"),
        _sticker("nada", fecha_creacion="2026-09-07T00:00:00Z"),
    ]
    survey = ["Ana Uno", "KATE UNO", "cuenta migrada", "Alguien Que No Existe"]
    return roster, main, vercel, fase2, stickers, survey


def _correr(fixture, *, roster=None, stickers=None, main=None, hoy=HOY):
    r, m, v, f, s, survey = fixture
    return dep.depurar(
        stickers=s if stickers is None else stickers,
        roster_by_cedula=r if roster is None else roster,
        nombres_survey=survey,
        referencia=_bundle(vercel=v, fase2=f, main=m if main is None else main),
        hoy=hoy,
    )


def _permutaciones(items, cantidad, semilla):
    rng = random.Random(semilla)
    for _ in range(cantidad):
        copia = list(items)
        rng.shuffle(copia)
        yield copia


def test_depurar_result_independent_of_roster_and_sticker_order():
    fixture = _universo_completo()
    roster, main, vercel, fase2, stickers, survey = fixture
    base = _correr(fixture)
    # the fixture really exercises the engine: it is not a vacuous "all empty" comparison
    assert len(base.inspectores) >= 10 and base.grupo_externos is not None
    assert len(base.revision_manual) >= 6 and base.alias_nombres
    assert any(f["cedulas_unificadas"] for f in base.inspectores)
    permutaciones = 0
    for indice, (roster_perm, stickers_perm) in enumerate(zip(
        _permutaciones(roster.items(), 30, 1), _permutaciones(stickers, 30, 2)
    )):
        resultado = _correr(fixture, roster=dict(roster_perm), stickers=stickers_perm)
        assert resultado.inspectores == base.inspectores, indice
        assert resultado.grupo_externos == base.grupo_externos, indice
        assert resultado.alias_nombres == base.alias_nombres, indice
        assert resultado.revision_manual == base.revision_manual, indice
        assert [f["cedulas_unificadas"] for f in resultado.inspectores] == [
            f["cedulas_unificadas"] for f in base.inspectores
        ], indice
        assert resultado == base, indice
        permutaciones += 1
    assert permutaciones == 30


def test_depurar_result_independent_of_the_reversed_and_rotated_inputs():
    fixture = _universo_completo()
    roster, _, _, _, stickers, _ = fixture
    base = _correr(fixture)
    items = list(roster.items())
    assert _correr(fixture, roster=dict(items[::-1]), stickers=stickers[::-1]) == base
    assert _correr(fixture, roster=dict(items[3:] + items[:3]), stickers=stickers[5:] + stickers[:5]) == base


def test_depurar_output_is_emitted_in_canonical_order():
    resultado = _correr(_universo_completo())
    claves = [f["identidad_key"] for f in resultado.inspectores]
    assert len(claves) > 5 and claves == sorted(claves)
    revision = resultado.revision_manual
    assert list(revision) == sorted(
        revision, key=lambda r: (r["motivo"], r.get("codigo", ""), r.get("identidad_key", ""))
    )
    detalle = resultado.grupo_externos["detalle"]
    assert len(detalle) >= 2
    assert [d["identificacion"] for d in detalle] == sorted(d["identificacion"] for d in detalle)
    assert list(resultado.alias_nombres) == sorted(resultado.alias_nombres)


# ── 9.18 terminal tie-break (D-TIEBREAK) ────────────────────────────────────


@pytest.mark.parametrize("orden", [("1111111", "2222222"), ("2222222", "1111111")])
def test_unificar_duplicados_terminal_tiebreak_identidad_key_when_creado_en_blank(orden):
    perfiles = {k: _perfil(k, "juan perez", firestore_backed=True) for k in orden}
    resultado, fusiones = dep.unificar_duplicados(perfiles)
    assert list(resultado) == ["1111111"]
    assert fusiones == ({"survivor": "1111111", "perdedor": "2222222", "nombre_norm": "juan perez"},)


@pytest.mark.parametrize("orden", list(itertools.permutations(["3", "1", "2"])))
def test_unificar_duplicados_terminal_tiebreak_holds_for_three_way_groups_and_unparseable_dates(orden):
    perfiles = {k: _perfil(k, "juan perez", firestore_backed=True, creado_en="no es una fecha") for k in orden}
    resultado, fusiones = dep.unificar_duplicados(perfiles)
    assert list(resultado) == ["1"]
    assert sorted(f["perdedor"] for f in fusiones) == ["2", "3"]


@pytest.mark.parametrize("orden", [("A", "Z"), ("Z", "A")])
def test_unificar_duplicados_firestore_backed_still_outranks_identidad_key(orden):
    perfiles = {
        "A": _perfil("A", "juan perez", firestore_backed=False),
        "Z": _perfil("Z", "juan perez", firestore_backed=True),  # the HIGHER key, but Firestore-backed
    }
    resultado, _ = dep.unificar_duplicados({k: perfiles[k] for k in orden})
    assert list(resultado) == ["Z"]


@pytest.mark.parametrize("orden", [("A", "Z"), ("Z", "A")])
def test_unificar_duplicados_a_blank_creado_en_never_beats_a_real_date_whatever_the_key(orden):
    perfiles = {
        "A": _perfil("A", "juan perez", firestore_backed=True, creado_en=""),
        "Z": _perfil("Z", "juan perez", firestore_backed=True, creado_en="2024-05-01"),
    }
    resultado, _ = dep.unificar_duplicados({k: perfiles[k] for k in orden})
    assert list(resultado) == ["Z"]


@pytest.mark.parametrize("orden", list(itertools.permutations(["B", "A", "C"])))
def test_unificar_duplicados_backfill_uses_the_losers_in_identidad_key_order(orden):
    # three same-name profiles, the survivor (has stickers) has no correo; the LOSERS
    # carry different correos: the first non-empty one in key order fills it, always.
    perfiles = {
        "S": _perfil("S", "juan perez", n_stickers=1),
        "A": _perfil("A", "juan perez", correo="a@example.com"),
        "B": _perfil("B", "juan perez", correo="b@example.com"),
        "C": _perfil("C", "juan perez", correo="c@example.com"),
    }
    entrada = {k: perfiles[k] for k in ("S", *orden)}
    resultado, _ = dep.unificar_duplicados(entrada)
    assert resultado["S"].correo == "a@example.com"


# ── 9.19 alias collisions, revision canon, empty inputs, bundle order ───────


@pytest.mark.parametrize("orden", [("A", "B"), ("B", "A")])
def test_alias_nombres_collision_winner_is_order_independent(orden, caplog):
    perfiles = {k: _perfil(k, "juan perez") for k in orden}
    with caplog.at_level(logging.INFO, logger=dep.__name__):
        alias = dep.alias_nombres(perfiles, ["Juan Perez", "JUAN  PÉREZ"])
    assert alias == {"juan perez": "A"}
    registros = [r for r in caplog.records if "colision" in r.getMessage()]
    assert len(registros) == 1  # recorded once
    assert "juan" not in registros[0].getMessage().lower()


def test_alias_nombres_three_way_collision_is_recorded_once_and_the_lowest_key_wins(caplog):
    for orden in itertools.permutations(["C", "A", "B"]):
        caplog.clear()
        perfiles = {k: _perfil(k, "juan perez") for k in orden}
        with caplog.at_level(logging.INFO, logger=dep.__name__):
            alias = dep.alias_nombres(perfiles, ["juan perez"])
        assert alias == {"juan perez": "A"}
        assert len([r for r in caplog.records if "colision" in r.getMessage()]) == 1


def test_revision_manual_emission_is_canonical_and_free_of_duplicates():
    main = [
        _main("9000001", "kate uno", id="m1"),
        _main("9000001", "kate otra"),          # duplicated cédula ...
        _main("9000001", "kate otra"),          # ... and an identical third row
        _entrada(cedula_key="", nombre_norm="sin cedula", nombre="Sin Cedula"),
        _entrada(cedula_key="", nombre_norm="sin cedula", nombre="Sin Cedula"),  # indistinguishable twin
        _main("999", "ana gomez"),
        _main("1053812345", "carla otra"),
    ]
    fixture = (
        {"r": _roster_entry("2222222", "Beto Dos", codigo="052")},
        main,
        [_v("9999999", "052", "otro nombre"), _v("2222222", "060", "beto dos"), _v("2222222", "061", "beto dos")]
        + [_v("4444444", "097", "p"), _v("5555555", "097", "q")],
        [_f2("1053812345", "ana gomez")],
        [],
        [],
    )
    resultado = _correr(fixture)
    revision = list(resultado.revision_manual)
    serializados = [json.dumps(r, sort_keys=True) for r in revision]
    assert len(serializados) == len(set(serializados))
    assert revision == sorted(revision, key=lambda r: (r["motivo"], r.get("codigo", ""), r.get("identidad_key", "")))
    motivos = [r["motivo"] for r in revision]
    assert motivos.count("cedula_duplicada_main") == 1 and motivos.count("main_sin_cedula") == 1
    assert {"codigo_vercel_duplicado", "remap_sin_duenio", "remap_conflicto", "fase2_cedula_colision"} <= set(motivos)
    for _ in range(3):
        assert list(_correr(fixture).revision_manual) == revision


def test_depurar_empty_inputs_are_deterministic():
    def correr():
        return dep.depurar(stickers=[], roster_by_cedula={}, nombres_survey=[], referencia=EMPTY_REF, hoy=HOY)

    primero = correr()
    assert primero.inspectores == () and primero.grupo_externos is None
    assert primero.alias_nombres == {} and primero.revision_manual == ()
    assert primero.activa is False and primero.motivo == "sin_blob"
    for _ in range(3):
        assert correr() == primero


def test_main_duplicate_cedula_winner_follows_bundle_order_only():
    primera = _main("9000001", "kate uno", telefono="3001111111")
    segunda = _main("9000001", "kate otra", telefono="3002222222")
    roster = {"a": _roster_entry("1111111", "Ana Uno"), "b": _roster_entry("2222222", "Beto Dos")}
    stickers = [_sticker("1111111", fecha_creacion="2026-09-05T00:00:00Z"), _sticker("2222222")]

    def ganador(main, roster_perm=roster, stickers_perm=stickers):
        resultado = _depurar(roster=roster_perm, main=main, stickers=stickers_perm)
        return _by_key(resultado)["9000001"]["nombre_completo"]

    assert ganador([primera, segunda]) == "Kate Uno"
    assert ganador([segunda, primera]) == "Kate Otra"  # swapping the BUNDLE rows may change the winner
    for roster_perm in itertools.permutations(roster.items()):
        for stickers_perm in (stickers, stickers[::-1]):
            assert ganador([primera, segunda], dict(roster_perm), stickers_perm) == "Kate Uno"
            assert ganador([segunda, primera], dict(roster_perm), stickers_perm) == "Kate Otra"


# ── 9.20 / 9.21 purity: no mutation, no aliasing ────────────────────────────


def test_depurar_does_not_mutate_inputs():
    roster, main, vercel, fase2, stickers, survey = _universo_completo()
    referencia = _bundle(vercel=vercel, fase2=fase2, main=main, codigos_duplicados=["127"])
    roster_antes, stickers_antes, survey_antes = copy.deepcopy(roster), copy.deepcopy(stickers), list(survey)
    referencia_antes = copy.deepcopy(referencia)
    resultado = dep.depurar(
        stickers=stickers, roster_by_cedula=roster, nombres_survey=survey, referencia=referencia, hoy=HOY,
    )
    assert len(resultado.inspectores) > 5  # the call really did work over these inputs
    assert roster == roster_antes and stickers == stickers_antes and survey == survey_antes
    assert referencia == referencia_antes


def test_depurar_survey_names_as_generator_or_set_give_the_same_result():
    roster, main, vercel, fase2, stickers, survey = _universo_completo()
    referencia = _bundle(vercel=vercel, fase2=fase2, main=main)

    def con(nombres):
        return dep.depurar(
            stickers=stickers, roster_by_cedula=roster, nombres_survey=nombres, referencia=referencia, hoy=HOY,
        )

    de_lista = con(list(survey))
    assert de_lista.alias_nombres  # the survey names are consumed (not an empty comparison)
    assert con(n for n in survey) == de_lista  # one-shot iterable, materialized once
    assert con(set(survey)) == de_lista
    assert con(iter(survey)) == de_lista
    assert con(tuple(survey)) == de_lista


def test_depurar_result_does_not_alias_inputs():
    roster, main, vercel, fase2, stickers, survey = _universo_completo()
    referencia = _bundle(vercel=vercel, fase2=fase2, main=main)

    def correr():
        return dep.depurar(
            stickers=stickers, roster_by_cedula=roster, nombres_survey=survey, referencia=referencia, hoy=HOY,
        )

    primera = correr()
    pristina = copy.deepcopy(primera)
    entradas_antes = (copy.deepcopy(roster), copy.deepcopy(stickers), copy.deepcopy(referencia))
    # mutate every list/dict the caller can reach
    for fila in primera.inspectores:
        fila["codigo"] = "MUTADO"
        fila["cedulas_unificadas"].append("MUTADA")
        fila["cedulas_unificadas"].clear()
    if primera.grupo_externos is not None:
        primera.grupo_externos["n_colapsados"] = -1
        for item in primera.grupo_externos["detalle"]:
            item["identificacion"] = "MUTADA"
    primera.alias_nombres["mutado"] = "MUTADO"
    for item in primera.revision_manual:
        item["motivo"] = "MUTADO"
    assert primera != pristina  # the mutation really did change the object we hold
    segunda = correr()
    assert segunda == pristina
    assert (roster, stickers, referencia) == entradas_antes


def test_depurar_two_results_from_the_same_inputs_share_no_mutable_object():
    fixture = _universo_completo()
    a, b = _correr(fixture), _correr(fixture)
    assert a == b
    for fila_a, fila_b in zip(a.inspectores, b.inspectores):
        assert fila_a is not fila_b and fila_a["cedulas_unificadas"] is not fila_b["cedulas_unificadas"]
    assert a.alias_nombres is not b.alias_nombres


# ── 9.22 no clock, no environment, no module state ──────────────────────────


class _RelojProhibido(type):
    """A stand-in for `date`/`datetime` that constructs and type-checks like the real
    class but raises on any read of the current time."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, cls._real)

    def __call__(cls, *args, **kwargs):
        return cls._real(*args, **kwargs)

    def __getattr__(cls, nombre):
        if nombre in {"today", "now", "utcnow", "fromtimestamp", "utcfromtimestamp"}:
            raise AssertionError(f"depurar() read the clock through {cls._real.__name__}.{nombre}")
        return getattr(cls._real, nombre)


class _EntornoProhibido(dict):
    def _no(self, *args, **kwargs):
        raise AssertionError("depurar() read the process environment")

    __getitem__ = get = __contains__ = __iter__ = keys = items = values = copy = _no


def _estado_del_modulo(*modulos):
    instantanea = {}
    for modulo in modulos:
        for nombre, valor in vars(modulo).items():
            if nombre.startswith("__") or isinstance(
                valor,
                (types.ModuleType, type, types.FunctionType, logging.Logger, re.Pattern, __future__._Feature),
            ):
                continue
            instantanea[f"{modulo.__name__}.{nombre}"] = copy.deepcopy(valor)
    return instantanea


@contextlib.contextmanager
def _sin_reloj_ni_entorno():
    def prohibido(*args, **kwargs):
        raise AssertionError("depurar() read the clock")

    with unittest.mock.patch.object(dep, "date", _RelojProhibido("date", (), {"_real": date})), \
            unittest.mock.patch.object(dep, "datetime", _RelojProhibido("datetime", (), {"_real": datetime})), \
            unittest.mock.patch.object(time, "time", prohibido), \
            unittest.mock.patch.object(os, "environ", _EntornoProhibido()):
        yield


def test_depurar_reads_no_clock_env_or_module_state():
    fixture = _universo_completo()
    esperado = _correr(fixture)
    estado_antes = _estado_del_modulo(dep, dep.cedula_utils)
    assert len(estado_antes) >= 8  # the snapshot is not vacuous (constants, patterns' owners, ...)
    with _sin_reloj_ni_entorno():
        resultado = _correr(fixture)
    assert resultado == esperado
    assert _estado_del_modulo(dep, dep.cedula_utils) == estado_antes


def test_the_clock_guard_itself_fires():
    # the guard is only worth something if it raises when the clock IS read
    with _sin_reloj_ni_entorno():
        with pytest.raises(AssertionError):
            dep.date.today()
        with pytest.raises(AssertionError):
            dep.datetime.now()
        with pytest.raises(AssertionError):
            time.time()
        with pytest.raises(AssertionError):
            os.environ.get("HOME")
        assert dep.datetime(2026, 8, 20) == datetime(2026, 8, 20)  # constructing is fine


def test_depurar_interleaved_calls_with_different_inputs_do_not_leak_state():
    fixture_a = _universo_completo()
    roster_b = {"x": _roster_entry("3030303", "Nora Otra", codigo="010")}
    fixture_b = (roster_b, [_main("4040404", "pablo main")], [_v("3030303", "010", "nora otra")], [],
                 [_sticker("3030303", fecha_creacion="2026-09-10T00:00:00Z")], ["Nora Otra"])
    aislado_a, aislado_b = _correr(fixture_a), _correr(fixture_b)
    assert aislado_a != aislado_b
    estado = _estado_del_modulo(dep, dep.cedula_utils)
    for _ in range(3):
        assert _correr(fixture_a) == aislado_a
        assert _correr(fixture_b) == aislado_b
    assert _estado_del_modulo(dep, dep.cedula_utils) == estado


def test_depurar_hoy_is_the_only_date_source():
    fixture = _universo_completo()
    hoy_a, hoy_b = date(2026, 9, 12), date(2026, 9, 30)
    a, b = _correr(fixture, hoy=hoy_a), _correr(fixture, hoy=hoy_b)
    filas_a, filas_b = _by_key(a), _by_key(b)
    comunes = set(filas_a) & set(filas_b)
    assert len(comunes) >= 8
    diferencias = set()
    for clave in comunes:
        for campo in filas_a[clave]:
            if filas_a[clave][campo] != filas_b[clave][campo]:
                diferencias.add(campo)
    assert diferencias == {"dias_inactivo"}  # nothing but the days-inactive figure moves
    # ... and the collapse membership moves with it: inactive-for-longer people collapse
    assert set(filas_a) != set(filas_b) or a.grupo_externos != b.grupo_externos
    assert b.grupo_externos["n_colapsados"] >= a.grupo_externos["n_colapsados"]
    assert a.alias_nombres == b.alias_nombres and a.revision_manual == b.revision_manual


# ── 9.23 idempotency and the performance canary ─────────────────────────────


def test_depurar_idempotent_same_inputs_equal_result():
    fixture = _universo_completo()
    primero = _correr(fixture)
    assert len(primero.inspectores) > 5
    assert _correr(fixture) == primero == _correr(fixture)


def _sintetico(n_perfiles=373, n_stickers=3000, *, stickers_atribuibles=True, vercel_sin_titular=False):
    rng = random.Random(20260919)
    cedulas = [str(10_000_000 + 7 * i) for i in range(n_perfiles)]
    main = [
        _entrada(
            cedula_key=c, nombre_norm=f"persona numero {i}", nombre=f"Persona Numero {i}",
            np=f"P{i % 4}", telefono=f"300{i:07d}", correo=f"p{i}@example.com",
        )
        for i, c in enumerate(cedulas)
    ]
    roster = {c: _roster_entry(c, f"Persona Numero {i}") for i, c in enumerate(cedulas[:130])}
    vercel = [
        _v(cedulas[i], f"{i + 100:03d}", f"persona numero {i}", np="P2", entidad="DAGRD")
        for i in range(0, n_perfiles, 3)
    ]
    if vercel_sin_titular:
        vercel += [_v(f"7{i:08d}", f"{i + 600:03d}", f"persona numero {i} bis") for i in range(n_perfiles)]
    fase2 = [_f2(cedulas[i], f"persona numero {i}") for i in range(0, n_perfiles, 4)]
    if stickers_atribuibles:
        cedulas_sticker = [rng.choice(cedulas) for _ in range(n_stickers)]
    else:
        cedulas_sticker = [f"9{rng.randrange(10**7, 10**8)}" for _ in range(n_stickers)]
    stickers = [
        _sticker(c, fecha_creacion=f"2026-09-{1 + rng.randrange(11):02d}T00:00:00Z") for c in cedulas_sticker
    ]
    survey = [f"Persona Numero {i}" for i in range(0, n_perfiles, 5)]
    return roster, main, vercel, fase2, stickers, survey


def _mejor_tiempo(fixture, repeticiones=5):
    mejor = float("inf")
    for _ in range(repeticiones):
        t0 = time.perf_counter()
        _correr(fixture)
        mejor = min(mejor, time.perf_counter() - t0)
    return mejor


def test_depurar_perf_canary_373_profiles_3000_stickers_under_500ms():
    fixture = _sintetico()
    resultado = _correr(fixture)
    assert len(resultado.inspectores) + (resultado.grupo_externos or {"n_colapsados": 0})["n_colapsados"] == 373
    assert any(f["ultimo_sticker"] for f in resultado.inspectores)  # stickers were really attributed
    mejor = _mejor_tiempo(fixture)
    assert mejor < 0.5, f"depurar() took {mejor:.3f}s (best of 5) for 373 profiles + 3000 stickers, budget 0.5s"


def test_depurar_perf_canary_worst_case_unattributable_stickers_and_fuzzy_remap_under_500ms():
    fixture = _sintetico(stickers_atribuibles=False, vercel_sin_titular=True)
    resultado = _correr(fixture)
    assert not any(f["ultimo_sticker"] for f in resultado.inspectores)  # none resolved
    assert sum(1 for r in resultado.revision_manual if r["motivo"] == "codigo_remap_candidato") > 100  # fuzzy ran
    mejor = _mejor_tiempo(fixture)
    assert mejor < 0.5, f"depurar() worst case took {mejor:.3f}s (best of 5), budget 0.5s"


# ── slice 08 — extra edge cases: holders with empty codes, huge inputs, logging ──


@pytest.mark.parametrize("vacio", ["", "   ", None, float("nan")])
def test_remapear_codigos_a_profile_with_an_empty_none_nan_or_blank_codigo_is_not_a_holder(vacio):
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo=vacio),
    }
    perfiles, revision = _remap(roster, [_v("9999999", "041", "nadie parecido a nadie")])
    assert _codigos(perfiles) == {"1111111": "", "2222222": ""}
    assert revision == ()  # nobody held 041, so there is no wrong holder to report


def test_remapear_codigos_huge_inputs_stay_fast():
    n = 3000

    def nombre(i):  # one 4-letter token: two neighbours score 75, far below the same-person cutoff
        return "".join("abcdefghij"[int(d)] for d in f"{i:04d}")

    roster = {
        str(10_000_000 + i): _roster_entry(str(10_000_000 + i), nombre(i), codigo=f"{i:04d}")
        for i in range(n)
    }
    # a huge rotation: profile i is the Vercel owner of the code profile i+1 holds today
    vercel = [_v(str(10_000_000 + i), f"{(i + 1) % n:04d}", nombre(i)) for i in range(n)]
    t0 = time.perf_counter()
    perfiles, revision = _remap(roster, vercel)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"remapear_codigos over {n} profiles took {elapsed:.2f}s"
    assert revision == ()
    assert all(perfiles[str(10_000_000 + i)].codigo == f"{(i + 1) % n:04d}" for i in range(n))


def test_fase2_cedula_fix_huge_inputs_stay_fast():
    n = 2000
    main = [_main(str(100 + i), f"persona numero {i}") for i in range(n)]  # all suspicious, all name-only
    fase2 = [_f2(str(20_000_000 + i), f"persona numero {i}") for i in range(n)]
    t0 = time.perf_counter()
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main, fase2=fase2))
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"fusionar_identidad with {n} Fase 2 fixes took {elapsed:.2f}s"
    assert len(perfiles) == n and revision == ()
    # every profile adopted its Fase 2 cédula, kept the suspicion of the ORIGINAL one and exports the old key
    assert all(p.cedula_sospechosa and len(p.cedulas_unificadas) == 1 for p in perfiles.values())
    assert set(perfiles) == {str(20_000_000 + i) for i in range(n)}


def test_slice_08_never_logs_names_cedulas_or_codes(caplog, monkeypatch):
    roster, main, vercel, fase2, stickers, survey = _universo_completo()
    # D-P2 would merge the two "Gina Seis" profiles before `alias_nombres`, so no name
    # collision would ever reach the logger and the assertions below would be vacuous.
    # Bypass the unification (and survey-name the pair) to force a REAL collision record.
    monkeypatch.setattr(dep, "unificar_duplicados", lambda perfiles: (perfiles, ()))
    fixture = (roster, main, vercel, fase2, stickers, [*survey, "Gina Seis"])
    with caplog.at_level(logging.DEBUG):
        resultado = _correr(fixture)
    assert resultado.revision_manual  # the remap/fix emitters ran
    propios = [r for r in caplog.records if r.name == dep.__name__]
    assert any("colision" in r.getMessage() for r in propios)  # a record WAS emitted
    texto = " ".join(r.getMessage() for r in caplog.records)
    for sensible in (
        "1111111", "3333333", "1053812345", "999", "6666666", "6666667",
        "Ana Uno", "ana uno", "Gina", "gina", "julia", "041", "052", "077",
    ):
        assert sensible not in texto, sensible


# ══════════════════════════════════════════════════════════════════════════
# Judgment-day fixes, slice 08 (C1, C2, W1-W7, S1-S6)
# ══════════════════════════════════════════════════════════════════════════


def _revision_de(revision, motivo):
    return [r for r in revision if r["motivo"] == motivo]


# ── C1 — D-MISMAPERSONA: a holder who IS the Vercel owner is never a wrong holder ──


def test_c1_a_holder_with_the_same_name_as_the_vercel_owner_keeps_its_codigo_end_to_end():
    # The reviewer's repro: the roster profile IS the person Vercel names (same name),
    # only the cédula differs (a corrected/never-imported one).
    roster = {"1234567": _roster_entry("1234567", "Juan Perez Gomez", codigo="041")}
    resultado = _depurar(roster=roster, vercel=[_v("1099999999", "041", "juan perez gomez")])
    fila = _by_key(resultado)["1234567"]
    assert fila["codigo"] == "041" and fila["estado_sugerido"] == "activo"
    assert resultado.revision_manual == ()


def test_c1_route_cedula_key_holder_keeps_the_code_even_when_the_names_differ():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="041")}
    perfiles, revision = _remap(roster, [_v("1.111.111", "041", "un nombre completamente otro")])
    assert _codigos(perfiles) == {"1111111": "041"} and revision == ()


def test_c1_route_fase2_cedula_holder_keeps_the_code_when_its_fix_was_blocked():
    # Ana (main 999) matched Fase 2 by name and WOULD adopt 1053812345, but Carla already
    # owns that key: the fix is blocked, yet Ana's Fase 2 cédula still says she is the
    # person Vercel registered under it. The names are unrelated on purpose.
    roster = {"1053812345": _roster_entry("1053812345", "Carla Otra")}
    main = [_main("999", "ana gomez")]
    ref = _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")],
                  vercel=[_v("1053812345", "041", "zzz completamente distinto")])
    perfiles, _ = dep.fusionar_identidad([], roster, ref)
    perfiles["999"].codigo = "041"  # what the Firestore/main data says today
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert _codigos(perfiles) == {"1053812345": "", "999": "041"}  # nothing changes, nothing reported
    assert revision == ()


def test_c1_route_pre_fix_cedula_holder_keeps_the_code_after_the_fase2_fix():
    # notebook 4.d compares the ORIGINAL main cédula: Vercel still names "999".
    main = [_main("999", "ana gomez", codigo="041")]
    ref = _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")],
                  vercel=[_v("999", "041", "otro nombre sin relacion")])
    perfiles, _ = dep.fusionar_identidad([], {}, ref)
    assert set(perfiles) == {"1053812345"}
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1053812345"].codigo == "041" and revision == ()


@pytest.mark.parametrize("nombre_holder, nombre_vercel, misma", [
    ("abcdefghij", "abcdefghix", True),      # token_sort_ratio == 90.0: the cutoff is inclusive
    ("abcdefghi", "abcdefghx", False),       # 88.9: below the cutoff
    ("juan perez gomez", "gomez perez juan", True),  # token order is irrelevant (100.0)
    ("juan perez gomez", "juan perez gomes", True),  # 93.75
    ("juan perez", "maria lopez", False),
])
def test_c1_route_fuzzy_name_boundary_is_inclusive_at_90(nombre_holder, nombre_vercel, misma):
    from rapidfuzz import fuzz
    assert (fuzz.token_sort_ratio(nombre_holder, nombre_vercel) >= 90.0) is misma  # fixture sanity
    roster = {"1111111": _roster_entry("1111111", nombre_holder, codigo="041")}
    perfiles, revision = _remap(roster, [_v("9999999", "041", nombre_vercel)])
    if misma:
        assert _codigos(perfiles) == {"1111111": "041"} and revision == ()
    else:
        assert _codigos(perfiles) == {"1111111": ""}
        assert _revision_de(revision, "remap_sin_duenio")


def test_c1_a_different_person_holder_is_still_cleared():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": ""} and revision == ()


@pytest.mark.parametrize("nombre_holder, nombre_vercel", [
    ("", ""), ("   ", "  "), ("", "ana uno"), ("Ana Uno", ""), ("Ana Uno", "   "),
])
def test_c1_an_empty_name_never_matches_by_fuzzy(nombre_holder, nombre_vercel):
    # rapidfuzz scores two empty strings as 100: the engine must not read that as "same person"
    roster = {"1111111": _roster_entry("1111111", nombre_holder, codigo="041")}
    perfiles, revision = _remap(roster, [_v("9999999", "041", nombre_vercel)])
    assert _codigos(perfiles) == {"1111111": ""}
    assert _revision_de(revision, "remap_sin_duenio")


def test_c1_an_empty_vercel_cedula_never_matches_the_holder_by_cedula():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="041")}
    perfiles, revision = _remap(roster, [_v("", "041", "otra persona")])
    assert _codigos(perfiles) == {"1111111": ""}
    assert _revision_de(revision, "remap_sin_duenio")


def test_c1_same_person_holder_keeps_the_code_while_the_other_holders_are_cleared():
    # D-REMAP-TODOS (W4) and D-MISMAPERSONA together: three holders of 041, only the
    # one that IS the Vercel owner (by name) keeps it. No sin_duenio, no candidate.
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo="041"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
        "3333333": _roster_entry("3333333", "Carla Tres", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("9999999", "041", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": "", "3333333": ""}
    assert revision == ()


def test_c1_the_cedula_owner_is_not_assigned_the_code_when_another_holder_is_the_same_person():
    # notebook parity: misma_persona=True means NO remap row at all, so the profile that
    # owns the Vercel cédula does not receive the code either.
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno"),
        "2222222": _roster_entry("2222222", "Ana Uno", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "", "2222222": "041"} and revision == ()


def test_c1_a_titular_holder_with_two_vercel_codes_is_still_a_conflict():
    # the guard must not hide a person registered twice in Vercel
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="041")}
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno"), _v("1111111", "052", "ana uno")])
    assert _codigos(perfiles) == {"1111111": ""}
    assert {r["codigo"] for r in _revision_de(revision, "remap_conflicto")} == {"041", "052"}


def test_c1_the_code_stays_with_its_holder_in_any_roster_order():
    filas = [("1111111", "Ana Uno", "041"), ("2222222", "Beto Dos", "041"), ("3333333", "Ana Uno Bis", "")]
    for orden in itertools.permutations(filas):
        roster = {c: _roster_entry(c, n, codigo=k) for c, n, k in orden}
        perfiles, revision = _remap(roster, [_v("9999999", "041", "ana uno")])
        assert _codigos(perfiles) == {"1111111": "041", "2222222": "", "3333333": ""}
        assert revision == ()


# ── C2 — the Vercel overlay is re-run for a profile whose cédula the Fase 2 fix rewrote ──


# a sticker keeps the (suspicious-cédula) profile out of GRUPO-EXTERNOS so the row is observable
_ST999 = [_sticker("999", fecha_creacion="2026-09-10T00:00:00Z")]


def _fixture_c2(vercel_entidad="DAGRD", fase2_np="P3"):
    main = [_main("999", "ana gomez")]
    fase2 = [_f2("1053812345", "ana gomez", np=fase2_np)]
    vercel = [_v("1053812345", "041", "ana g. gomez", np="P2", entidad=vercel_entidad)]
    return main, fase2, vercel


def test_c2_the_fixed_cedula_finds_its_vercel_row_entidad_fuente_dato_and_codigo():
    main, fase2, vercel = _fixture_c2()
    resultado = _depurar(main=main, fase2=fase2, vercel=vercel, stickers=_ST999)
    assert list(_by_key(resultado)) == ["1053812345"]
    fila = _by_key(resultado)["1053812345"]
    assert fila["entidad"] == "DAGRD"
    assert fila["fuente_dato"] == "main+fase2+vercel"
    assert fila["codigo"] == "041"
    assert (fila["np"], fila["np_fuente"]) == ("P3", "fase2")  # hierarchy unchanged: fase2 first


def test_c2_np_falls_back_to_the_vercel_np_found_by_the_fixed_cedula():
    main, fase2, vercel = _fixture_c2(fase2_np="")
    fila = _by_key(_depurar(main=main, fase2=fase2, vercel=vercel, stickers=_ST999))["1053812345"]
    assert (fila["np"], fila["np_fuente"]) == ("P2", "vercel")


def test_c2_a_fixed_profile_without_a_vercel_row_is_left_alone():
    main = [_main("999", "ana gomez")]
    fila = _by_key(_depurar(main=main, fase2=[_f2("1053812345", "ana gomez")], stickers=_ST999))["1053812345"]
    assert fila["entidad"] == "" and fila["fuente_dato"] == "main+fase2"
    assert fila["estado_sugerido"] != "activo"


def test_c2_a_name_only_vercel_hit_before_the_fix_is_kept_and_never_supplies_entidad():
    # Vercel row matched by NAME (different cédula): en_vercel/np stay, entidad is never taken
    main = [_main("999", "ana gomez")]
    vercel = [_v("5555555", "041", "ana gomez", np="P4", entidad="OTRA")]
    fila = _by_key(_depurar(main=main, fase2=[_f2("1053812345", "ana gomez")], vercel=vercel, stickers=_ST999))["1053812345"]
    assert fila["entidad"] == "" and fila["fuente_dato"] == "main+fase2+vercel"


def test_c2_a_blocked_fix_does_not_re_overlay():
    roster = {"1053812345": _roster_entry("1053812345", "Carla Otra")}
    main = [_main("999", "ana gomez")]
    ref = _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")],
                  vercel=[_v("1053812345", "041", "carla otra", entidad="DAGRD")])
    perfiles, revision = dep.fusionar_identidad([], roster, ref)
    assert _revision_de(revision, "fase2_cedula_colision")
    ana = perfiles["999"]
    assert (ana.en_vercel, ana.entidad_vercel) == (False, "")  # Carla's row is not Ana's
    assert perfiles["1053812345"].entidad_vercel == "DAGRD"


def test_c2_profiles_not_touched_by_the_fix_are_identical_before_and_after():
    main = [_main("999", "ana gomez"), _main("1111111", "beto dos"), _main("2222222", "carla tres")]
    fase2 = [_f2("1053812345", "ana gomez"), _f2("1111111", "beto dos")]
    vercel = [_v("1111111", "041", "beto dos", entidad="X"), _v("1053812345", "052", "ana", entidad="Y")]
    ref = _bundle(main=main, fase2=fase2, vercel=vercel)
    perfiles, _ = dep.construir_universo({}, ref)
    dep.superponer_referencia(perfiles, ref)
    dep.atribuir_stickers(perfiles, [])
    antes = {k: copy.copy(p) for k, p in perfiles.items()}
    despues, _ = dep.corregir_cedula_fase2(perfiles, ref)
    assert set(despues) == {"1053812345", "1111111", "2222222"}
    for clave in ("1111111", "2222222"):
        assert despues[clave] == antes[clave]
    assert despues["1053812345"].entidad_vercel == "Y" and antes["999"].entidad_vercel == ""


def test_c2_the_re_overlay_is_exact_key_only_never_by_name():
    # the Vercel row shares the NAME but its cédula is a different one: nothing is adopted
    main = [_main("999", "ana gomez")]
    vercel = [_v("7777777", "041", "ana gomez", entidad="DAGRD")]
    ref = _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")], vercel=vercel)
    perfiles, _ = dep.fusionar_identidad([], {}, ref)
    assert perfiles["1053812345"].entidad_vercel == ""  # only the pre-fix NAME match, never entidad


# ── W3 — the remap owner lookup also resolves through the Fase 2 cédula ──


def test_w3_an_ambiguous_fase2_cedula_reassigns_nothing_and_clears_nobody():
    main = [_main("999", "ana gomez"), _entrada(cedula_key="998", nombre_norm="ana gomez", nombre="Ana Gomez B")]
    roster = {"2222222": _roster_entry("2222222", "Beto Dos", codigo="041")}
    ref = _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")],
                  vercel=[_v("1053812345", "041", "otro nombre")])
    perfiles, revision = dep.fusionar_identidad([], roster, ref)
    assert _revision_de(revision, "fase2_cedula_colision")  # the fix was blocked twice
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert _codigos(perfiles) == {"2222222": "041", "998": "", "999": ""}
    assert revision == ({
        "motivo": "remap_owner_ambiguo", "codigo": "041", "cedula_key": "1053812345",
        "identidad_keys": ["998", "999"], "identidad_keys_titulares": ["2222222"],
    },)


def test_w3_a_unique_fase2_cedula_resolves_the_owner():
    perfiles = {
        "999": _perfil("999", "ana gomez", cedula_fase2="1053812345"),
        "2222222": _perfil("2222222", "beto dos", codigo="041"),
    }
    ref = _bundle(vercel=[_v("1053812345", "041", "nombre sin relacion")])
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert _codigos(perfiles) == {"999": "041", "2222222": ""} and revision == ()


def test_w3_an_own_key_owner_wins_over_another_profiles_fase2_claim():
    perfiles = {
        "1053812345": _perfil("1053812345", "carla otra"),
        "999": _perfil("999", "ana gomez", cedula_fase2="1053812345"),
        "2222222": _perfil("2222222", "beto dos", codigo="041"),
    }
    ref = _bundle(vercel=[_v("1053812345", "041", "nombre sin relacion")])
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    # Ana is not "cleared" (she holds nothing); Carla owns the cédula outright; Beto loses 041
    assert _codigos(perfiles) == {"1053812345": "041", "999": "", "2222222": ""} and revision == ()


def test_w3_an_empty_fase2_cedula_is_never_indexed():
    perfiles = {
        "1": _perfil("1", "a b", cedula_fase2=""),
        "2": _perfil("2", "c d", cedula_fase2=""),
        "3": _perfil("3", "e f", codigo="041"),
    }
    perfiles, revision = dep.remapear_codigos(perfiles, _bundle(vercel=[_v("", "041", "zzz")]))
    assert revision[0]["motivo"] == "remap_sin_duenio"
    assert not _revision_de(revision, "remap_owner_ambiguo")


def test_w3_the_ambiguity_is_reported_once_per_code_whatever_the_order():
    for orden in itertools.permutations(["1", "2", "3"]):
        perfiles = {k: _perfil(k, f"persona {k}", cedula_fase2="555") for k in orden}
        perfiles["9"] = _perfil("9", "beto", codigo="041")
        _, revision = dep.remapear_codigos(perfiles, _bundle(vercel=[_v("555", "041", "zzz")]))
        assert revision == ({
            "motivo": "remap_owner_ambiguo", "codigo": "041", "cedula_key": "555",
            "identidad_keys": ["1", "2", "3"], "identidad_keys_titulares": ["9"],
        },)


def test_w3_the_fase2_cedula_is_recorded_even_when_the_fix_is_blocked():
    main = [_main("999", "ana gomez"), _main("998", "ana gomez")]
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=main, fase2=[_f2("1053812345", "ana gomez")]))
    assert {p.cedula_fase2 for p in perfiles.values()} == {"1053812345"}
    assert set(perfiles) == {"998", "999"}


# ── S2 — the review items carry the identidad_key of the profile they are about ──


def test_s2_remap_sin_duenio_carries_the_key_of_every_cleared_holder():
    roster = {
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
        "3333333": _roster_entry("3333333", "Carla Tres", codigo="041"),
    }
    _, revision = _remap(roster, [_v("9999999", "041", "nadie parecido")])
    assert sorted(revision, key=lambda r: r["identidad_key"]) == [
        {"motivo": "remap_sin_duenio", "codigo": "041", "identidad_key": "2222222"},
        {"motivo": "remap_sin_duenio", "codigo": "041", "identidad_key": "3333333"},
    ]


def test_s2_remap_conflicto_carries_the_key_of_the_contended_profile():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno")}
    _, revision = _remap(roster, [_v("1111111", "041", "ana uno"), _v("1111111", "052", "ana uno")])
    assert sorted(revision, key=lambda r: r["codigo"]) == [
        {"motivo": "remap_conflicto", "codigo": "041", "identidad_key": "1111111"},
        {"motivo": "remap_conflicto", "codigo": "052", "identidad_key": "1111111"},
    ]


def test_s2_the_canonical_order_uses_the_new_identidad_key():
    roster = {
        "3333333": _roster_entry("3333333", "Carla Tres", codigo="041"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
    }
    resultado = _depurar(roster=roster, vercel=[_v("9999999", "041", "nadie parecido")])
    assert [r["identidad_key"] for r in resultado.revision_manual] == ["2222222", "3333333"]


# ── W6 — identical review items collapse into ONE item carrying `n_ocurrencias` ──


def test_w6_three_identical_duplicate_main_rows_report_two_duplicates():
    main = [_main("9000001", "kate uno")] * 3
    revision = _depurar(main=main).revision_manual
    assert len(revision) == 1 and revision[0]["motivo"] == "cedula_duplicada_main"
    assert revision[0]["n_ocurrencias"] == 2


def test_w6_two_main_rows_without_a_cedula_report_two_occurrences():
    main = [_entrada(cedula_key="N/A", nombre_norm="sin cedula", nombre="Sin Cedula")] * 2
    revision = _depurar(main=main).revision_manual
    assert [(r["motivo"], r["n_ocurrencias"]) for r in revision] == [("main_sin_cedula", 2)]


def test_w6_a_singleton_item_carries_no_counter_and_keeps_its_shape():
    main = [_main("9000001", "kate uno", id="a"), _main("9000001", "kate uno", id="b")]
    revision = _depurar(main=main).revision_manual
    assert len(revision) == 1 and "n_ocurrencias" not in revision[0]


def test_w6_the_counting_is_idempotent_and_composable():
    item = dep._revision("main_sin_cedula", cedula_key="", nombre_completo="X", id="")
    una = dep._canonizar_revision([dict(item)] * 3)
    assert len(una) == 1 and una[0]["n_ocurrencias"] == 3
    assert dep._canonizar_revision(una) == una
    assert dep._canonizar_revision([*una, dict(item)])[0]["n_ocurrencias"] == 4
    assert dep._canonizar_revision([]) == ()


def test_w6_the_result_does_not_depend_on_the_input_order():
    a = dep._revision("cedula_duplicada_main", cedula_key="1", id="a")
    b = dep._revision("cedula_duplicada_main", cedula_key="1", id="b")
    c = dep._revision("main_sin_cedula", cedula_key="")
    esperado = dep._canonizar_revision([a, a, b, c, c, c])
    assert sorted(r.get("n_ocurrencias", 1) for r in esperado) == [1, 2, 3]
    for orden in itertools.permutations([a, a, b, c, c, c]):
        assert dep._canonizar_revision(orden) == esperado


def test_w6_the_input_items_are_not_mutated():
    item = dep._revision("main_sin_cedula", cedula_key="")
    dep._canonizar_revision([item, dict(item)])
    assert item == {"motivo": "main_sin_cedula", "cedula_key": ""}


# ── S1 — the worst case stays within a jitter-proof ceiling of the normal case (same run) ──


def test_s1_worst_case_is_within_a_jitter_proof_ceiling_of_the_normal_case_in_the_same_run():
    # the normal case takes 19-40 ms, so a pure ratio is dominated by scheduler jitter
    # (measured 3.4x-6.6x on an idle machine): the ceiling is 10x OR an absolute 150 ms,
    # whichever is larger; the absolute 500 ms canaries above stay as they are
    normal = _mejor_tiempo(_sintetico(), repeticiones=7)
    peor = _mejor_tiempo(_sintetico(stickers_atribuibles=False, vercel_sin_titular=True), repeticiones=7)
    techo = max(10 * normal, 0.15)
    assert peor <= techo, f"worst {peor:.3f}s vs normal {normal:.3f}s (ratio {peor / normal:.1f}x, ceiling {techo:.3f}s)"


# ══════════════════════════════════════════════════════════════════════════
# Judgment-day round 2, slice 08 (C-1, W-1, W-3, W-4)
# ══════════════════════════════════════════════════════════════════════════


# ── C-1 — D-MISMOSTITULARES: two holders that are the same person keep ONE código ──


def _mismos(revision):
    return _revision_de(revision, "remap_mismos_titulares")


def test_c1r2_reproduced_two_fuzzy_same_person_holders_keep_exactly_one_codigo():
    # the reviewer's repro: token_sort_ratio 93.3 between the two holders, both >= 90
    # against the Vercel name, neither owns the Vercel cédula (a third profile does).
    roster = {
        "1111111111": _roster_entry("1111111111", "Ana Maria Gomez", codigo="041"),
        "2222222222": _roster_entry("2222222222", "Ana Maria Gomes", codigo="041"),
        "3333333333": _roster_entry("3333333333", "Duenio Vercel"),
    }
    perfiles, revision = _remap(roster, [_v("3333333333", "041", "ana maria gomez")])
    assert _codigos(perfiles) == {"1111111111": "041", "2222222222": "", "3333333333": ""}
    assert revision == ({
        "motivo": "remap_mismos_titulares", "codigo": "041",
        "identidad_key": "2222222222", "identidad_key_conservado": "1111111111",
    },)


def test_c1r2_reproduced_end_to_end_one_activo_and_the_review_item():
    roster = {
        "1111111111": _roster_entry("1111111111", "Ana Maria Gomez", codigo="041"),
        "2222222222": _roster_entry("2222222222", "Ana Maria Gomes", codigo="041"),
        "3333333333": _roster_entry("3333333333", "Duenio Vercel"),
    }
    stickers = [_sticker(c, fecha_creacion="2026-09-10T00:00:00Z") for c in roster]
    resultado = _depurar(roster=roster, vercel=[_v("3333333333", "041", "ana maria gomez")], stickers=stickers)
    filas = _by_key(resultado)
    assert [k for k, f in filas.items() if f["codigo"] == "041"] == ["1111111111"]
    assert filas["1111111111"]["estado_sugerido"] == "activo"
    assert filas["2222222222"]["estado_sugerido"] != "activo"
    assert [r["motivo"] for r in resultado.revision_manual] == ["remap_mismos_titulares"]


def test_c1r2_the_cedula_owner_keeps_it_even_with_a_lower_fuzzy_score():
    # the owner ("gomes", 93.3) beats the exact-name holder ("gomez", 100)
    roster = {
        "1111111111": _roster_entry("1111111111", "Ana Maria Gomes", codigo="041"),
        "2222222222": _roster_entry("2222222222", "Ana Maria Gomez", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("1111111111", "041", "ana maria gomez")])
    assert _codigos(perfiles) == {"1111111111": "041", "2222222222": ""}
    assert _mismos(revision) == [{
        "motivo": "remap_mismos_titulares", "codigo": "041",
        "identidad_key": "2222222222", "identidad_key_conservado": "1111111111",
    }]


def test_c1r2_the_pre_fix_cedula_holder_is_the_cedula_owner_too():
    perfiles = {
        "1111111": _perfil("1111111", "ana maria gomes", codigo="041", cedula_original="9999999"),
        "2222222": _perfil("2222222", "ana maria gomez", codigo="041"),
    }
    perfiles, revision = dep.remapear_codigos(perfiles, _bundle(vercel=[_v("9999999", "041", "ana maria gomez")]))
    assert _codigos(perfiles) == {"1111111": "041", "2222222": ""}
    assert [(r["identidad_key"], r["identidad_key_conservado"]) for r in _mismos(revision)] == [("2222222", "1111111")]


def test_c1r2_the_fase2_cedula_owner_is_the_cedula_owner_and_is_not_given_the_code_twice():
    # "9" is the unique Fase 2 owner of the Vercel cédula and is also a same-person holder:
    # it keeps the code, the fuzzy-better holder "1" is cleared, and "9" is not re-assigned
    # on top of a cleared holder (the claim step runs after the clearing).
    perfiles = {
        "1": _perfil("1", "ana maria gomez", codigo="041"),
        "9": _perfil("9", "ana maria gomes", codigo="041", cedula_fase2="5555555"),
    }
    perfiles, revision = dep.remapear_codigos(perfiles, _bundle(vercel=[_v("5555555", "041", "ana maria gomez")]))
    assert _codigos(perfiles) == {"1": "", "9": "041"}
    assert [(r["identidad_key"], r["identidad_key_conservado"]) for r in _mismos(revision)] == [("1", "9")]


def test_c1r2_three_same_person_holders_keep_the_best_scored_and_report_both_others():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Maria Gomes", codigo="041"),   # 93.3
        "2222222": _roster_entry("2222222", "Ana Maria Gomez", codigo="041"),   # 100
        "3333333": _roster_entry("3333333", "Ana Maria Gomz", codigo="041"),    # >= 90
    }
    from rapidfuzz import fuzz
    assert fuzz.token_sort_ratio("ana maria gomz", "ana maria gomez") >= 90.0  # fixture sanity
    perfiles, revision = _remap(roster, [_v("9999999", "041", "ana maria gomez")])
    assert _codigos(perfiles) == {"1111111": "", "2222222": "041", "3333333": ""}
    assert [(r["identidad_key"], r["identidad_key_conservado"], r["codigo"]) for r in _mismos(revision)] == [
        ("1111111", "2222222", "041"), ("3333333", "2222222", "041"),
    ]


def test_c1r2_a_score_tie_is_decided_by_the_lowest_identidad_key():
    # token order is irrelevant: all three score 100.0 against "ana maria gomez"
    roster = {
        "3333333": _roster_entry("3333333", "Gomez Ana Maria", codigo="041"),
        "1111111": _roster_entry("1111111", "Ana Maria Gomez", codigo="041"),
        "2222222": _roster_entry("2222222", "Maria Gomez Ana", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("9999999", "041", "ana maria gomez")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": "", "3333333": ""}
    assert [r["identidad_key"] for r in _mismos(revision)] == ["2222222", "3333333"]
    assert {r["identidad_key_conservado"] for r in _mismos(revision)} == {"1111111"}


def test_c1r2_the_boundary_score_90_counts_as_the_same_person_and_89_9_does_not():
    from rapidfuzz import fuzz
    assert fuzz.token_sort_ratio("abcdefghij", "abcdefghix") == 90.0  # fixture sanity
    assert fuzz.token_sort_ratio("abcdefghi", "abcdefghx") < 90.0
    roster = {
        "1111111": _roster_entry("1111111", "abcdefghij", codigo="041"),   # 100
        "2222222": _roster_entry("2222222", "abcdefghix", codigo="041"),   # 90.0: the same person
        "3333333": _roster_entry("3333333", "abcdefghyz", codigo="041"),   # 80: a different person
    }
    perfiles, revision = _remap(roster, [_v("9999999", "041", "abcdefghij")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": "", "3333333": ""}
    # the boundary holder is reported (same person), the different one is cleared silently
    assert [r["identidad_key"] for r in _mismos(revision)] == ["2222222"]
    assert len(revision) == 1


def test_c1r2_the_result_never_depends_on_the_roster_order():
    filas = [
        ("1111111", "Ana Maria Gomez", "041"), ("2222222", "Ana Maria Gomes", "041"),
        ("3333333", "Gomez Ana Maria", "041"), ("4444444", "Zulema Katz", "041"),
        ("5555555", "Duenio Vercel", ""),
    ]
    esperado = None
    for orden in itertools.permutations(filas):  # 120 orders
        roster = {c: _roster_entry(c, n, codigo=k) for c, n, k in orden}
        perfiles, revision = _remap(roster, [_v("5555555", "041", "ana maria gomez")])
        resultado = (_codigos(perfiles), revision)
        esperado = esperado or resultado
        assert resultado == esperado
    assert esperado[0] == {"1111111": "041", "2222222": "", "3333333": "", "4444444": "", "5555555": ""}
    assert [r["identidad_key"] for r in esperado[1]] == ["2222222", "3333333"]


def test_c1r2_the_full_pipeline_is_deterministic_over_25_plus_roster_orders():
    filas = [("1111111", "Ana Maria Gomez"), ("2222222", "Ana Maria Gomes"), ("3333333", "Ana Maria Gomz"),
             ("4444444", "Otra Persona"), ("5555555", "Duenio Vercel")]
    primero = None
    for orden in itertools.islice(itertools.permutations(filas), 30):
        roster = {c: _roster_entry(c, n, codigo="041" if c != "5555555" else "") for c, n in orden}
        resultado = _depurar(roster=roster, vercel=[_v("5555555", "041", "ana maria gomez")])
        instantanea = (resultado.inspectores, resultado.revision_manual)
        primero = primero or instantanea
        assert instantanea == primero
    assert sum(1 for f in primero[0] if f["codigo"] == "041") == 1


def test_c1r2_zero_padded_codes_are_not_conflated():
    # "021" and "21" are different codes: only the exact "021" holders are titulares of it
    roster = {
        "1111111": _roster_entry("1111111", "Ana Maria Gomez", codigo="021"),
        "2222222": _roster_entry("2222222", "Ana Maria Gomes", codigo="21"),
    }
    perfiles, revision = _remap(roster, [_v("9999999", "021", "ana maria gomez")])
    assert _codigos(perfiles) == {"1111111": "021", "2222222": "21"}
    assert revision == ()


def test_c1r2_a_single_same_person_holder_is_unchanged_and_reports_nothing():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Maria Gomez", codigo="041"),
        "2222222": _roster_entry("2222222", "Beto Dos"),
    }
    perfiles, revision = _remap(roster, [_v("9999999", "041", "ana maria gomez")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": ""}
    assert revision == ()


def test_c1r2_different_person_holders_alongside_are_cleared_without_an_item():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Maria Gomez", codigo="041"),
        "2222222": _roster_entry("2222222", "Ana Maria Gomes", codigo="041"),
        "3333333": _roster_entry("3333333", "Beto Dos", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("9999999", "041", "ana maria gomez")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": "", "3333333": ""}
    assert [r["identidad_key"] for r in revision] == ["2222222"]  # only the same-person one is reported


def test_c1r2_holders_matching_only_by_cedula_with_empty_names_still_keep_exactly_one():
    # empty names never match by fuzzy; the two holders are the same person by CÉDULA
    # (own key vs the pre-fix key): the own-key owner keeps it
    perfiles = {
        "1111111": _perfil("1111111", "", codigo="041"),
        "2222222": _perfil("2222222", "", codigo="041", cedula_original="1111111"),
    }
    perfiles, revision = dep.remapear_codigos(perfiles, _bundle(vercel=[_v("1111111", "041", "")]))
    assert _codigos(perfiles) == {"1111111": "041", "2222222": ""}
    assert [(r["identidad_key"], r["identidad_key_conservado"]) for r in _mismos(revision)] == [("2222222", "1111111")]


def test_c1r2_whitespace_only_names_never_count_as_the_same_person():
    roster = {
        "1111111": _roster_entry("1111111", "   ", codigo="041"),
        "2222222": _roster_entry("2222222", "", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("9999999", "041", "  ")])
    assert _codigos(perfiles) == {"1111111": "", "2222222": ""}
    assert not _mismos(revision)


def test_c1r2_a_person_registered_twice_in_vercel_is_still_a_conflict():
    # the person is registered twice in Vercel (041 and 052) -> conflict, nobody keeps either
    perfiles = {
        "1111111": _perfil("1111111", "ana maria gomez", codigo="041"),
        "2222222": _perfil("2222222", "ana maria gomes", codigo="041"),
    }
    ref = _bundle(vercel=[_v("1111111", "041", "ana maria gomez"), _v("1111111", "052", "ana maria gomez")])
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    # Round 3 (C-1): this test used to pin the BUG by asserting only that every código was
    # empty. The owner is emptied by its conflict, so the same-person twin is NOT cleared on
    # its behalf (that would leave 041 with nobody): it keeps 041 and the conflict reports it.
    assert _codigos(perfiles) == {"1111111": "", "2222222": "041"}
    assert {r["codigo"] for r in _revision_de(revision, "remap_conflicto")} == {"041", "052"}
    assert not _mismos(revision)


def test_c1r2_many_same_person_holders_stay_fast_and_keep_the_lowest_key():
    roster = {f"{10_000_000 + i}": _roster_entry(f"{10_000_000 + i}", "Ana Maria Gomez", codigo="041") for i in range(600)}
    t0 = time.perf_counter()
    perfiles, revision = _remap(roster, [_v("9999999", "041", "ana maria gomez")])
    assert time.perf_counter() - t0 < 3.0
    assert [k for k, c in _codigos(perfiles).items() if c] == ["10000000"]
    assert len(_mismos(revision)) == 599 and {r["identidad_key_conservado"] for r in revision} == {"10000000"}


def test_c1r2_inputs_are_not_mutated_and_the_items_are_fresh_dicts():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Maria Gomez", codigo="041"),
        "2222222": _roster_entry("2222222", "Ana Maria Gomes", codigo="041"),
    }
    vercel = [_v("9999999", "041", "ana maria gomez")]
    antes = copy.deepcopy(roster)
    a = _depurar(roster=roster, vercel=vercel)
    assert roster == antes
    a.revision_manual[0]["identidad_key"] = "tampered"
    b = _depurar(roster=roster, vercel=vercel)
    assert b.revision_manual[0]["identidad_key"] == "2222222"


def test_c1r2_no_pii_reaches_the_logs(caplog):
    roster = {
        "1111111111": _roster_entry("1111111111", "Zoraida Quintero Gomez", codigo="041"),
        "2222222222": _roster_entry("2222222222", "Zoraida Quintero Gomes", codigo="041"),
    }
    with caplog.at_level(logging.DEBUG):
        _depurar(roster=roster, vercel=[_v("9999999999", "041", "zoraida quintero gomez")])
    assert "zoraida" not in caplog.text.lower()
    assert "1111111111" not in caplog.text and "2222222222" not in caplog.text


# ── W-1 — the re-overlay never wipes a non-empty np_vercel (notebook 8.1 re-resolves only entidad) ──


def _w1(vercel_prefix_np, vercel_fixed_np, *, fixed_entidad="DAGRD"):
    main = [_main("999", "ana gomez")]
    fase2 = [_f2("1053812345", "ana gomez", np="")]  # name-only match: it triggers the D18 fix
    vercel = [
        _entrada(cedula_key="4242", nombre_norm="ana gomez", nombre="Ana Gomez", np=vercel_prefix_np, entidad="X"),
        _entrada(cedula_key="1053812345", nombre_norm="zzz otro", nombre="Zzz Otro", np=vercel_fixed_np,
                 entidad=fixed_entidad),
    ]
    return _by_key(_depurar(main=main, fase2=fase2, vercel=vercel, stickers=_ST999))["1053812345"]


def test_w1r2_an_empty_np_on_the_corrected_cedula_row_keeps_the_pre_fix_np_vercel():
    # the reviewer's repro (C2.7)
    fila = _w1("P7", "")
    assert (fila["np"], fila["np_fuente"], fila["fase"]) == ("P7", "vercel", "Fase II")
    assert fila["entidad"] == "DAGRD"  # entidad IS re-resolved from the corrected cédula


def test_w1r2_a_whitespace_only_np_on_the_corrected_row_keeps_the_pre_fix_np_vercel():
    assert _w1("P7", "   ")["np"] == "P7"


def test_w1r2_a_different_non_empty_np_on_the_corrected_row_does_not_replace_it():
    # the notebook takes np_vercel from the FIRST (name / cédula) Vercel match and re-resolves
    # ONLY entidad on the corrected identificacion
    fila = _w1("P7", "P9")
    assert (fila["np"], fila["np_fuente"]) == ("P7", "vercel")
    assert fila["entidad"] == "DAGRD"


def test_w1r2_an_empty_pre_fix_np_is_filled_from_the_corrected_row():
    fila = _w1("", "P9")
    assert (fila["np"], fila["np_fuente"]) == ("P9", "vercel")


def test_w1r2_both_empty_stays_empty():
    fila = _w1("", "")
    assert (fila["np"], fila["np_fuente"]) == ("", "ninguno")
    assert fila["fuente_dato"] == "main+fase2+vercel"


def test_w1r2_aplicar_vercel_keeps_the_first_non_empty_np_and_entidad():
    perfil = _perfil("1", "ana", en_vercel=True, np_vercel="P7", entidad_vercel="VIEJA")
    dep._aplicar_vercel(perfil, _entrada(cedula_key="1", np="", entidad="NUEVA"), True)
    assert (perfil.en_vercel, perfil.np_vercel, perfil.entidad_vercel) == (True, "P7", "VIEJA")
    dep._aplicar_vercel(perfil, _entrada(cedula_key="1", np="P9", entidad="NUEVA"), True)
    assert (perfil.np_vercel, perfil.entidad_vercel) == ("P7", "VIEJA")


def test_w1r2_the_fix_still_re_resolves_entidad_from_the_corrected_cedula_row():
    # pre-fix cédula "999" matched a Vercel row BY CÉDULA (entidad VIEJA, np P7); the corrected
    # cédula's row says NUEVA: entidad is re-resolved (D-FIXOVERLAY), np_vercel is not replaced
    main = [_main("999", "ana gomez")]
    fase2 = [_f2("1053812345", "ana gomez", np="")]
    vercel = [
        _entrada(cedula_key="999", nombre_norm="ana gomez", nombre="Ana Gomez", np="P7", entidad="VIEJA"),
        _entrada(cedula_key="1053812345", nombre_norm="zzz otro", nombre="Zzz Otro", np="P9", entidad="NUEVA"),
    ]
    fila = _by_key(_depurar(main=main, fase2=fase2, vercel=vercel, stickers=_ST999))["1053812345"]
    assert (fila["entidad"], fila["np"]) == ("NUEVA", "P7")


def test_w1r2_a_fresh_profile_takes_np_and_the_flag_from_its_first_match():
    perfil = _perfil("1", "ana")
    dep._aplicar_vercel(perfil, _entrada(cedula_key="1", np="  P2  ", entidad="  DAGMA "), True)
    assert (perfil.en_vercel, perfil.np_vercel, perfil.entidad_vercel) == (True, "P2", "DAGMA")


def test_w1r2_a_name_only_match_sets_the_flag_and_np_but_never_the_entidad():
    perfil = _perfil("1", "ana")
    dep._aplicar_vercel(perfil, _entrada(cedula_key="9", np="P2", entidad="DAGMA"), False)
    assert (perfil.en_vercel, perfil.np_vercel, perfil.entidad_vercel) == (True, "P2", "")


def test_w1r2_the_flag_is_never_downgraded():
    perfil = _perfil("1", "ana", en_vercel=True)
    dep._aplicar_vercel(perfil, _entrada(cedula_key="1", np="", entidad=""), True)
    assert perfil.en_vercel is True and perfil.np_vercel == ""


# ── W-4 — D-REMAP: Vercel's código replaces the owner's different one, and it is reported ──


def _reemplazos(revision):
    return _revision_de(revision, "codigo_reemplazado")


def test_w4r2_a_vercel_code_replaces_the_owners_different_one_and_reports_it():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="052")}
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "041"}  # Vercel is the golden rule: behavior kept
    assert revision == ({
        "motivo": "codigo_reemplazado", "identidad_key": "1111111",
        "codigo_anterior": "052", "codigo_nuevo": "041",
    },)


def test_w4r2_the_replacement_is_visible_end_to_end():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="052")}
    resultado = _depurar(roster=roster, vercel=[_v("1111111", "041", "ana uno")])
    assert _by_key(resultado)["1111111"]["codigo"] == "041"
    assert [r["motivo"] for r in resultado.revision_manual] == ["codigo_reemplazado"]


def test_w4r2_the_same_code_is_not_a_replacement():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="041")}
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "041"} and revision == ()


@pytest.mark.parametrize("previo", ["", "   "])
def test_w4r2_an_owner_without_a_code_is_not_a_replacement(previo):
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo=previo)}
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert perfiles["1111111"].codigo == "041" and revision == ()


def test_w4r2_zero_padded_codes_are_different_codes_and_report_the_replacement():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="21")}
    perfiles, revision = _remap(roster, [_v("1111111", "021", "ana uno")])
    assert perfiles["1111111"].codigo == "021"
    assert [(r["codigo_anterior"], r["codigo_nuevo"]) for r in _reemplazos(revision)] == [("21", "021")]


def test_w4r2_a_padded_previous_code_is_reported_trimmed():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo=" 052 ")}
    _, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert [(r["codigo_anterior"], r["codigo_nuevo"]) for r in _reemplazos(revision)] == [("052", "041")]


def test_w4r2_a_conflict_reports_the_conflict_not_a_replacement():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="052")}
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno"), _v("1111111", "063", "ana uno")])
    assert perfiles["1111111"].codigo == ""
    assert not _reemplazos(revision) and _revision_de(revision, "remap_conflicto")


def test_w4r2_a_swap_and_a_rotation_are_notebook_remaps_and_report_nothing():
    # every code HAS a current holder: the notebook remaps them too, so there is no divergence
    for orden in itertools.permutations([("1111111", "Ana Uno", "041"), ("2222222", "Beto Dos", "052")]):
        roster = {c: _roster_entry(c, n, codigo=k) for c, n, k in orden}
        perfiles, revision = _remap(roster, [_v("1111111", "052", "ana uno"), _v("2222222", "041", "beto dos")])
        assert _codigos(perfiles) == {"1111111": "052", "2222222": "041"}
        assert revision == ()


def test_w4r2_a_code_held_by_someone_else_is_a_notebook_remap_and_reports_nothing():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo="052"),
        "2222222": _roster_entry("2222222", "Beto Dos", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": ""} and revision == ()


def test_w4r2_the_report_is_order_independent():
    esperado = None
    filas = [("1111111", "Ana Uno", "052"), ("2222222", "Beto Dos", "060"), ("3333333", "Carla Tres", "")]
    for orden in itertools.permutations(filas):
        roster = {c: _roster_entry(c, n, codigo=k) for c, n, k in orden}
        _, revision = _remap(roster, [_v("1111111", "041", "ana uno"), _v("2222222", "042", "beto dos")])
        esperado = esperado or revision
        assert revision == esperado
    assert [(r["identidad_key"], r["codigo_anterior"], r["codigo_nuevo"]) for r in esperado] == [
        ("1111111", "052", "041"), ("2222222", "060", "042"),
    ]


def test_w4r2_the_old_code_of_the_owner_cleared_as_a_wrong_holder_is_still_reported():
    # Ana holds 052, which Vercel gives to Beto; Vercel gives Ana 041
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo="052"),
        "2222222": _roster_entry("2222222", "Beto Dos"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana uno"), _v("2222222", "052", "beto dos")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": "052"}
    assert [(r["identidad_key"], r["codigo_anterior"], r["codigo_nuevo"]) for r in _reemplazos(revision)] == [
        ("1111111", "052", "041"),
    ]


def test_w4r2_a_same_person_holder_of_the_vercel_code_is_left_alone_without_an_item():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="041")}
    _, revision = _remap(roster, [_v("9999999", "041", "ana uno")])
    assert revision == ()


def test_w4r2_the_item_is_a_fresh_dict_with_exactly_four_keys():
    roster = {"1111111": _roster_entry("1111111", "Ana Uno", codigo="052")}
    _, revision = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert list(revision[0]) == ["motivo", "identidad_key", "codigo_anterior", "codigo_nuevo"]
    revision[0]["codigo_nuevo"] = "tampered"
    _, otra = _remap(roster, [_v("1111111", "041", "ana uno")])
    assert otra[0]["codigo_nuevo"] == "041"


def test_w4r2_many_replacements_stay_fast_and_canonically_ordered():
    roster = {f"{10_000_000 + i}": _roster_entry(f"{10_000_000 + i}", f"Persona Numero {i}", codigo=f"9{i:03d}")
              for i in range(500)}
    vercel = [_v(f"{10_000_000 + i}", f"{i:03d}", f"persona numero {i}") for i in range(500)]
    t0 = time.perf_counter()
    resultado = _depurar(roster=roster, vercel=vercel)
    assert time.perf_counter() - t0 < 3.0
    claves = [r["identidad_key"] for r in resultado.revision_manual if r["motivo"] == "codigo_reemplazado"]
    assert len(claves) == 500 and claves == sorted(claves)


# ══════════════════════════════════════════════════════════════════════════
# Judgment-day round 3, slice 08 — INVARIANTS instead of case-by-case patches
# (INV-1..INV-6 are property-tested in test_inspectores_depuracion_properties.py;
# the reviewer's repros stay here as deterministic, named tests)
# ══════════════════════════════════════════════════════════════════════════


def _r3_c1_inputs():
    roster = {"a": _roster_entry("1234567", "Maria Fernanda Lopez", codigo="021")}
    main = [_main("999", "maria fernanda lopez", codigo="021")]
    vercel = [_v("1234567", "021", "maria fernanda lopez"), _v("1234567", "052", "pedro brigada prueba")]
    return roster, main, vercel


def test_r3_c1_a_keeper_emptied_by_its_own_conflict_does_not_get_its_twin_cleared():
    # the twin "999" is the same person as the cédula owner "1234567", but the owner is
    # registered twice in Vercel (021 and 052) -> remap_conflicto empties the owner. Clearing
    # the twin as well left NOBODY holding 021 while the review item claimed 999 "lost it to
    # the keeper". The twin keeps it, and the conflict item reports the código.
    roster, main, vercel = _r3_c1_inputs()
    perfiles, revision = _remap(roster, vercel, main=main)
    assert _codigos(perfiles) == {"1234567": "", "999": "021"}
    assert not _mismos(revision)
    assert {(r["codigo"], r["identidad_key"]) for r in _revision_de(revision, "remap_conflicto")} == {
        ("021", "1234567"), ("052", "1234567"),
    }


def test_r3_c1_end_to_end_the_codigo_survives_on_the_unified_row_and_is_reported():
    roster, main, vercel = _r3_c1_inputs()
    resultado = _depurar(roster=roster, main=main, vercel=vercel)
    assert [(f["identidad_key"], f["codigo"]) for f in resultado.inspectores] == [("999", "021")]
    assert not _mismos(resultado.revision_manual)
    conflictos = _revision_de(resultado.revision_manual, "remap_conflicto")
    assert {c["codigo"] for c in conflictos} == {"021", "052"}
    assert {c["identidad_key"] for c in conflictos} == {"999"}  # the final row, not the absorbed 1234567


def test_r3_c1_the_others_are_cleared_only_when_the_keeper_surely_ends_up_holding_it():
    # the keeper "1111111" is also the Vercel owner of ANOTHER código (052): it ends holding
    # 052, so clearing "2222222" of 041 would leave 041 with nobody.
    roster = {
        "1111111": _roster_entry("1111111", "Ana Maria Gomez", codigo="041"),
        "2222222": _roster_entry("2222222", "Ana Maria Gomes", codigo="041"),
    }
    vercel = [_v("9999999", "041", "ana maria gomez"), _v("1111111", "052", "otra persona")]
    perfiles, revision = _remap(roster, vercel)
    assert _codigos(perfiles) == {"1111111": "052", "2222222": "041"}
    assert not _mismos(revision)


def test_r3_c1_the_normal_clearing_is_unchanged_when_the_keeper_is_not_contested():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Maria Gomez", codigo="041"),
        "2222222": _roster_entry("2222222", "Ana Maria Gomes", codigo="041"),
    }
    perfiles, revision = _remap(roster, [_v("1111111", "041", "ana maria gomez")])
    assert _codigos(perfiles) == {"1111111": "041", "2222222": ""}
    assert [(r["identidad_key"], r["identidad_key_conservado"]) for r in _mismos(revision)] == [("2222222", "1111111")]


def _r3_c2_roster():
    return {
        "a": _roster_entry("999", "Juan Perez Gomez", codigo="099", creado_en="2024-01-01"),
        "b": _roster_entry("0012345", "Juan Perez Gomez", codigo="052", creado_en="2023-01-01"),
        "c": _roster_entry("166000", "Juan Perez Gomes", codigo="099", creado_en="2024-01-01"),
    }


def test_r3_c2_a_keeper_absorbed_by_the_unification_is_reported_by_its_survivor_key():
    resultado = _depurar(roster=_r3_c2_roster(), vercel=[_v("1053812345", "099", "juan perez gomez")])
    filas = _by_key(resultado)
    assert "999" not in filas and filas["0012345"]["codigo"] == "052"
    (mismos,) = _mismos(resultado.revision_manual)
    assert mismos["identidad_key_conservado"] == "0012345"  # the keeper 999 was absorbed by it
    assert mismos["identidad_key"] == "166000"


def test_r3_c2_a_codigo_lost_in_the_unification_is_reported_never_silent():
    resultado = _depurar(roster=_r3_c2_roster(), vercel=[_v("1053812345", "099", "juan perez gomez")])
    assert _revision_de(resultado.revision_manual, "codigo_perdido_unificacion") == [{
        "motivo": "codigo_perdido_unificacion", "codigo": "099",
        "identidad_key": "0012345", "identidad_key_absorbido": "999",
    }]
    assert "099" not in {f["codigo"] for f in resultado.inspectores}


def test_r3_c2_a_survivor_without_a_codigo_inherits_the_absorbed_one_and_nothing_is_reported():
    roster = _r3_c2_roster()
    roster["b"] = _roster_entry("0012345", "Juan Perez Gomez", creado_en="2023-01-01")
    # a sticker makes "0012345" the survivor (has_sticker outranks has_codigo); it has no
    # código, so the first-non-empty backfill hands it the absorbed keeper's 099
    resultado = _depurar(roster=roster, vercel=[_v("1053812345", "099", "juan perez gomez")],
                         stickers=[_sticker("0012345", fecha_creacion="2026-09-10T00:00:00Z")])
    assert _by_key(resultado)["0012345"]["codigo"] == "099"
    assert not _revision_de(resultado.revision_manual, "codigo_perdido_unificacion")


def test_r3_codigo_perdido_unificacion_covers_a_plain_name_twin_with_a_different_codigo():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo="041", creado_en="2020-01-01"),
        "2222222": _roster_entry("2222222", "Ana Uno", codigo="052", creado_en="2021-01-01"),
    }
    resultado = _depurar(roster=roster)
    assert _revision_de(resultado.revision_manual, "codigo_perdido_unificacion") == [{
        "motivo": "codigo_perdido_unificacion", "codigo": "052",
        "identidad_key": "1111111", "identidad_key_absorbido": "2222222",
    }]


def test_r3_codigo_perdido_unificacion_is_silent_when_both_twins_hold_the_same_codigo_or_none():
    for a, b in (("041", "041"), ("", ""), ("041", ""), ("", "041")):
        roster = {
            "1111111": _roster_entry("1111111", "Ana Uno", codigo=a),
            "2222222": _roster_entry("2222222", "Ana Uno", codigo=b),
        }
        assert not _revision_de(_depurar(roster=roster).revision_manual, "codigo_perdido_unificacion"), (a, b)


def test_r3_three_way_unification_reports_every_lost_codigo():
    roster = {
        "1111111": _roster_entry("1111111", "Ana Uno", codigo="041", creado_en="2020-01-01"),
        "2222222": _roster_entry("2222222", "Ana Uno", codigo="052", creado_en="2021-01-01"),
        "3333333": _roster_entry("3333333", "Ana Uno", codigo="099", creado_en="2022-01-01"),
    }
    perdidos = _revision_de(_depurar(roster=roster).revision_manual, "codigo_perdido_unificacion")
    assert [(p["codigo"], p["identidad_key_absorbido"]) for p in perdidos] == [("052", "2222222"), ("099", "3333333")]


def test_r3_resolver_clave_final_follows_the_rename_then_the_absorption_and_never_loops():
    assert dep._mapa_claves_finales({}, ()) == {}
    renombrado = _perfil("NUEVA", "x", cedula_original="VIEJA")
    fusiones = (
        {"survivor": "S", "perdedor": "NUEVA", "nombre_norm": "x"},
        {"survivor": "S", "perdedor": "P", "nombre_norm": "x"},
    )
    mapa = dep._mapa_claves_finales({"NUEVA": renombrado}, fusiones)
    assert dep._resolver_clave_final("VIEJA", mapa) == "S"   # renamed by D18, then absorbed
    assert dep._resolver_clave_final("NUEVA", mapa) == "S"
    assert dep._resolver_clave_final("P", mapa) == "S"
    assert dep._resolver_clave_final("S", mapa) == "S" and dep._resolver_clave_final("otra", mapa) == "otra"


def test_r3_a_key_absorbed_into_a_collapsed_survivor_resolves_to_a_grupo_externos_member():
    # seed 9 of the property corpus, reduced: the survivor "100" (an old sticker outranks the
    # loser) is collapsed (suspicious cédula, stale sticker, no código), so the absorbed
    # loser "1234567" named by `remap_sin_duenio` must be reported under the survivor, which is
    # listed in the group's detalle, instead of dangling.
    roster = {
        "100": _roster_entry("100", "Maria Perez"),
        "1234567": _roster_entry("1234567", "Maria Perez", codigo="21"),
    }
    resultado = _depurar(roster=roster, stickers=[_sticker("100", fecha_creacion="2026-08-01T00:00:00Z")],
                         vercel=[_v("9999999", "21", "zzz sin relacion")])
    assert resultado.inspectores == ()
    assert [d["identificacion"] for d in resultado.grupo_externos["detalle"]] == ["100"]
    assert [(r["motivo"], r["identidad_key"]) for r in resultado.revision_manual] == [("remap_sin_duenio", "100")]


# ── W-3a — remap_owner_ambiguo lists the holders that keep the código ──


def test_r3_w3a_remap_owner_ambiguo_lists_the_holders_that_keep_the_codigo():
    roster = {
        "100": _roster_entry("100", "Alpha Uno", codigo="041"),
        "200": _roster_entry("200", "Beta Dos", codigo="041"),
        "300": _roster_entry("300", "Gamma Tres"),
        "400": _roster_entry("400", "Delta Cuatro"),
    }
    fase2 = [_f2("500", "gamma tres"), _f2("500", "delta cuatro")]
    resultado = _depurar(roster=roster, fase2=fase2, vercel=[_v("500", "041", "zeta omega")])
    (item,) = _revision_de(resultado.revision_manual, "remap_owner_ambiguo")
    assert item["identidad_keys"] == ["300", "400"] and item["identidad_keys_titulares"] == ["100", "200"]
    assert [f["identidad_key"] for f in resultado.inspectores if f["codigo"] == "041"] == ["100", "200"]  # no evidence to clear


def test_r3_w3a_the_holders_list_is_empty_when_nobody_holds_the_codigo():
    perfiles = {k: _perfil(k, f"persona {k}", cedula_fase2="555") for k in ("1", "2")}
    _, revision = dep.remapear_codigos(perfiles, _bundle(vercel=[_v("555", "041", "zzz")]))
    assert revision[0]["identidad_keys_titulares"] == []


# ── W-3b — a código held by 2+ rows that Vercel never mentions ──


def test_r3_w3b_two_rows_holding_a_codigo_absent_from_vercel_are_reported_not_cleared():
    roster = {
        "100": _roster_entry("100", "Alpha Uno", codigo="041"),
        "200": _roster_entry("200", "Beta Dos", codigo="041"),
    }
    resultado = _depurar(roster=roster)
    assert [f["codigo"] for f in resultado.inspectores] == ["041", "041"]  # D13: no Vercel evidence, no clearing
    assert list(resultado.revision_manual) == [{
        "motivo": "codigo_duplicado_local", "codigo": "041", "identidad_keys": ["100", "200"],
    }]


def test_r3_w3b_it_is_not_repeated_when_another_item_already_names_the_codigo():
    roster = {
        "100": _roster_entry("100", "Alpha Uno", codigo="041"),
        "200": _roster_entry("200", "Beta Dos", codigo="041"),
    }
    resultado = _depurar(roster=roster, vercel=[_v("1", "041", "x"), _v("2", "041", "y")])  # codigo_vercel_duplicado
    assert [r["motivo"] for r in resultado.revision_manual] == ["codigo_vercel_duplicado"]


def test_r3_w3b_zero_padded_blank_and_single_holders_are_never_reported():
    roster = {
        "100": _roster_entry("100", "Alpha Uno", codigo="021"),
        "200": _roster_entry("200", "Beta Dos", codigo="21"),     # a different código
        "300": _roster_entry("300", "Gamma Tres", codigo="   "),
        "400": _roster_entry("400", "Delta Cuatro", codigo=""),
    }
    assert _depurar(roster=roster).revision_manual == ()


def test_r3_w3b_name_twins_unify_first_so_they_are_one_holder():
    roster = {
        "100": _roster_entry("100", "Alpha Uno", codigo="041"),
        "200": _roster_entry("200", "Alpha Uno", codigo="041"),
    }
    resultado = _depurar(roster=roster)
    assert len(resultado.inspectores) == 1 and resultado.revision_manual == ()


def test_r3_w3b_the_report_is_canonical_whatever_the_roster_order():
    filas = [("100", "Alpha Uno"), ("200", "Beta Dos"), ("300", "Gamma Tres")]
    vistos = set()
    for orden in itertools.permutations(filas):
        roster = {c: _roster_entry(c, n, codigo="041") for c, n in orden}
        vistos.add(repr(_depurar(roster=roster).revision_manual))
    assert len(vistos) == 1 and "'identidad_keys': ['100', '200', '300']" in vistos.pop()


# ── W-4 — a código pre-listed in `referencia.codigos_duplicados` ──


def test_r3_w4_a_prelisted_codigo_held_by_two_profiles_is_reported_with_its_holders():
    roster = {
        "100": _roster_entry("100", "Alpha Uno", codigo="041"),
        "200": _roster_entry("200", "Beta Dos", codigo="041"),
    }
    ref = _bundle(codigos_duplicados=["041"])
    resultado = dep.depurar(stickers=[], roster_by_cedula=roster, nombres_survey=[], referencia=ref, hoy=HOY)
    assert list(resultado.revision_manual) == [{
        "motivo": "codigo_vercel_duplicado", "codigo": "041", "identidad_keys_titulares": ["100", "200"],
    }]
    assert [f["codigo"] for f in resultado.inspectores] == ["041", "041"]  # excluded from remap: nobody cleared


def test_r3_w4_a_prelisted_codigo_held_by_one_profile_reports_nothing():
    roster = {"100": _roster_entry("100", "Alpha Uno", codigo="041")}
    ref = _bundle(codigos_duplicados=["041", " ", "099"])
    assert dep.depurar(stickers=[], roster_by_cedula=roster, nombres_survey=[], referencia=ref, hoy=HOY).revision_manual == ()


def test_r3_w4_a_codigo_both_prelisted_and_repeated_in_vercel_is_reported_once():
    roster = {
        "100": _roster_entry("100", "Alpha Uno", codigo="041"),
        "200": _roster_entry("200", "Beta Dos", codigo="041"),
    }
    ref = _bundle(codigos_duplicados=["041"], vercel=[_v("1", "041", "x"), _v("2", "041", "y")])
    resultado = dep.depurar(stickers=[], roster_by_cedula=roster, nombres_survey=[], referencia=ref, hoy=HOY)
    assert [r["motivo"] for r in resultado.revision_manual] == ["codigo_vercel_duplicado"]
    assert resultado.revision_manual[0]["identidad_keys_titulares"] == ["100", "200"]


def test_r3_w4_a_repeated_vercel_codigo_nobody_holds_keeps_the_original_item_shape():
    _, revision = _remap({"100": _roster_entry("100", "Alpha Uno")}, [_v("1", "097", "x"), _v("2", "097", "y")])
    assert revision == ({"motivo": "codigo_vercel_duplicado", "codigo": "097"},)


# ── S-5 — D-CODIGOTRIM ──


def test_r3_s5_a_whitespace_only_roster_codigo_is_no_codigo_and_never_activo():
    resultado = _depurar(roster={"100": _roster_entry("100", "Alpha Uno", codigo="   ")},
                         stickers=[_sticker("100", fecha_creacion="2026-09-10T00:00:00Z")])
    (fila,) = resultado.inspectores
    assert fila["codigo"] == "" and fila["estado_sugerido"] == "revisar"


def test_r3_s5_a_whitespace_only_vercel_codigo_is_ignored_and_clears_nobody():
    roster = {"100": _roster_entry("100", "Alpha Uno", codigo="041")}
    perfiles, revision = _remap(roster, [_v("100", "   ", "alpha uno"), _v("999", "\t", "otro")])
    assert _codigos(perfiles) == {"100": "041"} and revision == ()


def test_r3_s5_a_padded_vercel_codigo_is_trimmed_when_assigned():
    roster = {"100": _roster_entry("100", "Alpha Uno")}
    resultado = _depurar(roster=roster, vercel=[_v("100", "  041 ", "alpha uno")])
    assert _by_key(resultado)["100"]["codigo"] == "041"


def test_r3_no_pii_reaches_the_logs_for_the_new_items(caplog):
    roster = {
        "1111111111": _roster_entry("1111111111", "Zoraida Quintero Gomez", codigo="041"),
        "2222222222": _roster_entry("2222222222", "Ximena Lopez Diaz", codigo="041"),
    }
    with caplog.at_level(logging.DEBUG):
        _depurar(roster=roster)
        _depurar(roster=_r3_c2_roster(), vercel=[_v("1053812345", "099", "juan perez gomez")])
    texto = caplog.text.lower()
    assert "zoraida" not in texto and "ximena" not in texto and "juan perez" not in texto
    assert "1111111111" not in texto and "0012345" not in texto


# ── D-ENFASIS: free-text `enfasis` from the registry (`main`) ───────────────


def test_enfasis_main_only_profile_takes_the_main_entry_text():
    main = [_entrada(cedula_key="12345678", nombre_norm="ana gomez", enfasis="Especialización en Estructuras")]
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert perfiles["12345678"].enfasis == "Especialización en Estructuras"


def test_enfasis_defaults_to_blank_for_a_main_entry_without_it():
    perfiles, _ = dep.fusionar_identidad([], {}, _bundle(main=[_entrada(cedula_key="12345678", nombre_norm="ana")]))
    assert perfiles["12345678"].enfasis == ""


def test_enfasis_roster_and_main_intersection_backfills_when_roster_has_none():
    roster = {"12345678": _roster_entry("12345678", "Juan Perez")}
    main = [_entrada(cedula_key="12345678", nombre_norm="juan perez", enfasis="Geotecnia")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(main=main))
    assert len(perfiles) == 1
    assert perfiles["12345678"].enfasis == "Geotecnia"
    assert perfiles["12345678"].firestore_backed is True


def test_enfasis_roster_and_main_intersection_a_blank_main_value_backfills_nothing():
    roster = {"12345678": _roster_entry("12345678", "Juan Perez")}
    main = [_entrada(cedula_key="12345678", nombre_norm="juan perez", enfasis="")]
    perfiles, _ = dep.fusionar_identidad([], roster, _bundle(main=main))
    assert perfiles["12345678"].enfasis == ""


def test_enfasis_duplicate_main_cedula_first_row_wins():
    main = [
        _entrada(cedula_key="12345678", nombre_norm="ana gomez", enfasis="Estructuras"),
        _entrada(cedula_key="12345678", nombre_norm="ana gomez b", enfasis="Geotecnia"),
    ]
    perfiles, revision = dep.fusionar_identidad([], {}, _bundle(main=main))
    assert perfiles["12345678"].enfasis == "Estructuras"
    assert [r["motivo"] for r in revision] == ["cedula_duplicada_main"]


def test_enfasis_unificar_survivor_without_it_takes_the_first_non_empty_loser_text():
    survivor = _perfil("S", "juan perez", ultimo_sticker=date(2026, 8, 1))
    l1 = _perfil("L1", "juan perez", enfasis="")
    l2 = _perfil("L2", "juan perez", enfasis="Geotecnia")
    l3 = _perfil("L3", "juan perez", enfasis="Estructuras")
    resultado, _ = dep.unificar_duplicados({"L3": l3, "S": survivor, "L2": l2, "L1": l1})
    assert resultado["S"].enfasis == "Geotecnia"  # ascending identidad_key: L2 before L3


def test_enfasis_unificar_survivor_own_text_is_never_overwritten():
    survivor = _perfil("S", "juan perez", ultimo_sticker=date(2026, 8, 1), enfasis="Estructuras")
    loser = _perfil("L1", "juan perez", enfasis="Geotecnia")
    resultado, _ = dep.unificar_duplicados({"S": survivor, "L1": loser})
    assert resultado["S"].enfasis == "Estructuras"


def test_enfasis_unificar_is_deterministic_under_every_permutation():
    import itertools

    def perfiles():
        return [
            _perfil("S", "juan perez", ultimo_sticker=date(2026, 8, 1)),
            _perfil("L1", "juan perez", enfasis=""),
            _perfil("L2", "juan perez", enfasis="Geotecnia"),
            _perfil("L3", "juan perez", enfasis="Estructuras"),
        ]

    resultados = set()
    for orden in itertools.permutations(range(4)):
        base = perfiles()
        resultado, _ = dep.unificar_duplicados({base[i].identidad_key: base[i] for i in orden})
        resultados.add(resultado["S"].enfasis)
    assert resultados == {"Geotecnia"}


def test_enfasis_is_emitted_in_the_depuracion_row_and_blank_by_default():
    main = [
        _entrada(cedula_key="1111111", nombre_norm="ana gomez", enfasis="Especialización en estructuras"),
        _entrada(cedula_key="2222222", nombre_norm="beto ruiz"),
    ]
    filas = _by_key(_depurar(main=main))
    assert filas["1111111"]["enfasis"] == "Especialización en estructuras"
    assert filas["2222222"]["enfasis"] == ""


def test_enfasis_survives_the_whole_pipeline_for_a_roster_person_and_a_unified_duplicate():
    roster = {"1111111": _roster_entry("1111111", "Ana Gomez")}
    main = [
        _entrada(cedula_key="1111111", nombre_norm="ana gomez", enfasis="Geotecnia"),
        _entrada(cedula_key="3333333", nombre_norm="carlos ruiz", enfasis=""),
        _entrada(cedula_key="4444444", nombre_norm="carlos ruiz", enfasis="Construccion"),
    ]
    filas = _by_key(_depurar(roster=roster, main=main))
    assert filas["1111111"]["enfasis"] == "Geotecnia"
    survivor = next(f for k, f in filas.items() if k in ("3333333", "4444444"))
    assert survivor["enfasis"] == "Construccion"  # the empty survivor took the loser's text


@pytest.mark.parametrize(
    "valor", ["<script>alert(1)</script>", "\"quoted\" & <b>", "Estructuras " * 200], ids=["script", "quoted", "long"],
)
def test_enfasis_hostile_and_very_long_text_pass_through_the_engine_verbatim(valor):
    main = [_entrada(cedula_key="1111111", nombre_norm="ana gomez", enfasis=valor)]
    assert _by_key(_depurar(main=main))["1111111"]["enfasis"] == valor.strip()


@pytest.mark.parametrize("valor", [None, float("nan"), float("inf"), "", "   "])
def test_enfasis_blank_entry_values_never_leak_nan_or_none_text(valor):
    main = [_entrada(cedula_key="1111111", nombre_norm="ana gomez", enfasis=valor)]
    assert _by_key(_depurar(main=main))["1111111"]["enfasis"] == ""
