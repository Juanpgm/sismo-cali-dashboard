"""Depuración engine for `seguimiento-inspectores-depurado` — design.md
File Changes / Interfaces-Contracts. **Deep module, zero I/O.** `depurar()`
is the only public entry point; everything else is an internal seam,
independently testable (design's Testing Strategy table).

## Internal data contracts (this module's own, since design.md left the six
seam signatures as sketches for `sdd-apply` to flesh out)

`stickers: list[dict]` — one dict per sticker, expected shape:

    {
        "origen": str,                 # "sistema" | "firebase" | other
        "fecha_creacion": str | datetime | None,  # ISO-8601 or aware
                                        # datetime; the sticker's raw
                                        # creation moment (NOT the possibly
                                        # match-overridden `fecha` field
                                        # `stickers_atencionsismo.normalize_sticker`
                                        # emits today).
        "inspector": {
            "identificacion": str,     # professional cédula; resolved to a
                                        # profile through the cédula alias
                                        # index (dots/zeros/float tails are
                                        # normalized). Unattributable stickers
                                        # are silently skipped, never raised.
            "codigo": str,
            "nombre_completo": str,
        },
    }

Snake_case (not the raw API's `fechaCreacion` camelCase) because this is a
deep module one level removed from the HTTP/JSON boundary — Phase 3's router
wiring owns translating the live `evaluaciones[]` shape (today: `origen` +
`fecha`/`fecha_fuente`, where `fecha` is match-overridden and does not always
equal the raw `fechaCreacion` — a real integration gap flagged for Phase 3,
see the apply report's Deviations section) into this contract.

`roster_by_cedula: dict[str, dict]` — Firestore `inspector_profiles()`.
Each value:

    {
        "identificacion": str,   # REQUIRED to be represented at all; blank
                                  # -> skipped. Profiles are deduplicated by
                                  # the digits-only `_cedula_key` of it.
        "nombre_completo": str,
        "correo": str,
        "codigo": str,            # existing brigade code assignment, if any
        "entidad": str,
        "tarjeta_profesional": str,
        "num_telefono": str,
        "correo_contacto": str,
        "creado_en": str,         # ISO date/datetime string, tie-break only
    }

`nombres_survey: Iterable[str]` — raw `survey_cali` person names (any case/
accents); normalized internally via `normalizar_nombre`.

`referencia: ReferenciaBundle` — `app.services.inspectores_referencia`. Its
`main` section is a UNIVERSE source (extension 2026-09-19, D9): one Perfil per
distinct cédula, Firestore first. `vercel`/`fase2` stay overlays.

## Pipeline (design.md "Pipeline Order")

1. `fusionar_identidad` = universe (roster, then `main`) + overlays + sticker
   attribution through the cédula alias index. Returns `(perfiles, revision)`.
2. `remapear_codigos`, 3. `unificar_duplicados` (D-P2, AFTER the overlays and
   the sticker attribution so the survivor score sees populated flags, D10),
4. `entidad` / `np` / `fase` / `estado_sugerido` / `fuente_dato`,
5. `colapsar_externos`, 6. `alias_nombres` (redacted logging).

`hoy: date` — Bogotá "today" (design D6: cache-key input, not computed here).
"""
from __future__ import annotations

import copy
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable

from rapidfuzz import fuzz

from app.services import cedula_utils
from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle

_LOG = logging.getLogger(__name__)

# ── Constants (explore.md Q4/Q6) ────────────────────────────────────────────

CORTE_20AGO = datetime(2026, 8, 20, tzinfo=timezone.utc)
FASE_NP_RE = re.compile(r"^P?\s*(\d+)", re.IGNORECASE)
FUZZY_REMAP_THRESHOLD = 90.0

PATRONES_CORREO_NO_PERSONA = ("@import.local", ".internal", "migrated", "@sismo.cali.gov.co")
PATRONES_NOMBRE_NO_PERSONA = ("prueba", "migracion", "brigada", "secretaria de educacion", "test ")


def normalizar_nombre(nombre: object) -> str:
    """Casefold + strip diacritics + collapse whitespace — the single
    normalization used for every name-based match in this module (exact
    dedup keys, fuzzy remap input, survey alias matching)."""
    text = str(nombre or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def es_cuenta_no_persona(correo: object, nombre: object) -> bool:
    """explore.md Q4 — a PRIORITIZATION heuristic (spec: "Priority
    Heuristics Are Not Auto-Disqualifying"), never an auto-disqualifier by
    itself outside the dedicated `estado_sugerido="no_persona"` branch."""
    correo_norm = str(correo or "").strip().lower()
    if any(p in correo_norm for p in PATRONES_CORREO_NO_PERSONA):
        return True
    nombre_norm = normalizar_nombre(nombre)
    return any(p in nombre_norm for p in PATRONES_NOMBRE_NO_PERSONA)


def _txt(value: object) -> str:
    """Tolerant scalar -> stripped text. `None` and non-finite floats (NaN/inf)
    become `""`, never the literal "nan"/"None". Never raises."""
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value).strip()


def _digitos(value: object) -> str:
    """Digits-only form of a cédula via the shared rule (`cedula_utils`): a lone
    float-artifact ".0" tail ("12345.0", a float that went through pandas/JSON)
    is dropped first so it never appends a spurious trailing zero; every other
    dot/space/dash is a plain separator ("12.345.678" -> "12345678", "166.000"
    -> "166000": a thousands separator is NOT a float artifact, design
    D-CEDDEC). Leading zeros are kept (this is the value a profile is keyed and
    displayed by)."""
    return cedula_utils.solo_digitos(_txt(value))


