# Ported from Juanpgm/normalizador_data_sismo_cali@b013360 cruce_sticker.py
# and @551a73a job_sticker.py (merged into one module) (2026-08-26)
"""Cron entrypoint: `python -m app.jobs.cruce_sticker` (design.md ADR-2/ADR-6).

Cross-reference ("cruce") every Panel point against Firestore `evaluaciones`
(field stickers) and persist the result to `sticker_matches`, recurringly.

Absorbs `integracion_F1/cruce_sticker.py` (the pipeline logic: matching
cascade, incremental candidate selection, watermarked Firestore read/write)
merged with `integracion_F1/job_sticker.py`'s runlog-wrapped entrypoint
(durable logging: `resolve_log_dir` -> `start_tee` -> run -> `append_run`) —
the SAME single-file pattern `app/jobs/dashboard_refresh.py` established
(task 7.2). Reuses its matching cascade from
`app.integracion.cruce_gestor` (`nearest`, `match_by_direccion`,
`build_addr_index`, `addr_key`, `_eval_latlon`) — "its copied pipeline
module" per design.md ADR-9 — instead of reimplementing it.

`sticker_matches/{fuente}_{registro_id}` is split into a pipeline-owned field
group (this job) and an admin-owned field group (`routers/sticker_asignaciones.py`,
slice 8). The job only ever writes the pipeline-owned subset via a
`merge:true` batched set, seeding `estado_asignacion:'pendiente'` (+
`cuadrilla_id`/`inspector_uid: null`) on a doc's first write only, and never
overwrites those fields on a doc that already exists — see design.md ADR-9.

THIRD (and final) module allowlisted for the `sticker_matches`/`cuadrillas`
literal (with `routers/inspector_asignaciones.py` and, later,
`routers/sticker_asignaciones.py`) under
`tests/invariants/test_sole_writer.py` — see task 7.9.

INCREMENTAL, not a full re-match every run: a point already
`tiene_sticker=true` is never re-scanned (cheap one-time `tiene_sticker`
pre-read via `get_all`, not a full-document read), and `evaluaciones` is
fetched only from `timestamp` after the last successful run's watermark
(`_meta/cruce_sticker_state`), not the whole collection every time.
Since 31-ago-2026 the cheap watermarked evaluaciones fetch runs FIRST and
an early-exit gate (0 new evaluaciones AND unchanged `panel_hash`) skips
even the `tiene_sticker` pre-read — that unconditional ~1k-doc `get_all`
alone was ~48k reads/day at 48 runs/day, the entire Spark daily quota.
`--full` bypasses the gate (and the watermark).

Confirmed clean of Google Sheets/gspread AND dagma dependencies (design.md
open-question-6 resolution; job-scheduling spec "Absorbed job code carries
no Sheets or dagma read/write path") — the job's own Firestore access goes
exclusively through `credentials.sismo()` (design.md ADR-4/ADR-9), NOT the
legacy module's own 3-tier `STICKERS_FIREBASE_SA`/`FIREBASE_SERVICE_ACCOUNT_JSON`
/ADC resolution.

    python -m app.jobs.cruce_sticker --check     # offline self-check, no network
    python -m app.jobs.cruce_sticker --dry       # real data, no Firestore write
    python -m app.jobs.cruce_sticker             # real data, write sticker_matches
    python -m app.jobs.cruce_sticker --top 50    # cap to the first N panel points (debug)
    python -m app.jobs.cruce_sticker --full      # ignore watermark + early-exit gate (full re-scan)
"""
from __future__ import annotations

import hashlib
import json
import os
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
INSPECTIONS_JSON = REPO_ROOT / "web" / "data" / "inspections.json"
ISRAEL_JSON = REPO_ROOT / "puntos_israel_cali.json"

STICKER_MATCHES_COLLECTION = "sticker_matches"
EVALUACIONES_COLLECTION = "evaluaciones"
STATE_DOC = "_meta/cruce_sticker_state"  # {"last_run_at", "last_checked_at": Timestamp, "panel_hash": str} — watermark + early-exit gate inputs

