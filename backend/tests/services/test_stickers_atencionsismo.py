"""Pure normalization of atencionsismo `informe/stickers` rows into the
dashboard's evaluaciones shape (design D1/D2). No I/O."""
from __future__ import annotations

from app.services import stickers_atencionsismo as sa


def _row(**over) -> dict:
    base = {"id": "ev-1", "direccion": "Calle 1 # 2-3", "latitud": "3.4516", "longitud": "-76.5320",
            "numero": "76001-1-0040007", "personaAfectada": "Juan", "origen": "firebase",
            "color": "rojo", "colorEtiqueta": "No habitable"}
    base.update(over)
    return base


def _eval_firestore(**over) -> dict:
    base = {"id": "fs-1", "codigo_edificacion": "76001-1-0040007", "consecutivo": 7, "municipio": "76001",
            "area": "1", "area_nombre": "Norte", "clasificacion": "INSEGURO", "alcance": "Exterior",
            "coords": {"lat": 3.4516, "lng": -76.532, "accuracy": 5},
            "inspector": {"uid": "u1", "codigo": "004", "nombre_completo": "Ana", "identificacion": "1",
                          "entidad": "E", "np": "P4"},
            "descripcion": {"nombre": "Torre A", "direccion": "Calle 1 # 2-3"},
            "restricciones": "r", "acciones_posteriores": {"barricadas": True, "evaluacion_detallada": False},
            "comentarios": "c", "fotos": ["https://x/1.jpg"], "fecha": "2026-08-20T10:00:00"}
    base.update(over)
    return base


# ── parse_codigo ──────────────────────────────────────────────────────────

def test_parse_codigo_our_format():
    assert sa.parse_codigo("76001-1-0040007") == {
        "municipio": "76001", "area": "1", "codigo_inspector": "004", "consecutivo": 7}


def test_parse_codigo_wide_consecutivo():
    assert sa.parse_codigo("76001-2-00410000")["consecutivo"] == 10000


def test_parse_codigo_rejects_other_formats():
    for bad in ("76001001-123-0001", "Sin código", "", None, 42, "76001-1-004", "76001-x-0040001"):
        assert sa.parse_codigo(bad) is None


# ── normalize_sticker ─────────────────────────────────────────────────────

def test_normalize_uses_firestore_evaluacion_when_code_matches():
    out = sa.normalize_sticker(_row(), np_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": _eval_firestore()})
    assert out["fuente"] == "atencionsismo"
    assert out["origen"] == "firebase"
    assert out["id"] == "ev-1"  # atencionsismo id wins: it is the row identity in this source
    assert out["codigo_edificacion"] == "76001-1-0040007"
    assert out["clasificacion"] == "INSEGURO"
    assert out["inspector"]["np"] == "P4"
    assert out["inspector"]["nombre_completo"] == "Ana"
    assert out["fotos"] == ["https://x/1.jpg"]
    assert out["fecha"] == "2026-08-20T10:00:00"
    assert out["alcance"] == "Exterior"
    assert out["color_etiqueta"] == "No habitable"


def test_normalize_falls_back_to_roster_np_by_inspector_code():
    out = sa.normalize_sticker(_row(), np_by_codigo={"004": "P2"}, evaluacion_by_codigo={})
    assert out["inspector"] == {"uid": "", "codigo": "004", "nombre_completo": "", "identificacion": "",
                                "entidad": "", "np": "P2"}
    assert out["fecha"] is None and out["fotos"] == []


def test_normalize_sistema_origin_has_no_np():
    out = sa.normalize_sticker(_row(origen="sistema", numero="76001001-123-0001"), np_by_codigo={"004": "P4"},
                               evaluacion_by_codigo={})
    assert out["inspector"]["codigo"] == "" and out["inspector"]["np"] == ""
    assert out["codigo_edificacion"] == "76001001-123-0001"
    assert out["consecutivo"] is None and out["area"] is None


def test_normalize_color_to_clase():
    assert sa.normalize_sticker(_row(color="verde"), np_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == "INSPECCIONADA"
    assert sa.normalize_sticker(_row(color="amarillo"), np_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == "USO_RESTRINGIDO"
    assert sa.normalize_sticker(_row(color=""), np_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == ""
    assert sa.normalize_sticker(_row(color="Rojo "), np_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == "INSEGURO"


def test_normalize_placeholders_become_empty():
    out = sa.normalize_sticker(_row(numero="Sin código", direccion="Sin dirección", personaAfectada="Sin identificar",
                                    colorEtiqueta="Sin clasificación"), np_by_codigo={}, evaluacion_by_codigo={})
    assert out["codigo_edificacion"] == ""
    assert out["descripcion"] == {"nombre": "", "direccion": ""}
    assert out["color_etiqueta"] == "Sin clasificación"  # kept: it is a real label the UI shows


def test_normalize_coords():
    assert sa.normalize_sticker(_row(), np_by_codigo={}, evaluacion_by_codigo={})["coords"] == {
        "lat": 3.4516, "lng": -76.532, "accuracy": None}
    for lat, lng in (("", ""), ("abc", "-76"), ("0", "0"), (None, None)):
        assert sa.normalize_sticker(_row(latitud=lat, longitud=lng), np_by_codigo={}, evaluacion_by_codigo={})["coords"] is None


def test_normalize_without_id_is_dropped():
    assert sa.normalize_sticker(_row(id=""), np_by_codigo={}, evaluacion_by_codigo={}) is None
    assert sa.normalize_sticker({}, np_by_codigo={}, evaluacion_by_codigo={}) is None


# ── build_evaluaciones ────────────────────────────────────────────────────

def test_build_indexes_firestore_by_code_and_sorts_by_fecha_desc():
    rows = [_row(id="a", numero="76001-1-0040001"), _row(id="b", numero="76001-1-0040002"), _row(id="c", numero="Sin código")]
    fs = [_eval_firestore(codigo_edificacion="76001-1-0040001", fecha="2026-08-01T00:00:00"),
          _eval_firestore(codigo_edificacion="76001-1-0040002", fecha="2026-08-05T00:00:00")]
    out = sa.build_evaluaciones(rows, np_by_codigo={}, evaluaciones_firestore=fs)
    assert [e["id"] for e in out] == ["b", "a", "c"]  # newest first, no-date rows last


def test_build_tolerates_bad_rows():
    out = sa.build_evaluaciones([_row(), {"id": ""}, "not-a-dict", None], np_by_codigo={}, evaluaciones_firestore=[])
    assert len(out) == 1
