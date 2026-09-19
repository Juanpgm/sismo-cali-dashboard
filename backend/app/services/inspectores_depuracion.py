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
   attribution through the cédula alias index + the Fase 2 cédula fix (D18,
   `corregir_cedula_fase2`). Returns `(perfiles, revision)`.
2. `remapear_codigos` (clears the código from every holder that is a DIFFERENT person than
   the Vercel owner, D13 / D-MISMAPERSONA),
   3. `unificar_duplicados` (D-P2, AFTER the overlays and
   the sticker attribution so the survivor score sees populated flags, D10),
4. `entidad` / `np` / `fase` / `estado_sugerido` / `fuente_dato`,
5. `colapsar_externos`, 6. `alias_nombres` (redacted logging).

## Review-item invariants (judgment-day round 3; property-tested)

INV-1 a código held by 2+ final rows is always named by a review item (`codigo_duplicado_local`
is the catch-all); clearing needs Vercel evidence (D13). INV-2 every profile key an item
names is a final row key or a `grupo_externos.detalle` member (`_resolver_claves_revision`).
INV-5 a keeper is never emptied by the pass that cleared the other holders; a código the
unification cannot keep is reported (`codigo_perdido_unificacion`).

`hoy: date` — Bogotá "today" (design D6: cache-key input, not computed here).

## Purity and determinism (design D29)

`depurar()` mutates none of its inputs, returns nothing aliased to them, reads no
clock/environment/module state, and its result does not depend on the ORDER of the
roster or of the stickers (every first-wins choice iterates in `identidad_key`
order; the survivor tie-break ends in the key; every emitted list is canonical).
The `referencia` row order IS part of the input (D19: the first duplicate `main`
row wins). Every `Perfil` field is immutable, which is why a shallow `copy.copy`
is a real copy.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable

from rapidfuzz import fuzz, process

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


def _revision(motivo: str, **detalle: object) -> dict:
    """The ONE constructor of a `revision_manual` item (a plain, fresh dict the
    caller may mutate freely): `motivo` first, then the detail fields."""
    return {"motivo": motivo, **detalle}


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
    # D-ENFASIS: free text from the registry (`main`), never derived; "" when the registry has none.
    enfasis: str = ""
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
    # `_cedula_key` of the Fase 2 row this profile matched (by cédula OR by name), kept
    # even when the D18 fix is blocked: the remap resolves a Vercel owner through it (W3).
    cedula_fase2: str = ""
    # Its `main` cédula key BEFORE the D18 fix rewrote `identificacion` ("" when never
    # rewritten): the notebook's `titular.cedula` in the same-person check (C1).
    cedula_original: str = ""
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
    parity-bearing rules), then the ascending `identidad_key` as the TERMINAL total
    order (D29 / D-TIEBREAK: a full tie is decided by the key, never by iteration
    order, so the survivor is the same for any roster ordering)."""
    puntajes = [_survivor_score(c) for c in candidatos]
    max_score = max(puntajes)

    def desempate(indice: int) -> tuple:
        fecha = _parse_fecha(candidatos[indice].creado_en)
        candidato = candidatos[indice]
        return (fecha is None, fecha or _SIN_FECHA, not candidato.firestore_backed, candidato.identidad_key)

    mejor = min((i for i, score in enumerate(puntajes) if score == max_score), key=desempate)
    return candidatos[mejor]


# `creado_en` is deliberately NOT here: it is the survivor tie-break input, not
# an output field, and the survivor keeps its own date (a loser's date must not
# rewrite the survivor's history).
_CAMPOS_TEXTO_BACKFILL = (
    "correo", "codigo", "tarjeta_profesional", "enfasis", "num_telefono", "correo_contacto",
    "rango_main", "id", "entidad_firestore", "entidad_vercel", "entidad_main",
)


def _recalcular_no_persona(perfil: Perfil) -> None:
    """`es_cuenta_no_persona` from the profile's OWN correo / nombre / `no_persona_ref`
    (bundle-precomputed flag), evaluated ONCE by the overlay pass, before any D-P2
    merge. It is deliberately never called again afterwards: a merge never changes
    the survivor's flags (D-SURVFLAGS, design D20)."""
    perfil.es_cuenta_no_persona = perfil.no_persona_ref or es_cuenta_no_persona(
        perfil.correo, perfil.nombre_completo
    )


