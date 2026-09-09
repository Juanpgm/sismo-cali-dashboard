"""Normalize atencionsismo `GET /api/informe/stickers` rows into the
dashboard's evaluaciones shape (design D1/D2 in
docs/superpowers/specs/2026-09-08-atencionsismo-reportes-ciudadanos-stickers-design.md).

Pure functions, no I/O. Fase I/II input (`inspector.np`) is derived in
priority order: matched Firestore evaluación -> roster NP by the 3-digit
inspector code embedded in our sticker code -> "" (the UI renders "sin
dato" for this source when np is empty; see web/js/evaluaciones.js faseDe).

Extensión (2026-09-08): the same no-match fallback also completes
`inspector.nombre_completo`, `identificacion`, `entidad` and `uid` from the
matching `roster_by_codigo` entry — not just `np`. This ONLY applies when
there is no matching Firestore evaluación; a matched evaluación's own
`inspector` sub-object stays fully authoritative (never mixed field-by-field
with the roster), for the same reason `np` already worked this way: brigade
codes are reused after an inspector is deleted, so mixing sources within a
matched record could attach a different inspector's identity to an old
evaluación.

`inspector_fuente` (2026-09-08): a top-level `"evaluacion" | "roster" | "api"
| ""` flag on every returned record, added because the roster fallback above
is a real misattribution risk — brigade codes are reused once an inspector
is deleted, so an old sticker can end up displaying the CURRENT holder's
real name and cédula (a specific, named, uninvolved person), not just a
wrong Fase. Callers use this flag to caveat/relabel a roster-sourced
identity instead of presenting it with the same confidence as a verified
match.

Contrato v3 (2026-09-08): the atencionsismo API now also returns `fase`,
`profesional` ({cedula, nombre, rango}) and `fotografias` per row. Priority
for identity/NP on the no-match path is now:

1. Matching Firestore evaluación by code (unchanged, fully authoritative;
   `inspector_fuente = "evaluacion"`. `profesional` is IGNORED on this
   path — never mixed with a matched evaluación's own inspector data,
   regardless of `origen`).
2. No match, `row.origen` (stripped, lower-cased) is `"sistema"`, and
   `profesional` names a person (non-blank `cedula` or `nombre`): identity
   comes from `profesional` (`identificacion = cedula`, verbatim/stripped,
   NOT the join key below; `nombre_completo = nombre`; `np = rango`,
   verbatim, not parsed). `roster_by_cedula` is joined by `cedula_key`
   (digits only, both sides — F7), so a formatted cédula like
   "1.234.567" still matches a roster doc stored as "1234567"; this join
   carries none of the brigade-code reuse risk since a cédula is unique
   per person. The roster only fills `uid`/`entidad`, and backfills
   `np`/`nombre_completo` when the API left them blank — the API always
   wins on conflict. `inspector_fuente = "api"`.
3. No match, `origen == "sistema"`, `profesional` has no person but a
   non-blank `rango`: an NP with nobody named has nobody to attribute, so
   sources are never mixed field-by-field (same invariant as step 1) — if
   the brigade-code roster already names a person, its OWN `np` is kept
   and the API's `rango` is ignored entirely (`inspector_fuente =
   "roster"`); only when the roster has NO identity at all (no entry, or
   an np-only entry) does the bare `rango` become `np`, and
   `inspector_fuente` stays `""` (still nobody to attribute).
4. No match, and EITHER `origen != "sistema"` OR `profesional` has no
   usable data at all: the roster-by-brigade-code fallback, unchanged
   since before `profesional` existed — `profesional` is ignored entirely
   for non-"sistema" origins (see below), not merged with the roster.

"Names a person" (steps 3 and 4 above) means a non-blank
`nombre_completo`, `identificacion` or `entidad` on the roster entry — NOT
`uid` (F2, 2026-09-08). `inspector_profiles` always sets `uid` to the
Firestore doc id, so every roster entry has one whether or not it names
anyone; counting `uid` here would make an np-only roster doc (e.g. one
carrying only `NP`) wrongly read as "roster" for every real caller.

Why step 4 requires `origen == "sistema"` for steps 2/3 at all:
`profesional`'s reliability was verified only for `origen == "sistema"`
rows. For `firebase`-origin (or blank/unknown-origin) rows that importer's
reliability for `profesional` is unverified, so for those rows OUR
Firestore roster stays the source of truth for identity and `profesional`
is never consulted, not even for a bare `rango`. This is independent of
the `fase` decision below — a separate field, confirmed reliable for ALL
origins, including `firebase`.

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


def _profesional_fields(row: dict) -> tuple[str, str, str]:
    """Cleaned (cedula, nombre, rango) from the API's `profesional` object,
    tolerant of it being missing, None, or not a dict at all — every one of
    those is treated as "no data", never a crash. Values pass through
    `_clean` (F1), not a bare `.strip()`: the API's own placeholder strings
    ("Sin código"/"Sin dirección"/"Sin identificar") are not real data and
    must collapse to "" here too, same as every other API field — a bare
    `.strip()` used to leave them as truthy non-empty text, which could
    wrongly read as `profesional` "naming a person" (step 2) or carrying a
    usable bare rango (step 3) when it actually carried nothing at all."""
    profesional = row.get("profesional")
    if not isinstance(profesional, dict):
        profesional = {}
    cedula = _clean(profesional.get("cedula"))
    nombre = _clean(profesional.get("nombre"))
    rango = _clean(profesional.get("rango"))
    return cedula, nombre, rango


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
    # D1 (updated): if a Firestore evaluación matched, its inspector fields
    # (np, nombre_completo, identificacion, entidad, uid) are AUTHORITATIVE
    # even when empty — the roster is consulted ONLY when there is no match.
    # Brigade codes are reused once an inspector is deleted, so falling back
    # to the roster here could hand an old evaluación the new inspector's
    # identity.
    roster_match = roster_by_codigo.get(codigo_inspector, {}) if codigo_inspector else {}
    cedula_api, nombre_api, rango_api = _profesional_fields(row)
    profesional_names_person = bool(cedula_api or nombre_api)
    # F3: `profesional` is trusted ONLY when the row's own `origen` is
    # "sistema" — the SAME atencionsismo import that tags every
    # Firebase-origin row `fase: 2` regardless of the real NP (module
    # docstring) also wrote `profesional` for those rows, and that
    # importer's reliability for `profesional` on non-"sistema" origins is
    # unverified. For any other origen (including "firebase", blank, or
    # unknown), `profesional` is ignored entirely and the plain
    # roster-by-brigade-code fallback applies, exactly as it did before
    # `profesional` existed.
    origen_value = str(row.get("origen") or "").strip().lower()
    trust_profesional = origen_value == "sistema"
    if match is not None:
        np_value = str(insp_match.get("np") or "").strip()
        uid_value = str(insp_match.get("uid") or "")
        nombre_completo_value = str(insp_match.get("nombre_completo") or "")
        identificacion_value = str(insp_match.get("identificacion") or "")
        entidad_value = str(insp_match.get("entidad") or "")
        inspector_fuente = "evaluacion"
    elif trust_profesional and profesional_names_person:
        # Contrato v3, step 2: `profesional` is the technician tied to THIS
        # evaluation — its `cedula` is a unique per-person key, unlike the
        # reused brigade `codigo`, so joining it against `roster_by_cedula`
        # (by the digits-only `cedula_key`, F7) carries no misattribution
        # risk. The API always wins on conflict; the roster only fills what
        # the API left blank. `identificacion` stores the API's cedula
        # VERBATIM (stripped) — the normalised key is a join key only.
        cedula_join_key = cedula_key(cedula_api)
        roster_cedula_match = roster_by_cedula.get(cedula_join_key, {}) if cedula_join_key else {}
        np_value = rango_api or str(roster_cedula_match.get("np") or "").strip()
        nombre_completo_value = nombre_api or str(roster_cedula_match.get("nombre_completo") or "").strip()
        identificacion_value = cedula_api
        uid_value = str(roster_cedula_match.get("uid") or "")
        entidad_value = str(roster_cedula_match.get("entidad") or "")
        inspector_fuente = "api"
    elif trust_profesional and rango_api:
        # Contrato v3, step 3: `profesional` carries a bare `rango` (no
        # person to name it). F4: never mix sources field-by-field — if the
        # brigade-code roster already names a person, its OWN np is
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
        roster_names_person = any((nombre_completo_value, identificacion_value, entidad_value))
        if roster_names_person:
            np_value = str(roster_match.get("np") or "")
            inspector_fuente = "roster"
        else:
            np_value = rango_api
            inspector_fuente = ""
    else:
        # Contrato v3, step 4 (also reached for any non-"sistema" origen,
        # per F3 above, and for a `profesional` with no usable data at
        # all): the plain roster-by-brigade-code fallback, unchanged since
        # before `profesional` existed. F3 fix: coerce None/other
        # falsy-but-present roster values to "", same as the matched branch
        # above — `dict.get(k, "")` only applies its default when the key
        # is ABSENT, so a roster doc with an explicit `None` field (e.g. a
        # manually repaired Firestore doc) used to leak a raw `None` into
        # the response instead of "".
        np_value = str(roster_match.get("np") or "")
        uid_value = str(roster_match.get("uid") or "")
        nombre_completo_value = str(roster_match.get("nombre_completo") or "")
        identificacion_value = str(roster_match.get("identificacion") or "")
        entidad_value = str(roster_match.get("entidad") or "")
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
            "codigo": codigo_inspector,
            "nombre_completo": nombre_completo_value,
            "identificacion": identificacion_value,
            "entidad": entidad_value,
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
        "fotos": (
            list((match or {}).get("fotos") or [])
            if match is not None
            else _fotos_from_fotografias(row.get("fotografias"))
        ),
        "fecha": (match or {}).get("fecha"),
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
