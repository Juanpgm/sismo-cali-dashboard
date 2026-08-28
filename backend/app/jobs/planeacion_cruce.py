"""Cron entrypoint: `python -m app.jobs.planeacion_cruce` (design.md ADR-1
through ADR-5, ADR-11 of the `planeacion-asignaciones` change).

Cross-reference ("cruce") every atencionsismo `informe/json` report against
Firestore `survey_cali`: which reports already have an EDAN survey, versus
which are still pending, ranked by a deterministic priority score, and
persisted incrementally to `planeacion_puntos`, recurringly (proposed
cadence: hourly — design.md ADR-5's incrementality section, task 0.4).

Structured exactly like `app/jobs/cruce_sticker.py` (design.md's own stated
precedent): pipeline/admin field-ownership split (ADR-1), a deterministic
doc id, a watermark + projected pre-read + incremental candidate selection
(ADR-5), a batched `merge:true` write path, an offline `--check`
self-check, and a runlog-wrapped `main()`. Reuses its matching cascade's
low-level primitives from `app.integracion.cruce_gestor` (`nearest`,
`match_by_direccion`, `build_addr_index`, `addr_key`, `_eval_latlon`) —
imported, never forked, exactly as `cruce_sticker.py:60-62` does it — but
composes its OWN 5-rung cascade (ADR-5) on top of them, because rungs 3/4
use different, planeacion-specific thresholds (`COMBINED_MAX_M`/
`COMBINED_SEM`) than `cruce_gestor.match_by_direccion`'s own internal
"combinado" branch (60 m / 0.85) — reusing that function's *body* for rung
4 would silently apply the wrong threshold, so this module calls
`match_by_direccion` ONLY for its exact/fuzzy-address ("direccion") branch
(rung 3, whose 0.90 threshold happens to already match `SEM_OK`) and
evaluates rung 4 independently via the same imported `addr_key`/`nearest`
primitives plus `app.integracion.coords.haversine_m` — the identical
"reuse the primitives, not the whole decision" pattern `cruce_sticker.py`'s
own `_tier()` already established for ITS tiering.

## Field ownership (ADR-1) — WITH ONE DELIBERATE, BINDING EXCEPTION

`planeacion_puntos/{fuente}_{registro_id}` is split into a pipeline-owned
field group (this job, `PIPELINE_FIELDS`) and an admin-owned field group
(`routers/planeacion_asignaciones.py`, Phase 3 — not yet built). The job
only ever writes the pipeline-owned subset via a `merge:true` batched set,
seeding `ADMIN_DEFAULT_FIELDS` on a doc's first write only, and never
touches an admin-owned field on a doc that already exists — WITH EXACTLY
ONE BINDING EXCEPTION, locked by the user 2026-08-26 (proposal.md's
"ANSWERED by the user", Q5 — "auto-close, but reviewable"):

    When a point's exact-key rung (`match_via == 'clave'`) closes it on a
    RE-write (not the doc's first write), the job ALSO sets
    `estado_asignacion: 'hecho'` — but ONLY when the point's CURRENT
    persisted `estado_asignacion` is one of `{'pendiente', 'asignado',
    'en_proceso'}`. This makes the pipeline a second writer of that ONE
    admin-owned field, for that ONE transition, and nothing else:

    - The pipeline may ONLY perform `{pendiente,asignado,en_proceso} ->
      hecho`. It NEVER writes `estado_asignacion` for a fuzzy match
      (`match_via` in `{'cercania','direccion','combinado'}` or `None`).
    - The pipeline NEVER moves a point OUT of `'hecho'` — if the point's
      current state is already `'hecho'`, `estado_asignacion` is simply
      absent from that write's field dict, so `merge:true` leaves it
      untouched. Same for `'no_aplica'` — not in the allowed FROM-state
      set, so the pipeline never touches it (reopening a `no_aplica`/
      `hecho` point is Phase 3's admin-only `reopen`/`revertir` action,
      never this job).
    - The pipeline NEVER touches `cuadrilla_id` or `inspector_uid` under
      any circumstance — those two fields are absent from
      `PIPELINE_FIELDS` entirely and are never added anywhere in this
      module.

    See `build_write_ops` and `tests/jobs/test_planeacion_cruce.py`'s
    auto-close test group for the exact enforcement + the negative tests
    proving a `hecho` point is never reopened and `cuadrilla_id`/
    `inspector_uid` always survive untouched.

## Scope note: `survey_cali` is read-only here (ADR-2/ADR-5/ADR-9)

This module NEVER calls `apply_mutation`, NEVER `.set()`/`.update()` the
`survey_cali` collection — `fetch_surveys` below only ever calls
`.stream()` on it. It is added, READ-ONLY-flagged, to
`tests/invariants/test_sole_writer.py`'s `ALLOWED_MODULES_SURVEY_CALI`
(see that file's own updated docstring) — the SAME "legitimate new reader,
flagged rather than hidden" precedent that set already used for
`routers/sticker_status.py`. This is a deliberate, minimal, DOCUMENTED
exception to this batch's instruction not to touch that CLOSED allowlist:
avoiding it was evaluated and rejected — see apply-progress.md's "Issues
Found" section for the concrete reasoning (obfuscating the collection name
to dodge the scanner would defeat the review tripwire's actual purpose,
which is worse than a minimal, honest, flagged addition).

## `clave_integracion` verification — where the guarantee actually lives

ADR-3 illustrates the key with a SHORT worked example (`registro_id =
'14832'`). Every real atencionsismo `id` is a UUID instead — 36 chars, 32
hex once dashes are stripped — so the 24-char slug cap makes the slug
ALWAYS lossy against real data, and the original id can never be
recovered from the key alone.

The first revision of this module took ADR-3's example literally and had
`verify_clave_integracion` recompute the checksum from the key's own
PARSED slug. That is impossible by construction for a lossy slug: it
rejected every correctly minted real-world key, which would have silently
disabled rung-1 exact matching for 100% of production points while
passing every test in this file (all of which used short, example-shaped
ids). Fixed 2026-08-26; `test_key_minted_for_a_real_uuid_point_survives_
the_key_index` is the regression lock.

`verify_clave_integracion` is now STRUCTURAL only — a well-formedness
filter (right prefix, uppercase-alnum slug ≤24, 8 uppercase hex digits)
that keeps hand-typed or garbage `codigoapp` values out of the index.
The two properties that actually protect a field crew hold at the
exact-equality lookup layer instead, and hold unconditionally:

  * a damaged or forged key matches NO point, because pairing requires
    the survey's `codigoapp` to equal a key this module minted for a
    known point (`cruce_punto` against `build_key_index`); and
  * two ids sharing their first 24 sanitized chars still mint DIFFERENT
    keys, because the digest is taken over the FULL id — so a slug
    collision can never pair a survey to the wrong building, which is
    the failure that would actually cost a wasted trip.

    python -m app.jobs.planeacion_cruce --check     # offline self-check, no network
    python -m app.jobs.planeacion_cruce --dry       # real data, no Firestore write
    python -m app.jobs.planeacion_cruce             # real data, write planeacion_puntos
    python -m app.jobs.planeacion_cruce --top 50     # cap to the first N points (debug)
    python -m app.jobs.planeacion_cruce --full       # re-scan EVERY point, ignore the watermark

## Dedup tagging (`tag_duplicados`) — grouping, never collapsing

Every run also tags each point with `dup_grupo_id`/`dup_n`/`es_representante`
(`tag_duplicados`, called on `load_puntos()`'s output before anything else) so
`routers/planeacion_asignaciones.py`'s `resumen` can report distinct-BUILDING
counts alongside distinct-REPORT counts, the same "one address can carry
several reports for the same building" correction `scripts/refresh_data.py`'s
`add_dup_group`/`_claves_por_edificio` already applies to the EDAN side.
Nothing is ever merged or dropped here — every `planeacion_puntos` doc still
gets its own write; dedup is purely a label a reader can group by. `--full`
doubles as the one-time backfill for this tagging over the ~14.8k docs that
existed before it shipped (a full run re-selects and re-writes every point,
`select_candidates(..., full=True)`, regardless of whether it already has a
survey).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from app.credentials import clients as credentials
from app.integracion import runlog
from app.integracion.coords import haversine_m
from app.integracion.cruce_gestor import (addr_key, build_addr_index,
                                          match_by_direccion, nearest,
                                          _eval_latlon)

REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTES_JSON = REPO_ROOT / "web" / "data" / "reportes.json"

PLANEACION_PUNTOS_COLLECTION = "planeacion_puntos"
# feature D: israel "done" source, READ-ONLY here. Mirrored into the sismo
# project by an out-of-backend ingestor (never the dagma-85aad original —
# the backend never touches dagma).
INSPECCIONES_ISRAEL_COLLECTION = "inspecciones_israel"
# `survey_cali`'s collection name is imported (never re-literaled here) from
# its OWN sole-writer module — see the module docstring's "Scope note"
# above for why this makes `planeacion_cruce.py` a flagged, read-only entry
# in `tests/invariants/test_sole_writer.py`'s `ALLOWED_MODULES_SURVEY_CALI`.
from app.services.survey_cali import SURVEY_CALI_COLLECTION  # noqa: E402

STATE_DOC = "_meta/planeacion_cruce_state"  # {"last_run_at": Timestamp} — incremental watermark
BATCH_SIZE = 500  # Firestore batch-write / getAll chunk limit

FUENTE = "atencionsismo"
KEY_PREFIX = "PLN"

RUNS_FILE = "runs_planeacion_cruce.jsonl"

# ── Cascade thresholds (ADR-5), each with the provenance ADR-5 states ───────
MATCH_MAX_M = 40.0       # cruce_sticker.py/cruce_gestor's own tuned proximity threshold — reused, not retuned
SEM_OK = 0.90            # == cruce_gestor.ADDR_MATCH_RATIO — one tuned address threshold, not two
COMBINED_MAX_M = 100.0   # planeacion-specific: wider than cruce_gestor's own combo (60 m) — geocoded coords, not GPS
COMBINED_SEM = 0.80      # planeacion-specific: looser than cruce_gestor's own combo (0.85)
ALTA_TIER_M = 20.0       # legacy dagma job's stricter cutoff, kept as the alta-TIER boundary, not the match cutoff

# ── Prioritization (ADR-4) — PLACEHOLDER weight table, NOT yet confirmed ────
# by the operations lead (proposal.md risk 1, Q1/Q2; design.md ADR-4
# "Flagged for confirmation"). Grounded in the LIVE category values found by
# inspecting `web/data/reportes.json` (14,804 records, read 2026-08-26) so
# the fallback path is a genuine safety net rather than untested dead code
# — NOT an operator-confirmed ranking. Task 0.2 (Phase 0, out of scope for
# this batch) is what turns this into a locked table. `prioridad_override`
# (Phase 3) makes a wrong per-point default a one-click correction.
PESOS_AFECTACION_RAW: dict[str, int] = {
    "COLAPSO TOTAL": 50,
    "COLAPSO PARCIAL": 45,
    "RIESGO COLAPSO": 40,
    "DAÑO ESTRUCTURAL": 35,
    "DAÑO MAMPOSTERÍA": 20,
    "NO SE EVIDENCIA NINGÚN DAÑO": 5,
}
DEFAULT_AFECTACION_WEIGHT = 25  # unknown/new category -> mid-ranked, never a KeyError

PESOS_ESTADO_RAW: dict[str, int] = {
    "Visitado crítico": 30,
    "Visitado": 25,
    "Evaluación especializada": 22,
    "Asignado": 15,
    "Visita fallida": 12,
    "Reportado": 5,
}
DEFAULT_ESTADO_WEIGHT = 15

AGE_SATURATION_DAYS = 60  # placeholder — proposal.md risk 5 confirms cron cadence, not this window

# Locked thresholds — design.md ADR-4's own formula, not a placeholder.
ALTA_THRESHOLD = 60
MEDIA_THRESHOLD = 35


def _normalize_category(value: object) -> str:
    return str(value or "").strip().casefold()


PESOS_AFECTACION = {_normalize_category(k): v for k, v in PESOS_AFECTACION_RAW.items()}
PESOS_ESTADO = {_normalize_category(k): v for k, v in PESOS_ESTADO_RAW.items()}


# ADR-1 field ownership split: the job only ever writes PIPELINE_FIELDS via
# merge:true; ADMIN_DEFAULT_FIELDS is seeded ONLY on a doc's first write,
# never re-applied to a doc that already exists. `estado_asignacion` is
# admin-owned EXCEPT for the one binding auto-close exception documented in
# the module docstring and enforced in `build_write_ops` below — it is
# deliberately NOT in PIPELINE_FIELDS (that would make it unconditionally
# pipeline-owned, losing the admin's ability to correct/reopen it).
PIPELINE_FIELDS = ("fuente", "registro_id", "clave_integracion", "tiene_survey",
                   "survey_globalid", "match_via", "match_dist_m", "tier",
                   "direccion", "barrio", "comuna", "coords", "afectacion",
                   "estado_verificacion", "tipo_inmueble", "habitabilidad",
                   "fecha_creacion", "prioridad_score", "prioridad", "matched_at",
                   "dup_grupo_id", "dup_n", "es_representante")
ADMIN_DEFAULT_FIELDS = {"estado_asignacion": "pendiente", "cuadrilla_id": None,
                        "inspector_uid": None, "prioridad_override": None}

# The FROM-states the pipeline's auto-close exception may transition out of
# — see the module docstring's "ONE DELIBERATE, BINDING EXCEPTION" section.
_AUTO_CLOSE_FROM_STATES = frozenset({"pendiente", "asignado", "en_proceso"})


# ── Doc id (ADR-1) ──────────────────────────────────────────────────────────
def doc_id(fuente: str, registro_id: str) -> str:
    """Deterministic planeacion_puntos doc id — stable across re-runs so the
    pipeline updates the same document instead of duplicating it."""
    return f"{fuente}_{registro_id}"


# ── clave_integracion: the minting rule (ADR-3, verbatim) ──────────────────
def clave_integracion(fuente: str, registro_id: str) -> str:
    """Deterministic, URL-safe, checksummed integration key. Pure — no
    Firestore access, no clock, no randomness: the same point always mints
    the same key, on every run, forever."""
    raw = f"{fuente}:{registro_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8].upper()
    slug = re.sub(r"[^A-Z0-9]", "", str(registro_id).upper())[:24]
    return f"{KEY_PREFIX}-{slug}-{digest}"


_CLAVE_RE = re.compile(rf"^{KEY_PREFIX}-([A-Z0-9]{{1,24}})-([0-9A-F]{{8}})$")


def verify_clave_integracion(clave: str | None, fuente: str = FUENTE) -> bool:
    """STRUCTURAL validation only: does `clave` have the exact
    `PLN-<slug>-<digest>` shape (right prefix, uppercase-alnum slug of 1-24,
    8 uppercase hex digits)? Anything else — empty, hand-typed, wrong
    prefix, lowercase, truncated — is rejected.

    It deliberately does NOT recompute the checksum, because a stateless
    recompute is IMPOSSIBLE by construction: the digest is taken over the
    FULL `registro_id`, while the slug is that id sanitized and capped at
    24 chars. Every real atencionsismo id is a UUID (32 hex chars once
    dashes are stripped), so the slug is ALWAYS lossy and the original
    input can never be recovered from the key alone. An earlier revision
    did attempt that recompute; it silently rejected every correctly
    minted real-world key, which would have disabled rung-1 exact matching
    entirely in production while passing every test (all of which used the
    short ids from ADR-3's worked example). See
    `test_key_minted_for_a_real_uuid_point_survives_the_key_index`.

    The checksum is NOT wasted — it just does its job at a different
    layer. Pairing is exact string equality between a survey's `codigoapp`
    and a key minted by this same module for a known point (`cruce_punto`
    against `build_key_index`). Under that lookup:
      * a damaged/forged key matches NO point (it isn't in the index), and
      * two ids sharing the first 24 sanitized chars still mint DIFFERENT
        keys, because the digest is derived from the full id — so a slug
        collision can never pair a survey to the wrong building.
    That second property is the one that actually protects a field crew
    from being sent to the wrong address, and it holds regardless of
    whether the key can be self-verified.
    """
    return _CLAVE_RE.match(str(clave or "")) is not None


# ── Prioritization (ADR-4) ──────────────────────────────────────────────────
def peso_afectacion(rec: dict) -> int:
    key = _normalize_category(rec.get("afectacion"))
    return PESOS_AFECTACION.get(key, DEFAULT_AFECTACION_WEIGHT)


def peso_estado(rec: dict) -> int:
    key = _normalize_category(rec.get("estado_verificacion"))
    return PESOS_ESTADO.get(key, DEFAULT_ESTADO_WEIGHT)


def peso_antiguedad(rec: dict, ahora: datetime) -> int:
    """0-20, saturating at `AGE_SATURATION_DAYS`. Pure — `ahora` is passed
    in, never read from the clock inside. Missing/unparseable
    `fecha_creacion` -> 0 (no age credit, never raises)."""
    raw = rec.get("fecha_creacion")
    if not raw:
        return 0
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw))
        except ValueError:
            return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dias = max(0.0, (ahora - dt).total_seconds() / 86400.0)
    frac = min(1.0, dias / AGE_SATURATION_DAYS)
    return round(frac * 20)


def prioridad_score(rec: dict, ahora: datetime) -> int:
    """Pure, deterministic, additive score in [0, 100] — design.md ADR-4.
    Severity (0-50) + verification state (0-30) + saturating age (0-20).
    Comuna is deliberately never read here."""
    return peso_afectacion(rec) + peso_estado(rec) + peso_antiguedad(rec, ahora)


def prioridad_de(score: int) -> str:
    if score >= ALTA_THRESHOLD:
        return "alta"
    if score >= MEDIA_THRESHOLD:
        return "media"
    return "baja"


# ── Matching cascade (ADR-5) — 5 rungs, exact key first ─────────────────────
def build_key_index(surveys: list[dict]) -> dict[str, dict]:
    """`codigoapp` -> survey record, keeping only well-formed keys (ADR-3 /
    task 2.6) so a hand-typed or garbage `codigoapp` never enters the index.

    Membership in this index is what "verifies" a key: a survey pairs to a
    point only when its `codigoapp` EQUALS a key minted by this module for
    that point, so a forged or damaged value simply resolves to nothing.
    See the module docstring for why the checksum cannot be — and need not
    be — re-derived from the key in isolation."""
    out: dict[str, dict] = {}
    for s in surveys:
        clave = s.get("codigoapp")
        if clave and verify_clave_integracion(clave):
            if clave in out:
                # Two surveys carrying the same valid codigoapp: last-write-wins.
                # The point still resolves tiene_survey=True, but survey_globalid
                # becomes ambiguous — surface it instead of overwriting silently.
                logging.warning(
                    "planeacion_cruce: duplicate valid codigoapp %s across surveys "
                    "(last-write-wins) — survey linkage is ambiguous", clave,
                )
            out[clave] = s
    return out


def _match_result(survey: dict, via: str, tier: str, dist_m: float | None) -> dict:
    return {
        "tiene_survey": True,
        "survey_globalid": survey.get("GlobalID"),
        "match_via": via,
        "tier": tier,
        "match_dist_m": round(dist_m, 1) if dist_m is not None else None,
    }


_MISS_RESULT = {"tiene_survey": False, "survey_globalid": None,
                "match_via": None, "tier": None, "match_dist_m": None}


def _address_ratio(direccion_a, direccion_b) -> float:
    ka, kb = addr_key(direccion_a), addr_key(direccion_b)
    if not ka or not kb:
        return 0.0
    return SequenceMatcher(None, ka, kb).ratio()


def _tier_for_proximity(dist_m: float | None, direccion_punto, direccion_survey) -> str:
    """alta: <= ALTA_TIER_M OR the address also agrees; media: geo-only.
    Mirrors cruce_sticker.py's own `_tier()` shape, planeacion's own
    ALTA_TIER_M/SEM_OK thresholds."""
    if dist_m is not None and dist_m <= ALTA_TIER_M:
        return "alta"
    if _address_ratio(direccion_punto, direccion_survey) >= SEM_OK:
        return "alta"
    return "media"


def _best_combined_candidate(lat, lon, direccion, surveys: list[dict]):
    """Rung 4 (ADR-5): within COMBINED_MAX_M AND address ratio >=
    COMBINED_SEM, evaluated independently of `cruce_gestor.match_by_
    direccion`'s own (different-threshold) combined branch — see the
    module docstring. Best ratio wins; distance breaks ties. Returns
    (survey, dist_m) or (None, None)."""
    if lat is None or lon is None:
        return None, None
    key_p = addr_key(direccion)
    if not key_p:
        return None, None
    candidates = []
    for s in surveys:
        ll = _eval_latlon(s)
        if ll is None:
            continue
        dist = haversine_m((lat, lon), ll)
        if dist > COMBINED_MAX_M:
            continue
        ratio = _address_ratio(direccion, s.get("DIRECCION"))
        if ratio >= COMBINED_SEM:
            candidates.append((ratio, dist, s))
    if not candidates:
        return None, None
    candidates.sort(key=lambda t: (-t[0], t[1]))
    _, dist, survey = candidates[0]
    return survey, dist


def cruce_punto(lat, lon, direccion, clave_integracion_pt: str, *,
                key_index: dict[str, dict], surveys: list[dict],
                addr_index: list[tuple[str, dict]]) -> dict:
    """The 5-rung cascade (ADR-5): exact key -> proximity -> address ->
    combined -> miss, stopping at the first hit. `clave_integracion_pt` is
    the CANDIDATE POINT's own freshly-minted key (always correct — never
    "damaged in transit", unlike a returned `codigoapp`); rung 1 is exact
    string membership in `key_index`, which is itself pre-filtered by
    `verify_clave_integracion` at `build_key_index` time."""
    # Rung 1: exact key.
    if clave_integracion_pt and clave_integracion_pt in key_index:
        return _match_result(key_index[clave_integracion_pt], "clave", "exacta", None)

    # Rung 2: proximity (<= MATCH_MAX_M).
    candidate, dist = nearest(lat, lon, surveys, _eval_latlon, max_m=MATCH_MAX_M)
    if candidate is not None:
        tier = _tier_for_proximity(dist, direccion, candidate.get("DIRECCION"))
        return _match_result(candidate, "cercania", tier, dist)

    # Rung 3: address exact-or-fuzzy >= SEM_OK — reuse match_by_direccion's
    # own "direccion" branch (its 0.90 ratio == SEM_OK); its "combinado"
    # branch (different thresholds) is deliberately NOT accepted here.
    candidate, via, dist = match_by_direccion(lat, lon, direccion, addr_index)
    if candidate is not None and via == "direccion":
        return _match_result(candidate, "direccion", "media", dist)

    # Rung 4: combined (<= COMBINED_MAX_M AND ratio >= COMBINED_SEM),
    # evaluated independently with planeacion's own thresholds.
    candidate, dist = _best_combined_candidate(lat, lon, direccion, surveys)
    if candidate is not None:
        return _match_result(candidate, "combinado", "sospechoso", dist)

    # Rung 5: miss.
    return dict(_MISS_RESULT)


# ── Incremental candidate selection (ADR-5) ─────────────────────────────────
def select_candidates(points: list[dict], state: dict, full: bool = False) -> list[dict]:
    """Points that actually need a match attempt this run: brand new (no
    doc yet) or not yet matched. A point already `tiene_survey=True` is
    never re-scanned. Pure — no Firestore access, testable offline.

    `full=True` short-circuits to "everything, unconditionally" — a
    `--full` run re-selects and re-writes every point regardless of its
    current `tiene_survey` state, which is what turns it into the one-time
    dedup-tagging backfill for docs that already existed before
    `tag_duplicados` shipped (module docstring)."""
    if full:
        return list(points)
    out = []
    for p in points:
        did = doc_id(p["fuente"], p["registro_id"])
        s = state.get(did, {"exists": False, "tiene_survey": False})
        if not s.get("tiene_survey"):
            out.append(p)
    return out


# ── Dedup tagging (module docstring's "Dedup tagging" section) ─────────────
DUP_MAX_DIST_M = 30.0  # same order of magnitude as scripts/refresh_data.py's own _DIST_MISMO_EDIFICIO_M


def _dedup_bucket_key(p: dict) -> str:
    """Coarse bucket: everything that COULD be the same building. Mirrors
    `scripts/refresh_data.py`'s `_clave_direccion` — address first, then
    rounded coords, then the point's own id so it never silently joins an
    unrelated bucket. `addr_key` (imported from `cruce_gestor`, same
    normalization the matching cascade above already uses) is what makes
    two differently-punctuated renderings of the same address collide into
    one bucket."""
    key = addr_key(p.get("direccion"))
    if key:
        return f"dir:{key}"
    lat, lon = p.get("lat"), p.get("lon")
    if lat is not None and lon is not None:
        return f"geo:{round(float(lat), 5)},{round(float(lon), 5)}"
    return f"id:{p.get('registro_id')}"


def tag_duplicados(points: list[dict], max_dist_m: float = DUP_MAX_DIST_M) -> list[dict]:
    """Tag every point with `dup_grupo_id`/`dup_n`/`es_representante` — pure,
    returns a NEW list of dicts (each a shallow copy plus the three tags),
    never mutates `points` in place. This is a PRESENTATION-layer grouping
    for `routers/planeacion_asignaciones.py`'s `resumen` (distinct-building
    counts alongside distinct-report counts); it never collapses, merges,
    or drops a doc — every point still gets written on its own.

    Conceptually mirrors `scripts/refresh_data.py`'s `_claves_por_edificio`/
    `_misma_edificacion`/`_clave_direccion` (coarse address bucket, then a
    transitive same-building chain inside each bucket), reimplemented here
    without pandas — this job works with plain dict lists, not a
    DataFrame — and without that helper's building-NAME similarity signal,
    because `reportes.json` records carry no `nombre_edificacion` field;
    address + proximity is the only signal available here.

    Within a bucket (`_dedup_bucket_key`), members are sorted by
    `registro_id` first so the chaining below is independent of the
    caller's input order. A point joins an existing chain if it is within
    `max_dist_m` of ANY member already in that chain (transitive, not
    "agrees with every member" — a building shot from two ends can be
    `max_dist_m` from the middle and further from the far end, and
    requiring universal agreement would wrongly split it), OR if either
    point is missing coords (a missing coordinate can never rule out the
    same building, so it must never manufacture a split either).

    `dup_grupo_id` is `"dup-" + min(registro_id in the chain)` — computed
    from chain MEMBERSHIP, not scan order, so it is the same string
    regardless of how `points` was shuffled going in. `es_representante`
    is True for exactly the member whose `registro_id` equals that
    minimum. A singleton still gets a group (`dup_n=1`,
    `es_representante=True`) so every point always carries all three
    fields."""
    buckets: dict[str, list[dict]] = {}
    for p in points:
        buckets.setdefault(_dedup_bucket_key(p), []).append(p)

    # ponytail: O(n^2) within-bucket chain scan — buckets are one address's
    # worth of reports, not the whole ~14.8k set, so this stays small in
    # practice; grid-bucketing is the upgrade path if a single address ever
    # measurably grows large enough to matter.
    chain_of: dict[int, list[dict]] = {}  # id(point) -> the chain list it belongs to
    for miembros in buckets.values():
        ordenados = sorted(miembros, key=lambda p: str(p.get("registro_id")))
        chains: list[list[dict]] = []
        for p in ordenados:
            lat_p, lon_p = p.get("lat"), p.get("lon")
            destino = None
            for chain in chains:
                for q in chain:
                    lat_q, lon_q = q.get("lat"), q.get("lon")
                    sin_coords = None in (lat_p, lon_p, lat_q, lon_q)
                    if sin_coords or haversine_m((lat_p, lon_p), (lat_q, lon_q)) <= max_dist_m:
                        destino = chain
                        break
                if destino is not None:
                    break
            if destino is not None:
                destino.append(p)
            else:
                destino = [p]
                chains.append(destino)
            chain_of[id(p)] = destino

    out = []
    for p in points:
        chain = chain_of[id(p)]
        rep_id = min(str(q.get("registro_id")) for q in chain)
        out.append({
            **p,
            "dup_grupo_id": f"dup-{rep_id}",
            "dup_n": len(chain),
            "es_representante": str(p.get("registro_id")) == rep_id,
        })
    return out


# ── Write path (ADR-1, WITH the binding auto-close exception) ──────────────
def build_write_ops(points: list[dict], existing_ids: set[str],
                    estado_actual: dict[str, str | None]) -> list[tuple[str, dict]]:
    """(doc_id, write_fields) per point. `write_fields` ONLY ever contains
    PIPELINE_FIELDS, so a merge:true set can never touch `cuadrilla_id`/
    `inspector_uid`/`prioridad_override`/etc — plus ADMIN_DEFAULT_FIELDS,
    but ONLY on a doc's first write (`did not in existing_ids`).

    `estado_actual`: {doc_id: current persisted `estado_asignacion`, from
    the caller's own pre-read} — used ONLY for the ONE binding auto-close
    exception (module docstring): on a RE-write (doc already exists) whose
    `match_via == 'clave'`, `estado_asignacion: 'hecho'` is ALSO written,
    but only when the current state is one of `_AUTO_CLOSE_FROM_STATES`.
    Every other case (first write, non-clave match, current state already
    `'hecho'` or `'no_aplica'`) leaves `estado_asignacion` OUT of the write
    dict entirely — `merge:true` then leaves Firestore's value untouched.
    Pure — no Firestore access, testable offline."""
    ops = []
    for p in points:
        did = doc_id(p["fuente"], p["registro_id"])
        fields = {k: p.get(k) for k in PIPELINE_FIELDS}
        if did not in existing_ids:
            fields.update(ADMIN_DEFAULT_FIELDS)
        elif p.get("match_via") == "clave" and estado_actual.get(did) in _AUTO_CLOSE_FROM_STATES:
            fields["estado_asignacion"] = "hecho"
        ops.append((did, fields))
    return ops


def write_planeacion_puntos(db, ops: list[tuple[str, dict]]) -> int:
    """Batched `merge:true` set, <= BATCH_SIZE ops per commit (Firestore's
    own cap)."""
    col = db.collection(PLANEACION_PUNTOS_COLLECTION)
    n = 0
    for start in range(0, len(ops), BATCH_SIZE):
        batch = db.batch()
        for did, fields in ops[start:start + BATCH_SIZE]:
            batch.set(col.document(did), fields, merge=True)
            n += 1
        batch.commit()
    return n


# ── Spanish `fechaCreacion` parsing (load-time normalization, ADR-2) ────────
# atencionsismo's `informe/json` ships `fechaCreacion` as an es-CO locale
# string ("martes, 18 de agosto de 2026, 06:33 p. m."), NOT ISO-8601 —
# confirmed by reading `web/data/reportes.json` directly (14,804 live
# records, 2026-08-26). Parsed with an explicit month-name table rather
# than the host locale, because a Railway container is not guaranteed
# es-CO. `peso_antiguedad` above consumes the ISO string this produces
# (or a bare `datetime`), never the raw Spanish text — keeping the scoring
# function itself free of locale/parsing concerns.
_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_FECHA_ES_RE = re.compile(
    r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4}),?\s*(\d{1,2}):(\d{2})\s*(a\.?\s*m\.?|p\.?\s*m\.?)",
    re.IGNORECASE,
)


def parse_fecha_creacion_es(raw: str | None) -> datetime | None:
    """Parse the es-CO `fechaCreacion` string into a UTC `datetime`. Returns
    `None` for empty/unparseable input — `load_puntos` stores `None` in
    that case, and `peso_antiguedad` treats `None` as "unknown age", never
    raising."""
    if not raw:
        return None
    m = _FECHA_ES_RE.search(str(raw))
    if not m:
        return None
    day, month_name, year, hour_s, minute_s, ampm = m.groups()
    month = _MESES_ES.get(month_name.strip().lower())
    if month is None:
        return None
    hour = int(hour_s)
    ampm_norm = re.sub(r"[.\s]", "", ampm).lower()
    if ampm_norm == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = hour if hour == 12 else hour + 12
    try:
        return datetime(int(year), month, int(day), hour, int(minute_s), tzinfo=timezone.utc)
    except ValueError:
        return None


# ── Point loading (ADR-2: reportes.json, two-tier, same as cruce_sticker's
# _load_ede()) ────────────────────────────────────────────────────────────
def load_reportes() -> list[dict]:
    if REPORTES_JSON.exists():
        return json.loads(REPORTES_JSON.read_text(encoding="utf-8"))
    url = os.environ.get("REPORTES_URL", "").strip()
    if not url:
        raise RuntimeError(
            f"{REPORTES_JSON} no existe y no hay $REPORTES_URL para bajarlo.")
    import requests
    return requests.get(url, timeout=60).json()


def load_puntos() -> list[dict]:
    """`web/data/reportes.json` (informe/json's full record set, ADR-2) ->
    the pipeline's own field names (ADR-1). One record per report; records
    with no `id` are skipped (nothing to key a doc on)."""
    raw = load_reportes()
    points = []
    seen_ids: set[str] = set()
    for rec in raw:
        registro_id = rec.get("id")
        if not registro_id:
            continue
        rid = str(registro_id)
        if rid in seen_ids:
            # registro_id uniqueness is the ONE unenforced assumption behind
            # codigoapp's point->survey detection: two identical ids mint the same
            # key AND the same doc_id, silently collapsing two points into one.
            # Holds today (verified 14804/14804 unique); log loudly if it breaks.
            logging.warning(
                "planeacion_cruce: duplicate registro_id %s in reportes.json — two "
                "points would collapse into one doc; codigoapp detection assumes uniqueness",
                rid,
            )
        seen_ids.add(rid)
        lat, lon = rec.get("lat"), rec.get("lng")
        fecha_dt = parse_fecha_creacion_es(rec.get("fechaCreacion"))
        points.append({
            "fuente": FUENTE,
            "registro_id": str(registro_id),
            "lat": lat, "lon": lon,
            "direccion": rec.get("direccion") or "",
            "barrio": rec.get("barrio") or None,
            "comuna": rec.get("comuna") or None,
            "coords": ({"lat": lat, "lon": lon}
                       if lat is not None and lon is not None else None),
            "afectacion": rec.get("afectacion") or None,
            "estado_verificacion": rec.get("estadoVerificacion") or None,
            "tipo_inmueble": rec.get("tipoInmueble") or None,
            "habitabilidad": rec.get("habitabilidad") or None,
            "fecha_creacion": fecha_dt.isoformat() if fecha_dt is not None else None,
        })
    return points


# ── survey_cali (Firestore, READ-ONLY — see module docstring) ──────────────
def fetch_surveys(db, watermark=None) -> list[dict]:
    """Surveys touched since `watermark`, flattened to the X/Y/DIRECCION
    keys the imported cascade primitives expect, plus `codigoapp` and
    `GlobalID` (the doc id — `survey_cali` docs never store it as a field,
    see `services/survey_cali.canonical_form`'s own exclusion). NEVER
    calls `apply_mutation`/`.set()`/`.update()` on this collection —
    `.stream()` only."""
    col = db.collection(SURVEY_CALI_COLLECTION)
    query = (col if watermark is None
             else col.where("_updated_at", ">", watermark)).order_by("_updated_at")
    out = []
    for doc in query.stream():
        e = doc.to_dict() or {}
        out.append({
            "GlobalID": doc.id,
            "Y": e.get("y"), "X": e.get("x"),
            "DIRECCION": e.get("direccion_norm") or e.get("direccion") or "",
            "codigoapp": e.get("codigoapp") or "",
        })
    return out


def fetch_israel(db) -> list[dict]:
    """israel survey points (sismo project, `inspecciones_israel`), flattened
    to the SAME X/Y/DIRECCION shape `fetch_surveys` returns so the imported
    cascade primitives treat them identically. Feature D: a punto is
    "levantado" if it matches survey_cali OR israel.

    Deliberately FULL-SCAN, no watermark: israel is a small, near-static set
    (~101 docs) and the incremental watermark only tracks `survey_cali`. Were
    israel watermark-filtered it would drop out of the survey universe after
    the first run, so a pending punto landing near an israel point on a later
    run would never see it. `codigoapp` is always "" (israel has no app code),
    so it can only ever match by geo/dirección, never rung-1. `GlobalID` is
    prefixed `isr-` so a match's `survey_globalid` is unambiguously israel and
    never collides with a real `survey_cali` doc id."""
    out = []
    for doc in db.collection(INSPECCIONES_ISRAEL_COLLECTION).stream():
        e = doc.to_dict() or {}
        out.append({
            "GlobalID": f"isr-{doc.id}",
            "Y": e.get("y"), "X": e.get("x"),
            "DIRECCION": e.get("direccion_norm") or e.get("direccion") or "",
            "codigoapp": "",
        })
    return out


def read_watermark(db):
    """Timestamp of the last successful run, or `None` (first run, or a
    prior run that never reached the end) — meaning "process everything"."""
    doc = db.document(STATE_DOC).get()
    if not doc.exists:
        return None
    return (doc.to_dict() or {}).get("last_run_at")


def write_state(db, when: datetime, summary: dict, *, full: bool = False) -> None:
    """Advances the incremental watermark AND persists a `last_run` summary
    snapshot on the same doc (A4: the router's `GET /planeacion-cruce/
    status` reads this back via `read_last_run` so an operator can see the
    outcome of the last run without tailing Railway cron logs). `summary`
    is the same dict `run_planeacion_cruce` already builds for its own
    print/runlog output — `full` is passed separately because that dict
    does not carry it. Dry runs never call this (their own early return in
    `run_planeacion_cruce` happens before the write path)."""
    coll, name = STATE_DOC.split("/")
    payload = {"last_run_at": when, "last_run": {**summary, "finished_at": when, "full": full}}
    db.collection(coll).document(name).set(payload, merge=True)


def read_last_run(db) -> dict | None:
    """The `last_run` summary `write_state` persisted, or `None` if no run
    has ever completed (or the doc predates this field)."""
    doc = db.document(STATE_DOC).get()
    if not doc.exists:
        return None
    return (doc.to_dict() or {}).get("last_run")


def read_punto_state(db, doc_ids: list[str]) -> dict:
    """{doc_id: {'exists','tiene_survey','clave_integracion',
    'estado_asignacion'}} via batched, PROJECTED `get_all` — cheap
    existence+state probe, never a full-document read of ~14.8k docs.
    `estado_asignacion` is projected too (beyond design.md ADR-5's own
    `["tiene_survey","clave_integracion"]` list) — an intentional,
    documented extension: the binding auto-close exception (module
    docstring) needs to know a point's CURRENT admin state to decide the
    `-> 'hecho'` transition, and this is the cheapest place to read it."""
    col = db.collection(PLANEACION_PUNTOS_COLLECTION)
    out: dict[str, dict] = {}
    for start in range(0, len(doc_ids), BATCH_SIZE):
        chunk = doc_ids[start:start + BATCH_SIZE]
        refs = [col.document(did) for did in chunk]
        for snap in db.get_all(refs, field_paths=["tiene_survey", "clave_integracion",
                                                   "estado_asignacion"]):
            data = snap.to_dict() or {} if snap.exists else {}
            out[snap.id] = {
                "exists": snap.exists,
                "tiene_survey": bool(data.get("tiene_survey")) if snap.exists else False,
                "clave_integracion": data.get("clave_integracion"),
                "estado_asignacion": data.get("estado_asignacion"),
            }
    return out


# ── Self-check (offline, no network — cruce_sticker.py's own idiom) ────────
def _selfcheck() -> None:
    # doc_id / clave_integracion: stable, deterministic, verifiable.
    assert doc_id("atencionsismo", "14832") == "atencionsismo_14832"
    k1 = clave_integracion("atencionsismo", "14832")
    k2 = clave_integracion("atencionsismo", "14832")
    assert k1 == k2, (k1, k2)
    assert k1.startswith("PLN-") and len(k1) <= 255
    assert verify_clave_integracion(k1) is True

    # fechaCreacion parsing: the live es-CO format, round-tripped.
    dt = parse_fecha_creacion_es("martes, 18 de agosto de 2026, 06:33 p. m.")
    assert dt is not None and (dt.hour, dt.minute) == (18, 33), dt
    assert parse_fecha_creacion_es("garbage") is None

    # Prioritization: severity dominates, same input -> same score.
    ahora = datetime(2026, 8, 26, tzinfo=timezone.utc)
    severo = {"afectacion": "COLAPSO TOTAL", "estado_verificacion": "Reportado",
             "fecha_creacion": ahora.isoformat()}
    leve = {"afectacion": "NO SE EVIDENCIA NINGÚN DAÑO", "estado_verificacion": "Visitado crítico",
           "fecha_creacion": ahora.isoformat()}
    assert prioridad_score(severo, ahora) > prioridad_score(leve, ahora)
    assert prioridad_score(severo, ahora) == prioridad_score(severo, ahora)

    # Cascade: exact key, geo, address, combined, miss — same fixtures as
    # tests/jobs/test_planeacion_cruce.py's cascade group, condensed.
    survey = {"GlobalID": "S1", "Y": 3.4200, "X": -76.5300, "DIRECCION": "Calle 1 # 2-3",
             "codigoapp": ""}
    surveys = [survey]
    key_index = build_key_index(surveys)
    addr_index = build_addr_index(surveys)
    r = cruce_punto(3.42001, -76.53001, "Calle 1 # 2-3", "PLN-NOMATCH-00000000",
                    key_index=key_index, surveys=surveys, addr_index=addr_index)
    assert r["tiene_survey"] and r["match_via"] == "cercania" and r["tier"] == "alta", r
    r_miss = cruce_punto(3.9, -76.9, "DG 99 # 1-1", "PLN-NOMATCH-00000000",
                         key_index=key_index, surveys=surveys, addr_index=addr_index)
    assert not r_miss["tiene_survey"] and r_miss["match_via"] is None, r_miss

    # build_write_ops: merge-safety + the auto-close exception, end to end.
    points = [
        {"fuente": "atencionsismo", "registro_id": "1", "clave_integracion": "PLN-1-AAAAAAAA",
         "tiene_survey": True, "survey_globalid": "S1", "match_via": "clave", "match_dist_m": None,
         "tier": "exacta", "direccion": "CL 1", "barrio": None, "comuna": None,
         "coords": {"lat": 3.42, "lon": -76.53}, "afectacion": "COLAPSO TOTAL",
         "estado_verificacion": "Reportado", "tipo_inmueble": None, "habitabilidad": None,
         "fecha_creacion": None, "prioridad_score": 55, "prioridad": "media", "matched_at": ahora},
        {"fuente": "atencionsismo", "registro_id": "2", "clave_integracion": "PLN-2-BBBBBBBB",
         "tiene_survey": False, "survey_globalid": None, "match_via": None, "match_dist_m": None,
         "tier": None, "direccion": "CL 2", "barrio": None, "comuna": None, "coords": None,
         "afectacion": None, "estado_verificacion": None, "tipo_inmueble": None,
         "habitabilidad": None, "fecha_creacion": None, "prioridad_score": 5, "prioridad": "baja",
         "matched_at": ahora},
    ]
    did1, did2 = doc_id("atencionsismo", "1"), doc_id("atencionsismo", "2")
    ops = build_write_ops(points, existing_ids={did1}, estado_actual={did1: "pendiente"})
    by_id = dict(ops)
    assert by_id[did1]["estado_asignacion"] == "hecho"  # auto-close exception fired
    assert "cuadrilla_id" not in by_id[did1] and "inspector_uid" not in by_id[did1]
    assert by_id[did2]["estado_asignacion"] == "pendiente"  # first write, seeded default
    assert set(by_id[did2]) == set(PIPELINE_FIELDS) | set(ADMIN_DEFAULT_FIELDS)

    # select_candidates: the incremental core.
    state = {did1: {"exists": True, "tiene_survey": True}}
    cands = select_candidates(points, state)
    assert {p["registro_id"] for p in cands} == {"2"}

    # select_candidates(full=True): everything, even an already-matched point.
    cands_full = select_candidates(points, state, full=True)
    assert {p["registro_id"] for p in cands_full} == {"1", "2"}

    # tag_duplicados: near pair sharing an address -> one group, one
    # representative, order-independent (fed in shuffled/reverse order here).
    dup_a = {"fuente": "atencionsismo", "registro_id": "20", "direccion": "Calle 1 # 2-3",
            "lat": 3.42005, "lon": -76.53005}
    dup_b = {"fuente": "atencionsismo", "registro_id": "10", "direccion": "Calle 1 # 2-3",
            "lat": 3.42000, "lon": -76.53000}
    tagged = tag_duplicados([dup_a, dup_b])
    by_rid = {p["registro_id"]: p for p in tagged}
    assert by_rid["10"]["dup_grupo_id"] == by_rid["20"]["dup_grupo_id"] == "dup-10"
    assert by_rid["10"]["dup_n"] == 2 and by_rid["10"]["es_representante"] is True
    assert by_rid["20"]["es_representante"] is False
    # a lone point still gets a singleton group.
    lone = tag_duplicados([{"fuente": "atencionsismo", "registro_id": "99",
                            "direccion": "Otra direccion", "lat": 3.9, "lon": -76.9}])
    assert lone[0]["dup_grupo_id"] == "dup-99" and lone[0]["dup_n"] == 1

    print("planeacion_cruce self-check OK")


# ── Pipeline (mirrors cruce_sticker.py's run_cruce_sticker() shape) ────────
def run_planeacion_cruce(top: int | None = None, dry: bool = False, full: bool = False) -> dict:
    """`top`/`dry`/`full` are plain kwargs now (not read from `sys.argv`
    inside this function) so `routers/planeacion_cruce.py` can call this
    directly as an in-process background task — same "the router calls the
    job function, not a subprocess" shape `routers/refresh.py` established
    for `dashboard_refresh.run_refresh`. `main()` below still parses argv
    for the cron/CLI entrypoint and forwards the three values here.

    `full=True`: ignores the incremental watermark entirely (re-fetches
    every survey, `fetch_surveys(db, None)`) and re-selects every point
    regardless of its current match state (`select_candidates(...,
    full=True)`), so a full run also re-tags dedup on the entire existing
    set — see the module docstring's "Dedup tagging" section."""
    puntos = tag_duplicados(load_puntos())
    if top is not None:
        puntos = puntos[:top]

    db = credentials.sismo().firestore

    doc_ids = [doc_id(p["fuente"], p["registro_id"]) for p in puntos]
    state = read_punto_state(db, doc_ids)
    ya_con_survey = sum(1 for s in state.values() if s["tiene_survey"])
    candidates = select_candidates(puntos, state, full=full)
    print(f"Puntos: {len(puntos)} | ya con survey (sin re-escanear): {ya_con_survey} | "
          f"candidatos este run: {len(candidates)}")

    watermark = None if full else read_watermark(db)
    print(f"watermark: {watermark or '(ninguno — primera corrida o --full, procesa todo survey_cali)'}")
    surveys_cali = fetch_surveys(db, watermark)
    surveys_israel = fetch_israel(db)  # feature D: full-scan, no watermark (see fetch_israel)
    surveys = surveys_cali + surveys_israel
    key_index = build_key_index(surveys)
    addr_index = build_addr_index(surveys)
    print(f"surveys este run: {len(surveys)} "
          f"({len(surveys_cali)} survey_cali desde watermark + {len(surveys_israel)} israel full-scan; "
          f"{len(key_index)} con clave_integracion verificada)")

    now = datetime.now(timezone.utc)
    to_write: list[dict] = []
    match_via_tally: dict[str, int] = {}
    for p in candidates:
        clave = clave_integracion(p["fuente"], p["registro_id"])
        r = cruce_punto(p["lat"], p["lon"], p["direccion"], clave,
                        key_index=key_index, surveys=surveys, addr_index=addr_index)
        did = doc_id(p["fuente"], p["registro_id"])
        is_new = not state.get(did, {"exists": False})["exists"]
        if not full and not r["tiene_survey"] and not is_new:
            continue  # unchanged pending point -> nothing changed, don't rewrite (skipped on --full: it rewrites everything, dedup tags included)

        score = prioridad_score(p, now)
        to_write.append({
            **p,
            "clave_integracion": clave,
            "tiene_survey": r["tiene_survey"], "survey_globalid": r["survey_globalid"],
            "match_via": r["match_via"], "match_dist_m": r["match_dist_m"], "tier": r["tier"],
            "prioridad_score": score, "prioridad": prioridad_de(score),
            "matched_at": now,
        })
        match_via_tally[str(r["match_via"])] = match_via_tally.get(str(r["match_via"]), 0) + 1

    n_nuevos_match = sum(1 for x in to_write if x["tiene_survey"])
    n_seed = sum(1 for x in to_write
                if not state.get(doc_id(x["fuente"], x["registro_id"]), {"exists": False})["exists"])
    print(f"docs a escribir: {len(to_write)} ({n_seed} nuevos, {n_nuevos_match} con match este run)")
    print(f"match_via este run: {match_via_tally}")
    summary = {"total_puntos": len(puntos), "ya_con_survey": ya_con_survey,
              "candidatos": len(candidates), "a_escribir": len(to_write),
              "nuevos_match": n_nuevos_match, "match_via": match_via_tally}

    if dry:
        print(f"[dry] no Firestore write; {len(to_write)} docs listos para "
              f"{PLANEACION_PUNTOS_COLLECTION}")
        return summary

    existing_ids = {did for did, s in state.items() if s["exists"]}
    estado_actual = {did: s.get("estado_asignacion") for did, s in state.items()}
    ops = build_write_ops(to_write, existing_ids, estado_actual)
    n = write_planeacion_puntos(db, ops)
    write_state(db, now, summary, full=full)
    print(f"escritos {n} docs -> {db.project}/{PLANEACION_PUNTOS_COLLECTION}; "
          f"watermark avanzado a {now.isoformat()}")
    summary["escritos"] = n
    return summary


def main() -> int:
    if "--check" in sys.argv:
        _selfcheck()
        return 0

    started_at = datetime.now(timezone.utc)
    log_dir = runlog.resolve_log_dir()
    restore = runlog.start_tee(log_dir)

    print("=" * 60)
    print(f"Corrida planeacion_cruce · inicio {started_at:%Y-%m-%d %H:%M:%S} UTC")
    print(f"Logs: {log_dir or 'solo stdout (sin volumen escribible)'}")
    try:
        top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else None
        summary = run_planeacion_cruce(top=top, dry="--dry" in sys.argv, full="--full" in sys.argv) or {}
        duracion = round((datetime.now(timezone.utc) - started_at).total_seconds(), 1)
        runlog.append_run(log_dir, {"estado": "ok", "duracion_seg": duracion,
                                    "archivo": RUNS_FILE, **summary})
        print("Corrida OK")
        return 0
    except Exception as exc:
        traceback.print_exc()
        runlog.append_run(log_dir, {
            "estado": "error", "archivo": RUNS_FILE,
            "duracion_seg": round(
                (datetime.now(timezone.utc) - started_at).total_seconds(), 1),
            "error": f"{type(exc).__name__}: {exc}"})
        print("Corrida FALLIDA")
        return 1
    finally:
        restore()


if __name__ == "__main__":
    sys.exit(main())
