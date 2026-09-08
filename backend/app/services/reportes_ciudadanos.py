"""Projection of atencionsismo informe/json records into the lightweight
public snapshot the "Reportes ciudadanos" tab reads (design D5 in
docs/superpowers/specs/2026-09-08-atencionsismo-reportes-ciudadanos-stickers-design.md).
Input is the already PII/heavy-stripped record `dashboard_refresh._raw_record_mapper`
produces; this narrows it further to what the tab renders and parses the
es-CO formatted creation date into ISO 8601 (Bogota, UTC-5)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

DESCRIPCION_MAX = 500
BOGOTA = timezone(timedelta(hours=-5))
_DIGIT_RUN_RE = re.compile(r"\d{7,}")


def mask_digit_runs(texto: str) -> str:
    """Blank phone numbers / cedulas citizens type into the free text (7+
    consecutive digits) before the text reaches the PUBLIC snapshot. Task 0
    measured ~2% of descriptions carrying one."""
    return _DIGIT_RUN_RE.sub("***", texto or "")


_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
          "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}

# "martes, 18 de agosto de 2026, 06:33 p. m." — weekday optional, NBSP
# tolerated, "a. m."/"p. m." with or without the inner space/dots.
_FECHA_RE = re.compile(
    r"^(?:[a-záéíóúü]+,\s*)?(?P<d>\d{1,2})\s+de\s+(?P<m>[a-záéíóúü]+)\s+de\s+(?P<y>\d{4}),?\s*"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})\s*(?P<ap>[ap])\.?\s*m\.?$",
    re.IGNORECASE,
)

_STICKER_KEYS = (("numero", "numero"), ("color", "color"), ("colorEtiqueta", "etiqueta"),
                 ("origen", "origen"), ("clasificacion", "clasificacion"))


def parse_fecha_es_co(texto: object) -> str | None:
    if not isinstance(texto, str):
        return None
    # NBSP (U+00A0) shows up before "p. m."/"a. m." in real API responses
    # (Task 0); normalize it to a regular space before matching/stripping.
    normalized = texto.replace(" ", " ").strip()
    m = _FECHA_RE.match(normalized)
    if not m:
        return None
    mes = _MESES.get(m.group("m").lower())
    if mes is None:
        return None
    hora = int(m.group("h")) % 12
    if m.group("ap").lower() == "p":
        hora += 12
    try:
        dt = datetime(int(m.group("y")), mes, int(m.group("d")), hora, int(m.group("mi")), tzinfo=BOGOTA)
    except ValueError:
        return None
    return dt.isoformat()


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str:
    return str(value or "").strip()


def project_reporte(rep: object) -> dict[str, Any] | None:
    if not isinstance(rep, dict):
        return None
    rid = _text(rep.get("id"))
    if not rid:
        return None
    sticker = rep.get("sticker") if isinstance(rep.get("sticker"), dict) else {}
    lat = _float_or_none(rep.get("lat"))
    lng = _float_or_none(rep.get("lng"))
    if lat is None or lng is None:
        lat = _float_or_none(rep.get("latitud"))
        lng = _float_or_none(rep.get("longitud"))
    creado_texto = _text(rep.get("fechaCreacion"))
    return {
        "id": rid,
        "direccion": _text(rep.get("direccion")),
        "barrio": _text(rep.get("barrio")),
        "comuna": _text(rep.get("comuna")),
        "estado": _text(rep.get("estadoVerificacion")),
        "afectacion": _text(rep.get("afectacion")),
        "tipo_inmueble": _text(rep.get("tipoInmueble")),
        "nombre_edificio": _text(rep.get("nombreEdificio")),
        "lat": lat,
        "lng": lng,
        "creado": parse_fecha_es_co(creado_texto),
        "creado_texto": creado_texto,
        "habitabilidad": _text(rep.get("habitabilidad")),
        "visitado": _text(rep.get("visitado")).lower() in ("sí", "si", "true", "1"),
        "pudo_evaluar": _text(rep.get("pudoEvaluar")),
        "alcance": _text(rep.get("alcanceInspeccion")),
        "descripcion": mask_digit_runs(_text(rep.get("descripcion")))[:DESCRIPCION_MAX],
        "sticker": {out_key: _text(sticker.get(in_key)) for in_key, out_key in _STICKER_KEYS},
    }


def build_snapshot(records: Iterable[object]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rep in records:
        row = project_reporte(rep)
        if row is not None:
            out.append(row)
    return out
