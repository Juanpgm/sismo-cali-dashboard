"""Property tests for `inspectores_depuracion.depurar` (judgment-day round 3).

The `remap_mismos_titulares` keeper logic kept producing defects when patched case by
case, so the contract is stated as INVARIANTS and checked over a deterministic
randomized corpus (no new dependency: `random.Random(seed)`):

    INV-1  no silent multi-holder: every non-empty código held by 2+ rows has a review
           item whose `codigo` equals it (`codigo_vercel_duplicado`, `remap_owner_ambiguo`,
           `remap_conflicto`, `remap_mismos_titulares`, `codigo_duplicado_local`).
    INV-2  no dangling keys: every profile key mentioned by a review item resolves to a
           final row key, to an entry of some row's `cedulas_unificadas`, or to a member of
           `grupo_externos.detalle`.
    INV-3  the output is independent of the roster / sticker / survey ORDER.
    INV-4  idempotent: `depurar(x) == depurar(x)`.
    INV-5  a keeper never ends with an empty código because of the pass that cleared the
           other holders (unless a review item says where the código went).
    INV-6  no `activo` row without a código (D7: `activo` is driven by the código alone).
    INV-7  a merge never changes the survivor's own flags (D-SURVFLAGS): every output row's
           `no_persona` and `cedula_sospechosa` equal the flags its OWN profile had before the
           D-P2 unification (a loser's flag or a backfilled correo never flips them).

plus "no exception, no input mutation". A failure message carries the SEED and a minimal
description; rebuild the input with `_generar(random.Random(seed))`.

The corpus is built ONCE per module (seeds x permutations) and shared by the tests.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import random
from datetime import date

import pytest

from app.services import inspectores_depuracion as dep
from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle

HOY = date(2026, 9, 19)
SEMILLAS = 300          # broad universe (`_generar`)
SEMILLAS_FOCALIZADAS = 200  # keeper-heavy universe (`_generar_focalizado`)
PERMUTACIONES = 6

NOMBRES = [
    "ana maria gomez", "ana maria gomes", "juan perez gomez", "juan perez gomes",
    "carlos ruiz", "carlos ruis", "maria fernanda lopez", "", "  ",
    "pedro brigada prueba", "luisa torres diaz",
]
CEDULAS = ["100", "0100", "1099999999", "1234567", "999", "1053812345", "12345", "0012345",
           "", "abc", "166.000", "1234567.0", "31837630"]
CODIGOS = ["041", "21", "021", "052", "", "  ", "099"]
ENTIDADES = ["DAGRD", "BOMBEROS", "", " DAGRD "]
NPS = ["P1", "P3", "7", "", "P7", "P2"]
CORREOS = ["a@x.com", "b@import.local", "", "c@sismo.cali.gov.co", "d@y.com"]
FECHAS = ["2026-08-19T10:00:00Z", "2026-08-21T10:00:00Z", "2026-07-01T00:00:00Z", None, "junk"]
CREADOS = ["", "2024-01-01", "2023-05-05", "junk", "2025-12-31"]
ENFASIS = ["", "Estructuras", "Geotecnia", "", "Especialización en estructuras", " "]


def _generar(rnd: random.Random) -> dict:
    """The reviewer's fresh generator (a small universe so collisions are FREQUENT)."""
    def ent():
        return EntradaReferencia(
            cedula_key=rnd.choice(CEDULAS), nombre_norm=rnd.choice(NOMBRES), np=rnd.choice(NPS),
            entidad=rnd.choice(ENTIDADES), codigo=rnd.choice(CODIGOS), pasos=(),
            no_persona=rnd.random() < 0.1, nombre="", telefono=str(rnd.randrange(10**7)),
            creado_en=rnd.choice(CREADOS), id=(entrada_id := str(rnd.randrange(1000))), correo=rnd.choice(CORREOS),
            tarjeta_profesional=str(rnd.randrange(100)),
            # derived from the id: it adds NO draw, so every existing seed keeps its universe
            enfasis=ENFASIS[int(entrada_id) % len(ENFASIS)],
        )

    roster = {}
    for i in range(rnd.randrange(0, 7)):
        roster[f"doc{i}"] = {
            "identificacion": rnd.choice(CEDULAS),
            "nombre_completo": rnd.choice(NOMBRES),
            "correo": rnd.choice(CORREOS),
            "codigo": rnd.choice(CODIGOS),
            "entidad": rnd.choice(ENTIDADES),
            "tarjeta_profesional": str(rnd.randrange(100)),
            "num_telefono": str(rnd.randrange(10**7)),
            "correo_contacto": rnd.choice(CORREOS),
            "creado_en": rnd.choice(CREADOS),
        }
    vercel = tuple(ent() for _ in range(rnd.randrange(0, 6)))
    fase2 = tuple(ent() for _ in range(rnd.randrange(0, 5)))
    main = tuple(ent() for _ in range(rnd.randrange(0, 7)))
    duplicados = tuple(rnd.sample(CODIGOS, rnd.randrange(0, 2)))
    bundle = ReferenciaBundle(vercel=vercel, fase2=fase2, main=main, generado_en="2026-09-12",
                              activa=True, motivo="", codigos_duplicados=duplicados)
    stickers = [{
        "origen": rnd.choice(["sistema", "firebase", ""]),
        "fecha_creacion": rnd.choice(FECHAS),
        "inspector": {"identificacion": rnd.choice(CEDULAS), "codigo": rnd.choice(CODIGOS),
                      "nombre_completo": rnd.choice(NOMBRES)},
    } for _ in range(rnd.randrange(0, 8))]
    survey = [rnd.choice(NOMBRES) for _ in range(rnd.randrange(0, 4))]
    return dict(stickers=stickers, roster_by_cedula=roster, nombres_survey=survey, referencia=bundle)