def _fusionar_en_survivor(survivor: Perfil, perdedor: Perfil) -> None:
    """Backfill = "first non-empty wins" for every text field (losers are visited
    in ascending `identidad_key` order — `_unificar_por_nombre` builds each group
    from the sorted profiles — so the donor never depends on the roster order,
    D-TIEBREAK), NOT a sum. The sticker aggregates ARE absorbed
    (count summed, `ultimo_sticker` the later one, `tiene_sticker_valido`
    OR-ed), and every key the loser answered to — its own cédula and any it had
    already absorbed — is registered in `cedulas_unificadas` so later lookups
    resolve to the survivor (compared by `_cedula_key`, never by raw string).
    The survivor's FLAGS are never touched (D-SURVFLAGS): `es_cuenta_no_persona`,
    `no_persona_ref` and `cedula_sospechosa` stay what they were, computed per row
    from the survivor's OWN cédula / correo / nombre / bundle flag BEFORE the
    unification, exactly like the notebook (its flags are columns of the row and
    the backfill only fills empty fields, first-non-empty wins). A correo
    backfilled from a loser, or a loser's bundle flag, never turns a real person
    into a "non-person" (nor cures a non-person): that flipped 22 real profiles in
    the first live parity run."""
    for campo in _CAMPOS_TEXTO_BACKFILL:
        if not getattr(survivor, campo):
            setattr(survivor, campo, getattr(perdedor, campo))
    if not survivor.en_vercel and perdedor.en_vercel:
        survivor.en_vercel, survivor.np_vercel = True, perdedor.np_vercel
    if not survivor.en_fase2 and perdedor.en_fase2:
        survivor.en_fase2, survivor.np_fase2 = True, perdedor.np_fase2
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


def _unificar_por_nombre(perfiles: dict[str, Perfil]) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """Exact `nombre_norm` match only (spec: "Exact-name duplicate merge
    never deletes the losing record" — excluded from the OUTPUT dict, never
    mutated/removed from the CALLER's input `perfiles`: every merge works on
    a deep copy of the survivor, so `perfiles`'s own objects are untouched).
    An empty `nombre_norm` never groups (two nameless profiles are two
    people)."""
    grupos: dict[str, list[str]] = {}
    for key, perfil in sorted(perfiles.items()):  # canonical order: groups, losers, backfill (D29)
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
        survivor = copy.copy(survivor_original)  # every Perfil field is immutable
        for candidato in candidatos:
            if candidato is survivor_original:
                continue
            _fusionar_en_survivor(survivor, candidato)
            del resultado[candidato.identidad_key]
            fusion = {
                "survivor": survivor.identidad_key,
                "perdedor": candidato.identidad_key,
                "nombre_norm": nombre_norm,
            }
            if candidato.codigo and survivor.codigo != candidato.codigo:
                # the backfill only fills an EMPTY código: a loser's different one is lost
                fusion["codigo_perdido"] = candidato.codigo
            fusiones.append(fusion)
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
        enfasis=_txt(entrada.enfasis),
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
        ("enfasis", _txt(entrada.enfasis)),
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

    filas: list[tuple[str, str, str, dict]] = []
    for clave_roster, roster_entry in (roster_by_cedula or {}).items():
        if not isinstance(roster_entry, dict):
            continue
        identificacion = _txt(roster_entry.get("identificacion"))
        if not identificacion:
            continue  # no identity anchor at all -> cannot be represented
        filas.append((_cedula_key(identificacion) or identificacion, identificacion, str(clave_roster), roster_entry))
    # Canonical order (D29): the roster dict's own key breaks the last tie, so which of
    # two entries sharing a cédula is "first" never depends on the dict's order.
    filas.sort(key=lambda fila: fila[:3])
    for clave, identificacion, _clave_roster, roster_entry in filas:
        # Keyed by the normalized cédula exactly like `_perfil_desde_main` (C3): the
        # join key never depends on how the roster spelled it ("1.234.567",
        # "1234567.0"). The RAW roster text is exported in `cedulas_unificadas`
        # (once, never the row's own key) so any form the API/frontend carries maps
        # to the row.
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
            revision.append(_revision(
                "main_sin_cedula",
                cedula_key=_txt(entrada.cedula_key),
                nombre_completo=_nombre_de_entrada(entrada),
                id=_txt(entrada.id),
            ))
            continue
        primera = vistos_main.get(clave)
        if primera is not None:
            nombre_a, nombre_b = _nombre_de_entrada(primera), _nombre_de_entrada(entrada)
            revision.append(_revision(
                "cedula_duplicada_main",
                cedula_key=clave,
                nombre_completo=nombre_a,
                nombre_completo_duplicado=nombre_b,
                id=_txt(primera.id),
                id_duplicado=_txt(entrada.id),
                mismo_nombre=bool(nombre_a) and normalizar_nombre(nombre_a) == normalizar_nombre(nombre_b),
            ))
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


