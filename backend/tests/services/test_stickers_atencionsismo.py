"""Pure normalization of atencionsismo `informe/stickers` rows into the
dashboard's evaluaciones shape (design D1/D2). No I/O.

Exception: the F2 tests below build a roster through the REAL
`stickers.inspector_profiles(db)` (via a minimal fake Firestore double)
instead of a hand-built dict — `inspector_profiles` always sets `uid` to
the Firestore doc id, and a hand-built roster dict could accidentally omit
it, hiding the exact bug those tests guard against (see `_FakeInspectorDb`
below)."""
from __future__ import annotations

from app.routers import stickers
from app.services import stickers_atencionsismo as sa


class _FakeInspectorSnap:
    def __init__(self, doc_id: str, data: dict) -> None:
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class _FakeInspectorCollection:
    def __init__(self, docs: list[_FakeInspectorSnap]) -> None:
        self._docs = docs

    def get(self) -> list[_FakeInspectorSnap]:
        return list(self._docs)


class _FakeInspectorDb:
    """Minimal Firestore double — just enough for
    `stickers.inspector_profiles(db)` (`db.collection(name).get()` ->
    snapshots exposing `.id`/`.to_dict()`). Used ONLY by the F2 tests below."""

    def __init__(self, docs_by_id: dict[str, dict]) -> None:
        self._docs = [_FakeInspectorSnap(doc_id, data) for doc_id, data in docs_by_id.items()]

    def collection(self, _name: str) -> _FakeInspectorCollection:
        return _FakeInspectorCollection(self._docs)


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
    out = sa.normalize_sticker(_row(), roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": _eval_firestore()})
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
    out = sa.normalize_sticker(_row(), roster_by_codigo={"004": {"np": "P2"}}, evaluacion_by_codigo={})
    assert out["inspector"] == {"uid": "", "codigo": "004", "nombre_completo": "", "identificacion": "",
                                "entidad": "", "np": "P2", "tarjeta_profesional": "", "num_telefono": "",
                                "correo_contacto": ""}
    assert out["fecha"] is None and out["fotos"] == []


def test_normalize_evaluacion_match_np_is_authoritative_even_when_empty():
    # D1 (updated): brigade codes are reused when an inspector is deleted —
    # falling back to the roster's NP here could hand an OLD evaluación the
    # NEW inspector's NP. If there is a match, its np is final, blank or not.
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "004", "nombre_completo": "Ana", "identificacion": "1",
                   "entidad": "E", "np": ""}
    )
    out = sa.normalize_sticker(_row(), roster_by_codigo={"004": {"np": "P4"}},
                               evaluacion_by_codigo={"76001-1-0040007": matched_eval})
    assert out["inspector"]["np"] == ""


def test_normalize_sistema_origin_has_no_np():
    out = sa.normalize_sticker(_row(origen="sistema", numero="76001001-123-0001"), roster_by_codigo={"004": {"np": "P4"}},
                               evaluacion_by_codigo={})
    assert out["inspector"]["codigo"] == "" and out["inspector"]["np"] == ""
    assert out["codigo_edificacion"] == "76001001-123-0001"
    assert out["consecutivo"] is None and out["area"] is None


def test_normalize_no_match_completes_full_identity_from_roster():
    # Extension (2026-09-08): the no-match fallback completes nombre_completo/
    # identificacion/entidad/uid from the same roster doc as np, not just np.
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez", "identificacion": "123",
                      "entidad": "Curaduria 1", "uid": "u-004"}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"] == {"uid": "u-004", "codigo": "004", "nombre_completo": "Ana Gomez",
                                "identificacion": "123", "entidad": "Curaduria 1", "np": "P4",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}


def test_normalize_evaluacion_match_never_mixes_roster_identity():
    # Critical regression guard: a matched evaluación's inspector fields are
    # authoritative even when a roster entry for the SAME code carries a
    # DIFFERENT identity — the roster's name must never leak into the output.
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "004", "nombre_completo": "Ana", "identificacion": "1",
                   "entidad": "E", "np": "P4"}
    )
    roster = {"004": {"np": "P9", "nombre_completo": "Otro Nombre", "identificacion": "999",
                      "entidad": "Otra Entidad", "uid": "u-otro"}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster,
                               evaluacion_by_codigo={"76001-1-0040007": matched_eval})
    assert out["inspector"] == {"uid": "u1", "codigo": "004", "nombre_completo": "Ana",
                                "identificacion": "1", "entidad": "E", "np": "P4",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}
    for value in roster["004"].values():
        assert value not in out["inspector"].values()


def test_normalize_evaluacion_match_empty_name_stays_empty_not_backfilled():
    # Same authoritative-even-when-empty rule as np: a legacy evaluación doc
    # with an empty inspector.nombre_completo is never backfilled from roster.
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "004", "nombre_completo": "", "identificacion": "1",
                   "entidad": "E", "np": "P4"}
    )
    roster = {"004": {"np": "P9", "nombre_completo": "Roster Name", "identificacion": "999",
                      "entidad": "Otra Entidad", "uid": "u-otro"}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster,
                               evaluacion_by_codigo={"76001-1-0040007": matched_eval})
    assert out["inspector"]["nombre_completo"] == ""


def test_normalize_no_codigo_parsed_ignores_nonempty_roster():
    roster = {"004": {"np": "P9", "nombre_completo": "Alguien", "identificacion": "1",
                      "entidad": "E", "uid": "u-1"}}
    out = sa.normalize_sticker(_row(origen="sistema", numero="Sin código"), roster_by_codigo=roster,
                               evaluacion_by_codigo={})
    assert out["inspector"] == {"uid": "", "codigo": "", "nombre_completo": "", "identificacion": "",
                                "entidad": "", "np": "", "tarjeta_profesional": "", "num_telefono": "",
                                "correo_contacto": ""}