def _generar_focalizado(rnd: random.Random) -> dict:
    """A tiny universe built to make the KEEPER paths collide: the same person spelled two
    ways (a same-person pair by fuzzy score), one shared código, and a Vercel owner that is
    sometimes registered twice with two códigos (a `remap_conflicto`) or holds a name-twin
    in `main`. The broad generator hits these only ~1 seed in 100."""
    nombres = ["ana maria gomez", "ana maria gomes", "juan perez gomez", "juan perez gomes"]
    cedulas = ["1111111", "2222222", "3333333", "999", "0012345"]
    codigos = ["041", "052"]

    def ent(cedula=None, nombre=None, codigo=None):
        return EntradaReferencia(
            cedula_key=cedula if cedula is not None else rnd.choice(cedulas),
            nombre_norm=nombre if nombre is not None else rnd.choice(nombres),
            np="P1", entidad="", codigo=codigo if codigo is not None else rnd.choice(codigos),
            pasos=(), no_persona=False, creado_en=rnd.choice(CREADOS), id=str(rnd.randrange(1000)),
        )

    roster = {}
    for i in range(rnd.randrange(1, 4)):
        roster[f"doc{i}"] = {
            "identificacion": rnd.choice(cedulas), "nombre_completo": rnd.choice(nombres),
            "codigo": rnd.choice(codigos + [""]), "creado_en": rnd.choice(CREADOS),
        }
    main = tuple(ent(codigo=rnd.choice(codigos + [""])) for _ in range(rnd.randrange(0, 3)))
    duenio = rnd.choice(cedulas)
    vercel = [ent(cedula=duenio)]
    if rnd.random() < 0.6:  # the same person registered twice with two different códigos
        vercel.append(ent(cedula=duenio, codigo=rnd.choice(codigos)))
    vercel += [ent() for _ in range(rnd.randrange(0, 2))]
    bundle = ReferenciaBundle(vercel=tuple(vercel), fase2=(), main=main, generado_en="2026-09-12",
                              activa=True, motivo="", codigos_duplicados=())
    return dict(stickers=[], roster_by_cedula=roster, nombres_survey=[], referencia=bundle)


def _correr(entrada: dict) -> dep.Depuracion:
    return dep.depurar(hoy=HOY, **entrada)


def _canonico(resultado: dep.Depuracion) -> str:
    return json.dumps({
        "inspectores": list(resultado.inspectores), "grupo": resultado.grupo_externos,
        "alias": resultado.alias_nombres, "revision": list(resultado.revision_manual),
    }, sort_keys=True, default=str)


def _permutar(entrada: dict, rnd: random.Random) -> dict:
    copia = copy.deepcopy(entrada)
    items = list(copia["roster_by_cedula"].items())
    rnd.shuffle(items)
    copia["roster_by_cedula"] = dict(items)
    rnd.shuffle(copia["stickers"])
    rnd.shuffle(copia["nombres_survey"])
    return copia


