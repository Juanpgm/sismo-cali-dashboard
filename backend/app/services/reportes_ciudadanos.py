"""Projection of atencionsismo informe/json records into the lightweight
public snapshot the "Reportes ciudadanos" tab reads (design D5 in
docs/superpowers/specs/2026-09-08-atencionsismo-reportes-ciudadanos-stickers-design.md).
Input is the already PII/heavy-stripped record `dashboard_refresh._raw_record_mapper`
produces; this narrows it further to what the tab renders and parses the
es-CO formatted creation date into ISO 8601 (Bogota, UTC-5)."""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

DESCRIPCION_MAX = 240
BOGOTA = timezone(timedelta(hours=-5))
# 7+ digits, optionally separated by a single space/dot/dash between each
# pair of digits — catches both contiguous runs ("3113872391") and the
# spaced/dashed phone numbers citizens also type ("301 226 3431",
# "311-764-8858"). Short digit groups (house numbers, "# 3-45") stay under
# 7 digits and survive untouched.
_DIGIT_RUN_RE = re.compile(r"\d(?:[ .\-]?\d){6,}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def mask_pii(texto: str) -> str:
    """Blank phone numbers / cedulas / emails citizens type into free text
    (`descripcion`, `direccion`, `nombre_edificio`) before it reaches the
    PUBLIC snapshot. Task 0 measured ~2% of descriptions carrying a digit
    run; a later review found the same in `direccion`/`nombre_edificio`,
    plus emails and spaced/dashed phone numbers the digits-only pattern
    missed. NAMES typed into free text are NOT masked — a known residual
    exposure, same as today's `reportes.json`."""
    out = _EMAIL_RE.sub("***", texto or "")
    return _DIGIT_RUN_RE.sub("***", out)


# Backward-compatible alias — `mask_digit_runs` was the original (narrower)
# name before the email/spaced-digit masking above was added.
mask_digit_runs = mask_pii


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
        f = float(value)
    else:
        try:
            f = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    # `json.dumps` emits nan/inf/-inf as invalid, non-standard JSON tokens
    # (NaN/Infinity/-Infinity); reject them here so they never reach the
    # PUBLIC reportes_ciudadanos.json.
    return f if math.isfinite(f) else None


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
    if lat == 0 and lng == 0:
        # Null-island: not a real Cali coordinate. `_parse_coords` already
        # nulls a (0, 0) primary lat/lng, but the latitud/longitud string
        # fallback was resurrecting it — reject it here too.
        lat = None
        lng = None
    fecha_texto = _text(rep.get("fechaCreacion"))
    creado = parse_fecha_es_co(fecha_texto)
    return {
        "id": rid,
        "direccion": mask_pii(_text(rep.get("direccion"))),
        "barrio": _text(rep.get("barrio")),
        "comuna": _text(rep.get("comuna")),
        "estado": _text(rep.get("estadoVerificacion")),
        "afectacion": _text(rep.get("afectacion")),
        "tipo_inmueble": _text(rep.get("tipoInmueble")),
        "nombre_edificio": mask_pii(_text(rep.get("nombreEdificio"))),
        "lat": lat,
        "lng": lng,
        "creado": creado,
        # Only kept as a fallback for display when the date failed to
        # parse — no need to duplicate it once `creado` has a good ISO
        # value (also keeps the PUBLIC snapshot smaller, D5 Task 0).
        "creado_texto": "" if creado is not None else fecha_texto,
        "habitabilidad": _text(rep.get("habitabilidad")),
        "visitado": _text(rep.get("visitado")).lower() in ("sí", "si", "true", "1"),
        "pudo_evaluar": _text(rep.get("pudoEvaluar")),
        "alcance": _text(rep.get("alcanceInspeccion")),
        "descripcion": mask_pii(_text(rep.get("descripcion")))[:DESCRIPCION_MAX],
        "sticker": {out_key: _text(sticker.get(in_key)) for in_key, out_key in _STICKER_KEYS},
    }


def build_snapshot(records: Iterable[object]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rep in records:
        row = project_reporte(rep)
        if row is not None:
            out.append(row)
    return out
