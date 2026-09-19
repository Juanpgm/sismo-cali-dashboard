#!/usr/bin/env python3
"""Parity harness for `seguimiento-inspectores-depurado` (tasks.md Phase 12, PR 11).

Four independent checks over ONE snapshot of the inputs `depurar()` consumes
(`stickers`, `roster_by_cedula`, `nombres_survey`, `referencia`, `hoy`):

1. parity     `depurar()` vs the notebook's `outputs/inspectores_depurado_seguimiento.xlsx`
              (thresholds of design "Parity Acceptance Criteria", every documented
              divergence listed key by key, D20 register);
2. determinism the engine gives the same result for shuffled roster / sticker order (D29);
3. timing     best of >= 3 runs under 0.5 s, with the input sizes (design budgets);
4. ledger     Firestore documents streamed (E evaluaciones, I inspectores, S survey_cali),
              Atencion Sismo rows walked, Blob GET/PUT, and the projection of reads per
              continuously-open hour and per day through the REAL component caches with a
              fake clock, compared with the budget ratified under design O1.

Everything is a PURE function over already-loaded inputs (tests use synthetic fixtures);
only `--live` assembles the inputs from live READ-ONLY sources, exactly like the router
(`get_stickers_atencionsismo`). It never writes anywhere: the Firestore client is wrapped
in a counting proxy that exposes no write method, and any Blob PUT is refused.

Usage:
    python scripts/parity_inspectores_depurado.py --snapshot snapshot.json [--xlsx ref.xlsx]
    python scripts/parity_inspectores_depurado.py --live [--xlsx ref.xlsx]      # read-only

`--live` reads credentials from the environment ONLY (FIREBASE_SERVICE_ACCOUNT_JSON,
VISITADOS_API_PASS, BLOB_PRIVATE_TOKEN or BLOB_READ_WRITE_TOKEN); it never loads a .env file.
The report prints aggregates and keys masked to their last 3 digits: never a name, correo,
phone or full cedula. Full keys live only in the structures the functions return.

Exit codes: 0 every check passed, 1 a check failed, 2 the harness itself could not run
(missing/empty/unusable input, missing credentials): it never reports success on nothing.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "backend", REPO_ROOT / "deploy"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.services import blob_lkg, cedula_utils  # noqa: E402
from app.services import inspectores_depuracion as dep  # noqa: E402
from app.services import inspectores_referencia as referencia_svc  # noqa: E402

EXIT_OK, EXIT_CHECK_FAILED, EXIT_ERROR = 0, 1, 2

SHEET_PRINCIPAL = "Inspectores Depurado"
SHEET_EXTERNOS = "Externos Agrupados (detalle)"
GRUPO_ID = "GRUPO-EXTERNOS"
DEFAULT_XLSX = REPO_ROOT / "outputs" / "inspectores_depurado_seguimiento.xlsx"
REQUIRED_COLUMNS = (
    "id", "identificacion", "nombre_completo", "codigo", "entidad", "np", "np_fuente", "fase", "fase_np_faltante",
    "tarjeta_profesional", "num_telefono", "correo_contacto", "estado_sugerido", "fuente_dato", "tiene_sticker_valido",
)

THRESHOLD = 99.0  # design "Parity Acceptance Criteria"
CONTACT_COLUMNS = ("tarjeta_profesional", "num_telefono", "correo_contacto")
COMPARED = (
    "np", "np_fuente", "fase", "fase_np_faltante", "codigo", "fuente_dato", "entidad", "estado_sugerido",
    *CONTACT_COLUMNS, "nombre_completo", "tiene_sticker_valido",
)
# `nombre_completo` (compared through `normalizar_nombre`) and `tiene_sticker_valido` (the D7 column) carry no
# threshold in the acceptance table: they are enumerated but informational.
THRESHOLD_COLUMNS = frozenset(COMPARED) - {"nombre_completo", "tiene_sticker_valido"} | {"identificacion"}
SENSITIVE_COLUMNS = frozenset({*CONTACT_COLUMNS, "nombre_completo"})
BOOL_COLUMNS = frozenset({"fase_np_faltante", "tiene_sticker_valido"})

# Every documented divergence (design D20 register). Each one is an explicit entry of the register, an empty
# list being a valid, explicit result. `D-N-COLAPSADOS` is a dict, the rest are sorted lists of `identidad_key`.
REVISION_REGISTER = {
    "codigo_reemplazado": "D-REMAP",
    "remap_sin_duenio": "D-REMAP-TODOS",
    "remap_mismos_titulares": "D-MISMOSTITULARES",
    "remap_conflicto": "D-REMAPCONFLICTO",
    "remap_owner_ambiguo": "D-REMAP-OWNER-AMBIGUO",
    "codigo_perdido_unificacion": "D-CODIGOPERDIDO",
    "codigo_duplicado_local": "D-DUPLOCAL",
    "codigo_vercel_duplicado": "D-CODIGO-VERCEL-DUPLICADO",
    # admin-only review SIGNALS, not divergences from the notebook: listed by key, they explain nothing by themselves
    "fase2_cedula_colision": "D-FASE2-COLISION",
    "codigo_remap_candidato": "D-REMAP-CANDIDATO",
}
REVIEW_SIGNALS = frozenset({"D-FASE2-COLISION", "D-REMAP-CANDIDATO"})
# The engine rules that can explain a `codigo` mismatch, each REPLAYED (`notebook_codigo`): D-REMAP (a code nobody
# holds in main is assigned to its Vercel owner), D-REMAP-TODOS / D13 (every different-person holder is cleared, not
# only the recorded titular), D-MISMAPERSONA (an empty name never matches) and D-OWNERFASE2 (own cedula beats an
# earlier row's Fase 2 claim, a shared Fase 2 cedula is ambiguous). D-CODIGOPERDIDO is item-based.
CODIGO_RULES = ("D-REMAP", "D-REMAP-TODOS", "D-MISMAPERSONA", "D-OWNERFASE2")
CODIGO_EXPLAINED = (*CODIGO_RULES, "D-CODIGOPERDIDO")
REGISTER_NAMES = (
    "D7", "D7-SIN-EXPLICAR", *REVISION_REGISTER.values(), "D-EXENTOS", "D-TIEBREAK", "D-TIEBREAK-ROSTER",
    "D-SURVFECHA", "D-CEDDEC", "D-ENTIDADTRIM", "D-CODIGOTRIM", "D12", "D-MISMAPERSONA", "D-OWNERFASE2",
    "D-N-COLAPSADOS",
)


class HarnessError(Exception):
    """The harness cannot produce a trustworthy answer. `main` prints the message and exits non-zero."""


@dataclass(frozen=True)
class Inputs:
    """The five inputs of `depurar()`, already loaded."""

    stickers: list
    roster_by_cedula: dict
    nombres_survey: list
    referencia: Any
    hoy: date

    def run(self, depurar_fn: Callable[..., Any] | None = None) -> Any:
        fn = depurar_fn or dep.depurar  # resolved at call time so a test can substitute the engine
        return fn(stickers=self.stickers, roster_by_cedula=self.roster_by_cedula,
                  nombres_survey=self.nombres_survey, referencia=self.referencia, hoy=self.hoy)


@dataclass(frozen=True)
class ReferenceXlsx:
    rows: list  # "Inspectores Depurado", group row included
    externos: list  # "Externos Agrupados (detalle)" ([] when the sheet is absent)
    n_colapsados: int | None  # the notebook's figure (externos sheet, else the group row's text)


# ── keys, cells, thresholds ─────────────────────────────────────────────────

def mask_key(key: object) -> str:
    """`***123`: the last 3 digits, the only form of a cedula the printed report may carry."""
    digits = re.sub(r"\D", "", str(key or ""))
    return "***" + digits[-3:] if len(digits) >= 3 else "***"


def normalize_key(value: object) -> str:
    """The engine's own identity key (digits only, a lone `.0` float tail dropped, leading zeros KEPT: "0012345"
    and "12345" are two cedulas, so a zero-padded key is reported as unmatched, never silently joined)."""
    return dep._cedula_key(value)


def meets_threshold(matches: int, total: int, threshold: float = THRESHOLD) -> bool:
    """Exact (no float rounding): 990/1000 meets 99.0, 989/1000 does not. Never true on an empty denominator."""
    return total > 0 and Fraction(matches, total) * 100 >= Fraction(str(threshold))


def _cell(value: object) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return "" if text.strip().lower() in ("nan", "none", "<na>", "nat") else text


def _as_bool(value: object) -> bool:
    return _cell(value).strip().lower() in ("true", "1", "1.0", "yes", "si")


def _zfill3(text: str) -> str:
    text = text.strip()
    return text.zfill(3) if text.isdigit() else text


def _compare_cell(column: str, backend: object, reference: object) -> tuple[bool, str | None]:
    """`(equal, register tag)`. The tag names a documented, purely-textual divergence (D-ENTIDADTRIM,
    D-CODIGOTRIM) so it is listed by key instead of being absorbed by normalization."""
    b, x = _cell(backend), _cell(reference)
    if column in BOOL_COLUMNS:
        return _as_bool(b) == _as_bool(x), None
    if column == "nombre_completo":
        return dep.normalizar_nombre(b) == dep.normalizar_nombre(x), None
    if column == "num_telefono":
        return re.sub(r"\D", "", b) == re.sub(r"\D", "", x), None
    if column == "correo_contacto":
        return b.strip().lower() == x.strip().lower(), None
    if column in ("np", "entidad", "codigo"):
        if b == x:
            return True, None
        if b.strip() == x.strip():
            return False, "D-ENTIDADTRIM" if column != "codigo" else "D-CODIGOTRIM"
        return (column == "codigo" and _zfill3(b) == _zfill3(x)), None  # padding-only: the 3-digit form is the same código
    return b.strip() == x.strip(), None


def _sanitize(column: str, value: object) -> str:
    """What a mismatch stores: the cell, except for PII columns where only its presence survives."""
    if column in SENSITIVE_COLUMNS:
        return "<set>" if _cell(value).strip() else "<blank>"
    return _cell(value)


def _item_keys(item: dict) -> list[str]:
    keys: list[str] = []
    for field, value in item.items():
        if field.startswith("identidad_key"):
            keys.extend(str(v) for v in (value if isinstance(value, (list, tuple)) else [value]) if v)
    return keys


# ── reference xlsx ──────────────────────────────────────────────────────────

_N_COLAPSADOS_RE = re.compile(r"\((?:[^,)]*,\s*)?(\d+)\s+registros colapsados\)")


def load_reference_xlsx(path: str | os.PathLike) -> ReferenceXlsx:
    """Fails loudly (HarnessError) on a missing, unreadable, empty or wrong-shaped file: an absent reference must
    never look like a 100% match."""
    path = Path(path)
    if not path.is_file():
        raise HarnessError(f"reference xlsx not found: {path}")
    import pandas as pd

    try:
        book = pd.ExcelFile(path)
    except Exception as exc:  # noqa: BLE001 - type only: a parser message can quote cell content
        raise HarnessError(f"cannot read reference xlsx {path.name} ({type(exc).__name__})") from None
    if SHEET_PRINCIPAL not in book.sheet_names:
        raise HarnessError(f"reference xlsx has no sheet {SHEET_PRINCIPAL!r} (sheets: {book.sheet_names})")
    frame = book.parse(SHEET_PRINCIPAL, dtype=str).fillna("")
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise HarnessError(f"reference xlsx sheet {SHEET_PRINCIPAL!r} is missing columns: {missing}")
    if frame.empty:
        raise HarnessError(f"reference xlsx sheet {SHEET_PRINCIPAL!r} has no rows")
    rows = frame.to_dict("records")
    externos: list = []
    n_colapsados: int | None = None
    if SHEET_EXTERNOS in book.sheet_names:
        externos_frame = book.parse(SHEET_EXTERNOS, dtype=str).fillna("")
        if "identificacion" not in externos_frame.columns:
            raise HarnessError(f"reference xlsx sheet {SHEET_EXTERNOS!r} is missing columns: ['identificacion']")
        externos = externos_frame.to_dict("records")
        n_colapsados = len(externos)
    else:
        for row in rows:
            match = _N_COLAPSADOS_RE.search(_cell(row.get("fuente_dato"))) if _cell(row.get("id")) == GRUPO_ID else None
            if match:
                n_colapsados = int(match.group(1))
    return ReferenceXlsx(rows=rows, externos=externos, n_colapsados=n_colapsados)


# ── the register (design D20) ───────────────────────────────────────────────

def _collapse_candidate(row: dict) -> bool:
    """`colapsar_externos`'s predicate, read back from the emitted row."""
    dias = row.get("dias_inactivo")
    return bool(
        (row.get("cedula_sospechosa") or row.get("no_persona"))
        and (row.get("ultimo_sticker") is None or (dias is not None and dias > 7))
        and not row.get("codigo")
    )