def _aplicar_vercel(perfil: Perfil, entrada: EntradaReferencia, por_cedula: bool) -> None:
    """The ONE Vercel overlay of a profile (shared by `superponer_referencia` and the
    post-fix re-overlay). `en_vercel` is only ever set to True, never back to False.
    `np_vercel` and `entidad_vercel` are BOTH first-non-empty-wins: a later call fills
    them only while they are empty, so a re-overlay can never wipe (or replace) a
    non-empty `np_vercel` with an empty (or different) one (D-FIXOVERLAY, notebook 8.1
    re-resolves only `entidad`; the caller clears `entidad_vercel` first to re-resolve
    it). `entidad_vercel` is additionally taken ONLY from a match BY CÉDULA (D12)."""
    perfil.en_vercel = True
    if not perfil.np_vercel:
        perfil.np_vercel = _txt(entrada.np)
    if por_cedula and not perfil.entidad_vercel:
        perfil.entidad_vercel = _txt(entrada.entidad)


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
            _aplicar_vercel(perfil, entrada, por_cedula)

        entrada, _ = fase2.buscar(cedula_k, perfil.nombre_norm)
        if entrada is not None:
            perfil.en_fase2 = True
            perfil.np_fase2 = _txt(entrada.np)
            perfil.cedula_fase2 = _cedula_key(entrada.cedula_key)

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
        ordenados = sorted(perfiles.values(), key=lambda p: p.identidad_key)
        for perfil in ordenados:
            clave = _cedula_key(perfil.identificacion)
            if clave:
                self.exacto.setdefault(clave, perfil)
        for perfil in ordenados:
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
    # The index stays static while the stickers are read (registering a form the
    # stripped fallback already resolves changes no later lookup), so the outcome
    # cannot depend on the sticker order; the forms are exported afterwards, sorted.
    nuevos: dict[str, tuple[Perfil, set[str]]] = {}
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
            nuevos.setdefault(perfil.identidad_key, (perfil, set()))[1].add(clave)
        perfil.n_stickers += 1
        fecha = _parse_fecha(sticker.get("fecha_creacion"))
        if fecha is not None:
            fecha_solo = fecha.date()
            if perfil.ultimo_sticker is None or fecha_solo > perfil.ultimo_sticker:
                perfil.ultimo_sticker = fecha_solo
        if _sticker_es_valido(sticker):
            perfil.tiene_sticker_valido = True
    for identidad_key in sorted(nuevos):
        perfil, claves = nuevos[identidad_key]
        for clave in sorted(claves):
            indice.registrar(perfil, clave)


def corregir_cedula_fase2(
    perfiles: dict[str, Perfil], referencia: ReferenciaBundle
) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """D18, the notebook's "Fase 2 cédula overwrites main's": a profile that matched
    Fase 2 by exact `nombre_norm` ONLY (its cédula did not match) adopts the Fase 2
    cédula as its `identidad_key`/`identificacion`. Everything else follows from
    keeping the join contract intact:

    - `cedula_sospechosa` is NOT recomputed: it stays as computed from the ORIGINAL
      cédula, in both directions (recomputing would clear the flag on exactly the
      rows that most need review, and move collapse membership off parity).
    - The OLD key is exported once in `cedulas_unificadas` (never the row's own key)
      so a raw sticker still carrying it — or its zero-padded forms already
      registered by the attribution — keeps resolving to the row on the frontend.
    - A match BY cédula, a Fase 2 row without a usable cédula, or a cédula that is
      already the profile's own key change nothing.
    - No silent capture: when the Fase 2 cédula is already answered to (own key or
      alias) by ANOTHER profile, or two profiles want the same one, nobody moves and
      each such profile is reported (`fase2_cedula_colision`). Decisions read the
      keys as they were BEFORE the pass, so the outcome is independent of order.

    - A profile whose key WAS rewritten re-runs the Vercel overlay by its NEW cédula
      (exact key only, never by name; `_aplicar_vercel`): `entidad_vercel` is re-resolved
      from the corrected cédula (notebook 8.1 resolves `entidad` on the corrected
      `identificacion`), while `en_vercel`/`np_vercel` are first-non-empty-wins: a
      non-empty pre-fix `np_vercel` is never wiped or replaced, only filled when empty
      (W-1). No Vercel row for the new cédula
      leaves the profile exactly as the pre-fix overlay left it; a blocked fix
      re-overlays nothing. `cedula_original` records the replaced key for the remap's
      same-person check.

    Runs AFTER the sticker attribution (aggregates were counted under the old key;
    they stay on the profile object) and BEFORE the remap and the D-P2 unification
    (the remap then finds the owner by the corrected cédula)."""
    fase2 = _IndiceReferencia(referencia.fase2)
    ordenados = sorted(perfiles.values(), key=lambda p: p.identidad_key)

    reclamos: dict[str, set[str]] = {}
    for perfil in ordenados:
        for clave in (_cedula_key(perfil.identificacion), *map(_cedula_key, perfil.cedulas_unificadas)):
            if clave:
                reclamos.setdefault(clave, set()).add(perfil.identidad_key)

    objetivos: dict[str, str] = {}
    for perfil in ordenados:
        propia = _cedula_key(perfil.identificacion)
        entrada, por_cedula = fase2.buscar(propia, perfil.nombre_norm)
        if entrada is None or por_cedula:
            continue
        nueva = _cedula_key(entrada.cedula_key)
        if nueva and nueva != propia:
            objetivos[perfil.identidad_key] = nueva
    pretendientes = Counter(objetivos.values())

    revision: list[dict] = []
    nuevas_claves: dict[str, str] = {}
    vercel: _IndiceReferencia | None = None  # built lazily: most bundles rewrite nothing
    for perfil in ordenados:
        nueva = objetivos.get(perfil.identidad_key)
        if nueva is None:
            continue
        duenios = sorted(reclamos.get(nueva, set()) - {perfil.identidad_key})
        if duenios or pretendientes[nueva] > 1:
            revision.append(_revision(
                "fase2_cedula_colision",
                cedula_key=nueva,
                identidad_key=perfil.identidad_key,
                identidad_key_existente=duenios[0] if duenios else "",
                nombre_completo=perfil.nombre_completo,
            ))
            continue
        vieja = perfil.identidad_key
        vieja_normalizada = _cedula_key(vieja) or vieja
        alias = [a for a in perfil.cedulas_unificadas if _cedula_key(a) != nueva]
        if vieja_normalizada not in {_cedula_key(a) or a for a in alias}:
            alias.append(vieja)
        perfil.identidad_key = perfil.identificacion = nueva
        perfil.cedulas_unificadas = tuple(alias)
        perfil.cedula_original = vieja_normalizada
        nuevas_claves[vieja] = nueva
        if vercel is None:
            vercel = _IndiceReferencia(referencia.vercel)
        entrada_vercel = vercel.por_cedula.get(nueva)
        if entrada_vercel is not None:
            perfil.entidad_vercel = ""  # the corrected cédula is authoritative (D12, notebook 8.1)
            _aplicar_vercel(perfil, entrada_vercel, True)
    return {nuevas_claves.get(clave, clave): perfil for clave, perfil in perfiles.items()}, tuple(revision)


