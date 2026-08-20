"""Remapeo del survey estructural del equipo de Israel (formulario ArcGIS en
hebreo) al esquema EDE normalizado de Cali que produce `scripts/refresh_data.py`.

Los dos formularios cubren el mismo dominio (etiquetado de seguridad de edificios
post-sismo) con esquemas distintos. Este módulo traduce los campos israelíes a los
nombres y vocabularios `snake_case` de Cali y REINDEXA la salida al esquema completo
de `inspections.json`, de modo que el DataFrame resultante tenga exactamente las
mismas columnas normalizadas que el primer survey (los campos sin equivalente quedan
en None).

DEFENSIVO — todo lo mapeado usa el VOCABULARIO REAL de Cali (verificado contra
inspections.json). Las derivaciones de varios campos (grados de daño, evaluación
requerida) son heurísticas documentadas campo por campo. Límites conocidos:
  * `criterio_habitabilidad`: la escala israelí `BldDetailedRate` tiene 4 niveles;
    Cali distingue i1/i2/i3 y r1/r2. El código fino NO es recuperable → `criterio_color`
    (verde/amarillo/rojo) es el mapeo robusto y el código EDE es aproximación.
  * `sistema_estructural`/`material_estructura`: heurísticos (ejes de clasificación
    distintos). Ver comentarios en las funciones _sistema/_material.
  * `afectacion_planta`, `epoca_construccion`, `frente/fondo`, `n_ocupantes`,
    `estado_edificacion`, etc.: el formulario israelí NO los captura → None.
  * Columnas derivadas del pipeline (comuna, barrio_geo, direccion_norm, geocode_*,
    *_calc, coords_*): quedan None; se llenarían pasando la salida por refresh_data.

Uso:
    python israel_to_cali.py --check   # self-check offline, sin red
    python israel_to_cali.py           # baja el survey y escribe puntos_israel_cali.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
LAYER = ("https://services-eu1.arcgis.com/eeu6dGizBqA14mjm/ArcGIS/rest/services/"
         "service_c9a79f2605a4455582d81c12ec3ba2f3_form/FeatureServer/0")
INSPECTIONS_JSON = HERE / "web" / "data" / "inspections.json"
OUT_JSON = HERE / "puntos_israel_cali.json"

# Basemaps CRUDOS (no los preparados de web/data): mismo point-in-polygon que
# scripts/refresh_data.spatial_join, pero contra estas capas directamente.
COMUNAS_GEOJSON = HERE / "basemaps" / "comunas_corregimientos.geojson"
BARRIOS_GEOJSON = HERE / "basemaps" / "barrios_veredas.geojson"

# Constantes de contexto: todos los puntos son del sismo de Cali.
MUNICIPIO = "Cali"
TIPO_EVENTO = "sismo"
FUENTE = "survey_israel"
ENTIDAD = "Inspectores de Israel"   # campo EDE "Especifique la entidad:" -> identifica el equipo

# Renames directos (valor pasa igual; texto libre en hebreo se conserva).
FIELD_MAP = {
    "surveyorNameTwo": "nombre_evaluador",
    "Building_Address": "direccion",
    "floorNo": "n_pisos",
    "basementParkingNo": "n_sotanos",
    "remarks": "observaciones",                    # texto libre (hebreo)
    "generalRemarks": "observaciones_generales",   # texto libre (hebreo)
    "NeedActionsDesc": "recomendaciones",          # texto libre (hebreo)
    "dangerousAreaDesc": "aislamiento",
    "buildingTypeOther": "sistema_estructural_cual",
    "NeedExpertOther": "eval_otra",
    "globalid": "GlobalID",   # ArcGIS sirve el campo en minúscula; Cali lo normaliza a GlobalID
    "Creator": "Creator",
}

RATE_TO_CRITERIO = {"1": "h", "4": "r1", "3": "r2", "2": "i2"}
RATE_TO_COLOR = {"1": "verde", "4": "amarillo", "3": "amarillo", "2": "rojo"}
RATE_TO_NIVEL = {"1": "sin_dano", "4": "bajo", "3": "medio", "2": "alto"}


def _code(v):
    """Normaliza un código a string ('2.0'->'2', None/nan->None)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return str(int(float(v)))
    except (ValueError, TypeError):
        s = str(v).strip()
        return s or None