def notebook_estado(*, no_persona: bool, sticker: bool, referencia: bool, pre_codigo: bool, post_codigo: bool,
                    tiene_sticker_valido: bool) -> str:
    """The notebook's `estado_sugerido` rule (8.1, step 8), replayed. Its `desactivar` / `revision` id sets are computed
    BEFORE the remap from the row's own ORIGINAL codigo (`pre_codigo`); the trailing `activo` test reads the codigo AFTER
    the remap (`post_codigo`) OR `tiene_sticker_valido`. The engine differs on purpose in exactly two places (D-REMAP: the
    sets use the post-remap codigo; D7: `activo` needs a codigo, never the stickers), so replaying the notebook's rule is
    how a mismatch is attributed to one of them instead of being excused by a key list."""
    if no_persona:
        return "no_persona"
    if not sticker and not pre_codigo:
        return "revisar" if referencia else "candidato_desactivacion"
    return "activo" if post_codigo or tiene_sticker_valido else "revisar"


def _pre_remap_state(perfiles: dict) -> dict[str, dict]:
    """Each profile's OWN state before the remap and the D-P2 unification: what the notebook's `desactivar` / `revision`
    sets and its per-row flags were computed from. Keyed by the normalized `identidad_key`."""
    return {normalize_key(key): {"no_persona": bool(p.es_cuenta_no_persona), "sticker": p.n_stickers > 0,
                                 "referencia": bool(p.en_vercel or p.en_fase2), "codigo": bool(p.codigo)}
            for key, p in perfiles.items()}


@dataclass(frozen=True)
class CodigoReplay:
    """The notebook's remap outcome over `referencia.main` (one entry per main row, bundle order)."""

    codes: tuple  # the final código of each main row
    keys: tuple  # each row's own cédula key
    fase2: tuple = ()  # each row's matched Fase 2 cédula ("" when none)
    aplicados: int = 0
    omitidos: int = 0
    sin_duenio: int = 0
    conflicto: int = 0

    def by_key(self) -> dict[str, str]:
        """The final código per row key: a row's own key first (first row wins), then a Fase 2 cédula for a key no
        row owns (the key of a row whose cédula the D18 fix rewrote)."""
        out: dict[str, str] = {}
        for key, code in zip(self.keys, self.codes):
            if key:
                out.setdefault(key, code)
        for key, code in zip(self.fase2, self.codes):
            if key:
                out.setdefault(key, code)
        return out


def notebook_dup_codes(referencia: Any) -> frozenset[str]:
    """The notebook's `codigos_dup_v` (cells 30 and 67): the codes of every Vercel row whose person is duplicated BY
    NAME inside the Vercel padrón (`vercel["nombre_norm"].duplicated(keep=False)`); their remap is omitted entirely.
    Empty names never pair and blank codes are ignored (the publisher's `_codigos_duplicados` rule)."""
    por_nombre: dict[str, list[str]] = {}
    for entrada in referencia.vercel:
        nombre = dep._nombre_norm_de_entrada(entrada)
        if nombre:
            por_nombre.setdefault(nombre, []).append(dep._txt(entrada.codigo))
    return frozenset(codigo for codigos in por_nombre.values() if len(codigos) >= 2 for codigo in codigos if codigo)


def notebook_codigo(referencia: Any, *, excluded: Any = None, rules: Any = frozenset()) -> CodigoReplay:
    """The notebook's `codigo` remap (cells 29, 53 and 67), replayed over the bundle WITH mutation in row order, the
    way `notebook_estado` replays its estado rule. `titular_por_codigo` = the FIRST `referencia.main` row per non-empty
    trimmed código; the remap rows = Vercel rows (bundle order) whose código has a titular that is NOT the same person
    (`misma_persona`: cédula, Fase 2 cédula or `token_sort_ratio >= 90`, NO guard on empty names); for each one, in
    order: skipped when its código is excluded (`notebook_dup_codes`), a conflict when the titular no longer holds it,
    else the titular is cleared and the owner, found through a FIRST-ROW-WINS index over (cédula, Fase 2 cédula) in
    main order, gets it (`sin_duenio` when nobody is). Codes are exact trimmed text: "021" and "21" differ.

    `rules` turns ON, one at a time, the engine's deliberate differences, so a mismatch is attributed to a rule by
    replaying it (never by a key list): `D-REMAP` (a código with no main titular is assigned to its owner, stripping
    the owner's other one), `D-REMAP-TODOS` (every different-person holder of the código is cleared, not only the
    titular), `D-MISMAPERSONA` (an empty name never matches) and `D-OWNERFASE2` (an own cédula beats an earlier row's
    Fase 2 claim; a Fase 2 cédula shared by 2+ profiles is ambiguous: nothing cleared or assigned)."""
    rules = frozenset(rules)
    excluded = notebook_dup_codes(referencia) if excluded is None else frozenset(excluded)
    indice_fase2 = dep._IndiceReferencia(referencia.fase2)
    filas: list[tuple[str, str, str]] = []  # (own cédula key, Fase 2 cédula, nombre_norm)
    for entrada in referencia.main:
        clave, nombre = dep._cedula_key(entrada.cedula_key), dep._nombre_norm_de_entrada(entrada)
        hallada, _ = indice_fase2.buscar(clave, nombre)
        filas.append((clave, dep._cedula_key(hallada.cedula_key) if hallada is not None else "", nombre))
    codigos = [dep._txt(entrada.codigo) for entrada in referencia.main]

    titular: dict[str, int] = {}
    titulares: dict[str, set[int]] = {}
    for i, codigo in enumerate(codigos):
        if codigo:
            titular.setdefault(codigo, i)
            titulares.setdefault(codigo, set()).add(i)
    primero: dict[str, int] = {}  # the notebook's `cedula_idx`: first row wins over the pair (cédula, Fase 2 cédula)
    propios: dict[str, int] = {}
    por_fase2: dict[str, dict[str, int]] = {}
    for i, (clave, cedula_f2, _) in enumerate(filas):
        for cedula in (clave, cedula_f2):
            if cedula:
                primero.setdefault(cedula, i)
        if clave:
            propios.setdefault(clave, i)
        if cedula_f2 and cedula_f2 != clave:
            por_fase2.setdefault(cedula_f2, {}).setdefault(clave, i)

    def duenio(cedula: str) -> tuple[int | None, bool]:
        if "D-OWNERFASE2" not in rules:
            return primero.get(cedula), False
        if cedula in propios:
            return propios[cedula], False
        candidatos = por_fase2.get(cedula, {})
        if len(candidatos) == 1:
            return next(iter(candidatos.values())), False
        return None, len(candidatos) >= 2

    def misma_persona(i: int, cedula: str, nombre: str) -> bool:
        clave, cedula_f2, nombre_fila = filas[i]
        if (clave and clave == cedula) or (cedula_f2 and cedula_f2 == cedula):
            return True
        if "D-MISMAPERSONA" in rules and not (nombre_fila and nombre):
            return False  # the engine never matches an empty name (rapidfuzz scores "" vs "" as 100)
        return dep.fuzz.token_sort_ratio(nombre_fila, nombre) >= 90

    def limpiar(i: int) -> None:
        if codigos[i]:
            titulares[codigos[i]].discard(i)
            codigos[i] = ""

    def asignar(i: int, codigo: str) -> None:
        limpiar(i)
        codigos[i] = codigo
        titulares.setdefault(codigo, set()).add(i)

    aplicados = omitidos = sin_duenio = conflicto = 0
    for entrada in referencia.vercel:
        codigo = dep._txt(entrada.codigo)
        if not codigo:
            continue
        cedula, nombre = dep._cedula_key(entrada.cedula_key), dep._nombre_norm_de_entrada(entrada)
        excluido = codigo in excluded
        fila_titular = titular.get(codigo)
        if fila_titular is None:
            if "D-REMAP" in rules and not excluido:
                due, _ = duenio(cedula)
                if due is not None:
                    asignar(due, codigo)
            continue
        if not misma_persona(fila_titular, cedula, nombre):  # a remap row (`misma_persona == False`)
            if excluido:
                omitidos += 1
            else:
                due, ambiguo = duenio(cedula)
                if ambiguo:
                    pass  # engine: an ambiguous Fase 2 owner assigns nothing and clears nobody
                elif codigos[fila_titular] != codigo:
                    conflicto += 1  # the recorded titular no longer holds the código the remap expected to withdraw
                else:
                    limpiar(fila_titular)
                    if due is None:
                        sin_duenio += 1
                    else:
                        asignar(due, codigo)
                        aplicados += 1
        if "D-REMAP-TODOS" in rules and not excluido:
            for i in sorted(titulares.get(codigo, ())):
                if not misma_persona(i, cedula, nombre):
                    limpiar(i)
    return CodigoReplay(codes=tuple(codigos), keys=tuple(f[0] for f in filas), fase2=tuple(f[1] for f in filas),
                        aplicados=aplicados, omitidos=omitidos, sin_duenio=sin_duenio, conflicto=conflicto)


def _codigo_context(referencia: Any) -> dict:
    """The replays `_codigo_cause` reads: the notebook's own final código per key, and the final código per key when
    ONE engine rule is switched on (computed once per run, keyed by cédula, no name)."""
    excluidos = notebook_dup_codes(referencia)
    return {
        "notebook": notebook_codigo(referencia, excluded=excluidos).by_key(),
        "rules": {rule: notebook_codigo(referencia, excluded=excluidos, rules=frozenset({rule})).by_key()
                  for rule in CODIGO_RULES},
    }


def _reemplazos_del_bundle(referencia: Any) -> list[str]:
    """D-REMAP, roster-independent: the keys whose codigo the remap replaced (`codigo_reemplazado`) when the pass starts
    from the BUNDLE's own codes only. The production roster is already remapped (it carries the code the remap would
    assign), so the roster-fed pass sees anterior == nuevo and emits nothing; the bundle's `main` still holds the code
    the notebook saw, and comparing against it keeps the registered divergence visible."""
    perfiles, _ = dep.fusionar_identidad([], {}, referencia)
    _, revision = dep.remapear_codigos(perfiles, referencia)
    return [normalize_key(item.get("identidad_key")) for item in revision
            if item.get("motivo") == "codigo_reemplazado" and item.get("identidad_key")]