@dataclasses.dataclass
class Caso:
    semilla: int
    entrada: dict
    resultado: dep.Depuracion | None = None
    error: BaseException | None = None
    repetido: dep.Depuracion | None = None
    permutados: list = dataclasses.field(default_factory=list)
    mutacion: bool = False


@pytest.fixture(scope="module")
def corpus() -> list[Caso]:
    casos = []
    for semilla in range(SEMILLAS + SEMILLAS_FOCALIZADAS):
        focalizado = semilla >= SEMILLAS
        entrada = (_generar_focalizado if focalizado else _generar)(random.Random(semilla))
        caso = Caso(semilla, entrada)
        antes = copy.deepcopy(entrada)
        try:
            caso.resultado = _correr(entrada)
            caso.mutacion = entrada != antes
            caso.repetido = _correr(copy.deepcopy(antes))
            for p in range(PERMUTACIONES):
                caso.permutados.append(_correr(_permutar(antes, random.Random(semilla * 1000 + p + 7))))
        except Exception as exc:  # noqa: BLE001 - reported by `test_no_exception_on_any_seed`
            caso.error = exc
        casos.append(caso)
    return casos


# ── helpers over a result ────────────────────────────────────────────────────

MOTIVOS_INV1 = {
    "codigo_vercel_duplicado", "remap_owner_ambiguo", "remap_conflicto",
    "remap_mismos_titulares", "codigo_duplicado_local",
}


def _norm(clave) -> str:
    """The module's own key normalization (`_cedula_key`, else the trimmed text)."""
    return dep._cedula_key(clave) or dep._txt(clave)


def _claves_de(item: dict) -> list[str]:
    claves = []
    for campo in ("identidad_key", "identidad_key_conservado", "identidad_key_candidato",
                  "identidad_key_existente", "identidad_key_absorbido"):
        if item.get(campo):
            claves.append(item[campo])
    for campo in ("identidad_keys", "identidad_keys_titulares"):
        claves.extend(item.get(campo) or ())
    return claves


def _claves_resolubles(resultado: dep.Depuracion) -> tuple[set[str], set[str]]:
    """`(final keys incl. collapsed members, keys that only resolve through an alias)`."""
    finales = {_norm(f["identidad_key"]) for f in resultado.inspectores}
    if resultado.grupo_externos:
        finales |= {_norm(d["identificacion"]) for d in resultado.grupo_externos["detalle"]}
    alias = {_norm(a) for f in resultado.inspectores for a in f["cedulas_unificadas"]}
    return finales, alias


def _repro(caso: Caso) -> str:
    e = caso.entrada
    return (
        f"seed={caso.semilla}: rebuild with {'_generar_focalizado' if caso.semilla >= SEMILLAS else '_generar'}"
        f"(random.Random({caso.semilla})); roster codes="
        f"{sorted((v['identificacion'], v['codigo']) for v in e['roster_by_cedula'].values())}, vercel="
        f"{[(x.cedula_key, x.codigo) for x in e['referencia'].vercel]}, main="
        f"{[(x.cedula_key, x.codigo) for x in e['referencia'].main]}"
    )


def _fallos(corpus, comprobar) -> list[str]:
    mensajes = []
    for caso in corpus:
        if caso.resultado is None:
            continue
        for detalle in comprobar(caso):
            mensajes.append(f"{detalle} | {_repro(caso)}")
    return mensajes


def _ok(mensajes: list[str]) -> None:
    assert not mensajes, f"{len(mensajes)} violation(s); first 3:\n" + "\n".join(mensajes[:3])


# ── the corpus is real: it exercises the interesting paths ───────────────────


def test_corpus_size_and_variety(corpus):
    assert len(corpus) >= 300 + SEMILLAS_FOCALIZADAS and PERMUTACIONES >= 6
    assert all(len(c.permutados) == PERMUTACIONES for c in corpus if c.error is None)
    motivos = {i["motivo"] for c in corpus if c.resultado for i in c.resultado.revision_manual}
    assert {"codigo_vercel_duplicado", "remap_conflicto", "remap_mismos_titulares"} <= motivos