MATCH_MAX_M = 40.0     # same proximity threshold as cruce_gestor/asignar_f3
SEM_OK = 0.90           # same "fuzzy exacto" address-ratio threshold as cruce_gestor.ADDR_MATCH_RATIO
BATCH_SIZE = 500        # Firestore batch-write / getAll chunk limit

RUNS_FILE = "runs_cruce_sticker.jsonl"

# ADR-9 field ownership split: the job only ever writes PIPELINE_FIELDS via
# merge:true; ADMIN_DEFAULT_FIELDS is seeded ONLY on a doc's first write, never
# re-applied to a doc that already exists.
PIPELINE_FIELDS = ("fuente", "registro_id", "tiene_sticker", "tier",
                    "sticker_dist_m", "direccion", "coords", "zona_id", "matched_at",
                    "criterio_habitabilidad", "colapso")
ADMIN_DEFAULT_FIELDS = {"estado_asignacion": "pendiente", "cuadrilla_id": None,
                        "inspector_uid": None}


# ── Doc id (ADR-9) ──────────────────────────────────────────────────────────
def doc_id(fuente: str, registro_id: str) -> str:
    """Deterministic sticker_matches doc id — stable across re-runs so the
    pipeline updates the same document instead of duplicating it."""
    return f"{fuente}_{registro_id}"


# ── Panel loading (same as the notebook: EDE + Israel, EXIF-corrected coords) ─
def _load_ede() -> list[dict]:
    """EDE Panel points: the local web/data/inspections.json in dev, else the
    Blob-published copy from $INSPECTIONS_URL — the Railway image has no
    web/ (backend/Dockerfile only COPYs backend/, scripts/, deploy/, design.md
    ADR-1), so the cron reads the fresh Panel over HTTP. Raise (not silently 0
    points) when neither source is available, so a misconfigured service
    fails loud."""
    if INSPECTIONS_JSON.exists():
        return json.loads(INSPECTIONS_JSON.read_text(encoding="utf-8"))
    url = os.environ.get("INSPECTIONS_URL", "").strip()
    if not url:
        raise RuntimeError(
            f"{INSPECTIONS_JSON} no existe y no hay $INSPECTIONS_URL para bajarlo.")
    import requests
    return requests.get(url, timeout=60).json()


def load_panel() -> list[dict]:
    ede = _load_ede()
    # Israel points are a static 101-point delegation set with no field stickers;
    # only present locally, so they're skipped in the container (fine — the
    # cron refreshes the EDE Panel, which is what gains stickers).
    israel = (json.loads(ISRAEL_JSON.read_text(encoding="utf-8"))
              if ISRAEL_JSON.exists() else [])
    points = []
    for row in [*ede, *israel]:
        x, y = row.get("x"), row.get("y")
        if x is None or y is None:
            continue
        registro_id = row.get("GlobalID") or row.get("id_edan")
        if not registro_id:
            continue
        fuente_raw = str(row.get("fuente") or "EDE").lower()
        fuente = "israel" if "israel" in fuente_raw else "ede"
        # Colapso: single derived tag from the two EDE booleans (Israel points
        # lack them -> "no"). Total wins over parcial when both are set.
        if str(row.get("colapso_total") or "").lower() == "si":
            colapso = "total"
        elif str(row.get("colapso_parcial") or "").lower() == "si":
            colapso = "parcial"
        else:
            colapso = "no"
        points.append({
            "fuente": fuente, "registro_id": str(registro_id),
            "lat": float(y), "lon": float(x),
            "direccion": row.get("direccion_norm") or row.get("direccion") or "",
            # Best-effort zone tag; no KML/polygon lookup in scope — comuna is
            # the only zone-shaped field the Panel already carries.
            "zona_id": row.get("comuna") or None,
            # Habitability + collapse from the EDE, surfaced to the assignment
            # table and the inspector's pre-form cards so field crews see the
            # criticality at a glance.
            "criterio_habitabilidad": row.get("criterio_habitabilidad") or None,
            "colapso": colapso,
        })
    return points