def _cedula_key(value: object) -> str:
    """The module's ONE identity key for a cédula: digits only, a lone ".0"
    float artifact dropped, NOTHING else. Leading zeros are KEPT, exactly like
    the frontend's `cedulaKey` (web/js/seguimiento.js), an EXACT mirror of this
    function (same regex, `cedula_utils.COLA_FLOTANTE_PATRON`, pinned by a
    test): "0123456" and "123456" are two different keys, so the row keys the
    backend emits are the ones the frontend derives. Used by the universe, the
    overlays, the remap, `identidad_key`/`identificacion`, `cedula_sospechosa`
    and the exported `cedulas_unificadas`."""
    return _digitos(value)


def _cedula_alias_key(value: object) -> str:
    """The zero-STRIPPED form ("0012345" -> "12345"), used ONLY as the fallback
    of the sticker alias index (`atribuir_stickers`, spec "Exact-string
    identificacion lookup is not the path"). It never keys a profile: when it
    is what resolves a sticker, the sticker's own `_cedula_key` form is exported
    in `cedulas_unificadas` so the frontend can still route it. An all-zero
    value keeps its zeros (a placeholder must not collapse into "" = no key)."""
    digitos = _cedula_key(value)
    return digitos.lstrip("0") or digitos


def cedula_sospechosa(cedula: object) -> bool:
    """explore.md Q4 — digit-only length outside 6-10, or exactly 10 digits
    not starting with "1". Also a PRIORITIZATION-only heuristic.

    Reads the SAME zero-preserving digits as the join key (`_cedula_key`), so
    "0012345678" is 10 digits starting with "0" (suspicious) and a lone ".0"
    float artifact is dropped ("1234567890.0" is fine, "12345.0" is 5 digits)
    while a thousands-separated one keeps all its digits ("166.000" is the
    6-digit 166000). Only divergence from the 04fbf2e behaviour: design.md
    D-CEDDEC."""
    digits = _cedula_key(cedula)
    if not digits:
        return True
    if not (6 <= len(digits) <= 10):
        return True
    return len(digits) == 10 and not digits.startswith("1")