def test_normalize_color_to_clase():
    assert sa.normalize_sticker(_row(color="verde"), roster_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == "INSPECCIONADA"
    assert sa.normalize_sticker(_row(color="amarillo"), roster_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == "USO_RESTRINGIDO"
    assert sa.normalize_sticker(_row(color=""), roster_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == ""
    assert sa.normalize_sticker(_row(color="Rojo "), roster_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == "INSEGURO"


def test_normalize_placeholders_become_empty():
    out = sa.normalize_sticker(_row(numero="Sin código", direccion="Sin dirección", personaAfectada="Sin identificar",
                                    colorEtiqueta="Sin clasificación"), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["codigo_edificacion"] == ""
    assert out["descripcion"] == {"nombre": "", "direccion": ""}
    assert out["color_etiqueta"] == "Sin clasificación"  # kept: it is a real label the UI shows


def test_normalize_coords():
    assert sa.normalize_sticker(_row(), roster_by_codigo={}, evaluacion_by_codigo={})["coords"] == {
        "lat": 3.4516, "lng": -76.532, "accuracy": None}
    for lat, lng in (("", ""), ("abc", "-76"), ("0", "0"), (None, None)):
        assert sa.normalize_sticker(_row(latitud=lat, longitud=lng), roster_by_codigo={}, evaluacion_by_codigo={})["coords"] is None


def test_normalize_without_id_is_dropped():
    assert sa.normalize_sticker(_row(id=""), roster_by_codigo={}, evaluacion_by_codigo={}) is None
    assert sa.normalize_sticker({}, roster_by_codigo={}, evaluacion_by_codigo={}) is None


# ── build_evaluaciones ────────────────────────────────────────────────────

def test_build_indexes_firestore_by_code_and_sorts_by_fecha_desc():
    rows = [_row(id="a", numero="76001-1-0040001"), _row(id="b", numero="76001-1-0040002"), _row(id="c", numero="Sin código")]
    fs = [_eval_firestore(codigo_edificacion="76001-1-0040001", fecha="2026-08-01T00:00:00"),
          _eval_firestore(codigo_edificacion="76001-1-0040002", fecha="2026-08-05T00:00:00")]
    out = sa.build_evaluaciones(rows, roster_by_codigo={}, evaluaciones_firestore=fs)
    assert [e["id"] for e in out] == ["b", "a", "c"]  # newest first, no-date rows last


def test_build_tolerates_bad_rows():
    out = sa.build_evaluaciones([_row(), {"id": ""}, "not-a-dict", None], roster_by_codigo={}, evaluaciones_firestore=[])
    assert len(out) == 1


def test_build_matches_firestore_evaluacion_when_codigo_has_stray_whitespace():
    # A sticker's `numero` is already stripped by `_clean` before the
    # by_codigo lookup; the Firestore side must be stripped too or a
    # trailing/leading space on `codigo_edificacion` silently breaks the
    # join (falls through to the roster/"sin dato" instead of the real
    # evaluación).
    rows = [_row(id="a", numero=" 76001-1-0040007 ")]
    fs = [_eval_firestore(codigo_edificacion="76001-1-0040007 ")]
    out = sa.build_evaluaciones(rows, roster_by_codigo={}, evaluaciones_firestore=fs)
    assert out[0]["inspector"]["np"] == "P4"


# ── inspector_fuente: distinguishes verified identity (matched evaluación)
# from the current holder of a reused brigade code (roster fallback) ───────


def test_inspector_fuente_is_evaluacion_when_matched():
    matched_eval = _eval_firestore()
    out = sa.normalize_sticker(_row(), roster_by_codigo={"004": {"nombre_completo": "Otro"}},
                               evaluacion_by_codigo={"76001-1-0040007": matched_eval})
    assert out["inspector_fuente"] == "evaluacion"


def test_inspector_fuente_is_evaluacion_even_when_matched_identity_is_blank():
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "004", "nombre_completo": "", "identificacion": "",
                   "entidad": "", "np": ""}
    )
    out = sa.normalize_sticker(_row(), roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval})
    assert out["inspector_fuente"] == "evaluacion"


def test_inspector_fuente_is_roster_when_no_match_and_roster_has_an_identity_field():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez", "identificacion": "123",
                      "entidad": "Curaduria 1", "uid": "u-004"}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector_fuente"] == "roster"


def test_inspector_fuente_is_empty_when_only_uid_is_present():
    # F2: `uid` alone does NOT count as "roster names a person" —
    # `inspector_profiles` always sets `uid` to the Firestore doc id, so
    # counting it here would make EVERY roster entry read as "roster"
    # regardless of whether it actually names anyone.
    roster = {"004": {"np": "P4", "uid": "u-004"}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector_fuente"] == ""


def test_inspector_fuente_is_empty_when_no_match_and_roster_has_only_np():
    # A roster entry with ONLY `np` set carries a Fase number, not a person —
    # there is nobody to (mis)attribute, so this is NOT "roster".
    roster = {"004": {"np": "P4"}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector_fuente"] == ""
    assert out["inspector"]["np"] == "P4"  # np itself is still set


def test_inspector_fuente_is_empty_when_no_match_and_roster_entry_is_entirely_blank():
    roster = {"004": {}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector_fuente"] == ""


def test_inspector_fuente_is_empty_when_no_match_and_no_roster_entry():
    out = sa.normalize_sticker(_row(), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["inspector_fuente"] == ""


def test_inspector_fuente_is_empty_when_codigo_does_not_parse():
    roster = {"004": {"np": "P9", "nombre_completo": "Alguien", "identificacion": "1",
                      "entidad": "E", "uid": "u-1"}}
    out = sa.normalize_sticker(_row(origen="sistema", numero="Sin código"), roster_by_codigo=roster,
                               evaluacion_by_codigo={})
    assert out["inspector_fuente"] == ""


# ── F3: roster fields that are explicitly None (not just missing) must
# coerce to "", same as the matched-evaluación branch already does ─────────


def test_roster_none_identity_values_coerce_to_empty_string_not_none():
    roster = {"004": {"np": None, "uid": None, "nombre_completo": None,
                      "identificacion": None, "entidad": None}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"] == {"uid": "", "codigo": "004", "nombre_completo": "",
                                "identificacion": "", "entidad": "", "np": "",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}
    assert out["inspector_fuente"] == ""  # all blank after coercion -> no person to attribute


# ── contrato v3 (2026-09-08): `profesional` (API-reported technician),
# `fase` (Fase I/II per atencionsismo's own process, API developer
# confirmation 2026-09-08) and `fotografias` ─────────────────────────────


def test_profesional_missing_falls_back_to_roster_by_codigo():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez"}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["np"] == "P4" and out["inspector"]["nombre_completo"] == "Ana Gomez"
    assert out["inspector_fuente"] == "roster"


def test_profesional_none_falls_back_to_roster_by_codigo():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez"}}
    out = sa.normalize_sticker(_row(profesional=None), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["np"] == "P4" and out["inspector_fuente"] == "roster"


def test_profesional_non_dict_falls_back_to_roster_by_codigo():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez"}}
    out = sa.normalize_sticker(_row(profesional="not-a-dict"), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["np"] == "P4" and out["inspector_fuente"] == "roster"


def test_profesional_all_blank_falls_back_to_roster_by_codigo():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez"}}
    out = sa.normalize_sticker(_row(profesional={"cedula": "", "nombre": "", "rango": ""}),
                               roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["np"] == "P4" and out["inspector_fuente"] == "roster"


# ── F1: `_profesional_fields` must clean the API's own placeholder strings
# ("Sin código"/"Sin identificar"/...), not just `.strip()` — a placeholder
# is not a real value and must never be treated as naming a person or as a
# usable bare rango. ────────────────────────────────────────────────────


def test_profesional_placeholder_cedula_and_nombre_do_not_leak_falls_back_to_roster():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez", "identificacion": "123",
                      "entidad": "Curaduria 1", "uid": "u-004"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "Sin código", "nombre": "Sin identificar", "rango": ""}),
        roster_by_codigo=roster, evaluacion_by_codigo={},
    )
    # Roster fallback applies (step 4, not step 2/3): the placeholders never
    # named a person nor carried a bare rango.
    assert out["inspector"] == {"uid": "u-004", "codigo": "004", "nombre_completo": "Ana Gomez",
                                "identificacion": "123", "entidad": "Curaduria 1", "np": "P4",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}
    assert out["inspector_fuente"] == "roster"
    for placeholder in ("Sin código", "Sin identificar"):
        assert placeholder not in out["inspector"].values()


def test_profesional_placeholder_cedula_with_real_rango_is_rango_only():
    # A placeholder cedula must NOT count as "naming a person" even with a
    # real rango present — this must land in the rango-only branch (step 3,
    # fuente "roster" here because the roster names a person), not the
    # api-identity branch (step 2, which would read fuente "api").
    roster = {"004": {"np": "P9", "nombre_completo": "Ana Gomez", "identificacion": "123",
                      "entidad": "Curaduria 1", "uid": "u-004"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "Sin código", "nombre": "", "rango": "P2"}),
        roster_by_codigo=roster, evaluacion_by_codigo={},
    )
    assert out["inspector"]["np"] == "P9"  # roster's own np wins (F4 no-mixing), API rango ignored
    assert out["inspector_fuente"] == "roster"
    assert "Sin código" not in out["inspector"].values()


def test_profesional_rango_only_uses_roster_np_when_roster_names_a_person():
    # F4: no source mixing — when the brigade-code roster already names a
    # person, its OWN np is authoritative and the API's bare rango is
    # ignored, same "never mix sources field-by-field" invariant as the
    # matched branch. Identity comes entirely from the roster.
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez", "identificacion": "123",
                      "entidad": "Curaduria 1", "uid": "u-004"}}
    out = sa.normalize_sticker(_row(origen="sistema", profesional={"rango": "P9"}),
                               roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"] == {"uid": "u-004", "codigo": "004", "nombre_completo": "Ana Gomez",
                                "identificacion": "123", "entidad": "Curaduria 1", "np": "P4",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}
    assert out["inspector_fuente"] == "roster"


def test_profesional_rango_only_fuente_empty_when_roster_has_no_person():
    # F2 regression: the roster here is built through the REAL
    # `stickers.inspector_profiles`, which ALWAYS sets `uid` to the
    # Firestore doc id — a hand-built `{"np": "P4"}` dict (no uid) used to
    # pass this test even when the "roster names a person" predicate wrongly
    # counted uid, because that dict never HAD a uid to begin with. A real
    # roster entry always does, so the predicate must exclude uid on its
    # own merits: same rango-only branch, roster entry carries no identity
    # field (uid included) -> nobody to attribute, so the bare rango becomes
    # np and fuente stays "".
    db = _FakeInspectorDb({"u1": {"codigo": "004", "NP": "P4"}})
    roster, _ = stickers.inspector_profiles(db)
    out = sa.normalize_sticker(_row(origen="sistema", profesional={"rango": "P9"}),
                               roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["np"] == "P9"
    assert out["inspector_fuente"] == ""


def test_inspector_fuente_is_empty_when_no_match_and_roster_has_only_np_via_inspector_profiles():
    # F2 mirror: same guard as the rango-only test above, but for the PLAIN
    # roster-by-brigade-code fallback (no `profesional` at all) — the roster
    # is still built through `inspector_profiles`, so `uid` is set (doc id)
    # even though the doc names nobody. `inspector_fuente` must stay "".
    db = _FakeInspectorDb({"u1": {"codigo": "004", "NP": "P4"}})
    roster, _ = stickers.inspector_profiles(db)
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["np"] == "P4"
    assert out["inspector_fuente"] == ""


def test_profesional_rango_only_no_roster_entry_at_all_uses_rango_for_np():
    # F4: no roster entry at all for the code -> nobody to attribute either,
    # same convention as the np-only-roster case above.
    out = sa.normalize_sticker(_row(origen="sistema", profesional={"rango": "P9"}),
                               roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["inspector"]["np"] == "P9"
    assert out["inspector_fuente"] == ""


def test_profesional_cedula_and_nombre_without_roster_hit_is_fuente_api():
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "123", "nombre": "Juan Perez", "rango": "P2"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula={},
    )
    assert out["inspector"] == {"uid": "", "codigo": "004", "nombre_completo": "Juan Perez",
                                "identificacion": "123", "entidad": "", "np": "P2",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}
    assert out["inspector_fuente"] == "api"


def test_profesional_cedula_found_in_roster_by_cedula_fills_uid_and_entidad():
    roster_cedula = {"123": {"uid": "u9", "entidad": "E9", "nombre_completo": "Roster Nombre", "np": "P9"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "123", "nombre": "Juan Perez", "rango": "P2"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula=roster_cedula,
    )
    # API values win on conflict: nombre and np both came from the API.
    assert out["inspector"] == {"uid": "u9", "codigo": "004", "nombre_completo": "Juan Perez",
                                "identificacion": "123", "entidad": "E9", "np": "P2",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}
    assert out["inspector_fuente"] == "api"


def test_profesional_cedula_found_roster_np_used_only_when_api_rango_blank():
    roster_cedula = {"123": {"np": "P9"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "123", "nombre": "Juan Perez", "rango": ""}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula=roster_cedula,
    )
    assert out["inspector"]["np"] == "P9"


def test_profesional_cedula_found_roster_nombre_used_only_when_api_nombre_blank():
    roster_cedula = {"123": {"nombre_completo": "Roster Nombre"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "123", "nombre": "", "rango": "P2"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula=roster_cedula,
    )
    assert out["inspector"]["nombre_completo"] == "Roster Nombre"


def test_profesional_rango_whitespace_and_case_kept_as_stripped_string_not_parsed():
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "1", "nombre": "A", "rango": "  p3  "}),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["inspector"]["np"] == "p3"


def test_profesional_wins_identity_even_when_evaluacion_matched():
    # Rule A (2026-09-15, supersedes the old "evaluación always wins"
    # contract): `profesional` is `evaluacion.tecnico` per the API's own
    # docs, so when it names a person it wins identity UNCONDITIONALLY —
    # even when a Firestore evaluación also matched by code. The match's
    # own `inspector` sub-object is never consulted in that case, but
    # everything else the match drives (fecha, fotos, clasificacion, ...)
    # is untouched by this rule.
    matched_eval = _eval_firestore(fecha="2026-08-20T10:00:00", fotos=["https://firestore/1.jpg"])
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "999", "nombre": "Otra Persona", "rango": "P9"}),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval},
        roster_by_cedula={"999": {"uid": "u-x"}},
    )
    assert out["inspector"] == {"uid": "u-x", "codigo": "004", "nombre_completo": "Otra Persona",
                                "identificacion": "999", "entidad": "", "np": "P9",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}
    assert out["inspector_fuente"] == "api"
    # The match still drives everything else, unaffected by Rule A.
    assert out["fecha"] == "2026-08-20T10:00:00"
    assert out["fecha_fuente"] == "evaluacion"
    assert out["fotos"] == ["https://firestore/1.jpg"]
    assert out["clasificacion"] == "INSEGURO"


