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
            "identificacion": str,     # Firestore identity anchor (spec:
                                        # "Identity Anchor Is Firestore
                                        # identificacion") — MUST match a
                                        # `roster_by_cedula` entry's own
                                        # `identificacion` to be attributed;
                                        # unattributable stickers are
                                        # silently skipped, never raised.
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

`roster_by_cedula: dict[str, dict]` — Firestore `inspector_profiles()`,
keyed by the SAME digits-only `cedula_key` join key
`stickers_atencionsismo.cedula_key()` already uses. Each value:

    {
        "identificacion": str,   # identity anchor (spec) — REQUIRED to be
                                  # represented at all; blank -> skipped.
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

`referencia: ReferenciaBundle` — `app.services.inspectores_referencia`
(Phase 1). Note: Phase 1's `EntradaReferencia` does not carry `correo`/
`tarjeta_profesional` (design's bundle JSON example shows them on `main`
rows, but `parse_bundle` never parses them) — a documented Phase 1/design
gap, out of scope here; this module only reads `EntradaReferencia`'s actual
fields (`cedula_key`, `nombre_norm`, `np`, `entidad`, `codigo`, `no_persona`).

`hoy: date` — Bogotá "today" (design D6: cache-key input, not computed here).
"""
from __future__ import annotations

import copy
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable

from rapidfuzz import fuzz

from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle

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


def cedula_sospechosa(cedula: object) -> bool:
    """explore.md Q4 — digit-only length outside 6-10, or exactly 10 digits
    not starting with "1". Also a PRIORITIZATION-only heuristic."""
    digits = re.sub(r"\D", "", str(cedula or ""))
    if not digits:
        return True
    if not (6 <= len(digits) <= 10):
        return True
    return len(digits) == 10 and not digits.startswith("1")


def _cedula_key(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


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
    entidad: str = ""
    tarjeta_profesional: str = ""
    num_telefono: str = ""
    correo_contacto: str = ""
    creado_en: str = ""
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
    revision_manual: tuple[dict, ...]


# ── Survivor scoring, shared by stage 1's D-P2 fix and stage 3 ─────────────


def _survivor_score(p: Perfil) -> tuple:
    """has_sticker > has_codigo > en_vercel > en_fase2 > not_no_persona >
    not_cedula_sospechosa (design's Interfaces/Contracts, stage 3 row)."""
    return (
        bool(p.ultimo_sticker),
        bool(p.codigo),
        p.en_vercel,
        p.en_fase2,
        not p.es_cuenta_no_persona,
        not p.cedula_sospechosa,
    )


def _elegir_survivor(candidatos: list[Perfil]) -> Perfil:
    """Tie -> oldest `creado_en`. A blank `creado_en` never wins a tie over
    a real date; if every tied candidate is blank, the first-seen
    (insertion order) wins deterministically."""
    max_score = max(_survivor_score(c) for c in candidatos)
    empatados = [c for c in candidatos if _survivor_score(c) == max_score]
    if len(empatados) == 1:
        return empatados[0]
    con_fecha = [c for c in empatados if c.creado_en]
    pool = con_fecha or empatados
    return min(pool, key=lambda c: (c.creado_en or "", candidatos.index(c)))


def _fusionar_en_survivor(survivor: Perfil, perdedor: Perfil) -> None:
    """Backfill = "primero no vacío gana", NOT a sum (explore.md §6.5/§8.1
    'B' — e.g. `n_stickers` is intentionally never summed, matching the
    notebook's documented silent-discard of the loser's own counts)."""
    for campo in ("correo", "codigo", "entidad", "tarjeta_profesional",
                  "num_telefono", "correo_contacto", "rango_main"):
        if not getattr(survivor, campo):
            setattr(survivor, campo, getattr(perdedor, campo))
    if not survivor.en_vercel and perdedor.en_vercel:
        survivor.en_vercel, survivor.np_vercel = True, perdedor.np_vercel
    if not survivor.en_fase2 and perdedor.en_fase2:
        survivor.en_fase2, survivor.np_fase2 = True, perdedor.np_fase2
    if not survivor.no_persona_ref and perdedor.no_persona_ref:
        survivor.no_persona_ref = True
    if perdedor.ultimo_sticker and (
        not survivor.ultimo_sticker or perdedor.ultimo_sticker > survivor.ultimo_sticker
    ):
        survivor.ultimo_sticker = perdedor.ultimo_sticker
    survivor.tiene_sticker_valido = survivor.tiene_sticker_valido or perdedor.tiene_sticker_valido
    survivor.cedulas_unificadas = tuple(
        dict.fromkeys((*survivor.cedulas_unificadas, perdedor.identificacion))
    )


def _unificar_por_nombre(perfiles: dict[str, Perfil]) -> tuple[dict[str, Perfil], tuple[dict, ...]]:
    """Exact `nombre_norm` match only (spec: "Exact-name duplicate merge
    never deletes the losing record" — excluded from the OUTPUT dict, never
    mutated/removed from the CALLER's input `perfiles`: every merge works on
    a deep copy of the survivor, so `perfiles`'s own objects are untouched)."""
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


# ── Stage 1: fusionar_identidad ─────────────────────────────────────────────


def _buscar_entrada(
    entradas: tuple[EntradaReferencia, ...], cedula_key: str, nombre_norm: str
) -> EntradaReferencia | None:
    """Q1: cédula match first, `nombre_norm` fallback."""
    if cedula_key:
        for entrada in entradas:
            if entrada.cedula_key == cedula_key:
                return entrada
    if nombre_norm:
        for entrada in entradas:
            if entrada.nombre_norm == nombre_norm:
                return entrada
    return None


def _overlay_vercel(perfil: Perfil, entradas: tuple[EntradaReferencia, ...], cedula_k: str) -> None:
    entrada = _buscar_entrada(entradas, cedula_k, perfil.nombre_norm)
    if entrada is None:
        return
    perfil.en_vercel = True
    perfil.np_vercel = entrada.np
    if not perfil.entidad:
        perfil.entidad = entrada.entidad


def _overlay_fase2(perfil: Perfil, entradas: tuple[EntradaReferencia, ...], cedula_k: str) -> None:
    entrada = _buscar_entrada(entradas, cedula_k, perfil.nombre_norm)
    if entrada is None:
        return
    perfil.en_fase2 = True
    perfil.np_fase2 = entrada.np


def _overlay_main(perfil: Perfil, entradas: tuple[EntradaReferencia, ...], cedula_k: str) -> None:
    entrada = _buscar_entrada(entradas, cedula_k, perfil.nombre_norm)
    if entrada is None:
        return
    if not perfil.rango_main:
        perfil.rango_main = entrada.np
    if entrada.no_persona:
        perfil.no_persona_ref = True
    if not perfil.entidad:
        perfil.entidad = entrada.entidad


def fusionar_identidad(
    stickers: list[dict],
    roster_by_cedula: dict[str, dict],
    referencia: ReferenciaBundle,
) -> dict[str, Perfil]:
    """Stage 1 (design's Interfaces/Contracts table). Builds the initial
    identity index keyed by Firestore `identificacion` (spec: Identity
    Anchor), applies the D-P2 cédula-typo fix (unify only on exact
    `nombre_norm` match — `_unificar_por_nombre`, shared with stage 3),
    overlays the reference bundle (Q1), computes the two priority heuristics,
    then aggregates sticker activity per identity."""
    perfiles: dict[str, Perfil] = {}
    for roster_entry in (roster_by_cedula or {}).values():
        if not isinstance(roster_entry, dict):
            continue
        identificacion = str(roster_entry.get("identificacion") or "").strip()
        if not identificacion:
            continue  # no identity anchor at all -> cannot be represented
        if identificacion in perfiles:
            # Two `cedula_key` dict entries already collapse to the SAME
            # identificacion -> trivially one person; keep first-seen, note
            # a name discrepancy if the duplicate carries a different name.
            existente = perfiles[identificacion]
            nombre_dup = normalizar_nombre(roster_entry.get("nombre_completo"))
            if nombre_dup and nombre_dup != existente.nombre_norm:
                existente.cedulas_unificadas = tuple(
                    dict.fromkeys((*existente.cedulas_unificadas, identificacion))
                )
            continue
        nombre_completo = str(roster_entry.get("nombre_completo") or "")
        perfiles[identificacion] = Perfil(
            identidad_key=identificacion,
            identificacion=identificacion,
            nombre_completo=nombre_completo,
            nombre_norm=normalizar_nombre(nombre_completo),
            correo=str(roster_entry.get("correo") or ""),
            codigo=str(roster_entry.get("codigo") or "").strip(),
            entidad=str(roster_entry.get("entidad") or ""),
            tarjeta_profesional=str(roster_entry.get("tarjeta_profesional") or ""),
            num_telefono=str(roster_entry.get("num_telefono") or ""),
            correo_contacto=str(roster_entry.get("correo_contacto") or ""),
            creado_en=str(roster_entry.get("creado_en") or ""),
        )

    # D-P2 fix: different identificacion, EXACT same normalized name.
    perfiles, _ = _unificar_por_nombre(perfiles)

    for perfil in perfiles.values():
        cedula_k = _cedula_key(perfil.identificacion)
        _overlay_vercel(perfil, referencia.vercel, cedula_k)
        _overlay_fase2(perfil, referencia.fase2, cedula_k)
        _overlay_main(perfil, referencia.main, cedula_k)
        perfil.cedula_sospechosa = cedula_sospechosa(perfil.identificacion)
        perfil.es_cuenta_no_persona = perfil.no_persona_ref or es_cuenta_no_persona(
            perfil.correo, perfil.nombre_completo
        )

    for sticker in (stickers or []):
        if not isinstance(sticker, dict):
            continue
        inspector = sticker.get("inspector")
        if not isinstance(inspector, dict):
            continue
        identificacion = str(inspector.get("identificacion") or "").strip()
        perfil = perfiles.get(identificacion)
        if perfil is None:
            continue  # unattributable sticker -> skipped, never raised
        perfil.n_stickers += 1
        fecha = _parse_fecha(sticker.get("fecha_creacion"))
        if fecha is not None:
            fecha_solo = fecha.date()
            if perfil.ultimo_sticker is None or fecha_solo > perfil.ultimo_sticker:
                perfil.ultimo_sticker = fecha_solo
        if _sticker_es_valido(sticker):
            perfil.tiene_sticker_valido = True

    return perfiles


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

    perfiles_por_cedula = {_cedula_key(p.identificacion): p for p in perfiles.values() if p.identificacion}

    for entrada in referencia.vercel:
        if not entrada.codigo or entrada.codigo in excluidos:
            continue
        titular = perfiles_por_cedula.get(entrada.cedula_key) if entrada.cedula_key else None
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
    """General post-remap exact-name dedup pass — same survivor scoring as
    stage 1's D-P2 fix (`_unificar_por_nombre`), run again here since remap
    (stage 2) may reassign a `codigo` in a way that changes survivor
    scoring for a name-duplicate pair stage 1 already saw."""
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
    `no_persona` branch."""
    if perfil.es_cuenta_no_persona:
        return "no_persona"
    tiene_codigo = bool(perfil.codigo)
    en_referencia = perfil.en_vercel or perfil.en_fase2
    if not tiene_codigo and not en_referencia:
        return "candidato_desactivacion"
    if not tiene_codigo and en_referencia:
        return "revisar"
    if tiene_codigo:
        return "activo"
    return "revisar"  # unreachable given the branches above; kept for
    # literal fidelity to the spec's 5-step precedence list.


# ── Stage 6: alias_nombres ───────────────────────────────────────────────────


def alias_nombres(perfiles: dict[str, Perfil], nombres_survey: Iterable[str]) -> dict[str, str]:
    """Maps a `survey_cali` normalized name to the real person's
    `identidad_key` it corresponds to — spec: "a survey name that maps to a
    real person's key never lands in GRUPO-EXTERNOS". Deterministic
    first-wins on a name collision (two different perfiles sharing the
    exact same `nombre_norm`), with the discarded candidate logged, never
    silently dropped without a trace."""
    survey_norm = {normalizar_nombre(n) for n in (nombres_survey or [])}
    survey_norm.discard("")
    alias: dict[str, str] = {}
    for key, perfil in perfiles.items():
        if not perfil.nombre_norm or perfil.nombre_norm not in survey_norm:
            continue
        if perfil.nombre_norm in alias:
            logging.info(
                "inspectores_depuracion.alias_nombres: nombre_norm=%r ya mapeado a "
                "identidad_key=%r; se descarta el candidato identidad_key=%r (first-wins)",
                perfil.nombre_norm, alias[perfil.nombre_norm], key,
            )
            continue
        alias[perfil.nombre_norm] = key
    return alias


# ── Response assembly ────────────────────────────────────────────────────────


def _perfil_a_dict(perfil: Perfil) -> dict:
    return {
        "identidad_key": perfil.identidad_key,
        "nombre_completo": perfil.nombre_completo,
        "identificacion": perfil.identificacion,
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
    perfiles = fusionar_identidad(stickers, roster_by_cedula, referencia)
    perfiles, revision_remap = remapear_codigos(perfiles, referencia)
    perfiles, _fusiones = unificar_duplicados(perfiles)
    alias = alias_nombres(perfiles, nombres_survey)
    perfiles, detalle_externos = colapsar_externos(perfiles, hoy, exentos=frozenset(alias.values()))

    for perfil in perfiles.values():
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
        revision_manual=revision_remap,
    )