def _parse_fecha(value: object) -> datetime | None:
    """Never raises. Accepts an aware/naive `datetime` (naive assumed UTC)
    or an ISO-8601 string (`Z` suffix tolerated). Anything else -> `None`."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _sticker_es_valido(sticker: dict) -> bool:
    """Spec: "20-August Cutoff Scoped To sistema Origin" (D7 fix). Only
    `origen == "sistema"` (case-insensitive) counts; any other value,
    including "firebase" or an unrecognized origin, is excluded."""
    if not isinstance(sticker, dict):
        return False
    origen = str(sticker.get("origen") or "").strip().lower()
    if origen != "sistema":
        return False
    fecha = _parse_fecha(sticker.get("fecha_creacion"))
    if fecha is None:
        return False
    return fecha >= CORTE_20AGO


# ── Perfil — the per-identity accumulator ───────────────────────────────────


@dataclass
class Perfil:
    identidad_key: str
    identificacion: str
    nombre_completo: str
    nombre_norm: str
    correo: str = ""
    codigo: str = ""
    # DERIVED, never set by a builder: the RESOLVED value (`resolver_entidad`)
    # computed from the three `entidad_*` source fields below, after the overlays.
    entidad: str = ""
    entidad_firestore: str = ""
    entidad_vercel: str = ""  # only ever set by a Vercel match BY CÉDULA (D12)
    entidad_main: str = ""
    tarjeta_profesional: str = ""
    num_telefono: str = ""
    correo_contacto: str = ""
    creado_en: str = ""
    id: str = ""  # `main` row uuid; carried for review/audit, not part of the response
    firestore_backed: bool = False  # last tie-break of the D-P2 survivor score (D10)
    cedulas_unificadas: tuple[str, ...] = ()
    en_vercel: bool = False
    en_fase2: bool = False
    np_vercel: str = ""
    np_fase2: str = ""
    rango_main: str = ""
    no_persona_ref: bool = False
    ultimo_sticker: date | None = None
    n_stickers: int = 0
    tiene_sticker_valido: bool = False
    dias_inactivo: int | None = None
    cedula_sospechosa: bool = False
    es_cuenta_no_persona: bool = False
    np: str = ""
    np_fuente: str = "ninguno"
    fase: str = "Fase I"
    fase_np_faltante: bool = True
    estado_sugerido: str = "revisar"
    fuente_dato: str = "main"


@dataclass(frozen=True)
class Depuracion:
    activa: bool
    motivo: str
    referencia_generada_en: str
    inspectores: tuple[dict, ...]
    grupo_externos: dict | None
    alias_nombres: dict[str, str]
    # Carries cédulas and person names (PII): it must stay admin-gated, exactly
    # like `inspectores` (the router only ships it under the admin role).
    revision_manual: tuple[dict, ...]


# ── Survivor scoring, used by the D-P2 unification (stage 3) ────────────────


def _survivor_score(p: Perfil) -> tuple:
    """has_sticker > has_codigo > en_vercel > en_fase2 > not_no_persona >
    not_cedula_sospechosa (spec: "Code Remap And Duplicate Unification").
    `has_sticker` is `n_stickers > 0` (notebook) — the `ultimo_sticker` check
    only covers a profile built without an aggregate count."""
    return (
        p.n_stickers > 0 or p.ultimo_sticker is not None,
        bool(p.codigo),
        p.en_vercel,
        p.en_fase2,
        not p.es_cuenta_no_persona,
        not p.cedula_sospechosa,
    )


_SIN_FECHA = datetime.max.replace(tzinfo=timezone.utc)


def _elegir_survivor(candidatos: list[Perfil]) -> Perfil:
    """Highest score wins. Ties break on the oldest `creado_en` (a blank or
    unparseable one never beats a real date), then on Firestore-backed before
    main-only (D10: kept LAST so it never displaces the notebook's
    parity-bearing rules), then first-seen (insertion order) so the outcome is
    deterministic."""
    puntajes = [_survivor_score(c) for c in candidatos]
    max_score = max(puntajes)

    def desempate(indice: int) -> tuple:
        fecha = _parse_fecha(candidatos[indice].creado_en)
        return (fecha is None, fecha or _SIN_FECHA, not candidatos[indice].firestore_backed, indice)

    mejor = min((i for i, score in enumerate(puntajes) if score == max_score), key=desempate)
    return candidatos[mejor]


# `creado_en` is deliberately NOT here: it is the survivor tie-break input, not
# an output field, and the survivor keeps its own date (a loser's date must not
# rewrite the survivor's history).
_CAMPOS_TEXTO_BACKFILL = (
    "correo", "codigo", "tarjeta_profesional", "num_telefono", "correo_contacto",
    "rango_main", "id", "entidad_firestore", "entidad_vercel", "entidad_main",
)


def _recalcular_no_persona(perfil: Perfil) -> None:
    """`es_cuenta_no_persona` from the profile's CURRENT correo / nombre /
    `no_persona_ref` (bundle-precomputed flag). Called by the overlay pass and
    again after a D-P2 merge, whose backfilled correo / ORed `no_persona_ref`
    would otherwise leave the flag stale (the overlays already ran)."""
    perfil.es_cuenta_no_persona = perfil.no_persona_ref or es_cuenta_no_persona(
        perfil.correo, perfil.nombre_completo
    )


def _fusionar_en_survivor(survivor: Perfil, perdedor: Perfil) -> None:
    """Backfill = "first non-empty wins" for every text field (losers are
    visited in roster order), NOT a sum. The sticker aggregates ARE absorbed
    (count summed, `ultimo_sticker` the later one, `tiene_sticker_valido`
    OR-ed), and every key the loser answered to — its own cédula and any it had
    already absorbed — is registered in `cedulas_unificadas` so later lookups
    resolve to the survivor (compared by `_cedula_key`, never by raw string).
    `es_cuenta_no_persona` is NOT a pure recompute of the merged fields: it is
    the survivor's own flag OR-ed with a fresh evaluation of the survivor's
    CURRENT correo / nombre / `no_persona_ref`. Only the bundle flag
    `no_persona_ref` is OR-ed from the loser; the loser's HEURISTIC flag (its
    own `@import.local` correo, say) is dropped whenever the survivor already
    has a non-empty correo (backfill never overwrites), so a real survivor never
    becomes a "non-person" because of a loser. That is the notebook's rule:
    "survivor keeps its own flags, backfill first-non-empty". `cedula_sospechosa`
    stays the survivor's, computed from its ORIGINAL cédula (D18)."""
    for campo in _CAMPOS_TEXTO_BACKFILL:
        if not getattr(survivor, campo):
            setattr(survivor, campo, getattr(perdedor, campo))
    if not survivor.en_vercel and perdedor.en_vercel:
        survivor.en_vercel, survivor.np_vercel = True, perdedor.np_vercel
    if not survivor.en_fase2 and perdedor.en_fase2:
        survivor.en_fase2, survivor.np_fase2 = True, perdedor.np_fase2
    if not survivor.no_persona_ref and perdedor.no_persona_ref:
        survivor.no_persona_ref = True
    survivor.n_stickers += perdedor.n_stickers
    if perdedor.ultimo_sticker and (
        not survivor.ultimo_sticker or perdedor.ultimo_sticker > survivor.ultimo_sticker
    ):
        survivor.ultimo_sticker = perdedor.ultimo_sticker
    survivor.tiene_sticker_valido = survivor.tiene_sticker_valido or perdedor.tiene_sticker_valido
    survivor.firestore_backed = survivor.firestore_backed or perdedor.firestore_backed
    propia = _cedula_key(survivor.identificacion) or _txt(survivor.identificacion)
    vistas = {propia}
    unificadas: list[str] = []
    # Forms the survivor ALREADY exports are kept verbatim (this includes the raw
    # roster form of its own cédula, e.g. "1234567.0"); only exact repeats and its
    # own key are dropped. The loser's forms are then filtered by cédula key.
    for clave in survivor.cedulas_unificadas:
        texto = _txt(clave)
        if texto and texto != propia and clave not in unificadas:
            vistas.add(_cedula_key(texto) or texto)
            unificadas.append(clave)
    for clave in (perdedor.identificacion, *perdedor.cedulas_unificadas):
        normalizada = _cedula_key(clave) or _txt(clave)
        if normalizada and normalizada not in vistas:
            vistas.add(normalizada)
            unificadas.append(clave)
    survivor.cedulas_unificadas = tuple(unificadas)
    previo = survivor.es_cuenta_no_persona
    _recalcular_no_persona(survivor)
    survivor.es_cuenta_no_persona = survivor.es_cuenta_no_persona or previo


def _unificar_por_nombre(perfiles: dict[str, Perfil]) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """Exact `nombre_norm` match only (spec: "Exact-name duplicate merge
    never deletes the losing record" — excluded from the OUTPUT dict, never
    mutated/removed from the CALLER's input `perfiles`: every merge works on
    a deep copy of the survivor, so `perfiles`'s own objects are untouched).
    An empty `nombre_norm` never groups (two nameless profiles are two
    people)."""
    grupos: dict[str, list[str]] = {}
    for key, perfil in perfiles.items():
        if not perfil.nombre_norm:
            continue
        grupos.setdefault(perfil.nombre_norm, []).append(key)

    resultado = dict(perfiles)
    fusiones: list[dict] = []
    for nombre_norm, keys in grupos.items():
        if len(keys) < 2:
            continue
        candidatos = [perfiles[k] for k in keys]
        survivor_original = _elegir_survivor(candidatos)
        survivor = copy.deepcopy(survivor_original)
        for candidato in candidatos:
            if candidato is survivor_original:
                continue
            _fusionar_en_survivor(survivor, candidato)
            del resultado[candidato.identidad_key]
            fusiones.append({
                "survivor": survivor.identidad_key,
                "perdedor": candidato.identidad_key,
                "nombre_norm": nombre_norm,
            })
        resultado[survivor.identidad_key] = survivor
    return resultado, tuple(fusiones)