# ── Fresh review (2026-09-15): on Rule A, `codigo` must NEVER come from the
# matched evaluación's own inspector — only `roster_by_cedula`'s own
# `codigo` (if the cédula is in the roster) or the code parsed from the
# sticker's own `numero`, same "profesional identity, never mixed with the
# match" invariant as every other Rule A field. The existing Rule A tests
# above could not catch this: `_eval_firestore()`'s default inspector.codigo
# ("004") is IDENTICAL to the code embedded in the default `numero`, so a
# bug that leaked the match's codigo was invisible until the two differ. ──


def test_rule_a_codigo_comes_from_parsed_numero_never_matched_evaluacion():
    # matched_eval's own codigo ("777") deliberately DIFFERS from the one
    # embedded in `numero` ("004") -- if `codigo` leaked from the match this
    # would read "777" instead of the correct "004".
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "777", "nombre_completo": "Ana", "identificacion": "1",
                   "entidad": "E", "np": "P4"}
    )
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "999", "nombre": "Otra Persona"}),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval},
        roster_by_cedula={},
    )
    assert out["inspector_fuente"] == "api"
    assert out["inspector"]["codigo"] == "004", "codigo must come from the parsed numero, never the matched evaluación"


def test_rule_a_codigo_prefers_roster_by_cedula_over_parsed_and_match():
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "777", "nombre_completo": "Ana", "identificacion": "1",
                   "entidad": "E", "np": "P4"}
    )
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "999", "nombre": "Otra Persona"}),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval},
        roster_by_cedula={"999": {"codigo": "010"}},
    )
    assert out["inspector"]["codigo"] == "010"