def test_no_exception_on_any_seed(corpus):
    errores = [f"seed={c.semilla}: {type(c.error).__name__}: {c.error}" for c in corpus if c.error]
    assert not errores, "\n".join(errores[:3])


def test_no_input_is_mutated_on_any_seed(corpus):
    assert [c.semilla for c in corpus if c.mutacion] == []


# ── INV-1 ────────────────────────────────────────────────────────────────────


def test_inv1_a_codigo_held_by_two_rows_always_has_a_review_item(corpus):
    def comprobar(caso):
        cubiertos = {dep._txt(i.get("codigo")) for i in caso.resultado.revision_manual if i["motivo"] in MOTIVOS_INV1}
        titulares: dict[str, list[str]] = {}
        for fila in caso.resultado.inspectores:
            if fila["codigo"]:
                titulares.setdefault(fila["codigo"], []).append(fila["identidad_key"])
        for codigo, claves in titulares.items():
            if len(claves) >= 2 and codigo not in cubiertos:
                yield f"INV-1: codigo {codigo!r} held by {claves} and no review item names it"
    _ok(_fallos(corpus, comprobar))


# ── INV-2 ────────────────────────────────────────────────────────────────────


def test_inv2_every_key_in_a_review_item_resolves(corpus):
    def comprobar(caso):
        finales, alias = _claves_resolubles(caso.resultado)
        for item in caso.resultado.revision_manual:
            for clave in _claves_de(item):
                if _norm(clave) not in finales | alias:
                    yield f"INV-2: {item['motivo']} names {clave!r}, which is not a final row, alias or collapsed member"
    _ok(_fallos(corpus, comprobar))


def test_inv2_strict_pointer_keys_name_a_final_row_or_a_collapsed_member(corpus):
    """Stricter than the contract: a consumer that looks the key up in `inspectores`
    (or in `grupo_externos.detalle`) finds it without going through an alias."""
    def comprobar(caso):
        finales, _ = _claves_resolubles(caso.resultado)
        for item in caso.resultado.revision_manual:
            for campo in ("identidad_key_conservado", "identidad_key_candidato", "identidad_keys",
                          "identidad_keys_titulares", "identidad_key"):
                valores = item.get(campo)
                for clave in ([valores] if isinstance(valores, str) else valores or ()):
                    if _norm(clave) not in finales:
                        yield f"INV-2 strict: {item['motivo']}.{campo}={clave!r} is not a final row key"
    _ok(_fallos(corpus, comprobar))


# ── INV-3 / INV-4 ────────────────────────────────────────────────────────────


def test_inv3_output_is_independent_of_roster_sticker_and_survey_order(corpus):
    def comprobar(caso):
        base = _canonico(caso.resultado)
        for indice, otro in enumerate(caso.permutados):
            if _canonico(otro) != base:
                yield f"INV-3: permutation #{indice} changes the result"
                return
    _ok(_fallos(corpus, comprobar))


def test_inv4_depurar_is_idempotent(corpus):
    def comprobar(caso):
        if _canonico(caso.repetido) != _canonico(caso.resultado):
            yield "INV-4: two runs on equal inputs differ"
    _ok(_fallos(corpus, comprobar))


# ── INV-5 ────────────────────────────────────────────────────────────────────


def test_inv5_a_keeper_never_ends_empty_because_of_the_pass_that_cleared_the_others(corpus):
    def comprobar(caso):
        filas = {_norm(f["identidad_key"]): f for f in caso.resultado.inspectores}
        alias = {_norm(a): _norm(f["identidad_key"]) for f in caso.resultado.inspectores for a in f["cedulas_unificadas"]}
        # where a código may legitimately go instead of the keeper: reported, never silent
        explicados = {
            (i["motivo"], dep._txt(i.get("codigo")) or dep._txt(i.get("codigo_anterior")))
            for i in caso.resultado.revision_manual
            if i["motivo"] in ("codigo_perdido_unificacion", "codigo_reemplazado")
        }
        for item in caso.resultado.revision_manual:
            if item["motivo"] != "remap_mismos_titulares":
                continue
            codigo = item["codigo"]
            clave = _norm(item["identidad_key_conservado"])
            fila = filas.get(clave) or filas.get(alias.get(clave, ""))
            if fila is None:
                yield f"INV-5: keeper {clave!r} of codigo {codigo!r} is not a row"
            elif fila["codigo"] != codigo and not any((m, codigo) in explicados for m in (
                    "codigo_perdido_unificacion", "codigo_reemplazado")):
                yield (f"INV-5: keeper {clave!r} ends with codigo {fila['codigo']!r} but the others were "
                       f"cleared of {codigo!r} and no item says where it went")
    _ok(_fallos(corpus, comprobar))


