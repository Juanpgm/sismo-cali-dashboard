"""Parity fixture (task 2.27): a representative 10-person sample built from
REAL cross-referenced data — `outputs/inspectores_depurado_seguimiento.xlsx`
("Inspectores Depurado" sheet, sampled 2026-09-16) as the golden expectation,
with inputs reconstructed from the actual raw source files
(`context/EDA_HAMON/01_Insumos/{inspectores_vercel-app.csv,
Listado verificado Fase 2.xlsx, tecnicos atension sismo (12 sept 2026).csv}`)
so this is a genuine forward-computation check, not a tautology.

`depurar()` is run over this sample and `np`/`np_fuente`/`fase`/
`fase_np_faltante`/`codigo`/`fuente_dato`/`estado_sugerido` are diffed
against the golden row for each identity.

## Documented divergences (found by tracing real rows, not invented)

1. **D7 fix (permitted, per design.md D7 / spec's "Estado Sugerido
   Precedence")** — Tatiana Erazo (66826632): golden `estado_sugerido=
   "activo"` via the notebook's bug (`tiene_sticker_valido=true` driving
   `"activo"` even with no `codigo` and no Fase2/Vercel membership). This
   engine deliberately never reads `tiene_sticker_valido` for
   classification, so it correctly returns `"candidato_desactivacion"`
   instead — this is the ONE divergence the task explicitly permits.

2. **Second divergence found, NOT part of D7, flagged for a follow-up spec
   correction** — Mario Fernando Rosas Martinez (13068447): golden
   `estado_sugerido="revisar"` despite being absent from BOTH the Vercel
   and Fase 2 reference sources (confirmed by name AND cédula search
   against the raw source files — no match). `spec.md`'s "Estado Sugerido
   Precedence" requirement conditions `"revisar"` vs
   `"candidato_desactivacion"` ONLY on Fase 2/Vercel membership; it says
   nothing about the base CSV's own `activo` flag. ALL THREE `"revisar"`
   rows in the golden 373-row sheet share this exact profile (`np_fuente=
   "main"`, base CSV `activo="true"`, absent from Fase 2/Vercel) — strongly
   suggesting the notebook's real `ids_revision` set also considered the
   base record's own `activo` flag, a signal `explore.md`'s own citation
   (Q3) never mentioned and `spec.md` never adopted. This engine follows
   `spec.md` literally (the approved, literal contract for this phase) and
   therefore returns `"candidato_desactivacion"` here instead of
   `"revisar"` — documented for a future spec correction pass, not silently
   worked around with an undocumented input this phase was never asked to
   model.

3. **Faber Albeiro Gaviria Salazar (9728480) intentionally excluded from
   this fixture** — his golden `identificacion` (9728480, from Fase2/
   Vercel) does not match his own main-CSV row's `cedula` (3215214340), a
   genuine cross-source cédula mismatch confirmed against the raw files
   (D-P2-shaped), AND he is flagged `no_persona` in "Inspectores Depurado"
   but absent from the "Externos Agrupados (detalle)" sheet — i.e. the
   golden export itself is internally inconsistent about whether he was
   collapsed (this engine, following spec's explicit "hidden from the
   primary list" requirement, WOULD collapse him). Rather than build a
   fixture around two independent, confounding inconsistencies in the
   source export, a cleaner `no_persona`+collapsed pair (Juan Evangelista
   Delgado Gaviria, confirmed in BOTH sheets consistently) is used instead
   for that scenario.
"""
from __future__ import annotations

from datetime import date

from app.services import inspectores_depuracion as dep
from app.services.inspectores_referencia import EntradaReferencia, ReferenciaBundle

nn = dep.normalizar_nombre


def _entrada(cedula_key, nombre, np="", entidad="", codigo=""):
    return EntradaReferencia(
        cedula_key=cedula_key, nombre_norm=nn(nombre), np=np, entidad=entidad,
        codigo=codigo, pasos=(), no_persona=False,
    )