def test_rule_a_codigo_falls_back_to_parsed_when_no_roster_by_cedula_hit():
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "999", "nombre": "Otra Persona"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula={},
    )
    assert out["inspector"]["codigo"] == "004"  # parsed from the default numero, no match involved


# ── Fresh review (2026-09-15): same-person backfill on Rule A for np/
# entidad/uid — when the API/roster leave one of these blank, fall back to
# the MATCHED evaluación's own inspector, but ONLY when that inspector's
# `identificacion` equals the API cédula after digit-only normalization
# (same person, so no identity mixing risk). Precedence per field: API
# value -> roster_by_cedula -> same-cédula matched evaluación -> "".
# Different cédula, blank cédula, or no match at all -> never touch the
# match. `identificacion`/`nombre_completo`/`codigo` are NOT part of this
# backfill (they have their own, stricter rules above). ────────────────────


def test_rule_a_same_cedula_match_backfills_np_when_rango_blank():
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "777", "nombre_completo": "Ana", "identificacion": "1.234.567",
                   "entidad": "E", "np": "P4"}
    )
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "1234567", "nombre": "Otra Persona", "rango": ""}),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval}, roster_by_cedula={},
    )
    assert out["inspector_fuente"] == "api"
    assert out["inspector"]["np"] == "P4"


def test_rule_a_different_cedula_match_never_backfills_np():
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "777", "nombre_completo": "Ana", "identificacion": "1",
                   "entidad": "E", "np": "P4"}
    )
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "999", "nombre": "Otra Persona", "rango": ""}),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval}, roster_by_cedula={},
    )
    assert out["inspector"]["np"] == ""


def test_rule_a_same_cedula_match_backfills_entidad_when_roster_lacks_it():
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "777", "nombre_completo": "Ana", "identificacion": "1234567",
                   "entidad": "Curaduria 9", "np": "P4"}
    )
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "1.234.567", "nombre": "Otra Persona"}),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval},
        roster_by_cedula={"1234567": {"uid": "u9"}},  # no entidad here
    )
    assert out["inspector"]["entidad"] == "Curaduria 9"
    assert out["inspector"]["uid"] == "u9"  # roster still wins for uid, unaffected


def test_rule_a_different_cedula_match_never_backfills_entidad_or_uid():
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "777", "nombre_completo": "Ana", "identificacion": "1",
                   "entidad": "Curaduria 9", "np": "P4"}
    )
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "999", "nombre": "Otra Persona"}),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval}, roster_by_cedula={},
    )
    assert out["inspector"]["entidad"] == ""
    assert out["inspector"]["uid"] == ""


def test_rule_a_same_cedula_match_backfills_uid_when_roster_lacks_it():
    matched_eval = _eval_firestore(
        inspector={"uid": "u-real", "codigo": "777", "nombre_completo": "Ana", "identificacion": "999",
                   "entidad": "E", "np": "P4"}
    )
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "999", "nombre": "Otra Persona"}),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval}, roster_by_cedula={},
    )
    assert out["inspector"]["uid"] == "u-real"


def test_rule_a_blank_cedula_never_backfills_from_match_even_with_blank_insp_match_identificacion():
    # Belt-and-suspenders: a blank API cédula must never fall back to the
    # match even in the degenerate case where the match's own identificacion
    # is ALSO blank (cedula_key("") == cedula_key("") would otherwise be a
    # false "same person").
    matched_eval = _eval_firestore(
        inspector={"uid": "u1", "codigo": "777", "nombre_completo": "", "identificacion": "",
                   "entidad": "Curaduria 9", "np": "P4"}
    )
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "", "nombre": "Solo Nombre", "rango": ""}),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval}, roster_by_cedula={},
    )
    assert out["inspector"]["np"] == ""
    assert out["inspector"]["entidad"] == ""
    assert out["inspector"]["uid"] == ""


