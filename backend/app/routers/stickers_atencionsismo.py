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
import copy
import json
import logging
import os
import threading
import time
import traceback
from datetime import date, datetime
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.deps import require_role
from app.auth.roles import role_from_claims
from app.credentials import clients as credentials
from app.routers import stickers
from app.services import atencionsismo, blob_lkg, fechas_es_co
from app.services import inspectores_depuracion as depuracion_svc
from app.services import inspectores_referencia as referencia_svc
from app.services import survey_cali as survey_cali_svc
from app.services.versioned_cache import VersionedCache
from app.services.stickers_atencionsismo import build_evaluaciones

router = APIRouter()
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

STICKERS_LKG_BLOB = "data/stickers_atencionsismo_last_good.json"

# design.md's Data Flow diagram: "cargar_referencia (30 min)" — how often
# `DepuracionCache` re-reads the (private) reference bundle from Blob,
# independent of the sticker payload's own 5-min TTL.
REFERENCIA_CACHE_TTL_SECONDS = 30 * 60

# Versioned component caches (design D22): each Firestore-derived input has its
# own TTL, so "0 recomputes/hour with static inputs" holds. Admin writes to the
# roster invalidate its component immediately (`routers/stickers.py`).
ROSTER_CACHE_TTL_SECONDS = 30 * 60
SURVEY_NAMES_CACHE_TTL_SECONDS = 60 * 60
EVALUACIONES_FS_CACHE_TTL_SECONDS = 15 * 60

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


# Per-request opt-in (design D25): the Stickers tab uses the same endpoint and
# must never pay for (or download) the PII block, so only a caller that asks for
# it - Seguimiento - gets it.
DEPURACION_QUERY_PARAM = "depuracion"
DEPURACION_OPT_IN_VALUE = "1"

# `motivo` of the block served when the sticker snapshot is the redacted Blob
# restore (design D16); distinct from the reference bundle's own `motivo`s.
MOTIVO_STICKERS_DEGRADADOS = "stickers_degradados"


def _depuracion_solicitada(request: Request) -> bool:
    """True only for exactly one `depuracion=1` parameter. `0`, `true`, `yes`,
    an empty value, `01`, `1 ` and a repeated parameter (`depuracion=1&depuracion=0`,
    even `depuracion=1&depuracion=1`) all mean "not requested" - never an
    error: an unknown value is simply ignored."""
    return request.query_params.getlist(DEPURACION_QUERY_PARAM) == [DEPURACION_OPT_IN_VALUE]


def _depuracion_stickers_degradados() -> depuracion_svc.Depuracion:
    """D16: a degraded snapshot (redacted LKG, `identificacion` blank) is
    unattributable, so no table is built from it - with the full universe every
    code-less person would turn into a `candidato_desactivacion` and every
    suspicious one would be wrongly collapsed. Nothing is read or computed."""
    return depuracion_svc.Depuracion(
        activa=False, motivo=MOTIVO_STICKERS_DEGRADADOS, referencia_generada_en="",
        inspectores=(), grupo_externos=None, alias_nombres={}, revision_manual=(),
    )


def _resumen_seguro(exc: BaseException) -> str:
    """Exception TYPE plus where it was raised, never its message: a `KeyError`
    on a cédula key or a `ValueError` quoting a row would put PII in the log
    (spec: contact fields and identity keys are never logged in clear)."""
    frames = traceback.extract_tb(exc.__traceback__)
    donde = f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno} in {frames[-1].name}" if frames else "?"
    return f"{type(exc).__name__} at {donde}"


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


def depuracion_inputs_version(stickers_proj: list[dict[str, Any]]) -> str:
    """Content fingerprint of EXACTLY what `depurar()` consumes from the stickers
    (the `_stickers_para_depuracion` projection), judgment-day W5.

    `snapshot_version` hashes the whole served payload (photos, comments,
    coordinates, addresses...) because it will drive the HTTP ETag; keying the
    depuración cache by it would force a full `depurar()` for changes the engine
    never reads. This one changes only when an `origen`, a `fecha` or an
    inspector identity changes.

    Order-independent, like the engine itself (its result does not depend on the
    sticker order): each projected row is serialised canonically and the rows are
    sorted, so a re-ordered upstream walk fingerprints the same. A duplicated
    sticker still counts (multiset, not set). `default=str` keeps a raw
    `datetime` deterministic instead of raising."""
    rows = sorted(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) for row in stickers_proj)
    return blob_lkg.payload_hash(rows)


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


