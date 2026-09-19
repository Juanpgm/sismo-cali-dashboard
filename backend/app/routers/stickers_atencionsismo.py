"""GET /stickers-atencionsismo — the Stickers tab's Evaluaciones list sourced
from atencionsismo `GET /api/informe/stickers` instead of Firestore
`evaluaciones` (design D1..D4). Same response shape as GET /evaluaciones
plus `fuente`/`origen`/`color_etiqueta` per record, so web/js/evaluaciones.js
renders both sources with one code path.

Cache: `stickers.EvaluacionesCache` parametrized with its own Blob
last-known-good pathname and an allowlist redaction that blanks the
affected person's name and the inspector NP (same sensitivity class as the
evaluaciones copy). A Firestore failure (roster or evaluaciones) fails the
whole fetch on purpose: NP is still the FALLBACK Fase source (contrato v3,
2026-09-08 — the API's own `fase` is primary, see
stickers_atencionsismo.py) for rows where `fase` is `None`, and it also
carries inspector identity (nombre_completo, uid, entidad) — a silently
empty Fase/identity for those rows is worse than a stale payload.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
import time
from datetime import date, datetime
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.deps import require_role
from app.auth.roles import role_from_claims
from app.credentials import clients as credentials
from app.routers import stickers
from app.services import atencionsismo, fechas_es_co
from app.services import inspectores_depuracion as depuracion_svc
from app.services import inspectores_referencia as referencia_svc
from app.services import survey_cali as survey_cali_svc
from app.services.stickers_atencionsismo import build_evaluaciones

router = APIRouter()
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

STICKERS_LKG_BLOB = "data/stickers_atencionsismo_last_good.json"

# design.md's Data Flow diagram: "cargar_referencia (30 min)" — how often
# `DepuracionCache` re-reads the (private) reference bundle from Blob,
# independent of the sticker payload's own 5-min TTL.
REFERENCIA_CACHE_TTL_SECONDS = 30 * 60

# Rollback switch (design's Migration/Rollout): unset or "0" -> the response
# omits `depuracion` entirely, byte-identical to the pre-Phase-3 payload.
SEGUIMIENTO_DEPURACION_ENV = "SEGUIMIENTO_DEPURACION"

# Dedicated executor for the two Firestore reads in `build_payload`'s
# concurrent gather (fail-fast fix, adversarial review 2026-09-10) — this
# must NOT be `asyncio.to_thread`'s default (loop) executor. `build_payload`
# runs its own fresh event loop per call via `asyncio.run(...)`, and
# `asyncio.run`'s own teardown calls `loop.shutdown_default_executor()`,
# which SYNCHRONOUSLY JOINS every thread ever submitted to the loop's
# default executor (up to a 300s cap) before `asyncio.run` returns — even
# for a task whose asyncio-level wrapper we already cancelled. Cancelling a
# `Task` wrapping a default-executor call only stops OUR await of it; it
# does not detach the underlying thread from the executor `asyncio.run`
# joins on exit, so using the default executor here would silently defeat
# the whole fail-fast fix on a slow Firestore call. A private executor
# sidesteps that entirely (`shutdown_default_executor` only touches the
# loop's OWN default executor, which this never becomes).
_FIRESTORE_READ_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    thread_name_prefix="stickers-atencionsismo-firestore"
)

# Hard ceiling on the whole informe/stickers pull: fetch_stickers can walk
# hundreds of pages (MAX_PAGES), each individually retried — without an
# outer deadline a sustained slow-but-alive upstream could hang this sync
# route (running in FastAPI's threadpool) far longer than any caller would
# wait. 45s comfortably covers a normal full walk while still failing fast
# into the cache's serve-stale/Blob chain instead of hanging the worker.
STICKERS_FETCH_DEADLINE_S = 45.0

# inspector_fuente ("evaluacion"|"roster"|"api"|"") is a bare enum, not PII,
# so it is allowlisted alongside the other atencionsismo-only fields —
# dropping it on the public Blob copy would silently undo the
# misattribution-risk caveat callers key off of
# (stickers_atencionsismo.normalize_sticker). fase (contrato v3, 2026-09-08)
# is likewise a bare 1|2|None value, not PII — it is the Stickers tab's own
# Fase I/II signal (API developer confirmation), not a raw storage artifact.
# fecha_fuente ("evaluacion"|"api"|"no_aplica"|"sin_fecha", W2) is the same
# kind of bare enum. barrio_reportado/comuna_reportada (W3, plan
# cozy-wobbling-dragonfly) are the CITIZEN-reported location of the
# building, not personal data about anyone — same class as the existing
# `municipio`/`area` fields already on this allowlist.
_BLOB_ALLOWED_FIELDS = stickers._BLOB_ALLOWED_FIELDS + (
    "fuente", "origen", "color_etiqueta", "inspector_fuente", "fase",
    "fecha_fuente", "barrio_reportado", "comuna_reportada",
)


def redact_for_blob(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allowlist projection for the PUBLIC Blob copy — `descripcion.nombre`
    is the affected person's name here (personaAfectada), so it is blanked
    along with `inspector.np`; everything else mirrors
    `stickers._redact_for_blob`. `tarjeta_profesional`/`num_telefono`/
    `correo_contacto` (W3) are blanked with an explicit `""` placeholder —
    the KEYS stay present (never popped) so a degraded frontend never has
    to guard `undefined` before a `.trim()`/render. Never mutates the
    input.

    H1 (adversarial review 2026-09-10): this is a PURE projection — no
    logging, no serialization. It used to run its own `json.dumps(out)`
    (without `default=str`) just to log a byte count, which raised
    TypeError whenever a `fecha` was a raw `datetime` (reachable via
    `stickers.list_evaluaciones`'s `fecha_hora_dispositivo` fallback). That
    TypeError propagated out of `EvaluacionesCache._persist_last_good`,
    mis-logged as a fetch failure by `get_or_fetch`'s `except Exception`,
    armed the failure backoff, and silently stopped the LKG Blob write
    forever. Size visibility now lives in `_persist_last_good`
    (`app/routers/stickers.py`), right after the hash gate decides an
    upload is actually about to happen, reusing the same
    `default=str`-safe serialization `blob_lkg.payload_hash` already does."""
    out: list[dict[str, Any]] = []
    for e in payload:
        insp = e.get("inspector") or {}
        desc = e.get("descripcion") or {}
        out.append({
            **{k: e.get(k) for k in _BLOB_ALLOWED_FIELDS},
            "descripcion": {"nombre": "", "direccion": desc.get("direccion") or ""},
            "inspector": {**{k: insp.get(k) or "" for k in stickers._BLOB_ALLOWED_INSPECTOR},
                          "nombre_completo": "", "identificacion": "", "np": "",
                          "tarjeta_profesional": "", "num_telefono": "", "correo_contacto": ""},
            "comentarios": "",
            "fotos": [],
        })
    return out