def _yn(v):
    """Código israelí 0/1 -> True/False/None."""
    c = _code(v)
    return {"1": True, "0": False}.get(c)


def _material(r):
    """buildingType (+ esqueleto/no-esqueleto) -> material_estructura de Cali."""
    bt = _code(r.get("buildingType"))
    if bt == "2":  # hormigón
        return "conc_prefab_mc" if _code(r.get("buildingBetonSkeleton")) == "5" else "concreto_mc"
    if bt == "1":  # mampostería / sin esqueleto
        return "mamp_confinada" if _code(r.get("buildingNonSkeleton")) == "6" else "mamp_simple"
    if bt == "4":  # prefabricado
        return "conc_prefab_mc"
    if bt in ("3", "5"):  # acero / otro (sin código propio en Cali)
        return "mat_no_claro"
    return None


def _sistema(r):
    """buildingBetonSkeleton (eje lateral) -> sistema_estructural de Cali."""
    bs = _code(r.get("buildingBetonSkeleton"))
    m = {"1": "muros_carga", "2": "porticos", "3": "porticos", "4": "muros_carga", "5": "otro"}
    if bs in m:
        return m[bs]
    bt = _code(r.get("buildingType"))
    if bt == "1":
        return "muros_carga"          # mampostería suele ser muro de carga
    if bt in ("3", "4", "5"):
        return "otro"
    return None


def _danos_estructura(r):
    """Grado de daño estructural desde las banderas de grieta israelíes."""
    crit = any(_yn(r.get(k)) for k in ("cracksCritical", "cracksTromy", "cracksPenetration"))
    struct = any(_yn(r.get(k)) for k in ("cracksBeam", "cracksDiagonal", "cracksBending"))
    if crit:
        return "severo"
    if struct:
        return "moderado"
    if _yn(r.get("Cracks")):
        return "leve"
    if _code(r.get("Cracks")) == "0":
        return "sin_dano"
    return None


def _danos_muro_div(r):
    """Grado de daño en muros divisorios / arquitectónicos."""
    if _yn(r.get("ArchitecturalDmgCritical")):
        return "severo"
    if _yn(r.get("CracksPeriferal")):
        return "moderado"
    if _yn(r.get("CracksNegligible")):
        return "leve"
    if _code(r.get("Cracks")) == "0" and _code(r.get("ArchitecturalDmg")) == "0":
        return "sin_dano"
    return None


def _si_no(cond_si, known):
    return "si" if cond_si else ("no" if known else None)


def _requiere_eval(r, dano_estruct):
    """requiere_evaluacion_adicional: combos 'estructural,geotecnica,otra' de Cali."""
    parts = []
    if _yn(r.get("NeedSageActions")) or dano_estruct in ("moderado", "severo"):
        parts.append("estructural")
    if _yn(r.get("NeedExpertGeotechny")):
        parts.append("geotecnica")
    if _yn(r.get("NeedDangerousMaterials")) or _code(r.get("NeedExpertOther")):
        parts.append("otra")
    return ",".join(parts) or None


def _uso(r):
    """bldUsageText: solo 'מוסד רפואי' (institución médica) -> salud; resto opaco -> None."""
    t = r.get("bldUsageText")
    if isinstance(t, str) and "רפואי" in t:
        return "salud"
    return None