# ── Stage 1: fusionar_identidad — universe, overlays, sticker attribution ────


class _IndiceReferencia:
    """One reference section indexed ONCE by `_cedula_key` and by
    `nombre_norm` (D14: replaces the O(n·m) linear scan). First row wins on a
    repeated key, exactly like the scan it replaces; an empty key is never
    indexed, so an empty name/cédula can never match another empty one."""

    __slots__ = ("por_cedula", "por_nombre")

    def __init__(self, entradas: Iterable[EntradaReferencia]) -> None:
        self.por_cedula: dict[str, EntradaReferencia] = {}
        self.por_nombre: dict[str, EntradaReferencia] = {}
        for entrada in entradas or ():
            cedula = _cedula_key(entrada.cedula_key)
            if cedula:
                self.por_cedula.setdefault(cedula, entrada)
            nombre = _nombre_norm_de_entrada(entrada)
            if nombre:
                self.por_nombre.setdefault(nombre, entrada)

    def buscar(self, cedula_k: str, nombre_norm: str) -> tuple[EntradaReferencia | None, bool]:
        """Q1: cédula match first, exact `nombre_norm` fallback. The bool says
        whether the hit was BY CÉDULA (D12 needs that distinction)."""
        if cedula_k:
            entrada = self.por_cedula.get(cedula_k)
            if entrada is not None:
                return entrada, True
        if nombre_norm:
            entrada = self.por_nombre.get(nombre_norm)
            if entrada is not None:
                return entrada, False
        return None, False


def _nombre_de_entrada(entrada: EntradaReferencia) -> str:
    return _txt(entrada.nombre) or _txt(entrada.nombre_norm)


def _nombre_norm_de_entrada(entrada: EntradaReferencia) -> str:
    """The ONE `nombre_norm` of a reference row: its own `nombre_norm` when
    present (the publisher already normalized it), else `nombre` normalized
    (an old bundle carries no `nombre`; a new one may carry no `nombre_norm`).
    Shared by the reference indexes and the `main` profile builders."""
    return normalizar_nombre(_txt(entrada.nombre_norm)) or normalizar_nombre(_txt(entrada.nombre))


def _perfil_desde_main(entrada: EntradaReferencia, cedula_id: str) -> Perfil:
    """A `main`-only profile (D9). `nombre_completo` falls back to the title-
    cased `nombre_norm` when an OLD bundle carries no `nombre`; the notebook
    also title-cases. `correo` feeds `es_cuenta_no_persona` (notebook input);
    `correo_contacto` is the lowercased contact copy."""
    nombre = _txt(entrada.nombre)
    nombre_norm = _nombre_norm_de_entrada(entrada)
    correo = _txt(entrada.correo)
    return Perfil(
        identidad_key=cedula_id,
        identificacion=cedula_id,
        nombre_completo=nombre or nombre_norm.title(),
        nombre_norm=nombre_norm,
        correo=correo,
        codigo=_txt(entrada.codigo),
        tarjeta_profesional=_txt(entrada.tarjeta_profesional),
        num_telefono=re.sub(r"\D", "", _txt(entrada.telefono), flags=re.ASCII),
        correo_contacto=correo.lower(),
        creado_en=_txt(entrada.creado_en),
        id=_txt(entrada.id),
    )


def _rellenar_desde_main(perfil: Perfil, entrada: EntradaReferencia) -> None:
    """Firestore-first: a `main` row that matches an existing profile by
    cédula only FILLS what is still empty — it never overwrites."""
    nombre = _txt(entrada.nombre)
    if not perfil.nombre_norm:
        nombre_norm = _nombre_norm_de_entrada(entrada)
        if nombre_norm:
            perfil.nombre_norm = nombre_norm
            perfil.nombre_completo = perfil.nombre_completo or nombre or nombre_norm.title()
    correo = _txt(entrada.correo)
    for campo, valor in (
        ("correo", correo),
        ("codigo", _txt(entrada.codigo)),
        ("tarjeta_profesional", _txt(entrada.tarjeta_profesional)),
        ("num_telefono", re.sub(r"\D", "", _txt(entrada.telefono), flags=re.ASCII)),
        ("correo_contacto", correo.lower()),
        ("creado_en", _txt(entrada.creado_en)),
        ("id", _txt(entrada.id)),
    ):
        if valor and not getattr(perfil, campo):
            setattr(perfil, campo, valor)