def fusionar_identidad(
    stickers: list[dict],
    roster_by_cedula: dict[str, dict],
    referencia: ReferenciaBundle,
) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """Stage 1: universe (Firestore roster ∪ `referencia.main`, D9) + Vercel /
    Fase 2 / `main` overlays + priority heuristics + sticker attribution (D11) +
    the Fase 2 cédula fix (D18). Returns `(perfiles, revision_manual)`. It does NOT
    unify name duplicates: D-P2 runs later (`unificar_duplicados`, D10) so its
    survivor score is evaluated on populated flags."""
    perfiles, revision = construir_universo(roster_by_cedula, referencia)
    superponer_referencia(perfiles, referencia)
    atribuir_stickers(perfiles, stickers)
    perfiles, revision_fix = corregir_cedula_fase2(perfiles, referencia)
    return perfiles, (*revision, *revision_fix)


# ── Stage 2: remapear_codigos ────────────────────────────────────────────────


def _misma_persona(perfil: Perfil, clave_vercel: str, nombre_vercel: str) -> bool:
    """D-MISMAPERSONA — the notebook's 4.d `misma_persona`, verbatim: the current
    holder IS the person the Vercel entry names when the Vercel cédula key equals its
    own cédula key (or the one it had before the D18 fix), or its matched Fase 2
    cédula, or `token_sort_ratio(nombre_norm, nombre_vercel) >= 90` (inclusive, the
    same scorer/cutoff as the fuzzy scan). An empty cédula or an empty name never
    matches anything: rapidfuzz scores two empty strings as 100."""
    if clave_vercel and clave_vercel in (
        _cedula_key(perfil.identificacion), perfil.cedula_original, perfil.cedula_fase2,
    ):
        return True
    if perfil.nombre_norm and nombre_vercel:
        return fuzz.token_sort_ratio(perfil.nombre_norm, nombre_vercel) >= FUZZY_REMAP_THRESHOLD
    return False


def _rango_conservado(
    perfil: Perfil, clave_vercel: str, nombre_vercel: str, titular: Perfil | None
) -> tuple:
    """D-MISMOSTITULARES: which of 2+ same-person holders keeps the código (`min` wins).
    (1) the cédula owner by its OWN key or its pre-fix key, (2) the profile the owner
    lookup resolved (`titular`, which includes a unique Fase 2 owner), (3) the highest
    `token_sort_ratio` against the Vercel name (0 when either name is empty), (4) the
    lowest `identidad_key` as the TERMINAL total order (D29)."""
    directa = bool(clave_vercel) and clave_vercel in (_cedula_key(perfil.identificacion), perfil.cedula_original)
    es_titular = titular is not None and perfil.identidad_key == titular.identidad_key
    puntaje = (
        fuzz.token_sort_ratio(perfil.nombre_norm, nombre_vercel) if perfil.nombre_norm and nombre_vercel else 0.0
    )
    return (not directa, not es_titular, -puntaje, perfil.identidad_key)


