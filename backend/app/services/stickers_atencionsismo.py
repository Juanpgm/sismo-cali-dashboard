"""Normalize atencionsismo `GET /api/informe/stickers` rows into the
dashboard's evaluaciones shape (design D1/D2 in
docs/superpowers/specs/2026-09-08-atencionsismo-reportes-ciudadanos-stickers-design.md).

Pure functions, no I/O. Fase I/II input (`inspector.np`) is derived from
the identity priority chain below (Rule A / Rule B).

`inspector_fuente` (2026-09-08): a top-level `"evaluacion" | "roster" | "api"
| ""` flag on every returned record, added because the roster fallback below
is a real misattribution risk — brigade codes are reused once an inspector
is deleted, so an old sticker can end up displaying the CURRENT holder's
real name and cédula (a specific, named, uninvolved person), not just a
wrong Fase. Callers use this flag to caveat/relabel a roster-sourced
identity instead of presenting it with the same confidence as a verified
match.

Contrato v3 (2026-09-08) + Rule A/B precedence (2026-09-15 — supersedes the
old "a matched evaluación always wins identity" contract below): the
atencionsismo API's `profesional` ({cedula, nombre, rango,
tarjetaProfesional}) is documented as `evaluacion.tecnico`
(context/mejoras_seguimiento/uploads/api-informe-json.md L278, L416) — the
technician tied to THIS evaluation, for every sticker, not an unverified
side channel. A live sample (2026-09-15, 2561 stickers) found `profesional`
fully populated (cedula + nombre + tarjetaProfesional, rango on all but 4)
on 1121/1125 `origen == "sistema"` rows AND on 990/1436 `origen ==
"firebase"` rows (only 446 firebase rows have an empty `profesional`) — the
old "`profesional` is only reliable for `origen == 'sistema'`" restriction
below was verified FALSE and has been removed: `profesional` is now
consulted regardless of `origen`. The Survey/Firestore-evaluación-match
identity path is now legacy: it is a FALLBACK (Rule B), not the default.
Priority is:

Rule A — `profesional` names a person (non-blank `cedula` OR non-blank
`nombre`, after `_profesional_fields` cleaning): identity comes from the
API UNCONDITIONALLY — regardless of `origen`, and regardless of whether a
Firestore evaluación also matched by code (`identificacion = cedula`,
verbatim/stripped, NOT the join key below; `nombre_completo = nombre`;
`np = rango`, verbatim, not parsed; `tarjeta_profesional =
tarjetaProfesional`, cleaned). `roster_by_cedula` is joined by
`cedula_key` (digits only, both sides — F7), so a formatted cédula like
"1.234.567" still matches a roster doc stored as "1234567"; this join
carries none of the brigade-code reuse risk since a cédula is unique per
person. The roster only fills `uid`/`entidad`/`num_telefono`/
`correo_contacto`, and backfills `np`/`nombre_completo`/
`tarjeta_profesional` when the API left them blank — the API always wins
on conflict. `inspector_fuente = "api"`.

A matched evaluación's OWN `inspector` sub-object (`insp_match`) is
CONSULTED on this path ONLY for one narrow same-person backfill (fresh
review, 2026-09-15): if `np`/`entidad`/`uid` are still blank after the API
and `roster_by_cedula` above, AND `insp_match.identificacion` equals the
API's `cedula` after digits-only normalization (`cedula_key` — the SAME
person, confirmed, not a guess), that field is filled from `insp_match`.
Different cédula, either side blank, or no match at all -> never touched,
stays "". `identificacion`/`nombre_completo` are NEVER part of this
backfill (the API already owns them outright above), and neither is
`codigo`: a matched evaluación's brigade `codigo` is NEVER used on this
path (a different sticker's/person's brigade code, not this one's) — only
`roster_by_cedula`'s own `codigo` (if the cédula is in the roster) or the
code parsed straight from THIS sticker's own `numero` are legitimate
`codigo` sources here. Everything else the match still drives
(`fecha`/`fecha_fuente`, `fotos`, `clasificacion`, `alcance`, `coords`,
`descripcion`, `restricciones`, `acciones_posteriores`, `comentarios`) is
UNAFFECTED by this rule and keeps coming from the match exactly as before.

Rule B — `profesional` does not name a person (fallback chain, unchanged
in substance since before Rule A existed):

1. Matching Firestore evaluación by code: its own `inspector` fields (np,
   nombre_completo, identificacion, entidad, uid) are AUTHORITATIVE even
   when empty (`inspector_fuente = "evaluacion"`) — brigade codes are
   reused once an inspector is deleted, so falling back to the roster here
   could hand an old evaluación the new inspector's identity.
2. No match, `profesional` has no person but a non-blank `rango`: an NP
   with nobody named has nobody to attribute, so sources are never mixed
   field-by-field (same invariant as step 1) — if the brigade-code roster
   already names a person, its OWN `np` is kept and the API's `rango` is
   ignored entirely (`inspector_fuente = "roster"`); only when the roster
   has NO identity at all (no entry, or an np-only entry) does the bare
   `rango` become `np`, and `inspector_fuente` stays `""` (still nobody to
   attribute).
3. No match, and `profesional` has no usable data at all: the
   roster-by-brigade-code fallback, unchanged since before `profesional`
   existed.

"Names a person" (steps 2 and 3 above) means a non-blank
`nombre_completo`, `identificacion` or `entidad` on the roster entry — NOT
`uid` (F2, 2026-09-08). `inspector_profiles` always sets `uid` to the
Firestore doc id, so every roster entry has one whether or not it names
anyone; counting `uid` here would make an np-only roster doc (e.g. one
carrying only `NP`) wrongly read as "roster" for every real caller.

`fase` (2026-09-08, contrato v3) IS the Fase I/II signal for the Stickers
tab: the atencionsismo API developer confirmed (2026-09-08) that `fase` is
their own Fase variable — `1` = Fase I, `2` = Fase II — per atencionsismo's
own process definition (paso 1 vs paso 2 / evaluación especializada), not a
bare storage-column artifact. It is surfaced verbatim as `fase` and is what
`web/js/evaluaciones.js` faseDe reads for `fuente === "atencionsismo"`
records, falling back to `inspector.np` (via `faseInspector`) only when
`fase` is `None`.

Factual note: every row imported from OUR Firebase (`origen == "firebase"`)
carries `fase: 2` regardless of the matched inspector's real NP (verified:
1068 of 1436 such rows have a P1/P2 inspector in Firestore). The
Firestore-sourced Evaluaciones tab still classifies those same evaluaciones
by inspector NP (unaffected by this contract), so the Stickers tab and the
Evaluaciones tab can disagree on the Fase of the same evaluación — by
design, since they are now two independent signals.
"""
from __future__ import annotations