def _codigo_cause(key: str, backend_value: object, xlsx_value: object, register: dict) -> str | None:
    """Attributes a `codigo` mismatch to ONE registered engine rule by REPLAYING the notebook (`notebook_codigo`).
    Explained only when (1) the notebook replay reproduces the XLSX value on this key (otherwise it is input drift or
    something else) and (2) EXACTLY ONE registered rule reproduces the BACKEND value (D-REMAP, D-REMAP-TODOS,
    D-MISMAPERSONA, D-OWNERFASE2 by replay, D-CODIGOPERDIDO by its item). Zero or two matching rules, a key the replay
    does not know, or a hand-made register without the replay: unexplained (counts against the gate). Codes compare as
    trimmed text; a padding-only difference is the same código."""
    replay = register.get("context", {}).get("codigo_replay")
    if not replay or key not in replay["notebook"]:
        return None
    if _zfill3(replay["notebook"][key]) != _zfill3(_cell(xlsx_value).strip()):
        return None
    backend = _zfill3(_cell(backend_value).strip())
    causas = [rule for rule, codes in replay["rules"].items() if key in codes and _zfill3(codes[key]) == backend]
    if key in register.get("D-CODIGOPERDIDO", ()):
        causas.append("D-CODIGOPERDIDO")
    return causas[0] if len(causas) == 1 else None


def _survivor_ties(inputs: Inputs) -> tuple[list[str], list[str]]:
    """`(tie-break survivors, survivors whose date rule changed the winner)`, read from the D-P2 groups at the
    point the unification runs (stage 1 + remap). Uses the engine's own ranking helpers: this harness is welded to
    the engine on purpose, the register is what the engine is compared AGAINST."""
    perfiles, _ = dep.fusionar_identidad(inputs.stickers, inputs.roster_by_cedula, inputs.referencia)
    perfiles, _ = dep.remapear_codigos(perfiles, inputs.referencia)
    grupos: dict[str, list] = {}
    for _, perfil in sorted(perfiles.items()):
        if perfil.nombre_norm:
            grupos.setdefault(perfil.nombre_norm, []).append(perfil)

    def sin_clave(p):  # the survivor ordering WITHOUT its terminal identidad_key
        fecha = dep._parse_fecha(p.creado_en)
        return (fecha is None, fecha or dep._SIN_FECHA, not p.firestore_backed)

    tiebreak: list[str] = []
    survfecha: list[str] = []
    for candidatos in grupos.values():
        if len(candidatos) < 2:
            continue
        ganador = dep._elegir_survivor(candidatos)
        mejor = max(dep._survivor_score(c) for c in candidatos)
        empatados = [c for c in candidatos if dep._survivor_score(c) == mejor]
        if sum(1 for c in empatados if sin_clave(c) == sin_clave(ganador)) > 1:
            tiebreak.append(ganador.identidad_key)
        con_fecha = [c for c in empatados if c.creado_en]
        if con_fecha and min(con_fecha, key=lambda c: c.creado_en).identidad_key != ganador.identidad_key:
            survfecha.append(ganador.identidad_key)
    return sorted(set(tiebreak)), sorted(set(survfecha))


def _stage_context(inputs: Inputs) -> tuple[dict[str, dict], dict[str, str]]:
    """`(own pre-remap state per profile, source of the resolved entidad per final profile)`, replayed through the
    engine's own stages (the harness is welded to the engine on purpose, like `_survivor_ties`)."""
    perfiles, _ = dep.fusionar_identidad(inputs.stickers, inputs.roster_by_cedula, inputs.referencia)
    pre_state = _pre_remap_state(perfiles)  # BEFORE the remap: its notebook counterpart is the row's own original state
    perfiles, _ = dep.remapear_codigos(perfiles, inputs.referencia)
    perfiles, _ = dep.unificar_duplicados(perfiles)
    source = {normalize_key(key): ("vercel" if p.entidad_vercel else "firestore" if p.entidad_firestore
                                   else "main" if p.entidad_main else "")
              for key, p in perfiles.items()}
    return pre_state, source


def build_register(inputs: Inputs, depurado: Any) -> dict:
    """Every documented divergence that can be derived from the inputs and the result alone, listed by
    `identidad_key` (D7, D-N-COLAPSADOS and the two trim divergences also need the xlsx: `compare` adds them)."""
    register: dict[str, Any] = {name: [] for name in REGISTER_NAMES if name != "D-N-COLAPSADOS"}
    names_by_key: dict[str, list[str]] = {}
    sin_registrar: Counter = Counter()
    colision: dict[str, str] = {}  # a key whose D18 Fase 2 fix was blocked -> the Fase 2 cedula the notebook used
    for item in depurado.revision_manual:
        name = REVISION_REGISTER.get(item.get("motivo"))
        if name is None:
            sin_registrar[item.get("motivo")] += int(item.get("n_ocurrencias") or 1)
            continue
        for key in _item_keys(item):
            register[name].append(key)
            if name not in REVIEW_SIGNALS:  # a review signal is listed by key but never explains a mismatch
                names_by_key.setdefault(key, []).append(name)
        if name == "D-FASE2-COLISION":
            bloqueada, fase2 = normalize_key(item.get("identidad_key")), normalize_key(item.get("cedula_key"))
            if bloqueada and fase2 and bloqueada != fase2:
                colision[bloqueada] = fase2
    # D-REMAP is roster-independent: the roster-fed pass hides a replacement whose new codigo the roster already carries
    register["D-REMAP"] += _reemplazos_del_bundle(inputs.referencia)

    exentos = set(depurado.alias_nombres.values())
    register["D-EXENTOS"] = [r["identidad_key"] for r in depurado.inspectores
                             if r["identidad_key"] in exentos and _collapse_candidate(r)]
    register["D-TIEBREAK"], register["D-SURVFECHA"] = _survivor_ties(inputs)
    pre_state, entidad_source = _stage_context(inputs)
    claves_roster = Counter(k for k in (normalize_key(r.get("identificacion")) for r in inputs.roster_by_cedula.values()) if k)
    register["D-TIEBREAK-ROSTER"] = [k for k, n in claves_roster.items() if n > 1]

    row_keys = {normalize_key(r["identidad_key"]) for r in depurado.inspectores}
    crudas = [r.get("identificacion") for r in inputs.roster_by_cedula.values()]
    crudas += [(s.get("inspector") or {}).get("identificacion") for s in inputs.stickers]
    register["D-CEDDEC"] = [k for k in (normalize_key(c) for c in crudas
                                        if c and cedula_utils.quitar_cola_flotante(str(c)) != str(c)) if k in row_keys]

    indice: dict[str, str] = {}
    for row in depurado.inspectores:
        propia = normalize_key(row["identidad_key"])
        indice[propia] = propia
        for otra in row.get("cedulas_unificadas") or ():
            indice.setdefault(normalize_key(otra), propia)
    origenes: dict[str, Counter] = {}
    for sticker in inputs.stickers:
        clave = indice.get(normalize_key((sticker.get("inspector") or {}).get("identificacion")))
        if clave:
            origen = str(sticker.get("origen") or "").strip().lower()
            origenes.setdefault(clave, Counter())["otro" if origen not in ("sistema", "firebase") else origen] += 1

    for name, keys in register.items():
        register[name] = sorted(set(keys))
    register["revision_no_registrados"] = dict(sorted(sin_registrar.items(), key=lambda kv: str(kv[0])))
    register["context"] = {
        "names_by_key": {k: sorted(v) for k, v in names_by_key.items()},
        "origenes": {k: dict(v) for k, v in origenes.items()},
        "roster_keys": sorted(set(claves_roster)),
        "main_keys": sorted({normalize_key(e.cedula_key) for e in inputs.referencia.main}),
        "pre": pre_state,
        "entidad_source": entidad_source,
        "colision": colision,
        "codigo_replay": _codigo_context(inputs.referencia),
    }
    return register


# ── compare: depurar() vs the notebook xlsx ─────────────────────────────────

def _estado_cause(key: str, backend_row: dict, xlsx_row: dict, ctx: dict) -> str | None:
    """Attributes an `estado_sugerido` mismatch to D-REMAP or D7 by REPLAYING the notebook's rule (`notebook_estado`),
    never by a key list. Explained only when (1) the notebook's rule, fed with the notebook's own columns, reproduces
    ITS estado (otherwise the row is something else) and (2) turning exactly one of the two engine deliberate differences
    on reproduces the ENGINE's estado: D-REMAP = the desactivar/revision sets read the post-remap codigo (a codigo the
    remap cleared no longer shields the row), D7 = `activo` needs a codigo, never `tiene_sticker_valido`. Anything else
    (a no_persona flip, a codigo mismatch, a merge effect) returns None and counts against the gate."""
    pre = ctx["pre"].get(key)
    if pre is None:
        return None  # no engine context for this key (a hand-made row): never guess
    notebook, engine = _cell(xlsx_row.get("estado_sugerido")).strip(), _cell(backend_row.get("estado_sugerido")).strip()
    notebook_post = bool(_cell(xlsx_row.get("codigo")).strip())
    engine_post = bool(_cell(backend_row.get("codigo")).strip())
    valido = _as_bool(xlsx_row.get("tiene_sticker_valido"))
    # the notebook's own pre-remap column when the file has it, else the engine's reconstruction of the same thing
    pre_codigo = bool(_cell(xlsx_row["codigo_inspector_original"]).strip()) if "codigo_inspector_original" in xlsx_row         else pre["codigo"]
    flags = {"no_persona": pre["no_persona"], "sticker": pre["sticker"], "referencia": pre["referencia"]}
    replay = lambda pre_c, post_c, tsv: notebook_estado(pre_codigo=pre_c, post_codigo=post_c, tiene_sticker_valido=tsv, **flags)  # noqa: E731
    if replay(pre_codigo, notebook_post, valido) != notebook:
        return None
    if replay(pre_codigo, engine_post, valido) == engine:
        return None  # the difference is the codigo itself, not the estado rule
    if replay(engine_post, engine_post, valido) == engine:
        return "D-REMAP"
    if replay(pre_codigo, engine_post, False) == engine:
        return "D7"
    return None


def _explain(column: str, key: str, xlsx_value: object, register: dict) -> str | None:
    """The register entry that explains a mismatch of `column` on `key`, if any."""
    ctx = register["context"]
    if column == "estado_sugerido" and key in ctx["names_by_key"]:
        return ctx["names_by_key"][key][0]  # the `codigo` column is attributed by `_codigo_cause`, never by a bare item
    if column == "tiene_sticker_valido" and key in register["D7"]:
        return "D7"
    if column == "estado_sugerido" and key in register["D7"] and _cell(xlsx_value).strip().lower() == "activo":
        return "D7"
    for name in ("D-TIEBREAK", "D-SURVFECHA"):
        if key in register[name]:
            return name
    return None


def _column_result(column: str, matches: int, n: int, n_effective: int, mismatches: list, threshold: float) -> dict:
    gated = column in THRESHOLD_COLUMNS
    return {
        "column": column, "n": n, "matches": matches, "n_effective": n_effective,
        "pct": round(100 * matches / n_effective, 2) if n_effective else None,
        "threshold": threshold if gated else None,
        "passed": meets_threshold(matches, n_effective, threshold) if gated else None,
        "fully_mismatched": n > 0 and matches == 0, "mismatches": mismatches,
    }


def _not_candidate_reason(row: dict) -> str:
    """Why a backend row that the notebook collapsed is not a collapse candidate in the backend."""
    parts = []
    if not (row.get("cedula_sospechosa") or row.get("no_persona")):
        parts.append("no flag")
    elif row.get("ultimo_sticker") is not None and not (row.get("dias_inactivo") is not None and row["dias_inactivo"] > 7):
        parts.append("recent sticker")
    if row.get("codigo"):
        parts.append("has codigo")
    return ", ".join(parts) or "unknown"