def _transform(r: dict) -> dict:
    """Una fila israelí cruda -> dict con columnas del esquema de Cali."""
    o = {}
    for src, dst in FIELD_MAP.items():
        v = r.get(src)
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            o[dst] = v

    rate = _code(r.get("BldDetailedRate"))
    o["criterio_habitabilidad"] = RATE_TO_CRITERIO.get(rate)
    o["criterio_color"] = RATE_TO_COLOR.get(rate)

    o["sistema_estructural"] = _sistema(r)
    o["material_estructura"] = _material(r)

    coll = _code(r.get("CollapseElements"))
    if coll is not None:
        o["colapso_total"] = "si" if coll == "2" else "no"
        o["colapso_parcial"] = "si" if coll in ("5", "6") else "no"

    o["inclinacion_importante"] = _si_no(_yn(r.get("bldTendency")), _yn(r.get("bldTendency")) is not None)
    o["riesgo_caida"] = _si_no(
        any(_yn(r.get(k)) for k in ("ArchitecturalDmg", "ArchitecturalDmgCover", "ArchitecturalDmgOther"))
        or coll in ("3", "4"),
        _code(r.get("ArchitecturalDmg")) is not None)

    base_known = _code(r.get("baseDamages")) is not None
    o["suelo_inestable"] = _si_no(
        any(_yn(r.get(k)) for k in ("baseDamages", "baseDmgSignificantRisk", "baseDmgExposure")), base_known)
    o["asentamiento_severo"] = _si_no(
        any(_yn(r.get(k)) for k in ("baseDmgSignificantRisk", "baseDmgExposure")), base_known)

    de = _danos_estructura(r)
    o["danos_estructura"] = de
    o["danos_muro_div"] = _danos_muro_div(r)

    # nivel de daño: colapso total domina; si no, grado estructural; backstop = rate
    if o.get("colapso_total") == "si":
        o["nivel_dano"] = "alto"
    elif de is not None:
        o["nivel_dano"] = {"severo": "alto", "moderado": "medio", "leve": "bajo", "sin_dano": "sin_dano"}[de]
    else:
        o["nivel_dano"] = RATE_TO_NIVEL.get(rate)
    o["severidad_danos"] = o["nivel_dano"]   # misma escala alto/medio/bajo/sin_dano

    o["requiere_evaluacion_adicional"] = _requiere_eval(r, de)

    infra = _code(r.get("NeedInfraImmediate"))
    o["suspension_servicios"] = "si" if infra else "no"

    o["alc_exterior"] = {"1": "completa", "2": "parcial"}.get(_code(r.get("scanAreaFullPartial")))
    o["alc_interior"] = {"1": "no_ingreso", "2": "completa"}.get(_code(r.get("scanAreaOuterInner")))

    o["uso_edificacion"] = _uso(r)
    usage = r.get("bldUsageText")
    if isinstance(usage, str) and "רפואי" not in usage:
        o["uso_cual"] = usage   # códigos opacos -> se conservan crudos, sin perder info

    # ObjectID: key load-bearing del dashboard (marcador, modal de detalle, fila).
    # Prefijo 'isr-' -> nunca colisiona con los ObjectID enteros de Cali si se
    # fusiona en inspections.json; el dashboard lo compara como String(ObjectID).
    oid = _code(r.get("objectid"))
    if oid is not None:
        o["ObjectID"] = f"isr-{oid}"

    o["municipio"] = MUNICIPIO
    o["tipo_evento"] = TIPO_EVENTO
    o["entidad"] = ENTIDAD
    o["fuente"] = FUENTE
    return o


def remap(df: pd.DataFrame, target_cols: list[str] | None = None) -> pd.DataFrame:
    """DataFrame de campos israelíes crudos -> DataFrame con esquema Cali.
    Si `target_cols`, reindexa a ese esquema completo (campos faltantes -> None),
    conservando además `criterio_color`/`fuente` al final."""
    out = pd.DataFrame([_transform(r) for r in df.to_dict("records")], index=df.index)

    if "surveyDate" in df.columns:
        dt = pd.to_datetime(df["surveyDate"], unit="ms", errors="coerce")
        out["fecha_inspeccion"] = dt.dt.strftime("%Y-%m-%d")
        out["hora"] = dt.dt.strftime("%H:%M")
        out["fecha_hora"] = dt.dt.strftime("%Y-%m-%d %H:%M")
    if "lon" in df.columns:
        out["x"] = out["x_form"] = df["lon"].values
    if "lat" in df.columns:
        out["y"] = out["y_form"] = df["lat"].values

    if target_cols is not None:
        extras = [c for c in ("criterio_color", "fuente") if c not in target_cols]
        out = out.reindex(columns=list(target_cols) + extras)
    return out