# ── evaluaciones (Firestore, via credentials.sismo() — design.md ADR-4/ADR-9) ─
def fetch_evaluaciones(db, watermark=None) -> list[dict]:
    """Field stickers, flattened with the SAME X/Y/DIRECCION keys
    `app.integracion.cruce_gestor`'s cascade functions expect. `watermark`
    (a Firestore Timestamp / datetime, or None) restricts this to
    evaluaciones written AFTER the last successful run — the incremental
    part: an evaluación already scanned in a prior run is never fetched
    again."""
    col = db.collection(EVALUACIONES_COLLECTION)
    query = (col if watermark is None
             else col.where("timestamp", ">", watermark)).order_by("timestamp")
    out = []
    for doc in query.stream():
        e = doc.to_dict() or {}
        coords = e.get("coords") or {}
        desc = e.get("descripcion") or {}
        out.append({
            "CODIGO_EDIFICACION": e.get("codigo_edificacion") or doc.id,
            "Y": coords.get("lat"), "X": coords.get("lng"),
            "DIRECCION": desc.get("direccion") or "",
            # Exact-key rung: the formulario stamps the sticker_matches doc id
            # it was assigned from — when present, no geo/address cascade needed.
            "sticker_match_id": e.get("sticker_match_id"),
        })
    return out


def read_state(db) -> dict:
    """The full STATE_DOC dict (or `{}` if it has never been written) — one
    Firestore get, reused for the watermark (`last_run_at`, None on a first
    run meaning "process every evaluación that exists") AND the early-exit
    gate field (`panel_hash`), mirroring `planeacion_cruce.read_state`."""
    doc = db.document(STATE_DOC).get()
    return (doc.to_dict() or {}) if doc.exists else {}


