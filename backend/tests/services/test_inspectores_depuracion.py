"""RED-first tests for `app.services.inspectores_depuracion` — the 6-seam
depuración engine (design.md Interfaces/Contracts; tasks.md Phase 2).

Every seam is a pure function: plain dict/dataclass fixtures, no mocks, no
I/O. See the module docstring for the exact `stickers`/`roster_by_cedula`
dict contracts this module defines.
"""
from __future__ import annotations

import inspect
from datetime import date

from app.services import inspectores_depuracion as dep
from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle


# ── Fixture builders ─────────────────────────────────────────────────────


def _entrada(cedula_key="", nombre_norm="", np="", entidad="", codigo="", pasos=(), no_persona=False):
    return EntradaReferencia(
        cedula_key=cedula_key, nombre_norm=nombre_norm, np=np, entidad=entidad,
        codigo=codigo, pasos=tuple(pasos), no_persona=no_persona,
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
    perfiles = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert set(perfiles) == {"1234567"}
    assert perfiles["1234567"].nombre_norm == "juan perez"


def test_fusionar_identidad_blank_identificacion_skipped():
    roster = {"000": _roster_entry("", "Sin Identidad")}
    perfiles = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert perfiles == {}


def test_fusionar_identidad_cedula_ties_same_name_unified():
    roster = {
        "111": _roster_entry("111", "Juan Perez"),
        "222": _roster_entry("222", "Juan Perez"),
    }
    perfiles = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert len(perfiles) == 1
    survivor = next(iter(perfiles.values()))
    assert set(survivor.cedulas_unificadas) | {survivor.identificacion} == {"111", "222"}


def test_fusionar_identidad_cedula_ties_different_name_not_unified():
    roster = {
        "111": _roster_entry("111", "Juan Perez"),
        "222": _roster_entry("222", "Maria Lopez"),
    }
    perfiles = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert len(perfiles) == 2


def test_fusionar_identidad_persona_sin_ninguna_fuente_de_identidad():
    # roster vacío, stickers vacíos, referencia vacía -> no explota, dict vacío
    perfiles = dep.fusionar_identidad([], {}, EMPTY_REF)
    assert perfiles == {}


def test_fusionar_identidad_malformed_roster_entry_skipped():
    roster = {"1": "not-a-dict", "2": _roster_entry("222", "Juan Perez")}
    perfiles = dep.fusionar_identidad([], roster, EMPTY_REF)
    assert set(perfiles) == {"222"}


def test_fusionar_identidad_sticker_aggregation():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    stickers = [
        _sticker("1234567", fecha_creacion="2026-08-25T00:00:00Z"),
        _sticker("1234567", fecha_creacion="2026-08-30T00:00:00Z"),
    ]
    perfiles = dep.fusionar_identidad(stickers, roster, EMPTY_REF)
    perfil = perfiles["1234567"]
    assert perfil.n_stickers == 2
    assert perfil.ultimo_sticker == date(2026, 8, 30)
    assert perfil.tiene_sticker_valido is True


def test_fusionar_identidad_sticker_unattributable_skipped_not_raised():
    stickers = [_sticker("nadie-existe", fecha_creacion="2026-08-25T00:00:00Z")]
    perfiles = dep.fusionar_identidad(stickers, {}, EMPTY_REF)
    assert perfiles == {}


def test_fusionar_identidad_malformed_sticker_skipped():
    roster = {"1": _roster_entry("1", "Juan Perez")}
    stickers = ["not-a-dict", {"origen": "sistema"}]  # no "inspector" key
    perfiles = dep.fusionar_identidad(stickers, roster, EMPTY_REF)
    assert perfiles["1"].n_stickers == 0


def test_fusionar_identidad_referencia_overlay_cedula_match():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(
        vercel=[_entrada(cedula_key="1234567", nombre_norm="juan perez", np="P3", entidad="DAGRD")],
        fase2=[_entrada(cedula_key="1234567", nombre_norm="juan perez", np="P3")],
        main=[_entrada(cedula_key="1234567", nombre_norm="juan perez", np="P2", no_persona=False)],
    )
    perfiles = dep.fusionar_identidad([], roster, ref)
    p = perfiles["1234567"]
    assert p.en_vercel is True and p.np_vercel == "P3"
    assert p.en_fase2 is True and p.np_fase2 == "P3"
    assert p.rango_main == "P2"


def test_fusionar_identidad_referencia_overlay_nombre_fallback():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(vercel=[_entrada(cedula_key="9999999", nombre_norm="juan perez", np="P4")])
    perfiles = dep.fusionar_identidad([], roster, ref)
    assert perfiles["1234567"].en_vercel is True
    assert perfiles["1234567"].np_vercel == "P4"


def test_fusionar_identidad_no_persona_ref_from_main_folds_into_heuristic():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez", correo="juan@example.com")}
    ref = _bundle(main=[_entrada(cedula_key="1234567", nombre_norm="juan perez", no_persona=True)])
    perfiles = dep.fusionar_identidad([], roster, ref)
    assert perfiles["1234567"].es_cuenta_no_persona is True


# ── Stage 2: remapear_codigos (tasks 2.4, 2.5, 2.6) ─────────────────────────


def test_remapear_codigos_cedula_match_assigns_current_titular():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(vercel=[_entrada(cedula_key="1234567", nombre_norm="juan perez", codigo="041")])
    perfiles = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1234567"].codigo == "041"
    assert revision == ()


def test_remapear_codigos_vercel_duplicate_excluded():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(vercel=[
        _entrada(cedula_key="1111111", nombre_norm="ana gomez", codigo="097"),
        _entrada(cedula_key="2222222", nombre_norm="ana gomez dos", codigo="097"),
    ])
    perfiles = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1234567"].codigo == ""
    assert {"motivo": "codigo_vercel_duplicado", "codigo": "097"} in revision


def test_remapear_codigos_referencia_codigos_duplicados_also_excluded():
    roster = {}
    ref = _bundle(
        vercel=[_entrada(cedula_key="1111111", nombre_norm="ana gomez", codigo="127")],
        codigos_duplicados=["127"],
    )
    perfiles = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    # already-known duplicate is not RE-reported (only newly-discovered ones are)
    assert revision == ()


def test_remapear_codigos_fuzzy_never_autoemerges():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez Gomez")}
    ref = _bundle(vercel=[_entrada(cedula_key="9999999", nombre_norm="juan perez gomes", codigo="055")])
    perfiles = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1234567"].codigo == ""  # never auto-assigned
    assert any(r["motivo"] == "codigo_remap_candidato" and r["codigo"] == "055" for r in revision)


def test_remapear_codigos_no_match_at_all_skipped_not_raised():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    ref = _bundle(vercel=[_entrada(cedula_key="9999999", nombre_norm="alguien completamente distinto", codigo="200")])
    perfiles = dep.fusionar_identidad([], roster, ref)
    perfiles, revision = dep.remapear_codigos(perfiles, ref)
    assert perfiles["1234567"].codigo == ""
    assert revision == ()  # "código que ya no existe" -> nadie lo reclama, sin crash


def test_remapear_codigos_empty_referencia_no_crash():
    roster = {"1234567": _roster_entry("1234567", "Juan Perez")}
    perfiles = dep.fusionar_identidad([], roster, EMPTY_REF)
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


def test_unificar_duplicados_backfill_first_non_empty_wins_not_summed():
    perdedor = _perfil("A", "juan perez", correo="juan@old.com", n_stickers=5)
    survivor = _perfil("B", "juan perez", ultimo_sticker=date(2026, 8, 1), n_stickers=3)
    perfiles = {"A": perdedor, "B": survivor}
    resultado, _ = dep.unificar_duplicados(perfiles)
    ganador = resultado["B"]
    assert ganador.correo == "juan@old.com"  # backfilled: survivor's own was blank
    assert ganador.n_stickers == 3  # NOT summed (5+3=8) — notebook parity


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
    p = _perfil("A", "x", tiene_sticker_valido=True)
    assert dep.estado_sugerido(p) == "candidato_desactivacion"


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