def _load_polygons(path: Path, name_prop: str):
    """Geometrías + nombre de un basemap crudo (features GeoJSON)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    from shapely.geometry import shape
    geoms, names = [], []
    for ft in data["features"]:
        geoms.append(shape(ft["geometry"]))
        names.append(ft["properties"].get(name_prop))
    return geoms, names


PREPARED_BARRIOS_JSON = HERE / "web" / "data" / "barrios.geojson"


def _repair_barrio_names(names: list) -> list:
    """Repara el mojibake U+FFFD (tildes/ñ perdidas) de los nombres de barrio,
    reusando scripts/basemap_utils (mismo reparador que prepare_basemaps.py).
    Referencia: los nombres YA canónicos del basemap preparado del proyecto
    (web/data/barrios.geojson), para que el resultado sea idéntico al pipeline
    de Cali y una por nombre con los coropletas del dashboard."""
    sys.path.insert(0, str(HERE / "scripts"))
    import basemap_utils as bu
    ref = []
    if PREPARED_BARRIOS_JSON.exists():
        prep = json.loads(PREPARED_BARRIOS_JSON.read_text(encoding="utf-8"))
        ref = bu.build_reference_tokens([ft["properties"].get("name") for ft in prep["features"]])
    mapping, _ = bu.repair_all_barrio_names([n for n in names if n], ref)
    return [mapping.get(n, n) for n in names]


def fill_comuna_barrio(df: pd.DataFrame) -> pd.DataFrame:
    """Rellena `comuna` y `barrio_geo` por point-in-polygon contra los basemaps
    CRUDOS (basemaps/comunas_corregimientos.geojson y barrios_veredas.geojson).
    Misma lógica que scripts/refresh_data.spatial_join."""
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    cg, cn = _load_polygons(COMUNAS_GEOJSON, "comuna_corregimiento")
    bg, bn = _load_polygons(BARRIOS_GEOJSON, "barrio_vereda")
    bn = _repair_barrio_names(bn)   # corrige mojibake U+FFFD (tildes, ñ, acentos)
    ctree, btree = STRtree(cg), STRtree(bg)

    def match(tree, geoms, names, x, y):
        if pd.isna(x) or pd.isna(y):
            return None
        p = Point(x, y)
        for idx in tree.query(p, predicate="intersects"):
            if geoms[idx].covers(p):
                return names[idx]
        return None

    xy = list(zip(df["x"], df["y"]))
    df["comuna"] = [match(ctree, cg, cn, x, y) for x, y in xy]
    df["barrio_geo"] = [match(btree, bg, bn, x, y) for x, y in xy]
    return df


def fetch_raw() -> pd.DataFrame:
    """Baja el survey israelí (features -> DataFrame de campos crudos + lon/lat)."""
    params = {"where": "BldDetailedRate is not NULL", "outFields": "*",
              "returnGeometry": "true", "outSR": 4326, "f": "json"}
    feats = requests.get(LAYER + "/query", params=params, timeout=60).json()["features"]
    rows = []
    for f in feats:
        a = dict(f["attributes"])
        g = f.get("geometry") or {}
        a["lon"], a["lat"] = g.get("x"), g.get("y")
        rows.append(a)
    return pd.DataFrame(rows)


def cali_schema() -> list[str]:
    """Columnas del primer survey (inspections.json), en orden."""
    recs = json.loads(INSPECTIONS_JSON.read_text(encoding="utf-8"))
    cols = []
    for r in recs:
        for k in r:
            if k not in cols:
                cols.append(k)
    return cols


def selfcheck() -> None:
    """Offline: valida el remapeo sobre filas sintéticas conocidas."""
    raw = pd.DataFrame([
        # apto, sin colapso, hormigón/pórticos, grieta leve, acciones básicas
        {"BldDetailedRate": 1, "CollapseElements": 1, "bldTendency": 0,
         "buildingType": 2, "buildingBetonSkeleton": 2, "ArchitecturalDmg": 0,
         "Cracks": 1, "NeedActionsDesc": "reparar", "surveyDate": 1786888639859,
         "floorNo": 3, "surveyorNameTwo": "X", "Building_Address": "Calle 1",
         "GlobalID": "aaa", "scanAreaFullPartial": 1, "scanAreaOuterInner": 2,
         "baseDamages": 0, "NeedExpertGeotechny": 0, "bldUsageText": "מוסד רפואי"},
        # peligroso, derrumbe total, mampostería, grieta crítica, geotécnico+obras
        {"BldDetailedRate": 2, "CollapseElements": 2, "bldTendency": 1,
         "buildingType": 1, "ArchitecturalDmg": 1, "cracksCritical": 1,
         "NeedSageActions": 1, "NeedExpertGeotechny": 1, "baseDamages": 1,
         "baseDmgSignificantRisk": 1, "NeedInfraImmediate": 1, "floorNo": 1,
         "surveyorNameTwo": "Y", "Building_Address": "Cra 2", "GlobalID": "bbb",
         "bldUsageText": "2"},
        # apto con zonas peligrosas, derrumbe local -> parcial, prefab
        {"BldDetailedRate": 4, "CollapseElements": 6, "bldTendency": 0,
         "buildingType": 4, "ArchitecturalDmg": 0, "Cracks": 0, "floorNo": 2,
         "surveyorNameTwo": "Z", "Building_Address": "Av 3", "GlobalID": "ccc",
         "scanAreaFullPartial": 2, "scanAreaOuterInner": 1},
    ])
    o = remap(raw)
    eq = lambda col, exp: [None if (isinstance(v, float) and pd.isna(v)) else v for v in o[col]] == exp
    assert eq("criterio_habitabilidad", ["h", "i2", "r1"]), o["criterio_habitabilidad"].tolist()
    assert eq("criterio_color", ["verde", "rojo", "amarillo"]), o["criterio_color"].tolist()
    assert eq("colapso_total", ["no", "si", "no"]), o["colapso_total"].tolist()
    assert eq("colapso_parcial", ["no", "no", "si"]), o["colapso_parcial"].tolist()
    assert eq("inclinacion_importante", ["no", "si", "no"]), o["inclinacion_importante"].tolist()
    assert eq("sistema_estructural", ["porticos", "muros_carga", "otro"]), o["sistema_estructural"].tolist()
    assert eq("material_estructura", ["concreto_mc", "mamp_simple", "conc_prefab_mc"]), o["material_estructura"].tolist()
    assert eq("danos_estructura", ["leve", "severo", "sin_dano"]), o["danos_estructura"].tolist()
    assert eq("nivel_dano", ["bajo", "alto", "sin_dano"]), o["nivel_dano"].tolist()
    assert eq("suelo_inestable", ["no", "si", None]), o["suelo_inestable"].tolist()
    assert eq("requiere_evaluacion_adicional", [None, "estructural,geotecnica", None]), o["requiere_evaluacion_adicional"].tolist()
    assert eq("suspension_servicios", ["no", "si", "no"]), o["suspension_servicios"].tolist()
    assert eq("alc_exterior", ["completa", None, "parcial"]), o["alc_exterior"].tolist()
    assert eq("alc_interior", ["completa", None, "no_ingreso"]), o["alc_interior"].tolist()
    assert eq("uso_edificacion", ["salud", None, None]), o["uso_edificacion"].tolist()
    assert o.loc[0, "fecha_inspeccion"] == "2026-08-16", o.loc[0, "fecha_inspeccion"]
    assert list(o["municipio"].unique()) == ["Cali"], o["municipio"].tolist()

    # reindex al esquema completo: todas las columnas objetivo presentes
    o2 = remap(raw, target_cols=["direccion", "criterio_habitabilidad", "afectacion_planta"])
    assert list(o2.columns)[:3] == ["direccion", "criterio_habitabilidad", "afectacion_planta"], o2.columns.tolist()
    assert o2["afectacion_planta"].isna().all(), "afectacion_planta no es mapeable -> None"
    print("israel_to_cali self-check OK")


def main() -> None:
    if "--check" in sys.argv:
        selfcheck()
        return
    schema = cali_schema()
    out = remap(fetch_raw(), target_cols=schema)
    out = fill_comuna_barrio(out)   # comuna/barrio_geo desde los basemaps crudos
    records = [{k: (None if isinstance(v, float) and pd.isna(v) else v)
                for k, v in row.items()} for row in out.to_dict("records")]
    OUT_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=1, allow_nan=False),
                        encoding="utf-8")
    filled = [c for c in out.columns if out[c].notna().any()]
    print(f"remapeados {len(out)} edificios -> {OUT_JSON.name}")
    print(f"esquema Cali: {len(schema)} columnas | con dato: {len(filled)} | vacías: {len(out.columns) - len(filled)}")
    print(out["criterio_habitabilidad"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
