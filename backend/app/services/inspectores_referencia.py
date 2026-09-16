"""Referencia bundle I/O seam for `inspectores-depurado` (design.md D8 /
File Changes / Interfaces-Contracts).

`ReferenciaBundle` is the single atomic snapshot of the three reference
sources (Vercel roster, Fase 2 verified listing, base "main" CSV), published
by `scripts/publicar_referencia_inspectores.py` to Vercel Blob at
`referencia/inspectores/bundle.json` with `access: 'private'` (D8) and read
back through `blob_lkg.load_json`, mirroring the last-known-good pattern
`reportes_historico.py`/`dashboard_refresh.py` already use for other Blob
datasets.

Two functions, two purities:
- `parse_bundle` is PURE — a `dict` in, a `ReferenciaBundle | None` out, no
  I/O, no exceptions.
- `cargar_referencia` is the I/O seam and NEVER raises.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.services import blob_lkg

BUNDLE_BLOB = "referencia/inspectores/bundle.json"

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EntradaReferencia:
    cedula_key: str
    nombre_norm: str
    np: str
    entidad: str
    codigo: str
    pasos: tuple[int, ...]
    no_persona: bool


@dataclass(frozen=True)
class ReferenciaBundle:
    vercel: tuple[EntradaReferencia, ...]
    fase2: tuple[EntradaReferencia, ...]
    main: tuple[EntradaReferencia, ...]
    generado_en: str
    activa: bool
    motivo: str
    codigos_duplicados: tuple[str, ...]

    @classmethod
    def vacia(cls, motivo: str = "") -> "ReferenciaBundle":
        """The degraded, fold-safe value every caller can rely on: every
        section empty, `activa=False`, `motivo` explaining why (D5)."""
        return cls(
            vercel=(),
            fase2=(),
            main=(),
            generado_en="",
            activa=False,
            motivo=motivo,
            codigos_duplicados=(),
        )


def _norm_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_entrada(raw: object) -> EntradaReferencia | None:
    """A single reference row. Missing/blank `cedula_key` -> `None` (skipped
    by the caller), never raised — spec: malformed rows are excluded, not
    fatal to the section. `main` rows carry `rango` instead of `np` (design's
    bundle example); `np` wins if both are present."""
    if not isinstance(raw, dict):
        return None
    cedula_key = raw.get("cedula_key")
    if not isinstance(cedula_key, str) or not cedula_key.strip():
        return None
    np_valor = raw.get("np")
    if np_valor in (None, ""):
        np_valor = raw.get("rango")
    pasos_raw = raw.get("pasos") or []
    try:
        pasos = tuple(int(paso) for paso in pasos_raw)
    except (TypeError, ValueError):
        pasos = ()
    return EntradaReferencia(
        cedula_key=cedula_key.strip(),
        nombre_norm=_norm_str(raw.get("nombre_norm")),
        np=_norm_str(np_valor),
        entidad=_norm_str(raw.get("entidad")),
        codigo=_norm_str(raw.get("codigo")),
        pasos=pasos,
        no_persona=bool(raw.get("no_persona", False)),
    )


def _parse_seccion(raw_seccion: object) -> tuple[EntradaReferencia, ...]:
    """A section that isn't a list (missing, malformed, wrong type) degrades
    to an empty tuple rather than failing the whole bundle — spec: a
    malformed section leaves ONLY that section absent."""
    if not isinstance(raw_seccion, list):
        return ()
    entradas = []
    for fila in raw_seccion:
        entrada = _parse_entrada(fila)
        if entrada is not None:
            entradas.append(entrada)
    return tuple(entradas)


def parse_bundle(raw: dict) -> ReferenciaBundle | None:
    """Pure schema validation. `None` on any shape error at the BUNDLE level
    (not a dict, or an unrecognized `schema`) — spec: an unknown `schema`
    value makes the whole bundle absent, never a crash."""
    if not isinstance(raw, dict):
        return None
    if raw.get("schema") != _SCHEMA_VERSION:
        return None
    codigos_raw = raw.get("codigos_duplicados") or []
    if not isinstance(codigos_raw, list):
        codigos_raw = []
    codigos_duplicados = tuple(
        _norm_str(codigo) for codigo in codigos_raw if _norm_str(codigo)
    )
    return ReferenciaBundle(
        vercel=_parse_seccion(raw.get("vercel")),
        fase2=_parse_seccion(raw.get("fase2")),
        main=_parse_seccion(raw.get("main")),
        generado_en=_norm_str(raw.get("generado_en")),
        activa=True,
        motivo="",
        codigos_duplicados=codigos_duplicados,
    )


def _token_available() -> bool:
    """Independent of `blob_lkg._token_available()` (which only logs and
    returns a bool) because `cargar_referencia` needs to distinguish
    `sin_token` from `sin_blob` in its `motivo` BEFORE calling `load_json` —
    `blob_lkg.load_json` collapses every failure reason to a bare `None`."""
    return bool(os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip())


def cargar_referencia(*, load_json=blob_lkg.load_json_private, ahora=None) -> ReferenciaBundle:
    """The I/O seam. NEVER raises (D5) — every failure mode degrades to
    `ReferenciaBundle.vacia(motivo=...)`:

    Phase 3 fix (router-wiring apply pass, 2026-09-16): the default was
    `blob_lkg.load_json` (an UNAUTHENTICATED GET to the public CDN host),
    which cannot actually read an `access:'private'` blob — D8 explicitly
    requires "lectura autenticada con BLOB_READ_WRITE_TOKEN". Switched to
    `blob_lkg.load_json_private` (sends the Bearer token on the GET). Every
    Phase 1 test injects its own `load_json` fake, so this default-only
    change breaks nothing; production behavior changes from "always
    degrades to sin_blob against a private bundle" to "actually reads it".
    - `sin_token`: `BLOB_READ_WRITE_TOKEN` unset, checked before calling
      `load_json` since `blob_lkg.load_json` itself doesn't distinguish WHY
      it returned `None`.
    - `sin_blob`: `load_json` returned `None` (missing blob, network,
      malformed JSON already handled inside `blob_lkg`).
    - `manifiesto_invalido`: `load_json` raised, or returned a non-dict
      payload (garbage that isn't even a shape `parse_bundle` can look at).
    - `esquema_invalido`: a dict was returned but `parse_bundle` rejected it
      (unknown `schema`).

    `ahora` is accepted for forward-compat with a future TTL/staleness check
    (Data Flow diagram: "cargar_referencia (30 min)") but is not used yet —
    that cadence lives in the router's `DepuracionCache` (Phase 3, D4/D6),
    not in this I/O seam.
    """
    if not _token_available():
        return ReferenciaBundle.vacia(motivo="sin_token")
    try:
        raw = load_json(BUNDLE_BLOB, dict)
    except Exception as exc:  # noqa: BLE001 - never propagate, degrade instead
        logging.warning(
            "inspectores_referencia: fallo leyendo %s (%s); degradando a bundle vacío",
            BUNDLE_BLOB, exc,
        )
        return ReferenciaBundle.vacia(motivo="manifiesto_invalido")
    if raw is None:
        return ReferenciaBundle.vacia(motivo="sin_blob")
    if not isinstance(raw, dict):
        return ReferenciaBundle.vacia(motivo="manifiesto_invalido")
    bundle = parse_bundle(raw)
    if bundle is None:
        return ReferenciaBundle.vacia(motivo="esquema_invalido")
    return bundle