def construir_universo(
    roster_by_cedula: dict[str, dict], referencia: ReferenciaBundle
) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """D9/D19: one profile per distinct `_cedula_key`, Firestore roster first,
    then `referencia.main`. Returns `(perfiles, revision_manual)`.

    - A `main` row whose cédula already has a profile only backfills empty
      fields; it never creates a second profile.
    - Two `main` rows with the same cédula: the FIRST wins, the second changes
      nothing and is reported (`cedula_duplicada_main`), never silently dropped.
    - A `main` row without a single digit in its cédula creates no profile and
      is reported (`main_sin_cedula`).
    Email-derived cédulas are never used as a key."""
    perfiles: dict[str, Perfil] = {}
    por_cedula: dict[str, Perfil] = {}
    revision: list[dict] = []

    for roster_entry in (roster_by_cedula or {}).values():
        if not isinstance(roster_entry, dict):
            continue
        identificacion = _txt(roster_entry.get("identificacion"))
        if not identificacion:
            continue  # no identity anchor at all -> cannot be represented
        # Keyed by the normalized cédula exactly like `_perfil_desde_main` (C3): the
        # join key never depends on how the roster spelled it ("1.234.567",
        # "1234567.0"). The RAW roster text is exported in `cedulas_unificadas`
        # (once, never the row's own key) so any form the API/frontend carries maps
        # to the row.
        clave = _cedula_key(identificacion) or identificacion
        existente = por_cedula.get(clave)
        if existente is not None:
            # Two roster entries collapse to the SAME cédula -> one person; keep
            # first-seen and ALWAYS register the absorbed cédula (same name or
            # not) so the frontend can still resolve a sticker carrying it.
            if identificacion != existente.identificacion and identificacion not in existente.cedulas_unificadas:
                existente.cedulas_unificadas = (*existente.cedulas_unificadas, identificacion)
            continue
        nombre_completo = _txt(roster_entry.get("nombre_completo"))
        perfil = Perfil(
            identidad_key=clave,
            identificacion=clave,
            nombre_completo=nombre_completo,
            nombre_norm=normalizar_nombre(nombre_completo),
            correo=_txt(roster_entry.get("correo")),
            codigo=_txt(roster_entry.get("codigo")),
            entidad_firestore=_txt(roster_entry.get("entidad")),
            tarjeta_profesional=_txt(roster_entry.get("tarjeta_profesional")),
            num_telefono=_txt(roster_entry.get("num_telefono")),
            correo_contacto=_txt(roster_entry.get("correo_contacto")),
            creado_en=_txt(roster_entry.get("creado_en")),
            firestore_backed=True,
            cedulas_unificadas=(identificacion,) if identificacion != clave else (),
        )
        perfiles[clave] = perfil
        por_cedula[clave] = perfil

    vistos_main: dict[str, EntradaReferencia] = {}
    for entrada in referencia.main:
        clave = _cedula_key(entrada.cedula_key)
        if not clave:
            revision.append({
                "motivo": "main_sin_cedula",
                "cedula_key": _txt(entrada.cedula_key),
                "nombre_completo": _nombre_de_entrada(entrada),
                "id": _txt(entrada.id),
            })
            continue
        primera = vistos_main.get(clave)
        if primera is not None:
            nombre_a, nombre_b = _nombre_de_entrada(primera), _nombre_de_entrada(entrada)
            revision.append({
                "motivo": "cedula_duplicada_main",
                "cedula_key": clave,
                "nombre_completo": nombre_a,
                "nombre_completo_duplicado": nombre_b,
                "id": _txt(primera.id),
                "id_duplicado": _txt(entrada.id),
                "mismo_nombre": bool(nombre_a) and normalizar_nombre(nombre_a) == normalizar_nombre(nombre_b),
            })
            continue
        vistos_main[clave] = entrada
        perfil = por_cedula.get(clave)
        if perfil is None:
            perfil = _perfil_desde_main(entrada, clave)
            perfiles[clave] = perfil
            por_cedula[clave] = perfil
        else:
            _rellenar_desde_main(perfil, entrada)

    return perfiles, tuple(revision)


def superponer_referencia(perfiles: dict[str, Perfil], referencia: ReferenciaBundle) -> None:
    """Overlays Vercel / Fase 2 / `main` onto every profile by `_cedula_key`,
    else by exact `nombre_norm`; first row wins (D14 indexes, built once). They
    never create profiles. `entidad_vercel` is only taken from a match BY
    CÉDULA (D12), then the two priority heuristics are computed."""
    vercel = _IndiceReferencia(referencia.vercel)
    fase2 = _IndiceReferencia(referencia.fase2)
    main = _IndiceReferencia(referencia.main)
    for perfil in perfiles.values():
        cedula_k = _cedula_key(perfil.identificacion)

        entrada, por_cedula = vercel.buscar(cedula_k, perfil.nombre_norm)
        if entrada is not None:
            perfil.en_vercel = True
            perfil.np_vercel = _txt(entrada.np)
            if por_cedula and not perfil.entidad_vercel:
                perfil.entidad_vercel = _txt(entrada.entidad)

        entrada, _ = fase2.buscar(cedula_k, perfil.nombre_norm)
        if entrada is not None:
            perfil.en_fase2 = True
            perfil.np_fase2 = _txt(entrada.np)

        entrada, _ = main.buscar(cedula_k, perfil.nombre_norm)
        if entrada is not None:
            if not perfil.rango_main:
                perfil.rango_main = _txt(entrada.np)
            if entrada.no_persona:
                perfil.no_persona_ref = True
            if not perfil.entidad_main:
                perfil.entidad_main = _txt(entrada.entidad)

        perfil.cedula_sospechosa = cedula_sospechosa(perfil.identificacion)
        _recalcular_no_persona(perfil)


