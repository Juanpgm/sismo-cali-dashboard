"""POST /usuarios — admin CRUD for the dashboard's superset user list
("Usuarios" tab); design.md ADR-3/ADR-4; backend-platform spec "Route
Parity Across Consolidated Endpoints" (`/usuarios` row), "usuarios endpoint
enforces its extra provider/domain gate".

**Scope for THIS batch (slice 8b, tasks 8.5/8.6) is deliberately narrower
than the full `api/usuarios.js` action set.** tasks.md 8.6's own text names
exactly FOUR actions to port: `create`, `list`, `setPassword`, `delete`.
`setEnabled` and `setRole` — both present in `api/usuarios.js` and both
documented as required behavior in `openspec/specs/user-management/spec.md`
("Disable / enable user") — are NOT wired up here; no task in tasks.md's
Phase 8 assigns them. **Flagged for verify / a future batch**: repointing
`web/js/usuarios.js`'s "Habilitar/deshabilitar" or "Cambiar rol" UI actions
(task 8.8) against this router would 400 on an unrecognized action until a
follow-up batch adds them — this is a real functional gap, not an oversight
being silently swallowed.

Two Firebase surfaces, both memoized/named per ADR-4:
- `credentials.sismo().app` — Auth account list/create/update/delete
  (`firebase_admin.auth`, imported as `fb_auth` below and monkeypatched
  wholesale in tests — same "patch the imported module reference"
  convention `routers/stickers.py`/`routers/source_status.py` established).
- `credentials.sismo().firestore` — only for `delete`'s best-effort
  `inspectores/{uid}` profile cleanup (not the create path — see
  `create_usuario`'s docstring: this router mints plain password admins,
  no Firestore profile write, unlike Stickers' `createInspector`).

## The extra provider/domain gate — design open question 2, read carefully

`api/usuarios.js`'s auth preamble (lines 200-214) has a comment claiming
"provider 'password', caller NOT @sismocali.gov.co" — but its ACTUAL
executable check is a single `roleFromClaims(claims) !== 'admin'` test,
identical in shape to every other admin-gated router (`stickers.js`,
`sticker-asignaciones.js`). Reading `stickers.js:231-234`'s own comment
confirms why: "`roleFromClaims` already resolves inspectors (@sismocali,
password-provider) to 'inspector' — not 'admin' — so this one check
REPLACES the old provider + domain guard." `usuarios.js`'s comment at
lines 200-201 is the stale, un-updated leftover from BEFORE that
refactor — it was never deleted when the check collapsed into
`roleFromClaims`.

So does `usuarios.py` need a SEPARATE gate at all, or would
`Depends(require_role("admin"))` alone already be byte-for-byte parity
with what `usuarios.js` actually executes today? **It needs the separate
gate — this is a deliberate, security-motivated CLOSING of a real latent
gap in the legacy JS, not a literal copy of its executable behavior**,
because:

1. `openspec/specs/backend-platform/spec.md`'s own formal requirement row
   (`/usuarios` — "Bearer + `admin`, PLUS the acting admin's provider MUST
   be `password` and email MUST NOT be under `@sismocali.gov.co`") and its
   dedicated scenario ("usuarios endpoint enforces its extra provider/domain
   gate") explicitly require it as a SEPARATE, additive check.
2. `openspec/specs/user-management/spec.md` (the original, already-built
   spec this endpoint's UI targets) states the SAME requirement verbatim:
   "valid Firebase ID token + `sign_in_provider === 'password'`" (line 15).
3. The archived `usuarios-tab` design.md's ADR-1 (`openspec/changes/
   archive/2026-08-24-usuarios-tab/design.md:48-51`) locked this in as the
   INTENDED auth preamble at design time — "reuse the stickers guard
   exactly: valid ID token + `sign_in_provider === 'password'` + caller
   email NOT `@sismocali.gov.co`" — and its own security rationale still
   holds: `role_from`'s claim-override precedence (`claim_role` beats
   domain/provider) means an admin using the `setRole` action could, in
   principle, grant the `admin` custom claim to a `@sismocali.gov.co`
   inspector account or a `google.com`-provider viewer account (nothing in
   `setRole`'s own validation prevents it). Such an account would then
   pass a bare `require_role("admin")` check despite never having a
   legitimate password-admin identity. This router closes that gap
   explicitly, at the route, rather than porting the latent bypass
   forward. Task 8.5's own text ("MUST fail" / "byte-for-byte per design
   open question 2") and design.md's open question 2 resolution instruct
   exactly this: read the JS's STATED intent carefully and encode it, not
   just its (stale-commented) executable shortcut.

`_require_usuarios_admin` below layers this check ON TOP OF
`Depends(require_role("admin"))` (`auth/deps.py`'s own docstring already
anticipates this: "`/usuarios` — the latter layers its own extra
provider/domain gate at the route, per ADR-3's table"). Rejection reuses
the SAME 403 message `api/usuarios.js`'s single check emits
("Solo administradores pueden gestionar usuarios.") — the legacy source
has no distinct message for a separate provider/domain failure to port
byte-for-byte, since its comment and its code were never actually two
branches.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from firebase_admin import auth as fb_auth
from pydantic import BaseModel

from app.auth.deps import require_role
from app.auth.roles import role_from
from app.credentials import clients as credentials

# `sismo` is already unconditionally in credentials.WEB_STARTUP_CLIENTS, but
# this router still declares it per ADR-4's declaration mechanism (same
# precedent every other admin router sets).
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

INSPECTORES_COLLECTION = "inspectores"
INSPECTOR_DOMAIN = "@sismocali.gov.co"

log = logging.getLogger(__name__)

router = APIRouter()


# ---- Pure validators / classifiers (exported for the self-check, ports
# api/usuarios.js's isValidPassword/hasProvider/classify/isEnabledAdmin/
# checkDeleteGuards verbatim) -------------------------------------------


def is_valid_password(v: Any) -> bool:
    return isinstance(v, str) and len(v) >= 6


def _has_provider(u: Any, provider_id: str) -> bool:
    return any(getattr(p, "provider_id", None) == provider_id for p in (getattr(u, "provider_data", None) or []))


def classify(u: Any) -> str:
    """Effective role for a `list_users` `UserRecord`-shaped object,
    delegating to `role_from` (the single source of truth) so this
    server's notion of role cannot drift from `auth/deps.py`'s
    `require_role`. Verbatim port of `api/usuarios.js`'s `classify`."""
    custom_claims = getattr(u, "custom_claims", None) or {}
    provider = "password" if _has_provider(u, "password") else ("google.com" if _has_provider(u, "google.com") else "")
    return role_from(
        email=getattr(u, "email", None),
        claim_role=custom_claims.get("role"),
        provider=provider,
    )