def _pair_residuals(only_backend: list, only_notebook: list, backend_names: dict, notebook_names: dict) -> list[dict]:
    """The D-N-COLAPSADOS residuals are keyed by cedula, but D-P2 unifies people by exact normalized NAME
    (`normalizar_nombre`): when the two sides picked a different surviving cedula for the same person, one side lists the
    cedula it collapsed and the other the cedula it kept, and the two residual rows cancel on the count. Rows of the same
    name group pair 1:1 (`min(#backend, #notebook)` pairs, lowest cedulas first: deterministic); an empty name never
    pairs. Pairs are `{"backend": cedula, "notebook": cedula}`, never a name (PII), sorted by the backend cedula."""
    groups: dict[str, tuple[list, list]] = {}
    for side, keys, names in ((0, only_backend, backend_names), (1, only_notebook, notebook_names)):
        for key in keys:
            name = dep.normalizar_nombre(names.get(key))
            if name:
                groups.setdefault(name, ([], []))[side].append(key)
    pairs = [{"backend": b, "notebook": n} for backend, notebook in groups.values()
             for b, n in zip(sorted(backend), sorted(notebook))]
    return sorted(pairs, key=lambda p: (p["backend"], p["notebook"]))


def compare(depurado: Any, xlsx_rows: list, register: dict, *, externos_rows: list | None = None,
            n_colapsados_notebook: int | None = None, threshold: float = THRESHOLD) -> dict:
    """Per-column match counts on the matched keys, the matched / only-in-backend / only-in-xlsx key lists,
    every mismatch enumerated (not an aggregate score) and the register asserted key by key. Full keys live in the
    returned structure; `format_parity_report` masks them."""
    register = copy.deepcopy(register)
    ctx = register["context"]
    backend: dict[str, dict] = {}
    for row in depurado.inspectores:
        backend.setdefault(normalize_key(row.get("identidad_key") or row.get("identificacion")), row)

    xlsx: dict[str, dict] = {}
    duplicados: list[str] = []
    sin_clave = 0
    for row in xlsx_rows:
        if _cell(row.get("id")).strip() == GRUPO_ID:
            continue
        key = normalize_key(row.get("identificacion"))
        if not key:
            sin_clave += 1
        elif key in xlsx:
            duplicados.append(key)  # first wins, deterministically; the duplicate is a finding
        else:
            xlsx[key] = row
            if cedula_utils.quitar_cola_flotante(_cell(row.get("identificacion"))) != _cell(row.get("identificacion")) and key in backend:
                register["D-CEDDEC"].append(key)

    matched = sorted(set(backend) & set(xlsx))
    only_backend = sorted(set(backend) - set(xlsx))
    only_xlsx = sorted(set(xlsx) - set(backend))

    # D7: the notebook counted a firebase sticker as valid; the engine (origen == "sistema" only) does not.
    for key in matched:
        if _as_bool(xlsx[key].get("tiene_sticker_valido")) and not _as_bool(backend[key].get("tiene_sticker_valido")):
            origenes = ctx["origenes"].get(key, {})
            solo_firebase = origenes.get("firebase", 0) > 0 and not origenes.get("sistema") and not origenes.get("otro")
            register["D7" if solo_firebase else "D7-SIN-EXPLICAR"].append(key)

    columns: dict[str, dict] = {}
    for column in COMPARED:
        n = matches = 0
        mismatches: list[dict] = []
        for key in matched:
            reference = xlsx[key].get(column)
            if column in CONTACT_COLUMNS and not _cell(reference).strip():
                continue  # contact columns count only where the source is non-empty
            n += 1
            igual, etiqueta = _compare_cell(column, backend[key].get(column), reference)
            if igual:
                matches += 1
                continue
            if column == "estado_sugerido":
                etiqueta = _estado_cause(key, backend[key], xlsx[key], ctx)
                if etiqueta:
                    register[etiqueta].append(key)  # D7 / D-REMAP, listed by key
            elif column == "codigo" and not etiqueta:
                etiqueta = _codigo_cause(key, backend[key].get(column), reference, register)  # replay-attributed rule
            elif column == "entidad" and not etiqueta and ctx["entidad_source"].get(key) == "main"                     and _cell(backend[key].get("entidad")).strip() and not _cell(reference).strip():
                etiqueta = "D12"  # the notebook maps entidad from the Vercel cedula map only; `main.entidad` is an extra source
            if etiqueta:
                register[etiqueta].append(key)
            mismatches.append({"key": key, "backend": _sanitize(column, backend[key].get(column)),
                               "xlsx": _sanitize(column, reference),
                               "explained": etiqueta or _explain(column, key, reference, register)})
        excluidos = 0  # explained mismatches of these two columns are listed by key and leave the gated denominator
        if column == "estado_sugerido":
            excluidos = sum(1 for m in mismatches if m["explained"])
        elif column == "entidad":
            excluidos = sum(1 for m in mismatches if m["explained"] == "D12")
        elif column == "codigo":
            excluidos = sum(1 for m in mismatches if m["explained"] in CODIGO_EXPLAINED)
        columns[column] = _column_result(column, matches, n, n - excluidos, mismatches, threshold)

    # identificacion: the join key cannot differ on a matched row, so the column measures the people the backend
    # has under ANOTHER cedula (a D-P2 survivor): those reference rows resolve through `cedulas_unificadas`.
    alias = {normalize_key(c): key for key, row in backend.items() for c in row.get("cedulas_unificadas") or ()
             if normalize_key(c) != key}
    detalle_keys = {normalize_key(d.get("identificacion")) for d in ((depurado.grupo_externos or {}).get("detalle") or ())}
    externos_keys = {normalize_key(r.get("identificacion")) for r in externos_rows or ()} - {""}
    # D-FASE2-COLISION: a profile whose D18 Fase 2 fix was blocked (`fase2_cedula_colision`) stays under its OWN cedula in
    # the backend while the notebook, which applied the fix, has the row under the Fase 2 cedula. Look the notebook row up
    # there before calling the backend key "added after the snapshot" and the notebook key "absent from the backend".
    colision = {k: f for k, f in ctx.get("colision", {}).items()
                if f != k and k not in xlsx and (f in xlsx or f in externos_keys) and (k in backend or k in detalle_keys)}
    colision_destinos = set(colision.values())
    resolved = [k for k in only_xlsx if k in alias]
    colapsados = [k for k in only_xlsx if k not in alias and k in detalle_keys]
    por_colision = [k for k in only_xlsx if k not in alias and k not in detalle_keys and k in colision_destinos]
    faltan = [k for k in only_xlsx if k not in alias and k not in detalle_keys and k not in colision_destinos]
    columns["identificacion"] = _column_result(
        "identificacion", len(matched), len(matched) + len(resolved), len(matched) + len(resolved),
        [{"key": k, "backend": alias[k], "xlsx": k, "explained": _explain("identificacion", alias[k], k, register)}
         for k in resolved], threshold)

    # the row accounting: every only-in-backend key is a Firestore-only extra, a D-EXENTOS survivor or listed as unexplained.
    # A D-EXENTOS survivor is recognised by the ENGINE's own exemption (`alias_nombres`, a survey_cali corroboration) plus
    # the D-EXENTOS definition (a collapse candidate the exemption kept), NOT by its presence in the notebook's externos
    # sheet: a person added to `main` after the xlsx snapshot can never be in that sheet.
    exentos = set(register["D-EXENTOS"])
    roster_keys, main_keys = set(ctx["roster_keys"]), set(ctx["main_keys"])
    firestore_only = [k for k in only_backend if k in roster_keys and k not in main_keys]
    exento = [k for k in only_backend if k in exentos and k not in firestore_only]
    exento_post_snapshot = [k for k in exento if externos_keys and k not in externos_keys and k not in colision]
    colision_backend = [k for k in only_backend if k in colision and k not in firestore_only]
    colision_no_exento = [k for k in colision_backend if k not in exento]
    sin_explicar = [k for k in only_backend if k not in firestore_only and k not in exento and k not in colision]
    accounting = {"backend": len(backend), "matched": len(matched), "firestore_only": len(firestore_only),
                  "exento_survivor": len(exento), "unexplained": len(sin_explicar)}
    if colision_no_exento:  # present only when it applies: the accounting keeps its shape on a run without collisions
        accounting["fase2_colision"] = len(colision_no_exento)
    accounting["balanced"] = accounting["backend"] == sum(accounting[k] for k in (
        "matched", "firestore_only", "exento_survivor", "unexplained", "fase2_colision") if k in accounting)

    # D-N-COLAPSADOS: recomputed by the engine (D13 / D-MISMAPERSONA semantics), reported, never assumed
    n_backend = int((depurado.grupo_externos or {}).get("n_colapsados") or 0)
    notebook = n_colapsados_notebook if n_colapsados_notebook is not None else (len(externos_keys) or None)
    dropped = started = exentos_vivos = solo_backend = solo_notebook = []
    pares_colision: list[dict] = []
    if externos_rows is not None:
        dropped = sorted(k for k in detalle_keys - externos_keys if k in xlsx and _cell(xlsx[k].get("codigo")).strip())
        started = sorted(k for k in externos_keys - detalle_keys if k in backend and backend[k].get("codigo"))
        notebook_exentos = externos_keys & exentos
        colision_exentos = {k: f for k, f in colision.items() if k in exentos and f in externos_keys}
        exentos_vivos = sorted(notebook_exentos | set(colision_exentos))  # a colision survivor is listed by its backend key
        solo_backend = sorted((detalle_keys - externos_keys) - set(dropped))
        colision_colapsados = {k: f for k, f in colision.items() if k in solo_backend and f in externos_keys}
        # the collapsed row the notebook has under the Fase 2 cedula pairs with the blocked backend key: net 0 on the count
        solo_backend = [k for k in solo_backend if k not in colision_colapsados]
        solo_notebook = sorted((externos_keys - detalle_keys) - set(started) - notebook_exentos
                               - set(colision_exentos.values()) - set(colision_colapsados.values()))
        pares_colision = sorted(({"backend": k, "notebook": f} for k, f in (*colision_exentos.items(), *colision_colapsados.items())),
                                key=lambda p: (p["backend"], p["notebook"]))
    # the same person under a different surviving cedula (D-TIEBREAK / D-P2): explained, and out of the residuals
    backend_names = {normalize_key(d.get("identificacion")): d.get("nombre_completo")
                     for d in ((depurado.grupo_externos or {}).get("detalle") or ())}
    notebook_names = {normalize_key(r.get("identificacion")): r.get("nombre_completo") for r in externos_rows or ()}
    same_person = _pair_residuals(solo_backend, solo_notebook, backend_names, notebook_names)
    paired_backend, paired_notebook = {p["backend"] for p in same_person}, {p["notebook"] for p in same_person}
    solo_backend = [k for k in solo_backend if k not in paired_backend]
    solo_notebook = [k for k in solo_notebook if k not in paired_notebook]
    esperado = None if notebook is None else notebook - len(exentos_vivos) - len(started) + len(dropped)
    motivos = {normalize_key(d.get("identificacion")): _cell(d.get("motivo")) or "unknown"
               for d in ((depurado.grupo_externos or {}).get("detalle") or ())}
    reasons = {k: ("notebook kept the row" if k in xlsx else "absent from the reference xlsx")
                  + f" (backend motivo {motivos.get(k, 'unknown')})" for k in solo_backend}
    for k in solo_notebook:
        if k in backend:
            reasons[k] = f"backend row is not a collapse candidate ({_not_candidate_reason(backend[k])})"
        else:
            reasons[k] = "absorbed by a D-P2 survivor in the backend" if k in alias else "absent from the backend"
    register["D-N-COLAPSADOS"] = {
        "backend": n_backend, "notebook": notebook, "expected": esperado,
        "unexplained_delta": None if esperado is None else n_backend - esperado,
        "exentos_survivors": exentos_vivos, "dropped_exclusion": dropped, "started_exclusion": started,
        "residual_only_backend": solo_backend, "residual_only_notebook": solo_notebook, "residual_reasons": reasons,
        "same_person_different_survivor": same_person,
        "fase2_colision": pares_colision,
    }

    for name in register:
        if name != "context" and isinstance(register[name], list):
            register[name] = sorted(set(register[name]))

    failures: list[str] = []
    if not matched:
        failures.append("no matched keys between the backend and the reference: nothing was compared")
    for column, result in columns.items():
        if result["threshold"] is not None and not result["passed"]:
            failures.append(f"{column} below {result['threshold']}% ({result['matches']}/{result['n_effective']})")
    if duplicados:
        failures.append(f"{len(set(duplicados))} duplicate key(s) in the reference xlsx")
    if register["D7-SIN-EXPLICAR"]:
        failures.append("tiene_sticker_valido differences that D7 does not explain")
    if sin_explicar:
        failures.append(f"{len(sin_explicar)} backend row(s) beyond the enumerated extras")
    if faltan:
        failures.append(f"{len(faltan)} reference row(s) missing from the backend")
    if register["D-N-COLAPSADOS"]["unexplained_delta"]:
        failures.append("n_colapsados differs from the notebook by more than the register explains")

    return {
        "counts": {"backend": len(backend), "xlsx": len(xlsx), "matched": len(matched)},
        "keys": {"matched": matched, "only_in_backend": only_backend, "only_in_xlsx": only_xlsx,
                 "duplicate_xlsx": sorted(set(duplicados)), "xlsx_sin_clave": sin_clave},
        "extras": {"firestore_only": firestore_only, "exento_survivor": exento, "exento_post_snapshot": exento_post_snapshot,
                   "fase2_colision": colision_backend, "unexplained": sin_explicar},
        "accounting": accounting,
        "only_in_xlsx_detail": {"resolved_via_unificadas": resolved, "collapsed_in_backend": colapsados,
                                "fase2_colision": por_colision, "missing": faltan},
        "columns": columns, "register": register, "passed": not failures, "failures": failures,
    }