class _IndiceAlias:
    """D11: cédula -> profile for sticker attribution. Two levels:

    - `exacto`: `_cedula_key` (zero-preserving) of each profile's OWN cédula and
      of every cédula unified into it. Own keys are registered first so a profile
      always owns its cédula over another profile's alias claim.
    - `sin_ceros`: the zero-STRIPPED fallback (`_cedula_alias_key`) so "0012345"
      and "12345" still resolve (spec: "Exact-string identificacion lookup is not
      the path"). A stripped key claimed by two DIFFERENT profiles is ambiguous
      (`None`): a sticker is never guessed onto either, which also keeps the
      outcome independent of input order."""

    __slots__ = ("exacto", "sin_ceros")

    def __init__(self, perfiles: dict[str, Perfil]) -> None:
        self.exacto: dict[str, Perfil] = {}
        for perfil in perfiles.values():
            clave = _cedula_key(perfil.identificacion)
            if clave:
                self.exacto.setdefault(clave, perfil)
        for perfil in perfiles.values():
            for alias in perfil.cedulas_unificadas:
                clave = _cedula_key(alias)
                if clave:
                    self.exacto.setdefault(clave, perfil)
        self.sin_ceros: dict[str, Perfil | None] = {}
        for clave, perfil in self.exacto.items():
            self._reclamar_sin_ceros(clave, perfil)

    def _reclamar_sin_ceros(self, clave: str, perfil: Perfil) -> None:
        """`perfil` claims the zero-stripped form of `clave`; a form already
        claimed by a DIFFERENT profile becomes ambiguous (`None`) and stays so."""
        despojada = _cedula_alias_key(clave)
        if despojada in self.sin_ceros and self.sin_ceros[despojada] is not perfil:
            self.sin_ceros[despojada] = None
        else:
            self.sin_ceros[despojada] = perfil

    def resolver(self, cedula_sticker: object) -> tuple[Perfil | None, str]:
        """`(profile, sticker_key)`; the key is `""` when the value has no digits."""
        clave = _cedula_key(cedula_sticker)
        if not clave:
            return None, ""
        perfil = self.exacto.get(clave)
        if perfil is not None:
            return perfil, clave
        return self.sin_ceros.get(_cedula_alias_key(clave)), clave

    def registrar(self, perfil: Perfil, clave: str) -> None:
        """A sticker resolved through the zero-stripped fallback: export the form
        it carried in `cedulas_unificadas` (the frontend routes by its own
        `cedulaKey`, which keeps zeros) and make the next identical form exact."""
        perfil.cedulas_unificadas = (*perfil.cedulas_unificadas, clave)
        self.exacto[clave] = perfil
        self._reclamar_sin_ceros(clave, perfil)


def atribuir_stickers(perfiles: dict[str, Perfil], stickers: list[dict]) -> None:
    """Aggregates sticker activity onto the profile the sticker's professional
    cédula resolves to THROUGH the alias index (D11) — never by exact-string
    `identificacion`. Resolution is exact-key first, then zero-stripped; when
    only the stripped form matched, the sticker's own cédula form is registered
    in that profile's `cedulas_unificadas` (frontend payload contract). An
    unresolvable/blank cédula or a malformed sticker is skipped, never raised.
    Mutates the profiles in place."""
    indice = _IndiceAlias(perfiles)
    for sticker in (stickers or []):
        if not isinstance(sticker, dict):
            continue
        inspector = sticker.get("inspector")
        if not isinstance(inspector, dict):
            continue
        perfil, clave = indice.resolver(inspector.get("identificacion"))
        if perfil is None:
            continue  # unattributable sticker -> skipped, never raised
        if clave not in indice.exacto:
            indice.registrar(perfil, clave)
        perfil.n_stickers += 1
        fecha = _parse_fecha(sticker.get("fecha_creacion"))
        if fecha is not None:
            fecha_solo = fecha.date()
            if perfil.ultimo_sticker is None or fecha_solo > perfil.ultimo_sticker:
                perfil.ultimo_sticker = fecha_solo
        if _sticker_es_valido(sticker):
            perfil.tiene_sticker_valido = True


def fusionar_identidad(
    stickers: list[dict],
    roster_by_cedula: dict[str, dict],
    referencia: ReferenciaBundle,
) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """Stage 1: universe (Firestore roster ∪ `referencia.main`, D9) + Vercel /
    Fase 2 / `main` overlays + priority heuristics + sticker attribution
    (D11). Returns `(perfiles, revision_manual)`. It does NOT unify name
    duplicates: D-P2 runs later (`unificar_duplicados`, D10) so its survivor
    score is evaluated on populated flags."""
    perfiles, revision = construir_universo(roster_by_cedula, referencia)
    superponer_referencia(perfiles, referencia)
    atribuir_stickers(perfiles, stickers)
    return perfiles, revision


# ── Stage 2: remapear_codigos ────────────────────────────────────────────────


def remapear_codigos(
    perfiles: dict[str, Perfil], referencia: ReferenciaBundle
) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """Cédula match auto-remaps (D1: attribute to the CURRENT titular).
    Fuzzy `token_sort_ratio >= 90` only SURFACES a candidate for manual
    review — NEVER auto-assigns (spec: "Fuzzy match never merges
    automatically"). D-P1: a `codigo` shared by 2+ Vercel entries (or
    already listed in `referencia.codigos_duplicados`) is excluded from
    remap entirely and listed for manual review."""
    revision_manual: list[dict] = []
    excluidos = set(referencia.codigos_duplicados)

    por_codigo: dict[str, list[EntradaReferencia]] = {}
    for entrada in referencia.vercel:
        if entrada.codigo:
            por_codigo.setdefault(entrada.codigo, []).append(entrada)
    for codigo, entradas in por_codigo.items():
        if len(entradas) >= 2 and codigo not in excluidos:
            excluidos.add(codigo)
            revision_manual.append({"motivo": "codigo_vercel_duplicado", "codigo": codigo})

    perfiles_por_cedula = {
        clave: p for p in perfiles.values() if (clave := _cedula_key(p.identificacion))
    }

    for entrada in referencia.vercel:
        if not entrada.codigo or entrada.codigo in excluidos:
            continue
        clave_vercel = _cedula_key(entrada.cedula_key)
        titular = perfiles_por_cedula.get(clave_vercel) if clave_vercel else None
        if titular is not None:
            titular.codigo = entrada.codigo
            continue
        # No cédula match -> fuzzy fallback, SURFACE only (D3).
        mejor_perfil: Perfil | None = None
        mejor_score = 0.0
        for perfil in perfiles.values():
            if not entrada.nombre_norm or not perfil.nombre_norm:
                continue
            score = fuzz.token_sort_ratio(entrada.nombre_norm, perfil.nombre_norm)
            if score > mejor_score:
                mejor_score = score
                mejor_perfil = perfil
        if mejor_perfil is not None and mejor_score >= FUZZY_REMAP_THRESHOLD:
            revision_manual.append({
                "motivo": "codigo_remap_candidato",
                "codigo": entrada.codigo,
                "nombre_vercel": entrada.nombre_norm,
                "identidad_key_candidato": mejor_perfil.identidad_key,
                "score": round(mejor_score, 1),
            })

    return perfiles, tuple(revision_manual)