def remapear_codigos(
    perfiles: dict[str, Perfil], referencia: ReferenciaBundle
) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """Cédula match auto-remaps (D1: attribute to the CURRENT titular) and, since
    D13, CLEARS the código from every current holder that is a DIFFERENT person than
    the Vercel owner, so a código is single-valued by construction. A holder who IS
    that person (`_misma_persona`, D-MISMAPERSONA, notebook parity) is never a wrong
    holder: it keeps the código and, as in the notebook, the pair is left alone (no
    assignment to another profile, no `remap_sin_duenio`, no candidate). Fuzzy
    `token_sort_ratio >= 90` only SURFACES a candidate for manual review — NEVER
    auto-assigns (spec: "Fuzzy match never merges automatically"). D-P1: a `codigo`
    shared by 2+ Vercel entries (or already listed in `referencia.codigos_duplicados`)
    is excluded from remap entirely (nothing assigned, nobody cleared) and listed for
    manual review.

    The owner is the profile whose own cédula key is the Vercel cédula, else the one
    whose matched Fase 2 cédula is (W3, notebook `cedula_idx`); a Fase 2 cédula shared
    by 2+ profiles is ambiguous: nothing is assigned or cleared and
    `remap_owner_ambiguo` lists the profiles. When 2+ current holders are the same
    person (D-MISMOSTITULARES) exactly ONE keeps the código (`_rango_conservado`) and
    each other one is cleared and reported as `remap_mismos_titulares`, but ONLY when the
    keeper surely ends up holding it (round 3, INV-5): a keeper emptied by its own
    `remap_conflicto`, or given another código by a claim, leaves the others in place
    (nothing is cleared or reported for them; the conflict item names the código). Review motivos:
    `remap_sin_duenio` (one
    per cleared holder: the Vercel owner has no profile), `codigo_reemplazado` (D-REMAP:
    the owner held a DIFFERENT código before the pass and Vercel's, which NOBODY held,
    replaces it; the behavior is kept, the divergence from the notebook is surfaced), `remap_conflicto` (one per
    código: ONE profile is claimed by two DIFFERENT Vercel códigos, it is left empty;
    a same-código pair is D-P1) and `remap_owner_ambiguo`. Every decision is taken
    over the holdings as they were BEFORE the pass, so a swap or a rotation (A holds
    B's code, B holds A's) resolves to the Vercel owners instead of wiping both.
    Profiles are visited in `identidad_key` order (D29): the outcome never depends on
    the roster order. Codes are compared as exact trimmed text — "021" and "21" are
    different códigos; a whitespace-only Vercel código is no código (D-CODIGOTRIM).

    `codigo_vercel_duplicado` (D-P1) is emitted for every excluded código that Vercel repeats
    or that 2+ profiles hold (a pre-listed one too, W-4); with 2+ holders it lists them in
    `identidad_keys_titulares`, as does `remap_owner_ambiguo` (its holders keep the código:
    no evidence to clear them). The items name the keys AS OF THIS STAGE; `depurar` rewrites
    them to the keys that survive the unification (`_resolver_claves_revision`)."""
    revision_manual: list[dict] = []
    excluidos = {codigo for codigo in map(_txt, referencia.codigos_duplicados) if codigo}

    vercel: list[tuple[str, EntradaReferencia]] = []
    por_codigo: dict[str, int] = {}
    for entrada in referencia.vercel:
        codigo = _txt(entrada.codigo)  # D-CODIGOTRIM: a whitespace-only código is no código
        if codigo:
            vercel.append((codigo, entrada))
            por_codigo[codigo] = por_codigo.get(codigo, 0) + 1
    repetidos = {codigo for codigo, cantidad in por_codigo.items() if cantidad >= 2}
    excluidos |= repetidos

    ordenados = sorted(perfiles.values(), key=lambda p: p.identidad_key)
    propios: dict[str, Perfil] = {}
    por_fase2: dict[str, list[Perfil]] = {}
    for perfil in ordenados:
        clave = _cedula_key(perfil.identificacion)
        if clave:
            propios.setdefault(clave, perfil)
        if perfil.cedula_fase2 and perfil.cedula_fase2 != clave:
            por_fase2.setdefault(perfil.cedula_fase2, []).append(perfil)
    titulares_previos: dict[str, list[Perfil]] = {}
    for perfil in ordenados:
        if perfil.codigo:
            titulares_previos.setdefault(perfil.codigo, []).append(perfil)
    # D-P1 / W-4: an excluded código is reported when Vercel repeats it OR when it matters
    # (2+ profiles hold it, whether the bundle pre-listed it or not); the holders ride along
    # only when there are 2+, so the plain "Vercel repeats a código nobody holds" item keeps
    # its exact shape.
    for codigo in sorted(excluidos):
        titulares = titulares_previos.get(codigo, ())
        if codigo not in repetidos and len(titulares) < 2:
            continue
        holders = {"identidad_keys_titulares": [h.identidad_key for h in titulares]} if len(titulares) >= 2 else {}
        revision_manual.append(_revision("codigo_vercel_duplicado", codigo=codigo, **holders))

    def duenio_de(clave: str) -> tuple[Perfil | None, tuple[str, ...]]:
        """`(owner, ambiguous keys)`: an own cédula beats another profile's Fase 2 claim."""
        if not clave:
            return None, ()
        propio = propios.get(clave)
        if propio is not None:
            return propio, ()
        candidatos = por_fase2.get(clave, ())
        if len(candidatos) == 1:
            return candidatos[0], ()
        return None, tuple(p.identidad_key for p in candidatos)  # already in key order

    a_limpiar: dict[str, Perfil] = {}
    reclamos: dict[str, tuple[Perfil, set[str]]] = {}
    # (código, keeper, the other same-person holders): decided AFTER every claim is known,
    # because the keeper may still lose the código to its own conflict / another claim.
    pendientes_mismos: list[tuple[str, Perfil, list[Perfil]]] = []
    sin_titular: list[tuple[str, EntradaReferencia]] = []
    for codigo, entrada in vercel:
        if codigo in excluidos:
            continue
        clave_vercel = _cedula_key(entrada.cedula_key)
        nombre_vercel = _nombre_norm_de_entrada(entrada)
        titular, ambiguos = duenio_de(clave_vercel)
        titulares = titulares_previos.get(codigo, ())
        mismos = {h.identidad_key for h in titulares if _misma_persona(h, clave_vercel, nombre_vercel)}
        equivocados = [h for h in titulares if h.identidad_key not in mismos]
        if mismos:
            for perfil in equivocados:
                a_limpiar[perfil.identidad_key] = perfil
            mismos_perfiles = [h for h in titulares if h.identidad_key in mismos]
            conservado = min(mismos_perfiles, key=lambda h: _rango_conservado(h, clave_vercel, nombre_vercel, titular))
            otros = [perfil for perfil in mismos_perfiles if perfil is not conservado]
            if otros:  # the same person twice: a código is single-valued (decided below)
                pendientes_mismos.append((codigo, conservado, otros))
            if titular is not None and titular.identidad_key == conservado.identidad_key:
                # already the owner's: keep the claim so a person registered twice in
                # Vercel is still a conflict
                reclamos.setdefault(titular.identidad_key, (titular, set()))[1].add(codigo)
            continue
        if ambiguos:
            revision_manual.append(_revision(
                "remap_owner_ambiguo", codigo=codigo, cedula_key=clave_vercel,
                identidad_keys=list(ambiguos),
                # nobody is cleared (no evidence which candidate is the owner): the holders
                # keep the código, so they are listed (INV-1: no silent multi-holder)
                identidad_keys_titulares=[h.identidad_key for h in titulares],
            ))
            continue
        for perfil in equivocados:
            a_limpiar[perfil.identidad_key] = perfil
        if titular is not None:
            reclamos.setdefault(titular.identidad_key, (titular, set()))[1].add(codigo)
            continue
        for perfil in equivocados:
            revision_manual.append(_revision("remap_sin_duenio", codigo=codigo, identidad_key=perfil.identidad_key))
        sin_titular.append((codigo, entrada))

    # What each claimed profile will hold: its one código, or "" when two DIFFERENT Vercel
    # códigos claim it (remap_conflicto). Computed before any mutation.
    nuevos_codigos = {
        titular.identidad_key: next(iter(codigos)) if len(codigos) == 1 else ""
        for titular, codigos in reclamos.values()
    }
    for codigo, conservado, otros in pendientes_mismos:
        # INV-5: the other same-person holders lose the código ONLY when the keeper surely ends
        # up holding it. A keeper emptied by its own conflict (or re-assigned another código)
        # would leave the código with nobody; then they keep it and the conflict item reports it.
        if nuevos_codigos.get(conservado.identidad_key, codigo) != codigo:
            continue
        for perfil in otros:
            a_limpiar[perfil.identidad_key] = perfil
            revision_manual.append(_revision(
                "remap_mismos_titulares", codigo=codigo, identidad_key=perfil.identidad_key,
                identidad_key_conservado=conservado.identidad_key,
            ))

    anteriores = {p.identidad_key: _txt(p.codigo) for p in ordenados}  # BEFORE any clearing
    for perfil in a_limpiar.values():
        perfil.codigo = ""
    for titular, codigos in reclamos.values():
        nuevo = nuevos_codigos[titular.identidad_key]
        if nuevo:
            anterior = anteriores[titular.identidad_key]
            if anterior and anterior != nuevo and nuevo not in titulares_previos:
                # D-REMAP: nobody held the código, so the notebook's remap table (holders
                # only) has no row for it; the engine assigns it and strips the owner's
                # different one. Vercel still wins, but the loss is surfaced. A código that
                # DID have a holder (swap, rotation) is a notebook remap: nothing to report.
                revision_manual.append(_revision(
                    "codigo_reemplazado", identidad_key=titular.identidad_key,
                    codigo_anterior=anterior, codigo_nuevo=nuevo,
                ))
            titular.codigo = nuevo
            continue
        titular.codigo = ""
        for codigo in sorted(codigos):
            revision_manual.append(_revision("remap_conflicto", codigo=codigo, identidad_key=titular.identidad_key))

    # No cédula match -> fuzzy fallback, SURFACE only (D3). `extractOne` returns the
    # FIRST best candidate, and the list is in `identidad_key` order, so a tie is
    # resolved by the lowest key whatever the roster order was.
    candidatos = [p for p in ordenados if p.nombre_norm]
    nombres = [p.nombre_norm for p in candidatos]
    for codigo, entrada in sin_titular:
        nombre = _nombre_norm_de_entrada(entrada)
        if not nombre or not nombres:
            continue
        mejor = process.extractOne(
            nombre, nombres, scorer=fuzz.token_sort_ratio, processor=None,
            score_cutoff=FUZZY_REMAP_THRESHOLD,
        )
        if mejor is not None:
            _, score, indice = mejor
            revision_manual.append(_revision(
                "codigo_remap_candidato",
                codigo=codigo,
                nombre_vercel=nombre,
                identidad_key_candidato=candidatos[indice].identidad_key,
                score=round(score, 1),
            ))

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
    for key, perfil_original in sorted(perfiles.items()):
        perfil = copy.copy(perfil_original)  # every Perfil field is immutable
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
    detalle.sort(key=lambda d: (d["identificacion"], d["nombre_completo"]))
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
    first-wins on a name collision — the LOWEST `identidad_key` wins, whatever the
    dict order (D29) — (two different perfiles sharing the
    exact same `nombre_norm`). A collision is reported as a COUNT only: an
    `identidad_key` is now a cédula and `nombre_norm` a person's name, so
    neither may reach a log line at any level (spec: "Contact Fields Are
    Admin-Only And Never Logged In Clear")."""
    survey_norm = {normalizar_nombre(n) for n in (nombres_survey or [])}
    survey_norm.discard("")
    alias: dict[str, str] = {}
    colisiones = 0
    for key, perfil in sorted(perfiles.items()):
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
    return dict(sorted(alias.items()))


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
        "enfasis": perfil.enfasis,
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
        # Notebook parity shape (spec: "Aggregate row carries the parity shape").
        "np_fuente": "ninguno",
        "fase": "Fase I",
        "fase_np_faltante": True,
        "activo": False,
        "estado_sugerido": "grupo_externos_agrupado",
        "fuente_dato": f"grupo_agregado (main, {len(detalle)} registros colapsados)",
        "detalle": detalle,
    }