MAX_LISTED = 50


def _masked(keys: list) -> str:
    listed = ", ".join(mask_key(k) for k in keys[:MAX_LISTED])
    return f"[{listed}{f', ... +{len(keys) - MAX_LISTED} more' if len(keys) > MAX_LISTED else ''}]"


def format_parity_report(report: dict) -> str:
    """Aggregates and masked keys only: no name, correo, phone, tarjeta or full cedula."""
    c, k = report["counts"], report["keys"]
    lines = [
        "== parity: depurar() vs the notebook xlsx ==",
        f"rows: backend {c['backend']}, xlsx {c['xlsx']}, matched {c['matched']}, only-in-backend {len(k['only_in_backend'])},"
        f" only-in-xlsx {len(k['only_in_xlsx'])}, duplicate-in-xlsx {len(k['duplicate_xlsx'])}, xlsx-without-key {k['xlsx_sin_clave']}",
    ]
    a = report["accounting"]
    lines.append(f"  row accounting: backend {a['backend']} = matched {a['matched']} + firestore-only {a['firestore_only']}"
                 f" + D-EXENTOS survivors {a['exento_survivor']}"
                 + (f" + Fase 2 collisions {a['fase2_colision']}" if a.get("fase2_colision") else "")
                 + f" + unexplained {a['unexplained']} ({'balanced' if a['balanced'] else 'NOT BALANCED'})")
    for label, keys in (*report["extras"].items(), *report["only_in_xlsx_detail"].items(),
                        ("duplicate_xlsx", k["duplicate_xlsx"])):
        lines.append(f"  {label}: {len(keys)} {_masked(keys)}")
    lines.append("columns (matched keys):")
    for column, r in report["columns"].items():
        pct = "n/a" if r["pct"] is None else f"{r['pct']:.2f}%"
        status = "info" if r["threshold"] is None else ("PASS" if r["passed"] else f"FAIL (threshold {r['threshold']}%)")
        extra = f", {r['n'] - r['n_effective']} explained excluded" if r["n"] != r["n_effective"] else ""
        lines.append(f"  {column}: {r['matches']}/{r['n_effective']} ({pct}) {status}{extra}")
        if r["fully_mismatched"]:
            lines.append(f"    !! COLUMN FULLY MISMATCHED: 0 of {r['n']} rows agree with the reference")
        for m in r["mismatches"][:MAX_LISTED]:
            values = (mask_key(m["backend"]), mask_key(m["xlsx"])) if column == "identificacion" else (m["backend"], m["xlsx"])
            tag = f" [{m['explained']}]" if m["explained"] else ""
            lines.append(f"    {mask_key(m['key'])}: backend={values[0]!r} xlsx={values[1]!r}{tag}")
        if len(r["mismatches"]) > MAX_LISTED:
            lines.append(f"    ... +{len(r['mismatches']) - MAX_LISTED} more mismatches")
    lines.append("register (divergences by key):")
    for name in REGISTER_NAMES:
        entry = report["register"][name]
        if name == "D-N-COLAPSADOS":
            nb = "n/a" if entry["notebook"] is None else entry["notebook"]
            lines.append(f"  n_colapsados: backend {entry['backend']}, notebook {nb}, expected {entry['expected']},"
                         f" unexplained delta {entry['unexplained_delta']}")
            for label in ("exentos_survivors", "dropped_exclusion", "started_exclusion", "residual_only_backend",
                          "residual_only_notebook"):
                lines.append(f"    {label}: {len(entry[label])} {_masked(entry[label])}")
            for key, reason in list(entry["residual_reasons"].items())[:MAX_LISTED]:
                lines.append(f"      {mask_key(key)}: {reason}")
            pairs = entry["same_person_different_survivor"]
            listed = ", ".join(f"{mask_key(p['backend'])} <-> {mask_key(p['notebook'])}" for p in pairs[:MAX_LISTED])
            more = f", ... +{len(pairs) - MAX_LISTED} more" if len(pairs) > MAX_LISTED else ""
            lines.append(f"    same_person_different_survivor: {len(pairs)} [{listed}{more}]"
                         " (explained, D-TIEBREAK / D-P2: the same person, a different surviving cedula; net 0 on the count)")
            pares = entry["fase2_colision"]
            listed = ", ".join(f"{mask_key(p['backend'])} <-> {mask_key(p['notebook'])}" for p in pares[:MAX_LISTED])
            more = f", ... +{len(pares) - MAX_LISTED} more" if len(pares) > MAX_LISTED else ""
            lines.append(f"    fase2_colision: {len(pares)} [{listed}{more}]"
                         " (explained, D-FASE2-COLISION: the D18 Fase 2 fix was blocked in the backend, the notebook applied it;"
                         " backend key <-> the Fase 2 cedula the notebook used)")
        else:
            lines.append(f"  {name}: {len(entry)} {_masked(entry)}")
    if report["register"]["revision_no_registrados"]:
        lines.append(f"  review motivos outside the register: {report['register']['revision_no_registrados']}")
    lines.append("PARITY " + ("PASS" if report["passed"] else "FAIL: " + "; ".join(report["failures"])))
    return "\n".join(lines)


# ── determinism (design D29) ────────────────────────────────────────────────

DETERMINISM_SEEDS = (1, 2)
TIMING_CEILING_S = 0.5  # design "Efficiency Acceptance Budgets": engine < 0.5 s (baseline about 0.26 s)
TIMING_MIN_RUNS = 3


def _is_empty(inputs: Inputs | None) -> bool:
    return inputs is None or (not inputs.roster_by_cedula and not inputs.stickers)


def _shuffled(items: list, rnd: random.Random) -> list:
    original, out = list(items), list(items)
    rnd.shuffle(out)
    while len(out) > 1 and out == original:  # a shuffle that leaves the order alone would make the check vacuous
        rnd.shuffle(out)
    return out


def shuffled_inputs(inputs: Inputs, seed: int) -> Inputs:
    """The same snapshot with roster, stickers and survey names in another order. `referencia` is untouched: its
    row order is part of the input (D19 first-wins, D29)."""
    rnd = random.Random(seed)
    return Inputs(
        stickers=_shuffled(inputs.stickers, rnd), roster_by_cedula=dict(_shuffled(list(inputs.roster_by_cedula.items()), rnd)),
        nombres_survey=_shuffled(inputs.nombres_survey, rnd), referencia=inputs.referencia, hoy=inputs.hoy,
    )


def _revision_keys(items: list[str]) -> list[str]:
    return [key for raw in items for key in (_item_keys(json.loads(raw)) or ["<item>"])]


def _result_diff(reference: Any, other: Any) -> dict[str, list[str]]:
    """Differing `identidad_key`s per field between two engine results, over every emitted structure."""
    diffs: dict[str, set] = {}

    def add(field: str, keys: Any) -> None:
        diffs.setdefault(field, set()).update(keys)

    rows_a = {normalize_key(r.get("identidad_key")): r for r in reference.inspectores}
    rows_b = {normalize_key(r.get("identidad_key")): r for r in other.inspectores}
    for key in set(rows_a) | set(rows_b):
        a, b = rows_a.get(key), rows_b.get(key)
        if a is None or b is None:
            add("<row>", [key])
            continue
        for field in set(a) | set(b):
            if a.get(field) != b.get(field):
                add(field, [key])
    if set(rows_a) == set(rows_b) and list(rows_a) != list(rows_b):
        add("<order>", ["inspectores"])

    def detalle(result: Any) -> dict:
        return {normalize_key(d.get("identificacion")): d for d in ((result.grupo_externos or {}).get("detalle") or ())}

    det_a, det_b = detalle(reference), detalle(other)
    add("grupo_externos", [k for k in set(det_a) | set(det_b) if det_a.get(k) != det_b.get(k)])
    if (reference.grupo_externos or {}).get("n_colapsados") != (other.grupo_externos or {}).get("n_colapsados"):
        add("grupo_externos", ["n_colapsados"])

    dump = lambda i: json.dumps(i, sort_keys=True, ensure_ascii=False, default=str)  # noqa: E731
    rev_a, rev_b = [dump(i) for i in reference.revision_manual], [dump(i) for i in other.revision_manual]
    sym = list((Counter(rev_a) - Counter(rev_b)).elements()) + list((Counter(rev_b) - Counter(rev_a)).elements())
    add("revision_manual", _revision_keys(sym) if sym else (["<order>"] if rev_a != rev_b else []))

    alias_a, alias_b = reference.alias_nombres, other.alias_nombres
    add("alias_nombres", [str(v) for n in set(alias_a) | set(alias_b) if alias_a.get(n) != alias_b.get(n)
                          for v in (alias_a.get(n), alias_b.get(n)) if v])
    return {field: sorted(keys) for field, keys in sorted(diffs.items()) if keys}


def check_determinism(inputs: Inputs | None, *, depurar_fn: Callable[..., Any] | None = None,
                      seeds: tuple = DETERMINISM_SEEDS) -> dict:
    """`depurar()` over the snapshot as given and with roster / stickers / survey names in shuffled orders: the
    result must be identical (D29). Never a vacuous pass: an empty snapshot is an error."""
    if _is_empty(inputs):
        raise HarnessError("empty snapshot: no roster and no stickers, there is nothing to shuffle or compare")
    reference = inputs.run(depurar_fn)
    if not reference.inspectores:
        raise HarnessError("empty snapshot: depurar() returned no profiles, nothing was compared")
    diffs: dict[str, list[str]] = {}
    for seed in seeds:
        for field, keys in _result_diff(reference, shuffled_inputs(inputs, seed).run(depurar_fn)).items():
            diffs[field] = sorted(set(diffs.get(field, [])) | set(keys))
    return {"ok": not diffs, "n_variants": 1 + len(seeds), "n_inspectores": len(reference.inspectores), "diffs": diffs}