# ── INV-6 ────────────────────────────────────────────────────────────────────


def test_inv6_no_activo_row_without_a_codigo(corpus):
    def comprobar(caso):
        for fila in caso.resultado.inspectores:
            if fila["estado_sugerido"] == "activo" and (not fila["codigo"] or fila["codigo"] != fila["codigo"].strip()):
                yield f"INV-6: row {fila['identidad_key']!r} is activo with codigo {fila['codigo']!r}"
    _ok(_fallos(corpus, comprobar))


# ── INV-7 ────────────────────────────────────────────────────────────────────


def _banderas_previas(entrada: dict) -> dict[str, tuple[bool, bool]]:
    """`{identidad_key: (es_cuenta_no_persona, cedula_sospechosa)}` of every profile as it stands
    BEFORE the D-P2 unification (stage 1 + remap), i.e. each row's own flags."""
    perfiles, _ = dep.fusionar_identidad(entrada["stickers"], entrada["roster_by_cedula"], entrada["referencia"])
    perfiles, _ = dep.remapear_codigos(perfiles, entrada["referencia"])
    return {clave: (p.es_cuenta_no_persona, p.cedula_sospechosa) for clave, p in perfiles.items()}


def test_inv7_a_merge_never_changes_the_survivors_own_flags(corpus):
    def comprobar(caso):
        previas = _banderas_previas(caso.entrada)
        for fila in caso.resultado.inspectores:
            propias = previas.get(fila["identidad_key"])
            if propias is None:
                yield f"INV-7: output row {fila['identidad_key']!r} has no pre-unification profile"
            elif (fila["no_persona"], fila["cedula_sospechosa"]) != propias:
                yield (f"INV-7: row {fila['identidad_key']!r} ends with (no_persona, cedula_sospechosa)="
                       f"{(fila['no_persona'], fila['cedula_sospechosa'])} but its own flags were {propias}")
    _ok(_fallos(corpus, comprobar))


def test_inv7_corpus_really_exercises_a_flagged_loser_merged_into_a_clean_survivor(corpus):
    """Anti-vacuity: the corpus contains merges where the loser carried a flag the survivor does not."""
    hallazgos = 0
    for caso in corpus:
        if caso.resultado is None or not any(f["cedulas_unificadas"] for f in caso.resultado.inspectores):
            continue
        perfiles, _ = dep.fusionar_identidad(caso.entrada["stickers"], caso.entrada["roster_by_cedula"],
                                             caso.entrada["referencia"])
        perfiles, _ = dep.remapear_codigos(perfiles, caso.entrada["referencia"])
        finales = {f["identidad_key"]: f for f in caso.resultado.inspectores}
        _, fusiones = dep.unificar_duplicados(perfiles)
        for fusion in fusiones:
            survivor, perdedor = perfiles[fusion["survivor"]], perfiles[fusion["perdedor"]]
            if perdedor.es_cuenta_no_persona and not survivor.es_cuenta_no_persona and fusion["survivor"] in finales:
                hallazgos += 1
    assert hallazgos >= 3, hallazgos


# ── hostile shapes: None / NaN everywhere still satisfies the invariants ─────


def _hostilizar(entrada: dict, rnd: random.Random) -> dict:
    """Replaces random string fields with None / NaN / whitespace (never a value the
    contract forbids: the engine must be tolerant, `_txt` never raises)."""
    def basura():
        return rnd.choice([None, float("nan"), "  ", ""])

    copia = copy.deepcopy(entrada)
    for fila in copia["roster_by_cedula"].values():
        for campo in ("nombre_completo", "codigo", "correo", "entidad", "creado_en", "num_telefono"):
            if rnd.random() < 0.25:
                fila[campo] = basura()
    ref = copia["referencia"]

    def hostil(entradas):
        salida = []
        for e in entradas:
            cambios = {c: basura() for c in ("nombre_norm", "codigo", "np", "entidad", "correo")
                       if rnd.random() < 0.25}
            salida.append(dataclasses.replace(e, **cambios))
        return tuple(salida)

    copia["referencia"] = dataclasses.replace(ref, vercel=hostil(ref.vercel), fase2=hostil(ref.fase2), main=hostil(ref.main))
    for sticker in copia["stickers"]:
        if rnd.random() < 0.3:
            sticker["inspector"] = rnd.choice([None, {}, {"identificacion": None}])
    return copia


