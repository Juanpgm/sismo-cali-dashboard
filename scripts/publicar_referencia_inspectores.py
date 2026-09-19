#!/usr/bin/env python3
"""One-shot CLI: publishes the `seguimiento-inspectores-depurado` reference
bundle (design.md D8 / File Changes / "Blob reference bundle").

Reads the three source files (Vercel roster, Fase 2 verified listing, base
"main" export), normalizes them into the schema
`app.services.inspectores_referencia.parse_bundle` expects, and uploads the
result to Vercel Blob at `referencia/inspectores/bundle.json` with
`access: 'private'` — never a public URL (D8).

Manual, NOT a cron job (design's Migration/Rollout: run once before flipping
`SEGUIMIENTO_DEPURACION`). Uses `BLOB_PRIVATE_TOKEN` (falls back to
`BLOB_READ_WRITE_TOKEN`) from the environment for a real publish — the same
variable the backend reads the bundle with; `--dry-run` needs no token at all (it never
reaches the upload step).

Usage:
    python scripts/publicar_referencia_inspectores.py \\
        --vercel "context/EDA_HAMON/inspectores_vercel-app.csv" \\
        --fase2 "context/EDA_HAMON/Listado verificado Fase 2.xlsx" \\
        --main "context/EDA_HAMON/tecnicos atension sismo (12 sept 2026).csv" \\
        [--generado-en 2026-09-16] [--dry-run]

Column mapping (verified against the real source files, 2026-09-16):
  vercel: identificacion|cedula, nombre_completo|nombre, NP, entidad, codigo
  fase2:  identificacion|cedula, nombre_completo, NP
  main:   cedula, nombre, addlInfo.rango, addlInfo.matriculaProfesional|
          addlInfo.matricula, addlInfo.entidad, correo, telefono, codigoInspector, creadoEn, id

`pasos` (design's bundle JSON example, under `fase2`) is intentionally still
NOT emitted in this slice: `app.services.inspectores_depuracion`'s six seams
never read `EntradaReferencia.pasos` today (grep finds no `.pasos` use outside
`_parse_entrada`). The real "Fase 2 verified listing" source has no comparable
column, and the real main CSV does have a `pasos` column but nothing consumes
it yet; `parse_bundle` already defaults an absent `pasos` to `()`.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
DEPLOY_DIR = REPO_ROOT / "deploy"
for _path in (BACKEND_DIR, DEPLOY_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.services import cedula_utils  # noqa: E402
from app.services import inspectores_referencia as referencia_svc  # noqa: E402
from app.services.inspectores_depuracion import (  # noqa: E402
    es_cuenta_no_persona,
    normalizar_nombre,
)

import blob_sync  # noqa: E402  (deploy/blob_sync.py; path set up above)

SCHEMA_VERSION = 1


def _limpiar(value: object) -> str:
    """Never raises on a pandas NaN (float) — `str(nan)` would otherwise
    produce the literal text "nan"."""
    if value is None:
        return ""
    if not isinstance(value, str):
        # Scalars only: `pd.isna` on an array-like returns an array (ambiguous
        # truth value). Covers float NaN, pd.NA and pd.NaT.
        try:
            if pd.api.types.is_scalar(value) and pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
    return str(value).strip()


def _sin_sufijo_decimal(texto: str) -> str:
    """Turns "31837630.0" into "31837630". A float-typed cell (Excel numeric, or a
    pandas column inferred as float64 because of one blank) stringifies with a
    ".0" tail; stripping non-digits from that would append a spurious "0".
    Only a lone ".0" at the end (the shared `cedula_utils` rule, review C3) is
    dropped: "166.000", "12345.00", "1.234.567" and "3.5" are left untouched (a
    dotted cédula is a thousands separator, not a float). Leading zeros are never
    added or removed here."""
    return cedula_utils.quitar_cola_flotante(texto)


def _campo_id(row: dict, columna: str) -> str:
    """One coercion for identifier-like optional fields (`telefono`, `codigo`,
    `id`, ...): blank/NaN/None -> "", whitespace stripped, a pure ".0" float
    tail dropped, leading zeros and every other character kept as-is."""
    return _sin_sufijo_decimal(_limpiar(row.get(columna)))


def _solo_digitos(value: object) -> str:
    return cedula_utils.solo_digitos(_limpiar(value))


def _cedula_key(row: dict, *columnas: str) -> str:
    for columna in columnas:
        digitos = _solo_digitos(row.get(columna))
        if digitos:
            return digitos
    return ""


def _nombre_norm_vercel(row: dict) -> str:
    """The Vercel row's normalized name. A pandas NaN is truthy, so `nombre_completo or nombre` used to hand the
    float over to `normalizar_nombre` (`str(nan)` -> "nan"): two rows with a missing name would then look like the
    same person. `_limpiar` maps every missing marker to "" first, so a blank `nombre_completo` falls back to
    `nombre` and a name that is blank in both is "" (which never pairs, see `_codigos_duplicados`)."""
    return normalizar_nombre(_limpiar(row.get("nombre_completo")) or _limpiar(row.get("nombre")))


def _fila_vercel(row: dict) -> dict[str, Any] | None:
    cedula_key = _cedula_key(row, "identificacion", "cedula")
    if not cedula_key:
        return None
    return {
        "cedula_key": cedula_key,
        "nombre_norm": _nombre_norm_vercel(row),
        "np": _limpiar(row.get("NP")),
        "entidad": _limpiar(row.get("entidad")),
        "codigo": _campo_id(row, "codigo"),
    }


def _fila_fase2(row: dict) -> dict[str, Any] | None:
    cedula_key = _cedula_key(row, "identificacion", "cedula")
    if not cedula_key:
        return None
    return {
        "cedula_key": cedula_key,
        "nombre_norm": normalizar_nombre(row.get("nombre_completo")),
        "np": _limpiar(row.get("NP")),
    }


def _fila_main(row: dict) -> dict[str, Any] | None:
    cedula_key = _cedula_key(row, "cedula")
    if not cedula_key:
        return None
    correo = _limpiar(row.get("correo"))
    nombre = _limpiar(row.get("nombre"))
    tarjeta = _limpiar(row.get("addlInfo.matriculaProfesional")) or _limpiar(row.get("addlInfo.matricula"))
    return {
        "cedula_key": cedula_key,
        "nombre_norm": normalizar_nombre(nombre),
        "rango": _limpiar(row.get("addlInfo.rango")),
        # design D12: `entidad` = Vercel-by-cedula > Firestore > main; this is
        # the source for main (real CSV column `addlInfo.entidad`).
        "entidad": _limpiar(row.get("addlInfo.entidad")),
        "tarjeta_profesional": tarjeta,
        "correo": correo,
        # Optional identity/contact fields (schema stays 1, additive). Sources
        # are read as strings, so codigo "021" keeps its leading zero; `id` and
        # `creado_en` are the raw export values (`id`, `creadoEn`).
        "nombre": nombre,
        "telefono": _campo_id(row, "telefono"),
        "codigo": _campo_id(row, "codigoInspector"),
        "creado_en": _campo_id(row, "creadoEn"),
        "id": _campo_id(row, "id"),
        # Precomputed at publish time (design's bundle JSON note): the
        # depuración pipeline never needs the raw correo for classification,
        # only this boolean.
        "no_persona": es_cuenta_no_persona(correo, nombre),
    }


def _codigos_duplicados(vercel_rows: list[dict], sin_cedula: list[dict] | tuple = ()) -> list[str]:
    """D-P1: the codes excluded from remap and routed to manual review, the union of two rules:

    1. a `codigo` shared by 2+ DIFFERENT Vercel cedulas (the original rule), and
    2. every `codigo` of a Vercel row whose `nombre_norm` occurs 2+ times among the Vercel rows: the notebook's
       `dup_vercel` (`vercel["nombre_norm"].duplicated(keep=False)`, cell 30), whose codes (`codigos_dup_v`, cell 67)
       have their remap OMITTED. A person registered twice in Vercel (two cedulas, two codes) is not one
       "code on two cedulas", so rule 1 alone never listed them (live parity run 2026-09-19: 097, 116, 123, 127,
       147, 148; 368/372 on the `codigo` column). Rows are counted, so a re-exported identical row pairs too.

    `sin_cedula` are the rows `construir_bundle` dropped for lacking a cedula (`{"nombre_norm", "codigo"}`): the
    notebook's frame keeps them, so they stay in the name count (a name twin of a cedula-less row is a duplicate)
    and their own code is listed, but they never reach `bundle["vercel"]`.

    Empty names never pair (two blank names are not one person) and blank codes are ignored. Codes are kept as
    exact strings ("097" and "97" are two codes, like the engine compares them); the result is sorted and
    deduplicated. `remapear_codigos` ALSO detects a repeated Vercel code at runtime; this list additionally
    carries what the runtime cannot see (the name rule needs the names, and an operator may hand-edit it)."""
    por_codigo: dict[str, set[str]] = {}
    por_nombre: dict[str, list[str]] = {}
    for fila in (*vercel_rows, *sin_cedula):
        codigo = fila.get("codigo") or ""
        nombre = fila.get("nombre_norm") or ""
        if nombre:
            por_nombre.setdefault(nombre, []).append(codigo)
        if codigo and fila.get("cedula_key"):
            por_codigo.setdefault(codigo, set()).add(fila["cedula_key"])
    duplicados = {codigo for codigo, cedulas in por_codigo.items() if len(cedulas) >= 2}
    for codigos in por_nombre.values():
        if len(codigos) >= 2:
            duplicados.update(codigo for codigo in codigos if codigo)
    return sorted(duplicados)


def construir_bundle(
    *,
    vercel_records: list[dict],
    fase2_records: list[dict],
    main_records: list[dict],
    generado_en: str,
    origen: dict[str, str],
) -> dict[str, Any]:
    """PURE — builds the raw dict `parse_bundle` reads. Each `*_records`
    argument is a plain list of dicts (e.g. `DataFrame.to_dict("records")`),
    never a DataFrame directly, so this stays testable with small
    list-of-dict fixtures — no pandas/file I/O needed in unit tests."""
    vercel_filas = [(r, _fila_vercel(r)) for r in vercel_records]
    vercel_rows = [f for _, f in vercel_filas if f is not None]
    vercel_sin_cedula = [{"nombre_norm": _nombre_norm_vercel(r), "codigo": _campo_id(r, "codigo")}
                         for r, f in vercel_filas if f is None]
    fase2_rows = [f for f in (_fila_fase2(r) for r in fase2_records) if f is not None]
    main_rows = [f for f in (_fila_main(r) for r in main_records) if f is not None]
    return {
        "schema": SCHEMA_VERSION,
        "generado_en": generado_en,
        "origen": origen,
        # Optional, additive (schema stays 1; `parse_bundle` ignores it): how many
        # source rows were dropped for lacking ANY digit in their cédula. Counts
        # only — a dropped row is never echoed (no PII). The parser drops blank
        # `cedula_key` rows too, so this count is the only trace of them.
        "descartados_sin_cedula": {
            "main": len(main_records) - len(main_rows),
            "vercel": len(vercel_records) - len(vercel_rows),
            "fase2": len(fase2_records) - len(fase2_rows),
        },
        "codigos_duplicados": _codigos_duplicados(vercel_rows, vercel_sin_cedula),
        "vercel": vercel_rows,
        "fase2": fase2_rows,
        "main": main_rows,
    }


def _leer_tabla(path: Path) -> list[dict]:
    # dtype=str is load-bearing: identifier columns (identificacion, cedula,
    # codigo) must never be float-inferred — a single blank turns the whole
    # column into float64 ("31837630" -> 31837630.0) and strips leading zeros
    # ("021" -> 21). Blanks stay NaN; `_limpiar` maps them to "".
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str)
    return df.to_dict("records")


def publicar(bundle: dict[str, Any], *, dry_run: bool, upload=blob_sync.upload) -> str | None:
    """Self-validates against `parse_bundle` BEFORE uploading anything —
    catching a shape bug here beats discovering it via a degraded
    `depuracion.activa=false` in production. Returns the Blob URL on a real
    publish, `None` on `--dry-run`."""
    validado = referencia_svc.parse_bundle(bundle)
    if validado is None:
        raise SystemExit("El bundle construido no pasa parse_bundle() — no se publica.")
    print(
        f"Bundle valido: {len(bundle['vercel'])} vercel, {len(bundle['fase2'])} fase2, "
        f"{len(bundle['main'])} main, {len(bundle['codigos_duplicados'])} codigos_duplicados."
    )
    descartados = bundle.get("descartados_sin_cedula")
    if isinstance(descartados, dict):
        print(
            "Filas descartadas por no tener cedula (solo conteo): "
            f"main={descartados.get('main', 0)} vercel={descartados.get('vercel', 0)} "
            f"fase2={descartados.get('fase2', 0)}."
        )
    if dry_run:
        print("--dry-run: no se sube nada.")
        return None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(bundle, fh, ensure_ascii=False)
            tmp = fh.name
        url = upload(tmp, referencia_svc.BUNDLE_BLOB, 0, "application/json", access="private")
        print(f"Publicado: {referencia_svc.BUNDLE_BLOB} ({url})")
        return url
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vercel", required=True, type=Path, help="inspectores_vercel-app.csv")
    parser.add_argument("--fase2", required=True, type=Path, help="Listado verificado Fase 2.xlsx")
    parser.add_argument("--main", required=True, type=Path, help="tecnicos atencion sismo (...).csv")
    parser.add_argument("--generado-en", default=None, help="ISO date; defaults to today (UTC)")
    parser.add_argument("--dry-run", action="store_true", help="Build and validate only; never uploads")
    args = parser.parse_args(argv)

    generado_en = args.generado_en or datetime.now(timezone.utc).date().isoformat()
    bundle = construir_bundle(
        vercel_records=_leer_tabla(args.vercel),
        fase2_records=_leer_tabla(args.fase2),
        main_records=_leer_tabla(args.main),
        generado_en=generado_en,
        origen={"vercel": args.vercel.name, "fase2": args.fase2.name, "main": args.main.name},
    )
    publicar(bundle, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