# ── Stage 3: unificar_duplicados ─────────────────────────────────────────────


def unificar_duplicados(perfiles: dict[str, Perfil]) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """D-P2 exact-name dedup (`_unificar_por_nombre`). Runs AFTER the
    overlays, the sticker attribution and the remap (D10, notebook order) so
    the survivor score (`n_stickers>0 > has_codigo > en_vercel > en_fase2 >
    not no_persona > not cedula_sospechosa`, then oldest `creado_en`, then
    Firestore-backed) is evaluated on populated flags. The survivor absorbs
    the losers' sticker aggregates and every loser cédula is registered in
    `cedulas_unificadas`."""
    return _unificar_por_nombre(perfiles)


# ── Stage 4: colapsar_externos ───────────────────────────────────────────────


def colapsar_externos(
    perfiles: dict[str, Perfil], hoy: date, *, exentos: frozenset[str] = frozenset()
) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """`(cedula_sospechosa OR es_cuenta_no_persona) AND (sin ultimo_sticker
    OR dias_inactivo > 7) AND sin codigo` -> collapsed into GRUPO-EXTERNOS,
    EXCEPT any `identidad_key` in `exentos` (survey_cali corroboration —
    `alias_nombres`, spec: "Non-Person Counts Deduped Against survey_cali").
    `dias_inactivo` is computed here (needs `hoy`) and always stored on the
    surviving Perfil, informational regardless of collapse outcome."""
    restantes: dict[str, Perfil] = {}
    detalle: list[dict] = []
    for key, perfil_original in perfiles.items():
        perfil = copy.deepcopy(perfil_original)
        dias_inactivo = None
        if perfil.ultimo_sticker is not None:
            dias_inactivo = (hoy - perfil.ultimo_sticker).days
        perfil.dias_inactivo = dias_inactivo

        candidato = (
            (perfil.cedula_sospechosa or perfil.es_cuenta_no_persona)
            and (perfil.ultimo_sticker is None or (dias_inactivo is not None and dias_inactivo > 7))
            and not perfil.codigo
        )
        if candidato and key not in exentos:
            detalle.append({
                "nombre_completo": perfil.nombre_completo,
                "identificacion": perfil.identificacion,
                "motivo": "cedula_sospechosa" if perfil.cedula_sospechosa else "cuenta_no_persona",
                "ultimo_sticker": perfil.ultimo_sticker.isoformat() if perfil.ultimo_sticker else None,
            })
        else:
            restantes[key] = perfil
    return restantes, tuple(detalle)


# ── Stage 5: np / fase / estado / fuente ────────────────────────────────────


def resolver_entidad(perfil: Perfil) -> str:
    """D12: first non-empty of the Vercel entry matched BY CÉDULA, the
    Firestore roster value, the `main` value. `""` when no source has one —
    never invented. (`entidad_vercel` is only ever filled by a cédula match,
    so a name-only Vercel hit cannot leak in through here.)"""
    return perfil.entidad_vercel or perfil.entidad_firestore or perfil.entidad_main


def resolver_np(perfil: Perfil) -> tuple[str, str]:
    """Spec: "NP Resolution Hierarchy" — Fase2 > Vercel > rango_main > none."""
    if perfil.en_fase2 and perfil.np_fase2:
        return perfil.np_fase2, "fase2"
    if perfil.en_vercel and perfil.np_vercel:
        return perfil.np_vercel, "vercel"
    if perfil.rango_main:
        return perfil.rango_main, "main"
    return "", "ninguno"


def calcular_fase(np_valor: str) -> tuple[str, bool]:
    """Spec: "Fase Calculation From NP" — `^P?\\s*(\\d+)`, `>=3` -> Fase II."""
    m = FASE_NP_RE.match(str(np_valor or "").strip())
    if not m:
        return "Fase I", True
    numero = int(m.group(1))
    return ("Fase II" if numero >= 3 else "Fase I"), False


def fuente_dato(perfil: Perfil) -> str:
    """explore.md Q5 — "main"/"main+fase2"/"main+vercel"/"main+fase2+vercel".
    The GRUPO-EXTERNOS outlier string is built separately (`_build_grupo_externos`),
    never by this function."""
    partes = ["main"]
    if perfil.en_fase2:
        partes.append("fase2")
    if perfil.en_vercel:
        partes.append("vercel")
    return "+".join(partes)


def estado_sugerido(perfil: Perfil) -> str:
    """Spec: "Estado Sugerido Precedence (Advisory Only)". `tiene_sticker_valido`
    is deliberately NEVER read here (D7 fix) — informational only, exposed
    as a separate field on the same record. `cedula_sospechosa` is also
    deliberately NEVER read here (spec: "Priority Heuristics Are Not
    Auto-Disqualifying") — only `es_cuenta_no_persona` drives the dedicated
    `no_persona` branch.

    `tiene_sticker` here is the RAW all-time flag (`n_stickers > 0`), NEVER
    `tiene_sticker_valido` (the post-20-Aug-informational one) — the
    notebook's own `desactivar`/`revision` id sets key off
    `df_inspectores["tiene_sticker"]` (`n_stickers > 0`), confirmed against
    the notebook source (`analisis_calidad_inspectores.ipynb`, the
    `accion_desactivar.csv`/`revision_manual.csv` construction cell), never
    the cutoff-filtered flag. Spec scenario: "Sticker history without a
    current código falls to the review fallback"."""
    if perfil.es_cuenta_no_persona:
        return "no_persona"
    tiene_codigo = bool(perfil.codigo)
    tiene_sticker = perfil.n_stickers > 0
    en_referencia = perfil.en_vercel or perfil.en_fase2
    if not tiene_sticker and not tiene_codigo and not en_referencia:
        return "candidato_desactivacion"
    if not tiene_sticker and not tiene_codigo and en_referencia:
        return "revisar"
    if tiene_codigo:
        return "activo"
    return "revisar"  # fallback: has had a sticker at some point (tiene_sticker
    # =True) with no código — branches above already exhausted every
    # tiene_sticker=False case, so this is reachable, not dead code.


