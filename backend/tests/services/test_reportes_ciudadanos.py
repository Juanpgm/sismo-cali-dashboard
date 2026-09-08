"""Projection of informe/json records into the Reportes ciudadanos
snapshot (design D5). Pure functions."""
from __future__ import annotations

from app.services import reportes_ciudadanos as rc


def _rep(**over) -> dict:
    base = {"id": "r1", "direccion": "Calle 1 # 2-3", "barrio": "San Antonio", "comuna": "Comuna 3",
            "estadoVerificacion": "Visitado crítico", "afectacion": "COLAPSO PARCIAL", "tipoInmueble": "Casa",
            "nombreEdificio": "", "lat": 3.45, "lng": -76.53, "latitud": "3.45", "longitud": "-76.53",
            "fechaCreacion": "martes, 18 de agosto de 2026, 06:33 p. m.", "habitabilidad": "No habitable",
            "visitado": "Sí", "pudoEvaluar": "Sí", "alcanceInspeccion": "En el interior",
            "descripcion": "Grieta en muro", "conceptoTecnico": "largo...", "danosCubierta": "x",
            "sticker": {"numero": "76001-1-0040001", "color": "rojo", "colorEtiqueta": "No habitable",
                        "origen": "firebase", "clasificacion": "peligro_colapso"}}
    base.update(over)
    return base


# ── parse_fecha_es_co ─────────────────────────────────────────────────────

def test_parse_fecha_pm():
    assert rc.parse_fecha_es_co("martes, 18 de agosto de 2026, 06:33 p. m.") == "2026-08-18T18:33:00-05:00"


def test_parse_fecha_am_and_noon_midnight():
    assert rc.parse_fecha_es_co("jueves, 20 de agosto de 2026, 07:19 a. m.") == "2026-08-20T07:19:00-05:00"
    assert rc.parse_fecha_es_co("lunes, 3 de agosto de 2026, 12:05 p. m.") == "2026-08-03T12:05:00-05:00"
    assert rc.parse_fecha_es_co("lunes, 3 de agosto de 2026, 12:05 a. m.") == "2026-08-03T00:05:00-05:00"


def test_parse_fecha_without_weekday_and_nbsp():
    assert rc.parse_fecha_es_co("18 de agosto de 2026, 6:33 p. m.") == "2026-08-18T18:33:00-05:00"


def test_parse_fecha_invalid():
    for bad in ("", None, "ayer", "32 de agosto de 2026, 06:33 p. m.", "18 de brumario de 2026, 06:33 p. m.", 123):
        assert rc.parse_fecha_es_co(bad) is None


# ── parse_fecha_es_co: mandatory edge cases ────────────────────────────────

def test_parse_fecha_uppercase_month():
    assert rc.parse_fecha_es_co("martes, 18 de AGOSTO de 2026, 06:33 p. m.") == "2026-08-18T18:33:00-05:00"


def test_parse_fecha_pm_without_inner_space():
    assert rc.parse_fecha_es_co("martes, 18 de agosto de 2026, 06:33 p.m.") == "2026-08-18T18:33:00-05:00"


def test_parse_fecha_leading_and_trailing_whitespace():
    assert rc.parse_fecha_es_co("  martes, 18 de agosto de 2026, 06:33 p. m.  ") == "2026-08-18T18:33:00-05:00"


def test_parse_fecha_nbsp_before_meridiem():
    # Real data (Task 0): the API may embed U+00A0 right before "p. m.".
    texto = "martes, 18 de agosto de 2026, 06:33 p. m."
    assert rc.parse_fecha_es_co(texto) == "2026-08-18T18:33:00-05:00"


def test_parse_fecha_february_30_is_none():
    assert rc.parse_fecha_es_co("lunes, 30 de febrero de 2026, 06:33 p. m.") is None


# ── project_reporte ───────────────────────────────────────────────────────

def test_project_keeps_only_tab_fields():
    out = rc.project_reporte(_rep())
    assert set(out) == {"id", "direccion", "barrio", "comuna", "estado", "afectacion", "tipo_inmueble",
                        "nombre_edificio", "lat", "lng", "creado", "creado_texto", "habitabilidad", "visitado",
                        "pudo_evaluar", "alcance", "descripcion", "sticker"}
    assert out["estado"] == "Visitado crítico" and out["visitado"] is True
    assert out["creado"] == "2026-08-18T18:33:00-05:00"
    assert out["sticker"] == {"numero": "76001-1-0040001", "color": "rojo", "etiqueta": "No habitable",
                              "origen": "firebase", "clasificacion": "peligro_colapso"}