def is_enabled_admin(u: Any) -> bool:
    return not getattr(u, "disabled", False) and classify(u) == "admin"


def check_delete_guards(users: list[Any], target_uid: str, caller_uid: str) -> dict[str, Any] | None:
    """Pure guard: self-management + last-admin (delete only). Factored
    out so it's testable without mocking the Admin SDK. Returns `None`
    when allowed, or `{"status", "message"}` describing the rejection.
    Verbatim port of `api/usuarios.js`'s `checkDeleteGuards`."""
    if target_uid == caller_uid:
        return {"status": 403, "message": "No podés eliminar tu propia cuenta."}
    target = next((u for u in users if getattr(u, "uid", None) == target_uid), None)
    if target is None:
        return {"status": 400, "message": "Usuario no encontrado."}
    if is_enabled_admin(target):
        enabled_admins = sum(1 for u in users if is_enabled_admin(u))
        if enabled_admins <= 1:
            return {"status": 403, "message": "No podés eliminar al último administrador."}
    return None


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


# ---- Auth-backed actions ---------------------------------------------------


def list_usuarios(app: Any) -> list[dict[str, Any]]:
    """Verbatim port of `api/usuarios.js`'s `listUsuarios`. `lastSignInTime`/
    `creationTime` are passed through as the Python Admin SDK's native
    `UserMetadata` epoch-millisecond values (`user_metadata.last_sign_in_
    timestamp`/`.creation_timestamp`) rather than JS's ISO-string
    equivalents — the direct Python-native form, same precedent
    `stickers.py`'s `datetime` port set for the Timestamp field."""
    page = fb_auth.list_users(max_results=1000, app=app)
    result: list[dict[str, Any]] = []
    for u in page.users:
        meta = getattr(u, "user_metadata", None)
        result.append(
            {
                "uid": u.uid,
                "email": u.email or "",
                "role": classify(u),
                "disabled": bool(u.disabled),
                "lastSignInTime": getattr(meta, "last_sign_in_timestamp", None) if meta else None,
                "creationTime": getattr(meta, "creation_timestamp", None) if meta else None,
            }
        )
    return result