def _roster(identificacion, nombre_completo, correo, *, creado_en=""):
    return {
        "identificacion": identificacion,
        "nombre_completo": nombre_completo,
        "correo": correo,
        "codigo": "",
        "entidad": "",
        "creado_en": creado_en,
    }


ROSTER = {
    "66841363": _roster("66841363", "Luz Adriana Muñoz", "luzadriana72@hotmail.com",
                         creado_en="2026-08-21T23:45:14.225Z"),
    "16720470": _roster("16720470", "Fernando Alberto Padilla Ramirez", "padillaangelconstructora@gmail.com",
                         creado_en="2026-08-21T23:45:14.224Z"),
    "66826632": _roster("66826632", "Tatiana Erazo", "erazotatiana27@gmail.com",
                         creado_en="2026-08-21T23:45:14.225Z"),
    "16621372": _roster("16621372", "Aurelio Agustin Lopez Gaspar", "arquitectoaurelio@gmail.com",
                         creado_en="2026-08-21T23:45:14.224Z"),
    "1088735627": _roster("1088735627", "William Esteban Florez Pantoja", "1088735627@sismocali.gov.co",
                           creado_en="2026-08-21T23:45:14.225Z"),
    "16934168": _roster("16934168", "Diego Fernando Corrales Marin", "dels.soluciones.contacto@gmail.com",
                         creado_en="2026-08-21T23:45:14.225Z"),
    "16735029": _roster("16735029", "Ivan Osvelio Mejia Palomino", "io.mejia@hotmail.com",
                         creado_en="2026-08-21T23:45:14.225Z"),
    "94532806": _roster("94532806", "Juan Pablo Jimenez", "arq.juan.p.jimenez@gmail.com",
                         creado_en="2026-08-21T23:45:14.225Z"),
    "13068447": _roster("13068447", "Mario Fernando Rosas Martinez", "arq.mfernando@gmail.com",
                         creado_en="2026-09-07T14:37:20.359Z"),
    "6320222994": _roster("6320222994", "Juan Evangelista Delgado Gaviria",
                           "arcgis-ede+mat-6320222994@import.local",
                           creado_en="2026-08-22T08:00:45.581Z"),
}

REFERENCIA = ReferenciaBundle(
    vercel=(
        _entrada("16934168", "Diego Fernando Corrales Marin", np="P1", entidad="Voluntario", codigo="098"),
        _entrada("16621372", "Aurelio Agustin Lopez Gaspar", np="P1", entidad="VOLUNTARIO", codigo="041"),
        _entrada("16735029", "Ivan Osvelio Mejia Palomino", np="P1", entidad="VOLUNTARIO", codigo="046"),
        _entrada("16720470", "Fernando Alberto Padilla Ramirez", np="P1", entidad="SGRED", codigo="025"),
        _entrada("1088735627", "William Esteban Florez Pantoja", np="P1", entidad="CAMACOL", codigo="123"),
    ),
    fase2=(
        _entrada("16934168", "Diego Fernando Corrales Marin", np="P1"),
        _entrada("16621372", "Aurelio Agustin Lopez Gaspar", np="P1"),
        _entrada("16735029", "Ivan Osvelio Mejia Palomino", np="P1"),
        _entrada("16720470", "Fernando Alberto Padilla Ramirez", np="P1"),
        _entrada("1088735627", "William Esteban Florez Pantoja", np="P1"),
    ),
    main=(
        _entrada("66841363", "Luz Adriana Muñoz", np=""),
        _entrada("16720470", "Fernando Alberto Padilla Ramirez", np="P1"),
        _entrada("66826632", "Tatiana Erazo", np="P1"),
        _entrada("16621372", "Aurelio Agustin Lopez Gaspar", np="P1"),
        _entrada("1088735627", "William Esteban Florez Pantoja", np="P1"),
        _entrada("16934168", "Diego Fernando Corrales Marin", np="P1"),
        _entrada("16735029", "Ivan Osvelio Mejia Palomino", np="P1"),
        _entrada("94532806", "Juan Pablo Jimenez", np=""),
        _entrada("13068447", "Mario Fernando Rosas Martinez", np="P1"),
        _entrada("6320222994", "Juan Evangelista Delgado Gaviria", np=""),
    ),
    generado_en="2026-09-12", activa=True, motivo="", codigos_duplicados=(),
)