def test_profesional_empty_evaluacion_matched_stays_evaluacion_unchanged():
    # Rule B fallback: when `profesional` names nobody, a matched
    # evaluación's own inspector is still authoritative, exactly as before
    # Rule A existed — including blank contact fields.
    matched_eval = _eval_firestore()
    out = sa.normalize_sticker(
        _row(origen="sistema"), roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval},
    )
    assert out["inspector"] == {"uid": "u1", "codigo": "004", "nombre_completo": "Ana",
                                "identificacion": "1", "entidad": "E", "np": "P4",
                                "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""}
    assert out["inspector_fuente"] == "evaluacion"


# ── Rule A (2026-09-15): `profesional` is now consulted regardless of
# `origen` — a live sample found it populated on both "sistema" and
# "firebase" origin rows (module docstring); the old "sistema"-only trust
# restriction (contrato v3, F3) was verified false and removed. ───────────


def test_profesional_used_on_firebase_origin_now_wins_over_roster():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez"}}
    out = sa.normalize_sticker(
        _row(origen="firebase", profesional={"cedula": "999", "nombre": "Otra Persona", "rango": "P9"}),
        roster_by_codigo=roster, evaluacion_by_codigo={}, roster_by_cedula={"999": {"uid": "u-x"}},
    )
    assert out["inspector"]["np"] == "P9"  # from the API's rango, not the roster
    assert out["inspector"]["nombre_completo"] == "Otra Persona"  # from the API, not the roster
    assert out["inspector_fuente"] == "api"


def test_profesional_used_on_firebase_origin_with_no_roster_hit_is_fuente_api():
    out = sa.normalize_sticker(
        _row(origen="firebase", profesional={"cedula": "999", "nombre": "Otra Persona", "rango": "P9"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula={"999": {"uid": "u-x"}},
    )
    assert out["inspector"]["np"] == "P9"
    assert out["inspector"]["identificacion"] == "999"
    assert out["inspector"]["uid"] == "u-x"
    assert out["inspector_fuente"] == "api"


def test_profesional_used_on_blank_origen_now_wins_over_roster():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez"}}
    out = sa.normalize_sticker(
        _row(origen="", profesional={"cedula": "999", "nombre": "Otra Persona"}),
        roster_by_codigo=roster, evaluacion_by_codigo={}, roster_by_cedula={"999": {"uid": "u-x"}},
    )
    assert out["inspector"]["nombre_completo"] == "Otra Persona"
    assert out["inspector_fuente"] == "api"


def test_profesional_trusted_regardless_of_origen_case_and_whitespace():
    for origen in ("Sistema", " sistema ", "SISTEMA", "firebase", "", "algo-desconocido"):
        out = sa.normalize_sticker(
            _row(origen=origen, profesional={"cedula": "123", "nombre": "Juan Perez"}),
            roster_by_codigo={}, evaluacion_by_codigo={},
        )
        assert out["inspector_fuente"] == "api", f"origen={origen!r} should trust profesional"
        assert out["inspector"]["nombre_completo"] == "Juan Perez"


def test_profesional_nombre_only_blank_cedula_is_api_identity_without_roster_cedula_join():
    # A blank cedula must never join `roster_by_cedula` — `cedula_key("")`
    # is "", and callers of the join treat "" as "no key" (F7).
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "", "nombre": "Solo Nombre", "rango": "P2"}),
        roster_by_codigo={}, evaluacion_by_codigo={},
        roster_by_cedula={"": {"uid": "should-not-match"}},
    )
    assert out["inspector"]["identificacion"] == ""
    assert out["inspector"]["nombre_completo"] == "Solo Nombre"
    assert out["inspector"]["uid"] == ""
    assert out["inspector_fuente"] == "api"


def test_firebase_origin_fase_2_with_p1_match_keeps_np_p1():
    # Regression guard: the identity/NP priority chain is unaffected by the
    # `fase` contract change — a matched Firestore evaluación's own np stays
    # authoritative even though every firebase-origin row carries fase=2.
    # Both are true at once BY DESIGN: `inspector.np` (Evaluaciones tab
    # classification) and `fase` (Stickers tab classification, API developer
    # confirmation 2026-09-08) are two independent Fase signals that can
    # disagree for the same evaluación (see module docstring).
    matched_eval = _eval_firestore(inspector={"uid": "u1", "codigo": "004", "nombre_completo": "Ana",
                                              "identificacion": "1", "entidad": "E", "np": "P1"})
    out = sa.normalize_sticker(_row(origen="firebase", fase=2), roster_by_codigo={},
                               evaluacion_by_codigo={"76001-1-0040007": matched_eval})
    assert out["inspector"]["np"] == "P1"
    assert out["fase"] == 2


# ── `fase` coercion: 1|2 = Fase I/II per atencionsismo's own process
# (API developer confirmation 2026-09-08); anything else is None ─────────


def test_fase_coercion():
    cases = [
        (1, 1), (2, 2), ("1", 1), ("2", 2), (None, None), (3, None), ("x", None),
        (2.0, 2), (1.5, None), (0, None), ("", None), (True, None),
    ]
    for raw, expected in cases:
        out = sa.normalize_sticker(_row(fase=raw), roster_by_codigo={}, evaluacion_by_codigo={})
        assert out["fase"] == expected, f"fase={raw!r} expected {expected!r} got {out['fase']!r}"