# ── Stage 6: alias_nombres ───────────────────────────────────────────────────


def alias_nombres(perfiles: dict[str, Perfil], nombres_survey: Iterable[str]) -> dict[str, str]:
    """Maps a `survey_cali` normalized name to the real person's
    `identidad_key` it corresponds to — spec: "a survey name that maps to a
    real person's key never lands in GRUPO-EXTERNOS". Deterministic
    first-wins on a name collision (two different perfiles sharing the
    exact same `nombre_norm`). A collision is reported as a COUNT only: an
    `identidad_key` is now a cédula and `nombre_norm` a person's name, so
    neither may reach a log line at any level (spec: "Contact Fields Are
    Admin-Only And Never Logged In Clear")."""
    survey_norm = {normalizar_nombre(n) for n in (nombres_survey or [])}
    survey_norm.discard("")
    alias: dict[str, str] = {}
    colisiones = 0
    for key, perfil in perfiles.items():
        if not perfil.nombre_norm or perfil.nombre_norm not in survey_norm:
            continue
        if perfil.nombre_norm in alias:
            colisiones += 1
            continue
        alias[perfil.nombre_norm] = key
    if colisiones:
        _LOG.info(
            "inspectores_depuracion.alias_nombres: %d colision(es) de nombre resueltas "
            "first-wins (detalle omitido: PII)", colisiones,
        )
    return alias


# ── Response assembly ────────────────────────────────────────────────────────


def _perfil_a_dict(perfil: Perfil) -> dict:
    return {
        "identidad_key": perfil.identidad_key,
        "nombre_completo": perfil.nombre_completo,
        "identificacion": perfil.identificacion,
        # CRITICAL (adversarial review): a name-duplicate merge
        # (_unificar_por_nombre/_fusionar_en_survivor) keeps the LOSING
        # identity's own cédula here, never on the dict before now — without
        # it, a raw sticker still carrying the losing cédula in
        # `inspector.identificacion` had no way to resolve to this survivor
        # row on the frontend (see seguimiento.js's
        # buildIdentityIndexFromDepuracion/professionalKeyOf).
        "cedulas_unificadas": list(perfil.cedulas_unificadas),
        "codigo": perfil.codigo,
        "entidad": perfil.entidad,
        "np": perfil.np,
        "np_fuente": perfil.np_fuente,
        "fase": perfil.fase,
        "fase_np_faltante": perfil.fase_np_faltante,
        "estado_sugerido": perfil.estado_sugerido,
        "fuente_dato": perfil.fuente_dato,
        "tarjeta_profesional": perfil.tarjeta_profesional,
        "num_telefono": perfil.num_telefono,
        "correo_contacto": perfil.correo_contacto,
        "no_persona": perfil.es_cuenta_no_persona,
        "cedula_sospechosa": perfil.cedula_sospechosa,
        "tiene_sticker_valido": perfil.tiene_sticker_valido,
        "dias_inactivo": perfil.dias_inactivo,
        "ultimo_sticker": perfil.ultimo_sticker.isoformat() if perfil.ultimo_sticker else None,
    }


def _build_grupo_externos(detalle: tuple[dict, ...]) -> dict:
    return {
        "identidad_key": "GRUPO-EXTERNOS",
        "n_colapsados": len(detalle),
        "estado_sugerido": "grupo_externos_agrupado",
        "fuente_dato": f"grupo_agregado (main, {len(detalle)} registros colapsados)",
        "detalle": detalle,
    }


def depurar(
    *,
    stickers: list[dict],
    roster_by_cedula: dict[str, dict],
    nombres_survey: Iterable[str],
    referencia: ReferenciaBundle,
    hoy: date,
) -> Depuracion:
    """The one public entry point (design's Interfaces/Contracts). Advisory
    only: returns a value, takes no mutable Firestore handle, calls no write
    API (spec: "Classification never writes to the inspector record")."""
    perfiles, revision_universo = fusionar_identidad(stickers, roster_by_cedula, referencia)
    perfiles, revision_remap = remapear_codigos(perfiles, referencia)
    perfiles, _fusiones = unificar_duplicados(perfiles)
    alias = alias_nombres(perfiles, nombres_survey)
    perfiles, detalle_externos = colapsar_externos(perfiles, hoy, exentos=frozenset(alias.values()))

    for perfil in perfiles.values():
        perfil.entidad = resolver_entidad(perfil)
        perfil.np, perfil.np_fuente = resolver_np(perfil)
        perfil.fase, perfil.fase_np_faltante = calcular_fase(perfil.np)
        perfil.fuente_dato = fuente_dato(perfil)
        perfil.estado_sugerido = estado_sugerido(perfil)

    inspectores = tuple(_perfil_a_dict(p) for p in perfiles.values())
    grupo_externos = _build_grupo_externos(detalle_externos) if detalle_externos else None

    return Depuracion(
        activa=referencia.activa,
        motivo=referencia.motivo,
        referencia_generada_en=referencia.generado_en,
        inspectores=inspectores,
        grupo_externos=grupo_externos,
        alias_nombres=alias,
        revision_manual=(*revision_universo, *revision_remap),
    )