import re
from datetime import timezone
from typing import Any

from app.services import fechas_es_co

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


def _profesional_fields(row: dict) -> tuple[str, str, str, str]:
    """Cleaned (cedula, nombre, rango, tarjeta_profesional) from the API's
    `profesional` object, tolerant of it being missing, None, or not a dict
    at all — every one of those is treated as "no data", never a crash.
    Values pass through `_clean` (F1), not a bare `.strip()`: the API's own
    placeholder strings ("Sin código"/"Sin dirección"/"Sin identificar")
    are not real data and must collapse to "" here too, same as every
    other API field — a bare `.strip()` used to leave them as truthy
    non-empty text, which could wrongly read as `profesional` "naming a
    person" (step 2) or carrying a usable bare rango (step 3) when it
    actually carried nothing at all. `tarjetaProfesional` (W3, plan
    cozy-wobbling-dragonfly — undocumented API field) follows the exact
    same cleaning rule."""
    profesional = row.get("profesional")
    if not isinstance(profesional, dict):
        profesional = {}
    cedula = _clean(profesional.get("cedula"))
    nombre = _clean(profesional.get("nombre"))
    rango = _clean(profesional.get("rango"))
    tarjeta = _clean(profesional.get("tarjetaProfesional"))
    return cedula, nombre, rango, tarjeta


