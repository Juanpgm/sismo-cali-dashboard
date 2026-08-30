"""POST /stickers — admin CRUD for field-form inspector accounts (dashboard
"Stickers" tab); design.md ADR-4; backend-platform spec "Admin-gated route
rejects non-admin" (`/stickers` row), "Route Parity Across Consolidated
Endpoints" (`/stickers` row).

Ports `api/stickers.js` verbatim: inspectors sign in to the field form with
`cedula@sismocali.gov.co` (Firebase Auth) and their profile lives in
Firestore `inspectores/{uid}`. Disabling flips Auth `disabled` AND the
doc's `activo` flag — the latter is the durable gate the Firestore rules
check, so an already-issued ID token (valid ~1h) can no longer create
`evaluaciones`.

Auth: `Depends(require_role("admin"))` — the legacy handler's own hand-rolled
"caller is admin, not an inspector" check (`api/stickers.js:231-245`) is
already exactly what `require_role("admin")` does (`role_from_claims`
resolves `@sismocali.gov.co` + password-provider callers to `'inspector'`,
never `'admin'`), so no extra gate is layered here, unlike `/usuarios`
(slice 8b, task 8.6's own extra provider/domain gate).

Two Firebase surfaces, both memoized/named per ADR-4:
- `credentials.sismo().firestore` — `inspectores`/`evaluaciones` Firestore
  access.
- `firebase_admin.auth` (imported as `fb_auth` below, bound to
  `credentials.sismo().app`) — Auth account list/create/update/delete.
  Imported at module level (not wrapped in a client accessor) so tests can
  monkeypatch `stickers.fb_auth` wholesale, the same "patch the imported
  module reference" convention `routers/source_status.py` established for
  `atencionsismo.probe_api`.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from firebase_admin import auth as fb_auth
from pydantic import BaseModel

from app.auth.deps import require_role
from app.credentials import clients as credentials

# `sismo` is already unconditionally in credentials.WEB_STARTUP_CLIENTS, but
# this router still declares it per ADR-4's declaration mechanism (same
# precedent inspector_asignaciones.py/sign.py set).
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

INSPECTORES_COLLECTION = "inspectores"
EVALUACIONES_COLLECTION = "evaluaciones"
INSPECTOR_DOMAIN = "@sismocali.gov.co"

# Brigade codes are the 3-digit segment inside every record id
# (76001-1-`004`0001), so the space is 001..999. Verbatim from
# api/stickers.js:34.
CODIGO_MAX = 999

_CEDULA_RE = re.compile(r"^\d{5,12}$")
_CODIGO_RE = re.compile(r"^\d{3}$")

EVALUACIONES_CACHE_TTL_SECONDS = 5 * 60


class EvaluacionesCache:
    """Process-lifetime 5-min TTL cache for the flattened evaluaciones list —
    same app.state / test-isolated pattern as sticker_status.py's
    StickerStatusCache. The dashboard's Evaluaciones tab reads this on every
    open AND on a 5-min poll while visible; caching collapses that to at most
    one full `evaluaciones` collection read per TTL window per process."""

    def __init__(self) -> None:
        self._at: float | None = None
        self._payload: list[dict[str, Any]] | None = None

    def get_or_fetch(self, fetch: Any) -> list[dict[str, Any]]:
        now = time.monotonic()
        stale = self._payload is None or self._at is None or (now - self._at) > EVALUACIONES_CACHE_TTL_SECONDS
        if stale:
            self._payload = fetch()
            self._at = now
        assert self._payload is not None
        return self._payload


router = APIRouter()


# ---- Pure validators (exported for the self-check, ports api/stickers.js's
# isValidCedula/isValidCodigo/isValidPassword/cedulaToEmail/emailToCedula) --


def is_valid_cedula(v: Any) -> bool:
    return bool(_CEDULA_RE.match(str(v if v is not None else "").strip()))


def is_valid_codigo(v: Any) -> bool:
    return bool(_CODIGO_RE.match(str(v if v is not None else "").strip()))


def is_valid_password(v: Any) -> bool:
    return isinstance(v, str) and len(v) >= 6


def cedula_to_email(cedula: Any) -> str:
    return f"{str(cedula).strip().lower()}{INSPECTOR_DOMAIN}"


def email_to_cedula(email: Any) -> str:
    return str(email or "").split("@")[0]


def next_available_codigo(used_codes: Any) -> str | None:
    """Lowest unused brigade code, counting up from 001 and stepping over
    the ones already taken — a plain count that fills gaps, NOT max+1.
    Verbatim port of `api/stickers.js`'s `nextAvailableCodigo`."""
    used = {str(c).strip().zfill(3) for c in used_codes}
    for n in range(1, CODIGO_MAX + 1):
        codigo = str(n).zfill(3)
        if codigo not in used:
            return codigo
    return None


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