# Review motivos that name a código held by 2+ rows (INV-1).
_MOTIVOS_CODIGO_REPORTADO = frozenset({
    "codigo_vercel_duplicado", "remap_owner_ambiguo", "remap_conflicto",
    "remap_mismos_titulares", "codigo_duplicado_local",
})
# Item fields that name a profile key (a scalar or a list of them). `identidad_key_absorbido`
# is deliberately absent: it names a key that no longer has a row, resolved by the survivor's
# `cedulas_unificadas`.
_CAMPOS_CLAVE = ("identidad_key", "identidad_key_conservado", "identidad_key_candidato", "identidad_key_existente")
_CAMPOS_CLAVES = ("identidad_keys", "identidad_keys_titulares")


def _mapa_claves_finales(perfiles_previos: dict[str, Perfil], fusiones: Iterable[dict]) -> dict[str, str]:
    """Pipeline-time key -> the key that survives to the output: a key the D18 fix
    rewrote (`cedula_original` -> `identidad_key`) and a key the D-P2 unification absorbed
    (`perdedor` -> `survivor`), composed in pipeline order. A key that is already final is
    absent (it maps to itself)."""
    absorbidos = {f["perdedor"]: f["survivor"] for f in fusiones}
    mapa = {
        p.cedula_original: absorbidos.get(p.identidad_key, p.identidad_key)
        for p in perfiles_previos.values() if p.cedula_original
    }
    for perdedor, survivor in absorbidos.items():
        mapa.setdefault(perdedor, survivor)
    return mapa


