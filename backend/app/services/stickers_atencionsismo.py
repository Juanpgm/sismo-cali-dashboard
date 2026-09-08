"""Normalize atencionsismo `GET /api/informe/stickers` rows into the
dashboard's evaluaciones shape (design D1/D2 in
docs/superpowers/specs/2026-09-08-atencionsismo-reportes-ciudadanos-stickers-design.md).

Pure functions, no I/O. Fase I/II input (`inspector.np`) is derived in
priority order: matched Firestore evaluación -> roster NP by the 3-digit
inspector code embedded in our sticker code -> "" (the UI renders "sin
dato" for this source when np is empty; see web/js/evaluaciones.js faseDe).
"""
from __future__ import annotations

import re
from typing import Any

# Our field-form code: 76001-{area}-{inspector 3 digits}{consecutivo 4+ digits}
# (formulario/js/logic.js buildCodigo). atencionsismo re-exports it verbatim
# as `numero` for stickers it imported from our Firebase (origen "firebase").
CODIGO_RE = re.compile(r"^(?P<municipio>\d{5})-(?P<area>\d)-(?P<inspector>\d{3})(?P<consecutivo>\d{4,})$")

COLOR_TO_CLASE = {"verde": "INSPECCIONADA", "amarillo": "USO_RESTRINGIDO", "rojo": "INSEGURO"}

# Values the API substitutes when a field is missing (contract §Stickers JSON).
PLACEHOLDERS = frozenset({"Sin código", "Sin dirección", "Sin identificar"})


def parse_codigo(numero: object) -> dict[str, Any] | None:
    if not isinstance(numero, str):
        return None
    m = CODIGO_RE.match(numero.strip())
    if not m:
        return None
    return {
        "municipio": m.group("municipio"),
        "area": m.group("area"),
        "codigo_inspector": m.group("inspector"),
        "consecutivo": int(m.group("consecutivo")),
    }


def _clean(value: object) -> str:
    text = str(value or "").strip()
    return "" if text in PLACEHOLDERS else text


def _float_or_none(value: object) -> float | None:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return out


def _coords(row: dict) -> dict[str, Any] | None:
    lat = _float_or_none(row.get("latitud"))
    lng = _float_or_none(row.get("longitud"))
    if lat is None or lng is None or (lat == 0 and lng == 0):
        return None
    return {"lat": lat, "lng": lng, "accuracy": None}


def normalize_sticker(
    row: dict,
    *,
    np_by_codigo: dict[str, str],
    evaluacion_by_codigo: dict[str, dict],
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    sticker_id = str(row.get("id") or "").strip()
    if not sticker_id:
        return None

    codigo = _clean(row.get("numero"))
    parsed = parse_codigo(codigo)
    match = evaluacion_by_codigo.get(codigo) if codigo else None
    insp_match = (match or {}).get("inspector") or {}
    codigo_inspector = str(insp_match.get("codigo") or (parsed or {}).get("codigo_inspector") or "")
    np_value = str(insp_match.get("np") or "").strip() or np_by_codigo.get(codigo_inspector, "")

    clase = COLOR_TO_CLASE.get(str(row.get("color") or "").strip().lower(), "")
    desc_match = (match or {}).get("descripcion") or {}
    acc_match = (match or {}).get("acciones_posteriores") or {}

    return {
        "id": sticker_id,
        "fuente": "atencionsismo",
        "origen": str(row.get("origen") or "").strip(),
        "color_etiqueta": str(row.get("colorEtiqueta") or "").strip(),
        "codigo_edificacion": codigo,
        "consecutivo": (match or {}).get("consecutivo") if match else (parsed or {}).get("consecutivo"),
        "municipio": (match or {}).get("municipio") or (parsed or {}).get("municipio") or "",
        "area": (match or {}).get("area") if match else (parsed or {}).get("area"),
        "area_nombre": (match or {}).get("area_nombre") or "",
        "clasificacion": (match or {}).get("clasificacion") or clase,
        "alcance": (match or {}).get("alcance") or "",
        "coords": _coords(row) or (match or {}).get("coords"),
        "inspector": {
            "uid": str(insp_match.get("uid") or ""),
            "codigo": codigo_inspector,
            "nombre_completo": str(insp_match.get("nombre_completo") or ""),
            "identificacion": str(insp_match.get("identificacion") or ""),
            "entidad": str(insp_match.get("entidad") or ""),
            "np": np_value,
        },
        "descripcion": {
            "nombre": _clean(desc_match.get("nombre")) or _clean(row.get("personaAfectada")),
            "direccion": _clean(row.get("direccion")) or _clean(desc_match.get("direccion")),
        },
        "restricciones": (match or {}).get("restricciones") or "",
        "acciones_posteriores": {
            "barricadas": bool(acc_match.get("barricadas")),
            "evaluacion_detallada": bool(acc_match.get("evaluacion_detallada")),
        },
        "comentarios": (match or {}).get("comentarios") or "",
        "fotos": list((match or {}).get("fotos") or []),
        "fecha": (match or {}).get("fecha"),
    }


def build_evaluaciones(
    rows: list,
    *,
    np_by_codigo: dict[str, str],
    evaluaciones_firestore: list[dict],
) -> list[dict[str, Any]]:
    by_codigo = {
        str(e.get("codigo_edificacion") or ""): e
        for e in evaluaciones_firestore
        if isinstance(e, dict) and e.get("codigo_edificacion")
    }
    out: list[dict[str, Any]] = []
    for row in rows:
        normalized = normalize_sticker(row, np_by_codigo=np_by_codigo, evaluacion_by_codigo=by_codigo)
        if normalized is not None:
            out.append(normalized)
    out.sort(key=lambda e: str(e.get("fecha") or ""), reverse=True)
    return out