def _hash_panel(panel: list[dict]) -> str:
    """sha256 of a stable JSON serialization of `load_panel()`'s own output —
    changes iff the Panel point set changes. Cheap: hashes a value already
    computed for `run_cruce_sticker`'s own use, never a second read. Same
    change signal as `planeacion_cruce._hash_puntos`."""
    blob = json.dumps(panel, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def touch_last_checked(db, when: datetime) -> None:
    """No-op run (the early-exit gate fired): stamp only `last_checked_at`
    so observability shows the cron alive, leaving the watermark and
    `panel_hash` untouched — nothing was actually processed, so there is
    nothing new to compare future runs against (same convention as
    `planeacion_cruce.touch_last_checked`)."""
    coll, name = STATE_DOC.split("/")
    db.collection(coll).document(name).set({"last_checked_at": when}, merge=True)


def write_watermark(db, when: datetime, *, panel_hash: str | None = None) -> None:
    """Advance the incremental watermark after a real run. `panel_hash`
    (when given) and `last_checked_at` are persisted alongside — the
    early-exit gate's own inputs, so the NEXT run can compare against what
    THIS run actually processed."""
    coll, name = STATE_DOC.split("/")
    payload: dict = {"last_run_at": when, "last_checked_at": when}
    if panel_hash is not None:
        payload["panel_hash"] = panel_hash
    db.collection(coll).document(name).set(payload, merge=True)


def read_tiene_sticker_state(db, doc_ids: list[str]) -> dict:
    """{doc_id: {'exists': bool, 'tiene_sticker': bool}} via one batched
    get_all with a field_paths projection (cheap — just the one flag, not the
    full document) — the pre-check that makes candidate selection possible
    without ever reading every sticker_matches doc in full."""
    col = db.collection(STICKER_MATCHES_COLLECTION)
    out: dict[str, dict] = {}
    for start in range(0, len(doc_ids), BATCH_SIZE):
        chunk = doc_ids[start:start + BATCH_SIZE]
        refs = [col.document(did) for did in chunk]
        for snap in db.get_all(refs, field_paths=["tiene_sticker"]):
            out[snap.id] = {
                "exists": snap.exists,
                "tiene_sticker": bool((snap.to_dict() or {}).get("tiene_sticker")) if snap.exists else False,
            }
    return out


def select_candidates(panel: list[dict], state: dict) -> list[dict]:
    """Panel points that actually need a match attempt this run: brand new
    (no sticker_matches doc yet — bootstrap) or not yet matched. A point
    already `tiene_sticker=true` is never re-scanned. Pure — no Firestore
    access, testable offline."""
    out = []
    for p in panel:
        s = state.get(doc_id(p["fuente"], p["registro_id"]), {"exists": False, "tiene_sticker": False})
        if not s["tiene_sticker"]:
            out.append(p)
    return out


# ── Matching cascade + quality tier ────────────────────────────────────────────
def _tier(dist_m: float | None, direccion_panel, direccion_sticker) -> str | None:
    """alta: geo AND address agree; media: only one signal backs the match;
    sospechoso: neither."""
    geo_ok = dist_m is not None and dist_m <= MATCH_MAX_M
    ka, kb = addr_key(direccion_panel), addr_key(direccion_sticker)
    ratio = SequenceMatcher(None, ka, kb).ratio() if ka and kb else 0.0
    sem_ok = ratio >= SEM_OK
    if geo_ok and sem_ok:
        return "alta"
    if geo_ok or sem_ok:
        return "media"
    return "sospechoso"


def cruce_exacto(did: str, lat, lon, por_match_id: dict[str, dict]) -> dict | None:
    """Exact-key rung, BEFORE the geo/address cascade: an evaluación whose
    `sticker_match_id` names this very sticker_matches doc id is the
    formulario's own app-minted pairing — matched directly, no cascade.
    `sticker_dist_m` via haversine when both sides have coords, else None.
    tier stays "alta" deliberately: status surfaces consume the 3-value enum
    and an exact key is the strongest signal — no 4th value is minted.
    Pure — no Firestore access, testable offline."""
    e = por_match_id.get(did)
    if e is None:
        return None
    dist_m = None
    if lat is not None and lon is not None and e.get("Y") is not None and e.get("X") is not None:
        dist_m = round(haversine_m((lat, lon), (e["Y"], e["X"])), 1)
    return {"tiene_sticker": True, "sticker_dist_m": dist_m, "tier": "alta"}


def cruce_sticker_punto(lat, lon, direccion, evaluaciones: list[dict],
                        addr_index: list[tuple[str, dict]]) -> dict:
    """Panel -> evaluaciones cascade: geo (<= MATCH_MAX_M) then address
    fallback. Calls into `app.integracion.cruce_gestor`'s cascade functions —
    the matching logic itself lives there, not here."""
    best, dist = nearest(lat, lon, evaluaciones, _eval_latlon, max_m=MATCH_MAX_M)
    if best is None:
        best, _via, dist = match_by_direccion(lat, lon, direccion, addr_index)
    if best is None:
        return {"tiene_sticker": False, "tier": None, "sticker_dist_m": None}
    dist_m = round(dist, 1) if dist is not None else None
    return {"tiene_sticker": True, "sticker_dist_m": dist_m,
            "tier": _tier(dist_m, direccion, best.get("DIRECCION"))}


# ── Write path (ADR-9: pipeline-owned fields only, merge:true, batched) ───────
def build_write_ops(points: list[dict], existing_ids: set[str]) -> list[tuple[str, dict]]:
    """(doc_id, write_fields) per point. write_fields ONLY ever contains
    PIPELINE_FIELDS, so a merge:true set can never touch an admin-owned field —
    plus ADMIN_DEFAULT_FIELDS, but ONLY when the doc has no prior write
    (first-write pending seed). Pure — no Firestore access, testable offline."""
    ops = []
    for p in points:
        did = doc_id(p["fuente"], p["registro_id"])
        # .get so a point missing an optional pipeline field (e.g. Israel points
        # have no habitability/colapso) writes None instead of crashing.
        fields = {k: p.get(k) for k in PIPELINE_FIELDS}
        if did not in existing_ids:
            fields.update(ADMIN_DEFAULT_FIELDS)
        ops.append((did, fields))
    return ops


def write_sticker_matches(db, points: list[dict], existing_ids: set[str]) -> int:
    """`existing_ids` comes from the caller's own pre-read (`run_cruce_sticker`
    already ran `read_tiene_sticker_state()` to pick candidates) — no second
    existence query here, that would just re-read what's already known."""
    col = db.collection(STICKER_MATCHES_COLLECTION)
    ops = build_write_ops(points, existing_ids)
    n = 0
    for start in range(0, len(ops), BATCH_SIZE):
        batch = db.batch()
        for did, fields in ops[start:start + BATCH_SIZE]:
            batch.set(col.document(did), fields, merge=True)
            n += 1
        batch.commit()
    return n


# ── Self-check (offline, no network — cruce_sticker.py's original idiom) ────
def _selfcheck() -> None:
    # Doc id is stable / deterministic.
    assert doc_id("ede", "1234") == "ede_1234"
    assert doc_id("israel", "45") == "israel_45"

    # Matching cascade reuse: geo hit, address-fallback hit, and a clean miss.
    evaluaciones = [
        {"CODIGO_EDIFICACION": "76001-1-0010001", "Y": 3.4200, "X": -76.5300,
         "DIRECCION": "Calle 1 # 2-3"},
        {"CODIGO_EDIFICACION": "76001-1-0020001", "Y": 3.4500, "X": -76.5600,
         "DIRECCION": "Carrera 9 # 8-7"},
    ]
    addr_index = build_addr_index(evaluaciones)

    r = cruce_sticker_punto(3.42001, -76.53001, "Calle 1 # 2-3", evaluaciones, addr_index)  # ~1 m, address agrees too
    assert r["tiene_sticker"] and r["sticker_dist_m"] < 2.0 and r["tier"] == "alta", r

    r = cruce_sticker_punto(3.9, -76.9, "CL 1 No. 2-3, Cali", evaluaciones, addr_index)  # far, same address
    assert r["tiene_sticker"] and r["tier"] == "media", r  # address agrees, geo doesn't

    r = cruce_sticker_punto(3.9, -76.9, "DG 99 # 1-1", evaluaciones, addr_index)  # neither signal
    assert not r["tiene_sticker"] and r["tier"] is None, r

    # Exact-key rung: sticker_match_id names the doc id directly — matches
    # even far away with no address agreement (tier stays "alta", 3-value
    # enum); an id absent from the index falls through (None -> geo cascade).
    por_match_id = {"ede_1234": {"CODIGO_EDIFICACION": "X", "Y": 3.4500, "X": -76.5600,
                                 "DIRECCION": "DG 99 # 1-1", "sticker_match_id": "ede_1234"}}
    r = cruce_exacto("ede_1234", 3.42, -76.53, por_match_id)
    assert r["tiene_sticker"] and r["tier"] == "alta" and r["sticker_dist_m"] > MATCH_MAX_M, r
    assert cruce_exacto("ede_9999", 3.42, -76.53, por_match_id) is None
    r = cruce_exacto("ede_1234", None, None, por_match_id)  # sin coords -> dist None, match igual
    assert r["tiene_sticker"] and r["sticker_dist_m"] is None and r["tier"] == "alta", r

    # (a) Re-run on an existing doc: the write dict never carries an
    # admin-owned key, so a merge:true set leaves estado_asignacion/
    # cuadrilla_id/inspector_uid untouched.
    points = [
        {"fuente": "ede", "registro_id": "1234", "tiene_sticker": True, "tier": "alta",
         "sticker_dist_m": 5.0, "direccion": "CL 1 # 2-3", "coords": {"lat": 3.42, "lon": -76.53},
         "zona_id": "Comuna 3", "matched_at": "2026-08-25T00:00:00"},
        {"fuente": "israel", "registro_id": "45", "tiene_sticker": False, "tier": None,
         "sticker_dist_m": None, "direccion": "", "coords": {"lat": 3.50, "lon": -76.40},
         "zona_id": None, "matched_at": "2026-08-25T00:00:00"},
    ]
    existing = {doc_id("ede", "1234")}
    ops = build_write_ops(points, existing)
    by_id = dict(ops)

    ede_fields = by_id[doc_id("ede", "1234")]
    for admin_field in ("estado_asignacion", "cuadrilla_id", "inspector_uid"):
        assert admin_field not in ede_fields, ede_fields
    assert ede_fields["tiene_sticker"] is True and ede_fields["tier"] == "alta"
    assert set(ede_fields) == set(PIPELINE_FIELDS)

    # (b) First write (no prior doc): pending assignment state is seeded
    # alongside the pipeline fields, in the same merge:true set.
    israel_fields = by_id[doc_id("israel", "45")]
    assert israel_fields["estado_asignacion"] == "pendiente"
    assert israel_fields["cuadrilla_id"] is None and israel_fields["inspector_uid"] is None
    assert israel_fields["tiene_sticker"] is False and israel_fields["tier"] is None
    assert set(israel_fields) == set(PIPELINE_FIELDS) | set(ADMIN_DEFAULT_FIELDS)

    # (c) select_candidates: the incremental core. A point already
    # tiene_sticker=true is dropped from the candidate list — never re-scanned.
    panel_c = [
        {"fuente": "ede", "registro_id": "A"},   # no state entry at all -> new, candidate
        {"fuente": "ede", "registro_id": "B"},   # exists, pendiente -> candidate
        {"fuente": "ede", "registro_id": "C"},   # exists, ya con sticker -> NOT a candidate
    ]
    state_c = {
        "ede_B": {"exists": True, "tiene_sticker": False},
        "ede_C": {"exists": True, "tiene_sticker": True},
    }
    cands = select_candidates(panel_c, state_c)
    assert {p["registro_id"] for p in cands} == {"A", "B"}, cands

    print("cruce_sticker self-check OK")


# ── Pipeline (absorbed from the legacy module's `main()`) ──────────────────
def run_cruce_sticker() -> dict:
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else None
    full = "--full" in sys.argv

    panel = load_panel()
    if top is not None:
        panel = panel[:top]
    # Hash AFTER the --top slice (RELI-001): a truncated debug run must
    # persist the TRUNCATED hash, so the next normal run always mismatches
    # and runs fully — hashing pre-slice would let a --top run's watermark
    # gate the never-scanned tail out forever.
    panel_hash = _hash_panel(panel)

    db = credentials.sismo().firestore
    now = datetime.now(timezone.utc)

    # Cheap change-detection inputs FIRST (mirrors planeacion_cruce.py's
    # early-exit gate): one state-doc get + the watermarked evaluaciones
    # stream (~0 docs steady-state) — NOT the unconditional projected
    # get_all over the full sticker_matches panel (~1k docs, 48 runs/day,
    # ~the entire Spark daily read quota by itself) that used to run before
    # anything else. `--full` ignores the persisted state entirely (watermark
    # None -> re-fetch every evaluación) and bypasses the gate below.
    state_doc = {} if full else read_state(db)
    watermark = state_doc.get("last_run_at")
    print(f"watermark: {watermark or '(ninguno — primera corrida, procesa toda evaluaciones)'}")
    evaluaciones = fetch_evaluaciones(db, watermark)
    print(f"evaluaciones nuevas desde el watermark: {len(evaluaciones)}")

    # Early-exit gate: 0 new evaluaciones AND unchanged Panel point set since
    # the last real run -> nothing a match attempt could change; skip
    # read_tiene_sticker_state (the full sticker_matches pre-read) and the
    # whole match/write path, stamping only last_checked_at so the status
    # surface still shows the cron alive.
    # ponytail: watermark+hash approximation — misses an in-place edit of an
    # already-scanned evaluación (timestamp unchanged, a miss the watermark
    # itself already accepts) and a manual deletion of a sticker_matches doc
    # (re-seeded only on the next panel/evaluaciones change or --full run).
    # Acceptable: both are effectively append-only in practice — same ceiling
    # planeacion_cruce's evaluaciones_count gate documents.
    if not full and watermark is not None and not evaluaciones \
            and state_doc.get("panel_hash") == panel_hash:
        print("cruce_sticker no-op: 0 evaluaciones nuevas y panel_hash sin cambios; "
              "salida temprana (sin read_tiene_sticker_state).")
        if "--dry" in sys.argv:
            print("[dry] sin escritura de last_checked_at")
        else:
            # --dry honors its "no Firestore write" contract even here.
            touch_last_checked(db, now)
        return {"noop": True, "total_panel": len(panel), "ya_con_sticker": None,
                "candidatos": 0, "a_escribir": 0, "nuevos_match": 0}

    doc_ids = [doc_id(p["fuente"], p["registro_id"]) for p in panel]
    state = read_tiene_sticker_state(db, doc_ids)
    ya_con_sticker = sum(1 for s in state.values() if s["tiene_sticker"])
    candidates = select_candidates(panel, state)
    print(f"Panel: {len(panel)} puntos | ya con sticker (sin re-escanear): {ya_con_sticker} | "
          f"candidatos este run: {len(candidates)}")

    addr_index = build_addr_index(evaluaciones)
    por_match_id = {e["sticker_match_id"]: e for e in evaluaciones if e.get("sticker_match_id")}
    to_write = []
    for p in candidates:
        did = doc_id(p["fuente"], p["registro_id"])
        r = (cruce_exacto(did, p["lat"], p["lon"], por_match_id)
             or cruce_sticker_punto(p["lat"], p["lon"], p["direccion"], evaluaciones, addr_index))
        is_new = not state.get(did, {"exists": False})["exists"]
        if not r["tiene_sticker"] and not is_new:
            continue  # ya tenía doc 'pendiente'; sigue sin match -> nada cambió, no se reescribe
        to_write.append({
            "fuente": p["fuente"], "registro_id": p["registro_id"],
            "tiene_sticker": r["tiene_sticker"], "tier": r["tier"],
            "sticker_dist_m": r["sticker_dist_m"], "direccion": p["direccion"],
            "coords": {"lat": p["lat"], "lon": p["lon"]}, "zona_id": p["zona_id"],
            "criterio_habitabilidad": p["criterio_habitabilidad"], "colapso": p["colapso"],
            "matched_at": now,
        })

    n_nuevos_match = sum(1 for x in to_write if x["tiene_sticker"])
    n_seed = sum(1 for x in to_write
                if not state.get(doc_id(x["fuente"], x["registro_id"]), {"exists": False})["exists"])
    print(f"docs a escribir: {len(to_write)} ({n_seed} nuevos, {n_nuevos_match} con match este run)")
    summary = {"total_panel": len(panel), "ya_con_sticker": ya_con_sticker,
              "candidatos": len(candidates), "a_escribir": len(to_write),
              "nuevos_match": n_nuevos_match}

    if "--dry" in sys.argv:
        print(f"[dry] no Firestore write; {len(to_write)} docs listos para {STICKER_MATCHES_COLLECTION}")
        return summary

    existing_ids = {did for did, s in state.items() if s["exists"]}
    n = write_sticker_matches(db, to_write, existing_ids)
    write_watermark(db, now, panel_hash=panel_hash)
    print(f"escritos {n} docs -> {db.project}/{STICKER_MATCHES_COLLECTION}; watermark avanzado a {now.isoformat()}")
    summary["escritos"] = n
    return summary


# ── Entrypoint (integracion_F1/job_sticker.py's runlog-wrapped pattern) ────
def main() -> int:
    if "--check" in sys.argv:
        _selfcheck()
        return 0

    started_at = datetime.now(timezone.utc)
    log_dir = runlog.resolve_log_dir()
    restore = runlog.start_tee(log_dir)

    print("=" * 60)
    print(f"Corrida cruce_sticker · inicio {started_at:%Y-%m-%d %H:%M:%S} UTC")
    print(f"Logs: {log_dir or 'solo stdout (sin volumen escribible)'}")
    try:
        summary = run_cruce_sticker() or {}
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