# identidad_key -> expected (np, np_fuente, fase, fase_np_faltante, codigo,
# fuente_dato, estado_sugerido). `None` for `estado_sugerido` marks a
# documented divergence (checked separately, never silently skipped).
GOLDEN = {
    "66841363": ("", "ninguno", "Fase I", True, "", "main", "candidato_desactivacion"),
    "16720470": ("P1", "fase2", "Fase I", False, "025", "main+fase2+vercel", "activo"),
    "66826632": ("P1", "main", "Fase I", False, "", "main", None),  # D7 divergence
    "16621372": ("P1", "fase2", "Fase I", False, "041", "main+fase2+vercel", "activo"),
    "1088735627": ("P1", "fase2", "Fase I", False, "123", "main+fase2+vercel", "activo"),
    "16934168": ("P1", "fase2", "Fase I", False, "098", "main+fase2+vercel", "activo"),
    "16735029": ("P1", "fase2", "Fase I", False, "046", "main+fase2+vercel", "activo"),
    "94532806": ("", "ninguno", "Fase I", True, "", "main", "candidato_desactivacion"),
    "13068447": ("P1", "main", "Fase I", False, "", "main", None),  # activo-flag divergence
    "6320222994": ("", "ninguno", "Fase I", True, "", "main", "no_persona"),
}

DIVERGENCES = {
    "66826632": "candidato_desactivacion",  # D7 fix — golden had "activo" via tiene_sticker_valido bug
    "13068447": "candidato_desactivacion",  # golden had "revisar" via an undocumented activo-flag signal
}


def _run():
    return dep.depurar(
        stickers=[], roster_by_cedula=ROSTER, nombres_survey=[],
        referencia=REFERENCIA, hoy=date(2026, 9, 12),
    )


def test_parity_np_fase_codigo_fuente_dato_match_golden_for_every_sampled_row():
    resultado = _run()
    by_key = {i["identidad_key"]: i for i in resultado.inspectores}
    for identidad_key, (np, np_fuente, fase, fase_faltante, codigo, fuente, _estado) in GOLDEN.items():
        if identidad_key == "6320222994":
            continue  # collapsed by design — asserted separately below
        inspector = by_key[identidad_key]
        assert inspector["np"] == np, identidad_key
        assert inspector["np_fuente"] == np_fuente, identidad_key
        assert inspector["fase"] == fase, identidad_key
        assert inspector["fase_np_faltante"] == fase_faltante, identidad_key
        assert inspector["codigo"] == codigo, identidad_key
        assert inspector["fuente_dato"] == fuente, identidad_key


def test_parity_estado_sugerido_matches_golden_except_documented_divergences():
    resultado = _run()
    by_key = {i["identidad_key"]: i for i in resultado.inspectores}
    for identidad_key, (*_rest, estado_golden) in GOLDEN.items():
        if identidad_key == "6320222994":
            continue
        if identidad_key in DIVERGENCES:
            assert by_key[identidad_key]["estado_sugerido"] == DIVERGENCES[identidad_key]
            continue
        assert by_key[identidad_key]["estado_sugerido"] == estado_golden, identidad_key


def test_parity_no_persona_row_is_collapsed_not_standalone():
    resultado = _run()
    keys = {i["identidad_key"] for i in resultado.inspectores}
    assert "6320222994" not in keys
    assert resultado.grupo_externos is not None
    detalle_ids = {d["identificacion"] for d in resultado.grupo_externos["detalle"]}
    assert "6320222994" in detalle_ids


def test_parity_no_unexpected_revision_manual_entries():
    resultado = _run()
    assert resultado.revision_manual == ()