def _resolver_clave_final(clave: str, mapa: dict[str, str]) -> str:
    return mapa.get(clave, clave)


def _resolver_claves_revision(items: Iterable[dict], mapa: dict[str, str]) -> list[dict]:
    """INV-2 in ONE place: every profile key a review item names is rewritten to the key
    that survives to the output (`_resolver_clave_final`), so a consumer never looks up a key
    the D-P2 unification or the D18 fix already retired. Lists stay sorted and duplicate-free.
    The items are copied, never mutated."""
    if not mapa:
        return [dict(item) for item in items]
    resueltos = []
    for item in items:
        nuevo = dict(item)
        for campo in _CAMPOS_CLAVE:
            if isinstance(nuevo.get(campo), str):
                nuevo[campo] = _resolver_clave_final(nuevo[campo], mapa)
        for campo in _CAMPOS_CLAVES:
            if isinstance(nuevo.get(campo), list):
                nuevo[campo] = sorted({_resolver_clave_final(c, mapa) for c in nuevo[campo]})
        resueltos.append(nuevo)
    return resueltos


def _codigos_perdidos_unificacion(fusiones: Iterable[dict]) -> list[dict]:
    """A código the D-P2 backfill could not keep (the survivor already held a different
    one) is reported, never lost silently."""
    return [
        _revision(
            "codigo_perdido_unificacion", codigo=f["codigo_perdido"],
            identidad_key=f["survivor"], identidad_key_absorbido=f["perdedor"],
        )
        for f in fusiones if f.get("codigo_perdido")
    ]