def test_project_never_leaks_pii_or_heavy_fields():
    out = rc.project_reporte(_rep(nombre="Juan", cedula="1", telefono="3", fotografiasEvaluacion=[{}], mensajes=[{}]))
    for k in ("nombre", "cedula", "telefono", "fotografiasEvaluacion", "mensajes", "conceptoTecnico"):
        assert k not in out


def test_project_never_leaks_undocumented_urbano_field():
    # Task 0 found an undocumented `urbano` ("Sí") field in the real API.
    # It must not appear in the projection.
    out = rc.project_reporte(_rep(urbano="Sí"))
    assert "urbano" not in out


def test_project_defaults_when_sticker_missing():
    out = rc.project_reporte(_rep(sticker=None))
    assert out["sticker"] == {"numero": "", "color": "", "etiqueta": "", "origen": "", "clasificacion": ""}
    out = rc.project_reporte({k: v for k, v in _rep().items() if k != "sticker"})
    assert out["sticker"]["color"] == ""


def test_project_truncates_descripcion():
    out = rc.project_reporte(_rep(descripcion="x" * 1000))
    assert len(out["descripcion"]) == rc.DESCRIPCION_MAX


def test_project_masks_long_digit_runs_in_descripcion():
    # Task 0 found phone numbers and cedulas typed into the free text.
    out = rc.project_reporte(_rep(descripcion="Llamar al 3001234567 o cc 1234567890, apto 302, calle 12 # 3-45"))
    assert out["descripcion"] == "Llamar al *** o cc ***, apto 302, calle 12 # 3-45"
    assert rc.mask_digit_runs("tel 300 123 4567") == "tel 300 123 4567"  # spaced digits are not a run
    assert rc.mask_digit_runs("") == ""


def test_project_visitado_false_and_coords_fallback():
    out = rc.project_reporte(_rep(visitado="", lat=None, lng=None))
    assert out["visitado"] is False
    assert out["lat"] == 3.45 and out["lng"] == -76.53  # parsed from latitud/longitud strings


def test_project_drops_rows_without_id():
    assert rc.project_reporte(_rep(id="")) is None
    assert rc.project_reporte("nope") is None


# ── project_reporte: mandatory edge cases ──────────────────────────────────

def test_project_lat_lng_numeric_strings():
    out = rc.project_reporte(_rep(lat="3.9", lng="-76.1"))
    assert out["lat"] == 3.9 and out["lng"] == -76.1


def test_project_lat_lng_garbage_falls_back_to_none_when_no_valid_fallback():
    out = rc.project_reporte(_rep(lat="not-a-number", lng="also-garbage", latitud="nope", longitud="nope"))
    assert out["lat"] is None and out["lng"] is None


def test_project_visitado_no_and_none():
    assert rc.project_reporte(_rep(visitado="No"))["visitado"] is False
    assert rc.project_reporte(_rep(visitado=None))["visitado"] is False


def test_project_sticker_non_dict_uses_defaults():
    out = rc.project_reporte(_rep(sticker="not-a-dict"))
    assert out["sticker"] == {"numero": "", "color": "", "etiqueta": "", "origen": "", "clasificacion": ""}


def test_project_descripcion_none_becomes_empty_string():
    out = rc.project_reporte(_rep(descripcion=None))
    assert out["descripcion"] == ""


def test_project_id_with_whitespace_is_stripped():
    out = rc.project_reporte(_rep(id="  r1  "))
    assert out["id"] == "r1"


# ── build_snapshot ─────────────────────────────────────────────────────────

def test_build_snapshot_filters_and_keeps_order():
    rows = rc.build_snapshot([_rep(id="a"), {"id": ""}, _rep(id="b")])
    assert [r["id"] for r in rows] == ["a", "b"]


def test_build_snapshot_skips_non_dict_entries():
    rows = rc.build_snapshot([_rep(id="a"), "not-a-dict", None, 42, _rep(id="b")])
    assert [r["id"] for r in rows] == ["a", "b"]


def test_build_snapshot_accepts_generator_input():
    def gen():
        yield _rep(id="a")
        yield {"id": ""}
        yield _rep(id="b")

    rows = rc.build_snapshot(gen())
    assert [r["id"] for r in rows] == ["a", "b"]