def create_usuario(app: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Admin-only creation: viewers auto-provision at first Google
    sign-in, and inspectors need the brigade-code transaction that already
    lives in Stickers. So this mints a plain password admin — no
    Firestore profile write, hence no rollback branch. Verbatim port of
    `api/usuarios.js`'s `createUsuario`."""
    email = str(body.get("email") or "").strip().lower()
    password = body.get("password")
    if not email or "@" not in email:
        raise bad_request("Email inválido.")
    if email.endswith(INSPECTOR_DOMAIN):
        raise bad_request("Los inspectores se crean desde la pestaña Stickers, no aquí.")
    if not is_valid_password(password):
        raise bad_request("La contraseña debe tener al menos 6 caracteres.")
    user = fb_auth.create_user(email=email, password=password, app=app)
    return {"uid": user.uid, "email": email}


def set_password(app: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Set a new password directly (no reset email) — inspectors have
    synthetic `@sismocali.gov.co` emails that never receive Firebase's
    password-reset mail. Verbatim port of `api/usuarios.js`'s
    `setPassword`."""
    uid = str(body.get("uid") or "").strip()
    password = str(body.get("password") or "")
    if not uid:
        raise bad_request("Falta el uid.")
    if not is_valid_password(password):
        raise bad_request("La contraseña debe tener al menos 6 caracteres.")
    fb_auth.update_user(uid, password=password, app=app)
    return {"uid": uid}


def delete_usuario(db: Any, app: Any, body: dict[str, Any], caller_uid: str) -> dict[str, Any]:
    """Removes the Auth user and its `inspectores/{uid}` profile (if any).
    `evaluaciones` are left INTACT — historical inspection records keyed
    by inspector uid, not account data. Verbatim port of
    `api/usuarios.js`'s `deleteUsuario`."""
    uid = str(body.get("uid") or "").strip()
    if not uid:
        raise bad_request("Falta el uid.")

    page = fb_auth.list_users(max_results=1000, app=app)
    users = list(page.users)
    rejection = check_delete_guards(users, uid, caller_uid)
    if rejection:
        raise HTTPException(status_code=rejection["status"], detail=rejection["message"])

    fb_auth.delete_user(uid, app=app)
    # The Auth user is already gone; if the profile delete fails, surface
    # the orphaned inspectores/{uid} doc in the logs instead of swallowing
    # it — same fail-soft `.catch(...)` contract as api/usuarios.js.
    try:
        db.collection(INSPECTORES_COLLECTION).document(uid).delete()
    except Exception as exc:  # pragma: no cover - best-effort cleanup, logged not raised
        log.error("usuarios.delete: perfil inspectores/%s huérfano — falló el borrado: %s", uid, exc)
    return {"uid": uid}


# ---- Extra provider/domain gate (see module docstring) ---------------------


async def _require_usuarios_admin(claims: dict[str, Any] = Depends(require_role("admin"))) -> dict[str, Any]:
    firebase = claims.get("firebase") or {}
    provider = firebase.get("sign_in_provider")
    email = str(claims.get("email") or "").lower()
    if provider != "password" or email.endswith(INSPECTOR_DOMAIN):
        raise HTTPException(status_code=403, detail="Solo administradores pueden gestionar usuarios.")
    return claims


class UsuariosRequest(BaseModel):
    action: str
    email: str | None = None
    password: str | None = None
    uid: str | None = None


@router.post("/usuarios")
def usuarios(
    body: UsuariosRequest,
    claims: dict[str, Any] = Depends(_require_usuarios_admin),
) -> JSONResponse:
    sismo = credentials.sismo()
    db, app = sismo.firestore, sismo.app
    caller_uid = str(claims.get("sub") or "")
    payload = body.model_dump()

    try:
        if body.action == "list":
            return JSONResponse({"ok": True, "usuarios": list_usuarios(app)})
        if body.action == "create":
            result = create_usuario(app, payload)
            return JSONResponse({"ok": True, **result}, status_code=201)
        if body.action == "setPassword":
            result = set_password(app, payload)
            return JSONResponse({"ok": True, **result})
        if body.action == "delete":
            result = delete_usuario(db, app, payload, caller_uid)
            return JSONResponse({"ok": True, **result})
        raise bad_request(f"Acción desconocida: {body.action}")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - legacy fail-open surface
        # Surface Firebase's own messages (e.g. email-already-exists) to
        # the admin, same 502 fallback api/usuarios.js's catch-all used.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