def _codigos_duplicados_locales(perfiles: dict[str, Perfil], items: Iterable[dict]) -> list[dict]:
    """D-DUPLOCAL (INV-1): a código held by 2+ FINAL rows that no other item names (Vercel
    never mentioned it, so D13 has no evidence to clear it) is reported with its holders,
    never cleared."""
    cubiertos = {_txt(i.get("codigo")) for i in items if i["motivo"] in _MOTIVOS_CODIGO_REPORTADO}
    titulares: dict[str, list[str]] = {}
    for key, perfil in sorted(perfiles.items()):
        if perfil.codigo:
            titulares.setdefault(perfil.codigo, []).append(key)
    return [
        _revision("codigo_duplicado_local", codigo=codigo, identidad_keys=claves)
        for codigo, claves in sorted(titulares.items())
        if len(claves) >= 2 and codigo not in cubiertos
    ]


def _canonizar_revision(items: Iterable[dict]) -> tuple[dict, ...]:
    """D29 canonical `revision_manual`: sorted by `(motivo, codigo, identidad_key)`
    with the item's own canonical JSON as the last tie-break (a total order).
    Indistinguishable items are NEVER silently dropped (three identical duplicate
    `main` rows are three facts): they collapse into ONE item carrying
    `n_ocurrencias` (>= 2 only when it repeats; a singleton keeps its exact shape).
    Counting is idempotent and composable: an item that already carries the counter
    adds it instead of counting once. The input items are never mutated."""
    def serializado(item: dict) -> str:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)

    def repeticiones(item: dict) -> int:
        n = item.get("n_ocurrencias")
        return n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else 1

    grupos: dict[str, tuple[dict, int]] = {}
    for item in items:
        base = {k: v for k, v in item.items() if k != "n_ocurrencias"}
        clave = serializado(base)
        previo = grupos.get(clave)
        grupos[clave] = (base, (previo[1] if previo else 0) + repeticiones(item))

    canonicos: list[tuple[tuple, dict]] = []
    for clave, (base, cuenta) in grupos.items():
        item = {**base, "n_ocurrencias": cuenta} if cuenta >= 2 else base
        canonicos.append(((base["motivo"], _txt(base.get("codigo")), _txt(base.get("identidad_key")), clave), item))
    canonicos.sort(key=lambda par: par[0])
    return tuple(item for _, item in canonicos)


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
    API (spec: "Classification never writes to the inspector record").

    Pure and deterministic (D29): no input is mutated, nothing returned aliases an
    input (every dict/list is built here), no clock/environment/module state is read
    (`hoy` is the only date source), and the result does not depend on the roster
    or sticker ORDER. `referencia` row order IS input (D19 first-wins). Every
    emitted list is canonical: `inspectores` by `identidad_key`, `revision_manual`
    by `(motivo, codigo, identidad_key)` (identical items collapse into one carrying
    `n_ocurrencias`), `grupo_externos.detalle` by `identificacion`, `alias_nombres`
    by name."""
    perfiles, revision_universo = fusionar_identidad(stickers, roster_by_cedula, referencia)
    perfiles_previos = perfiles  # keys as the D18 fix left them: what the remap items name
    perfiles, revision_remap = remapear_codigos(perfiles, referencia)
    perfiles, fusiones = unificar_duplicados(perfiles)
    alias = alias_nombres(perfiles, nombres_survey)
    perfiles, detalle_externos = colapsar_externos(perfiles, hoy, exentos=frozenset(alias.values()))

    for perfil in perfiles.values():
        perfil.entidad = resolver_entidad(perfil)
        perfil.np, perfil.np_fuente = resolver_np(perfil)
        perfil.fase, perfil.fase_np_faltante = calcular_fase(perfil.np)
        perfil.fuente_dato = fuente_dato(perfil)
        perfil.estado_sugerido = estado_sugerido(perfil)

    inspectores = tuple(_perfil_a_dict(p) for _, p in sorted(perfiles.items()))
    grupo_externos = _build_grupo_externos(detalle_externos) if detalle_externos else None

    # Review items: resolved to the keys that survive (INV-2), then the two items only the
    # END of the pipeline can know (a código lost in the unification, D-DUPLOCAL: INV-1).
    revision = _resolver_claves_revision(
        (*revision_universo, *revision_remap), _mapa_claves_finales(perfiles_previos, fusiones)
    )
    revision += _codigos_perdidos_unificacion(fusiones)
    revision += _codigos_duplicados_locales(perfiles, revision)

    return Depuracion(
        activa=referencia.activa,
        motivo=referencia.motivo,
        referencia_generada_en=referencia.generado_en,
        inspectores=inspectores,
        grupo_externos=grupo_externos,
        alias_nombres=alias,
        revision_manual=_canonizar_revision(revision),
    )