def format_determinism_report(result: dict) -> str:
    if result["ok"]:
        return (f"DETERMINISM PASS: {result['n_variants']} orders (original + {result['n_variants'] - 1} shuffled), "
                f"{result['n_inspectores']} profiles identical")
    lines = [f"DETERMINISM FAIL: the result depends on the input order ({result['n_variants']} orders compared)"]
    lines += [f"  field {field!r}: {len(keys)} key(s) {_masked(keys)}" for field, keys in result["diffs"].items()]
    return "\n".join(lines)


# ── timing ──────────────────────────────────────────────────────────────────

def check_timing(inputs: Inputs | None, *, runs: int = TIMING_MIN_RUNS, ceiling_s: float = TIMING_CEILING_S,
                 depurar_fn: Callable[..., Any] | None = None, clock: Callable[[], float] | None = None) -> dict:
    """Best of `runs` (>= 3) `depurar()` runs must stay strictly under `ceiling_s`. The best run is the signal:
    one slow run is scheduler noise, a slow engine is slow every time."""
    if _is_empty(inputs):
        raise HarnessError("empty snapshot: no roster and no stickers, a timing over nothing would prove nothing")
    if runs < TIMING_MIN_RUNS:
        raise HarnessError(f"timing needs at least {TIMING_MIN_RUNS} runs (got {runs})")
    if not ceiling_s > 0:
        raise HarnessError(f"timing ceiling must be positive (got {ceiling_s})")
    now = clock or time.perf_counter
    best = math.inf
    result = None
    for _ in range(runs):
        started = now()
        result = inputs.run(depurar_fn)
        best = min(best, now() - started)
    return {
        "ok": best < ceiling_s, "best_s": best, "runs": runs, "ceiling_s": ceiling_s,
        "n_roster": len(inputs.roster_by_cedula), "n_main": len(inputs.referencia.main),
        "n_stickers": len(inputs.stickers), "n_survey": len(inputs.nombres_survey),
        "n_inspectores": len(result.inspectores),
    }


def format_timing_report(result: dict) -> str:
    return (f"TIMING {'PASS' if result['ok'] else 'FAIL'} best of {result['runs']}: {result['best_s']:.3f} s "
            f"(ceiling {result['ceiling_s']} s) | inputs: roster {result['n_roster']}, main {result['n_main']}, "
            f"stickers {result['n_stickers']}, survey {result['n_survey']} -> {result['n_inspectores']} profiles")


# ── read ledger (design D32: ONE process; counts only, never a document) ─────

COLLECTION_KEYS = {"evaluaciones": "E", "inspectores": "I", "survey_cali": "S"}


class ReadOnlyViolation(HarnessError):
    """Something tried to write. This harness never writes: Firestore has no write surface here, a Blob PUT is refused."""