def _hoy() -> date:
    """Bogota calendar day (design D6): part of the depuracion cache key, so
    the midnight rollover costs exactly one recompute. Module-level so tests
    can pin it."""
    return datetime.now(fechas_es_co.BOGOTA).date()


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


# Upper bound a follower waits on another thread's referencia download or
# compute before giving up. A sync route parks one Starlette worker thread (40
# by default) for the whole wait, so 120 s of hung upstream could exhaust the
# pool; 10 s is far above a normal compute (~0.3 s) and a timeout degrades to a
# `calculo_fallido` / `referencia_timeout` block, never to an error. Read at
# call time so tests can inject a short deadline.
_FLIGHT_WAIT_S = 10.0

# `motivo`s of the blocks served instead of a table (see `DepuracionCache`).
MOTIVO_CALCULO_FALLIDO = "calculo_fallido"
MOTIVO_REFERENCIA_TIMEOUT = "referencia_timeout"


def _depuracion_calculo_fallido() -> depuracion_svc.Depuracion:
    """The compute failed and there is no result computed under the SAME key to
    fall back on: a declared gap (`activa=False`, no table), never data
    computed for other inputs."""
    return depuracion_svc.Depuracion(
        activa=False, motivo=MOTIVO_CALCULO_FALLIDO, referencia_generada_en="",
        inspectores=(), grupo_externos=None, alias_nombres={}, revision_manual=(),
    )