def test_hostile_none_nan_and_whitespace_inputs_keep_every_invariant():
    fallos = []
    for semilla in range(120):
        rnd = random.Random(10_000 + semilla)
        entrada = _hostilizar(_generar(random.Random(semilla)), rnd)
        try:
            resultado = _correr(entrada)
            otro = _correr(_permutar(entrada, random.Random(semilla)))
        except Exception as exc:  # noqa: BLE001
            fallos.append(f"seed={semilla}: {type(exc).__name__}: {exc}")
            continue
        caso = Caso(semilla, entrada, resultado)
        if _canonico(resultado) != _canonico(otro):
            fallos.append(f"seed={semilla}: order-dependent on hostile input")
        cubiertos = {dep._txt(i.get("codigo")) for i in resultado.revision_manual if i["motivo"] in MOTIVOS_INV1}
        titulares: dict[str, int] = {}
        for fila in resultado.inspectores:
            if fila["codigo"]:
                titulares[fila["codigo"]] = titulares.get(fila["codigo"], 0) + 1
        fallos += [f"seed={semilla}: INV-1 codigo {c!r}" for c, n in titulares.items() if n >= 2 and c not in cubiertos]
        fallos += [f"seed={semilla}: INV-6 {f['identidad_key']!r}" for f in resultado.inspectores
                   if f["estado_sugerido"] == "activo" and not f["codigo"]]
        finales, alias = _claves_resolubles(resultado)
        fallos += [f"seed={semilla}: INV-2 {i['motivo']} {c!r}" for i in resultado.revision_manual
                   for c in _claves_de(i) if _norm(c) not in finales | alias]
        assert caso.semilla == semilla
    assert not fallos, "\n".join(fallos[:5])


# ── degenerate and large inputs ──────────────────────────────────────────────


def test_empty_inputs_yield_an_empty_result():
    resultado = dep.depurar(stickers=[], roster_by_cedula={}, nombres_survey=[],
                            referencia=ReferenciaBundle.vacia(motivo="sin_blob"), hoy=HOY)
    assert resultado.inspectores == () and resultado.revision_manual == () and resultado.grupo_externos is None


def test_a_large_universe_sharing_one_codigo_stays_fast_and_reports_it_once():
    import time
    roster = {f"r{i}": {"identificacion": str(20_000_000 + i), "nombre_completo": f"Persona Numero {i} Distinta",
                        "codigo": "041"} for i in range(1500)}
    bundle = ReferenciaBundle.vacia(motivo="sin_blob")
    t0 = time.perf_counter()
    resultado = dep.depurar(stickers=[], roster_by_cedula=roster, nombres_survey=[], referencia=bundle, hoy=HOY)
    assert time.perf_counter() - t0 < 5.0  # generous: a canary against quadratic blow-ups only
    locales = [i for i in resultado.revision_manual if i["motivo"] == "codigo_duplicado_local"]
    assert len(locales) == 1 and len(locales[0]["identidad_keys"]) == 1500


# ── D-ENFASIS: the free-text field is carried, never invented ────────────────


def test_enfasis_output_only_ever_carries_a_text_of_a_main_entry(corpus):
    """Backfill and unification MOVE an `enfasis`; nothing may ever make one up. The corpus must also
    really exercise the field (some row ends with one, some without)."""
    con_texto = sin_texto = 0

    def comprobar(caso):
        nonlocal con_texto, sin_texto
        posibles = {e.enfasis for e in caso.entrada["referencia"].main}
        for fila in caso.resultado.inspectores:
            if fila["enfasis"]:
                con_texto += 1
                if fila["enfasis"] not in posibles:
                    yield f"INV-ENFASIS: {fila['identidad_key']} carries {fila['enfasis']!r}, not in main {posibles}"
            else:
                sin_texto += 1
    _ok(_fallos(corpus, comprobar))
    assert con_texto > 50 and sin_texto > 50