# ---- Firestore/Auth-backed actions -----------------------------------------


def _registros_count(db: Any, uid: str) -> int | None:
    """Real submitted `evaluaciones` per inspector — NOT `consecutivo` (that
    counter increments every time a code is generated, form started, not
    saved, so it overcounts). `None` on error so the UI can tell "unknown"
    from a real zero — same fail-soft contract as `api/stickers.js`'s
    `.catch(() => null)`."""
    try:
        docs = db.collection(EVALUACIONES_COLLECTION).where("inspector.uid", "==", uid).get()
        return len(list(docs))
    except Exception:
        return None


def list_inspectores(db: Any, app: Any) -> list[dict[str, Any]]:
    """Every `@sismocali.gov.co` Auth user joined with its
    `inspectores/{uid}` doc. Single `list_users` page (max 1000) — same
    ponytail note `api/stickers.js:65` carries: paginate with a page token
    if the roster ever exceeds that."""
    page = fb_auth.list_users(max_results=1000, app=app)
    inspectores = [u for u in page.users if (u.email or "").lower().endswith(INSPECTOR_DOMAIN)]

    by_uid: dict[str, dict[str, Any] | None] = {}
    if inspectores:
        refs = [db.collection(INSPECTORES_COLLECTION).document(u.uid) for u in inspectores]
        snaps = db.get_all(refs)
        by_uid = {s.id: (s.to_dict() if s.exists else None) for s in snaps}

    result: list[dict[str, Any]] = []
    for u in inspectores:
        d = by_uid.get(u.uid) or {}
        result.append(
            {
                "uid": u.uid,
                "email": u.email,
                "cedula": email_to_cedula(u.email),
                "nombre_completo": d.get("nombre_completo") or "",
                "codigo": d.get("codigo") or "",
                "entidad": d.get("entidad") or "",
                "registros": _registros_count(db, u.uid),
                "registrado": by_uid.get(u.uid) is not None,
                "disabled": bool(u.disabled),
                # Missing `activo` counts as active (legacy inspectors predate the flag).
                "activo": d.get("activo") is not False,
            }
        )
    result.sort(key=lambda r: r["cedula"])
    return result