class ReadLedger:
    """Thread-safe counters. Integers only: nothing that passes through the wrappers is kept."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._docs: Counter = Counter()
        self._other = self._np_lookup = self._api_rows = self._blob_get = self._blob_put = 0

    def count_docs(self, collection: str, n: int = 1) -> None:
        with self._lock:
            if collection in COLLECTION_KEYS:
                self._docs[collection] += n
            else:
                self._other += n

    def count_np_lookup(self, n: int = 1) -> None:
        with self._lock:
            self._np_lookup += n

    def add_api_rows(self, n: int) -> None:
        with self._lock:
            self._api_rows += n

    def count_blob(self, kind: str) -> None:
        with self._lock:
            if kind == "get":
                self._blob_get += 1
            else:
                self._blob_put += 1

    def report(self) -> dict:
        with self._lock:
            return {"E": self._docs["evaluaciones"], "I": self._docs["inspectores"], "S": self._docs["survey_cali"],
                    "np_lookup": self._np_lookup, "other_docs": self._other, "api_rows": self._api_rows,
                    "blob_get": self._blob_get, "blob_put": self._blob_put}


class _DocRef:
    """A document reference the caller can only hand back to `get_all`: no `set`, `update`, `delete`, nothing."""

    __slots__ = ("_inner", "id")

    def __init__(self, inner: Any, doc_id: str) -> None:
        self._inner, self.id = inner, doc_id


class _CountingCollection:
    __slots__ = ("_inner", "_name", "_ledger")

    def __init__(self, inner: Any, name: str, ledger: ReadLedger) -> None:
        self._inner, self._name, self._ledger = inner, name, ledger

    def stream(self) -> Iterator[Any]:
        for snapshot in self._inner.stream():
            self._ledger.count_docs(self._name)  # counted as it is streamed: a failure keeps the partial count
            yield snapshot

    def get(self) -> list:
        return list(self.stream())  # the real client's `get()` is a materialized stream

    def document(self, doc_id: str) -> _DocRef:
        return _DocRef(self._inner.document(doc_id), doc_id)


class CountingFirestore:
    """Read-only counting proxy of a Firestore client. Deliberately NOT a transparent proxy: there is no
    `__getattr__`, so a write (or any call this harness does not need) is an AttributeError, never a forward."""

    __slots__ = ("_inner", "_ledger")

    def __init__(self, inner: Any, ledger: ReadLedger) -> None:
        self._inner, self._ledger = inner, ledger

    def collection(self, name: str) -> _CountingCollection:
        return _CountingCollection(self._inner.collection(name), name, self._ledger)

    def get_all(self, refs: list) -> Iterator[Any]:
        for snapshot in self._inner.get_all([ref._inner for ref in refs]):
            self._ledger.count_np_lookup()
            yield snapshot


@contextmanager
def guard_blob(ledger: ReadLedger, *, put: str = "refuse") -> Iterator[ReadLedger]:
    """Counts Blob GETs and PUTs through `blob_lkg`. `put="refuse"` (live) raises `ReadOnlyViolation` on any PUT;
    `put="count"` (the simulation) counts it and reports success without uploading anything."""
    if put not in ("refuse", "count"):
        raise ValueError(f"put must be 'refuse' or 'count', got {put!r}")
    originals = {name: getattr(blob_lkg, name) for name in ("load_json", "load_json_private", "save_json")}

    def counting_get(original: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ledger.count_blob("get")
            return original(*args, **kwargs)
        return wrapper

    def counting_put(*_args: Any, **_kwargs: Any) -> bool:
        ledger.count_blob("put")
        if put == "refuse":
            raise ReadOnlyViolation("a Blob PUT was attempted: this harness is read-only")
        return True

    blob_lkg.load_json = counting_get(originals["load_json"])
    blob_lkg.load_json_private = counting_get(originals["load_json_private"])
    blob_lkg.save_json = counting_put
    try:
        yield ledger
    finally:
        for name, original in originals.items():
            setattr(blob_lkg, name, original)


def format_ledger_report(report: dict) -> str:
    return (f"LEDGER (one process, counts only): documents streamed E {report['E']} evaluaciones, I {report['I']} inspectores,"
            f" S {report['S']} survey_cali (other {report['other_docs']}); np lookups (get_all) {report['np_lookup']};"
            f" Atencion Sismo rows walked {report['api_rows']}; Blob GET {report['blob_get']}, PUT {report['blob_put']}")


def assemble_inputs(db: Any, *, fetch_rows: Callable[[], list], cargar_referencia: Callable[[], Any], hoy: date,
                    ledger: ReadLedger) -> Inputs:
    """The inputs `depurar()` gets in production, assembled exactly like `get_stickers_atencionsismo` does (same
    service functions, one scan of each collection) from READ-ONLY sources. `db` must be a `CountingFirestore`."""
    from app.routers import stickers as stickers_router
    from app.routers import stickers_atencionsismo as router
    from app.services.stickers_atencionsismo import build_evaluaciones

    roster_by_codigo, roster_by_cedula = stickers_router.inspector_profiles(db)
    # D33: the NP join of the evaluaciones scan is served from the roster just read (as the app's roster component
    # does); only the uids it cannot resolve go through `get_all`, and that is what the ledger's `np lookups` counts.
    evaluaciones_firestore = stickers_router.list_evaluaciones(
        db, np_lookup=stickers_router.roster_np_lookup(db, lambda: (roster_by_codigo, roster_by_cedula)))
    rows = fetch_rows()
    ledger.add_api_rows(len(rows))
    payload = build_evaluaciones(rows, roster_by_codigo=roster_by_codigo, evaluaciones_firestore=evaluaciones_firestore,
                                 roster_by_cedula=roster_by_cedula)
    nombres = router._nombres_survey(db)
    referencia = cargar_referencia()
    if not referencia.activa:
        raise HarnessError(f"reference bundle unavailable (motivo={referencia.motivo!r}): "
                           "a parity run against an empty bundle would prove nothing")
    return Inputs(stickers=router._stickers_para_depuracion(payload),
                  roster_by_cedula=router._roster_para_depuracion(roster_by_cedula),
                  nombres_survey=nombres, referencia=referencia, hoy=hoy)


# ── projection: the REAL component caches on a fake clock (design O1, D33, D34) ─────────

# O1 (design "Open Questions"): the acceptance criterion is per OPEN HOUR, not per day: flag ON must not cost more than
# the flag-OFF baseline of about 20,000 Firestore reads per open hour (production today, ~12 refreshes/hour of
# I + E + U documents). RECOMMENDED DEFAULT, PENDING OWNER CONFIRMATION; `--budget-per-hour` overrides it.
DEFAULT_BUDGET_PER_HOUR = 20_000
DEFAULT_OPEN_HOURS_PER_DAY = 8  # modeled workload: one admin tab open during a working day
# per continuously-open hour, static inputs (design "Efficiency Acceptance Budgets"); the scan limits count component
# REFRESHES, the `*_full_scans` limits count the ones that read the whole collection (a probe is not one)
HOURLY_LIMITS = {"roster": 2, "survey": 1, "evaluaciones": 4, "survey_full_scans": 1, "evaluaciones_full_scans": 1,
                 "walks": 12, "referencia_get": 2, "depurar": 1, "blob_put": 2}
PROBED = {"survey": ("survey_cali", "_updated_at"), "evaluaciones": ("evaluaciones", "timestamp")}


@dataclass(frozen=True)
class Ttls:
    """Seconds. `roster`, `survey`, `evaluaciones` and `referencia` are the component TTLs (D22/D24), `walk` the
    atencionsismo sticker cache's, `survey_reconcile` / `evaluaciones_reconcile` the forced full-reconcile interval of
    the probe-gated components (D34)."""

    roster: float
    survey: float
    evaluaciones: float
    referencia: float
    walk: float
    survey_reconcile: float = 6 * 3600.0
    evaluaciones_reconcile: float = 6 * 3600.0


@dataclass(frozen=True)
class Sizes:
    """Documents per full scan. `U` = inspector uids per evaluaciones scan that the roster component could NOT resolve
    (D33: they are read through one batched `get_all`; 0 when the roster covers every evaluación uid, which is what a
    `--live` run measures as `np lookups`)."""

    E: int
    I: int  # noqa: E741 - the design's own symbol
    S: int
    api_rows: int = 0
    U: int = 0


def default_ttls() -> Ttls:
    from app.routers import stickers as stickers_router
    from app.routers import stickers_atencionsismo as router
    from app.services import probed_scan

    return Ttls(roster=router.ROSTER_CACHE_TTL_SECONDS, survey=router.SURVEY_NAMES_CACHE_TTL_SECONDS,
                evaluaciones=router.EVALUACIONES_FS_CACHE_TTL_SECONDS, referencia=router.REFERENCIA_CACHE_TTL_SECONDS,
                walk=stickers_router.EVALUACIONES_CACHE_TTL_SECONDS,
                survey_reconcile=probed_scan.DEFAULT_RECONCILE_S, evaluaciones_reconcile=probed_scan.DEFAULT_RECONCILE_S)


def validate_ttls(ttls: Ttls) -> None:
    """A TTL of 0 or less is a configuration error, never "always fresh" (and never "always stale")."""
    for name, value in vars(ttls).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise HarnessError(f"TTL {name} must be a finite number of seconds > 0 (got {value!r})")


def probe_reads(documents: int) -> int:
    """Firestore bills a `count()` aggregation one read per 1000 index entries (minimum one) and the newest-document
    query one read (skipped for an empty collection): 3 reads at ~1.9k documents."""
    return 1 if documents == 0 else math.ceil(documents / 1000) + 1


@contextmanager
def _patched(module: Any, name: str, value: Any) -> Iterator[None]:
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, original)


def _components(ttls: dict | Ttls, clock: Any, calls: Any) -> dict[str, tuple[Any, Callable[[], Any]]]:
    """The three Firestore-derived components exactly as the app wires them: a `VersionedCache` per input and, for
    `survey` and `evaluaciones`, the real probe-gated `ProbedScan` over a static in-memory collection (one document, so
    the fake bills nothing: only calls are counted, the sizes are applied by `project_reads`)."""
    from app.services.probed_scan import ProbedScan
    from app.services.versioned_cache import VersionedCache
    from tests.ledger_fakes import LedgerFirestore

    ttl = ttls if isinstance(ttls, dict) else vars(ttls)
    db = LedgerFirestore({"survey_cali": {"a": {"_updated_at": 1}}, "evaluaciones": {"a": {"timestamp": 1}}}, calls)
    out: dict[str, tuple[Any, Callable[[], Any]]] = {}
    for name in ("roster", "survey", "evaluaciones"):
        cache = VersionedCache(name=name, ttl_s=ttl[name], clock=clock)
        if name in PROBED:
            collection, field = PROBED[name]
            source = ProbedScan(name=name, collection=collection, order_field=field,
                                reconcile_s=ttl[f"{name}_reconcile"], clock=clock)

            def fetch(name=name, source=source) -> Any:
                calls.hit(f"{name}:refresh")
                return source.fetch(db, lambda: (calls.hit(f"{name}:full_scan"), ["static"])[1])
        else:
            def fetch(name=name) -> Any:
                calls.hit(f"{name}:refresh")
                calls.hit(f"{name}:full_scan")
                return {"static": name}
        out[name] = (cache, fetch)
    return out


def _component_counts(calls: Any) -> dict[str, dict[str, int]]:
    return {name: {"refreshes": calls[f"{name}:refresh"], "full_scans": calls[f"{name}:full_scan"],
                   "probes": calls[f"count:{PROBED[name][0]}"] if name in PROBED else 0}
            for name in ("roster", "survey", "evaluaciones")}


def simulate_static_hour(ttls: Ttls | None = None, *, hour_s: int = 3600, tick_s: int = 1) -> dict:
    """One continuously-open hour with STATIC upstream inputs: a request every `tick_s` seconds drives the real
    `VersionedCache` components (with the real probe-gated sources), the real `EvaluacionesCache` (walk) and the real
    `DepuracionCache` (referencia + `depurar` key) on a fake clock. No real time passes, nothing is fetched or uploaded:
    the fetchers are counters (the shared `tests/ledger_fakes.py` fakes) and any Blob PUT is counted, not performed."""
    ttls = ttls or default_ttls()
    validate_ttls(ttls)
    if not (isinstance(hour_s, int) and isinstance(tick_s, int) and hour_s > 0 and tick_s > 0):
        raise HarnessError("hour_s and tick_s must be positive integers")
    from app.routers import stickers as stickers_router
    from app.routers import stickers_atencionsismo as router
    from tests.ledger_fakes import CallLedger, FakeClock

    clock, calls, blobs = FakeClock(1000.0), CallLedger(), ReadLedger()

    def counted(name: str, value: Any) -> Callable[[], Any]:
        return lambda: (calls.hit(name), value)[1]

    components = _components(ttls, clock, calls)
    bundle = referencia_svc.ReferenciaBundle(vercel=(), fase2=(), main=(), generado_en="2026-01-01", activa=True,
                                             motivo="", codigos_duplicados=(), huella="static")
    empty = dep.Depuracion(activa=True, motivo="", referencia_generada_en="", inspectores=(), grupo_externos=None,
                           alias_nombres={}, revision_manual=())
    walk_rows = [{"id": "static", "inspector": {}, "descripcion": {}}]
    hoy = date(2026, 1, 1)
    with _patched(router, "REFERENCIA_CACHE_TTL_SECONDS", ttls.referencia), \
            _patched(stickers_router, "EVALUACIONES_CACHE_TTL_SECONDS", ttls.walk), guard_blob(blobs, put="count"):
        depuracion = router.DepuracionCache(cargar_referencia=counted("referencia", bundle), clock=clock)
        walk = stickers_router.EvaluacionesCache(lkg_blob="data/parity-simulation.json", redact=router.redact_for_blob,
                                                 clock=clock)
        for second in range(0, hour_s, tick_s):
            clock.t = 1000.0 + second
            walk.get_or_fetch_snapshot(counted("walk", walk_rows))
            if walk._persist_thread is not None:  # the upload runs on a thread: join it so the PUT count is exact
                walk._persist_thread.join(timeout=5)
            versions = {name: cache.get(fetch).version for name, (cache, fetch) in components.items()}
            depuracion.resolve(inputs_version="static", roster_version=versions["roster"],
                               survey_version=versions["survey"], hoy=hoy,
                               compute=lambda referencia: (calls.hit("depurar"), empty)[1])
    counts = _component_counts(calls)
    return {"hour_s": hour_s, "ttls": vars(ttls).copy(),
            "scans": {name: counts[name]["refreshes"] for name in ("roster", "survey", "evaluaciones")},
            "full_scans": {name: counts[name]["full_scans"] for name in ("roster", "survey", "evaluaciones")},
            "probes": {name: counts[name]["probes"] for name in ("survey", "evaluaciones")},
            "walks": calls["walk"], "referencia_get": calls["referencia"], "depurar": calls["depurar"],
            "blob_put": blobs.report()["blob_put"]}


def simulate_static_day(ttls: dict, open_hours: float, *, tick_s: int = 1) -> dict:
    """The same real components over the whole modeled open day (cold start included), static inputs: refreshes, full
    scans and probes per component. Exact for any TTL (no "floor(hours / TTL) + 1" approximation) and it applies the
    forced reconcile of the probe-gated components."""
    from tests.ledger_fakes import CallLedger, FakeClock

    clock, calls = FakeClock(1000.0), CallLedger()
    components = _components(ttls, clock, calls)
    for second in range(0, int(open_hours * 3600), tick_s):
        clock.t = 1000.0 + second
        for cache, fetch in components.values():
            cache.get(fetch)
    return _component_counts(calls)


def _fmt(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.1f}"


def project_reads(simulation: dict, sizes: Sizes, *, open_hours_per_day: float = DEFAULT_OPEN_HOURS_PER_DAY,
                  budget_per_hour: int = DEFAULT_BUDGET_PER_HOUR, budget_per_day: int | None = None) -> dict:
    """Firestore reads per open hour and per day.

    - WORST CASE per open hour (`worst_case_reads_per_hour`, the gate): every refresh of the simulated hour finds a
      change, so it pays a full scan: `roster x I + survey x S + evaluaciones x (E + U)` (`worst_case_scan_reads_per_hour`)
      PLUS the probe each probe-gated refresh pays BEFORE the scan (a changed refresh pays probe AND scan): the simulated
      hour's probes x `probe_reads` (`worst_case_probe_reads_per_hour`). Independent of how often the data really
      changes; compared with `budget_per_hour` (design O1).
    - STATIC inputs over `open_hours_per_day` (`reads_per_day`, `reads_per_hour` = that day / the open hours): the same
      components on a fake clock over the whole day. The probe-gated survey and evaluaciones pay `probe_reads` per
      refresh and a full scan only at cold start and at the forced reconcile. Information, not a gate: a day in which
      the data changes at every refresh converges to the worst case.
    `budget_per_day`, when given, only adds an informational daily verdict."""
    if not budget_per_hour > 0 or (budget_per_day is not None and not budget_per_day > 0):
        raise HarnessError(f"budget must be > 0 (got per hour {budget_per_hour}, per day {budget_per_day})")
    if not 0 < open_hours_per_day <= 24:
        raise HarnessError(f"open hours per day must be in (0, 24] (got {open_hours_per_day})")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in vars(sizes).values()):
        raise HarnessError(f"collection size must be a non-negative integer (got {vars(sizes)})")
    scans = simulation["scans"]
    worst_scans = scans["roster"] * sizes.I + scans["survey"] * sizes.S + scans["evaluaciones"] * (sizes.E + sizes.U)
    probes = simulation["probes"]
    worst_probes = probes["survey"] * probe_reads(sizes.S) + probes["evaluaciones"] * probe_reads(sizes.E)
    worst = worst_scans + worst_probes
    design_formula = scans["roster"] * sizes.I + scans["survey"] * sizes.S + scans["evaluaciones"] * sizes.E
    open_hours_per_day = int(open_hours_per_day) if float(open_hours_per_day).is_integer() else open_hours_per_day
    day = simulate_static_day(simulation["ttls"], open_hours_per_day)
    per_day = (day["roster"]["full_scans"] * sizes.I
               + day["survey"]["full_scans"] * sizes.S + day["survey"]["probes"] * probe_reads(sizes.S)
               + day["evaluaciones"]["full_scans"] * (sizes.E + sizes.U)
               + day["evaluaciones"]["probes"] * probe_reads(sizes.E))
    per_hour = per_day / open_hours_per_day
    worst_per_day = worst * open_hours_per_day
    measured = {"roster": scans["roster"], "survey": scans["survey"], "evaluaciones": scans["evaluaciones"],
                "survey_full_scans": simulation["full_scans"]["survey"],
                "evaluaciones_full_scans": simulation["full_scans"]["evaluaciones"],
                "walks": simulation["walks"], "referencia_get": simulation["referencia_get"],
                "depurar": simulation["depurar"], "blob_put": simulation["blob_put"]}
    limits = {name: {"measured": value, "limit": HOURLY_LIMITS[name], "ok": value <= HOURLY_LIMITS[name]}
              for name, value in measured.items()}
    arithmetic_day = (
        f"roster {day['roster']['full_scans']} x {sizes.I} (I)"
        f" + survey {day['survey']['full_scans']} scans x {sizes.S} (S) + {day['survey']['probes']} probes x {probe_reads(sizes.S)}"
        f" + evaluaciones {day['evaluaciones']['full_scans']} scans x ({sizes.E} + {sizes.U}) (E + U)"
        f" + {day['evaluaciones']['probes']} probes x {probe_reads(sizes.E)} = {per_day:,}")
    return {
        "arithmetic_worst_case": (f"{scans['roster']} x {sizes.I} (I) + {scans['survey']} x {sizes.S} (S)"
                                  f" + {scans['evaluaciones']} x ({sizes.E} + {sizes.U}) (E + U)"
                                  f" + {probes['survey']} survey probes x {probe_reads(sizes.S)}"
                                  f" + {probes['evaluaciones']} evaluaciones probes x {probe_reads(sizes.E)} = {worst:,}"),
        "arithmetic_per_day": arithmetic_day,
        "worst_case_reads_per_hour": worst, "worst_case_reads_per_day": worst_per_day,
        "worst_case_scan_reads_per_hour": worst_scans, "worst_case_probe_reads_per_hour": worst_probes,
        "reads_per_hour_design_formula": design_formula,
        "reads_per_hour": int(per_hour) if float(per_hour).is_integer() else round(per_hour, 1),
        "reads_per_day": per_day, "open_hours_per_day": open_hours_per_day,
        "budget_per_hour": budget_per_hour, "budget_per_day": budget_per_day,
        "verdict": "PASS" if worst <= budget_per_hour else "FAIL",
        "verdict_per_day": None if budget_per_day is None else ("PASS" if per_day <= budget_per_day else "FAIL"),
        "scans_per_day": {name: day[name]["refreshes"] for name in ("roster", "survey", "evaluaciones")},
        "full_scans_per_day": {name: day[name]["full_scans"] for name in ("roster", "survey", "evaluaciones")},
        "probes_per_day": {name: day[name]["probes"] for name in ("survey", "evaluaciones")},
        "limits": limits, "limits_ok": all(v["ok"] for v in limits.values()), "simulation": simulation,
    }


def format_projection(projection: dict) -> str:
    sim, limits = projection["simulation"], projection["limits"]
    scans = sim["scans"]
    hours = projection["open_hours_per_day"]
    lines = [
        f"PROJECTION (one process, static inputs, real component caches on a fake clock, {sim['hour_s']} s):",
        f"  per open hour: refreshes roster {scans['roster']}, survey {scans['survey']}, evaluaciones {scans['evaluaciones']}"
        f" (full scans survey {sim['full_scans']['survey']}, evaluaciones {sim['full_scans']['evaluaciones']});"
        f" API walks {sim['walks']}; referencia GET {sim['referencia_get']}; depurar {sim['depurar']}; Blob PUT {sim['blob_put']}",
        "  hourly limits: " + ("all within" if projection["limits_ok"] else "EXCEEDED " + ", ".join(
            f"{n} {v['measured']}>{v['limit']}" for n, v in limits.items() if not v["ok"])),
        f"  worst case per open hour (every refresh finds a change: a probe AND a scan): {projection['arithmetic_worst_case']}"
        f" (design formula 2*I + S + 4*E = {projection['reads_per_hour_design_formula']:,})",
        f"  static inputs over {hours} open h (probe-gated survey and evaluaciones, forced reconcile every"
        f" {sim['ttls']['survey_reconcile'] / 3600:g} h): {projection['arithmetic_per_day']} per day"
        f" = {_fmt(projection['reads_per_hour'])} per open hour",
        f"  budget per open hour: {projection['budget_per_hour']:,} (recommended default, pending owner confirmation: the"
        f" flag-OFF baseline) -> {projection['verdict']} (worst case {projection['worst_case_reads_per_hour']:,})",
        f"  daily projection, information only: {projection['reads_per_day']:,} reads over {hours} open h with static inputs;"
        f" worst case {projection['worst_case_reads_per_day']:,}",
    ]
    if projection["verdict_per_day"] is not None:
        lines.append(f"  vs the daily budget {projection['budget_per_day']:,}: {projection['verdict_per_day']}")
    return "\n".join(lines)


# ── snapshot file, live sources, CLI ────────────────────────────────────────

LIVE_ENV_FIREBASE = "FIREBASE_SERVICE_ACCOUNT_JSON"  # app.credentials.clients._ENV_VARS["sismo"]
LIVE_ENV_API_PASS = "VISITADOS_API_PASS"  # app.services.atencionsismo.PASS_ENV
LIVE_ENV_BLOB = ("BLOB_PRIVATE_TOKEN", "BLOB_READ_WRITE_TOKEN")  # the private read takes either, like blob_sync
_SIZE_FIELDS = ("E", "I", "S", "api_rows", "U")


def load_snapshot(path: str | os.PathLike) -> tuple[Inputs, Sizes | None]:
    """Inputs (and, optionally, the measured collection sizes under `ledger`) from a local JSON file:
    `{stickers, roster_by_cedula, nombres_survey, referencia (the published schema-1 bundle), hoy, [ledger]}`."""
    path = Path(path)
    if not path.is_file():
        raise HarnessError(f"snapshot not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessError(f"cannot read snapshot {path.name} ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise HarnessError("snapshot must be a JSON object")
    for key, kind in (("stickers", list), ("roster_by_cedula", dict), ("nombres_survey", list), ("referencia", dict),
                      ("hoy", str)):
        if key not in data:
            raise HarnessError(f"snapshot is missing {key!r}")
        if not isinstance(data[key], kind):
            raise HarnessError(f"snapshot {key!r} must be a {kind.__name__}")
    try:
        hoy = date.fromisoformat(data["hoy"])
    except ValueError:
        raise HarnessError("snapshot 'hoy' must be an ISO date (YYYY-MM-DD)") from None
    referencia = referencia_svc.parse_bundle(data["referencia"])
    if referencia is None:
        raise HarnessError("snapshot 'referencia' is not a valid schema-1 reference bundle")
    sizes = None
    if "ledger" in data:
        raw = data["ledger"]
        if (not isinstance(raw, dict) or not {"E", "I", "S"} <= set(raw) or set(raw) - set(_SIZE_FIELDS)
                or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in raw.values())):
            raise HarnessError(f"snapshot 'ledger' must be an object with non-negative integers E, I, S and optionally "
                               f"{[f for f in _SIZE_FIELDS if f not in ('E', 'I', 'S')]}")
        sizes = Sizes(**raw)
    return Inputs(stickers=data["stickers"], roster_by_cedula=data["roster_by_cedula"],
                  nombres_survey=data["nombres_survey"], referencia=referencia, hoy=hoy), sizes


def check_live_env(environ: Any) -> None:
    """`--live` reads credentials from the environment ONLY (it never loads a .env file). Reports names, never values."""
    present = lambda name: bool(str(environ.get(name, "") or "").strip())  # noqa: E731
    missing = [n for n in (LIVE_ENV_FIREBASE, LIVE_ENV_API_PASS) if not present(n)]
    if not any(present(n) for n in LIVE_ENV_BLOB):
        missing.append(f"{LIVE_ENV_BLOB[0]} (or {LIVE_ENV_BLOB[1]})")
    if missing:
        raise HarnessError("--live needs credentials in the environment (this script never reads a .env file); "
                           "missing: " + ", ".join(missing))


def _firestore_client() -> Any:
    from app.credentials import clients

    return clients.sismo().firestore


def _fetch_live_rows() -> list:
    import asyncio

    import httpx

    from app.services import atencionsismo

    user, password = atencionsismo.credentials_from_env()

    async def pull() -> list:
        async with httpx.AsyncClient() as client:
            return await atencionsismo.fetch_stickers(client, user, password)

    return asyncio.run(pull())


def collect_live(environ: Any, ledger: ReadLedger, *, db_factory: Callable[[], Any] | None = None,
                 fetch_rows: Callable[[], list] | None = None, cargar_referencia: Callable[[], Any] | None = None,
                 hoy: date | None = None) -> Inputs:
    """Assembles the inputs from the live READ-ONLY sources. Firestore only through the counting proxy (no write
    surface), Blob only as a counted GET, any PUT refused. Not exercised by the tests against live services."""
    from datetime import datetime

    from app.services import fechas_es_co

    with guard_blob(ledger, put="refuse"):
        return assemble_inputs(
            CountingFirestore((db_factory or _firestore_client)(), ledger),
            fetch_rows=fetch_rows or _fetch_live_rows,
            cargar_referencia=cargar_referencia or (lambda: referencia_svc.cargar_referencia(load_json=blob_lkg.load_json_private)),
            hoy=hoy or datetime.now(fechas_es_co.BOGOTA).date(), ledger=ledger)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parity_inspectores_depurado",
        description="Parity, determinism, timing and read-ledger checks for the depurado base (read-only, no PII printed).")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--live", action="store_true",
                        help="assemble the inputs from live read-only sources (credentials from the environment only)")
    source.add_argument("--snapshot", type=Path, help="read the inputs from a local JSON snapshot")
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX, help="the notebook's reference xlsx")
    parser.add_argument("--skip-parity", action="store_true", help="skip the comparison with the xlsx (said in the output)")
    parser.add_argument("--timing-runs", type=int, default=TIMING_MIN_RUNS)
    parser.add_argument("--timing-ceiling", type=float, default=TIMING_CEILING_S, help="seconds")
    parser.add_argument("--budget-per-hour", type=int, default=DEFAULT_BUDGET_PER_HOUR,
                        help="Firestore reads per open hour (design O1; recommended default 20,000 = the flag-OFF "
                             "baseline, pending owner confirmation)")
    parser.add_argument("--budget-per-day", type=int, default=None,
                        help="optional: also print a verdict against a daily budget (information only)")
    parser.add_argument("--open-hours-per-day", type=float, default=DEFAULT_OPEN_HOURS_PER_DAY,
                        help="modeled workload: hours per day with an admin tab open")
    for name in Ttls.__dataclass_fields__:
        parser.add_argument(f"--ttl-{name}", type=float, default=None, help=f"override the {name} TTL in seconds (must be > 0)")
    return parser


def _ttls_from_args(args: argparse.Namespace) -> Ttls:
    import dataclasses

    overrides = {name: getattr(args, f"ttl_{name}") for name in Ttls.__dataclass_fields__
                 if getattr(args, f"ttl_{name}") is not None}
    ttls = dataclasses.replace(default_ttls(), **overrides)
    validate_ttls(ttls)
    return ttls


def _run_checks(args: argparse.Namespace, inputs: Inputs, ledger: ReadLedger, sizes: Sizes | None,
                reference: ReferenceXlsx | None, ttls: Ttls) -> int:
    failed = False
    if reference is None:
        print("PARITY SKIPPED (--skip-parity): no comparison with the notebook xlsx was made")
    else:
        depurado = inputs.run()
        report = compare(depurado, reference.rows, build_register(inputs, depurado),
                         externos_rows=reference.externos or None, n_colapsados_notebook=reference.n_colapsados)
        print(format_parity_report(report))
        failed |= not report["passed"]
    determinism = check_determinism(inputs)
    print(format_determinism_report(determinism))
    timing = check_timing(inputs, runs=args.timing_runs, ceiling_s=args.timing_ceiling)
    print(format_timing_report(timing))
    failed |= not (determinism["ok"] and timing["ok"])
    if args.live:
        print(format_ledger_report(ledger.report()))
    if sizes is None:
        print("PROJECTION SKIPPED: no measured collection sizes (run --live, or add a 'ledger' object to the snapshot)")
    else:
        projection = project_reads(simulate_static_hour(ttls), sizes, open_hours_per_day=args.open_hours_per_day,
                                   budget_per_hour=args.budget_per_hour, budget_per_day=args.budget_per_day)
        print(format_projection(projection))
        failed |= projection["verdict"] != "PASS" or not projection["limits_ok"] or projection["verdict_per_day"] == "FAIL"
    print("OVERALL " + ("FAIL" if failed else "PASS"))
    return EXIT_CHECK_FAILED if failed else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ledger = ReadLedger()
    collecting = False
    try:
        ttls = _ttls_from_args(args)  # configuration first: a bad TTL never reaches a check
        if args.live:
            check_live_env(os.environ)
            collecting = True
            inputs = collect_live(os.environ, ledger)
            report = ledger.report()
            sizes: Sizes | None = Sizes(E=report["E"], I=report["I"], S=report["S"], api_rows=report["api_rows"],
                                        U=report["np_lookup"])
        else:
            inputs, sizes = load_snapshot(args.snapshot)
        reference = None if args.skip_parity else load_reference_xlsx(args.xlsx)  # before any output: nothing runs on a bad reference
        return _run_checks(args, inputs, ledger, sizes, reference, ttls)
    except HarnessError as exc:
        message = str(exc)
    except Exception as exc:  # noqa: BLE001 - a live source failed: report the TYPE only, its message can quote data
        message = f"{type(exc).__name__} (message withheld: it may quote data)"
    if collecting:
        print(format_ledger_report(ledger.report()))  # the partial count of a run that failed while reading
    print(f"ERROR: {message}", file=sys.stderr)
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