# ── Depuración wiring (design.md D1-D8, tasks.md Phase 3) ──────────────────
#
# `depuracion` is assembled by the ROUTE HANDLER, entirely OUTSIDE
# `redact_for_blob`/`payload` above (D3, PII isolation): nothing in this
# section ever touches `payload` (the cached, Blob-persisted evaluaciones
# list) or `redact_for_blob`'s allowlist. See
# `get_stickers_atencionsismo`'s own body for the actual assembly order.


def _depuracion_habilitada() -> bool:
    """Unset or "0" -> disabled (byte-identical current payload, design's
    Migration/Rollout rollback switch); any other value -> enabled."""
    return os.environ.get(SEGUIMIENTO_DEPURACION_ENV, "0").strip() not in ("", "0")


def _stickers_para_depuracion(evaluaciones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapts the cached `evaluaciones[]` shape
    (`stickers_atencionsismo.normalize_sticker`) into
    `inspectores_depuracion.depurar()`'s `stickers` contract.

    KNOWN GAP (documented on `inspectores_depuracion.py`'s own module
    docstring, confirmed while wiring this): `fecha` is the MATCHED
    Firestore evaluación's own date when one exists, not always
    byte-identical to the sticker's raw `fechaCreacion` — `normalize_sticker`
    never preserves the raw value once a match overrides it. This proxy is
    the best available signal without threading a new raw-timestamp field
    through `normalize_sticker` (a Phase 2 file, out of this PR's assigned
    scope) — a real follow-up, not a silent shortcut. Impact: `perfil.
    tiene_sticker_valido`/`dias_inactivo` may be marginally stale for the
    subset of stickers with a Firestore-matched evaluación."""
    out: list[dict[str, Any]] = []
    for evaluacion in evaluaciones:
        insp = evaluacion.get("inspector") or {}
        out.append({
            "origen": evaluacion.get("origen"),
            "fecha_creacion": evaluacion.get("fecha"),
            "inspector": {
                "identificacion": insp.get("identificacion"),
                "codigo": insp.get("codigo"),
                "nombre_completo": insp.get("nombre_completo"),
            },
        })
    return out


def _roster_para_depuracion(roster_by_cedula: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Adapts `stickers.inspector_profiles()`'s `roster_by_cedula` shape
    into `depurar()`'s expected roster contract.

    `correo`: `inspectores` Firestore docs carry no dedicated `correo`
    field (only `correo_contacto`, W3) — used here as a best-effort proxy;
    documented gap, not a silent guess (both feed the SAME
    `es_cuenta_no_persona` heuristic, which is priority-only, never an
    auto-disqualifier by itself). `creado_en`: no backing Firestore field
    either — left blank; `_elegir_survivor`'s tie-break already degrades to
    insertion order when every tied candidate's `creado_en` is blank."""
    out: dict[str, dict[str, str]] = {}
    for cedula_key, perfil in (roster_by_cedula or {}).items():
        correo_contacto = perfil.get("correo_contacto", "")
        out[cedula_key] = {
            "identificacion": perfil.get("identificacion", ""),
            "nombre_completo": perfil.get("nombre_completo", ""),
            "correo": correo_contacto,
            "codigo": perfil.get("codigo", ""),
            "entidad": perfil.get("entidad", ""),
            "tarjeta_profesional": perfil.get("tarjeta_profesional", ""),
            "num_telefono": perfil.get("num_telefono", ""),
            "correo_contacto": correo_contacto,
            "creado_en": "",
        }
    return out


def _nombres_survey(db: Any) -> list[str]:
    """`survey_cali` docs' evaluator name field — `nombre_evaluador`
    (`scripts/refresh_data.py`'s own RENAME_MAP: "Nombre Evaluador:" ->
    "nombre_evaluador"). Spec: "Non-Person Counts Deduped Against
    survey_cali"."""
    nombres: list[str] = []
    for snap in db.collection(survey_cali_svc.SURVEY_CALI_COLLECTION).get():
        data = snap.to_dict() or {}
        nombre = data.get("nombre_evaluador")
        if nombre:
            nombres.append(str(nombre))
    return nombres


def _depuracion_a_dict(resultado: depuracion_svc.Depuracion) -> dict[str, Any]:
    return {
        "activa": resultado.activa,
        "motivo": resultado.motivo,
        "referencia_generada_en": resultado.referencia_generada_en,
        "inspectores": list(resultado.inspectores),
        "grupo_externos": resultado.grupo_externos,
        "alias_nombres": resultado.alias_nombres,
        "revision_manual": list(resultado.revision_manual),
    }


class DepuracionCache:
    """Design D4/D6: owns BOTH the reference bundle's own 30-min TTL read
    (Data Flow diagram's "cargar_referencia (30 min)") AND the derived
    `depurar()` result, keyed by the sticker payload's OBJECT IDENTITY plus
    Bogotá `hoy` plus the reference bundle's own `generado_en` — no separate
    TTL of its own for the derived result. `stickers.EvaluacionesCache`
    returns the SAME list object until it refetches, so `self._src is
    evaluaciones` is an O(1), exact recompute signal (same idea that
    class's own docstring documents). One shared lock guards both the
    referencia TTL check and the compare-and-recompute, mirroring
    `EvaluacionesCache`'s own concurrency story."""

    def __init__(self, *, cargar_referencia: Callable[[], referencia_svc.ReferenciaBundle] = referencia_svc.cargar_referencia) -> None:
        self._cargar_referencia = cargar_referencia
        self._referencia_at: float | None = None
        self._referencia: referencia_svc.ReferenciaBundle | None = None
        self._src: list[dict[str, Any]] | None = None
        self._hoy: date | None = None
        self._result: depuracion_svc.Depuracion | None = None
        self._lock = threading.Lock()

    def _referencia_actual(self) -> referencia_svc.ReferenciaBundle:
        now = time.monotonic()
        stale = (
            self._referencia is None
            or self._referencia_at is None
            or (now - self._referencia_at) > REFERENCIA_CACHE_TTL_SECONDS
        )
        if stale:
            nueva = self._cargar_referencia()
            # WARNING fix (adversarial review): `cargar_referencia()` never
            # raises — a transient failure (Blob network blip) degrades to
            # `ReferenciaBundle.vacia()` (`activa=False`), which would
            # otherwise unconditionally REPLACE a last-known-good bundle
            # here. `get_or_compute` below keys its recompute decision off
            # `referencia.activa`, so that replacement would immediately
            # recompute `depurar()` against an EMPTY reference and DISCARD
            # the previously-good `depuracion` result, exactly the
            # regression spec's "Unavailable reference source degrades to
            # last-good cache" scenario forbids. Keep serving the
            # last-known-good bundle instead; only ever adopt a degraded one
            # when there was no good bundle to fall back on. `_referencia_at`
            # still advances either way, so this doesn't retry
            # `cargar_referencia()` on every single request during an
            # outage — it still respects the 30-min TTL cadence.
            tenia_buena = self._referencia is not None and self._referencia.activa
            if not (nueva.activa is False and tenia_buena):
                self._referencia = nueva
            self._referencia_at = now
        assert self._referencia is not None
        return self._referencia

    def get_or_compute(
        self,
        *,
        evaluaciones: list[dict[str, Any]],
        hoy: date,
        compute: Callable[[referencia_svc.ReferenciaBundle], depuracion_svc.Depuracion],
    ) -> depuracion_svc.Depuracion:
        """`compute(referencia)` builds a fresh `Depuracion` from the
        CURRENT reference bundle — invoked only when the sticker payload's
        identity, `hoy`, or `referencia.generado_en`/`activa` changed since
        the last call.

        WARNING fix (adversarial review): `compute(...)` runs a Firestore
        roster/survey scan plus `depuracion_svc.depurar()`'s O(n×m) rapidfuzz
        loop — potentially hundreds of ms. It now runs OUTSIDE `self._lock`
        so a concurrent `/stickers-atencionsismo` request (including one
        that would have hit the `unchanged` fast path) is never blocked
        behind it; the lock only protects the referencia TTL check above and
        the cached-result compare/write below, mirroring
        `EvaluacionesCache`'s own concurrency story."""
        with self._lock:
            referencia = self._referencia_actual()
            unchanged = (
                self._result is not None
                and self._src is evaluaciones
                and self._hoy == hoy
                and self._result.referencia_generada_en == referencia.generado_en
                and self._result.activa == referencia.activa
            )
            if unchanged:
                assert self._result is not None
                return self._result

        result = compute(referencia)

        with self._lock:
            self._result = result
            self._src = evaluaciones
            self._hoy = hoy
            return self._result


def build_payload(
    db: Any,
    evaluaciones_cache: stickers.EvaluacionesCache,
    *,
    roster_out: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """`roster_out` (WARNING fix, adversarial review): when given, this
    function fills it IN PLACE with the same `roster_by_cedula` map this
    call's own `stickers.inspector_profiles(db)` scan already produced — a
    caller that also needs the roster for `depuracion_svc.depurar()`
    (`get_stickers_atencionsismo`'s `_compute`) can reuse it instead of
    triggering a second, redundant `inspectores` collection scan. Optional
    and additive: every existing caller that omits it keeps the exact same
    `list[dict]` return value/type."""
    user, password = atencionsismo.credentials_from_env()

    async def _pull() -> list[dict]:
        async with httpx.AsyncClient() as client:
            return await atencionsismo.fetch_stickers(client, user, password)

    async def _pull_with_deadline() -> list[dict]:
        try:
            return await asyncio.wait_for(_pull(), timeout=STICKERS_FETCH_DEADLINE_S)
        except asyncio.TimeoutError as exc:
            raise atencionsismo.ApiUnavailableError(
                f"informe/stickers: no respondio en {STICKERS_FETCH_DEADLINE_S}s", status=503
            ) from exc

    # Perf (2026-09-10): the upstream page walk and the two Firestore reads
    # below are mutually independent — nothing about `roster_map` or
    # `firestore_evals` depends on `rows` or vice versa — so they still run
    # concurrently instead of one after another. The blocking
    # (google-cloud-firestore) calls go through `_FIRESTORE_READ_EXECUTOR`
    # (see its own comment for why NOT `asyncio.to_thread`'s default
    # executor) so their I/O overlaps the async HTTP walk rather than
    # waiting behind it.
    #
    # Fail-fast fix (adversarial review, 2026-09-10): this is NOT
    # byte-identical to the old sequential code — a plain
    # `asyncio.gather(..., return_exceptions=True)` over all three would
    # always wait for the two Firestore reads even when the upstream walk
    # fails, which the OLD sequential code never did (it never even reached
    # them). `evaluaciones_cache` is shared with `GET /evaluaciones`, so an
    # upstream-only failure incorrectly running its own degraded/TTL/
    # Blob-restore chain would be a data-quality regression, not just a
    # perf one. So `pull` is awaited FIRST and, on failure, the other two
    # tasks are cancelled instead of awaited-through:
    #   - response body/status/headers, the error PRIORITY (pull error >
    #     roster error > firestore error), and the success-path result
    #     usage are all preserved exactly as before.
    #   - the ONE residual difference from byte-identical sequential
    #     behavior: on a pull failure, the two Firestore calls may already
    #     be running in their worker threads by the time we cancel (see the
    #     comment below) — the old code never started them at all in that
    #     case.
    async def _gather() -> tuple[list[dict], Any, Any]:
        loop = asyncio.get_running_loop()
        pull_task = asyncio.create_task(_pull_with_deadline())
        # Started AFTER the pull task so the failure path below never
        # depends on their timing to stay correct — they still run
        # concurrently with the upstream walk once the loop schedules them.
        # `ensure_future` because `run_in_executor` already returns a
        # Future, not a coroutine (`create_task` requires the latter).
        roster_task = asyncio.ensure_future(
            # F6: ONE `inspectores` collection scan for both roster shapes —
            # see inspector_profiles' own doc comment.
            loop.run_in_executor(_FIRESTORE_READ_EXECUTOR, stickers.inspector_profiles, db)
        )
        firestore_task = asyncio.ensure_future(
            loop.run_in_executor(
                _FIRESTORE_READ_EXECUTOR, evaluaciones_cache.get_or_fetch,
                lambda: stickers.list_evaluaciones(db),
            )
        )

        try:
            rows = await pull_task
        except BaseException:
            # Never consume or wait through the Firestore reads once the
            # upstream walk has failed.
            roster_task.cancel()
            firestore_task.cancel()
            # NOTE: cancelling the Task wrapping a `run_in_executor(...)`
            # call cancels OUR await of it right away (the task settles as
            # CancelledError without needing the executor thread to
            # finish), but it does NOT interrupt the underlying thread
            # mid-call: once a concurrent.futures.Future is actually
            # running, it cannot be aborted, so the google-cloud-firestore
            # call may keep running to completion in a background thread
            # after this function has already re-raised. That is harmless —
            # `stickers.EvaluacionesCache.get_or_fetch`'s own lock keeps the
            # call concurrency-safe either way, and nothing awaits its
            # result — but it is the residual behavioral difference noted
            # above. (This is also exactly why these two reads use
            # `_FIRESTORE_READ_EXECUTOR` instead of the default executor:
            # see that constant's comment — the default executor would make
            # `asyncio.run` below block on that background thread anyway,
            # defeating this whole fail-fast path.)
            await asyncio.gather(roster_task, firestore_task, return_exceptions=True)
            raise

        roster_result, firestore_result = await asyncio.gather(
            roster_task, firestore_task, return_exceptions=True
        )
        return rows, roster_result, firestore_result

    rows, roster_result, firestore_result = asyncio.run(_gather())
    # sync route runs in the threadpool: no running loop here, same as the
    # old single `asyncio.run(_pull_with_deadline())` call. A pull failure
    # propagates straight out of `_gather()` above (same exception/priority
    # as the old sequential code raised in that case).

    if isinstance(roster_result, BaseException):
        raise roster_result
    if isinstance(firestore_result, BaseException):
        raise firestore_result

    roster_map, roster_by_cedula = roster_result
    firestore_evals = firestore_result

    if roster_out is not None:
        roster_out.clear()
        roster_out.update(roster_by_cedula)

    if evaluaciones_cache.degraded:
        # design D4: "si Firestore falla, el fetch falla completo" — a
        # Blob-restored evaluaciones payload has `inspector.np`,
        # `nombre_completo` AND `identificacion` all blanked
        # (`stickers._redact_for_blob`), so silently joining it here would
        # poison BOTH the Fase fallback and the identity of a matched record
        # (not just serve a stale one). Fail this fetch too so THIS cache
        # (`stickers_atencionsismo_cache`) runs its own serve-stale /
        # Blob-restore chain instead of serving a payload with a poisoned
        # identity/Fase.
        raise RuntimeError("evaluaciones degradado: sin match de Firestore no hay identidad ni NP de respaldo")
    return build_evaluaciones(
        rows, roster_by_codigo=roster_map, evaluaciones_firestore=firestore_evals,
        roster_by_cedula=roster_by_cedula,
    )


@router.get("/stickers-atencionsismo")
def get_stickers_atencionsismo(
    request: Request,
    claims: dict[str, Any] = Depends(require_role("admin", "viewer")),
) -> JSONResponse:
    cache: stickers.EvaluacionesCache = request.app.state.stickers_atencionsismo_cache
    evaluaciones_cache: stickers.EvaluacionesCache = request.app.state.stickers_evaluaciones_cache
    db = credentials.sismo().firestore
    # WARNING fix (adversarial review, part a): filled in place by
    # `build_payload` ONLY when it actually runs a fresh fetch (`cache.
    # get_or_fetch` below calls it only while stale) — `_compute` reuses it
    # instead of re-scanning `inspectores` via `stickers.inspector_profiles`
    # a second time in the SAME request.
    roster_by_cedula_holder: dict[str, dict[str, str]] = {}
    try:
        payload = cache.get_or_fetch(
            lambda: build_payload(db, evaluaciones_cache, roster_out=roster_by_cedula_holder)
        )
    except HTTPException:
        raise
    except (atencionsismo.ApiUnavailableError, atencionsismo.ApiCredentialsError,
            atencionsismo.ApiEmptyResultError, RuntimeError) as exc:
        # RuntimeError: `build_payload`'s own "evaluaciones cache is
        # degraded, sin match de Firestore no hay identidad ni NP de
        # respaldo" guard above, re-raised verbatim by
        # `EvaluacionesCache.get_or_fetch` when this (stickers) cache is
        # ALSO cold and has nothing in Blob to restore. Maps to 503 like
        # every other upstream-unavailable case — this is the "nothing
        # trustworthy to serve" branch, not an unclassified bug, so it does
        # not deserve the generic 502 below.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - same CORS-preserving catch-all as GET /evaluaciones
        logging.exception("stickers-atencionsismo: fallo no clasificado")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # `body` — never `payload` itself — is where `depuracion` gets added
    # below (D3, PII isolation): `payload` is the exact list object
    # `stickers_atencionsismo_cache` already persisted (redacted) to the
    # PUBLIC Blob inside `cache.get_or_fetch` above, before this line ever
    # runs. Nothing past this point may mutate `payload`.
    body: dict[str, Any] = {
        "ok": True, "fuente": "atencionsismo", "evaluaciones": payload, "degraded": cache.degraded,
    }

    # CRITICAL (adversarial review): `depuracion` carries full PII
    # (cédula/tarjeta_profesional/num_telefono/correo_contacto/no_persona) —
    # this route's own `require_role("admin", "viewer")` above lets ANY
    # authenticated @cali.gov.co account through as "viewer"
    # (`app.auth.roles.role_from_claims`), not a curated admin allowlist.
    # The rest of the payload stays available to both roles (unchanged); only
    # this block is gated to `admin` so a viewer's response is exactly the
    # same shape as the flag-off case (key absent entirely).
    if _depuracion_habilitada() and role_from_claims(claims) == "admin":
        depuracion_cache: DepuracionCache = request.app.state.depuracion_cache
        hoy = datetime.now(fechas_es_co.BOGOTA).date()

        def _compute(referencia: referencia_svc.ReferenciaBundle) -> depuracion_svc.Depuracion:
            # WARNING fix (adversarial review, part a): reuse the roster
            # `build_payload` already scanned THIS request instead of
            # calling `stickers.inspector_profiles(db)` again — that helper
            # scans the whole `inspectores` collection, and `build_payload`
            # (via `cache.get_or_fetch`) already paid that cost moments ago
            # whenever it actually ran. Falls back to a fresh scan only for
            # the rarer case where `depuracion_cache` decides to recompute
            # (e.g. the referencia TTL just expired) without a fresh sticker
            # fetch happening in THIS same request, so `roster_by_cedula_
            # holder` was never populated.
            roster_by_cedula = roster_by_cedula_holder or None
            if roster_by_cedula is None:
                _, roster_by_cedula = stickers.inspector_profiles(db)
            return depuracion_svc.depurar(
                stickers=_stickers_para_depuracion(payload),
                roster_by_cedula=_roster_para_depuracion(roster_by_cedula),
                nombres_survey=_nombres_survey(db),
                referencia=referencia,
                hoy=hoy,
            )

        try:
            resultado = depuracion_cache.get_or_compute(evaluaciones=payload, hoy=hoy, compute=_compute)
            body["depuracion"] = _depuracion_a_dict(resultado)
        except Exception:
            # Advisory-only feature (spec: "Classification never writes to
            # the inspector record"): a depuración failure must never turn
            # an otherwise-healthy sticker response into an error. Omitting
            # the key entirely (not a half-filled `depuracion`) keeps the
            # frontend's existing "`depuracion` absent -> current code path"
            # branch (design's Frontend Changes) as the fallback, same as
            # the flag-off case.
            logging.exception(
                "stickers-atencionsismo: fallo calculando depuracion; se omite el bloque (advisory-only)"
            )

    return JSONResponse(body)