def _num_or_none(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n if n else None


def list_evaluaciones(db: Any) -> list[dict[str, Any]]:
    """Every ATC-20 evaluation, flattened for the dashboard's Stickers tab.
    Verbatim port of `api/stickers.js`'s `listEvaluaciones` — read here
    (Admin SDK, bypasses Firestore rules) rather than straight from the
    browser, mirroring the legacy handler's own comment: `evaluaciones` is
    open only to inspectores, and a dashboard admin is deliberately not
    one.

    `e.timestamp` is a Python `datetime` once read via `to_dict()` (the
    google-cloud-firestore client auto-converts Timestamp fields — there is
    no JS-style `.toDate()` method to duck-type against), so the
    `isoformat()` branch below is the Python-native equivalent of
    `api/stickers.js`'s `typeof e.timestamp.toDate === 'function'` check.
    """
    docs = db.collection(EVALUACIONES_COLLECTION).get()
    result: list[dict[str, Any]] = []
    for doc in docs:
        e = doc.to_dict() or {}
        coords = e.get("coords") or {}
        lat = _num_or_none(coords.get("lat"))
        lng = _num_or_none(coords.get("lng"))
        insp = e.get("inspector") or {}
        desc = e.get("descripcion") or {}
        acc = e.get("acciones_posteriores") or {}
        ts_value = e.get("timestamp")
        ts = ts_value.isoformat() if isinstance(ts_value, datetime) else None
        result.append(
            {
                "id": doc.id,
                "codigo_edificacion": e.get("codigo_edificacion") or doc.id,
                "consecutivo": e.get("consecutivo"),
                "municipio": e.get("municipio") or "",
                "area": e.get("area"),
                "area_nombre": e.get("area_nombre") or "",
                "clasificacion": e.get("clasificacion") or "",
                "alcance": e.get("alcance") or "",
                "coords": (
                    {"lat": lat, "lng": lng, "accuracy": _num_or_none(coords.get("accuracy"))}
                    if lat is not None and lng is not None
                    else None
                ),
                "inspector": {
                    "uid": insp.get("uid") or "",
                    "codigo": insp.get("codigo") or "",
                    "nombre_completo": insp.get("nombre_completo") or "",
                    "identificacion": insp.get("identificacion") or "",
                    "entidad": insp.get("entidad") or "",
                },
                "descripcion": {"nombre": desc.get("nombre") or "", "direccion": desc.get("direccion") or ""},
                "restricciones": e.get("restricciones") or "",
                "acciones_posteriores": {
                    "barricadas": bool(acc.get("barricadas")),
                    "evaluacion_detallada": bool(acc.get("evaluacion_detallada")),
                },
                "comentarios": e.get("comentarios") or "",
                "fotos": [f for f in (e.get("fotos") or []) if f],
                "fecha": ts or e.get("fecha_hora_dispositivo"),
            }
        )
    result.sort(key=lambda r: str(r.get("fecha") or ""), reverse=True)
    return result


def _allocate_codigo(db: Any, uid: str, perfil: dict[str, Any], codigo_pedido: str) -> str:
    """Allocate inside a transaction: the roster read joins the
    transaction's read set, so two admins creating an inspector at the same
    instant can never be handed the same code — the loser retries against
    fresh data. Verbatim port of `api/stickers.js`'s `createInspector`
    transaction body."""

    def _mutate(transaction: Any) -> str:
        snap = transaction.get(db.collection(INSPECTORES_COLLECTION))
        usados: set[str] = set()
        for d in snap:
            data = d.to_dict() or {}
            c = data.get("codigo")
            if c:
                usados.add(str(c).strip().zfill(3))
        if codigo_pedido and codigo_pedido in usados:
            raise bad_request(f"El código {codigo_pedido} ya está asignado a otro inspector.")
        asignado = codigo_pedido or next_available_codigo(usados)
        if not asignado:
            raise bad_request("No quedan códigos de brigada libres (001–999).")
        transaction.set(db.collection(INSPECTORES_COLLECTION).document(uid), {**perfil, "codigo": asignado})
        return asignado

    transaction = db.transaction()
    if getattr(transaction, "_is_test_double", False):
        return _mutate(transaction)
    from google.cloud import firestore as _fs  # deferred import, credentials/clients.py's own convention

    return _fs.transactional(_mutate)(transaction)


def create_inspector(db: Any, app: Any, body: dict[str, Any]) -> dict[str, Any]:
    """The brigade code is assigned by the server, not typed by the admin.
    An explicit `codigo` is still honoured (older clients/manual repair)
    but must be free. Verbatim port of `api/stickers.js`'s
    `createInspector`."""
    cedula = str(body.get("cedula") or "").strip()
    codigo_pedido = str(body.get("codigo") or "").strip()
    password = body.get("password")
    if not is_valid_cedula(cedula):
        raise bad_request("La cédula debe tener solo dígitos (5 a 12).")
    if codigo_pedido and not is_valid_codigo(codigo_pedido):
        raise bad_request('El código debe ser de 3 dígitos, ej "004".')
    if not is_valid_password(password):
        raise bad_request("La contraseña debe tener al menos 6 caracteres.")

    email = cedula_to_email(cedula)
    user = fb_auth.create_user(email=email, password=password, app=app)
    perfil = {
        "nombre_completo": str(body.get("nombre_completo") or "").strip(),
        "identificacion": str(body.get("identificacion") or cedula).strip(),
        "profesion": str(body.get("profesion") or "").strip(),
        "num_telefono": str(body.get("num_telefono") or "").strip(),
        "entidad": str(body.get("entidad") or "").strip(),
        "consecutivo": 0,
        "activo": True,
    }

    try:
        codigo = _allocate_codigo(db, user.uid, perfil, codigo_pedido)
        return {"uid": user.uid, "email": email, "cedula": cedula, "codigo": codigo}
    except Exception:
        # Never leave an orphan Auth account: without a profile doc the
        # inspector can't work, but the cédula would stay taken and block a
        # retry.
        try:
            fb_auth.delete_user(user.uid, app=app)
        except Exception:
            pass
        raise


def set_enabled(db: Any, app: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Verbatim port of `api/stickers.js`'s `setEnabled`."""
    uid = str(body.get("uid") or "").strip()
    enabled = body.get("enabled") is True or body.get("enabled") == "true"
    if not uid:
        raise bad_request("Falta el uid del inspector.")
    fb_auth.update_user(uid, disabled=not enabled, app=app)
    # The Firestore flag is the gate the security rules read; keep it in sync.
    db.collection(INSPECTORES_COLLECTION).document(uid).set({"activo": enabled}, merge=True)
    return {"uid": uid, "activo": enabled, "disabled": not enabled}


class StickersRequest(BaseModel):
    action: str
    cedula: str | None = None
    codigo: str | None = None
    password: str | None = None
    nombre_completo: str | None = None
    identificacion: str | None = None
    profesion: str | None = None
    num_telefono: str | None = None
    entidad: str | None = None
    uid: str | None = None
    enabled: Any = None


@router.post("/stickers")
def stickers(
    body: StickersRequest,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    sismo = credentials.sismo()
    db, app = sismo.firestore, sismo.app
    payload = body.model_dump()

    try:
        if body.action == "list":
            return JSONResponse({"ok": True, "inspectores": list_inspectores(db, app)})
        if body.action == "evaluaciones":
            return JSONResponse({"ok": True, "evaluaciones": list_evaluaciones(db)})
        if body.action == "create":
            result = create_inspector(db, app, payload)
            return JSONResponse({"ok": True, **result}, status_code=201)
        if body.action == "setEnabled":
            result = set_enabled(db, app, payload)
            return JSONResponse({"ok": True, **result})
        raise bad_request(f"Acción desconocida: {body.action}")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - legacy fail-open surface
        # Surface Firebase's own messages (e.g. email-already-exists) to the
        # admin, same 502 fallback api/stickers.js's catch-all used.
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/evaluaciones")
def get_evaluaciones(
    request: Request,
    claims: dict[str, Any] = Depends(require_role("admin")),
) -> JSONResponse:
    """Cached read of the flattened ATC-20 evaluaciones list (dashboard
    Evaluaciones tab). Replaces the legacy Vercel `POST /api/stickers
    {action:'evaluaciones'}` full-collection read on every tab open/poll with
    a 5-min TTL cache on app.state. Reuses `list_evaluaciones` verbatim."""
    cache: EvaluacionesCache = request.app.state.stickers_evaluaciones_cache
    db = credentials.sismo().firestore
    try:
        evaluaciones = cache.get_or_fetch(lambda: list_evaluaciones(db))
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - fail-open surface, mirrors
        # planeacion_asignaciones.py's own catch-all: an uncaught Firestore
        # exception here (e.g. a 429 quota error) was previously reaching
        # Starlette's default error handler as a bare 500 with NO CORS
        # headers attached, which the browser then reports as a misleading
        # "blocked by CORS policy" / "Failed to fetch" instead of the real
        # cause. A normal HTTPException always carries CORS headers.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "evaluaciones": evaluaciones})