def _fase(value: object) -> int | None:
    """Coerce the raw API `fase` into `1`, `2` or `None` — the Stickers tab's
    own Fase I/II signal per atencionsismo's process (see module docstring),
    not a bare traceability artifact. `bool` is deliberately excluded even
    though it is technically an `int` subclass: the API never documents
    booleans for this field, and accepting `True`/`False` as 1/0 would be
    guessing at an undocumented contract. A float coerces only when it is an
    exact integer value (e.g. `2.0` -> `2`); anything else (3, 0, "x",
    missing, non-integer floats) becomes `None`, and the frontend falls back
    to `inspector.np` in that case."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in (1, 2) else None
    if isinstance(value, float):
        if value.is_integer() and int(value) in (1, 2):
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        return int(text) if text in ("1", "2") else None
    return None


def _fotos_from_fotografias(value: object) -> list[str]:
    """`fotografias` ([{id, url}]) -> plain list of `url` strings, used only
    on the no-match path (a matched Firestore evaluación keeps its own
    `fotos` as today). Tolerant of `fotografias` missing/None/non-list, and
    of individual entries that are not dicts or carry no non-blank string
    `url` — those are skipped rather than raising. F8: stored stripped
    (never with stray leading/trailing whitespace), and skipped entirely
    when the stripped value does not start with `http://`/`https://` — a
    relative path or another scheme is not a usable image source in the
    dashboard/PDF, which always renders `fotos` as a bare `<img src>`."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str):
            continue
        stripped = url.strip()
        if stripped.startswith("http://") or stripped.startswith("https://"):
            out.append(stripped)
    return out


def cedula_key(value: object) -> str:
    """Digits-only join key for a cédula, used on BOTH sides of the
    `roster_by_cedula` join (F7): strips dots, spaces, dashes and any other
    non-digit punctuation so "1.234.567", 1234567 (int) and " 1234567 " all
    resolve to the same key. Returns "" when nothing digit-like remains
    (blank/whitespace-only input) — callers treat "" as "no key" and never
    look it up in the roster. This is a JOIN KEY ONLY: the API's own cedula
    is still stored verbatim (stripped) as `inspector.identificacion`."""
    return re.sub(r"\D", "", str(value or ""))