def test_fase_missing_is_none():
    out = sa.normalize_sticker(_row(), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["fase"] is None


# ── fotos from `fotografias` on the no-match path ─────────────────────────


def test_fotos_none_fotografias_is_empty_list():
    out = sa.normalize_sticker(_row(fotografias=None), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["fotos"] == []


def test_fotos_empty_list_fotografias_is_empty_list():
    out = sa.normalize_sticker(_row(fotografias=[]), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["fotos"] == []


def test_fotos_non_list_fotografias_is_empty_list():
    out = sa.normalize_sticker(_row(fotografias="not-a-list"), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["fotos"] == []


def test_fotos_skips_entries_without_url():
    out = sa.normalize_sticker(
        _row(fotografias=[{"id": "1"}, {"id": "2", "url": ""}, {"id": "3", "url": "   "}]),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fotos"] == []


def test_fotos_skips_non_dict_entries():
    out = sa.normalize_sticker(
        _row(fotografias=["https://x/1.jpg", None, 42]),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fotos"] == []


def test_fotos_valid_urls_are_collected_in_order():
    out = sa.normalize_sticker(
        _row(fotografias=[{"id": "1", "url": "https://x/1.jpg"}, {"id": "2", "url": "https://x/2.jpg"}]),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fotos"] == ["https://x/1.jpg", "https://x/2.jpg"]


def test_fotos_matched_row_keeps_firestore_fotos_even_when_fotografias_present():
    matched_eval = _eval_firestore(fotos=["https://firestore/1.jpg"])
    out = sa.normalize_sticker(
        _row(fotografias=[{"id": "1", "url": "https://api/1.jpg"}]),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval},
    )
    assert out["fotos"] == ["https://firestore/1.jpg"]


# ── build_evaluaciones threads roster_by_cedula through ───────────────────


def test_build_evaluaciones_threads_roster_by_cedula():
    rows = [_row(id="a", numero="76001-1-0040001", origen="sistema",
                 profesional={"cedula": "123", "nombre": "Juan Perez", "rango": "P2"})]
    roster_cedula = {"123": {"uid": "u9", "entidad": "E9"}}
    out = sa.build_evaluaciones(rows, roster_by_codigo={}, evaluaciones_firestore=[], roster_by_cedula=roster_cedula)
    assert out[0]["inspector"]["uid"] == "u9" and out[0]["inspector"]["entidad"] == "E9"
    assert out[0]["inspector_fuente"] == "api"


# ── F7: cédula join key normalisation — digits-only on BOTH sides of the
# roster_by_cedula join, while the API's own cedula is stored verbatim
# (stripped) as `identificacion` ────────────────────────────────────────────


def test_cedula_key_normalises_dots_spaces_and_dashes():
    assert sa.cedula_key("1.234.567") == "1234567"
    assert sa.cedula_key(" 123 - 456 ") == "123456"
    assert sa.cedula_key(1234567) == "1234567"
    assert sa.cedula_key("   ") == ""
    assert sa.cedula_key(None) == ""
    assert sa.cedula_key("") == ""


def test_profesional_cedula_with_punctuation_joins_digits_only_roster_key():
    roster_cedula = {"1234567": {"uid": "u9", "entidad": "E9"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "1.234.567", "nombre": "Juan Perez"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula=roster_cedula,
    )
    assert out["inspector"]["uid"] == "u9" and out["inspector"]["entidad"] == "E9"
    # The API's cedula is still stored VERBATIM (stripped) as identificacion,
    # not the normalised join key.
    assert out["inspector"]["identificacion"] == "1.234.567"


def test_profesional_numeric_cedula_joins_roster():
    roster_cedula = {"1234567": {"uid": "u9"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": 1234567, "nombre": "Juan Perez"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula=roster_cedula,
    )
    assert out["inspector"]["uid"] == "u9"


def test_profesional_whitespace_only_cedula_never_joins_the_roster():
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "   ", "nombre": "Juan Perez"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula={"": {"uid": "should-not-match"}},
    )
    assert out["inspector"]["identificacion"] == ""
    assert out["inspector"]["uid"] == ""


# ── F8: `fotografias` URL hygiene — stored stripped, http(s)-only ─────────


def test_fotos_padded_url_is_stored_stripped():
    out = sa.normalize_sticker(
        _row(fotografias=[{"id": "1", "url": "  https://x/1.jpg  "}]),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fotos"] == ["https://x/1.jpg"]


def test_fotos_relative_url_is_skipped():
    out = sa.normalize_sticker(
        _row(fotografias=[{"id": "1", "url": "/uploads/1.jpg"}]),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fotos"] == []


def test_fotos_non_http_scheme_is_skipped():
    out = sa.normalize_sticker(
        _row(fotografias=[{"id": "1", "url": "ftp://x/1.jpg"}]),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fotos"] == []


# ── F9: matched row with empty firestore fotos never falls back to
# fotografias, even when fotografias is populated ──────────────────────────


def test_fotos_matched_row_with_empty_firestore_fotos_stays_empty():
    matched_eval = _eval_firestore(fotos=[])
    out = sa.normalize_sticker(
        _row(fotografias=[{"id": "1", "url": "https://api/1.jpg"}]),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval},
    )
    assert out["fotos"] == []


# ── W2 (plan cozy-wobbling-dragonfly): `fecha`/`fecha_fuente` resolution.
# Priority: (1) matched Firestore evaluación -> its own `fecha`,
# "evaluacion" (byte-identical even when None, API's fechaCreacion never
# consulted); (2) no match, origen "sistema" -> shared es-CO parser (UTC),
# "api", or None/"sin_fecha" on parse failure; (3) everything else ->
# None/"no_aplica". ─────────────────────────────────────────────────────


def test_fecha_uses_matched_evaluacion_fecha_ignores_api_fechaCreacion():
    matched_eval = _eval_firestore(fecha="2026-08-20T10:00:00+00:00")
    out = sa.normalize_sticker(
        _row(origen="sistema", fechaCreacion="martes, 18 de agosto de 2026, 06:33 p. m."),
        roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval},
    )
    assert out["fecha"] == "2026-08-20T10:00:00+00:00"
    assert out["fecha_fuente"] == "evaluacion"


def test_fecha_matched_with_none_fecha_stays_none_fuente_evaluacion():
    matched_eval = _eval_firestore(fecha=None)
    out = sa.normalize_sticker(_row(), roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval})
    assert out["fecha"] is None
    assert out["fecha_fuente"] == "evaluacion"


def test_fecha_no_match_sistema_valid_fechaCreacion_is_api_iso():
    out = sa.normalize_sticker(
        _row(origen="sistema", fechaCreacion="martes, 18 de agosto de 2026, 06:33 p. m.", numero="Sin código"),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fecha"] == "2026-08-18T18:33:00+00:00"
    assert out["fecha_fuente"] == "api"


def test_fecha_no_match_firebase_is_no_aplica():
    out = sa.normalize_sticker(_row(origen="firebase"), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["fecha"] is None
    assert out["fecha_fuente"] == "no_aplica"


def test_fecha_match_with_firebase_origin_still_uses_match_fecha():
    matched_eval = _eval_firestore(fecha="2026-08-20T10:00:00+00:00")
    out = sa.normalize_sticker(_row(origen="firebase"), roster_by_codigo={},
                               evaluacion_by_codigo={"76001-1-0040007": matched_eval})
    assert out["fecha"] == "2026-08-20T10:00:00+00:00"
    assert out["fecha_fuente"] == "evaluacion"


def test_fecha_sistema_origen_with_whitespace_and_case_still_parses():
    out = sa.normalize_sticker(
        _row(origen=" Sistema ", fechaCreacion="martes, 18 de agosto de 2026, 06:33 p. m.", numero="Sin código"),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fecha_fuente"] == "api"
    assert out["fecha"] == "2026-08-18T18:33:00+00:00"


def test_fecha_sistema_malformed_fechaCreacion_is_sin_fecha():
    out = sa.normalize_sticker(
        _row(origen="sistema", fechaCreacion="no es una fecha", numero="Sin código"),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fecha"] is None
    assert out["fecha_fuente"] == "sin_fecha"


def test_fecha_sistema_missing_fechaCreacion_is_sin_fecha():
    out = sa.normalize_sticker(_row(origen="sistema", numero="Sin código"), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["fecha"] is None
    assert out["fecha_fuente"] == "sin_fecha"


def test_fecha_blank_origen_no_match_is_no_aplica():
    out = sa.normalize_sticker(
        _row(origen="", fechaCreacion="martes, 18 de agosto de 2026, 06:33 p. m."),
        roster_by_codigo={}, evaluacion_by_codigo={},
    )
    assert out["fecha"] is None
    assert out["fecha_fuente"] == "no_aplica"


def test_build_evaluaciones_sorts_dated_before_undated_with_mixed_fecha_formats():
    # Mixed formats on purpose: a Firestore `fecha` may already carry a
    # trailing "Z" (device-written) while ours always emits "+00:00" — the
    # sort must still put every dated row ahead of every undated one.
    rows = [
        _row(id="a", numero="76001-1-0040001", origen="firebase"),  # no match -> no_aplica -> None
        _row(id="b", numero="76001-1-0040002", origen="sistema",
             fechaCreacion="martes, 18 de agosto de 2026, 06:33 p. m."),  # api -> "+00:00"
        _row(id="c", numero="76001-1-0040003", origen="firebase"),
    ]
    fs = [_eval_firestore(codigo_edificacion="76001-1-0040003", fecha="2026-09-01T00:00:00Z")]
    out = sa.build_evaluaciones(rows, roster_by_codigo={}, evaluaciones_firestore=fs)
    ids = [e["id"] for e in out]
    assert ids.index("a") > ids.index("b")
    assert ids.index("a") > ids.index("c")
    by_id = {e["id"]: e for e in out}
    assert by_id["a"]["fecha"] is None
    assert by_id["b"]["fecha"] == "2026-08-18T18:33:00+00:00"
    assert by_id["c"]["fecha"] == "2026-09-01T00:00:00Z"


# ── W3 (plan cozy-wobbling-dragonfly): `barrio_reportado`, `comuna_reportada`
# and inspector contact fields (`tarjeta_profesional`, `num_telefono`,
# `correo_contacto`), always-present, never mixed sources on the matched
# branch. ────────────────────────────────────────────────────────────────


def test_barrio_reportado_from_row_barrio_field():
    out = sa.normalize_sticker(_row(barrio="San Antonio"), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["barrio_reportado"] == "San Antonio"


def test_barrio_reportado_placeholder_becomes_empty():
    # Goes through `_clean` (:124) — "Sin identificar" is a documented API
    # placeholder, not real data.
    out = sa.normalize_sticker(_row(barrio="Sin identificar"), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["barrio_reportado"] == ""


def test_barrio_reportado_missing_key_is_empty():
    out = sa.normalize_sticker(_row(), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["barrio_reportado"] == ""


def test_comuna_reportada_int_becomes_string():
    out = sa.normalize_sticker(_row(comuna=3), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["comuna_reportada"] == "3"


def test_comuna_reportada_string_kept():
    out = sa.normalize_sticker(_row(comuna="Comuna 3"), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["comuna_reportada"] == "Comuna 3"


def test_comuna_reportada_missing_key_is_empty():
    out = sa.normalize_sticker(_row(), roster_by_codigo={}, evaluacion_by_codigo={})
    assert out["comuna_reportada"] == ""


def test_contact_fields_from_api_win_even_when_evaluacion_matched():
    # Rule A (2026-09-15): once `profesional` names a person, contact
    # fields follow the SAME api-branch rules (API wins, roster backfills
    # what's blank) even when a Firestore evaluación also matched — the
    # OLD invariant ("contact fields always blank on the matched branch")
    # only holds now for Rule B (`profesional` empty), see
    # `test_contact_fields_blank_on_matched_branch_when_profesional_empty`.
    matched_eval = _eval_firestore()
    roster = {"004": {"tarjeta_profesional": "TP-ROSTER", "num_telefono": "3000000000",
                      "correo_contacto": "roster@x.co"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "1", "nombre": "A", "tarjetaProfesional": "TP-API"}),
        roster_by_codigo=roster, evaluacion_by_codigo={"76001-1-0040007": matched_eval},
        roster_by_cedula={"1": {"tarjeta_profesional": "TP-CEDULA", "num_telefono": "3009990000",
                                "correo_contacto": "cedula@x.co"}},
    )
    assert out["inspector"]["tarjeta_profesional"] == "TP-API"  # API wins on conflict
    assert out["inspector"]["num_telefono"] == "3009990000"  # no API source -> cedula-roster backfill
    assert out["inspector"]["correo_contacto"] == "cedula@x.co"
    assert out["inspector"]["nombre_completo"] == "A"
    assert out["inspector_fuente"] == "api"
    for value in roster["004"].values():
        assert value not in out["inspector"].values()  # brigade-code roster never consulted here


def test_contact_fields_blank_on_matched_branch_when_profesional_empty():
    # Preserves the pre-Rule-A invariant for the case Rule A does not
    # touch: no `profesional` identity at all, evaluación matched.
    matched_eval = _eval_firestore()
    roster = {"004": {"tarjeta_profesional": "TP-ROSTER", "num_telefono": "3000000000",
                      "correo_contacto": "roster@x.co"}}
    out = sa.normalize_sticker(
        _row(origen="sistema"), roster_by_codigo=roster,
        evaluacion_by_codigo={"76001-1-0040007": matched_eval},
    )
    assert out["inspector"]["tarjeta_profesional"] == ""
    assert out["inspector"]["num_telefono"] == ""
    assert out["inspector"]["correo_contacto"] == ""


def test_contact_fields_api_branch_prefers_api_tarjeta_roster_backfills_telefono_and_correo():
    roster_cedula = {"123": {"tarjeta_profesional": "TP-ROSTER", "num_telefono": "3001112233",
                             "correo_contacto": "ana@x.co"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "123", "nombre": "Juan Perez",
                                            "tarjetaProfesional": "TP-API"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula=roster_cedula,
    )
    assert out["inspector"]["tarjeta_profesional"] == "TP-API"  # API wins on conflict
    assert out["inspector"]["num_telefono"] == "3001112233"  # no API source -> roster backfill
    assert out["inspector"]["correo_contacto"] == "ana@x.co"


def test_contact_fields_api_branch_backfills_tarjeta_when_api_blank():
    roster_cedula = {"123": {"tarjeta_profesional": "TP-ROSTER"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "123", "nombre": "Juan Perez", "tarjetaProfesional": ""}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula=roster_cedula,
    )
    assert out["inspector"]["tarjeta_profesional"] == "TP-ROSTER"


def test_contact_fields_rango_only_branch_uses_brigade_code_roster():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez", "identificacion": "123",
                      "entidad": "Curaduria 1", "uid": "u-004", "tarjeta_profesional": "TP-1",
                      "num_telefono": "3009998877", "correo_contacto": "ana@x.co"}}
    out = sa.normalize_sticker(_row(origen="sistema", profesional={"rango": "P9"}),
                               roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["tarjeta_profesional"] == "TP-1"
    assert out["inspector"]["num_telefono"] == "3009998877"
    assert out["inspector"]["correo_contacto"] == "ana@x.co"


def test_contact_fields_plain_roster_fallback_branch_uses_brigade_code_roster():
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez", "tarjeta_profesional": "TP-2",
                      "num_telefono": "3001112222", "correo_contacto": "ana2@x.co"}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["tarjeta_profesional"] == "TP-2"
    assert out["inspector"]["num_telefono"] == "3001112222"
    assert out["inspector"]["correo_contacto"] == "ana2@x.co"


def test_contact_fields_none_roster_values_coerce_to_empty_string():
    roster = {"004": {"tarjeta_profesional": None, "num_telefono": None, "correo_contacto": None}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["tarjeta_profesional"] == ""
    assert out["inspector"]["num_telefono"] == ""
    assert out["inspector"]["correo_contacto"] == ""


def test_profesional_absent_contact_keys_present_and_blank():
    # Extends test_profesional_missing_falls_back_to_roster_by_codigo: the
    # roster-by-codigo fallback path still yields the contact keys, blank
    # when the roster has nothing for them.
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez"}}
    out = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})
    assert out["inspector"]["tarjeta_profesional"] == ""
    assert out["inspector"]["num_telefono"] == ""
    assert out["inspector"]["correo_contacto"] == ""


def test_cedula_with_punctuation_joins_roster_for_contact_fields_too():
    # Extends test_profesional_cedula_with_punctuation_joins_digits_only_roster_key.
    roster_cedula = {"1234567": {"uid": "u9", "entidad": "E9", "tarjeta_profesional": "TP-9",
                                 "num_telefono": "3005551111", "correo_contacto": "u9@x.co"}}
    out = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "1.234.567", "nombre": "Juan Perez"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula=roster_cedula,
    )
    assert out["inspector"]["identificacion"] == "1.234.567"  # verbatim, not the join key
    assert out["inspector"]["tarjeta_profesional"] == "TP-9"
    assert out["inspector"]["num_telefono"] == "3005551111"
    assert out["inspector"]["correo_contacto"] == "u9@x.co"


def test_inspector_shape_lock_new_contact_keys_always_present():
    out = sa.normalize_sticker(_row(), roster_by_codigo={}, evaluacion_by_codigo={})
    for key in ("tarjeta_profesional", "num_telefono", "correo_contacto"):
        assert key in out["inspector"]
        assert out["inspector"][key] == ""
    assert "barrio_reportado" in out and "comuna_reportada" in out


# ── N1: `inspector` key set must be IDENTICAL across every identity
# branch — evaluación match / api / rango-only / plain roster fallback —
# so a frontend can render one shape regardless of which branch produced
# it, never having to guard a branch-specific missing key. ────────────────


def test_inspector_key_set_identical_across_all_four_identity_branches():
    matched_eval = _eval_firestore()
    evaluacion_branch = sa.normalize_sticker(
        _row(), roster_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": matched_eval}
    )
    api_branch = sa.normalize_sticker(
        _row(origen="sistema", profesional={"cedula": "123", "nombre": "Juan Perez", "rango": "P2"}),
        roster_by_codigo={}, evaluacion_by_codigo={}, roster_by_cedula={},
    )
    roster = {"004": {"np": "P4", "nombre_completo": "Ana Gomez", "identificacion": "123",
                      "entidad": "Curaduria 1", "uid": "u-004"}}
    rango_only_branch = sa.normalize_sticker(
        _row(origen="sistema", profesional={"rango": "P9"}),
        roster_by_codigo=roster, evaluacion_by_codigo={},
    )
    roster_branch = sa.normalize_sticker(_row(), roster_by_codigo=roster, evaluacion_by_codigo={})

    branches = {
        "evaluacion": evaluacion_branch,
        "api": api_branch,
        "rango_only": rango_only_branch,
        "roster": roster_branch,
    }
    # Sanity: prove these really are four DIFFERENT branches before
    # asserting their shape agrees.
    fuentes = {name: out["inspector_fuente"] for name, out in branches.items()}
    assert fuentes == {"evaluacion": "evaluacion", "api": "api", "rango_only": "roster", "roster": "roster"}

    key_sets = {name: set(out["inspector"]) for name, out in branches.items()}
    expected = {"uid", "codigo", "nombre_completo", "identificacion", "entidad", "np",
                "tarjeta_profesional", "num_telefono", "correo_contacto"}
    for name, keys in key_sets.items():
        assert keys == expected, f"{name} branch inspector keys: {keys}"


def test_build_evaluaciones_3000_rows_perf_budget():
    import time

    rows = [
        _row(id=str(i), numero=f"76001-1-{i:07d}", origen="sistema" if i % 2 else "firebase",
             fechaCreacion="martes, 18 de agosto de 2026, 06:33 p. m.")
        for i in range(3000)
    ]
    t0 = time.perf_counter()
    out = sa.build_evaluaciones(rows, roster_by_codigo={}, evaluaciones_firestore=[])
    elapsed = time.perf_counter() - t0
    assert len(out) == 3000
    # M6 (adversarial review 2026-09-12): measured ~0.03s -> the old 1.0s
    # budget was 30x looser than reality and would never catch a real
    # regression. 0.25s still comfortably CI-safe (>8x the measured time)
    # while actually able to fail on a real slowdown.
    assert elapsed < 0.25, f"build_evaluaciones took {elapsed:.3f}s for 3000 rows, budget is 0.25s"