class _Flight:
    """One in-flight referencia load or compute that concurrent callers wait
    on. `error`/`result` are written BEFORE `event.set()`."""

    __slots__ = ("event", "result", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


def _referencia_key(referencia: referencia_svc.ReferenciaBundle) -> tuple[str, bool, str, str]:
    """Fingerprint of the bundle. `huella` (content hash, D24) is what detects
    a same-day republish; `activa`/`generado_en`/`motivo` are what the RESULT
    embeds (`Depuracion.referencia_generada_en`/`motivo`), so they must key it
    too (a degraded bundle has `huella == ""`)."""
    return (referencia.huella, referencia.activa, referencia.generado_en, referencia.motivo)


class DepuracionCache:
    """Design D23/D24: owns the reference bundle's own 30-min TTL read AND the
    derived `depurar()` result.

    Derived result: keyed by content fingerprints
    `(inputs_version, roster_version, survey_version, referencia key, hoy)` -
    never by object identity. `inputs_version` is the PROJECTED fingerprint of
    exactly what `depurar()` reads from the stickers
    (`depuracion_inputs_version`), not the whole-payload `snapshot_version`
    (that one also moves with photos/comments/coordinates and will drive the
    HTTP ETag). A single-flight in-flight marker per key: the first caller for a
    key computes OUTSIDE the lock, concurrent callers of the SAME key wait on
    the marker and receive the same result (N requests = 1 compute). Only the
    current key (plus the last-good result it replaced) is retained; no TTL of
    its own - freshness is bounded by the component TTLs.

    Failure (judgment-day W6): the marker is always cleared, also when the
    leader raises. A result computed under a DIFFERENT key is never presented as
    current (across a `hoy` rollover it would serve yesterday's dias_inactivo).
    So when the leader's compute fails: the leader serves the outcome below
    straight away (it just failed; no retry of its own) and each waiter retries
    ONCE as a new leader (single-flight again: N failing waiters cost one extra
    compute, not N). The outcome of a failed compute is the last-good result
    ONLY if it was computed under the same key (strict equality; reachable after
    `invalidate()`), else the `calculo_fallido` block (`activa=False`, no
    table). Leader and waiters therefore always agree. A waiter that times out
    on a hung leader gets the same outcome without a competing compute.

    Referencia: the TTL check under the lock is O(1); at most ONE caller
    downloads (outside the lock), everyone else reads the last-good bundle
    without waiting (a cold start with nothing to read waits, bounded by
    `_FLIGHT_WAIT_S`, for that one download and then degrades to a
    `referencia_timeout` bundle). A failed load keeps the last-good bundle and
    still advances the timestamp, so it is retried at most once per TTL; a
    degraded bundle is adopted only when there was no good one.

    Immutable boundary (D23): the cached `Depuracion` is shared across
    requests and its dicts are mutable, so EVERY caller receives a deep copy -
    a caller that mutates what it got can never change what the next request
    receives."""

    def __init__(
        self,
        *,
        cargar_referencia: Callable[[], referencia_svc.ReferenciaBundle] = referencia_svc.cargar_referencia,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._cargar_referencia = cargar_referencia
        self._clock = clock
        self._lock = threading.Lock()
        self._referencia: referencia_svc.ReferenciaBundle | None = None
        self._referencia_at: float | None = None
        self._referencia_flight: _Flight | None = None
        self._key: tuple | None = None  # key a cache HIT is valid for (cleared by invalidate())
        self._result: depuracion_svc.Depuracion | None = None
        self._result_key: tuple | None = None  # key `_result` was computed under (kept by invalidate())
        self._inflight: dict[tuple, _Flight] = {}
        self._generation = 0
        # (payload object, projection, fingerprint) of the last payload seen: the
        # projection is O(rows), so it is paid once per NEW payload object, never
        # per request. Identity is exact: the sticker cache keeps the same list
        # object while the content is unchanged.
        self._inputs: tuple[Any, list[dict[str, Any]], str] | None = None

    def _now(self) -> float:
        return (self._clock or time.monotonic)()

    def inputs_for(self, payload: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        """`(projection, inputs_version)` of the sticker payload, memoised by
        payload identity. The projection is shared read-only with `depurar()`
        (which never mutates its inputs)."""
        memo = self._inputs
        if memo is not None and memo[0] is payload:
            return memo[1], memo[2]
        projected = _stickers_para_depuracion(payload)
        version = depuracion_inputs_version(projected)
        self._inputs = (payload, projected, version)
        return projected, version

    def _referencia_fresh(self, now: float) -> bool:
        if self._referencia is None or self._referencia_at is None:
            return False
        age = now - self._referencia_at
        return 0 <= age <= REFERENCIA_CACHE_TTL_SECONDS  # a negative age (clock went back) is stale

    def _referencia_actual(self) -> referencia_svc.ReferenciaBundle:
        # Real time, never the injectable clock: this bounds an actual wait.
        deadline = time.monotonic() + _FLIGHT_WAIT_S
        while True:
            with self._lock:
                if self._referencia_fresh(self._now()):
                    assert self._referencia is not None
                    return self._referencia
                flight = self._referencia_flight
                if flight is None:
                    flight = self._referencia_flight = _Flight()
                    leader = True
                else:
                    leader = False
                    if self._referencia is not None:
                        return self._referencia  # last-good: never wait behind a download
            if leader:
                return self._descargar_referencia(flight)
            # Cold start with a download already running: wait for that one, but
            # never past the deadline (a hung download must not park the worker).
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not flight.event.wait(remaining):
                logging.warning("depuracion: referencia download still running; serving a degraded bundle")
                return referencia_svc.ReferenciaBundle.vacia(motivo=MOTIVO_REFERENCIA_TIMEOUT)
            if flight.error is not None and self._referencia is None:
                raise flight.error

    def _descargar_referencia(self, flight: _Flight) -> referencia_svc.ReferenciaBundle:
        """Runs OUTSIDE `self._lock` (a Blob GET can take up to 30 s)."""
        nueva: referencia_svc.ReferenciaBundle | None = None
        error: Exception | None = None
        try:
            nueva = self._cargar_referencia()
        except Exception as exc:  # noqa: BLE001 - the contract says it never raises; be safe anyway
            error = exc
        with self._lock:
            try:
                if error is None:
                    assert nueva is not None
                    # `cargar_referencia()` never raises: a transient failure degrades to
                    # `ReferenciaBundle.vacia()` (`activa=False`), which must NOT replace a
                    # last-known-good bundle (spec: "Unavailable reference source degrades to
                    # last-good cache"); a degraded one is adopted only with nothing better.
                    tenia_buena = self._referencia is not None and self._referencia.activa
                    if not (nueva.activa is False and tenia_buena):
                        self._referencia = nueva
                    self._referencia_at = self._now()  # advances on failure too: <= 1 retry per TTL
                elif self._referencia is not None:
                    self._referencia_at = self._now()
                    logging.warning(
                        "depuracion: referencia load failed (%s); keeping the last-good bundle",
                        type(error).__name__,
                    )
            finally:
                self._referencia_flight = None
                flight.error = error
                flight.event.set()
            referencia = self._referencia
        if referencia is None:
            assert error is not None
            raise error
        return referencia

    def get_or_compute(
        self,
        *,
        inputs_version: Any,
        roster_version: Any = "",
        survey_version: Any = "",
        hoy: date,
        compute: Callable[[referencia_svc.ReferenciaBundle], depuracion_svc.Depuracion],
    ) -> depuracion_svc.Depuracion:
        """`compute(referencia)` builds a fresh `Depuracion` from the CURRENT
        bundle - invoked only when one of the five fingerprints changed since
        the last call, by exactly one caller per key, outside every lock. A
        failing `compute` never raises (see the class docstring for the
        outcome); only a `BaseException` that is not an `Exception` unwinds."""
        referencia = self._referencia_actual()
        key = (inputs_version, roster_version, survey_version, _referencia_key(referencia), hoy)
        retried = False
        while True:
            cached = None
            with self._lock:
                if self._key == key and self._result is not None:
                    cached = self._result
                else:
                    flight = self._inflight.get(key)
                    leader = flight is None
                    if flight is None:
                        flight = self._inflight[key] = _Flight()
                    generation = self._generation
            if cached is not None:
                return copy.deepcopy(cached)
            if leader:
                try:
                    return self._computar(key, flight, generation, compute, referencia)
                except Exception as exc:  # noqa: BLE001 - advisory feature: degrade, never raise
                    logging.error(
                        "depuracion: compute failed (%s); serving the last-good of the same key or a declared gap",
                        _resumen_seguro(exc),
                    )
                    return self._sin_resultado(key)

            if not flight.event.wait(_FLIGHT_WAIT_S):
                logging.warning("depuracion: timed out waiting for the in-flight compute")
                return self._sin_resultado(key)
            if flight.error is None:
                return copy.deepcopy(flight.result)
            if retried:
                return self._sin_resultado(key)
            retried = True  # the leader failed: ONE retry as (or behind) a new leader, then give up

    def _sin_resultado(self, key: tuple) -> depuracion_svc.Depuracion:
        """Outcome of a failed compute for `key`: the last-good ONLY when it was
        computed under exactly this key, else the declared gap."""
        with self._lock:
            last_good = self._result if self._result_key == key else None
        if last_good is not None:
            return copy.deepcopy(last_good)
        return _depuracion_calculo_fallido()

    def _computar(self, key, flight, generation, compute, referencia) -> depuracion_svc.Depuracion:
        try:
            result = compute(referencia)
        except BaseException as exc:
            with self._lock:
                self._inflight.pop(key, None)
            flight.error = exc
            flight.event.set()
            raise
        with self._lock:
            self._inflight.pop(key, None)
            if generation == self._generation:  # an invalidate() mid-compute leaves it uncached
                self._key, self._result, self._result_key = key, result, key
        flight.result = result
        flight.event.set()
        return copy.deepcopy(result)

    def invalidate(self) -> None:
        """Forces one recompute on the next request; keeps the last-good
        result (with the key it was computed under) for a failing recompute of
        that same key."""
        with self._lock:
            self._generation += 1
            self._key = None


_EVALUACIONES_DEGRADADAS = "evaluaciones degradado: sin match de Firestore no hay identidad ni NP de respaldo"


def build_payload(
    db: Any,
    evaluaciones_cache: stickers.EvaluacionesCache,
    *,
    roster_cache: VersionedCache | None = None,
    evaluaciones_fs_cache: VersionedCache | None = None,
) -> list[dict[str, Any]]:
    """`roster_cache` / `evaluaciones_fs_cache` (D22, optional and additive):
    when given, the two Firestore reads go through those versioned component
    caches (TTL + serve-stale) instead of scanning on every 5-minute refresh,
    and the SAME roster snapshot is reused by the depuracion compute (no second
    `inspectores` scan). Omitted -> every call scans, exactly as before."""
    user, password = atencionsismo.credentials_from_env()

    def _read_roster() -> Any:
        if roster_cache is None:
            return stickers.inspector_profiles(db)
        return roster_cache.get(lambda: stickers.inspector_profiles(db)).value

    def _read_evaluaciones() -> list[dict[str, Any]]:
        evaluaciones = evaluaciones_cache.get_or_fetch(lambda: stickers.list_evaluaciones(db))
        if evaluaciones_cache.degraded:
            # design D4: "si Firestore falla, el fetch falla completo" — a
            # Blob-restored evaluaciones payload has `inspector.np`,
            # `nombre_completo` AND `identificacion` all blanked
            # (`stickers._redact_for_blob`), so silently joining it here would
            # poison BOTH the Fase fallback and the identity of a matched record
            # (not just serve a stale one). Fail this fetch too so THIS cache
            # (`stickers_atencionsismo_cache`) runs its own serve-stale /
            # Blob-restore chain instead of serving a payload with a poisoned
            # identity/Fase. Checked INSIDE the read so a versioned component
            # cache can never pin a degraded payload for its whole TTL.
            raise RuntimeError(_EVALUACIONES_DEGRADADAS)
        return evaluaciones

    def _read_evaluaciones_firestore() -> list[dict[str, Any]]:
        if evaluaciones_fs_cache is None:
            return _read_evaluaciones()
        return evaluaciones_fs_cache.get(_read_evaluaciones).value

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
            loop.run_in_executor(_FIRESTORE_READ_EXECUTOR, _read_roster)
        )
        firestore_task = asyncio.ensure_future(
            loop.run_in_executor(_FIRESTORE_READ_EXECUTOR, _read_evaluaciones_firestore)
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
    state = request.app.state
    try:
        # One consistent (payload, content version, degraded) triple. The
        # whole-payload version is ETag material (slice 09b); the depuracion
        # cache is keyed by the projected `depuracion_inputs_version` (W5).
        snapshot = cache.get_or_fetch_snapshot(
            lambda: build_payload(
                db, evaluaciones_cache,
                roster_cache=state.roster_cache, evaluaciones_fs_cache=state.evaluaciones_fs_cache,
            )
        )
        payload = snapshot.payload
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
        "ok": True, "fuente": "atencionsismo", "evaluaciones": payload, "degraded": snapshot.degraded,
    }

    # CRITICAL (adversarial review): `depuracion` carries full PII
    # (cédula/tarjeta_profesional/num_telefono/correo_contacto/no_persona) —
    # this route's own `require_role("admin", "viewer")` above lets ANY
    # authenticated @cali.gov.co account through as "viewer"
    # (`app.auth.roles.role_from_claims`), not a curated admin allowlist.
    # The rest of the payload stays available to both roles (unchanged); only
    # this block is gated to `admin` so a viewer's response is exactly the
    # same shape as the flag-off case (key absent entirely).
    #
    # Gates, cheapest first, ALL required (D25 + D16): the request carries
    # exactly `?depuracion=1`, the flag is on, the caller is an admin. A request
    # that fails any of them performs 0 depuracion work: no component read, no
    # referencia GET, no `depurar` (its body stays byte-identical to flag-off).
    if _depuracion_solicitada(request) and _depuracion_habilitada() and role_from_claims(claims) == "admin":
        depuracion_cache: DepuracionCache = state.depuracion_cache
        hoy = _hoy()

        try:
            if snapshot.degraded:
                # D16: unattributable stickers -> a declared gap, not a table.
                # Scoped to the STICKERS source only: a missing reference bundle
                # keeps its own path (D5) inside `depurar`. Nothing is read.
                resultado = _depuracion_stickers_degradados()
            else:
                # Component reads are TTL-guarded and single-flight (D22): a warm
                # request pays O(1) here. `build_payload` already shares the SAME
                # roster snapshot, so one window costs one `inspectores` scan.
                roster = state.roster_cache.get(lambda: stickers.inspector_profiles(db))
                survey = state.survey_names_cache.get(lambda: sorted(_nombres_survey(db)))

                # The cache key carries the PROJECTED fingerprint of what `depurar`
                # reads (W5), not `snapshot.version` (a new photo must not force a
                # recompute); the projection is memoised per payload object.
                stickers_proj, inputs_version = depuracion_cache.inputs_for(payload)

                def _compute(referencia: referencia_svc.ReferenciaBundle) -> depuracion_svc.Depuracion:
                    return depuracion_svc.depurar(
                        stickers=stickers_proj,
                        roster_by_cedula=_roster_para_depuracion(roster.value[1]),
                        nombres_survey=survey.value,
                        referencia=referencia,
                        hoy=hoy,
                    )

                resultado = depuracion_cache.get_or_compute(
                    inputs_version=inputs_version, roster_version=roster.version,
                    survey_version=survey.version, hoy=hoy, compute=_compute,
                )
            body["depuracion"] = _depuracion_a_dict(resultado)
        except Exception as exc:
            # Advisory-only feature (spec: "Classification never writes to
            # the inspector record"): a depuración failure must never turn
            # an otherwise-healthy sticker response into an error. Omitting
            # the key entirely (not a half-filled `depuracion`) keeps the
            # frontend's existing "`depuracion` absent -> current code path"
            # branch (design's Frontend Changes) as the fallback, same as
            # the flag-off case. Logged by type and location only - never
            # `logging.exception`, whose traceback text carries the message.
            logging.error(
                "stickers-atencionsismo: fallo calculando depuracion (%s); se omite el bloque (advisory-only)",
                _resumen_seguro(exc),
            )

    return JSONResponse(body)