def normalize_sticker(
    row: dict,
    *,
    roster_by_codigo: dict[str, dict[str, str]],
    evaluacion_by_codigo: dict[str, dict],
    roster_by_cedula: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    roster_by_cedula = roster_by_cedula or {}
    sticker_id = str(row.get("id") or "").strip()
    if not sticker_id:
        return None

    codigo = _clean(row.get("numero"))
    parsed = parse_codigo(codigo)
    match = evaluacion_by_codigo.get(codigo) if codigo else None
    insp_match = (match or {}).get("inspector") or {}
    codigo_inspector = str(insp_match.get("codigo") or (parsed or {}).get("codigo_inspector") or "")
    # D1 (updated): if a Firestore evaluación matched AND `profesional`
    # names nobody (Rule B below), its inspector fields (np,
    # nombre_completo, identificacion, entidad, uid) are AUTHORITATIVE even
    # when empty — the roster is consulted ONLY when there is no match.
    # Brigade codes are reused once an inspector is deleted, so falling
    # back to the roster here could hand an old evaluación the new
    # inspector's identity.
    roster_match = roster_by_codigo.get(codigo_inspector, {}) if codigo_inspector else {}
    cedula_api, nombre_api, rango_api, tarjeta_api = _profesional_fields(row)
    profesional_names_person = bool(cedula_api or nombre_api)
    # `origen_value` still drives `fecha_fuente` resolution below — it no
    # longer gates trust in `profesional` (see Rule A in the module
    # docstring, 2026-09-15).
    origen_value = str(row.get("origen") or "").strip().lower()
    # Default `codigo` output for every branch except Rule A (overridden
    # inside it below, per the fresh-review fix) — a matched evaluación's
    # own codigo when there's a match, else the code parsed from `numero`,
    # exactly as `codigo_inspector`/`roster_match` above already compute.
    codigo_value = codigo_inspector
    if profesional_names_person:
        # Rule A (2026-09-15, module docstring): `profesional` IS
        # `evaluacion.tecnico` per the API's own docs — the technician tied
        # to THIS evaluation — verified populated regardless of `origen`.
        # It wins identity UNCONDITIONALLY, even when a Firestore
        # evaluación also matched by code: the Survey-match identity path
        # (Rule B, below) is now legacy and only a fallback for when
        # `profesional` names nobody. `cedula` is a unique per-person key,
        # unlike the reused brigade `codigo`, so joining it against
        # `roster_by_cedula` (by the digits-only `cedula_key`, F7) carries
        # no misattribution risk. The API always wins on conflict; the
        # roster only fills what the API left blank. `identificacion`
        # stores the API's cedula VERBATIM (stripped) — the normalised key
        # is a join key only. `insp_match` (a matched evaluación's own
        # inspector) is NEVER consulted here — never mixed field-by-field
        # with `profesional` — but everything else the match drives
        # (fecha, fotos, clasificacion, coords, ...), computed
        # independently below, is unaffected by this branch.
        cedula_join_key = cedula_key(cedula_api)
        roster_cedula_match = roster_by_cedula.get(cedula_join_key, {}) if cedula_join_key else {}
        # Fresh review (2026-09-15): `codigo` must NEVER come from the
        # matched evaluación's brigade code (`insp_match`) on this path —
        # that would silently reattach a DIFFERENT sticker's brigade code
        # to this API-identified person. Only `roster_by_cedula`'s own
        # `codigo` (a per-person field, safe to trust like every other
        # `roster_cedula_match` field above) or the code parsed straight
        # from this sticker's own `numero` are legitimate sources here.
        codigo_value = str(roster_cedula_match.get("codigo") or "").strip() \
            or str((parsed or {}).get("codigo_inspector") or "")
        # Fresh review (2026-09-15): same-person backfill for np/entidad/
        # uid ONLY — never identificacion/nombre_completo/codigo, which
        # have their own, stricter rules. A matched evaluación's inspector
        # may fill in what the API/roster left blank, but ONLY when that
        # inspector's own `identificacion` is confirmed to be the SAME
        # person as `profesional` (digits-only cédula match) — never on a
        # different or blank cédula, and never when there is no match at
        # all (both sides of the comparison are then blank, which must NOT
        # read as "same person").
        same_cedula_match = bool(cedula_join_key) and cedula_join_key == cedula_key(insp_match.get("identificacion"))
        np_value = rango_api or str(roster_cedula_match.get("np") or "").strip()
        if not np_value and same_cedula_match:
            np_value = str(insp_match.get("np") or "").strip()
        nombre_completo_value = nombre_api or str(roster_cedula_match.get("nombre_completo") or "").strip()
        identificacion_value = cedula_api
        uid_value = str(roster_cedula_match.get("uid") or "")
        if not uid_value and same_cedula_match:
            uid_value = str(insp_match.get("uid") or "")
        entidad_value = str(roster_cedula_match.get("entidad") or "")
        if not entidad_value and same_cedula_match:
            entidad_value = str(insp_match.get("entidad") or "")
        inspector_fuente = "api"
        # Same "API wins, roster only backfills what's blank" pattern as
        # np_value above.
        tarjeta_value = tarjeta_api or str(roster_cedula_match.get("tarjeta_profesional") or "").strip()
        telefono_value = str(roster_cedula_match.get("num_telefono") or "").strip()
        correo_value = str(roster_cedula_match.get("correo_contacto") or "").strip()
    elif match is not None:
        # Rule B, step 1: `profesional` names nobody, but a Firestore
        # evaluación matched by code — its own inspector fields are
        # AUTHORITATIVE even when empty, unchanged since before Rule A
        # existed.
        np_value = str(insp_match.get("np") or "").strip()
        uid_value = str(insp_match.get("uid") or "")
        nombre_completo_value = str(insp_match.get("nombre_completo") or "")
        identificacion_value = str(insp_match.get("identificacion") or "")
        entidad_value = str(insp_match.get("entidad") or "")
        inspector_fuente = "evaluacion"
        # W3 (plan cozy-wobbling-dragonfly): contact fields (never present
        # on a Firestore evaluación doc) stay blank on the matched branch —
        # same "never mix sources field-by-field" invariant as np/identity
        # above: neither the API's `profesional` (already established
        # empty by the `elif` above) nor the roster is ever consulted once
        # a Firestore evaluación has matched.
        tarjeta_value = ""
        telefono_value = ""
        correo_value = ""
    elif rango_api:
        # Rule B, step 2: no match, `profesional` carries a bare `rango`
        # (no person to name it). F4: never mix sources field-by-field —
        # if the brigade-code roster already names a person, its OWN np is
        # authoritative and the API's rango is ignored (same invariant as
        # the matched branch above); only when the roster has NO identity
        # at all (no entry, or an np-only entry) does the bare rango become
        # np, and even then there is nobody to attribute it to, so
        # inspector_fuente stays "". F2: `uid` is NOT part of "names a
        # person" — `inspector_profiles` always sets it to the Firestore doc
        # id, so it is present on every roster entry regardless of whether
        # that entry names anyone; counting it here would make an np-only
        # roster doc wrongly read as "roster".
        uid_value = str(roster_match.get("uid") or "")
        nombre_completo_value = str(roster_match.get("nombre_completo") or "")
        identificacion_value = str(roster_match.get("identificacion") or "")
        entidad_value = str(roster_match.get("entidad") or "")
        # W3: contact fields follow the SAME brigade-code roster this
        # branch already reads uid/identity from — regardless of
        # `roster_names_person` below, exactly like uid_value above (they
        # are blank anyway when the roster entry names nobody).
        tarjeta_value = str(roster_match.get("tarjeta_profesional") or "").strip()
        telefono_value = str(roster_match.get("num_telefono") or "").strip()
        correo_value = str(roster_match.get("correo_contacto") or "").strip()
        roster_names_person = any((nombre_completo_value, identificacion_value, entidad_value))
        if roster_names_person:
            np_value = str(roster_match.get("np") or "")
            inspector_fuente = "roster"
        else:
            np_value = rango_api
            inspector_fuente = ""
    else:
        # Rule B, step 3 (also reached when `profesional` has no usable
        # data at all): the plain roster-by-brigade-code fallback,
        # unchanged since before `profesional` existed. F3 fix: coerce
        # None/other falsy-but-present roster values to "", same as the
        # matched branch above — `dict.get(k, "")` only applies its
        # default when the key is ABSENT, so a roster doc with an explicit
        # `None` field (e.g. a manually repaired Firestore doc) used to
        # leak a raw `None` into the response instead of "".
        np_value = str(roster_match.get("np") or "")
        uid_value = str(roster_match.get("uid") or "")
        nombre_completo_value = str(roster_match.get("nombre_completo") or "")
        identificacion_value = str(roster_match.get("identificacion") or "")
        entidad_value = str(roster_match.get("entidad") or "")
        # W3: same brigade-code roster, same "coerce None/absent to ''"
        # rule as the other roster-sourced fields just above.
        tarjeta_value = str(roster_match.get("tarjeta_profesional") or "").strip()
        telefono_value = str(roster_match.get("num_telefono") or "").strip()
        correo_value = str(roster_match.get("correo_contacto") or "").strip()
        # "roster" only when the roster actually names a PERSON (any one of
        # nombre_completo/identificacion/entidad non-blank) — a roster entry
        # carrying only `np` (a Fase number) has nobody to attribute, so it
        # is treated the same as no roster entry at all: "". F2: `uid` is
        # deliberately EXCLUDED — `inspector_profiles` always sets it to the
        # Firestore doc id, present on every roster entry regardless of
        # identity, so counting it would make an np-only roster doc wrongly
        # read as "roster" for every real caller.
        inspector_fuente = (
            "roster"
            if any((nombre_completo_value, identificacion_value, entidad_value))
            else ""
        )

    clase = COLOR_TO_CLASE.get(str(row.get("color") or "").strip().lower(), "")
    desc_match = (match or {}).get("descripcion") or {}
    acc_match = (match or {}).get("acciones_posteriores") or {}

    # W2 (plan cozy-wobbling-dragonfly): `fecha`/`fecha_fuente` resolution,
    # additive to the existing identity/np priority chain above (same
    # `match`/`origen_value` this function already computed, never
    # re-derived). Priority: (1) a matched Firestore evaluación is
    # AUTHORITATIVE for `fecha` — its own value, byte-identical even when
    # `None` — the API's `fechaCreacion` is never consulted on this path,
    # same invariant as the inspector identity above; (2) no match and
    # `origen_value == "sistema"` -> the shared es-CO parser (D8, UTC,
    # `fechaCreacion` is confirmed rendered in UTC — see the plan's Zona
    # horaria section) -> `"api"` on success, or `None`/`"sin_fecha"` when
    # the string is missing or fails to parse (a drift metric, not a
    # silent failure); (3) everything else (no match, any other origen,
    # including blank/"firebase") -> `None`/`"no_aplica"`.
    if match is not None:
        fecha_value = match.get("fecha")
        fecha_fuente = "evaluacion"
    elif origen_value == "sistema":
        fecha_dt = fechas_es_co.parse_fecha_es_co(row.get("fechaCreacion"), tz=timezone.utc)
        if fecha_dt is not None:
            fecha_value = fechas_es_co.to_iso(fecha_dt)
            fecha_fuente = "api"
        else:
            fecha_value = None
            fecha_fuente = "sin_fecha"
    else:
        fecha_value = None
        fecha_fuente = "no_aplica"

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
        "inspector_fuente": inspector_fuente,
        "inspector": {
            "uid": uid_value,
            "codigo": codigo_value,
            "nombre_completo": nombre_completo_value,
            "identificacion": identificacion_value,
            "entidad": entidad_value,
            "np": np_value,
            "tarjeta_profesional": tarjeta_value,
            "num_telefono": telefono_value,
            "correo_contacto": correo_value,
        },
        "barrio_reportado": _clean(row.get("barrio")),
        "comuna_reportada": _clean(row.get("comuna")),
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
        "fotos": (
            list((match or {}).get("fotos") or [])
            if match is not None
            else _fotos_from_fotografias(row.get("fotografias"))
        ),
        "fecha": fecha_value,
        "fecha_fuente": fecha_fuente,
        "fase": _fase(row.get("fase")),
    }


def build_evaluaciones(
    rows: list,
    *,
    roster_by_codigo: dict[str, dict[str, str]],
    evaluaciones_firestore: list[dict],
    roster_by_cedula: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    by_codigo = {
        str(e.get("codigo_edificacion") or "").strip(): e
        for e in evaluaciones_firestore
        if isinstance(e, dict) and e.get("codigo_edificacion")
    }
    out: list[dict[str, Any]] = []
    for row in rows:
        normalized = normalize_sticker(
            row, roster_by_codigo=roster_by_codigo, evaluacion_by_codigo=by_codigo,
            roster_by_cedula=roster_by_cedula,
        )
        if normalized is not None:
            out.append(normalized)
    out.sort(key=lambda e: str(e.get("fecha") or ""), reverse=True)
    return out
